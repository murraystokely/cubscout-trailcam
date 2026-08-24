---
name: wolfram
description: Run Wolfram Language / Mathematica on this machine, and evaluate or export the notebooks in analysis/. Use when asked to run Wolfram code, evaluate a .nb, export a notebook to HTML, or when a Wolfram kernel reports a licensing error. Covers the two-seat license limit that makes kernels fail with a misleading "not activated" message.
---

# Running Wolfram on this machine

Wolfram 15.0.1 Professional is installed at `/usr/local/Wolfram/Wolfram/15.0`.
There is **no always-on MCP server**; run the kernel on demand instead.

## The one thing to know first

This is a **two-seat licence**: `$MaxLicenseProcesses` is 2. Every running kernel
takes a seat — an open Mathematica window, a Wolfram MCP server, and each
`wolframscript` call alike.

When no seat is free the kernel does **not** say so. It says:

```
Your Wolfram product is not activated or is experiencing a license-related problem.
```

That message is misleading. The licence is almost certainly fine. Check seats
before believing it:

```bash
pgrep -a -f WolframKernel | grep -v bash
```

Ignore any kernel launched with `-pwfile ... playerpass -sandbox` — that is a
preview helper and does not take a seat. If two *other* kernels are up, that is
the whole problem.

In practice: **if a Mathematica window is open, you have exactly one seat left.**

## Running code

```bash
wolframscript -code 'Print[ImageDimensions[Import["analysis/data/quiet_patio.jpg"]]]'
wolframscript -file /tmp/script.wl
```

Each call starts a kernel, takes a seat, and releases it on exit. Nothing is
held between calls, so **state does not persist** — a function defined in one
call is gone by the next. Put anything multi-step in a single `.wl` file.

Startup is roughly 10–20 seconds, so batch work rather than making many small
calls, and use a generous timeout.

## Checking a notebook without a front end

To find errors in a `.nb` without opening the GUI, pull out its input cells and
evaluate them in order. This needs only one seat:

```wolfram
SetDirectory["/home/murray/git/cubscouts/analysis"];
nb = Get["step7_image_processing.nb"];
inputs = Cases[nb, Cell[c_String, "Input"] :> c, Infinity];
Do[Module[{code = inputs[[i]], r, m},
   code = StringReplace[code, "NotebookDirectory[]" -> "\"" <> Directory[] <> "\""];
   Internal`$MessageList = {};
   r = Quiet[Check[ToExpression[code], $Failed, {}], {}];
   m = Internal`$MessageList;
   If[r === $Failed || m =!= {}, Print["cell ", i, " PROBLEM: ", InputForm[m]]]],
 {i, Length[inputs]}];
```

`NotebookDirectory[]` has no meaning headless, hence the substitution.

## Exporting a notebook to HTML

GitHub renders `.ipynb` files with their outputs but does nothing with `.nb`, so
the Wolfram notebook has to be evaluated and exported once:

```bash
analysis/export_mathematica_html.sh
```

**This needs a front end**, which wants a *second* seat on top of the script
kernel. With a Mathematica window already open there will not be one. Either
close the window first, or do it from inside the open session:

```wolfram
SetDirectory["/home/murray/git/cubscouts/analysis"];
nb = NotebookOpen[FileNameJoin[{Directory[], "step7_image_processing.nb"}]];
NotebookEvaluate[nb, InsertResults -> True];
NotebookSave[nb];
Export[FileNameJoin[{Directory[], "step7_image_processing.html"}], nb, "HTML"];
```

Commit both the `.nb` and the `.html`.

## Pitfalls already hit here

- **`ImageTranslate` does not exist.** It looks plausible and fails silently,
  leaving the expression unevaluated rather than raising. To shift an image a
  pixel: `Image[RotateLeft[ImageData[img], {0, 1}]]`. Check anything uncertain
  with `Names["Foo"]` before relying on it — an empty list means no such symbol.
- **`ImageMeasurements[img, "Mean"]` returns a scalar** for a single-channel
  image, not a list, so `First[...]` on it throws. `Mean[Flatten[ImageData[img]]]`
  is unambiguous.
- **Wolfram and OpenCV disagree slightly.** They resize, blur and threshold
  differently, so the same blob measures ~5,375 px in OpenCV and ~6,850 in
  Wolfram. The camera uses the OpenCV numbers; treat those as authoritative.
- **`ComponentMeasurements` returns rules**, `id -> {values}`, so sorting by area
  is `SortBy[measures, -#[[2, 1]] &]`.

## The MCP server, if it is ever wanted back

Wolfram ships an MCP server (the `Wolfram/AgentTools` paclet) that gives Claude
Code a persistent Wolfram kernel. It was **removed here deliberately**: it is
registered at user scope, so *every* Claude Code session starts its own kernel,
and a session left open for days sat on the second seat and broke both the GUI
and `wolframscript`.

A persistent kernel is genuinely nicer for iterative work — state survives
between calls and there is no per-call startup. It is worth re-enabling only
with headroom to spare, and then at **project scope** so it does not follow
every session:

```wolfram
Needs["Wolfram`AgentTools`"]
InstallMCPServer[{"ClaudeCode", "/home/murray/git/cubscouts"}, "Wolfram"]
```

To take it away again:

```bash
claude mcp remove Wolfram
```

Removing the registration does not stop a kernel a running session already
started; find it with `pgrep -f AgentTools` and note that it ignores SIGTERM.
