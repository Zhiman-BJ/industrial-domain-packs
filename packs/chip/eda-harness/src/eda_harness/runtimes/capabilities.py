"""Bounded native capability probes; no project scripts or design execution."""

import subprocess

from eda_harness.plugins.advanced import OPENROAD, YOSYS
from eda_harness.runtimes.execution import run_probe


def requirements(tool, parameters):
    if parameters is None:
        return {"commands": [], "executables": [tool], "scope": "script contents not inferred"}
    op = parameters.operation
    commands = []
    executables = [tool]
    if tool == "yosys":
        if op == "equivalence":
            commands = ["read_verilog", "prep", "async2sync", "dffunmap", "memory_map", "equiv_make", "equiv_induct", "equiv_status"]
            if parameters.engine == "eqy":
                executables += ["eqy", "sby", parameters.solver]
            return {"commands": commands, "executables": executables, "scope": "presence only; real positive and negative proofs establish compatibility"}
        if parameters.frontend == "slang" or op == "prepare_lvs_reference":
            executables.append("python3")
        commands = ["read_slang" if parameters.frontend == "slang" else "read_verilog", "hierarchy", "check"]
        if op == "prepare_lvs_reference":
            commands.append("write_spice")
        elif op == "synthesize":
            commands += ["synth", "techmap", "dfflibmap", "abc"]
    elif tool == "openroad":
        commands = {
            "export_design": ["read_db", "write_db", "write_verilog", "write_sdc", "write_spef"],
            "connect_power": ["add_global_connection", "global_connect"],
            "generate_pdn": ["define_pdn_grid", "add_pdn_stripe", "pdngen"],
            "insert_tapcells": ["tapcell"],
            "insert_fillers": ["filler_placement"],
            "check_constraints": ["check_setup"],
            "check_design_rules": ["report_check_types"],
            "sta": ["read_spef", "report_checks", "sta::worst_slack"],
            "power": ["read_spef", "report_power"],
        }.get(op, [op] if op in OPENROAD else [])
    elif tool == "gtkwave":
        executables += ["python3", "xvfb-run"]
        if parameters.format == "vcd":
            executables.append("vcd2fst")
    elif tool == "klayout" and getattr(parameters, "rule_format", None) == "macro":
        executables.append("python3")
    return {
        "commands": commands,
        "executables": executables,
        "scope": "command presence; native contract tests establish compatibility",
    }


def probe(runtime, tool, parameters):
    from eda_harness.plugins.semantic import SUPPORT

    if parameters is not None and tool not in SUPPORT[parameters.operation]:
        raise ValueError(f"Unsupported backend {tool} for {parameters.operation}")
    required = requirements(tool, parameters)
    prefix = []
    if runtime.kind == "docker":
        inspected = subprocess.run(
            ["docker", "image", "inspect", runtime.image, "--format", "{{.Id}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        prefix = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--entrypoint",
        ]
        image = inspected.stdout.strip()

    def run(argv, stdin=None):
        actual = [*prefix, argv[0], image, *argv[1:]] if prefix else argv
        result = run_probe(actual, input=stdin, capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise ValueError((result.stderr or result.stdout)[-2000:])
        return result.stdout + result.stderr

    evidence = []
    for binary in required["executables"]:
        evidence.append(
            {"executable": binary, "path": run(["/bin/sh", "-c", 'command -v "$1"', "probe", binary]).strip()}
        )
    for command in required["commands"]:
        if tool == "yosys":
            text = run(["yosys", "-Q", "-T", "-p", f"help {command}"])
            if "No such command" in text:
                raise ValueError(f"Yosys command unavailable: {command}")
        else:
            text = run(
                ["openroad", "-exit", "/dev/stdin"],
                f'if {{[llength [info commands {command}]] == 0}} {{error "Missing {command}"}}\nputs EDA_CAPABILITY_OK\n',
            )
            if "EDA_CAPABILITY_OK" not in text:
                raise ValueError(f"OpenROAD command unavailable: {command}")
        evidence.append({"command": command, "available": True})
    return {"requirements": required, "evidence": evidence}


SUPPORTED_OPERATIONS = sorted(YOSYS | OPENROAD)
