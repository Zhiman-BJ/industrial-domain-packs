#!/usr/bin/env python3
"""Real KiCad round-trip and DRC smoke; not a complete engineering qualification."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pcbnew


def main():
    source, output = Path(sys.argv[1]), Path(sys.argv[2])
    output.mkdir(parents=True, exist_ok=True)
    version = subprocess.check_output(["kicad-cli", "version"], text=True).strip()
    if version != "10.0.6" or not pcbnew.Version().startswith("10.0.6"):
        raise RuntimeError("KiCad CLI and pcbnew must both be 10.0.6")
    print(f"KiCad {version}: reading {source.name}", flush=True)
    board = pcbnew.LoadBoard(str(source))
    if board is None or not list(board.GetDrawings()):
        raise RuntimeError("Input board has no geometry")
    artifact = output / "validated.kicad_pcb"
    if not pcbnew.SaveBoard(str(artifact), board) or artifact.stat().st_size == 0:
        raise RuntimeError("KiCad did not save a board")
    # A separate native process reads the saved artifact, independent of the writer.
    readback = subprocess.check_output([
        sys.executable, "-c",
        "import json,pcbnew,sys; b=pcbnew.LoadBoard(sys.argv[1]); "
        "r=b.GetBoardEdgesBoundingBox(); print(json.dumps({"
        "'footprints':len(list(b.GetFootprints())),"
        "'width_mm':pcbnew.ToMM(r.GetWidth()),"
        "'height_mm':pcbnew.ToMM(r.GetHeight())}))", str(artifact),
    ], text=True)
    geometry = json.loads(readback)
    if geometry["width_mm"] <= 0 or geometry["height_mm"] <= 0:
        raise RuntimeError("Saved board has no usable outline")
    report = output / "drc.json"
    subprocess.run(["kicad-cli", "pcb", "drc", "--format", "json", "--severity-all",
                    "--output", str(report), str(artifact)], check=True, timeout=90)
    drc = json.loads(report.read_text())
    errors = [item for item in drc["violations"] if item["severity"] == "error"]
    warnings = [item for item in drc["violations"] if item["severity"] == "warning"]
    unconnected = drc["unconnected_items"]
    summary = {"version": version, **geometry, "errors": len(errors),
               "warnings": len(warnings), "unconnected": len(unconnected)}
    (output / "board-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)
    if errors or unconnected:
        raise RuntimeError("Board has DRC errors or unconnected items; see drc.json")
    print("Saved board readback and DRC succeeded; warnings remain in drc.json.", flush=True)


if __name__ == "__main__":
    os.environ.setdefault("HOME", "/tmp")
    try:
        main()
    except Exception as error:
        print(f"PCB validation failed: {error}", file=sys.stderr, flush=True)
        sys.exit(1)
