#!/usr/bin/env python3
"""LSP manager tests (Desktop Cycle 2, D4).

The fake server fixture (tests/fixtures/fake_lsp_server.py)
speaks real framed JSON-RPC, so these prove framing,
lifecycle, the diagnostics round-trip, requests and
crash handling against the actual manager. A separate
class covers trust gating and the TypeScript
unavailable path. Nothing here needs a real language
server or the network.
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import lsp_manager as lsp

FIXTURE = REPO / "tests" / "fixtures" / "fake_lsp_server.py"


def wait_for(pred, timeout=10.0, step=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(step)
    return pred()


class ManagerCase(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="ghostide-lsp-"))
        self.ws = self.td / "ws"
        self.ws.mkdir()
        (self.ws / "a.py").write_text("abc = 1\nprint(abc)\n")
        self.data = self.td / "data"
        self.count_file = self.td / "starts.txt"
        self._env = {}
        self.managers = []

    def tearDown(self):
        for m in self.managers:
            m.shutdown_all()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def set_env(self, **kw):
        for k, v in kw.items():
            self._env[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def make_manager(self, trusted=True, **kw):
        kw.setdefault("command_overrides",
                      {"python": [sys.executable, str(FIXTURE)]})
        kw.setdefault("environ", {})
        m = lsp.LspManager(
            self.data, workspace_fn=lambda: self.ws,
            trusted_fn=lambda: trusted,
            python_cmd=lambda: [sys.executable], **kw)
        self.managers.append(m)
        return m

    def starts(self):
        try:
            return len(self.count_file.read_text().splitlines())
        except OSError:
            return 0


class FakeServerTests(ManagerCase):
    def setUp(self):
        super().setUp()
        self.set_env(FAKE_LSP_COUNT_FILE=str(self.count_file))

    def test_lifecycle_diagnostics_and_requests(self):
        m = self.make_manager()
        m.open_document("a.py", "abc = 1\nprint(abc)\n")
        # Framing + lifecycle: the server started once and
        # reached ready.
        self.assertEqual(self.starts(), 1)
        st = m.status()["servers"]["python"]
        self.assertEqual(st["state"], "ready")
        self.assertEqual(st["documents"], 1)
        # Diagnostics round-trip: didOpen in, a published
        # diagnostic back out, keyed by relative path.
        self.assertTrue(wait_for(
            lambda: bool(m.diagnostics("a.py")["a.py"])))
        diags = m.diagnostics("a.py")["a.py"]
        self.assertEqual(diags[0]["message"], "fake diagnostic")
        self.assertEqual(diags[0]["source"], "fake-lsp")
        # Requests over the same connection.
        hov = m.hover("a.py", 0, 1)
        self.assertEqual(hov["contents"]["value"],
                         "fake hover text")
        dfn = m.definition("a.py", 1, 7)
        self.assertEqual(dfn[0]["range"]["start"]["line"], 0)
        comp = m.completion("a.py", 0, 3)
        labels = [i["label"] for i in comp["items"]]
        self.assertEqual(labels, ["fake_alpha", "fake_beta"])
        syms = m.document_symbols("a.py")
        self.assertEqual(syms[0]["name"], "fake_symbol")
        # Full-document change + save + close do not error
        # and the document set empties on close.
        m.change_document("a.py", "abc = 2\n")
        m.save_document("a.py")
        m.close_document("a.py")
        st = m.status()["servers"]["python"]
        self.assertEqual(st["documents"], 0)
        self.assertEqual(m.diagnostics("a.py")["a.py"], [])

    def test_crash_restart_bounded(self):
        self.set_env(FAKE_LSP_DIE_AFTER_INIT="1")
        m = self.make_manager()
        raised = None
        for _ in range(8):
            try:
                m.open_document("a.py", "abc = 1\n")
            except lsp.LspError as e:
                raised = e
                break
            # Let the death land before the next attempt.
            wait_for(lambda: m.status()["servers"]["python"]
                     ["state"] == "crashed", timeout=3)
        self.assertIsNotNone(raised)
        self.assertEqual(raised.code, "lsp-crashed")
        # It really did respawn between attempts.
        self.assertGreaterEqual(self.starts(), 3)
        # A manual restart clears the crash budget.
        m.restart("python")
        st = m.status()["servers"]["python"]
        self.assertEqual(st["restarts"], 0)


class GateTests(ManagerCase):
    def test_trust_gate_blocks_spawn(self):
        self.set_env(FAKE_LSP_COUNT_FILE=str(self.count_file))
        m = self.make_manager(trusted=False)
        with self.assertRaises(lsp.LspTrustError) as cm:
            m.open_document("a.py", "abc = 1\n")
        self.assertEqual(cm.exception.code,
                         "workspace-untrusted")
        with self.assertRaises(lsp.LspTrustError):
            m.hover("a.py", 0, 0)
        # The process never started.
        self.assertEqual(self.starts(), 0)

    def test_typescript_unavailable_without_node(self):
        m = self.make_manager(which=lambda name: None)
        st = m.status()["servers"]["typescript"]
        self.assertEqual(st["state"], "unavailable")
        self.assertIn("Node.js", st["detail"])
        self.assertTrue(st["needsNode"])
        with self.assertRaises(lsp.LspError) as cm:
            m.open_document("a.ts", "let x = 1;\n")
        self.assertEqual(cm.exception.code, "lsp-unavailable")

    def test_unsupported_file_type(self):
        m = self.make_manager()
        with self.assertRaises(lsp.LspError) as cm:
            m.hover("notes.txt", 0, 0)
        self.assertEqual(cm.exception.code, "lsp-unsupported")


class PinTests(unittest.TestCase):
    def test_jedi_pins_are_complete_and_hashed(self):
        names = {row[0] for row in lsp.JEDI_WHEELS}
        self.assertIn("jedi-language-server", names)
        self.assertIn("jedi", names)
        self.assertIn("pygls", names)
        for name, ver, fname, sha in lsp.JEDI_WHEELS:
            self.assertEqual(len(sha), 64, name)
            int(sha, 16)  # raises unless hex
            self.assertIn(ver, fname, name)
            if name == "jedi-language-server":
                self.assertEqual(ver, lsp.JEDI_VERSION)

    def test_language_map(self):
        self.assertEqual(lsp.language_for_path("x.py"),
                         ("python", "python"))
        self.assertEqual(lsp.language_for_path("x.tsx"),
                         ("typescript", "typescriptreact"))
        self.assertIsNone(lsp.language_for_path("x.md"))


if __name__ == "__main__":
    unittest.main()
