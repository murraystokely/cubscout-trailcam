#!/usr/bin/env python3
"""Step 7 -- wildlife trigger for the Raspberry Pi AI Camera.

This program decides one thing: is this frame worth keeping?  It saves
the photograph and a small JSON file beside it, and the laptop does the
real animal identification later with MegaDetector and SpeciesNet.

The camera does NOT try to name the animal.  The AI model built into the
sensor only knows 80 everyday objects and has never heard of a raccoon.
So the AI is used in one direction only: it can *promote* a weak motion
blob into a photograph, but it can never veto one.

While you are choosing thresholds for your own camera site, run:

    python3 -u step7_ai_motion_detection.py --dry-run

That saves no photographs.  Instead it writes one row of measurements
per check into a CSV file, so you can look at what your own patch of
woods really does all day and pick numbers from your own data instead of
guessing.
"""

import argparse
import csv
import json
import os
import shutil
import socket
import sys
import time

from datetime import datetime

import cv2
import numpy as np

from picamera2 import Picamera2
from picamera2.devices import IMX500


MODEL = (
    "/usr/share/imx500-models/"
    "imx500_network_ssd_mobilenetv2_fpnlite_320x320_pp.rpk"
)

PHOTO_DIR = "/var/www/html/photos"

CAMERA_NAME = socket.gethostname()


# ------------------------------------------------------------
# Camera streams
# ------------------------------------------------------------

# "main" is the photograph we keep.  4:3 uses the whole sensor.  Asking
# for a 16:9 size throws away the top and bottom of the view, which is
# the last thing a trail camera wants.
MAIN_SIZE = (2028, 1520)

# Picamera2 format names describe the byte order in memory, which is the
# REVERSE of the numpy channel order.  "RGB888" therefore hands OpenCV
# the BGR array it expects.  Using "BGR888" here saves every photograph
# with red and blue swapped.
MAIN_FORMAT = "RGB888"

# "lores" is a small copy the camera hardware produces for free.  Its Y
# plane already is a greyscale image, so motion detection costs us no
# resizing and no colour conversion at all.
LORES_SIZE = (320, 240)

MOTION_WIDTH, MOTION_HEIGHT = LORES_SIZE
MOTION_PIXELS = MOTION_WIDTH * MOTION_HEIGHT

JPEG_QUALITY = 88

# Each full-resolution buffer is about 9 MB, so keep only a few.
BUFFER_COUNT = 4


# ------------------------------------------------------------
# Motion detection settings
# ------------------------------------------------------------

# How different one pixel must be before we call it "changed".  On a
# dark night the sensor turns up its gain and invents differences of its
# own, so this is only a floor: the real threshold is raised to sit
# above whatever noise the frame actually has.
PIXEL_THRESHOLD = 25
NOISE_MULTIPLIER = 4

# ...but not without limit, or a big shadow would blind the camera for
# as long as it lasted.
MAX_PIXEL_THRESHOLD = 75

# Blob sizes are written as a fraction of the frame so they keep their
# meaning if you ever change LORES_SIZE.
#
# MIN_BLOB_FRACTION was measured, not guessed.  On wildlifecam4, pointed
# at the patio, a fern frond at the right-hand edge of the frame waved in
# the breeze all afternoon and produced blobs of 19 to 95 pixels, over and
# over, in the same spot.  Real motion at the same camera -- a person on
# the path, a hand near the lens -- produced 8,960 pixels and upwards.
# Two orders of magnitude apart, so the floor goes between them, nearer
# the weeds than the wildlife.
#
# Note what does NOT work here: waiting for a second sighting.  A leaf
# that gusts and drops does fail that test, but a frond in a steady
# breeze keeps moving, in one place, for hours -- so it confirms itself
# perfectly.  Persistence catches twitchy noise; only size catches a
# plant that genuinely will not sit still.
#
# The cost is real: this floor gives up on an animal smaller than about
# 150 px, which at this lens is a squirrel beyond ~10 m or a deer beyond
# ~40 m.  If a camera looks down a long trail rather than at a patio,
# measure its own site with --record and lower this for that camera.
MIN_BLOB_FRACTION = 0.00195       # ~150 px at 320x240
STRONG_BLOB_FRACTION = 0.0043     # ~330 px: big enough to save on sight

MIN_BLOB_AREA = int(MIN_BLOB_FRACTION * MOTION_PIXELS)
STRONG_BLOB_AREA = int(STRONG_BLOB_FRACTION * MOTION_PIXELS)

# Shape gates.  Blowing grass and swaying branches make thin, stringy,
# hollow blobs.  Animals make solid, compact ones.
#
# Nothing with a backbone is eight times longer than it is wide, so the
# aspect test applies to every blob, however big.  A grass stem stays a
# grass stem at any size.  The hollowness test is only used on small
# blobs, because a big animal half hidden behind brush really can come
# out sparse and we would rather keep it.
MIN_EXTENT = 0.30                 # blob area / bounding box area
MAX_ASPECT = 8.0                  # longest side / shortest side

# Here is the hard part.  One pixel covers about 0.36 cm for every metre
# of distance, so a 10 cm leaf 2 m away covers MORE pixels than a 1.5 m
# deer 30 m away.  Size alone can never tell them apart.  What does tell
# them apart is that a leaf blows back to where it started and an animal
# does not, so a small blob has to still be there on the next check.
CONFIRM_CHECKS = 2
CONFIRM_RADIUS = 40               # px the centre may travel between checks
CONFIRM_TIMEOUT = 1.5             # s before we forget a half-confirmed blob

# Whole-scene changes.  Lots of change is only suspicious when no single
# blob owns most of it.  One big lump is an animal standing close to the
# camera; change scattered over the whole frame is cloud shadow or wind.
BUSY_FRACTION = 0.35
DOMINANT_RATIO = 0.50

# When MOST of the frame moves the same way at once -- everything a bit
# darker together, everything a bit lighter together -- that is the light
# changing, not an animal.
#
# We measure it with the MIDDLE signed difference of the whole frame.
# "Signed" matters: grainy noise pushes just as many pixels up as down,
# so it cancels out to zero, while a passing cloud pushes every pixel the
# same way.  That one number tells the two apart.
#
# This also covers the camera adjusting its own exposure, which changes
# every pixel at once in exactly the same way.
LIGHTING_SHIFT = 10

# Below this average brightness the sensor is mostly amplifying its own
# noise, and nothing we saved would be usable anyway.
MIN_MEAN_LUMA = 25

# How long the background takes to forget the past, in seconds.
#
# Where nothing is happening it has to keep up with the world quietly
# drifting: the sun moving, shadows creeping across a patio, the camera
# settling a hair on soft ground.  Ten seconds keeps up with all of that.
BACKGROUND_TAU = 10.0

# Underneath whatever IS moving it learns four times more slowly, so an
# animal that stops to browse does not dissolve into the scenery while we
# are still watching it.
#
# But it does still learn, and that is the whole point.  An earlier
# version froze the moving region completely -- it only learned where the
# mask was clear.  A pixel that once looked different therefore stayed
# different for ever, so the tiniest camera shake wrote a permanent scar
# along every sharp edge in the scene, the scars grew into each other,
# and the camera photographed an empty patio 48 times in two minutes.
BACKGROUND_TAU_BUSY = 40.0

# ...and how fast it re-learns just after a lighting change.
SETTLE_ALPHA = 0.25
SETTLE_CHECKS = 8


# ------------------------------------------------------------
# Timing and rate limits
# ------------------------------------------------------------

LOOP_DELAY = 0.25                 # check four times a second
WARMUP_SECONDS = 5.0              # let exposure and white balance settle

SAVE_COOLDOWN = 2.0
MAX_SAVES_PER_HOUR = 240          # a stuck camera cannot fill the card

DISK_FULL_PERCENT = 95.0
DISK_CHECK_INTERVAL = 10.0

HEARTBEAT_INTERVAL = 300.0        # prove we are alive in journalctl

# ------------------------------------------------------------
# Training bursts
# ------------------------------------------------------------

# A detector cannot be judged using only the pictures it chose to keep.
# If it ignores every distant fox, its own photo album will never contain
# one, and it will look perfect.  So now and then the camera records a
# plain run of frames with the motion rules switched off, and those
# unbiased frames are what we test new ideas against offline.
#
# See ai/evaluation-design.md for what happens to them on the laptop.

RECORD_EVERY = 3600.0             # start a burst this often, in seconds
RECORD_LENGTH = 60.0              # keep recording for this long
RECORD_INTERVAL = 1.0             # save a frame this often during a burst
RECORD_PREFIX = "train"

# Whenever we keep a photograph we keep a second copy with the boxes
# drawn on: the blue one the motion rules found, the green ones the AI
# recognised.  Looking at those side by side is far and away the quickest
# way to see WHY the camera made a decision, which is the whole point of
# the exercise.
#
# The cost is real and worth knowing: it doubles the storage on the card
# and doubles the sync time.  --no-annotated turns it off if a card ever
# gets tight.
#
# One thing to remember for the laptop: ingest in ai/design.md walks
# <camera>/<date>/*.jpg, so it must skip anything ending in
# _annotated.jpg or every event will be counted twice.
SAVE_ANNOTATED = True


# ------------------------------------------------------------
# AI settings
# ------------------------------------------------------------

# The AI never decides whether we save, so these are only ever used to
# add evidence, not to take it away.
MIN_AI_CONFIDENCE = 0.25          # worth writing into the JSON
MIN_AI_RESCUE_CONFIDENCE = 0.40   # worth promoting a weak blob
MIN_AI_HINT_CONFIDENCE = 0.45     # worth flagging as probably an animal

# The AI recognises the furniture too.  A bench, a chair, a fence post is
# detected in every single frame, and its box is huge -- so a stray blob
# of noise that happens to land inside it would be "confirmed" by an
# object that has not moved all day.  To count as support, the AI's box
# has to be roughly the size of the thing that actually moved.
MAX_AI_BOX_RATIO = 20

# COCO has no deer, raccoon, squirrel or coyote, so a real animal here
# usually lands on the nearest thing the model does happen to know.
ANIMAL_CLASSES = {
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "teddy bear",
}


# ------------------------------------------------------------
# Command line
# ------------------------------------------------------------

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])

parser.add_argument("--dry-run", action="store_true",
                    help="save no photographs; write measurements to CSV "
                         "so you can choose your own thresholds")

parser.add_argument("--no-annotated", action="store_true",
                    help="do not save the _annotated.jpg copy with the "
                         "boxes drawn on (saves half the card space)")

parser.add_argument("--record", action="store_true",
                    help=f"also record a {RECORD_LENGTH:.0f} second burst of "
                         f"training frames every "
                         f"{RECORD_EVERY / 60:.0f} minutes, whether anything "
                         f"is happening or not")

parser.add_argument("--photo-dir", default=PHOTO_DIR,
                    help=f"where photographs go (default: {PHOTO_DIR})")

options = parser.parse_args()

photo_dir = options.photo_dir
dry_run = options.dry_run
save_annotated = SAVE_ANNOTATED and not options.no_annotated
recording_enabled = options.record


# ------------------------------------------------------------
# Set up the AI Camera
# ------------------------------------------------------------

imx500 = IMX500(MODEL)

intrinsics = imx500.network_intrinsics
intrinsics.update_with_defaults()

labels = intrinsics.labels

picam2 = Picamera2(imx500.camera_num)

config = picam2.create_preview_configuration(
    main={"size": MAIN_SIZE, "format": MAIN_FORMAT},
    lores={"size": LORES_SIZE, "format": "YUV420"},
    controls={"FrameRate": intrinsics.inference_rate},
    buffer_count=BUFFER_COUNT,
)

print("Loading AI model...")

# The progress bar is for people, not for the systemd journal.
if sys.stdout.isatty():
    imx500.show_network_fw_progress_bar()

picam2.configure(config)
picam2.start()


# ------------------------------------------------------------
# Reading the AI results
# ------------------------------------------------------------

def get_ai_detections(metadata):
    """Return whatever the sensor's own AI model thinks it can see.

    Every box comes back in full-resolution photograph pixels, the same
    coordinates we use for the motion box, so the laptop never has to
    guess which picture a box belongs to.
    """
    outputs = imx500.get_outputs(metadata, add_batch=True)

    if outputs is None:
        return []

    boxes = outputs[0][0]
    scores = outputs[1][0]
    classes = outputs[2][0]

    _, input_height = imx500.get_input_size()

    if intrinsics.bbox_normalization:
        boxes = boxes / input_height

    if intrinsics.bbox_order == "xy":
        boxes = boxes[:, [1, 0, 3, 2]]

    detections = []

    for box, score, category in zip(boxes, scores, classes):
        if score < MIN_AI_CONFIDENCE:
            continue

        name = labels[int(category)]

        x, y, w, h = imx500.convert_inference_coords(box, metadata, picam2)

        detections.append({
            "class": name,
            "confidence": float(score),
            "box": [int(x), int(y), int(w), int(h)],
        })

    return detections


def ai_supports(detections, motion_box):
    """True if the AI sees SOMETHING solid where the motion was.

    We deliberately accept any class, not just the animal ones.  A
    raccoon might come back as "cat", a turkey as "bird", a fawn as
    "dog".  All we are asking is whether the model agrees that an object
    is there, which is exactly the question a leaf fails.
    """
    _, _, motion_w, motion_h = motion_box
    motion_area = max(motion_w * motion_h, 1)

    for detection in detections:
        if detection["confidence"] < MIN_AI_RESCUE_CONFIDENCE:
            continue

        _, _, w, h = detection["box"]

        # Ignore the scenery: see MAX_AI_BOX_RATIO above.
        if w * h > MAX_AI_BOX_RATIO * motion_area:
            continue

        if boxes_overlap(detection["box"], motion_box):
            return True

    return False


def boxes_overlap(first, second):
    ax, ay, aw, ah = first
    bx, by, bw, bh = second

    return (ax < bx + bw and bx < ax + aw
            and ay < by + bh and by < ay + ah)


def to_main_coords(box):
    """Scale a box from the small motion image up to the photograph."""
    x, y, w, h = box

    scale_x = MAIN_SIZE[0] / MOTION_WIDTH
    scale_y = MAIN_SIZE[1] / MOTION_HEIGHT

    return [int(x * scale_x), int(y * scale_y),
            int(w * scale_x), int(h * scale_y)]


# ------------------------------------------------------------
# Remembering a blob from one check to the next
# ------------------------------------------------------------

# The blob we are waiting on, as (x, y, when, count) -- or None.
pending = None


def seen_again(centroid, when):
    """Count how many checks in a row a blob has been in the same place.

    This is the single most useful trick in the whole program.  A leaf
    blows one way and then blows back, so it is rarely in the same spot
    twice.  An animal keeps being there.  Counting is enough.

    We do not clear this when a check finds nothing, only when it goes
    stale -- so an animal that slips behind a tree trunk for a moment
    still gets counted when it comes out the other side.
    """
    global pending

    count = 1

    if pending is not None:
        x, y, seen_at, seen_count = pending

        near = (abs(centroid[0] - x) <= CONFIRM_RADIUS
                and abs(centroid[1] - y) <= CONFIRM_RADIUS)

        if near and when - seen_at <= CONFIRM_TIMEOUT:
            count = seen_count + 1

    pending = (centroid[0], centroid[1], when, count)

    return count


# ------------------------------------------------------------
# Measurement log for choosing thresholds (--dry-run)
# ------------------------------------------------------------

MEASUREMENT_FIELDS = [
    "time",
    "file",
    "mean_luma",
    "pixel_threshold",
    "changed_fraction",
    "largest_area",
    "largest_fraction",
    "extent",
    "aspect",
    "brightness_shift",
    "confirmations",
    "exposure_us",
    "analogue_gain",
    "ai_class",
    "ai_confidence",
    "decision",
]

measurement_file = None
measurement_writer = None
measurement_day = None


def log_measurement(now, row):
    """Append one row of numbers, starting a new file each day."""
    global measurement_file, measurement_writer, measurement_day

    day = now.strftime("%Y-%m-%d")

    if day != measurement_day:
        if measurement_file is not None:
            measurement_file.close()

        day_directory = f"{photo_dir}/{day}"
        os.makedirs(day_directory, exist_ok=True)

        path = f"{day_directory}/measurements-{CAMERA_NAME}.csv"
        is_new = not os.path.exists(path)

        measurement_file = open(path, "a", newline="")
        measurement_writer = csv.DictWriter(measurement_file,
                                            fieldnames=MEASUREMENT_FIELDS)

        if is_new:
            measurement_writer.writeheader()

        measurement_day = day

    measurement_writer.writerow(row)
    measurement_file.flush()


# ------------------------------------------------------------
# Saving
# ------------------------------------------------------------

def save_event(now, image, decision, measurements, ai_detections):
    """Write the photograph and its JSON description."""
    day_directory = f"{photo_dir}/{now.strftime('%Y-%m-%d')}"
    os.makedirs(day_directory, exist_ok=True)

    base_filename = now.strftime("%H%M%S")

    original_filename = f"{day_directory}/{base_filename}.jpg"
    json_filename = f"{day_directory}/{base_filename}.json"

    cv2.imwrite(original_filename, image,
                [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])

    animal_hint = any(
        detection["class"] in ANIMAL_CLASSES
        and detection["confidence"] >= MIN_AI_HINT_CONFIDENCE
        for detection in ai_detections
    )

    information = {
        "camera": CAMERA_NAME,
        "time": now.isoformat(),

        "image": {
            "file": os.path.basename(original_filename),
            "width": MAIN_SIZE[0],
            "height": MAIN_SIZE[1],
        },

        "trigger": decision,

        # Every box in this file, motion and AI alike, is in
        # full-resolution photograph pixels.
        "motion": measurements,

        "ai": {
            "model": os.path.basename(MODEL),
            "animal_hint": animal_hint,
            "detections": ai_detections,
        },
    }

    with open(json_filename, "w") as file:
        json.dump(information, file, indent=2)

    if save_annotated:
        save_annotated_copy(day_directory, base_filename, image,
                            measurements["box"], ai_detections)

    return original_filename, animal_hint


def save_training_frame(now, image):
    """Save a frame the motion rules did NOT choose.

    Two things about the name matter.  It lives in its own training/
    subdirectory, so the laptop's "walk every *.jpg" step cannot mistake
    these for wildlife photographs.  And it carries milliseconds, so a
    program replaying the burst offline knows exactly how far apart the
    frames really were instead of assuming.
    """
    training_directory = (f"{photo_dir}/{now.strftime('%Y-%m-%d')}/training")
    os.makedirs(training_directory, exist_ok=True)

    name = (f"{RECORD_PREFIX}_{now.strftime('%H%M%S')}"
            f"_{now.microsecond // 1000:03d}.jpg")

    cv2.imwrite(f"{training_directory}/{name}", image,
                [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])

    return name


def save_annotated_copy(day_directory, base_filename, image,
                        motion_box, ai_detections):
    """Save a second copy of the photograph with the boxes drawn on.

    Blue is the patch of connected pixels the motion rules found.  Green
    is whatever the camera's own AI recognised, with how sure it was.
    When the camera saves a picture of nothing, this is the copy that
    tells you which rule to go and argue with.

    It sits next to the original as <time>_annotated.jpg.
    """
    annotated = image.copy()

    if motion_box is not None:
        x, y, w, h = motion_box

        cv2.rectangle(annotated, (x, y), (x + w, y + h), (255, 0, 0), 3)
        cv2.putText(annotated, "MOTION", (x, max(y - 10, 24)),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 0, 0), 3)

    for detection in ai_detections:
        x, y, w, h = detection["box"]

        label = f"{detection['class']} {detection['confidence']:.0%}"

        cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 255, 0), 3)
        cv2.putText(annotated, label, (x, max(y - 10, 24)),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 3)

    cv2.imwrite(f"{day_directory}/{base_filename}_annotated.jpg", annotated,
                [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])


# ------------------------------------------------------------
# Let the camera settle, then learn the background
# ------------------------------------------------------------

print(f"Warming up for {WARMUP_SECONDS:.0f} seconds...")

time.sleep(WARMUP_SECONDS)

request = picam2.capture_request()

try:
    lores = request.make_array("lores")
    gray = lores[:MOTION_HEIGHT, :MOTION_WIDTH]
    background = cv2.GaussianBlur(gray, (5, 5), 0).astype("float32")
finally:
    request.release()

OPEN_KERNEL = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
CLOSE_KERNEL = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))

BACKGROUND_ALPHA = min(1.0, LOOP_DELAY / BACKGROUND_TAU)
BACKGROUND_ALPHA_BUSY = min(1.0, LOOP_DELAY / BACKGROUND_TAU_BUSY)

settle_checks = 0

# The next burst starts immediately, so a tuning session gets data at
# once instead of an hour from now.
record_next = time.time() if recording_enabled else None
record_until = 0.0
record_last = 0.0

last_save_time = 0.0
save_times = []

disk_ok = True
last_disk_check = 0.0
last_heartbeat = time.time()

print(f"Watching for wildlife on {CAMERA_NAME}"
      f"{' (dry run: saving nothing)' if dry_run else ''}...")
print(f"  blob >= {MIN_BLOB_AREA} px needs confirming, "
      f">= {STRONG_BLOB_AREA} px saves on sight")

if recording_enabled:
    print(f"  recording a {RECORD_LENGTH:.0f} second training burst every "
          f"{RECORD_EVERY / 60:.0f} minutes")


# ------------------------------------------------------------
# Main wildlife loop
# ------------------------------------------------------------

while True:

    try:
        now = datetime.now()
        moment = time.time()

        # ----------------------------------------------------
        # Protect the filesystem, but stay alive
        # ----------------------------------------------------

        if moment - last_disk_check >= DISK_CHECK_INTERVAL:
            total, used, free = shutil.disk_usage("/")
            percent_used = used / total * 100

            was_ok = disk_ok
            disk_ok = percent_used < DISK_FULL_PERCENT

            if was_ok and not disk_ok:
                print(f"Filesystem is {percent_used:.1f}% full. "
                      f"Not saving until photographs are synced away.")
            elif disk_ok and not was_ok:
                print("There is room again. Saving resumed.")

            last_disk_check = moment

        # ----------------------------------------------------
        # One frame, and the AI result that came with it
        # ----------------------------------------------------

        request = picam2.capture_request()

        try:
            metadata = request.get_metadata()

            # The Y plane of the small stream is already greyscale.
            lores = request.make_array("lores")
            gray = cv2.GaussianBlur(
                lores[:MOTION_HEIGHT, :MOTION_WIDTH], (5, 5), 0)

            # ------------------------------------------------
            # Compare against the learned background
            # ------------------------------------------------

            background_image = background.astype("uint8")

            # Keep the SIGN of the difference.  It is what separates
            # "everything got darker together" (the light changed) from
            # "some pixels went each way" (something moved).
            signed = (gray.astype(np.int16)
                      - background_image.astype(np.int16))

            shift = float(np.median(signed))

            # The typical size of a difference IS the noise level, so let
            # it set the threshold.  A calm afternoon keeps 25; a grainy
            # night raises it by itself.
            noise = float(np.median(np.abs(signed)))

            pixel_threshold = min(
                MAX_PIXEL_THRESHOLD,
                max(PIXEL_THRESHOLD, int(NOISE_MULTIPLIER * noise + 5)))

            difference = cv2.absdiff(gray, background_image)

            _, mask = cv2.threshold(difference, pixel_threshold, 255,
                                    cv2.THRESH_BINARY)

            # Open first to rub out lone speckles, then close to join a
            # broken-up animal into one shape.  Closing does not inflate
            # the area the way a plain dilate does, so the blob sizes
            # below mean what they say.
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, OPEN_KERNEL)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, CLOSE_KERNEL)

            changed_fraction = cv2.countNonZero(mask) / MOTION_PIXELS

            # ------------------------------------------------
            # Find the biggest patch of connected pixels
            # ------------------------------------------------

            count, _, stats, centroids = cv2.connectedComponentsWithStats(
                mask, connectivity=8)

            largest_area = 0
            largest_box = None
            largest_centroid = None

            for index in range(1, count):
                area = int(stats[index, cv2.CC_STAT_AREA])

                if area > largest_area:
                    largest_area = area
                    largest_box = (
                        int(stats[index, cv2.CC_STAT_LEFT]),
                        int(stats[index, cv2.CC_STAT_TOP]),
                        int(stats[index, cv2.CC_STAT_WIDTH]),
                        int(stats[index, cv2.CC_STAT_HEIGHT]),
                    )
                    largest_centroid = (float(centroids[index][0]),
                                        float(centroids[index][1]))

            largest_fraction = largest_area / MOTION_PIXELS

            if largest_box is not None:
                _, _, box_w, box_h = largest_box
                extent = largest_area / float(max(box_w * box_h, 1))
                aspect = max(box_w, box_h) / float(max(min(box_w, box_h), 1))
            else:
                extent = 0.0
                aspect = 0.0

            # ------------------------------------------------
            # How bright is it?
            # ------------------------------------------------

            mean_luma = float(gray.mean())

            # Not used to decide anything -- just handy to know later,
            # when we are looking at a photograph and wondering why it
            # came out blurry.
            exposure_us = int(metadata.get("ExposureTime", 0))
            analogue_gain = float(metadata.get("AnalogueGain", 1.0))

            # ------------------------------------------------
            # Decide, in order.  The first rule that matches wins.
            #
            #   1. too dark to see anything      -> skip
            #   2. the light changed             -> skip
            #   3. the whole treeline is moving  -> skip
            #   4. too small, or the wrong shape -> skip
            #   5. a big blob                    -> SAVE
            #   6. a small blob, seen twice      -> SAVE
            #   7. a small blob the AI agrees on -> SAVE
            #   8. anything else                 -> wait and see
            #
            # Notice that the AI only ever appears in rule 7, and only
            # ever says yes.  It can turn a maybe into a photograph; it
            # can never stop one.  That is deliberate: the model knows 80
            # everyday objects and not one of them is a deer, so a "no"
            # from it means nothing at all.
            # ------------------------------------------------

            decision = "quiet"
            save_now = False
            ai_detections = []
            confirmations = 0

            big_enough = largest_area >= MIN_BLOB_AREA
            strong = largest_area >= STRONG_BLOB_AREA

            # A grass stem is thin at any size, so the aspect test applies
            # to every blob.  The hollowness test only applies to small
            # ones: a big animal half hidden behind brush can genuinely
            # come out sparse, and we would rather keep it.
            right_shape = (aspect <= MAX_ASPECT
                           and (strong or extent >= MIN_EXTENT))

            motion_box = to_main_coords(largest_box) if largest_box else None

            if mean_luma < MIN_MEAN_LUMA:
                # 1. At night, with no infra-red lamp, the sensor turns
                # its gain right up and mostly photographs its own noise.
                decision = "too dark"
                pending = None

            elif abs(shift) >= LIGHTING_SHIFT:
                # 2. Most of the frame moved the same way at once: a
                # cloud, or dusk, or the camera re-metering.
                #
                # An animal close enough to fill over half the view would
                # also land here and be skipped.  That is a fair trade:
                # to get that close it had to walk through the middle
                # distance first, and we already photographed it there.
                decision = "lighting change"
                pending = None

            elif (changed_fraction >= BUSY_FRACTION
                    and largest_fraction < DOMINANT_RATIO * changed_fraction):
                # 3. Plenty of change, but broken into scraps with no
                # single lump owning most of it -- wind through the whole
                # treeline.  An animal is ONE lump, however big, so a deer
                # standing close falls through to rule 5 instead.
                decision = "scene change"
                pending = None

            elif not (big_enough and right_shape):
                # 4. Too small to be anything, or too thin and stringy to
                # be an animal.
                if big_enough:
                    decision = "wrong shape"

            elif strong:
                # 5. Big and solid.  Do not think about it, take the
                # picture.
                decision = "strong motion"
                save_now = True

            else:
                # Small.  This is where a distant deer and a nearby leaf
                # look exactly alike, so we need a second opinion.
                confirmations = seen_again(largest_centroid, moment)

                if confirmations >= CONFIRM_CHECKS:
                    # 6. Still there, in the same place, a moment later.
                    # The leaf blew back; this did not.
                    decision = "confirmed motion"
                    save_now = True

                else:
                    # 7. Only seen once so far -- but if the AI can see an
                    # object right there, that is good enough. This is
                    # what catches an animal far away on the very first
                    # frame instead of a quarter-second later.
                    ai_detections = get_ai_detections(metadata)

                    if ai_supports(ai_detections, motion_box):
                        decision = "small blob, AI agrees"
                        save_now = True
                    else:
                        # 8. Wait and see.
                        decision = "waiting for confirmation"

            # ------------------------------------------------
            # Rate limits
            # ------------------------------------------------

            if save_now:
                save_times = [t for t in save_times if moment - t < 3600.0]

                if dry_run:
                    decision += " (dry run)"
                    save_now = False
                elif not disk_ok:
                    decision += " (disk full)"
                    save_now = False
                elif moment - last_save_time < SAVE_COOLDOWN:
                    decision += " (cooldown)"
                    save_now = False
                elif len(save_times) >= MAX_SAVES_PER_HOUR:
                    decision += " (hourly limit)"
                    save_now = False

            # ------------------------------------------------
            # Is this frame part of a training burst?
            # ------------------------------------------------

            if record_next is not None and moment >= record_next:
                record_until = moment + RECORD_LENGTH
                record_next = moment + RECORD_EVERY

                print(f"{now:%H:%M:%S} recording a "
                      f"{RECORD_LENGTH:.0f} second training burst")

            recording = (moment < record_until
                         and moment - record_last >= RECORD_INTERVAL
                         and disk_ok)

            # Copying the full-resolution frame is the expensive part, so
            # only do it once we know the frame is being kept -- either as
            # a wildlife photograph or as a training frame.
            image = (request.make_array("main")
                     if (save_now or recording) else None)

        finally:
            request.release()

        # ----------------------------------------------------
        # Update the background
        # ----------------------------------------------------

        if decision in ("lighting change", "scene change"):
            settle_checks = SETTLE_CHECKS

        if settle_checks > 0:
            # The whole scene really did change, so relearn it quickly.
            cv2.accumulateWeighted(gray, background, SETTLE_ALPHA)
            settle_checks -= 1
        else:
            # Always learn a little, everywhere, including underneath the
            # thing that is moving.  This line is what stops a mistake
            # from becoming permanent.
            cv2.accumulateWeighted(gray, background, BACKGROUND_ALPHA_BUSY)

            # Then learn again, faster, wherever nothing is moving, so
            # the background keeps up with the light and the weather.
            cv2.accumulateWeighted(gray, background, BACKGROUND_ALPHA,
                                   mask=cv2.bitwise_not(mask))

        # ----------------------------------------------------
        # Save
        # ----------------------------------------------------

        if save_now:
            if not ai_detections:
                ai_detections = get_ai_detections(metadata)

            measurements = {
                "largest_blob_area": largest_area,
                "largest_blob_fraction": largest_fraction,
                "changed_fraction": changed_fraction,
                "extent": extent,
                "aspect": aspect,
                "brightness_shift": shift,
                "pixel_threshold": pixel_threshold,
                "confirmations": confirmations,
                "mean_luma": mean_luma,
                "exposure_us": exposure_us,
                "analogue_gain": analogue_gain,
                "box": motion_box,
            }

            filename, animal_hint = save_event(now, image, decision,
                                               measurements, ai_detections)

            save_times.append(moment)
            last_save_time = moment

            print(f"{now:%H:%M:%S} {decision}: blob={largest_area} px "
                  f"({largest_fraction:.2%}), changed={changed_fraction:.2%} "
                  f"-> {filename}")

            for detection in ai_detections:
                print(f"    AI: {detection['class']} "
                      f"{detection['confidence']:.2f}")

            if not ai_detections:
                print("    AI: no recognised objects")

            last_heartbeat = moment

        # ----------------------------------------------------
        # Training burst
        # ----------------------------------------------------

        training_file = ""

        if recording:
            training_file = save_training_frame(now, image)
            record_last = moment

        # ----------------------------------------------------
        # Measurements, for choosing thresholds
        # ----------------------------------------------------

        # Every training frame is logged with what the CURRENT rules
        # decided about it.  That gives the offline evaluation its
        # baseline for free: this is what the camera in the woods would
        # have done with a frame it was not allowed to filter.

        if dry_run or training_file:
            if not ai_detections and largest_area >= MIN_BLOB_AREA:
                ai_detections = get_ai_detections(metadata)

            best = max(ai_detections, key=lambda d: d["confidence"],
                       default=None)

            log_measurement(now, {
                "time": now.isoformat(timespec="milliseconds"),
                "file": training_file,
                "mean_luma": round(mean_luma, 1),
                "pixel_threshold": pixel_threshold,
                "changed_fraction": round(changed_fraction, 5),
                "largest_area": largest_area,
                "largest_fraction": round(largest_fraction, 5),
                "extent": round(extent, 3),
                "aspect": round(aspect, 2),
                "brightness_shift": round(shift, 1),
                "confirmations": confirmations,
                "exposure_us": exposure_us,
                "analogue_gain": round(analogue_gain, 2),
                "ai_class": best["class"] if best else "",
                "ai_confidence": round(best["confidence"], 3) if best else "",
                "decision": decision,
            })

            if dry_run and decision != "quiet":
                print(f"{now:%H:%M:%S} {decision}: blob={largest_area} px "
                      f"({largest_fraction:.2%}), "
                      f"changed={changed_fraction:.2%}, "
                      f"extent={extent:.2f}, aspect={aspect:.1f}, "
                      f"shift={shift:+.0f}, "
                      f"threshold={pixel_threshold}")

                last_heartbeat = moment

        # ----------------------------------------------------
        # Prove we are still alive
        # ----------------------------------------------------

        if moment - last_heartbeat >= HEARTBEAT_INTERVAL:
            print(f"{now:%H:%M:%S} still watching: "
                  f"luma={mean_luma:.0f}, threshold={pixel_threshold}, "
                  f"saves this hour={len(save_times)}")

            last_heartbeat = moment

        time.sleep(LOOP_DELAY)

    except KeyboardInterrupt:
        break

    except Exception as error:
        # One bad frame must never take a camera off the air for the
        # rest of the weekend.
        print(f"Recovered from an error: {error!r}", file=sys.stderr)
        time.sleep(1.0)


picam2.stop()

if measurement_file is not None:
    measurement_file.close()

print("Program finished safely.")
