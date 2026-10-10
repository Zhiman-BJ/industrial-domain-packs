"""Print a symbol's pin table.

Getting the pin-to-net mapping right is worth more than everything downstream:
a wrong mapping caps an EDA-bench score at 0.05 no matter how good the layout
is. So look the pins up; never infer them from the part name.

    pins_of.py Interface_Expansion:MCP23017_SO
"""
from __future__ import annotations
import re
import sys
import pathlib

from tools import library
SYMBOL_DIRS = library.symbol_dirs()
RE_PIN = re.compile(
    r'\(pin\s+(input|output|bidirectional|tri_state|passive|free|unspecified|'
    r'power_in|power_out|open_collector|open_emitter|no_connect)\s+\w+'
    r'.*?\(name\s+"([^"]*)".*?\(number\s+"([^"]*)"', re.S)


def symbol_chunk(lib: str, name: str) -> str | None:
    for d in SYMBOL_DIRS:
        p = pathlib.Path(d) / f"{lib}.kicad_sym"
        if not p.is_file():
            continue
        txt = p.read_text(errors="replace")
        for chunk in re.split(r'\n(?:\t|  )\(symbol ', txt)[1:]:
            m = re.match(r'"([^"]+)"', chunk)
            if m and m.group(1) == name:
                return chunk
    return None


def _near(lib: str, name: str, limit: int = 5) -> list[str]:
    """Best guesses for a symbol name that was not found.

    Ranked by longest shared prefix with what was asked for, so a typo or a
    missing suffix lands on the real symbol: asking for
    `USB_C_Receptacle_USB2.0` suggests `USB_C_Receptacle_USB2.0_16P`, not some
    unrelated USB part. The named library is searched first and, if that yields
    nothing, every installed library.
    """
    def shared(a: str, b: str) -> int:
        a, b = a.lower(), b.lower()
        n = 0
        while n < min(len(a), len(b)) and a[n] == b[n]:
            n += 1
        return n

    def scan(files):
        found = []
        for f in files:
            for m in re.finditer(r'\n(?:\t|  )\(symbol "([^"]+)"',
                                 f.read_text(errors="replace")):
                n = m.group(1)
                if re.search(r"_\d+_\d+$", n):
                    continue
                s = shared(n, name)
                if s >= 3:
                    found.append((s, f"{f.stem}:{n}"))
        found.sort(key=lambda x: (-x[0], len(x[1])))
        return [n for _s, n in found[:limit]]

    dirs = [pathlib.Path(d) for d in SYMBOL_DIRS if pathlib.Path(d).is_dir()]
    same = [d / f"{lib}.kicad_sym" for d in dirs]
    hits = scan([f for f in same if f.is_file()])
    if hits:
        return hits
    return scan([f for d in dirs for f in sorted(d.glob("*.kicad_sym"))])


def pins(lib_id: str, strict: bool = True) -> list[tuple[str, str, str]]:
    """[(number, name, electrical_type)] for a symbol, following `extends`.

    Raises LookupError when the symbol is not found, instead of returning an
    empty list. A silent empty result is the worst outcome here: the caller
    maps zero pins to nets, the board still builds, and the score is capped by
    wrong_external_io_pin_net_mapping with nothing in the logs to explain it.
    Measured: a wrong library name (`Connector:USB_C_Receptacle_USB2.0`, which
    does not exist -- the real one is `..._USB2.0_16P`) produced 0 pins, 22
    unassigned pads and a project that looked fine. Pass strict=False for the
    old behaviour.
    """
    from tools import schematic
    lib, name = lib_id.split(":", 1)
    try:
        node = schematic.resolved_symbol(lib, name, SYMBOL_DIRS)
        facts = {}
        for unit, ps in schematic.symbol_units(node).items():
            for n, nm, x, y, ty in ps:
                if n in facts and facts[n] != (n, nm, ty):
                    raise ValueError(f"Conflicting pin {lib_id}.{n}")
                facts[n] = (n, nm, ty)
        if not facts and strict:
            raise LookupError(f"No pins in {lib_id}")
        return list(facts.values())
    except (LookupError, FileNotFoundError) as e:
        if not strict:
            return []
        raise LookupError(str(e) + f"; candidates: {_near(lib, name)}") from e


if __name__ == "__main__":
    lid = sys.argv[1]
    ps = pins(lid)
    print(f"{lid}: {len(ps)} pins")
    for num, nm, ty in sorted(ps, key=lambda x: (len(x[0]), x[0])):
        print(f"  {num:>4}  {nm:<12} {ty}")
