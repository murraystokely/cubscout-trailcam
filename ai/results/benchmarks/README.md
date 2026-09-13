# Benchmarks

One JSON file per (machine, model, device, mode), plus whatever write-ups
the numbers earn. `python3 -m trailcam bench report` reads them into a
table.

First write-up: [which machine should run the
pass](../2026-09-13-benchmarking-the-lab.md) --- the Mac Studio is 36x the
ThinkPad, batching on MPS made it *worse*, and no machine disagreed with
another about a single verdict.

## What is being measured, and why that

The pass we actually run: **decode a JPEG from disk, letterbox it, one
forward pass, non-maximum suppression, one image at a time.** Not
inference-only and not synthetic.

That choice matters. On a fast GPU the JPEG decode is a serious share of
the total, and a benchmark that skipped it would recommend hardware that
does not help.

### Two modes

`--mode single` (the default) is that one-at-a-time loop --- what
`detect.py` does today, and the only shape every machine can run, which
makes it the number they can all be compared on.

`--mode batch` measures the library's batch pipeline instead. Three knobs,
fixing three different problems:

| flag | what it does |
|---|---|
| `--batch-size` | images through the GPU at once. **The library forces this to 1 on CPU**, which is why we never wrote it: the only machine we had was a CPU laptop |
| `--loader-workers` | decode in parallel with inference rather than between inferences. Batching fills the device; this stops it starving |
| `--n-cores` | CPU worker processes. Ignored on a GPU |

Measured on the ThinkPad (8 cores, CPU only, on battery --- so treat the
absolute numbers as indicative, and the ratios as the point):

| mode | s/frame |
|---|---|
| single | 0.089 |
| batch, no loaders | 0.094 |
| batch, 4 loader workers | 0.102 |

So on this machine the library's batch pipeline is slightly *worse* than our
own loop, and parallel decode is worse again --- about 15%. The direction
matches the theory: where inference is the bottleneck, decode workers steal
cores from it. The size says the effect is small.

**An earlier draft of this file claimed 2.5x worse, and that was wrong.**
It came from a run with `--min-seconds 20`, which did a single pass of 250
frames and charged the whole process-pool startup to it. Given a proper
measured window the startup amortises away. The rule that says "measure long
enough to get hot" caught its own author out inside a day, which is the best
argument for it this document is going to get.

None of this says anything about a GPU, where inference stops being the
bottleneck and the loader workers have idle cores to use. That is the
measurement we cannot make here --- the library forces batch size to 1 on
CPU --- and it is the reason the flags exist.

Batch mode is reported as its own row, never as a replacement for the
baseline. Two caveats it carries: the library reloads the model on every
call, so the corpus is repeated *inside* one call and the separately-timed
model load is subtracted --- an approximation, stated in the result; and
there are no per-pass times, so **no throttle signal**. Run it twice if you
need to know whether the machine got hot.

Batching can also change the answers slightly, because images are
letterboxed to a common size within a batch. That is what the findings
digest is for.

Four rules, each one there because breaking it is how benchmarks lie:

**The same bytes everywhere.** The corpus is a fixed set of frames, chosen
once with a seed, with a sha256 for every file. `bench run` verifies all of
them before timing anything and refuses to report a number from a corpus
that does not match.

**Warm up, then measure.** The first frames carry lazy initialisation,
kernel compilation and a cold page cache. Eight frames are discarded.

**Measure long enough to get hot.** A machine that finishes in twenty
seconds reports its burst clock, not the throughput of an overnight pass.
The corpus repeats until at least 180 seconds have elapsed, and every pass
time is recorded: if the last pass is slower than the first, that machine
throttles, and `throttle_ratio` says so.

**Check the answers, not just the clock.** A benchmark that only times
things will measure a broken install at record speed. Every run records
what the model found, so machines are compared on agreement as well as on
seconds. Float arithmetic genuinely differs between CPU, MPS and CUDA; the
question worth asking is whether it differs enough to move a frame across
the 0.8 animal threshold, and `bench report` answers exactly that.

## The corpus

250 frames, stratified by camera in proportion to the archive --- the
cameras shoot at 2028x1520 and 1520x1140, and decode time follows
resolution, so a corpus from one camera would measure the wrong mix. Every
frame the reference run called an animal or a person is forced in, because
those are the frames the correctness check has something to say about.

```
corpus dc1aa80c0519897b: 250 frames, 118 MB
{'animal': 28, 'empty': 169, 'person': 36, 'uncertain': 17}
```

Rebuild it with `bench build`; the seed makes it reproducible, and the
`corpus_id` in every result says which one a number came from.

## Running it on another machine

The corpus and the weights are the two big files. Copying both beats
re-downloading 280 MB of weights over a slow link.

```bash
# on the machine that has the archive
rsync -a ai/data/bench/ lab-machine:~/trailcam-bench/
rsync -a ai/models/     lab-machine:~/trailcam-models/
```

Then on the machine under test:

```bash
git clone git@github.com:murraystokely/cubscout-trailcam.git
cd cubscout-trailcam/ai
python3 -m venv .venv
```

**Apple silicon (M3 MacBook, M4 Ultra Mac Studio).** The default wheel
carries MPS; there is nothing extra to install.

```bash
.venv/bin/pip install torch torchvision megadetector
cp ~/trailcam-models/*.pt models/

.venv/bin/python -m trailcam bench run --corpus ~/trailcam-bench \
    --model MDV5A --device mps
.venv/bin/python -m trailcam bench run --corpus ~/trailcam-bench \
    --model MDV5A --device cpu      # worth having both
```

**NVIDIA / CUDA.** Install the CUDA build of torch first, or pip quietly
gives you the CPU one and the benchmark measures the wrong thing:

```bash
.venv/bin/pip install torch torchvision \
    --index-url https://download.pytorch.org/whl/cu124
.venv/bin/pip install megadetector
cp ~/trailcam-models/*.pt models/

.venv/bin/python -m trailcam bench run --corpus ~/trailcam-bench \
    --model MDV5A --device cuda:0
```

The result prints `cuda_available` and the GPU name in its machine block.
If that says `false`, the torch install is wrong and the number is not a
GPU number.

Copy the JSON files back into this directory, then:

```bash
python3 -m trailcam bench report
```

## What to record, and what to trust

**Record every run, with the conditions it ran under.** That includes the
bad ones. A result taken on battery is not this machine's throughput and
must never be quoted as such --- but it is the only measurement we have of
what a throttled laptop does, and it describes a real four-hour pass that
actually happened. Every result carries `on_battery`, the run prints a
warning, `bench report` flags the row, and `--note` takes anything the
harness cannot see for itself:

```bash
.venv/bin/python -m trailcam bench run --device cpu \
    --note "on battery, 60% charge, lid open"
```

So: keep it, mark it, do not quote it. Deleting a labelled result throws
away evidence that could not have misled anyone who read the row.

**Plug the laptop in for the number you intend to quote.** Battery changes
the answer by more than the difference between two models.

**Close everything else.** The benchmark uses every core it is given.

**Run each model on each device you care about**, including `cpu` on the
fast machines. "How much does the GPU actually buy us" is the question, and
it needs both halves.

## Where the results live

- **`*.json` in this directory** --- one per (machine, model, device,
  mode), committed. They are small, they are findings, and they are what
  `bench report` reads.
- **A write-up in [`../`](../)** once there is something to say ---
  `YYYY-MM-DD-something.md`, same convention as every other analysis. A
  table of numbers is data; which machine should run the nightly pass is a
  finding, and findings get prose.
