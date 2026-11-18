"""Dataclasses and RFC 3339 helpers for Google Tasks API resources."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

_UTC = timezone.utc


def parse_rfc3339(value: str | None) -> datetime | None:
    """Parse an RFC 3339 timestamp, treating naive input as UTC; None when invalid."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=_UTC)
    return parsed.astimezone(_UTC)


def to_rfc3339(value: datetime | date | str) -> str:
    """Render a datetime, date or timestamp string as an RFC 3339 UTC string.

    Date-only strings become midnight UTC; other non-empty strings pass through.
    """
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("RFC 3339 value must not be empty")
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
            try:
                parsed_date = date.fromisoformat(text)
            except ValueError:
                return value
            return f"{parsed_date.isoformat()}T00:00:00.000Z"
        return value
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=_UTC)
        moment = moment.astimezone(_UTC)
        return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"
    if isinstance(value, date):
        return f"{value.isoformat()}T00:00:00.000Z"
    raise TypeError(f"cannot convert {type(value).__name__} to RFC 3339")


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


@dataclass(slots=True)
class TaskList:
    """A Google Tasks task list."""

    id: str
    title: str
    updated: str | None = None
    etag: str | None = None
    self_link: str | None = None

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "TaskList":
        return cls(
            id=str(data.get("id") or ""),
            title=str(data.get("title") or ""),
            updated=_optional_str(data.get("updated")),
            etag=_optional_str(data.get("etag")),
            self_link=_optional_str(data.get("selfLink")),
        )

    def to_api(self) -> dict[str, Any]:
        return {"title": self.title}


@dataclass(slots=True)
class Task:
    """A Google Tasks task or subtask."""

    id: str
    title: str = ""
    notes: str | None = None
    status: str = "needsAction"
    due: str | None = None
    completed: str | None = None
    parent: str | None = None
    position: str | None = None
    deleted: bool = False
    hidden: bool = False
    links: list[dict[str, Any]] = field(default_factory=list)
    updated: str | None = None
    etag: str | None = None
    self_link: str | None = None

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "Task":
        links = data.get("links")
        return cls(
            id=str(data.get("id") or ""),
            title=str(data.get("title") or ""),
            notes=_optional_str(data.get("notes")),
            status=str(data.get("status") or "needsAction"),
            due=_optional_str(data.get("due")),
            completed=_optional_str(data.get("completed")),
            parent=_optional_str(data.get("parent")),
            position=_optional_str(data.get("position")),
            deleted=bool(data.get("deleted", False)),
            hidden=bool(data.get("hidden", False)),
            links=[dict(link) for link in links if isinstance(link, Mapping)]
            if isinstance(links, list)
            else [],
            updated=_optional_str(data.get("updated")),
            etag=_optional_str(data.get("etag")),
            self_link=_optional_str(data.get("selfLink")),
        )

    def to_api(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "notes": self.notes,
            "status": self.status,
            "due": self.due,
            "completed": self.completed,
        }

    @property
    def is_completed(self) -> bool:
        return self.status == "completed"

    @property
    def is_subtask(self) -> bool:
        return self.parent is not None

    @property
    def due_datetime(self) -> datetime | None:
        return parse_rfc3339(self.due)

    @property
    def completed_datetime(self) -> datetime | None:
        return parse_rfc3339(self.completed)
