"""Tests for gtasks.cli: every subcommand, JSON shapes, flags and exit codes."""

import io
import json
import os
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from gtasks.auth import Token
from gtasks.cli import main, run
from gtasks.errors import ApiError, GtasksError
from gtasks.models import Task, TaskList
from gtasks.sync import SyncChange, SyncReport, SyncState
from gtasks.tasks import UNSET

from tests.support import (
    FakeClient,
    as_datetime,
    json_items,
    last_call,
    patch_attr,
)


def invoke(argv, client=None):
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            code = main(argv) if client is None else main(argv, client=client)
        except SystemExit as exc:
            code = exc.code if exc.code is not None else 0
    return code, stdout.getvalue(), stderr.getvalue()


def find_kwargs(client, prefix):
    return last_call(client, prefix)[2]


@contextmanager
def env(**values):
    keys = ("GTASKS_SYNC_STATE", "GTASKS_CLIENT_SECRET", "GTASKS_TOKEN")
    original = {key: os.environ.get(key) for key in keys}
    for key in keys:
        os.environ.pop(key, None)
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class TempDirMixin(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)


class ListsCommandTests(TempDirMixin):
    def test_lists_human_output(self):
        client = FakeClient(tasklists=[TaskList("l1", "One"), TaskList("l2", "Two")])
        code, out, err = invoke(["lists"], client)
        self.assertEqual(code, 0)
        self.assertEqual(out, "l1\tOne\nl2\tTwo\n")
        self.assertEqual(err, "")

    def test_lists_json(self):
        client = FakeClient(tasklists=[TaskList("l1", "One")])
        code, out, err = invoke(["lists", "--json"], client)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual([item["id"] for item in json_items(payload)], ["l1"])
        self.assertEqual(json_items(payload)[0]["title"], "One")

    def test_lists_error_exit_code(self):
        client = FakeClient()

        def boom(**kwargs):
            raise ApiError(404, "No such list")

        client.tasklists.list = boom
        client.tasklists.iterate = boom
        code, out, err = invoke(["lists"], client)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("error: HTTP 404: No such list", err)


class TasksCommandTests(TempDirMixin):
    def client(self):
        return FakeClient(
            tasks=[
                Task("t1", "First"),
                Task(
                    "t2",
                    "Second",
                    status="completed",
                    due="2024-01-02T03:04:05.000Z",
                ),
            ]
        )

    def test_tasks_human_output_includes_completed_by_default(self):
        code, out, err = invoke(["tasks", "L1"], self.client())
        self.assertEqual(code, 0)
        self.assertEqual(
            out,
            "t1\tneedsAction\t-\tFirst\n"
            "t2\tcompleted\t2024-01-02T03:04:05.000Z\tSecond\n",
        )

    def test_tasks_json(self):
        code, out, _ = invoke(["tasks", "L1", "--json"], self.client())
        self.assertEqual(code, 0)
        payload = json_items(json.loads(out))
        self.assertEqual([item["id"] for item in payload], ["t1", "t2"])
        self.assertEqual(payload[0]["status"], "needsAction")

    def test_show_completed_flags(self):
        client = self.client()
        invoke(["tasks", "L1", "--no-completed"], client)
        self.assertIs(find_kwargs(client, "tasks.iterate")["show_completed"], False)

        client = self.client()
        invoke(["tasks", "L1", "--completed"], client)
        self.assertIs(find_kwargs(client, "tasks.iterate")["show_completed"], True)

    def test_text_filter(self):
        client = FakeClient(tasks=[Task("t1", "Buy Milk"), Task("t2", "Bread")])
        code, out, _ = invoke(["tasks", "L1", "--text", "milk"], client)
        self.assertEqual(code, 0)
        self.assertIn("t1", out)
        self.assertNotIn("t2", out)
        spec = find_kwargs(client, "tasks.iterate")["filter"]
        self.assertIsNotNone(spec, "CLI did not pass a TaskFilter to tasks.iterate")
        self.assertEqual(spec.text, "milk")

    def test_filter_flags(self):
        client = self.client()
        code, _, _ = invoke(
            [
                "tasks",
                "L1",
                "--text",
                "milk",
                "--status",
                "active",
                "--due-before",
                "2024-02-01T00:00:00Z",
                "--due-after",
                "2024-01-01T00:00:00Z",
                "--completed-before",
                "2024-03-01T00:00:00Z",
                "--completed-after",
                "2024-01-01T00:00:00Z",
                "--parent",
                "p1",
                "--top-level",
            ],
            client,
        )
        self.assertEqual(code, 0)
        spec = find_kwargs(client, "tasks.iterate")["filter"]
        self.assertIsNotNone(spec)
        self.assertEqual(spec.text, "milk")
        self.assertEqual(spec.status, "active")
        self.assertEqual(spec.parent, "p1")
        self.assertTrue(spec.top_level)
        self.assertEqual(
            as_datetime(spec.due_before), as_datetime("2024-02-01T00:00:00Z")
        )
        self.assertEqual(
            as_datetime(spec.due_after), as_datetime("2024-01-01T00:00:00Z")
        )
        self.assertEqual(
            as_datetime(spec.completed_before), as_datetime("2024-03-01T00:00:00Z")
        )
        self.assertEqual(
            as_datetime(spec.completed_after), as_datetime("2024-01-01T00:00:00Z")
        )


class ShowCommandTests(TempDirMixin):
    def test_show_human(self):
        client = FakeClient(tasks=[Task("t1", "First", notes="N")])
        code, out, _ = invoke(["show", "L1", "t1"], client)
        self.assertEqual(code, 0)
        self.assertIn("id: t1", out)
        self.assertIn("title: First", out)

    def test_show_json(self):
        client = FakeClient(tasks=[Task("t1", "First", notes="N")])
        code, out, _ = invoke(["show", "L1", "t1", "--json"], client)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["id"], "t1")
        self.assertEqual(payload["title"], "First")
        self.assertEqual(payload["notes"], "N")


class CreateCommandTests(TempDirMixin):
    def test_create_human_prints_id(self):
        client = FakeClient()
        code, out, _ = invoke(["create", "L1", "--title", "New"], client)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "new-task")
        self.assertEqual(find_kwargs(client, "tasks.create")["title"], "New")

    def test_create_with_notes_due_parent(self):
        client = FakeClient()
        code, out, _ = invoke(
            [
                "create",
                "L1",
                "--title",
                "New",
                "--notes",
                "N",
                "--due",
                "2024-01-02T03:04:05Z",
                "--parent",
                "p1",
                "--json",
            ],
            client,
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["id"], "new-task")
        self.assertEqual(payload["title"], "New")
        kwargs = find_kwargs(client, "tasks.create")
        self.assertEqual(kwargs["notes"], "N")
        self.assertEqual(as_datetime(kwargs["due"]), as_datetime("2024-01-02T03:04:05Z"))
        self.assertEqual(kwargs["parent"], "p1")


class UpdateCommandTests(TempDirMixin):
    def test_update_title_only(self):
        client = FakeClient(tasks=[Task("t1", "First")])
        code, out, _ = invoke(["update", "L1", "t1", "--title", "New"], client)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "t1")
        kwargs = find_kwargs(client, "tasks.update")
        self.assertEqual(kwargs["title"], "New")
        self.assertIn(kwargs.get("notes", UNSET), (UNSET, None))

    def test_update_clear_due_sends_none(self):
        client = FakeClient(tasks=[Task("t1", "First")])
        code, _, _ = invoke(["update", "L1", "t1", "--clear-due"], client)
        self.assertEqual(code, 0)
        kwargs = find_kwargs(client, "tasks.update")
        self.assertIsNone(kwargs["due"])

    def test_update_due(self):
        client = FakeClient(tasks=[Task("t1", "First")])
        invoke(["update", "L1", "t1", "--due", "2024-01-02T03:04:05Z"], client)
        kwargs = find_kwargs(client, "tasks.update")
        self.assertEqual(as_datetime(kwargs["due"]), as_datetime("2024-01-02T03:04:05Z"))

    def test_update_status(self):
        client = FakeClient(tasks=[Task("t1", "First")])
        invoke(["update", "L1", "t1", "--status", "completed"], client)
        self.assertEqual(find_kwargs(client, "tasks.update")["status"], "completed")

    def test_update_json(self):
        client = FakeClient(tasks=[Task("t1", "First")])
        code, out, _ = invoke(["update", "L1", "t1", "--title", "New", "--json"], client)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["id"], "t1")


class SimpleCommandTests(TempDirMixin):
    def test_delete(self):
        client = FakeClient()
        code, out, _ = invoke(["delete", "L1", "t1"], client)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "deleted t1")

    def test_clear(self):
        client = FakeClient()
        code, out, _ = invoke(["clear", "L1"], client)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "cleared L1")

    def test_move(self):
        client = FakeClient(tasks=[Task("t1", "First")])
        code, out, _ = invoke(
            ["move", "L1", "t1", "--parent", "p1", "--previous", "q1"], client
        )
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "t1")
        kwargs = find_kwargs(client, "tasks.move")
        self.assertEqual(kwargs["parent"], "p1")
        self.assertEqual(kwargs["previous"], "q1")

    def test_subtasks(self):
        client = FakeClient(tasks=[Task("c1", "Child", parent="p1")])
        code, out, _ = invoke(["subtasks", "L1", "p1"], client)
        self.assertEqual(code, 0)
        self.assertEqual(out, "c1\tneedsAction\t-\tChild\n")
        kwargs = last_call(client, "tasks.subtasks")[2]
        self.assertTrue(kwargs["include_completed"])

    def test_subtasks_no_completed(self):
        client = FakeClient(tasks=[Task("c1", "Child", parent="p1")])
        invoke(["subtasks", "L1", "p1", "--no-completed"], client)
        kwargs = last_call(client, "tasks.subtasks")[2]
        self.assertFalse(kwargs["include_completed"])


class FakeSyncEngine:
    instances: list["FakeSyncEngine"] = []

    def __init__(self, transport=None):
        self.transport = transport
        self.pulls = []
        FakeSyncEngine.instances.append(self)

    def pull(self, state, *, full=False):
        self.pulls.append((state, full))
        report = SyncReport(
            changes=[
                SyncChange("tasklist", "created", "l1"),
                SyncChange("task", "updated", "t1", tasklist_id="l1"),
                SyncChange("task", "updated", "t2", tasklist_id="l1"),
                SyncChange("task", "deleted", "t3", tasklist_id="l1"),
                SyncChange("task", "deleted", "t4", tasklist_id="l1"),
                SyncChange("task", "deleted", "t5", tasklist_id="l1"),
            ],
            synced_at="2024-01-02T03:04:05.000Z",
        )
        state.synced_at = report.synced_at
        return report


class SyncCommandTests(TempDirMixin):
    def setUp(self):
        super().setUp()
        FakeSyncEngine.instances = []

    def run_sync(self, extra_args=(), client=None):
        with patch_attr(
            ["gtasks.cli", "gtasks.sync"], "SyncEngine", FakeSyncEngine
        ):
            return invoke(["sync", *extra_args], client or FakeClient())

    def test_sync_human_output_and_state_saved(self):
        state_path = self.dir / "sync.json"
        code, out, err = self.run_sync(["--state", str(state_path)])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "created=1 updated=2 deleted=3")
        self.assertEqual(err, "")
        self.assertTrue(state_path.exists())
        parsed = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(parsed["synced_at"], "2024-01-02T03:04:05.000Z")
        engine = FakeSyncEngine.instances[-1]
        self.assertEqual(len(engine.pulls), 1)
        state, full = engine.pulls[0]
        self.assertIsInstance(state, SyncState)
        self.assertFalse(full)

    def test_sync_json_shape(self):
        state_path = self.dir / "sync.json"
        code, out, _ = self.run_sync(["--state", str(state_path), "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["synced_at"], "2024-01-02T03:04:05.000Z")
        self.assertEqual(payload["counts"], {"created": 1, "updated": 2, "deleted": 3})
        self.assertEqual(payload["changes"][0]["kind"], "tasklist")
        self.assertEqual(payload["changes"][0]["action"], "created")
        self.assertEqual(payload["changes"][0]["id"], "l1")

    def test_sync_full_flag(self):
        state_path = self.dir / "sync.json"
        code, _, _ = self.run_sync(["--state", str(state_path), "--full"])
        self.assertEqual(code, 0)
        self.assertTrue(FakeSyncEngine.instances[-1].pulls[0][1])

    def test_sync_default_state_from_env(self):
        state_path = self.dir / "env-sync.json"
        with env(GTASKS_SYNC_STATE=str(state_path)):
            code, _, _ = self.run_sync()
        self.assertEqual(code, 0)
        self.assertTrue(state_path.exists())


class LoginCommandTests(TempDirMixin):
    def test_login_runs_flow_and_prints_token_path(self):
        secret = self.dir / "client_secret.json"
        secret.write_text(json.dumps({"installed": {"client_id": "cid"}}), encoding="utf-8")
        token_path = self.dir / "token.json"
        token = Token("access-token", refresh_token="refresh")
        fake_flow = mock.MagicMock()
        fake_flow.authorize.return_value = token
        fake_flow.store.path = token_path
        fake_cls = mock.MagicMock()
        fake_cls.return_value = fake_flow
        fake_cls.from_client_secret_file.return_value = fake_flow
        with patch_attr(
            ["gtasks.cli", "gtasks.auth"], "OAuth2Flow", fake_cls
        ):
            code, out, err = invoke(
                ["login", "--client-secret", str(secret), "--token", str(token_path)]
            )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn(str(token_path), out)
        self.assertTrue(fake_flow.authorize.called)


class ExitCodeTests(TempDirMixin):
    def test_keyboard_interrupt_returns_130(self):
        client = FakeClient()

        def interrupt(**kwargs):
            raise KeyboardInterrupt()

        client.tasklists.list = interrupt
        client.tasklists.iterate = interrupt
        code, _, _ = invoke(["lists"], client)
        self.assertEqual(code, 130)

    def test_gtasks_error_returns_1(self):
        client = FakeClient()

        def boom(**kwargs):
            raise GtasksError("kaput")

        client.tasklists.list = boom
        client.tasklists.iterate = boom
        code, _, err = invoke(["lists"], client)
        self.assertEqual(code, 1)
        self.assertIn("error: kaput", err)

    def test_help_exits_zero(self):
        code, out, _ = invoke(["--help"], FakeClient())
        self.assertEqual(code, 0)
        self.assertIn("gtasks", out)
        self.assertIn("lists", out)

    def test_subcommand_help_exits_zero(self):
        code, out, _ = invoke(["tasks", "--help"], FakeClient())
        self.assertEqual(code, 0)
        self.assertIn("tasklist", out.lower())

    def test_unknown_command_exits_two(self):
        code, _, _ = invoke(["bogus"], FakeClient())
        self.assertEqual(code, 2)

    def test_missing_required_argument_exits_two(self):
        code, _, _ = invoke(["delete", "L1"], FakeClient())
        self.assertEqual(code, 2)


class ClientResolutionTests(TempDirMixin):
    def test_missing_client_uses_from_env(self):
        client = FakeClient(tasklists=[TaskList("l1", "One")])
        fake_cls = mock.MagicMock()
        fake_cls.from_env.return_value = client
        with patch_attr(
            ["gtasks.cli", "gtasks.client"], "GoogleTasksClient", fake_cls
        ):
            code, out, _ = invoke(["lists"])
        self.assertEqual(code, 0)
        self.assertEqual(out, "l1\tOne\n")
        self.assertTrue(fake_cls.from_env.called)


class RunTests(TempDirMixin):
    def test_run_raises_system_exit_with_main_code(self):
        with patch_attr(["gtasks.cli"], "main", lambda *args, **kwargs: 0):
            with self.assertRaises(SystemExit) as ctx:
                run()
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
