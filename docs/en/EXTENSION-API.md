# The Extension Interface

> The Nodvard Deck interface is currently available in German only; an English interface is planned. UI labels are given in English with the German original in quotes.

The core does not know about any particular extension. It knows **capabilities**. An extension declares which
of them it fulfills, and the core uses them generically.

This document is the specification. The matching, importable contract lives in
`sdk/python/nodvard_sdk/`.

---

## 1. Anatomy of an extension

```
extensions/nexus-soc/
├─ extension.toml            ← manifest, readable without importing code
├─ pyproject.toml            ← optional; for pip-installable extensions
├─ src/nodvard_deck_ext_nexus_soc/
│  ├─ __init__.py            ← contains  class Extension(NodvardExtension)
│  ├─ api.py                 ← APIRouter
│  ├─ connectors.py          ← Ollama and ntfy connector types
│  ├─ capabilities.py        ← ActionExecutor, AIProvider …
│  ├─ widgets.py             ← declarative WidgetSpecs
│  ├─ pipeline.py            ← incident queue/batching
│  ├─ models.py              ← own tables (prefix ext_nexus_soc_)
│  └─ migrations/            ← own Alembic branch
└─ frontend/
   ├─ package.json
   └─ src/index.tsx          ← registerPage/registerWidget, ESM bundle
```

### Manifest (`extension.toml`)

```toml
[extension]
id          = "nexus-soc"          # stable, lowercase, namespace for everything
name        = "Nodvard Shield"
version     = "0.1.0"
api_version = "1.0"                # SemVer against nodvard_sdk.API_VERSION
author      = "…"
description = "Autonomous diagnosis and remediation for your own fleet."
icon        = "shield-check"
entrypoint  = "nodvard_deck_ext_nexus_soc:Extension"
frontend    = "frontend/dist/index.js"    # optional
requires    = ["terminal>=0.1"]           # other extensions, optional
enable_on   = ["host_credential"]         # optional: see below
category    = "servers"                   # optional: group in the module selection of the setup wizard
sort_order  = 10                          # optional: order within the group (default 100)

# What the extension asks of the core. The admin confirms this at installation.
# The core enforces it on EVERY ctx call.
permissions = [
  "hosts.read",
  "hosts.execute",          # run commands on hosts (only through the gate)
  "secrets.read:ssh-fleet",
  "audit.write",
  "notify.send",
  "schedule.register",
  "net.outbound:192.168.1.0/24",
]

[settings]                  # JSON Schema; the core renders the settings page from it
schema = "settings.schema.json"
```

**Package name (`entrypoint`):** The Python packages of the bundled extensions are called
`nodvard_deck_ext_<id>` (hyphens in the ID become `_`, so `nodvard_deck_ext_nexus_soc`) and live
under `extensions/<id>/src/`. This is the convention for new extensions; the core does not enforce
it. It imports exactly the module named in `entrypoint` and reads the manifest fresh from disk on
every start. **Third-party extensions that follow the old convention `lattice_ext_<id>` (or use any
other package name) therefore keep loading unchanged**; they do not have to be renamed. The bundled
extensions are no longer available under the old name (not even as an alias): extensions do not
import each other anyway (other extensions are reached only through `ctx.events` and `requires` +
`ctx.capabilities.query()`, see "Deliberately not available on the context" further down). If you
ever imported a module of a bundled extension directly, use the new name.

**SDK dependency (pip-installable extensions):** The SDK distribution is now called
`nodvard-sdk` (import name `nodvard_sdk`). The old import name `lattice_sdk` lives in the same
distribution and keeps working, but a distribution `lattice-sdk` no longer exists. If your
`pyproject.toml` lists `lattice-sdk` as a dependency, switch it to `nodvard-sdk` (or drop it: the
core brings the SDK along anyway).

**`category` and `sort_order` (optional):** The setup wizard shows the installed modules as tiles,
grouped by `category` (known: `servers`, `security`, `tools`, `connections`, `example`; anything
else, and a missing value, ends up under "More modules" („Weitere Module“)) and, within the group,
by `sort_order` (smaller = further up; on a tie, by name). The values are also in `GET /extensions`. Modules with
`example` (examples for developers such as hello-world) are not shown in the wizard; they only appear under
Settings → Extensions („Einstellungen → Erweiterungen“).

**`enable_on` (optional):** Occasions on which the core switches the extension on by itself, so that
beginners do not first have to look for it in the settings. Known is `host_credential` (a server has
received an SSH login; this is how Terminal switches itself on). The core does not know any
extension by name for this; it only reads this marker. The extension is switched on **only if nobody
has ever switched it on or off on purpose** – a user who switched it off is left alone. The audit log
records `extension.auto_enabled`. On installations that already existed before this feature,
extensions that were already created stay untouched; only newly added ones count as "untouched".

**Why the manifest is a file and not a Python constant:** The core must be able to read
the manifests of all extensions — including disabled and broken ones — without importing
their code. An import is code execution; a broken or switched-off extension must not run
just because it is shown in the registry.

### Settings schema: `settings.schema.json` and the `x-*` additions

From the JSON Schema, the core builds the extension's settings page (Settings → Extensions –
„Einstellungen → Erweiterungen“). Supported are `type` (`string`, `integer`, `number`, `boolean`,
`array`, `object`), `title`, `description`, `default`, `enum`, `required`, `properties`, `items`
and `pattern`. In addition, these additions (all optional; unknown ones are ignored):

| Addition | Where | Effect |
|---|---|---|
| `x-advanced` | field | Appears under "Advanced" („Erweitert“), collapsed. |
| `x-hidden` | field | Is not displayed (internal state). At the top level, `PUT /extensions/{id}/settings` ignores the field; it belongs to the extension's own routes (`ctx.settings.set()`). |
| `x-item-title` | list of objects | What an entry is called ("Add server" – „Server hinzufügen“). |
| `x-enum-labels` | field with `enum` | Readable texts per value: `{"all": "Alle Updates"}` ("All updates"). |
| `x-widget: "schedule"` | text (cron) | Schedule picker instead of a cron field. |
| `x-widget: "host"` | text or list of texts | Choice from the servers (value = server name, from `GET /hosts`). List: chips with "Add server" („Server hinzufügen“). Without a server list: a text field or word list. |
| `x-widget: "host-tag"` | text or list of texts | Like `host`, but a choice from the servers' tags, with a count. A stored tag that no longer exists stays visible. |
| `x-widget: "remote-select"` | text | Selection list that the extension supplies itself (e.g. the models of an AI server). Needs `x-options-url`. If the query fails or the list is empty, a text field and the reason appear. |
| `x-options-url` | `remote-select` | Path under `/api/v1`, e.g. `/ext/nexus-soc/ai/models?which=primary`. The interface sends no form values along; the extension only asks for what is stored (no address parameter – otherwise the route would be a way to send requests to arbitrary addresses, including the key). After entering a new address: save, then "Reload" („Neu laden“). Response: `{"options": [{"value": "…", "label": "…"}], "error": null}`; on problems `options: []` and `error` as a German sentence (no error status). |
| `x-empty-label` | selection | Text for "nothing selected" (default "– nicht gesetzt –", i.e. "– not set –"), e.g. "alle Server" ("all servers"). |
| `pattern` | text, words of a list | Regular expression (as in JSON Schema, not anchored). The interface shows the message at the field and disables "Save"; the backend checks the same (`422`). Empty values are always allowed. |
| `x-pattern-message` | next to `pattern` | German message when the pattern does not match (default: "Das Format stimmt nicht.", i.e. "The format is not correct."). |
| `x-test-message` | top level | `true` if the extension provides a `NotificationChannel`: the connection card shows "Send test message" („Testnachricht senden“). |
| `x-secrets` | top level | List of the secrets (vault labels): `{"label": "proxmox-token:{name}", "title": "…", "description": "…", "per_item": "connections", "optional": true}`. `per_item` creates one secret per entry of the named list (via its `name`); if an entry disappears or is renamed when saving via `PUT /extensions/{id}/settings`, the core deletes its secret, and a newly created entry starts without one (an old secret under the same name is deleted). `optional: true`: the extension also works without it; if a non-optional secret or a `required` field is missing, `GET /extensions` reports `needs_setup`. |
| `x-secret-bound-to` | entry in `x-secrets` | Fields the secret is bound to: with `per_item`, field names of the entry (`["base_url"]`), otherwise paths like `pihole.url`. If one of them changes via `PUT /extensions/{id}/settings`, the core deletes the secret (response `secrets_cleared`, audit log `detail.secrets_cleared`), also for the first address (empty → value); without a stored value, the field's `default` counts. Other spellings of the same address are not a change: upper/lower case of scheme and host name, default port (`:80` for http, `:443` for https), trailing `/`, fragment (`#…`), missing scheme (= `http://`). A different path or query (`?…`) counts as a change. `PUT /extensions/{id}/secrets` only accepts the secret once one of the fields has a stored value or `default`, otherwise `409` („Erst die Adresse eintragen und die Einstellungen speichern, danach die Zugangsdaten hinterlegen.“, i.e. "First enter the address and save the settings, then store the credentials."). |

**Own routes for connections.** If an extension saves settings itself (`ctx.settings.set()`, e.g. via
its own `/connections` routes), the core deletes no secrets. The extension then calls
`ctx.secrets.delete(label)` itself when a connection is created or removed or its address changes.
For the comparison there is `nodvard_sdk.same_target(old, new)` (`True` = same target, by the same
rules as for `x-secret-bound-to`; the interface computes the same in
`frontend/src/lib/targetAddress.ts`). Proxmox VE and Backups do it this way.

**Testing the connection.** Every extension with settings gets the button "Test connection"
(„Verbindung testen“) in the interface (`POST /extensions/{id}/test`). For this, the core calls the
extension's `health()` (and `test()` of its `NotificationChannel`, if it provides one) and
translates technical errors into understandable German sentences (no response, credentials
rejected, certificate, address not found …). For good messages it is enough to leave the HTTP
client's error text in `HealthReport.message`; with several connections, `HealthReport.details`
returns `{"name": {"healthy": bool, "error": "…"}}` per connection – this becomes the list "Result
per connection" („Ergebnis je Verbindung“). Secrets never belong in `message` or `details`; the
core blacks out known values anyway.

### Entry class

```python
from nodvard_sdk import NodvardExtension, ExtensionContext

class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        """Register only. No I/O, no network access, no DB writes."""

    async def on_start(self, ctx: ExtensionContext) -> None:
        """Start background tasks. Use ctx.spawn() instead of asyncio.create_task()."""

    async def on_stop(self, ctx: ExtensionContext) -> None:
        """Clean up. Tasks started via ctx.spawn are stopped automatically."""

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        """Optional. Feeds the registry UI and the notification center."""

    async def on_settings_changed(self, ctx: ExtensionContext, values: dict) -> None:
        """Optional. After every ctx.settings.set() -- after saving, get() already returns
        the new values. A set() in here does not trigger the hook again; an exception is
        logged and does not undo the save."""
```

The split into `setup` / `on_start` is not cosmetic: it lets the core collect **all**
registrations first (and thereby build routers, navigation and the widget catalog in one
go) before any background task runs and pushes events onto a bus that is still half
built.

---

## 2. The `ExtensionContext`

The only object an extension receives from the core. Everything on it is
permission-checked and writes to the audit log where needed.

| Handle | Purpose | Permission |
|---|---|---|
| `ctx.api` | `include_router(r, permission=…, public=…)` → `/api/v1/ext/<id>/…`, without further arguments only with a login (see "Routes and login" below). If a route does not catch `HostUnreachable` or an SSH error itself, the core answers `502` with the reason in `detail` (not `500`, no traceback in the container log) | —; `public=True`: `api.public` |
| `ctx.ui` | `register_page()`, `register_widget()`, `register_nav()` | — |
| `ctx.connectors` | `register_type(ConnectorType)` | — |
| `ctx.capabilities` | `provide(protocol_instance)` | per protocol |
| `ctx.actions` | `register(ActionSpec)`, `propose(ActionRequest)` (optionally with `standing_approval`, see section 3), `check_standing_approval(granted_by_user_id, risk=…)` (would a standing approval by this person still apply today? `None` = yes, otherwise the reason) | `hosts.execute` and others; standing approval: `actions.standing_approval` |
| `ctx.hosts` | `list()`, `get()`, `upsert_discovered()` | `hosts.read` / `hosts.write` |
| `ctx.exec` | `run(host, command)`, `stream(host, command)` (live output, e.g. live logs), `open_shell(host)`, `sftp(host)` | `hosts.execute` |
| `ctx.secrets` | `get_handle(label)`, `create()`, `delete(label)` (deletes the secret; `True` if there was one, otherwise `False`; audit log `extension.secret_removed`; older cores do not have it) | `secrets.read:<label>` |
| `ctx.vault_use` | context manager that materializes a value in memory only | same |
| `ctx.db` | AsyncSession factory, **only** on `ext_<id>_*` tables | — |
| `ctx.settings` | `declare(schema)`, `get()`, `set()` | — |
| `ctx.scheduler` | `register_job(JobSpec)` | `schedule.register` |
| `ctx.events` | `publish(Event)`, `subscribe(pattern, handler)` | — |
| `ctx.notify` | `send(Notification, raise_on_failure=False)` → `NotifyResult` (`notification_id`, `suppressed`, `delivered`) – with `raise_on_failure=True` it raises `NotificationNotDelivered` if there were channels and none delivered (for "try again"); `would_suppress(host_id=…, host_ids=…)` only asks whether a maintenance window would currently silence a notification (see below) | `notify.send` |
| `ctx.audit` | `log(entry)` | `audit.write` |
| `ctx.http` | preconfigured `httpx.AsyncClient` with target checking, plus `websocket(url, …)`. The target check only covers the start address: `websocket()` never follows redirects (3xx) during the handshake (`async with` then raises a `ConnectionError` with a German message, no second connection is made); `get()`, `post()`, `request()` and `stream()` do not follow them by default either | `net.outbound:<cidr>` |
| `ctx.ws` | `broadcast(channel, payload)` on `ext.<id>.*` | — |
| `ctx.spawn` | supervised background task with restart policy | — |
| `ctx.logger` | structured logger, pre-tagged with the extension ID | — |
| `ctx.data_dir` | the extension's own writable folder (in the image `/app/data/ext/<id>/`). Files belong here: the code in the image belongs to root and is read-only, including the extension's own package folder. New files are readable only by the user `lattice` | — |

Deliberately not available on the context: direct access to core tables, the `asyncio`
loop object, other extensions (only via `ctx.events` and declared `requires` +
`ctx.capabilities.query()`).

### Routes and login

`ctx.api.include_router(router, prefix=…, permission=…, public=…)` mounts a FastAPI router under
`/api/v1/ext/<id><prefix>`. The check applies to **all** routes of that router:

| Call | Who gets through |
|---|---|
| `include_router(r)` | any logged-in account, no particular permission needed (`401` without a token) |
| `include_router(r, permission="hosts.read")` | logged in **and** holding this permission (`401` without a token, `403` without the permission) |
| `include_router(r, public=True)` | anyone, even without logging in; needs `api.public` in the manifest |

**A login is not a permission.** Without `permission=`, any logged-in account gets through, whatever
its role. Routes that show data or change something therefore set `permission=` or check for
themselves. A route without `permission=` used to have no check at all; if you deliberately offer an
address without login (e.g. for a webhook), switch to `public=True`.

**`public=True`** only works with the permission `api.public` in the manifest; otherwise the call
raises `PermissionDenied` and the extension ends up in the error state. Combined with `permission=`
it is an error as well (`InvalidRegistration`). When the extension is enabled, the public addresses
(`/api/v1/ext/<id><prefix>`) go into the audit log, in `detail.public_routes` of `extension.enabled`
or `extension.auto_enabled`; a warning also appears in the log.

**Limits of the check.** It only works for normal FastAPI routes (`@router.get` etc.). WebSocket
routes, `router.add_route()` and `router.mount()` (e.g. `StaticFiles`) are rejected by the core
without `public=True` (`InvalidRegistration`), and the extension cannot be enabled. With
`public=True` the extension secures them itself. The check runs after `setup()` and after
`on_start()`; if `on_start()` adds such routes, the core takes the extension out again. Anything
attached to an already mounted router later at runtime (for example from a background task) is no
longer seen by the core: such routes must secure themselves.

**Request size.** The core accepts at most 1 MiB on every route (`NODVARD_DECK_MAX_BODY_BYTES`) and answers anything
larger with `413` ([API, section 1](../04-API.md#1-konventionen), in German). A route that deliberately accepts larger
uploads declares this with `nodvard_sdk.max_body_bytes(limit)`: `@router.post(...)` outermost, `@max_body_bytes(...)`
directly above the function. `limit` is an upper bound in bytes, or a function without arguments that returns it on
every request (for configurable limits). The core checks the `Content-Length` and counts along for requests without a
length. The declaration only raises the limit; it cannot lower it below the general limit. The route should still read
its data as a stream or keep its own checks. Among the bundled extensions, `documents` (20 MB per document) and
`inventory` (5 MB per image) use it.

Pages in the frontend bundle send the token along for these routes (`Authorization: Bearer …` with
the token from `window.__nodvardDeck.getAccessToken()`, like `HelloPage.tsx` in hello-world; the
other bundled extensions use `authedFetch`, see section 5).

### Notifications and maintenance windows

A notification with `payload["host_id"]` (one server) or `payload["host_ids"]`
(collective notification; silent only if **all** servers fall into a running window)
is silenced by a maintenance window: it appears in the history but goes to no channel
(see [Data model, section 9](../03-DATA-MODEL.md#9-einstellungen-und-branding), in
German). Without a host reference a notification is never silent.

- `send()` returns `NotifyResult(notification_id, suppressed, delivered)`. `suppressed=True`
  means: history only, no push. `delivered=True` means: at least one channel delivered the
  notification; `False`: none did – no channel is set up, all of them failed, or a
  maintenance window suppressed the push; `None`: unknown (older cores). Older cores and
  test doubles return `None` instead of `NotifyResult` – extensions treat that like
  `suppressed=False` and `delivered=None` (e.g. `getattr(result, "suppressed", False) is True`,
  `getattr(result, "delivered", None)`). If you do not need the return value,
  nothing changes for you.
- `would_suppress(host_id=None, host_ids=None) -> bool` checks the same rule but creates
  nothing. As in `send()`, a single string passed as `host_ids` counts as a broken list
  (never silent). It also requires `notify.send`.

**Pattern for state watchers** (Proxmox, backups, game servers): A watcher reports only
state changes and remembers "reported". If that notification was silent, it additionally
remembers this ("reported silently") and on the following checks only asks
`would_suppress()` – so exactly **one** silent history entry is created in the window,
not one per check. When the window is over and the condition still persists, the
notification follows **once, audibly**, with "(since the maintenance window)"
(„(seit dem Wartungsfenster)“) in the title. If the condition has already recovered
within the window, no belated outage notification is sent; the all-clear ("back online",
„wieder online“) goes through the same window check like any notification – so within the
window it, too, goes to the history only. If the all-clear is only noticed after the
window (between two checks), it is sent audibly, with a note that the disruption fell
within the maintenance window.

**The all-clear follows the outage.** If the outage was audible (it came before the
window or as a belated notification) and only the all-clear falls into the window, it
stays silent in the window (nobody gets woken at night) and follows **once, audibly**
after the window, with "(during the maintenance window)" („(im Wartungsfenster)“) in the
title – an alarm that was heard is always closed by an all-clear that is heard, otherwise
it would stay open on the phone. If outage and all-clear both fall into the window, both
stay silent. For this, the watcher remembers "all-clear pending" (including the host) in
the same state; if the condition fails again beforehand, the all-clear is obsolete and is
dropped. What if the belated notification itself falls into a window? Then it stays silent
and follows after that window.

The state ("reported silently", "all-clear pending", plus the host the window applied to)
is kept where the watcher already keeps its state (for all three, in
`data_dir/watch-state.json`), so it survives a restart of the dashboard. If the host
cannot currently be read, the watcher sticks with the remembered host instead of
immediately sending a belated audible notification. If the state file is lost, the worst
outcome is one notification too many – except for the game server's join code: afterwards
it is only remembered, as on the very first run, so a code that stayed silent in the window
is no longer sent later.

---

## 3. Capabilities — the heart of the decoupling

The core defines a small, finite set of `Protocol` classes. It calls them without knowing
who fulfills them.

```python
ctx.capabilities.provide(ProxmoxHostProvider(ctx))
```

| Protocol | Who implements it | What the core builds with it |
|---|---|---|
| `HostProvider` | proxmox, docker, static inventory | the host list, node page, start/stop actions |
| `TerminalTarget` | terminal (SSH), serial/container shells in the future | the web terminal |
| `ConsoleTarget` | proxmox (vncproxy/vncwebsocket) | the graphical console (`/console/:hostId`, noVNC) |
| `FileSource` | files-sftp, nextcloud, truenas, syncthing | the **one** file manager with sidebar |
| `ActionExecutor` | terminal (SSH exec), proxmox (API actions) | execution behind the gate |
| `AIProvider` | nexus-soc (Ollama), others in the future | chat, diagnosis, summaries |
| `NotificationChannel` | ntfy, email, webhook | the notification center |
| `MetricsProvider` | proxmox, docker, node-exporter | tiles and history charts |
| `ServiceCatalog` | service-matrix (Docker API) | the app launcher |
| `BackupProvider` | backups (Proxmox jobs, restic …) | the backup center |
| `SearchProvider` | any | the global search |

### Example: `FileSource` — the interface the file manager is built on

```python
@runtime_checkable
class FileSource(Protocol):
    source_id: str          # unique within the extension
    label: str              # "Nextcloud", "TrueNAS · tank/media", "root@docker"
    icon: str
    caps: FileSourceCaps    # write/rename/search/range_read/sync_status/quota/trash

    async def stat(self, path: PurePosixPath) -> FileEntry: ...
    async def list_dir(self, path, *, cursor=None) -> Page[FileEntry]: ...
    async def open_read(self, path, *, offset=0) -> AsyncIterator[bytes]: ...
    async def open_write(self, path, stream, *, size) -> FileEntry: ...
    async def mkdir(self, path) -> FileEntry: ...
    async def remove(self, path, *, recursive: bool) -> None: ...
    async def rename(self, src, dst) -> FileEntry: ...
    async def search(self, query: str, *, root) -> AsyncIterator[FileEntry]: ...
    async def info(self) -> SourceInfo: ...   # quota, health, deep link for the info panel
```

From this the core provides, **without knowing a single line about Nextcloud or
TrueNAS**:

- the sidebar (all registered sources of all extensions),
- the explorer view,
- the global search as a fan-out across all sources with `caps.search`,
- **drag & drop between sources** as a generic `open_read(A) → open_write(B)` with
  progress over WS,
- sync status icons for sources with `caps.sync_status` (the OneDrive cloud behavior),
- the info panel from `info()` (storage usage, pool health, SMART warnings, deep link
  into the native UI).

Adding a new source means: registering one object. No core code changes.

`open_write` should replace the target only once everything has arrived and never truncate it up front: otherwise
an abort would lose the old content, and if source and target are the same file, writing would truncate the source
itself (`files-sftp` therefore writes, where possible, to a temp file in the target folder and renames it
afterwards).

Optional, deliberately **not** a required member of the protocol: `async file_identity(path) -> dict | None`. With
it the core detects before a transfer whether source and target are the same file (link, `..` detour, second entry
for the same machine), and `/files/transfer` then answers `409`. Return value: `path` (the resolved, absolute path)
and, as far as known, `size`, `mtime`, `uid`, `gid`, `mode` plus `machine` (a fixed ID of the machine, e.g.
`/etc/machine-id`); `None` if the file does not exist or the source cannot determine it. An exception or a return
value that is not a `dict` counts as `None`. Within the same source, `path` decides. Across two sources, two
different `machine` IDs are always two files; otherwise, besides `path`, all five values must be present and match
(cloned machines often share the same ID). Without `file_identity` the core only recognizes the same path within
the same source. The core reads it with `getattr(source, "file_identity", None)`; older sources without the method
stay valid.

**Sources with their own permission:** Optionally, a source names `required_permission: str | None`
(if the attribute is missing, `None` applies). The core then shows and opens it only for users who
hold that permission themselves; `files.read`/`files.write` alone are not enough. This is meant for
sources that work with someone else's credentials: the SSH sources of the terminal extension require
`hosts.execute`, because access runs with the server's login (often root), not with the user's
account. Downloads, searches, writes, copies and denied attempts on such sources are recorded in the
audit log ([API, section 3](../04-API.md#dateien), in German). The attribute is deliberately not a
member of the protocol (otherwise `@runtime_checkable` would reject older sources without it); the
core reads it with `getattr`. If the value is not a string or is empty, only the owner and `admin`
can see and open the source.

**Errors of a source:** A source does not have to translate its errors itself. The core answers `404` for a
`FileNotFoundError` and `502` with `Zugriff auf die Quelle fehlgeschlagen: <Grund>` (access to the source failed:
reason) for any other exception ([API, section 3](../04-API.md#dateien), in German). Network errors (timeout, connection
refused or lost, unknown name, no route to the server) become a German sentence, from any other `OSError` only the
exception's name gets through (its text can contain paths), otherwise its text. If the text of an exception of your
own is already a finished sentence for the interface, the source sets `readable = True` on the class (like
`WebDavError` of the Nextcloud source): a non-empty text then arrives unchanged, and the case counts as expected, like
a network error (no traceback in the container log). Like `required_permission`, the attribute is not a member of the
protocol; the core reads it with `getattr`. The same applies to exceptions from `TerminalTarget.open()`; there the
reason appears in the terminal's error message ([API, section 4](../04-API.md#4-websocket), in German).

### Example: `ActionExecutor` — the interface Nodvard Shield and the script repository are built on

```python
@runtime_checkable
class ActionExecutor(Protocol):
    action_types: frozenset[str]     # e.g. {"shell.exec", "docker.restart"}
    async def execute(self, req: ActionRequest) -> ActionResult: ...
    async def dry_run(self, req: ActionRequest) -> DryRunReport | None: ...
```

Extensions **never** execute anything themselves. They propose:

```python
decision = await ctx.actions.propose(ActionRequest(
    action_type="shell.exec",
    host_ref=host.id,
    payload={"command": "docker restart nextcloud-app"},
    risk=Risk.MEDIUM,
    proposed_by=Actor.ai(model="qwen2.5:7b"),
    reason=justification,                # mandatory field, otherwise ValidationError
    correlation_id=incident.id,
))
```

The core decides (the gate; see [Architecture, section 4](../01-ARCHITECTURE.md#4-das-aktions-gate),
in German), writes the audit entry and, if applicable, executes the action via the matching
`ActionExecutor`.
`reason` is anchored in the data type as a mandatory field — so the obligation to give a
reason cannot be bypassed by accident.

If the core approves the action immediately (mode `full`), it runs in the background.
By default `propose()` waits until it has finished — right for background jobs that need
the result. An HTTP route passes `wait_s=REQUEST_WAIT_S` (20 s, from `nodvard_sdk`); if
the action is not finished by then, `status = executing` is returned, and the page points
to Actions („Aktionen“).

`ctx.actions.result(action_id)` and `ctx.actions.list(correlation_id=…, limit=…)` return only
the extension's own actions, but with the full `result` and the full `payload`, i.e. including the
command as well as output and error text from the server. The core does not check who calls the route
here (its own API shows output and error text only with `hosts.execute`, the full `payload` only with
`hosts.execute` or the matching `actions.approve:<risk>`; see [API, „Aktionen“](../04-API.md#aktionen--der-bestätigungs-workflow),
in German). A route that shows them must therefore require `hosts.execute` itself, e.g. with
`ctx.api.include_router(router, permission="hosts.execute")` like the scripts extension. From the
result, the core puts no text from the server into the audit log (`action.executed`), only
success, exit code, duration and the length of output and error text (plus a fixed sentence when
the gate sets the reason itself). In `detail` of your own entries (`ctx.audit.log`) the core
empties the fields `output`, `stdout`, `stderr` and `command` for readers without `hosts.execute`,
but not `reason` or any other field – so text from the server belongs neither in `reason` nor in
other fields of `detail`, nor in notifications, and a command only in `detail.command` (users who
may only read see notifications as well). In-process handlers (`ctx.events.subscribe`) receive
`action.*` events (such as `action.executed` with the `payload`) in full; over the WebSocket, users
without `hosts.execute` get only a shortened `payload`
([API, section 4](../04-API.md#4-websocket), in German).

**Standing approval (without a click).** A proposal can rely on an approval that a person gave in advance:
`ActionRequest.standing_approval = StandingApproval(granted_by_user_id=…, granted_at=…, label=…)`. This only works
with the permission `actions.standing_approval` in the manifest. Whether the approval still fits the proposal (for
scripts: nothing has changed) is checked by the extension itself. On every proposal, the gate checks whether the
person is still active and may grant standing approvals and approve actions of this risk level
(`actions.standing_approval` and `actions.approve:<risk>`; built in: owner and `admin`). Proposals from an AI never
run via a standing approval. If it applies, the action starts without a click, even in mode `propose` (blocklist and
anti-flapping still apply); `GateDecision.rule` is then `standing_approval`, and the gate appends „– ohne Klick, lief
mit Dauerfreigabe vom <Datum> durch <Benutzername>“ (without a click, ran with the standing approval of <date> by
<username>) to `reason` (date in the dashboard's configured time zone). Otherwise it becomes a normal proposal:
`reason` gets „– Dauerfreigabe vom … durch … gilt nicht: <Grund>“ (standing approval … does not apply: <reason>), and
if the action waits for a click, `GateDecision.detail` names the reason. The name of a missing permission is only in
`gate_decision.standing_approval_rejected.permission` (and thus in `detail` of the audit entry), never in the text.
So the extension's own `reason` only says what is to run.

The executor receives `req.standing_approval` only if the action really started without a click (after a click it is
`None`); this lets it check right before the command whether the approval still applies. For displays such as
"standing approval applies" („Dauerfreigabe gilt“), `ctx.actions.check_standing_approval()` checks the person the same
way as the gate, read-only. Related: `nodvard_sdk.current_job_trigger()` tells a `JobSpec.handler` whether it runs on
schedule (`"schedule"`), by hand (`"manual"`: `POST /jobs/{id}/run` or `ctx.scheduler.trigger()`) or outside a job
(`None`); `Host.credential_username` and `Host.credential_port` give the account and SSH port of the default login
(never the secret; `None` without a login or with older cores).

---

## 4. Widgets — declarative, not code

**The rule:** No widget without a declarative form. For the reason, see
decision D-04 in [00-DECISIONS](../00-DECISIONS.md)
(in German): extensions ship React; the Flutter app cannot run React. If the widget
format were code, the Android app would see nothing of any extension.

```python
ctx.ui.register_widget(WidgetSpec(
    id="incidents",
    title="Vorfälle",                         # "Incidents"
    icon="siren",
    size=GridSize(w=2, h=2, min_w=1, min_h=1),
    refresh=Refresh(interval_s=60, ws_channel="ext.nexus-soc.incidents"),
    data_endpoint="widgets/incidents",        # relative to /api/v1/ext/nexus-soc/
    permissions=["soc.read"],
    view=ListView(
        item=ListItem(
            title="{{ title }}",
            subtitle="{{ host }} · {{ ts | relative }}",
            badge=Badge(text="{{ status_label }}", tone="{{ tone }}"),   # display text in German, color set explicitly
            actions=[
                WidgetAction(id="confirm", label="Bestätigen",          # "Confirm"
                             endpoint="incidents/{{ id }}/confirm",
                             method="POST", confirm=True, style="primary"),
                WidgetAction(id="dismiss", label="Verwerfen",           # "Dismiss"
                             endpoint="incidents/{{ id }}/dismiss", method="POST"),
            ],
        ),
        empty_text="Keine offenen Vorfälle",                            # "No open incidents"
    ),
    component="IncidentFeed",   # optional, web ONLY: richer presentation
))
```

**View types** (a finite set; React and Flutter both implement all of them):

`StatView` · `ListView` · `TableView` · `ChartView` (line/bar/area) · `StatusGridView`
(the service matrix tiles) · `GaugeView` · `MarkdownView` · `ActionsView` (a plain
button bar) · `LogView` (monospace, auto-scroll)

**Template expressions** are deliberately tiny and free of logic: `{{ field.path }}` plus
a fixed list of filters (`relative`, `datetime`, `date`, `bytes`, `percent`, `number`,
`duration`, `tone`, `truncate`, `upper`, `lower`). No expressions, no conditionals, no
loops — otherwise the Flutter side would have to ship an interpreter, and you would have
acquired a second programming language.

**Showing/hiding buttons per row:** `WidgetAction.show_if="{{ can_start }}"` shows the
button only if the rendered value is truthy (not empty, not `false`/`0`/`none`). The
decision is made by the BACKEND (it delivers `can_start` as a ready-made field) — this,
too, is not a condition in the template, just a yes/no field.

**Do not derive badge colors from the display text:** `| tone` guesses the color from
keywords ("critical", "warning" …). For German display words, take the text from a
`*_label` field and the color from the machine value (`{{ status | tone }}`) or from an
explicit `tone` field.

**Data contract:** `GET /api/v1/ext/<id>/<data_endpoint>` returns
`{"data": …, "meta": {…}}`, shaped to match the view type. The core validates the shape
against the view type and reports deviations as an extension error instead of a broken
UI.

**The limit, stated clearly:** `component` is an enhancement, not a replacement. A widget
that exists only as a `component` is rejected at registration — otherwise you get exactly
the web-only imbalance this rule is meant to prevent.

---

## 5. Pages (full UI views)

```python
ctx.ui.register_page(PageSpec(
    id="soc",
    path="/soc",                      # becomes /ext/nexus-soc/soc
    title="Nodvard Shield",
    icon="shield-check",
    nav_section="Sicherheit",         # "Security"
    nav_order=20,
    permissions=["soc.read"],
    component="SocPage",              # export from the frontend bundle
    mobile=MobileFallback.WIDGETS,    # WIDGETS | WEBVIEW | HIDDEN
))
```

Full pages are React — that is fine here, because `mobile` explicitly defines what the
Android app does instead. The default `WIDGETS` means: the app shows this extension's
widgets instead of the page. `WEBVIEW` is allowed for exceptions (e.g. the script editor),
but must be justified in review — a webview is the beginning of the end of the native
feel that is the reason for choosing Flutter.

### Tools on the host page (`register_host_tool`)

In the core, every host has its own page (`/hosts/<id>`, Plesk style: everything about ONE
server in one place). The core does not know any extension by name — what an extension can
do for a host, it declares as a tool tile:

```python
ctx.ui.register_host_tool(HostToolSpec(
    id="guest",
    title="Hardware, Netzwerk & Snapshots",        # "Hardware, network & snapshots"
    description="Kerne, RAM, Disks mit Speicherort, Netzwerk, Snapshots",   # "Cores, RAM, disks with storage location, network, snapshots"
    icon="server",
    category="settings",              # control | monitoring | services | data | settings
    path="/nodes?host={host_id}",     # page of THIS extension, {host_id} is substituted
    kinds=["vm", "lxc"],              # only for these host kinds (empty = all)
    tags=[],                          # at least one of these tags (empty = don't care)
    own_hosts_only=True,              # only hosts that this extension creates itself
    os_families=[],                   # e.g. ["linux"] (empty = all)
    permissions=[],                   # user needs all of them (empty = hosts.read is enough)
))
```

All conditions that are set must match. `GET /hosts/{id}/tools` returns the matching
tiles sorted by category, `order`, title. Registering the same `id` again **replaces** the
tool — so an extension can re-declare it in `on_settings_changed` with new conditions
(example: service-matrix, whose Docker tag is configurable).

### What an extension needs on a server (`register_host_requirement`)

Some extensions need more on the server than just an SSH login: root without a password (`sudo`),
membership in a group (containers without sudo), a program. Here too, the core knows no extension
and no tool by name — the extension declares it, the core offers it in the setup command and checks
it at "Check connection" („Verbindung prüfen“):

```python
ctx.ui.register_host_requirement(HostRequirementSpec(
    id="docker-group",
    label="Docker ohne sudo (Service-Matrix)",        # "Docker without sudo (service matrix)"
    check_command="docker ps -q",         # read-only; exit 0 = ok, 127 = not installed
    ok_text="Docker lässt sich ohne sudo benutzen.",   # "Docker can be used without sudo."
    fail_hint="Benutzer zur Gruppe docker hinzufügen: sudo usermod -aG docker {user}",   # "Add the user to the docker group: …"
    unix_group="docker",                  # the setup command offers "add to group"
    tags=["docker"],                      # only servers with one of these tags (empty = all)
    os_families=["linux"],                # default; empty = any system
))
ctx.ui.register_host_requirement(HostRequirementSpec(
    id="root", label="Root-Rechte", needs_root=True, root_reason="Updates einspielen, Quarantäne",
))                                        # "Root rights" / "apply updates, quarantine"
```

Registering the same `id` again **replaces** the declaration (so that it follows a configurable tag
in `on_settings_changed`); when the extension is deactivated, it disappears. `unix_group` must
match `^[a-z_][a-z0-9_-]{0,31}$`, otherwise the core ignores the name (it ends up in a shell
command). It also ignores privileged groups (`root`, `sudo`, `wheel`, `admin`, `shadow`, `disk`,
`adm`, `staff`, `lxd`, `libvirt`, `kvm`) and writes a log entry; `docker` is allowed.
`GET /hosts/{id}/requirements` returns the matching declarations. Additive: `API_VERSION` stays.

The target page reads `?host=` via the `useUrlParams()` hook (below) and highlights the
host or filters to it — with a visible, removable filter. For the highlighting there is
the core class `nodvard-deck-focus`; the old name `lattice-focus` remains valid (transition),
both are in the same CSS rule in the core. If the query changes from the router's point of view, the
core also rebuilds the page.

A page cannot rely on that alone once it maintains its **own** address via
`replaceState` (tabs in the tab strip ↔ `?tab=`, removing a filter, choosing another server):
from the router's point of view the query then does not change even though the address bar
shows a different one, and a link to the same address does not rebuild anything. The core
therefore fires the event `nodvard-deck:navigate` on `window` after every navigation within an
extension page (`ExtensionPage.tsx`; the browser otherwise reports `pushState` to nobody),
also after back/forward, and sets `window.__nodvardDeck.navigateEvents` for this. It keeps
firing the old name `lattice:navigate` as well (transition, see "Names in the frontend
contract" below). The `useUrlParams()` hook (`extensions/_shared/frontend/src/location.ts`) follows this event —
and only without this marker (tests, preview) additionally `popstate`: in the core shell
the old page would otherwise briefly show the new address and query the server before the
core rebuilds it. The hook returns the current query together with a setter (`replaceState`;
other values, the anchor and the history state are preserved). The third return value is a
trigger: it changes on every navigation to the page (even to exactly the same address), but
not on the page's own changes made through the setter — use it as an effect dependency for whatever
every link should re-trigger although it is not in the address (scrolling to something,
expanding something). Its value does not matter.

For these pages the address bar is the source of truth: what the page filters or shows is
in the query, and "Remove filter" („Filter entfernen“) removes only that one value. A link
without a query (menu item) to the page that is already open resets it to its initial state
(Nodvard Shield: the Overview („Übersicht“) tab, no server filter), even if the router already
considered this address current.

**All bundled pages that read their query use the hook:** Nodvard Shield (`?tab=`,
`?host=`); Backups (`?host=`); Scripts („Skripte“, `?host=`); Service Matrix
(„Service-Matrix“, `?host=`, `?logs=`); System (`?host=`, the server picker writes it too);
Game servers („Gameserver“, `?host=`: bring to the front, scroll to it); and the Proxmox
node page (`?host=`, `&tasks=1`: highlight, expand, filter the history, scroll to it). A new
page that reads its query only once at mount (`new URLSearchParams(window.location.search)`),
on the other hand, does not follow the address bar: a link to the same address no longer has any effect there once the page has
changed itself in the meantime. End-to-end tests with a real router: the pages'
`*.navigation.test.tsx` files; helpers in `extensions/_shared/frontend/src/testShell.tsx`.

Actions appear on the host page automatically (`GET /hosts/{id}/actions`): what a
`HostProvider` offers for exactly this host via `host_actions()` appears as a button at
the top (`source="host"`); actions registered for every host that take an input field
(`params_schema.required`, `command_field`) are listed under "More actions"
(„Weitere Aktionen“). `ActionSpec.host_tags` restricts a globally registered action to
hosts with at least one of these tags — both the listing AND triggering check this
(example: `container.*`/`docker.prune_*` only on Docker hosts). Actions with
`host_bound=False` never appear there and cannot be triggered via
`POST /hosts/{id}/actions/{typ}` either (404) — only the extension itself proposes them
via `ctx.actions.propose`. Required fields of type object/list get no form there; such
actions need their own form in the extension (example: `vm.config_set`).

### Loading the frontend bundle

The bundle is an **ESM module**, built with React, ReactDOM and the SDK as `external`.
The core serves it at `/api/v1/ext/<id>/frontend/index.js` and loads it via `import(url)`.
Shared singletons hang off `window.__nodvardDeck` (old name `window.__lattice`, remains
valid — transition); an import-map shim resolves `react`,
`react/jsx-runtime` and `react-dom` to it.

**Names in the frontend contract (transition).** With the rename to Nodvard Deck, everything
bundles hook into has a new name. The old names remain valid until a major version removes
them explicitly:

| New | Old (remains valid, transition) |
|---|---|
| `window.__nodvardDeck` | `window.__lattice` — **the same object**; a change through one is visible through the other |
| event `nodvard-deck:navigate` | `lattice:navigate` |
| event `nodvard-deck:timezone` | `lattice:timezone` |
| CSS class `nodvard-deck-focus` | `lattice-focus` (both in one rule) |
| TypeScript type `NodvardDeckTokenRefreshResult` | `LatticeTokenRefreshResult` (alias) |

The core shell sets both names and fires every event under both names. A bundle or kit reads
the object as `window.__nodvardDeck ?? window.__lattice` (the UI kit does this via `deck()` from
`extensions/_shared/frontend/src/deck.ts`) and listens to **exactly one** event: to
`nodvard-deck:…` if `window.__nodvardDeck` exists, otherwise to `lattice:…`. That way it never
reacts twice. The reason for both: a browser tab that was open before an update loads new
bundles into its old core (it only knows `__lattice`), and third-party extensions with an older
UI kit run against a new core. The import-map shims (`/lattice-shim/react.js` etc.) read both
names as well; their **path** `/lattice-shim/` stays unchanged, because the page of an old tab
points to it.

For calls to the dashboard API, the pages of the bundled extensions share a UI kit
(`extensions/_shared/frontend/src/`): `authedFetch` attaches the token from
`window.__nodvardDeck.getAccessToken()` and, on a 401, refreshes it once via
`window.__nodvardDeck.refreshAccessTokenResult()`. That returns `{status: "ok", token}`,
`{status: "rejected"}` (the server rejects the login) or `{status: "unavailable"}` (the
server is not responding right now or responded with an error; the login is kept) and
never throws. If the server responded with a real error (e.g. 507 "Insufficient storage",
500), `unavailable` additionally contains `httpStatus` and `message` (its status and
reason); without these fields (network error, timeout, 502/503/504 without a readable
reason) there is no statement from the server. Both fields are optional; older cores do
not supply them. Older cores also lack `refreshAccessTokenResult()` itself; in that case
`authedFetch` uses `window.__nodvardDeck.refreshAccessToken()`, which, as before, returns the
new token or `null` on any failure and likewise never throws. If the server does not
respond — network error, 502/503/504 without a reason of its own, refreshing currently
impossible — `authedFetch` throws `ServerUnavailableError` with the same message as the
core ("Server currently unreachable – please try again shortly.", „Server gerade nicht
erreichbar – bitte gleich noch einmal versuchen.“). If the server responds to the refresh
with a real error and names a reason, the error carries `status` and the message "Server
reports: <reason>" („Server meldet: <Grund>“); older cores without `message` stay with the
first message. Only a login that was truly rejected comes back as a 401 ("Not
authenticated.", „Nicht authentifiziert.“). `runAction` proposes an action, approves it
itself if the user has the permission, and on a 202 "executing" polls `GET /actions/{id}`
every 3 s until it has finished (server outages during polling do not count as a result).

There is deliberately **no** Module Federation: it couples the extension to the build tool
and the exact bundler version of the core. A plain ESM module with externals is
tool-neutral — an extension can be built with Vite, esbuild or `tsc`.

```tsx
// extensions/<id>/frontend/src/index.tsx (example)
// Every `component` from the manifest is a named export of the bundle.
export { SocPage } from "./SocPage";
export { IncidentFeed } from "./IncidentFeed";
```

A real, minimal example is in `extensions/hello-world/frontend/src/`: one page
(`HelloPage.tsx`), built with `extensions/hello-world/frontend/build.mjs`.

---

## 6. Connectors — configurable connections to third-party systems

An extension registers a **type**; the user creates **instances** in the UI.

```python
ctx.connectors.register_type(ConnectorType(
    id="ollama",
    label="Ollama",
    icon="cpu",
    schema=OLLAMA_SCHEMA,             # JSON Schema → form
    secret_fields=["api_key"],        # move into the vault automatically
    test=test_ollama,                 # async (config) -> ConnectorHealth
    factory=build_ollama_client,
))
```

The core takes care of: the form, saving, moving secrets into the vault, a "test
connection" button, the periodic health check, and showing errors in the notification
center.

This structurally replaces hand-maintained lists of hosts, web addresses and critical
containers: a new game server is a connector instance or a discovered
host with a tag, not a JSON entry that quietly goes stale.

---

## 7. Data, migrations, namespaces

- An extension's tables must be named `ext_<id>_<name>` (with `-` → `_`).
  The core checks this on load and otherwise refuses the extension.
- Every extension has its **own Alembic branch**
  (`alembic upgrade heads` brings up the core plus all extensions).
- `ctx.db` provides sessions whose access is restricted to the extension's own prefix.
  Core data is never read directly, but through `ctx.hosts`, `ctx.audit` and so on —
  otherwise the core's data model would be frozen as soon as the first extension exists.
- Uninstallation: `state=uninstalling` → `on_stop` → optional
  `drop_data` hook → remove tables → delete the registry row.

---

## 8. How three modules are implemented against the interface

This is the proof that the interface is sufficient for them.

### Nodvard Shield

| Component | Implementation against the interface |
|---|---|
| Ollama chat | `ConnectorType("ollama")` + `AIProvider` capability |
| Free-text chat UI | `register_page("soc")`, streaming via `ctx.ws` |
| Incident queue/batching | own tables `ext_nexus_soc_incidents`, background task via `ctx.spawn` |
| Docker watcher (8 s) | `ctx.spawn` + `ctx.exec.run()` against hosts with the tag `docker` |
| Remediation | `ctx.actions.propose(...)` — **never** `ctx.exec` directly. AI text only ever yields a restart of a crashed container (`shell.exec`, the extension builds the command itself); everything else stays text |
| Blocklist / anti-flapping | core gate; the extension only supplies additional patterns |
| Obligation to give a reason | `ActionRequest.reason` is a mandatory field |
| Two operating modes | core setting `autonomy.mode`, not extension code |
| Scheduled audits (e.g. nightly Lynis) | `ctx.scheduler.register_job(...)` — the same scheduler as in the script repository |
| ntfy notifications | `ctx.notify.send(...)` → `NotificationChannel` of the ntfy extension |
| Incident feed widget | `WidgetSpec` with `ListView` → automatically appears in the Android app as well |

What the core does **not** know in all this: that Ollama exists, that hosts are Proxmox
guests, what a container is.

### Script repository

| Component | Implementation |
|---|---|
| Versioning | own Git repo under `/data/ext/scripts/repo`, `pygit2`/`dulwich`; the extension sets the Git settings itself on every start, and hooks, filters and signing never run (from `.git`, a backup only carries the history) |
| Metadata, parameters | own tables + a JSON Schema per script |
| "Run now" | `ctx.actions.propose(ActionSpec("script.run"))` → the same SSH layer as the terminal (no second execution path) |
| Fleet-wide schedule | `ctx.scheduler` — **one** view, because there is **one** scheduler |
| Scheduled runs without a click | standing approval per script, stored in `ctx.data_dir` (not in the Git repo: resetting the script does not revive an expired approval). Only real scheduled runs (`current_job_trigger() == "schedule"`) send it as `ActionRequest.standing_approval`. Beforehand the extension checks that content, parameters, target, schedule and the servers' account, address and SSH port are still as they were when it was granted; the core gate checks that the person who granted it is still allowed to |
| Run history | core tables `jobs`/`job_runs` + audit |
| Secrets | `ctx.secrets.get_handle(...)`, never plaintext in the script |
| In-browser editor | `register_page`, CodeMirror 6 |
| Security gate | the same core gate as in Nodvard Shield — no second confirmation UI |
| AI "promotes" a recurring fix | `ctx.events.subscribe("action.executed")` in the scripts extension; the proposal is kept as a draft in the repo. The two extensions talk via the event bus, not to each other. |

### File manager sources

Every source is a `FileSource`. `files-sftp` (host admin access) lives in the terminal
extension because it already has the SSH layer; `nextcloud` (WebDAV), `truenas` (API +
SMB/NFS) and `syncthing` (REST) are separate extensions, each with one connector instance
per server. The core renders one explorer across all of them.

---

## 9. Why the script repository is a *bundled* extension and not a core module

It is tempting to put the script repository into the core platform. The reasoning behind
that is sound — the repository is generic and valuable to every self-hoster. It still ships
as a bundled extension, for three reasons:

1. **It keeps the interface honest.** The script repository is the most demanding generic
   module: execution, schedule, secrets, gate, run logs, its own editor. If it can be
   built *against* the interface, the interface is sound. If it is built into the core
   instead, it goes unnoticed which gaps the interface has — until a third-party extension
   trips over them.
2. **It keeps the core empty.** An empty, renameable core
   ([Architecture, section 1](../01-ARCHITECTURE.md#1-die-drei-schichten), in German) is
   only empty if it really contains nothing infrastructure-specific. A script runner in the
   core is a feature every installation carries whether it needs it or not — and one that
   could then not be switched off.
3. **Nothing changes for the user.** Bundled and enabled by default, it feels like part of
   the core. The only difference is that it can be switched off.

What **really** belongs in the core is what the script repository presupposes: execution
layer, scheduler, gate, vault, run log, audit. Those are exactly the core services.

---

## 10. Versioning and compatibility

`nodvard_sdk.API_VERSION` is SemVer. An extension declares `api_version`.

| Change | Version | Behavior |
|---|---|---|
| New optional field, new protocol | Minor | Old extensions keep working |
| A field's meaning changes, a protocol method is removed | Major | The core does not load old extensions; it marks them "Not compatible" („Nicht kompatibel“) and names the API version the extension requires and the one the core provides |
| Bug fix | Patch | — |

**SDK stability (from now on):** Even while the version is `0.x`, `nodvard_sdk` changes only in a
backward-compatible way. Public names (`__all__` and the public classes/functions of the modules)
are not removed or renamed, parameters are not dropped, and new parameters get a default value.
Renaming is only possible with a transition: the old name stays in parallel for several releases
(documented as deprecated). Background: Nodvard Link, see [API, section 7](../04-API.md#7-kompatibilität)
(in German). A test (`backend/tests/contract/test_sdk_contract.py`) compares against the checked-in
list `sdk_public.json`; new names are brought up to date with `python scripts/update_api_contract.py`,
exceptions only with a justification in `backend/tests/contract/breaking_exceptions.toml`. The major
version will be raised to `1.0` once the bundled extensions are stable; the rules of the table
above then apply unchanged.

**Deliberately stricter for security reasons:** `ctx.api.include_router()` without `permission=` requires a login
(such a route used to be reachable without one), and the core rejects WebSocket routes, `add_route()` and `mount()`
without `public=True`; the extension then cannot be enabled (see section 2). The signature stays backward-compatible,
`public=` is new and optional. Older cores know neither `api.public` nor `public=` (an extension with `api.public` in its
manifest does not load there) and leave routes without `permission=` open; if you also support them, set `permission=`.

The same goes for the request size: the core rejects any request over 1 MiB on extension routes with `413`; a route that
accepts larger uploads needs `max_body_bytes` for that (see section 2). Older cores do not know the name (the import
fails there) and have no general limit.
