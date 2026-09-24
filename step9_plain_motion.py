#!/usr/bin/env python3
"""Step 9 -- the same lesson, without the AI Camera.

Steps 6, 7 and 8 were written for the Raspberry Pi AI Camera, and because
of that the cameras with an ordinary Camera Module never got them.  They
stayed on step 5: save a photograph whenever enough pixels changed.

Step 7 already explained why that fails.  "Ten thousand pixels of
shivering leaves count exactly the same as ten thousand pixels of deer."
It is worth knowing how badly.  wildlifecam14 watched a fence line for
twenty-one hours and kept 1,109 photographs.  Not one of them had an
animal in it -- every single one was wind in an oleander.  Meanwhile a
squirrel running along that same fence covers about 1,100 pixels of the
small frame, and step 5's rule was waiting for 20,000.  It was never
going to see one.

So this program borrows step 7's two ideas and leaves the neural network
behind, because it turns out the neural network was never the part that
mattered:

    1. Look for the biggest JOINED-UP patch of change, not the total
       number of changed pixels anywhere.  Wind scatters thousands of
       tiny specks across the whole frame.  A squirrel is one solid
       lump.  They look identical if you only count; they look nothing
       alike if you ask which pixels are touching.

    2. Compare each frame against a MEMORY of what the scene usually
       looks like, not against the frame before it.  An animal that
       stops moving disappears from a frame-to-frame comparison, and an
       animal holding still is exactly the photograph worth having.

There is a third change that is not an idea at all, just a number.  Step
5 looked four times a MINUTE.  A squirrel crosses this fence in about a
second, so it could run the whole way through the gap between two looks.
This program looks four times a second, like step 8 does.

Everything here is plain OpenCV.  Step 8 is 1,645 lines and only 16 of
them touch the AI camera, which is the whole point: the AI was doing
almost none of the work.

Run it by hand to watch it think:

    python3 step9_plain_motion.py

Or measure a site without filling the card up, which is what you want
before choosing BIGGEST_BLOB_TO_SAVE for a new position:

    python3 step9_plain_motion.py --record
"""

import argparse
import csv
import hashlib
import json
import os
import shutil
import socket
import time
from datetime import datetime

import cv2
import numpy as np
from picamera2 import Picamera2

PHOTO_DIR = "/var/www/html/photos"


# ------------------------------------------------------------
# What the camera is asked for
# ------------------------------------------------------------

# The photograph we keep.  2304x1296 is the Camera Module 3's native
# size when it groups its pixels in twos, so nothing is resized and the
# picture uses the whole width of the lens.
MAIN_SIZE = (2304, 1296)

# The small frame every decision below is made on.  It is free -- the
# camera's own hardware produces it -- and at 640x480 it is smaller than
# a postage stamp, which is why a Zero 2 W can afford to look at it four
# times a second.
LORES_SIZE = (640, 480)
MOTION_WIDTH, MOTION_HEIGHT = LORES_SIZE
MOTION_PIXELS = MOTION_WIDTH * MOTION_HEIGHT

# Four times a second, the same as step 8.  Step 5 used two seconds and
# that is long enough for an animal to cross the frame unseen.
LOOP_DELAY = 0.25


# ------------------------------------------------------------
# Rate limits, so a windy day cannot fill the card
# ------------------------------------------------------------
#
# The first real run had neither of these.  wildlifecam13, pointed at
# oleander in wind, kept 1,814 photographs in one hour -- about 1.5 GB --
# and would have filled its card in a day and a half with nothing on it.
# The blob floor is the wrong tool for that: raising it to 599 px only
# took that camera down to 1,350 an hour, and it costs distant animals on
# every other camera.  A ceiling on saves costs nothing an animal needs.
#
# Two limits, the same two step 8 has had since it was written:
#
#   SAVE_COOLDOWN       the least time between two photographs.  An
#                       animal that stays is photographed once a second,
#                       which is plenty; wind that trips the camera three
#                       times a second is not photographed three times.
#   MAX_SAVES_PER_HOUR  the ceiling.  When it is reached the camera keeps
#                       looking and keeps writing the CSV -- so nothing
#                       is hidden from the evaluation -- and simply stops
#                       saving until the hour has moved on.
#
# The numbers.  A 32 GB card has about 20 GB free and a photograph here
# is 0.5 to 0.9 MB; the darkness gate makes the night free, so a day
# costs about MAX_SAVES_PER_HOUR x 13 hours x 0.7 MB.  At 720 an hour
# that is 6.5 GB a day from a camera that never stops triggering: a
# three-day campout fits with room to spare, a windy week does not, and
# the 95% disk guard below is what stops it then.  Step 8 used 240 and 2
# seconds for a month; that was set for cards left out for weeks, and it
# threw away 13 frames of a crow that were wanted.  We would rather have
# the frames.
SAVE_COOLDOWN = 1.0
MAX_SAVES_PER_HOUR = 720


# ------------------------------------------------------------
# Cleaning up the picture before comparing it
# ------------------------------------------------------------

# A little blur first.  Every photograph has a faint fizz of sensor
# noise, and without the blur that fizz becomes thousands of specks that
# the "biggest joined-up patch" test then has to sort out.  These are
# step 8's numbers for a 640x480 motion frame.
BLUR_KERNEL = 11
OPEN_SIZE = 7      # rub out specks smaller than this
CLOSE_SIZE = 15    # fill in holes, so one animal is one patch

OPEN_KERNEL = cv2.getStructuringElement(cv2.MORPH_RECT, (OPEN_SIZE,) * 2)
CLOSE_KERNEL = cv2.getStructuringElement(cv2.MORPH_RECT, (CLOSE_SIZE,) * 2)


# ------------------------------------------------------------
# How different a pixel has to be to count as changed
# ------------------------------------------------------------

# A dark scene is noisier than a bright one, so a fixed number is either
# too jumpy at night or half asleep at noon.  Instead we measure the
# noise in each frame -- the middle value of how much every pixel
# disagrees with the background -- and set the bar a few times above it.
PIXEL_THRESHOLD = 25          # never go below this
NOISE_MULTIPLIER = 4          # bar = this many times the measured noise
MAX_PIXEL_THRESHOLD = 75      # never go above this


# ------------------------------------------------------------
# How big the biggest patch has to be before it is worth a photograph
# ------------------------------------------------------------

# THIS IS THE NUMBER TO TUNE for a new camera position, and the reason
# --record exists.  It is a fraction of the frame rather than a bare
# pixel count, so it means the same thing if the small frame ever
# changes size.
#
# For scale, measured against real animals the laptop found in this
# archive:
#
#     smallest animal ever detected     338 px
#     a squirrel on the ground        ~1,120 px
#     a crow close to the lens        ~7,900 px
#
# It started at step 8's 0.00098, about 301 px, and the first real run
# said that was too low: wildlifecam14 kept 4,463 photographs in five
# hours, and the ones between 301 and 600 px were tree shadow crawling
# across a white wall, not animals.  0.00195 is about 600 px, which
# halves the take and still sits well under a squirrel.
#
# It is deliberately NOT tuned any harder than that.  A back garden with
# a big sunlit wall is not a park, and a threshold fitted to this one
# wall would be deaf somewhere with no wall in it.  Run --record at a
# new site for an hour and read the CSV before changing it again.
MIN_BLOB_FRACTION = 0.00195
BIGGEST_BLOB_TO_SAVE = int(MIN_BLOB_FRACTION * MOTION_PIXELS)


# ------------------------------------------------------------
# Too dark to see anything
# ------------------------------------------------------------

# In the dark a Camera Module sees almost nothing but sensor noise, and
# that noise is not evenly scattered -- it clumps, so the "biggest
# joined-up patch" test finds patches in it.  On the night of
# 23 September this camera kept 359 photographs of a black frame, median
# blob 6,437 px, twenty times the daylight median.  There is nothing in
# any of them.
#
# So the first question is the one step 8 has always asked first: is
# there enough light to see anything at all?  Below this the camera
# keeps looking and keeps measuring -- the CSV still gets its row -- it
# just refuses to spend a photograph.
TOO_DARK_TO_SEE = 25          # mean brightness, 0-255


# ------------------------------------------------------------
# The memory of what the scene usually looks like
# ------------------------------------------------------------

# The background is a running average.  TAU is roughly how many seconds
# it takes to forget something, so a smaller TAU learns faster.
#
# It learns at two speeds on purpose: slowly EVERYWHERE, including
# underneath whatever is moving, and faster where nothing is happening.
# The slow-everywhere part is what stops a mistake becoming permanent --
# an earlier version froze the moving region completely, and camera
# shake wrote a scar along every sharp edge that never healed.
BACKGROUND_TAU = 10.0         # where the scene is still
BACKGROUND_TAU_BUSY = 40.0    # underneath something that is moving

ALPHA_QUIET = min(1.0, LOOP_DELAY / BACKGROUND_TAU)
ALPHA_BUSY = min(1.0, LOOP_DELAY / BACKGROUND_TAU_BUSY)


# ------------------------------------------------------------
# Who and what wrote this photograph
# ------------------------------------------------------------

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

    A Zero 2 W has no clock of its own, so the timestamps lie until the
    network corrects them.  This number comes from the kernel and starts
    at zero at power on, so the largest value in a run says how long the
    battery lasted.
    """
    try:
        with open("/proc/uptime") as f:
            return round(float(f.read().split()[0]), 1)
    except Exception:
        return None


def write_sidecar(photo_path, now, measurement):
    """Describe one photograph in JSON, beside the photograph."""
    information = {
        "camera": CAMERA_NAME,
        "code": CODE_VERSION,
        "time": now.isoformat(),

        "boot": BOOT_ID,
        "uptime_s": measurement["uptime_s"],

        "image": {
            "file": os.path.basename(photo_path),
            "width": MAIN_SIZE[0],
            "height": MAIN_SIZE[1],
        },

        # One rule, one trigger.  The name matches nothing in step 8 on
        # purpose: a photograph should say which program's rules kept it.
        "trigger": "biggest blob",

        "motion": {
            "biggest_blob": measurement["biggest_blob"],
            "blob_threshold": BIGGEST_BLOB_TO_SAVE,
            "blob_box": measurement["blob_box"],
            "changed_pixels": measurement["changed_pixels"],
            "pixel_threshold": measurement["pixel_threshold"],
            "noise": measurement["noise"],
            "mean_luma": measurement["mean_luma"],
            "lores_size": list(LORES_SIZE),
        },
    }
    try:
        with open(os.path.splitext(photo_path)[0] + ".json", "w") as f:
            json.dump(information, f, indent=2)
    except OSError as problem:
        # A sidecar is never worth losing a photograph over.
        print(f"could not write the sidecar: {problem}")


# ------------------------------------------------------------
# One row of measurements per LOOK, saved or not
# ------------------------------------------------------------
#
# This is the file that lets you choose BIGGEST_BLOB_TO_SAVE honestly.
# Sidecars only exist for photographs that were kept, so on their own
# they can never tell you what the camera walked past.  A row here is
# written every single time the camera looks, which means the numbers
# for the frames it rejected are there too.

MEASUREMENT_FIELDS = [
    "time", "uptime_s", "boot", "mean_luma", "noise", "pixel_threshold",
    "changed_pixels", "biggest_blob", "blob_x", "blob_y", "blob_w", "blob_h",
    "saved",
    # Why a frame the rules wanted was not saved: "cooldown", "hourly
    # limit", or empty.  Without this a rate limit would look, in the
    # CSV, exactly like the rules deciding there was nothing there --
    # and somebody would spend a weekend tuning the wrong number.
    "held",
]


class Measurements:
    """Appends one row per look, into a CSV per day."""

    def __init__(self):
        self.day = None
        self.handle = None
        self.writer = None

    def record(self, now, measurement, saved, held=""):
        day_directory = f"{PHOTO_DIR}/{now.strftime('%Y-%m-%d')}"
        if self.day != day_directory:
            self.close()
            os.makedirs(day_directory, exist_ok=True)
            path = f"{day_directory}/measurements-{CAMERA_NAME}.csv"
            new_file = not os.path.exists(path)
            self.handle = open(path, "a", newline="")
            self.writer = csv.DictWriter(self.handle,
                                         fieldnames=MEASUREMENT_FIELDS)
            if new_file:
                self.writer.writeheader()
            self.day = day_directory

        box = measurement["blob_box"] or (None, None, None, None)
        self.writer.writerow({
            "time": now.isoformat(),
            "uptime_s": measurement["uptime_s"],
            "boot": BOOT_ID,
            "mean_luma": measurement["mean_luma"],
            "noise": measurement["noise"],
            "pixel_threshold": measurement["pixel_threshold"],
            "changed_pixels": measurement["changed_pixels"],
            "biggest_blob": measurement["biggest_blob"],
            "blob_x": box[0], "blob_y": box[1],
            "blob_w": box[2], "blob_h": box[3],
            "saved": int(bool(saved)),
            "held": held,
        })
        # Flush every row.  A trail camera is switched off by having its
        # battery pulled, so anything still sitting in a buffer is lost.
        self.handle.flush()

    def close(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None
            self.writer = None


# ------------------------------------------------------------
# The one measurement this program makes
# ------------------------------------------------------------

def look(blurred, background):
    """Compare one small frame with the background.

    Returns the mask of what changed and a dictionary of numbers.  The
    mask goes back into the background update; the numbers go into the
    CSV and, if we keep the photograph, into its sidecar.
    """
    reference = background.astype("uint8")
    difference = cv2.absdiff(blurred, reference)

    # How noisy is this frame?  The middle value of "how much does each
    # pixel disagree with the background" is a good answer, because most
    # of the frame is not moving, so most of those numbers ARE the noise.
    noise = float(np.median(difference))
    pixel_threshold = min(MAX_PIXEL_THRESHOLD,
                          max(PIXEL_THRESHOLD,
                              int(NOISE_MULTIPLIER * noise + 5)))

    _, mask = cv2.threshold(difference, pixel_threshold, 255,
                            cv2.THRESH_BINARY)

    # Count the changed pixels BEFORE tidying up, so the number means
    # "how much of the frame disagrees with the background".  Note it is
    # NOT step 5's number and the two must not be compared: step 5
    # measured against the PREVIOUS FRAME, which is a different question
    # and gives a much smaller answer for anything moving slowly.
    changed_pixels = int(np.count_nonzero(mask))

    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, OPEN_KERNEL)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, CLOSE_KERNEL)

    # Find every joined-up patch and keep the biggest.  Label 0 is the
    # background, so the search starts at 1.
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask,
                                                          connectivity=8)
    biggest_blob = 0
    blob_box = None
    for i in range(1, count):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area > biggest_blob:
            biggest_blob = area
            blob_box = (int(stats[i, cv2.CC_STAT_LEFT]),
                        int(stats[i, cv2.CC_STAT_TOP]),
                        int(stats[i, cv2.CC_STAT_WIDTH]),
                        int(stats[i, cv2.CC_STAT_HEIGHT]))

    measurement = {
        "uptime_s": seconds_since_boot(),
        "mean_luma": round(float(blurred.mean()), 2),
        "noise": round(noise, 2),
        "pixel_threshold": pixel_threshold,
        "changed_pixels": changed_pixels,
        "biggest_blob": biggest_blob,
        "blob_box": blob_box,
    }
    return mask, measurement


# ------------------------------------------------------------
# Watching
# ------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--record", action="store_true",
                        help="save no photographs; only write the "
                             "measurements CSV, for choosing "
                             "BIGGEST_BLOB_TO_SAVE at a new site")
    parser.add_argument("--quiet", action="store_true",
                        help="print only the looks that keep a photograph")
    options = parser.parse_args()

    picam2 = Picamera2()
    picam2.configure(picam2.create_preview_configuration(
        main={"size": MAIN_SIZE, "format": "RGB888"},
        lores={"size": LORES_SIZE, "format": "YUV420"},
        buffer_count=2,
    ))
    picam2.start()
    time.sleep(2)

    print(f"Photographs will be {MAIN_SIZE[0]}x{MAIN_SIZE[1]}; "
          f"watching at {MOTION_WIDTH}x{MOTION_HEIGHT}, "
          f"{1 / LOOP_DELAY:.0f} times a second.")
    print(f"Keeping a photograph when the biggest joined-up patch of "
          f"change reaches {BIGGEST_BLOB_TO_SAVE} pixels, and only while "
          f"there is enough light to see (brightness {TOO_DARK_TO_SEE}+).")
    if options.record:
        print("--record: measuring only, no photographs will be saved.")

    measurements = Measurements()
    background = None

    # For the rate limits: when we last saved, and every save in the
    # last hour.  Wall-clock seconds, from time.monotonic(), so a clock
    # that jumps when the network is found cannot open or close the gate.
    last_save_time = -1e9
    save_times = []

    try:
        while True:
            disk = shutil.disk_usage("/")
            percent_used = disk.used / disk.total * 100
            if percent_used >= 95:
                print(f"Filesystem is {percent_used:.1f}% full. Stopping "
                      f"before it fills completely.")
                break

            # The small stream arrives as YUV420: the grey picture first,
            # then the colour squashed underneath it.  The top rows ARE
            # the grey image, so there is nothing to convert.
            lores = picam2.capture_array("lores")
            gray = lores[:MOTION_HEIGHT, :MOTION_WIDTH]
            blurred = cv2.GaussianBlur(gray, (BLUR_KERNEL, BLUR_KERNEL), 0)

            if background is None:
                print("first look: remembering the scene.")
                background = blurred.astype("float32")
                time.sleep(LOOP_DELAY)
                continue

            mask, measurement = look(blurred, background)
            now = datetime.now()
            # Light first, then size -- the same order step 8 asks them
            # in, and for the same reason: in the dark the size question
            # has no meaningful answer.
            too_dark = measurement["mean_luma"] < TOO_DARK_TO_SEE
            worth_keeping = (not too_dark
                             and measurement["biggest_blob"]
                             >= BIGGEST_BLOB_TO_SAVE)

            # The rules wanted it.  Now the rate limits get a say, and
            # the CSV records their answer separately from the rules'.
            held = ""
            if worth_keeping:
                moment = time.monotonic()
                save_times = [t for t in save_times if moment - t < 3600.0]
                if moment - last_save_time < SAVE_COOLDOWN:
                    held = "cooldown"
                elif len(save_times) >= MAX_SAVES_PER_HOUR:
                    held = "hourly limit"

            if worth_keeping and not held and not options.record:
                day_directory = f"{PHOTO_DIR}/{now.strftime('%Y-%m-%d')}"
                os.makedirs(day_directory, exist_ok=True)
                # Milliseconds in the name, like step 8's training
                # bursts.  This program looks four times a SECOND, so a
                # name good only to the second quietly overwrites its
                # own work: the first run kept 4,463 photographs and left
                # 2,225 files, losing every frame of a burst but the
                # last -- which are the ones where something is moving
                # fastest.
                stamp = f"{now.strftime('%H%M%S')}_{now.microsecond // 1000:03d}"
                filename = f"{day_directory}/{stamp}.jpg"
                picam2.capture_file(filename)      # from "main": the big one
                write_sidecar(filename, now, measurement)
                last_save_time = moment
                save_times.append(moment)
                print(f"kept {os.path.basename(filename)} -- "
                      f"biggest patch {measurement['biggest_blob']} px "
                      f"at {measurement['blob_box']}")
            elif held and not options.quiet:
                print(f"wanted it ({measurement['biggest_blob']} px) but "
                      f"held: {held}  "
                      f"[{len(save_times)} saves this hour]")
            elif not options.quiet:
                if too_dark:
                    print(f"too dark to see anything "
                          f"(brightness {measurement['mean_luma']:.1f}, "
                          f"need {TOO_DARK_TO_SEE})")
                else:
                    print(f"biggest patch {measurement['biggest_blob']:6d} px "
                          f"(need {BIGGEST_BLOB_TO_SAVE})  "
                          f"changed {measurement['changed_pixels']:6d}  "
                          f"noise {measurement['noise']:5.1f}  "
                          f"bar {measurement['pixel_threshold']:3d}")

            measurements.record(now, measurement,
                                saved=worth_keeping and not held
                                and not options.record,
                                held=held)

            # Update the memory: slowly everywhere, faster where nothing
            # moved.  cv2 wants the mask the other way round -- it
            # updates where the mask is non-zero -- so we invert it.
            still = cv2.bitwise_not(mask)
            cv2.accumulateWeighted(blurred, background, ALPHA_BUSY)
            cv2.accumulateWeighted(blurred, background, ALPHA_QUIET,
                                   mask=still)

            time.sleep(LOOP_DELAY)
    finally:
        measurements.close()
        picam2.stop()
        print("Program finished safely.")


if __name__ == "__main__":
    main()
