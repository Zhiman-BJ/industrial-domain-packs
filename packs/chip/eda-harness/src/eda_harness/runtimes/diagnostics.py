"""Classify evidence without guessing OOM from an exit code or accepting partial proofs."""

import re


def diagnose(execution, text, action):
    findings = []

    def add(code, summary, next_step):
        findings.append({
            "category": "execution", "severity": "high", "summary": summary,
            "evidence": [], "details": {"code": code, "action": action, "next_step": next_step},
        })

    state = execution.container_state
    resource_error = re.search(r"EDA_RESOURCE_(?:BUSY|CPU|MEMORY):[^\n]+", text)
    if resource_error:
        add(resource_error.group().split(":", 1)[0], resource_error.group(),
            "Inspect/recover the existing run, or increase actual daemon capacity before retrying.")
    if execution.cleanup and not execution.cleanup.get("confirmed"):
        add("CONTAINER_CLEANUP_PENDING", "Docker container deletion could not be confirmed",
            "Restore the Docker connection and call recover_runs before submitting another run.")
    if state.get("Running"):
        add("DOCKER_CLIENT_DISCONNECTED", "Docker client exited while the tool was still running",
            "Inspect the saved container state and cleanup result; do not repeat the mutation blindly.")
    if state.get("OOMKilled"):
        add("CONTAINER_OOM", "Docker reports that the container was OOM-killed",
            "Increase actual Docker daemon memory or reduce compilation/proof concurrency.")
    if re.search(r"illegal instruction|\bSIGILL\b", text, re.I) or execution.returncode in (-4, 132):
        add("ILLEGAL_INSTRUCTION", "Tool failed with an illegal instruction",
            "Verify the image CPU architecture and native CTS/repair tests; do not skip timing repair.")
    if re.search(r"internal compiler error|cc1plus.*(?:Segmentation|killed)", text, re.I):
        add("COMPILER_CRASH", "The compiler crashed while building generated simulation code",
            "Use build_jobs: 1 and split generated C++; verify a native CPU/toolchain environment.")
    if "Error 247" in text:
        add("ORFS_EXIT_247", "ORFS reported Error 247; its cause is not established by that code",
            "Use the saved Docker OOM/exit state and stage log to distinguish kill, tool crash and timeout.")
    if re.search(r"conflicting matches|Failed to partition|partition.*(?:returncode=-|crash)", text, re.I):
        add("FORMAL_PARTITION_FAILURE", "Formal partitioning failed",
            "Use explicit gold/gate models and the bounded equivalence adapter; inspect matching conflicts.")
    if re.search(r"No SAT model.*\$mem|No such command: memory_nomap", text):
        add("FORMAL_MEMORY_MODEL", "The formal flow has unsupported or incorrectly prepared memory",
            "Use memory -nomap or memory_map, with identical real memory models on both sides.")
    if re.search(r"No SAT model.*(?:\$adff|sg13g2_)", text):
        add("FORMAL_CELL_MODEL", "The solver has no model for an asynchronous flop or technology cell",
            "Normalize asynchronous flops and supply functional cell models before proving equivalence.")
    if execution.status == "TIMEOUT":
        add("EXECUTION_TIMEOUT", "Tool exceeded its configured execution deadline",
            "Inspect partial logs and resource demand; a timeout never establishes proof or acceptance.")
    if not findings and execution.status != "SUCCESS":
        add("TOOL_EXECUTION_FAILED", f"Tool execution ended with {execution.status}, exit {execution.returncode}",
            "Inspect the immutable tool log and execution report before retrying.")
    return findings
