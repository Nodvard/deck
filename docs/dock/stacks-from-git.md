# Stacks aus Git

[English](../en/dock/stacks-from-git.md) · [Dock-Dokumentation](README.md) · [Schnellstart](../../deploy/nodvard-dock/README.md#loslegen)

## Voraussetzungen und erster Stack

Der Host ist per SSH verbunden und mit `docker` markiert, Docker Compose ist installiert.
Git-Quellen lassen sich nur für übernommene Stacks eintragen. Einen vorhandenen Stack unter **Docker → Stacks → Übernehmen**
prüfen und mit **Jetzt übernehmen** registrieren; dabei werden keine Container verändert.

Für den ersten Versuch ein Repository mit einer einzelnen Compose-Datei ohne benötigte Zusatzdateien verwenden:

1. **Neuer Stack** öffnen, einen freien Namen wählen und den Inhalt der Compose-Datei aus dem Repository einfügen.
2. Den Zielordner prüfen, möglichst unter dem Heimatordner eines normalen Benutzers. **Prüfen** und **Anlegen**
   wählen, dann die Freigabe bestätigen. Das registriert den Stack, startet ihn aber noch nicht.
3. Beim Stack **Aus Git** öffnen und die Quelle wie unten eintragen.
4. Sind alle Dateien bereits gleich, gibt es nichts einzuspielen. Den Stack über **Anwenden** und die Freigabe starten.
   Andernfalls den Vergleich prüfen und **Einspielen** freigeben; dabei wird Compose angewendet.

Für komplexere Stacks müssen lokale Umgebungsdateien und benötigte Zusatzdateien vor der Compose-Prüfung auf dem Host
bereitliegen. Einen solchen Stack zuerst auf dem Host einrichten und dann übernehmen. Geheimnisse gehören nicht ins Repository.

## Quelle eintragen

- **Adresse des Repositorys:** die HTTPS-Klonadresse, etwa `https://git.example.org/team/apps.git`, ohne Token,
  Benutzername, Abfrage oder Anker. Git per SSH wird nicht unterstützt.
- **Branch oder Tag:** etwa `main` oder ein veröffentlichter Tag.
- **Pfad zur Compose-Datei:** relativ zum Repository, etwa `apps/web/compose.yaml`.
  Dock gleicht den Ordner dieser Datei mit dem Stack-Ordner ab; die Compose-Datei wird der lokalen Compose-Datei zugeordnet.
- **Zugang:** für öffentliche Repositorys **Öffentlich, ohne Anmeldung**. Für private Repositorys unter
  **Einstellungen → Erweiterungen → Docker → Erweitert → Git-Zugänge** einen Token für den passenden Git-Server hinterlegen
  und diesen Zugang wählen. Der Token liegt im Tresor.

**Eintragen und holen** speichert die Quelle und lädt den Vergleich. Später gibt es **Speichern und holen** oder
**Holen und ansehen**. Dock holt das Repository selbst über HTTPS; auf dem Docker-Host braucht es dafür kein Git.
Holen und Vergleichen schreiben keine Dateien auf den Host und führen kein Programm aus dem Repository aus.

## Prüfen und einspielen

Der Vergleich zeigt Commit, neue und geänderte Dateien sowie Probleme und Warnungen.
Vorhandene `.env` und die dem Stack bekannten Umgebungsdateien werden nie ersetzt.
Wenn du **Volumes vorher sichern** wählst, zeigt Dock Volumes und freien Platz an. Betroffene Dienste stehen für die
Sicherung still, bis Compose den Stack anwendet. Archive lassen sich unter **Stacks → Sicherungen** zurückspielen.

**Einspielen** benötigt Bestätigung und Freigabe. Fehlt die Berechtigung zur Freigabe, wartet der Vorschlag unter
**Aktionen** auf einen Administrator. Compose erstellt Dienste mit geänderter Konfiguration neu; zusätzliche Neustarts
für geänderte Dateien nennt die Freigabe. Bleiben weitere Dienste mit altem Dateistand übrig, erscheint **Jetzt neu starten**.
Früher aus Git geschriebene, inzwischen entfernte Dateien löscht Dock nur, wenn sie auf dem Host unverändert sind.

**Zurück zum Stand davor** bietet einen Schritt zurück für die zuletzt geschriebenen Dateien.
Es stellt keine Anwendungsdaten oder Volumes wieder her; seitdem lokal geänderte Dateien bleiben erhalten.
**Repository vergessen** entfernt Quelle und geholten Stand in Dock, lässt aber Stack und Dateien auf dem Host bestehen.

## Neue Commits und Grenzen

**Auf neuen Stand prüfen** fragt den Commit von Branch oder Tag ab. Optional gibt es unter den erweiterten
Docker-Einstellungen eine Git-Prüfung nach Zeitplan (`git_watch_enabled`, Standard aus).
Sie meldet neue Commits, holt und deployt sie aber nicht automatisch. Ein neuer Commit muss den Stack-Ordner nicht ändern.

Der lokale Stack muss aus genau einer Compose-Datei direkt im Stack-Ordner bestehen.
Nur HTTPS, keine Submodule, Symlinks oder Git-LFS-Dateien im abgeglichenen Ordner.
Verweise auf Compose-, Umgebungs- oder Build-Dateien außerhalb dieses Ordners können das Einspielen verhindern.
Private Netzadressen brauchen **Git-Server im eigenen Netz erlauben**; Loopback-Adressen bleiben gesperrt.
Größen- und Zeitlimits begrenzen den Abruf, unter anderem 300 Dateien, 10 MiB Dateiinhalte und 256 KiB für die Compose-Datei.
Dock ersetzt keine CI-Pipeline und keinen Datensicherungsplan.

Codebelege: [Quelle und Vergleich](../../extensions/docker/src/nodvard_deck_ext_docker/git_source.py),
[Abruf und Grenzen](../../extensions/docker/src/nodvard_deck_ext_docker/git_fetch.py),
[Einspielen und Rückweg](../../extensions/docker/src/nodvard_deck_ext_docker/git_apply.py),
[Prüfung nach Zeitplan](../../extensions/docker/src/nodvard_deck_ext_docker/git_watch.py).
