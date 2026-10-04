"""Tool-specific preparation/parsing. No agent-runtime dependencies."""

import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from eda_harness.core.models import ActionConfig, Diagnostic, Metric, VerificationResult
from eda_harness.core.workspace import safe_path


@dataclass
class Prepared:
    commands: list[list[str]]
    outputs: dict[str, str] = field(default_factory=dict)


@dataclass
class Observation:
    metrics: list[Metric]
    verification: VerificationResult
    diagnostics: list[Diagnostic]


class EDAPlugin(Protocol):
    def prepare(self, action, project, snapshot, paths, work) -> Prepared: ...
    def observe(self, action, project, work, log, artifacts) -> Observation: ...


def quoted(value):
    if any(c in value for c in ('"', "\n", "\r", "\\")):
        raise ValueError(f"Unsupported tool path: {value!r}")
    return f'"{value}"'


class ToolPlugin:
    def prepare(self, action, project, snapshot, paths, work):
        config = project.actions.get(action, ActionConfig())
        if config.parameters is not None:
            from eda_harness.plugins import semantic

            return semantic.prepare(action, project, snapshot, paths, work)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", project.top):
            raise ValueError("Top must be a simple HDL identifier")
        rtl = [
            str(Path(paths["input"]) / path)
            for path, info in sorted(snapshot["files"].items())
            if info["type"] == "rtl"
        ]
        includes = sorted(
            {
                str((Path(paths["input"]) / path).parent)
                for path, info in snapshot["files"].items()
                if info["type"] == "include"
            }
        )
        if action == "rtl.lint" and not config.script:
            return Prepared(
                [
                    [
                        "verilator",
                        "--lint-only",
                        "--sv",
                        "-Wall",
                        "--top-module",
                        project.top,
                        *[f"-I{p}" for p in includes],
                        *config.args,
                        *rtl,
                    ]
                ],
                config.outputs,
            )
        if action == "logic.synthesize" and not config.script:
            lines = [
                "read_verilog -sv " + " ".join([*[f"-I{quoted(p)}" for p in includes], *map(quoted, rtl)]),
                f"hierarchy -check -top {project.top}",
                f"synth -top {project.top}",
                "check -assert",
                "write_verilog netlist.v",
                "write_json netlist.json",
            ]
            (work / "synth.ys").write_text("\n".join(lines) + "\n")
            return Prepared(
                [["yosys", *config.args, "-s", "synth.ys"]],
                {"netlist.gate": "netlist.v", "netlist.json": "netlist.json", **config.outputs},
            )
        if not config.script:
            raise ValueError(f"{action} requires a project-owned script and declared output/evidence files")
        script = str(Path(paths["input"]) / config.script)
        from eda_harness.core.workflow import catalog

        tool = catalog(project.workflow)[action].tool
        if tool == "klayout":
            command = ["klayout", "-b", *config.args, "-r", script]
        elif tool == "openroad":
            command = ["openroad", "-exit", *config.args, script]
        elif tool == "yosys":
            command = ["yosys", *config.args, "-s", script]
        elif tool == "magic":
            command = ["magic", "-dnull", "-noconsole", *config.args, script]
        elif tool == "netgen":
            command = ["netgen", "-batch", "source", script, *config.args]
        elif tool == "gtkwave":
            command = ["/bin/sh", script, *config.args]
        elif tool == "verilator":
            # Script builds/runs a testbench with the project's tool options; it is part of the snapshot.
            command = ["/bin/sh", script, *config.args]
        else:
            raise ValueError(action)
        return Prepared([command], config.outputs)

    def observe(self, action, project, work, log, artifacts):
        config = project.actions.get(action, ActionConfig())
        if config.parameters is not None:
            from eda_harness.plugins import semantic

            return semantic.observe(action, project, work, log, artifacts)
        evidence = [a["id"] for a in artifacts]
        log_id = next(a["id"] for a in artifacts if a["type"] == "log.tool")
        metrics, diagnostics = [], []
        status, summary = "UNKNOWN", "No independent design evidence was provided"
        text = log.read_text(errors="replace")
        if action == "rtl.lint":
            errors = len(re.findall(r"^%Error", text, re.MULTILINE))
            warnings = len(re.findall(r"^%Warning", text, re.MULTILINE))
            metrics += [
                Metric(name="lint.errors", value=errors, unit="count", evidence=log_id, action=action),
                Metric(name="lint.warnings", value=warnings, unit="count", evidence=log_id, action=action),
            ]
            status = "FAIL" if errors or warnings else "PASS"
            summary = f"Verilator: {errors} errors, {warnings} warnings"
        if action == "logic.synthesize":
            net = next((a for a in artifacts if a["type"] == "netlist.json"), None)
            if net:
                data = json.loads(safe_path(work, net["path"]).read_text())
                module = data.get("modules", {}).get(project.top)
                if module is not None:
                    metrics.append(
                        Metric(
                            name="logic.cells",
                            value=len(module.get("cells", {})),
                            unit="count",
                            evidence=net["id"],
                            action=action,
                        )
                    )
                    status, summary = (
                        "PASS",
                        "Top module exists in parsed Yosys netlist (not equivalence proof)",
                    )
        if action == "verify.drc":
            report = next((a for a in artifacts if a["type"] == "report.drc"), None)
            if report:
                root = ET.parse(safe_path(work, report["path"])).getroot()
                items = root.find("items")
                if root.tag != "report-database" or items is None:
                    raise ValueError("Expected KLayout report-database with items")
                entries = items.findall("item")
                counts = Counter()
                examples = []
                for item in entries:
                    multiplicity = int(item.findtext("multiplicity", "1"))
                    if multiplicity < 1:
                        raise ValueError("Invalid DRC item multiplicity")
                    rule = item.findtext("category", "unknown").strip("'")
                    counts[rule] += multiplicity
                    if len(examples) < 8:
                        examples.append(
                            {
                                "rule": rule,
                                "cell": item.findtext("cell"),
                                "geometry": [v.text for v in item.findall("values/value")][:3],
                            }
                        )
                count = sum(counts.values())
                if count:
                    diagnostics.append(
                        Diagnostic(
                            category=action,
                            severity="high",
                            summary=f"DRC: {count} violations across {len(counts)} rules",
                            evidence=[report["id"]],
                            details={"rules": dict(counts.most_common(20)), "examples": examples},
                        )
                    )
                metrics.append(
                    Metric(
                        name="drc.violations", value=count, unit="count", evidence=report["id"], action=action
                    )
                )
                status = "PASS" if count == 0 else "FAIL"
                summary = f"KLayout DRC: {count} violations"
        if action == "physical.route":
            lengths = re.findall(r"^Total wire length = ([0-9.eE+-]+) um\.", text, re.MULTILINE)
            if lengths:
                metrics.append(
                    Metric(
                        name="route.wirelength",
                        value=float(lengths[-1]),
                        unit="um",
                        evidence=log_id,
                        action=action,
                    )
                )
        if action == "analysis.power":
            report = next((a for a in artifacts if a["type"] == "report.power"), None)
            if report:
                power = safe_path(work, report["path"]).read_text()
                total = re.search(
                    r"^Total\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)",
                    power,
                    re.MULTILINE,
                )
                if total and "Watts" in power:
                    for name, value in zip(
                        ("internal", "switching", "leakage", "total"), total.groups(), strict=True
                    ):
                        metrics.append(
                            Metric(
                                name="power." + name,
                                value=float(value),
                                unit="W",
                                evidence=report["id"],
                                action=action,
                            )
                        )
                    status = "PASS" if all(float(v) >= 0 for v in total.groups()) else "FAIL"
                    summary = "OpenSTA estimated power report parsed (default activity unless project supplies activity)"
        if config.metrics_file:
            artifact = next(a for a in artifacts if a["path"] == config.metrics_file)
            records = json.loads(safe_path(work, config.metrics_file).read_text())
            if not isinstance(records, list):
                raise ValueError("Metrics report must be a list of {name,value,unit}")
            for item in records:
                metrics.append(Metric(**item, evidence=artifact["id"], action=action))
        if config.verification_file:
            artifact = next(a for a in artifacts if a["path"] == config.verification_file)
            records = json.loads(safe_path(work, config.verification_file).read_text())
            # Independent checks must report explicit booleans; empty assertions never mean PASS.
            checks = records.get("checks", [])
            if not checks or any(type(c.get("passed")) is not bool or not c.get("name") for c in checks):
                raise ValueError("Verification report requires nonempty named boolean checks")
            status = "PASS" if status != "FAIL" and all(c["passed"] for c in checks) else "FAIL"
            check_summary = "; ".join(f"{c['name']}: {c['passed']}" for c in checks)
            summary = (
                check_summary
                if summary == "No independent design evidence was provided"
                else summary + "; " + check_summary
            )
        if action == "analysis.sta":
            wns = next((m for m in metrics if m.name == "timing.setup.wns" and m.unit == "ns"), None)
            if wns:
                hold = next((m for m in metrics if m.name == "timing.hold.wns" and m.unit == "ns"), None)
                status = (
                    "PASS"
                    if status != "FAIL" and wns.value >= 0 and (hold is None or hold.value >= 0)
                    else "FAIL"
                )
                summary = (
                    f"Post-route setup WNS: {wns.value} ns; hold WNS: {hold.value if hold else 'missing'} ns"
                )
                report = next((a for a in artifacts if a["type"] == "report.timing"), None)
                if report and status == "FAIL":
                    timing = safe_path(work, report["path"]).read_text()
                    paths = re.findall(r"Startpoint:.*?(?:slack[^\n]*|\Z)", timing, re.DOTALL)
                    diagnostics.append(
                        Diagnostic(
                            category=action,
                            severity="high",
                            summary=summary,
                            evidence=[report["id"]],
                            details={"critical_paths": [p[:4000] for p in paths[:4]]},
                        )
                    )
        names = [m.name for m in metrics]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate normalized metric names in one action")
        if status != "PASS" and not diagnostics:
            diagnostics.append(
                Diagnostic(
                    category=action,
                    severity="high" if status == "FAIL" else "medium",
                    summary=summary,
                    evidence=evidence,
                )
            )
        return Observation(
            metrics,
            VerificationResult(action=action, status=status, summary=summary, evidence=evidence),
            diagnostics,
        )


class VerilatorPlugin(ToolPlugin):
    pass


class YosysPlugin(ToolPlugin):
    pass


class OpenROADPlugin(ToolPlugin):
    pass


class KLayoutPlugin(ToolPlugin):
    pass


PLUGINS = {
    "verilator": VerilatorPlugin(),
    "yosys": YosysPlugin(),
    "openroad": OpenROADPlugin(),
    "klayout": KLayoutPlugin(),
    "magic": ToolPlugin(),
    "netgen": ToolPlugin(),
    "gtkwave": ToolPlugin(),
}
