"""Tests for gtasks.tasks: paths, bodies, query params, pagination and UNSET semantics."""

import unittest
from datetime import datetime, timedelta, timezone

from gtasks.filters import TaskFilter
from gtasks.models import Task, parse_rfc3339
from gtasks.tasks import TaskPage, TasksResource, UNSET

from tests.support import FakeTransport, as_datetime

UTC = timezone.utc


def page(items, token=None):
    payload = {"items": items}
    if token is not None:
        payload["nextPageToken"] = token
    return payload


def make_task(task_id="t1", **kwargs):
    payload = {"id": task_id}
    payload.update(kwargs)
    return payload


class TasksResourceTests(unittest.TestCase):
    def make(self, responses):
        transport = FakeTransport(responses)
        return TasksResource(transport), transport

    # -- list ---------------------------------------------------------------
    def test_list_defaults(self):
        resource, transport = self.make([page([make_task("t1", title="One")])])
        result = resource.list("l1")
        self.assertIsInstance(result, TaskPage)
        self.assertEqual(len(result.items), 1)
        self.assertIsInstance(result.items[0], Task)
        self.assertIsNone(result.next_page_token)
        call = transport.last
        self.assertEqual(call.method, "GET")
        self.assertEqual(call.path, "/lists/l1/tasks")
        self.assertEqual(call.params, {"maxResults": 100})

    def test_list_all_query_params(self):
        resource, transport = self.make([page([])])
        resource.list(
            "l1",
            max_results=7,
            page_token="tok",
            show_completed=True,
            show_deleted=False,
            show_hidden=True,
            updated_min=datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
            due_min="2024-01-01T00:00:00.000Z",
            due_max=datetime(2024, 2, 1, tzinfo=UTC),
        )
        params = transport.last.params
        self.assertEqual(params["maxResults"], 7)
        self.assertEqual(params["pageToken"], "tok")
        self.assertIs(params["showCompleted"], True)
        self.assertIs(params["showDeleted"], False)
        self.assertIs(params["showHidden"], True)
        self.assertEqual(
            as_datetime(params["updatedMin"]), datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
        )
        self.assertEqual(params["dueMin"], "2024-01-01T00:00:00.000Z")
        self.assertEqual(as_datetime(params["dueMax"]), datetime(2024, 2, 1, tzinfo=UTC))

    def test_list_omits_unset_booleans(self):
        resource, transport = self.make([page([])])
        resource.list("l1", show_completed=None)
        self.assertNotIn("showCompleted", transport.last.params)
        self.assertNotIn("showDeleted", transport.last.params)
        self.assertNotIn("showHidden", transport.last.params)

    def test_list_pagination(self):
        resource, transport = self.make([page([], token="p2")])
        result = resource.list("l1", page_token="p1")
        self.assertEqual(result.next_page_token, "p2")
        self.assertEqual(transport.last.params["pageToken"], "p1")

    # -- iterate ------------------------------------------------------------
    def test_iterate_follows_pages_and_page_size(self):
        resource, transport = self.make(
            [
                page([make_task("t1"), make_task("t2")], token="p2"),
                page([make_task("t3")]),
            ]
        )
        items = list(resource.iterate("l1", page_size=50, show_completed=False))
        self.assertEqual([item.id for item in items], ["t1", "t2", "t3"])
        self.assertEqual(len(transport.calls), 2)
        first, second = transport.calls
        self.assertEqual(first.params["maxResults"], 50)
        self.assertIs(first.params["showCompleted"], False)
        self.assertNotIn("pageToken", first.params)
        self.assertEqual(second.params["pageToken"], "p2")

    def test_iterate_applies_filter(self):
        resource, transport = self.make(
            [
                page(
                    [
                        make_task("t1", title="Keep me"),
                        make_task("t2", title="drop me"),
                    ],
                    token="p2",
                ),
                page([make_task("t3", notes="keep this note")]),
            ]
        )
        spec = TaskFilter(text="keep")
        items = list(resource.iterate("l1", filter=spec))
        self.assertEqual([item.id for item in items], ["t1", "t3"])
        self.assertEqual(len(transport.calls), 2)

    # -- get ----------------------------------------------------------------
    def test_get_quotes_default_and_slashes(self):
        resource, transport = self.make([make_task("t/1", title="One")])
        result = resource.get("@default", "t/1")
        self.assertEqual(result.id, "t/1")
        self.assertEqual(
            transport.last.path, "/lists/%40default/tasks/t%2F1"
        )
        self.assertEqual(transport.last.method, "GET")

    # -- create -------------------------------------------------------------
    def test_create_minimal(self):
        resource, transport = self.make([make_task("new", title="T")])
        result = resource.create("l1", title="T")
        self.assertEqual(result.id, "new")
        call = transport.last
        self.assertEqual(call.method, "POST")
        self.assertEqual(call.path, "/lists/l1/tasks")
        self.assertEqual(call.body, {"title": "T"})
        self.assertFalse(call.params)

    def test_create_full_body_and_query_parent_previous(self):
        resource, transport = self.make([make_task("new", title="T")])
        resource.create(
            "l1",
            title="T",
            notes="N",
            due=datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
            status="completed",
            parent="parent-1",
            previous="prev-1",
        )
        call = transport.last
        self.assertEqual(call.body["title"], "T")
        self.assertEqual(call.body["notes"], "N")
        self.assertEqual(call.body["status"], "completed")
        self.assertEqual(
            as_datetime(call.body["due"]), datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)
        )
        self.assertNotIn("parent", call.body)
        self.assertEqual(call.params, {"parent": "parent-1", "previous": "prev-1"})

    def test_create_omits_none_fields(self):
        resource, transport = self.make([make_task("new", title="T")])
        resource.create("l1", title="T", notes=None, due=None, status=None, parent=None)
        call = transport.last
        self.assertEqual(call.body, {"title": "T"})
        self.assertFalse(call.params)

    # -- update -------------------------------------------------------------
    def test_update_only_provided_fields(self):
        resource, transport = self.make([make_task("t1", title="New")])
        resource.update("l1", "t1", title="New")
        call = transport.last
        self.assertEqual(call.method, "PATCH")
        self.assertEqual(call.path, "/lists/l1/tasks/t1")
        self.assertEqual(call.body, {"title": "New"})

    def test_update_explicit_none_clears_field(self):
        resource, transport = self.make([make_task("t1")])
        resource.update("l1", "t1", notes=None, due=None)
        call = transport.last
        self.assertEqual(call.body, {"notes": None, "due": None})

    def test_update_all_fields_including_none(self):
        resource, transport = self.make([make_task("t1")])
        resource.update(
            "l1",
            "t1",
            title="T",
            notes="N",
            due=None,
            status="completed",
            completed="2024-01-02T03:04:05.000Z",
        )
        self.assertEqual(
            transport.last.body,
            {
                "title": "T",
                "notes": "N",
                "due": None,
                "status": "completed",
                "completed": "2024-01-02T03:04:05.000Z",
            },
        )

    def test_update_without_arguments_sends_empty_patch(self):
        resource, transport = self.make([make_task("t1")])
        resource.update("l1", "t1")
        call = transport.last
        self.assertEqual(call.method, "PATCH")
        self.assertFalse(call.body or {})

    def test_update_invalid_status_raises_value_error(self):
        resource, transport = self.make([])
        with self.assertRaises(ValueError):
            resource.update("l1", "t1", status="bogus")
        self.assertEqual(transport.calls, [])

    # -- delete / clear -----------------------------------------------------
    def test_delete(self):
        resource, transport = self.make([None])
        self.assertIsNone(resource.delete("l1", "t1"))
        self.assertEqual(transport.last.method, "DELETE")
        self.assertEqual(transport.last.path, "/lists/l1/tasks/t1")

    def test_clear(self):
        resource, transport = self.make([None])
        self.assertIsNone(resource.clear("l1"))
        self.assertEqual(transport.last.method, "POST")
        self.assertEqual(transport.last.path, "/lists/l1/clear")

    # -- move ---------------------------------------------------------------
    def test_move_with_parent_and_previous(self):
        resource, transport = self.make([make_task("t1")])
        resource.move("l1", "t1", parent="p1", previous="s1")
        self.assertEqual(transport.last.method, "POST")
        self.assertEqual(transport.last.path, "/lists/l1/tasks/t1/move")
        self.assertEqual(transport.last.params, {"parent": "p1", "previous": "s1"})

    def test_move_without_params(self):
        resource, transport = self.make([make_task("t1")])
        resource.move("l1", "t1")
        call = transport.last
        self.assertFalse(call.params)
        self.assertEqual(call.path, "/lists/l1/tasks/t1/move")

    def test_move_explicit_none_is_omitted(self):
        resource, transport = self.make([make_task("t1")])
        resource.move("l1", "t1", parent=None, previous=None)
        self.assertFalse(transport.last.params)

    # -- complete / uncomplete ---------------------------------------------
    def test_complete(self):
        resource, transport = self.make([make_task("t1", status="completed")])
        resource.complete("l1", "t1")
        call = transport.last
        self.assertEqual(call.method, "PATCH")
        self.assertEqual(call.path, "/lists/l1/tasks/t1")
        self.assertEqual(call.body, {"status": "completed"})

    def test_uncomplete(self):
        resource, transport = self.make([make_task("t1")])
        resource.uncomplete("l1", "t1")
        call = transport.last
        self.assertEqual(call.method, "PATCH")
        self.assertEqual(call.body, {"status": "needsAction", "completed": None})

    # -- subtasks -----------------------------------------------------------
    def test_subtasks_filters_by_parent_and_completion(self):
        items = [
            make_task("t1", parent="p1"),
            make_task("t2", parent="p1", status="completed"),
            make_task("t3", parent="other"),
            make_task("t4"),
        ]
        resource, transport = self.make([page(items)])
        subtasks = resource.subtasks("l1", "p1")
        self.assertEqual([task.id for task in subtasks], ["t1", "t2"])
        self.assertEqual(len(transport.calls), 1)

    def test_subtasks_excludes_completed_when_asked(self):
        items = [
            make_task("t1", parent="p1"),
            make_task("t2", parent="p1", status="completed"),
        ]
        resource, _ = self.make([page(items)])
        subtasks = resource.subtasks("l1", "p1", include_completed=False)
        self.assertEqual([task.id for task in subtasks], ["t1"])

    def test_create_subtask(self):
        resource, transport = self.make([make_task("new", title="Child", parent="p1")])
        result = resource.create_subtask(
            "l1",
            "p1",
            title="Child",
            notes="N",
            due=datetime(2024, 1, 2, tzinfo=UTC),
        )
        self.assertEqual(result.id, "new")
        call = transport.last
        self.assertEqual(call.method, "POST")
        self.assertEqual(call.path, "/lists/l1/tasks")
        self.assertEqual(call.body["title"], "Child")
        self.assertEqual(call.body["notes"], "N")
        self.assertEqual(as_datetime(call.body["due"]), datetime(2024, 1, 2, tzinfo=UTC))
        self.assertNotIn("parent", call.body)
        self.assertEqual(call.params, {"parent": "p1"})

    def test_create_subtask_omits_none(self):
        resource, transport = self.make([make_task("new", parent="p1")])
        resource.create_subtask("l1", "p1", title="Child")
        self.assertEqual(transport.last.body, {"title": "Child"})
        self.assertEqual(transport.last.params, {"parent": "p1"})


class UnsetSentinelTests(unittest.TestCase):
    def test_sentinel_is_shared(self):
        from gtasks.tasks import UNSET as again

        self.assertIs(again, UNSET)
        self.assertNotIsInstance(UNSET, (str, int, bool, dict, list))


if __name__ == "__main__":
    unittest.main()
