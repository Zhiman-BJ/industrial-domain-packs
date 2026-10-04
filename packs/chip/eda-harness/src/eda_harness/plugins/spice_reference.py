"""Assemble Yosys SPICE with explicit device models and validate scalar cell interfaces."""

import json
import re
from pathlib import Path


def assemble(job):
    design = json.loads(Path("netlist.json").read_text())
    rtlil = Path("interface.il").read_text()
    cells = {}
    current = None
    for line in rtlil.splitlines():
        match = re.fullmatch(r"module \\(\S+)", line)
        if match:
            current = match[1]
            cells[current] = []
        match = re.search(r"^  wire (.*?)\b(input|output|inout) (\d+) \\(\S+)$", line)
        if match and current:
            width = re.search(r"\bwidth (\d+)", match[1])
            if width and int(width[1]) != 1:
                cells[current].append((int(match[3]), None))
            else:
                cells[current].append((int(match[3]), match[4]))
    libraries = "\n".join(Path(p).read_text() for p in job["models"])
    # Join standard SPICE continuation lines before reading subcircuit interfaces.
    logical = re.sub(r"\n\s*\+\s*", " ", libraries)
    models = {}
    for match in re.finditer(r"^\s*\.subckt\s+(\S+)\s*([^\n]*)", logical, re.I | re.M):
        name = match[1].lower()
        if name in models:
            raise ValueError(f"Duplicate SPICE model: {name}")
        models[name] = match[2].split()
    used = {
        cell["type"] for module in design["modules"].values() for cell in module.get("cells", {}).values()
    }
    validated = []
    for name in sorted(used):
        module = design["modules"].get(name)
        if module is None:
            raise ValueError(f"Unresolved cell interface: {name}")
        if not int(module.get("attributes", {}).get("blackbox", "0"), 2):
            continue
        pins = [pin for _, pin in sorted(cells[name])]
        if not pins or None in pins:
            raise ValueError(f"LVS cell model requires scalar ports: {name}")
        if [p.lower() for p in pins] != [p.lower() for p in models.get(name.lower(), [])]:
            raise ValueError(f"SPICE/Verilog cell port order mismatch or missing model: {name}")
        validated.append(name)
    raw = Path("reference-raw.spice").read_text()
    if not re.search(r"^\.SUBCKT\s+" + re.escape(job["top"]) + r"\s", raw, re.I | re.M):
        raise ValueError("Missing exported top subcircuit")
    Path("reference.spice").write_text(raw + "\n" + libraries + "\n")
    Path("reference-check.json").write_text(
        json.dumps(
            {
                "top": job["top"],
                "validated_cells": validated,
                "scope": "Model interface validation and assembly; not transistor model correctness or LVS proof",
            }
        )
    )


if __name__ == "__main__":
    assemble(json.loads(Path("reference-job.json").read_text()))
