# GhostIDE for desktop

An offline AI code editor. Your code never leaves the
machine; the AI model runs locally through llama.cpp.

## Install

- Linux: unpack, then `./install.sh`
- macOS: unpack, then `./install.sh` (unsigned app:
  right-click, Open, the first time)
- Windows: unzip, then `install.bat`

Then run GhostIDE. It starts a local server and the IDE
opens at http://127.0.0.1:5173 - your browser is the
window. Your first workspace is created in ~/GhostIDE
(Windows: %USERPROFILE%\GhostIDE); open any other folder
from inside the IDE.

## The AI model and llama.cpp

Neither the 7B model nor the llama.cpp runtime is in
this package - that is what keeps it small. Both are
one-time downloads, and the editor works without either
of them; only the AI waits.

The llama.cpp runtime (about 18 MB) is fetched the
first time you press "Start model", or ahead of time in
Settings, "Download llama.cpp". It is a pinned release
(llama.cpp b11429), verified by SHA-256 before it is
used, and it resumes if interrupted. If you already
have a llama-server of your own, point
LLAMA_SERVER_BIN at it and nothing is downloaded.

The 7B model (Qwen2.5-Coder-7B, 4.7 GB): in the IDE,
Settings, "Download 7B model". Verified by SHA-256 and
resumable, the same way. Prefer your own GGUF? Point
Settings, "Model file" at it instead, or set
GHOST_IDE_MODEL.

## Updates

The packaged app can update itself, with your say-so
at every step and nothing ever installed silently:

1. On launch it checks `latest.json` on the downloads
   site (turn this off in Settings, "Check on launch").
   Settings, "Check for updates" also compares against
   the release note at any time; that check only
   reports.
2. "Download update" fetches the package for your OS
   and verifies it against the manifest's SHA-256 and
   size. A mismatch deletes the download and stops -
   verification fails closed.
3. "Restart to apply" stages the swap: at the next
   launch the new build replaces the app folder, and
   the previous version is kept beside it. If the new
   build fails to start, the next launch rolls back to
   it automatically; you can also roll back yourself
   in Settings, "Roll back to previous".

## Portable mode

Run the app with `--portable` (or set
`GHOSTIDE_PORTABLE=1`) and everything GhostIDE keeps
lives in one `ghostide-data/` folder beside the
executable: the seeded workspace, downloaded models,
the llama.cpp runtime, the pair-mode certificate, MCP
config, trust decisions and update state. Copy the
folder and the app folder together and the whole
setup travels. Without the flag, data lives in
`~/.ghostide` and the first workspace in `~/GhostIDE`,
as before.

## Workspace trust and AGENTS.md

The folder you launch in is trusted. A folder you open
later starts untrusted: you can read and edit files,
but the agent, the terminal and MCP tools stay off
(the server refuses them, not just the buttons) until
you choose "Trust this workspace". The decision is
remembered per folder.

If a workspace has an `AGENTS.md` file at its root,
its contents join the agent's system context on every
chat and agent request; a `.ghostide/AGENTS.md` file
overrides it when both exist. Settings shows which
instruction files are loaded for the open workspace.

## Notes

- Features that run your code (Run, tests, debugger,
  REPL) use a Python 3 found on your PATH in the
  packaged app. Everything else - editing, the AI,
  git (system git), terminal, pair mode, backups -
  works from the package alone.
- LAN / pair mode: set GHOST_IDE_HOST=0.0.0.0 before
  launching to let your phone join this workspace
  (trusted networks only - there is no login).
