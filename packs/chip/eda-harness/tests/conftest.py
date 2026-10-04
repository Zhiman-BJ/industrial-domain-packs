import json
import sys

import pytest
import yaml

from eda_harness.core.models import ActionConfig
from eda_harness.core.service import Harness
from eda_harness.core.workflow import ACTIONS, catalog
from eda_harness.plugins.tools import Prepared, ToolPlugin


class FixturePlugin(ToolPlugin):
    """Synthetic subprocess evidence only for harness contract tests; never a shipped plugin."""

    def prepare(self, action, project, snapshot, paths, work):
        cfg = project.actions.get(action, ActionConfig())
        outputs = {
            kind: kind.replace(".", "_") + ".json"
            for kind in catalog(project.workflow)[action].artifact_types
        }
        payload = {path: "artifact " + snapshot["files"]["rtl/top.sv"]["hash"] for path in outputs.values()}
        if action == "logic.synthesize":
            outputs["netlist.json"] = "netlist.json"
            payload["netlist.json"] = json.dumps({"modules": {"top": {"cells": {"a": {}}}}})
        if action == "verify.drc":
            payload[outputs["report.drc"]] = "<report-database><items/></report-database>"
        if cfg.metrics_file:
            payload[cfg.metrics_file] = json.dumps(
                [
                    {"name": "timing.setup.wns", "value": project.identities.get("wns", "0.1"), "unit": "ns"},
                    {"name": "area.total", "value": 100, "unit": "um^2"},
                ]
            )
        if cfg.verification_file:
            payload[cfg.verification_file] = json.dumps(
                {"checks": [{"name": "fixture-check", "passed": True}]}
            )
        if project.identities.get("missing_output"):
            payload = {}
        code = (
            "from pathlib import Path; import time; time.sleep(" + project.identities.get("sleep", "0") + ")"
        )
        if payload:
            code += "; "
        code += "; ".join(f"Path({path!r}).write_text({text!r})" for path, text in payload.items())
        code += '; print("fixture tool completed")'
        return Prepared([[sys.executable, "-c", code]], outputs)


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl/top.sv").write_text("module top; endmodule\n")
    (tmp_path / "constraints.sdc").write_text("create_clock -period 10 clk\n")
    (tmp_path / "rules.drc").write_text("rule version 1\n")
    (tmp_path / "flow.txt").write_text("fixture script\n")
    config = {
        "name": "fixture",
        "top": "top",
        "inputs": {"rtl": ["rtl/*.sv"], "sdc": ["constraints.sdc"], "drc_rules": ["rules.drc"]},
        "runtime": {"kind": "local", "resources": {"timeout_seconds": 10}},
        "required_verification": ["rtl.lint", "analysis.sta", "verify.drc", "verify.lvs"],
        "constraints": {"timing.setup.wns": {"op": ">=", "value": 0, "unit": "ns"}},
        "actions": {a: {"script": "flow.txt"} for a in ACTIONS},
    }
    config["actions"]["analysis.sta"]["metrics_file"] = "metrics.json"
    config["actions"]["verify.lvs"]["verification_file"] = "verification.json"
    config["actions"]["rtl.simulate"]["verification_file"] = "verification.json"
    (tmp_path / "eda.yaml").write_text(yaml.safe_dump(config))
    monkeypatch.setattr(
        "eda_harness.core.service.identity", lambda *args: {"kind": "local", "version": "fixture-v1"}
    )
    monkeypatch.setattr(
        "eda_harness.core.service.PLUGINS",
        {tool: FixturePlugin() for tool in ["verilator", "yosys", "openroad", "klayout"]},
    )
    return Harness(tmp_path)


def configure(h, update):
    path = h.root / "eda.yaml"
    cfg = yaml.safe_load(path.read_text())
    update(cfg)
    path.write_text(yaml.safe_dump(cfg))
