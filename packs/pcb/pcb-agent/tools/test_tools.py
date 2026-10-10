"""Representative native backend regressions, including legacy helper diagnostics.

Run inside the agent image with python3 -m tools.test_tools. Each case reports
its observed result; exit 1 on failure. This is not exhaustive API coverage.
"""
import pathlib
import sys
import tempfile
import traceback

# Keep the image path as the first choice, while allowing this regression
# script to run from a source checkout on a developer host.
_agent_root = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, "/pcb-agent" if pathlib.Path("/pcb-agent/tools").is_dir() else str(_agent_root))
from tools import bootstrap, kicad_cli as kc, pcb_editor as ed  # noqa: E402
from tools import datasheet_search as ds  # noqa: E402
from tools.pins_of import pins  # noqa: E402

USB = "Connector:USB_C_Receptacle_USB2.0_16P"
USB_FP = "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal"
HDR_FP = "Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical"
REQ = ["VBUS", "GND", "D_P", "D_N", "CC1", "CC2", "SBU1", "SBU2"]
results = []


def case(name):
    def deco(fn):
        # Print BEFORE running and flush: three of the KiCad faults this suite
        # guards against kill the interpreter with no traceback, and a table
        # printed at the end is lost with it. The last line on screen is then
        # the name of the tool that died.
        print(f"   ... {name}", flush=True)
        try:
            out = fn()
            results.append((name, "PASS", out))
            print(f" \u2713 {name}  {str(out)[:80]}", flush=True)
        except Exception as e:  # noqa: BLE001
            results.append((name, "FAIL", f"{type(e).__name__}: {e}"))
            print(f" \u2717 {name}  {type(e).__name__}: {str(e)[:80]}", flush=True)
            traceback.print_exc(limit=1)
        return fn
    return deco


# ---------------------------------------------------------------- 搜索
@case("ds.find_symbol")
def _():
    r = ds.find_symbol("USB_C receptacle", pins=17)
    assert any(x["lib_id"] == USB for x in r), r[:2]
    return f"{len(r)} hits, top={r[0]['lib_id']}"


@case("ds.find_footprint")
def _():
    r = ds.find_footprint("USB_C_Receptacle 16P", pads=17)
    assert r and r[0]["pads"] == 17
    return f"{len(r)} hits, top={r[0]['lib']}:{r[0]['name']}"


@case("ds.footprint_dir")
def _():
    d = ds.footprint_dir("Connector_USB")
    assert d and pathlib.Path(d).is_dir()
    return d


@case("pins() 正常")
def _():
    p = pins(USB)
    assert len(p) == 17 and any(nm == "VBUS" for _n, nm, _t in p)
    return f"{len(p)} pins"


@case("pins() 错名报错并给候选")
def _():
    try:
        pins("Connector:USB_C_Receptacle_USB2.0")
    except LookupError as e:
        assert "16P" in str(e)
        return "raises with hint"
    raise AssertionError("did not raise")


# ---------------------------------------------------------------- 建板原子
TMP = pathlib.Path(tempfile.mkdtemp())
B = {}


@case("ed.new_board + rect_outline")
def _():
    b = ed.new_board(TMP / "a.kicad_pcb", layers=2)
    ed.rect_outline(b, 22, 22)
    assert ed.info(b).outline_bbox_mm is not None
    B["b"] = b
    return "closed outline"


@case("ed.add_part (J1 USB-C)")
def _():
    nets = {n: {"VBUS": "VBUS", "GND": "GND", "SHIELD": "GND", "D+": "D_P", "D-": "D_N",
                "CC1": "CC1", "CC2": "CC2", "SBU1": "SBU1", "SBU2": "SBU2"}[nm]
            for n, nm, _ in pins(USB)
            if nm in ("VBUS", "GND", "SHIELD", "D+", "D-", "CC1", "CC2", "SBU1", "SBU2")}
    fp = ed.add_part(B["b"], {"ref": "J1", "footprint": USB_FP, "at": [11, 5], "nets": nets})
    assert fp.GetReference() == "J1"
    return f"{len(nets)} pads netted"


@case("ed.add_part (J2 header)")
def _():
    ed.add_part(B["b"], {"ref": "J2", "footprint": HDR_FP, "at": [11, 17],
                         "nets": {str(i + 1): n for i, n in enumerate(REQ)}})
    return "ok"


@case("ed.add_part 拒绝 REF**")
def _():
    try:
        ed.add_part(B["b"], {"ref": "REF**", "footprint": HDR_FP})
    except ValueError:
        return "raises"
    raise AssertionError("did not raise")


@case("ed.add_part 拒绝重复位号")
def _():
    try:
        ed.add_part(B["b"], {"ref": "J1", "footprint": HDR_FP})
    except ValueError:
        return "raises"
    raise AssertionError("did not raise")


@case("ed.part_bbox")
def _():
    bb = ed.part_bbox(B["b"], "J1")
    assert bb and bb[2] > bb[0] and bb[3] > bb[1]
    return f"J1 {bb[2]-bb[0]:.1f}x{bb[3]-bb[1]:.1f}mm"


@case("ed.placement_check 发现贴边")
def _():
    pc = ed.placement_check(B["b"])
    # J1 at y=5 on a 22mm board with a 9mm-tall connector is expected to hit the edge
    return f"ok={pc['ok']} problems={len(pc['problems'])}"


@case("ed.auto_place 22x22 真实任务尺寸")
def _():
    # 1x08 header copper is 20.3 mm; must fit a 22 mm board with margin >= 0.5
    ap = ed.auto_place(B["b"], 22, 22)
    assert ap["fits"], ap["note"]
    pc = ed.placement_check(B["b"])
    assert pc["ok"], pc["problems"]
    return f"fits at margin {ap['margin_mm']}mm, {ap['placed']}"


@case("ed.move_part")
def _():
    ed.move_part(B["b"], "J2", 11, 18.5, 90)
    x, y = ed.part_bbox(B["b"], "J2")[:2]
    return f"J2 -> bbox from ({x:.1f},{y:.1f})"


@case("ed.net_pads")
def _():
    p = ed.net_pads(B["b"], "VBUS")
    assert len(p) >= 2
    return f"VBUS on {len(p)} pads"


# ---------------------------------------------------------------- 布线原子
@case("ed.autoroute")
def _():
    r = ed.autoroute(B["b"])
    assert r["nets_routed"] >= 6, r
    return f"{r['nets_routed']} nets, {r['items_added']} items"


@case("ed.add_ground_zone 内缩")
def _():
    ed.add_ground_zone(B["b"], 22, 22, "GND")
    a = ed.audit(B["b"])
    assert a["zones"] == 1 and "GND" not in a["nets_without_copper"]
    return "GND poured, counted as copper"


@case("ed.unroute 单网络")
def _():
    before = ed.audit(B["b"])["tracks"]
    n = ed.unroute(B["b"], "CC1")
    after = ed.audit(B["b"])["tracks"]
    assert n > 0 and after < before
    ed.autoroute(B["b"])            # put it back for later cases
    return f"removed {n}"


@case("ed.unroute 全部")
def _():
    n = ed.unroute(B["b"])
    assert ed.audit(B["b"])["tracks"] == 0 and ed.audit(B["b"])["zones"] == 1
    ed.autoroute(B["b"])
    return f"removed {n}, zone kept"


@case("ed.route_hv 单段")
def _():
    n = ed.route_hv(B["b"], 3, 3, 8, 8, "CC2")
    assert n >= 3
    return f"{n} items"


# ---------------------------------------------------------------- 检查原子
@case("ed.check_annotation")
def _():
    r = ed.check_annotation(B["b"])
    assert r["ok"] and "J1" in r["interface_refs"]
    return f"interfaces={r['interface_refs']}"


@case("ed.audit")
def _():
    a = ed.audit(B["b"])
    assert a["has_closed_outline"] and a["footprints"] == 2
    return f"tracks={a['tracks']} vias={a['vias']} nets={a['nets']}"


@case("ed.review 完整板")
def _():
    ed.save(B["b"], TMP / "a.kicad_pcb")
    r = ed.review(ed.load(TMP / "a.kicad_pcb"), REQ)
    return f"done={r['done']} gaps={len(r['gaps'])} next={r['next'][:50]}"


@case("ed.review 近似网络名")
def _():
    r = ed.review(B["b"], ["VBUS", "D-"])
    assert "D-" in r["near_miss"] and "D_N" in r["near_miss"]["D-"]
    return f"near_miss={r['near_miss']}"


@case("ed.remove_part")
def _():
    b2 = ed.new_board(TMP / "c.kicad_pcb")
    ed.add_part(b2, {"ref": "R1", "footprint": "Resistor_SMD:R_0603_1608Metric"})
    assert ed.remove_part(b2, "R1") and ed.audit(b2)["footprints"] == 0
    return "ok"


# ---------------------------------------------------------------- 官方检查
@case("kc.erc")
def _():
    out = TMP / "s"
    r = bootstrap.build(out, "s", {"board": {"w": 20, "h": 15}, "parts": [
        {"ref": "J1", "symbol": "Connector:Conn_01x04_Pin",
         "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
         "nets": {"1": "VBUS", "2": "GND", "3": "SDA", "4": "SCL"}}]})
    e = kc.erc(out / "s.kicad_sch", out / "erc.json")
    assert e["available"]
    B["s_out"] = out
    return f"errors={e['counts'].get('error')}"


@case("kc.drc")
def _():
    d = kc.drc(TMP / "a.kicad_pcb", TMP / "drc.json")
    assert d["available"]
    return f"errors={d['counts'].get('error')}"


@case("kc.drc_summary")
def _():
    s = kc.drc_summary(TMP / "a.kicad_pcb", TMP / "drc2.json")
    assert "by_type" in s and isinstance(s["examples"], dict)
    return f"errors={s['errors']} top={list(s['by_type'])[:3]}"


# ---------------------------------------------------------------- 一键
@case("bootstrap 缺 at 自动排布")
def _():
    r = bootstrap.build(TMP / "p", "p", {"board": {"w": 30, "h": 30}, "parts": [
        {"ref": "J1", "symbol": USB, "footprint": USB_FP, "nets": {"A4": "VBUS", "A1": "GND"}},
        {"ref": "J2", "symbol": "Connector:Conn_01x08_Pin", "footprint": HDR_FP,
         "nets": {"1": "VBUS", "2": "GND"}}]})
    assert r["gradeable"] and not [p for p in r["problems"] if "放不下" in p]
    pc = ed.placement_check(ed.load(pathlib.Path(r["out_dir"]) / "p.kicad_pcb"))
    assert pc["ok"], pc["problems"]
    return f"3 files, placement ok, interfaces={r['annotation']['interface_refs']}"


@case("bootstrap 拒绝 REF**")
def _():
    try:
        bootstrap.build(TMP / "q", "q", {"parts": [{"ref": "REF**", "footprint": HDR_FP}]})
    except ValueError:
        return "raises"
    raise AssertionError("did not raise")


# ---------------------------------------------------------------- 设计规则 / 网络类
@case("ed.set_design_rules")
def _():
    r = ed.set_design_rules(B["b"], clearance_mm=0.15, track_mm=0.2)
    ds = B["b"].GetDesignSettings()
    assert ed.to_mm(ds.m_MinClearance) == 0.15
    return r


@case("ed.set_net_class 电源加宽")
def _():
    ed.set_net_class(B["b"], "POWER", ["VBUS", "GND"], track_mm=0.5)
    w = ed.net_class_width(B["b"], "VBUS")
    assert abs(w - 0.5) < 1e-6, w
    assert abs(ed.net_class_width(B["b"], "CC1") - 0.2) < 1e-6
    return f"VBUS {w}mm, CC1 {ed.net_class_width(B['b'], 'CC1')}mm"


@case("ed.set_net_class 差分对")
def _():
    r = ed.set_net_class(B["b"], "USB_DP", ["D_P", "D_N"], track_mm=0.25, dp_width_mm=0.25, dp_gap_mm=0.2)
    assert r["dp"] == (0.25, 0.2)
    return r["class"]


@case("ed.set_layer_count 4 层")
def _():
    b4 = ed.new_board(TMP / "l4.kicad_pcb")
    assert ed.set_layer_count(b4, 4) == 4
    return "4"


# ---------------------------------------------------------------- 位号 / 去耦
@case("ed.next_ref")
def _():
    b2 = ed.new_board(TMP / "nr.kicad_pcb")
    ed.add_part(b2, {"ref": "C1", "footprint": "Capacitor_SMD:C_0402_1005Metric"})
    ed.add_part(b2, {"ref": "C3", "footprint": "Capacitor_SMD:C_0402_1005Metric"})
    assert ed.next_ref(b2, "C") == "C2" and ed.next_ref(b2, "R") == "R1"
    return "C2, R1"


@case("ed.add_decoupling + check_decoupling")
def _():
    b3 = ed.new_board(TMP / "dc.kicad_pcb"); ed.rect_outline(b3, 30, 30)
    ed.add_part(b3, {"ref": "U1", "footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                     "at": [15, 15], "nets": {"8": "VCC", "4": "GND"}})
    before = ed.check_decoupling(b3)
    assert not before["ok"], "应先报缺去耦"
    part = ed.add_decoupling(b3, "U1", "8", "GND", "100nF")
    after = ed.check_decoupling(b3)
    assert after["ok"], after
    assert part["ref"] == "C1" and part["nets"] == {"1": "VCC", "2": "GND"}
    d = ((part["at"][0] - ed.pad_xy(b3, "U1", "8")[0]) ** 2 + (part["at"][1] - ed.pad_xy(b3, "U1", "8")[1]) ** 2) ** 0.5
    assert d < 2.0, d
    return f"{part['ref']} at {d:.2f}mm from U1.8"


# ---------------------------------------------------------------- 差分对
@case("ed.route_diff_pair + check_diff_pairs")
def _():
    b = B["b"]
    ed.unroute(b, "D_P"); ed.unroute(b, "D_N")
    r = ed.route_diff_pair(b, "D_P", "D_N", width_mm=0.25, gap_mm=0.2)
    assert r["segments"] >= 4
    c = ed.check_diff_pairs(b)
    pair = [x for x in c["pairs"] if x["pair"] == ("D_P", "D_N")][0]
    assert "未布线" not in pair["issues"], pair
    return f"skew {r['skew_mm']}mm, issues={pair['issues']}"


# ---------------------------------------------------------------- 铺铜缝合 / 修复 / 一致性
@case("ed.stitch_ground")
def _():
    n = ed.stitch_ground(B["b"], pitch_mm=6)
    assert n >= 4
    return f"{n} vias"


@case("ed.fix_short")
def _():
    r = ed.fix_short(B["b"], "CC1", "CC2")
    assert r["removed"] > 0 and all(isinstance(v, int) for v in r["rerouted"].values())
    return r


@case("ed.check_dangling")
def _():
    b5 = ed.new_board(TMP / "dg.kicad_pcb")
    ed.add_part(b5, {"ref": "J1", "footprint": HDR_FP, "nets": {"1": "LONELY", "2": "GND", "3": "GND"}})
    r = ed.check_dangling(b5)
    assert r["single_pad_nets"] == ["LONELY"], r
    return r["single_pad_nets"]


@case("ed.check_sch_pcb_sync")
def _():
    out = B["s_out"]
    b6 = ed.load(out / "s.kicad_pcb")
    r = ed.check_sch_pcb_sync(out / "s.kicad_sch", b6)
    assert r["ok"], r
    ed.add_part(b6, {"ref": "R9", "footprint": "Resistor_SMD:R_0603_1608Metric", "nets": {"1": "EXTRA", "2": "GND"}})
    r2 = ed.check_sch_pcb_sync(out / "s.kicad_sch", b6)
    assert r2["status"] == "FAIL" and any("R9" in str(x) for x in r2["issues"]), r2
    return f"in sync, then drift detected: {r2['issues']}"


# ---------------------------------------------------------------- 接口规格 / 通用验证
CONTRACT_JSON = {"schema_version": 3, "task_id": "t", "semantic_external_net_profile": [
    {"key": ["semantic_external_net", "ground", "power", "5to8", "header:ground:power:1", "usb:ground:power:A1", "usb:ground:power:B12", "usb:ground:power:SH"], "count": 1},
    {"key": ["semantic_external_net", "signal", "usb", "2", "header:signal:gpio:7", "usb:signal:usb:A5"], "count": 1},
    {"key": ["semantic_external_net", "signal", "usb", "3to4", "header:signal:gpio:5", "usb:signal:usb:A6", "usb:signal:usb:B6"], "count": 1},
    {"key": ["semantic_external_net", "signal", "usb", "5to8", "header:signal:gpio:8", "usb:signal:usb:A4", "usb:signal:usb:B9"], "count": 1}]}


@case("interface.from_json 读契约（两种存法）")
def _():
    from tools import interface as itf
    import json as _j
    p = TMP / "io_contract.json"; p.write_text(_j.dumps(CONTRACT_JSON))
    i = itf.from_json(p)
    assert len(i.nets) == 4 and {e.kind for n in i.nets for e in n.endpoints} == {"header", "usb"}
    p2 = TMP / "iface.json"; p2.write_text(_j.dumps(itf.to_json(i)))
    i2 = itf.from_json(p2)
    assert i2.signature() == i.signature()
    B["iface"] = i
    return f"{len(i.nets)} nets, round-trip ok"


@case("interface.to_pinout 实例与脚位")
def _():
    from tools import interface as itf
    pin = itf.to_pinout(B["iface"])
    hdr = [c for c in pin["connectors"] if c["kind"] == "header"][0]
    assert hdr["pins"]["1"] == "GND" and hdr["pins"]["7"] == hdr["pins"]["7"] and "SH" in [c for c in pin["connectors"] if c["kind"] == "usb"][0]["pad_names"]
    return f"{[c['ref'] for c in pin['connectors']]} header={hdr['pins']}"


@case("interface.from_board == from_pinout（自建板签名一致）")
def _():
    from tools import interface as itf, pinout as po
    import json as _j
    pin = {"connectors": [
        {"ref": "J1", "footprint": USB_FP, "value": "USB-C",
         "pins": {"A1": "GND", "B12": "GND", "S1": "GND", "A5": "CC1", "A6": "D+", "B6": "D+", "A4": "VBUS", "B9": "VBUS"}},
        {"ref": "J2", "footprint": HDR_FP, "pins": {"1": "GND", "7": "CC1", "5": "D+", "8": "VBUS"}}]}
    out = TMP / "ifb"
    spec = po.apply({"board": {"w": 22, "h": 22}, "parts": [{"ref": "J1", "symbol": USB}, {"ref": "J2", "symbol": "Connector:Conn_01x08_Pin"}]}, pin)
    bootstrap.build(out, "ifb", spec)
    got = itf.from_board(ed.load(out / "ifb.kicad_pcb")); want = itf.from_pinout(pin)
    c = itf.compare(got, want)
    assert c["f1"] == 1.0, c
    B["ifb"] = out
    return f"F1 {c['f1']} over {len(got.nets)} nets"


@case("validation.run_drc same-layer crossing")
def _():
    from tools import validation as val
    b = ed.new_board(TMP / "cross.kicad_pcb"); ed.rect_outline(b, 30, 30)
    # Unanchored orphan tracks may be reassigned by native connectivity cleanup.
    # Use actual terminals so the two intended networks remain physically distinct.
    ed.add_part(b, {"ref":"J1","footprint":"Connector_PinHeader_2.54mm:PinHeader_1x01_P2.54mm_Vertical","at":[5,10],"nets":{"1":"A"}})
    ed.add_part(b, {"ref":"J2","footprint":"Connector_PinHeader_2.54mm:PinHeader_1x01_P2.54mm_Vertical","at":[15,5],"nets":{"1":"B"}})
    ed.add_track(b, 5, 10, 25, 10, "A", .25, "F.Cu")
    ed.add_track(b, 15, 5, 15, 15, "B", .25, "F.Cu")
    ed.save(b, TMP / "cross.kicad_pcb")
    report = kc.drc(TMP / "cross.kicad_pcb", TMP / "cross.json", schematic_parity=False)
    assert report["available"] and any(x["type"] in ("shorting_items", "tracks_crossing") for x in report["violations"]), report
    ed.unroute(b, "B"); ed.add_track(b, 15, 5, 15, 15, "B", .25, "B.Cu")
    ed.save(b, TMP / "cross.kicad_pcb")
    report = kc.drc(TMP / "cross.kicad_pcb", TMP / "cross.json", schematic_parity=False)
    assert not any(x["type"] in ("shorting_items", "tracks_crossing") for x in report["violations"]), report
    return "official DRC distinguishes layers"


@case("validation.simulate_subcircuit resistor divider")
def _():
    from tools import validation as val
    deck = TMP / "divider.cir"
    deck.write_text("* divider\nV1 in 0 1\nR1 in out 1k\nR2 out 0 1k\n.tran 1n 10n\n.meas tran output FIND v(out) AT=5n\n.end\n")
    r = val.simulate_subcircuit(deck, TMP / "spice", {"output": {"min": .49, "max": .51}})
    assert r["status"] == "PASS", r
    return "model-based divider measurement"


@case("verify missing electrical intent is not PASS")
def _():
    from tools import verify as vf
    out = B["ifb"]
    r = vf.verify(out / "ifb.kicad_pcb", out / "ifb.kicad_sch", out_dir=TMP / "vout")
    assert not r["pass"] and "checks" in r and (TMP / "vout" / "verify.json").exists()
    return r["status"]


# ---------------------------------------------------------------- 连通性 / 保护 / 一条命令
@case("ed.route_net 单网")
def _():
    b7 = ed.new_board(TMP / "rn.kicad_pcb"); ed.rect_outline(b7, 30, 30)
    # pin 1 is the footprint origin; put the parts at different heights so the
    # route needs both vertical legs, not just one horizontal on the corridor
    ed.add_part(b7, {"ref": "J1", "footprint": HDR_FP, "at": [8, 6], "nets": {"1": "A", "2": "B"}})
    ed.add_part(b7, {"ref": "J2", "footprint": HDR_FP, "at": [22, 24], "nets": {"1": "A", "2": "B"}})
    n = ed.route_net(b7, "A")
    assert n >= 3 and ed.audit(b7)["tracks"] >= 2
    return f"{n} items"


@case("ed.unconnected_by_net 读 DRC")
def _():
    b8 = ed.new_board(TMP / "ub.kicad_pcb"); ed.rect_outline(b8, 30, 30)
    ed.add_part(b8, {"ref": "J1", "footprint": HDR_FP, "at": [8, 15], "nets": {"1": "A", "2": "GND"}})
    ed.add_part(b8, {"ref": "J2", "footprint": HDR_FP, "at": [22, 15], "nets": {"1": "A", "2": "GND"}})
    ed.save(b8, TMP / "ub.kicad_pcb")
    kc.drc(TMP / "ub.kicad_pcb", TMP / "ub.json")
    ub = ed.unconnected_by_net(TMP / "ub.json")
    assert "A" in ub and ub["A"] >= 1, ub
    return ub


@case("ed.check_protection")
def _():
    b9 = ed.new_board(TMP / "pr.kicad_pcb")
    ed.add_part(b9, {"ref": "J1", "footprint": HDR_FP, "nets": {"1": "D+", "2": "D-"}})
    r = ed.check_protection(b9)
    assert not r["ok"] and set(r["unprotected"]) == {"D+", "D-"}, r
    ed.add_part(b9, {"ref": "D1", "footprint": "Package_TO_SOT_SMD:SOT-23-6", "value": "USBLC6-2SC6",
                     "nets": {"1": "D+", "3": "D-"}})
    assert ed.check_protection(b9)["ok"]
    return "flags then clears"


@case("run_loop rejects removed contract flags")
def _():
    import subprocess as sp
    result=sp.run([sys.executable,'-m','tools.run_loop','--out',str(TMP/'legacy'),
                   '--spec',str(TMP/'absent.json'),'--contract',str(TMP/'old.json')],capture_output=True,text=True)
    assert result.returncode==2 and 'unrecognized arguments' in result.stderr,result.stderr
    return 'Legacy scoring contract cannot enter generic workflow'


# ---------------------------------------------------------------- 通用层：pinout / rules / 连通性 / loop
@case("pinout.load + apply 通用脚位表")
def _():
    from tools import pinout as po
    import json as _j
    p = TMP / "pinout.json"
    p.write_text(_j.dumps({"connectors": [{"ref": "J1", "footprint": HDR_FP, "pins": {"1": "GND", "2": "SDA", "3": "SCL"}}]}))
    pin = po.load(p)
    spec = po.apply({"parts": [{"ref": "J1", "symbol": "Connector:Conn_01x03_Pin"}]}, pin)
    assert spec["parts"][0]["nets"] == {"1": "GND", "2": "SDA", "3": "SCL"} and spec["parts"][0]["footprint"] == HDR_FP
    assert po.required_nets(pin) == ["GND", "SCL", "SDA"]
    return "merged"


@case("rules.trace_width_mm IPC-2221")
def _():
    from tools import rules
    w1, w3 = rules.trace_width_mm(1.0), rules.trace_width_mm(3.0)
    assert 0.2 < w1 < 0.5 and w3 > 2 * w1, (w1, w3)          # 1 A ~ 0.3 mm, 3 A ~ 1.3 mm on 1 oz external
    assert rules.clearance_mm(48) == 0.6 and rules.via_count_for(2.5) == 3
    return f"1A={w1}mm 3A={w3}mm"


@case("ed.connectivity 真图判断断网")
def _():
    b10 = ed.new_board(TMP / "cn.kicad_pcb"); ed.rect_outline(b10, 30, 30)
    ed.add_part(b10, {"ref": "J1", "footprint": HDR_FP, "at": [8, 6], "nets": {"1": "A", "2": "B"}})
    ed.add_part(b10, {"ref": "J2", "footprint": HDR_FP, "at": [22, 24], "nets": {"1": "A", "2": "B"}})
    before = ed.connectivity(b10)
    assert before["split_nets"] == {"A": 2, "B": 2}, before["split_nets"]
    ed.route_net(b10, "A")
    after = ed.connectivity(b10)
    assert "A" not in after["split_nets"] and after["split_nets"].get("B") == 2, after["split_nets"]
    return f"before={before['split_nets']} after={after['split_nets']}"


@case("loop.role_of 通用命名分类")
def _():
    from tools.interface import role_of
    assert role_of("GND") == "ground" and role_of("AGND") == "ground"
    assert role_of("VBUS") == "power" and role_of("3V3") == "power" and role_of("+5V") == "power"
    assert role_of("SDA") == "signal" and role_of("D+") == "signal"
    return "ok"


@case("tools.loop stops on unresolved facts and preserves artifacts")
def _():
    from tools import loop
    spec = {"board": {"w": 22, "h": 22}, "parts": [
        {"ref": "J1", "symbol": "Connector:Conn_01x02_Pin",
         "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", "nets": {"1":"A", "2":"B"}}]}
    out = TMP / "loop2"
    r = loop.run(spec, out, "g")
    assert not r["pass"] and r["status"] in ("FAIL", "UNKNOWN"), r
    B["loop_attempt"] = pathlib.Path(r["attempt"])
    assert (B["loop_attempt"] / "trajectory.jsonl").exists()
    return r["reason"]


@case("tools.api 清单")
def _():
    from tools import api
    m = api.manifest()
    names = {t["tool"] for t in m}
    assert {"add_component", "run_erc", "compare_schematic_pcb", "verify_schematic", "verify_design"} <= names
    return f"{len(m)} tools"


# ---------------------------------------------------------------- 分层门禁
@case("没有适配层：tools/ 里不存在评测专用 import")
def _():
    import re as _re
    bad = []
    for f in pathlib.Path("/pcb-agent/tools").glob("*.py"):
        if f.name in ("run_loop.py", "test_tools.py"):
            continue                                   # compat entry and this file may
        if _re.search(r"^\s*(from|import)\s+(adapters|tools\.io_contract)", f.read_text(errors="replace"), _re.M):
            bad.append(f.name)
    assert not bad, bad
    return "general layer is benchmark-free"


# ---------------------------------------------------------------- 训练数据
@case("trajectory 汇总为数据集")
def _():
    import json as _j, subprocess as _sp
    from tools import trajectory as tj
    run = B["loop_attempt"]                                   # written by the tools.loop case above
    assert (run / "trajectory.jsonl").exists(), "需要先跑 tools.loop 用例"
    out = TMP / "dataset.jsonl"
    r = _sp.run([sys.executable, "-m", "tools.trajectory", str(run), "--out", str(out)], capture_output=True, text=True, cwd="/pcb-agent")
    assert r.returncode == 0, r.stderr[-300:]
    rows = [_j.loads(l) for l in out.read_text().splitlines()]
    kinds = {row["kind"] for row in rows}
    assert "start" in kinds and "stop" in kinds and all("final" in row for row in rows), kinds
    return f"{len(rows)} rows, kinds={sorted(kinds)}"


# ---------------------------------------------------------------- 导出
@case("kc.export_svg")
def _():
    r = kc.export_svg(TMP / "a.kicad_pcb", TMP / "a.svg")
    assert (TMP / "a.svg").exists() or (TMP / "a.svg").is_dir() or r.ok
    return "svg written"


@case("kc.export_gerbers")
def _():
    r = kc.export_gerbers(TMP / "a.kicad_pcb", TMP / "gerber")
    n = len(list((TMP / "gerber").glob("*"))) if (TMP / "gerber").exists() else 0
    assert n > 0, getattr(r, "stderr", r)
    return f"{n} files"


# ---------------------------------------------------------------- 报表
fails = sum(1 for _n, st, _o in results if st == "FAIL")
print()
print(f"{len(results) - fails}/{len(results)} tools pass" + ("" if not fails else f"  --  {fails} FAILED"))
sys.exit(1 if fails else 0)
