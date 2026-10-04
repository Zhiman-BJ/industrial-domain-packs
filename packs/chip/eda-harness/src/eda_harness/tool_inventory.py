"""Read-only inspection of the unified tool image, usable without an EDA project."""

import json
import subprocess

from eda_harness.runtimes.execution import run_probe


def inspect_tools(image):
    inspection = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{json .}}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if inspection.returncode:
        raise ValueError(f"Tool image unavailable: {image}; build Dockerfile.tools first")
    metadata = json.loads(inspection.stdout)
    platform = f"{metadata['Os']}/{metadata['Architecture']}"
    process = run_probe(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            platform,
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "128",
            "--cpus",
            "1",
            "--memory",
            "1g",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m",
            "--entrypoint",
            "python3",
            metadata["Id"],
            "/opt/eda-tools/inventory.py",
        ],
        capture_output=True,
        text=True,
        timeout=100,
    )
    try:
        result = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise ValueError(f"Image has no usable tool inventory: {process.stderr[-2000:]}") from error
    result.update(image=image, image_id=metadata["Id"], platform=platform)
    result["ready"] = bool(result.get("ready")) and process.returncode == 0
    return result
