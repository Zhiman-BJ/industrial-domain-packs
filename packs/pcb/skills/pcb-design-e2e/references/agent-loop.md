# Model loop and recovery

The session adapter exposes canonical `workspace.definitions()` plus `run_python`. Each call names a specific operation and receives its own observation, artifact changes and receipt. Multiple calls in one response execute in order; they are not an all-or-nothing transaction. Wait for a prerequisite's result before dependent repairs. Python extensions start fresh, read-only candidate processes; persist calculation output only in the supplied scratch directory.

For bound tasks, `project_status` returns the public acceptance document and its SHA-256. Both interactive stage checks and independent final acceptance use those requirements. `spec.json` is model intent, so `set_requirements` cannot change task tests, waivers or acceptance scope. Repair tasks must start with a real candidate schematic, PCB and project; a missing seed is a launcher error. Checkpoints also pin the public requirements hash.

The design path is intent → schematic/ERC/netlist/electrical checks → footprint/PCB parity → placement review/routing/copper fill → final verification. Placement and routing may iterate: use the [layout reference](layout.md) to inspect geometry and revise the arrangement before treating generated packing as final. `run_analysis(stage="schematic")` defers PCB-dependent analyses; final analysis uses actual PCB geometry, including declared mechanical assembly checks. Prepare missing 3D models before checking mechanical constraints; placement changes invalidate both electrical and assembly evidence. Focused checks isolate causes; `verify_schematic` and `verify_design` aggregate the same implementations.

Existing schematics require explicit overwrite to regenerate; old root/child files are archived. After circuit edits, use `update_pcb` to preserve unaffected routing and inspect its affected-net report. Full PCB regeneration discards routing. Placement/lock/outline/layer tools update intent and an existing PCB. New components can be placed in intent before ECO. Reports include child-sheet and model hashes; `project_status` marks mismatches STALE. Acceptance reruns checks after all edits.

Freerouting adopts only a DRC-passing candidate. Use `checkout_board` with its candidate path, candidate hash and current board hash to continue a failed route. It checks dependency/circuit preservation and returns a rollback candidate. Run DRC on the active version before using reported UUIDs. Checkout is not acceptance. Repeating identical geometry is not progress. The separate `tools.loop` is a fresh-build utility, not the agent repair loop. It refuses to repeat a build into an existing attempt tree unless `--fresh-build` is explicit. Work in the retained candidate with named tools for repairs.

## Local repair after a failed check

Use the current report's failing condition and located objects before choosing an
edit. Identify the reference/pin/net or item UUID, sheet/layer and verified mm
position where provided. `project_status.repair_context` retains recent findings
and report paths across context compression and checkpoint resume. STALE locations
must be refreshed. If objects are absent, inspect the full report, actual board or
exported connectivity; do not invent coordinates or replace the whole design.

State the suspected cause and the limited change being tested. Copper/placement
faults usually need only item or component edits, followed by DRC. Circuit-intent
changes need schematic regeneration and checks, then `update_pcb` and DRC; its
default preserves existing-net copper even on affected nets. Read the affected-net
and removed/retained-item result. `prune_affected=true`, net-wide unroute and PCB
overwrite are broader operations requiring an engineering reason in the trace.
A genuinely global topology or layout failure may justify redesign; one local
violation does not establish that.

After editing, compare the relevant measurement/violation and inspect newly
introduced errors. A lower error count is insufficient if necessary connectivity
was deleted. Keep independent fixed requirements and final checks intact. UNKNOWN
from missing models or test evidence needs that dependency, not a board rebuild.

Legacy direct-loop defaults: 36 turns, 3600 seconds, 900 seconds per API/engine request, four HTTP attempts, and 120 seconds for Python extensions. Documentation reads use the bounded reference tools. CLI flags adjust these values; requests are bounded by remaining session time. HTTP timeout/429/selected 5xx failures may retry. Mutations never retry automatically. A timed-out tool process group is stopped; inspect persisted artifacts before retrying. Checkpoints bind to the tool-schema hash; resume an older session with its original frozen image. Receipts resume completed actions without replay. A started receipt with unknown outcome blocks mutations as `AMBIGUOUS_TOOL_OUTCOME`. On a still-connected MCP session, `project_status`, `inspect_board`, `inspect_schematic` and schema/reference reads remain available; they retain the pending record. Controller reconciliation is required before edits or a stopped harness can resume. Do not delete receipts or infer success from an inspection. PCB/spec file replacement can be interrupted between files.

Every session retains prompts, responses, actions, scripts, complete observations, inputs, hashes and reports. Large feedback preserves key statuses and candidate report paths; read the candidate `report_path` or `attempt_path` when omitted evidence matters. Returned `action-*.stdout` and `action-*.stderr` paths are readable by `run_python`; other controller records remain private. For argument errors, use `input_error.field`, `received`, `expected` and `remediation` to correct the offending field while retaining valid arguments. `operation_executed=false` identifies a pre-execution rejection; otherwise inspect `operation_outcome` before retrying. Expected values describe the public API or current candidate identifiers, not a reference answer. Use `schema_hint` for additional structure and pass native JSON values. Termination and `DONE.md` are not acceptance. Keep failed trajectories; reduced DRC counts are not a safe reward if necessary copper or requirements were removed.

The Docker launchers run tools under a separate unprivileged identity. Candidate
CAD remains editable through named operations; controller-owned `session/` records and first-submission
snapshots cannot be rewritten or replaced by tools. Python calculations can read returned tool stdout/stderr but cannot read private controller records or verifier source, change CAD, spawn processes or use the network. Read generated `.pcb` reports for detailed verification feedback. The host
checks recording ownership and permissions before copying results. Standalone
sessions without `--tool-user` do not provide this recording isolation.

Provider fields such as `enable_thinking` use the session CLI's `--request-options`
JSON object. The operator freezes them with the run; resumed sessions must preserve
the same options. Trajectories retain provider-returned reasoning fields and usage
when supplied; requesting a thinking option alone is not evidence it was enabled.

The supervisor runs independent acceptance in a separate container against original requirements outside model-writable state, without API credentials or network. Required analysis categories cannot be removed by editing the workspace. FAIL blocks acceptance; missing facts, models or solvers remain UNKNOWN. Explicit CAD-prototype authorization permits only that scope and does not grant functional PASS.

Optional object/array parameters use omission for defaults; when included, pass a native JSON object/array. A parameter rejection establishes the received type, not whether the model or provider parser caused it. Describe the observed error and correction; claiming a transport defect requires a separate protocol comparison.

Call IDs are retained even when argument decoding fails. Non-normal termination retains an explicit stop reason; a missing model final answer is never synthesized as model success. Claim attempts and final acceptance are recorded independently.

Subprocess status, persisted mutation status and design verdict are recorded separately. `CHANGED_REVIEW_REQUIRED` forbids blind replay: inspect current objects, compare hashes and rerun the affected native check. A demo pause is acknowledged only at a tool boundary through the trusted controller; failure/timeout leaves it retryable and does not imply a pause.

For Kimi/Orbit MCP sessions, the controller configures `PCB_TOOL_TIMEOUT_SECONDS` (default 900, maximum 86400) and `PCB_PYTHON_TIMEOUT_SECONDS` (default 120, maximum 3600). Client waits include a 60-second receipt margin. These budgets are frozen in runtime identity; per-test solver timeouts and the overall session deadline still apply. Request a larger controller budget before starting a long simulation; tool arguments cannot extend it.

New Kimi Code sessions reconcile interrupted MCP operations during explicit
resume. A process-held lease rejects recovery while an orphaned tool is active.
The controller checks the frozen identity, retains original receipts, reads the
current CAD under the tool identity and supplies a recovery report. Recovery is
not ERC/DRC acceptance: run affected checks and repair in place. Unreadable or
missing sources retain the pending journal. Older journals still need their
original frozen runtime and recovery procedure.

Kimi Code transport uses a 600-second request total deadline and a 120-second stream idle limit. Native context compaction has a separate 180-second deadline and 4096-token output budget, with thinking enabled. Deadlines also reserve part of the remaining session budget for recovery. Incomplete compaction output is rejected; transport failures retain request IDs, phases and byte timing. These limits are controller settings, not tool arguments or evidence of a completed check.
