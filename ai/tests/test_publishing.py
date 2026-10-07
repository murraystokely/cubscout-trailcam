"""Tests for the publishing side: what leaves this machine, and how.

Run with:  cd ai && python3 -m unittest discover tests

No network.  Everything that would talk to iNaturalist is a fake with the
same method names as `publish_inat.Api`.
"""

import contextlib
import io
import shutil
import sqlite3
import sys
import tempfile
import unittest

from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trailcam import bursts, deployments, manifest, publish_inat  # noqa: E402


HOME = (37.4000, -122.1000)          # made up: open hills, nobody's garden
PARK = (37.3390, -121.7120)          # Grant Park, 35 km away


def observation(identifier, lat, lon, geoprivacy=None, taxon="Mule Deer"):
    return {"id": identifier, "location": f"{lat},{lon}",
            "geoprivacy": geoprivacy, "observed_on": "2026-09-26",
            "taxon": {"name": taxon}}


class FakeApi:
    def __init__(self, observations):
        self.observations = observations
        self.changed = []

    def my_observations(self, user_id):
        return list(self.observations)

    def set_geoprivacy(self, observation_id, geoprivacy):
        self.changed.append((observation_id, geoprivacy))
        for row in self.observations:
            if row["id"] == observation_id:
                row["geoprivacy"] = geoprivacy


class Distances(unittest.TestCase):
    def test_home_to_park_is_tens_of_kilometres(self):
        metres = publish_inat.distance_m(*HOME, *PARK)
        self.assertGreater(metres, 30_000)
        self.assertLess(metres, 40_000)

    def test_a_point_is_zero_from_itself(self):
        self.assertEqual(publish_inat.distance_m(*HOME, *HOME), 0.0)


class ChoosingWhatToObscure(unittest.TestCase):
    def test_open_observations_near_home_are_chosen(self):
        rows = [observation(1, HOME[0] + 0.0005, HOME[1]),            # 55 m
                observation(2, HOME[0], HOME[1] - 0.004, "open"),     # 350 m
                observation(3, *PARK),                                # far
                observation(4, *HOME, geoprivacy="obscured")]         # done
        chosen = publish_inat.needs_obscuring(rows, [HOME], radius_m=1000)
        self.assertEqual([row["id"] for row in chosen], [1, 2])

    def test_no_location_is_skipped(self):
        rows = [{"id": 9, "location": None, "geoprivacy": None}]
        self.assertEqual(publish_inat.needs_obscuring(rows, [HOME]), [])


class FixingGeoprivacy(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi([observation(1, *HOME),
                            observation(2, *PARK),
                            observation(3, *HOME, geoprivacy="obscured")])
        self.sites = {"backyard": (HOME[0], HOME[1], 186.0)}
        self.said = io.StringIO()

    def say(self, text):
        self.said.write(text + "\n")

    def test_dry_run_changes_nothing_and_says_so(self):
        count = publish_inat.fix_geoprivacy(
            api=self.api, dry_run=True, private_sites=self.sites, out=self.say)
        self.assertEqual(count, 1)
        self.assertEqual(self.api.changed, [])
        self.assertIn("Dry run", self.said.getvalue())
        self.assertIn("  1  ", self.said.getvalue())

    def test_the_real_run_obscures_only_the_near_open_ones(self):
        count = publish_inat.fix_geoprivacy(
            api=self.api, private_sites=self.sites, out=self.say)
        self.assertEqual(count, 1)
        self.assertEqual(self.api.changed, [(1, "obscured")])

    def test_a_second_run_finds_nothing(self):
        publish_inat.fix_geoprivacy(api=self.api, private_sites=self.sites,
                                    out=self.say)
        again = publish_inat.fix_geoprivacy(
            api=self.api, private_sites=self.sites, out=self.say)
        self.assertEqual(again, 0)

    def test_no_private_sites_means_no_calls(self):
        count = publish_inat.fix_geoprivacy(
            api=self.api, private_sites={}, out=self.say)
        self.assertEqual(count, 0)
        self.assertIn("No private site coordinates", self.said.getvalue())


# ------------------------------------------------------------
# Schema version 5: places
# ------------------------------------------------------------

class MigratingToPlaces(unittest.TestCase):
    """A version-4 manifest kept boot and uptime only in the camera run's
    metrics JSON.  Opening it must grow the columns and copy them across."""

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.path = self.directory / "v4.sqlite"
        old = sqlite3.connect(self.path)
        old.executescript("""
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version VALUES (4);
            CREATE TABLE frames (
                id INTEGER PRIMARY KEY, camera TEXT NOT NULL,
                day TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
                captured_at TEXT NOT NULL, mean_luma REAL,
                kind TEXT NOT NULL DEFAULT 'training');
            INSERT INTO frames VALUES
                (1, 'wildlifecam9', '2026-09-25',
                 'wildlifecam9/2026-09-25/113449_532.jpg',
                 '2026-09-25T11:34:49.532036', 120.0, 'photo'),
                (2, 'wildlifecam4', '2026-08-24',
                 'wildlifecam4/2026-08-24/training/train_1.jpg',
                 '2026-08-24T10:34:15', 120.0, 'training');
            CREATE TABLE runs (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
                params TEXT, weights_md5 TEXT, code_version TEXT, host TEXT,
                started_at TEXT, finished_at TEXT, role TEXT, notes TEXT);
            INSERT INTO runs (id, kind, name) VALUES
                (1, 'camera', 'wildlifecam9'), (2, 'detector', 'MDV5A');
            CREATE TABLE frame_results (
                id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL,
                frame_id INTEGER NOT NULL, recorded_at TEXT,
                status TEXT NOT NULL DEFAULT 'ok', error TEXT,
                decision TEXT, largest_area INTEGER, n_animal INTEGER,
                n_person INTEGER, n_vehicle INTEGER, max_animal_conf REAL,
                max_person_conf REAL, metrics TEXT, UNIQUE (run_id, frame_id));
            INSERT INTO frame_results (run_id, frame_id, decision, metrics)
                VALUES (1, 1, 'strong motion',
                        '{"boot": "1c7e1a69", "extent": "0.5", "uptime_s": 56.7}'),
                       (2, 1, NULL, '{"boot": "not-a-camera-run"}');
            CREATE TABLE detections (
                id INTEGER PRIMARY KEY, frame_result_id INTEGER NOT NULL,
                category TEXT NOT NULL, confidence REAL NOT NULL,
                x REAL, y REAL, w REAL, h REAL, crop_path TEXT);
            CREATE TABLE annotations (
                id INTEGER PRIMARY KEY, frame_id INTEGER NOT NULL,
                label TEXT NOT NULL, who TEXT, labelled_at TEXT NOT NULL,
                notes TEXT);
        """)
        old.commit()
        old.close()

    def tearDown(self):
        shutil.rmtree(self.directory)

    def test_columns_arrive_and_are_back_filled_from_the_camera_run(self):
        with contextlib.redirect_stdout(io.StringIO()) as said:
            database = manifest.open_manifest(self.path)

        columns = {row[1] for row in
                   database.execute("PRAGMA table_info(frames)")}
        self.assertTrue({"boot", "uptime_s", "deployment_id"} <= columns)

        campout = database.execute(
            "SELECT boot, uptime_s, deployment_id FROM frames WHERE id = 1"
        ).fetchone()
        self.assertEqual(campout["boot"], "1c7e1a69")
        self.assertAlmostEqual(campout["uptime_s"], 56.7)
        self.assertIsNone(campout["deployment_id"])

        # The detector run's metrics are not the camera's record.
        backyard = database.execute(
            "SELECT boot, uptime_s FROM frames WHERE id = 2").fetchone()
        self.assertEqual(tuple(backyard), (None, None))

        self.assertEqual(database.execute(
            "SELECT version FROM schema_version").fetchone()[0], 5)
        self.assertIn("1 frames given their boot", said.getvalue())

        # The new tables and views are there, and the time view passes
        # an unplaced frame's clock through untouched.
        self.assertEqual(database.execute(
            "SELECT true_at FROM frame_times WHERE frame_id = 1"
        ).fetchone()[0], "2026-09-25T11:34:49.532036")
        self.assertEqual(database.execute(
            "SELECT COUNT(*) FROM observation_events").fetchone()[0], 0)
        database.close()

    def test_opening_twice_is_harmless(self):
        with contextlib.redirect_stdout(io.StringIO()):
            manifest.open_manifest(self.path).close()
            manifest.open_manifest(self.path).close()
        database = sqlite3.connect(self.path)
        self.assertEqual(database.execute(
            "SELECT boot FROM frames WHERE id = 1").fetchone()[0], "1c7e1a69")
        database.close()


class PlacesBase(unittest.TestCase):
    """A manifest with a campout camera, a synced backyard camera and
    the two CSVs that place them."""

    TRUE_START = "2026-09-25T19:15:00"

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.database = manifest.open_manifest(self.directory / "test.sqlite")

        self.sites_csv = self.directory / "sites.csv"
        self.sites_csv.write_text(
            "slug,name,description,aliases,latitude,longitude,"
            "uncertainty_m,geoprivacy\n"
            "backyard,The back garden,patio,home;backyard,,,,obscured\n"
            "grant-park-site2,Grant Park,oaks,grantpark,37.3390,-121.7120,"
            "300,open\n")
        self.private_csv = self.directory / "sites.private.csv"
        self.private_csv.write_text(
            "slug,latitude,longitude,uncertainty_m\n"
            "backyard,37.4000,-122.1000,186\n")
        self.deployments_csv = self.directory / "deployments.csv"
        self.deployments_csv.write_text(
            "camera,boot,site,code,clock_first,clock_last,photos,evidence,"
            "true_start,time_uncertainty_s,camera_model,rotation,baited,"
            "range_start,range_end\n"
            # The campout boot: clock_first is 42 s before the first frame
            # minus its uptime, as step8's start-up delay makes it.
            f"wildlifecam9,1c7e1a69,grant-park-site2,a7e0f27a4e31,"
            f"2026-09-25T11:34:35,2026-09-26T09:20:02,243,deer,"
            f"{self.TRUE_START},1800,Camera Module 3,0,0,,\n"
            # A backyard range on the same camera that would also contain
            # the campout frames' clock dates, were boots not tried first.
            "wildlifecam9,,backyard,,,,,,,0,Camera Module 3,0,0,"
            "2026-09-01,2026-09-30\n"
            "wildlifecam4,,backyard,,,,,,,0,AI Camera,0,1,"
            "2026-08-24,2026-09-07\n")

        self.campout = [
            self._frame("wildlifecam9", "2026-09-25", "113449_532.jpg",
                        datetime(2026, 9, 25, 11, 34, 49, 532036),
                        boot="1c7e1a69", uptime=56.7),
            self._frame("wildlifecam9", "2026-09-26", "022026_309.jpg",
                        datetime(2026, 9, 26, 2, 20, 26, 309000),
                        boot="1c7e1a69", uptime=53193.5),
        ]
        self.synced = self._frame("wildlifecam4", "2026-09-07",
                                  "training/train_120000_000.jpg",
                                  datetime(2026, 9, 7, 12, 0, 0))
        self.stray = self._frame("wildlifecam4", "2026-09-08",
                                 "training/train_120000_000.jpg",
                                 datetime(2026, 9, 8, 12, 0, 0))
        manifest.add_frames(self.database,
                            self.campout + [self.synced, self.stray])

    def tearDown(self):
        self.database.close()
        shutil.rmtree(self.directory)

    @staticmethod
    def _frame(camera, day, name, captured_at, boot=None, uptime=None):
        metrics = {"extent": "0.5"}
        if boot:
            metrics.update(boot=boot, uptime_s=uptime)
        return bursts.Frame(
            camera=camera, day=day, relative_path=f"{camera}/{day}/{name}",
            absolute_path=Path(name), captured_at=captured_at,
            camera_decision="strong motion", mean_luma=100.0,
            largest_area=500, code_version="abc", metrics=metrics,
            kind="photo")

    def _load(self, private=None):
        result = deployments.load(
            self.database, self.sites_csv, self.deployments_csv,
            private if private is not None else self.private_csv)
        placed, unplaced = deployments.place_frames(self.database)
        return result, placed, unplaced

    def _frame_id(self, frame):
        return self.database.execute(
            "SELECT id FROM frames WHERE path = ?",
            (frame.relative_path,)).fetchone()[0]


class Deployments(PlacesBase):

    def test_add_frames_fills_the_boot_columns(self):
        row = self.database.execute(
            "SELECT boot, uptime_s FROM frames WHERE path = ?",
            (self.campout[0].relative_path,)).fetchone()
        self.assertEqual(row["boot"], "1c7e1a69")
        self.assertAlmostEqual(row["uptime_s"], 56.7)

    def test_a_boot_row_beats_a_date_range(self):
        result, placed, unplaced = self._load()
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["deployments"], 3)

        where = {row["path"]: row["site"] for row in self.database.execute(
            "SELECT path, site FROM frame_times")}
        for frame in self.campout:
            self.assertEqual(where[frame.relative_path], "grant-park-site2")
        self.assertEqual(where[self.synced.relative_path], "backyard")

        self.assertEqual(placed, 3)
        self.assertEqual(unplaced, {("wildlifecam4", "2026-09-08"): 1})

    def test_the_offset_is_the_hand_value(self):
        self._load()
        offset = self.database.execute(
            "SELECT clock_offset_s FROM deployments WHERE boot = '1c7e1a69'"
        ).fetchone()[0]

        booted_on_clock = (datetime(2026, 9, 25, 11, 34, 49, 532036)
                           - timedelta(seconds=56.7))
        by_hand = (datetime.fromisoformat(self.TRUE_START)
                   - booted_on_clock).total_seconds()
        self.assertAlmostEqual(offset, by_hand, places=3)
        self.assertAlmostEqual(
            deployments.clock_offset_s(self.TRUE_START,
                                       "2026-09-25T11:34:49.532036", 56.7),
            by_hand, places=3)

    def test_true_at_adds_the_offset_and_leaves_a_synced_camera_alone(self):
        self._load()
        offset = self.database.execute(
            "SELECT clock_offset_s FROM deployments WHERE boot = '1c7e1a69'"
        ).fetchone()[0]

        deer = self.campout[1]
        true_at = self.database.execute(
            "SELECT true_at FROM frame_times WHERE frame_id = ?",
            (self._frame_id(deer),)).fetchone()[0]
        expected = deer.captured_at + timedelta(seconds=offset)
        self.assertEqual(true_at[:19], expected.isoformat()[:19])
        # The write-up's deer: 10:01 on the morning of the 26th.
        self.assertTrue(true_at.startswith("2026-09-26T10:01"))

        synced = self.database.execute(
            "SELECT true_at, captured_at, time_uncertainty_s, baited "
            "FROM frame_times WHERE frame_id = ?",
            (self._frame_id(self.synced),)).fetchone()
        self.assertEqual(synced["true_at"], synced["captured_at"])
        self.assertEqual(synced["time_uncertainty_s"], 0)
        self.assertEqual(synced["baited"], 1)

    def test_the_private_overlay_fills_the_blank_coordinate(self):
        self._load()
        site = self.database.execute(
            "SELECT latitude, longitude, uncertainty_m, geoprivacy "
            "FROM sites WHERE slug = 'backyard'").fetchone()
        self.assertAlmostEqual(site["latitude"], 37.4000)
        self.assertAlmostEqual(site["longitude"], -122.1000)
        self.assertEqual(site["uncertainty_m"], 186)
        self.assertEqual(site["geoprivacy"], "obscured")

        # And the public file's text is unchanged by the overlay.
        self.assertEqual(
            deployments.read_sites(self.sites_csv)["backyard"]["latitude"],
            None)

    def test_an_obscured_site_with_no_coordinate_is_a_warning(self):
        result, _, _ = self._load(private=self.directory / "missing.csv")
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("backyard is obscured but has no coordinate",
                      result["warnings"][0])
        self.assertIsNone(self.database.execute(
            "SELECT latitude FROM sites WHERE slug = 'backyard'"
        ).fetchone()[0])

    def test_a_clock_first_that_disagrees_with_the_frames_is_a_warning(self):
        text = self.deployments_csv.read_text().replace(
            "2026-09-25T11:34:35", "2026-09-25T11:00:00")
        self.deployments_csv.write_text(text)
        result, _, _ = self._load()
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("1c7e1a69: clock_first", result["warnings"][0])

    def test_loading_twice_updates_rather_than_duplicates(self):
        self._load()
        text = self.deployments_csv.read_text().replace(
            self.TRUE_START, "2026-09-25T19:16:00")
        self.deployments_csv.write_text(text)
        result, placed, _ = self._load()
        self.assertEqual(self.database.execute(
            "SELECT COUNT(*) FROM deployments").fetchone()[0], 3)
        self.assertEqual(self.database.execute(
            "SELECT true_start FROM deployments WHERE boot = '1c7e1a69'"
        ).fetchone()[0], "2026-09-25T19:16:00")
        self.assertEqual(placed, 3)

    def test_an_unknown_site_is_an_error(self):
        text = self.deployments_csv.read_text().replace(
            "grant-park-site2,a7e0", "grant-prak,a7e0")
        self.deployments_csv.write_text(text)
        with self.assertRaises(ValueError):
            self._load()


class ObservationTables(PlacesBase):

    def setUp(self):
        super().setUp()
        self._load()
        self.deployment = self.database.execute(
            "SELECT id FROM deployments WHERE boot = '1c7e1a69'"
        ).fetchone()[0]

    def test_an_observation_spans_its_frames_true_times(self):
        observation = manifest.add_observation(
            self.database, self.deployment, "propose", taxon_guess="deer")
        manifest.add_observation_frames(
            self.database, observation,
            [(self._frame_id(self.campout[1]), 1, None),
             (self._frame_id(self.campout[0]), 2, None)])

        event = self.database.execute(
            "SELECT * FROM observation_events WHERE observation_id = ?",
            (observation,)).fetchone()
        times = sorted(row[0] for row in self.database.execute(
            "SELECT true_at FROM frame_times WHERE deployment_id = ?",
            (self.deployment,)))
        self.assertEqual(event["event_start"], times[0])
        self.assertEqual(event["event_end"], times[-1])
        self.assertEqual(event["frames"], 2)
        self.assertEqual(event["time_uncertainty_s"], 1800)

        row = self.database.execute(
            "SELECT status, source, taxon_guess, curated_by FROM observations "
            "WHERE id = ?", (observation,)).fetchone()
        self.assertEqual(tuple(row), ("candidate", "propose", "deer", None))

    def test_re_attaching_a_frame_moves_it(self):
        observation = manifest.add_observation(
            self.database, self.deployment, "manual")
        frame = self._frame_id(self.campout[0])
        manifest.add_observation_frames(self.database, observation,
                                        [(frame, 1, None)])
        manifest.add_observation_frames(self.database, observation,
                                        [(frame, 3, None)])
        self.assertEqual([tuple(row) for row in self.database.execute(
            "SELECT position FROM observation_frames WHERE frame_id = ?",
            (frame,))], [(3,)])

    def test_approving_records_who_and_publishing_locks_it(self):
        observation = manifest.add_observation(
            self.database, self.deployment, "propose")
        manifest.set_observation_status(self.database, observation,
                                        "approved", "murray", notes="buck")
        row = self.database.execute(
            "SELECT status, curated_by, curated_at, notes FROM observations "
            "WHERE id = ?", (observation,)).fetchone()
        self.assertEqual(row["status"], "approved")
        self.assertEqual(row["curated_by"], "murray")
        self.assertIsNotNone(row["curated_at"])
        self.assertEqual(row["notes"], "buck")

        with self.assertRaises(ValueError):
            manifest.set_observation_status(self.database, observation,
                                            "maybe", "murray")

        publication = manifest.add_publication(
            self.database, observation, "inaturalist", 123456,
            remote_url="https://www.inaturalist.org/observations/123456")
        with self.assertRaises(ValueError):
            manifest.set_observation_status(self.database, observation,
                                            "rejected", "murray")

        manifest.complete_publication(self.database, publication, 2)
        row = self.database.execute(
            "SELECT state, photos_uploaded, remote_id, last_synced_at "
            "FROM publications WHERE id = ?", (publication,)).fetchone()
        self.assertEqual(row["state"], "complete")
        self.assertEqual(row["photos_uploaded"], 2)
        self.assertEqual(row["remote_id"], "123456")
        self.assertIsNotNone(row["last_synced_at"])

    def test_one_publication_per_destination(self):
        observation = manifest.add_observation(
            self.database, self.deployment, "backfill")
        manifest.add_publication(self.database, observation, "inaturalist",
                                 "1", state="complete")
        with self.assertRaises(sqlite3.IntegrityError):
            manifest.add_publication(self.database, observation,
                                     "inaturalist", "2")
        # A different destination is fine.
        manifest.add_publication(self.database, observation, "zenodo", "9")
        self.assertEqual(self.database.execute(
            "SELECT COUNT(*) FROM publications").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()


class RebuildingTheGallery(unittest.TestCase):
    """A rebuilt gallery holds exactly the current build's files.

    Ranks move between builds, and the file names carry the rank, so a
    rebuild that left old files behind let a reader pick the wrong
    animal by rank prefix (6 October 2026).
    """

    def test_stale_crops_are_removed(self):
        import shutil, tempfile
        from pathlib import Path
        from trailcam import shortlist
        directory = Path(tempfile.mkdtemp())
        try:
            images = directory / "images"
            images.mkdir(parents=True)
            stale = images / "001-wildlifecam4-999999-crop.jpg"
            stale.write_bytes(b"old")
            # An empty ranked list writes a page and no images, which is
            # enough to prove the directory was emptied first.
            shortlist.write_gallery([], [{"name": "MDV5A"}], directory, top=1)
            self.assertFalse(stale.exists())
            self.assertTrue((directory / "index.html").exists())
            self.assertEqual(list(images.iterdir()), [])
        finally:
            shutil.rmtree(directory)


class ShortlistBySite(unittest.TestCase):
    """`shortlist --site` keeps one place's frames and drops the rest."""

    def test_candidates_filter_by_deployment_site(self):
        import shutil, tempfile
        from datetime import datetime
        from pathlib import Path
        from trailcam import bursts, manifest, shortlist
        from trailcam.detector import Box
        directory = Path(tempfile.mkdtemp())
        try:
            db = manifest.open_manifest(directory / "t.sqlite")
            db.execute("INSERT INTO sites (slug, name) VALUES ('backyard', 'Garden'), ('park', 'Park')")
            db.execute("INSERT INTO deployments (camera, site, range_start, range_end, camera_model) "
                       "VALUES ('wildlifecam4', 'backyard', '2026-09-01', '2026-09-30', 'm'), "
                       "('wildlifecam9', 'park', '2026-09-01', '2026-09-30', 'm')")
            frames = []
            for camera in ("wildlifecam4", "wildlifecam9"):
                frames.append(bursts.Frame(
                    camera=camera, day="2026-09-10",
                    relative_path=f"{camera}/2026-09-10/100000.jpg",
                    absolute_path=Path("x"), captured_at=datetime(2026, 9, 10, 10),
                    camera_decision=None, mean_luma=100.0, largest_area=0,
                    code_version="c", metrics=None, kind="photo"))
            manifest.add_frames(db, frames)
            db.execute("UPDATE frames SET deployment_id = (SELECT id FROM deployments d WHERE d.camera = frames.camera)")
            run = manifest.start_run(db, "detector", "MDV5A")
            for row in db.execute("SELECT id FROM frames"):
                manifest.record_detections(db, run, row["id"], [Box("animal", 0.95, .1, .1, .2, .2, None)])
            both = shortlist.candidates(db, [run])
            garden = shortlist.candidates(db, [run], site="backyard")
            self.assertEqual(len(both), 2)
            self.assertEqual([e["camera"] for e in garden], ["wildlifecam4"])
            self.assertEqual(shortlist.looked_at(db, [run], site="backyard")["frames"], 1)
            db.close()
        finally:
            shutil.rmtree(directory)
