#!/usr/bin/env python3
"""Language Server Protocol support (Desktop Cycle 2, D4).

One language server process per (workspace, language),
spoken to over stdio with standard LSP Content-Length
framing. The web UI reaches the manager through plain
HTTP endpoints on server.py and polls for diagnostics
and status, the same bridge pattern the rest of the IDE
uses (health, pair mode, downloads): this codebase has
no WebSocket/SSE channel, and LSP over HTTP+poll is
enough for diagnostics, hover, definition and
completion at editor scale.

Server choices, and why:

- Python: jedi-language-server 0.47.0 (pure Python, MIT).
  It runs on a Python interpreter, so GhostIDE needs no
  Node.js for its primary language. Pyright would give
  stronger type diagnostics but its language server is
  a Node program; on machines without Node it cannot
  run at all. Jedi's limits are stated plainly: its
  diagnostics are essentially syntax errors, not type
  errors. The manager is spec-driven, so a Pyright spec
  can be added later for machines that have Node.
  Jedi is NOT bundled: on first use its pinned wheels
  (exact versions + SHA-256, below) are downloaded from
  PyPI into a venv under the app data dir (ghost_paths,
  so portable mode carries it too), and pip installs
  them with --require-hashes. Nothing is installed
  globally and nothing is downloaded silently: the UI
  shows "not installed" and the user (or the first
  Python file open, via the status chip) triggers it.

- TypeScript/JavaScript: typescript-language-server
  6.1.0 + typescript 5.9.3, installed with `npm ci`
  from a pinned package-lock (integrity hashes in the
  lockfile) into the app data dir. This one genuinely
  needs Node.js + npm on the machine; without them the
  status is "unavailable" with that reason, never a
  silent failure. Node is never bundled.

Trust: language servers are spawned processes, so the
manager refuses to start one while the workspace is
untrusted (the caller passes the same trust check the
API layer enforces; server.py also gates the routes).

Sync: the client sends full-document didChange events
(a change event with no range is a whole-document
replacement in the LSP spec, whatever the server's
advertised sync kind), which keeps one code path for
every server.

Test hook: GHOSTIDE_LSP_PYTHON_CMD / a constructor
command override lets the test suite substitute a fake
server without touching installs.
"""
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

# ------------------------------------------------------------------
# Pinned servers
# ------------------------------------------------------------------

JEDI_VERSION = "0.47.0"
# (distribution, version, wheel filename, wheel sha256).
# All wheels are py3-none-any (platform independent),
# captured from PyPI at pin time (Desktop Cycle 2).
JEDI_WHEELS = [
    ("attrs", "26.1.0", "attrs-26.1.0-py3-none-any.whl",
     "c647aa4a12dfbad9333ca4e71fe62ddc36f4e63b2d260a37a8b83d2f043ac309"),
    ("cattrs", "26.2.1", "cattrs-26.2.1-py3-none-any.whl",
     "a12aaa3453dc8f633a815293179f08b7421ed18d2575c459c3c736f840beac24"),
    ("docstring-to-markdown", "0.17",
     "docstring_to_markdown-0.17-py3-none-any.whl",
     "fd7d5094aa83943bf5f9e1a13701866b7c452eac19765380dead666e36d3711c"),
    ("importlib_metadata", "10.2.0",
     "importlib_metadata-10.2.0-py3-none-any.whl",
     "c7a4bf1c7479601f6b8e437cfc14f4f77028ba62a8f34ae95cd93d9774dfba51"),
    ("jedi", "0.20.1", "jedi-0.20.1-py2.py3-none-any.whl",
     "0fb16d86c4a4c73c37ba518c77419975e30fcc620658a8d14fbb5720cdd34142"),
    ("jedi-language-server", "0.47.0",
     "jedi_language_server-0.47.0-py3-none-any.whl",
     "0e00d5af41c1a966c9ec73221e81b1af3b8c0243d29477e5441c661d5b61ec3e"),
    ("lazy-importer", "0.1.0", "lazy_importer-0.1.0-py3-none-any.whl",
     "bd93fab7bef18e842eea02b839410954d37889140dc64ca4d9680aec6880c739"),
    ("lsprotocol", "2025.0.0", "lsprotocol-2025.0.0-py3-none-any.whl",
     "f9d78f25221f2a60eaa4a96d3b4ffae011b107537facee61d3da3313880995c7"),
    ("parso", "0.8.7", "parso-0.8.7-py2.py3-none-any.whl",
     "a8926eb2a1b915486941fdbd31e86a4baf88fe8c210f25f2f35ecec5b574ca1c"),
    ("pygls", "2.1.1", "pygls-2.1.1-py3-none-any.whl",
     "510a6dea2476177230c7d851125e5948efdf3fdb9ebfd8543fc434972f8faed4"),
    ("typing_extensions", "4.16.0",
     "typing_extensions-4.16.0-py3-none-any.whl",
     "481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8"),
    ("zipp", "4.1.2", "zipp-4.1.2-py3-none-any.whl",
     "b30cb6cd253122169c47de245ca24a86bac2a5d3802ba15bf7ba6414eb9e144d"),
]

TS_SERVER_VERSION = "6.1.0"
TS_VERSION = "5.9.3"
# package.json + package-lock.json for `npm ci`; the
# lockfile carries the registry integrity hashes for
# both packages (typescript-language-server has no
# runtime dependencies beyond typescript itself).
TS_PACKAGE_JSON = {
    "name": "ghostide-lsp-ts",
    "version": "1.0.0",
    "private": True,
    "dependencies": {
        "typescript": TS_VERSION,
        "typescript-language-server": TS_SERVER_VERSION,
    },
}
TS_PACKAGE_LOCK = {
    "name": "ghostide-lsp-ts",
    "version": "1.0.0",
    "lockfileVersion": 3,
    "requires": True,
    "packages": {
        "": {
            "name": "ghostide-lsp-ts",
            "version": "1.0.0",
            "dependencies": {
                "typescript": TS_VERSION,
                "typescript-language-server": TS_SERVER_VERSION,
            },
        },
        "node_modules/typescript": {
            "version": TS_VERSION,
            "resolved": "https://registry.npmjs.org/typescript/-/"
                        "typescript-5.9.3.tgz",
            "integrity": "sha512-jl1vZzPDinLr9eUt3J/t7V6FgNEw9QjvBPdysz"
                         "9KfQDD41fQrC2Y4vKQdiaUpFT4bXlb1RHhLpp8wtm6M5TgSw==",
            "bin": {"tsc": "bin/tsc", "tsserver": "bin/tsserver"},
            "engines": {"node": ">=14.17"},
        },
        "node_modules/typescript-language-server": {
            "version": TS_SERVER_VERSION,
            "resolved": "https://registry.npmjs.org/"
                        "typescript-language-server/-/"
                        "typescript-language-server-6.1.0.tgz",
            "integrity": "sha512-/O/dr2CbvDS1W9jKJcBQxk4WagUQ97oBb8k/eq9c"
                         "OslsbtAuC2BDPpkcULxE2SEZiPIJvVNLeNjLZe9hQl1zCQ==",
            "bin": {"typescript-language-server":
                    "lib/cli.mjs"},
            "engines": {"node": ">=18"},
        },
    },
}

# File extension -> (server key, LSP languageId)
LANG_BY_EXT = {
    ".py": ("python", "python"),
    ".js": ("typescript", "javascript"),
    ".mjs": ("typescript", "javascript"),
    ".cjs": ("typescript", "javascript"),
    ".jsx": ("typescript", "javascriptreact"),
    ".ts": ("typescript", "typescript"),
    ".mts": ("typescript", "typescript"),
    ".cts": ("typescript", "typescript"),
    ".tsx": ("typescript", "typescriptreact"),
}

SERVER_NAMES = {
    "python": "jedi-language-server %s (Jedi)" % JEDI_VERSION,
    "typescript": "typescript-language-server %s" % TS_SERVER_VERSION,
}

MAX_RESTARTS = 3
# 30s, not less: the first request into a freshly
# installed Jedi venv pays bytecode compilation plus
# Jedi's first inference, which measured over 20s once
# in testing (0.5s warm). The client treats every LSP
# answer as best-effort and drops stale ones.
REQUEST_TIMEOUT = 30.0
INIT_TIMEOUT = 45.0


def language_for_path(path):
    """(server key, languageId) for a file path, or None."""
    return LANG_BY_EXT.get(Path(str(path)).suffix.lower())


def path_to_uri(path):
    return Path(path).resolve().as_uri()


def uri_to_path(uri):
    if not uri or not uri.startswith("file://"):
        return None
    p = urllib.parse.urlparse(uri)
    return str(Path(urllib.parse.unquote(p.path)))


class LspError(Exception):
    def __init__(self, message, code="lsp-error"):
        super().__init__(message)
        self.code = code


class LspTrustError(LspError):
    def __init__(self):
        super().__init__(
            "workspace is not trusted: language servers "
            "do not start until you trust the folder",
            code="workspace-untrusted")


# ------------------------------------------------------------------
# JSON-RPC over stdio
# ------------------------------------------------------------------

class _Pending:
    __slots__ = ("event", "result", "error")

    def __init__(self):
        self.event = threading.Event()
        self.result = None
        self.error = None


class JsonRpcConnection:
    """One language server process, framed JSON-RPC.

    A reader thread owns stdout: responses complete
    pending requests, notifications go to the callback,
    and server-to-client requests get the small set of
    generic answers below (configuration, work-done
    progress) so real servers do not stall waiting.
    """

    def __init__(self, argv, cwd, on_notification, on_exit):
        self.argv = argv
        self.proc = subprocess.Popen(
            argv, cwd=str(cwd),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL)
        self._on_notification = on_notification
        self._on_exit = on_exit
        self._pending = {}
        self._lock = threading.Lock()
        self._next_id = 0
        self.alive = True
        self._reader = threading.Thread(
            target=self._read_loop, daemon=True)
        self._reader.start()

    # -- framing ------------------------------------------------
    def _read_loop(self):
        stream = self.proc.stdout
        try:
            while True:
                length = None
                while True:
                    line = stream.readline()
                    if not line:
                        raise EOFError("server closed stdout")
                    line = line.strip()
                    if not line:
                        break
                    if line.lower().startswith(b"content-length:"):
                        length = int(line.split(b":", 1)[1])
                if length is None:
                    continue
                body = b""
                while len(body) < length:
                    chunk = stream.read(length - len(body))
                    if not chunk:
                        raise EOFError("server closed mid-body")
                    body += chunk
                self._dispatch(json.loads(body.decode("utf-8")))
        except Exception:
            pass
        finally:
            self.alive = False
            with self._lock:
                pending = list(self._pending.values())
                self._pending.clear()
            for p in pending:
                p.error = LspError("language server exited")
                p.event.set()
            self._on_exit()

    def _dispatch(self, msg):
        if not isinstance(msg, dict):
            return
        mid = msg.get("id")
        method = msg.get("method")
        if method is not None and mid is not None:
            # A request FROM the server.
            self._answer_server_request(mid, method,
                                        msg.get("params"))
        elif method is not None:
            self._on_notification(method, msg.get("params"))
        elif mid is not None:
            with self._lock:
                p = self._pending.pop(mid, None)
            if p is not None:
                if "error" in msg and msg["error"]:
                    p.error = LspError(str(
                        msg["error"].get("message", msg["error"])))
                else:
                    p.result = msg.get("result")
                p.event.set()

    def _answer_server_request(self, mid, method, params):
        result = None
        if method == "workspace/configuration":
            items = (params or {}).get("items") or []
            result = [{} for _ in items] or [{}]
        elif method == "workspace/workspaceFolders":
            result = None  # answered from initialize state
        # window/workDoneProgress/create, client/register
        # Capability and everything else: a null result is
        # a valid "done / not supported" answer.
        self._send({"jsonrpc": "2.0", "id": mid,
                    "result": result})

    def _send(self, obj):
        body = json.dumps(obj).encode("utf-8")
        frame = (b"Content-Length: %d\r\n\r\n" % len(body)) + body
        try:
            self.proc.stdin.write(frame)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self.alive = False
            raise LspError("language server is not running")

    # -- public ---------------------------------------------------
    def request(self, method, params, timeout=REQUEST_TIMEOUT):
        if not self.alive:
            raise LspError("language server is not running")
        with self._lock:
            self._next_id += 1
            mid = self._next_id
            p = _Pending()
            self._pending[mid] = p
        try:
            self._send({"jsonrpc": "2.0", "id": mid,
                        "method": method, "params": params})
        except LspError:
            with self._lock:
                self._pending.pop(mid, None)
            raise
        if not p.event.wait(timeout):
            with self._lock:
                self._pending.pop(mid, None)
            raise LspError("language server did not answer "
                           "%s in time" % method)
        if p.error is not None:
            raise p.error
        return p.result

    def notify(self, method, params):
        if self.alive:
            try:
                self._send({"jsonrpc": "2.0", "method": method,
                            "params": params})
            except LspError:
                pass

    def close(self, graceful=True):
        if graceful and self.alive:
            try:
                self.request("shutdown", None, timeout=5)
            except Exception:
                pass
            self.notify("exit", None)
        try:
            self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.alive = False


# ------------------------------------------------------------------
# One server process
# ------------------------------------------------------------------

class _Server:
    def __init__(self, key):
        self.key = key
        self.conn = None
        self.state = "stopped"  # starting/ready/crashed
        self.capabilities = {}
        self.documents = {}     # uri -> {"version", "languageId"}
        self.diagnostics = {}   # uri -> [diagnostic, ...]
        self.restarts = 0
        self.last_error = ""
        self.lock = threading.RLock()


# ------------------------------------------------------------------
# The manager
# ------------------------------------------------------------------

class LspManager:
    def __init__(self, data_dir, workspace_fn, trusted_fn,
                 python_cmd=None, which=None,
                 command_overrides=None, environ=None):
        """data_dir: app data dir (ghost_paths.data_dir).
        workspace_fn/trusted_fn: live callables, so a
        workspace switch or trust change is honoured
        without rebuilding the manager. python_cmd:
        callable returning the interpreter argv prefix
        (server.py passes its py_cmd). command_overrides:
        {server key: argv} test hook that also marks the
        server as installed."""
        self.data_dir = Path(data_dir)
        self._workspace_fn = workspace_fn
        self._trusted_fn = trusted_fn
        self._python_cmd = python_cmd or (lambda: [sys.executable])
        self._which = which or shutil.which
        self._overrides = dict(command_overrides or {})
        env = environ if environ is not None else os.environ
        fake = env.get("GHOSTIDE_LSP_PYTHON_CMD", "").strip()
        if fake and "python" not in self._overrides:
            self._overrides["python"] = shlex.split(fake)
        self.root = self.data_dir / "lsp"
        self._servers = {k: _Server(k) for k in SERVER_NAMES}
        self._installing = {}   # key -> {"state", "error"}
        self._install_lock = threading.Lock()

    # -- paths ----------------------------------------------------
    @property
    def workspace(self):
        return Path(self._workspace_fn()).resolve()

    def _venv_dir(self):
        return self.root / "venv"

    def _venv_python(self):
        if os.name == "nt":
            cand = self._venv_dir() / "Scripts" / "python.exe"
            exe = self._venv_dir() / "Scripts" / \
                "jedi-language-server.exe"
        else:
            cand = self._venv_dir() / "bin" / "python"
            exe = self._venv_dir() / "bin" / "jedi-language-server"
        return cand, exe

    def _ts_dir(self):
        return self.root / "ts"

    # -- install state ---------------------------------------------
    def _marker(self, name):
        try:
            return json.loads(
                (self.root / name).read_text(encoding="utf-8"))
        except Exception:
            return None

    def _write_marker(self, name, data):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / name).write_text(
            json.dumps(data, indent=1), encoding="utf-8")

    def python_installed(self):
        if "python" in self._overrides:
            return True
        marker = self._marker("python-server.json")
        if not marker or marker.get("version") != JEDI_VERSION:
            return False
        _, exe = self._venv_python()
        return exe.is_file()

    def typescript_installed(self):
        if "typescript" in self._overrides:
            return True
        marker = self._marker("ts-server.json")
        if not marker or \
                marker.get("server") != TS_SERVER_VERSION:
            return False
        return (self._ts_dir() / "node_modules" /
                "typescript-language-server" / "lib" /
                "cli.mjs").is_file()

    def _unavailable_reason(self, key):
        if key == "python":
            base = self._python_cmd()[0]
            if not base or not Path(str(base)).exists() \
                    and not self._which(str(base)):
                return ("no Python interpreter found to run "
                        "the language server venv")
            return ""
        if key == "typescript":
            if not self._which("node"):
                return ("TypeScript language server needs "
                        "Node.js, which was not found on PATH")
            if not self._which("npm"):
                return ("TypeScript language server needs "
                        "npm, which was not found on PATH")
            return ""
        return ""

    # -- installers --------------------------------------------------
    def install(self, key):
        """Start (or report) installation of a server.
        Returns the status dict for that server."""
        with self._install_lock:
            cur = self._installing.get(key)
            if cur and cur.get("state") == "installing":
                return self.status()
            reason = self._unavailable_reason(key)
            installed = (self.python_installed()
                         if key == "python"
                         else self.typescript_installed())
            if installed or reason:
                return self.status()
            self._installing[key] = {"state": "installing",
                                     "error": ""}
        t = threading.Thread(target=self._install_worker,
                             args=(key,), daemon=True)
        t.start()
        return self.status()

    def _install_worker(self, key):
        try:
            if key == "python":
                self._install_python()
            else:
                self._install_typescript()
            with self._install_lock:
                self._installing[key] = {"state": "done",
                                         "error": ""}
        except Exception as e:
            with self._install_lock:
                self._installing[key] = {"state": "failed",
                                         "error": str(e)}

    def _install_python(self):
        base = self._python_cmd()[0]
        venv = self._venv_dir()
        py, exe = self._venv_python()
        if venv.exists():
            shutil.rmtree(venv, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            [base, "-m", "venv", str(venv)],
            capture_output=True, text=True, timeout=300)
        if r.returncode != 0 or not py.exists():
            raise LspError(
                "creating the language server venv failed: "
                + (r.stderr or r.stdout or "")[-400:])
        req = self.root / "jedi-requirements.txt"
        lines = []
        for name, ver, _fname, sha in JEDI_WHEELS:
            lines.append("%s==%s --hash=sha256:%s"
                         % (name, ver, sha))
        req.write_text("\n".join(lines) + "\n",
                       encoding="utf-8")
        pip = venv / ("Scripts" if os.name == "nt"
                      else "bin") / ("pip.exe" if os.name == "nt"
                                     else "pip")
        r = subprocess.run(
            [str(pip), "install", "--require-hashes",
             "--no-input", "--disable-pip-version-check",
             "-r", str(req)],
            capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            raise LspError(
                "installing jedi-language-server failed: "
                + (r.stderr or r.stdout or "")[-400:])
        check = subprocess.run(
            [str(py), "-c",
             "import importlib.metadata, sys; "
             "sys.stdout.write(importlib.metadata.version"
             "('jedi-language-server'))"],
            capture_output=True, text=True, timeout=60)
        if check.stdout.strip() != JEDI_VERSION \
                or not exe.is_file():
            raise LspError(
                "jedi-language-server install did not "
                "verify (got %r)" % check.stdout.strip())
        self._write_marker("python-server.json", {
            "server": "jedi-language-server",
            "version": JEDI_VERSION,
            "installed": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        })

    def _install_typescript(self):
        ts = self._ts_dir()
        ts.mkdir(parents=True, exist_ok=True)
        (ts / "package.json").write_text(
            json.dumps(TS_PACKAGE_JSON, indent=1),
            encoding="utf-8")
        (ts / "package-lock.json").write_text(
            json.dumps(TS_PACKAGE_LOCK, indent=1),
            encoding="utf-8")
        npm = self._which("npm")
        r = subprocess.run(
            [npm, "ci", "--no-audit", "--no-fund",
             "--loglevel=error"],
            cwd=str(ts), capture_output=True, text=True,
            timeout=900)
        if r.returncode != 0:
            raise LspError(
                "installing typescript-language-server "
                "failed: " + (r.stderr or r.stdout or "")[-400:])
        cli = ts / "node_modules" / \
            "typescript-language-server" / "lib" / "cli.mjs"
        if not cli.is_file():
            raise LspError(
                "typescript-language-server install did "
                "not verify (cli.mjs missing)")
        self._write_marker("ts-server.json", {
            "server": TS_SERVER_VERSION,
            "typescript": TS_VERSION,
            "installed": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        })

    # -- commands ------------------------------------------------------
    def _command(self, key):
        if key in self._overrides:
            return list(self._overrides[key])
        if key == "python":
            _, exe = self._venv_python()
            return [str(exe)]
        node = self._which("node")
        cli = self._ts_dir() / "node_modules" / \
            "typescript-language-server" / "lib" / "cli.mjs"
        return [node, str(cli), "--stdio"]

    # -- lifecycle -------------------------------------------------------
    def _on_notification(self, srv, method, params):
        if method == "textDocument/publishDiagnostics" \
                and isinstance(params, dict):
            uri = params.get("uri", "")
            with srv.lock:
                srv.diagnostics[uri] = \
                    params.get("diagnostics") or []

    def _on_exit(self, srv):
        with srv.lock:
            if srv.state == "ready":
                srv.state = "crashed"
                srv.restarts += 1
                srv.last_error = "language server exited"

    def ensure(self, key):
        """A ready server for `key`, spawning if needed.
        Raises LspTrustError / LspError with a status-ish
        code the API layer can surface honestly."""
        if not self._trusted_fn():
            raise LspTrustError()
        srv = self._servers[key]
        with srv.lock:
            if srv.conn is not None and srv.conn.alive \
                    and srv.state == "ready":
                return srv
            installed = (self.python_installed()
                         if key == "python"
                         else self.typescript_installed())
            if not installed:
                reason = self._unavailable_reason(key)
                if reason:
                    raise LspError(reason,
                                   code="lsp-unavailable")
                self.install(key)
                raise LspError(
                    "%s is installing; try again in a "
                    "moment" % SERVER_NAMES[key],
                    code="lsp-installing")
            if srv.state == "crashed" \
                    and srv.restarts >= MAX_RESTARTS:
                raise LspError(
                    "%s crashed %d times; restart it from "
                    "the LSP status chip"
                    % (SERVER_NAMES[key], srv.restarts),
                    code="lsp-crashed")
            self._spawn(srv)
            return srv

    def _spawn(self, srv):
        ws = self.workspace
        old = srv.conn
        if old is not None:
            try:
                old.close(graceful=False)
            except Exception:
                pass
        srv.state = "starting"
        conn = JsonRpcConnection(
            self._command(srv.key), cwd=ws,
            on_notification=lambda m, p: self._on_notification(
                srv, m, p),
            on_exit=lambda: self._on_exit(srv))
        srv.conn = conn
        try:
            result = conn.request("initialize", {
                "processId": os.getpid(),
                "rootUri": path_to_uri(ws),
                "capabilities": {
                    "textDocument": {
                        "publishDiagnostics": {
                            "relatedInformation": True},
                        "hover": {"contentFormat":
                                  ["markdown", "plaintext"]},
                        "definition": {"linkSupport": True},
                        "completion": {
                            "completionItem": {
                                "snippetSupport": False,
                                "documentationFormat":
                                    ["markdown", "plaintext"]}},
                        "documentSymbol": {
                            "hierarchicalDocumentSymbolSupport":
                                True},
                    },
                    "workspace": {"configuration": True,
                                  "workspaceFolders": True},
                },
                "workspaceFolders": [{
                    "uri": path_to_uri(ws),
                    "name": ws.name or "workspace"}],
            }, timeout=INIT_TIMEOUT)
        except Exception as e:
            srv.state = "crashed"
            srv.restarts += 1
            srv.last_error = str(e)
            try:
                conn.close(graceful=False)
            except Exception:
                pass
            srv.conn = None
            raise LspError("language server failed to "
                           "start: %s" % e, code="lsp-crashed")
        srv.capabilities = (result or {}).get(
            "capabilities") or {}
        conn.notify("initialized", {})
        srv.state = "ready"
        srv.last_error = ""
        # Re-open documents the client still has open.
        docs = dict(srv.documents)
        srv.documents = {}
        for uri, doc in docs.items():
            srv.documents[uri] = {"version": doc["version"],
                                  "languageId":
                                      doc["languageId"],
                                  "text": doc["text"]}
            conn.notify("textDocument/didOpen", {
                "textDocument": {
                    "uri": uri,
                    "languageId": doc["languageId"],
                    "version": doc["version"],
                    "text": doc["text"]}})

    def restart(self, key):
        srv = self._servers[key]
        with srv.lock:
            srv.restarts = 0
            conn = srv.conn
            srv.conn = None
            srv.state = "stopped"
            srv.last_error = ""
            if conn is not None:
                try:
                    conn.close(graceful=False)
                except Exception:
                    pass
        return self.status()

    def shutdown_all(self):
        for srv in self._servers.values():
            with srv.lock:
                conn = srv.conn
                srv.conn = None
                srv.state = "stopped"
                if conn is not None:
                    try:
                        conn.close(graceful=True)
                    except Exception:
                        pass

    # -- documents -----------------------------------------------------
    def _doc(self, srv, path, language_id, text=None):
        uri = path_to_uri(self.workspace / path)
        doc = srv.documents.get(uri)
        if doc is None:
            doc = {"version": 0, "languageId": language_id,
                   "text": text or ""}
            srv.documents[uri] = doc
            srv.conn.notify("textDocument/didOpen", {
                "textDocument": {"uri": uri,
                                 "languageId": language_id,
                                 "version": 0,
                                 "text": doc["text"]}})
        return uri, doc

    def open_document(self, path, text):
        key, language_id = language_for_path(path)
        srv = self.ensure(key)
        with srv.lock:
            self._doc(srv, path, language_id, text)
        return self.status()

    def change_document(self, path, text):
        found = language_for_path(path)
        if not found:
            return self.status()
        key, language_id = found
        srv = self._servers[key]
        with srv.lock:
            if srv.conn is None or not srv.conn.alive:
                return self.status()
            uri, doc = self._doc(srv, path, language_id, text)
            doc["version"] += 1
            doc["text"] = text
            srv.conn.notify("textDocument/didChange", {
                "textDocument": {"uri": uri,
                                 "version": doc["version"]},
                # No range: a whole-document replacement,
                # valid under any advertised sync kind.
                "contentChanges": [{"text": text}]})
        return self.status()

    def save_document(self, path):
        found = language_for_path(path)
        if not found:
            return self.status()
        key, _lid = found
        srv = self._servers[key]
        with srv.lock:
            if srv.conn is not None and srv.conn.alive:
                uri = path_to_uri(self.workspace / path)
                if uri in srv.documents:
                    srv.conn.notify("textDocument/didSave", {
                        "textDocument": {"uri": uri}})
        return self.status()

    def close_document(self, path):
        found = language_for_path(path)
        if not found:
            return self.status()
        key, _lid = found
        srv = self._servers[key]
        with srv.lock:
            uri = path_to_uri(self.workspace / path)
            if uri in srv.documents:
                srv.documents.pop(uri, None)
                srv.diagnostics.pop(uri, None)
                if srv.conn is not None and srv.conn.alive:
                    srv.conn.notify("textDocument/didClose", {
                        "textDocument": {"uri": uri}})
        return self.status()

    # -- requests -----------------------------------------------------
    def _request(self, path, method, extra):
        found = language_for_path(path)
        if not found:
            raise LspError("no language server for this "
                           "file type", code="lsp-unsupported")
        key, language_id = found
        srv = self.ensure(key)
        uri = path_to_uri(self.workspace / path)
        with srv.lock:
            if uri not in srv.documents:
                # Hovering a file the client never opened:
                # open it from disk so the server knows it.
                try:
                    text = (self.workspace / path).read_text(
                        encoding="utf-8", errors="replace")
                except OSError:
                    text = ""
                self._doc(srv, path, language_id, text)
            params = {"textDocument": {"uri": uri}}
            params.update(extra)
            return srv.conn.request(method, params)

    def hover(self, path, line, character):
        return self._request(path, "textDocument/hover", {
            "position": {"line": int(line),
                         "character": int(character)}})

    def definition(self, path, line, character):
        return self._request(path, "textDocument/definition", {
            "position": {"line": int(line),
                         "character": int(character)}})

    def completion(self, path, line, character):
        return self._request(path, "textDocument/completion", {
            "position": {"line": int(line),
                         "character": int(character)}})

    def document_symbols(self, path):
        return self._request(
            path, "textDocument/documentSymbol", {})

    # -- readouts -----------------------------------------------------
    def diagnostics(self, path=None):
        """{workspace-relative path: [diagnostics]} for
        every file the servers have reported on, or just
        one file when `path` is given."""
        out = {}
        ws = self.workspace
        for srv in self._servers.values():
            with srv.lock:
                items = list(srv.diagnostics.items())
            for uri, diags in items:
                p = uri_to_path(uri)
                if p is None:
                    continue
                try:
                    rel = str(Path(p).relative_to(ws))
                except ValueError:
                    rel = p
                out[rel] = diags
        if path is not None:
            return {path: out.get(path, [])}
        return out

    def status(self):
        servers = {}
        for key, srv in self._servers.items():
            installed = (self.python_installed()
                         if key == "python"
                         else self.typescript_installed())
            with self._install_lock:
                inst = dict(self._installing.get(key) or {})
            reason = self._unavailable_reason(key)
            with srv.lock:
                state = srv.state
                restarts = srv.restarts
                last_error = srv.last_error
                open_docs = len(srv.documents)
            if inst.get("state") == "installing":
                state = "installing"
            elif inst.get("state") == "failed" \
                    and not installed:
                state = "install-failed"
                last_error = inst.get("error", "")
            elif reason and not installed:
                state = "unavailable"
                last_error = reason
            elif not installed:
                state = "not-installed"
            elif state == "stopped":
                state = "stopped"
            servers[key] = {
                "server": SERVER_NAMES[key],
                "state": state,
                "installed": installed,
                "restarts": restarts,
                "documents": open_docs,
                "detail": last_error,
                "needsNode": key == "typescript"
                and not self._which("node"),
            }
        return {"servers": servers,
                "workspace": str(self.workspace)}

    def status_summary(self):
        """Cheap one-line-per-server state for /api/health
        (never spawns or installs anything)."""
        out = {}
        for key, st in self.status()["servers"].items():
            out[key] = st["state"]
        return out
