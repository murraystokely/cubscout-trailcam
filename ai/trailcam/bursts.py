"""Finding the training bursts on disk, and what the camera thought of them.

Two jobs, and the second one is the interesting half.

Walking the directories is easy.  The part that makes E1 worth doing is
that every training frame also has a row in `measurements-<camera>.csv`
recording what the rules in the woods decided about that exact frame --
`quiet`, `waiting for confirmation`, `strong motion`, and so on.  Pair that
with what MegaDetector says and you have the thing we could never get
before: what the camera would have done with frames it was not allowed to
filter.

So this module reads both and hands back one object per frame.
"""

import csv

from collections import namedtuple
from datetime import datetime
from pathlib import Path

from . import config


# `relative_path` is stored rather than the absolute one, so the manifest
# survives the photo library moving or being read on another machine.
Frame = namedtuple("Frame", [
    "camera", "day", "relative_path", "absolute_path", "captured_at",
    "camera_decision", "mean_luma", "largest_area",
])


# The decisions in step8 that mean "the rules wanted this photograph",
# copied from its own vocabulary (step8_reject_shadows.py, rules 5 to 8:
# the four branches that set `save_now = True`).
#
# Hard-coding strings from another file is a coupling we should not have to
# live with, and E2 removes it: once the rules move out into `motion.py`,
# both sides import the same names.  Until then, this list is the seam, and
# `classify_decision` shouts if it meets a decision it does not recognise
# rather than quietly filing it as a rejection.
WANTED = frozenset((
    "strong motion",                        # rule 6: big, with structure
    "confirmed motion",                     # rule 7: still there a moment on
    "shadow, but the AI sees an animal",    # rule 5, overruled
    "small blob, the AI sees an animal",    # rule 8, vouched for
))

REJECTED = frozenset((
    "quiet",
    "too dark",
    "lighting change",
    "scene change",
    "wrong shape",
    "shadow",
    "waiting for confirmation",
))

# Appended by step8 when it wanted the photograph but something else
# stopped it being written.  These are a different kind of miss entirely --
# the thresholds were right and the housekeeping got in the way -- so they
# get their own bucket instead of being averaged into either side.
SUPPRESSIONS = ("(cooldown)", "(hourly limit)", "(disk full)", "(dry run)")


def classify_decision(decision):
    """'wanted' | 'suppressed' | 'rejected' | 'unknown' for a CSV decision.

    Why three buckets and not two: "confirmed motion (cooldown)" means the
    rules correctly spotted the animal and the rate limiter threw the
    photograph away.  Counting that as a threshold failure would send us
    off tuning the wrong number.
    """
    if not decision:
        return "unknown"

    for suffix in SUPPRESSIONS:
        if decision.endswith(suffix):
            base = decision[:-len(suffix)].strip()
            return "suppressed" if base in WANTED else "rejected"

    if decision in WANTED:
        return "wanted"
    if decision in REJECTED:
        return "rejected"

    return "unknown"


def _parse_timestamp(day, filename):
    """train_143015_287.jpg + 2026-08-24 -> datetime, to the millisecond.

    The milliseconds are in the filename on purpose (evaluation-design.md):
    the replay derives the real interval between frames from the names
    rather than assuming the loop kept to its cadence.  Nothing here needs
    that yet, but throwing the precision away now would be silly.
    """
    stem = Path(filename).stem                      # train_143015_287
    _, clock, milliseconds = stem.split("_", 2)

    return datetime.strptime(
        f"{day} {clock}.{milliseconds}", "%Y-%m-%d %H%M%S.%f"
    )


def _read_measurements(day_directory):
    """{filename: (decision, mean_luma, largest_area)} for one camera-day.

    The CSV is named for the camera's own hostname, which is not always the
    directory the laptop filed it under -- sync_cameras.py lets a camera be
    filed under a Scout's name, and this archive already has a directory
    called `camera-35`.  So glob for it instead of assuming the name.

    A missing or half-written CSV is not fatal: the frames are still worth
    running the detector over, they just have no baseline to be graded
    against.  Better a row with a NULL decision than no row at all.
    """
    measurements = {}

    for path in sorted(day_directory.glob("measurements-*.csv")):
        try:
            with open(path, newline="") as handle:
                for row in csv.DictReader(handle):
                    name = (row.get("file") or "").strip()
                    if not name:
                        continue

                    def number(field):
                        try:
                            return float(row[field])
                        except (KeyError, TypeError, ValueError):
                            return None

                    area = number("largest_area")

                    measurements[name] = (
                        (row.get("decision") or "").strip() or None,
                        number("mean_luma"),
                        int(area) if area is not None else None,
                    )
        except OSError:
            continue

    return measurements


def find_frames(camera=None, day=None, photo_root=None):
    """Every full-colour training frame under PHOTO_ROOT, with its CSV row.

    Only `training/train_*.jpg`.  Three things are deliberately left out:

      * the wildlife photographs themselves -- they are a biased sample,
        being exactly the frames the rules chose, and grading a detector on
        them is the mistake evaluation-design.md opens by warning about
      * `*_annotated.jpg` -- the same photograph with boxes painted on it,
        which would both double-count the sighting and feed MegaDetector a
        picture of somebody else's boxes
      * the lores `*.png` buffers -- 640x480 YUV, nothing identifiable in
        them; they are the replay's input, not the detector's
    """
    root = Path(photo_root or config.PHOTO_ROOT)
    if not root.is_dir():
        return []

    frames = []

    for camera_directory in sorted(p for p in root.iterdir() if p.is_dir()):
        if camera and camera_directory.name != camera:
            continue

        for day_directory in sorted(p for p in camera_directory.iterdir()
                                    if p.is_dir()):
            if day and day_directory.name != day:
                continue

            training = day_directory / "training"
            if not training.is_dir():
                continue

            measurements = _read_measurements(day_directory)

            for image in sorted(training.glob("train_*.jpg")):
                if image.name.endswith("_annotated.jpg"):
                    continue

                try:
                    captured_at = _parse_timestamp(day_directory.name,
                                                   image.name)
                except (ValueError, IndexError):
                    # A name we do not understand is a bug in our own
                    # filename convention, not a reason to skip the photo.
                    # Fall back to the file's own timestamp.
                    captured_at = datetime.fromtimestamp(image.stat().st_mtime)

                decision, mean_luma, largest_area = measurements.get(
                    image.name, (None, None, None))

                frames.append(Frame(
                    camera=camera_directory.name,
                    day=day_directory.name,
                    relative_path=str(image.relative_to(root)),
                    absolute_path=image,
                    captured_at=captured_at,
                    camera_decision=decision,
                    mean_luma=mean_luma,
                    largest_area=largest_area,
                ))

    return frames
