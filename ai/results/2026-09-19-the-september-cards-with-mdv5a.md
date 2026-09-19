# The September cards, with the model we trust

**Run:** 19 September 2026. Run 9 in the Mac Studio's manifest, extended
(`detect --extend 9`) to cover everything that reached the NAS since the
13th.
**Machine:** Mac Studio (`hilbert`, M4 Max), MPS, archive over SMB.
**Detector:** MegaDetector v5a. This is the re-run the private analysis
repo asked for: every pass over these cards on the ThinkPad used the fast
`sorrel` model, which the same analyses found wrong in both directions.
**Ranked list:** [`shortlists/2026-09-19-mdv5a-run9.csv`](shortlists/2026-09-19-mdv5a-run9.csv);
gallery on the NAS at `derived/shortlist/index.html`.

---

## The cost

| | frames | time | s/frame |
|---|---|---|---|
| new since 13 Sep | **16,921** (15,624 photographs, 1,300 bursts) | **21.8 min** | 0.077 |
| run 9 now covers | 58,099 | | |

Indexing the new files first took six minutes, most of it reading sidecars
over SMB. The ThinkPad would have spent about seven hours on the same
frames.

## What arrived

Five cameras, two of them new to the archive. wildlifecam13 writes plain
JPEGs with no sidecars and no bursts, so its rows carry only the time in
the filename and it has no deployment run.

| camera | new photographs | animal (>= 0.8) | person (>= 0.8) | uncertain | empty | unreadable |
|---|---|---|---|---|---|---|
| wildlifecam4 | 7,129 | **460** | 345 | 338 | 5,984 | 2 |
| wildlifecam10 | 5,396 | 75 | 24 | 674 | 4,603 | 20 |
| wildlifecam11 | 772 | 27 | 30 | 141 | 567 | 7 |
| wildlifecam12 | 990 | 8 | 0 | 56 | 923 | 3 |
| wildlifecam13 | 3,638 | 6 | 46 | **3,085** | 495 | 6 |

The 46 unreadable frames are zero-byte files, almost all from the last
seconds before a battery died (wildlifecam10 and 11 on the 15th and 18th,
wildlifecam13 on the 18th) --- the signature the private analyses describe.
They are recorded as errors against run 9 and `detect --retry-errors`
will pick them up if the originals ever appear.

**wildlifecam13 is pointed at a hanging feeder.** 3,085 of its 3,638
frames are uncertain, and the contact sheets show why: a purple hanging
object and a bright hanging basket, under leaves, in moving sun, scored
between 0.2 and 0.7 all afternoon, in events of up to 199 frames. That is
the fourth static object to fool the detector on this project (the
Cheerios, the grass, the plant pot, and now the feeder). It also caught a
hand at 0.46 covering half the frame, which is the camera being set up.
Its six animal frames at 0.8 are real: squirrels on the ground below.

## What the animals are

The new photographs hold **581 animal frames in 90 visits**, by camera:

| camera | visits |
|---|---|
| wildlifecam4 (patio) | 59 |
| wildlifecam10 (under the trees by the shed) | 14 |
| wildlifecam11 | 9 |
| wildlifecam12 (side yard) | 6 |
| wildlifecam13 | 2 |

Every visit was looked at as a crop, on contact sheets ordered least
crow-like first. By eye, and so approximate:

| what | visits | where |
|---|---|---|
| crow | about 40 | almost all on the patio |
| **grey squirrel** | **about 35** | every camera; the leaf-litter cameras (10, 11) and the patio in roughly equal numbers |
| small bird: California towhee, black phoebe, a junco or two | about 12 | patio mornings, one on wildlifecam12 |
| too small or too blurred to say | a few | |

Two weeks ago the same exercise over the whole archive found nine
squirrel visits against about 125 crows. **Moving cameras off the patio
did what it was meant to do.** wildlifecam10 and wildlifecam11 sit over
leaf litter and bark, the squirrels are large in frame (1 to 3% of it,
against 0.3% on the patio), and the backgrounds are brown rather than
white, which is why they now rank alongside the close crows.

The hours have not changed:

```
06:00   70      09:00  119      12:00   30      15:00   21
07:00  121      10:00   31      13:00    1      16:00   13
08:00   80      11:00   36      14:00    9      17:00   21    18:00   24
```

Two thirds of the animal frames are before ten in the morning, with a
second, smaller shift at dusk from the squirrels.

## The best pictures

The ranking is unchanged in kind --- confidence x size x wholeness x
sharpness, one frame per visit --- and the new positions put non-crows
in its top ten for the first time.

| # | score | frame | what |
|---|---|---|---|
| 1 | 0.97 | `wildlifecam4/2026-08-24/103403.jpg` | the calling crow, still |
| 2 | 0.91 | `wildlifecam4/2026-08-24/103925.jpg` | crow in profile |
| 3 | 0.87 | `wildlifecam4/2026-08-26/142514.jpg` | crow at the doorpost |
| **4** | **0.84** | `wildlifecam11/2026-09-17/082953.jpg` | **squirrel running across bark, tail up, 3% of the frame** |
| 5--6 | 0.81 | 24 August | crows |
| **7** | **0.72** | `wildlifecam10/2026-09-17/125515.jpg` | **squirrel foraging in leaf litter, tail curled over its back** |
| **8** | **0.71** | `wildlifecam10/2026-09-15/111609.jpg` | squirrel, the frame the private analysis picked as sharpest |
| 9 | 0.63 | `wildlifecam10/2026-09-15/102347.jpg` | crow in full profile on the leaf litter |
| 12 | 0.61 | `wildlifecam11/2026-09-17/182708.jpg` | squirrel at dusk, side on |
| 33 | 0.53 | `wildlifecam13/2026-09-18/145902.jpg` | squirrel on the ground under the feeder |

Worth knowing below the top: wildlifecam4 has a towhee on the 12th at
07:36 and again on the 13th, 14th and 16th mornings; a black phoebe on
the 15th at 09:32 and the 16th at 08:33; and a squirrel climbing a
blue-painted post on wildlifecam11 on the 17th and 18th, which is the
oddest picture of the set.

## People

**445 photographs of people** in the new days at 0.8, 345 of them on the
patio (151 on the 12th, 113 on the 16th), and 46 on wildlifecam13 during
its setup. The shortlist now excludes any animal frame that falls in a
motion event with a person anywhere in it at 0.3 or above, which is the
rule the private analyses asked for after MegaDetector scored a gardener
as an animal for fourteen minutes. It removed 22 frames from the ranking,
all from August and early September events; nothing from the new days
needed it, because on the new days the people and the animals kept
different hours.

## What this does not establish

- **Species are by eye from crops.** "About 35 squirrel visits" is a
  count of tiles that looked like squirrels. The 0.5 to 0.8 band on the
  new days holds more real squirrels (several at 0.6 to 0.79 on
  wildlifecam13 and wildlifecam4) that the 0.8 line leaves out; a species
  classifier over the crops is still the tool for both jobs.
- **Nothing about the camera's recall.** The bursts are the only thing
  the rules can be graded on, and `report` still reads those alone.
- **The comparison with the private analyses is loose.** They counted at
  0.5 with sorrel and then re-ran two cameras with v5a on the laptop;
  where the same days and threshold can be compared (wildlifecam10 on the
  14th and 15th, wildlifecam12 on the 15th) the counts agree to within a
  frame.

## Reproducing this

```bash
cd ai
export WILDLIFE_PHOTOS=/Volumes/datasets/trailcam/photos
.venv/bin/python -m trailcam scan --kind all
.venv/bin/python -m trailcam detect --extend 9      # 22 minutes for these cards
.venv/bin/python -m trailcam shortlist --run 9 --top 40
```
