"""Does the generated schematic actually load? kicad-cli is the judge."""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, "/pcb-agent")
from tools import datasheet_search as ds   # noqa: E402
from tools import kicad_cli                # noqa: E402
from tools.schematic import Schematic, write_project  # noqa: E402

work = pathlib.Path(tempfile.mkdtemp(prefix="sch-"))
NAME = "probe"
out = {}

# pick parts the way an agent would: by description and pin count
usb = ds.find_symbol("USB C receptacle usb2", limit=5)
hdr = ds.find_symbol("conn 01x08 pin header", limit=5)
out["usb_candidates"] = [(s["lib_id"], s["pins"]) for s in usb][:3]
out["hdr_candidates"] = [(s["lib_id"], s["pins"]) for s in hdr][:3]

sch = Schematic(NAME)
try:
    sch.add_part("J1", "Connector:USB_C_Receptacle_USB2.0_16P",
                 "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12",
                 nets={"A1": "GND", "A4": "VBUS", "A5": "CC1", "A6": "DP",
                       "A7": "DN", "A8": "SBU1", "A9": "VBUS", "A12": "GND",
                       "B1": "GND", "B4": "VBUS", "B5": "CC2", "B6": "DP",
                       "B7": "DN", "B8": "SBU2", "B9": "VBUS", "B12": "GND"})
    out["J1"] = "placed"
except Exception as e:
    out["J1"] = f"failed: {e}"

try:
    sch.add_part("J2", "Connector:Conn_01x08_Pin",
                 "Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical",
                 nets={"1": "VBUS", "2": "GND", "3": "DP", "4": "DN",
                       "5": "CC1", "6": "CC2", "7": "SBU1", "8": "SBU2"})
    out["J2"] = "placed"
except Exception as e:
    out["J2"] = f"failed: {e}"

p = sch.write(work / f"{NAME}.kicad_sch")
write_project(work / f"{NAME}.kicad_pro", NAME)
out["sch_bytes"] = pathlib.Path(p).stat().st_size
out["pins_found"] = {q.ref: len(q.pins) for q in sch.parts}

erc = kicad_cli.erc(p, work / "erc.json")
out["erc"] = {"available": erc["available"], "counts": erc["counts"]}
nl = kicad_cli.export_netlist(p, work / "netlist.net")
out["netlist_export_rc"] = nl.returncode
if (work / "netlist.net").exists():
    txt = (work / "netlist.net").read_text(errors="replace")
    import re
    nets = sorted(set(re.findall(r'\(net \(code "\d+"\) \(name "([^"]+)"\)', txt)))
    out["nets_in_netlist"] = nets
    out["netlist_bytes"] = len(txt)

print(json.dumps(out, indent=2, ensure_ascii=False))
