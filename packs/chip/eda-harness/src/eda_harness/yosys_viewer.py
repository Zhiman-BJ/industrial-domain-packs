"""Yosys-native schematic rendering using the project's configured execution runtime."""

import dataclasses
import subprocess
from pathlib import Path
from uuid import uuid4

from eda_harness.core.workspace import load_project
from eda_harness.netlist_viewer import TIMEOUT, render
from eda_harness.runtimes.execution import Runtime, identity


def prepare(result, harness):
    config = load_project(harness.root).runtime.model_copy(deep=True)
    config.resources.timeout_seconds = min(config.resources.timeout_seconds, TIMEOUT)
    directory = Path(result["directory"])
    mode = "show" if result["viewer"] == "yosys_show" else "viz"
    options = "-width -long -stretch " if mode == "show" else ""
    (directory / "viewer.ys").write_text(
        f"read_json selected.json\n{mode} {options}-format dot -prefix schematic -viewer none\n"
    )
    commands = [
        ["yosys", "-Q", "-T", "-s", "viewer.ys"],
        ["dot", "-Tsvg", "schematic.dot", "-o", "schematic.svg"],
    ]
    result.update(
        commands=commands,
        runtime=config.model_dump(mode="json"),
        execution_host="project_runtime",
        argv=commands[0],
        manual_command=None,
        dot_output=str(directory / "schematic.dot"),
        note="Render with the configured project runtime; SVG display occurs on the MCP host. No acceptance change.",
    )
    return config


def run(result, config, launch):
    directory = Path(result["directory"])
    result["executions"] = []
    try:
        tool_identity = identity(config, "yosys")
        result["tool_identity"] = tool_identity
        deps = directory / "deps"
        deps.mkdir(exist_ok=True)
        runtime = Runtime(
            config, tool_identity, "eda-view-" + uuid4().hex, directory / "artifacts", deps, directory
        )

        def execute(commands):
            last = None
            for command in commands:
                last = runtime.execute(command, {}, Path(result["log"]), lambda: False, lambda *args: None)
                result["executions"].append(dataclasses.asdict(last))
                if last.status != "SUCCESS":
                    raise ValueError(
                        f"{command[0]} {last.status}; inspect viewer.log. Yosys and Graphviz dot are required in the project runtime; rebuild Dockerfile.tools for older images."
                    )
            return last

        # Confirm both executables before rendering; Graphviz's version flag is -V.
        execute([["yosys", "-V"], ["dot", "-V"]])
        render(result, None, launch, executor=lambda: execute(result["commands"]))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result.update(status="RENDER_FAILED", error=str(error))
