# The laptop side: MegaDetector over the training bursts

The cameras in the woods decide what to photograph with a few hundred lines
of arithmetic. This directory is the workshop behind that decision.

Two design documents set out what gets built here and why:

- [`design.md`](design.md) --- the detect -> classify -> rank pipeline that
  surfaces the good animal pictures out of a campout's photographs.
- [`evaluation-design.md`](evaluation-design.md) --- how we find out whether
  the motion detector on the camera is any good.

What is implemented so far is **milestone E1, Label**, from the second
document: run a research-grade detector over the unbiased training bursts,
so that every frame gets a verdict better than the camera's own.

Nothing else from either document exists yet. No species classifier, no
ranking, no gallery, no replay harness.

## Why this stage comes first

The photographs the cameras keep cannot be used to grade the cameras:

> If the detector ignores every distant fox, no photograph of a distant fox
> ever reaches the laptop, and the photo album looks flawless.

So `step8 --record` also saves **training bursts** --- runs of frames with
the motion rules switched off --- and writes a row into
`measurements-<camera>.csv` for every one of them saying what the rules
*would* have decided. Those frames are an unbiased sample. All that is
missing is a trustworthy answer to "was there actually an animal in it",
and hand-labelling tens of thousands of frames is not happening.

MegaDetector is that answer. It was trained on tens of millions of
camera-trap photographs and it answers exactly one question --- animal,
person or vehicle, and where. The camera in the woods runs a 320x320 SSD
MobileNet that has never heard of a raccoon; measured over this archive it
reported 9,254 `bench` detections and 137 `bird`.

## Install

`megadetector` brings torch with it, which is most of a gigabyte. Install
the CPU build of torch first, or pip fetches the CUDA one and spends 2.5 GB
on a laptop with no NVIDIA card:

```bash
cd ai
python3 -m venv .venv
.venv/bin/pip install torch torchvision \
    --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install megadetector
```

The model weights (~280 MB for MDv5a) download themselves on first use, into
`ai/models/`. Both that and `ai/data/` are gitignored: everything in them
can be rebuilt from the photo library.

## Run it

```bash
cd ai
.venv/bin/python -m trailcam scan                # index the bursts on disk
.venv/bin/python -m trailcam detect --limit 50   # try it on fifty frames
.venv/bin/python -m trailcam detect              # the real pass
.venv/bin/python -m trailcam report              # what it found
```

`scan` is cheap and safe to rerun after every sync; it adds rows for frames
that arrived since last time and touches nothing else.

`detect` is the expensive one. Measured on this laptop (8-core Core Ultra 7,
CPU only, 7 threads), MDv5a runs at **2.75 seconds a frame** --- so the 5,459
training frames in the archive today are about **4 hours**. It is built to be
interrupted: the work queue is "frames with no result yet", results are
committed as they finish, and Ctrl-C stops cleanly between frames. Rerunning
it resumes. Leave it overnight, or narrow it with `--camera` / `--day` /
`--limit`.

## What comes out

One SQLite database, `ai/data/manifest.sqlite`, holding every verdict
anything has reached about every frame --- the camera's in the woods, each
detector's on the laptop, and each person's.

```
sqlite3 data/manifest.sqlite \
  "SELECT camera_decision, label, COUNT(*) FROM truth GROUP BY 1, 2"
```

### The data model

Five tables, and the split between the first two is the whole design:

```
frames          one row per file.  Nothing any algorithm decided --
                camera, day, path, when it was taken, how bright it is.

runs            one row per thing-that-looked-at-frames, per execution:
                  camera    what step8 decided in the woods, one run per
                            (camera, code version)
                  detector  a pass of MegaDetector on the laptop
                  replay    E2/E3: motion.py offline at some thresholds
                with its parameters, weights, host, and when it ran.

frame_results   one row per frame per run.  UNIQUE(run_id, frame_id).

detections      one row per box, hanging off a frame_result.

labels          what a person saw.  Reachable by no run.
```

Four things follow from that shape, and each is why a table is as it is:

**A run's queue is the frames it has no row for.** Resume is rerun, per
run, so two models can be part-finished at once and neither disturbs the
other. `--new-run` starts a fresh one instead of continuing.

**"We looked and found nothing" is a `frame_results` row with no
detections; "we have not looked" is no row at all.** In one table those are
indistinguishable, and confusing them turns unprocessed frames into
confident empties --- which is exactly the bug that would silently wreck a
recall number.

**The camera is a run too.** Not a flourish: this archive holds **six
deployments**, and wildlifecam4 alone ran three different versions of step8
in three weeks. A single `camera_decision` column would average three
algorithms together and never say so. `report` breaks the comparison down
per deployment, which is how you find out whether a change to the rules
actually helped.

**Which run counts as ground truth is a policy, not a fact.** One run
carries `role = 'reference'`; the `truth` view reads it, and
`trailcam reference <id>` switches it --- one UPDATE, nothing recomputed,
every other run still there to compare against.

```bash
.venv/bin/python -m trailcam runs           # every run and its coverage
.venv/bin/python -m trailcam reference 3    # adopt run 3 as ground truth
.venv/bin/python -m trailcam compare 1 3    # where two runs disagree
```

`compare` is milestone E4 in one command. Run it on MDv5a against the fast
v6 variant and the Cheerios story below comes straight back out as a
table: 117 frames seen by both, 34.2% agreement.

An older single-model manifest is migrated automatically on first open ---
detector results move across intact, and the camera's decisions are rebuilt
by the next `scan`, which is where the version fingerprints come from.

`report` prints the same thing in a readable form, in four parts:

1. **Coverage** --- how much of the archive has been through the detector.
2. **The confidence split** --- frames MegaDetector is sure about get their
   label for free; the muddy middle gets listed for a person. The threshold
   pair that decides this (`ANIMAL_TRUTH`, `EMPTY_TRUTH`) is the most
   important thing in [`trailcam/config.py`](trailcam/config.py).
3. **Camera against detector** --- the miss rate and the false-positive
   rate of the rules running in the woods, per frame.
4. **By light level** --- the same, split day against dusk, because a
   number averaged over both describes neither.

Frame-level rates are not the real metric: a deer present in twenty frames
that we photographed three times is a complete success, not a 15% score.
Event recall is E2's number and needs the replay harness. What `report`
gives is the honest first look --- enough to tell whether the thresholds in
`step8` are in the right postcode.

## What happened the first time we ran it

Worth recording, because it is the argument for everything in the section
below.

The first full pass used `md1000-spruce`, the small MegaDetector v6 variant
--- 14 MB against 281 MB, and **23 times faster measured here**: nine
minutes for the whole archive instead of four hours. It reported **1,057 of
5,459 frames (19%) as containing an animal** at 0.8 or better.

They were not animals. The crops made it obvious in one glance: the model
was scoring the **pile of cereal put out for the crows** at 0.86, in frame
after identical frame. Of those 1,057 "animal" frames, the camera itself had
called 1,008 of them `quiet` --- an animal that never moves, for hours, in
the same spot.

MDv5a, over a sample of the same frames, reports **nothing at all**. It is
not that MDv5a is blind: on three photographs from this archive that
certainly do contain a bird --- the calling crow of 24 August, the crow in
profile beside it, and the junco of the 26th --- it says animal at **0.96,
0.97 and 0.95**.

Two things follow, and both are now in the code:

- `config.DETECTOR` stays on **MDV5A**. The fast variant is for getting an
  answer in ten minutes, not for anything we would quote.
- The manifest records **which detector saw each frame**, and `report` says
  so out loud when it finds more than one. A ground truth assembled half
  from one model and half from another is worse than no ground truth.

Had this pass gone straight into E2 unchecked, the replay would have
concluded that the camera misses 95% of the animals in front of it --- and
somebody would have spent a weekend loosening thresholds to chase a pile of
Cheerios.

## Checking the checker

MegaDetector is not an oracle. It is weakest on small distant animals and on
night frames, which are our hard cases exactly.

```bash
.venv/bin/python -m trailcam sample --size 200 --seed 1
.venv/bin/python -m trailcam label train_103415_876.jpg animal
```

`sample` picks frames at random --- not the interesting-looking ones, which
could not measure anything --- and prints them for a person to judge.
`label` records what that person saw. Hand labels always win over the
detector's, and rerunning `detect` cannot overwrite them.

This is the genuinely good Scout activity in the whole plan: label a few
hundred pictures by eye, then see how a research-grade model compares
against you. Until that comparison exists, `AUTO_TRUTH_MIN_LUMA` in
`config.py` stays at 0 --- we exclude no frames from automatic labelling,
because excluding them on a hunch would be indistinguishable, six months
from now, from excluding them on evidence.

## Getting the results into a review tool

```bash
.venv/bin/python -m trailcam export data/megadetector.json
```

Writes MegaDetector's own JSON format, which
[Timelapse](https://saul.cpsc.ucalgary.ca/timelapse/) --- the standard
camera-trap review application --- ingests directly. Worth knowing about
before writing any labelling UI of our own.

## Benchmarking a machine

The archive grows by roughly 300 frames a day per camera, and a pass takes
four hours on this laptop. Which machine should be doing that --- and does
moving it somewhere else change the answers?

```bash
.venv/bin/python -m trailcam bench build           # a fixed 250-frame corpus
.venv/bin/python -m trailcam bench run --device cpu
.venv/bin/python -m trailcam bench report          # every machine, in a table
```

It measures the pass we actually run (decode, letterbox, forward, NMS, one
image at a time), verifies the corpus by sha256 before timing anything,
discards a warmup, repeats until the machine has had time to get hot, and
records what the model *found* as well as how long it took --- so two
machines can be compared on agreement, not just on seconds.

[`results/benchmarks/`](results/benchmarks/) has the results, the rules,
and what to install on a Mac or a CUDA box.

## Results

Measured findings go in [`results/`](results/), one file per analysis, named
by date. The first one --- [what the pass found on 12 September
2026](results/2026-09-12-first-megadetector-pass.md) --- covers 5,459 frames
across three cameras, and is where the Cheerios story above is written down
properly.

## Layout

```
ai/
  trailcam/
    config.py      every path and threshold, each with its reason
    manifest.py    the SQLite schema; the spine everything hangs off
    bursts.py      finding the frames, and joining the camera's own verdict
    detector.py    MegaDetector behind a thin interface
    detect.py      the pass: resumable, interruptible, one frame at a time
    report.py      the confidence split and the comparison
    cli.py         scan | detect | report | sample | label | export | status
  results/         measured findings, one file per analysis, by date
  models/          downloaded weights            (gitignored)
  data/            manifest.sqlite, crops/       (gitignored)
```

The photo library is **read-only** to everything here. Nothing in this
package writes inside `~/wildlifecam-photos`.
