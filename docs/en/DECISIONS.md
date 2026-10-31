# Architecture Decisions

> The Nodvard Deck interface is currently available in German only; an English interface is planned. UI labels are given in English with the German original in quotes.

This document records the fundamental technology and architecture decisions behind
Nodvard Deck, each with its rationale and consequences. Code, comments and other
documents refer to them by the identifier `D-<n>`; the numbers stay stable.

---

## Summary

| Area | Decision | Note |
|---|---|---|
| Backend | Python 3.12 · FastAPI | exactly one worker process (D-01) |
| Frontend | React 18 · Vite · TypeScript | Tailwind + Radix (D-03) |
| Mobile | Flutter | widgets declarative only (D-04) |
| Database | SQLite by default | PostgreSQL optional (D-02) |
| Terminal | xterm.js + SSH | `asyncssh`, not `paramiko` (D-05) |
| Delivery | one multi-arch Docker image | one container, one volume (D-10) |

---

## D-01 Backend: Python 3.12 + FastAPI

**Decision:** The backend is written in Python 3.12 with FastAPI.

**Rationale:** async for WebSockets and the terminal. Python also lowers the risk when
replacing the predecessor system, which was also written in Python: proven regex
patterns (e.g. the command blocklist), prompts and heuristics (e.g. against restart
loops) were carried over from the predecessor instead of being translated into another
language. The ecosystem fits: `asyncssh`, `proxmoxer`, `docker`, `httpx`,
`cryptography`, `pyotp`.

**Constraint 1: exactly one worker process** (`uvicorn --workers 1`), no Gunicorn
multiprocessing. The core holds in-process state that must not be duplicated: extension
registry, event bus, WebSocket hubs, scheduler, SSH connection pool, anti-flapping
history. Scaling is done through `asyncio` when needed, not through processes. Anyone who
wants multiple processes must first externalize the event bus and scheduler. That is a
deliberate, documented break, not an oversight.

**Constraint 2: no blocking code in the event loop.** Anything that blocks
(`subprocess`, `paramiko`, Tesseract, ClamAV) runs via `asyncio.to_thread` / an explicit
executor. This is the most common source of errors in FastAPI projects of this size and
the reason for the `asyncssh` decision (D-05).

**Rejected alternative:** Go. It would be leaner on a Raspberry Pi and would allow
delivery as a single binary. However, it loses out on carrying over code from the
predecessor system and on the Python ecosystem for infrastructure integration. The
delivery question is solved instead by a multi-arch Docker image (D-10).

---

<a id="d-02-datenbank-sqlite-als-default-postgresql-optional--abweichung-von-9"></a>

## D-02 Database: SQLite by default, PostgreSQL optional

**Decision:** SQLite is the default database. PostgreSQL is planned and the code stays
dialect-neutral for it, but it is **untested**: there has been no test run against
PostgreSQL yet, and the `asyncpg` driver is not in the image.

**Rationale:**

1. **Target hardware.** A typical hosting target is a Raspberry Pi with a few GB of RAM
   that already runs other services. A Postgres server permanently costs 100–200 MB RSS
   there, plus a second service that has to be patched, backed up and monitored. With
   SQLite, the Pi remains a realistic hosting target.
2. **Operational overhead.** Nodvard Deck should be operable without hand-holding:
   offline, without a license server, with documentation instead of a support contact.
   Every additional mandatory component is another hurdle during installation and another
   source of errors. `docker run` with one volume has to be enough.
3. **The load doesn't justify Postgres.** A handful of users, a few dozen hosts, audit
   and run logs in the range of a few thousand rows per week. SQLite in WAL mode is
   orders of magnitude above this need.

**Implementation:** SQLAlchemy 2.0 (async) + Alembic. `DATABASE_URL` decides;
`sqlite+aiosqlite:///./data/lattice.db` is the default. `postgresql+asyncpg://…` is
planned, but only counts as supported after a real test run.

**Discipline rules that preserve the freedom of choice, binding for all new code:**

- No `JSONB`, no `ARRAY`, no `ILIKE`, no `ON CONFLICT … DO UPDATE` dialect code, no
  Postgres functions. Only `sa.JSON`, `sa.String`, portable expressions.
- Lists (e.g. host tags) as an association table, not as an array column.
- Timestamps always UTC-aware `DateTime(timezone=True)`, default set in Python (not
  `server_default=func.now()`, which drifts between dialects).
- SQLite pragmas on connect: `journal_mode=WAL`, `foreign_keys=ON`,
  `busy_timeout=5000`, `synchronous=NORMAL`.
- Writes go through **one** session path; no parallel writer tasks that block each other.
  Long write operations (log import) in batches.

**Limit that SQLite must not cross:** time-series telemetry (per-host CPU/RAM curves at
one-second resolution) does **not** belong in the relational DB, neither in SQLite nor
in Postgres. Live values exist only in memory. Anyone who wants long metric history
attaches an existing Prometheus/Grafana via a connector.

**Addendum: metric history.** A history exists nonetheless, without violating this limit.
If the source itself has history (Proxmox keeps RRD data), the provider passes it through
via `MetricsProvider.history()`; Nodvard Deck stores nothing. Only for hosts without such
a source (e.g. a Raspberry Pi connected via SSH) does `core/metrics_history.py` collect
data every 30 s into a **separate** SQLite file `data/metrics.db` with its own
connection and exactly one writer, not in `lattice.db`, without Alembic, deletable at any
time (then only the history is missing). Raw values for 48 h, 5-minute aggregation
(mean/min/max) for 35 days. One-second resolution is reserved for the live view and is
never stored.

---

<a id="d-03-frontend-react-18--vite--typescript--bestätigt"></a>

## D-03 Frontend: React 18 + Vite + TypeScript

**Decision:** React 18 with Vite and TypeScript, supplemented by the following
specifications:

- **UI foundation: Tailwind CSS + Radix UI Primitives.** Deliberately *no* ready-made
  component framework (MUI, Mantine, Ant). Reason: white-labeling must work **at
  runtime**: an admin changes colors in the settings without rebuilding the frontend.
  This works cleanly only via CSS custom properties fed from `GET /api/v1/branding`.
  Ready-made frameworks bring their own theming systems, some fixed at build time, and
  work against this.
- **Server state: TanStack Query.** Client state: Zustand. No Redux.
- **Routing: React Router v6** with lazy routes, where extension pages hook in.
- **Terminal: xterm.js** + `@xterm/addon-fit`, `@xterm/addon-webgl`.
- **Editor (script repository): CodeMirror 6**, not Monaco. Monaco is ~4 MB and pulls in
  web workers; CodeMirror 6 is modular and noticeably cheaper for a bundle served from a
  Pi. Its feature set is sufficient for shell, Python and YAML.
- **Build target:** The frontend is built statically and served by the backend
  (`StaticFiles`), with no separate web server. One port, one service, one reverse-proxy
  entry.

---

<a id="d-04-mobile-flutter--bestätigt-mit-einer-konsequenz-die-9-nicht-zieht"></a>

## D-04 Mobile: Flutter, declarative widgets

**Decision:** The planned app will be built with Flutter: a real APK, native feel, iOS
remains possible.

**Consequence:** An extension cannot ship a Flutter UI; it ships React. If widgets were
React components, the app would see **nothing** of the extensions, and the principle
that web and app share one API and no logic is built twice would be broken with the first
extension.

That is why the widget interface is **declarative**: an extension describes a widget as a
data structure (type + field bindings), not as code. React and Flutter both implement the
same finite set of widget types. Details in
[Extension interface, section 4](EXTENSION-API.md#4-widgets--declarative-not-code).

An extension may additionally ship a React component for a richer web presentation, but
only as an *enhancement* on top of the declarative base, never as its replacement. Rule:
**no widget without a declarative form.**

The app will only be built once the API is stable.

---

## D-05 Terminal: xterm.js + asyncssh

**Decision:** The web terminal is not a local PTY but a remote PTY over SSH; the browser
displays it with xterm.js. The core speaks SSH via `asyncssh`.

**`asyncssh` instead of `paramiko`.** Paramiko is blocking and would need one thread per
session plus a bridge into the event loop. With several open terminals, script runs and
watcher loops on weak hardware such as a Raspberry Pi, that is exactly the architecture
that later produces inexplicable hangs. `asyncssh` is async-native, can pool connections
and channels, and fits the object model of the rest of the core.

The same SSH layer serves **three** consumers: web terminal, script execution (there is
no second execution path) and AI remediation. One layer, one audit point, one failure
profile.

**Security correction compared with the predecessor system:** There, SSH ran with
`-o StrictHostKeyChecking=no`. The new layer maintains its own known-hosts table (TOFU on
first contact, pinning afterwards; a change produces a visible warning instead of silent
acceptance). New installations skip TOFU by default: a new key is only remembered once
someone has confirmed the fingerprint (setting `ssh.confirm_new_host_keys`); after a key
has been forgotten, this always applies.

**Own SSH keys:** Credentials belong in the vault (D-06); rotation and revocation are
platform features, not manual work in `authorized_keys`. Nodvard Deck can generate a
dedicated ed25519 key pair per server: the private part goes straight into the vault, the
public part is entered once in the server's `authorized_keys`. An existing access method
remains the default until the new one has been verified and deliberately switched over.

---

## Further decisions

### D-06 Secrets vault
`cryptography` / Fernet (AES-128-CBC + HMAC-SHA256). The master key lives in
`data/master.key` (0600, path configurable via `NODVARD_DECK_MASTER_KEY_PATH`) and is
generated on first start, never stored in the database. No home-grown crypto, no secrets
in plaintext in the DB, no secret values in any API response; extensions get a
`SecretHandle`, never the string. The key version is stored along with the data
(`data/vault_keyring.json`) so that rotation is possible without re-encrypting all
secrets at once. Unlocking with a passphrase (Argon2id KDF) at startup is planned but not
yet implemented.

### D-07 Auth
- Passwords: **Argon2id** (`argon2-cffi`). For requests, verifying and hashing run in a
  thread (not in the event loop), at most two at a time. If 16 more are already waiting,
  login, 2FA with recovery code and initial setup respond with 503 (`Retry-After: 5`).
  Things that only logged-in users trigger (changing or setting a password, creating
  users, security questions, new recovery codes) wait instead.
- Access token: JWT, 15 min, held in client memory.
- Refresh token: opaque random value, hashed in the DB, **revocable**, 30 days.
  Web receives it as an `HttpOnly; Secure; SameSite=Strict` cookie, Android as a value
  for `flutter_secure_storage`. The access-token path is identical for both
  (`Authorization: Bearer`), which is why the API is truly a shared one.
- 2FA: TOTP (`pyotp`), the secret lives in the vault.
- Additionally `api_tokens` for automation (scoped, revocable), instead of unprotected
  endpoints called from outside via cron.

### D-08 Scheduler
**One** scheduler in the core, based on **APScheduler 3.x**. Security audits and the
script repository register their jobs with it. No cron entries on the hosts: scattered
cron entries easily lead to jobs running twice that nobody sees.

Originally, APScheduler's `SQLAlchemyJobStore` was planned. What is implemented is the
`MemoryJobStore`; the only persistent source is the dedicated `jobs` table. Reason: the
SQLAlchemy job store persists via pickle, but extension handlers are methods of instances
that are recreated on every start, and two persistent sources could drift apart. Details
in `core/scheduler.py`.

The script repository itself is a bundled extension; its building blocks (execution,
scheduler, gate, vault, run log) belong to the core. See
[Extension interface, section 9](EXTENSION-API.md#9-why-the-script-repository-is-a-bundled-extension-and-not-a-core-module).

### D-09 Background work / queue
**No Celery, no Redis, no RabbitMQ.** Background work runs as an `asyncio.Task` with a
`job_runs` record persisted in the DB. Rationale as with D-02: every additional mandatory
component is operational and support burden, and the load doesn't justify it. Persistence
exists nonetheless: a run interrupted by a restart is marked `interrupted`, not silently
forgotten.

### D-10 Delivery
Multi-arch Docker image (`linux/arm64` for Raspberry Pi and other ARM machines,
`linux/amd64` for x86 machines), one container, one volume (`/app/data`: SQLite, master
key, extension data, script Git repo). A demo image for trying things out needs no build
of its own: the same image starts with `NODVARD_DECK_DEMO_MODE=1` with sample data.

The Raspberry Pi is confirmed as a hosting target: in a 24-hour run on a Pi that carries
other services on the side, the container stayed well below the abort criteria
(load average above 4.0 or more than 400 MB RSS); an x86 machine is the fallback target
if the load grows.

**Addendum: sample data ("View with sample data", „Mit Beispieldaten ansehen“).** Demo
mode creates the same sample data as the button in the cockpit (`POST /demo/seed`,
`core/demo_seed.py`): five servers from the documentation range `192.0.2.0/24` (RFC 5737,
without credentials, so no background job and no connection check can touch them), four
notifications and, if modules provide widgets, a sample layout in the user's dashboard
(the old one is backed up and restored). In addition there are three sample apps:
*custom apps* (manually created links, a core concept) with addresses likewise from
`192.0.2.0/24`, two of them with a sample server as reference; Nodvard Deck never fetches
these addresses. **Deleting sample data** („Beispieldaten löschen“) removes them again.
*Detected* apps do not exist as sample data, because they come from a module's container
overview; creating them without a module would be either misleading or would pull module
logic into the core.
- **Marking without a migration:** The IDs of all created rows are stored in the global
  setting `demo.seeded_ids`. Deletion removes exactly these IDs; real data (including data
  that is created alongside later) remains untouched. In addition, a sample server that
  has been converted into a real one (address no longer from `192.0.2.0/24` or imported
  by a module) is kept; likewise a sample app whose address is no longer in
  `192.0.2.0/24`.
- **Credentials on a sample server:** The server is deleted anyway (it is recognizably a
  sample; otherwise a remnant would remain that never disappears), but the confirmation
  prompt names it beforehand.
- **Permissions:** Creating/deleting requires `hosts.write` **and** `settings.write`
  (servers, notifications for everyone, dashboard: the whole installation); the status
  (band) is visible to anyone with `hosts.read`.
- **Only on an installation without real servers** (otherwise `409`); notifications are
  created directly as rows, never sent through the channels.

### D-11 Observability
`structlog` as JSON logs on stdout, `X-Request-Id`/`correlation_id` through all layers
including the audit log. No metrics pipeline of our own; a `/metrics` endpoint in
Prometheus format is enough, and an existing Prometheus can scrape it.

Status: `correlation_id` is implemented in the audit log, actions and job runs. JSON logs
via `structlog`, `X-Request-Id` and the `/metrics` endpoint are not yet implemented.

### D-12 Async SQLAlchemy: `relationship()` on newly created objects

**Rule:** After `session.add(obj)` (+ flush, if applicable), no `relationship()` column on
`obj` may be read OR written via `.append()` without first calling
`await db.refresh_relationships(session, obj, "column_name")`
(`backend/src/nodvard_deck/db/base.py`).

**Rationale:** `lazy="selectin"` (the only loading strategy used in this schema, see
`models/*.py`) only loads a collection eagerly if the parent object entered the session
via an **awaited query**. An object that has just been constructed via `Model(...)` and
added with `session.add()` never came through a query, so its collections are not loaded.
The first access (read or write) triggers SQLAlchemy's synchronous fallback lazy load,
which crashes with `MissingGreenlet` in an async session without a running greenlet
context.

**Known cases** (all occurred in running code, none found by reading):
1. `services/auth.ensure_builtin_roles()` (`Role.permissions`).
2. Reading `User.roles` in a test (the same pitfall).
3. `ext/context.py` `HostsHandle.upsert_discovered()` (`Host.tags`).
4. `services/hosts.create_host()` (`Host.tags`, in a different form): the code already
   called `refresh_relationships()`, but only `if tags:`. The more common case without
   tags slipped through, because an empty but unloaded collection triggers a lazy load
   just like a populated one.

**It follows that:** `refresh_relationships()` is **never behind a condition of any
kind.** The justification for an exception can sound different every time ("probably
empty", "is read right away anyway", "only needed for bulk import"). Any `if`/`else` that
decides between `session.add()` and the refresh call whether to refresh is therefore a
bug candidate by definition, regardless of the content of the condition. The refresh
itself is cheap (one additional `SELECT` for an empty collection); the risk of an
overlooked guard is not.

**Considered and deliberately NOT built: an automatic lint/AST check** (analogous to
`scripts/check_core_purity.py`). Detection would require real data-flow analysis (which
name is a freshly constructed object, which attribute is a relationship, was it refreshed
in between). Purely syntactic scanning would have a high false-positive/false-negative
rate here, for currently only four affected columns in the entire schema (`Host.tags`,
`Host.credentials`, `User.roles`, `Role.permissions`). The effort for a reliable checker
is out of proportion to the benefit, as long as the tests run real code paths against a
real database and the error shows up loudly there immediately.

**Instead:** `db.refresh_relationships()` as a named, greppable helper (makes the fix
findable in one place) plus this documentation. Every function that creates a model with
its own `relationship()` columns should cite this rule in its docstring, as
`ensure_builtin_roles()` and `HostsHandle.upsert_discovered()` do.

---

### D-13 Singleton/DI escape: code that bypasses `app.dependency_overrides`

**Rule:** Any code that depends on a value the caller (a FastAPI request, a test, an
extension load) may want to OVERRIDE (`Settings`, the DB `Session`, the override wiring of
an entire `APIRouter`) MUST receive that value explicitly through the call path
(parameter, `Depends()`, settings passed to `build_context()`). A convenient reach for a
process singleton (`config.get_settings()` called directly, a freshly constructed
`APIRouter()` without a link to the app) looks exactly like the correct path in the
success case, until a caller (a test, a second request context) expects something
different and the singleton silently returns the wrong thing.

**Known cases** (the same root cause in three different places):

1. **`ext/context.py` `SecretsHandle.create()`/`Context.vault_use()`.** Both called
   `config.get_settings()` directly instead of the settings passed in when the extension
   was loaded. In tests, `app.dependency_overrides[get_settings]` therefore had no effect
   (it only applies to DI resolution of endpoints); the call ended up at the real, cached
   process singleton and thus at the real project paths (`data/master.key`). Even the test
   for exactly this isolation
   (`test_vault_use_isolation_survives_a_real_extension_request_failure`) didn't notice,
   because it only checked the status code and audit entry, not the keyring file that was
   used. **Fixed structurally:** `settings` is passed explicitly through
   `build_context()` and stored on `Context` (see the docstring of
   `Context._app_settings`).
2. **Scattered places in the core** (`api/v1/auth.py::_cookies_require_https()`,
   `db/session.py::get_engine()`, the lifespan in `main.py`). These places still call
   `get_settings()` directly, deliberately not reworked individually (see below).
   Instead, the autouse fixture `_reset_settings_singleton` in `tests/conftest.py` sets
   `config._settings` to `None` before AND after every test, so that no test is
   contaminated by the singleton of a previous test. **Only shielded:** This protects the
   tests but changes nothing about the production code.
3. **`ext/runtime.py` `ExtensionRuntime.mount_router()`.** A freshly constructed
   `APIRouter()` (`scratch`) has `dependency_overrides_provider=None`; FastAPI (0.141)
   carries this value over IMMUTABLY into every resulting route on the `include_router()`
   call (`_RouterIncludeContext.for_include()`). Without
   `scratch.dependency_overrides_provider = app` BEFORE the `include_router()` call, every
   route mounted via an extension that uses `Depends(get_settings)` or
   `Depends(get_session)` (directly or via `CurrentUser`) would have silently ignored
   `app.dependency_overrides`. **Fixed structurally** for all extensions, because
   `mount_router()` is the only call path through which an extension route is mounted. A
   regression test (`test_include_router_permission_param_protects_route_default_needs_login`)
   checks it against a real caller loaded via the registry.

**The difference between case 3 and cases 1+2 is the real lesson:**
Case 3 is fixed structurally because there is EXACTLY ONE call path, which the fix covers
centrally. Case 2 is only SHIELDED (test fixture), because the affected places are
scattered and each would have to be reworked individually, without any caller having been
harmed by it so far (they all run early in the process lifecycle, never repeated per test
with different settings). Whether a new case is fixed "structurally" or only "shielded"
must therefore be explicitly checked and stated each time, and not assumed.

**Considered and deferred: a lint/AST check against direct `get_settings()`/
`get_session()` calls outside of `Depends(...)`.** Unlike D-12, this would be
syntactically feasible. Deferred, not rejected: the scattered places from case 2 are
known, harmless (see above) and few; a checker would currently be more maintenance effort
than benefit. If a fourth, different instance of this class of error occurs, the checker
will be built.

**Instead:** this documentation. Every new case of this class is added here as a further
numbered item (as with D-12), so that the question "is this already the fourth instance?"
remains a grep query.

---

### D-14 SQLite single-writer deadlock caused by nested, independent sessions

**Rule:** Any core caller that keeps a DB `Session` open across a call into extension code
MUST commit its own pending writes BEFORE that call.

**Rationale:** Almost every DB-touching `ExtensionContext` handle (`ctx.hosts`,
`ctx.exec`, `ctx.secrets`, `ctx.settings`, `ctx.scheduler`, `ctx.notify`, `ctx.audit`,
`ctx.actions`, `ctx.vault_use`, `ctx.connectors`) opens its own independent
`session_scope()` on every call (`ext/context.py`), intentionally, so that each handle
call is atomic on its own, independent of the caller. But if the CALLER already holds an
open, unflushed write transaction, SQLite's single-writer rule (D-02) blocks the nested
second transaction until `busy_timeout` (5 s) expires; it then fails with "database is
locked". This is not a coincidence but a structural deadlock.

**Known cases** (the same root cause in two places, plus a probable explanation for a
third):

1. **`core/gate.py::execute_action()`.** The caller
   (`api/v1/actions.py::approve_action()`, session open for the entire request duration)
   calls `executor.execute()`, which is extension code that, for example, calls
   `ctx.vault_use()` (`build_connector()` of the Proxmox extension). Occurred on the first
   `vm.stop` against a Proxmox API mock: 5 seconds of waiting, then "database is locked"
   on the `secret.used` audit entry. **Fixed structurally:** `execute_action()` commits
   BEFORE calling the executor; every action execution runs exclusively through this path.
   Verified against a real file DB
   (`test_execute_action_does_not_deadlock_when_executor_opens_nested_session`); the
   in-memory test fixture with `StaticPool` shares one physical connection and would
   never have shown the problem. The same argument applies to
   `test_vault_use_audit_survives_caller_session_rollback`.
2. **`services/extensions.py::enable_extension()`.** Holds `session` open with a pending
   write (`record.granted_permissions`) while `instance.setup()`/`instance.on_start()`
   (extension code) themselves call writing handles (for hello-world
   `ctx.scheduler.register_job()` in `setup()` and `ctx.audit.log()` in `on_start()`).
   Reproduced against a real file DB
   (`test_enable_extension_does_not_deadlock_with_caller_session_held_open`).
   **Fixed structurally:** `enable_extension()` commits before every transition into
   extension code (`setup()`, `on_start()`); `disable_extension()` received the same
   protection before `on_stop()`, even without a currently known pending write at that
   point. This is structural protection instead of an assumption about the respective
   caller.
3. **Probable, not proven:** occasional "database is locked" errors in an extension's
   `on_start()` on restart. The boot path
   (`load_enabled_from_registry()` → `enable_extension()` → `on_start()`) is the same as
   in case 2, which plausibly explains these errors. There is no independent reproduction
   of the original errors; the explanation must therefore not be treated as proven.

**Not solvable centrally: extensions' own routes.** A route mounted via
`ctx.api.include_router()` that itself declares `session: SessionDep` AND calls a writing
`ctx.*` handle carries the same risk. The core cannot prevent this, because it does not
control what an extension route does with a session it requested itself. The same rule
therefore applies to extension authors: commit your own session before a writing `ctx.*`
call. No core caller is affected by this.

**Considered and deliberately NOT built:** a general safeguard in `session_scope()`/
`ext/context.py` itself (e.g. every handle call automatically commits a caller session
that is open in the same task context). This would invisibly affect every caller, also
outside the two fixed cases, and would be harder to understand than two targeted fixes.
Deferred until a third, independently reproduced CORE caller (not the case of extension
routes above) shows this pattern. This is the same stance as D-13 toward a lint check.

---

### D-15 Extension routes don't know the calling user

**Status: solved.** `ctx.api.current_actor` is a FastAPI dependency
(`actor: Actor = Depends(ctx.api.current_actor)`) that provides `Actor.user(id, username)`
of the logged-in user (401 without a valid token, like any core route). An extension sets
`actor` as `proposed_by` in the `ActionRequest` for `ctx.actions.propose()` and as `actor`
in `ctx.audit.log(..., actor=...)`. The audit log then shows the human behind the click,
with the extension only as `detail.via`.

**Original problem:** `ApiHandle.include_router()` (`ext/context.py`) injected only a
permission check (`Depends(require_permission(permission))`) for a route mounted with
`ctx.api.include_router(router, permission=...)`. `require_permission()` does resolve the
`CurrentUser` internally to perform the RBAC check, but returns it only as a
`dependencies=[...]` declaration, never to the route function itself. An extension route
therefore could not identify the calling user, unlike a core route
(`trigger_host_action()` in `api/v1/hosts.py` declares `user: CurrentUser` directly and
uses `Actor.user(user.id, user.username)`).

This was noticed in two places:

1. **nexus-soc (Nodvard Shield).** The `/confirm`/`/dismiss` endpoints of the incident
   widget changed `incident.status` without being able to record WHO clicked. (`/chat` was
   not affected: there, `Actor.ai(model=...)` correctly names the AI as the originator;
   the user only asked.)
2. **scripts.** `POST .../scripts/{id}/run` proposes a `script.run` action via
   `ctx.actions.propose()`, but had to specify `Actor.extension("scripts")` instead of the
   admin who clicked. The audit log could not show who triggered the run.

Host actions of the Proxmox extension were not affected: they go through the core route
`POST /hosts/{id}/actions/{type}`, which already knows `CurrentUser`.

**Implementation:** Initially deferred, because a change to the extension contract affects
all extensions and deserves a decision of its own. Implemented as an additional, optional
dependency: existing extensions keep running unchanged; without `actor`, the extension
itself remains the originator. Converted so far, among others, are scripts (manual run),
nexus-soc (confirm, dismiss, resolve, reopen), backups (create/edit job, retry) and
gameserver (start, stop, restart, back up world).
