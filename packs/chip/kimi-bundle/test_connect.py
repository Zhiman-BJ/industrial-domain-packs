"""Existing-Kimi connector regressions; fixtures are not engineering acceptance."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("connect_configure", Path(__file__).with_name("connect-configure.py"))
connector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(connector)


class ConnectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="existing kimi '")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "runtime"
        self.home = self.base / "existing native home"
        self.binary = self.base / "existing cli/kimi"
        self.binary.parent.mkdir()
        self.binary.write_text('#!/bin/sh\nprintf "2.1.1\\n"\n')
        self.binary.chmod(0o755)
        for name in ("upstream/kimi", "skills/chip-design/SKILL.md", "bin/eda-chip"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture " + name)
        self.manifest = {
            "name": "kimi-chip", "sourceDirty": False, "sourceCommit": "a" * 40,
            "releaseTag": "kimi-chip-fixture", "upstream": {"kimi": {"version": "2.1.1", "binarySha256": connector.shared.sha(self.root / "upstream/kimi")}},
            "files": {str(path.relative_to(self.root)): connector.shared.sha(path)
                      for path in self.root.rglob("*") if path.is_file()},
        }
        (self.root / "bundle.json").write_text(json.dumps(self.manifest))
        self.home.mkdir()
        (self.home / "mcp.json").write_text('{"custom":"retained","mcpServers":{"existing":{"url":"https://example.invalid/mcp","enabled":false}}}')
        self.user_files = {
            "config.toml": b'default_model="existing"\n# preserve byte for byte\n',
            "oauth/credentials.json": b'{"fixture":"user credential"}\n',
            "sessions/existing/agents/main/wire.jsonl": b'{"fixture":"existing session"}\n',
            "AGENTS.md": b"Existing user instructions\n",
            "SYSTEM.md": b"Existing user system prompt\n",
            "skills/user-skill/SKILL.md": b"Existing user skill\n",
        }
        for name, value in self.user_files.items():
            path = self.home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)

    def connect(self, **kwargs):
        return connector.connect(self.root, self.home, self.binary, "fixture:image", "sha256:fixture", **kwargs)

    def test_existing_native_files_and_binary_preserved(self):
        binary_before = self.binary.read_bytes()
        self.connect()
        for name, content in self.user_files.items():
            self.assertEqual((self.home / name).read_bytes(), content)
        self.assertEqual(self.binary.read_bytes(), binary_before)
        mcp = json.loads((self.home / "mcp.json").read_text())
        self.assertEqual(mcp["custom"], "retained")
        self.assertEqual(mcp["mcpServers"]["existing"]["enabled"], False)
        self.assertEqual(mcp["mcpServers"]["chip"], {"command": str(self.root / "bin/eda-chip"), "args": ["mcp"]})
        self.assertFalse((self.binary.parent / "kimi-chip").exists())

    def test_reconnect_retains_unrelated_changes_and_updates_managed_runtime(self):
        self.connect()
        mcp = json.loads((self.home / "mcp.json").read_text())
        mcp["mcpServers"]["later"] = {"command": "user-owned"}
        (self.home / "mcp.json").write_text(json.dumps(mcp))
        connector.connect(self.root, self.home, self.binary, "different:image", "sha256:next")
        self.assertIn("later", json.loads((self.home / "mcp.json").read_text())["mcpServers"])
        runtime = json.loads((self.home / "skills/chip-design/runtime.json").read_text())
        self.assertEqual(runtime["imageId"], "sha256:next")

    def test_docker_bridge_is_scoped_to_chip_mcp(self):
        self.connect(docker_access="/fixture/docker access")
        servers = json.loads((self.home / "mcp.json").read_text())["mcpServers"]
        self.assertEqual(servers["chip"]["env"], {"KIMI_CHIP_DOCKER_ACCESS_BIN": "/fixture/docker access"})
        self.assertNotIn("env", servers["existing"])

    def test_unmanaged_chip_registration_is_not_overwritten(self):
        before = '{"mcpServers":{"chip":{"command":"user-owned"}}}'
        (self.home / "mcp.json").write_text(before)
        with self.assertRaisesRegex(ValueError, "unmanaged or edited"):
            self.connect()
        self.assertEqual((self.home / "mcp.json").read_text(), before)
        self.assertFalse((self.home / "chip-pack-connect.json").exists())

    def test_edited_skill_preserved_without_rewriting_mcp(self):
        self.connect()
        (self.home / "skills/chip-design/SKILL.md").write_text("user edits")
        before = (self.home / "mcp.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "Preserving modified"):
            self.connect()
        self.assertEqual((self.home / "mcp.json").read_bytes(), before)
        self.assertEqual((self.home / "skills/chip-design/SKILL.md").read_text(), "user edits")

    def test_unmanaged_runtime_resource_is_not_overwritten(self):
        path = self.home / "skills/chip-design/runtime.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"image":"user-owned"}')
        before = (self.home / "mcp.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "Preserving modified"):
            self.connect()
        self.assertEqual(path.read_text(), '{"image":"user-owned"}')
        self.assertEqual((self.home / "mcp.json").read_bytes(), before)

    def test_legacy_and_unqualified_versions_rejected_before_config_write(self):
        before = (self.home / "mcp.json").read_bytes()
        for version in ("1.26.0", "2.1.2", "2.1.1-preview.1"):
            with self.subTest(version=version):
                self.binary.write_text(f'#!/bin/sh\nprintf "{version}\\n"\n')
                with self.assertRaisesRegex(ValueError, "not qualified"):
                    self.connect()
                self.assertEqual((self.home / "mcp.json").read_bytes(), before)

    def test_native_home_matches_upstream_and_ignores_xdg(self):
        with patch.dict(os.environ, {"XDG_DATA_HOME": "/fixture/xdg"}, clear=True):
            self.assertEqual(connector.native_home(), Path.home() / ".kimi-code")
        with patch.dict(os.environ, {"KIMI_CODE_HOME": "/fixture/native data"}, clear=True):
            self.assertEqual(connector.native_home(), Path("/fixture/native data"))

    def test_symlink_skill_directory_cannot_write_outside_native_home(self):
        outside = self.base / "outside"
        outside.mkdir()
        (self.home / "skills/chip-design").symlink_to(outside, target_is_directory=True)
        before = (self.home / "mcp.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.connect()
        self.assertEqual((self.home / "mcp.json").read_bytes(), before)
        self.assertFalse((outside / "SKILL.md").exists())


if __name__ == "__main__":
    unittest.main()
