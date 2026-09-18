#!/bin/bash
#
# Campsite: SD card -> laptop -> MegaDetector -> the best pictures.
#
# Everything here runs with no internet and no help.  It is the offline
# version of what we normally do at home against the NAS: copy the cards,
# look at every photograph with a research-grade detector, and put the
# good ones in a folder you can open.
#
# Before you leave home, once:
#
#     ./campsite.sh --check
#
# That is the whole preparation step.  It tells you what is missing while
# you can still fix it -- the Python environment, the detector weights,
# disk space.  At the campsite there is no way to download any of them.
#
# At the campsite, for each camera card:
#
#     ./campsite.sh                      # card in the reader, go
#     ./campsite.sh --card /media/you/rootfs
#     ./campsite.sh --skip-copy          # cards already copied, just detect
#
# It is safe to run again.  Copying skips files already copied, and the
# detector only looks at photographs it has not seen, so a run that is
# interrupted picks up where it stopped.
#
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PHOTOS="${WILDLIFE_PHOTOS:-$HOME/wildlifecam-photos}"   # where cards are copied to
VENV="$HERE/ai/.venv"
MODEL_DIR="$HERE/ai/models"
MODEL_FILE="md_v5a.0.1.pt"          # the model we trust; see ai/README.md
CARD=""
DO_COPY=1
DO_CHECK=0
TOP=40

while [ $# -gt 0 ]; do
  case "$1" in
    --check)      DO_CHECK=1 ;;
    --card)       CARD="$2"; shift ;;
    --skip-copy)  DO_COPY=0 ;;
    --photos)     PHOTOS="$2"; shift ;;
    --top)        TOP="$2"; shift ;;
    -h|--help)    sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "campsite: unknown option $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '   ok   %s\n' "$*"; }
bad()  { printf '   NO   %s\n' "$*"; PROBLEMS=$((PROBLEMS+1)); }
PROBLEMS=0

# ----------------------------------------------------------------------
# Preflight.  Runs every time; --check does only this.
# ----------------------------------------------------------------------
say "Checking this laptop can do the job"

[ -x "$VENV/bin/python" ] \
  && ok "Python environment ($VENV)" \
  || bad "no Python environment at $VENV -- run, with internet:
             cd ai && python3 -m venv .venv
             .venv/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
             .venv/bin/pip install megadetector"

if [ -x "$VENV/bin/python" ]; then
  if "$VENV/bin/python" -c "import megadetector, torch" 2>/dev/null; then
    ok "megadetector and torch import"
  else
    bad "megadetector or torch will not import -- reinstall as above"
  fi
fi

if [ -s "$MODEL_DIR/$MODEL_FILE" ]; then
  ok "detector weights ($MODEL_FILE, $(du -h "$MODEL_DIR/$MODEL_FILE" | cut -f1))"
else
  bad "no detector weights at $MODEL_DIR/$MODEL_FILE
         They are NOT downloaded automatically without internet.  Copy them
         from the NAS before leaving: /mnt/datasets/trailcam/models/"
fi

command -v rsync >/dev/null && ok "rsync" || bad "rsync is not installed"

# Do not create anything during the check: the free-space question can be
# answered from the nearest directory that does exist.
WHERE="$PHOTOS"
while [ ! -d "$WHERE" ] && [ "$WHERE" != "/" ]; do WHERE=$(dirname "$WHERE"); done
if [ -w "$WHERE" ]; then
  FREE=$(df -BG --output=avail "$WHERE" | tail -1 | tr -dc '0-9')
  NOTE=""; [ "$WHERE" != "$PHOTOS" ] && NOTE=" (will be created under $WHERE)"
  [ "${FREE:-0}" -ge 20 ] \
    && ok "photo directory $PHOTOS${NOTE} -- ${FREE}G free" \
    || bad "only ${FREE}G free for $PHOTOS -- a full card is about 16G"
else
  bad "cannot write to $WHERE, so $PHOTOS cannot be used"
fi

if [ "$DO_CHECK" = 1 ]; then
  say "$([ $PROBLEMS -eq 0 ] && echo 'Ready for the field.' || echo "$PROBLEMS thing(s) to fix BEFORE you leave.")"
  exit $([ $PROBLEMS -eq 0 ] && echo 0 || echo 1)
fi
[ $PROBLEMS -gt 0 ] && { say "Fix the $PROBLEMS problem(s) above first."; exit 1; }

# ----------------------------------------------------------------------
# 1. Copy the card.
# ----------------------------------------------------------------------
if [ "$DO_COPY" = 1 ]; then
  say "Copying the card"
  mkdir -p "$PHOTOS" || exit 1
  if [ -n "$CARD" ]; then
    "$HERE/sync/sync_sdcard.py" "$CARD" --dest "$PHOTOS" || exit 1
  else
    # No path given: let it find the card in the reader itself.
    "$HERE/sync/sync_sdcard.py" --dest "$PHOTOS" || exit 1
  fi
  echo
  echo "   The card has NOT been emptied.  Once you have seen the pictures"
  echo "   below, empty it for redeployment with:"
  echo "       ./sync/sync_sdcard.py ${CARD:-<card>} --dest $PHOTOS --wipe --checksum"
fi

# ----------------------------------------------------------------------
# 2. Index, then look at every photograph.
# ----------------------------------------------------------------------
export WILDLIFE_PHOTOS="$PHOTOS"
cd "$HERE/ai" || exit 1

say "Indexing what is on disk"
"$VENV/bin/python" -m trailcam scan --kind photo || exit 1

say "Looking at every new photograph (MegaDetector v5a)"
echo "   About 1.6 seconds each on this laptop, so a thousand photographs"
echo "   is roughly half an hour.  Ctrl-C stops cleanly; run again to resume."
"$VENV/bin/python" -m trailcam detect --kind photo || exit 1

# ----------------------------------------------------------------------
# 3. The pictures worth looking at.
# ----------------------------------------------------------------------
say "The best pictures"
"$VENV/bin/python" -m trailcam shortlist --kind photo --top "$TOP" || exit 1

say "Done"
echo "   Thumbnails and the ranked list are in ai/data/shortlist/."
echo "   Open ai/data/shortlist/index.html to look through them."
