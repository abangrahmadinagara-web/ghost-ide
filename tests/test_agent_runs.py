#!/usr/bin/env python3
"""HTTP integration tests for agent run records and
the diff base override (Desktop Cycle 2, D2) against a
real server subprocess:

- POST /api/agent-runs stores a valid record verbatim
  under .ghostide/agent-runs/<id>.json
- GET /api/agent-runs lists it; GET ?id= fetches it
- a record is data: markup/script-looking content is
  stored and returned untouched, and nothing executes
- bad kind, bad id (incl. traversal), oversize bodies
  and oversized step lists are refused
- a missing record is a 404
- POST /api/diff-text honors an explicit "base"
  (checkpoint original vs current disk content), and
  without one still diffs against disk

The copied-tree setup mirrors tests/test_lsp_server.py
so the repo and the real home dir are never touched.
"""
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def call(port, method, path, body=None, timeout=15,
         raw=None):
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path), method=method)
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data,
                                    timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


class StubLlama(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        out = json.dumps({"status": "ok"}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        out = json.dumps({"choices": [{"message": {
            "role": "assistant", "content": "ok"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


RECORD = {
    "kind": "ghostide-agent-run", "version": 1,
    "id": "run-20261117-010000-test",
    "mode": "act", "task": "edit the thing",
    "planPath": ".ghostide/plans/plan-20261117-005900.md",
    "startedAt": "2026-11-17T01:00:00",
    "endedAt": "2026-11-17T01:00:30",
    "outcome": "done", "summary": "did it",
    "steps": [{"step": 1, "type": "edit", "target": "a.py"}],
    "approvals": [{"kind": "run", "target": "ls",
                   "verdict": "granted"}],
    "filesChanged": [{"path": "a.py", "kind": "modified"}],
    "checkpoint": ".ghostide/checkpoints/ckpt-run-x.json",
    "skipped": [],
}


class AgentRunsCase(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(
            prefix="ghostide-runs-"))
        self.app = self.td / "app"
        self.app.mkdir()
        for f in ("server.py", "ghost_paths.py", "updater.py",
                  "workspace_trust.py", "lsp_manager.py",
                  "debug_bridge.py", "VERSION.json"):
            shutil.copy(REPO / f, self.app / f)
        (self.app / "web").mkdir()
        (self.app / "web" / "index.html").write_text(
            "<html></html>")
        (self.app / "playground").mkdir()
        self.ws = self.td / "ws"
        self.ws.mkdir()
        (self.ws / "a.py").write_text("old line\nkeep\n")
        self.llama_port = free_port()
        self.llama = http.server.ThreadingHTTPServer(
            ("127.0.0.1", self.llama_port), StubLlama)
        threading.Thread(target=self.llama.serve_forever,
                         daemon=True).start()
        self.port = free_port()
        env = dict(os.environ)
        env.update({
            "HOME": str(self.td / "home"),
            "GHOST_IDE_PORT": str(self.port),
            "GHOST_IDE_WORKSPACE": str(self.ws),
            "LLAMA_BASE": "http://127.0.0.1:%d"
                          % self.llama_port,
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        self.proc = subprocess.Popen(
            [sys.executable, str(self.app / "server.py")],
            cwd=str(self.app), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True)
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                st, _ = call(self.port, "GET", "/api/health",
                             timeout=2)
                if st == 200:
                    return
            except Exception:
                pass
            if self.proc.poll() is not None:
                raise AssertionError(
                    "server exited early:\n"
                    + self.proc.stdout.read())
            time.sleep(0.2)
        raise AssertionError("server did not come up")

    def tearDown(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
        self.llama.shutdown()
        shutil.rmtree(self.td, ignore_errors=True)

    def test_record_round_trip_and_list(self):
        st, d = call(self.port, "POST", "/api/agent-runs",
                     RECORD)
        self.assertEqual(st, 200)
        self.assertEqual(
            d["path"], ".ghostide/agent-runs/"
            "run-20261117-010000-test.json")
        on_disk = self.ws / ".ghostide" / "agent-runs" / \
            "run-20261117-010000-test.json"
        self.assertTrue(on_disk.is_file())
        self.assertEqual(
            json.loads(on_disk.read_text())["task"],
            "edit the thing")
        st, d = call(self.port, "GET", "/api/agent-runs")
        self.assertEqual(st, 200)
        self.assertEqual(len(d["runs"]), 1)
        self.assertEqual(d["runs"][0]["outcome"], "done")
        self.assertEqual(d["runs"][0]["files"], 1)
        st, d = call(self.port, "GET",
                     "/api/agent-runs?id="
                     "run-20261117-010000-test")
        self.assertEqual(st, 200)
        self.assertEqual(d["record"]["approvals"][0]
                         ["verdict"], "granted")
        self.assertEqual(d["record"]["steps"][0]["type"],
                         "edit")

    def test_record_is_data_never_executed(self):
        rec = dict(RECORD)
        rec["id"] = "run-20261117-010100-data"
        rec["task"] = "$(touch pwned) `touch pwned2` " \
                      "<script>alert(1)</script>"
        st, _ = call(self.port, "POST", "/api/agent-runs",
                     rec)
        self.assertEqual(st, 200)
        self.assertFalse((self.ws / "pwned").exists())
        self.assertFalse((self.ws / "pwned2").exists())
        st, d = call(self.port, "GET",
                     "/api/agent-runs?id="
                     "run-20261117-010100-data")
        self.assertIn("<script>", d["record"]["task"])

    def test_record_validation(self):
        bad = dict(RECORD)
        bad["kind"] = "something-else"
        st, _ = call(self.port, "POST", "/api/agent-runs",
                     bad)
        self.assertEqual(st, 400)
        bad = dict(RECORD)
        bad["id"] = "../escape"
        st, _ = call(self.port, "POST", "/api/agent-runs",
                     bad)
        self.assertEqual(st, 400)
        self.assertFalse((self.td / "escape.json").exists())
        bad = dict(RECORD)
        bad["id"] = "run-ok"
        bad["steps"] = [{"step": i} for i in range(401)]
        st, _ = call(self.port, "POST", "/api/agent-runs",
                     bad)
        self.assertEqual(st, 400)
        big = json.dumps(dict(RECORD, id="run-big",
                              summary="x" * 300000)).encode()
        st, _ = call(self.port, "POST", "/api/agent-runs",
                     raw=big)
        self.assertEqual(st, 413)
        st, _ = call(self.port, "GET",
                     "/api/agent-runs?id=run-missing")
        self.assertEqual(st, 404)

    def test_diff_text_base_override(self):
        # Disk says "old line"; the checkpoint base says
        # the same, the proposed content changed it: the
        # diff must be base -> content, not disk -> disk.
        st, d = call(self.port, "POST", "/api/diff-text", {
            "path": "a.py", "base": "old line\nkeep\n",
            "content": "new line\nkeep\n"})
        self.assertEqual(st, 200)
        self.assertIn("-old line", d["diff"])
        self.assertIn("+new line", d["diff"])
        # Without a base it still diffs against disk.
        st, d = call(self.port, "POST", "/api/diff-text", {
            "path": "a.py", "content": "old line\nkeep\n"})
        self.assertEqual(d["diff"], "")


if __name__ == "__main__":
    unittest.main()
