"""Tests for gtasks.auth: Token, TokenStore, Credentials, config loading and OAuth flow."""

import json
import os
import stat
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit, parse_qs

import gtasks.auth as auth_module
from gtasks.auth import (
    Credentials,
    OAuth2Flow,
    Token,
    TokenStore,
    create_credentials,
    load_client_config,
)
from gtasks.errors import AuthError

from tests.support import (
    browser_opener,
    json_response,
    patch_browser,
    patch_urlopen,
    request_form,
    s256,
)

NOW = 1_700_000_000.0

INSTALLED = {
    "client_id": "cid.apps.googleusercontent.com",
    "client_secret": "client-secret",
    "auth_uri": "https://accounts.example.test/o/oauth2/auth",
    "token_uri": "https://oauth2.example.test/token",
    "redirect_uris": ["http://127.0.0.1"],
}


def token_json(**overrides):
    payload = {
        "access_token": "new-access",
        "expires_in": 3600,
        "refresh_token": "refresh-1",
        "token_type": "Bearer",
        "scope": "https://www.googleapis.com/auth/tasks",
    }
    payload.update(overrides)
    return json_response(payload)


@contextmanager
def clean_env(**values):
    keys = ("GTASKS_CLIENT_SECRET", "GTASKS_TOKEN")
    original = {key: os.environ.get(key) for key in keys}
    for key in keys:
        os.environ.pop(key, None)
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class AuthTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.token_path = self.dir / "token.json"
        self.store = TokenStore(self.token_path)
        self.config = dict(INSTALLED)

    def write_client_secret(self, name="client_secret.json", section="installed"):
        path = self.dir / name
        path.write_text(json.dumps({section: dict(INSTALLED)}), encoding="utf-8")
        return path


class TokenTests(unittest.TestCase):
    def test_expired(self):
        now = time.time()
        self.assertFalse(Token("a", expires_at=now + 3600).expired())
        self.assertTrue(Token("a", expires_at=now - 1).expired())
        self.assertTrue(Token("a", expires_at=now + 30).expired())
        self.assertTrue(Token("a", expires_at=now + 30).expired(skew=60.0))
        self.assertFalse(Token("a", expires_at=now + 120).expired(skew=60.0))

    def test_authorization(self):
        self.assertEqual(Token("abc").authorization, "Bearer abc")
        self.assertEqual(Token("abc", token_type="Custom").authorization, "Custom abc")

    def test_json_roundtrip(self):
        token = Token(
            access_token="a",
            refresh_token="r",
            expires_at=123.5,
            token_type="Bearer",
            scope="s",
        )
        self.assertEqual(Token.from_json(token.to_json()), token)

    def test_from_json_defaults(self):
        token = Token.from_json({"access_token": "a"})
        self.assertEqual(token.access_token, "a")
        self.assertIsNone(token.refresh_token)
        self.assertEqual(token.expires_at, 0.0)
        self.assertEqual(token.token_type, "Bearer")
        self.assertEqual(token.scope, "")


class TokenStoreTests(AuthTestCase):
    def test_save_load_roundtrip_and_atomic_file(self):
        token = Token("access", refresh_token="refresh", expires_at=NOW + 60)
        nested = self.dir / "nested" / "deeper" / "token.json"
        store = TokenStore(nested)
        store.save(token)
        self.assertTrue(nested.exists())
        self.assertEqual(stat.S_IMODE(nested.stat().st_mode), 0o600)
        self.assertEqual(store.load(), token)
        self.assertEqual(sorted(path.name for path in nested.parent.iterdir()), ["token.json"])

    def test_save_overwrites(self):
        self.store.save(Token("one"))
        self.store.save(Token("two"))
        self.assertEqual(self.store.load().access_token, "two")

    def test_load_missing_returns_none(self):
        self.assertIsNone(self.store.load())

    def test_load_invalid_json_returns_none(self):
        self.token_path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(self.store.load())

    def test_clear_removes_file_and_tolerates_missing(self):
        self.store.save(Token("one"))
        self.store.clear()
        self.assertFalse(self.token_path.exists())
        self.store.clear()


class LoadClientConfigTests(AuthTestCase):
    def test_installed_section_flattened(self):
        path = self.write_client_secret()
        config = load_client_config(path)
        for key, value in INSTALLED.items():
            self.assertEqual(config[key], value)
        self.assertNotIn("installed", config)

    def test_web_section_flattened(self):
        path = self.write_client_secret(section="web")
        config = load_client_config(path)
        self.assertEqual(config["client_id"], INSTALLED["client_id"])
        self.assertEqual(config["token_uri"], INSTALLED["token_uri"])
        self.assertNotIn("web", config)


class CredentialsTests(AuthTestCase):
    def make_credentials(self):
        return Credentials(self.config, self.store)

    def test_missing_token_raises_auth_error(self):
        credentials = self.make_credentials()
        self.assertIsNone(credentials.token)
        with self.assertRaises(AuthError):
            credentials.access_token()

    def test_valid_token_returned_without_refresh(self):
        self.store.save(Token("valid-access", expires_at=time.time() + 3600))
        credentials = self.make_credentials()
        self.assertEqual(credentials.token.access_token, "valid-access")
        with patch_urlopen("gtasks.auth", []) as recorder:
            self.assertEqual(credentials.access_token(), "valid-access")
        self.assertEqual(recorder.calls, [])

    def test_expired_token_refreshed_and_saved(self):
        self.store.save(
            Token("old-access", refresh_token="refresh-1", expires_at=time.time() - 10)
        )
        credentials = self.make_credentials()
        with patch_urlopen("gtasks.auth", [token_json()]) as recorder:
            access = credentials.access_token()
        self.assertEqual(access, "new-access")
        saved = self.store.load()
        self.assertEqual(saved.access_token, "new-access")
        self.assertEqual(saved.refresh_token, "refresh-1")
        self.assertGreater(saved.expires_at, time.time())

        request = recorder.requests[0]
        self.assertEqual(request.url, INSTALLED["token_uri"])
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.headers.get("content-type"), "application/x-www-form-urlencoded")
        form = request_form(request)
        self.assertEqual(form["grant_type"], ["refresh_token"])
        self.assertEqual(form["refresh_token"], ["refresh-1"])
        self.assertEqual(form["client_id"], [INSTALLED["client_id"]])
        self.assertEqual(form["client_secret"], [INSTALLED["client_secret"]])

    def test_expired_without_refresh_token_raises_auth_error(self):
        self.store.save(Token("old-access", expires_at=time.time() - 10))
        with self.assertRaises(AuthError):
            self.make_credentials().access_token()

    def test_refresh_without_access_token_raises_auth_error(self):
        self.store.save(Token("old", refresh_token="r", expires_at=time.time() - 10))
        with patch_urlopen("gtasks.auth", [json_response({"expires_in": 3600})]):
            with self.assertRaises(AuthError):
                self.make_credentials().access_token()

    def test_refresh_keeps_existing_refresh_token(self):
        self.store.save(Token("old", refresh_token="keep-me", expires_at=time.time() - 10))
        with patch_urlopen(
            "gtasks.auth", [token_json(refresh_token=None)]
        ):
            self.make_credentials().access_token()
        self.assertEqual(self.store.load().refresh_token, "keep-me")

    def test_refresh_returns_token(self):
        self.store.save(Token("old", refresh_token="r", expires_at=time.time() - 10))
        with patch_urlopen("gtasks.auth", [token_json()]):
            refreshed = self.make_credentials().refresh()
        self.assertEqual(refreshed.access_token, "new-access")
        self.assertEqual(self.store.load().access_token, "new-access")


class CreateCredentialsTests(AuthTestCase):
    def test_explicit_paths(self):
        secret = self.write_client_secret()
        credentials = create_credentials(secret, self.token_path)
        self.assertIsInstance(credentials, Credentials)
        self.assertIsNone(credentials.token)

    def test_env_paths(self):
        secret = self.write_client_secret()
        with clean_env(GTASKS_CLIENT_SECRET=str(secret), GTASKS_TOKEN=str(self.token_path)):
            credentials = create_credentials()
        self.assertIsInstance(credentials, Credentials)
        self.assertIsNone(credentials.token)

    def test_missing_client_secret_file_raises_auth_error(self):
        with self.assertRaises(AuthError):
            create_credentials(self.dir / "does-not-exist.json", self.token_path)

    def test_defaults_client_secret_in_cwd_and_token_path(self):
        self.write_client_secret()
        token_path = self.dir / "default-token.json"
        cwd = os.getcwd()
        with clean_env(), mock.patch.object(
            auth_module, "DEFAULT_TOKEN_PATH", token_path
        ):
            os.chdir(self.dir)
            try:
                credentials = create_credentials()
            finally:
                os.chdir(cwd)
        self.assertIsInstance(credentials, Credentials)


class OAuthFlowConstructionTests(AuthTestCase):
    def test_from_client_secret_file(self):
        secret = self.write_client_secret()
        flow = OAuth2Flow.from_client_secret_file(
            secret, store=self.store, redirect_port=0, open_browser=False
        )
        self.assertIsInstance(flow, OAuth2Flow)


class OAuthFlowLoopbackTests(AuthTestCase):
    def make_flow(self, *, timeout=5.0, open_browser=True):
        secret = self.write_client_secret()
        return OAuth2Flow.from_client_secret_file(
            secret,
            store=self.store,
            redirect_host="127.0.0.1",
            redirect_port=0,
            timeout=timeout,
            open_browser=open_browser,
        )

    def run_authorize(self, flow, wait=8.0):
        result = {}

        def target():
            try:
                result["token"] = flow.authorize()
            except BaseException as exc:  # noqa: BLE001 - reported to the test
                result["error"] = exc

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(wait)
        self.assertFalse(
            thread.is_alive(), "authorize() did not return within the test deadline"
        )
        return result

    def test_authorize_pkce_state_and_token_exchange(self):
        flow = self.make_flow()
        opened = []
        with patch_browser("gtasks.auth", browser_opener(opened, code="code-123")), patch_urlopen(
            "gtasks.auth", [token_json()]
        ) as recorder:
            result = self.run_authorize(flow)

        self.assertNotIn("error", result, result.get("error"))
        token = result["token"]
        self.assertEqual(token.access_token, "new-access")
        self.assertEqual(token.refresh_token, "refresh-1")
        self.assertEqual(self.store.load().access_token, "new-access")

        self.assertEqual(len(opened), 1, "browser flow did not start")
        parts = urlsplit(opened[0])
        query = parse_qs(parts.query)
        self.assertEqual(query["client_id"], [INSTALLED["client_id"]])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["response_type"], ["code"])
        challenge = query["code_challenge"][0]
        self.assertTrue(challenge)
        self.assertTrue(query["state"][0])

        self.assertEqual(len(recorder.requests), 1, "expected one token exchange")
        exchange = recorder.requests[0]
        self.assertEqual(exchange.url, INSTALLED["token_uri"])
        form = request_form(exchange)
        self.assertEqual(form["grant_type"], ["authorization_code"])
        self.assertEqual(form["code"], ["code-123"])
        verifier = form["code_verifier"][0]
        self.assertEqual(s256(verifier), challenge)
        self.assertEqual(form["redirect_uri"], [query["redirect_uri"][0]])

    def test_state_mismatch_raises_auth_error(self):
        flow = self.make_flow()
        with patch_browser(
            "gtasks.auth", browser_opener([], code="code-123", state="wrong-state")
        ), patch_urlopen("gtasks.auth", [token_json()]):
            result = self.run_authorize(flow)
        self.assertNotIn("token", result)
        self.assertIsInstance(result.get("error"), AuthError)

    def test_timeout_raises_auth_error(self):
        flow = self.make_flow(timeout=0.3, open_browser=False)
        with patch_browser("gtasks.auth", lambda url: True):
            result = self.run_authorize(flow, wait=5.0)
        self.assertNotIn("token", result)
        self.assertIsInstance(result.get("error"), AuthError)


if __name__ == "__main__":
    unittest.main()
