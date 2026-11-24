"""Minimal stdlib-only client for the Google Tasks REST API."""

from __future__ import annotations

import importlib
from typing import Any

from .auth import (
    DEFAULT_CLIENT_SECRET_PATH,
    DEFAULT_CONFIG_DIR,
    DEFAULT_TOKEN_PATH,
    SCOPES,
    Credentials,
    OAuth2Flow,
    Token,
    TokenStore,
    create_credentials,
    load_client_config,
    resolve_client_secret_path,
    save_client_config,
)
from .errors import ApiError, AuthError, GtasksError, NotFoundError
from .models import Task, TaskList, parse_rfc3339, to_rfc3339
from .transport import HttpTransport

__version__ = "0.3.0"

__all__ = [
    "ApiError",
    "AuthError",
    "Credentials",
    "DEFAULT_CLIENT_SECRET_PATH",
    "DEFAULT_CONFIG_DIR",
    "DEFAULT_TOKEN_PATH",
    "GtasksError",
    "GoogleTasksClient",
    "HttpTransport",
    "NotFoundError",
    "OAuth2Flow",
    "SCOPES",
    "SyncChange",
    "SyncEngine",
    "SyncReport",
    "SyncState",
    "Task",
    "TaskFilter",
    "TaskList",
    "TaskListPage",
    "TaskListsResource",
    "TaskPage",
    "TasksResource",
    "Token",
    "TokenStore",
    "UNSET",
    "__version__",
    "create_credentials",
    "filter_tasks",
    "load_client_config",
    "parse_rfc3339",
    "resolve_client_secret_path",
    "save_client_config",
    "to_rfc3339",
]

_API_MODULES = {
    "GoogleTasksClient": "client",
    "TaskListPage": "tasklists",
    "TaskListsResource": "tasklists",
    "TaskPage": "tasks",
    "TasksResource": "tasks",
    "UNSET": "tasks",
    "TaskFilter": "filters",
    "filter_tasks": "filters",
    "SyncState": "sync",
    "SyncChange": "sync",
    "SyncReport": "sync",
    "SyncEngine": "sync",
}


def __getattr__(name: str) -> Any:
    module_name = _API_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(f".{module_name}", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
