"""Prove the tool layer works, end to end, inside the agent container.

Builds a tiny two-layer board from nothing, routes one net, pours a ground
zone, saves it, then runs DRC on the result. If this passes, the tools are
sound and an agent can rely on them.
"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tools import kicad_cli, pcb_editor as ed  # noqa: E402

out = {}
out["kicad_cli"] = kicad_cli.self_test()

work = pathlib.Path(tempfile.mkdtemp(prefix="pcbtool-"))
brd = work / "probe.kicad_pcb"

b = ed.new_board(brd, layers=2)
ed.rect_outline(b, 22.0, 22.0)

fp_dir = "/usr/share/kicad/footprints/Connector_PinHeader_2.54mm.pretty"
try:
    ed.add_footprint(b, fp_dir, "PinHeader_1x08_P2.54mm_Vertical", "J1", 6.0, 6.0)
    ed.add_footprint(b, fp_dir, "PinHeader_1x08_P2.54mm_Vertical", "J2", 16.0, 6.0)
    out["footprints_placed"] = 2
except Exception as e:
    out["footprints_placed"] = f"failed: {e}"

try:
    ed.connect_pads(b, ("J1", "1"), ("J2", "1"), "TESTNET")
    ed.add_track(b, 6.0, 6.0, 16.0, 6.0, "TESTNET", width_mm=0.3)
    ed.add_via(b, 11.0, 6.0, "TESTNET")
    out["routed"] = True
except Exception as e:
    out["routed"] = f"failed: {e}"

try:
    ed.add_ground_zone(b, 22.0, 22.0, "GND", layer="B.Cu")
    ed.fill_zones(b)
    out["zone"] = True
except Exception as e:
    out["zone"] = f"failed: {e}"

ed.save(b, brd)
out["audit"] = ed.audit(ed.load(brd))
out["drc"] = {k: v for k, v in kicad_cli.drc(brd, work / "drc.json").items()
              if k in ("available", "counts")}
out["board_bytes"] = brd.stat().st_size
print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
