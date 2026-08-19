import cv2

from picamera2 import MappedArray, Picamera2
from picamera2.devices import IMX500


MODEL = (
    "/usr/share/imx500-models/"
    "imx500_network_ssd_mobilenetv2_fpnlite_320x320_pp.rpk"
)

OUTPUT_FILE = "/home/webelos/step6_ai_detect_objects.jpg"

MIN_CONFIDENCE = 0.55


# This must be created before Picamera2.
imx500 = IMX500(MODEL)

intrinsics = imx500.network_intrinsics
intrinsics.update_with_defaults()

labels = intrinsics.labels

picam2 = Picamera2(imx500.camera_num)

config = picam2.create_preview_configuration(
    main={"size": (1280, 720), "format": "RGB888"},
    controls={"FrameRate": intrinsics.inference_rate},
    buffer_count=12,
)


detections = []


def get_detections(metadata):
    outputs = imx500.get_outputs(metadata, add_batch=True)

    if outputs is None:
        return []

    boxes = outputs[0][0]
    scores = outputs[1][0]
    classes = outputs[2][0]

    input_width, input_height = imx500.get_input_size()

    if intrinsics.bbox_normalization:
        boxes = boxes / input_height

    if intrinsics.bbox_order == "xy":
        boxes = boxes[:, [1, 0, 3, 2]]

    found = []

    for box, score, category in zip(boxes, scores, classes):
        if score < MIN_CONFIDENCE:
            continue

        x, y, w, h = imx500.convert_inference_coords(
            box,
            metadata,
            picam2,
        )

        category = int(category)
        name = labels[category]

        found.append(
            (name, float(score), x, y, w, h)
        )

    return found


def draw_detections(request):
    with MappedArray(request, "main") as image:
        for name, score, x, y, w, h in detections:
            label = f"{name} {score:.0%}"

            cv2.rectangle(
                image.array,
                (x, y),
                (x + w, y + h),
                (0, 255, 0),
                2,
            )

            cv2.putText(
                image.array,
                label,
                (x, max(y - 8, 20)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2,
            )


print("Loading AI model...")
imx500.show_network_fw_progress_bar()

picam2.configure(config)

# Every image produced by Picamera2 will now have our
# current detection boxes drawn onto it.
picam2.pre_callback = draw_detections

picam2.start()

print("Looking for objects...")


while not detections:
    metadata = picam2.capture_metadata()

    detections = get_detections(metadata)


print()
print("Detected objects:")

for name, score, x, y, w, h in detections:
    print(f"  {name}: {score:.2f}")


# Capture one image. The pre_callback above draws the
# bounding boxes and labels before the image is saved.
picam2.capture_file(OUTPUT_FILE)

print()
print(f"Saved {OUTPUT_FILE}")

picam2.stop()
