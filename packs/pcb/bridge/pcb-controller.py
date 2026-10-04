"""PCB Domain Runtime launch adapter; delegates all CAD actions to the upstream controller.

Only runs as a root controller inside the declared Docker image. No model loop,
direct CAD execution, hidden evaluator, automatic mutation retry or host fallback.
"""
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys


def main():
    policy = json.loads(Path(sys.argv[1]).read_text())
    if sys.platform != "linux" or os.geteuid() != 0:
        raise RuntimeError("PCB controller requires its isolated Linux container, not host Python.")
    for relative, expected in policy["sourceFiles"].items():
        file = Path("/") / relative
        if file.is_symlink() or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            raise RuntimeError("PCB runtime source differs from its declared snapshot: " + relative)
    version = subprocess.check_output(["kicad-cli", "version"], text=True).strip()
    if version != policy["kicadVersion"]:
        raise RuntimeError("PCB runtime requires KiCad " + policy["kicadVersion"])
    for package in ("kicad", "kicad-symbols", "kicad-footprints", "kicad-packages3d"):
        installed = subprocess.check_output(["dpkg-query", "-W", "-f=${Version}", package], text=True).strip()
        if installed != policy["kicadPackageVersion"]:
            raise RuntimeError("PCB runtime package version mismatch: " + package)
    import pcbnew
    match = re.match(r"^(\d+\.\d+\.\d+)", pcbnew.Version())
    if not match or match.group(1) != policy["kicadVersion"]:
        raise RuntimeError("pcbnew does not match the KiCad CLI.")

    sys.path.insert(0, "/pcb-agent")
    from tools import agent_session, session_isolation, session_recovery, kimi_mcp, task_contract
    if agent_session.TOOLSET_SHA256 != policy["toolSchemaSha256"]:
        raise RuntimeError("PCB native tool schema differs from the registered snapshot.")
    root = Path("/workspace")
    os.chdir(root)
    # Protect startup as well as the lifetime of this controller. Individual
    # actions retain upstream process-held leases and pending/completed receipts.
    fd = os.open(root, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another PCB controller owns this workspace; use another candidate directory.") from error
        logs = root / "session"
        if logs.is_symlink():
            raise RuntimeError("PCB session directory cannot be a symlink.")
        identity_file = logs / "industrial-harness-pcb.json"
        identity = {"sourceSha256": policy["sourceSha256"], "imageId": policy["imageId"],
                    "toolSchemaSha256": policy["toolSchemaSha256"], "requirementsSha256": policy.get("requirementsSha256")}
        if logs.exists():
            if identity_file.is_symlink() or not identity_file.is_file() or json.loads(identity_file.read_text()) != identity:
                raise RuntimeError("Existing session has another PCB runtime identity; preserve it and use a new candidate.")
        state = root / ".pcb"
        if state.is_symlink():
            raise RuntimeError("PCB state directory cannot be a symlink.")
        state.mkdir(exist_ok=True)
        session_isolation.prepare(root, {"uid": 1000, "gid": 1000}, resume=logs.exists())
        logs.mkdir(mode=0o700, exist_ok=True)
        logs.chmod(0o700)
        os.environ["PCB_TOOL_IDENTITY"] = "1000:1000"
        os.environ["PCB_AGENT_HOME"] = "/pcb-agent"
        os.environ["PCB_SKILLS"] = "/skills"
        os.environ["PYTHONPATH"] = "/pcb-agent"
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        os.environ["PCB_MODEL_VISION"] = "1" if policy.get("imageInput") else "0"
        if policy.get("requirementsPath"):
            os.environ.update(PCB_PUBLIC_REQUIREMENTS="/public/requirements.json",
                              PCB_REQUIREMENTS_SHA256=policy["requirementsSha256"], PCB_REQUIREMENTS_REQUIRED="1")
        task_contract.binding()  # Reject a changed public document before any action.
        with session_recovery.lease(root, "harness-controller"):
            session_recovery.durable_write(identity_file, identity)
            identity_file.chmod(0o600)
            # Upstream owns action execution, observation guards and recovery.
            # Pending mutations remain frozen; connecting never replays them.
            kimi_mcp.main()
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
