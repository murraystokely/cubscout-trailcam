import hashlib
import json
import os
import socket
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

# ------------------------------------------------------------
# Ask the camera for a proper photograph, not a preview
# ------------------------------------------------------------
#
# For a month this program started the camera with no settings at all,
# and Picamera2's default is a 640x480 preview -- the size you would use
# to watch a video call, not to photograph a squirrel.  Nobody noticed,
# because the motion detection works fine at that size.  Then the
# pictures were put beside the AI Camera's: the same squirrel was 300
# pixels long in one and 40 in the other.
#
# So the camera now runs two streams at once, the way step8 does:
#
#   main    the photograph we save.  Big.
#   lores   a small copy the camera hardware makes for free.  The motion
#           detection reads this one, so watching for movement costs the
#           same as it always did no matter how big the photograph is.
#
# 2304x1296 is the size the Camera Module 3's sensor produces natively
# when it groups its pixels in twos (it is a 4608x2592 sensor), so the
# camera does no resizing and the picture uses the whole width of the
# lens.  It is small enough for a Pi Zero 2 W with two buffers.  If you
# want the very largest picture the sensor can take, 4608x2592 works on
# a Pi 4 or 5, but a Zero 2 W will run out of memory.
MAIN_SIZE = (2304, 1296)
LORES_SIZE = (640, 480)

# How much of the small frame has to change before this is worth a
# photograph.  It was written inline as a bare 20000 for a month; it is
# named here because the sidecar records it, and a number a photograph
# reports should be a number you can find.
CHANGED_PIXELS_TO_SAVE = 20000

# ------------------------------------------------------------
# A JSON sidecar beside every photograph
# ------------------------------------------------------------
#
# step8 has always written one of these next to each picture, and every
# analysis on the laptop reads them: which build kept the photograph,
# which rule fired, how big the moving thing was.  This program wrote
# bare JPEGs, so a camera with an ordinary Camera Module produced
# pictures that could not be traced back to anything -- including the
# uptime that tells us how long a battery lasted.
#
# So it writes one too.  Same file name, same shape, fewer fields: there
# is no AI here to report, and the motion numbers are the simple ones
# this program actually measures.  The laptop tools read sidecars with
# .get(), so the missing sections cost nothing.

CAMERA_NAME = socket.gethostname()


def code_fingerprint():
    """A short hash of this very file, recorded in every photograph."""
    try:
        with open(os.path.abspath(__file__), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except Exception:
        return "unknown"


CODE_VERSION = code_fingerprint()


def boot_id():
    """This boot's random id, so photographs group into runs."""
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()[:8]
    except Exception:
        return "unknown"


BOOT_ID = boot_id()


def seconds_since_boot():
    """How long this Pi has been up.

    A Zero 2 W has no clock of its own: it starts each boot believing
    the time it last saved, so the timestamps lie until the network
    corrects them.  This number comes from the kernel and starts at zero
    at power on, so the largest value in a run says how long the battery
    lasted.
    """
    try:
        with open("/proc/uptime") as f:
            return round(float(f.read().split()[0]), 1)
    except Exception:
        return None


def write_sidecar(photo_path, now, changed_pixels):
    """Describe one photograph in JSON, beside the photograph."""
    information = {
        "camera": CAMERA_NAME,
        "code": CODE_VERSION,
        "time": now.isoformat(),

        "boot": BOOT_ID,
        "uptime_s": seconds_since_boot(),

        "image": {
            "file": os.path.basename(photo_path),
            "width": MAIN_SIZE[0],
            "height": MAIN_SIZE[1],
        },

        # This program has one rule, so there is one trigger.  The name
        # matches nothing in step8 on purpose: a photograph should say
        # which program's rules kept it.
        "trigger": "changed pixels",

        "motion": {
            "changed_pixels": int(changed_pixels),
            "threshold": CHANGED_PIXELS_TO_SAVE,
            "lores_size": list(LORES_SIZE),
        },
    }
    try:
        with open(os.path.splitext(photo_path)[0] + ".json", "w") as f:
            json.dump(information, f, indent=2)
    except OSError as problem:
        # A sidecar is never worth losing a photograph over.
        print(f"could not write the sidecar: {problem}")


picam2 = Picamera2()
picam2.configure(picam2.create_preview_configuration(
    main={"size": MAIN_SIZE, "format": "RGB888"},
    lores={"size": LORES_SIZE, "format": "YUV420"},
    buffer_count=2,
))
picam2.start()
time.sleep(2)
print(f"Photographs will be {MAIN_SIZE[0]}x{MAIN_SIZE[1]}; "
      f"watching for motion at {LORES_SIZE[0]}x{LORES_SIZE[1]}.")
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
    #
    # The small stream arrives as YUV420: the grey picture first, then
    # the colour information squashed underneath it.  The top 480 rows
    # ARE the grey image, so there is nothing to convert.
    lores = picam2.capture_array("lores")
    gray = lores[:LORES_SIZE[1], :LORES_SIZE[0]]
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
    if changed_pixels > CHANGED_PIXELS_TO_SAVE:
        now = datetime.now()
        day_directory = f"{PHOTO_DIR}/{now.strftime('%Y-%m-%d')}"
        os.makedirs(day_directory, exist_ok=True)
        filename = f"{day_directory}/{now.strftime('%H%M%S')}.jpg"
        picam2.capture_file(filename)          # from "main": the big one
        write_sidecar(filename, now, changed_pixels)
        print(f"motion detected! Wrote {filename}.")


    else:
        print("no motion.")
    last_image = gray
    time.sleep(2)
picam2.stop()
print("Program finished safely.")
