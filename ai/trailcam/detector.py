"""MegaDetector, behind a thin interface.

`design.md` asks for the detector to sit behind an interface so MDv5 and
MDv6 -- or something else entirely -- can be swapped without touching the
pipeline.  This module is that interface.  It is deliberately small: one
class, one method, one plain result type.

    detector = MegaDetector()
    boxes = detector.detect(Path("train_143015_287.jpg"))

Why an interface for a single implementation?  Because E4 in
evaluation-design.md is "re-run the same data through alternatives", and the
alternatives arrive as second implementations of exactly this.  Writing the
seam now costs ten lines; retrofitting it later means touching every caller.

A note on what MegaDetector is, since a Scout will ask.  It is not a species
identifier.  It answers one question -- "is there an animal, a person or a
vehicle here, and where" -- and it was trained on tens of millions of real
trail-camera photographs, which is why it is good at exactly the pictures
our cameras take: bad angles, small distant animals, harsh light.  The
camera in the woods runs a 320x320 SSD MobileNet that has never heard of a
raccoon and calls a crow a bench.  That gap is the whole reason this pass
exists.
"""

import hashlib
import os
import shutil

from collections import namedtuple
from pathlib import Path

from . import config


# One box.  Coordinates are MegaDetector's own convention, passed straight
# through so nothing has to be converted back for the export: fractions of
# the frame in [0, 1], with (x, y) the TOP-LEFT corner and (w, h) the size.
#
# That is not the convention step8 uses in its JSON (absolute pixels), and
# the mismatch is a real source of bugs, so it is written down in both
# places rather than assumed.
Box = namedtuple("Box", ["category", "confidence", "x", "y", "w", "h",
                         "crop_path"])


# MegaDetector numbers its classes; we store the words, because a database
# full of '1' and '2' is a database nobody can read in six months.
CATEGORIES = {"1": "animal", "2": "person", "3": "vehicle"}


def resolve_weights(name):
    """Turn a model name like "MDV5A" into a local file, kept in ai/models.

    Two things wrong with letting the `megadetector` package fetch these
    itself, both met the first time this was run:

      * it downloads into `/tmp/megadetector_models`, and /tmp is cleared
        on reboot, so the next overnight run starts by fetching 280 MB
        again
      * its downloader has no read timeout.  Ours stalled at 95 MB of 280
        and sat there for seventy minutes using no CPU, which looks
        exactly like a slow model load and is not

    So the file is fetched here instead, with a timeout, into `ai/models/`
    where it survives a reboot.  An existing path is passed straight
    through, which is how a model that is not a known name gets used.
    """
    from megadetector.detection.run_detector import (
        known_models, model_string_to_model_version)

    if Path(name).exists():
        return name

    version = model_string_to_model_version.get(name.lower(), name.lower())
    described = known_models.get(version)

    if described is None:
        # Not a name we can resolve.  Hand it over unchanged and let the
        # package produce its own, better, error message.
        return name

    config.ensure_directories()
    local = config.MODEL_DIR / described["url"].rsplit("/", 1)[-1]

    if not local.exists():
        _download(described["url"], local, described.get("md5"))

    return str(local)


def _download(url, destination, expected_md5=None, timeout=60):
    """Fetch one large file, with a timeout, checking it afterwards.

    Written to a `.part` file and renamed at the end, so an interrupted
    download can never be mistaken for a finished one -- the same reason
    the pass commits per frame rather than at the end.
    """
    import urllib.request

    partial = destination.with_suffix(destination.suffix + ".part")

    print(f"Fetching {url}")

    digest = hashlib.md5()
    downloaded = 0

    with urllib.request.urlopen(url, timeout=timeout) as response:
        total = int(response.headers.get("Content-Length") or 0)

        with open(partial, "wb") as handle:
            while True:
                block = response.read(1 << 20)
                if not block:
                    break

                handle.write(block)
                digest.update(block)
                downloaded += len(block)

                if total and downloaded % (32 << 20) < (1 << 20):
                    print(f"  {downloaded >> 20} of {total >> 20} MB")

    # The package publishes an md5 for every model, so checking it costs
    # four lines and turns "the download was truncated" from a baffling
    # stack trace into a sentence.
    if expected_md5 and digest.hexdigest() != expected_md5:
        partial.unlink()
        raise RuntimeError(
            f"{destination.name} downloaded corrupt (md5 "
            f"{digest.hexdigest()}, expected {expected_md5}); "
            f"deleted, run it again")

    shutil.move(partial, destination)
    print(f"Weights are in {destination}")


class MegaDetector:
    """The real thing, from the `megadetector` package on PyPI.

    Construction downloads the weights on first use (about 280 MB for
    MDv5a) and takes a few seconds to warm up, so build it once and hand it
    every frame -- never once per frame.
    """

    def __init__(self, model=None, threads=None, device=None):
        self.name = model or config.DETECTOR

        # Torch defaults to every core, which makes the laptop unusable
        # while an overnight pass runs.  Set this BEFORE torch builds its
        # thread pool, i.e. before the import below does anything.
        threads = threads or config.THREADS
        os.environ.setdefault("OMP_NUM_THREADS", str(threads))

        # Imported here, not at module scope, so that `trailcam status` and
        # `trailcam report` work on a machine with no torch installed.
        # Those two commands only read the manifest, and making them
        # require a 1 GB dependency would be rude.
        import torch

        from megadetector.detection.run_detector import load_detector

        torch.set_num_threads(threads)

        self.weights = resolve_weights(self.name)

        # Left alone the package picks cuda, then mps, then cpu.  That is
        # the right default; `device` exists so a benchmark can pin one
        # machine to each in turn and compare them honestly.
        options = {"device": device} if device else None

        self.model = load_detector(self.weights, detector_options=options)
        self.threads = threads
        self.device = str(getattr(self.model, "device", "unknown"))

    def detect(self, image_path, minimum_confidence=None):
        """Boxes for one frame, above `minimum_confidence`.

        Raises on an unreadable file; the caller records that against the
        frame and carries on, because one truncated JPEG in five thousand
        must not end an overnight run.
        """
        from megadetector.visualization.visualization_utils import load_image

        threshold = (config.MIN_STORED_CONFIDENCE
                     if minimum_confidence is None else minimum_confidence)

        image = load_image(str(image_path))

        result = self.model.generate_detections_one_image(
            image, str(image_path), detection_threshold=threshold)

        boxes = []
        for detection in result.get("detections", []):
            x, y, w, h = detection["bbox"]
            boxes.append(Box(
                category=CATEGORIES.get(detection["category"],
                                        detection["category"]),
                confidence=float(detection["conf"]),
                x=float(x), y=float(y), w=float(w), h=float(h),
                crop_path=None,
            ))

        # Biggest confidence first, so `boxes[0]` is the one a person cares
        # about and the crop written for a frame is its best animal.
        boxes.sort(key=lambda b: -b.confidence)
        return boxes


def write_crop(image_path, box, destination):
    """Save the padded contents of one box, for a person or SpeciesNet.

    Returns the path written, or None if the box was too small to be worth
    looking at.  Pillow only -- no OpenCV on the laptop side, so the
    dependency list stays short.
    """
    from PIL import Image

    with Image.open(image_path) as image:
        width, height = image.size

        # Normalised -> pixels, with the margin added and then clamped to
        # the frame.  An animal at the edge of the picture is exactly the
        # case where the padding would otherwise run off the sensor.
        margin_x = box.w * config.CROP_MARGIN
        margin_y = box.h * config.CROP_MARGIN

        left = max(0.0, box.x - margin_x) * width
        top = max(0.0, box.y - margin_y) * height
        right = min(1.0, box.x + box.w + margin_x) * width
        bottom = min(1.0, box.y + box.h + margin_y) * height

        if max(right - left, bottom - top) < config.CROP_MIN_PIXELS:
            return None

        crop = image.crop((int(left), int(top), int(right), int(bottom)))

        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        crop.convert("RGB").save(destination, "JPEG", quality=90)

    return destination
