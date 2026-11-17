#!/usr/bin/env python3
"""Unit tests for Desktop Cycle 2 D8 (MCP polish) against
the server module imported with a temp HOME and workspace:
entry validation, per-tool policy round-trip and
server-side refusal, paste-import with per-entry errors,
header splitting (credentials land in app data, never in
a config file, never in a view), the effective-config
merge, and the OAuth flow functions against a fixture
authorization server (discovery, dynamic registration,
PKCE exchange, refresh)."""
import base64
import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_TD = Path(tempfile.mkdtemp(prefix="ghostide-d8-"))
_WS = _TD / "ws"
_WS.mkdir()
_HOME = _TD / "home"
_HOME.mkdir()
_SAVED_ENV = {k: os.environ.get(k)
              for k in ("HOME", "GHOST_IDE_WORKSPACE")}
os.environ["HOME"] = str(_HOME)
os.environ["GHOST_IDE_WORKSPACE"] = str(_WS)

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import server  # noqa: E402

# server.py binds its paths at import time, so the temp
# env has done its job; restore the real one so sibling
# test modules (subprocess cases read os.environ) are
# not contaminated.
for _k, _v in _SAVED_ENV.items():
    if _v is None:
        os.environ.pop(_k, None)
    else:
        os.environ[_k] = _v


class TestValidation(unittest.TestCase):
    def test_good_entries(self):
        self.assertEqual(server.mcp_validate_entry(
            "fx", {"command": "npx", "args": ["-y", "p"]}),
            [])
        self.assertEqual(server.mcp_validate_entry(
            "web", {"url": "https://x.example/mcp"}), [])

    def test_bad_entries(self):
        errs = server.mcp_validate_entry(
            "bad name!", {"command": "npx"})
        self.assertTrue(any("name" in e for e in errs))
        errs = server.mcp_validate_entry("x", {})
        self.assertTrue(any("command" in e for e in errs))
        errs = server.mcp_validate_entry(
            "x", {"command": "npx", "url": "https://y"})
        self.assertTrue(any("either" in e for e in errs))
        errs = server.mcp_validate_entry(
            "x", {"command": "npx", "args": "oops"})
        self.assertTrue(any("args" in e for e in errs))
        errs = server.mcp_validate_entry(
            "x", {"url": "https://y", "headers": {"A": 3}})
        self.assertTrue(any("headers" in e for e in errs))


class TestPolicy(unittest.TestCase):
    def test_defaults_and_round_trip(self):
        self.assertEqual(
            server.mcp_tool_setting("fx", "echo"),
            {"enabled": True, "approval": "ask"})
        server.mcp_policy_set(
            {"server": "fx", "tool": "echo",
             "approval": "allow"})
        self.assertEqual(
            server.mcp_tool_setting("fx", "echo"),
            {"enabled": True, "approval": "allow"})
        server.mcp_policy_set(
            {"server": "fx", "tool": "echo",
             "enabled": False})
        self.assertFalse(
            server.mcp_tool_setting("fx", "echo")["enabled"])
        self.assertTrue(
            (_WS / ".ghostide" / "mcp-policy.json").is_file())
        view = server.mcp_policy_view()
        self.assertEqual(
            view["servers"]["fx"]["tools"]["echo"],
            {"enabled": False, "approval": "allow"})

    def test_bad_approval_rejected(self):
        with self.assertRaises(ValueError):
            server.mcp_policy_set(
                {"server": "fx", "tool": "echo",
                 "approval": "sometimes"})

    def test_call_refusal_is_server_side(self):
        calls = []
        orig = server._mcp_cli
        server._mcp_cli = lambda args, timeout=50: \
            calls.append(args) or {"ok": True}
        try:
            server.mcp_policy_set(
                {"server": "fx", "tool": "echo",
                 "enabled": False})
            with self.assertRaises(ValueError) as cm:
                server.mcp_call("fx", "echo", {})
            self.assertIn("disabled", str(cm.exception))
            self.assertEqual(calls, [])
            server.mcp_policy_set(
                {"server": "fx", "tool": "echo",
                 "enabled": True})
            self.assertEqual(
                server.mcp_call("fx", "echo", {}),
                {"ok": True})
            self.assertEqual(len(calls), 1)
            server.mcp_policy_set(
                {"server": "fx", "server_enabled": False})
            with self.assertRaises(ValueError):
                server.mcp_call("fx", "echo", {})
            self.assertEqual(len(calls), 1)
        finally:
            server._mcp_cli = orig
            server.mcp_policy_set(
                {"server": "fx", "server_enabled": True})


class TestManagedConfig(unittest.TestCase):
    TOKEN = "Bearer unit-test-token-123"

    def test_headers_split_out_and_redacted(self):
        server.mcp_save_user_server("web", {
            "url": "https://api.example/mcp",
            "headers": {"Authorization": self.TOKEN}})
        user_cfg = json.loads(
            server.mcp_user_config_path().read_text())
        self.assertNotIn(self.TOKEN,
                         json.dumps(user_cfg))
        secrets = json.loads(
            server.mcp_secrets_path().read_text())
        self.assertEqual(
            secrets["web"]["headers"]["Authorization"],
            self.TOKEN)
        view = server.mcp_config_view()
        self.assertNotIn(self.TOKEN, json.dumps(view))
        mine = [s for s in view["servers"]
                if s["name"] == "web"][0]
        self.assertEqual(mine["header_names"],
                         ["Authorization"])
        self.assertEqual(mine["label"], "Remote server")

    def test_effective_config_merges_headers(self):
        # The user config is the active config here (the
        # temp workspace has no .mcp.json yet).
        server.mcp_save_user_server("web", {
            "url": "https://api.example/mcp",
            "headers": {"Authorization": self.TOKEN}})
        eff = server.mcp_active_config_path()
        data = json.loads(Path(eff).read_text())
        entry = data["mcpServers"]["web"]
        self.assertEqual(
            entry["headers"]["Authorization"], self.TOKEN)
        self.assertEqual(
            str(server.DATA_DIR / "mcp-effective.json"),
            str(eff))

    def test_import_reports_per_entry_errors(self):
        out = server.mcp_import_config(json.dumps({
            "mcpServers": {
                "good": {"command": "npx",
                         "args": ["-y", "pkg"]},
                "bad": {"args": []},
                "tok": {"url": "https://t.example/mcp",
                        "headers": {
                            "Authorization": "Bearer z"}}}}))
        self.assertEqual(sorted(out["imported"]),
                         ["good", "tok"])
        self.assertIn("bad", out["errors"])
        user_cfg = json.loads(
            server.mcp_user_config_path().read_text())
        self.assertNotIn("Bearer z",
                         json.dumps(user_cfg))
        with self.assertRaises(ValueError):
            server.mcp_import_config("{not json")


class OAuthFixture:
    """Discovery + registration + authorize + token with
    a real S256 PKCE check, in one threading server."""

    def __init__(self):
        self.codes = {}
        self.refresh_count = 0
        self.token_n = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, obj, status=200):
                data = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type",
                                 "application/json")
                self.send_header("Content-Length",
                                 str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                origin = "http://127.0.0.1:%d" % \
                    outer.server.server_address[1]
                if u.path == "/.well-known/" \
                        "oauth-protected-resource":
                    return self._json({
                        "resource": origin + "/mcp",
                        "authorization_servers": [origin]})
                if u.path in (
                        "/.well-known/"
                        "oauth-authorization-server",
                        "/.well-known/openid-configuration"):
                    return self._json({
                        "issuer": origin,
                        "authorization_endpoint":
                            origin + "/authorize",
                        "token_endpoint": origin + "/token",
                        "registration_endpoint":
                            origin + "/register"})
                if u.path == "/authorize":
                    q = urllib.parse.parse_qs(u.query)
                    outer.codes["authcode"] = {
                        "challenge":
                            q["code_challenge"][0],
                        "client_id": q["client_id"][0]}
                    loc = q["redirect_uri"][0] + \
                        "?code=authcode&state=" + \
                        q["state"][0]
                    self.send_response(302)
                    self.send_header("Location", loc)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                return self._json({"error": "nope"}, 404)

            def do_POST(self):
                n = int(self.headers.get("Content-Length")
                        or 0)
                body = self.rfile.read(n).decode() \
                    if n else ""
                u = urllib.parse.urlparse(self.path)
                if u.path == "/register":
                    return self._json(
                        {"client_id": "fixture-client"})
                if u.path == "/token":
                    form = urllib.parse.parse_qs(body)
                    grant = form.get(
                        "grant_type", [""])[0]
                    if grant == "authorization_code":
                        rec = outer.codes.get(
                            form.get("code", [""])[0])
                        if rec is None:
                            return self._json(
                                {"error": "bad code"}, 400)
                        verifier = form.get(
                            "code_verifier", [""])[0]
                        digest = base64.urlsafe_b64encode(
                            hashlib.sha256(
                                verifier.encode()).digest()
                        ).decode().rstrip("=")
                        if digest != rec["challenge"]:
                            return self._json(
                                {"error": "bad pkce"}, 400)
                    elif grant == "refresh_token":
                        outer.refresh_count += 1
                        if form.get("refresh_token",
                                    [""])[0] != "ref-1":
                            return self._json(
                                {"error": "bad refresh"},
                                400)
                    else:
                        return self._json(
                            {"error": "bad grant"}, 400)
                    outer.token_n += 1
                    return self._json({
                        "access_token":
                            "acc-%d" % outer.token_n,
                        "refresh_token": "ref-1",
                        "expires_in": 3600,
                        "token_type": "Bearer"})
                return self._json({"error": "nope"}, 404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                          Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)

    @property
    def origin(self):
        return "http://127.0.0.1:%d" % \
            self.server.server_address[1]

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        return False


class TestOAuth(unittest.TestCase):
    def test_start_exchange_refresh(self):
        with OAuthFixture() as fx:
            (_WS / ".mcp.json").write_text(json.dumps({
                "mcpServers": {"oauthfx": {
                    "url": fx.origin + "/mcp",
                    "oauth": True}}}))
            out = server.mcp_oauth_start("oauthfx")
            self.assertIn("/authorize?", out["auth_url"])
            q = urllib.parse.parse_qs(urllib.parse
                                        .urlparse(
                                            out["auth_url"])
                                        .query)
            self.assertEqual(q["client_id"],
                             ["fixture-client"])
            self.assertEqual(
                q["code_challenge_method"], ["S256"])
            state = q["state"][0]
            # Drive the authorize step like a browser
            # would (without following the redirect): it
            # registers the PKCE challenge with the
            # fixture and 302s back to our callback.
            class NoRedirect(urllib.request
                             .HTTPRedirectHandler):
                def redirect_request(self, *a):
                    return None
            opener = urllib.request.build_opener(
                NoRedirect)
            try:
                opener.open(out["auth_url"], timeout=10)
                self.fail("authorize should redirect")
            except urllib.error.HTTPError as e:
                loc = e.headers.get("Location")
            cb = urllib.parse.parse_qs(
                urllib.parse.urlparse(loc).query)
            self.assertEqual(cb["state"], [state])
            name = server.mcp_oauth_exchange(
                state, cb["code"][0])
            self.assertEqual(name, "oauthfx")
            self.assertEqual(
                server.mcp_oauth_token_for(
                    "oauthfx", {"url": fx.origin + "/mcp"}),
                "acc-1")
            # Expire the stored token: the next read must
            # refresh exactly once and hand back acc-2.
            store = server.mcp_oauth_all()
            store["oauthfx"]["expires_at"] = 1
            server._mcp_write_json(server.mcp_oauth_path(),
                                   store, secret=True)
            self.assertEqual(
                server.mcp_oauth_token_for(
                    "oauthfx", {"url": fx.origin + "/mcp"}),
                "acc-2")
            self.assertEqual(fx.refresh_count, 1)
            # The effective config carries the fresh token.
            eff = server.mcp_active_config_path()
            data = json.loads(Path(eff).read_text())
            self.assertEqual(
                data["mcpServers"]["oauthfx"]
                ["headers"]["Authorization"],
                "Bearer acc-2")

    def test_state_is_single_use(self):
        with self.assertRaises(ValueError):
            server.mcp_oauth_exchange("no-such-state", "x")


if __name__ == "__main__":
    unittest.main()
