"""Task resource, pagination model and the UNSET sentinel for partial updates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator
from urllib.parse import quote

from .filters import TaskFilter
from .models import Task, to_rfc3339
from .transport import HttpTransport

VALID_STATUSES = frozenset({"needsAction", "completed"})


def quoted(identifier: object) -> str:
    return quote(str(identifier), safe="")


def _validate_status(status: str) -> str:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid task status: {status!r}")
    return status


def _tasklist_tasks_path(tasklist: str) -> str:
    return f"/lists/{quoted(tasklist)}/tasks"


def _single_task_path(tasklist: str, task: str) -> str:
    return f"{_tasklist_tasks_path(tasklist)}/{quoted(task)}"


def _query_value(value: datetime | str) -> str:
    return to_rfc3339(value)


@dataclass(slots=True)
class TaskPage:
    items: list[Task]
    next_page_token: str | None = None


class _Unset:
    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET = _Unset()


class TasksResource:
    def __init__(self, transport: HttpTransport) -> None:
        self._transport = transport

    def list(
        self,
        tasklist: str,
        *,
        max_results: int = 100,
        page_token: str | None = None,
        show_completed: bool | None = None,
        show_deleted: bool | None = None,
        show_hidden: bool | None = None,
        show_assigned: bool | None = None,
        updated_min: datetime | str | None = None,
        due_min: datetime | str | None = None,
        due_max: datetime | str | None = None,
    ) -> TaskPage:
        params: dict[str, Any] = {"maxResults": max_results}
        if page_token is not None:
            params["pageToken"] = page_token
        for name, flag in (
            ("showCompleted", show_completed),
            ("showDeleted", show_deleted),
            ("showHidden", show_hidden),
            ("showAssigned", show_assigned),
        ):
            if flag is not None:
                params[name] = flag
        for name, moment in (
            ("updatedMin", updated_min),
            ("dueMin", due_min),
            ("dueMax", due_max),
        ):
            if moment is not None:
                params[name] = to_rfc3339(moment)
        data = self._transport.request(
            "GET", _tasklist_tasks_path(tasklist), params=params
        ) or {}
        items = [Task.from_api(item) for item in data.get("items") or []]
        return TaskPage(items=items, next_page_token=data.get("nextPageToken"))

    def iterate(
        self,
        tasklist: str,
        *,
        filter: TaskFilter | None = None,
        show_completed: bool | None = None,
        show_deleted: bool | None = None,
        show_hidden: bool | None = None,
        show_assigned: bool | None = None,
        updated_min: datetime | str | None = None,
        due_min: datetime | str | None = None,
        due_max: datetime | str | None = None,
        page_size: int = 100,
    ) -> Iterator[Task]:
        page_token: str | None = None
        while True:
            page = self.list(
                tasklist,
                max_results=page_size,
                page_token=page_token,
                show_completed=show_completed,
                show_deleted=show_deleted,
                show_hidden=show_hidden,
                show_assigned=show_assigned,
                updated_min=updated_min,
                due_min=due_min,
                due_max=due_max,
            )
            for task in page.items:
                if filter is None or filter.match(task):
                    yield task
            page_token = page.next_page_token
            if not page_token:
                return

    def get(self, tasklist: str, task: str) -> Task:
        data = self._transport.request("GET", _single_task_path(tasklist, task)) or {}
        return Task.from_api(data)

    def create(
        self,
        tasklist: str,
        *,
        title: str,
        notes: str | None = None,
        due: datetime | str | None = None,
        status: str | None = None,
        parent: str | None = None,
        previous: str | None = None,
    ) -> Task:
        body: dict[str, Any] = {"title": title}
        if notes is not None:
            body["notes"] = notes
        if due is not None:
            body["due"] = _query_value(due)
        if status is not None:
            body["status"] = _validate_status(status)
        params: dict[str, Any] = {}
        if parent is not None:
            params["parent"] = parent
        if previous is not None:
            params["previous"] = previous
        data = self._transport.request(
            "POST", _tasklist_tasks_path(tasklist), params=params or None, body=body
        ) or {}
        return Task.from_api(data)

    def update(
        self,
        tasklist: str,
        task: str,
        *,
        title: Any = UNSET,
        notes: Any = UNSET,
        due: Any = UNSET,
        status: Any = UNSET,
        completed: Any = UNSET,
    ) -> Task:
        """Patch only the fields given; None clears a field. status="needsAction" does not clear completed."""
        body: dict[str, Any] = {}
        if title is not UNSET:
            body["title"] = title
        if notes is not UNSET:
            body["notes"] = notes
        if due is not UNSET:
            body["due"] = None if due is None else _query_value(due)
        if status is not UNSET:
            body["status"] = None if status is None else _validate_status(status)
        if completed is not UNSET:
            body["completed"] = None if completed is None else _query_value(completed)
        data = self._transport.request(
            "PATCH", _single_task_path(tasklist, task), body=body
        ) or {}
        return Task.from_api(data)

    def delete(self, tasklist: str, task: str) -> None:
        self._transport.request("DELETE", _single_task_path(tasklist, task))

    def clear(self, tasklist: str) -> None:
        """Hide every completed task; completed task timestamps may stay unchanged."""
        self._transport.request("POST", f"/lists/{quoted(tasklist)}/clear")

    def move(
        self,
        tasklist: str,
        task: str,
        *,
        parent: str | None = None,
        previous: str | None = None,
    ) -> Task:
        """Reposition a task: omitted parent moves to top level, omitted previous to first among siblings."""
        params: dict[str, Any] = {}
        if parent is not None:
            params["parent"] = parent
        if previous is not None:
            params["previous"] = previous
        data = self._transport.request(
            "POST", f"{_single_task_path(tasklist, task)}/move", params=params or None
        ) or {}
        return Task.from_api(data)

    def complete(self, tasklist: str, task: str) -> Task:
        return self.update(tasklist, task, status="completed")

    def uncomplete(self, tasklist: str, task: str) -> Task:
        """Mark a task incomplete and clear its completed timestamp."""
        return self.update(tasklist, task, status="needsAction", completed=None)

    def subtasks(
        self, tasklist: str, parent: str, *, include_completed: bool = True
    ) -> list[Task]:
        spec = TaskFilter(parent=parent)
        tasks = list(
            self.iterate(
                tasklist,
                filter=spec,
                show_completed=True if include_completed else False,
            )
        )
        if include_completed:
            return tasks
        return [task for task in tasks if not task.is_completed]

    def create_subtask(
        self,
        tasklist: str,
        parent: str,
        *,
        title: str,
        notes: str | None = None,
        due: datetime | str | None = None,
    ) -> Task:
        return self.create(
            tasklist, title=title, notes=notes, due=due, parent=parent
        )
