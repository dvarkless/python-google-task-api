"""Google Tasks client facade."""

from __future__ import annotations

from .auth import create_credentials
from .tasklists import TaskListsResource
from .tasks import TasksResource
from .transport import HttpTransport


class GoogleTasksClient:
    def __init__(self, transport: HttpTransport) -> None:
        self.transport = transport
        self.tasklists = TaskListsResource(transport)
        self.tasks = TasksResource(transport)

    @classmethod
    def from_client_secret(cls, path: str, token_path: str | None = None) -> "GoogleTasksClient":
        credentials = create_credentials(path, token_path)
        return cls(HttpTransport(credentials))

    @classmethod
    def from_env(cls) -> "GoogleTasksClient":
        return cls(HttpTransport(create_credentials()))
