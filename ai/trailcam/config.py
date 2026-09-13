"""Every path, threshold and weight, in one file.

`design.md` gives a reason for this that is worth repeating: if the numbers
live next to the logic, then tuning a threshold means editing logic, and
nobody can see at a glance what the program was actually configured to do.
So they all live here, each one with the reason it has that value.

Anything below that is a guess rather than a measurement says so.
"""

import os

from pathlib import Path


# ------------------------------------------------------------
# Where things are
# ------------------------------------------------------------

# The photo library, exactly as sync/sync_cameras.py leaves it:
#   <PHOTO_ROOT>/<camera>/<YYYY-MM-DD>/training/train_<HHMMSS>_<mmm>.jpg
# Read-only.  Nothing in this package ever writes inside it.
PHOTO_ROOT = Path(os.environ.get("WILDLIFE_PHOTOS",
                                 Path.home() / "wildlifecam-photos"))

# Everything derived lands here, and nothing else does, so `rm -rf` on this
# directory costs a rerun and never a photograph.
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

MANIFEST = DATA_DIR / "manifest.sqlite"
CROP_DIR = DATA_DIR / "crops"

# Downloaded model weights (~280 MB for MDv5a).  Gitignored.
MODEL_DIR = Path(__file__).resolve().parent.parent / "models"


# ------------------------------------------------------------
# Which detector
# ------------------------------------------------------------

# A name the `megadetector` package knows how to fetch for itself.  The
# ones worth knowing about, with the package's own relative speed figures:
#
#   MDV5A           281 MB, speed 1.0   the v5 workhorse; the model most
#                                       camera-trap papers are talking about
#   md1000-redwood  268 MB, speed 1.0   the v6 flagship, same speed as v5a
#   md1000-cedar     ?  MB, speed 2.0
#   md1000-larch     ?  MB, speed 2.4
#   md1000-spruce    14 MB, speed 12.7  the small fast one
#
# Default to MDV5A because it is the one with years of published behaviour
# behind it, so our numbers can be compared with everyone else's.
#
# Measured here, on the 8-core laptop, over the 5,459 training frames:
# md1000-spruce runs at 10 frames a second (nine minutes for the archive)
# and MDV5A at roughly a tenth of that (a few hours -- an overnight job).
# Spruce is a fine way to get a first answer in ten minutes.  It is not the
# one to quote: on this archive it calls the pile of cereal put out for the
# crows an animal, at 0.86, in frame after frame, where MDV5A finds nothing
# at all (ai/results/2026-09-12-first-megadetector-pass.md).
#
# What that does NOT show is that v5 beats v6.  Spruce is the smallest and
# fastest of the v6 family; md1000-redwood is the flagship, and at 268 MB
# and speed 1.0 it costs exactly what MDV5A costs to run.  It has never
# been tried here, and the published comparisons tend to put it ahead of
# v5a.  So the default below is convention, not evidence -- and settling it
# is precisely milestone E4, "Rivals", in ../evaluation-design.md.
#
# Switching is one word here, and the manifest records per frame which
# detector saw it, so a mixed database stays honest -- `report` says so
# out loud if it finds one.
DETECTOR = os.environ.get("TRAILCAM_DETECTOR", "MDV5A")

# MegaDetector reports every box it can imagine, most of them nonsense at
# 0.02 confidence.  Keeping them all would make the database enormous and
# tell us nothing.  0.05 is the conventional floor and is well below any
# threshold we go on to reason about.
MIN_STORED_CONFIDENCE = 0.05

# Torch on a laptop: leave a core for the desktop, or an overnight run makes
# the machine unpleasant to use in the morning.
THREADS = max(1, (os.cpu_count() or 4) - 1)


# ------------------------------------------------------------
# The confidence split  (evaluation-design.md, "Ground truth")
# ------------------------------------------------------------
#
# The plan is not "MegaDetector is right".  The plan is:
#
#   confident animal   -> believe it
#   confident empty    -> believe it
#   the muddy middle   -> a person looks, and there are only a few hundred
#
# These two numbers are the whole idea, which is why they are the two most
# important constants in this package.

# At or above this, treat an animal box as ground truth.
ANIMAL_TRUTH = 0.8

# If the best animal box in the frame is below this, treat the frame as
# genuinely empty.  Between the two is the uncertain band.
EMPTY_TRUTH = 0.1

# People are not wildlife, and a Scout walking past is a different kind of
# frame from an empty patio -- the replay in E2 must not count it as either.
# Same threshold as animals, for the same reason.
PERSON_TRUTH = 0.8


# ------------------------------------------------------------
# Which frames may be labelled automatically at all
# ------------------------------------------------------------
#
# evaluation-design.md is blunt about this: MegaDetector is weakest on small
# distant animals and on night frames, which are exactly our hard cases.
# The honest response is to measure the agreement first and only then decide
# what to exclude.
#
# So the default here excludes nothing (0 = no light floor).  Once the
# check-the-checker sample in E1 has a number, raise it and write the number
# in this comment.  A guess put here today would be indistinguishable from a
# measurement six months from now, which is the failure this whole document
# set exists to avoid.
AUTO_TRUTH_MIN_LUMA = 0.0

# The bursts themselves already refuse to record below mean_luma 40
# (RECORD_MIN_LUMA in step8), and daylight on the patio measures about 120.
# Frames are bucketed by this for the per-light breakdown the metrics ask
# for, which is reporting, not policy.
DUSK_LUMA = 70.0


# ------------------------------------------------------------
# Crops
# ------------------------------------------------------------

# Crops exist for two later jobs: they are what a person flips through when
# hand-labelling the uncertain band, and what SpeciesNet gets fed in M3.
# Written for animal and uncertain boxes only -- never for people.  Scout
# selfies are the one thing here we deliberately do not keep on disk.
WRITE_CROPS = True

# A tight box on a distant animal is unidentifiable even to a person, so
# pad it.  Fraction of the box's own size, added on each side.
CROP_MARGIN = 0.15

# Skip crops smaller than this on the long side: below it there is nothing
# for anyone to see, and thousands of 12 px thumbnails just cost inodes.
CROP_MIN_PIXELS = 48


# ------------------------------------------------------------
# Housekeeping
# ------------------------------------------------------------

# How often the pass reports progress and commits.  Small enough that an
# overnight run killed at 3am loses seconds of work, large enough that
# SQLite is not the bottleneck.
COMMIT_EVERY = 25


def ensure_directories():
    """Make the derived-data directories.  Never touches PHOTO_ROOT."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    if WRITE_CROPS:
        CROP_DIR.mkdir(parents=True, exist_ok=True)
