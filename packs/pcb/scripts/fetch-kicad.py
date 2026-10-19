#!/usr/bin/env python3
"""Fetch immutable official KiCad build artifacts after rolling PPA retirement."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

lock, output = Path(sys.argv[1]), Path(sys.argv[2])
output.mkdir(parents=True, exist_ok=True)
for package in json.loads(lock.read_text())["packages"]:
    destination = output / (package["package"] + ".deb")
    subprocess.run(["curl", "--fail", "--location", "--show-error", "--retry", "5",
                    "--retry-all-errors", "--connect-timeout", "30", "--max-time", "1800",
                    "--max-filesize", str(package["bytes"]), "--output", str(destination),
                    package["url"]], check=True)
    digest = hashlib.sha256()
    with destination.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if destination.stat().st_size != package["bytes"] or digest.hexdigest() != package["sha256"]:
        raise RuntimeError("KiCad artifact identity mismatch: " + package["package"])
    print(f"Verified {package['package']} {package['version']}", flush=True)
