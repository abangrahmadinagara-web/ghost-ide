#!/usr/bin/env python3
"""Unit tests for the D5 neural embedding index
(embed_index.py): symbol-aware and sliding-window
chunking, content-hash incremental sync (changed files
re-embedded, unchanged files untouched, deleted files
purged), brute-force cosine ranking order on synthetic
vectors, and honest stats when no index exists. The
embedder is a deterministic fake at the module seam; the
real model is exercised by test_embed_e2e.py.
"""
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import embed_index as ei


class CountingEmbedder:
    """Deterministic fake embedder: a text's vector is a
    4-dim bag over marker words, and every call is counted
    so tests can prove what was (not) re-embedded."""

    def __init__(self):
        self.calls = 0
        self.texts = 0

    def __call__(self, texts):
        self.calls += 1
        self.texts += len(texts)
        out = []
        for t in texts:
            v = [0.1, 0.1, 0.1, 0.1]
            if "sleep" in t:
                v[0] += 2.0
            if "urlopen" in t:
                v[1] += 2.0
            if "digit" in t:
                v[2] += 2.0
            out.append(v)
        return out


class ChunkTests(unittest.TestCase):
    def test_symbol_chunks_start_at_definitions(self):
        text = ("import os\nimport sys\n\n\n"
                "def alpha():\n    return 1\n\n\n"
                "def beta():\n    return 2\n")
        syms = [{"name": "alpha", "kind": "function",
                 "line": 5},
                {"name": "beta", "kind": "function",
                 "line": 9}]
        chunks = ei.chunk_text("a.py", text, syms)
        names = [(c["name"], c["start_line"]) for c in chunks]
        self.assertIn(("alpha", 5), names)
        self.assertIn(("beta", 9), names)
        # The imports before the first definition are a
        # chunk of their own, so they stay searchable.
        self.assertIn(("", 1), names)
        alpha = [c for c in chunks if c["name"] == "alpha"][0]
        self.assertEqual(alpha["end_line"], 8)
        self.assertIn("return 1", alpha["text"])

    def test_sliding_window_without_symbols(self):
        text = "\n".join("line %d" % i for i in range(1, 131))
        chunks = ei.chunk_text("plain.txt", text, None)
        self.assertEqual(
            [(c["start_line"], c["end_line"]) for c in chunks],
            [(1, 60), (49, 108), (97, 130)])
        self.assertTrue(all(c["name"] == "" for c in chunks))

    def test_chunk_cap_per_file(self):
        parts = []
        syms = []
        for i in range(300):
            parts.append("def f%d():\n    return %d" % (i, i))
            syms.append({"name": "f%d" % i,
                         "kind": "function",
                         "line": 2 * i + 1})
        chunks = ei.chunk_text("big.py", "\n".join(parts),
                               syms)
        self.assertEqual(len(chunks), ei.MAX_CHUNKS_PER_FILE)

    def test_empty_file_has_no_chunks(self):
        self.assertEqual(ei.chunk_text("e.py", "", None), [])
        self.assertEqual(ei.chunk_text("e.py", "\n", None),
                         [])


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="ghostide-emb-"))
        self.db = self.td / "embeddings.db"
        self.emb = CountingEmbedder()
        self.files = {
            "retry.py": "def deliver():\n    sleep(1)\n",
            "fetch.py": "def grab():\n    urlopen(a)\n",
            "sort.py": "def quicksort(x):\n    return x\n",
        }

    def open_index(self, model="m1"):
        return ei.EmbedIndex(self.db, model_id=model)

    def test_first_sync_embeds_everything(self):
        idx = self.open_index()
        st = idx.sync(self.files, {}, self.emb, now=100)
        idx.close()
        self.assertEqual(st["embedded_files"], 3)
        self.assertEqual(st["removed_files"], 0)
        self.assertEqual(st["unchanged_files"], 0)
        self.assertEqual(st["files"], 3)
        self.assertEqual(st["chunks"], 3)
        self.assertEqual(self.emb.texts, 3)

    def test_second_sync_embeds_nothing(self):
        idx = self.open_index()
        idx.sync(self.files, {}, self.emb, now=100)
        calls = self.emb.calls
        st = idx.sync(self.files, {}, self.emb, now=200)
        idx.close()
        self.assertEqual(st["embedded_files"], 0)
        self.assertEqual(st["unchanged_files"], 3)
        self.assertEqual(self.emb.calls, calls)

    def test_changed_file_only_is_reembedded(self):
        idx = self.open_index()
        idx.sync(self.files, {}, self.emb)
        before = self.emb.texts
        changed = dict(self.files)
        changed["fetch.py"] = ("def grab():\n"
                               "    urlopen(a)\n"
                               "    urlopen(b)\n")
        st = idx.sync(changed, {}, self.emb)
        idx.close()
        self.assertEqual(st["embedded_files"], 1)
        self.assertEqual(st["unchanged_files"], 2)
        # Exactly the changed file's chunks went back
        # through the embedder.
        self.assertEqual(self.emb.texts - before, 1)

    def test_deleted_file_is_purged(self):
        idx = self.open_index()
        idx.sync(self.files, {}, self.emb)
        fewer = {k: v for k, v in self.files.items()
                 if k != "retry.py"}
        st = idx.sync(fewer, {}, self.emb)
        self.assertEqual(st["removed_files"], 1)
        self.assertEqual(st["files"], 2)
        hits = idx.search([2.1, 0.1, 0.1, 0.1], 10)
        idx.close()
        self.assertFalse(
            any(h["path"] == "retry.py" for h in hits))

    def test_ranking_order_on_synthetic_vectors(self):
        # Direct store: three chunks with hand-set vectors
        # via a mapping embedder, ranked against [1, 0].
        vecs = {"aaa": [1.0, 0.0], "bbb": [0.0, 1.0],
                "ccc": [0.7, 0.7]}

        def mapping(texts):
            return [vecs[t.strip()] for t in texts]

        idx = self.open_index()
        idx.sync({"x.py": "aaa\n", "y.py": "bbb\n",
                  "z.py": "ccc\n"}, {}, mapping)
        hits = idx.search([1.0, 0.0], 3)
        idx.close()
        self.assertEqual([h["path"] for h in hits],
                         ["x.py", "z.py", "y.py"])
        self.assertAlmostEqual(hits[0]["score"], 1.0)
        self.assertAlmostEqual(hits[2]["score"], 0.0)

    def test_search_matches_brute_force_reference(self):
        # The store search IS brute force; prove its scores
        # equal an independent cosine over the raw vectors.
        import math

        def ref_cosine(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            return dot / (math.sqrt(sum(x * x for x in a))
                          * math.sqrt(sum(y * y for y in b)))

        table = {"one": [0.3, 0.9, 0.1], "two": [0.8, 0.2, 0.4],
                 "three": [0.1, 0.1, 0.99]}

        def mapping(texts):
            return [table[t.strip()] for t in texts]

        idx = self.open_index()
        idx.sync({"a.py": "one\n", "b.py": "two\n",
                  "c.py": "three\n"}, {}, mapping)
        q = [0.5, 0.5, 0.5]
        hits = {h["path"]: h["score"]
                for h in idx.search(q, 10)}
        idx.close()
        for path, key in (("a.py", "one"), ("b.py", "two"),
                          ("c.py", "three")):
            self.assertAlmostEqual(
                hits[path], ref_cosine(q, table[key]),
                places=6)

    def test_model_change_wipes_index(self):
        idx = self.open_index("m1")
        idx.sync(self.files, {}, self.emb)
        idx.close()
        idx2 = self.open_index("m2")
        st = idx2.stats()
        idx2.close()
        self.assertEqual(st["chunks"], 0)
        self.assertEqual(st["files"], 0)


class HonestyTests(unittest.TestCase):
    def test_stats_and_search_without_index(self):
        td = Path(tempfile.mkdtemp(prefix="ghostide-emb-"))
        idx = ei.EmbedIndex(td / "nope.db", model_id="m1")
        st = idx.stats()
        self.assertFalse(st["exists"])
        self.assertEqual(st["files"], 0)
        self.assertEqual(st["chunks"], 0)
        self.assertIsNone(st["updated"])
        self.assertEqual(idx.search([1.0, 0.0], 5), [])
        self.assertEqual(idx.file_hashes(), {})


if __name__ == "__main__":
    unittest.main()
