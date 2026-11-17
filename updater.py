#!/usr/bin/env python3
"""GhostIDE desktop auto-update (Desktop Cycle 1, D3).

Packaged-app updates with no silent installs:

    check -> download -> SHA-256 verify -> stage ->
    (user restarts) -> swap at launch -> previous version
    kept for one-click rollback

The manifest is latest.json on the downloads site:

    {"version": "3.11.0", "notes": "...",
     "platforms": {
       "linux":   {"url": "https://.../ghostide-linux-v3.11.0.tar.gz",
                   "sha256": "<hex>", "size": 10166540},
       "macos":   {...}, "windows": {...}}}

Rules:
- Source runs are report-only (check() works; download and
  apply refuse). The packaged app is the only self-updater.
- Verification fails closed: a size or SHA-256 mismatch
  deletes the download and records an error; nothing is
  staged.
- Apply happens at launch, before the server serves: the
  staged package is extracted to a sibling temp folder,
  then two renames swap it with the live app folder. The
  old folder is kept as <app>.previous for rollback.
- Boot confirmation: after a swap the new build must
  reach confirm_boot_ok() (called once the server is up).
  If the next launch finds a swap still unconfirmed, it
  rolls back automatically instead of booting the suspect
  build again.
- Check-on-launch is a setting (settings.json in the
  update folder) and can be turned off in Settings.

CLI (also the self-test path; never points at the live
site unless you pass its URL yourself):
    python3 updater.py selftest
    python3 updater.py check --manifest <path-or-url> \
        --current 3.10.1
    python3 updater.py verify --manifest <path> --file <archive>
"""
import hashlib
import json
import os
import shutil
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

DEFAULT_MANIFEST_URL = \
    "https://downloads.ghostdeveloperstudio.com/latest.json"
USER_AGENT = "GhostIDE-Updater"


class UpdateError(Exception):
    pass


def parse_version(v):
    out = []
    for x in str(v).strip().split("."):
        try:
            out.append(int(x))
        except Exception:
            out.append(0)
    return tuple(out)


def platform_key():
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    if os.name == "nt":
        return "windows"
    return None


def fetch_manifest(source, timeout=10):
    """Load a manifest from an http(s) URL, a file:// URL
    or a plain local path. Returns the parsed dict."""
    source = str(source)
    if source.startswith(("http://", "https://")):
        req = urllib.request.Request(
            source, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    if source.startswith("file://"):
        source = source[len("file://"):]
    return json.loads(Path(source).read_text(encoding="utf-8"))


def check_manifest(manifest, current, plat=None):
    """Pure check: does the manifest offer something newer
    for this platform? Returns a dict the UI can show."""
    plat = plat or platform_key()
    latest = str(manifest.get("version", ""))
    asset = None
    if plat:
        asset = (manifest.get("platforms") or {}).get(plat)
    available = bool(latest) and bool(asset) and \
        parse_version(latest) > parse_version(current)
    return {"ok": True, "current": current, "latest": latest,
            "platform": plat, "updateAvailable": available,
            "asset": asset if available else None,
            "notes": manifest.get("notes", "")}


def verify_file(path, size, sha256):
    """Fail-closed verification of a downloaded archive."""
    p = Path(path)
    if not p.is_file():
        raise UpdateError("download missing: " + str(p))
    if size and p.stat().st_size != int(size):
        raise UpdateError(
            "size mismatch: got %d, manifest says %s"
            % (p.stat().st_size, size))
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if sha256 and h.hexdigest().lower() != str(sha256).lower():
        raise UpdateError("sha256 mismatch: got " + h.hexdigest())
    return True


def _safe_extract(archive, dest):
    """Extract a .tar.gz or .zip package into dest,
    refusing absolute paths and .. traversal."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    name = str(archive).lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(str(archive)) as z:
            for m in z.namelist():
                mp = Path(m)
                if mp.is_absolute() or ".." in mp.parts:
                    raise UpdateError("unsafe path in package: " + m)
            z.extractall(str(dest))
    else:
        with tarfile.open(str(archive)) as t:
            for m in t.getmembers():
                mp = Path(m.name)
                if mp.is_absolute() or ".." in mp.parts:
                    raise UpdateError(
                        "unsafe path in package: " + m.name)
            t.extractall(str(dest), filter="data")


class Updater:
    """Stateful updater rooted at <data_dir>/update."""

    def __init__(self, data_dir, current_version, app_dir=None,
                 frozen=False, manifest_url=None):
        self.root = Path(data_dir) / "update"
        self.current = str(current_version)
        self.app_dir = Path(app_dir) if app_dir else None
        self.frozen = bool(frozen)
        self.manifest_url = manifest_url or DEFAULT_MANIFEST_URL
        self._dl = {"state": "idle", "done": 0, "total": 0,
                    "error": "", "version": ""}

    # ----- small JSON state files -----
    def _read(self, name, default):
        try:
            return json.loads(
                (self.root / name).read_text(encoding="utf-8"))
        except Exception:
            return default

    def _write(self, name, obj):
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / (name + ".tmp")
        tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
        tmp.replace(self.root / name)

    def settings(self):
        s = self._read("settings.json", {})
        return {"checkOnLaunch": bool(s.get("checkOnLaunch", True))}

    def set_settings(self, check_on_launch):
        self._write("settings.json",
                    {"checkOnLaunch": bool(check_on_launch)})
        return self.settings()

    def state(self):
        return self._read("state.json", {})

    def _set_state(self, **kw):
        st = self.state()
        st.update(kw)
        self._write("state.json", st)
        return st

    # ----- check -----
    def run_check(self):
        try:
            m = fetch_manifest(self.manifest_url)
            res = check_manifest(m, self.current)
            self._set_state(lastCheck={
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "latest": res["latest"],
                "updateAvailable": res["updateAvailable"]})
            return res
        except Exception as e:
            return {"ok": False, "current": self.current,
                    "error": "update check failed: " + str(e)}

    # ----- download + stage -----
    def download(self):
        """Synchronous download + verify + stage. Raises
        UpdateError on any failure (fail closed)."""
        if not self.frozen:
            raise UpdateError(
                "source runs are report-only; updates apply "
                "to the packaged app")
        res = self.run_check()
        if not res.get("ok"):
            raise UpdateError(res.get("error", "check failed"))
        if not res.get("updateAvailable"):
            raise UpdateError("no update available")
        asset = res["asset"]
        version = res["latest"]
        stage = self.root / "staging" / version
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir(parents=True, exist_ok=True)
        url = asset["url"]
        fname = url.rsplit("/", 1)[-1] or ("ghostide-" + version)
        dest = stage / fname
        self._dl = {"state": "downloading", "done": 0,
                    "total": int(asset.get("size", 0)),
                    "error": "", "version": version}
        try:
            if url.startswith(("http://", "https://")):
                req = urllib.request.Request(
                    url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=60) as r, \
                        dest.open("wb") as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        self._dl["done"] += len(chunk)
            elif url.startswith("file://"):
                shutil.copyfile(url[len("file://"):], str(dest))
                self._dl["done"] = dest.stat().st_size
            else:
                shutil.copyfile(url, str(dest))
                self._dl["done"] = dest.stat().st_size
            self._dl["state"] = "verifying"
            verify_file(dest, asset.get("size"),
                        asset.get("sha256"))
        except Exception as e:
            self._dl = {"state": "error", "done": 0, "total": 0,
                        "error": str(e), "version": version}
            shutil.rmtree(stage, ignore_errors=True)
            raise UpdateError("download failed verification: "
                              + str(e))
        self._dl["state"] = "staged"
        info = {"version": version, "archive": str(dest),
                "sha256": asset.get("sha256"),
                "size": asset.get("size"),
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        self._set_state(staged=info)
        return info

    def download_async(self):
        import threading
        def _run():
            try:
                self.download()
            except Exception:
                pass  # state already recorded in self._dl
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return t

    # ----- apply / rollback at launch -----
    def _backup_dir(self):
        return self.app_dir.parent / (self.app_dir.name + ".previous")

    def _extract_package(self, archive):
        tmp = self.app_dir.parent / (self.app_dir.name + ".new")
        if tmp.exists():
            shutil.rmtree(tmp)
        _safe_extract(archive, tmp)
        # Packages carry one top folder (GhostIDE/); the app
        # folder's contents live inside it.
        entries = [p for p in tmp.iterdir()]
        if len(entries) == 1 and entries[0].is_dir():
            return entries[0], tmp
        return tmp, tmp

    def _swap(self, new_contents_dir, cleanup=None):
        """Replace the app folder with new_contents_dir,
        keeping the old folder as <app>.previous."""
        app = self.app_dir
        backup = self._backup_dir()
        if backup.exists():
            shutil.rmtree(backup)
        os.rename(str(app), str(backup))
        try:
            os.rename(str(new_contents_dir), str(app))
        except Exception:
            os.rename(str(backup), str(app))
            raise
        if cleanup is not None and Path(cleanup) != app and \
                Path(cleanup).exists():
            shutil.rmtree(cleanup, ignore_errors=True)

    def apply_at_launch(self):
        """Run once at startup, before serving. Returns
        "applied", "rolled-back", "auto-rolled-back" when a
        swap happened (the caller should re-exec), else
        None."""
        if not self.frozen or self.app_dir is None:
            return None
        st = self.state()
        # A previous swap never confirmed its boot: do not
        # trust that build; swap back first.
        if st.get("awaitingConfirm") and self._backup_dir().exists():
            cur = self.app_dir
            bak = self._backup_dir()
            trash = cur.parent / (cur.name + ".suspect")
            if trash.exists():
                shutil.rmtree(trash)
            os.rename(str(cur), str(trash))
            os.rename(str(bak), str(cur))
            shutil.rmtree(trash, ignore_errors=True)
            self._set_state(awaitingConfirm=None, staged=None,
                            pendingApply=None,
                            lastResult="auto-rolled-back")
            return "auto-rolled-back"
        if st.get("pendingRollback") and \
                self._backup_dir().exists():
            bak = self._backup_dir()
            cur = self.app_dir
            trash = cur.parent / (cur.name + ".replaced")
            if trash.exists():
                shutil.rmtree(trash)
            os.rename(str(cur), str(trash))
            os.rename(str(bak), str(cur))
            shutil.rmtree(trash, ignore_errors=True)
            self._set_state(pendingRollback=None, staged=None,
                            pendingApply=None,
                            lastResult="rolled-back")
            return "rolled-back"
        staged = st.get("staged")
        if st.get("pendingApply") and staged:
            archive = staged.get("archive", "")
            # Re-verify before touching the install: the
            # staged bytes must still match the manifest.
            verify_file(archive, staged.get("size"),
                        staged.get("sha256"))
            contents, tmp = self._extract_package(archive)
            self._swap(contents, cleanup=tmp)
            self._set_state(pendingApply=None,
                            awaitingConfirm=staged.get("version"),
                            lastResult="applied "
                                       + str(staged.get("version")))
            return "applied"
        return None

    def confirm_boot_ok(self):
        """Called once the server is up: the running build
        is good, so a pending boot confirmation clears."""
        st = self.state()
        if st.get("awaitingConfirm"):
            self._set_state(awaitingConfirm=None,
                            lastResult="confirmed "
                                       + str(st["awaitingConfirm"]))

    def mark_apply_pending(self):
        st = self.state()
        if not st.get("staged"):
            raise UpdateError("nothing staged to apply")
        self._set_state(pendingApply=st["staged"].get("version"))
        return self.status()

    def mark_rollback_pending(self):
        if not self._backup_dir().exists():
            raise UpdateError("no previous version to roll back to")
        self._set_state(pendingRollback=True)
        return self.status()

    # ----- status for the UI -----
    def status(self):
        st = self.state()
        return {
            "mode": "packaged" if self.frozen else "report-only",
            "current": self.current,
            "manifest": self.manifest_url,
            "settings": self.settings(),
            "lastCheck": st.get("lastCheck"),
            "staged": st.get("staged"),
            "pendingApply": st.get("pendingApply"),
            "pendingRollback": bool(st.get("pendingRollback")),
            "awaitingConfirm": st.get("awaitingConfirm"),
            "backupAvailable": bool(
                self.app_dir and self._backup_dir().exists()),
            "lastResult": st.get("lastResult"),
            "download": dict(self._dl),
        }


# ---------------- CLI + self-test ----------------

def _fixture_tree(base):
    """A tiny fake install + a fake 9.9.9 package, for the
    self-test. Returns (app_dir, manifest_path, archive)."""
    import io
    base = Path(base)
    app = base / "GhostIDE"
    (app / "_internal").mkdir(parents=True)
    (app / "GhostIDE").write_text("old-exe")
    (app / "_internal" / "VERSION").write_text("1.0.0")
    pkg_src = base / "pkgsrc" / "GhostIDE"
    (pkg_src / "_internal").mkdir(parents=True)
    (pkg_src / "GhostIDE").write_text("new-exe")
    (pkg_src / "_internal" / "VERSION").write_text("9.9.9")
    archive = base / "ghostide-linux-v9.9.9.tar.gz"
    with tarfile.open(str(archive), "w:gz") as t:
        t.add(str(pkg_src), arcname="GhostIDE")
    data = archive.read_bytes()
    manifest = {"version": "9.9.9", "notes": "fixture",
                "platforms": {"linux": {
                    "url": "file://" + str(archive),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data)},
                    "macos": {
                    "url": "file://" + str(archive),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data)},
                    "windows": {
                    "url": "file://" + str(archive),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data)}}}
    mpath = base / "latest.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    return app, mpath, archive


def selftest():
    import tempfile
    passed = failed = 0

    def ck(name, cond):
        nonlocal passed, failed
        print(("PASS " if cond else "FAIL ") + name)
        if cond:
            passed += 1
        else:
            failed += 1

    with tempfile.TemporaryDirectory() as td:
        app, mpath, archive = _fixture_tree(Path(td))
        up = Updater(Path(td) / "data", "1.0.0", app_dir=app,
                     frozen=True, manifest_url=str(mpath))
        res = up.run_check()
        ck("check sees the fixture update",
           res.get("updateAvailable") and res.get("latest") == "9.9.9")
        info = up.download()
        ck("download stages after verify",
           info["version"] == "9.9.9"
           and Path(info["archive"]).is_file())
        up.mark_apply_pending()
        action = up.apply_at_launch()
        ck("apply swaps at launch", action == "applied"
           and (app / "GhostIDE").read_text() == "new-exe"
           and (app / "_internal" / "VERSION").read_text() == "9.9.9")
        ck("previous version kept",
           (Path(td) / "GhostIDE.previous" / "GhostIDE")
           .read_text() == "old-exe")
        up.confirm_boot_ok()
        ck("boot confirm clears", not up.state().get("awaitingConfirm"))
        up.mark_rollback_pending()
        action = up.apply_at_launch()
        ck("rollback restores the old build",
           action == "rolled-back"
           and (app / "GhostIDE").read_text() == "old-exe")
        # Tampered manifest hash must fail closed.
        bad = json.loads(mpath.read_text())
        bad["platforms"]["linux"]["sha256"] = "0" * 64
        bad["platforms"]["macos"]["sha256"] = "0" * 64
        bad["platforms"]["windows"]["sha256"] = "0" * 64
        mpath.write_text(json.dumps(bad))
        up2 = Updater(Path(td) / "data2", "1.0.0", app_dir=app,
                      frozen=True, manifest_url=str(mpath))
        try:
            up2.download()
            ck("tampered hash rejected", False)
        except UpdateError:
            ck("tampered hash rejected",
               up2._dl["state"] == "error")
        # Source runs stay report-only.
        up3 = Updater(Path(td) / "data3", "1.0.0", app_dir=app,
                      frozen=False, manifest_url=str(mpath))
        try:
            up3.download()
            ck("source run refuses download", False)
        except UpdateError:
            ck("source run refuses download", True)
        # Unconfirmed boot auto-rolls-back.
        app2, mpath2, _ = _fixture_tree(Path(td) / "case2")
        up4 = Updater(Path(td) / "data4", "1.0.0", app_dir=app2,
                      frozen=True, manifest_url=str(mpath2))
        up4.download()
        up4.mark_apply_pending()
        ck("second apply swaps", up4.apply_at_launch() == "applied")
        # No confirm_boot_ok: next launch must roll back.
        ck("unconfirmed boot auto-rolls-back",
           up4.apply_at_launch() == "auto-rolled-back"
           and (app2 / "GhostIDE").read_text() == "old-exe")
    print("SELFTEST %d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="updater.py")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("selftest")
    c = sub.add_parser("check")
    c.add_argument("--manifest", required=True)
    c.add_argument("--current", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--manifest", required=True)
    v.add_argument("--file", required=True)
    v.add_argument("--platform", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "check":
        m = fetch_manifest(args.manifest)
        print(json.dumps(check_manifest(m, args.current), indent=1))
        return 0
    if args.cmd == "verify":
        m = fetch_manifest(args.manifest)
        plat = args.platform or platform_key()
        asset = (m.get("platforms") or {}).get(plat) or {}
        verify_file(args.file, asset.get("size"),
                    asset.get("sha256"))
        print("verified ok")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
