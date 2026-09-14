"""Finding the photographs the cameras chose to keep.

`bursts.py` finds the training frames -- the unbiased sample the motion
rules were not allowed to filter.  This module finds the other thing in
the archive: the photographs the rules *did* keep, which is where every
good picture of an animal is, and which are useless for grading the
rules for exactly that reason.  The two must never be confused, so a
frame from here carries `kind = 'photo'` and the evaluation queries
refuse to read it.

The layout, from evaluation-design.md:

    <camera>/<YYYY-MM-DD>/
      141530.jpg               the photograph
      141530.json              what the camera measured and decided
      141530_annotated.jpg     the same frame with boxes painted on
      training/                the bursts; bursts.py's business

Two things are skipped on purpose.  The `_annotated.jpg` copies would
double-count every sighting and feed MegaDetector a picture of somebody
else's boxes.  And `training/`, obviously.

The JSON sidecar is the camera's own account of the frame: when it was
taken, how bright it was, which rule fired (`trigger`), and the same
motion measurements that go into the CSV for a training frame.  The
oldest camera in the archive (wildlifecam1, August) wrote no sidecars at
all, so everything read from one is optional: a photograph with no
sidecar still gets a row, with the time taken from its filename.
"""

import json

from datetime import datetime
from pathlib import Path

from . import config
from .bursts import Frame, read_code_version


# The sidecar's motion block, flattened into the same metrics JSON a
# training frame gets from the CSV, under the same names where they
# coincide, so a query over `frame_results.metrics` reads both alike.
MOTION_FIELDS = {
    "changed_fraction": "changed_fraction",
    "largest_blob_fraction": "largest_fraction",
    "extent": "extent",
    "aspect": "aspect",
    "blob_range": "blob_range",
    "blob_edge": "blob_edge",
    "brightness_shift": "brightness_shift",
    "pixel_threshold": "pixel_threshold",
    "confirmations": "confirmations",
    "exposure_us": "exposure_us",
    "analogue_gain": "analogue_gain",
}


def _read_sidecar(path):
    """The camera's JSON for one photograph, or {} if there is none."""
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _parse_timestamp(day, filename):
    """103415.jpg + 2026-08-24 -> datetime, to the second."""
    return datetime.strptime(f"{day} {Path(filename).stem}",
                             "%Y-%m-%d %H%M%S")


def find_photos(camera=None, day=None, photo_root=None):
    """Every photograph the cameras kept, with what the camera said about it.

    Same shape as `bursts.find_frames`, with `kind = 'photo'`, so the
    manifest and the camera runs take them through the same code.
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

            code_version = read_code_version(day_directory)

            for image in sorted(day_directory.glob("*.jpg")):
                if image.name.endswith("_annotated.jpg"):
                    continue

                sidecar = _read_sidecar(image.with_suffix(".json"))
                motion = sidecar.get("motion") or {}

                captured_at = None
                if sidecar.get("time"):
                    try:
                        captured_at = datetime.fromisoformat(sidecar["time"])
                    except ValueError:
                        captured_at = None
                if captured_at is None:
                    try:
                        captured_at = _parse_timestamp(day_directory.name,
                                                       image.name)
                    except ValueError:
                        captured_at = datetime.fromtimestamp(
                            image.stat().st_mtime)

                metrics = {ours: motion[theirs]
                           for theirs, ours in MOTION_FIELDS.items()
                           if motion.get(theirs) is not None}

                # The on-board model's best guess rides along, as it does
                # for a training frame, because "the camera thought it
                # was a bench" is half the story of every sighting.
                ai = sidecar.get("ai") or {}
                detections = ai.get("detections") or []
                if detections:
                    best = max(detections,
                               key=lambda d: d.get("confidence", 0.0))
                    metrics["ai_class"] = best.get("class")
                    metrics["ai_confidence"] = best.get("confidence")

                area = motion.get("largest_blob_area")

                frames.append(Frame(
                    camera=camera_directory.name,
                    day=day_directory.name,
                    relative_path=str(image.relative_to(root)),
                    absolute_path=image,
                    captured_at=captured_at,
                    camera_decision=sidecar.get("trigger"),
                    mean_luma=motion.get("mean_luma"),
                    largest_area=int(area) if area is not None else None,
                    code_version=sidecar.get("code") or code_version,
                    metrics=metrics or None,
                    kind="photo",
                ))

    return frames
