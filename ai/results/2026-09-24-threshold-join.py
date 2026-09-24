"""What would the 599 px floor and the luma-25 gate have cost?

For every step-9 photograph (code bf0a63149ef4: 301 px, no gate) the
sidecar records the biggest blob and the mean luma of the look that saved
it -- exact per frame, no join by the second needed.  MDv5a's verdict on
the same frame comes from the manifest.  The CSV is used for one thing
only: the population of *all* saves, which the surviving files
under-count because of the overwrite bug.

Usage: threshold.py <run_id>
"""
import csv
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ARCHIVE = Path("/Volumes/datasets/trailcam/photos")
RUN = int(sys.argv[1])
CAMERAS = ("wildlifecam9", "wildlifecam13", "wildlifecam14")
STEP9_OLD = "bf0a63149ef4"
OLD_FLOOR, NEW_FLOOR, DARK = 301, 599, 25

db = sqlite3.connect("/Users/murray/git/cubscouts/ai/data/manifest.sqlite")
db.row_factory = sqlite3.Row


def band(blob):
    return ("<301" if blob < OLD_FLOOR else
            "301-598" if blob < NEW_FLOOR else ">=599")


def csv_saves(camera):
    """(biggest_blob, mean_luma) for every save the CSV recorded."""
    out = []
    for path in ARCHIVE.glob(f"{camera}/*/measurements-{camera}.csv"):
        text = path.read_text(errors="replace").replace("\x00", "")
        reader = csv.DictReader(text.splitlines())
        if "biggest_blob" not in (reader.fieldnames or []):
            continue
        for r in reader:
            if not (r.get("time") or "").startswith("2026"):
                continue
            if r.get("saved") == "1":
                out.append((float(r["biggest_blob"] or 0),
                            float(r["mean_luma"] or 0)))
    return out


def sidecar(camera, day, stem):
    try:
        with open(ARCHIVE / camera / day / f"{stem}.json") as f:
            d = json.load(f)
        m = d.get("motion") or {}
        return d.get("code"), m.get("biggest_blob"), m.get("mean_luma")
    except (OSError, ValueError):
        return None, None, None


summary = []
for camera in CAMERAS:
    frames = db.execute(
        """SELECT f.path, f.day, r.max_animal_conf AS animal,
                  r.max_person_conf AS person
             FROM frames f JOIN frame_results r ON r.frame_id = f.id
            WHERE r.run_id = ? AND f.camera = ? AND f.kind = 'photo'
              AND r.status = 'ok'""", (RUN, camera)).fetchall()

    joined, other_code, missing = [], 0, 0
    for fr in frames:
        code, blob, luma = sidecar(camera, fr["day"], Path(fr["path"]).stem)
        if code != STEP9_OLD:
            other_code += 1
            continue
        if blob is None or luma is None:
            missing += 1
            continue
        joined.append((fr, float(blob), float(luma)))

    saves = csv_saves(camera)
    print(f"\n===== {camera}: {len(frames)} photographs detected; "
          f"{len(joined)} are step 9 at 301 px with blob and luma in the "
          f"sidecar; {other_code} other programs; {missing} no numbers")
    print(f"  every save in the CSV ({len(saves)}): blob bands "
          f"{dict(Counter(band(b) for b, _ in saves))}; "
          f"dark (<{DARK}): {sum(1 for _, l in saves if l < DARK)}")
    print(f"  surviving step-9 files ({len(joined)}): blob bands "
          f"{dict(Counter(band(b) for _, b, _ in joined))}; "
          f"dark: {sum(1 for _, _, l in joined if l < DARK)}")

    for label, lo, hi in (("animal >= 0.8", 0.8, 1.01),
                          ("animal 0.5-0.8", 0.5, 0.8),
                          ("animal 0.2-0.5", 0.2, 0.5)):
        sub = [(f, b, l) for f, b, l in joined if lo <= (f["animal"] or 0) < hi]
        print(f"  {label}: {len(sub)} frames; blob bands "
              f"{dict(Counter(band(b) for _, b, _ in sub))}; "
              f"dark: {sum(1 for _, _, l in sub if l < DARK)}")
        if label != "animal 0.2-0.5":
            for f, b, l in sorted(sub, key=lambda t: t[1])[:10]:
                print(f"      blob {b:6.0f}  luma {l:5.1f}  conf "
                      f"{f['animal']:.2f}  {f['path']}")
    animals = [(f, b, l) for f, b, l in joined if (f["animal"] or 0) >= 0.8]
    summary.append((camera, len(animals),
                    sum(1 for _, b, _ in animals if b < NEW_FLOOR),
                    sum(1 for _, _, l in animals if l < DARK),
                    len(saves),
                    sum(1 for b, _ in saves if b < NEW_FLOOR),
                    sum(1 for _, l in saves if l < DARK)))

print("\n===== summary (step 9 at 301 px only)")
print(f"{'camera':14} {'animals>=.8':>11} {'of which <599':>14} "
      f"{'dark':>5} | {'all saves':>9} {'<599 saves':>10} {'dark saves':>10}")
for row in summary:
    print(f"{row[0]:14} {row[1]:11d} {row[2]:14d} {row[3]:5d} | "
          f"{row[4]:9d} {row[5]:10d} {row[6]:10d}")
