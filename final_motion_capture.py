#!/usr/bin/env python3
"""What systemd starts at boot: pick the right program and get out of the way.

Every camera in the fleet starts THIS script from systemd.  All it does
is look at which camera is plugged in and hand over to the newest step
written for that camera:

    Raspberry Pi AI Camera (IMX500)   ->  step8_reject_shadows.py
    an ordinary Camera Module         ->  step9_plain_motion.py

Upgrade the hardware, reboot, and the right program runs by itself; no
service file to edit on eleven Raspberry Pis.

It used to be lopsided.  The AI Camera was handed to a numbered step,
while the ordinary Camera Module ran a copy of step 5's motion rule
buried at the bottom of THIS file -- where no Scout reads it and nobody
thought to improve it.  So the AI cameras gained connected blobs
(step 7) and shadow rejection (step 8) while the plain ones sat on a
rule from August that could not see a squirrel.  wildlifecam14 kept
1,109 photographs of an oleander moving in the wind and not one animal.

Both paths now point at a numbered step, and this file contains no
motion detection at all.  That is the point: the next time one track
learns something, the other one is a file you can actually find.

Both programs write into the same <date>/<HHMMSS>.jpg layout under
PHOTO_DIR, so nginx and sync_cameras.py cannot tell them apart.  step8
adds _annotated.jpg beside each picture; both write a .json sidecar.
"""

import os
import sys

from picamera2 import Picamera2

PHOTO_DIR = "/var/www/html/photos"

HERE = os.path.dirname(os.path.abspath(__file__))

# The newest step for each kind of camera.  Bump the name here when a
# step 10 arrives.  There is deliberately no fallback to an older step:
# a camera quietly running last week's rules is worse than one that does
# not start, because the first kind is discovered weeks later on a card
# full of leaves.
AI_SCRIPT = os.path.join(HERE, "step8_reject_shadows.py")
PLAIN_SCRIPT = os.path.join(HERE, "step9_plain_motion.py")

# step8 cannot start without this, so if the imx500 packages were never
# installed, an AI Camera is better off running the plain program than
# crash-looping in one that needs a model file it does not have.
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


def choose_program():
    """Which step should run on this Pi, and why."""
    if ai_camera_attached():
        if os.path.exists(AI_SCRIPT) and os.path.exists(AI_MODEL):
            return AI_SCRIPT, "AI Camera found"
        # An AI camera with no model file or no step8 still takes
        # perfectly good photographs; it just cannot use the sensor's
        # neural network.  The plain program will treat it as an
        # ordinary camera, which is better than not starting.
        return PLAIN_SCRIPT, ("AI Camera found, but step8 or its model "
                              "is missing -- falling back to the plain "
                              "program")
    return PLAIN_SCRIPT, "ordinary Camera Module"


def main():
    program, reason = choose_program()

    if not os.path.exists(program):
        # Fail loudly.  systemd's Restart=on-failure will keep trying
        # and the journal will say exactly which file is missing, which
        # is a much better morning than a card full of nothing.
        print(f"{reason}, but {os.path.basename(program)} is not on this "
              f"card. Copy it to {HERE} and reboot.", file=sys.stderr)
        sys.exit(1)

    print(f"{reason} -- handing over to {os.path.basename(program)}")
    sys.stdout.flush()

    # execv REPLACES this process rather than starting a second one, so
    # systemd carries on supervising the same service and Restart= still
    # works.  Nothing after this line ever runs.
    os.execv(sys.executable, [sys.executable, "-u", program] + sys.argv[1:])


if __name__ == "__main__":
    main()
