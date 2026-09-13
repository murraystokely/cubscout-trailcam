"""Command line: scan, detect, report, label, export, status.

    python3 -m trailcam scan                  # find the bursts on disk
    python3 -m trailcam detect --limit 50     # try it on fifty frames
    python3 -m trailcam detect                # the real pass, hours long
    python3 -m trailcam report                # what it found
    python3 -m trailcam sample --size 200     # frames to label by eye
    python3 -m trailcam label <path> animal   # record what you saw
    python3 -m trailcam export md.json        # for Timelapse

Each stage is a separate subcommand rather than one `run`, because the
expensive stage is `detect` and nobody should have to rerun it to get a
different report out.
"""

import argparse
import sys

from . import bursts
from . import config
from . import detect as detect_module
from . import manifest as manifest_module
from . import report as report_module


def command_scan(options):
    """Walk the photo library and put a row in the manifest per frame."""
    frames = bursts.find_frames(camera=options.camera, day=options.day)

    if not frames:
        print(f"No training bursts found under {config.PHOTO_ROOT}.")
        print("Expected <camera>/<YYYY-MM-DD>/training/train_*.jpg -- "
              "is --record switched on out there, and has sync run?")
        return 1

    database = manifest_module.open_manifest()
    added = manifest_module.add_frames(database, frames)

    without_csv = sum(1 for f in frames if f.camera_decision is None)

    print(f"{len(frames)} training frames on disk, {added} new to the "
          f"manifest.")

    if without_csv:
        # Worth flagging loudly: a frame with no CSV row is a frame we
        # cannot grade the camera against, which is the whole point.
        print(f"  {without_csv} of them have no row in any "
              f"measurements-*.csv, so there is nothing to compare the "
              f"detector against for those.")

    database.close()
    return 0


def command_detect(options):
    """The pass itself."""
    summary = detect_module.run(
        camera=options.camera, day=options.day, limit=options.limit,
        redo=options.redo, crops=options.crops, model=options.model,
        threads=options.threads)

    # Nothing to do is a success.  Every frame failing is not.
    return 1 if summary["frames"] and summary["errors"] == summary["frames"] \
        else 0


def command_report(options):
    database = manifest_module.open_manifest()
    manifest_module.refresh_truth_view(database)      # pick up edited config
    report_module.everything(database)

    if options.uncertain:
        report_module.uncertain_queue(database, limit=options.uncertain)

    database.close()
    return 0


def command_sample(options):
    database = manifest_module.open_manifest()
    manifest_module.refresh_truth_view(database)
    report_module.check_the_checker(database, sample_size=options.size,
                                    seed=options.seed)
    database.close()
    return 0


def command_label(options):
    """Record what a person saw in one frame.

    Matched on the tail of the path, so any of these work:

        wildlifecam4/2026-08-24/training/train_103415_876.jpg
        train_103415_876.jpg
    """
    database = manifest_module.open_manifest()

    rows = database.execute(
        "SELECT id, path FROM frames WHERE path LIKE ?",
        (f"%{options.path}",)).fetchall()

    if not rows:
        print(f"No frame in the manifest matching {options.path!r}.")
        return 1
    if len(rows) > 1:
        print(f"{options.path!r} matches {len(rows)} frames; be more "
              f"specific:")
        for row in rows[:10]:
            print(f"  {row['path']}")
        return 1

    database.execute(
        "UPDATE frames SET hand_label = ?, hand_labelled_at = "
        "datetime('now') WHERE id = ?",
        (options.label, rows[0]["id"]))
    database.commit()

    print(f"{rows[0]['path']}: {options.label}")
    database.close()
    return 0


def command_export(options):
    database = manifest_module.open_manifest()
    report_module.export_megadetector_json(database, options.destination,
                                           camera=options.camera)
    database.close()
    return 0


def command_status(options):
    database = manifest_module.open_manifest()
    report_module.coverage(database)
    database.close()
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="trailcam",
        description="MegaDetector over the cameras' training bursts "
                    "(milestone E1 in ai/evaluation-design.md).")

    subcommands = parser.add_subparsers(dest="command", required=True)

    def with_selection(subparser):
        subparser.add_argument("--camera",
                               help="only this camera, e.g. wildlifecam4")
        subparser.add_argument("--day", help="only this day, e.g. 2026-08-24")
        return subparser

    scan = with_selection(subcommands.add_parser(
        "scan", help="find training bursts on disk and index them"))
    scan.set_defaults(function=command_scan)

    detect = with_selection(subcommands.add_parser(
        "detect", help="run MegaDetector over every frame not yet seen"))
    detect.add_argument("--limit", type=int,
                        help="stop after this many frames (try 50 first)")
    detect.add_argument("--redo", action="store_true",
                        help="run frames that already have results again")
    detect.add_argument("--model", default=None,
                        help=f"detector name (default {config.DETECTOR})")
    detect.add_argument("--threads", type=int, default=None,
                        help=f"CPU threads (default {config.THREADS})")
    detect.add_argument("--no-crops", dest="crops", action="store_false",
                        default=None, help="do not save animal crops")
    detect.set_defaults(function=command_detect)

    report = subcommands.add_parser(
        "report", help="the confidence split and the camera comparison")
    report.add_argument("--uncertain", type=int, metavar="N", default=0,
                        help="also list N frames from the uncertain band")
    report.set_defaults(function=command_report)

    sample = subcommands.add_parser(
        "sample", help="a random set of frames to label by eye")
    sample.add_argument("--size", type=int, default=200)
    sample.add_argument("--seed", type=int, default=None,
                        help="fix the sample so it can be reproduced")
    sample.set_defaults(function=command_sample)

    label = subcommands.add_parser(
        "label", help="record a person's verdict on one frame")
    label.add_argument("path")
    label.add_argument("label", choices=("animal", "empty", "person",
                                         "vehicle", "cannot tell"))
    label.set_defaults(function=command_label)

    export = subcommands.add_parser(
        "export", help="write MegaDetector-format JSON (for Timelapse)")
    export.add_argument("destination")
    export.add_argument("--camera")
    export.set_defaults(function=command_export)

    status = subcommands.add_parser("status", help="how much is done")
    status.set_defaults(function=command_status)

    return parser


def main(argv=None):
    options = build_parser().parse_args(argv)
    return options.function(options)


if __name__ == "__main__":
    sys.exit(main())
