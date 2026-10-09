"""Add Chip MCP and its native Skill to an existing, qualified Kimi installation."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess

spec = importlib.util.spec_from_file_location("bundle_configure", Path(__file__).with_name("configure.py"))
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)

QUALIFIED_KIMI_VERSION = "2.1.1"


def native_home():
    # Kimi-specific resources follow this upstream override, not XDG_DATA_HOME.
    override = os.environ.get("KIMI_CODE_HOME", "").strip()
    return Path(override).expanduser() if override else Path.home() / ".kimi-code"


def check_kimi(binary):
    if not binary.is_absolute() or not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError("--kimi-bin must name an existing absolute executable path.")
    completed = subprocess.run([str(binary), "--version"], check=True, capture_output=True, text=True)
    versions = re.findall(r"(?<![\w.])(\d+\.\d+\.\d+(?:-[\w.-]+)?)(?![\w.])", completed.stdout)
    if versions != [QUALIFIED_KIMI_VERSION]:
        raise ValueError(
            f"Existing Kimi version is not qualified: {completed.stdout.strip()!r}. "
            f"Use official Kimi Code {QUALIFIED_KIMI_VERSION}; this connector never upgrades it. "
            "Legacy Python Kimi uses a different data layout."
        )
    return versions[0]


def checked_path(path, home):
    if not path.resolve().is_relative_to(home):
        raise ValueError(f"Configuration destination escapes the native data directory: {path}")
    shared.regular_or_absent(path)


def read_object(path):
    value = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def connect(root, home, binary, image="", image_id="", docker_access=""):
    version = check_kimi(binary)
    manifest = shared.verify(root)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    mcp_file = home / "mcp.json"
    receipt_file = home / "chip-pack-connect.json"
    for path in (mcp_file, receipt_file):
        checked_path(path, home)
    previous = read_object(receipt_file)
    mcp = read_object(mcp_file)
    servers = mcp.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("Existing mcpServers must be a JSON object; nothing was changed.")
    entry = {"command": str(root / "bin/eda-chip"), "args": ["mcp"]}
    if docker_access:
        entry["env"] = {"KIMI_CHIP_DOCKER_ACCESS_BIN": docker_access}
    existing = servers.get("chip")
    if existing is not None and existing != entry and existing != previous.get("mcpEntry"):
        raise ValueError("Existing chip MCP is unmanaged or edited; preserving it. Resolve the name conflict first.")
    servers["chip"] = entry
    runtime = {
        "sourceCommit": manifest["sourceCommit"], "releaseTag": manifest["releaseTag"],
        "image": image or None, "imageId": image_id or None,
        "tools": "CHECKED" if image_id else "EXPLICITLY_SKIPPED",
        "engineeringAcceptance": "NOT_RUN_BY_INSTALLER",
    }
    candidates = [(source.relative_to(root / "skills"), source.read_bytes())
                  for source in sorted((root / "skills").rglob("*")) if source.is_file()]
    candidates.append((Path("chip-design/runtime.json"), shared.json_bytes(runtime)))
    writes = [(mcp_file, shared.json_bytes(mcp), 0o600)]
    managed = {}
    for relative_skill, content in candidates:
        destination = home / "skills" / relative_skill
        checked_path(destination, home)
        relative = str(destination.relative_to(home))
        if destination.exists() and destination.read_bytes() != content and shared.sha(destination) != previous.get("files", {}).get(relative):
            raise ValueError(f"Preserving modified or unmanaged Skill resource: {destination}")
        writes.append((destination, content, 0o644))
        managed[relative] = hashlib.sha256(content).hexdigest()
    # Validate all conflicts before writing; user model config, OAuth, instructions,
    # existing CLI, sessions and update settings are never opened for modification.
    for path, content, mode in writes:
        shared.atomic(path, content, mode)
    receipt = {
        **runtime, "mode": "existing-native-kimi", "bundle": str(root), "mcpEntry": entry,
        "files": managed, "kimiBinary": str(binary), "kimiVersionAtConnect": version,
    }
    shared.atomic(receipt_file, shared.json_bytes(receipt))
    print(json.dumps({"status": "CONNECTED", "home": str(home), **receipt}))
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--home", type=Path, default=native_home())
    parser.add_argument("--kimi-bin", required=True, type=Path)
    parser.add_argument("--image", default="")
    parser.add_argument("--image-id", default="")
    parser.add_argument("--docker-access", default="")
    args = parser.parse_args()
    connect(args.root.resolve(), args.home.resolve(), args.kimi_bin,
            args.image, args.image_id, args.docker_access)
