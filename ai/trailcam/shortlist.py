"""The best pictures of animals, ranked, with no model but the detector.

design.md calls this the differentiator over stock camera-trap tools: the
detector says where the animals are, and this says which of those frames
a person would actually want to look at.  Four signals, all cheap:

    confidence   the detector really thinks it is an animal
    size         a speck at thirty metres is not a photograph of anything
    wholeness    an animal half out of frame is half a picture
    sharpness    motion blur, from the variance of the Laplacian

multiplied into one score, and then the frames are grouped into visits --
the same camera, no gap longer than a minute -- and only the best frame
of each visit makes the list.  A crow that stayed for forty frames is one
entry, not forty.

Nothing here is tuned.  The constants live in config.py with the reason
each has the value it has, and every one of them is a guess written down
so a season of looking at the output can correct it.
"""

import csv
import html
import shutil
import statistics

from datetime import datetime
from pathlib import Path

# Pillow is imported inside the two functions that read pixels, not here,
# so the ranking and grouping can be tested (and `trailcam status` run)
# on a machine with nothing but the standard library.

from . import config
from . import manifest as manifest_module


def _placeholders(run_ids):
    return ", ".join("?" for _ in run_ids)


def candidates(database, run_ids, kind=None):
    """Every animal box at or above ANIMAL_TRUTH in any of the runs, best
    per frame.

    More than one run on purpose.  v5a and redwood were each run over the
    whole archive (results/2026-09-19-v5a-against-redwood-over-everything.md)
    and at the 0.8 line each finds real animals the other leaves just
    under it -- redwood about a hundred frames of squirrels in leaf
    litter, v5a a hundred of crows under the table -- while neither puts
    an empty frame over the line.  So for the gallery the honest input is
    the union: a frame is a candidate if either model is sure, and its box
    is whichever model was surer.
    """
    where = (f"r.run_id IN ({_placeholders(run_ids)}) "
             f"AND d.category = 'animal' AND d.confidence >= ?")
    arguments = [*run_ids, config.ANIMAL_TRUTH]
    if kind:
        where += " AND f.kind = ?"
        arguments.append(kind)

    rows = database.execute(
        f"""SELECT f.id AS frame_id, f.path, f.camera, f.day, f.captured_at,
                   f.kind, f.mean_luma, r.run_id,
                   d.confidence, d.x, d.y, d.w, d.h, d.crop_path
              FROM detections d
              JOIN frame_results r ON r.id = d.frame_result_id
              JOIN frames f ON f.id = r.frame_id
             WHERE {where}
             ORDER BY f.camera, f.captured_at, d.confidence DESC""",
        arguments).fetchall()

    # One box per frame: the first, which the ORDER BY makes the most
    # confident across every run.  Two animals in one frame is a nicer
    # problem than we have.
    best = {}
    for row in rows:
        best.setdefault(row["frame_id"], dict(row))

    return list(best.values())


def size_term(box):
    area = box["w"] * box["h"]
    return min(1.0, area / config.SUBJECT_FULL_AREA) ** config.SIZE_EXPONENT


def events_with_people(database, run_ids):
    """The stretches of each camera's day that had a person in them.

    Every frame any of the runs saw -- not just the animal ones -- is
    grouped into events: same camera, no gap over EVENT_GAP_SECONDS.  An
    event is tainted if EVERY run that saw it puts a person somewhere in
    it at PERSON_NEARBY or above, and the whole event is returned as a
    (camera, start, end) span.

    Every run, not any run.  The first version took any run's word, on
    the grounds that redwood finds more people than v5a; it then threw out
    126 frames of morning crows, because redwood's person boxes in those
    events were a crow's feet under the bench at 0.54, a Cheerio at the
    edge of the frame at 0.52, and a crow at 0.86.  The real people --
    the child of 4 September, the gardener, the evening of the 18th --
    score over 0.9 with both models.  A person two detectors agree on is
    a person; a person one detector sees at 0.5 in a frame full of crows
    is, on this archive, a crow.

    Why the whole event and not a minute either side: a gardener on
    wildlifecam10 tripped the camera 240 times in fourteen minutes and
    MegaDetector put a person box on two of those frames.  In the rest it
    scored him as an animal, or as nothing.  A person does not leave and
    come back between two frames a second apart, so the presence of one
    anywhere in a run of consecutive triggers means the run is about that
    person.  (analysis-2026-09-15-wildlifecam10.md and
    analysis-2026-09-18-wildlifecam4.md in the private analysis repo.)
    """
    rows = database.execute(
        f"""SELECT f.camera, f.captured_at, r.run_id, r.max_person_conf
              FROM frame_results r JOIN frames f ON f.id = r.frame_id
             WHERE r.run_id IN ({_placeholders(run_ids)})
               AND r.status = 'ok'
             ORDER BY f.camera, f.captured_at""", list(run_ids)).fetchall()

    spans = []
    current = None                  # [camera, start, end, {run: max person}]

    def close():
        if current and current[3] and all(
                best >= config.PERSON_NEARBY for best in current[3].values()):
            spans.append((current[0], current[1], current[2]))

    for row in rows:
        when = datetime.fromisoformat(row["captured_at"])
        person = row["max_person_conf"] or 0.0
        if (current is None or row["camera"] != current[0]
                or (when - current[2]).total_seconds()
                > config.EVENT_GAP_SECONDS):
            close()
            current = [row["camera"], when, when, {row["run_id"]: person}]
        else:
            current[2] = max(current[2], when)
            seen = current[3]
            seen[row["run_id"]] = max(seen.get(row["run_id"], 0.0), person)

    close()
    return spans


def without_people(entries, spans):
    """Drop entries that fall inside an event that had a person in it."""
    by_camera = {}
    for camera, start, end in spans:
        by_camera.setdefault(camera, []).append((start, end))

    kept = []
    for entry in entries:
        when = datetime.fromisoformat(entry["captured_at"])
        margin = config.EVENT_GAP_SECONDS
        tainted = any(
            (start - when).total_seconds() <= margin
            and (when - end).total_seconds() <= margin
            for start, end in by_camera.get(entry["camera"], ()))
        if not tainted:
            kept.append(entry)
    return kept


def is_clipped(box):
    m = config.EDGE_MARGIN
    return (box["x"] < m or box["y"] < m
            or box["x"] + box["w"] > 1.0 - m
            or box["y"] + box["h"] > 1.0 - m)


def sharpness(image_path, box):
    """Variance of the Laplacian over the box, on the full-size original.

    The standard blur measure: a sharp edge has a large second derivative,
    blur smears it flat.  Read from the original rather than the crop on
    disk because the crop was JPEG-compressed once more on the way, and
    that alone changes the number.
    """
    from PIL import Image, ImageFilter, ImageStat

    laplacian = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0],
                                   scale=1, offset=128)
    with Image.open(image_path) as image:
        width, height = image.size
        region = image.crop((int(box["x"] * width), int(box["y"] * height),
                             int((box["x"] + box["w"]) * width),
                             int((box["y"] + box["h"]) * height)))
        edges = region.convert("L").filter(laplacian)
        return ImageStat.Stat(edges).var[0]


def score(entries):
    """Attach the four terms and the score to each candidate, in place."""
    for entry in entries:
        try:
            entry["sharpness"] = sharpness(
                config.PHOTO_ROOT / entry["path"], entry)
        except OSError:
            entry["sharpness"] = None

    measured = [e["sharpness"] for e in entries if e["sharpness"] is not None]
    sharp_enough = statistics.median(measured) if measured else 1.0

    for entry in entries:
        entry["size_term"] = size_term(entry)
        entry["clipped"] = is_clipped(entry)
        if entry["sharpness"] is None:
            entry["sharp_term"] = config.SHARPNESS_FLOOR
        else:
            entry["sharp_term"] = max(
                config.SHARPNESS_FLOOR,
                min(1.0, entry["sharpness"] / sharp_enough))

        entry["score"] = (entry["confidence"]
                          * entry["size_term"]
                          * (config.CLIPPED_PENALTY if entry["clipped"] else 1.0)
                          * entry["sharp_term"])

    return entries


def visits(entries):
    """Group frames into visits: same camera, no gap over EVENT_GAP_SECONDS."""
    ordered = sorted(entries, key=lambda e: (e["camera"], e["captured_at"]))
    grouped = []
    current = []
    previous = None

    for entry in ordered:
        when = datetime.fromisoformat(entry["captured_at"])
        if (previous is not None
                and (entry["camera"] != previous["camera"]
                     or (when - datetime.fromisoformat(previous["captured_at"]))
                     .total_seconds() > config.EVENT_GAP_SECONDS)):
            grouped.append(current)
            current = []
        current.append(entry)
        previous = entry

    if current:
        grouped.append(current)

    return grouped


def best_of_each_visit(entries):
    result = []
    for group in visits(entries):
        best = max(group, key=lambda e: e["score"])
        best["visit_frames"] = len(group)
        best["visit_start"] = group[0]["captured_at"]
        best["visit_end"] = group[-1]["captured_at"]
        result.append(best)
    return sorted(result, key=lambda e: -e["score"])


def looked_at(database, run_ids):
    """How much the runs have seen between them: the number for the top
    of the page.  Frames, not results, so a frame two models both saw
    counts once."""
    row = database.execute(
        f"""SELECT COUNT(DISTINCT r.frame_id) AS frames,
                   COUNT(DISTINCT CASE WHEN f.kind = 'photo'
                                       THEN r.frame_id END) AS photos,
                   COUNT(DISTINCT f.camera) AS cameras,
                   MIN(f.day) AS first_day, MAX(f.day) AS last_day
              FROM frame_results r JOIN frames f ON f.id = r.frame_id
             WHERE r.run_id IN ({_placeholders(run_ids)})""",
        list(run_ids)).fetchone()
    return dict(row)


def build(database, run_ids=None, kind=None, top=30, destination=None,
          quiet=False):
    """Rank, dedupe, print, and write the CSV and the gallery."""
    if run_ids:
        runs = [database.execute("SELECT * FROM runs WHERE id = ?",
                                 (run_id,)).fetchone() for run_id in run_ids]
        if any(run is None for run in runs):
            print(f"No such run in {run_ids}. `trailcam runs` lists them.")
            return []
    else:
        reference = manifest_module.reference_run(database)
        if reference is None:
            print("No run to rank: pass --run, or set a reference run.")
            return []
        runs = [reference]

    run_ids = [run["id"] for run in runs]
    found = candidates(database, run_ids, kind=kind)
    entries = score(without_people(found,
                                   events_with_people(database, run_ids)))
    ranked = best_of_each_visit(entries)
    totals = looked_at(database, run_ids)

    if not quiet:
        names = ", ".join(f"{run['id']} ({run['name']})" for run in runs)
        print(f"Run{'s' if len(runs) > 1 else ''} {names}: "
              f"{totals['frames']} frames looked at, {len(found)} animal "
              f"frames at >= {config.ANIMAL_TRUTH}, "
              f"{len(found) - len(entries)} left out for being in an event "
              f"with a person in it, {len(ranked)} visits.\n")
        print(f"{'#':>3}  {'score':>5}  {'conf':>4}  {'size':>5}  "
              f"{'sharp':>5}  {'frames':>6}  frame")
        for rank, entry in enumerate(ranked[:top], start=1):
            print(f"{rank:3d}  {entry['score']:5.2f}  "
                  f"{entry['confidence']:4.2f}  "
                  f"{100 * entry['w'] * entry['h']:4.1f}%  "
                  f"{entry['sharp_term']:5.2f}  "
                  f"{entry['visit_frames']:6d}  "
                  f"{entry['path']}{'  (clipped)' if entry['clipped'] else ''}")

    destination = Path(destination or config.SHORTLIST_DIR)
    write_csv(ranked, destination / "shortlist.csv")
    write_gallery(ranked, runs, destination, top=top, totals=totals)

    if not quiet:
        print(f"\nWrote {destination / 'shortlist.csv'} and "
              f"{destination / 'index.html'}")

    return ranked


def write_csv(ranked, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["rank", "score", "camera", "captured_at", "path", "confidence",
              "area_fraction", "clipped", "sharpness", "visit_frames",
              "visit_start", "visit_end"]
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        for rank, e in enumerate(ranked, start=1):
            writer.writerow([
                rank, f"{e['score']:.3f}", e["camera"], e["captured_at"],
                e["path"], f"{e['confidence']:.3f}",
                f"{e['w'] * e['h']:.4f}", int(e["clipped"]),
                "" if e["sharpness"] is None else f"{e['sharpness']:.1f}",
                e["visit_frames"], e["visit_start"], e["visit_end"]])


def write_gallery(ranked, runs, destination, top=30, totals=None):
    """A static page of every visit: the top ones large, the rest as crops.

    The top entries get two images each -- the crop, padded generously,
    shows the animal; the full frame shows the photograph, which is the
    thing being judged -- and the originals are copied in so the page
    works with the archive unmounted.  Every visit below the cut still
    gets its crop, because the ranking judges picture quality and nothing
    else: the first pass hid every squirrel in the archive behind the crows
    that walk up to the lens, and a person flipping through 150 thumbnails
    found them in a minute.  Nothing the detector called an animal should
    be invisible from this page.
    """
    destination.mkdir(parents=True, exist_ok=True)
    images = destination / "images"
    images.mkdir(exist_ok=True)

    cards = []
    strip = []
    for rank, e in enumerate(ranked, start=1):
        source = config.PHOTO_ROOT / e["path"]
        stem = f"{rank:03d}-{e['camera']}-{Path(e['path']).stem}"
        crop = images / f"{stem}-crop.jpg"
        when = e["captured_at"].replace("T", " ")[:19]
        why = (f"conf {e['confidence']:.2f} &times; size {e['size_term']:.2f}"
               f" &times; sharp {e['sharp_term']:.2f}"
               + (f" &times; clipped {config.CLIPPED_PENALTY}"
                  if e["clipped"] else ""))

        try:
            _write_padded_crop(source, e, crop)
            if rank <= top:
                full = images / f"{stem}.jpg"
                shutil.copyfile(source, full)
        except OSError:
            continue

        if rank <= top:
            cards.append(f"""
  <figure>
    <a href="images/{full.name}"><img src="images/{crop.name}"
         alt="animal, {e['camera']} {when}"></a>
    <figcaption>
      <b>#{rank}</b> {html.escape(e['camera'])} &middot; {when}<br>
      score {e['score']:.2f} = {why}<br>
      {e['visit_frames']} frame{'s' if e['visit_frames'] != 1 else ''} in this visit
      &middot; <code>{html.escape(e['path'])}</code>
    </figcaption>
  </figure>""")
        else:
            strip.append(f"""
  <figure class="small">
    <img src="images/{crop.name}" alt="animal, {e['camera']} {when}"
         title="#{rank} {e['camera']} {when} -- score {e['score']:.2f}, {e['path']}">
    <figcaption>#{rank} {e['camera'][-2:]} {when[5:16]}</figcaption>
  </figure>""")

    # noindex, because this page may end up on a public web server at an
    # address handed out by hand.  Nothing links to it, and this asks the
    # search engines not to either, should somebody share the link.
    models = " and ".join(html.escape(run["name"]) for run in runs)
    totals = totals or {}
    if totals.get("frames"):
        looked = (f"<p class=\"total\"><b>{totals['frames']:,}</b> images "
                  f"looked at by MegaDetector "
                  f"({totals['photos']:,} photographs the cameras chose, "
                  f"the rest training frames), from "
                  f"{totals['cameras']} cameras, "
                  f"{totals['first_day']} to {totals['last_day']}.</p>")
    else:
        looked = ""

    page = f"""<!doctype html>
<meta charset="utf-8">
<meta name="robots" content="noindex, nofollow">
<title>Best animal pictures</title>
<style>
  body {{ font: 14px/1.4 system-ui, sans-serif; margin: 2em; background: #fafafa; }}
  main {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 1.5em; }}
  .rest {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: .6em; }}
  .total {{ font-size: 18px; }}
  figure {{ margin: 0; background: white; border: 1px solid #ddd; padding: .5em; }}
  figure.small {{ padding: .25em; }}
  figure.small figcaption {{ font-size: 11px; padding-top: .2em; }}
  img {{ width: 100%; height: auto; display: block; }}
  figcaption {{ padding: .5em 0 0; color: #333; }}
  code {{ font-size: 12px; color: #666; }}
</style>
<h1>Best animal pictures</h1>
{looked}
<p>Detector{'s' if len(runs) > 1 else ''} {models}: a frame is in if any
of them is sure it holds an animal, and out if they agree a person was
about.
One frame per visit, ranked by confidence &times; size &times; wholeness
&times; sharpness. Click a crop for the whole photograph. Generated
{datetime.now():%Y-%m-%d %H:%M}.</p>
<main>{''.join(cards)}
</main>
<h2>Every other visit ({len(strip)})</h2>
<p>The ranking judges picture quality, not what the animal is. Everything
the detector called an animal is here; hover for the file.</p>
<div class="rest">{''.join(strip)}
</div>
"""
    (destination / "index.html").write_text(page)


def _write_padded_crop(source, box, destination, margin=0.6, longest=900):
    from PIL import Image

    with Image.open(source) as image:
        width, height = image.size
        mx, my = box["w"] * margin, box["h"] * margin
        left = max(0.0, box["x"] - mx) * width
        top = max(0.0, box["y"] - my) * height
        right = min(1.0, box["x"] + box["w"] + mx) * width
        bottom = min(1.0, box["y"] + box["h"] + my) * height
        crop = image.crop((int(left), int(top), int(right), int(bottom)))
        crop.thumbnail((longest, longest))
        crop.convert("RGB").save(destination, "JPEG", quality=88)
