"""Tests for the parts that do not need a 280 MB model.

Run them with:

    cd ai && python3 -m unittest discover tests

Plain `unittest`, so they run on the system python with no venv and no
pytest.  What they cover is everything between the photo library and the
detector -- which is where the bugs that would quietly corrupt a number
live.  The detector itself is a third-party model; the useful test of it is
the check-the-checker sample in `report.py`, done by a person.

Four things here are worth the trouble in particular:

  * `classify_decision`, because it hard-codes strings from step8, and a
    typo would silently move frames into the wrong column of the headline
    result
  * the `truth` view, because the confidence split is the whole idea and it
    is expressed in SQL, which nothing else type-checks
  * run isolation, because the entire point of the schema is that one
    model's answers cannot touch another's
  * the migration, because it is the only code here that can destroy four
    hours of somebody's CPU time
"""

import contextlib
import io
import json
import shutil
import sqlite3
import tempfile
import types
import unittest

from datetime import datetime
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trailcam import bench, bursts, detector, manifest, report  # noqa: E402
from trailcam.detector import Box                          # noqa: E402


class DecisionVocabulary(unittest.TestCase):
    """step8's decision strings, sorted into what they meant."""

    def test_the_four_decisions_that_take_a_photograph(self):
        for decision in ("strong motion", "confirmed motion",
                         "shadow, but the AI sees an animal",
                         "small blob, the AI sees an animal"):
            self.assertEqual(bursts.classify_decision(decision), "wanted",
                             decision)

    def test_rejections(self):
        for decision in ("quiet", "too dark", "lighting change",
                         "scene change", "wrong shape", "shadow",
                         "waiting for confirmation"):
            self.assertEqual(bursts.classify_decision(decision), "rejected",
                             decision)

    def test_rate_limits_are_their_own_bucket(self):
        # The rules wanted it; housekeeping stopped it.  Counting these as
        # rejections would send us tuning thresholds that were already right.
        for suffix in ("(cooldown)", "(hourly limit)", "(disk full)",
                       "(dry run)"):
            self.assertEqual(
                bursts.classify_decision(f"confirmed motion {suffix}"),
                "suppressed", suffix)

    def test_a_suffix_on_a_rejection_is_still_a_rejection(self):
        self.assertEqual(bursts.classify_decision("quiet (cooldown)"),
                         "rejected")

    def test_an_unknown_decision_is_reported_not_guessed(self):
        self.assertEqual(bursts.classify_decision("brand new rule"),
                         "unknown")
        self.assertEqual(bursts.classify_decision(None), "unknown")


class FindingFrames(unittest.TestCase):
    """Walking a photo library that looks like the real one."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.day = self.root / "wildlifecam9" / "2026-08-24"
        training = self.day / "training"
        training.mkdir(parents=True)

        (training / "train_103415_876.jpg").write_bytes(b"jpeg")
        (training / "train_103420_112.jpg").write_bytes(b"jpeg")
        (training / "train_103415_876.png").write_bytes(b"png")
        (self.day / "103415.jpg").write_bytes(b"jpeg")
        (self.day / "103415_annotated.jpg").write_bytes(b"jpeg")

        # step8 stamps its own fingerprint into every photograph's JSON.
        (self.day / "103415.json").write_text(json.dumps(
            {"camera": "pi-in-the-oak", "code": "abc123def456"}))

        # The CSV is named for the camera's hostname, deliberately NOT the
        # directory name.
        (self.day / "measurements-pi-in-the-oak.csv").write_text(
            "time,file,mean_luma,largest_area,extent,aspect,decision\n"
            "2026-08-24T10:34:15.876,train_103415_876.jpg,128.7,4210,0.65,"
            "1.45,strong motion\n"
            "2026-08-24T10:34:20.112,train_103420_112.jpg,127.1,0,0.0,0.0,"
            "quiet\n")

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_finds_only_the_training_jpegs(self):
        frames = bursts.find_frames(photo_root=self.root)

        self.assertEqual([Path(f.relative_path).name for f in frames],
                         ["train_103415_876.jpg", "train_103420_112.jpg"])

    def test_reads_the_cameras_own_verdict(self):
        first, second = bursts.find_frames(photo_root=self.root)

        self.assertEqual(first.camera_decision, "strong motion")
        self.assertEqual(first.mean_luma, 128.7)
        self.assertEqual(first.largest_area, 4210)
        self.assertEqual(second.camera_decision, "quiet")

    def test_keeps_the_algorithms_other_measurements(self):
        # extent and aspect are step8's workings; E3 will sweep them, so
        # they must survive the trip into the manifest.
        first = bursts.find_frames(photo_root=self.root)[0]

        self.assertEqual(first.metrics["extent"], "0.65")
        self.assertEqual(first.metrics["aspect"], "1.45")

    def test_finds_which_step8_was_running(self):
        first = bursts.find_frames(photo_root=self.root)[0]

        self.assertEqual(first.code_version, "abc123def456")

    def test_a_day_with_no_sightings_has_no_fingerprint(self):
        (self.day / "103415.json").unlink()

        self.assertIsNone(bursts.read_code_version(self.day))
        # ... and that is not fatal; the frames are still worth detecting.
        self.assertEqual(len(bursts.find_frames(photo_root=self.root)), 2)

    def test_timestamp_keeps_the_milliseconds(self):
        first = bursts.find_frames(photo_root=self.root)[0]

        self.assertEqual(first.captured_at,
                         datetime(2026, 8, 24, 10, 34, 15, 876000))

    def test_camera_comes_from_the_directory_not_the_csv_name(self):
        self.assertEqual(bursts.find_frames(photo_root=self.root)[0].camera,
                         "wildlifecam9")

    def test_a_missing_photo_library_is_not_a_crash(self):
        self.assertEqual(bursts.find_frames(photo_root="/nonexistent"), [])


class ManifestBase(unittest.TestCase):

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.database = manifest.open_manifest(self.directory / "test.sqlite")

    def tearDown(self):
        self.database.close()
        shutil.rmtree(self.directory)

    def _frame(self, name, decision="quiet", luma=120.0, code="abc123",
               camera="wildlifecam9"):
        return bursts.Frame(
            camera=camera, day="2026-08-24",
            relative_path=f"{camera}/2026-08-24/training/{name}",
            absolute_path=Path(name),
            captured_at=datetime(2026, 8, 24, 10, 34, 15),
            camera_decision=decision, mean_luma=luma, largest_area=0,
            code_version=code, metrics={"extent": "0.65"})

    def _add(self, *frames):
        manifest.add_frames(self.database, list(frames))
        return [row["id"] for row in self.database.execute(
            "SELECT id FROM frames ORDER BY id")]

    def _verdict_on(self, path_ending):
        row = self.database.execute(
            "SELECT verdict FROM verdicts WHERE path LIKE ?",
            (f"%{path_ending}",)).fetchone()
        return row["verdict"]


class Frames(ManifestBase):
    """The file table, and re-scanning."""

    def test_rescanning_adds_nothing_and_loses_nothing(self):
        frames = [self._frame("train_1.jpg"), self._frame("train_2.jpg")]

        self.assertEqual(manifest.add_frames(self.database, frames), 2)
        self.assertEqual(manifest.add_frames(self.database, frames), 0)

    def test_rescanning_cannot_wipe_a_result(self):
        identifiers = self._add(self._frame("train_1.jpg"))
        run = manifest.start_run(self.database, "detector", "MDV5A")
        manifest.record_detections(self.database, run, identifiers[0], [])

        manifest.add_frames(self.database, [self._frame("train_1.jpg")])

        self.assertEqual(
            len(manifest.frames_to_detect(self.database, run)), 0)


class CameraRuns(ManifestBase):
    """One run per deployment of step8 -- the thing v1 could not express."""

    def test_one_run_per_camera_and_code_version(self):
        frames = [self._frame("train_1.jpg", code="aaa"),
                  self._frame("train_2.jpg", code="aaa"),
                  self._frame("train_3.jpg", code="bbb"),
                  self._frame("train_4.jpg", camera="wildlifecam7",
                              code="aaa")]
        self._add(*frames)
        manifest.record_camera_decisions(self.database, frames)

        runs = [r for r in manifest.runs(self.database) if r["kind"] == "camera"]

        self.assertEqual(len(runs), 3)
        self.assertEqual(sorted((r["name"], r["code_version"], r["frames"])
                                for r in runs),
                         [("wildlifecam7", "aaa", 1),
                          ("wildlifecam9", "aaa", 2),
                          ("wildlifecam9", "bbb", 1)])

    def test_recording_decisions_twice_is_a_no_op(self):
        frames = [self._frame("train_1.jpg")]
        self._add(*frames)

        self.assertEqual(
            manifest.record_camera_decisions(self.database, frames), 1)
        self.assertEqual(
            manifest.record_camera_decisions(self.database, frames), 0)

    def test_the_measurements_survive_as_json(self):
        frames = [self._frame("train_1.jpg")]
        self._add(*frames)
        manifest.record_camera_decisions(self.database, frames)

        metrics = self.database.execute(
            "SELECT metrics FROM frame_results").fetchone()[0]

        self.assertEqual(json.loads(metrics)["extent"], "0.65")

    def test_the_camera_decision_reaches_the_verdicts_view(self):
        frames = [self._frame("train_1.jpg", decision="strong motion")]
        self._add(*frames)
        manifest.record_camera_decisions(self.database, frames)

        row = self.database.execute("SELECT * FROM verdicts").fetchone()

        self.assertEqual(row["camera_decision"], "strong motion")
        self.assertEqual(row["camera_code"], "abc123")


class RunIsolation(ManifestBase):
    """Two models over the same frames must not touch each other."""

    def setUp(self):
        super().setUp()
        self.frame_ids = self._add(self._frame("train_1.jpg"),
                                   self._frame("train_2.jpg"))
        self.first = manifest.start_run(self.database, "detector", "MDV5A")
        self.second = manifest.start_run(self.database, "detector", "redwood")

    def test_each_run_has_its_own_queue(self):
        manifest.record_detections(self.database, self.first,
                                   self.frame_ids[0], [])

        self.assertEqual(
            len(manifest.frames_to_detect(self.database, self.first)), 1)
        self.assertEqual(
            len(manifest.frames_to_detect(self.database, self.second)), 2)

    def test_one_run_cannot_overwrite_another(self):
        manifest.record_detections(self.database, self.first,
                                   self.frame_ids[0],
                                   [Box("animal", 0.94, .1, .2, .3, .4, None)])
        manifest.record_detections(self.database, self.second,
                                   self.frame_ids[0], [])

        surviving = self.database.execute(
            "SELECT max_animal_conf FROM frame_results WHERE run_id = ?",
            (self.first,)).fetchone()[0]

        self.assertAlmostEqual(surviving, 0.94)

    def test_a_retry_within_one_run_replaces_its_own_boxes(self):
        for _ in range(2):
            manifest.record_detections(
                self.database, self.first, self.frame_ids[0],
                [Box("animal", 0.94, .1, .2, .3, .4, None)])

        self.assertEqual(self.database.execute(
            "SELECT COUNT(*) FROM detections").fetchone()[0], 1)

    def test_deleting_a_run_takes_its_results_and_boxes(self):
        manifest.record_detections(self.database, self.first,
                                   self.frame_ids[0],
                                   [Box("animal", 0.94, .1, .2, .3, .4, None)])

        self.database.execute("DELETE FROM runs WHERE id = ?", (self.first,))

        self.assertEqual(self.database.execute(
            "SELECT COUNT(*) FROM frame_results").fetchone()[0], 0)
        self.assertEqual(self.database.execute(
            "SELECT COUNT(*) FROM detections").fetchone()[0], 0)

    def test_an_unfinished_run_is_resumed_rather_than_duplicated(self):
        run_id, resumed = manifest.resume_or_start_run(
            self.database, "detector", "MDV5A", params={"a": 1})
        again, resumed_again = manifest.resume_or_start_run(
            self.database, "detector", "MDV5A", params={"a": 1})

        self.assertFalse(resumed)
        self.assertTrue(resumed_again)
        self.assertEqual(run_id, again)

    def test_a_finished_run_is_never_resumed(self):
        run_id, _ = manifest.resume_or_start_run(
            self.database, "detector", "MDV5A", params={"a": 1})
        manifest.finish_run(self.database, run_id)

        again, resumed = manifest.resume_or_start_run(
            self.database, "detector", "MDV5A", params={"a": 1})

        self.assertFalse(resumed)
        self.assertNotEqual(run_id, again)

    def test_different_parameters_are_a_different_run(self):
        run_id, _ = manifest.resume_or_start_run(
            self.database, "detector", "MDV5A", params={"threshold": 0.1})
        other, resumed = manifest.resume_or_start_run(
            self.database, "detector", "MDV5A", params={"threshold": 0.2})

        self.assertNotEqual(run_id, other)
        self.assertFalse(resumed)


class TheConfidenceSplit(ManifestBase):
    """The `verdicts` view, which is the whole idea, expressed in SQL."""

    def setUp(self):
        super().setUp()
        self.frame_ids = self._add(self._frame("train_1.jpg"))
        self.run = manifest.start_run(self.database, "detector", "MDV5A")
        manifest.set_reference(self.database, self.run)

    def _record(self, *boxes, error=None):
        manifest.record_detections(self.database, self.run, self.frame_ids[0],
                                   list(boxes), error=error)

    def test_a_frame_no_run_has_seen(self):
        self.assertEqual(self._verdict_on("train_1.jpg"), "not yet seen")

    def test_a_confident_animal(self):
        self._record(Box("animal", 0.94, .1, .2, .3, .4, None))
        self.assertEqual(self._verdict_on("train_1.jpg"), "animal")

    def test_no_boxes_at_all_is_empty(self):
        self._record()
        self.assertEqual(self._verdict_on("train_1.jpg"), "empty")

    def test_the_muddy_middle(self):
        self._record(Box("animal", 0.42, .1, .2, .3, .4, None))
        self.assertEqual(self._verdict_on("train_1.jpg"), "uncertain")

    def test_a_person_is_neither_animal_nor_empty(self):
        self._record(Box("person", 0.91, .1, .2, .3, .4, None))
        self.assertEqual(self._verdict_on("train_1.jpg"), "person")

    def test_unreadable_says_so_rather_than_reading_as_empty(self):
        self._record(error=OSError("truncated"))

        self.assertEqual(self._verdict_on("train_1.jpg"), "unreadable")
        # ... and it is out of the queue, so one bad file cannot loop.
        self.assertEqual(manifest.frames_to_detect(self.database, self.run), [])

    def test_a_retry_can_pick_up_the_unreadable_ones(self):
        self._record(error=OSError("truncated"))

        self.assertEqual(len(manifest.frames_to_detect(
            self.database, self.run, retry_errors=True)), 1)

    def test_switching_the_reference_changes_the_answer(self):
        self._record(Box("animal", 0.94, .1, .2, .3, .4, None))
        self.assertEqual(self._verdict_on("train_1.jpg"), "animal")

        # A second opinion, and no recompute needed to adopt it.
        other = manifest.start_run(self.database, "detector", "redwood")
        manifest.record_detections(self.database, other, self.frame_ids[0], [])
        manifest.set_reference(self.database, other)

        self.assertEqual(self._verdict_on("train_1.jpg"), "empty")

    def test_only_one_run_can_be_the_reference(self):
        other = manifest.start_run(self.database, "detector", "redwood")
        manifest.set_reference(self.database, other)

        self.assertEqual(self.database.execute(
            "SELECT COUNT(*) FROM runs WHERE role = 'reference'"
        ).fetchone()[0], 1)

    def test_two_references_cannot_be_forced_in(self):
        other = manifest.start_run(self.database, "detector", "redwood")

        with self.assertRaises(sqlite3.IntegrityError):
            self.database.execute(
                "UPDATE runs SET role = 'reference' WHERE id = ?", (other,))


class HumanAnnotations(ManifestBase):
    """What a person saw, which no rerun may touch."""

    def setUp(self):
        super().setUp()
        self.frame_ids = self._add(self._frame("train_1.jpg"))
        self.run = manifest.start_run(self.database, "detector", "MDV5A")
        manifest.set_reference(self.database, self.run)
        manifest.record_detections(self.database, self.run, self.frame_ids[0],
                                   [Box("animal", 0.99, .1, .2, .3, .4, None)])

    def test_an_annotation_beats_the_detector(self):
        manifest.add_annotation(self.database, self.frame_ids[0], "empty",
                           who="nolan")

        self.assertEqual(self._verdict_on("train_1.jpg"), "empty")

    def test_rerunning_the_detector_cannot_erase_it(self):
        manifest.add_annotation(self.database, self.frame_ids[0], "empty")
        manifest.record_detections(self.database, self.run, self.frame_ids[0],
                                   [Box("animal", 0.99, .1, .2, .3, .4, None)])

        self.assertEqual(self._verdict_on("train_1.jpg"), "empty")

    def test_two_people_may_disagree_and_the_later_one_counts(self):
        manifest.add_annotation(self.database, self.frame_ids[0], "empty",
                           who="nolan")
        manifest.add_annotation(self.database, self.frame_ids[0], "animal",
                           who="murray")

        self.assertEqual(self._verdict_on("train_1.jpg"), "animal")
        # Both are kept: disagreement is data.
        self.assertEqual(self.database.execute(
            "SELECT COUNT(*) FROM annotations").fetchone()[0], 2)


class Migration(unittest.TestCase):
    """Version 1 held hours of CPU time.  This is the code that could lose it."""

    V1_SCHEMA = """
        CREATE TABLE frames (
            id INTEGER PRIMARY KEY, camera TEXT, day TEXT, path TEXT UNIQUE,
            captured_at TEXT, camera_decision TEXT, mean_luma REAL,
            largest_area INTEGER, detected_at TEXT, detector TEXT,
            n_animal INTEGER, n_person INTEGER, n_vehicle INTEGER,
            max_animal_conf REAL, max_person_conf REAL, detect_error TEXT,
            hand_label TEXT, hand_labelled_at TEXT);
        CREATE TABLE detections (
            id INTEGER PRIMARY KEY, frame_id INTEGER, category TEXT,
            confidence REAL, x REAL, y REAL, w REAL, h REAL, crop_path TEXT);
        CREATE VIEW truth AS SELECT f.*, 'animal' AS label FROM frames f;
    """

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.path = self.directory / "v1.sqlite"

        old = sqlite3.connect(self.path)
        old.executescript(self.V1_SCHEMA)
        old.execute(
            """INSERT INTO frames (id, camera, day, path, captured_at,
                   camera_decision, mean_luma, detected_at, detector,
                   n_animal, max_animal_conf, hand_label, hand_labelled_at)
               VALUES (1, 'wildlifecam4', '2026-08-24', 'a/b/train_1.jpg',
                   '2026-08-24T10:34:15', 'strong motion', 128.7,
                   '2026-09-12 10:00:00', 'MDV5A', 1, 0.94, 'animal',
                   '2026-09-12 11:00:00')""")
        old.execute(
            """INSERT INTO frames (id, camera, day, path, captured_at,
                   detected_at, detector, detect_error)
               VALUES (2, 'wildlifecam4', '2026-08-24', 'a/b/train_2.jpg',
                   '2026-08-24T10:34:20', '2026-09-12 10:00:01', 'MDV5A',
                   'truncated')""")
        old.execute("INSERT INTO detections VALUES "
                    "(1, 1, 'animal', 0.94, 0.1, 0.2, 0.3, 0.4, NULL)")
        old.commit()
        old.close()

        # The migration narrates what it is doing, which is right at a
        # terminal and noise in a test run.
        with contextlib.redirect_stdout(io.StringIO()):
            self.database = manifest.open_manifest(self.path)

    def tearDown(self):
        self.database.close()
        shutil.rmtree(self.directory)

    def test_the_expensive_part_survives(self):
        row = self.database.execute(
            """SELECT r.name, fr.max_animal_conf, fr.n_animal
                 FROM frame_results fr JOIN runs r ON r.id = fr.run_id
                WHERE fr.frame_id = 1""").fetchone()

        self.assertEqual(row["name"], "MDV5A")
        self.assertAlmostEqual(row["max_animal_conf"], 0.94)

    def test_the_boxes_follow_their_result(self):
        row = self.database.execute(
            """SELECT d.confidence FROM detections d
                 JOIN frame_results fr ON fr.id = d.frame_result_id
                WHERE fr.frame_id = 1""").fetchone()

        self.assertAlmostEqual(row["confidence"], 0.94)

    def test_an_unreadable_frame_stays_unreadable(self):
        row = self.database.execute(
            "SELECT status, error FROM frame_results WHERE frame_id = 2"
        ).fetchone()

        self.assertEqual(row["status"], "error")
        self.assertEqual(row["error"], "truncated")

    def test_hand_labels_become_rows_in_their_own_table(self):
        row = self.database.execute("SELECT * FROM annotations").fetchone()

        self.assertEqual(row["frame_id"], 1)
        self.assertEqual(row["label"], "animal")

    def test_the_recovered_run_becomes_the_reference(self):
        self.assertIsNotNone(manifest.reference_run(self.database))

    def test_the_model_columns_are_gone_from_frames(self):
        columns = {row[1] for row in
                   self.database.execute("PRAGMA table_info(frames)")}

        self.assertNotIn("detector", columns)
        self.assertNotIn("camera_decision", columns)
        self.assertIn("mean_luma", columns)

    def test_migrating_twice_is_harmless(self):
        self.database.close()
        with contextlib.redirect_stdout(io.StringIO()):
            self.database = manifest.open_manifest(self.path)

        self.assertEqual(self.database.execute(
            "SELECT COUNT(*) FROM frame_results").fetchone()[0], 2)


class RenameMigration(unittest.TestCase):
    """Version 2 called them `labels` and `truth`.  Both names were wrong."""

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.path = self.directory / "v2.sqlite"

        old = sqlite3.connect(self.path)
        old.executescript("""
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version VALUES (2);
            CREATE TABLE frames (
                id INTEGER PRIMARY KEY, camera TEXT, day TEXT,
                path TEXT UNIQUE, captured_at TEXT, mean_luma REAL);
            CREATE TABLE runs (
                id INTEGER PRIMARY KEY, kind TEXT, name TEXT, params TEXT,
                weights_md5 TEXT, code_version TEXT, host TEXT,
                started_at TEXT, finished_at TEXT, role TEXT, notes TEXT);
            CREATE TABLE frame_results (
                id INTEGER PRIMARY KEY, run_id INTEGER, frame_id INTEGER,
                recorded_at TEXT, status TEXT DEFAULT 'ok', error TEXT,
                decision TEXT, largest_area INTEGER, n_animal INTEGER,
                n_person INTEGER, n_vehicle INTEGER, max_animal_conf REAL,
                max_person_conf REAL, metrics TEXT);
            CREATE TABLE detections (
                id INTEGER PRIMARY KEY, frame_result_id INTEGER,
                category TEXT, confidence REAL, x REAL, y REAL, w REAL,
                h REAL, crop_path TEXT);
            CREATE TABLE labels (
                id INTEGER PRIMARY KEY, frame_id INTEGER NOT NULL,
                label TEXT NOT NULL, who TEXT, labelled_at TEXT NOT NULL,
                notes TEXT);
            CREATE VIEW latest_label AS SELECT * FROM labels;
            CREATE VIEW truth AS SELECT f.*, 'empty' AS label FROM frames f;
            INSERT INTO frames VALUES
                (1, 'wildlifecam4', '2026-08-24', 'a/train_1.jpg',
                 '2026-08-24T10:34:15', 128.7);
            INSERT INTO labels VALUES
                (1, 1, 'animal', 'nolan', '2026-09-12 11:00:00', NULL);
        """)
        old.commit()
        old.close()

        with contextlib.redirect_stdout(io.StringIO()):
            self.database = manifest.open_manifest(self.path)

    def tearDown(self):
        self.database.close()
        shutil.rmtree(self.directory)

    def test_a_persons_work_survives_the_rename(self):
        row = self.database.execute("SELECT * FROM annotations").fetchone()

        self.assertEqual(row["label"], "animal")
        self.assertEqual(row["who"], "nolan")

    def test_the_old_names_are_gone(self):
        names = {row[0] for row in self.database.execute(
            "SELECT name FROM sqlite_master")}

        self.assertNotIn("labels", names)
        self.assertNotIn("truth", names)
        self.assertIn("annotations", names)
        self.assertIn("verdicts", names)

    def test_the_annotation_still_wins_afterwards(self):
        row = self.database.execute(
            "SELECT verdict, verdict_source FROM verdicts").fetchone()

        self.assertEqual(row["verdict"], "animal")
        self.assertEqual(row["verdict_source"], "human")


class Export(unittest.TestCase):
    """The MegaDetector-format JSON that Timelapse reads."""

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.database = manifest.open_manifest(self.directory / "test.sqlite")

        manifest.add_frames(self.database, [bursts.Frame(
            camera="wildlifecam9", day="2026-08-24",
            relative_path="wildlifecam9/2026-08-24/training/train_1.jpg",
            absolute_path=Path("train_1.jpg"),
            captured_at=datetime(2026, 8, 24, 10, 34, 15),
            camera_decision="strong motion", mean_luma=120.0,
            largest_area=4210, code_version="abc123", metrics=None)])

        self.run = manifest.start_run(self.database, "detector", "MDV5A")
        manifest.set_reference(self.database, self.run)
        frame_id = self.database.execute(
            "SELECT id FROM frames").fetchone()["id"]
        manifest.record_detections(self.database, self.run, frame_id, [
            Box("animal", 0.94, 0.1, 0.2, 0.3, 0.4, None),
            Box("person", 0.31, 0.5, 0.5, 0.1, 0.1, None)])

    def tearDown(self):
        self.database.close()
        shutil.rmtree(self.directory)

    def test_writes_the_documented_shape(self):
        destination = self.directory / "md.json"
        with contextlib.redirect_stdout(io.StringIO()):
            report.export_megadetector_json(self.database, destination)

        written = json.loads(destination.read_text())

        self.assertEqual(written["detection_categories"],
                         {"1": "animal", "2": "person", "3": "vehicle"})
        self.assertEqual(len(written["images"]), 1)

        image = written["images"][0]
        self.assertEqual(image["file"],
                         "wildlifecam9/2026-08-24/training/train_1.jpg")
        self.assertEqual([d["category"] for d in image["detections"]],
                         ["1", "2"])
        self.assertEqual(image["detections"][0]["bbox"], [0.1, 0.2, 0.3, 0.4])
        # max_detection_conf is over every category, not just animals.
        self.assertEqual(image["max_detection_conf"], 0.94)

    def test_it_names_the_run_it_exported(self):
        destination = self.directory / "md.json"
        with contextlib.redirect_stdout(io.StringIO()):
            report.export_megadetector_json(self.database, destination)

        self.assertEqual(
            json.loads(destination.read_text())["info"]["detector"], "MDV5A")


class ComparingTwoMachines(unittest.TestCase):
    """`bench report` says whether two machines reached the same verdicts.

    The first version only asked about animals, and so reported "no
    verdict changed" between the ThinkPad and the Mac while a person
    scored 0.802 on one and 0.799 on the other.
    """

    def compare(self, first, second):
        return bench._compare_findings({"f.jpg": first}, {"f.jpg": second})

    def test_identical_findings_differ_nowhere(self):
        boxes = [{"category": "1", "conf": 0.85}]
        result = self.compare(boxes, boxes)
        self.assertEqual(result["frames_differing"], 0)
        self.assertEqual(result["verdicts_changed"], 0)

    def test_a_small_animal_difference_is_not_a_changed_verdict(self):
        result = self.compare([{"category": "1", "conf": 0.85}],
                              [{"category": "1", "conf": 0.86}])
        self.assertEqual(result["frames_differing"], 1)
        self.assertAlmostEqual(result["max_delta"], 0.01)
        self.assertEqual(result["verdicts_changed"], 0)

    def test_an_animal_crossing_the_line_is(self):
        result = self.compare([{"category": "1", "conf": 0.801}],
                              [{"category": "1", "conf": 0.799}])
        self.assertEqual(result["verdicts_changed"], 1)

    def test_a_person_crossing_the_line_counts_too(self):
        # The ThinkPad-against-Mac case, verbatim.
        result = self.compare([{"category": "2", "conf": 0.802}],
                              [{"category": "2", "conf": 0.799}])
        self.assertEqual(result["frames_differing"], 0)    # no animal box
        self.assertEqual(result["verdicts_changed"], 1)


class LoadingNewerCheckpoints(unittest.TestCase):
    """The MDv6 checkpoints name a class the bundled YOLOv5 fork lacks.

    The package patches that itself, but only when the error message says
    "Can't get attribute", and Python 3.14 says "has no attribute" instead.
    These pin down our own retry so that a future Python cannot silently
    take redwood away again.
    """

    def setUp(self):
        # A stand-in for the YOLOv5 fork's `models.yolo`, which only exists
        # on sys.path once the real loader has run.  Ours is there from the
        # start; what matters is that it has `Model` and no `DetectionModel`.
        self.yolo = types.ModuleType("models.yolo")
        self.yolo.Model = object
        self.models = types.ModuleType("models")
        self.models.yolo = self.yolo
        sys.modules["models"] = self.models
        sys.modules["models.yolo"] = self.yolo

    def tearDown(self):
        sys.modules.pop("models", None)
        sys.modules.pop("models.yolo", None)

    def test_the_python_3_14_wording_triggers_the_alias_and_a_retry(self):
        attempts = []

        def loader(weights, detector_options=None):
            attempts.append(weights)
            if len(attempts) == 1:
                raise AttributeError(
                    "module 'models.yolo' has no attribute 'DetectionModel'")
            return "loaded"

        loaded = detector._load_with_newer_yolo_names(
            loader, "redwood.pt", None)

        self.assertEqual(loaded, "loaded")
        self.assertEqual(len(attempts), 2)
        self.assertIs(self.yolo.DetectionModel, self.yolo.Model)

    def test_the_older_wording_is_handled_the_same_way(self):
        calls = [0]

        def loader(weights, detector_options=None):
            calls[0] += 1
            if calls[0] == 1:
                raise AttributeError(
                    "Can't get attribute 'DetectionModel' on <module "
                    "'models.yolo'>")
            return "loaded"

        self.assertEqual(
            detector._load_with_newer_yolo_names(loader, "w.pt", None),
            "loaded")

    def test_a_checkpoint_that_loads_first_time_is_not_touched(self):
        def loader(weights, detector_options=None):
            return "loaded"

        self.assertEqual(
            detector._load_with_newer_yolo_names(loader, "v5a.pt", None),
            "loaded")
        self.assertFalse(hasattr(self.yolo, "DetectionModel"))

    def test_any_other_attribute_error_is_not_ours_to_swallow(self):
        def loader(weights, detector_options=None):
            raise AttributeError("module 'torch' has no attribute 'frobnicate'")

        with self.assertRaises(AttributeError):
            detector._load_with_newer_yolo_names(loader, "w.pt", None)


if __name__ == "__main__":
    unittest.main()
