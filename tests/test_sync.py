"""Tests for gtasks.sync: first/full/incremental pulls, diffs, determinism and state files."""

import json
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from gtasks.models import Task, TaskList, parse_rfc3339
from gtasks.sync import SyncChange, SyncEngine, SyncReport, SyncState

from tests.support import RouteTransport

UTC = timezone.utc
OLD_SYNCED_AT = "2024-01-01T00:00:00.000Z"


def task_payload(task_id, **kwargs):
    payload = {"id": task_id}
    payload.update(kwargs)
    return payload


def calls_for(transport, path):
    return [call for call in transport.calls if call.path == path]


class SyncStateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_load_missing_file_returns_empty_state(self):
        state = SyncState.load(self.dir / "missing.json")
        self.assertEqual(state.tasklists, {})
        self.assertEqual(state.tasks, {})
        self.assertIsNone(state.synced_at)
        self.assertEqual(state.version, 1)
        self.assertTrue(state.is_empty())

    def test_save_and_load_roundtrip(self):
        state = SyncState(
            tasklists={"l1": {"id": "l1", "title": "One"}},
            tasks={"l1": {"t1": {"id": "t1", "title": "Task"}}},
            synced_at=OLD_SYNCED_AT,
        )
        path = self.dir / "nested" / "sync.json"
        state.save(path)
        self.assertTrue(path.exists())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(
            sorted(item.name for item in path.parent.iterdir()), ["sync.json"]
        )
        loaded = SyncState.load(path)
        self.assertEqual(loaded, state)
        self.assertFalse(loaded.is_empty())

    def test_save_creates_parent_dirs(self):
        path = self.dir / "a" / "b" / "sync.json"
        SyncState().save(path)
        self.assertTrue(path.exists())

    def test_is_empty(self):
        self.assertTrue(SyncState().is_empty())
        self.assertFalse(SyncState(tasklists={"l1": {}}).is_empty())
        self.assertFalse(
            SyncState(tasks={"l1": {"t1": {}}}).is_empty()
        )


class SyncReportTests(unittest.TestCase):
    def test_counts(self):
        report = SyncReport(
            changes=[
                SyncChange("tasklist", "created", "l1"),
                SyncChange("task", "created", "t1", tasklist_id="l1"),
                SyncChange("task", "updated", "t2", tasklist_id="l1"),
                SyncChange("task", "deleted", "t3", tasklist_id="l1"),
                SyncChange("task", "deleted", "t4", tasklist_id="l1"),
            ],
            synced_at=OLD_SYNCED_AT,
        )
        self.assertEqual(report.counts(), {"created": 2, "updated": 1, "deleted": 2})


class SyncEngineTests(unittest.TestCase):
    def make_engine(self):
        transport = RouteTransport()
        return SyncEngine(transport), transport

    def test_first_pull(self):
        engine, transport = self.make_engine()
        transport.add(
            "GET",
            "/users/@me/lists",
            {"items": [{"id": "L1", "title": "List one"}]},
        )
        transport.add(
            "GET",
            "/lists/L1/tasks",
            {
                "items": [
                    task_payload("t2", title="Second", status="completed"),
                    task_payload("t1", title="First"),
                ]
            },
        )
        state = SyncState()
        report = engine.pull(state)

        self.assertEqual(
            [(change.kind, change.action, change.id) for change in report.changes],
            [
                ("tasklist", "created", "L1"),
                ("task", "created", "t1"),
                ("task", "created", "t2"),
            ],
        )
        self.assertIsInstance(report.changes[0].item, TaskList)
        self.assertIsInstance(report.changes[1].item, Task)
        self.assertEqual(report.counts(), {"created": 3, "updated": 0, "deleted": 0})
        self.assertEqual(report.synced_at, state.synced_at)
        self.assertIsNotNone(parse_rfc3339(report.synced_at))

        tasks_calls = calls_for(transport, "/lists/L1/tasks")
        self.assertEqual(len(tasks_calls), 1)
        self.assertIs(tasks_calls[0].params.get("showDeleted"), False)
        self.assertIs(tasks_calls[0].params.get("showHidden"), True)
        self.assertNotIn("updatedMin", tasks_calls[0].params)

        self.assertEqual(state.tasklists["L1"]["title"], "List one")
        self.assertEqual(state.tasks["L1"]["t1"]["title"], "First")
        self.assertEqual(state.tasks["L1"]["t2"]["status"], "completed")
        self.assertEqual(state.version, 1)
        self.assertFalse(state.is_empty())

    def test_full_pull_from_empty_state_uses_show_deleted(self):
        engine, transport = self.make_engine()
        transport.add(
            "GET", "/users/@me/lists", {"items": [{"id": "L1", "title": "One"}]}
        )
        transport.add(
            "GET", "/lists/L1/tasks", {"items": [task_payload("t1"), task_payload("t2")]}
        )
        state = SyncState()
        report = engine.pull(state, full=True)
        tasks_calls = calls_for(transport, "/lists/L1/tasks")
        self.assertIs(tasks_calls[0].params.get("showDeleted"), True)
        self.assertIs(tasks_calls[0].params.get("showHidden"), True)
        self.assertEqual(report.counts()["created"], 3)

    def test_full_pull_diffs_missing_tasks(self):
        engine, transport = self.make_engine()
        transport.add(
            "GET", "/users/@me/lists", {"items": [{"id": "L1", "title": "One"}]}
        )
        transport.add(
            "GET",
            "/lists/L1/tasks",
            {"items": [task_payload("t1", title="Changed"), task_payload("t3")]},
        )
        state = SyncState(
            tasklists={"L1": {"id": "L1", "title": "One"}},
            tasks={
                "L1": {
                    "t1": task_payload("t1", title="Old"),
                    "t2": task_payload("t2", title="Removed"),
                }
            },
            synced_at=OLD_SYNCED_AT,
        )
        report = engine.pull(state, full=True)
        tasks_calls = calls_for(transport, "/lists/L1/tasks")
        self.assertIs(tasks_calls[0].params.get("showDeleted"), True)
        self.assertIs(tasks_calls[0].params.get("showHidden"), True)
        self.assertNotIn("updatedMin", tasks_calls[0].params)

        actions = {(change.id, change.action) for change in report.changes}
        self.assertIn(("t2", "deleted"), actions)
        self.assertIn(("t3", "created"), actions)
        self.assertNotIn(("t2", "updated"), actions)
        self.assertNotIn("t2", state.tasks["L1"])
        self.assertIn("t3", state.tasks["L1"])
        self.assertIn("t1", state.tasks["L1"])

    def test_incremental_created_updated_deleted(self):
        engine, transport = self.make_engine()
        transport.add(
            "GET", "/users/@me/lists", {"items": [{"id": "L1", "title": "One"}]}
        )
        transport.add(
            "GET",
            "/lists/L1/tasks",
            {
                "items": [
                    task_payload("t1", title="New title"),
                    task_payload("t4", title="Brand new"),
                    task_payload("t2", title="Tombstone", deleted=True),
                ]
            },
        )
        state = SyncState(
            tasklists={"L1": {"id": "L1", "title": "One"}},
            tasks={
                "L1": {
                    "t1": task_payload("t1", title="Old title"),
                    "t2": task_payload("t2", title="Second"),
                    "t3": task_payload("t3", title="Untouched"),
                }
            },
            synced_at=OLD_SYNCED_AT,
        )
        report = engine.pull(state)

        tasks_calls = calls_for(transport, "/lists/L1/tasks")
        self.assertEqual(len(tasks_calls), 1)
        self.assertIs(tasks_calls[0].params.get("showDeleted"), True)
        self.assertIs(tasks_calls[0].params.get("showHidden"), True)
        self.assertEqual(tasks_calls[0].params.get("updatedMin"), OLD_SYNCED_AT)

        self.assertEqual(
            [(change.kind, change.action, change.id) for change in report.changes],
            [
                ("task", "updated", "t1"),
                ("task", "deleted", "t2"),
                ("task", "created", "t4"),
            ],
        )
        self.assertEqual(report.counts(), {"created": 1, "updated": 1, "deleted": 1})
        self.assertEqual(state.tasks["L1"]["t1"]["title"], "New title")
        self.assertNotIn("t2", state.tasks["L1"])
        self.assertIn("t4", state.tasks["L1"])
        self.assertEqual(state.tasks["L1"]["t3"]["title"], "Untouched")
        self.assertNotEqual(state.synced_at, OLD_SYNCED_AT)

    def test_incremental_unchanged_task_reports_nothing(self):
        engine, transport = self.make_engine()
        transport.add(
            "GET", "/users/@me/lists", {"items": [{"id": "L1", "title": "One"}]}
        )
        snapshot = task_payload("t1", title="Same", status="needsAction")
        transport.add("GET", "/lists/L1/tasks", {"items": [dict(snapshot)]})
        state = SyncState(
            tasklists={"L1": {"id": "L1", "title": "One"}},
            tasks={"L1": {"t1": dict(snapshot)}},
            synced_at=OLD_SYNCED_AT,
        )
        report = engine.pull(state)
        self.assertEqual(report.changes, [])

    def test_tasklist_diff_and_delete_cleanup(self):
        engine, transport = self.make_engine()
        transport.add(
            "GET",
            "/users/@me/lists",
            {
                "items": [
                    {"id": "L1", "title": "Renamed"},
                    {"id": "L3", "title": "Third"},
                ]
            },
        )
        transport.add("GET", "/lists/L1/tasks", {"items": [task_payload("t1")]})
        transport.add("GET", "/lists/L3/tasks", {"items": []})
        state = SyncState(
            tasklists={
                "L1": {"id": "L1", "title": "Old name"},
                "L2": {"id": "L2", "title": "Gone"},
            },
            tasks={
                "L1": {"t1": task_payload("t1")},
                "L2": {"t9": task_payload("t9")},
            },
            synced_at=OLD_SYNCED_AT,
        )
        report = engine.pull(state)

        tasklist_changes = [c for c in report.changes if c.kind == "tasklist"]
        self.assertEqual(
            [(change.id, change.action) for change in tasklist_changes],
            [("L1", "updated"), ("L2", "deleted"), ("L3", "created")],
        )
        self.assertEqual(tasklist_changes[0].item.title, "Renamed")
        self.assertEqual(set(state.tasklists), {"L1", "L3"})
        self.assertNotIn("L2", state.tasks)
        self.assertEqual(state.tasks["L1"]["t1"]["id"], "t1")

    def test_deterministic_change_ordering(self):
        engine, transport = self.make_engine()
        transport.add(
            "GET",
            "/users/@me/lists",
            {
                "items": [
                    {"id": "b", "title": "B"},
                    {"id": "a", "title": "A"},
                ]
            },
        )
        transport.add(
            "GET", "/lists/a/tasks", {"items": [task_payload("t2"), task_payload("t1")]}
        )
        transport.add(
            "GET", "/lists/b/tasks", {"items": [task_payload("t9"), task_payload("t8")]}
        )
        state = SyncState()
        report = engine.pull(state)
        self.assertEqual(
            [(change.kind, change.id) for change in report.changes],
            [
                ("tasklist", "a"),
                ("tasklist", "b"),
                ("task", "t1"),
                ("task", "t2"),
                ("task", "t8"),
                ("task", "t9"),
            ],
        )
        self.assertFalse(state.is_empty())


if __name__ == "__main__":
    unittest.main()
