# Finding the good pictures at a campsite, with no internet

At home the cameras sync over Wi-Fi and the archive lives on the NAS. In a
field neither exists. This is the offline version: card in a reader,
photographs on the laptop, a research-grade detector over every one of them,
and a folder of the pictures worth showing people.

One command does all of it:

```bash
cd ~/git/cubscouts
./campsite.sh
```

## Before you leave home — do this once

```bash
./campsite.sh --check
```

It checks the four things that cannot be fixed without internet and says
which are missing:

| | what it needs | how to fix it at home |
| --- | --- | --- |
| Python environment | `ai/.venv` with torch and megadetector | `cd ai && python3 -m venv .venv`, then the two `pip install` lines in [`../ai/README.md`](../ai/README.md) |
| detector weights | `ai/models/md_v5a.0.1.pt`, 268 MB | copy from `/mnt/datasets/trailcam/models/` — **they do not download themselves without internet** |
| `rsync` | for copying cards | `sudo apt install rsync` |
| disk space | ~16 GB per full card | free some up |

It creates nothing and changes nothing, so it is safe to run whenever.
`Ready for the field.` means go.

## At the campsite

```bash
./campsite.sh                          # card in the reader
./campsite.sh --card /media/you/rootfs # if it cannot find the card itself
./campsite.sh --skip-copy              # cards already copied; just look again
./campsite.sh --top 60                 # a longer shortlist
```

What happens, in order:

1. **Copies the card** with `sync/sync_sdcard.py`, into `~/wildlifecam-photos/<camera>/<day>/`. The camera's name comes off the card itself, so several cards in a row file themselves correctly. **The card is not emptied** — the script prints the command to do that once you are happy.
2. **Indexes** what is now on disk (`trailcam scan`).
3. **Looks at every new photograph** with MegaDetector v5a (`trailcam detect`), about **1.6 seconds each** on the ThinkPad — a thousand photographs is roughly half an hour. Ctrl-C stops cleanly and running again resumes; it only ever looks at photographs it has not seen.
4. **Ranks the best** (`trailcam shortlist`) into `ai/data/shortlist/`, with thumbnails and an `index.html` to click through.

Run it again after each card. Nothing is recomputed.

## Why v5a and not the fast model

`campsite.sh` uses MegaDetector v5a deliberately. The small MDv6 variants
(`md1000-spruce`, `md1000-sorrel`) are roughly ten times faster and will hand you a
shortlist full of plant pots, gutters and — memorably — the pile of cereal
put out for the crows, which one of them scored 0.86 in frame after frame.
The reasoning and the measurements are in [`../ai/README.md`](../ai/README.md)
under *What happened the first time we ran it*.

If you are only curious how many pictures there are and want an answer in
minutes rather than an hour, the fast model is there:

```bash
cd ai
WILDLIFE_PHOTOS=~/wildlifecam-photos .venv/bin/python -m trailcam detect \
    --kind photo --model md1000-spruce --new-run
```

Treat what it says as a rumour, not a result, and do not put its numbers in
a write-up.

## If something goes wrong

**"no Python environment" / "megadetector or torch will not import"** — you
are in the field without the thing that needed internet. Nothing to be done
there; the photographs are safely copied, so run the detector at home.

**"no detector weights"** — same. Copying is still worth doing.

**The card is not found** — wait for the desktop to mount it, then pass the
path: `./campsite.sh --card /media/you/rootfs`. A Raspberry Pi card has two
partitions and the photographs are on the big one, usually `rootfs`.

**It is slower than half an hour a thousand** — something else is using the
CPU, or the photographs are being read across a network. On battery, expect
it to be slower still.

**You want the pictures off before the detector has finished** — they are
already on the laptop at the end of step 1. The rest only decides which to
look at first.
