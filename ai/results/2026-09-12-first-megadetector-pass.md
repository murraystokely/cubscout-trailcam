# The first MegaDetector pass over the training bursts

**Run:** 12 September 2026.
**Milestone:** E1 (Label) from [`../evaluation-design.md`](../evaluation-design.md).
**Detector:** MegaDetector v5a (`md_v5a.0.1.pt`, md5 `60f8e7ec...`).
**Code:** `trailcam` at commit `bdd8ee2`, cameras running `step8_reject_shadows.py`.
**Machine:** ThinkPad, 8-core Core Ultra 7 258V, CPU only, 7 threads.
**Cost:** 5,339 frames in 3 h 57 m --- 2.66 s/frame.

---

## What was in the data

| | |
|---|---|
| training frames (full colour) | 5,459 |
| cameras | wildlifecam4 (3,080), wildlifecam10 (1,443), wildlifecam12 (936) |
| days | 19, from 2026-08-23 to 2026-09-10 |
| burst windows | 581 |
| **unbiased recording time** | **about 4.8 hours** |

That last figure is the one to keep in mind while reading everything below.
A month of three cameras running produced under five hours of footage the
motion rules were not allowed to filter, because a burst is thirty seconds
once an hour. Everything here rests on those 4.8 hours.

## What the detector found

```
empty       4942   90.5%
uncertain    451    8.3%   <- the hand-labelling queue
person        36    0.7%
animal        28    0.5%
unreadable     2
```

5,006 of 5,459 frames were labelled without a person looking at them. The
uncertain band came to 451 frames, which is the "few hundred, not a hundred
thousand" that `evaluation-design.md` predicted, and a realistic evening's
work for a Scout.

**The 28 animal frames are three events.** Twenty-five of them are a single
crow on wildlifecam10. Every per-frame percentage in this document therefore
rests on three animal visits, and none of them should be quoted as a rate.

All four crops inspected by eye were unmistakably crows. At the 0.8 band, on
this data, MegaDetector was not making things up.

## The three events, and what the camera did

| event | camera | when | frames | photographed? |
|---|---|---|---|---|
| 1 | wildlifecam10 | 5 Sep, 10:32 | 25 | **yes --- 39 photographs** |
| 2 | wildlifecam4 | 26 Aug, 09:26 | 2 | no |
| 3 | wildlifecam4 | 5 Sep, 10:30 | 1 | no |

**Event recall: 1 of 3.** With n = 3 that is an anecdote, not a measurement.
The mechanisms behind it are worth more than the ratio.

A caution about reading the `report` output: its "photograph taken" column
counts *training frames whose decision was a save*, not photographs of the
event. Event 1 shows as 3 there, and the camera actually wrote 39 JPEGs
during that minute. The training frames are a sparse sample of the event;
the photo directory is the truth about what was kept.

## Why the two misses happened --- and they are different

This is the useful part, and it is only visible because the CSV records the
rules' own numbers at 4 Hz alongside the frames.

**Event 2 --- too small.** The crow *was* seen. Through the whole visit the
largest blob measured **49 to 91 pixels**, steadily, with the decision
`quiet` throughout:

```
09:26:41.041  area=70  chg=0.00023  quiet
09:26:43.850  area=87  chg=0.00028  quiet
09:26:44.958  area=91  chg=0.00030  quiet
```

`MIN_BLOB_AREA` is **301**. The bird was three to six times below the floor,
so no threshold in the shape or texture tests ever ran --- rule 4 dropped it
first. `step8` already predicts this in a comment: the floor "gives up on an
animal smaller than about 150 px, which at this lens is a squirrel beyond
~10 m or a deer beyond ~40 m". Here is that comment happening, measured, to
a crow on a patio.

Note what *did* work: the full-colour frame exists at all because the burst
takes an extra frame whenever anything stirs, at `RECORD_MOTION_AREA` = **92
px** --- a sixth of the save floor. The blob touched 91 px. That mechanism,
added specifically so the training set would contain an animal, is the only
reason this event is in the dataset.

**Event 3 --- perfectly still.** Different failure entirely. The crow is in
the frame at 0.82 confidence, and the motion rules report `area=0` --- *zero
changed pixels* --- for the whole window either side of it:

```
10:30:12.278  area=0  quiet
10:30:12.833  area=0  quiet   <- the frame with the crow in it
10:30:13.156  area=0  quiet
```

A background-subtraction detector cannot see a motionless animal. There is
no threshold to loosen here; the bird was not moving.

## The same thing, visible inside event 1

Event 1 shows the second failure mode beginning, in a visit the camera
otherwise caught:

```
10:32:30 .. 10:32:48   confirmed motion      (16 frames)
10:32:49 .. 10:32:58   quiet                 (9 frames, MegaDetector 0.81-0.92)
```

After about twenty seconds in one spot, the crow stopped registering as
motion at all while the detector could still see it plainly. The background
model had absorbed it.

**Consequence worth thinking about:** a bird that lands, is photographed, and
then settles down to eat becomes invisible. For "did we get the photograph"
that is fine --- we got it in the first twenty seconds. For any future attempt
to count how long an animal stayed, it is not.

## What the camera got right

**The shadow rule did not cost us a single animal frame.** `step8`'s own
comment calls the texture test "the one rule that can throw away an animal in
silence", and it is the newest and riskiest thing in the pipeline. Across the
archive:

| what the shadow rule rejected | frames |
|---|---|
| MegaDetector says empty | 1,151 |
| MegaDetector says uncertain | 130 |
| MegaDetector says person | 1 |
| **MegaDetector says animal** | **0** |

It threw away 1,151 pictures of nothing and no pictures of anything. That is
the evidence commit `3f078aa` ("Reject shadows by texture, not by shape") was
written on judgement alone and has not had until now.

## Rate limits

Of the 28 animal frames, 13 carry a `(cooldown)` suffix --- the rules wanted
the photograph and `SAVE_COOLDOWN` (2.0 s) discarded it. All 13 are inside
event 1, which was photographed 39 times regardless, so nothing was actually
lost here.

But it is worth watching. Across all 5,459 frames, **496 empty frames and 50
uncertain ones** were also wanted-then-rate-limited. On a quiet patio that is
free. On a windy day at a camera with a lower floor it is the difference
between catching the one animal and spending the cooldown on a branch.

The three-bucket split (wanted / rate-limited / rejected) exists so this
class of failure cannot be mistaken for a threshold being wrong. They need
completely different fixes.

## False positives

82 of 4,942 empty frames were photographed: **1.7%** at frame level. Another
496 were wanted and rate-limited away.

This is *not* the false-positives-per-hour figure the objective in
`evaluation-design.md` is stated in terms of. That number needs every check
the rules made, not just the instants a full-colour frame happened to exist
--- it is a 4 Hz question, and the 53,144 lores PNGs plus their CSV rows are
sitting there waiting for E2's replay to ask it.

## Night

95 frames came in below mean_luma 70, and none of them contained an animal.
Bursts refuse to record below mean_luma 40 at all, so there is barely a night
sample to judge, and no basis yet for excluding night frames from automatic
ground truth. `AUTO_TRUTH_MIN_LUMA` stays at 0.

The cameras have no infra-red illumination. Night is a hardware gap, not a
threshold to tune.

## A detour worth recording: the wrong detector

The first full pass used `md1000-spruce`, the small MegaDetector v6 variant:
14 MB against 281 MB, and **23 times faster measured here** --- nine minutes
for the archive instead of four hours. It reported **1,057 of 5,459 frames
(19%) as animals** at 0.8 or better.

They were the pile of cereal put out for the crows, scored at 0.86 in frame
after identical frame. Of those 1,057 "animals", the camera itself had called
1,008 `quiet` --- an animal that never moves, for hours, in one spot.

MDv5a finds nothing there, and is not blind: on three photographs from this
archive that certainly do contain a bird it says animal at 0.96, 0.97, 0.95.

Had that pass gone into E2 unchecked, the replay would have concluded the
camera misses 95% of what walks past it, and the next weekend would have gone
on loosening thresholds to chase a pile of Cheerios. The manifest now records
which detector saw each frame, and `report` says so out loud if it ever finds
a mix.

## What this does not establish

- **Nothing about recall, statistically.** Three events. The next campaign
  needs sites where animals actually walk past, not a suburban patio.
- **Nothing about event recall properly.** The 1-of-3 above was worked out by
  hand from the photo directory. E2's replay computes it for real.
- **Nothing about false positives per hour**, for the reason given above.
- **Nothing about night.**
- **Nothing hand-checked yet.** The 451-frame uncertain band is untouched and
  no random sample has been labelled by eye, so "how far do we trust
  MegaDetector on our own frames" remains unanswered. `trailcam sample` is
  waiting.

## What to do next

1. **Hand-label a random sample** (`trailcam sample --size 200 --seed 1`).
   Until that exists, every "MegaDetector says" in this document is on trust.
2. **Work the 451-frame uncertain band.** It is one evening, and it is the
   difference between 90% of the archive labelled and all of it.
3. **Put a camera where the animals are.** 4.8 hours of unbiased recording
   yielding three crows is the binding constraint on everything here. A
   trail edge or a creek for a week would produce more evidence than another
   month on the patio.
4. **Then E2.** Extract `motion.py`, replay these bursts, and get event
   recall and FP/hour properly.
5. **Two files need re-syncing:** `wildlifecam12/2026-09-03/training/
   train_202034_616.jpg` and `train_202044_760.jpg` will not open. Both from
   20:20 on the same evening --- probably an interrupted sync.

## Reproducing this

```bash
cd ai
.venv/bin/python -m trailcam scan
.venv/bin/python -m trailcam detect       # about four hours
.venv/bin/python -m trailcam report
```

The manifest is `ai/data/manifest.sqlite` (gitignored; rebuildable). The
numbers in this document came from `report` plus direct queries against it,
and from the cameras' own `measurements-*.csv` files for the 4 Hz detail.
