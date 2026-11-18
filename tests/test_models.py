"""Tests for gtasks.models: models round-trip and RFC 3339 conversion."""

import unittest
from datetime import date, datetime, timedelta, timezone

from gtasks.models import Task, TaskList, parse_rfc3339, to_rfc3339

UTC = timezone.utc


class ParseRfc3339Tests(unittest.TestCase):
    def test_zulu_designator(self):
        parsed = parse_rfc3339("2024-01-02T03:04:05Z")
        self.assertEqual(parsed, datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC))
        self.assertEqual(parsed.utcoffset(), timedelta(0))

    def test_numeric_offset(self):
        parsed = parse_rfc3339("2024-01-02T03:04:05+02:00")
        self.assertEqual(parsed, datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=2))))

    def test_naive_is_utc(self):
        parsed = parse_rfc3339("2024-01-02T03:04:05")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.utcoffset(), timedelta(0))
        self.assertEqual(parsed, datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC))

    def test_fractional_seconds(self):
        parsed = parse_rfc3339("2024-01-02T03:04:05.123456Z")
        self.assertEqual(parsed.microsecond, 123456)

    def test_empty_and_invalid_return_none(self):
        for value in ("", None, "not-a-date", "2024-13-40T99:99:99Z"):
            with self.subTest(value=value):
                self.assertIsNone(parse_rfc3339(value))


class ToRfc3339Tests(unittest.TestCase):
    def test_naive_datetime_is_utc_z(self):
        self.assertEqual(
            to_rfc3339(datetime(2024, 1, 2, 3, 4, 5)),
            "2024-01-02T03:04:05.000Z",
        )

    def test_aware_datetime_normalised_to_utc(self):
        value = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=2)))
        self.assertEqual(to_rfc3339(value), "2024-01-02T01:04:05.000Z")

    def test_date_value(self):
        self.assertEqual(to_rfc3339(date(2024, 1, 2)), "2024-01-02T00:00:00.000Z")

    def test_string_passthrough(self):
        value = "2024-01-02T03:04:05Z"
        self.assertEqual(to_rfc3339(value), value)

    def test_date_only_string_becomes_midnight_utc(self):
        self.assertEqual(to_rfc3339("2026-12-01"), "2026-12-01T00:00:00.000Z")
        self.assertEqual(to_rfc3339("1999-01-31"), "1999-01-31T00:00:00.000Z")

    def test_fractional_milliseconds(self):
        self.assertEqual(
            to_rfc3339(datetime(2024, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)),
            "2024-01-02T03:04:05.123Z",
        )

    def test_millisecond_roundtrip(self):
        value = datetime(2024, 1, 2, 3, 4, 5, 123000, tzinfo=UTC)
        serialised = to_rfc3339(value)
        self.assertTrue(serialised.endswith("Z"))
        self.assertEqual(parse_rfc3339(serialised), value)


class TaskListTests(unittest.TestCase):
    def test_from_api_maps_fields_and_tolerates_extras(self):
        tasklist = TaskList.from_api(
            {
                "id": "list-1",
                "title": "Groceries",
                "updated": "2024-01-02T03:04:05.000Z",
                "etag": '"abc"',
                "selfLink": "https://example/lists/list-1",
                "unknown": "ignored",
            }
        )
        self.assertEqual(tasklist.id, "list-1")
        self.assertEqual(tasklist.title, "Groceries")
        self.assertEqual(tasklist.updated, "2024-01-02T03:04:05.000Z")
        self.assertEqual(tasklist.etag, '"abc"')
        self.assertEqual(tasklist.self_link, "https://example/lists/list-1")

    def test_from_api_minimal(self):
        tasklist = TaskList.from_api({"id": "list-1", "title": "Groceries"})
        self.assertIsNone(tasklist.updated)
        self.assertIsNone(tasklist.etag)
        self.assertIsNone(tasklist.self_link)

    def test_to_api_only_writable_fields(self):
        tasklist = TaskList.from_api({"id": "list-1", "title": "Groceries", "etag": "x"})
        self.assertEqual(tasklist.to_api(), {"title": "Groceries"})


class TaskTests(unittest.TestCase):
    def test_defaults(self):
        task = Task(id="task-1")
        self.assertEqual(task.title, "")
        self.assertEqual(task.notes, None)
        self.assertEqual(task.status, "needsAction")
        self.assertIsNone(task.due)
        self.assertIsNone(task.completed)
        self.assertIsNone(task.parent)
        self.assertIsNone(task.position)
        self.assertFalse(task.deleted)
        self.assertFalse(task.hidden)
        self.assertEqual(task.links, [])
        self.assertIsNone(task.updated)
        self.assertIsNone(task.etag)
        self.assertIsNone(task.self_link)

    def test_from_api_full(self):
        task = Task.from_api(
            {
                "id": "task-1",
                "title": "Buy milk",
                "notes": "2 litres",
                "status": "completed",
                "due": "2024-01-02T03:04:05.000Z",
                "completed": "2024-01-03T03:04:05.000Z",
                "parent": "parent-1",
                "position": "00000000000000000001",
                "deleted": True,
                "hidden": True,
                "links": [{"type": "email", "link": "mailto:a@example.com"}],
                "updated": "2024-01-04T03:04:05.000Z",
                "etag": '"etag"',
                "selfLink": "https://example/tasks/task-1",
                "unknown": {"ignored": True},
            }
        )
        self.assertEqual(task.id, "task-1")
        self.assertEqual(task.title, "Buy milk")
        self.assertEqual(task.notes, "2 litres")
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.due, "2024-01-02T03:04:05.000Z")
        self.assertEqual(task.completed, "2024-01-03T03:04:05.000Z")
        self.assertEqual(task.parent, "parent-1")
        self.assertEqual(task.position, "00000000000000000001")
        self.assertTrue(task.deleted)
        self.assertTrue(task.hidden)
        self.assertEqual(task.links, [{"type": "email", "link": "mailto:a@example.com"}])
        self.assertEqual(task.updated, "2024-01-04T03:04:05.000Z")
        self.assertEqual(task.etag, '"etag"')
        self.assertEqual(task.self_link, "https://example/tasks/task-1")

    def test_from_api_minimal(self):
        task = Task.from_api({"id": "task-1"})
        self.assertEqual(task.title, "")
        self.assertEqual(task.status, "needsAction")
        self.assertEqual(task.links, [])

    def test_from_api_links_defaults_to_fresh_list(self):
        first = Task.from_api({"id": "a"})
        second = Task.from_api({"id": "b"})
        self.assertIsNot(first.links, second.links)

    def test_to_api_writable_fields(self):
        task = Task(
            id="task-1",
            title="Buy milk",
            notes="2 litres",
            status="completed",
            due="2024-01-02T03:04:05.000Z",
            completed="2024-01-03T03:04:05.000Z",
            parent="parent-1",
        )
        self.assertEqual(
            task.to_api(),
            {
                "title": "Buy milk",
                "notes": "2 litres",
                "status": "completed",
                "due": "2024-01-02T03:04:05.000Z",
                "completed": "2024-01-03T03:04:05.000Z",
            },
        )

    def test_to_api_omits_readonly_fields(self):
        payload = Task(id="task-1", title="T", parent="p", hidden=True).to_api()
        for readonly in ("id", "parent", "hidden", "deleted", "links", "updated", "etag", "selfLink"):
            self.assertNotIn(readonly, payload)
        self.assertEqual(payload["title"], "T")

    def test_properties(self):
        completed = Task(id="a", status="completed")
        self.assertTrue(completed.is_completed)
        self.assertFalse(Task(id="b").is_completed)
        self.assertTrue(Task(id="c", parent="p").is_subtask)
        self.assertFalse(Task(id="d").is_subtask)

    def test_datetime_properties(self):
        task = Task(
            id="a",
            due="2024-01-02T03:04:05.000Z",
            completed="2024-01-03T04:05:06.000Z",
        )
        self.assertEqual(task.due_datetime, datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC))
        self.assertEqual(task.completed_datetime, datetime(2024, 1, 3, 4, 5, 6, tzinfo=UTC))
        self.assertIsNone(Task(id="b").due_datetime)
        self.assertIsNone(Task(id="b").completed_datetime)


if __name__ == "__main__":
    unittest.main()
