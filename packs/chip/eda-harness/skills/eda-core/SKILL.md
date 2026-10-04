---
name: eda-core
description: Operate an EDA Harness project through shared MCP tools, including persistent design states, formal tool runs, PPA comparison, and recovery after context loss. Use for EDA setup, installation checks and project initialization, including directories without eda.yaml.
---

First call `get_tool_guide` (or read `eda://manual/tools`) for tool contracts, examples and errors.
For setup or environment questions, call `check_environment` before initialization, even without
eda.yaml. Supply `image` if the user has a specific candidate Docker tool image; otherwise the
reported default candidate is eda-harness-tools:dev. A successful call confirms MCP connectivity.
Report environment_ready separately from project readiness: PROJECT_MISSING is expected before init.
Do not use host `command -v yosys/openroad/verilator` as evidence of Docker tool availability.
Host viewers are optional; missing GTKWave does not block container execution. Do not invent an
iverilog requirement. Existing projects must be checked using their configured runtime.
If the MCP tool is unavailable, report that connection/version problem before claiming readiness.
Initialization order:
1. Confirm the plugin/MCP is installed and connected by calling get_tool_guide. If unavailable,
   report the installation/connection issue. A working MCP needs no separate global Harness install.
2. Call check_environment to check Docker and the candidate image. Default to Docker; never
   switch to local execution merely because local EDA binaries happen to exist.
3. With a user-selected dedicated project root and top, call initialize_project through MCP.
   It rechecks the Docker environment and creates eda.yaml only when ready; do not invoke uvx
   separately to fetch CLI help or schema for this supported initialization path.
4. Configure the requested design inputs, PDK and flow; call check_environment(target=...).
   A new eda.yaml alone is not a runnable or PDK-ready project.
5. Report optional host viewers separately. Missing viewers do not block initialization,
   simulation, synthesis or verification in the image; they limit native interactive display.
Only use local=true when the user explicitly selects local computation; then host execution
binaries are required. Do not automatically install software or pull images during diagnosis.
Pass the intended absolute project_path to every project tool, including initialize_project.
The plugin has no startup-directory binding. Never infer a project from the Claude working
folder or an old conversation. Ask for the intended directory if it is unknown. PROJECT_REQUIRED
means no path was supplied; PROJECT_NOT_INITIALIZED means that explicit path has no eda.yaml.
Confirm it is a new project before initialization; do not create a duplicate to silence an error.
Different calls may target different projects without reconnecting. Never initialize the user's home.
State/run/artifact IDs are project-scoped: always use the originating project's path.

Recover the current task with `get_operational_context`. Treat its persisted goal, baseline,
working-copy hashes, active runs, and acceptance evidence as the source of EDA state.
A new conversation or compaction does not require recreating a goal or restarting a run.

Use normal editor, search, shell, and Git tools to modify the project. Submit formal EDA
computation with `run_action` or `run_until`. Inspect `workspace_status` after input changes.
`run_action` requires valid dependencies; `run_until` builds the dependency DAG from one
frozen snapshot. Both return a run ID immediately. Poll `get_run` at an interval appropriate
to the tool rather than keeping the MCP request open. A closed agent session does not cancel a run.

Use artifact types and evidence IDs to locate results. Historical states retain valid evidence
for their own frozen inputs; that evidence may be stale for the current working copy.
Read bounded reports only when their diagnostic summaries are insufficient.

Persist the user's metric constraints with `create_goal`; include units and the required
verification actions. Set an explicit baseline with measured area for percentage-growth goals.
Use `compare_states` to compare experiments and `record_decision` for concise experimental facts.
Check out a clean state to branch. Forced checkout captures a recovery snapshot but should only
replace edits the user intends to discard.

Execution `SUCCESS` means the process ran successfully. It can accompany design `BLOCKED`.
Acceptance `INCOMPLETE` means evidence is missing or has incompatible units, not success.
Claim completion only for the configured goal when current-working-copy acceptance is `PASS`.
A setup timing pass does not imply hold closure, DRC/LVS clearance, equivalence, or foundry signoff.

## Bounded execution and proof failures

These patch features require server version 0.6.1 and the rebuilt image.
Inspect get_server_info and get_tool_capabilities first; do not assume an older pinned
remote plugin exposes these parameters.

Submit native computation through run_action/run_until, including custom project scripts.
Raw shell/Docker execution bypasses runtime resource accounting, cleanup and verification.
Use build_jobs: 1 for large generated C++; a CPU quota alone does not cap make workers.
The patched image caps make/Verilator jobs, and the runtime records actual Docker capacity.
Do not overlap resource-heavy retries or infer OOM from Error 247 without saved Docker evidence.

Inspect report.execution and log.tool after SIGILL, compiler failure, timeout or formal failure.
If cleanup_pending is set, restore Docker and recover_runs before resubmitting. A transport
timeout can leave an asynchronous worker running; look up the existing run first.
Do not skip timing repair, LEC or memory checks to claim acceptance. The patched ORFS image
uses a real Yosys LEC callback and keeps functional IHP cells in the exported proof netlists.

Use structured operation: equivalence with both gold/gate sources and real cell_models or
cell_liberties. memory_mode: map supports SAT. Native-memory SMT flows require a separately verified
custom script and real matching memory models; they are not a structured adapter mode. EQY collects matched nets together to
avoid unsafe memory cutpoints, runs one worker, and applies strategy plus action deadlines.
A partial, empty, crashed or timed-out proof is never PASS. Check get_tool_capabilities for
the exact project schema and require full proof evidence before updating design conclusions.
Inspect the exact required checks and rule coverage before describing the result.


For repeated reconnect, stale-version or wrapper concerns, first call get_server_info. Compare
package version, source_commit evidence, session_id, project root, effective viewer paths and
available_tools against the intended configuration. Launcher declarations are not independently
verified; a missing commit is unknown, not proof of an old server. Do not infer launch history
from process names: serve.sh uses exec and wrappers can disappear from the process list.
Do not create replacement wrappers, edit plugin cache files or kill processes as a default fix.
Have the user reload plugins via /reload-plugins, then inspect /mcp and get_server_info again.
If environment variables changed, restart Claude from the intended environment. Investigate
conflicting manual/plugin registrations if the wrong service remains. Only terminate a process
when evidence identifies its owner and the user has authorized that specific interruption.
If get_server_info is unavailable, inspect get_tool_guide and connection/version information;
do not repeatedly call a missing tool or substitute an unverified PID diagnosis.
