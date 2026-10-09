"""Package pristine upstream Kimi with Chip MCP; run on native Linux amd64."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile

RECIPE_DIR = Path(__file__).resolve().parent
ROOT = RECIPE_DIR.parents[2]


def run(*args, **kwargs):
    return subprocess.check_output([str(arg) for arg in args], **kwargs).decode().strip()


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(spec, cache, name):
    target = cache / name
    if not target.exists():
        partial = target.with_suffix(".part")
        subprocess.run(["curl", "--proto", "=https", "--proto-redir", "=https", "-fL",
                        "--retry", "3", spec["url"], "-o", str(partial)], check=True)
        if sha(partial) != spec["sha256"]:
            raise ValueError(f"Checksum mismatch: {name}")
        partial.rename(target)
    if sha(target) != spec["sha256"]:
        raise ValueError(f"Checksum mismatch: {name}")
    return target


def extract(archive, destination):
    with tarfile.open(archive) as stream:
        stream.extractall(destination, filter="data")


def build(output, cache):
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("Build on native Linux x86_64 (Python >=3.12).")
    if run("git", "status", "--porcelain", cwd=ROOT):
        raise ValueError("Build only a clean, committed tree; no local/private files.")
    recipe = json.loads((RECIPE_DIR / "recipe.json").read_text())
    commit = run("git", "rev-parse", "HEAD", cwd=ROOT)
    tag = f"kimi-chip-v{recipe['version']}"
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    archive = output / f"{tag}-linux-x64.tar.gz"
    if archive.exists():
        raise ValueError("Refusing to replace an existing release archive.")
    with tempfile.TemporaryDirectory(prefix="kimi-chip-build-") as temporary:
        temp = Path(temporary)
        source_tar = temp / "source.tar"
        subprocess.run(["git", "archive", "--output", str(source_tar), commit,
                        "packs/chip", "LICENSE", "THIRD_PARTY_NOTICES.md", "licenses",
                        "content-lock.json"], cwd=ROOT, check=True)
        extract(source_tar, temp / "source")
        source = temp / "source"
        chip = source / "packs/chip"
        bundle = chip / "kimi-bundle"
        stage = temp / tag
        stage.mkdir()
        (stage / "upstream").mkdir()
        extract(download(recipe["kimi"], cache, "kimi-2.1.1-linux-x64.tar.gz"), stage / "upstream")
        if sha(stage / "upstream/kimi") != recipe["kimi"]["binarySha256"]:
            raise ValueError("Upstream native binary identity differs from its published manifest.")
        extract(download(recipe["python"], cache, "python-3.13.16-linux-x64.tar.gz"), stage)
        extract(download(recipe["uv"], cache, "uv-0.11.6-linux-x64.tar.gz"), temp)
        uv = temp / "uv-x86_64-unknown-linux-gnu/uv"
        python = stage / "python/bin/python3"
        if run(python, "--version") != "Python " + recipe["python"]["version"]:
            raise ValueError("Unexpected Python interpreter version.")
        if recipe["kimi"]["version"] not in run(stage / "upstream/kimi", "--version"):
            raise ValueError("Unexpected Kimi CLI version.")
        eda = chip / "eda-harness"
        libs = stage / "python-libs"
        locks = eda / "src/eda_harness/dependency_locks"
        for requirements, target in [("runtime.txt", libs), ("build.txt", temp / "build-libs")]:
            subprocess.run([str(uv), "pip", "install", "--python", str(python), "--target", str(target),
                            "--require-hashes", "--only-binary", ":all:", "-r", str(locks / requirements)], check=True)
        build_env = {**os.environ, "PYTHONPATH": str(temp / "build-libs"), "PYTHONNOUSERSITE": "1"}
        subprocess.run([str(uv), "build", "--wheel", "--no-build-isolation", "--python", str(python),
                        "--out-dir", str(temp / "wheels"), str(eda)], env=build_env, check=True)
        wheel, = (temp / "wheels").glob("*.whl")
        subprocess.run([str(uv), "pip", "install", "--python", str(python), "--target", str(libs),
                        "--no-deps", str(wheel)], check=True)
        for path in libs.rglob("direct_url.json"):
            path.unlink()  # Temporary build paths are not release provenance.
        # Domain source, locked image recipe and public fixtures only; no internal docs/runs.
        target_eda = stage / "chip/eda-harness"
        target_eda.mkdir(parents=True)
        for name in ("src", "tools", "examples"):
            shutil.copytree(eda / name, target_eda / name)
        for name in ("Dockerfile.tools", "LICENSE", "pyproject.toml", "uv.lock"):
            shutil.copy2(eda / name, target_eda / name)
        shutil.copytree(bundle / "skills", stage / "skills")
        shutil.copy2(chip / "scripts/mcp-smoke.py", stage / "mcp-smoke.py")
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            shutil.copy2(source / name, stage / name)
        shutil.copytree(source / "licenses", stage / "licenses")
        # Kimi's original MIT notice, obtained from its exact tagged source.
        license_spec = {"url": f"https://raw.githubusercontent.com/MoonshotAI/kimi-code/{recipe['kimi']['sourceCommit']}/LICENSE",
                        "sha256": recipe["kimi"]["licenseSha256"]}
        shutil.copy2(download(license_spec, cache, "kimi-2.1.1.LICENSE"), stage / "licenses/kimi-code.MIT")
        (stage / "bin").mkdir()
        for name in ("kimi-chip", "eda-chip"):
            shutil.copy2(bundle / name, stage / "bin" / name)
            (stage / "bin" / name).chmod(0o755)
        for name in ("install.sh", "configure.py", "README.md", "README.zh-CN.md"):
            shutil.copy2(bundle / name, stage / name)
        manifest = {
            "schemaVersion": 1, "name": "kimi-chip", "releaseTag": tag, "platform": "linux/amd64",
            "sourceCommit": commit, "sourceDirty": False,
            "packContentSha256": json.loads((source / "content-lock.json").read_text())["contentSha256"],
            "edaVersion": "0.6.1", "upstream": recipe,
            "defaultImage": f"eda-harness-tools:{tag}",
            "imageRecipeSha256": sha(target_eda / "Dockerfile.tools"),
            "trajectoryFormat": "upstream Kimi Code session agents/*/wire.jsonl and native export ZIP",
            "files": {},
        }
        for path in sorted(stage.rglob("*")):
            if path.is_file() and not path.is_symlink():
                manifest["files"][str(path.relative_to(stage))] = sha(path)
        (stage / "bundle.json").write_text(json.dumps(manifest, indent=2) + "\n")
        with tarfile.open(archive, "w:gz", compresslevel=6) as stream:
            stream.add(stage, arcname=tag)
    digest = sha(archive)
    archive.with_name(archive.name + ".sha256").write_text(f"{digest}  {archive.name}\n")
    bootstrap = (RECIPE_DIR / "bootstrap.sh").read_text().replace("@BUNDLE_SHA256@", digest).replace(
        "@BUNDLE_URL@", f"https://github.com/Zhiman-BJ/industrial-domain-packs/releases/download/{tag}/{archive.name}").replace("@RELEASE_TAG@", tag)
    entry = output / "install-kimi-chip.sh"
    entry.write_text(bootstrap)
    entry.chmod(0o755)
    entry.with_name(entry.name + ".sha256").write_text(f"{sha(entry)}  {entry.name}\n")
    (output / "build-identity.json").write_text(json.dumps({**manifest, "files": None, "archiveSha256": digest}, indent=2) + "\n")
    print(json.dumps({"archive": str(archive), "sha256": digest, "sourceCommit": commit}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/kimi-chip")
    parser.add_argument("--cache", type=Path, default=ROOT / "tmp/kimi-chip-downloads")
    args = parser.parse_args()
    build(args.output.resolve(), args.cache.resolve())
