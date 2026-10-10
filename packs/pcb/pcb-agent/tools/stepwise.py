"""Isolate which tool call aborts, one step at a time, flushing as it goes."""
import json
import sys
import traceback

sys.path.insert(0, "/pcb-agent")


def step(n, fn):
    print(f"{n} …", flush=True)
    try:
        r = fn()
        print(f"   OK {json.dumps(r, default=str)[:200] if r is not None else ''}", flush=True)
        return r
    except Exception:
        print("   FAILED", flush=True)
        traceback.print_exc()
        sys.stdout.flush()
        raise


from tools import kicad_cli            # noqa: E402
step("1 kicad_cli.self_test", kicad_cli.self_test)

from tools import pcb_editor as ed     # noqa: E402
print("2 pcbnew imported", flush=True)

b = step("3 new_board", lambda: ed.new_board("/tmp/p.kicad_pcb", layers=2) and None)
b = ed.load("/tmp/p.kicad_pcb")
step("4 rect_outline", lambda: ed.rect_outline(b, 22, 22))
FP = "/usr/share/kicad/footprints/Connector_PinHeader_2.54mm.pretty"
step("5 add_footprint J1", lambda: ed.add_footprint(b, FP, "PinHeader_1x08_P2.54mm_Vertical", "J1", 6, 6) and None)
step("6 add_footprint J2", lambda: ed.add_footprint(b, FP, "PinHeader_1x08_P2.54mm_Vertical", "J2", 16, 6) and None)
step("7 connect_pads", lambda: ed.connect_pads(b, ("J1", "1"), ("J2", "1"), "TESTNET"))
step("8 add_track", lambda: ed.add_track(b, 6, 6, 16, 6, "TESTNET", 0.3) and None)
step("9 add_via", lambda: ed.add_via(b, 11, 6, "TESTNET") and None)
step("10 add_ground_zone", lambda: ed.add_ground_zone(b, 22, 22, "GND", "B.Cu") and None)
step("11 fill_zones", lambda: ed.fill_zones(b))
step("12 save", lambda: ed.save(b, "/tmp/p.kicad_pcb"))
step("13 audit", lambda: ed.audit(ed.load("/tmp/p.kicad_pcb")))
step("14 drc", lambda: {k: v for k, v in
                        kicad_cli.drc("/tmp/p.kicad_pcb", "/tmp/drc.json").items()
                        if k in ("available", "counts")})
print("ALL STEPS PASSED", flush=True)
