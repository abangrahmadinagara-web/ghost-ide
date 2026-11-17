#!/usr/bin/env python3
"""Unit tests for updater (Desktop Cycle 1, D3).

The end-to-end swap/rollback flow is covered by
`python3 updater.py selftest`; these tests pin the pure
pieces: version compare, manifest check, fail-closed
verification and the settings opt-out.
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import updater as up


def manifest(version="9.9.9", sha="a" * 64, size=10):
    asset = {"url": "https://example.invalid/pkg.tar.gz",
             "sha256": sha, "size": size}
    return {"version": version,
            "platforms": {"linux": asset, "macos": asset,
                          "windows": asset}}


class VersionTests(unittest.TestCase):
    def test_parse(self):
        self.assertGreater(up.parse_version("3.11.0"),
                           up.parse_version("3.10.1"))
        self.assertEqual(up.parse_version("3.10.1"),
                         up.parse_version("3.10.1"))
        self.assertEqual(up.parse_version("x"), (0,))


class CheckTests(unittest.TestCase):
    def test_newer_available(self):
        r = up.check_manifest(manifest(), "3.10.1", plat="linux")
        self.assertTrue(r["updateAvailable"])
        self.assertEqual(r["latest"], "9.9.9")

    def test_same_or_older_not_available(self):
        self.assertFalse(up.check_manifest(
            manifest("3.10.1"), "3.10.1", plat="linux")
            ["updateAvailable"])
        self.assertFalse(up.check_manifest(
            manifest("3.9.0"), "3.10.1", plat="linux")
            ["updateAvailable"])

    def test_missing_platform_asset(self):
        m = manifest()
        m["platforms"] = {}
        self.assertFalse(up.check_manifest(
            m, "1.0.0", plat="linux")["updateAvailable"])


class VerifyTests(unittest.TestCase):
    def test_verify_ok_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "pkg.bin"
            p.write_bytes(b"hello world")
            sha = hashlib.sha256(b"hello world").hexdigest()
            self.assertTrue(up.verify_file(p, 11, sha))
            with self.assertRaises(up.UpdateError):
                up.verify_file(p, 12, sha)
            with self.assertRaises(up.UpdateError):
                up.verify_file(p, 11, "0" * 64)
            with self.assertRaises(up.UpdateError):
                up.verify_file(Path(td) / "nope", 0, "")


class SettingsTests(unittest.TestCase):
    def test_opt_out_persists(self):
        with tempfile.TemporaryDirectory() as td:
            u = up.Updater(td, "3.11.0")
            self.assertTrue(u.settings()["checkOnLaunch"])
            u.set_settings(False)
            u2 = up.Updater(td, "3.11.0")
            self.assertFalse(u2.settings()["checkOnLaunch"])
            self.assertEqual(
                u2.status()["mode"], "report-only")


class ManifestFileTests(unittest.TestCase):
    def test_fetch_local_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "latest.json"
            p.write_text(json.dumps(manifest()))
            m = up.fetch_manifest(str(p))
            self.assertEqual(m["version"], "9.9.9")
            m2 = up.fetch_manifest("file://" + str(p))
            self.assertEqual(m2["version"], "9.9.9")


if __name__ == "__main__":
    unittest.main()
