#!/usr/bin/env python3
"""Step 8 -- telling a branch's shadow from an animal.

Step 7 photographs anything that moves and is the right size.  Under a tree
that turns out to be mostly shadow: a day on the patio gave 750 photographs,
71 of which had a bird in them, and nine in ten of the rest were the shadow
of a branch sliding across the concrete.

The obvious fix is a rule about shape -- shadows come out as wide flat
slabs.  It is also wrong, and the data cannot show you that: the only animal
we had photographed was a crow, which is compact, so a shape rule looked
perfect while quietly ruling out a fox with its tail out, a cougar, or a
weasel.

So step 8 measures texture instead, which says nothing about shape:

    range - the spread of brightness inside the blob.  A real object has
            light parts and dark parts.  A shadow is the SAME ground,
            uniformly dimmed, so its spread stays small.
    edge  - the strongest edges inside the blob.  An animal has a hard
            silhouette against the ground.  A shadow has a soft penumbra.

Measured on that day, requiring both keeps every one of the 71 crow frames
and drops nearly three quarters of the rest.

Step 8 is also where the camera stops being a lesson and starts being
equipment, because a rule tuned by guesswork is no better than the guess.
It records unbiased training bursts so the numbers can be measured from real
frames, writes a measurements CSV so a day in the woods can be argued about
with evidence, keeps rate limits and a disk guard so a windy afternoon
cannot fill a card, and stamps a fingerprint of its own source into every
photograph so "is this camera running what I think I copied to it" has a
one-line answer.

That last part is more than one step's worth of ideas, and would read better
split across a step 9 and 10 than bundled here.

Read [`step7_ai_motion_detection.py`](step7_ai_motion_detection.py) first.
Everything here rests on it: a learned background rather than the previous
frame, connected pixels rather than a count of changed ones, and the shape
tests.  What step 7 does NOT do is notice that the thing which moved was a
shadow.

While you are choosing thresholds for your own camera site, run:

    python3 -u step8_reject_shadows.py --dry-run

which saves no photographs but writes one row of measurements per check, so
you can look at what your own patch of woods really does all day and pick
numbers from your own data instead of guessing.
"""

import argparse
import csv
import hashlib
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


def code_fingerprint():
    """A short hash of this very file.

    Every photograph records it, and it is printed at start-up.  That way
    a picture can always be traced back to the exact code that chose to
    keep it -- and, just as usefully, you can tell at a glance whether the
    camera is running what you think you copied to it.  Deploying to ten
    Raspberry Pis by hand, that question comes up more than you would
    like.
    """
    try:
        with open(os.path.abspath(__file__), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except Exception:
        return "unknown"


CODE_VERSION = code_fingerprint()


def board_model():
    """Which Raspberry Pi is this?  Empty string if we cannot tell."""
    try:
        with open("/proc/device-tree/model") as handle:
            return handle.read().rstrip("\0").strip()
    except OSError:
        return ""


BOARD = board_model()

# The Pi Zero 2 W has 512 MB against a Pi 5's several gigabytes, and after
# the GPU split the ARM sees about 415 MB of it.  Measured on
# wildlifecam10: two thirds of this program was swapped out to the SD
# card and every numpy operation became a disk read.
#
# So on that board we keep smaller photographs and fewer of them in
# flight.  Between them those two settings are worth about 27 MB, which
# is the difference between running in memory and running from swap.
SMALL_BOARD = "Zero 2" in BOARD


# ------------------------------------------------------------
# Camera streams
# ------------------------------------------------------------

# "main" is the photograph we keep.  4:3 uses the whole sensor.  Asking
# for a 16:9 size throws away the top and bottom of the view, which is
# the last thing a trail camera wants.
# Neither of these two touches a motion threshold, which is why they are
# safe to change per board: nothing in the decision chain ever reads the
# "main" stream.  It is only the photograph we keep.
#
# LORES_SIZE deliberately does NOT change.  SHADOW_MIN_RANGE is measured
# in grey levels and SHADOW_MIN_EDGE in grey levels per pixel, and
# neither is scaled by MOTION_SCALE -- so moving the motion resolution
# would silently change what the shadow rule does on a board we have
# never measured.
MAIN_SIZE = (1520, 1140) if SMALL_BOARD else (2028, 1520)

# Picamera2 format names describe the byte order in memory, which is the
# REVERSE of the numpy channel order.  "RGB888" therefore hands OpenCV
# the BGR array it expects.  Using "BGR888" here saves every photograph
# with red and blue swapped.
MAIN_FORMAT = "RGB888"

# "lores" is a small copy the camera hardware produces for free.  Its Y
# plane already is a greyscale image, so motion detection costs us no
# resizing and no colour conversion at all.
# A patio only needs 320x240, but the real job is a trail, where animals
# are further away.  Resolution does NOT separate an animal from a weed
# any better -- both grow together, and the ratio between them stays the
# same at any size.  What it buys is reach at the small end, because the
# cleanup kernels below are a fixed number of pixels and rub out anything
# only a few across:
#
#     320 wide : deer at 30 m = 14x9 px,  squirrel at 10 m = 7x4 px
#     640 wide : deer at 30 m = 28x18px,  squirrel at 10 m = 14x8px
#
# So this is a distance setting, not an accuracy setting.
LORES_SIZE = (640, 480)

MOTION_WIDTH, MOTION_HEIGHT = LORES_SIZE

# Every setting below that is measured in pixels was chosen on a 320-wide
# frame.  Scaling them here means changing LORES_SIZE moves how FAR the
# camera can see, and nothing else -- a blob of the same real-world size
# still counts the same.
MOTION_SCALE = MOTION_WIDTH / 320.0


def odd(value):
    """Nearest odd number >= 3.  OpenCV kernels have to be odd."""
    return max(3, int(round(value)) | 1)


BLUR_KERNEL = odd(5 * MOTION_SCALE)
OPEN_SIZE = odd(3 * MOTION_SCALE)
CLOSE_SIZE = odd(7 * MOTION_SCALE)
MOTION_PIXELS = MOTION_WIDTH * MOTION_HEIGHT

JPEG_QUALITY = 88

# Each full-resolution buffer is about 9 MB, so keep only a few -- and
# on a small board, fewer still.
BUFFER_COUNT = 2 if SMALL_BOARD else 4


# ------------------------------------------------------------
# Which of these numbers travel, and which do not
# ------------------------------------------------------------
#
# Some of what follows is physics and will hold anywhere.  Some was
# measured in one back garden, pointed at one patio, on two days, with a
# crow as the only animal that ever turned up.  It matters which is
# which, because this camera is going to be moved.
#
# Travels:
#   the two-speed background, and why it must always keep learning
#   the signed-median test for "the light changed"
#   "one big lump is an object, scattered change is wind"
#   a shadow is the same ground uniformly dimmed, so it has little
#     internal range and a soft edge -- an object has both
#   blob thresholds as FRACTIONS of the frame, so they survive a change
#     of resolution
#
# Measured here, and to be re-measured after the camera moves:
#   MIN_BLOB_FRACTION      - set between a fern (19-95 px) and a crow
#                            (862 px and up).  A different site has a
#                            different smallest nuisance.
#   SHADOW_MIN_RANGE/EDGE  - a black crow on grey concrete in sunshine.
#                            A pale animal on pale ground in flat light
#                            has less of both.
#   RECORD_MIN_LUMA        - daylight here measured about 120.
#   LORES_SIZE             - a distance setting: how far away the
#                            smallest animal you care about will be.
#
# The training bursts exist precisely so the second list can be measured
# again rather than argued about.  After moving the camera, give it a day
# and look at the CSV before changing any of them.

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
MIN_BLOB_FRACTION = 0.00098       # ~300 px at 640x480
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

# Note on MAX_ASPECT: it is deliberately loose, and stays loose.  A crow is
# compact and tightening this to 3 would have removed 40% of a day's false
# positives at no cost to the crow -- but a fox with its tail out is about
# 3.1 times longer than tall, a cougar 3.3, a weasel 4 or 5, and the blob
# can include the animal's own cast shadow on top of that.  A rule fitted
# to the proportions of the one animal we happened to photograph would
# throw away exactly the animals worth having.  The texture tests below do
# the same job without any assumption about shape.

# Here is the hard part.  One pixel covers about 0.36 cm for every metre
# of distance, so a 10 cm leaf 2 m away covers MORE pixels than a 1.5 m
# deer 30 m away.  Size alone can never tell them apart.  What does tell
# them apart is that a leaf blows back to where it started and an animal
# does not, so a small blob has to still be there on the next check.
CONFIRM_CHECKS = 2
CONFIRM_RADIUS = int(40 * MOTION_SCALE)   # px the centre may travel per check
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

# Telling a shadow from an animal, without guessing at its shape.
#
# A whole day under a tree produced 750 photographs, and nine out of ten
# were the shadow of a branch sliding across the concrete.  Two things
# separate those from an animal, and neither cares how long the animal is:
#
#   range - the spread of brightness inside the blob.  A real object has
#           light parts and dark parts.  A shadow is the SAME concrete,
#           uniformly dimmed, so its spread stays small.
#   edge  - the strongest edges inside the blob.  An animal has a hard
#           silhouette against the ground.  A shadow has a soft penumbra.
#
# Measured on that day: every one of the 71 crow frames had a range above
# 144 and edges above 500, while the median shadow managed 103 and 107.
# The thresholds sit well below the bird and well above the shadow.
#
# A blob that fails these is thrown away, and rule 5 clears `pending`
# too, so a frame judged a shadow also resets the confirmation count of
# whatever was standing there.  A pale animal in flat light could
# plausibly fail both tests, and this is the only rule in the program
# that can discard an animal in silence.
#
# So it is the only rule the AI is allowed to overrule.  If the model
# recognises a LIVE ANIMAL where the blob is -- a whitelisted class, see
# ai_promotes() -- the picture is taken anyway.  That is a thin net: on
# 25 August it agreed with 4 of 31 crow frames.  But it caught none of
# the 240 false positives that day either, so it costs nothing to hang.
#
# Two other things keep the miss measurable rather than invisible: the
# training bursts record every frame with the rules switched off, and a
# rejection that came close to passing is written to the measurements
# CSV whatever time of day it happens (see LOG_NEAR_MISS).
SHADOW_MIN_RANGE = 110
SHADOW_MIN_EDGE = 250

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

# This is ON by default, so a camera starts collecting the moment the
# file is copied across, with nothing else to remember.  The first burst
# begins immediately rather than an hour later.
RECORD_BY_DEFAULT = True

RECORD_EVERY = 3600.0             # start a burst this often, in seconds
RECORD_LENGTH = 30.0              # keep recording for this long
RECORD_PREFIX = "train"

# A burst is recorded twice over, because the two jobs want different
# pictures and the first afternoon of collecting made that obvious.
#
# 1311 unbiased frames came back from a quiet patio.  99.3% of them were
# "quiet", and within a burst each frame differed from the first by a
# median of 0.22% of its pixels.  Fifty-seven near-identical 682 KB
# photographs an hour is a lot of sync for very little new information.
#
# What the MOTION rules need is the little 320x240 grey frame -- because
# that is literally all they ever look at.  Storing the big colour one
# and shrinking it later is 23 times the bytes AND less faithful, since
# it replays a JPEG of a downscale rather than the plane the camera
# actually handed us.  PNG, so it is exact.
RECORD_LORES_INTERVAL = 0.0       # 0 = every single check (the loop rate)

# What SPECIES identification needs is the big colour one, and it does
# not need many: MegaDetector on the laptop wants a recognisable animal,
# not a smooth film.
RECORD_FULL_INTERVAL = 10.0       # seconds between full-resolution frames

# ...and one straight away whenever anything at all stirs, however small.
# Animals are rare.  On a quiet patio this costs nothing, and when
# something finally does walk past it is the difference between having a
# training set and having a training set with an animal in it.  The
# threshold is deliberately far below the one that keeps a photograph, so
# it catches things the rules themselves would ignore.
#
# This does NOT bias the measurements: the little frames keep recording
# every check regardless, so we can still see what the rules missed.
RECORD_MOTION_FRACTION = 0.0003   # ~92 px at 640x480, a sixth of the floor
RECORD_MOTION_AREA = int(RECORD_MOTION_FRACTION * MOTION_PIXELS)
RECORD_MOTION_INTERVAL = 1.0      # at most one a second while it lasts

# After dark the sensor gives us a black rectangle with noise in it, and
# nobody can identify an animal in that -- neither MegaDetector nor a
# Scout.  So bursts wait for the light.  This is higher than
# MIN_MEAN_LUMA, which is only asking "can we tell motion from noise";
# here we are asking "would this picture be worth looking at".  Daylight
# on the patio measured about 120.
RECORD_MIN_LUMA = 40
RECORD_DARK_RETRY = 600.0         # try again in ten minutes, to catch dawn

# Sizes measured on real frames from wildlifecam4:
#
#   full 2028x1520 colour JPEG    682 KB
#   lores 320x240 grey PNG         54 KB   (exact, what the rules see)
#
#   30 s burst, lores at 4 Hz + full every 10 s  =  ~8 MB
#   hourly, all day                              = ~200 MB
#
# The old settings -- 60 s of full-resolution frames every 10 minutes --
# came to 5.6 GB a day for the same information.

# Know what this costs before leaving it running.  At roughly 450 KB a
# frame, one burst a minute in six:
#
#     60 frames an hour  x  6 bursts  =   360 frames  ~=  160 MB an hour
#                                                        3.8 GB a day
#
# That is sized for a tuning session of an hour or two, not for a season
# in the woods.  Before leaving a camera out unattended, put RECORD_EVERY
# back to 3600 (one burst an hour, ~650 MB a day) or set RECORD_BY_DEFAULT
# to False.  The disk guard stops training frames at 95% full either way,
# so a forgotten setting cannot fill a card, but it can make the sync
# slow and dull.

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

# The AI may only ever say YES.  It can turn a blob we were going to
# throw away into a photograph; it can never stop one.
MIN_AI_CONFIDENCE = 0.25          # worth writing into the JSON
MIN_AI_HINT_CONFIDENCE = 0.40     # worth flagging as probably an animal

# ...and it may only say yes about a LIVE ANIMAL.  This is a whitelist,
# and the whitelist is the whole design: an earlier version asked only
# "does the model agree an object is there", which the bench answered
# yes to in every frame of every day, and the two saves it ever won us
# were both the bench vouching for a blob of noise.  Nothing that is not
# in ANIMAL_CLASSES gets a vote now, so the furniture cannot vote at all.
#
# Measured on 25 August 2026: nine animal-class detections in 271
# photographs, all nine on frames with a real crow in them, none on any
# of the 240 false positives.  It agreed with only 4 of the 31 crow
# frames, so it is a safety net and not a detector -- but a net with
# nothing false in it is worth hanging under the shadow rule.
MIN_AI_PROMOTE_CONFIDENCE = 0.40


# ------------------------------------------------------------
# Watching the shadow rule from outside the training bursts
# ------------------------------------------------------------

# A rejection we cannot see is a rejection we cannot argue with.  The
# measurements CSV is written only for training-burst frames, and a
# burst is 30 seconds in every hour -- under 1% of the day -- so better
# than ninety-nine times in a hundred, when the shadow rule throws
# something away, no record of it survives.
#
# That is exactly the wrong blind spot to have over the one rule that
# can discard an animal in silence.  So a blob that was rejected as a
# shadow but came CLOSE to passing gets a row of its own, whatever time
# of day it is.  These rows have an empty `file` column, which is what
# tells them apart from burst rows.
#
# Not every rejection: a shadow that fails by a mile teaches nothing,
# and there are thousands of those.  Only the near misses.
LOG_NEAR_MISS = True
NEAR_MISS_EDGE = 150              # cf. SHADOW_MIN_EDGE = 250
NEAR_MISS_RANGE = 80              # cf. SHADOW_MIN_RANGE = 110

# ...and a cap, because the estimate above came from one day in which a
# sunlit bench dominated the sample, and a windy morning could plausibly
# be an order of magnitude worse.  Roughly a megabyte a day at this rate,
# against six hundred of photographs.
MAX_NEAR_MISS_PER_HOUR = 400

# The AI recognises the furniture too, and a bench box covers a sixth of
# the scene.  Even a whitelisted class has to be about the size of what
# actually moved before it counts as agreement.
MAX_AI_BOX_RATIO = 20

# COCO has no deer, raccoon, squirrel, fox or coyote, so a real animal here
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

parser.add_argument("--no-record", action="store_true",
                    help=f"do not record the {RECORD_LENGTH:.0f} second burst "
                         f"of training frames taken every "
                         f"{RECORD_EVERY / 60:.0f} minutes whether anything is "
                         f"happening or not")

parser.add_argument("--photo-dir", default=PHOTO_DIR,
                    help=f"where photographs go (default: {PHOTO_DIR})")

options = parser.parse_args()

photo_dir = options.photo_dir
dry_run = options.dry_run
save_annotated = SAVE_ANNOTATED and not options.no_annotated
recording_enabled = RECORD_BY_DEFAULT and not options.no_record


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

    This is metadata and nothing more.  Since the AI was taken out of the
    decision it cannot cause a photograph to be taken -- and it must not be
    able to stop one either.  Anything that goes wrong in here therefore
    degrades to "the AI saw nothing" rather than escaping: an exception
    would reach the loop's outer handler, which would skip the save, and
    the camera would go on looking healthy while quietly never saving
    another picture.  That is the worst way for a trail camera to fail,
    because nothing about it looks like failure.

    Every box comes back in full-resolution photograph pixels, the same
    coordinates we use for the motion box, so the laptop never has to
    guess which picture a box belongs to.
    """
    try:
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

            # The label list and the network's class indices come from
            # different files.  If they ever disagree, an IndexError here would
            # escape into the outer handler and the camera would keep running
            # while silently never saving another photograph -- the worst way
            # to fail.  Name the class by number instead.
            index = int(category)
            name = labels[index] if 0 <= index < len(labels) else f"class{index}"

            x, y, w, h = imx500.convert_inference_coords(box, metadata, picam2)

            detections.append({
                "class": name,
                "confidence": float(score),
                "box": [int(x), int(y), int(w), int(h)],
            })

        return detections
    except Exception as error:
        print(f"AI metadata unavailable ({error!r}); saving anyway",
              file=sys.stderr)
        return []


def blob_texture(raw_gray, box):
    """Return (range, edge) for the pixels inside a blob's bounding box.

    range - the spread of brightness inside the blob.  A real object has
            light parts and dark parts.  A shadow is the SAME ground,
            uniformly dimmed, so its spread stays small.
    edge  - the strongest edges inside the blob.  An animal has a hard
            silhouette; a shadow has a soft penumbra.

    Both are measured on the UNBLURRED frame: the blur that motion
    detection runs on would soften exactly the hard silhouette we are
    looking for, and hide the difference we want.

    Neither says anything about the SHAPE of the thing, which matters --
    a fox with its tail out, or a weasel, is far longer than it is tall,
    and a rule about proportions would throw exactly those away.
    """
    x, y, w, h = box

    patch = raw_gray[y:y + h, x:x + w]

    if patch.size < 25:
        return 0.0, 0.0

    low, high = np.percentile(patch, (5, 95))

    patch = patch.astype(np.float32)
    gx = cv2.Sobel(patch, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(patch, cv2.CV_32F, 0, 1, ksize=3)

    return float(high - low), float(np.percentile(np.hypot(gx, gy), 99))


def boxes_overlap(first, second):
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    return (ax < bx + bw and bx < ax + aw
            and ay < by + bh and by < ay + ah)


def ai_promotes(detections, motion_box):
    """True if the AI recognises a live animal where the motion is.

    Three things have to hold together, and the first is the one that
    matters: the class must be on the ANIMAL_CLASSES whitelist.  A crow
    comes back as "bird" on a good day and as "cat" or "dog" on a bad
    one, and all three are on the list; "bench" and "chair" never will
    be, whatever they score.

    The other two are sanity: it has to be sure enough, and its box has
    to be roughly the size of the thing that moved rather than a piece
    of furniture that happens to contain it.

    Both boxes are in full-resolution photograph pixels.
    """
    if motion_box is None:
        return False

    _, _, motion_w, motion_h = motion_box
    motion_area = max(motion_w * motion_h, 1)

    for detection in detections:
        if detection["class"] not in ANIMAL_CLASSES:
            continue

        if detection["confidence"] < MIN_AI_PROMOTE_CONFIDENCE:
            continue

        _, _, w, h = detection["box"]

        if w * h > MAX_AI_BOX_RATIO * motion_area:
            continue

        if boxes_overlap(detection["box"], motion_box):
            return True

    return False


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
    "blob_range",
    "blob_edge",
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
        "code": CODE_VERSION,
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


def training_name(now, suffix):
    """<time>_<milliseconds>.<suffix>, in its own training/ subdirectory.

    The subdirectory keeps these out of the laptop's "walk every *.jpg"
    step, so they cannot be mistaken for wildlife photographs.  The
    milliseconds let a program replaying the burst offline know exactly
    how far apart the frames really were instead of assuming.
    """
    training_directory = f"{photo_dir}/{now.strftime('%Y-%m-%d')}/training"
    os.makedirs(training_directory, exist_ok=True)

    return (f"{training_directory}/{RECORD_PREFIX}_{now.strftime('%H%M%S')}"
            f"_{now.microsecond // 1000:03d}.{suffix}")


def save_training_lores(now, raw_lores):
    """Save exactly the buffer the camera handed us, losslessly.

    Unblurred and uncompressed, so replaying it offline runs the very
    same pipeline over the very same pixels.

    We keep the COLOUR planes as well, even though today's rules only
    read brightness, because it costs 30% more and cannot be recovered
    later.  Two things we may well want it for:

      * A shadow keeps its colour and only changes brightness.  That is
        the standard way to tell a cloud passing over from an animal
        walking past, and it is the one false positive we gave up on.
      * Green leaves against a brown animal separate far better in
        colour than in grey.

    The file is the raw YUV420 buffer, so it reads back as a tall thin
    grey image.  To get a picture out of it:

        buf = cv2.imread(name, cv2.IMREAD_GRAYSCALE)
        grey   = buf[:240]                                     # what the rules saw
        colour = cv2.cvtColor(buf, cv2.COLOR_YUV2BGR_I420)     # the full frame
    """
    name = training_name(now, "png")
    cv2.imwrite(name, raw_lores)
    return os.path.basename(name)


def save_training_frame(now, image):
    """Save a full-resolution colour frame for the laptop to identify."""
    training_directory = f"{photo_dir}/{now.strftime('%Y-%m-%d')}/training"
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
    background = cv2.GaussianBlur(gray, (BLUR_KERNEL, BLUR_KERNEL), 0).astype("float32")
finally:
    request.release()

OPEN_KERNEL = cv2.getStructuringElement(cv2.MORPH_RECT, (OPEN_SIZE, OPEN_SIZE))
CLOSE_KERNEL = cv2.getStructuringElement(cv2.MORPH_RECT, (CLOSE_SIZE, CLOSE_SIZE))

BACKGROUND_ALPHA = min(1.0, LOOP_DELAY / BACKGROUND_TAU)
BACKGROUND_ALPHA_BUSY = min(1.0, LOOP_DELAY / BACKGROUND_TAU_BUSY)

settle_checks = 0

# The next burst starts immediately, so a tuning session gets data at
# once instead of an hour from now.
record_next = time.time() if recording_enabled else None
record_until = 0.0
record_last = 0.0
record_lores_last = 0.0
waiting_for_light = False

last_save_time = 0.0
save_times = []
near_miss_times = []

disk_ok = True
last_disk_check = 0.0
last_heartbeat = time.time()

print(f"step8 code {CODE_VERSION} on {BOARD or 'an unrecognised board'}")
print(f"  photographs {MAIN_SIZE[0]}x{MAIN_SIZE[1]}, {BUFFER_COUNT} buffers"
      f"{'  (small-board settings)' if SMALL_BOARD else ''} into {photo_dir}")
print(f"Watching for wildlife on {CAMERA_NAME}"
      f"{' (dry run: saving nothing)' if dry_run else ''}...")
print(f"  blob >= {MIN_BLOB_AREA} px needs confirming, "
      f">= {STRONG_BLOB_AREA} px saves on sight")

if recording_enabled:
    print(f"  recording a {RECORD_LENGTH:.0f} second training burst every "
          f"{RECORD_EVERY / 60:.0f} minutes into training/: "
          f"{LORES_SIZE[0]}x{LORES_SIZE[1]} every check, full colour every "
          f"{RECORD_FULL_INTERVAL:.0f} s or whenever a blob reaches "
          f"{RECORD_MOTION_AREA} px, and only above luma {RECORD_MIN_LUMA}")


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

            # Keep the plane exactly as the camera handed it over.  This
            # is what a training frame stores: blurring first and saving
            # that would make an offline replay blur a second time, and
            # measure a slightly different picture from the live one.
            # The whole YUV420 buffer: the Y (brightness) plane the rules
            # use, followed by quarter-size U and V (colour) planes.  We
            # keep all of it for training frames -- see save_training_lores.
            raw_lores = lores[:MOTION_HEIGHT * 3 // 2, :MOTION_WIDTH].copy()

            gray = cv2.GaussianBlur(raw_lores[:MOTION_HEIGHT],
                                    (BLUR_KERNEL, BLUR_KERNEL), 0)

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
                blob_range, blob_edge = blob_texture(
                    raw_lores[:MOTION_HEIGHT], largest_box)
            else:
                blob_range, blob_edge = 0.0, 0.0

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
            #   5. flat inside, soft edges       -> skip (a shadow)
            #      ...unless the AI sees an animal -> SAVE
            #   6. a big blob                    -> SAVE
            #   7. a small blob, seen twice      -> SAVE
            #   8. a small blob the AI vouches for -> SAVE
            #   9. anything else                 -> wait and see
            #
            # The AI appears twice, in rules 5 and 8, and in both it may
            # only ever say YES.
            # Over two days it decided 2 saves out of 1069, and both were
            # the bench "confirming" a blob of noise that had not moved.
            # It knows eighty everyday objects; fox, raccoon, deer, coyote
            # and squirrel are not among them.  What it recognises is
            # written into every JSON for the laptop to use later, but it
            # is not allowed to decide anything here.
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

            elif not (blob_range >= SHADOW_MIN_RANGE
                      and blob_edge >= SHADOW_MIN_EDGE):
                # 5. Flat inside and soft at the edges: the same ground,
                # dimmed.  A branch's shadow sliding across the concrete
                # looks like this at any size and any shape, and persistence
                # cannot help -- a shadow creeps steadily and confirms
                # itself perfectly.
                #
                # This is the one rule that can throw away an animal in
                # silence -- a pale animal in flat light fails both tests
                # -- so it is the one rule the AI is allowed to overrule.
                ai_detections = get_ai_detections(metadata)

                if ai_promotes(ai_detections, motion_box):
                    decision = "shadow, but the AI sees an animal"
                    save_now = True
                else:
                    decision = "shadow"
                    pending = None

            elif strong:
                # 6. Big, with real structure in it.  Take the picture.
                decision = "strong motion"
                save_now = True

            else:
                # Small.  This is where a distant deer and a nearby leaf
                # look exactly alike, so we need a second opinion.
                confirmations = seen_again(largest_centroid, moment)

                if confirmations >= CONFIRM_CHECKS:
                    # 7. Still there, in the same place, a moment later.
                    # The leaf blew back; this did not.
                    decision = "confirmed motion"
                    save_now = True

                else:
                    ai_detections = get_ai_detections(metadata)

                    if ai_promotes(ai_detections, motion_box):
                        # 8. Too soon to confirm, but the AI recognises a
                        # live animal right where the blob is.  A leaf
                        # never gets this vote.
                        decision = "small blob, the AI sees an animal"
                        save_now = True

                    else:
                        # 9. Wait and see.  A quarter of a second from
                        # now this blob either is still there or it is
                        # not, and that answers the question better than
                        # anything else can.
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
                if mean_luma < RECORD_MIN_LUMA:
                    # Too dark to be worth keeping.  Do not spend the
                    # slot; look again shortly, so dawn is not missed by
                    # an hour.
                    record_next = moment + RECORD_DARK_RETRY

                    if not waiting_for_light:
                        print(f"{now:%H:%M:%S} too dark for training frames "
                              f"(luma {mean_luma:.0f} < {RECORD_MIN_LUMA}); "
                              f"waiting for the light")
                        waiting_for_light = True
                else:
                    if waiting_for_light:
                        print(f"{now:%H:%M:%S} light is back "
                              f"(luma {mean_luma:.0f})")
                        waiting_for_light = False

                    record_until = moment + RECORD_LENGTH
                    record_next = moment + RECORD_EVERY

                    print(f"{now:%H:%M:%S} recording a "
                          f"{RECORD_LENGTH:.0f} second training burst")

            # If the light goes while a burst is running, stop it there
            # rather than filling the card with black rectangles.
            in_burst = (moment < record_until
                        and disk_ok
                        and mean_luma >= RECORD_MIN_LUMA)

            # the little frame, every check
            record_lores = (in_burst
                            and moment - record_lores_last
                            >= RECORD_LORES_INTERVAL)

            # the big colour one, on a slow tick...
            recording = (in_burst
                         and moment - record_last >= RECORD_FULL_INTERVAL)

            # ...and straight away if anything at all is stirring
            if (in_burst
                    and largest_area >= RECORD_MOTION_AREA
                    and moment - record_last >= RECORD_MOTION_INTERVAL):
                recording = True

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
                "blob_range": blob_range,
                "blob_edge": blob_edge,
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

        if record_lores:
            training_file = save_training_lores(now, raw_lores)
            record_lores_last = moment

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

        near_miss = (LOG_NEAR_MISS
                     and not training_file
                     and not dry_run
                     and decision == "shadow"
                     and (blob_edge >= NEAR_MISS_EDGE
                          or blob_range >= NEAR_MISS_RANGE))

        if near_miss:
            near_miss_times = [t for t in near_miss_times
                               if moment - t < 3600.0]

            if len(near_miss_times) >= MAX_NEAR_MISS_PER_HOUR:
                near_miss = False
            else:
                near_miss_times.append(moment)

        if dry_run or training_file or near_miss:
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
                "blob_range": round(blob_range, 1),
                "blob_edge": round(blob_edge, 1),
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
