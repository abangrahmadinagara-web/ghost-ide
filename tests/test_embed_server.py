#!/usr/bin/env python3
"""Integration tests for Desktop Cycle 3 D5 against a
real server subprocess (a copied tree in a temp dir):

- status is honest when the embedding model is absent
  (present: false, available: false) and search answers
  with code "no-model" instead of failing
- with a model file present and a fake llama-server at
  the engine seam (tests/fixtures/fake_embed_server.py,
  deterministic concept vectors), the full pipeline runs
  over HTTP: build embeds the fixture workspace, marker
  queries rank the right file first, a second build
  embeds nothing, an edit re-embeds one file, a delete
  purges one file
- the build and search routes are trust-gated: after
  switching to an untrusted folder both are 403 while
  the status GET stays open
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURE_WS = REPO / "tests" / "fixtures" / "sem"
FAKE_BIN = REPO / "tests" / "fixtures" / "fake_embed_server.py"
MODEL_BYTES = 146146432


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def call(port, method, path, body=None, timeout=15):
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path), method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data,
                                    timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


class EmbedServerCase(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="ghostide-d5-"))
        self.app = self.td / "app"
        self.app.mkdir()
        for f in ("server.py", "ghost_paths.py", "updater.py",
                  "workspace_trust.py", "lsp_manager.py",
                  "debug_bridge.py", "embed_index.py",
                  "VERSION.json"):
            shutil.copy(REPO / f, self.app / f)
        (self.app / "web").mkdir()
        (self.app / "web" / "index.html").write_text(
            "<html></html>")
        (self.app / "playground").mkdir()
        (self.app / "playground" / "hello.txt").write_text(
            "seeded")
        self.ws = self.td / "ws"
        shutil.copytree(FIXTURE_WS, self.ws)
        self.models = self.td / "models"
        self.models.mkdir()
        self.embed_port = free_port()
        self.proc = None

    def tearDown(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
        shutil.rmtree(self.td, ignore_errors=True)

    def place_model(self):
        dest = self.models / "nomic-embed-text-v1.5.Q8_0.gguf"
        with open(dest, "wb") as f:
            f.truncate(MODEL_BYTES)

    def start_server(self, with_model):
        if with_model:
            self.place_model()
        port = free_port()
        env = dict(os.environ)
        env.update({
            "HOME": str(self.td / "home"),
            "GHOST_IDE_PORT": str(port),
            "GHOST_IDE_WORKSPACE": str(self.ws),
            "GHOST_IDE_MODELS": str(self.models),
            "LLAMA_SERVER_BIN": str(FAKE_BIN),
            "GHOSTIDE_EMBED_BASE":
                "http://127.0.0.1:%d" % self.embed_port,
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        os.chmod(FAKE_BIN, 0o755)
        self.proc = subprocess.Popen(
            [sys.executable, str(self.app / "server.py")],
            cwd=str(self.app), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True)
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                st, _ = call(port, "GET", "/api/health",
                             timeout=2)
                if st == 200:
                    return port
            except Exception:
                pass
            if self.proc.poll() is not None:
                raise AssertionError(
                    "server exited early:\n"
                    + self.proc.stdout.read())
            time.sleep(0.2)
        raise AssertionError("server did not come up")

    def build_and_wait(self, port):
        st, d = call(port, "POST", "/api/embed/build", {})
        self.assertEqual(st, 200, d)
        deadline = time.time() + 60
        while time.time() < deadline:
            st, d = call(port, "GET", "/api/embed/status")
            if d["build"]["state"] in ("done", "error"):
                return d
            time.sleep(0.25)
        self.fail("build did not finish")

    def test_status_honest_without_model(self):
        port = self.start_server(with_model=False)
        st, d = call(port, "GET", "/api/embed/status")
        self.assertEqual(st, 200)
        self.assertFalse(d["model"]["present"])
        self.assertFalse(d["available"])
        self.assertEqual(d["index"]["chunks"], 0)
        st, d = call(port, "POST", "/api/embed/search",
                     {"query": "anything"})
        self.assertEqual(st, 200)
        self.assertFalse(d["available"])
        self.assertEqual(d["code"], "no-model")
        st, d = call(port, "POST", "/api/embed/build", {})
        self.assertEqual(st, 400)
        self.assertIn("not downloaded", d["error"])

    def test_pipeline_with_fake_engine(self):
        port = self.start_server(with_model=True)
        st, d = call(port, "GET", "/api/embed/status")
        self.assertTrue(d["model"]["present"])
        d = self.build_and_wait(port)
        self.assertEqual(d["build"]["state"], "done",
                         d["build"])
        stats = d["build"]["stats"]
        self.assertEqual(stats["files"], 7)
        self.assertGreaterEqual(stats["chunks"], 10)
        self.assertEqual(stats["embedded_files"], 7)
        self.assertEqual(d["index"]["skipped"], 0)
        self.assertTrue(d["available"])
        # Marker queries rank the matching fixture file
        # first through the real HTTP pipeline.
        for query, want in (("sleep", "retry.py"),
                            ("urlopen", "fetchdoc.py"),
                            ("digit", "passwords.py")):
            st, d = call(port, "POST", "/api/embed/search",
                         {"query": query})
            self.assertEqual(st, 200)
            self.assertEqual(d["engine"], "neural")
            self.assertTrue(d["results"])
            self.assertEqual(d["results"][0]["path"], want,
                             (query, d["results"][:3]))
        # A second build embeds nothing.
        d = self.build_and_wait(port)
        self.assertEqual(d["build"]["stats"]
                         ["embedded_files"], 0)
        self.assertEqual(d["build"]["stats"]
                         ["unchanged_files"], 7)
        # An edit re-embeds exactly that file.
        target = self.ws / "dates.py"
        target.write_text(target.read_text()
                          + "\n# a comment\n")
        d = self.build_and_wait(port)
        self.assertEqual(d["build"]["stats"]
                         ["embedded_files"], 1)
        # A delete purges exactly that file.
        (self.ws / "dates.py").unlink()
        d = self.build_and_wait(port)
        self.assertEqual(d["build"]["stats"]
                         ["removed_files"], 1)
        self.assertEqual(d["index"]["files"], 6)

    def test_trust_gate(self):
        port = self.start_server(with_model=True)
        other = self.td / "other"
        other.mkdir()
        (other / "x.py").write_text("x = 1\n")
        st, d = call(port, "POST", "/api/workspace",
                     {"path": str(other)})
        self.assertEqual(st, 200, d)
        st, d = call(port, "POST", "/api/embed/build", {})
        self.assertEqual(st, 403)
        self.assertEqual(d["code"], "workspace-untrusted")
        st, d = call(port, "POST", "/api/embed/search",
                     {"query": "sleep"})
        self.assertEqual(st, 403)
        st, d = call(port, "GET", "/api/embed/status")
        self.assertEqual(st, 200)


if __name__ == "__main__":
    unittest.main()
