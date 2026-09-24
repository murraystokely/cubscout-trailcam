# Five cards before the campout, and whether 599 px would have cost an animal

**Run:** evening of 24 September 2026, on the Mac Studio, from the handoff
the ThinkPad wrote that afternoon. Run 9 (MDv5a) extended over everything
new on the NAS: **14,819 frames in 17.7 minutes**, 0.07 s/frame.
**Cards:** wildlifecam9, 13, 14, 15 (Camera Module 3, step 9 or the old
loop) and wildlifecam12 (IMX500, step 8). Folder dates are the cameras'
own clocks and are not trusted below; frames are grouped by the sidecar's
`code` fingerprint, and times are quoted within a boot only.
**Crops:** every visit at animal >= 0.5 on the five cameras, with the
whole frame a click away, on the NAS at
`derived/handoff-2026-09-24/index.html` (134 visits).

---

## 1. Is there any real wildlife?

Almost none. Over each camera's whole history on the NAS:

| camera | photographs | animal >= 0.8 | 0.2 to 0.8 | person >= 0.8 | unreadable |
|---|---|---|---|---|---|
| wildlifecam9 | 2,594 | 0 | 27 | 239 | 36 |
| wildlifecam13 | 9,685 | 7 | 3,164 | 332 | 28 |
| wildlifecam14 | 3,343 | 0 | 10 | 42 | 10 |
| wildlifecam15 | 81 | 0 | 5 | 18 | 12 |
| wildlifecam12 | 9,664 | 12 | 834 | 16 | 5 |

Every candidate at 0.2 or above on cameras 9, 14 and 15, and every one at
0.5 or above on 12 and 13, was looked at as a crop with the detector's
box drawn on the frame. What they are:

- **wildlifecam9** (patio, step 9): nothing. Foliage against the fence, a
  tree trunk, a hand at 0.64 while the camera was set up, and black night
  frames. The 239 "people" are real people on the patio between 15:00 and
  16:00 and again 19:00 to 21:00, not the dark-band artefact (no box is
  full-width).
- **wildlifecam14** (side yard, step 9 and the old loop): nothing. A tree,
  a ceiling lamp, a blur, a window.
- **wildlifecam15** (old loop, 81 undated frames): nothing. A person
  gardening, scored 0.65 as an animal, and a tree trunk.
- **wildlifecam12** (IMX500, step 8): nothing on this card. All sixteen
  visits at 0.5 or above are **the white plastic bag with teal handles**,
  in the same place in every frame, at 0.51 to 0.67. Its 12 animals at
  0.8 are from earlier folders, already written up.
- **wildlifecam13** (fence line, oleander): **one squirrel**, on the 23rd
  at 17:13 to 17:14 (40 frames) and again at 17:18 (2 frames), on the
  ground beside the blue shed, scored 0.50 to 0.78. That is the only
  wildlife in the five cards. Its lone 0.81 frame is **the green pot**,
  which with light moving on it fills 64 frames at 15:45 and 12 at 16:00
  in the 0.5 to 0.8 band. The rest of its 3,164 uncertain frames are the
  bag (143 boxes at the bag's position), the pot, and the fence.

So: **one squirrel visit across five cards and 25,000 photographs.** The
cameras did not fail; the gardens were quiet, and two of the cameras were
pointed at a bag and a pot.

## 2. The crops

`derived/handoff-2026-09-24/index.html` on the NAS. The squirrel is the
visit at 17:13:50 with the box on the ground left of the shed door.
Everything else on the page is a bag, a pot, a flare, a gardener or a leaf,
and it is worth ten minutes to see what 0.5 to 0.8 means on these cameras.

## 3. The threshold change: 301 px to 599 px, and a darkness gate

The join is the sidecar, not the CSV: every step-9 photograph records the
`biggest_blob` and `mean_luma` of the look that saved it, exactly, so no
join by the second was needed. The CSV supplied the population of all
saves, which the surviving files under-count by half (the overwrite bug).

| camera (step 9 at 301 px) | saves in CSV | 301 to 598 px | mean luma < 25 | animals >= 0.8 | of which < 599 | of which dark |
|---|---|---|---|---|---|---|
| wildlifecam9 | 5,151 | 1,289 (25%) | 1,376 (27%) | 0 | 0 | 0 |
| wildlifecam13 | 10,461 | 2,677 (26%) | 10 | 1 (the pot, 666) | 0 | 0 |
| wildlifecam14 | 4,463 | 1,821 (41%) | 359 (8%) | 0 | 0 | 0 |

And the one real animal, the squirrel, frame by frame:

| visit | frames at >= 0.5 | blob below 301 | 301 to 598 | 599 and up | smallest |
|---|---|---|---|---|---|
| squirrel, 17:13 | 40 | 0 | 0 | 40 | **1,747** |
| squirrel, 17:18 | 2 | 0 | 0 | 2 | 1,491 |
| the pot, 15:45 | 64 | 0 | 29 | 35 | 518 |
| the pot, 16:00 | 12 | 0 | 5 | 7 | 339 |
| lens flare, 17:38 | 49 | 0 | 18 | 31 | 311 |

**On this data the 599 px floor costs no animal and the darkness gate
costs no animal.** The squirrel's smallest blob is 2.5 times the new floor.
Nothing the detector called an animal at any confidence was taken at mean
luma under 25; the 1,745 dark saves are sensor noise, exactly as the
handoff said. The "338 px smallest animal ever detected" that the handoff
flagged as below the new floor is in this data --- wildlifecam13 at
17:38:47, confidence 0.71 --- and **it is lens flare**, not an animal. That
figure should be struck.

**But this data cannot settle the number, and I would not deploy 599 on
it.** Three reasons.

- **One animal.** A single squirrel two metres from the lens, in one
  garden, is not a measurement of what a floor costs. It is one point.
- **The archive already holds an animal far below both floors.** The
  crow of 26 August on wildlifecam4 made a blob of **49 to 91 px** at the
  same 640x480 motion resolution, steadily, for the whole visit
  (`results/2026-09-12-first-megadetector-pass.md`). MDv5a saw it at
  0.8; the camera called it `quiet`. That is what a small bird at ten
  metres looks like to this pipeline, and 599 is six times it.
- **The geometry is the argument for the park.** `evaluation-design.md`'s
  own table, at 640 wide: a deer at 30 m is about 28x18 = **504 px**; a
  squirrel at 10 m is 14x8 = **112 px**. At 599 the floor is above a deer
  at thirty metres in an open field. At 301 it is under the deer and
  still above the squirrel. The 599 figure was chosen to defeat tree
  shadow on a white stucco wall, and Grant Park has no stucco wall.

The floor is also not what removes the false positives that matter. The
pot at 15:45 has 35 frames over 599 and the flare has 31; both would
survive. What the floor removes on these cards is 25 to 41% of saves, and
on the one camera with an animal in it, none of the animal.

**Recommendation for the park.** Keep the darkness gate: it is free, the
camera cannot see in the dark anyway, and it removes a quarter of
wildlifecam9's saves. Put the blob floor **back to 301 px**, or lower, for
the deployment, and accept the shadow frames. The stated objective is
event recall against a false-positive budget, and a card that fills with
shadows loses nothing that a card that missed a deer at thirty metres
would not lose worse. If 599 is kept anywhere, keep it on the stucco
camera only, as a per-site number, which is what
[`sites-design.md`](../sites-design.md) is for.

What would settle it: a week at the park at 301, and this same join. The
CSV rows for every look are the unbiased sample; the detector over the
saves says which blobs were animals; the blob sizes of the animals that
appear are the floor. Two cameras with different scenes would be twice as
good as one.

## 4. The step-8 follow-up: were the shadow rejections shadows?

wildlifecam12's bursts: 354 training frames the camera rejected as
`shadow` and MDv5a has now seen. **None is an animal at 0.8.** 325 are
under 0.2; 29 are between 0.2 and 0.6, the two highest at 0.56 and 0.60
on 3 September at 16:24 with blobs around 1,300 px. Those two are worth
one look, and the claim that the shadow rule threw away nothing still
holds on this camera.

## What this does not establish

- **Where the floor should be.** One squirrel cannot say. The archive's
  crow at 49 px and the geometry say 599 is too high for a park; they do
  not say what is right.
- **Whether the pot, the bag and the flare will have cousins at the
  campsite.** They will, and no floor fixes them; the detector on the
  laptop is what removes them, as designed.
- **Anything about the wildlifecam9 and 15 undated frames** beyond the
  fact that no animal is in them.

## Reproducing this

```bash
cd ai
export WILDLIFE_PHOTOS=/Volumes/datasets/trailcam/photos
.venv/bin/python -m trailcam scan --kind all
.venv/bin/python -m trailcam detect --extend 9
.venv/bin/python results/2026-09-24-threshold-join.py 9   # the sidecar join
```
