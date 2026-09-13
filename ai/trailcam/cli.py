"""Command line: scan, detect, report, label, export, status.

    python3 -m trailcam scan                  # find the bursts on disk
    python3 -m trailcam detect --limit 50     # try it on fifty frames
    python3 -m trailcam detect                # the real pass, hours long
    python3 -m trailcam report                # what it found
    python3 -m trailcam runs                  # every run, and its coverage
    python3 -m trailcam reference 3           # which run counts as truth
    python3 -m trailcam compare 1 3           # where two runs disagree
    python3 -m trailcam sample --size 200     # frames to label by eye
    python3 -m trailcam label <path> animal   # record what you saw
    python3 -m trailcam export md.json        # for Timelapse

Each stage is a separate subcommand rather than one `run`, because the
expensive stage is `detect` and nobody should have to rerun it to get a
different report out.
"""

import argparse
import sys

from pathlib import Path

from . import bench as bench_module
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
    decisions = manifest_module.record_camera_decisions(database, frames)

    without_csv = sum(1 for f in frames if f.camera_decision is None)

    print(f"{len(frames)} training frames on disk, {added} new to the "
          f"manifest.")

    if decisions:
        print(f"  {decisions} camera decisions recorded.")

    # One run per (camera, step8 version).  Showing them here is how you
    # notice that a camera was reflashed mid-campaign.
    deployments = [r for r in manifest_module.runs(database)
                   if r["kind"] == "camera"]
    if deployments:
        print(f"  {len(deployments)} deployment(s) of step8 in this archive:")
        for row in deployments:
            print(f"    run {row['id']:3d}  {row['name']:14s} "
                  f"{row['code_version'] or 'unfingerprinted':14s} "
                  f"{row['frames']:6d} frames")

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
        new_run=options.new_run, crops=options.crops, model=options.model,
        threads=options.threads, retry_errors=options.retry_errors)

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

    manifest_module.add_label(database, rows[0]["id"], options.label)

    print(f"{rows[0]['path']}: {options.label}")
    database.close()
    return 0


def command_export(options):
    database = manifest_module.open_manifest()
    report_module.export_megadetector_json(database, options.destination,
                                           camera=options.camera,
                                           run_id=options.run)
    database.close()
    return 0


def command_runs(options):
    """Every run in the manifest, and how much of the archive it covers."""
    database = manifest_module.open_manifest()
    rows = manifest_module.runs(database)

    if not rows:
        print("No runs yet. `scan` records the camera's own decisions; "
              "`detect` adds a detector.")
        return 0

    total = manifest_module.counts(database)["frames"] or 0

    for row in rows:
        mark = "*" if row["role"] == "reference" else " "
        print(f"{mark} {row['id']:3d}  {row['kind']:8s} {row['name']:16s} "
              f"{row['code_version'] or '':14s} "
              f"{row['frames']:6d}/{total} frames  "
              f"{'open' if row['finished_at'] is None else 'done'}")
        if row["params"]:
            print(f"      params: {row['params']}")
        if row["errors"]:
            print(f"      {row['errors']} frames it could not read")

    print("\n* = reference run (what `truth` reads). "
          "Change it with `reference <id>`.")
    database.close()
    return 0


def command_reference(options):
    """Choose which run counts as ground truth."""
    database = manifest_module.open_manifest()

    row = database.execute("SELECT * FROM runs WHERE id = ?",
                           (options.run,)).fetchone()
    if row is None:
        print(f"No run {options.run}. `trailcam runs` lists them.")
        return 1

    if row["kind"] == "camera":
        # Not forbidden -- but grading the camera against itself scores
        # 100% and means nothing, so say so.
        print("Careful: that is a camera run. Making it the reference means "
              "grading the camera against its own decisions.")

    manifest_module.set_reference(database, options.run)
    print(f"Run {row['id']} ({row['name']}) is now the reference.")
    print("Nothing was recomputed; `report` will read it from here on.")
    database.close()
    return 0


def command_compare(options):
    database = manifest_module.open_manifest()
    manifest_module.refresh_truth_view(database)
    report_module.compare(database, options.run_a, options.run_b)
    database.close()
    return 0


def command_status(options):
    database = manifest_module.open_manifest()
    report_module.coverage(database)
    database.close()
    return 0


def command_bench(options):
    """Build a corpus, time a machine on it, or read the results back."""
    if options.what == "build":
        database = manifest_module.open_manifest()
        manifest_module.refresh_truth_view(database)
        bench_module.build_corpus(database, options.corpus,
                                  size=options.size, seed=options.seed)
        database.close()
        return 0

    if options.what == "run":
        result = bench_module.run_benchmark(
            options.corpus, model=options.model, device=options.device,
            threads=options.threads, min_seconds=options.min_seconds,
            verify=not options.no_verify)
        bench_module.save_result(result, options.out)
        return 0

    paths = sorted(Path(options.out).glob("*.json"))
    bench_module.summarise(paths)
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
    detect.add_argument("--new-run", action="store_true",
                        help="start a fresh run rather than continuing an "
                             "unfinished one with the same settings")
    detect.add_argument("--retry-errors", action="store_true",
                        help="also re-try frames this run could not read")
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
    export.add_argument("--run", type=int, default=None,
                        help="which run to export (default: the reference)")
    export.set_defaults(function=command_export)

    runs = subcommands.add_parser(
        "runs", help="every run in the manifest and its coverage")
    runs.set_defaults(function=command_runs)

    reference = subcommands.add_parser(
        "reference", help="choose which run counts as ground truth")
    reference.add_argument("run", type=int)
    reference.set_defaults(function=command_reference)

    compare = subcommands.add_parser(
        "compare", help="where two runs disagree")
    compare.add_argument("run_a", type=int)
    compare.add_argument("run_b", type=int)
    compare.set_defaults(function=command_compare)

    status = subcommands.add_parser("status", help="how much is done")
    status.set_defaults(function=command_status)

    bench = subcommands.add_parser(
        "bench", help="time the detector on this machine, comparably")
    bench.add_argument("what", choices=("build", "run", "report"),
                       help="build a corpus / time this machine / read "
                            "the results back")
    bench.add_argument("--corpus", default=str(config.DATA_DIR / "bench"),
                       help="the corpus directory (copy it between machines)")
    bench.add_argument("--out",
                       default=str(Path(__file__).resolve().parent.parent
                                   / "results" / "benchmarks"),
                       help="where result JSON files live")
    bench.add_argument("--size", type=int, default=250,
                       help="frames in the corpus (build only)")
    bench.add_argument("--seed", type=int, default=20260912)
    bench.add_argument("--model", default=None,
                       help=f"detector to time (default {config.DETECTOR})")
    bench.add_argument("--device", default=None,
                       help="cpu | mps | cuda:0 (default: let torch choose)")
    bench.add_argument("--threads", type=int, default=None)
    bench.add_argument("--min-seconds", type=float,
                       default=bench_module.MIN_SECONDS,
                       help="keep repeating the corpus until this much time "
                            "has passed, so the machine reaches a steady "
                            "thermal state")
    bench.add_argument("--no-verify", action="store_true",
                       help="skip the corpus checksum (not recommended)")
    bench.set_defaults(function=command_bench)

    return parser


def main(argv=None):
    options = build_parser().parse_args(argv)
    return options.function(options)


if __name__ == "__main__":
    sys.exit(main())
