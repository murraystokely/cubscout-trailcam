# The Grant Park campout: a deer, three flickers, two rabbits, and 7,936 photographs

**Run:** afternoon of 27 September 2026, on the Mac Studio, from the
handoff the ThinkPad wrote that day. Run 9 (MDv5a) extended over the
sixteen `<camera>/<day>` folders the campout touched: **22,376 frames were
queued, of which 7,936 are campout photographs**, in about 14 minutes at
0.07 s/frame on the GPU. Everything below is about the 7,936.
**Site:** Joseph D. Grant County Park, group campsite 2, 25 to 27
September (`ai/sites.csv`, slug `grant-park-site2`).
**Cameras:** wildlifecam3, 9, 10, 11, 12, 13, 14, 15. Eight of them; step 9
on five, step 8 (IMX500) on three.
**What "campout" means:** a row in
`derived/grant-park-site2/frames.csv` on the NAS, built from `boot` and
`uptime_s` in every sidecar (`ai/deployments.csv` has the boot list and the
evidence). Folder dates and camera clocks were not used to decide anything:
there was no network at the park, so every camera's clock is the last time
it was at home, and wildlifecam3 filed the whole weekend under
`2026-08-29/`.
**Picks:** `derived/grant-park-site2/animals/index.html` on the NAS, one card
per visit with the crop, the full frame, the species by eye and the MDv5a
confidence; the CSV is
[`shortlists/2026-09-27-grant-park-campout-mdv5a-run9.csv`](shortlists/2026-09-27-grant-park-campout-mdv5a-run9.csv).

---

## 1. Coverage

Every one of the 7,936 campout frames has a run-9 result with status `ok`.
The 235 "cannot identify image file" errors the pass printed are the
0-byte files each boot leaves when the power is pulled; none of them is in
`frames.csv`, so none of them counts. The join is by path: a frame is in
if its path is in `frames.csv`, and out otherwise, and after `scan` all
7,936 paths were in the manifest.

## 2. What MDv5a found

| camera | campout photos | animal >= 0.5 | 0.2 to 0.5 | person >= 0.8 |
|---|---:|---:|---:|---:|
| wildlifecam3 | 608 | 8 | 25 | 244 |
| wildlifecam9 | 420 | 15 | 21 | 27 |
| wildlifecam10 | 135 | 15 | 1 | 11 |
| wildlifecam11 | 468 | 7 | 54 | 67 |
| wildlifecam12 | 706 | 0 | 23 | 65 |
| wildlifecam13 | 818 | 21 | 8 | 404 |
| wildlifecam14 | 150 | 10 | 9 | 0 |
| wildlifecam15 | 4,631 | 488 | 2,098 | 2,453 |
| **total** | **7,936** | **564** | **2,239** | **3,271** |

Grouped by camera + boot + `uptime_s` with a 60 s gap, the 2,803 animal
candidates at 0.2 or above are **232 visits**, 160 of them on
wildlifecam15. The best two frames of every visit were looked at as crops,
and the doubtful ones at full size with the box drawn on.

## 3. What was actually there

**22 visits held an animal.** By camera:

- **wildlifecam9** (oak meadow by the fence, step 9): the **deer**, mid-leap
  in full sun at boot `1c7e1a69` uptime 53,193 s (`2026-09-26/022026_309`,
  0.94), the one frame from the weekend that a Scout will put on a wall.
  Also a **California scrub-jay** in flight with wings and tail spread
  (0.87), and a **northern flicker** on the ground under the oaks (0.84;
  the red malar stripe of a male shows in the next frame).
- **wildlifecam13** (trail edge by chaparral, step 9): a **cottontail
  rabbit** in morning sun (0.90), a second rabbit at first light (0.81), a
  **northern flicker** taking off with the salmon-red under-wing showing
  (0.91), and nine visits of small birds on the trail or in the tree, most
  of which I cannot name: a towhee or two, something dove-like landing, a
  crow taking off cut in half by the frame edge.
- **wildlifecam10** (chaparral hillside, step 8): the bird the handoff
  called a probable mockingbird, fourteen frames of it hopping around the
  camera. With the wings open (`051312`, 0.90) it has the salmon-red
  under-wing and spotted breast of a **northern flicker**. I am fairly
  sure; a birder may correct me.
- **wildlifecam14** (chaparral trail, step 9): probably a second **deer**,
  head down at last light, six frames over five seconds (0.79, and very
  dark); two flying birds with white wing flashes that may be
  **mockingbirds**; a bird landing; and one "small brown bird in the grass"
  at 0.81 that may be a leaf. That last one is the weakest card on the
  page and is labelled so.
- **wildlifecam15** (campground field, step 9): one **crow or raven**
  silhouette overhead (0.80), and two more corvids a few minutes later.
  That is the only animal in 2,586 candidates on this camera.

No coyote, bobcat, turkey, squirrel or jackrabbit, on any camera, at any
threshold I looked at.

## 4. What fooled the detector

The 0.2 to 0.5 band was worth looking through, because the deer scores
0.94 and a rabbit 0.81 but the dusk deer only 0.79 and the flicker on the
ground 0.84, so the line is not far above the noise here. What the noise
was:

- **wildlifecam15**: people. 2,453 frames with a person at 0.8 or above,
  and a further couple of thousand where the same people at dusk, in the
  dark, or half behind a tent scored 0.2 to 0.6 as animals instead. Grass
  moving in the wind under the shelter roof made up the rest.
- **wildlifecam11** (meadow over the tent line): a person in deep shadow
  at 0.55 as an animal with another person at 0.73 in the same frame, and
  grass.
- **wildlifecam12** (IMX500): hands over the lens while placing it, and
  a bag. Zero animals at 0.5 in 706 frames.
- **wildlifecam3**: the campsite being set up, upside down.

The shortlist rule from earlier passes held: **every animal pick was
checked for a person in the same frame, and the whole frame was looked
at.** The highest person score on any pick is 0.11 (the deer frame, which
has a fence and a parked car in the far background and nobody in it).

## 5. What this does not establish

- Species names are by eye from crops. "Fairly sure" and "unsure" on each
  card mean what they say; there is still no species classifier.
- Nothing about the camera's motion rules: these are photographs, not
  training bursts, so the miss rate is not measurable from them
  (`evaluation-design.md`). What the weekend does say is that at a real
  park the cameras' output is dominated by people, and the animal frames
  that matter are the ones the rules did keep.
- The count of visits depends on the 60 s gap and on `uptime_s` being
  right within a boot. Across a restart the clock jumps, and two visits
  either side of one are two visits whatever the animal did.

## 6. Reproducing it

```bash
export WILDLIFE_PHOTOS=/Volumes/datasets/trailcam/photos
for cam in wildlifecam3 wildlifecam9 wildlifecam10 wildlifecam11 wildlifecam12 wildlifecam13 wildlifecam14 wildlifecam15; do
    .venv/bin/python -m trailcam scan --kind photo --camera $cam
done
tail -n +2 /Volumes/datasets/trailcam/derived/grant-park-site2/frames.csv | cut -d, -f1 | cut -d/ -f1,2 | sort -u \
  | while IFS=/ read cam day; do
    .venv/bin/python -m trailcam detect --kind photo --camera $cam --day $day --extend 9
done
```

Then join `frame_results` for run 9 onto the paths in `frames.csv`, group
animal boxes at 0.2 or above by (camera, boot, uptime_s) with a 60 s gap,
and look at every visit. `trailcam shortlist` was not used: it groups by
`captured_at`, which is the camera clock, and that is exactly the thing
that cannot be trusted for this site. Teaching it about `boot` is the
obvious next change and was not done in a hurry today.

The second deliverable from the same handoff, the candid photographs of
the Scouts, is on the NAS beside this one and nowhere else, on purpose.
