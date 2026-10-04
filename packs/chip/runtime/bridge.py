"""Thin adapter over the vendored EDA runtime; never invent tool results."""
import hashlib
import json
from pathlib import Path
import sys

from eda_harness.core.workspace import load_project, scan


def inspect(root):
    project = load_project(root)
    files = scan(root, project)
    inputs = {name: item["hash"] for name, item in sorted(files.items())}
    inputs["eda.yaml"] = hashlib.sha256((root / "eda.yaml").read_bytes()).hexdigest()
    # Engineering stages are derived from declared existing sources, never a prompt.
    return {"inputHashes": inputs, "stage": "rtl" if any(item["type"] == "rtl" for item in files.values()) else None}


def execute(root):
    from eda_harness.core.service import Harness
    harness = Harness(root)
    project = load_project(root)
    config = project.actions.get("rtl.simulate")
    if config is None or config.parameters is None or config.parameters.operation != "simulate":
        raise ValueError("Configure rtl.simulate structured simulation parameters; arbitrary project scripts are not accepted by this Core tool")
    # The existing runtime snapshots real inputs, runs its dependency DAG, owns
    # process cleanup/resource limits, collects CAS outputs and observes assertions.
    run = harness.submit("rtl.simulate", workflow=True, background=False, force=True)
    artifacts = []
    steps = run.get("steps", [])
    for step in steps:
        for identity in step.get("artifacts", []):
            artifact = harness.store.get("artifact", identity)
            harness.store.read_blob(artifact["content_hash"])
            artifacts.append({"kind": artifact["type"], "file": str(harness.store.root / "objects" / artifact["content_hash"]), "sha256": artifact["content_hash"]})
    state = harness.state(run.get("state_id")) if run.get("state_id") else None
    native = (state or {}).get("results", {}).get("rtl.simulate", {})
    return {"executionSucceeded": run["status"] == "SUCCESS", "artifacts": artifacts,
            "toolVersion": run["tool_identities"]["rtl.simulate"].get("version", run["tool_identities"]["rtl.simulate"].get("image_id", "unavailable")),
            "diagnostics": [run["error"]] if run.get("error") else [],
            "nativeRunId": run["id"], "nativeVerification": native.get("verification"),
            "nativeSteps": [{"action": step["action"], "status": step["status"]} for step in steps]}


if __name__ == "__main__":
    request = json.load(sys.stdin)
    root = Path(request["projectDir"]).resolve(strict=True)
    if request["operation"] == "cancel":
        from eda_harness.core.service import Harness
        harness = Harness(root)
        result = {"cancelled": [harness.cancel_run(run["id"]) for run in harness._active()]}
    else:
        result = inspect(root) if request["operation"] == "inspect" else execute(root)
    print(json.dumps(result, allow_nan=False))
