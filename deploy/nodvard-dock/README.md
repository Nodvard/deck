# Nodvard Dock (Beta)

Container-Verwaltung für Docker, schlicht und auf Deutsch. Gedacht für alle, die ihre Container auf ein paar Servern
im Blick behalten wollen, auch für Umsteiger von Portainer CE. Nodvard Dock ist Nodvard Deck mit nur dem Modul Docker:
derselbe Code, nur weniger drin.

*English version: [below](#english).*

> **Beta-Stand:** Das fertige Image `ghcr.io/nodvard/dock` gibt es erst ab Version 0.11.0. Bis dahin kannst du
> es [selbst bauen](#selbst-bauen). Die Oberfläche heißt in dieser Beta noch „Nodvard Deck“.

## Was es kann

- **Container** aller Server auf einer Seite: Zustand, Starten, Stoppen, Neustarten, gestoppte Container löschen,
  Konsole, Details. Einen mit `docker run` gestarteten Container wandelst du in einen Stack um; läuft der neue nicht,
  kommt der alte zurück. Läuft der neue, löscht Nodvard Dock den alten (was nur im Container lag, ist dann weg).
- **Logs** mit Zeitraum, Suche in den geladenen Zeilen und Download.
- **Image-Updates** finden und bei Compose-Diensten einspielen. Das alte Image bleibt als Sicherung. Stürzt der neue
  Container beim Start ab, geht es von selbst zurück, außer bei Datenbanken und anderen Datenspeichern (abschaltbar in
  den Einstellungen des Moduls).
- **Images, Volumes und Netze** auflisten und einzeln löschen; unbenutzte Images und den Build-Cache auf einmal
  aufräumen.
- **Stacks** sehen und ihre Compose-Datei lesen. Stacks aus einem Ordner auf dem Server kannst du übernehmen; Nodvard
  Dock merkt sich dann Ordner und Dateien, am Server ändert sich dabei nichts. Übernommene Stacks lassen sich anwenden,
  stoppen, herunterfahren (die Volumes bleiben) und ihre Images holen. Stacks, die Portainer angelegt hat, ziehen in
  einen eigenen Ordner um (siehe „Umsteigen von Portainer“).
- **Compose-Dateien bearbeiten,** als Text oder im Formular, das nur die geänderte Stelle anfasst. Vor dem Speichern
  prüft Docker Compose die Datei auf dem Server, gespeichert wird über die Freigabe, und die letzten Stände bleiben im
  Verlauf. Neue Stacks legst du in einem Ordner je Server an (Vorgabe `/opt/stacks`), auch aus einer Vorlage: Uptime
  Kuma, Vaultwarden, Nginx Proxy Manager und Gitea. Stacks löschen geht mit Herunterfahren; den Ordner löscht Nodvard
  Dock nur, wenn du es anhakst.
- **Stacks als Ganzes aktualisieren:** Die alten Images bleiben als Sicherung, und „Zurück zur vorigen Version“ steht
  danach im Reiter Stacks bereit. Auf Wunsch sichert Nodvard Dock vorher die Volumes, mit Platzprüfung.
- **Freigabe und Protokoll:** Jede Änderung an Containern, Stacks, Images, Volumes und Netzen ist eine Aktion mit
  Risikostufe.
  Was ohne Rückfrage durchläuft, bestimmt der Inhaber. Alles steht im Protokoll.

**Noch nicht in dieser Beta:** Volume-Sicherungen über die Oberfläche zurückspielen und das Anmelden bei privaten
Registries. Das kommt mit den nächsten Beta-Ständen.

## In 5 Minuten startklar

**Voraussetzung:** ein Rechner mit Docker und Docker Compose, etwa ein Raspberry Pi 4/5 mit 64-Bit-System oder ein
x86-64-Rechner.

```bash
mkdir ~/nodvard-dock && cd ~/nodvard-dock
curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/nodvard-dock/compose.yml
docker compose up -d
```

- **Öffnen:** `http://<dein-server>:8080`
- **Einrichtungscode:** `docker compose logs nodvard-dock` im selben Ordner, die Zeile beginnt mit
  „Einrichtungscode“. Die Einrichtungsseite nennt in dieser Beta noch `nodvard-deck`; bei Dock heißt der Dienst
  `nodvard-dock`.
- **Port schon belegt?** Neben die `compose.yml` eine Datei `.env` mit der Zeile `NODVARD_DOCK_PORT=8081` legen und
  `docker compose up -d` noch einmal ausführen.
- **Aktualisieren:** `docker compose pull && docker compose up -d`. Deine Daten bleiben erhalten.

Danach im Browser dem Assistenten folgen:

1. **Administrator-Konto anlegen** und die Zeitzone wählen.
2. **Was willst du nutzen?** Hier das Modul **Docker** einschalten.
3. **Zwei-Faktor-Anmeldung:** Der Schritt ist freiwillig, wir empfehlen ihn sehr.

Dann den ersten Server anbinden:

1. Unter **Einstellungen → Server & Zugänge** den Server anlegen, mit der Markierung `docker`.
2. **Mit Passwort einrichten:** Du gibst einmal ein Konto mit root- oder sudo-Rechten an. Nodvard Dock legt damit
   einen eigenen Benutzer mit SSH-Schlüssel an, trägt ihn in die Gruppe `docker` ein und vergisst das Passwort.
3. Die Seite **Docker** zeigt jetzt die Container dieses Servers.

**Der eigene Rechner:** Auch den Rechner, auf dem Nodvard Dock läuft, trägst du so ein, mit seiner Adresse im
Heimnetz (nicht `localhost`, das wäre der Container selbst).

**Eigener Datenspeicher:** Nodvard Dock hat sein eigenes Volume `nodvard-dock-data`. Den Datenspeicher eines
Nodvard Deck kann es nicht übernehmen, weil darin Daten von Modulen liegen, die Dock nicht kennt.

## Umsteigen von Portainer

- **Beide dürfen nebeneinander laufen.** Nodvard Dock ändert nichts, solange du nichts auslöst.
- **Container und Volumes bleiben, wo sie sind.** Nach dem Einrichten des Servers siehst du alle Container, auch die
  aus Portainer, und kannst sie starten, stoppen und neu starten.
- **Stacks aus einem Ordner** (gestartet mit `docker compose up` in einem Ordner auf dem Server) übernimmst du im
  Reiter **Stacks** mit **Übernehmen**. Danach kannst du sie dort anwenden, stoppen und herunterfahren.
- **Stacks, die Portainer selbst angelegt hat,** liegen in Portainers eigenem Datenspeicher. Nodvard Dock zeigt sie
  mit dem Abzeichen „Portainer“. **Von Portainer übernehmen** holt Datei und Variablen aus diesem Speicher, legt sie in
  einen eigenen Ordner (Vorgabe `/opt/stacks/<Name>`) und rollt den Stack unter demselben Namen neu aus; die Volumes
  bleiben dieselben. Zeigt die Datei mit relativen Pfaden auf Daten bei Portainer, besteht sie aus mehreren Dateien
  (etwa aus Git) oder passt sie nicht zu den laufenden Diensten, zieht Nodvard Dock nicht um und nennt den Grund, bei
  relativen Pfaden auch den Ort der Daten.
- **Danach den Stack nicht in Portainer löschen** und dort nicht neu ausrollen: Er hat denselben Namen, und Portainer
  würde die neuen Container entfernen oder mit seiner alten Datei ersetzen. Lass den Eintrag einfach stehen.
- **Portainer abschalten:** erst, wenn alle Stacks umgezogen sind. Stoppst du den Container von Portainer, laufen deine
  anderen Container weiter.

## Rechte, ehrlich gesagt

- **Kein Docker-Socket im Container.** Wer den Socket hat, ist root auf dem Rechner, und das wäre jeder, der in die
  Oberfläche kommt. Nodvard Dock spricht darum mit jedem Server über SSH mit einem eigenen Benutzer, auch mit dem
  eigenen Rechner. So funktioniert es für entfernte Server genauso.
- **Die Gruppe `docker` heißt praktisch root.** Wer Docker steuern darf, kann jeden Ordner des Rechners in einen
  Container einhängen. Das gilt für jedes Werkzeug dieser Art, auch für Portainer.
- **Die SSH-Schlüssel liegen verschlüsselt** im Tresor von Nodvard Dock. Wer Nodvard Dock bedienen darf, kann deine
  Server steuern. Darum: ein starkes Passwort, Zwei-Faktor an und Nodvard Dock nicht offen ins Internet stellen.
- **Riskantes nur mit Freigabe:** Löschen, Stoppen und Updates sind Aktionen mit Risikostufe. Rollen wie
  Administrator, Bediener und Betrachter legen fest, wer was darf.
- **Ohne die Gruppe `docker`:** Ein Modus „Nur bedienen“, der über ein eigenes Hilfsskript nur Starten, Stoppen und
  Neustarten erlaubt, ist in Arbeit.

## Selbst bauen

Im geklonten Repository, am besten auf einem PC (auf einem Raspberry Pi dauert der Bau sehr lange):

```bash
docker build -f deploy/Dockerfile --build-arg NODVARD_DECK_EXTENSIONS=docker -t nodvard-dock:selbst .
```

Dann in der `compose.yml` die Zeile `image:` auf `nodvard-dock:selbst` ändern und `docker compose up -d`.

## Lizenz

Wie Nodvard Deck: **PolyForm Noncommercial License 1.0.0**, siehe [LICENSE](../../LICENSE). Kurz gesagt (verbindlich
ist nur der Lizenztext): Privat und für andere nicht-kommerzielle Zwecke ist Nodvard Dock frei nutzbar. Firmen und
bezahlte Dienste brauchen eine gesonderte Erlaubnis. Anfragen: **kontakt@nodvard.com**. Der Quellcode ist einsehbar,
Nodvard Dock ist aber keine Open-Source-Software im engeren Sinn.

Docker und Portainer sind Marken ihrer jeweiligen Inhaber. Nodvard Dock steht in keiner Verbindung zu ihnen.

---

## English

Container management for Docker, simple and to the point. Made for anyone who wants to keep an eye on the containers
on a few servers, including people moving away from Portainer CE. Nodvard Dock is Nodvard Deck with only the Docker
module: the same code, just less of it. The user interface is in German for now.

> **Beta:** The ready-made image `ghcr.io/nodvard/dock` is only published from version 0.11.0 on. Until then you can
> [build it yourself](#build-it-yourself). In this beta the user interface is still called "Nodvard Deck".

### What it does

- **Containers** of all servers on one page: state, start, stop, restart, delete stopped containers, console,
  details. A container started with `docker run` can be turned into a stack; if the new one does not run, the old one
  comes back. If the new one runs, Nodvard Dock deletes the old one (anything kept only inside the container is gone then).
- **Logs** with time range, search in the loaded lines and download.
- **Image updates:** finds them and applies them to Compose services. The old image is kept as a backup. If the new
  container crashes on start, it rolls back on its own, except for databases and other data stores (can be turned off
  in the module settings).
- **Images, volumes and networks:** lists and deleting single ones; clean up unused images and the build cache in one
  go.
- **Stacks:** view them and read their Compose file. Stacks from a folder on the server can be adopted; Nodvard Dock
  then remembers folder and files, nothing changes on the server. Adopted stacks can be applied, stopped, taken down
  (volumes stay) and have their images pulled. Stacks created by Portainer can move into a folder of their own (see
  "Moving from Portainer").
- **Editing Compose files,** as text or in a form that only touches the changed spot. Docker Compose checks the file
  on the server before saving, saving goes through approval, and the latest versions stay in the history. New stacks
  are created in a folder per server (default `/opt/stacks`), also from a template: Uptime Kuma, Vaultwarden, Nginx
  Proxy Manager and Gitea. Deleting a stack takes it down; Nodvard Dock deletes its folder only if you tick that box.
- **Updating stacks as a whole:** the old images are kept as a backup, and "back to the previous version" is ready
  in the Stacks tab afterwards. On request Nodvard Dock backs up the volumes first, after checking the free space.
- **Approval and audit log:** every change to containers, stacks, images, volumes and networks is an action with a
  risk level. The owner decides what runs without asking. Everything is logged.

**Not in this beta yet:** restoring volume backups from the user interface and logging in to private registries.
These come with the next beta releases.

### Up and running in 5 minutes

**You need** a computer with Docker and Docker Compose, for example a Raspberry Pi 4/5 with a 64-bit system or an
x86-64 machine.

```bash
mkdir ~/nodvard-dock && cd ~/nodvard-dock
curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/nodvard-dock/compose.yml
docker compose up -d
```

- **Open:** `http://<your-server>:8080`
- **Setup code:** `docker compose logs nodvard-dock` in the same folder, the line starts with "Einrichtungscode". The
  setup page still says `nodvard-deck` in this beta; for Dock the service is called `nodvard-dock`.
- **Port already in use?** Put a file `.env` next to `compose.yml` with the line `NODVARD_DOCK_PORT=8081` and run
  `docker compose up -d` again.
- **Update:** `docker compose pull && docker compose up -d`. Your data stays.

Then follow the setup assistant in the browser:

1. **Administrator-Konto anlegen** (create the administrator account) and pick the time zone.
2. **Was willst du nutzen?** (what do you want to use): switch on the **Docker** module here.
3. **Zwei-Faktor-Anmeldung** (two-factor sign-in): optional, but strongly recommended.

Then connect your first server:

1. Under **Einstellungen → Server & Zugänge** (Settings → Servers & access), add the server with the tag `docker`.
2. **Mit Passwort einrichten** (set up with password): enter an account with root or sudo rights once. Nodvard Dock
   creates its own user with an SSH key, adds it to the `docker` group and forgets the password.
3. The **Docker** page now shows the containers of this server.

**The machine itself:** Add the computer Nodvard Dock runs on the same way, with its address in your home network
(not `localhost`, which would be the container itself).

**Its own data volume:** Nodvard Dock has its own volume `nodvard-dock-data`. It cannot take over the data volume of
a Nodvard Deck, because that holds data of modules Dock does not know.

### Moving from Portainer

- **Both can run side by side.** Nodvard Dock changes nothing unless you trigger it.
- **Containers and volumes stay where they are.** Once the server is set up, you see all containers, including the
  ones from Portainer, and can start, stop and restart them.
- **Stacks from a folder** (started with `docker compose up` in a folder on the server) can be adopted in the
  **Stacks** tab with **Übernehmen** (adopt). After that you can apply, stop and take them down there.
- **Stacks created by Portainer itself** live in Portainer's own data volume. Nodvard Dock shows them with the badge
  "Portainer". **Von Portainer übernehmen** (move from Portainer) fetches the file and variables from that volume, puts
  them in a folder of their own (default `/opt/stacks/<name>`) and redeploys the stack under the same name; the
  volumes stay the same. If the file points to data at Portainer with relative paths, consists of several files
  (for example from Git) or does not match the running services, Nodvard Dock does not move it and says why, for
  relative paths also where the data is.
- **Afterwards, do not delete the stack in Portainer** and do not redeploy it there: it has the same name, and
  Portainer would remove the new containers or replace them with its old file. Just leave the entry as it is.
- **Turning Portainer off:** only once all stacks have moved. Stopping the Portainer container leaves your other
  containers running.

### Permissions, honestly

- **No Docker socket in the container.** Whoever has the socket is root on the machine, and that would be anyone who
  gets into the user interface. So Nodvard Dock talks to every server over SSH with its own user, including its own
  machine. That works the same way for remote servers.
- **The `docker` group is practically root.** Whoever controls Docker can mount any folder of the machine into a
  container. This is true for every tool of this kind, Portainer included.
- **SSH keys are stored encrypted** in Nodvard Dock's vault. Whoever can use Nodvard Dock can control your servers.
  So: a strong password, two-factor on, and do not expose Nodvard Dock to the internet.
- **Risky actions need approval:** deleting, stopping and updating are actions with a risk level. Roles such as
  administrator, operator and viewer decide who may do what.
- **Without the `docker` group:** an "operate only" mode, which allows only start, stop and restart through a
  dedicated helper script, is in the works.

### Build it yourself

In a clone of the repository, preferably on a PC (on a Raspberry Pi the build takes very long):

```bash
docker build -f deploy/Dockerfile --build-arg NODVARD_DECK_EXTENSIONS=docker -t nodvard-dock:selbst .
```

Then change the `image:` line in `compose.yml` to `nodvard-dock:selbst` and run `docker compose up -d`.

### License

Same as Nodvard Deck: **PolyForm Noncommercial License 1.0.0**, see [LICENSE](../../LICENSE). In short (only the
license text is binding): free for personal and other non-commercial use. Companies and paid services need separate
permission. Inquiries: **kontakt@nodvard.com**. The source code is open to read, but Nodvard Dock is not open source
software in the strict sense.

Docker and Portainer are trademarks of their respective owners. Nodvard Dock is not affiliated with them.
