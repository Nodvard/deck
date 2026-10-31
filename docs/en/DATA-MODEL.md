# Core Data Model

> The Nodvard Deck interface is currently available in German only; an English interface is planned. UI labels are given in English with the German original in quotes.

SQLAlchemy 2.0, dialect-neutral (SQLite by default; Postgres is planned but untested, see
D-02 in [Decisions](DECISIONS.md)).

Conventions:
- PK: `id` as UUIDv7 (RFC 9562) in `String(36)` (time-sortable, dialect-neutral, readable in logs).
  IDs from versions up to 0.9.0 are 40 characters long (12 characters of time up front) and remain valid in SQLite.
- Timestamps: `DateTime(timezone=True)`, UTC, set in Python.
- Structured fields: `sa.JSON` (not `JSONB`).
- Lists: association table, never an array column.
- Soft delete only where the domain requires it (`revoked_at`, `disabled_at`).
- Extension tables: prefix `ext_<id>_`, own Alembic branch, not described here.

---

## 1. Identity and Access

### `users`
| Column | Type | Notes |
|---|---|---|
| id | str(36) PK | |
| username | str(64) unique | Login |
| email | str(255) null | optional, just an attribute of the account; Nodvard Deck does not send emails |
| display_name | str(128) | |
| password_hash | str(255) | Argon2id |
| totp_secret_id | FK secrets.id null | the 2FA secret lives in the vault, not here |
| is_active | bool | |
| is_owner | bool | exactly one; bypasses RBAC, cannot be revoked |
| locale | str(8) | `de` by default; UI language is a user preference |
| created_at, last_login_at | ts | |

### `roles`, `user_roles`, `role_permissions`
`roles(id, name unique, description, is_builtin)`.
Built in: `owner`, `admin`, `operator`, `viewer`.
Permissions are **strings**, not a table. Extensions bring their own, without a
migration:

```
<domain>.<action>[:<scope>]

hosts.read            hosts.write         hosts.execute
secrets.read:ssh-*    secrets.write
audit.read            settings.write      extensions.manage
system.read           apps.write
actions.approve       actions.approve:high
soc.read              soc.manage          (Nodvard Shield)
inventory.read        inventory.write     (Inventory)
```

`*` is allowed as a suffix wildcard in the scope (`secrets.read:ssh-*`); otherwise matching is exact.
Evaluation happens in `core/rbac.py`, pure functions, testable without a DB.

### `refresh_tokens`
| Column | Notes |
|---|---|
| id, user_id | |
| token_hash | SHA-256 of the opaque value; the value itself is never stored |
| client_type | `web` \| `android` \| `cli`, controls cookie vs. bearer |
| device_name, user_agent, ip | for "Active sessions" („Aktive Sitzungen“) in the settings |
| expires_at, revoked_at, last_used_at | |
| replaced_by_id | successor session, set only on rotation (not on logout/password change), without a foreign key. Basis of the 30-second grace period on refresh: a token that was just rotated still gets an access token for the successor session, but no new refresh token (two tabs, app + tab). It also carries the check of the `sid` in the access token (valid as long as a valid row sits at the end of the chain) and logging out of the whole chain (`logout` revokes the row at the end, even with an already renewed token, as long as that token's row still exists, that is until one refresh lifetime after the renewal, see "Cleanup"). The chain is read in a single recursive query (`WITH RECURSIVE`, `services.auth.session_chain_end`); limits are in [API, section 2](../04-API.md#2-ohne-authentifizierung), "Access token and login" („Access-Token und Anmeldung“), in German |

**Cleanup** (`services.auth.purge_old_logins`, core job `logins-retention`, nightly at 03:50): rows whose `expires_at`
or `revoked_at` lies more than `refresh_token_ttl_days` ago (default 30) are deleted. Until then, renewed and ended rows
stay too, because the chain via `replaced_by_id` needs them (grace period, `sid` of open terminal and live connections).
An access token points to a row only during its lifetime (default 15 minutes); a row only goes one refresh lifetime after
its end. Because the renewals of a chain end one after another, only its oldest part ever goes; from every remaining row
the chain leads on to the current login (logout, check of open logins). Without the job, every renewal, including address
and browser, would stay forever.

### `api_tokens`
For automation and the Android app in long-term operation.
`id, user_id, name, token_hash, scopes (JSON), expires_at, revoked_at, last_used_at`.
Even cron calls therefore need a token; there are no unprotected endpoints for automation.

---

## 2. Hosts

The core owns host identity, see
[Architecture, section 5](ARCHITECTURE.md#5-hosts-belong-to-the-core-not-the-extension).

### `hosts`
| Column | Notes |
|---|---|
| id | |
| name | str(64) unique, technical (`pve1`, `bastel-pi`) |
| display_name | str(128) |
| address | str(255), IP or DNS |
| os_family | `linux` \| `windows` \| `other` |
| kind | free-form, set by the extension (`vm`, `container`, `bare-metal`) |
| provider_ext_id | null for manually created hosts. This is also the marker for "no module maintains the state": only such hosts are checked by the core job `host-reachability` |
| provider_ref | foreign key at the provider (e.g. `node/qemu/110`) |
| is_managed | whether actions are allowed |
| enabled | |
| last_seen_at | last successful check (module, "Check connection" („Verbindung prüfen“), or `host-reachability`); empty = never seen, in which case there is no notification on the first outage |
| status | `up` \| `down` \| `unknown` \| `maintenance`. Manually created hosts are maintained by `host-reachability` (TCP to the SSH port, "down" only after 2 consecutive failures); the job does not touch `maintenance`. Imported hosts are maintained by their module via `ctx.hosts.set_status` (Proxmox: the only node of a connection that has not answered in any sync for at least 4 minutes in a row is set to `down`; nodes of a cluster or of a connection that only refuses, guests whose list could not be read, and hosts of disabled connections are set to `unknown`) |
| metadata | JSON, provider-specific. The exception is the key `login_ok`, which belongs to the core: the record of a successful SSH login with the default credential (`credential_id`, `address`, `port`, `at`). It yields `login_ok_at` in the API, as long as credential, address, and port still match. A provider's sync replaces the rest and preserves this key |
| created_at, updated_at | |

`host_tags(host_id, tag)`: tags are the generic replacement for hard-wired
special lists (such as "all Docker hosts" or "critical containers"). An extension asks for
"all hosts with tag `docker`" instead of maintaining its own list.

`host_groups(id, name, description)` + `host_group_members(group_id, host_id)`:
target groups for scripts (script repository, see [Extension interface, section 8](EXTENSION-API.md#8-how-three-modules-are-implemented-against-the-interface)).

### `host_credentials`
| Column | Notes |
|---|---|
| id, host_id | |
| kind | `ssh_key` \| `ssh_password` \| `api_token` |
| username | |
| port | |
| secret_id | FK `secrets.id`, the value lives exclusively in the vault |
| is_default | exactly one per host |

### `known_hosts`
`(host_id, key_type, fingerprint, first_seen_at, accepted_by_user_id)`:
replaces `StrictHostKeyChecking=no`. Mismatch ⇒ the connection visibly fails. An unknown key
is automatically remembered on first connect only if no confirmation is required (§9
`ssh.confirm_new_host_keys`, `ssh.host_key_confirm_required`); otherwise only via `POST /hosts/{id}/known-hosts`.

---

## 3. Secrets Vault

### `secrets`
| Column | Notes |
|---|---|
| id | |
| label | str(128) unique, the name extensions reference |
| kind | `password` \| `ssh_private_key` \| `api_token` \| `totp` \| `generic` |
| ciphertext | LargeBinary, Fernet |
| key_version | int, enables rotation without a big bang |
| owner_ext_id | null = belongs to the core/admin |
| description | |
| created_by_user_id, created_at, rotated_at, last_used_at | |
| expires_at | expiry date, informational only: nothing is blocked afterwards. The core job `secret-expiry-reminder` reminds about it |
| metadata | JSON, **never sensitive** (e.g. public key fingerprint). Under `expiry_notice` `{for, stage}` it records which reminder has already been sent for which expiry date |

`secret_grants(secret_id, grantee_type ext|role, grantee_id)`: who may read.

`secret_usages(secret_id, consumer_type, consumer_id, first_used_at, last_used_at)`: one row per consumer, written
by `vault_use`. Fixed bindings (owner module, host credential, two-factor) are not stored there; they are derived on read
(`used_by` in `GET /secrets`, with only the fixed bindings additionally in `bound`).

**Invariants enforced in code:**
1. No API endpoint returns `ciphertext` or plaintext. Ever. Not even for `owner`.
   Writing and replacing yes, reading no.
2. Extensions receive a `SecretHandle`; the plaintext exists only inside an
   `async with ctx.vault_use(handle) as value:` block.
3. Every materialization is recorded (label and consumer, without the value), in aggregated form: at most one audit row
   `secret.used` per hour, secret, and consumer. When a consumer last used a secret is
   additionally recorded in `secret_usages` and in `secrets.last_used_at`.
4. The master key never lives in the DB.
5. `PUT /secrets/{id}/value` and `DELETE /secrets/{id}` reject the secret behind `users.totp_secret_id`
   (an account's two-factor key) with `409`: it cannot be replaced or deleted there. It can only be disabled via
   My Account („Mein Konto“), "Reset two-factor" („Zwei-Faktor zurücksetzen“), or the emergency command.

---

## 4. Audit Log

### `audit_log`, append-only
| Column | Notes |
|---|---|
| id | |
| ts | indexed |
| actor_type | `user` \| `extension` \| `ai` \| `scheduler` \| `system` \| `anonymous` (not logged in) |
| actor_id | user ID, extension ID, or model name; for `login.failed`/`login.locked` with an unknown username (an overly long input counts the same) `unbekannt` ("unknown") with `actor_type` `anonymous`, never the typed text |
| action | `host.restart`, `secret.used`, `action.denied`, `login.failed` … |
| target_type, target_id | |
| outcome | `success` \| `failure` \| `denied` \| `proposed` |
| reason | **Text**, this is where `BEGRUENDUNG:` ends up |
| detail | JSON: command, exit code, gate decision; for `action.executed`, only the length of output and error text, never text from the server (older entries may still contain output); for `login.failed`/`login.locked` with an unknown username, instead of the text only `username_ref` (12 hex characters, HMAC-SHA256 of the input with the installation's JWT secret; same input, same identifier) and `username_length` |
| correlation_id | links incident → proposal → execution → notification |
| ip, user_agent | |

No `UPDATE`, no `DELETE` from the application; only a retention job deletes beyond
`audit.retention_days`. The separation of `outcome=proposed` from `success` is the difference
between "the AI wanted to" and "the AI did". Without it, it is impossible to tell afterwards
what was actually executed.

Values that are too long in `actor_type`, `actor_id`, `action`, `target_type`, `target_id`, `ip`, and
`user_agent` are truncated to the column length on write (last character "…");
`reason` and `detail` are not truncated.

Older versions wrote the typed text or an identifier `unbekannt:…` as `actor_id` to the log on a login with an unknown name,
and for `login.locked` also into `detail.username`.
The migration `f3a9c6d18e24` cleans up these entries once: actor `anonymous`/`unbekannt`,
`detail.username` is dropped (under SQLite with `PRAGMA secure_delete`). A `login.locked` for an
account that has since been deleted is treated the same way. This cannot be undone.
The copies under `backups/vor-update/` and older backups still contain the names until they are
replaced or deleted.

---

## 5. Actions: the Gate Journal

### `actions`
| Column | Notes |
|---|---|
| id | |
| ext_id | who proposes |
| action_type | `shell.exec`, `docker.restart`, `vm.start`, `script.run`, `file.delete` |
| host_id | null for actions not bound to a host |
| payload | JSON |
| risk | `low` \| `medium` \| `high` \| `critical` |
| status | `proposed` → `approved` → `executing` → `succeeded`/`failed`, or `denied`/`expired`/`dismissed` |
| proposed_by_type / _id | `ai` + model, `user`, `scheduler`, `event` |
| reason | mandatory field, NOT NULL |
| gate_decision | JSON: which rule applied, which patterns matched |
| approved_by_user_id, approved_at | |
| executed_at, finished_at | |
| result | JSON: exit_code, stdout/stderr (truncated), duration |
| correlation_id | |
| idempotency_key | prevents double execution on retry/double click |
| expires_at | unconfirmed proposals expire (default 24 h) |

This table separates proposing from executing (see
[Architecture, section 4](ARCHITECTURE.md#4-the-action-gate)). The confirmation card in the
UI is a row with `status=proposed`; "full autonomy" means the gate sets it directly to
`approved`: the same record, the same traceability, just without a click.
The same applies to a valid standing approval ([Architecture, section 4](ARCHITECTURE.md#4-the-action-gate)): `gate_decision` is then
`{"rule": "standing_approval", "standing_approval": {granted_by, granted_at, label}}`, and `approved_by_user_id`
is the person who granted it. If the gate does not accept a standing approval, it appears with `problem` (and, if applicable,
the missing `permission`) under `gate_decision.standing_approval_rejected`.

`action_flap_history(host_id, fingerprint, ts, blocked)`: generalized anti-flapping
based on a hash of (host, action_type, normalized payload), not just for
Docker restarts. Instead of fixed thresholds in a single watcher, it applies
to **all** extensions, because it lives in the core.

---

## 6. Extension Registry

### `extensions`
| Column | Notes |
|---|---|
| id | = manifest ID, PK. After a rename (`legacy_ids`, [Extension interface, section 1](EXTENSION-API.md#renaming-an-extension-legacy_ids)) the row stays under the old identifier (**storage ID**), e.g. `nexus-soc` for Nodvard Shield on installations from before 0.7; new installations create the row with the new identifier |
| version, api_version | |
| state | `enabled` \| `disabled` \| `error` \| `incompatible` \| `uninstalling` |
| source | `bundled` \| `local` \| `pip` \| `marketplace` |
| manifest | JSON, as read in |
| granted_permissions | JSON, what the admin confirmed; may be less than requested |
| settings | JSON, validated against the declared schema |
| last_error, last_error_at | |
| installed_at, updated_at | |

### `connector_instances`
`id, ext_id, type_id, name, config (JSON, secrets as a `{"$secret": "<label>"}` reference),
enabled, health_status, health_message, last_check_at`.

---

## 7. Schedule and Runs

### `jobs`
`id, ext_id (null = core, otherwise the extension's storage ID), name, kind, schedule (cron), timezone, target (JSON: host/group),
params (JSON), enabled, next_run_at, created_by_user_id`

`schedule` is a standard 5-field crontab (minute hour day month weekday) in the job's time zone.
In the weekday field, **0 and 7 are both Sunday**, 1 = Monday … 6 = Saturday, as in the schedule picker.
The translation to APScheduler's own numbering (0 = Monday) is done by `nodvard_deck.core.cron.cron_trigger`;
the same applies to the cron expressions of maintenance windows.

**One** table for all scheduled work: Nodvard Shield audits and scripts alike.
The "Fleet-wide schedule" („Fleet-weiter Zeitplan“) view is a `SELECT`. A job accidentally created twice
shows up in it as a duplicate row.

### `job_runs`
`id, job_id (null for ad hoc), ext_id, trigger (manual|schedule|ai|event), status
(running|succeeded|failed|interrupted|skipped), started_at, finished_at, exit_code,
host_id, output_ref, error, actor, correlation_id`

`output_ref` points to a file under `/data/runs/<id>.log`, not into the DB:
script output is unbounded in size, DB rows should not be.
`interrupted` exists so that a restart in the middle of a run becomes visible instead of
disappearing; likewise, a run that shutdown aborts or never starts at all ends this way.

**Cleanup** (`services/job_retention.py`, core job `job-runs-retention`, nightly at 03:20): finished runs
(`status` ≠ `running`) that started more than `jobs.run_retention_days` days ago (default 30, 1–3650) are deleted along with
their log file. For each `job_id`, the 20 newest finished runs always remain; runs with an empty `job_id`
(job deleted, ad hoc) are removed by age only; running ones never. Deletion happens in chunks of 5000 rows (commit in between,
so SQLite does not lock for long); files only after the commit and only within `<data folder>/runs`.
In addition, `*.log` files there that have no corresponding row and are older than the retention period are removed.
`host-reachability` still cleans up its own runs after 24 hours (`services.jobs.prune_runs`).
The database runs in WAL mode with `auto_vacuum=NONE`; SQLite refills free pages with new rows, and the
file size levels off. A `VACUUM` (rewrites everything, holds the write lock, needs up to twice the
space) and `incremental_vacuum` (only effective after such a switch) deliberately do not run during operation.

---

## 8. Notifications

`notifications(id, ts, severity (info|warning|critical), title, body, source_ext_id,
correlation_id, read_at, payload)`: the history in the dashboard
("Notification Center", „Notification-Center“).

Delivery channels are **connector instances** of an extension that fulfills `NotificationChannel`,
not a separate table. `notification_deliveries(notification_id, channel_id,
status, error, sent_at)` records whether delivery succeeded. Without this
feedback, a channel that reports into the void may go unnoticed for weeks.

**Cleanup** (`services/notification_retention.py`, core job `notifications-retention`, nightly at 03:30): notifications whose
`ts` is older than `notifications.retention_days` days (default 90, 1–3650) are deleted along with their delivery logs,
read or unread. Only an **unread critical** notification (`severity` `critical`, `read_at` empty)
stays until someone reads it. Deletion runs in chunks of 5000 rows with a commit in between. The delivery logs
explicitly go with them (not just via `ON DELETE CASCADE`, because `PRAGMA foreign_keys` is per connection). Manual
deletion works via `DELETE /notifications/{id}` and `POST /notifications/delete-read` ([04-API.md](../04-API.md), in German); a delivery
that is currently in progress writes its log only as long as the notification still exists (`services.notifications._log_deliveries`).

---

## 9. Settings and Branding

`settings(key, scope (global|user|host), user_id, value JSON, updated_at, updated_by_user_id)`

Reserved keys:

```
branding.product_name      branding.short_name     branding.logo_url
branding.favicon_url       branding.colors         branding.login_subtitle
autonomy.mode              autonomy.max_risk       autonomy.window
audit.retention_days       maintenance.windows     security.deny_patterns
jobs.run_retention_days    notifications.retention_days
locale.default             notifications.defaults     system.timezone
backup.config              backup.key              backup.last_run
system.instance_id         hosts.reachability.enabled
hosts.reachability.interval_minutes                hosts.reachability.state
ssh.confirm_new_host_keys  ssh.host_key_confirm_required
```

Backups (`services/backups.py`, only via `/system/backups/...`, not via `/settings`; only the owner may change them):

| Key | Content |
|---|---|
| `backup.config` | `{enabled, schedule, keep, dir, include_runs}`; `dir = null` means `<data folder>/backups` |
| `backup.key` | `{recipient ("age1…"), key_id, kdf {alg: "argon2id", t, m_kib, p, salt}, created_at}`, with **no** password and **no** private key. The key is re-derived from the password + `kdf` when needed |
| `backup.last_run` | `{at, ok, trigger, name, size, error, deleted[]}` |
| `system.instance_id` | random identifier of this installation (included in the manifest of every backup) |

`system.read` (Settings → View System, „Einstellungen → System ansehen“) is held only by `admin` via `*`; anything critical there requires the owner
themselves, see [04-API.md](../04-API.md) (in German), "System and backups" („System und Sicherungen“).

`system.timezone` (IANA name, default from `NODVARD_DECK_TIMEZONE`/`TZ`, otherwise Europe/Berlin) and
`audit.retention_days` (7–3650, falls back to the environment variable) and `jobs.run_retention_days` (1–3650, default 30;
retention of job runs, see §7 `job_runs`) and `notifications.retention_days` (1–3650, default 90; retention of
notifications, see §8) can be set via `PUT /settings/{key}`, see
[04-API.md](../04-API.md), section 3, "Settings" („Einstellungen“), in German.

Reachability check (`services/reachability.py`): `hosts.reachability.enabled` (default `true`) and
`hosts.reachability.interval_minutes` (1–60, default 2) can be set via `PUT /settings/{key}`. `hosts.reachability.state`
is an internal marker `{host_id: {"muted": bool}}` for hosts whose outage has already been reported (`muted` = during a
maintenance window only in the history, caught up once audibly afterwards); it does not appear in `GET /settings`.
The core job `host-reachability` is stored in `jobs` like the other core jobs (`ext_id` null); its runs in `job_runs`
are kept for only 24 hours, because it runs up to 1440 times a day.

Host keys (`services/hosts.py`): `ssh.confirm_new_host_keys` (global, default `false`) determines whether new
host keys are remembered only after confirmation; creating the first account stores `true`, installations
from before do not have the value. `NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS` overrides it (see [04-API.md](../04-API.md), section 3,
"Settings", in German). `ssh.host_key_confirm_required` is an internal marker per host (`scope = 'host'`, `user_id` =
host ID): set when forgetting a remembered key (`DELETE /hosts/{id}/known-hosts/{key_type}`, in the same
commit), removed only on confirmation (`POST /hosts/{id}/known-hosts`) and together with the host. As long as it exists, no
connection to this host silently remembers a key, not even against the environment variable; it does not appear in
`GET /settings`.

`maintenance.windows` is a list of (cron expression, duration, affected hosts, what is
suppressed) instead of a hard-wired window. Important: suppression applies
**symmetrically** to offline *and* online notifications. Otherwise a silently
reported outage is followed by an audible all-clear with no backstory.

Which notification a window captures is determined by the notification's payload: `host_id` (one host)
or `host_ids` (collective notification about several hosts, silent only if **all** lie within a
running window). A notification with neither is never suppressed.

`security.deny_patterns` is the blocklist of the action gate
([Architecture, section 4](ARCHITECTURE.md#4-the-action-gate)) as configuration rather than as a
constant: the core ships a set of defaults, extensions may add to them,
nobody may empty them.

### `dashboard_layouts`
`id, user_id, name, is_default, items (JSON: [{widget_id, ext_id, x, y, w, h, config}]), groups (JSON, null)`:
identical for web and app, so the arrangement on the phone doesn't have to be reinvented. `config.group` places a widget
in a group other than that of its module. `config.size` (`s`, `m`, or `l`, in the UI small, medium, large:
2, 3, or 5 grid rows high) sets a fixed height; without it, the height follows the content. The
UI stores `x + w` at most 12. `groups` holds the order, custom names, and collapsing of the groups (`[{key, title, collapsed}]`);
empty means default.

### `custom_apps`
Custom app tiles in the cockpit ("+ Add app", „+ App hinzufügen“): manually created links for everything that
container discovery doesn't notice (router, NAS interface, Pi-hole …).

| Column | Notes |
|---|---|
| id | |
| name | str(60), trimmed; without control characters, line/paragraph separators, and special whitespace, with at least one visible character |
| url | str(1000), only `http://`/`https://`, without user/password and without whitespace/control characters |
| icon | str(32) null: name from the fixed lucide selection (`services/custom_apps.APP_ICONS`) or a single emoji, never an image URL |
| color | str(7) null: `#rrggbb`; empty = color derived from the name |
| group_name | str(40) null: group for the filter in the cockpit (`group` in the API), same character rules as `name` |
| sort_order | int, indexed: position (new apps go to the end; `PUT /apps/order` renumbers) |
| open_in_new_tab | bool, default `true` |
| host_id | FK hosts.id null, `ON DELETE SET NULL`: optional host reference, for display only |
| created_by_user_id | str(36) null, without a foreign key (like `jobs`) |
| created_at, updated_at | ts |

The server **never fetches** the address itself (no status polling → no SSRF); it is only a link in the browser.
At most 200 apps (creating more returns `409`; the sample data respects the limit, and when reordering, the list may be slightly longer in case two creations went through simultaneously). Alembic revision `d4b7a2c91e63` in the core branch (downgrade drops the table and indexes).
The permission `apps.write` is additive (purely a new string, no migration): administrator and owner
have it, operator and viewer do not by default; reading requires `hosts.read`, like the discovered apps.
