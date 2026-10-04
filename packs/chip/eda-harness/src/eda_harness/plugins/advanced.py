"""Native tool operations, independent of any design or external flow framework."""

import json
import re
from pathlib import Path

from eda_harness.plugins.tools import Prepared, quoted

YOSYS = {"elaborate", "check_netlist", "prepare_lvs_reference"}
REPAIRS = {"repair_design", "repair_timing", "repair_tie_fanout", "repair_antennas"}
UTILITIES = {"connect_power", "generate_pdn", "insert_tapcells", "insert_fillers"}
CHECKS = {"check_constraints", "check_design_rules"}
OPENROAD = REPAIRS | UTILITIES | CHECKS | {"export_design"}
EXPORTS = {
    "odb": ("layout.exported_odb", "result.odb"),
    "def": ("layout.def", "result.def"),
    "verilog": ("netlist.physical", "result.v"),
    "sdc": ("constraints.sdc", "result.sdc"),
    "spef": ("parasitic.spef", "result.spef"),
}


def output_types(p):
    if p.operation in YOSYS:
        return (
            {"netlist.reference", "report.reference"} if p.operation == "prepare_lvs_reference" else set()
        ) | {
            "netlist.json",
            "netlist.gate",
            "report.netlist",
        }
    if p.operation == "export_design":
        return {EXPORTS[f][0] for f in p.formats} | {"report.database"}
    if p.operation in CHECKS:
        return {"report.checks", "report.check_counts", "report.database"}
    return (
        {
            "layout.modified_odb",
            "layout.def",
            "netlist.physical",
            "constraints.sdc",
            "report.database",
            "report.operation",
        }
        | ({"report.connections"} if getattr(p, "connections", []) else set())
        | ({"report.repair"} if p.operation in REPAIRS else set())
    )


def yosys(p, ref):
    lines = []
    if p.liberty:
        lines.append(f"read_liberty -lib {quoted(ref(p.liberty))}")
    for model in p.cell_models:
        lines.append(f"read_verilog -lib {quoted(ref(model))}")
    quote = (lambda value: value) if p.frontend == "slang" else quoted
    options = [f"-I{quote(str(Path(ref(r)).parent))}" for r in p.includes]
    options += [f"-D{k}={v}" for k, v in p.defines.items()]
    sources = " ".join(quote(ref(r)) for r in p.sources)
    if p.frontend == "slang":
        options += [f"-G{k}={v}" for k, v in p.top_parameters.items()]
        lines.append(f"read_slang --top {p.top} {' '.join(options)} {sources}")
    else:
        lines.append(f"read_verilog -sv -defer {' '.join(options)} {sources}")
    parameters = (
        " ".join(f"-chparam {k} {v}" for k, v in p.top_parameters.items()) if p.frontend == "verilog" else ""
    )
    lines.append(f"hierarchy -check -top {p.top} {parameters}")
    lines += ["proc", "opt"]
    if p.flatten:
        lines.append("flatten")
    if p.operation == "synthesize":
        lines.append(f"synth -top {p.top}")
    for mapping in p.technology_maps:
        lines.append(f"techmap -map {quoted(ref(mapping))}")
    if p.operation == "synthesize" and p.liberty:
        lines += [f"dfflibmap -liberty {quoted(ref(p.liberty))}", f"abc -liberty {quoted(ref(p.liberty))}"]
    lines += ["clean", "tee -o netlist-check.txt check" + (" -mapped" if p.mapped else "")]
    lines += ["write_verilog -noattr netlist.v", "write_json netlist.json"]
    if p.operation == "prepare_lvs_reference":
        lines += [
            "select -assert-none t:$*",
            "write_rtlil interface.il",
            "setattr -mod -unset top",
            f"write_spice -pos {p.power_net} -neg {p.ground_net} reference-raw.spice",
        ]
    outputs = {
        "netlist.gate": "netlist.v",
        "netlist.json": "netlist.json",
        "report.netlist": "netlist-check.txt",
    }
    if p.operation == "prepare_lvs_reference":
        outputs["netlist.reference"] = "reference.spice"
        outputs["report.reference"] = "reference-check.json"
    return "\n".join(lines) + "\n", outputs


def prepare(p, ref, script):
    from eda_harness.plugins.semantic import tcl

    if p.operation in YOSYS or p.operation == "synthesize":
        commands = []
        original_ref = ref
        if p.frontend == "slang":
            aliases = {}
            links = {}
            for index, r in enumerate(p.sources):
                alias = f"slang-source-{index}.sv"
                aliases[original_ref(r)] = alias
                links[alias] = original_ref(r)
            for index, r in enumerate(p.includes):
                alias = f"slang-include-{index}"
                aliases[original_ref(r)] = alias + "/header"
                links[alias] = str(Path(original_ref(r)).parent)
            helper = "from pathlib import Path\n"
            for alias, source in links.items():
                helper += f"Path({alias!r}).symlink_to({source!r})\n"
            commands.append(["python3", script("slang-inputs.py", helper)])

            def ref(r):
                return aliases.get(original_ref(r), original_ref(r))

        body, outputs = yosys(p, ref)
        commands.append(["yosys", "-s", script("semantic.ys", body)])
        if p.operation == "prepare_lvs_reference":
            script(
                "reference-job.json",
                json.dumps({"top": p.top, "models": [original_ref(r) for r in p.spice_models]}),
            )
            commands.append(
                [
                    "python3",
                    script(
                        "assemble-reference.py", Path(__file__).with_name("spice_reference.py").read_text()
                    ),
                ]
            )
        return Prepared(commands, outputs)
    body = "\n".join(f"read_liberty {tcl(ref(r))}" for r in p.libraries) + "\n"
    body += f"read_db {tcl(ref(p.database))}\n"
    if p.constraints:
        body += f"read_sdc {tcl(ref(p.constraints))}\n"
    if p.technology_setup:
        body += f"source {tcl(ref(p.technology_setup))}\n"
    if p.parasitics:
        body += f"read_spef {tcl(ref(p.parasitics))}\n"
    body += "set block [ord::get_db_block]\nset before [llength [$block getInsts]]\n"
    op = p.operation
    if op == "check_design_rules" and not p.parasitics:
        body += "estimate_parasitics -placement\n"
    if op in REPAIRS:
        if not p.parasitics:
            body += "estimate_parasitics -placement\n"
        body += "report_check_types -violators -max_slew -max_capacitance -max_fanout > repair-before.txt\n"
        body += "set f [open repair-counts.txt w]\n"
        for kind in ("capacitance", "slew", "fanout"):
            body += f"puts $f [sta::max_{kind}_violation_count]\n"
        body += "close $f\n"
        if op == "repair_design":
            body += f"repair_design -max_utilization {p.max_utilization}\n"
        elif op == "repair_timing":
            body += f"set_propagated_clock [all_clocks]\nrepair_timing -{p.mode} -{p.mode}_margin {p.margin} -max_utilization {p.max_utilization}\n"
        elif op == "repair_tie_fanout":
            body += f"repair_tie_fanout {tcl(p.tie_port)} -separation {p.separation}\n"
        else:
            body += f"set_routing_layers -signal {tcl('-'.join(p.routing_layers))}\nglobal_route\nrepair_antennas {tcl(p.diode_cell)} -iterations {p.iterations}\n"
        body += "detailed_placement\ncheck_placement -verbose\nestimate_parasitics -placement\n"
        body += "report_check_types -violators -max_slew -max_capacitance -max_fanout > repair-after.txt\n"
        body += "set f [open repair-counts.txt a]\n"
        for kind in ("capacitance", "slew", "fanout"):
            body += f"puts $f [sta::max_{kind}_violation_count]\n"
        body += "close $f\n"

    if op in UTILITIES:
        for rule in p.connections:
            body += f"add_global_connection -net {tcl(rule.net)} -inst_pattern {tcl(rule.instance_pattern)} -pin_pattern {tcl(rule.pin_pattern)} -{rule.kind}\n"
        if p.connections:
            body += "global_connect\n"
        if op == "generate_pdn":
            body += f"set_voltage_domain -name CORE -power {tcl(p.power_net)} -ground {tcl(p.ground_net)}\ndefine_pdn_grid -name core -voltage_domains CORE\n"
            for stripe in p.stripes:
                body += (
                    f"add_pdn_stripe -grid core -layer {tcl(stripe.layer)} -width {stripe.width} -pitch {stripe.pitch} -offset {stripe.offset}"
                    + (" -followpins" if stripe.followpins else "")
                    + "\n"
                )
            for layers in p.connect_layers:
                body += f"add_pdn_connect -grid core -layers [list {' '.join(tcl(x) for x in layers)}]\n"
            body += "pdngen -failed_via_report failed-vias.rpt\n"
        elif op == "insert_tapcells":
            body += (
                "tapcell"
                + (f" -tapcell_master {tcl(p.tapcell)}" if p.tapcell else "")
                + (f" -endcap_master {tcl(p.endcap)}" if p.endcap else "")
                + f" -distance {p.distance}\n"
            )
        elif op == "insert_fillers":
            body += (
                f"filler_placement [list {' '.join(tcl(x) for x in p.fillers)}]\ncheck_placement -verbose\n"
            )
    outputs = {"report.repair": "repair-counts.txt"} if op in REPAIRS else {}
    if op in CHECKS:
        command = (
            "check_setup -verbose"
            if op == "check_constraints"
            else "report_check_types -violators -max_slew -max_capacitance -max_fanout -no_line_splits"
        )
        body += f"{command} > checks-native.txt\n"
        body += 'set f [open checks-native.txt r]\nset checks [read $f]\nclose $f\nset f [open checks.txt w]\nputs $f "EDA_NATIVE_CHECK_REPORT"\nputs $f $checks\nclose $f\n'
        body += "set f [open check-counts.txt w]\n"
        if op == "check_constraints":
            body += "puts $f [llength [sta::check_timing_cmd 1 1 1 1 1 1 1]]\n"
        else:
            for kind in ("capacitance", "slew", "fanout"):
                body += f"puts $f [sta::max_{kind}_violation_count]\n"
        body += "close $f\n"
        outputs["report.check_counts"] = "check-counts.txt"
        outputs["report.checks"] = "checks.txt"
    formats = (
        p.formats if op == "export_design" else ([] if op in CHECKS else ["odb", "def", "verilog", "sdc"])
    )
    for fmt in formats:
        kind, filename = EXPORTS[fmt]
        if fmt == "verilog":
            flag = " -include_pwr_gnd" if getattr(p, "include_power_ground", True) else ""
            body += f"write_verilog{flag} {filename}\n"
        else:
            body += f"write_{'db' if fmt == 'odb' else fmt} {filename}\n"
        outputs[kind if op == "export_design" or fmt != "odb" else "layout.modified_odb"] = filename
    if op in REPAIRS | UTILITIES:
        body += "set f [open operation.txt w]\nputs $f $before\nputs $f [llength [[ord::get_db_block] getInsts]]\nclose $f\n"
        outputs["report.operation"] = "operation.txt"
    if op in UTILITIES and p.connections:
        body += "set f [open connections.txt w]\n"
        for rule in p.connections:
            body += "set matched 0\nset incorrect 0\nforeach term [[ord::get_db_block] getITerms] {\n"
            body += f"if {{[regexp {tcl(rule.instance_pattern)} [[$term getInst] getName]] && [regexp {tcl(rule.pin_pattern)} [[$term getMTerm] getName]]}} {{\n"
            body += f'incr matched\nset net [$term getNet]\nif {{$net eq "NULL" || $net eq ""}} {{incr incorrect}} elseif {{[$net getName] ne {tcl(rule.net)}}} {{incr incorrect}}\n}}\n}}\n'
            body += 'puts $f "$matched $incorrect"\n'
        body += "close $f\n"
        outputs["report.connections"] = "connections.txt"
    # Always round-trip the final ODB, even for checks and alternate export formats.
    body += "write_db readback.odb\n"
    readback = "read_db readback.odb\nset block [ord::get_db_block]\nset f [open database.txt w]\nputs $f [$block getName]\nputs $f [llength [$block getInsts]]\nclose $f\n"
    outputs["report.database"] = "database.txt"
    return Prepared(
        [
            ["openroad", "-exit", script("operation.tcl", body)],
            ["openroad", "-exit", script("readback.tcl", readback)],
        ],
        outputs,
    )


def observe(p, read, metric):
    op = p.operation
    details = {}
    if op in YOSYS or op == "synthesize":
        data = json.loads(read("netlist.json"))
        module = data.get("modules", {}).get(p.top)
        match = re.search(r"Found and reported (\d+) problems", read("report.netlist"))
        if not match:
            raise ValueError("Missing native Yosys check summary")
        problems = int(match[1])
        metric("netlist.problems", problems, "count", "report.netlist")
        if module is not None:
            metric("logic.cells", len(module.get("cells", {})), "count", "netlist.json")
        return (
            module is not None and problems == 0,
            "Yosys hierarchy and connectivity check; not equivalence or LVS proof",
            details,
        )
    native = read("report.database").splitlines()
    if len(native) != 2:
        raise ValueError("Missing database readback")
    count = int(native[1])
    passed = native[0] == p.top and count > 0
    metric("physical.instances", count, "count", "report.database")
    summary = "Native operation and database readback completed; downstream STA/DRC/LVS must be rerun"
    if op in REPAIRS | UTILITIES:
        values = [int(x) for x in read("report.operation").splitlines()]
        if len(values) != 2 or min(values) < 0 or values[1] != count:
            raise ValueError("Invalid before/after instance evidence")
        metric("physical.instances.delta", values[1] - values[0], "count", "report.operation")
        details["instances_before"] = values[0]
        details["instances_after"] = values[1]
    if op in REPAIRS:
        counts = [int(x) for x in read("report.repair").splitlines()]
        if len(counts) != 6 or min(counts) < 0:
            raise ValueError("Missing native before/after repair counts")
        for stage, values in (("before", counts[:3]), ("after", counts[3:])):
            for kind, value in zip(("capacitance", "slew", "fanout"), values, strict=True):
                metric(f"repair.{stage}.max_{kind}.violations", value, "count", "report.repair")
        details["parasitic_scope"] = (
            "After repair: placement estimates; reroute and re-extract before signoff checks"
        )
    if op in UTILITIES and p.connections:
        connections = [list(map(int, row.split())) for row in read("report.connections").splitlines()]
        if len(connections) != len(p.connections) or any(
            len(row) != 2 or min(row) < 0 for row in connections
        ):
            raise ValueError("Invalid power connection evidence")
        passed = passed and all(matched > 0 and incorrect == 0 for matched, incorrect in connections)
        details["connections"] = connections
        metric(
            "power_connections.incorrect", sum(row[1] for row in connections), "count", "report.connections"
        )
        metric(
            "power_connections.unmatched_rules",
            sum(row[0] == 0 for row in connections),
            "count",
            "report.connections",
        )

    if op in CHECKS:
        report = read("report.checks")
        if not report.startswith("EDA_NATIVE_CHECK_REPORT\n"):
            raise ValueError("Missing native check report")
        content = report.split("\n", 1)[1].strip()
        counts = [int(line) for line in read("report.check_counts").splitlines()]
        expected = 1 if op == "check_constraints" else 3
        if len(counts) != expected or any(c < 0 for c in counts):
            raise ValueError("Invalid native check counts")
        passed = passed and sum(counts) == 0
        if op == "check_constraints":
            metric("constraints.issue_groups", counts[0], "count", "report.check_counts")
        else:
            for kind, count in zip(("capacitance", "slew", "fanout"), counts, strict=True):
                metric(f"design_rules.max_{kind}.violations", count, "count", "report.check_counts")
        details = {"native_report": content[:4000]}
        summary = (
            "OpenSTA native constraint check"
            if op == "check_constraints"
            else "OpenSTA maximum capacitance/slew/fanout check"
        )
    return passed, summary, details
