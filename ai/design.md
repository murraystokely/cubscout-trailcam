# Wildlife photo triage --- design

**Status:** design only, no code yet (2026-08-14).
**Goal of this document:** capture the plan and rationale so work can pause here
and resume cold in a later session.

---

## Problem and goal

We run ~10 Raspberry Pi trail cameras (the `wildlifecam*` fleet in this repo).
Each camera is motion-triggered and writes JPEGs; the laptop pulls them with
[`../sync/sync_cameras.py`](../sync/sync_cameras.py) into a per-camera,
per-day layout:

```
~/wildlifecam-photos/
  wildlifecam1/2026-08-09/150501.jpg
  wildlifecam2/2026-08-10/073344.jpg
  ...
```

Motion triggers produce **mostly empty frames** (wind, moving shadows, plants).
Reviewing everything by hand does not scale across 10 cameras.

**Primary goal:** automatically surface the photos most likely to be a *good
animal picture*, so Murray reviews a short ranked list instead of everything.
**Secondary goal:** identify the species in each photo.

---

## Approach: detect -> classify -> rank

This is the well-established **camera-trap ML** problem, with a mature, mostly
free ecosystem built by conservation biologists. Key decision: **do not build
the detector from scratch --- stand on that ecosystem.**

Standard pipeline shape:

1. **Detection** --- "is there an animal, and where?" A generic detector draws
   boxes and filters the empties. This stage does the heavy lifting.
2. **Species classification** --- "what animal?" Run *on the cropped animal*,
   not the whole frame. Keeping it separate from detection is what makes it
   tractable: the classifier only ever sees tight animal crops.
3. **Best-photo ranking** --- a scoring layer we add on top for the "surface the
   good ones" goal. This is our differentiator over stock camera-trap tools.

---

## Recommended stack and models

### Detection --- MegaDetector (the highest-value recommendation)
- **MegaDetector** (Microsoft AI for Earth; now maintained under
  **Pytorch-Wildlife**), trained on tens of millions of trail-camera images.
- Outputs boxes in three classes: **animal / person / vehicle**, with
  confidence.
- Why it fits: built for exactly our image distribution (motion triggers, odd
  angles, distant animals, day/night). It alone solves most of the goal --- run
  it, drop high-confidence empties, and 10,000 frames become a few hundred.
- The **person/vehicle** classes let us auto-filter Scouts walking past (also a
  privacy win).
- Emits a standard JSON format that other tools consume.
- Use MegaDetector v5 (or v6 via Pytorch-Wildlife).

### Species --- SpeciesNet (default), BioCLIP (flexible)
Run only on the animal crops MegaDetector found.
- **SpeciesNet** (Google, ~2024): ensemble classifier over ~2,000 species,
  designed to pair with MegaDetector, with **geofencing** --- restrict
  predictions to species plausible in our region. Best turnkey accuracy for
  North American mammals. **Default choice.**
- **BioCLIP**: CLIP model trained on the biological tree of life. Great for
  flexible/zero-shot "what is this" and for the *educational Scout angle*
  (taxonomy from a photo). Lower precision than a dedicated classifier.
- Expect coarse labels (deer, raccoon, squirrel, bird, coyote) to work well and
  fine species to be shakier --- surface a **top-3 with confidences**, not a
  single hard label.

### Best-photo ranking --- cheap signals, no extra model
Define a per-detection quality score from inexpensive metrics:
- **detection confidence** (animal really there)
- **subject size** --- box area / frame area (reject distant specks)
- **wholeness** --- box not clipped at the frame edge (animal fully in shot)
- **sharpness** --- variance-of-Laplacian (OpenCV) on the crop (reject motion
  blur)
- **exposure** --- reject too-dark / blown-out crops (dawn/dusk)
- optional: centeredness, single-clear-subject

Combine into a weighted `final_score`, then **deduplicate bursts**: trailcams
fire 3-shot sequences, so group by camera + close timestamps and keep the best
frame per event. Output a **ranked shortlist per camera / species / day**.

### Review / output
- Natural end product: a **static HTML gallery** of the ranked shortlist
  (thumbnails + species label + score), which fits the existing nginx habit.
- Heavier annotation/correction: **Timelapse** (standard camera-trap review app)
  ingests MegaDetector JSON directly.

---

## Architecture

### Directory layout
This `ai/` directory, sibling to `sync/` and `clone/`. Proposed package
`trailcam` (names are a bikeshed):

```
ai/
  design.md                 # this file
  README.md                 # (later) usage
  pyproject.toml            # deps: pytorch-wildlife, pillow, opencv, exif, jinja2, ...
  trailcam/
    __init__.py
    config.py               # paths, thresholds, region/geofence, score weights
    manifest.py             # SQLite schema + read/write helpers  (the spine)
    ingest.py               # walk sync output -> image rows (camera, timestamp, EXIF)
    detect.py               # MegaDetector wrapper -> detection rows + crops
    classify.py             # species classifier on crops -> species rows
    quality.py              # sharpness / exposure / size / wholeness metrics
    sequence.py             # burst grouping + "best of event" dedupe
    rank.py                 # combine metrics -> final_score, shortlist queries
    gallery.py              # render static HTML gallery from the manifest
    cli.py                  # subcommands: ingest | detect | classify | score | gallery | run
  models/                   # downloaded weights            (gitignored)
  data/                     # manifest.sqlite, crops/, thumbs/, gallery/  (gitignored)
  tests/
    fixtures/               # sample images: animal, empty, person, blurry
```

### Data contract --- SQLite is the spine
Every stage reads and writes one SQLite DB. This is the most important design
choice: each stage becomes **independent, idempotent, and resumable** (crucial
for multi-hour overnight CPU runs --- a crash resumes where it stopped).

Conceptual tables:
- **images** --- `id, camera, path, captured_at, width, height, mean_luma, event_id`
- **detections** --- `id, image_id, class{animal,person,vehicle}, confidence, bbox, crop_path`
- **species** --- `detection_id, label, confidence, rank`  (top-3 per detection)
- **events** --- `id, camera, start_ts, end_ts`  (a burst / motion trigger)
- **scores** --- `image_id, q_sharpness, q_size, q_wholeness, q_exposure, det_conf, final_score, is_best_of_event`

Originals are **read-only**; everything derived (crops, thumbs, gallery, DB)
lands in `ai/data/`.

### Module responsibilities

| Module | Input | Output | Notes |
|---|---|---|---|
| `ingest` | sync dir `~/wildlifecam-photos/<cam>/<date>/*.jpg` | `images` rows | camera+date from path; `captured_at` from EXIF `DateTimeOriginal`, fallback mtime |
| `detect` | `images` | `detections` + crops | MegaDetector; skips already-detected images |
| `quality` | crops / images | quality metrics | pure OpenCV (variance-of-Laplacian, luma, bbox geometry); no ML |
| `sequence` | `images` | `events`, `is_best_of_event` | group by camera + timestamp gap; pick best frame |
| `classify` | animal crops | `species` rows | SpeciesNet geofenced; only runs on surviving crops |
| `rank` | metrics + detections | `scores.final_score` | config-weighted combination; shortlist queries |
| `gallery` | manifest | `ai/data/gallery/` HTML | thumbnails, label, score; grouped by camera/species/day |
| `cli` | --- | orchestration | each stage a subcommand; `run` chains them |

### Design principles
- **Detector/classifier behind a thin interface** --- swap MegaDetector v5<->v6
  or SpeciesNet<->BioCLIP without touching the pipeline.
- **All thresholds and score weights live in `config.py`** --- tuning never
  means editing logic.
- **Read-only w.r.t. the photo library** --- never modify originals.
- **Every stage idempotent and resumable** --- skip already-processed rows.

---

## Compute constraints
- The laptop (ThinkPad) has **no discrete NVIDIA GPU** --- plan for CPU batch
  runs (MegaDetector overnight; tens of thousands of images is a few hours).
  Consider **ONNX Runtime** for a speed bump.
- For very large batches or fast turnaround, a **cloud GPU or Google Colab** for
  the detection pass is a reasonable escape hatch.
- Species classification is cheap --- it only runs on the (few) crops that
  survive detection.

---

## Milestones

Each is independently useful and leaves something working.

**M0 --- Skeleton + ingest (no ML).**
`trailcam ingest` walks the sync output and builds the manifest with per-image
metadata.
*Done when:* image counts per camera/day match files on disk; timestamps look
right. Gives a queryable index immediately.

**M1 --- Detection + gallery (the keystone).**
`detect` (MegaDetector) -> `quality` -> `sequence` (dedupe) -> `gallery`,
ranked by `final_score`.
*Done when:* on **one camera**, empties are filtered and the gallery surfaces
the good animal shots first, bursts collapsed to one frame. **This milestone
alone delivers the core goal**; everything after is refinement.

**M2 --- Evaluation + threshold tuning.**
A small labeling helper to hand-tag a few hundred images (animal / empty /
person), plus a precision/recall report for "has animal".
*Done when:* measured numbers justify the detector-confidence threshold and
score weights instead of guessing. Skipping this is why people distrust output.

**M3 --- Species classification.**
`classify` (SpeciesNet, geofenced) on surviving crops; gallery gains per-species
grouping/filtering and top-3 labels.
*Done when:* spot-checks on the M2 labeled set look sane and the geofence rules
out implausible species.

**M4 --- Scale + ergonomics.**
Multi-camera batch, one-shot `trailcam run`, resumable overnight runs, ONNX/perf
pass, saved queries ("best 20 deer from wildlifecam3 this week"). Optional
Colab/cloud-GPU path for large batches.
*Done when:* a full 10-camera campout processes overnight into one ranked
shortlist.

**M5 --- Active learning + local fine-tune (later/optional).**
Use accumulated confirmed labels to fine-tune a species classifier on *our*
local species; a review queue that prioritizes uncertain ones.
*Done when:* local accuracy beats the off-the-shelf classifier on our cameras.
Only worth it after a season of labels.

**Critical path:** M0 -> **M1** -> M2 -> M3, folding M4 in once M1 proves out.
M1 is maximum payoff for minimum code --- mostly glue around MegaDetector plus a
Jinja gallery.

---

## Gotchas / risks to design around
- **Empty-frame dominance** --- the whole reason MegaDetector exists; do not skip
  detection.
- **Night shots** --- Camera Module 3 has **no IR** illumination, so after-dark
  frames may be black/useless. Add an exposure pre-filter; expect this to be
  largely a **daylight** system.
- **Small / distant / partial animals** --- handled by the size + wholeness
  scores.
- **Domain/region shift** --- always geofence the species model; a raw global
  classifier hallucinates exotic species.
- **Evaluation** --- hand-label a small ground-truth set (M2) to tune thresholds
  rather than guess.
- **People/privacy** --- MegaDetector's person class lets us exclude Scout
  selfies automatically.

---

## Open questions to revisit next session
- Region for the species geofence (Murray's TZ is US Pacific --- confirm exact
  area).
- Rough per-campout image volume (drives CPU-vs-cloud decision).
- Whether to adopt **Pytorch-Wildlife** as the batteries-included backbone (MD +
  classifiers in one pip install) vs. standalone **MegaDetector + SpeciesNet**.
- Final `final_score` weight formula (draft in `config.py` at start of M1).
- Gallery vs. Timelapse for the review step (can do both).

---

## How to resume
Start at **M0/M1**. First real code: `manifest.py` (schema) + `ingest.py`, then
`detect.py` around MegaDetector, then `quality.py` + `sequence.py` +
`gallery.py`. Keep thresholds/weights in `config.py`. Everything hangs off the
SQLite manifest, so build that first.
