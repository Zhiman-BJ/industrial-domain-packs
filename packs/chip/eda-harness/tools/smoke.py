"""Real seven-tool processing smoke. Proves tool interoperability, not signoff."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from inventory import inventory
from verification_smoke import smoke as verification_smoke


def run(argv, env=None):
    print("Running: " + " ".join(argv), flush=True)
    subprocess.run(argv, check=True, env=env)


def smoke(source, top, output):
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", top):
        raise ValueError("Invalid top module")
    output.mkdir(parents=True, exist_ok=True)
    source = source.resolve()
    os.chdir(output)
    # The caller mounts a single input at a controlled name, never interpolated tool source.
    rtl_name = f"{top}.sv"
    Path(rtl_name).write_bytes(source.read_bytes())
    versions = inventory()
    if not versions["ready"]:
        raise ValueError(json.dumps(versions))
    Path("versions.json").write_text(json.dumps(versions, indent=2))
    platform = Path("/OpenROAD-flow-scripts/flow/platforms/nangate45")
    liberty = platform / "lib/NangateOpenCellLibrary_typical.lib"
    run(["verilator", "--lint-only", "--sv", "-Wall", "--top-module", top, rtl_name])
    Path("synth.ys").write_text(
        f"read_liberty -lib {liberty}\nread_verilog -sv {rtl_name}\nhierarchy -check -top {top}\n"
        f"synth -top {top}\ndfflibmap -liberty {liberty}\nabc -liberty {liberty}\n"
        "clean\ncheck -assert\nwrite_verilog -noattr -noexpr netlist.v\nwrite_json netlist.json\n"
    )
    run(["yosys", "-s", "synth.ys"])
    netlist = json.loads(Path("netlist.json").read_text())
    if top not in netlist.get("modules", {}):
        raise ValueError("Synthesized top missing")
    Path("physical.tcl").write_text(
        f"read_liberty {liberty}\nread_lef {platform}/lef/NangateOpenCellLibrary.tech.lef\n"
        f"read_lef {platform}/lef/NangateOpenCellLibrary.macro.mod.lef\n"
        f"read_verilog netlist.v\nlink_design {top}\n"
        "initialize_floorplan -die_area {0 0 40 40} -core_area {2 2 38 38} -site FreePDK45_38x28_10R_NP_162NW_34O\n"
        f"source {platform}/make_tracks.tcl\nsource {platform}/setRC.tcl\n"
        "place_pins -hor_layers metal3 -ver_layers metal2\nglobal_placement -density 0.3\n"
        "detailed_placement\ncheck_placement -verbose\nwrite_db placed.odb\nwrite_def placed.def\n"
    )
    run(["openroad", "-exit", "-threads", "2", "physical.tcl"])
    Path("inputs").mkdir(exist_ok=True)
    Path("inputs/platform").symlink_to(platform, target_is_directory=True)
    Path("deps/physical.route").mkdir(parents=True, exist_ok=True)
    Path("deps/physical.route/layout.def").write_bytes(Path("placed.def").read_bytes())
    env = {
        **os.environ,
        "EDA_INPUT_DIR": str(output / "inputs"),
        "EDA_DEPS_DIR": str(output / "deps"),
        "EDA_TOP": top,
    }
    run(["klayout", "-b", "-r", "/opt/eda-tools/streamout.py"], env)
    for artifact in (
        "netlist.v",
        "netlist.json",
        "placed.odb",
        "placed.def",
        "routed.gds",
        "streamout-checks.json",
    ):
        if not Path(artifact).is_file() or Path(artifact).stat().st_size == 0:
            raise ValueError(f"Missing/empty {artifact}")
    checks = json.loads(Path("streamout-checks.json").read_text())["checks"]
    if not checks or not all(c["passed"] for c in checks):
        raise ValueError("GDS downstream validation failed")
    verification_smoke(output / "verification")
    print("Seven-tool smoke passed: lint, mapped synthesis, placement, GDS round-trip", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--top", default="counter")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    smoke(args.input, args.top, args.output.resolve())
