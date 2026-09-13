"""What the pass found, in the forms a person actually wants it.

Four questions, in the order they get asked:

  1. How much of the archive has been through the detector?
  2. What did the confidence split leave us -- how many frames are labelled
     for free, and how many does somebody have to look at by hand?
  3. What did the camera in the woods do with the frames MegaDetector says
     had an animal in them?  This is the number evaluation-design.md says
     we have never had.
  4. Which frames should a person label, to check the checker?

Question 3 wants stating carefully.  What follows is a FRAME-level count,
and the metric that matters is EVENT-level recall -- a deer in twenty frames
of which we photographed three is a complete success, not a 15% score.  That
comparison is E2's job, and it needs the replay harness.  What is here is
the honest first look, and it is already enough to tell whether the rules in
the woods are in the right postcode.
"""

import random

from collections import Counter

from . import bursts
from . import config
from . import manifest as manifest_module


def _bar(count, total, width=28):
    if not total:
        return ""
    filled = int(round(width * count / total))
    return "#" * filled + "." * (width - filled)


def coverage(database):
    """Question 1: what is in the manifest, and what has looked at it."""
    totals = manifest_module.counts(database)

    print("Frames in the manifest")
    print("----------------------")
    print(f"  training frames   {totals['frames'] or 0}")
    print(f"  cameras           {totals['cameras'] or 0}")
    print(f"  days              {totals['days'] or 0}  "
          f"({totals['first_day']} .. {totals['last_day']})")

    print("\nRuns")
    print("----")

    rows = manifest_module.runs(database)
    if not rows:
        print("  none yet -- `scan` records the camera's own decisions, "
              "`detect` adds a detector.")
        return

    total_frames = totals["frames"] or 0
    for row in rows:
        mark = " *" if row["role"] == "reference" else "  "
        state = "open" if row["finished_at"] is None else "done"
        version = f" {row['code_version']}" if row["code_version"] else ""
        print(f"{mark}{row['id']:3d}  {row['kind']:8s} {row['name']:16s}"
              f"{version:14s} {row['frames']:6d}/{total_frames}  {state}"
              + (f"  {row['errors']} errors" if row["errors"] else ""))

    print("\n  * = the reference run: the one `truth` reads, and the one")
    print("      every label below comes from.  `trailcam reference <id>`")
    print("      changes it -- one UPDATE, no recompute.")

    if manifest_module.reference_run(database) is None:
        print("\n  No reference run is set, so nothing can be labelled. "
              "Pick one with `trailcam reference <id>`.")


def split(database):
    """Question 2: what the confidence split bought us."""
    print(f"\nConfidence split  (animal >= {config.ANIMAL_TRUTH}, "
          f"empty < {config.EMPTY_TRUTH})")
    print("----------------")

    rows = database.execute(
        """
        SELECT label, label_source, COUNT(*) AS n
          FROM truth GROUP BY label, label_source ORDER BY n DESC
        """
    ).fetchall()

    total = sum(row["n"] for row in rows)
    if not total:
        print("  nothing yet")
        return

    for row in rows:
        source = "" if row["label_source"] == "auto" else " (hand)"
        print(f"  {row['label'] + source:22s} {row['n']:6d}  "
              f"{100.0 * row['n'] / total:5.1f}%  "
              f"{_bar(row['n'], total)}")

    def count(label, source="auto"):
        return sum(row["n"] for row in rows
                   if row["label"] == label and row["label_source"] == source)

    # Only frames the detector has actually seen are in the split at all --
    # counting the queue as "labelled for free" would be a cheerful lie.
    seen = total - count("not yet seen")
    if not seen:
        print("\n  Nothing has been through the detector yet: run `detect`.")
        return

    uncertain = count("uncertain")
    free = count("animal") + count("person") + count("empty")

    print(f"\n  Of {seen} frames seen: {free} labelled for free, "
          f"{uncertain} left for a person "
          f"({100.0 * uncertain / seen:.1f}%).")

    # The claim in evaluation-design.md is that the uncertain band is "a few
    # hundred frames, not a hundred thousand".  If that turns out false on
    # real data, the thresholds are wrong and it should be said out loud
    # rather than left for somebody to discover at labelling time.
    if uncertain > 2000:
        print("  That is a lot more than the 'few hundred' the design "
              "assumed -- worth widening EMPTY_TRUTH or narrowing "
              "ANIMAL_TRUTH before labelling.")


def against_the_camera(database):
    """Question 3: what the rules in the woods did with these frames."""
    print("\nWhat the camera decided, against what MegaDetector saw")
    print("------------------------------------------------------")

    rows = database.execute(
        """
        SELECT label,
               COALESCE(camera_decision, '(no CSV row)') AS decision,
               COUNT(*) AS n
          FROM truth
         WHERE status IS NOT NULL
         GROUP BY label, decision
        """
    ).fetchall()

    if not rows:
        print("  nothing detected yet")
        return

    # label -> outcome -> count, and label -> raw decision -> count, so the
    # summary and the detail underneath it cannot disagree.
    outcomes = {}
    details = {}
    unknown = Counter()

    for row in rows:
        outcome = bursts.classify_decision(row["decision"])

        outcomes.setdefault(row["label"], Counter())[outcome] += row["n"]
        details.setdefault(row["label"], Counter())[row["decision"]] += \
            row["n"]

        if outcome == "unknown" and row["decision"] != "(no CSV row)":
            unknown[row["decision"]] += row["n"]

    for label in ("animal", "person", "uncertain", "empty",
                  "too dark to trust", "unreadable"):
        bucket = outcomes.get(label)
        if not bucket:
            continue

        total = sum(bucket.values())
        print(f"\n  MegaDetector: {label}  ({total} frames)")

        for outcome, description in (
                ("wanted", "photograph taken"),
                ("suppressed", "wanted, rate-limited away"),
                ("rejected", "frame rejected"),
                ("unknown", "no decision recorded")):
            if not bucket[outcome]:
                continue
            print(f"    {description:28s} {bucket[outcome]:6d}  "
                  f"{100.0 * bucket[outcome] / total:5.1f}%")

        for decision, n in details[label].most_common():
            print(f"      {decision:34s} {n:6d}")

    print("\n  Read the 'animal' block as the miss rate and the 'empty'")
    print("  block as the false-positive rate -- per FRAME, which flatters")
    print("  neither. Event recall is E2's number and needs the replay.")

    if unknown:
        # A decision string we do not recognise means step8's vocabulary has
        # moved and bursts.WANTED is now out of date -- which would quietly
        # corrupt every number above.
        print("\n  Unrecognised decisions in the CSV -- update "
              "bursts.WANTED/REJECTED:")
        for decision, n in unknown.most_common():
            print(f"    {decision!r}: {n}")


def by_light(database):
    """Question 3b: the same thing, split by how bright it was.

    The metrics section asks for everything broken out by light, because a
    threshold that works at noon and fails at dusk averages out to a number
    that describes neither.
    """
    print("\nBy light level")
    print("--------------")

    rows = database.execute(
        f"""
        SELECT CASE
                 WHEN mean_luma IS NULL        THEN 'unknown'
                 WHEN mean_luma < {config.DUSK_LUMA} THEN 'dim'
                 ELSE 'daylight'
               END AS light,
               label,
               COUNT(*) AS n
          FROM truth
         WHERE status IS NOT NULL
         GROUP BY light, label
         ORDER BY light, n DESC
        """
    ).fetchall()

    current = None
    for row in rows:
        if row["light"] != current:
            current = row["light"]
            print(f"\n  {current} (mean_luma "
                  f"{'<' if current == 'dim' else '>='} {config.DUSK_LUMA})")
        print(f"    {row['label']:22s} {row['n']:6d}")


def check_the_checker(database, sample_size=200, seed=None):
    """Question 4: pick a random sample for a person to label by eye.

    Random, not "the interesting ones".  The point is to measure how often
    MegaDetector is right, and a sample chosen by how suspicious the frames
    look cannot measure that.

    Prints paths.  Deliberately does not write anything: hand labels go in
    via `label`, one at a time, by a person who has actually looked.
    """
    rows = database.execute(
        """
        SELECT path, label, max_animal_conf, camera_decision, mean_luma
          FROM truth
         WHERE status IS NOT NULL AND hand_label IS NULL
        """
    ).fetchall()

    if not rows:
        print("\nNothing detected yet to sample.")
        return []

    random.Random(seed).shuffle(rows)
    sample = rows[:sample_size]

    print(f"\nA random {len(sample)} frames to label by eye")
    print("-------------------------------------")
    print("  Look at these without reading the last two columns, write down")
    print("  animal or empty, then compare. That comparison is how far we")
    print("  get to trust everything else in this database.\n")

    for row in sample:
        luma = f"{row['mean_luma']:5.1f}" if row["mean_luma"] is not None \
            else "    ?"
        print(f"  {row['path']}  [{row['label']}, "
              f"conf {row['max_animal_conf'] or 0:.2f}, luma {luma}]")

    return [row["path"] for row in sample]


def uncertain_queue(database, limit=50):
    """The muddy middle, worst first: the actual hand-labelling worklist."""
    rows = database.execute(
        """
        SELECT path, max_animal_conf, camera_decision, mean_luma
          FROM truth
         WHERE label = 'uncertain' AND hand_label IS NULL
         ORDER BY max_animal_conf DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()

    print(f"\nUncertain band, {len(rows)} shown, highest confidence first")
    print("---------------------------------------------------------")

    for row in rows:
        print(f"  {row['path']}  conf {row['max_animal_conf'] or 0:.2f}  "
              f"camera said: {row['camera_decision'] or '?'}")

    return [row["path"] for row in rows]


def export_megadetector_json(database, destination, camera=None,
                             run_id=None):
    """Write results in MegaDetector's own JSON format.

    Worth the twenty lines because it is the lingua franca of camera-trap
    tooling: Timelapse, the standard review application, ingests this file
    directly, which means the hand-labelling in E1 can happen in a proper
    review UI instead of anything we would write ourselves.
    """
    import json

    run = (database.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
           .fetchone() if run_id else manifest_module.reference_run(database))

    if run is None:
        print("No run to export: pass a run id, or set a reference run.")
        return None

    arguments = [run["id"]]
    where = "WHERE r.run_id = ? AND r.status = 'ok'"
    if camera:
        where += " AND f.camera = ?"
        arguments.append(camera)

    frames = database.execute(
        f"""SELECT f.id, f.path, r.id AS result_id FROM frames f
            JOIN frame_results r ON r.frame_id = f.id {where}
            ORDER BY f.camera, f.day, f.captured_at""", arguments).fetchall()

    number_for = {name: number for number, name
                  in enumerate(("animal", "person", "vehicle"), start=1)}

    images = []
    for frame in frames:
        boxes = database.execute(
            "SELECT category, confidence, x, y, w, h FROM detections "
            "WHERE frame_result_id = ? ORDER BY confidence DESC",
            (frame["result_id"],)).fetchall()

        images.append({
            "file": frame["path"],
            # The format's own definition: the highest confidence of ANY
            # box in the frame, person and vehicle included.
            "max_detection_conf": round(
                max((box["confidence"] for box in boxes), default=0.0), 4),
            "detections": [{
                "category": str(number_for.get(box["category"], 0)),
                "conf": round(box["confidence"], 4),
                "bbox": [round(box["x"], 5), round(box["y"], 5),
                         round(box["w"], 5), round(box["h"], 5)],
            } for box in boxes],
        })

    document = {
        "images": images,
        "detection_categories": {"1": "animal", "2": "person",
                                 "3": "vehicle"},
        "info": {
            "detector": run["name"],
            "detection_completion_time": None,
            "format_version": "1.3",
            "photo_root": str(config.PHOTO_ROOT),
        },
    }

    with open(destination, "w") as handle:
        json.dump(document, handle, indent=1)

    print(f"Wrote {len(images)} images to {destination}")
    return destination


def by_deployment(database):
    """Question 5, which only the runs schema can ask: did a change help?

    Each camera run is one version of step8 on one camera.  Comparing them
    is the difference between "our false-positive rate is 1.7%" and "it was
    4% until the shadow rule landed on 26 August".  The first is a number;
    the second is a reason to keep working.

    Read the columns as rates only where the frame count is large enough to
    carry one --- a deployment with forty frames in it is telling you
    nothing.
    """
    print("\nBy deployment (one row per version of step8 in the woods)")
    print("--------------------------------------------------------")

    rows = database.execute(
        """
        SELECT r.name AS camera, r.code_version, r.id AS run_id,
               COUNT(*) AS frames,
               SUM(t.label = 'animal') AS animals,
               SUM(t.camera_decision IS NOT NULL) AS decided
          FROM runs r
          JOIN frame_results fr ON fr.run_id = r.id
          JOIN truth t ON t.frame_id = fr.frame_id
         WHERE r.kind = 'camera'
         GROUP BY r.id ORDER BY r.name, r.code_version
        """
    ).fetchall()

    if not rows:
        print("  no camera runs yet -- `scan` builds them from the CSVs")
        return

    for row in rows:
        wanted = database.execute(
            """SELECT decision, COUNT(*) n FROM frame_results
                WHERE run_id = ? AND decision IS NOT NULL
             GROUP BY decision""", (row["run_id"],)).fetchall()

        kept = sum(r["n"] for r in wanted
                   if bursts.classify_decision(r["decision"]) == "wanted")
        limited = sum(r["n"] for r in wanted
                      if bursts.classify_decision(r["decision"]) == "suppressed")

        print(f"\n  run {row['run_id']:3d}  {row['camera']:14s} "
              f"{row['code_version'] or 'unfingerprinted'}")
        print(f"       {row['frames']:6d} frames, "
              f"{row['animals'] or 0} with an animal in them")
        print(f"       photographed {kept}, rate-limited {limited}")


def compare(database, run_a, run_b):
    """Two runs over the same frames: where do they disagree?

    This is milestone E4 ("Rivals") in one query, and it is the reason the
    schema has a runs table at all.  Before, comparing two models meant
    copying the database aside and diffing two files by hand.
    """
    def describe(run_id):
        row = database.execute("SELECT * FROM runs WHERE id = ?",
                               (run_id,)).fetchone()
        if row is None:
            raise SystemExit(f"No run {run_id}. `trailcam runs` lists them.")
        return row

    first, second = describe(run_a), describe(run_b)

    print(f"Run {first['id']} ({first['name']}) "
          f"against run {second['id']} ({second['name']})")
    print("-" * 62)

    # Both runs' verdicts for every frame they have both seen, bucketed by
    # the same thresholds the confidence split uses.
    verdict = f"""
        CASE WHEN {{}}.status <> 'ok' THEN 'error'
             WHEN {{}}.max_animal_conf >= {config.ANIMAL_TRUTH} THEN 'animal'
             WHEN {{}}.max_person_conf >= {config.PERSON_TRUTH} THEN 'person'
             WHEN {{}}.max_animal_conf < {config.EMPTY_TRUTH} THEN 'empty'
             ELSE 'uncertain' END
    """

    rows = database.execute(
        f"""
        SELECT {verdict.format('a', 'a', 'a', 'a')} AS verdict_a,
               {verdict.format('b', 'b', 'b', 'b')} AS verdict_b,
               COUNT(*) AS n
          FROM frame_results a
          JOIN frame_results b ON b.frame_id = a.frame_id AND b.run_id = ?
         WHERE a.run_id = ?
         GROUP BY verdict_a, verdict_b
        """, (second["id"], first["id"])).fetchall()

    if not rows:
        print("  no frames in common")
        return

    total = sum(row["n"] for row in rows)
    agreed = sum(row["n"] for row in rows
                 if row["verdict_a"] == row["verdict_b"])

    print(f"\n  {total} frames seen by both, "
          f"{agreed} agreed ({100.0 * agreed / total:.1f}%)\n")

    labels = sorted({row["verdict_a"] for row in rows}
                    | {row["verdict_b"] for row in rows})
    counts = {(row["verdict_a"], row["verdict_b"]): row["n"] for row in rows}

    header = "".join(f"{label:>11s}" for label in labels)
    print(f"  {'run ' + str(first['id']):>12s} \\ run {second['id']}")
    print(f"  {'':12s}{header}")
    for a in labels:
        cells = "".join(f"{counts.get((a, b), 0):11d}" for b in labels)
        print(f"  {a:>12s}{cells}")

    print("\n  Rows are run {}, columns run {}. Off the diagonal is where "
          "they\n  disagree, and those frames are the ones worth looking at "
          "by eye.".format(first["id"], second["id"]))

    disagreements = database.execute(
        f"""
        SELECT f.path,
               a.max_animal_conf AS conf_a, b.max_animal_conf AS conf_b
          FROM frame_results a
          JOIN frame_results b ON b.frame_id = a.frame_id AND b.run_id = ?
          JOIN frames f ON f.id = a.frame_id
         WHERE a.run_id = ?
           AND {verdict.format('a', 'a', 'a', 'a')}
            <> {verdict.format('b', 'b', 'b', 'b')}
         ORDER BY ABS(COALESCE(a.max_animal_conf, 0)
                    - COALESCE(b.max_animal_conf, 0)) DESC
         LIMIT 10
        """, (second["id"], first["id"])).fetchall()

    if disagreements:
        print(f"\n  Worst disagreements:")
        for row in disagreements:
            print(f"    {row['path']}  "
                  f"{first['id']}: {row['conf_a'] or 0:.2f}  "
                  f"{second['id']}: {row['conf_b'] or 0:.2f}")


def everything(database):
    """The whole report, in the order the questions get asked."""
    coverage(database)
    split(database)
    against_the_camera(database)
    by_light(database)
    by_deployment(database)
