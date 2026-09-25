# Camping cheatsheet

Everything here works on the ThinkPad with **no internet and no NAS**.
Checked on 25 Sept 2026: `./campsite.sh --check` says *Ready for the field*
(Python environment, MegaDetector weights, rsync, 287 GB free).

Every command assumes a terminal. The card reader shows a card as
`/media/murray/rootfs` (photos + software) and `/media/murray/bootfs`.

---

## 1. Copy a card's photos to the laptop

Put the card in the reader and wait a few seconds for it to mount.

```bash
cd ~/git/cubscouts/sync
./sync_sdcard.py --list                     # which camera is this, how many photos?
```

**Copy only (card keeps its photos):**

```bash
./sync_sdcard.py
```

**Copy, then wipe the card (normal before redeploying):**

```bash
./sync_sdcard.py --wipe --checksum --yes
```

`--checksum` compares every file by sha256 before deleting it from the card.
The script deletes only files it has verified, and only inside the photos
folder. The camera's software is never touched.

Photos land in `~/wildlifecam-photos/<camera>/<day>/`, e.g.
`~/wildlifecam-photos/wildlifecam9/2026-09-26/`. That's the same layout as
the NAS, which makes copying back easy (section 7).

Changed your mind and want to wipe a card you already copied? Just run the
wipe command. It skips files that are already copied, checks them, then
empties the card.

**Eject the card** before pulling it out:

```bash
udisksctl unmount -b /dev/sda2 && udisksctl unmount -b /dev/sda1
```

Don't use `udisksctl power-off`: the reader then stays dead until you
unplug it and plug it back in.

---

## 2. Quick look, no AI

```bash
D=~/wildlifecam-photos/wildlifecam9/2026-09-26     # <- camera and day
ls $D/*.jpg | wc -l                                 # how many photos
ls $D | grep jpg | cut -c1-2 | sort | uniq -c       # photos per hour (HH)
xdg-open $D                                         # browse in the file manager
```

A burst of hundreds in one hour is usually sun, shadows or wind, not animals.
**Empty (0-byte) files at the very end are normal**: the camera was unplugged
mid-save.

---

## 3. Run MegaDetector on the photos

```bash
cd ~/git/cubscouts/ai
export WILDLIFE_PHOTOS=~/wildlifecam-photos

.venv/bin/python -m trailcam scan --kind photo                       # index what's on disk
ls ~/wildlifecam-photos/*/                                           # which days were copied?
.venv/bin/python -m trailcam detect --kind photo --day 2026-09-26    # one day at a time
```

**Always give `--day`.** The manifest also lists ~58,000 photos that live on
the NAS. Without `--day`, detect tries all of them, and each one fails as
"missing" because the NAS isn't here. Adding `--camera wildlifecam9` narrows
it further.

- **Speed:** 1.6–5 seconds per photo on this laptop, so about **30–90
  minutes per 1,000 photos**. It's slower on battery; plug in if you can.
  Try `--limit 20` first to see the speed.
- **Ctrl-C stops cleanly.** Run the same command again to carry on where it
  stopped.
- **Check the day folders:** a Pi has no clock battery and no internet at a
  campsite, so its date can be wrong. Photos may land in an odd day folder,
  such as the day the camera was last switched off at home. Run detect for
  every day folder `ls` shows.

**See the best pictures:**

```bash
.venv/bin/python -m trailcam runs          # note the run number(s) from today, e.g. 33
.venv/bin/python -m trailcam shortlist --kind photo --run 33 --top 40 --out ~/campout-shortlist
xdg-open ~/campout-shortlist/index.html
```

Each `detect` that finishes is its own run. To rank several days together,
repeat `--run`: `--run 33 --run 34 --run 35`.

Don't use `./campsite.sh` at the campsite. Its copy step is fine, but its
detect step has no `--day`, so it hits the 58,000-missing-photos problem
above.

---

## 4. Put the latest software on a card

The laptop's repo has the current code: step 9 at 1,440 an hour, step 8 at
720 an hour. With a card mounted:

```bash
cd ~/git/cubscouts
C=/media/murray/rootfs/home/webelos
cat /media/murray/rootfs/etc/hostname                  # which camera?
for f in step*.py final_motion_capture.py README.md; do
  cmp -s $f $C/$f || { echo "updating $f"; cp $f $C/$f; }
done
rm -rf $C/__pycache__
for f in step*.py final_motion_capture.py README.md; do cmp -s $f $C/$f || echo "STILL DIFFERENT: $f"; done
```

No output from the last line means the card matches. Then eject (section 1).

Every card through the reader on 24–25 Sept (wildlifecam9–15) has this code
**except the step 9 change to 1,440, which only wildlifecam11 has**. Run
this on each of the others before deploying; the loop will print
`updating step9_plain_motion.py`.

---

## 5. Make a new camera card from an image

The two images on the laptop:

| Image | Use for |
| --- | --- |
| `~/wc-lowmem.img` | Normal field camera, especially a Pi Zero 2 W. Boots to the console, no desktop. |
| `~/wc-gui.img` | A camera you want the desktop on. |

Both have **older scripts** (from 23 Sept), so always do section 4 after
burning.

```bash
cd ~/git/cubscouts/clone
lsblk -o NAME,SIZE,TYPE,RM,TRAN,MODEL            # find the card: usually /dev/sda, NOT nvme

# 1. Give it its own name (writes a new image file; never touches a card)
sudo ./clone_image.sh --hostname wildlifecam16 ~/wc-lowmem.img ~/wc16.img

# 2. Only if burn says it won't fit (32 GB cards differ by a few MB):
sudo ./shrink_image.sh --dry-run --fit /dev/sda ~/wc16.img
sudo ./shrink_image.sh --fit /dev/sda ~/wc16.img

# 3. Burn and verify (refuses the laptop's own disk; asks you to type the device)
sudo ./burn_image.sh --verify ~/wc16.img /dev/sda
```

Then take the card out and put it back in. It mounts again, and you can do
section 4. Delete `~/wc16.img` afterwards (32 GB).

`wc-lowmem.img` is 31.91 GB, which is right at the edge of a "32 GB" card.
Expect to need step 2.

---

## 6. Settings on the cards, and where to point cameras

| | Regular camera (step 9) | AI camera (step 8) |
| --- | --- | --- |
| Smallest motion that counts | 250 px (distant deer ≈ 500 px) | AI + shadow checks |
| Cooldown | 1 s between photos | 1 s |
| Hourly limit | **1,440** | 720 |
| Night | saves nothing when it's too dark to see | same |
| Worst case (limit every daylight hour) | card full in 1–2 days | card full in ~2 days |
| Typical day | 1.4–2.5 GB, so 3 days ≈ 4–8 GB of ~20 GB | ~1.5 GB/day |

A card at 95% full stops taking photos.

**Placement matters more than any setting:**
- Keep sunlit leaves and sunny ground patches out of the picture. They were
  the #1 waste this week and used up the hourly limit on the patio camera.
- Keep branches and thin trunks away from the edges of the picture.
- Don't point it at a lantern, campfire or lit tent. A light makes the night
  count as daytime.
- A crow 10 m away is only a small blob. Closer is better.

**Changing a setting in the field:** edit the number on the card, e.g.
`nano /media/murray/rootfs/home/webelos/step9_plain_motion.py`, and search
for `MAX_SAVES_PER_HOUR` or `SAVE_COOLDOWN`. Write down what you changed so
it can go into git back home.

---

## 7. Back home

```bash
ls /mnt/datasets/trailcam                    # NAS mounted? If not: sudo mount /mnt/datasets
rsync -a ~/wildlifecam-photos/ /mnt/datasets/trailcam/photos/
rsync -a --checksum --dry-run --itemize-changes ~/wildlifecam-photos/ /mnt/datasets/trailcam/photos/
```

If the second rsync prints nothing, every file on the NAS matches. Only then
delete the laptop copy. After that, point `WILDLIFE_PHOTOS` at
`/mnt/datasets/trailcam/photos`, run `scan`, and extend the reference run as
usual.

---

## 8. When something goes wrong

| Problem | Fix |
| --- | --- |
| Card doesn't show up | `lsblk`. If there's no `sda`, unplug the reader and plug it back in. |
| `sync_sdcard.py` can't find the card | `./sync_sdcard.py /media/murray/rootfs` |
| Detect says "missing" / lots of "unreadable frames" | You forgot `--day`. Stop it (Ctrl-C) and rerun with `--day`. |
| Detect very slow | Plug in power, and close the browser. |
| Photos in a strange date folder | The Pi's clock was wrong (no internet). The photos are fine; run detect for that day too. |
| 0-byte files at the end of a day | The camera was unplugged mid-save. Normal. |
| Hundreds of photos of nothing | Sun, shadows or wind. Move or re-aim the camera (section 6). |
| AI camera says "bird" for a squirrel | Its model has no squirrel. "bird" means small animal. |
