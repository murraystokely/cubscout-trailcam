"""The SQLite manifest: the spine every stage hangs off.

`design.md` calls this the most important design choice in the pipeline, and
the reason is the machine we are running on.  MegaDetector on a laptop CPU
is a multi-hour job.  A multi-hour job on a laptop gets interrupted -- the
lid closes, the battery goes, somebody wants the machine.  So the unit of
work has to be one frame, and finishing has to mean "there is a row for it",
not "the program reached the end".

That gives us, for free:

  * resumable       -- rerun it and it picks up the frames with no row yet
  * idempotent      -- rerunning finished work changes nothing
  * inspectable     -- `sqlite3 manifest.sqlite` beats any log file
  * joinable        -- the camera's own decision sits in the same row as
                       MegaDetector's, which is the entire point of E1

Two tables, and one view that does the confidence split.
"""

import sqlite3

from . import config


SCHEMA = """
-- One row per full-colour training-burst frame.
--
-- The lores PNGs are deliberately absent: 53,144 of them against 5,459
-- JPEGs, and nothing can be identified in a 640x480 YUV buffer anyway.
-- They are the replay's input in E2, not the detector's.
CREATE TABLE IF NOT EXISTS frames (
    id              INTEGER PRIMARY KEY,
    camera          TEXT    NOT NULL,
    day             TEXT    NOT NULL,   -- YYYY-MM-DD, from the path
    path            TEXT    NOT NULL UNIQUE,   -- relative to PHOTO_ROOT
    captured_at     TEXT    NOT NULL,   -- ISO, to the millisecond

    -- What the camera in the woods decided about this very frame, lifted
    -- from measurements-<camera>.csv.  This is the baseline E2 grades.
    -- NULL means the frame had no CSV row, which should not happen and is
    -- worth knowing about if it does.
    camera_decision TEXT,
    mean_luma       REAL,
    largest_area    INTEGER,

    -- Filled in by the detector pass.  NULL detected_at is the work queue.
    detected_at     TEXT,
    detector        TEXT,
    n_animal        INTEGER,
    n_person        INTEGER,
    n_vehicle       INTEGER,
    max_animal_conf REAL,
    max_person_conf REAL,
    detect_error    TEXT,               -- set if this frame would not read

    -- A person's verdict, once they have looked.  Only ever written by the
    -- hand-labelling step, never by the detector, so a rerun of the pass
    -- cannot destroy human work.
    hand_label      TEXT,
    hand_labelled_at TEXT
);

CREATE INDEX IF NOT EXISTS frames_todo  ON frames(detected_at);
CREATE INDEX IF NOT EXISTS frames_where ON frames(camera, day);

-- One row per box MegaDetector drew above config.MIN_STORED_CONFIDENCE.
-- Coordinates are MegaDetector's own convention, kept unchanged so the
-- export is a straight copy: normalised [0,1] fractions of the frame,
-- x and y being the top-left corner.
CREATE TABLE IF NOT EXISTS detections (
    id          INTEGER PRIMARY KEY,
    frame_id    INTEGER NOT NULL REFERENCES frames(id) ON DELETE CASCADE,
    category    TEXT    NOT NULL,       -- animal | person | vehicle
    confidence  REAL    NOT NULL,
    x           REAL    NOT NULL,
    y           REAL    NOT NULL,
    w           REAL    NOT NULL,
    h           REAL    NOT NULL,
    crop_path   TEXT                    -- relative to DATA_DIR, or NULL
);

CREATE INDEX IF NOT EXISTS detections_frame ON detections(frame_id);

-- The confidence split, as a view rather than a column, because it is a
-- question about thresholds and thresholds change.  Re-reading the view
-- after editing config.py costs nothing; re-labelling 5,000 rows does not.
CREATE VIEW IF NOT EXISTS truth AS
SELECT
    f.*,
    CASE
        WHEN f.detected_at IS NULL                    THEN 'not yet seen'
        WHEN f.detect_error IS NOT NULL               THEN 'unreadable'
        WHEN f.hand_label IS NOT NULL                 THEN f.hand_label
        WHEN f.mean_luma IS NOT NULL
             AND f.mean_luma < {auto_truth_min_luma}  THEN 'too dark to trust'
        WHEN f.max_animal_conf >= {animal_truth}      THEN 'animal'
        WHEN f.max_person_conf >= {person_truth}      THEN 'person'
        WHEN f.max_animal_conf <  {empty_truth}
             AND (f.max_person_conf IS NULL
                  OR f.max_person_conf < {empty_truth}) THEN 'empty'
        ELSE 'uncertain'
    END AS label,
    CASE WHEN f.hand_label IS NOT NULL THEN 'hand' ELSE 'auto' END AS label_source
FROM frames f;
"""


def open_manifest(path=None):
    """Open (creating if needed) the manifest and return the connection."""
    config.ensure_directories()

    database = sqlite3.connect(path or config.MANIFEST)
    database.row_factory = sqlite3.Row

    # Write-ahead logging so a reader -- a second terminal running
    # `report` -- does not block the overnight writer.
    database.execute("PRAGMA journal_mode=WAL")
    database.execute("PRAGMA foreign_keys=ON")

    database.executescript(SCHEMA.format(
        animal_truth=config.ANIMAL_TRUTH,
        empty_truth=config.EMPTY_TRUTH,
        person_truth=config.PERSON_TRUTH,
        auto_truth_min_luma=config.AUTO_TRUTH_MIN_LUMA,
    ))

    return database


def refresh_truth_view(database):
    """Rebuild the `truth` view from the current config.

    Call this after editing thresholds in config.py.  The stored view text
    is a snapshot of the numbers as they were when the file was created, and
    SQLite will happily keep using yesterday's.
    """
    database.execute("DROP VIEW IF EXISTS truth")
    database.executescript(SCHEMA.format(
        animal_truth=config.ANIMAL_TRUTH,
        empty_truth=config.EMPTY_TRUTH,
        person_truth=config.PERSON_TRUTH,
        auto_truth_min_luma=config.AUTO_TRUTH_MIN_LUMA,
    ))
    database.commit()


def add_frames(database, frames):
    """Insert frames discovered on disk.  Returns how many were new.

    `INSERT OR IGNORE` on the UNIQUE path is what makes re-scanning safe:
    the fifty-ninth run of `scan` after a fresh sync adds only the frames
    that arrived since the fifty-eighth, and touches nothing else -- in
    particular it does not clear anybody's detection results.
    """
    before = database.total_changes

    database.executemany(
        """
        INSERT OR IGNORE INTO frames
            (camera, day, path, captured_at,
             camera_decision, mean_luma, largest_area)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [(f.camera, f.day, f.relative_path, f.captured_at.isoformat(),
          f.camera_decision, f.mean_luma, f.largest_area) for f in frames],
    )
    database.commit()

    return database.total_changes - before


def frames_to_detect(database, camera=None, day=None, limit=None, redo=False):
    """The work queue: frames with no detector result yet.

    With `redo`, everything matching instead -- for when the detector
    changes and old rows have to be replaced.
    """
    where = [] if redo else ["detected_at IS NULL"]
    arguments = []

    if camera:
        where.append("camera = ?")
        arguments.append(camera)
    if day:
        where.append("day = ?")
        arguments.append(day)

    sql = "SELECT id, path FROM frames"
    if where:
        sql += " WHERE " + " AND ".join(where)
    # Chronological, so an interrupted run leaves whole days finished and
    # a partial day is obvious rather than scattered.
    sql += " ORDER BY camera, day, captured_at"
    if limit:
        sql += f" LIMIT {int(limit)}"

    return database.execute(sql, arguments).fetchall()


def record_detections(database, frame_id, detector, boxes, error=None):
    """Write one frame's result.  Replaces anything already there.

    The delete-then-insert is what makes `--redo` safe: a frame cannot end
    up with MDv5's boxes and MDv6's boxes both claiming to describe it.
    """
    database.execute("DELETE FROM detections WHERE frame_id = ?", (frame_id,))

    if error is not None:
        database.execute(
            """
            UPDATE frames SET detected_at = datetime('now'), detector = ?,
                   detect_error = ?, n_animal = NULL, n_person = NULL,
                   n_vehicle = NULL, max_animal_conf = NULL,
                   max_person_conf = NULL
             WHERE id = ?
            """,
            (detector, str(error)[:500], frame_id),
        )
        return

    database.executemany(
        """
        INSERT INTO detections
            (frame_id, category, confidence, x, y, w, h, crop_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [(frame_id, b.category, b.confidence, b.x, b.y, b.w, b.h, b.crop_path)
         for b in boxes],
    )

    def confidences(category):
        return [b.confidence for b in boxes if b.category == category]

    animals = confidences("animal")
    people = confidences("person")

    database.execute(
        """
        UPDATE frames SET detected_at = datetime('now'), detector = ?,
               detect_error = NULL,
               n_animal = ?, n_person = ?, n_vehicle = ?,
               max_animal_conf = ?, max_person_conf = ?
         WHERE id = ?
        """,
        (detector,
         len(animals), len(people), len(confidences("vehicle")),
         max(animals, default=0.0), max(people, default=0.0),
         frame_id),
    )


def counts(database):
    """A few numbers for `status`: what is on disk, what has been seen."""
    row = database.execute(
        """
        SELECT COUNT(*)                                    AS frames,
               SUM(detected_at IS NOT NULL)                AS detected,
               SUM(detect_error IS NOT NULL)               AS errors,
               COUNT(DISTINCT camera)                      AS cameras,
               COUNT(DISTINCT day)                         AS days,
               MIN(day)                                    AS first_day,
               MAX(day)                                    AS last_day
          FROM frames
        """
    ).fetchone()

    return dict(row)
