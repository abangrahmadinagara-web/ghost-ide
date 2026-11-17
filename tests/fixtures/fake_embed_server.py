#!/usr/bin/env python3
"""Fake llama-server for the D5 embedding tests.

Stands in for the real binary at the engine seam: it
accepts the same command line GhostIDE spawns (--model,
--embedding, --port, ...) and serves the two routes the
engine uses, GET /health and POST /v1/embeddings. The
embeddings are deterministic concept vectors, not a real
model: marker words land on dedicated dimensions (sleep
-> 0, urlopen -> 1, digit -> 2) and every other word is
hashed thinly over the remaining dimensions, so marker
chunks rank first for marker queries while the full
spawn, batch, store and search pipeline runs for real.
"""
import hashlib
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, \
    ThreadingHTTPServer

DIM = 8
MARKERS = {"sleep": 0, "urlopen": 1, "digit": 2}


def embed(text):
    for prefix in ("search_document: ", "search_query: "):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    vec = [0.0] * DIM
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        hit = False
        for marker, dim in MARKERS.items():
            if marker in w:
                vec[dim] += 1.0
                hit = True
        if not hit:
            h = hashlib.sha256(w.encode()).digest()
            vec[3 + h[0] % (DIM - 3)] += 0.05
    if not any(vec):
        vec[DIM - 1] = 1.0
    return vec


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"status": "ok"}).encode()
            self.send_response(200)
        else:
            body = b"{}"
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(n) or b"{}")
        inputs = data.get("input") or []
        if isinstance(inputs, str):
            inputs = [inputs]
        out = {"data": [
            {"index": i, "embedding": embed(t)}
            for i, t in enumerate(inputs)]}
        body = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    port = 8081
    args = sys.argv[1:]
    if "--port" in args:
        port = int(args[args.index("--port") + 1])
    ThreadingHTTPServer(("127.0.0.1", port),
                        Handler).serve_forever()


if __name__ == "__main__":
    main()
