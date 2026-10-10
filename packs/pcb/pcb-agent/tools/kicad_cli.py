"""Thin, honest wrapper around `kicad-cli`.

Every call returns a CliResult; nothing raises on a non-zero exit, because a
failing ERC/DRC is data, not an error.
"""
from __future__ import annotations

import dataclasses
import contextlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import typing as t

_MAC_CLI_CANDIDATES = (
    "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
    "/Applications/KiCad.app/Contents/MacOS/kicad-cli",
)
KICAD_CLI = (os.environ.get("KICAD_CLI") or shutil.which("kicad-cli") or
             next((p for p in _MAC_CLI_CANDIDATES if pathlib.Path(p).is_file()),
                  "/usr/bin/kicad-cli"))


@dataclasses.dataclass
class CliResult:
    returncode: int
    stdout: str
    stderr: str
    argv: list[str]

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def __str__(self) -> str:
        return f"<{' '.join(self.argv[1:4])}… rc={self.returncode}>"


def _run(args: list[str], timeout: int = 900) -> CliResult:
    argv = [KICAD_CLI, *args]
    try:
        # Controller-side visual observations also use this wrapper. Native
        # CAD subprocesses must not inherit provider credentials.
        env={k:v for k,v in os.environ.items() if not any(tag in k.upper() for tag in ('KEY','TOKEN','SECRET','PASSWORD','MODEL_ENDPOINT'))}
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
        return CliResult(p.returncode, p.stdout, p.stderr, argv)
    except OSError as e:
        return CliResult(127, "", str(e), argv)
    except subprocess.TimeoutExpired as error:
        def decoded(value):return value.decode(errors='replace') if isinstance(value,bytes) else value or ''
        return CliResult(124, decoded(error.stdout), decoded(error.stderr)+f"\ntimeout after {timeout}s", argv)


def version() -> str:
    """KiCad version string, e.g. '9.0.2'."""
    r = _run(["--version"], timeout=60)
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "unknown"


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------
@contextlib.contextmanager
def _native_project(source, prefix):
    """Writable native context with unchanged CAD/rules and original library URIs.

    KiCad 9 can ignore local library tables when a project is read-only. Only
    disposable files are writable; missing libraries remain missing.
    """
    source=pathlib.Path(source).resolve()
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        from tools import hierarchy
        root=pathlib.Path(tmp)
        schematic=source if source.suffix=='.kicad_sch' else source.with_suffix('.kicad_sch')
        if schematic.is_file():hierarchy.copy_sheets(schematic,root)
        shutil.copy2(source,root/source.name)
        (root/source.name).chmod(0o600)
        for suffix in ('.kicad_pro','.kicad_dru'):
            project=source.with_suffix(suffix)
            if project.is_file():
                shutil.copy2(project,root/project.name)
                (root/project.name).chmod(0o600)
        for name in ('sym-lib-table','fp-lib-table'):
            table=source.parent/name
            if table.is_file():
                # Resolve project-local URIs against the frozen candidate,
                # not the temporary directory; do not synthesize missing libs.
                from tools.sexpr import parse, children, child, dump, Quoted
                tree=parse(table.read_text())
                for lib in children(tree,'lib'):
                    uri=child(lib,'uri')
                    if uri:
                        value=str(uri[1]).replace('${KIPRJMOD}',str(source.parent))
                        if not pathlib.Path(value).is_absolute() and '${' not in value:
                            value=str(source.parent/value)
                        uri[1]=Quoted(value)
                (root/name).write_text(dump(tree))
        yield root/source.name


def erc(schematic: str | pathlib.Path, out_json: str | pathlib.Path) -> dict:
    """Run ERC and return the parsed report.

    Returns {"available", "counts": {severity: n}, "violations": [...], "raw"}.
    `counts` is what you compare against a baseline; an absolute count is
    rarely meaningful because real open-hardware boards ship with violations.
    """
    out_json = pathlib.Path(out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.unlink(missing_ok=True)
    with _native_project(schematic,'pcb-erc-') as native:
        r = _run(["sch", "erc", "--format", "json", "--severity-all",
                  "--output", str(out_json.resolve()), str(native)])
    from tools.erc_positions import normalize
    return normalize(_parse_report(r, out_json, kind="erc"), schematic)


def drc(board: str | pathlib.Path, out_json: str | pathlib.Path,
        schematic_parity: bool = True) -> dict:
    """Run DRC and return the parsed report.

    Unconnected-item checks detect missing physical copper connections.
    `schematic_parity` additionally compares the declared schematic/PCB
    component and net memberships; it is not a physical continuity test.
    """
    out_json = pathlib.Path(out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.unlink(missing_ok=True)
    args = ["pcb", "drc", "--format", "json", "--severity-all",
            "--output", str(out_json)]
    if schematic_parity:
        args.append("--schematic-parity")
    # Use the same native context for model-writable and independently mounted
    # read-only candidates. Native UUIDs and project rules are preserved.
    args[args.index('--output')+1]=str(out_json.resolve())
    with _native_project(board,'pcb-drc-') as native:
        args.append(str(native))
        return _parse_report(_run(args), out_json, kind="drc")


def _parse_report(r: CliResult, out_json: pathlib.Path, kind: str) -> dict:
    counts: dict[str, int] = {}
    violations: list[dict] = []
    errors: list[str] = []
    data = None
    if out_json.exists():
        try:
            data = json.loads(out_json.read_text(encoding="utf-8"))
        except Exception as error:
            errors.append('Invalid report JSON: ' + str(error))
            data = None
    if isinstance(data, dict):
        if kind == "erc" and "sheets" in data:
            flattened = []
            sheets = data['sheets']
            if not isinstance(sheets, list):
                errors.append('ERC sheets must be a list')
            else:
                for sheet in sheets:
                    if not isinstance(sheet, dict) or not isinstance(sheet.get('violations'), list):
                        errors.append('ERC sheet must contain a violation list'); continue
                    for entry in sheet['violations']:
                        if not isinstance(entry, dict):
                            errors.append('ERC violation must be an object'); continue
                        flattened.append(dict(entry, sheet_path=sheet.get('path'), sheet_uuid_path=sheet.get('uuid_path')))
            data['violations'] = flattened
        if not any(key in data for key in ('violations', 'unconnected_items', 'schematic_parity')):
            errors.append('Native report has no recognized violation groups')
        for key in ("violations", "unconnected_items", "schematic_parity"):
            entries = data.get(key, [])
            if not isinstance(entries, list):
                errors.append(key + ': expected a violation list'); continue
            for v in entries:
                if not isinstance(v, dict):
                    errors.append(key + ': violation must be an object'); continue
                sev = str(v.get("severity", "unknown")).lower()
                if sev not in ('error', 'warning', 'ignore', 'exclusion'):
                    errors.append(key + ': unknown violation severity: ' + sev)
                items = v.get('items', [])
                if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                    errors.append(key + ': invalid native object list'); continue
                counts[sev] = counts.get(sev, 0) + 1
                violations.append({
                    "severity": sev,
                    "type": v.get("type"),
                    "description": v.get("description"),
                    "group": key,
                    "items": items,
                    **{field: v[field] for field in ('sheet_path', 'sheet_uuid_path') if field in v},
                })
    else:  # fall back to the human-readable line kicad-cli always prints
        if not errors:
            errors.append('Missing report or invalid report root')
        m = re.search(r"Found (\d+) violations", r.stdout)
        if m:
            counts["total_reported"] = int(m.group(1))
    counts.setdefault("error", counts.get("error", 0))
    counts["total"] = sum(v for k, v in counts.items() if k != "total")
    return {
        "kind": kind,
        "coordinate_units": data.get('coordinate_units','unspecified') if isinstance(data,dict) else 'unspecified',
        "report_valid": isinstance(data, dict) and not errors,
        "report_errors": errors,
        "returncode": r.returncode,
        "available": r.returncode in (0, 5),   # 5 == violations found
        "counts": counts,
        "violations": violations,
        "report_path": str(out_json),
        "raw_stdout": r.stdout[-4000:],
        "raw_stderr": r.stderr[-2000:],
    }


# --------------------------------------------------------------------------
# exports -- useful both for fabrication output and for eyeballing a board
# --------------------------------------------------------------------------
def drc_summary(board, out_json, top: int = 3) -> dict:
    """DRC as something you can act on: counts by type, worst types first, with
    a few located examples each. The raw report is 100 KB of JSON; reading it
    into the conversation is how one run burned six million prompt tokens."""
    import collections
    d = drc(board, out_json)
    if not d.get("available"):
        return d
    errs = [v for v in d["violations"] if v.get("severity") == "error"]
    by_type = collections.Counter(v["type"] for v in errs)
    examples = {}
    for typ, _n in by_type.most_common(top):
        ex = []
        for v in errs:
            if v["type"] != typ:
                continue
            items = v.get("items") or []
            pos = items[0].get("pos") if items else None
            where = f"({pos['x']:.1f},{pos['y']:.1f})" if pos else ""
            ex.append(f"{(v.get('description') or '')[:70]} {where}")
            if len(ex) >= 3:
                break
        examples[typ] = ex
    return {"available": True, "report_valid": d.get("report_valid", False), "errors": d["counts"].get("error", 0), "warnings": d["counts"].get("warning", 0),
            "by_type": dict(by_type.most_common()), "examples": examples,
            "unconnected": sum(v.get('group') == 'unconnected_items' for v in d.get('violations', [])),
            "report_path": d.get("report_path")}


def export_netlist(schematic, out_path) -> CliResult:
    Path = pathlib.Path
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).unlink(missing_ok=True)
    return _run(["sch", "export", "netlist", "--format", "kicadxml", "--output", str(out_path), str(schematic)])


def export_gerbers(board, out_dir) -> CliResult:
    pathlib.Path(out_dir).mkdir(parents=True, exist_ok=True)
    return _run(["pcb", "export", "gerbers", "--output", str(out_dir), str(board)])


def export_svg(board, out_path, layers: str = "F.Cu,B.Cu,Edge.Cuts") -> CliResult:
    pathlib.Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    return _run(["pcb", "export", "svg", "--layers", layers,
                 "--output", str(out_path), str(board)])


def export_bom(schematic, out_path) -> CliResult:
    return _run(["sch", "export", "bom", "--output", str(out_path), str(schematic)])


# --------------------------------------------------------------------------
# libraries
# --------------------------------------------------------------------------
def list_symbol_libs(search_dirs: t.Iterable[str] = ("/usr/share/kicad/symbols",)) -> list[str]:
    out = []
    for d in search_dirs:
        p = pathlib.Path(d)
        if p.is_dir():
            out += sorted(f.stem for f in p.glob("*.kicad_sym"))
    return out


def list_footprint_libs(search_dirs: t.Iterable[str] = ("/usr/share/kicad/footprints",)) -> list[str]:
    out = []
    for d in search_dirs:
        p = pathlib.Path(d)
        if p.is_dir():
            out += sorted(f.stem for f in p.glob("*.pretty"))
    return out


def self_test() -> dict:
    """One call that tells an agent what it is working with."""
    return {
        "kicad_cli": KICAD_CLI,
        "version": version(),
        "symbol_libs": len(list_symbol_libs()),
        "footprint_libs": len(list_footprint_libs()),
        "has_schematic_parity": "--schematic-parity" in _run(["pcb", "drc", "--help"]).stdout,
    }


if __name__ == "__main__":
    print(json.dumps(self_test(), indent=2))


def export_drill(board, out_dir):
    """Export Excellon drill independently of Gerber export."""
    pathlib.Path(out_dir).mkdir(parents=True, exist_ok=True)
    return _run(["pcb", "export", "drill", "--format", "excellon", "--output", str(out_dir), str(board)])


def export_positions(board, out_path):
    """Export placement positions with explicit units and side field."""
    pathlib.Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    return _run(["pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both", "--output", str(out_path), str(board)])


def fill_zones_file(board):
    """Fill in a subprocess; a pcbnew crash cannot kill the orchestration process.

    Only replace the original board after a successful load/fill/save. Unsupported
    installations report UNKNOWN; zone bounding boxes are never copper evidence.
    """
    import tempfile, os, sys
    source = pathlib.Path(board).resolve()
    fd, tmp = tempfile.mkstemp(prefix=".fill-", suffix=".kicad_pcb", dir=source.parent)
    os.close(fd)
    script = "import pcbnew,sys; b=pcbnew.LoadBoard(sys.argv[1]); b.BuildConnectivity(); pcbnew.ZONE_FILLER(b).Fill(b.Zones()); pcbnew.SaveBoard(sys.argv[2],b)"
    try:
        r = subprocess.run([sys.executable, "-c", script, str(source), tmp], capture_output=True, text=True, timeout=120)
        if r.returncode or not pathlib.Path(tmp).stat().st_size:
            return {"status": "UNKNOWN", "issues": ["Copper fill failed"], "returncode": r.returncode, "stderr": r.stderr[-1000:]}
        os.replace(tmp, source)
        return {"status": "PASS", "issues": [], "scope": "zone fill execution"}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"status": "UNKNOWN", "issues": [str(e)]}
    finally:
        pathlib.Path(tmp).unlink(missing_ok=True)
