# Cloning a personalized camera image

[`clone_image.sh`](clone_image.sh) runs on a **Linux laptop**. It takes the
master wildlife-camera SD-card image and writes a new image file that is made
unique for one camera, ready to burn.

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
result separately.

## Usage

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

## After cloning

1. Burn each image to a card --- see
   [`../docs/sdcard-image-instructions.md`](../docs/sdcard-image-instructions.md),
   Part 3.
2. On a card larger than the master, expand the filesystem on the Pi after its
   first boot (`sudo raspi-config --expand-rootfs`); that step only works on
   the running Pi, not on the image.
3. Verify on first boot (hostname, unique machine ID, camera, nginx, the
   wildlife-camera service) as described in Part 5 of the same guide.

## Options

| Option | What it does |
| --- | --- |
| `-H`, `--hostname NAME` | Hostname for this clone, e.g. `wildlifecam7` (required). |
| `-f`, `--force` | Overwrite the output image if it already exists. |
| `-h`, `--help` | Show usage. |
