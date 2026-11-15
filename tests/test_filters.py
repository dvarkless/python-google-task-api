"""Tests for gtasks.filters: every TaskFilter predicate, boundaries and AND semantics."""

import unittest
from datetime import date, datetime, timezone

from gtasks.filters import TaskFilter, filter_tasks
from gtasks.models import Task

UTC = timezone.utc


def dt(*args):
    return datetime(*args, tzinfo=UTC)


class TextFilterTests(unittest.TestCase):
    def test_matches_title_case_insensitively(self):
        task = Task(id="t1", title="Buy Milk")
        self.assertTrue(TaskFilter(text="milk").match(task))
        self.assertTrue(TaskFilter(text="BUY").match(task))

    def test_matches_notes_case_insensitively(self):
        task = Task(id="t1", title="Shopping", notes="Two Litres of milk")
        self.assertTrue(TaskFilter(text="litres").match(task))

    def test_does_not_match_unrelated_text(self):
        task = Task(id="t1", title="Buy Milk", notes=None)
        self.assertFalse(TaskFilter(text="bread").match(task))

    def test_missing_notes_does_not_crash(self):
        task = Task(id="t1", title="Buy Milk")
        self.assertFalse(TaskFilter(text="anything").match(task))


class StatusFilterTests(unittest.TestCase):
    def test_exact_status(self):
        active = Task(id="a")
        completed = Task(id="b", status="completed")
        self.assertTrue(TaskFilter(status="needsAction").match(active))
        self.assertFalse(TaskFilter(status="needsAction").match(completed))
        self.assertTrue(TaskFilter(status="completed").match(completed))
        self.assertFalse(TaskFilter(status="completed").match(active))

    def test_active_is_alias_for_needs_action(self):
        self.assertTrue(TaskFilter(status="active").match(Task(id="a")))
        self.assertFalse(
            TaskFilter(status="active").match(Task(id="b", status="completed"))
        )


class DateBoundTests(unittest.TestCase):
    def test_due_before_is_strict(self):
        spec = TaskFilter(due_before=dt(2024, 1, 10))
        self.assertTrue(spec.match(Task(id="a", due="2024-01-09T00:00:00.000Z")))
        self.assertFalse(spec.match(Task(id="b", due="2024-01-10T00:00:00.000Z")))
        self.assertFalse(spec.match(Task(id="c", due="2024-01-11T00:00:00.000Z")))

    def test_due_after_is_strict(self):
        spec = TaskFilter(due_after=dt(2024, 1, 10))
        self.assertTrue(spec.match(Task(id="a", due="2024-01-11T00:00:00.000Z")))
        self.assertFalse(spec.match(Task(id="b", due="2024-01-10T00:00:00.000Z")))
        self.assertFalse(spec.match(Task(id="c", due="2024-01-09T00:00:00.000Z")))

    def test_missing_due_fails_date_condition(self):
        self.assertFalse(
            TaskFilter(due_before=dt(2025, 1, 1)).match(Task(id="a"))
        )
        self.assertFalse(TaskFilter(due_after=dt(2000, 1, 1)).match(Task(id="a")))

    def test_completed_bounds_are_strict(self):
        task = Task(id="a", status="completed", completed="2024-01-10T00:00:00.000Z")
        self.assertFalse(
            TaskFilter(completed_before=dt(2024, 1, 10)).match(task)
        )
        self.assertTrue(
            TaskFilter(completed_before=dt(2024, 1, 11)).match(task)
        )
        self.assertFalse(
            TaskFilter(completed_after=dt(2024, 1, 10)).match(task)
        )
        self.assertTrue(TaskFilter(completed_after=dt(2024, 1, 9)).match(task))

    def test_missing_completed_fails_date_condition(self):
        self.assertFalse(
            TaskFilter(completed_after=dt(2000, 1, 1)).match(Task(id="a"))
        )

    def test_naive_bound_is_utc(self):
        task = Task(id="a", due="2024-01-02T00:00:00.000Z")
        self.assertTrue(
            TaskFilter(due_after=datetime(2024, 1, 1)).match(task)
        )

    def test_date_bound_is_utc_midnight(self):
        task = Task(id="a", due="2024-01-02T00:00:00.000Z")
        self.assertTrue(TaskFilter(due_after=date(2024, 1, 1)).match(task))
        self.assertFalse(TaskFilter(due_after=date(2024, 1, 2)).match(task))

    def test_unparsable_string_bound_raises_value_error(self):
        with self.assertRaises(ValueError):
            TaskFilter(due_before="not-a-date").match(
                Task(id="a", due="2024-01-02T00:00:00.000Z")
            )


class ParentFilterTests(unittest.TestCase):
    def test_parent_exact_match(self):
        spec = TaskFilter(parent="p1")
        self.assertTrue(spec.match(Task(id="a", parent="p1")))
        self.assertFalse(spec.match(Task(id="b", parent="p2")))
        self.assertFalse(spec.match(Task(id="c")))

    def test_top_level(self):
        self.assertTrue(TaskFilter(top_level=True).match(Task(id="a")))
        self.assertFalse(TaskFilter(top_level=True).match(Task(id="b", parent="p1")))
        self.assertTrue(TaskFilter(top_level=False).match(Task(id="b", parent="p1")))
        self.assertFalse(TaskFilter(top_level=False).match(Task(id="a")))


class FieldsFilterTests(unittest.TestCase):
    def test_fields_exact_equality(self):
        spec = TaskFilter(fields={"status": "completed", "hidden": False})
        self.assertTrue(
            spec.match(Task(id="a", status="completed", hidden=False))
        )
        self.assertFalse(
            spec.match(Task(id="b", status="completed", hidden=True))
        )
        self.assertFalse(TaskFilter(fields={"status": "completed"}).match(Task(id="c")))

    def test_fields_none_equality(self):
        self.assertTrue(TaskFilter(fields={"notes": None}).match(Task(id="a")))
        self.assertFalse(
            TaskFilter(fields={"notes": None}).match(Task(id="b", notes="x"))
        )

    def test_unknown_field_raises_value_error(self):
        with self.assertRaises(ValueError):
            TaskFilter(fields={"no_such_attribute": 1}).match(Task(id="a"))


class FilterTasksTests(unittest.TestCase):
    def test_and_semantics_and_order_preserved(self):
        tasks = [
            Task(id="t1", title="Buy milk", status="needsAction"),
            Task(id="t2", title="Buy milk", status="completed"),
            Task(id="t3", title="Buy bread", status="needsAction"),
            Task(id="t4", title="Buy Milk", status="needsAction", parent="p1"),
        ]
        spec = TaskFilter(text="buy milk", status="active")
        self.assertEqual([task.id for task in filter_tasks(tasks, spec)], ["t1", "t4"])

    def test_filter_tasks_returns_new_list(self):
        tasks = [Task(id="t1", title="Keep")]
        result = filter_tasks(tasks, TaskFilter(text="keep"))
        self.assertEqual(len(result), 1)
        self.assertIsNot(result, tasks)

    def test_empty_filter_matches_everything(self):
        tasks = [Task(id="a"), Task(id="b", status="completed")]
        self.assertEqual(len(filter_tasks(tasks, TaskFilter())), 2)


if __name__ == "__main__":
    unittest.main()
