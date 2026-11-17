#!/usr/bin/env python3
"""Neural embedding index (Desktop Cycle 3, D5).

The fourth search layer: a workspace-local SQLite store of
chunk vectors produced by a local embedding model, ranked
by brute-force cosine similarity in Python. Workspace scale
(hundreds to a few thousand chunks) makes brute force the
honest choice: exact scores, no approximate index to drift.

This module is deliberately engine-free. Callers pass an
embed_fn(texts) -> [vectors] at the seam, so the server can
drive llama.cpp while tests drive a deterministic fake
embedder through the exact same chunk, store and rank code.

Chunking is symbol-aware where the caller supplies symbol
definitions (chunks start at a definition and run to the
next one, windowed when long) and falls back to a sliding
window for files without definitions. Files are tracked by
content hash: a sync re-embeds only files whose hash moved
and purges chunks of files that are gone.
"""
import hashlib
import math
import sqlite3
import struct
from pathlib import Path

# Chunk shape: symbol spans window at 80 lines with a
# 64-line step (16 lines of overlap), plain files at 60
# lines with a 48-line step. A chunk's text is capped so it
# always fits the embedding model's context with room for
# the task prefix.
SYMBOL_WINDOW_LINES = 80
SYMBOL_WINDOW_STEP = 64
PLAIN_WINDOW_LINES = 60
PLAIN_WINDOW_STEP = 48
CHUNK_MAX_CHARS = 6000
MAX_CHUNKS_PER_FILE = 200
EXCERPT_MAX_CHARS = 4000


def hash_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _window_chunks(rel, lines, start, end, name):
    """Window the 1-based inclusive line range [start, end]
    into chunks; step depends on whether the range came from
    a symbol span or a plain file."""
    if name:
        size, step = SYMBOL_WINDOW_LINES, SYMBOL_WINDOW_STEP
    else:
        size, step = PLAIN_WINDOW_LINES, PLAIN_WINDOW_STEP
    out = []
    s = start
    while s <= end:
        e = min(end, s + size - 1)
        out.append({
            "path": rel, "start_line": s, "end_line": e,
            "name": name,
            "text": "\n".join(lines[s - 1:e])[:CHUNK_MAX_CHARS],
        })
        if e >= end:
            break
        s += step
    return out


def chunk_text(rel, text, symbols=None):
    """Split one file into embedding chunks.

    symbols: [{name, kind, line}] for this file (1-based
    definition lines), or None/empty for the sliding-window
    fallback. Lines before the first definition (imports,
    module docstrings) become one unnamed chunk so they are
    searchable too.
    """
    lines = text.splitlines()
    if not lines or not any(ln.strip() for ln in lines):
        return []
    defs = sorted(
        (s for s in (symbols or [])
         if isinstance(s.get("line"), int)
         and 1 <= s["line"] <= len(lines)),
        key=lambda s: s["line"])
    chunks = []
    if defs:
        first = defs[0]["line"]
        if first > 1 and any(ln.strip()
                             for ln in lines[:first - 1]):
            chunks.extend(_window_chunks(
                rel, lines, 1, first - 1, ""))
        for i, d in enumerate(defs):
            end = (defs[i + 1]["line"] - 1
                   if i + 1 < len(defs) else len(lines))
            chunks.extend(_window_chunks(
                rel, lines, d["line"], end, d.get("name", "")))
    else:
        chunks = _window_chunks(rel, lines, 1, len(lines), "")
    return chunks[:MAX_CHUNKS_PER_FILE]


def cosine(a, b):
    dot = math.fsum(x * y for x, y in zip(a, b))
    na = math.sqrt(math.fsum(x * x for x in a))
    nb = math.sqrt(math.fsum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _pack(vec):
    return struct.pack("<%df" % len(vec),
                       *(float(v) for v in vec))


def _unpack(blob):
    n = len(blob) // 4
    return list(struct.unpack("<%df" % n, blob[:n * 4]))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY, hash TEXT NOT NULL,
    n_chunks INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL, start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL, name TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL, vec BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
"""


class EmbedIndex:
    """SQLite-backed chunk vector store for one workspace."""

    def __init__(self, db_path, model_id=""):
        self.db_path = Path(db_path)
        self.model_id = model_id or ""
        self._conn = None

    def _db(self):
        if self._conn is not None:
            return self._conn
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        # A build writes from a background thread while the
        # status endpoint reads; wait out the write lock
        # instead of erroring the poll.
        conn.execute("PRAGMA busy_timeout = 15000")
        conn.executescript(_SCHEMA)
        stored = self._meta_get(conn, "model")
        if stored is not None and stored != self.model_id:
            # Vectors from a different model live in a
            # different space; ranking across models would
            # be meaningless, so a model change starts the
            # index over (the next sync re-embeds).
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM files")
        self._meta_set(conn, "model", self.model_id)
        conn.commit()
        self._conn = conn
        return conn

    @staticmethod
    def _meta_get(conn, key):
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?",
            (key,)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _meta_set(conn, key, value):
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value))

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def exists(self):
        return self.db_path.is_file()

    def file_hashes(self):
        if not self.exists():
            return {}
        conn = self._db()
        return {p: h for p, h in conn.execute(
            "SELECT path, hash FROM files")}

    def stats(self):
        out = {"exists": self.exists(), "files": 0,
               "chunks": 0, "updated": None,
               "model": self.model_id, "dim": 0}
        if not self.exists():
            return out
        conn = self._db()
        out["files"] = conn.execute(
            "SELECT COUNT(*) FROM files").fetchone()[0]
        out["chunks"] = conn.execute(
            "SELECT COUNT(*) FROM chunks").fetchone()[0]
        upd = self._meta_get(conn, "updated")
        out["updated"] = int(upd) if upd else None
        dim = self._meta_get(conn, "dim")
        out["dim"] = int(dim) if dim else 0
        return out

    def sync(self, file_texts, symbols_by_path, embed_fn,
             progress=None, now=0):
        """Bring the index in line with file_texts
        ({rel: text}). Only files whose content hash moved
        are re-chunked and re-embedded; files missing from
        file_texts are purged. embed_fn(texts) must return
        one vector per text. progress(done, total), when
        given, fires per embedded file. Returns honest
        counts."""
        conn = self._db()
        known = self.file_hashes()
        removed = sorted(set(known) - set(file_texts))
        for rel in removed:
            conn.execute("DELETE FROM chunks WHERE path = ?",
                         (rel,))
            conn.execute("DELETE FROM files WHERE path = ?",
                         (rel,))
        changed = []
        unchanged = 0
        for rel, text in file_texts.items():
            if known.get(rel) == hash_text(text):
                unchanged += 1
            else:
                changed.append(rel)
        embedded_chunks = 0
        dim = 0
        for n, rel in enumerate(sorted(changed)):
            text = file_texts[rel]
            chunks = chunk_text(
                rel, text, (symbols_by_path or {}).get(rel))
            vecs = []
            if chunks:
                vecs = embed_fn([c["text"] for c in chunks])
                if len(vecs) != len(chunks):
                    raise ValueError(
                        "embedder returned %d vectors for %d "
                        "chunks" % (len(vecs), len(chunks)))
                for v in vecs:
                    if dim and len(v) != dim:
                        raise ValueError(
                            "embedder changed dimension "
                            "mid-build (%d != %d)"
                            % (len(v), dim))
                    dim = dim or len(v)
            conn.execute("DELETE FROM chunks WHERE path = ?",
                         (rel,))
            for c, v in zip(chunks, vecs):
                conn.execute(
                    "INSERT INTO chunks(path, start_line, "
                    "end_line, name, text, vec) VALUES"
                    "(?, ?, ?, ?, ?, ?)",
                    (c["path"], c["start_line"], c["end_line"],
                     c["name"], c["text"][:EXCERPT_MAX_CHARS],
                     _pack(v)))
            conn.execute(
                "INSERT INTO files(path, hash, n_chunks) "
                "VALUES(?, ?, ?) ON CONFLICT(path) DO UPDATE "
                "SET hash = excluded.hash, "
                "n_chunks = excluded.n_chunks",
                (rel, hash_text(text), len(chunks)))
            # Commit per file: the write lock is only held
            # briefly, so status polls and searches keep
            # working while a build runs, and a build that
            # dies midway keeps the files it finished.
            conn.commit()
            embedded_chunks += len(chunks)
            if progress:
                progress(n + 1, len(changed))
        if dim:
            self._meta_set(conn, "dim", str(dim))
        if now:
            self._meta_set(conn, "updated", str(int(now)))
        conn.commit()
        st = self.stats()
        return {"files": st["files"], "chunks": st["chunks"],
                "embedded_files": len(changed),
                "embedded_chunks": embedded_chunks,
                "removed_files": len(removed),
                "unchanged_files": unchanged, "dim": dim}

    def search(self, query_vec, limit=10):
        """Brute-force cosine over every stored chunk.
        Returns [{path, start_line, end_line, name, score,
        excerpt}] best first."""
        if not self.exists():
            return []
        conn = self._db()
        limit = max(1, min(int(limit or 10), 50))
        scored = []
        for path, s, e, name, text, blob in conn.execute(
                "SELECT path, start_line, end_line, name, "
                "text, vec FROM chunks"):
            vec = _unpack(blob)
            if len(vec) != len(query_vec):
                continue
            excerpt = ""
            for ln in text.splitlines():
                if ln.strip():
                    excerpt = ln.strip()[:160]
                    break
            scored.append({
                "path": path, "start_line": s, "end_line": e,
                "name": name, "score": cosine(query_vec, vec),
                "excerpt": excerpt})
        scored.sort(key=lambda r: (-r["score"], r["path"],
                                   r["start_line"]))
        return scored[:limit]
