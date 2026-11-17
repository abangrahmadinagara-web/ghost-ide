#!/usr/bin/env python3
"""Unit tests for Desktop Cycle 3 D6 (model store)
against the server module imported with a temp HOME and
workspace (the test_mcp_d8 pattern). The store's module
globals (MODELS_DIR, the import registry path, MODEL,
the download states) are repointed at temp dirs per
test, so nothing here touches the real model library:

- machine-fit classification on synthetic RAM values,
  including the unknown cases
- GGUF header validation, including refusals
- the import registry round-trip
- local import: copy, hash recorded, honest labelling;
  a non-GGUF file is refused and registers nothing
- delete: not-installed, plain, imported (file +
  registry), and the active-model guard (refused while
  loaded, stop-first for a process we started, refused
  for a server started outside GhostIDE)
- pause/cancel controls on fabricated download states
- the download worker itself, against a fake urlopen:
  pause keeps the .part file, resume finishes it via
  Range, cancel deletes the .part file
- the store listing's catalog cards carry exactly the
  downloader's pinned sizes (no drift)
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

_TD = Path(tempfile.mkdtemp(prefix="ghostide-d6-"))
_WS = _TD / "ws"
_WS.mkdir()
_HOME = _TD / "home"
_HOME.mkdir()
_SAVED_ENV = {k: os.environ.get(k)
              for k in ("HOME", "GHOST_IDE_WORKSPACE",
                        "GHOST_IDE_MODELS")}
os.environ["HOME"] = str(_HOME)
os.environ["GHOST_IDE_WORKSPACE"] = str(_WS)

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import server  # noqa: E402

for _k, _v in _SAVED_ENV.items():
    if _v is None:
        os.environ.pop(_k, None)
    else:
        os.environ[_k] = _v

GIB = 1024 ** 3


def gguf_file(path, payload=b"x" * 64):
    path.write_bytes(b"GGUF" + payload)
    return path


class StoreCase(unittest.TestCase):
    """Repoint the store's globals at a fresh temp
    models dir and registry for every test."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp(
            prefix="ghostide-d6-case-"))
        self.models = self.td / "models"
        self.models.mkdir()
        self._saved = {
            "MODELS_DIR": server.MODELS_DIR,
            "IMPORTED_MODELS_FILE":
                server.IMPORTED_MODELS_FILE,
            "MODEL": server.MODEL,
            "MODEL_NAME": server.MODEL_NAME,
            "_model_proc": server._model_proc,
            "llama_health": server.llama_health,
            "DL_STATE": dict(server.DL_STATE),
            "EMBED_DL_STATE": dict(server.EMBED_DL_STATE),
            "IMPORT_DL_STATE":
                dict(server.IMPORT_DL_STATE),
            "DL_CONTROL": dict(server.DL_CONTROL),
            "EMBED_DL_CONTROL":
                dict(server.EMBED_DL_CONTROL),
        }
        server.MODELS_DIR = self.models
        server.IMPORTED_MODELS_FILE = \
            self.td / "imported-models.json"
        server._model_proc = None
        server.llama_health = lambda: False

    def tearDown(self):
        for k, v in self._saved.items():
            if isinstance(v, dict):
                getattr(server, k).clear()
                getattr(server, k).update(v)
            else:
                setattr(server, k, v)


class TestFitClass(unittest.TestCase):
    def test_fits_tight_too_big(self):
        self.assertEqual(server._fit_class(1.2, 16 * GIB),
                         "fits")
        self.assertEqual(server._fit_class(6.5, 16 * GIB),
                         "fits")
        self.assertEqual(server._fit_class(6.5, 8 * GIB),
                         "tight")
        self.assertEqual(server._fit_class(6.5, 4 * GIB),
                         "too-big")

    def test_boundaries(self):
        # 60% of total is the fits line, 90% the tight
        # line (total 10 GiB for round numbers).
        self.assertEqual(server._fit_class(6.0, 10 * GIB),
                         "fits")
        self.assertEqual(server._fit_class(6.1, 10 * GIB),
                         "tight")
        self.assertEqual(server._fit_class(9.0, 10 * GIB),
                         "tight")
        self.assertEqual(server._fit_class(9.1, 10 * GIB),
                         "too-big")

    def test_unknown(self):
        self.assertEqual(server._fit_class(6.5, None),
                         "unknown")
        self.assertEqual(server._fit_class(None, 16 * GIB),
                         "unknown")
        self.assertEqual(server._fit_class(0, 16 * GIB),
                         "unknown")
        self.assertEqual(server._fit_class(6.5, 0),
                         "unknown")


class TestMachineRam(unittest.TestCase):
    def test_ram_read_or_none(self):
        ram = server._machine_ram_bytes()
        if sys.platform.startswith("linux"):
            self.assertIsInstance(ram, int)
            self.assertGreater(ram, GIB)
        else:
            self.assertTrue(ram is None or ram > 0)


class TestGguf(StoreCase):
    def test_magic(self):
        good = gguf_file(self.td / "good.gguf")
        self.assertTrue(server._gguf_ok(good))

    def test_refusals(self):
        bad = self.td / "bad.gguf"
        bad.write_bytes(b"NOPE" + b"x" * 64)
        self.assertFalse(server._gguf_ok(bad))
        short = self.td / "short.gguf"
        short.write_bytes(b"GG")
        self.assertFalse(server._gguf_ok(short))
        self.assertFalse(
            server._gguf_ok(self.td / "missing.gguf"))


class TestRegistry(StoreCase):
    def test_round_trip(self):
        entry = {"id": "imp-x-12345678", "name": "x",
                 "file": "x.gguf", "bytes": 68,
                 "sha256": "ab" * 32, "source": "file",
                 "origin": "/tmp/x.gguf",
                 "imported_at": "2026-01-01T00:00:00Z"}
        server._register_import(entry)
        got = server._imported_models()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["id"], entry["id"])
        self.assertEqual(
            server._imported_by_id("imp-x-12345678"),
            entry)
        self.assertIsNone(server._imported_by_id("nope"))
        # The registry is a real file in app data.
        raw = json.loads(
            server.IMPORTED_MODELS_FILE.read_text())
        self.assertEqual(raw[0]["file"], "x.gguf")


class TestImportLocal(StoreCase):
    def test_import_copies_hashes_and_labels(self):
        src = gguf_file(self.td / "mine.gguf",
                        b"payload" * 16)
        d = server._import_local(str(src))
        self.assertTrue(d["ok"], d)
        ent = d["model"]
        dest = self.models / "mine.gguf"
        self.assertTrue(dest.is_file())
        self.assertEqual(
            ent["sha256"],
            hashlib.sha256(src.read_bytes()).hexdigest())
        self.assertEqual(ent["source"], "file")
        card = [c for c in server.models_store()["models"]
                if c["id"] == ent["id"]]
        self.assertEqual(len(card), 1)
        self.assertEqual(card[0]["kind"], "imported")
        self.assertFalse(card[0]["verified"])
        self.assertTrue(card[0]["installed"])

    def test_import_refuses_non_gguf(self):
        bad = self.td / "bad.gguf"
        bad.write_bytes(b"just some text, not a model")
        d = server._import_local(str(bad))
        self.assertIn("GGUF", d["error"])
        self.assertEqual(server._imported_models(), [])
        self.assertFalse((self.models / "bad.gguf")
                         .exists())

    def test_import_missing_file(self):
        d = server._import_local(str(self.td / "no.gguf"))
        self.assertIn("no such file", d["error"])


class TestStoreShape(StoreCase):
    def test_cards_match_catalog_pins(self):
        d = server.models_store()
        self.assertTrue(d["ok"])
        cards = {c["id"]: c for c in d["models"]}
        for mid, e in server.MODEL_CATALOG.items():
            self.assertIn(mid, cards)
            # No drift: the card IS the catalog entry.
            self.assertEqual(cards[mid]["bytes"],
                             e["bytes"])
            self.assertEqual(cards[mid]["file"],
                             e["file"])
            self.assertEqual(cards[mid]["role"], "chat")
            self.assertTrue(cards[mid]["verified"])
            self.assertIn(cards[mid]["fit"],
                          ("fits", "tight", "too-big",
                           "unknown"))
        self.assertIn("embed", cards)
        self.assertEqual(cards["embed"]["role"],
                         "embedding")
        self.assertEqual(cards["embed"]["bytes"],
                         server.EMBED_MODEL["bytes"])
        rec = [c for c in d["models"] if c["recommended"]]
        self.assertLessEqual(len(rec), 1)
        if rec:
            self.assertEqual(rec[0]["kind"], "catalog")
            self.assertEqual(rec[0]["role"], "chat")
            self.assertEqual(rec[0]["fit"], "fits")
        self.assertEqual(d["disk"]["modelsDir"],
                         str(self.models))
        self.assertEqual(d["disk"]["modelsBytes"], 0)
        self.assertEqual(d["active"]["path"],
                         server.MODEL)


class TestSetActive(StoreCase):
    def test_embed_refused(self):
        d = server._set_active_model("embed")
        self.assertIn("chat model", d["error"])

    def test_catalog_not_downloaded(self):
        d = server._set_active_model("0.5b")
        self.assertIn("not downloaded", d["error"])

    def test_catalog_installed(self):
        e = server.MODEL_CATALOG["0.5b"]
        dest = self.models / e["file"]
        with open(dest, "wb") as f:
            f.truncate(e["bytes"])
        d = server._set_active_model("0.5b")
        self.assertTrue(d["ok"], d)
        self.assertEqual(server.MODEL, str(dest))

    def test_imported(self):
        src = gguf_file(self.td / "act.gguf")
        ent = server._import_local(str(src))["model"]
        d = server._set_active_model(ent["id"])
        self.assertTrue(d["ok"], d)
        self.assertEqual(server.MODEL,
                         str(self.models / "act.gguf"))


class FakeProc:
    def __init__(self):
        self.alive = True
        self.terminated = False

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated = True
        self.alive = False


class TestDelete(StoreCase):
    def _install(self, mid="0.5b"):
        dest = self.models / server.MODEL_CATALOG[mid][
            "file"]
        dest.write_bytes(b"GGUF" + b"z" * 32)
        return dest

    def test_not_installed(self):
        d = server._delete_model("0.5b")
        self.assertIn("not installed", d["error"])

    def test_plain_delete(self):
        dest = self._install()
        d = server._delete_model("0.5b")
        self.assertTrue(d["ok"], d)
        self.assertFalse(dest.exists())

    def test_imported_delete_removes_registry(self):
        src = gguf_file(self.td / "del.gguf")
        ent = server._import_local(str(src))["model"]
        dest = self.models / "del.gguf"
        d = server._delete_model(ent["id"])
        self.assertTrue(d["ok"], d)
        self.assertFalse(dest.exists())
        self.assertEqual(server._imported_models(), [])

    def test_active_not_loaded_deletes(self):
        dest = self._install()
        server.MODEL = str(dest)
        d = server._delete_model("0.5b")
        self.assertTrue(d["ok"], d)
        self.assertFalse(dest.exists())
        self.assertNotEqual(server.MODEL, str(dest))

    def test_active_loaded_refused_without_stop(self):
        dest = self._install()
        server.MODEL = str(dest)
        server.llama_health = lambda: True
        d = server._delete_model("0.5b")
        self.assertEqual(d.get("code"), "model-loaded")
        self.assertTrue(d.get("canStop"))
        self.assertTrue(dest.exists())

    def test_active_loaded_outside_cannot_stop(self):
        dest = self._install()
        server.MODEL = str(dest)
        server.llama_health = lambda: True
        d = server._delete_model("0.5b", stop=True)
        self.assertIn("started outside", d["error"])
        self.assertTrue(dest.exists())

    def test_active_loaded_stop_and_delete(self):
        dest = self._install()
        server.MODEL = str(dest)
        proc = FakeProc()
        server._model_proc = proc
        server.llama_health = lambda: proc.alive
        d = server._delete_model("0.5b", stop=True)
        self.assertTrue(d["ok"], d)
        self.assertTrue(proc.terminated)
        self.assertFalse(dest.exists())

    def test_embed_delete_blocked_by_download(self):
        server.EMBED_DL_STATE.update(
            {"state": "paused", "id": "nomic"})
        d = server._delete_model("embed")
        self.assertIn("cancel it first", d["error"])


class TestPauseCancel(StoreCase):
    def test_pause_noop_when_idle(self):
        d = server._pause_download("7b")
        self.assertTrue(d["ok"])
        self.assertIn("nothing", d["note"])

    def test_pause_sets_flag(self):
        server.DL_STATE.update(
            {"state": "downloading", "id": "7b"})
        d = server._pause_download("7b")
        self.assertTrue(d["ok"])
        self.assertTrue(server.DL_CONTROL["pause"])

    def test_cancel_while_verifying_refused(self):
        server.DL_STATE.update(
            {"state": "verifying", "id": "7b"})
        d = server._cancel_download("7b")
        self.assertIn("too late", d["error"])

    def test_cancel_paused_removes_part(self):
        part = self.models / (
            server.MODEL_CATALOG["7b"]["file"] + ".part")
        part.write_bytes(b"partial")
        server.DL_STATE.update(
            {"state": "paused", "id": "7b", "done": 7})
        d = server._cancel_download("7b")
        self.assertTrue(d["ok"], d)
        self.assertFalse(part.exists())
        self.assertEqual(server.DL_STATE["state"],
                         "idle")

    def test_unknown_id(self):
        self.assertIn("error",
                      server._pause_download("nope"))
        self.assertIn("error",
                      server._cancel_download("nope"))


class FakeResp:
    def __init__(self, data, status=200, on_read=None):
        self._data = data
        self.status = status
        self._pos = 0
        self._on_read = on_read
        self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n):
        self.reads += 1
        if self._on_read:
            self._on_read(self.reads)
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk


class TestWorkerPauseResume(StoreCase):
    """_dl_worker against a fake urlopen and a throwaway
    catalog entry: pause keeps the .part, resume finishes
    through the Range path, cancel removes the .part."""

    PAYLOAD = bytes(range(256)) * 14000  # 3,584,000 bytes

    def setUp(self):
        super().setUp()
        self.sha = hashlib.sha256(
            self.PAYLOAD).hexdigest()
        server.MODEL_CATALOG["testm"] = {
            "file": "testm.gguf",
            "url": "https://example.invalid/testm.gguf",
            "bytes": len(self.PAYLOAD), "sha256": self.sha,
            "name": "Test", "params": "0B",
            "quant": "Q4_K_M", "ram_gb": 0.1,
            "blurb": "test"}
        self._real_urlopen = urllib.request.urlopen
        self.ranges = []

    def tearDown(self):
        urllib.request.urlopen = self._real_urlopen
        del server.MODEL_CATALOG["testm"]
        super().tearDown()

    def _install_fake(self, on_read=None):
        payload = self.PAYLOAD
        ranges = self.ranges

        def fake_urlopen(req, timeout=None):
            rng = req.get_header("Range")
            if rng:
                start = int(rng.split("=")[1]
                            .split("-")[0])
                ranges.append(start)
                return FakeResp(payload[start:],
                                status=206,
                                on_read=on_read)
            return FakeResp(payload, status=200,
                            on_read=on_read)

        urllib.request.urlopen = fake_urlopen

    def test_pause_then_resume(self):
        def hook(reads):
            if reads == 2:
                server.DL_CONTROL["pause"] = True

        self._install_fake(on_read=hook)
        server._dl_worker("testm")
        part = self.models / "testm.gguf.part"
        self.assertEqual(server.DL_STATE["state"],
                         "paused")
        self.assertTrue(part.is_file())
        self.assertEqual(part.stat().st_size, 2 << 20)
        # Resume: the worker must ask for the rest.
        self._install_fake()
        server._dl_worker("testm")
        self.assertEqual(self.ranges, [2 << 20])
        self.assertEqual(server.DL_STATE["state"],
                         "done")
        dest = self.models / "testm.gguf"
        self.assertTrue(dest.is_file())
        self.assertEqual(
            hashlib.sha256(
                dest.read_bytes()).hexdigest(), self.sha)

    def test_cancel_removes_part(self):
        def hook(reads):
            if reads == 2:
                server.DL_CONTROL["cancel"] = True

        self._install_fake(on_read=hook)
        server._dl_worker("testm")
        self.assertEqual(server.DL_STATE["state"],
                         "idle")
        self.assertFalse(
            (self.models / "testm.gguf.part").exists())
        self.assertFalse(
            (self.models / "testm.gguf").exists())


class TestDlView(unittest.TestCase):
    def test_percent(self):
        v = server._dl_view({"state": "downloading",
                             "done": 25, "total": 200,
                             "error": ""})
        self.assertEqual(v["percent"], 12.5)
        v = server._dl_view({"state": "idle", "done": 0,
                             "total": 0, "error": ""})
        self.assertEqual(v["percent"], 0.0)


if __name__ == "__main__":
    unittest.main()
