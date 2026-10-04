"""Session identity: observed process facts, with launcher claims explicitly separated."""

import json
import os
import sys
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from uuid import uuid4

from eda_harness.viewers import viewer_capabilities


def capture_identity(project_root, read_only):
    package = {"version": None, "source_commit": None, "commit_evidence": None}
    try:
        dist = distribution("eda-harness")
        package["version"] = dist.version
        raw = dist.read_text("direct_url.json")
        direct = json.loads(raw) if raw else {}
        vcs = direct.get("vcs_info", {})
        if vcs.get("vcs") == "git" and vcs.get("commit_id"):
            package.update(source_commit=vcs["commit_id"], commit_evidence="installed direct_url.json")
    except (PackageNotFoundError, ValueError, TypeError):
        pass
    return {
        "session_id": uuid4().hex,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "process": {
            "pid": os.getpid(),
            "parent_pid": os.getppid(),
            "python_executable": sys.executable,
            "python_version": sys.version.split()[0],
            "package_path": str(Path(__file__).resolve().parents[1]),
        },
        "package": package,
        "default_project_path": str(Path(project_root).resolve()) if project_root is not None else None,
        "project_selection": "per_call_project_path",
        "read_only": read_only,
        "launcher_declaration": {
            "path": os.environ.get("EDA_HARNESS_LAUNCHER_PATH"),
            "plugin_version": os.environ.get("EDA_HARNESS_PLUGIN_VERSION"),
            "evidence": "environment supplied at startup; not independent proof of launch history",
        },
        "viewer_configuration": {
            name: {
                **details,
                "configured_override": os.environ.get(details["configuration"])
                if details["configuration"]
                else None,
                "resolution_source": "project_runtime"
                if details["configuration"] is None
                else "environment_override"
                if details["configuration"] in os.environ
                else "PATH_or_standard_app_bundle",
            }
            for name, details in viewer_capabilities()["viewers"].items()
        },
        "limitations": [
            "Snapshot of this MCP session, not the client's desired configuration or other servers.",
            "Unknown source_commit is not proof of an old version; source/wheel installs may omit VCS metadata.",
            "exec replaces the launcher process; missing wrapper in ps does not prove it was skipped.",
            "Killing a process does not guarantee that the client reloads its configuration.",
        ],
    }
