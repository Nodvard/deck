# Nodvard Dock 0.12.0 (Beta) – Docker-Stacks aus Git und engere Grenzen für Anmeldungen

*English version: [below](#english).*

## Deutsch

**Nodvard Dock** verwaltet die Container auf deinen Servern in einer Oberfläche. Es ist Nodvard Deck mit nur dem Modul
Docker: derselbe Code, nur weniger drin.

### Das Wichtigste

- **Docker-Stacks aus Git:** Stacks aus einem Git-Repository einspielen, mit Vorschau jeder Änderung und Weg zurück zum Stand davor.
- **„Nur bedienen“ hält auch bei schon vorgeschlagenen Aktionen:** Wird ein Server umgestellt, läuft dort nur noch Starten, Stoppen und Neustarten.
- **Mehr Sicherheit:** Abmelden wirkt sofort, Passwort-Raten wird gebremst, und der Browser lädt nur noch vom eigenen Server.
- **Keine Umstellung der Datenbank.**
- **Noch nicht behoben:** Name, Befehl für den Einrichtungscode, die Frage nach Modulen und die Seite „Updates“ sind noch auf Nodvard Deck zugeschnitten, siehe „Gut zu wissen“.

### Was du tun musst

1. **Aktualisieren** im Ordner mit der Compose-Datei: `docker compose pull && docker compose up -d`. Die Seite Einstellungen → System → Updates zeigt in 0.12 noch Anleitungen für Nodvard Deck, für Dock gilt nur dieser Befehl.
2. **Server im Modus „Nur bedienen“: Einrichtungsbefehl noch einmal ausführen (empfohlen).** Erst danach spricht Dock dort immer mit Docker auf dem Server selbst, und „Verbindung prüfen“ zeigt das Hilfsskript richtig an.
3. **Alte volle sudo-Regel entfernen (empfohlen).** Server, die vor 0.11.1 mit „Root-Rechte ohne Passwort“ eingerichtet wurden, haben oft noch `<benutzer> ALL=(root) NOPASSWD: ALL`. Die Übersicht weist unter „Braucht Aufmerksamkeit“ darauf hin und führt zum fertigen Einrichtungsbefehl mit „Volle sudo-Regel entfernen“.

### Neu

**Docker: Stacks aus Git**
- Ein übernommener Stack kann ein Git-Repository als Quelle bekommen, nur über `https://`.
- Vor jedem Einspielen siehst du, welche Dateien neu oder anders wären, samt Unterschied der Compose-Datei und Hinweisen auf alles, was nicht aus dem Repository kommt.
- Beim Ansehen führt Dock nichts aus dem Repository aus, Compose arbeitet erst nach der Freigabe damit.
- Eingespielt wird nach Rückfrage und Freigabe, mit den Rechten des Besitzers des Stack-Ordners.
- Lehnt Compose den neuen Stand ab, steht sofort wieder der alte auf dem Server.
- „Zurück zum Stand davor“ stellt den Stand vor dem letzten Einspielen wieder her und entfernt dabei nur Dateien, die Dock selbst geschrieben hat und die seitdem niemand geändert hat.
- Dock ersetzt vorhandene Umgebungsdateien wie `.env` nie und löscht eine selbst angelegte nur bei „Zurück zum Stand davor“ wieder.
- Neu gestartet werden nur die Dienste aus der Freigabe, und für weitere gibt es „Jetzt neu starten“ mit eigener Freigabe.
- Nach dem Neustart prüft Dock, ob die Dienste stabil laufen, auch solche, die vorher gestoppt waren.
- Vorher lassen sich die Volumes des Stacks sichern, wobei die Dienste, die sie nutzen, stillstehen.
- Zurückspielen geht im Reiter Stacks unter „Sicherungen“.
- Git-Zugänge für private Repositorys stehen in den Einstellungen des Moduls Docker, und ihr Token liegt im Tresor und geht nur an den eingetragenen Server.

**Server und Einstellungen**
- „Volle sudo-Regel entfernen“ unter „Erweitert“ im Einrichtungsbefehl prüft jedes Hilfsskript vorher und nachher und stellt die Regel bei einem Fehler sofort wieder her.
- Meldet sich der Inhaber über einen Proxy im Heimnetz an, der nicht als vertrauenswürdig eingetragen ist, gibt es einen Hinweis, denn ohne Eintrag sieht Dock die echten Adressen nicht.

### Sicherheit

**Docker**
- Steht ein Server inzwischen auf „Nur bedienen“, wird eine vorgeschlagene Aktion nicht mehr ausgeführt, außer Starten, Stoppen und Neustarten von Containern.
- Ist ein Server nicht mehr als Docker-Server markiert, wird dort keine vorgeschlagene Docker-Aktion mehr ausgeführt.
- „Nur bedienen“ nutzt nach dem erneuten Einrichten immer Docker auf dem Server selbst, auch wenn dort für den Administrator ein anderer Docker-Rechner eingestellt ist.

**Anmeldung**
- Zugangs-Schlüssel werden beim Abmelden, beim Passwortwechsel (für die anderen Anmeldungen) und beim Deaktivieren und Wiederaktivieren eines Kontos sofort ungültig, statt noch bis zu 15 Minuten zu gelten.
- Nach 10 falschen Anmeldungen für einen Namen in 24 Stunden wartet jeder weitere Versuch aus demselben Netz doppelt so lange wie der vorige, höchstens 5 Minuten, und du bekommst eine Meldung.
- Die Bremse gilt je Netz: Aus deinem Heimnetz, per VPN und aus anderen Netzen kommst du weiter sofort hinein.
- Gegen Angreifer mit sehr vielen Adressen helfen weiter ein langes Passwort und Zwei-Faktor.

**Browser**
- Kamera, Mikrofon, Standort und andere Geräte, die Dock nie braucht, sind im Browser gesperrt.
- Fremde Seiten können Dock nicht mehr einbinden, außer Logo, Symbolen und der Erreichbarkeits-Prüfung.
- Bilder, Daten und Verbindungen lädt die Oberfläche nur noch vom eigenen Server, und der Browser sperrt fremde Adressen jetzt, statt sie nur zu melden.

**Server und Hilfsskripte**
- Hilfsskripte landen nur noch in Ordnern, in die außer root niemand schreiben darf, auch keine Gruppe.
- Ist der Ordner zu offen, nennt die Einrichtung Grund und Abhilfe, und „Verbindung prüfen“ warnt bei schon eingerichteten Servern.

**Weiteres**
- Scheitert eine Sicherung am Schreibrecht, stehen Benutzernummer und Befehl nur noch unter Einstellungen → System, nicht in Protokoll und Benachrichtigung.
- Die eingebaute Git-Bibliothek ist aktualisiert und bricht bei absichtlich kaputten Git-Daten nicht mehr ab.
- Alle Härtungen stehen in den Release-Notes von Nodvard Deck 0.12.0.

### Verbessert

- **Erst-Einrichtung:** Zwei-Faktor lässt sich auch nach „Jetzt einrichten“ mit „Später einrichten“ überspringen.
- **Modul Docker:** Stürzt es beim Start ab, steht der Grund verständlich da, mit den Knöpfen „Neu starten“ und „Ausschalten“.
- **sudo:** „Verbindung prüfen“ und die Übersicht zeigen eine unnötige volle sudo-Regel und führen direkt zum passenden Einrichtungsbefehl.
- **Sicherungen:** Fehlt das Schreibrecht für den Sicherungsordner, zeigt Einstellungen → System Benutzer, Besitzer und den Befehl zum Beheben.
- **Startseite:** Widgets zeigen nur noch Knöpfe, für die du das Recht hast.
- **Anmeldungen:** Längst abgelaufene oder beendete Anmeldungen samt Adresse und Browser werden jede Nacht gelöscht.

### Behoben

- **Docker:** Ein Server mit den Markierungen „docker“ und „docker-bedienen“ gilt als voller Docker-Server.
- **Docker:** Einrichtung, „Verbindung prüfen“ und Übersicht melden ein fehlendes Hilfsskript für Docker jetzt als Problem.
- **Docker:** „Verbindung prüfen“ und Übersicht zeigen ein älteres Hilfsskript, das bis zum erneuten Einrichten weiterläuft, jetzt richtig an.
- **Docker:** Fehlt einem Server die Markierung „docker“, nennen Image-Update, Stack-Aktionen, Umzug von Portainer und Registry-Anmeldung den Grund statt nur „Kein Docker-Host.“
- **Docker:** Bricht das Zurückspielen einer Volume-Sicherung vor dem Tausch der Daten ab, läuft der Stack wieder und der halb entpackte Ordner ist weg.
- **Docker:** Bleibt ein Stack nach so einem Abbruch gestoppt, sagt eine Meldung Grund und nächsten Schritt.
- **Docker:** Eine unerwartete Antwort einer Registry macht nur dieses Image unprüfbar, nicht mehr den ganzen Server.
- **Freigaben:** Abgelaufene Freigaben heißen „Abgelaufen“ und lassen sich nur noch verwerfen.
- **Hilfsskripte:** Bei einer ACL am Ordner der Hilfsskripte nennt die Einrichtung jetzt `setfacl -b` und danach `chmod g-w` als Abhilfe.
- **Einrichtung:** Hast du den Schlüssel von Dock auf einem Server mit `#` abgeschaltet, trägt das erneute Einrichten ihn wieder funktionierend ein.
- **Anzeige:** Docker-Stacks mit vielen Knöpfen brechen um, und Datum und Uhrzeit stehen immer auf Deutsch da.

### Gut zu wissen

- **Noch auf Nodvard Deck zugeschnitten:** Titel, Anmeldung und Einrichtung heißen in 0.12 noch „Nodvard Deck“.
- **Einrichtungscode:** `docker compose logs nodvard-dock` im Ordner mit der Compose-Datei zeigt ihn, die Einrichtungsseite nennt in 0.12 noch `nodvard-deck`.
- **Assistent:** Im Schritt „Was willst du nutzen?“ das Modul **Docker** einschalten, ein anderes gibt es in Dock nicht.
- **Seite „Updates“:** Die Seite spricht in 0.12 von einem „selbst gebauten Image“ und zeigt Anleitungen für `ghcr.io/nodvard/deck` und `scripts/deploy_pi.sh`, für Dock gilt nur `docker compose pull && docker compose up -d`.
- **Offene Browser-Tabs:** Ein Tab, der vor dem Update offen war, muss beim ersten Öffnen einer Modulseite einmal neu geladen werden, die Seite bietet dafür einen Knopf.

### Installieren in zwei Schritten

Voraussetzung: ein Rechner mit Docker und Docker Compose (amd64 oder arm64, etwa ein Raspberry Pi 4/5 mit
64-Bit-System).

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
- **Stacks aus Git:** nur `https://`, eine Compose-Datei je Stack, kein automatisches Einspielen und kein Webhook.
- **Rechte beim Einspielen aus Git:** Meldet sich Dock nicht als Besitzer des Stack-Ordners an, braucht es root oder `sudo -n` für `setpriv` (das kommt root-Rechten gleich), sonst schreibt es nichts.
- **Stack-Ordner von root:** Dann schreibt Dock mit root-Rechten, und ein Verweis im Ordner kann auf den ganzen Rechner zeigen, wovor nur die Prüfung direkt vor jeder Datei schützt. Empfehlung: Stacks in Ordnern eines normalen Benutzers.
- **Docker auf einzelnen Servern:** Stacks sind Docker-Compose-Projekte, Swarm und Kubernetes unterstützt Dock nicht.
- **Anbindung über SSH:** Auch der eigene Rechner wird über SSH angebunden, einen Docker-Socket bekommt Dock nicht.
- **Rechte auf dem Server:** Dock braucht dort einen Benutzer in der Gruppe `docker`, das heißt praktisch root. Wo dir das zu viel ist, gibt es den Modus „Nur bedienen“ mit weniger Funktionen.
- **Wer Dock bedienen darf, kann deine Server steuern.** Darum: starkes Passwort, Zwei-Faktor an und Dock nicht offen ins Internet stellen.

### Lizenz

PolyForm Noncommercial License 1.0.0: privat und für andere nicht-kommerzielle Zwecke frei nutzbar. Der Quellcode ist
einsehbar, Nodvard Dock ist aber keine Open-Source-Software im engeren Sinn. Firmen und bezahlte Dienste brauchen eine
gesonderte Erlaubnis: kontakt@nodvard.com.

---

## English

**Nodvard Dock** manages the containers on your servers in one user interface. It is Nodvard Deck with only the Docker
module: the same code, just less of it. The web interface is in German only for now. Buttons, pages and settings are
quoted in German as they appear, with a translation in brackets.

### The essentials

- **Docker stacks from Git:** deploy a stack straight from a Git repository, preview every change and step back to the previous state.
- **“Nur bedienen” (operate only) applies right away:** switching a server blocks actions that were already proposed, except start, stop and restart.
- **Security:** logging out takes effect immediately, password guessing is throttled, and the browser only loads from your own server.
- **No database migration:** update and keep working.
- **Not fixed yet:** the name, the setup code command, the module question in the setup assistant and the “Updates” page still assume Nodvard Deck. See “Good to know”.

### What you need to do

1. **Update** in the folder with the Compose file: `docker compose pull && docker compose up -d`. In 0.12 the page “Einstellungen → System → Updates” (Settings → System → Updates) still shows guides for Nodvard Deck. For Dock, only this command applies.
2. **Servers in “Nur bedienen” (operate only) mode: run the setup command again (recommended).** Afterwards Dock always talks to Docker on the server itself, and “Verbindung prüfen” (check connection) shows the helper script correctly.
3. **Remove the old full sudo rule (recommended).** Servers set up before 0.11.1 with “Root-Rechte ohne Passwort” (root without password) often still have `<user> ALL=(root) NOPASSWD: ALL`. The overview flags it under “Braucht Aufmerksamkeit” (needs attention) and links to the setup command with “Volle sudo-Regel entfernen” (remove full sudo rule).

### Added

**Docker: stacks from Git**
- **Git as source:** an adopted stack can be deployed from a Git repository (`https://` only).
- **Change preview:** see every new or changed file, the Compose diff and hints about anything that does not come from the repository.
- **Safe inspection:** Dock runs nothing from the repository while you look. Compose only works with it after the approval.
- **Approved deploys:** changes are applied after a confirmation and an approval (“Freigabe”), with the permissions of the stack folder's owner.
- **Automatic fallback:** if Compose rejects the new files, the previous state is back on the server immediately.
- **Safe rollback:** “Zurück zum Stand davor” (back to previous state) restores the state before the last deploy and only deletes unmodified files that Dock created itself.
- **Your `.env` stays yours:** Dock never replaces an existing environment file and only removes one it created itself, on rollback.
- **Targeted restarts:** only the services named in the approval restart. “Jetzt neu starten” (restart now) handles the rest with its own approval.
- **Health check:** after a restart, Dock checks that the services keep running, including ones that were stopped before.
- **Volume backups:** back up the stack's volumes before deploying. The services using them stop during the backup.
- **Restore:** restore those backups in the Stacks tab under “Sicherungen” (backups).
- **Private repositories:** Git credentials live in the Docker module settings, the token stays in the vault (“Tresor”) and only goes to the server it is for.

**Servers and settings**
- **Remove the full sudo rule safely:** “Volle sudo-Regel entfernen” (remove full sudo rule) under “Erweitert” (advanced) checks every helper script before and after, and restores the rule if anything breaks.
- **Proxy hint:** the owner gets a hint when logging in through a home network proxy that is not listed as trusted, because without that entry Dock cannot see real addresses.

### Changed

- **First setup:** two-factor login can be skipped with “Später einrichten” (set up later), even after starting it.
- **Docker module:** if it crashes on start, it shows the reason with “Neu starten” (restart) and “Ausschalten” (turn off).
- **sudo:** “Verbindung prüfen” (check connection) and the overview flag a full sudo rule that is no longer needed and link to the right setup command.
- **Backups:** if Dock cannot write to the backup folder, “Einstellungen → System” (Settings → System) shows the user, the folder's owner and the fix.
- **Home page:** widgets only show buttons you are allowed to use.
- **Logins:** logins that expired or ended long ago are deleted every night, including address and browser.

### Fixed

**Docker**
- A server tagged both “docker” and “docker-bedienen” counts as a full Docker server.
- Setup, “Verbindung prüfen” (check connection) and the overview report a missing Docker helper script as a problem.
- “Verbindung prüfen” and the overview correctly show that an older Docker helper script keeps working until the setup command runs again.
- On a server without the “docker” tag, image updates, stack actions, Portainer migration and registry login now say why, instead of just “Kein Docker-Host.” (no Docker host).
- If a volume restore aborts before the data swap, the stack runs again and the half-extracted folder is gone.
- If a stack stays stopped after such an abort, a message gives the reason and the next step.
- An unexpected registry response only marks that image as uncheckable, not the whole server.

**Other**
- **Approvals:** expired approvals are labelled “Abgelaufen” (expired) and can only be dismissed.
- **Helper scripts:** for an ACL on the helper script folder, setup now suggests `setfacl -b` followed by `chmod g-w`.
- **Server setup:** if you disabled Dock's key on a server with a leading `#`, running the setup command again restores a working key.
- **Display:** Docker stacks with many buttons wrap, and dates and times are always in German.

### Security

**Docker**
- Once a server is switched to “Nur bedienen” (operate only), proposed actions no longer run there, except starting, stopping and restarting containers.
- Once a server is no longer tagged as a Docker server, proposed Docker actions no longer run there.
- After the setup command runs again, “Nur bedienen” always uses Docker on the server itself, even if another Docker host is set for the administrator there.

**Logins**
- Access tokens stop working immediately on logout, on a password change (for the other logins) and when an account is deactivated and reactivated, instead of up to 15 minutes later.
- After 10 failed logins for one user name within 24 hours, each further attempt from the same network waits twice as long as the one before, up to 5 minutes, and you get an alert.
- The throttling applies per network, so you can still log in right away from your home network, a VPN or anywhere else.
- Against attackers with very many addresses, a long password and two-factor login still help.

**Browser**
- Camera, microphone, location and other devices Dock never needs are blocked.
- Other sites can no longer embed Dock, except for the logo, icons and the reachability check.
- The interface only loads images, data and connections from its own server, and the browser now blocks other addresses instead of only reporting them.

**Other**
- **Helper scripts:** they are only installed in folders that nobody but root can write to, not even a group.
- **Helper scripts:** if the folder is too open, setup names the reason and the fix, and “Verbindung prüfen” (check connection) warns about servers already set up.
- **Backups:** the user ID and fix command for a failed backup only appear under “Einstellungen → System” (Settings → System), not in logs and notifications.
- **Dependencies:** the bundled Git library is up to date and no longer crashes on deliberately broken Git data.
- The release notes of Nodvard Deck 0.12.0 list all hardening.

### Good to know

- **Still says Nodvard Deck:** in 0.12 the title, the login page and the setup still say “Nodvard Deck”.
- **Setup code:** run `docker compose logs nodvard-dock` in the folder with the Compose file. The setup page still says `nodvard-deck` in 0.12.
- **Setup assistant:** in the step “Was willst du nutzen?” (what do you want to use), switch on the **Docker** module. Dock has no other.
- **“Updates” page:** in 0.12 it talks about a “selbst gebautes Image” (self-built image) and shows guides for `ghcr.io/nodvard/deck` and `scripts/deploy_pi.sh`. For Dock, only `docker compose pull && docker compose up -d` applies.
- **Open browser tabs:** a tab that was open before the update needs one reload when it first opens a module page. The page offers a button.

### Install in two steps

You need a machine with Docker and Docker Compose (amd64 or arm64, for example a Raspberry Pi 4/5 with a 64-bit
system).

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
- **Stacks from Git:** `https://` only, one Compose file per stack, no automatic deploys and no webhook.
- **Permissions when deploying from Git:** if Dock does not log in as the owner of the stack folder, it needs root or `sudo -n` for `setpriv` (which amounts to root rights). Otherwise it writes nothing.
- **Stack folders owned by root:** Dock then writes with root rights, and a link inside the folder can point anywhere on the machine. Only the check right before each file protects against that. Keep stacks in folders of a normal user.
- **Single Docker hosts:** stacks are Docker Compose projects. Dock does not support Swarm or Kubernetes.
- **Connected over SSH:** the machine Dock runs on is connected over SSH too. Dock gets no Docker socket.
- **Rights on the server:** Dock needs a user in the `docker` group, which is practically root. If that is too much, “Nur bedienen” (operate only) mode offers fewer functions.
- **Whoever can use Dock can control your servers.** Use a strong password, turn on two-factor login and keep Dock off the open internet.

### License

PolyForm Noncommercial License 1.0.0: free for personal and other non-commercial use. The source code is open to read,
but Nodvard Dock is not open source software in the strict sense. Companies and paid services need separate
permission: kontakt@nodvard.com.
