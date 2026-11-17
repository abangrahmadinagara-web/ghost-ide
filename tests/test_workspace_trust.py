#!/usr/bin/env python3
"""Unit tests for workspace_trust (Desktop Cycle 1, D7)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import workspace_trust as wt


class TrustStoreTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.store_path = Path(self.td.name) / "sub" / "trust.json"
        self.ws = Path(self.td.name) / "proj"
        self.ws.mkdir()

    def tearDown(self):
        self.td.cleanup()

    def test_unknown_is_untrusted_and_undecided(self):
        s = wt.TrustStore(self.store_path)
        self.assertIsNone(s.decision(self.ws))
        self.assertFalse(s.is_trusted(self.ws))

    def test_decisions_persist_across_instances(self):
        wt.TrustStore(self.store_path).set(self.ws, True,
                                           source="user")
        s2 = wt.TrustStore(self.store_path)
        self.assertTrue(s2.is_trusted(self.ws))
        s2.set(self.ws, False, source="user")
        self.assertIs(wt.TrustStore(self.store_path)
                      .decision(self.ws), False)

    def test_paths_are_resolved(self):
        s = wt.TrustStore(self.store_path)
        s.set(str(self.ws) + "/./", True)
        self.assertTrue(s.is_trusted(self.ws))


class InstructionsTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.ws = Path(self.td.name)
        (self.ws / "AGENTS.md").write_text("root rules")

    def tearDown(self):
        self.td.cleanup()

    def test_root_agents_md_loaded(self):
        files = wt.load_instructions(self.ws)
        self.assertEqual([f["rel"] for f in files], ["AGENTS.md"])
        self.assertIn("root rules", files[0]["text"])

    def test_override_replaces_root(self):
        (self.ws / ".ghostide").mkdir()
        (self.ws / ".ghostide" / "AGENTS.md").write_text(
            "private rules")
        files = wt.load_instructions(self.ws)
        self.assertEqual([f["rel"] for f in files],
                         [".ghostide/AGENTS.md"])
        block = wt.instructions_block(self.ws)
        self.assertIn("private rules", block)
        self.assertNotIn("root rules", block)

    def test_none_when_absent(self):
        (self.ws / "AGENTS.md").unlink()
        self.assertEqual(wt.load_instructions(self.ws), [])
        self.assertEqual(wt.instructions_block(self.ws), "")

    def test_cap_truncates(self):
        (self.ws / "AGENTS.md").write_text(
            "x" * (wt.INSTRUCTIONS_CAP + 500))
        files = wt.load_instructions(self.ws)
        self.assertLessEqual(files[0]["chars"],
                             wt.INSTRUCTIONS_CAP + 100)
        self.assertIn("truncated", files[0]["text"])


if __name__ == "__main__":
    unittest.main()
