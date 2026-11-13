# Nodvard Dock 0.13.0 (Beta): Netze, Image holen, eigene Vorlagen und saubere Dock-Edition

*English version: [below](#english).*

## Deutsch

**Nodvard Dock** verwaltet die Container auf deinen Servern in einer Oberfläche. Es ist Nodvard Deck mit nur dem Modul Docker: derselbe Code, nur weniger drin.

### Das Wichtigste

- **Netze verwalten:** Im Reiter Netze neue Docker-Netze anlegen, Container verbinden und trennen.
- **Images herunterladen:** Im Reiter Images neue Images per „Image holen“ direkt herunterladen.
- **Eigene Vorlagen für Stacks:** Compose-Dateien unter „Neuer Stack“ oder aus vorhandenen Stacks als Vorlage speichern.
- **Git-Prüfung nach Zeitplan:** Stacks aus Git prüfen automatisch nach Zeitplan auf neue Stände im Repository.
- **Container sortieren:** Container per Klick nach Zustand, Name, Status, Laufzeit, CPU oder RAM sortieren.
- **Docker am Handy:** Kartenansicht für Stacks, Images, Volumes und Netze auf mobilen Geräten.
- **Saubere Dock-Edition:** Nodvard Dock zeigt überall den eigenen Namen, öffnet direkt die Docker-Verwaltung, sperrt nicht verfügbare Funktionen und bietet passende Updates und Anleitungen.
- **Mehr Sicherheit:** Striktere Git-Prüfung vor dem Holen, Schutz vor Cache-Speicherung von Anmeldedaten und Warnung bei unverschlüsseltem Zugriff.

### Was du tun musst

1. **Aktualisieren** im Ordner mit der Compose-Datei: `docker compose pull && docker compose up -d`.
2. **Git-Server im eigenen Netz erlauben:** Liegt dein Git-Repository im eigenen Heimnetz (etwa Gitea auf dem Heimserver), schalte in den Einstellungen des Moduls Docker unter „Erweitert“ den Schalter „Git-Server im eigenen Netz erlauben“ ein. Ohne ihn sperrt Dock das Holen aus Sicherheitsgründen.

### Neu

**Netze anlegen und verwalten**
- Im Reiter Netze legst du ein neues Netz an, auf Wunsch nur intern ohne Zugang nach außen, und verbindest Container damit oder trennst sie davon. Alles läuft über die Freigabe.
- Die Rückfrage warnt, wenn ein Container danach ohne Netz wäre oder zu einem Stack gehört, dessen Neuerstellen die Änderung zurücknimmt. Die Netze von Nodvard Dock selbst ändert das Modul nie.

**Images herunterladen**
- Im Reiter Images gibt es „Image holen“. Name und Tag eingeben, Docker lädt das Image über die Freigabe herunter, private Images mit der Anmeldung unter Registries. Scheitert es, steht der Grund da, etwa Abrufgrenze von Docker Hub, falsch geschriebener Name oder fehlender Platz.

**Eigene Vorlagen für neue Stacks**
- Unter „Neuer Stack“ oder in der Datei eines Stacks speicherst du die Compose-Datei als Vorlage. Sie steht dann neben dem Katalog bereit und lässt sich ersetzen oder löschen.
- Eine Vorlage enthält nur die Compose-Datei, nie die Umgebungswerte des Stacks. Feste Passwörter, Tokens und Schlüssel werden beim Speichern abgelehnt, soweit sie sich erkennen lassen, mit Zeile und Grund.

**Stacks aus Git: Prüfung nach Zeitplan**
- Auf Wunsch prüft Docker nach Zeitplan, ob es im Repository einen neuen Stand gibt, und schickt dann eine Nachricht. Scheitert die Prüfung dreimal hintereinander, kommt eine Warnung. Eingespielt wird weiter nur per Klick mit Freigabe.
- Im Bereich „Aus Git“ steht, ob es einen neuen Stand gibt, und der Knopf „Auf neuen Stand prüfen“ prüft das sofort.

### Sicherheit

**Stacks aus Git**
- Vor dem Holen prüft Dock, wohin der Name des Git-Servers wirklich zeigt, und verbindet sich nur mit genau dieser Adresse. Dock selbst und die internen Dienste von Cloud-Anbietern sind immer gesperrt.
- Läuft Nodvard Dock über einen Proxy (HTTPS_PROXY), holt es aus Git nur noch von Adressen aus Zahlen, weil der Proxy einen Namen selbst nachschlägt. Einen Git-Server mit Namen trägst du unter NO_PROXY ein, dann verbindet sich Dock direkt.

**Anmeldung und Sitzung**
- Antworten mit Anmeldedaten und Kontoinformationen verbieten jetzt die Speicherung im Browser-Cache.
- Bei unverschlüsseltem Zugriff im Heimnetz warnen die Einrichtung und die Systemeinstellung davor, dass dein Anmelde-Token mitgelesen werden kann, und zeigen Wege zu einer geschützten Verbindung. Die Warnung erscheint auch bei lokalen Proxys ohne Vertrauenseintrag oder mit unlesbarer Geräteadresse.

**Hilfsskripte und Oberfläche**
- sudo startet die Hilfsskripte von Nodvard Deck jetzt nur noch in genau der Fassung, die beim Einrichten auf den Server kam (ab dem nächsten Einrichten, nicht mit sudo-rs, dem Standard ab Ubuntu 25.10).
- Die Oberfläche sperrt fremde Quellen und nicht freigegebene Skripte und Stile jetzt mit einer strengeren Sicherheitsrichtlinie. Konsole, Erweiterungen und Dateivorschauen bleiben nutzbar.

### Verbessert

**Container sortieren**
- Die Docker-Container lassen sich per Klick auf Container, Zustand, Status/Laufzeit, CPU oder RAM sortieren. Ein zweiter Klick kehrt die Reihenfolge um, ein dritter stellt die Standardreihenfolge wieder her. Die Laufzeit richtet sich nach der tatsächlichen Startzeit.

**Dock-Edition und Einrichtung**
- Nodvard Dock zeigt seinen Namen auch auf der Über-Seite und erhält ein passendes Änderungsprotokoll sowie eigene Release-Notes.
- Der Einrichtungs-Assistent schaltet das Modul Docker selbst ein, statt danach zu fragen.
- Git-Server im eigenen Netz, etwa Gitea auf dem Heimserver, gehen erst, wenn du in den Einstellungen des Moduls Docker unter „Erweitert“ den Schalter „Git-Server im eigenen Netz erlauben“ einschaltest. Ohne ihn sagt Dock beim Holen genau das.

**Anleitung und Dokumentation**
- Nodvard Dock hat jetzt eine deutsche und englische Anleitung vom ersten Start bis zur Fehlerbehebung, die Einrichtung, Funktionen, Grenzen und Lizenz genauer beschreibt.

### Behoben

**Nodvard Dock und Benutzeroberfläche**
- Nodvard Dock öffnet direkt die Docker-Verwaltung und zeigt nur die passenden Seiten, Einstellungen und Hilfetexte.
- Nodvard Dock sperrt nicht verfügbare Funktionen auch bei direkten Aufrufen und lässt seine fest eingebauten Module nicht mehr ausschalten.
- Die Container-Konsole von Nodvard Dock zeigt keine allgemeine Serverauswahl mehr.
- Nodvard Dock zeigt in Titel, Anmeldung und Einrichtung seinen eigenen Namen, und die Befehle für Einrichtungscode und Notfall nennen den richtigen Dienst.
- Die Seite Einstellungen → System → Updates sucht nach dem eigenen Image (ghcr.io/nodvard/dock) und zeigt nur die passenden Anleitungen.
- Nodvard Dock zeigt keinen Schalter zum selbstständigen Einspielen von Sicherheitsupdates mehr.
- Module mit neuem Namen wie Docker finden ihre gespeicherten Dateien auch dann, wenn unter dem neuen Namen nur ein leerer Ordner liegt.
- Ein geändertes Passwort oder Token gilt sofort in allen Modulen, ohne Neustart.

**Docker am Handy**
- Die Reiter Stacks, Images, Volumes und Netze zeigen am Handy jeden Eintrag als eigene Karte mit den Knöpfen darunter. Vorher liefen Angaben und Knöpfe ineinander oder standen schief.
- Unter „Docker-Speicher“ ist die Liste der Images am Handy nicht mehr rechts abgeschnitten; Größe, Alter und Nutzung stehen beschriftet in der Karte.
- Am Handy verdeckt die Leiste des Browsers nicht mehr das Ende einer Seite, man kann bis ganz nach unten blättern.

**Netze und Aktionen**
- Beim Verbinden und Trennen von Containern zeigt die Docker-Seite das Ergebnis samt Fehlergrund auch nach dem Zuklappen des Containerbereichs.
- Fehler beim automatischen Anlegen eines Netzes erscheinen mit ihrem Grund im Formular.
- Die Docker-Seite behält Ergebnisse, Fehler und Ablehnungsgründe von Ressourcenaktionen beim Neuladen und bei Ladefehlern sichtbar.
- Die Docker-Seite zeigt nach fremder Freigabe auch das Ergebnis von Löschaktionen samt Fehler oder Ablehnungsgrund. Ergebnisse gleichzeitig abgeschlossener Ressourcenaktionen bleiben gemeinsam sichtbar.
- Beim Laden einer eigenen Stack-Vorlage bleiben Änderungen an Name und Compose-Datei erhalten, die während des Ladens eingegeben wurden.

### Gut zu wissen

- **Aktualisieren:** Im Ordner mit der Compose-Datei wie gewohnt aktualisieren: `docker compose pull && docker compose up -d`.
- **Eigener Name und passende Oberfläche:** Titel, Anmeldung, Einrichtung und die Über-Seite heißen jetzt Nodvard Dock. Die Seite Einstellungen → System → Updates sucht nach dem offiziellen Dock-Image (`ghcr.io/nodvard/dock`) und zeigt nur die passenden Anleitungen für Dock.
- **Keine Modul-Auswahl nötig:** Der Einrichtungs-Assistent aktiviert das Modul Docker automatisch, da andere Module in Nodvard Dock nicht enthalten sind.
- **Fest eingebaute Module:** Das Modul Docker kann in Nodvard Dock nicht ausgeschaltet werden, und Deck-eigene Seiten und Schnittstellen sind gesperrt.

### Installieren in zwei Schritten

Voraussetzung: ein Rechner mit Docker und Docker Compose (amd64 oder arm64, etwa ein Raspberry Pi 4/5 mit 64-Bit-System).

1. In einem leeren Ordner die Compose-Datei laden:
   ```bash
   curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/dock/main/compose.yml
   ```
2. Starten und im Browser `http://<dein-server>:8080` öffnen:
   ```bash
   docker compose up -d
   ```

Den Einrichtungscode für den ersten Schritt zeigt `docker compose logs nodvard-dock`.

### Grenzen

- **Beta:** Die Oberfläche gibt es bisher nur auf Deutsch.
- **Stacks aus Git:** nur `https://`, eine Compose-Datei je Stack, kein automatisches Einspielen und kein Webhook (die Prüfung auf neue Stände läuft auf Wunsch nach Zeitplan, das Einspielen bleibt manuell mit Freigabe).
- **Rechte beim Einspielen aus Git:** Meldet sich Dock nicht als Besitzer des Stack-Ordners an, braucht es root oder `sudo -n` für `setpriv` (das kommt root-Rechten gleich), sonst schreibt es nichts.
- **Stack-Ordner von root:** Dann schreibt Dock mit root-Rechten, und ein Verweis im Ordner kann auf den ganzen Rechner zeigen, wovor nur die Prüfung direkt vor jeder Datei schützt. Empfehlung: Stacks in Ordnern eines normalen Benutzers.
- **Docker auf einzelnen Servern:** Stacks sind Docker-Compose-Projekte, Swarm und Kubernetes unterstützt Dock nicht.
- **Anbindung über SSH:** Auch der eigene Rechner wird über SSH angebunden, einen Docker-Socket bekommt Dock nicht.
- **Rechte auf dem Server:** Dock braucht dort einen Benutzer in der Gruppe `docker`, das heißt praktisch root. Wo dir das zu viel ist, gibt es den Modus „Nur bedienen“ mit weniger Funktionen.
- **Wer Dock bedienen darf, kann deine Server steuern.** Darum: starkes Passwort, Zwei-Faktor an und Dock nicht offen ins Internet stellen.

### Lizenz

PolyForm Noncommercial License 1.0.0: Quelltext einsehbar, kostenlos für den privaten Gebrauch und andere nicht-kommerzielle Zwecke. Firmen und bezahlte Dienste brauchen eine gesonderte Erlaubnis: kontakt@nodvard.com.

---

## English

**Nodvard Dock** manages the containers on your servers in one user interface. It is Nodvard Deck with only the Docker module: the same code, just less of it. The web interface is in German only for now. Buttons, pages and settings are quoted in German as they appear, with a translation in brackets.

### The essentials

- **Manage networks:** create Docker networks, connect and disconnect containers in the Netze (networks) tab.
- **Download images:** download images directly using “Image holen” (pull image) in the Images tab.
- **Custom stack templates:** save Compose files as templates under “Neuer Stack” (new stack) or from existing stacks.
- **Scheduled Git checks:** stacks from Git can automatically check the repository for new updates on a schedule.
- **Sort containers:** click to sort containers by state, name, status, uptime, CPU or RAM.
- **Docker on mobile:** card views for stacks, images, volumes and networks on mobile devices.
- **Clean Dock edition:** Nodvard Dock displays its own name everywhere, opens Docker management directly, blocks unavailable routes and offers matching update guides.
- **Stronger security:** stricter Git server verification before fetching, blocked browser caching for credentials and warnings for unencrypted access.

### What you need to do

1. **Update** in the folder with the Compose file: `docker compose pull && docker compose up -d`.
2. **Allow Git servers on your local network:** if your Git repository is hosted on your local network (for example Gitea on a home server), enable “Git-Server im eigenen Netz erlauben” (allow Git servers on local network) under “Erweitert” (advanced) in the Docker module settings. Without this setting, Dock blocks access to local network addresses for security reasons.

### Added

**Docker: networks**
- Create a new network in the Netze (networks) tab, optionally internal without external access, and connect or disconnect containers. Everything requires approval.
- A confirmation dialog warns if a container would be left without a network or belongs to a stack whose rebuild would undo the change. Dock never modifies its own networks.

**Docker: download images**
- The Images tab now offers “Image holen” (pull image). Enter the name and tag, and Docker downloads the image through an approval, using credentials under Registries for private images. If pulling fails, clear error reasons are shown (such as Docker Hub rate limits, typos or insufficient disk space).

**Docker: custom stack templates**
- Save Compose files as templates under “Neuer Stack” (new stack) or from a stack's file view. They appear alongside the catalog and can be replaced or deleted.
- Templates only contain the Compose file, never stack environment values. Hardcoded passwords, tokens and keys are rejected upon saving when detected, indicating line and reason.

**Docker: stacks from Git**
- Stacks can check for repository updates on a schedule and send notifications. If the check fails three times in a row, a warning is raised. Deploying remains manual with approval.
- The “Aus Git” (from Git) section shows if an update is available, and the “Auf neuen Stand prüfen” (check for updates) button checks immediately.

### Changed

**Sort containers**
- Docker containers can be sorted by clicking Container, Zustand (state), Status/Laufzeit (status/uptime), CPU or RAM. A second click reverses the order, a third restores the default order. The uptime is based on the actual start time.

**Dock edition and setup**
- Nodvard Dock shows its name on the “Über” (about) page and receives dedicated changelog entries and release notes.
- The setup wizard enables the Docker module automatically instead of asking.
- Accessing Git servers on the local network requires enabling “Git-Server im eigenen Netz erlauben” (allow Git servers on local network) under “Erweitert” (advanced) in Docker settings.

**Documentation**
- Nodvard Dock now features comprehensive German and English documentation from initial start to troubleshooting, covering setup, features, limits and licensing.

### Fixed

**Nodvard Dock interface**
- Nodvard Dock directly opens Docker management and only shows matching pages, settings and help texts.
- Nodvard Dock blocks unavailable features on direct navigation and prevents built-in modules from being disabled.
- The container console in Nodvard Dock no longer shows a general server selection dropdown.
- Nodvard Dock shows its own name in title, login and setup, and emergency and setup commands reference the correct service.
- The “Einstellungen → System → Updates” (Settings → System → Updates) page checks for the official Dock image (`ghcr.io/nodvard/dock`) and shows appropriate instructions.
- Nodvard Dock removes the toggle for automatic installation of security updates.
- Modules with updated names like Docker find their files even if only an empty folder exists under the new name.
- Password and token changes take effect immediately across all modules without restarting.

**Docker on mobile**
- Stacks, Images, Volumes and Netze (networks) tabs display each item as a dedicated card with buttons placed underneath, preventing text overlapping.
- Under “Docker-Speicher” (Docker storage), the image list is no longer clipped on mobile screens; size, age and usage are clearly labelled.
- The mobile browser bar no longer obscures the bottom of the page.

**Networks and actions**
- When connecting or disconnecting containers, the Docker page keeps the result and error message visible even after collapsing the container section.
- Errors during automatic network creation appear with their explanation in the form.
- The Docker page preserves results, errors and rejection reasons for resource actions during reloads and load errors.
- The Docker page displays action results after external approvals, and concurrent resource actions remain visible together.
- Editing name and Compose file while loading a custom stack template no longer discards unsaved input.

### Security

**Stacks from Git**
- Before fetching, Dock verifies the actual target address of the Git server hostname and only connects to that resolved address. Dock itself and internal cloud provider services are always blocked.
- When running behind a proxy (HTTPS_PROXY), Dock only fetches from IP addresses because the proxy resolves hostnames. Named Git servers must be configured under NO_PROXY for direct connections.

**Authentication and sessions**
- Responses containing credentials and account details now prohibit storage in browser cache.
- Setup and system settings warn about unencrypted local network access and show paths to secure connections, even when connecting through untrusted local proxies or with unreadable client addresses.

**Helper scripts and UI**
- sudo executes helper scripts only in the exact version deployed during server setup (from next setup, not with sudo-rs).
- The web interface enforces a stricter Content Security Policy against third-party scripts and styles. Console, extensions and file previews remain functional.

### Good to know

- **Updating:** update as usual in the folder containing your Compose file: `docker compose pull && docker compose up -d`.
- **Dedicated name and interface:** title, login, setup and the about page now say Nodvard Dock. The “Einstellungen → System → Updates” (Settings → System → Updates) page looks for `ghcr.io/nodvard/dock` and displays Dock instructions.
- **No module selection required:** the setup assistant activates Docker automatically because Nodvard Dock includes no other modules.
- **Fixed built-in modules:** Docker cannot be disabled in Nodvard Dock, and Deck-only routes are blocked.

### Install in two steps

You need a machine with Docker and Docker Compose (amd64 or arm64, for example a Raspberry Pi 4/5 with a 64-bit system).

1. In an empty folder, download the Compose file:
   ```bash
   curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/dock/main/compose.yml
   ```
2. Start it and open `http://<your-server>:8080` in the browser:
   ```bash
   docker compose up -d
   ```

`docker compose logs nodvard-dock` shows the setup code for the first step.

### Limits

- **Beta:** the user interface is in German only for now.
- **Stacks from Git:** `https://` only, one Compose file per stack, no automatic deploys and no webhook (scheduled checks notify of updates, deploying remains manual with approval).
- **Permissions when deploying from Git:** if Dock does not log in as the owner of the stack folder, it needs root or `sudo -n` for `setpriv` (which amounts to root rights). Otherwise it writes nothing.
- **Stack folders owned by root:** Dock then writes with root rights, and a link inside the folder can point anywhere on the machine. Only the check right before each file protects against that. Keep stacks in folders of a normal user.
- **Single Docker hosts:** stacks are Docker Compose projects. Dock does not support Swarm or Kubernetes.
- **Connected over SSH:** the machine Dock runs on is connected over SSH too. Dock gets no Docker socket.
- **Rights on the server:** Dock needs a user in the `docker` group, which is practically root. If that is too much, “Nur bedienen” (operate only) mode offers fewer functions.
- **Whoever can use Dock can control your servers.** Use a strong password, turn on two-factor login and keep Dock off the open internet.

### License

PolyForm Noncommercial License 1.0.0: free for personal and other non-commercial use. The source code is available to view. Companies and paid services need separate permission: kontakt@nodvard.com.
