"""Timing the detector on whatever machine you have.

The question this answers is not "which computer is fastest", which nobody
needs a program for.  It is: **the archive grows by about 300 frames a day
per camera, and a pass over it currently takes four hours -- which machine
should be doing that, and does moving it somewhere else change the
answers?**

So the benchmark measures the pass we actually run: decode a JPEG from
disk, letterbox it, one forward pass, non-maximum suppression, one image at
a time.  Not inference-only, not batched, not synthetic.  On a fast GPU the
JPEG decode turns out to be a serious share of the total, and a benchmark
that quietly skipped it would recommend hardware that does not help.

Four rules, each one there because breaking it is how benchmarks lie:

**The same bytes everywhere.** The corpus is a fixed set of frames, chosen
once with a seed, shipped as a directory with a sha256 for every file.  A
machine that cannot verify the corpus refuses to report a number.

**Warm up, then measure.** The first inference carries lazy initialisation,
kernel compilation and a cold page cache.  Discarded, always.

**Measure long enough to get hot.** A laptop that finishes in twenty
seconds reports its burst clock, not the throughput you would get from an
overnight pass.  So the corpus is repeated until a minimum wall time has
elapsed, and the per-pass times are all reported: if the last pass is
slower than the first, that machine throttles, and you want to know that
before choosing it.

**Check the answers, not just the clock.** A benchmark that only times
things will happily measure a broken install at record speed.  Every run
also records what the model actually found, so two machines can be compared
on agreement as well as on seconds.  Float arithmetic differs between CPU,
MPS and CUDA -- the interesting question is whether it differs enough to
change a verdict, and this is how you find out.
"""

import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import time

from datetime import datetime, timezone
from pathlib import Path

from . import config


CORPUS_FILE = "corpus.json"

# How long the measured window should be, if the machine is quick enough to
# get there.  Three minutes is long enough for a laptop to reach a steady
# thermal state and short enough that nobody minds running it.
MIN_SECONDS = 180.0
MAX_PASSES = 20

# Discarded frames at the start: lazy init, kernel compile, cold cache.
WARMUP_FRAMES = 8


# ------------------------------------------------------------
# Building the corpus
# ------------------------------------------------------------

def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def build_corpus(database, destination, size=250, seed=20260912):
    """Choose a corpus and copy it somewhere self-contained.

    Stratified by camera, in proportion to the archive, because the cameras
    shoot at different resolutions (2028x1520 and 1520x1140) and decode
    time follows resolution.  A corpus drawn from one camera would measure
    the wrong mix.

    Every frame the reference run called an animal or a person is then
    forced in, however the sample fell.  Those are the frames the
    correctness check has something to say about -- 5,000 pictures of an
    empty patio agreeing across two machines proves very little.
    """
    import random

    destination = Path(destination)
    frames_directory = destination / "frames"
    frames_directory.mkdir(parents=True, exist_ok=True)

    rows = database.execute(
        """SELECT f.id, f.path, f.camera, f.mean_luma, t.label
             FROM frames f JOIN truth t ON t.frame_id = f.id
            WHERE t.label NOT IN ('not yet seen', 'unreadable')
            ORDER BY f.path"""
    ).fetchall()

    if not rows:
        raise SystemExit("Nothing to build a corpus from: run `scan` and "
                         "`detect` first, and set a reference run.")

    interesting = [r for r in rows if r["label"] in ("animal", "person")]
    ordinary = [r for r in rows if r["label"] not in ("animal", "person")]

    # Proportional by camera, so the resolution mix matches the archive.
    by_camera = {}
    for row in ordinary:
        by_camera.setdefault(row["camera"], []).append(row)

    generator = random.Random(seed)
    remaining = max(0, size - len(interesting))
    total_ordinary = len(ordinary)

    chosen = list(interesting)
    for camera, camera_rows in sorted(by_camera.items()):
        share = round(remaining * len(camera_rows) / total_ordinary)
        chosen.extend(generator.sample(camera_rows,
                                       min(share, len(camera_rows))))

    chosen.sort(key=lambda r: r["path"])          # deterministic order

    manifest = {
        "name": destination.name,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed": seed,
        "requested_size": size,
        "frames": [],
    }

    print(f"Copying {len(chosen)} frames into {frames_directory}...")

    for row in chosen:
        source = config.PHOTO_ROOT / row["path"]
        # Flatten: <camera>__<day>__<name>.jpg, so the corpus is one
        # directory that can be tarred, copied and verified anywhere.
        flat = row["path"].replace("/training/", "__").replace("/", "__")
        target = frames_directory / flat

        if not target.exists():
            shutil.copy2(source, target)

        manifest["frames"].append({
            "file": flat,
            "origin": row["path"],
            "camera": row["camera"],
            "label": row["label"],
            "mean_luma": row["mean_luma"],
            "bytes": target.stat().st_size,
            "sha256": _sha256(target),
        })

    manifest["total_bytes"] = sum(f["bytes"] for f in manifest["frames"])
    manifest["cameras"] = sorted({f["camera"] for f in manifest["frames"]})
    manifest["labels"] = {
        label: sum(1 for f in manifest["frames"] if f["label"] == label)
        for label in sorted({f["label"] for f in manifest["frames"]})}

    # The corpus identifies itself by the hash of its own file list, so a
    # result can say exactly what it was measured on.
    manifest["corpus_id"] = hashlib.sha256(
        json.dumps([f["sha256"] for f in manifest["frames"]],
                   sort_keys=True).encode()).hexdigest()[:16]

    with open(destination / CORPUS_FILE, "w") as handle:
        json.dump(manifest, handle, indent=1)

    print(f"  corpus {manifest['corpus_id']}: {len(chosen)} frames, "
          f"{manifest['total_bytes'] / 1e6:.0f} MB")
    print(f"  {manifest['labels']}")
    print(f"\nCopy the whole {destination.name}/ directory to the machine "
          f"under test.")

    return manifest


def load_corpus(directory, verify=True):
    """Read a corpus and check it is the one it claims to be."""
    directory = Path(directory)

    with open(directory / CORPUS_FILE) as handle:
        manifest = json.load(handle)

    if verify:
        print(f"Verifying {len(manifest['frames'])} files...")
        for entry in manifest["frames"]:
            path = directory / "frames" / entry["file"]
            if not path.exists():
                raise SystemExit(f"Corpus is incomplete: {entry['file']} "
                                 f"is missing.")
            if _sha256(path) != entry["sha256"]:
                raise SystemExit(
                    f"Corpus is corrupt: {entry['file']} does not match its "
                    f"sha256. A timing from it would not be comparable.")
        print("  every file matches its sha256.")

    return manifest


# ------------------------------------------------------------
# What machine is this?
# ------------------------------------------------------------

def _command(*arguments):
    try:
        return subprocess.check_output(arguments, stderr=subprocess.DEVNULL,
                                       text=True).strip()
    except Exception:                               # noqa: BLE001
        return None


def _memory_gb():
    if platform.system() == "Darwin":
        total = _command("sysctl", "-n", "hw.memsize")
        return round(int(total) / 1e9, 1) if total else None
    try:
        with open("/proc/meminfo") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) * 1024 / 1e9, 1)
    except OSError:
        pass
    return None


def _processor():
    if platform.system() == "Darwin":
        return _command("sysctl", "-n", "machdep.cpu.brand_string")
    try:
        with open("/proc/cpuinfo") as handle:
            for line in handle:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or None


def _on_battery():
    """Laptops throttle hard on battery, so a number without this is noise."""
    if platform.system() == "Darwin":
        power = _command("pmset", "-g", "batt")
        if power:
            return "Battery Power" in power
        return None
    for supply in sorted(Path("/sys/class/power_supply").glob("*")):
        try:
            if (supply / "type").read_text().strip() == "Mains":
                return (supply / "online").read_text().strip() == "0"
        except OSError:
            continue
    return None


def describe_machine(device=None, threads=None):
    """Everything needed to read a timing six months from now."""
    description = {
        "host": platform.node(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": _processor(),
        "cpu_count": os.cpu_count(),
        "memory_gb": _memory_gb(),
        "python": platform.python_version(),
        "on_battery": _on_battery(),
        "threads": threads,
    }

    try:
        import torch

        description["torch"] = torch.__version__
        description["cuda_available"] = torch.cuda.is_available()
        if torch.cuda.is_available():
            description["gpu"] = torch.cuda.get_device_name(0)
            description["cuda_version"] = torch.version.cuda
        description["mps_available"] = bool(
            getattr(torch.backends, "mps", None)
            and torch.backends.mps.is_available())
    except Exception:                               # noqa: BLE001
        pass

    try:
        from importlib.metadata import version

        description["megadetector"] = version("megadetector")
    except Exception:                               # noqa: BLE001
        pass

    return description


# ------------------------------------------------------------
# The measurement
# ------------------------------------------------------------

def run_benchmark(corpus_directory, model=None, device=None, threads=None,
                  min_seconds=MIN_SECONDS, max_passes=MAX_PASSES,
                  verify=True):
    """Time one model on one device over the corpus.  Returns a result dict."""
    from megadetector.visualization.visualization_utils import load_image

    from .detector import MegaDetector

    corpus_directory = Path(corpus_directory)
    manifest = load_corpus(corpus_directory, verify=verify)
    files = [corpus_directory / "frames" / f["file"]
             for f in manifest["frames"]]

    name = model or config.DETECTOR

    print(f"\nLoading {name}"
          + (f" on {device}" if device else " (auto device)") + "...")

    load_started = time.perf_counter()
    detector = MegaDetector(model=name, threads=threads, device=device)
    load_seconds = time.perf_counter() - load_started

    print(f"  loaded in {load_seconds:.1f}s on device {detector.device}, "
          f"{detector.threads} torch threads")

    # --- warm up, and throw it away -----------------------------------
    for path in files[:WARMUP_FRAMES]:
        detector.model.generate_detections_one_image(
            load_image(str(path)), str(path),
            detection_threshold=config.MIN_STORED_CONFIDENCE)

    # --- measure -------------------------------------------------------
    passes = []
    decode_times = []
    inference_times = []
    findings = {}

    print(f"  measuring: repeating {len(files)} frames until "
          f"{min_seconds:.0f}s have passed (at most {max_passes} passes)")

    started = time.perf_counter()
    while True:
        pass_started = time.perf_counter()

        for path in files:
            decode_started = time.perf_counter()
            image = load_image(str(path))
            decoded = time.perf_counter()

            result = detector.model.generate_detections_one_image(
                image, str(path),
                detection_threshold=config.MIN_STORED_CONFIDENCE)
            finished = time.perf_counter()

            decode_times.append(decoded - decode_started)
            inference_times.append(finished - decoded)

            # Last pass wins; they should all be identical anyway, and if
            # they are not that is worth discovering.
            findings[path.name] = [
                {"category": d["category"], "conf": round(d["conf"], 4)}
                for d in result.get("detections", [])
                if d["conf"] >= 0.1]

        passes.append(time.perf_counter() - pass_started)
        elapsed = time.perf_counter() - started

        print(f"    pass {len(passes)}: {passes[-1]:.1f}s "
              f"({len(files) / passes[-1]:.2f} frames/s)")

        if elapsed >= min_seconds or len(passes) >= max_passes:
            break

    per_frame = [d + i for d, i in zip(decode_times, inference_times)]
    per_frame.sort()

    def percentile(values, fraction):
        return values[min(len(values) - 1, int(len(values) * fraction))]

    result = {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "corpus": {
            "id": manifest["corpus_id"],
            "frames": len(files),
            "bytes": manifest["total_bytes"],
        },
        "model": {
            "name": name,
            "weights": Path(detector.weights).name,
            "device": detector.device,
        },
        "machine": describe_machine(device=detector.device,
                                    threads=detector.threads),
        "timing": {
            "model_load_seconds": round(load_seconds, 2),
            "passes": [round(p, 3) for p in passes],
            "frames_measured": len(per_frame),
            "seconds_per_frame": round(statistics.median(per_frame), 4),
            "frames_per_second": round(1.0 / statistics.median(per_frame), 2),
            "p50_seconds": round(percentile(per_frame, 0.50), 4),
            "p90_seconds": round(percentile(per_frame, 0.90), 4),
            "decode_seconds_median": round(statistics.median(decode_times), 4),
            "inference_seconds_median": round(
                statistics.median(inference_times), 4),
            # If the last pass is slower than the first, the machine got hot.
            "throttle_ratio": round(passes[-1] / passes[0], 3),
        },
        "findings": findings,
        "findings_digest": hashlib.sha256(
            json.dumps(findings, sort_keys=True).encode()).hexdigest()[:16],
    }

    _print_result(result)
    return result


def _print_result(result):
    timing = result["timing"]
    machine = result["machine"]

    print(f"\n  {result['model']['name']} on {result['model']['device']} "
          f"({machine['host']})")
    print(f"    {timing['seconds_per_frame']:.3f} s/frame  "
          f"({timing['frames_per_second']:.2f} frames/s)")
    print(f"    decode {timing['decode_seconds_median']:.3f}s + "
          f"inference {timing['inference_seconds_median']:.3f}s")
    print(f"    p90 {timing['p90_seconds']:.3f}s, "
          f"throttle ratio {timing['throttle_ratio']:.2f}")

    if machine.get("on_battery"):
        print("    WARNING: this machine is on battery. Plug it in and "
              "run again; the number above is not comparable.")


def save_result(result, directory):
    """One file per (machine, model, device), named so it cannot collide."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    name = (f"{result['machine']['host']}"
            f"-{result['model']['name']}"
            f"-{result['model']['device'].replace(':', '')}.json"
            ).replace("/", "-")

    path = directory / name
    with open(path, "w") as handle:
        json.dump(result, handle, indent=1)

    print(f"\n  written to {path}")
    return path


# ------------------------------------------------------------
# Reading the results back
# ------------------------------------------------------------

def summarise(paths):
    """A table of every result, and whether they agree with each other."""
    results = []
    for path in paths:
        with open(path) as handle:
            results.append(json.load(handle))

    if not results:
        print("No results to summarise.")
        return

    corpora = {r["corpus"]["id"] for r in results}
    if len(corpora) > 1:
        print("These results are NOT comparable: they used different "
              f"corpora ({', '.join(sorted(corpora))}).\n")

    results.sort(key=lambda r: r["timing"]["seconds_per_frame"])
    fastest = results[0]["timing"]["seconds_per_frame"]

    print(f"{'machine':22s} {'model':16s} {'device':8s} "
          f"{'s/frame':>8s} {'frames/s':>9s} {'decode':>7s} {'infer':>7s} "
          f"{'throt':>6s} {'vs best':>8s}")
    print("-" * 100)

    for result in results:
        timing = result["timing"]
        machine = result["machine"]
        battery = " (battery!)" if machine.get("on_battery") else ""

        print(f"{machine['host'][:22]:22s} "
              f"{result['model']['name'][:16]:16s} "
              f"{result['model']['device'][:8]:8s} "
              f"{timing['seconds_per_frame']:8.3f} "
              f"{timing['frames_per_second']:9.2f} "
              f"{timing['decode_seconds_median']:7.3f} "
              f"{timing['inference_seconds_median']:7.3f} "
              f"{timing['throttle_ratio']:6.2f} "
              f"{timing['seconds_per_frame'] / fastest:7.1f}x"
              f"{battery}")

    # --- do they agree? ------------------------------------------------
    print("\nDo they find the same things?")
    print("-----------------------------")

    by_model = {}
    for result in results:
        by_model.setdefault(result["model"]["name"], []).append(result)

    for model, group in sorted(by_model.items()):
        if len(group) < 2:
            continue

        reference = group[0]
        print(f"\n  {model}, against {reference['machine']['host']} "
              f"on {reference['model']['device']}:")

        for other in group[1:]:
            same_digest = (other["findings_digest"]
                           == reference["findings_digest"])
            differences = _compare_findings(reference["findings"],
                                            other["findings"])

            print(f"    {other['machine']['host']} on "
                  f"{other['model']['device']}: "
                  + ("identical" if same_digest else
                     f"{differences['frames_differing']} of "
                     f"{differences['frames']} frames differ, "
                     f"largest confidence change "
                     f"{differences['max_delta']:.3f}"))

            if not same_digest and differences["verdicts_changed"]:
                print(f"      {differences['verdicts_changed']} of them "
                      f"cross the {config.ANIMAL_TRUTH} animal threshold "
                      f"-- those are real disagreements, not float noise")


def _compare_findings(first, second):
    """How far apart are two machines' answers, in terms that matter?"""
    frames = sorted(set(first) & set(second))
    differing = 0
    max_delta = 0.0
    verdicts_changed = 0

    for name in frames:
        def best_animal(boxes):
            return max((b["conf"] for b in boxes if b["category"] == "1"),
                       default=0.0)

        a, b = best_animal(first[name]), best_animal(second[name])
        delta = abs(a - b)

        if delta > 0:
            differing += 1
            max_delta = max(max_delta, delta)

        # The only difference anyone cares about: did the label change?
        if (a >= config.ANIMAL_TRUTH) != (b >= config.ANIMAL_TRUTH):
            verdicts_changed += 1

    return {
        "frames": len(frames),
        "frames_differing": differing,
        "max_delta": max_delta,
        "verdicts_changed": verdicts_changed,
    }
