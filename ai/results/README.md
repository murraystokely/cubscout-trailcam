# Results

One file per analysis, named `YYYY-MM-DD-what-it-was.md`, so the directory
sorts into a history.

These are **findings, not documentation**: what we ran, on which data, with
which code, and what came out. They are written to be read a season later by
someone who no longer remembers the run --- so each one states its inputs
and how to reproduce it, and says plainly what it does *not* establish.

The distinction from the rest of the repository:

| | |
|---|---|
| `ai/design.md`, `ai/evaluation-design.md` | what we intend to build, and why |
| `ai/README.md`, `docs/` | how to run things |
| `ai/results/` | what happened when we did |

A number quoted anywhere else in the repository --- a threshold in `step8`, a
claim in a design document --- should be traceable to a file in here. That is
the whole point: `evaluation-design.md` opens by admitting that not one of
the camera's thresholds was chosen by measuring a real camera in real woods.
This directory is where that gets fixed, one measurement at a time.

## Two kinds of record

**Analyses** are files in this directory: prose, dated, each stating its
inputs and what it does not establish.

**Raw results** live in subdirectories next to them ---
[`benchmarks/`](benchmarks/) so far --- as machine-readable files a tool can
read back. Those accumulate; an analysis is written when they add up to
something worth saying. A table of timings is data. "The Mac Studio should
run the nightly pass, and here is what that costs" is a finding, and
findings get prose.

Record raw results even when the conditions were poor, as long as the
conditions are recorded with them. A benchmark taken on battery is not that
machine's throughput and must not be quoted as such, but it is a real
measurement of a real run, and the file says `on_battery: true` where
nobody can miss it. Deleting it loses evidence; mislabelling it would be
the actual sin.

## Index

- [2026-09-12 --- the first MegaDetector pass over the training bursts](2026-09-12-first-megadetector-pass.md)
  --- 5,459 frames, three cameras, three animals. Two misses explained, and
  the shadow rule vindicated.
- [2026-09-13 --- which machine should run the pass](2026-09-13-benchmarking-the-lab.md)
  --- the Mac Studio is 36x the ThinkPad, batching made it worse (and it
  is batching itself, not the pipeline), redwood's failure was a Python
  3.14 error message, and the machines disagree only at the threshold line.
- [2026-09-13 --- the first passes on the Mac Studio](2026-09-13-first-passes-on-the-mac.md)
  --- the archive works from the NAS and the ThinkPad's numbers reproduce;
  MDv6-redwood against v5a on our frames: redwood sees people better and
  sunlit paving worse, so v5a stays the reference.
- [2026-09-13 --- what is in the photographs](2026-09-13-what-is-in-the-photographs.md)
  --- MegaDetector over all 41,175 frames in 56 minutes: 1,202 animal
  photographs in 150 visits: about 125 crows, 9 squirrels, 15 small brown
  birds, and a ranking that hid every one of the squirrels until a person
  looked. wildlifecam12 has no animal at 0.8 in 7,006 photographs, and
  squirrels at 0.13.
- [2026-09-19 --- the September cards, with the model we trust](2026-09-19-the-september-cards-with-mdv5a.md)
  --- 16,921 new frames in 22 minutes; 90 animal visits on five cameras,
  squirrels now as common as crows; wildlifecam13 is pointed at a feeder.
- [`shortlists/`](shortlists/) --- the ranked "best animal pictures" list
  each pass produced, one CSV per run.
- [`benchmarks/`](benchmarks/) --- how long the detector takes on each
  machine in the lab, on one fixed corpus, with the rules that make the
  numbers comparable.
