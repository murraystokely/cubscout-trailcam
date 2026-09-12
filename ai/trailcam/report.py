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
    """Question 1: how much has been done."""
    totals = manifest_module.counts(database)

    print("Frames in the manifest")
    print("----------------------")
    print(f"  training frames   {totals['frames'] or 0}")
    print(f"  through detector  {totals['detected'] or 0}")
    print(f"  unreadable        {totals['errors'] or 0}")
    print(f"  cameras           {totals['cameras'] or 0}")
    print(f"  days              {totals['days'] or 0}  "
          f"({totals['first_day']} .. {totals['last_day']})")

    rows = database.execute(
        """
        SELECT camera,
               COUNT(*) AS frames,
               SUM(detected_at IS NOT NULL) AS detected,
               COUNT(DISTINCT detector) AS detectors
          FROM frames GROUP BY camera ORDER BY camera
        """
    ).fetchall()

    if len(rows) > 1:
        print()
        for row in rows:
            print(f"  {row['camera']:16s} {row['detected'] or 0:6d}"
                  f" / {row['frames']:6d}")

    detectors = database.execute(
        "SELECT detector, COUNT(*) AS n FROM frames "
        "WHERE detector IS NOT NULL GROUP BY detector"
    ).fetchall()

    if len(detectors) > 1:
        print("\n  Careful: more than one detector in this manifest --")
        for row in detectors:
            print(f"    {row['detector']}: {row['n']} frames")
        print("  Rerun with --redo to put them all on the same footing.")


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
         WHERE detected_at IS NOT NULL
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
         WHERE detected_at IS NOT NULL
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
         WHERE detected_at IS NOT NULL AND hand_label IS NULL
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


def export_megadetector_json(database, destination, camera=None):
    """Write results in MegaDetector's own JSON format.

    Worth the twenty lines because it is the lingua franca of camera-trap
    tooling: Timelapse, the standard review application, ingests this file
    directly, which means the hand-labelling in E1 can happen in a proper
    review UI instead of anything we would write ourselves.
    """
    import json

    where = "WHERE detected_at IS NOT NULL AND detect_error IS NULL"
    arguments = []
    if camera:
        where += " AND camera = ?"
        arguments.append(camera)

    frames = database.execute(
        f"SELECT id, path FROM frames {where} "
        "ORDER BY camera, day, captured_at", arguments).fetchall()

    number_for = {name: number for number, name
                  in enumerate(("animal", "person", "vehicle"), start=1)}

    images = []
    for frame in frames:
        boxes = database.execute(
            "SELECT category, confidence, x, y, w, h FROM detections "
            "WHERE frame_id = ? ORDER BY confidence DESC",
            (frame["id"],)).fetchall()

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
            "detector": config.DETECTOR,
            "detection_completion_time": None,
            "format_version": "1.3",
            "photo_root": str(config.PHOTO_ROOT),
        },
    }

    with open(destination, "w") as handle:
        json.dump(document, handle, indent=1)

    print(f"Wrote {len(images)} images to {destination}")
    return destination


def everything(database):
    """The whole report, in the order the questions get asked."""
    coverage(database)
    split(database)
    against_the_camera(database)
    by_light(database)
