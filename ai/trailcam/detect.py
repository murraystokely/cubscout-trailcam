"""The pass: MegaDetector over every training-burst frame.

This is milestone E1 in evaluation-design.md, and the shape of it is set by
one fact -- on a laptop CPU this is hours of work, so it must survive being
interrupted.  Everything here follows from that:

  * one frame is one unit of work, committed as it finishes
  * the work queue is a query (`detected_at IS NULL`), not a position in a
    loop, so "resume" is just "run it again"
  * a frame that will not read is recorded as broken and skipped, never
    raised, because one truncated JPEG must not cost a night's progress
  * Ctrl-C finishes the frame in hand and stops cleanly

Progress is printed as it goes, with a rate and an estimate, because a
program that prints nothing for four hours is indistinguishable from a
program that has hung.
"""

import signal
import time

from pathlib import Path

from . import config
from . import manifest as manifest_module
from .detector import MegaDetector, write_crop


def _crop_destination(frame_path, index, run_id):
    """ai/data/crops/run-7/<camera>/<day>/train_143015_287_0.jpg

    Mirroring the photo library's own layout means a crop can be traced
    back to its frame by eye, with no database lookup.  The run comes
    first because two models crop the same frame to different boxes, and
    without it the second would silently overwrite the first.
    """
    frame_path = Path(frame_path)
    # <camera>/<day>/training/train_x.jpg -> <camera>/<day>/training, and
    # <camera>/<day>/103415.jpg -> <camera>/<day>.  The crop sits in the
    # same place relative to the crops directory as the frame does to the
    # photo library, whatever kind of frame it is.  (An earlier version
    # took the grandparent, which was right for training frames and put a
    # photograph's crop under the camera directory with no day.)
    return (config.CROP_DIR / f"run-{run_id}" / frame_path.parent /
            f"{frame_path.stem}_{index}.jpg")


def run(camera=None, day=None, limit=None, new_run=False, crops=None,
        model=None, threads=None, quiet=False, retry_errors=False,
        kind=None):
    """Run the detector over every frame this run has not seen yet.

    Returns a small summary dictionary.  Safe to call again at any time:
    frames this run has already done are skipped, so the second call over a
    finished day does no work at all.

    Runs are per execution.  An interruption leaves the run open and the
    next call continues it; finishing closes it, so the pass after that
    starts a fresh run and the two can be compared.
    """
    write_crops = config.WRITE_CROPS if crops is None else crops
    name = model or config.DETECTOR

    database = manifest_module.open_manifest()

    # The parameters that make two runs of the same model different
    # answers.  Anything that changes what comes out belongs here, because
    # this is what a later comparison will be reading.
    parameters = {
        "min_confidence": config.MIN_STORED_CONFIDENCE,
        "crops": bool(write_crops),
    }

    run_id, resumed = manifest_module.resume_or_start_run(
        database, "detector", name, params=parameters, force_new=new_run)

    queue = manifest_module.frames_to_detect(
        database, run_id, camera=camera, day=day, limit=limit,
        retry_errors=retry_errors, kind=kind)

    if not queue:
        if not quiet:
            print(f"Nothing to do: run {run_id} ({name}) has seen every "
                  f"frame in the manifest.")
            print("(`scan` first if you have synced new bursts; `--new-run` "
                  "to run them all again as a fresh run.)")
        manifest_module.finish_run(database, run_id)
        return {"frames": 0, "animals": 0, "people": 0, "errors": 0,
                "run": run_id}

    if not quiet:
        print(f"{'Resuming' if resumed else 'Starting'} run {run_id}: "
              f"{name}, {len(queue)} frames to detect.")
        print(f"Loading {name} "
              f"(first run downloads the weights into {config.MODEL_DIR})...")

    started = time.time()
    detector = MegaDetector(model=model, threads=threads)

    if not quiet:
        print(f"Loaded in {time.time() - started:.0f}s on "
              f"{detector.threads} threads.\n")

    # Ctrl-C sets a flag instead of tearing out of the loop, so we always
    # stop between frames with the database consistent.
    interrupted = {"now": False}

    def on_interrupt(signum, frame):
        interrupted["now"] = True
        print("\nInterrupt: finishing this frame, then stopping. "
              "Rerun to resume.")

    previous_handler = signal.signal(signal.SIGINT, on_interrupt)

    summary = {"frames": 0, "animals": 0, "people": 0, "errors": 0,
               "run": run_id}
    started = time.time()

    try:
        for position, row in enumerate(queue, start=1):
            absolute = config.PHOTO_ROOT / row["path"]

            try:
                boxes = detector.detect(absolute)
            except Exception as failure:            # noqa: BLE001
                # Deliberately broad.  Anything at all that goes wrong with
                # one photograph is that photograph's problem, and the run
                # has thousands more to get through.
                manifest_module.record_detections(
                    database, run_id, row["id"], [], error=failure)
                summary["errors"] += 1
                if not quiet:
                    print(f"  ! {row['path']}: {failure}", flush=True)
            else:
                if write_crops:
                    boxes = _write_crops_for(absolute, boxes, run_id)

                manifest_module.record_detections(
                    database, run_id, row["id"], boxes)

                summary["animals"] += sum(
                    1 for b in boxes
                    if b.category == "animal"
                    and b.confidence >= config.ANIMAL_TRUTH)
                summary["people"] += sum(
                    1 for b in boxes
                    if b.category == "person"
                    and b.confidence >= config.PERSON_TRUTH)

            summary["frames"] += 1

            if position % config.COMMIT_EVERY == 0:
                database.commit()
                if not quiet:
                    _print_progress(position, len(queue), started, summary)

            if interrupted["now"]:
                break

        database.commit()

        # Only a run that emptied its queue is finished.  An interrupted
        # one stays open so the next call picks it up rather than starting
        # a second run over the same frames.
        if not interrupted["now"] and summary["frames"] == len(queue) \
                and not limit:
            manifest_module.finish_run(database, run_id)

            if manifest_module.reference_run(database) is None:
                manifest_module.set_reference(database, run_id)
                if not quiet:
                    print(f"\nRun {run_id} is now the reference run "
                          f"(nothing else was).")

    finally:
        signal.signal(signal.SIGINT, previous_handler)
        database.commit()
        database.close()

    if not quiet:
        elapsed = time.time() - started
        print(f"\nRun {run_id}: {summary['frames']} frames in "
              f"{elapsed / 60:.1f} min "
              f"({elapsed / max(summary['frames'], 1):.2f} s/frame)")
        print(f"  animals (>= {config.ANIMAL_TRUTH}): {summary['animals']}")
        print(f"  people  (>= {config.PERSON_TRUTH}): {summary['people']}")
        if summary["errors"]:
            print(f"  unreadable frames: {summary['errors']}")
        print("\nNext: `report` for the confidence split.")

    return summary


def _write_crops_for(absolute_path, boxes, run_id):
    """Attach crop paths to the boxes worth cropping.

    Animals and the uncertain band only.  People are detected so they can
    be excluded -- writing a folder of cropped Scouts would be the opposite
    of the privacy win the person class is supposed to buy us.
    """
    with_crops = []

    for index, box in enumerate(boxes):
        crop_path = None

        if box.category == "animal" and box.confidence >= config.EMPTY_TRUTH:
            destination = _crop_destination(absolute_path.relative_to(
                config.PHOTO_ROOT), index, run_id)
            try:
                written = write_crop(absolute_path, box, destination)
            except Exception:                       # noqa: BLE001
                written = None                      # a crop is never fatal
            if written is not None:
                crop_path = str(Path(written).relative_to(config.DATA_DIR))

        with_crops.append(box._replace(crop_path=crop_path))

    return with_crops


def _print_progress(position, total, started, summary):
    elapsed = time.time() - started
    rate = position / elapsed if elapsed else 0.0
    remaining = (total - position) / rate if rate else 0.0

    # flush, because an overnight run is usually `> detect.log &`, and
    # block-buffered progress in a log file is no progress at all.
    print(f"  {position:5d}/{total} "
          f"{rate:5.2f} frames/s  "
          f"{summary['animals']:4d} animals  "
          f"{summary['people']:4d} people  "
          f"eta {remaining / 60:5.1f} min", flush=True)
