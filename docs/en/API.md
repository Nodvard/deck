# API: REST + WebSocket

> This is the English version of docs/04-API.md; the German file is the reference.

One API for web and Android. The web interface gets **no** special path: if a feature works only on the web, that is a bug in the API, not a property of the app.

Base: `/api/v1`. `GET /api/v1/system/openapi.json` serves the OpenAPI document to signed-in accounts with the `system.read` permission; the Flutter models are generated from it, not written by hand. Without signing in, the API docs (`/docs`, `/redoc`, `/openapi.json`) are available only in development mode (`NODVARD_DECK_ENV=dev`) or with `NODVARD_DECK_API_DOCS=1`; otherwise those addresses answer 404, so an installation doesn't show every visitor all endpoints and their fields.

---

## 1. Conventions

| Topic | Rule |
|---|---|
| Auth | `Authorization: Bearer <access_token>`, identical for web and app |
| Errors | JSON `{"detail": …}`: usually a German sentence; for `422` from input validation, a list `[{type, loc, msg}]` |
| Pagination | Cursor: `?limit=50&cursor=…` → `{items, next_cursor}`. No offset. |
| Filters | explicit query parameters, no generic filter language |
| Time | ISO 8601 with `Z`. Always UTC. Local time is up to the client. |
| IDs | UUIDv7-like strings |
| Idempotency | `Idempotency-Key` header on all execution endpoints |
| Correlation | `X-Request-Id` in, returned in the response and the audit log |
| Partial responses | `?fields=` is **not** supported; saving mobile data is done with narrow, purpose-built endpoints |

If input validation rejects a request (`422`), the response names only `type`, `loc`, and `msg` per field, never the input itself. `msg` is a German sentence even for the common standard checks (required value missing, too short or too long, number too small or too large, wrong type for numbers, text, or yes/no, value not allowed, not valid JSON, unknown field), for example „Mindestens 8 Zeichen.“ (At least 8 characters.); rarer checks (such as a list instead of an object) keep pydantic's English text. Custom checks (`type` `value_error`) return their own sentence, which often already names the field („Benutzername: …“ (Username: …)). If an endpoint raises an SSH error (server unreachable, login rejected, server key not confirmed) or `HostUnreachable` (also from an extension) and doesn't catch it itself, the response is `502` with the reason in `detail` instead of `500`, with no traceback in the container log. This includes a command that would need more than 8000 bytes on the command line of a Linux server (with its own input, as a live stream, or in a console): it never goes out, and `detail` names the length and the reason („Der Befehl ist mit … Byte zu lang für eine SSH-Befehlszeile …“ (The command is … bytes too long for an SSH command line …)). Longer commands without their own input arrive via standard input. Any other network error (such as one from an HTTP connection) that the endpoint doesn't catch stays `500`.

By default, the body of a request is limited to 1 MiB (`NODVARD_DECK_MAX_BODY_BYTES`), even without signing in. If `Content-Length` is larger, the server answers `413` (plus `Connection: close`) as soon as the endpoint reads the body, and reads none of it; an endpoint that doesn't read it at all (such as an upload whose login or permission already fails) answers as usual. Without a length (`Transfer-Encoding: chunked`), the server counts along and aborts with `413` when the limit is exceeded. `detail` names the allowed size („Die Anfrage ist zu groß (erlaubt sind höchstens 1 MB).“ (The request is too large (at most 1 MB allowed).)). Uploads have their own, higher limits: uploading a backup 4 GiB (`NODVARD_DECK_RESTORE_MAX_UPLOAD_BYTES`), logo 2 MB, documents (extension `documents`) 20 MB, images in the inventory (extension `inventory`) 5 MB; `POST /files/{source}/upload` is unlimited by default (`NODVARD_DECK_FILES_MAX_UPLOAD_BYTES`, see „Dateien“ (Files)). Extensions declare a higher limit with `max_body_bytes` from the SDK ([Extension interface, section 2](EXTENSION-API.md#routes-and-login)).

Every response, including one from the emergency page, carries fixed security headers that a route cannot loosen: `X-Frame-Options: DENY` and `frame-ancestors 'none'` in the CSP (for JSON without its own CSP, also `default-src 'none'`), `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Cross-Origin-Opener-Policy: same-origin` (takes effect only over https or localhost), `Permissions-Policy` with blocked devices (camera, microphone, location, motion sensors, USB, serial, HID, Bluetooth, MIDI, screen capture, payment; fullscreen and clipboard stay at the browser default), and `Cross-Origin-Resource-Policy: same-origin`. Only the public icons (`/branding/logo`, `/branding/favicon`, `/app/icon-<size>.png`) and `/health` carry `cross-origin`, so that a start page such as Homarr can show Deck icons and ping Deck.

---

## 2. Without authentication

```
GET  /api/v1/health              → {status, version, uptime_s}
                                   (emergency page instead of the application: 503 {"status":"rescue"}, never "ok", see „Kopie vor jeder Migration“ (Copy before every migration))
GET  /api/v1/branding            → name, logo, colors (the login page needs it)
GET  /api/v1/branding/logo       → the uploaded logo (404 if there is none). Answers with
                                   `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; sandbox`
                                   and `X-Content-Type-Options: nosniff`: whoever opens the address directly gets no
                                   script executed; embedded as `<img>`, nothing changes.
GET  /api/v1/branding/favicon    → the uploaded favicon (404 if there is none), with the same headers as
                                   the logo; `image/png`, `image/svg+xml`, or `image/x-icon`.
GET  /api/v1/branding/custom.css → the admins' own CSS as `text/css` (empty if there is none), with
                                   `Cache-Control: no-cache`, `nosniff`, and `ETag`; with a matching `If-None-Match`
                                   it returns 304 without content. Public, because the login page carries it too. A
                                   stored value that fails the check is not served (empty
                                   response); each value is checked only once.
POST /api/v1/auth/login          → {username, password}
                                   → 200 {access_token, expires_in, refresh_token?, user}
                                   → 202 {mfa_required: true, mfa_token}
                                   → 401 „Ungültiger Benutzername oder Passwort.“ (Invalid username or password.), also for a
                                   deactivated account and for overlong input (name over 64, password over 1024
                                   characters). The response time doesn't reveal whether the account exists or whether it is
                                   deactivated. The log (`login.failed`, `login.locked`) doesn't record an unknown name
                                   (overlong input counts the same), but the entries exist: the actor is
                                   `anonymous`/`unbekannt`, with only a reference and the length of the name (see `/audit`).
                                   → 503 with `Retry-After: 5`: too many password checks are running right now (at most
                                   2 at once, 16 waiting); doesn't count as a failed attempt. If the account is deactivated or its
                                   password changed during the check: 401, as for a wrong password.
                                   → 429 with `Retry-After`, checked before the password and without the database: 10
                                   failed attempts per address and name, or 30 per address (IPv6 per /64) in 5 minutes.
                                   On top of that, the per-name brake: from 10 failed attempts for one name in 24 hours,
                                   from all foreign addresses combined, each attempt from a network (IPv4 per
                                   address, IPv6 per /64) waits twice as long as the previous one from the same network (2 s,
                                   4 s, …, at most 5 minutes); after 24 hours without an attempt, a network starts
                                   over, and a successful login resets it (with two-factor, once the
                                   code is also correct). From a network where nobody is guessing, the first attempt goes through immediately;
                                   this way nobody can lock someone else's account out. Whoever shares the network with the attacker
                                   waits there like the attacker. Whoever uses a new address for every attempt is hardly
                                   slowed down. That doesn't take a botnet: a personal IPv6 prefix, as many
                                   providers hand out, contains many /64 networks (a /56 has 256, a /48 has 65,536). A long password and
                                   two-factor help against this. Local networks (127.0.0.0/8, 10.0.0.0/8,
                                   172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, 100.64.0.0/10, ::1, fc00::/7,
                                   fe80::/10) and requests without a recognizable address (Unix socket) are not slowed down by it.
                                   Unknown names are slowed down the same way; the names share 4096 slots.
                                   When the brake starts, `login.locked` appears in the log (`scopes` contains `name`); for
                                   an existing account, the notification „Passwort wird durchprobiert“ (Password is being guessed) follows, after the response.
                                   All counters live in memory and start over after a restart.
                                   Behind a reverse proxy with no entry under „Vertrauenswürdiger Proxy“ (Trusted proxy), the
                                   server sees only its address; if that is local, the brake doesn't apply, and the per-address limits
                                   apply to everyone together.
POST /api/v1/auth/mfa            → {mfa_token, code} → like 200 above
                                   `code` = 6-digit authenticator code OR a recovery code
                                   (`ABCDE-FGHJK`, spelling doesn't matter); the latter is used up in the process.
                                   Wrong codes count against the limit of 5 per `mfa_token`. An `mfa_token` is valid
                                   for one login only (afterwards 401), an authenticator code only once per account
                                   (repeated: 401 „Dieser Code wurde schon benutzt. …“ (This code was already used. …)). Wrong 6-digit
                                   codes also count per account, regardless of IP and together with the
                                   wrong codes at confirmations (change password, turn off two-factor,
                                   new recovery codes, load and restore a backup, update and rollback;
                                   see §3 and „System and backups“): after 10 in 15 min or 20 in 24 h, 429 with `Retry-After` (even
                                   with the correct code), `login.locked` (if the lock starts at a confirmation:
                                   `auth.password_check_locked`) in the
                                   log, and the notification „Zwei-Faktor-Code wird durchprobiert“ (Two-factor code is being guessed). This lock doesn't
                                   apply to recovery codes. With a recovery code, also 503 as for `/auth/login`; the code is then not used up, and the attempt
                                   doesn't count.
                                   The per-account count survives a restart: on startup, Nodvard Deck re-enters the
                                   wrong codes of the last 24 hours from the log
                                   (`mfa.failed`, `auth.totp_check_failed`, without `via: recovery_code`; timestamps
                                   up to 24 hours in the future count as "now", later ones not at all). The locks
                                   per address and name and per `mfa_token` start over after a restart.
GET  /api/v1/auth/bootstrap      → {needed}: is there no user yet? As long as none exists, there is
                                   additionally `restore: {ok: false, message, at}` if a restore
                                   from the wizard did NOT work (the wizard then explains why).
POST /api/v1/auth/bootstrap      → {username, password, setup_code} → 201 {id, username, is_owner}
                                   First setup: creates the owner. The `setup_code` is in the
                                   container log (or `NODVARD_DECK_SETUP_CODE`). Missing/wrong: 403;
                                   too many failed attempts per IP (IPv6 per /64 network): 429; a user already exists: 409;
                                   too many password checks at once: 503 as for `/auth/login`.
                                   After success, the code is deleted.
                                   `username` is trimmed and lowercased, has 3 to 64 characters,
                                   starts with a letter or digit, and contains only `a-z`, `0-9`, `.`, `-`,
                                   and `_`; `password` 8 to 255 characters. Otherwise 422 with a German message
                                   („Benutzername: Nur Kleinbuchstaben, Ziffern sowie . - und _ erlaubt …“ (Username: Only lowercase letters, digits, and . - _ allowed …)).
PUT/POST/DELETE /api/v1/auth/bootstrap/restore/…, POST /api/v1/auth/bootstrap/restart
                                 → restore a backup in the wizard, see „Restore“ under §3
                                   „System and backups“. ONLY as long as there is no account (otherwise 409) and
                                   ONLY with the setup code in the `X-Setup-Code` header, on EVERY call,
                                   checked **before** the body is read, throttled like `POST /auth/bootstrap`
                                   (403 wrong/missing, 429 too often; same counter per IP).
POST /api/v1/auth/refresh        → new access token (+ rotated refresh token; within the 30 s after
                                   a rotation, the same old token returns only an access token,
                                   without a cookie and without `refresh_token` in the body)
POST /api/v1/auth/logout         → revoke the refresh token (on web: the tokens of both cookies) and delete both cookies
GET  /api/v1/capabilities        → what this server can do
```

Extension routes (`/api/v1/ext/<id>/…`) are not included: they require signing in, unless an extension explicitly offers them without it with the permission `api.public` ([Extension interface, section 2](EXTENSION-API.md#routes-and-login)).

Web receives the refresh token as an `HttpOnly; Secure; SameSite=Strict` cookie and **not** in the body. Android receives it in the body and stores it in `flutter_secure_storage`. The access-token path is identical for both; that is the core of the "one API" promise.

**Access token and session.** Every access token carries in the `sid` claim the session (refresh-token row) it belongs to. On every request, the server checks whether that session is still valid; it follows a renewal (`replaced_by_id`), so an older token from another tab stays valid. If the session has ended (logout, „überall abmelden“ (sign out everywhere), expired), the result is 401, even if the token itself hasn't expired yet; the client gets a new one via `POST /auth/refresh` as usual or signs in again. A token without `sid` (from older versions) is valid as long as the account has any valid session, and one with the `sid` of another account never is. `POST /auth/logout` ends the session at the end of the chain, even with an already renewed refresh token, as long as its row still exists: the nightly cleanup deletes it one refresh lifetime after the renewal. The access-token check follows at most 50 renewals (beyond that 401), logout and signing out the other sessions (password change, turning off 2FA) at most 10,000; if the chain is even longer, all sessions of the account end for safety, including the current one. The chain is read in a single database query each time.

**Name of the refresh cookie (transition period of the renaming).** The cookie is called `nodvard_deck_refresh` (formerly `lattice_refresh`), path `/api/v1/auth`. As long as a rollback to the old image should remain possible, the following applies to web clients (the browser doesn't have to do anything; the cookie is `HttpOnly` and read only by the server):

- **Set** (login, 2FA completion, refresh with a new token): both cookies, with the same value and the same attributes (`HttpOnly`, `SameSite=Strict`, `Path`, `Max-Age`, `Secure` as before). During the grace period (see above) there is no new token: then there is **no** `Set-Cookie`, and nothing is deleted either.
- **Read** (`POST /auth/refresh`): first `nodvard_deck_refresh`, then `lattice_refresh`; empty and duplicate values are dropped, and the first value that is valid wins. A failed attempt changes nothing in the database and doesn't revoke a session chain (there is no reuse detection with revocation of the chain): a stale value in one of the two cookies, for example after a rollback, therefore doesn't sign anyone out. A `refresh_token` in the body (Android/CLI) still applies on its own; the cookies are then not read.
- **Logout** (`POST /auth/logout`): the tokens of all cookies are revoked (identical values only once), and both cookies are deleted, with the same attributes as when setting. A `refresh_token` in the body applies on its own here too.

`lattice_refresh` is dropped in a later cleanup step, at the earliest 30 days (lifetime of the refresh token) after the deploy with both cookies and when no rollback to an older image is needed any more. For Android/CLI (token in the body), nothing changes.

`capabilities` lists enabled extensions, available view types, API version, and feature flags. The Android app queries it when connecting and hides what the server doesn't have; this way **one** APK works against differently equipped installations: a generic client, its own server URL per user.

---

## 3. Core resources

```
GET    /me                          PATCH /me            POST /me/password
                                     → GET /me contains `totp_enabled`, `recovery_codes_remaining`, and
                                       `timezone` (the dashboard's time zone, for every signed-in user)
                                     → POST /me/password: body {current_password, new_password, totp_code}; 204, signs out
                                       all OTHER sessions (the current one stays). If two-factor is on,
                                       `totp_code` is required (optional in the schema, no break): the current code from the
                                       app or a recovery code; checking, responses, and counters as for
                                       DELETE /me/totp. First the password (wrong: 400 without `code`), then, only if the
                                       new password differs from the old one (otherwise 422, without
                                       using up the code), the code: if missing, 403 {detail, code: "totp_missing"}, no
                                       failed attempt. Its `detail` is a dedicated sentence for this operation that stands on its own
                                       („Zum Ändern des Passworts brauchst du jetzt deinen
                                       Zwei-Faktor-Code. Nutzt du die Handy-App und siehst kein Feld dafür, aktualisiere
                                       zuerst die App.“ (To change your password you now need your two-factor code. If you use the phone app and see no field for it, update the app first.)): an older app doesn't know the field and shows only this
                                       text. Wrong: 400 `totp_wrong`, app code already used: 400 `totp_used`, too many:
                                       429. Without two-factor, the password is enough as before (a code sent along
                                       is then not checked). Audit `auth.password_change` with `detail.via`
                                       (`totp`/`recovery_code`, only with two-factor); with a recovery code,
                                       additionally `mfa.recovery_used` ({remaining, aktion: "passwort_aendern"}) and
                                       a notification as for DELETE /me/totp.
GET    /me/preferences              PATCH /me/preferences
                                     → {first_steps_dismissed, theme, design_notice_dismissed}, per user and the same on every
                                       device. `theme`: `classic` („Klassisch hell“ (Classic light)), `simple` („Schlicht dunkel“ (Simple dark),
                                       default), `pro` („Profi dunkel“ (Pro dark)), `original` („Bisher“ (Previous), the look up to 0.8); an
                                       unknown stored value yields `simple`. `design_notice_dismissed`: the
                                       notice „Deck hat ein neues Aussehen.“ (Deck has a new look.) is done; new accounts have it as
                                       done right away, accounts from before the update see it once. PATCH changes only the
                                       fields sent, wrong values 422.
GET    /me/sessions                 DELETE /me/sessions/{id}
GET    /me/tokens                   POST /me/tokens      DELETE /me/tokens/{id}
POST   /me/totp/setup               POST /me/totp/confirm   DELETE /me/totp
                                     → setup: body {current_password} → 200 {secret, otpauth_uri}; without a password,
                                       400 „Zum Einschalten der Zwei-Faktor-Anmeldung brauchst du dein aktuelles
                                       Passwort.“ (To turn on two-factor sign-in you need your current password.) (the body is optional in the schema so that older callers get this
                                       response instead of 422); wrong password as below
                                     → confirm: 200 {recovery_codes: [10 codes]} (visible once,
                                       only Argon2 hashes are stored); only for a setup that is not yet
                                       confirmed (otherwise 409 „Zwei-Faktor ist schon aktiv.“ (Two-factor is already active.));
                                       wrong codes: 400, throttled (see below); the confirming code is afterwards not valid again per
                                       account (login, confirmation)
                                     → DELETE /me/totp: body {current_password, totp_code}; 204, also deletes the codes
                                       and signs out all OTHER sessions (the current one stays). If two-factor is on,
                                       `totp_code` is required: the current code from the app (valid only once per account)
                                       or a recovery code (`ABCDE-FGHJK`, spelling doesn't matter; is
                                       used up). First the password (wrong: 400 without `code`, the code stays
                                       untouched), then the code: if missing, 403 {detail, code: "totp_missing"} (no
                                       failed attempt); wrong 400 {detail, code: "totp_wrong"}, app code already used 400
                                       {detail, code: "totp_used"}; too many 429 with `Retry-After`. Wrong app codes
                                       count toward the per-account limit as at /auth/mfa (10 in 15 min, 20 in 24 h,
                                       together with login and confirmations); once it applies, the 429 message says that
                                       a recovery code still works. Wrong recovery codes count, as at login,
                                       toward their own counter (10 in 5 min, per account instead of per
                                       address; the correct password doesn't reset it) and toward the counter of all
                                       security checks of the account (30 in 5 min). Without two-factor, the password is enough.
                                       Audit `auth.2fa_disabled` with `detail.via` (`totp`/`recovery_code`); wrong codes
                                       `auth.totp_check_failed` (`aktion`, if applicable `via`, `replayed`), never the code. With a
                                       recovery code, additionally `mfa.recovery_used` ({remaining, aktion}) and
                                       a notification as at login.
POST   /me/recovery-codes           → {current_password, totp_code} → 200 {recovery_codes}; replaces all old codes;
                                       409 without active 2FA; `totp_code` (app code or one of the existing
                                       recovery codes), responses, audit (`auth.recovery_codes_generated` with
                                       `detail.via`), and notification as for DELETE /me/totp.
                                     → Security checks (POST /me/password, POST /me/totp/setup, DELETE /me/totp,
                                       POST /me/recovery-codes; with the admin's password: POST /users/{id}/reset-2fa,
                                       PATCH /users/{id} with `password` or management roles, POST /users with
                                       management roles):
                                       wrong `current_password` = 400 „Das aktuelle Passwort stimmt nicht.“ (The current password is incorrect.) + audit
                                       `auth.password_check_failed`. This also includes a wrong code at
                                       POST /me/totp/confirm (audit `auth.totp_confirm_failed`). Throttled per
                                       user across all of them together: 10 failed attempts / 5 min, then 429 with
                                       `Retry-After` (even with the correct password); the start of the lock appears as
                                       `auth.password_check_locked` in the log.
                                     → Signing out (password change, deactivating an account, turning off/resetting 2FA,
                                       emergency command) revokes the refresh tokens immediately. An access token already issued
                                       is valid only as long as its session (`sid`, also after renewals in
                                       another tab) is valid, and then immediately gets 401 (section
                                       „Access token and session“). At `/ws`, an ended session cannot
                                       reconnect, and open connections end after 30 s at the latest (see §4).
                                       Terminal and console of a signed-out session, by contrast, end after
                                       15 s at the latest (see §4, „Terminal“).
POST   /me/devices                  → push registration of the app

GET    /app/changelog               → {current, build, unreleased[], versions[]}: changelog
                                       (every signed-in user; files under backend/src/nodvard_deck/changelog/)

GET    /users        POST /users     GET/PATCH/DELETE /users/{id}
                                     → Management roles (`manages_users` in GET /roles: the role has `users.write` or
                                       `*`): POST with such `role_ids` and PATCH with such `role_ids` require, like
                                       PATCH with `password`, `current_password` and, if applicable, the admin's `totp_code` (errors
                                       as there: missing → 403 `code: "password_missing"`). Afterwards a notification „Neues
                                       Konto mit Verwaltungsrechten: …“ (New account with management rights: …) or „Verwaltungsrechte für …“ (Management rights for …) (only if the
                                       account didn't have them before). The password is checked before the target (404, 409,
                                       403 only after that; only the own account with `password` → 409 immediately), the code
                                       last. DELETE stays without confirmation.
                                     → POST: `username` as for `POST /auth/bootstrap` (trimmed, lowercase, 3 to 64
                                       characters, only `a-z 0-9 . - _`, letter or digit first; otherwise 422). The rule
                                       applies only to new accounts: `/auth/login` and the emergency command still accept
                                       any name, older accounts (for example with spaces in the name) stay usable.
                                       `email` (POST and PATCH) is not validated by the server.
                                     → PATCH: `password` sets only the password of OTHER users; the own one → 409
                                       (only via /me/password with the old password), the owner's → 403 (only the owner).
                                       With `password`, the server additionally requires `current_password` (password of the
                                       acting admin; missing → 403 `code: "password_missing"`, wrong → 400, too often
                                       → 429) and, if the admin
                                       has two-factor on, `totp_code` (their code from the app, no
                                       recovery code; missing → 403 `code: "totp_missing"`, wrong → 400
                                       `totp_wrong`/`totp_used`). Both fields are optional in the schema (only added);
                                       without `password`, PATCH needs no confirmation. Afterwards a notification
                                       „Zugang von „<name>“ geändert“ (Access of „<name>“ changed) (level warning, path `/settings/users`).
                                     → PATCH `is_active: false` (owner → 409) and a new `password` sign the user out
                                       everywhere (all refresh tokens revoked, their access tokens immediately get
                                       401). After reactivation, no old session is valid any more; they must sign in
                                       again.
                                     → Log: `user.created`, `user.updated`, `user.deleted` (who acts,
                                       `target_id` = account). `detail` names `username`, roles, and for PATCH only the
                                       changes (`roles`/`is_active` with old and new value,
                                       `display_name_changed`, `email_changed`, `password_reset`); never a password
                                       or an email address.
POST   /users/{id}/reset-2fa        → `users.write`, body {current_password, totp_code?}: password and, if the admin has
                                       two-factor on, a code from the acting admin's app (errors as for PATCH
                                       with `password`; the password is checked first, the code only when the target
                                       fits, so a rejection because of the target doesn't use up a code);
                                       turns off the 2FA of ANOTHER user and signs them out
                                       everywhere (204), afterwards the same notification as for PATCH. Owner: 403 (only the owner
                                       themselves); own account: 409 (→ /me/totp); without 2FA: 409. GET /users contains
                                       `totp_enabled`.
GET    /roles        POST /roles     GET/PATCH/DELETE /roles/{id}
GET    /permissions                  → catalog including those contributed by extensions

GET    /hosts?tag=&group=&status=   POST /hosts
GET/PATCH/DELETE /hosts/{id}         → servers are core data (hosts.read / hosts.write)
                                     → PATCH with a new `address` and DELETE: `current_password`, `totp_code` in the body
                                       if stored credentials are dropped in the process (see „Vault“)
GET    /hosts/{id}/status
GET    /hosts/{id}/actions           → which actions extensions offer here
POST   /hosts/{id}/actions/{action}  → goes through the gate, may return 202 "proposed"
GET    /hosts/{id}/credentials       POST /hosts/{id}/credentials   DELETE /hosts/{id}/credentials/{cid}
                                     → DELETE …/credentials/{cid} only with confirmation in the body (see „Vault“)
POST   /hosts/{id}/credentials/generate-key           → generate an SSH key (private part only in the vault)
GET    /hosts/{id}/credentials/{cid}/setup            → setup command for the server
POST   /hosts/{id}/credentials/{cid}/setup-with-password → set up the server with a one-time password (hosts.write + hosts.execute)
POST   /hosts/{id}/credentials/{cid}/make-default     → switch the default credential (with `delete_previous` and
                                                        confirmation in the body, see „Vault“)
GET    /hosts/{id}/requirements      → what extensions need on the server
POST   /hosts/{id}/check             → check the connection (reachable, server key, login, root, extensions, OS)
POST   /hosts/{id}/helper-hint/dismiss → 204, hide the hint „Server neu einrichten“ (Set up the server again) in the overview (hosts.write)
GET    /hosts/{id}/known-hosts       POST /hosts/{id}/known-hosts (confirm key)
DELETE /hosts/{id}/known-hosts/{key_type}
GET    /host-groups   POST /host-groups   PATCH/DELETE /host-groups/{id}
POST/DELETE /host-groups/{id}/members/{host_id}

GET    /secrets                      → metadata ONLY. Never values.
POST   /secrets      PUT /secrets/{id}/value      DELETE /secrets/{id}
                                     → PUT …/value `{value, current_password, totp_code}`, DELETE with body
                                       `{current_password, totp_code}`: confirmation as described under „Vault“
                                     → PUT …/value and DELETE: 409 if the secret is an account's two-factor key
                                       (turn off only via DELETE /me/totp, POST /users/{id}/reset-2fa,
                                       or the emergency command)
                                     → DELETE: 409 `secret_in_use` with `used_by[]` as long as the entry is firmly bound
                                       (owner module or server credential; replace or remove it there). A merely
                                       recorded use (`last_used_at`) is a hint and doesn't block
POST   /secrets/{id}/test            → check usability without showing the value
PATCH  /secrets/{id}                 → change description and expiry date, never the value

GET    /audit?actor_type=&action=&outcome=&target_type=&target_id=&correlation_id=&since=&until=
               &exclude_action=…&limit=100&offset=0   (limit at most 1000)
GET    /audit/export                 → NDJSON stream
                                     → /audit and /audit/export: output in `detail` (result of `action.executed`,
                                       fields `output`/`stdout`/`stderr`) and commands (fields `command`) only with
                                       `hosts.execute`, also in old entries; see „Actions“
                                     → /audit and /audit/export: typed login names appear in no response.
                                       `detail.username_ref` and `detail.username_length` go only to the owner
                                       (for everyone else, including admins, they are missing). Old entries for unknown
                                       names appear in the response for everyone with actor `anonymous`/`unbekannt`; for
                                       `login.locked`, `detail.username` is always missing, even for known accounts.
                                       The response format stays the same.

GET    /settings     PUT /settings/{key}
GET    /branding     PUT /branding   POST /branding/logo   POST /branding/favicon
GET    /branding/custom-css         PUT /branding/custom-css
                                     → `branding.write`; body and response `{css}`. PUT checks before saving
                                       (otherwise 422 with a German sentence, nothing saved): at most 64 KB
                                       (UTF-8), no null character, no `@import`, no `<!--` or `-->`, no
                                       functions that load strings as an address (`image()`, `image-set()`,
                                       `cross-fade()`, `src()`, also with a vendor prefix). The target of every `url()` only
                                       `data:` or a path on this server (`/…` made of
                                       `[A-Za-z0-9._~!$&'()*+,;=:@%/?#-]`, not `//`); whitespace and
                                       control characters in the target are rejected, also as an escape, not rewritten.
                                       It is read as in the browser (CSS Syntax Level 3): line endings are
                                       normalized, comments, strings, escapes, and `url()` are evaluated in one
                                       pass; a string with a line break or an open
                                       `url()` counts as an error. Log `branding.custom_css.saved` with `bytes`.
                                     → PUT: `support_url` needs the scheme `http:`, `https:`, or `mailto:`
                                       (case doesn't matter, not empty after it) and no whitespace or
                                       control characters in the middle, otherwise 422. Whitespace at the edges is dropped, empty = no
                                       link (`null`). An invalid value saved earlier comes back in
                                       `GET /branding` as `null`.
                                     → PUT: `logo_url` and `favicon_url` only as a path on this server (the same
                                       rule as for `url()` in the custom CSS) or `data:image/…`, otherwise 422; empty =
                                       `null`. Reason: the app page loads images only from there (`img-src 'self' data:
                                       blob:`). A foreign value saved earlier stays saved and may be
                                       sent back unchanged so that other fields stay saveable; the
                                       interface doesn't use it and shows a hint to upload. If
                                       `logo_url` or `favicon_url` afterwards no longer points to the uploaded file
                                       („Entfernen“ (Remove), `data:`, or another path), it is deleted; `GET
                                       /branding/logo` or `/favicon` then answers 404.
                                     → POST /branding/logo: raw body, `Content-Type` `image/png`, `image/jpeg`,
                                       `image/svg+xml`, or `image/webp` (otherwise 415), at most 2 MB (otherwise 413).
                                       An empty file or a file start that doesn't match the type (PNG, JPEG,
                                       WebP, ICO by their first bytes) gives 422, nothing is saved.
                                       An SVG is checked before saving (allowlist): no scripts,
                                       event attributes (`on…`), animations, `<foreignObject>`, or
                                       processing instructions (`<?xml-stylesheet …?>`), no DOCTYPE declaration with
                                       `[…]` (custom entities), no foreign namespaces (except those of
                                       Inkscape/Illustrator), references (`href`, `url(…)`) only to parts of the same
                                       file or embedded PNG/JPEG/GIF/WebP images (`data:…;base64,…`).
                                       Otherwise, also for a broken file, 422 „SVG-Logo abgelehnt: …“ (SVG logo rejected: …).
                                     → POST /branding/favicon: like the logo, but `image/png`, `image/svg+xml`,
                                       `image/x-icon`, or `image/vnd.microsoft.icon` (otherwise 415), at most 512 KB
                                       (otherwise 413), SVG with the same check („SVG-Favicon abgelehnt: …“ (SVG favicon rejected: …)). Sets
                                       `favicon_url` to `/api/v1/branding/favicon`. Is stored next to the logo under
                                       `branding/` and is therefore in every backup.

GET    /extensions                   GET /extensions/{id}
POST   /extensions/{id}/enable       POST /extensions/{id}/disable
PUT    /extensions/{id}/settings     PUT /extensions/{id}/permissions
POST   /extensions/{id}/reload       DELETE /extensions/{id}
GET    /extensions/{id}/frontend/index.js     → ESM bundle
GET    /extensions/{id}/settings     → {schema, values, secrets: [{label, title, description, item, is_set, optional}], secrets_cleared}
PUT    /extensions/{id}/secrets      → set/replace the secret of an `x-secrets` label (never read); `409` as long as none
                                       of the fields from `x-secret-bound-to` has a stored value or `default`,
                                       and `409` as long as the name of the list entry (`per_item`) occurs more than once.
                                       One already stored only with `current_password`/`totp_code` in the body
DELETE /extensions/{id}/secrets?label=…  → remove the secret (idempotent, 204); one already stored only with body
                                       `{current_password, totp_code}`
                                     → PUT /extensions/{id}/settings: if the change deletes stored credentials (new
                                       address, see `secrets_cleared`), `current_password`/`totp_code` belong in the body
                                       next to `values`
POST   /extensions/{id}/test         → check the connection, body optional {"mode": "connection"|"message"}
POST   /extensions/{id}/takeover     → {old_id}: allow taking over the data stored under an old identifier (`extensions.manage`),
                                       see „Erweiterungen einrichten und testen“ (Setting up and testing extensions)

GET    /connectors                   POST /connectors
GET/PATCH/DELETE /connectors/{id}    POST /connectors/{id}/test

GET    /jobs         POST /jobs      GET/PATCH/DELETE /jobs/{id}
POST   /jobs/{id}/run                → run immediately; a disabled job (`enabled: false`) → `409`
GET    /jobs/{id}/runs               GET /runs/{id}      GET /runs/{id}/output
POST   /runs/{id}/cancel

GET    /notifications?unread=        POST /notifications/read
GET    /notifications/{id}           POST /notifications/read-all
GET    /notifications/unread-count   → {unread}
DELETE /notifications/{id}           → 204, unknown ID 404
POST   /notifications/delete-read    → {deleted}
```

**Notifications** (`notifications.read` to read and count): The read status (`read_at`) is shared by all users. Marking as read (`POST /notifications/read` with `{ids}`, `POST /notifications/read-all`) therefore additionally requires `notifications.write` (built in: „Bediener“ (operator), Admin, Owner), otherwise `403`. Deleting also affects everyone and requires `notifications.write`: `DELETE /notifications/{id}` removes a notification together with its delivery logs (`204`, unknown ID `404`), `POST /notifications/delete-read` removes all read ones at once (`{"deleted": n}`, unread ones stay, `0` if nothing matches). Both appear in the log as `notifications.deleted` (`target_type` `notification`): singly with the ID as `target_id` and the `title` in the details, in bulk with `{"count": n}`; a call with no match writes nothing. Deletion happens in chunks with a commit in between; if a later chunk fails, an entry with the progress so far is still written (`outcome` `failure`, `{"count": n, "interrupted": true}`) and the error propagates. A send to the channels that is running just when its notification is deleted finishes normally (only the delivery log is gone). In addition, the core job `notifications-retention` cleans up every night (setting `notifications.retention_days`, see below).

**Vault** (`secrets.read` to read, `secrets.write` to change): Besides the metadata, `GET /secrets` returns `expires_at` and `used_by[]` (`type`: `extension` | `host` | `login`, more possible; plus `id`, `name`, and `last_used_at` if a use is recorded). `bound[]` (same structure, also for `PATCH`) contains only the firm bindings among them (owner module, server credential, two-factor): as long as it is not empty, `DELETE` refuses. Whoever has merely read an entry appears only in `used_by[]`. `POST /secrets` optionally takes `expires_at`. `PATCH /secrets/{id}` changes `description` and `expires_at` (`null` removes the date, a time without a zone counts as UTC); only `PUT /secrets/{id}/value` replaces the value. An account's two-factor key gives `409`, an unknown entry `404`. The expiry date is only information: nothing is locked after it passes.

Replacing or deleting an **already stored** secret requires the same confirmation on these paths: `PUT /secrets/{id}/value`, `DELETE /secrets/{id}`, `PUT`/`DELETE /extensions/{id}/secrets`, `PUT /extensions/{id}/settings` (if the change deletes stored credentials), `DELETE /hosts/{id}/credentials/{cid}`, `DELETE /hosts/{id}` (if the server has stored credentials), `PATCH /hosts/{id}` with a new `address` (if stored SSH passwords are dropped in the process), and `POST /hosts/{id}/credentials/{cid}/make-default` with `delete_previous` (if a previous credential is dropped). The body contains `current_password` and, if two-factor is on, `totp_code` (code from the app). Responses:

- `403` with `code: "confirm_required"` and `two_factor` (bool) if the password is missing. Nothing is changed; the interface asks for the password and, if needed, the code, and sends the call again.
- `400` for a wrong password, `403` `totp_missing` without a code, `400` `totp_wrong`/`totp_used` for a wrong or already used code, `429` with `Retry-After` after too many failed attempts (the same throttling as at login).
- An initial creation (nothing stored yet) needs no confirmation, and neither does `PATCH /secrets/{id}` (description, expiry).
- **Exception:** „Mit Passwort verbinden“ (Connect with password) in Proxmox VE and passing it on to Proxmox Backup replace the API token of the own connection without this prompt. That requires the password of the Proxmox server, Deck creates a new token there, and the old one stays valid in Proxmox until it is deleted there (see [10-PROXMOX-TOKEN.md](../10-PROXMOX-TOKEN.md), in German). A second prompt in the middle of connecting would be only a hurdle without protection.

Only what is no longer firmly bound (owner module, server credential, two-factor) can be deleted from the vault: otherwise `409` with `code: "secret_in_use"` and `used_by[]`. A module having read an entry once doesn't block. The secrets a module creates itself always belong to that module; they are replaced or removed via the module settings (`/extensions/{id}/secrets`). The log contains `secret.created`, `secret.replaced`, `secret.updated`, and `secret.deleted` (label and changed fields, never a value).

**Behavior change for older clients** (such as an older Android app): Whoever previously replaced or deleted a stored secret via the module settings or a server credential now gets `403 confirm_required` until they send the password (and code). Reading and initial creation don't change.

The core job `secret-expiry-reminder` („Erinnerung an ablaufende Zugänge“ (Reminder for expiring credentials), daily at 08:07 in the configured zone) notifies for each entry with `expires_at` at most three times: 14 days before (`info`), 3 days before (`warning`), and after expiry (`warning`, so it also appears on the start page). Calendar days in the configured zone are counted. The job doesn't catch up on missed stages: whoever enters the date only 2 days before gets only the 3-day notification. If `expires_at` changes, the count starts over. The notification names the label and date, never the value, and says who owns the credential (module or server). `payload.path` points to `/settings/extensions/{id}` or `/settings/hosts/{id}`, `tags` are `key` and, from 3 days before, additionally `warning`. What has already been sent is stored in `secrets.metadata.expiry_notice` (see docs/03-DATA-MODEL.md). A pause via „Automatik“ (Automation) lasts only until the next start, as with the other fixed core jobs.

**Servers, credentials, and groups** (`hosts.read` to read, `hosts.write` to change; every change appears in the log):

- `POST /hosts` and `PATCH /hosts/{id}` validate the inputs. The short name (`name`) is trimmed and lowercased and must match `[a-z0-9][a-z0-9_-]{0,63}`; the address is an IP address (not `0.0.0.0`/`::`, no multicast address) or a hostname per RFC 1123, without scheme, `user@`, path, or port (a single trailing dot is removed); `tags` follow the tag rules (at most 20). Invalid → `422` with a German message. The short name cannot be changed. `PATCH` additionally takes `current_password` and `totp_code` (code from the app if two-factor is on). They are needed only if a new `address` deletes the server's stored SSH passwords; if the password is then missing, `403 confirm_required` follows and nothing is changed (responses as under „Vault“).
- `HostOut` additionally contains `credential` (the default credential as `{id, kind, username, port}` or `null`, never a secret), `managed_tags` (the tags managed by an extension, a subset of `tags`), and `login_ok_at`: when Nodvard Deck demonstrably signed in with the current default credential at this address and port. `last_seen_at`, by contrast, means only that the server responded (for the core job `host-reachability`, it is enough that the SSH port accepts connections). `login_ok_at` is set by a successful login with the default credential in `POST /hosts/{id}/check` or `GET /hosts/{id}/status` (each time anew) or by an extension's SSH connection (`ctx.exec`; only if there is no proof yet), never by the core job `host-reachability`. `null` means: not proven, either never, or since the server rejected the login with the default credential or showed a different server key, or since a new address or a new default credential, each until the next successful login (exception: `make-default` after a fresh check, see below).
- For groups and badges („LXC auf pve1“ (LXC on pve1)), `HostOut` also has `category` (`server`, `vm`, or `lxc`, from `kind`; a hypervisor node and a manually created server without a kind are `server`), `parent_label` (for a guest, the host it runs on, as the provider names it, otherwise `null`), and `power_state` (`running`, `stopped`, `paused`, or `null` if the provider doesn't report it). A stopped guest still has `status: "down"`; the interface shows it gray as „aus“ (off) and doesn't count it as a fault. The three values are reported via the identically named fields of `DiscoveredHost` in the SDK (`parent_ref`, `parent_label`, `power_state`); the core stores them anew in the host's metadata on every reconciliation.
- `gone_since` in `HostOut`: since when the provider no longer knows the server (such as a guest deleted in Proxmox), otherwise `null`. Set via `ctx.hosts.mark_gone(provider_refs)` in the SDK, which an extension calls only for areas it has just read completely (Proxmox: the node list per connection, the guests per node; an unreachable connection marks nothing). If a later reconciliation reports the server again, the mark disappears. The interface doesn't count such servers, shows them gray, and offers „Entfernen“ (Remove) (`DELETE /hosts/{id}`).
- `DELETE /hosts/{id}` `{current_password, totp_code}` also deletes the stored credentials together with their secrets in the vault and closes open SSH connections. If the server has stored credentials, the password and, if two-factor is on, the code are required in the body (otherwise `403 confirm_required`, responses as under „Vault“); without credentials, the body may be omitted. If an action is currently running on the server (`executing`) → `409`.
- `GET /hosts/{id}/metrics` (`hosts.read`) queries the current measurements from the server's `MetricsProvider` → `{values, sampled_at}` (unknown server or no provider: `404`). If the query fails at the connection (SSH error such as server unreachable, login rejected, or key not confirmed, `HostUnreachable`, timeout, or another network error), `502` follows with the reason in `detail`. The same for an exception whose class carries `readable = True` ([Extension interface](EXTENSION-API.md)): the Proxmox extension uses this to report a Proxmox that doesn't answer or answers with an error. Any other provider error stays `500`, including a file error.
- `POST /hosts/{id}/credentials`: `username` `[A-Za-z0-9_][A-Za-z0-9_.@\ -]{0,63}` (also Windows names such as `Max Mustermann`, `user@domain`, `DOMAIN\user`; no control characters), `port` 1–65535; for `kind = ssh_key`, `secret_value` must be a private key without a passphrase (otherwise `422`: „Der Schlüssel ist mit einer Passphrase geschützt …“ (The key is protected with a passphrase …) or „Das ist kein gültiger privater SSH-Schlüssel.“ (This is not a valid private SSH key.)). Keys and passwords never come back in a response or in the log; `422` responses don't contain the input either (only `type`, `loc`, `msg`). Deleting a credential or a remembered key, or changing the address, closes the server's open SSH connections. A new address also deletes the server's stored SSH passwords together with the secret in the vault (keys stay): a password belongs to the server it was entered for. This also applies when an extension's reconciliation (e.g. Proxmox) changes the address; exempt there are VMs and LXC containers whose address the guest reports itself and for which a server key is already remembered.
- `GET /hosts/{id}/known-hosts` → `[{key_type, fingerprint, first_seen_at, accepted_by_user_id, accepted_by_label}]`, sorted by `key_type`. `accepted_by_label` is the username (`null` for automatic remembering on first contact); other people's names are visible only to users with `users.read` or the approval permission, otherwise `user/<id>` appears.
- `GET /hosts/{id}/requirements` (`hosts.read`) → `[{ext_id, id, label, check_command, ok_text, fail_hint, unix_group, needs_root, root_reason, order}]`: what extensions report for this server via `ctx.ui.register_host_requirement()` (filtered by the server's `tags` and `os_families`, without those with a tag of the server in `skip_tags`, sorted by `order`). `unix_group` is `null` if the name doesn't match `^[a-z_][a-z0-9_-]{0,31}$`.
- `POST /hosts/{id}/credentials/generate-key` `{username = "lattice", port = 22}` (`hosts.write`, `201`) generates an ed25519 key for this server only (comment `nodvard@<short name>`). The default `username = "lattice"` stays for compatibility reasons (older callers often send no name); the interface suggests `nodvard` and always sends the name. Response: `{credential, public_key, fingerprint}`; **the private key lives only in the vault** and never appears in a response (not even a `422`), the log, or the logs. `is_default` is `true` only if the server has no SSH credential yet (key or password); otherwise the previous default stays until `make-default` switches it. `username` and `port` are checked as for `POST .../credentials`. At most 10 keys per 5 minutes and user, otherwise `429` with `Retry-After`. Log `host.key_generated` (`credential_id`, `username`, `port`, `fingerprint`).
- `GET /hosts/{id}/credentials/{cid}/setup?sudo=false&full_sudo=false&groups=` (`hosts.write`) → `{username, public_key, fingerprint, one_liner, script, notes, groups, sudo, full_sudo, helpers}`: the command to run on the server (directly as root, otherwise via `sudo`; `script` is the same, readable). The public key is derived server-side from the vault, with the comment `nodvard@<short name>` (never the one stored in the key). The command creates the user (if it doesn't exist, without password login), enters the key as `restrict,pty …`, with a running Dropbear as `no-port-forwarding,no-agent-forwarding,no-X11-forwarding …` (it recognizes an already entered one by type and key text, also with the older comment `lattice@…`; with Dropbear it replaces its own old line `restrict,pty <key>`, also with the comment `lattice@…` or an earlier short name, and reports „Schlüssel für Dropbear angepasst.“ (Key adjusted for Dropbear.); if the key is in the file without options, it puts them in front of every such line and reports „Der schon eingetragene Schlüssel für Nodvard Deck ist jetzt eingeschränkt …“ (The key already entered for Nodvard Deck is now restricted …), in the log of the setup itself as `key_fixed`) and, on request, sets up `sudo` without a password (`sudo=true`, checked with `visudo`, see below) and groups (`groups=docker` or `groups=a,b`). Groups are entered only if extensions require them for this server (`unix_group`; the core ignores privileged groups (`root`, `sudo`, `wheel`, `admin`, `shadow`, `disk`, `adm`, `staff`, `lxd`, `libvirt`, `kvm`) and records that in the log; `docker` is deliberately allowed and effectively means root); for `root`, there are neither groups nor `sudo`. With `sudo=true`, the command brings along the helper scripts of the enabled modules that need root for this server (`helpers`: `[{ext_id, module, path, version, sha256, text}]`), each with its own rule `/etc/sudoers.d/nodvard_<ext_id>_<user>` only for its script. Only these rules exist if at least one enabled module needs root on this server and each of them has a helper script. If a module needs root without a helper script, or with `full_sudo=true`, the full rule (`/etc/sudoers.d/nodvard-<user>`, `NOPASSWD: ALL`) is added; `full_sudo` in the response says whether it is included. If no module needs root, `sudo=true` without `full_sudo` sets up nothing with sudo at all: `sudo` in the response is then `false`, and `notes` says so. Without the full rule, a helper script that cannot be set up is an `FEHLER` (error; the command ends with an error), with the full rule a hint, except for a module that never takes the full rule without its script (`sudo_fallback = false` in the manifest, currently Docker): then even with the full rule an `FEHLER`, and `notes` says so. The command never withdraws an existing full rule. For a user other than `root`, everything that touches `~/.ssh` and `authorized_keys` runs **as that user** (`runuser`, otherwise `su`), never as root with `chown`; if `~/.ssh` or `authorized_keys` is a link, the command aborts with `FEHLER` instead of following it. For `root` (e.g. Proxmox, where `authorized_keys` is a link into the cluster file system), the link is left untouched. An older rule `/etc/sudoers.d/lattice-<user>` is removed by the command only once the new one is entered and the whole configuration is checked, and only if it matches byte for byte the rule it created itself earlier; otherwise (also as a link) it stays with a hint. Each own file is checked by `visudo -c -f` on its own; the whole configuration (`visudo -c`) must also be valid afterwards only if it was before (a foreign file with wrong permissions would otherwise make the setup fail). `messages` then contains up to five lines `visudo: …` with the message from `visudo`. If `visudo` rejects the sudo rule or it is rolled back, the command ends with an error instead of „Fertig“ (Done), likewise if it has to restore the old rule. Every value in the command is strictly validated or quoted with `shlex.quote`. `422` for password credentials, non-Linux servers, a username that is not a usual Linux name (`^[a-z_][a-z0-9_-]{0,31}$`), and an unreadable key. Changes nothing (no log entry).
- `POST /hosts/{id}/credentials/{cid}/setup-with-password` `{password, login_user = "root", sudo = false, full_sudo = false, groups = [], sudo_password_same = true}` (`hosts.write` **and** `hosts.execute`) sets up the server for this key credential without anyone pasting the command themselves: Nodvard Deck signs in **once** with `login_user`/`password`, runs exactly the command from `GET .../setup` (`sudo`, `full_sudo`, and `groups` as there; directly as `root`, otherwise via `sudo -S`, which gets the password via standard input, with `sudo_password_same = false` via `sudo -n`, which works only if the account may use sudo without a password), closes the connection, and then checks with the new key like `POST /hosts/{id}/check`. `login_user` is the account for this one login, not the credential's user. Response `200` `{ok, check, notes, messages, made_default}`: `ok` means the command ran and the check found no error (if it still shows something, it stays `200` with `ok: false`); `check` is the response of `POST /hosts/{id}/check` or `null` if checking wasn't possible just then (a hint is then in `notes`); `messages` are only the command's own lines („Benutzer … angelegt.“ (User … created.), hints, `FEHLER: …`), never the output of sudo or the server; `made_default` is `true` if the credential thereby became the default (only if the server had none; otherwise it stays with the `make-default` button). The same preconditions as `GET .../setup` (`404` unknown credential, `422` for password credentials, non-Linux servers, and invalid usernames); `422` with a fixed sentence at the field also for an empty password, one over 512 characters, or one that cannot be sent (line break, NUL, lone surrogate), and for an invalid `login_user`, never with the value. **The password is never stored, logged, or returned** (not in the database, the vault, the log, a response, not even a `422`; it never appears on the command line). If the command is longer than 8000 bytes, the command line is a short, fixed text: the script arrives via standard input between two identical random markers, followed by the line that `sudo -S` itself reads as the password. The password goes only to the stored address and port of the credential, and only if Nodvard Deck already knows the server key: otherwise `409` `host_key_unconfirmed` with `host_key` (`{status, key_type, fingerprint, expected}`), without the password having been used and without it costing an attempt; confirm with `POST /hosts/{id}/known-hosts` and the `credential_id` of this credential. **The route deliberately doesn't go through the action gate** (see [Architecture, section 4](ARCHITECTURE.md#4-the-action-gate)): a proposal would store its inputs in `actions.payload`, and the password must never end up there. Instead, it requires `hosts.execute` in addition to `hosts.write` (the command runs as root on the server, as with Terminal and console), and every attempt appears in the log. Errors have the form `{detail, code}` with a fixed German sentence (never text from the server or the SSH library); depending on the case, `host_key` or `messages` is added: `wrong_password` (`400`, login rejected), `sudo_failed` (`400`, the command didn't run at all, e.g. no sudo permission or wrong sudo password; likewise if the login shell cannot read a long command, such as fish or tcsh, then with its own sentence and as root with the code `setup_failed`), `invalid_password` (`400`), `host_key_unconfirmed` and `host_key_changed` (`409`, with `host_key`; for `changed`, the password was likewise not sent, `expected` is the remembered fingerprint; the changed key is never offered for confirmation), `helpers_blocked` (`409`, with `messages`: only with the helper scripts' rules that cannot be stored safely on the server; the command aborted before any change, the messages name the folder, reason, and remedy), `rate_limited` and `busy` (`429`, `rate_limited` with `Retry-After`; `busy`: a setup is already running for this server), `unreachable` (`502`, not reachable), `connection_failed` (`502`, the connection wasn't established or broke off during login; nothing was executed), `connection_lost` (`502`, the connection broke off after the command started: the setup may have run partially, check with „Verbindung prüfen“ (Check connection), a new attempt is harmless), `setup_failed` (`502`, the command ran and failed, with `messages`; likewise if a long command didn't arrive completely at the login shell, also because something had already read from standard input beforehand: then nothing ran, `messages` is empty, and a new attempt is harmless), and `timeout` (`504`, after 90 s without an end; it may have run through anyway). Limits: per target (address and port) 5 and per user 10 attempts in 10 minutes (every run in which the password could go to the server counts as an attempt), plus 30 calls per user in 5 minutes, and only one run at a time per server. The counters live in memory. Log `host.setup_with_password` (`target_type` `host`; `outcome` `success`/`failure`; `detail`: `credential_id`, `username`, `login_user`, `sudo`, `full_sudo`, `groups`, `helpers`, `result` (`ok` or the error code), on success additionally `check_ok` and `made_default`; never the password), plus `host.connection_checked` for the check and `host.credential_made_default` if the credential became the default. A lock (`rate_limited`) appears in the log only once per user within the time window; errors before the run (`403`, `404`, `422`) not at all.
- `POST /hosts/{id}/credentials/{cid}/make-default` `{delete_previous = false, current_password, totp_code}` (`hosts.write`; body may be omitted) makes the credential the default. `current_password` and, if two-factor is on, `totp_code` are needed only if `delete_previous` actually deletes a previous credential (otherwise `403 confirm_required`, responses as under „Vault“; checked only after the check below, so a `409` doesn't use up a code). With `delete_previous`, the previous default and its secret are deleted in the same transaction and its open SSH connections are closed immediately; without `delete_previous`, the connections are only retired (running terminals keep running, new ones take the new default). **`delete_previous` works only after a fresh, successful check of the NEW credential:** `POST /hosts/{id}/check` with its `credential_id` must have returned `ok` for „Anmeldung“ (Login) within the last 10 minutes (and address and port are unchanged since), otherwise `409` („Bitte zuerst „Verbindung prüfen“ …“ (Please run „Verbindung prüfen“ first …)), and nothing is deleted or switched. Applies only if an old credential would really be deleted. The state lives in memory (after a restart, simply check again). If an old credential is deleted, `notice` contains a hint, otherwise `null`: for a key, that it stays in `authorized_keys` on the server (Nodvard Deck doesn't remove it there), for a password, that nothing changes on the server. If `POST /hosts/{id}/check` with the new credential succeeded in logging in within the last 10 minutes (address and port unchanged since), `login_ok_at` takes the time of that check; otherwise it is `null` until the next successful login. If the credential is already the default, nothing happens. Log `host.credential_made_default` (`credential_id`, `previous_credential_id`, `deleted_previous`) and, with `delete_previous`, additionally `host.credential_deleted`.
- `POST /hosts/{id}/check` `{credential_id?}` (`hosts.write`, body may be omitted) checks step by step whether Nodvard Deck can connect to the server, with the default credential or the one given (`404` for a credential of another server, `422` for `api_token`). Response `{ok, checked_at, items, host_key, os, credential_id}`; `ok` means: no item is `fail` or `confirm`. Items `{id, label, status, detail, hint}` with `status` `ok` | `warn` | `fail` | `skipped` | `confirm`, in this order (after a failure in the first three, the check stops): `reachable` (TCP + SSH banner), `host_key`, `login`, `root` (`sudo -n true`; only `warn`, because "without root" is allowed), one per report of an extension with `check_command` `req:<ext_id>:<id>` (exit 0 → `ok`, 127 → `skipped`, otherwise `warn` with the `fail_hint`, `{user}` replaced), `os`. Windows servers: `root`, `os`, and `req:*` are skipped. `host_key`: `{status: known|new|changed, key_type, fingerprint, expected}`. **Without a confirmed server key, no login data goes to the server:** if the key is new, the check reports `host_key` as `confirm` (with fingerprint) and stops; even the key exchange aborts before username, password, or key are sent, and nothing is remembered. Without a credential, the key is only read (port 22). A **changed** key is always `fail` (also if the server merely shows a different key type than the remembered one; for a server with several keys, the remembered type is preferred) and is never offered for confirmation (first `DELETE .../known-hosts/{key_type}`, then check again and confirm). Only the credential's stored address and port are checked. The check uses its own connection (not from the pool); after a successful login, the server's pooled connections are retired (new group permissions apply, running terminals are not cut off) and the server's `status`/`last_seen_at` are updated (`down`: not reachable, `unknown`: key problem). With the default credential, a successful login sets `login_ok_at`, a rejected one clears it; a changed server key always clears it, an unreachable server never. Checking another credential otherwise changes nothing there. Texts are fixed German sentences, never password, key, error output from the server, or exception text. Time limits: 35 s in total, 8 s per command; at most two checks at once. `429` (with `Retry-After` where there is a wait time) for more than 12 checks per 5 minutes and user, for a check already running on the same server, and when checking is at capacity. Log `host.connection_checked` (`outcome` `success`/`failure`, `detail`: `credential_id` and `items` as `{id: status}`, nothing else).
- `POST /hosts/{id}/known-hosts` `{key_type, fingerprint}` (`hosts.write`, `201`) remembers a server key (response like an entry from `GET`, `accepted_by_user_id` is the confirming user). Only the key seen by the **last check of this server** (at most 15 minutes ago, address and port unchanged since; those of the default credential, 22 without a credential), and only once; anything else → `409` („Bitte zuerst „Verbindung prüfen“ – der Fingerabdruck muss frisch vom Server kommen.“ (Please run „Verbindung prüfen“ first; the fingerprint must come fresh from the server.)). If **any** key is already remembered for the server (also of another type) → `409` („… Erst den alten vergessen.“ (… Forget the old one first.)): a further key type is never added by confirming. Log `host.known_key_pinned` (`key_type`, `fingerprint`).
- `credential_id` at `POST /hosts/{id}/known-hosts` (optional, new; older callers don't send it): the SSH credential of this server with which the check saw the key. Its port counts as that of the last check; without it, the port of the default credential applies as before (22 without a credential). Needed if a further credential is on a different port than the default, e.g. when setting up with a password for this credential. A credential of another server or an unknown ID → `404`, a credential without SSH (e.g. `api_token`) → `422`; everything else (fresh fingerprint, only once, no other key already remembered) applies unchanged.
- `DELETE /hosts/{id}/known-hosts/{key_type}` (`hosts.write`, `204`; no remembered key of this type → `404`) forgets a remembered server key and closes the server's open SSH connections. Afterwards Nodvard Deck no longer remembers a key for this server on its own (Terminal, jobs, status), regardless of the setting of `ssh.confirm_new_host_keys` or `NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS`: only `POST /hosts/{id}/known-hosts` after a new check lifts that (or `DELETE /hosts/{id}`). Log `host.known_key_forgotten` (`key_type`, `fingerprint`).
- `PATCH /host-groups/{id}` (`name`, `description`; name taken → `409`, unknown → `404`) and `DELETE /host-groups/{id}` (deletes only the group and its assignments, never servers). `POST /host-groups/{id}/members/{host_id}` is idempotent and returns `404` for an unknown group or an unknown server.
- Log entries (`target_type` `host`, for groups `host_group`): `host.created`, `host.updated` (`changed` with `from`/`to` per field; if a new address deleted SSH passwords, additionally `password_credentials_removed` with their count, also in an extension's reconciliation, then as an entry of the extension), `host.deleted`, `host.credential_added`, `host.credential_deleted`, `host.key_generated`, `host.credential_made_default`, `host.known_key_pinned`, `host.known_key_forgotten`, `host.connection_checked`, `host.group_changed` (`change`: `created`, `renamed`, `description_changed`, `member_added`, `member_removed`, `deleted`).

**Settings** (`GET /settings`, `PUT /settings/{key}` with `{"value": ...}`; both `settings.write`). Managed keys, invalid value → `422`:

| Key | Value | Default |
|---|---|---|
| `autonomy.mode` | `propose` or `full` | `propose` |
| `autonomy.max_risk` | `low`, `medium`, `high`, `critical` | `low` |
| `security.deny_patterns` | list of regex strings | `[]` |
| `maintenance.windows` | list of `{cron, duration_minutes, host_ids}` | `[]` |
| `system.timezone` | IANA name from the zone database (`Europe/Berlin`) | `NODVARD_DECK_TIMEZONE`, otherwise `TZ`, otherwise `Europe/Berlin` |
| `audit.retention_days` | integer 7 to 3650 | `NODVARD_DECK_AUDIT_RETENTION_DAYS` (90) |
| `jobs.run_retention_days` | integer 1 to 3650 | `30` |
| `notifications.retention_days` | integer 1 to 3650 | `90` |
| `hosts.reachability.enabled` | `true` or `false` (real booleans only) | `true` |
| `hosts.reachability.interval_minutes` | integer 1 to 60 | `2` |
| `system.update_check.enabled` | `true` or `false` | `true` |
| `system.update_check.channel` | `stable` (final versions only) or `beta` (also pre-releases) | `stable` |
| `ssh.confirm_new_host_keys` | `true` or `false` | `false`; new installations `true` (set at the first account) |
| `system.trusted_proxies` | list of IP addresses/networks (CIDR, IPv4/IPv6), at most 20, public IPv4 networks from `/24` (private ranges down to the size of the range, at most `/8`), IPv6 from `/48` | `[]` (trust no one); `NODVARD_DECK_TRUSTED_PROXIES` wins |

- `system.timezone` is the time zone of all schedules (`Job.timezone`, including those of extensions) and of the maintenance windows. When it is set, all jobs with the previous default zone get the new one (an explicitly different one stays) and are rescheduled, `next_run_at` follows immediately. A job that is newly registered (extension reloaded) keeps its zone. The time of day stays, the moment shifts; around the daylight-saving change, a run is skipped once or runs twice (APScheduler decides). The server never formats displayed timestamps; the interface shows them in the device's time.
- `audit.retention_days`: The nightly run `audit-retention-purge` deletes log entries that are older. The setting wins, the environment variable is the fallback (also for an invalid stored value).
- `jobs.run_retention_days`: The nightly core job `job-runs-retention` (03:20) deletes finished job runs (`job_runs`, everything except `running`) that began more than this many days ago, together with their log file (`output_ref`). Exceptions: **running runs** always stay, and **per job, the last 20 finished runs** stay (a monthly job keeps its history); runs without a job go by age only. Files are deleted only within the `runs` folder (resolved path, symlinks are not followed), and a missing file is not an error. In addition, files in the `runs` folder that have no matching row and are older than the retention period are removed. Deletion happens in chunks of 5000 rows with a commit in between; no `VACUUM` (see docs/03-DATA-MODEL.md §7). The separate, stricter cleanup of `host-reachability` (24 hours) remains. The run reports `{retention_days, runs_deleted, files_deleted, orphans_deleted, chunks}`. A stored value outside 1 to 3650 counts as not set (default 30).
- `notifications.retention_days`: The nightly core job `notifications-retention` (03:30 in the configured zone, its own minute next to the other cleanup jobs) deletes notifications created more than this many days ago (`notifications.ts`), read or unread, together with their delivery logs (`notification_deliveries`). **An unread critical notification (`severity` `critical`, not yet read) stays** until someone reads it; once it is read, it goes after the period like any other. Today's notifications are never affected (smallest value: 1 day). Deletion happens in chunks of 5000 rows with a commit in between, no `VACUUM` (see docs/03-DATA-MODEL.md §7). The run reports `{retention_days, deleted, chunks}`. A stored value outside 1 to 3650 counts as not set (default 90). Whoever wants to keep notifications longer raises the number; something deleted once cannot be brought back.
- `hosts.reachability.*`: Switch and interval of the core job `host-reachability` (reachability check, see docs/11-ERST-EINRICHTUNG.md §4.1). Every change reschedules the job immediately (`Job.schedule` = `*/N * * * *` from the full hour, 60 = hourly; off = the job is deregistered, the servers' states stay as they are). The job belongs to the setting: `PATCH`/`DELETE /jobs/{id}` on it answer with `409`. Only servers whose state no module maintains (`provider_ext_id` empty) are checked, never example servers. A change produces the event `host.status_changed` (`{host_id, status, previous}`, WS channel `events`, visible with `hosts.read`) and, on failure and recovery, a notification with `payload.path = /hosts/{id}`. The internal marker `hosts.reachability.state` (which failures have already been reported) is not in `GET /settings`.
- `system.update_check.*`: Switch of the daily core job `system-update-check` („Nach Updates suchen“ (Check for updates), 04:41 in the configured zone) and which versions count. The switch reschedules the job immediately; `PATCH`/`DELETE /jobs/{id}` and `POST /jobs/{id}/run` on it → `409` (the button „Jetzt suchen“ (Search now) = `POST /system/updates/check`, with a per-minute throttle; a route via the jobs would bypass it). Privacy: ghcr.io belongs to GitHub, which sees the IP address and the time on every check, nothing else (the user agent `nodvard-deck` names no version); the daily check can be turned off. See „System and backups“.
- `ssh.confirm_new_host_keys`: `true` = Terminal, jobs, and status don't silently remember the key of a server for which none is remembered yet; the connection fails before login data is sent until the fingerprint is confirmed via `POST /hosts/{id}/check` and `POST /hosts/{id}/known-hosts`. `false` = the key is remembered on first connect. `POST /auth/bootstrap` (first account) stores `true`; installations from before have no value, `false` applies. This never affects remembered keys; after `DELETE /hosts/{id}/known-hosts/{key_type}`, confirmation is always needed for this server. If `NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS` is set (`true`/`false`, empty counts as not set), `GET /settings` reports its value, and `PUT` answers with `409`.
- `system.trusted_proxies`: `X-Forwarded-For` (rightmost address that is not itself a registered proxy; if you enter a whole network, any device in it can claim an address) and `X-Forwarded-Proto` apply only from these peers, for HTTP and WebSocket; otherwise they are ignored. Applies immediately. Only the owner may change it; others get `403`, and the attempt appears as `denied` in the log. The normalized notation is stored. If `NODVARD_DECK_TRUSTED_PROXIES` is set (comma-separated), `GET /settings` reports its list, and `PUT` answers with `409`. Guide: docs/12-REVERSE-PROXY.md.
- All changes to this table from `system.timezone` on appear in the log with old and new value (`system.settings.changed`, `target_id` = key). So do the four keys of the approval gate (`autonomy.mode`, `autonomy.max_risk`, `security.deny_patterns`, `maintenance.windows`). A list that is longer than 8000 characters as JSON appears there only with its length (`{"truncated": true, "count": n}`).

### System and backups

All paths are under `/system`. **Viewing** requires `system.read` (the `admin` role has it via `*`). Everything that changes something or hands out data may be done **only by the owner** (`require_owner`: the role alone is not enough, not even `*`, otherwise `403`). Calls marked **[PW]** additionally require `current_password` in the body: if missing → `403`, wrong → `400`, too often wrong → `429` (the same throttling as for `/me/password`). Passwords never appear in the address or in the log. Calls marked **[2FA]** additionally require `totp_code` when two-factor sign-in is on, the current six-digit code from the app (recovery codes don't apply here): if missing → `403` (`code` `totp_missing`), wrong → `400` (`code` `totp_wrong`), already used → `400` (`code` `totp_used`), too many wrong → `429` with `Retry-After` (see „Two-factor code for update and rollback“ below). Without two-factor, `totp_code` is ignored.

| Method, path | Permission | Purpose |
|---|---|---|
| `GET /system/info` | `system.read` | `version`, `build` (exact version of the release image or `NODVARD_DECK_BUILD`, otherwise `null`), `image` (origin without tag, from the file `/app/image-info.json` in the image or `NODVARD_DECK_IMAGE`; `null` for a self-built image), `timezone`, `data_dir`, `data_free_bytes`, `database` (`sqlite`/`postgresql`), `updater_available` (an update helper is set up and answers; same source as `present` in `GET /system/updates/helper`), `pre_update_copies[]` (the newest, at most three **copies of the database from before a migration**, newest first: `name`, `created_at`, `from_version`, `to_version`, `size`; without paths; empty as long as there are none) |
| `GET /system/updates` | `system.read` | State of „Nach Updates suchen“ (Check for updates), **without** a request to the network (from `<data folder>/update_check.json`): `current` (running version: for the official image, its exact version from `/app/image-info.json`, also a pre-release such as `0.6.0-rc1`; otherwise `version`), `latest` (newest version in the channel according to the last successful check; `null` if never checked, or checked but no matching version found, in which case `checked_at` is set), `latest_digest` (sha256 of the manifest, if known), `available` (`latest` newer than `current`, semver), `channel` (`stable`/`beta`), `enabled` (daily check on), `checked_at` (last successful check), `attempted_at` (last attempt), `source` (`cache`; `offline` if the last attempt failed), `error` (then „Konnte nicht prüfen (offline?).“ (Could not check (offline?).); `latest`, `checked_at`, `attempted_at`, and `error` apply only to the selected channel), `official_image` (according to `image`, the image comes exactly from `ghcr.io/nodvard/deck`), `image`, `official_image_name`, `helper` (like `updater_available` in `GET /system/info`), `release_notes_url` (`https://github.com/nodvard/deck/blob/v<latest>/CHANGELOG.md`) |
| `POST /system/updates/check` | `system.read` | Check now; response like `GET`, with `source: live`. If the query fails (offline, `401`/`429`/`5xx` from the registry, broken JSON, timeout), there is **no** error code, but the last state with `source: offline` and `error`. At most **once per minute** for everyone together, otherwise `429` „Gerade erst nachgesehen …“ (Just checked …) with `Retry-After` |
| `GET /system/updates/helper` | `system.read` | State of the update helper, without a request to the network (see „Update helper“ below): `present` (set up and answering, heartbeat at most 90 s old), `reason` (why not: `missing` not set up, `stale` not answering, `unsafe` channel not safe, `proto` different protocol version, `invalid` unreadable status; otherwise `null`), `ready` (could apply an update now), `ready_reason` (fixed identifier from the helper: why it isn't ready, or a hint such as `channel_cluttered`; `finishing`: the last operation is decided, the helper is only cleaning up, `ready` is then `false` and `busy` `null`), `state` (`idle`/`busy`/`unsafe`/`error`), `helper_version`, `heartbeat_at`, `target` (`current_version`, `floating_tag` = `latest` or `X.Y`, `null` for a fixed version; `pinned`: the version is fixed in the Compose file, `X.Y.Z` or digest), `busy` (`id`, `action` `update`/`rollback`, `step`, `since`), `previous` (rollback: `version`, `until`; only as long as it is valid; plus `data_revert`: `true` = the running version rebuilt the database at startup, on rollback everything since `data_since` is lost and `accept_data_loss` is mandatory, `false` = the data stays, `null` = unclear, the rollback is then rejected with `data_unclear`, the same decision as for `POST /system/updates/rollback`; `data_since`: Unix time of the rebuild, only with `data_revert: true`), `last_result` (`id`, `action`, `from`, `to`, `outcome`, `code`, `finished_at`), `pending` (own request without a result, at most 30 minutes old: `id`, `action`, `to`, `at`), `request_options` (what the helper additionally accepts in a request; `["digest"]`: an update works only with `latest_digest` from `GET /system/updates`, without it the interface shows no button but asks to check for updates again). Times as Unix time in seconds, reasons and codes only fixed identifiers, never free text. If `present` is `false`, `ready` is `false`, `request_options` is empty, and all other fields except `reason` are `null`; only `last_result` (for `stale`) and `pending` can still be set. Also records new results from the helper in the log |
| `POST /system/updates/apply` | Owner [PW] [2FA] | `{version, current_password, totp_code?}`: request an update to `version` from the update helper → `202` `{request_id, action: "update", from, to, data_revert: false, digest}` (`Cache-Control: no-store`); the result then appears in `GET /system/updates/helper`. `version` must be the newest one found (`latest` from `GET /system/updates`) and newer than the running one. If the helper can check a digest (its status names `request_options: ["digest"]`), Deck sends along the digest from the last update check. The helper still resolves the floating tag itself and pulls only if it still points to exactly this digest, otherwise it refuses with `digest_changed` (check for updates again); this way, nothing other than what the check showed is ever applied silently. `digest` in the response and in the log (`system.update.requested`) names it; with an older helper it is `null`. `409`: helper missing or not answering (`helper_missing`), still cleaning up (`helper_finishing`), busy (`helper_busy`) or not ready (`helper_not_ready`), channel not safe (`channel_unsafe`), a request already open (`request_pending`), not the official image (`not_official_image`), version fixed in the Compose file (`pinned`), no digest from the update check although the helper could pull one (`no_digest`, check for updates again); `422`: not a valid version (`bad_version`), pre-release (`prerelease`), not the newest found (`not_latest`), not newer than the running one (`not_newer`), doesn't match the tag of the Compose file (`tag_mismatch`); `429` (`rate_limited`) with `Retry-After` |
| `POST /system/updates/rollback` | Owner [PW] [2FA] | `{current_password, accept_data_loss?, totp_code?}`: request the rollback to the version before the last update (`previous` in `GET /system/updates/helper`) → `202` as for `apply`, with `action: "rollback"`. If the running version rebuilt the database at startup, the data goes back with it (`data_revert: true`: everything since the update is lost to the dashboard, the replaced state then lies in the data folder under `restore/replaced-…`): then `accept_data_loss: true` is mandatory (otherwise `422` `accept_data_loss`), and the marker `.boot/rollback.json` is written before the request; if writing the request fails afterwards or the rollback ends with `refused` or `aborted`, it is deleted again. `409` as for `apply` except `not_official_image` and `pinned`, plus `no_previous` (no rollback, or it no longer applies), `data_unclear` (the database was rebuilt, but the entry doesn't match this rollback exactly or the copy is missing), `marker_failed` (the marker couldn't be saved, nothing requested); `429` as for `apply` |
| `GET /system/connection` | `system.read` | How this request arrives: `peer` (direct peer), `client` (address used), `scheme` (`http`/`https`), `via_trusted_proxy`, `forwarded_headers` (request carried `X-Forwarded-*`), `trusted_proxies`, `trusted_proxies_source` (`setting`/`env`), `untrusted_proxies` (`[{address, last_seen}]`: peers from the home network through which a login of the owner with the correct password and `X-Forwarded-For` came in the last 7 days and which are not registered, most recently seen first; only address and time, at most 8, in memory only; filled only for the owner, otherwise empty) |
| `GET /system/openapi.json` | `system.read` | The complete OpenAPI document (all endpoints including bundled extensions); replaces the public `/openapi.json`, which exists only in development mode or with `NODVARD_DECK_API_DOCS=1` |
| `GET /system/backups` | `system.read` | `config`, `key` (`key_id`, `created_at`; never key or password), `target` (`dir`, `default_dir`, `external_root`, `external_available`, `same_storage_as_data`, `free_bytes`, `error`), `backups[]` (`name`, `size`, `created_at`, `app_version`, `key_id`, `mode`, `status` `ok`/`ungeprueft`/`beschaedigt`, `checked_at`, `check_ok`, `key_current`), `last_run`, `running`, `sqlite`, `limits` |
| `PUT /system/backups/config` | Owner, [PW] only for a dangerous change | `{enabled, schedule, keep, dir, include_runs, current_password?}`; `current_password` is **optional** and needed only if `keep` becomes smaller than before, `enabled` goes from on to off, or `dir` changes (otherwise `403` with a text saying which change needs the password; wrong `400`, too often `429` as for the other [PW] endpoints; checked **before** input validation, without a folder being created); `keep` 1–60, `schedule` cron, `dir` only `<data folder>/backups` or `/backups` or below it (absolute, no `..`, no symlink, same `realpath`, writable) → otherwise `422`; `enabled` without a backup password → `409`. Response like `GET /system/backups` |
| `PUT /system/backups/key` | Owner [PW] | `{current_password, password}` (≥ 12 characters, otherwise `422`). Response **once** `{key_id, recipient, recovery_key}` (`Cache-Control: no-store`) |
| `POST /system/backups/run` | Owner | Back up now, as the core job `system-backup` in the background → `202`; if a backup is already running or the key is missing → `409` |
| `POST /system/backups/download` | Owner [PW] [2FA] | `{current_password, mode: "schluessel"\|"passwort", password?, totp_code?}` (the code, because the file contains all keys, including that of two-factor sign-in) → `202` with ticket `{ticket, job_id, status, filename, size, error, expires_in, url}`; builds the file in the background (not a job: its log would contain the parameters). `409` if a backup is running, the key is missing (`schluessel`), or there is no SQLite file; `507` if there is too little space |
| `GET /system/backups/download-jobs/{job_id}` | Owner, own download only | State `building`/`ready`/`failed`, response like the ticket (`404` for foreign, unknown, or already collected downloads). `job_id` is a separate random ID and is **not** suitable for downloading: the interface polls it every second, so it appears in the address (and in the access log), the ticket does not |
| `GET /system/backups/download/{ticket}/status` | Owner, own ticket only | **Deprecated** (`deprecated`), stays as a transition: the same via the ticket in the address. New: `download-jobs/{job_id}` |
| `GET /system/backups/download/{ticket}` | Ticket | Delivers the file as a normal download (streaming). Valid **once**, 5 minutes from completion, only as long as the user is still an active owner; otherwise `404`, `409` during the build. A specially built file is deleted afterwards. A filter on the uvicorn access log (`core/log_filters.py`) replaces the ticket in addresses under `…/system/backups/download/` with `…` |
| `POST /system/backups/{name}/ticket` | Owner [PW] [2FA] | `{current_password, totp_code?}`: ticket for an existing automatic backup (the file stays in place) |
| `POST /system/backups/{name}/verify` | Owner | sha256 of the file against the checksum file `<name>.json` (without a password) → `{ok, detail}` |
| `DELETE /system/backups/{name}` | Owner [PW] | Body `{current_password}`; only `nodvard-deck-sicherung-*.ndbak` with its own header, the `.json` next to it disappears with it → `204`; unknown → `404` |

- **File** `nodvard-deck-sicherung-YYYYMMDD-HHMMSS.ndbak` (local time of the configured zone): line 1 `NODVARD-DECK-BACKUP/1`, line 2 JSON header (`format`, `created_at`, `app_version`, `mode`, for `schluessel` additionally `key_id` and `kdf` = Argon2id parameters with salt), followed by a normal age file containing a tar.gz: `db/lattice.db` (online copy, `integrity_check`), `files/master.key`, `files/vault_keyring.json`, `files/jwt_secret.key` (only without `NODVARD_DECK_JWT_SECRET`), `files/ext/**`, `files/branding/**`, optionally `files/runs/**`, and last `manifest.json` (copy of the header, Alembic heads, extensions, sha256 of each file). Not included: `*-wal`/`-shm`/`-journal`, `setup_code.txt`, `backups/`, `restore/`, `.boot/`, temp files, symlinks, the history `metrics.db`, and Git settings in `ext/`, `branding/`, `runs/` (`core/backup/format.py`): `.gitattributes`, `.gitmodules`, `.gitconfig`, a file `.git`, and under `.git/` everything except objects and packs (`objects/`), `refs/`, `HEAD`, `index`, and `packed-refs`, so neither `config` nor `hooks/`, `info/`, `logs/`. This applies regardless of case, also for Windows forms such as `.git.` or the short name `GIT~1`; `.gitignore` stays, and the Git history itself is still in the backup. Skipped symlinks are not hidden: `last_run.warnings` (list of German sentences, older entries lack the field) names the count and at most five paths („2 Verknüpfungen in Erweiterungsdaten nicht gesichert: ext/a/link, …“ (2 links in extension data not backed up: ext/a/link, …)); the backup still counts as successful.
- A newer format version is rejected with a hint. Caps apply when reading: scrypt log2 N ≤ 18 (18 is used when writing; 2^20 would be 1 GiB and knock a Pi with 1 GB out of memory), Argon2id m ≤ 128 MiB (64 MiB when writing), t ≤ 10, p ≤ 4. Files encrypted yourself with `age`/`rage` with a larger work factor are rejected.
- Log: `system.backup.created`, `.failed`, `.config_changed`, `.key_set` (`key_id`), `.download_prepared`, `.downloaded`, `.verified`, `.deleted`. A failed automatic backup additionally reports as a notification („Sicherung fehlgeschlagen“ (Backup failed), `payload.path` = `/settings/system`). If Nodvard Deck may not write to the folder, the log entry, notification, and job run (`GET /jobs/{id}/runs`, `GET /runs/{id}/output`, channel `runs.<id>`; also readable by viewers) name only the short reason; notification and job run also say that the help is under „Einstellungen“ (Settings) → „System“ at „Sicherung“ (Backup). The whole hint with user number and command appears only in `last_run.error` and `target.error` of `GET /system/backups` (`system.read`) and in the `422` responses of the endpoints under `/system/backups` that only the owner calls (save settings, check, download, delete).
- **Check for updates** (`core/updates.py`): the only source is ghcr.io. Anonymous token (`GET https://ghcr.io/token?scope=repository:nodvard/deck:pull&service=ghcr.io`, works only for a public package), tags (`GET /v2/nodvard/deck/tags/list?n=100`, further pages via the `Link` header, at most 10; a follow-up page on a different host or path counts as an error, the token goes only to ghcr.io). Only tags `1.2.3` count, in the `beta` channel also `1.2.3-rc1`; comparison is by semver with the running version (`current`); deviating from that, parts of a pre-release with a number at the end are sorted naturally (`rc1 < rc2 < rc9 < rc10`). Then optionally the manifest of the newest version (OCI index), whose sha256 is `latest_digest`. At most 5 s per request; token and tags together at most 15 s, the manifest gets only the remaining time (if it isn't enough, `latest_digest` stays `null`, the check still counts). Every response is requested unpacked (`Accept-Encoding: identity`, a packed one counts as an error) and at most 1 MiB is read (a larger `Content-Length` aborts before reading). User agent `nodvard-deck`, without a version. A proxy from the environment (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`) applies here, unlike for extensions (`ctx.http`). Only one check runs at a time. A response without a tag list counts as an error (the last state stays); a complete response without a matching version is a result (`latest: null`). Daily as the core job `system-update-check` (switch `system.update_check.enabled`), plus the button „Jetzt suchen“ (Search now). The repository is a constant in the core (`core.updates.REPOSITORY`). The release image carries origin and version as the file `/app/image-info.json` (build arguments `IMAGE` and `VERSION` in `release.yml`/`deploy/Dockerfile`, `nodvard_deck.image_info`; no environment variable that could travel along when a container is recreated). That is how the interface recognizes a self-built image. Precedence for `image` and `build` in `GET /system/info`: non-empty variable `NODVARD_DECK_IMAGE`/`NODVARD_DECK_BUILD` (old `LATTICE_*`), then the file, otherwise `null`. `current` of the update check (and `from_version`/`to_version` of the pre-update copies), by contrast, takes for the official image first the version from the file, then `build` (each only if it is a version), otherwise `version`: a stale variable doesn't distort the comparison.
- **Update helper** (`services/update_helper.py`, `core/updater_client.py`): a small program of its own next to the dashboard that applies update and rollback itself. The two share only one folder (`/app/updater`, setting `updater_dir`, change only for tests), a separate volume that the helper sets up and that belongs to root: only the helper writes `status.json`, and the dashboard places requests (action, version and, if the helper accepts it, the digest) under `requests/`. If the folder is missing, there is no helper (`present: false`, `reason: missing`). The helper is enabled with its own Compose file ([deploy/README „Update-Helfer“ (Update helper)](../../deploy/README.md#update-helfer), in German). The helper always decides bindingly; what the dashboard checks beforehand ensures a clear response. What is pulled is always the digest that the floating tag in the official repository currently points to; a digest in the request must be the same (`digest_changed`), it selects nothing. The image counts only if its version label is exactly the requested version, this is newer than the running one, and it fits the series in the tag. Whoever has control of the dashboard or the channel therefore cannot apply a different version from the registry, and a recall by resetting the tag takes effect. A request may freely choose the digest only with the signature (`v: 2`). At most **one request per 10 minutes**, update and rollback together, otherwise `429` „Gerade erst angefordert …“ (Just requested …) with `Retry-After`; what wasn't requested (channel not safe, marker not saved) or what the helper refused (`refused`) doesn't count. Refusals of `apply`/`rollback` carry, besides `detail`, a fixed `code` (see table, plus `403` `not_owner`, `password_missing`, and `totp_missing`), for `helper_missing` and `channel_unsafe` also `helper_reason`, for `helper_not_ready` if the helper names a reason. A wrong password answers, as for the other [PW] endpoints, only with `detail`, a wrong or already used two-factor code with `code` `totp_wrong` or `totp_used`.
- **Two-factor code for update and rollback** ([2FA]): the same building blocks as for `POST /auth/mfa`. A code is valid only once per account, also across login and confirmation. Wrong codes count toward the same per-account limit (10 in 15 minutes, 20 in 24 hours, login and confirmation together); after that `429` even with the correct code. If the lock starts at a confirmation, it appears as `auth.password_check_locked` in the log, plus the notification „Zwei-Faktor-Code wird durchprobiert“ (Two-factor code is being guessed). Every wrong code that doesn't already fail at the lock appears as `auth.totp_check_failed` in the log (`detail.aktion` `update_einspielen` or `update_zurueck`, for an already used code `replayed: true`; never the code). During a lock, nothing is checked any more: then there is `429` and no `auth.totp_check_failed` entry. The refusal still appears as `system.update.request_refused` or `system.update.rollback_refused` with `detail.code` `confirm_429` in the log. The per-account limit also applies across a restart (see `POST /auth/mfa`), as does the one for wrong recovery codes at confirmations (10 in 5 minutes per account). After a backup is applied, its log counts. The same applies to loading and applying a backup ([2FA] in the table above).
- Update helper log: `system.update.requested` or `system.update.rollback_requested` (`request_id`, `from`, `to`, for rollback also `copy` and `data_revert`); every refusal as `system.update.request_refused` or `system.update.rollback_refused` with `detail.code` (at the confirmation `outcome` `denied` and `not_owner`, `password_missing`, or `confirm_<status code>`, otherwise `failure` and the response's code). Results from the helper appear in the log **exactly once per request** as `system.update.<outcome>` (actor `system`/`updater`, `target_id` = request, `outcome` `success` for `applied` and `reverted`, otherwise `failure`): `applied` (update applied), `reverted` (rollback applied), `rolled_back` (didn't work, the original version keeps running or runs again), `refused` (refused by the helper), `aborted` (aborted before the new version was started; anything already changed is undone), `failed_manual` (the previous version couldn't be started again, please check by hand), `external_change` (something about the container was changed from outside). Recording happens at startup (if something is still open afterwards, every 30 s, at most 30 minutes), at `GET /system/updates/helper`, and before every new request; which ones are already recorded is stored in `<data folder>/updater_seen.json` (outside the database, so it survives restoring a copy). For `applied`, `reverted`, `rolled_back`, `failed_manual`, and `external_change`, a notification is added (`payload.path` = `/settings/system`).
- The core job `system-backup` is controlled only by this card: `PATCH`/`DELETE /jobs/{id}` on it → `409` (otherwise an admin could silently turn off the automatic backups via `jobs.write`).

**Restore** (owner, `core/backup/restore.py`, `boot.py`): upload → check → mark → restart; applied at startup, **before** the migration. The intermediate state lies under `<data folder>/restore/<id>/`; there is at most one.

| Endpoint | Permission | Purpose |
|---|---|---|
| `GET /system/restore/status` | `system.read` | `{pending, staged, result, replaced, limits, busy}`: marker, intermediate state (**owner only**), result of the last apply (without IP addresses), old state (`replaced`: name, size, `expires_at` = when it is deleted on its own), limits |
| `PUT /system/restore/upload` | Owner [PW in header] | Raw stream (`Content-Type: application/octet-stream`, **not** multipart; otherwise `415`). Header `X-Confirm-Password`: login password **percent-encoded** (UTF-8, `encodeURIComponent`; headers don't know Unicode). Owner and password are checked **before** the body is read (`403` missing, `400` wrong, `429` too often). `Content-Length` above `restore_max_upload_bytes` (4 GiB, `NODVARD_DECK_RESTORE_MAX_UPLOAD_BYTES`) → `413` without reading, likewise when exceeded in the middle of the stream; too little space → `507` before reading; an upload/check is already running → `409`; something is marked → `409`; no backup (header) → `422`. `201` `{id, state: "uploaded", size, header: {mode, created_at, app_version}, expires_in}` |
| `POST /system/restore/{id}/inspect` | Owner | `{password}` **or** `{recovery_key}` (exactly one, otherwise `422`). Decrypts into the staging folder and checks everything. `200` `{id, state: "ready", summary}`; `summary`: `created_at`, `app_version`, `instance_id`, `mode`, `owner_name`, `users`, `hosts`, `extensions[]`, `includes`, `warnings[]` (among others: different installation, older/newer version, missing extensions, no account in the backup, skipped Git settings). Wrong secret `400` (upload stays, one more attempt), too little space `507` (stays), `409` while work is running; any other error (damaged, too new, extension missing, too large `413`, cannot be applied) `422` and the intermediate state is deleted; unknown or foreign `id` `404`. Only **one** backup is decrypted at a time (shares the lock with the backups) |
| `POST /system/restore/{id}/schedule` | Owner [PW] [2FA] | `{current_password, sign_out_all: true, totp_code?}` → `200` marker `{id, source, scheduled_at, expires_in, sign_out_all, backup}`; `restore/pending.json`. Not checked before or something already marked → `409`. Valid for one hour |
| `DELETE /system/restore/pending` | Owner | Discards the marker **and** every intermediate state → `204` |
| `DELETE /system/restore/replaced` | Owner [PW] | Body `{current_password}`; deletes the old state (`restore/replaced-…`, contains old accounts and keys) → `204` |
| `POST /system/restart` | Owner [PW] | Body `{current_password}`. Ends the process after the response with exit code **75**; the container restarts it (rule `restart: unless-stopped`; without the rule the service stays off). → `202` `{restarting, exit_code: 75}` |
| `PUT /auth/bootstrap/restore/upload`, `POST /auth/bootstrap/restore/{id}/inspect`, `POST …/{id}/schedule`, `DELETE …/restore/pending` | Setup code | As above, but without an account and without the password header: **only without an account** (otherwise `409`), header `X-Setup-Code` on every call. `schedule` takes `{sign_out_all}` |
| `POST /auth/bootstrap/restart` | Setup code | like `POST /system/restart`; only if something is marked (otherwise `409`) |

- **Hardening when unpacking:** tar only as a stream (`tarfile.data_filter` per entry, we write every file ourselves with `O_EXCL|O_NOFOLLOW`), names only from an allowlist (`manifest.json`, `db/lattice.db`, `files/{master.key,vault_keyring.json,jwt_secret.key}`, `files/ext/**`, `files/branding/**`, `files/runs/**`), only regular files and folders (no links, hard links, devices, FIFOs), each file once, the manifest last. Git settings in it (the same rule as under „Not included“ above, for example from older backups) are not rejected but **skipped**: read and checked by sha256 against the manifest, but not written. Their count appears in `summary.warnings` („3 Git-Einstellungen aus der Sicherung wurden nicht übernommen, der Verlauf bleibt.“ (3 Git settings from the backup were not applied, the history stays.)), up to five names in the log (`restore_skipped_vcs_entries`). Limits on the **unpacked** stream: entries (`…_MAX_ENTRIES`, 200,000), single file and total amount (`…_MAX_UNPACKED_BYTES`, 16 GiB, never more than the free space minus 128 MiB), against gzip bombs. On **every** error, the staging folder is deleted. Header and manifest must match, sha256 of each file.
- **The SQLite file is not trusted:** opened read-only and immutable (`mode=ro&immutable=1`), `trusted_schema=OFF`, an authorizer that allows only reading, no triggers, views, or virtual tables (Nodvard Deck never creates them), `integrity_check`, and `alembic_version` may contain only revisions this image knows (otherwise `422` „aus einer neueren Version“ (from a newer version) or „Erweiterung … fehlt“ (extension … missing)). The manifest's statements about the heads must match the database. Older known states are fine (the migration follows at startup).
- **Applying at startup** (`python -m nodvard_deck.boot`, called by `entrypoint.sh` instead of `nodvard_deck.migrate`): check the intermediate state again (sha256 of all files, database against the revisions of **this** image), **move** the current state to `restore/replaced-<time>/` (`rename`, also `-wal`/`-shm`/`-journal`), the new one into its place, with `sign_out_all` delete all refresh tokens, delete `setup_code.txt` if there are accounts. A journal (`restore/journal.json`) undoes every error **and** every crash midway; it is **final** only once the migration afterwards has succeeded too (otherwise the old state goes back). `restore/result.json` records the result; `main.lifespan` then writes `system.restore.applied`/`.failed` into the now valid database. Only one `replaced-…` remains; **after 30 days** the core job `restore-cleanup` (daily 03:40) deletes it, and at startup `restore.sweep` does so on its own (`replaced.expires_at` names the day). Abandoned intermediate states and expired markers are deleted after an hour.
- **Permissions:** only the owner (admin without owner `403`), where [PW] is stated above additionally the current password; without accounts only with the setup code. A marker from the wizard is discarded at startup if an account exists by then.
- Log: `system.restore.uploaded`/`.upload_failed`/`.inspected`/`.inspect_failed`/`.scheduled`/`.schedule_failed`/`.cancelled`/`.replaced_deleted`, `system.restart.requested`, after startup `system.restore.applied`/`.failed`. Never a password, key, or code.
- Without the interface: `python -m nodvard_deck.admin restore-backup <datei.ndbak>` checks and marks (password at the terminal or from standard input with `--yes`); `deploy/restore.sh sicherung.ndbak` calls it.

#### Copy before every migration, lock, and emergency page (`nodvard_deck.boot`, `nodvard_deck.rescue`)

In addition to applying a backup (see above), `python -m nodvard_deck.boot` does the following before the application (`backend/src/nodvard_deck/boot.py`, state in `<data folder>/.boot/`, copies in `<data folder>/backups/vor-update/`; neither `.boot/` nor `backups/` is part of a backup):

- **Lock** (`.boot/app.lock`, `flock`): the running application holds it, `boot` and `admin restore-backup` check it. If someone holds it, `boot` ends with **75** and changes nothing (so a `compose run` without `--entrypoint` never applies anything under the running application); `admin restore-backup` then doesn't clear away a running upload of the application. Where `flock` doesn't work, there is a lock without effect.
- **Copy before every migration** (SQLite only): copy the database online (`sqlite3.backup`, then `integrity_check`) to `backups/vor-update/<time>_<from>_<to>.db` (+ `.json`), with at least **1.2 times** the database including WAL free beforehand, **three** are kept. **No migration without a copy** (`no_space`, `no_copy`). Emergency exit `NODVARD_DECK_SKIP_PRE_MIGRATE_BACKUP=1` (the old name `LATTICE_…` still applies only in this version). If the migration fails, the database goes back to the copy (the copy is renamed in the process, not copied); `state.json` records `last_migration` beforehand as `running`, and a crash midway is treated the same way at the next start. The rollback discards the half-finished state in the same start; after a crash, it moves it to `restore/replaced-…` (unless the database is on a different drive and there is no space in the data folder). Newer data (downgrade, explicit rollback) is never discarded: if there is no space, `no_space` follows.
- **`state.json`:** `app_version`, `db_heads`, `started_ok` (set by `main.lifespan` after a successful start; `boot` sets it to `false` with every migration), `last_migration` (`from_version`, `to_version`, `from_heads`, `to_heads`, `copy`, `state`), `revert` (journal of the reset), and `failure` (kind, reason, sanitized log of the last 50 lines: no paths, passwords, SQL parameters).
- **Downgrade detection:** If the image doesn't know revisions of the database (it is newer), the copy comes back automatically only if (1) the last migration started from states this image knows and produced exactly the current state, (2) the copy exists, is intact, and has those states, and (3) the new version never started successfully (`started_ok=false`). Or an explicit marker (`.boot/rollback.json`, valid 24 hours, from the emergency page or from `POST /system/updates/rollback`) is present: then also after a good start; the newer data stays under `restore/replaced-…` (30 days). Otherwise: emergency page.
- **Errors lead to the emergency page**, never to a restart loop: `boot` returns 1, `deploy/entrypoint.sh` starts `python -m nodvard_deck.rescue`; if that fails, the container waits (`exec sleep`). This also applies to an unreadable journal entry or a failed rollback of a restore (`rollback_failed`).

**The emergency page** (`backend/src/nodvard_deck/rescue.py`, **standard library only**, a test checks this via the syntax tree and starts it without installed packages) answers on the application's port:

| Method, path | Response |
|---|---|
| `GET /api/v1/health` | **`503`** `{"status":"rescue"}` (never `ok`: healthcheck, `scripts/deploy_pi.sh`, and a helper thus recognize that something is wrong). Every other path under `/api/` likewise `503` with `{"status":"rescue","detail":…}` |
| `GET <everything else>` | The page (`503`, German, without foreign files). **Without a code**, only general information |
| `POST /rescue/unlock` | Emergency code (`.boot/rescue_code.txt`, like the setup code; appears as a banner in the log) → `303` + cookie `rescue_session` (HttpOnly, SameSite=Strict, 15 minutes). 5 failed attempts per address, 25 in total in 10 minutes, then `429` (with `Retry-After`, response only after 0.5 s) for every further **wrong** code. The correct code always goes through, even during a lock; attempts during a lock don't extend it |
| `GET /rescue/status`, `GET /rescue/log` | with cookie: reason and sanitized log; without: only `{"status":"rescue","unlocked":false}` or `401` |
| `POST /rescue/retry` | with cookie: process exit with 75, the container restarts (restart rule) and `boot` runs once more |
| `POST /rescue/rollback` | with cookie, only if `boot` found a usable copy (otherwise `409`): mark „Stand vor dem Update wiederherstellen“ (Restore the state before the update), then like `retry`. The copy comes from the state, never from the request |
| `POST /rescue/lock` | End the session |

POST requests check `Origin` against `Host`. An action „Kopie herunterladen“ (Download copy) is **deliberately not offered**: handing out an unencrypted database over HTTP is irresponsible, and the encryption (age/`pyrage`) is not part of the standard library. The copy lies in the data folder.

Limits: content at most 4 KiB (otherwise `413`), request line and headers together at most 32 KiB, with fewer than 100 headers (otherwise `431`); 3 s for the headers, 10 s for the whole connection, after which it is closed. At most 64 connections at once, of which 8 per sender (for IPv6 per /64 network); `127.0.0.1` and `::1` (healthcheck in the container) have 8 slots of their own that others cannot occupy.

### Setting up and testing extensions

`GET`/`PUT /extensions/{id}/settings`, `PUT`/`DELETE /extensions/{id}/secrets`, and `POST /extensions/{id}/test` require `extensions.manage`.

- `GET /extensions` and `GET /extensions/{id}` additionally return (additive only, old fields unchanged): `needs_setup` (bool, only for enabled extensions: required fields or required secrets are missing, or the last connection test failed), `setup_reasons` (list of German sentences, empty if everything is fine), and `last_test` (`{ok, message, at}` or `null`; cleared after every change of the settings or credentials). Without `extensions.manage`, the text is missing: `last_test` then contains only `ok` and `at`, and `setup_reasons` names the failure without the reason for it.
- Every entry of `GET /extensions` additionally contains `bundled` (`true` for extensions shipped with Nodvard Deck, i.e. `source == "bundled"`) and `display_version` (for bundled ones the program version, otherwise their own `version`). `version` stays the version from `extension.toml`. `/capabilities` is unchanged.
- Every entry of `GET /extensions` (and `GET /extensions/{id}`) additionally contains `stage` (`stable` or `beta`, from `stage` in the manifest, see [Extension interface, section 1](EXTENSION-API.md#1-anatomy-of-an-extension); if it is missing there, `stable`). Every entry of `GET /pages` also carries the `stage` of its extension. The interface marks `beta` with a badge; for clients it is display only.
- Every entry of `GET /extensions` (and `GET /extensions/{id}`) additionally contains `takeover_requests`: extensions that want to take over the stored data of this row (same or earlier identifier, see [Extension interface](EXTENSION-API.md#renaming-an-extension-legacy_ids)), each `{id, old_id, name, source, permissions, allowed}`; `old_id` is the identifier under which the data lies and can equal `id`. They are loaded only once someone allows it; `allowed: true` means allowed but takes effect only after the next start. Without `extensions.manage`, the list is empty.
- `POST /extensions/{id}/takeover` with `{old_id}` allows the waiting extension `id` to take over (`extensions.manage`, log `extension.takeover_allowed`). Response `{restart_needed: true, switched_off, extra_permissions}`: the takeover applies from the next start, for the source and the permissions `id` has now. If the new one wanted more permissions than the previous one had, its row is now off (`switched_off`), and `extra_permissions` names the additional ones. `404` if `id` isn't waiting for `old_id`; `409` if `old_id` already belongs to another extension or another one may already take it over.
- `POST /extensions/{id}/test` calls the extension's `health()` (for notification channels additionally `test()`); with `{"mode": "message"}`, it instead sends a test message through the channel (`422` if the extension has none). Response `{ok, message, details?}`: `message` is an understandable German sentence („Keine Antwort von 192.168.1.20:8006 – Adresse und Port prüfen.“ (No response from 192.168.1.20:8006; check address and port.), „Zugangsdaten abgelehnt …“ (Credentials rejected …) for 401, „Zugriff verweigert – dem Konto oder Token fehlt ein Recht auf dem Server …“ (Access denied; the account or token lacks a permission on the server …) for 403, „Zertifikat wird nicht vertraut …“ (Certificate is not trusted …), „Adresse nicht gefunden …“ (Address not found …)). From errors of httpcore, h11, idna, and websockets in the cause chain, the test takes no text (they can contain parts of foreign responses); fixed sentences of the extension appear without the preamble „Technische Meldung“ (Technical message). `details` for several connections: `[{name, ok, message}]`. Secrets never appear in the text. A failed test is `200` with `ok: false`. `409`: extension disabled, `404` unknown, `429` after more than 10 tests per minute and user (with `Retry-After`). Every test appears in the log (`extension.test`, without text).
- `DELETE /extensions/{id}/secrets?label=…` removes a secret from `x-secrets` (`422` for foreign labels); log `extension.secret_removed`.
- `PUT /extensions/{id}/settings` `{values}` takes only fields the schema knows and that are not `x-hidden` (the extension maintains those itself). Fields not sent stay as stored, `null` removes a value (the `default` then applies again); the core checks required fields against the state afterwards (a `default` counts as set). If a field to which a secret is bound changes (`x-secret-bound-to`, see [Extension interface, section 1](EXTENSION-API.md#1-anatomy-of-an-extension)), the core deletes that secret, also for the first address (empty → value); a different spelling of the same address doesn't count as a change. The response names the deleted labels in `secrets_cleared` (otherwise `[]`, for `GET` always `[]`), and the log `extension.settings` additionally to `changed` in `detail.secrets_cleared` (only if something was deleted). Fields with `x-widget: "schedule"` must be a valid cron expression (five parts, the same check as when registering the job); otherwise `422` „„<field title>“: Der Zeitplan „…“ ist ungültig. Erlaubt sind fünf Angaben: …“ („<field title>“: The schedule „…“ is invalid. Five parts are allowed: …), and nothing is saved. Empty or `null` stays allowed (the extension's default then applies). The core checks number fields (`integer`, `number`) against `minimum` and `maximum` of the schema (both inclusive, [Extension interface, section 1](EXTENSION-API.md#1-anatomy-of-an-extension)); otherwise `422` „„<field title>“: Erlaubt sind Werte von 2 bis 59. Eingetragen ist -5.“ („<field title>“: Allowed values are 2 to 59. Entered is -5.), and nothing is saved, not even another field of the same request. It rejects `NaN` and infinity for every number field („Bitte eine Zahl eintragen.“ (Please enter a number.)), even without limits. A value outside the selection (`enum`) gives `422` „„<field title>“ muss einer dieser Werte sein: …“ („<field title>“ must be one of these values: …) (with the field's title, not its path); for numbers the type must also match, `"10"` instead of `10` is rejected. A value that was already stored before a new limit stays (no migration); the extension itself clamps it to the limit when reading.
- `PUT /extensions/{id}/secrets` answers for a bound secret with `409` („Erst die Adresse eintragen und die Einstellungen speichern, danach die Zugangsdaten hinterlegen.“ (First enter the address and save the settings, then store the credentials.)) as long as none of the fields has a stored value or `default`. Likewise with `409` („Der Name … kommt mehrfach vor. Erst einen der Einträge umbenennen …“ (The name … occurs more than once. Rename one of the entries first …)) as long as the name of the list entry occurs more than once; `PUT /extensions/{id}/settings` deletes the secret of a duplicate name anyway (after confirmation).
- The old `POST /ext/proxmox|backups/connections/{name}/token` only creates a token (`409` if one already exists or the name occurs more than once). For setting **and replacing**, use `PUT /extensions/{id}/secrets` with `{label: "proxmox-token:<name>", value}`; the interface uses only this path now. The connection's token is deleted by the routes of the two extensions themselves: `POST /ext/proxmox|backups/connections` deletes an old one under the same name, `PUT …/connections/{name}` deletes one if `base_url` points to a different target (compared with `nodvard_sdk.same_target`, a different spelling of the same address doesn't count), and `DELETE …/connections/{name}` deletes that of the removed connection. If there was one, `extension.secret_removed` appears in the log. A secret under the name that doesn't come from the extension (created, for example, via `POST /secrets`) always stays unchanged: `POST` and `PUT` then answer `409` with a sentence naming the secret before anything changes; `DELETE` removes the connection and leaves the secret in place. In general, an extension route that wanted to replace or delete a foreign secret (`SecretNotOwned`) answers with `409` and a sentence about it, not `500`.
- **Certificate fingerprint** (`proxmox` and `backups`, added): The connection routes additionally accept `tls_fingerprint_sha256` (64 hex characters, colons welcome; stored as `AB:CD:…`; invalid → `422`; empty or `null` on `PUT` removes it). A set fingerprint turns off `tls_insecure_skip_verify`; if `base_url` points to a different target on `PUT`, it is dropped. `GET …/connections` additionally returns `tls_fingerprint_sha256` (`null` if none is remembered) and `tls_fingerprint_invalid` (something invalid is in the settings field; the connection is then blocked). Confirming after a change:

  ```
  GET  /ext/proxmox|backups/connections/{name}/certificate → 200 {sha256, trusted, subject, issuer, not_after,
                                                                  stored, stored_invalid, matches}
  POST /ext/proxmox|backups/connections/{name}/certificate {sha256} → 200 like GET
  ```

  `GET` only reads the certificate (TLS handshake, nothing is sent): `sha256` is what the server shows now, `stored` is the remembered fingerprint, `matches` whether both are equal. `POST` reads it again and saves only if `sha256` matches exactly what the server shows at that moment; otherwise `409 {detail, code: "certificate_mismatch", sha256}` with the current value. `tls_insecure_skip_verify` is turned off in the process; log `proxmox.certificate_confirmed` or `backups.certificate_confirmed` with connection, address, old and new fingerprint. Both need `secrets.write`. `400` no fingerprint, `404` unknown connection, `502` server not reachable. A connection with a remembered fingerprint whose server shows a different certificate reports `code: "certificate_changed"` in the state (`details[<name>]`), one with an invalid one `certificate_invalid`.

### Actions: the confirmation workflow

```
GET    /actions?status=proposed          → the cards in the incident feed
GET    /actions/{id}                     → query the state of a (running) action
POST   /actions/{id}/approve?wait=0..20  → starts execution (Idempotency-Key!)
POST   /actions/{id}/reject              → {reason}
POST   /actions/{id}/dismiss
```

A `POST` to a state-changing resource answers:

- `200`: executed (autonomy mode `full`, risk below threshold)
- `202`: **created as a proposal**, the body contains the `action` resource
- `403`: rejected by the gate, the body names the rule that applied
- `409`: proposal expired
- `422`: only for `POST /hosts/{id}/actions/{action}`: the values in `payload` don't match the action's `params_schema` (wrong type, required field missing, value outside `enum`, pattern, or limits). `detail` is a German sentence that names the field and says what is wrong. No action is created then and nothing goes to the gate. The check covers `type`, `enum`, `pattern`, `minLength`/`maxLength`, `minimum`/`maximum`, `properties`, `required`, `additionalProperties`, `items`, and `minItems`/`maxItems`; other keywords are not checked (not rejected). The action's executor still checks bindingly.

That `202` is the normal case follows from the action gate ([Architecture, section 4](ARCHITECTURE.md#4-the-action-gate)): proposing and executing are separate steps. Clients must handle `202`, the Flutter app just like the web.

**Background execution.** Approved actions no longer run inside the HTTP request (an update takes up to 45 min). `approve` and `POST /hosts/{id}/actions/{type}` (in `full` mode) start the execution and wait at most `wait` seconds (default 20) for the result:

- finished → `200` with the `action` resource as before (`succeeded`/`failed`)
- not finished yet → `202` with `status = "executing"`, header `Location: /api/v1/actions/{id}` and `Retry-After: 3`; the client polls `GET /actions/{id}` until a final status is there. `wait=0` answers immediately (for bulk approvals).

An error in the background always ends in `failed` with `result.error` (only those with `hosts.execute` see the text, see below); if the dashboard is shut down in the meantime, the core records the action as aborted, and at the next start it sets leftover `executing` rows to `failed`. The event `action.executed` comes only after the result and audit row are saved.

**Output only with `hosts.execute`.** Output and error text of an execution can contain arbitrary text from the server (e.g. the result of a shell command). The `action` resource therefore returns them only to users with `hosts.execute` (built in: „Bediener“ (operator), Admin, Owner), in `GET /actions`, `GET /actions/{id}`, the responses of `approve`, `reject`, and `dismiss`, and of `POST /hosts/{id}/actions/{action}`. All others, e.g. the viewer, see status, times, and participants; of `result`, only `success`, `exit_code`, and `duration_ms` remain; the other fields the result has stay, but empty (`output` and `error` as `null`, `detail` as `{}`). The field `output_hidden` is then `true` if something was actually omitted; with `hosts.execute` it is always `false`.

**Command only with `hosts.execute` or approval permission.** An action's `payload` (shell command, script content, parameters) can contain credentials. The `action` resource returns it in full (in the same responses as above) only to users with `hosts.execute` or with `actions.approve:<risk>` for the risk of this action: whoever approves an action must see its command. Everyone else gets from the `payload` only identifiers from a fixed list (`host_id`, `vmid`, `node`, `connection`, `job_id`, `script_id`, `unit`, `container`, `project`, `service`, `image`, `snapname`, `storage`, `minutes`), and even those only as text up to 200 characters, number, or `true`/`false`; command, script content, and parameters are missing. The field `payload_hidden` is then `true` if something was actually omitted, otherwise `false`. A stricter rule applies to the `events` channel (§4).

For the log (`action.executed`), the core writes from the result under `detail.result` only `success`, `exit_code`, `duration_ms`, and the length of output and error text (`output_length`, `error_length`), never the text. If the gate determines the reason itself (no executor, timeout, aborted, interrupted at startup, or an exception), a fixed sentence in `gate_error` accompanies it; for an exception it names only its kind, the action has the full text. `GET /audit` and `GET /audit/export` also sanitize, for users without `hosts.execute`, older entries that still contain output: for `action.executed`, `detail.result` keeps only the values named, all other fields are empty (`output` and `error` are always `null` in it, `detail` is `{}`); in all entries, the fields `output`, `stdout`, `stderr`, and `command` (also nested) become `null`, because a command, like an action's `payload`, can contain credentials. If something was removed in the process, `GET /audit` sets `output_hidden: true`; the export doesn't have this field.

**Names instead of identifiers.** Besides the raw fields (`proposed_by_type`/`proposed_by_id`, `approved_by_user_id`), the `action` resource carries two readable ones: `proposed_by_label` (username, name of the extension, `System`, `Zeitplan` (schedule), or `KI` (AI)) and `approved_by_label` (username of the decider; `null` as long as nobody has decided). Actors that cannot be resolved (deleted user, removed extension) get the raw value `kind/identifier` in `proposed_by_label`, and in `approved_by_label` the user identifier as stored (`approved_by_user_id`, without the `user/` prefix). Only the name is resolved, with one query for all rows of a list. Usernames of **other** users are given only to those with `users.read` or who may decide on actions (any `actions.approve:<risk>`, e.g. „Bediener“ (operator)); the viewer still sees `user/<uuid>` there (or the bare `<uuid>` for `approved_by_label`), since `GET /users` is also denied to them. Everyone sees their own name; names of extensions as well as `System`/`Zeitplan`/`KI` are not user data and are open to everyone. Extensions get the same for their own rows via `ctx.actions.proposer_labels(rows)` (the lookup goes by `id`, not by the rows passed in). There the core doesn't check who calls the page; the extension's route must be secured itself.

**Without a click via a standing approval.** If an action ran via a standing approval ([Architecture, section 4](ARCHITECTURE.md#4-the-action-gate)), `gate_decision.rule` is `standing_approval`, `approved_by_user_id` or `approved_by_label` name the person who granted it, and `reason` ends with „– ohne Klick, lief mit Dauerfreigabe vom <date> durch <username>“ (– without a click, ran with standing approval from <date> by <username>). If the gate didn't recognize it, it is a normal proposal with the reason in `reason` and in `gate_decision.standing_approval_rejected` (fields: [03 §5](../03-DATA-MODEL.md#5-aktionen--das-gate-journal), in German).

### Standing approval for scheduled scripts (extension `scripts`)

```
POST   /ext/scripts/scripts/{id}/standing-approval   → 200 script (like GET)   {fingerprint, targets_fingerprint}
DELETE /ext/scripts/scripts/{id}/standing-approval   → 204, withdraw the approval (takes effect immediately)
```

Both need `actions.standing_approval` (built in: Admin and Owner; „Bediener“ (operator) and viewer get `403`). Granting works only for active scripts with a schedule. Both fields in the body are mandatory and come from the last response of `GET /ext/scripts/scripts` or `GET /ext/scripts/scripts/{id}`: `fingerprint` (content, parameters, target, schedule) and `targets_fingerprint` (the target servers with account, address, and SSH port). If any of that has changed since, `POST` answers with `409` and approves nothing; likewise without a schedule, with the script switched off, or without a server it can run on. `DELETE` without an approval: `404`. Granting, withdrawing, and expiring appear in the log (`scripts.standing_approval.granted`, `scripts.standing_approval.revoked`, `scripts.standing_approval.expired`).

`PUT /ext/scripts/scripts/{id}` (save script) checks a schedule before saving: an invalid one gives `422` „Der Zeitplan „…“ ist ungültig: …“ (The schedule „…“ is invalid: …), and nothing lands in the version history. An invalid schedule from an older version no longer cripples the extension at startup: the script then doesn't run on schedule for the time being, and a notification is added. Likewise, for an invalid stored schedule, Nodvard Shield uses its default for the time being and reports it.

`GET /ext/scripts/scripts` and `GET`/`PUT /ext/scripts/scripts/{id}` additionally return `fingerprint`, for active scripts with a schedule `standing_preview` (the servers an approval would cover now, each `id`, `name`, `account`, `address`, `port`) and `targets_fingerprint`, plus `standing_approval`: `null` without an approval, otherwise `active`, `problem` (why it no longer applies), `granted_by_label`, `granted_at`, `hosts` (as for `standing_preview`), and `new_hosts` (names of new target servers for which it doesn't apply). Entries of `GET /ext/scripts/scripts/{id}/runs` additionally carry `standing_approval` (ran without a click) and `reason`.

### Update proposals for many servers and hardening audit (extension `shield`)

```
POST /ext/shield/defender/updates/propose-all   → 200 {mode, results[], counts}   permission: soc.manage
POST /ext/shield/defender/audits                → 200 {hosts, started, running, audits}   permission: soc.manage
```

**`propose-all`.** Body: `{"mode": "security" | "all", "host_ids": ["…"]}`. `host_ids` is optional; without it, every server of the update overview that has something open for `mode` applies (`security`: security updates, `all`: updates). Cleanup and reboot are not offered here (`422`); both remain a per-server decision (`POST /ext/shield/defender/hosts/{id}/upgrade`).

The route only creates proposals (gate, action „System-Updates einspielen“ (Apply system updates)); approval works as for the single path under „Actions“, in bulk, except for high risk (dist-upgrade). If the automation is set to „Selbstständig handeln“ (Act on its own), approved actions start on their own. Every proposal is created exactly like the single proposal (plan from the stored update state, command from the fields, with the setting „Proxmox: Alle Updates als dist-upgrade“ (Proxmox: all updates as dist-upgrade) risk `high`). The proposals are created one after another and without waiting for approved actions; the route opens no SSH connection.

`results[]`: per server `host_id`, `name`, `result` (`proposed`, `skipped`, `failed`), for `skipped`/`failed` `reason` (German sentence; for an unexpected error a fixed sentence, details in the dashboard's log), for `proposed` `action_id`, `status` (`proposed`, with automatic approval `executing`), and `risk`. `skipped`: nothing open, never checked, not supported, apply run active, or an open update proposal already exists for the server. `failed`: plan or gate failed; the other servers are unaffected. `counts`: `proposed`, `skipped`, `failed`, `total`. If a bulk proposal is already running, the route returns `409`. The log contains an entry `shield.updates_proposed_all` (mode, count, proposed servers). The route finds open proposals among the extension's last 200 actions.

**`audits`.** Body `{"host_ids": [...]}` or `{"host_ids": "all"}` (default: all of Shield's servers). The response comes immediately: `started` (newly started), `running` (was already running, no second one starts), and `audits` (per server `requested_at`, `started_at`, `waiting` (three audits are already running), `already_running`). Lynis runs on the server decoupled from the SSH channel, with low priority and for at most 3 hours; the result comes later in `GET /ext/shield/defender/audits`, also after a dashboard restart. `GET /ext/shield/defender/overview` names a running audit per server (`audit_run`).

The old addresses `/ext/nexus-soc/…` answer the same (deprecated, see §7).

### Connecting Proxmox with a password (extension `proxmox`)

```
POST /ext/proxmox/connect/certificate            → 200 {base_url, certificate, backups_available}
POST /ext/proxmox/connect                        → 200 {status: "connected", …} or {status: "tfa_required", tfa}
POST /ext/proxmox/connect/tfa                    → 200 like /connect
GET  /ext/proxmox/connections/{name}/old-tokens  → 200 {connection, users[], tokens[], privsep_off[], backups_checked}
```

All four need `secrets.write` (built in: Admin and Owner). The password goes into exactly one login at Proxmox, only to the confirmed certificate, and is never stored; flow and permissions: [Proxmox token guide](../10-PROXMOX-TOKEN.md#schneller-weg-mit-passwort-verbinden), in German. Every attempt in which a password or code went out appears in the log (`proxmox.connect_with_password`, without secrets).

| Endpoint | Field | Meaning |
|---|---|---|
| `connect/certificate` | `base_url` | Address of the node; without a scheme `https://` applies, without a port 8006. Only reads the certificate, sends nothing. |
| `connect` | `base_url` | as above |
| | `username` | default `root@pam`, always with a realm |
| | `password` | at most 512 characters |
| | `name` | short name of the connection, optional (empty: name of the cluster or node) |
| | `fingerprint` | the compared SHA-256 fingerprint; required if `certificate.trusted` is `false` |
| | `access` | `full` (default, „Ansehen und steuern“ (View and control)) or `read` |
| | `backups` | `true`: also create a credential for Proxmox Backup (if the module is off, `notes` says so), `false`: don't; not given: if the module is on |
| `connect/tfa` | `flow` | from `tfa.flow` of the `tfa_required` response (valid 180 s, only for the user who started) |
| | `code`, `kind` | the code; `kind` `totp` (default), `recovery`, or `yubico` |

**Responses.** `certificate`: `{sha256, trusted, subject, issuer, not_after, alt_names}`; `backups_available`: whether the Proxmox Backup module is on. `tfa_required`: `tfa` = `{flow, kinds, expires_in_s}`. `connected`: `connection` (`name`, `base_url`, `token_id`, `tls_insecure_skip_verify` (now always `false`), `tls_fingerprint_sha256` (the confirmed fingerprint for a certificate that isn't recognized, otherwise `null`), `tls_fingerprint_invalid`, `enabled`, `has_token`), `created` (what was newly created in Proxmox, German sentences), `nodes`, `access`, `privileges` (permissions of the role), `replaced_token_id` (previous token of an existing connection or `null`), `backups` (`null` or `{name, token_id, replaced_token_id, privileges, storages, pool, tls_insecure_skip_verify, tls_fingerprint_sha256, enabled}`; token of `nodvard-backup@pve`; an existing connection is never weakened in the certificate check, remembers the confirmed fingerprint, and stays off if it was off), `old_tokens` (as for `old-tokens`), `discovery` (`started`: reading runs in the background, `not_started`), `notes` (German sentences). `old-tokens` reads with the connection's stored token (no password, changes nothing): `users` the users read (`nodvard@pve`, plus `nodvard-backup@pve` if Proxmox Backup could be queried), `tokens` each `{token_id, user, name, privsep, command}`, only tokens of these users with the names Nodvard Deck assigns (`deck…`) that no connection uses any more; `command` is the `pveum` command to delete it. `privsep_off`: tokens of these users without privilege separation. `backups_checked`: whether Proxmox Backup could be queried (otherwise its tokens never count as old). `404` unknown connection, `502` Proxmox not readable.

**Errors** (`connect…`): body `{detail, code}`, plus depending on the case `certificate` (the certificate the server shows now), `created` (what was left created in Proxmox), `leftover_token`/`leftover_tokens` (new tokens that may have been left in Proxmox, for deleting yourself), and `retry_after` (also as a header). `detail` is always a fixed German sentence, never text from Proxmox.

| `code` | Status | Meaning |
|---|---|---|
| `invalid_address`, `invalid_name`, `invalid_user`, `invalid_password`, `invalid_code`, `invalid_fingerprint` | 400 | input invalid, nothing sent |
| `https_required` | 400 | `http://` address, nothing sent |
| `name_taken`, `already_connected` | 409 | short name belongs to a different server or address entered under a different name |
| `name_twice` | 409 | short name appears in the settings for two connections; rename one first. Checked before login, otherwise the new token in Proxmox is deleted again |
| `certificate_unconfirmed` | 409 | certificate not recognized and no `fingerprint`; password not sent |
| `certificate_changed` | 409 | different certificate than confirmed, before login; password not sent |
| `certificate_changed_after_login` | 409 | different certificate after login; the password went only to the confirmed one, then abort |
| `wrong_password`, `login_rejected` | 400 | login rejected |
| `tfa_unsupported` | 409 | second factor without a typeable code (WebAuthn/U2F) |
| `tfa_wrong` | 400 | code wrong; start over with the password |
| `tfa_expired` | 409 | operation expired or foreign |
| `not_allowed` | 403 | the Proxmox account may not create users, roles, or tokens |
| `redirect` | 502 | Proxmox redirects; Nodvard Deck doesn't follow |
| `unreachable`, `no_tls`, `connection_failed` | 502 | server not reachable, no https, connection aborted |
| `proxmox_error`, `probe_failed` | 502 | error while creating, or new credential not usable; nothing saved |
| `secret_foreign` | 409 | the vault holds under `proxmox-token:<short name>` a secret that doesn't come from the extension (for example via `POST /secrets`); it stays unchanged, nothing saved, new tokens removed again |
| `save_failed` | 500 | saving in Nodvard Deck failed; new tokens removed again |
| `rate_limited`, `busy` | 429 | too many attempts (`retry_after`) or already running |

### Proxmox Backup: restore as a copy and cause of failure (extension `backups`)

```
GET  /ext/backups/restore/backups?connection=&vmid=&kind=   → 200 {vmid, name, kind, exists, backups[], errors[]}   permission: hosts.execute
GET  /ext/backups/restore/options?connection=&volid=        → 200 {kind, nodes[], suggested_vmid, suggested_name, blocked, refuse, warnings[], …}   permission: hosts.execute
POST /ext/backups/restore                                   → 200 {action_id, status, risk, warnings[]}   permission: hosts.execute
GET  /ext/backups/jobs/{job_ref}/failure?upid=              → 200 {upid, node, status, key, what, todo, detail, …}   permission: hosts.read
```

**Restore as a copy.** Creates a **new** guest (VM or container) from a Proxmox backup. The original is never touched and never overwritten. The copy gets a new number and new MAC addresses and is created in the fixed pool `nodvard-kopien`; autostart is off, it doesn't start on its own. All three routes need `hosts.execute`, because the selection shows fixed addresses and passed-through devices from the guest configuration. What Proxmox needs for this (version, pool, token permissions): [Proxmox token guide](../10-PROXMOX-TOKEN.md#wiederherstellen-als-kopie-backups), in German. Times (`ctime`, `backup_at`) are Unix seconds and sizes are bytes, both as Proxmox delivers them (not ISO 8601).

| Endpoint | Field | Meaning |
|---|---|---|
| `restore/backups` | `connection` | Name of the backup connection (required) |
| | `vmid` | Number of the guest, 100 to 999999999 (required) |
| | `kind` | `qemu` or `lxc`, optional. Without it, the guest's kind as Proxmox knows it now applies; if it no longer exists, both kinds are returned |
| `restore/options` | `connection` | as above |
| | `volid` | Identifier of the backup, from `backups[].volid` (required) |
| `restore` (body) | `connection`, `volid` | as above |
| | `node` | Target node; must be online and see the backup |
| | `storage` | Target storage on this node; must be allowed to hold VM disks or containers, be on, and have enough space |
| | `vmid` | new number, 100 to 999999999 (number or text) |
| | `name` | Name of the copy: letters without umlauts, digits, hyphens, and dots, at most 63 characters, must not start or end with a hyphen |
| | `link_down` | `true` (default): the copy's network cards are created disconnected, because in the guest it has the same IP setting as the original; reconnecting is possible in Proxmox. `false`: don't disconnect |

**Responses.** `restore/backups`: `vmid`, `name` (current name of the guest or `null`), `kind`, `exists` (whether the guest still exists in Proxmox), `backups[]` (newest first; each `volid`, `storage`, `kind`, `kind_label` (`VM` or `Container`), `ctime`, `size`, `notes` (single line, at most 120 characters or `null`), `protected`), and `errors[]` (per backup storage that couldn't be read, a sentence „<storage>: <reason>“; the other storages are still in `backups`).

`restore/options` changes nothing and returns what a dialog needs: `kind`, `kind_label`, `source_vmid`, `source_name`, `source_exists` (the original), `backup_at`, `backup_size`, `disk_bytes` (rough upper bound of the disk size according to the backup or `null`), `nets[]` (network cards according to the backup: `key`, `bridge`, `tag`, `addresses[]`, fixed addresses the copy inherits), `warnings[]` (hints, for example about fixed addresses or passed-through devices), `suggested_vmid` (a free number under which neither old backups lie nor an HA rule exists, otherwise `null`), `suggested_name`, `pool` and `keep_hint` (the copy's pool and a sentence on how to keep it permanently), `default_node`, `default_storage` (`null` if nothing fits), and `nodes[]`: for each node that can see the backup, `node` and `storages[]` with `storage`, `type`, `avail`, `total`, `need_bytes` (minimum need: backup + 10 % + 2 GB, for `lvm` at least disk size + 2 GB; `null` if the backup's size is unknown), `fits` (`avail` is enough for it), and `warning` (sentence if the space is enough only with thin provisioning). Fitting storages come first, then the one with the most free space. Two fields block the dialog, otherwise they are `null`: `blocked` (sentence if a copy doesn't work even before any input: Proxmox too old (VMs need VE 7.2, containers VE 6), or the pool or a permission on it is missing) and `refuse` (sentence if exactly this backup doesn't work because it passes through devices or folders of the server).

**Proposal.** `POST /ext/backups/restore` checks everything that is also checked on execution (node online, backup readable, Proxmox version, number free and not that of the original, no old backups and no HA rule under this number, target storage and space, token permissions), and then creates a proposal with **high risk** (action `backup.restore_copy`). If something doesn't fit, no proposal is created at all. The route answers `200` even for a proposal: `action_id`, `status` (state of the action after the gate, as under „Actions“; normally `proposed`, the action waits for approval), `risk` (`high`), and `warnings[]` (hints from the checks). Only roles with `actions.approve:high` (built in: Admin and Owner) may approve; `GET /actions/{id}` shows the state.

On execution, everything is checked afresh once more. If the kind, the original's number, or the time of the backup have changed since the proposal (the backup was replaced), the action fails without creating anything. After creating, it waits for the Proxmox task (at most 45 minutes) and finishes setting up the copy (disconnect network cards, check name and autostart). If it ends earlier (time limit, no answer from Proxmox, dashboard restart), the backup watcher catches up on the finishing. There is exactly one notification per copy; nothing is ever deleted in Proxmox. The log contains `backups.restore_copy` (`target_type` `restore`, `target_id` `<connection>/<new number>`, `outcome` `success` or `failure`).

**Errors** of the three restore routes: `detail` is a German sentence. If a required value is missing entirely (query parameter or body unreadable), the usual FastAPI 422 comes, where `detail` is the list of validation errors.

| Status | Meaning |
|---|---|
| 404 | Connection unknown (for `POST` also: `connection` missing) |
| 422 | Input invalid (number of the guest, identifier of the backup, node, storage, new number, name, `link_down`) |
| 409 | Check failed, nothing was created: Proxmox not reachable; backup not readable from this node or gone; kind not recognizable or very old tar format; Proxmox too old; new number already taken, equal to the original's, or occupied by old backups or an HA rule; target storage unsuitable, switched off, or too full; devices or folders of the server passed through (`refuse`); pool or permission missing |

**Cause of failure.** `GET /ext/backups/jobs/{job_ref}/failure` explains in plain language why the newest run of a backup job failed for a guest. At the end, Proxmox usually reports only „job errors“; the reason is in the task log.

| Field | Meaning |
|---|---|
| `job_ref` (path) | `<connection>--<job>--<VMID>`, as in the rows of `GET /ext/backups/jobs` |
| `upid` (query) | optional: a specific failed run (`upid` from `GET /ext/backups/jobs/{job_ref}/history`); without it, the newest failed run of this job for the guest |

Response: `upid`, `node`, `started_at`, `finished_at` (Unix seconds), `status` (outcome according to Proxmox), `log_readable` (whether the task log could be read; without the Proxmox permission `Sys.Audit`, only the status counts), `key` (kind of cause), `what` (what that means) and `todo` (what to do; both fixed German sentences), and `detail` (the line from Proxmox that best describes the error, shortened to 300 characters, otherwise the status; `null` if there is nothing). `key` is one of `no_space`, `target_lost`, `storage_offline`, `locked`, `pbs_auth`, `guest_agent`, `vm_timeout`, `missing_right`, `permission`, `io_error`, `guest_missing`, `aborted`, `unreachable`, `other_guest` (a different guest in the same job failed), or `unknown` (no explanation known, `detail` remains). `404`: `job_ref` invalid or no failed run found; `409`: connection not (or no longer) registered or Proxmox not readable.

### Address check for proxy hosts (extension `npm`)

```
GET /ext/npm/address-check   → 200 {state, message, checks[], problems}   permission: hosts.read
GET /ext/npm/summary         → 200 state of Nginx Proxy Manager (hosts[], certificates)   permission: hosts.read
```

Until 0.8, this belonged to the „Netzwerk“ (Network) module. The old addresses `GET /ext/network/npm/address-check` and `GET /ext/network/npm` still answer the same (deprecated, see §7) as long as the module `npm` is on.

Which Docker container does each proxy host's target in Nginx Proxy Manager lead to, and is it running? The route only reads, has no parameters, and always answers `200`: states such as „not set up“ appear in `state`, never as an error status. The containers come from the Docker module (capability `ServiceCatalog`), which asks every Docker server via SSH for this.

| `state` | Meaning |
|---|---|
| `ok` | checked; `checks` has one entry per proxy host. `message` is `null`, except if no server is marked as a Docker server: then a sentence appears there, and all entries are `unknown` |
| `not_configured` | Nginx Proxy Manager is not (completely) entered; `message` says what is missing |
| `no_catalog` | the Docker module is switched off or not loaded, there is no check |
| `unreachable`, `auth_failed` | Nginx Proxy Manager doesn't answer or rejects the login |
| `docker_unreadable` | the Docker module returned no container list at all (error or time limit); `message` names the reasons. If individual or all Docker servers are unreachable, the Docker module returns placeholders for them: then it stays `ok` with `unreachable` entries. Reading may take at most 45 seconds per container-list provider, after which „Zeitüberschreitung nach 45 s“ (Timeout after 45 s) appears there. If it succeeds only partially, the check uses what was read |

Except for `ok`, `checks` is empty and `problems` is `0`. An entry of `checks[]` has:

| Field | Meaning |
|---|---|
| `proxy_id` | ID of the proxy host (`hosts[].id` in `GET /ext/npm/summary`) |
| `status` | with a container: its state (`running`, `stopped`, `restarting`, `paused`, `removing`; another Docker state appears verbatim, `unknown` if it is missing). Without a container: `unknown` (no target, no Docker target, or nothing found), `ambiguous` (several containers match), or `unreachable` (the server was not reachable, not checked) |
| `tone` | `good` (running), `bad` (container stopped), `warn` (restarts constantly, is paused, is being removed, or has a different state; also: server not reachable), otherwise `neutral` |
| `text` | finished German sentence |
| `container` | Name of the assigned container or `null` |
| `docker_host` | Name of the server or `null` |
| `docker_host_id` | Server ID, only if a container is assigned |
| `can_start` | `true` if the assigned container is stopped (`exited`/`created`) and can be started |

`problems` counts the entries with `tone` `bad`. A switched-off proxy host is still assigned but is never `bad` or `warn` and offers no starting: that nothing runs behind it is then usually intended. Likewise for a merely presumed match (a container name matches, but the Proxy Manager's server is unknown; the sentence begins „Führt vermutlich …“ (Probably leads to …)).

**Assignment**, in this order: (1) If the target (`forward_host`) is the address or name of a Docker server, the container on that server that publishes `forward_port` counts; if exactly one of several is running, it wins. (2) `host.docker.internal` and Docker gateways (`172.17.0.1` etc.) count as the server Nginx Proxy Manager runs on; `localhost` only if it runs in the host network. (3) Otherwise the target counts as a container name (or Compose service) on the Proxy Manager's server. Several matches give `ambiguous`, none `unknown`; that is not an error, the target can be a service outside Docker. Additional locations (`locations`) of a proxy host are not checked. A client can start via the server's action path: `POST /hosts/{docker_host_id}/actions/container.start` with `{"payload": {"container": …}, "reason": …}`, going through the gate like any action.

### Antivirus watcher in real time (extension `shield`)

```
GET  /ext/shield/defender/realtime/readiness?refresh=   → 200 {hosts[]}                       permission: soc.read
POST /ext/shield/defender/realtime/{host_id}            → 200 {action_id, status, risk}      permission: soc.manage
```

Without real time, the antivirus watcher checks the most recently changed files every minute. In real time, clamonacc (fanotify) reports every new file to clamd immediately; the watcher run then only picks up its log and checks every finding once more via clamd. There is no interface for this yet, only this API.

**`realtime/readiness`** changes nothing. For each server, it reads whether real time would be possible there (remembered for ten minutes, `refresh=true` queries anew). Fields per entry in `hosts[]`:

- `host_id`, `host_name`, `reachable`, and `possible`.
- `reasons[]`: why not possible, German sentences.
- `notes[]`: hints.
- `container`: kind of virtualization if the server is a container. Real time doesn't work then.
- `mem_total_mb` and `mem_available_mb`.
- `fanotify`.
- `clamd_installed`, `clamonacc_installed`, `clamd_active`, and `clamonacc_active`. `false` also for a restart in a loop, `null` means unknown.
- `root`: `root`, `helper`, `sudo`, or `none`.
- `apt`.
- `error` if the server is not reachable.

In addition, the remembered state:

- `realtime_requested`: switched on.
- `realtime_down_since`: timestamp (ISO 8601) since which the watcher run checks every minute again because clamonacc is not running there. Otherwise `null`. It stays switched on, and if clamonacc runs again, real time applies again.
- `realtime_on`: switched on, `clamonacc_active` is not `false`, and there is no fallback.

**`realtime/{host_id}`** with body `{"enable": true|false}` creates a proposal „Viren-Wächter in Echtzeit“ (Antivirus watcher in real time) (medium risk). Approval happens under „Aktionen“ (Actions). Beforehand, the route checks:

- When switching on, it requires `possible` and an installed clamd, otherwise `409` with the reason. Nodvard Deck does not install clamd itself.
- The folders come from the settings `watch_paths` and `exclude_paths`.

The new state is remembered only once the server confirms the change. If clamonacc does not start, the server cleans up again, and the watcher counts as switched off. `404` is returned for an unknown server.

### Watcher templates for the real-time watcher (extension `shield`)

```
GET  /ext/shield/defender/watch-templates   → 200 {suggestions[], errors[], watch_paths[]}   permission: soc.manage
POST /ext/shield/defender/watch-paths       → 200 {added, covered_by, watch_paths[]}          permission: settings.write
```

Without custom configuration, the real-time watcher checks only locations where malware typically lands (`/tmp`, `/var/tmp`, `/dev/shm`, `/home`, `/root`, `/opt`). The templates find folders where external files land—Samba and NFS shares and the data folder of a Nextcloud instance—and add them to the `watch_paths` setting on click. The setting applies to all servers; a folder that does not exist on a server costs nothing there. A large folder costs time on every run of the watcher.

**`watch-templates`** has no parameters, changes nothing, and answers with `200`. It searches read-only on the servers checked by antivirus protection (managed Linux servers with credentials, and if a tag is set, only those): `smb.conf`, `/etc/exports`, Nextcloud's `config.php`, and Docker volumes with „nextcloud“ in their name. It runs as root if possible without a password, otherwise as a normal user (in which case Nextcloud is usually missing); at most 20 seconds per server, up to four servers simultaneously. It filters out Samba printers and `[homes]`, paths with placeholders (`%U`), entire system folders (`/etc`, `/usr`, `/var`, and similar), and everything under `/proc`, `/sys`, `/dev`, `/run`, and `/boot`.

- `suggestions[]`: per folder `path`, `kinds[]` (`samba`, `nfs`, `nextcloud`), `hosts[]` (names of the servers where it was found), `covered_by` (the already watched folder containing this one, otherwise `null`), and `excluded_by` (the „ausgenommene Ordner“ (excluded folder) containing it—where the watcher does not search—otherwise `null`). Folders that are neither watched nor excluded come first, then the remaining ones, each sorted by path.
- `errors[]`: a sentence per server where searching failed completely or partially (no response, timeout, incomplete response). The other servers still appear in `suggestions`.
- `watch_paths[]`: the folders currently watched (the default locations as long as nothing is configured).

**`watch-paths`** takes the body `{"path": "/srv/samba/fotos"}` (1 to 255 characters) and appends the folder to `watch_paths`; if nothing is stored there yet, the default locations are saved together with the new folder. The path is normalized (trailing slash, `//`, and an appended `/*` are stripped). Response `200`: `added` (`true`: newly added; `false`: an already watched folder contains it, nothing changed), `covered_by` (that folder, otherwise `null`), and `watch_paths` (the list afterwards). Concurrent calls do not insert duplicates. Only a newly added folder appears in the log (`shield.watch_path_added`, `detail.path`).

| Status | Meaning |
|---|---|
| 400 | not a usable folder path: does not start with `/`, has `..` as a path component, contains `%` or control characters, is an entire system folder, `/` itself, or lies under `/proc`, `/sys`, `/dev`, `/run`, or `/boot` |
| 409 | the folder lies within an excluded folder (`exclude_paths`); remove the exclusion in the settings first |
| 422 | invalid body, `path` is missing, empty, or longer than 255 characters |

The old addresses `/ext/nexus-soc/…` answer the same (deprecated, see §7).

### Subscription link for the ntfy app (extension `ntfy`)

```
GET /ext/ntfy/subscribe-link   → 200 {url, text, error}   permission: extensions.manage
```

The extension `ntfy` is called „Push-Nachrichten“ (Push notifications) in the interface. The route provides the link with which the ntfy app on the phone subscribes to the dashboard's topic; the settings page turns it into the QR code (`x-qr-url` in `settings.schema.json`). The route has no parameters and calculates from the **stored** settings (`server_url`, `topic`), not from unsaved inputs. The link contains only address, port, path, and topic, never credentials from the address, the access token, or any other secret. The response is always `200`; if no link can be calculated, a sentence appears in `error` rather than an error status.

- `url`: `ntfy://<server>[:<port>][/<path>]/<topic>?display=Nodvard%20Deck`, for an `http://` server additionally `secure=false` (otherwise the app assumes https). Example: `ntfy://ntfy.example.com/deck-alarme?display=Nodvard%20Deck`.
- `text`: a sentence explaining what to do with the code (point camera at it, the ntfy app offers to subscribe).
- `error`: a sentence if no link is possible; then `url` and `text` are `null`. Reasons: server address and/or topic are still missing, the address is invalid (such as neither `http` nor `https`), or only additional channels are configured (Telegram, Discord, etc.) and not ntfy itself—the QR code belongs to ntfy.

### Custom temperature limit per server (extension `system`)

```
GET /ext/system/hosts/{host_id}/temp-limit   → 200 {host_id, own_c, global_c, effective_c}   permission: hosts.read
PUT /ext/system/hosts/{host_id}/temp-limit   {own_c}  → 200 like GET                          permission: settings.write
```

The extension `system` is called „Server-Zustand“ (Server status) in the interface. It warns when a server gets too hot, starting from the threshold `warn_temp_c` in its settings (default 75 °C). If a server always runs warmer (such as a Proxmox host under nightly load), it gets its own threshold here; the other servers remain at the global one.

- `own_c`: the custom threshold in °C (integer from 40 to 110) or `null`. In PUT, the field is mandatory: only an explicit `null` removes the threshold, in which case `global_c` applies again. If the field is missing (`{}`), an unknown field is present in the body (such as a typo in the name), the type is different (string, float, `true`), or the value is outside the range, `422` is returned and nothing is changed.
- `global_c`: the global threshold `warn_temp_c`, `effective_c`: the threshold currently effective for this server.
- Unknown server: `404`. When saving, thresholds of deleted servers are also dropped.

It is stored in the setting `warn_temp_by_host` (host ID → °C). It is hidden in the form (`x-hidden`); saving there leaves it unchanged. The same threshold applies to the notification („Warnung ab 90 °C, eigene Grenze dieses Servers“ (Warning from 90 °C, this server's own threshold)) and to the temperature color on the page; `GET /ext/system/hosts/{host_id}/info` additionally provides `temp_warn_c` (effective threshold) and `temp_warn_own_c` (custom threshold or `null`). A temperature only triggers a notification on the second consecutive check above the threshold; a single spike remains silent; if the threshold changes between two checks, the count starts over.

Every change to the custom threshold appears in the log as `ext_system.temp_limit_changed` (target: the server, actor: the signed-in user, `detail`: `own_c_before`, `own_c`, `effective_c`). A PUT with the already stored value writes nothing. The extension requires `audit.write` for this; if the permission is missing or the entry fails, the threshold is still saved and a warning is written to the container log.

On the „Server-Zustand“ (Server status) page, the threshold is set under temperatures (live view, „Sensoren“ (Sensors) section, and „Zustand & Updates“ (Status & updates)): field „Warnen ab (°C)“ (Warn from (°C)), „Speichern“ (Save), and „Allgemeine Grenze nutzen“ (Use global threshold) (sends `null`). Whoever lacks `settings.write` sees only the effective threshold.

### Dashboard and widgets

```
GET    /dashboard/layouts            GET/PUT /dashboard/layouts/{id}
GET    /widgets                      → catalog of all WidgetSpecs of enabled extensions
GET    /ext/{ext_id}/{data_endpoint} → widget data, {data, meta}
```

In addition to `items`, a layout carries the list `groups` (`[{key, title, collapsed}]`, `key` one of `servers`, `security`, `apps`, `backups`, `services`, `title` at most 60 characters, empty means default name). `PUT` accepts `items` and `groups` separately; an omitted field stays as it is; duplicate or unknown groups yield 422. If a group is missing from the list, it is placed behind in the default order. Every widget from `/widgets` has `group`.

`GET /widgets` delivers only widgets whose `permissions` the person has in full. Likewise, buttons (`view.actions`, `view.item.actions`, `view.row_actions`) whose own `permissions` do not all belong to them are omitted.

An entry in `items` has the form `{widget_id, ext_id, x, y, w, h, config}`. The backend only checks that `items` is a list of JSON objects and stores fields and values as they arrive. The core itself modifies the entries only when a module adopts another's widgets (`adopts`: new `ext_id` and `widget_id`, duplicates are dropped) and with the demo data (custom arrangement; on removal the old one returns). `x`, `y`, `w`, `h`, and `config` are evaluated only in the interface:

- `x`, `y`, `w`: position and width in a 12-column grid. Where the web interface sets position or width (new widgets, „Widgets verwalten“ (Manage widgets), dragging, „Schmal“ (Narrow) and „Breit“ (Wide)), `x + w ≤ 12` applies; the backend does not reject an entry extending beyond that. If the widget area is at least 960 px wide, each group is packed densely from top left, ordered by `y`, then `x`; `w` is bumped by the interface to the display type's minimum width and capped at 12. When narrower, cards are placed one below another only in this order (on screens from 640 px in two columns); `x` and `w` do not count there.
- `h`: height in grid rows; without `config.size`, only the default until the card has measured its content. Number and gauge always take 2 rows until then; the other types take `h`, at least their minimum height (2 or 3) and at most 5. The interface never stores the measured height.
- `config.hidden`: `true` means the widget is removed from the start page. The entry stays in place, otherwise the widget would count as new and return on its own; „Widgets verwalten“ (Manage widgets) removes the key again upon restoring. Any other value counts as none.
- `config.group`: one of the five group keys above; places the widget in a different group than that from `/widgets`. If the value is missing or unknown, the widget's group applies.
- `config.size`: `"s"`, `"m"`, or `"l"` (in the interface „Klein“ (Small), „Mittel“ (Medium), „Groß“ (Large)) gives the card a fixed height of 2, 3, or 5 grid rows (one row 90 px, 12 px in between). Without a specification or with another value, the height adjusts to the content: the smallest step among 1, 2, 3, or 5 rows that fits it. Number and gauge are always at least 2 rows high. Lists, tables, and tile grids that do not fit end with „+ x weitere“ (+ x more); other content scrolls.

The web interface preserves other keys in `config` when saving.

The Android app fetches `/widgets`, renders the declarative specs, and pulls the data from the same endpoints as the web. A new extension thereby appears in the app without updating the app—that is the practical gain from [Extension interface, section 4](EXTENSION-API.md#4-widgets--declarative-not-code).

### Custom apps („+ App hinzufügen“ (+ Add app))

```
GET    /overview                     → among others `apps`: custom apps (first) + detected services, each with `source`
GET    /apps                         → [App] (hosts.read)
POST   /apps                         → 201 App   {name, url, icon?, color?, group?, open_in_new_tab?, host_id?, sort_order?}
PATCH  /apps/{id}                    → App       only the named fields; clear icon/color/group/host_id with null
DELETE /apps/{id}                    → 204
PUT    /apps/order                   → [App]     {ids: [...]}: these first, in this order; remainder behind
                                                 (up to 50 more than the limit, as Cockpit always sends all)
GET    /apps/suggestions             → {suggestions: [{name, url, source, host, service_id}], errors: [str]} (apps.write)
```

`GET /apps/suggestions` (added) collects suggestions for new tiles („Apps“ page): from every extension with `AppSuggestionProvider` (e.g. Nginx Proxy Manager: enabled proxy hosts, `https://` if a certificate is attached) and from currently running detected services (`ServiceCatalog`) with an address (`source` is then the name of the extension). The detected services come from the same cache as `GET /overview` (no separate request) and carry `service_id` (added), the identifier of their tile (`apps[].id` with `source="detected"`); the Apps page shows them on this tile („Als App übernehmen“ (Adopt as app)) instead of under „Vorschläge“ (Suggestions) again. If an extension reports the same address as a detected service, the suggestion stays attached to the service. Every suggestion is checked like a manually created app (name shortened to 60 characters; unusable addresses are silently dropped); addresses already present as an app (same address, case of scheme and host and a trailing `/` do not count) and duplicates do not occur; at most 200. Each source has 12 s; if one does not answer, a sentence appears in `errors` (without text from the source), while the others remain. If detected services could not be read, a sentence appears for that in `errors` (without text from the source); the more detailed message is in `errors` of `GET /overview`.
The server requests none of the addresses; creation happens only with `POST /apps`.

App = `{id, name, url, icon, color, group, sort_order, open_in_new_tab, host_id, host, created_at, updated_at}` (`host` = display name of the server). Permissions: Read `hosts.read` (like detected apps in Cockpit); Write **`apps.write`**—new and additive: only Administrator and Owner have it by default. The tiles appear on the start page of all users, a link there is a click target for everyone; `hosts.write` (servers and SSH credentials) and `settings.write` (entire installation) would be far too broad for that. Every change appears in the log (`app.created`, `app.updated`, `app.deleted`, `app.reordered`; with name and `https://host:port`, never with path or parameters of the address).

Validation (422 with a German message on field `loc`): `name` 1–60 characters, trimmed, without control characters, line/paragraph separators (U+2028/U+2029), special whitespace (only regular space is allowed), and unassigned characters (only within emoji blocks are they treated as a new emoji) and with at least one visible character (letter, digit, symbol, or punctuation—fill characters like U+3164 and joiners alone are not a name); `url` only `http://`/`https://` with hostname or IP, at most 1000 characters, without user/password, whitespace/control characters, and backslash (so never `javascript:`, `data:`, `file:`, …); `group` at most 40 characters, with the same character rules as `name` (empty = no group); `color` `#rrggbb`; `icon` a name from the fixed list (`services/custom_apps.APP_ICONS`, identical to `frontend/src/lib/appIcons.ts`) or a single emoji—**no image URL** (otherwise each user's browser would query an external server when rendering the start page: tracking, mixed content); `host_id` must be an existing server; at most 200 apps (`409`; demo data complies with this limit as well). **The server never requests the addresses itself**—there is no status check for custom apps (no SSRF); such a check would be a separate package.

`GET /overview` is extended purely additively: `services` and `services_running` stay as they were (only detected services; the metric „Dienste“ (Services) counts no links); new is `apps`—custom apps (`source: "custom"`, by position, always fresh from the database) before detected services (`source: "detected"`, from the 30 s cache). Fields: `id, source, name, url, host, host_id, state, tone, image, icon, color, group, open_in_new_tab, sort_order` (`null` where not applicable for the type). If a server is deleted, its app remains, just without `host_id`. Also new: `services[].unreachable` (`true` on the placeholder tile of a server whose containers could not be read—it is not a service and does not count as "not running") and `services_unreachable_hosts` (names of these servers, otherwise empty). The placeholder also appears if the server answers but Docker does not (service down, no permissions). The server validates `services[].url` (and thus `url` of detected apps) following the same rule as the address of a custom app: only `http://` or `https://` with hostname or IP address, otherwise `null` (no `javascript:`, `data:`, or `//…`).

Also additive: `helper_hints`—servers that should be set up again so that modules run with restricted permissions: `[{host_id, host_name, modules: [display_name], checked_at, without_root, still_used, no_fallback}]`. An entry appears only when sudo on a Linux server permits executing helper scripts (full rule `NOPASSWD: ALL`, also with further specifications like `NOPASSWD: SETENV: ALL`, or the dedicated rule of at least one script) and at least one helper script of an enabled module is missing or not the bundled version (older, modified, unreadable); never for root and never for Windows. `without_root` is `true` if no full rule is present there: these modules then currently have no root at all (otherwise they run via the full rule). An exception are the modules in `still_used` (subset of `modules`, otherwise empty): these modules call their script in any version (`calls_any_version` in the manifest, currently Docker) and continue running with an older one there as long as its version is readable, only root can modify it, and sudo permits it. `no_fallback` (subset of `modules`, otherwise empty; missing in older cores) names the modules that have no root without their script even with the full rule (`sudo_fallback = false` in the manifest, currently Docker); except for those in `still_used`, they also lack root when `without_root` is `false`. The status comes from „Verbindung prüfen“ (Check connection) (only with the default credential) and the core job `helper-hints` (every 6 hours a read-only command per server, the same as during the check); `GET /overview` itself queries no server. `POST /hosts/{id}/helper-hint/dismiss` (`hosts.write`, 204, 404 without server) hides the hint for this server until the missing scripts or their bundled versions change (such as via an update).

Also additive: `untrusted_proxies`—as in `GET /system/connection`: proxies behind which Nodvard Deck does not see the real addresses because they are not listed under „Vertrauenswürdiger Proxy“ (Trusted proxy). It is recorded only on a login of the owner with the correct password (even if the two-factor code is still pending afterwards) carrying `X-Forwarded-For`, and only from a peer in the home network (private, loopback, link-local, `100.64.0.0/10`, `fc00::/7`); otherwise any account or anyone from the Internet could write an address into the hint. `Forwarded` alone does not count because Deck evaluates only `X-Forwarded-*`. The field is populated only for the owner, who is the only one permitted to change the list, otherwise empty. Nothing is ever entered automatically.

Also additive: `legacy_env` (`[{old, new, new_set}]`)—old environment variables (`LATTICE_<NAME>`) with which Nodvard Deck was started and their new name (`NODVARD_DECK_<NAME>`), sorted by the old name. `new_set`: the new name is also set and takes precedence; the old one can simply be removed. Only names, never values. The old names still apply in this version, no longer from 1.0. Populated only for the owner, otherwise empty.

### Demo data

```
GET    /demo                         → {active, hosts, notifications, apps, layout_replaced, hosts_with_access}
POST   /demo/seed                    → like GET, additionally {created}; seeds demo data (idempotent)
DELETE /demo                         → {removed_hosts, kept_hosts, removed_notifications, layout_restored, removed_apps, kept_apps}
```

„Mit Beispieldaten ansehen“ (View with demo data): five example servers (addresses from `192.0.2.0/24`, RFC 5737, without credentials so that nothing is ever queried), four notifications of varying severity with `payload.path`, three example apps (custom tiles with addresses from `192.0.2.0/24` that disappear again with the demo data; one that someone switched to a real address stays: `kept_apps`; they are placed after already existing custom apps), and, if modules contribute widgets, an example layout in the calling user's dashboard (the previous one is backed up and restored upon deletion). The IDs of the created rows are in the global setting `demo.seeded_ids`—`DELETE /demo` removes exactly those and nothing else. An example server that someone converted into a real one (address outside `192.0.2.0/24` or imported by a module) stays (`kept_hosts`). If a credential is attached to an example server, it is deleted along with it; `hosts_with_access` names such servers beforehand (for confirmation).

Permissions: `GET` requires `hosts.read`; `POST` and `DELETE` require `hosts.write` **and** `settings.write` (they create/remove servers, write notifications for all users, and modify the dashboard). `POST` answers `409` as soon as real servers exist (other than examples); a second `POST` changes nothing (`created: false`). `DELETE` without demo data is a harmless no-op; if an action is currently running on an example server, it answers `409` and changes nothing. Log: `system.demo.seeded`, `system.demo.removed`. `NODVARD_DECK_DEMO_MODE=1` calls the same function at startup (actor `system`).

### Files

```
GET    /files/sources                            → all FileSources of all extensions (excluding locked ones, see below)
GET    /files/places?host_id=                    → important locations of a server: [{label, path, icon, hint}]
GET    /files/{source}/list?path=&cursor=
GET    /files/{source}/stat?path=
GET    /files/{source}/info                       → SourceInfo (quota, health, deep link)
GET    /files/{source}/download?path=             → streaming, range-capable
POST   /files/{source}/upload?path=               → streaming
POST   /files/{source}/mkdir | /rename | /remove
POST   /files/transfer                            → {from:{source,path}, to:{source,path}}
                                                  → job_run ID, progress via WS
GET    /files/search?q=&sources=                  → fan-out, results mixed
```

`/files/places` queries all `FilePlaceProvider` ([Extension interface, section 3](EXTENSION-API.md#3-capabilities--the-heart-of-the-decoupling)) for shortcuts for a server. Each provider has six seconds and runs concurrently with the others; if one fails or takes too long, only its locations are missing (one line in the container log, without a traceback). Only absolute paths without `..` are accepted, normalized and deduplicated by path, at most 30. Permissions: `files.read` and `hosts.read` (otherwise `403`); an unknown server is `404`. Whoever is not allowed to open a file source for this server (the server has none or it requires `hosts.execute`, see below) gets an empty list.

`/files/transfer` is drag-and-drop between sources in the file manager: the server streams `open_read(A) → open_write(B)` without the file routing through the client.

`GET /files/sources` delivers per source `source_id`, `label`, `icon`, `caps`, and `host_id` (additive only): the ID of the server behind the source (the servers' SSH sources), otherwise `null`. A source declares it via the optional attribute `host_id` ([Extension interface, section 3](EXTENSION-API.md#3-capabilities--the-heart-of-the-decoupling)); only non-empty text counts, anything else yields `null`. The file manager uses this to query `GET /hosts` (only if at least one source has a server) and displays status and OS of the server for these sources. Without read permission on servers (`hosts.read`), only these details are missing; the sources remain.

If source and destination refer to the same file, `/files/transfer` answers `409` („Quelle und Ziel sind dieselbe Datei.“ (Source and destination are the same file.)) and starts no run. Within the same source, the identical path counts; for a source with `file_identity()` ([Extension interface, section 3](EXTENSION-API.md#3-capabilities--the-heart-of-the-decoupling), currently the servers' SFTP sources), also the same resolved path (symlink, `..` detour). Across two sources (such as two entries for the same server) only if both provide `file_identity()`, the resolved path matches, and size, modification time, owner, group, and permissions match as well; two different machine IDs are always two files. In doubt, the file is considered different. A folder mounted under two paths is not detected: moving a file there to its own second path deletes it. In the file manager, moving is a transfer followed by `remove` of the source; the original is deleted only if exactly as many bytes arrived as the source reported when displayed, otherwise it remains and the file manager reports it.

The servers' SFTP sources (`terminal` extension) on `upload` and `/files/transfer` write first to a temp file in the destination folder (`.<name>.nodvard-tmp-…`) and replace the target only once everything has arrived. An aborted upload or transfer then leaves the existing file untouched. If the temp file replaces an existing file, it is readable only by its owner until completion (`0600`); the new file retains owner, group, and permissions of the old one. A symlink is followed to the real file and remains a symlink. Direct writing to the target occurs when the account is not permitted to create files in the destination folder or cannot adopt the owner of the old file (such as without root on someone else's file). In that case, Nodvard Deck reads the entire source first (over 8 MiB into a local temp file) before opening the destination: if the source breaks off, the target stays untouched; if writing fails midway, it may be incomplete. Pipes and devices (such as `/dev/null`) are written to directly. Because replacement creates a new file, a container into which the file is individually bind-mounted sees the new content only after restarting the container; a mounted folder is unaffected.

`upload` by default has no size limit; the body streams to the source. `NODVARD_DECK_FILES_MAX_UPLOAD_BYTES` (bytes, `0` = no limit) sets one. If `Content-Length` is larger, `413` with `Connection: close` follows before the source opens the target. Without length (`chunked`), counting along aborts when reaching the configured limit (see §1), but at least at the global limit (1 MiB); by then the source has already begun writing (the SFTP sources then leave an existing file untouched as described above).

If access fails at the source (server down, login rejected, timeout), `info`, `list`, `stat`, `download`, `upload`, `mkdir`, `rename`, and `remove` answer `502` and the text `Zugriff auf die Quelle fehlgeschlagen: <Grund>` (Access to source failed: <reason>), missing file with `404`. For network errors, the reason is a German sentence („Server antwortet nicht (Zeitüberschreitung …)“ (Server does not answer (timeout …))); the Nextcloud source returns a dedicated fixed sentence per case, never the response text of the server (e.g. „Nextcloud antwortet nicht (Zeitüberschreitung).“ (Nextcloud does not answer (timeout).), „Nextcloud findet … nicht (HTTP 404). …“ (Nextcloud does not find … (HTTP 404). …), „In der Nextcloud ist kein Speicherplatz mehr frei (HTTP 507).“ (There is no free storage left in Nextcloud (HTTP 507).), „Nextcloud ist gerade nicht bereit (HTTP 503), zum Beispiel im Wartungsmodus. …“ (Nextcloud is not ready right now (HTTP 503), for example in maintenance mode. …)). A redirect (3xx) is never a success for it: „Nextcloud leitet auf eine andere Adresse um (HTTP n). …“ (Nextcloud redirects to another address (HTTP n). …), without the target address. For a file error (`OSError` such as `PermissionError`, not a network error), only the name of the error appears, never a path; the server writes the full text only to the container log. A failed `/files/transfer` records the same reason in the run (`error`).

`download` retrieves the first part from the source before the response begins. If the source fails during this (redirect, file missing, server unreachable), an HTTP error is returned instead of an empty or broken file: `404` with „'<pfad>' nicht gefunden.“ ('<path>' not found.), otherwise `502` as above. An empty file remains `200` without content. For sources with custom permissions, a failed download appears in the log as `files.download` with outcome `failure` and the status.

Permissions: `sources`, `info`, `list`, `stat`, `download`, and `search` require `files.read`; `upload`, `mkdir`, `rename`, `remove`, and `transfer` require `files.write`. A source can additionally require a custom permission (`required_permission`, [Extension interface, section 3](EXTENSION-API.md#3-capabilities--the-heart-of-the-decoupling)): the servers' SSH sources of the `terminal` extension require `hosts.execute` because they work with the server's credential and not the user's. Whoever lacks it does not see the source in `/files/sources` and `/files/search`; all other endpoints answer `403` for them, and `/files/transfer` also if only source or target is locked.

Log, only for sources with custom permissions (`target_type` `file_source`, always only source and path, never contents): `files.access` (denied, `outcome` `denied`), `files.download`, `files.upload`, `files.mkdir`, `files.rename`, `files.remove` (the four writing actions also if they fail), `files.search` (per searched source, without search term), `files.transfer_start`, and `files.transfer` (outcome with `bytes`, both with `run_id`). `list`, `stat`, and `info` do not appear in the log.

For the Nextcloud source, every path stays within the configured user's file area. A path that could escape it (a `..` component, including URL-encoded like `%2e%2e` or multiply encoded, a backslash, a NUL character, or an encoded slash like `%2F`) is rejected before any request with that path goes to the Nextcloud server: `list`, `stat`, `download`, `upload`, `mkdir`, `rename`, and `remove` answer `404`, and a `/files/transfer` with such a path ends as a failed run.

---

## 4. WebSocket

The image starts uvicorn with `--ws-max-size 1048576`: a single message from the client to one of the sockets may be at most 1 MiB; a larger one closes the connection (without this switch, uvicorn's default of 16 MiB applies).

### Multiplexed: `GET /ws`

One connection, many channels. On a mobile device, every additional connection drains battery life—hence deliberately not one socket per feature.

Authentication: the first message after connecting is `{"type":"auth","token":"<access_token>"}`. No tokens in the URL (they end up in proxy logs). The token of an already ended login (signed out, revoked) receives `auth_error`, even while it has not expired yet.

An open connection is re-checked on every `ping` (every 30 s): if the account is deactivated, the password changed, or the login ended, an `error` arrives with the reason in `payload.title`, after which the server closes with code `4401`. Changed roles apply for RBAC filtering from this check onwards, without a new connection. In addition, the connection ends as soon as the access token used to sign in expires (default 15 minutes, `access_token_ttl_seconds`): likewise `error` with the reason, then code `4401`. A token without `exp` receives `auth_error` upon connection setup. The client gets a new access token via `POST /auth/refresh`, reconnects, and resubscribes to its channels. A connection terminated in this way receives only this `error` until closure, but no more events, even on channels without permission checks.

Envelope, in both directions:

```json
{ "type": "...", "channel": "...", "id": "...", "payload": { } }
```

| type | Direction | Purpose |
|---|---|---|
| `auth` / `auth_ok` / `auth_error` | ↔ | |
| `subscribe` / `unsubscribe` / `subscribed` | ↔ | |
| `event` | ← | payload on a channel |
| `ping` / `pong` | ↔ | 30 s, app detects dead connections |
| `error` | ← | `payload.title` = reason as a German sentence |

Channels:

```
events                     everything published by the event bus (filtered by RBAC)
notifications              new notifications
actions                    proposal created / confirmed / executed
hosts                      status changes
jobs.{job_id}              run status
runs.{run_id}              live output of a run
files.transfer.{id}        progress
ext.{ext_id}.{arbitrary}   extension-specific channels (ctx.ws.broadcast)
```

`action.*` events on channel `events` (permission `hosts.read`, e.g. `action.executed`) contain the action's `payload` (in the envelope under `payload.data.payload`) in full only for connections with `hosts.execute`; `actions.approve:<risk>` is not sufficient here, unlike with the REST resource. Everyone else receives the same message with the identifiers as in §3 „Actions“ and `payload.data.payload_hidden` (`true` if anything was omitted). `action.executed` also names in `payload.data.ext_id` the module that proposed the action (older cores without this field).

Behavior on disconnection: after reconnecting, the client resubscribes and retrieves the missed state via REST. There is **no** message replay over WS—an event stream with guaranteed delivery would be a message queue, which according to [Decisions, D-09](../00-DECISIONS.md#d-09-hintergrundarbeit--queue) should not exist. Every event is a hint to "check back"; the source of truth lies in the REST resource.

### Terminal: `GET /ws/terminal/{session_id}` — dedicated socket

Deliberately separated from the multiplexer: keyboard inputs are latency-critical and outputs are binary and voluminous; both would clog the control channel.

```
GET  /terminal/hosts                                    → [host_id, …]   (hosts for which a session is possible)
POST /terminal/sessions   {host_id, cols, rows, user?, scope?, ref?}  → {session_id, ws_url}
```

The core page `/terminal` (multiple tabs, one session per tab) builds upon this. `scope` and `ref` (only together, otherwise `422`) open a shell **inside** something on the server rather than on the server itself, such as `{"scope": "container", "ref": "nginx"}`. `scope` has the form `^[a-z][a-z0-9_-]{0,31}$`, `ref` starts with a letter or digit and otherwise contains only `A–Z a–z 0–9 _ . : @ -` (at most 255 characters, otherwise `422`). If no module provides a shell for it (`ScopedTerminalTarget` with this `scope` capable of opening `ref`), the server answers with `404`; if the provider drops out before connecting, the socket ends with `4501` (never a shell on the server itself). The page opens such a tab directly via `/terminal?host=<id>&scope=…&ref=…` (e.g. „Konsole“ (Console) on the Docker page). `terminal.open` and `terminal.close` then carry `scope` and `ref` in `detail`. An incomplete or invalid link opens nothing on the page (never the server's shell as a fallback). The Docker module's container console depends only on the permission `hosts.execute`, thus applies even when the „Terminal“ module is off, runs in the container as its default user (often root), and never opens in Nodvard Deck's own container. If opening fails, `{"type":"error","message":…}` precedes the close frame. `message` names the reason and is never empty, for network errors a German sentence („Server antwortet nicht (Zeitüberschreitung bei …). Ist er eingeschaltet und im Netz?“ (Server does not answer (timeout at …). Is it switched on and reachable on the network?)); the same reason appears in `terminal.open` with outcome `failure` (in `reason`).

On the socket: **binary** frames = raw PTY bytes in both directions.
**Text** frames = JSON control:

```json
{"type":"resize","cols":120,"rows":40}
{"type":"signal","name":"SIGINT"}
{"type":"exit","code":0}
```

Sessions are recorded in the audit log as `terminal.open` / `terminal.close` with duration.

A session depends on account, permission `hosts.execute`, password, and the exact login from which the ticket originates. The server checks this upon opening and every 15 s thereafter (`terminal_recheck_interval_s`): if the account is deactivated, the permission revoked, the password changed, or the login ended (signed out or revoked), the session ends. Without any input **and** output, it ends after 30 minutes (`terminal_idle_timeout_s`, 0 = off); a command still yielding text keeps it open. The reason arrives in each case as `{"type":"error","message":…}` before the close frame. In the log, it appears upon opening in `terminal.open` with `denied`, for a running session in `terminal.close` (`reason`, additionally `detail.ended_by`: `access_ended` or `idle`).

| Close code | Meaning |
|---|---|
| `1000` | normal closure |
| `4401` | account, permission, password, or login no longer valid |
| `4404` | ticket unknown or expired, host unknown |
| `4408` | idle timeout |
| `4500` | opening failed |
| `4501` | no extension provides a terminal for this host |

### Console: `GET /ws/console/{session_id}` — screen of a VM/container

```
GET  /console/hosts                  → [host_id, …]   (hosts with available console)
POST /console/sessions {host_id}     → {session_id, ws_url, protocol, password}
```

Unlike the terminal, `POST` opens the console **immediately** (via the `ConsoleTarget` capability, e.g. Proxmox' vncproxy/vncwebsocket): the one-time password for protocol-specific authentication (`protocol: "vnc"` → RFB-VNC auth) is generated only in the process, and the browser client (noVNC) needs it prior to the WS connection. Sessions not collected within `terminal_session_ttl_s` are closed.

On the socket: only **binary** frames, raw protocol bytes in both directions; a requested subprotocol `binary` is acknowledged. The browser communicates only with Nodvard Deck, never with the hypervisor—its token remains in the vault. Every opening (including a failed one) appears in the audit log as `console.open`.

Unknown host or no console for it: `404`. If opening fails afterwards, `POST` answers with `502` and `detail` „Konsole konnte nicht geöffnet werden: …“ (Console could not be opened: …); the same reason appears in `console.open` with outcome `failure` (in `reason`). This also applies if the hypervisor answers WebSocket connection setup with a redirect (3xx): Nodvard Deck does not follow it, no second connection is established, and the reason states the redirect including status code (e.g. „… umgeleitet (HTTP 302) …“ (… redirected (HTTP 302) …)). The console never uses a proxy from Nodvard Deck's environment (`HTTP_PROXY`, `HTTPS_PROXY`, `WSS_PROXY`, etc.); its connections always go directly to the hypervisor's registered address.

Like the terminal, the console ends as soon as account, permission `hosts.execute`, password, or login no longer apply (check on setup and every 15 s): close code `4401`, otherwise `1000`. An unknown or expired ticket: `4404`.

---

## 5. What the Android app additionally needs

To ensure the app does not have to wait for API retrofits, these four things are already part of the API:

1. **`/capabilities`**—one APK, many differently equipped servers.
2. **Declarative widgets**—otherwise the app is blind to every new extension.
3. **`202` semantics**—the confirmation workflow must work on the phone; that is even the most likely place for it (notification arrives on the mobile device, confirmation with a thumb).
4. **Push:** `POST /me/devices {platform, push_token}`. Delivery runs via a `NotificationChannel`, deliberately not via a central push relay. In practice, this means: ntfy topic subscription in the app (works without custom infrastructure and without Google account binding), FCM optional for anyone who wants it.

---

## 6. Versioning

`/api/v1` remains compatible: additive changes only. Breaking changes receive `/api/v2`; both run in parallel until the app has caught up. Until the first APK exists, `v1` is effectively unstable—which is why the app is built after the web interface, not in parallel.

---

## 7. Compatibility

The dashboard is called „Nodvard Deck“. All Nodvard products are eventually intended to work together with Deck and the planned app via a versioned contract („Nodvard Link“), and the app communicates exclusively with Deck. Therefore, effective immediately, for `/api/v1` (core **and** extension routes under `/api/v1/ext/<id>/…`): only backward-compatible changes.

| Change | Allowed? |
|---|---|
| New endpoint | yes |
| New optional field / new optional parameter in a request | yes |
| New field in a response | yes |
| Remove or rename endpoint (path or method) | **no** |
| Remove or rename response field | **no** |
| New required field / new required parameter in a request (also: body previously missing) | **no** |
| Change type of a field | **no** |
| Remove a value from a fixed value list (enum) | **no** |

**Deliberately stricter for security reasons:** `DELETE /me/totp` and `POST /me/totp/setup` require `current_password`, which they previously did not require (otherwise a stolen session alone could turn off two-factor or enable it with an attacker's own authenticator). For `DELETE /me/totp`, the body is therefore mandatory (entry in `breaking_exceptions.toml`); for `POST /me/totp/setup`, it remains optional in the contract; without a password, `400` is returned there instead of `422` (see §3). Likewise for extension routes: whatever an extension mounts without `permission=` requires authentication and answers `401` without a token (previously it was reachable without signing in). Without signing in, only what it explicitly mounts with `public=True` and the manifest permission `api.public` is accessible ([Extension interface, section 2](EXTENSION-API.md#routes-and-login)). In hello-world, `POST /notify-test` and `POST /vault-use-and-fail` additionally require `extensions.manage`. Likewise, `POST /notifications/read` and `POST /notifications/read-all` require `notifications.write` (previously `notifications.read` was sufficient), and whoever lacks both `hosts.execute` and `actions.approve:<risk>` for an action's risk gets its `payload` truncated (`payload_hidden`, see §3). Likewise for request size: an endpoint that reads a body larger than 1 MiB (see §1) answers `413` (previously there was no global limit); uploads have their own, higher limits. Likewise, `POST /jobs/{id}/run` rejects a disabled job with `409` (previously it ran anyway; for instance, Nodvard Shield's disabled update automation could be started with `jobs.run`); the automation itself applies nothing without `auto_updates_enabled` anyway. Scripts without an enabled schedule continue to be started via `POST /ext/scripts/scripts/{id}/run`.
Likewise, `sudo=true` on `GET .../setup` and `POST .../setup-with-password` sets up only the helper script rules if those suffice; the full sudo rule is only granted with `full_sudo=true` (see §3).
Likewise, `POST /me/password`, `DELETE /me/totp`, and `POST /me/recovery-codes` with two-factor sign-in enabled additionally require `totp_code` (app code or recovery code); `POST /system/backups/download`, `POST /system/backups/{name}/ticket`, and `POST /system/restore/{id}/schedule` require the app code. Likewise, `PATCH /users/{id}` with `password` or management roles and `POST /users` with management roles require the admin's own `current_password` (missing: `403` with `code: "password_missing"`), and `POST /users/{id}/reset-2fa` additionally requires the app code if the admin has two-factor turned on. In the contract, the field is optional (no schema break). Older clients, e.g. an older Android app, receive `403` with `code: "totp_missing"` and can execute these actions only after updating; when changing passwords, `detail` itself explains that the app must be updated first. Code errors additionally carry `code` (`totp_wrong`, `totp_used`), including during update and rollback.
Likewise, all paths that replace or delete an already stored secret require `current_password` and, with two-factor, `totp_code`: `PUT /secrets/{id}/value`, `DELETE /secrets/{id}`, `PUT`/`DELETE /extensions/{id}/secrets`, `PUT /extensions/{id}/settings` (if the change deletes credentials), `DELETE /hosts/{id}/credentials/{cid}`, `DELETE /hosts/{id}` (if the server has stored credentials), `PATCH /hosts/{id}` with a new address (if SSH passwords are removed), and `make-default` with `delete_previous`. Without a password, `403` with `code: "confirm_required"` follows (see „Vault“ in §3); initial creation remains without prompt, as does „Mit Passwort verbinden“ (Connect with password) in Proxmox (exception in §3).
Likewise, `PUT /branding` no longer accepts external addresses for `logo_url` and `favicon_url` (`422`), because the page should load images only from its own server; an already stored value may remain unchanged, and `POST /branding/favicon` is available for the favicon (see §3).
Likewise, in Nodvard Shield, `POST /ext/shield/incidents/{id}/confirm`, `…/dismiss`, `…/resolve`, and `…/reopen` require `soc.manage` (previously `soc.read` was sufficient); reading remains at `soc.read`.
And for extensions: `ctx.http` no longer follows redirects, even with `follow_redirects=True`, and `ctx.audit.log()` writes no core entries (`mfa.*`, `auth.*`, `login.*`, `system.*`, `user.*`); both in [Extension interface, section 2](EXTENSION-API.md#routes-and-login).

**Renaming** is only done as follows: the old name remains supported in parallel across several releases and is marked as `deprecated` (`deprecated=True` on the endpoint or field, accompanied by a note in the changelog). Only when app and extensions use the new name may the old one be dropped later. Truly unavoidable breaking changes require `/api/v2` (see §6).

**Renamed extensions (`legacy_ids`).** If an extension receives a new identifier ([Extension interface](EXTENSION-API.md#renaming-an-extension-legacy_ids)), its old addresses continue to apply. These remain supported throughout all 1.x (marked as deprecated) and are removed at the earliest with 2.0. They are not redirects (no `3xx`, no custom header in the response), but the identical routes under a second address:
- `/api/v1/ext/<old>/…`: the same routes as under `/api/v1/ext/<new>/…`, with the same authentication check and identical responses. In the OpenAPI schema, they are `deprecated` and appear behind the new address. Public routes appear with both prefixes in the log.
- `/api/v1/extensions/<old>/…` (retrieval, enable, disable, `frontend/index.js`, settings, secrets, test) refers to the same extension and the same stored row. Responses name the new identifier in `id`. Exception: if an orphaned state (a twin, e.g. after fresh installation, rollback, and renewed update) still lies under the old identifier, modifying calls via it answer `409`. Reading continues to work.
- New optional fields: `ExtensionOut.legacy_ids`, `PageOut.legacy_ext_ids`, `WidgetOut.legacy_ext_ids` (each `[]` without renaming), `ActionOut.action_label` (name of the registered action kind, otherwise `null`), and `ActionOut.ext_name` (name of the installed extension, also with old identifier, otherwise `null`).
- `GET /extensions` lists each extension once under its new identifier; orphaned twins are omitted. `GET /jobs?ext_id=` filters by storage identifier (`nexus-soc` for Nodvard Shield on existing installations); `GET /capabilities` names only the new identifier.

**Adopted parts (`adopts.routes`).** When a component moves into its own module ([Extension interface, section 1](EXTENSION-API.md#taking-over-part-of-another-module-adopts)), its old addresses `/api/v1/ext/<source>/<old>` continue to apply as long as the new module is enabled: the identical routes of the new module under a second address, with the same authentication check and identical permissions, marked `deprecated` in the OpenAPI schema. They also remain supported throughout all 1.x and are dropped at the earliest with 2.0. If the source still serves an address itself, the source continues to answer. This applies only among bundled modules; a third-party package receives no external addresses.

**Pi-hole** is its own module `pihole` since this release (beta, previously part of `network`). New are `GET /api/v1/ext/pihole/summary`, `GET …/widgets/status`, and `POST …/pause`, `…/resume`, `…/gravity`, `…/domains`. Deprecated and still answered (as long as `pihole` is enabled): `GET /api/v1/ext/network/pihole`, `GET /api/v1/ext/network/widgets/pihole`, `POST /api/v1/ext/network/pihole/pause`, and `…/pihole/resume`.

**Nginx Proxy Manager** is likewise its own module `npm` (beta). New are `GET /api/v1/ext/npm/summary`, `GET …/address-check`, `GET …/widgets/certificates`, and `POST …/hosts/{host_id}/enable` or `…/disable`. Deprecated and still answered (as long as `npm` is enabled): `GET /api/v1/ext/network/npm`, `GET /api/v1/ext/network/npm/address-check`, `GET /api/v1/ext/network/widgets/certificates`, and `POST /api/v1/ext/network/npm/hosts/{host_id}/enable` or `…/disable`. „Netzwerk“ (Network) itself is now merely a pointer without its own addresses.

**Nodvard Shield** is called `shield` since 0.7 (formerly `nexus-soc`). Thus `/api/v1/ext/nexus-soc/**` (all routes of the extension, currently 34 paths, each also under `/api/v1/ext/shield/…`) and `/api/v1/extensions/nexus-soc/**` are deprecated. Both answer identically to the new ones. New entries in the log are named `shield.*`; older `nexus_soc.*` remain; a filter `GET /audit?action=shield.…` does not find the older ones. Action kinds remain `nexus_soc.*` ([Architecture, „Old and new names“](ARCHITECTURE.md#old-and-new-names)).

**Docker** is called `docker` since 0.9 (formerly `service-matrix`). Thus `/api/v1/ext/service-matrix/**` (all routes of the extension, each also under `/api/v1/ext/docker/…`) and `/api/v1/extensions/service-matrix/**` are deprecated, both throughout all 1.x. They answer identically to the new ones. Log names remain `service_matrix.tile_url` and `service_matrix.image_update`, action kinds `container.*` and `docker.*`. On existing installations, `GET /jobs?ext_id=service-matrix` filters the schedule of the image check ([Architecture, „Old and new names“](ARCHITECTURE.md#old-and-new-names)).

**Automatic protection:** `backend/tests/contract/api_v1.json` is a checked-in snapshot of the contract (per path and method: parameters, body fields with required flag and type, fields of the 2xx response including enum values). The tests in `backend/tests/contract/` compare the current `app.openapi()` (with all bundled extensions) against it:

- A **break** causes the test to fail; the message lists all breaks.
- An **addition** (allowed) causes a second test to fail until the snapshot is updated: `python scripts/update_api_contract.py`, then check in the modified JSON. The script refuses to write if doing so would introduce a break.
- **Exceptions** are possible only with justification: an entry in `backend/tests/contract/breaking_exceptions.toml` (path, method, kind, justification), then `python scripts/update_api_contract.py --allow-breaking`. The list is initially empty and should remain so whenever possible.

