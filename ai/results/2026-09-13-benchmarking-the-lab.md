# Which machine should run the pass

**Run:** 12--13 September 2026.
**Corpus:** `dc1aa80c0519897b` --- 250 frames, 118 MB, sha256-verified on both machines.
**Models:** MegaDetector v5a; MDv6-spruce on the ThinkPad only.
**Method:** [`benchmarks/README.md`](benchmarks/README.md). Raw results in
[`benchmarks/`](benchmarks/).

---

## The answer

| machine | device | mode | s/frame | a pass over the archive |
|---|---|---|---|---|
| Mac Studio (M4 Max) | mps | single | **0.072** | **7 minutes** |
| Mac Studio | mps | batch 8 + 4 loaders | 0.177 | 17 min |
| Mac Studio | mps | batch 8 | 0.182 | 17 min |
| Mac Studio | cpu | single | 0.362 | 34 min |
| ThinkPad (Core Ultra 7 258V) | cpu | single | 2.586 | 4 h 4 m |

"A pass over the archive" is 5,673 training frames at the measured rate.

**The Mac Studio is 36 times faster than the ThinkPad**, and the four-hour
overnight job becomes a seven-minute one. The archive currently grows by
about 900 frames a day across three cameras; that is 65 seconds of work on
the Mac against 39 minutes on the laptop. The nightly pass should run on
the Mac Studio, and it stops being a nightly pass at all --- it can run
whenever a sync finishes.

Two caveats on the ThinkPad number, both stated in its result files: it was
on battery, and its MDv5a figure comes from a single pass (250 frames at
2.586 s/frame is already ten minutes, past the three-minute floor). It is
still the machine that produced the real four-hour pass, and it measured
**2.586 against that run's 2.66** --- within 3%, which is the useful thing
it tells us: the E1 write-up's number describes what actually happened.

## The GPU is worth 5x over the same machine's CPU

0.072 against 0.362 on identical hardware. Worth knowing separately from
the machine comparison, because it means a Mac without a usable GPU path
would still beat the ThinkPad by 7x --- most of the win here is the machine,
not the accelerator.

## Batching made it worse, which was not the hypothesis

We expected batching to help on a GPU: batch-1 inference underuses the
device, and a serial decode loop leaves it idle between frames. Measured,
`--batch-size 8` is **2.5x slower** than our one-at-a-time loop, and adding
four loader workers barely moves it (0.177 against 0.182).

That result is not yet understood, and the write-up should not pretend
otherwise. Three candidate explanations:

1. **The batch path is not the same code path.** `--mode single` runs our
   own loop; `--mode batch` runs the library's `load_and_run_detector_batch`.
   On the ThinkPad, where the library forces batch size to 1, that path was
   already 6% slower than our loop. Some fixed overhead is in the pipeline
   itself, not in batching.
2. **Batched 1280x1280 convolutions may simply be bad on MPS** --- memory
   bandwidth, or an op falling back to CPU inside a batched graph.
3. **Measurement.** Batch mode times one long call and subtracts a
   separately-measured model load. If the batch call pays a cost our
   subtraction misses, the number is inflated. The MPS single run loaded in
   **45.6 seconds** against 2.6 for every later run --- a cold Metal shader
   cache, paid once per machine --- which is exactly the kind of hidden
   first-call cost that could contaminate a short batched run.

**The experiment that separates them is cheap:** run `--mode batch
--batch-size 1` on the Mac. If it lands near 0.072, the overhead is
batching. If it lands near 0.18, the overhead is the library's batch
pipeline on MPS and batching is innocent. Four minutes, and it turns three
hypotheses into one answer.

Until then: `detect.py` stays one frame at a time, which is also the mode
every machine can run.

## Do the machines agree with each other?

This is the half a timing table usually leaves out, and it came out better
than expected.

| comparison | frames differing | largest change | verdicts changed |
|---|---|---|---|
| Mac MPS vs Mac CPU | **0 of 250** | 0.0000 | 0 |
| Mac vs ThinkPad (both CPU) | 27 of 250 | 0.1020 | **0** |
| Mac MPS vs ThinkPad CPU | 27 of 250 | 0.1020 | **0** |

**The GPU and the CPU on the same machine agree exactly**, to the four
decimal places we record. Whatever MPS does differently, it is below our
recording precision.

Across architectures --- Apple silicon against Intel, Python 3.14 against
3.12 --- 27 frames of 250 differ. The differences are tiny:

```
0.102   0.102 vs 0.000   wildlifecam4__2026-08-23__train_161104_439.jpg
0.032   0.384 vs 0.416   wildlifecam12__2026-09-03__train_162436_300.jpg
0.018   0.226 vs 0.244   wildlifecam12__2026-08-31__train_182421_537.jpg
0.017   0.440 vs 0.423   wildlifecam4__2026-08-27__train_151841_265.jpg
```

The 0.102 at the top is an **artifact of our own comparison**, not a
disagreement: we only record boxes at 0.1 or above, so a box that scores
0.102 on one machine and 0.098 on the other reads as a 0.102 gap. The real
spread is the 0.032 below it.

**No frame changed its verdict on any comparison.** Nothing crossed the 0.8
animal threshold, which is the only difference that would change a
conclusion. Running the pass on a different machine is safe.

## Redwood did not run on the Mac

The MDv6 flagship failed there and produced no result. It loads and detects
correctly on the ThinkPad's CPU --- checked directly, `person 0.94` on a
corpus frame --- so this is not a broken weights file or a bad download; the
md5 matched on both machines.

The error text was not captured, so the cause is unknown. The obvious first
step is `--device cpu` on the Mac: if redwood works there, the problem is
MPS (an unsupported operation in the v6 architecture is the usual
suspect); if it fails there too, the problem is the Mac's install.

This matters beyond the benchmark. Redwood is the model we would need to
settle whether v5a or v6 is better on our own data --- the question
`config.DETECTOR` currently answers by convention rather than evidence.

## Notes for next time

- **The Mac Studio reports as an M4 Max**, 16 cores, 137 GB. Worth recording
  accurately; it was described as an M4 Ultra when the run was planned.
- **The first MPS run on a machine costs about 45 seconds of shader
  compilation.** One-off per machine, but it will look like a hang.
- **Decode is not yet the bottleneck**, even on the GPU: 0.005 s/frame on
  the Mac against 0.065 of inference. It would start to bind somewhere
  around 0.005 s/frame of inference, which no model here approaches.
- Still missing: **the CUDA box**, and any plugged-in number from the
  ThinkPad.
