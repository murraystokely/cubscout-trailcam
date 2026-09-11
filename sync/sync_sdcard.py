#!/usr/bin/env python3
"""Copy photographs off a camera's SD card, instead of over Wi-Fi.

This program runs on a laptop (macOS or Linux), not on the Raspberry Pi.
Take the card out of the camera, put it in the laptop's card reader, and run
this. A card reader moves a day of photographs in seconds; the same copy over
the cameras' Wi-Fi can take an hour.

It files the photographs exactly where sync_cameras.py would have put them,
so the two programs can be mixed freely. Which camera a card belongs to is
read from the card itself:

    <card>/etc/hostname                       the camera's own name
    <card>/var/www/html/photos/<date>/*.jpg   the photographs

Both are on the big Linux partition. A Raspberry Pi card also has a small
FAT32 boot partition, which the desktop may mount as well; there are no
photographs on it.

With --wipe the photographs are deleted from the card once they are safely
copied, ready for the card to go back in the camera. That only ever touches
the photos directory, never the rest of the card, and a photograph is only
deleted after its copy on the laptop has been checked.

Examples:

    ./sync_sdcard.py --list                 # what is in the card reader?
    ./sync_sdcard.py                        # copy everything off it
    ./sync_sdcard.py /media/murray/rootfs   # a card that was not found
    ./sync_sdcard.py --wipe                 # copy, check, then empty the card
    ./sync_sdcard.py --wipe --checksum      # ...reading every byte first
"""

import argparse
import getpass
import hashlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

from sync_cameras import (
    DEFAULT_DEST,
    DEFAULT_USER,
    directory_stats,
    human,
    read_config,
    sanitize,
)

# Where the photographs are on the card, relative to the card's own root.
DEFAULT_PHOTO_SUBDIR = "var/www/html/photos"

# Where desktop Linux and macOS mount removable media.
MOUNT_ROOTS = ("/media", "/run/media", "/mnt", "/Volumes")

# Files that say "this is the small boot partition, not the photographs".
BOOT_PARTITION_FILES = ("cmdline.txt", "config.txt")

# How much to read at a time when checksumming.
CHUNK = 1024 * 1024


@dataclass
class Card:
    """One camera SD card, mounted on this laptop."""

    root: str  # the card's Linux partition
    hostname: str  # what the camera calls itself
    name: str  # the local directory its photographs go into
    photos: str  # the photo directory on the card
    source: str = ""  # how we found it, for the --list output
    files: int = 0
    bytes: int = 0


@dataclass
class Result:
    card: Card
    ok: bool = True
    copied: int = 0
    copied_bytes: int = 0
    removed: int = 0
    freed: int = 0
    problems: list = field(default_factory=list)
    message: str = ""


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------


def note(message: str) -> None:
    print(f"[sdcard] {message}")


def warn(message: str) -> None:
    print(f"[sdcard] warning: {message}", file=sys.stderr)


def print_cards(cards: list) -> None:
    width = max(len(c.name) for c in cards)
    print(f"Found {len(cards)} card(s):")
    for card in cards:
        print(
            f"  {card.name:<{width}}  {card.hostname:<16} "
            f"{card.files:>6} file(s)  {human(card.bytes):>10}  {card.root}"
        )


# --------------------------------------------------------------------------
# Finding cards
# --------------------------------------------------------------------------


def read_hostname(root: str) -> str:
    """The camera's own name, as recorded on its card."""
    path = os.path.join(root, "etc", "hostname")
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            fields = handle.read().split()
    except OSError:
        return ""
    if fields:
        return fields[0]
    # /etc/hostname is occasionally empty; /etc/hosts still knows the name.
    return hostname_from_hosts(os.path.join(root, "etc", "hosts"))


def hostname_from_hosts(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                fields = line.split("#", 1)[0].split()
                if len(fields) >= 2 and fields[0] == "127.0.1.1":
                    return fields[1]
    except OSError:
        pass
    return ""


def is_card_root(path: str) -> bool:
    """Is this a mounted camera card?

    This laptop's own filesystem has an /etc/hostname too, and walking up
    from a path given on the command line can easily arrive at it. It is
    never a camera card, and --wipe must never be pointed at it.
    """
    if os.path.realpath(path) == os.sep:
        return False
    return os.path.isfile(os.path.join(path, "etc", "hostname"))


def is_boot_partition(path: str) -> bool:
    return any(
        os.path.isfile(os.path.join(path, name)) for name in BOOT_PARTITION_FILES
    )


def card_root_from(path: str, photo_subdir: str) -> str:
    """Accept the card's mount point, or the photos directory inside it."""
    path = os.path.abspath(os.path.expanduser(path))
    if is_card_root(path):
        return path
    # They may have pointed at the photographs themselves, or at one day of
    # them. Walk up far enough to get back out to the root of the card.
    steps = len(photo_subdir.strip("/").split("/")) + 2
    walk = path
    for _ in range(steps):
        parent = os.path.dirname(walk)
        if parent == walk:
            break
        walk = parent
        if is_card_root(walk):
            return walk
    return ""


def candidate_mounts() -> list:
    """Every directory a card could plausibly be mounted on."""
    found = []
    user = getpass.getuser()
    for base in MOUNT_ROOTS:
        for directory in (os.path.join(base, user), base):
            try:
                entries = sorted(os.scandir(directory), key=lambda e: e.name)
            except OSError:
                continue
            for entry in entries:
                if entry.is_dir(follow_symlinks=False) and entry.path not in found:
                    found.append(entry.path)
    return found


def describe(root: str, options, names: dict, source: str):
    """Work out which camera a card belongs to and what is on it."""
    hostname = read_hostname(root)
    photos = os.path.join(root, options.photo_subdir)
    if not os.path.isdir(photos):
        warn(
            f"{root}: no photographs at {options.photo_subdir} -- is this a "
            f"wildlife camera's card?"
        )
        return None

    short = sanitize(hostname) if hostname else ""
    name = options.name or names.get(short) or short
    if not name:
        warn(f"{root}: cannot tell which camera this is; name it with --name")
        return None

    files, size = directory_stats(photos)
    return Card(
        root=root,
        hostname=hostname or "unknown",
        name=name,
        photos=photos,
        source=source,
        files=files,
        bytes=size,
    )


def discover(options, names: dict) -> list:
    """Look through the card reader for wildlife camera cards."""
    cards = []
    for mount in candidate_mounts():
        if not is_card_root(mount):
            if is_boot_partition(mount):
                note(
                    f"{mount} is the card's boot partition; the photographs "
                    f"are on its other partition"
                )
            continue
        card = describe(mount, options, names, source="card reader")
        if card:
            cards.append(card)
    return cards


def friendly_names(path: str) -> dict:
    """hostname -> directory name, from the cameras.conf sync_cameras reads."""
    names = {}
    for camera in read_config(path, DEFAULT_USER):
        names[sanitize(camera.host)] = camera.name
    return names


# --------------------------------------------------------------------------
# Copying
# --------------------------------------------------------------------------


def copy_card(card: Card, options) -> bool:
    """Copy the card's photographs into the camera's local directory."""
    destination = os.path.join(options.dest, card.name)
    os.makedirs(destination, exist_ok=True)

    command = ["rsync", "-a", "--human-readable", "--partial"]
    if options.verbose:
        command.append("--verbose")
    if options.progress:
        command.append("--info=progress2")
    if options.dry_run:
        command.append("--dry-run")
    # There is deliberately no --delete here. After a --wipe the card is
    # empty, and mirroring that emptiness back would erase the archive.
    command += [card.photos.rstrip("/") + os.sep, destination + os.sep]

    note(f"{card.name}: copying {human(card.bytes)} from {card.photos}")
    if options.verbose:
        note("  " + " ".join(command))

    try:
        # stdout and stderr are inherited so rsync's progress shows normally.
        completed = subprocess.run(command, check=False)
    except OSError as error:
        warn(f"{card.name}: could not run rsync: {error}")
        return False
    if completed.returncode != 0:
        warn(f"{card.name}: rsync exited {completed.returncode}")
        return False
    return True


# --------------------------------------------------------------------------
# Checking, before anything is deleted
# --------------------------------------------------------------------------


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def compare(source: str, copy: str, checksum: bool) -> str:
    """Return "" if the copy is good, otherwise why it is not."""
    try:
        original = os.stat(source)
    except OSError as error:
        return f"cannot read it on the card: {error}"
    if not os.path.isfile(copy):
        return "was not copied"
    try:
        if os.stat(copy).st_size != original.st_size:
            return "the copy is a different size"
        if checksum and sha256(copy) != sha256(source):
            return "the copy has different contents"
    except OSError as error:
        return f"cannot check the copy: {error}"
    return ""


def verify_copies(card: Card, options) -> tuple:
    """Check every photograph on the card against its copy on the laptop.

    Returns (verified, problems). Only the files in `verified` are safe to
    delete from the card.
    """
    destination = os.path.join(options.dest, card.name)
    inside = os.path.realpath(card.photos) + os.sep
    verified = []
    problems = []
    for directory, _, filenames in os.walk(card.photos):
        for filename in sorted(filenames):
            source = os.path.join(directory, filename)
            # Only ever plain files, and only ones genuinely on the card:
            # a symlink could otherwise point the delete somewhere else.
            if os.path.islink(source) or not os.path.isfile(source):
                continue
            if not os.path.realpath(source).startswith(inside):
                problems.append((source, "leads outside the photos directory"))
                continue
            relative = os.path.relpath(source, card.photos)
            trouble = compare(source, os.path.join(destination, relative),
                              options.checksum)
            if trouble:
                problems.append((source, trouble))
            else:
                verified.append(source)
    return verified, problems


def total_size(paths: list) -> int:
    total = 0
    for path in paths:
        try:
            total += os.path.getsize(path)
        except OSError:
            pass
    return total


# --------------------------------------------------------------------------
# Wiping the card
# --------------------------------------------------------------------------


def confirm(card: Card, count: int, size: int, destination: str) -> bool:
    print()
    print(f"About to delete {count} file(s), {human(size)}, from the card:")
    print(f"  {card.photos}")
    print("Every one has been copied to and checked against:")
    print(f"  {destination}")
    print("Nothing else on the card is touched.")
    if not sys.stdin.isatty():
        warn("not running in a terminal; pass --yes to wipe without asking")
        return False
    try:
        answer = input("Type 'wipe' to empty the card: ").strip()
    except EOFError:
        return False
    return answer == "wipe"


def prune_empty(top: str) -> None:
    """Remove day directories the wipe emptied. `top` itself always stays."""
    keep = os.path.realpath(top)
    for directory, _, _ in os.walk(top, topdown=False):
        if os.path.realpath(directory) == keep:
            continue
        try:
            os.rmdir(directory)  # fails harmlessly if anything is left in it
        except OSError:
            pass


def wipe(card: Card, verified: list) -> tuple:
    """Delete the checked photographs from the card, then tidy up."""
    removed = 0
    freed = 0
    failures = []
    for path in verified:
        try:
            size = os.path.getsize(path)
            os.remove(path)
            removed += 1
            freed += size
        except OSError as error:
            failures.append((path, str(error)))
    prune_empty(card.photos)
    return removed, freed, failures


# --------------------------------------------------------------------------
# One card, start to finish
# --------------------------------------------------------------------------


def handle(card: Card, options) -> Result:
    destination = os.path.join(options.dest, card.name)
    before = directory_stats(destination)

    result = Result(card=card)
    if not copy_card(card, options):
        result.ok = False
        result.message = "the copy failed, so the card was left alone"
        return result

    after = directory_stats(destination)
    result.copied = max(0, after[0] - before[0])
    result.copied_bytes = max(0, after[1] - before[1])

    if not options.wipe:
        return result
    if options.dry_run:
        note(f"{card.name}: --dry-run, so the card keeps its {card.files} file(s)")
        return result

    note(
        f"{card.name}: checking every file against its copy"
        + (" (sha256)" if options.checksum else "")
    )
    verified, problems = verify_copies(card, options)
    result.problems = problems
    if problems:
        result.ok = False
        for path, why in problems[:10]:
            warn(f"{os.path.relpath(path, card.photos)}: {why}")
        if len(problems) > 10:
            warn(f"...and {len(problems) - 10} more")
        result.message = f"not wiped: {len(problems)} file(s) did not check out"
        warn(f"{card.name}: {result.message}")
        return result

    if not verified:
        note(f"{card.name}: there is nothing on the card to delete")
        return result

    size = total_size(verified)
    if not options.yes and not confirm(card, len(verified), size, destination):
        result.message = "not wiped (declined)"
        note(f"{card.name}: left the card alone")
        return result

    removed, freed, failures = wipe(card, verified)
    result.removed = removed
    result.freed = freed
    if failures:
        result.ok = False
        result.message = f"could not delete {len(failures)} file(s)"
        for path, why in failures[:5]:
            warn(f"{path}: {why}")
        warn(
            "if the card is mounted read-only, eject it, mount it read-write "
            "and run this again"
        )
    return result


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def parse_args(argv):
    default_config = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "cameras.conf"
    )

    parser = argparse.ArgumentParser(
        description="Copy photographs from a wildlife camera's SD card to this "
        "laptop, and optionally empty the card afterwards.",
        epilog="Each card is filed under the camera's own hostname, read from "
        "/etc/hostname on the card, so the photographs land in the same place "
        "sync_cameras.py would have put them.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "cards",
        nargs="*",
        metavar="CARD",
        help="where a card is mounted, e.g. /media/you/rootfs (default: look "
        "in the card reader)",
    )

    parser.add_argument("--dest", default=DEFAULT_DEST,
                        help=f"local photo directory (default: {DEFAULT_DEST})")
    parser.add_argument("--config", default=default_config,
                        help=f"camera list file, for friendlier directory names "
                             f"(default: {default_config})")
    parser.add_argument("--name", default="",
                        help="file this card under this name instead of the "
                             "camera's hostname (one card at a time)")
    parser.add_argument("--photo-subdir", default=DEFAULT_PHOTO_SUBDIR,
                        help=f"where the photographs are on the card "
                             f"(default: {DEFAULT_PHOTO_SUBDIR})")

    parser.add_argument("--list", action="store_true",
                        help="only show the cards that were found, copy nothing")
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="show what would be copied without copying it")

    parser.add_argument("--wipe", action="store_true",
                        help="delete the photographs from the card once they "
                             "are copied and checked; nothing outside the "
                             "photos directory is touched")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="do not ask before wiping a card")
    parser.add_argument("--checksum", action="store_true",
                        help="check the copies by sha256 rather than by size "
                             "(slower, and surer, before a --wipe)")

    parser.add_argument("--verbose", "-v", action="store_true",
                        help="list every file as it is copied")
    parser.add_argument("--no-progress", dest="progress", action="store_false",
                        help="hide the rsync progress meter")

    options = parser.parse_args(argv)
    options.dest = os.path.abspath(os.path.expanduser(options.dest))
    options.config = os.path.expanduser(options.config)
    options.photo_subdir = options.photo_subdir.strip("/")
    return options


def main(argv=None) -> int:
    options = parse_args(argv if argv is not None else sys.argv[1:])

    if not shutil.which("rsync") and not options.list:
        warn("rsync is not installed on this laptop; install it and try again")
        return 2

    names = friendly_names(options.config)

    if options.cards:
        cards = []
        for given in options.cards:
            root = card_root_from(given, options.photo_subdir)
            if not root:
                if is_boot_partition(given):
                    warn(f"{given}: that is the card's boot partition; the "
                         f"photographs are on its other partition")
                else:
                    warn(f"{given}: not a camera card (no etc/hostname there, "
                         f"or in any directory above it)")
                continue
            cards.append(describe(root, options, names, source=given))
        cards = [c for c in cards if c]
    else:
        note("looking for camera cards...")
        cards = discover(options, names)

    if not cards:
        warn("no camera cards found.")
        warn("put a card in the reader and wait for the desktop to mount it,")
        warn("or say where it is: ./sync_sdcard.py /media/you/rootfs")
        return 1

    print_cards(cards)
    if options.list:
        return 0
    if options.name and len(cards) > 1:
        warn("--name only makes sense with a single card")
        return 2

    os.makedirs(options.dest, exist_ok=True)
    note(f"copying photographs into {options.dest}")
    results = [handle(card, options) for card in cards]

    print()
    print("Summary")
    print("-------")
    for result in results:
        line = f"  {result.card.name}: "
        line += "OK" if result.ok else "FAILED"
        line += f", {result.copied} new file(s), {human(result.copied_bytes)}"
        if result.removed:
            line += f", wiped {result.removed} from the card ({human(result.freed)})"
        if result.message:
            line += f" -- {result.message}"
        print(line)

    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        note("interrupted")
        sys.exit(130)
