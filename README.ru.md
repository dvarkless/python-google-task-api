# gtasks

[English](README.md) | **Русский**

Минимальный Python-клиент и утилита командной строки для
[Google Tasks REST API v1](https://developers.google.com/workspace/tasks/reference/rest),
использующие **только стандартную библиотеку**.

`gtasks` поддерживает списки задач, задачи и подзадачи, клиентскую фильтрацию, а
также одностороннюю инкрементальную синхронизацию (pull) данных Google Tasks в
локальный JSON-снимок. У проекта **нет зависимостей времени выполнения**, и он
поддерживает **Python 3.10+**.

## Возможности

- **Списки задач** — список, получение, создание, переименование, удаление.
- **Задачи** — список (с ленивой пагинацией), получение, создание, обновление,
  удаление, очистка, перемещение/изменение порядка, завершение и отмена
  завершения.
- **Подзадачи** — список дочерних задач, создание подзадачи, перемещение задач
  между уровнями.
- **Клиентская фильтрация** — по тексту, статусу, датам выполнения/завершения,
  родительской задаче и произвольным полям с точным совпадением (`TaskFilter`);
  условия объединяются по логике AND.
- **Инкрементальная односторонняя синхронизация (pull)** — Google Tasks →
  локальное JSON-состояние, с обработкой маркеров удаления (tombstones;
  `showDeleted` + `updatedMin`) и режимом восстановления полной синхронизацией.
- **Оба интерфейса** — типизированная библиотека Python и CLI `gtasks` с
  выводом, разделённым табуляцией, или `--json`.

## Требования

- Python **3.10 или новее**
- Только стандартная библиотека — никаких сторонних пакетов времени выполнения
- Проект Google Cloud с включённым Google Tasks API и OAuth-клиентом типа
  **Desktop app**

## Установка

```bash
git clone https://github.com/dvarkless/python-google-task-api.git
cd python-google-task-api

python3 -m venv .venv
source .venv/bin/activate

pip install -e .        # development install (editable)
# or
pip install .           # regular install
```

Устанавливаются консольный скрипт `gtasks` и пакет `gtasks`. Утилиту командной
строки также можно запускать как `python -m gtasks`.

```bash
gtasks --help
```

После выполнения `gtasks login --client-secret <file>` CLI работает из любого
каталога (конфигурация хранится в `~/.config/gtasks/`).

## Настройка OAuth в Google Cloud

1. Откройте [Google Cloud Console](https://console.cloud.google.com/) и создайте
   проект (или выберите существующий).
2. В разделе **APIs & Services → Library** найдите **Google Tasks API** и
   нажмите **Enable**.
3. В разделе **APIs & Services → OAuth consent screen** выберите **External**
   (или **Internal** для организации Google Workspace), заполните название
   приложения и адрес поддержки, а также добавьте свою учётную запись Google
   в **Test users**, пока приложение находится в статусе **Testing**.
4. В разделе **APIs & Services → Credentials → Create credentials → OAuth
   client ID** выберите тип приложения **Desktop app**, создайте клиента и
   скачайте JSON. Переименуйте файл в `client_secret.json` (или оставьте
   сгенерированное имя — оба варианта игнорируются git).
5. Один раз выполните вход. Клиенты типа Desktop app допускают loopback-редирект
   (`http://127.0.0.1:<random port>`), который использует `gtasks`;
   регистрировать redirect URI вручную не требуется.

```bash
gtasks login --client-secret ./client_secret.json
```

Команда `login` открывает браузер, ожидает страницу согласия Google, обменивает
код авторизации (PKCE S256 + state) и сохраняет токен. По завершении выводится
путь к файлу токена. После первого успешного входа конфигурация OAuth-клиента
копируется в `~/.config/gtasks/client_secret.json` (права `0600`), поэтому
последующие вызовы `gtasks` работают из любого рабочего каталога вне
репозитория; для первого входа по-прежнему нужен путь к скачанному JSON клиента.

> Если оставить приложение в статусе **Testing**, Google аннулирует выданные
> refresh-токены через 7 дней. Опубликуйте приложение (или используйте
> **Internal**-приложение в Workspace), если нужна долговременная авторизация.
> Отозвать доступ можно в любой момент на
> <https://myaccount.google.com/permissions>.

## Аутентификация

Порядок определения настроек:

| Настройка | 1-й способ | 2-й способ | 3-й способ | Резервный вариант |
|---|---|---|---|---|
| OAuth-клиент | `--client-secret PATH` | `GTASKS_CLIENT_SECRET` | `~/.config/gtasks/client_secret.json` (также `client_secret*.json`) | `./client_secret.json` |
| Токен | `--token PATH` | `GTASKS_TOKEN` | — | `~/.config/gtasks/token.json` |

Три равноценных способа аутентификации:

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

Токены хранятся в JSON-файле с правами `0600` и автоматически обновляются по
истечении срока действия.

## Быстрый старт библиотеки

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

Все временные метки при передаче по сети — строки RFC 3339; на входе принимаются
объекты `datetime`/`date`, а datetime без часового пояса трактуются как UTC.
Google хранит даты `due` задач без компонента времени.

## Фильтрация

Условия `TaskFilter` объединяются по логике **AND** и могут применяться лениво
через `client.tasks.iterate(..., filter=spec)` или к уже имеющемуся списку с
помощью `filter_tasks(tasks, spec)`.

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

Семантика фильтрации:

- **AND** — должны выполняться все заданные условия.
- **Строгие границы дат** — `*_before` использует `<`, `*_after` — `>`;
  задача без даты соответствующего типа никогда не удовлетворяет заданному
  условию по дате.
- **Псевдоним «active»** — `status="active"` означает
  `status == "needsAction"`. В остальных случаях статус сравнивается точно
  (`"needsAction"` / `"completed"`).
- **Точное совпадение полей** — `fields` сравнивает атрибуты `Task` на точное
  равенство; неизвестное имя атрибута вызывает `ValueError`.
- Границы фильтра `datetime`/`date` без часового пояса трактуются как UTC;
  некорректные строки RFC 3339 вызывают `ValueError`.

## Использование CLI

Полный список параметров: `gtasks <command> --help`. Глобальные параметры
`--client-secret` и `--token` должны указываться **до** подкоманды (подкоманда
`login` также принимает их после себя). Параметр `--json` доступен для `lists`,
`list-create`, `list-update`, `tasks`, `show`, `create`, `update`, `move`,
`subtasks` и `sync`.

### Списки

```bash
$ gtasks lists
MDEyMzQ1Njc4OTAxMjM0NTY3ODkw	My Tasks
MTU3Mzc4NTY3ODkwMTIzNDU2Nw	Work

# Exact, case-sensitive title lookup
$ gtasks lists --title "Work"
MTU3Mzc4NTY3ODkwMTIzNDU2Nw	Work

# Create a list (prints the new id)
$ gtasks list-create "Work"
MTU3Mzc4NTY3ODkwMTIzNDU2NzA

# Rename a list (prints its id)
$ gtasks list-update MTU3Mzc4NTY3ODkwMTIzNDU2NzA "Work 2026"
MTU3Mzc4NTY3ODkwMTIzNDU2NzA

# Delete a list
$ gtasks list-delete MTU3Mzc4NTY3ODkwMTIzNDU2NzA
deleted MTU3Mzc4NTY3ODkwMTIzNDU2NzA

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

`lists --title` сравнивает заголовок точно и с учётом регистра. `list-create`
и `list-update` также поддерживают `--json`.

### Задачи

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

Строки задач в человекочитаемом выводе имеют вид
`id<TAB>status<TAB>due-or-dash<TAB>title`.

### Просмотр

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

`gtasks show ... --json` выводит тот же объект в формате JSON.

### Создание / обновление / удаление / очистка / перемещение

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

# Mark complete with an explicit completion time (implies --status completed)
$ gtasks update @default MTU3Mzc4NTY3ODkwMTIzNDU2NzE --completed 2025-01-02
MTU3Mzc4NTY3ODkwMTIzNDU2NzE

# RFC 3339 completion times are normalized to UTC and preserved
$ gtasks update @default MTU3Mzc4NTY3ODkwMTIzNDU2NzE --completed 2025-01-02T10:30:00+03:00
MTU3Mzc4NTY3ODkwMTIzNDU2NzE

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

Параметры `--due` и `--completed` принимают значения `YYYY-MM-DD` или
RFC 3339; дата без времени нормализуется к полуночи UTC. `--completed`
подразумевает `--status completed` и сохраняет переданное время завершения;
указание его вместе с `--status`, отличным от `completed`, является ошибкой
(`--completed requires --status completed`).

### Подзадачи

```bash
$ gtasks subtasks @default MTU3Mzc4NTY3ODkwMTIzNDU2NzQ
MTU3Mzc4NTY3ODkwMTIzNDU2NzU	needsAction	-	Draft changelog
MTU3Mzc4NTY3ODkwMTIzNDU2NzY	completed	2026-11-14T00:00:00.000Z	Review changelog

$ gtasks subtasks @default MTU3Mzc4NTY3ODkwMTIzNDU2NzQ --no-completed
```

### Синхронизация

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

## Поведение и ограничения синхронизации

`SyncEngine.pull(state)` выполняет **одностороннюю синхронизацию (pull)** из
Google Tasks в локальный JSON-файл состояния. Она никогда не записывает данные
в Google Tasks.

```python
from gtasks import SyncEngine, SyncState

state = SyncState.load("sync-state.json")
report = SyncEngine(client.transport).pull(state)   # full=True for recovery
state.save("sync-state.json")

print(report.counts())      # {'created': N, 'updated': N, 'deleted': N}
for change in report.changes:
    print(change.kind, change.action, change.id, change.tasklist_id)
```

Принцип работы:

- Списки задач при каждой синхронизации загружаются целиком и сравниваются со
  снимком; отсутствующий на сервере список помечается как удалённый (для списков
  задач нет API дельта-изменений).
- **Первая синхронизация** загружает все задачи каждого списка и помечает их как
  `created`. Она запрашивает `showHidden=true`, чтобы скрытые/очищенные задачи не
  помечались позже как ложные `created`.
- **Инкрементальные синхронизации** запрашивают `showDeleted=true`,
  `showHidden=true` и `updatedMin=<last synced_at>`; маркеры удаления
  превращаются в изменения `deleted`, изменившиеся сырые словари задач — в
  `updated`, а новые идентификаторы — в `created`.
- **Полная синхронизация** (`sync --full`) дополнительно запрашивает
  `showDeleted=true`, включает скрытые задачи и помечает отсутствующие
  идентификаторы задач как удаления.
- Изменения упорядочены детерминированно: изменения списков задач — по `id`,
  затем изменения задач — по `(tasklist_id, id)`.
- Файл состояния записывается атомарно с правами `0600`.

Ограничения:

- **`tasks.clear()` скрывает выполненные задачи и может не обновлять их
  временную метку `updated`**, поэтому чисто инкрементальная синхронизация по
  `updatedMin` может пропустить очистку. Для восстановления используйте
  `gtasks sync --full` (или `pull(state, full=True)`).
- Срок хранения маркеров удаления (tombstones) для `showDeleted` не документирован
  Google. Запланируйте периодическую полную синхронизацию; не рассчитывайте на
  длинную историю удалений.
- Синхронизация работает только на получение (pull). Отправки, слияния и
  разрешения конфликтов нет: локальные изменения не отправляются в Google, а
  файл состояния — это снимок, а не локальное хранилище задач.
- Удаления списков задач определяются по отсутствию, поэтому временно невидимый
  список будет помечен как удалённый при следующей синхронизации.

## Ограничения API

- Списки задач: `maxResults` — значение по умолчанию и максимум **1000**.
- Задачи: `maxResults` — максимум **100** (клиент по умолчанию запрашивает 100
  на страницу).
- Примерно **50 000 запросов в день** на проект, а также ограничения ёмкости:
  2000 списков задач на пользователя, 20 000 нескрытых задач на список,
  100 000 задач суммарно, 2000 подзадач на задачу, 1024 символа в заголовке,
  8192 — в заметках.
- Транспорт повторяет запросы при HTTP 429 (для любого метода) и идемпотентных
  ответах 5xx — до двух раз, учитывая `Retry-After` (ограничение 5 секунд).
- Кратковременные сетевые сбои — сброс соединения/`RemoteDisconnected`,
  тайм-ауты и `URLError` — также повторяются до двух раз для идемпотентных
  методов (GET/HEAD/PATCH/PUT/DELETE); POST никогда не повторяется при сетевой
  ошибке.

Полезные замечания по API:

- Если в `move` не указать `previous`, задача ставится **первой** среди соседей;
  если не указать `parent`, она перемещается на верхний уровень.
- `uncomplete` устанавливает `status="needsAction"` **и очищает `completed`** —
  один только статус оставил бы устаревшую метку завершения.
- При завершении задачи в клиентах Google она может помечаться как скрытая; при
  полной синхронизации запрашиваются и скрытые задачи, чтобы избежать дубликатов.
- В Google Tasks нет состояния *cancelled*: чтобы обозначить отмену, удалите
  задачу или закодируйте её в `notes`/`title` (соглашение на стороне
  вызывающего кода).
- Массового (bulk) эндпоинта нет: каждая операция с задачей или списком — это
  один HTTP-запрос, а синхронизация работает только на получение (pull), без
  push, upsert и разрешения конфликтов.

## Безопасность

- **Никогда не коммитьте** `client_secret*.json`, `token*.json`, `*.token`,
  `.env` и каталог `secret/`. `.gitignore` исключает их — сохраняйте это правило.
- Токены записываются с правами `0600`, но остаются bearer-учётными данными —
  относитесь к файлу токена как к секрету.
- Если секрет или токен мог утечь, отзовите доступ на
  <https://myaccount.google.com/permissions>, затем удалите файл токена и
  войдите заново.

## Лицензия

[MIT](LICENSE) © 2026 Amir Suleymanov
