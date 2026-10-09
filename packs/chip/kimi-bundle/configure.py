"""Install standard native MCP/Skill configuration, never agent orchestration."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import tempfile


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def atomic(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".kimi-chip-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def regular_or_absent(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"Expected a regular file or absent path: {path}")


def verify(root):
    manifest = json.loads((root / "bundle.json").read_text())
    if manifest["name"] != "kimi-chip" or manifest["sourceDirty"] is not False:
        raise ValueError("Not a committed Kimi Chip bundle.")
    for name, expected in manifest["files"].items():
        path = root / name
        if not path.resolve().is_relative_to(root) or path.is_symlink() or sha(path) != expected:
            raise ValueError(f"Bundle file checksum mismatch: {name}")
    if sha(root / "upstream/kimi") != manifest["upstream"]["kimi"]["binarySha256"]:
        raise ValueError("Upstream Kimi was modified.")
    return manifest


def configure(root, home, bin_dir, image, image_id):
    manifest = verify(root)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    bin_dir.mkdir(parents=True, exist_ok=True)
    mcp_file = home / "mcp.json"
    state_file = home / "chip-bundle-install.json"
    for path in (mcp_file, state_file):
        regular_or_absent(path)
    previous = json.loads(state_file.read_text()) if state_file.exists() else {}
    mcp = json.loads(mcp_file.read_text()) if mcp_file.exists() else {}
    servers = mcp.setdefault("mcpServers", {})
    entry = {"command": str(root / "bin/eda-chip"), "args": ["mcp"]}
    existing = servers.get("chip")
    if existing is not None and existing != entry and existing != previous.get("mcpEntry"):
        raise ValueError("The chip MCP registration was edited or is unmanaged. Choose another KIMI_CODE_HOME.")
    servers["chip"] = entry
    runtime = {
        "sourceCommit": manifest["sourceCommit"], "releaseTag": manifest["releaseTag"],
        "image": image or None, "imageId": image_id or None,
        "tools": "CHECKED" if image_id else "EXPLICITLY_SKIPPED",
        "engineeringAcceptance": "NOT_RUN_BY_INSTALLER",
    }
    writes = [(mcp_file, json_bytes(mcp), 0o600)]
    managed = {}
    for source in sorted((root / "skills").rglob("*")):
        if not source.is_file():
            continue
        destination = home / "skills" / source.relative_to(root / "skills")
        regular_or_absent(destination)
        content = source.read_bytes()
        relative = str(destination.relative_to(home))
        if destination.exists() and destination.read_bytes() != content and sha(destination) != previous.get("files", {}).get(relative):
            raise ValueError(f"Preserving modified/unmanaged Skill: {destination}. Choose another KIMI_CODE_HOME.")
        writes.append((destination, content, 0o644))
        managed[relative] = hashlib.sha256(content).hexdigest()
    runtime_file = home / "skills/chip-design/runtime.json"
    regular_or_absent(runtime_file)
    writes.append((runtime_file, json_bytes(runtime), 0o644))
    access = os.environ.get("KIMI_CHIP_DOCKER_ACCESS_BIN")
    for name in ("kimi-chip", "eda-chip"):
        launcher = bin_dir / name
        regular_or_absent(launcher)
        marker = "# Kimi Chip managed launcher"
        if launcher.exists() and marker not in launcher.read_text().splitlines():
            raise ValueError(f"Preserving unmanaged launcher: {launcher}")
        content = "#!/usr/bin/env bash\n" + marker + "\nset -euo pipefail\n"
        content += "default_kimi_home=" + shlex.quote(str(home)) + "\n"
        content += 'export KIMI_CODE_HOME="${KIMI_CODE_HOME:-$default_kimi_home}"\n'
        if access:
            content += "export KIMI_CHIP_DOCKER_ACCESS_BIN=" + shlex.quote(access) + "\n"
            content += 'export PATH="$KIMI_CHIP_DOCKER_ACCESS_BIN:$PATH"\n'
        content += "exec " + shlex.quote(str(root / "bin" / name)) + ' "$@"\n'
        writes.append((launcher, content.encode(), 0o755))
    # Check every conflict before publishing any replacement. Preserve all native settings/secrets.
    for path, content, mode in writes:
        atomic(path, content, mode)
    state = {**runtime, "bundle": str(root), "mcpEntry": entry, "files": managed,
             "kimiVersion": manifest["upstream"]["kimi"]["version"],
             "upstreamBinarySha256": sha(root / "upstream/kimi")}
    atomic(state_file, json_bytes(state))
    print(json.dumps({"status": "INSTALLED", "home": str(home), "launcher": str(bin_dir / "kimi-chip"), **state}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["verify", "configure"])
    parser.add_argument("root", type=Path)
    parser.add_argument("--home", type=Path)
    parser.add_argument("--bin-dir", type=Path)
    parser.add_argument("--image", default="")
    parser.add_argument("--image-id", default="")
    args = parser.parse_args()
    if args.operation == "verify":
        print(json.dumps({"status": "VERIFIED", "sourceCommit": verify(args.root.resolve())["sourceCommit"]}))
    else:
        if args.home is None or args.bin_dir is None:
            parser.error("configure needs --home and --bin-dir")
        configure(args.root.resolve(), args.home.resolve(), args.bin_dir.resolve(), args.image, args.image_id)
