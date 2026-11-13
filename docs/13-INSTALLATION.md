# Installation je Plattform

> English version: [en/INSTALLATION.md](en/INSTALLATION.md)

Der kurze Weg mit drei Befehlen steht in der [Erst-Einrichtung, Abschnitt 1](11-ERST-EINRICHTUNG.md#1-schnellstart).
Hier stehen alle Wege nach Art sortiert: im Terminal, mit einer Web-Oberfläche, auf einem NAS und auf bestimmten Geräten.
Jeder Weg sagt, wie du die Seite öffnest, einen anderen Port nimmst, den Einrichtungscode findest und aktualisierst.
Danach geht es in der Erst-Einrichtung mit dem
[Einrichtungsassistenten](11-ERST-EINRICHTUNG.md#2-einrichtungsassistent-setup) weiter.

Um Server über dein Tailnet zu verbinden, folge der [Tailscale-Anleitung](13-TAILSCALE.md).
Sie führt durch die Installation auf Deck-Host und Server, die MagicDNS-Adresse und den SSH-Zugang.

Wer nur Docker hat und das Repository nicht kennt, braucht eine einzige Datei:
[`deploy/compose.standalone.yml`](../deploy/compose.standalone.yml). Das fertige Image kommt aus der
GitHub-Container-Registry (`ghcr.io/nodvard/deck`, für x86-Rechner und Raspberry Pi), es wird nichts gebaut.

- [Gut zu wissen, egal welcher Weg](#gut-zu-wissen-egal-welcher-weg) und [Kurze Fassung für Oberflächen](#kurze-fassung-für-oberflächen)
- [Im Terminal](#im-terminal): [Docker Compose](#docker-compose-im-terminal),
  [`docker run`](#ein-einzelner-docker-run-befehl-ohne-compose), [Podman](#podman)
- [Mit einer Web-Oberfläche](#mit-einer-web-oberfläche): [Portainer, Weg 1](#portainer-weg-1-aus-dem-repository),
  [Portainer, Weg 2](#portainer-weg-2-web-editor), [Dockge](#dockge)
- [Auf einem NAS](#auf-einem-nas): [Synology](#synology-container-manager-dsm-72-oder-neuer),
  [UGREEN](#ugreen-ugos-pro-app-docker), [QNAP](#qnap-container-station-3), [Unraid](#unraid),
  [TrueNAS SCALE](#truenas-scale-ab-2410), [OpenMediaVault](#openmediavault-plugin-compose),
  [ZimaOS und CasaOS](#zimaos-und-casaos)
- [Raspberry Pi, Proxmox und Docker Desktop](#raspberry-pi-proxmox-und-docker-desktop):
  [Raspberry Pi](#raspberry-pi), [Proxmox](#proxmox-vm-oder-lxc-container),
  [Docker Desktop](#docker-desktop-windows-macos-zum-ausprobieren)
- [Nach der Installation](#nach-der-installation)

## Gut zu wissen, egal welcher Weg

- **Erster Start:** Das Image (knapp 400 MB) wird geladen, danach legt der Container die Datenbank an.
  Oberflächen wie Portainer oder der Container-Bereich eines NAS zeigen ihn in dieser Zeit als „starting“
  oder noch nicht als „healthy“. Bis zu fünf Minuten ist das normal. Steht danach „unhealthy“, findest du
  den Grund im Protokoll.
- **Port:** Nodvard Deck antwortet im Container auf Port 8080. Ist 8080 auf dem Gerät schon belegt, änderst
  du nur die Zahl **links** vom Doppelpunkt (`"8081:8080"`), rechts bleibt immer `8080`. Auf manchen NAS ist
  8080 von Haus aus vergeben; die Wege unten sagen, wo.
- **HTTP und Erreichbarkeit:** Nodvard Deck lauscht im Container standardmäßig auf `0.0.0.0:8080`, also auf
  allen IPv4-Adressen, und spricht unverschlüsseltes HTTP. Die Portfreigabe `"8080:8080"` in den Beispielen
  veröffentlicht den Port auf allen Netzwerkadressen des Rechners. Für HTTPS brauchst du einen
  [Reverse-Proxy](12-REVERSE-PROXY.md). Den direkten Zugriff beschränkst du wie im folgenden Abschnitt.
- **Daten:** Alles liegt im Docker-Speicher (Volume) `nodvard-deck-data`. Das ist auch auf einem NAS der
  empfohlene Weg für neue Installationen. Ohne eigene `user:`-Vorgabe startet das Image kurz als root,
  übernimmt `/app/data` samt Inhalt für **1000:1000** und wechselt vor Migration und Anwendung zu diesem
  Benutzer. So starten auch ältere root-eigene Datenordner nach einem Update weiter. Die Nummer muss zu
  keinem Benutzer des NAS gehören. Die mitgelieferten Compose-Dateien setzen dagegen `user: "1000:1000"`
  und `cap_drop: ALL`; dabei entfällt die automatische Besitzübernahme. Gehören vorhandene Daten noch root,
  führe einmal die Befehle unten aus. Auf NAS-Freigaben mit eigenen Zugriffsregeln (ACL) oder Netzlaufwerken
  braucht Benutzer 1000 zusätzlich Schreibrechte. Ist `/app/data` nicht beschreibbar, bricht der Start mit
  „FEHLER: /app/data ist für Benutzer … nicht beschreibbar.“ und dem Reparaturbefehl ab. Klappt es,
  können danach nur noch der Benutzer 1000 und root den Ordner lesen; Dateimanager oder
  Sicherungsprogramme, die als anderer Benutzer laufen, kommen nicht mehr an die Dateien. Sichere Nodvard
  Deck deshalb mit der eingebauten Sicherung ([Erst-Einrichtung, Abschnitt 6](11-ERST-EINRICHTUNG.md#6-das-dashboard-selbst-sichern)).
- **Einrichtungscode:** Er steht im Protokoll (Log) des Containers, in einem Rahmen aus `=`-Zeichen. Bei jedem
  Weg steht, wo du das Protokoll findest.

Vorhandene root-eigene Daten bei `user: "1000:1000"` einmalig umstellen, im Ordner mit `compose.yml`:

```bash
docker compose stop nodvard-deck
docker compose run --rm --no-deps --user 0:0 --cap-add CHOWN --cap-add DAC_OVERRIDE --entrypoint /usr/local/bin/migrate-data nodvard-deck
docker compose up -d nodvard-deck
```

Der Hilfscontainer ändert nur die Besitzrechte in `/app/data`. Daten und Volume-Namen bleiben erhalten.

### Zugriff auf diesen Rechner oder das LAN beschränken

Die Beispiele verwenden Host-Port 8080. Behalte bei Compose, `docker run` und `podman run` deinen bisherigen
Host-Port bei: Aus `8081:8080` wird `127.0.0.1:8081:8080` oder `192.168.1.10:8081:8080`.
Passe auch die Adresse im Browser bzw. das Proxy-Ziel an: `http://127.0.0.1:8081` oder
`http://192.168.1.10:8081`. Der Container-Port rechts bleibt `8080`.

**Reverse-Proxy direkt auf demselben Rechner:** Ersetze beim Dienst `nodvard-deck` die vorhandene Liste
unter `ports:` durch diese Zeile, damit Port 8080 nur über `127.0.0.1` auf diesem Rechner erreichbar ist:

```yaml
services:
  nodvard-deck:
    ports:
      - "127.0.0.1:8080:8080"
```

Der Proxy leitet dann an `http://127.0.0.1:8080` weiter. Läuft er als Container im selben Docker-Netz,
lasse `ports:` bei Nodvard Deck ganz weg und leite an `http://nodvard-deck:8080` weiter. Die Adresse des
Proxys trägst du außerdem als [vertrauenswürdigen Proxy](12-REVERSE-PROXY.md#4-adresse-eintragen) ein.

**Nur im LAN:** Ersetze die vorhandene Portfreigabe stattdessen durch die LAN-Adresse des Rechners,
hier als Beispiel `192.168.1.10`:

```yaml
services:
  nodvard-deck:
    ports:
      - "192.168.1.10:8080:8080"
```

Öffne dann `http://192.168.1.10:8080`. Verwende die tatsächliche, möglichst feste LAN-Adresse deines
Rechners. Die Bindung wählt die Zieladresse, beschränkt aber keine Absender: Richte im Router keine
Portweiterleitung auf 8080 ein und erlaube den Zugriff in der Firewall nur aus deinem LAN.

Ändere die vorhandene Compose-Datei direkt und führe dort `docker compose up -d` aus. Die Ausschnitte
ersetzen nur `ports:`, die übrigen Einstellungen und der Datenspeicher bleiben stehen. Bei `docker run`
oder `podman run` ersetzt du entsprechend `-p 8080:8080` durch `-p 127.0.0.1:8080:8080` oder
`-p 192.168.1.10:8080:8080` und erstellst den Container wie im jeweiligen Weg beschrieben neu.

## Kurze Fassung für Oberflächen

Die Datei `compose.standalone.yml` liest den Port aus einer verschachtelten Variablen
(`${NODVARD_DECK_PORT:-${LATTICE_PORT:-8080}}`, der innere Teil ist ein
[alter Name](11-ERST-EINRICHTUNG.md#m-alte-namen-bei-älteren-installationen)). Nicht jede Oberfläche versteht diese
Schreibweise. Verwende in eigenen Konfigurationsdateien stets den aktuellen Namen `NODVARD_DECK_PORT`.
Das Auslieferungsskript `deploy_pi.sh` benennt alte `LATTICE_*`-Einträge in `deploy/.env` automatisch um;
bei manueller Bereitstellung ersetzt du alte Namen selbst. Für die NAS-Wege gibt es diese Fassung ohne Variablen;
den Port änderst du direkt in der Zeile unter `ports:`:

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

## Im Terminal

### Docker Compose im Terminal

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
  Ordner (mehr dazu in [Erst-Einrichtung, Abschnitt 2](11-ERST-EINRICHTUNG.md#2-einrichtungsassistent-setup)).
- **Aktualisieren:** im selben Ordner `docker compose pull && docker compose up -d`. Die Daten bleiben
  erhalten.

### Ein einzelner `docker run`-Befehl (ohne Compose)

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

### Podman

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

## Mit einer Web-Oberfläche

### Portainer, Weg 1: aus dem Repository

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

### Portainer, Weg 2: Web-Editor

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

### Dockge

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

## Auf einem NAS

Die Menünamen stehen hier so, wie sie in der englischen Oberfläche und Anleitung des Herstellers heißen. In
einer deutschen Oberfläche sind sie übersetzt.

### Synology (Container Manager, DSM 7.2 oder neuer)

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
  die Daten von Nodvard Deck. Vorher sichern ([Erst-Einrichtung, Abschnitt 6](11-ERST-EINRICHTUNG.md#6-das-dashboard-selbst-sichern)).
- **Daten:** im Speicher `nodvard-deck-data`. Lieber ein Ordner, z. B. `/volume1/docker/nodvard-deck:/app/data`?
  Ohne `user:`-Vorgabe übernimmt der Entrypoint vorhandene root-eigene Daten beim Update. Mit
  `user: "1000:1000"` das Projekt stoppen und die einmalige Umstellung oben per SSH im Projektordner
  ausführen. Bei abweichendem Dateinamen jeweils `docker compose -f docker-compose.yml` verwenden.
  Verhindern DSM-ACLs den Zugriff weiterhin, Benutzer 1000 Schreibrechte nur auf diesen Ordner geben.

### UGREEN (UGOS Pro, App „Docker“)

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
  Ohne `user:`-Vorgabe übernimmt der Entrypoint root-eigene Daten beim Update automatisch. Mit
  `user: "1000:1000"` das Projekt stoppen und die einmalige Umstellung oben per SSH im Speicherpfad des
  Projekts ausführen. Den vorhandenen Ordner und seine Zuordnung zu `/app/data` beibehalten.

### QNAP (Container Station 3)

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

### Unraid

Unraid bringt Docker mit, aber kein Docker Compose. Der einfachste Weg ist eine eigene Vorlage:

Vor dem ersten Start im Unraid-Terminal als root den Datenordner vorbereiten:

```bash
mkdir -p /mnt/user/appdata/nodvard-deck
chown -R 1000:1000 /mnt/user/appdata/nodvard-deck
chmod 700 /mnt/user/appdata/nodvard-deck
```

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
- **Daten:** im Ordner `/mnt/user/appdata/nodvard-deck`. Das Image startet als root, setzt die Besitzrechte
  des Ordners einmal auf `1000:1000` und läuft danach ohne root weiter. Setzt ein Rechte-Werkzeug von Unraid
  sie später zurück, richtet der Container das beim nächsten Start wieder ein.
- **Lieber mit Compose?** Das Plugin **Compose Manager Plus** (im Reiter **Apps** suchen) bringt Docker
  Compose mit. Dort unter **Docker** → **Compose** mit **Add Stack** einen Stack anlegen, die
  [kurze Fassung](#kurze-fassung-für-oberflächen) einfügen und im Menü des Stacks **Compose Up**; das Protokoll
  steht unter **View Logs**. Aktualisiert wird mit **Update**; der Knopf erscheint nur, wenn es eine neuere
  Version gibt, sonst heißt er **Force Update**.

### TrueNAS SCALE (ab 24.10)

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

### OpenMediaVault (Plugin „compose“)

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

### ZimaOS und CasaOS

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

Vor dem ersten Start per SSH oder im Terminal des Hosts den Datenordner vorbereiten:

```bash
sudo mkdir -p /DATA/AppData/nodvard-deck
sudo chown -R 1000:1000 /DATA/AppData/nodvard-deck
sudo chmod 700 /DATA/AppData/nodvard-deck
```

1. Im Bereich **Apps** auf **+** → **Install a customized app**.
2. Oben auf **Import**, Reiter **Docker Compose**, die Fassung oben einfügen → **Submit**.
3. Die übernommenen Angaben prüfen → **Install**.

- **Öffnen:** `http://<Adresse-des-Geräts>:8080`
- **Port schon belegt?** Im Menü der App **Settings**, die linke Portzahl ändern, **Save**.
- **Einrichtungscode:** Im Menü der App **Settings** → oben **Terminal and Logs** → Reiter **Logs**.
- **Aktualisieren:** Im Menü der App **Check then update**.
- **Daten:** im Ordner `/DATA/AppData/nodvard-deck`. Das Image startet als root, setzt die Besitzrechte
  des Ordners einmal auf `1000:1000` und läuft danach ohne root weiter. Werden sie später zurückgesetzt,
  richtet der Container das beim nächsten Start wieder ein.

## Raspberry Pi, Proxmox und Docker Desktop

### Raspberry Pi

- Ein Raspberry Pi 4 oder 5 mit 4 GB RAM und einem **64-Bit-System** (`uname -m` zeigt `aarch64`); das
  Image gibt es für `linux/arm64`. Im Leerlauf braucht Nodvard Deck rund 100 MB Arbeitsspeicher, das Image
  ist knapp 400 MB groß. Docker mit Docker Compose muss installiert sein.
- Eine SD-Karte kann sterben: Sicherungen nicht nur auf dem Pi lassen ([Erst-Einrichtung, Abschnitt 6](11-ERST-EINRICHTUNG.md#6-das-dashboard-selbst-sichern));
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

### Proxmox (VM oder LXC-Container)

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

### Docker Desktop (Windows, macOS) zum Ausprobieren

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

## Nach der Installation

Die Datenbank wird beim Containerstart automatisch angelegt bzw. aktualisiert, dafür ist nichts
zu tun. Den Einrichtungscode ([Erst-Einrichtung, 2.1](11-ERST-EINRICHTUNG.md#21-den-einrichtungscode-finden)) zeigt der Assistent mit einer Anleitung, wo er steht
(Befehlszeile, Docker Desktop, Portainer, Synology, Unraid).

**Alle Befehle der [Erst-Einrichtung](11-ERST-EINRICHTUNG.md)** (Einrichtungscode, Protokoll, Notfall-Befehle,
Wiederherstellen) laufen im Ordner mit der `compose.yml`, hier also nach `cd ~/nodvard-deck`.
Die Beispiele schreiben `sudo docker …`; ist dein Benutzer in der Gruppe `docker` (oder nutzt du
Docker Desktop), lässt du `sudo` weg. **Mit Portainer oder einer NAS-Oberfläche** gibt es diesen Ordner
nicht: Protokoll und Neustart findest du beim Container in der Oberfläche, Befehle wie die Notfall-Befehle
gibst du in seiner Konsole ein ([Erst-Einrichtung, Abschnitt 7.3](11-ERST-EINRICHTUNG.md#73-ausgesperrt-notfall-befehle)). Für `docker run` und
Podman steht beim jeweiligen Weg, wie die Befehle dann heißen.
