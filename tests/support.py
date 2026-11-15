"""Shared fakes and helpers for the gtasks unittest suite.

Everything here is stdlib-only and network-free.  Tests use the fakes below to
drive the resources, transport and CLI without touching the real Google API.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import importlib
import io
import json
import threading
import time
import urllib.error
import urllib.parse
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from unittest import mock

# ---------------------------------------------------------------------------
# transport / urlopen helpers
# ---------------------------------------------------------------------------


@dataclass
class Call:
    method: str
    path: str
    params: dict | None = None
    body: dict | None = None


class FakeTransport:
    """Canned-response transport used to test the resource classes."""

    def __init__(self, responses: list | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[Call] = []

    def request(self, method, path, *, params=None, body=None):
        self.calls.append(
            Call(
                method,
                path,
                dict(params) if params is not None else None,
                dict(body) if body is not None else None,
            )
        )
        if not self.responses:
            raise AssertionError(f"unexpected request: {method} {path}")
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    @property
    def last(self) -> Call:
        return self.calls[-1]


class RouteTransport:
    """Transport that answers requests by exact (method, path, params) route."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, str, dict, object]] = []
        self.calls: list[Call] = []

    def add(self, method: str, path: str, response, *, params: dict | None = None) -> None:
        self.routes.append((method, path, dict(params or {}), response))

    def request(self, method, path, *, params=None, body=None):
        params = dict(params or {})
        self.calls.append(
            Call(method, path, dict(params), dict(body) if body is not None else None)
        )
        for route_method, route_path, required, response in self.routes:
            if route_method != method or route_path != path:
                continue
            if any(params.get(key) != value for key, value in required.items()):
                continue
            if isinstance(response, BaseException):
                raise response
            return response
        raise AssertionError(f"unexpected request: {method} {path} {params}")


class FakeHeaders(dict):
    def getheader(self, name, default=None):
        return self.get(name, default)

    def getheaders(self):
        return list(self.items())


class FakeResponse:
    """Minimal stand-in for the object returned by urllib.request.urlopen."""

    def __init__(self, body=b"", status: int = 200, headers=None, reason: str = "OK") -> None:
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.body = body
        self.status = status
        self.headers = FakeHeaders(headers or {})
        self.reason = reason

    def read(self, *args):
        return self.body

    def getcode(self):
        return self.status

    def geturl(self):
        return ""

    def info(self):
        return self.headers

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


def json_response(data, status: int = 200, headers=None) -> FakeResponse:
    return FakeResponse(json.dumps(data), status, headers)


def http_error(status: int, body: str = "", headers=None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://tasks.googleapis.com/tasks/v1/test",
        status,
        f"HTTP {status}",
        FakeHeaders(headers or {}),
        io.BytesIO(body.encode("utf-8")),
    )


def api_error_response(status: int, message: str | None = None, *, headers=None, body=None):
    if body is None:
        body = json.dumps(
            {"error": {"code": status, "message": message or f"error {status}"}}
        )
    return http_error(status, body, headers)


def normalize_urlopen_call(args, kwargs) -> SimpleNamespace:
    """Normalise a urlopen call made with a Request or with a plain URL."""
    first = args[0]
    if hasattr(first, "get_full_url"):
        headers = {str(k).lower(): v for k, v in first.header_items()}
        data = first.data
        url = first.get_full_url()
        method = first.get_method()
        timeout = kwargs.get("timeout")
    else:
        data = kwargs.get("data", args[1] if len(args) > 1 else None)
        headers = {str(k).lower(): v for k, v in (kwargs.get("headers") or {}).items()}
        url = str(first)
        method = kwargs.get("method") or ("POST" if data is not None else "GET")
        timeout = kwargs.get("timeout", args[2] if len(args) > 2 else None)
    return SimpleNamespace(url=url, method=method, data=data, headers=headers, timeout=timeout)


class UrlopenRecorder:
    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if not self.responses:
            raise AssertionError("unexpected urlopen call")
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    @property
    def requests(self) -> list[SimpleNamespace]:
        return [normalize_urlopen_call(args, kwargs) for args, kwargs in self.calls]


@contextmanager
def patch_urlopen(module_name: str, responses: list):
    """Patch urlopen in urllib.request and (when imported by name) in module_name."""
    recorder = UrlopenRecorder(responses)
    with ExitStack() as stack:
        stack.enter_context(mock.patch("urllib.request.urlopen", side_effect=recorder))
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            module = None
        if module is not None and hasattr(module, "urlopen"):
            stack.enter_context(mock.patch.object(module, "urlopen", side_effect=recorder))
        yield recorder


@contextmanager
def patch_sleep(module_name: str = "gtasks.transport"):
    """Patch time.sleep and any direct sleep binding in the module under test."""
    with ExitStack() as stack:
        sleeper = stack.enter_context(mock.patch("time.sleep"))
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            module = None
        if module is not None:
            for name in ("sleep", "_sleep", "time_sleep"):
                if hasattr(module, name):
                    stack.enter_context(mock.patch.object(module, name, sleeper))
        yield sleeper


def sleep_args(sleeper) -> list[float]:
    return [call.args[0] if call.args else call.kwargs.get("seconds") for call in sleeper.call_args_list]


@contextmanager
def patch_attr(module_names, attr: str, replacement):
    """Patch attr in every given module that exposes it."""
    with ExitStack() as stack:
        for module_name in module_names:
            try:
                module = importlib.import_module(module_name)
            except ImportError:
                continue
            if hasattr(module, attr):
                stack.enter_context(mock.patch.object(module, attr, replacement))
        yield


def request_json(request: SimpleNamespace):
    return json.loads(request.data.decode("utf-8"))


def request_form(request: SimpleNamespace) -> dict:
    return urllib.parse.parse_qs(request.data.decode("utf-8"))


def split_url(url: str):
    parts = urllib.parse.urlsplit(url)
    return parts, urllib.parse.parse_qs(parts.query)


# ---------------------------------------------------------------------------
# OAuth loopback helpers
# ---------------------------------------------------------------------------


def s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@contextmanager
def patch_browser(module_name: str, opener):
    """Patch webbrowser entry points and any browser helper in the module."""
    with ExitStack() as stack:
        stack.enter_context(mock.patch("webbrowser.open", side_effect=opener))
        stack.enter_context(mock.patch("webbrowser.open_new_tab", side_effect=opener))
        module = importlib.import_module(module_name)
        webbrowser_attr = getattr(module, "webbrowser", None)
        if webbrowser_attr is not None:
            stack.enter_context(mock.patch.object(webbrowser_attr, "open", side_effect=opener))
            stack.enter_context(
                mock.patch.object(webbrowser_attr, "open_new_tab", side_effect=opener)
            )
        for name in ("open_browser", "_open_browser", "browser_open"):
            if hasattr(module, name):
                stack.enter_context(mock.patch.object(module, name, side_effect=opener))
        yield


def send_loopback_request(redirect_uri: str, code: str, state: str, *, deadline: float = 5.0):
    """GET redirect_uri?code=...&state=... (retrying until the server listens)."""
    parts = urllib.parse.urlsplit(redirect_uri)
    host = parts.hostname or "127.0.0.1"
    port = parts.port or 80
    path = parts.path or "/"
    query = urllib.parse.urlencode({"code": code, "state": state})
    target = f"{path}?{query}"
    last_error = None
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        try:
            connection = http.client.HTTPConnection(host, port, timeout=2.0)
            connection.request("GET", target)
            response = connection.getresponse()
            body = response.read()
            status = response.status
            connection.close()
            return status, body
        except OSError as exc:  # connection refused while the server starts up
            last_error = exc
            time.sleep(0.02)
    raise AssertionError(f"loopback request failed: {last_error}")


def browser_opener(log: list, code: str = "test-code", state: str | None = None):
    """Build a webbrowser.open replacement that calls the loopback redirect."""

    def opener(url: str):
        log.append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        redirect_uri = query.get("redirect_uri", [""])[0]
        callback_state = state if state is not None else query.get("state", [""])[0]
        threading.Thread(
            target=send_loopback_request,
            args=(redirect_uri, code, callback_state),
            daemon=True,
        ).start()
        return True

    return opener


# ---------------------------------------------------------------------------
# Fake CLI client
# ---------------------------------------------------------------------------


class FakeClient:
    """Duck-typed GoogleTasksClient for CLI tests."""

    def __init__(self, tasklists=None, tasks=None, transport=None):
        self.calls: list[tuple[str, tuple, dict]] = []
        self.tasklists = FakeTaskLists(self.calls, tasklists)
        self.tasks = FakeTasks(self.calls, tasks)
        self.transport = transport if transport is not None else FakeTransport([])


class FakeTaskLists:
    def __init__(self, calls, items=None):
        self.calls = calls
        self.items = list(items or [])

    def _record(self, name, args=(), kwargs=None):
        self.calls.append((f"tasklists.{name}", args, dict(kwargs or {})))

    def list(self, *, max_results=100, page_token=None):
        self._record("list", (), {"max_results": max_results, "page_token": page_token})
        from gtasks.tasklists import TaskListPage

        return TaskListPage(items=list(self.items))

    def iterate(self):
        self._record("iterate")
        return iter(list(self.items))

    def get(self, tasklist):
        self._record("get", (tasklist,))
        from gtasks.models import TaskList

        for item in self.items:
            if item.id == tasklist:
                return item
        return TaskList(id=tasklist, title=tasklist)

    def create(self, title):
        self._record("create", (title,))
        from gtasks.models import TaskList

        return TaskList(id="new-list", title=title)

    def update(self, tasklist, title):
        self._record("update", (tasklist, title))
        from gtasks.models import TaskList

        return TaskList(id=tasklist, title=title)

    def delete(self, tasklist):
        self._record("delete", (tasklist,))
        return None


class FakeTasks:
    def __init__(self, calls, items=None):
        self.calls = calls
        self.items = list(items or [])

    def _record(self, name, args=(), kwargs=None):
        self.calls.append((f"tasks.{name}", args, dict(kwargs or {})))

    def _find(self, task):
        from gtasks.models import Task

        for item in self.items:
            if item.id == task:
                return item
        return Task(id=task)

    def list(self, tasklist, **kwargs):
        self._record("list", (tasklist,), kwargs)
        from gtasks.tasks import TaskPage

        return TaskPage(items=list(self.items))

    def iterate(self, tasklist, *, filter=None, **kwargs):
        self._record("iterate", (tasklist,), dict(kwargs, filter=filter))
        items = list(self.items)
        if filter is not None:
            from gtasks.filters import filter_tasks

            items = filter_tasks(items, filter)
        return iter(items)

    def get(self, tasklist, task):
        self._record("get", (tasklist, task))
        return self._find(task)

    def create(self, tasklist, **kwargs):
        self._record("create", (tasklist,), kwargs)
        from gtasks.models import Task

        return Task(id="new-task", title=kwargs.get("title", ""), parent=kwargs.get("parent"))

    def update(self, tasklist, task, **kwargs):
        self._record("update", (tasklist, task), kwargs)
        return self._find(task)

    def delete(self, tasklist, task):
        self._record("delete", (tasklist, task))
        return None

    def clear(self, tasklist):
        self._record("clear", (tasklist,))
        return None

    def move(self, tasklist, task, **kwargs):
        self._record("move", (tasklist, task), kwargs)
        return self._find(task)

    def complete(self, tasklist, task):
        self._record("complete", (tasklist, task))
        return self._find(task)

    def uncomplete(self, tasklist, task):
        self._record("uncomplete", (tasklist, task))
        return self._find(task)

    def subtasks(self, tasklist, parent, *, include_completed=True):
        self._record(
            "subtasks", (tasklist, parent), {"include_completed": include_completed}
        )
        return [
            item
            for item in self.items
            if item.parent == parent and (include_completed or not item.is_completed)
        ]

    def create_subtask(self, tasklist, parent, **kwargs):
        self._record("create_subtask", (tasklist, parent), kwargs)
        from gtasks.models import Task

        return Task(id="new-subtask", title=kwargs.get("title", ""), parent=parent)


def calls_named(client, prefix: str):
    return [call for call in client.calls if call[0] == prefix]


def last_call(client, prefix: str):
    matches = calls_named(client, prefix)
    if not matches:
        raise AssertionError(f"no {prefix} call recorded; got {client.calls}")
    return matches[-1]


def json_items(payload):
    """Accept either a bare list or a page-shaped dict for --json list output."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "tasklists", "tasks"):
            if key in payload:
                return payload[key]
        if "id" in payload:
            return [payload]
    raise AssertionError(f"unexpected JSON payload shape: {payload!r}")


def as_datetime(value):
    """Coerce a datetime/date/str value into an aware datetime for comparisons."""
    from datetime import date, datetime, timezone

    from gtasks.models import parse_rfc3339

    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    parsed = parse_rfc3339(value)
    if parsed is None:
        raise AssertionError(f"not a parseable datetime: {value!r}")
    return parsed
