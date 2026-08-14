#!/usr/bin/env python3
"""Find the wildlife cameras on the local network and copy their photos here.

This program runs on a laptop (macOS or Linux), not on the Raspberry Pi.

It works in two stages:

  1. Discovery -- figure out which wildlife cameras are on the Wi-Fi network.
  2. Sync      -- run rsync to copy new photographs from each camera into a
                  separate directory on the laptop.

Discovery tries several methods, cheapest first:

  * Cameras listed on the command line with --host.
  * Cameras listed in a configuration file (see cameras.conf.example).
  * Guessed mDNS names: wildlifecam.local, wildlifecam1.local, ... These are
    the names Raspberry Pi OS announces on the local network by itself.
  * Browsing for mDNS services with dns-sd (macOS) or avahi-browse (Linux),
    which finds cameras even if they were given a different number.
  * With --scan, checking every address on the local subnet. This is the
    last resort for a network where mDNS is blocked.

Only hosts whose name looks like a camera are trusted automatically. A host
found by --scan must identify itself as a wildlife camera over HTTP before
we copy anything from it, so we never rsync from a stranger's computer.

Examples:

    ./sync_cameras.py --list                    # just show what is out there
    ./sync_cameras.py                           # discover and sync everything
    ./sync_cameras.py --host wildlifecam2.local # sync one camera
    ./sync_cameras.py --scan --dest ~/camping   # no mDNS on this network
"""

import argparse
import ipaddress
import os
import re
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

DEFAULT_PREFIX = "wildlifecam"
DEFAULT_USER = "webelos"
DEFAULT_REMOTE_DIR = "/var/www/html/photos"
DEFAULT_DEST = "~/wildlifecam-photos"

SSH_PORT = 22
HTTP_PORT = 80

# The well-known mDNS multicast group and port (RFC 6762). We send our own
# queries here as a fallback when the system resolver cannot reach a .local
# name; see mdns_query() for why that fallback is necessary.
MDNS_ADDR = "224.0.0.251"
MDNS_PORT = 5353

# A host found by scanning the whole subnet has to prove it is a camera. We
# look for this file first, then fall back to the word "wildlife" on the
# camera's home page.
MARKER_PATH = "/wildlifecam.txt"
MARKER_WORD = "wildlife"


@dataclass
class Camera:
    """One wildlife camera we found (or were told about)."""

    name: str  # short name, used for the local directory
    host: str  # hostname or IP address we will ssh to
    user: str
    remote_dir: str = DEFAULT_REMOTE_DIR
    ip: str = ""  # filled in once the name resolves
    source: str = ""  # how we found it, for the --list output
    confirmed: bool = False  # answered as a wildlife camera over HTTP
    reachable: bool = False  # accepted a TCP connection on port 22

    def target(self) -> str:
        return f"{self.user}@{self.host}"


@dataclass
class SyncResult:
    camera: Camera
    ok: bool
    new_files: int = 0
    new_bytes: int = 0
    message: str = ""


# --------------------------------------------------------------------------
# Small network helpers
# --------------------------------------------------------------------------


def resolve(host: str) -> str:
    """Return the IPv4 address for a hostname, or "" if it does not resolve.

    Ordinary names go through the system resolver. For .local names we fall
    back to a direct mDNS query when the system resolver comes up empty,
    because on Linux the system resolver can be silently starved by another
    program on the machine -- see mdns_query() for the whole story.
    """
    ip = _resolve_system(host)
    if ip:
        return ip
    if host.lower().rstrip(".").endswith(".local"):
        return mdns_query(host)
    return ""


def _resolve_system(host: str) -> str:
    """Resolve a hostname through the operating system's resolver.

    This handles ordinary DNS and, on machines where it works, .local names
    too (via mDNSResponder on macOS or avahi/NSS on Linux). It does its own
    timing out, usually within a few seconds for a name nothing answers to.
    """
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
        return infos[0][4][0]
    except (socket.gaierror, OSError, IndexError):
        return ""


def outgoing_ip() -> str:
    """The laptop's own IPv4 on the interface it uses to reach the network."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packets are actually sent; this just picks the outgoing interface.
        probe.connect(("192.0.2.1", 9))
        return probe.getsockname()[0]
    except OSError:
        return ""
    finally:
        probe.close()


def mdns_query(host: str, timeout: float = 1.5) -> str:
    """Resolve a .local hostname to an IPv4 address with our own mDNS query.

    Why we cannot simply trust the system resolver
    ----------------------------------------------
    On macOS, .local names "just work" because mDNSResponder is built into the
    OS. On Linux the same job is done by avahi-daemon through the libnss-mdns
    module, so socket.getaddrinfo() quietly asks avahi on our behalf.

    The trouble is a Linux multicast-delivery quirk. mDNS runs on UDP port 5353
    on the multicast group 224.0.0.251, and avahi-daemon binds that port on the
    wildcard address 0.0.0.0. But other programs speak mDNS too -- notably
    Google Chrome, which binds a socket to the *specific* address
    224.0.0.251:5353 for Chromecast discovery. When a multicast reply arrives,
    the kernel hands it to the socket bound to the most specific address, so
    Chrome receives every response and avahi receives none. avahi's queries
    still go out, but the answers are siphoned away, so each resolve times out
    and getaddrinfo() returns nothing. Closing Chrome fixes it; leaving Chrome
    running breaks .local name resolution for the whole machine. (Diagnosed the
    hard way: avahi-resolve times out while `ss -ulnp` shows chrome sitting on
    224.0.0.251:5353.)

    The workaround: send the mDNS query ourselves from an *ephemeral* UDP port
    rather than from 5353. RFC 6762 section 6.7 calls this a "legacy" or
    one-shot query: because our source port is not 5353, the camera replies
    with an ordinary unicast UDP packet sent straight back to our port. That
    reply never touches the contested 224.0.0.251:5353 multicast socket, so no
    other program can intercept it and we get our answer no matter what else is
    running on the laptop.
    """
    request = _build_a_query(host)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        # On a laptop with several interfaces (Wi-Fi, docker bridges, a USB
        # dongle) make sure the query leaves by the one that reaches the LAN.
        out_ip = outgoing_ip()
        if out_ip:
            try:
                sock.setsockopt(
                    socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(out_ip)
                )
            except OSError:
                pass
        # Read replies until the deadline. We are on an ephemeral port, so the
        # only packets we see are unicast answers addressed to us. Wi-Fi drops
        # multicast freely and the first query is the one most often lost, so we
        # resend every RESEND_EVERY seconds rather than trusting a single shot
        # (RFC 6762 expects one-shot queriers to retransmit).
        RESEND_EVERY = 0.4
        deadline = time.monotonic() + timeout
        next_send = 0.0  # 0 means "send immediately on the first pass"
        while True:
            now = time.monotonic()
            if now >= deadline:
                return ""
            if now >= next_send:
                sock.sendto(request, (MDNS_ADDR, MDNS_PORT))
                next_send = now + RESEND_EVERY
            sock.settimeout(min(RESEND_EVERY, deadline - now))
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue
            ip = _parse_a_reply(data)
            if ip:
                return ip
    except OSError:
        return ""
    finally:
        sock.close()


def mdns_reverse(ip: str, timeout: float = 1.0) -> str:
    """Ask a host, over unicast mDNS, what its own hostname is.

    Used to give a friendly directory name to a camera found by --scan. Reverse
    DNS (socket.gethostbyaddr) returns nothing on a typical home LAN, and a
    multicast mDNS query may never reach the camera on a Wi-Fi network that
    handles multicast poorly (see mdns_query). But a *unicast* mDNS query sent
    straight to the camera's address is answered reliably -- it does not depend
    on multicast delivery at all -- so we ask the camera for the PTR record of
    its own address and get back its .local name.
    """
    request = _build_ptr_query(ip)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Resend within the window: Wi-Fi occasionally drops the odd packet, and
        # a lost single query would leave a camera needlessly unnamed.
        RESEND_EVERY = 0.4
        deadline = time.monotonic() + timeout
        next_send = 0.0
        while True:
            now = time.monotonic()
            if now >= deadline:
                return ""
            if now >= next_send:
                sock.sendto(request, (ip, MDNS_PORT))
                next_send = now + RESEND_EVERY
            sock.settimeout(min(RESEND_EVERY, deadline - now))
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue
            name = _parse_ptr_reply(data)
            if name:
                return name
    except OSError:
        return ""
    finally:
        sock.close()


def _build_a_query(host: str) -> bytes:
    """Build a DNS query packet asking for the A (IPv4) record of a host."""
    # Header: id, flags (0 = standard query), 1 question, no answers/authority/
    # additional. The id is arbitrary; a one-shot responder echoes it back.
    header = struct.pack(">HHHHHH", 0x4242, 0x0000, 1, 0, 0, 0)
    labels = host.rstrip(".").split(".")
    qname = b"".join(bytes([len(l)]) + l.encode("ascii") for l in labels) + b"\x00"
    question = qname + struct.pack(">HH", 1, 1)  # QTYPE=A(1), QCLASS=IN(1)
    return header + question


def _build_ptr_query(ip: str) -> bytes:
    """Build a DNS query packet asking for the PTR (name) record of an address."""
    header = struct.pack(">HHHHHH", 0x4243, 0x0000, 1, 0, 0, 0)
    labels = ip.split(".")[::-1] + ["in-addr", "arpa"]
    qname = b"".join(bytes([len(l)]) + l.encode("ascii") for l in labels) + b"\x00"
    question = qname + struct.pack(">HH", 12, 1)  # QTYPE=PTR(12), QCLASS=IN(1)
    return header + question


def _read_name(data: bytes, offset: int) -> tuple:
    """Read a (possibly compressed) DNS name; return (name, offset_after_it)."""
    labels = []
    after = offset
    jumped = False
    while offset < len(data):
        length = data[offset]
        if length == 0:  # end of name
            offset += 1
            if not jumped:
                after = offset
            break
        if length & 0xC0 == 0xC0:  # compression pointer
            if not jumped:
                after = offset + 2
            offset = ((length & 0x3F) << 8) | data[offset + 1]
            jumped = True
            continue
        offset += 1
        labels.append(data[offset:offset + length].decode("ascii", "replace"))
        offset += length
    return ".".join(labels), after


def _skip_name(data: bytes, offset: int) -> int:
    """Advance past a (possibly compressed) DNS name, returning the new offset."""
    while offset < len(data):
        length = data[offset]
        if length == 0:  # end of name
            return offset + 1
        if length & 0xC0 == 0xC0:  # compression pointer: two bytes, then done
            return offset + 2
        offset += 1 + length
    return offset


def _parse_a_reply(data: bytes) -> str:
    """Pull the first A record out of a DNS reply, or "" if there is none.

    We asked one question about one host and only unicast answers reach our
    ephemeral port, so the first A record in the answer section is our host.
    """
    if len(data) < 12:
        return ""
    _id, flags, qdcount, ancount = struct.unpack(">HHHH", data[:8])
    if not flags & 0x8000:  # QR bit clear: this is a query, not a response
        return ""
    offset = 12
    for _ in range(qdcount):  # skip the echoed question section
        offset = _skip_name(data, offset) + 4  # + QTYPE + QCLASS
    for _ in range(ancount):
        offset = _skip_name(data, offset)
        if offset + 10 > len(data):
            break
        rtype, _rclass, _ttl, rdlen = struct.unpack(">HHIH", data[offset:offset + 10])
        offset += 10
        if rtype == 1 and rdlen == 4 and offset + 4 <= len(data):  # an A record
            return socket.inet_ntoa(data[offset:offset + 4])
        offset += rdlen
    return ""


def _parse_ptr_reply(data: bytes) -> str:
    """Pull the first PTR (hostname) out of a DNS reply, or "" if none."""
    if len(data) < 12:
        return ""
    _id, flags, qdcount, ancount = struct.unpack(">HHHH", data[:8])
    if not flags & 0x8000:  # QR bit clear: a query, not a response
        return ""
    offset = 12
    for _ in range(qdcount):  # skip the echoed question section
        offset = _skip_name(data, offset) + 4  # + QTYPE + QCLASS
    for _ in range(ancount):
        offset = _skip_name(data, offset)
        if offset + 10 > len(data):
            break
        rtype, _rclass, _ttl, rdlen = struct.unpack(">HHIH", data[offset:offset + 10])
        offset += 10
        if rtype == 12:  # PTR record: rdata is the host's name
            name, _ = _read_name(data, offset)
            return name
        offset += rdlen
    return ""


def port_open(host: str, port: int, timeout: float) -> bool:
    """True if something is listening on this TCP port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_get(host: str, path: str, timeout: float) -> str:
    """Fetch a small page from the camera's web server. "" if that fails."""
    url = f"http://{host}:{HTTP_PORT}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read(4096).decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, ValueError):
        return ""


def looks_like_camera(host: str, timeout: float) -> bool:
    """Ask the host's web server whether it is one of our wildlife cameras."""
    if MARKER_WORD in http_get(host, MARKER_PATH, timeout).lower():
        return True
    return MARKER_WORD in http_get(host, "/", timeout).lower()


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def guessed_names(prefix: str, highest: int) -> list:
    """The mDNS names a camera is likely to have: wildlifecam.local, etc."""
    names = [f"{prefix}.local"]
    names += [f"{prefix}{n}.local" for n in range(1, highest + 1)]
    return names


def browse_mdns(prefix: str, timeout: float) -> list:
    """Ask the network which machines are advertising themselves.

    Uses whichever mDNS browser this laptop has. Returns hostnames such as
    "wildlifecam3.local". Returns an empty list if no browser is installed.
    """
    if shutil.which("avahi-browse"):
        return _browse_avahi(prefix, timeout)
    if shutil.which("dns-sd"):
        return _browse_dns_sd(prefix, timeout)
    return []


def _run_briefly(command: list, timeout: float) -> str:
    """Run a command that never exits on its own and keep whatever it printed."""
    try:
        done = subprocess.run(
            command, capture_output=True, timeout=timeout, text=True, check=False
        )
        return done.stdout
    except subprocess.TimeoutExpired as expired:
        output = expired.stdout or ""
        return output if isinstance(output, str) else output.decode(errors="replace")
    except OSError:
        return ""


def _browse_avahi(prefix: str, timeout: float) -> list:
    # -t stops once the cache is exhausted, -p prints machine-readable lines:
    # =;wlan0;IPv4;wildlifecam1;_ssh._tcp;local;wildlifecam1.local;10.0.0.5;22;
    found = []
    for service in ("_ssh._tcp", "_workstation._tcp"):
        text = _run_briefly(
            ["avahi-browse", "-t", "-r", "-p", service], timeout=timeout
        )
        for line in text.splitlines():
            fields = line.split(";")
            if len(fields) > 6 and fields[0] == "=":
                found.append(fields[6])
    return [h for h in found if h.lower().startswith(prefix.lower())]


def _browse_dns_sd(prefix: str, timeout: float) -> list:
    # dns-sd runs until interrupted, so we let it print for a couple of
    # seconds and then read what it managed to say. Instance names on a
    # Raspberry Pi match its hostname.
    found = []
    for service in ("_ssh._tcp", "_workstation._tcp"):
        text = _run_briefly(["dns-sd", "-B", service, "local."], timeout=timeout)
        for line in text.splitlines():
            if " Add " not in line:
                continue
            fields = line.split()
            if len(fields) >= 7:
                instance = " ".join(fields[6:]).strip()
                if instance.lower().startswith(prefix.lower()):
                    found.append(f"{instance}.local")
    return found


def local_subnet() -> str:
    """Guess the laptop's own /24 network, e.g. "192.168.1.0/24"."""
    my_ip = outgoing_ip()
    if not my_ip:
        return ""
    return str(ipaddress.ip_network(f"{my_ip}/24", strict=False))


def scan_subnet(cidr: str, timeout: float, workers: int) -> list:
    """Return every address on the subnet that accepts an ssh connection."""
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return []
    addresses = [str(a) for a in network.hosts()]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = pool.map(lambda a: (a, port_open(a, SSH_PORT, timeout)), addresses)
        return [address for address, is_open in results if is_open]


def sanitize(name: str) -> str:
    """Turn a hostname into a safe directory name."""
    short = name.split(".")[0].strip().lower()
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", short).strip("-")
    return cleaned or "camera"


def read_config(path: str, default_user: str) -> list:
    """Read a cameras.conf file. Each line is "name host" or just "host"."""
    cameras = []
    if not os.path.exists(path):
        return cameras
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            fields = line.split()
            if len(fields) == 1:
                name, host = sanitize(fields[0]), fields[0]
            else:
                name, host = sanitize(fields[0]), fields[1]
            user = default_user
            if "@" in host:
                user, host = host.split("@", 1)
            cameras.append(
                Camera(name=name, host=host, user=user, source=f"config:{path}")
            )
    return cameras


def discover(options) -> list:
    """Find cameras using every method the options allow."""
    candidates = []
    seen_hosts = set()

    def add(name, host, source, trusted, user=None, remote_dir=None):
        user = user or options.user
        if "@" in host:
            user, host = host.split("@", 1)
        key = host.lower()
        if key in seen_hosts:
            return
        seen_hosts.add(key)
        candidates.append(
            Camera(
                name=name,
                host=host,
                user=user,
                remote_dir=remote_dir or options.remote_dir,
                source=source,
                # Hosts we were told about, or whose name matches the camera
                # naming scheme, are trusted without an HTTP check.
                confirmed=trusted,
            )
        )

    for host in options.host:
        add(sanitize(host), host, "--host", True)

    for entry in read_config(options.config, options.user):
        add(entry.name, entry.host, entry.source, True, user=entry.user)

    if not options.no_mdns:
        for host in guessed_names(options.prefix, options.max_index):
            add(sanitize(host), host, "mdns-name", True)
        for host in browse_mdns(options.prefix, options.browse_timeout):
            add(sanitize(host), host, "mdns-browse", True)

    # Work out which candidates actually answer.
    with ThreadPoolExecutor(max_workers=options.workers) as pool:
        pool.map(lambda c: probe(c, options.probe_timeout), candidates)

    live = [c for c in candidates if c.reachable]

    if options.scan:
        cidr = options.subnet or local_subnet()
        if not cidr:
            warn("could not work out the local subnet, skipping --scan")
        else:
            note(f"scanning {cidr} for cameras (this takes a moment)")
            known_ips = {c.ip for c in live}
            for address in scan_subnet(cidr, options.probe_timeout, options.workers):
                if address in known_ips or address.lower() in seen_hosts:
                    continue
                # A scanned host is only a camera if it says so itself.
                if not looks_like_camera(address, options.probe_timeout):
                    continue
                camera = Camera(
                    name=reverse_name(address) or f"camera-{address.split('.')[-1]}",
                    host=address,
                    user=options.user,
                    remote_dir=options.remote_dir,
                    ip=address,
                    source="subnet-scan",
                    confirmed=True,
                    reachable=True,
                )
                live.append(camera)

    # Two names can point at the same Pi (wildlifecam.local and its IP).
    # Keep the first one we found for each address.
    unique = []
    seen_ips = set()
    for camera in live:
        if camera.ip and camera.ip in seen_ips:
            continue
        seen_ips.add(camera.ip)
        unique.append(camera)
    return unique


def probe(camera: Camera, timeout: float) -> None:
    """Resolve a candidate and see whether its ssh port is open."""
    camera.ip = resolve(camera.host)
    if not camera.ip:
        return
    camera.reachable = port_open(camera.ip, SSH_PORT, timeout)
    if camera.reachable and not camera.confirmed:
        camera.confirmed = looks_like_camera(camera.ip, timeout)


def reverse_name(address: str) -> str:
    """Best-effort hostname for an IP address, used to name its directory.

    Tries the system reverse resolver first, then a unicast mDNS query to the
    host itself. On a home LAN there is usually no PTR record in ordinary DNS,
    but the camera answers a unicast mDNS query reliably (see mdns_reverse),
    so a camera found by --scan still gets its real "wildlifecam*" name rather
    than a name made up from its IP address.
    """
    try:
        return sanitize(socket.gethostbyaddr(address)[0])
    except OSError:
        pass
    name = mdns_reverse(address)
    return sanitize(name) if name else ""


# --------------------------------------------------------------------------
# Syncing
# --------------------------------------------------------------------------


def directory_stats(path: str) -> tuple:
    """Count the files and bytes already stored under a directory."""
    files = 0
    total = 0
    for directory, _, names in os.walk(path):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(directory, name))
                files += 1
            except OSError:
                pass
    return files, total


def human(count: int) -> str:
    """Print a byte count the way a person would say it."""
    size = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def sync_camera(camera: Camera, options) -> SyncResult:
    """Copy new photographs from one camera into its own local directory."""
    destination = os.path.join(options.dest, camera.name)
    os.makedirs(destination, exist_ok=True)

    before_files, before_bytes = directory_stats(destination)

    ssh_options = [
        "ssh",
        "-o",
        f"ConnectTimeout={int(options.connect_timeout)}",
    ]
    if options.insecure_hostkeys:
        # Useful when a Pi has been reimaged and its host key changed.
        ssh_options += [
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
        ]
    else:
        ssh_options += ["-o", "StrictHostKeyChecking=accept-new"]
    if options.identity:
        ssh_options += ["-i", shlex.quote(os.path.expanduser(options.identity))]

    source = f"{camera.target()}:{camera.remote_dir.rstrip('/')}/"

    command = [
        "rsync",
        "-a",
        "--human-readable",
        "--partial",
        "--timeout=60",
        "-e",
        " ".join(ssh_options),
    ]
    if options.verbose:
        command.append("--verbose")
    if options.progress:
        command.append("--info=progress2")
    if options.dry_run:
        command.append("--dry-run")
    if options.delete:
        command.append("--delete")
    command += [source, destination + os.sep]

    note(f"{camera.name}: rsync from {source}")
    if options.verbose:
        note("  " + " ".join(command))

    try:
        # stdout/stderr are inherited so rsync progress and any ssh password
        # prompt appear normally in the terminal.
        completed = subprocess.run(command, check=False)
    except OSError as error:
        return SyncResult(camera, False, message=str(error))

    after_files, after_bytes = directory_stats(destination)
    result = SyncResult(
        camera,
        completed.returncode == 0,
        new_files=max(0, after_files - before_files),
        new_bytes=max(0, after_bytes - before_bytes),
    )
    if not result.ok:
        result.message = f"rsync exited {completed.returncode}"
    return result


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------


def note(message: str) -> None:
    print(f"[sync] {message}")


def warn(message: str) -> None:
    print(f"[sync] warning: {message}", file=sys.stderr)


def print_cameras(cameras: list) -> None:
    if not cameras:
        print("No wildlife cameras found.")
        return
    width = max(len(c.name) for c in cameras)
    print(f"Found {len(cameras)} camera(s):")
    for camera in cameras:
        confirmed = "yes" if camera.confirmed else "no "
        print(
            f"  {camera.name:<{width}}  {camera.host:<24} {camera.ip:<15} "
            f"web-check={confirmed}  found-by={camera.source}"
        )


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Discover wildlife cameras on the local network and rsync "
        "their photographs to this laptop.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    default_config = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "cameras.conf")

    parser.add_argument("--host", action="append", default=[],
                        help="camera hostname or IP; may be repeated")
    parser.add_argument("--config", default=default_config,
                        help=f"camera list file (default: {default_config})")
    parser.add_argument("--dest", default=DEFAULT_DEST,
                        help=f"local photo directory (default: {DEFAULT_DEST})")
    parser.add_argument("--user", default=DEFAULT_USER,
                        help=f"ssh user on the cameras (default: {DEFAULT_USER})")
    parser.add_argument("--remote-dir", default=DEFAULT_REMOTE_DIR,
                        help=f"photo directory on the camera "
                             f"(default: {DEFAULT_REMOTE_DIR})")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX,
                        help=f"camera hostname prefix (default: {DEFAULT_PREFIX})")
    parser.add_argument("--max-index", type=int, default=9,
                        help="highest camera number to guess (default: 9)")

    parser.add_argument("--list", action="store_true",
                        help="only discover cameras, do not copy anything")
    parser.add_argument("--scan", action="store_true",
                        help="also scan the local subnet (slower; for networks "
                             "where .local names do not work)")
    parser.add_argument("--subnet", default="",
                        help="subnet to scan, e.g. 192.168.1.0/24 "
                             "(default: this laptop's own /24)")
    parser.add_argument("--no-mdns", action="store_true",
                        help="skip mDNS discovery entirely")

    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="show what rsync would copy without copying it")
    parser.add_argument("--delete", action="store_true",
                        help="delete local photos that are gone from the camera")
    parser.add_argument("--identity", "-i", default="",
                        help="ssh private key to use")
    parser.add_argument("--insecure-hostkeys", action="store_true",
                        help="ignore ssh host key changes (handy after a Pi "
                             "has been reimaged)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="list every file rsync copies")
    parser.add_argument("--no-progress", dest="progress", action="store_false",
                        help="hide the rsync progress meter")

    parser.add_argument("--probe-timeout", type=float, default=1.5,
                        help="seconds to wait for a camera to answer (default: 1.5)")
    parser.add_argument("--browse-timeout", type=float, default=3.0,
                        help="seconds to browse for mDNS services (default: 3)")
    parser.add_argument("--connect-timeout", type=float, default=10.0,
                        help="ssh connection timeout in seconds (default: 10)")
    parser.add_argument("--workers", type=int, default=64,
                        help="how many hosts to probe at once (default: 64)")

    options = parser.parse_args(argv)
    options.dest = os.path.abspath(os.path.expanduser(options.dest))
    options.config = os.path.expanduser(options.config)
    return options


def main(argv=None) -> int:
    options = parse_args(argv if argv is not None else sys.argv[1:])

    if not shutil.which("rsync") and not options.list:
        warn("rsync is not installed on this laptop; install it and try again")
        return 2

    note("looking for wildlife cameras...")
    cameras = discover(options)
    print_cameras(cameras)

    if options.list:
        return 0 if cameras else 1
    if not cameras:
        warn("nothing to sync. Are the cameras powered on and on this Wi-Fi?")
        warn("try: --scan, or --host <ip address>")
        return 1

    skipped = [c for c in cameras if not c.confirmed]
    for camera in skipped:
        warn(f"skipping {camera.host}: it did not identify itself as a camera")
    cameras = [c for c in cameras if c.confirmed]

    os.makedirs(options.dest, exist_ok=True)
    note(f"copying photos into {options.dest}")

    results = [sync_camera(camera, options) for camera in cameras]

    print()
    print("Summary")
    print("-------")
    for result in results:
        if result.ok:
            print(
                f"  {result.camera.name}: OK, {result.new_files} new file(s), "
                f"{human(result.new_bytes)}"
            )
        else:
            print(f"  {result.camera.name}: FAILED ({result.message})")
    for camera in skipped:
        print(f"  {camera.name}: skipped (not confirmed as a camera)")

    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        note("interrupted")
        sys.exit(130)
