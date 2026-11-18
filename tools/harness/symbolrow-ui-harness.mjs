// Mobile Cycle 1 (M3): the slim symbol row.
//
// Static pins: the row element docks inside the
// editor column, the PHONE settings section carries
// the toggle + key field + reset, the CSS keeps the
// row phone-only and sideways-scrolling, the touch
// pass lifts the named controls to a 40px minimum,
// and the census of the counted string in main.js
// stays exactly 101 (this file's additions build
// DOM nodes, never markup strings).
//
// Live pins: the SYMBOLROW block is extracted from
// main.js and driven against the REAL CodeMirror
// packages (state, autocomplete, commands) in Node.
// The fake view mirrors the app editor's relevant
// configuration - closeBrackets() from basicSetup
// plus the history extension - so the bracket proof
// is the editor's own auto-close behavior, not a
// reimplementation: pair in, cursor inside, closing
// key steps over, selection wrapped.
import { readFileSync, existsSync } from "node:fs";
import { EditorState, EditorSelection }
  from "@codemirror/state";
import { insertBracket, closeBrackets }
  from "@codemirror/autocomplete";
import { undo, cursorCharLeft, cursorCharRight,
  insertTab, history } from "@codemirror/commands";

const home = process.env.HOME;
const root = home + "/workspace/ghost-ide/";
const src = readFileSync(root + "web/src/main.js", "utf8");
const app = readFileSync(root + "web/app.js", "utf8");
const html = readFileSync(root + "web/index.html", "utf8");
const css = readFileSync(root + "web/style.css", "utf8");
const gate = readFileSync(root + "tools/viewport_gate.py",
  "utf8");
let passed = 0, failed = 0;
const ck = (n, c) => {
  console.log((c ? "PASS " : "FAIL ") + n);
  c ? passed++ : failed++;
};

/* ---------- static pins ---------- */
const iRow = html.indexOf('id="editorRow"');
const iSym = html.indexOf('id="symbolRow"');
const iRun = html.indexOf('id="runPane"');
ck("M3 html: symbol row docks in the editor column, " +
   "after the editor row and before the run pane",
   iRow > -1 && iSym > iRow && iRun > iSym &&
   html.includes('id="symbolRow" class="hidden" ' +
     'role="toolbar"'));
ck("M3 html: PHONE settings section with toggle, " +
   "key field and reset",
   html.includes("<h4>PHONE</h4>") &&
   html.includes('id="setSymbolRow"') &&
   html.includes('id="setSymbolKeys"') &&
   html.includes('id="setSymbolKeysReset"'));
const m3css = css.split("Mobile Cycle 1 (M3)")[1] || "";
ck("M3 css: row exists only at phone widths and " +
   "scrolls inside itself",
   css.includes("#symbolRow { display: none; }") &&
   m3css.includes("overflow-x: auto") &&
   m3css.includes("#symbolRow.hidden { display: none; }") &&
   m3css.includes("scrollbar-width: none"));
ck("M3 css: keys are 40px touch targets",
   m3css.includes(".symKey {") &&
   m3css.includes("min-width: 40px; height: 40px"));
ck("M3 css: touch pass lifts the named controls to " +
   "40px at phone widths only",
   m3css.includes(".tabClose {") &&
   m3css.includes("#viewBar button { min-height: 40px; }") &&
   m3css.includes(".panel-head button { min-height: 40px; }") &&
   m3css.includes(".gitRow button, .hunkHead button,") &&
   m3css.includes(".conflictRow button { min-height: 40px; }") &&
   m3css.includes(".tab { min-height: 40px; }"));
ck("M3 src: block markers, pointerdown insert path, " +
   "init call",
   src.includes("/*@@SYMBOLROW@@*/") &&
   src.includes("/*@@SYMBOLROW-END@@*/") &&
   src.includes('addEventListener("pointerdown"') &&
   src.includes("initSymbolRow();") &&
   src.includes('SYMBOL_ROW_KEY = "ghostide-symbolrow"'));
ck("M3 src: census unchanged at 101 in source and " +
   "built bundle",
   (src.match(/innerHTML/g) || []).length === 101 &&
   (app.match(/innerHTML/g) || []).length === 101 &&
   app.includes("ghostide-symbolrow"));
ck("M3 gate: symbol-row states are in the matrix",
   gate.includes("symbol-row") &&
   gate.includes("did not insert exactly one") &&
   gate.includes("stayed") &&
   gate.includes("_MEASURES"));

/* ---------- live block, real CodeMirror ---------- */
const block = src.split("/*@@SYMBOLROW@@*/")[1]
  .split("/*@@SYMBOLROW-END@@*/")[0];
const store = new Map();
const fakeLS = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};
const api = new Function("localStorage", "window",
  "insertBracket", "insertTab", "undo",
  "cursorCharLeft", "cursorCharRight",
  block + "; return { symbolRowConfig, " +
  "saveSymbolRowConfig, symbolRowParseField, " +
  "symbolRowInsert, SYMBOL_DEFAULT_KEYS, " +
  "SYMBOL_SPECIALS };")(
  fakeLS, {}, insertBracket, insertTab, undo,
  cursorCharLeft, cursorCharRight);
const { symbolRowConfig, saveSymbolRowConfig,
  symbolRowParseField, symbolRowInsert,
  SYMBOL_DEFAULT_KEYS, SYMBOL_SPECIALS } = api;

function fakeView(doc, selection) {
  const v = {
    state: EditorState.create({ doc, selection,
      extensions: [closeBrackets(), history()] }),
    hasFocus: true,
    focus() {},
    /* cursorCharLeft/Right ask the view two things
       the real EditorView answers from layout: the
       text direction at the cursor (Direction.LTR
       is 0 in the view package's enum) and, for a
       bare cursor, a grapheme-aware char move. The
       fake answers LTR and moves one char in the
       plain ASCII test docs; the collapse behavior
       under test never reaches moveByChar. */
    textDirectionAt: () => 0,
    moveByChar: (range, forward) => EditorSelection
      .cursor(Math.max(0, Math.min(v.state.doc.length,
        range.head + (forward ? 1 : -1)))),
    dispatch: (...specs) => {
      for (const s of specs)
        v.state = v.state.update(s).state;
    },
  };
  return v;
}
const doc = (v) => v.state.doc.toString();
const head = (v) => v.state.selection.main.head;
const anchor = (v) => v.state.selection.main.anchor;

ck("M3 config: defaults are on, in the brief's " +
   "order, specials named",
   symbolRowConfig().enabled === true &&
   JSON.stringify(symbolRowConfig().keys) ===
     JSON.stringify(["Tab", "(", ")", "{", "}", "[",
       "]", ";", ":", "=", ".", ",", "/", "\"", "'",
       "Undo", "Left", "Right"]) &&
   JSON.stringify(SYMBOL_SPECIALS) ===
     JSON.stringify(["Tab", "Undo", "Left", "Right"]) &&
   JSON.stringify(SYMBOL_DEFAULT_KEYS) ===
     JSON.stringify(symbolRowConfig().keys));
saveSymbolRowConfig({ enabled: false, keys: [";", "Tab"] });
ck("M3 config: save and read round-trip under the " +
   "one storage key",
   store.has("ghostide-symbolrow") &&
   symbolRowConfig().enabled === false &&
   JSON.stringify(symbolRowConfig().keys) ===
     JSON.stringify([";", "Tab"]));
store.set("ghostide-symbolrow", "not json");
const junk1 = symbolRowConfig();
store.set("ghostide-symbolrow",
  JSON.stringify({ keys: [123, "", null] }));
const junk2 = symbolRowConfig();
ck("M3 config: corrupt or empty stored data falls " +
   "back to defaults, never to an empty row",
   junk1.enabled === true &&
   junk1.keys.length === SYMBOL_DEFAULT_KEYS.length &&
   junk2.keys.length === SYMBOL_DEFAULT_KEYS.length);
ck("M3 config: the settings field parses space " +
   "separated keys, keeps multi-char literals, and " +
   "refuses an empty list",
   JSON.stringify(symbolRowParseField("Tab ; : ( )")) ===
     JSON.stringify(["Tab", ";", ":", "(", ")"]) &&
   JSON.stringify(symbolRowParseField("-> :=")) ===
     JSON.stringify(["->", ":="]) &&
   symbolRowParseField("") === null &&
   symbolRowParseField("   ") === null);

let v = fakeView("ab", { anchor: 1 });
ck("M3 insert: a literal lands at the cursor",
   symbolRowInsert(v, ";") === true &&
   doc(v) === "a;b" && head(v) === 2);
v = fakeView("ab", EditorSelection.range(0, 2));
ck("M3 insert: a literal replaces the selection",
   symbolRowInsert(v, ";") === true &&
   doc(v) === ";" && head(v) === 1);
v = fakeView("", { anchor: 0 });
ck("M3 insert: ( auto-closes, cursor inside " +
   "(the editor's own closeBrackets behavior)",
   symbolRowInsert(v, "(") === true &&
   doc(v) === "()" && head(v) === 1);
v = fakeView("x", EditorSelection.range(0, 1));
ck("M3 insert: ( wraps the selection in the pair",
   symbolRowInsert(v, "(") === true &&
   doc(v) === "(x)" && anchor(v) === 1 && head(v) === 2);
/* The pair must be built through the row itself:
   step-over is a property of brackets the editor
   auto-closed this session (the extension tracks
   them), exactly as with typed input. A ")" in a
   file that merely contains one is typed literally,
   which the fallback check after this one covers. */
v = fakeView("", { anchor: 0 });
symbolRowInsert(v, "(");
ck("M3 insert: ) steps over the auto-closed " +
   "partner instead of doubling it",
   symbolRowInsert(v, ")") === true &&
   doc(v) === "()" && head(v) === 2);
v = fakeView("()", { anchor: 1 });
ck("M3 insert: ) in front of a bracket the editor " +
   "did not auto-close inserts literally (typed " +
   "parity, via the fallback)",
   symbolRowInsert(v, ")") === true &&
   doc(v) === "())" && head(v) === 2);
v = fakeView("", { anchor: 0 });
ck("M3 insert: a quote pairs like a bracket",
   symbolRowInsert(v, "\"") === true &&
   doc(v) === "\"\"" && head(v) === 1);
v = fakeView("", { anchor: 0 });
ck("M3 insert: a non-bracket falls back to plain " +
   "insertion",
   symbolRowInsert(v, "=") === true &&
   doc(v) === "=" && head(v) === 1);
v = fakeView("ab", { anchor: 1 });
ck("M3 insert: Tab is the editor's own insertTab",
   symbolRowInsert(v, "Tab") === true &&
   doc(v) === "a\tb" && head(v) === 2);
v = fakeView("ab\ncd", EditorSelection.range(0, 5));
symbolRowInsert(v, "Tab");
ck("M3 insert: Tab over lines indents (insertTab " +
   "with a selection), it does not replace",
   doc(v).startsWith(" ") || doc(v).startsWith("\t"));
v = fakeView("ab", { anchor: 2 });
symbolRowInsert(v, ";");
ck("M3 insert: Undo takes the last insert back",
   doc(v) === "ab;" &&
   symbolRowInsert(v, "Undo") === true &&
   doc(v) === "ab");
v = fakeView("ab", EditorSelection.range(0, 2));
ck("M3 insert: Left and Right collapse a selection " +
   "to its edges, like the arrow keys",
   symbolRowInsert(v, "Left") === true &&
   head(v) === 0 && anchor(v) === 0);
v = fakeView("ab", EditorSelection.range(0, 2));
symbolRowInsert(v, "Right");
ck("M3 insert: Right collapses to the far edge",
   head(v) === 2 && anchor(v) === 2);
v = fakeView("ab", { anchor: 2 });
symbolRowInsert(v, "Left");
ck("M3 insert: Left moves a bare cursor one char",
   head(v) === 1);
ck("M3 insert: junk input is refused, not thrown",
   symbolRowInsert(v, "") === false &&
   symbolRowInsert(null, ";") === false &&
   symbolRowInsert(v, 42) === false);

console.log("SYMBOLROW (M3): " + passed +
  " passed, " + failed + " failed");
process.exit(failed ? 1 : 0);
