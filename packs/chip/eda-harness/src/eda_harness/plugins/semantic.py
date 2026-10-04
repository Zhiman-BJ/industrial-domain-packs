"""Tool-independent inputs and native adapters for structured actions.

Technology rule bodies are user inputs, not bundled design projects.
"""

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from pydantic import BaseModel, TypeAdapter

from eda_harness.core.models import Project
from eda_harness.core.parameters import InputRef, Parameters
from eda_harness.core.workflow import action_input_categories, project_input_categories
from eda_harness.core.workspace import safe_path
from eda_harness.plugins import advanced, equivalence

SUPPORT = {
    "floorplan": ["openroad"],
    "place": ["openroad"],
    "cts": ["openroad"],
    "route": ["openroad"],
    "streamout": ["klayout"],
    "lint": ["verilator"],
    "simulate": ["verilator"],
    "synthesize": ["yosys"],
    "equivalence": ["yosys"],
    "extract": ["magic"],
    "lvs": ["netgen", "klayout"],
    "drc": ["klayout"],
    "waveform": ["gtkwave"],
    "sta": ["openroad"],
    "power": ["openroad"],
}

SUPPORT.update({op: ["yosys"] for op in sorted(advanced.YOSYS)})
SUPPORT.update({op: ["openroad"] for op in sorted(advanced.OPENROAD)})


def capabilities():
    return {
        "schema_version": 1,
        "operations": SUPPORT,
        "parameters_schema": TypeAdapter(Parameters).json_schema(),
        "project_schema": Project.model_json_schema(),
        "input_contract": {
            "default_allowed_categories": project_input_categories(),
            "category_rule": "Union of resolved workflow action input_types plus config and script. Custom actions may add categories.",
            "action_rule": "list_actions.allowed_input_categories gives categories accepted by each action, including shared config and script.",
            "path_rule": "Declare project-relative paths/globs in inputs.<category>, then reference captured files with parameters {path: ...}. Parameters alone do not capture files or bypass validation.",
            "artifact_rule": "Use {action: ..., artifact: ...} for declared upstream dependencies; do not add generated artifact paths to inputs.",
            "validation": "Project JSON Schema describes shape; workflow-dependent categories are checked on project load, and captured references on preflight.",
        },
        "input_reference": "path in captured project OR action + artifact from a declared dependency",
        "rule_contracts": {
            "magic": "Matching Magic technology file, or explicitly selected bundled scmos",
            "netgen": "Optional native Netgen setup Tcl; absent means no device mapping",
            "klayout.drc": "Ruby rule body or native XML macro; explicit input/report variables",
            "klayout.lvs": "Ruby extraction body or native XML macro; explicit bindings and native final comparison",
        },
        "qualification": "Adapters are tested with pinned tools; no claim of arbitrary version or PDK compatibility",
    }


def output_types(parameters):
    if parameters.operation == "equivalence":
        return {"report.equivalence"}
    if parameters.operation in advanced.YOSYS | advanced.OPENROAD:
        return advanced.output_types(parameters)
    outputs = {
        "streamout": {"layout.gds", "report.streamout"},
        "lint": set(),
        "synthesize": {"netlist.gate", "netlist.json", "report.netlist"},
        "simulate": {"report.simulation"}
        | ({"waveform.vcd"} if getattr(parameters, "trace", False) else set()),
        "extract": {"netlist.extracted"},
        "lvs": {"report.lvs", "report.native_match"},
        "drc": {"report.drc"},
        "waveform": {"waveform.fst", "waveform.session", "report.signals"},
        "sta": {"report.timing", "report.setup", "report.hold", "report.tns", "report.endpoints"},
        "power": {"report.power"},
    }
    outputs.update(
        {
            op: {"layout." + name + "_odb", "layout.def", "netlist.physical", "constraints.sdc"}
            for op, name in (
                ("floorplan", "floorplan"),
                ("place", "placed"),
                ("cts", "cts"),
                ("route", "routed"),
            )
        }
    )
    outputs["route"] |= {"parasitic.spef", "report.route_drc"}
    return outputs[parameters.operation]


def references(value):
    if isinstance(value, InputRef):
        yield value
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            yield from references(getattr(value, name))
    elif isinstance(value, dict):
        for item in value.values():
            yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def preflight(action, project, snapshot):
    from eda_harness.core.workflow import catalog

    cfg = project.actions[action]
    p = cfg.parameters
    spec = catalog(project.workflow)[action]
    if spec.tool not in SUPPORT[p.operation]:
        raise ValueError(f"{p.operation} does not support backend {spec.tool}")
    for name in ("top", "reference_top"):
        value = getattr(p, name, None)
        if value is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError(f"{name} must be a simple identifier")
    for ref in references(p):
        if ref.path is not None:
            info = snapshot["files"].get(ref.path)
            if info is None:
                raise ValueError(f"Input is not captured by project inputs: {ref.path}")
            if info["type"] not in action_input_categories(spec):
                raise ValueError(f"Input is not tracked by action {action}: {ref.path}")
        else:
            if ref.action not in spec.dependencies:
                raise ValueError(f"Undeclared dependency: {ref.action}")
            producer = catalog(project.workflow)[ref.action]
            pcfg = project.actions.get(ref.action)
            offered = set(producer.artifact_types) | (set(pcfg.outputs) if pcfg else set())
            if pcfg and pcfg.parameters:
                offered |= output_types(pcfg.parameters)
            if ref.artifact not in offered:
                raise ValueError(f"Undeclared producer artifact: {ref.action}/{ref.artifact}")
            safe_path(Path("/deps"), f"{ref.action}/{ref.artifact}")
    if p.operation == "lvs" and spec.tool != "klayout" and (p.rule_format != "ruby" or p.variables):
        raise ValueError("Native macro options require KLayout")
    if spec.tool == "klayout" and p.operation == "lvs" and p.rules is None:
        raise ValueError("KLayout LVS requires extraction/connectivity rules")
    return {"action": action, "backend": spec.tool, "operation": p.operation, "ready": True}


def ruby(value):
    # Ruby double-quoted JSON strings permit #{...} interpolation; use a literal word.
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def tcl(value):
    # Quoted Tcl word: block command and variable substitution, including literal backslashes.
    return (
        '"'
        + str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("[", "\\[")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        + '"'
    )


def prepare(action, project, snapshot, paths, work):
    from eda_harness.core.workflow import catalog
    from eda_harness.plugins.tools import Prepared

    preflight(action, project, snapshot)
    p = project.actions[action].parameters
    tool = catalog(project.workflow)[action].tool

    def ref(r):
        return str(
            safe_path(Path(paths["input"]), r.path)
            if r.path is not None
            else safe_path(Path(paths["deps"]), f"{r.action}/{r.artifact}")
        )

    def script(name, text):
        (work / name).write_text(text + "\n")
        return name

    op = p.operation
    if op == "equivalence":
        return equivalence.prepare(p, ref, script)
    if op == "lint":
        return Prepared(
            [
                [
                    "verilator",
                    "--lint-only",
                    "--sv",
                    "-Wall",
                    "--top-module",
                    p.top,
                    *[ref(r) for r in p.sources],
                ]
            ]
        )
    if op == "synthesize" or op in advanced.YOSYS | advanced.OPENROAD:
        return advanced.prepare(p, ref, script)
    if op == "simulate":
        trace_header = '#include "verilated_vcd_c.h"' if p.trace else ""
        trace_init = (
            'VerilatedVcdC trace; context.traceEverOn(true); model.trace(&trace, 99); trace.open("wave.vcd");'
            if p.trace
            else ""
        )
        trace_dump = "trace.dump(context.time());" if p.trace else ""
        trace_close = "trace.close();" if p.trace else ""
        driver = f"""#include "V{p.top}.h"
#include "verilated.h"
#include <iostream>\n#include <fstream>\n#include <sys/resource.h>
{trace_header}
int main(int argc, char** argv) {{
 rlimit core_limit{{0, 0}}; setrlimit(RLIMIT_CORE, &core_limit);
 VerilatedContext context; context.commandArgs(argc, argv);
 V{p.top} model(&context);
 {trace_init}
 while (!context.gotFinish()) {{
  model.eval(); {trace_dump}
  if (!model.eventsPending()) break;
  context.time(model.nextTimeSlot());
 }}
 model.final(); {trace_close}
 if (!context.gotFinish()) return 2;
 std::ofstream("simulation.txt") << "EDA_SIMULATION_FINISHED\\n";\n std::cout << "EDA_SIMULATION_FINISHED" << std::endl;
 return 0;
}}"""
        script("driver.cpp", driver)
        args = [
            "verilator",
            "--cc",
            "--exe",
            "--build",
            "--timing",
            "--assert",
            "-j",
            str((project.actions[action].runtime or project.runtime).resources.build_jobs),
            "--output-split",
            str(p.output_split),
            "--output-split-cfuncs",
            str(p.output_split_cfuncs),
            "--top-module",
            p.top,
            "-CFLAGS",
            "-std=c++20",
            *(["--trace"] if p.trace else []),
            *[ref(r) for r in p.sources],
            str(Path(paths["work"]) / "driver.cpp"),
        ]
        outputs = {"report.simulation": "simulation.txt"}
        if p.trace:
            outputs["waveform.vcd"] = "wave.vcd"
        return Prepared([args, [f"./obj_dir/V{p.top}"]], outputs)
    if op == "extract":
        tech = ref(p.technology) if p.technology else p.builtin_technology
        if ref(p.layout).lower().endswith((".gds", ".gdsii")):
            load = f"gds read {tcl(ref(p.layout))}\nload {tcl(p.top)}"
        else:
            load = f"load {tcl(ref(p.layout))}"
        body = (
            load
            + "\nselect top cell\nextract all\next2spice lvs\next2spice -o extracted.spice\nquit -noprompt"
        )
        return Prepared(
            [["magic", "-dnull", "-noconsole", "-T", tech, script("extract.tcl", body)]],
            {"netlist.extracted": "extracted.spice"},
        )
    if op == "lvs" and tool == "netgen":
        setup = tcl(ref(p.rules)) if p.rules else "nosetup"
        body = f"""set left [netgen::readnet spice {tcl(ref(p.layout))}]
set right [netgen::readnet spice {tcl(ref(p.reference))}]
lvs [list {tcl(p.top)} $left] [list {tcl(p.reference_top or p.top)} $right] {setup} lvs.log -json
set f [open native-match.txt w]
puts $f [netgen::verify unique]
close $f
quit"""
        return Prepared(
            [["netgen", "-batch", "source", script("lvs.tcl", body)]],
            {
                "report.lvs": "lvs.log",
                "report.lvs_json": "lvs.json",
                "report.native_match": "native-match.txt",
            },
        )
    if op in ("drc", "lvs") and p.rule_format == "macro":
        report = "drc.lyrdb" if op == "drc" else "lvs.lvsdb"
        variables = {k: ref(v) if isinstance(v, InputRef) else v for k, v in p.variables.items()}
        variables[p.layout_variable] = ref(p.layout)
        variables[p.report_variable] = str(Path(paths["work"]) / report)
        suffix = ""
        outputs = {"report." + op: report}
        if op == "lvs":
            variables[p.reference_variable] = ref(p.reference)
            suffix = '\nFile.write("native-devices.txt", netlist.each_circuit.to_a.sum { |c| c.each_device.to_a.size }.to_s)\n'
            suffix += f"same_circuits({ruby(p.top)}, {ruby(p.reference_top or p.top)})\n"
            suffix += 'File.write("native-match.txt", compare ? "1\n" : "0\n")\n'
            outputs.update(
                {"report.native_match": "native-match.txt", "report.native_devices": "native-devices.txt"}
            )
        helper = (
            "import xml.etree.ElementTree as ET\n"
            + f"root = ET.parse({ref(p.rules)!r}).getroot()\n"
            + f"assert root.tag == 'klayout-macro' and root.findtext('dsl-interpreter-name') == {op + '-dsl-xml'!r}, 'Invalid native macro interpreter'\n"
            + "body = root.find('text')\nassert body is not None and body.text, 'Missing macro body'\n"
            + f"body.text += {suffix!r}\n"
            + f"ET.ElementTree(root).write('native-rules.ly{op}', encoding='unicode')\n"
        )
        args = ["klayout", "-b"]
        for name, value in variables.items():
            args.extend(["-rd", f"{name}={value}"])
        return Prepared(
            [["python3", script("prepare-macro.py", helper)], [*args, "-r", "native-rules.ly" + op]], outputs
        )
    if op in ("drc", "lvs"):
        body = f"source({ruby(ref(p.layout))}" + (f", {ruby(p.top)}" if op == "lvs" else "") + ")\n"
        if op == "drc":
            body += f'report("Harness DRC", {ruby(str(Path(paths["work"]) / "drc.lyrdb"))})\n'
            outputs = {"report.drc": "drc.lyrdb"}
        else:
            body += f"schematic({ruby(ref(p.reference))})\nreport_lvs({json.dumps(str(Path(paths['work']) / 'lvs.lvsdb'))})\n"
            outputs = {
                "report.lvs": "lvs.lvsdb",
                "report.native_match": "native-match.txt",
                "report.native_devices": "native-devices.txt",
            }
        body += f"instance_eval(File.read({ruby(ref(p.rules))}), {ruby(ref(p.rules))})\n"
        if op == "lvs":
            body += 'File.write("native-devices.txt", netlist.each_circuit.to_a.sum { |c| c.each_device.to_a.size }.to_s)\n'
            body += f"same_circuits({ruby(p.top)}, {ruby(p.reference_top or p.top)})\n"
            body += 'File.write("native-match.txt", compare ? "1\\n" : "0\\n")\n'
        macro = ET.Element("klayout-macro")
        ET.SubElement(macro, "interpreter").text = "dsl"
        ET.SubElement(macro, "dsl-interpreter-name").text = op + "-dsl-xml"
        ET.SubElement(macro, "text").text = body
        return Prepared(
            [["klayout", "-b", "-r", script("rules.ly" + op, ET.tostring(macro, encoding="unicode"))]],
            outputs,
        )
    if op == "waveform":
        body = """set n [gtkwave::getNumFacs]
set signals {}
for {set i 0} {$i < $n} {incr i} {lappend signals [gtkwave::getFacName $i]}
gtkwave::addSignalsFromList $signals
gtkwave::/File/Write_Save_File wave.gtkw
set f [open signals.txt w]
puts $f $n
close $f
gtkwave::/File/Quit"""
        script("wave.tcl", body)
        commands = (
            [["vcd2fst", ref(p.waveform), "wave.fst"]]
            if p.format == "vcd"
            else [["cp", ref(p.waveform), "wave.fst"]]
        )
        commands += [
            ["xvfb-run", "-a", "gtkwave", "--script", "wave.tcl", "wave.fst"],
            ["xvfb-run", "-a", "gtkwave", "--exit", "wave.fst", "wave.gtkw"],
        ]
        script(
            "wave.py",
            "import subprocess\nfor command in "
            + repr(commands)
            + ":\n    subprocess.run(command, check=True)\n",
        )
        return Prepared(
            [["python3", "wave.py"]],
            {"waveform.fst": "wave.fst", "waveform.session": "wave.gtkw", "report.signals": "signals.txt"},
        )
    if op == "streamout":
        job = {
            "top": p.top,
            "layout": ref(p.layout),
            "technology": ref(p.technology),
            "lefs": [ref(r) for r in p.lefs],
            "libraries": [ref(r) for r in p.libraries],
            "dbu": p.dbu,
        }
        script("job.json", json.dumps(job))
        script("streamout.py", Path(__file__).with_name("klayout_streamout.py").read_text())
        return Prepared(
            [["klayout", "-b", "-r", "streamout.py"]],
            {"layout.gds": "layout.gds", "report.streamout": "streamout.json"},
        )
    if op in ("floorplan", "place", "cts", "route"):
        body = "\n".join(f"read_liberty {tcl(ref(r))}" for r in p.libraries) + "\n"
        if op == "floorplan":
            body += "\n".join(f"read_lef {tcl(ref(r))}" for r in p.lefs) + "\n"
            body += f"read_verilog {tcl(ref(p.netlist))}\nlink_design {tcl(p.top)}\n"
            die = " ".join(map(str, p.die_area))
            core = " ".join(map(str, p.core_area))
            body += f"initialize_floorplan -die_area {{{die}}} -core_area {{{core}}} -site {p.site}\n"
        else:
            body += f"read_db {tcl(ref(p.database))}\n"
        body += f"read_sdc {tcl(ref(p.constraints))}\n"
        if p.technology_setup:
            body += f"source {tcl(ref(p.technology_setup))}\n"
        if op == "floorplan":
            body += f"make_tracks\nplace_pins -hor_layers {p.pin_layers[0]} -ver_layers {p.pin_layers[1]}\n"
        elif op == "place":
            body += f"global_placement -density {p.density}\ndetailed_placement\ncheck_placement -verbose\n"
        elif op == "cts":
            buffers = " ".join(p.clock_buffers)
            body += f"clock_tree_synthesis -buf_list {{{buffers}}} -root_buf {p.clock_buffers[0]}\nrepair_clock_nets\ndetailed_placement\ncheck_placement -verbose\n"
        else:
            body += f"set_routing_layers -signal {p.routing_layers[0]}-{p.routing_layers[1]}\nglobal_route\ndetailed_route -output_drc route_drc.rpt\n"
            body += f"define_process_corner -ext_model_index 0 X\nextract_parasitics -ext_model_file {tcl(ref(p.extraction_rules))}\nwrite_spef routed.spef\n"
        body += "write_db result.odb\nwrite_def result.def\nwrite_verilog -include_pwr_gnd physical.v\nwrite_sdc physical.sdc\n"
        # Re-open the native output and report its actual design name/instance count.
        readback = "read_db result.odb\nset block [ord::get_db_block]\nset f [open physical.txt w]\nputs $f [$block getName]\nputs $f [llength [$block getInsts]]\nclose $f\n"
        label = {"floorplan": "floorplan", "place": "placed", "cts": "cts", "route": "routed"}[op]
        outputs = {
            f"layout.{label}_odb": "result.odb",
            "layout.def": "result.def",
            "netlist.physical": "physical.v",
            "constraints.sdc": "physical.sdc",
            "report.physical": "physical.txt",
        }
        if op == "route":
            # An empty native DRC text file is normal for zero violations; retain it in a nonempty envelope.
            body += 'set f [open route_drc.rpt r]\nset drc [read $f]\nclose $f\nset f [open route_drc.txt w]\nputs $f "OpenROAD detailed-route DRC"\nputs $f $drc\nclose $f\n'
            outputs.update({"parasitic.spef": "routed.spef", "report.route_drc": "route_drc.txt"})
        return Prepared(
            [
                ["openroad", "-exit", script("physical.tcl", body)],
                ["openroad", "-exit", script("readback.tcl", readback)],
            ],
            outputs,
        )
    if op in ("sta", "power"):
        corner = f" -corner {p.corner}" if p.corner else ""
        body = f"define_corners {p.corner}\n" if p.corner else ""
        body += "\n".join(
            f"read_liberty{corner}" + (" -max" if p.min_libraries else "") + f" {tcl(ref(r))}"
            for r in p.libraries
        )
        body += "\n" + "\n".join(f"read_liberty{corner} -min {tcl(ref(r))}" for r in p.min_libraries)
        body += f"\nread_db {tcl(ref(p.database))}\nread_sdc {tcl(ref(p.constraints))}\nread_spef{corner} {tcl(ref(p.parasitics))}\n"
        body += 'if {[llength [all_clocks]] == 0} {error "No clocks were constrained"}\nset_propagated_clock [all_clocks]\nreport_units\n'
        if op == "sta":
            body += "report_checks -path_delay min_max > timing.txt\n"
            for mode, flag in (("setup", "max"), ("hold", "min")):
                body += f"set f [open {mode}.txt w]\nputs $f [sta::worst_slack -{flag}]\nclose $f\n"
            body += "set f [open tns.txt w]\nputs $f [sta::total_negative_slack -max]\nputs $f [sta::total_negative_slack -min]\nclose $f\n"
            body += "set f [open endpoints.txt w]\nputs $f [sta::endpoint_violation_count max]\nputs $f [sta::endpoint_violation_count min]\nclose $f\n"
            outputs = {
                "report.endpoints": "endpoints.txt",
                "report.timing": "timing.txt",
                "report.setup": "setup.txt",
                "report.hold": "hold.txt",
                "report.tns": "tns.txt",
            }
        else:
            body += "report_power > power.txt\n"
            outputs = {"report.power": "power.txt"}
        return Prepared([["openroad", "-exit", script("analysis.tcl", body)]], outputs)
    raise ValueError(f"Unsupported operation {op}")


def observe(action, project, work, log, artifacts):
    from eda_harness.core.models import Diagnostic, Metric, VerificationResult
    from eda_harness.plugins.tools import Observation

    p = project.actions[action].parameters
    by_type = {a["type"]: a for a in artifacts}
    metrics = []

    def read(type_):
        if type_ not in by_type:
            raise ValueError(f"Missing required evidence: {type_}")
        return safe_path(work, by_type[type_]["path"]).read_text(errors="replace")

    def metric(name, value, unit, type_):
        if getattr(p, "corner", None):
            name = f"corner.{p.corner}.{name}"
        metrics.append(
            Metric(name=name, value=value, unit=unit, action=action, evidence=by_type[type_]["id"])
        )

    passed, summary, details = False, "", {}
    op = p.operation
    text = log.read_text(errors="replace")
    if op == "lint":
        errors = len(re.findall(r"^%Error", text, re.M))
        warnings = len(re.findall(r"^%Warning", text, re.M))
        metric("lint.errors", errors, "count", "log.tool")
        metric("lint.warnings", warnings, "count", "log.tool")
        passed, summary = not (errors or warnings), f"Verilator: {errors} errors, {warnings} warnings"
    elif op == "synthesize" or op in advanced.YOSYS | advanced.OPENROAD:
        passed, summary, details = advanced.observe(p, read, metric)
    elif op == "equivalence":
        passed, summary, details = equivalence.observe(p, read("report.equivalence"))
    elif op == "simulate":
        passed = "EDA_SIMULATION_FINISHED" in text and "%Error" not in text
        summary = (
            "Simulation reached $finish with assertions enabled; functional coverage is testbench-defined"
        )
    elif op == "extract":
        spice = read("netlist.extracted")
        count = len(re.findall(r"^M\S+\s", spice, re.M | re.I))
        passed = count > 0 and bool(
            re.search(r"^\.subckt\s+" + re.escape(p.top) + r"(?:\s|$)", spice, re.M | re.I)
        )
        metric("extract.mos_devices", count, "count", "netlist.extracted")
        summary = f"Magic extracted {count} MOS devices; extraction is not LVS"
    elif op == "lvs":
        result = read("report.native_match").strip()
        if not re.fullmatch(r"-?\d+", result):
            raise ValueError("Invalid native LVS comparison result")
        passed = result == "1"
        if "report.native_devices" in by_type:
            passed = passed and int(read("report.native_devices")) > 0
        if "report.lvs_json" in by_type:
            cells = json.loads(read("report.lvs_json"))
            if not isinstance(cells, list) or not cells:
                raise ValueError("Netgen comparison has no circuit evidence")
            failures = [c for c in cells if any(c.get(k) for k in ("badnets", "badelements", "properties"))]
            passed = passed and not failures
            details = {"native_result": int(result), "mismatched_cells": failures[:10]}
        metric("lvs.match", int(passed), "bool", "report.native_match")
        summary = (
            "Native LVS match"
            if passed
            else "Native LVS mismatch, ambiguity, empty circuit or property/port error"
        )
    elif op == "drc":
        root = ET.fromstring(read("report.drc"))
        if root.tag != "report-database" or root.find("items") is None:
            raise ValueError("Malformed KLayout DRC report")
        items = root.findall("items/item")
        counts = [int(i.findtext("multiplicity", "1")) for i in items]
        if any(c < 1 for c in counts):
            raise ValueError("Invalid DRC multiplicity")
        count = sum(counts)
        metric("drc.violations", count, "count", "report.drc")
        passed, summary = count == 0, f"KLayout DRC: {count} violations"
        details = {
            "violations": [
                {
                    "rule": i.findtext("category"),
                    "cell": i.findtext("cell"),
                    "geometry": [v.text for v in i.findall("values/value")],
                }
                for i in items[:10]
            ]
        }
    elif op == "waveform":
        count = int(read("report.signals"))
        passed = count > 0 and bool(read("waveform.session").strip())
        metric("waveform.signals", count, "count", "report.signals")
        summary = f"GTKWave loaded {count} signals and saved/reopened session; not design verification"
    elif op == "streamout":
        checks = json.loads(read("report.streamout"))
        required = {"top_present", "dbu_preserved", "bbox_preserved", "nonempty_geometry"}
        if set(checks) != required or any(type(v) is not bool for v in checks.values()):
            raise ValueError("Malformed GDS readback evidence")
        passed = all(checks.values())
        summary = "KLayout DEF/GDS library merge and physical-scale readback"
        details = checks
    elif op in ("floorplan", "place", "cts", "route"):
        native = read("report.physical").splitlines()
        if len(native) != 2:
            raise ValueError("Missing physical database readback evidence")
        count = int(native[1])
        passed = native[0] == p.top and count > 0
        metric("physical.instances", count, "count", "report.physical")
        summary = "OpenROAD database written and re-opened; not DRC/LVS signoff"
        if op == "route":
            violations = len(re.findall(r"violation type:", read("report.route_drc"), re.I))
            metric("route.drc_violations", violations, "count", "report.route_drc")
            passed = passed and violations == 0
            summary += f"; router DRC violations: {violations}"
    elif op == "sta":
        units = read("log.tool")
        # Never label arbitrary library time units as nanoseconds.
        if not re.search(r"Time\s+1(?:\.0*)?ns", units, re.I):
            raise ValueError("This STA adapter requires reported time units of 1ns")
        slacks = []
        for mode in ("setup", "hold"):
            value = float(read("report." + mode).strip())
            slacks.append(value)
            metric(f"timing.{mode}.wns", value, "ns", "report." + mode)
        tns = read("report.tns").splitlines()
        if len(tns) != 2:
            raise ValueError("Missing setup/hold TNS evidence")
        for mode, value in zip(("setup", "hold"), tns, strict=True):
            metric(f"timing.{mode}.tns", float(value), "ns", "report.tns")
        endpoints = read("report.endpoints").splitlines()
        if len(endpoints) != 2:
            raise ValueError("Missing native endpoint counts")
        for mode, value in zip(("setup", "hold"), endpoints, strict=True):
            metric(f"timing.{mode}.violating_endpoints", int(value), "count", "report.endpoints")
        if "Startpoint:" not in read("report.timing"):
            raise ValueError("No timing paths were reported")
        passed, summary = min(slacks) >= 0, "OpenSTA setup and hold timing"
    elif op == "power":
        match = re.search(r"^Total\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)", read("report.power"), re.M)
        if not match or "Watts" not in read("report.power"):
            raise ValueError("Missing native power report with units")
        value = float(match[4])
        metric("power.total", value, "W", "report.power")
        passed, summary = (
            value >= 0,
            "OpenSTA estimated power with default activity; no activity-based accuracy claim",
        )
    evidence = [a["id"] for a in artifacts]
    status = "PASS" if passed else "FAIL"
    diagnostics = (
        []
        if passed
        else [
            Diagnostic(category=action, severity="high", summary=summary, evidence=evidence, details=details)
        ]
    )
    return Observation(
        metrics,
        VerificationResult(action=action, status=status, summary=summary, evidence=evidence),
        diagnostics,
    )
