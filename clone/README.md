# Cloning a personalized camera image

Two helpers, both for a **Linux laptop**:

- [`clone_image.sh`](clone_image.sh) --- turn the master image into a
  personalized image file for one camera (never touches a card).
- [`burn_image.sh`](burn_image.sh) --- write an image to an SD card, with
  safety checks so it only ever writes to a removable card.

[`clone_image.sh`](clone_image.sh) takes the master wildlife-camera SD-card
image and writes a new image file that is made unique for one camera, ready to
burn.

It automates *Part 4 --- Make each clone unique* of
[`../docs/sdcard-image-instructions.md`](../docs/sdcard-image-instructions.md):

- sets `/etc/hostname` to the name you give it;
- rewrites the matching entry in `/etc/hosts`;
- deletes the master's SSH host keys and generates a fresh set, so every
  camera has its own SSH identity;
- empties `/etc/machine-id` (and fixes the D-Bus copy) so each Pi mints its
  own machine ID on first boot.

By design it **never writes to an SD card** --- it only reads the master and
writes a new image file, so it cannot `dd` over the wrong disk. Burn the
result with [`burn_image.sh`](burn_image.sh) (below) or the manual steps in
the guide.

## Making a personalized image

```bash
sudo ./clone_image.sh --hostname wildlifecam7 ~/webelos-wildlifecam.img ~/wc7.img
```

The master may be a raw `.img` or compressed as `.img.gz` / `.img.xz`. The
output format follows its own filename in the same way: end the name in `.gz`
or `.xz` to compress the finished image, or use any other name to write it
raw. (Personalization always runs on the raw image first; a compressed output
is produced at the end.) Root is needed to loop-mount the image and to
generate the host keys, so the script re-runs itself under `sudo` if you do
not start it that way.

```bash
# raw output
sudo ./clone_image.sh --hostname wildlifecam7 ~/webelos-wildlifecam.img.gz ~/wc7.img
# gzip-compressed output
sudo ./clone_image.sh --hostname wildlifecam7 ~/webelos-wildlifecam.img.gz ~/wc7.img.gz
# xz-compressed output
sudo ./clone_image.sh --hostname wildlifecam7 ~/webelos-wildlifecam.img.gz ~/wc7.img.xz
```

To make one image per camera:

```bash
for n in 3 4 5; do
    sudo ./clone_image.sh --hostname "wildlifecam$n" \
        ~/webelos-wildlifecam.img ~/"wildlifecam$n.img"
done
```

### Options

| Option | What it does |
| --- | --- |
| `-H`, `--hostname NAME` | Hostname for this clone, e.g. `wildlifecam7` (required). |
| `-f`, `--force` | Overwrite the output image if it already exists. |
| `-h`, `--help` | Show usage. |

## Burning to a card

[`burn_image.sh`](burn_image.sh) writes an image (from `clone_image.sh` or the
master itself) to an SD card. `dd` to the wrong device destroys the laptop's
own disk, so the script checks the target *before* writing anything and refuses
unless it is a **whole, removable disk that is not part of the running
system**:

- the target must be a whole disk, not a partition (`/dev/sdc`, not `/dev/sdc1`);
- it must not back `/`, `/boot`, `/home` or swap;
- it must look removable (kernel removable flag, hotplug, or a USB/MMC bus) ---
  an internal NVMe/SATA disk is rejected;
- a raw image must fit on the card;
- you confirm by typing the device name (unless `--yes`).

Find the device name first, then burn:

```bash
lsblk -o NAME,SIZE,TYPE,RM,TRAN,MODEL         # which one is the card?
sudo ./burn_image.sh --verify ~/wildlifecam7.img.gz /dev/sdc
```

Like the other script, the image may be raw `.img` or compressed `.img.gz` /
`.img.xz`. Root is required, so it re-runs itself under `sudo`. `--verify`
reads the card back and compares it to the image, catching a card that was
pulled early or is failing.

### Options

| Option | What it does |
| --- | --- |
| `--verify` | Read the card back and compare it to the image after writing. |
| `-y`, `--yes` | Skip the typed confirmation (all safety checks still apply). |
| `--allow-internal` | Permit a non-removable target (dangerous; the system-disk and partition checks still apply). |
| `-h`, `--help` | Show usage. |

## After cloning

1. Burn each image to a card with `burn_image.sh` above (or the manual `dd`
   steps in
   [`../docs/sdcard-image-instructions.md`](../docs/sdcard-image-instructions.md),
   Part 3).
2. On a card larger than the master, expand the filesystem on the Pi after its
   first boot (`sudo raspi-config --expand-rootfs`); that step only works on
   the running Pi, not on the image.
3. Verify on first boot (hostname, unique machine ID, camera, nginx, the
   wildlife-camera service) as described in Part 5 of the same guide.
