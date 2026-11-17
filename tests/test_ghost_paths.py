#!/usr/bin/env python3
"""Unit tests for ghost_paths (Desktop Cycle 1: portable mode)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ghost_paths as gp

ROOT = Path("/repo/ghost-ide")


class PortableRequested(unittest.TestCase):
    def test_flag(self):
        self.assertTrue(gp.portable_requested(
            argv=["GhostIDE", "--portable"], env={}))

    def test_env(self):
        for v in ("1", "true", "YES", "on"):
            self.assertTrue(gp.portable_requested(
                argv=[], env={"GHOSTIDE_PORTABLE": v}))

    def test_off(self):
        self.assertFalse(gp.portable_requested(argv=[], env={}))
        self.assertFalse(gp.portable_requested(
            argv=[], env={"GHOSTIDE_PORTABLE": "0"}))


class DataDir(unittest.TestCase):
    def test_default_is_home_dotghostide(self):
        d = gp.data_dir(ROOT, frozen=False, argv=[], env={})
        self.assertEqual(d, Path.home() / ".ghostide")

    def test_portable_source_beside_server(self):
        d = gp.data_dir(ROOT, frozen=False,
                        argv=["--portable"], env={})
        self.assertEqual(d, ROOT / "ghostide-data")

    def test_portable_frozen_beside_executable(self):
        d = gp.data_dir(ROOT, frozen=True, argv=[],
                        env={"GHOSTIDE_PORTABLE": "1"},
                        argv0="/apps/GhostIDE/GhostIDE")
        self.assertEqual(d, Path("/apps/GhostIDE/ghostide-data"))


class DefaultWorkspace(unittest.TestCase):
    def test_source_plain_keeps_playground(self):
        self.assertIsNone(gp.default_workspace(
            ROOT, frozen=False, argv=[], env={}))

    def test_frozen_plain_is_home_ghostide(self):
        self.assertEqual(
            gp.default_workspace(ROOT, frozen=True, argv=[], env={}),
            Path.home() / "GhostIDE")

    def test_portable_always_inside_data(self):
        for frozen in (False, True):
            ws = gp.default_workspace(
                ROOT, frozen=frozen, argv=["--portable"], env={},
                argv0="/apps/GhostIDE/GhostIDE")
            self.assertTrue(str(ws).endswith(
                "ghostide-data/workspace"), ws)


if __name__ == "__main__":
    unittest.main()
