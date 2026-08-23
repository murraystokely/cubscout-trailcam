import cv2
import json
import os
import shutil
import time

from datetime import datetime

from picamera2 import Picamera2
from picamera2.devices import IMX500


MODEL = (
    "/usr/share/imx500-models/"
    "imx500_network_ssd_mobilenetv2_fpnlite_320x320_pp.rpk"
)

photo_dir = "/var/www/html/photos"


# ------------------------------------------------------------
# Motion detection settings
# ------------------------------------------------------------

# Work on a small image.  This is plenty for motion detection
# and makes distant objects occupy a meaningful number of pixels.
MOTION_WIDTH = 320
MOTION_HEIGHT = 180

# How different a pixel must be from the background.
PIXEL_THRESHOLD = 25

# Smallest connected moving region worth keeping.
# Start here and experiment.
MIN_BLOB_AREA = 40

# If almost the whole image changes at once, it is probably
# exposure/lighting/camera movement rather than an animal.
MAX_CHANGED_FRACTION = 0.35

# How quickly the stored background adapts when nothing is moving.
BACKGROUND_ALPHA = 0.02

# Don't save thirty nearly identical photos of the same animal.
SAVE_COOLDOWN = 5

# How often to check.
LOOP_DELAY = 1


# ------------------------------------------------------------
# AI settings
# ------------------------------------------------------------

# AI does NOT control whether we save.
# We keep even fairly weak detections as useful metadata.
MIN_AI_CONFIDENCE = 0.20


# Classes that are particularly interesting if the stock
# COCO model recognizes them.
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
}


# ------------------------------------------------------------
# Set up AI Camera
# ------------------------------------------------------------

imx500 = IMX500(MODEL)

intrinsics = imx500.network_intrinsics
intrinsics.update_with_defaults()

labels = intrinsics.labels

picam2 = Picamera2(imx500.camera_num)

config = picam2.create_preview_configuration(
    main={
        "size": (1280, 720),
        "format": "BGR888",
    },
    controls={
        "FrameRate": intrinsics.inference_rate,
    },
    buffer_count=12,
)

print("Loading AI model...")
imx500.show_network_fw_progress_bar()

picam2.configure(config)
picam2.start()

time.sleep(2)

print("Watching for wildlife...")


# ------------------------------------------------------------
# Helper: read AI detections
# ------------------------------------------------------------

def get_ai_detections(metadata):

    outputs = imx500.get_outputs(
        metadata,
        add_batch=True,
    )

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

    for box, score, category in zip(
        boxes,
        scores,
        classes,
    ):

        if score < MIN_AI_CONFIDENCE:
            continue

        category = int(category)
        name = labels[category]

        x, y, w, h = imx500.convert_inference_coords(
            box,
            metadata,
            picam2,
        )

        detections.append(
            {
                "class": name,
                "confidence": float(score),
                "box": [
                    int(x),
                    int(y),
                    int(w),
                    int(h),
                ],
            }
        )

    return detections


# ------------------------------------------------------------
# First frame becomes our initial background
# ------------------------------------------------------------

request = picam2.capture_request()

image = request.make_array("main")
request.release()

small = cv2.resize(
    image,
    (MOTION_WIDTH, MOTION_HEIGHT),
)

gray = cv2.cvtColor(
    small,
    cv2.COLOR_BGR2GRAY,
)

gray = cv2.GaussianBlur(
    gray,
    (5, 5),
    0,
)

background = gray.astype("float32")

last_save_time = 0


# ------------------------------------------------------------
# Main wildlife loop
# ------------------------------------------------------------

while True:

    # Protect the filesystem.
    total, used, free = shutil.disk_usage("/")
    percent_used = used / total * 100

    if percent_used >= 95:
        print("Filesystem is at least 95% full.")
        print("Stopping before the disk fills completely.")
        break


    # Capture one frame and its AI metadata.
    request = picam2.capture_request()

    metadata = request.get_metadata()
    image = request.make_array("main")

    request.release()


    # --------------------------------------------------------
    # Motion detection
    # --------------------------------------------------------

    small = cv2.resize(
        image,
        (MOTION_WIDTH, MOTION_HEIGHT),
    )

    gray = cv2.cvtColor(
        small,
        cv2.COLOR_BGR2GRAY,
    )

    gray = cv2.GaussianBlur(
        gray,
        (5, 5),
        0,
    )


    background_image = cv2.convertScaleAbs(background)

    difference = cv2.absdiff(
        gray,
        background_image,
    )

    _, threshold_image = cv2.threshold(
        difference,
        PIXEL_THRESHOLD,
        255,
        cv2.THRESH_BINARY,
    )


    # Remove isolated noisy pixels.
    threshold_image = cv2.morphologyEx(
        threshold_image,
        cv2.MORPH_OPEN,
        None,
    )

    # Join nearby changed pixels into coherent objects.
    threshold_image = cv2.dilate(
        threshold_image,
        None,
        iterations=2,
    )


    contours, _ = cv2.findContours(
        threshold_image,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )


    largest_area = 0
    largest_box = None

    for contour in contours:

        area = cv2.contourArea(contour)

        if area > largest_area:

            largest_area = area

            largest_box = cv2.boundingRect(
                contour
            )


    changed_pixels = cv2.countNonZero(
        threshold_image
    )

    changed_fraction = (
        changed_pixels /
        (MOTION_WIDTH * MOTION_HEIGHT)
    )


    motion_detected = (
        largest_area >= MIN_BLOB_AREA
        and
        changed_fraction < MAX_CHANGED_FRACTION
    )


    # If almost everything changed simultaneously,
    # treat it as a new scene rather than an animal.
    if changed_fraction >= MAX_CHANGED_FRACTION:

        print(
            "Large scene change -- "
            "resetting background."
        )

        background = gray.astype("float32")

        time.sleep(LOOP_DELAY)

        continue


    # --------------------------------------------------------
    # Candidate wildlife event
    # --------------------------------------------------------

    if motion_detected:

        now = datetime.now()

        print(
            f"Motion: largest blob={largest_area:.0f}, "
            f"changed={changed_fraction:.3%}"
        )


        ai_detections = get_ai_detections(
            metadata
        )


        animal_hint = False

        for detection in ai_detections:

            if (
                detection["class"]
                in ANIMAL_CLASSES
            ):
                animal_hint = True


        # Respect save cooldown.
        if (
            time.time() - last_save_time
            >= SAVE_COOLDOWN
        ):

            day_directory = (
                f"{photo_dir}/"
                f"{now.strftime('%Y-%m-%d')}"
            )

            os.makedirs(
                day_directory,
                exist_ok=True,
            )


            base_filename = now.strftime(
                "%H%M%S"
            )


            original_filename = (
                f"{day_directory}/"
                f"{base_filename}.jpg"
            )

            annotated_filename = (
                f"{day_directory}/"
                f"{base_filename}_annotated.jpg"
            )

            json_filename = (
                f"{day_directory}/"
                f"{base_filename}.json"
            )


            # -----------------------------
            # Save untouched original
            # -----------------------------

            cv2.imwrite(
                original_filename,
                image,
            )


            # -----------------------------
            # Create annotated copy
            # -----------------------------

            annotated = image.copy()


            # Draw the motion bounding box.
            if largest_box is not None:

                x, y, w, h = largest_box

                scale_x = (
                    image.shape[1]
                    / MOTION_WIDTH
                )

                scale_y = (
                    image.shape[0]
                    / MOTION_HEIGHT
                )

                x = int(x * scale_x)
                y = int(y * scale_y)
                w = int(w * scale_x)
                h = int(h * scale_y)

                cv2.rectangle(
                    annotated,
                    (x, y),
                    (x + w, y + h),
                    (255, 0, 0),
                    2,
                )

                cv2.putText(
                    annotated,
                    "MOTION",
                    (x, max(y - 8, 20)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (255, 0, 0),
                    2,
                )


            # Draw AI boxes as well.
            for detection in ai_detections:

                x, y, w, h = detection["box"]

                label = (
                    f"{detection['class']} "
                    f"{detection['confidence']:.0%}"
                )

                cv2.rectangle(
                    annotated,
                    (x, y),
                    (x + w, y + h),
                    (0, 255, 0),
                    2,
                )

                cv2.putText(
                    annotated,
                    label,
                    (x, max(y - 8, 20)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 0),
                    2,
                )


            cv2.imwrite(
                annotated_filename,
                annotated,
            )


            # -----------------------------
            # Save metadata
            # -----------------------------

            information = {
                "time": now.isoformat(),

                "motion": {
                    "largest_blob_area":
                        float(largest_area),

                    "changed_fraction":
                        float(changed_fraction),

                    "box":
                        list(largest_box)
                        if largest_box
                        else None,
                },

                "ai_animal_hint":
                    animal_hint,

                "ai_detections":
                    ai_detections,
            }


            with open(
                json_filename,
                "w",
            ) as file:

                json.dump(
                    information,
                    file,
                    indent=2,
                )


            print(
                f"Saved candidate "
                f"{original_filename}"
            )

            if ai_detections:

                for detection in ai_detections:

                    print(
                        f"  AI: "
                        f"{detection['class']} "
                        f"{detection['confidence']:.2f}"
                    )

            else:

                print(
                    "  AI: no recognized objects"
                )


            last_save_time = time.time()


    else:

        # Only adapt the background when we do NOT
        # believe an object is moving.
        cv2.accumulateWeighted(
            gray,
            background,
            BACKGROUND_ALPHA,
        )


    time.sleep(LOOP_DELAY)


picam2.stop()

print("Program finished safely.")
