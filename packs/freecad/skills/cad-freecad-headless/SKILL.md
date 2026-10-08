---
name: cad-freecad-headless
description: FreeCAD parametric 3D part modelling through Harness native Runtime tools: constrained rectangle/circle sketches, pads, holes, primitives, boolean solids, FCStd/STEP/STL export and separate-process geometry readback. Use for FreeCAD, sketches, extrusions, mechanical parts and CAD conversion.
---

# FreeCAD parametric parts

Use the Harness-owned FreeCAD 1.1.4 tool pack for native engineering work.
First call `industrial_tool_describe` for the selected canonical tool in the
current Broker allowlist. Submit its declared inputs through
`industrial_action_call` with the current `expectedStateId`. Read the returned
verification and its limits, and use the returned State ID for the next action.
Do not launch FreeCAD, macros or Python through Shell or an external MCP.
Missing dependencies and unsupported model features must remain visible failures.

## First batch

- `cad.freecad.build`: rebuild a parametric recipe as a native FreeCAD feature
  tree. Rectangle/circle Sketcher profiles are fully constrained; `sketch_pad`
  makes a PartDesign Pad. `box`, `cylinder`, cylindrical `hole`, and
  `cut`/`fuse`/`common` create native primitives and boolean features.
- `cad.freecad.edit`: modify an existing generated model using its hash-bound
  `.recipe.json` and preview manifest. `changes.parameters` updates known named
  dimensions; `changes.features` patches known feature IDs (dimensions, profile,
  origin). Preserve the original, return a new Action model and verify it.
- `cad.freecad.inspect`: read a supported project FCStd/STEP, preserve the input,
  and produce geometry reports and a mesh preview in a new Action directory.
- `cad.freecad.export`: produce FCStd, STEP and STL and reopen the files in a
  separate FreeCAD process before evaluating acceptance.

Build input is `{recipe:{parameters,features,result},expect}`. Features have
unique safe `id`s; boolean `base`/`tool` values refer only to earlier features.
Dimensions are numeric mm or a key in `parameters`; `origin` is an optional
numeric `[x,y,z]`. No command, script or arbitrary expression is accepted.
Rectangle pad needs `profile:"rectangle",length,width,height`; circular pad
needs `profile:"circle",radius,height`. Hole needs `base,radius,height,origin`.
A hole axis is +Z; its height and origin must actually intersect the intended
part. Choose the final `result` feature explicitly.

Before changing an existing part, read its actual `.recipe.json` to identify
parameter names and feature IDs; never guess them. If the user only asks to
"change the shape" without an outline or dimensions, ask for clarification before
mutating. For explicit changes, prefer `cad.freecad.edit`; a new version is saved
under its returned Action path. Continue from that returned model on the next
turn. Read every VerificationResult and report failures visibly.

Parameter recipes are the reproducible source of intent. Resubmit changed
parameters or use edit to create a new Action and model version. The FCStd keeps editable
native dimensions and feature history. Its parameter group records supplied
parameters; it does not implement a live Spreadsheet expression network.

## Geometry acceptance

Supply measurable requirements in `expect`: `volume` in mm³, `bounds` as
`[xSize,ySize,zSize]` in mm, `solids` and optional absolute `tolerance` (default
0.00001). Derive expected values from the user's design, not from the tool's
reported result. For a 40×20×5 plate with radius-2 through-hole, expected volume
is `4000 - 20*Math.PI`, approximately `3937.168146928204` mm³, and bounds are
`[40,20,5]` with one solid. Send a numeric volume; expressions are unsupported.

The independent readback checks native/STEP valid solid geometry, positive
volume, solid count, matching volume/bounds, constrained sketches, and a
nonempty STL. A successful native exit can still fail dimensional acceptance.
Without `expect`, only geometry and format consistency are checked; do not
claim the user's design dimensions were validated. These tests do not establish
strength, assembly correctness, tolerance compliance or manufacturability.

## Inputs, outputs and viewing

Input `file` for inspect/export is relative to the selected project and ends in
`.FCStd`, `.step` or `.stp`. FCStd is limited to this batch's native feature
classes and datum objects; Python proxies/attributes and expressions are
rejected. Plugin workbenches and complex assemblies are unsupported. Files are
bounded to 16 MiB; recipes to 50 features and dimensions to 10000 mm.

Read the returned artifact paths under `cad-output/<action-id>/`: FCStd, STEP,
STL, BREP, hash-bound recipe, original shape report, separate readback report, input recipe/snapshot
identity, preview manifest and logs. Keep source edits in ordinary project
folders so the StateProvider can detect changes. Historical `cad-output`
artifacts should be treated as immutable outputs.

The file tree can open STL directly and generated FCStd/STEP with hash-checked
STL/BREP companions. BREP uses the official OCCT AIS/V3d WebGL2
visualization module with shaded surfaces and face boundary edges. Viewing supports rotation, pan, zoom, Fit and fullscreen; it is
read-only triangulated display and does not produce engineering acceptance.

## Environment

Native execution is currently qualified only on macOS Apple Silicon with
FreeCAD **1.1.4**. The default executable is
`/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd`; users can configure
`INDUSTRIAL_HARNESS_FREECAD_CMD` for another trusted installation. The Pack does
not install or distribute the upstream app. Do not remove system trust settings
or claim Windows/Linux native execution without qualification.
