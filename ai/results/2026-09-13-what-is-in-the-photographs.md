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

**wildlifecam12 took seven thousand photographs and not one has an animal
at 0.8.** Two people. Most of what it fires on is a flowering shrub
against a blue wall, which MegaDetector scores as an animal at 0.1 to 0.5
over and over. But it is not quite nothing: in its uncertain band there
are squirrels, at 0.13 to 0.36 --- small, distant, and real, found by eye
in the contact sheets described below. The camera is pointed at the
wrong thing, not at nothing.

## What animals are they?

**Mostly crows --- and the first draft of this section said "all crows",
which was wrong.** The 1,202 animal photographs group into **150 visits**
(same camera, no gap over a minute) on 35 camera-days. The first draft
looked at the top of the ranking, saw crows, and generalised. Murray then
pointed at `wildlifecam4/2026-09-12/101502.jpg` --- a squirrel running at
the camera, which MegaDetector had scored **0.925** and the ranking had
put at **126 of 150**.

So every one of the 150 visits was then looked at, as contact sheets of
the best crop per visit, ordered least crow-like first (a crow is black;
sort by how colourful and bright the box is). Found that way, in a few
minutes:

| what | visits | examples |
|---|---|---|
| crow | about 125 | everywhere, all day |
| **squirrel** | **9** | `wildlifecam4/2026-09-12/085105.jpg` (the good one: 2.7% of the frame, four frames); `2026-09-12/101502.jpg` (Murray's); `2026-09-08/165400.jpg` and `165529.jpg`; `2026-09-07/184206.jpg`; `2026-09-07/105900.jpg`; `2026-08-29/064859.jpg`; `wildlifecam10/2026-09-05/103237.jpg` (mid-leap); `wildlifecam10/2026-09-01/065942.jpg` |
| **small brown bird** (California towhee, by the shape; a dark-eyed junco or two) | about 15 | `wildlifecam4/2026-09-12/073606.jpg` (tail up, eight frames); `2026-09-07/155120.jpg`; `2026-09-09/064607.jpg`; `2026-09-10/112301.jpg` (junco); `2026-09-05/104556.jpg`; `2026-09-11/075819.jpg`; `wildlifecam10/2026-09-07/070153.jpg` |
| a person | 1 | `wildlifecam4/2026-09-04/135618.jpg`, animal 0.80, no person box at all; the frames either side of it say person at 0.65 to 0.93 |

And below the 0.8 line, in the 2,376-frame uncertain band (496 visits by
the same grouping), the top eight sheets of thirty were also read: more
squirrels at 0.7 to 0.78 on wildlifecam4 and wildlifecam10 (mostly
companions of visits already counted), squirrels at 0.13 to 0.36 on
wildlifecam12, two more towhees, a leaf at 0.78, the pile of Cheerios at
0.79 and 0.80, a hand, and a child's face upside down against the lens at
0.12. The rest of that band is wildlifecam12's shrub.

**What the detector missed is not the story.** It found the squirrel at
0.925. What buried it was the ranking, twice over: first by size (a
squirrel at 0.3% of the frame against crows at 15%), and then, once the
size term was softened, by sharpness (it is running, and blurred). Both
are honest judgements of the *photograph*; neither has any idea what the
animal is. A list ranked by picture quality will be a list of the animal
that comes closest to the lens, and here that is a crow. Two things
changed as a result:

- the gallery now shows **every** visit, the top thirty large and the
  rest as a strip of crops, so nothing the detector found is invisible;
- the size term is a fourth root rather than a square root, the clipped
  penalty is 0.35 rather than 0.6, and an animal frame within a minute
  of a person on the same camera is left out (that is how the child at
  13:56 goes).

The right fix is the one `design.md` always had as M3: a species
classifier on the crops, so "best squirrel" and "best towhee" are
questions the manifest can answer. The colour sort above is a stand-in
for it that happens to work in a garden whose commonest animal is black.

The crows keep hours. Visits by hour of day (all species; the squirrels
and towhees are spread through the day, which the crows are not):

```
06:00   25       10:00   18       14:00    2
07:00   28       11:00   15       15:00    3
08:00   28       12:00    7       16:00    2
09:00   16       13:00    3       17:00    1     18:00    2
```

**Four in five visits are before ten in the morning.**

Most of the animals are small. The median animal box covers **0.4%** of
the frame; only 15 visits of 150 have a subject over 2%, and only 10 over
5%. The great photographs are the ten, and they are all from the first
week, when a crow was walking up to the lens.

## The best pictures

The ranking is confidence x size x wholeness x sharpness, one frame per
visit, no model beyond the detector. The top of it, after the changes
above:

| # | score | frame | what it is |
|---|---|---|---|
| 1 | 0.96 | `wildlifecam4/2026-08-24/103403.jpg` | crow, whole, filling a fifth of the frame, Cheerios at its feet; a 74-frame visit |
| 2 | 0.88 | `wildlifecam4/2026-08-24/103925.jpg` | crow in profile beside the bench, head up |
| 3 | 0.87 | `wildlifecam4/2026-08-26/142514.jpg` | crow walking out past the doorpost |
| 4 | 0.79 | `wildlifecam4/2026-08-24/094743.jpg` | crow eyeing the pile of Cheerios |
| 5 | 0.78 | `wildlifecam4/2026-08-24/104420.jpg` | crow at the table |
| 6--20 | 0.53--0.60 | mornings, late August to 11 September | crows at half a metre to two metres, sharp, small in the frame |
| 21 | 0.53 | `wildlifecam4/2026-09-05/103240.jpg` | the first non-crow: the leaping squirrel's visit, seen from wildlifecam4 |
| 56 | 0.46 | `wildlifecam4/2026-09-12/073650.jpg` | the towhee, tail up |
| 109 | 0.37 | `wildlifecam4/2026-09-12/085105.jpg` | the best squirrel: close, but soft |
| 121 | 0.31 | `wildlifecam4/2026-09-12/101502.jpg` | Murray's squirrel, running and blurred |

Entry 1 is the calling crow the handoff named as the best picture in the
archive, and the ranking found it with no help. The crow's feet from
wildlifecam1 that were 8th and 9th on the first pass are now 60th and
below, which is where a picture of half an animal belongs.

**For anything that is not a crow, the ranking is the wrong tool and the
gallery's full strip is the right one.** Ranked by picture quality, the
best squirrel in the archive is 109th. It is not a bad picture; it is a
soft one, of a small animal, in a list of sharp pictures of a big one.

## What this does not establish

- **Species are by eye, from crops, by one person, in one evening.**
  "Towhee" is a shape and a colour; "about 125 crows" is a count of tiles
  that looked like crows. A classifier (M3, SpeciesNet) is the tool, both
  for the 150 visits and for the 2,376 uncertain frames, where the
  wildlifecam12 squirrels live at 0.13.
- **The first draft of this file said every visit was a crow.** It was
  written after looking at the top of a ranking that cannot see species,
  and it was wrong within the hour. Left in as a correction rather than
  rewritten, because that is exactly the mistake a ranking invites.
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
