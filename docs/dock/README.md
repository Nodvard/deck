# Nodvard Dock: Dokumentation

[English](../en/dock/README.md) · [Dock-Schnellstart](../../deploy/nodvard-dock/README.md#loslegen) · [Nodvard Deck](../../README.md)

Self-hosted, Quelltext einsehbar (PolyForm Noncommercial), kostenlos für private Nutzung.
Dock verwaltet Docker über SSH. Die Oberfläche ist derzeit auf Deutsch; die englischen Anleitungen nennen dieselben Schaltflächen.

| Anleitung | Inhalt |
|---|---|
| [Stacks aus Git](stacks-from-git.md) | Erster Stack, HTTPS-Quelle, Vergleich, Freigabe, Rückweg und Grenzen |
| [Image-Updates](image-updates.md) | Prüfen und Einspielen unterscheiden, Sicherung und Rückweg |
| [Vorlagen](templates.md) | Vorhandener Katalog, Ports und Einrichtung |
| [Fehlerbehebung](troubleshooting.md) | Start, Login, SSH, Git, Updates und Wiederherstellung |

## Edition und Umfang

Dock entsteht aus demselben Quelltext wie Deck. Die folgenden Unterschiede ergeben sich aus dem Build und der Editionsdefinition:

| Eigenschaft | Dock | Deck | Codebeleg |
|---|---|---|---|
| Build | `EDITION=dock`, `NODVARD_DECK_EXTENSIONS=docker` | Modulauswahl über `NODVARD_DECK_EXTENSIONS`, ohne Auswahl alle Module | [Dockerfile](../../deploy/Dockerfile), [Modulauswahl](../../deploy/select_extensions.sh) |
| Image und Compose-Dienst | `ghcr.io/nodvard/dock`, `nodvard-dock` | `ghcr.io/nodvard/deck`, `nodvard-deck` | [edition.py](../../backend/src/nodvard_deck/edition.py), [Dock-Compose](../../deploy/nodvard-dock/compose.yml) |
| Einrichtungsassistent | `choose_modules=False`, mitgelieferte Module automatisch aktiv | `choose_modules=True`, Module auswählbar | [edition.py](../../backend/src/nodvard_deck/edition.py), [SetupPage.tsx](../../frontend/src/routes/SetupPage.tsx) |
| Update-Werkzeuge | `repo_tools=False`, Compose verwenden | `repo_tools=True`, auch Deck-Repository-Werkzeuge | [edition.py](../../backend/src/nodvard_deck/edition.py) |

Dock enthält nur das Docker-Modul und den gemeinsamen Kern mit Konten, Tresor, Freigaben und Dock-Sicherung.
Server-Module wie Proxmox, Server-Zustand und Nodvard Shield werden im Dock-Build nicht mitgeliefert.
Die Docker-Funktionen für Git, Image-Updates und Vorlagen stammen aus dem gemeinsamen
[Docker-Modul](../../extensions/docker/src/nodvard_deck_ext_docker/__init__.py); dort gibt es keine eigene Dock-Sonderausgabe dieser Funktionen.
Die [Vorlagen](templates.md) installieren Anwendungen als Stacks, keine zusätzlichen Deck-Module.

## Betrieb

Der [Schnellstart](../../deploy/nodvard-dock/README.md) beschreibt Compose, ersten Login und ersten Docker-Host sowie
Dock-Updates, Sicherung und Wiederherstellung. Dock-Daten liegen im Volume `nodvard-dock-data`.
Anwendungsdaten bleiben auf den Docker-Hosts und müssen separat gesichert werden.
Für HTTPS vor Dock gilt die gemeinsame [Reverse-Proxy-Anleitung](../12-REVERSE-PROXY.md).

Es gilt die [PolyForm Noncommercial License 1.0.0](../../LICENSE). Kommerzielle Nutzung braucht eine gesonderte Erlaubnis.
