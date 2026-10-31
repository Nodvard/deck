# Nodvard Deck documentation

> The user guides, the extension API, the architecture decisions, the architecture and the data model are available in English under [en/](en/).
> The other documents are in German. If something is unclear, open an issue — see
> [CONTRIBUTING.md](../CONTRIBUTING.md).

## For users

| Document | Content |
|---|---|
| [en/FIRST-SETUP.md](en/FIRST-SETUP.md) · [Deutsch](11-ERST-EINRICHTUNG.md) | First setup step by step: start, setup assistant, users, extensions, servers and SSH keys, Proxmox, ntfy, automation, backups, troubleshooting |
| [en/PROXMOX-TOKEN.md](en/PROXMOX-TOKEN.md) · [Deutsch](10-PROXMOX-TOKEN.md) | Creating a Proxmox API token with limited rights |
| [en/REVERSE-PROXY.md](en/REVERSE-PROXY.md) · [Deutsch](12-REVERSE-PROXY.md) | Running Nodvard Deck behind Nginx Proxy Manager, Traefik or Caddy: trusted proxy, HTTPS, real client addresses |
| [../deploy/README.md](../deploy/README.md) | Building the image, deployment, backup and restore, demo mode |
| [../SECURITY.md](../SECURITY.md) | Reporting vulnerabilities, hardening notes |

## For developers

| Document | Content |
|---|---|
| [en/DECISIONS.md](en/DECISIONS.md) · [Deutsch](00-DECISIONS.md) | Architecture decisions and their reasons |
| [en/ARCHITECTURE.md](en/ARCHITECTURE.md) · [Deutsch](01-ARCHITECTURE.md) | Overall architecture: core, extensions, gate, scheduler |
| [en/EXTENSION-API.md](en/EXTENSION-API.md) · [Deutsch](02-EXTENSION-API.md) | How to write an extension with `nodvard_sdk` |
| [en/DATA-MODEL.md](en/DATA-MODEL.md) · [Deutsch](03-DATA-MODEL.md) | Database tables and relations |
| [04-API.md](04-API.md) | REST and WebSocket API reference |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Feedback and issues, notes for building it yourself |
