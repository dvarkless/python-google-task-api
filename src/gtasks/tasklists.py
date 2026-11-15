"""Task list resource and pagination model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator
from urllib.parse import quote

from .models import TaskList
from .transport import HttpTransport

_TASKLISTS_PATH = "/users/@me/lists"


def quoted(identifier: object) -> str:
    return quote(str(identifier), safe="")


@dataclass(slots=True)
class TaskListPage:
    items: list[TaskList]
    next_page_token: str | None = None


class TaskListsResource:
    def __init__(self, transport: HttpTransport) -> None:
        self._transport = transport

    @staticmethod
    def _list_path(tasklist: str) -> str:
        return f"{_TASKLISTS_PATH}/{quoted(tasklist)}"

    def list(self, *, max_results: int = 1000, page_token: str | None = None) -> TaskListPage:
        params: dict[str, object] = {"maxResults": max_results}
        if page_token is not None:
            params["pageToken"] = page_token
        data = self._transport.request("GET", _TASKLISTS_PATH, params=params) or {}
        items = [TaskList.from_api(item) for item in data.get("items") or []]
        return TaskListPage(items=items, next_page_token=data.get("nextPageToken"))

    def iterate(self) -> Iterator[TaskList]:
        page_token: str | None = None
        while True:
            page = self.list(page_token=page_token)
            yield from page.items
            page_token = page.next_page_token
            if not page_token:
                return

    def get(self, tasklist: str) -> TaskList:
        data = self._transport.request("GET", self._list_path(tasklist)) or {}
        return TaskList.from_api(data)

    def create(self, title: str) -> TaskList:
        data = self._transport.request("POST", _TASKLISTS_PATH, body={"title": title}) or {}
        return TaskList.from_api(data)

    def update(self, tasklist: str, title: str) -> TaskList:
        data = self._transport.request(
            "PATCH", self._list_path(tasklist), body={"title": title}
        ) or {}
        return TaskList.from_api(data)

    def delete(self, tasklist: str) -> None:
        self._transport.request("DELETE", self._list_path(tasklist))
