"""Where and when: putting the GPS dongle's answer into each photograph.

Not a step, and not a program: the step 10 camera programs import this.
wildlife_gps.py runs while a GPS dongle is plugged in -- at setup and at
takedown -- and writes what it learned to GPS_FILE.  When a camera
program saves a photograph it asks this module three things:

    gps = where_and_when(BOOT_ID)      what the dongle said, if this boot
    gps_blocks(gps, uptime_s)          the "gps" and "clock" sidecar blocks
    gps_exif(gps)                      the GPS tags for the JPEG

and saves with write_jpeg() (AI Camera, OpenCV) or capture_with_gps()
(Camera Module, Picamera2), which put the tags in and push the
photograph onto the card with onto_the_card().

Every part of this is allowed to fail: a photograph without a location
is fine, a lost photograph is not.  The whole design is in
docs/gps-design.md.
"""

import io
import json
import os

GPS_FILE = "/var/lib/wildlifecam/gps.json"
NETWORK_SYNCED = "/run/systemd/timesync/synchronized"

try:
    import piexif          # Picamera2 needs it too, so it is always here
except ImportError:
    piexif = None

gps_cache = {"stamp": None, "gps": None}
complaints = set()


def complain(message):
    """Print a problem once, not at every photograph."""
    if message not in complaints:
        complaints.add(message)
        print(message, flush=True)


def where_and_when(boot_id):
    """What the GPS program last wrote, and whether it is about this boot.

    boot_id is the camera program's own boot id, the eight characters
    every sidecar carries.

    Kept in memory.  We only read the file again when it has been
    replaced -- a few times a boot at most -- which one os.stat() tells
    us.  The test is "changed", never "newer": the file's time comes from
    the very clock GPS is busy correcting.

    Returns None when there is no usable fix.
    """
    try:
        info = os.stat(GPS_FILE)
    except OSError:
        return None                      # no dongle this boot, or ever
    stamp = (info.st_ino, info.st_mtime_ns, info.st_size)
    if stamp != gps_cache["stamp"]:
        gps_cache["stamp"] = stamp
        gps_cache["gps"] = None
        try:
            with open(GPS_FILE) as f:
                gps = json.load(f)
            position = gps["position"]
            float(position["lat"])       # must both be numbers
            float(position["lon"])
            gps["this_boot"] = gps.get("boot") == boot_id
            gps_cache["gps"] = gps
        except Exception as problem:
            complain(f"ignoring {GPS_FILE}: {problem!r}")
    return gps_cache["gps"]


def gps_blocks(gps, uptime_s):
    """The "gps" and "clock" blocks for a photograph's sidecar.

    The "gps" block is only ever for a fix from THIS boot.  A file left by
    an earlier boot -- a battery swap, or a card cloned from another
    camera -- says nothing about where this run's photographs were taken.

    clock.source answers "can I trust this photograph's time?":
        gps      GPS set the clock this boot, before this photograph
        network  a time server set it (systemd-timesyncd says so)
        saved    neither: whatever the Pi woke up believing, plus uptime
    """
    blocks = {}
    clock = {"source": "saved"}
    try:
        if os.path.exists(NETWORK_SYNCED):
            clock = {"source": "network"}
        if gps and gps["this_boot"]:
            position = gps["position"]
            blocks["gps"] = {
                "boot": gps.get("boot"),
                "lat": float(position["lat"]),
                "lon": float(position["lon"]),
                "alt_m": position.get("alt_m"),
                "hdop": position.get("hdop"),
                "samples": position.get("samples"),
            }
            set_at = (gps.get("clock") or {}).get("first_set_uptime_s")
            if (set_at is not None
                    and uptime_s is not None and uptime_s >= set_at):
                clock = {"source": "gps", "set_uptime_s": set_at}
    except Exception as problem:
        complain(f"could not describe the GPS fix: {problem!r}")
    blocks["clock"] = clock
    return blocks


def gps_exif(gps):
    """GPS tags for the JPEG, as a piexif "GPS" block, or None.

    Only for a fix from THIS boot, like the sidecar's "gps" block.
    """
    if piexif is None or not gps or not gps.get("this_boot"):
        return None
    try:
        position = gps["position"]
        lat = float(position["lat"])
        lon = float(position["lon"])

        def degrees_minutes_seconds(value):
            # EXIF wants three fractions: 37.33333 -> 37/1, 19/1, 5999/100
            hundredths = round(abs(value) * 360000)
            return ((hundredths // 360000, 1),
                    (hundredths % 360000 // 6000, 1),
                    (hundredths % 6000, 100))

        tags = {
            piexif.GPSIFD.GPSVersionID: (2, 3, 0, 0),
            piexif.GPSIFD.GPSLatitudeRef: "N" if lat >= 0 else "S",
            piexif.GPSIFD.GPSLatitude: degrees_minutes_seconds(lat),
            piexif.GPSIFD.GPSLongitudeRef: "E" if lon >= 0 else "W",
            piexif.GPSIFD.GPSLongitude: degrees_minutes_seconds(lon),
            piexif.GPSIFD.GPSMapDatum: "WGS-84",
        }
        if position.get("alt_m") is not None:
            altitude = float(position["alt_m"])
            tags[piexif.GPSIFD.GPSAltitudeRef] = 0 if altitude >= 0 else 1
            tags[piexif.GPSIFD.GPSAltitude] = (round(abs(altitude) * 10), 10)
        if position.get("hdop") is not None:
            tags[piexif.GPSIFD.GPSDOP] = (round(float(position["hdop"]) * 100),
                                          100)
        piexif.dump({"GPS": tags})       # fail HERE, never while saving
        return tags
    except Exception as problem:
        complain(f"saving without GPS tags: {problem!r}")
        return None


def onto_the_card(target):
    """Make sure a file is really on the SD card, not just in memory.

    Linux keeps newly written data in memory for up to thirty seconds
    before it writes it to the card.  A camera unplugged in that time
    leaves files that exist but are empty: wildlifecam15 lost its last 22
    photographs that way on 4 October 2026.  os.fsync asks for the data
    to be written now.  The card writes the same bytes either way, so it
    costs no battery to speak of; what changes is that we wait for it,
    a fraction of a second per photograph, and only when one is saved.
    target is an open file, a file name, or a folder (so a new file's
    name is on the card too).
    """
    try:
        if hasattr(target, "fileno"):
            target.flush()
            os.fsync(target.fileno())
        else:
            descriptor = os.open(target, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except OSError as problem:
        name = getattr(target, "name", target)
        complain(f"could not push {name} onto the card: {problem!r}")


def capture_with_gps(picam2, filename, gps_tags):
    """The Camera Module's save: picam2.capture_file, with GPS tags added.

    Picamera2 encodes the JPEG in memory and splices its own EXIF block
    (camera, exposure, time) in as it writes the file, once.  exif_data
    adds our GPS tags to that same block, so there is no second write.
    If that fails for any reason -- an older Picamera2 without exif_data,
    say -- the photograph is taken again without them, as step 9 did.
    """
    if gps_tags:
        try:
            picam2.capture_file(filename, exif_data={"GPS": gps_tags})
            return
        except Exception as problem:
            complain(f"saving without GPS tags: {problem!r}")
    picam2.capture_file(filename)


def write_jpeg(filename, image, gps_tags, jpeg_quality):
    """The AI Camera's save: cv2.imwrite, with GPS tags, written once.

    cv2.imencode makes exactly the bytes cv2.imwrite would have written;
    piexif splices the tags in, in memory; then one write.  Nothing is
    written and read back.  If anything about the GPS part fails, the
    photograph is written exactly as step 8 wrote it.
    """
    import cv2                  # only the camera programs need OpenCV

    quality = [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
    if gps_tags:
        try:
            encoded_ok, encoded = cv2.imencode(".jpg", image, quality)
            if encoded_ok:
                with_tags = io.BytesIO()
                piexif.insert(piexif.dump({"GPS": gps_tags}),
                              encoded.tobytes(), with_tags)
                with open(filename, "wb") as f:
                    f.write(with_tags.getvalue())
                    onto_the_card(f)
                return
        except Exception as problem:
            complain(f"saving without GPS tags: {problem!r}")
    cv2.imwrite(filename, image, quality)
    onto_the_card(filename)
