"""Tests for the parts that do not need a 280 MB model.

Run them with:

    cd ai && python3 -m unittest discover tests

Plain `unittest`, so they run on the system python with no venv and no
pytest.  What they cover is everything between the photo library and the
detector -- which is where the bugs that would quietly corrupt a number
live.  The detector itself is a third-party model; the useful test of it is
the check-the-checker sample in `report.py`, done by a person.

Two things here are worth the trouble in particular:

  * `classify_decision`, because it hard-codes strings from step8, and a
    typo would silently move frames into the wrong column of the headline
    result
  * the `truth` view, because the confidence split is the whole idea and it
    is expressed in SQL, which nothing else type-checks
"""

import json
import shutil
import sqlite3
import tempfile
import unittest

from datetime import datetime
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trailcam import bursts, config, manifest, report      # noqa: E402
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
        # step8 only appends these after deciding to save, so this should
        # not arise -- but if it ever does, "quiet" must not become a save.
        self.assertEqual(bursts.classify_decision("quiet (cooldown)"),
                         "rejected")

    def test_an_unknown_decision_is_reported_not_guessed(self):
        self.assertEqual(bursts.classify_decision("brand new rule"),
                         "unknown")
        self.assertEqual(bursts.classify_decision(None), "unknown")
        self.assertEqual(bursts.classify_decision(""), "unknown")


class FindingFrames(unittest.TestCase):
    """Walking a photo library that looks like the real one."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        day = self.root / "wildlifecam9" / "2026-08-24"
        training = day / "training"
        training.mkdir(parents=True)

        # Two training frames, one lores buffer, one wildlife photograph
        # with its annotated twin.  Only the first two should be found.
        (training / "train_103415_876.jpg").write_bytes(b"jpeg")
        (training / "train_103420_112.jpg").write_bytes(b"jpeg")
        (training / "train_103415_876.png").write_bytes(b"png")
        (day / "103415.jpg").write_bytes(b"jpeg")
        (day / "103415_annotated.jpg").write_bytes(b"jpeg")

        # The CSV is named for the camera's hostname, which here is
        # deliberately NOT the directory name.
        (day / "measurements-pi-in-the-oak.csv").write_text(
            "time,file,mean_luma,largest_area,decision\n"
            "2026-08-24T10:34:15.876,train_103415_876.jpg,128.7,4210,"
            "strong motion\n"
            "2026-08-24T10:34:20.112,train_103420_112.jpg,127.1,0,quiet\n"
        )

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

    def test_timestamp_keeps_the_milliseconds(self):
        first = bursts.find_frames(photo_root=self.root)[0]

        self.assertEqual(first.captured_at,
                         datetime(2026, 8, 24, 10, 34, 15, 876000))

    def test_camera_comes_from_the_directory_not_the_csv_name(self):
        first = bursts.find_frames(photo_root=self.root)[0]

        self.assertEqual(first.camera, "wildlifecam9")

    def test_a_missing_photo_library_is_not_a_crash(self):
        self.assertEqual(bursts.find_frames(photo_root="/nonexistent"), [])

    def test_a_missing_csv_leaves_the_verdict_empty(self):
        for csv_file in (self.root / "wildlifecam9" / "2026-08-24").glob(
                "measurements-*.csv"):
            csv_file.unlink()

        frames = bursts.find_frames(photo_root=self.root)

        self.assertEqual(len(frames), 2)
        self.assertIsNone(frames[0].camera_decision)


class Manifest(unittest.TestCase):
    """The database: idempotency, and the confidence split."""

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.database = manifest.open_manifest(self.directory / "test.sqlite")

    def tearDown(self):
        self.database.close()
        shutil.rmtree(self.directory)

    def _frame(self, name, decision="quiet", luma=120.0):
        return bursts.Frame(
            camera="wildlifecam9", day="2026-08-24",
            relative_path=f"wildlifecam9/2026-08-24/training/{name}",
            absolute_path=Path(name),
            captured_at=datetime(2026, 8, 24, 10, 34, 15),
            camera_decision=decision, mean_luma=luma, largest_area=0)

    def _label_of(self, path_ending):
        row = self.database.execute(
            "SELECT label FROM truth WHERE path LIKE ?",
            (f"%{path_ending}",)).fetchone()
        return row["label"]

    def test_rescanning_adds_nothing_and_loses_nothing(self):
        frames = [self._frame("train_1.jpg"), self._frame("train_2.jpg")]

        self.assertEqual(manifest.add_frames(self.database, frames), 2)
        self.assertEqual(manifest.add_frames(self.database, frames), 0)

        # And a rescan must not wipe a result already recorded.
        identifier = manifest.frames_to_detect(self.database)[0]["id"]
        manifest.record_detections(self.database, identifier, "MDV5A", [])
        manifest.add_frames(self.database, frames)

        self.assertEqual(len(manifest.frames_to_detect(self.database)), 1)

    def test_the_work_queue_is_what_has_not_been_seen(self):
        manifest.add_frames(self.database,
                            [self._frame("train_1.jpg"),
                             self._frame("train_2.jpg")])

        first = manifest.frames_to_detect(self.database)[0]
        manifest.record_detections(self.database, first["id"], "MDV5A", [])

        self.assertEqual(len(manifest.frames_to_detect(self.database)), 1)
        self.assertEqual(
            len(manifest.frames_to_detect(self.database, redo=True)), 2)

    def test_a_confident_animal_is_labelled_animal(self):
        manifest.add_frames(self.database, [self._frame("train_1.jpg")])
        identifier = manifest.frames_to_detect(self.database)[0]["id"]

        manifest.record_detections(self.database, identifier, "MDV5A", [
            Box("animal", 0.94, 0.1, 0.2, 0.3, 0.4, None)])

        self.assertEqual(self._label_of("train_1.jpg"), "animal")

    def test_no_boxes_at_all_is_an_empty_frame(self):
        manifest.add_frames(self.database, [self._frame("train_1.jpg")])
        identifier = manifest.frames_to_detect(self.database)[0]["id"]

        manifest.record_detections(self.database, identifier, "MDV5A", [])

        self.assertEqual(self._label_of("train_1.jpg"), "empty")

    def test_the_muddy_middle_is_uncertain(self):
        manifest.add_frames(self.database, [self._frame("train_1.jpg")])
        identifier = manifest.frames_to_detect(self.database)[0]["id"]

        manifest.record_detections(self.database, identifier, "MDV5A", [
            Box("animal", 0.42, 0.1, 0.2, 0.3, 0.4, None)])

        self.assertEqual(self._label_of("train_1.jpg"), "uncertain")

    def test_a_person_is_neither_animal_nor_empty(self):
        manifest.add_frames(self.database, [self._frame("train_1.jpg")])
        identifier = manifest.frames_to_detect(self.database)[0]["id"]

        manifest.record_detections(self.database, identifier, "MDV5A", [
            Box("person", 0.91, 0.1, 0.2, 0.3, 0.4, None)])

        self.assertEqual(self._label_of("train_1.jpg"), "person")

    def test_an_unreadable_frame_says_so_rather_than_reading_as_empty(self):
        manifest.add_frames(self.database, [self._frame("train_1.jpg")])
        identifier = manifest.frames_to_detect(self.database)[0]["id"]

        manifest.record_detections(self.database, identifier, "MDV5A", [],
                                   error=OSError("truncated"))

        self.assertEqual(self._label_of("train_1.jpg"), "unreadable")
        # ... and it is out of the queue, so one bad file cannot loop.
        self.assertEqual(manifest.frames_to_detect(self.database), [])

    def test_a_hand_label_beats_the_detector(self):
        manifest.add_frames(self.database, [self._frame("train_1.jpg")])
        identifier = manifest.frames_to_detect(self.database)[0]["id"]

        manifest.record_detections(self.database, identifier, "MDV5A", [
            Box("animal", 0.99, 0.1, 0.2, 0.3, 0.4, None)])
        self.database.execute(
            "UPDATE frames SET hand_label = 'empty' WHERE id = ?",
            (identifier,))

        self.assertEqual(self._label_of("train_1.jpg"), "empty")

    def test_rerunning_the_detector_replaces_boxes_rather_than_adding(self):
        manifest.add_frames(self.database, [self._frame("train_1.jpg")])
        identifier = manifest.frames_to_detect(self.database)[0]["id"]

        for _ in range(2):
            manifest.record_detections(self.database, identifier, "MDV5A", [
                Box("animal", 0.94, 0.1, 0.2, 0.3, 0.4, None)])

        count = self.database.execute(
            "SELECT COUNT(*) FROM detections").fetchone()[0]
        self.assertEqual(count, 1)

    def test_deleting_a_frame_takes_its_boxes_with_it(self):
        manifest.add_frames(self.database, [self._frame("train_1.jpg")])
        identifier = manifest.frames_to_detect(self.database)[0]["id"]
        manifest.record_detections(self.database, identifier, "MDV5A", [
            Box("animal", 0.94, 0.1, 0.2, 0.3, 0.4, None)])

        self.database.execute("DELETE FROM frames WHERE id = ?",
                              (identifier,))

        count = self.database.execute(
            "SELECT COUNT(*) FROM detections").fetchone()[0]
        self.assertEqual(count, 0)


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
            largest_area=4210)])

        identifier = manifest.frames_to_detect(self.database)[0]["id"]
        manifest.record_detections(self.database, identifier, "MDV5A", [
            Box("animal", 0.94, 0.1, 0.2, 0.3, 0.4, None),
            Box("person", 0.31, 0.5, 0.5, 0.1, 0.1, None)])

    def tearDown(self):
        self.database.close()
        shutil.rmtree(self.directory)

    def test_writes_the_documented_shape(self):
        destination = self.directory / "md.json"
        report.export_megadetector_json(self.database, destination)

        written = json.loads(destination.read_text())

        self.assertEqual(written["detection_categories"],
                         {"1": "animal", "2": "person", "3": "vehicle"})
        self.assertEqual(len(written["images"]), 1)

        image = written["images"][0]
        self.assertEqual(image["file"],
                         "wildlifecam9/2026-08-24/training/train_1.jpg")
        # Categories go back to MegaDetector's numbers, and the boxes stay
        # in its coordinate convention -- the export is a straight copy.
        self.assertEqual([d["category"] for d in image["detections"]],
                         ["1", "2"])
        self.assertEqual(image["detections"][0]["bbox"], [0.1, 0.2, 0.3, 0.4])
        # max_detection_conf is over every category, not just animals.
        self.assertEqual(image["max_detection_conf"], 0.94)


if __name__ == "__main__":
    unittest.main()
