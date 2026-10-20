"""Catalog transport/integrity regressions; fixtures are not CAD acceptance."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "packs/pcb/scripts/generate-kicad-3d-catalog.py"
spec = importlib.util.spec_from_file_location("model_catalog", SCRIPT)
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.models = {"Fixture.3dshapes/A.step": b"ISO-10303-21;\r\nEND-ISO-10303-21;\r\n",
                       "Fixture.3dshapes/B.step": b"fixture content B\n",
                       "LICENSE.md": b"Original fixture license\n"}
        self.tree = [{"path": name, "type": "blob", "mode": "100644",
                      "id": hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()}
                     for name, data in self.models.items()]

    def archive(self, entries=None):
        target = self.root / "fixture.tar.xz"
        with tarfile.open(target, "w:xz") as output:
            for name, data in entries if entries is not None else self.models.items():
                item = tarfile.TarInfo("kicad-packages3D-10.0.7/" + name)
                if data is None:
                    item.type = tarfile.SYMTYPE
                    item.linkname = "/outside"
                    output.addfile(item)
                else:
                    item.size = len(data)
                    output.addfile(item, io.BytesIO(data))
        return target

    def scan(self, entries=None, **limits):
        return catalog.scan_archive(self.archive(entries), self.tree, expected_count=2, **limits)

    def test_sha256_is_computed_from_original_bytes_including_crlf(self):
        files, notice = self.scan()
        self.assertEqual(len(files), 2)
        self.assertEqual(notice, self.models["LICENSE.md"])
        for item in files:
            original = self.models[item["path"]]
            self.assertEqual(item["size"], len(original))
            self.assertEqual(item["sha256"], hashlib.sha256(original).hexdigest())

    def test_altered_bytes_and_missing_models_are_rejected(self):
        changed = {**self.models, "Fixture.3dshapes/A.step": b"changed source"}
        with self.assertRaisesRegex(ValueError, "official Git blob"):
            self.scan(changed.items())
        with self.assertRaisesRegex(ValueError, "missing official models"):
            self.scan(list(self.models.items())[1:])

    def test_extra_step_repeated_path_links_and_traversal_are_rejected(self):
        for extra in [("Unknown.3dshapes/Extra.step", b"extra"),
                      ("Fixture.3dshapes/A.step", self.models["Fixture.3dshapes/A.step"]),
                      ("../outside", b"unsafe")]:
            with self.subTest(extra=extra[0]), self.assertRaises(ValueError):
                self.scan([*self.models.items(), extra])
        linked = [(name, None if name.endswith("A.step") else data) for name, data in self.models.items()]
        with self.assertRaisesRegex(ValueError, "Invalid, repeated or oversized"):
            self.scan(linked)

    def test_model_expansion_compression_and_entry_budgets_are_checked(self):
        with self.assertRaisesRegex(ValueError, "oversized"):
            self.scan(model_limit=8)
        with self.assertRaisesRegex(ValueError, "decompressed byte budget"):
            self.scan(expanded_limit=1024)
        with patch.object(catalog, "MAX_ARCHIVE", 1):
            with self.assertRaisesRegex(ValueError, "compressed budget"):
                self.scan()
        with patch.object(catalog, "MAX_ENTRIES", 2):
            with self.assertRaisesRegex(ValueError, "entry count"):
                self.scan()

    def test_partial_or_unpinned_tree_cannot_be_used_as_release_catalog(self):
        target = self.root / "tree.json"
        target.write_text(json.dumps(self.tree))
        with self.assertRaisesRegex(ValueError, "differs from pinned"):
            catalog.read_tree(target)
        with self.assertRaisesRegex(ValueError, "model count"):
            catalog.scan_archive(self.archive(), self.tree)
        with self.assertRaisesRegex(ValueError, "repeated"):
            catalog.canonical_tree([*self.tree, self.tree[0]])

    def test_source_receipt_requires_exact_archive_name_and_limits(self):
        target = self.root / "source.dsc"
        digest = catalog.ARCHIVE_SHA256
        target.write_text(f"Checksums-Sha256:\n {digest} {catalog.ARCHIVE_SIZE} {catalog.ARCHIVE_NAME}\n")
        self.assertEqual(catalog.archive_receipt(target)["sha256"], digest)
        target.write_text(f"Checksums-Sha256:\n {digest} 123 different.tar.xz\n")
        with self.assertRaisesRegex(ValueError, "expected 10.0.7"):
            catalog.archive_receipt(target)
        target.write_text(f"Checksums-Sha256:\n {digest} 123 {catalog.ARCHIVE_NAME}\n")
        with self.assertRaisesRegex(ValueError, "differs from the pinned"):
            catalog.archive_receipt(target)

    def test_download_limits_and_failed_hash_remove_partial_files(self):
        class Response(io.BytesIO):
            headers = {}

            def geturl(self):
                return "https://example.invalid/fixture"
        target = self.root / "download"
        for options in ({"max_bytes": 2}, {"max_bytes": 1024, "expected_sha256": "00" * 32}):
            with self.subTest(options=options), patch.object(catalog.urllib.request, "urlopen", return_value=Response(b"source")):
                with self.assertRaises(ValueError):
                    catalog.download("https://example.invalid/fixture", target, **options)
                self.assertFalse(target.exists())
                self.assertFalse(target.with_name("download.partial").exists())
        with self.assertRaisesRegex(ValueError, "Only HTTPS"):
            catalog.download("http://example.invalid/fixture", target, 100)


if __name__ == "__main__":
    unittest.main()
