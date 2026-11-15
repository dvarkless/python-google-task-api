"""Tests for gtasks.tasklists: paths, bodies, pagination and iterate."""

import unittest

from gtasks.errors import NotFoundError
from gtasks.models import TaskList
from gtasks.tasklists import TaskListPage, TaskListsResource

from tests.support import FakeTransport


def page(items, token=None):
    payload = {"items": items}
    if token is not None:
        payload["nextPageToken"] = token
    return payload


class TaskListsResourceTests(unittest.TestCase):
    def make(self, responses):
        transport = FakeTransport(responses)
        return TaskListsResource(transport), transport

    def test_list_defaults(self):
        resource, transport = self.make([page([{"id": "l1", "title": "One"}])])
        result = resource.list()
        self.assertIsInstance(result, TaskListPage)
        self.assertEqual(len(result.items), 1)
        self.assertIsInstance(result.items[0], TaskList)
        self.assertEqual(result.items[0].title, "One")
        self.assertIsNone(result.next_page_token)
        call = transport.last
        self.assertEqual(call.method, "GET")
        self.assertEqual(call.path, "/users/@me/lists")
        self.assertEqual(call.params, {"maxResults": 1000})

    def test_list_max_results_and_page_token(self):
        resource, transport = self.make(
            [page([{"id": "l2", "title": "Two"}], token="next-token")]
        )
        result = resource.list(max_results=5, page_token="tok-1")
        self.assertEqual(result.next_page_token, "next-token")
        call = transport.last
        self.assertEqual(call.params, {"maxResults": 5, "pageToken": "tok-1"})

    def test_iterate_follows_pages(self):
        resource, transport = self.make(
            [
                page([{"id": "l1", "title": "One"}], token="p2"),
                page([{"id": "l2", "title": "Two"}, {"id": "l3", "title": "Three"}]),
            ]
        )
        items = list(resource.iterate())
        self.assertEqual([item.id for item in items], ["l1", "l2", "l3"])
        self.assertEqual(len(transport.calls), 2)
        self.assertNotIn("pageToken", transport.calls[0].params)
        self.assertEqual(transport.calls[1].params["pageToken"], "p2")

    def test_get_quotes_id(self):
        resource, transport = self.make([{"id": "a/b c", "title": "Odd"}])
        result = resource.get("a/b c")
        self.assertEqual(result.id, "a/b c")
        call = transport.last
        self.assertEqual(call.method, "GET")
        self.assertEqual(call.path, "/users/@me/lists/a%2Fb%20c")

    def test_create(self):
        resource, transport = self.make([{"id": "new", "title": "Groceries"}])
        result = resource.create("Groceries")
        self.assertEqual(result.id, "new")
        call = transport.last
        self.assertEqual(call.method, "POST")
        self.assertEqual(call.path, "/users/@me/lists")
        self.assertEqual(call.body, {"title": "Groceries"})
        self.assertIsNone(call.params)

    def test_update_is_patch_with_title_body(self):
        resource, transport = self.make([{"id": "l1", "title": "Renamed"}])
        result = resource.update("l1", "Renamed")
        self.assertEqual(result.title, "Renamed")
        call = transport.last
        self.assertEqual(call.method, "PATCH")
        self.assertEqual(call.path, "/users/@me/lists/l1")
        self.assertEqual(call.body, {"title": "Renamed"})

    def test_delete(self):
        resource, transport = self.make([None])
        self.assertIsNone(resource.delete("l1"))
        call = transport.last
        self.assertEqual(call.method, "DELETE")
        self.assertEqual(call.path, "/users/@me/lists/l1")

    def test_get_propagates_not_found(self):
        resource, _ = self.make([NotFoundError(404, "No such list")])
        with self.assertRaises(NotFoundError):
            resource.get("missing")


if __name__ == "__main__":
    unittest.main()
