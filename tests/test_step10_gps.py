"""Tests for the GPS part of step10_ai_camera.py and step10_camera_module.py.

    python3 -m unittest discover tests

The camera programs cannot be imported on a laptop: they open the camera
as soon as they start.  So these tests pull the GPS functions out of each
file's source and run them with a fake camera and a fake OpenCV.  They
also check the shared GPS code is the same in both files.

The EXIF tests need piexif, which every camera has (Picamera2 uses it);
they are skipped where it is missing.
"""

import ast
import base64
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

try:
    import piexif
except ImportError:
    piexif = None

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "..")

SHARED = ["GPS_FILE", "NETWORK_SYNCED", "gps_cache", "complaints",
          "complain", "where_and_when", "gps_blocks", "gps_exif",
          "onto_the_card"]

# An 8x8 JPEG, as cv2.imencode makes it.
TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQFxQY"
    "GBcUFhYaHSUfGhsjHBYWICwgIyYnKSopGR8tMC0oMCUoKSj/2wBDAQcHBwoIChMKChMoGhYa"
    "KCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCj/wAAR"
    "CAAIAAgDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAA"
    "AgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkK"
    "FhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWG"
    "h4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl"
    "5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREA"
    "AgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYk"
    "NOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOE"
    "hYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk"
    "5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDzn9on/mX/APt4/wDaVFFFePkH/Ivp/P8A"
    "9KZ6ud/79U+X5I//2Q==")


def definitions(filename, names):
    """The top-level assignments and functions with these names, as AST."""
    with open(os.path.join(REPO, filename)) as f:
        tree = ast.parse(f.read())
    found = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            found[node.name] = node
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    found[target.id] = node
    missing = set(names) - set(found)
    if missing:
        raise AssertionError(f"{filename} is missing {sorted(missing)}")
    return found


def load(filename, extra, namespace):
    """Run just the GPS code from one camera program, in namespace."""
    nodes = definitions(filename, SHARED + extra)
    module = ast.Module(body=list(nodes.values()), type_ignores=[])
    exec(compile(module, filename, "exec"), namespace)
    return namespace


class FakeArray:
    def __init__(self, data):
        self.data = data

    def tobytes(self):
        return self.data


class FakeCv2:
    IMWRITE_JPEG_QUALITY = 1

    def __init__(self, encode_ok=True):
        self.encode_ok = encode_ok
        self.imwrites = []

    def imencode(self, extension, image, params):
        return self.encode_ok, FakeArray(TINY_JPEG)

    def imwrite(self, filename, image, params):
        self.imwrites.append(filename)
        with open(filename, "wb") as f:
            f.write(TINY_JPEG)
        return True


class FakePicamera2:
    """capture_file as Picamera2 0.3.x has it, or an old one without exif_data."""

    def __init__(self, knows_exif_data=True):
        self.knows_exif_data = knows_exif_data
        self.calls = []

    def capture_file(self, filename, **options):
        if "exif_data" in options and not self.knows_exif_data:
            raise TypeError("capture_file() got an unexpected keyword "
                            "argument 'exif_data'")
        self.calls.append(options)
        with open(filename, "wb") as f:
            f.write(TINY_JPEG)


def a_fix(boot="thisboot", lat=37.333333, lon=-121.7, alt=51.4, hdop=0.86,
          first_set=87.0):
    return {"camera": "wildlifecam10", "boot": boot, "receiver": "u-blox 7",
            "position": {"lat": lat, "lon": lon, "alt_m": alt, "hdop": hdop,
                         "satellites": 11, "samples": 600, "spread_m": 1.0,
                         "attachment": 1},
            "clock": {"set_by_gps": first_set is not None,
                      "first_set_uptime_s": first_set,
                      "last_set_uptime_s": first_set},
            "attachments": []}


def decimal(dms, ref):
    (d, dn), (m, mn), (s, sn) = dms
    value = d / dn + m / mn / 60 + s / sn / 3600
    return -value if ref in (b"S", b"W", "S", "W") else value


class BothFiles(unittest.TestCase):

    def test_shared_gps_code_is_identical(self):
        ai = definitions("step10_ai_camera.py", SHARED)
        plain = definitions("step10_camera_module.py", SHARED)
        for name in SHARED:
            self.assertEqual(ast.dump(ai[name]), ast.dump(plain[name]),
                             f"{name} differs between the two programs")


class Base(unittest.TestCase):
    FILE = "step10_camera_module.py"
    EXTRA = ["capture_with_gps"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.ns = load(self.FILE, self.EXTRA, {
            "os": os, "json": json, "io": io, "piexif": piexif,
            "BOOT_ID": "thisboot", "JPEG_QUALITY": 90})
        self.ns["GPS_FILE"] = os.path.join(self.dir, "gps.json")
        self.ns["NETWORK_SYNCED"] = os.path.join(self.dir, "synchronized")

    def tearDown(self):
        self.tmp.cleanup()

    def write_state(self, state):
        temporary = self.ns["GPS_FILE"] + ".tmp"
        with open(temporary, "w") as f:
            if isinstance(state, str):
                f.write(state)
            else:
                json.dump(state, f)
        os.replace(temporary, self.ns["GPS_FILE"])   # as wildlife_gps.py does

    def read(self):
        with redirect_stdout(io.StringIO()) as out:
            gps = self.ns["where_and_when"]()
        return gps, out.getvalue()


class OntoTheCard(Base):

    def test_open_file_path_and_folder(self):
        path = os.path.join(self.dir, "photo.jpg")
        with open(path, "wb") as f:
            f.write(b"bytes")
            self.ns["onto_the_card"](f)
        self.ns["onto_the_card"](path)
        self.ns["onto_the_card"](self.dir)
        self.assertEqual(self.ns["complaints"], set())

    def test_failure_is_said_once_and_never_raised(self):
        missing = os.path.join(self.dir, "gone.jpg")
        with redirect_stdout(io.StringIO()) as out:
            self.ns["onto_the_card"](missing)
            self.ns["onto_the_card"](missing)
        self.assertEqual(out.getvalue().count("could not push"), 1)


class WhereAndWhen(Base):

    def test_no_file(self):
        self.assertEqual(self.read(), (None, ""))

    def test_this_boot(self):
        self.write_state(a_fix())
        gps, _ = self.read()
        self.assertTrue(gps["this_boot"])

    def test_earlier_boot(self):
        self.write_state(a_fix(boot="oldboot"))
        gps, _ = self.read()
        self.assertFalse(gps["this_boot"])

    def test_broken_json_is_ignored_and_said_once(self):
        self.write_state("{not json")
        gps, out = self.read()
        self.assertIsNone(gps)
        self.assertIn("ignoring", out)
        self.ns["gps_cache"]["stamp"] = None       # force a re-read
        _, out = self.read()
        self.assertEqual(out, "")                  # not said twice

    def test_no_position_is_ignored(self):
        state = a_fix()
        state["position"] = None
        self.write_state(state)
        self.assertIsNone(self.read()[0])

    def test_text_where_numbers_belong_is_ignored(self):
        self.write_state(a_fix(lat="somewhere"))
        self.assertIsNone(self.read()[0])

    def test_read_again_only_when_replaced(self):
        self.write_state(a_fix())
        first, _ = self.read()
        first["marker"] = True                     # the cached object
        self.assertTrue(self.read()[0].get("marker"))
        self.write_state(a_fix(lat=38.0))          # replaced: new inode
        second, _ = self.read()
        self.assertNotIn("marker", second)
        self.assertEqual(second["position"]["lat"], 38.0)

    def test_file_removed(self):
        self.write_state(a_fix())
        self.read()
        os.remove(self.ns["GPS_FILE"])
        self.assertIsNone(self.read()[0])


class Sidecar(Base):

    def blocks(self, gps, uptime_s):
        with redirect_stdout(io.StringIO()):
            return self.ns["gps_blocks"](gps, uptime_s)

    def test_no_gps_saved_clock(self):
        self.assertEqual(self.blocks(None, 100.0),
                         {"clock": {"source": "saved"}})

    def test_network(self):
        open(self.ns["NETWORK_SYNCED"], "w").close()
        self.assertEqual(self.blocks(None, 100.0)["clock"]["source"],
                         "network")

    def test_gps_set_the_clock_before_this_photograph(self):
        blocks = self.blocks(dict(a_fix(), this_boot=True), 100.0)
        self.assertEqual(blocks["clock"], {"source": "gps",
                                           "set_uptime_s": 87.0})
        self.assertEqual(blocks["gps"]["boot"], "thisboot")
        self.assertAlmostEqual(blocks["gps"]["lat"], 37.333333)

    def test_photograph_before_the_clock_was_set(self):
        blocks = self.blocks(dict(a_fix(), this_boot=True), 50.0)
        self.assertEqual(blocks["clock"]["source"], "saved")
        self.assertIn("gps", blocks)        # where it is, even before the clock

    def test_gps_never_set_the_clock(self):
        blocks = self.blocks(dict(a_fix(first_set=None), this_boot=True), 99.0)
        self.assertEqual(blocks["clock"]["source"], "saved")

    def test_earlier_boot_is_not_used(self):
        blocks = self.blocks(dict(a_fix(boot="oldboot"), this_boot=False),
                             100.0)
        self.assertEqual(blocks, {"clock": {"source": "saved"}})

    def test_cloned_card_ignores_the_masters_fix(self):
        self.write_state(a_fix(boot="masterboot"))
        gps, _ = self.read()
        self.assertEqual(self.blocks(gps, 100.0),
                         {"clock": {"source": "saved"}})
        self.assertIsNone(self.ns["gps_exif"](gps))

    def test_unknown_uptime(self):
        blocks = self.blocks(dict(a_fix(), this_boot=True), None)
        self.assertEqual(blocks["clock"]["source"], "saved")

    def test_broken_fix_still_gives_a_clock(self):
        blocks = self.blocks({"this_boot": True, "position": {}}, 100.0)
        self.assertNotIn("gps", blocks)
        self.assertEqual(blocks["clock"]["source"], "saved")

    def test_serialisable(self):
        json.dumps(self.blocks(dict(a_fix(), this_boot=True), 100.0))


@unittest.skipIf(piexif is None, "piexif not installed (every camera has it)")
class Exif(Base):

    def tags(self, gps):
        with redirect_stdout(io.StringIO()):
            return self.ns["gps_exif"](gps)

    def test_only_this_boot(self):
        self.assertIsNone(self.tags(None))
        self.assertIsNone(self.tags(dict(a_fix(boot="old"), this_boot=False)))

    def test_without_piexif(self):
        self.ns["piexif"] = None
        self.assertIsNone(self.tags(dict(a_fix(), this_boot=True)))

    def test_round_trip(self):
        tags = self.tags(dict(a_fix(), this_boot=True))
        back = piexif.load(piexif.dump({"GPS": tags}))["GPS"]
        G = piexif.GPSIFD
        self.assertAlmostEqual(decimal(back[G.GPSLatitude],
                                       back[G.GPSLatitudeRef]), 37.333333,
                               places=5)
        self.assertAlmostEqual(decimal(back[G.GPSLongitude],
                                       back[G.GPSLongitudeRef]), -121.7,
                               places=5)
        self.assertEqual(back[G.GPSLongitudeRef], b"W")
        self.assertEqual(back[G.GPSAltitude], (514, 10))
        self.assertEqual(back[G.GPSAltitudeRef], 0)
        self.assertEqual(back[G.GPSMapDatum], b"WGS-84")

    def test_below_sea_level_and_south_east(self):
        tags = self.tags(dict(a_fix(lat=-33.9, lon=151.2, alt=-3.0),
                              this_boot=True))
        G = piexif.GPSIFD
        self.assertEqual(tags[G.GPSLatitudeRef], "S")
        self.assertEqual(tags[G.GPSLongitudeRef], "E")
        self.assertEqual(tags[G.GPSAltitudeRef], 1)

    def test_no_altitude_or_hdop(self):
        tags = self.tags(dict(a_fix(alt=None, hdop=None), this_boot=True))
        self.assertNotIn(piexif.GPSIFD.GPSAltitude, tags)
        self.assertNotIn(piexif.GPSIFD.GPSDOP, tags)

    def test_seconds_never_reach_sixty(self):
        tags = self.tags(dict(a_fix(lat=37.9999999), this_boot=True))
        degrees, minutes, seconds = tags[piexif.GPSIFD.GPSLatitude]
        self.assertEqual(degrees, (38, 1))
        self.assertEqual(minutes, (0, 1))
        self.assertLess(seconds[0], 6000)


@unittest.skipIf(piexif is None, "piexif not installed (every camera has it)")
class CameraModuleCapture(Base):

    def capture(self, camera, tags):
        filename = os.path.join(self.dir, "photo.jpg")
        with redirect_stdout(io.StringIO()) as out:
            self.ns["capture_with_gps"](camera, filename, tags)
        return filename, out.getvalue()

    def test_tags_go_to_exif_data(self):
        camera = FakePicamera2()
        tags = self.ns["gps_exif"](dict(a_fix(), this_boot=True))
        self.capture(camera, tags)
        self.assertEqual(camera.calls, [{"exif_data": {"GPS": tags}}])

    def test_no_tags_plain_capture(self):
        camera = FakePicamera2()
        self.capture(camera, None)
        self.assertEqual(camera.calls, [{}])

    def test_old_picamera2_falls_back(self):
        camera = FakePicamera2(knows_exif_data=False)
        tags = self.ns["gps_exif"](dict(a_fix(), this_boot=True))
        filename, out = self.capture(camera, tags)
        self.assertEqual(camera.calls, [{}])
        self.assertTrue(os.path.exists(filename))
        self.assertIn("saving without GPS tags", out)

    def test_picamera2_merge_and_splice(self):
        """Our tags through the same steps Picamera2 0.3.37 takes."""
        tags = self.ns["gps_exif"](dict(a_fix(), this_boot=True))
        theirs = {"0th": {piexif.ImageIFD.Make: "Raspberry Pi"},
                  "Exif": {piexif.ExifIFD.ISOSpeedRatings: 100}}
        exif = piexif.dump(theirs | {"GPS": tags})
        jpeg = (TINY_JPEG[:2] + bytes.fromhex("ffe1")
                + (len(exif) + 2).to_bytes(2, "big") + exif + TINY_JPEG[2:])
        back = piexif.load(jpeg)
        self.assertEqual(back["0th"][piexif.ImageIFD.Make], b"Raspberry Pi")
        self.assertIn(piexif.GPSIFD.GPSLatitude, back["GPS"])


@unittest.skipIf(piexif is None, "piexif not installed (every camera has it)")
class AiCameraWrite(Base):
    FILE = "step10_ai_camera.py"
    EXTRA = ["write_jpeg"]

    def write(self, cv2, tags):
        self.ns["cv2"] = cv2
        filename = os.path.join(self.dir, "photo.jpg")
        with redirect_stdout(io.StringIO()) as out:
            self.ns["write_jpeg"](filename, object(), tags)
        return filename, out.getvalue()

    def pushed(self):
        calls = []
        self.ns["onto_the_card"] = lambda target: calls.append(
            getattr(target, "name", target))
        return calls

    def test_both_paths_push_the_photo_onto_the_card(self):
        tags = self.ns["gps_exif"](dict(a_fix(), this_boot=True))
        for given in (tags, None):
            calls = self.pushed()
            filename, _ = self.write(FakeCv2(), given)
            self.assertEqual(calls, [filename])

    def test_tags_in_the_file_written_once(self):
        cv2 = FakeCv2()
        tags = self.ns["gps_exif"](dict(a_fix(), this_boot=True))
        filename, _ = self.write(cv2, tags)
        self.assertEqual(cv2.imwrites, [])          # did not use imwrite
        with open(filename, "rb") as f:
            data = f.read()
        self.assertTrue(data.startswith(b"\xff\xd8"))
        self.assertTrue(data.endswith(b"\xff\xd9"))
        self.assertIn(piexif.GPSIFD.GPSLatitude, piexif.load(data)["GPS"])
        self.assertLess(len(data) - len(TINY_JPEG), 400)

    def test_no_tags_is_plain_imwrite(self):
        cv2 = FakeCv2()
        self.write(cv2, None)
        self.assertEqual(len(cv2.imwrites), 1)

    def test_encode_failure_falls_back(self):
        cv2 = FakeCv2(encode_ok=False)
        tags = self.ns["gps_exif"](dict(a_fix(), this_boot=True))
        self.write(cv2, tags)
        self.assertEqual(len(cv2.imwrites), 1)

    def test_piexif_failure_falls_back(self):
        cv2 = FakeCv2()
        filename, out = self.write(cv2, {"not": "a tag"})
        self.assertEqual(len(cv2.imwrites), 1)
        self.assertIn("saving without GPS tags", out)
        self.assertTrue(os.path.exists(filename))


if __name__ == "__main__":
    unittest.main()
