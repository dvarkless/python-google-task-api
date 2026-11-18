"""argparse command line interface for gtasks."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .auth import (
    DEFAULT_TOKEN_PATH,
    OAuth2Flow,
    TokenStore,
    create_credentials,
    load_client_config,
    resolve_client_secret_path,
    save_client_config,
)
from .client import GoogleTasksClient
from .errors import GtasksError
from .filters import TaskFilter
from .models import Task, parse_rfc3339, to_rfc3339
from .sync import SyncEngine, SyncReport, SyncState
from .tasks import UNSET
from .transport import HttpTransport

SYNC_STATE_ENV = "GTASKS_SYNC_STATE"
CLIENT_SECRET_ENV = "GTASKS_CLIENT_SECRET"
TOKEN_ENV = "GTASKS_TOKEN"
DEFAULT_SYNC_STATE_PATH = Path.home() / ".cache" / "gtasks" / "sync.json"


def _add_json_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", dest="as_json", help="print JSON")


def _add_global_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--client-secret", metavar="PATH", default=None)
    parser.add_argument("--token", metavar="PATH", default=None)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gtasks", description="Google Tasks command line client"
    )
    _add_global_options(parser)
    commands = parser.add_subparsers(dest="command", required=True)

    login = commands.add_parser("login", help="authorize with Google OAuth2")
    login.add_argument("--client-secret", metavar="PATH", default=argparse.SUPPRESS)
    login.add_argument("--token", metavar="PATH", default=argparse.SUPPRESS)
    login.set_defaults(handler=_cmd_login)

    lists = commands.add_parser("lists", help="list task lists")
    lists.add_argument(
        "--title",
        default=None,
        metavar="TITLE",
        help="only lists whose title matches exactly (case-sensitive)",
    )
    _add_json_option(lists)
    lists.set_defaults(handler=_cmd_lists)

    list_create = commands.add_parser("list-create", help="create a task list")
    list_create.add_argument("title", metavar="TITLE")
    _add_json_option(list_create)
    list_create.set_defaults(handler=_cmd_list_create)

    list_update = commands.add_parser("list-update", help="rename a task list")
    list_update.add_argument("tasklist", metavar="TASKLIST")
    list_update.add_argument("title", metavar="TITLE")
    _add_json_option(list_update)
    list_update.set_defaults(handler=_cmd_list_update)

    list_delete = commands.add_parser("list-delete", help="delete a task list")
    list_delete.add_argument("tasklist", metavar="TASKLIST")
    list_delete.set_defaults(handler=_cmd_list_delete)

    tasks = commands.add_parser("tasks", help="list tasks in a task list")
    tasks.add_argument("tasklist", metavar="TASKLIST")
    tasks.add_argument(
        "--completed",
        action=argparse.BooleanOptionalAction,
        default=True,
        dest="show_completed",
    )
    _add_json_option(tasks)
    _add_filter_options(tasks)
    tasks.set_defaults(handler=_cmd_tasks)

    show = commands.add_parser("show", help="show one task")
    show.add_argument("tasklist", metavar="TASKLIST")
    show.add_argument("task", metavar="TASK")
    _add_json_option(show)
    show.set_defaults(handler=_cmd_show)

    create = commands.add_parser("create", help="create a task")
    create.add_argument("tasklist", metavar="TASKLIST")
    create.add_argument("--title", required=True)
    create.add_argument("--notes", default=None)
    create.add_argument("--due", default=None)
    create.add_argument("--parent", default=None)
    _add_json_option(create)
    create.set_defaults(handler=_cmd_create)

    update = commands.add_parser("update", help="update a task")
    update.add_argument("tasklist", metavar="TASKLIST")
    update.add_argument("task", metavar="TASK")
    update.add_argument("--title", default=UNSET)
    update.add_argument("--notes", default=UNSET)
    due_group = update.add_mutually_exclusive_group()
    due_group.add_argument("--due", default=UNSET)
    due_group.add_argument("--clear-due", action="store_true", dest="clear_due")
    update.add_argument("--status", default=UNSET)
    update.add_argument(
        "--completed",
        default=UNSET,
        metavar="TIMESTAMP",
        help="completion time (RFC 3339 or YYYY-MM-DD); implies --status completed",
    )
    _add_json_option(update)
    update.set_defaults(handler=_cmd_update)

    delete = commands.add_parser("delete", help="delete a task")
    delete.add_argument("tasklist", metavar="TASKLIST")
    delete.add_argument("task", metavar="TASK")
    delete.set_defaults(handler=_cmd_delete)

    clear = commands.add_parser("clear", help="clear completed tasks")
    clear.add_argument("tasklist", metavar="TASKLIST")
    clear.set_defaults(handler=_cmd_clear)

    move = commands.add_parser("move", help="move or reorder a task")
    move.add_argument("tasklist", metavar="TASKLIST")
    move.add_argument("task", metavar="TASK")
    move.add_argument("--parent", default=None)
    move.add_argument("--previous", default=None)
    _add_json_option(move)
    move.set_defaults(handler=_cmd_move)

    subtasks = commands.add_parser("subtasks", help="list subtasks of a task")
    subtasks.add_argument("tasklist", metavar="TASKLIST")
    subtasks.add_argument("parent", metavar="PARENT")
    subtasks.add_argument(
        "--no-completed",
        action="store_false",
        dest="include_completed",
        default=True,
    )
    _add_json_option(subtasks)
    subtasks.set_defaults(handler=_cmd_subtasks)

    sync = commands.add_parser("sync", help="pull remote changes into local state")
    sync.add_argument("--state", metavar="PATH", default=None)
    sync.add_argument("--full", action="store_true", dest="full")
    _add_json_option(sync)
    sync.set_defaults(handler=_cmd_sync)

    return parser


def _add_filter_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--text", default=None)
    parser.add_argument("--status", default=None)
    parser.add_argument("--due-before", default=None)
    parser.add_argument("--due-after", default=None)
    parser.add_argument("--completed-before", default=None)
    parser.add_argument("--completed-after", default=None)
    parser.add_argument("--parent", default=None)
    parser.add_argument("--top-level", action="store_true", dest="top_level")


def _build_filter(args: argparse.Namespace) -> TaskFilter | None:
    spec = TaskFilter(
        text=args.text,
        status=args.status,
        due_before=args.due_before,
        due_after=args.due_after,
        completed_before=args.completed_before,
        completed_after=args.completed_after,
        parent=args.parent,
        top_level=True if args.top_level else None,
    )
    if all(
        value is None
        for value in (
            spec.text,
            spec.status,
            spec.due_before,
            spec.due_after,
            spec.completed_before,
            spec.completed_after,
            spec.parent,
            spec.top_level,
        )
    ):
        return None
    return spec


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _task_line(task: Task) -> str:
    return f"{task.id}\t{task.status}\t{task.due or '-'}\t{task.title}"


def _print_task_lines(tasks: Sequence[Task]) -> None:
    for task in tasks:
        print(_task_line(task))


def _print_task_detail(task: Task) -> None:
    for key, value in asdict(task).items():
        print(f"{key}: {'-' if value is None else value}")


def _resolve_client(
    args: argparse.Namespace, client: GoogleTasksClient | None
) -> GoogleTasksClient:
    if client is not None:
        return client
    client_secret = getattr(args, "client_secret", None)
    token_path = getattr(args, "token", None)
    if client_secret or token_path:
        return GoogleTasksClient(HttpTransport(create_credentials(client_secret, token_path)))
    return GoogleTasksClient.from_env()


def _cmd_login(args: argparse.Namespace, client: GoogleTasksClient | None) -> int:
    config_path = resolve_client_secret_path(getattr(args, "client_secret", None))
    token_path = Path(
        getattr(args, "token", None)
        or os.environ.get(TOKEN_ENV)
        or DEFAULT_TOKEN_PATH
    )
    store = TokenStore(token_path)
    client_config = load_client_config(config_path)
    OAuth2Flow(client_config, store=store).authorize()
    save_client_config(client_config)
    print(store.path)
    return 0


def _cmd_lists(args: argparse.Namespace, client: GoogleTasksClient) -> int:
    tasklists = []
    page_token = None
    while True:
        page = client.tasklists.list(page_token=page_token)
        tasklists.extend(page.items)
        page_token = page.next_page_token
        if not page_token:
            break
    if args.title is not None:
        tasklists = [tasklist for tasklist in tasklists if tasklist.title == args.title]
    if args.as_json:
        _print_json([asdict(tasklist) for tasklist in tasklists])
    else:
        for tasklist in tasklists:
            print(f"{tasklist.id}\t{tasklist.title}")
    return 0


def _cmd_list_create(args: argparse.Namespace, client: GoogleTasksClient) -> int:
    tasklist = client.tasklists.create(args.title)
    if args.as_json:
        _print_json(asdict(tasklist))
    else:
        print(tasklist.id)
    return 0


def _cmd_list_update(args: argparse.Namespace, client: GoogleTasksClient) -> int:
    tasklist = client.tasklists.update(args.tasklist, args.title)
    if args.as_json:
        _print_json(asdict(tasklist))
    else:
        print(tasklist.id)
    return 0


def _cmd_list_delete(args: argparse.Namespace, client: GoogleTasksClient) -> int:
    client.tasklists.delete(args.tasklist)
    print(f"deleted {args.tasklist}")
    return 0


def _cmd_tasks(args: argparse.Namespace, client: GoogleTasksClient) -> int:
    tasks = list(
        client.tasks.iterate(
            args.tasklist, filter=_build_filter(args), show_completed=args.show_completed
        )
    )
    if not args.show_completed:
        tasks = [task for task in tasks if not task.is_completed]
    if args.as_json:
        _print_json([asdict(task) for task in tasks])
    else:
        _print_task_lines(tasks)
    return 0


def _cmd_show(args: argparse.Namespace, client: GoogleTasksClient) -> int:
    task = client.tasks.get(args.tasklist, args.task)
    if args.as_json:
        _print_json(asdict(task))
    else:
        _print_task_detail(task)
    return 0


def _cmd_create(args: argparse.Namespace, client: GoogleTasksClient) -> int:
    task = client.tasks.create(
        args.tasklist,
        title=args.title,
        notes=args.notes,
        due=args.due,
        parent=args.parent,
    )
    if args.as_json:
        _print_json(asdict(task))
    else:
        print(task.id)
    return 0


def _completion_timestamp(value: str) -> str:
    parsed = parse_rfc3339(value)
    if parsed is None and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        parsed = parse_rfc3339(f"{value}T00:00:00")
    if parsed is None:
        raise GtasksError(
            f"invalid completion timestamp (expected RFC 3339 or YYYY-MM-DD): {value}"
        )
    return to_rfc3339(parsed)


def _cmd_update(args: argparse.Namespace, client: GoogleTasksClient) -> int:
    changes: dict[str, Any] = {}
    if args.title is not UNSET:
        changes["title"] = args.title
    if args.notes is not UNSET:
        changes["notes"] = args.notes
    if args.clear_due:
        changes["due"] = None
    elif args.due is not UNSET:
        changes["due"] = args.due
    if args.status is not UNSET:
        changes["status"] = args.status
    if args.completed is not UNSET:
        if args.status is not UNSET and args.status != "completed":
            raise GtasksError("--completed requires --status completed")
        changes["completed"] = _completion_timestamp(args.completed)
        changes["status"] = "completed"
    task = client.tasks.update(args.tasklist, args.task, **changes)
    if args.as_json:
        _print_json(asdict(task))
    else:
        print(task.id)
    return 0


def _cmd_delete(args: argparse.Namespace, client: GoogleTasksClient) -> int:
    client.tasks.delete(args.tasklist, args.task)
    print(f"deleted {args.task}")
    return 0


def _cmd_clear(args: argparse.Namespace, client: GoogleTasksClient) -> int:
    client.tasks.clear(args.tasklist)
    print(f"cleared {args.tasklist}")
    return 0


def _cmd_move(args: argparse.Namespace, client: GoogleTasksClient) -> int:
    task = client.tasks.move(
        args.tasklist, args.task, parent=args.parent, previous=args.previous
    )
    if args.as_json:
        _print_json(asdict(task))
    else:
        print(task.id)
    return 0


def _cmd_subtasks(args: argparse.Namespace, client: GoogleTasksClient) -> int:
    tasks = client.tasks.subtasks(
        args.tasklist, args.parent, include_completed=args.include_completed
    )
    if args.as_json:
        _print_json([asdict(task) for task in tasks])
    else:
        _print_task_lines(tasks)
    return 0


def _sync_payload(report: SyncReport) -> dict[str, Any]:
    return {
        "synced_at": report.synced_at,
        "counts": report.counts(),
        "changes": [
            {
                "kind": change.kind,
                "action": change.action,
                "id": change.id,
                "tasklist_id": change.tasklist_id,
                "item": None if change.item is None else asdict(change.item),
            }
            for change in report.changes
        ],
    }


def _cmd_sync(args: argparse.Namespace, client: GoogleTasksClient) -> int:
    state_path = Path(
        args.state or os.environ.get(SYNC_STATE_ENV) or DEFAULT_SYNC_STATE_PATH
    )
    state = SyncState.load(state_path)
    report = SyncEngine(client.transport).pull(state, full=args.full)
    state.save(state_path)
    if args.as_json:
        _print_json(_sync_payload(report))
    else:
        counts = report.counts()
        print(
            f"created={counts['created']} "
            f"updated={counts['updated']} "
            f"deleted={counts['deleted']}"
        )
    return 0


def main(
    argv: Sequence[str] | None = None, *, client: GoogleTasksClient | None = None
) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "login":
            return _cmd_login(args, None)
        return args.handler(args, _resolve_client(args, client))
    except GtasksError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def run() -> None:
    raise SystemExit(main())
