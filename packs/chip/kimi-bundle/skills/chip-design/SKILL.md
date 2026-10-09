---
name: chip-design
description: Design, simulate, synthesize and inspect chip projects with the Chip Domain Pack's native EDA MCP. Use for RTL, formal equivalence, PPA, physical design, tool setup and recovery of long-running chip experiments.
---

Use the connected `chip` MCP server directly. Kimi exposes its tools as
`mcp__chip__<tool>`. Call `mcp__chip__get_tool_guide` for the maintained tool
contracts and `mcp__chip__get_server_info` for the observed server identity.
There is no additional Harness gateway or industrial_action_call in this bundle.

Read `${KIMI_SKILL_DIR}/runtime.json` for the installed tool image and its observed
ID. A null image means the installer explicitly skipped native tools; report that
before claiming the environment is ready. For a new project, pass this image
explicitly to `check_environment` and `initialize_project`. Existing projects use
their own `eda.yaml`; do not silently replace its runtime or image.

Pass the intended absolute `project_path` on every project tool call. The MCP has
no default project. The current working directory supplied by the task is a usable
project path; ask only if the task leaves the intended project ambiguous. Never
initialize the user's home, replace an existing project or infer that an old
conversation's path is the new task's path. State/run/artifact IDs are scoped to
the originating project.

For a new project, check the environment first, then initialize the intended
dedicated project and top. `PROJECT_MISSING` before initialization is expected;
environment readiness is separate from project readiness. Do not diagnose Docker
tools using host `command -v`. Optional host viewers do not block container runs.
Initialization does not supply design inputs, a PDK or a signoff recipe. Configure
the requested inputs and flow, then check the target's environment. Select local
execution only when the user explicitly requests it.

Recover existing work with `get_operational_context`: persisted goals, active
runs, input hashes, baselines and evidence survive conversation compaction and
process restarts. Edit RTL, scripts and `eda.yaml` with Kimi's native file/shell
tools. Inspect `workspace_status` after changes. Submit formal computation with
`run_action` or `run_until`; both return a run ID promptly. Poll `get_run` with an
appropriate delay. Do not hold an MCP request open for the full computation or
submit duplicate work after a transport timeout. `run_until` builds prerequisites
from a frozen snapshot; `run_action` requires valid dependency evidence.
Use native `Bash` waits between status queries (for example `sleep 10` or longer
for slow flows), or work on other requested tasks. Rapid identical polls produce
no new engineering work and can trigger Kimi's native repeat breaker. Keep that
guard intact; adjust waiting and polling to the actual computation.
Before a long computation, inspect `runtime.resources` in `eda.yaml` and choose
CPU, memory, build jobs and `timeout_seconds` appropriate to the task. The Pack's
default action deadline is 600 seconds, separate from the unbounded Kimi turn.

Use `create_goal` for the task's metric constraints, units and required verification.
For percentage improvements, retain a measured baseline. Compare experiments with
`compare_states` and record experimental facts with `record_decision`. Historical
evidence proves only its original inputs; changed working files need new evidence.
Only check out a state when the task calls for it; forced checkout replaces edits.

Inspect diagnostics, artifact IDs and bounded reports. `SUCCESS` means the process
ran; it can accompany design `BLOCKED`. `INCOMPLETE` is missing evidence, never a
pass. Claim completion only when the configured goal's current-input acceptance is
`PASS`. A timing result does not establish hold closure, DRC/LVS, equivalence or
foundry signoff. Never omit requested checks to make an experiment appear complete.

Check `tool_capabilities` before selecting operation parameters. For generated
Verilator builds, set `build_jobs: 1`; CPU quotas do not cap make workers. Inspect
execution and tool logs after crashes, OOM or proof failures. If cleanup is pending,
restore the execution environment and call `recover_runs` before retrying. Use the
Pack's execution path for EDA computation so it retains resource and native evidence.
An interrupted Kimi conversation does not necessarily cancel an asynchronous EDA
run; explicitly use `cancel_run` when the task requires cancellation.

For structured equivalence, provide real gold/gate inputs and matching cell models
or libraries. Partial, empty, crashed or timed-out proof is never `PASS`. Require
the configured rule coverage before drawing a design conclusion.

Kimi owns conversation persistence, context compression, approvals and subagents.
Keep the task's actual tool inputs/results in its native session. Do not construct
replacement histories or summarize evidence as if it were an original observation.
