import os
import shutil
import time
import cv2
from datetime import datetime
from picamera2 import Picamera2

PHOTO_DIR="/var/www/html/photos"

picam2 = Picamera2()
picam2.start()
time.sleep(2)
print("Watching for motion...")
last_image = None
while True:
    # exit early if you run out of storage
    disk = shutil.disk_usage("/")
    percent_used = disk.used / disk.total * 100
    print(f"Filesystem used:{percent_used:.1f}%")
    if percent_used >= 95:
        print("Filesystem is at least 95% full.")
        print("Stopping before the disk fills completely.")
        break
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
        now = datetime.now()
        day_directory = f"{PHOTO_DIR}/{now.strftime('%Y-%m-%d')}"
        os.makedirs(day_directory, exist_ok=True)
        filename = f"{day_directory}/{now.strftime('%H%M%S')}.jpg"
        picam2.capture_file(filename)
        print(f"motion detected! Wrote {filename}.")


    else:
        print("no motion.")
    last_image = gray
    time.sleep(2)
picam2.stop()
print("Program finished safely.")
