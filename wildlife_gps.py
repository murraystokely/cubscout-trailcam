#!/usr/bin/env python3
"""The GPS dongle: set the clock, and remember where the camera is.

This is not one of the numbered steps.  It runs BESIDE whichever camera
program is running, and only while a VK-162 GPS dongle is plugged in.
Nothing starts it by hand: plugging the dongle in makes udev and systemd
start it, and pulling it out stops it (config/90-wildlife-gps.rules and
config/wildlife-gps.service).  The whole design, and why, is in
docs/gps-design.md.

A Raspberry Pi has no clock that keeps time while it is off.  It wakes up
believing whatever time it last saved, which on a fence post with no
network can be days wrong.  A GPS receiver knows the time from the
satellites, and where it is.  So, once the dongle has a fix it trusts,
this program:

    1. MEASURES how wrong the Pi's clock is -- before fixing it, because
       at takedown that number is the whole point: it is how far the
       clock drifted during the deployment;
    2. SETS the clock, if it is out by more than a second;
    3. writes what it learned to /var/lib/wildlifecam/gps.json, which the
       camera programs read when they save a photograph;
    4. appends a row to a log beside the photographs, so the laptop can
       correct every photograph's time later.

What the dongle sends is plain text, one line per sentence, several
sentences a second.  You can watch it yourself with

    cat /dev/gps0

and this program mostly needs two kinds of line:

    $GPRMC,160211.00,A,3720.00000,N,12142.00000,W,0.021,,111026,,,A*63
           time      ^ latitude      longitude              date
                     A = I know where I am, V = not yet

    $GPGGA,160211.00,3720.00000,N,12142.00000,W,1,07,1.21,412.3,M,...*67
                                                  ^  ^    ^
                                    satellites used  HDOP  altitude (m)

(and one field from $GPGSV: how many satellites are in view at all, for
the "waiting" message).  The two characters after the * are a checksum, so a line garbled on the
way down the cable is thrown away instead of believed.

Run it by hand on a laptop to watch, without touching the clock:

    python3 wildlife_gps.py /dev/gps0 --state-dir /tmp/gps --photo-dir /tmp/gps
"""

import argparse
import json
import math
import os
import socket
import statistics
import sys
import termios
import time
from datetime import datetime, timezone

# ------------------------------------------------------------
# Where things go
# ------------------------------------------------------------

STATE_DIR = "/var/lib/wildlifecam"          # gps.json: read by the cameras
PHOTO_DIR = "/var/www/html/photos"          # the log: copied with the photos
STATUS_FILE = "/var/www/html/gps-status.txt"   # one line, for a phone

# systemd-timesyncd sets the clock to this file's modification time at
# boot.  Touching it after a GPS fix means the next boot starts from a
# true time instead of an old one.
TIMESYNC_CLOCK = "/var/lib/systemd/timesync/clock"

# systemd-timesyncd creates this once it has reached a time server.  If
# the network is in charge of the clock, we measure but leave it alone.
NETWORK_SYNCED = "/run/systemd/timesync/synchronized"

# ------------------------------------------------------------
# The rules
# ------------------------------------------------------------

# The trust test.  A receiver's first valid readings can be a little off,
# so it has to say "A" with at least 4 satellites this many seconds in a
# row before we believe it.
TRUST_SECONDS = 10
MIN_SATELLITES = 4

# A receiver with an old firmware bug can get the date decades wrong.
# This camera has never existed before this year.
MIN_YEAR = 2026

# Only move the clock if it is out by more than this.  Less than a
# second is closer than the photographs need.
STEP_IF_OFF_BY_S = 1.0

# If the network has set the clock and GPS disagrees by more than this,
# one of them is wrong; say so in the journal.
DISAGREE_WARNING_S = 2.0

# While the dongle stays in: a log row this often, and a fresh
# measure-then-correct this often.
LOG_EVERY_S = 60.0
CHECK_CLOCK_EVERY_S = 600.0

# A later plug-in this far from the first one means the camera was moved
# without being switched off.
MOVED_M = 50.0

LOG_COLUMNS = ["gps_utc", "pi_utc", "uptime_s", "offset_s", "stepped_s",
               "attachment", "lat", "lon", "alt_m", "hdop", "satellites",
               "network_synced"]

CAMERA_NAME = socket.gethostname()


def boot_id():
    """This boot's random id, the same eight characters the cameras use."""
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()[:8]
    except OSError:
        return "unknown"


BOOT_ID = boot_id()


def seconds_since_boot():
    """The kernel's count since power-on.  The one clock that never lies."""
    with open("/proc/uptime") as f:
        return round(float(f.read().split()[0]), 1)


# ------------------------------------------------------------
# Reading the dongle
# ------------------------------------------------------------

def open_serial(device):
    """Open the dongle as a raw 9600-baud serial line."""
    fd = os.open(device, os.O_RDONLY | os.O_NOCTTY)
    settings = termios.tcgetattr(fd)
    settings[0] = 0                                    # no input processing
    settings[1] = 0                                    # no output processing
    settings[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    settings[3] = 0                                    # raw: no line editing
    settings[4] = settings[5] = termios.B9600
    settings[6][termios.VMIN] = 1                      # wait for a byte
    settings[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, settings)
    return fd


def lines_from(device):
    """Each line the dongle sends, with when it arrived.

    Yields (line, pi_time, uptime_s).  pi_time is the Pi's own clock when
    the line arrived -- the clock we are about to judge.  Stops when the
    dongle is pulled out.
    """
    fd = open_serial(device)
    waiting = b""
    while True:
        try:
            chunk = os.read(fd, 512)
        except OSError:
            return                                     # unplugged
        if not chunk:
            return
        waiting += chunk
        while b"\n" in waiting:
            raw, waiting = waiting.split(b"\n", 1)
            line = raw.decode("ascii", "replace").strip()
            if line:
                yield line, time.time(), seconds_since_boot()


def checksum_ok(line):
    """XOR every character between $ and *; it must match what follows *."""
    if not line.startswith("$") or "*" not in line:
        return False
    body, _, given = line[1:].partition("*")
    total = 0
    for character in body:
        total ^= ord(character)
    return given[:2].upper() == f"{total:02X}"


def to_degrees(value, hemisphere):
    """'3720.00000', 'N' -> 37.33333.

    NMEA writes degrees and then MINUTES: 37 degrees and 20 minutes, not
    37.2 degrees.  Getting this wrong puts the camera twenty kilometres
    away.
    """
    if not value:
        return None
    dot = value.index(".")
    degrees = float(value[:dot - 2]) + float(value[dot - 2:]) / 60
    return -degrees if hemisphere in ("S", "W") else degrees


def fixes_from(lines):
    """Turn sentences into fixes, one per second.

    Each second the dongle sends RMC (time, date, valid, position) and then
    GGA (satellites, HDOP, altitude) for the same moment.  We remember the
    RMC and yield a fix when its GGA arrives, as a dict:

        utc, valid, lat, lon, alt_m, hdop, satellites, in_view,
        pi_time, uptime_s

    satellites is how many the fix USES, which is 0 until there is a fix;
    in_view is how many it can hear, from GSV, which says much more while
    you wait.  GSV comes at the END of each second, so in_view is None
    until the first one has arrived.

    pi_time and uptime_s are from when the RMC arrived, the first sentence
    of that second.
    """
    rmc = None
    in_view = None
    for line, pi_time, uptime_s in lines:
        if not checksum_ok(line):
            continue
        fields = line[1:line.index("*")].split(",")
        kind = fields[0][2:]                    # GPRMC, GNRMC -> RMC

        try:
            fix = None
            if kind == "GSV" and len(fields) >= 4:
                in_view = int(fields[3] or 0)
            elif kind == "RMC" and len(fields) >= 10:
                rmc = {"clock": fields[1], "fields": fields,
                       "pi_time": pi_time, "uptime_s": uptime_s}
            elif kind == "GGA" and len(fields) >= 10 and rmc:
                fix = combine(rmc, fields, in_view)
                rmc = None
        except (ValueError, IndexError):
            rmc = None                  # a sentence we cannot read: skip it
            continue
        if fix:
            yield fix


def combine(rmc, fields, in_view):
    """One fix from a second's RMC and GGA; None if they do not match."""
    if fields[1] != rmc["clock"]:
        return None                     # a different second; skip it
    r = rmc["fields"]
    utc = None
    if r[1] and r[9]:
        utc = datetime.strptime(r[9] + r[1].split(".")[0],
                                "%d%m%y%H%M%S").replace(tzinfo=timezone.utc)
    return {
        "utc": utc,
        "valid": r[2] == "A",
        "lat": to_degrees(r[3], r[4]),
        "lon": to_degrees(r[5], r[6]),
        "alt_m": float(fields[9]) if fields[9] else None,
        "hdop": float(fields[8]) if fields[8] else None,
        "satellites": int(fields[7] or 0),
        "in_view": in_view,
        "pi_time": rmc["pi_time"],
        "uptime_s": rmc["uptime_s"],
    }


# ------------------------------------------------------------
# The trust test
# ------------------------------------------------------------

class TrustTest:
    """Believe a fix only after TRUST_SECONDS good ones in a row.

    Good means: the receiver says A, with at least MIN_SATELLITES, and a
    year of MIN_YEAR or later.  One bad second starts the count again.
    """

    def __init__(self):
        self.in_a_row = 0

    def check(self, fix):
        good = (fix["valid"]
                and fix["satellites"] >= MIN_SATELLITES
                and fix["utc"] is not None
                and fix["utc"].year >= MIN_YEAR
                and fix["lat"] is not None
                and fix["lon"] is not None)
        self.in_a_row = self.in_a_row + 1 if good else 0
        return self.in_a_row >= TRUST_SECONDS


# ------------------------------------------------------------
# The clock
# ------------------------------------------------------------

def network_has_the_clock():
    return os.path.exists(NETWORK_SYNCED)


def measure_and_set_clock(fix, may_set):
    """How wrong is the Pi's clock?  Measure, then (maybe) correct.

    Returns (offset_s, stepped_s).  offset_s is GPS minus Pi: positive
    means the Pi was behind.  stepped_s is how far we moved the clock, or
    None if we did not.
    """
    offset_s = round((fix["utc"] - datetime.fromtimestamp(
        fix["pi_time"], timezone.utc)).total_seconds(), 3)
    synced = network_has_the_clock()

    if synced and abs(offset_s) > DISAGREE_WARNING_S:
        print(f"warning: the network set the clock, and GPS says it is "
              f"{describe(offset_s)}; leaving it to the network", flush=True)

    if synced or not may_set or abs(offset_s) <= STEP_IF_OFF_BY_S:
        return offset_s, None

    try:
        # Add the offset to the clock as it reads NOW, so the time spent
        # since the sentence arrived is kept.
        time.clock_settime(time.CLOCK_REALTIME, time.time() + offset_s)
    except PermissionError:
        print("could not set the clock: this needs CAP_SYS_TIME "
              "(see config/wildlife-gps.service)", flush=True)
        return offset_s, None
    except OSError as problem:
        print(f"could not set the clock: {problem}", flush=True)
        return offset_s, None

    try:
        os.utime(TIMESYNC_CLOCK)
    except OSError as problem:
        print(f"could not touch {TIMESYNC_CLOCK}: {problem}", flush=True)

    return offset_s, offset_s


def describe(offset_s):
    """+412337.6 -> '4.8 days slow';  -48.3 -> '48.3 s fast'."""
    size = abs(offset_s)
    way = "slow" if offset_s > 0 else "fast"
    if size >= 86400:
        return f"{size / 86400:.1f} days {way}"
    if size >= 3600:
        return f"{size / 3600:.1f} hours {way}"
    return f"{size:.1f} s {way}"


# ------------------------------------------------------------
# What we write
# ------------------------------------------------------------

def metres_between(a, b):
    """Distance between two (lat, lon) points, near enough for a garden."""
    mid = math.radians((a[0] + b[0]) / 2)
    north = (a[0] - b[0]) * 111_320
    east = (a[1] - b[1]) * 111_320 * math.cos(mid)
    return math.hypot(north, east)


def median_position(fixes):
    """The middle of the trusted fixes, and how much they disagreed."""
    lat = statistics.median(f["lat"] for f in fixes)
    lon = statistics.median(f["lon"] for f in fixes)
    altitudes = [f["alt_m"] for f in fixes if f["alt_m"] is not None]
    hdops = [f["hdop"] for f in fixes if f["hdop"] is not None]
    spread = statistics.median(metres_between((f["lat"], f["lon"]), (lat, lon))
                               for f in fixes)
    return {
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "alt_m": round(statistics.median(altitudes), 1) if altitudes else None,
        "hdop": round(statistics.median(hdops), 2) if hdops else None,
        "satellites": fixes[-1]["satellites"],
        "samples": len(fixes),
        "spread_m": round(spread, 1),
    }


def load_state(state_file):
    """gps.json if it is from this boot; otherwise a fresh one.

    A file from an earlier boot is left on disk untouched until this
    program has something trusted to replace it with.
    """
    try:
        with open(state_file) as f:
            state = json.load(f)
        if (state.get("boot") == BOOT_ID
                and isinstance(state.get("attachments"), list)
                and isinstance(state.get("clock"), dict)):
            return state
    except Exception:
        pass                    # missing, half-written or the wrong shape
    return {"camera": CAMERA_NAME, "boot": BOOT_ID, "receiver": None,
            "position": None,
            "clock": {"set_by_gps": False, "first_set_uptime_s": None,
                      "last_set_uptime_s": None},
            "attachments": []}


def save_state(state, state_file):
    """Write gps.json so a reader never sees half of it."""
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    temporary = state_file + ".tmp"
    with open(temporary, "w") as f:
        json.dump(state, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, state_file)


def log_row(photo_dir, fix, offset_s, stepped_s, attachment):
    """One row in <day>/gps-<camera>-<boot>.csv, safely on the card at once.

    Same pattern as the measurements CSV, so the sync scripts copy it with
    the photographs.  The day is the Pi's, read AFTER any clock change.
    """
    day = datetime.now().strftime("%Y-%m-%d")
    folder = os.path.join(photo_dir, day)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"gps-{CAMERA_NAME}-{BOOT_ID}.csv")
    row = [
        fix["utc"].strftime("%Y-%m-%dT%H:%M:%S.%f")[:-4] + "Z",
        datetime.fromtimestamp(fix["pi_time"], timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f")[:-4] + "Z",
        fix["uptime_s"], offset_s, "" if stepped_s is None else stepped_s,
        attachment, round(fix["lat"], 6), round(fix["lon"], 6),
        "" if fix["alt_m"] is None else fix["alt_m"],
        "" if fix["hdop"] is None else fix["hdop"],
        fix["satellites"], int(network_has_the_clock()),
    ]
    new = not os.path.exists(path)
    with open(path, "a") as f:
        if new:
            f.write(",".join(LOG_COLUMNS) + "\n")
        f.write(",".join(str(value) for value in row) + "\n")
        f.flush()
        os.fsync(f.fileno())


complaints = set()


def complain(message):
    """Say a problem once in the journal, then keep going.

    A camera with an unwritable folder should still get its clock set,
    not crash and restart every few seconds.
    """
    if message not in complaints:
        complaints.add(message)
        print(message, flush=True)


def status(text, status_file):
    """The journal, and a one-line file a phone on the camera's Wi-Fi can read.

    No coordinates in it, ever: it is served to anyone in range.
    """
    print(text, flush=True)
    if not status_file:
        return
    try:
        with open(status_file, "w") as f:
            f.write(f"{datetime.now(timezone.utc):%H:%M:%S} UTC  {text}\n")
    except OSError:
        pass                    # the journal has it; that is enough


def receiver_name(device):
    """What the USB device calls itself, e.g. 'u-blox 7 - GPS/GNSS Receiver'."""
    tty = os.path.basename(os.path.realpath(device))
    try:
        usb = os.path.realpath(f"/sys/class/tty/{tty}/device/..")
        with open(os.path.join(usb, "product")) as f:
            return f.read().strip()
    except OSError:
        return None


# ------------------------------------------------------------
# One attachment: from plug-in to pull-out
# ------------------------------------------------------------

def run(device, state_dir, photo_dir, status_file, may_set_clock, lines=None):
    state_file = os.path.join(state_dir, "gps.json")
    state = load_state(state_file)
    attachment = len(state["attachments"]) + 1
    receiver = receiver_name(device)

    trust = TrustTest()
    trusted = []                # this attachment's trusted fixes
    entry = None                # this attachment's line in gps.json
    last_log = last_check = last_waiting = None
    warned_moved = False

    status(f"dongle in ({receiver or device}); attachment {attachment} of "
           f"boot {BOOT_ID}; waiting for a fix", status_file)

    if lines is None:
        lines = lines_from(device)

    for fix in fixes_from(lines):
        now = fix["uptime_s"]

        if not trust.check(fix):
            if last_waiting is None or now - last_waiting >= 60:
                last_waiting = now
                if trust.in_a_row:
                    progress = f"valid {trust.in_a_row} s in a row"
                else:
                    progress = "no fix yet"
                if fix["in_view"] is None:
                    heard = f"{fix['satellites']} satellites in use"
                else:
                    heard = (f"{fix['in_view']} satellites in view, "
                             f"{fix['satellites']} in use")
                status(f"waiting: {heard}, {progress}", status_file)
            continue

        trusted.append(fix)
        first = entry is None
        check_due = last_check is not None and now - last_check >= CHECK_CLOCK_EVERY_S
        log_due = last_log is not None and now - last_log >= LOG_EVERY_S

        if not (first or check_due or log_due):
            continue

        # Measure the clock (and maybe set it) on the first trusted fix
        # and every ten minutes after; otherwise just log where we are.
        offset_s, stepped_s = None, None
        if first or check_due:
            offset_s, stepped_s = measure_and_set_clock(fix, may_set_clock)
            last_check = now
            if stepped_s is not None:
                clock = state["clock"]
                clock["set_by_gps"] = True
                if clock["first_set_uptime_s"] is None:
                    clock["first_set_uptime_s"] = now
                clock["last_set_uptime_s"] = now
        else:
            offset_s = round((fix["utc"] - datetime.fromtimestamp(
                fix["pi_time"], timezone.utc)).total_seconds(), 3)

        try:
            log_row(photo_dir, fix, offset_s, stepped_s, attachment)
        except OSError as problem:
            complain(f"could not write the log in {photo_dir}: {problem}")
        last_log = now

        if first:
            entry = {"first_fix_uptime_s": now, "last_fix_uptime_s": now,
                     "gps_utc": fix["utc"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "offset_s": offset_s,
                     "stepped": stepped_s is not None}
            state["attachments"].append(entry)
            state["receiver"] = receiver
            if stepped_s is not None:
                what = "corrected"
            elif network_has_the_clock():
                what = "left to the network"
            elif abs(offset_s) <= STEP_IF_OFF_BY_S:
                what = "close enough, left alone"
            elif not may_set_clock:
                what = "measured only (--no-clock)"
            else:
                what = "not corrected"
            status(f"fix {fix['utc']:%H:%M:%S} UTC with {fix['satellites']} "
                   f"satellites; clock was {describe(offset_s)}; {what}",
                   status_file)
        elif stepped_s is not None:
            print(f"clock had drifted {describe(offset_s)}; corrected",
                  flush=True)
        entry["last_fix_uptime_s"] = now

        # The boot's position belongs to the first attachment that found
        # one.  A later plug-in only checks the camera has not moved.
        here = median_position(trusted)
        if state["position"] is None or state["position"].get(
                "attachment") == attachment:
            state["position"] = dict(here, attachment=attachment)
        elif not warned_moved:
            moved = metres_between((here["lat"], here["lon"]),
                                   (state["position"]["lat"],
                                    state["position"]["lon"]))
            if moved > MOVED_M:
                warned_moved = True
                print(f"warning: {moved:.0f} m from where this boot's first "
                      f"fix put the camera -- was it moved? Keeping the "
                      f"first position.", flush=True)

        try:
            save_state(state, state_file)
        except OSError as problem:
            complain(f"could not write {state_file}: {problem} -- does "
                     f"{state_dir} exist, owned by this user?")

    print("dongle out", flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Set the clock and record the camera's position from "
                    "a USB GPS dongle.")
    parser.add_argument("device", nargs="?", default="/dev/gps0")
    parser.add_argument("--state-dir", default=STATE_DIR,
                        help=f"where gps.json goes (default {STATE_DIR})")
    parser.add_argument("--photo-dir", default=PHOTO_DIR,
                        help=f"where the log goes (default {PHOTO_DIR})")
    parser.add_argument("--status-file", default=STATUS_FILE,
                        help="one-line status for a phone; '' for none")
    parser.add_argument("--no-clock", action="store_true",
                        help="measure the clock but never set it")
    options = parser.parse_args()
    try:
        return run(options.device, options.state_dir, options.photo_dir,
                   options.status_file, not options.no_clock)
    except KeyboardInterrupt:
        print("stopped", flush=True)    # Ctrl-C when running it by hand
        return 0


if __name__ == "__main__":
    sys.exit(main())
