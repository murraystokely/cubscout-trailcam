# What is in the photographs: MegaDetector over the whole archive

**Run:** 13 September 2026, evening. Run 9 in the Mac Studio's manifest.
**Machine:** Mac Studio (`hilbert`, M4 Max), MPS, reading the archive over
SMB from `/Volumes/datasets/trailcam/photos/`.
**Detector:** MegaDetector v5a.
**Code:** `trailcam` at `6bc0e08` --- the commit that taught `frames` the
difference between a training frame and a photograph.
**Ranked list:** [`shortlists/2026-09-13-mdv5a-run9.csv`](shortlists/2026-09-13-mdv5a-run9.csv);
the gallery is on the NAS at `derived/shortlist/index.html`.

Until today MegaDetector had only ever seen the 5,673 training frames. The
35,502 photographs the cameras chose to keep --- where every good picture
is --- had been looked at by eye, in contact sheets, and by nothing else.

---

## The cost

| | frames | time | s/frame |
|---|---|---|---|
| training frames + photographs, one run | **41,175** | **56.1 min** | 0.082 |

That is the whole archive, from the NAS, in under an hour. The ThinkPad
would have taken about 30 hours. The camera fleet adds roughly 1,200
frames a day at present, which is 100 seconds of Mac Studio time, so
"has MegaDetector seen everything" can be true every morning.

## What the detector found

Over the 35,502 photographs:

| verdict | photographs | |
|---|---|---|
| empty | 30,806 | 86.8% |
| uncertain | 2,376 | 6.7% |
| **animal** (>= 0.8) | **1,202** | 3.4% |
| person (>= 0.8) | 1,113 | 3.1% |
| unreadable | 5 | zero-byte files |

Two things to read from that. **Seven photographs in eight are of nothing**,
which is the motion rules doing what they were designed to do: err on the
side of the picture, and let the laptop throw the junk away. And **as many
photographs are of people as of animals** --- 1,113 of them, on
wildlifecam10 (509) and wildlifecam4 (601), the two cameras that look at
the patio. The person class is doing the privacy job it was chosen for.

By camera, the animal count is not evenly spread:

| camera | photographs | animal | person |
|---|---|---|---|
| wildlifecam4 | 14,214 | **834** | 601 |
| wildlifecam10 | 14,099 | 317 | 509 |
| wildlifecam1 | 183 | 51 | 1 |
| wildlifecam12 | 7,006 | **0** | 2 |

**wildlifecam12 took seven thousand photographs of nothing.** Not one
animal at 0.8, two people. Whatever it is pointed at moves --- leaves,
shadow --- and never contains a subject. That is the strongest single
argument in this archive for moving a camera.

## What animals are they?

**Crows.** The 1,202 animal photographs group into **150 visits** (same
camera, no gap over a minute), on 35 camera-days, and every visit inspected
by eye --- the top thirty of the ranking and a sample below it --- is a
crow, or two crows, on the patio. The dark-eyed junco and possible towhee
noted by eye in earlier weeks did not make the ranked list; if they are in
the archive at all, they are in the uncertain band, where a small bird at
distance belongs.

The crows keep hours. Visits by hour of day:

```
06:00   25       10:00   18       14:00    2
07:00   28       11:00   15       15:00    3
08:00   28       12:00    7       16:00    2
09:00   16       13:00    3       17:00    1     18:00    2
```

**Four in five visits are before ten in the morning.** Anyone wanting to
photograph something other than a crow should look at the afternoon
frames, which is where the uncertain band's small subjects will be.

Most of the animals are small. The median animal box covers **0.4%** of
the frame; only 15 visits of 150 have a subject over 2%, and only 10 over
5%. The great photographs are the ten, and they are all from the first
week, when a crow was walking up to the lens.

## The best pictures

The ranking is confidence x size x wholeness x sharpness, one frame per
visit, no model beyond the detector. The top of it:

| # | score | frame | what it is |
|---|---|---|---|
| 1 | 0.96 | `wildlifecam4/2026-08-24/103403.jpg` | crow, whole, filling a fifth of the frame, Cheerios at its feet; a 74-frame visit |
| 2 | 0.88 | `wildlifecam4/2026-08-24/103925.jpg` | crow in profile beside the bench, head up |
| 3 | 0.79 | `wildlifecam4/2026-08-24/094743.jpg` | crow eyeing the pile of Cheerios |
| 4 | 0.79 | `wildlifecam4/2026-08-26/142514.jpg` | crow walking out past the doorpost |
| 5 | 0.78 | `wildlifecam4/2026-08-24/104420.jpg` | crow at the table |
| 6--7 | 0.53--0.56 | 26 and 27 August, 06:47 and 07:01 | dawn crows, marked down for blur |
| 8--9 | 0.51--0.52 | `wildlifecam1/2026-08-13/` | crow's feet: big and sharp, and cut off at the top |
| 12 | 0.38 | `wildlifecam4/2026-08-29/065153.jpg` | two crows, one near, one far |

Entries 8 and 9 are the ranking working as intended and not quite hard
enough: the clipped-box penalty (0.6) is what keeps a pair of legs out of
the top five, and it should probably be harsher. Entry 1 is the calling
crow the handoff named as the best picture in the archive, and the
ranking found it with no help.

## What this does not establish

- **Nothing about species beyond "crow", by eye, on 150 visits.** A
  classifier (M3, SpeciesNet) is the tool for the 2,376 uncertain frames,
  and for telling a junco from a leaf.
- **Nothing about the camera's recall.** The photographs are the frames
  that passed the rules; the training frames remain the only thing the
  rules can be graded on, and every number in `report` still reads those
  alone.
- **The ranking weights are guesses**, written down in `config.py` so
  they can be argued with. The right test is a person putting thirty
  pictures in order and seeing where the score disagrees.

## Reproducing this

```bash
cd ai
export WILDLIFE_PHOTOS=/Volumes/datasets/trailcam/photos
.venv/bin/python -m trailcam scan --kind all
.venv/bin/python -m trailcam detect --new-run        # 56 minutes on the Mac
.venv/bin/python -m trailcam shortlist --top 30
open data/shortlist/index.html
```
