<div align="center">

# Nodvard Deck

**One dashboard for your whole homelab — servers, VMs, containers, backups, security and more.**

Self-hosted · Source-available (PolyForm Noncommercial) · Free for personal use

[Deutsch](README.md) · [Documentation](docs/README.md) · [Getting started](#getting-started) · [Discord](https://discord.gg/gTTs7qMabt) · [Feedback](CONTRIBUTING.md)

[![Release](https://img.shields.io/github/v/release/nodvard/deck)](https://github.com/nodvard/deck/releases/latest)
[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue)](LICENSE)
![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB)
![React](https://img.shields.io/badge/frontend-React%20%2B%20TypeScript-61DAFB)
![Status](https://img.shields.io/badge/status-beta-orange)
![UI: German](https://img.shields.io/badge/UI-German-blue)

The web interface is currently available in German only. This README is also available in [German](README.md).

</div>

---

Nodvard Deck bundles the state, logs and actions of your homelab in **one web interface**, with an **approval gate**
for changes to your servers. Every server gets its own page with its status, live load, actions and settings.
Whatever changes something is started by you or released through an approval; the exceptions are listed under
[Security and privacy](#security-and-privacy-at-a-glance). Some modules are still beta or read-only (see the tables
under [Features](#features)).

> **Status:** Nodvard Deck is in active development (beta). It runs 24/7 in a real homelab,
> but APIs and data formats may still change between versions.

## What it looks like

| Overview | Docker |
|---|---|
| ![Overview with the state of all servers and services](docs/img/uebersicht.png) | ![Containers of a server with state and actions](docs/img/docker.png) |
| **Nodvard Shield** | **Updates** |
| ![Nodvard Shield with protection score, virus scan and update center](docs/img/shield.png) | ![A new version with one button through the update helper](docs/img/updates.png) |

The screenshots show sample data (the interface is in German for now).

## Security and privacy at a glance

- **No telemetry:** Nodvard Deck sends no usage or crash data anywhere.
- **On its own it only contacts the update search:** once a day Nodvard Deck asks GitHub for a new version, and GitHub
  sees your IP address and the time (can be switched off, details in
  [docs/en/FIRST-SETUP.md, section 10.5](docs/en/FIRST-SETUP.md#105-finding-and-installing-a-new-version)). Everything
  else only goes where you set it up: your servers via SSH, Proxmox, notification services such as ntfy, an AI such as
  Ollama.
- **Your servers ask on their own:** Nodvard Shield has them reload their package lists from their package sources once
  a day to find updates. If you switch on the search for newer images in the Docker module (off by default), your
  Docker servers ask the registries of their containers.
- **Secrets in the vault:** passwords, tokens and SSH keys are stored encrypted in the database, the key for them
  (`master.key`) in the data folder. Whoever has the data folder can read everything. Backups made in the web interface
  are always encrypted (with a password or a recovery key); `deploy/backup.sh` only encrypts when a backup key is set up
  in Nodvard Deck, otherwise the file contains the key to the vault.
- **Do not expose it to the internet directly:** Nodvard Deck belongs in your home network or behind a VPN. Behind a
  reverse proxy with HTTPS: [docs/en/REVERSE-PROXY.md](docs/en/REVERSE-PROXY.md).
- **Rights on your servers:** the modules work via SSH. Where root is needed, Nodvard Deck sets up a narrow sudo rule
  for its own helper scripts only by default, and full rights only for modules without a helper script. The Docker
  module needs the docker group, which amounts to root; if you only want to view, start and stop containers, use the
  "operate only" mode („Nur bedienen“) without the group
  ([docs/en/FIRST-SETUP.md, section 6](docs/en/FIRST-SETUP.md#6-root-rights-without-a-password-and-the-docker-group)).
- **No Docker socket for the dashboard:** only the optional update helper gets it, in its own Compose file.
- **Changes need your OK:** whatever changes something on your servers is started by you (risky steps ask first) or
  approved under "Actions" („Aktionen“), and it is logged. Without a click only what you explicitly allow runs: the
  "Act autonomously" mode („Selbstständig handeln“), a standing approval or automatic updates. Of these, only the
  antivirus quarantine is on out of the box: it moves findings right away (reversible, can be switched off).

Please do **not** report security issues in public issues. See [SECURITY.md](SECURITY.md).

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

**Servers and infrastructure**

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
| Scripts | Versioned script repository, run on one or many servers, scheduled – optionally without a click, as long as nothing changes |
| ntfy | Push notifications to your phone |

**More modules**

| Extension | What it does |
|---|---|
| Game servers (beta) | Start/stop game servers and show the current join code (e.g. Valheim) |
| Documents | Document archive with OCR and full-text search |
| Inventory | Devices and items with purchase data and warranty reminders |
| Nextcloud | Nextcloud as a source in the file manager, plus a tile with version, updates, free space, active users and load |
| Uptime Kuma (beta) | State of all monitors, availability over the last 24 hours, recent outages and expiring certificates (read-only) |
| Home Assistant (beta) | Version, pending updates, unavailable devices and selected values such as temperatures (read-only) |
| Jellyfin (beta) | Current playback with user and device, whether it transcodes, library sizes and recently added media (read-only); everyone with access to the page also sees users and titles |
| Plex (beta) | Current playback with user and device, library sizes and recently added media; talks only to your own server, never to plex.tv (read-only); everyone with access to the page also sees users and titles |
| qBittorrent (beta) | Speed, active torrents, count per state and free space (read-only); everyone with access to the page also sees the torrent names |
| Synology DSM (beta) | Model, DSM version, load, storage pools, volumes, disks, Hyper Backup and whether an update is available (read-only); reports when something is wrong; best with a dedicated DSM account without two-factor, and for storage, updates and Hyper Backup a member of the "administrators" group |
| Vaultwarden (beta) | Whether your password manager is running, which version, whether it is reachable over HTTPS and whether strangers can sign up; Nodvard Deck never sees a password (read-only) |

"Beta" means: the extension works, but is not yet as thoroughly tested as the rest. Feedback helps.

You do **not** need Proxmox: a Raspberry Pi, a Debian VM, a NAS or a few Docker hosts are enough. You add
servers by hand; load, Docker containers, updates and security all work over SSH.

Extensions are built against a documented SDK, so you can write your own —
see [docs/en/EXTENSION-API.md](docs/en/EXTENSION-API.md).

## Getting started

### Quick start with Docker Compose

**Requirements:** Docker with Docker Compose. Nodvard Deck runs fine on a Raspberry Pi 4/5 (arm64) as well as on any
x86-64 machine. Nothing is built: the ready-made image comes from the GitHub Container Registry
(`ghcr.io/nodvard/deck`).

The essentials of [deploy/compose.standalone.yml](deploy/compose.standalone.yml), simplified; the complete file also
contains a health check and a limit for the log file:

```yaml
services:
  nodvard-deck:
    image: ghcr.io/nodvard/deck:latest
    restart: unless-stopped
    ports:
      - "${NODVARD_DECK_PORT:-8080}:8080"
    volumes:
      - nodvard_deck_data:/app/data
# …
volumes:
  nodvard_deck_data:
    name: nodvard-deck-data
```

Nodvard Deck is then reachable on port 8080 (change the port with `NODVARD_DECK_PORT`), and all data lives in the
volume `nodvard_deck_data` under `/app/data`. Download the file and start it:

```bash
mkdir ~/nodvard-deck && cd ~/nodvard-deck
curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml
docker compose up -d
```

**Windows:** the commands are for a Linux shell (also WSL or Git Bash). In PowerShell, write
`curl.exe` instead of `curl` and run the commands one by one.

**Note:** the web interface, including the setup assistant, is currently in German only.

- **Open:** `http://<your-server>:8080`
- **Port already in use?** (message "port is already allocated" or "address already in use"): put a file
  `.env` with the line `NODVARD_DECK_PORT=8081` next to `compose.yml` and run `docker compose up -d` again.
  The address is then `http://<your-server>:8081`.
- **Setup code:** on the first start, Nodvard Deck writes a setup code to the container's log:
  `docker compose logs nodvard-deck` in the same folder; the log line is in German and starts with
  "Einrichtungscode". You enter it in the browser when you create the first account, the owner. That way the first
  person to open the page does not automatically become the owner. The code stays the same across restarts until the
  account exists; after that it is no longer needed
  ([docs/en/FIRST-SETUP.md, section 2](docs/en/FIRST-SETUP.md#2-setup-wizard-setup)).
- **Updating:** in the same folder, `docker compose pull && docker compose up -d`. Your data is kept.

**Raspberry Pi:** the image is available for `linux/arm64`, so you need a **64-bit system** (`uname -m` shows
`aarch64`). The commands are the same, just with `sudo` in front (`sudo docker compose up -d`) as long as your user is
not in the `docker` group. An SD card can die: do not keep backups only on the Pi
([section 10 of the guide](docs/en/FIRST-SETUP.md#10-back-up-the-dashboard-itself)). More in the
[guide for the Pi](docs/en/FIRST-SETUP.md#raspberry-pi-proxmox-and-docker-desktop).

Then follow the setup assistant in the browser. Everything else – servers, SSH access, modules,
backups – is set up in the web interface.

The full step-by-step guide (setup code, SSH access, Proxmox token, push notifications,
backups of Nodvard Deck itself, emergency commands) is in
[docs/en/FIRST-SETUP.md](docs/en/FIRST-SETUP.md).

### Further ways

For these ways, Docker or Podman alone is enough in some cases. Each one is described step by step in
[docs/en/FIRST-SETUP.md, section 1.1](docs/en/FIRST-SETUP.md#11-installation-without-the-repo):

- **In a terminal:** a single `docker run` command, Podman
  ([guide](docs/en/FIRST-SETUP.md#in-a-terminal))
- **With a web interface:** Portainer (from the repository or with the web editor) and Dockge
  ([guide](docs/en/FIRST-SETUP.md#with-a-web-interface)). In Portainer's web editor, paste the **content** of the
  Compose file, not the commands from the quick start; otherwise Portainer reports "top-level object must be a
  mapping".
- **On a NAS:** Synology, UGREEN, QNAP, Unraid, TrueNAS SCALE, OpenMediaVault, ZimaOS and CasaOS
  ([guide](docs/en/FIRST-SETUP.md#on-a-nas))
- **On particular machines:** Raspberry Pi, Proxmox (VM or LXC container) and Docker Desktop for trying it out
  ([guide](docs/en/FIRST-SETUP.md#raspberry-pi-proxmox-and-docker-desktop))

**Building from source** (developers): clone the repository, then run `docker compose build --pull && docker compose up -d`
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

## How it is tested

Before every release, all tests for backend, frontend and extensions run together with the check scripts (licenses,
core independent of the modules, no secrets in the code), and the image is built as a trial. The release workflow on
GitHub (`.github/workflows/release.yml`) then builds and checks the published images.

## Contributing

Nodvard Deck is free for personal and other noncommercial use, and its source code is available to read.

- **Pull requests** for this repository are not accepted by the project at the moment.
- **Welcome** are bug reports, ideas and feedback: see [CONTRIBUTING.md](CONTRIBUTING.md) for how. Questions, ideas and
  chat: join the [Discord](https://discord.gg/gTTs7qMabt) (mostly German, English is welcome in #english).
- **Welcome** too are your own extensions built with the SDK, see
  [docs/en/EXTENSION-API.md](docs/en/EXTENSION-API.md).

Nodvard Deck is a private, noncommercial project.

## Deck or Dock?

- **Nodvard Deck** is the whole dashboard with all modules: servers, Proxmox, Docker, backups, security and more.
- **[Nodvard Dock](https://github.com/nodvard/dock)** is Nodvard Deck with only the Docker module: the same code, just
  less of it. Pick Dock if all you want is to manage the containers and stacks on your servers.

## License

Nodvard Deck is licensed under the **PolyForm Noncommercial License 1.0.0** — see
[LICENSE](LICENSE). In short (only the license text is binding): you may use Nodvard Deck for personal
and other noncommercial purposes, read the source code and adapt it for yourself. Commercial use,
for example inside a company or as a paid service, is not permitted without separate permission.
Inquiries: **kontakt@nodvard.com**.
The source code is available, but Nodvard Deck is not open source software in the strict sense.
Licenses of bundled third-party libraries are listed in [THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES).

**Why not an open source license?** Nodvard Deck is built by one person, and the license is meant to keep anyone from
selling it as their own product or paid service without asking. The source stays open to read all the same: you can
check what the program does on your servers, adapt it for yourself and report bugs. If you want to use it in a company
or commercially, just ask at **kontakt@nodvard.com**.

## Trademarks

Proxmox, Docker, Portainer, Synology, Unraid, Nextcloud, Pi-hole, Nginx Proxy Manager, ntfy, Ollama, ClamAV, Lynis,
Fail2ban, Raspberry Pi and other names mentioned are trademarks of their respective owners. Nodvard Deck is not affiliated with them.

© 2026 Nico Benks
