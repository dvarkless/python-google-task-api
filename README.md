# gtasks

**English** | [Русский](README.ru.md)

A minimal, **stdlib-only** Python client and command line tool for the
[Google Tasks REST API v1](https://developers.google.com/workspace/tasks/reference/rest).

`gtasks` covers task lists, tasks and subtasks, client-side filtering, and a
one-way incremental pull sync of your Google Tasks data into a local JSON
snapshot. It has **zero runtime dependencies** and supports **Python 3.10+**.

## Features

- **Task lists** — list, get, create, rename, delete.
- **Tasks** — list (with lazy pagination), get, create, update, delete, clear,
  move/reorder, complete, uncomplete.
- **Subtasks** — list the children of a task, create a subtask, move tasks
  between levels.
- **Client-side filtering** — by text, status, due/completed dates, parent and
  arbitrary exact-match fields (`TaskFilter`), combined with AND semantics.
- **Incremental one-way pull sync** — Google Tasks → local JSON state, with
  tombstones (`showDeleted` + `updatedMin`) and a full-pull recovery mode.
- **Both interfaces** — a typed Python library and the `gtasks` CLI with
  tab-separated human output or `--json`.

## Requirements

- Python **3.10 or newer**
- Standard library only — no third-party runtime packages
- A Google Cloud project with the Google Tasks API enabled and an OAuth
  **Desktop app** client

## Install

```bash
git clone https://github.com/dvarkless/python-google-task-api.git
cd python-google-task-api

python3 -m venv .venv
source .venv/bin/activate

pip install -e .        # development install (editable)
# or
pip install .           # regular install
```

This installs the `gtasks` console script and the `gtasks` package. The command
line tool is also runnable as `python -m gtasks`.

```bash
gtasks --help
```

After `gtasks login --client-secret <file>`, the CLI works from any directory
(config stored in `~/.config/gtasks/`).

## Google Cloud OAuth setup

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and
   create (or select) a project.
2. **APIs & Services → Library**, search for **Google Tasks API**, and click
   **Enable**.
3. **APIs & Services → OAuth consent screen**: choose **External** (or
   **Internal** for a Google Workspace organization), fill in the app name and
   support email, and add your Google account under **Test users** while the app
   is in **Testing** status.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**,
   choose application type **Desktop app**, then create and download the JSON.
   Rename it to `client_secret.json` (or keep the generated name — both are
   ignored by git).
5. Run the login flow once. Desktop clients allow the loopback redirect
   (`http://127.0.0.1:<random port>`) that `gtasks` uses; no redirect URI needs
   to be registered manually.

```bash
gtasks login --client-secret ./client_secret.json
```

`login` opens your browser, waits for the Google consent page, exchanges the
authorization code (PKCE S256 + state), and saves the token. It prints the token
path when finished. After the first successful login the client configuration is
copied to `~/.config/gtasks/client_secret.json` (mode `0600`), so later `gtasks`
calls work from any working directory without the repository; the first login
still needs the path to the downloaded client-secret JSON.

> Apps left in **Testing** status issue refresh tokens that Google expires after
> 7 days. Publish the app (or use an **Internal** app in a Workspace) if you want
> long-lived authorization. Revoke access at any time at
> <https://myaccount.google.com/permissions>.

## Authentication

Configuration resolves in this order:

| Setting | 1st | 2nd | 3rd | Fallback |
|---|---|---|---|---|
| OAuth client | `--client-secret PATH` | `GTASKS_CLIENT_SECRET` | `~/.config/gtasks/client_secret.json` (also `client_secret*.json`) | `./client_secret.json` |
| Token | `--token PATH` | `GTASKS_TOKEN` | — | `~/.config/gtasks/token.json` |

Three equivalent ways to authenticate:

```bash
# 1. CLI flags (global flags go before the subcommand; login also accepts them after)
gtasks --client-secret ./client_secret.json --token ~/.config/gtasks/token.json lists
gtasks login --client-secret ./client_secret.json --token ~/.config/gtasks/token.json

# 2. Environment variables
export GTASKS_CLIENT_SECRET="$HOME/.config/gtasks/client_secret.json"
export GTASKS_TOKEN="$HOME/.config/gtasks/token.json"
gtasks login

# 3. Default paths
#    ~/.config/gtasks/client_secret.json  ->  ~/.config/gtasks/token.json
gtasks login
```

Tokens are stored as JSON with `0600` permissions and refreshed
automatically when they expire.

## Library quickstart

```python
from datetime import date

from gtasks import GoogleTasksClient, TaskFilter, filter_tasks

# Uses GTASKS_CLIENT_SECRET / GTASKS_TOKEN / default paths.
client = GoogleTasksClient.from_client_secret("client_secret.json")
# or an explicit token path:
# client = GoogleTasksClient.from_client_secret("client_secret.json", "~/.config/gtasks/token.json")
# or fully environment/default based:
# client = GoogleTasksClient.from_env()

# --- Task lists ------------------------------------------------------------
for tasklist in client.tasklists.iterate():          # lazily follows pages
    print(tasklist.id, tasklist.title)

work = client.tasklists.create("Work")
client.tasklists.update(work.id, "Work 2026")

# --- Tasks -----------------------------------------------------------------
created = client.tasks.create(
    work.id,
    title="Write release notes",
    notes="Include CLI examples",
    due=date(2026, 11, 20),
)

page = client.tasks.list(work.id)                    # TaskPage(items=[...])
task = client.tasks.get(work.id, created.id)

client.tasks.update(work.id, created.id, title="Write v0.1.0 release notes")
client.tasks.update(work.id, created.id, due=None)   # explicit None clears the field

client.tasks.complete(work.id, created.id)           # status = completed
client.tasks.uncomplete(work.id, created.id)         # needsAction + clears completed

for item in client.tasks.iterate(work.id, filter=TaskFilter(status="active")):
    print(item.id, item.title)

client.tasks.move(work.id, created.id)               # top level, first position
client.tasks.clear(work.id)                          # hide completed tasks
client.tasks.delete(work.id, created.id)

# --- Subtasks --------------------------------------------------------------
parent = client.tasks.create(work.id, title="Ship v0.1.0")
child = client.tasks.create_subtask(work.id, parent.id, title="Draft changelog")
children = client.tasks.subtasks(work.id, parent.id, include_completed=False)

# --- Cleanup ---------------------------------------------------------------
client.tasklists.delete(work.id)
```

All timestamps are RFC 3339 strings on the wire; `datetime`/`date` objects are
accepted on input and naive datetimes are treated as UTC. Google stores task
`due` dates without a time component.

## Filtering

`TaskFilter` conditions are combined with **AND** semantics and can be applied
lazily through `client.tasks.iterate(..., filter=spec)` or to an existing list
with `filter_tasks(tasks, spec)`.

```python
from gtasks import TaskFilter, filter_tasks

spec = TaskFilter(
    text="release",              # case-insensitive substring of title or notes
    status="active",             # "active" is an alias for "needsAction"; or "completed"
    due_before="2026-12-01",     # due < 2026-12-01T00:00:00Z (strict)
    due_after="2026-11-01",      # due > 2026-11-01T00:00:00Z (strict)
    # completed_before=...,      # completed < bound (strict)
    # completed_after=...,       # completed > bound (strict)
    # parent="<task id>",        # exact parent task id
    top_level=True,              # True -> only top-level tasks; False -> only subtasks
    fields={"notes": None},      # exact equality on any Task attribute
)

matching = filter_tasks(client.tasks.iterate("@default"), spec)

# Convenience: children of a task, without completed ones
children = client.tasks.subtasks("@default", parent_id, include_completed=False)
```

Filter semantics:

- **AND** — every specified condition must hold.
- **Strict date bounds** — `*_before` uses `<`, `*_after` uses `>`; a task with
  no date of the relevant kind never matches a set date condition.
- **"active" alias** — `status="active"` means `status == "needsAction"`.
  Otherwise status is an exact match (`"needsAction"` / `"completed"`).
- **Fields exact-match** — `fields` compares `Task` attributes for exact
  equality; an unknown attribute name raises `ValueError`.
- Naive `datetime`/`date` filter bounds are interpreted as UTC; malformed RFC
  3339 strings raise `ValueError`.

## CLI usage

Run `gtasks <command> --help` for the full option list. Global `--client-secret`
and `--token` options must appear **before** the subcommand (the `login`
subcommand also accepts them itself). `--json` is available on `lists`, `tasks`,
`show`, `create`, `update`, `move`, `subtasks` and `sync`.

### Lists

```bash
$ gtasks lists
MDEyMzQ1Njc4OTAxMjM0NTY3ODkw	My Tasks
MTU3Mzc4NTY3ODkwMTIzNDU2Nw	Work

$ gtasks lists --json
[
  {
    "id": "MDEyMzQ1Njc4OTAxMjM0NTY3ODkw",
    "title": "My Tasks",
    "updated": "2026-11-15T18:31:02.000Z",
    "etag": "\"MTcyNTg4NDI2Mg\"",
    "self_link": "https://www.googleapis.com/tasks/v1/users/@me/lists/MDEyMzQ1Njc4OTAxMjM0NTY3ODkw"
  }
]
```

### Tasks

```bash
$ gtasks tasks @default
MTU3Mzc4NTY3ODkwMTIzNDU2NzE	needsAction	2026-11-20T00:00:00.000Z	Write release notes
MTU3Mzc4NTY3ODkwMTIzNDU2NzI	completed	-	Ship v0.1.0
MTU3Mzc4NTY3ODkwMTIzNDU2NzM	needsAction	-	Plan sprint

# Exclude completed tasks (they are included by default)
$ gtasks tasks @default --no-completed

# Filtering: text + status + strict due bound, combined with AND
$ gtasks tasks @default --no-completed --text release --status active --due-before 2026-12-01

# Only top-level tasks
$ gtasks tasks @default --top-level

# Only direct children of a task
$ gtasks tasks @default --parent MTU3Mzc4NTY3ODkwMTIzNDU2NzE

$ gtasks tasks @default --json
```

Human task lines are `id<TAB>status<TAB>due-or-dash<TAB>title`.

### Show

```bash
$ gtasks show @default MTU3Mzc4NTY3ODkwMTIzNDU2NzE
id: MTU3Mzc4NTY3ODkwMTIzNDU2NzE
title: Write release notes
notes: Draft the 0.1.0 release notes
status: needsAction
due: 2026-11-20T00:00:00.000Z
completed: -
parent: -
position: 00000000000000000000
deleted: False
hidden: False
links: []
updated: 2026-11-15T18:31:03.000Z
etag: "..."
self_link: https://www.googleapis.com/tasks/v1/lists/@default/tasks/MTU3Mzc4NTY3ODkwMTIzNDU2NzE
```

`gtasks show ... --json` prints the same object as JSON.

### Create / update / delete / clear / move

```bash
# Create (prints the new task id)
$ gtasks create @default --title "Write release notes" --due 2026-11-20 \
      --notes "Draft the 0.1.0 release notes"
MTU3Mzc4NTY3ODkwMTIzNDU2NzE

# Create a subtask
$ gtasks create @default --title "Draft changelog" --parent MTU3Mzc4NTY3ODkwMTIzNDU2NzQ
MTU3Mzc4NTY3ODkwMTIzNDU2NzU

# Update selected fields; unspecified fields are left untouched
$ gtasks update @default MTU3Mzc4NTY3ODkwMTIzNDU2NzE \
      --title "Write v0.1.0 release notes" --clear-due
MTU3Mzc4NTY3ODkwMTIzNDU2NzE

# Mark complete / incomplete
$ gtasks update @default MTU3Mzc4NTY3ODkwMTIzNDU2NzE --status completed

$ gtasks delete @default MTU3Mzc4NTY3ODkwMTIzNDU2NzE
deleted MTU3Mzc4NTY3ODkwMTIzNDU2NzE

# Hide every completed task in the list (never deletes data)
$ gtasks clear @default
cleared @default

# Move to top level, first among siblings
$ gtasks move @default MTU3Mzc4NTY3ODkwMTIzNDU2NzE
MTU3Mzc4NTY3ODkwMTIzNDU2NzE

# Nest under a parent, after a specific sibling
$ gtasks move @default MTU3Mzc4NTY3ODkwMTIzNDU2NzE \
      --parent MTU3Mzc4NTY3ODkwMTIzNDU2NzQ --previous MTU3Mzc4NTY3ODkwMTIzNDU2NzU
MTU3Mzc4NTY3ODkwMTIzNDU2NzE
```

### Subtasks

```bash
$ gtasks subtasks @default MTU3Mzc4NTY3ODkwMTIzNDU2NzQ
MTU3Mzc4NTY3ODkwMTIzNDU2NzU	needsAction	-	Draft changelog
MTU3Mzc4NTY3ODkwMTIzNDU2NzY	completed	2026-11-14T00:00:00.000Z	Review changelog

$ gtasks subtasks @default MTU3Mzc4NTY3ODkwMTIzNDU2NzQ --no-completed
```

### Sync

```bash
# Incremental pull; state defaults to $GTASKS_SYNC_STATE or ~/.cache/gtasks/sync.json
$ gtasks sync
created=2 updated=1 deleted=0

# Explicit state file
$ gtasks sync --state ./sync-state.json

# Recovery / first-time full pull (also reveals hidden and cleared tasks)
$ gtasks sync --full

# Machine-readable report
$ gtasks sync --json
```

## Sync behavior and limitations

`SyncEngine.pull(state)` performs a **one-way pull** from Google Tasks into a
local JSON state file. It never writes to Google Tasks.

```python
from gtasks import SyncEngine, SyncState

state = SyncState.load("sync-state.json")
report = SyncEngine(client.transport).pull(state)   # full=True for recovery
state.save("sync-state.json")

print(report.counts())      # {'created': N, 'updated': N, 'deleted': N}
for change in report.changes:
    print(change.kind, change.action, change.id, change.tasklist_id)
```

How it works:

- Task lists are fetched in full every pull and diffed against the snapshot;
  a list missing remotely is reported as deleted (there is no delta API for task
  lists).
- The **first pull** fetches all tasks per list and reports them as `created`.
  It requests `showHidden=true` so cleared/hidden tasks are not later reported as
  false creates.
- **Incremental pulls** request `showDeleted=true`, `showHidden=true` and
  `updatedMin=<last synced_at>`; tombstones become `deleted` changes, changed
  raw task dicts become `updated`, and unseen ids become `created`.
- A **full pull** (`sync --full`) additionally requests `showDeleted=true`,
  includes hidden tasks, and diffs missing task ids as deletions.
- Changes are ordered deterministically: task-list changes by id, then task
  changes by `(tasklist_id, id)`.
- The state file is written atomically with `0600` permissions.

Caveats:

- **`tasks.clear()` hides completed tasks and may not bump their `updated`
  timestamp**, so a purely incremental `updatedMin` pull can miss a clear. Use
  `gtasks sync --full` (or `pull(state, full=True)`) as the recovery path.
- Tombstone retention for `showDeleted` is not documented by Google. Schedule a
  periodic full pull; do not assume a long history of deletions is available.
- This is pull-only. There is no push, merge or conflict resolution: local
  edits are not sent to Google, and the state file is a snapshot, not a local
  task store.
- Task list deletions are detected by absence, so a temporarily invisible list
  is reported as deleted on the next pull.

## API limits

- Task lists: `maxResults` default and maximum **1000**.
- Tasks: `maxResults` maximum **100** (the client defaults to 100 per page).
- Roughly **50,000 queries/day** per project, plus capacity limits: 2,000 task
  lists per user, 20,000 non-hidden tasks per list, 100,000 tasks total, 2,000
  subtasks per task, 1,024 characters per title, 8,192 per notes.
- The transport retries HTTP 429 (any method) and idempotent 5xx responses up
  to two times, honouring `Retry-After` (capped at 5 seconds).
- Transient network-level failures — connection resets/`RemoteDisconnected`,
  timeouts and `URLError` — are also retried up to two times for idempotent methods
  (GET/HEAD/PATCH/PUT/DELETE); POST is never retried on a network error.

API notes worth knowing:

- Omitting `previous` in `move` puts the task **first** among its siblings;
  omitting `parent` moves it to the top level.
- `uncomplete` sets `status="needsAction"` **and clears `completed`** — status
  alone would leave a stale completion timestamp.
- Completing a task in first-party clients may mark it hidden; full sync
  pulls request hidden tasks to avoid duplicates.

## Security

- **Never commit** `client_secret*.json`, `token*.json`, `*.token`, `.env`, or
  the `secret/` directory. `.gitignore` excludes them; keep it that way.
- Tokens are written with `0600` permissions, but they are still bearer
  credentials — treat the token file as a secret.
- Revoke access at <https://myaccount.google.com/permissions> if a secret or
  token may have leaked, then delete the token file and log in again.

## License

[MIT](LICENSE) © 2026 Amir Suleymanov
