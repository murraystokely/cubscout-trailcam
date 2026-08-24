#!/usr/bin/env bash
#
# export_mathematica_html.sh -- evaluate the Mathematica notebook and write a
# static HTML copy, so its results can be read in a web browser straight from
# GitHub without Mathematica installed.
#
# GitHub renders .ipynb files by itself, outputs and all, so the Jupyter
# notebook needs nothing.  It does not render .nb files at all, hence this.
#
# Requires an ACTIVATED Wolfram installation.  If you see
#
#     Your Wolfram product is not activated
#
# then run `wolframscript` once by hand and enter your activation key first.

set -euo pipefail

cd "$(dirname "$0")"

NB=step7_image_processing.nb
OUT=step7_image_processing.html

command -v wolframscript >/dev/null || {
    echo "wolframscript is not on PATH" >&2; exit 1; }
[ -f "$NB" ] || { echo "$NB not found" >&2; exit 1; }

echo "evaluating $NB (this opens a front end and takes a minute)..."

wolframscript -code "
  UsingFrontEnd[
    nb = NotebookOpen[FileNameJoin[{Directory[], \"$NB\"}]];
    NotebookEvaluate[nb, InsertResults -> True];
    NotebookSave[nb];
    Export[FileNameJoin[{Directory[], \"$OUT\"}], nb, \"HTML\"];
    NotebookClose[nb];
  ];
  Print[\"exported \", \"$OUT\"];
"

echo
echo "wrote $OUT and saved evaluated outputs back into $NB"
echo "commit both so the results are visible on GitHub."
