"""Host desktop viewers. Export immutable evidence; never change design acceptance."""

import json
import math
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

NETLIST_VIEWERS = {"yosys_show", "yosys_viz", "netlistsvg"}

FORMATS = {
    "yosys_show": [".json"],
    "yosys_viz": [".json"],
    "netlistsvg": [".json"],
    "gtkwave": [".vcd", ".fst"],
    "klayout": [".gds", ".gds2", ".oas", ".oasis", ".lyrdb", ".lvsdb", ".l2n"],
    "magic": [".mag", ".gds", ".gds2"],
    "openroad": [".odb", ".def"],
}
BUNDLES = {
    "gtkwave": ["/Applications/gtkwave.app/Contents/Resources/bin/gtkwave"],
    "klayout": ["/Applications/klayout.app/Contents/MacOS/klayout"],
}


def executable(viewer):
    configured = os.environ.get("EDA_VIEWER_" + viewer.upper())
    candidates = [configured] if configured else [shutil.which(viewer), *BUNDLES.get(viewer, [])]
    return next(
        (str(Path(p).resolve()) for p in candidates if p and Path(p).is_file() and os.access(p, os.X_OK)),
        None,
    )


def viewer_capabilities():
    return {
        "execution_host": "local",
        "remote_supported": False,
        "viewers": {
            name: {
                "formats": formats,
                "executable": executable(name),
                "available": executable(name) is not None
                and (name != "netlistsvg" or shutil.which("node") is not None),
                "configuration": "EDA_VIEWER_" + name.upper(),
                **(
                    {
                        "available": None,
                        "executable": None,
                        "execution_host": "project_runtime",
                        "configuration": None,
                        "requirements": [
                            "Yosys and Graphviz dot in project runtime",
                            "browser on MCP host for launch",
                        ],
                        "artifact_types": ["netlist.json"],
                        "render_only": True,
                        "availability_scope": "Checked against the selected project runtime by open_viewer; null is not unavailable",
                        "output_format": "SVG and DOT; schematic"
                        if name == "yosys_show"
                        else "SVG and DOT; data flow",
                        "default_for_netlist": name == "yosys_show",
                    }
                    if name in {"yosys_show", "yosys_viz"}
                    else {}
                ),
                **(
                    {
                        "artifact_types": ["netlist.json"],
                        "input_format": "Yosys write_json; Verilog must first be elaborated/synthesized to JSON",
                        "output_format": "SVG schematic, one module level; child instances shown as blocks",
                        "module_selection": "top parameter, unique top attribute, or sole module; ambiguity is an error",
                        "render_only": True,
                        "requirements": [
                            "Node.js on MCP host PATH",
                            "netlistsvg 1.0.2",
                            "browser for launch",
                        ],
                        "limits": {"input_bytes": 33554432, "cells_per_module": 5000, "timeout_seconds": 60},
                    }
                    if name == "netlistsvg"
                    else {}
                ),
            }
            for name, formats in FORMATS.items()
        },
    }


def tcl(value):
    value = str(value)
    for before, after in (
        ("\\", "\\\\"),
        ('"', '\\"'),
        ("$", "\\$"),
        ("[", "\\["),
        ("\n", "\\n"),
        ("\r", "\\r"),
    ):
        value = value.replace(before, after)
    return '"' + value + '"'


def safe_session(content):
    """Accept passive GTKWave save records only, never process/transaction filters."""
    lines = []
    for line in content.decode("utf-8").splitlines():
        if line.startswith(("[dumpfile]", "[savefile]", "[dumpfile_mtime]", "[dumpfile_size]")):
            continue
        if not line or line.startswith(("#", "[*]")):
            continue
        if (
            re.fullmatch(
                r"\[(?:timestart|size|pos|treeopen|sst_width|signals_width|sst_expanded|sst_vpaned_height|pattern_trace)\].*",
                line,
            )
            or re.fullmatch(r"@[0-9a-fA-F]+", line)
            or re.fullmatch(r"\*[0-9.eE+\- ]+", line)
            or re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_.$:\[\]\\/ -]*", line)
        ):
            lines.append(line)
        else:
            raise ValueError("Session contains unsupported records; use explicit signals instead")
    return "\n".join(lines) + "\n"


def open_viewer(
    harness,
    artifact_id,
    viewer=None,
    *,
    companion_ids=None,
    state_id=None,
    signals=None,
    time_range=None,
    technology=None,
    top=None,
    launch=True,
    render_only=False,
):
    """Prepare and optionally launch a native viewer; launch is not a load/verification result.

    Companions: GTKWave session; KLayout layout for reports; Magic child MAG cells;
    OpenROAD LEF(s) for DEF. Technology is an installed Magic technology name or .tech path.
    """
    primary = harness.store.get("artifact", artifact_id)
    suffix = Path(primary["path"]).suffix.lower()
    if suffix in {".v", ".sv"} and viewer in {None, *NETLIST_VIEWERS}:
        raise ValueError(
            "Verilog must first use Yosys elaborate/synthesize; select its netlist.json artifact"
        )
    viewer = viewer or next((v for v, formats in FORMATS.items() if suffix in formats), None)
    if viewer not in FORMATS or suffix not in FORMATS[viewer]:
        raise ValueError("No compatible viewer for artifact; inspect viewer_capabilities")
    if render_only and viewer not in NETLIST_VIEWERS:
        raise ValueError("render_only requires a netlist viewer")
    if viewer in NETLIST_VIEWERS and primary["type"] != "netlist.json":
        raise ValueError("Netlist viewers require a netlist.json artifact, not an arbitrary JSON report")
    if signals is not None or time_range is not None:
        if viewer != "gtkwave":
            raise ValueError("signals/time_range require GTKWave")
    if time_range is not None and (
        len(time_range) != 2
        or any(not math.isfinite(t) for t in time_range)
        or time_range[0] < 0
        or time_range[0] >= time_range[1]
    ):
        raise ValueError("time_range must contain finite increasing nonnegative start/end")
    if technology is not None and viewer != "magic":
        raise ValueError("technology requires Magic")
    if top is not None and viewer not in {"magic", *NETLIST_VIEWERS}:
        raise ValueError("top requires Magic or a netlist viewer")
    if viewer == "magic" and not technology:
        raise ValueError("Magic requires an explicit installed technology name or .tech path")
    if viewer == "magic" and suffix != ".mag" and not top:
        raise ValueError("Magic GDS viewing requires top cell name")
    if technology and (technology.startswith("-") or "\x00" in technology):
        raise ValueError("Invalid technology")
    if technology and ("/" in technology or "\\" in technology):
        technology = str(Path(technology).expanduser().resolve(strict=True))
    records = [primary] + [
        harness.store.get("artifact", a) for a in dict.fromkeys(companion_ids or []) if a != artifact_id
    ]
    if state_id:
        available = {a["id"] for a in harness.get_artifacts(state_id)}
        if any(a["id"] not in available for a in records):
            raise ValueError("All viewer artifacts must belong to the selected state")
    allowed = {
        "netlistsvg": set(),
        "yosys_show": set(),
        "yosys_viz": set(),
        "gtkwave": {".gtkw"},
        "klayout": {".gds", ".gds2", ".oas", ".oasis"},
        "magic": {".mag"},
        "openroad": {".lef"},
    }[viewer]
    if any(Path(a["path"]).suffix.lower() not in allowed for a in records[1:]):
        raise ValueError("Unsupported companion format for " + viewer)
    if viewer in {"gtkwave", "klayout"} and len(records) > 2:
        raise ValueError("This viewer accepts at most one companion")
    if viewer == "klayout" and suffix in {".lyrdb", ".lvsdb", ".l2n"} and len(records) != 2:
        raise ValueError("KLayout report viewing requires a companion GDS/OAS layout artifact")
    if viewer == "openroad" and suffix == ".def" and len(records) < 2:
        raise ValueError("DEF viewing requires companion technology/cell LEF artifacts in read order")
    # Validate every CAS object before creating an export. Flattened names must not collide.
    names = [Path(a["path"]).name for a in records]
    if len(set(names)) != len(names) or any(n in {"", ".", ".."} for n in names):
        raise ValueError("Viewer artifact basenames must be distinct")
    contents = [harness.store.read_blob(a["content_hash"]) for a in records]
    if viewer in NETLIST_VIEWERS:
        from eda_harness.netlist_viewer import select_module

        selected, selected_top, modules = select_module(contents[0], top, native=viewer != "netlistsvg")
    parent = harness.store.root / "viewers"
    parent.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="view-", dir=parent)).resolve()
    data_dir = directory / "artifacts"
    data_dir.mkdir()
    paths = [data_dir / n for n in names]
    for path, content in zip(paths, contents, strict=True):
        path.write_bytes(content)
    binary = executable(viewer)
    argv = [binary or viewer]
    if viewer in NETLIST_VIEWERS:
        source = directory / "selected.json"
        source.write_text(json.dumps(selected))
        argv += [str(source), "-o", str(directory / "schematic.svg")]
    elif viewer == "gtkwave":
        argv += ["--dump", str(paths[0])]
        if len(paths) > 1:
            session = directory / "session.gtkw"
            session.write_text(safe_session(contents[1]))
            argv += ["--save", str(session)]
        body = ""
        if signals is not None:
            body += "gtkwave::addSignalsFromList [list " + " ".join(tcl(s) for s in signals) + "]\n"
        if time_range is not None:
            body += "gtkwave::setZoomRangeTimes " + " ".join(tcl(t) for t in time_range) + "\n"
        if body:
            script = directory / "viewer.tcl"
            script.write_text(body)
            argv += ["--script", str(script)]
    elif viewer == "klayout":
        config = directory / "klayout.xml"
        config.write_text(
            '<?xml version="1.0"?><config>'
            "<tip-window-hidden>editor-mode,only-top-level-shown-by-default</tip-window-hidden>"
            "</config>"
        )
        argv += ["-c", str(config), "-t", "-rx", "-ne"]
        if suffix in {".lyrdb", ".lvsdb", ".l2n"}:
            argv += [str(paths[1]), "-m" if suffix == ".lyrdb" else "-mn", str(paths[0])]
        else:
            argv += [str(p) for p in paths]
    else:
        script = directory / "viewer.tcl"
        if viewer == "magic":
            body = (
                "load " + tcl(paths[0])
                if suffix == ".mag"
                else "gds read " + tcl(paths[0]) + "\nload " + tcl(top)
            )
            body += "\nselect top cell\nexpand\nview\n"
            argv += ["-norc", "-T", technology, str(script)]
        else:
            body = "".join("read_lef " + tcl(p) + "\n" for p in paths[1:])
            body += ("read_db " if suffix == ".odb" else "read_def ") + tcl(paths[0]) + "\n"
            argv += ["-no_init", "-gui", str(script)]
        script.write_text(body)
    result = {
        "viewer": viewer,
        "status": "PREPARED",
        "directory": str(directory),
        "execution_host": "local",
        "argv": argv,
        "manual_command": shlex.join(argv),
        "state_id": state_id,
        "artifacts": [
            {
                "id": a["id"],
                "content_hash": a["content_hash"],
                "producer_run_id": a.get("producer_run_id"),
                "path": str(p),
            }
            for a, p in zip(records, paths, strict=True)
        ],
        "log": str(directory / "viewer.log"),
        "note": "Process launch does not confirm GUI loading or design verification.",
    }
    if viewer in NETLIST_VIEWERS:
        from eda_harness.netlist_viewer import render

        result.update(output=str(directory / "schematic.svg"), top=selected_top, available_modules=modules)
        if viewer in {"yosys_show", "yosys_viz"}:
            from eda_harness.yosys_viewer import prepare, run

            config = prepare(result, harness)
            if launch or render_only:
                run(result, config, launch and not render_only)
        elif launch or render_only:
            render(result, binary, launch and not render_only)
    elif launch:
        if not binary:
            result.update(
                status="VIEWER_UNAVAILABLE",
                install_hint=(
                    "Install "
                    + viewer
                    + " on this computer; put it on PATH or set EDA_VIEWER_"
                    + viewer.upper()
                    + " to its executable path, then restart the MCP server."
                ),
            )
        else:
            try:
                with open(result["log"], "wb") as log:
                    process = subprocess.Popen(
                        argv,
                        cwd=data_dir,
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=log,
                        start_new_session=True,
                    )
                result.update(status="LAUNCHED", pid=process.pid)
                threading.Thread(target=process.wait, daemon=True).start()
            except OSError as error:
                result.update(status="LAUNCH_FAILED", error=str(error))
    (directory / "manifest.json").write_text(json.dumps(result, indent=2))
    return result
