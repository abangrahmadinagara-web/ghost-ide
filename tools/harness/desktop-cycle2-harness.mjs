// Desktop Cycle 2 (v3.12.0), D4: LSP support. Static
// pins over the shipped sources; the behaviour proofs
// are the Python suites in tests/ (fake-server manager
// tests + the HTTP round trip) and the real-server
// smoke run recorded for the cycle.
import { readFileSync, existsSync } from "node:fs";
const home = process.env.HOME;
const root = home + "/workspace/ghost-ide/";
const py = readFileSync(root + "server.py", "utf8");
const mgr = readFileSync(root + "lsp_manager.py", "utf8");
const src = readFileSync(root + "web/src/main.js", "utf8");
const html = readFileSync(root + "web/index.html", "utf8");
const vjson = JSON.parse(readFileSync(root + "VERSION.json", "utf8"));
let passed = 0, failed = 0;
const ck = (n, c) => { console.log((c ? "PASS " : "FAIL ") + n); c ? passed++ : failed++; };

ck("C2 version: desktop train at 3.12.0, phone train untouched",
   py.includes('APP_VERSION = "3.12.0"') &&
   vjson.desktop && vjson.desktop.version === "3.12.0" &&
   vjson.latest === "3.10.1" &&
   vjson.android && vjson.android.version === "3.10.1");
ck("C2 manager exists, wired, follows workspace + trust",
   existsSync(root + "lsp_manager.py") &&
   py.includes("import lsp_manager as _lsp_mod") &&
   py.includes("workspace_fn=lambda: WORKSPACE") &&
   py.includes("trusted_fn=workspace_trusted"));
ck("C2 LSP spawn routes sit behind the trust gate",
   py.includes('"/api/lsp/open", "/api/lsp/change", "/api/lsp/close",') &&
   py.includes('"/api/lsp/hover",') &&
   py.includes('"/api/lsp/install",'));
ck("C2 manager refuses to spawn for untrusted workspaces",
   mgr.includes("class LspTrustError") &&
   mgr.includes("if not self._trusted_fn():") &&
   mgr.includes('code="workspace-untrusted"'));
ck("C2 routes: status + diagnostics reads, bridge posts",
   py.includes('"/api/lsp/status"') &&
   py.includes('"/api/lsp/diagnostics"') &&
   py.includes("def _lsp_post(self, action)") &&
   py.includes('"/api/lsp/definition"') &&
   py.includes('"/api/lsp/completion"'));
ck("C2 framing is Content-Length JSON-RPC over stdio",
   mgr.includes("Content-Length: %d") &&
   mgr.includes("class JsonRpcConnection") &&
   mgr.includes("textDocument/publishDiagnostics"));
ck("C2 Python server is pinned Jedi with wheel hashes",
   mgr.includes('JEDI_VERSION = "0.47.0"') &&
   mgr.includes("--require-hashes") &&
   mgr.includes("jedi_language_server-0.47.0-py3-none-any.whl"));
ck("C2 TypeScript server pinned, Node required honestly",
   mgr.includes('TS_SERVER_VERSION = "6.1.0"') &&
   mgr.includes('TS_VERSION = "5.9.3"') &&
   mgr.includes("Node.js, which was not found on PATH") &&
   mgr.includes('"needsNode"'));
ck("C2 crash handling is bounded with a manual restart",
   mgr.includes("MAX_RESTARTS = 3") &&
   mgr.includes("def restart(self, key)"));
ck("C2 UI: chip, toggle, hover, completion, F12, merge",
   html.includes('id="lspChip"') &&
   html.includes('id="setLsp"') &&
   src.includes("lspHoverExt()") &&
   src.includes("lspCompletionExt()") &&
   src.includes('key: "F12"') &&
   src.includes("function applyDiagnostics()") &&
   src.includes("lspDiagsByPath[currentFile]"));
ck("C2 lifecycle hooks: open, change, save, close",
   src.includes("lspOpenFile(path, d.content)") &&
   src.includes("queueLspChange()") &&
   src.includes("lspDidSave(currentFile)") &&
   src.includes("lspCloseFile(p)"));
ck("C2 servers die with the IDE and on workspace switch",
   py.includes("_LSP.shutdown_all()") &&
   py.includes("never leave language server processes"));
console.log("=== " + passed + " passed, " + failed + " failed ===");
process.exit(failed ? 1 : 0);
