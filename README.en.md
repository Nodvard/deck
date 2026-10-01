<div align="center">

# Nodvard Deck

**One dashboard for your whole homelab — servers, VMs, containers, backups, security and more.**

Self-hosted · Extensible · White-label ready

[Deutsch](README.md) · [Documentation](docs/README.md) · [Getting started](#getting-started) · [Feedback](CONTRIBUTING.md)

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
| Backups (Proxmox only) | All Proxmox backups at a glance, create and edit backup jobs, warnings for missing or failed backups |
| Service Matrix | Docker containers on your servers: start/stop, live logs, details, storage cleanup, compose stacks, image update check |
| System | Linux servers over SSH: OS, load, disks, failed services, pending updates |
| Nodvard Shield | Security: antivirus (ClamAV), hardening audits (Lynis), update center, intrusion detection, file integrity monitoring, AI container watch |
| Network | Pi-hole and Nginx Proxy Manager: statistics, pause blocking, proxy hosts and certificate expiry |
| Game servers | Start/stop game servers and show the current join code (e.g. Valheim) |
| Scripts | Versioned script repository, run on one or many servers, scheduled |
| Documents | Document archive with OCR and full-text search |
| Inventory | Devices and items with purchase data and warranty reminders |
| Nextcloud | Nextcloud as a source in the file manager |
| ntfy | Push notifications to your phone |

You do **not** need Proxmox: a Raspberry Pi, a Debian VM, a NAS or a few Docker hosts are enough. You add
servers by hand; load, Docker containers, updates and security all work over SSH.

Extensions are built against a documented SDK, so you can write your own —
see [docs/en/EXTENSION-API.md](docs/en/EXTENSION-API.md).

## Getting started

**Note:** The user interface is currently in German only; an English interface is planned.

**Requirements:** Docker with Docker Compose. Nodvard Deck runs fine on a Raspberry Pi 4/5
(arm64) as well as on any x86-64 machine.

```bash
mkdir ~/nodvard-deck && cd ~/nodvard-deck
curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml
docker compose up -d
```

**Windows:** the commands are for a Linux shell (also WSL or Git Bash). In PowerShell, write
`curl.exe` instead of `curl` and run the commands one by one.

Then open `http://<your-server>:8080` and follow the setup assistant. The setup code is
shown in the container log (`docker compose logs nodvard-deck` in the same folder, or
"Logs" in Portainer, Docker Desktop, Synology or Unraid); the log line is in German and
starts with "Einrichtungscode". Everything else – servers, SSH
access, modules, backups – is set up in the web interface.

With **Portainer** or a similar tool: paste the content of
[deploy/compose.standalone.yml](deploy/compose.standalone.yml) as a new stack and deploy it.

**Updating:** in the same folder, `docker compose pull && docker compose up -d`
(or, in Portainer, update the stack with "Re-pull image and redeploy"). Your data is kept.

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
[CONTRIBUTING.md](CONTRIBUTING.md). The project does not accept code contributions from outside.
Commercial use needs a separate license: kontakt@nodvard.com.

## Security

Please do **not** report security issues in public issues. See [SECURITY.md](SECURITY.md).

## License

Nodvard Deck is licensed under the **PolyForm Noncommercial License 1.0.0** — see
[LICENSE](LICENSE). In short (only the license text is binding): you may use Nodvard Deck for personal
and other noncommercial purposes, read the source code and adapt it for yourself. Any commercial use,
for example inside a company or as a paid service, needs a commercial license: **kontakt@nodvard.com**.
The source code is available, but Nodvard Deck is not open source software in the strict sense.
Licenses of bundled third-party libraries are listed in [THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES).

## Trademarks

Proxmox, Docker, Portainer, Synology, Unraid, Nextcloud, Pi-hole, Nginx Proxy Manager, ntfy, Ollama, ClamAV, Lynis,
Fail2ban, Raspberry Pi and other names mentioned are trademarks of their respective owners. Nodvard Deck is not affiliated with them.

© 2026 Nico Benks
