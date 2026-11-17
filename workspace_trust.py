#!/usr/bin/env python3
"""Workspace trust + workspace instructions (Desktop Cycle 1, D7).

Two small, separately testable pieces:

1. TrustStore: per-folder trust decisions, persisted as
   JSON in the app data dir (ghost_paths.data_dir, so it
   follows portable mode). A workspace the user has never
   decided on is UNTRUSTED: the server refuses agent
   actions, terminal execution and MCP tool calls for it
   at the API layer until the user trusts it. Decisions:
     {"version": 1, "workspaces": {
        "/abs/path": {"trusted": true, "source": "user",
                      "updated": "2026-11-17T..."}}}

2. Workspace instructions: AGENTS.md at the workspace
   root is the cross-tool instruction file; a
   .ghostide/AGENTS.md in the workspace OVERRIDES it when
   both exist (the project-local private copy wins). The
   server folds the loaded text into the system context
   of every chat/agent request, and the UI lists which
   files were loaded.
"""
import json
import time
from pathlib import Path

INSTRUCTIONS_CAP = 32000  # chars; a runaway file cannot eat the context


def _key(workspace):
    return str(Path(workspace).expanduser().resolve())


class TrustStore:
    def __init__(self, path):
        self.path = Path(path)
        self._data = None

    def _load(self):
        if self._data is None:
            try:
                self._data = json.loads(
                    self.path.read_text(encoding="utf-8"))
                if not isinstance(self._data.get("workspaces"), dict):
                    self._data = {"version": 1, "workspaces": {}}
            except Exception:
                self._data = {"version": 1, "workspaces": {}}
        return self._data

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._load(), indent=1),
                       encoding="utf-8")
        tmp.replace(self.path)

    def decision(self, workspace):
        """True (trusted), False (decided untrusted) or
        None (never decided -> untrusted, prompt once)."""
        rec = self._load()["workspaces"].get(_key(workspace))
        if rec is None:
            return None
        return bool(rec.get("trusted"))

    def is_trusted(self, workspace):
        return self.decision(workspace) is True

    def set(self, workspace, trusted, source="user"):
        self._load()["workspaces"][_key(workspace)] = {
            "trusted": bool(trusted),
            "source": source,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        self._save()

    def known(self):
        return dict(self._load()["workspaces"])


def load_instructions(workspace):
    """The instruction files in force for a workspace:
    a list of {"rel", "chars", "text"} (at most one entry:
    the .ghostide override replaces the root file)."""
    ws = Path(workspace)
    override = ws / ".ghostide" / "AGENTS.md"
    root = ws / "AGENTS.md"
    if override.is_file():
        chosen = [(".ghostide/AGENTS.md", override)]
    elif root.is_file():
        chosen = [("AGENTS.md", root)]
    else:
        chosen = []
    out = []
    for rel, p in chosen:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if len(text) > INSTRUCTIONS_CAP:
            text = text[:INSTRUCTIONS_CAP] + \
                "\n[... truncated by GhostIDE at %d chars ...]" \
                % INSTRUCTIONS_CAP
        out.append({"rel": rel, "chars": len(text), "text": text})
    return out


def instructions_block(workspace):
    """The system-context block for chat/agent requests,
    or "" when the workspace carries no instructions."""
    parts = []
    for f in load_instructions(workspace):
        parts.append("Workspace instructions (from %s; treat "
                     "them as the project's own rules):\n%s"
                     % (f["rel"], f["text"].strip()))
    return "\n\n".join(parts)
