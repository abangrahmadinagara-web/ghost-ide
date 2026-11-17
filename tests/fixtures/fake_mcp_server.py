#!/usr/bin/env python3
"""Fixture MCP server (stdio, newline-delimited JSON-RPC)
for the D8 MCP-polish tests. Two tools: ``echo`` (annotated
read-only) and ``write_note`` (annotated destructive).
Calls append one line per invocation to the file named by
FAKE_MCP_LOG (when set) so tests can prove a call did or
did not reach the server."""
import json
import os
import sys

TOOLS = [
    {"name": "echo",
     "description": "Echo the text back.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "text": {"type": "string"}}},
     "annotations": {"readOnlyHint": True,
                     "openWorldHint": False}},
    {"name": "write_note",
     "description": "Pretend to write a note.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "text": {"type": "string"}}},
     "annotations": {"destructiveHint": True}},
]


def log(line):
    path = os.environ.get("FAKE_MCP_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def handle(msg):
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        return {"protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": "fake-mcp",
                               "version": "1.0.0"}}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        log("call " + str(name))
        if name == "echo":
            return {"content": [{"type": "text",
                                 "text": "echo: " +
                                 str(args.get("text", ""))}],
                    "isError": False}
        if name == "write_note":
            return {"content": [{"type": "text",
                                 "text": "noted: " +
                                 str(args.get("text", ""))}],
                    "isError": False}
        return None
    if method == "ping":
        return {}
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        result = handle(msg)
        mid = msg.get("id")
        if mid is None:
            continue
        if result is None:
            reply = {"jsonrpc": "2.0", "id": mid, "error": {
                "code": -32602, "message": "unknown tool"}}
        else:
            reply = {"jsonrpc": "2.0", "id": mid,
                     "result": result}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
