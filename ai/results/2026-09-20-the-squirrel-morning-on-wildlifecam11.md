# The squirrel morning on wildlifecam11

**Run:** 20 September 2026. Run 19 in the ThinkPad's manifest --- a fresh run
over one camera's two new days, not an extension of the archive-wide run 9,
which lives on the Mac Studio.
**Machine:** ThinkPad (Core Ultra 7 258V), CPU only, 7 threads, reading the
archive over CIFS from `/mnt/datasets/trailcam/photos/`.
**Detector:** MegaDetector v5a.
**Code:** `trailcam` at `8dcb9c4`; the photographs were written by
`step8_reject_shadows.py` at `797880a7b017`.
**Ranked list:** [`shortlists/2026-09-20-mdv5a-run19.csv`](shortlists/2026-09-20-mdv5a-run19.csv);
the gallery is on the laptop at `ai/data/shortlist/index.html`.

wildlifecam11's card came off the camera this morning, after the 10,000 mAh
Anker pack under it went flat at 10:02. It held 633 photographs over two
days, and they divide almost perfectly in half: a day on which the camera
photographed nothing at all, and a single morning of squirrels.

---

## The cost

| | frames | time | s/frame |
|---|---|---|---|
| run 19 | **633** (photographs only) | **24.5 min** | 2.31 |

**That 2.31 is not a benchmark number, and must not be quoted as this
machine's throughput.** Two reasons, both disqualifying on their own:

- **The first frames ran on battery.** The laptop was unplugged, on the
  `powersave` governor, with a browser competing for the CPU. Frames 1--25
  took **6.67 s** each; mains power arrived during frames 26--50, which
  averaged 4.44 s; frames 51--633 settled at **2.01 s/frame**. The whole-run
  average is a blend of two machines.
- **These are not the bench corpus.** They are 1520x1140 frames from an
  IMX500, not the frozen set in [`benchmarks/`](benchmarks/), so nothing
  here is comparable to the numbers in that directory by design.

Recorded anyway, because the conditions are recorded with it --- and because
the battery penalty is a useful measurement in its own right: **3.3x** on
the first 25 frames, against a machine that was otherwise idle a few minutes
later.

## What arrived

Two days, one camera, no unreadable frames:

| day | photographs | animal (>= 0.8) | person (>= 0.8) | uncertain | empty |
|---|---|---|---|---|---|
| 2026-09-19 | 450 | 14 | 3 | 25 | **408** |
| 2026-09-20 | 183 | **107** | 0 | 72 | 4 |
| both | 633 | 121 | 3 | 97 | 412 |

The 121 animal photographs hold **133 animal boxes** at 0.8 --- twelve frames
have two squirrels in them at once.

## The two days are not alike, and the difference is the point

Same camera, same position, same build, twenty-four hours apart:

| hour | 19 Sep photographs | animals | | hour | 20 Sep photographs | animals |
|---|---:|---:|---|---|---:|---:|
| 10 | 65 | 3 | | 09 | 182 | **106** |
| 11 | 3 | 1 | | 10 | 1 | 1 |
| 12 | 22 | 7 | | | | |
| 13 | 14 | 0 | | | | |
| 14 | 10 | 0 | | | | |
| 15 | 37 | 2 | | | | |
| **16** | **240** | **0** | | | | |
| 17 | 56 | 0 | | | | |
| 18 | 3 | 1 | | | | |

**The 16:00 hour on the 19th took 240 photographs and MegaDetector found an
animal in none of them.** 239 of the 240 are empty at 0.3. The 17:00 hour
adds 56 more, 55 of them empty. Between them those two hours are **296 of
that day's 450 photographs, with one animal frame in the lot** --- and the
camera logged every one of them as `confirmed motion`, its strongest verdict
short of the AI hint.

Whatever the camera was photographing between 16:00 and 18:00 was not an
animal MegaDetector can see. Low afternoon sun through leaves is the obvious
suspect, and it is exactly the case the shadow rules in `step8` exist for.
The `measurements-wildlifecam11.csv` for that day is on the NAS beside the
photographs, and it holds a row for every motion check made in those hours,
decided and rejected alike. That file, not this one, is where the question
gets settled.

The 20th is the mirror image: **182 photographs in the 09:00 hour, 106 of
them animals, only 4 empty.** The camera was right almost every time it
fired.

## What the animals are

**Grey squirrels.** Eight of the seventeen visits were opened as crops ---
ranks 1--5, 9, 13 and 17 --- and every one of them is a squirrel: on the
ground in leaf litter, and repeatedly running up and down a tree trunk set
against a blue-painted wall. One frame has a squirrel climbing the trunk and
a second on the ground at its foot. The other nine visits were not opened,
so "all of them are squirrels" is an inference from eight, not a count of
seventeen.

Nothing else appeared in those eight --- no crow, no small brown bird. The
September pass already had wildlifecam11 down as a leaf-litter position
where the squirrels rank alongside the close crows, so this reads as that
position doing what it was moved there to do.

Nor, on the evidence of the single crop opened from it, is anything hiding
in the uncertain band: the 97 frames between 0.3 and 0.8 top out just under the
line, and the highest of them is the same squirrel, smaller. That is the
band the September pass already described as holding real squirrels the 0.8
line leaves out. It was not worked through frame by frame here.

## The best pictures

Ranking unchanged in kind: confidence x size x wholeness x sharpness, one
frame per visit. Descriptions are from looking at the crops; ranks not
described here were not opened.

| # | score | conf | frame | what |
|---|---|---|---|---|
| **1** | 0.70 | 0.91 | `2026-09-19/105820.jpg` | squirrel in leaf litter, tail up, something pale held to its mouth. 1.8% of the frame, the largest unclipped animal of the set |
| 2 | 0.61 | 0.87 | `2026-09-20/095039.jpg` | squirrel foraging on bare soil, tail extended --- the pick of a **53-frame** visit |
| **3** | 0.61 | 0.89 | `2026-09-20/094240.jpg` | **two squirrels**: one climbing the trunk, one on the ground below it |
| 4 | 0.55 | 0.85 | `2026-09-19/181050.jpg` | squirrel sitting upright in leaf litter, at dusk |
| 5 | 0.54 | 0.88 | `2026-09-20/092818.jpg` | squirrel on the trunk against the blue wall, tail curled over its back |
| 6 | 0.53 | 0.88 | `2026-09-20/100032.jpg` | the **last photograph of the run**, two minutes before the pack died |
| 9 | 0.50 | 0.87 | `2026-09-19/150818.jpg` | squirrel on the trunk, mid-climb, hard afternoon shadow |
| 13 | 0.42 | 0.86 | `2026-09-19/123728.jpg` | squirrel head-down on the trunk |
| 17 | 0.24 | 0.86 | `2026-09-19/110115.jpg` | 2.1% of the frame and the biggest animal in the set, ranked last: clipped at the edge and badly motion-blurred |

Rank 17 is the ranking working as intended. It is the largest animal of the
two days and the worst picture of it.

## The morning itself

Nine of the seventeen visits fall in forty-four minutes on the 20th:

| visit | from | to | frames |
|---|---|---|---|
| 16 | 09:16:26 | 09:16:28 | 2 |
| 5 | 09:28:14 | 09:28:18 | 2 |
| 8 | 09:29:19 | 09:29:33 | 6 |
| 10 | 09:33:36 | 09:34:00 | 5 |
| **3** | **09:35:07** | **09:43:20** | **32** |
| **2** | **09:45:18** | **09:53:16** | **53** |
| 14 | 09:54:34 | 09:54:34 | 1 |
| 7 | 09:57:12 | 09:58:22 | 5 |
| 6 | 10:00:32 | 10:00:32 | 1 |

Two of them run for eight minutes each and account for 85 of the morning's
107 animal frames. The camera then took one more photograph at 10:00:32 ---
a squirrel, at 0.88 --- and the battery died two minutes later.

## People

Three photographs, 10:08:14 to 10:08:47 on the 19th, at 0.94--0.95. The
camera powered on at 10:04:56, so these are three and a half minutes into
its life: the deployment itself. No animal frame falls in a motion event
with a person in it, so the person-event exclusion removed nothing from the
ranking.

## What this does not establish

- **The species is by eye, from crops.** "Grey squirrel" is what seventeen
  tiles look like. It is a confident call for an animal this distinctive at
  this size, and it is still not a classifier.
- **The 412 empty frames are "MegaDetector saw nothing", not "nothing was
  there."** A distant or well-hidden animal that the detector misses is
  counted as empty here, and the archive already holds squirrels it scored
  at 0.13. The afternoon storm is a strong hint about the camera's rules,
  not a measurement of them.
- **Nothing about the camera's recall.** The 1,158 training-burst frames
  from these two days were copied to the NAS but are not in this run, which
  covers `--kind photo` only. The bursts are the only frames the rules can
  be graded on, and grading them is a separate pass.
- **Two days of one camera.** The September pass a day earlier credited
  wildlifecam11 with nine animal visits over all of its days; these two days
  alone hold seventeen. That is a good position, not a trend.
- **The timing is not comparable to anything in
  [`benchmarks/`](benchmarks/),** for the two reasons at the top.

The battery run behind these photographs --- 23 h 57 m 48 s on the A1229,
power-on recovered from the journal and confirmed by `uptime_s` --- is
recorded in the private analysis repo, not here.
