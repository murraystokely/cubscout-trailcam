# MegaDetector v5a against v6 redwood, over everything

**Run:** 19 September 2026. Run 13 in the Mac Studio's manifest: MDv6
redwood (`md_v1000.0.0-redwood.pt`) over all 58,099 frames, compared
against run 9 (v5a) with `trailcam compare 9 13`.
**Cost:** about 77 minutes on the Mac Studio, at 12 to 13 frames a second
from the NAS --- the same speed as v5a. The run was killed once by the
machine's memory guard at 51,650 frames and resumed; a run's queue is the
frames it has no row for, so nothing was redone.

The 13 September comparison covered 5,673 training frames of one patio
and four crow events, and said so. This one covers six cameras, 51,126
photographs, about 200 animal visits, squirrels and small birds as well
as crows. It is the comparison that one was waiting for.

---

## The headline

**Neither model dominates, and the 0.8 line cuts through real animals on
both sides.** Over the photographs:

| at animal >= 0.8 | frames | visits |
|---|---|---|
| both models | 1,498 | |
| v5a only (redwood 0.5 to 0.79, a few at 0.0) | 108 | 68 |
| redwood only (v5a 0.6 to 0.79) | 201 | 98 |

Every visit in both exclusive sets was looked at as a crop, with the
calling model's own box. **They are real animals, almost without
exception**: the redwood-only set is squirrels in the leaf litter and on
the bark of wildlifecam10 and 11, crows on the patio, and a towhee v5a
put at 0.38; the v5a-only set is crows, mostly close and partly hidden
under the table, a few squirrels, and towhees. The one thing in either
set that is not an animal is the child of 4 September, which v5a scored
0.80 and redwood 0.14. Neither model puts an empty frame over 0.8
anywhere in 51,126 photographs. At that line both are, as far as a person
can tell from crops, precise; redwood finds about a hundred more real
frames and thirty more visits than v5a, and v5a a hundred fewer.

Redwood's outright misses are few and odd: fourteen frames v5a scores at
0.8 or better that redwood puts under 0.1. Most are a crow filling the
frame at close range, sometimes half under the table --- and on
wildlifecam1's crow's-feet frames redwood scores **person** at 0.75 to
0.86 instead. Big, partial, black things are where it is weakest.

## People

Redwood is the better person detector, again, and by more this time:

| person >= 0.8, photographs | |
|---|---|
| v5a | 1,410 |
| redwood | 1,587 |
| redwood only | 296 |

and it declines the child that v5a called an animal. For the privacy
filter in front of any gallery, redwood's person scores are the ones to
trust.

## The uncertain band, which is where the two really differ

Everything below 0.8 and above 0.1 is the hand-labelling queue for the
evaluation and the "maybe" pile for the gallery. Here the models trade
scenes:

| uncertain animal frames, photographs | v5a | redwood |
|---|---|---|
| wildlifecam4 (patio, sunlit paving) | 1,020 | **5,423** |
| wildlifecam10 (leaf litter) | 769 | **1,988** |
| wildlifecam13 (hanging feeder) | **2,959** | 563 |
| wildlifecam12 (shrub, white wall) | **407** | 163 |
| wildlifecam11 | 110 | 37 |
| **all photographs** | **5,350** | **8,234** |
| **training frames** | **645** | **1,610** |

Redwood sees animals in dappled sunlight on paving and on leaf litter:
4,607 of v5a's patio empties come up to between 0.1 and 0.8, 652 of them
above 0.5. That is the finding from 13 September, now over the whole
archive, and it is scene-specific. On the two cameras whose false
positives are objects rather than light --- wildlifecam13's feeder,
wildlifecam12's shrub --- redwood is the quieter model by a wide margin:
it puts 2,425 of the 2,959 feeder frames under 0.1.

## What to do with that

**For the evaluation, v5a stays the reference.** Its job is to label the
training frames cheaply and leave a short list for a person, and its list
is 645 frames against redwood's 1,610. The animals at 0.8 are the same 33
frames on both. Nothing in this comparison moves that decision; the
patio's paving is still where the evaluation data comes from.

**For the gallery, use both.** The two exclusive sets at 0.8 are real
animals, so the right input to the shortlist is the union of the two
runs, and the right person filter is redwood's. That is a small change to
`shortlist` --- take more than one `--run`, keep each frame's best box
across them, and read person events from all of them --- and it would add
about thirty visits to the gallery at no cost in false positives. Not
done yet.

**The 0.5 to 0.8 band on both models holds more real animals** than the
ranking currently shows, especially squirrels on the leaf-litter cameras.
A species classifier over the crops is still the tool that turns that
band into something usable, for either model.

## What this does not establish

- **Species and "real animal" are by eye from crops**, over 166 visits of
  disagreement. A person can tell a squirrel from a leaf at this size; a
  person can also be wrong, and nobody checked the person.
- **Nothing about either model on a real trail camera.** Six cameras in
  one garden, three species, one autumn.
- **The redwood weakness on big close crows is a handful of frames.**
  Fourteen, all on the patio, all frames v5a also caught. It would matter
  if redwood were the only model; it is not.

## Reproducing this

```bash
cd ai
export WILDLIFE_PHOTOS=/Volumes/datasets/trailcam/photos
.venv/bin/python -m trailcam detect --model md1000-redwood --new-run   # ~77 min
.venv/bin/python -m trailcam compare 9 13
```
