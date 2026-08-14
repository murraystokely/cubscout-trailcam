#!/usr/bin/env bash
#
# clone_image.sh -- make a personalized wildlife-camera image from the master.
#
# This runs on a Linux laptop. It copies the master SD-card image to a new
# image file and then makes that copy unique, so the resulting .img is ready to
# burn to a card for one specific camera. It automates "Part 4 -- Make each
# clone unique" of docs/sdcard-image-instructions.md:
#
#   * set /etc/hostname to the name you pass in;
#   * rewrite the matching entry in /etc/hosts;
#   * delete the copied SSH host keys and generate fresh ones, so every camera
#     has its own SSH identity;
#   * empty /etc/machine-id (and fix the D-Bus copy) so each Pi mints its own
#     machine ID on first boot.
#
# It deliberately does NOT write to an SD card: it only ever reads the master
# and writes a new image file, so it cannot dd over the wrong disk. Burn the
# result separately (see docs/sdcard-image-instructions.md, Part 3), and expand
# the filesystem on the Pi after first boot if the card is larger than the
# master (that step only works on the running Pi).
#
# Usage:
#   sudo ./clone_image.sh --hostname wildlifecam7 MASTER.img OUTPUT.img
#
# The master may be a raw .img, or compressed as .img.gz or .img.xz. The output
# format follows its filename the same way: a plain name is written raw, while
# a .gz or .xz suffix compresses the finished image (the personalization always
# happens on the raw image first, then it is compressed). Root is required to
# mount the image and to generate the host keys, so the script re-runs itself
# under sudo if needed.

set -euo pipefail

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

PROG=${0##*/}

msg()  { printf '[clone] %s\n' "$*"; }
warn() { printf '[clone] warning: %s\n' "$*" >&2; }
die()  { printf '[clone] error: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<EOF
Make a personalized wildlife-camera image from the master.

Usage:
  sudo $PROG --hostname NAME [--force] MASTER_IMAGE OUTPUT_IMAGE

Arguments:
  MASTER_IMAGE   the master card image: .img, .img.gz, or .img.xz
  OUTPUT_IMAGE   the personalized image to create; a .gz or .xz suffix
                 compresses it, any other suffix is written raw

Options:
  -H, --hostname NAME   hostname for this clone, e.g. wildlifecam7 (required)
  -f, --force           overwrite OUTPUT_IMAGE if it already exists
  -h, --help            show this help

Example:
  sudo $PROG --hostname wildlifecam7 ~/webelos-wildlifecam.img.gz ~/wc7.img
EOF
}

# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------

HOSTNAME_NEW=""
FORCE=0
POSITIONAL=()

while [ $# -gt 0 ]; do
    case "$1" in
        -H|--hostname) HOSTNAME_NEW=${2:-}; shift 2 ;;
        -f|--force)    FORCE=1; shift ;;
        -h|--help)     usage; exit 0 ;;
        --)            shift; while [ $# -gt 0 ]; do POSITIONAL+=("$1"); shift; done ;;
        -*)            die "unknown option: $1 (try --help)" ;;
        *)             POSITIONAL+=("$1"); shift ;;
    esac
done

[ -n "$HOSTNAME_NEW" ] || { usage; exit 2; }
[ "${#POSITIONAL[@]}" -eq 2 ] || die "expected MASTER_IMAGE and OUTPUT_IMAGE (try --help)"
MASTER=${POSITIONAL[0]}
OUTPUT=${POSITIONAL[1]}

# A hostname is lowercase letters, digits and hyphens, not starting or ending
# with a hyphen. The camera naming scheme is wildlifecamN, but any valid name
# is accepted.
if ! [[ "$HOSTNAME_NEW" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]]; then
    die "invalid hostname: '$HOSTNAME_NEW' (use lowercase letters, digits, hyphens)"
fi

[ -f "$MASTER" ] || die "master image not found: $MASTER"
if [ -e "$OUTPUT" ] && [ "$FORCE" -ne 1 ]; then
    die "output already exists: $OUTPUT (use --force to overwrite)"
fi
if [ "$(readlink -f "$MASTER")" = "$(readlink -f "$OUTPUT")" ]; then
    die "master and output are the same file; refusing to clobber the master"
fi

# --------------------------------------------------------------------------
# Become root and check tools
# --------------------------------------------------------------------------

if [ "$(id -u)" -ne 0 ]; then
    msg "re-running under sudo (mounting the image and making host keys need root)"
    reexec=(--hostname "$HOSTNAME_NEW")
    [ "$FORCE" -eq 1 ] && reexec+=(--force)
    exec sudo -- "$0" "${reexec[@]}" -- "$MASTER" "$OUTPUT"
fi

for tool in losetup mount umount ssh-keygen truncate partprobe; do
    command -v "$tool" >/dev/null 2>&1 || die "required tool not installed: $tool"
done
case "$MASTER" in
    *.gz) command -v gunzip >/dev/null || die "gunzip needed for a .gz master" ;;
    *.xz) command -v xz     >/dev/null || die "xz needed for a .xz master" ;;
esac

# The output format follows its filename. Personalization happens on a raw
# image, which is then compressed to OUTPUT if the name ends in .gz or .xz.
case "$OUTPUT" in
    *.gz) OUT_COMPRESS=gz; command -v gzip >/dev/null || die "gzip needed for a .gz output" ;;
    *.xz) OUT_COMPRESS=xz; command -v xz   >/dev/null || die "xz needed for a .xz output" ;;
    *)    OUT_COMPRESS=none ;;
esac

# --------------------------------------------------------------------------
# Cleanup: always unmount and detach whatever we set up, even on error
# --------------------------------------------------------------------------

MNT=""
LOOP=""
RAW_TMP=""   # raw working image to remove afterwards (only for compressed output)

# Unmount the root filesystem and detach the loop device. Called both in the
# normal flow (before compressing) and from the cleanup trap.
detach() {
    if [ -n "$MNT" ] && mountpoint -q "$MNT"; then
        sync
        umount "$MNT"
    fi
    [ -n "$LOOP" ] && losetup -d "$LOOP" 2>/dev/null
    [ -n "$MNT" ] && [ -d "$MNT" ] && rmdir "$MNT" 2>/dev/null
    MNT=""
    LOOP=""
}

cleanup() {
    set +e
    detach
    # Drop the raw working file if we were compressing and did not finish.
    [ -n "$RAW_TMP" ] && [ -e "$RAW_TMP" ] && rm -f "$RAW_TMP"
}
trap cleanup EXIT

# --------------------------------------------------------------------------
# Copy the master to the output image
# --------------------------------------------------------------------------

# For raw output we build the image straight at OUTPUT. For compressed output
# we build it at a temporary raw file alongside OUTPUT and compress that at the
# end -- personalization has to run on the raw, mountable image.
if [ "$OUT_COMPRESS" = none ]; then
    RAW="$OUTPUT"
else
    RAW=$(mktemp -- "${OUTPUT}.rawXXXXXX")
    RAW_TMP="$RAW"
fi

msg "building raw image -> $RAW"
case "$MASTER" in
    *.gz) gunzip -c -- "$MASTER" > "$RAW" ;;
    *.xz) xz -dc -- "$MASTER" > "$RAW" ;;
    *)    cp --reflink=auto -- "$MASTER" "$RAW" ;;
esac
sync

# --------------------------------------------------------------------------
# Attach the image and mount its root filesystem (partition 2)
# --------------------------------------------------------------------------

# --show -Pf: pick a free loop device, scan its partition table, and print the
# device name. Partition 2 is the Raspberry Pi OS root; partition 1 is /boot.
LOOP=$(losetup --show -Pf -- "$RAW")
msg "attached $RAW as $LOOP"

ROOT_PART="${LOOP}p2"
# The partition node can take a moment to appear after the table is scanned.
for _ in 1 2 3 4 5; do
    [ -b "$ROOT_PART" ] && break
    partprobe "$LOOP" 2>/dev/null || true
    sleep 0.3
done
[ -b "$ROOT_PART" ] || die "root partition $ROOT_PART did not appear"

MNT=$(mktemp -d)
mount "$ROOT_PART" "$MNT"

# Guard against a surprise partition layout before we start editing files.
[ -f "$MNT/etc/os-release" ] || die "$ROOT_PART is not a Linux root filesystem"

# --------------------------------------------------------------------------
# Personalize (docs/sdcard-image-instructions.md, Part 4)
# --------------------------------------------------------------------------

msg "hostname -> $HOSTNAME_NEW"
old_hostname=""
[ -f "$MNT/etc/hostname" ] && old_hostname=$(tr -d '[:space:]' < "$MNT/etc/hostname")
printf '%s\n' "$HOSTNAME_NEW" > "$MNT/etc/hostname"

if [ -f "$MNT/etc/hosts" ]; then
    if [ -n "$old_hostname" ] && grep -qw "$old_hostname" "$MNT/etc/hosts"; then
        # Replace the master's own name wherever it appears (the 127.0.1.1 line).
        sed -i "s/\b${old_hostname}\b/${HOSTNAME_NEW}/g" "$MNT/etc/hosts"
    else
        # Fall back to the wildlifecamN pattern if the old name is not found.
        sed -i "s/wildlifecam[0-9]*/${HOSTNAME_NEW}/g" "$MNT/etc/hosts"
    fi
    if ! grep -q "127.0.1.1" "$MNT/etc/hosts"; then
        printf '127.0.1.1\t%s\n' "$HOSTNAME_NEW" >> "$MNT/etc/hosts"
    fi
else
    warn "no /etc/hosts in the image; skipping"
fi

msg "regenerating SSH host keys"
# Delete the master's keys so every clone gets a unique SSH identity, then let
# ssh-keygen -A make a fresh set. With -A the -f value is a *prefix*, so the
# keys land in \$MNT/etc/ssh, not the laptop's own /etc/ssh.
rm -f "$MNT"/etc/ssh/ssh_host_*
ssh-keygen -A -f "$MNT" >/dev/null

msg "resetting machine-id"
# An empty /etc/machine-id is systemd's signal to generate a fresh one on the
# first boot -- do not delete it or put a value in by hand.
truncate -s 0 "$MNT/etc/machine-id"
# D-Bus keeps its own copy; on Raspberry Pi OS it is normally a symlink to
# /etc/machine-id (already handled above). Replace it with that symlink if the
# image happens to carry a real file instead.
dbus_id="$MNT/var/lib/dbus/machine-id"
if [ -e "$dbus_id" ] && [ ! -L "$dbus_id" ]; then
    rm -f "$dbus_id"
    ln -s /etc/machine-id "$dbus_id"
fi

# --------------------------------------------------------------------------
# Verify and finish
# --------------------------------------------------------------------------

sync
msg "verification:"
printf '    /etc/hostname   : %s\n' "$(cat "$MNT/etc/hostname")"
printf '    /etc/hosts      : %s\n' "$(grep -E '127\.0\.1\.1' "$MNT/etc/hosts" 2>/dev/null || echo '(no 127.0.1.1 line)')"
printf '    host keys       : %s new key(s)\n' "$(ls "$MNT"/etc/ssh/ssh_host_*_key 2>/dev/null | wc -l)"
printf '    machine-id size : %s byte(s) (should be 0)\n' "$(stat -c %s "$MNT/etc/machine-id")"

# Unmount and detach before touching the raw image again.
detach

# Compress the finished image if the output name asked for it.
if [ "$OUT_COMPRESS" != none ]; then
    msg "compressing -> $OUTPUT"
    case "$OUT_COMPRESS" in
        gz) gzip -c -- "$RAW" > "$OUTPUT" ;;
        xz) xz -zc -T0 -- "$RAW" > "$OUTPUT" ;;
    esac
    sync
    rm -f "$RAW"
    RAW_TMP=""
fi

msg "done: $OUTPUT is ready to burn for $HOSTNAME_NEW"
msg "burn it with the steps in docs/sdcard-image-instructions.md (Part 3)"
