# Benchmarks

One JSON file per (machine, model, device), plus whatever write-ups the
numbers earn. `python3 -m trailcam bench report` reads them into a table.

## What is being measured, and why that

The pass we actually run: **decode a JPEG from disk, letterbox it, one
forward pass, non-maximum suppression, one image at a time.** Not
inference-only, not batched, not synthetic.

That choice matters. On a fast GPU the JPEG decode is a serious share of
the total, and a benchmark that skipped it would recommend hardware that
does not help. If batching later turns out to be worth it, that is a change
to the pipeline, and the benchmark should change with it --- not before.

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

## Before you trust any number

**Plug the laptop in.** Battery changes everything on both macOS and Linux,
by more than the difference between two models. Every result records
`on_battery`, the run prints a warning, and `bench report` flags the row ---
but the fix is to plug it in and run again, not to note it and move on.

**Close everything else.** The benchmark uses every core it is given.

**Run each model on each device you care about**, including `cpu` on the
fast machines. "How much does the GPU actually buy us" is the question, and
it needs both halves.
