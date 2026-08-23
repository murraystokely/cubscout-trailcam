# Offline evaluation --- design

**Status:** design only, no code yet (2026-08-23).
**Companion to:** [`design.md`](design.md), which covers the laptop's
detect -> classify -> rank pipeline. This document covers a different question:
**how do we know whether the motion detector on the camera is any good?**

---

## The problem: a detector cannot be graded on its own photo album

Every threshold in
[`../step7_ai_motion_detection.py`](../step7_ai_motion_detection.py) was chosen
from geometry (how many pixels a deer covers at 30 m), from synthetic test
frames, and from judgement. Not one of them was chosen by measuring a real
camera in real woods. That is the honest situation today.

The obvious way to check them is to look at the photographs the cameras saved.
That does not work, and the reason is worth being precise about:

> If the detector ignores every distant fox, no photograph of a distant fox
> ever reaches the laptop, and the photo album looks flawless.

The saved photographs are a **biased sample** --- they are exactly the frames
that passed the rules we are trying to test. They can tell us about false
positives (pictures of nothing) but they can say **nothing at all** about
misses, and misses are the failure we actually care about.

To measure misses we need frames the detector was **not allowed to filter**.

---

## What we collect: unbiased training bursts

`step7 --record` sets a timer alongside the normal detection loop. Once an hour
it records a plain run of frames with the motion rules switched off, then goes
back to sleep. Detection keeps running the entire time --- the burst is extra
work, not a different mode.

```
/var/www/html/photos/
  2026-08-23/
    141530.jpg                 <- a wildlife photograph the rules chose
    141530_annotated.jpg       <- the same frame with the boxes drawn on
    141530.json
    training/
      train_150000_012.jpg     <- a burst frame, chosen by nobody
      train_150001_014.jpg
      train_150002_011.jpg
      ...
    measurements-wildlifecam3.csv
```

Four decisions in that layout, each for a reason:

**Bursts, not scattered single frames.** Motion detection is not a function of
one image. The background model has a memory, and the confirmation rule asks
"was it still there a quarter second later". Neither question can be asked of an
isolated frame. A contiguous run is a little silent film we can replay through
any algorithm we like.

**Once an hour, all day.** A week of hourly bursts samples dawn glare, midday
contrast, dusk, full dark, still air, wind, rain and cloud. Thresholds that only
work at noon are exactly the failure mode we are trying to catch.

**Milliseconds in the filename.** The replay tool derives the real interval
between frames from the names, rather than assuming a cadence. If the Pi is busy
and a frame lands late, the replay knows.

**Their own subdirectory.** `sync_cameras.py` copies everything with `rsync -a`,
so these arrive on the laptop automatically. But `ingest` in
[`design.md`](design.md) walks `<camera>/<date>/*.jpg`, and training frames must
**not** become rows in the wildlife manifest --- they are test data, not
sightings. The subdirectory is what keeps them out.

**Two filters `ingest` will need.** For the same reason, it has to skip the
`training/` subdirectory *and* anything ending in `_annotated.jpg`. Those
annotated copies are how we see why the camera made a decision, so they are
saved for every event and they sit right next to the originals --- which means a
naive `*.jpg` walk would count every sighting twice and then run MegaDetector
over frames that already have boxes painted on them.

### The baseline comes for free

Every training frame also gets a row in `measurements-<camera>.csv` recording
what the **current** rules decided about it: blob area, changed fraction, extent,
aspect, brightness shift, adaptive threshold, and the decision itself
(`quiet`, `waiting for confirmation`, `strong motion`, ...).

That is the single most useful thing in the whole scheme. It means the moment a
burst lands on the laptop we already know what the camera in the woods *would*
have done with frames it was not allowed to filter --- which is precisely the
number we could never get before.

### Open question: burst cadence vs loop cadence

The detector checks four times a second (`LOOP_DELAY = 0.25`). Bursts record
once a second (`RECORD_INTERVAL = 1.0`). These do not match, and it matters:
replaying a 1 Hz burst exercises `CONFIRM_CHECKS` and `BACKGROUND_TAU` at a
quarter of their real speed, so any conclusion about the *confirmation* rules
would be wrong.

Two honest options, and we should probably do both:

| Setting | Covers | Good for |
|---|---|---|
| `RECORD_INTERVAL = 1.0`, `RECORD_LENGTH = 60` | 60 s of real time | Does an animal appear at all? Threshold sweeps on blob size and shape. |
| `RECORD_INTERVAL = 0.25`, `RECORD_LENGTH = 15` | 15 s, faithful | Confirmation rules, background dynamics --- a true replay of the live loop. |

Start with the 1 Hz form, because coverage of real time is what gets animals
into the dataset at all. Switch a camera or two to the faithful form once we
start tuning `CONFIRM_CHECKS`.

### Storage budget

At roughly 450 KB per 2028x1520 JPEG, 60 frames an hour:

| | frames/day | per day |
|---|---|---|
| one camera | 1,440 | 0.63 GB |
| ten cameras | 14,400 | 6.3 GB |
| ten cameras, one week | 100,800 | **44 GB** |

That is real money on SD cards. Mitigations, in the order we should reach for
them:

1. **Collect in campaigns, not continuously.** One week of bursts is a dataset;
   we do not need bursts forever. Turn `--record` off again afterwards.
2. **Two or three cameras at a time**, sited differently (meadow, under trees,
   trail edge) rather than all ten.
3. **Daylight only**, if night frames turn out to be uniformly black.
4. Only then consider recording bursts at reduced resolution --- and note this
   costs fidelity, since the detector's own downscale would no longer match.

The camera already refuses to write training frames when the disk guard trips,
so a forgotten `--record` cannot fill a card.

---

## Ground truth: let MegaDetector do the labelling

A dataset with no labels is just a pile of pictures. Hand-labelling 100,000
frames is not happening.

The insight is that **we already plan to run a far better detector on the
laptop**. MegaDetector was trained on tens of millions of camera-trap images; the
Pi is running a 320x320 SSD MobileNet that has never heard of a raccoon. So:

> Run MegaDetector over every training frame. Its animal/person/vehicle boxes
> become the ground truth against which we grade the little detector.

This costs nothing we were not already going to spend, and it is the whole
reason the two design documents belong together.

Caveats worth stating plainly:

- **MegaDetector is not an oracle.** It is very good on this image distribution,
  not perfect, and it is weakest on exactly our hard cases: small distant
  animals, and night frames.
- **Split by confidence.** Treat high-confidence detections (> 0.8) and confident
  empties as truth. Everything in the uncertain middle band gets hand-labelled.
  That is a few hundred frames, not a hundred thousand.
- **Check the checker.** Hand-label a random sample and measure how often
  MegaDetector agrees. If agreement is poor on night frames, we exclude night
  frames from the automatic ground truth rather than quietly trusting them.

That last step is a genuinely good Scout activity: label a few hundred pictures
by eye, then see how a research-grade model compares against you. It teaches
what "ground truth" means and why nobody should take a model's word for it.

---

## The replay harness

```
ai/
  evaluation-design.md          # this file
  trailcam/
    motion.py                   # THE algorithm, imported by both sides
    replay.py                   # feed a burst through motion.py
    evaluate.py                 # compare decisions against ground truth
    sweep.py                    # vary one threshold, plot the curve
```

### One copy of the algorithm, imported twice

The most important structural decision here, and the easiest one to get wrong:
**the camera and the test program must run the same code.** If `replay.py`
contains its own copy of the motion rules, the two drift within a month and the
evaluation quietly starts measuring an algorithm that is not deployed.

So the motion rules move out of `step7` into a small `motion.py` holding a
single class:

```python
detector = MotionDetector(width=320, height=240)
decision = detector.update(gray, when)     # -> "quiet" | "strong motion" | ...
```

`step7` keeps the camera, the files and the JSON, and becomes a thin wrapper
around that call. `replay.py` imports the very same file and feeds it JPEGs
instead of sensor frames. Swapping in a different algorithm then means writing a
second class with the same `update()` method --- which is also the cleanest way
to explain what an interface is for.

**Fidelity caveat:** the camera's greyscale comes from the sensor's own hardware
downscale (the `lores` Y plane); the replay gets it by shrinking a saved JPEG.
They are close but not identical, and JPEG compression adds artefacts the live
path never sees. If this turns out to matter, the fix is to also save the lores
plane as a small PNG beside each training frame.

---

## Metrics that actually matter

The obvious metric --- "what fraction of frames containing an animal did we
save?" --- is the wrong one for a trail camera. Three better ones:

**1. Event recall (the headline number).** Group consecutive frames containing
the same animal into an *event*. If a deer is present in twenty frames and we
saved three of them, that is a complete success: we got the photograph. Score
events, not frames.

```
event recall = events where we saved >= 1 frame / events containing an animal
```

**2. False positives per hour.** Not precision --- *rate*. This is the number
that decides whether a 32 GB card lasts a week or a day, and it is the honest
cost of a loose threshold.

**3. Miss profile.** *Which* events did we lose? Broken out by distance (blob
size), light level, and camera. "We catch 95% in the open and 40% under the
trees" is an actionable finding; a single averaged number is not.

### The objective, stated once

We have decided that **false positives are cheap and misses are expensive** ---
the laptop's MegaDetector pass exists precisely to throw away junk, and a
photograph never taken can never be recovered. So the target is:

> Maximise event recall, subject to false positives staying under a budget of
> roughly 20 per hour per camera.

Any threshold change gets judged against that sentence, which is why it is
written down rather than left implicit.

Report everything split by light condition (day / dusk / night) and per camera.
A threshold that is right for a camera in an open meadow will be wrong for one
pointed into moving branches, and a single fleet-wide number hides that.

---

## The tuning loop

1. **Collect** --- two or three differently-sited cameras run `--record` for a
   week.
2. **Label** --- MegaDetector over every training frame; hand-label the
   uncertain band.
3. **Baseline** --- replay the current rules; write down event recall and
   FP/hour. *This number is the thing we have never had.*
4. **Sweep** --- vary one constant at a time (`MIN_BLOB_AREA`,
   `CONFIRM_CHECKS`, `MAX_ASPECT`, `LIGHTING_SHIFT`) and plot recall against
   FP/hour.
5. **Choose the knee** --- the point past which more recall costs a lot more
   junk. Update the constants in `step7`, with the graph as the justification.
6. **Keep collecting.** Autumn leaves fall, snow arrives, the sun moves. A
   threshold tuned in August is a hypothesis about September.

Step 4 is the part worth doing carefully: **one constant at a time**, so we can
see what each one actually buys. Sweeping several at once produces a number
nobody can explain.

---

## Milestones

**E0 --- Collect.** `--record` runs on two cameras for a week; bursts arrive on
the laptop via the existing sync.
*Done when:* the training frames are on the laptop and the per-frame CSV rows
line up with them.

**E1 --- Label.** MegaDetector over the bursts; a confidence split; a few hundred
hand-labels for the uncertain band and for checking the checker.
*Done when:* every training frame has a label, and we know how far to trust the
automatic ones.

**E2 --- Baseline (the keystone).** `motion.py` extracted, `replay.py` written,
current rules replayed against the labels.
*Done when:* we can state the event recall and FP/hour of the code that is
running in the woods right now. **Everything before this is preparation and
everything after is refinement.**

**E3 --- Sweep.** One curve per constant.
*Done when:* the numbers in `step7` are justified by a graph instead of by
arithmetic and good intentions.

**E4 --- Rivals.** Re-run the same data through alternatives --- MOG2 background
subtraction with shadow detection, plain frame differencing (what
`final_motion_capture.py` does), a longer confirmation window --- and compare on
the same axes.
*Done when:* we can say which algorithm is best **for our woods**, with evidence.

---

## Where the code lives, and why there is no step8

The recording lives **inside `step7`**, not in a separate script, for a hard
technical reason: only one process can hold the IMX500 camera. A separate
`step8_collect_training_data.py` would mean stopping the `wildlife-camera`
service to run it --- so the cameras would be blind while collecting, and the
training data would be gathered under different conditions from the deployment
it is meant to describe. Building it in costs about forty lines: a timer, a
filename, and one extra branch.

Everything else --- replay, labelling, metrics, sweeps --- runs on the laptop
next to the pipeline in [`design.md`](design.md), shares its SQLite manifest, and
belongs in `ai/`. It is not a step in the Scout tutorial; it is the workshop
behind it.

That said, a small `step8_replay_a_burst.py` would be a genuinely good teaching
artifact: point it at one burst, watch the algorithm's decisions scroll past
next to what MegaDetector saw, and see for yourself which ones it got wrong.
Worth writing once E2 exists and there is something real to replay.
