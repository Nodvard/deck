# Nodvard Dock documentation

[Deutsch](../../dock/README.md) · [Dock quick start](../../../deploy/nodvard-dock/README.en.md#getting-started) · [Nodvard Deck](../../../README.en.md)

Self-hosted, source available (PolyForm Noncommercial), free for personal use.
Dock manages Docker over SSH. The interface is currently in German; the English guides quote the same button labels.

| Guide | Contents |
|---|---|
| [Stacks from Git](stacks-from-git.md) | First stack, HTTPS source, comparison, approval, rollback and limits |
| [Image updates](image-updates.md) | Checking versus applying, backups and rollback |
| [Templates](templates.md) | Existing catalog, ports and setup |
| [Troubleshooting](troubleshooting.md) | Startup, login, SSH, Git, updates and recovery |

## Edition and scope

Dock is built from the same source as Deck. These differences follow from the build and edition definition:

| Property | Dock | Deck | Code reference |
|---|---|---|---|
| Build | `EDITION=dock`, `NODVARD_DECK_EXTENSIONS=docker` | Module selection through `NODVARD_DECK_EXTENSIONS`, all modules when omitted | [Dockerfile](../../../deploy/Dockerfile), [module selection](../../../deploy/select_extensions.sh) |
| Image and Compose service | `ghcr.io/nodvard/dock`, `nodvard-dock` | `ghcr.io/nodvard/deck`, `nodvard-deck` | [edition.py](../../../backend/src/nodvard_deck/edition.py), [Dock Compose](../../../deploy/nodvard-dock/compose.yml) |
| Setup assistant | `choose_modules=False`, bundled modules enabled automatically | `choose_modules=True`, modules selectable | [edition.py](../../../backend/src/nodvard_deck/edition.py), [SetupPage.tsx](../../../frontend/src/routes/SetupPage.tsx) |
| Update tools | `repo_tools=False`, use Compose | `repo_tools=True`, Deck repository tools also available | [edition.py](../../../backend/src/nodvard_deck/edition.py) |

Dock contains only the Docker module and the shared core with accounts, the vault, approvals and Dock backups.
Server modules such as Proxmox, Server-Zustand and Nodvard Shield are not shipped in the Dock build.
Git, image updates and templates come from the shared
[Docker module](../../../extensions/docker/src/nodvard_deck_ext_docker/__init__.py); it has no separate Dock implementation of these features.
[Templates](templates.md) install applications as stacks, without adding Deck modules.

## Operation

The [quick start](../../../deploy/nodvard-dock/README.en.md) covers Compose, first login, the first Docker host,
Dock updates, backup and restore. Dock data lives in the `nodvard-dock-data` volume.
Application data stays on the Docker hosts and needs separate backups.
For HTTPS in front of Dock, use the shared [reverse proxy guide](../REVERSE-PROXY.md).

The [PolyForm Noncommercial License 1.0.0](../../../LICENSE) applies. Commercial use needs separate permission.
