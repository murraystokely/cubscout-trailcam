#!/usr/bin/env bash
#
# shrink_image.sh -- shrink a wildlife-camera image so it fits a smaller card.
#
# Two cards sold as "32 GB" are rarely the same number of bytes, and an
# image read off the larger one will not fit the smaller by a hair. This
# shrinks the image's last partition -- the Linux root filesystem -- until
# the whole thing fits the card you actually have, and leaves the boot
# partition alone.
#
# It works ONLY on an image file. It never opens a card, so unlike
# burn_image.sh there is no way for it to write over a disk. The dangerous
# thing here is different: a botched resize quietly corrupts an image you
# then copy to ten cameras. So the order of work is the careful one --
#
#   1. attach the image to a loop device (the file, never a real disk);
#   2. confirm the last partition really is ext4;
#   3. fsck it, and stop if it is not clean;
#   4. ask resize2fs how small it could possibly go, and refuse to go
#      below that;
#   5. shrink the filesystem;
#   6. shrink the partition to match the filesystem, never the reverse;
#   7. truncate the file to the end of the partition;
#   8. fsck again, mount it read-only, and check it still looks like a
#      Raspberry Pi root before saying it worked.
#
# Steps 6 and 7 are the ones that eat data if they run in the wrong order
# or with the wrong number, so each is computed from what the filesystem
# reports AFTER the resize, never from what we asked for.
#
# THIS SCRIPT DOES NOT BURN ANYTHING. It edits the image FILE, in place.
# --fit names a card only so its capacity can be read; nothing is ever
# written to it. Burn the result afterwards with burn_image.sh.
#
# Usage:
#   sudo ./shrink_image.sh [options] IMAGE
#
# Examples:
#   sudo ./shrink_image.sh --fit /dev/sda ~/wc10.img       # fit that card
#   sudo ./shrink_image.sh --size 29G ~/wc10.img           # fit a size
#   sudo ./shrink_image.sh --minimal ~/wc10.img            # as small as it goes
#   sudo ./shrink_image.sh --fit /dev/sda --output ~/small.img ~/wc10.img

set -euo pipefail

PROG=${0##*/}

msg()  { printf '[shrink] %s\n' "$*"; }
warn() { printf '[shrink] warning: %s\n' "$*" >&2; }
die()  { printf '[shrink] error: %s\n' "$*" >&2; exit 1; }

human() { numfmt --to=iec --suffix=B "$1" 2>/dev/null || echo "$1 bytes"; }

usage() {
    cat <<EOF
Shrink a wildlife-camera image so it fits a smaller card.

This does NOT burn anything. It rewrites the image FILE, in place, and
leaves it for burn_image.sh to write to a card afterwards.

Usage:
  sudo $PROG [options] IMAGE

Arguments:
  IMAGE    the image file to shrink, IN PLACE (use --output to keep it)

Options:
  --fit DEVICE    shrink until it fits this card, e.g. /dev/sda.  Only the
                  card's capacity is read; nothing is written to it
  --size SIZE     fit this many bytes instead of a card (accepts K/M/G)
  --minimal       shrink as far as the filesystem allows, ignoring the target
  --margin SIZE   keep this much spare so the image also fits a card that is
                  a little smaller (default ${DEFAULT_MARGIN_HUMAN})
  --output FILE   work on a copy and leave IMAGE untouched (needs the space)
  -n, --dry-run   work out the plan and print it, change nothing
  -y, --yes       skip the typed confirmation
  -h, --help      show this help

The Pi grows the filesystem back on first boot, so a shrunken image is not
a smaller camera:  sudo raspi-config --expand-rootfs
EOF
}

DEFAULT_MARGIN=16777216            # 16 MiB
DEFAULT_MARGIN_HUMAN="16MiB"

# --------------------------------------------------------------------------
# Parse arguments
# --------------------------------------------------------------------------

TARGET_SIZE=""
DEVICE=""
MINIMAL=0
MARGIN=$DEFAULT_MARGIN
OUTPUT=""
DRY_RUN=0
ASSUME_YES=0
POSITIONAL=()

to_bytes() {
    local v="$1"
    if [[ "$v" =~ ^[0-9]+$ ]]; then echo "$v"; return; fi
    numfmt --from=iec -- "$v" 2>/dev/null || die "cannot understand size: $v"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --fit)      [ $# -ge 2 ] || die "--fit needs a device, e.g. --fit /dev/sda"
                    DEVICE="$2"; shift 2 ;;
        --size)     [ $# -ge 2 ] || die "--size needs a value, e.g. --size 29G"
                    TARGET_SIZE=$(to_bytes "$2"); shift 2 ;;
        --minimal)  MINIMAL=1; shift ;;
        --margin)   [ $# -ge 2 ] || die "--margin needs a value, e.g. --margin 16M"
                    MARGIN=$(to_bytes "$2"); shift 2 ;;
        --output)   [ $# -ge 2 ] || die "--output needs a filename"
                    OUTPUT="$2"; shift 2 ;;
        -n|--dry-run) DRY_RUN=1; shift ;;
        -y|--yes)   ASSUME_YES=1; shift ;;
        -h|--help)  usage; exit 0 ;;
        --)         shift; while [ $# -gt 0 ]; do POSITIONAL+=("$1"); shift; done ;;
        -*)         die "unknown option: $1 (try --help)" ;;
        *)          POSITIONAL+=("$1"); shift ;;
    esac
done

# burn_image.sh takes IMAGE and DEVICE as two positionals, so that habit
# turns up here.  Say what to do instead, rather than printing the usage
# and letting someone guess which half was wrong.
for arg in ${POSITIONAL+"${POSITIONAL[@]}"}; do
    case "$arg" in
        /dev/*) die "$arg is a card, and this script never writes to one. Pass only the image FILE and name the card with --fit:  $PROG --fit $arg IMAGE" ;;
    esac
done

if [ "${#POSITIONAL[@]}" -ne 1 ]; then
    [ "${#POSITIONAL[@]}" -gt 1 ] && warn "this takes one image; --fit names the card"
    usage
    exit 2
fi
IMAGE=${POSITIONAL[0]}

# --------------------------------------------------------------------------
# Refuse anything that is not a plain file
# --------------------------------------------------------------------------

[ -e "$IMAGE" ] || die "image not found: $IMAGE"
[ -b "$IMAGE" ] && die "$IMAGE is a block device. This script only ever shrinks an image FILE; it will not touch a card."
[ -f "$IMAGE" ] || die "$IMAGE is not a regular file"

if [ -n "$DEVICE" ] && [ -n "$TARGET_SIZE" ]; then
    die "give either --fit or --size, not both"
fi
if [ -z "$DEVICE" ] && [ -z "$TARGET_SIZE" ] && [ "$MINIMAL" -ne 1 ]; then
    die "say what it has to fit: --fit DEVICE, or --size SIZE, or --minimal"
fi

# --------------------------------------------------------------------------
# Become root and check tools
# --------------------------------------------------------------------------

if [ "$(id -u)" -ne 0 ]; then
    msg "re-running under sudo (loop devices and e2fsck need root)"
    reexec=()
    [ -n "$DEVICE" ]         && reexec+=(--fit "$DEVICE")
    [ -n "$TARGET_SIZE" ]    && reexec+=(--size "$TARGET_SIZE")
    [ "$MINIMAL" -eq 1 ]     && reexec+=(--minimal)
    [ "$MARGIN" -ne "$DEFAULT_MARGIN" ] && reexec+=(--margin "$MARGIN")
    [ -n "$OUTPUT" ]         && reexec+=(--output "$OUTPUT")
    [ "$DRY_RUN" -eq 1 ]     && reexec+=(--dry-run)
    [ "$ASSUME_YES" -eq 1 ]  && reexec+=(--yes)
    exec sudo -- "$0" "${reexec[@]}" -- "$IMAGE"
fi

for tool in losetup sfdisk e2fsck resize2fs dumpe2fs truncate blkid numfmt findmnt; do
    command -v "$tool" >/dev/null 2>&1 || die "required tool not installed: $tool"
done

# --------------------------------------------------------------------------
# Work out the target size
# --------------------------------------------------------------------------

IMG_BYTES=$(stat -c %s -- "$IMAGE")

if [ -n "$DEVICE" ]; then
    [ -b "$(readlink -f -- "$DEVICE")" ] || die "$DEVICE is not a block device"
    DEV=$(readlink -f -- "$DEVICE")
    TARGET_SIZE=$(blockdev --getsize64 "$DEV")
    msg "$DEVICE holds $(human "$TARGET_SIZE") (reading its size only; nothing is written to it)"

    # We only read its size, but say so plainly if it is in use.
    for part in $(lsblk -lno NAME -- "$DEV" | tail -n +2); do
        if findmnt -rno TARGET "/dev/$part" >/dev/null 2>&1; then
            warn "/dev/$part is mounted. Nothing here writes to it, but unmount it before burning."
        fi
    done
fi

if [ "$MINIMAL" -eq 1 ]; then
    BUDGET=0
else
    BUDGET=$(( TARGET_SIZE - MARGIN ))
    [ "$BUDGET" -gt 0 ] || die "margin $(human "$MARGIN") is larger than the target $(human "$TARGET_SIZE")"
    if [ "$IMG_BYTES" -le "$BUDGET" ]; then
        msg "$IMAGE is already $(human "$IMG_BYTES"), which fits $(human "$TARGET_SIZE") with $(human "$MARGIN") to spare."
        msg "Nothing to do."
        exit 0
    fi
fi

# --------------------------------------------------------------------------
# Optionally work on a copy
# --------------------------------------------------------------------------

WORK="$IMAGE"
if [ -n "$OUTPUT" ]; then
    [ -e "$OUTPUT" ] && die "$OUTPUT already exists"
    OUTDIR=$(dirname -- "$OUTPUT")
    FREE=$(df --output=avail -B1 -- "$OUTDIR" | tail -1 | tr -dc '0-9')
    [ "${FREE:-0}" -ge "$IMG_BYTES" ] || die "$OUTDIR has $(human "${FREE:-0}") free but the copy needs $(human "$IMG_BYTES")"
    if [ "$DRY_RUN" -eq 0 ]; then
        msg "copying $IMAGE -> $OUTPUT"
        cp --sparse=always -- "$IMAGE" "$OUTPUT"
    fi
    WORK="$OUTPUT"
fi

# --------------------------------------------------------------------------
# Attach the image, and always clean up after ourselves
# --------------------------------------------------------------------------

LOOP=""
MNT=""
cleanup() {
    [ -n "$MNT" ] && mountpoint -q "$MNT" && umount "$MNT" 2>/dev/null || true
    [ -n "$MNT" ] && rmdir "$MNT" 2>/dev/null || true
    [ -n "$LOOP" ] && losetup -d "$LOOP" 2>/dev/null || true
}
trap cleanup EXIT

attach() {
    LOOP=$(losetup -P -f --show -- "$WORK") || die "could not attach $WORK to a loop device"
    udevadm settle 2>/dev/null || sleep 1
}

# --------------------------------------------------------------------------
# Find the last partition and check it is one we can shrink
# --------------------------------------------------------------------------

SECTOR=512

# sfdisk pads its output ("start=     1064960,"), so pull the numbers out
# with a pattern rather than by splitting on whitespace.
LAST_LINE=$(sfdisk -d -- "$WORK" | grep 'start=' | tail -1) \
    || die "could not read a partition table from $WORK"
[ -n "$LAST_LINE" ] || die "no partitions found in $WORK"

LAST_DEV=$(printf '%s' "$LAST_LINE" | awk -F' *: *' '{print $1}')
LAST_NUM=${LAST_DEV##*[!0-9]}          # trailing digits of e.g. wc10.img2
PART_START=$(printf '%s' "$LAST_LINE" | grep -oE 'start=[[:space:]]*[0-9]+' | grep -oE '[0-9]+')
PART_SECTORS=$(printf '%s' "$LAST_LINE" | grep -oE 'size=[[:space:]]*[0-9]+' | grep -oE '[0-9]+')

[[ "$LAST_NUM" =~ ^[0-9]+$ ]] || die "could not work out the last partition number from '$LAST_DEV'"
[ -n "$PART_START" ] && [ -n "$PART_SECTORS" ] || die "could not read the last partition from $WORK"

attach
PART_DEV="${LOOP}p${LAST_NUM}"
[ -b "$PART_DEV" ] || die "expected partition device $PART_DEV did not appear"

FSTYPE=$(blkid -o value -s TYPE -- "$PART_DEV" 2>/dev/null || true)
case "$FSTYPE" in
    ext2|ext3|ext4) : ;;
    *) die "the last partition is '$FSTYPE', not ext2/3/4. Only the Linux root filesystem can be shrunk here; the FAT boot partition is left alone." ;;
esac

msg "last partition: #$LAST_NUM  $FSTYPE  start=$PART_START  sectors=$PART_SECTORS  ($(human $((PART_SECTORS * SECTOR))))"

# --------------------------------------------------------------------------
# Check it, then ask how small it could possibly be
# --------------------------------------------------------------------------

msg "checking the filesystem (this takes a minute on a big image)"
e2fsck -f -p -- "$PART_DEV" || {
    rc=$?
    [ "$rc" -le 2 ] || die "e2fsck says the filesystem is damaged (exit $rc). Fix or re-capture the image before shrinking it."
}

BLOCK_SIZE=$(dumpe2fs -h "$PART_DEV" 2>/dev/null | awk -F': *' '/Block size/{print $2}')
CUR_BLOCKS=$(dumpe2fs -h "$PART_DEV" 2>/dev/null | awk -F': *' '/Block count/{print $2}')
MIN_BLOCKS=$(resize2fs -P "$PART_DEV" 2>/dev/null | awk -F': *' '{print $NF}')
[ -n "$BLOCK_SIZE" ] && [ -n "$CUR_BLOCKS" ] && [ -n "$MIN_BLOCKS" ] || die "could not read the filesystem geometry"

# resize2fs -P is an estimate, and shrinking to exactly it sometimes fails.
# Give it room, and never let the result be smaller than the estimate.
SLACK_BLOCKS=$(( 64 * 1024 * 1024 / BLOCK_SIZE ))       # 64 MiB of elbow room
FLOOR_BLOCKS=$(( MIN_BLOCKS + SLACK_BLOCKS ))

msg "filesystem: block size $BLOCK_SIZE, $CUR_BLOCKS blocks ($(human $((CUR_BLOCKS * BLOCK_SIZE))))"
msg "smallest it can be: $MIN_BLOCKS blocks ($(human $((MIN_BLOCKS * BLOCK_SIZE)))), plus 64MiB elbow room"

# --------------------------------------------------------------------------
# Decide the new size
# --------------------------------------------------------------------------

if [ "$MINIMAL" -eq 1 ]; then
    NEW_BLOCKS=$FLOOR_BLOCKS
else
    # The image ends where the last partition ends, so the partition may
    # occupy at most (budget - start).
    MAX_PART_SECTORS=$(( BUDGET / SECTOR - PART_START ))
    [ "$MAX_PART_SECTORS" -gt 0 ] || die "the boot partition alone overflows $(human "$BUDGET")"
    NEW_BLOCKS=$(( MAX_PART_SECTORS * SECTOR / BLOCK_SIZE ))

    if [ "$NEW_BLOCKS" -lt "$FLOOR_BLOCKS" ]; then
        die "will not fit: the data needs at least $(human $((FLOOR_BLOCKS * BLOCK_SIZE))) but the card leaves room for $(human $((NEW_BLOCKS * BLOCK_SIZE))). Delete something on the master, or use a larger card."
    fi
fi

if [ "$NEW_BLOCKS" -ge "$CUR_BLOCKS" ]; then
    msg "the filesystem is already small enough; only the empty tail needs trimming."
    NEW_BLOCKS=$CUR_BLOCKS
fi

NEW_PART_SECTORS=$(( NEW_BLOCKS * BLOCK_SIZE / SECTOR ))
NEW_IMG_BYTES=$(( (PART_START + NEW_PART_SECTORS) * SECTOR ))

echo
if [ -n "$OUTPUT" ]; then
    echo "  WILL REWRITE : $WORK  (a copy; $IMAGE is left alone)"
else
    echo "  WILL REWRITE : $WORK  (in place)"
fi
echo "  no card is written to by this script."
echo "  image        : $WORK"
echo "  now          : $(human "$IMG_BYTES")"
echo "  after        : $(human "$NEW_IMG_BYTES")   (saving $(human $((IMG_BYTES - NEW_IMG_BYTES))))"
[ "$MINIMAL" -eq 1 ] || echo "  must fit     : $(human "$TARGET_SIZE") less $(human "$MARGIN") margin = $(human "$BUDGET")"
echo "  filesystem   : $CUR_BLOCKS -> $NEW_BLOCKS blocks"
echo

if [ "$MINIMAL" -ne 1 ] && [ "$NEW_IMG_BYTES" -gt "$BUDGET" ]; then
    die "internal check failed: the plan still does not fit. Refusing to touch the image."
fi

if [ "$DRY_RUN" -eq 1 ]; then
    msg "dry run: nothing was changed."
    exit 0
fi

if [ "$ASSUME_YES" -ne 1 ]; then
    printf 'Type the image name to confirm shrinking it (%s): ' "$(basename -- "$WORK")"
    read -r answer
    [ "$answer" = "$(basename -- "$WORK")" ] || die "confirmation did not match ('$answer'); aborting."
fi

# --------------------------------------------------------------------------
# Shrink: filesystem first, then partition, then file
# --------------------------------------------------------------------------

if [ "$NEW_BLOCKS" -lt "$CUR_BLOCKS" ]; then
    msg "shrinking the filesystem to $NEW_BLOCKS blocks"
    resize2fs "$PART_DEV" "$NEW_BLOCKS" || die "resize2fs failed; the image is unchanged on disk apart from the filesystem, run e2fsck on it before using it"
fi

# Re-read what the filesystem ACTUALLY became. resize2fs rounds, and the
# partition must be sized from the truth, not from what we asked for.
FINAL_BLOCKS=$(dumpe2fs -h "$PART_DEV" 2>/dev/null | awk -F': *' '/Block count/{print $2}')
[ -n "$FINAL_BLOCKS" ] || die "could not confirm the new filesystem size"
FINAL_PART_SECTORS=$(( (FINAL_BLOCKS * BLOCK_SIZE + SECTOR - 1) / SECTOR ))
msg "filesystem is now $FINAL_BLOCKS blocks ($(human $((FINAL_BLOCKS * BLOCK_SIZE))))"

losetup -d "$LOOP"; LOOP=""

msg "shrinking partition #$LAST_NUM to $FINAL_PART_SECTORS sectors"
echo ",$FINAL_PART_SECTORS" | sfdisk --no-reread --no-tell-kernel -N "$LAST_NUM" -- "$WORK" >/dev/null \
    || die "sfdisk could not resize the partition; the filesystem is already smaller, so re-run to finish"

FINAL_IMG_BYTES=$(( (PART_START + FINAL_PART_SECTORS) * SECTOR ))
msg "truncating the image to $(human "$FINAL_IMG_BYTES")"
truncate -s "$FINAL_IMG_BYTES" -- "$WORK"

# --------------------------------------------------------------------------
# Prove it still works before saying it worked
# --------------------------------------------------------------------------

msg "verifying"
attach
PART_DEV="${LOOP}p${LAST_NUM}"
[ -b "$PART_DEV" ] || die "the shrunken image has no partition $LAST_NUM -- do not burn it"

e2fsck -f -p -- "$PART_DEV" || {
    rc=$?
    [ "$rc" -le 2 ] || die "the shrunken filesystem does not check out (exit $rc) -- do not burn it"
}

MNT=$(mktemp -d)
mount -o ro "$PART_DEV" "$MNT" || die "the shrunken filesystem will not mount -- do not burn it"
for want in /etc/hostname /etc/fstab /home; do
    [ -e "$MNT$want" ] || die "$want is missing from the shrunken image -- do not burn it"
done
HOST=$(cat "$MNT/etc/hostname" 2>/dev/null || echo "?")
umount "$MNT"; rmdir "$MNT"; MNT=""
losetup -d "$LOOP"; LOOP=""

FINAL=$(stat -c %s -- "$WORK")
echo
msg "done. $WORK is now $(human "$FINAL") (was $(human "$IMG_BYTES"))."
msg "it still contains the root filesystem for '$HOST'."
if [ -n "$DEVICE" ]; then
    if [ "$FINAL" -le "$TARGET_SIZE" ]; then
        msg "it fits $DEVICE ($(human "$TARGET_SIZE")) with $(human $((TARGET_SIZE - FINAL))) to spare."
    else
        die "it still does not fit $DEVICE. Use --minimal, or a larger card."
    fi
fi
echo
echo "Next:"
echo "  sudo ./burn_image.sh --verify $WORK ${DEVICE:-/dev/sdX}"
echo "  then on the Pi's first boot:  sudo raspi-config --expand-rootfs"
