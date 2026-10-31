<div align="center">

# Nodvard Dock

**Docker-Verwaltung für deine Server: auf Deutsch, über SSH, ohne Agent.**

Selbst gehostet · Quelltext einsehbar (PolyForm Noncommercial) · kostenlos für den privaten Gebrauch

[English](README.en.md) · [Loslegen](#loslegen) · [Umsteigen von Portainer](#umsteigen-von-portainer) · [Discord](https://discord.gg/gTTs7qMabt)

[![Lizenz: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/Lizenz-PolyForm%20Noncommercial%201.0.0-blue)](../../LICENSE)
![Status: Beta](https://img.shields.io/badge/Status-Beta-orange)

</div>

> **Sprache:** Die Oberfläche von Nodvard Dock gibt es derzeit nur auf Deutsch.
>
> **English:** This README is also available in English – see [README.en.md](README.en.md). The web interface is
> currently in German only.

---

Nodvard Dock verwaltet die Container und Compose-Stacks auf deinen Servern in einer Weboberfläche. Es spricht mit
jedem Server über SSH: Auf den Servern läuft kein Agent, und der Container von Nodvard Dock bekommt keinen
Docker-Socket. Was etwas verändert, startest du selbst oder gibst es über eine **Freigabe** frei, und alles steht im
Protokoll. Gedacht ist es für Homelabs mit einem oder mehreren Servern, auch für Umsteiger von Portainer CE.

Nodvard Dock ist das Modul Docker aus [Nodvard Deck](https://github.com/nodvard/deck), als eigenes Image. Wer später
auch Proxmox, Backups oder Sicherheit im Blick haben will, nimmt Nodvard Deck.

> **Stand:** Beta. Schnittstellen und Datenformate können sich noch ändern. Das fertige Image `ghcr.io/nodvard/dock`
> gibt es für normale PCs und NAS (amd64) und für den Raspberry Pi (arm64).

## Auf einen Blick

| | Nodvard Dock | Portainer CE |
|---|---|---|
| **Weitere Server** | über SSH mit einem eigenen Benutzer, kein Agent | Portainer Agent, Edge Agent oder Docker-API je Server |
| **Docker-Socket im Container der Oberfläche** | nein | ja, für den Rechner, auf dem Portainer läuft |
| **Wo die Compose-Dateien liegen** | in einem Ordner auf dem Server (Vorgabe `/opt/stacks/<Name>`) | im Datenspeicher von Portainer |
| **Sprache der Oberfläche** | Deutsch | Englisch |
| **Lizenz** | PolyForm Noncommercial: privat kostenlos, Quelltext einsehbar | zlib (Open Source) |

Die Angaben zu Portainer CE stammen aus dessen öffentlicher Dokumentation und Lizenz (Stand: Oktober 2026).

## Was es kann

- **Alle Container auf einer Seite:** Zustand, Starten, Stoppen, Neustarten, Konsole und Details. Logs mit Zeitraum,
  Suche und Download. Gestoppte Container lassen sich löschen.
- **Image-Updates mit Rückweg:** Nodvard Dock findet neue Images und spielt sie bei Compose-Diensten ein. Das alte
  Image bleibt als Sicherung. Stürzt der neue Container beim Start ab, geht es von selbst zum alten zurück, außer bei
  Datenbanken und anderen Datenspeichern (abschaltbar in den Einstellungen des Moduls).
- **Stacks bleiben Ordner auf dem Server:** Vorhandene Stacks übernimmst du mit einem Klick, am Server ändert sich
  dabei nichts. Danach lassen sie sich anwenden, stoppen, herunterfahren (die Volumes bleiben) und ihre Images holen.
- **Compose-Editor mit Prüfung und Verlauf:** Bearbeiten als Text oder im Formular, das nur die geänderte Stelle
  anfasst. Vor dem Speichern prüft Docker Compose die Datei auf dem Server, die letzten Stände bleiben im Verlauf.
- **Neue Stacks aus Vorlagen:** Uptime Kuma, Vaultwarden, Nginx Proxy Manager und Gitea, oder eine eigene Datei. Jeder
  neue Stack bekommt einen eigenen Ordner (Vorgabe `/opt/stacks`). Löschen geht mit Herunterfahren; den Ordner löscht
  Nodvard Dock nur, wenn du es anhakst.
- **Stack-Updates mit Sicherung:** Die alten Images bleiben, „Zurück zur vorigen Version“ steht danach bereit. Auf
  Wunsch sichert Nodvard Dock vorher die Volumes, mit Platzprüfung; zurückspielen und löschen kannst du sie im Reiter
  Stacks unter „Sicherungen“.
- **Stacks aus Git:** Ein Stack bekommt ein Git-Repository als Quelle (nur `https://`, auch private). Vor dem
  Einspielen siehst du, welche Dateien neu sind oder sich ändern. Deine `.env` auf dem Server ersetzt Nodvard Dock nie.
- **Aus `docker run` wird ein Stack:** Ein einzeln gestarteter Container wird zu einem Stack mit Compose-Datei. Läuft
  der neue nicht, kommt der alte zurück. Läuft er, löscht Nodvard Dock den alten; was nur im Container lag, ist dann
  weg.
- **Images, Volumes und Netze** ansehen und einzeln löschen; unbenutzte Images und den Build-Cache auf einmal
  aufräumen.
- **Private Registries:** Das Passwort liegt im Tresor. Angemeldet wird je Server über die Freigabe, das Passwort
  steht dabei nie auf der Kommandozeile.
- **Freigabe und Protokoll:** Jede Änderung an Containern, Stacks, Images, Volumes und Netzen ist eine Aktion mit
  Risikostufe. Was ohne Rückfrage durchläuft, bestimmt der Inhaber.

<details>
<summary><b>Stacks aus Git: was beim Einspielen genau passiert</b></summary>

- Nodvard Dock holt den Stand und zeigt vorher den Vergleich. Eingespielt wird über die Freigabe.
- Umgebungsdateien auf dem Server (`.env` und die beim Übernehmen genannten) ersetzt Nodvard Dock nie.
- Von sich aus startet es nur die Dienste neu, die in der Freigabe stehen. Dienste, deren Angaben sich in der
  Compose-Datei ändern, erstellt Compose beim Anwenden ohnehin neu; das zeigt der Vergleich vorher.
- Lesen weitere Dienste vielleicht noch den alten Stand, nennt das Ergebnis sie mit dem Knopf „Jetzt neu starten“.
- Direkt nach dem Einspielen gibt es „Zurück zum Stand davor“. Das holt nur die Dateien zurück, keine Daten.
- Auf Wunsch sichert Nodvard Dock vorher die Volumes des Stacks wie beim Stack-Update. Die Dienste, die sie nutzen,
  stehen dafür still, bis Compose den Stack anwendet. Zurückspielen geht im Reiter Stacks unter „Sicherungen“.

</details>

## Loslegen

**Voraussetzung:** ein Rechner mit Docker und Docker Compose, etwa ein Raspberry Pi 4/5 mit 64-Bit-System oder ein
x86-64-Rechner.

### 1. Starten

```bash
mkdir ~/nodvard-dock && cd ~/nodvard-dock
curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/nodvard-dock/compose.yml
docker compose up -d
```

- **Öffnen:** `http://<dein-server>:8080`
- **Einrichtungscode:** `docker compose logs nodvard-dock` im selben Ordner, die Zeile beginnt mit
  „Einrichtungscode“.
- **Port schon belegt?** Neben die `compose.yml` eine Datei `.env` mit der Zeile `NODVARD_DOCK_PORT=8081` legen und
  `docker compose up -d` noch einmal ausführen.
- **Aktualisieren:** `docker compose pull && docker compose up -d`. Deine Daten bleiben erhalten.

### 2. Einrichten

Im Browser führt dich ein Assistent durch drei Schritte:

1. **Administrator-Konto anlegen** und die Zeitzone wählen.
2. **Was willst du nutzen?** Hier das Modul **Docker** einschalten.
3. **Zwei-Faktor-Anmeldung:** Der Schritt ist freiwillig, wir empfehlen ihn sehr.

### 3. Server verbinden, auch den eigenen

Nodvard Dock nutzt keinen Docker-Socket. Darum verbindet es sich mit jedem Server über SSH, auch mit dem Rechner, auf
dem es selbst läuft.

1. Unter **Einstellungen → Server & Zugänge** den Server anlegen, mit der Markierung `docker`. Für den eigenen Rechner
   seine Adresse im Heimnetz eintragen, nicht `localhost` (das wäre der Container selbst).
2. **Mit Passwort einrichten:** Du gibst einmal ein Konto mit root- oder sudo-Rechten an. Nodvard Dock legt damit
   einen eigenen Benutzer mit SSH-Schlüssel an, trägt ihn in die Gruppe `docker` ein und vergisst das Passwort.
3. Die Seite **Docker** zeigt jetzt die Container dieses Servers.

**Eigener Datenspeicher:** Nodvard Dock hat sein eigenes Volume `nodvard-dock-data`. Den Datenspeicher eines
Nodvard Deck kann es nicht übernehmen, weil darin Daten von Modulen liegen, die Dock nicht kennt.

> **Bekannt in dieser Beta:**
> - Die Oberfläche nennt sich an einigen Stellen noch „Nodvard Deck“.
> - Die Einrichtungsseite zeigt den Befehl für den Einrichtungscode mit `nodvard-deck`. Richtig ist
>   `docker compose logs nodvard-dock`.
> - Die Seite Einstellungen → System → Updates passt noch nicht zu Dock. Aktualisieren geht mit dem Befehl unter
>   „Starten“.

## Umsteigen von Portainer

1. **Nebeneinander starten:** Nodvard Dock einrichten, Portainer läuft weiter. Nodvard Dock ändert nichts, solange du
   nichts auslöst, und zeigt nach dem Einrichten alle Container, auch die aus Portainer.
2. **Stacks übernehmen:** Stacks aus einem Ordner auf dem Server übernimmst du im Reiter **Stacks** mit
   **Übernehmen**. Stacks, die Portainer selbst angelegt hat, tragen das Abzeichen „Portainer“. **Von Portainer
   übernehmen** holt Datei und Variablen aus Portainers Datenspeicher, legt sie in einen eigenen Ordner (Vorgabe
   `/opt/stacks/<Name>`) und rollt den Stack unter demselben Namen neu aus. Die Volumes bleiben dieselben.
3. **Portainer abschalten,** wenn alle Stacks umgezogen sind. Deine anderen Container laufen dabei weiter.

**Wichtig:** Einen umgezogenen Stack in Portainer weder löschen noch neu ausrollen. Er hat denselben Namen, und
Portainer würde die neuen Container entfernen oder mit seiner alten Datei ersetzen. Lass den Eintrag einfach stehen.

**Wann Nodvard Dock nicht umzieht:** wenn die Datei mit relativen Pfaden auf Daten bei Portainer zeigt, aus mehreren
Dateien besteht (etwa aus Git) oder nicht zu den laufenden Diensten passt. Dann nennt Nodvard Dock den Grund, bei
relativen Pfaden auch den Ort der Daten.

## Sicherheit und Datenschutz auf einen Blick

- **Keine Telemetrie:** Nodvard Dock schickt keine Nutzungs- oder Absturzdaten irgendwohin.
- **Von selbst nach außen** geht nur die Update-Suche: Einmal am Tag fragt Nodvard Dock bei GitHub nach einer neuen
  Version, GitHub sieht dabei deine IP-Adresse und den Zeitpunkt (abschaltbar unter Einstellungen → System →
  Updates). Alles andere geht nur dorthin, wo du es einrichtest: deine Server per SSH und die Registries und
  Git-Repositorys, die du einträgst. Schaltest du die Suche nach neuen Images ein (Standard aus), fragen deine
  Docker-Server bei den Registries ihrer Container nach. Ein Git-Repository fragt Nodvard Dock nur, wenn du es
  auslöst.
- **Kein Docker-Socket im Container:** Wer den Socket hat, ist root auf dem Rechner, und das wäre jeder, der in die
  Oberfläche kommt. Darum arbeitet Nodvard Dock über SSH mit einem eigenen Benutzer.
- **Die Gruppe `docker` ist so viel wie root:** Wer Docker steuern darf, kann jeden Ordner des Rechners in einen
  Container einhängen. Das gilt für jedes Werkzeug dieser Art, auch für Portainer.
- **Modus „Nur bedienen“ ohne die Gruppe:** Für Server mit der Markierung `docker-bedienen` erlaubt ein eigenes
  Hilfsskript per sudo nur Ansehen, Logs, Starten, Stoppen und Neustarten. Stacks, Updates und die Konsole gibt es
  dort nicht. Eine früher eingetragene Gruppe `docker` bleibt aber bestehen, eine früher angelegte volle sudo-Regel
  ebenso, bis du sie beim Einrichten unter „Erweitert“ mit „Volle sudo-Regel entfernen“ zurücknimmst. „Verbindung
  prüfen“ zeigt das an, ebenso einen Zugang als `root`. Aus der Gruppe nimmst du den Zugang mit
  `sudo gpasswd -d <benutzer> docker`, den Rest beschreibt die
  [Erst-Einrichtung, Abschnitt 6](../../docs/11-ERST-EINRICHTUNG.md#6-root-rechte-ohne-passwort-und-gruppe-docker).
- **Schlüssel im Tresor:** Die SSH-Schlüssel liegen verschlüsselt in Nodvard Dock. Wer Nodvard Dock bedienen darf,
  kann deine Server steuern. Darum: ein starkes Passwort, Zwei-Faktor an und Nodvard Dock nicht offen ins Internet
  stellen. Hinter einem Reverse Proxy mit HTTPS: [docs/12-REVERSE-PROXY.md](../../docs/12-REVERSE-PROXY.md).
- **Rollen:** Administrator, Bediener und Betrachter legen fest, wer was darf.
- **Stacks aus Git schreiben mit den Rechten des Ordners:** Nodvard Dock schreibt die Dateien mit den Rechten des
  Besitzers des Stack-Ordners. Legt ein Container im Ordner einen Verweis an, kann darüber also nur etwas
  überschrieben werden, wo der Besitzer ohnehin schreiben darf. Gehört der Ordner root, ist das der ganze Rechner, und
  dann schützt nur die Prüfung direkt vor jeder Datei. Darum Stacks in Ordnern eines normalen Benutzers anlegen, nicht
  unter root.

Sicherheitslücken bitte **nicht** öffentlich melden, sondern wie in der [SECURITY.md](SECURITY.md) beschrieben.

## Sichern und entfernen

- **Nodvard Dock selbst sichern** geht unter **Einstellungen → System → Sicherung**: verschlüsselt, auf Wunsch nach
  Zeitplan und zum Herunterladen. Darin stecken Konten, Einstellungen, der Tresor mit den SSH-Schlüsseln und die
  Merkliste der übernommenen Stacks. Für einen Ordner außerhalb des Datenspeichers (anderes Laufwerk, NAS) die Zeile
  `- ./sicherungen:/backups` in der `compose.yml` einkommentieren; was dazu nötig ist, steht dort.
- **Zurückholen** geht unter **Einstellungen → System → Wiederherstellen** oder bei einer frischen Installation im
  ersten Schritt des Assistenten mit **Oder: Sicherung einspielen**.
- **Die Volume-Sicherungen vor einem Stack-Update** liegen auf dem jeweiligen Server, im Heimatordner des Benutzers
  von Nodvard Dock (Vorgabe `nodvard`) unter `nodvard-sicherungen/<Stack>/<Zeit>/`, je Volume ein `.tar.gz`. Außer
  root kann sie nur dieser Benutzer lesen. Nodvard Dock löscht sie nicht von selbst; plane den Platz dafür ein.
- **Entfernen:** `docker compose down` im Ordner der `compose.yml` hält Nodvard Dock an. Die Daten bleiben im Volume
  `nodvard-dock-data`. Erst `docker compose down -v` löscht auch die Daten samt aller gespeicherten Schlüssel, also
  vorher sichern. Deine Container, Stacks und Volumes auf den Servern bleiben in beiden Fällen, wie sie sind. Den
  Benutzer, den Nodvard Dock auf den Servern angelegt hat, entfernst du dort bei Bedarf selbst.

## Selbst bauen

In einem Klon von [nodvard/deck](https://github.com/nodvard/deck), am besten auf einem PC (auf einem Raspberry Pi
dauert der Bau sehr lange):

```bash
docker build -f deploy/Dockerfile --build-arg NODVARD_DECK_EXTENSIONS=docker -t nodvard-dock:selbst .
```

Dann in der `compose.yml` die Zeile `image:` auf `nodvard-dock:selbst` ändern und `docker compose up -d`.

## Quellcode und Fehler

- **Quellcode:** Nodvard Dock entsteht aus [nodvard/deck](https://github.com/nodvard/deck), dem Kern von Nodvard Deck
  mit dem Modul `extensions/docker`.
- **Fehler und Wünsche** meldest du als Issue in [nodvard/dock](https://github.com/nodvard/dock/issues).
- **Mehr über Nodvard:** [nodvard.com](https://nodvard.com)

## Lizenz

Wie Nodvard Deck: **PolyForm Noncommercial License 1.0.0**, siehe [LICENSE](../../LICENSE). Kurz gesagt (verbindlich
ist nur der Lizenztext): Privat und für andere nicht-kommerzielle Zwecke ist Nodvard Dock frei nutzbar. Firmen und
bezahlte Dienste brauchen eine gesonderte Erlaubnis. Anfragen: **kontakt@nodvard.com**. Der Quellcode ist einsehbar,
Nodvard Dock ist aber keine Open-Source-Software im engeren Sinn.

Docker und Portainer sind Marken ihrer jeweiligen Inhaber. Nodvard Dock steht in keiner Verbindung zu ihnen.
