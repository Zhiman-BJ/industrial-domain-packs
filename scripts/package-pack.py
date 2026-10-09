"""Build a source-only Pack archive from committed Git objects."""

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def package(pack, output):
    if not re.fullmatch(r"[a-z][a-z0-9-]*", pack):
        raise ValueError("Use a trusted Pack directory name.")
    commit = git("rev-parse", "HEAD").decode().strip()
    prefix = f"packs/{pack}/"
    metadata = json.loads(git("show", f"{commit}:{prefix}pack.json"))
    entries = []
    hashes = []
    for record in git("ls-tree", "-r", "-z", commit, "--", prefix, "lib", "content-lock.json", "package.json").split(b"\0"):
        if not record:
            continue
        identity, name = record.split(b"\t", 1)
        mode, kind, oid = identity.decode().split()
        path = name.decode()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError(f"Pack sources must be regular files: {path}")
        relative = path[len(prefix):] if path.startswith(prefix) else "repository-shared/" + path
        data = git("cat-file", "blob", oid)
        entries.append((f"{pack}-pack/{relative}", data, 0o755 if mode == "100755" else 0o644))
        hashes.append({"path": relative, "sha256": hashlib.sha256(data).hexdigest(), "sizeBytes": len(data)})
    if not entries:
        raise ValueError("No committed Pack files found.")
    for path in ("LICENSE", "THIRD_PARTY_NOTICES.md", "licenses/industrial-agent-harness.MIT", "provenance/chip-bootstrap.json", "provenance/domain-migration.json"):
        entries.append((f"{pack}-pack/repository-notices/{path}", git("show", f"{commit}:{path}"), 0o644))
    manifest = {"schemaVersion": 1, "kind": "source-only", "packId": metadata["id"], "sourceVersion": metadata["sourceVersion"], "sourceCommit": commit, "files": hashes}
    entries.append((f"{pack}-pack/source-manifest.json", (json.dumps(manifest, indent=2) + "\n").encode(), 0o644))
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"{pack}-source-{commit[:12]}.tar.gz"
    with archive.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as tar:
                for name, data, mode in sorted(entries):
                    if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
                        raise ValueError("Archive path escaped the Pack.")
                    item = tarfile.TarInfo(name)
                    item.size = len(data)
                    item.mode = mode
                    tar.addfile(item, io.BytesIO(data))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name + ".sha256").write_text(f"{digest}  {archive.name}\n")
    return {"archive": str(archive), "sha256": digest, "sourceCommit": commit, "kind": "source-only"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("pack", choices=["chip", "godot", "pcb", "freecad", "cad", "cuda"])
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    print(json.dumps(package(args.pack, args.output)))
