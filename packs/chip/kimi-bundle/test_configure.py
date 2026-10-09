"""Installer regression checks; fixtures are not native engineering evidence."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("bundle_configure", Path(__file__).with_name("configure.py"))
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kimi chip '")
        self.addCleanup(self.temp.cleanup)
        self.root = (Path(self.temp.name) / "bundle").resolve()
        self.home = (Path(self.temp.name) / "native data").resolve()
        self.bin = (Path(self.temp.name) / "bin").resolve()
        for name in ("upstream/kimi", "skills/chip-design/SKILL.md"):
            file = self.root / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("fixture " + name)
        launcher = self.root / "bin/kimi-chip"
        launcher.parent.mkdir()
        launcher.write_text('#!/bin/sh\nprintf "%s\\n" "$KIMI_CODE_HOME" "$@"\n')
        launcher.chmod(0o755)
        binary_hash = configure.sha(self.root / "upstream/kimi")
        manifest = {"name": "kimi-chip", "sourceDirty": False, "sourceCommit": "a" * 40,
                    "releaseTag": "fixture", "upstream": {"kimi": {"version": "fixture", "binarySha256": binary_hash}},
                    "files": {str(file.relative_to(self.root)): configure.sha(file)
                              for file in self.root.rglob("*") if file.is_file()}}
        (self.root / "bundle.json").write_text(json.dumps(manifest))

    def install(self):
        configure.configure(self.root, self.home, self.bin, "fixture:image", "sha256:fixture")

    def test_native_config_and_arguments_survive_paths_with_quotes(self):
        self.install()
        result = subprocess.check_output([str(self.bin / "kimi-chip"), "-p", "a b ' c", "--continue"], text=True)
        self.assertEqual(result.splitlines(), [str(self.home), "-p", "a b ' c", "--continue"])
        entry = json.loads((self.home / "mcp.json").read_text())["mcpServers"]["chip"]
        self.assertEqual(entry, {"command": str(self.root / "bin/eda-chip"), "args": ["mcp"]})

    def test_reinstall_preserves_provider_config_and_other_mcp(self):
        self.install()
        (self.home / "config.toml").write_text('api_key = "fixture-only"\n')
        mcp = json.loads((self.home / "mcp.json").read_text())
        mcp["mcpServers"]["other"] = {"url": "https://example.invalid/mcp"}
        (self.home / "mcp.json").write_text(json.dumps(mcp))
        self.install()
        self.assertEqual((self.home / "config.toml").read_text(), 'api_key = "fixture-only"\n')
        self.assertIn("other", json.loads((self.home / "mcp.json").read_text())["mcpServers"])

    def test_modified_skill_is_preserved_before_any_config_write(self):
        self.install()
        skill = self.home / "skills/chip-design/SKILL.md"
        skill.write_text("user edits")
        original_mcp = (self.home / "mcp.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "Preserving modified"):
            self.install()
        self.assertEqual(skill.read_text(), "user edits")
        self.assertEqual((self.home / "mcp.json").read_bytes(), original_mcp)

    def test_conflicting_mcp_is_preserved(self):
        self.home.mkdir()
        config = '{"mcpServers":{"chip":{"command":"user-owned"}}}'
        (self.home / "mcp.json").write_text(config)
        with self.assertRaisesRegex(ValueError, "unmanaged"):
            self.install()
        self.assertEqual((self.home / "mcp.json").read_text(), config)

    def test_modified_upstream_binary_fails_identity(self):
        (self.root / "upstream/kimi").write_text("modified")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.install()


if __name__ == "__main__":
    unittest.main()
