"""The SQLite manifest: the spine every stage hangs off.

`design.md` calls this the most important design choice in the pipeline, and
the reason is the machine we are running on.  MegaDetector on a laptop CPU
is a multi-hour job.  A multi-hour job on a laptop gets interrupted -- the
lid closes, the battery goes, somebody wants the machine.  So the unit of
work has to be one frame, and finishing has to mean "there is a row for it",
not "the program reached the end".

The shape, in one paragraph: `frames` says what each file is and nothing
that any algorithm decided.  `runs` says what we ran, with which settings,
when.  `frame_results` pairs the two -- one row per frame per run -- and
`detections` hangs off that.  Human verdicts live apart in `labels`, where
no rerun can reach them.

Five consequences, and each one is why a table is shaped as it is:

  * resumable   -- a run's queue is the frames with no row for THAT run
  * idempotent  -- rerunning finished work changes nothing
  * comparable  -- two models over the same frames is a self-join, which is
                   the entire question in milestone E4
  * honest      -- "we looked and found nothing" is a `frame_results` row
                   with no detections; "we have not looked" is no row.  In
                   one table those are indistinguishable, and confusing them
                   turns unprocessed frames into confident empties
  * auditable   -- every number traces to a run that records its parameters,
                   its weights, the code that produced it and when

The camera in the woods is a run too.  That is not a flourish: this archive
contains six of them -- wildlifecam4 alone ran three different versions of
step8 in three weeks -- and a single `camera_decision` column would average
three algorithms together without saying so.
"""

import json
import os
import socket
import sqlite3
import subprocess

from . import config


SCHEMA_VERSION = 3


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

-- One row per full-colour training-burst frame, holding only what is true
-- about the file itself.  The test for belonging here: would this column
-- still be right if no model had ever been run?
--
-- The lores PNGs are deliberately absent: 53,144 of them against 5,459
-- JPEGs, and nothing can be identified in a 640x480 YUV buffer anyway.
-- They are the replay's input in E2, not the detector's.
CREATE TABLE IF NOT EXISTS frames (
    id          INTEGER PRIMARY KEY,
    camera      TEXT NOT NULL,
    day         TEXT NOT NULL,          -- YYYY-MM-DD, from the path
    path        TEXT NOT NULL UNIQUE,   -- relative to PHOTO_ROOT
    captured_at TEXT NOT NULL,          -- ISO, to the millisecond

    -- Brightness of the image.  Algorithms read it, none of them decided
    -- it, so it sits with the file.
    mean_luma   REAL
);

CREATE INDEX IF NOT EXISTS frames_where ON frames(camera, day);

-- One row per thing-that-looked-at-frames.  Three kinds so far:
--
--   camera    what step8 decided in the woods, one run per (camera, code
--             version), because that is one deployment of one algorithm
--   detector  a pass of MegaDetector or similar on the laptop
--   replay    E2/E3: motion.py offline, at some set of thresholds
--
-- Per execution, not per configuration: running MDV5A again next month
-- makes a second row, and the drift between them is then a question you
-- can ask.  `params` carries whatever distinguishes two runs of the same
-- name -- for a sweep that is the constant being varied, and it is the
-- only thing telling those twenty runs apart.
CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY,
    kind         TEXT NOT NULL,         -- camera | detector | replay
    name         TEXT NOT NULL,
    params       TEXT,                  -- JSON, may be NULL
    weights_md5  TEXT,
    code_version TEXT,                  -- step8 fingerprint, or our commit
    host         TEXT,
    started_at   TEXT,
    finished_at  TEXT,                  -- NULL while still running
    role         TEXT,                  -- NULL | 'reference'
    notes        TEXT
);

-- At most one run per role.  Which model counts as ground truth is a
-- policy, and policies change: switching it is one UPDATE and no recompute,
-- and every other run stays queryable for comparison.
CREATE UNIQUE INDEX IF NOT EXISTS runs_one_per_role
    ON runs(role) WHERE role IS NOT NULL;

-- One row per frame per run.  The existence of the row is the claim "this
-- run has seen this frame"; everything else is what it concluded.
CREATE TABLE IF NOT EXISTS frame_results (
    id           INTEGER PRIMARY KEY,
    run_id       INTEGER NOT NULL REFERENCES runs(id)   ON DELETE CASCADE,
    frame_id     INTEGER NOT NULL REFERENCES frames(id) ON DELETE CASCADE,
    recorded_at  TEXT,
    status       TEXT NOT NULL DEFAULT 'ok',  -- ok | error
    error        TEXT,

    -- Motion runs (camera and replay) answer with a decision and a blob.
    decision     TEXT,
    largest_area INTEGER,

    -- Detector runs answer with boxes; these summarise them so the common
    -- queries do not have to touch `detections` at all.
    n_animal        INTEGER,
    n_person        INTEGER,
    n_vehicle       INTEGER,
    max_animal_conf REAL,
    max_person_conf REAL,

    -- Everything else the run measured, as JSON.  A sweep that invents a
    -- new metric should not need a migration; the ones that turn out to
    -- matter can be promoted to real columns later.
    metrics      TEXT,

    UNIQUE (run_id, frame_id)
);

CREATE INDEX IF NOT EXISTS frame_results_queue ON frame_results(run_id, status);
CREATE INDEX IF NOT EXISTS frame_results_frame ON frame_results(frame_id);

-- One row per box, in MegaDetector's own coordinate convention: fractions
-- of the frame in [0, 1], with (x, y) the top-left corner.  That is NOT
-- what step8's JSON uses (absolute pixels), and the mismatch is a real
-- source of bugs, so it is written down in both places.
CREATE TABLE IF NOT EXISTS detections (
    id              INTEGER PRIMARY KEY,
    frame_result_id INTEGER NOT NULL
                    REFERENCES frame_results(id) ON DELETE CASCADE,
    category        TEXT NOT NULL,      -- animal | person | vehicle
    confidence      REAL NOT NULL,
    x REAL NOT NULL, y REAL NOT NULL, w REAL NOT NULL, h REAL NOT NULL,
    crop_path       TEXT                -- relative to DATA_DIR, or NULL
);

CREATE INDEX IF NOT EXISTS detections_result ON detections(frame_result_id);

-- What a person saw.  Its own table, reachable by no run, so "a rerun
-- cannot destroy human work" is structural rather than remembered.
--
-- Called `annotations` rather than `labels` because in machine learning a
-- "label" is just as often the model's own class output; annotation means
-- unambiguously that a human wrote it.  (The camera-trap data standard,
-- Camtrap DP, calls these `observations` -- worth knowing if we ever
-- publish, but observation implies an animal was seen, and most of ours
-- will say "empty".)
--
-- Several rows per frame are allowed on purpose: two Scouts disagreeing
-- about the same picture is data, not a constraint violation.  The view
-- below takes the most recent.
CREATE TABLE IF NOT EXISTS annotations (
    id           INTEGER PRIMARY KEY,
    frame_id     INTEGER NOT NULL REFERENCES frames(id) ON DELETE CASCADE,
    label        TEXT NOT NULL,
    who          TEXT,
    labelled_at  TEXT NOT NULL,
    notes        TEXT
);

CREATE INDEX IF NOT EXISTS annotations_frame
    ON annotations(frame_id);

CREATE VIEW IF NOT EXISTS latest_annotation AS
SELECT a.* FROM annotations a
JOIN (SELECT frame_id, MAX(id) AS id FROM annotations GROUP BY frame_id)
     newest ON newest.id = a.id;

-- The confidence split, as a view rather than a column, because it is a
-- question about thresholds and thresholds change.  Re-reading the view
-- after editing config.py costs nothing; re-labelling 5,000 rows does not.
--
-- It is called `verdicts`, and NOT `truth`, which is what it was called
-- first.  `evaluation-design.md` says in as many words that MegaDetector
-- is not an oracle -- and then a view named `truth` presents one model's
-- opinion as exactly that.  Six months from now nobody would remember the
-- caveat; they would remember the column name.  A verdict is what a
-- nominated authority currently says, which is the honest description.
--
-- Two joins do the work.  `reference` is whichever run currently holds
-- that authority.  The camera join needs no role: each frame belongs to
-- exactly one camera run -- the deployment that recorded it -- so joining
-- on kind alone picks out that frame's own baseline.
CREATE VIEW IF NOT EXISTS verdicts AS
SELECT
    f.id AS frame_id, f.camera, f.day, f.path, f.captured_at, f.mean_luma,

    camera_result.decision     AS camera_decision,
    camera_result.largest_area AS largest_area,
    camera_run.code_version    AS camera_code,

    reference_result.run_id          AS run_id,
    reference_result.status          AS status,
    reference_result.max_animal_conf AS max_animal_conf,
    reference_result.max_person_conf AS max_person_conf,

    human.label AS annotation,

    CASE
        WHEN human.label IS NOT NULL                     THEN human.label
        WHEN reference_result.id IS NULL                 THEN 'not yet seen'
        WHEN reference_result.status <> 'ok'             THEN 'unreadable'
        WHEN f.mean_luma IS NOT NULL
             AND f.mean_luma < {auto_truth_min_luma}     THEN 'too dark to trust'
        WHEN reference_result.max_animal_conf >= {animal_truth}
                                                         THEN 'animal'
        WHEN reference_result.max_person_conf >= {person_truth}
                                                         THEN 'person'
        WHEN reference_result.max_animal_conf < {empty_truth}
             AND (reference_result.max_person_conf IS NULL
                  OR reference_result.max_person_conf < {empty_truth})
                                                         THEN 'empty'
        ELSE 'uncertain'
    END AS verdict,

    CASE WHEN human.label IS NOT NULL THEN 'human' ELSE 'model' END
        AS verdict_source

FROM frames f
LEFT JOIN runs reference          ON reference.role = 'reference'
LEFT JOIN frame_results reference_result
       ON reference_result.frame_id = f.id
      AND reference_result.run_id = reference.id
LEFT JOIN frame_results camera_result
       ON camera_result.frame_id = f.id
      AND camera_result.run_id IN (SELECT id FROM runs WHERE kind = 'camera')
LEFT JOIN runs camera_run         ON camera_run.id = camera_result.run_id
LEFT JOIN latest_annotation human ON human.frame_id = f.id;
"""


def _formatted_schema():
    return SCHEMA.format(
        animal_truth=config.ANIMAL_TRUTH,
        empty_truth=config.EMPTY_TRUTH,
        person_truth=config.PERSON_TRUTH,
        auto_truth_min_luma=config.AUTO_TRUTH_MIN_LUMA,
    )


def open_manifest(path=None):
    """Open (creating or migrating if needed) the manifest."""
    config.ensure_directories()

    database = sqlite3.connect(path or config.MANIFEST)
    database.row_factory = sqlite3.Row

    # Write-ahead logging so a reader -- a second terminal running
    # `report` -- does not block the overnight writer.
    database.execute("PRAGMA journal_mode=WAL")
    database.execute("PRAGMA foreign_keys=ON")

    migrate(database)

    database.executescript(_formatted_schema())

    if not database.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]:
        database.execute("INSERT INTO schema_version VALUES (?)",
                         (SCHEMA_VERSION,))
    database.commit()

    return database


def refresh_verdicts(database):
    """Rebuild the views from the current config.

    Call this after editing thresholds in config.py.  The stored view text
    is a snapshot of the numbers as they were when it was created, and
    SQLite will happily keep using yesterday's.
    """
    database.execute("DROP VIEW IF EXISTS verdicts")
    database.execute("DROP VIEW IF EXISTS latest_annotation")
    database.executescript(_formatted_schema())
    database.commit()


# ------------------------------------------------------------
# Migration from the single-model schema
# ------------------------------------------------------------

def _tables(database):
    return {row[0] for row in database.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def _migrate_names(database):
    """Version 2 called them `labels` and `truth`.  Both names were wrong.

    `truth` presented one model's opinion as ground truth in a project
    whose design document says the model is not an oracle, and `label` is
    used in machine learning for a model's own output as often as for a
    person's.  Renamed while there was exactly one manifest in the world;
    doing it later would mean everyone with a database doing it too.
    """
    tables = _tables(database)

    if "labels" in tables and "annotations" not in tables:
        print("Renaming labels -> annotations, truth -> verdicts...")
        database.execute("DROP VIEW IF EXISTS truth")
        database.execute("DROP VIEW IF EXISTS latest_label")
        database.execute("DROP INDEX IF EXISTS labels_frame")
        database.execute("ALTER TABLE labels RENAME TO annotations")
        database.execute("DELETE FROM schema_version")
        database.execute("INSERT INTO schema_version VALUES (?)",
                         (SCHEMA_VERSION,))
        database.commit()


def migrate(database):
    """Bring an older manifest up to the current shape.

    Version 1 put one model's answers in columns on `frames`.  The detector
    results are hours of CPU time and are moved across intact.  The camera's
    decisions are NOT: version 1 had nowhere to record which step8 produced
    them, and that attribution only exists on disk.  They are dropped, and
    the next `scan` rebuilds them properly as one run per deployment.
    """
    tables = _tables(database)

    if "frames" not in tables or "runs" in tables:
        _migrate_names(database)
        return                                  # new database, or already v2

    columns = {row[1] for row in database.execute("PRAGMA table_info(frames)")}
    if "detector" not in columns:
        return                                  # not the shape we expect

    print("Migrating the manifest to the multi-run schema...")

    # Views first: version 1's `truth` reads columns that are about to move,
    # and SQLite refuses to rename a table while a view points at one that
    # no longer exists.  The full schema recreates them at the end.
    database.execute("DROP VIEW IF EXISTS truth")
    database.execute("DROP VIEW IF EXISTS verdicts")
    database.execute("DROP VIEW IF EXISTS latest_label")
    database.execute("DROP VIEW IF EXISTS latest_annotation")

    # Only the new tables, by hand.  The full schema script cannot run yet:
    # it wants to index `detections.frame_result_id`, and at this point
    # `detections` is still the old table keyed by frame.
    database.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
            params TEXT, weights_md5 TEXT, code_version TEXT, host TEXT,
            started_at TEXT, finished_at TEXT, role TEXT, notes TEXT);
        CREATE TABLE IF NOT EXISTS frame_results (
            id INTEGER PRIMARY KEY,
            run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            frame_id INTEGER NOT NULL,
            recorded_at TEXT, status TEXT NOT NULL DEFAULT 'ok', error TEXT,
            decision TEXT, largest_area INTEGER,
            n_animal INTEGER, n_person INTEGER, n_vehicle INTEGER,
            max_animal_conf REAL, max_person_conf REAL, metrics TEXT,
            UNIQUE (run_id, frame_id));
        CREATE TABLE IF NOT EXISTS annotations (
            id INTEGER PRIMARY KEY, frame_id INTEGER NOT NULL,
            label TEXT NOT NULL, who TEXT, labelled_at TEXT NOT NULL,
            notes TEXT);
        ALTER TABLE detections ADD COLUMN frame_result_id INTEGER;
    """)

    # One run per distinct detector name that appears in the old table.
    detectors = [row[0] for row in database.execute(
        "SELECT DISTINCT detector FROM frames WHERE detector IS NOT NULL")]

    run_for = {}
    for detector in detectors:
        when = database.execute(
            "SELECT MIN(detected_at), MAX(detected_at) FROM frames "
            "WHERE detector = ?", (detector,)).fetchone()

        cursor = database.execute(
            """INSERT INTO runs (kind, name, code_version, host, started_at,
                                 finished_at, notes)
               VALUES ('detector', ?, NULL, NULL, ?, ?, ?)""",
            (detector, when[0], when[1],
             "recovered from the single-model schema by migration"))
        run_for[detector] = cursor.lastrowid

    moved = 0
    for row in database.execute(
            "SELECT * FROM frames WHERE detector IS NOT NULL").fetchall():
        cursor = database.execute(
            """INSERT INTO frame_results
                   (run_id, frame_id, recorded_at, status, error,
                    n_animal, n_person, n_vehicle,
                    max_animal_conf, max_person_conf)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_for[row["detector"]], row["id"], row["detected_at"],
             "error" if row["detect_error"] else "ok", row["detect_error"],
             row["n_animal"], row["n_person"], row["n_vehicle"],
             row["max_animal_conf"], row["max_person_conf"]))

        database.execute(
            "UPDATE detections SET frame_result_id = ? WHERE frame_id = ?",
            (cursor.lastrowid, row["id"]))
        moved += 1

    for row in database.execute(
            "SELECT id, hand_label, hand_labelled_at FROM frames "
            "WHERE hand_label IS NOT NULL").fetchall():
        database.execute(
            "INSERT INTO annotations (frame_id, label, who, labelled_at) "
            "VALUES (?, ?, 'migrated', ?)",
            (row["id"], row["hand_label"], row["hand_labelled_at"]))

    # The first detector run becomes the reference, so the verdicts view
    # has something to read.  `trailcam reference` changes it.
    if run_for:
        database.execute("UPDATE runs SET role = 'reference' WHERE id = ?",
                         (min(run_for.values()),))

    # Rebuild `frames` without the model columns.  SQLite can drop columns
    # since 3.35, but a rebuild also drops the old indexes and views in one
    # step and works on any version anyone has.
    database.executescript("""
        CREATE TABLE frames_v2 (
            id          INTEGER PRIMARY KEY,
            camera      TEXT NOT NULL,
            day         TEXT NOT NULL,
            path        TEXT NOT NULL UNIQUE,
            captured_at TEXT NOT NULL,
            mean_luma   REAL
        );
        INSERT INTO frames_v2 (id, camera, day, path, captured_at, mean_luma)
            SELECT id, camera, day, path, captured_at, mean_luma FROM frames;
        DROP TABLE frames;
        ALTER TABLE frames_v2 RENAME TO frames;
        CREATE INDEX IF NOT EXISTS frames_where ON frames(camera, day);
    """)

    # And `detections` without its old frame_id.
    database.executescript("""
        CREATE TABLE detections_v2 (
            id              INTEGER PRIMARY KEY,
            frame_result_id INTEGER NOT NULL
                            REFERENCES frame_results(id) ON DELETE CASCADE,
            category        TEXT NOT NULL,
            confidence      REAL NOT NULL,
            x REAL NOT NULL, y REAL NOT NULL, w REAL NOT NULL, h REAL NOT NULL,
            crop_path       TEXT
        );
        INSERT INTO detections_v2 (id, frame_result_id, category, confidence,
                                   x, y, w, h, crop_path)
            SELECT id, frame_result_id, category, confidence,
                   x, y, w, h, crop_path
              FROM detections WHERE frame_result_id IS NOT NULL;
        DROP TABLE detections;
        ALTER TABLE detections_v2 RENAME TO detections;
        CREATE INDEX IF NOT EXISTS detections_result
            ON detections(frame_result_id);
    """)

    database.execute("DELETE FROM schema_version")
    database.execute("INSERT INTO schema_version VALUES (?)",
                     (SCHEMA_VERSION,))
    database.commit()

    print(f"  {moved} detector results moved into "
          f"{len(run_for)} run(s): {', '.join(detectors)}")
    print("  the camera's own decisions were dropped -- version 1 could not "
          "record which step8 made them.")
    print("  Run `scan` to rebuild them, one run per deployment.")


# ------------------------------------------------------------
# Runs
# ------------------------------------------------------------

def _code_version():
    """The commit this code is running from, for the audit trail.

    step8 stamps its own fingerprint into every photograph for exactly this
    reason; a run that cannot say which code produced it is a number with
    no provenance.
    """
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:                           # noqa: BLE001
        return None


def start_run(database, kind, name, params=None, weights_md5=None,
              code_version=None, notes=None):
    """Record a new run and return its id."""
    cursor = database.execute(
        """INSERT INTO runs (kind, name, params, weights_md5, code_version,
                             host, started_at, notes)
           VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?)""",
        (kind, name,
         json.dumps(params, sort_keys=True) if params else None,
         weights_md5,
         code_version if code_version is not None else _code_version(),
         socket.gethostname(), notes))
    database.commit()

    return cursor.lastrowid


def resume_or_start_run(database, kind, name, params=None, weights_md5=None,
                        force_new=False):
    """Continue an unfinished run with these exact settings, or start one.

    Runs are per execution, which would make "resume after Ctrl-C" create a
    second run and redo the work.  So an *unfinished* run with the same
    kind, name and parameters is picked up instead.  Finishing a run closes
    it for good: the next pass with the same settings starts a new row, and
    the two can then be compared.
    """
    parameters = json.dumps(params, sort_keys=True) if params else None

    if not force_new:
        existing = database.execute(
            """SELECT id FROM runs
                WHERE kind = ? AND name = ? AND finished_at IS NULL
                  AND params IS ?
             ORDER BY id DESC LIMIT 1""",
            (kind, name, parameters)).fetchone()

        if existing:
            return existing["id"], True

    return start_run(database, kind, name, params=params,
                     weights_md5=weights_md5), False


def finish_run(database, run_id):
    database.execute(
        "UPDATE runs SET finished_at = datetime('now') WHERE id = ?",
        (run_id,))
    database.commit()


def camera_run(database, camera, code_version):
    """Find or create the run for one deployment of step8.

    One per (camera, code version): that is one algorithm, running on one
    machine, over some stretch of days.  wildlifecam4 alone has three in
    this archive.
    """
    found = database.execute(
        """SELECT id FROM runs
            WHERE kind = 'camera' AND name = ? AND code_version IS ?""",
        (camera, code_version)).fetchone()

    if found:
        return found["id"]

    return start_run(
        database, "camera", camera, code_version=code_version,
        notes="what step8 decided in the woods, read from measurements CSV")


def set_reference(database, run_id):
    """Choose which run counts as ground truth.  One UPDATE, no recompute."""
    database.execute("UPDATE runs SET role = NULL WHERE role = 'reference'")
    database.execute("UPDATE runs SET role = 'reference' WHERE id = ?",
                     (run_id,))
    database.commit()


def reference_run(database):
    return database.execute(
        "SELECT * FROM runs WHERE role = 'reference'").fetchone()


def runs(database):
    """Every run, with how much of the archive it has covered."""
    return database.execute(
        """SELECT r.*,
                  (SELECT COUNT(*) FROM frame_results fr WHERE fr.run_id = r.id)
                      AS frames,
                  (SELECT COUNT(*) FROM frame_results fr
                    WHERE fr.run_id = r.id AND fr.status <> 'ok') AS errors
             FROM runs r ORDER BY r.id"""
    ).fetchall()


# ------------------------------------------------------------
# Frames
# ------------------------------------------------------------

def add_frames(database, frames):
    """Insert frames discovered on disk.  Returns how many were new.

    `INSERT OR IGNORE` on the UNIQUE path is what makes re-scanning safe:
    the fifty-ninth run of `scan` after a fresh sync adds only the frames
    that arrived since the fifty-eighth, and touches nothing else -- in
    particular it does not clear anybody's results.
    """
    before = database.total_changes

    database.executemany(
        """INSERT OR IGNORE INTO frames
               (camera, day, path, captured_at, mean_luma)
           VALUES (?, ?, ?, ?, ?)""",
        [(f.camera, f.day, f.relative_path, f.captured_at.isoformat(),
          f.mean_luma) for f in frames])
    database.commit()

    return database.total_changes - before


def record_camera_decisions(database, frames):
    """Write what the camera decided, into the run for its deployment.

    Idempotent: a frame already recorded against its camera run is left
    alone, so `scan` after every sync stays cheap and safe.
    """
    identifiers = {row["path"]: row["id"] for row in database.execute(
        "SELECT id, path FROM frames")}

    written = 0
    for frame in frames:
        frame_id = identifiers.get(frame.relative_path)
        if frame_id is None or frame.camera_decision is None:
            continue

        run_id = camera_run(database, frame.camera, frame.code_version)

        cursor = database.execute(
            """INSERT OR IGNORE INTO frame_results
                   (run_id, frame_id, recorded_at, status, decision,
                    largest_area, metrics)
               VALUES (?, ?, ?, 'ok', ?, ?, ?)""",
            (run_id, frame_id, frame.captured_at.isoformat(),
             frame.camera_decision, frame.largest_area,
             json.dumps(frame.metrics, sort_keys=True)
             if frame.metrics else None))
        written += cursor.rowcount

    # A camera run's span is the deployment's span: first and last frame we
    # have from it.  Recomputed on every scan, so a camera still out there
    # keeps extending while an old version stays fixed at the day it was
    # replaced.  (For a camera run, "finished" means "we have imported
    # everything we hold", not "that Pi has been switched off".)
    database.execute("""
        UPDATE runs SET
            started_at = (SELECT MIN(recorded_at) FROM frame_results
                           WHERE run_id = runs.id),
            finished_at = (SELECT MAX(recorded_at) FROM frame_results
                            WHERE run_id = runs.id)
         WHERE kind = 'camera'
    """)

    database.commit()
    return written


def frames_to_detect(database, run_id, camera=None, day=None, limit=None,
                     retry_errors=False):
    """The work queue for one run: frames it has no row for.

    With `retry_errors`, frames it recorded as unreadable come back too --
    for when the cause was a truncated file that has since been re-synced.
    """
    where = ["result.id IS NULL"]
    arguments = [run_id]

    if retry_errors:
        where = ["(result.id IS NULL OR result.status <> 'ok')"]
    if camera:
        where.append("f.camera = ?")
        arguments.append(camera)
    if day:
        where.append("f.day = ?")
        arguments.append(day)

    sql = f"""SELECT f.id, f.path FROM frames f
              LEFT JOIN frame_results result
                     ON result.frame_id = f.id AND result.run_id = ?
              WHERE {' AND '.join(where)}
              ORDER BY f.camera, f.day, f.captured_at"""
    if limit:
        sql += f" LIMIT {int(limit)}"

    return database.execute(sql, arguments).fetchall()


def record_detections(database, run_id, frame_id, boxes, error=None):
    """Write one frame's result for one run.  Replaces anything there.

    The delete-then-insert is what makes a retry safe: a frame cannot end
    up with two attempts' boxes both claiming to describe it.  It is scoped
    to this run, so it can never touch another model's answers.
    """
    database.execute(
        "DELETE FROM frame_results WHERE run_id = ? AND frame_id = ?",
        (run_id, frame_id))

    if error is not None:
        database.execute(
            """INSERT INTO frame_results
                   (run_id, frame_id, recorded_at, status, error)
               VALUES (?, ?, datetime('now'), 'error', ?)""",
            (run_id, frame_id, str(error)[:500]))
        return

    def confidences(category):
        return [b.confidence for b in boxes if b.category == category]

    animals = confidences("animal")
    people = confidences("person")

    cursor = database.execute(
        """INSERT INTO frame_results
               (run_id, frame_id, recorded_at, status,
                n_animal, n_person, n_vehicle,
                max_animal_conf, max_person_conf)
           VALUES (?, ?, datetime('now'), 'ok', ?, ?, ?, ?, ?)""",
        (run_id, frame_id, len(animals), len(people),
         len(confidences("vehicle")),
         max(animals, default=0.0), max(people, default=0.0)))

    database.executemany(
        """INSERT INTO detections
               (frame_result_id, category, confidence, x, y, w, h, crop_path)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [(cursor.lastrowid, b.category, b.confidence, b.x, b.y, b.w, b.h,
          b.crop_path) for b in boxes])


def add_annotation(database, frame_id, label, who=None, notes=None):
    """Record what a person saw.  Never overwrites an earlier one."""
    database.execute(
        """INSERT INTO annotations (frame_id, label, who, labelled_at, notes)
           VALUES (?, ?, ?, datetime('now'), ?)""",
        (frame_id, label, who or os.environ.get("USER"), notes))
    database.commit()


def counts(database):
    """A few numbers for `status`: what is on disk, what has been seen."""
    reference = reference_run(database)

    row = database.execute(
        """SELECT COUNT(*) AS frames,
                  COUNT(DISTINCT camera) AS cameras,
                  COUNT(DISTINCT day) AS days,
                  MIN(day) AS first_day,
                  MAX(day) AS last_day
             FROM frames""").fetchone()

    totals = dict(row)
    totals["reference"] = reference["name"] if reference else None
    totals["detected"] = database.execute(
        "SELECT COUNT(*) FROM frame_results WHERE run_id = ?",
        (reference["id"] if reference else -1,)).fetchone()[0]
    totals["errors"] = database.execute(
        "SELECT COUNT(*) FROM frame_results WHERE run_id = ? AND status <> 'ok'",
        (reference["id"] if reference else -1,)).fetchone()[0]

    return totals
