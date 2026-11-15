"""HTTP transport for the Google Tasks REST API."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, NoReturn

from .errors import ApiError, AuthError, GtasksError, NotFoundError

if TYPE_CHECKING:
    from .auth import Credentials

_RETRYABLE_METHODS = frozenset({"GET", "HEAD", "PATCH", "PUT", "DELETE"})
_MAX_RETRIES = 2
_RETRY_DELAYS = (0.25, 0.5)
_MAX_RETRY_AFTER = 5.0


class HttpTransport:
    """Issues authenticated JSON requests to the Tasks API."""

    BASE_URL = "https://tasks.googleapis.com/tasks/v1"

    def __init__(
        self,
        credentials: Credentials,
        *,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        self.credentials = credentials
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        method = method.upper()
        url = self._build_url(path, params)
        payload = None if body is None else json.dumps(dict(body)).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.credentials.access_token()}",
            "Accept": "application/json",
            "User-Agent": "gtasks/0.1.0",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        attempt = 0
        while True:
            request = urllib.request.Request(
                url, data=payload, headers=headers, method=method
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return self._parse_body(response.read())
            except urllib.error.HTTPError as exc:
                body_text = exc.read().decode("utf-8", errors="replace")
                if attempt < _MAX_RETRIES and self._is_retryable(method, exc.code):
                    time.sleep(self._retry_delay(exc.headers, attempt))
                    attempt += 1
                    continue
                self._raise_api_error(exc.code, body_text)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < _MAX_RETRIES and method in _RETRYABLE_METHODS:
                    time.sleep(self._retry_delay(None, attempt))
                    attempt += 1
                    continue
                raise self._network_error(exc) from exc

    def _build_url(self, path: str, params: Mapping[str, Any] | None) -> str:
        url = f"{self.base_url}{path}"
        query = self._encode_params(params)
        return f"{url}?{query}" if query else url

    @staticmethod
    def _encode_params(params: Mapping[str, Any] | None) -> str:
        if not params:
            return ""
        pairs: list[tuple[str, str]] = []
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, bool):
                encoded = "true" if value else "false"
            else:
                encoded = str(value)
            pairs.append((str(key), encoded))
        return urllib.parse.urlencode(pairs)

    @staticmethod
    def _parse_body(data: bytes) -> dict[str, Any] | None:
        text = data.decode("utf-8").strip()
        if not text:
            return None
        try:
            value = json.loads(text)
        except ValueError as exc:
            raise GtasksError(f"invalid JSON response: {exc}") from exc
        return value if isinstance(value, dict) else {"data": value}

    @staticmethod
    def _is_retryable(method: str, status: int) -> bool:
        if status == 429:
            return True
        return status >= 500 and method in _RETRYABLE_METHODS

    @staticmethod
    def _retry_delay(headers: Any, attempt: int) -> float:
        retry_after = headers.get("Retry-After") if headers is not None else None
        if retry_after:
            try:
                return max(0.0, min(float(retry_after), _MAX_RETRY_AFTER))
            except ValueError:
                pass
        return _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]

    @staticmethod
    def _network_error(exc: OSError) -> GtasksError:
        if isinstance(exc, urllib.error.URLError) and exc.reason:
            return GtasksError(f"network error: {exc.reason}")
        if isinstance(exc, TimeoutError):
            return GtasksError("request timed out")
        detail = str(exc) or type(exc).__name__
        return GtasksError(f"network error: {detail}")

    @staticmethod
    def _raise_api_error(status: int, body: str) -> NoReturn:
        payload = None
        message = ""
        try:
            parsed = json.loads(body) if body.strip() else None
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            payload = parsed
            error = parsed.get("error")
            if isinstance(error, Mapping):
                raw_message = error.get("message")
                if isinstance(raw_message, str):
                    message = raw_message
            elif isinstance(error, str):
                message = error
        message = message or body.strip() or f"HTTP {status}"
        if status == 404:
            raise NotFoundError(status, message, payload=payload, body=body)
        if status == 401:
            raise AuthError(f"HTTP {status}: {message}")
        raise ApiError(status, message, payload=payload, body=body)
