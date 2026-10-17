#!/usr/bin/env python3
"""Trusted PCB verification executor for the protected worker protocol.

The shared ``worker-http.py`` owns authentication, snapshot-manifest
verification, argument transport, timeout/reaping and artifact byte caps. This
executor only accepts the typed PCB verify arguments and runs the pinned KiCad
CLI/native readback against the server-materialized ``/input`` tree.
"""

import json
import hashlib
import shutil
import os
import re
import subprocess
import sys
import time
from pathlib import Path

MAX_LOG_BYTES = 2 * 1024 * 1024
VERSION = "10.0.6"


def fail(message):
    raise ValueError(message)


def relative_board(root, value):
    if not isinstance(value, str) or not value.endswith(".kicad_pcb"):
        fail("file must be a project-relative .kicad_pcb path")
    path = Path(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        fail("file must not escape the input snapshot")
    candidate = root / path
    if candidate.is_symlink():
        fail("board file cannot be a symlink")
    target = candidate.resolve()
    if root.resolve() not in target.parents or not target.is_file():
        fail("board file is absent or escaped the input snapshot")
    return target


def parse_board_declaration(root, filename):
    declaration = root / "pcb.yaml"
    if not declaration.is_file() or declaration.is_symlink():
        fail("pcb.yaml is required by the pcb-verify-cpu input profile")
    values = {}
    for line in declaration.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"\s*([A-Za-z][A-Za-z0-9_]*)\s*:\s*['\"]?([^'\"#]+?)['\"]?\s*", line)
        if match:
            values[match.group(1)] = match.group(2).strip()
    if values.get("schemaVersion") != "0.1" or values.get("operation") != "verify":
        fail("pcb.yaml must declare schemaVersion 0.1 and operation verify")
    if values.get("board") != filename:
        fail("pcb.yaml board must match the requested file")


def validate_arguments(arguments):
    if not isinstance(arguments, dict) or set(arguments) != {"file", "expect", "timeoutMs"}:
        fail("arguments must contain exactly file, expect and timeoutMs")
    timeout = arguments["timeoutMs"]
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 120000:
        fail("timeoutMs must be an integer between 1 and 120000")
    expect = arguments["expect"]
    if not isinstance(expect, dict) or set(expect) - {"bounds", "footprints", "maxWarnings"}:
        fail("expect contains unsupported fields")
    bounds = expect.get("bounds")
    if not isinstance(bounds, list) or len(bounds) != 4 or any(not isinstance(value, (int, float)) for value in bounds):
        fail("expect.bounds must be [x, y, width, height]")
    footprints = expect.get("footprints", [])
    if not isinstance(footprints, list) or len(footprints) > 64:
        fail("expect.footprints must contain at most 64 entries")
    seen = set()
    for footprint in footprints:
        if not isinstance(footprint, dict) or set(footprint) - {"reference", "position", "rotation"}:
            fail("invalid footprint expectation")
        reference = footprint.get("reference")
        if not isinstance(reference, str) or not re.fullmatch(r"\w[\w.-]{0,63}", reference) or reference in seen:
            fail("footprint references must be unique bounded identifiers")
        seen.add(reference)
        position = footprint.get("position")
        if not isinstance(position, list) or len(position) != 2 or any(not isinstance(value, (int, float)) for value in position):
            fail("footprint position must be a 2D vector")
        if "rotation" in footprint and not isinstance(footprint["rotation"], (int, float)):
            fail("footprint rotation must be numeric")
    maximum = expect.get("maxWarnings")
    if maximum is not None and (isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0):
        fail("maxWarnings must be a nonnegative integer")


def native_readback(board):
    script = r'''
import json, pcbnew, sys
board = pcbnew.LoadBoard(sys.argv[1])
if board is None:
    raise RuntimeError("KiCad failed to load board")
mm = pcbnew.ToMM
box = board.GetBoardEdgesBoundingBox()
drawings = [item for item in board.GetDrawings() if item.GetLayer() == pcbnew.Edge_Cuts]
points = []
for edge in drawings:
    points.append({"shape": int(edge.GetShape()), "start": [mm(edge.GetStart().x), mm(edge.GetStart().y)], "end": [mm(edge.GetEnd().x), mm(edge.GetEnd().y)]})
xs = [point[key][0] for point in points for key in ("start", "end")]
ys = [point[key][1] for point in points for key in ("start", "end")]
bounds = [min(xs), min(ys), max(xs)-min(xs), max(ys)-min(ys)] if xs else []
print(json.dumps({"schemaVersion": 1, "version": pcbnew.Version(), "nativeBounds": [mm(box.GetX()), mm(box.GetY()), mm(box.GetWidth()), mm(box.GetHeight())], "bounds": bounds, "edges": points, "footprints": [{"reference": item.GetReference(), "position": [mm(item.GetPosition().x), mm(item.GetPosition().y)], "rotation": item.GetOrientationDegrees()} for item in board.GetFootprints()]}))
'''
    result = subprocess.run(["python3", "-c", script, str(board)], capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "native KiCad readback failed")
    return json.loads(result.stdout)


def main(input_directory, work_directory):
    root = Path(input_directory).resolve()
    work = Path(work_directory).resolve()
    work.mkdir(parents=True, exist_ok=True)
    request = json.loads((work / "request.json").read_text(encoding="utf-8"))
    arguments = request.get("arguments")
    validate_arguments(arguments)
    filename = arguments["file"]
    board = relative_board(root, filename)
    parse_board_declaration(root, filename)
    input_hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
    # KiCad writes project-local sidecars even for readback. Isolate those
    # writes in this invocation so the frozen input remains reusable.
    staged_root = work / "project"
    shutil.copytree(root, staged_root)
    board = staged_root / filename
    started = time.monotonic()
    report = {
        "schemaVersion": "1",
        "profileId": "pcb-verify-cpu",
        "toolVersion": None,
        "executionSucceeded": False,
        "inputUnchanged": True,
        "expect": arguments["expect"],
        "diagnostic": None,
        "durationMs": 0,
    }
    log_path = work / "tool.log"
    try:
        version = subprocess.check_output(["kicad-cli", "version"], text=True, stderr=subprocess.STDOUT, timeout=30).strip()
        if version != VERSION:
            fail(f"KiCad CLI must be {VERSION}, got {version}")
        report["toolVersion"] = version
        drc_path = work / "drc.json"
        with log_path.open("wb") as log:
            drc = subprocess.run(["kicad-cli", "pcb", "drc", "--format", "json", "--severity-all", "--output", str(drc_path), str(board)], stdout=log, stderr=subprocess.STDOUT, timeout=arguments["timeoutMs"] / 1000)
        if drc.returncode != 0:
            fail(f"kicad-cli DRC failed with exit {drc.returncode}")
        if not drc_path.is_file():
            fail(f"kicad-cli did not produce drc.json (exit {drc.returncode})")
        drc_document = json.loads(drc_path.read_text(encoding="utf-8"))
        if not isinstance(drc_document.get("violations"), list) or not isinstance(drc_document.get("unconnected_items"), list):
            fail("unrecognized KiCad DRC report schema")
        readback = native_readback(board)
        (work / "readback.json").write_text(json.dumps(readback, sort_keys=True) + "\n", encoding="utf-8")
        errors = sum(1 for item in drc_document["violations"] if item.get("severity") == "error")
        warnings = sum(1 for item in drc_document["violations"] if item.get("severity") == "warning")
        report.update({
            "executionSucceeded": drc.returncode == 0,
            "drcExitCode": drc.returncode,
            "errorCount": errors,
            "warningCount": warnings,
            "unconnectedCount": len(drc_document["unconnected_items"]),
        })
        if drc.returncode != 0:
            report["diagnostic"] = "kicad-cli DRC exited with status %d" % drc.returncode
    except Exception as error:
        report["diagnostic"] = str(error)[:1024]
        with log_path.open("ab") as log:
            if log.tell() < MAX_LOG_BYTES:
                log.write(("PCB verify: " + report["diagnostic"] + "\n").encode())
    report["inputUnchanged"] = input_hashes == {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
    report["durationMs"] = round((time.monotonic() - started) * 1000)
    (work / "report.json").write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if report["executionSucceeded"] else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(64)
    try:
        sys.exit(main(*sys.argv[1:]))
    except Exception as error:
        print(f"PCB verify failed: {error}", file=sys.stderr)
        sys.exit(1)

