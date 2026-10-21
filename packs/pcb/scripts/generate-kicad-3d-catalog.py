#!/usr/bin/env python3
"""Hash original STEP bytes, then bind them to the pinned official Git tree.

The Debian orig archive is a transport source, never the identity authority.
No model is extracted to disk. No WRL-to-STEP path substitution is performed.
"""

import argparse
import hashlib
import io
import json
import lzma
from pathlib import Path, PurePosixPath
import re
import tarfile
import time
import urllib.parse
import urllib.request


VERSION = "10.0.7"
REVISION = "8d4070daa0fa3f7a7f3c176f4ca7bffc4df392be"
REPOSITORY = "https://gitlab.com/kicad/libraries/kicad-packages3D"
BASE_URL = "https://gitlab.com/api/v4/"
DOWNLOAD = {"type": "gitlab-repository-files", "project": "kicad/libraries/kicad-packages3D", "ref": REVISION}
LICENSE_URL = f"{REPOSITORY}/-/raw/{REVISION}/LICENSE.md"
TREE_SHA256 = "6dd8d088e42f8a4cce5dae97daf0d6b8308c1c187497aac41f519edf9145e277"
MODEL_COUNT = 7252
ARCHIVE_NAME = f"kicad-packages3d_{VERSION}.orig.tar.xz"
ARCHIVE_SIZE = 313340588
ARCHIVE_SHA256 = "82bd1384887935a06b7bcb1f6be895fe2813556a6972bcf403022da53819547e"
DEBIAN_BASE = "https://deb.debian.org/debian/pool/main/k/kicad-packages3d/"
DSC_NAME = f"kicad-packages3d_{VERSION}-1.dsc"
MAX_ARCHIVE = 512 * 1024 * 1024
MAX_MODEL = 512 * 1024 * 1024
MAX_EXPANDED = 4 * 1024 * 1024 * 1024
MAX_ENTRIES = 20000
CHUNK = 1024 * 1024


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for data in iter(lambda: source.read(CHUNK), b""):
            digest.update(data)
    return digest.hexdigest()


def safe_path(path):
    value = PurePosixPath(path)
    if (not path or value.is_absolute() or ".." in value.parts
            or "\\" in path or "\0" in path):
        raise ValueError("Unsafe library archive path")
    return value


def canonical_tree(tree):
    records = []
    seen = set()
    for item in tree:
        path = item["path"]
        safe_path(path)
        if path in seen or not re.fullmatch(r"[a-f0-9]{40}", item["id"]):
            raise ValueError("Invalid or repeated official tree entry")
        seen.add(path)
        records.append({key: item[key] for key in ("path", "type", "id", "mode")})
    records.sort(key=lambda item: item["path"])
    return json.dumps(records, separators=(",", ":"), ensure_ascii=False).encode()


def read_tree(path):
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("Official tree exceeds metadata budget")
    tree = json.loads(path.read_text())
    if hashlib.sha256(canonical_tree(tree)).hexdigest() != TREE_SHA256:
        raise ValueError("Official tree differs from pinned 10.0.7 source")
    return tree


def download(url, target, max_bytes, expected_size=None, expected_sha256=None):
    """One bounded HTTPS request; failures leave neither a cache hit nor retries."""
    if not url.startswith("https://"):
        raise ValueError("Only HTTPS library sources are supported")
    temporary = target.with_name(target.name + ".partial")
    target.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 600
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "KiCad-Pack-catalog/1"})
        with urllib.request.urlopen(request, timeout=15) as response, temporary.open("wb") as output:
            if urllib.parse.urlparse(response.geturl()).scheme != "https":
                raise ValueError("Library redirect must retain HTTPS")
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > max_bytes:
                raise ValueError("Library response exceeds download budget")
            if declared and expected_size is not None and int(declared) != expected_size:
                raise ValueError("Library response size differs from source receipt")
            size = 0
            digest = hashlib.sha256()
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutError("Library download exceeded its time budget")
                data = response.read(min(CHUNK, max_bytes - size + 1))
                if not data:
                    break
                size += len(data)
                if size > max_bytes:
                    raise ValueError("Library response exceeds download budget")
                digest.update(data)
                output.write(data)
            if expected_size is not None and size != expected_size:
                raise ValueError("Library download size differs from source receipt")
            if expected_sha256 and digest.hexdigest() != expected_sha256:
                raise ValueError("Library download SHA256 differs from source receipt")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def fetch_tree(target):
    records = []
    for page in range(1, 101):
        query = urllib.parse.urlencode({"ref": REVISION, "recursive": "true", "per_page": 100, "page": page})
        url = "https://gitlab.com/api/v4/projects/kicad%2Flibraries%2Fkicad-packages3D/repository/tree?" + query
        temporary = target.with_name(target.name + ".page")
        try:
            download(url, temporary, 256 * 1024)
            batch = json.loads(temporary.read_text())
        finally:
            temporary.unlink(missing_ok=True)
        if not isinstance(batch, list):
            raise ValueError("Invalid official Git tree response")
        records.extend(batch)
        if len(batch) < 100:
            break
    else:
        raise ValueError("Official Git tree pagination exceeded its budget")
    if hashlib.sha256(canonical_tree(records)).hexdigest() != TREE_SHA256:
        raise ValueError("Downloaded official tree differs from pinned source")
    target.write_text(json.dumps(records, separators=(",", ":")) + "\n")


def archive_receipt(dsc):
    if dsc.stat().st_size > 128 * 1024:
        raise ValueError("Debian source receipt exceeds metadata budget")
    section = re.search(r"^Checksums-Sha256:\n((?:[ \t].*\n)+)", dsc.read_text(), re.M)
    if section:
        for line in section.group(1).splitlines():
            sha256, size, name = line.split()
            if name == ARCHIVE_NAME and re.fullmatch(r"[a-f0-9]{64}", sha256):
                if not 0 < int(size) <= MAX_ARCHIVE:
                    raise ValueError("Source archive exceeds compressed budget")
                if int(size) != ARCHIVE_SIZE or sha256 != ARCHIVE_SHA256:
                    raise ValueError("Source receipt differs from the pinned original archive")
                return {"url": DEBIAN_BASE + ARCHIVE_NAME, "size": int(size), "sha256": sha256}
    raise ValueError("Debian receipt does not bind the expected 10.0.7 archive")


class BoundedXZ(io.RawIOBase):
    """Bound decompressed bytes, elapsed time and the XZ dictionary allocation."""

    def __init__(self, source, expanded_limit=MAX_EXPANDED, seconds=600):
        self.source = source
        self.decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=256 * 1024 * 1024)
        self.expanded_limit = expanded_limit
        self.total = 0
        self.deadline = time.monotonic() + seconds

    def read(self, size=-1):
        if size < 0:
            raise ValueError("Unbounded archive reads are not allowed")
        result = bytearray()
        while len(result) < size and not self.decoder.eof:
            if time.monotonic() > self.deadline:
                raise TimeoutError("Library scan exceeded its time budget")
            incoming = self.source.read(65536) if self.decoder.needs_input else b""
            if self.decoder.needs_input and not incoming:
                raise ValueError("Truncated XZ source archive")
            data = self.decoder.decompress(incoming, max_length=size - len(result))
            self.total += len(data)
            if self.total > self.expanded_limit:
                raise ValueError("Library exceeds decompressed byte budget")
            result.extend(data)
        return bytes(result)


def scan_archive(archive, tree, expected_count=MODEL_COUNT, model_limit=MAX_MODEL, expanded_limit=MAX_EXPANDED):
    expected = {item["path"]: item["id"] for item in tree
                if item["type"] == "blob" and item["path"].lower().endswith((".step", ".stp"))}
    if len(expected) != expected_count:
        raise ValueError("Official source model count differs from the expected release")
    if not 0 < archive.stat().st_size <= MAX_ARCHIVE:
        raise ValueError("Source archive exceeds compressed budget")
    files, seen = [], set()
    license_bytes = None
    with archive.open("rb") as compressed:
        decoded = BoundedXZ(compressed, expanded_limit)
        with tarfile.open(fileobj=decoded, mode="r|", bufsize=CHUNK) as stream:
            for number, member in enumerate(stream, 1):
                if number > MAX_ENTRIES:
                    raise ValueError("Library archive entry count exceeds budget")
                parts = safe_path(member.name).parts
                if not parts or parts[0].lower() != "kicad-packages3d-" + VERSION:
                    raise ValueError("Unexpected source archive root")
                relative = "/".join(parts[1:])
                selected = relative in expected or relative == "LICENSE.md"
                if relative.lower().endswith((".step", ".stp")) and relative not in expected:
                    raise ValueError("Archive contains a STEP model absent from the official tree")
                if not selected:
                    continue
                if relative in seen or not member.isfile() or not 0 < member.size <= model_limit:
                    raise ValueError("Invalid, repeated or oversized source model")
                seen.add(relative)
                sha1 = hashlib.sha1(f"blob {member.size}\0".encode())
                sha256 = hashlib.sha256()
                remaining, content = member.size, bytearray()
                source = stream.extractfile(member)
                while remaining:
                    data = source.read(min(CHUNK, remaining))
                    if not data:
                        raise ValueError("Truncated source model")
                    remaining -= len(data)
                    sha1.update(data)
                    sha256.update(data)
                    if relative == "LICENSE.md":
                        if member.size > 65536:
                            raise ValueError("Library license exceeds metadata budget")
                        content.extend(data)
                official = expected.get(relative) or next((item["id"] for item in tree if item["path"] == relative), None)
                if sha1.hexdigest() != official:
                    raise ValueError("Source bytes differ from the official Git blob: " + relative)
                if relative == "LICENSE.md":
                    license_bytes = bytes(content)
                else:
                    files.append({"path": relative, "size": member.size, "sha256": sha256.hexdigest()})
                    if len(files) % 500 == 0:
                        print(json.dumps({"verifiedModels": len(files)}), flush=True)
    if len(files) != expected_count or set(expected) != {item["path"] for item in files} or license_bytes is None:
        raise ValueError("Archive is missing official models or the library license")
    files.sort(key=lambda item: item["path"])
    return files, license_bytes


def build_catalog(files, receipt):
    return {"schemaVersion": 2, "id": "kicad-3d", "version": VERSION, "baseUrl": BASE_URL,
            "download": dict(DOWNLOAD),
            "source": {"repository": REPOSITORY, "revision": REVISION, "tag": VERSION,
                       "treeSha256": TREE_SHA256, "archive": receipt,
                       "verification": "All original STEP bytes match their pinned official Git blob SHA1; SHA256 is computed independently from those bytes."},
            "license": {"spdx": "CC-BY-SA-4.0", "url": LICENSE_URL,
                        "notice": "licenses/kicad-3d-LICENSE.md", "exception": "KiCad libraries exception; see the original upstream notice."},
            "files": files}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--tree", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--dsc", type=Path)
    parser.add_argument("--download", action="store_true", help="Fetch missing pinned source inputs once over HTTPS")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "resources" / f"kicad-3d-{VERSION}.json")
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    tree_path = args.tree or args.cache / f"official-{VERSION}-tree.json"
    dsc = args.dsc or args.cache / DSC_NAME
    archive = args.archive or args.cache / ARCHIVE_NAME
    if args.download:
        if not tree_path.exists():
            fetch_tree(tree_path)
        if not dsc.exists():
            download(DEBIAN_BASE + DSC_NAME, dsc, 128 * 1024)
    tree = read_tree(tree_path)
    receipt = archive_receipt(dsc)
    if args.download and not archive.exists():
        download(receipt["url"], archive, MAX_ARCHIVE, receipt["size"], receipt["sha256"])
    if archive.stat().st_size != receipt["size"] or digest_file(archive) != receipt["sha256"]:
        raise ValueError("Cached archive differs from the Debian source receipt")
    files, license_bytes = scan_archive(archive, tree)
    catalog = build_catalog(files, receipt)
    output = (json.dumps(catalog, indent=2, ensure_ascii=False) + "\n").encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    notices = args.output.parent / "licenses"
    notices.mkdir(exist_ok=True)
    (notices / "kicad-3d-LICENSE.md").write_bytes(license_bytes)
    temporary = args.output.with_name(args.output.name + ".partial")
    temporary.write_bytes(output)
    temporary.replace(args.output)
    print(json.dumps({"catalog": str(args.output), "files": len(files), "bytes": len(output), "sha256": hashlib.sha256(output).hexdigest()}))


if __name__ == "__main__":
    main()
