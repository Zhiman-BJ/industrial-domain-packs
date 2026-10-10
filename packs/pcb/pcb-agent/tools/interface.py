"""A board's external interface as data: which nets leave the board, through
which connector pads, with what role and what protocol family.

This is the interface specification every design has -- the pinout table --
in a form that can be *extracted from a finished board* and *compared* with
what the specification asked for, independent of reference designators. Two
boards that put the same signals on the same connector pins have the same
signature even if one calls the header J1 and the other K2.

    spec = interface.from_pinout(pinout)          # what was asked
    got  = interface.from_board(board)            # what was built
    interface.compare(got, spec)                  # -> f1, missing, extra

The JSON form written by to_json()/read by from_json() is the general
"interface contract" format: a list of nets, each with role, families and
endpoints `kind:role:family:pad`. It is deliberately refdes-free.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import re
from collections import Counter

# --- general engineering vocabulary --------------------------------------
GROUND_NAMES = {"0", "GND", "AGND", "DGND", "PGND", "GNDA", "GNDD", "EARTH", "SHIELD"}
POWER_RE = re.compile(r"^(V(CC|DD|DDA|BAT|IN|OUT|SYS|BUS|USB|REF|PWR)|\+?\d+(\.\d+)?V\d*|\d+V\d+|P\d+V\d*)$", re.I)

FAMILY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "usb": ("USB", "USB-C", "USBC", "TYPE-C", "D+", "D-", "DP", "DN", "VBUS", "CC1", "CC2"),
    "i2c": ("I2C", "SDA", "SCL", "EASYC", "QWIIC", "STEMMA"),
    "spi": ("SPI", "MOSI", "MISO", "SCK", "SCLK", "NCS", "CS"),
    "uart": ("UART", "TXD", "RXD", "TX", "RX", "SERIAL"),
    "can": ("CAN", "CANH", "CANL"),
    "rs485": ("RS485", "RS-485", "485"),
    "ethernet": ("ETH", "ETHERNET", "RJ45", "MDI"),
    "hdmi": ("HDMI",), "display": ("DISPLAY", "DSI", "LVDS", "LCD", "EDP"),
    "camera": ("CAM", "CAMERA", "CSI", "OV9281", "IMX"), "mipi": ("MIPI", "CSI", "DSI"),
    "audio": ("AUDIO", "I2S", "MIC", "SPK", "LINEIN", "LINEOUT"),
    "sdcard": ("SDCARD", "MICROSD", "SDIO", "DAT0", "CMD"),
    "debug": ("SWD", "SWDIO", "SWCLK", "JTAG", "DEBUG", "PROG", "UPDI", "BOOT", "RESET", "NRST", "INT"),
    "power": ("POWER", "VBUS", "VCC", "VDD", "VIN", "VOUT", "5V", "3V3", "GND"),
    "battery": ("BAT", "VBAT", "LIPO"), "antenna": ("ANT", "RF", "SMA", "UFL"),
    "pcie": ("PCIE", "PERST", "REFCLK"), "m2": ("M.2", "NGFF", "KEY-M", "KEY-B"), "fan": ("FAN",),
    "motor": ("MOTOR", "H-BRIDGE", "HBRIDGE", "MC33887", "DRV8", "TB6612", "L298"),
    "encoder": ("ENCODER",), "rtc": ("RTC", "CR2032"), "eeprom": ("EEPROM",), "flash": ("FLASH", "QSPI"),
    "dmx": ("DMX",), "sim": ("SIM", "USIM"), "ffc": ("FFC", "FPC"),
    "gpio": ("GPIO", "HEADER", "BREAKOUT", "EXPANSION", "IO"),
}
CONNECTOR_PREFIXES = ("J", "P", "X", "CN", "CON", "K", "SW", "M")
NON_INTERFACE_PREFIXES = ("#", "D", "FID", "H", "MH", "MK", "MP", "NT", "TP", "C", "R", "L", "U", "IC", "Q", "Y", "F", "FB")
CONNECTOR_KIND_KEYWORDS = (
    ("usb", ("USB", "TYPE_C", "TYPE-C")), ("hdmi", ("HDMI",)), ("m2", ("M.2", "NGFF")),
    ("pcie", ("PCIE",)), ("ethernet", ("RJ45", "MAGJACK", "ETH")), ("ffc", ("FFC", "FPC")),
    ("sdcard", ("MICROSD", "SDCARD")), ("sim", ("SIM",)), ("audio", ("AUDIO", "JACK")),
    ("rf", ("SMA", "UFL", "U.FL", "COAX", "ANT")), ("battery", ("BAT", "LIPO", "CR20")),
    ("fan", ("FAN",)), ("switch", ("SWITCH", "BUTTON", "PUSH")), ("terminal", ("TERMINAL", "SCREW")),
    ("header", ("HEADER", "PINHEADER", "CONN_", "SOCKET")),   # Conn_01xNN is KiCad's own generic header naming
)


_ROLE_OVERRIDE: dict = {}


def role_of(net: str) -> str:
    if net in _ROLE_OVERRIDE:
        return _ROLE_OVERRIDE[net]
    n = str(net or "").upper().strip()
    if not n or n.startswith("UNCONNECTED"):
        return "unconnected"
    if n in GROUND_NAMES or n.endswith("GND"):
        return "ground"
    if POWER_RE.match(n):
        return "power"
    return "signal"


def families_of(text: str) -> set[str]:
    """Interface families named in some text (a net name, a part's value...).
    Keywords match as whole tokens: 'I2C_1' is i2c, 'I2C1' is not, which is
    how engineers actually write bus names. Hierarchical net names keep their
    sheet path ('/DSI to LVDS/LVDS.D0_P'); the path is part of the text on
    purpose -- a sheet called 'DSI to LVDS' says what the net is for."""
    t = f" {str(text or '').upper().replace('-', '_')} "
    out = set()
    for fam, kws in FAMILY_KEYWORDS.items():
        for kw in kws:
            k = kw.upper().replace("-", "_")
            if re.fullmatch(r"[A-Z0-9_+]+", k):
                if re.search(rf"(?<![A-Z0-9]){re.escape(k)}(?![A-Z0-9])", t):
                    out.add(fam); break
            elif k in t:
                out.add(fam); break
    if re.search(r"\bD[+-]\b|\bD_[PN]\b", t): out.add("usb")
    if re.search(r"\b(SDA|SCL)\d*\b", t): out.add("i2c")
    if re.search(r"\b(MOSI|MISO|SCK|SCLK)\d*\b", t): out.add("spi")
    if re.search(r"\b(TX|RX)D?\d*\b", t): out.add("uart")
    return out


def ref_prefix(ref: str) -> str:
    m = re.match(r"^[A-Za-z]+", ref or "")
    return m.group(0).upper() if m else ""


def is_interface_part(ref: str, footprint: str = "", value: str = "") -> bool:
    """Is this part somewhere a signal leaves the board?  Decided the way a
    reviewer does: mechanical parts (mounting posts MP/MH/MK, fiducials, test
    points, logos) and on-board components (R, C, U, D, ...) are not; anything
    with a connector reference prefix (J, P, X, CN, K, SW, M) or a connector
    footprint is."""
    p = ref_prefix(ref)
    text = f"{footprint} {value}".upper()
    if p in NON_INTERFACE_PREFIXES or p.startswith(("MH", "MK", "MP", "FID", "TP", "NT")):
        return False
    if any(tok in text for tok in ("MOUNTINGHOLE", "TESTPOINT", "FIDUCIAL", "LOGO", "NETTIE", "TVS", "ESD", "LED")):
        return False
    if p.startswith(CONNECTOR_PREFIXES):
        return True
    return any(tok in text for kind, toks in CONNECTOR_KIND_KEYWORDS for tok in toks)


def connector_kind(ref: str, footprint: str = "", value: str = "", pad_names=()) -> str:
    text = f"{ref} {footprint} {value}".upper()
    if {"A1", "A4", "A5", "A6", "A7", "B12", "B1"} & {str(p).upper() for p in pad_names}:
        return "usb"
    for kind, toks in CONNECTOR_KIND_KEYWORDS:
        if any(tok in text for tok in toks):
            return kind
    p = ref_prefix(ref)
    return "switch" if p == "SW" else ("connector" if p in ("J", "K", "P", "CN", "CON", "X") else "external")


@dataclasses.dataclass(frozen=True)
class Endpoint:
    kind: str; role: str; family: str; pad: str
    def token(self) -> str:
        return f"{self.kind}:{self.role}:{self.family}:{self.pad}"


@dataclasses.dataclass
class Net:
    name: str; role: str; families: frozenset; endpoints: tuple


@dataclasses.dataclass
class Interface:
    nets: list
    def signature(self) -> Counter:
        """Refdes-invariant multiset of per-net keys."""
        sig = Counter()
        for n in self.nets:
            deg = len(n.endpoints)
            bucket = "1" if deg <= 1 else "2" if deg == 2 else "3to4" if deg <= 4 else "5to8" if deg <= 8 else "9+"
            fams = ".".join(sorted(n.families)) or "generic"
            eps = tuple(sorted(e.token() for e in n.endpoints))
            sig[(n.role, fams, bucket) + eps] += 1
        return sig
    def summary(self) -> str:
        lines = [f"{len(self.nets)} external nets"]
        for n in sorted(self.nets, key=lambda n: (n.role, n.name)):
            lines.append(f"  {n.name:<8} [{n.role}/{'.'.join(sorted(n.families)) or '-'}]  " + ", ".join(f"{e.kind}.{e.pad}" for e in n.endpoints))
        return "\n".join(lines)


def _endpoint_family(kind: str, role: str, part_text: str, net_name: str) -> str:
    """The family of a connector PIN comes from what the connector is (a USB
    receptacle, an easyC/Qwiic I2C socket, a generic pin header), read off its
    ref/kind/footprint/value -- not from the signal routed to it. The signal's
    family belongs to the net. Mixing the two makes every header pin look like
    whatever bus happens to cross it."""
    if role in ("power", "ground"):
        return "power"
    fams = families_of(part_text) - {"power"}
    if kind == "usb": fams.add("usb")
    if len(fams) > 1: fams.discard("gpio")
    return ".".join(sorted(fams)) if fams else "gpio"     # a CSI/MIPI socket is both: keep both


def _build(pads: list) -> Interface:
    """pads: [(ref, footprint, value, pad_names, pad, net)] for external parts."""
    by_net: dict[str, list] = {}
    for ref, fp, val, names, pad, net in pads:
        if not net: continue
        role = role_of(net)
        if role == "unconnected": continue
        kind = connector_kind(ref, fp, val, names)
        fam = _endpoint_family(kind, role, f"{ref} {kind} {fp} {val}", net)
        by_net.setdefault(net, []).append(Endpoint(kind, "power" if role in ("power", "ground") and role == "power" else role, fam, str(pad)))
    nets = []
    for net, eps in by_net.items():
        role = role_of(net)
        fams = {e.family for e in eps} | (families_of(net) - {"power"})
        if role in ("power", "ground"): fams = {"power"} | (families_of(net) - {"power"})
        if len(fams) > 1: fams.discard("gpio")
        nets.append(Net(net, role, frozenset(fams or {"gpio"}), tuple(eps)))
    return Interface(nets)


def from_board(board, roles: dict | None = None) -> Interface:
    """`roles` = {net_name: role} declared by the specification. A spec is the
    authority on what a net IS; naming heuristics only fill in where the spec
    is silent. (A 5 V rail named VUSB is power by any sane reading, but if the
    interface spec files it as a signal, that is what the board must present.)"""
    global _ROLE_OVERRIDE
    _ROLE_OVERRIDE = dict(roles or {})
    pads = []
    for fp in board.GetFootprints():
        ref, fpid, val = fp.GetReference(), fp.GetFPIDAsString(), fp.GetValue()
        if not is_interface_part(ref, fpid, val): continue
        # A pad NAME identifies a pin. Footprints often carry several pads
        # with one name (USB-C shields, thermal tabs); they are one pin of the
        # interface, not several endpoints -- the specification lists it once.
        by_name = {}
        for p in fp.Pads():
            by_name.setdefault(p.GetName(), p)
        names = list(by_name)
        for name, p in by_name.items():
            pads.append((ref, fpid, val, names, name, str(p.GetNetname())))
    return _build(pads)


def from_pinout(pinout: dict) -> Interface:
    global _ROLE_OVERRIDE
    _ROLE_OVERRIDE = dict(pinout.get("roles") or {})
    pads = []
    for c in pinout.get("connectors", []):
        names = list(c.get("pins", {}).keys()) + list(c.get("pad_names") or [])
        for pad, net in c.get("pins", {}).items():
            pads.append((c["ref"], c.get("footprint", ""), c.get("value", ""), names, pad, net))
    return _build(pads)


def to_json(iface: Interface) -> dict:
    return {"schema": "interface-contract/1", "nets": [
        {"name": n.name, "role": n.role, "families": sorted(n.families), "endpoints": [e.token() for e in n.endpoints]}
        for n in iface.nets]}


def from_json(path: str | pathlib.Path) -> Interface:
    """Read an interface contract. Accepts our own to_json() form and the
    profile form some datasets ship (a list of {key:[...], count}) -- both are
    just serialisations of nets-with-endpoints."""
    d = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    nets = []
    if "nets" in d:
        for n in d["nets"]:
            eps = tuple(Endpoint(*e.split(":")) for e in n["endpoints"] if e.count(":") == 3)
            nets.append(Net(n["name"], n["role"], frozenset(n["families"]), eps))
        return Interface(nets)
    prof = d.get("semantic_external_net_profile") or d.get("profile") or []
    used: set[str] = set()
    for entry in prof:
        key = entry.get("key") or []
        if len(key) < 5: continue
        role, fams = key[1], set(key[2].split("."))
        eps = tuple(Endpoint(*t.split(":")) for t in key[4:] if t.count(":") == 3)
        base = "GND" if role == "ground" else ("VBUS" if role == "power" and "usb" in fams else "VCC" if role == "power" else sorted(fams)[0].upper())
        if role == "signal":
            usb_pad = next((e.pad for e in eps if e.kind == "usb"), None)
            base = {"A6": "D+", "B6": "D+", "A7": "D-", "B7": "D-", "A5": "CC1", "B5": "CC2", "A8": "SBU1", "B8": "SBU2",
                    "A4": "VUSB", "A9": "VUSB", "B4": "VUSB", "B9": "VUSB"}.get(usb_pad, base)
        for _ in range(int(entry.get("count", 1))):
            name, i = base if (role != "signal" or base in ("D+", "D-", "CC1", "CC2", "SBU1", "SBU2", "VUSB")) else f"{base}_1", 2
            while name in used:
                name, i = f"{base}_{i}", i + 1
            used.add(name)
            nets.append(Net(name, role, frozenset(fams), eps))
    return Interface(nets)


def compare(got: Interface, want: Interface) -> dict:
    a, b = got.signature(), want.signature()
    inter = sum(min(a[k], b[k]) for k in a)
    prec = inter / max(sum(a.values()), 1); rec = inter / max(sum(b.values()), 1)
    f1 = 0.0 if prec + rec == 0 else 2 * prec * rec / (prec + rec)
    miss = [k for k in b if a[k] < b[k]]; extra = [k for k in a if b[k] < a[k]]
    return {"f1": round(f1, 3), "recall": round(rec, 3), "precision": round(prec, 3),
            "missing": [list(k) for k in miss][:10], "extra": [list(k) for k in extra][:10]}


# ---- turning a contract back into buildable connectors --------------------
def instances(iface: Interface, kind: str) -> int:
    mult = Counter()
    for n in iface.nets:
        for pad, k in Counter(e.pad for e in n.endpoints if e.kind == kind).items():
            mult[pad] += k
    return max(mult.values(), default=0)


def assign(iface: Interface, kind: str) -> list[dict]:
    """Per physical connector of this kind: {pad: net}. Greedy, family-coherent."""
    k = instances(iface, kind)
    inst: list[dict] = [dict() for _ in range(k)]
    for n in sorted(iface.nets, key=lambda n: (".".join(sorted(n.families)), n.name)):
        for e in n.endpoints:
            if e.kind != kind: continue
            for m in inst:
                if e.pad not in m:
                    m[e.pad] = n.name; break
    return [dict(sorted(m.items(), key=lambda kv: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", kv[0])])) for m in inst if m]


def to_pinout(iface: Interface, ref_prefix: str = "J") -> dict:
    """Interface contract -> pinout connectors (refs assigned here; a design
    may rename them freely, the signature does not depend on them)."""
    conns, n = [], 1
    kinds = sorted({e.kind for net in iface.nets for e in net.endpoints})
    KIND_VALUE = {"usb": "USB-C", "header": "Header", "switch": "Switch", "terminal": "Terminal"}
    for kind in kinds:
        # What the connector IS. For generic kinds the contract's endpoint
        # family says what sort of connector it is (an i2c socket, ...).
        fams = {e.family for net in iface.nets for e in net.endpoints if e.kind == kind and e.family not in ("power", "gpio")}
        value = KIND_VALUE.get(kind) or (" ".join(f.upper() for f in sorted(fams)) if fams else kind.capitalize())
        for i, pins in enumerate(assign(iface, kind)):
            ref = f"JP{n}" if kind == "external" else f"{ref_prefix}{n}"
            conns.append({"ref": ref, "kind": kind, "instance": i, "pins": pins, "value": value,
                          "pad_names": sorted({e.pad for net in iface.nets for e in net.endpoints if e.kind == kind})})
            n += 1
    return {"connectors": conns, "required_nets": [n_.name for n_ in iface.nets],
            "roles": {n_.name: n_.role for n_ in iface.nets}}


if __name__ == "__main__":
    import sys
    i = from_json(sys.argv[1]); print(i.summary())
    for c in to_pinout(i)["connectors"]: print(f"{c['ref']} ({c['kind']}): {c['pins']}")
