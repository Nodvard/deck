# Fehlerbehebung

[English](../en/dock/troubleshooting.md) · [Dock-Dokumentation](README.md) · [Dock-Schnellstart](../../deploy/nodvard-dock/README.md#loslegen)

## Dock startet nicht oder die Seite fehlt

Im Ordner der `compose.yml` prüfen:

```bash
docker compose ps
docker compose logs --tail=100 nodvard-dock
```

Beim ersten Start läuft die Datenbankmigration. Der Healthcheck hat dafür eine Startfrist von 300 Sekunden.
Bleibt Dock danach unerreichbar, das Protokoll auf Fehler prüfen.
Bei „port is already allocated“ eine `.env` neben `compose.yml` mit `NODVARD_DOCK_PORT=8090` anlegen,
`docker compose up -d` ausführen und `http://<Host-Adresse>:8090` öffnen.
Bei Namensauflösungsproblemen die auskommentierte `dns`-Option in der
[Compose-Datei](../../deploy/nodvard-dock/compose.yml) prüfen.

## Erster Login oder falscher Produktname

Den Einrichtungscode mit `docker compose logs nodvard-dock` ablesen und im Browser eingeben.
Es gibt kein Standardpasswort. Der Code gilt für das Anlegen des ersten Kontos; danach mit diesem Konto anmelden.
Steht in Titel oder Anmeldung noch „Nodvard Deck“, unter **Einstellungen → Darstellung** den gespeicherten Namen ändern.
Fehlende Server-Module sind kein Installationsfehler: [Dock enthält nur Docker](README.md#edition-und-umfang).

## Docker-Host bleibt leer oder SSH scheitert

- Unter **Einstellungen → Server & Zugänge** Adresse, SSH-Port, Zugang und Markierung `docker` prüfen.
  Auch für den Rechner mit Dock dessen LAN-Adresse verwenden; `localhost` meint den Dock-Container.
- **Verbindung prüfen** ausführen. Bei fehlenden Rechten den SSH-Zugang erneut einrichten; die Gruppe `docker`
  entspricht weitreichenden Host-Rechten. Details: [Root-Rechte](../14-ROOT-RECHTE.md).
- Auf dem Zielhost prüfen, ob Docker läuft und `docker compose version` funktioniert.
- Die Markierung `docker-bedienen` beschränkt den Host auf Ansehen, Logs und Start/Stopp/Neustart.
  Fehlende Stacks, Updates und Konsole sind in diesem Modus beabsichtigt.

## Git lädt nicht oder Einspielen fehlt

| Beobachtung | Prüfen und nächster Schritt |
|---|---|
| **Aus Git** fehlt | Stack zuerst übernehmen oder als neuen Stack anlegen; Host braucht volle Docker-Verwaltung |
| Repository nicht erreichbar | HTTPS-Klonadresse, Branch oder Tag, Compose-Pfad und bei privaten Repositorys Token und zugehörigen Git-Server prüfen |
| Git-Server im LAN gesperrt | **Git-Server im eigenen Netz erlauben** in den erweiterten Docker-Einstellungen einschalten; kein `localhost` verwenden |
| Vergleich meldet Probleme | Zusatzdateien im Compose-Ordner ablegen; Submodule, Symlinks und Git LFS werden nicht geholt; Größenlimits beachten |
| Dateien sind gleich, kein **Einspielen** | Es gibt nichts zu schreiben; einen noch nicht gestarteten Stack über **Anwenden** starten |
| Nur `.env` unterscheidet sich | Vorhandene Umgebungsdateien bleiben lokal; sie werden nicht aus Git ersetzt |
| Vorschlag wartet | Unter **Aktionen** den Status und die nötige Freigabe durch einen Administrator prüfen |
| Neuer Commit, aber keine Datei geändert | Der Commit kann andere Ordner betreffen; **Holen und ansehen** zeigt den Stack-Vergleich |

Nach einem unklaren Ergebnis zuerst **Aktionen** prüfen, bevor du erneut einspielst.
Details: [Stacks aus Git](stacks-from-git.md).

## Image-Update scheitert

Der Update-Plan nennt den Grund. Bei Registry-Problemen den Zugang und die Anmeldung des Docker-Hosts prüfen.
Ein unverändertes festes Versions-Tag führt nicht zu einer höheren Versionsnummer; dafür die Compose-Datei ändern.
Bei einem gestoppten Dienst oder zwischenzeitlich geänderter Compose-Datei zuerst den Zustand prüfen und den Plan neu laden.
Kein zweites Update für dasselbe Compose-Projekt starten, solange ein Lauf aktiv ist.
Ein Rückweg über Images repariert keine bereits veränderten Anwendungsdaten. Siehe [Image-Updates](image-updates.md).

## Wiederherstellung und fehlende Daten

Dock-Sicherung samt Sicherungspasswort oder Wiederherstellungsschlüssel bereithalten; für eine Sicherung mit
Einmal-Passwort gilt nur dieses Passwort. Über **Einstellungen → System → Wiederherstellen** oder bei der
Ersteinrichtung über **Sicherung einspielen** zurückholen. Danach Anmeldung, Host-Verbindungen und Stack-Liste prüfen.
Fehlen Anwendungsdaten, die Volumes oder Bind-Mounts auf dem Docker-Host prüfen: Sie sind kein Teil der Dock-Sicherung.
Volume-Archive aus Stack-Updates oder Git-Einspielen findest du unter **Stacks → Sicherungen** auf dem jeweiligen Host.
`docker compose down -v` löscht den Dock-Datenspeicher; es ist kein Reparaturschritt.

Für Fehlermeldungen Version, betroffenen Schritt und gekürztes Protokoll ohne Tokens, Passwörter oder Schlüssel angeben:
[Dock-Issues](https://github.com/nodvard/dock/issues). Sicherheitslücken über die
[Sicherheitsrichtlinie](../../deploy/nodvard-dock/SECURITY.md) melden.
