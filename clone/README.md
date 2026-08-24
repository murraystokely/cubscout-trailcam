# Cloning a personalized camera image

Four helpers, all for a **Linux laptop**, covering the round trip:

- [`capture_image.sh`](capture_image.sh) --- read the master camera's SD card
  into a master image file.
- [`shrink_image.sh`](shrink_image.sh) --- shrink an image so it fits a smaller
  card (never touches a card itself).
- [`clone_image.sh`](clone_image.sh) --- turn the master image into a
  personalized image file for one camera (never touches a card).
- [`burn_image.sh`](burn_image.sh) --- write an image to an SD card, with
  safety checks so it only ever writes to a removable card.

```text
master card      --capture_image.sh-->  master.img
master.img       --shrink_image.sh-->   master.img   (optional, smaller)
master.img       --clone_image.sh--->   wildlifecam7.img
wildlifecam7.img --burn_image.sh---->   a new card
```

## Capturing the master image

[`capture_image.sh`](capture_image.sh) is the first step: it reads the whole
SD card out of the camera you built by hand and into an image file. It
automates *Part 2, sections 11-13* of
[`../docs/sdcard-image-instructions.md`](../docs/sdcard-image-instructions.md).

```bash
lsblk -o NAME,SIZE,TYPE,RM,TRAN,MODEL         # which one is the card?
sudo ./capture_image.sh --verify /dev/sdc ~/webelos-wildlifecam.img.gz
```

Reading cannot damage the card, but it is still easy to aim at the wrong
device and spend twenty minutes copying the laptop's own disk into your home
directory. So the same checks as `burn_image.sh` apply before anything is
read: whole disk not a partition, not the disk backing the running system,
and it has to look removable. It also refuses to overwrite an existing image
without `--force`, checks there is room for the output, and unmounts the card
first --- reading a mounted filesystem captures it half-written and the clone
may not boot.

The output format follows the filename, exactly like `clone_image.sh`: end it
in `.gz` or `.xz` to compress, anything else for raw. Compressing is well
worth it **if you zeroed the free space first** (section 9 of the guide) ---
that is what turns a 32 GB card into well under 2 GB.

When it finishes it prints the source card size in bytes, which is the number
Part 3 of the guide asks you to write down, and hands the finished image back
to you rather than leaving it owned by root.

### Options

| Option | What it does |
| --- | --- |
| `--verify` | Read the card back and compare it to the image. |
| `-f`, `--force` | Overwrite the output image if it already exists. |
| `--allow-internal` | Permit a non-removable source (the system-disk and partition checks still apply). |
| `-h`, `--help` | Show usage. |

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

## Shrinking an image to fit a smaller card

Two cards sold as "32 GB" are rarely the same number of bytes, so an image read
off one may miss fitting another by a hair.
[`shrink_image.sh`](shrink_image.sh) shrinks the image's last partition --- the
Linux root filesystem --- until the whole file fits the card you actually have,
and leaves the FAT boot partition alone.

```bash
sudo ./shrink_image.sh --dry-run ~/wc10.img /dev/sda   # what would it do?
sudo ./shrink_image.sh ~/wc10.img /dev/sda             # do it
```

Always run `--dry-run` first. It prints the whole plan --- current size, target
size, how far the filesystem can go --- and changes nothing.

It only ever opens an image **file**; it refuses a block device outright, so
unlike `burn_image.sh` there is no way for it to write over a disk. The risk
here is a different one: a botched resize gives you a quietly corrupt image
that you then copy to ten cameras. So it shrinks in the careful order --- fsck,
ask `resize2fs` how small the data could possibly go, shrink the filesystem,
shrink the partition to match **what the filesystem actually became** rather
than what was asked for, truncate the file --- and then proves the result by
running `e2fsck` again, mounting it read-only, and checking `/etc/hostname`
still exists before it will call the job done.

Often no resize is needed at all. An image captured from a card usually has a
little unused space after the last partition, and trimming that is enough.
The script notices and takes the safe path.

### Making a much smaller master

`--minimal` shrinks as far as the data allows, ignoring the target:

```bash
sudo ./shrink_image.sh --minimal ~/webelos-wildlifecam.img
```

A camera master is mostly empty --- typically under 10 GB of a 32 GB card ---
so this can cut the image by two thirds. Smaller images clone faster, burn
faster and compress better, and cost nothing at the far end, because the Pi
grows the filesystem back on first boot:

```bash
sudo raspi-config --expand-rootfs
```

### Options

| Option | What it does |
| --- | --- |
| `--size SIZE` | Fit this many bytes instead of reading a device (accepts `K`/`M`/`G`). |
| `--minimal` | Shrink as far as the filesystem allows, ignoring the target. |
| `--margin SIZE` | Spare room so the image also fits a slightly smaller card (default 16MiB). |
| `--output FILE` | Work on a copy and leave the original untouched (needs the space). |
| `-n`, `--dry-run` | Print the plan, change nothing. |
| `-y`, `--yes` | Skip the typed confirmation. |
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
