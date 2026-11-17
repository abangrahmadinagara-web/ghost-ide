#!/usr/bin/env python3
"""GhostIDE app-data paths and portable mode (Desktop Cycle 1).

One place decides where GhostIDE keeps its own data, so the
server, the updater and the trust store cannot drift apart.

Default mode (unchanged from every release through 3.10.1):
- app data in ~/.ghostide (models, TLS certs, MCP config,
  trust store, update state)
- the seeded first workspace in ~/GhostIDE (packaged app)

Portable mode (--portable on the command line, or the
GHOSTIDE_PORTABLE=1 environment variable): ALL app data
lives in a ghostide-data/ folder that sits beside the
executable in the packaged app, or beside server.py in a
source run:

    ghostide-data/
      workspace/     the seeded first workspace
      models/        downloaded AI models
      llama/         the fetched llama.cpp runtime
      tls/           the LAN pair-mode host certificate
      mcp.json       user MCP server config
      trusted-workspaces.json
      update/        update staging, state and backups

Nothing outside that folder is written by the app in
portable mode (the user's own project folders aside).
"""
import os
import sys
from pathlib import Path

PORTABLE_ENV = "GHOSTIDE_PORTABLE"
_TRUTHY = ("1", "true", "yes", "on")


def portable_requested(argv=None, env=None):
    """True when portable mode was asked for, by flag or env."""
    argv = sys.argv if argv is None else argv
    env = os.environ if env is None else env
    if "--portable" in argv:
        return True
    return str(env.get(PORTABLE_ENV, "")).strip().lower() in _TRUTHY


def is_frozen(frozen=None):
    if frozen is not None:
        return bool(frozen)
    return bool(getattr(sys, "frozen", False))


def app_base(root, frozen=None, argv0=None):
    """The folder the app lives in: beside the executable
    when frozen, the repo root (server.py's folder) in a
    source run."""
    if is_frozen(frozen):
        return Path(argv0 or sys.executable).resolve().parent
    return Path(root).resolve()


def data_dir(root, frozen=None, argv=None, env=None, argv0=None):
    """Where app data lives (see module docstring)."""
    if portable_requested(argv, env):
        return app_base(root, frozen=frozen, argv0=argv0) / "ghostide-data"
    return Path.home() / ".ghostide"


def default_workspace(root, frozen=None, argv=None, env=None,
                      argv0=None):
    """The seeded first workspace, or None when the caller
    keeps its historical default (a source run's playground).
    Portable mode always seeds inside ghostide-data, in both
    source and packaged runs, so a portable copy is fully
    self-contained."""
    if portable_requested(argv, env):
        return data_dir(root, frozen=frozen, argv=argv, env=env,
                        argv0=argv0) / "workspace"
    if is_frozen(frozen):
        return Path.home() / "GhostIDE"
    return None
