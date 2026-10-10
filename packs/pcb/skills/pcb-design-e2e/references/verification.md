# Verification facts and boundaries

An external `verification_policy` supplies required checks and explicit reasons for excluded checks. It cannot contradict a fixed public test. Missing out-of-scope evidence remains visible in diagnostic layers without blocking that scoped policy; observed failures remain visible and blocking. Policy PASS certifies its named scope, not every diagnostic layer. Required analysis categories match exactly; AC does not supply dynamic evidence and SI does not supply RF evidence. Without a reviewed policy, complete engineering applicability stays unresolved; never infer non-applicability from a task name or overwrite it through candidate intent.

`finalize_claims(test_claims=[{"test_id":"...","conditions_sha256":"..."}])` validates citations against fresh passing tests and returns their actual conditions. It does not certify free-text statements in `remaining_issues` or the final response. A nominal test cannot support a different tolerance, temperature, load or compliance claim. Include the appropriate tests instead. `narrative_status=UNKNOWN` makes this boundary explicit.

Native warnings appear in `feedback.findings` with status `WARNING` even when ERC/DRC has zero errors. They require inspection; PASS does not mean zero warnings. ERC runs in a disposable writable project to retain library resolution for read-only candidates. Wire `length_mm` and `endpoints_mm` come from the native schematic UUID; `native_description` retains potentially mis-scaled CLI text.

Put stable rules in code: pin existence; NC/connect mutual exclusion; typed output conflicts; exact netlist membership; source/report hashes; voltage intervals; current-budget arithmetic. Installed CAD libraries supply symbol inheritance, units and declared pin types. The versioned `tools/data/electrical_rules.json` documents builtin rule sources and has an exact-MPN record namespace.

Keep manufacturer datasheet facts distinct from CAD declarations. A CAD library is not evidence of a device's absolute maximum voltage, thermal limits or recommended operating conditions. Exact-MPN entries require source and retrieval date, full pin facts and checked footprint identity. Add ranges only after reading the applicable datasheet revision. Project voltage, load, clock and testbench conditions remain project inputs.

Schematic/PCB parity compares declared component and pin/pad/net membership. A physical short can coexist with parity PASS; native DRC detects copper shorts and unconnected items under the enabled rules. Neither check establishes required circuit behavior. Verifier results name `check_kind` and limits where available.

PASS applies only to a named scope. Not-applicable checks need explicit justification; absent data is UNKNOWN. Do not propagate PASS from unavailable ngspice, empty XML, missing PCB, stale DRC, net-name-only comparison, or an unfilled zone. A registry record without provenance is not a validated datasheet.

A functional test needs its own models, operating points, excitation and expected measurements. Copper-only continuity is not USB enumeration, UART conversion, regulator stability, I2C timing or RF behavior. Physical layer/thermal quantities are required when making physical claims even though no factory adaptation is installed.

Regression tests must inject real failure conditions: swapped pins with unchanged net names, connected NC, inherited/multi-unit symbols, missing evidence, excess load/current, absent simulation measurements, stale DRC, many violations and incomplete tool execution. Keep logic tests distinct from KiCad/ngspice integration tests. A test using stubs establishes only the control/validation logic.

In a bound task, `verify_design.status` evaluates the immutable public requirements with the same implementation as independent acceptance. `cad_status` covers native CAD checks; `electrical_status` also needs fresh engineering checks and complete analysis coverage. An individual analysis PASS has its own narrower scope. `finalize_claims` records attempted claims and rejects unsupported completion or PASS. Workspace claims are not an authority.

Bound sessions receive a read-only `public_requirements.json`, pinned by the
controller's SHA-256. `project_status` exposes its requirements and scope.
When the fixed contract includes `brief_coverage`, final `task_coverage` reports each source clause and its mapped checks. A missing or uncompiled clause blocks completion even if CAD passes. The model may diagnose missing conditions but cannot replace this externally curated mapping or treat a mapping-readiness result as a simulation result.
Changing `spec.json` changes design intent, not acceptance criteria. Missing or
changed bindings stop verification. The launcher rejects hidden exact-reference
contracts and repair tasks without native initial projects and fresh rejection
evidence. Standalone verification can still accept an explicitly prescribed
external circuit contract.

Declarative requirements check board bounds,
allowed components or component groups, external port connectivity and mandatory
analyses against native files. It must not require a complete reference netlist
unless that connectivity is itself a user requirement. Equivalent resistor
networks, different reference names and permitted extra components can be valid;
external symbol-based model rules cover every actual physical component. Missing
behavior models or unsupported requirement operators remain UNKNOWN.
Malformed requirement types, empty endpoint groups, non-finite numbers and
contradictory bounds also remain UNKNOWN. Fix the external contract before
grading candidates; an invalid contract cannot establish a design PASS.

Verification tools return compact `feedback.findings`: check path, status,
message, native object/position data when available, evidence path and the
verification stage to rerun. FAIL findings precede missing-evidence UNKNOWNs.
Read the saved full report when `omitted_findings` is nonzero. Feedback never
rewrites a verdict, changes the original requirements or edits the board.

External `requirements.connected_pins` lists groups of distinct `ref:pin`
endpoints that must share an actual exported net, independently of its name.
Use `separate_nets` for endpoints that must remain separate. A public reference
schematic can supply a prescribed fragment's connection constraints, with pinned
source evidence; that does not qualify its device ratings or dynamic behavior.
For freely chosen circuits, require the intended interface/behavior instead of
matching the complete reference topology.

`verification_layers` uses a versioned schema for schematic connectivity, component/operating evidence, function/physics, layout geometry, input integrity, delivery files and manufacturing process qualification. Each layer exposes its checks and scope. `export_project(destination)` calls the existing release engine and reports `export_status` separately from `production_status`. Native DRC plus checked CAM exports do not prove process/material compatibility; the factory-neutral environment leaves that final layer UNKNOWN. No task name chooses an acceptance rule.

ERC object positions are resolved by UUID from the current schematic, including
rotation/mirroring and child pages. `pos` is mm only when `position_status` is
RESOLVED; `native_pos` retains the untouched CLI coordinate. UNKNOWN object
positions are unsuitable for coordinate-based edits. A consistent self-filled
`datasheet_facts` declaration without a source-qualified package remains UNKNOWN;
package checks still depend on the correctness of recorded source review.

The pre-export gate defers only delivery generation and final claims, preserving all design and analysis obligations. Final acceptance checks delivery paths and required claims; a pre-export result is not final submission approval. The evaluator checks first-submission snapshots without final claims while preserving the original contract.

Three acceptance tiers are reported separately: design delivery, required simulation, and hardware qualification. The simulation tier requires every independently required simulation for the final revision, not only whichever tests executed. An external test uses `backend="external"` and a registered `engine`. Missing engines remain unresolved; geometry and imported captures cannot be represented as newly executed numerical simulations.

`verify_schematic.checks.schematic_requirements` evaluates the fixed policy at the schematic stage; it is not full board electrical qualification. The top-level `electrical_status` remains UNKNOWN at this stage. `generate_pcb.schematic_requirements_status` reports the generation gate separately.
