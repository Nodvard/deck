# Architecture

> The Nodvard Deck interface is currently available in German only; an English interface is planned. UI labels are given in English with the German original in quotes.
>
> Note: The product is called **Nodvard Deck**, and `nodvard_deck` in code. It was formerly called "Lattice".
> Which old names still apply and which are intentionally kept is listed in [Old and new names](#old-and-new-names).

## 1. The three layers

```
┌──────────────────────────────────────────────────────────────┐
│  CLIENTS                                                     │
│  React web (served statically by the backend) │ Flutter Android (APK) │
└────────────────────┬─────────────────────────────────────────┘
                     │  one REST/WS API  (/api/v1, /ws)
┌────────────────────▼─────────────────────────────────────────┐
│  CORE: generic, renameable, knows no extension               │
│                                                              │
│  Auth/RBAC · Vault · Audit · Events · Scheduler · Gate       │
│  Execution (SSH) · Hosts · Settings/Branding · Ext-Host      │
└────────────────────┬─────────────────────────────────────────┘
                     │  nodvard_sdk  (Python contract)
                     │  window.__nodvardDeck + UI kit (React contract)
┌────────────────────▼─────────────────────────────────────────┐
│  EXTENSIONS: everything infrastructure-specific              │
│  proxmox · shield · terminal · files · scripts ·             │
│  docker · backups · gameserver · …                           │
└──────────────────────────────────────────────────────────────┘
```

**The one rule from which everything else follows:** dependencies only point upward.
An extension imports `nodvard_sdk`. The core **never** imports an extension and
doesn't know any extension name. The core only knows *capabilities* (protocols), never
*providers*.

This is machine-checkable, and it is checked: `scripts/check_core_purity.py` runs in CI
and fails if the code in `backend/src/nodvard_deck/` contains a word from a blocklist
(`proxmox`, `nexus`, `ollama`, `ntfy`, `nextcloud`, `truenas`, `syncthing`,
`teleport`, `docker`, plus `shield` as a standalone word) or the extensions' package prefix
(`nodvard_deck_ext_`). For `shield`, `asyncio.shield(…)` and hyphenated symbol names such as
`shield-check` don't count; each of these exceptions is documented at its occurrence in `backend/tests/test_core_purity.py`.
Comments and docstrings are exempt: what's forbidden is the *dependency*, not the
mention. A protocol may, and should, name examples in its documentation.

The check runs automatically because a rule alone isn't enough: without it, the
boundary erodes within a few weeks, because "just this one special case" always
sounds plausible.

**The same principle in the UI:** An extension's code under `extensions/<id>/frontend/src` only imports
itself and the shared UI kit (`extensions/_shared/frontend/src`), never core source code and never another
extension, with no exceptions (`backend/tests/test_extension_frontend_imports.py` checks this). What the core and
extensions share, such as the server picker and the schedule picker, lives in the kit; the core binds it via
`frontend/kit.resolve.ts` ([Extension interface, section 5](EXTENSION-API.md#5-pages-full-ui-views)).

---

## 2. Process model

**One process.** Extensions are Python packages loaded into the core process:
no sidecars, no subprocesses, no gRPC.

Rationale: the target hardware is a Raspberry Pi or a small x86 machine; one process per extension
would be the most expensive part of the system with eight extensions. Also: there is exactly one
administrator, who writes all extensions themselves or installs them deliberately.

**What has to be said openly:** an extension runs with the
full privileges of the core. The permission model (manifest permissions, see the
[Extension interface](EXTENSION-API.md)) is a *structure and traceability measure*, not a
sandbox. It prevents mistakes and makes accesses visible in the audit log, but it doesn't stop
malicious code. This is the same trust position as with WordPress plugins,
Home Assistant custom components, or Proxmox hooks. It must be named, not hidden.

To keep a later switch possible, the `ExtensionContext` is deliberately
designed so that every call is serializable (data in, data out, no
shared objects across the boundary). Anyone who later needs isolation swaps the
context implementation for an RPC proxy without changing any extension.

### Runtime in the process

| Part | Technology |
|---|---|
| HTTP/WS | FastAPI on uvicorn, **one** worker |
| Background loops (watchers) | `asyncio.Task`, managed by the extension host and cleanly cancelled on stop |
| Scheduled jobs | a single APScheduler object, SQLAlchemy job store |
| Blocking work | `asyncio.to_thread` / dedicated executor, never in the loop |
| SSH | one `asyncssh` connection pool, shared by Terminal, Scripts, and AI |

---

## 3. The lifecycle of a startup

```
1. Load config (.env → Settings)
2. Unlock vault (master key file or passphrase)
3. Connect DB, check Alembic migrations (core + one branch per extension)
4. Construct core services (Events, Audit, Scheduler, Gate, Execution, Notify)
5. Discover extensions → read manifests (without importing code!)
6. For each enabled extension: import, check version, check permissions,
   call setup(ctx)  → it registers routes/widgets/connectors/jobs/capabilities
7. Mount routers, freeze the widget catalog
8. Start scheduler
9. For each extension: on_start(ctx) → start background tasks
10. HTTP server ready
```

**Environment variables (step 1).** Every setting from `config.py` is a variable named
`NODVARD_DECK_<FIELD>` (e.g. `NODVARD_DECK_ENV`, `NODVARD_DECK_DATABASE_URL`). The former
names `LATTICE_<FIELD>` still apply as a fallback, but only for a transition: one of the next versions, by 1.0 at the
latest, will no longer read them. Order, first wins: arguments in
code, environment (new), environment (old), `.env` (new), `.env` (old), secret files. The process
environment therefore always beats `.env`, and the new name beats the old one. On startup,
a one-time warning in the log lists which old names are still in use (names only,
never the values).

Step 5 before step 6 is important: the manifest must be readable **without** running the
extension code. Only this way can a disabled or incompatible extension appear in
the registry UI without running, and only this way can a broken extension not
prevent startup.

**Failure behavior:** if an extension throws in `setup`/`on_start`, it is marked in the registry as
`state=error` with the exception and **skipped**. The core
starts anyway. A broken extension must never take down the dashboard. This is a
known failure mode of dashboards that embed modules via iframe (one iframe error =
one dead tab).

---

## 4. The action gate

Every state-changing action of an extension goes through **one** place in the core.
This isn't a feature of Nodvard Shield, but a core service that the
script repository, the game server module, and the file manager also use. It is deliberately
built as a generic building block.

```
Extension                Core Gate                        Result
   │                         │
   ├─ propose(ActionRequest)─▶│
   │                         ├─ 1. Extension permission?      → deny
   │                         ├─ 2. Actor's RBAC?              → deny
   │                         ├─ 3. Blocklist (patterns)?      → deny
   │                         ├─ 4. Anti-flapping / rate?      → deny
   │                         ├─ 5. Autonomy mode?
   │                         │     propose  → require_confirmation   (DEFAULT)
   │                         │     full     → allow, if risk ≤ threshold
   │                         ├─ 6. ALWAYS: write audit row
   │                         │            (incl. reason / JUSTIFICATION)
   │                         ▼
   │                   actions table: status = proposed | approved | denied
   │                         │
   │                         ├─ proposed → WS event → card in the UI → click
   │                         └─ approved → ActionExecutor runs → result + audit
```

The crucial point: **proposing and executing are separate steps with a
persistent record in between.** Where a single call both evaluates a remedy
*and* executes it, "confirm first" can't be cleanly added
later. Thanks to the separation, confirmation mode is the *natural* state and full
autonomy is the exception that requires a switch.

Side effect: the user gets no notification about an action that never
happened. The text in notifications and the UI is built from the `actions` record,
not from a model's intent.

**Background execution.** Once an action is approved (click, `full` mode, or standing approval),
the gate persists `executing` and starts the `ActionExecutor` as its own
`asyncio.Task` with its own database session (`core/gate.py`, `start_execution`). The
HTTP request waits at most 20 s for it and otherwise responds with `202 executing`; the
task itself writes the result along with the audit row. It always ends with `succeeded` or
`failed`, even if the executor throws, is still hanging after 60 min (`EXECUTE_TIMEOUT_S`,
an emergency brake above the extensions' limits), or the dashboard is shut down. Whatever a hard
crash leaves in `executing`, the core sets to `failed` on the next start.
`ctx.actions.propose()` waits until the end by default (background jobs); extension routes
pass `wait_s=REQUEST_WAIT_S` and may report `executing` afterwards.

**Standing approval.** A proposal can rely on an approval that a human granted in advance
(`ActionRequest.standing_approval`, [Extension interface, section 3](EXTENSION-API.md#3-capabilities--the-heart-of-the-decoupling); so far only
scheduled scripts). After the blocklist and anti-flapping, the gate itself checks on every proposal whether that person is still
active and is allowed to grant standing approvals and actions of this risk level; proposals from an AI never go through this path.
If it applies, it replaces the click, regardless of `autonomy.mode`, and the person is recorded as the approver of the action;
otherwise it becomes a normal proposal. The gate itself appends what happened to `reason`, with the date of the
approval in the dashboard's configured time zone.

**One exception: setup with password.** `POST /hosts/{id}/credentials/{cid}/setup-with-password` runs a
command as root on the server, but deliberately does not go through the gate: a proposal stores its inputs in
`actions.payload`, and the one-time password must not be stored anywhere. The route therefore requires
`hosts.write` and `hosts.execute`, the human triggers it by submitting, and every attempt is logged
(`host.setup_with_password`, without the password).

**Settings (global, overridable per extension):**

```
autonomy.mode          = "propose" | "full"        default: propose
autonomy.max_risk      = "low" | "medium" | "high" default: low
autonomy.window        = optional time window for "full"
```

---

## 5. Hosts belong to the core, not the extension

This is the second place where the extension boundary is easily drawn wrong.

The core owns the `hosts` table. Extensions **discover** hosts
(`HostProvider` capability) and write them there, with a back-reference
(`provider_ext_id`, `provider_ref`). A host can also be created manually.

This way, Terminal, script execution, the file manager, the audit log, and
permissions all address the **same** host identity, regardless of whether it came from Proxmox,
a Docker API, an inventory file, or a form. Without this
rule, every extension would have its own host list: one in the monitoring script, one for
the web links, one in the SSH access service, one in the tile dashboard. That makes four truths
that drift apart.

---

## 6. Branding as a configuration value

```
GET /api/v1/branding      (no auth, because the login page needs it)
→ { "product_name": "...", "short_name": "...", "logo_url": "...",
    "favicon_url": "...", "colors": { "accent": "#…", … },
    "login_subtitle": "...", "support_url": "..." }
```

The frontend writes the colors into CSS custom properties on `:root` at startup.
No rebuild, no restart, no environment variable. Logo and favicon are stored as uploads in the
data volume (`branding/`); `PUT /branding` accepts other addresses for them only as the app's own path or
`data:image/…`. The app page loads scripts only from its own server: `script-src 'self'` plus the checksum of the
embedded import map from `index.html` is fixed (`Content-Security-Policy`, also on the `304` response), so
no `javascript:` link, no `onclick=` from injected HTML, and no `eval` will run. The checksum is generated when
serving and recalculated as soon as `index.html` changes; a new embedded script therefore needs no
maintenance. Everything else likewise comes only from the app's own server (`default-src 'self'`, including
connections and WebSockets); images may additionally be `data:` and `blob:` (`img-src 'self' data: blob:`), frames
only `blob:` (the PDF preview in Documents, „Dokumente“). Styles need `'unsafe-inline'` because xterm (the console,
„Konsole“) creates `<style>` elements at runtime; images and fonts from foreign addresses are blocked all the same.
JSON responses without rules of their own get `default-src 'none'`. What this means for extension bundles is described in the
[Extension interface](EXTENSION-API.md#loading-the-frontend-bundle). Links from extension
data (app tiles, command palette, widgets, extension pages) are accepted by the UI only with `http:` or
`https:`; targets of notifications only as a path within the dashboard (`/…`, not `//…`).
An SVG is checked on upload (no scripts, no references out of the file), and every
uploaded image is served with a sandbox CSP;
the support link accepts only `http:`, `https:`, and `mailto:` ([API](../04-API.md), in German).

In code, the rule is: **no literal for product name or color** outside
`branding.py` (backend defaults) and the CSS variable definition (frontend). Also
`<title>`, email sender, push titles, and APK label draw from the same source.
The CI purity check covers this too. Deliberate exception: the login page always shows
"© 2026 Nico Benks · Nodvard Deck" (`copyrightLine()` in `frontend/src/lib/branding.ts`, with a
year range from 2027). Custom branding changes what the UI is called, not who owns the software.

---

## 7. Security position, summarized

| Area | Position |
|---|---|
| Extensions | Trusted code. Permissions = structure + audit, no sandbox. |
| AI actions | From AI text, Nodvard Shield creates at most one fixed action: restarting a container whose crash the incident reports. The code builds the command and justification itself, and the server comes from the incident; everything else stays text, and the chat never creates an action. Container logs and Docker error messages go to the AI as foreign data (not instructions). In addition: blocklist for catastrophic patterns, mandatory justification, anti-flapping, default = confirmation, never via a standing approval. |
| Secrets | Never in plaintext in the DB, a response, or a log. Extensions get handles; they replace and delete only their own, and never accounts' two-factor secrets. Access for another extension goes only through a capability (`ConnectionReceiver`), never over the event bus. |
| SSH | One identity from the vault, known-hosts pinning instead of `StrictHostKeyChecking=no`. |
| API | Everything authenticated except `/branding`, `/health`, `/auth/login`. Extension routes require login by default; without login only with `public=True` and the manifest permission `api.public` (logged when enabled). No endpoint gets to read more than 1 MiB of body, even without login (`413` above that; uploads have their own, higher limits). |
| Audit | Append-only, every action, including rejected and merely proposed ones. |
| Network | No outward-facing port needed (works behind DS-Lite/CGNAT too); access via reverse proxy on the LAN or a VPN such as Tailscale/WireGuard. |

**Deliberately not solved:** multi-tenancy, horizontal scaling,
extension sandboxing, SSO/OIDC. All four can be added later without breaking changes, and none of them
has a benefit in a single-administrator homelab that would justify the complexity.

## Old and new names

The project was formerly called "Lattice". The technical names have been switched to **Nodvard Deck**.
So that existing installations, old browser tabs, and third-party extensions keep working,
many old names remain valid **in parallel**; some are **deliberately** left entirely in place. The guard
`scripts/check_legacy_names.py` (runs as a test) ensures that no old name is newly
introduced. It has two allowlists, each with a justification per entry: `KEPT_NAMES` for "Lattice" and
`KEPT_SHIELD` for the old identifier of Nodvard Shield (there, each entry additionally names the files in which it
applies; a test checks that every entry is still needed). These two lists are authoritative. For the old identifier
of the Docker module, no list is needed: the check for old names only forbids the old package and the old addresses.

**Renamed, the old name keeps working (transition):**

| New | Old | How the old one lives on |
|---|---|---|
| Extension packages `nodvard_deck_ext_<id>` | `lattice_ext_<id>` | No alias for the built-in ones (the `entrypoint` comes fresh from `extension.toml`); third-party packages with the old name keep loading |
| Environment variables `NODVARD_DECK_*` | `LATTICE_*` | Fallback if the new one isn't set; only for a transition (by 1.0 at the latest), with a notice for the owner in the overview |
| Refresh cookie `nodvard_deck_refresh` | `lattice_refresh` | Both are written, read, and deleted on logout ([API](../04-API.md), in German) |
| Image, Compose service, containers `nodvard-deck`, `deploy-nodvard-deck-1` | `lattice`, `deploy-lattice-1` | Migrated on first deploy ([deploy/README](../../deploy/README.md)) |

**Removed with 0.12 (before the promise for 1.0):**

| New | Old | What happens now |
|---|---|---|
| Python package `nodvard_deck` | `lattice` | `import lattice` and `python -m lattice.…` no longer work. An image from before the rename, however, only knows the old name ([deploy/README](../../deploy/README.md), way back) |
| SDK `nodvard_sdk`, `NodvardExtension`, `NodvardError` | `lattice_sdk`, `LatticeExtension`, `LatticeError` | An extension that imports an old package or class name does not load. If it is enabled, the extension list gives the reason and the new name; otherwise the same reason appears when it is enabled (`ext/old_names.py`) |
| Entry-point group `nodvard_deck.extensions` | `lattice.extensions` | A package that registers only under the old group is not loaded. The log names the reason and, if the extension was enabled, so does its row in the list |
| Frontend: `window.__nodvardDeck`, events `nodvard-deck:*`, CSS `.nodvard-deck-focus`, import map `/nodvard-deck-shim/` | `window.__lattice`, `lattice:*`, `.lattice-focus`, `/lattice-shim/` | Bundles with an older UI kit that only knows the old names do not find the core shell ([Extension interface](EXTENSION-API.md)); an old tab needs one "Reload page" („Seite neu laden“) |
| Browser storage `nodvard-deck.*` | `lattice.*` | No longer carried over; the migration has run on every start since the rename |

**Deliberately kept:**

| Name | Why |
|---|---|
| Database file `lattice.db` (also `db/lattice.db` in backups) | Renaming would need a migration; after a rollback, the old image would open an empty database |
| Docker volume `deploy_lattice_data` | Otherwise the dashboard starts empty |
| Target folder `~/lattice-deploy-test` of `scripts/deploy_pi.sh` (default value of `DEPLOY_ROOT`) | Installations deployed this way depend on it; freely selectable via `DEPLOY_ROOT` |
| Linux user `lattice` (UID 1000) in the container | File permissions in the data folder |
| Default `lattice` for `username` in `POST /hosts/{id}/credentials/generate-key` (when generating, the UI suggests `nodvard` and always sends the name); on existing servers the user `lattice`, older keys with comment `lattice@<name>`, and old sudo files `/etc/sudoers.d/lattice-<user>` | Older API callers often send no name, so a different default would be a silent behavior change for them; existing servers are set up this way. New keys get `nodvard@<name>`, new sudo rules `/etc/sudoers.d/nodvard-<user>`; the setup command (with sudo) removes an old rule only if it is exactly the one Nodvard Deck created earlier |
| Names on managed servers (systemd units `lattice-upgrade-*`, rollback tags, state folders) | Resuming and rolling back updates depend on them |
| API value `source: "lattice"` | Documented value, clients evaluate it |

### Nodvard Shield: `nexus-soc` → `shield` (since 0.7)

The extension **Nodvard Shield** had a different name until 0.6 and the technical identifier `nexus-soc`. Since 0.7
it is called `shield`, and the manifest contains `legacy_ids = ["nexus-soc"]` (mechanics:
[Extension interface, "Renaming an extension"](EXTENSION-API.md#renaming-an-extension-legacy_ids)). There is **no migration**:
stored data is neither copied nor renamed, and a way back to the old image finds everything unchanged.

**Renamed, the old name keeps working (transition, throughout all 1.x):**

| New | Old | How the old one lives on |
|---|---|---|
| Identifier `shield` (`extension.toml`) | `nexus-soc` | `legacy_ids`. On existing installations, the registry row `nexus-soc` remains the **storage identifier**: settings, state, permissions, and schedules stay attached to it. New installations create `shield` |
| Folder `extensions/shield/`, package `nodvard_deck_ext_shield` | `extensions/nexus-soc/`, `nodvard_deck_ext_nexus_soc` | No alias (extensions don't import each other). If the old folder is still there, Shield is not loaded ([deploy/README](../../deploy/README.md)) |
| Addresses `/api/v1/ext/shield/…` | `/api/v1/ext/nexus-soc/…` | The same routes are additionally mounted under the old address, same login check, `deprecated` in the OpenAPI schema ([API, section 7](../04-API.md#7-kompatibilität), in German) |
| Core addresses `/api/v1/extensions/shield/…` (settings, secrets, test, bundle) | `/api/v1/extensions/nexus-soc/…` | The old identifier is resolved, responses give the new one in `id` |
| Pages `/ext/shield/soc`, `/settings/extensions/shield` | `/ext/nexus-soc/soc`, `/settings/extensions/nexus-soc` | The UI redirects (query and anchor are kept). Saved links in notifications, push, and bookmarks keep working this way |
| Dashboard tiles under `shield` | Entries under `nexus-soc` | Keep position, size, and visibility; the old entry stays in the layout for a way back |
| New log entries `shield.*` (e.g. `shield.incident`), actor `shield` | `nexus_soc.*`, `nexus-soc` | Old entries remain, the UI shows the name for both. The filter `GET /audit?action=shield.incident` does not find the older `nexus_soc.incident` |
| Logger `nodvard_deck.ext.shield`, live channels `ext.shield.*`, links in new notifications (`/ext/shield/soc`) | `…nexus-soc…` | Live messages additionally go to the old channels, so that open tabs with an old version receive them |

**Deliberately kept** (stored or on the servers; a way back depends on it, as with `lattice.db`):

| Name | Why |
|---|---|
| Registry row `nexus-soc` of existing installations | All settings live there; the old image finds them unchanged after a way back |
| Tables `ext_nexus_soc_*` and their indexes `ix_ext_nexus_soc_*` | Renaming would need a migration; the way back would then only work via the emergency page |
| The extension's Alembic revisions, their file names, and the branch `nexus-soc` (head `b8c9d0e1f2a3`) | Shipped revisions never change; only the folder moved with it |
| Action types `nexus_soc.{restore,delete,install,upgrade,reboot,ban}` | Open proposals and approvals carry them, and the gate finds the executor only via the type. The UI shows their name instead of the type |
| Secret `nexus-soc-ollama-key` and the permission `secrets.read:nexus-soc-*` | The key for Nodvard KI is found only via its label |
| Permissions `soc.read` and `soc.manage` | May appear in custom roles; the permission check has no alias |
| Folders on the servers: `/var/lib/nexus-quarantine` (quarantine), `/var/lib/nexus-updates` (running updates) | The paths are stored in saved findings, and running updates are resumed via the folder |
| Old entries in log, notifications, runs, and actions | History, not rewritten |

### Docker: `service-matrix` → `docker` (since 0.9)

The **Docker** module had a different name until 0.8 and the technical identifier `service-matrix`. Since 0.9 it is called
`docker`, and the manifest contains `legacy_ids = ["service-matrix"]`, just like Nodvard Shield. There is **no migration**
(the module has no tables); on existing installations, a way back to the old image finds everything unchanged
([deploy/README](../../deploy/README.md)).

**Renamed, the old name keeps working (transition, throughout all 1.x):**

| New | Old | How the old one lives on |
|---|---|---|
| Identifier `docker` (`extension.toml`) | `service-matrix` | `legacy_ids`. On existing installations, `service-matrix` remains the **storage identifier**: settings including custom tile addresses, the image-check schedule, and the data folder `data/ext/service-matrix/` (running image updates) stay attached to it. New installations create `docker` |
| Folder `extensions/docker/`, package `nodvard_deck_ext_docker` | `extensions/service-matrix/`, `nodvard_deck_ext_service_matrix` | No alias (extensions don't import each other) |
| Addresses `/api/v1/ext/docker/…` | `/api/v1/ext/service-matrix/…` | The same routes are additionally mounted under the old address, same login check, `deprecated` in the OpenAPI schema ([API, section 7](../04-API.md#7-kompatibilität), in German) |
| Core addresses `/api/v1/extensions/docker/…` | `/api/v1/extensions/service-matrix/…` | The old identifier is resolved, responses give the new one in `id` |
| Pages `/ext/docker/matrix`, `/settings/extensions/docker` | `/ext/service-matrix/matrix`, `/settings/extensions/service-matrix` | The UI redirects (query and anchor are kept). Saved links in notifications and bookmarks keep working this way |
| Dashboard tile "Container" under `docker` | Entry under `service-matrix` | Keeps position, size, and visibility (`legacy_ext_ids`) |
| Component `ContainersPage` in the bundle | `ServiceMatrixPage` | Second export, for tabs that are open during the update |
| `requires = ["docker"]` in other modules | `requires = ["service-matrix"]` | The core resolves the old identifier; the Network module still uses the old one |
| Logger `nodvard_deck.ext.docker`, links in new notifications (`/ext/docker/matrix`) | `…service-matrix…` | Old notifications keep their link, which keeps working |

**Deliberately kept:**

| Name | Why |
|---|---|
| Storage identifier `service-matrix` of existing installations, including the data folder | Settings and running updates live there; the old image finds them unchanged after a way back |
| Log names `service_matrix.tile_url` and `service_matrix.image_update` | Stored entries and filters depend on them; the UI shows a name for both |
| Action types `container.*` and `docker.*` | Open proposals and approvals carry them; they were never named after the module |
| Old entries in log, notifications, and actions | History, not rewritten |
