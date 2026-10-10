# Engineering analysis contract

`set_requirements("analysis", plan)` saves a plan; `run_analysis` executes it;
`verify_design` reruns it. `analysis.electrical_checks` is shared by schematic,
build-loop and final verification. Independent acceptance uses the original plan.

Fields: `required` (category names), `not_applicable` (category → justification),
`devices` (physical ref → explicit model binding), optional `device_rules`
(exact CAD symbols → binding), `tests` (executable checks).
Categories: function, stability, dynamic_simulation, protocol, physical, emc_precheck, emc, rf, thermal, si, pi, mechanical. Required
categories without tests return UNKNOWN. All tests within a category must pass.
Required and not-applicable categories cannot overlap. Empty plans cannot pass.

For circuit design/electrical repair, check that the fixed contract requires
functional simulation. A CAD-only regression is not an end-to-end circuit
success. Report missing fixed functional coverage as a task-curation gap;
candidate tests are useful diagnostics but cannot authorize completion.
`run_analysis` returns NOT_APPLICABLE only for categories explicitly excluded
by immutable requirements. It returns UNKNOWN for missing required tests or
unresolved applicability, and states `tests_executed`; neither means simulated
function passed. Measurement feedback contains `observed`, `expected` bounds
and per-measurement status. These bounds describe behavior, not repair answers.

Each test has `id`, `category`, `backend`, `assumptions`; IDs use letters, digits
and underscores. Reports preserve spec/CAD/model hashes, logs, measurements,
bounds and repair blockers. Bounds are explicit finite `min`/`max` (inclusive)
or `min_exclusive`/`max_exclusive` (strict); preserve the requirement's inequality,
for example `{"max_exclusive":0.05}` means below 50 mV, not at most 50 mV. Rerun after
any source/model change; a saved report alone is never fresh acceptance.

SPICE tests may declare `circuit_requirements` using the component/value,
component-group and connectivity predicates in [verification.md](verification.md).
Use these for sourced model operating conditions, such as an external capacitor's
allowed range and connection. They are checked against the exported KiCad netlist
before simulation. Failed prerequisites report `evidence_kind=model_validity` and
`simulation_executed=false`; they are not simulated waveforms. Pin referenced
documents with `inputs:{relative_path:sha256}`. Missing/changed inputs are UNKNOWN.
The frozen public contract supplies required predicates for bound tasks.

`timeout_s` is a per-test wall-time limit in `(0, 86400]` seconds; defaults are
1800 for SPICE and 3600 for external analyses. For long plans, give the outer
tool/session enough time for the sum of sequential tests; independent first and
final acceptance may each execute that plan. Increasing a deadline cannot repair
numerical divergence or missing model coverage. Simulation, geometry and measured
capture results have separate evidence kinds; none automatically substitutes for
another kind required by the original task.

Tests that set `bind_to_revision:true` additionally require the backend response
to echo the actual PCB SHA-256 (and, when supplied, schematic/spec and condition
hashes). `require_evidence:true` requires retained evidence hashes. This is the
common contract for protocol, RF, thermal and physical witnesses; an external
backend that cannot provide these fields returns UNKNOWN.

## SPICE from actual CAD

SPICE normally uses the exported **schematic** netlist. Reports identify `simulation_source` and `pcb_copper_extracted`; a PCB revision hash alone does not include copper shorts, opens or parasitics in the solver. Native DRC and schematic/PCB parity independently check the board. Report successful ideal-circuit tests within that scope, even when a board defect blocks overall acceptance.

To include routed DC resistance, a SPICE test can declare `pcb_parasitics` with `mode:"dc_resistance"`, `resistivity_ohm_m`, per-layer `copper_thickness_mm` and `layer_z_mm`, `plating_mm`, `mesh_mm`, `convergence_rel` (at most 0.2), and `ideal_nets` (net to justification, empty by default). Every enabled copper layer needs material/position data. Sources, ground and measurements use physical aliases from `terminals:{"@load":"J2:1"}`. The solver extracts native copper polygons into a multi-terminal resistance network and checks mesh convergence. Shorts, disconnected contacts, unsupported geometry or nonconvergence prevent simulation; there is no fallback to ideal wires. The result retains `pcb-resistance.json`, circuit and evidence hashes. This covers DC resistance at the declared resistivity, with equipotential pads and lumped barrels; it does not extract L/C, high-frequency coupling, skin effect or junction temperature. Fixed tasks obtain these conditions from the immutable public contract.

`engine:"qualified_device_cosimulation"` executes native-netlist transient SPICE followed by declared UART/SPI/SD-SPI/SWD waveform decoding or a reviewed steady thermal network. Parameters contain an `electrical` SPICE `dynamic_simulation` test, `purpose`, and any `model_records`, `operating_conditions`, `excluded_devices`, `decoders`, `logic_channels`, `max_interval_s`, `thermal_network`, `ambient_c`. Transient `print_vectors:{name:"v({NET})"}` retains numeric waveforms. Logic thresholds and sampling resolution are explicit; supported decoder fields follow the approved-capture contract below. Electrical measurements appear as `electrical_<name>`, decoder outputs as `<decoder>_<metric>`; every output requires bounds. Commercial models require host-reviewed records matching exact MPN, CAD, pins, model hashes and operating domain. Thermal fixture review must bind to the actual board and electrical loss expressions. Separate tests cover separate corners. This is one-way electrical-to-digital/steady-thermal analysis, without thermal feedback, USB/CAN device models, RF/EMC or hardware measurement. Missing testbenches or qualified records remain UNKNOWN; model-authored diagnostics cannot replace fixed acceptance tests.

Use `run_analysis(basis="candidate")` to execute additional tests in your design intent. The report is explicitly diagnostic and cannot replace public requirements, overwrite the mandatory analysis report or establish task completion. The default `basis="requirements"` executes the fixed public plan in bound tasks. This permits investigating a suspected tolerance/load failure without weakening the independent acceptance contract.

For ideal passive corners, a SPICE test may specify `tolerances:{"R":0.01,"C":0.05}` (fractional limits). The backend runs nominal plus every independent low/high combination for up to eight explicitly bound ideal passives, retaining each deck, measurement and value scale against the candidate revision. Larger sweeps return UNKNOWN. These extremes do not prove statistical yield, interior extrema, temperature coefficients, device-model corners or parasitics. State those separately when applicable. Bound-task acceptance uses the public test; additional model-authored corner tests are diagnostics until an external contract requires them.

`backend:"spice"` supports function/stability with `.tran`, `.ac` or `.dc`.
It exports actual KiCad connectivity and checks intent before generating a deck.
Every physical component needs an explicit model, or `kind:"exclude"` with a
reason (e.g. a connector represented by external stimuli). Never exclude a device
essential to the behavior being claimed.

Ideal passive: `{"kind":"R","pins":["1","2"]}` (also C/L). Values come from
actual CAD. KiCad values such as 4k7, 100nF, 1µF, 10uH and 1M are normalized to
numeric SI before SPICE execution; M means mega in a component value, m means
milli. Stimulus/model text still follows the simulator's own notation.
Semiconductor/subcircuit kinds D/Q/M/X require:

```json
{"kind":"X","pins":["3","2","7","4","6"],
 "file":"models/opamp.lib","model":"ExactModelName",
 "source":"manufacturer URL and revision","sha256":"actual SHA-256"}
```

Pins are ordered *symbol* pins following the model's terminal order. All connected
pins must be covered. A multi-unit device can use `instances:[{pins:[...]},...]`
with common kind/file/model/source/hash fields inherited by each instance.
Shared supply pins may occur in multiple units; explicit NC pins get separate
unconnected simulation nodes. `supported_analyses` limits qualified command
types, e.g. `["tran"]`; requests outside it remain UNKNOWN. Use flat `.model`/`.subckt` packages; unresolved includes,
control commands and missing hashes are rejected. `fetch_artifact(...)`
accepts `kind:"spice_model"` with URL/destination/revision/license URL/hash.
Syntax/hash checks do not validate a model's physical accuracy.

Manufacturer model names may contain hyphens or dots (for example
`OPAx320-Q1`); keep the actual subcircuit identity. For large models, a test may
declare `save_vectors:["v({OUT})","v({VIN})","i(Vstim0)"]` to retain only needed
waveforms instead of every internal node. Include every vector used by a
measurement; missing data stays UNKNOWN. This changes storage, not equations,
stimuli or limits. Inspect the solver log and memory use before extending a
timeout. A constant-current fixture before power-up may drive an unpowered
rail negative: document load compliance/enable timing separately from startup
requirements, and retain the original failing observation.

Component qualification testbenches may include explicit external `parts` and
matching `devices` bindings for loads, storage and feedback. The DUT remains
`U1`; fixtures are not inserted into the candidate design. Qualified nominal
operation does not establish temperature, tolerance, protocol or field coverage.

For designs with variable component names/counts, use
`device_rules:[{"symbols":["Device:R"],"binding":{"kind":"R","pins":["1","2"]}}]`
and equivalent explicitly chosen rules for other devices. Rules match the actual
exported CAD symbol, never a reference prefix or task name. Every component must
match exactly one rule or one explicit binding; unknown or ambiguous matches
remain UNKNOWN. A rule does not select components, synthesize topology or exempt
unmodeled devices. Private acceptance supplies its own frozen rules and bounds.

`compatibility:"pspice"` enables ngspice's whole-deck PSpice translation while
preserving source model bytes. Default is native ngspice. Test `temperature_c`
sets the actual `.temp`; optional `spice_options` permits rshunt/gmin/reltol/
abstol/vntol/itl1 only. Record numerical assumptions. Mixing compatibility modes
without a qualified unified package is rejected. Read the selected model metadata
for its qualified analysis modes; do not infer coverage from the device family.

Example RC cutoff test (IN/OUT/GND must exist):

```json
{"id":"cutoff","category":"function","backend":"spice",
 "ground":"GND","assumptions":"Ideal unloaded RC, nominal values, small signal",
 "sources":[{"kind":"V","p":"IN","n":"GND","value":"DC 0 AC 1"}],
 "command":".ac dec 40 1 100k",
 "measures":{"gain":"FIND vm({OUT}) AT=159.1549"},
 "assertions":{"gain":{"min":0.70,"max":0.72}}}
```

`{NET}` resolves to a generated node ID. Sources specify kind V/I, p/n nets, and
a single-line DC/AC/PULSE/PWL value. Every measurement needs bounds. Represent
each supply/load/temperature corner with a distinct test and explicit conditions.
For a test defined at external interfaces, use e.g.
`terminals:{"@input":"J1:1","@return":"J1:2","@output":"J2:1"}`,
then `ground:"@return"`, source `p:"@input",n:"@return"` and
`v({@output})` in measurements. Aliases resolve through the actual KiCad
exported pins, so internal net names can change. Missing endpoints and aliases
that collide with native net names return UNKNOWN.
Settling/overshoot or phase margin needs an appropriate testbench; RC settling
does not establish active-regulator stability. Legacy `spec.simulations` decks
remain diagnostic and cannot satisfy required design-bound categories.

## EMC precheck

`backend:"geometry", category:"emc_precheck"` executes explicit project limits.
`geometry` accepts the postroute nets/differential_pairs/placements/zones schema;
`pad_distances` accepts `[{"a":"U1:5","b":"C1:1","max_mm":2}]`.
The 2 mm example is not a universal rule. Actual pad anchors, copper lengths,
via counts, filled zones and positions are measured. This backend does not
calculate return currents, impedance, emissions or immunity.

## External analysis

`backend:"external"` selects `engine` from a host-owned read-only registry at
`PCB_ANALYSIS_BACKENDS` (default `/opt/pcb-analysis-backends.json`):

```json
{"my_solver":{"argv":["/opt/solver/adapter"],"categories":["emc","si"]}}
```

The runner executes argv with `--request REQUEST.json --output RESPONSE.json`,
without a shell or API credentials. Requests carry actual CAD paths, original
spec, parameters, assumptions, assertion bounds, source hashes and request_sha256. Optional test `inputs` maps relative input files to expected SHA-256; changed/missing inputs block execution. Registry `inputs` can list absolute solver dependency files for freshness tracking.
Responses echo request_sha256 and provide method, explicit PASS/FAIL/UNKNOWN
status and numeric measurements. Missing status is UNKNOWN; explicit physical
test failures remain FAIL instead of being relabeled as missing coverage.
Missing backends/results, stale hashes and solver failures return UNKNOWN. External solver timeouts retain partial stdout/stderr in engine.log and the request hash; inspect numerical progress before increasing the test timeout.

The installed numerical implementations and their physical limits are described
in [numerical.md](numerical.md). Backend UNKNOWN/convergence failure cannot be
overridden by measurements within bounds. New adapters must execute real
analysis or import authenticated measurements. A model-authored JSON is not a
trusted backend. Protocol tests do not establish solver physical validity.

## Approved captures

`engine:"approved_capture"` recomputes waveform statistics, sampled settling,
UART/SPI payloads and complex S-parameter magnitudes from original CSV captures.
It needs an operator-reviewed catalog at `/opt/pcb-evidence/catalog.json`, mounted
read-only outside the agent workspace. No captures or approvals are preinstalled.
Use `parameters:{record,conditions,measurements}` and explicit assertions for
every computed metric. Catalog entries bind exact CAD/spec/model/input hashes,
setup and qualification files, conditions, channel units and capture hashes.
Changed CAD, a different operating corner, incomplete windows or missing review
produce UNKNOWN. This adapter processes evidence; it does not collect it.

Waveform measurements declare `capture`, `kind:"waveform"`, `channel`, `unit`,
`operation`, `start`, `end`, `max_interval`. Supported operations are min, max,
mean, peak_to_peak, max_abs_error, undershoot, overshoot and settling_s. The last
four use `target`; settling also needs `trigger_s`, `tolerance`, `hold_s`.
CSV has a strictly increasing `time_s` column in seconds and samples at both
window boundaries. Resolution and bandwidth must suit the physical claim.

UART declares channel, baud, expected_bytes and optional 7/8 data bits, 1/2 stop
bits, no parity. SPI declares clock/select/data channels, cpol/cpha,
expected_words and word_bits/bit_order. Digital channels use unit `logic` and
0/1 values; both decoders compare the whole captured payload and expose errors.
Declare `max_interval_s` for UART/SPI captures. UART requires at least 16 samples
per bit, SPI at least 8 per clock; gaps anywhere in the retained capture must
meet that bound. SPI channels are distinct, CS changes at idle clock, and data
must be resolved before its sampling edge. Sparse or ambiguous captures return
UNKNOWN. In coupled SPICE, decoders inherit the explicitly declared top-level
`max_interval_s` unless they supply their own bound.
SPI byte decoding does not prove SD commands; UART decoding does not prove USB
enumeration or USB-to-UART delivery. Those need an independently qualified
end-to-end harness. USB wire decoding is not implemented here; the scoped
classic CAN capture decoder is described below.

`kind:"sd_spi"` adds full-duplex SPI command/response and single-block read/write
checks from clock/select/mosi/miso captures, mode 0/3 and max_interval_s. Declare
`transactions:[{command,argument,response,response_max_bytes}]`; block commands
17/24 also need block_bytes, payload_sha256, token_max_bytes; writes need
busy_max_bytes. Metrics include CRC, response/payload/transaction errors, block
counts and clock range. This decoder checks command CRCs explicitly and supports
commands 0/8/13/16/17/24/41/55/58/59. It does not infer card initialization or
addressing state: the original reviewed contract must specify the complete
sequence and intended block semantics. Other commands remain UNKNOWN.

`kind:"swd"` takes clock/data channels, max_interval_s, preamble_bits and
`transfers:[{ap,read,address,value,ack?,idle_cycles_before?}]`. It checks ADIv5
request/data parity, OK/WAIT/FAULT responses and captured values with one
turnaround cycle and ORUNDETECT disabled. Address is a 0/4/8/12 register offset.
This is register-transfer evidence; firmware programming/readback, AP posted-read
semantics, boot and oscillator behavior need their own reviewed harness. No
firmware hash or physical test PASS is inferred from valid SWD packets.

S-parameters use `kind:"sparameter"`, real/imaginary channel names, operation
`magnitude_max` or `magnitude_min`, and a bounded frequency window with explicit
max_interval. CSV frequency units are Hz; complex values are dimensionless.
This measures the sampled band only. Loss, de-embedding, enclosure, convergence,
efficiency and measurement calibration need their own approved evidence.

Mechanical checks use `category:"mechanical", engine:"pcb_mechanical"` through the same external-analysis chain. See [advanced-cad.md](advanced-cad.md) for model hashes, named STEP objects and measurement bounds. Mechanical evidence is separate from electrical/thermal/EMC evidence.

Candidate `not_applicable` entries are proposals only. The runtime reports them UNKNOWN until the immutable external contract approves that category; unavailable models/backends/hardware are missing evidence. No analysis plan and empty geometry predicates do not constitute PASS. Behavioral model results cover the declared assumptions; power devices need input/output current and loss checks plus applicable corners before a board-level power claim.

`project_status.analysis_readiness` separates installed backends from missing
inputs. A qualified co-simulation model requires a reviewed electrical-fixture
hash matching the applied stimuli and operating conditions. Merely declaring
an in-range supply does not qualify an arbitrary testbench. Read the model's
scope; transient output behavior alone may omit supply current or temperature.

`kind:"can_classic"` decodes standard 11-bit CAN data frames from RXD logic
captures. Supply `channel`, `bitrate`, `max_interval_s` (at least 16 samples/bit)
and `frames:[{id,data:[bytes],ack:0}]`. It checks full-frame payload, stuffing,
CRC, ACK, EOF and intermission. CAN FD, extended/RTR/error frames, oscillator
drift and transceiver/cable behavior are outside this decoder's scope. A valid
capture is not a model of the selected CAN device or physical-board proof.
