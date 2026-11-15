"""OAuth 2.0 authentication and token storage for the Google Tasks API."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from .errors import AuthError

SCOPES = ("https://www.googleapis.com/auth/tasks",)
DEFAULT_TOKEN_PATH = Path.home() / ".config" / "gtasks" / "token.json"

_DEFAULT_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
_DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
_ENV_CLIENT_SECRET = "GTASKS_CLIENT_SECRET"
_ENV_TOKEN = "GTASKS_TOKEN"


@dataclass(slots=True)
class Token:
    """An OAuth 2.0 access token with its optional refresh token."""

    access_token: str
    refresh_token: str | None = None
    expires_at: float = 0.0
    token_type: str = "Bearer"
    scope: str = ""

    def expired(self, skew: float = 60.0) -> bool:
        return time.time() >= self.expires_at - skew

    @property
    def authorization(self) -> str:
        return f"{self.token_type} {self.access_token}"

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "Token":
        return cls(
            access_token=str(data.get("access_token") or ""),
            refresh_token=_optional_str(data.get("refresh_token")),
            expires_at=_float_or_zero(data.get("expires_at")),
            token_type=str(data.get("token_type") or "Bearer"),
            scope=str(data.get("scope") or ""),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "token_type": self.token_type,
            "scope": self.scope,
        }


class TokenStore:
    """Persists a token as a JSON file readable only by its owner."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def load(self) -> Token | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return Token.from_json(data)

    def save(self, token: Token) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(token.to_json(), handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class OAuth2Flow:
    """Runs the installed/web application OAuth 2.0 loopback authorization flow."""

    def __init__(
        self,
        client_config: Mapping[str, Any],
        *,
        scopes: tuple[str, ...] = SCOPES,
        store: TokenStore,
        redirect_host: str = "127.0.0.1",
        redirect_port: int = 0,
        timeout: float = 300.0,
        open_browser: bool = True,
    ) -> None:
        self.client_id = _config_value(client_config, "client_id")
        self.client_secret = _config_value(client_config, "client_secret")
        self.auth_uri = _config_value(client_config, "auth_uri", _DEFAULT_AUTH_URI)
        self.token_uri = _config_value(client_config, "token_uri", _DEFAULT_TOKEN_URI)
        self.scopes = tuple(scopes)
        self.store = store
        self.redirect_host = redirect_host
        self.redirect_port = redirect_port
        self.timeout = timeout
        self.open_browser = open_browser

    @classmethod
    def from_client_secret_file(
        cls,
        path: str | Path,
        *,
        scopes: tuple[str, ...] = SCOPES,
        store: TokenStore | None = None,
        **kwargs: Any,
    ) -> "OAuth2Flow":
        return cls(
            load_client_config(path),
            scopes=scopes,
            store=store or TokenStore(DEFAULT_TOKEN_PATH),
            **kwargs,
        )

    def authorize(self) -> Token:
        code_verifier, code_challenge = _create_pkce_pair()
        state = secrets.token_urlsafe(32)
        received: dict[str, list[str]] = {}
        received_event = threading.Event()
        server = HTTPServer(
            (self.redirect_host, self.redirect_port),
            _create_callback_handler(received, received_event),
        )
        serves = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
        )
        serves.start()
        try:
            port = int(server.server_address[1])
            redirect_uri = f"http://{self.redirect_host}:{port}"
            authorization_url = self._authorization_url(
                redirect_uri, state, code_challenge
            )
            if self.open_browser:
                webbrowser.open(authorization_url)
            if not received_event.wait(self.timeout):
                raise AuthError("timed out waiting for the OAuth authorization response")
            query = {key: values[0] for key, values in received.items() if values}
            error = query.get("error")
            if error:
                description = query.get("error_description")
                detail = f": {description}" if description else ""
                raise AuthError(f"authorization failed: {error}{detail}")
            if query.get("state") != state:
                raise AuthError("OAuth state mismatch")
            code = query.get("code")
            if not code:
                raise AuthError("authorization response is missing a code")
            token = self._exchange_code(code, redirect_uri, code_verifier)
            return token
        finally:
            server.shutdown()
            server.server_close()
            serves.join(timeout=1.0)

    def _authorization_url(
        self, redirect_uri: str, state: str, code_challenge: str
    ) -> str:
        query = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(self.scopes),
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "access_type": "offline",
            }
        )
        return f"{self.auth_uri}?{query}"

    def _exchange_code(
        self, code: str, redirect_uri: str, code_verifier: str
    ) -> Token:
        form = {
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
        payload = _request_token(self.token_uri, form, timeout=self.timeout)
        token = _token_from_response(payload, fallback=self.store.load())
        self.store.save(token)
        return token


class Credentials:
    """Supplies valid access tokens, refreshing them from the token endpoint."""

    def __init__(
        self,
        client_config: Mapping[str, Any],
        store: TokenStore,
        *,
        timeout: float = 30.0,
    ) -> None:
        self.client_id = _config_value(client_config, "client_id")
        self.client_secret = _config_value(client_config, "client_secret")
        self.token_uri = _config_value(client_config, "token_uri", _DEFAULT_TOKEN_URI)
        self.store = store
        self.timeout = timeout

    @property
    def token(self) -> Token | None:
        return self.store.load()

    def access_token(self) -> str:
        token = self.store.load()
        if token is None:
            raise AuthError("not authorized; run the login flow")
        if token.expired():
            token = self.refresh()
        return token.access_token

    def refresh(self) -> Token:
        token = self.store.load()
        if token is None:
            raise AuthError("not authorized; run the login flow")
        if not token.refresh_token:
            raise AuthError("no refresh token; run the login flow")
        form = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": token.refresh_token,
            "grant_type": "refresh_token",
        }
        payload = _request_token(self.token_uri, form, timeout=self.timeout)
        refreshed = _token_from_response(payload, fallback=token)
        self.store.save(refreshed)
        return refreshed


def load_client_config(path: str | Path) -> dict[str, Any]:
    """Load an OAuth client configuration, flattening an installed/web section."""
    config_path = Path(path).expanduser()
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AuthError(f"cannot read client configuration: {config_path}") from exc
    except ValueError as exc:
        raise AuthError(f"invalid client configuration JSON: {config_path}") from exc
    if not isinstance(data, dict):
        raise AuthError(f"invalid client configuration: {config_path}")
    for section_name in ("installed", "web"):
        section = data.get(section_name)
        if isinstance(section, dict):
            return dict(section)
    return dict(data)


def create_credentials(
    client_secret_path: str | Path | None = None,
    token_path: str | Path | None = None,
) -> Credentials:
    """Build credentials from arguments, environment variables or default paths."""
    secret_path = _resolve_path(
        client_secret_path, _ENV_CLIENT_SECRET, Path.cwd() / "client_secret.json"
    )
    if not secret_path.is_file():
        raise AuthError(f"client secret file not found: {secret_path}")
    resolved_token_path = _resolve_path(
        token_path, _ENV_TOKEN, DEFAULT_TOKEN_PATH
    )
    return Credentials(load_client_config(secret_path), TokenStore(resolved_token_path))


def _resolve_path(
    explicit: str | Path | None, env_name: str, default: Path
) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser()
    from_env = os.environ.get(env_name)
    if from_env:
        return Path(from_env).expanduser()
    return default


def _config_value(
    client_config: Mapping[str, Any], key: str, default: str = ""
) -> str:
    value = client_config.get(key)
    return default if value is None else str(value)


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _token_from_response(
    payload: Mapping[str, Any], fallback: Token | None = None
) -> Token:
    access_token = payload.get("access_token")
    if not access_token:
        raise AuthError("token endpoint did not return an access_token")
    expires_in = payload.get("expires_in", 0)
    try:
        expires_at = time.time() + int(expires_in)
    except (TypeError, ValueError):
        expires_at = time.time()
    refresh_token = payload.get("refresh_token") or (
        fallback.refresh_token if fallback else None
    )
    token_type = payload.get("token_type") or (
        fallback.token_type if fallback else "Bearer"
    )
    scope = payload.get("scope") or (fallback.scope if fallback else "")
    return Token(
        access_token=str(access_token),
        refresh_token=_optional_str(refresh_token),
        expires_at=expires_at,
        token_type=str(token_type),
        scope=str(scope),
    )


def _request_token(
    token_uri: str, form: Mapping[str, str], *, timeout: float
) -> dict[str, Any]:
    data = urllib.parse.urlencode(dict(form)).encode("utf-8")
    request = urllib.request.Request(
        token_uri,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        message = _token_error_message(body) or f"HTTP {exc.code}"
        raise AuthError(f"token request failed: {message}") from exc
    except urllib.error.URLError as exc:
        raise AuthError(f"token request failed: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise AuthError(f"token request failed: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except ValueError as exc:
        raise AuthError("token endpoint returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise AuthError("token endpoint returned an unexpected payload")
    return payload


def _token_error_message(body: str) -> str:
    try:
        payload = json.loads(body) if body.strip() else None
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        description = payload.get("error_description")
        if isinstance(description, str) and description:
            return description
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message
        if isinstance(error, str) and error:
            return error
    return body.strip()


def _create_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _create_callback_handler(
    received: dict[str, list[str]], received_event: threading.Event
) -> type[BaseHTTPRequestHandler]:
    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path not in ("", "/"):
                self.send_error(404)
                return
            received.update(
                urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            )
            body = (
                b"<html><body>Authorization complete. "
                b"You can close this window.</body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            received_event.set()

        def log_message(self, _format: str, *args: Any) -> None:
            return

    return CallbackHandler
