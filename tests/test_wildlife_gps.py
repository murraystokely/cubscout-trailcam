"""Tests for wildlife_gps.py, with made-up sentences instead of a dongle.

    python3 -m unittest discover tests

Standard library only, so they run on a Pi or a laptop.  The coordinates
are invented (37.33333, -121.70000); never put a real recording from a
back garden in here.
"""

import csv
import glob
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from functools import reduce

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import wildlife_gps  # noqa: E402


def sentence(body):
    """'GPRMC,...' -> '$GPRMC,...*63' with a correct checksum."""
    checksum = reduce(lambda total, ch: total ^ ord(ch), body, 0)
    return f"${body}*{checksum:02X}"


START = datetime(2026, 10, 11, 16, 2, 0, tzinfo=timezone.utc)


def one_second(i, valid=True, satellites=7, year=None, lat="3720.00000",
               lon="12142.00000", offset_s=412337.6, uptime_start=80.0):
    """The RMC and GGA lines for second i, as (line, pi_time, uptime)."""
    utc = START + timedelta(seconds=i)
    if year:
        utc = utc.replace(year=year)
    clock = utc.strftime("%H%M%S.00")
    date = utc.strftime("%d%m%y")
    status = "A" if valid else "V"
    if valid:
        rmc = f"GPRMC,{clock},{status},{lat},N,{lon},W,0.02,,{date},,,A"
        gga = (f"GPGGA,{clock},{lat},N,{lon},W,1,{satellites:02d},1.2,"
               f"412.3,M,-30.1,M,,")
    else:
        rmc = f"GPRMC,{clock},V,,,,,,,{date},,,N"
        gga = f"GPGGA,{clock},,,,,0,{satellites:02d},99.99,,,,,,"
    pi_time = utc.timestamp() - offset_s      # offset_s is GPS minus Pi
    uptime = uptime_start + i
    return [(sentence(rmc), pi_time, uptime),
            (sentence(gga), pi_time + 0.05, uptime + 0.05)]


def seconds(n, **options):
    lines = []
    for i in range(n):
        lines += one_second(i, **options)
    return lines


class ReadingSentences(unittest.TestCase):

    def test_checksum(self):
        good = "$GPRMC,160211.00,A,3720.00000,N,12142.00000,W,0.021,,111026,,,A*63"
        self.assertTrue(wildlife_gps.checksum_ok(good))
        self.assertFalse(wildlife_gps.checksum_ok(good.replace("3720", "3721")))
        self.assertFalse(wildlife_gps.checksum_ok("GPRMC,no dollar*00"))

    def test_degrees_then_minutes(self):
        self.assertAlmostEqual(wildlife_gps.to_degrees("3720.00000", "N"),
                               37.333333, places=5)
        self.assertAlmostEqual(wildlife_gps.to_degrees("12142.00000", "W"),
                               -121.7, places=5)
        self.assertIsNone(wildlife_gps.to_degrees("", "N"))

    def test_one_fix_per_second(self):
        fixes = list(wildlife_gps.fixes_from(seconds(3)))
        self.assertEqual(len(fixes), 3)
        self.assertTrue(fixes[0]["valid"])
        self.assertEqual(fixes[0]["satellites"], 7)
        self.assertEqual(fixes[1]["utc"], START + timedelta(seconds=1))

    def test_satellites_in_view_while_waiting(self):
        gsv = (sentence("GPGSV,1,1,09,01,40,100,35,02,30,200,31,03,20,300,25,"
                        "04,10,50,22"), 0.0, 1.0)
        fixes = list(wildlife_gps.fixes_from([gsv] + one_second(0, valid=False,
                                                                satellites=0)))
        self.assertEqual(fixes[0]["in_view"], 9)
        self.assertEqual(fixes[0]["satellites"], 0)

    def test_in_view_unknown_before_the_first_gsv(self):
        fixes = list(wildlife_gps.fixes_from(seconds(1)))
        self.assertIsNone(fixes[0]["in_view"])

    def test_odd_field_with_good_checksum_is_skipped(self):
        lines = seconds(3)
        odd = sentence("GPGGA,160200.00,3720.00000,N,12142.00000,W,1,07x,1.2,"
                       "412.3,M,-30.1,M,,")
        lines[1] = (odd, lines[1][1], lines[1][2])
        fixes = list(wildlife_gps.fixes_from(lines))
        self.assertEqual(len(fixes), 2)         # second 0 skipped, no crash

    def test_garbled_line_is_dropped(self):
        lines = seconds(2)
        line, pi_time, uptime = lines[0]
        lines[0] = (line.replace("A,3720", "A,3730"), pi_time, uptime)
        fixes = list(wildlife_gps.fixes_from(lines))
        self.assertEqual(len(fixes), 1)        # second 0 lost its RMC


class TheTrustTest(unittest.TestCase):

    def trusted_after(self, lines):
        trust = wildlife_gps.TrustTest()
        for n, fix in enumerate(wildlife_gps.fixes_from(lines), start=1):
            if trust.check(fix):
                return n
        return None

    def test_ten_good_seconds(self):
        self.assertEqual(self.trusted_after(seconds(15)), 10)

    def test_a_bad_second_starts_again(self):
        lines = seconds(9)
        lines += one_second(9, valid=False)
        lines += [x for i in range(10, 25) for x in one_second(i)]
        self.assertEqual(self.trusted_after(lines), 20)

    def test_too_few_satellites(self):
        self.assertIsNone(self.trusted_after(seconds(30, satellites=3)))

    def test_year_before_2026(self):
        self.assertIsNone(self.trusted_after(seconds(30, year=2006)))


class OneAttachment(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.saved = (wildlife_gps.NETWORK_SYNCED, wildlife_gps.BOOT_ID)
        wildlife_gps.NETWORK_SYNCED = os.path.join(self.dir, "no-network")
        wildlife_gps.BOOT_ID = "testboot"

    def tearDown(self):
        wildlife_gps.NETWORK_SYNCED, wildlife_gps.BOOT_ID = self.saved
        self.tmp.cleanup()

    def run_with(self, lines):
        with redirect_stdout(io.StringIO()) as out:
            wildlife_gps.run("/dev/null-gps", self.dir, self.dir, "",
                             may_set_clock=False, lines=lines)
        return out.getvalue()

    def state(self):
        with open(os.path.join(self.dir, "gps.json")) as f:
            return json.load(f)

    def log_rows(self):
        rows = []
        for path in sorted(glob.glob(os.path.join(self.dir, "*", "gps-*.csv"))):
            with open(path) as f:
                rows += list(csv.DictReader(f))
        return rows

    def test_nothing_written_before_trust(self):
        self.run_with(seconds(9))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "gps.json")))
        self.assertEqual(self.log_rows(), [])

    def test_trusted_fix_writes_state_and_log(self):
        out = self.run_with(seconds(12))
        state = self.state()
        self.assertEqual(state["boot"], "testboot")
        self.assertAlmostEqual(state["position"]["lat"], 37.333333, places=5)
        self.assertEqual(len(state["attachments"]), 1)
        attachment = state["attachments"][0]
        self.assertAlmostEqual(attachment["offset_s"], 412337.6, places=1)
        self.assertFalse(attachment["stepped"])        # may_set_clock=False
        rows = self.log_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["attachment"], "1")
        self.assertIn("4.8 days slow", out)

    def test_second_attachment_keeps_first_position(self):
        self.run_with(seconds(12))
        # Four weeks later, 48.3 s fast, and 200 m north.
        later = seconds(12, lat="3720.10800", offset_s=-48.3,
                        uptime_start=2419200.0)
        out = self.run_with(later)
        state = self.state()
        self.assertEqual(len(state["attachments"]), 2)
        self.assertAlmostEqual(state["attachments"][1]["offset_s"], -48.3,
                               places=1)
        self.assertAlmostEqual(state["position"]["lat"], 37.333333, places=5)
        self.assertIn("was it moved?", out)
        self.assertEqual([r["attachment"] for r in self.log_rows()],
                         ["1", "2"])

    def test_earlier_boot_is_replaced_not_extended(self):
        self.run_with(seconds(12))
        wildlife_gps.BOOT_ID = "nextboot"
        self.run_with(seconds(12, uptime_start=30.0))
        state = self.state()
        self.assertEqual(state["boot"], "nextboot")
        self.assertEqual(len(state["attachments"]), 1)

    def test_unwritable_folders_do_not_stop_it(self):
        blocked = os.path.join(self.dir, "not-a-folder")
        open(blocked, "w").close()                 # a file where a folder goes
        with redirect_stdout(io.StringIO()) as out:
            result = wildlife_gps.run("/dev/null-gps", blocked, blocked, "",
                                      may_set_clock=False,
                                      lines=seconds(10 + 130))
        self.assertEqual(result, 0)
        self.assertIn("could not write", out.getvalue())
        self.assertEqual(out.getvalue().count("could not write the log"), 1)

    def test_wrong_shape_state_starts_fresh(self):
        with open(os.path.join(self.dir, "gps.json"), "w") as f:
            json.dump({"boot": "testboot", "attachments": "oops"}, f)
        self.run_with(seconds(12))
        self.assertEqual(len(self.state()["attachments"]), 1)

    def test_a_row_a_minute(self):
        self.run_with(seconds(10 + 130))
        self.assertEqual(len(self.log_rows()), 3)   # at trust, +60 s, +120 s


class SettingTheClock(unittest.TestCase):
    """time.clock_settime is swapped out: the test machine's clock is safe."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (wildlife_gps.NETWORK_SYNCED, wildlife_gps.TIMESYNC_CLOCK,
                      wildlife_gps.time.clock_settime)
        wildlife_gps.NETWORK_SYNCED = os.path.join(self.tmp.name, "synced")
        wildlife_gps.TIMESYNC_CLOCK = os.path.join(self.tmp.name, "clock")
        open(wildlife_gps.TIMESYNC_CLOCK, "w").close()
        os.utime(wildlife_gps.TIMESYNC_CLOCK, (0, 0))
        self.calls = []
        wildlife_gps.time.clock_settime = (
            lambda which, when: self.calls.append(when))

    def tearDown(self):
        (wildlife_gps.NETWORK_SYNCED, wildlife_gps.TIMESYNC_CLOCK,
         wildlife_gps.time.clock_settime) = self.saved
        self.tmp.cleanup()

    def a_fix(self, offset_s):
        utc = datetime.now(timezone.utc).replace(microsecond=0)
        return {"utc": utc, "pi_time": utc.timestamp() - offset_s}

    def test_measures_then_sets(self):
        offset, stepped = wildlife_gps.measure_and_set_clock(
            self.a_fix(-48.3), may_set=True)
        self.assertAlmostEqual(offset, -48.3, places=2)
        self.assertEqual(stepped, offset)
        self.assertEqual(len(self.calls), 1)
        self.assertAlmostEqual(self.calls[0],
                               datetime.now().timestamp() - 48.3, delta=1.0)
        self.assertGreater(os.path.getmtime(wildlife_gps.TIMESYNC_CLOCK), 0)

    def test_under_a_second_is_left_alone(self):
        _, stepped = wildlife_gps.measure_and_set_clock(self.a_fix(0.4), True)
        self.assertIsNone(stepped)
        self.assertEqual(self.calls, [])

    def test_network_has_the_clock(self):
        open(wildlife_gps.NETWORK_SYNCED, "w").close()
        with redirect_stdout(io.StringIO()) as out:
            offset, stepped = wildlife_gps.measure_and_set_clock(
                self.a_fix(5.0), True)
        self.assertAlmostEqual(offset, 5.0, places=2)
        self.assertIsNone(stepped)
        self.assertEqual(self.calls, [])
        self.assertIn("leaving it to the network", out.getvalue())


class Describing(unittest.TestCase):

    def test_slow_and_fast(self):
        self.assertEqual(wildlife_gps.describe(412337.6), "4.8 days slow")
        self.assertEqual(wildlife_gps.describe(-48.3), "48.3 s fast")
        self.assertEqual(wildlife_gps.describe(7200), "2.0 hours slow")


if __name__ == "__main__":
    unittest.main()
