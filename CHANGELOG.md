# Changelog

All notable changes to Nodvard Deck are documented here. The project uses
[Semantic Versioning](https://semver.org/); until 1.0, minor versions may contain breaking
changes.

The detailed release notes (in German) live in
[`backend/src/nodvard_deck/changelog/versions/`](backend/src/nodvard_deck/changelog/versions/) and are
shown inside Nodvard Deck under *Settings → About Nodvard Deck*. This file is the short English summary.

## [Unreleased]

## [0.7.0] – 2026-10-07

Update helper, Shield under its own name and more protection.

- **New:** an optional update helper (switched on with its own Compose file). With it, the owner
  installs future versions from Settings → System → Updates with password and two-factor code,
  follows every step, and goes back once within 7 days if needed. A new version that does not
  start is rolled back automatically. The first update by button is the step from 0.7.0 to the
  next version.
- **New:** "Propose all updates" and "Propose all security updates" in the Nodvard Shield update
  center, each still approved under "Actions".
- **Security:** turning off two-factor login and creating new recovery codes need password and
  code; with two-factor on, downloading and restoring a backup need the code too. The lockout after
  wrong codes survives a restart. User accounts, autonomy, blocked commands and maintenance windows
  are written to the audit log. Extensions follow no redirects and cannot write the core's own
  audit entries.
- **Fixed:** redirects and unexpected answers from Pi-hole, Nginx Proxy Manager, ntfy, Nextcloud,
  Proxmox and the AI server show a clear sentence instead of raw text or a false "works". The
  hardening audit also finishes on servers where Lynis needs more than an hour, shows "running
  since …" right away and never starts twice. Ignored or restored findings stay out of quarantine.
  Scripts keep `$HOME`, `$(date)` and similar, and a failed scheduled run counts as failed. Broken
  schedules can no longer be saved and no longer stop Shield or Scripts. Automatic updates restart
  the dashboard's own server last.
- **Improved:** Nodvard Shield uses `shield` as its internal name; old links, settings and
  findings keep working, and extensions can be renamed later without losing data. Names instead
  of internal IDs on the actions page, in notifications and in the audit log.

## [0.6.2] – 2026-10-02

Security and polish release.

- **Security:** fixes from a full security review:
  - Files on servers (SSH) only for users who may run commands on servers. File access, actions
    and failed logins land in the audit log; login names that do not exist are never stored in
    plain text.
  - Commands and output of actions only for users with server rights. The live connection
    re-checks account, session and rights and ends with its token.
  - Two-factor codes and login steps are single-use, with a per-account lockout; setting up 2FA
    asks for the current password. Deactivating an account or changing a password signs it out
    everywhere, terminal and console included.
  - Extension routes require a login by default; extension secrets are bound to their target
    address. Console and extension requests never follow redirects or use proxies from the
    environment.
  - Request size limit (1 MiB, with separate limits for uploads), SVG logos checked and served
    sandboxed, hardened rescue page, backups no longer carry Git settings, hardened image
    (code owned by root, data readable only by its owner).
  - The AI container watch only ever proposes a fixed container restart.
  - New installations ask you to confirm the fingerprint of a new server before any password
    or key is sent.
- **Fixed:** `backup.sh`/`restore.sh` work with sudo, with a lock and a safe swap; "Retry" on
  Proxmox backups keeps the job's retention; copying a file onto itself no longer empties it;
  quarantine restore follows no links; the virus-scan watcher no longer skips files silently;
  update check with reason and catch-up; no port alarms for random ports; the Pi deploy script no
  longer rolls back after a database migration.
- **New:** scheduled scripts can run without a click (per script, owner or admin only, ends with
  any change); new SSH logins are called `nodvard`; clearer error pages and hints; Nodvard Shield
  explains the next step and its terms; a server only turns green after a real login.

## [0.6.1] – 2026-10-02

- **Fixed:** every SSH connection failed in the Docker image with "Permission denied: '/root/.ssh/crt'"
  (the service ran as its own user but with root's home folder). Server pages, Service Matrix,
  Nodvard Shield and the file manager reach your servers again.
- **Security:** the antivirus scan can no longer be tricked by crafted file names.
- **New:** update check under Settings → System → Updates (daily, can be switched off), with a
  "What's new?" link to this changelog.
- The whole interface now uses informal German ("du"); API documentation only for signed-in admins
  or in development mode; a missing extension stays enabled; more complete third-party license notices.

## [0.6.0] – 2026-10-01

First public beta.

- **Set up without code:** published container image (`ghcr.io/nodvard/deck`), setup
  assistant with time zone, module choice and two-factor login, "First steps" card, empty
  states with next steps, demo data you can remove with one click.
- **Servers & access:** add servers in the web interface, Nodvard Deck generates the SSH key
  and shows a one-line setup command, "Test connection" explains what is missing, regular
  reachability checks with alerts.
- **Backup & restore:** encrypted backups (manual, scheduled, download), restore in the web
  interface or during setup, automatic copy before every database migration and a rescue
  page if a start fails, recovery codes and emergency commands for lost logins.
- **Custom apps:** your own tiles and links on the dashboard, next to discovered containers.
- **Monitoring:** disk-usage and temperature warnings, CPU/RAM/disk cards in the cockpit,
  Nodvard Shield incidents survive restarts and name the cause determined by the system.
- **Module settings:** "Test connection" for every module, secrets editable in place.
- **Renamed to Nodvard Deck:** packages `nodvard_deck`, SDK `nodvard_sdk`, extensions
  `nodvard_deck_ext_*`, environment variables `NODVARD_DECK_*`. Old names keep working.
- Project documents for the public release: PolyForm Noncommercial 1.0.0 license (source available,
  free for noncommercial use, commercial use only with permission), third-party licenses, README in
  German and English, contribution guide, security policy, code of conduct, English guides.

## [0.5.0] – 2026-09-30

- Apply container image updates from the Service Matrix with a preview, automatic health
  check and a kept backup image for rollback.
- Many fixes across the web interface and backend.

## [0.4.0] – 2026-09-30

- Image update check for Docker containers with optional daily push notification.
- New Nodvard Shield options, readable "proposed by" names in the gate, maintenance windows
  also mute watcher alerts, in-app changelog, many fixes.

## [0.3.0] – 2026-09-30

- Approved actions run in the background; updates keep running on the server even if the
  connection drops.
- Bulk approval in the gate, graceful VM/container shutdown, service restart, editable
  server tags, secret script parameters are no longer shown in plain text.

## [0.2.0] – 2026-09-29

- Security hardening: command injection in Nodvard Shield closed, login rate limiting, sessions
  revoked on password change.
- Reliability fixes across core and extensions, Network extension (Pi-hole, Nginx Proxy
  Manager), first-setup and Proxmox token guides.

## [0.1.0] – 2026-09-26

- First version: core with dashboard, server pages, approval gate, users and 2FA, audit
  log, secrets vault, web terminal and file manager, plus extensions for Proxmox VE,
  backups, Docker, Linux systems, security (Nodvard Shield), game servers, scripts, documents,
  inventory, Nextcloud and ntfy.
