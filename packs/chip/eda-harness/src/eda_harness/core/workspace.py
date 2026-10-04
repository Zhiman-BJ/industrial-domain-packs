import fcntl
import hashlib
import subprocess
from contextlib import contextmanager
from pathlib import Path

import yaml

from eda_harness.core.models import Project, SourceSnapshot
from eda_harness.core.store import digest, now, uid


@contextmanager
def project_lock(store):
    with (store.root / "project.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def safe_path(root: Path, relative: str):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] in (".eda", ".git"):
        raise ValueError(f"Unsafe project path: {relative}")
    target = root / path
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes project: {relative}")
    # Reject symlinks even when they point within the project: snapshot materialization must be unambiguous.
    if any(p.is_symlink() for p in [target, *target.parents] if p != root.parent):
        raise ValueError(f"Symlink inputs are unsupported: {relative}")
    return target


def load_project(root):
    return Project.model_validate(yaml.safe_load(safe_path(root, "eda.yaml").read_text()))


def scan(root, project):
    files = {}
    for category, patterns in project.inputs.items():
        for pattern in patterns:
            safe_path(root, pattern)
            matches = sorted(p for p in root.glob(pattern) if p.is_file())
            for path in matches:
                rel = path.relative_to(root).as_posix()
                safe_path(root, rel)
                if rel == "eda.yaml":
                    raise ValueError("eda.yaml is captured automatically; do not include it in input globs")
                if rel in files and files[rel]["type"] != category:
                    raise ValueError(f"Input belongs to multiple semantic types: {rel}")
                data = path.read_bytes()
                files[rel] = {"type": category, "hash": hashlib.sha256(data).hexdigest()}
    for cfg in project.actions.values():
        if cfg.script:
            path = safe_path(root, cfg.script)
            files.setdefault(
                cfg.script, {"type": "action_script", "hash": hashlib.sha256(path.read_bytes()).hexdigest()}
            )
    return files


def snapshot(root, store, base):
    project = load_project(root)
    files = scan(root, project)
    for path, item in files.items():
        if store.blob(safe_path(root, path).read_bytes()) != item["hash"]:
            raise ValueError("Working copy changed while snapshotting; retry")
    if scan(root, load_project(root)) != files or load_project(root) != project:
        raise ValueError("Working copy changed while snapshotting; retry")

    def git(*args):
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
        return result.stdout if result.returncode == 0 else None

    commit = git("rev-parse", "HEAD")
    diff = git("diff", "HEAD", "--binary")
    snap = SourceSnapshot(
        id=uid("I"),
        project_id=project.name,
        base_state_id=base,
        files=files,
        config=project.model_dump(),
        git={
            "commit": commit.decode().strip() if commit else None,
            "diff_hash": hashlib.sha256(diff).hexdigest() if diff is not None else None,
        },
        created_at=now(),
    ).model_dump()
    snap["fingerprint"] = digest({"files": files, "config": snap["config"]})
    store.put("snapshot", snap["id"], snap)
    return snap


def materialize(store, snap, destination):
    destination.mkdir(parents=True, exist_ok=True)
    for rel, item in snap["files"].items():
        path = safe_path(destination, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(store.read_blob(item["hash"]))
    safe_path(destination, "eda.yaml").write_text(yaml.safe_dump(snap["config"], sort_keys=False))
