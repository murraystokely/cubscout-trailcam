#!/usr/bin/env bash
#
# capture_image.sh -- read a wildlife-camera SD card into an image file.
#
# This runs on a Linux laptop and is the FIRST step of cloning: it turns the
# master camera's card into the master image that clone_image.sh personalizes
# and burn_image.sh writes back out. It automates Part 2, sections 11-13 of
# ../docs/sdcard-image-instructions.md.
#
# Reading is far less dangerous than writing -- nothing on the card is
# changed -- but it is still easy to point at the wrong device and spend
# twenty minutes copying the laptop's own disk into your home directory. So
# the same checks apply, before a single byte is read:
#
#   * the source must be a block device that exists;
#   * it must be a WHOLE disk, not a partition (e.g. /dev/sdc, not /dev/sdc1)
#     -- a partition image is not a bootable card;
#   * it must NOT be the disk backing the running system;
#   * it must look REMOVABLE (kernel "removable" flag, hotplug, or a USB/MMC
#     transport) -- an internal NVMe/SATA disk is rejected;
#   * the destination must have room for the image;
#   * an existing image file is never overwritten without --force.
#
# The card is unmounted first: copying a mounted filesystem captures it
# half-written, and the clone may not boot.
#
# The output format follows the filename, the same way clone_image.sh works:
# end it in .gz or .xz to compress, anything else to write raw. Compressing
# is well worth it if you zeroed the free space first (section 9 of the
# guide) -- a 32 GB card can come down to well under 2 GB.
#
# Root is required to read a block device, so the script re-runs itself under
# sudo, then hands the finished image back to you rather than leaving it
# owned by root.
#
# Usage:
#   sudo ./capture_image.sh [--verify] [--force] DEVICE IMAGE
#
# Example:
#   sudo ./capture_image.sh --verify /dev/sdc ~/webelos-wildlifecam.img.gz

set -euo pipefail

PROG=${0##*/}

msg()  { printf '[capture] %s\n' "$*"; }
warn() { printf '[capture] warning: %s\n' "$*" >&2; }
die()  { printf '[capture] error: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<EOF
Read an SD card into a wildlife-camera image file, with safety checks.

Usage:
  sudo $PROG [options] DEVICE IMAGE

Arguments:
  DEVICE   whole SD-card device, e.g. /dev/sdc (never a partition)
  IMAGE    image to create: .img, .img.gz, or .img.xz

Options:
  --verify           read the card back and compare it to the image
  -f, --force        overwrite IMAGE if it already exists
  --allow-internal   permit a non-removable source (the system-disk and
                     partition checks still apply)
  -h, --help         show this help

Find the device name first with:  lsblk -o NAME,SIZE,TYPE,RM,TRAN,MODEL

Next steps:
  clone_image.sh   personalize the image for one camera
  burn_image.sh    write a personalized image back to a card
EOF
}

# --------------------------------------------------------------------------
# Parse arguments
# --------------------------------------------------------------------------

VERIFY=0
FORCE=0
ALLOW_INTERNAL=0
POSITIONAL=()

while [ $# -gt 0 ]; do
    case "$1" in
        --verify)         VERIFY=1; shift ;;
        -f|--force)       FORCE=1; shift ;;
        --allow-internal) ALLOW_INTERNAL=1; shift ;;
        -h|--help)        usage; exit 0 ;;
        --)               shift; while [ $# -gt 0 ]; do POSITIONAL+=("$1"); shift; done ;;
        -*)               die "unknown option: $1 (try --help)" ;;
        *)                POSITIONAL+=("$1"); shift ;;
    esac
done

[ "${#POSITIONAL[@]}" -eq 2 ] || { usage; exit 2; }
DEVICE=${POSITIONAL[0]}
IMAGE=${POSITIONAL[1]}

# The easiest mistake to make is passing the two arguments the other way
# round, so say so plainly rather than complaining about a missing device.
case "$DEVICE" in
    /dev/*) ;;
    *) die "DEVICE should be a card under /dev, e.g. /dev/sdc -- got '$DEVICE'. The order is DEVICE then IMAGE: $PROG /dev/sdc ~/master.img" ;;
esac
case "$IMAGE" in
    /dev/*) die "IMAGE looks like a device ($IMAGE). The order is DEVICE then IMAGE: $PROG /dev/sdc ~/master.img" ;;
esac

[ -e "$DEVICE" ] || die "device not found: $DEVICE"

# --------------------------------------------------------------------------
# Become root and check tools
# --------------------------------------------------------------------------

if [ "$(id -u)" -ne 0 ]; then
    msg "re-running under sudo (reading a disk needs root)"
    reexec=()
    [ "$VERIFY" -eq 1 ]         && reexec+=(--verify)
    [ "$FORCE" -eq 1 ]          && reexec+=(--force)
    [ "$ALLOW_INTERNAL" -eq 1 ] && reexec+=(--allow-internal)
    exec sudo -- "$0" "${reexec[@]}" -- "$DEVICE" "$IMAGE"
fi

for tool in lsblk findmnt blockdev dd umount df; do
    command -v "$tool" >/dev/null 2>&1 || die "required tool not installed: $tool"
done
case "$IMAGE" in
    *.gz) command -v gzip >/dev/null || die "gzip needed for a .gz image" ;;
    *.xz) command -v xz   >/dev/null || die "xz needed for an .xz image" ;;
esac
[ "$VERIFY" -eq 0 ] || command -v cmp >/dev/null || die "cmp needed for --verify"

# lsblk right-aligns numeric columns even with -n, so RM comes back as " 0"
# rather than "0" and every numeric test below silently fails. Trim it.
lsblk1() { lsblk -dno "$1" -- "$2" 2>/dev/null | head -1 | tr -d '[:space:]'; }

# Resolve the device to its real path (follows /dev/disk/by-id/... symlinks).
DEV=$(readlink -f -- "$DEVICE")
[ -b "$DEV" ] || die "$DEVICE ($DEV) is not a block device"
KNAME=$(lsblk1 KNAME "$DEV")
[ -n "$KNAME" ] || die "could not identify $DEV"

# --------------------------------------------------------------------------
# Safety gate 1: must be a whole disk, not a partition
# --------------------------------------------------------------------------

DTYPE=$(lsblk1 TYPE "$DEV")
if [ "$DTYPE" != disk ]; then
    die "$DEV is a '$DTYPE', not a whole disk. Pass the whole card (e.g. /dev/sdc), not a partition -- an image of one partition will not boot."
fi

# --------------------------------------------------------------------------
# Safety gate 2: must not be the running system's disk
# --------------------------------------------------------------------------

# The whole-disk kname behind a device path, e.g. /dev/sda2 -> sda.
disk_of() {
    local src="$1" pk
    pk=$(lsblk -no PKNAME -- "$src" 2>/dev/null | head -1)
    if [ -n "$pk" ]; then echo "$pk"; else lsblk -dno KNAME -- "$src" 2>/dev/null | head -1; fi
}

for mp in / /boot /boot/firmware /home; do
    src=$(findmnt -nro SOURCE --target "$mp" 2>/dev/null | head -1) || true
    [ -n "$src" ] || continue
    case "$src" in
        "$DEV"|"$DEV"[0-9]*|"$DEV"p[0-9]*)
            die "$DEV backs $mp ($src) on this laptop -- that is the laptop's own disk, not the SD card. Refusing." ;;
    esac
done

PROTECTED=$(
    for mp in / /boot /boot/firmware /home; do
        src=$(findmnt -nro SOURCE --target "$mp" 2>/dev/null | head -1) || true
        case "$src" in /dev/*) disk_of "$src" ;; esac
    done
    if [ -r /proc/swaps ]; then
        awk 'NR>1{print $1}' /proc/swaps | while read -r s; do
            case "$s" in /dev/*) disk_of "$s" ;; esac
        done
    fi
)
if printf '%s\n' "$PROTECTED" | grep -qx -- "$KNAME"; then
    die "$DEV ($KNAME) holds the running system or its swap. Refusing."
fi

# --------------------------------------------------------------------------
# Safety gate 3: must look removable
# --------------------------------------------------------------------------

RM=$(lsblk1 RM "$DEV")
HOTPLUG=$(lsblk1 HOTPLUG "$DEV")
TRAN=$(lsblk1 TRAN "$DEV")
SYSRM=$(cat "/sys/block/$KNAME/removable" 2>/dev/null || echo 0)

REMOVABLE=no
if [ "$RM" = 1 ] || [ "$HOTPLUG" = 1 ] || [ "$SYSRM" = 1 ] || \
   [ "$TRAN" = usb ] || [ "$TRAN" = mmc ]; then
    REMOVABLE=yes
fi
if [ "$REMOVABLE" != yes ]; then
    if [ "$ALLOW_INTERNAL" -eq 1 ]; then
        warn "$DEV does not look removable (RM=$RM HOTPLUG=$HOTPLUG TRAN=$TRAN); continuing because --allow-internal was given."
    else
        die "$DEV does not look like a removable card (RM=$RM HOTPLUG=$HOTPLUG TRAN=${TRAN:-none}). Refusing. Re-check the device, or pass --allow-internal only if you are certain."
    fi
fi

# --------------------------------------------------------------------------
# Safety gate 4: somewhere to put it
# --------------------------------------------------------------------------

if [ -e "$IMAGE" ] && [ "$FORCE" -ne 1 ]; then
    die "$IMAGE already exists. Move it aside, or pass --force to overwrite it."
fi

OUTDIR=$(dirname -- "$IMAGE")
[ -d "$OUTDIR" ] || die "output directory does not exist: $OUTDIR"

# Never write the image onto the card we are reading.
OUTSRC=$(findmnt -nro SOURCE --target "$OUTDIR" 2>/dev/null | head -1) || true
case "$OUTSRC" in
    "$DEV"|"$DEV"[0-9]*|"$DEV"p[0-9]*)
        die "$OUTDIR lives on $DEV, the card being read. Choose somewhere on the laptop." ;;
esac

DEV_BYTES=$(blockdev --getsize64 "$DEV")
FREE_BYTES=$(df --output=avail -B1 -- "$OUTDIR" 2>/dev/null | tail -1 | tr -dc '0-9')
FREE_BYTES=${FREE_BYTES:-0}

human() { numfmt --to=iec --suffix=B "$1" 2>/dev/null || echo "$1 bytes"; }

case "$IMAGE" in
    *.gz|*.xz)
        # We cannot know the compressed size in advance. A zeroed card packs
        # down enormously; a full one barely at all.
        if [ "$FREE_BYTES" -lt "$DEV_BYTES" ]; then
            warn "$OUTDIR has $(human "$FREE_BYTES") free and the card is $(human "$DEV_BYTES"). Compression will probably fit, but it is not guaranteed -- especially if the card's free space was never zeroed."
        fi ;;
    *)
        if [ "$FREE_BYTES" -lt "$DEV_BYTES" ]; then
            die "$OUTDIR has only $(human "$FREE_BYTES") free but a raw image of $DEV needs $(human "$DEV_BYTES"). Free some space, or end the filename in .gz or .xz to compress it."
        fi ;;
esac

# --------------------------------------------------------------------------
# Show what we are about to do
# --------------------------------------------------------------------------

echo
echo "About to read this device into an image file:"
lsblk -o NAME,SIZE,TYPE,RM,HOTPLUG,TRAN,MOUNTPOINTS,MODEL -- "$DEV" || true
echo
echo "  device: $DEV  (removable=$REMOVABLE, $(human "$DEV_BYTES"))"
echo "  image : $IMAGE"
echo "  free  : $(human "$FREE_BYTES") in $OUTDIR"
echo

if [ "$DEV_BYTES" -gt $((256 * 1024 * 1024 * 1024)) ]; then
    warn "this device is larger than a typical SD card -- double-check it is really the card."
fi

# --------------------------------------------------------------------------
# Unmount the card so we capture a consistent filesystem
# --------------------------------------------------------------------------

for part in $(lsblk -lno NAME -- "$DEV" | tail -n +2); do
    if findmnt -rno TARGET "/dev/$part" >/dev/null 2>&1; then
        msg "unmounting /dev/$part"
        umount "/dev/$part" 2>/dev/null || true
    fi
done
STILL=""
for part in $(lsblk -lno NAME -- "$DEV" | tail -n +2); do
    if findmnt -rno TARGET "/dev/$part" >/dev/null 2>&1; then
        STILL="$STILL /dev/$part"
    fi
done
[ -z "$STILL" ] || die "still mounted:$STILL -- unmount it and retry. Reading a mounted card captures it half-written."

# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------

# A dying card gives read errors. We deliberately do NOT pass conv=noerror,
# which would paper over bad blocks with zeroes and hand you a corrupt image
# that looks fine.
msg "reading $DEV -> $IMAGE (this takes a while; do not remove the card)"

rm -f -- "$IMAGE"
case "$IMAGE" in
    *.gz) dd if="$DEV" bs=4M status=progress | gzip -c > "$IMAGE" ;;
    *.xz) dd if="$DEV" bs=4M status=progress | xz -c -T0 > "$IMAGE" ;;
    *)    dd if="$DEV" of="$IMAGE" bs=4M conv=fsync status=progress ;;
esac
sync

# --------------------------------------------------------------------------
# Optional verification
# --------------------------------------------------------------------------

if [ "$VERIFY" -eq 1 ]; then
    msg "verifying the image against the card..."
    blockdev --flushbufs "$DEV" 2>/dev/null || true
    vout=""
    vrc=0
    case "$IMAGE" in
        *.gz) vout=$(gzip -dc -- "$IMAGE" | cmp - "$DEV" 2>&1) || vrc=$? ;;
        *.xz) vout=$(xz -dc -- "$IMAGE"   | cmp - "$DEV" 2>&1) || vrc=$? ;;
        *)    vout=$(cmp -- "$IMAGE" "$DEV" 2>&1) || vrc=$? ;;
    esac
    if printf '%s' "$vout" | grep -q differ; then
        die "verify FAILED -- the image does not match the card: $vout"
    elif [ "$vrc" -eq 0 ]; then
        msg "verify OK: the image matches the card exactly"
    else
        die "verify inconclusive (rc=$vrc): $vout"
    fi
fi

# --------------------------------------------------------------------------
# Hand the image back to whoever ran sudo
# --------------------------------------------------------------------------

if [ -n "${SUDO_UID:-}" ] && [ -n "${SUDO_GID:-}" ]; then
    chown "$SUDO_UID:$SUDO_GID" -- "$IMAGE" 2>/dev/null || true
fi

IMG_BYTES=$(stat -c %s -- "$IMAGE")

echo
msg "done. $IMAGE is $(human "$IMG_BYTES")."
msg "source card size, in bytes (Part 3 of the guide needs this): $DEV_BYTES"
echo
echo "Next:"
echo "  sudo ./clone_image.sh --hostname wildlifecam7 $IMAGE ~/wildlifecam7.img"
echo "  sudo ./burn_image.sh --verify ~/wildlifecam7.img /dev/sdX"
