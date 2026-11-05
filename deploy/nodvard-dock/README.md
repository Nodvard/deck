<div align="center">

# Nodvard Dock

**Docker-Verwaltung für deine Server: auf Deutsch, über SSH, ohne Agent.**

Self-hosted, Quelltext einsehbar (PolyForm Noncommercial), kostenlos für private Nutzung

[English](README.en.md) · [Loslegen](#loslegen) · [Dokumentation](../../docs/dock/README.md) · [Umsteigen von Portainer](#umsteigen-von-portainer) · [Discord](https://discord.gg/gTTs7qMabt)

[![Lizenz: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/Lizenz-PolyForm%20Noncommercial%201.0.0-blue)](../../LICENSE)
![Status: Beta](https://img.shields.io/badge/Status-Beta-orange)

</div>

> **Sprache:** Die Oberfläche von Nodvard Dock gibt es derzeit nur auf Deutsch.
>
> **English:** This README is also available in English: see [README.en.md](README.en.md). The web interface is
> currently in German only.

---

## Was Dock ist: der Unterschied zu Deck in fünf Sätzen

Nodvard Dock ist die Docker-Ausgabe von [Nodvard Deck](../../README.md), gebaut mit `EDITION=dock` und `NODVARD_DECK_EXTENSIONS=docker`.
Dock bietet nur Docker-Verwaltung für Container, Images, Volumes, Netze und Compose-Stacks.
Es verbindet Docker-Hosts über SSH, ohne Agent auf den Hosts und ohne Docker-Socket im Dock-Container.
Der gemeinsame Kern stellt Konten, Tresor, Freigaben, Protokoll und die Sicherung der Dock-Einstellungen bereit.
Server-Module wie Proxmox, Server-Zustand und Nodvard Shield gehören zu Deck und sind in Dock nicht enthalten.

Die [Editionsübersicht mit Codebelegen](../../docs/dock/README.md#edition-und-umfang) erklärt auch die feste Modulauswahl und den Update-Weg.

> **Stand:** Beta. Schnittstellen und Datenformate können sich noch ändern. Das fertige Image `ghcr.io/nodvard/dock`
> gibt es für normale PCs und NAS (amd64) und für den Raspberry Pi (arm64).

## Was es kann

- **Alle Container auf einer Seite:** Zustand, Starten, Stoppen, Neustarten, Konsole und Details. Logs mit Zeitraum,
  Suche und Download. Gestoppte Container lassen sich löschen.
- **Image-Updates mit Rückweg:** Nodvard Dock findet neue Images und spielt sie bei Compose-Diensten ein. Das alte
  Image bleibt als Sicherung. Stürzt der neue Container beim Start ab, versucht Dock automatisch zum alten
  zurückzukehren, außer bei erkannten Datenbanken und anderen Datenspeichern (abschaltbar in den Einstellungen des Moduls).
- **Stacks bleiben Ordner auf dem Server:** Vorhandene Stacks übernimmst du mit einem Klick, am Server ändert sich
  dabei nichts. Danach lassen sie sich anwenden, stoppen, herunterfahren (die Volumes bleiben) und ihre Images holen.
- **Compose-Editor mit Prüfung und Verlauf:** Bearbeiten als Text oder im Formular, das nur die geänderte Stelle
  anfasst. Vor dem Speichern prüft Docker Compose die Datei auf dem Server, die letzten Stände bleiben im Verlauf.
- **Neue Stacks aus Vorlagen:** Uptime Kuma, Vaultwarden, Nginx Proxy Manager und Gitea, oder eine eigene Datei. Eine
  Compose-Datei lässt sich auch als eigene Vorlage speichern, nur die Datei selbst, ohne `.env` und ohne feste
  Passwörter. Jeder neue Stack bekommt einen eigenen Ordner (Vorgabe `/opt/stacks`). Löschen geht mit Herunterfahren;
  den Ordner löscht Nodvard Dock nur, wenn du es anhakst.
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

### Schnellstart in 5 Minuten

**Voraussetzung:** ein Rechner mit Docker und Docker Compose, etwa ein Raspberry Pi 4/5 mit 64-Bit-System oder ein
x86-64-Rechner, ein per SSH erreichbarer Linux-Docker-Host und ein vorbereitetes HTTPS-Git-Repository mit Compose-Datei.
Die fünf Minuten setzen voraus, dass Docker, SSH und das Repository schon bereitstehen; der Image-Download kann länger dauern.
Die Befehle gelten für eine Linux-Shell; in PowerShell `curl.exe` verwenden und die Befehle einzeln ausführen.

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

### 2. Einrichten und erster Login

Im Browser den Einrichtungscode aus dem Container-Protokoll eingeben. Es gibt kein Standardpasswort.
Die Einrichtung führt dich durch Konto, Zeitzone und weitere Einstellungen:

1. **Administrator-Konto anlegen**: Benutzername und eigenes Passwort festlegen. Danach bist du angemeldet.
2. **Zeitzone**: Zeitzone wählen. Anschließend schaltet Dock das Modul **Docker** automatisch ein.
3. **Zwei-Faktor-Anmeldung (optional)**: freiwillig, aber sehr empfohlen.
4. **Aussehen (optional)**: Produktname, Kurzname und Akzentfarbe anpassen oder überspringen.
5. **Fertig**: Mit **Weiter zum Dashboard** zu Dock wechseln.

Scheitert das Einschalten des Docker-Moduls, erscheint ein zusätzlicher Modulschritt zum erneuten Versuch.
Bei späteren Anmeldungen das angelegte Konto verwenden.

### 3. Server verbinden, auch den eigenen

Nodvard Dock nutzt keinen Docker-Socket. Darum verbindet es sich mit jedem Server über SSH, auch mit dem Rechner, auf
dem es selbst läuft.

1. Unter **Einstellungen → Server & Zugänge** den Server anlegen, mit der Markierung `docker`. Für den eigenen Rechner
   seine Adresse im Heimnetz eintragen, nicht `localhost` (das wäre der Container selbst).
2. **Mit Passwort einrichten:** Du gibst einmal ein Konto mit root- oder sudo-Rechten an. Nodvard Dock legt damit
   einen eigenen Benutzer mit SSH-Schlüssel an, trägt ihn in die Gruppe `docker` ein und vergisst das Passwort.
3. Die Seite **Docker** zeigt jetzt die Container dieses Servers.

### 4. Den ersten Stack aus Git einspielen

1. Auf **Docker → Stacks → Neuer Stack** einen Namen wie `meine-app` wählen und die Compose-Datei aus deinem
   Repository einfügen. Bei **Ordner für neue Stacks** zuerst **Ändern** → **Im Heimatordner** → **Übernehmen** wählen.
   So liegt der Stack im beschreibbaren Heimatordner des SSH-Benutzers statt unter `/opt/stacks`.
   Danach **Prüfen**, dann **Anlegen** wählen und die Freigabe bestätigen. Der Stack wird dabei noch
   nicht gestartet. Für diesen ersten Versuch eine Compose-Datei ohne benötigte Zusatzdateien verwenden.
2. Beim Stack **Aus Git** öffnen. HTTPS-Adresse des Repositorys, **Branch oder Tag** (etwa `main`) und den Pfad
   zur Compose-Datei (etwa `compose.yaml`) eintragen, dann **Eintragen und holen** wählen.
3. Den Dateivergleich prüfen. Sind alle Dateien gleich, den Stack über **Anwenden** starten; sonst **Einspielen**
   wählen. Die Freigabe bestätigen und unter **Aktionen** das Ergebnis prüfen; der Stack sollte danach unter
   **Stacks** mit seinen Containern erscheinen.

Für private Repositorys zuerst einen Token unter **Einstellungen → Erweiterungen → Docker → Erweitert → Git-Zugänge**
hinterlegen. Details und Grenzen: [Stacks aus Git](../../docs/dock/stacks-from-git.md).

**Eigener Datenspeicher:** Nodvard Dock hat sein eigenes Volume `nodvard-dock-data`. Den Datenspeicher eines
Nodvard Deck kann es nicht übernehmen, weil darin Daten von Modulen liegen, die Dock nicht kennt.

> **Bekannt in dieser Beta:** Steht in Titel oder Anmeldung noch „Nodvard Deck“, ist der Name aus einer älteren Beta
> gespeichert: unter Einstellungen → Darstellung ändern.

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

## Unterschied zu Deck

| Eigenschaft | Nodvard Dock | Nodvard Deck |
|---|---|---|
| Zweck | Docker- und Compose-Verwaltung | Dashboard für weitere Bereiche des Homelabs |
| Module im offiziellen Image | Nur `docker` | Weitere Module, etwa Proxmox, Backups, Server-Zustand und Shield |
| Ausgabe | `EDITION=dock` | `EDITION=deck` |
| Image | `ghcr.io/nodvard/dock` | `ghcr.io/nodvard/deck` |
| Datenspeicher der mitgelieferten Compose-Datei | `nodvard-dock-data` | `nodvard-deck-data` |
| Einrichtung | Docker automatisch aktivieren | Gewünschte Module auswählen |

Für weitere Module kannst du [Nodvard Deck](https://github.com/nodvard/deck) einsetzen.
Nutze für beide Installationen ihre eigenen Datenspeicher. Ein gemeinsames Volume oder eine automatische
Datenübernahme beim Wechsel wird hier nicht vorausgesetzt.

## Sicherheit und Datenschutz auf einen Blick

- **Keine Telemetrie als Voreinstellung:** Nodvard Dock schickt standardmäßig keine Nutzungs- oder Absturzdaten.
- **Standardmäßig nach außen** geht nur die Update-Suche: Einmal am Tag fragt Nodvard Dock bei `ghcr.io` nach einer neuen
  Version, die Registry sieht dabei deine IP-Adresse und den Zeitpunkt (abschaltbar unter Einstellungen → System →
  Updates). Weitere Verbindungen gehen zu den eingerichteten Servern per SSH, Registries und Git-Repositorys.
  Schaltest du die Suche nach neuen Images ein (Standard aus), fragen deine Docker-Server bei den Registries ihrer
  Container nach. Die optionale Git-Prüfung nach Zeitplan ist ebenfalls standardmäßig aus; sie meldet neue Commits,
  spielt sie aber nicht ein. Ohne Zeitplan fragt Dock das Git-Repository auf deinen Klick ab.
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
  [Anleitung zu den Root-Rechten](../../docs/14-ROOT-RECHTE.md#docker-auf-nur-bedienen-umstellen).
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

## Dock aktualisieren

Vor einem Update eine Dock-Sicherung herunterladen und die Versionshinweise lesen. Im Ordner der `compose.yml`:

```bash
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail=100 nodvard-dock
```

Kurzform: `docker compose pull && docker compose up -d`. Der Datenspeicher `nodvard-dock-data` bleibt erhalten.
Einstellungen → System → Updates zeigt verfügbare Dock-Versionen. Dock verwendet keinen Deck-Update-Helfer und
kein `scripts/deploy_pi.sh`. Ein Container-Image allein setzt eine Datenbankmigration nicht zurück; für einen
Rückweg die Sicherung und die dazu passende Image-Version aufbewahren.
Updates deiner Anwendungen sind separat beschrieben: [Image-Updates](../../docs/dock/image-updates.md).

## Sicherung und Wiederherstellung

- **Nodvard Dock selbst sichern** geht unter **Einstellungen → System → Sicherung**: verschlüsselt, auf Wunsch nach
  Zeitplan und zum Herunterladen. Darin stecken Konten, Einstellungen, der Tresor mit den SSH-Schlüsseln und die
  Merkliste der übernommenen Stacks. Für einen Ordner außerhalb des Datenspeichers (anderes Laufwerk, NAS) die Zeile
  `- ./sicherungen:/backups` in der `compose.yml` einkommentieren; was dazu nötig ist, steht dort.
- **Zurückholen** geht unter **Einstellungen → System → Wiederherstellen** oder bei einer frischen Installation im
  ersten Einrichtungsschritt mit **Sicherung einspielen**.
- **Wiederherstellung prüfen:** Sicherungsdatei und zugehöriges Sicherungspasswort oder den Wiederherstellungsschlüssel
  außerhalb des Dock-Volumes aufbewahren; für einen Download mit Einmal-Passwort brauchst du genau dieses Passwort.
  Nach dem Einspielen mit dem wiederhergestellten Konto anmelden, Host-Verbindungen und Stack-Liste
  prüfen. Die Dock-Sicherung enthält keine Anwendungsdaten von den Docker-Hosts; deren Volumes und Bind-Mounts
  separat sichern. Details zur verschlüsselten Sicherung: [Sicherungsanleitung](../../docs/16-SICHERUNG.md).
- **Die Volume-Sicherungen vor einem Stack-Update** liegen auf dem jeweiligen Server, im Heimatordner des Benutzers
  von Nodvard Dock (Vorgabe `nodvard`) unter `nodvard-sicherungen/<Stack>/<Zeit>/`, je Volume ein `.tar.gz`. Außer
  root kann sie nur dieser Benutzer lesen. Nodvard Dock löscht sie nicht von selbst; plane den Platz dafür ein.
- **Entfernen:** `docker compose down` im Ordner der `compose.yml` hält Nodvard Dock an. Die Daten bleiben im Volume
  `nodvard-dock-data`. Erst `docker compose down -v` löscht auch die Daten samt aller gespeicherten Schlüssel, also
  vorher sichern. Deine Container, Stacks und Volumes auf den Servern bleiben in beiden Fällen, wie sie sind. Den
  Benutzer, den Nodvard Dock auf den Servern angelegt hat, entfernst du dort bei Bedarf selbst.

## Grenzen

- Beta, Oberfläche derzeit nur auf Deutsch; keine Server-Module und keine freie Modulauswahl in Dock.
- Jeder Docker-Host braucht SSH und Docker Compose für Stacks. Die Markierung `docker-bedienen` erlaubt nur
  Ansehen, Logs, Starten, Stoppen und Neustarten, keine Stacks, Image-Updates oder Konsole.
- Git unterstützt HTTPS mit Branch oder Tag, keine SSH-Git-Adressen, Submodule oder Git-LFS-Dateien.
  Die optionale Prüfung meldet neue Commits; das Einspielen braucht deine Aktion und Freigabe.
- Ein Image-Rollback oder Git-Rückweg stellt keine Anwendungsdaten wieder her. Automatische Image-Rückkehr ist
  bei erkannten Datenbanken und Datenspeichern ausgeschlossen und ersetzt keine Sicherung.
- Die Gruppe `docker` gibt weitreichende Rechte auf dem Host. Die Dock-Sicherung und Sicherungen der Anwendungen
  sind getrennte Aufgaben.
- Die Image-Prüfung vergleicht den Digest unter demselben Tag und sucht keine höheren Versions-Tags. Der automatische
  Rückweg gilt nicht bei bloßem `unhealthy` und nicht für erkannte Datenbanken oder Datenspeicher wie Redis oder MinIO.
- Bind-Mounts, also Daten in Host-Ordnern, sichert die Volume-Sicherung vor einem Stack-Update nicht mit.
- Logs laden höchstens 5000 Zeilen, die Suche durchsucht nur diese. Der Verlauf des Compose-Editors hält je Stack
  bis zu 20 alte Fassungen.

Weiterlesen: [Stacks aus Git](../../docs/dock/stacks-from-git.md), [Image-Updates](../../docs/dock/image-updates.md),
[Vorlagen](../../docs/dock/templates.md), [Fehlerbehebung](../../docs/dock/troubleshooting.md).

## Selbst bauen

In einem Klon von [nodvard/deck](https://github.com/nodvard/deck), am besten auf einem PC (auf einem Raspberry Pi
dauert der Bau sehr lange):

```bash
docker build -f deploy/Dockerfile --build-arg NODVARD_DECK_EXTENSIONS=docker --build-arg EDITION=dock -t nodvard-dock:selbst .
```

`EDITION=dock` bestimmt die Ausgabe, `NODVARD_DECK_EXTENSIONS=docker` die Modulauswahl.
Dann in der `compose.yml` die Zeile `image:` auf `nodvard-dock:selbst` ändern und `docker compose up -d`.

## Quellcode und Fehler

- **Quellcode:** Nodvard Dock entsteht aus [nodvard/deck](https://github.com/nodvard/deck), dem Kern von Nodvard Deck
  mit dem Modul `extensions/docker`.
- **Fehler und Wünsche** meldest du als Issue in [nodvard/dock](https://github.com/nodvard/dock/issues).
- **Mehr über Nodvard:** [nodvard.com](https://nodvard.com)

## Mitmachen

Fehler, Wünsche und unklare Dokumentation bitte über die Vorlagen als [Issue](https://github.com/nodvard/dock/issues)
melden. Gib die Version, deine Installation und die Schritte zum Nachstellen an.
Externe Pull Requests oder Patches werden nicht angenommen. Beschreibe Vorschläge im Issue in Worten.
Eigene Anpassungen und Builds sind unter den Bedingungen der PolyForm Noncommercial License erlaubt.
Die Regeln stehen in [CONTRIBUTING.md](../../CONTRIBUTING.md).

## Support

Fragen zur Einrichtung und Austausch mit anderen Nutzern gibt es im Hilfe-Forum auf
[Discord](https://discord.gg/gTTs7qMabt). Mehr über Nodvard: [nodvard.com](https://nodvard.com).

## Lizenz

Wie Nodvard Deck: **PolyForm Noncommercial License 1.0.0**, siehe [LICENSE](../../LICENSE). Kurz gesagt (verbindlich
ist nur der Lizenztext): Privat und für andere nicht-kommerzielle Zwecke ist Nodvard Dock frei nutzbar. Firmen und
bezahlte Dienste brauchen eine gesonderte Erlaubnis. Anfragen: **kontakt@nodvard.com**. Der Quellcode ist einsehbar.

Docker und Portainer sind Marken ihrer jeweiligen Inhaber. Nodvard Dock steht in keiner Verbindung zu ihnen.
