import time
import cv2
from datetime import datetime
from picamera2 import Picamera2

PHOTO_DIR="photos"

picam2 = Picamera2()
picam2.start()
time.sleep(2)
print("Watching for motion...")
last_image = None
while True:
    #main code
    image = picam2.capture_array()
    gray = cv2.cvtColor(image,cv2.COLOR_RGB2GRAY)
    if last_image is None:
        print("first image.")
        last_image = gray
        continue
    difference = cv2.absdiff(gray, last_image)
    score = difference.sum()
    maximum = difference.max()
    mean = difference.mean()
    changed_pixels = (difference > 25).sum()
    print(
        f"sum={score:10.0f}  "
        f"mean={mean:6.2f}  "
        f"max={maximum:3d}  "
        f"Changed pixels={changed_pixels}"
    )
    last_image = gray
    print(f"Difference = {score}")
    # Your assignment:
    #   Run experiments and choose a good threshold to detect motion with your wildlife camera.
    #   Hint: pretend to be an animal in front of the camera while running the program and see if
    #   it notices your movement or not when the camera itself is perfectly still.
    #
    # if is too sensitive try bigger numbers below
    # if score > 5000000:
    if changed_pixels > 20000:
        filename = datetime.now().strftime(f"{PHOTO_DIR}/step5_%Y%m%d_%H%M%S.jpg")
        picam2.capture_file(filename)
        print(f"motion detected! Wrote {filename}.")


    else:
        print("no motion.")
    last_image = gray
    time.sleep(2)