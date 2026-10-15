<div align="center">

# Nodvard Deck

**One dashboard for your whole homelab — servers, VMs, containers, backups, security and more.**

Self-hosted · Extensible · White-label ready

[Deutsch](README.md) · [Documentation](docs/README.md) · [Getting started](#getting-started) · [Discord](https://discord.gg/gTTs7qMabt) · [Feedback](CONTRIBUTING.md)

![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue)
![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB)
![React](https://img.shields.io/badge/frontend-React%20%2B%20TypeScript-61DAFB)
![Status](https://img.shields.io/badge/status-beta-orange)

</div>

---

Nodvard Deck replaces the handful of admin tools a homelab usually needs — the Proxmox UI,
Portainer, a start page like Homarr, a backup overview, a security console — with **one
web interface**. Every server gets its own page (think Plesk) with its status, live load,
actions and settings. Everything that changes something goes through an **approval gate**
first, so nothing happens on your machines by accident.

> **Status:** Nodvard Deck is in active development (beta). It runs 24/7 in a real homelab,
> but APIs and data formats may still change between versions.

## Features

**Core**
- Dashboard with freely arrangeable widgets and a status overview ("cockpit")
- Server pages: status, live CPU/RAM, actions, tools and services per host
- Approval gate: every change is proposed first and only runs after approval (optional autonomy mode, deny list, maintenance windows)
- Users, roles and permissions, two-factor login, audit log
- Encrypted secrets vault for API tokens and SSH credentials
- Web terminal (SSH) and file manager across servers
- Notifications center, push notifications via ntfy
- White-label branding: name, logo and colors are settings, not code
- Installable as an app on phones (PWA)

**Extensions** (each can be switched on or off)

| Extension | What it does |
|---|---|
| Proxmox VE (Proxmox only) | Nodes, VMs and containers: start/stop, console in the browser, snapshots, hardware settings, storage, updates, disk health |
| Proxmox-Backups (Proxmox only) | All Proxmox backups at a glance, create and edit backup jobs, warnings for missing or failed backups |
| Docker | Docker containers on your servers: start/stop, live logs, details, storage cleanup, compose stacks, image update check |
| Server status („Server-Zustand“) | Linux servers over SSH: OS, load, disks, failed services, pending updates |
| Nodvard Shield | Security: antivirus (ClamAV), hardening audits (Lynis), update center, intrusion detection, file integrity monitoring, AI container watch |
| Pi-hole (beta) | Statistics, pause and resume blocking, reload block lists, block or allow domains |
| Nginx Proxy Manager (beta) | Switch proxy hosts on and off, certificate expiry, address check against the containers |
| Network (beta) | Only a signpost to Pi-hole and Nginx Proxy Manager now; can stay switched off |
| Game servers (beta) | Start/stop game servers and show the current join code (e.g. Valheim) |
| Scripts | Versioned script repository, run on one or many servers, scheduled – optionally without a click, as long as nothing changes |
| Documents | Document archive with OCR and full-text search |
| Inventory | Devices and items with purchase data and warranty reminders |
| Nextcloud | Nextcloud as a source in the file manager, plus a tile with version, updates, free space, active users and load |
| ntfy | Push notifications to your phone |
| Uptime Kuma (beta) | State of all monitors, availability over the last 24 hours, recent outages and expiring certificates (read-only) |
| Home Assistant (beta) | Version, pending updates, unavailable devices and selected values such as temperatures (read-only) |
| Jellyfin (beta) | Current playback with user and device, whether it transcodes, library sizes and recently added media (read-only) |
| Plex (beta) | Current playback with user and device, library sizes and recently added media; talks only to your own server, never to plex.tv (read-only) |
| qBittorrent (beta) | Speed, active torrents, count per state and free space (read-only) |

"Beta" means: the extension works, but is not yet as thoroughly tested as the rest. Feedback helps.

You do **not** need Proxmox: a Raspberry Pi, a Debian VM, a NAS or a few Docker hosts are enough. You add
servers by hand; load, Docker containers, updates and security all work over SSH.

Extensions are built against a documented SDK, so you can write your own —
see [docs/en/EXTENSION-API.md](docs/en/EXTENSION-API.md).

## Getting started

**Note:** The user interface is currently in German only; an English interface is planned.

**Requirements:** Docker with Docker Compose. Nodvard Deck runs fine on a Raspberry Pi 4/5
(arm64) as well as on any x86-64 machine. Nothing is built: the ready-made image comes from the
GitHub Container Registry (`ghcr.io/nodvard/deck`).

Pick one way and expand it:

<details>
<summary><b>Docker Compose in a terminal</b></summary>

```bash
mkdir ~/nodvard-deck && cd ~/nodvard-deck
curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml
docker compose up -d
```

**Windows:** the commands are for a Linux shell (also WSL or Git Bash). In PowerShell, write
`curl.exe` instead of `curl` and run the commands one by one.

- **Open:** `http://<your-server>:8080`
- **Port already in use?** (message "port is already allocated" or "address already in use"): put a file
  `.env` with the line `NODVARD_DECK_PORT=8081` next to `compose.yml` and run `docker compose up -d` again.
  The address is then `http://<your-server>:8081`.
- **Setup code:** `docker compose logs nodvard-deck` in the same folder; the log line is in German and
  starts with "Einrichtungscode".
- **Updating:** in the same folder, `docker compose pull && docker compose up -d`. Your data is kept.

</details>

<details>
<summary><b>Portainer, way 1: from the repository</b></summary>

1. **Stacks → Add stack**, name e.g. `nodvard-deck` (lowercase letters, digits, `-` and `_` only).
2. Choose **Repository** as the build method and enter:
   - **Repository URL:** `https://github.com/nodvard/deck`
   - **Repository reference:** `refs/heads/main`
   - **Compose path:** `deploy/compose.standalone.yml`

   You do not need to log in; the repository is public.
3. **Deploy the stack**.

- **Open:** `http://<your-server>:8080`
- **Port already in use?** Under **Environment variables**, add the variable `NODVARD_DECK_PORT` with e.g.
  `8081`: for a new stack before "Deploy the stack", for an existing one followed by a redeploy
  ("Pull and redeploy"). The address is then `http://<your-server>:8081`.
- **Setup code:** **Containers** → `nodvard-deck-nodvard-deck-1` (in general
  `<stack-name>-nodvard-deck-1`) → **Logs**; the line is in German and starts with "Einrichtungscode".
- **Updating:** in the stack, **Pull and redeploy** with **Re-pull image** switched on. You can optionally
  switch on Portainer's automatic updates ("Automatic updates", in newer versions "GitOps updates"): they
  pick up changes to the Compose file in the repository on their own. A new version of Nodvard Deck,
  however, is a new image and not a change to the file; you get it with "Pull and redeploy".

</details>

<details>
<summary><b>Portainer, way 2: web editor</b></summary>

1. Open the file [deploy/compose.standalone.yml](deploy/compose.standalone.yml)
   ([as plain text](https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml))
   and copy its **entire content**. It starts with `# Nodvard Deck ohne das Repository installieren` and
   contains `services:` further down.
2. **Stacks → Add stack**, name e.g. `nodvard-deck`, choose **Web editor** as the build method, paste the
   content, **Deploy the stack**.

Do **not** paste the three commands from the terminal way (`mkdir …`, `curl …`, `docker compose up -d`).
They are shell commands, not a Compose file; Portainer then reports "top-level object must be a mapping".

- **Open:** `http://<your-server>:8080`
- **Port already in use?** Under **Environment variables**, add the variable `NODVARD_DECK_PORT` with e.g.
  `8081`: for a new stack before "Deploy the stack", for an existing one followed by "Update the stack".
  The address is then `http://<your-server>:8081`.
- **Setup code:** **Containers** → `nodvard-deck-nodvard-deck-1` (in general
  `<stack-name>-nodvard-deck-1`) → **Logs**; the line is in German and starts with "Einrichtungscode".
- **Updating:** open the stack → **Editor** → **Update the stack** with **Re-pull image and redeploy**
  switched on. You do not need to change anything in the editor for that.

</details>

<details>
<summary><b>Raspberry Pi</b></summary>

- A Raspberry Pi 4 or 5 with 4 GB RAM and a **64-bit system** (`uname -m` shows `aarch64`); the image is
  available for `linux/arm64`. When idle, Nodvard Deck needs about 100 MB of memory; the image is just
  under 400 MB. Docker with Docker Compose has to be installed.
- An SD card can die: do not keep backups only on the Pi
  ([section 10 of the guide](docs/en/FIRST-SETUP.md#10-back-up-the-dashboard-itself)); a USB SSD lasts
  longer than an SD card.
- If Portainer runs on the Pi, the two Portainer ways above work the same.

```bash
mkdir ~/nodvard-deck && cd ~/nodvard-deck
curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml
sudo docker compose up -d
```

If your user is in the `docker` group, leave out `sudo`. The first start takes a while because the image
has to be downloaded first.

- **Open:** `http://<address-of-the-pi>:8080`
- **Port already in use?** Put a file `.env` with the line `NODVARD_DECK_PORT=8081` next to `compose.yml`
  and run `sudo docker compose up -d` again. The address is then `http://<address-of-the-pi>:8081`.
- **Setup code:** `sudo docker compose logs nodvard-deck` in the same folder; the log line is in German and
  starts with "Einrichtungscode".
- **Updating:** in the same folder, `sudo docker compose pull && sudo docker compose up -d`. Your data is
  kept.

</details>

Then follow the setup assistant in the browser. Everything else – servers, SSH access, modules,
backups – is set up in the web interface.

The full step-by-step guide (setup code, SSH access, Proxmox token, push notifications,
backups of Nodvard Deck itself, emergency commands) is in
[docs/en/FIRST-SETUP.md](docs/en/FIRST-SETUP.md).

**Building from source** (developers): clone the repository, then run `docker compose up -d --build`
in `deploy/`. Build, deployment and backup/restore details: [deploy/README.md](deploy/README.md) (German).

## Architecture

```
backend/     FastAPI (Python 3.12), SQLAlchemy + SQLite, Alembic migrations
sdk/python/  nodvard_sdk — the stable contract extensions are built against
frontend/    React + Vite + TypeScript + Tailwind web interface
extensions/  one folder per extension (Python backend + bundled frontend)
deploy/      Dockerfile, docker-compose, backup/restore scripts
docs/        architecture, extension API, data model, API reference
```

The core knows no vendor by name — everything specific (Proxmox, Docker, Pi-hole, …)
lives in extensions. A CI check (`scripts/check_core_purity.py`) enforces this.
More: [docs/01-ARCHITECTURE.md](docs/01-ARCHITECTURE.md) (German).

## Development

```bash
# Backend
python -m venv .venv
.venv/bin/pip install -c deploy/constraints.txt -e sdk/python -e "backend[dev]"
.venv/bin/pytest sdk/python/tests backend/tests
python scripts/check_core_purity.py

# Frontend (in frontend/)
npm ci
npm run typecheck && npm run typecheck:extensions
npm test && npm run test:extensions
npm run build
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for notes on building and checking it yourself.

## Support the project

Nodvard Deck is free for personal and other noncommercial use, and its source code is
available to read. The best way to help is to report bugs and share ideas – see
[CONTRIBUTING.md](CONTRIBUTING.md). Questions, ideas and chat: join the [Discord](https://discord.gg/gTTs7qMabt)
(mostly German, English is welcome in #english). The project does not accept code contributions from outside.
Nodvard Deck is a private, noncommercial project.

## Security

Please do **not** report security issues in public issues. See [SECURITY.md](SECURITY.md).

## License

Nodvard Deck is licensed under the **PolyForm Noncommercial License 1.0.0** — see
[LICENSE](LICENSE). In short (only the license text is binding): you may use Nodvard Deck for personal
and other noncommercial purposes, read the source code and adapt it for yourself. Commercial use,
for example inside a company or as a paid service, is not permitted without separate permission.
Inquiries: **kontakt@nodvard.com**.
The source code is available, but Nodvard Deck is not open source software in the strict sense.
Licenses of bundled third-party libraries are listed in [THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES).

## Trademarks

Proxmox, Docker, Portainer, Synology, Unraid, Nextcloud, Pi-hole, Nginx Proxy Manager, ntfy, Ollama, ClamAV, Lynis,
Fail2ban, Raspberry Pi and other names mentioned are trademarks of their respective owners. Nodvard Deck is not affiliated with them.

© 2026 Nico Benks
