"""One-way pull sync: Google Tasks -> local JSON state."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .models import Task, TaskList, to_rfc3339
from .transport import HttpTransport

_TASKLISTS_PATH = "/users/@me/lists"


def _quoted(identifier: object) -> str:
    return quote(str(identifier), safe="")


def _tasks_path(tasklist_id: str) -> str:
    return f"/lists/{_quoted(tasklist_id)}/tasks"


def _fetch_pages(
    transport: HttpTransport, path: str, params: dict[str, Any]
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        page_params: dict[str, Any] = {"maxResults": 100, **params}
        if page_token is not None:
            page_params["pageToken"] = page_token
        data = transport.request("GET", path, params=page_params) or {}
        items.extend(item for item in data.get("items") or [] if isinstance(item, dict))
        page_token = data.get("nextPageToken")
        if not page_token:
            return items


@dataclass(slots=True)
class SyncState:
    tasklists: dict[str, dict[str, Any]] = field(default_factory=dict)
    tasks: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    synced_at: str | None = None
    version: int = 1

    @classmethod
    def load(cls, path: str | Path) -> "SyncState":
        state_path = Path(path)
        try:
            text = state_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return cls()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return cls()
        if not isinstance(data, dict):
            return cls()
        raw_tasklists = data.get("tasklists")
        raw_tasks = data.get("tasks")
        tasklists = {
            str(list_id): dict(raw)
            for list_id, raw in (raw_tasklists or {}).items()
            if isinstance(raw, dict)
        }
        tasks: dict[str, dict[str, dict[str, Any]]] = {}
        for list_id, raw_entries in (raw_tasks or {}).items():
            if not isinstance(raw_entries, dict):
                continue
            tasks[str(list_id)] = {
                str(task_id): dict(raw)
                for task_id, raw in raw_entries.items()
                if isinstance(raw, dict)
            }
        synced_at = data.get("synced_at")
        version = data.get("version", 1)
        return cls(
            tasklists=tasklists,
            tasks=tasks,
            synced_at=synced_at if isinstance(synced_at, str) else None,
            version=version if isinstance(version, int) else 1,
        )

    def save(self, path: str | Path) -> None:
        state_path = Path(path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(self), ensure_ascii=False, indent=2, sort_keys=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".sync-", suffix=".json", dir=str(state_path.parent)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, state_path)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    def is_empty(self) -> bool:
        return not self.tasklists and not self.tasks and not self.synced_at


@dataclass(slots=True)
class SyncChange:
    kind: str
    action: str
    id: str
    tasklist_id: str | None = None
    item: TaskList | Task | None = None


@dataclass(slots=True)
class SyncReport:
    changes: list[SyncChange]
    synced_at: str

    def counts(self) -> dict[str, int]:
        counts = {"created": 0, "updated": 0, "deleted": 0}
        for change in self.changes:
            if change.action in counts:
                counts[change.action] += 1
        return counts


class SyncEngine:
    def __init__(self, transport: HttpTransport) -> None:
        self._transport = transport

    def pull(self, state: SyncState, *, full: bool = False) -> SyncReport:
        """Pull remote changes; full=True is the recovery path for hidden/cleared tasks."""
        initial_pull = not state.synced_at
        tasklist_changes = self._sync_tasklists(state)
        task_changes = (
            self._initial_or_full_tasks(state, full=full)
            if initial_pull or full
            else self._incremental_tasks(state)
        )
        synced_at = to_rfc3339(datetime.now(timezone.utc))
        state.synced_at = synced_at
        tasklist_changes.sort(key=lambda change: change.id)
        task_changes.sort(key=lambda change: (change.tasklist_id or "", change.id))
        return SyncReport(changes=tasklist_changes + task_changes, synced_at=synced_at)

    def _sync_tasklists(self, state: SyncState) -> list[SyncChange]:
        raw_tasklists = _fetch_pages(self._transport, _TASKLISTS_PATH, {})
        remote: dict[str, dict[str, Any]] = {}
        for raw in raw_tasklists:
            list_id = raw.get("id")
            if list_id is not None:
                remote[str(list_id)] = raw
        changes: list[SyncChange] = []
        previous = state.tasklists
        for list_id in sorted(remote):
            raw = remote[list_id]
            if list_id not in previous:
                changes.append(
                    SyncChange(
                        kind="tasklist",
                        action="created",
                        id=list_id,
                        item=TaskList.from_api(raw),
                    )
                )
            elif previous[list_id] != raw:
                changes.append(
                    SyncChange(
                        kind="tasklist",
                        action="updated",
                        id=list_id,
                        item=TaskList.from_api(raw),
                    )
                )
        removed = sorted(set(previous) - set(remote))
        for list_id in removed:
            changes.append(SyncChange(kind="tasklist", action="deleted", id=list_id))
            state.tasks.pop(list_id, None)
        state.tasklists = {list_id: remote[list_id] for list_id in sorted(remote)}
        return changes

    def _initial_or_full_tasks(self, state: SyncState, *, full: bool) -> list[SyncChange]:
        changes: list[SyncChange] = []
        for list_id in sorted(state.tasklists):
            fetched = _fetch_pages(
                self._transport,
                _tasks_path(list_id),
                {"showDeleted": bool(full), "showHidden": True},
            )
            fetched_by_id: dict[str, dict[str, Any]] = {}
            for raw in fetched:
                task_id = raw.get("id")
                if task_id is not None:
                    fetched_by_id[str(task_id)] = raw
            previous = state.tasks.get(list_id, {})
            for task_id in sorted(fetched_by_id):
                raw = fetched_by_id[task_id]
                deleted = bool(raw.get("deleted"))
                if deleted:
                    if full:
                        changes.append(
                            SyncChange(
                                kind="task",
                                action="deleted",
                                id=task_id,
                                tasklist_id=list_id,
                            )
                        )
                    continue
                if task_id not in previous:
                    changes.append(
                        SyncChange(
                            kind="task",
                            action="created",
                            id=task_id,
                            tasklist_id=list_id,
                            item=Task.from_api(raw),
                        )
                    )
                elif previous[task_id] != raw:
                    changes.append(
                        SyncChange(
                            kind="task",
                            action="updated",
                            id=task_id,
                            tasklist_id=list_id,
                            item=Task.from_api(raw),
                        )
                    )
            if full:
                for task_id in sorted(set(previous) - set(fetched_by_id)):
                    changes.append(
                        SyncChange(
                            kind="task",
                            action="deleted",
                            id=task_id,
                            tasklist_id=list_id,
                        )
                    )
            state.tasks[list_id] = {
                task_id: raw
                for task_id, raw in fetched_by_id.items()
                if not raw.get("deleted")
            }
        return changes

    def _incremental_tasks(self, state: SyncState) -> list[SyncChange]:
        changes: list[SyncChange] = []
        updated_min = state.synced_at
        for list_id in sorted(state.tasklists):
            fetched = _fetch_pages(
                self._transport,
                _tasks_path(list_id),
                {"showDeleted": True, "showHidden": True, "updatedMin": updated_min},
            )
            previous = state.tasks.setdefault(list_id, {})
            for raw in fetched:
                task_id = raw.get("id")
                if task_id is None:
                    continue
                task_id = str(task_id)
                if raw.get("deleted"):
                    changes.append(
                        SyncChange(
                            kind="task",
                            action="deleted",
                            id=task_id,
                            tasklist_id=list_id,
                        )
                    )
                    previous.pop(task_id, None)
                    continue
                if task_id not in previous:
                    changes.append(
                        SyncChange(
                            kind="task",
                            action="created",
                            id=task_id,
                            tasklist_id=list_id,
                            item=Task.from_api(raw),
                        )
                    )
                elif previous[task_id] != raw:
                    changes.append(
                        SyncChange(
                            kind="task",
                            action="updated",
                            id=task_id,
                            tasklist_id=list_id,
                            item=Task.from_api(raw),
                        )
                    )
                previous[task_id] = raw
        return changes
