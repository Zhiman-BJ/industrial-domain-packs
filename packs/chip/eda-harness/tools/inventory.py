"""Runtime inventory; exit nonzero if any required executable is unusable."""

import json
import os
import shutil
import subprocess
from pathlib import Path

COMMANDS = {
    "verilator": ["--version"],
    "yosys": ["-V"],
    "eda-orfs-lec": ["--version"],
    "dot": ["-V"],
    "openroad": ["-version"],
    "klayout": ["-v"],
    "magic": ["--version"],
    "netgen": ["-batch"],
    "gtkwave": ["--version"],
}


def inventory():
    result = {}
    for name, flags in COMMANDS.items():
        binary = shutil.which(name)
        item = {"path": binary, "available": False, "version": None}
        if binary:
            try:
                command = [binary, *flags]
                if name == "gtkwave":
                    command = ["xvfb-run", "-a", *command]
                process = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=20,
                    env={**os.environ, "GSETTINGS_BACKEND": "memory"},
                )
                output = (process.stdout + process.stderr).strip()
                item.update(
                    available=process.returncode == 0,
                    version=output.splitlines()[0] if output else None,
                    exit_code=process.returncode,
                )
            except (OSError, subprocess.SubprocessError) as error:
                item["error"] = str(error)
        result[name] = item
    return {
        "tools": result,
        "ready": all(i["available"] for i in result.values()),
        "platform": "linux/amd64",
        "nangate45": Path("/OpenROAD-flow-scripts/flow/platforms/nangate45").is_dir(),
    }


if __name__ == "__main__":
    result = inventory()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ready"] else 1)
