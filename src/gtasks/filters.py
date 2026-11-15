"""Client-side task filtering (AND semantics, strict date bounds)."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping

from .models import Task, parse_rfc3339

_TASK_FIELDS = frozenset(field.name for field in dataclasses.fields(Task))
_TASK_DERIVED = frozenset(
    {"is_completed", "is_subtask", "due_datetime", "completed_datetime"}
)
_KNOWN_TASK_ATTRIBUTES = _TASK_FIELDS | _TASK_DERIVED
_ACTIVE_STATUS = "needsAction"


def _as_utc(value: datetime | date | str) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, str):
        parsed = parse_rfc3339(value)
        if parsed is None:
            raise ValueError(f"invalid RFC 3339 datetime: {value!r}")
        return _as_utc(parsed)
    raise ValueError(f"expected datetime, date or RFC 3339 string, got {type(value).__name__}")


def _normalized_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@dataclass(slots=True)
class TaskFilter:
    text: str | None = None
    status: str | None = None
    due_before: datetime | str | None = None
    due_after: datetime | str | None = None
    completed_before: datetime | str | None = None
    completed_after: datetime | str | None = None
    parent: str | None = None
    top_level: bool | None = None
    fields: Mapping[str, Any] | None = None

    def match(self, task: Task) -> bool:
        if self.text is not None:
            needle = self.text.casefold()
            haystack = (task.title, task.notes or "")
            if not any(needle in part.casefold() for part in haystack):
                return False
        if self.status is not None:
            expected = _ACTIVE_STATUS if self.status == "active" else self.status
            if task.status != expected:
                return False
        if self.due_before is not None:
            due = _normalized_datetime(task.due_datetime)
            if due is None or not due < _as_utc(self.due_before):
                return False
        if self.due_after is not None:
            due = _normalized_datetime(task.due_datetime)
            if due is None or not due > _as_utc(self.due_after):
                return False
        if self.completed_before is not None:
            completed = _normalized_datetime(task.completed_datetime)
            if completed is None or not completed < _as_utc(self.completed_before):
                return False
        if self.completed_after is not None:
            completed = _normalized_datetime(task.completed_datetime)
            if completed is None or not completed > _as_utc(self.completed_after):
                return False
        if self.parent is not None and task.parent != self.parent:
            return False
        if self.top_level is not None and task.is_subtask == self.top_level:
            return False
        if self.fields is not None:
            for name, expected in self.fields.items():
                if name not in _KNOWN_TASK_ATTRIBUTES:
                    raise ValueError(f"unknown Task attribute for filtering: {name!r}")
                if getattr(task, name) != expected:
                    return False
        return True


def filter_tasks(tasks: Iterable[Task], spec: TaskFilter) -> list[Task]:
    return [task for task in tasks if spec.match(task)]
