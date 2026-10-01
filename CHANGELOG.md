# Changelog

All notable changes to Nodvard Deck are documented here. The project uses
[Semantic Versioning](https://semver.org/); until 1.0, minor versions may contain breaking
changes.

The detailed release notes (in German) live in
[`backend/src/nodvard_deck/changelog/versions/`](backend/src/nodvard_deck/changelog/versions/) and are
shown inside Nodvard Deck under *Settings → About Nodvard Deck*. This file is the short English summary.

## [Unreleased]

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
