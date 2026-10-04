---
name: chip-eda-operate
description: Use the registered Chip Pack MCP to inspect, initialize and operate the active EDA project, recover persisted context, and evaluate evidence after engineering runs.
---

The Harness binds this MCP gateway to the current project. Do not supply another project_path.
Start with domain_tool_list to discover the current allowed canonical IDs. Use domain_tool_describe
for the selected tool schema, then domain_tool_call with its canonical toolId and arguments.
Upstream names such as run_action map to eda.harness.run_action. Only listed tools are available;
a missing operation requires a new task/capability scope, not a shell bypass.

First read eda.harness.get_tool_guide, then eda.harness.get_operational_context. For an uninitialized
project, check eda.harness.check_environment before eda.harness.initialize_project. Missing Docker,
image, PDK or inputs are separate from MCP connectivity. Do not install software, pull images, or
switch to local execution during diagnosis. Initialize only a user-intended dedicated project.

Recover existing goals, baselines, working-copy hashes and active runs from persisted EDA context.
Submit computation through eda.harness.run_action or eda.harness.run_until. Their returned run ID
identifies an asynchronous run; inspect eda.harness.get_run and acceptance evidence afterwards.
The MCP transport can close while an EDA run continues. On submission timeout, inspect existing
runs before retrying; the gateway never automatically retries mutations.

The patched runtime defaults to one managed action per Docker daemon and build_jobs: 1.
Do not launch raw EDA tools or Docker containers in shell to bypass that boundary.
After failures, inspect report.execution and log.tool. Error 247 alone does not establish
OOM. A cleanup_pending run retains its container and budget; restore Docker and recover_runs
before resubmitting. Do not skip CTS repair, LEC, memory modeling or failed partitions to
claim PASS. Structured equivalence supports mapped memories, real functional models,
bounded single-worker EQY and complete proof; native-memory SMT requires a separately
verified custom flow. Query the actual server schema before configuring an action.

Use metrics, diagnostics and artifact IDs from that project's EDA state. read_artifact supports
bounded pagination. Large gateway responses are retained in a session-local response cache and
returned as paged text; use domain_tool_result_read with the returned responseId and offset.
Never infer engineering PASS from a Viewer, a process exit code, Core file observations, or a
successful MCP response. Verify the configured checks and current-working-copy acceptance.

Goal creation, checkout, decisions, initialization, submission, recovery, cancellation and external
viewer launch are mutating operations. They use the normal Kimi MCP approval path; the CLI's
approval policy applies. A direct EDA worker remains responsible for execution and verification.
