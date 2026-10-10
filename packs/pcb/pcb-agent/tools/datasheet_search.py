"""Search installed CAD libraries. Immutable images may ship a prebuilt index.

The index is an observation cache, never qualification evidence. Each query
process checks the source inventory; modified or newly installed libraries fall
back to rebuilding. No reference designs are indexed.
"""
from __future__ import annotations

import dataclasses
import functools
import hashlib
import os
import json
import pathlib
import re
import typing as t

from tools import library
SYMBOL_DIRS = library.symbol_dirs()
FOOTPRINT_DIRS = library.footprint_dirs()

RE_SYM = re.compile(r'\(symbol\s+"([^"]+)"')
RE_PIN = re.compile(r'\(pin\s+(?:input|output|bidirectional|tri_state|passive|free|unspecified|power_in|power_out|open_collector|open_emitter|no_connect)\s')
RE_PAD = re.compile(r'\(pad\s+"([^"]*)"')
RE_DESCR = re.compile(r'\(property\s+"(?:ki_description|Description)"\s+"([^"]*)"')
RE_KEYW = re.compile(r'\(property\s+"ki_keywords"\s+"([^"]*)"')


@dataclasses.dataclass
class Symbol:
    lib: str
    name: str
    pins: int
    description: str
    keywords: str

    @property
    def lib_id(self) -> str:
        return f"{self.lib}:{self.name}"


@dataclasses.dataclass
class Footprint:
    lib: str
    name: str
    pads: int
    path: str
    description: str

    @property
    def lib_id(self) -> str:
        return f"{self.lib}:{self.name}"


def _inventory(kind, directories):
    digest=hashlib.sha256()
    pattern="*.kicad_sym" if kind=="symbols" else "*.pretty/*.kicad_mod"
    for directory in directories:
        digest.update(str(directory).encode()+b"\0")
        for path in sorted(pathlib.Path(directory).glob(pattern)):
            stat=path.stat()
            digest.update((str(path)+":"+str(stat.st_size)+":"+str(stat.st_mtime_ns)).encode()+b"\0")
    return digest.hexdigest()


@functools.lru_cache(maxsize=1)
def _index():
    # Fixed root-owned path in the read-only runtime, not model-selected JSON.
    path=pathlib.Path('/opt/pcb-library-index.json')
    try:
        if path.is_symlink() or path.stat().st_uid!=0 or path.stat().st_mode&0o022:return {}
        return json.loads(path.read_text())
    except (OSError,ValueError):return {}


def _cached(kind,directories):
    data=_index().get(kind,{})
    if data.get('inventory')==_inventory(kind,directories):return data.get('rows')
    return None


def build_index(destination):
    _symbols.cache_clear();_footprints.cache_clear()
    data={kind:{'inventory':_inventory(kind,dirs),'rows':[dataclasses.asdict(x) for x in build()]}
          for kind,dirs,build in [('symbols',SYMBOL_DIRS,_symbols),('footprints',FOOTPRINT_DIRS,_footprints)]}
    pathlib.Path(destination).write_text(json.dumps(data,separators=(',',':')))
    return {kind:len(row['rows']) for kind,row in data.items()}


@functools.lru_cache(maxsize=1)
def _symbols() -> list[Symbol]:
    saved = _cached("symbols", SYMBOL_DIRS)
    if saved is not None: return [Symbol(**row) for row in saved]
    # Count inherited and multi-unit pin identifiers, not indentation matches.
    from tools.sexpr import parse, children, child
    from tools.schematic import symbol_units
    out = []
    seen = set()
    for directory in SYMBOL_DIRS:
        for f in sorted(pathlib.Path(directory).glob("*.kicad_sym")):
            nodes = {str(n[1]): n for n in children(parse(f.read_text()), "symbol")}
            cache = {}
            def facts(name, trail=()):
                if name in cache: return cache[name]
                if name in trail or name not in nodes: raise ValueError(f"Invalid inheritance {f}:{name}")
                node = nodes[name]; ext = child(node, "extends")
                inherited_pins, props = facts(str(ext[1]), trail + (name,)) if ext else (set(), {})
                pins = inherited_pins | {str(p[0]) for unit in symbol_units(node).values() for p in unit}
                props = dict(props, **{str(p[1]): str(p[2]) for p in children(node, "property") if len(p)>2})
                cache[name] = pins, props
                return cache[name]
            for name in nodes:
                lid = f"{f.stem}:{name}"
                if lid in seen: continue
                seen.add(lid)
                pins, props = facts(name)
                out.append(Symbol(f.stem,name,len(pins),props.get("Description",props.get("ki_description","")),props.get("ki_keywords","")))
    return out


@functools.lru_cache(maxsize=1)
def _footprints() -> list[Footprint]:
    saved = _cached("footprints", FOOTPRINT_DIRS)
    if saved is not None: return [Footprint(**row) for row in saved]
    out: list[Footprint] = []
    for d in FOOTPRINT_DIRS:
        p = pathlib.Path(d)
        if not p.is_dir():
            continue
        for lib in sorted(p.glob("*.pretty")):
            for f in sorted(lib.glob("*.kicad_mod")):
                txt = f.read_text(errors="replace")
                d_ = re.search(r'\(descr\s+"([^"]*)"', txt)
                pads = {n for n in RE_PAD.findall(txt) if n}
                out.append(Footprint(lib.stem, f.stem, len(pads), str(f),
                                     d_.group(1) if d_ else ""))
    return out


def _score(hay: str, terms: list[str]) -> int:
    hay = hay.lower()
    return sum(3 if hay.startswith(t) else (1 if t in hay else 0) for t in terms)

def _identity_score(name: str, query: str) -> int:
    """Prefer exact library identities and token boundaries over broad substrings."""
    q=query.lower().strip(); n=name.lower()
    if n == q: return 1000
    if n.endswith(':'+q) or n == q.replace(':','_'): return 800
    tokens=[x for x in re.split(r"[\s_:/\-]+",q) if x]
    return sum(40 if re.search(r'(^|[_:/-])'+re.escape(t)+r'($|[_:/-])',n) else 0 for t in tokens)


def find_symbol(query: str, pins: int | None = None, limit: int = 10) -> list[dict]:
    """Fuzzy-search symbols. `pins` filters to an exact pin count when given."""
    terms = [w for w in re.split(r"[\s_\-/]+", query.lower()) if w]
    hits = []
    for s in _symbols():
        if pins is not None and s.pins != pins:
            continue
        sc = _identity_score(s.lib_id, query) + _score(s.name, terms) * 2 + _score(s.description, terms) + _score(s.keywords, terms)
        if sc:
            hits.append((sc, s))
    hits.sort(key=lambda x: (-x[0], x[1].lib_id))
    return [dataclasses.asdict(s) | {"lib_id": s.lib_id, "score": sc}
            for sc, s in hits[:limit]]


def find_footprint(query: str, pads: int | None = None, limit: int = 10) -> list[dict]:
    """Fuzzy-search footprints. `pads` filters to an exact pad count."""
    terms = [w for w in re.split(r"[\s_\-/]+", query.lower()) if w]
    hits = []
    for f in _footprints():
        if pads is not None and f.pads != pads:
            continue
        sc = _identity_score(f.lib_id, query) + _score(f.name, terms) * 2 + _score(f.description, terms)
        if sc:
            hits.append((sc, f))
    hits.sort(key=lambda x: (-x[0], x[1].lib_id))
    return [dataclasses.asdict(f) | {"lib_id": f.lib_id, "score": sc}
            for sc, f in hits[:limit]]


def footprint_dir(lib: str, name: str | None = None) -> str | None:
    """Directory to hand to pcb_editor.add_footprint()."""
    for d in FOOTPRINT_DIRS:
        p = pathlib.Path(d) / f"{lib}.pretty"
        if p.is_dir() and (name is None or (p / (name + '.kicad_mod')).is_file()):
            return str(p)
    return None


def stats() -> dict:
    return {"symbols": len(_symbols()), "footprints": len(_footprints()),
            "symbol_dirs": [d for d in SYMBOL_DIRS if pathlib.Path(d).is_dir()],
            "footprint_dirs": [d for d in FOOTPRINT_DIRS if pathlib.Path(d).is_dir()]}


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "--build-index":
        print(json.dumps(build_index(sys.argv[2])));sys.exit(0)
    if len(sys.argv) > 1:
        q = " ".join(sys.argv[1:])
        print(json.dumps({"symbols": find_symbol(q, limit=5),
                          "footprints": find_footprint(q, limit=5)},
                         indent=2, ensure_ascii=False))
    else:
        print(json.dumps(stats(), indent=2))
