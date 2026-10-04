#!/usr/bin/env python3
"""Portable, fail-closed Yosys replacement for ORFS's gate-level LEC callback.

Accepts only the ORFS-generated two-netlist/Liberty YAML contract. It does not
emulate Kepler's other interfaces or treat an inconclusive proof as equivalent.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


def word(path):
    value = str(path)
    if any(c in value for c in ('"', "\\", "\n", "\r")):
        raise ValueError("Unsupported quote/control character in LEC input path")
    return '"' + value + '"'


def proof(config):
    allowed = {"format", "input_paths", "liberty_files", "log_file"}
    if not isinstance(config, dict) or set(config) != allowed or config["format"] != "verilog":
        raise ValueError("Only the ORFS two-Verilog-netlist/Liberty LEC contract is supported")
    nets, libs = config["input_paths"], config["liberty_files"]
    if not isinstance(nets, list) or len(nets) != 2 or not isinstance(libs, list) or not libs:
        raise ValueError("LEC requires exactly two netlists and nonempty functional Liberty models")
    for path in [*nets, *libs]:
        if not isinstance(path, str) or not Path(path).is_file():
            raise ValueError(f"LEC input unavailable: {path}")
    log = Path(config["log_file"])
    log.parent.mkdir(parents=True, exist_ok=True)
    script = []
    for name, net in zip(("gold", "gate"), nets):
        # No -lib: load functional cells, including sequential Liberty models.
        # Omit incomplete unused library cells; hierarchy -check rejects them if used.
        script += [*[f"read_liberty -ignore_miss_func {word(lib)}" for lib in libs],
                   f"read_verilog {word(net)}", "hierarchy -auto-top -check",
                   "rename -top top", "prep -top top -flatten", "async2sync",
                   "memory_map", "opt_clean", "dffunmap", "check -assert",
                   f"design -stash {name}"]
    script += ["design -copy-from gold -as gold top", "design -copy-from gate -as gate top",
               "equiv_make gold gate equiv", "hierarchy -top equiv", "equiv_simple -seq 10",
               "equiv_induct -seq 10", "equiv_status -assert"]
    timeout = int(os.environ.get("EDA_LEC_TIMEOUT_SECONDS", "120"))
    if timeout < 1:
        raise ValueError("EDA_LEC_TIMEOUT_SECONDS must be positive")
    with tempfile.TemporaryDirectory(prefix="eda-orfs-lec-") as temporary:
        source = Path(temporary) / "proof.ys"
        source.write_text("\n".join(script) + "\n")
        try:
            process = subprocess.run(["yosys", "-ql", str(log), "-s", str(source)],
                                     capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            with log.open("a") as output:
                output.write("\nEDA LEC TIMEOUT: proof is inconclusive\n")
            return 124
    text = log.read_text(errors="replace") if log.is_file() else ""
    counts = re.findall(r"Of those cells (\d+) are proven and (\d+) are unproven\.", text)
    passed = bool(counts) and int(counts[-1][0]) > 0 and int(counts[-1][1]) == 0
    passed = passed and "Equivalence successfully proven!" in text and process.returncode == 0
    print(f"EDA ORFS LEC (Yosys): {'PROVED' if passed else 'FAILED/INCONCLUSIVE'}; {log}")
    if not passed:
        print("Found difference or inconclusive proof", file=sys.stderr)
        print((process.stdout + process.stderr)[-2000:], file=sys.stderr)
    return 0 if passed else (process.returncode or 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version="EDA ORFS LEC 1 (Yosys; no Kepler execution)")
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    try:
        return proof(yaml.safe_load(args.config.read_text()))
    except (ValueError, OSError, yaml.YAMLError) as error:
        print(f"EDA ORFS LEC failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
