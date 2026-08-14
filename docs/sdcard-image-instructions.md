
# Webelos Wildlife Camera --- SD Card Configuration and Cloning Guide

This is the consolidated procedure for configuring the master Raspberry Pi
wildlife-camera SD card, backing it up on a laptop, burning clones, and
making each clone unique.

The cloning and personalization steps assume a **Linux laptop**. Burning a
card works on macOS too (see [Burning from
macOS](#burning-from-macos)), but the personalization steps mount the Pi's
ext4 root filesystem, which macOS cannot do without extra software. If the
only laptop available is a Mac, burn the card there and then do the
hostname and SSH host key changes on the Pi itself after its first boot.

---

# Part 1 --- Configure the master Raspberry Pi

## 1. Hostname

```bash
sudo hostnamectl set-hostname wildlifecam1
hostname
```

Each clone gets its own hostname later.

## 2. Wi-Fi

Raspberry Pi OS uses NetworkManager:

```bash
nmcli connection show
nmcli device wifi list
```

Add a network:

```bash
sudo nmcli device wifi connect "SSID" password "PASSWORD"
```

Set priority if desired:

```bash
sudo nmcli connection modify "CONNECTION_NAME" connection.autoconnect-priority 100
```

Higher priorities are preferred. Add all home/campsite fallback networks to
the master before cloning.

> **Wi-Fi passwords are copied into every clone.** NetworkManager stores
> them in `/etc/NetworkManager/system-connections/`, so any network saved on
> the master is readable by anyone holding a cloned card. If the cards go
> home with different families, put only the *campsite* network on the
> master and let each family add their own home network afterwards:
>
> ```bash
> sudo nmcli device wifi connect "THEIR_SSID" password "THEIR_PASSWORD"
> ```

## 3. SSH

Make sure SSH is enabled:

```bash
sudo systemctl enable --now ssh
sudo systemctl status ssh
```

Laptop public keys belong in:

```text
/home/webelos/.ssh/authorized_keys
```

Permissions:

```bash
mkdir -p /home/webelos/.ssh
chmod 700 /home/webelos/.ssh
chmod 600 /home/webelos/.ssh/authorized_keys
chown -R webelos:webelos /home/webelos/.ssh
```

## 4. Camera

Verify Camera Module 3 detection:

```bash
rpicam-hello --list-cameras
```

Incorrect ribbon orientation can result in `No cameras available` with
little useful `dmesg` output.

## 5. Wildlife-camera software

The project progresses through:

1. `step1_firstphoto.py` --- one photograph.
2. `step2_timestamp_photo.py` --- timestamped photo and directory creation.
3. `step3_preview.py` --- preview experiment.
4. `step4_repeat_photos.py` --- repeated photographs.
5. `step5_detect_motion.py` --- frame comparison and motion detection.
6. `final_motion_capture.py` --- final capture loop, including the 95%
   filesystem-full safety check.

Photos are ultimately written beneath:

```text
/var/www/html/photos/
```

with per-day directories.

## 6. nginx

Install and enable:

```bash
sudo apt update
sudo apt install nginx
sudo systemctl enable --now nginx
```

Web root:

```text
/var/www/html/
```

Main page:

```text
/var/www/html/index.html
```

Photos:

```text
/var/www/html/photos/
```

For directory listings, add this inside the existing `server { ... }`
section:

```nginx
location /photos/ {
    autoindex on;
    autoindex_exact_size off;
    autoindex_localtime on;
}
```

Test/reload:

```bash
sudo nginx -t
sudo systemctl reload nginx
curl http://127.0.0.1/
```

Optionally, drop a marker file so the laptop sync script can recognize a
camera by IP address even when mDNS names are unavailable:

```bash
echo "wildlife camera" | sudo tee /var/www/html/wildlifecam.txt
```

See [`../sync/README.md`](../sync/README.md) for how that is used.

## 7. Desktop background for the Scout image

Copy the custom background image, `cubscoutsbg.jpg`, onto the Raspberry Pi
before creating the master SD-card image so every clone inherits it.

A convenient system-wide location is:

```text
/usr/share/backgrounds/cubscoutsbg.jpg
```

For example, if `cubscoutsbg.jpg` is currently in the `webelos` user's home
directory:

```bash
sudo cp /home/webelos/cubscoutsbg.jpg /usr/share/backgrounds/cubscoutsbg.jpg
sudo chmod 644 /usr/share/backgrounds/cubscoutsbg.jpg
```

Verify:

```bash
ls -lh /usr/share/backgrounds/cubscoutsbg.jpg
```

Then, from the Raspberry Pi desktop UI:

1. Right-click an empty area of the desktop.
2. Open **Desktop Preferences**.
3. Select the **Appearance** or **Picture** setting for the desktop
   background.
4. Browse to `/usr/share/backgrounds/`.
5. Select `cubscoutsbg.jpg`.
6. Apply the setting and confirm that the Cub Scouts image is now the
   desktop background.

Do this on the **master image before cloning**. The desktop configuration
and `/usr/share/backgrounds/cubscoutsbg.jpg` will then be present on every
cloned SD card.

If a clone is primarily headless, this setting does not affect the camera,
nginx, SSH, or motion-capture services; it simply provides the customized
Scout desktop whenever a monitor is connected.

## 8. Automatic motion-capture service

The final script runs as a systemd service. Check it with:

```bash
sudo systemctl status wildlife-camera --no-pager
journalctl -u wildlife-camera -f
```

Python buffers its output when it is not writing to a terminal, which makes
`journalctl -f` look frozen for minutes at a time. Run it unbuffered so
status messages appear promptly. In
`/etc/systemd/system/wildlife-camera.service`, either use `python3 -u`:

```ini
ExecStart=/usr/bin/python3 -u /home/webelos/final_motion_capture.py
```

or set the environment variable:

```ini
Environment=PYTHONUNBUFFERED=1
```

After editing the unit:

```bash
sudo systemctl daemon-reload
sudo systemctl restart wildlife-camera
```

The full service file is in the [main README](../README.md).

---

# Part 2 --- Create the master image

## 9. Tidy up before imaging

Anything left on the master is copied to every clone. On the Pi:

```bash
rm -rf /var/www/html/photos/*        # test shots from building the master
rm -f ~/.bash_history
```

The image file is the same size as the whole card whether the card is full
or empty, but *empty* space compresses far better if it is zeroed first.
This step is optional. It briefly fills the card completely, so stop the
camera service first so it is not trying to save photographs at the same
time:

```bash
sudo systemctl stop wildlife-camera
sudo dd if=/dev/zero of=/zero.fill bs=4M status=progress || true
sudo rm -f /zero.fill
sync
```

`dd` deliberately runs until the card is full and reports "No space left on
device" --- that is the expected way for it to end, and removing
`/zero.fill` immediately afterwards gives the space straight back. This can
turn a 30 GB compressed image into well under 2 GB. The Pi is powered off
in the next step, so there is no need to restart the service.

## 10. Shut down cleanly

```bash
sudo poweroff
```

Remove the microSD after shutdown and insert it into the Linux laptop.

## 11. Identify the card

```bash
lsblk -o NAME,SIZE,TYPE,MOUNTPOINTS,MODEL
```

The device may be `/dev/sdb`, `/dev/sdc`, etc. **Check every time. Never
assume the device name.** Confirm the size and model match the SD card and
not the laptop's own disk --- `dd` to the wrong device destroys the laptop.

Example:

```text
sdc
├─sdc1
└─sdc2
```

means the whole card is `/dev/sdc`.

## 12. Unmount it

For the example `/dev/sdc`:

```bash
sudo umount /dev/sdc1
sudo umount /dev/sdc2
```

## 13. Copy the entire card

```bash
sudo dd if=/dev/sdc of=~/webelos-wildlifecam.img bs=4M status=progress conv=fsync
sync
```

Use the whole device (`/dev/sdc`), **not** `/dev/sdc1` or `/dev/sdc2`.

Optionally compress:

```bash
gzip ~/webelos-wildlifecam.img
```

giving:

```text
~/webelos-wildlifecam.img.gz
```

Record how big the source card is, in bytes --- the next section needs it:

```bash
lsblk -b -d -o NAME,SIZE /dev/sdc
```

---

# Part 3 --- Burn a cloned card

## 14. Check the destination card is big enough

> **Cards of the same advertised size are not always the same number of
> bytes.** Two different 32 GB cards can differ by tens of megabytes, and
> `dd` to a card even one byte too small produces a corrupt clone that may
> still appear to boot. Compare the byte counts before burning:
>
> ```bash
> lsblk -b -d -o NAME,SIZE /dev/sdc
> ```
>
> The destination must be **greater than or equal to** the source card. If
> it is smaller, use a larger card or shrink the image first with a tool
> such as [PiShrink](https://github.com/Drewsif/PiShrink).

A *larger* destination card is fine, but the extra space is not used until
the filesystem is expanded --- see [step 20](#20-expand-the-filesystem-on-a-larger-card).

## 15. Identify and unmount the destination

Insert the new card:

```bash
lsblk -o NAME,SIZE,TYPE,MOUNTPOINTS,MODEL
```

If it is `/dev/sdc`:

```bash
sudo umount /dev/sdc1
sudo umount /dev/sdc2
```

Again, verify the device before using `dd`.

## 16. Burn it

Uncompressed image:

```bash
sudo dd if=~/webelos-wildlifecam.img of=/dev/sdc bs=4M status=progress conv=fsync
sync
```

Compressed image:

```bash
gunzip -c ~/webelos-wildlifecam.img.gz | \
    sudo dd of=/dev/sdc bs=4M status=progress conv=fsync
sync
```

Wait for `dd` **and** `sync` to finish. Pulling the card early is the most
common cause of a clone that will not boot.

## Burning from macOS

The commands are nearly the same; only the device names differ. Use
`diskutil` instead of `lsblk`, and write to the *raw* device (`rdisk`, not
`disk`) --- it is many times faster:

```bash
diskutil list                        # find the card, e.g. /dev/disk4
diskutil unmountDisk /dev/disk4
sudo dd if=~/webelos-wildlifecam.img of=/dev/rdisk4 bs=4m status=progress conv=fsync
sync
diskutil eject /dev/disk4
```

Reading a master image works the same way with `if=/dev/rdisk4`. The
personalization steps below need Linux, because macOS cannot mount the Pi's
ext4 root filesystem.

---

# Part 4 --- Make each clone unique

Do this on the laptop, with the freshly burned card still inserted.

## 17. Mount the clone

```bash
sudo partprobe /dev/sdc
sudo mount /dev/sdc2 /mnt
```

Partition 2 is the Raspberry Pi OS root filesystem; partition 1 is the boot
partition. We use `/mnt` for the cloned root filesystem.

## 18. Change hostname

Example for `wildlifecam7`:

```bash
echo wildlifecam7 | sudo tee /mnt/etc/hostname
```

Update `/etc/hosts`:

```bash
sudo sed -i 's/wildlifecam[0-9]*/wildlifecam7/g' /mnt/etc/hosts
```

Verify:

```bash
cat /mnt/etc/hostname
grep wildlifecam /mnt/etc/hosts
```

If the old hostname does not follow the `wildlifecamN` pattern, edit
`/mnt/etc/hosts` directly.

## 19. Regenerate SSH host keys

Every clone must have its own SSH host identity. Without this, every camera
presents the same key, and the laptop cannot tell them apart.

Remove the copied host keys:

```bash
sudo rm -f /mnt/etc/ssh/ssh_host_*
```

Generate all standard host keys in one operation:

```bash
sudo ssh-keygen -A -f /mnt
```

This looks wrong but is correct: with `-A`, the `-f` argument is used as a
*prefix* to the default path, so the keys land in `/mnt/etc/ssh/` rather
than the laptop's own `/etc/ssh/`. `-A` only generates keys that do not
already exist, which is why the old ones are deleted first.

Verify:

```bash
ls -l /mnt/etc/ssh/ssh_host_*
```

Do **not** remove `/home/webelos/.ssh/authorized_keys`; those are the
laptop's login authorization keys and should remain on every clone.

### `/etc/machine-id`

We did not need to remove `/etc/machine-id` for the earlier working clones.
The important uniqueness changes for this project are the hostname and SSH
host keys. Raspberry Pi OS identifies itself to DHCP by its (unique) MAC
address rather than by machine-id, so duplicates cause no address
conflicts in practice.

If two cameras ever do end up fighting over one DHCP lease, this is the
first thing to rule out:

```bash
sudo rm -f /mnt/etc/machine-id
sudo truncate -s 0 /mnt/var/lib/dbus/machine-id
```

Both are regenerated on the next boot. Avoid adding extra
clone-preparation steps unless a concrete need arises.

## 20. Expand the filesystem on a larger card

A cloned card keeps the *source* card's partition sizes, so a 32 GB image
burned to a 64 GB card leaves half the card unused. Raspberry Pi OS only
auto-expands on the first boot of a freshly flashed official image, not on
a clone.

If the destination card is bigger, expand it once on the Pi after first
boot:

```bash
sudo raspi-config --expand-rootfs
sudo reboot
```

Confirm afterwards:

```bash
df -h /
```

More free space directly means more photographs before
`final_motion_capture.py` hits its 95% safety limit.

## 21. Finish

```bash
sync
sudo umount /mnt
sudo eject /dev/sdc
```

---

# Part 5 --- First boot verification

Insert the card in its Pi, connect the camera, and boot.

Find the Pi in the DHCP/client table. A FortiGate DHCP reservation can then
assign it a stable address; keeping the Pi itself as a DHCP client is
preferable to configuring static addressing individually.

SSH in:

```bash
ssh webelos@<IP>
```

If your laptop has an obsolete host-key entry --- expected, since step 19
gave this card a new identity:

```bash
ssh-keygen -R <IP>
```

or:

```bash
ssh-keygen -R wildlifecam7.local
```

Then verify on the Pi:

```bash
hostname
ip -4 addr show wlan0
rpicam-hello --list-cameras
systemctl status ssh --no-pager
systemctl status nginx --no-pager
systemctl status wildlife-camera --no-pager
curl http://127.0.0.1/
find /var/www/html/photos -type f | tail
journalctl -u wildlife-camera -n 50 --no-pager
```

From the laptop:

```bash
curl http://<IP>/
ssh webelos@<IP>
```

---

# Hardware notes

The workload is modest: Raspberry Pi OS, Wi-Fi, SSH, nginx, Python,
Picamera2/libcamera, motion detection, and static JPEG serving.

- Pi 5: proven working.
- Pi 4 with 1 GB+: expected to be ample.
- Pi 3 with 1 GB: expected to be adequate; test before mass cloning.
- Zero 2 W with 512 MB: expected to be adequate headlessly; test before mass
  cloning.

Camera cable differs:

- Pi 3/4: 15-pin Pi camera connector → generally 15-to-15-pin cable.
- Pi 5/Zero 2 W: 22-pin Pi connector → 22-to-15-pin cable for Camera
  Module 3.

An image made on one Pi model generally boots on the others, because
Raspberry Pi OS ships the kernels and device trees for all of them on the
boot partition. A 64-bit image will not boot on the original Pi Zero or
Pi 1, but every model listed above is fine.

---

# Collect photographs at camp

Because the laptop's public SSH key is already authorized, the photographs
can be pulled over the local Wi-Fi. The easiest way is the sync script in
this repository, which finds the cameras on the network by itself and gives
each one its own directory:

```bash
cd sync
./sync_cameras.py
```

See [`../sync/README.md`](../sync/README.md) for the details.

To do it by hand for a single camera:

```bash
mkdir -p ~/wildlife-photos/wildlifecam1

rsync -av --partial --timeout=10 \
    webelos@wildlifecam1.local:/var/www/html/photos/ \
    ~/wildlife-photos/wildlifecam1/
```

Repeat for each camera.

Do **not** use `--delete`; the laptop should retain photos even if files are
later removed from a camera.

Internet access is not required. The laptop and cameras only need to be on
the same functioning local Wi-Fi network.

---

# Quick clone procedure

For each new card:

1. `lsblk` and identify the destination device.
2. Confirm the destination is at least as large as the source card.
3. Unmount its partitions.
4. `dd` the master image to the **whole device**.
5. `sync`.
6. Mount root partition at `/mnt`.
7. Change `/mnt/etc/hostname`.
8. Update `/mnt/etc/hosts`.
9. Delete `/mnt/etc/ssh/ssh_host_*`.
10. Run `sudo ssh-keygen -A -f /mnt`.
11. Verify the new SSH host keys.
12. `sync`.
13. Unmount `/mnt`.
14. Eject the card.
15. Boot the target Pi.
16. Expand the filesystem if the card is larger than the master.
17. Find its DHCP address.
18. Verify SSH.
19. Verify camera detection.
20. Verify nginx.
21. Verify the wildlife-camera service.
22. Verify new photographs are being written.

## Copy/paste example: `/dev/sdc` → `wildlifecam7`

**Only use `/dev/sdc` after `lsblk` confirms that it is the SD card.**

Burn:

```bash
sudo umount /dev/sdc1 2>/dev/null || true
sudo umount /dev/sdc2 2>/dev/null || true

sudo dd if=~/webelos-wildlifecam.img of=/dev/sdc \
    bs=4M status=progress conv=fsync
sync
```

Personalize:

```bash
sudo partprobe /dev/sdc
sudo mount /dev/sdc2 /mnt

echo wildlifecam7 | sudo tee /mnt/etc/hostname
sudo sed -i 's/wildlifecam[0-9]*/wildlifecam7/g' /mnt/etc/hosts

sudo rm -f /mnt/etc/ssh/ssh_host_*
sudo ssh-keygen -A -f /mnt

cat /mnt/etc/hostname
grep wildlifecam /mnt/etc/hosts
ls -l /mnt/etc/ssh/ssh_host_*

sync
sudo umount /mnt
sudo eject /dev/sdc
```
