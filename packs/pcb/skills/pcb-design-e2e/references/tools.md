# Workspace operations and data contracts

`tools.workspace` operates on `spec.json` and `board.*` in the current directory. Named calls use one registry and the same argument checks. Use `inspect_tool` to retrieve a signature and output schema during a model session. For operators, `python3 -m tools.api --md` lists the registry; `python3 -m tools.api --groups core,pcb,verification` emits a capability subset; `--internal` and `--include-legacy` describe implementation libraries outside the model execution boundary. This catalog is not a deployed MCP server.

## Operation families

| Purpose | Canonical tools |
|---|---|
| Intent and progress | `new_project`, `set_requirements`, `project_status`, `inspect_tool`, `list_references`, `read_reference` |
| Component discovery and intake | `search_components`, `inspect_symbol`, `lookup_component`, `prepare_component`, `qualify_component`, `install_component`, `discover_artifacts`, `fetch_artifact`, `read_datasheet`, `bind_model` |
| CAD drafts | `create_symbol`, `add_symbol_pin`, `update_symbol_pin`, `remove_symbol_pin`, `create_footprint`, `add_footprint_pad`, `update_footprint_pad`, `remove_footprint_pad` |
| Circuit edits | `add_component`, `remove_component`, `set_component_value`, `assign_footprint`, `configure_component`, `add_power_symbol` |
| Connectivity | `add_net`, `remove_net`, `rename_net`, `connect_pin`, `disconnect_pin`, `add_no_connect` |
| Native schematic edits | `inspect_schematic`, `place_schematic_component`, `set_schematic_field`, `set_schematic_wire`, `set_schematic_label`, `remove_schematic_item` |
| Sheet organization | `add_sheet`, `remove_sheet`, `assign_sheet`, `set_sheet_port` |
| Placement and geometry | `place_component`, `lock_component`, `set_net_rule`, `set_board_outline`, `set_layer_count` |
| Copper and annotations | `add_track`, `add_arc`, `add_via`, `remove_board_item`, `set_track_width`, `add_zone`, `add_keepout`, `add_text`, `set_component_text`, `unroute`, `inspect_board`, `view_design` |
| Engine stages | `generate_schematic`, `generate_pcb`, `update_pcb`, `route_board`, `checkout_board`, `route_differential_pair`, `tune_length`, `fill_zones`, `run_analysis` |
| Focused checks | `run_erc`, `run_drc`, `export_netlist`, `compare_schematic_pcb`, `check_footprints`, `check_component_datasheet`, `check_power_tree`, `check_interface` |
| Stage and final gates | `verify_schematic`, `verify_design`, `export_project`, `finalize_claims` |

The Python extension `run_python` belongs to the session adapter. There is no public batch/action dispatcher, alternate move entrypoint or implicit whole-design agent. Multiple model tool calls execute in listed order with separate results and receipts. Internal transactions share implementation without exposing duplicate tools.

## Mutation contracts

Coordinates and dimensions are mm; rotation is degrees; pin/pad identifiers and item UUIDs are strings. Top-level schemas reject unknown arguments; supported geometry atoms validate numeric values and enums. Nested object coverage varies by tool; inspect its contract. Authoring rejects invalid geometry before atomic replacement. Other multi-file actions require inspection after interrupted execution. PCB/spec replacements are individually atomic with archived originals; a process crash between replacements still needs inspection.

`place_component(ref,x_mm,y_mm,rotation?,side?)` updates intent and an existing PCB. Omitted rotation/side are preserved. `lock_component` applies to both. Movement retains tracks: inspect affected nets and reroute. `inspect_board(include_copper=true)` returns actual pad anchors, poses, lock states, copper UUIDs, track endpoints/widths and via dimensions. `remove_board_item` affects one UUID; `set_track_width` accepts a track UUID only.

`inspect_board(collection="copper"|"pads"|"parts"|"drawings", limit=10)` returns complete `items` pages, sorted by UUID. Follow `next_call` to preserve filters and `expected_board_sha256`; a changed board rejects an old cursor. Optional `net_name`, `layer`, `ref` (parts/pads) and `bounds_mm=[[xmin,ymin],[xmax,ymax]]` select candidate objects. Bounds intersect native axis-aligned envelopes, not exact copper contact. Part pages omit nested pads: use their `children_query`. Overview omissions are identified outside object arrays; a missing entry is not evidence of absence.

Use the output schema from `inspect_tool("inspect_board")`: tracks have `start/end/width_mm`; vias have `at/diameter_mm/drill_mm/layers`; pads have `xy/size_mm/rotation_deg/layers`. Arcs also have `mid`. Do not apply endpoint calculations to every copper kind or infer full contact/continuity from centre distances. Zones currently expose bounds and fill state, not an exact polygon-contact query.

`inspect_board(include_geometry=true)` adds board/footprint bounds, pad sizes and
rotations, courtyard bounds and reference/value text geometry from the actual PCB.
These are observations, not an aesthetic score or collision verdict. See
[layout.md](layout.md) for placement and review using them.

`add_track(points=...,net_name=...,width_mm=...)` creates one continuous path. Legacy x1/y1/x2/y2 arguments are retained for a single segment; do not mix them with points. Exact duplicate endpoints (either direction), layer, net and width return `ALREADY_PRESENT` without changing the file. Use measured copper counts/lengths rather than successful call counts. `unroute(net_name)` clears that net's tracks/vias; an empty name clears all tracks/vias. Zones remain.

`set_board_outline(points)` replaces all Edge.Cuts with one straight polygon, including existing cutouts. `set_layer_count` rejects reductions while internal copper remains. Zone geometry and thermal parameters are explicit; `fill_zones` is a separate engine action. `add_arc(start,mid,end,net_name,width_mm,layer)` creates one native copper arc and is subject to the same UUID, stale-revision and DRC rules as tracks. Blind/buried via creation is still intentionally unavailable until layer-pair and fabrication-rule validation is implemented; the calculation sandbox cannot supply these edits.

Other circuit edits change intent only: regenerate the schematic, then `update_pcb`. Full PCB overwrite archives existing files and discards derived routing. `configure_component` can revise the symbol while preserving used pin numbers, and edits MPN, operating conditions, required operating checks, sourced datasheet facts and mechanical-pad declarations; value/footprint/placement have their own tools.

## Intent and checks

Spec contains `board:{w,h,layers}`, `parts`, `nets`, `interfaces`, `power_tree`, optional `constraints`, `analysis` and `postroute`. A part uses `ref`, `symbol`, `value`, `footprint`, `nets:{pin:net}`, `no_connect:[pin]`, optional `at:[x,y]`, `rot`, `side`, `locked`, `pad_map`, `mpn`, `datasheet_facts`. `pad_map` explicitly maps symbol pins to physical pads; never rename footprint pads to satisfy a checker.

`set_requirements(section,value)` changes one model-intent section, never the immutable task requirements returned by `project_status`. Large documents return an explicit section index. `project_status(requirements_path="/requirements/analysis/tests",offset=0,limit=5)` pages public tests; each entry's pointer retrieves the exact test. Follow `next_call` while `has_more`; `page_complete` means this page's entries are unabridged, while `section_complete`/`complete` means the entire section is in this one response. `project_status(check_stage="analysis",offset=0,limit=10)` pages test statuses and condition hashes; choose a stage listed in `checks` and check `fresh`. No private evaluator path is accepted. `power_tree`/`interfaces` take `{items:[...]}`. `constraints.board_rules` needs positive `clearance_mm`, `track_mm`, `via_mm`, `drill_mm`, `edge_mm`, `hole_mm` for standalone routing. Bound verification applies the public rules independently. Per-net overrides use `set_net_rule(net,rules)` with `track_mm`, `clearance_mm`, `via_mm`, `drill_mm`, `dp_width_mm`, `dp_gap_mm`. Net roles: signal/power/ground/chassis.

`power_tree` entries: `{net,voltage_v,current_limit_a,loads:[{ref,current_a,voltage_min_v,voltage_max_v}]}`; linear rails may add `{input_net,kind:'linear',dropout_v}`. This checks declared DC arithmetic, not transient or thermal behavior.

`interfaces` entries: `connections:[{ref,pin,net}]` with optional voltage ranges, and `required_components:[{ref,value,nets}]`. Empty interfaces mean none declared, not protocol compliance. `postroute` can constrain net length/via count, placements and zones; `{}` explicitly selects no additional postroute constraints. See [analysis.md](analysis.md) for circuit-bound function/stability/EMC plans.

Focused checks aid diagnosis; stage gates aggregate their existing implementations. Final acceptance reruns checks with original requirements, regardless of prior tool reports. Export helpers remain in `tools.kicad_cli`; `tools.release.export_bundle` requires fresh verification and writes a revision-bound deliverable directory without factory adaptation.

## Exact-MPN intake

`inspect_symbol(symbol)` reads CAD pin declarations. `lookup_component(mpn)` reads sourced commercial-device facts. These are different evidence types.

`prepare_component(facts,cad_sources?,documents?,models?)` stages one MPN under `.pcb/intake`. Required facts: `mpn`, `symbol`, `footprint`, `pins`, `source`, `retrieved_at`. Qualification also requires recorded `review:{reviewer,reviewed_at}` and `citations:{field:{document,sha256,locator}}` for MPN, pins, footprint and claimed ratings. A download cannot establish the correctness of reviewed facts.

`cad_sources` maps `symbol`/`footprint` to `{url,revision,license_url,expected_sha256?}`, or for a workspace-authored draft to `{path,sha256,license}`. Draft paths must match the requested CAD identity under `.pcb/authoring`; a draft license concerns authored CAD, not the manufacturer document. Omit an entry to reuse installed CAD with provenance. `documents` contains `{name,download:{url,revision,license_url,expected_sha256?}}`. `qualify_component(package)` validates evidence and native loading; `install_component(package)` requalifies before publication. Both require a staged path. New processes discover installed packages automatically.

`facts.pins` is an object such as `{"1":{"name":"IN","type":"input"}}`, not an array. Citation `document` paths are relative to the staged package, e.g. `documents/device.pdf`; use the same filename in `documents[].name`. Errors name the offending CAD kind or field. System library paths are not authored drafts: omit that kind from `cad_sources` to reuse installed CAD. A draft source accepts exactly `path`, `sha256`, `license`, without extra provenance keys. `fetch_artifact.destination` must be `downloads/<filename>`; `kind:"datasheet"` accepts PDF, not license text.

`fetch_artifact` downloads one symbol, footprint, PDF or flat SPICE model with provenance into `downloads/`. It requires a known HTTPS URL; this is not a web search engine. `search_components(query,limit,pins?,pads?)` searches installed CAD only. The immutable runtime index accelerates unchanged libraries; source inventory changes invalidate it. `read_datasheet(path,first_page,max_pages)` extracts at most 10 PDF pages and 20,000 characters, with source hash. It cannot interpret diagrams or authenticate extracted facts. A ZIP source needs `archive_member` and matching `archive_sha256`; `expected_sha256` applies to the selected member. Only that bounded member is read, without executing/extracting the archive. Original model terms remain intact.

Optional `models` entries are `{id,name,download,definition}`. `definition` contains `binding`, `mapping_source`, `scope`, and `testbench:{nets,no_connect,tests}` for a single physical U1. Binding uses the [analysis](analysis.md) schema; acquired file/source/hash are supplied by intake. Testbench tests must execute SPICE function assertions and cover every functional unit. Qualification runs them with the native KiCad netlist and ngspice; installation and `bind_model(ref,model_id)` requalify. Unqualified analysis modes stay UNKNOWN.

Official library installation and catalog automation remain internal (`tools.library`, `tools.intake.ensure_component`); they are not duplicate model tools. No reference-answer libraries belong in default search paths.

`list_references()` lists installed reference filenames and hashes; `read_reference(name,start_line,max_lines)` reads at most 400 lines within that directory. `inspect_tool` returns relevant reference filenames. These operations do not traverse project trees or system directories.

`finalize_claims(cad_status,electrical_status,completed,remaining_issues,scope)` reruns final verification, records the submitted claim, and writes `claims.json` only if the claim is supported. Default scope is engineering; an explicit task may permit cad_prototype completion with electrical UNKNOWN, provided its required analyses pass. A passing individual analysis belongs in the summary, not in a global electrical PASS. The independent evaluator enforces the original scope and requirements, even if a workspace claim uses another scope.

### Authored CAD atoms

`create_symbol(library_id,pins,description)` and `add_symbol_pin(library_id,pin)`
use string `number`, `name`, `electrical_type`, `x`, `y` (mm), and optional `angle`
(0/90/180/270), `length` (positive mm), `unit` (positive integer). Pins must be
explicitly typed from the datasheet. An empty initial list is allowed for a draft.

`create_footprint(library_id,pads,description)` and `add_footprint_pad(library_id,pad)`
use string `number`, `x`, `y`, positive `sx`, `sy` (mm); optional `shape` is
rect/circle/oval, `type` is smd/thru_hole/np_thru_hole, `angle` is degrees, and
`side` is front/back for SMD. Through holes need a positive `drill`; plated holes
need positive annular copper. Non-plated holes have an empty electrical number.

Drafts are excluded from installed-library lookup until qualification. This is a
limited authoring interface: no custom pads, slot holes, graphics, courtyards or
3D-body assignment. `update_symbol_pin`/`update_footprint_pad` replace one complete
definition at its existing number; `remove_symbol_pin`/`remove_footprint_pad` delete
one unambiguous numbered item. Changes affect drafts and invalidate old draft
hashes, not installed packages or board instances. Do not claim these operations
exist or that native loading alone qualifies package dimensions.

For nested `power_tree` or `interfaces` declarations, read
`inspect_tool("power_tree")` or `inspect_tool("interfaces")` before
`set_requirements(section, {"items": [...]})`. Rail entries identify `net`;
loads use `ref`, `current_a`, `voltage_min_v` and `voltage_max_v`, and rail
budgets use `voltage_v` and `current_limit_a`. Interface `connections` use
string `ref`, `pin`, `net` and optional voltage bounds. These declarations
check DC budgets and pin/voltage predicates, not protocol behavior. Malformed
legacy declarations should be corrected at the reported field path; changing
metadata alone does not require regenerating the schematic or PCB.

`add_component` has no model-defined pin map. Use `inspect_symbol` for canonical pins and `connect_pin` for connections. Partial legacy metadata cannot hide real library pins. Native schematic tools edit the actual sheet, preserve other objects, and invalidate checks. Pose/field edits reject unexpected netlist changes; explicit wire edits require ERC and intent parity.

`checkout_board(candidate,expected_board_sha256,expected_candidate_sha256)` accepts a retained router candidate or its rollback path only when dependencies and physical circuit/placement match. It returns MODIFIED/verification REQUIRED, never engineering PASS. Run DRC after checkout to locate the active version. `remove_board_item` accepts an optional expected board hash to reject stale UUID edits.
