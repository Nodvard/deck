# Nodvard Dock 0.11.1 (Beta) – Container-Verwaltung für Docker, über SSH und mit Freigabe

*English version: [below](#english).*

## Deutsch

**Nodvard Dock** verwaltet die Container auf deinen Servern in einer Oberfläche. Es ist Nodvard Deck mit nur dem Modul
Docker: derselbe Code, nur weniger drin. Das Image gibt es seit 0.11.0, mit dieser Version stellen wir es offiziell
vor.

### Für wen

- Für alle, die ein paar Server mit Docker im Blick behalten wollen, ohne jedes Mal per SSH draufzugehen.
- Für alle, die von Portainer CE umsteigen wollen. Portainer 3.0 erscheint ohne Community Edition. CE bleibt auf der
  2.x-Linie ([Portainer, 11.09.2026](https://www.portainer.io/blog/portainer-3-0-is-coming)). Beide können eine Zeit
  lang nebeneinander laufen. Stacks aus einem Ordner übernimmst du direkt, Stacks, die Portainer selbst angelegt hat,
  zieht Nodvard Dock in einen eigenen Ordner um. Zeigt die Datei mit relativen Pfaden auf Daten bei Portainer, besteht
  sie aus mehreren Dateien oder passt sie nicht zu den laufenden Diensten, zieht es nicht um und nennt den Grund.

### Was es kann

- Container aller Server auf einer Seite: starten, stoppen, neu starten, Logs, Konsole, Details.
- Image-Updates finden und bei Compose-Diensten einspielen. Das alte Image bleibt als Sicherung, und stürzt der neue Container beim Start
  ab, geht es von selbst zurück, außer bei Datenbanken und anderen Datenspeichern.
- Stacks übernehmen, als Text oder im Formular bearbeiten, neu anlegen (auch aus Vorlagen) und als Ganzes
  aktualisieren, auf Wunsch mit Sicherung der Volumes vorher.
- Images, Volumes und Netze auflisten und einzeln löschen, unbenutzte Images und den Build-Cache auf einmal
  aufräumen. Private Registries mit dem Passwort im Tresor.
- Jede Änderung ist eine Aktion mit Risikostufe. Was ohne Rückfrage durchläuft, bestimmt der Inhaber, und alles steht
  im Protokoll.

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

Den Einrichtungscode für den ersten Schritt zeigt `docker compose logs nodvard-dock`. Wie du danach den ersten Server
anbindest, steht im README.

### Grenzen

- **Beta.** Die Oberfläche gibt es bisher nur auf Deutsch, und an einigen Stellen heißt sie noch „Nodvard Deck“.
- **Docker auf einzelnen Servern.** Stacks sind Docker-Compose-Projekte; Swarm und Kubernetes unterstützt Nodvard Dock
  nicht.
- **Server werden über SSH angebunden,** auch der eigene Rechner; einen Docker-Socket bekommt Nodvard Dock nicht. Auf
  dem Server braucht es einen Benutzer in der Gruppe `docker`, das heißt praktisch root. Wo dir das zu viel ist, gibt
  es den Modus „Nur bedienen“ mit weniger Funktionen.
- **Wer Nodvard Dock bedienen darf, kann deine Server steuern.** Darum: starkes Passwort, Zwei-Faktor an und Nodvard
  Dock nicht offen ins Internet stellen.

### Neu in 0.11.1

Sicherheits- und Fehlerkorrekturen aus Nodvard Deck 0.11.1, darunter engere Rechte beim Einrichten von Servern. Für
das Modul Docker vor allem: Das Zurückspielen einer
Volume-Sicherung prüft, dass das Archiv noch dasselbe ist, und startet nach einem Abbruch mitten im Tausch nicht mit
halb getauschten Daten. Der Umzug von Portainer prüft direkt vor dem Neu-Ausrollen noch einmal die Images. Die
Registry-Liste zeigt „unbekannt“ statt fälschlich „angemeldet“, wenn ein Server ein eigenes Hilfsprogramm für
Anmeldungen nutzt. Alle Punkte stehen in den Release-Notes von Nodvard Deck 0.11.1.

### Lizenz

PolyForm Noncommercial License 1.0.0: privat und für andere nicht-kommerzielle Zwecke frei nutzbar. Der Quellcode ist
einsehbar, Nodvard Dock ist aber keine Open-Source-Software im engeren Sinn. Firmen und bezahlte Dienste brauchen eine
gesonderte Erlaubnis: kontakt@nodvard.com.

---

## English

**Nodvard Dock** manages the containers on your servers in one user interface. It is Nodvard Deck with only the Docker
module: the same code, just less of it. The image exists since 0.11.0; with this release we officially introduce it.

### Who it is for

- Anyone who wants to keep an eye on a few servers running Docker without logging in over SSH every time.
- Anyone who wants to move away from Portainer CE. Portainer 3.0 ships without a Community Edition. CE stays on the 2.x
  line ([Portainer, 11 Sep 2026](https://www.portainer.io/blog/portainer-3-0-is-coming)). Both can run side by side for
  a while. Stacks from a folder are adopted directly; stacks that Portainer created itself are moved into a folder of
  their own. If the file points to data at Portainer with relative paths, consists of several files or does not match
  the running services, Nodvard Dock does not move it and says why.

### What it does

- Containers of all servers on one page: start, stop, restart, logs, console, details.
- Find image updates and apply them to Compose services. The old image stays as a backup, and if the new container crashes on start, it goes
  back on its own, except for databases and other data stores.
- Adopt stacks, edit them as text or in a form, create new ones (also from templates) and update them as a whole,
  optionally backing up the volumes first.
- List images, volumes and networks and delete them one by one; clean up unused images and the build cache in one go.
  Private registries with the password in the vault.
- Every change is an action with a risk level. The owner decides what runs without asking, and everything is logged.

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

`docker compose logs nodvard-dock` shows the setup code for the first step. How to connect your first server is
described in the README.

### Limits

- **Beta.** The user interface is in German only for now, and in some places it is still called "Nodvard Deck".
- **Docker on single servers.** Stacks are Docker Compose projects; Nodvard Dock does not support Swarm or Kubernetes.
- **Servers are connected over SSH,** including the machine Nodvard Dock runs on; it gets no Docker socket. On the
  server it needs a user in the `docker` group, which is practically root. Where that is too much, the "operate only"
  mode offers fewer functions.
- **Whoever can use Nodvard Dock can control your servers.** So: a strong password, two-factor on, and do not expose
  Nodvard Dock to the internet.

### New in 0.11.1

Security and bug fixes from Nodvard Deck 0.11.1, including narrower rights when setting up servers. For the Docker
module mainly: restoring a volume backup checks that the archive is
still the same one, and after a cancellation in the middle of the swap it does not start with half-swapped data. Moving
from Portainer checks the images once more right before redeploying. The registry list shows "unknown" instead of
wrongly "logged in" when a server stores its logins through a helper program. All items are in the release notes of
Nodvard Deck 0.11.1.

### License

PolyForm Noncommercial License 1.0.0: free for personal and other non-commercial use. The source code is open to read,
but Nodvard Dock is not open source software in the strict sense. Companies and paid services need separate
permission: kontakt@nodvard.com.
