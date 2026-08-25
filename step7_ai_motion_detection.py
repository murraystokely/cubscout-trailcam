#!/usr/bin/env python3
"""Step 7 -- using the AI Camera to decide what is worth photographing.

Step 5 saved a picture whenever enough pixels changed.  That works until the
wind blows, and then it works very badly: ten thousand pixels of shivering
leaves count exactly the same as ten thousand pixels of deer.

This program asks a better question.  Instead of counting changed pixels it
finds the biggest *connected patch* of them, and instead of comparing each
frame with the one before it, it compares against a memory of what the scene
usually looks like.  That second change matters more than it sounds: an
animal that stops moving vanishes from frame-to-frame comparison, and a deer
standing still is exactly the photograph worth having.

The Raspberry Pi AI Camera also runs a neural network on the sensor itself,
for free, on every frame.  We use it in one direction only.  It knows eighty
everyday objects and not one of them is a deer, so a "no" from it means
nothing at all -- but a "yes" can rescue a blob we were unsure about.

The whole decision, four times a second:

    1. too dark to see anything      -> skip
    2. the light changed             -> skip
    3. the whole treeline is moving  -> skip
    4. too small, or the wrong shape -> skip
    5. a big blob                    -> SAVE
    6. a small blob, seen twice      -> SAVE
    7. a small blob the AI agrees on -> SAVE
    8. anything else                 -> wait and see

Run it and watch what it prints.  Wave at it, then hold still, then walk
away, and see which rule fires each time.

This is the lesson.  The program that actually lives in the woods is
[`step8_wildlife_camera.py`](step8_wildlife_camera.py), which is this plus
the unglamorous parts -- training bursts, disk guards, rate limits, shadow
rejection -- that keep a fleet alive for a season but teach nothing about
finding animals in pictures.
"""

import json
import os
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


# ------------------------------------------------------------
# The two pictures the camera gives us
# ------------------------------------------------------------

# "main" is the photograph we keep.  4:3 uses the whole sensor; asking for
# a 16:9 size throws away the top and bottom of the view, which is the last
# thing a trail camera wants.
MAIN_SIZE = (2028, 1520)

# Picamera2 format names describe the byte order in memory, which is the
# REVERSE of the numpy channel order.  "RGB888" therefore hands OpenCV the
# BGR array it expects.  Using "BGR888" here saves every photograph with red
# and blue swapped -- which looks fine on a bench and very odd on a fox.
MAIN_FORMAT = "RGB888"

# "lores" is a small copy the camera hardware produces for free.  Its Y
# plane already IS a greyscale image, so motion detection costs us no
# resizing and no colour conversion at all.  Every decision below is made on
# this, an image smaller than a postage stamp.
LORES_SIZE = (640, 480)

MOTION_WIDTH, MOTION_HEIGHT = LORES_SIZE
MOTION_PIXELS = MOTION_WIDTH * MOTION_HEIGHT

JPEG_QUALITY = 88
BUFFER_COUNT = 4


# Every setting below that is measured in pixels was chosen on a 320-wide
# frame, so they are scaled here.  Changing LORES_SIZE then changes how FAR
# the camera can see and nothing else.
MOTION_SCALE = MOTION_WIDTH / 320.0


def odd(value):
    """Nearest odd number >= 3.  OpenCV kernels have to be odd."""
    return max(3, int(round(value)) | 1)


BLUR_KERNEL = odd(5 * MOTION_SCALE)
OPEN_SIZE = odd(3 * MOTION_SCALE)
CLOSE_SIZE = odd(7 * MOTION_SCALE)


# ------------------------------------------------------------
# Deciding what counts as motion
# ------------------------------------------------------------

# How different one pixel must be before we call it "changed".  On a dark
# night the sensor turns up its gain and invents differences of its own, so
# this is only a floor: the real threshold rises to sit above whatever noise
# the frame actually has, and is capped so a big shadow cannot blind us.
PIXEL_THRESHOLD = 25
NOISE_MULTIPLIER = 4
MAX_PIXEL_THRESHOLD = 75

# Blob sizes as a fraction of the frame, so they keep their meaning if you
# change LORES_SIZE.
MIN_BLOB_FRACTION = 0.00098       # ~300 px at 640x480: below this is noise
STRONG_BLOB_FRACTION = 0.0043     # ~1320 px: big enough to save on sight

MIN_BLOB_AREA = int(MIN_BLOB_FRACTION * MOTION_PIXELS)
STRONG_BLOB_AREA = int(STRONG_BLOB_FRACTION * MOTION_PIXELS)

# Shape.  Blowing grass makes thin, stringy, hollow blobs; animals make
# solid ones.  MAX_ASPECT is deliberately loose and should stay loose: a fox
# with its tail out is three times longer than tall, a weasel four or five,
# and a rule fitted to the proportions of whatever animal you photographed
# first will throw away all the others.
MIN_EXTENT = 0.30                 # blob area / bounding box area
MAX_ASPECT = 8.0                  # longest side / shortest side

# One pixel covers about 0.36 cm for every metre of distance, so a 10 cm leaf
# 2 m away covers MORE pixels than a 1.5 m deer 30 m away.  Size alone can
# never tell them apart.  What does is that a leaf blows back to where it
# started and an animal does not, so a small blob has to still be there on
# the next check.
CONFIRM_CHECKS = 2
CONFIRM_RADIUS = int(40 * MOTION_SCALE)
CONFIRM_TIMEOUT = 1.5             # s before we forget a half-confirmed blob

# Lots of change is only suspicious when no single blob owns most of it.  One
# big lump is an animal standing close; change scattered over the whole frame
# is wind through the treeline.
BUSY_FRACTION = 0.35
DOMINANT_RATIO = 0.50

# When MOST of the frame moves the same way at once -- everything darker
# together, everything lighter together -- the light changed, not the scene.
# Measured as the MIDDLE SIGNED difference: grainy noise pushes as many
# pixels up as down and cancels to zero, while a cloud pushes them all one
# way.  That one number tells the two apart, and it also covers the camera
# adjusting its own exposure.
LIGHTING_SHIFT = 10

# Below this average brightness the sensor is mostly amplifying its own
# noise.  This camera has no infra-red lamp, so after dusk it is simply
# blind, and nothing it saved would be worth looking at.
MIN_MEAN_LUMA = 25

# How long the background takes to forget, in seconds.  Where nothing is
# happening it must keep up with the sun moving and shadows creeping.
BACKGROUND_TAU = 10.0

# Underneath whatever IS moving it learns four times more slowly, so an
# animal that stops to browse does not dissolve into the scenery.  But it
# does still learn, and that is the whole point: an earlier version froze
# the moving region completely, so any pixel that once looked different
# stayed different for ever, and the tiniest camera shake wrote a permanent
# scar along every sharp edge in the scene.
BACKGROUND_TAU_BUSY = 40.0

# ...and how fast it re-learns just after the light changes.
SETTLE_ALPHA = 0.25
SETTLE_CHECKS = 8

LOOP_DELAY = 0.25                 # check four times a second
WARMUP_SECONDS = 5.0
SAVE_COOLDOWN = 2.0


# ------------------------------------------------------------
# The AI, which may only ever say yes
# ------------------------------------------------------------

MIN_AI_CONFIDENCE = 0.25          # worth writing into the JSON
MIN_AI_RESCUE_CONFIDENCE = 0.40   # worth promoting a weak blob
MIN_AI_HINT_CONFIDENCE = 0.45     # worth flagging as probably an animal

# The AI recognises the furniture too.  A bench is detected in every single
# frame with a box covering a sixth of the scene, so a stray blob of noise
# landing inside it would be "confirmed" by something that has not moved all
# day.  To count as support, its box must be about the size of what moved.
MAX_AI_BOX_RATIO = 20

# COCO has no deer, raccoon, squirrel or coyote, so a real animal usually
# lands on the nearest thing the model does happen to know.
ANIMAL_CLASSES = {
    "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "teddy bear",
}


# ------------------------------------------------------------
# Set up the AI Camera
# ------------------------------------------------------------

imx500 = IMX500(MODEL)

intrinsics = imx500.network_intrinsics
intrinsics.update_with_defaults()

labels = intrinsics.labels

picam2 = Picamera2(imx500.camera_num)

picam2.configure(picam2.create_preview_configuration(
    main={"size": MAIN_SIZE, "format": MAIN_FORMAT},
    lores={"size": LORES_SIZE, "format": "YUV420"},
    controls={"FrameRate": intrinsics.inference_rate},
    buffer_count=BUFFER_COUNT,
))

print("Loading AI model...")
picam2.start()


# ------------------------------------------------------------
# Reading what the sensor's own AI thinks it can see
# ------------------------------------------------------------

def get_ai_detections(metadata):
    """Every box comes back in full-resolution photograph pixels."""
    outputs = imx500.get_outputs(metadata, add_batch=True)

    if outputs is None:
        return []

    boxes, scores, classes = outputs[0][0], outputs[1][0], outputs[2][0]

    _, input_height = imx500.get_input_size()

    if intrinsics.bbox_normalization:
        boxes = boxes / input_height
    if intrinsics.bbox_order == "xy":
        boxes = boxes[:, [1, 0, 3, 2]]

    found = []

    for box, score, category in zip(boxes, scores, classes):
        if score < MIN_AI_CONFIDENCE:
            continue

        x, y, w, h = imx500.convert_inference_coords(box, metadata, picam2)

        found.append({
            "class": labels[int(category)],
            "confidence": float(score),
            "box": [int(x), int(y), int(w), int(h)],
        })

    return found


def boxes_overlap(first, second):
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    return (ax < bx + bw and bx < ax + aw
            and ay < by + bh and by < ay + ah)


def ai_supports(detections, motion_box):
    """True if the AI sees something solid, about the right size, right there.

    Any class counts, not just the animal ones: a raccoon might come back as
    "cat", a turkey as "bird", a fawn as "dog".  All we are asking is whether
    the model agrees an object is there -- which is exactly the question a
    shadow or a leaf fails.
    """
    _, _, motion_w, motion_h = motion_box
    motion_area = max(motion_w * motion_h, 1)

    for detection in detections:
        if detection["confidence"] < MIN_AI_RESCUE_CONFIDENCE:
            continue

        _, _, w, h = detection["box"]

        if w * h > MAX_AI_BOX_RATIO * motion_area:
            continue                      # that is the furniture, not a fox

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

pending = None                    # (x, y, when, count) or None


def seen_again(centroid, when):
    """Count how many checks in a row a blob has been in the same place.

    This is the single most useful trick in the whole program.  A leaf blows
    one way and then blows back, so it is rarely in the same spot twice.  An
    animal keeps being there.  Counting is enough.
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
# Saving
# ------------------------------------------------------------

def save_event(now, image, decision, measurements, ai_detections):
    """The photograph, a copy with the boxes drawn on, and the numbers."""
    day_directory = f"{PHOTO_DIR}/{now.strftime('%Y-%m-%d')}"
    os.makedirs(day_directory, exist_ok=True)

    base = now.strftime("%H%M%S")
    filename = f"{day_directory}/{base}.jpg"

    cv2.imwrite(filename, image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])

    animal_hint = any(
        d["class"] in ANIMAL_CLASSES
        and d["confidence"] >= MIN_AI_HINT_CONFIDENCE
        for d in ai_detections
    )

    with open(f"{day_directory}/{base}.json", "w") as f:
        json.dump({
            "time": now.isoformat(),
            "image": {"file": f"{base}.jpg",
                      "width": MAIN_SIZE[0], "height": MAIN_SIZE[1]},
            "trigger": decision,
            # every box in this file is in full-resolution photograph pixels
            "motion": measurements,
            "ai": {"animal_hint": animal_hint, "detections": ai_detections},
        }, f, indent=2)

    # The annotated copy is how you see WHY the camera kept a picture: blue
    # is the patch of connected pixels that moved, green is whatever the AI
    # recognised.  Open it next to the original.
    annotated = image.copy()

    if measurements["box"]:
        x, y, w, h = measurements["box"]
        cv2.rectangle(annotated, (x, y), (x + w, y + h), (255, 0, 0), 3)
        cv2.putText(annotated, "MOTION", (x, max(y - 10, 24)),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 0, 0), 3)

    for d in ai_detections:
        x, y, w, h = d["box"]
        cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 255, 0), 3)
        cv2.putText(annotated, f"{d['class']} {d['confidence']:.0%}",
                    (x, max(y - 10, 24)), cv2.FONT_HERSHEY_SIMPLEX,
                    1.0, (0, 255, 0), 3)

    cv2.imwrite(f"{day_directory}/{base}_annotated.jpg", annotated,
                [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])

    return filename


# ------------------------------------------------------------
# Let the camera settle, then learn what the scene looks like
# ------------------------------------------------------------

print(f"Warming up for {WARMUP_SECONDS:.0f} seconds...")
time.sleep(WARMUP_SECONDS)

request = picam2.capture_request()
try:
    lores = request.make_array("lores")
    background = cv2.GaussianBlur(
        lores[:MOTION_HEIGHT, :MOTION_WIDTH].copy(),
        (BLUR_KERNEL, BLUR_KERNEL), 0).astype("float32")
finally:
    request.release()

OPEN_KERNEL = cv2.getStructuringElement(cv2.MORPH_RECT, (OPEN_SIZE,) * 2)
CLOSE_KERNEL = cv2.getStructuringElement(cv2.MORPH_RECT, (CLOSE_SIZE,) * 2)

BACKGROUND_ALPHA = min(1.0, LOOP_DELAY / BACKGROUND_TAU)
BACKGROUND_ALPHA_BUSY = min(1.0, LOOP_DELAY / BACKGROUND_TAU_BUSY)

settle_checks = 0
last_save_time = 0.0

print(f"Watching for wildlife.  A blob of {MIN_BLOB_AREA} px needs "
      f"confirming; {STRONG_BLOB_AREA} px saves on sight.")


# ------------------------------------------------------------
# The main loop
# ------------------------------------------------------------

while True:

    now = datetime.now()
    moment = time.time()

    request = picam2.capture_request()

    try:
        metadata = request.get_metadata()

        # The Y plane of the small stream is already greyscale.
        lores = request.make_array("lores")
        gray = cv2.GaussianBlur(lores[:MOTION_HEIGHT, :MOTION_WIDTH],
                                (BLUR_KERNEL, BLUR_KERNEL), 0)

        # --------------------------------------------------------
        # Compare against the learned background
        # --------------------------------------------------------

        background_image = background.astype("uint8")

        # Keep the SIGN: it is what separates "everything got darker
        # together" from "some pixels went each way".
        signed = gray.astype(np.int16) - background_image.astype(np.int16)

        shift = float(np.median(signed))
        noise = float(np.median(np.abs(signed)))

        pixel_threshold = min(
            MAX_PIXEL_THRESHOLD,
            max(PIXEL_THRESHOLD, int(NOISE_MULTIPLIER * noise + 5)))

        difference = cv2.absdiff(gray, background_image)
        _, mask = cv2.threshold(difference, pixel_threshold, 255,
                                cv2.THRESH_BINARY)

        # Open first to rub out lone speckles, then close to join a broken-up
        # animal into one shape.  Closing does not inflate the area the way a
        # plain dilate does, so the blob sizes mean what they say.
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, OPEN_KERNEL)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, CLOSE_KERNEL)

        changed_fraction = cv2.countNonZero(mask) / MOTION_PIXELS

        # --------------------------------------------------------
        # Find the biggest patch of connected pixels
        # --------------------------------------------------------

        count, _, stats, centroids = cv2.connectedComponentsWithStats(
            mask, connectivity=8)

        largest_area = 0
        largest_box = None
        largest_centroid = None

        for i in range(1, count):
            area = int(stats[i, cv2.CC_STAT_AREA])
            if area > largest_area:
                largest_area = area
                largest_box = (int(stats[i, cv2.CC_STAT_LEFT]),
                               int(stats[i, cv2.CC_STAT_TOP]),
                               int(stats[i, cv2.CC_STAT_WIDTH]),
                               int(stats[i, cv2.CC_STAT_HEIGHT]))
                largest_centroid = (float(centroids[i][0]),
                                    float(centroids[i][1]))

        largest_fraction = largest_area / MOTION_PIXELS

        if largest_box is not None:
            _, _, box_w, box_h = largest_box
            extent = largest_area / float(max(box_w * box_h, 1))
            aspect = max(box_w, box_h) / float(max(min(box_w, box_h), 1))
        else:
            extent = aspect = 0.0

        mean_luma = float(gray.mean())

        # --------------------------------------------------------
        # Decide.  The first rule that matches wins.
        #
        # Notice the AI appears only in rule 7, and only ever says yes.  It
        # can turn a maybe into a photograph; it can never stop one.
        # --------------------------------------------------------

        decision = "quiet"
        save_now = False
        ai_detections = []
        confirmations = 0

        big_enough = largest_area >= MIN_BLOB_AREA
        strong = largest_area >= STRONG_BLOB_AREA
        right_shape = aspect <= MAX_ASPECT and (strong or extent >= MIN_EXTENT)
        motion_box = to_main_coords(largest_box) if largest_box else None

        if mean_luma < MIN_MEAN_LUMA:
            decision = "too dark"                                   # 1
            pending = None

        elif abs(shift) >= LIGHTING_SHIFT:
            decision = "lighting change"                            # 2
            pending = None

        elif (changed_fraction >= BUSY_FRACTION
                and largest_fraction < DOMINANT_RATIO * changed_fraction):
            decision = "scene change"                               # 3
            pending = None

        elif not (big_enough and right_shape):                      # 4
            if big_enough:
                decision = "wrong shape"

        elif strong:
            decision = "strong motion"                              # 5
            save_now = True

        else:
            confirmations = seen_again(largest_centroid, moment)

            if confirmations >= CONFIRM_CHECKS:
                decision = "confirmed motion"                       # 6
                save_now = True
            else:
                ai_detections = get_ai_detections(metadata)
                if ai_supports(ai_detections, motion_box):
                    decision = "small blob, AI agrees"              # 7
                    save_now = True
                else:
                    decision = "waiting for confirmation"           # 8

        if save_now and moment - last_save_time < SAVE_COOLDOWN:
            decision += " (cooldown)"
            save_now = False

        # Copying the full-resolution frame is the expensive part, so only do
        # it once we know we are keeping it.
        image = request.make_array("main") if save_now else None

    finally:
        request.release()

    # ------------------------------------------------------------
    # Update the background
    # ------------------------------------------------------------

    if decision in ("lighting change", "scene change"):
        settle_checks = SETTLE_CHECKS

    if settle_checks > 0:
        cv2.accumulateWeighted(gray, background, SETTLE_ALPHA)
        settle_checks -= 1
    else:
        # Always learn a little, everywhere, including underneath the thing
        # that is moving -- that is what stops a mistake becoming permanent.
        cv2.accumulateWeighted(gray, background, BACKGROUND_ALPHA_BUSY)
        # Then learn again, faster, wherever nothing is moving.
        cv2.accumulateWeighted(gray, background, BACKGROUND_ALPHA,
                               mask=cv2.bitwise_not(mask))

    # ------------------------------------------------------------
    # Save
    # ------------------------------------------------------------

    if save_now:
        if not ai_detections:
            ai_detections = get_ai_detections(metadata)

        filename = save_event(now, image, decision, {
            "largest_blob_area": largest_area,
            "largest_blob_fraction": largest_fraction,
            "changed_fraction": changed_fraction,
            "extent": extent,
            "aspect": aspect,
            "pixel_threshold": pixel_threshold,
            "brightness_shift": shift,
            "confirmations": confirmations,
            "mean_luma": mean_luma,
            "box": motion_box,
        }, ai_detections)

        last_save_time = moment

        print(f"{now:%H:%M:%S} {decision}: blob={largest_area} px "
              f"({largest_fraction:.2%}) -> {filename}")

        for d in ai_detections:
            print(f"    AI: {d['class']} {d['confidence']:.2f}")

    time.sleep(LOOP_DELAY)
