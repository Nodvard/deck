<div align="center">

# Nodvard Deck

**Ein Dashboard für dein ganzes Homelab – Server, VMs, Container, Backups, Sicherheit und mehr.**

Selbst gehostet · Erweiterbar · White-Label-fähig

[English](README.en.md) · [Dokumentation](docs/README.md) · [Loslegen](#loslegen) · [Discord](https://discord.gg/gTTs7qMabt) · [Feedback](CONTRIBUTING.md)

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
| Proxmox-Backups (nur mit Proxmox) | Alle Proxmox-Backups auf einen Blick, Backup-Jobs anlegen und bearbeiten, Warnungen bei fehlenden oder fehlgeschlagenen Backups |
| Docker | Docker-Container auf deinen Servern: Start/Stopp, Live-Logs, Details, Aufräumen, Compose-Stacks, Image-Update-Prüfung |
| Server-Zustand | Linux-Server per SSH: Betriebssystem, Last, Platten, fehlgeschlagene Dienste, ausstehende Updates |
| Nodvard Shield | Sicherheit: Virenscan (ClamAV), Härtungs-Audits (Lynis), Update-Zentrale, Einbruchserkennung, Datei-Integrität, KI-Container-Wache |
| Pi-hole (Beta) | Statistiken, Blocken pausieren und fortsetzen, Sperrlisten neu laden, Domains sperren oder erlauben |
| Nginx Proxy Manager (Beta) | Proxy-Hosts ein- und ausschalten, Zertifikatsablauf, Adress-Check zu den Containern |
| Netzwerk (Beta) | Nur noch Wegweiser zu Pi-hole und Nginx Proxy Manager; kann ausgeschaltet bleiben |
| Gameserver (Beta) | Gameserver starten/stoppen und aktuellen Beitrittscode anzeigen (z. B. Valheim) |
| Skripte | Versionierte Skript-Bibliothek, auf einem oder vielen Servern ausführen, zeitgesteuert – auf Wunsch ohne Klick, solange sich nichts ändert |
| Dokumente | Dokumentenarchiv mit Texterkennung (OCR) und Volltextsuche |
| Inventar | Geräte und Gegenstände mit Kaufdaten und Garantie-Erinnerung |
| Nextcloud | Nextcloud als Quelle im Dateimanager, dazu eine Kachel mit Version, Updates, freiem Speicher, aktiven Nutzern und Last |
| ntfy | Push-Benachrichtigungen aufs Handy |
| Uptime Kuma (Beta) | Zustand aller Monitore, Verfügbarkeit der letzten 24 Stunden, letzte Ausfälle und ablaufende Zertifikate (nur lesend) |
| Home Assistant (Beta) | Version, bereitstehende Updates, nicht verfügbare Geräte und ausgewählte Werte wie Temperaturen (nur lesend) |
| Jellyfin (Beta) | Laufende Wiedergaben mit Benutzer und Gerät, ob transkodiert wird, Größe der Bibliotheken und neue Medien (nur lesend) |
| Plex (Beta) | Laufende Wiedergaben mit Benutzer und Gerät, Größe der Bibliotheken und neue Medien; spricht nur mit deinem Server, nie mit plex.tv (nur lesend) |
| qBittorrent (Beta) | Geschwindigkeit, aktive Torrents, Anzahl je Zustand und freier Platz (nur lesend) |

„Beta“ heißt: Die Erweiterung funktioniert, ist aber noch nicht so gründlich erprobt wie der Rest. Rückmeldungen helfen.

Du brauchst **kein Proxmox**: Ein Raspberry Pi, eine Debian-VM, ein NAS oder ein paar Docker-Hosts reichen.
Server legst du von Hand an, Auslastung, Docker-Container, Updates und Sicherheit laufen per SSH.

Erweiterungen bauen auf einem dokumentierten SDK auf – du kannst also eigene schreiben,
siehe [docs/02-EXTENSION-API.md](docs/02-EXTENSION-API.md).

## Loslegen

**Voraussetzung:** Docker mit Docker Compose. Nodvard Deck läuft auf einem Raspberry Pi 4/5
(arm64) genauso wie auf jedem x86-64-Rechner. Es wird nichts gebaut: Das fertige Image kommt aus
der GitHub-Container-Registry (`ghcr.io/nodvard/deck`).

Such dir einen Weg aus und klapp ihn auf:

<details>
<summary><b>Docker Compose im Terminal</b></summary>

```bash
mkdir ~/nodvard-deck && cd ~/nodvard-deck
curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml
docker compose up -d
```

**Windows:** Die Befehle sind für eine Linux-Shell (auch WSL oder Git Bash). In PowerShell `curl.exe`
statt `curl` schreiben und die Befehle einzeln ausführen.

- **Öffnen:** `http://<dein-server>:8080`
- **Port schon belegt?** (Meldung „port is already allocated“ oder „address already in use“): Neben die
  `compose.yml` eine Datei `.env` mit der Zeile `NODVARD_DECK_PORT=8081` legen und `docker compose up -d`
  noch einmal ausführen. Die Adresse ist dann `http://<dein-server>:8081`.
- **Einrichtungscode:** `docker compose logs nodvard-deck` im selben Ordner, die Zeile beginnt mit
  „Einrichtungscode“.
- **Aktualisieren:** im selben Ordner `docker compose pull && docker compose up -d`. Deine Daten bleiben
  erhalten.

</details>

<details>
<summary><b>Portainer, Weg 1: aus dem Repository</b></summary>

1. **Stacks → Add stack**, Name z. B. `nodvard-deck` (nur Kleinbuchstaben, Ziffern, `-` und `_`).
2. Als Aufbau **Repository** wählen und eintragen:
   - **Repository URL:** `https://github.com/nodvard/deck`
   - **Repository reference:** `refs/heads/main`
   - **Compose path:** `deploy/compose.standalone.yml`

   Eine Anmeldung brauchst du nicht, das Repository ist öffentlich.
3. **Deploy the stack**.

- **Öffnen:** `http://<dein-server>:8080`
- **Port schon belegt?** Unter **Environment variables** die Variable `NODVARD_DECK_PORT` mit z. B. `8081`
  anlegen, beim neuen Stack vor „Deploy the stack“, beim bestehenden danach neu ausrollen
  („Pull and redeploy“). Die Adresse ist dann `http://<dein-server>:8081`.
- **Einrichtungscode:** **Containers** → `nodvard-deck-nodvard-deck-1` (allgemein
  `<Stack-Name>-nodvard-deck-1`) → **Logs**, die Zeile beginnt mit „Einrichtungscode“.
- **Aktualisieren:** im Stack **Pull and redeploy** mit eingeschaltetem **Re-pull image**. Die
  automatischen Updates von Portainer („Automatic updates“, in neueren Versionen „GitOps updates“) kannst
  du optional einschalten: Sie übernehmen Änderungen an der Compose-Datei im Repository von selbst. Eine
  neue Version von Nodvard Deck ist aber ein neues Image und keine Änderung an der Datei; die holst du
  mit „Pull and redeploy“.

</details>

<details>
<summary><b>Portainer, Weg 2: Web-Editor</b></summary>

1. Die Datei [deploy/compose.standalone.yml](deploy/compose.standalone.yml) öffnen
   ([als reiner Text](https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml))
   und ihren **ganzen Inhalt** kopieren. Er beginnt mit `# Nodvard Deck ohne das Repository installieren`
   und enthält weiter unten `services:`.
2. **Stacks → Add stack**, Name z. B. `nodvard-deck`, als Aufbau **Web editor**, den Inhalt einfügen,
   **Deploy the stack**.

**Nicht** die drei Befehle aus dem Terminal-Weg (`mkdir …`, `curl …`, `docker compose up -d`) einfügen.
Das sind Befehle für die Shell, keine Compose-Datei; Portainer meldet dann „top-level object must be a
mapping“.

- **Öffnen:** `http://<dein-server>:8080`
- **Port schon belegt?** Unter **Environment variables** die Variable `NODVARD_DECK_PORT` mit z. B. `8081`
  anlegen, beim neuen Stack vor „Deploy the stack“, beim bestehenden danach „Update the stack“. Die
  Adresse ist dann `http://<dein-server>:8081`.
- **Einrichtungscode:** **Containers** → `nodvard-deck-nodvard-deck-1` (allgemein
  `<Stack-Name>-nodvard-deck-1`) → **Logs**, die Zeile beginnt mit „Einrichtungscode“.
- **Aktualisieren:** Stack öffnen → **Editor** → **Update the stack** mit eingeschaltetem
  **Re-pull image and redeploy**. Am Inhalt im Editor musst du dafür nichts ändern.

</details>

<details>
<summary><b>Raspberry Pi</b></summary>

- Ein Raspberry Pi 4 oder 5 mit 4 GB RAM und einem **64-Bit-System** (`uname -m` zeigt `aarch64`); das
  Image gibt es für `linux/arm64`. Im Leerlauf braucht Nodvard Deck rund 100 MB Arbeitsspeicher, das Image
  ist knapp 400 MB groß. Docker mit Docker Compose muss installiert sein.
- Eine SD-Karte kann sterben: Sicherungen nicht nur auf dem Pi lassen
  ([docs/11, Abschnitt 10](docs/11-ERST-EINRICHTUNG.md#10-das-dashboard-selbst-sichern)); eine USB-SSD
  hält länger als eine SD-Karte.
- Läuft auf dem Pi Portainer, gelten die beiden Portainer-Wege oben genauso.

```bash
mkdir ~/nodvard-deck && cd ~/nodvard-deck
curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml
sudo docker compose up -d
```

Ist dein Benutzer in der Gruppe `docker`, lässt du `sudo` weg. Der erste Start dauert etwas, weil das
Image erst geladen wird.

- **Öffnen:** `http://<adresse-des-pi>:8080`
- **Port schon belegt?** Neben die `compose.yml` eine Datei `.env` mit der Zeile `NODVARD_DECK_PORT=8081`
  legen und `sudo docker compose up -d` noch einmal ausführen. Die Adresse ist dann
  `http://<adresse-des-pi>:8081`.
- **Einrichtungscode:** `sudo docker compose logs nodvard-deck` im selben Ordner, die Zeile beginnt mit
  „Einrichtungscode“.
- **Aktualisieren:** im selben Ordner `sudo docker compose pull && sudo docker compose up -d`. Deine Daten
  bleiben erhalten.

</details>

Danach im Browser dem Einrichtungs-Assistenten folgen. Alles Weitere – Server, SSH-Zugang, Module,
Sicherung – richtest du in der Oberfläche ein.

Die komplette Schritt-für-Schritt-Anleitung (Einrichtungscode, SSH-Zugang, Proxmox-Token,
Push-Nachrichten, Sicherung von Nodvard Deck selbst, Notfall-Befehle) steht in
[docs/11-ERST-EINRICHTUNG.md](docs/11-ERST-EINRICHTUNG.md).

**Selbst bauen** (für Entwickler): Repository klonen, dann in `deploy/` `docker compose build --pull && docker compose up -d`.
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
[CONTRIBUTING.md](CONTRIBUTING.md). Fragen, Ideen und Austausch gibt es im [Discord](https://discord.gg/gTTs7qMabt).
Code-Beiträge von außen nimmt das Projekt nicht an. Nodvard Deck ist
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
