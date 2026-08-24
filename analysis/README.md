# Taking the camera program apart

Two notebooks that walk through
[`../step7_ai_motion_detection.py`](../step7_ai_motion_detection.py) one step at
a time, showing each piece working on real photographs the cameras actually
took. Same pipeline, same pictures, two languages.

| | opens in | readable on GitHub |
| --- | --- | --- |
| [`step7_image_processing.ipynb`](step7_image_processing.ipynb) | Jupyter / VS Code | **yes** — GitHub renders notebooks with their outputs |
| [`step7_image_processing.nb`](step7_image_processing.nb) | Mathematica | only after running the export step below |

Start with the Jupyter one. Click it above and the whole thing — code,
explanation and every picture — reads in the browser with nothing installed.

## What they cover

Each step of the decision the camera makes four times a second:

1. the two camera streams, and why the rules only look at the small one
2. brightness, and the filter that stops it photographing the dark
3. blurring on purpose, to survive the camera shaking by a fraction of a pixel
4. the background model, and why it learns at two speeds
5. subtracting, and how the threshold sets itself on a noisy evening
6. cleaning up with open and close
7. connected pixels — finding the blob
8. shape: is it solid, or is it a grass stem?
9. the whole eight-rule checklist, run on three real frames
10. what the on-sensor AI is used for, and what it is deliberately not used for

Both end with a **Future work** section: statistical background models, using
colour to solve the cloud-shadow problem, optical flow, three-frame
differencing, motion vectors from the video encoder, and training a model of our
own on the frames the cameras are collecting now.

The Jupyter notebook reads its thresholds **out of `step7` itself** rather than
copying them, so it cannot quietly disagree with the code that is running in the
woods.

## The pictures

Everything in [`data/`](data) came off `wildlifecam4` on 23 August 2026, pointed
at a patio with cheerios on the ground to attract a bird. Nothing is staged and
nothing is simulated except two deliberately darkened frames, which say so.
[`data/index.json`](data/index.json) records where each one came from and what
the camera decided about it at the time.

There are no identifiable people in the set. The only person in it is a foot in
a sandal.

Each demonstration pairs its test frame with background frames from the training
burst recorded **immediately before it**. That matters: a background learned even
a few minutes earlier makes the whole scene look like it moved, because the sun
has shifted. The real camera never has this problem, since its background is
updated four times a second.

## Running the Jupyter notebook yourself

```bash
python3 -m venv venv
./venv/bin/pip install jupyter matplotlib numpy opencv-python-headless
./venv/bin/jupyter notebook analysis/step7_image_processing.ipynb
```

To re-run it and save the outputs back in, which is what makes it readable on
GitHub:

```bash
./venv/bin/jupyter nbconvert --execute --inplace --to notebook \
    analysis/step7_image_processing.ipynb
```

## Making the Mathematica notebook readable on GitHub

GitHub renders `.ipynb` files by itself but does nothing with `.nb`, so the
Wolfram notebook needs to be evaluated and exported to HTML once:

```bash
./export_mathematica_html.sh
```

That evaluates every cell, saves the outputs back into the `.nb`, and writes
`step7_image_processing.html` beside it. Commit both.

**This has not been run yet.** The Wolfram installation on this machine reports

```
Your Wolfram product is not activated or is experiencing a license-related problem.
```

so the notebook is checked in with its code and explanation but **without
outputs**. Run `wolframscript` once by hand, enter the activation key, then run
the script above.
