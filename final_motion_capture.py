import os
import sys
import shutil
import time
import cv2
from datetime import datetime
from picamera2 import Picamera2

PHOTO_DIR="/var/www/html/photos"

# ------------------------------------------------------------
# Hand over to the AI Camera program, if this Pi has one
# ------------------------------------------------------------
#
# Every camera in the fleet starts THIS script from systemd at boot.  As
# each one gets upgraded to a Raspberry Pi AI Camera it should really be
# running step8 instead, which uses the AI built into the sensor.
#
# Rather than editing the service file on every Pi as we work through
# them, we just ask what camera is actually plugged in.  Upgrade the
# hardware, reboot, and the right program runs by itself.
#
# Both programs write to PHOTO_DIR above, in the same
# <date>/<HHMMSS>.jpg layout, so nginx and sync_cameras.py cannot tell
# the difference.  step8 simply adds _annotated.jpg and .json beside it.

# The newest step is what an AI Camera should be running.  Bump this one
# name when a step 9 arrives -- there is deliberately no fallback to an
# older step, because a camera quietly running last week's rules is worse
# than one that does not start.
AI_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "step8_reject_shadows.py",
)

# Neither can start without this, so if the imx500 packages were never
# installed we are better off staying here than crash-looping there.
AI_MODEL = (
    "/usr/share/imx500-models/"
    "imx500_network_ssd_mobilenetv2_fpnlite_320x320_pp.rpk"
)


def ai_camera_attached():
    """True if one of the attached cameras is an IMX500 AI Camera.

    global_camera_info() just reads what is on the camera connector.  It
    does not open the camera, so it is safe to call before we start.
    """
    try:
        cameras = Picamera2.global_camera_info()
    except Exception:
        return False

    return any("imx500" in camera.get("Model", "").lower()
               for camera in cameras)


if (ai_camera_attached()
        and os.path.exists(AI_SCRIPT)
        and os.path.exists(AI_MODEL)):

    print("AI Camera found -- handing over to "
          f"{os.path.basename(AI_SCRIPT)}")
    sys.stdout.flush()

    # execv REPLACES this process rather than starting a second one, so
    # systemd carries on supervising the same service and Restart= still
    # works.  Nothing below this line ever runs on an AI Camera Pi.
    os.execv(sys.executable,
             [sys.executable, "-u", AI_SCRIPT] + sys.argv[1:])

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
