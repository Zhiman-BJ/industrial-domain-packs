# Placement, routing and board readability

Use this reference when creating or changing a PCB layout. Select arrangements
from the actual circuit, mechanical requirements and component documentation.
The generator's automatic packing considers footprint size, not circuit function;
it is a starting arrangement. DRC clearance success does not establish a good
layout. There is no required row pattern, reference design or universal spacing.

## Page, board and view coordinates

A board near a drawing sheet's upper-left origin may be mechanically correct.
Use a fit-to-board/objects view for inspection; do not move the board or its origin
just to centre a screenshot. The schematic is a separate drawing: keep each page's
content within usable margins and clear of its title block. The current generator
centres its schematic layout, but arbitrary schematic graphic placement is not a
public operation.

Within a new, unconstrained rectangular board, provisional packing balances spare
space around the footprint envelope instead of accumulating parts in a corner.
That is a starting pose only. Keep required edge connectors, mounting holes and
other mechanical anchors in their specified positions. Do not force symmetry or
centre a connector that must reach an enclosure opening. Use native board bounds,
including nonzero origins; coordinate zero is not necessarily a board corner.
The initial packer refuses locked parts, existing copper and nonrectangular/cutout
boards; select explicit placements for these cases. Review full bodies/courtyards,
routing corridors and connector access, not only component centre points.

## Plan from constraints

Identify fixed connectors, mounting holes, enclosure openings, height limits and
keepouts first. Orient connectors for mating and access; lock only genuinely
fixed parts. Do not move mechanical anchors merely to shorten tracks.

Use `postroute.placement_regions` when the requirement permits a region rather
than a single coordinate. Each entry identifies `ref`, lower/upper bounds in
`x_fraction` and `y_fraction`, and an optional `side`. Select bounds and side
from the actual project mechanics; this reference supplies no placement values.
Fractions locate the footprint reference point within the native board-outline
bounding box; they do not check the full body, mating access or irregular edges.

Group parts by their signal and power relationships. Choose a readable signal
flow where mechanics permit; keep support parts with the device they serve.
Compare plausible positions and rotations using actual pad locations. A nearby
footprint origin can still put the relevant pin on the wrong side of the device.
Leave routing corridors and assembly/probe access instead of packing every gap.

Prioritize the electrically sensitive paths before cosmetic alignment:

- Place decoupling to shorten the supply-pin/capacitor/return loop. Follow the
  device's capacitor and return-via guidance; centre distance alone is inadequate.
- Put connector protection along the incoming path, with a short, appropriate
  discharge return. Avoid routing the unprotected path through the circuit first.
- Keep switching-current loops compact and feedback/sense nodes away from noisy
  copper. Keep crystal and high-impedance nodes short and away from aggressors.
- Preserve the intended reference/return path under fast signals. Do not split a
  ground plane merely because blocks are named analog and digital.
- Reserve differential corridors, thermal paths and antenna keepouts according
  to the selected design's documented requirements. Their dimensions are inputs,
  not fixed values supplied by this skill.

## Make the arrangement readable

Within the electrical and mechanical constraints, align related passives, use
consistent orientations and leave deliberate space between functional groups.
Prefer a few coherent rows or columns to scattered placements; neither symmetry
nor minimum board area is a universal objective. Keep polarity/pin-1 marks and
connector labels legible. Reference text should identify its part without covering
pads, openings or other text; inspect both sides in their own viewing orientation.
Do not hide required labels solely to remove a warning.

Route critical paths first where appropriate, then review congestion, detours,
layer changes and return paths. Use clean bends and consistent widths within each
net's rules. Reposition or rotate a part when that improves the circuit; do not
accept a large detour solely because an autorouter found a DRC-passing solution.
Fill zones and inspect their actual continuity after routing.

For a local pad-to-plane connection change, use the pad UUID and board hash from
`inspect_board`, then `set_pad_zone_connection`. Select solid or thermal relief
from the device's electrical, heat-flow and assembly requirements; solid is not
a universal default. This changes only the placed pad's override. Refill zones,
rerun DRC and renew affected power/thermal evidence; an electrical connection
alone does not establish adequate heat flow.

## Observe, revise and verify

Use `inspect_board(include_geometry=true)` for native footprint bounds, pad sizes,
courtyard bounds and visible reference/value text positions. Coordinates and bounds
are in board space after rotation/flipping. Bounds are axis-aligned envelopes,
not exact collision tests or mechanical height models. Missing courtyard geometry
stays missing; do not infer safe clearance from it. Use `include_copper=true` when
reviewing tracks and vias. Large observations may be truncated in model context;
read the saved full observation or query the needed geometry with `run_python`.

Use `view_design` for the native schematic/front PCB preview and `export_3d` for
assembly views. The controller must enable image delivery for a vision-capable
model; exporting an image alone is not visual review. Inspect unshown layers and
sheets through native object observations and relevant checks.

Use `place_component`, `lock_component`, `set_component_text` and copper operations
for PCB revisions. Use `inspect_schematic`, `place_schematic_component` and
`set_schematic_field` to correct drawing placement without rebuilding components.
Movement retains PCB tracks; inspect affected nets and repair locally.

Rerun DRC and applicable placement, length and electrical checks after changes.
Separate measured violations from visual suggestions. Alignment, balance and
compactness are design judgments unless the original requirements define a
measurable constraint. Neither a prettier preview nor lower total wire length
alone justifies electrical PASS. Record the important placement tradeoff in the
normal task response; an extra design-report artifact is not mandatory.
