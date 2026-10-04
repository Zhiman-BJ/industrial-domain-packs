"""Project initialization shared by MCP and CLI, with Docker as the default runtime."""

from pathlib import Path

import yaml

from eda_harness.core.environment import check_environment
from eda_harness.core.models import Project

DEFAULT_IMAGE = "eda-harness-tools:dev"


def initialize_project(root, top, name="design", image=None, local=False):
    root = Path(root).resolve()
    target = root / "eda.yaml"
    if target.exists() or target.is_symlink():
        raise ValueError("eda.yaml already exists; inspect the existing project")
    if root == Path.home().resolve():
        raise ValueError("Choose a dedicated project directory; do not initialize your home directory")
    if not top.strip() or not name.strip():
        raise ValueError("top and name must not be empty")
    if local and image is not None:
        raise ValueError("Choose either explicit local execution or a Docker image")
    selected_image = None if local else (image or DEFAULT_IMAGE)
    project = Project(
        name=name,
        top=top,
        inputs={"rtl": ["rtl/**/*.sv"]},
        runtime={"kind": "local" if local else "docker", "image": selected_image},
    )
    environment = None if local else check_environment(root, image=selected_image)
    if environment is not None and not environment["environment_ready"]:
        return {
            "status": "BLOCKED",
            "created": False,
            "project": str(root),
            "environment": environment,
            "next_step": "Fix Docker or the candidate tool image, then retry initialization. Host viewers are optional.",
        }
    root.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents overwriting a config created while the probe was running.
    with target.open("x") as stream:
        stream.write(yaml.safe_dump(project.model_dump(), sort_keys=False))
    return {
        "status": "INITIALIZED",
        "created": True,
        "project": str(root),
        "config": str(target),
        "runtime": project.runtime.model_dump(),
        "environment": environment,
        "execution_ready": False,
        "next_steps": [
            "Add design inputs, PDK references and flow/action configuration.",
            "Call check_environment(target=...) to check the configured runtime and required inputs/tools.",
            "Inspect optional host viewers separately; missing viewers only limit native interactive display.",
        ],
        "note": "Configuration created; this does not install a PDK or prove execution/acceptance readiness."
        if not local
        else "Explicit local runtime: required host EDA tools must be checked for the chosen target.",
    }
