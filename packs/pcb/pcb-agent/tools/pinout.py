"""A connector pinout table -- the part of a design brief that says which
connector pin carries which signal. Every real board has one (it is the first
page of the interface spec); agents get it from the brief, a datasheet, or a
benchmark adapter. Format, JSON or YAML:

    {"connectors": [
       {"ref": "J1", "footprint": "Connector_USB:USB_C_Receptacle_...", "value": "USB-C",
        "pins": {"A1": "GND", "A4": "VBUS", "A6": "D+", "A7": "D-"}},
       {"ref": "J2", "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical",
        "pins": {"1": "GND", "2": "CC2", ...}}],
     "required_nets": ["GND", "VBUS", "D+", "D-"]}

`pins` is authoritative for connectivity; `required_nets` is what the checks
insist exists on the board. Nothing here depends on any benchmark.
"""
from __future__ import annotations

import json
import pathlib


def load(path: str | pathlib.Path) -> dict:
    p = pathlib.Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix in (".yaml", ".yml"):
        import yaml  # optional dependency
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    validate(data)
    return data


def validate(data: dict) -> None:
    if not isinstance(data, dict) or "connectors" not in data:
        raise ValueError("pinout 需要顶层 connectors 列表")
    seen = set()
    for c in data["connectors"]:
        ref = str(c.get("ref", ""))
        if not ref or ref in seen:
            raise ValueError(f"连接器位号缺失或重复: {ref!r}")
        seen.add(ref)
        if not isinstance(c.get("pins"), dict) or not c["pins"]:
            raise ValueError(f"{ref}: pins 必须是非空的 {{焊盘: 网络}}")
    data.setdefault("required_nets", sorted({n for c in data["connectors"] for n in c["pins"].values()}))


def apply(spec: dict, pinout: dict) -> dict:
    """Merge a pinout into a netlist spec: connectors named in the pinout get
    their pins, value and (if absent) footprint; connectors in the pinout but
    not in the spec are appended so nothing on the interface is silently
    dropped."""
    by_ref = {p.get("ref"): p for p in spec.setdefault("parts", [])}
    for c in pinout["connectors"]:
        part = by_ref.get(c["ref"])
        if part is None:
            part = {"ref": c["ref"]}
            spec["parts"].append(part); by_ref[c["ref"]] = part
        part.setdefault("nets", {}).update(c["pins"])
        for k in ("footprint", "symbol", "value", "rename_pads"):
            if c.get(k) and not part.get(k):
                part[k] = c[k]
    return spec


def required_nets(pinout: dict) -> list[str]:
    return list(pinout.get("required_nets") or [])
