# Nodvard Deck documentation

> The user guides, the extension API, the architecture decisions, the architecture and the data model are available in English under [en/](en/).
> The other documents are in German. If something is unclear, open an issue — see
> [CONTRIBUTING.md](../CONTRIBUTING.md).

## For users

| Document | Content |
|---|---|
| [en/dock/README.md](en/dock/README.md) · [Deutsch](dock/README.md) | Nodvard Dock: Docker edition, quick start, stacks from Git, image updates, templates and troubleshooting |
| [en/FIRST-SETUP.md](en/FIRST-SETUP.md) · [Deutsch](11-ERST-EINRICHTUNG.md) | First setup step by step: quick start, setup assistant, first server, extensions, push notifications, backups, common stumbling blocks; appendix with users, two-factor, automation, updates, troubleshooting and rescue page |
| [en/INSTALLATION.md](en/INSTALLATION.md) · [Deutsch](13-INSTALLATION.md) | Installation per platform: terminal, Portainer, Dockge, Synology, UGREEN, QNAP, Unraid, TrueNAS, OpenMediaVault, ZimaOS, Raspberry Pi, Proxmox, Docker Desktop |
| [en/ROOT-RIGHTS.md](en/ROOT-RIGHTS.md) · [Deutsch](14-ROOT-RECHTE.md) | Root rights without a password, the docker group and the modules' helper scripts |
| [en/SYNOLOGY-DSM.md](en/SYNOLOGY-DSM.md) · [Deutsch](15-SYNOLOGY-DSM.md) | The Synology DSM module: account, several devices, notifications, Hyper Backup |
| [en/BACKUP.md](en/BACKUP.md) · [Deutsch](16-SICHERUNG.md) | Backups in detail: NAS or NFS, opening a backup without Nodvard Deck, the backup scripts |
| [en/WITHOUT-INTERFACE.md](en/WITHOUT-INTERFACE.md) · [Deutsch](17-OHNE-OBERFLAECHE.md) | Setting up servers through the API with a small helper script |
| [en/PROXMOX-TOKEN.md](en/PROXMOX-TOKEN.md) · [Deutsch](10-PROXMOX-TOKEN.md) | Creating a Proxmox API token with limited rights |
| [en/REVERSE-PROXY.md](en/REVERSE-PROXY.md) · [Deutsch](12-REVERSE-PROXY.md) | Running Nodvard Deck behind Nginx Proxy Manager, Traefik or Caddy: trusted proxy, HTTPS, real client addresses |
| [en/TAILSCALE.md](en/TAILSCALE.md) · [Deutsch](13-TAILSCALE.md) | Connecting servers through Tailscale: installation, Docker connectivity, SSH access and troubleshooting |
| [../deploy/README.md](../deploy/README.md) | Building the image, deployment, backup and restore, demo mode |
| [../SECURITY.md](../SECURITY.md) | Reporting vulnerabilities, hardening notes |

## For developers

| Document | Content |
|---|---|
| [en/DECISIONS.md](en/DECISIONS.md) · [Deutsch](00-DECISIONS.md) | Architecture decisions and their reasons |
| [en/ARCHITECTURE.md](en/ARCHITECTURE.md) · [Deutsch](01-ARCHITECTURE.md) | Overall architecture: core, extensions, gate, scheduler |
| [en/EXTENSION-API.md](en/EXTENSION-API.md) · [Deutsch](02-EXTENSION-API.md) | How to write an extension with `nodvard_sdk` |
| [en/DATA-MODEL.md](en/DATA-MODEL.md) · [Deutsch](03-DATA-MODEL.md) | Database tables and relations |
| [en/API.md](en/API.md) · [Deutsch](04-API.md) | REST and WebSocket API reference |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Feedback and issues, notes for building it yourself |
