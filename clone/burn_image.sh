#!/usr/bin/env bash
#
# burn_image.sh -- write a wildlife-camera image to an SD card, safely.
#
# This runs on a Linux laptop. Writing an image with dd is unforgiving: aim it
# at the wrong device and it silently destroys the laptop's own disk. This
# script therefore refuses to write to anything that is not clearly a
# removable card, and refuses outright to touch a disk that backs the running
# system, before it will copy a single byte.
#
# Safety checks, all before any write:
#   * the target must be a block device that exists;
#   * it must be a WHOLE disk, not a partition (e.g. /dev/sdc, not /dev/sdc1);
#   * it must NOT back the root, /boot or swap of the running system;
#   * it must look REMOVABLE (kernel "removable" flag, hotplug, or a USB/MMC
#     transport) -- an internal NVMe/SATA disk is rejected;
#   * a raw image must fit on the card;
#   * you confirm by typing the device name (unless --yes).
#
# The image may be a raw .img or compressed as .img.gz / .img.xz, matching
# clone_image.sh. Root is required, so the script re-runs itself under sudo.
#
# Usage:
#   sudo ./burn_image.sh [--verify] [--yes] IMAGE DEVICE
#
# Example:
#   sudo ./burn_image.sh --verify ~/wildlifecam7.img.gz /dev/sdc

set -euo pipefail

PROG=${0##*/}

msg()  { printf '[burn] %s\n' "$*"; }
warn() { printf '[burn] warning: %s\n' "$*" >&2; }
die()  { printf '[burn] error: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<EOF
Write a wildlife-camera image to a removable SD card, with safety checks.

Usage:
  sudo $PROG [options] IMAGE DEVICE

Arguments:
  IMAGE    image to write: .img, .img.gz, or .img.xz
  DEVICE   whole SD-card device, e.g. /dev/sdc (never a partition)

Options:
  --verify           read the card back and compare it to the image
  -y, --yes          do not ask for typed confirmation (checks still apply)
  --allow-internal   permit a non-removable target (DANGEROUS; the system-disk
                     and partition checks still apply)
  -h, --help         show this help

Find the device name first with:  lsblk -o NAME,SIZE,TYPE,RM,TRAN,MODEL
EOF
}

# --------------------------------------------------------------------------
# Parse arguments
# --------------------------------------------------------------------------

VERIFY=0
ASSUME_YES=0
ALLOW_INTERNAL=0
POSITIONAL=()

while [ $# -gt 0 ]; do
    case "$1" in
        --verify)         VERIFY=1; shift ;;
        -y|--yes)         ASSUME_YES=1; shift ;;
        --allow-internal) ALLOW_INTERNAL=1; shift ;;
        -h|--help)        usage; exit 0 ;;
        --)               shift; while [ $# -gt 0 ]; do POSITIONAL+=("$1"); shift; done ;;
        -*)               die "unknown option: $1 (try --help)" ;;
        *)                POSITIONAL+=("$1"); shift ;;
    esac
done

[ "${#POSITIONAL[@]}" -eq 2 ] || { usage; exit 2; }
IMAGE=${POSITIONAL[0]}
DEVICE=${POSITIONAL[1]}

[ -f "$IMAGE" ] || die "image not found: $IMAGE"
[ -e "$DEVICE" ] || die "device not found: $DEVICE"

# --------------------------------------------------------------------------
# Become root and check tools
# --------------------------------------------------------------------------

if [ "$(id -u)" -ne 0 ]; then
    msg "re-running under sudo (writing to a disk needs root)"
    reexec=()
    [ "$VERIFY" -eq 1 ]         && reexec+=(--verify)
    [ "$ASSUME_YES" -eq 1 ]     && reexec+=(--yes)
    [ "$ALLOW_INTERNAL" -eq 1 ] && reexec+=(--allow-internal)
    exec sudo -- "$0" "${reexec[@]}" -- "$IMAGE" "$DEVICE"
fi

for tool in lsblk findmnt blockdev dd umount partprobe; do
    command -v "$tool" >/dev/null 2>&1 || die "required tool not installed: $tool"
done
case "$IMAGE" in
    *.gz) command -v gunzip >/dev/null || die "gunzip needed for a .gz image" ;;
    *.xz) command -v xz     >/dev/null || die "xz needed for a .xz image" ;;
esac
[ "$VERIFY" -eq 0 ] || command -v cmp >/dev/null || die "cmp needed for --verify"

# Resolve the device to its real path (follows /dev/disk/by-id/... symlinks).
DEV=$(readlink -f -- "$DEVICE")
[ -b "$DEV" ] || die "$DEVICE ($DEV) is not a block device"
KNAME=$(lsblk -dno KNAME -- "$DEV" 2>/dev/null | head -1)
[ -n "$KNAME" ] || die "could not identify $DEV"

# --------------------------------------------------------------------------
# Safety gate 1: must be a whole disk, not a partition
# --------------------------------------------------------------------------

DTYPE=$(lsblk -dno TYPE -- "$DEV" 2>/dev/null | head -1)
if [ "$DTYPE" != disk ]; then
    die "$DEV is a '$DTYPE', not a whole disk. Pass the whole card (e.g. /dev/sdc), not a partition."
fi

# --------------------------------------------------------------------------
# Safety gate 2: must not back the running system
# --------------------------------------------------------------------------

# The whole-disk kname behind a device path, e.g. /dev/sda2 -> sda.
disk_of() {
    local src="$1" pk
    pk=$(lsblk -no PKNAME -- "$src" 2>/dev/null | head -1)
    if [ -n "$pk" ]; then echo "$pk"; else lsblk -dno KNAME -- "$src" 2>/dev/null | head -1; fi
}

# Direct check: does this device (or one of its partitions) back a critical
# mount? Catches /dev/sdc AND /dev/sdc2 / mmcblk0p2 / nvme0n1p2 forms.
for mp in / /boot /boot/firmware /home; do
    src=$(findmnt -nro SOURCE --target "$mp" 2>/dev/null | head -1) || true
    [ -n "$src" ] || continue
    case "$src" in
        "$DEV"|"$DEV"[0-9]*|"$DEV"p[0-9]*)
            die "$DEV backs $mp ($src) on this laptop. Refusing." ;;
    esac
done

# Broader check by whole-disk name: root, /boot, /boot/firmware, /home, swap.
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
    fpath=$(findmnt -nro SOURCE --target "$IMAGE" 2>/dev/null | head -1) || true
    case "$fpath" in /dev/*) disk_of "$fpath" ;; esac   # don't wipe the image's own disk
)
if printf '%s\n' "$PROTECTED" | grep -qx -- "$KNAME"; then
    die "$DEV ($KNAME) holds the system, swap, or the image itself. Refusing."
fi

# --------------------------------------------------------------------------
# Safety gate 3: must look removable
# --------------------------------------------------------------------------

RM=$(lsblk -dno RM -- "$DEV" 2>/dev/null | head -1)
HOTPLUG=$(lsblk -dno HOTPLUG -- "$DEV" 2>/dev/null | head -1)
TRAN=$(lsblk -dno TRAN -- "$DEV" 2>/dev/null | head -1)
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
# Safety gate 4: a raw image must fit on the card
# --------------------------------------------------------------------------

DEV_BYTES=$(blockdev --getsize64 "$DEV")
case "$IMAGE" in
    *.gz|*.xz) : ;;  # compressed size unknown up front; dd will stop if it overflows
    *)
        IMG_BYTES=$(stat -c %s -- "$IMAGE")
        if [ "$IMG_BYTES" -gt "$DEV_BYTES" ]; then
            die "image is $IMG_BYTES bytes but $DEV is only $DEV_BYTES bytes. Use a larger card."
        fi ;;
esac

# --------------------------------------------------------------------------
# Show what we are about to do and confirm
# --------------------------------------------------------------------------

echo
echo "About to ERASE and overwrite this device:"
lsblk -o NAME,SIZE,TYPE,RM,HOTPLUG,TRAN,MOUNTPOINTS,MODEL -- "$DEV" || true
echo
echo "  image : $IMAGE"
echo "  device: $DEV  (removable=$REMOVABLE, $(numfmt --to=iec --suffix=B "$DEV_BYTES" 2>/dev/null || echo "$DEV_BYTES bytes"))"
echo

if [ "$DEV_BYTES" -gt $((256 * 1024 * 1024 * 1024)) ]; then
    warn "this device is larger than a typical SD card -- double-check it is really the card."
fi

if [ "$ASSUME_YES" -ne 1 ]; then
    printf 'Type the device name to confirm you want to ERASE it (%s): ' "$DEV"
    read -r answer
    [ "$answer" = "$DEV" ] || die "confirmation did not match ('$answer'); aborting."
fi

# --------------------------------------------------------------------------
# Unmount anything currently mounted from the target
# --------------------------------------------------------------------------

for part in $(lsblk -lno NAME -- "$DEV" | tail -n +2); do
    if findmnt -rno TARGET "/dev/$part" >/dev/null 2>&1; then
        msg "unmounting /dev/$part"
        umount "/dev/$part" 2>/dev/null || true
    fi
done
# Bail out if anything is still mounted -- writing under a live mount corrupts.
STILL=""
for part in $(lsblk -lno NAME -- "$DEV" | tail -n +2); do
    if findmnt -rno TARGET "/dev/$part" >/dev/null 2>&1; then
        STILL="$STILL /dev/$part"
    fi
done
[ -z "$STILL" ] || die "still mounted:$STILL -- unmount it and retry."

# --------------------------------------------------------------------------
# Write
# --------------------------------------------------------------------------

msg "writing $IMAGE -> $DEV (this takes a while; do not remove the card)"
case "$IMAGE" in
    *.gz) gunzip -c -- "$IMAGE" | dd of="$DEV" bs=4M conv=fsync iflag=fullblock status=progress ;;
    *.xz) xz -dc -- "$IMAGE"    | dd of="$DEV" bs=4M conv=fsync iflag=fullblock status=progress ;;
    *)    dd if="$IMAGE" of="$DEV" bs=4M conv=fsync status=progress ;;
esac
sync
blockdev --flushbufs "$DEV" 2>/dev/null || true

# --------------------------------------------------------------------------
# Optional verification
# --------------------------------------------------------------------------

if [ "$VERIFY" -eq 1 ]; then
    msg "verifying the card against the image..."
    sync
    blockdev --flushbufs "$DEV" 2>/dev/null || true
    vout=""
    vrc=0
    case "$IMAGE" in
        *.gz) vout=$(gunzip -c -- "$IMAGE" | cmp - "$DEV" 2>&1) || vrc=$? ;;
        *.xz) vout=$(xz -dc -- "$IMAGE"    | cmp - "$DEV" 2>&1) || vrc=$? ;;
        *)    vout=$(cmp -- "$IMAGE" "$DEV" 2>&1) || vrc=$? ;;
    esac
    if printf '%s' "$vout" | grep -q differ; then
        die "verify FAILED -- the card does not match the image: $vout"
    elif [ "$vrc" -eq 0 ] || printf '%s' "$vout" | grep -q 'EOF on'; then
        # "EOF on -" just means the image ended; the card is larger. All the
        # image's bytes matched, which is what we want.
        msg "verify OK: every image byte matches the card"
    else
        die "verify inconclusive (rc=$vrc): $vout"
    fi
fi

# Re-read the new partition table so the card is ready to eject/mount.
partprobe "$DEV" 2>/dev/null || true

msg "done. It is safe to remove $DEV now."
