# Hierarchy, incremental synchronization and routing

`add_sheet('/Analog')`, `add_sheet('/Analog/Filter')` create logical child sheets;
`assign_sheet(ref,path)` assigns a whole component, including all its symbol
units. `set_sheet_port(path,name,net,direction)` connects one declared global
intent net across the boundary. Direction is input/output/bidirectional. Each
sheet may expose a net once; parent paths must exist. `remove_sheet` accepts an
empty leaf. The generator writes native sheet instances and project-relative
files. Repeated reusable sheet templates/buses are not structured operations.

For separate local nets with identical labels, declare e.g. `Analog/RESET` with
`add_net(...,properties={"scope":"/Analog"})`. Only parts in that sheet may use
it. Normal intent nets are global. Hierarchical ports must have real connections;
a label on a sheet border does not supply a missing endpoint. ERC and exported
netlist parity are both required. Missing/cyclic/escaping sheet files block checks.

`update_pcb(prune_affected=false)` requires a newly generated schematic matching
current intent. It updates values, parts, pad nets and footprints in a candidate
and adopts only after exact native netlist parity. Existing poses and locks are
preserved; new parts need positions. Exact net renames preserve copper when pad
membership is identical. Unchanged copper/footprints retain UUIDs. Replaced
footprints get new native UUIDs at the previous pose. The result lists affected
nets and removed/retained copper. The default retains existing-net tracks/vias for local repair, including affected nets;
`true` explicitly removes all copper tracks/vias on those nets. Copper whose net
no longer exists is removed in either mode. Inspect removed/retained UUIDs; adding
a pin to a supply net does not normally justify removing that entire net. Zones may require refilling. ECO parity
PASS requires fresh DRC, electrical and postroute checks before acceptance.

`route_differential_pair(p_net,n_net,centerline,width_mm,gap_mm,layer,terminals)`
constructs offset paths along an explicit corridor. `terminals` is
`{p:["J1:1","J2:1"],n:["J1:2","J2:2"]}`. Both endpoints must match actual pad
centers; positive is left of the ordered centerline. Width/gap are explicit mm.
The tool rejects existing copper on those nets, invalid offsets and gap collapse.
Fanout, vias and obstacle avoidance need explicit edits or another backend.

`tune_length(item_id,target_length_mm,pitch_mm,max_amplitude_mm,side)` replaces
one straight segment of an unbranched two-pad net with rectangular trombones.
Target is total copper centerline length, excluding package/via flight time.
Pitch and amplitude bound the corridor. Vias/arcs/zones and ambiguous branching
are rejected. Self-intersections and same-net shortcuts are checked in addition
to native DRC. Both routing operations preserve source files unless the candidate
passes local checks; unrelated existing unconnected nets may remain. Inspect the
result, then final DRC and declared skew/SI requirements. These operations are
not a push-and-shove router, impedance solver or phase-delay optimizer.

## Three-dimensional assembly

`prepare_3d_model(ref)` acquires the selected footprint's assigned 3D bodies.
Standard KiCad paths use the pinned official library; custom paths need
`sources:{original_model_name:{url,revision,license_url,sha256}}`. Files and
provenance stay under the project. No model is guessed from a similar package.
Preparation also rewrites that footprint's body paths to `${KIPRJMOD}` so native
KiCad can reopen a moved project. This changes the board revision; rerun affected
checks after preparation. Available standard bodies are prepared at PCB generation.
Missing assignments require an explicit sourced assignment through an operator
extension. Library shapes are nominal geometry, not exact-MPN dimensional proof.

`export_3d(destination,format)` exports one STEP, GLB or PNG from the current PCB.
PNG accepts `view="top"`, `"bottom"` or `"isometric"` (default), plus
`width_px` and `height_px` (requested 64–4096 pixels, default 1600 × 1000).
KiCad may round the render dimensions; the report records actual `image_size`. The final
independent exporter includes all three native PNG views, front/back PCB SVG,
schematic SVG, STEP, GLB and the submitted CAD archive. It does not download
new bodies into a frozen candidate; prepare required bodies before submission.
KiCad handles board side, offsets, rotation and scale. Missing bodies produce
UNKNOWN even if a partial preview can be shown. `export_project(...,include_3d=true)`
adds these formats and project-local dependencies to a freshly checked bundle.
The separate replay renderer captures project 3D dependencies and serves a
local GLB viewer; browser appearance does not set any acceptance status.

For actual nominal geometry checks, use an analysis test with
`category:"mechanical",backend:"external",engine:"pcb_mechanical"`:

```json
{
  "model_sha256": {"U1":["actual body hash"],"J1":["actual body hash"]},
  "checks": {"gap":{"kind":"pair","a":"U1","b":"J1"}}
}
```

This is `parameters`, not a whole test. Supply id, assumptions and assertions
through the usual analysis contract. Pin ordered model hashes for every
populated body; native DNP parts are omitted. An unchanged round bare-copper land
from an installed library has no separate body. The inventory records its library
source/hash and checks native pad geometry and assembly attributes; a custom
waiver, missing ordinary body or modified pad cannot claim that exemption.
Copper lands remain in connectivity and board-clearance checks and cannot be
selected as independent STEP solids. Pair checks measure
`gap_clearance_mm` and `gap_overlap_mm3`; **both** need explicit original bounds.
Touching faces have zero clearance and zero overlap; intersecting solids have
positive overlap volume. No universal mechanical spacing is invented.

`kind:"bounds",object:"U1"` reports xmin/ymin/zmin/xmax/ymax/zmax in mm, prefixed
by the check ID; bound every returned metric. Coordinates follow KiCad STEP
export, not screen pixels. The reserved object `@board` is the drilled board
body. External enclosures/fixtures use `external_solids:{"@case":{file,sha256,
coordinate_frame:"kicad_step_mm"}}`; their files must also be pinned in test
`inputs` and already placed in that same coordinate frame.

Checks run OpenCascade solid distance and Boolean intersection on native STEP
exports. Invalid/non-solid geometry, missing files, changed model hashes and
failed calculations remain UNKNOWN. Only named pairs/envelopes are tested;
define all pairs required by the assembly contract. This does not establish
tolerance stacks, connector insertion travel, thermal performance or production
fit. A model, enclosure or CAD change requires a fresh verification.
