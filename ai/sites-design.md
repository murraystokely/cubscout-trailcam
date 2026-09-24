# Sites --- design

**Status:** design, no code yet (2026-09-23).
**Companion to:** [`design.md`](design.md) and
[`evaluation-design.md`](evaluation-design.md). Those two assume every
photograph comes from one place. Until now every one has: a back garden
in Los Altos. Next weekend a set of cameras goes to Joseph D. Grant County
Park for a campout, and after that the archive holds two places whose
pictures should not be ranked against each other, and whose galleries
people will want separately.

---

## What a site is, and what it is not

A **site** is a place cameras get put: *the back garden*, *Grant Park,
woodland group site*. It is not a camera --- wildlifecam10 has already been
on the patio and under the shed trees, and will be at Grant Park next
week --- and it is not a day. The thing that has a site is a
**deployment**: one camera, at one place, for a stretch of time.

The manifest already has most of a deployment. The camera runs in `runs`
are one per (camera, step8 version); the private analysis repo's battery
table is one per (camera, power-on, power-off). Neither knows where the
camera was. That is the one fact nothing in the archive records, because
the camera does not know either.

## Three ways to record it, and which

**1. The camera writes it.** A small file on the card, `/etc/wildlifecam/site`,
set as part of the deploy checklist and copied into every sidecar next to
`camera` and `code`. This is the right answer in the long run: the fact
travels with the photographs, a card read at a campsite knows where it
was taken, and nothing has to be reconstructed later. It costs one line in
step8 and one in the checklist, and it does not help with the 58,000
frames already on the NAS or with wildlifecam13, which writes no sidecars.

**2. A deployments table, kept by hand.** A CSV in the repo:

```
camera,site,from,to
wildlifecam4,backyard,2026-08-18,
wildlifecam10,backyard,2026-08-27,2026-09-19
wildlifecam10,grant-park,2026-09-26,2026-09-28
```

`scan` reads it and stamps each frame with the site whose deployment
covers its camera and time. This is the only way the existing archive
gets a site, and the fallback for any camera that does not write one.
An open `to` means "still there".

**3. The archive layout.** `photos/<site>/<camera>/<day>/`. Rejected: it
would mean reorganising 167,000 files that the NAS README says are never
reorganised, `sync_cameras.py` and `sync_sdcard.py` would both need to
know the site at copy time, and a camera that moves between sites would
split its history across directories.

**Do 1 and 2.** The sidecar wins when it is present; the table fills in
history and covers cameras without sidecars. `scan` prints a warning for
any frame it cannot place, because an unplaced frame is a deployment
nobody wrote down.

## In the manifest

`frames` gains `site TEXT`, filled at `scan` the same way `kind` was: from
the sidecar if it has one, else from the deployments table, else NULL.
A column rather than a join, for the same reason `kind` is: every query
that groups by site is simpler, and the fact does not change once known.

A `sites` table holds the human-readable name, a slug for URLs, and a
free-text description (where the cameras were pointed, what the ground
was), keyed by slug:

```
slug         name                                   description
backyard     The back garden                        patio, side yard, under the shed trees
grant-park   Joseph D. Grant County Park, woodland  oak woodland, group site, creek below
```

Sites are few and named by a person, so the CSV that seeds the
deployments table can seed this one too.

## What changes downstream

**The shortlist ranks within a site.** The sharpness term is normalised
to the median among the candidates so that one scene's light does not
decide the ranking; that median must be taken per site, or the woodland's
dim mornings will be marked down against the patio's white paving. Visits
are already per camera and need nothing. The gallery becomes one page per
site plus an index that lists them, each page carrying its own "images
looked at" line and its own top thirty. Published as
`www.stokely.org/trailcam/<slug>/`.

**`report` splits by site** as it already splits by deployment and by
light, because a threshold that is right for a patio is wrong for a
trail, and `evaluation-design.md` says so in as many words.

**The person filter gets stricter at a campsite.** Sixty Scouts around a
group site will trip every camera all weekend. The event-level rule with
two detectors agreeing is the right shape; the bar `PERSON_NEARBY` may
want to come down for a site flagged public, and the review before
publishing wants more care, not less.

**The species notes become per site**, because the answer to "what lives
here" is the point of the exercise and it is a different answer in each
place.

## For next weekend, specifically

- No network at the campsite, so the sidecar `site` field is the one that
  will still be right when the cards are read a week later. Set it before
  leaving. The deployments CSV row can be written on the way home.
- All cards from the trip go into the archive as they always do,
  `<camera>/<day>/`; the site is a stamp on the frames, not a place in
  the tree.
- `campsite.sh` does not need to know about sites to work; it needs to
  know about them to show the right gallery. That is a `--site` argument
  it can pass through to `shortlist`.

## Order of work

1. `sites.csv` and `deployments.csv` in `ai/`, with the back garden's
   history in them; `frames.site`; `scan` fills it; `report`, `shortlist`
   and the gallery group by it. This is what makes the existing archive a
   site and the campout a second one.
2. step8 reads `/etc/wildlifecam/site` into the sidecar; `scan` prefers
   it. One line each side.
3. The per-site gallery index, and `publish-shortlist.sh` publishing the
   tree rather than one page.
