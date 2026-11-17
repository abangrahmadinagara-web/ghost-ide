// Desktop Cycle 1 (v3.11.0): portable mode, D3 packaged
// updates, D7 AGENTS.md + workspace trust. Static pins
// over the shipped sources; the behaviour proofs are
// the Python suites in tests/ plus updater.py selftest.
import { readFileSync, existsSync } from "node:fs";
const home = process.env.HOME;
const root = home + "/workspace/ghost-ide/";
const py = readFileSync(root + "server.py", "utf8");
const src = readFileSync(root + "web/src/main.js", "utf8");
const html = readFileSync(root + "web/index.html", "utf8");
const vjson = JSON.parse(readFileSync(root + "VERSION.json", "utf8"));
let passed = 0, failed = 0;
const ck = (n, c) => { console.log((c ? "PASS " : "FAIL ") + n); c ? passed++ : failed++; };

ck("C1 modules exist and are wired into the server",
   existsSync(root + "ghost_paths.py") &&
   existsSync(root + "updater.py") &&
   existsSync(root + "workspace_trust.py") &&
   py.includes("import ghost_paths") &&
   py.includes("import workspace_trust as _trust_mod") &&
   py.includes("import updater as _updater_mod"));
ck("C1 portable flag + env honoured, data dir centralised",
   py.includes("PORTABLE = ghost_paths.portable_requested()") &&
   py.includes('DATA_DIR / "tls"') &&
   py.includes('DATA_DIR / "models"') &&
   py.includes('DATA_DIR / "mcp.json"'));
/* Was a 3.11.0 literal in Cycle 1 (true only that
   release); the durable claims are server/VERSION.json
   agreement on the desktop train and the phone train
   staying put. Desktop Cycle 2 moved the desktop train
   to 3.12.0; "latest" and android stay 3.10.1 until the
   next Android release re-syncs them. */
ck("C1 version: desktop train agrees, phone train untouched",
   py.includes('APP_VERSION = "' + vjson.desktop.version + '"') &&
   vjson.desktop && vjson.version === vjson.desktop.version &&
   vjson.latest === "3.10.1" &&
   vjson.android && vjson.android.version === "3.10.1");
ck("C1 update check follows the desktop train",
   py.includes('(info.get("desktop") or {})'));
ck("C1 updater routes present, source runs report-only",
   py.includes('"/api/update/status"') &&
   py.includes('"/api/update/download"') &&
   py.includes('"/api/update/apply"') &&
   py.includes('"/api/update/rollback"') &&
   py.includes('"report-only; updates apply "'));
ck("C1 trust enforced at the API layer, not just hidden",
   py.includes("TRUST_EXEC_ROUTES = frozenset({") &&
   py.includes('"/api/terminal", "/api/terminal/reset",') &&
   py.includes('"/api/mcp/call", "/api/mcp/prompt", "/api/mcp/restart",') &&
   py.includes('"code": "workspace-untrusted"'));
ck("C1 trust routes + startup auto-trust + host-only decisions",
   py.includes('"/api/trust"') &&
   py.includes('"trust is decided "') &&
   py.includes('TRUST_STORE.set(WORKSPACE, True, source="startup")'));
ck("C1 AGENTS.md rides the chat system context",
   py.includes("_trust_mod.instructions_block(WORKSPACE)") &&
   py.includes('"/api/instructions"'));
ck("C1 UI: trust pill, banner, settings rows, update flow",
   html.includes('id="trustPill"') &&
   html.includes('id="trustBanner"') &&
   html.includes('id="setTrustInfo"') &&
   html.includes('id="setInstrInfo"') &&
   html.includes('id="setUpdFlowRow"') &&
   src.includes("refreshTrustUI(true)") &&
   src.includes('fetch("/api/update/status")') &&
   src.includes('postJson("/api/trust", { trusted })'));
ck("C1 apply-at-launch + boot confirmation in main",
   py.includes("get_updater().apply_at_launch()") &&
   py.includes("get_updater().confirm_boot_ok()"));
console.log(`RESULT ${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
