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

## Index

- [2026-09-12 --- the first MegaDetector pass over the training bursts](2026-09-12-first-megadetector-pass.md)
  --- 5,459 frames, three cameras, three animals. Two misses explained, and
  the shadow rule vindicated.
