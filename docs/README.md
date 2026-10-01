# Nodvard Deck documentation

> The user guides and the extension API are available in English under [en/](en/).
> The other documents are in German. If something is unclear, open an issue — see
> [CONTRIBUTING.md](../CONTRIBUTING.md).

## For users

| Document | Content |
|---|---|
| [en/FIRST-SETUP.md](en/FIRST-SETUP.md) · [Deutsch](11-ERST-EINRICHTUNG.md) | First setup step by step: start, setup assistant, users, extensions, servers and SSH keys, Proxmox, ntfy, automation, backups, troubleshooting |
| [en/PROXMOX-TOKEN.md](en/PROXMOX-TOKEN.md) · [Deutsch](10-PROXMOX-TOKEN.md) | Creating a Proxmox API token with limited rights |
| [../deploy/README.md](../deploy/README.md) | Building the image, deployment, backup and restore, demo mode |
| [../SECURITY.md](../SECURITY.md) | Reporting vulnerabilities, hardening notes |

## For developers

| Document | Content |
|---|---|
| [00-DECISIONS.md](00-DECISIONS.md) | Architecture decisions and their reasons |
| [01-ARCHITECTURE.md](01-ARCHITECTURE.md) | Overall architecture: core, extensions, gate, scheduler |
| [en/EXTENSION-API.md](en/EXTENSION-API.md) · [Deutsch](02-EXTENSION-API.md) | How to write an extension with `nodvard_sdk` |
| [03-DATA-MODEL.md](03-DATA-MODEL.md) | Database tables and relations |
| [04-API.md](04-API.md) | REST and WebSocket API reference |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Feedback and issues, notes for building it yourself |
