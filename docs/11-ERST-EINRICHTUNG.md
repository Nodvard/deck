# Erst-Einrichtung – Schritt für Schritt

Stand 07.10.2026 (Version 0.7). Für eine frische Installation, in dieser Reihenfolge. Wo Nodvard Deck noch
eine Lücke hat, steht das dabei. Ohne Oberfläche (nur mit der API) geht es über den
[Anhang](#anhang-ohne-oberfläche).

Im Dashboard öffnet **„Anleitung“** diese Seite in einem neuen Tab: unten in der Navigation der Einstellungen und über
die Suche (Strg+K, „Anleitung“, „Hilfe“ oder „Doku“). An vielen Stellen steht ein Link direkt am passenden Abschnitt.

1. [Container starten](#1-container-starten)
2. [Einrichtungsassistent](#2-einrichtungsassistent-setup)
3. [Weitere Benutzer](#3-weitere-benutzer)
4. [Erweiterungen einschalten](#4-erweiterungen-einschalten)
5. [Server und SSH-Zugang](#5-server-und-ssh-zugang)
6. [Root-Rechte ohne Passwort und Gruppe docker](#6-root-rechte-ohne-passwort-und-gruppe-docker)
7. [Proxmox verbinden](#7-proxmox-verbinden) (nur mit Proxmox)
8. [Push-Nachrichten (ntfy)](#8-push-nachrichten-ntfy)
9. [Automatik und Wartungsfenster](#9-automatik-und-wartungsfenster)
10. [Das Dashboard selbst sichern](#10-das-dashboard-selbst-sichern)
11. [Wenn etwas hakt](#11-wenn-etwas-hakt)
12. [Ausgesperrt? Notfall-Befehle](#12-ausgesperrt-notfall-befehle)
13. [Nodvard Deck startet nicht: die Notseite](#13-nodvard-deck-startet-nicht-die-notseite)
14. [Anhang: Ohne Oberfläche](#anhang-ohne-oberfläche)

## 1. Container starten

### 1.1 Installation ohne das Repo

Wer nur Docker hat und das Repository nicht kennt, braucht eine einzige Datei:
[`deploy/compose.standalone.yml`](../deploy/compose.standalone.yml). Das fertige Image kommt aus der
GitHub-Container-Registry (`ghcr.io/nodvard/deck`, für x86-Rechner und Raspberry Pi), es wird nichts
gebaut. Unten stehen die Wege nach Art sortiert: im Terminal, mit einer Web-Oberfläche, auf einem NAS und
auf bestimmten Geräten. Such dir einen aus und klapp ihn auf.

#### Gut zu wissen, egal welcher Weg

- **Erster Start:** Das Image (knapp 400 MB) wird geladen, danach legt der Container die Datenbank an.
  Oberflächen wie Portainer oder der Container-Bereich eines NAS zeigen ihn in dieser Zeit als „starting“
  oder noch nicht als „healthy“. Bis zu fünf Minuten ist das normal. Steht danach „unhealthy“, findest du
  den Grund im Protokoll.
- **Port:** Nodvard Deck antwortet im Container auf Port 8080. Ist 8080 auf dem Gerät schon belegt, änderst
  du nur die Zahl **links** vom Doppelpunkt (`"8081:8080"`), rechts bleibt immer `8080`. Auf manchen NAS ist
  8080 von Haus aus vergeben; die Wege unten sagen, wo.
- **Daten:** Alles liegt im Docker-Speicher (Volume) `nodvard-deck-data`. Das ist auch auf einem NAS der
  empfohlene Weg, weil er keine Rechte-Einstellungen braucht. Lieber ein Ordner (`<ordner>:/app/data`)? Dann
  macht der Container beim Start den Benutzer mit der Nummer **1000** zum Besitzer dieses Ordners und läuft
  danach als dieser Benutzer. Die Nummer muss zu keinem Benutzer des NAS gehören. Auf einem normalen
  Laufwerk klappt das ohne dein Zutun. Auf NAS-Freigaben mit eigenen Zugriffsregeln (ACL) oder auf einem
  Netzlaufwerk kann es scheitern: Dann steht im Protokoll „Die Besitzrechte an /app/data liessen sich nicht
  setzen“, und der Benutzer 1000 braucht Schreibrechte auf den Ordner (Hinweise je NAS unten). Klappt es,
  können danach nur noch der Benutzer 1000 und root den Ordner lesen; Dateimanager oder
  Sicherungsprogramme, die als anderer Benutzer laufen, kommen nicht mehr an die Dateien. Sichere Nodvard
  Deck deshalb mit der eingebauten Sicherung ([Abschnitt 10](#10-das-dashboard-selbst-sichern)).
- **Einrichtungscode:** Er steht im Protokoll (Log) des Containers, in einem Rahmen aus `=`-Zeichen. Bei jedem
  Weg steht, wo du das Protokoll findest.

#### Kurze Fassung für Oberflächen

Die Datei `compose.standalone.yml` liest den Port aus einer Variablen
(`${NODVARD_DECK_PORT:-${LATTICE_PORT:-8080}}`). Nicht jede Oberfläche versteht diese verschachtelte
Schreibweise. Für die NAS-Wege gibt es deshalb diese Fassung ohne Variablen; den Port änderst du direkt in
der Zeile unter `ports:`:

```yaml
services:
  nodvard-deck:
    image: ghcr.io/nodvard/deck:latest
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - nodvard_deck_data:/app/data

volumes:
  nodvard_deck_data:
    name: nodvard-deck-data
```

Der Gesundheits-Check steckt im Image selbst, er fehlt hier also nicht.

#### Im Terminal

<details>
<summary><b>Docker Compose im Terminal</b></summary>

Die Datei als `compose.yml` in einen leeren Ordner laden und dort starten:

```bash
mkdir ~/nodvard-deck && cd ~/nodvard-deck
curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml
docker compose up -d
```

Unter Windows sind das Befehle für eine Linux-Shell (auch WSL oder Git Bash); in PowerShell `curl.exe`
statt `curl` schreiben und die Befehle einzeln ausführen.

Kontrolle danach, auf demselben Rechner:

```bash
curl -s http://localhost:8080/api/v1/health      # erwartet: "status":"ok"
```

- **Öffnen:** `http://<Adresse-des-Rechners>:8080`, z. B. **http://192.168.1.10:8080**.
- **Port schon belegt?** (Meldung „port is already allocated“ oder „address already in use“): Neben die
  `compose.yml` eine Datei `.env` mit der Zeile `NODVARD_DECK_PORT=8081` legen und `docker compose up -d`
  noch einmal ausführen. Die Adresse ist dann `…:8081`, auch bei der Kontrolle oben.
- **Einrichtungscode:** `docker compose logs nodvard-deck | grep -A1 Einrichtungscode` im selben
  Ordner (mehr dazu in [Abschnitt 2](#2-einrichtungsassistent-setup)).
- **Aktualisieren:** im selben Ordner `docker compose pull && docker compose up -d`. Die Daten bleiben
  erhalten.

</details>

<details>
<summary><b>Ein einzelner <code>docker run</code>-Befehl (ohne Compose)</b></summary>

```bash
docker run -d --name nodvard-deck --restart unless-stopped \
  -p 8080:8080 \
  -v nodvard-deck-data:/app/data \
  --log-opt max-size=10m --log-opt max-file=3 \
  ghcr.io/nodvard/deck:latest
```

Der Speicher heißt wie beim Compose-Weg `nodvard-deck-data`; der Gesundheits-Check steckt im Image.

- **Öffnen:** `http://<Adresse-des-Rechners>:8080`. Kontrolle auf demselben Rechner:
  `curl -s http://localhost:8080/api/v1/health` zeigt `"status":"ok"`.
- **Port schon belegt?** Im Befehl `-p 8081:8080` schreiben. Läuft der Container schon, erst
  `docker stop nodvard-deck && docker rm nodvard-deck`, dann den Befehl neu ausführen; die Daten bleiben im
  Speicher.
- **Einrichtungscode:** `docker logs nodvard-deck 2>&1 | grep -A1 Einrichtungscode`
- **Aktualisieren:**

  ```bash
  docker pull ghcr.io/nodvard/deck:latest
  docker stop nodvard-deck && docker rm nodvard-deck
  ```

  und danach denselben `docker run`-Befehl wie oben. Die Daten bleiben erhalten.
- **Befehle aus dieser Anleitung:** Wo `docker compose logs nodvard-deck` steht, nimmst du
  `docker logs nodvard-deck`; aus `docker compose exec nodvard-deck …` wird `docker exec nodvard-deck …`.

</details>

<details>
<summary><b>Podman</b></summary>

Podman braucht kein Compose. Der Befehl ist derselbe wie bei Docker:

```bash
podman run -d --name nodvard-deck --restart always \
  -p 8080:8080 \
  -v nodvard-deck-data:/app/data \
  ghcr.io/nodvard/deck:latest
```

- **Ohne root (rootless):** geht. Ports unter 1024 darf ein normaler Benutzer nicht öffnen, 8080 ist also
  kein Problem.
- **Nach einem Neustart des Rechners:** Podman startet Container nicht von selbst neu, das übernimmt der Dienst
  `podman-restart.service`, und zwar zuverlässig nur für Container mit `--restart always` (deshalb steht das oben
  statt `unless-stopped`). Einmal einschalten, mit root:
  `sudo systemctl enable --now podman-restart.service`; ohne root als dein Benutzer:
  `systemctl --user enable --now podman-restart.service` und zusätzlich `loginctl enable-linger $USER`,
  damit der Dienst auch ohne Anmeldung startet.
- **Mit Compose:** `podman compose up -d` im Ordner mit der `compose.yml` aus dem Terminal-Weg. Das klappt nur,
  wenn ein Compose-Programm installiert ist (`docker-compose` oder `podman-compose`); `podman compose` ruft es
  nur auf. In der `compose.yml` dann `restart: always` statt `restart: unless-stopped` eintragen, sonst startet
  der Container nach einem Neustart des Rechners nicht von selbst. Meldet es einen Fehler in der Zeile `ports`,
  nimm die [kurze Fassung](#kurze-fassung-für-oberflächen).
- **Ein Ordner statt Speicher** auf einem System mit SELinux (Fedora, RHEL und Verwandte): an die Zeile ein
  `:Z` hängen, z. B. `-v ./daten:/app/data:Z`.
- **Öffnen:** `http://<Adresse-des-Rechners>:8080`
- **Port schon belegt?** Im Befehl `-p 8081:8080` schreiben (bestehenden Container vorher mit
  `podman stop nodvard-deck && podman rm nodvard-deck` entfernen; die Daten bleiben).
- **Einrichtungscode:** `podman logs nodvard-deck 2>&1 | grep -A1 Einrichtungscode`
- **Aktualisieren:** `podman pull ghcr.io/nodvard/deck:latest`, dann
  `podman stop nodvard-deck && podman rm nodvard-deck` und den `podman run`-Befehl von oben noch einmal.
- **Befehle aus dieser Anleitung:** `docker` durch `podman` ersetzen, aus `docker compose exec nodvard-deck …`
  wird `podman exec nodvard-deck …`.

</details>

#### Mit einer Web-Oberfläche

<details>
<summary><b>Portainer, Weg 1: aus dem Repository</b></summary>

1. **Stacks → Add stack**, Name z. B. `nodvard-deck` (nur Kleinbuchstaben, Ziffern, `-` und `_`).
2. Als Aufbau **Repository** wählen und eintragen:
   - **Repository URL:** `https://github.com/nodvard/deck`
   - **Repository reference:** `refs/heads/main`
   - **Compose path:** `deploy/compose.standalone.yml`

   Eine Anmeldung brauchst du nicht, das Repository ist öffentlich.
3. **Deploy the stack**.

- **Öffnen:** `http://<Adresse-des-Rechners>:8080`. Zur Kontrolle zeigt
  `http://<Adresse-des-Rechners>:8080/api/v1/health` im Browser `"status":"ok"`.
- **Port schon belegt?** Unter **Environment variables** die Variable `NODVARD_DECK_PORT` mit z. B. `8081`
  anlegen, beim neuen Stack vor „Deploy the stack“, beim bestehenden danach neu ausrollen
  („Pull and redeploy“). Die Adresse ist dann `…:8081`.
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

1. Die Datei [`deploy/compose.standalone.yml`](../deploy/compose.standalone.yml) öffnen
   ([als reiner Text](https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml))
   und ihren **ganzen Inhalt** kopieren. Er beginnt mit `# Nodvard Deck ohne das Repository installieren`
   und enthält weiter unten `services:`.
2. **Stacks → Add stack**, Name z. B. `nodvard-deck`, als Aufbau **Web editor**, den Inhalt einfügen,
   **Deploy the stack**.

**Nicht** die drei Befehle aus dem Terminal-Weg (`mkdir …`, `curl …`, `docker compose up -d`) einfügen.
Das sind Befehle für die Shell, keine Compose-Datei; Portainer meldet dann „top-level object must be a
mapping“.

- **Öffnen:** `http://<Adresse-des-Rechners>:8080`. Zur Kontrolle zeigt
  `http://<Adresse-des-Rechners>:8080/api/v1/health` im Browser `"status":"ok"`.
- **Port schon belegt?** Unter **Environment variables** die Variable `NODVARD_DECK_PORT` mit z. B. `8081`
  anlegen, beim neuen Stack vor „Deploy the stack“, beim bestehenden danach „Update the stack“. Die
  Adresse ist dann `…:8081`.
- **Einrichtungscode:** **Containers** → `nodvard-deck-nodvard-deck-1` (allgemein
  `<Stack-Name>-nodvard-deck-1`) → **Logs**, die Zeile beginnt mit „Einrichtungscode“.
- **Aktualisieren:** Stack öffnen → **Editor** → **Update the stack** mit eingeschaltetem
  **Re-pull image and redeploy**. Am Inhalt im Editor musst du dafür nichts ändern.

</details>

<details>
<summary><b>Dockge</b></summary>

1. Oben **+ Compose**.
2. Bei **Stack Name** z. B. `nodvard-deck` eintragen (nur Kleinbuchstaben).
3. In den Editor den ganzen Inhalt von [`deploy/compose.standalone.yml`](../deploy/compose.standalone.yml)
   einfügen ([als reiner Text](https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml)).
4. **Deploy**.

Dockge legt die Datei als `compose.yaml` in seinen Stacks-Ordner (Vorgabe `/opt/stacks/nodvard-deck/`); du
kannst sie dort auch mit `docker compose` bedienen.

- **Öffnen:** `http://<Adresse-des-Rechners>:8080`
- **Port schon belegt?** Im Stack **Edit**, im Feld **.env** die Zeile `NODVARD_DECK_PORT=8081` eintragen,
  **Deploy**. Die Adresse ist dann `…:8081`.
- **Einrichtungscode:** Auf der Seite des Stacks läuft unter **Terminal** das Protokoll mit, die Zeile beginnt
  mit „Einrichtungscode“.
- **Aktualisieren:** Auf der Seite des Stacks **Update**. Dockge holt das neue Image und startet den Stack neu;
  die Daten bleiben erhalten.

</details>

#### Auf einem NAS

Die Menünamen stehen hier so, wie sie in der englischen Oberfläche und Anleitung des Herstellers heißen. In
einer deutschen Oberfläche sind sie übersetzt.

<details>
<summary><b>Synology (Container Manager, DSM 7.2 oder neuer)</b></summary>

1. Das Paket **Container Manager** installieren (ab DSM 7.2, es ersetzt dort das alte Paket „Docker“).
2. Im Container Manager **Project** → **Create**.
3. **Project Name** z. B. `nodvard-deck`, bei **Path** einen leeren Ordner wählen oder anlegen.
4. Bei **Source** eine neue `docker-compose.yml` anlegen und die [kurze Fassung](#kurze-fassung-für-oberflächen)
   einfügen.
5. Weiter bis **Done** (den Schritt für ein Web-Portal kannst du auslassen).

- **Öffnen:** `http://<Adresse-des-NAS>:8080`. DSM selbst nutzt 5000 und 5001, 8080 ist normalerweise frei.
- **Port schon belegt?** Das Projekt auswählen → **YAML Configurations**, die linke Zahl unter `ports:` ändern
  und das Projekt mit der neuen Einstellung starten.
- **Einrichtungscode:** **Container** → den Container von Nodvard Deck auswählen → **Action** → **Details** →
  **Log**.
- **Aktualisieren:** **Image** → `ghcr.io/nodvard/deck` auswählen → **Action** → **Update**. Ob die neue Version
  läuft, zeigt danach **Einstellungen → Über Nodvard Deck**.
- **Vorsicht mit „Clean“:** Diese Aktion im Projekt entfernt laut Synology auch die Volumes des Projekts, also
  die Daten von Nodvard Deck. Vorher sichern ([Abschnitt 10](#10-das-dashboard-selbst-sichern)).
- **Daten:** im Speicher `nodvard-deck-data`. Lieber ein Ordner, z. B. `/volume1/docker/nodvard-deck:/app/data`?
  Meldet der Container „Die Besitzrechte an /app/data liessen sich nicht setzen“, gib in der File Station nur
  diesem Ordner das Recht **Read & Write** für **Everyone**. So beschreibt es Synology für Container, die
  ihren Ordner nicht beschreiben können. Damit kommen aber alle Benutzer des NAS an den Ordner, und dort
  liegen auch Schlüssel; der Speicher ist deshalb der bessere Weg.

</details>

<details>
<summary><b>UGREEN (UGOS Pro, App „Docker“)</b></summary>

1. Die App **Docker** aus dem **App Center** installieren.
2. In der App **Project** → **Create**.
3. Projektname z. B. `nodvard-deck`, den vorgeschlagenen Speicherpfad lassen.
4. Die [kurze Fassung](#kurze-fassung-für-oberflächen) einfügen → **Deploy**.

- **Öffnen:** `http://<Adresse-des-NAS>:8080`
- **Port schon belegt?** Im Projekt (nicht beim Container, Container eines Projekts lassen sich dort nicht
  bearbeiten) die linke Zahl ändern und wieder **Deploy**.
- **Einrichtungscode:** **Container** → den Container von Nodvard Deck auswählen → **Log**.
- **Aktualisieren:** Gibt es eine neuere Version des Images, zeigt UGOS das in der Projektliste an; dort
  aktualisierst du das Projekt. Die Daten bleiben im Speicher.
- **Daten:** im Speicher `nodvard-deck-data`. Lieber ein Ordner? Der Ordner muss vor **Deploy** existieren,
  sonst bricht UGOS ab. Ein relativer Pfad wie `./daten:/app/data` landet im Speicherpfad des Projekts.

</details>

<details>
<summary><b>QNAP (Container Station 3)</b></summary>

Container Station 3 braucht QTS 5.1.0 bzw. QuTS hero h5.1.0 oder neuer und wird über das **App Center**
installiert.

**Wichtig:** Die Weboberfläche von QTS nutzt selbst Port **8080**. Nimm deshalb einen anderen, freien Port,
z. B. `"8090:8080"` statt `"8080:8080"` in der kurzen Fassung.

1. In Container Station **Applications** → **Create**.
2. Name z. B. `nodvard-deck` (nur `a`–`z`, `0`–`9`, `-` und `_`, höchstens 32 Zeichen).
3. Die [kurze Fassung](#kurze-fassung-für-oberflächen) mit dem geänderten Port einfügen, optional
   **Validate**, dann **Create**.

- **Öffnen:** `http://<Adresse-des-NAS>:8090` (also mit dem Port, den du links eingetragen hast).
- **Port ändern:** **Applications** → die App → Menü neben **Edit** → **Recreate**, die linke Zahl ändern,
  **Update**. Beim Neuerstellen bleibt nur, was in Volumes oder Ordnern liegt; die Daten von Nodvard Deck
  liegen im Speicher und bleiben also.
- **Einrichtungscode:** **Containers** → den Container von Nodvard Deck öffnen → Reiter **Logs** (nicht „Event
  Logs“, das ist das Protokoll von Container Station selbst).
- **Aktualisieren:** **Images** → **Pull**, `ghcr.io/nodvard/deck:latest` eintragen. Danach wie beim Port:
  **Recreate** → **Update**.
- **Daten:** im Speicher `nodvard-deck-data`. Lieber ein Ordner (`/share/<Freigabe>/…`)? Laut QNAP können die
  **Advanced Folder Permissions** (bei den **Shared Folders**) Container stören. Erscheint die Warnung zu den
  Besitzrechten, dort nachsehen.

</details>

<details>
<summary><b>Unraid</b></summary>

Unraid bringt Docker mit, aber kein Docker Compose. Der einfachste Weg ist eine eigene Vorlage:

1. Reiter **Docker** → ganz unten **Add Container**.
2. **Name** `nodvard-deck`, **Repository** `ghcr.io/nodvard/deck:latest`, **Network Type** `Bridge`.
3. **Add another Path, Port, Variable, Label or Device** → **Config Type** `Port`, **Container Port** `8080`,
   **Host Port** `8080` → **Add**.
4. Noch einmal **Add another Path, Port, Variable, Label or Device** → **Config Type** `Path`, **Container Path**
   `/app/data`, **Host Path** `/mnt/user/appdata/nodvard-deck` → **Add**.
5. **Apply**.

- **Öffnen:** `http://<Adresse-des-Servers>:8080`
- **Port schon belegt?** Auf das Symbol des Containers klicken → **Edit**, nur den **Host Port** ändern (der
  Container Port bleibt 8080) → **Apply**.
- **Einrichtungscode:** Symbol des Containers → **Logs**.
- **Aktualisieren:** oben rechts auf **Advanced View** umschalten, beim Container **force update**.
- **Daten:** im Ordner `/mnt/user/appdata/nodvard-deck`. Der Container macht beim Start den Benutzer 1000 zum
  Besitzer. Setzt ein Rechte-Werkzeug von Unraid die Besitzer später zurück, richtet der Container das beim
  nächsten Start wieder ein.
- **Lieber mit Compose?** Das Plugin **Compose Manager Plus** (im Reiter **Apps** suchen) bringt Docker
  Compose mit. Dort unter **Docker** → **Compose** mit **Add Stack** einen Stack anlegen, die
  [kurze Fassung](#kurze-fassung-für-oberflächen) einfügen und im Menü des Stacks **Compose Up**; das Protokoll
  steht unter **View Logs**. Aktualisiert wird mit **Update**; der Knopf erscheint nur, wenn es eine neuere
  Version gibt, sonst heißt er **Force Update**.

</details>

<details>
<summary><b>TrueNAS SCALE (ab 24.10)</b></summary>

1. **Apps** → **Discover Apps** → Menü ⋮ → **Install via YAML**.
2. **Name** `nodvard-deck` (Kleinbuchstaben, Ziffern und `-`, beginnt mit einem Buchstaben).
3. Bei **Custom Config** die [kurze Fassung](#kurze-fassung-für-oberflächen) einfügen → **Save**.

TrueNAS prüft dabei nur die YAML-Schreibweise, nicht den Inhalt. Nimm hier die kurze Fassung, nicht die
Datei mit Variablen.

- **Öffnen:** `http://<Adresse-des-NAS>:8080`. TrueNAS selbst nutzt 80 und 443.
- **Port schon belegt?** **Apps** → **Installed** → die App → **Edit**, die linke Zahl ändern, speichern.
- **Einrichtungscode:** **Apps** → **Installed** → die App → in der Kachel **Workloads** **View Logs**.
- **Aktualisieren:** Steht bei der App **Update available**, im Menü ⋮ der Kachel **Application Info** auf
  **Update**.
- **Daten:** im Speicher `nodvard-deck-data`, der im versteckten Datensatz `ix-apps` des App-Pools liegt.
  TrueNAS empfiehlt für App-Daten eigene Datensätze; das geht mit der Zeile
  `- /mnt/<Pool>/<Datensatz>:/app/data` statt `- nodvard_deck_data:/app/data`. Erscheint dann die Warnung zu
  den Besitzrechten, unter **Datasets** → Datensatz → Kachel **Permissions** → **Edit** dem Benutzer mit der
  Nummer 1000 Schreibrechte geben.

</details>

<details>
<summary><b>OpenMediaVault (Plugin „compose“)</b></summary>

Das Plugin kommt von **omv-extras**. omv-extras, das Plugin `openmediavault-compose` und Docker richtest du
nach der [Anleitung von omv-extras](https://wiki.omv-extras.org/) ein. Danach:

1. **Services** → **Compose** → **Settings**: Bei **Compose Files** einen **Shared folder** wählen (dort legt das
   Plugin die Dateien ab) und speichern.
2. **Services** → **Compose** → **Files** → **Add** → **Add**: **Name** `nodvard-deck`, bei **File** die
   [kurze Fassung](#kurze-fassung-für-oberflächen) einfügen, **Save**.
3. Die Datei in der Liste auswählen → **up**.

- **Öffnen:** `http://<Adresse-des-NAS>:8080`
- **Port schon belegt?** Die Datei auswählen → **Edit**, die linke Zahl ändern, **Save**, dann **up**.
- **Einrichtungscode:** Die Datei auswählen → **Tools** (Zahnrad) → **logs**.
- **Aktualisieren:** Die Datei auswählen → **pull**, danach **up**.
- **Daten:** im Speicher `nodvard-deck-data`, also im Docker-Speicher des NAS (Vorgabe `/var/lib/docker`,
  einstellbar unter **Settings** → **Docker storage**).

</details>

<details>
<summary><b>ZimaOS und CasaOS</b></summary>

Beim Import macht die Oberfläche aus einem benannten Speicher eine Ordner-Freigabe und kommt mit der Angabe
unter `volumes:` nicht zurecht. Hier deshalb diese Fassung mit festem Ordner:

```yaml
name: nodvard-deck
services:
  nodvard-deck:
    image: ghcr.io/nodvard/deck:latest
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - /DATA/AppData/nodvard-deck:/app/data
```

1. Im Bereich **Apps** auf **+** → **Install a customized app**.
2. Oben auf **Import**, Reiter **Docker Compose**, die Fassung oben einfügen → **Submit**.
3. Die übernommenen Angaben prüfen → **Install**.

- **Öffnen:** `http://<Adresse-des-Geräts>:8080`
- **Port schon belegt?** Im Menü der App **Settings**, die linke Portzahl ändern, **Save**.
- **Einrichtungscode:** Im Menü der App **Settings** → oben **Terminal and Logs** → Reiter **Logs**.
- **Aktualisieren:** Im Menü der App **Check then update**.
- **Daten:** im Ordner `/DATA/AppData/nodvard-deck`. Der Container macht beim Start den Benutzer 1000 zum
  Besitzer.

</details>

#### Raspberry Pi, Proxmox und Docker Desktop

<details>
<summary><b>Raspberry Pi</b></summary>

- Ein Raspberry Pi 4 oder 5 mit 4 GB RAM und einem **64-Bit-System** (`uname -m` zeigt `aarch64`); das
  Image gibt es für `linux/arm64`. Im Leerlauf braucht Nodvard Deck rund 100 MB Arbeitsspeicher, das Image
  ist knapp 400 MB groß. Docker mit Docker Compose muss installiert sein.
- Eine SD-Karte kann sterben: Sicherungen nicht nur auf dem Pi lassen ([Abschnitt 10](#10-das-dashboard-selbst-sichern));
  eine USB-SSD hält länger als eine SD-Karte.
- Läuft auf dem Pi Portainer, gelten die beiden Portainer-Wege oben genauso.

```bash
mkdir ~/nodvard-deck && cd ~/nodvard-deck
curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml
sudo docker compose up -d
curl -s http://localhost:8080/api/v1/health      # erwartet: "status":"ok"
```

Ist dein Benutzer in der Gruppe `docker`, lässt du `sudo` weg. Der erste Start dauert etwas, weil das
Image erst geladen wird.

- **Öffnen:** `http://<Adresse-des-Pi>:8080`
- **Port schon belegt?** Neben die `compose.yml` eine Datei `.env` mit der Zeile `NODVARD_DECK_PORT=8081`
  legen und `sudo docker compose up -d` noch einmal ausführen. Die Adresse ist dann `…:8081`.
- **Einrichtungscode:** `sudo docker compose logs nodvard-deck | grep -A1 Einrichtungscode` im selben
  Ordner.
- **Aktualisieren:** im selben Ordner `sudo docker compose pull && sudo docker compose up -d`. Die Daten
  bleiben erhalten.

</details>

<details>
<summary><b>Proxmox (VM oder LXC-Container)</b></summary>

Docker läuft nicht direkt auf dem Proxmox-Host, sondern in einer VM oder in einem LXC-Container. Darin
installierst du Docker ([Anleitung von Docker für Debian](https://docs.docker.com/engine/install/debian/)) und
folgst dann dem Weg **Docker Compose im Terminal** oben.

- **VM (der sichere Weg):** Proxmox selbst empfiehlt für Anwendungs-Container wie Docker eine VM, wenn es auf
  Abschottung und Live-Migration ankommt. Eine kleine Debian-VM reicht, Nodvard Deck selbst braucht im Leerlauf
  rund 100 MB Arbeitsspeicher.
- **LXC-Container:** sparsamer, braucht aber zwei Schalter. Docker läuft in einem **unprivilegierten**
  Container (die Vorgabe beim Anlegen) nur mit **Nesting** und **keyctl**:
  - **Nesting** ist beim Anlegen über **Erstelle CT** schon angehakt.
  - **keyctl** schaltest du danach ein: Container auswählen → **Optionen** → **Features** → **keyctl** anhaken.
  - Oder in der Shell des Proxmox-Hosts: `pct set <CT-ID> --features nesting=1,keyctl=1`
  - Danach den Container neu starten.
- **Öffnen, Port, Einrichtungscode, Aktualisieren:** wie beim Terminal-Weg, in der VM bzw. im Container (in der
  **Konsole** des Containers oder per SSH). Die Adresse ist die der VM bzw. des Containers, nicht die des
  Proxmox-Hosts.

</details>

<details>
<summary><b>Docker Desktop (Windows, macOS) zum Ausprobieren</b></summary>

Für einen Test auf dem eigenen Rechner. Für den Dauerbetrieb ist ein Rechner besser, der immer läuft (NAS,
Raspberry Pi, kleiner Server).

1. [Docker Desktop](https://docs.docker.com/desktop/) installieren und starten.
2. Ein Terminal öffnen (Windows: PowerShell, macOS: Terminal) und:

   ```bash
   mkdir nodvard-deck
   cd nodvard-deck
   curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml
   docker compose up -d
   ```

   In PowerShell `curl.exe` statt `curl` schreiben.

- **Öffnen:** **http://localhost:8080**
- **Port schon belegt?** Wie beim Terminal-Weg: Datei `.env` mit `NODVARD_DECK_PORT=8081` neben die
  `compose.yml`, `docker compose up -d` noch einmal.
- **Einrichtungscode:** In Docker Desktop links **Containers** → den Container von Nodvard Deck auswählen →
  Reiter **Logs**.
- **Aktualisieren:** im selben Ordner `docker compose pull` und `docker compose up -d`.

</details>

Die Datenbank wird beim Containerstart automatisch angelegt bzw. aktualisiert, dafür ist nichts
zu tun. Den Einrichtungscode (nächster Abschnitt) zeigt der Assistent mit einer Anleitung, wo er steht
(Befehlszeile, Docker Desktop, Portainer, Synology, Unraid).

**Alle weiteren Befehle in dieser Anleitung** (Einrichtungscode, Protokoll, Notfall-Befehle,
Wiederherstellen) laufen im Ordner mit der `compose.yml`, hier also nach `cd ~/nodvard-deck`.
Die Beispiele schreiben `sudo docker …`; ist dein Benutzer in der Gruppe `docker` (oder nutzt du
Docker Desktop), lässt du `sudo` weg. **Mit Portainer oder einer NAS-Oberfläche** gibt es diesen Ordner
nicht: Protokoll und Neustart findest du beim Container in der Oberfläche, Befehle wie die Notfall-Befehle
gibst du in seiner Konsole ein ([Abschnitt 12](#12-ausgesperrt-notfall-befehle)). Für `docker run` und
Podman steht beim jeweiligen Weg, wie die Befehle dann heißen.

### 1.2 Aus dem Repository bauen (für Entwickler)

Wer das Repository klont und das Image selbst baut, startet im Ordner `deploy/` des Repositorys
(`docker compose build --pull && docker compose up -d`; `--pull` holt frische Basis-Images). Bauen, auf einen Raspberry Pi ausliefern
(`scripts/deploy_pi.sh`), Rückfallweg und Rollback stehen in
[deploy/README.md](../deploy/README.md). Die Kontrolle danach ist dieselbe wie oben
(`curl -s http://localhost:8080/api/v1/health`).

Bei dieser Installation laufen die Befehle aus dieser Anleitung im Ordner `deploy/` des
Repositorys (z. B. `cd ~/deck/deploy`) statt in `~/nodvard-deck`. Abschnitte, die nur für diesen
Weg gelten, sind entsprechend markiert.

## 2. Einrichtungsassistent (/setup)

Solange es noch keinen Benutzer gibt, leitet die Anmeldeseite automatisch auf `/setup` um.

**Du hast schon eine Sicherung?** Im ersten Schritt gibt es **„Oder: Sicherung einspielen“**: Datei hochladen,
Passwort eingeben, Zusammenfassung prüfen, einspielen – danach meldest du dich mit dem Konto aus der Sicherung an
und musst nichts neu einrichten ([10.2](#102-wiederherstellen-sicherung-einspielen)). Auch dort wird der
Einrichtungscode (unten) verlangt.

**Einrichtungscode:** Damit nicht jeder im Netz, der die Seite zuerst öffnet, das erste Konto
(und damit den Inhaber) bekommt, verlangt `/setup` einen Code. Nodvard Deck würfelt ihn beim Start
und schreibt ihn ins Protokoll – bei **jedem** Start, bis das erste Konto angelegt ist:

```bash
cd ~/nodvard-deck && sudo docker compose logs nodvard-deck | grep -A1 Einrichtungscode
# Einrichtungscode: K7MQ-X2VD-H9PA – im Browser eingeben
```

- Groß-/Kleinschreibung, Striche und Leerzeichen sind egal.
- Der Code bleibt über Neustarts gleich (er liegt als `setup_code.txt` im Datenverzeichnis,
  nur für Nodvard Deck lesbar) und wird nach dem Anlegen des Kontos gelöscht.
- Falsch eingegebene Codes werden wie falsche Passwörter gebremst (nach 10 Fehlversuchen
  einige Minuten Pause) und stehen im Protokoll als „Einrichtungscode falsch“.
- Wer den Code lieber selbst festlegt (mindestens 8 Zeichen), trägt ihn in der Compose-Datei als
  **`NODVARD_DECK_SETUP_CODE: "…"`** unter `environment:` ein. Die `compose.yml` aus
  [1.1](#11-installation-ohne-das-repo) hat noch keinen `environment:`-Block; du ergänzt ihn
  unter `nodvard-deck:` (gleiche Einrückung wie `image:`) und übernimmst ihn mit
  `docker compose up -d`:

  ```yaml
      environment:
        NODVARD_DECK_SETUP_CODE: "mein-eigener-code"
  ```

  In `deploy/docker-compose.yml` des Repositorys gibt es den Block schon. Ohne diese Einstellung
  würfelt Nodvard Deck selbst.
- Bestehende Installationen mit Konto merken davon nichts.

Im Protokoll steht der Code zusätzlich groß und allein in einer Zeile (zwischen Leerzeilen, in einem
Rahmen aus `=`-Zeichen). Im Assistenten klappt „Wo finde ich den Code?“ eine Anleitung auf.

Der Assistent hat **sechs Schritte** und zeigt oben „Schritt X von 6“. Nur der erste ist Pflicht, alles
andere lässt sich überspringen und später in den Einstellungen nachholen. „Zurück“ gibt es ab Schritt 3.

**Schritt 1 von 6 — Administrator-Konto anlegen**

- **Einrichtungscode:** siehe oben.
- **Benutzername:** mindestens 3 Zeichen, nur Kleinbuchstaben, Ziffern sowie `.`, `-` und `_`, ohne
  Leerzeichen, vorne ein Buchstabe oder eine Ziffer (z. B. `admin`). Großbuchstaben werden automatisch
  umgewandelt.
- **Passwort** und **Passwort bestätigen:** mindestens 8 Zeichen.
- „Konto anlegen“. Passt eine Angabe nicht, steht gleich da, was (z. B. „Benutzername: Mindestens 3 Zeichen.“).

Dieses erste Konto ist der **Inhaber**. Der Inhaber darf immer alles, auch wenn später
Rollen verstellt werden – so kann man sich nicht aussperren.

**Schritt 2 von 6 — Zeitzone**

Zeitpläne (zum Beispiel das Morgen-Briefing um 7 Uhr) und Wartungsfenster richten sich nach dieser
Zeitzone. Vorausgewählt ist die Zeitzone des Geräts, mit dem du im Browser sitzt; kennt der Browser
sie nicht, bleibt die bisherige Einstellung stehen. Mit der Suche („Berlin“, „New York“, auch deutsche Namen wie
„Wien“ oder „Mitteleuropa“) oder der Liste
wählst du eine andere. „Weiter“ speichert, „Überspringen“ lässt die Einstellung, wie sie ist. Ändern
geht später unter Einstellungen → System. Ohne Suche steht die gewählte Zone oben, darunter die gängigen
(Berlin, Wien, Zürich …); bekannte Zonen zeigen daneben den deutschen Namen (etwa „Österreich, Wien“ bei
`Europe/Vienna`).

**Schritt 3 von 6 — Was willst du nutzen?**

Alle mitgelieferten Module als Kacheln mit Schalter (außer dem Beispiel-Modul „Hello World“ für Entwickler, das nur
unter Einstellungen → Erweiterungen steht), nach Themen sortiert: zuerst „Für deine Server“
(Server-Zustand, Terminal, Docker, Proxmox VE …), dann Sicherheit, Werkzeuge, Verbindungen zu anderen
Diensten. Ein Klick auf die Kachel oder den Schalter schaltet das Modul ein oder wieder aus. Kein Modul
ist Pflicht („Überspringen“). Module, die danach noch Angaben brauchen (Adresse, Zugangsdaten,
Token), tragen den Hinweis „Braucht danach noch Angaben“; diese Angaben kommen **erst jetzt nach dem
Assistenten** unter Einstellungen → Erweiterungen, und die Karte „Erste Schritte“ im Cockpit erinnert
daran (siehe [4.](#4-erweiterungen-einschalten)). Reihenfolge und Gruppe stammen aus dem Manifest des
Moduls (`category`, `sort_order`).

**Schritt 4 von 6 — Zwei-Faktor-Anmeldung (optional)**

„Jetzt einrichten“ fragt zur Sicherheit erst dein aktuelles Passwort ab („Weiter“) und zeigt dann einen
**QR-Code** für die Authenticator-App (er wird im Browser gezeichnet,
nichts geht an einen fremden Dienst) und zusätzlich den Schlüssel als Text, falls das Scannen nicht
klappt. Den sechsstelligen Code aus der App eingeben, „Bestätigen“. Danach erscheinen **einmalig die
Wiederherstellungs-Codes** („Rettungscodes“, siehe [2.1](#21-zwei-faktor-und-wiederherstellungs-codes)):
kopieren oder als Textdatei speichern, dann „Ich habe die Codes gesichert“. „Später einrichten“ steht bei der
Auswahl, bei der Passwortabfrage und neben „Bestätigen“, richtet nichts ein und geht zu Schritt 5; nachholen
unter Einstellungen → Mein Konto. „Zurück“ führt innerhalb des Schritts zur Auswahl.

**Schritt 5 von 6 — Aussehen (optional)**

Produktname, Untertitel (die kleine Zeile unter dem Namen; derselbe Text ist der Name der App auf dem
Handy-Startbildschirm, also kurz halten), Akzentfarbe → „Speichern“, oder „Überspringen“. Logo, Favicon und Theme
(Klassisch hell, Schlicht dunkel, Profi dunkel, Bisher) gibt es später unter Einstellungen → Darstellung. Logo und
Favicon werden dort **hochgeladen**; Bilder von fremden Adressen lädt Nodvard Deck nicht. Stand früher eine fremde
Adresse drin, zeigt die Karte „Marke“ einen Hinweis, bis du das Bild hochlädst oder entfernst.
Dort können Admins (Recht `branding.write`) auch **eigenes CSS** für alle hinterlegen, am einfachsten als
Variablen der Themes, z. B. `:root { --radius: 0px; }`. Erlaubt sind eingebettete Bilder (`data:`) und Pfade auf
Nodvard Deck selbst, kein `@import` und keine fremden Adressen. **Notausgang:** Macht das eigene CSS die Oberfläche
unbedienbar, hänge `?ohne-css=1` an die Adresse (z. B. `http://deck:8080/settings/appearance?ohne-css=1`). Dann lädt
der Tab ohne eigenes CSS, auch beim Weiterklicken; die Karte „Eigenes CSS“ zeigt das an und hat den Knopf „Wieder
einschalten“. Für alle anderen gilt das CSS weiter, bis du es korrigierst und speicherst.

**Schritt 6 von 6 — Fertig**

Hinweis auf die Karte „Erste Schritte“ im Cockpit und der Notfall-Befehl (siehe
[12.](#12-ausgesperrt-notfall-befehle)); derselbe Befehl steht auf der Anmeldeseite hinter „Passwort
vergessen?“. Wer die Zwei-Faktor-Anmeldung übersprungen hat, bekommt hier den Link zum Nachholen.
„Weiter zum Dashboard“ öffnet das Cockpit.

**Neu geladen mitten im Assistenten?** Das Konto gibt es dann schon, `/setup` führt deshalb eigentlich
zur Anmeldung. Der Assistent merkt sich den erreichten Schritt aber in diesem Browser-Tab: ist man noch
angemeldet, geht es genau dort weiter (Zeitzone, Module, Zwei-Faktor, Aussehen), sonst landet man auf
der Anmeldeseite, und danach übernimmt die Karte „Erste Schritte“. Die Wiederherstellungs-Codes lassen
sich nach einem Neuladen nicht noch einmal anzeigen – unter Mein Konto erzeugt man neue.

Danach ist `/setup` weg (führt nur noch zur Anmeldung).

### 2.1 Zwei-Faktor und Wiederherstellungs-Codes

Im Assistenten (Schritt 4) oder später unter Einstellungen → Mein Konto → „Zwei-Faktor-Anmeldung“ →
„Einrichten“: zur Sicherheit das aktuelle Passwort eingeben („Weiter“), dann den QR-Code mit der
Authenticator-App scannen (oder den Schlüssel von Hand eintragen),
den sechsstelligen Code bestätigen. Sobald die Zwei-Faktor-
Anmeldung aktiv ist, zeigt Nodvard Deck **einmalig zehn Wiederherstellungs-Codes** (Form
`ABCDE-FGHJK`). Mit „Kopieren“ oder „Als Textdatei speichern“ sichern – am besten im
Passwortmanager oder ausgedruckt, **nicht** nur auf dem Handy, das ja verloren gehen soll.

- **Benutzen:** Bei der Anmeldung im Schritt „Bestätigung“ auf „Handy nicht zur Hand?
  Wiederherstellungs-Code verwenden“ klicken und einen Code eintippen. Jeder Code gilt
  **genau einmal**; Nodvard Deck trägt die Benutzung ins Protokoll ein und schickt eine
  Meldung (Meldungen-Seite, bei eingerichtetem ntfy auch als Push).
- **Codes aus der App** gelten je Konto nur **einmal** (auch der, mit dem du die Einrichtung
  bestätigt hast); ist einer schon benutzt, auf den nächsten warten. Nach 10 falschen Codes in
  15 Minuten (oder 20 in 24 Stunden), egal von welchem Gerät, ist die Code-Eingabe für das Konto
  eine Weile gesperrt, und Nodvard Deck schickt eine Meldung. Die Sperre gilt auch nach einem
  Neustart von Nodvard Deck weiter. Wer so oft rät, kennt vermutlich das Passwort. Warst du es
  nicht, ändere gleich dein Passwort. Dafür und fürs Anmelden brauchst du in der Zeit einen
  Wiederherstellungs-Code, denn die Codes aus der App sind gesperrt. Bestätigungen mit dem Code aus der
  App (etwa für ein Update) gehen erst wieder, wenn die Sperre vorbei ist.
- **Wie viele übrig sind**, steht unter Mein Konto. Bei wenigen oder keinen: „Neue
  Wiederherstellungs-Codes erzeugen“. Dafür brauchst du dein Passwort und einen Code: den aktuellen
  aus der App oder einen deiner bisherigen Wiederherstellungs-Codes. Die alten Codes sind danach
  sofort ungültig.
- **Abschalten** (Mein Konto → „Abschalten“) geht nur mit Passwort **und** Code: dem aktuellen aus
  der App oder einem Wiederherstellungs-Code, der dabei verbraucht wird. Wer nur dein Passwort kennt,
  kann die Zwei-Faktor-Anmeldung so nicht abschalten. Ältere Apps fragen den Code noch nicht ab; dort
  gehen Abschalten, neue Codes, Passwort ändern und Sicherungen erst nach einem Update der App, im Browser sofort.
- **Passwort ändern** (Mein Konto → „Passwort“) verlangt bei eingeschalteter Zwei-Faktor-Anmeldung ebenfalls
  den Code aus der App oder einen Wiederherstellungs-Code; das Feld dafür erscheint gleich mit. Sonst könnte
  jemand, der nur dein altes Passwort kennt, ein neues setzen und dich aussperren. Eine ältere Handy-App
  zeigt dafür den Hinweis, sich zuerst zu aktualisieren.
- Wird beim Abschalten, beim Ändern des Passworts oder beim Erzeugen neuer Codes ein
  Wiederherstellungs-Code benutzt, steht das wie bei der Anmeldung im Protokoll, und Nodvard Deck
  schickt eine Meldung.
- **Sicherung herunterladen oder einspielen** verlangt bei eingeschalteter Zwei-Faktor-Anmeldung
  ebenfalls den Code aus der App (hier kein Wiederherstellungs-Code): Eine Sicherung enthält alle
  Schlüssel, auch den der Zwei-Faktor-Anmeldung. Das Feld dafür erscheint gleich mit. Vertippst du
  dich, bleiben die Passwörter stehen, nur der Code muss neu.
- Nodvard Deck speichert nur Prüfsummen, keine Codes – ein verlorener Satz lässt sich nicht
  nachschlagen, nur ersetzen.
- Wer schon vor dieser Funktion Zwei-Faktor eingerichtet hatte, hat noch keine Codes:
  unter Mein Konto einmal „Neue Wiederherstellungs-Codes erzeugen“ (mit Passwort und dem Code aus
  der App).
- Verliert jemand Handy **und** Codes: siehe [12.](#12-ausgesperrt-notfall-befehle).

## 3. Weitere Benutzer

Einstellungen → Benutzer → **„Neuer Benutzer“**:

- **Benutzername:** dieselbe Regel wie beim ersten Konto (mindestens 3 Zeichen, nur Kleinbuchstaben,
  Ziffern, `.`, `-` und `_`, vorne ein Buchstabe oder eine Ziffer); Großbuchstaben werden automatisch
  umgewandelt. Bei der Anmeldung ist die Schreibweise egal. Ältere Konten, deren Name nicht zu dieser
  Regel passt (etwa mit Leerzeichen), melden sich weiter normal an.
- **Passwort:** mindestens 8 Zeichen. Der neue Benutzer kann es unter „Mein Konto“ ändern. (Das
  Passwort *anderer* Benutzer setzt man später unter „Bearbeiten“; das eigene und das des
  Inhabers lassen sich dort nicht setzen – eigenes unter „Mein Konto“.)
- **Anzeigename**, **E-Mail (optional)**; eine eingetragene Adresse braucht ein `@` (z. B. `name@beispiel.de`). Nodvard Deck
  verschickt keine E-Mails: Die Adresse ist nur eine Angabe zum Konto (Benachrichtigungen kommen als Push über ntfy).
- **Rollen:**
  - **Administrator** (`admin`) – Volle Verwaltung inkl. Benutzer und Einstellungen
  - **Bediener** (`operator`) – Server bedienen, Aktionen bis mittleres Risiko freigeben
  - **Betrachter** (`viewer`) – Nur ansehen, nichts ändern. Ohne das Recht `hosts.execute` (wie für Befehle) sieht der Betrachter
    keine Dateien auf Servern und weder Befehle noch Ausgabe von Aktionen (Text vom Server), auch nicht im Protokoll.
    Meldungen kann `viewer` lesen, aber nicht als gelesen markieren oder löschen (beides gilt für alle Benutzer gemeinsam)
- „Anlegen“. Passt eine Angabe nicht, steht oben gleich, was (z. B. „Passwort: Mindestens 8 Zeichen.“).

**Zwei-Faktor eines Benutzers zurücksetzen:** Hat jemand Handy und Wiederherstellungs-Codes
verloren, schaltet ein Benutzer mit Recht `users.write` (Rolle `admin`) unter Einstellungen →
Benutzer → **„Zwei-Faktor zurücksetzen“** die Zwei-Faktor-Anmeldung dieser Person ab (zur
Sicherheit wird dabei das eigene Passwort des Admins abgefragt und, wenn er selbst Zwei-Faktor an hat, sein
eigener Code aus der App). Dasselbe gilt, wenn ein Admin beim Bearbeiten ein neues Passwort für jemand anderen
setzt, ein Konto mit Verwaltungsrechten (Rolle `admin`) anlegt oder einem Konto diese Rolle gibt. Die Person wird dabei überall abgemeldet; das Protokoll hält fest, wer es getan hat, und unter
„Meldungen“ steht „Zugang von … geändert“. Danach geht
die Anmeldung wieder nur mit Passwort, und sie kann Zwei-Faktor neu einrichten. **Beim
Inhaber geht das nicht** – seine Zwei-Faktor-Anmeldung kann nur er selbst (unter Mein Konto)
oder der Notfall-Befehl auf dem Server ([12.](#12-ausgesperrt-notfall-befehle)) abschalten.

**Benutzer deaktivieren:** Unter Einstellungen → Benutzer → **„Deaktivieren“** sperrt ein Benutzer
mit Recht `users.write` das Konto einer anderen Person (in der Liste steht dann „Gesperrt“; beim
Inhaber und beim eigenen Konto gibt es den Knopf nicht). Die Person wird dabei überall abgemeldet,
eine schon offene Seite bekommt nach spätestens einer halben Minute keine Live-Aktualisierung mehr.
Nach **„Aktivieren“** melden sich alte Geräte nicht von allein wieder an, sie muss sich neu anmelden.

## 4. Erweiterungen einschalten

Bei einer frischen Installation sind **alle Erweiterungen aus** – bis auf eine Ausnahme: **Terminal
schaltet sich von selbst ein**, sobald du dem ersten Server einen SSH-Zugang gibst (Schlüssel
erzeugen, Passwort oder eigener Schlüssel; siehe [5.2](#52-ssh-zugang-einrichten--drei-wege)). Das
Protokoll hält es fest („Erweiterung automatisch eingeschaltet“). Das passiert nur, solange
niemand Terminal je selbst ein- oder ausgeschaltet hat: Wer es bewusst ausschaltet, bei dem bleibt
es aus. Bereits bestehende Installationen sind davon nicht betroffen.

Im Einrichtungsassistenten (Schritt 3, siehe [2.](#2-einrichtungsassistent-setup)) schaltest du die Module,
die du nutzen willst, per Klick ein. Später geht das jederzeit unter Einstellungen →
Erweiterungen, mit dem Schalter rechts, „Konfigurieren“ öffnet die
Einstellungen der Erweiterung („Einrichtung nötig“ heißt: es fehlen noch Angaben oder der
letzte Verbindungstest ist fehlgeschlagen). Öffnest du die Seite eines ausgeschalteten Moduls (etwa über ein
Lesezeichen), sagt sie das, und **„Einschalten“** schaltet es dort gleich ein (ohne das Recht dazu steht da, dass ein
Administrator es einschalten kann). Ein guter Anfang für fast jedes Heimnetz: Terminal, Server-Zustand,
Nodvard Shield und Push-Nachrichten, dazu das Modul Docker, wenn auf einem Server Docker läuft, und
Gameserver, wenn dort ein Spieleserver steht. **Proxmox VE und Proxmox-Backups brauchst du nur, wenn du Proxmox
nutzt** – sonst lass sie aus (siehe [4.1](#41-ohne-proxmox)). Der Rest nach Bedarf.

Unter Einstellungen → Erweiterungen stehen die Module in denselben Gruppen wie im Assistenten; die Beispiel-Module
für Entwickler („Hello World“) stehen ganz unten unter „Für Entwickler“. Hat ein Modul Server eingelesen (etwa Proxmox),
nennt die Rückfrage beim Ausschalten ihre Zahl: Die Server bleiben in der Liste stehen, im Cockpit tragen sie
„Modul aus“, und ihr Zustand wird nicht mehr aktualisiert. Löschen kannst du sie unter Einstellungen → Server & Zugänge.

**„Beta“:** Gameserver, Home Assistant, Jellyfin, Netzwerk, Nginx Proxy Manager, Pi-hole, Plex, qBittorrent, Synology DSM,
Uptime Kuma und Vaultwarden tragen das Kennzeichen „Beta“ (in den Einstellungen, im Assistenten, im Menü und über der Seite
des Moduls). Sie funktionieren, sind aber noch nicht so gründlich erprobt wie der Rest. Rückmeldungen helfen.

**Adress-Check (Nginx Proxy Manager):** Auf der Seite „Nginx Proxy Manager“ steht unter jedem Proxy-Host, zu welchem
Docker-Container er führt und ob der läuft, etwa „Führt zum Container immich_server auf docker – der ist gestoppt.“
Ist er gestoppt, startet ihn der Knopf „Container starten“ über die Freigabe (wer `hosts.execute` hat). Zugeordnet wird
über die Server-Adresse mit Port (auch bei gestoppten Containern; nur an `127.0.0.1` gebundene Ports zählen nicht), über
`host.docker.internal` auf dem Server, auf dem der Proxy Manager selbst läuft (`localhost` nur, wenn er im Host-Netz
läuft, sonst ist das der Proxy Manager selbst), und über den Containernamen bzw. Compose-Dienst. Namen zählen nur auf dem Server des Proxy
Managers (Docker kennt sie nur im eigenen Netz); ist der unbekannt, heißt es „vermutlich“, ohne Rot und ohne Knopf.
Dafür muss das Modul Docker eingeschaltet sein, es kennt die Container. Ein Ziel, das zu keinem Container passt
(etwa die Proxmox-Oberfläche), steht grau da und ist kein Fehler. Nicht erkannt werden Container im Host-Netz (sie
zeigen keine Ports), Netzwerk-Aliase und zusätzliche Orte (`locations`) eines Proxy-Hosts.

**Synology DSM (Beta, nur lesend):** Die Seite „Synology DSM“ zeigt von einer oder mehreren Synology Modell, DSM-Version,
Auslastung, Temperatur, Speicherpools, Volumes, Festplatten (mit SMART), die Aufgaben von Hyper Backup und ob ein
DSM-Update bereitsteht. Nodvard Deck liest nur und ändert an der Synology nichts. Welche Version jeder Schnittstelle es
nimmt, fragt es DSM vorher; so laufen DSM 7.1 bis 7.4, auch nebeneinander. Erprobt ist das bisher mit nachgebauten
Antworten von DSM, Rückmeldungen von echten Geräten helfen.

- **Mehrere Geräte:** Trag unter Einstellungen → Erweiterungen → Synology DSM jedes Gerät einzeln ein (bis zu zehn): mit
  Kurzname (z. B. „ds920“ oder „keller“), Adresse, eigenem Konto, eigenem Passwort im Tresor und eigenen Angaben zum
  Zertifikat. Der Kurzname steht in Meldungen und auf den Kacheln, damit man die Geräte unterscheidet; ändere ihn nach dem
  Einrichten nicht mehr, denn das Passwort gehört zum Namen und müsste neu eingetragen werden. Die Seite zeigt oben eine
  Auswahl aller Geräte mit ihrem Zustand. Lässt sich ein Gerät nicht abfragen, steht bei ihm der Grund und „Erneut
  versuchen“, die anderen bleiben davon unberührt. Auf der Startseite hat jedes Gerät seine drei Kacheln (Zustand,
  Speicher, Hyper Backup), bei mehreren mit dem Kurznamen im Titel; neue Kacheln erscheinen unter „Widgets verwalten“. Auch
  die Meldungen sind getrennt: Ihr Titel nennt bei mehreren Geräten den Kurznamen (bei einem einzigen das Modell), und
  ein Problem oder eine Entwarnung des einen Geräts berührt das andere nicht. Kommt ein Kurzname doppelt vor (Groß- und
  Kleinschreibung zählt nicht), fragt Nodvard Deck keinen dieser Einträge ab. Bei genau gleicher Schreibweise wird ein dazu
  hinterlegtes Passwort beim Speichern gelöscht (nach Rückfrage), damit es nie bei der Adresse eines anderen Eintrags
  landet, und ein neues nimmt Nodvard Deck erst an, wenn einer der Einträge umbenannt und gespeichert ist. Unterscheiden
  sich die Namen nur in Groß- und Kleinschreibung, bleibt das Passwort beim bisherigen Eintrag stehen, wird aber nicht
  benutzt, bis einer der Namen geändert ist.
- **Von einem Gerät auf mehrere:** Wer das Modul schon mit einem Gerät eingerichtet hat, trägt nichts neu ein. Beim ersten
  Start mit dieser Version wird das bisherige Gerät als Gerät „synology“ übernommen: Adresse, Konto, Angaben zum
  Zertifikat, das Passwort (als Kopie im Tresor unter „synology-password:synology“), die Kacheln auf der Startseite und der
  Stand der Meldungen. Schon gemeldete Probleme kommen darum nicht noch einmal. Die alten Einzelfelder und der alte
  Tresor-Eintrag „synology-password“ bleiben unverändert stehen, damit ein Zurück auf die ältere Version das Gerät
  findet. Solange das Gerät „synology“ in der Liste steht, bleibt der alte Eintrag im Tresor; er gehört dem Modul und
  lässt sich dort nicht einzeln löschen. Entfernst du das Gerät „synology“ später oder benennst es um, löscht Nodvard Deck
  den alten Eintrag gleich mit, danach führt kein Weg mehr zurück auf die ältere Version. Ein neues Passwort trägst du ab
  jetzt beim Gerät „synology“ ein. Weitere Geräte kommen einfach dazu.

- **Konto:** Lege in DSM ein eigenes Konto nur für Nodvard Deck an (Systemsteuerung → Benutzer und Gruppe) und trag es
  in den Einstellungen des Moduls ein, das Passwort kommt in den Tresor. Ehrlich gesagt: Speicher, Auslastung, Updates
  und Hyper Backup gibt DSM nur an Konten in der Gruppe **„administrators“** heraus. Ein Konto ohne diese Gruppe
  funktioniert, die Seite zeigt dann aber nur, was es lesen darf, und sagt bei jedem Teil, dass Rechte fehlen. Weil das
  Konto damit Administrator ist, gib ihm ein langes, eigenes Passwort, das du sonst nirgends nutzt.
- **Zwei-Faktor geht nicht:** Ein Konto mit Zwei-Faktor-Anmeldung kann Nodvard Deck nicht nutzen, es kennt den Code aus
  der App nicht; die Seite sagt dann „Dieses Konto verlangt Zwei-Faktor“. Nimm darum das eigene Konto ohne Zwei-Faktor.
  Verlangt DSM Zwei-Faktor für alle Administratoren, gilt das auch für dieses Konto; dann beschränke die Pflicht in den
  Sicherheitseinstellungen von DSM auf bestimmte Konten.
- **Falsches Passwort:** DSM sperrt Adressen nach mehreren Fehlversuchen automatisch. Nach einer abgelehnten Anmeldung
  fragt Nodvard Deck darum 30 Minuten lang nicht erneut, außer du drückst „Verbindung testen“ (das hebt die Pause aller
  Geräte auf) oder änderst Adresse, Konto oder Zertifikat-Angaben dieses Geräts. Die Pause gilt nur für das Gerät, bei dem
  die Anmeldung abgelehnt wurde; die anderen Geräte werden weiter abgefragt. Andere Einstellungen zu speichern oder ein
  Gerät hinzuzufügen oder zu entfernen ändert nichts an der Pause. Hast du nur das Passwort im Tresor ersetzt, drücke
  „Verbindung testen“.
- **Zertifikat:** DSM hat oft ein selbst erstelltes Zertifikat. Dann im eigenen Netz „Selbstsigniertes Zertifikat
  erlauben“ einschalten, oder besser unter „Erweitert“ den Fingerabdruck des Zertifikats eintragen (nur mit https://):
  Dann spricht Nodvard Deck nur mit genau diesem Zertifikat.
- **Meldungen:** Alle fünf Minuten sieht Nodvard Deck bei jedem Gerät nach und meldet sich, wenn ein Speicherpool oder Volume nicht in
  Ordnung ist (etwa „Eingeschränkt“ nach einem Plattenausfall), eine Festplatte warnt (SMART, defekte Sektoren,
  Restlebensdauer), das Gerät oder ein Laufwerk zu warm wird oder Hyper Backup fehlschlägt oder überfällig ist. Die
  Meldung steht unter „Braucht Aufmerksamkeit“ und kommt über die eingerichteten Kanäle aufs Handy; ist alles wieder gut,
  kommt eine Entwarnung. Gemeldet wird nur ein Wechsel, nicht jede Prüfung, und noch einmal, wenn es schlimmer wird (etwa
  von „Eingeschränkt“ zu „Ausgefallen“). Eine Temperatur erst, wenn sie 10 bis 15
  Minuten anhält (Grenze für Laufwerke unter „Erweitert“, Vorgabe 55 °C für Festplatten, 65 °C für SSD, 70 °C für NVMe).
  Reparieren, Erweitern oder Prüfen eines Pools oder Volumes gilt nicht als Problem, ebenso ein Zustand, den Nodvard Deck
  nicht kennt; war er vorher gemeldet, kommt die Entwarnung aber erst, wenn er wieder „Normal“ ist. Solange ein Pool
  nicht „Normal“ ist, melden seine Volumes nichts Eigenes. Fällt ein Volume aus, steht das in der Meldung seines Pools,
  wenn der Pool selbst eine hat (etwa „Eingeschränkt“), und diese Meldung wird dringend; ohne Meldung des Pools (etwa
  während einer Reparatur) meldet sich das Volume selbst. Ein neues DSM-Update kommt einmal je Version als
  ruhiger Hinweis (Stufe „Info“, nicht unter „Braucht Aufmerksamkeit“). Beides lässt sich in den Einstellungen des Moduls
  abschalten. Ist die Synology nicht erreichbar oder wird die Anmeldung abgelehnt, meldet der Wächter nichts Neues; das
  zeigen die Seite und die Kachel.
- **Hyper Backup:** Je Aufgabe stehen Ergebnis, letzter und nächster Lauf da, in der Ortszeit der Synology. Überfällig
  heißt: Ein geplanter Lauf ist seit mehr als sechs Stunden ausgeblieben, oder es gab seit zwei Abständen zwischen den
  Läufen und sechs Stunden Puffer (bei täglicher Sicherung also seit 54 Stunden) keinen erfolgreichen mehr. Gezählt wird
  mit der Uhr der Synology; liefert DSM sie nicht, kommen 14 Stunden Puffer für eine andere Zeitzone dazu. Läuft eine
  Sicherung oder die Integritätsprüfung gerade, ist sie nicht überfällig; während der Prüfung steht „Integritätsprüfung
  läuft“ da, ein Fehler des letzten Laufs bleibt aber sichtbar. Ist Hyper Backup nicht installiert, steht dort ein
  ruhiger Hinweis, der Rest der Seite bleibt. Nodvard Deck fragt bis zu 64 Aufgaben ab; hat Hyper Backup mehr, sagt die
  Seite das. Für Liste und Stände hat Hyper Backup zusammen 25 Sekunden, gerechnet ab Beginn der Abfrage. Kommt die
  Liste nicht rechtzeitig, steht bei Hyper Backup „Hyper Backup hat nicht rechtzeitig geantwortet“; sind nicht alle
  Stände da, stehen die übrigen Aufgaben mit „Stand unbekannt“ da, ohne Meldung und ohne Entwarnung. Der Rest der Seite
  bleibt in beiden Fällen. Noch nicht an echten Geräten bestätigt (Beta): dass DSM eine Aufgabe, die gerade nicht
  sichert, mit dem Zustand `status: none` meldet. Steht eine Aufgabe dauerhaft auf „Läuft gerade“, obwohl nichts läuft,
  gib bitte Bescheid. Ebenso stammt aus einer Rückmeldung, nicht aus einer Beschreibung von Synology, dass der Zustand
  `error_detect` die laufende Integritätsprüfung ist; steht eine Aufgabe tagelang auf „Integritätsprüfung läuft“, gib
  bitte ebenfalls Bescheid.

**Zugangsdaten gehören zur Adresse.** In den Einstellungen einer Erweiterung erst die Adresse eintragen
und „Speichern“ drücken, dann in der Karte „Verbindung“ Token oder Passwort hinterlegen – vorher nimmt
Nodvard Deck sie nicht an. Änderst du später die Adresse (etwa den ntfy-Server oder eine Proxmox-Adresse),
löscht Nodvard Deck beim Speichern die Zugangsdaten dazu, und du trägst sie neu ein; die Seite weist
vorher darauf hin. Eine andere Schreibweise derselben Adresse (Groß-/Kleinschreibung im Rechnernamen,
Standardport, `/` am Ende, fehlendes `http://`) zählt nicht als Änderung.

### 4.1 Ohne Proxmox

Nodvard Deck braucht kein Proxmox. Ein Raspberry Pi, eine Debian-VM, ein NAS oder ein paar Docker-Hosts
reichen. Was dann anders ist:

- **Server legst du von Hand an** ([5.1](#51-server-anlegen)) und gibst jedem einen SSH-Zugang
  ([5.2](#52-ssh-zugang-einrichten--drei-wege)). Die Module Proxmox VE und Proxmox-Backups bleiben aus; ihre Einträge
  erscheinen dann weder im Menü noch im Cockpit.
- **Zustand (online/offline):** Nodvard Deck prüft von Hand angelegte Server **von selbst alle 2 Minuten**
  (Einstellungen → Server & Zugänge → „Erreichbarkeit prüfen“; Schalter und Abstand von 1 bis 60 Minuten).
  Geprüft wird nur, ob der SSH-Port des Servers (Standard 22, sonst der Port des Zugangs) eine Verbindung annimmt:
  ohne Anmeldung, auch für Server ohne Zugang. Ein Server gilt erst nach **zwei Fehlversuchen hintereinander** als
  „nicht erreichbar“, „wieder da“ gilt sofort. Fällt ein Server aus (oder kommt wieder), gibt es **eine Meldung**
  mit Link zur Server-Seite, auf Wunsch auch als Push; das Cockpit zeigt ihn unter „Braucht Aufmerksamkeit“. Im
  **Wartungsfenster** gibt es keinen Push (nur den Verlauf); dauert der Ausfall nach dem Fenster an, kommt die Meldung
  einmal nach. Ein Server, der **noch nie geantwortet hat** (zum Beispiel ein Tippfehler in der Adresse), steht
  einfach auf „nicht erreichbar“, ohne Meldung. Nicht geprüft werden Server, die ein Modul selbst einliest
  (etwa Proxmox-Gäste), Beispiel-Server, Server im Zustand „Wartung“ und Server, deren Standard-Zugang kein SSH ist.
  Ein Server ohne SSH-Dienst (zum Beispiel ein Gerät, auf dem SSH aus ist) zeigt deshalb dauerhaft „nicht
  erreichbar“; wer das nicht will, schaltet ihn auf seiner Seite aus. „Verbindung prüfen“ gibt es weiter für den
  genauen Test mit Anmeldung. Das Cockpit zeigt bei ungeprüften Servern „noch nicht geprüft“.
- **Auslastung und Verlauf** (CPU, Arbeitsspeicher, Platte, Netz, Temperatur) kommen vom Modul **Server-Zustand** (bis Version 0.8.0 hieß es „System“)
  per SSH, nur für Linux-Server mit SSH-Zugang. Ohne dieses Modul zeigt die Server-Seite einen Hinweis statt
  der Ringe und Kurven. Der Verlauf wird alle 30 Sekunden gemessen und ist die ersten Minuten noch leer. Hat der
  Server noch nie geantwortet oder ist die Anmeldung noch nicht bestätigt, sagt die Server-Seite das, statt auf Kurven
  warten zu lassen (wer Server ändern darf, bekommt den Link „Zum Zugang“). Im Cockpit steht bei einem Server mit
  SSH-Zugang, der „nicht erreichbar“ ist und noch nie geantwortet hat, „Noch keine Verbindung: prüfe zuerst den Zugang“.
- **Auslastung im Cockpit:** Im Bereich „Infrastruktur“ bekommt jeder Linux-Server mit Messwerten eine Karte mit
  Ringen für CPU, Arbeitsspeicher und Platte, so wie die Proxmox-Knoten. Die Zahlen stammen aus dem schon geführten
  Verlauf (letzte Messung, alle 30 Sekunden); das Cockpit löst beim Laden **keine** SSH-Verbindung aus und fragt alle
  Server in einer einzigen Abfrage (`GET /hosts/metrics/latest`). Ist die letzte Messung älter als 2 Minuten, steht
  „veraltet“ mit dem Alter darunter und die Ringe sind gedämpft; nach 10 Minuten ohne Messung wird der Server wieder
  eine schlichte Zeile. Ein Server, der „nicht erreichbar“ ist, zeigt keine alten Werte als aktuell. Server ohne
  Messwerte (Windows, ohne SSH-Zugang, Modul Server-Zustand aus, Messwerte für den Server abgeschaltet) bleiben eine Zeile,
  wo es passt mit einem Hinweis, warum.
- **Docker-Container:** das Modul Docker, für Server mit der Markierung `docker`. Dort startest, stoppst und löschst
  du Container (gelöscht werden nur gestoppte; Volumes und Image bleiben), liest ihre Logs live, öffnest mit
  „Konsole“ eine Shell im Container und spielst neuere Images ein („Einspielen“, nur bei Docker-Compose-Diensten). Stürzt
  die neue Version beim Start ab, geht das Modul Docker in der Regel von selbst auf die alte zurück – nie bei
  Datenbanken und anderen Datenspeichern; abschaltbar unter „Konfigurieren“ („Bei Fehlstart automatisch zurück“).
  Nach „Starten“, „Neustart“ und „Stack neu starten“ sieht Deck bis zu einer Minute lang nach, ob die Container
  wirklich laufen und laufen bleiben (ohne Healthcheck: zweimal im Abstand von 15 Sekunden ohne neuen Start). Stürzt
  ein Container gleich wieder ab, gilt die Aktion als fehlgeschlagen und sagt, warum (etwa „beendet, Exit-Code 1“).
  Beim Stack zählen nur die Container, die vorher liefen. Ein einzelner Neustart (etwa weil eine Abhängigkeit noch
  nicht bereit war) und ein Ende mit Exit-Code 0 (Container für einmalige Aufgaben) gelten nicht als Fehler, sie stehen
  nur als Hinweis dabei; „ungesund“ zählt erst, wenn es bis zum Ende so bleibt.
- **Apps im Cockpit ohne das Modul Docker:** Im Bereich „Apps“ legst du mit **+ App hinzufügen** eigene Kacheln an –
  Name, Adresse (`http://192.168.1.1`), Symbol (aus einer Auswahl oder ein Emoji), Farbe, Gruppe und auf Wunsch der
  Server dazu. So stehen auch Router, NAS-Oberfläche oder Pi-hole im Cockpit, ohne Code und ohne Container-Erkennung;
  erkannte Container erscheinen daneben, wenn das Modul Docker läuft. Über das Menü (⋮) an der Kachel bearbeitest,
  verschiebst oder löschst du sie, oben filterst du nach Gruppen. Anlegen und Ändern dürfen Inhaber und
  Administratoren (Recht `apps.write`); sehen dürfen es alle, die Server sehen. Nodvard Deck ruft die Adressen nie
  selbst auf (es zeigt also kein „läuft/läuft nicht“ an), erlaubt nur `http://` und `https://` und keine Zugangsdaten
  in der Adresse; Symbole sind nie Bild-Adressen.
- **Seite „Apps“** (links im Menü): alle Kacheln an einem Ort, eigene Apps in **Ordnern** (ihre Gruppe), die du
  zuklappen kannst. Mit `apps.write` ziehst du eine Kachel auf eine andere (davor einsortiert) oder auf die Überschrift
  eines Ordners (dort ans Ende); ohne Maus über das Menü (⋮). Unter **„Vorschläge“** stehen Adressen, die deine Module
  schon kennen – zum Beispiel die Proxy-Hosts aus dem Nginx Proxy Manager – und die es als App noch nicht gibt;
  **„Übernehmen“** füllt den Dialog vor, angelegt wird erst mit „Speichern“. Erkannte Container mit Web-Oberfläche
  stehen nicht dort, sondern unter **„Erkannte Dienste“**; dort macht **„Als App übernehmen“** (nur bei laufenden
  Containern) eine eigene Kachel daraus. Beide Listen kommen aus demselben Lesen der Docker-Server. Nur wenn die Kachel
  eines Containers gerade nicht zu sehen ist (etwa weil die Seite sie noch nicht geladen hat), steht er kurz unter
  „Vorschläge“.
- **Updates, Virenschutz, Einbruchschutz:** Nodvard Shield, für alle Linux-Server mit SSH-Zugang. Die KI-Container-Wache
  ist darin optional; ohne KI-Server zeigt sie abgestürzte Container trotzdem an, mit der Ursache laut System (etwa
  Speichermangel oder von außen beendet) und zwei festen Sätzen: was das heißt und was du tun kannst. Mit KI schlägt sie höchstens den
  Neustart eines abgestürzten Containers vor, du findest ihn unter „Aktionen“; andere Ideen der KI stehen nur als
  Text im Lagebericht der Container-Wache. Schlägt sie einen anderen Befehl vor, steht im Lagebericht nur ein Hinweis
  darauf; den Befehl selbst siehst du mit Server-Rechten (`hosts.execute`) im Protokoll.
  Ein Klick auf die Push-Meldung des Lageberichts öffnet genau diesen Vorschlag unter „Aktionen“ (hervorgehoben, auch
  wenn er schon erledigt ist); ohne Vorschlag öffnet er den Reiter „Container-Wache“. Anmelden musst du dich trotzdem.
  Läuft ein Container nach einem Absturz wieder fünf Minuten ruhig, setzt die Container-Wache seinen Vorfall von selbst
  auf „Erledigt“ („Läuft wieder seit 04:12 – von selbst erledigt.“, ohne Push-Meldung, mit Eintrag im Protokoll).
  Wartet dazu noch ein vorgeschlagener Neustart unter „Aktionen“, steht dabei, dass er nicht mehr nötig ist; ablehnen
  musst du ihn dort selbst. Stürzt der Container innerhalb der Ruhezeit wieder ab, ist derselbe Vorfall wieder offen,
  sofern ihn noch niemand bearbeitet hatte und kein Neustart dazu schon gelaufen ist; sonst gibt es einen neuen Vorfall
  mit Meldung, auch in der Ruhezeit (zuletzt hieß es ja „läuft wieder“). Die fünf
  ruhigen Minuten zählt die Container-Wache nur, solange das Dashboard läuft: Nach einem Neustart des Dashboards bleibt
  ein offener Vorfall offen, bis der Container wieder eine Störung hatte und danach ruhig lief, oder bis du ihn abhakst.
  Stürzt ein Container innerhalb von sechs Stunden nach einem freigegebenen Neustart genau dieses Containers wieder ab
  (und lief er dazwischen nicht ruhig), sagt die nächste Meldung: „nginx auf docker ist nach dem freigegebenen Neustart
  um 03:10 wieder abgestürzt.“ – auch innerhalb der Ruhezeit, in der sonst keine neue Meldung kommt.
- **Server wählen, Dateien:** Auf den Seiten „Server-Zustand“ und „Terminal“ stehen die Server als Kacheln mit Name, Adresse,
  System und Status (Ampelpunkt). Auf „Server-Zustand“ zeigen sie auch die Auslastung („CPU 12 % · RAM 48 %“, bis zu 5 Minuten
  alt) und einen Befund wie eine fast volle Platte als Hinweis; der zuletzt gewählte Server ist beim nächsten Besuch
  vorgewählt (das merkt sich der Browser). Im Dateimanager steht jeder Server mit SSH-Zugang als Quelle (nur mit dem
  Recht `hosts.execute`); die Leiste zeigt daneben, ob er erreichbar ist und welches System darauf läuft. Quellen ohne
  Server, etwa Nextcloud, haben beides nicht. Ohne das Recht, Server zu sehen (`hosts.read`), fehlen nur Punkt und System.
  Textdateien (Konfiguration, Logs, Skripte) und Bilder öffnest du mit **„Ansehen“** direkt über der Liste; bei großen
  Dateien zeigt die Vorschau die ersten 256 KB. HTML erscheint als Text, SVG- und PDF-Dateien holst du mit „Laden“.
- **Konsole im Browser** (Bildschirm eines Gasts) gibt es nur für Proxmox-Gäste. Für alle anderen Server
  nimmst du das **Terminal**.
- **Sicherungen:** Das Modul „Proxmox-Backups“ zeigt nur die Backups deiner Proxmox-Server. Das Dashboard selbst sicherst du unter
  Einstellungen → System ([10.](#10-das-dashboard-selbst-sichern)).
  Mit „Als Kopie wiederherstellen“ holst du dort einen Gast aus einer Sicherung zurück: als neuen Gast mit neuer Nummer,
  der nicht von selbst startet; das Original bleibt unberührt. Jede Kopie entsteht im Pool `nodvard-kopien` (nur dort
  braucht der Zugang das Recht zum Anlegen; ab Proxmox VE 7.2 für VMs). Die Netzwerkkarten trennt Nodvard Deck, sobald
  Proxmox fertig ist (sonst holt es der Backup-Wächter nach und meldet sich). Pool und Token-Rechte stehen in
  [docs/10](10-PROXMOX-TOKEN.md#wiederherstellen-als-kopie-backups).

**Die Karte „Erste Schritte“:** Auf der Startseite (Cockpit) führt oben eine Checkliste durch
genau diese Schritte – Server anlegen, SSH-Zugang hinterlegen, Verbindung prüfen, Module
einrichten, Push-Nachrichten, erstes Widget – jeder mit einem Knopf zum richtigen Ort. Die
Häkchen setzt Nodvard Deck selbst aus dem, was schon eingerichtet ist. „SSH-Zugang hinterlegen“ zählt einen
Schlüssel erst, wenn sich Nodvard Deck damit wirklich angemeldet hat (ein Passwort sofort); bis dahin führt
**„Befehl ansehen“** zur Seite des Servers mit schon aufgeklapptem Einrichtungsbefehl. „Verbindung prüfen“ ist erst
abgehakt, wenn eine Anmeldung bestätigt ist: Dass der Server antwortet, reicht nicht. Es erscheinen nur Schritte,
für die du das Recht hast. Die Karte verschwindet von allein, wenn alles erledigt ist; mit
**„Ausblenden“** nimmst du sie früher weg (gilt für dein Konto auf jedem Gerät). Auch leere Seiten
(Terminal, Dateien, Server-Seite, Docker, Gameserver, Proxmox) sagen jetzt, was fehlt, und
bieten den Knopf zum nächsten Schritt an.

**Die Widgets auf der Startseite:** Die Module bringen ihre Widgets mit; sie stehen in Gruppen (Server und Proxmox,
Sicherheit, Docker und Apps, Backups, Dienste). Ein Klick auf den Namen klappt eine Gruppe zu; ein farbiger Punkt daneben
meldet dann eine Warnung, eine Störung oder ein Widget, das gerade nicht lädt. Mit **„Bearbeiten“** über den Widgets
ordnest du alles an: Karten am Griff verschieben, Höhe (Automatisch, Klein, Mittel, Groß), Breite (Schmal, Breit) und
Gruppe wählen, Widgets entfernen und Gruppen umbenennen. Erst **„Fertig“** speichert alles auf einmal, **„Abbrechen“**
verwirft die Änderungen; ein entferntes Widget holst du über **„Widgets verwalten“** zurück. Am Handy stehen die Karten
untereinander: Höhe, Gruppe und Entfernen gehen dort auch, Verschieben und Breite nur auf einem größeren Bildschirm.

## 5. Server und SSH-Zugang

Alles dazu gibt es auf einer Seite: **Einstellungen → Server & Zugänge**. Dort legst du
Server an, richtest ihren SSH-Zugang ein und prüfst, ob die Verbindung klappt. Die Seite ist
für das Handy gedacht und nur für Benutzer mit dem Recht `hosts.write` (Inhaber und
`admin`) sichtbar.

**Woher die Server kommen:**

- Proxmox-Knoten, VMs und Container liest die Erweiterung „Proxmox VE“ **automatisch** ein
  (alle 5 Minuten, siehe [7.](#7-proxmox-verbinden)). Sie stehen mit dem Hinweis „Automatisch
  eingelesen“ in der Liste.
- Alles andere – vor allem der **Rechner, auf dem Nodvard Deck läuft** (z. B. ein Raspberry Pi),
  wenn er kein Proxmox-Gast ist – legst du von Hand an.
- Terminal, Server-Zustand, Docker, Nodvard Shield, Gameserver und Skripte brauchen für jeden
  Server einen **SSH-Zugang** (Benutzer + Schlüssel oder Passwort). Den speichert Nodvard Deck
  verschlüsselt; der private Schlüssel verlässt den Tresor nie und wird nirgends angezeigt.
- **Kleine SSH-Server** wie Dropbear ab Version 2015.68 (etwa auf DietPi, Routern und manchen NAS) gehen auch: Lange
  Befehle (über 8000 Byte, z. B. die Einrichtung, Updates, Härtungs-Audit und Einbruchschutz von Nodvard Shield)
  schickt Nodvard Deck nicht als Befehlszeile, sondern über die Standardeingabe. Dafür muss die Login-Shell des Kontos
  eine POSIX-Shell sein (sh, bash, dash oder die ash von BusyBox); mit fish oder tcsh als Login-Shell klappen diese
  langen Befehle nicht. Wo die Standardeingabe nicht geht (Konsole, Live-Logs), sagt Nodvard Deck bei einem zu langen
  Befehl vorher in Klartext Bescheid und schickt ihn nicht los. Ältere Dropbear-Versionen (etwa auf OpenWrt 15.05)
  nehmen nur Befehle bis 1024 Byte an, dort scheitern viele Befehle.

**Noch keinen Server zur Hand?** Auf einer frischen Installation ohne Server gibt es im Cockpit (und
auf dieser Seite) den Knopf **Mit Beispieldaten ansehen**. Ein Klick legt fünf ausgedachte Server
(Adressen aus `192.0.2.x`, es wird nie etwas angefragt), ein paar Meldungen, drei Beispiel-Apps und – wenn Module Widgets
mitbringen – ein Beispiel-Dashboard an, damit du siehst, wie alles gefüllt aussieht. Oben steht dann
ein gelbes Band **„Du siehst Beispieldaten“**; **Beispieldaten löschen** (nach Rückfrage) entfernt genau
diese Dinge wieder, stellt dein Dashboard-Layout zurück und lässt alles Echte unberührt. Den Knopf
sehen nur Inhaber und Administratoren (`hosts.write` und `settings.write`). Sobald du einen echten
Server angelegt hast, gibt es den Knopf nicht mehr; die Beispieldaten kannst du trotzdem jederzeit
löschen.

### 5.1 Server anlegen

1. **Server hinzufügen** drücken (bei einer leeren Liste gibt es den Knopf auch in der Mitte). Kommst du über
   „Server hinzufügen“ im Cockpit oder in der Karte „Erste Schritte“, ist das Formular schon offen.
2. **Kurzname** – klein, ohne Leerzeichen, z. B. `bastel-pi`. Er lässt sich später **nicht**
   mehr ändern (Nodvard Shield und andere Erweiterungen erkennen den Server daran).
3. **Anzeigename** (so heißt der Server in den Listen; leer = Kurzname) und **Adresse**
   (IP oder Rechnername, ohne `http://` und ohne Port, z. B. `192.168.1.30`).
4. **Betriebssystem:** Linux oder Windows. Für Windows-Server gibt es keinen
   Einrichtungsbefehl und keine Rechte-Prüfung. Einen Windows-Server (etwa einen Gameserver) wirklich als Windows
   eintragen: Lange Befehle schickt Nodvard Deck sonst auf Linux-Art, und die scheitern unter Windows.
5. **Markierungen** (mit Komma getrennt): `docker`, wenn dort Docker läuft – das Modul Docker
   (Einstellung „Server-Markierung“) und die KI-Container-Wache von Nodvard Shield („Überwachte
   Server“) nehmen genau die Server mit dieser Markierung. Gameserver erscheinen nur mit der
   Markierung `gameserver`. Markierungen lassen sich jederzeit ändern, auch bei Proxmox-VMs
   (die von Proxmox gesetzten bleiben, man kann sie nicht ändern).
6. **Anlegen.** Die Seite des neuen Servers öffnet sich und bietet gleich den nächsten Schritt an.

Falsche Eingaben meldet die Seite direkt am Feld auf Deutsch (z. B. „Kurzname: nur
Kleinbuchstaben, Ziffern, - und _ …“).

**Adresse korrigieren:** Proxmox-VMs ohne laufenden Gast-Agenten bekommen als Platzhalter die
Adresse des Proxmox-Servers. Dann auf der Seite des Servers unter **Allgemein → Bearbeiten** die
echte IP eintragen – Nodvard Deck überschreibt eine von Hand gesetzte Adresse nicht mehr mit dem
Platzhalter, nur mit einer Adresse, die der Gast selbst meldet. Bei Proxmox-*Knoten* überschreibt
die Erweiterung die Adresse beim nächsten Abgleich wieder.

Hat der Server ein gespeichertes **SSH-Passwort**, wird es beim Ändern der Adresse gelöscht; du gibst es
danach neu ein. SSH-Schlüssel bleiben. Das gilt auch, wenn der Abgleich einer Erweiterung die Adresse
ändert (etwa bei einem Proxmox-Knoten); bei VMs und Containern, die ihre Adresse selbst melden und deren
Server-Schlüssel schon gemerkt ist, bleibt das Passwort.

### 5.2 SSH-Zugang einrichten – drei Wege

Auf der Seite des Servers, Karte **SSH-Zugang**:

- **SSH-Schlüssel erzeugen (empfohlen).** Nodvard Deck erzeugt einen eigenen Schlüssel nur für
  diesen Server. Du gibst nur den **Benutzer auf dem Server** an (Vorgabe `nodvard`, ein eigener
  Benutzer nur für Nodvard Deck; wird angelegt, falls es ihn noch nicht gibt – bei Proxmox-Knoten `root` nehmen) und den Port (22).
  Ältere Zugänge heißen vielleicht noch `lattice`, sie laufen unverändert weiter. Danach zeigt die Seite den
  **Einrichtungsbefehl** (5.3).
- **Passwort eingeben.** Für einen Benutzer, den es auf dem Server schon gibt. Das Passwort wird
  verschlüsselt gespeichert und nie wieder angezeigt. Ein Schlüssel ist sicherer.
- **Eigenen Schlüssel einfügen.** Den privaten Schlüssel (Text, der mit `-----BEGIN …` beginnt,
  **ohne Passphrase**) einfügen. Danach gibt es auch dazu den Einrichtungsbefehl für den Server.

Was du eintippst oder einfügst, wird beim Abschicken aus dem Formular gelöscht.

### 5.3 Einrichtungsbefehl auf dem Server ausführen

Der Befehl ist **ein einziger Einzeiler**. Mit **Kopieren** (geht auch auf `http://`, ohne
sicheren Kontext) in die Zwischenablage, auf dem Server anmelden (per `ssh`, am Bildschirm oder über die
Konsole deiner VM-Verwaltung), einfügen, Enter. Er

- legt den Benutzer an, falls nötig (ohne Passwort, Anmeldung nur mit dem Schlüssel),
- trägt den öffentlichen Schlüssel in `~/.ssh/authorized_keys` ein (mit `restrict,pty`: Terminal,
  Dateien und Befehle gehen, Weiterleitungen nicht). Läuft auf dem Server Dropbear (etwa bei DietPi), nimmt er
  stattdessen `no-port-forwarding,no-agent-forwarding,no-X11-forwarding`: Dropbear kennt `pty` nicht und verwirft
  sonst die ganze Zeile,
- richtet auf Wunsch **Root-Rechte ohne Passwort** und die **Gruppe docker** ein (siehe [6.](#6-root-rechte-ohne-passwort-und-gruppe-docker)).

Unter „Was macht der Befehl?“ steht derselbe Ablauf lesbar, Zeile für Zeile. Der Befehl läuft als root direkt, sonst
über `sudo` – er funktioniert also auf Proxmox (kein sudo) ebenso wie auf Debian oder dem Pi. Er lässt sich gefahrlos
mehrfach ausführen: Einen schon eingetragenen Schlüssel erkennt er auch mit dem älteren Kommentar `lattice@…` und
trägt ihn nicht doppelt ein. Eine auskommentierte Zeile (`# …`) zählt dabei nicht: Wer Nodvard Deck so gesperrt hat,
bekommt beim nächsten Einrichten wieder eine gültige Zeile, die auskommentierte bleibt stehen. Steht der Schlüssel
ohne Einschränkung in der Datei (etwa von Hand eingetragen), setzt der Befehl `restrict,pty` bzw. die Optionen für
Dropbear davor und meldet „Der schon eingetragene Schlüssel für Nodvard Deck ist jetzt eingeschränkt …“. Zeilen mit
eigenen Optionen, etwa `from="…"`, bleiben, wie sie sind. Zum Prüfen des Geschriebenen braucht er dabei `cmp` (Paket
diffutils); fehlt es, ändert er nichts und nennt die Abhilfe.

Hast du einen Server mit Dropbear schon mit einer älteren Version eingerichtet und „Verbindung prüfen“ meldet „Der
Server hat den Schlüssel nicht akzeptiert“, dann richte ihn einfach noch einmal ein (mit Passwort oder mit dem
Befehl). Der Befehl ersetzt dabei genau seine eigene alte Zeile `restrict,pty …` durch die Zeile für Dropbear
(Meldung „Schlüssel für Dropbear angepasst.“); andere Zeilen mit demselben Schlüssel und eigenen Optionen bleiben,
wie sie sind.

#### Mit Passwort einrichten (Nodvard Deck führt den Befehl aus)

Du musst den Befehl nicht selbst einfügen: Im Kasten **„Server für diesen Schlüssel einrichten“** wählst du statt
„Befehl selbst einfügen“ den zweiten Weg, **„Mit Passwort einrichten (Nodvard Deck führt den Befehl aus)“**. Das gibt
es für Zugänge mit Schlüssel (erzeugt oder eigener) auf Linux-Servern. Es ist etwas anderes als der Zugang „Passwort
eingeben“ aus [5.2](#52-ssh-zugang-einrichten--drei-wege): Hier brauchst du das Passwort nur für diese eine
Einrichtung, und es wird nie gespeichert. Danach meldet sich Nodvard Deck mit dem Schlüssel an.

- **Was du eingibst:** einmal das Konto (Vorgabe `root`) und sein Passwort. Das Konto muss sich per SSH mit Passwort
  anmelden dürfen: `root` oder ein Konto mit sudo. Debian und Ubuntu lassen `root` meist nicht mit Passwort herein; nimm
  dann ein Konto mit sudo (das Formular sagt das am Feld). Bei einem Konto ohne `root` nimmt sudo dasselbe Passwort;
  nimmst du den Haken „Für sudo dasselbe Passwort nehmen“ weg, muss das Konto sudo ohne Passwort dürfen.
- **Was passiert:** Nodvard Deck meldet sich damit **einmal** an, führt **genau denselben Befehl** aus wie in 5.3 (die
  Haken „Root-Rechte ohne Passwort“ und „Gruppe docker“ darüber gelten auch hier), schließt die Verbindung und prüft
  danach mit dem neuen Schlüssel wie bei „Verbindung prüfen“ ([5.4](#54-verbindung-prüfen)). Darunter stehen die
  Meldungen des Befehls und das Ergebnis der Prüfung. Hatte der Server noch keinen Standard-Zugang, wird dieser Zugang
  der Standard. Beim Ersetzen eines Zugangs ([5.5](#55-zugang-ersetzen-löschen-gruppen)) zählt die Prüfung wie „Neuen
  Zugang prüfen“; danach kannst du „Neuen Zugang verwenden“ drücken.
- **Das Passwort wird nie gespeichert:** nicht in der Datenbank, nicht im Tresor, nicht im Protokoll und nicht im Log.
  Das Feld wird beim Absenden sofort geleert, und der Browser soll es nicht merken. Im Protokoll steht, dass es einen
  Versuch gab („Server mit Passwort einrichten“, `host.setup_with_password`), mit Server, Konto und Ergebnis – nie das
  Passwort.
- **Der Server-Schlüssel muss bekannt sein.** Das Passwort geht nie an einen Server, dessen Fingerabdruck Nodvard Deck
  noch nicht kennt oder der sich geändert hat. Kennt Nodvard Deck ihn noch nicht, zeigt das Formular den Fingerabdruck
  zum Vergleichen und Bestätigen (wie in [5.4](#54-verbindung-prüfen)); danach gibst du das Passwort noch einmal ein.
  Bei einem geänderten Schlüssel gibt es nur die Warnung, ohne Bestätigen-Knopf.
- **Grenzen:** Du brauchst die Rechte `hosts.write` **und** `hosts.execute`, denn der Befehl läuft auf dem Server als
  root, wie beim Terminal. Nodvard Deck bremst Versuche: je Adresse und Port höchstens 5, je Benutzer 10 in 10 Minuten
  (dazu 30 Aufrufe je Benutzer in 5 Minuten), und je Server läuft nur eine Einrichtung gleichzeitig; dann steht „Zu
  viele Versuche mit Passwort …“ bzw. „Für diesen Server läuft gerade schon eine Einrichtung …“. Die Zähler liegen im
  Speicher und beginnen nach einem Neustart von vorn. Der Befehl bekommt bis zu 90 Sekunden. Reißt die Verbindung ab
  oder läuft die Zeit aus, kann er auf dem Server trotzdem teilweise gelaufen sein: „Verbindung prüfen“ zeigt, was
  schon da ist, und ein neuer Versuch ist gefahrlos, weil sich der Befehl mehrfach ausführen lässt.
- **Ohne HTTPS** warnt dich das Formular, bevor du das Passwort abschickst: Es geht dann unverschlüsselt von deinem
  Browser zu Nodvard Deck. Im eigenen Heimnetz ist das meist in Ordnung, in einem fremden Netz nicht; sicherer ist dann
  „Befehl selbst einfügen“. Beim Aufruf direkt auf dem Rechner (`localhost`) erscheint die Warnung nicht. Gesperrt wird
  nichts.
- **Noch nicht möglich:** Proxmox per Passwort, Windows-Server (dafür gibt es keinen Einrichtungsbefehl) und der
  Einrichtungsassistent.

### 5.4 Verbindung prüfen

Karte **Verbindung prüfen** (bis zu 30 Sekunden). Die Seite zeigt Punkt für Punkt, was klappt
und was nicht – mit Hinweis, was zu tun ist:

1. **Server erreichbar** – Adresse, Port, Firewall, läuft SSH?
2. **Server-Schlüssel** – siehe unten.
3. **Anmeldung** – nimmt der Server den Schlüssel bzw. das Passwort?
4. **Root-Rechte** – geht `sudo` ohne Passwort? (Nur ein Hinweis, kein Fehler: Die schlanke
   Variante ohne Root-Rechte ist erlaubt.)
5. **Was Erweiterungen brauchen** – z. B. Docker ohne sudo für das Modul Docker.
6. **Betriebssystem** – z. B. „Debian 12 · aarch64 · Raspberry Pi 4 Model B“.

**Der Server-Schlüssel (Fingerabdruck):** Jeder SSH-Server hat einen eigenen Fingerabdruck.

- **Neu:** Beim ersten Mal zeigt die Prüfung den Fingerabdruck und hält an. Bis dahin ging **kein
  Passwort und kein Schlüssel** an den Server. Vergleiche den Fingerabdruck mit dem auf dem Server
  (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`, die Seite zeigt den Befehl zum
  Kopieren) und drücke **„Fingerabdruck stimmt – bestätigen“**. Danach prüft die Seite gleich weiter. Im eigenen
  Heimnetz und bei einem frisch eingerichteten Server ist Bestätigen in Ordnung; bei einem Server im Internet
  vergleiche den Fingerabdruck wirklich.
- **Geändert:** Zeigt der Server später einen anderen Fingerabdruck, erscheint eine deutliche
  Warnung – **ohne** Bestätigen-Knopf. Das passiert nach einer Neuinstallation des Servers, aber
  auch, wenn sich jemand dazwischenschaltet. War es eine Neuinstallation: Karte **Server-Schlüssel**
  → **Vergessen**, dann erneut prüfen und den neuen Fingerabdruck bestätigen. Wenn nicht: erst
  herausfinden, warum.
- Zu viele Prüfungen hintereinander bremst die Seite („Zu viele Prüfungen. Bitte in N Minuten …“).

Ab der Liste geht es auch schneller: **Prüfen** neben jedem Server zeigt das Ergebnis kurz
(„4 von 5 in Ordnung“), und die Server-Seite (`/hosts/<ID>`) hat eine Karte **Zugang** mit
„Verbindung prüfen“.

**Wann der Zugang grün ist:** In der Liste und auf der Server-Seite wird das Abzeichen des SSH-Zugangs erst grün, wenn
der Server antwortet und sich Nodvard Deck dort mit diesem Zugang wirklich angemeldet hat, etwa bei „Verbindung prüfen“
oder wenn ein Modul den Server per SSH abfragt. Dass der Server antwortet, heißt nur, dass sein SSH-Port offen ist. Bis
dahin bleibt das Abzeichen grau, und daneben steht gelb, was fehlt: „noch nicht geprüft“, „noch keine Verbindung“ (der
Server hat nie geantwortet), „Anmeldung noch nicht bestätigt“ (bei einem Schlüssel fehlt meist noch der
Einrichtungsbefehl), „keine Antwort“ oder „Anmeldung klappt nicht“. Nach einer neuen Adresse, einer abgelehnten
Anmeldung, einem geänderten Server-Schlüssel oder einem neuen Standard-Zugang ohne frische Prüfung gilt die Anmeldung
wieder als nicht bestätigt, bis sie das nächste Mal klappt.

> **Hinweis:** Ob Nodvard Deck einen neuen Server-Schlüssel beim allerersten Verbinden (Terminal,
> Überwachung, …) von allein merkt, stellst du unter **Einstellungen → Server & Zugänge** in der Karte
> **„Neue Server-Schlüssel“** ein. Bei neuen Installationen ist **„Neue Server-Schlüssel erst nach meiner
> Bestätigung merken“** an: Dann merkt sich Nodvard Deck einen Schlüssel nur über „Verbindung prüfen“,
> vorher geht kein Passwort und kein Schlüssel an den Server. Installationen, die schon vor dieser
> Einstellung eingerichtet waren, bleiben auf „aus“ und merken weiter von allein, bis du den Schalter
> einschaltest. Die Umgebungsvariable `NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS` (`true` oder `false`; in der
> Compose-Datei unter `environment:`, siehe das Beispiel in [2.](#2-einrichtungsassistent-setup); in
> `deploy/docker-compose.yml` des Repositorys ist die Zeile vorbereitet) übersteuert den Schalter: Dann
> zeigt die Karte deren Wert, und Speichern meldet einen Fehler.
> Nach **„Vergessen“** musst du den neuen Fingerabdruck immer bestätigen, egal wie das eingestellt ist –
> bis dahin verbindet sich Nodvard Deck nicht mehr mit dem Server.
> Bereits gemerkte Schlüssel sind nie betroffen. Sauberer ist immer: zuerst prüfen und bestätigen.

### 5.5 Zugang ersetzen, löschen, Gruppen

- **Ersetzen:** Karte SSH-Zugang → **Ersetzen** legt einen *neuen* Zugang an (Schlüssel erzeugen,
  Passwort oder eigener Schlüssel); der alte bleibt Standard. Führe den Befehl des neuen Zugangs
  auf dem Server aus, drücke **Neuen Zugang prüfen** und danach **Neuen Zugang verwenden**. Erst
  nach einer erfolgreichen, frischen Prüfung (höchstens 10 Minuten alt) wird der alte Zugang
  gelöscht – so sperrst du dich nicht aus. War der alte Zugang ein Schlüssel, **bleibt sein öffentlicher Schlüssel auf
  dem Server in `~/.ssh/authorized_keys` stehen**; dort bei Bedarf selbst entfernen. Bei einem Passwort ändert sich auf
  dem Server nichts. Die Prüfung des neuen Zugangs ändert nichts an der Bestätigung des alten (5.4); erst wenn du den
  neuen verwendest, gilt seine Prüfung.
- **Zugang löschen:** Terminal, Updates und Überwachung erreichen den Server dann nicht mehr. Ein
  Schlüssel bleibt auf dem Server eingetragen; bei einem Passwort ändert sich dort nichts.
- **Gruppen:** Auf der Liste unter **Gruppen** anlegen, umbenennen, löschen; auf der Seite des
  Servers per Haken zuordnen. „Skripte“ kann ein Skript auf allen Servern einer Gruppe laufen lassen.
- **Server löschen:** Karte „Gefahrenbereich“ (oder „Löschen“ auf der Server-Seite). Zur Sicherheit
  den Kurznamen eintippen. Gelöscht werden auch SSH-Zugang und gemerkter Server-Schlüssel;
  Verläufe in Nodvard Shield bleiben mit dem alten Namen stehen. Von Proxmox eingelesene Server
  tauchen beim nächsten Abgleich wieder auf – dann ohne SSH-Zugang. Läuft gerade eine Aktion auf
  dem Server, geht das Löschen nicht.
  Ist das Modul, das den Server eingelesen hat, ausgeschaltet, sagt der Dialog
  stattdessen, dass der Server nicht wiederkommt, solange das Modul aus bleibt.

**Testen:** Im Menü „Terminal“ taucht der Server jetzt auf (die Terminal-Erweiterung schaltet sich mit dem
ersten SSH-Zugang ein, falls du sie nicht selbst ausgeschaltet hattest).
Eine Sitzung öffnen – klappt die Anmeldung, klappt sie auch für alle anderen Erweiterungen. Eine
Terminal-Sitzung ohne Eingabe und Ausgabe schließt sich nach 30 Minuten von selbst.

## 6. Root-Rechte ohne Passwort und Gruppe docker

Meldet sich Nodvard Deck auf einem Server **nicht als root** an (z. B. auf dem Pi), führt es
root-Befehle über `sudo -n` aus, also ohne Passwortabfrage. Verlangt sudo ein Passwort,
scheitert die Funktion mit „Keine root-Rechte: Das Dashboard ist auf diesem Server nicht
als root angemeldet und darf sudo nicht ohne Passwort nutzen.“

Den Einrichtungsbefehl (5.3) musst du dafür **nicht von Hand ändern**: Die Seite setzt die
passenden Haken selbst, weil die Erweiterungen melden, was sie brauchen. Dieselben Haken gelten, wenn du den Server
mit Passwort einrichtest (Nodvard Deck führt dann denselben Befehl aus).

- **„Root-Rechte ohne Passwort“** – nötig für (die Seite nennt es hinter dem Haken):
  - **Nodvard Shield, Virenschutz:** Datei in Quarantäne verschieben, wiederherstellen oder löschen,
    Lynis-Härtungsaudit, Werkzeuge installieren (ClamAV, Lynis, Signaturen, Fail2ban,
    automatische Updates). Scans laufen auch ohne root, prüfen dann aber nur Dateien, die der
    Benutzer lesen darf. Ob ClamAV und Lynis installiert sind, erkennt Nodvard Shield auch ohne root
    (Lynis liegt bei Debian und dem Betriebssystem des Pi unter `/usr/sbin`); nach einer Installation fragt es den
    Server gleich neu ab. Das **Härtungs-Audit** läuft auf dem Server im Hintergrund mit niedriger
    Priorität und kann je nach Server einige Minuten bis über eine Stunde dauern (Obergrenze 3 Stunden).
    Nach dem Klick steht sofort „Audit läuft seit …“ beim Server, ein zweiter Klick startet kein zweites
    Audit, und das Dashboard darf währenddessen neu starten. Die häufigsten Hinweise von Lynis stehen im Ergebnis mit
    deutscher Überschrift; ein Klick darauf zeigt „Was heißt das?“ und „Was kannst du tun?“ (vieles ist für ein Heimnetz
    bewusst nicht nötig, das steht dann dabei). Ganz oben stehen „Deine wichtigsten Baustellen“: höchstens fünf Hinweise
    mit der größten Wirkung auf den angezeigten Servern, mit den Servern, auf denen sie vorkommen. Die Protokolle liegen auf dem Server unter
    `/var/lib/nodvard-shield-audit/`. Scans und Echtzeit-Wächter laufen auf einem Server nie
    gleichzeitig; für diese Sperre legt Shield dort einen kleinen Ordner an: `/run/nodvard-shield` (als
    root) bzw. `~/.nodvard-shield`. In die Quarantäne kommt eine Datei nur, wenn ClamAV sie dabei selbst noch einmal
    als Fund meldet, erst an ihrem Ort und dann in einem Prüfordner, in den nur root schreibt; das gilt mit und ohne
    Hilfsskript. Fehlt ClamAV oder antwortet es nicht, bleibt die Datei, wo sie ist. Wiederhergestellt wird nur an den
    Ort und mit den Rechten, die Shield beim Verschieben auf dem Server hinterlegt hat (auch besondere wie `4755` oder
    `044`), ebenfalls mit und ohne Hilfsskript. Rückfrage und Freigabe nennen den Pfad aus dem Fund deshalb nur „laut
    Fund“; nach dem Zurücklegen nennt Shield den Ort, an den die Datei wirklich ging, und weicht er vom Fund ab, steht
    das auch am Fund. Fehlt der Eintrag oder ist er ungültig (etwa bei einer Datei, die mit Nodvard Deck 0.8 oder älter
    in die Quarantäne kam), bleibt die Datei dort, und du legst sie auf dem Server von Hand zurück oder löschst sie.
    Einen Fund, den du ignoriert oder wiederhergestellt hast, verschiebt der nächste Scan nicht noch einmal in die
    Quarantäne (außer ClamAV meldet dieselbe Datei mit einer anderen Signatur). Zurück geht das unter „Funde &
    Quarantäne“ mit dem Filter „Alle, inkl. erledigt“ und „Wieder beachten“: Der Fund ist dann wieder offen, und der
    nächste Scan meldet die Datei wieder.
  - **Schutzwert** (0 bis 100, auf der Übersicht): zählt den Virenschutz (ClamAV, Signaturen, Scans, offene Funde),
    die Härtung und außerdem offene Sicherheitsupdates, nötige Neustarts, Fail2ban, die SSH-Einstellungen (root oder
    Anmeldung mit Passwort, nach der Zahl der betroffenen Server) und offene kritische Sicherheitsereignisse (je 5 Punkte,
    höchstens 15). Darunter steht, was ihn gerade senkt und wie viele Punkte das kostet. Ist die letzte Update- oder
    Einbruchschutz-Prüfung eines Servers gescheitert oder älter als drei Tage, zählt sein alter Stand nicht; das steht
    dann als Grund „zuletzt nicht geprüft (ohne Abzug)“ da.
  - **Update-Zentrale:** Updates einspielen und neu starten. Das Neuladen der Paketlisten vor der
    Prüfung braucht root – ohne bleibt es beim zuletzt geladenen Stand. Updates laufen auf dem
    Server im Hintergrund (mit systemd als `lattice-upgrade-…`, sonst per `setsid`) und laufen
    weiter, auch wenn die Verbindung abreißt oder das Dashboard neu startet. Das Protokoll jedes
    Laufs liegt 30 Tage unter `/var/lib/nexus-updates/`. Das Dashboard wartet 45 Minuten auf das
    Ergebnis; dauert ein Lauf länger, steht er zunächst als „Keine Rückmeldung“ da (beendet wird
    er nie), das Dashboard fragt aber noch bis zu etwa 3 Stunden nach, berichtigt dann den Eintrag
    und meldet das Ergebnis per Push. Startet das Dashboard in dieser Zeit neu, bleibt der Eintrag
    stehen – dann im Protokoll auf dem Server nachsehen.
  - **Einbruchschutz und Datei-Wächter:** SSH-Log, Fail2ban-Status, Prozesse hinter offenen Ports,
    SSH-Einstellungen, Prüfsummen von Dateien wie `/etc/shadow`. Ohne root nur eingeschränkt –
    Nodvard Deck sagt das dann dazu, statt falsch Entwarnung zu geben. IP-Adressen per Fail2ban
    sperren/entsperren geht nur mit root.
    Läuft Fail2ban, zeigt jeder Server unter „Gesperrte Adressen“ die gesperrten IPs, nach Jail (der Schutzregel für
    einen Dienst, etwa `sshd` für SSH) sortiert, mit „Entsperren“ an jeder Adresse. Ab 20 Adressen gibt es eine Suche,
    gezeigt werden höchstens 200 auf einmal. Darunter sperrst du eine einzelne Adresse von Hand, im Jail `sshd` oder
    einem anderen, den der Server hat; welche das sind, weiß Shield erst nach einer Prüfung des Servers. Eine Sperre
    endet meist nach der Sperrzeit des Jails von selbst. Nicht gesperrt werden Adressen, die zu keinem anderen Rechner
    gehören (etwa `127.0.0.1`), die Adresse deines Browsers (hinter einem Reverse-Proxy sicher erst, wenn er unter
    „Vertrauenswürdiger Proxy“ steht; sonst vergleicht Shield zusätzlich die Angaben des Proxys in `X-Forwarded-For`)
    und die Adresse, über die Nodvard Deck den Server erreicht; sonst sperrt man sich selbst aus. Die letzte prüft auch der Server selbst, bevor er sperrt
    (aus `SSH_CLIENT` bzw. `SSH_CONNECTION`); kann er sie nicht feststellen, sperrt er nichts. Hat der Server keinen
    Jail für SSH, bietet Shield bei Angreifern kein „Sperren“ an. War eine Adresse beim Entsperren schon frei, sagt
    Shield das, statt einen Fehler zu melden.
    Einen neuen offenen Port meldet der Einbruchschutz einzeln („Neuer offener Port“). Programme, die bei jedem Start
    oder je Anfrage zufällige Ports wählen (NFS, Tailscale, Pi-hole und andere DNS-Dienste), zählen als ein Eintrag. Für
    ein Programm mit eigenem Portbereich, etwa einen Spieleserver, trägst du unter Einstellungen → Erweiterungen →
    „Nodvard Shield“ → „Erweitert“ → „Eigene Portbereiche je Programm“ Programm, Protokoll und Bereich ein. Neue Ports
    darin melden dann nicht mehr einzeln. Den Programmnamen kann sich jedes Programm selbst geben, auch eine
    Schadsoftware: nur Programme eintragen, denen du vertraust, und die Bereiche klein halten. Ports unter 1024 zählen
    nur mit dem Häkchen „Auch Ports unter 1024“. Ein Bereich umfasst höchstens 1000 Ports (ein größerer zählt nur bis
    dahin), mehr nur mit dem Häkchen „Breiter Bereich“; ein breiter Bereich beginnt immer frühestens bei 1024, SSH und
    andere feste Dienste bleiben also sichtbar. Es zählen höchstens 50 Bereiche.
  - **Server-Zustand:** Dienste (systemd) neu starten. Danach sieht Deck bis zu 45 Sekunden lang nach, ob der Dienst läuft
    und laufen bleibt (zweimal im Abstand von 10 Sekunden ohne neuen Start; das Nachsehen braucht kein root). Fällt er
    gleich wieder aus oder startet er immer wieder neu, gilt die Aktion als fehlgeschlagen und sagt das.
  - **Docker im Modus „Nur bedienen“** (Server nur mit der Markierung `docker-bedienen`): Container ansehen, Logs lesen,
    starten, stoppen und neu starten über das Hilfsskript `docker-root`, ohne die Gruppe docker.
- **„Zur Gruppe docker hinzufügen“** – nötig für das Modul Docker und die KI-Container-Wache von
  Nodvard Shield; sie rufen `docker` ohne sudo auf. Die Gruppe wird nur eingetragen, wenn es sie auf
  dem Server gibt. Einem Server nur mit der Markierung `docker-bedienen` bietet die Seite die Gruppe nicht an; dort
  reicht das Hilfsskript.

Ohne beide Haken bekommst du die **schlanke Variante**: eigener Benutzer, nur Schlüssel, keine
Root-Rechte. Die reicht für Terminal, Dateien und Skripte.

**Was der Haken „Root-Rechte“ genau tut:** Das hängt davon ab, welche Module auf diesem Server root brauchen.

- **Nur die Hilfsskripte (Standard):** Braucht hier mindestens ein Modul root und hat jedes davon ein Hilfsskript
  (Nodvard Shield, Server-Zustand und Docker im Modus „Nur bedienen“ haben eins), richtet der Haken nur diese Skripte
  ein (siehe „Hilfsskripte“ unten). Jedes bekommt eine
  eigene Regel `<benutzer> ALL=(root) NOPASSWD: /usr/local/lib/nodvard-deck/<modul>-root`, eine volle sudo-Regel gibt es
  nicht. Der Benutzer darf per sudo genau diese Skripte starten, und was sie tun, steht in ihnen selbst (etwa Updates
  einspielen, Quarantäne, Dienste neu starten). Unter „Was macht der Befehl?“ kannst du jedes Skript lesen.
  Vorher prüft der Befehl, ob sich die Skripte sicher einrichten lassen: `sudo`, `base64`, `sha256sum` und `stat` müssen
  da sein, jeder Ordner über `/usr/local/lib/nodvard-deck` muss ein echter Ordner sein, der root gehört und in dem außer
  root niemand schreiben darf: keine Gruppe, auch nicht die Gruppe root, und kein anderer. Eine ACL kann dabei nur
  schreiben lassen, wenn die Gruppenbits es erlauben (sie sind dann die Maske), deshalb reicht der Blick auf sie. Gibt
  es `/usr/local/lib/nodvard-deck` schon, gilt für ihn dasselbe (sonst legt der Befehl ihn an). Klappt das nicht, ändert
  er **nichts**, nennt den Ordner, den Grund und die Abhilfe (etwa `chmod g-w /usr/local/lib` oder
  `mkdir -p /usr/local/lib`) und endet mit einem Fehler; „Mit Passwort einrichten“ zeigt dieselben Zeilen. Ältere
  Debian lassen die Gruppe staff in `/usr/local` und `/usr/local/lib` schreiben; dort hilft als root
  `chmod g-w /usr/local /usr/local/lib`. Dann den Grund beheben oder, außer wenn sudo fehlt, „Volle Rechte“ wählen und noch
  einmal einrichten. Docker im Modus „Nur bedienen“ hilft „Volle Rechte“ nicht: Es arbeitet nur über sein Hilfsskript,
  und das sagt der Befehl dann auch.
- **Volle Rechte:** Braucht ein Modul root und hat kein Hilfsskript, oder setzt du unter „Erweitert“ das Häkchen
  „Volle Rechte“, kommt die volle Regel dazu. Das Häkchen brauchst du nur, wenn du über Nodvard Deck eigene Befehle mit
  sudo startest, etwa in Skripten.
- **Kein Modul braucht root:** Dann richtet der Haken gar kein sudo ein, auch keine volle Regel. Die Seite sagt das unter
  dem Haken. Willst du trotzdem sudo, etwa für eigene Befehle in Skripten, setz unter „Erweitert“ „Volle Rechte“.

Eine schon vorhandene volle Regel nimmt der Befehl nur zurück, wenn du es ausdrücklich willst: Ein Server, der sie hat,
läuft weiter wie bisher, auch wenn du den Befehl dort noch einmal ausführst. Willst du sie loswerden, führ den Befehl
zuerst noch einmal aus und drück „Verbindung prüfen“. Hat dann jedes Hilfsskript seine eigene Regel, steht bei
„Root-Rechte“ gelb „sudo ohne Passwort für alles ist hier nicht nötig. Die Hilfsskripte reichen.“, und unter „Braucht
Aufmerksamkeit“ in der Übersicht steht ein dezenter Hinweis (alle 6 Stunden geprüft, mit × ausblendbar). Der Hinweis
führt zum Einrichtungsbefehl, dort ist unter „Erweitert“ schon „Volle sudo-Regel entfernen“ angekreuzt. Führ den Befehl
aus (selbst einfügen oder „Mit Passwort einrichten“) und prüf noch einmal: Bei „Root-Rechte“ steht dann „sudo erlaubt
nur die Hilfsskripte der Module, keine volle Regel. Das reicht.“

So geht der Befehl dabei vor, damit du dich nicht aussperrst:

- Er richtet zuerst wie immer die Hilfsskripte und ihre Regeln ein. Geht dabei etwas schief, rührt er die volle Regel
  nicht an.
- Gibt es keine Regel von Nodvard Deck (nur Links, andere Inhalte oder gar keine), sagt er das und ändert nichts.
- Er löscht nur `/etc/sudoers.d/nodvard-<benutzer>` und `lattice-<benutzer>`, und nur, wenn die Datei genau die Zeile
  `<benutzer> ALL=(root) NOPASSWD: ALL` enthält. Links und Dateien mit anderem Inhalt bleiben stehen, mit Hinweis.
- Vorher fragt er jedes Skript über sudo nach seiner Version. Das zeigt nur, dass es läuft (noch mit voller Regel);
  antwortet eins nicht, bleibt die volle Regel stehen.
- Nach dem Löschen prüft er die sudo-Konfiguration mit `visudo -c` (wenn sie vorher in Ordnung war) und fragt jedes
  Skript noch einmal, jetzt ohne volle Regel. Klappt etwas davon nicht oder bricht der Lauf ab (etwa weil die Verbindung
  reißt), legt er die Regel sofort wieder an. Schlüssel und Anmeldung ändert er nie.
- Darf der Benutzer danach über eine fremde Regel weiter alles (etwa `010_pi-nopasswd` auf Raspberry Pi OS oder die
  Gruppe `sudo` mit NOPASSWD), sagt er das. Diese Regel bleibt stehen, ob sie weg kann, entscheidest du. Solange sie
  gilt, zeigt die Prüfung nicht, ob die Hilfsskripte allein reichen.

Danach gehen eigene Befehle mit sudo über Nodvard Deck nicht mehr. Bringt ein Update von Nodvard Deck neue
Hilfsskripte, haben die Module dort erst wieder root, wenn du den Server noch einmal einrichtest; die Übersicht sagt es
dir.

Willst du die volle Regel zurück, richte den Server mit „Volle Rechte“ noch einmal ein. Brauchst du sie für eigene
Befehle mit sudo, setz beim Server unter „Bearbeiten“, „Erweitert“ das Häkchen „Volle sudo-Regel gewollt“: Dann bleibt
„Root-Rechte“ grün, und der Hinweis kommt nicht. Braucht auf dem Server gar kein Modul root, bringt der Befehl keine
Hilfsskripte mit und kann die Regel darum nicht entfernen. Brauchst du sudo dann nicht für eigene Befehle über Nodvard
Deck, lösch sie selbst (`sudo rm /etc/sudoers.d/nodvard-<benutzer>`, auf älteren Servern `lattice-<benutzer>`).
„Verbindung prüfen“ zeigt danach bei „Root-Rechte“: „Kein eingeschaltetes Modul braucht hier root, sudo ohne Passwort
ist nicht nötig.“

Die volle Regel ist `/etc/sudoers.d/nodvard-<benutzer>` mit
der Zeile `<benutzer> ALL=(root) NOPASSWD: ALL`. Die Zeile wird erst in eine Probedatei geschrieben
und mit `visudo -c` geprüft; nur dann kommt sie mit den Rechten `0440` (root) an ihren Platz.
Danach prüft `visudo` die eingetragene Datei und die ganze Konfiguration noch einmal. Bei einem Fehler wird die Datei
sofort wieder entfernt („sudo-Regel zurückgenommen“), darunter steht, was `visudo` meldet. Meldet `visudo -c` schon
**vor** der Einrichtung einen Fehler in einer fremden Datei (z. B. „bad permissions, should be mode 0440“, weil eine
Datei in `/etc/sudoers.d` die Rechte `0644` hat), zählt nur die Prüfung der eigenen Dateien. Der Befehl nennt dann die
fremde Datei im Hinweis; mit `sudo chmod 0440 <Datei>` und `sudo chown root:root <Datei>` ist sie in Ordnung. Eine ältere Regel `lattice-<benutzer>` löscht der Befehl erst nach dieser
Prüfung und nur, wenn sie aus genau der Zeile oben besteht; sonst bleibt sie stehen, und du bekommst einen Hinweis.
Ist `sudo` gar nicht installiert, sagt der Befehl das
(als root `apt install sudo`; bei Proxmox am besten gleich als `root` anmelden).

**Das ist praktisch root – die Seite sagt es dir auch:** Wer in der Gruppe `docker` ist oder die volle sudo-Regel
hat, kann auf dem Server praktisch alles. Nodvard Deck startet ohne Hilfsskript einige root-Befehle
als `sudo -n sh -c '…'`, darum braucht es dann die volle sudo-Regel, und wer `sh` als root starten darf,
darf alles. Die Sicherheit kommt daher, dass **nur**
dieser eine Benutzer die Rechte bekommt, er sich **nur mit dem Schlüssel** anmelden kann und der
Schlüssel verschlüsselt in Nodvard Deck liegt.

**Hilfsskripte:** Mit dem Haken „Root-Rechte“ legt der Befehl die Hilfsskripte der Module
(Nodvard Shield, Server-Zustand und Docker im Modus „Nur bedienen“) nach `/usr/local/lib/nodvard-deck` und erlaubt jedes mit einer
eigenen, engen sudo-Regel. Klappt das für ein Skript nicht, nennt der Befehl den Grund (Ordner oder Datei nicht sicher,
Werkzeug fehlt, Regel von `visudo` abgelehnt). Mit voller Regel ist das ein Hinweis, das Modul nimmt dann die volle
Regel; nur Docker nimmt sie nie, für `docker-root` ist es darum immer ein Fehler. Ohne volle Regel ist es ein Fehler,
denn dann hätte das Modul gar kein root: Ordner und Werkzeuge prüft dort schon
die Vorprüfung, bevor sich etwas ändert (siehe oben); ein beschädigtes Skript oder eine abgelehnte Regel meldet der
Befehl unterwegs mit „FEHLER: …“ und am Ende „Nicht alles hat geklappt“.
Die Module rufen ein Skript nur auf, wenn es auf dem Server Byte für Byte das mitgelieferte ist. Ausnahme ist Docker:
Es ruft auch eine ältere Fassung auf. Kann nur root sie ändern und erlaubt sudo sie, behält Docker damit bis zur neuen
Einrichtung root (bei „Veraltet“ steht dann „Docker ruft es trotzdem auf …“). „Verbindung prüfen“
zeigt je Skript, ob es passt, und bei „Root-Rechte“: „sudo erlaubt nur die Hilfsskripte der Module, keine volle Regel.
Das reicht.“, wenn alle passen. Fehlt ein Skript, steht dort „Fehlt auf dem Server“ (gelb), ohne volle Regel und bei
Docker immer mit dem Zusatz, dass das Modul hier kein root hat; fehlt `docker-root` neben der vollen Regel, ist auch
„Root-Rechte“ gelb. Nach einem Update, das ein neues Skript mitbringt, steht dort „Veraltet“: dann
den Einrichtungsbefehl noch einmal ausführen. Das musst du nicht selbst bemerken: Fehlt ein Skript oder ist es veraltet,
während sudo dort Skripte ausführen darf (volle Regel oder die Regel eines Skripts), steht unter „Braucht
Aufmerksamkeit“ in der Übersicht ein dezenter Hinweis mit Link zur Einrichtung. Nodvard Deck prüft das alle 6 Stunden und bei jedem „Verbindung prüfen“. Mit dem × blendest du den
Hinweis für diesen Server aus, bis ein Update neuere Skripte mitbringt.

**Was die Hilfsskripte dürfen:** Diese Unterbefehle kann der Benutzer des Dashboards per sudo als root aufrufen, auch
am Dashboard vorbei. Jeder prüft seine Argumente selbst und bricht bei allem anderen ab, bevor er etwas tut. Ein Test
ruft jedes Skript in `sh` und `dash` mit über 200 ungültigen Eingaben auf (Optionen statt Namen, Leerzeichen,
Zeilenumbrüche, `;`, `$(…)`, `..`, relative Pfade, überlange Werte) und verlangt, dass dabei kein Programm mit
Nebenwirkung startet. Die volle sudo-Regel erlaubt dagegen alles.

| Skript | Unterbefehl | Was es als root tut | Grenzen |
|---|---|---|---|
| alle | `version` | nennt die Version | keine Argumente |
| `system-root` | `service-restart` | startet einen systemd-Dienst neu und meldet seinen Zustand | nur `.service`, `.socket`, `.timer`; nie SSH, D-Bus, `systemd-*`, `networking`, `NetworkManager`, `dhcpcd`, `wpa_supplicant` oder Nodvard Deck; andere Netzwerkdienste wie `firewalld` oder `wg-quick@…` schon. Docker und containerd auf dem Rechner des Dashboards sperrt nur das Dashboard, das Skript kann diesen Rechner nicht erkennen |
| `docker-root` | `ps`, `stats` | listet Container und ihre Last | keine Argumente |
| `docker-root` | `inspect`, `id`, `id-state`, `state`, `ports` | liest Angaben zu Containern in festen Formaten | nur gültige Namen bzw. IDs, höchstens 200 |
| `docker-root` | `logs` | liest das Protokoll eines Containers | zu Beginn höchstens 5000 Zeilen, auf Wunsch danach mitlaufend; Zeitraum nur in Minuten oder Stunden |
| `docker-root` | `start`, `stop`, `restart` | startet, stoppt oder startet einen Container neu | genau ein gültiger Name; Löschen gibt es nicht |
| `shield-root` | `guard` | liest Anmeldungen aus dem SSH-Protokoll, die wirksamen Einstellungen von sshd (Port, Anmeldung als root, Passwort- und Schlüssel-Anmeldung und ähnliche), Fail2ban, offene Ports samt Bereich für ausgehende Verbindungen sowie Prüfsumme, Rechte, Besitzer, Änderungszeit und Größe der überwachten Dateien und Programme | nur lesend; absolute Pfade ohne `..`, `*` nur bei Dateien |
| `shield-root` | `detect-templates`, `refresh-packages` | liest die Einstellungen von Samba, NFS und Nextcloud bzw. lädt die Paketliste neu | keine Argumente |
| `shield-root` | `quarantine`, `restore`, `delete`, `quarantine-state` | verschiebt eine von ClamAV gemeldete Datei in die Quarantäne, legt sie zurück, löscht sie dort oder nennt ihren Zustand | ClamAV prüft die Datei vorher noch einmal; zurück nur an den Ort, den das Skript selbst notiert hat; keine Links |
| `shield-root` | `ban`, `unban` | sperrt oder entsperrt eine Adresse in Fail2ban | Adresse nur aus Hex-Ziffern, `:` und `.`, höchstens 45 Zeichen; Jail nur aus Buchstaben, Ziffern, `_`, `.` und `-`, höchstens 64 Zeichen. Was keine gültige Adresse ist, lehnt Fail2ban ab |
| `shield-root` | `install` | installiert ClamAV, Lynis, Fail2ban oder unattended-upgrades, lädt neue Virensignaturen | nur diese fünf |
| `shield-root` | `reboot` | startet den Server in einer Minute neu (ohne `shutdown` nach 5 Sekunden) | keine Argumente |
| `shield-root` | `upgrade-start`, `upgrade-run`, `upgrade-poll` | spielt Updates mit apt, dnf oder apk ein und meldet den Stand | feste Arten; einzelne Pakete nur bei Sicherheitsupdates mit apt, Namen aus festen Zeichen |
| `shield-root` | `audit-start`, `audit-run`, `audit-poll`, `audit-stop` | startet, verfolgt oder beendet eine Prüfung mit Lynis | Lauf-ID in fester Form, feste Zeitgrenze, beendet nur den eigenen Lauf |
| `shield-root` | `scan`, `watch` (intern `scan-run`, `watch-run`, `odd-scan`) | prüft Ordner mit ClamAV | absolute Pfade, Zahlen mit Obergrenze, ein Lauf zur Zeit, Zeitgrenze |
| `shield-root` | `realtime-on`, `realtime-off`, `realtime-log`, `realtime-confirm` | schaltet den Echtzeit-Schutz von ClamAV ein oder aus, liest sein Protokoll, prüft gemeldete Dateien erneut | schreibt nur feste Dateien unter `/etc/clamav` und eine Ergänzung für systemd; Ordner absolut ohne `..`, höchstens 50 Dateien |

Welche Dateien `guard` liest, kommt aus den Einstellungen des Datei-Wächters von Nodvard Shield („Überwachte Dateien“).
Wer diese Einstellung ändern darf, bestimmt damit auch, von welchen Dateien root Prüfsumme, Rechte, Besitzer,
Änderungszeit und Größe liest. Den Inhalt dieser Dateien gibt das Skript nie aus.

**Docker auf „Nur bedienen“ umstellen:** Bekommt ein Server, der schon mit der Gruppe docker oder der vollen
sudo-Regel eingerichtet ist, die Markierung `docker-bedienen` statt `docker`, bleibt beides erhalten. Der
Einrichtungsbefehl nimmt weder die Gruppe noch die Regel zurück, der Zugang ist dort also weiter praktisch root.
„Verbindung prüfen“ zeigt das in den Punkten „Nur bedienen: Zugang nicht in der Gruppe docker“ und „Nur bedienen:
keine volle sudo-Regel“ (gelb). Meldet sich der Zugang als `root` an, schränkt „Nur bedienen“ nichts ein; dann ist
„Nur bedienen: Zugang ist nicht root“ gelb, und der Zugang braucht einen eigenen Benutzer. Hat ein Server beide
Markierungen (`docker` und `docker-bedienen`), ist er ein voller Docker-Server: Die Punkte „Nur bedienen: …“ und das
Hilfsskript `docker-root` entfallen dann, Docker läuft über die Gruppe docker. So stellst du um:

1. Den Einrichtungsbefehl mit „Root-Rechte ohne Passwort“ noch einmal ausführen. Er legt `docker-root` ab und bietet die
   Gruppe docker nicht mehr an.
2. „Verbindung prüfen“: Bei „Docker nur bedienen“ muss stehen, dass das Hilfsskript antwortet.
3. Den Zugang aus der Gruppe nehmen: `sudo gpasswd -d <benutzer> docker` (gilt ab der nächsten Anmeldung).
4. Braucht kein anderes Modul hier volle Rechte, die volle Regel entfernen wie oben beschrieben.
5. Noch einmal „Verbindung prüfen“: Die Prüfung meldet sich dafür neu an, die Punkte „Nur bedienen: …“ sind also gleich
   grün, und danach nutzt Nodvard Deck auch sonst nur neue Verbindungen.

Zurück zum vollen Docker-Server: Markierung `docker` wieder setzen und den Einrichtungsbefehl mit „Zur Gruppe docker
hinzufügen“ ausführen.

**Proxmox-Knoten:** Dort meldet sich Nodvard Deck am einfachsten direkt als `root` an (Benutzer
`root` beim Erzeugen eintragen). Dann braucht es kein sudo und keine Haken. Der Schlüssel landet in
`/root/.ssh/authorized_keys`, das bei Proxmox ein Verweis in den Cluster-Ordner ist – er gilt dann
auf **allen Knoten des Clusters**.

**Vorhandenen Benutzer nehmen** (auf dem Pi z. B. `admin`): beim Erzeugen statt `nodvard` dessen
Namen eintragen. `sudo -n true && echo geht-schon` auf dem Server zeigt, ob er sudo schon ohne
Passwort darf (auf Raspberry Pi OS oft beim ersten Benutzer der Fall).

Nicht wundern: Kopierst du den Befehl und führst ihn selbst aus, meldet der Datei-Wächter von Nodvard Shield die neue
sudo-Datei und die neue `authorized_keys` einmal – das warst du. Mit **„Mit Passwort einrichten“** meldet der Befehl Nodvard
Deck genau, was er geändert hat: für die Benutzer- und Gruppendateien und die `authorized_keys` des Deck-Benutzers
Prüfsumme, Rechte und Besitzer vorher und nachher. Die eigenen sudo-Regeln (`nodvard-<benutzer>`, die Regeln der
Hilfsskripte) kennt Nodvard Deck sowieso, es schreibt sie selbst. Der Wächter übernimmt bei seiner nächsten Prüfung still
nur, was genau dazu passt: Der bestätigte Stand ist der von vorher, der jetzige der von nachher, und der Befehl hat die
passende Aktion gemeldet (Benutzer angelegt, in eine Gruppe eingetragen, Schlüssel eingetragen, alte Regel ersetzt). Das
steht im Verlauf als „Nach der Einrichtung durch Nodvard Deck um HH:MM übernommen“, schon erledigt und ohne Meldung.
Alles andere meldet er wie immer: andere Dateien, fremde sudo-Regeln (auch solche, die mit `nodvard` oder `lattice`
beginnen), nur geänderte Rechte oder Besitzer, ein anderer Inhalt als gemeldet und jede spätere Änderung. Jeder Lauf zählt
nur einmal, bei der ersten Prüfung danach. Dasselbe gilt, wenn du den Befehl mit Haken „Root-Rechte“ auf einem
Server erneut ausführst, der noch die alte Regel `lattice-<benutzer>` hat: Dann kommt `nodvard-<benutzer>` dazu,
und die alte fällt weg, wenn sie unverändert ist (siehe oben).

## 7. Proxmox verbinden

**Nur wenn du Proxmox nutzt – sonst überspringst du diesen Abschnitt** (siehe [4.1](#41-ohne-proxmox)).
Ausführlich in **[docs/10-PROXMOX-TOKEN.md](10-PROXMOX-TOKEN.md)**.

Am einfachsten: Erweiterung „Proxmox VE“ einschalten, dann auf der Seite **„Proxmox“** → **„Mit Passwort
verbinden“**. Adresse und Passwort von `root@pam` eingeben, Fingerabdruck des Zertifikats vergleichen (Nodvard Deck
merkt ihn sich und prüft ihn danach bei jeder Anfrage) – Nodvard Deck
legt Benutzer, Rolle und Token in Proxmox selbst an, liest gleich danach alles ein und vergisst das Passwort sofort
([Schneller Weg](10-PROXMOX-TOKEN.md#schneller-weg-mit-passwort-verbinden)). Ist „Backups“ eingeschaltet, bekommt es
dabei gleich einen eigenen Zugang (Schalter „Auch Proxmox-Backups verbinden“) – Schritt 3 unten entfällt dann. Je
Proxmox-Server einmal. Von Hand geht es so:

1. Auf jedem Proxmox-Server (im Beispiel pve1 **und** pve2) einen Benutzer `nodvard@pve` mit
   Token `dashboard` und passender Rolle anlegen.
2. Einstellungen → Erweiterungen → „Proxmox VE“ → „Konfigurieren“ → „Server hinzufügen“
   (Kurzname `pve1`/`pve2`, Adresse `https://…:8006`, API-Token-ID
   `nodvard@pve!dashboard`) → „Speichern“ → unter „Zugangsdaten“ das Geheimnis eintragen.
3. Dasselbe unter „Proxmox-Backups“ mit denselben Kurznamen.
4. Nach spätestens 5 Minuten sind Knoten, VMs und Container da. VMs ohne Gast-Agent
   bekommen die Proxmox-Adresse als Platzhalter – die richtige IP unter Einstellungen →
   Server & Zugänge eintragen ([5.1](#51-server-anlegen)).
5. Von selbst melden die Module außerdem, wenn ein Speicher fast voll ist (Proxmox VE, ab 90 %, einstellbar) und wenn
   die letzte erfolgreiche Sicherung einer VM älter als 3 Tage ist (Backups, einstellbar, mindestens 2 Tage); beides
   unter „Konfigurieren“ des jeweiligen Moduls. Läuft der Backup-Job einer VM seltener (z. B. nur samstags), gilt
   automatisch dessen Abstand, wenn er größer ist; dazu kommen 6 Stunden Luft, und solange gerade eine Sicherung
   läuft, kommt keine Warnung. Proxmox VE meldet außerdem einen ZFS-Pool, der nicht mehr ONLINE ist (eingeschränkt
   als Warnung, ausgefallen als „kritisch“) oder ganz fehlt (etwa nach einem Neustart nicht eingebunden), und einen
   Datenträger, der 10 bis 15 Minuten lang zu heiß ist (Festplatten ab 55 °C, SSD ab 65 °C, NVMe ab 70 °C, einstellbar
   unter „Erweitert“). Die ZFS-Pools stehen auch beim Knoten unter
   „Knotendetails“.

## 8. Push-Nachrichten (ntfy)

Einstellungen → Erweiterungen → **„Push-Nachrichten“** (früher „ntfy-Benachrichtigungen“) einschalten →
„Konfigurieren“. Für ntfy:

- **ntfy-Server:** z. B. `https://ntfy.sh` oder dein eigener. Nodvard Deck verbindet sich immer direkt damit, nie über
  einen Proxy aus der Umgebung (`HTTP_PROXY`, `HTTPS_PROXY`). Kommt der Rechner mit Nodvard Deck nur über einen Proxy ins
  Internet, ist `ntfy.sh` nicht erreichbar; nimm dann einen ntfy-Server im eigenen Netz.
- **Thema (Topic):** der Kanal, den du in der ntfy-App abonnierst. Bei ntfy.sh schwer
  erratbar wählen, denn dort kann jeder ein Thema mitlesen, der den Namen kennt. Der Knopf
  **„Zufällig wählen“** neben dem Feld trägt einen zufälligen Namen ein.
- **Adresse des Dashboards (optional):** `http://192.168.1.10:8080`. Dann öffnet ein Tipp
  auf die Nachricht direkt die passende Seite. Muss mit `http://` oder `https://`
  anfangen, sonst wird sie ignoriert.
- **Mindest-Stufe für Push-Nachrichten:** „Alles (Info, Warnung, Kritisch)“ ist der Standard. Mit
  „Ab Warnung“ oder „Nur Kritisch“ kommen kleinere Meldungen nicht mehr aufs Handy; sie stehen
  weiter unter „Meldungen“. Die Testnachricht kommt immer an.
- **Lautstärke bei Info, Warnung und Kritisch:** wie auffällig eine Meldung der jeweiligen Stufe
  auf dem Handy ankommt, von „1 – ohne Ton“ bis „5 – sehr laut“. Ob Stufe 5 auch bei „Nicht
  stören“ durchkommt, entscheidet dein Handy.
- „Speichern“.
- **Zugangsdaten → „Zugriffstoken (optional)“:** nur, wenn dein ntfy-Server eine Anmeldung
  verlangt.

In der ntfy-App auf dem Handy denselben Server und dasselbe Thema abonnieren. Das Abtippen
sparst du dir: Nach dem Speichern zeigt die Seite unter den Einstellungen einen **QR-Code**. Halte
die Kamera deines Handys darauf, dann öffnet sich die ntfy-App und bietet an, das Thema zu
abonnieren. Der Code enthält nur Server und Thema, nie das Zugriffstoken.

**Testen:** Seite „Nodvard Shield“ → „Briefing senden“. Das Erstellen kann bis zu einer halben
Minute dauern, wenn ein Server nicht antwortet. Danach sagt dir die Seite, ob der Lagebericht
(„Lagebericht – …“) auch als Push-Nachricht zugestellt wurde. Wenn nicht, ist ntfy noch nicht
eingerichtet oder der ntfy-Server nicht erreichbar. Alle Nachrichten stehen außerdem im
Dashboard unter „Meldungen“ – auch dann, wenn die Push-Nachricht nicht ankommt. Dort kannst du eine Meldung mit dem
Papierkorb löschen oder mit „Alle gelesenen löschen“ aufräumen. Von selbst löscht Nodvard Deck jede Nacht Meldungen, die
älter als 90 Tage sind (ungelesene kritische Meldungen bleiben stehen, bis du sie liest); die Zahl stellst du unter
Einstellungen → System → „Zeit & Protokoll“ um.

**Was im Lagebericht steht:** Server, Schutzwert, Bedrohungen, Härtung, Updates, Einbruchschutz und – falls vorhanden – wie
viele Vorschläge (aus allen Modulen) unter „Aktionen“ auf deine Bestätigung warten und welche Container in den letzten
24 Stunden abgestürzt sind. Eine wartende Freigabe oder ein noch offener Absturz macht ihn zu „es gibt etwas zu tun“. Er
kommt auch, wenn alles in Ordnung ist; bleibt er aus, ist Deck selbst vermutlich ausgefallen.

Hast du die Mindest-Stufe auf „Ab Warnung“ oder „Nur Kritisch“ gestellt, teste lieber mit „Testnachricht
senden“ (Karte „Verbindung“ unter den ntfy-Einstellungen): Ein Briefing ohne Probleme ist nur eine
Info-Meldung und kommt dann nicht aufs Handy.

### Weitere Kanäle (Telegram, Discord, Gotify, Pushover, Matrix, Slack, Mattermost)

Statt ntfy oder zusätzlich (bis zu 10 Kanäle): Unter **„Weitere Kanäle“** auf „Kanal hinzufügen“ klicken, einen
**Kurznamen** wählen (z. B. `telegram`), die **Mindest-Stufe** (Vorgabe „Ab Warnung“) und ob er **aktiv** ist →
„Speichern“. Danach unten unter **Zugangsdaten** beim Eintrag „Adresse des Kanals“ mit diesem Kurznamen die Adresse
eintragen. Sie enthält Zugangsdaten (Bot-Token, Webhook-Schlüssel) und liegt deshalb nur im Tresor. Benennst du einen
Kanal um, ist seine Adresse weg und muss neu eingetragen werden. Geschrieben wird sie wie bei Apprise:

| Dienst | Adresse |
|---|---|
| Telegram | `tgram://BOT-TOKEN/CHAT-ID` (mehrere Chats mit `/` getrennt; Kanäle als `@name`) |
| Discord | `discord://WEBHOOK-ID/WEBHOOK-TOKEN` oder die Webhook-Adresse, die Discord anzeigt |
| Gotify | `gotifys://SERVER/APP-TOKEN` (`gotify://` ohne HTTPS, Port mit `:`) |
| Pushover | `pover://USER-KEY@APP-TOKEN` (optional `/GERÄT`) |
| Matrix | `matrixs://ZUGRIFFSTOKEN@SERVER/!RAUM-ID:SERVER` oder `…/#raumname` (nur mit Zugriffstoken, nicht mit Passwort) |
| Slack | `slack://T…/B…/X…` oder die Webhook-Adresse von Slack |
| Mattermost | `mmosts://SERVER/WEBHOOK-ID` |
| alles andere | `apprises://SERVER/SCHLÜSSEL` – eine eigene [Apprise-API](https://github.com/caronc/apprise-api) (eigener Container), die dann an jeden von Apprise unterstützten Dienst weitergibt |

Nodvard Deck schickt die Nachrichten selbst und direkt an den Dienst (keine Zusatzsoftware, kein Proxy aus der
Umgebung, keine Umleitungen). Telegram, Discord, Slack und Pushover brauchen dafür direkten Zugang ins Internet, und der
Text jeder Meldung (z. B. Servernamen, Dateipfade, Adressen) geht an diesen Dienst. Sammel-Pings wie `@everyone`,
`@channel`, `@all` oder `@room` aus einer Meldung bleiben nur Text. Ist die „Adresse des Dashboards“ oben
eingetragen, steht unter jeder Nachricht ein Link dorthin.

- **„Testnachricht senden“** schickt an ntfy und alle aktiven Kanäle gleichzeitig und nennt jeden, bei dem es nicht
  geklappt hat.
- **„Verbindung testen“** fragt jeden Dienst, ohne etwas zu senden: Bei Telegram, Discord, Pushover, Matrix und einer
  Apprise-API prüft das Token bzw. den Schlüssel, bei Telegram auch, ob der Bot in jede Gruppe und jeden Kanal
  schreiben darf, bei Matrix, ob das Konto in jedem Raum ist. Slack, Gotify und Mattermost erlauben das nicht (ebenso
  private Telegram-Chats); dort steht ein Hinweis, und ein Fehler beim letzten Senden zählt mit.
- Den Kurznamen „ntfy“ gibt es nicht, er steht für den ntfy-Server oben.
- Fällt bei einer normalen Meldung ein Kanal (oder ein Chat eines Kanals) aus, bekommen die anderen sie trotzdem.

## 9. Automatik und Wartungsfenster

Einstellungen → **Automatik & Sicherheit**:

- **Selbstständigkeit:** „Nur vorschlagen“ ist der Standard und für den Anfang richtig –
  jede Aktion, die Nodvard Deck von sich aus vorschlägt, wartet unter „Aktionen“ auf deine Freigabe. „Selbstständig handeln“ lässt
  Aktionen bis zur gewählten Risikostufe („Ohne Rückfrage erlaubt bis Risikostufe“) ohne
  Rückfrage laufen. Unter „Aktionen“ steht der Befehl einer Aktion direkt in ihrer Zeile (ohne das Recht
  `hosts.execute` und ohne Freigaberecht für ihr Risiko steht dort nur ein Hinweis); unsichtbare oder umlenkende
  Zeichen darin (etwa eine umgekehrte Schreibrichtung) erscheinen als sichtbare Marke wie `⟦U+202E⟧`. „Ausgewählte
  freigeben“ zeigt in der Rückfrage alle Befehle ungekürzt, gibt aber Aktionen mit hohem oder kritischem Risiko nicht
  mit frei; die bestätigst du einzeln.
- **Selbst ausgelöst:** Klickst du selbst einen Knopf wie „Endgültig löschen“ (Nodvard Shield) oder „Neustart“
  (Docker) und hast das Freigaberecht für das Risiko der Aktion, ist deine Bestätigung im Dialog schon die
  Freigabe: Die Aktion läuft sofort. Sie steht trotzdem unter „Aktionen“ und im Protokoll, nur nicht unter „Wartet auf
  Bestätigung“, sondern unter „Alle“ bzw. „Läuft“, „Erfolgreich“, „Fehlgeschlagen“ oder (Sperrliste) „Abgelehnt“. Ohne
  dieses Recht wartet sie unter „Wartet auf Bestätigung“ auf jemanden, der es hat. Ausnahmen: „Alle Updates
  vorschlagen“ schlägt bewusst nur vor (die Vorschläge warten), beim Einspielen eines Image-Updates ist die Übersicht die
  Bestätigung, und „In Quarantäne“, „Ignorieren“ und „Wieder beachten“ in Nodvard Shield laufen ohne Aktion (nur im
  Protokoll).
- **Skripte ohne Klick:** Unabhängig davon kann ein aktives Skript mit Zeitplan eine Dauerfreigabe bekommen: auf der
  Skripte-Seite beim Skript den Schalter „Ohne Freigabe nach Zeitplan“ (nur Inhaber und `admin`). Vorher siehst du, für
  welche Server, unter welchem Konto und unter welcher Adresse sie gilt. Jede Änderung an Inhalt, Parametern, Ziel oder
  Zeitplan und ein anderes Konto, eine andere Adresse oder ein anderer SSH-Port eines dieser Server heben sie auf; neue
  Server in einer Gruppe oder unter „Alle Server“ fragen weiter nach. Ist die Person, die freigegeben hat, gesperrt oder
  hat sie die Rechte dafür nicht mehr, warten die Läufe ebenfalls wieder auf deinen Klick. Jeder Lauf steht weiter unter
  „Aktionen“ und im Protokoll, „Freigabe zurückziehen“ wirkt sofort.
- **Fehlgeschlagene Läufe nach Zeitplan:** Scheitert ein geplanter Skript-Lauf (auf dem Server mit einem Fehler, vom
  Dashboard abgelehnt oder gar nicht erst gestartet), steht er im Verlauf des Skripts als fehlgeschlagen, und du
  bekommst eine Meldung (Stufe Warnung, mit Link zur Skripte-Seite) – **einmal je Fehler**: Dieselbe Meldung kommt
  erst wieder, wenn sich der Fehler ändert oder das Skript zwischendurch durchgelaufen ist, nicht bei jedem Lauf.
  Der Text nennt die Server, aber nie die Ausgabe; die steht unter „Aktionen“. Wer ein Skript von Hand startet, sieht das
  Ergebnis selbst, dafür gibt es keine Meldung. Lief ein Skript nirgends, weil alle Server übersprungen wurden, sagt das
  schon die Meldung zu den übersprungenen Servern.
- **Gesperrte Befehle:** eigene Muster (reguläre Ausdrücke), die nie ausgeführt werden. Die
  gefährlichen Standardfälle sind immer gesperrt.
- **Wartungsfenster:** „Fenster hinzufügen“ → Beginn, Dauer, „Gilt für“ (Alle Server /
  Ausgewählte Server) → „Speichern“. Während des Fensters werden Push-Nachrichten zu diesen
  Servern stummgeschaltet; unter „Meldungen“ stehen sie trotzdem. Die Zeit gilt in der
  eingestellten Zeitzone (Einstellungen → System). Stummgeschaltet werden nur Meldungen, die
  zu bestimmten Servern gehören: die von **Proxmox-Backups**, **Gameserver**, dem **Proxmox-Wächter** (Knoten, Datenträger,
  Verbindung) und von **Nodvard Shield** (Updates, Härtungs-Audit, Einbruchschutz-Warnungen,
  Lagebericht der Container-Wache). Ein Bericht über mehrere Server bleibt hörbar, solange
  auch nur einer davon nicht im Fenster liegt.
  Immer hörbar bleiben **Schadsoftware-Funde** und **kritische Einbruchschutz-Ereignisse**
  (der Tiefenscan läuft ab Werk sonntags um 03:30, mitten im vorgeschlagenen Fenster), das
  **Morgen-Briefing** und die Meldungen von Zertifikatswächter und Skripten.
  Gut zu wissen: Fällt bei Proxmox-Backups, Gameserver oder Proxmox-Wächter im Fenster etwas aus
  und ist es nach dem Fenster immer noch so, kommt die Meldung **einmal als Push nach**,
  mit „(seit dem Wartungsfenster)“ im Titel. Das gilt auch für einen neuen Join-Code des
  Gameservers, der sich etwa bei einem nächtlichen Neustart ändert. Ist alles schon im Fenster
  wieder in Ordnung, kommt nichts nach; unter „Meldungen“ stehen dann Ausfall und
  Entwarnung still. Wird die Entwarnung erst nach dem Fenster bemerkt, kommt sie als Push
  mit dem Hinweis, dass die Störung im Wartungsfenster lag. Kam der Ausfall als Push und
  fällt nur das „wieder in Ordnung“ ins Fenster, bleibt es im Fenster still und kommt
  danach einmal als Push, damit die Meldung auf dem Handy nicht offen bleibt. Einbruchschutz-Warnungen von
  Nodvard Shield kommen nicht nach – sie bleiben als offene Ereignisse im Einbruchschutz stehen,
  und das Morgen-Briefing nennt sie.

**Automatische Updates** (Nodvard Shield): Einstellungen → Erweiterungen → „Nodvard Shield“ →
„Updates automatisch einspielen“ ist ab Werk **aus**, ebenso „Danach automatisch neu
starten, wenn nötig“. Erst einschalten, wenn die Update-Zentrale eine Weile sauber
gelaufen ist. Den Server, auf dem Nodvard Deck selbst läuft, nimmt die Runde als letzten; sein Neustart
kommt erst, nachdem die Zusammenfassung verschickt ist (mit einer eigenen kurzen Meldung).
Einzelne Server kannst du vom automatischen Neustart ausnehmen: Direkt unter „Danach automatisch neu starten, wenn
nötig“ wählst du bei „Diese Server nie automatisch neu starten“ die Server aus (zum Beispiel ein NAS oder einen
Spieleserver). Sie bekommen ihre Updates trotzdem, nur der Neustart entfällt. Braucht so ein Server danach einen
Neustart, sagt das die Zusammenfassung („Neustart nötig, für diesen Server aber ausgeschaltet – bitte selbst neu
starten“), und du startest ihn selbst neu. Die Update-Zentrale nennt die Ausnahmen am Abzeichen („Neustart danach:
automatisch, außer …“). Steht der Server von Nodvard Deck selbst in der Liste, startet auch er nicht neu.
Ist das letzte Update auf einem Server fehlgeschlagen, abgebrochen oder noch nicht fertig, startet Deck ihn ebenfalls
nicht neu, auch in den folgenden Nächten nicht: Halb eingerichtete Pakete und ein Neustart vertragen sich schlecht. Die
Zusammenfassung sagt dann „Neustart nötig, aber ausgelassen“. Als Warnung, also im Cockpit unter „Braucht
Aufmerksamkeit“, kommt sie nur einmal je gescheitertem Lauf; in den Nächten danach steht der Satz weiter in der
Zusammenfassung, aber als Info. Ist der Lauf fehlgeschlagen, zeigt auch die Übersicht von Nodvard Shield dort „Neustart wartet auf ein
sauberes Update“, bis ein Lauf wieder klappt. Sieh in der Update-Zentrale nach, was schiefging; nach dem nächsten
sauberen Lauf startet Deck wieder selbst neu. Ist nichts mehr einzuspielen, starte den Server selbst neu.

**Alle Updates auf einmal vorschlagen:** In der Update-Zentrale legen „Alle Sicherheitsupdates
vorschlagen“ bzw. „Alle Updates vorschlagen“ für jeden passenden Server einen Vorschlag an; vorher
nennt eine Rückfrage die Server. Server mit einem offenen Update-Vorschlag oder einem laufenden
Einspielen werden übersprungen. Freigegeben wird danach unter „Aktionen“, gesammelt mit „Ausgewählte
freigeben“; Vorschläge mit hohem Risiko (bei „Proxmox: Alle Updates als dist-upgrade“) einzeln. Steht
die Selbstständigkeit auf „Selbstständig handeln“, laufen die Vorschläge bis zur eingestellten
Risikostufe ohne Rückfrage an.

**Durch Phasing verzögerte Updates (Ubuntu):** Ubuntu verteilt manche Updates schrittweise, erst an einen Teil der
Rechner, nach und nach an alle. Bis ein Server an der Reihe ist, hält apt das Update zurück, auch bei „Alle einspielen“.
Die Update-Zentrale zählt solche Updates mit und schreibt dazu „davon X durch Phasing verzögert“; ein Klick darauf erklärt
es, in der Paketliste stehen sie als „verzögert“. Das ist gewollt und kein Fehler: Sobald der Server an der Reihe ist,
lassen sie sich wie jedes andere Update einspielen. Erkannt wird das ohne root und unabhängig von der Sprache des Servers; auf Debian und
Proxmox gibt es kein Phasing, dort fehlt der Hinweis.

**Updates auf dem Server, auf dem Nodvard Deck läuft:** Ist unter den Updates ein Paket, das Docker neu startet
(`docker-ce`, `docker.io`, `containerd` und ähnliche), sieht die Prüfung zusätzlich nach, ob dort Nodvard Deck läuft.
Die Rückfrage vor dem Einspielen sagt es dann: Nodvard Deck ist danach kurz nicht erreichbar und lädt von selbst neu.
Das Einspielen läuft auf dem Server weiter, auch wenn Nodvard Deck dabei neu startet. Die Seite merkt sich den Lauf und
zeigt sein Ergebnis, sobald er fertig ist, auch nach dem Neuladen. Unter „Aktionen“ steht die Aktion dann als „durch
Neustart unterbrochen“; was wirklich passiert ist, steht im Verlauf der Update-Zentrale und in der Push-Nachricht.
Ist Nodvard Deck gerade nicht erreichbar, sagt der Reiter das und versucht es alle paar Sekunden selbst wieder.

## 10. Das Dashboard selbst sichern

### 10.1 In der Oberfläche (empfohlen)

**Einstellungen → System → Sicherung.** Nur der Inhaber (Owner) kann dort etwas ändern.

1. **Sicherungspasswort festlegen** (mindestens 12 Zeichen, zweimal eingeben, mit dem
   Anmeldepasswort bestätigen). Nodvard Deck speichert das Passwort nicht, nur einen
   öffentlichen Schlüssel. Danach erscheint **einmal** der *Wiederherstellungsschlüssel*
   (`AGE-SECRET-KEY-1…`): kopieren oder als Datei speichern und gut aufbewahren.
   **Passwort und Schlüssel weg = Sicherungen wertlos.** Niemand kann sie dann noch öffnen.
2. **Automatisch sichern** einschalten, Uhrzeit wählen und festlegen, wie viele Sicherungen
   bleiben sollen (1 bis 60, ältere werden gelöscht).
3. **Ordner:** Standard ist `/app/data/backups` im Datenordner. Das schützt vor Fehlbedienung,
   aber nicht vor einer kaputten SD-Karte. Besser einen Ordner von einem anderen Laufwerk oder
   NAS einbinden: In der Compose-Datei (`compose.yml` aus [1.1](#11-installation-ohne-das-repo),
   bei einer Installation aus dem Repository `deploy/docker-compose.yml`) steht unter `volumes:`
   schon die Zeile `# - ./sicherungen:/backups`. Das `#` davor entfernen (oder einen NAS-Pfad statt
   `./sicherungen` eintragen), im selben Ordner `mkdir sicherungen && sudo chown 1000:1000 sicherungen`
   ausführen (der Ordner muss dem Nutzer mit der Nummer 1000 gehören), mit `docker compose up -d`
   übernehmen und in der Oberfläche als Ordner `/backups` wählen. Für ein NAS oder NFS siehe
   [Sicherungen auf ein NAS oder NFS](#sicherungen-auf-ein-nas-oder-nfs).
4. **Prüfen** vergleicht eine Sicherung mit ihrer Prüfsumme (erkennt kaputte Dateien, ohne
   Passwort). **Herunterladen** holt eine Sicherung auf den PC, **Sicherung herunterladen**
   erstellt sofort eine frische – wahlweise mit dem Sicherungspasswort oder mit einem eigenen
   Einmal-Passwort nur für diese Datei.

#### Sicherungen auf ein NAS oder NFS

**Welcher Ordner geht:** Als Ziel nimmt Nodvard Deck nur `/app/data/backups` im Datenordner oder den eingebundenen
Ordner `/backups` samt Unterordnern (etwa `/backups/deck`). Gemeint ist immer der Pfad **im Container**, nicht der auf
dem Rechner oder dem NAS.

**So geht's:**

1. Den Ordner vom NAS auf dem Rechner einhängen, auf dem Nodvard Deck läuft, zum Beispiel unter `/srv/backup/deck`.
   Bei NFS in `/etc/fstab`:

   ```
   nas:/volume1/backup/deck  /srv/backup/deck  nfs  defaults,_netdev,x-systemd.before=docker.service  0  0
   ```

   Das NFS muss eingehängt sein, bevor Docker startet. Sonst bindet Docker den leeren Ordner auf der eigenen Platte
   ein, und die Sicherungen landen dort statt auf dem NAS. `_netdev` sagt dem System, dass es ein Netzlaufwerk ist,
   `x-systemd.before=docker.service` lässt Docker darauf warten.
2. In der Compose-Datei unter `volumes:` die vorbereitete Zeile einkommentieren und den Pfad eintragen:
   `- /srv/backup/deck:/backups`. Mit `docker compose up -d` übernehmen. In Portainer heißt das: Stack bearbeiten und
   „Update the stack“.
3. **Schreibrechte für den Nutzer 1000:** Nodvard Deck läuft im Container als Nutzer und Gruppe 1000. Der Ordner auf
   dem NAS muss für 1000 beschreibbar sein:
   - auf dem NAS `chown 1000:1000` für den Ordner, oder
   - beim NFS-Export alle Zugriffe auf 1000 abbilden: `all_squash,anonuid=1000,anongid=1000`.

   Bei NFS bringt ein `chown` auf dem Rechner meist nichts, weil das NAS root nicht durchlässt (`root_squash`).
4. **Testen**, bevor du in der Oberfläche speicherst (den Namen des Containers zeigt `docker ps`):

   ```bash
   docker exec -u 1000 <container> sh -c 'touch /backups/.test && rm /backups/.test && echo ok'
   ```

   Kommt `ok`, in der Oberfläche als Ordner `/backups` (oder einen Unterordner) wählen.

**Fehlermeldungen beim Speichern:**

| Meldung | Was hilft |
|---|---|
| „Der Ordner /backups existiert nicht (ist er eingebunden?).“ | Die Zeile `- …:/backups` fehlt oder ist noch auskommentiert, oder der Container wurde danach nicht neu erstellt (`docker compose up -d`, in Portainer „Update the stack“). |
| „Sicherungen dürfen nur nach /app/data/backups oder in den eingebundenen Ordner /backups geschrieben werden.“ | Als Ordner den Pfad im Container eintragen (`/backups` oder `/backups/…`), nicht den Pfad auf dem Rechner wie `/srv/backup/…`. |
| „In /backups darf Nodvard Deck nicht schreiben.“ | Schreibrechte für 1000 fehlen (Schritt 3), oder das NFS war beim Start von Docker noch nicht eingehängt (Schritt 1). |

Notfall ohne Nodvard Deck: Eine `.ndbak`-Datei ist eine normale [age](https://age-encryption.org)-Datei
mit zwei Zeilen davor.

```bash
tail -n +3 nodvard-deck-sicherung-20261001-023000.ndbak > sicherung.age
age -d -i wiederherstellungsschluessel.txt -o sicherung.tar.gz sicherung.age   # oder: age -d (Einmal-Passwort)
tar -xzf sicherung.tar.gz    # db/lattice.db, files/master.key, files/ext/ …
```

Einspielen: siehe [10.2](#102-wiederherstellen-sicherung-einspielen).

### 10.2 Wiederherstellen (Sicherung einspielen)

Zwei Wege, derselbe Ablauf: **Einstellungen → System → Wiederherstellen** (nur der Inhaber, mit
Anmeldepasswort) oder, auf einer **frischen Installation**, im Einrichtungsassistenten unter
**„Oder: Sicherung einspielen“** (dort statt eines Kontos mit dem **Einrichtungscode** aus dem
Protokoll des Containers – wie beim Anlegen des ersten Kontos).

1. **Datei wählen** (`.ndbak`) und hochladen. Es gibt eine Fortschrittsanzeige; die Datei geht als Strom
   auf die Platte, nicht in den Speicher. Obergrenze 4 GiB (`NODVARD_DECK_RESTORE_MAX_UPLOAD_BYTES`).
2. **Passwort oder Wiederherstellungsschlüssel** eingeben (bei einer Datei „mit eigenem Einmal-Passwort“ nur
   das Einmal-Passwort). Nodvard Deck entschlüsselt und prüft alles in einem getrennten Zwischenordner;
   die Datenbank der Sicherung wird dabei nur gelesen und nie ausgeführt.
3. **Zusammenfassung ansehen:** erstellt am, Version, Kennung der Installation, Name des Inhabers, Zahl der
   Nutzer und Server, Erweiterungen, Warnungen. **age prüft nicht, wer eine Sicherung erstellt hat** – wer das
   Passwort kennt, kann eine bauen. Spiele nur Sicherungen ein, die du selbst erstellt hast.
4. **„Einspielen und neu starten“.** Das ersetzt **alles, auch die Konten**; danach melden sich alle neu an (mit
   den Konten aus der Sicherung). Nodvard Deck beendet sich und startet neu; beim Start wird eingespielt, danach
   migriert es wie immer. Die Seite wartet und führt dann zur Anmeldung.

Gut zu wissen:

- **Neustart-Regel nötig.** Der Neustart funktioniert nur, wenn der Container eine Neustart-Regel hat
  (`restart: unless-stopped`; die mitgelieferten Compose-Dateien haben sie). Mit `docker run` ohne `--restart`
  bleibt er danach aus: von Hand starten (`docker start …`), die Sicherung wird beim Start eingespielt.
- **Der alte Stand bleibt liegen** unter `restore/replaced-<Zeit>` im Datenordner (mit alten Konten und
  Schlüsseln), genau einer, und wird **nach 30 Tagen automatisch gelöscht** (die Karte nennt den Tag). Unter *Wiederherstellen* lässt er sich früher
  mit dem Anmeldepasswort löschen.
- **Läuft die alte Installation noch**, etwa auf einem anderen Gerät, haben beide dieselben Schlüssel und
  Zugangsdaten. Danach nur eine betreiben.
- **Scheitert etwas** (kaputte oder zu neue Sicherung, Fehler beim Verschieben, Migration schlägt fehl), kommt der
  alte Stand zurück und die Karte nennt den Grund. Ein Absturz mitten im Einspielen wird beim nächsten Start
  zurückgenommen. Eine Sicherung aus einer **neueren** Version oder mit einer Erweiterung, die hier fehlt, wird
  mit Hinweis abgelehnt. Eine Vormerkung gilt eine Stunde, ein aufgegebener Zwischenstand wird nach einer Stunde
  gelöscht.
- Mit der Umgebungsvariable `NODVARD_DECK_JWT_SECRET` liegt kein Anmelde-Geheimnis in der Sicherung.
- Der Verlauf der Messwerte (`metrics.db`) gehört nicht zur Sicherung und bleibt, wie er ist.
- **Git-Einstellungen** in den Daten der Erweiterungen (etwa bei „Skripte“) gehören ebenfalls nicht zur Sicherung, der
  Verlauf deiner Skripte schon; die Einstellungen legt die Erweiterung selbst neu an. Enthält eine ältere Sicherung noch
  welche, werden sie beim Einspielen übersprungen, und die Zusammenfassung nennt ihre Zahl unter den Warnungen.

**Notfall ohne Oberfläche:** im Ordner mit der Compose-Datei

```bash
cd ~/nodvard-deck
sudo docker compose exec nodvard-deck python -m nodvard_deck.admin restore-backup /app/data/backups/nodvard-deck-sicherung-….ndbak
sudo docker compose restart nodvard-deck      # eingespielt wird beim Start
```

(Die Datei muss im Container sichtbar sein; der Befehl fragt das Passwort ab und zeigt die Zusammenfassung. `compose exec` läuft im
bestehenden Container; ein `compose run` daneben würde von der Sperre des Datenordners abgewiesen, siehe [10.4](#104-updates-die-kopie-vor-der-migration).)
Bei einer Installation aus dem Repository geht dasselbe kürzer mit `deploy/restore.sh sicherung.ndbak`
([10.3](#103-mit-dem-skript-nur-installation-aus-dem-repository)).
Geht gar nichts mehr, liegt der alte Stand unter `restore/replaced-…`: Container anhalten, dessen Inhalt
(`lattice.db`, `master.key`, `ext/` …) zurück in den Datenordner kopieren, starten.

### 10.3 Mit dem Skript (nur Installation aus dem Repository)

Die Skripte `deploy/backup.sh` und `deploy/restore.sh` gehören zur Installation aus dem Repository
([1.2](#12-aus-dem-repository-bauen-für-entwickler)): Sie rufen Compose mit dem Projektnamen von
`deploy/docker-compose.yml` auf und funktionieren mit der `compose.yml` aus
[1.1](#11-installation-ohne-das-repo) nicht. Dort sicherst du über die Oberfläche ([10.1](#101-in-der-oberfläche-empfohlen)).

`deploy/backup.sh` hält den Container kurz an und packt den kompletten Datenordner in eine Datei:
Datenbank, `master.key`, `jwt_secret.key`, Schlüsselbund, Daten der Erweiterungen,
Skript-Repository. Details und Wiederherstellen:
[deploy/README.md](../deploy/README.md#backup--restore).

Im Ordner `deploy/` des Repositorys (hier als Beispiel unter `~/deck`):

```bash
cd ~/deck/deploy
sudo ./backup.sh ~/nodvard-deck-backups
```

Wiederherstellen: `sudo ./restore.sh ~/nodvard-deck-backups/nodvard-deck-backup-<Zeitstempel>.tar.gz`
(fragt vorher nach).

Ist dein Benutzer in der Gruppe `docker`, lässt du `sudo` weg. Ohne Zugriff auf Docker brechen beide Skripte ab,
bevor sie etwas anhalten oder ändern (`backup.sh` meldet „Docker antwortet nicht …“). Mit `sudo` gestartet gehört
die Sicherung trotzdem dir, nicht root, und nur du kannst sie lesen.

So läuft das Wiederherstellen:

- `restore.sh` prüft die Datei, entpackt sie erst **neben** die bisherigen Daten und tauscht dann aus. Dafür braucht
  es kurz doppelt so viel Platz. Scheitert das Entpacken (Platte voll, Strg+C), läuft Nodvard Deck auf dem alten
  Stand weiter.
- Bricht der Austausch selbst ab (etwa bei einem Stromausfall), bleibt Nodvard Deck gestoppt. Dann denselben Befehl
  mit derselben Datei noch einmal aufrufen: Er macht den Austausch fertig und startet Nodvard Deck. Bis dahin nicht
  von Hand starten; `backup.sh` lehnt so lange ab.
- Reißt nach der Rückfrage die SSH-Verbindung ab, läuft das Einspielen weiter. Wie es ausging, steht in
  `deploy/restore.log`.
- Läuft schon eine Sicherung oder ein Einspielen, bricht ein zweiter Lauf ab, ohne etwas zu ändern.

Wichtig:

- **Die Sicherung enthält die Schlüssel** (`master.key`, `vault_keyring.json`). Damit
  lassen sich alle in Nodvard Deck gespeicherten Passwörter, SSH-Schlüssel und Tokens
  entschlüsseln – die Datei also so gut wegschließen wie die Passwörter selbst. Umgekehrt:
  Ohne diese Schlüsseldateien sind die Geheimnisse weg.
- Die Sicherung gehört **nicht nur auf denselben Rechner** (eine SD-Karte kann sterben). Vom PC
  aus abholen: `scp admin@192.168.1.10:~/nodvard-deck-backups/*.tar.gz .`
- Regelmäßig, z. B. sonntags 04:30 (per `sudo crontab -e`, eine Zeile):

  ```
  30 4 * * 0 cd /home/admin/deck/deploy && ./backup.sh /home/admin/nodvard-deck-backups >> /home/admin/nodvard-deck-backup.log 2>&1
  ```

  Die Datei gehört dann dem Besitzer des Zielordners, wenn das nicht root ist (hat ihn der erste `sudo ./backup.sh`
  angelegt, bist das du); so klappt das Abholen mit `scp` wie oben. Läuft gerade eine Sicherung oder ein Einspielen,
  bricht der Lauf ab, ohne etwas zu ändern (Meldung im Log).

### 10.4 Updates: die Kopie vor der Migration

Vor jedem Update, das die Datenbank umbaut (eine **Migration**), legt Nodvard Deck beim Start **automatisch eine Kopie der Datenbank** an:
`backups/vor-update/<Zeit>_<von>_<nach>.db` im Datenordner. Die **drei neuesten** bleiben. Zu sehen unter **Einstellungen → System → „Kopien vor Updates“**.

- **Ohne Kopie keine Migration.** Reicht der Platz nicht (nötig ist gut das 1,2-fache der Datenbank), startet Nodvard Deck nicht, sondern zeigt die
  Notseite mit der Zahl, wie viel fehlt (siehe [13](#13-nodvard-deck-startet-nicht-die-notseite)). Platz schaffen, „Neu versuchen“.
- **Klappt die Migration nicht,** setzt Nodvard Deck die Datenbank von selbst auf die Kopie zurück. Danach läuft die Notseite; die Daten sind wie vor dem Update.
  Der halbe Stand wird dabei verworfen; er enthält nichts, was nicht auch in der Kopie steht.
- **Bricht die Migration mittendrin ab** (Strom weg, Container neu gestartet), setzt der nächste Start die Datenbank zuerst auf die Kopie zurück und migriert dann neu.
  Der halbe Stand bleibt dabei 30 Tage unter `restore/replaced-…` liegen (verworfen wird er nur, wenn die Datenbank auf einem eigenen Laufwerk liegt und im
  Datenordner der Platz dafür fehlt). Bricht die **allererste** Migration einer neuen Installation ab
  (noch ohne Konto), legt der nächste Start den halben Stand dort ab und beginnt mit einer frischen Datenbank.
- **Zurück auf die alte Version** (Image-Version in der Compose-Datei zurückstellen, Container neu starten):
  - Hat die neue Version **nie erfolgreich gestartet**, spielt die alte die Kopie **selbst** wieder ein. Es gingen ja keine Daten verloren; die neueren Daten
    bleiben trotzdem 30 Tage unter `restore/replaced-…` liegen (neuere Daten werden beim Zurücksetzen nie gelöscht).
  - Hat die neue Version **schon gearbeitet**, zeigt die alte die Notseite („Die Daten sind neuer als diese Version“). Mit dem Notfallcode lässt sich dort
    **„Stand vor dem Update wiederherstellen“** wählen: Alles seit dem Update geht verloren (die neueren Daten bleiben 30 Tage unter `restore/replaced-…`).
- **Der Rückweg funktioniert erst ab einer Version mit dieser Funktion.** Eine alte Version (bis 0.5.x) kann eine Kopie nicht selbst einspielen und kennt die Notseite nicht. Vorversion und
  neue Version müssen also beide mindestens die Version mit „Kopie vor jeder Migration“ sein, damit der Weg zurück offen ist. Das erste Update **auf** diese Version legt die Kopie schon an.
- **Nicht in der Kopie** sind Dateien außerhalb der Datenbank (Daten der Erweiterungen, Schlüssel). Migrationen fassen sie nicht an. Für alles andere gibt es die Sicherung ([10.1](#101-in-der-oberfläche-empfohlen)).
- **Notausgang** (nicht empfohlen): `NODVARD_DECK_SKIP_PRE_MIGRATE_BACKUP=1` in der Compose-Datei schaltet die Kopie ab, damit auch bei zu wenig Platz migriert wird. Scheitert die Migration dann, gibt es keinen Rückweg.
- Was beiseitegelegt wurde (`restore/replaced-…`: der alte Stand einer Wiederherstellung oder eine Datenbank, die ein Rückweg ersetzt hat), wird nach 30 Tagen automatisch gelöscht.
- **Datenbank auf einem eigenen Laufwerk** (eigener `NODVARD_DECK_DATABASE_URL`, z. B. eine USB-SSD): Zum Beiseitelegen wird sie dann in den Datenordner kopiert.
  Dafür muss dort so viel Platz frei sein, wie die Datenbank groß ist; sonst zeigt die Notseite „zu wenig Platz“ mit den Zahlen, und es wurde nichts verändert.
  Nur den halben Stand einer abgebrochenen Migration verwirft der Start in diesem Fall, statt auf der Notseite hängen zu bleiben.
- **Eine Sicherung einspielen, deren Migration abbricht** (Strom weg mitten drin): Der nächste Start nimmt das Einspielen zurück, und es gilt wieder genau der alte
  Stand mit seinen Schlüsseln. Er wird danach nie durch die Sicherung ersetzt.
- Bei einer anderen Datenbank als SQLite gibt es keine Kopie (bitte vorher selbst sichern).

### 10.5 Neue Version finden und einspielen

Unter **Einstellungen → System → „Updates“** steht, welche Version läuft und welche die neueste ist. Nodvard Deck sieht dazu **einmal am Tag**
bei ghcr.io nach, wo die fertigen Images liegen; mit **„Jetzt suchen“** geht es sofort (höchstens einmal pro Minute).

- **Datenschutz:** ghcr.io gehört zu **GitHub**, ebenso github.com, wo die Suche die Freigabe einer Version abholt (Downloads über
  `objects.githubusercontent.com` bzw. `release-assets.githubusercontent.com`; wer filtert, lässt diese Adressen durch). Bei jeder Suche
  sieht GitHub die IP-Adresse deines Anschlusses und den Zeitpunkt; sonst wird
  nichts übertragen, auch nicht die installierte Version. **Du kannst die Suche abschalten:** „Täglich automatisch nach Updates suchen“ aus
  (Recht `settings.write`); dann fragt das Dashboard von sich aus nie nach. „Jetzt suchen“ fragt auch bei ausgeschaltetem Schalter nach – das
  kann jeder mit dem Recht `system.read`, und auch diese Anfrage sieht GitHub.
- **Welche Versionen:** „Nur fertige Versionen“ (Vorgabe) oder „Auch Vorabversionen (Beta)“. Vorabversionen (z. B. `0.7.0-rc1`) gibt es nicht
  unter `:latest`: Zum Einspielen trägst du bei `image:` genau `ghcr.io/nodvard/deck:0.7.0-rc1` ein.
- **Ohne Internet** steht dort „Konnte nicht prüfen (offline?)“ und der letzte bekannte Stand; das ist kein Fehler. „Keine gefunden“ heißt:
  Die Abfrage hat geklappt, aber im gewählten Kanal gibt es noch keine Version.
- **Einspielen** macht ohne Update-Helfer nicht das Dashboard selbst, sondern deine Umgebung (mit Helfer per Knopf, siehe
  [10.6](#106-update-per-knopf-update-helfer)). Die Karte hat Anleitungen als Reiter: Docker Compose, Docker Desktop,
  Portainer („Update the stack“ mit „Re-pull image“), Synology Container Manager, Unraid („Check for Updates“ → „Apply update“) und
  `scripts/deploy_pi.sh`. Mit Compose (Installation wie in [1.1](#11-installation-ohne-das-repo)) im Ordner mit der `compose.yml`:

  ```bash
  docker compose pull
  docker compose up -d
  ```

  Unter Linux ggf. `sudo` davor. Heißt deine Datei anders (z. B. `compose.standalone.yml` aus einer älteren Anleitung), häng `-f` und den
  Dateinamen an (`docker compose -f compose.standalone.yml pull`). Steht bei
  `image:` eine feste Version (z. B. `:0.5.0`) oder eine Reihe wie `:0.5` statt `:latest`, trag dort zuerst die neue Version ein.
- **Zurück zur Vorversion:** Notiere dir vor dem Update die jetzige Version (steht in der Karte). Zum Zurückgehen trägst du sie wieder bei
  `image:` ein und startest neu (`docker compose up -d`). Vor einem Umbau der Datenbank legt Nodvard Deck beim Start
  automatisch eine Kopie an (nur bei SQLite, der Standard-Datenbank). Hat die neue Version noch nie richtig gestartet, spielt die alte diese
  Kopie von selbst wieder ein. Lief die neue Version schon und hat sie dabei die Datenbank umgebaut, zeigt die alte eine **Notseite**: Dort mit
  dem Notfallcode (steht im Protokoll des Containers) „Stand vor dem Update wiederherstellen“ wählen; was seit dem Update geändert wurde, geht
  dabei verloren. Oder wieder die neue Version eintragen. Einzelheiten in [10.4](#104-updates-die-kopie-vor-der-migration) und
  [13](#13-nodvard-deck-startet-nicht-die-notseite).
- Läuft ein **selbst gebautes** Image (z. B. vom PC über `scripts/deploy_pi.sh`), sagt die Karte das; aktualisiert wird dann auf demselben Weg.

### 10.6 Update per Knopf (Update-Helfer)

Mit dem **Update-Helfer** spielst du Updates unter Einstellungen → System → „Updates“ per Knopf ein und kannst danach
7 Tage lang einmal per Knopf zurück. Der Helfer ist ein kleiner eigener Dienst in derselben Compose-Datei, den du bewusst
einschaltest. Er hat Zugriff auf Docker, das ist so viel wie root auf diesem Rechner. Einrichten, aktualisieren und
entfernen: [deploy/README.md, Abschnitt „Update-Helfer“](../deploy/README.md#update-helfer).

- **Zustand:** Die Karte „Updates“ zeigt „Update-Helfer: nicht eingerichtet“ (mit Link zur Anleitung), „bereit“ (mit
  Version und letztem Lebenszeichen), „nicht bereit“ (mit dem Grund und was hilft) oder „antwortet nicht“.
- **Voraussetzungen für den Knopf:** Helfer bereit, eine neuere fertige Version ist da, es läuft das offizielle Image
  `ghcr.io/nodvard/deck` mit `:latest` oder einer passenden Reihe wie `:0.7` (keine feste Version), und du bist Inhaber
  der Installation. Der Helfer nimmt erst Versionen ab 0.7.0 an; das erste Update per Knopf geht also von 0.7.0 auf die
  Version danach. Fehlt etwas, sagt die Karte, warum, wo es hilft.
- **Freigabe:** Per Knopf kommt nur eine Version, die am Release freigegeben ist (signiert mit dem Schlüssel des
  Projekts); bis dahin sagt die Karte „Für Version X liegt noch keine gültige Freigabe vor …“, von Hand geht das Update
  trotzdem. Ein aktueller Helfer prüft die Signatur selbst, die Karte zeigt dann „Freigabe liegt vor (Schlüssel …)“. Bei
  einem älteren Helfer bittet sie, ihn zu erneuern.
- **Update:** „Jetzt aktualisieren auf X“, dann Passwort und, wenn Zwei-Faktor an ist, den Code aus der App eingeben.
  Baut die neue Version die Datenbank um, legt Nodvard Deck vorher wie immer eine Kopie an ([10.4](#104-updates-die-kopie-vor-der-migration)).
  Die Karte zeigt die Schritte; du kannst die Seite dabei schließen, der Helfer arbeitet ohne sie weiter. Beim Umschalten
  ist das Dashboard ein paar Minuten weg; die Seite fragt weiter nach und lädt nach dem Erfolg neu. Dauert es länger als
  30 Minuten (langsamer Download), fragt die Karte weiter nach und zeigt das Ergebnis, sobald es da ist.
- **Klappt es nicht** (die neue Version beendet sich, startet immer wieder neu, zeigt die Notseite oder ist nach 15
  Minuten nicht bereit), schaltet der Helfer von selbst auf die alte Version zurück. Die Karte sagt, warum und was du tun
  kannst; wichtige Ergebnisse kommen zusätzlich als Meldung.
- **Rückweg:** In der Karte „Kopien vor Updates“ steht 7 Tage lang „Zurück zu Version X“, einmal. Hat die neue Version
  die Datenbank umgebaut, gehen die Daten mit zurück: Die Karte nennt vorher den Zeitpunkt, seit dem alles verloren geht,
  und verlangt ein Häkchen. Der ersetzte Stand bleibt 30 Tage unter `restore/replaced-…`. Ist unklar, was mit den Daten
  passiert, gibt es keinen Knopf, nur die Erklärung. Nach einem Rückweg spielt der Helfer die verlassene Version 24 Stunden
  lang nicht wieder ein; die Karte sagt, ab wann es wieder geht. Ist ein Rückweg gescheitert, der die Daten mitnehmen
  sollte, steht in der Meldung (Glocke), ob die Datenbank schon zurückgesetzt war.
- Update und Rückweg zusammen gehen höchstens einmal in 10 Minuten. Ohne Helfer gelten die Anleitungen aus
  [10.5](#105-neue-version-finden-und-einspielen).

## 11. Wenn etwas hakt

**„HTTP 401“ oder „Nicht authentifiziert“ auf einer Erweiterungsseite** (Proxmox, Proxmox-Backups,
Skripte …): Im sichtbaren Tab erneuert Nodvard Deck die Anmeldung selbst. Erweiterungsseiten, Herunter- und
Hochladen auf der Dateien-Seite, Sicherung hochladen und der Export des Protokolls erneuern sie bei einer
abgelaufenen Anmeldung auch selbst und versuchen es einmal neu. Kommt die Meldung trotzdem, Seite neu laden (F5).

**Irgendwo „HTTP 500“:** Log ansehen (im Ordner mit der Compose-Datei), dort steht der Grund:

```bash
cd ~/nodvard-deck && sudo docker compose logs --since 15m nodvard-deck | grep -iE -A5 "error|traceback"
```

**„Seite nicht gefunden“:** Die Adresse gibt es nicht (mehr), etwa ein altes Lesezeichen; „Zur Übersicht“ führt ins
Cockpit. **„Die Seite konnte nicht geladen werden“** kommt meist direkt nach einem Update: Seite neu laden (F5). Bei
**„Hier ist etwas schiefgegangen“** ebenfalls neu laden; bleibt es, nennt „Technische Einzelheiten“ den Fehler, und
das Log (siehe oben) hilft weiter.

**Proxmox nicht erreichbar** (Push „Proxmox 'pve1' nicht erreichbar“, Kacheln mit „nicht
erreichbar“ oder „Nicht abrufbar: …“) – die Fehlermeldung verrät meist den Grund:

- „Proxmox ist nicht erreichbar (Verbindung abgelehnt oder kein Weg dorthin). Läuft der Server, und stimmen Adresse und
  Port?“ oder „Proxmox antwortet nicht (Zeitüberschreitung). Prüfe Adresse und Port.“: Server aus, falsche Adresse
  oder Port. Die Adresse braucht `https://` und `:8006`.
- „Das Zertifikat von Proxmox wird nicht akzeptiert …“: Fingerabdruck noch nicht gemerkt – Seite „Proxmox“, Reiter
  „Verbindungen“ → „Zertifikat“ ([docs/10](10-PROXMOX-TOKEN.md#zertifikat-fingerabdruck-merken)).
- „Der Server zeigt ein anderes Zertifikat als das gemerkte …“: Das Zertifikat in Proxmox hat sich geändert. Neuen
  Fingerabdruck vergleichen und im Reiter „Verbindungen“ → „Zertifikat“ bestätigen – nur, wenn du den Wechsel
  selbst kennst.
- „HTTP 401: Proxmox hat den Zugang abgelehnt. Prüfe Token-ID und Geheimnis in den Einstellungen.“: Token-ID oder
  Geheimnis falsch, oder Token in Proxmox gelöscht.
- „HTTP 403: Dem Token fehlt ein Recht für diese Abfrage. Grund laut Proxmox: Permission check failed (…)“: Recht
  fehlt – Tabelle in [docs/10](10-PROXMOX-TOKEN.md#nachschlagen-welcher-aufruf-welches-recht-braucht). Den Zusatz
  „Grund laut Proxmox“ gibt es nur, wenn Proxmox einen Grund nennt. „Verbindung testen“ sagt dazu allgemein „Zugriff
  verweigert – dem Konto oder Token fehlt ein Recht auf dem Server …“. Fehlen einzelne VMs ganz, fehlt ihnen
  `VM.Audit`.
- „Der Server hat die Verbindung auf eine andere Adresse umgeleitet …“: Meist steht `http://` statt `https://` in der
  Adresse, oder ein Proxy davor leitet um. Nodvard Deck folgt Weiterleitungen nicht – die Adresse des Knotens direkt
  eintragen: `https://<adresse>:8006`.
- „Die Antwort von Proxmox hat nicht das erwartete Format (HTTP 200). Stimmt die Adresse?“: Unter der Adresse antwortet
  etwas anderes als die Proxmox-API, etwa die Anmeldeseite eines Proxys. Adresse prüfen. „Verbindung testen“ zeigt
  beide Sätze genauso an.
- Testen vom Rechner mit Nodvard Deck aus: der `curl`-Befehl in
  [docs/10](10-PROXMOX-TOKEN.md#prüfen-bevor-du-es-in-nodvard-deck-einträgst).
- Ein ausgefallener Proxmox (z. B. pve2 aus) bremst die anderen nicht mehr, er steht nur
  als „nicht erreichbar“ da. Auf der Startseite zählt sein Knoten dann nicht als online: beim ersten Abgleich ohne
  Antwort steht er als „unbekannt“ da (ein kurzer Neustart bleibt so ohne Fehlalarm). Als „nicht erreichbar“ gilt er
  erst, wenn er mindestens 4 Minuten am Stück bei keinem Abgleich antwortet, in der Regel also beim nächsten geplanten
  Abgleich (nach etwa 5 bis 10 Minuten); ein von Hand gestarteter Abgleich verkürzt diese Frist nicht. Eine Antwort
  oder eine Ablehnung dazwischen beginnt sie neu. Steht hinter der Verbindung ein Cluster oder lehnt Proxmox nur ab
  (etwa ein abgelaufenes Token), bleiben die Knoten „unbekannt“, denn die Maschinen können noch laufen. Seine VMs und
  Container stehen als „unbekannt“ da, bis er wieder antwortet. Ist er länger aus: auf der Seite „Proxmox“ (Reiter
  „Verbindungen“) und „Proxmox-Backups“ (unter „Verbindungen verwalten“) auf „deaktiviert“ schalten, dann kommen auch
  keine Meldungen; seine Server stehen danach als „unbekannt“ da, bis du die Verbindung wieder einschaltest.

**„… leitet auf eine andere Adresse um“** (Pi-hole, Nginx Proxy Manager, ntfy, Nextcloud): Der Server hat
statt der Antwort eine Weiterleitung geschickt (HTTP 301, 302, 303, 307 oder 308). Nodvard Deck folgt dem nie, weil sonst
Passwort oder Token an eine Adresse gingen, die niemand eingetragen hat, und nennt auch die Zieladresse nicht. Meist
steht in den Einstellungen `http://` statt `https://` (oder umgekehrt), oder ein Proxy davor leitet um. Abhilfe: in den
Einstellungen die endgültige Adresse eintragen, so wie sie im Browser nach der Weiterleitung steht. Andere Fehler der
Nextcloud erscheinen als fester Satz, etwa „Nextcloud ist gerade nicht bereit (HTTP 503), zum Beispiel im
Wartungsmodus.“ oder „In der Nextcloud ist kein Speicherplatz mehr frei (HTTP 507).“

**„Der Zeitplan „…“ ist ungültig“** beim Speichern (Einstellungen einer Erweiterung, Skripte): Ein eigener Zeitplan
(Auswahl „Eigener“) braucht genau fünf Angaben: Minute (0–59), Stunde (0–23), Tag (1–31), Monat (1–12) und Wochentag
(0–7), zum Beispiel `0 2 * * *` für täglich um 2 Uhr. Kürzel wie `@daily` gehen nicht. Stammt ein kaputter Zeitplan
noch aus einer älteren Version, läuft Nodvard Shield mit seinem Standard-Zeitplan weiter bzw. das Skript vorerst nicht
nach Zeitplan; eine Meldung „Zeitplan … ist ungültig“ sagt, welcher es ist.

**Backup fehlgeschlagen** (Proxmox-Backups): Klapp auf der Seite „Proxmox-Backups“ beim Job den „Verlauf“ auf. Oben
steht in zwei Sätzen, was passiert ist und was du tun kannst, darunter die Zeile, die Proxmox geschrieben hat. Bekannt
sind die häufigsten Gründe: Ziel voll, Verbindung zum Netzlaufwerk abgerissen, Ziel nicht eingehängt, Gast gesperrt,
Backup-Server lehnt die Anmeldung ab, Gast-Agent antwortet nicht, fehlende Rechte (auf dem Ziel oder in Proxmox),
Datenträgerfehler, Gast gelöscht, Lauf abgebrochen, Ziel nicht erreichbar. Bei einem Job für mehrere Gäste steht auch da,
wenn dieser Gast gesichert wurde und nur ein anderer fehlschlug. Bei einem älteren Fehlschlag zeigt „Warum?“ neben dem Lauf seinen Grund. Die
Meldung des Backup-Wächters nennt dieselben zwei Sätze, die Zeile von Proxmox aber nicht (sie kann Pfade und Adressen
enthalten). Ohne das Recht `Sys.Audit` liest Nodvard Deck das Protokoll des Laufs nicht und erklärt nur dessen
Ergebnis. Ein Lauf mit Warnungen („WARNINGS“ in Proxmox) gilt als gelungen, die Sicherung ist da; im Verlauf steht „OK,
mit Warnungen“.

**Virenscan von Nodvard Shield fehlgeschlagen:** Die Liste der Scans nennt einen verständlichen Grund: ob der Server
nicht erreichbar war, die Verbindung mitten im Scan abgebrochen ist, der Scan sein Zeitlimit überschritten hat (mit
Dauer) oder welche Meldung ClamAV geschrieben hat, etwa „Input/output error“ bei einer defekten Platte. Wurden neue
Dateien vor dem Scan des Echtzeit-Wächters wieder gelöscht, gilt das nicht als Fehler. Läuft auf einem Server gerade ein
Scan, setzt der Wächter still aus und prüft beim nächsten Lauf die ausgelassene Zeit mit (bis zu 6 Stunden zurück); kann
er länger nicht prüfen, steht das als Fehler in der Liste. Ein Scan wartet bis zu 6 Minuten auf einen laufenden
Wächter, und ein hängender Lauf wird vom Server kurz vor dem Zeitlimit beendet. Das braucht auf dem Server `flock` und
`timeout` (util-linux, coreutils); fehlen sie, läuft alles wie früher.

**Ordner vom Virenscan ausnehmen** (Nodvard Shield, etwa ein großes Medienarchiv, das den Scan bremst): Einstellungen →
Erweiterungen → „Nodvard Shield“ → „Erweitert“ → „Ausgenommene Ordner“, dort volle Pfade wie `/srv/medien`. Sie gelten
für den Schnell- und den Tiefenscan **und** für den Echtzeit-Wächter, der dort weder nach neuen Dateien sucht noch etwas
prüft. Ein Eintrag gilt genau für diesen Ordner samt Inhalt: `/srv/backup` nimmt nicht nebenbei `/srv/backups-wichtig`
aus, und der eingebaute Ausschluss von `/dev` trifft nicht `/devel`. Platzhalter wie `*` gibt es nicht, sie zählen als
normale Zeichen; nur ein `/*` oder `/**` am Ende ist erlaubt und meint denselben Ordner (`/srv/backup/*` wirkt wie
`/srv/backup`). Doppelte Schrägstriche, `/./` und ein Schrägstrich am Ende fallen weg. Einträge, die nicht gelten,
werden übergangen: Pfade, die nicht mit `/` beginnen, Pfade mit einem Zeilenumbruch, Pfade mit `..` (der Eintrag
würde sonst einen ganz anderen Ordner treffen) und `/` allein; beim Virenscan außerdem Pfade über 511 Bytes. Ist ein
überwachter Ordner selbst ein Link (etwa `/home` → `/data/home`), trag den Ausschluss mit dem Pfad
darunter ein (`/home/…`), nicht mit dem Ziel des Links. Liegt ein ausgenommener Ordner über einem geprüften Ordner,
fällt dessen Prüfung ganz weg.

**Freigaben und Nextcloud vom Echtzeit-Wächter prüfen lassen** (Nodvard Shield → Reiter „Scans“ → „Wächter-Vorlagen“):
Der Wächter prüft von sich aus nur Orte, an denen Schadsoftware typischerweise abgelegt wird (`/tmp`, `/home` …).
Was andere hochladen, landet oft woanders. „Auf den Servern suchen“ liest auf jedem prüfbaren Server die Samba-Freigaben
(`path =` bzw. `directory =` in `/etc/samba/smb.conf`, ohne Drucker und `[homes]`), die NFS-Freigaben (`/etc/exports`,
auch mit Anführungszeichen oder `\040`) und den Datenordner einer Nextcloud (aus ihrer `config.php` oder ein
Docker-Volume mit „nextcloud“ im Namen, auch Nextcloud AIO) und ändert dabei nichts. Ohne root bzw. `sudo -n` fehlt
meist die Nextcloud. „Zum Wächter hinzufügen“ hängt den Ordner an „Überwachte Ordner“ an (braucht das Recht,
Einstellungen zu ändern; steht im Protokoll). Die Liste gilt für alle Server, auf einem Server ohne diesen Ordner kostet
sie nichts. Systemordner (`/etc`, `/var` …, `/proc`, `/sys`, `/dev`, `/run`, `/boot` samt allem darunter) und
Samba-Platzhalter wie `%U` werden nicht vorgeschlagen. Liegt ein Fund in einem „ausgenommenen Ordner“, steht er als
„ausgenommen“ da und lässt sich nicht hinzufügen (dort prüft der Wächter nicht). Ein sehr großer Ordner kostet bei jedem
Wächterlauf etwas Zeit; entfernen geht in den Einstellungen unter „Überwachte Ordner“. Der Wächter prüft neue und
geänderte Dateien, auch solche, die mit alter Änderungszeit hineinkopiert wurden (Samba, Nextcloud-Sync, `cp -p`,
`rsync -a`): Er schaut außer auf die Änderungszeit auch darauf, wann die Datei dort angelegt wurde.

**Server fehlt im Terminal / „Noch kein Server prüfbar“ oder „Keine Linux-Server mit SSH-Zugang gefunden“ bei Nodvard Shield:** Kein
SSH-Zugang hinterlegt: Einstellungen → Server & Zugänge ([5.2](#52-ssh-zugang-einrichten--drei-wege)). Nodvard Shield nimmt außerdem nur Server, die als `linux`
eingetragen sind, und – wenn in seinen Einstellungen „Nur Server mit Markierung“ ausgefüllt ist – nur Server mit dieser Markierung.

**„Keine root-Rechte …“:** sudo ohne Passwort fehlt ([6.](#6-root-rechte-ohne-passwort-und-gruppe-docker)). Die Karte „Verbindung prüfen“ zeigt es unter „Root-Rechte“.

**„Der Server-Schlüssel von diesem Server ist noch nicht bestätigt.“:** Nodvard Deck kennt den
Fingerabdruck dieses Servers noch nicht (neuer Server, und „Neue Server-Schlüssel erst nach meiner
Bestätigung merken“ ist an) oder du hast ihn vergessen. Unter Server & Zugänge beim Server „Verbindung
prüfen“ drücken und den Fingerabdruck bestätigen ([5.4](#54-verbindung-prüfen)).

**„Host-Schlüssel … hat sich geändert“:** Server neu installiert? Dann unter Server & Zugänge
beim Server in der Karte „Server-Schlüssel“ den alten vergessen, erneut prüfen und den neuen
Fingerabdruck bestätigen. Wenn nicht: erst herausfinden, warum – das kann auch ein Angriff sein.

**„Server antwortet nicht (Zeitüberschreitung …)“, „Der Server lehnt die Verbindung ab …“, „Kein Weg zum Server …“
oder „Den Namen … kennt das Netz nicht.“** im Terminal oder Dateimanager (dort mit „Zugriff auf die Quelle
fehlgeschlagen:“ davor): Nodvard Deck erreicht den Server nicht. Der Satz nennt meist Adresse und Port. Prüfen, ob der
Server an ist, die Adresse unter Server & Zugänge stimmt und dort SSH läuft. Im Log steht dazu kein Traceback.

**„Server nicht erreichbar – bitte gleich noch einmal versuchen.“ bei der Anmeldung:** Das
Dashboard antwortet nicht. Auf dem Rechner mit Nodvard Deck
`curl -s http://localhost:8080/api/v1/health` und, im Ordner mit der Compose-Datei,
`sudo docker compose ps` prüfen.
Steht stattdessen **„Server meldet: …“** da (auch beim Neuladen der Seite), antwortet das
Dashboard und nennt den Grund, z. B. dass die Platte voll ist – dann diesen Grund beheben.
Abgemeldet wird man dabei nicht.

**„Gerade sind viele Anmeldungen gleichzeitig im Gange. Bitte versuche es in ein paar Sekunden noch einmal.“ bei der
Anmeldung:** Nodvard Deck prüft nur wenige Passwörter gleichzeitig, damit viele Anmeldeversuche auf einmal das Dashboard
nicht ausbremsen. Ein paar Sekunden warten und noch einmal versuchen; wer schon angemeldet ist, arbeitet normal weiter.
Kommt die Meldung öfter, gibt es sehr viele Anmeldeversuche auf einmal; fehlgeschlagene stehen im Protokoll
(„Anmeldung fehlgeschlagen“). Bei einem Namen, den es nicht gibt, steht dort unter „Wer“ nur
„Nicht angemeldet · unbekannt“, nie die Eingabe selbst (es könnte ein ins Namensfeld getipptes Passwort sein). Nur der
Inhaber sieht beim Aufklappen der Zeile eine Kennung (`username_ref`) und die Länge der Eingabe; gleiche Eingaben haben
dieselbe Kennung.

## 12. Ausgesperrt? Notfall-Befehle

Wenn jemand – vor allem der Inhaber – Passwort oder Zwei-Faktor verloren hat und auch nicht
mehr über die Weboberfläche hineinkommt, gibt es Befehle **auf dem Rechner, auf dem Nodvard Deck
läuft** (z. B. dem Raspberry Pi), im Ordner mit der Compose-Datei. Wer sie ausführen kann, hat
ohnehin Zugriff auf alles im Datenverzeichnis; deshalb braucht es keine Anmeldung, aber jede
Änderung steht im Protokoll (Akteur `system/cli`).

```bash
cd ~/nodvard-deck
sudo docker compose exec nodvard-deck python -m nodvard_deck.admin list-users
sudo docker compose exec nodvard-deck python -m nodvard_deck.admin reset-password admin
sudo docker compose exec nodvard-deck python -m nodvard_deck.admin disable-2fa admin
```

(`admin` steht hier für den Benutzernamen, den `list-users` zeigt; ein älterer Name mit Leerzeichen gehört in
Anführungszeichen. Bei einer Installation aus dem Repository statt `cd ~/nodvard-deck` in den Ordner `deploy/` des
Repositorys wechseln.)

- **`list-users`** zeigt Benutzername, Inhaber/Benutzer, gesperrt oder aktiv und ob
  Zwei-Faktor an ist.
- **`reset-password <benutzername>`** setzt ein neues, zufälliges Passwort und **gibt es aus**
  (z. B. `k7mq-x2vd-h9pa-tn4c`). Alle bestehenden Anmeldungen dieses Benutzers werden beendet.
  Sonst ändert sich nichts (Zwei-Faktor bleibt). Nach der Anmeldung unter Mein Konto ein
  eigenes Passwort setzen.
- **`disable-2fa <benutzername>`** schaltet die Zwei-Faktor-Anmeldung ab, löscht die
  Wiederherstellungs-Codes und beendet alle Anmeldungen. Danach geht es nur mit Passwort,
  Zwei-Faktor lässt sich unter Mein Konto neu einrichten.

Bei Fehlern (Benutzer gibt es nicht, Datenbank nicht erreichbar) steht eine verständliche
Meldung da. Der Befehl ändert nur Benutzer – keine Server, keine Schlüssel, keine Einstellungen.

**Ohne Befehlszeile** (Portainer, Docker Desktop, NAS): die Konsole des Containers von Nodvard Deck öffnen und den
Befehl dort **ohne** `sudo docker compose exec nodvard-deck` davor eingeben, also etwa
`python -m nodvard_deck.admin reset-password admin`. Den Container erkennst du an „nodvard-deck“ im Namen, meist
heißt er `nodvard-deck-nodvard-deck-1`. Die Konsole findest du so:

- **Portainer:** Containers → Container von Nodvard Deck → Symbol „Exec Console“ → „Connect“.
- **Docker Desktop:** „Containers“ → die Gruppe „nodvard-deck“ aufklappen → den Container darin anklicken → Reiter „Exec“.
- **Synology Container Manager:** Container → den von Nodvard Deck auswählen → „Details“ → Reiter „Terminal“ → „Erstellen“.
- **Unraid:** Reiter „Docker“ → Symbol des Containers von Nodvard Deck → „Console“.

Dieselbe Anleitung steht auf der Anmeldeseite unter „Passwort vergessen?“.

**Gut zu wissen:** Wenn Nodvard Deck jemanden „überall abmeldet“ (Passwort geändert, Zwei-Faktor
zurückgesetzt oder abgeschaltet, Notfall-Befehl) oder du dich abmeldest, endet die Anmeldung sofort,
auch für einen schon ausgestellten Zugangs-Schlüssel im Browser: Die nächste Anfrage damit wird
abgelehnt. Offene Terminal- und Konsolen-Sitzungen einer beendeten Anmeldung schließen sich nach
spätestens 15 Sekunden, die Live-Aktualisierung nach spätestens 30 Sekunden.

## 13. Nodvard Deck startet nicht: die Notseite

Scheitert der Start (die Migration, zu wenig Platz für die Kopie, Daten neuer als die Version, ein unerwarteter Fehler), startet Nodvard Deck **nicht**, sondern eine kleine **Notseite**
auf demselben Port (z. B. `http://192.168.1.10:8080`). Sie braucht nichts außer Python selbst und läuft deshalb auch, wenn im neuen Image etwas fehlt. Der Container bleibt oben und
startet **nicht endlos neu**. Sie lauscht auf derselben Adresse wie die Anwendung (`--host` im Startbefehl, sonst `UVICORN_HOST`); ist keine erkennbar (kein `--host`, ein
Hostname wie `localhost`), nur lokal auf `127.0.0.1` – so wie uvicorn selbst. Das Image startet mit `--host 0.0.0.0`, im Normalfall ist sie also im Heimnetz erreichbar.

1. **Ohne Code** zeigt die Seite nur, dass Nodvard Deck nicht gestartet ist, und die allgemeinen Wege zurück.
2. **Notfallcode holen:** Er steht im Protokoll des Containers, als Zeile „Notfallcode: XXXX-XXXX-XXXX“ in einem auffälligen Block, und in der Datei `.boot/rescue_code.txt` im Datenordner:

   ```bash
   cd ~/nodvard-deck && sudo docker compose logs nodvard-deck | grep -A3 Notfallcode
   ```

   Er bleibt bis zum nächsten erfolgreichen Start gleich. Falsche Eingaben werden gebremst (5 pro Rechner, insgesamt 25 in 10 Minuten, danach „Zu viele Fehlversuche“). Der richtige Code geht trotzdem
   immer sofort durch: Du musst nicht warten, auch wenn vorher jemand anderes im Netz oft falsch getippt hat.
3. **Mit Code** erscheinen Grund und bereinigtes Protokoll (ohne Pfade, Passwörter, SQL-Parameter) und die Schritte, die zum Grund passen:
   - **Neu versuchen:** beendet den Container-Prozess; die Neustart-Regel (`restart: unless-stopped`) startet ihn neu, und der Start läuft noch einmal. Ohne Neustart-Regel bitte von Hand starten.
   - **Stand vor dem Update wiederherstellen** (nur wenn die Daten neuer sind und eine heile Kopie da ist): merkt den Rückweg vor und startet neu; beim Start wird die Kopie eingespielt.
     Änderungen seit dem Update gehen verloren.
   - **Zurück auf die alte Version:** Image-Version in der Compose-Datei zurückstellen und den Container neu starten (siehe [10.4](#104-updates-die-kopie-vor-der-migration)).
4. `GET /api/v1/health` antwortet auf der Notseite mit **503** `{"status":"rescue"}`; der Healthcheck des Containers zeigt deshalb „unhealthy“. Wer aus dem Repository mit `scripts/deploy_pi.sh`
   ausliefert, bekommt dadurch automatisch das alte Image zurück (ohne die volle Wartezeit).

Was die Notseite **nicht** kann: eine Kopie zum Herunterladen anbieten. Die Kopie liegt im Datenordner unter `backups/vor-update/` und lässt sich dort (oder per `docker cp`) sichern.
Fällt die Notseite selbst aus, wartet der Container, statt sich zu beenden – Ursache im Protokoll, dann von Hand neu starten.

Wichtig: Ein zweiter Container mit demselben Datenordner (`docker compose run` neben dem laufenden Dienst) ändert nichts; `nodvard_deck.boot` beendet sich dort mit Code 75, weil die laufende Anwendung (oder die Notseite)
die Sperre des Datenordners hält.

## Anhang: Ohne Oberfläche

Normalerweise nimmst du die Seite „Server & Zugänge“ (5.). Nur wenn die Oberfläche nicht
erreichbar ist (z. B. beim ersten Einrichten per SSH auf dem Rechner mit Nodvard Deck), geht
alles auch über die Nodvard-Deck-API mit diesem kleinen Hilfsskript. Es gibt keinen Einrichtungsbefehl und keine
Verbindungsprüfung – den Schlüssel erzeugst du selbst, und den öffentlichen Teil trägst du von
Hand in `~/.ssh/authorized_keys` ein.

**Schlüssel erzeugen** (einmal, auf dem Rechner mit Nodvard Deck):

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/nodvard_ed25519 -C nodvard
cat ~/.ssh/nodvard_ed25519.pub        # diese eine Zeile kommt auf die Server
```

Ohne Passphrase (`-N ""`), weil Nodvard Deck beim Verbinden keine Passphrase abfragt. Nodvard Deck
bekommt eine eigene, verschlüsselte Kopie des privaten Schlüssels; die Datei auf diesem Rechner
brauchst du nur noch zum Eintragen und Testen.

**Hilfsskript speichern** (einmal, auf demselben Rechner) – den ganzen Block in die Shell kopieren, er legt
`~/lattice-server.py` an:

```bash
cat > ~/lattice-server.py <<'EOF'
#!/usr/bin/env python3
"""Nodvard Deck: Server anlegen, Adresse korrigieren, SSH-Zugang hinterlegen (docs/11)."""
import getpass, json, os, urllib.error, urllib.request

API = os.environ.get("LATTICE_URL", "http://localhost:8080").rstrip("/") + "/api/v1"
token = None


class Fehler(Exception):
    pass


def call(method, path, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            raw = res.read()
            return res.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as err:
        raise Fehler(f"HTTP {err.code}: {err.read().decode(errors='replace')[:300]}") from None


def ask(text, default=""):
    value = input(f"{text}{f' [{default}]' if default else ''}: ").strip()
    return value or default


try:
    status, data = call("POST", "/auth/login", {
        "username": ask("Nodvard-Deck-Benutzer").lower(),
        "password": getpass.getpass("Passwort: "),
        "client_type": "cli",
    })
    if status == 202:  # Zwei-Faktor-Anmeldung ist eingeschaltet
        status, data = call("POST", "/auth/mfa", {"mfa_token": data["mfa_token"], "code": ask("Code aus der App")})
except Fehler as exc:
    raise SystemExit(f"Anmeldung fehlgeschlagen: {exc}")
token = data["access_token"]

while True:
    try:
        _, hosts = call("GET", "/hosts")
    except Fehler as exc:
        raise SystemExit(f"{exc} -- nach 15 Minuten läuft die Anmeldung ab, dann Skript neu starten.")
    print()
    for h in sorted(hosts, key=lambda h: h["name"]):
        print(f"  {h['id']}  {h['name']:<26} {h['address']:<16} {h['os_family']}")
    print("\n1 = Server anlegen  2 = Adresse ändern  3 = SSH-Zugang hinterlegen  4 = SSH-Zugänge zeigen")
    print("5 = gemerkten SSH-Host-Schlüssel vergessen  q = Ende")
    choice = ask("Auswahl", "q")
    try:
        if choice == "1":
            name = ask("Kurzname, klein und ohne Leerzeichen (z. B. pi-host)")
            _, h = call("POST", "/hosts", {
                "name": name,
                "display_name": ask("Anzeigename", name),
                "address": ask("IP-Adresse"),
                "os_family": ask("Betriebssystem (linux/windows)", "linux"),
                "tags": [t.strip() for t in ask("Markierungen mit Komma (z. B. docker), leer = keine").split(",") if t.strip()],
            })
            print("Angelegt, ID:", h["id"])
        elif choice == "2":
            host_id = ask("ID des Servers")
            call("PATCH", f"/hosts/{host_id}", {"address": ask("Neue IP-Adresse")})
            print("Gespeichert.")
        elif choice == "3":
            host_id = ask("ID des Servers")
            user = ask("SSH-Benutzer", "nodvard")
            port = int(ask("SSH-Port", "22"))
            if ask("Anmeldung mit Schlüssel (s) oder Passwort (p)?", "s") == "p":
                kind, secret = "ssh_password", getpass.getpass("SSH-Passwort: ")
            else:
                with open(os.path.expanduser(ask("Privater Schlüssel", "~/.ssh/nodvard_ed25519"))) as f:
                    kind, secret = "ssh_key", f.read()
            call("POST", f"/hosts/{host_id}/credentials",
                 {"kind": kind, "username": user, "port": port, "secret_value": secret, "is_default": True})
            print("SSH-Zugang gespeichert und ab jetzt der Standard für diesen Server.")
        elif choice == "4":
            _, creds = call("GET", f"/hosts/{ask('ID des Servers')}/credentials")
            for c in creds:
                print(f"  {c['kind']:<13} {c['username']} Port {c['port']}{'  (Standard)' if c['is_default'] else ''}")
            if not creds:
                print("  Noch kein SSH-Zugang hinterlegt.")
        elif choice == "5":
            host_id = ask("ID des Servers")
            call("DELETE", f"/hosts/{host_id}/known-hosts/{ask('Schlüsseltyp aus der Fehlermeldung', 'ssh-ed25519')}")
            print("Vergessen. Jetzt in der Oberfläche „Verbindung prüfen“ drücken und den neuen Fingerabdruck bestätigen – vorher verbindet sich Nodvard Deck nicht mehr mit dem Server.")
        elif choice.lower() == "q":
            break
    except (Fehler, OSError, ValueError) as exc:
        print("Fehler:", exc)
EOF
```

**Benutzen:**

```bash
python3 ~/lattice-server.py
```

Anmelden mit einem Nodvard-Deck-Konto, das Server verwalten darf (Inhaber oder `admin`). Das Skript
zeigt alle Server mit ihrer **ID** (die steht auch in der Browser-Adresse einer Server-Seite:
`/hosts/<ID>`). Mit 1 legst du einen Server an, mit 2 änderst du die Adresse (ein gespeichertes
SSH-Passwort wird dabei gelöscht, danach mit 3 neu hinterlegen), mit 3 hinterlegst du
einen SSH-Zugang (Schlüssel `~/.ssh/nodvard_ed25519` oder Passwort), mit 4 zeigst du die Zugänge,
mit 5 vergisst du einen gemerkten Server-Schlüssel (nach einer Neuinstallation des Servers).
Fingerabdrücke bestätigt das Skript nicht. Nach „Vergessen“ (5) verbindet sich Nodvard Deck erst wieder, wenn du in
der Oberfläche „Verbindung prüfen“ gedrückt und den neuen Fingerabdruck bestätigt hast. Dasselbe gilt für neue Server,
solange „Neue Server-Schlüssel erst nach meiner Bestätigung merken“ an ist (bei neuen Installationen von Anfang an).

Der Schlüssel auf dem Server wird dann von Hand eingetragen, z. B. für einen eigenen Benutzer:

```bash
sudo adduser --disabled-password --gecos "Nodvard Deck" nodvard
sudo install -d -m 700 -o nodvard -g nodvard /home/nodvard/.ssh
echo 'HIER DIE ZEILE AUS nodvard_ed25519.pub' | sudo tee /home/nodvard/.ssh/authorized_keys
sudo chown nodvard:nodvard /home/nodvard/.ssh/authorized_keys
sudo chmod 600 /home/nodvard/.ssh/authorized_keys
sudo usermod -aG docker nodvard        # nur wo Docker läuft
```

Für Root-Rechte ohne Passwort (siehe [6.](#6-root-rechte-ohne-passwort-und-gruppe-docker) – praktisch root!):

```bash
echo 'nodvard ALL=(root) NOPASSWD: ALL' > /tmp/nodvard-sudo
sudo visudo -cf /tmp/nodvard-sudo && sudo install -m 0440 -o root -g root /tmp/nodvard-sudo /etc/sudoers.d/nodvard-nodvard
rm /tmp/nodvard-sudo
sudo visudo -c
```

Meldet das letzte `visudo -c` einen Fehler, die Datei **sofort** wieder löschen
(`sudo rm /etc/sudoers.d/nodvard-nodvard`) – eine kaputte sudo-Datei kann sudo lahmlegen. Der
Dateiname darf keinen Punkt enthalten, sonst ignoriert sudo die Datei. Prüfen, vom Rechner mit Nodvard Deck aus:

```bash
ssh -i ~/.ssh/nodvard_ed25519 nodvard@SERVER-IP 'sudo -n true && echo sudo-ok; docker ps >/dev/null && echo docker-ok'
```
