"""Read-only environment checks shared by CLI and MCP; never create a Harness store."""

import shutil
import subprocess
from pathlib import Path

import yaml

from eda_harness.core.models import ActionConfig
from eda_harness.core.workflow import catalog, plan
from eda_harness.core.workspace import load_project, scan
from eda_harness.plugins.semantic import preflight
from eda_harness.runtimes.execution import identity, run_probe
from eda_harness.tool_inventory import inspect_tools
from eda_harness.viewers import viewer_capabilities


def check_environment(root, target=None, image=None, probe_capabilities=False):
    """Check configured runtime, or a target's inputs and tool availability, without running a job."""
    checks = []

    def record(component, code, ready, detail, next_step=None):
        checks.append(dict(component=component, code=code, ready=ready, detail=detail, next_step=next_step))

    def probe(args):
        result = run_probe(args, capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise ValueError((result.stderr or result.stdout or "Probe failed")[-2000:])
        return result.stdout.strip()

    root = Path(root).resolve() if root is not None else None
    result = {
        "project_root": str(root) if root is not None else None,
        "target": target,
        "scope": "target_inputs_and_tool_availability" if target else "project_and_default_runtime",
        "checks": checks,
        "host": {"docker_executable": shutil.which("docker")},
        "limitations": [
            "Does not prove task execution, tool compatibility, PDK correctness or acceptance.",
            "Desktop viewers are optional; discovery does not prove GUI loading.",
            "Without target, action overrides, inputs and EDA executables are not checked.",
            "Reports the MCP server host, not a remote client's environment.",
        ],
    }

    def finish():
        result["viewers"] = viewer_capabilities()
        result["ready"] = all(check["ready"] for check in checks)
        return result

    if root is None or not (root / "eda.yaml").exists():
        record(
            "project",
            "PROJECT_REQUIRED" if root is None else "PROJECT_MISSING",
            False,
            "No project selected" if root is None else "eda.yaml not found",
            "Run eda init --top <top> --local, or eda init --top <top> --image <image>.",
        )
        candidate = image or "eda-harness-tools:dev"
        result["scope"] = "preinitialization_docker_environment"
        result["runtime"] = {
            "kind": "docker",
            "image": candidate,
            "source": "explicit_candidate" if image else "default_candidate",
        }
        result["limitations"] = [
            "Candidate image only; no project runtime has been configured.",
            "Host EDA binaries are not checked; desktop viewers are optional.",
            "Environment readiness does not establish project, PDK or acceptance readiness.",
            "Reports the MCP server host and its Docker context, not the client computer.",
        ]
        if target is not None:
            record(
                "target",
                "TARGET_REQUIRES_PROJECT",
                False,
                "Target inputs cannot be checked before initialization",
                "Initialize eda.yaml first.",
            )
        if not result["host"]["docker_executable"]:
            record(
                "docker",
                "DOCKER_MISSING",
                False,
                "Docker executable not found",
                "Install Docker on the MCP host and start its daemon.",
            )
        else:
            try:
                version = probe(["docker", "info", "--format", "{{.ServerVersion}}"])
                record("docker", "DOCKER_AVAILABLE", True, {"server_version": version})
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                record(
                    "docker",
                    "DOCKER_UNAVAILABLE",
                    False,
                    str(error),
                    "Start Docker and check the MCP process Docker context and permissions.",
                )
            else:
                try:
                    inventory = inspect_tools(candidate)
                    result["tool_inventory"] = inventory
                    record(
                        "image_tools",
                        "IMAGE_TOOLS_READY" if inventory["ready"] else "IMAGE_TOOLS_INCOMPLETE",
                        inventory["ready"],
                        {"image": candidate, "image_id": inventory["image_id"]},
                        None
                        if inventory["ready"]
                        else "Repair missing/failing tools in the candidate image.",
                    )
                except (ValueError, OSError, subprocess.SubprocessError) as error:
                    record(
                        "image_tools",
                        "IMAGE_INSPECTION_FAILED",
                        False,
                        str(error),
                        "Provide an existing Harness tool image with image=..., or build/pull it explicitly.",
                    )
        result["environment_ready"] = all(
            c["ready"] for c in checks if c["component"] not in {"project", "target"}
        )
        result["project_ready"] = False
        return finish()
    if image is not None:
        record(
            "runtime",
            "IMAGE_OVERRIDE_REJECTED",
            False,
            "image is only accepted before initialization; existing projects use eda.yaml",
            "Omit image to check the configured runtime.",
        )
        return finish()
    try:
        project = load_project(root)
        actions = plan(target, project.workflow) if target is not None else []
    except (ValueError, OSError, yaml.YAMLError) as error:
        record("project", "PROJECT_INVALID", False, str(error), "Fix eda.yaml or the target name.")
        return finish()
    record("project", "PROJECT_VALID", True, "Project configuration parsed")
    if target is not None:
        try:
            files = scan(root, project)
            for action in actions:
                cfg = project.actions.get(action, ActionConfig())
                if cfg.parameters is not None:
                    preflight(action, project, {"files": files})
                elif not cfg.script and action not in ("rtl.lint", "logic.synthesize"):
                    raise ValueError(f"{action}: configure structured parameters or a project script")
            record("inputs", "INPUTS_VALID", True, "Declared target input checks passed")
        except (ValueError, OSError) as error:
            record("inputs", "INPUTS_INVALID", False, str(error), "Fix target inputs and configuration.")

    specs = catalog(project.workflow)
    requests = [
        (project.actions.get(a, ActionConfig()).runtime or project.runtime, specs[a].tool) for a in actions
    ] or [(project.runtime, None)]
    checked = set()
    docker_ready = None
    images = {}
    for runtime, tool in requests:
        key = (runtime.kind, runtime.image, tool)
        if key in checked:
            continue
        checked.add(key)
        if runtime.kind == "local":
            if tool is None:
                record(
                    "runtime",
                    "LOCAL_RUNTIME",
                    True,
                    "Local execution configured; choose a target to check tools",
                )
                continue
            try:
                info = identity(runtime, tool)
                record(tool, "TOOL_AVAILABLE", True, info)
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                record(tool, "TOOL_UNAVAILABLE", False, str(error), f"Install/fix {tool} on the server PATH.")
            continue
        if docker_ready is None:
            docker_ready = False
            if not shutil.which("docker"):
                record(
                    "docker",
                    "DOCKER_MISSING",
                    False,
                    "Docker executable not found",
                    "Install Docker on the server, or configure a local runtime with installed EDA tools.",
                )
            else:
                try:
                    probe(["docker", "info", "--format", "{{.ServerVersion}}"])
                    docker_ready = True
                    record("docker", "DOCKER_AVAILABLE", True, "Docker daemon reachable")
                except (ValueError, OSError, subprocess.SubprocessError) as error:
                    record(
                        "docker",
                        "DOCKER_UNAVAILABLE",
                        False,
                        str(error),
                        "Start Docker and check server permissions/context.",
                    )
        if not docker_ready:
            continue
        if runtime.image not in images:
            try:
                images[runtime.image] = probe(
                    ["docker", "image", "inspect", runtime.image, "--format", "{{.Id}}"]
                )
                record(runtime.image, "IMAGE_AVAILABLE", True, images[runtime.image])
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                images[runtime.image] = None
                record(
                    runtime.image,
                    "IMAGE_UNAVAILABLE",
                    False,
                    str(error),
                    "Build or pull the configured image on this Docker daemon.",
                )
        image = images[runtime.image]
        if tool and image:
            try:
                location = probe(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "--read-only",
                        "--cap-drop",
                        "ALL",
                        "--security-opt",
                        "no-new-privileges",
                        "--pids-limit",
                        "32",
                        "--memory",
                        "128m",
                        "--cpus",
                        "1",
                        "--entrypoint",
                        "/bin/sh",
                        image,
                        "-c",
                        'command -v "$1"',
                        "eda-doctor",
                        tool,
                    ]
                )
                record(f"{runtime.image}:{tool}", "TOOL_AVAILABLE", True, location)
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                record(
                    f"{runtime.image}:{tool}",
                    "TOOL_PROBE_FAILED",
                    False,
                    str(error),
                    f"Check image execution, /bin/sh and {tool} on the container PATH.",
                )
    if probe_capabilities:
        if target is None:
            record(
                "capabilities",
                "TARGET_REQUIRED",
                False,
                "Capability probing requires a target",
                "Specify target.",
            )
        else:
            from eda_harness.runtimes.capabilities import probe as probe_native

            for action in actions:
                cfg = project.actions.get(action, ActionConfig())
                runtime = cfg.runtime or project.runtime
                try:
                    detail = probe_native(runtime, specs[action].tool, cfg.parameters)
                    record(action, "CAPABILITIES_AVAILABLE", True, detail)
                except (ValueError, OSError, subprocess.SubprocessError) as error:
                    record(
                        action,
                        "CAPABILITY_UNAVAILABLE",
                        False,
                        str(error),
                        "Install/fix the required command or helper in the selected runtime.",
                    )
    return finish()
