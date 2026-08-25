# Offline evaluation --- design

**Status:** design only, no code yet (2026-08-23).
**Companion to:** [`design.md`](design.md), which covers the laptop's
detect -> classify -> rank pipeline. This document covers a different question:
**how do we know whether the motion detector on the camera is any good?**

---

## The problem: a detector cannot be graded on its own photo album

Every threshold in
[`../step8_wildlife_camera.py`](../step8_wildlife_camera.py) was chosen
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

`step8 --record` sets a timer alongside the normal detection loop. Once an hour
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
      train_150000_012.png     <- what the motion rules saw, every check
      train_150000_262.png
      train_150000_512.png
      ...
      train_150000_012.jpg     <- full colour, every 10 s, for MegaDetector
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

### Two streams, because the two jobs want different pictures

The first afternoon of collecting settled a question this document had left
open, and settled it differently from either option originally listed.

1311 unbiased frames came back from a quiet patio. **99.3% of them were
`quiet`**, and within a burst each frame differed from that burst's first by a
median of **0.22% of its pixels**. Fifty-seven near-identical 682 KB
photographs an hour is a great deal of sync for very little new information.

What the **motion rules** need is the little 320x240 grey frame, because that
is literally all they ever look at. Storing the big colour one and shrinking it
offline is 23 times the bytes *and less faithful* --- it replays a JPEG of a
downscale rather than the plane the camera actually handed over. So the burst
saves the raw lores plane as PNG, unblurred, **every check** at the full loop
rate. That also closes the cadence problem below: a replay now runs the same
pipeline over the same pixels at the same 4 Hz the live loop used.

What **species identification** needs is the big colour one, and it does not
need many --- MegaDetector wants a recognisable animal, not a smooth film. So a
full-resolution colour frame lands every 10 seconds.

| | size | rate | per 30 s burst |
|---|---|---|---|
| lores 640x480 YUV420 PNG | 246 KB | every check (4 Hz) | 120 frames, 30 MB |
| full 2028x1520 colour JPEG | 686 KB | every 10 s | 3 frames, 2.1 MB |
| full 2028x1520 colour JPEG | 686 KB | **plus one whenever anything stirs**, at most one a second | 0 on a quiet scene |

Hourly bursts, daylight only, come to about **450 MB a day** on a quiet scene
and **750 MB** if something is moving through every burst --- against 6.1 GB a
day for the settings we started with.

That is per camera. Ten cameras all recording is 4.5 GB a day, which is why
[the milestones below](#milestones) say to collect in week-long campaigns on
two or three differently-sited cameras rather than continuously on the whole
fleet.

### Catching the frames that actually matter

The first afternoon produced 1311 unbiased frames and **not one animal**. That
is the real problem with a uniform sample: the quiet frames are abundant and
nearly worthless, the animal frames are rare and irreplaceable, and spending
the same bytes on both gets the ratio exactly backwards.

So a full-resolution frame is also taken the instant *anything* stirs, at a
threshold about six times below the one that keeps a photograph, capped at one
a second. On a still patio this costs nothing. When something finally walks
past, it is the difference between having a training set and having a training
set with an animal in it.

It does not bias the measurements, which is the thing to be careful about here:
the little frames keep recording every check regardless of what the rules
think, so we can still see what they missed.

### Waiting for the light

After dark the sensor hands over a black rectangle with noise in it, and
nothing in it can be identified --- not by MegaDetector, not by a Scout. Bursts
therefore check the light first and skip the slot if `mean_luma` is below
`RECORD_MIN_LUMA` (40; daylight on the patio measured about 120), retrying
every ten minutes so dawn is picked up promptly rather than up to an hour late.
A burst already running when the light goes stops there.

This is a higher bar than `MIN_MEAN_LUMA` (25), which only asks "can we tell
motion from sensor noise". Here the question is "would this picture be worth
looking at", and it deserves a stricter answer.

Night coverage is a genuine gap, not something this solves --- the AI Camera
has no infra-red illumination, so after dusk these cameras are simply blind.
Worth knowing before anyone concludes the wildlife is nocturnal.

### Why the lores frames keep their colour

Today's rules read brightness only, so the obvious thing is to store the Y plane
and nothing else. Measured on a real frame, that would save 54 KB against 70 KB
--- because YUV420 keeps the two colour planes at quarter resolution, the whole
buffer costs only **30% more than grey alone**, not three times.

Thirty percent is worth paying, because colour is the one thing that cannot be
added back later:

- **A shadow keeps its colour and only changes its brightness.** That is the
  standard way to separate a cloud crossing the patio from an animal walking
  across it --- it is what `MOG2`'s `detectShadows` does --- and cloud shadow is
  precisely the false positive we decided to live with. Stored in grey, that
  decision is permanent. Stored in YUV, it can be revisited using frames we
  have already collected.
- Green leaves against a brown animal separate far better in colour than in grey.
- Any learned model we might try later will want colour.

The file is the raw YUV420 buffer written as a lossless PNG, so it reads back as
a tall thin grey image:

```python
buf    = cv2.imread(name, cv2.IMREAD_GRAYSCALE)
grey   = buf[:240]                                  # exactly what the rules saw
colour = cv2.cvtColor(buf, cv2.COLOR_YUV2BGR_I420)  # the full frame
```

Verified round trip: the buffer reads back byte-identical, and blurring
`buf[:240]` offline produces exactly the array the live loop worked from.

### Resolution: 320x240 is a range limit, not an accuracy limit

Worth writing down, because it is not obvious. More pixels do **not** separate
animals from vegetation any better --- both scale with resolution, so the ratio
between them is unchanged:

| lores width | fern | real motion | ratio |
|---|---|---|---|
| 320 | ~95 px | ~8,960 px | 94x |
| 640 | ~380 px | ~35,840 px | 94x |
| 1280 | ~1,520 px | ~143,360 px | 94x |

What resolution does buy is reach at the small end, because the 3x3 opening
kernel is a fixed size and erases anything only a few pixels across:

| lores width | deer at 30 m | squirrel at 10 m |
|---|---|---|
| 320 | 14x9 px | 7x4 px --- marginal |
| 640 | 28x18 px | 14x8 px |

`LORES_SIZE` is therefore **640x480**: the patio is where we are testing, but
trails are the actual job, and animals there are further away.

Every other setting measured in pixels --- the blur, the two cleanup kernels,
the confirmation radius --- is scaled from the 320-wide frame they were
measured on, so changing `LORES_SIZE` moves how far the camera can see and
nothing else. A blob of the same real-world size still counts the same, because
the blob thresholds are fractions of the frame.

Replaying 1262 real burst frames at both sizes confirmed it, and turned up
something better than expected --- at 640x480 the vegetation noise is *smaller*
as a fraction of the frame, because the scaled blur and opening kernels smooth
its fine structure more effectively:

| | 320x240 | 640x480 |
|---|---|---|
| p95 blob | 0.0833% | 0.0364% |
| frames over the save floor | 11/1262 | 5/1262 |
| margin below the threshold | 2.3x | **5.4x** |

So the higher resolution buys reach *and* a quieter baseline. It costs about
3.5x the storage.

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

So the motion rules move out of `step8` into a small `motion.py` holding a
single class:

```python
detector = MotionDetector(width=320, height=240)
decision = detector.update(gray, when)     # -> "quiet" | "strong motion" | ...
```

`step8` keeps the camera, the files and the JSON, and becomes a thin wrapper
around that call. `replay.py` imports the very same file and feeds it JPEGs
instead of sensor frames. Swapping in a different algorithm then means writing a
second class with the same `update()` method --- which is also the cleanest way
to explain what an interface is for.

**Fidelity is no longer a caveat.** An earlier draft of this document worried
that the replay would shrink a saved JPEG while the camera used the sensor's own
hardware downscale, so the two would differ by exactly the compression artefacts
we are trying to measure. That is why the burst now stores the raw `lores` plane
as a lossless PNG, before the blur: the replay runs the identical pipeline over
the identical pixels.

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
   junk. Update the constants in `step8`, with the graph as the justification.
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
*Done when:* the numbers in `step8` are justified by a graph instead of by
arithmetic and good intentions.

**E4 --- Rivals.** Re-run the same data through alternatives --- MOG2 background
subtraction with shadow detection, plain frame differencing (what
`final_motion_capture.py` does), a longer confirmation window --- and compare on
the same axes.
*Done when:* we can say which algorithm is best **for our woods**, with evidence.

---

## Where the code lives

The recording lives **inside `step8`**, not in a separate script, for a hard
technical reason: only one process can hold the IMX500 camera. A separate
collector script would mean stopping the `wildlife-camera` service to run it --- so the cameras would be blind while collecting, and the
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
