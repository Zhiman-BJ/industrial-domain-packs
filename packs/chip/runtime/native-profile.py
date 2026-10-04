"""Trusted rtl-cpu entry. Local and sandbox execution use EDA prepare/observe.

The outer Harness owns canonical Runs, grants and acceptance. This program
returns attributed native evidence and has no Docker/Kubernetes credentials.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packs/chip/eda-harness/src"))
import yaml
from eda_harness.core.models import Project
from eda_harness.plugins import semantic

MAX_LOG = 2 * 1024 * 1024


def identity():
    lock = json.loads((ROOT / "content-lock.json").read_text())
    for name, expected in lock["files"].items():
        parts = Path(name).parts
        if parts[0] not in ("packs", "lib") or ".." in parts:
            raise ValueError("Unsafe content path")
        current = ROOT
        for part in parts:
            current /= part
            if current.is_symlink():
                raise ValueError("Pack source cannot be a symlink")
        if not current.is_file() or hashlib.sha256(current.read_bytes()).hexdigest() != expected:
            raise ValueError("Domain Pack integrity mismatch: " + name)
    calculated = hashlib.sha256(json.dumps(lock["files"], separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    if calculated != lock["contentSha256"]:
        raise ValueError("Invalid Pack content identity")
    return {"contentSha256": lock["contentSha256"],
            "packageVersion": json.loads((ROOT / "package.json").read_text())["version"]}


def project(root):
    file = root / "eda.yaml"
    if file.is_symlink() or not file.is_file() or file.stat().st_size > 65536:
        raise ValueError("eda.yaml must be a regular file of at most 64 KiB")
    config = yaml.safe_load(file.read_text())
    params = config["actions"]["rtl.simulate"]["parameters"]
    if set(params) - {"operation", "sources", "top", "trace"}:
        raise ValueError("Unsupported sandbox simulation parameter")
    if params["operation"] != "simulate" or params.get("trace", True) is not True:
        raise ValueError("rtl-cpu requires traced simulation")
    if not isinstance(params["top"], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", params["top"]):
        raise ValueError("Invalid top module")
    sources = params["sources"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= 128:
        raise ValueError("Use 1–128 direct source paths")
    captured = {}
    for source in sources:
        if not isinstance(source, dict) or set(source) != {"path"}:
            raise ValueError("Only direct source paths are supported")
        name = source["path"]
        if not isinstance(name, str) or not name or len(name) > 512 or "\\" in name or ":" in name or name.startswith("/"):
            raise ValueError("Source must be project-relative")
        if any(part in ("", ".", "..") for part in name.split("/")):
            raise ValueError("Invalid source path")
        current = root
        for part in name.split("/"):
            current /= part
            if current.is_symlink():
                raise ValueError("Input cannot contain symlinks")
        if not current.is_file() or root not in current.resolve().parents:
            raise ValueError("Source is absent or escaped the input root")
        captured[name] = {"type": "rtl", "hash": hashlib.sha256(current.read_bytes()).hexdigest()}
    # The coordinator chooses resources and runtime; user runtime/scripts are
    # deliberately not copied into the trusted native Project.
    model = Project.model_validate({"name": "rtl-cpu", "top": params["top"],
        "inputs": {"rtl": list(captured)},
        "runtime": {"kind": "local", "resources": {"cpu": 2, "memory_gb": 2, "build_jobs": 1}},
        "actions": {"rtl.simulate": {"parameters": params}}, "required_verification": ["rtl.simulate"]})
    return model, {"files": captured}


def invoke(args, work, log):
    process = subprocess.Popen(args, cwd=work, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env={"PATH": os.environ["PATH"], "HOME": "/tmp", "LANG": "C.UTF-8", "CCACHE_DISABLE": "1"})
    try:
        while chunk := process.stdout.read1(8192):
            if log.tell() + len(chunk) > MAX_LOG:
                raise ValueError("Tool diagnostic limit exceeded")
            log.write(chunk)
            log.flush()
        return process.wait()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def run(root, work):
    release = identity()
    root, work = Path(root).resolve(), Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
    started = time.monotonic()
    version = subprocess.check_output(["verilator", "--version"], text=True).strip()
    if not version.startswith("Verilator 5.026 "):
        raise ValueError("rtl-cpu requires pinned Verilator 5.026")
    report = {"schemaVersion": "0.2", "packIdentity": release, "profileId": "rtl-cpu",
        "toolVersion": version, "compileExitCode": None, "simulationExitCode": None,
        "completed": False, "completionMarker": None, "nativeVerification": None, "diagnostic": None}
    exit_code = 1
    with (work / "tool.log").open("wb") as log:
        try:
            model, snapshot = project(root)
            prepared = semantic.prepare("rtl.simulate", model, snapshot,
                {"input": str(root), "deps": str(work / "deps"), "work": str(work)}, work)
            report["compileExitCode"] = invoke(prepared.commands[0], work, log)
            if report["compileExitCode"] == 0:
                report["simulationExitCode"] = invoke(prepared.commands[1], work, log)
            evidence = [{"id": kind, "type": kind, "path": name}
                for kind, name in prepared.outputs.items() if (work / name).is_file()]
            observation = semantic.observe("rtl.simulate", model, work, work / "tool.log", evidence)
            report["nativeVerification"] = observation.verification.model_dump()
            completion = work / "simulation.txt"
            report["completed"] = report["simulationExitCode"] == 0 and completion.is_file() and "EDA_SIMULATION_FINISHED" in completion.read_text()
            if report["completed"]:
                report["completionMarker"] = "EDA_SIMULATION_FINISHED"
            wave = work / "wave.vcd"
            if report["completed"] and wave.is_file() and 0 < wave.stat().st_size <= 8 * 1024 * 1024 and observation.verification.status == "PASS":
                exit_code = 0
        except Exception as error:
            report["diagnostic"] = str(error)[:1024]
            if log.tell() < MAX_LOG - 2048:
                log.write(("RTL profile: " + report["diagnostic"] + "\n").encode())
    report["durationMs"] = round((time.monotonic() - started) * 1000)
    (work / "report.json").write_text(json.dumps(report, sort_keys=True) + "\n")
    print(json.dumps({"executionSucceeded": exit_code == 0, "packIdentity": release, "toolVersion": version}))
    return exit_code


if __name__ == "__main__":
    if sys.argv[1:] == ["--identity"]:
        print(json.dumps(identity(), sort_keys=True))
    elif len(sys.argv) == 3:
        sys.exit(run(*sys.argv[1:]))
    else:
        sys.exit(64)
