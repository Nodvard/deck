<div align="center">

# Nodvard Deck

**Ein Dashboard für dein ganzes Homelab – Server, VMs, Container, Backups, Sicherheit und mehr.**

Selbst gehostet · Erweiterbar · White-Label-fähig

[English](README.en.md) · [Dokumentation](docs/README.md) · [Loslegen](#loslegen) · [Feedback](CONTRIBUTING.md)

</div>

> **English:** This README is also available in English – see [README.en.md](README.en.md).

---

Nodvard Deck ersetzt die Handvoll Admin-Werkzeuge, die ein Homelab normalerweise braucht – die
Proxmox-Oberfläche, Portainer, eine Startseite wie Homarr, eine Backup-Übersicht, eine
Sicherheitskonsole – durch **eine Weboberfläche**. Jeder Server bekommt eine eigene Seite
(ähnlich wie bei Plesk) mit Status, Live-Auslastung, Aktionen und Einstellungen. Alles, was
etwas verändert, läuft zuerst durch eine **Freigabe** – so passiert nichts versehentlich.

> **Stand:** Nodvard Deck ist in aktiver Entwicklung (Beta). Es läuft rund um die Uhr in einem
> echten Homelab, Schnittstellen und Datenformate können sich aber noch ändern.

## Funktionen

**Kern**
- Dashboard mit frei anordenbaren Widgets und Statusübersicht („Cockpit“)
- Serverseiten: Status, Live-CPU/RAM, Aktionen, Werkzeuge und Dienste pro Host
- Freigabe-System: Änderungen werden erst vorgeschlagen und laufen nach Freigabe (optional Autonomie-Modus, Sperrliste, Wartungsfenster)
- Benutzer, Rollen und Rechte, Zwei-Faktor-Login, Audit-Log
- Verschlüsselter Tresor für API-Tokens und SSH-Zugänge
- Web-Terminal (SSH) und Dateimanager über alle Server
- Benachrichtigungszentrale, Push-Nachrichten über ntfy
- White-Label: Name, Logo und Farben sind Einstellungen, kein Code
- Als App auf dem Handy installierbar (PWA)

**Erweiterungen** (einzeln an- und abschaltbar)

| Erweiterung | Was sie macht |
|---|---|
| Proxmox VE (nur mit Proxmox) | Nodes, VMs und Container: Start/Stopp, Konsole im Browser, Snapshots, Hardware, Speicher, Updates, Festplatten-Zustand |
| Backups (nur mit Proxmox) | Alle Proxmox-Backups auf einen Blick, Backup-Jobs anlegen und bearbeiten, Warnungen bei fehlenden oder fehlgeschlagenen Backups |
| Service Matrix | Docker-Container auf deinen Servern: Start/Stopp, Live-Logs, Details, Aufräumen, Compose-Stacks, Image-Update-Prüfung |
| System | Linux-Server per SSH: Betriebssystem, Last, Platten, fehlgeschlagene Dienste, ausstehende Updates |
| Nodvard Shield | Sicherheit: Virenscan (ClamAV), Härtungs-Audits (Lynis), Update-Zentrale, Einbruchserkennung, Datei-Integrität, KI-Container-Wache |
| Netzwerk | Pi-hole und Nginx Proxy Manager: Statistiken, Blocken pausieren, Proxy-Hosts und Zertifikatsablauf |
| Gameserver | Gameserver starten/stoppen und aktuellen Beitrittscode anzeigen (z. B. Valheim) |
| Skripte | Versionierte Skript-Bibliothek, auf einem oder vielen Servern ausführen, zeitgesteuert |
| Dokumente | Dokumentenarchiv mit Texterkennung (OCR) und Volltextsuche |
| Inventar | Geräte und Gegenstände mit Kaufdaten und Garantie-Erinnerung |
| Nextcloud | Nextcloud als Quelle im Dateimanager |
| ntfy | Push-Benachrichtigungen aufs Handy |

Du brauchst **kein Proxmox**: Ein Raspberry Pi, eine Debian-VM, ein NAS oder ein paar Docker-Hosts reichen.
Server legst du von Hand an, Auslastung, Docker-Container, Updates und Sicherheit laufen per SSH.

Erweiterungen bauen auf einem dokumentierten SDK auf – du kannst also eigene schreiben,
siehe [docs/02-EXTENSION-API.md](docs/02-EXTENSION-API.md).

## Loslegen

**Voraussetzung:** Docker mit Docker Compose. Nodvard Deck läuft auf einem Raspberry Pi 4/5
(arm64) genauso wie auf jedem x86-64-Rechner.

```bash
mkdir ~/nodvard-deck && cd ~/nodvard-deck
curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml
docker compose up -d
```

**Windows:** Die Befehle sind für eine Linux-Shell (auch WSL oder Git Bash). In PowerShell `curl.exe`
statt `curl` schreiben und die Befehle einzeln ausführen.

Danach `http://<dein-server>:8080` öffnen und dem Einrichtungs-Assistenten folgen. Den
Einrichtungscode zeigt das Protokoll des Containers (`docker compose logs nodvard-deck` im selben
Ordner, oder in Portainer, Docker Desktop, Synology bzw. Unraid unter „Logs“). Alles
Weitere – Server, SSH-Zugang, Module, Sicherung – richtest du in der Oberfläche ein.

Mit **Portainer** oder einer anderen Oberfläche: den Inhalt von
[deploy/compose.standalone.yml](deploy/compose.standalone.yml) als neuen Stack einfügen und starten.

**Aktualisieren:** im selben Ordner `docker compose pull && docker compose up -d`
(bzw. in Portainer den Stack aktualisieren mit „Re-pull image and redeploy“). Deine Daten bleiben
dabei erhalten.

Die komplette Schritt-für-Schritt-Anleitung (Einrichtungscode, SSH-Zugang, Proxmox-Token,
Push-Nachrichten, Sicherung von Nodvard Deck selbst, Notfall-Befehle) steht in
[docs/11-ERST-EINRICHTUNG.md](docs/11-ERST-EINRICHTUNG.md).

**Selbst bauen** (für Entwickler): Repository klonen, dann in `deploy/` `docker compose up -d --build`.
Bauen, Deployen, Sichern und Wiederherstellen: [deploy/README.md](deploy/README.md).

## Aufbau

```
backend/     FastAPI (Python 3.12), SQLAlchemy + SQLite, Alembic-Migrationen
sdk/python/  nodvard_sdk – der stabile Vertrag für Erweiterungen
frontend/    Weboberfläche mit React + Vite + TypeScript + Tailwind
extensions/  ein Ordner pro Erweiterung (Python-Backend + gebautes Frontend)
deploy/      Dockerfile, docker-compose, Backup-/Restore-Skripte
docs/        Architektur, Extension-API, Datenmodell, API-Referenz
```

Der Kern kennt keinen Hersteller beim Namen – alles Spezifische (Proxmox, Docker, Pi-hole …)
steckt in Erweiterungen. Ein Prüfskript (`scripts/check_core_purity.py`) stellt das sicher.
Mehr dazu: [docs/01-ARCHITECTURE.md](docs/01-ARCHITECTURE.md).

## Entwicklung

```bash
# Backend
python -m venv .venv
.venv/bin/pip install -c deploy/constraints.txt -e sdk/python -e "backend[dev]"
.venv/bin/pytest sdk/python/tests backend/tests
python scripts/check_core_purity.py

# Frontend (im Ordner frontend/)
npm ci
npm run typecheck && npm run typecheck:extensions
npm test && npm run test:extensions
npm run build
```

Hinweise zum Selbstbauen und Prüfen: [CONTRIBUTING.md](CONTRIBUTING.md).

## Projekt unterstützen

Für private und andere nicht-kommerzielle Zwecke ist Nodvard Deck kostenlos, der Quellcode ist
einsehbar. Am meisten hilft es, wenn du Fehler meldest und Ideen einbringst – siehe
[CONTRIBUTING.md](CONTRIBUTING.md). Code-Beiträge von außen nimmt das Projekt nicht an. Nodvard Deck ist
ein privates, nicht-kommerzielles Projekt.

## Sicherheit

Sicherheitslücken bitte **nicht** in öffentlichen Issues melden – siehe [SECURITY.md](SECURITY.md).

## Lizenz

Nodvard Deck steht unter der **PolyForm Noncommercial License 1.0.0** – siehe [LICENSE](LICENSE).
Kurz gesagt (verbindlich ist nur der Lizenztext): Du darfst Nodvard Deck für private und andere
nicht-kommerzielle Zwecke nutzen, den Quellcode lesen und für dich anpassen. Kommerzielle
Nutzung, zum Beispiel in einem Unternehmen oder als bezahlter Dienst, ist ohne gesonderte Erlaubnis
nicht gestattet. Anfragen: **kontakt@nodvard.com**. Der Quellcode ist damit einsehbar, Nodvard Deck ist aber keine
Open-Source-Software im engeren Sinn. Die Lizenzen der mitgelieferten Bibliotheken stehen in
[THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES).

## Marken

Proxmox, Docker, Portainer, Synology, Unraid, Nextcloud, Pi-hole, Nginx Proxy Manager, ntfy, Ollama, ClamAV, Lynis,
Fail2ban, Raspberry Pi und andere genannte Namen sind Marken ihrer jeweiligen Inhaber. Nodvard Deck steht in keiner Verbindung zu ihnen.

© 2026 Nico Benks
