# The first passes on the Mac Studio: the archive works from the NAS, and v5a against v6

**Run:** 13 September 2026, afternoon.
**Machine:** Mac Studio (`hilbert`, M4 Max, 16 cores), MPS.
**Archive:** `/Volumes/datasets/trailcam/photos/` on the NAS, over SMB ---
5,673 training frames, three cameras, 21 days (2026-08-23 to 2026-09-12).
**Code:** `trailcam` at `d98ae12` (the redwood fix), Python 3.14, torch 2.14.
**Detectors:** MegaDetector v5a (`md_v5a.0.1.pt`) and MDv6-redwood
(`md_v1000.0.0-redwood.pt`), both from the NAS `models/` directory.

Two questions from the handoff, answered in order.

---

## 1. Does the archive work end-to-end from another machine?

Yes. A manifest built from nothing on this machine, over the network:

```
scan      5,673 frames indexed, 6 deployments of step8 found       31 s
detect    MDv5a, run 7, 5,673 frames                          8.3 min
report
```

**0.088 s/frame from the NAS** against the 0.072 the benchmark measured
from local disk --- about **22% slower over SMB**, rather more than the 12%
the ThinkPad saw over its link, and still 30x the ThinkPad's local rate.
Decode is not the binding constraint yet, so nothing needs copying locally
for a daily pass; it would be worth revisiting if the pass ever grows to the
35,000 event photographs, where 22% is nine minutes.

### The numbers reproduce

| verdict | ThinkPad, 12 Sep, 5,459 frames | Mac, 13 Sep, 5,673 frames |
|---|---|---|
| empty | 4,942 | 5,145 |
| uncertain | 451 | 461 |
| person | 36 | **35** |
| animal | 28 | **30** |
| unreadable | 2 | 2 |

The 214 extra frames are two more days of bursts synced since the ThinkPad
ran (the 11th and 12th). Both differences in the interesting rows are
explained frame by frame:

- **Two more animals**: both on `wildlifecam4/2026-09-11`, at 17:37, scoring
  0.864 and 0.832 --- new data, not drift. That is a fourth animal event
  in the archive; the camera's decisions for it are in the report and
  worth a look when the next E1 write-up is done.
- **One fewer person**: `wildlifecam10/2026-08-27/train_224930_301.jpg`
  scores **0.799** here and **0.802** on the ThinkPad. It sits on the
  `PERSON_TRUTH` line and the 0.003 cross-architecture spread the benchmark
  measured pushed it over. The benchmark's own comparison had missed this,
  because it only checked the animal threshold; it now checks both.

Nothing else moved. The shadow rule's record holds: 1,185 empty frames and
127 uncertain ones rejected as shadow, and still **not one** frame
MegaDetector calls an animal.

**The ThinkPad's second copy of the archive can go.** That was the
condition set for deleting it, and it is met.

### The two unreadable frames are zero bytes

`wildlifecam12/2026-09-03/training/train_202034_616.jpg` and
`train_202044_760.jpg` are **0 bytes on the NAS**, so they were 0 bytes on
the ThinkPad too (the migration was checksum-verified). The copy is
faithful; the loss happened at or before the first sync, at 20:20 on an
evening when the burst was probably being written as the sync ran. If
wildlifecam12 still has the originals, a re-sync fixes it; if not, two
frames out of 5,673 is nothing, and `detect --retry-errors` picks them up
whenever they arrive.

---

## 2. v5a against v6-redwood

`config.DETECTOR` has defaulted to MDv5a "by convention, not evidence"
since the day it was written. Redwood --- the MDv6 flagship, same size and
same speed --- had never been run on our frames because it would not load
on this machine (a Python 3.14 error-message change; see the benchmark
write-up). With that fixed, run 8 is redwood over the same 5,673 frames:
8.5 minutes, **32 animal frames and 51 person frames** against v5a's 30
and 35.

```
trailcam compare 7 8

  5673 frames seen by both, 4352 agreed (76.7%)

         run 7 \ run 8
                   animal      empty      error     person  uncertain
        animal         27          0          0          0          3
         empty          0       4095          0          0       1050
         error          0          0          2          0          0
        person          0          0          0         35          0
     uncertain          3        249          0         16        193
```

Read along the diagonal first: on the frames that matter most --- the
animals and the people --- the two models agree. Every disagreement is in
one of three places, and each was looked at by eye.

### The animals: same crows, threshold noise

Six frames swap between `animal` and `uncertain`, three each way. All six
are crows from the events already known, scoring between 0.75 and 0.89 on
both models:

| frame | v5a | redwood |
|---|---|---|
| wildlifecam4/2026-08-26 09:26:22 | 0.746 | 0.845 |
| wildlifecam4/2026-08-26 09:26:42 | 0.518 | 0.801 |
| wildlifecam4/2026-08-26 09:26:48 | 0.764 | 0.885 |
| wildlifecam10/2026-09-05 10:32:53 | 0.839 | 0.766 |
| wildlifecam4/2026-09-05 10:30:12 | 0.826 | 0.794 |
| wildlifecam4/2026-09-11 17:37:27 | 0.832 | 0.778 |

Redwood is a little more confident about the 26 August crow (the small,
distant one the camera missed) and a little less about the others. Neither
model finds an animal the other has no idea about. **No new species, no new
event.** Whatever is in this archive, both detectors agree on it.

### The people: redwood is right, and v5a was under-calling

Sixteen frames go from v5a `uncertain` to redwood `person`. On every one
v5a already had a person box at **0.41 to 0.80**, redwood puts it at **0.82
to 0.96**, the camera's motion rules fired (`strong motion` or `confirmed
motion` on all sixteen), and on nine of them the camera's own little
on-board model also said "person". These are people. v5a's person recall
at the 0.8 line is the weaker of the two.

For the evaluation this matters only slightly --- a person frame must not
be counted as empty, and at 0.4 to 0.8 v5a already keeps them out of
`empty` and in the hand-labelling queue. For the eventual "best animal
photos" gallery it matters more: excluding Scouts is the privacy
mechanism, and redwood does that job better.

### The empties: redwood sees animals in sunlit paving, all day long

This is the finding. **1,050 frames v5a calls empty (below 0.1), redwood
scores between 0.1 and 0.8.** By camera:

| camera | frames | v5a uncertain | redwood uncertain |
|---|---|---|---|
| wildlifecam4 | 3,276 | 256 | **1,009** |
| wildlifecam10 | 1,443 | 14 | 69 |
| wildlifecam12 | 954 | 124 | 53 |

On wildlifecam12 redwood is actually the quieter model. On wildlifecam4 it
is four times noisier, and the crops show why: the strongest of them, day
after day, are **dappled sunlight on the paving slabs**, and the corner of
the planter in front of the lens.

```
wildlifecam4/2026-09-08 11:30   redwood 0.78-0.80   v5a 0.00   a whole burst
wildlifecam4/2026-09-09 11:30   redwood 0.75        v5a 0.00   same slabs
wildlifecam4/2026-09-07 11:30   redwood 0.67        v5a 0.00
wildlifecam4/2026-09-10 09:40   redwood 0.67        v5a 0.00
wildlifecam4/2026-09-06 11:30   redwood 0.66        v5a 0.00
```

Nothing is in any of them. It is not one hour of the day either: the
redwood-only uncertain frames on wildlifecam4 are half of everything
recorded at 08:00 and at 12:00, and a fifth of what is recorded at 16:00.
It is the scene --- light through leaves onto pale stone --- and this
camera looks at it all day. One burst on the 8th reaches **0.80**, which is
the animal line: redwood would have put a burst of shadows into the
ground truth as a crow.

Redwood's uncertain band, the list a person has to work through by hand,
is **1,246 frames against v5a's 461**. Most of the extra 785 are the same
slabs.

### The decision: v5a stays the reference

Two things are being traded. Redwood is better on people. v5a is far
quieter on the one scene we have most of, and the reference run's whole
job is to label animals cheaply and leave a short list for a person. A
detector that triples that list, and that scores shadows at 0.8, is the
wrong tool for that job however good it is at the other one. **Run 7
(v5a) remains the reference; `config.DETECTOR` stays MDV5A, now with a
reason.**

Which is *not* the same as "v5a is the better model". This is one suburban
patio, four crow events, and a camera pointed at sunlit paving. The
published comparisons that put v6 ahead were made on real trail cameras
with real animals in them, and nothing here contradicts them. When the
cameras go somewhere with wildlife, run both again; the comparison is one
command and eight minutes.

### A defect this turned up in `compare`

The first version of the matrix above showed sixteen frames going from
`empty` to `person`, which made no sense --- a frame with a 0.7 person box
is not empty. `compare` was bucketing on animal confidence alone, while
the `verdicts` view requires the person confidence to be under 0.1 as well.
They now agree. The corrected matrix is the one printed here, and the
sixteen are where they belong, in the `uncertain` row.

---

## What this does not establish

- **Nothing about redwood on real wildlife.** Four events, all crows.
- **Nothing about which model a person would agree with more often.**
  The 461-frame uncertain band is still unlabelled, and until it is, "v5a
  is quieter" and "v5a is missing things" are the same observation. The
  1,050 frames the two models disagree about are, as `compare` says, the
  ones to hand-label first; they are the cheapest possible check on both.
- **Nothing about the 35,502 event photographs.** Neither model has seen
  one. That is the next job, and it needs `frames` to learn the difference
  between a training frame and a photograph the camera chose.

## Reproducing this

```bash
cd ai
export WILDLIFE_PHOTOS=/Volumes/datasets/trailcam/photos
.venv/bin/python -m trailcam scan
.venv/bin/python -m trailcam detect                              # run 7
.venv/bin/python -m trailcam detect --model md1000-redwood --new-run   # run 8
.venv/bin/python -m trailcam report
.venv/bin/python -m trailcam compare 7 8
```

Crops for both runs are under `ai/data/crops/run-7/` and `run-8/`, which
is where the sunlit-paving frames above were looked at.
