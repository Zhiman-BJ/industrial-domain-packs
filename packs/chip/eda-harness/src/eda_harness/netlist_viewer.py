"""Render a captured Yosys JSON module with the optional local netlistsvg tool."""

import hashlib
import json
import shutil
import subprocess
import webbrowser
import xml.etree.ElementTree as ET

MAX_BYTES = 32 * 1024 * 1024
MAX_CELLS = 5000
TIMEOUT = 60


def select_module(content, top=None, *, native=False):
    if len(content) > MAX_BYTES:
        raise ValueError("Netlist exceeds 32 MiB; export a smaller module with Yosys")
    try:
        data = json.loads(content)
    except (ValueError, UnicodeError) as error:
        raise ValueError("Expected a Yosys write_json netlist") from error
    modules = data.get("modules") if isinstance(data, dict) else None
    if not isinstance(modules, dict) or not modules or not all(isinstance(m, dict) for m in modules.values()):
        raise ValueError("Expected a Yosys write_json netlist with nonempty modules")
    available = sorted(modules)
    if top is None:
        marked = [
            name
            for name, mod in modules.items()
            if str(mod.get("attributes", {}).get("top", "0")).lstrip("0") == "1"
        ]
        top = marked[0] if len(marked) == 1 else available[0] if len(available) == 1 else None
    if top not in modules:
        raise ValueError(f"Select top from available netlist modules: {available}")
    module = modules[top]
    if module.get("processes") or module.get("memories"):
        raise ValueError("Lower processes/memories with Yosys before rendering this module")
    if not isinstance(module.get("ports"), dict) or not isinstance(module.get("cells"), dict):
        raise ValueError("Selected module must contain ports and cells dictionaries")
    if len(module["cells"]) > MAX_CELLS:
        raise ValueError(f"Selected module exceeds {MAX_CELLS} cells; select/export a smaller module")
    for port in [] if native else module["ports"].values():
        if not isinstance(port, dict) or port.get("direction") not in {"input", "output"}:
            raise ValueError(
                "netlistsvg supports input/output ports only; bidirectional ports are unsupported"
            )
    for cell in [] if native else module["cells"].values():
        if not isinstance(cell, dict) or not isinstance(cell.get("connections"), dict):
            raise ValueError("Invalid netlist cell connections")
        directions = cell.get("port_directions", {})
        if not isinstance(directions, dict) or any(
            directions.get(port) not in {"input", "output"} for port in cell["connections"]
        ):
            raise ValueError(
                "Cell port directions missing or bidirectional; require input/output directions from Yosys cell models"
            )
    # Render one hierarchy level; submodule instances retain their original port connections.
    return {"modules": {top: module}}, top, available


def render(result, binary, launch, executor=None):
    from pathlib import Path

    if executor is None and (not binary or not shutil.which("node")):
        result.update(
            status="VIEWER_UNAVAILABLE",
            install_hint=(
                "Install the locked optional renderer with npm ci --prefix tools/netlist-viewer from the "
                "Harness source checkout; set EDA_VIEWER_NETLISTSVG to its node_modules/.bin/netlistsvg "
                "absolute path and ensure node is on the MCP process PATH, then restart MCP."
            ),
        )
        return
    svg = Path(result["output"])
    try:
        if executor is not None:
            process = executor()
        else:
            with open(result["log"], "wb") as log:
                process = subprocess.run(
                    result["argv"],
                    cwd=result["directory"],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    timeout=TIMEOUT,
                )
        result["render_exit_code"] = process.returncode
        if process.returncode:
            raise ValueError("Netlist renderer failed; inspect viewer.log")
        if not svg.is_file() or svg.stat().st_size > MAX_BYTES:
            raise ValueError("Renderer did not produce a bounded SVG file")
        content = svg.read_bytes()
        root = ET.fromstring(content)
        if root.tag != "{http://www.w3.org/2000/svg}svg":
            raise ValueError("Renderer output is not SVG")
        for element in root.iter():
            if element.tag.split("}")[-1] in {"script", "foreignObject"} or any(
                key.lower().startswith("on") or (key.split("}")[-1] == "href" and not value.startswith("#"))
                for key, value in element.attrib.items()
            ):
                raise ValueError("Renderer output contains active or external content")
        # netlistsvg emits a transparent background; make black wires readable in dark viewers.
        ET.register_namespace("", "http://www.w3.org/2000/svg")
        root.insert(
            0,
            ET.Element(
                "{http://www.w3.org/2000/svg}rect",
                {
                    "width": "100%",
                    "height": "100%",
                    "style": "fill:white;stroke:none",
                },
            ),
        )
        content = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        svg.write_bytes(content)
        result.update(status="RENDERED", output_sha256=hashlib.sha256(content).hexdigest())
    except (OSError, ValueError, ET.ParseError, subprocess.TimeoutExpired) as error:
        result.update(status="RENDER_FAILED", error=str(error))
        return
    if launch:
        try:
            opened = webbrowser.open(svg.as_uri(), new=2)
            result["status"] = "LAUNCHED" if opened else "LAUNCH_FAILED"
            if not opened:
                result["error"] = "SVG rendered; no browser accepted the open request. Open output manually."
        except (OSError, webbrowser.Error) as error:
            result.update(status="LAUNCH_FAILED", error=str(error))
