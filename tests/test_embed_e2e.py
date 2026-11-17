#!/usr/bin/env python3
"""Real-model retrieval E2E for Desktop Cycle 3 D5.

Runs the shipped server against the shipped fixture
workspace (tests/fixtures/sem) with the REAL embedding
model (nomic-embed-text-v1.5 Q8_0, the pinned asset) on
the REAL pinned llama.cpp. The fixture functions are
written so the test queries share almost no vocabulary
with their targets; the bar is the lexical harness's bar:
each target function must rank in the neural top 3.

Skipped unless the model and a llama-server binary are
findable: GHOSTIDE_EMBED_TEST_MODEL / LLAMA_SERVER_BIN
env, else the sandbox download/tool paths. Lexical
(/api/semsearch) results for the same queries are
printed for comparison but not asserted.
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

QUERIES = [
    ("keep trying again when something fails, waiting "
     "longer each time", "retry.py", "deliver_payload"),
    ("grab a file from the internet and store it on disk",
     "fetchdoc.py", "fetch_remote_document"),
    ("make sure a secret phrase is hard for someone to "
     "guess", "passwords.py", "check_password_strength"),
]


def find_model():
    env = os.environ.get("GHOSTIDE_EMBED_TEST_MODEL")
    if env and Path(env).is_file():
        return Path(env)
    cand = (Path.home() / "workspace" / "downloads"
            / "nomic-embed-text-v1.5.Q8_0.gguf")
    return cand if cand.is_file() else None


def find_llama():
    env = os.environ.get("LLAMA_SERVER_BIN")
    if env and Path(env).is_file():
        return env
    cand = (Path.home() / "workspace" / "tools" / "llama.cpp"
            / "llama-b11429" / "llama-server")
    return str(cand) if cand.is_file() else None


MODEL = find_model()
LLAMA = find_llama()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def call(port, method, path, body=None, timeout=30):
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


@unittest.skipUnless(
    MODEL and LLAMA,
    "real embedding model or llama-server not available")
class RealModelRetrieval(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = Path(tempfile.mkdtemp(prefix="ghostide-d5e2e-"))
        app = cls.td / "app"
        app.mkdir()
        for f in ("server.py", "ghost_paths.py", "updater.py",
                  "workspace_trust.py", "lsp_manager.py",
                  "debug_bridge.py", "embed_index.py",
                  "VERSION.json"):
            shutil.copy(REPO / f, app / f)
        (app / "web").mkdir()
        (app / "web" / "index.html").write_text("<html></html>")
        (app / "playground").mkdir()
        ws = cls.td / "ws"
        shutil.copytree(FIXTURE_WS, ws)
        models = cls.td / "models"
        models.mkdir()
        (models / MODEL.name).symlink_to(MODEL)
        port = free_port()
        env = dict(os.environ)
        env.update({
            "HOME": str(cls.td / "home"),
            "GHOST_IDE_PORT": str(port),
            "GHOST_IDE_WORKSPACE": str(ws),
            "GHOST_IDE_MODELS": str(models),
            "LLAMA_SERVER_BIN": LLAMA,
            "GHOSTIDE_EMBED_BASE":
                "http://127.0.0.1:%d" % free_port(),
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        cls.proc = subprocess.Popen(
            [sys.executable, str(app / "server.py")],
            cwd=str(app), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True)
        cls.port = port
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                st, _ = call(port, "GET", "/api/health",
                             timeout=2)
                if st == 200:
                    break
            except Exception:
                pass
            if cls.proc.poll() is not None:
                raise AssertionError(
                    "server exited early:\n"
                    + cls.proc.stdout.read())
            time.sleep(0.2)
        st, d = call(port, "POST", "/api/embed/build", {})
        assert st == 200, d
        deadline = time.time() + 300
        while time.time() < deadline:
            st, d = call(port, "GET", "/api/embed/status")
            if d["build"]["state"] in ("done", "error"):
                break
            time.sleep(0.5)
        assert d["build"]["state"] == "done", d["build"]
        cls.build_stats = d["build"]["stats"]

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except Exception:
            cls.proc.kill()
        shutil.rmtree(cls.td, ignore_errors=True)

    def test_build_stats(self):
        self.assertEqual(self.build_stats["files"], 7)
        self.assertGreaterEqual(self.build_stats["chunks"],
                                10)
        self.assertEqual(self.build_stats["dim"], 768)

    def test_queries_rank_target_in_top_3(self):
        for query, want_path, want_name in QUERIES:
            st, lex = call(self.port, "POST",
                           "/api/semsearch", {"query": query})
            lex_top = [(r["path"], r["name"])
                       for r in lex.get("results", [])[:3]]
            st, d = call(self.port, "POST",
                         "/api/embed/search",
                         {"query": query, "limit": 5})
            self.assertEqual(st, 200)
            self.assertEqual(d["engine"], "neural")
            top = [(r["path"], r["name"])
                   for r in d["results"][:3]]
            print("\nquery: %s\n  neural top3: %s\n"
                  "  lexical top3: %s"
                  % (query, top, lex_top))
            self.assertIn((want_path, want_name), top,
                          (query, d["results"]))


if __name__ == "__main__":
    unittest.main()
