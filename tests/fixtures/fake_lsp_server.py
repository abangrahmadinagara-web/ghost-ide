#!/usr/bin/env python3
"""A fake language server for the LSP manager tests.

Speaks just enough JSON-RPC over stdio to prove the
manager's framing, lifecycle, diagnostics round-trip,
requests and crash handling without a real server:

- initialize -> fixed capabilities + serverInfo
- didOpen -> publishDiagnostics with one fake diagnostic
- hover / definition / completion / documentSymbol ->
  fixed, recognisable results
- shutdown / exit handled properly

Environment knobs:
- FAKE_LSP_COUNT_FILE: each launch appends a line, so
  a test can count (re)starts.
- FAKE_LSP_DIE_AFTER_INIT=1: exit(3) immediately after
  answering initialize, simulating a crash loop.
"""
import json
import os
import sys


def send(obj):
    body = json.dumps(obj).encode("utf-8")
    sys.stdout.buffer.write(
        b"Content-Length: %d\r\n\r\n" % len(body) + body)
    sys.stdout.buffer.flush()


def read_message():
    length = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1])
    if length is None:
        return None
    body = b""
    while len(body) < length:
        chunk = sys.stdin.buffer.read(length - len(body))
        if not chunk:
            return None
        body += chunk
    return json.loads(body.decode("utf-8"))


def main():
    count_file = os.environ.get("FAKE_LSP_COUNT_FILE")
    if count_file:
        with open(count_file, "a", encoding="utf-8") as f:
            f.write("start\n")
    die = os.environ.get("FAKE_LSP_DIE_AFTER_INIT") == "1"
    while True:
        msg = read_message()
        if msg is None:
            return
        method = msg.get("method")
        mid = msg.get("id")
        params = msg.get("params") or {}
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "capabilities": {
                    "textDocumentSync": 1,
                    "hoverProvider": True,
                    "definitionProvider": True,
                    "completionProvider": {
                        "triggerCharacters": ["."]},
                    "documentSymbolProvider": True,
                },
                "serverInfo": {"name": "fake-lsp",
                               "version": "0.0-test"},
            }})
            if die:
                os._exit(3)
        elif method == "textDocument/didOpen":
            uri = params["textDocument"]["uri"]
            send({"jsonrpc": "2.0",
                  "method": "textDocument/publishDiagnostics",
                  "params": {"uri": uri, "diagnostics": [{
                      "range": {
                          "start": {"line": 0, "character": 0},
                          "end": {"line": 0, "character": 3}},
                      "severity": 2,
                      "message": "fake diagnostic",
                      "source": "fake-lsp",
                  }]}})
        elif method == "textDocument/hover":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "contents": {"kind": "plaintext",
                             "value": "fake hover text"}}})
        elif method == "textDocument/definition":
            uri = params["textDocument"]["uri"]
            send({"jsonrpc": "2.0", "id": mid, "result": [{
                "uri": uri,
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 3}}}]})
        elif method == "textDocument/completion":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "isIncomplete": False,
                "items": [
                    {"label": "fake_alpha", "kind": 3},
                    {"label": "fake_beta", "kind": 6,
                     "detail": "fake detail"},
                ]}})
        elif method == "textDocument/documentSymbol":
            send({"jsonrpc": "2.0", "id": mid, "result": [{
                "name": "fake_symbol", "kind": 12,
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 3}},
                "selectionRange": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 3}}}]})
        elif method == "shutdown":
            send({"jsonrpc": "2.0", "id": mid, "result": None})
        elif method == "exit":
            return
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "result": None})


if __name__ == "__main__":
    main()
