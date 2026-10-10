# Installed numerical analysis backends

Use `set_requirements('analysis',plan)` and `run_analysis()` with
`backend:'external'`, an engine below, explicit assumptions, parameters and
numeric assertions. Final verification reruns the same real solver. The
read-only registry is `/opt/pcb-analysis-backends.json`. This is not a factory
profile: dimensions/materials are physical analysis inputs. All geometries come
from current native pads, tracks, arcs, filled zones and board outline.

## DC PI: engine `pcb_dc`, category `pi`

Parameters:

```json
{"net":"VCC","source_pad":"J1:1","voltage_v":3.3,
 "loads":[{"pad":"U1:5","current_a":0.1}],
 "resistivity_ohm_m":1.68e-8,
 "copper_thickness_mm":{"F.Cu":0.035,"B.Cu":0.035},
 "mesh_mm":0.05,"convergence_rel":0.05}
```

Values above illustrate a particular assumed conductor and load, not defaults.
For vias/PTH add `layer_z_mm` for every layer and `plating_mm`. The finite-volume
solver uses actual copper polygons/holes, contacts, resistance and current
conservation. A disconnected/singular mesh remains UNKNOWN. Resolve the narrowest
trace with at least three cells. Measurements: `drop_max_v`, `loss_w`,
`effective_resistance_ohm`, `source_current_a`. It compares mesh and half-mesh
resistance. This does not calculate AC PDN impedance, decoupling resonance,
electromigration or transient droop.

PI grids cover all copper on the selected net, including disconnected islands.
The default budget is 350000 cells per layer; an explicit `max_mesh_cells` may
raise it to at most 16000000. The 600000-unknown system limit still applies.
Choose budgets from board dimensions and trace width; exceeding a budget or
failing convergence is not permission to coarsen away required geometry.
Same-number exposed pads and thermal vias share one logical terminal when their
nets agree. Conflicting nets remain an input error.

## Thermal: engine `pcb_thermal`, category `thermal`

Parameters: `conductivity_w_mk:[kx,ky]`, `thickness_mm`,
`convection_w_m2k`, `ambient_c`, `mesh_mm`, `convergence_rel`,
`sources:{ref:{watts,radius_mm}}`, `zero_power_components:{ref:reason}`.
Every physical component must have a heat source or explicit zero-power reason.
Sources are centered on actual footprint positions. The solver calculates
steady-state anisotropic effective board conduction with convection on both
faces; outline holes are excluded. Assert `max_board_temperature_c`,
`mean_board_temperature_c`, `rise_max_c` or `rejected_power_w`.
Power balance and refinement convergence are required. Effective conductivity
must be justified for the board. This does not resolve junction temperature,
individual copper-layer spreading, enclosure airflow or transient heating.

For actual copper spreading, use `layered_stack` instead of the three homogeneous
parameters `conductivity_w_mk`, `thickness_mm` and `convection_w_m2k`:

```json
{"layer_z_mm":{"F.Cu":1.6,"B.Cu":0},
 "copper_thickness_mm":{"F.Cu":0.035,"B.Cu":0.035},
 "copper_conductivity_w_mk":400,"plating_mm":0.025,
 "dielectrics":[{"layers":["F.Cu","B.Cu"],"conductivity_w_mk":[0.3,0.3,0.3]}],
 "dielectric_slices":4,"convection_w_m2k":{"F.Cu":5,"B.Cu":5}}
```

These are illustrative material/boundary assumptions, not qualified defaults.
`layer_z_mm` locates copper midplanes; dielectric intervals exclude foil thickness.
Each adjacent native layer pair needs a dielectric with `[kx,ky,kz]` conductivity.
`dielectric_slices` subdivides each interval through thickness. Refinement halves
the lateral cell size and doubles the slices (maximum 64); the 600000-unknown
limit remains. Actual copper polygons and plated barrels conduct heat. Unresolved
contacts, missing conditions or failed refinement remain UNKNOWN.

For small contacts or thermal spokes, layered thermal parameters may include
`mesh_windows:[{bounds_mm:[xmin,ymin,xmax,ymax],refinement:integer}]`.
Choose each window from inspected native geometry; it must lie inside the board's
bounding box. Refinement factors are 2..32, with at most 32 windows. The entire
board remains in the solve; the rectilinear grid uses finer spacing along axes
crossing the windows. Cell areas, lateral/vertical conductance, convection and
heat injection use those physical widths. Coarse/fine solves retain the same
window boundaries while halving spacing and doubling dielectric subdivisions.
This can resolve a small pad without refining the whole board uniformly. It
does not waive the unknown-count budget or contact-temperature convergence.
An under-resolved high temperature is not a qualified overheating finding.

Use `sources:{ref:{watts,pad,layer}}` to inject loss on that component's native pad;
retain explicit reasons in `zero_power_components` for all other components.
Outputs include `ref_contact_max_c` and `ref_contact_mean_c`, board maximum,
temperature rise and rejected power. Perfect bonding and declared convection are
assumed; this does not calculate solder/package junctions, airflow, radiation,
thermal transients or temperature feedback. A converged board-temperature solve
alone cannot qualify junction temperature or the declared material properties.

For `qualified_device_cosimulation` with `thermal_network`, voltage/transient
qualification is insufficient. The host's reviewed model record must also have
`qualified_power_testbenches[exact_electrical_conditions_sha256]` with a nonempty
`power_measurements` list. Each name must exist in `electrical.measures` and feed
the reviewed network's `power_measurements`. Missing loss qualification returns
UNKNOWN. The network must bind to this board hash, electrical fixture and ambient
domain. Candidate-authored catalog entries cannot supply this qualification.
Datasheet theta-JA depends on its test PCB; psi-JT/JB are characterization
parameters, not independent resistances to apply to an arbitrary board.

## SI and radiation: engine `openems`, category `si` / `emc`

The installed openEMS solves 3D Maxwell fields. This adapter supports explicit
port-driven bare-board interconnects with PEC copper and lossy dielectric layers.
It exports actual polygons, round drilled holes and plated barrels, then runs
coarse/fine meshes and native time-domain decay checks.

Parameters:

- `layer_z_mm`: all native copper layers in physical order; distinct positions.
- `dielectrics`: `{z0_mm,z1_mm,epsilon_r,conductivity_s_m}` intervals covering the
  entire stack without gaps or overlaps. These are declared physical assumptions.
- `component_omissions:{ref:reason}` for every omitted body/circuit model. The
  adapter does not synthesize transistor switching or connector/enclosure bodies.
- `ports`: `{pad,signal_layer,reference_layer,reference_net,resistance_ohm,excite}`.
  A port joins a real signal pad vertically to actual reference copper at the
  same x/y. For a repeated pad number, also specify `pad_member_uuid` from the
  actual native pad to choose its physical anchor. Exactly one port has
  `excite:1`; others have `0`. Optional `extent_mm:[width,height]` fixes the finite
  port area, which must lie inside both signal pad and reference copper. If absent,
  the adapter derives an inscribed area from that intersection, independent of
  mesh size. Refinement preserves the physical port. Native skipped/unused port
  warnings invalidate the solve; a pure-resistor port need not activate the
  separate series/parallel RLC extension.
- `center_hz`, `bandwidth_hz`, `air_margin_mm`, `boundary:'MUR'` or
  `'PML_8'`, `max_timesteps`, `end_energy_rel` (at most 1e-3).
- Choose `measure_hz` for one frequency, or `frequencies_hz` for 2..201 strictly
  increasing samples inside the excitation band. They are mutually exclusive.
- `mesh_mm`, `convergence_rel` (at most 0.2), `convergence_metric`.
- Optional `mesh_alignment:'copper_edges'` fixes rectilinear copper and outline
  edges (including slots) across coarse/fine grids. The default `'ports'` retains
  the existing port-only grid. Curves and diagonal edges still need convergence.
  Incompatible near-coincident features return UNKNOWN; do not move physical
  copper or relax the convergence limit to make a mesh fit.
- Optional `air_mesh_mm` caps graded cells outside the native board bounding box.
  It must be at least `mesh_mm` and obey the air wavelength limit. Interior cells,
  port extents and physical geometry are preserved; both interior and air caps
  halve during refinement. PML needs eight uniform outer cells after the graded
  transition. A short air margin is rejected, not silently shrunk. This reduces
  exterior cell cost; mesh/domain/decay convergence still requires actual solves.
- Optional `domain_convergence:{air_margin_mm,convergence_rel}` runs a third
  solve with a larger air margin, at the fine mesh. Its tolerance is at most 0.2.
- Optional `max_mesh_cells`: explicit FDTD budget, default 3000000 and maximum
  12000000. A larger budget does not relax mesh or decay checks.
- `plating_mm` when barrels exist. Radiation also needs `observation_radius_m`
  and `accepted_power_w` to normalize absolute far-field magnitudes.

Measurements are `s11_mag`, `s21_mag` etc. for the excited column,
`input_resistance_ohm`, `input_reactance_ohm`; radiation adds
`radiated_power_w`, `directivity`, `sampled_e_max_v_m`. The far-field sample
angles are 15 degrees in theta and 30 in phi; sampled maxima are not guaranteed
continuous-angle maxima. With multiple frequencies, assert aggregate keys such
as `s11_mag_max` or `s21_mag_min`; `spectral_measurements` retains each frequency.
`convergence_metric` uses the base name, such as `s11_mag`, and checks every
sample for both mesh and requested domain refinement. Sampled extrema do not
prove a continuous-band maximum or minimum. Run separate tests for excited
ports, power/load corners and other conditions relevant to the claim.

Current geometry limits: 600000 DC/thermal unknowns, round drilled
holes, vertically referenced lumped ports. Missing conditions, unsupported
geometry, missing decay evidence or failed mesh convergence return UNKNOWN.
Model/registry/CAD hashes and complete solver logs bind every report to inputs.

A full-wave engine is not a complete product EMC model. Immunity injection,
ESD/EFT/surge, cable/chassis geometry, conductor surface loss, IBIS drivers,
eye diagrams and full compliance sweeps require additional validated adapters,
models or physical measurements. Never use the `emc` category name to claim
those absent tests. The external-backend protocol remains open for extensions.

## Optional laboratory dependencies

The separately pinned engineering-lab image includes Icarus Verilog, Gmsh/GetDP,
sigrok protocol decoders, OpenOCD and VISA/serial drivers. These are host-side
model-development and acquisition dependencies, not additional qualified engines
in `run_analysis`. HDL execution does not model an unspecified commercial IC;
FEM execution needs package/material/power/boundary evidence before it can support
a junction-temperature claim. Existing native SPICE, thermal-network and openEMS
adapters remain the analysis entry points with their documented scopes.

An operator can use `/opt/pcb-lab/pcb_lab.py` to discover local instruments and
retain scalar SCPI samples with CAD/input hashes, raw responses and setup records.
It writes `kind:unreviewed_capture`, outside the candidate workspace. It neither
publishes a trusted catalog entry nor changes verification status. Host timestamps
are acquisition timestamps, not oscilloscope trigger times. A calibrated,
reviewed measurement still enters through the existing `approved_capture` backend.
No attached instrument means no hardware witness.

The host-only `/opt/pcb-lab/usb_uart_probe.py` can retain a selected USB device's
descriptor and bidirectional UART byte observations with two explicitly identified
local ports and a documented cross-connected fixture. It verifies the bridge tty
belongs to that USB bus/address/VID/PID. It does not reset the device or prove the
USB enumeration transaction sequence, electrical timing, voltage or ESD limits.
Raw matching bytes still require independent review; disconnected hardware
produces a failed acquisition receipt, never a synthetic device response.
