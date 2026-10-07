# Auslieferung

Ein Image, ein Container, ein Volume (`/app/data`) — [docs/00-DECISIONS.md D-10](../docs/00-DECISIONS.md#d-10-auslieferung).
Multi-Arch: `linux/amd64` (x86-Rechner) und `linux/arm64` (Raspberry Pi).

Wer nur Docker hat, beginnt mit „Installation ohne das Repo“. Die Abschnitte „Bauen“, „Starten
aus dem Repo“ und „Deploy auf einen eigenen Pi per SSH“ richten sich an alle, die Nodvard Deck
aus dem Quellcode bauen.

## Installation ohne das Repo

Für Nutzer, die nur Docker haben: Die Datei [`compose.standalone.yml`](compose.standalone.yml)
als `compose.yml` in einen leeren Ordner laden und dort ausführen. Sie zieht das fertige Image
`ghcr.io/nodvard/deck:latest` (Multi-Arch: `linux/amd64` und `linux/arm64`), baut nichts und braucht
kein Repository:

```bash
mkdir nodvard-deck && cd nodvard-deck
curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml
docker compose up -d
# dann im Browser http://<Adresse-des-Rechners>:8080 öffnen
```

Port ändern: Umgebungsvariable `NODVARD_DECK_PORT` (Rückfall: `LATTICE_PORT`, sonst 8080). Die Daten liegen im
Volume `nodvard-deck-data`; ein eingebundener Ordner (`./daten:/app/data`) geht auch, weil der Entrypoint die
Besitzrechte selbst setzt (siehe unten). Das Image entsteht bei jedem Tag `v*` durch
`.github/workflows/release.yml` (Tags `:<version>`, `:<major>.<minor>`, `:latest`).

**Benutzerwechsel im Container:** Das Image startet als root. `entrypoint.sh` setzt dann die Besitzrechte an
`/app/data` (nur dort, ohne Links zu folgen, nur das, was nicht schon `lattice` gehört) und startet sich
per `setpriv` als Benutzer `lattice` (UID 1000) neu – Migration und Anwendung laufen nie als root. Läuft der
Container schon als Nicht-root (`user: "1000:1000"`, Kubernetes), ändert sich nichts; dann muss der Ordner
diesem Benutzer gehören. `docker compose exec` läuft als root; `python -m nodvard_deck.admin` wechselt selbst zum
Besitzer des Datenordners. Hilfscontainer mit `--entrypoint` (`backup.sh`, `restore.sh`) laufen mit
`--user 1000:1000`.

**Dateirechte:** Was `entrypoint.sh` startet (Migration, Anwendung, Notseite), legt neue Dateien und Ordner nur für
den Besitzer an (`umask 077`); `python -m nodvard_deck.admin` und `python -m nodvard_deck.boot` tun das auch ohne den
Entrypoint (`docker compose exec`). Bei jedem Start setzt `entrypoint.sh`, nie als root, `/app/data` auf 0700 und
nimmt Gruppe und anderen alle Rechte an den Dateien und Ordnern darin (Links bleiben unberührt); so werden auch
ältere Installationen mit offenen Rechten (0644/0755) nachgezogen. Ein eingebundener Hostordner ist danach nur für
UID 1000 und root lesbar: Backup-Werkzeuge oder Dateimanager, die als anderer Benutzer laufen, brauchen `sudo`.
Unterstützt der Speicher keine Rechte (manche NAS-Freigaben), läuft der Start weiter, im Protokoll steht höchstens
eine Warnung. Code, Erweiterungen und Oberfläche unter `/app` gehören root und sind für `lattice` nur lesbar; unter
`/app` darf `lattice` nur in `/app/data` schreiben. So kann ein Prozess als `lattice` in den Code unter `/app` nichts
einschleusen, das später als root startet (Healthcheck, `docker compose exec`).

**Zusatzgruppen (`group_add:`):** Durch den Benutzerwechsel (`--clear-groups`) gelten sie nicht mehr. Wer sie braucht
(z. B. eine Gruppe, die einen NAS-Ordner freigibt), startet den Container selbst als Nicht-root und setzt beides
zusammen: `user: "1000:1000"` und `group_add: [...]`; der Datenordner muss dann diesem Benutzer gehören.

## Bauen

```bash
# Aus dem Repository-Root:
docker buildx build -f deploy/Dockerfile \
  --platform linux/amd64,linux/arm64 \
  -t nodvard-deck:latest \
  --load .    # --load fuer eine einzelne lokale Architektur; fuer beide zugleich
              # braucht es einen Push in eine Registry (--push statt --load) --
              # `docker buildx` kann kein Multi-Arch-Manifest lokal "laden".
```

Für einen einzelnen Zielrechner reicht ein normaler Build ohne `buildx`:

```bash
docker build -f deploy/Dockerfile -t nodvard-deck:latest .
```

Laufzeitdaten gelangen nicht ins Image: `.dockerignore` schließt in jedem Unterordner (z. B. ein `backend/data/` nach
einem Start aus `backend/`) Datenordner `data/`, Datenbanken (`*.db`, `*.sqlite`), Sicherungen (`*.ndbak`), Schlüssel
(`*.key`, `jwt_secret*`, `vault_keyring.json`) und `.env`-Dateien aus.

## Starten aus dem Repo

```bash
cd deploy
docker compose up -d
```

Danach `http://<host>:8080/` öffnen. Eine frische Installation (kein Nutzer in der
Datenbank) zeigt automatisch den **Einrichtungsassistenten** (`/setup` statt `/login`,
solange `GET /api/v1/auth/bootstrap` `{"needed": true}` liefert): Administrator-Konto,
Zeitzone, Auswahl der Module, optional Zwei-Faktor-Anmeldung und Aussehen (Produktname,
Untertitel, Akzentfarbe). Logo, alle fünf Farben und Support-Link stellt man danach unter
Einstellungen → Aussehen ein (`PUT /api/v1/branding`). Schritt für Schritt:
[docs/11-ERST-EINRICHTUNG.md](../docs/11-ERST-EINRICHTUNG.md).

**Log-Rotation:** Die Docker-Logdatei des Dashboards ist auf 3 Dateien à 10 MB begrenzt
(`logging:` in beiden mitgelieferten Compose-Dateien), damit sie nicht ungebremst wächst. Die
Einstellung gilt, sobald der Container mit `docker compose up -d` neu erstellt wird.

## Deploy auf einen eigenen Pi per SSH

Für alle, die aus dem Quellcode bauen und das Ergebnis auf einen eigenen Raspberry Pi (oder
einen anderen Linux-Rechner mit Docker) bringen wollen. Ein Docker-Build **auf dem Pi** ist
langsam und belastet ihn stark (hohe Last, Swap, verzögerter Containerstart, dadurch auch
falsche „DEPLOY-FAIL“-Meldungen). `scripts/deploy_pi.sh` baut deshalb auf dem PC und kopiert
nur das fertige Image:

```bash
# Aus dem Repository-Root in Git Bash, Docker Desktop läuft (QEMU für arm64 ist dabei):
scripts/deploy_pi.sh
```

Was das Skript tut:

1. `docker buildx build --platform linux/arm64 -f deploy/Dockerfile -t nodvard-deck:pi-<sha> --load .`
   Die Frontend-Stufe läuft dank `FROM --platform=$BUILDPLATFORM` nativ, nur die
   Python-Stufe wird für arm64 gebaut (alle Wheels vorhanden, ~2 Min beim ersten Mal).
2. `deploy/` (compose, entrypoint) per `git archive` auf den Pi kopieren.
3. `docker save | gzip | ssh 'gunzip | docker load'`.
4. Vorab (`pi_switch.sh precheck`, bis zu 5 Versuche im Abstand von 3 s): Ist der Dienst schon
   eingerichtet (`GET /api/v1/auth/bootstrap` → `"needed":false`)? Antwortet das alte Image mit 404,
   weil es den Endpunkt nicht kennt, gilt er als eingerichtet, sofern der Container das Volume
   `deploy_lattice_data` hat. **Läuft ein Dashboard-Container, antwortet aber gar nicht, bricht das
   Skript ab, bevor etwas verändert wird** (erst klären: `docker ps`, `docker logs`).
   Danach merkt sich das Skript (`pi_switch.sh loaded`), welche Erweiterungen der laufende Dienst geladen hat. Jeder Start
   und jedes Ein- oder Ausschalten einer Erweiterung schreibt die Zeile `Erweiterungen geladen: <Kennungen>` ins Protokoll:
   Speicher-Kennungen (nach einer Umbenennung die alte, z. B. `nexus-soc` für Nodvard Shield), sortiert, durch Komma
   getrennt, `(keine)`, wenn nichts läuft. Gelesen wird die letzte solche Zeile im ganzen Docker-Protokoll des laufenden
   Containers. Kennt der alte Stand die Zeile noch nicht (erster Lauf mit dieser Prüfung) oder ist sie aus dem Protokoll
   herausgerollt (Docker behält 3 × 10 MB), gibt es nur einen Hinweis und keinen Vergleich. Vorher prüfen:
   `docker logs deploy-nodvard-deck-1 2>&1 | grep 'Erweiterungen geladen' | tail -n 1`; fehlt die Zeile, erst
   `docker restart deploy-nodvard-deck-1`.
5. Auf dem Pi läuft `deploy/pi_switch.sh switch <tag>` (die Schritte stehen dort, getestet in
   `backend/tests/test_deploy_switch.py`), **von der ssh-Leitung abgekoppelt** (`setsid`, Ausgabe in
   `pi_switch.out`/`pi_switch.err` im Deploy-Ordner): Reißt die Verbindung ab, läuft der Schritt auf
   dem Pi zu Ende. Das Skript meldet dann `DEPLOY-FAIL … abgerissen` **ohne** automatischen
   Rollback; bitte auf dem Pi nachsehen (`docker ps -a`, `cat pi_switch.out`, `bash pi_switch.sh verify`):
   - **vorab prüfen, ohne etwas zu ändern** (sonst Abbruch mit Exit 3, `DEPLOY-FAIL`, kein
     Rollback nötig): Docker erreichbar (`docker info`), das Image vorhanden,
     `docker compose -p deploy config -q` läuft durch (zu alte Compose-Version fällt hier auf);
   - das bisherige `nodvard-deck:latest` als `nodvard-deck:previous` sichern; gibt es das noch
     nicht (**erster Übergang** vom alten Namen), wird das alte `lattice:latest` zu
     `nodvard-deck:previous`;
   - gab es `nodvard-deck:latest` schon vorher und liegt ein **gestoppter** `deploy-lattice-1` herum
     (früherer Übergang, Aufräumen war gescheitert), wird er entfernt: Er wäre beim nächsten
     Rollback ein uraltes Image;
   - das neue Image als `nodvard-deck:latest` taggen;
   - den alten Container `deploy-lattice-1` (Dienst `lattice` vor der Umbenennung) nur
     **stoppen, nicht löschen** – er belegt sonst Port 8080, bleibt aber als Rückweg erhalten;
   - `docker compose -p deploy up -d --no-build` (Projekt immer fest `deploy`; kein
     `--remove-orphans`, das wirkt auf das ganze Projekt);
   - prüfen (`verify`): `deploy-nodvard-deck-1` hat den Status `running` (keine
     Neustart-Schleife), trägt das Image von `nodvard-deck:latest` und hat unter `/app/data` das
     Volume `deploy_lattice_data` (ein anderes wäre leer); der alte Container läuft nicht mehr.
6. Bis zu 4 Minuten auf `"status":"ok"` von `/api/v1/health` warten, dann `verify` noch einmal
   („gesund“ allein könnte ein alter Container sein), dann den **Datencheck**: War der Dienst
   vorher eingerichtet, muss `/api/v1/auth/bootstrap` weiter `"needed":false` liefern (sonst sind
   die Daten weg). Dann der **Erweiterungen-Vergleich**: Jede Erweiterung, die vorher geladen war, muss im neuen
   Container wieder geladen sein (neue dazu sind in Ordnung). Das Skript wartet dafür bis zu 30 Sekunden auf die Zeile des
   neuen Stands (`LOADED_WAIT_S`, nie über den Rest der Health-Wartezeit hinaus). Fehlt eine Erweiterung oder fehlt die
   Zeile ganz, obwohl der alte Stand eine hatte, gilt das wie ein gescheiterter Datencheck. Die Ausgabe nennt die
   fehlenden Kennungen. Soll eine davon mit dem neuen Stand bewusst wegfallen: sie erst in den Einstellungen
   ausschalten, dann das Deploy wiederholen. Dann die Logs der letzten 5 Minuten auf `Traceback` prüfen. Scheitert
   diese Log-Prüfung selbst (Verbindung weg), ist der Zustand unklar: `DEPLOY-FAIL`, ebenfalls ohne automatischen
   Rollback.
7. Scheitert einer der Schritte, wird **automatisch zurückgeschaltet** (`pi_switch.sh rollback`):
   Beim ersten Übergang wird der neue Container entfernt und der alte mit `docker start`
   wieder gestartet (ohne Compose), aber nur, wenn sein Image dasselbe ist wie
   `nodvard-deck:previous`; sonst, und bei späteren Deploys, läuft `nodvard-deck:previous` per Compose.
   Danach wartet das Skript, bis `/api/v1/health` wieder antwortet. Klappt das nicht, steht
   **`ROLLBACK-FAIL`** in der Ausgabe (Dienst vermutlich aus), sonst `DEPLOY-FAIL: Rollback
   ausgeführt`. Gibt es nichts zum Zurückschalten (frische Maschine), steht das ehrlich dort.

   **Ausnahme: kein automatischer Rollback nach einer Migration.** Hat der neue Container die
   Datenbank umgebaut und ist gestartet (`.boot/state.json`: `last_migration.at` nicht älter als der
   Container, `started_ok: true`), lehnt das alte Image die Daten ab und zeigt nur die Notseite. Meldet danach eine Prüfung ein Problem (Datencheck, Erweiterungen, Tracebacks, Absturz oder
   Neustart-Schleife nach dem Health-Ok), endet das Skript mit `DEPLOY-FAIL … hat die Datenbank aber
   schon umgebaut … KEIN automatischer Rollback`. Lässt sich der Migrationsstand nicht feststellen
   (Datei nicht lesbar, Verbindung weg), heißt es `Zustand unklar, KEIN automatischer Rollback`.
   Dann zuerst `docker ps -a` und `docker logs --since 10m deploy-nodvard-deck-1` ansehen. Läuft
   die neue Version nicht brauchbar und soll der alte Stand zurück, bewusst
   `bash pi_switch.sh rollback` ausführen und auf der Notseite (Port 8080) den Stand von vor dem
   Update wiederherstellen. Den Notfallcode dafür zeigt `docker logs deploy-nodvard-deck-1`.
   Änderungen seit dem Update gehen dabei verloren. Das klappt nur, wenn die alte Version
   (`nodvard-deck:previous`) die Notseite schon kennt. Sonst die neue Version laufen lassen und den
   Fehler dort beheben. Bei falschem Volume
   oder falschem Image wird weiter automatisch zurückgeschaltet, dann sind die alten Daten unberührt.
8. Erst wenn alles gestimmt hat, räumt `pi_switch.sh cleanup` auf: den alten Container
   `deploy-lattice-1` löschen, alte `nodvard-deck:pi-*`-Tags (außer den Ständen hinter
   `latest`/`previous`) und die alten `lattice:*`-Images. Die `lattice:*`-Images bleiben, solange
   `nodvard-deck:previous` noch derselbe Stand wie `lattice:latest` ist, also bis zum zweiten
   Deploy; im Zweifel bleiben sie stehen. Danach `docker image prune -f`. Das Volume wird nie
   angefasst.

Solange noch nicht aufgeräumt ist (oder wenn Docker/ssh mittendrin abbricht), belegt der alte
Stand auf dem Pi ein paar hundert MB: Die alten `lattice:*`-Images und der gestoppte Container
kosten Platz (ein Image pro Stand), sie verschwinden mit dem zweiten erfolgreichen Deploy
bzw. per `bash pi_switch.sh cleanup`.

**Was bewusst gleich bleibt:** Das Volume heißt weiter `deploy_lattice_data` (in der
Compose-Datei fest mit `name: deploy_lattice_data` eingetragen, Compose-Projekt fest `name: deploy`,
Schlüssel `lattice_data`; alle Skripte rufen `docker compose -p deploy` auf), die Daten werden
nicht kopiert. Der Zielordner auf dem Zielrechner ist `~/<DEPLOY_ROOT>`; der Standardwert
von `DEPLOY_ROOT` ist `lattice-deploy-test` (ein Name aus der Zeit vor der Umbenennung, nur
ein Vorgabewert, frei wählbar). Dort liegt auch die `.env`. Den Dienst nicht mit
`container_name:`/`hostname:` festnageln: das Dashboard erkennt sich an Hostname = Container-ID.

**Umbenennung und Migration nicht im selben Deploy.** Der Rollback setzt nur das Image zurück,
Migrationen bleiben. Ein Stand mit neuer Migration lässt sich daher nicht mehr auf das alte
Image zurückschalten. Wer von einem Stand vor der Umbenennung (`lattice:*`) kommt, liefert
deshalb zuerst die Umbenennung allein aus und die Migration danach (vorher sichern).

Vorher gehört die komplette Test-Suite grün (siehe [README.md, Entwicklung](../README.md#entwicklung), ~6 Min) – das Skript
prüft das bewusst nicht selbst. `scripts/deploy_pi.sh --no-build` liefert das zuletzt
gebaute Image erneut aus.

**Zielrechner festlegen.** Das Skript hat keinen festen Zielrechner. Es liest
`PI_HOST` (SSH-Ziel `benutzer@adresse`, Pflicht – ohne bricht das Skript mit einer Meldung ab)
aus der Umgebung oder aus der lokalen, git-ignorierten Datei `deploy/.env.deploy`:

```bash
cp deploy/.env.deploy.example deploy/.env.deploy   # dann PI_HOST=… eintragen
# oder einmalig:
PI_HOST=admin@192.168.1.50 scripts/deploy_pi.sh
```

Weitere Werte (`DEPLOY_ROOT`, `CONTAINER`, `HEALTH_TIMEOUT_S`) sind dort ebenfalls
setzbar; gesetzte Umgebungsvariablen haben Vorrang vor der Datei.

**Eigener DNS-Resolver für den Container.** Der Container nutzt standardmäßig die
öffentlichen Resolver 1.1.1.1/1.0.0.1. Braucht er einen lokalen Resolver (z. B. einen
Pi-hole, der interne Namen wie `cloud.home.example` auflöst), setzt man
`NODVARD_DECK_DNS_1` (und optional `NODVARD_DECK_DNS_2`) in `deploy/.env.deploy`. Die
alten Namen `LATTICE_DNS_1`/`LATTICE_DNS_2` gelten weiter (sind beide gesetzt, gewinnt der
neue). Das Skript legt die Werte beim Ausliefern als `deploy/.env` auf dem Zielrechner ab
(unter **beiden** Namen), wo `docker compose` sie liest; eine bestehende `deploy/.env` mit nur
`LATTICE_DNS_*` funktioniert unverändert weiter, weil Compose auf den alten Namen zurückfällt. Ohne Skript (Rückfallweg) legt man `deploy/.env` dort
selbst an.

**Rückfallweg, falls Docker Desktop nicht läuft** (baut auf dem Pi, dauert ~10 Min und
belastet ihn stark):

```bash
# ~/lattice-deploy-test ist der Standardordner (DEPLOY_ROOT); bei anderem DEPLOY_ROOT anpassen.
# Vor dem Auspacken den alten Ordner extensions/ löschen: sonst bleibt z. B. extensions/nexus-soc neben
# extensions/shield liegen, und Nodvard Deck startet wegen doppelter Migrationen nicht.
git archive -o /tmp/nodvard-deck.tar HEAD && scp /tmp/nodvard-deck.tar "$PI_HOST":~/nodvard-deck.tar
ssh "$PI_HOST" 'rm -rf ~/lattice-deploy-test/extensions && tar -xf ~/nodvard-deck.tar -C ~/lattice-deploy-test \
  && cd ~/lattice-deploy-test/deploy \
  && { docker stop deploy-lattice-1 2>/dev/null || true; } && docker compose -p deploy up -d --build'
# danach bis zu 4 Min: curl -s localhost:8080/api/v1/health  ->  "status":"ok"
# und: curl -s localhost:8080/api/v1/auth/bootstrap  ->  {"needed":false}  (sonst: Daten fehlen!)
# Klappt es: docker rm deploy-lattice-1   (der alte Container, nur noch Altlast)
# Klappt es nicht: docker rm -f deploy-nodvard-deck-1 && docker start deploy-lattice-1
# aufräumen: docker builder prune -f --reserved-space 2gb; docker image prune -f
```

Nach dem Bauen auf dem Pi trägt das Image den Namen `nodvard-deck:latest` (aus der Compose-Datei).
Den alten Container `deploy-lattice-1` vorher stoppen (er belegt sonst Port 8080), aber nicht
mit `--remove-orphans` wegräumen lassen: das wirkt auf das ganze Projekt.

Kaputter Stand nach einem Deploy: auf dem Pi im Deploy-Ordner
`bash pi_switch.sh rollback` (oder von Hand
`docker tag nodvard-deck:previous nodvard-deck:latest && docker compose -p deploy up -d --no-build`).
Zurück auf einen **älteren Commit** nicht mit dem alten `deploy_pi.sh` ausliefern (es kennt den
neuen Dienstnamen nicht und würde den alten Container neben dem neuen starten), sondern über
`nodvard-deck:previous` zurückgehen.

## Umgebungsvariablen: neue Namen, alte gelten weiter

Alle Einstellungen des Dashboards heißen jetzt `NODVARD_DECK_<NAME>` (früher
`LATTICE_<NAME>`), zum Beispiel `NODVARD_DECK_ENV`, `NODVARD_DECK_LOG_JSON`,
`NODVARD_DECK_DATA_DIR` und `NODVARD_DECK_DNS_1`/`_2` (nur Compose). `NODVARD_DECK_API_DOCS=1` schaltet die
API-Doku (`/docs`, `/redoc`, `/openapi.json`) auch außerhalb des Entwicklungsmodus ohne Anmeldung frei;
standardmäßig ist sie aus, angemeldete Admins bekommen das Dokument über `GET /api/v1/system/openapi.json`.
`NODVARD_DECK_MAX_BODY_BYTES` begrenzt die Größe einer Anfrage (Standard 1 MiB, darüber `413`; Uploads wie Sicherung,
Logo, Dokumente und Bilder haben eigene Grenzen), `NODVARD_DECK_FILES_MAX_UPLOAD_BYTES` die Größe eines Uploads im
Dateimanager (Standard `0`, keine Grenze).

- **Die alten Namen gelten weiter.** Eine vorhandene `.env`, eine eigene Compose-Datei oder
  ein `docker run -e LATTICE_…` laufen unverändert. Beim Start steht einmalig eine Warnung
  im Log, welche alten Namen noch benutzt werden (nur die Namen, nie die Werte).
- **Der neue Name gewinnt.** Reihenfolge, vorne gewinnt: Umgebung (neu), Umgebung (alt),
  `.env` (neu), `.env` (alt). Die Umgebung schlägt also immer die `.env`.
- **Achtung bei eigenen Überschreibungen des Images:** Das Image legt jetzt
  `NODVARD_DECK_ENV`, `NODVARD_DECK_LOG_JSON` und `NODVARD_DECK_DATA_DIR` fest. Wer das mit
  `docker run -e LATTICE_DATA_DIR=…` (alter Name) überschreiben will, wird vom neuen
  Namen im Image überstimmt – dann bitte auf den neuen Namen umstellen.
- **Zurück aufs alte Image** (`nodvard-deck:previous`) geht ohne Änderung: Das alte Image bringt
  seine eigenen alten Werte mit und ignoriert die neuen Namen.

**Proxy für ausgehende Verbindungen** (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` im Container): Den nutzt nur die
Update-Suche bei ghcr.io. Erweiterungen (Proxmox, Nextcloud, ntfy usw.) und die Konsole einer VM oder eines Containers
verbinden sich immer direkt mit ihrem Ziel, damit Zugangsdaten und Konsolen-Ticket bei keiner anderen Stelle landen.
Kommt der Rechner nur über einen Proxy ins Internet, erreichen Erweiterungen externe Ziele wie `ntfy.sh` deshalb nicht;
Ziele, die der Rechner direkt erreicht (z. B. im eigenen Netz), gehen weiter.

## Backup / Restore

**Sicherungen in der Oberfläche** (Einstellungen → System → Sicherung): verschlüsselt (age),
ohne Anhalten des Dienstes, automatisch nach Zeitplan, mit Rotation und Download. Anleitung in
[docs/11-ERST-EINRICHTUNG.md §10.1](../docs/11-ERST-EINRICHTUNG.md#101-in-der-oberfläche-empfohlen).
Für einen Ordner außerhalb des Datenvolumes die vorbereitete Zeile `- ./sicherungen:/backups` in
der Compose-Datei (`compose.yml` bzw. `deploy/docker-compose.yml`) einkommentieren. Die Skripte
unten bleiben und funktionieren wie bisher.

**Wiederherstellen** geht ebenfalls in der Oberfläche (Einstellungen → System → Wiederherstellen) und im
Einrichtungsassistenten einer frischen Installation („Oder: Sicherung einspielen“, mit dem Einrichtungscode).
Die Datei wird hochgeladen, geprüft und erst nach einer Bestätigung **beim nächsten Start** eingespielt:
`entrypoint.sh` ruft dafür `python -m nodvard_deck.boot` auf (es spielt eine vorgemerkte Sicherung ein und
migriert danach wie bisher). Der Dienst beendet sich zum Neustart mit dem Rückgabewert 75 – dafür braucht der
Container eine Neustart-Regel (`restart: unless-stopped`, in den mitgelieferten Compose-Dateien gesetzt; ein
`docker run` ohne `--restart` bleibt danach aus). Der alte Stand liegt danach unter `/app/data/restore/replaced-…`.
**`restore.sh` kann eine `.ndbak`-Datei vormerken** (`./restore.sh sicherung.ndbak`): es prüft sie mit
`python -m nodvard_deck.admin restore-backup`, fragt das Passwort ab und startet den Dienst auf Wunsch neu; eingespielt
wird dann beim Start. Der Container bekommt dafür eine Kopie in einem eigenen Ordner unter `/tmp` (bzw. `$TMPDIR`), die
andere lokale Benutzer nicht lesen können; das Skript löscht sie am Ende wieder. So klappt es auch mit einer Datei, die
root gehört (dann mit `sudo`), oder in einem Ordner, den der Container-Benutzer 1000 nicht betreten darf. Wer das Skript
weder als root noch als UID 1000 startet, braucht `setfacl`, sonst bricht es ab und verlangt `sudo`. Liegt noch die
Marke eines unterbrochenen `.tar.gz`-Einspielens (siehe unten), merkt es nichts vor (Exit-Code 3). **Nach einem Rückweg (`pi_switch.sh rollback`) auf ein Image von vor der Paket-Umbenennung**
kennt das Image nur den alten Modulnamen; dann statt `restore.sh` direkt
`docker compose -p deploy run --rm --no-deps --user 1000:1000 --entrypoint python -v "$PWD:/backup:ro" nodvard-deck -m lattice.admin restore-backup /backup/<datei>.ndbak`
verwenden (`.tar.gz`-Sicherungen betrifft das nicht, die spielt `restore.sh` ohne Python ein). Das ist der Weg ohne Oberfläche. Hochladen größer als 4 GiB (`NODVARD_DECK_RESTORE_MAX_UPLOAD_BYTES`)
oder mehr als 200 000 Dateien (`…_MAX_ENTRIES`) wird abgelehnt; entpackt dürfen es höchstens 16 GiB
(`…_MAX_UNPACKED_BYTES`) und nie mehr als der freie Platz sein. Zusätzlich verlangt das Verschieben, dass der
Datenordner (Datenbank, Schlüssel, `ext`, `branding`, `runs`) auf **einem** Laufwerk liegt (`rename`).

Der Dienst heißt in Compose jetzt `nodvard-deck` (früher `lattice`). Wer **vor dem ersten
Deploy mit dem neuen Namen** noch sichern will, nimmt dafür das `backup.sh` des alten Stands
(beim Deploy wird `deploy/` auf dem Pi ersetzt) oder sichert direkt:
`docker run --rm --user 1000:1000 -v deploy_lattice_data:/data -v "$PWD":/backup --entrypoint tar <image> czf /backup/vorher.tar.gz -C /data .`

```bash
cd deploy
./backup.sh [zielverzeichnis]              # legt nodvard-deck-backup-<timestamp>.tar.gz an
./restore.sh /pfad/zu/nodvard-deck-backup-*.tar.gz   # auch ältere lattice-backup-*.tar.gz
```

Läuft noch (oder nach einem Rollback wieder) der alte Container `deploy-lattice-1`, hat er
dasselbe Volume offen: beide Skripte stoppen ihn dann ebenfalls und starten ihn am Ende wieder.

Beide stoppen den Dienst kurz für eine garantiert konsistente Kopie des gesamten
`/app/data`-Baums (SQLite inkl. WAL/SHM, `master.key`, `jwt_secret.key`,
`vault_keyring.json`, Extension-Daten, Script-Git-Repo) — für die in
D-02 ([docs/00-DECISIONS.md](../docs/00-DECISIONS.md))
beschriebene Last (eine Handvoll Nutzer, ein paar Dutzend Hosts) ist die kurze
Ausfallzeit (Sekunden) der einfachste Weg, keine inkonsistente Kopie zu ziehen.

**Rechte:** Ohne Zugriff auf Docker (nicht root, nicht in der Gruppe `docker`) beide mit `sudo` starten. Sonst brechen
sie ab, bevor sie etwas anhalten oder verändern: `backup.sh` mit „Docker antwortet nicht …“, `restore.sh` mit „Der
Datenordner liess sich nicht pruefen“. Mit `sudo` gestartet gehören die Sicherung und ein dabei neu angelegter
Zielordner dem Aufrufer (`SUDO_UID`), als root ohne sudo (etwa aus der root-Crontab) dem Besitzer des Zielordners,
wenn das nicht root ist. Eine `.tar.gz` liest `restore.sh` selbst und reicht sie über die Standardeingabe in den
Container; eine Datei, die root gehört, geht also mit `sudo`.

**`backup.sh`** prüft vor dem Anhalten, ob das Image `nodvard-deck:latest` da ist und ob sich in den Zielordner
schreiben lässt. Die Datei entsteht unter einem Zwischennamen (`<name>.partial.<Zufall>`, Rechte 0600) und bekommt
ihren Namen erst, wenn sie mindestens 1 KiB groß und ein lesbares tar.gz ist; sonst wird sie gelöscht.

**Sperre:** Beide Skripte (auch `restore.sh` mit `.ndbak`) halten bis zum Ende dieselbe Sperre (`flock` auf
`deploy/.backup-restore.lock`). Läuft schon eine Sicherung oder ein Einspielen (etwa eine Cron-Sicherung), bricht der
zweite Lauf mit Exit-Code 4 ab, ohne etwas zu verändern. Fehlt `flock` (Paket util-linux), läuft es mit einer Warnung
ohne Sperre.

**Einspielen einer `.tar.gz`** (ersetzt den kompletten Inhalt von `/app/data`, fragt vorher nach): `restore.sh` prüft
die Datei mit `tar tzf`, hält den Dienst an und entpackt sie nach `/app/data/.restore-neu`; die bisherigen Daten
bleiben dabei unberührt. Scheitert das (Platz, Lesefehler, Strg+C), startet der Dienst wieder auf dem alten Stand
(außer beim Fertigmachen eines abgebrochenen Austauschs, siehe unten: dann bleibt er gestoppt). Erst danach tauscht es
per Umbenennen innerhalb des Volumes aus (alte Daten nach `.restore-alt`, neue an ihre Stelle, `.restore-alt` weg);
kurz liegen die Daten doppelt im Volume. Die Marke `/app/data/.restore-austausch` hält fest, dass und wie weit der
Austausch läuft. Bricht er mittendrin ab, bleibt der Dienst gestoppt; derselbe Aufruf mit derselben Datei macht ihn an
der richtigen Stelle fertig und startet den Dienst. Solange die Marke liegt, lehnt `backup.sh` ab (Exit-Code 3); ist
sie leer oder beschädigt, brechen beide Skripte mit Exit-Code 5 ab, und die Meldung nennt den Weg von Hand. Liegt beim
Beginn eines neuen Austauschs noch ein `.restore-alt` von früher, wird es nicht gelöscht, sondern als
`.restore-alt-<Zeit>` beiseitegelegt. Arbeitsordner und Marke kommen in keine Sicherung. SIGHUP (SSH-Abbruch) hält
das Skript nicht an, den Austausch auch Strg+C nicht. Die Meldungen ab der Rückfrage stehen zusätzlich in
`deploy/restore.log` (anderer Ordner: `RESTORE_LOG_DIR`); das Protokoll und eine dabei neu angelegte Sperrdatei
gehören nach `sudo` dem Aufrufer.

**Backup ohne Stopp (fortgeschritten, nicht in `backup.sh` automatisiert):** SQLites
eigene Online-Backup-API erzeugt eine konsistente Momentaufnahme auch bei laufendem
Betrieb:

```bash
# Das Image hat keine sqlite3-Kommandozeile (python:3.12-slim), Pythons sqlite3-Modul reicht:
docker compose exec -u lattice nodvard-deck python -c "import sqlite3; s=sqlite3.connect('/app/data/lattice.db'); d=sqlite3.connect('/app/data/_snapshot.db'); s.backup(d); d.close(); s.close()"
docker compose cp nodvard-deck:/app/data/_snapshot.db ./lattice-db-snapshot.db
chmod 600 ./lattice-db-snapshot.db   # die Kopie enthält die ganze Datenbank, nur für dich lesbar
docker compose exec -u lattice nodvard-deck rm /app/data/_snapshot.db
```

Beide Skripte lassen den Hilfscontainer bewusst **nicht** durch `/entrypoint.sh` laufen
(`--entrypoint tar` bzw. `--entrypoint sh`): sonst würde vor dem Sichern oder Einspielen
die Datenbank-Migration laufen. Ein „Vorher-Backup“ nach einem neuen Build wäre dann
schon migriert, und `restore.sh` würde an genau der kaputten Datenbank scheitern, die es
ersetzen soll. Ein `trap` startet den Dienst auch bei einem Fehler wieder, außer solange ein Austausch der Daten durch
`restore.sh` nicht fertig ist (siehe oben).

Das sichert nur `lattice.db` selbst, nicht `master.key`/`jwt_secret.key`/
Extension-Daten/Script-Repo — für einen vollständigen Restore-Punkt weiterhin
`backup.sh` verwenden.

## Aktualisieren: Kopie vor der Migration, Notseite, Rückweg

**Gibt es eine neue Version?** Steht unter Einstellungen → System → „Updates“. Das Dashboard fragt einmal am Tag bei ghcr.io nach
(abschaltbar; ghcr.io gehört zu GitHub, das dabei die IP-Adresse und den Zeitpunkt sieht, sonst nichts, auch nicht die installierte Version), „Jetzt suchen“ geht
jederzeit. Dort stehen auch die Anleitungen für Docker Compose, Docker Desktop, Portainer, Synology, Unraid und `scripts/deploy_pi.sh`.
Mit dem [Update-Helfer](#update-helfer) geht das Einspielen dort per Knopf; ohne ihn wie folgt von Hand.
Mit dem fertigen Image (Datei `deploy/compose.standalone.yml`, bei der Installation als `compose.yml` abgelegt) reicht im Ordner der Datei:

```bash
docker compose pull
docker compose up -d
```

Heißt die Datei anders (z. B. noch `compose.standalone.yml`), `-f <Dateiname>` anhängen. Vorher die laufende Version notieren (steht
in der Karte), für den Rückweg unten. Vorabversionen (`0.7.0-rc1`, Kanal „Beta“) gibt es nicht unter `:latest`, nur unter ihrem genauen Tag.

Das Release-Image trägt die Datei `/app/image-info.json` mit Herkunft und genauer Version (`{"image": "ghcr.io/nodvard/deck",
"version": "<Version>"}`, aus den Build-Argumenten `IMAGE` und `VERSION`, geschrieben von `python -m nodvard_deck.image_info`). Ein selbst
gebautes Image (z. B. über `deploy_pi.sh`) hat sie nicht; die Oberfläche zeigt dann den Weg über `deploy_pi.sh`. Bewusst eine Datei und
keine Umgebungsvariable im Image: Die käme beim Neuanlegen eines Containers mit dessen alten Einstellungen (Portainer „Recreate“,
Update-Helfer) in den neuen Container mit und zeigte dort die alte Version. Eine gesetzte Variable `NODVARD_DECK_IMAGE` bzw.
`NODVARD_DECK_BUILD` geht der Datei vor (leer zählt als nicht gesetzt) – normalerweise braucht es sie nicht.

Beim Start läuft vor der Anwendung `python -m nodvard_deck.boot` (`entrypoint.sh`). Es macht, in dieser Reihenfolge:
vorgemerkte Wiederherstellung einspielen, **Kopie der Datenbank anlegen, wenn eine Migration ansteht**, migrieren.

- **Kopie vor jeder Migration** (SQLite): `/app/data/backups/vor-update/<Zeit>_<von>_<nach>.db` (+ `.json`), die **letzten drei**
  bleiben. Vorher muss mindestens das **1,2-fache der Datenbank** frei sein, sonst gibt es **keine Migration** (und die Notseite sagt, wie viel fehlt).
  **Ohne Kopie wird nie migriert.** Notausgang (nicht empfohlen): `NODVARD_DECK_SKIP_PRE_MIGRATE_BACKUP=1`
  (alter Name `LATTICE_SKIP_PRE_MIGRATE_BACKUP`). Nicht Teil der Kopie: Dateien außerhalb der Datenbank (Erweiterungsdaten, Schlüssel); Migrationen fassen
  sie nicht an. Bei einer anderen Datenbank als SQLite gibt es keine Kopie (bitte vorher selbst sichern).
  Die Oberfläche zeigt die letzten Kopien unter Einstellungen → System → „Kopien vor Updates“ (`GET /api/v1/system/info`).
- **Scheitert die Migration**, geht die Datenbank auf die Kopie zurück (die Daten sind danach wie vor dem Update), und statt der Anwendung läuft die **Notseite**.
  Der halbe Stand wird dabei verworfen (er enthält nichts, was nicht auch in der Kopie steht).
  **Bricht sie mittendrin ab** (Strom weg, `docker restart`), macht der nächste Start dasselbe und migriert dann neu; der halbe Stand bleibt dann 30 Tage unter
  `restore/replaced-…`. Bei der allerersten Migration einer neuen Installation (noch ohne Konto) wird der halbe Stand dort abgelegt und frisch begonnen.
- **Neuere Daten werden beim Zurücksetzen nie gelöscht:** Was der automatische Rückweg auf die alte Version oder „Stand vor dem Update wiederherstellen“ ersetzt, liegt
  30 Tage unter `restore/replaced-…`. Liegt die Datenbank auf einem eigenen Laufwerk (eigener `NODVARD_DECK_DATABASE_URL`), wird sie dafür in den Datenordner kopiert;
  fehlt dort der Platz, zeigt die Notseite „zu wenig Platz“, und es wurde nichts verändert. Nur den halben Stand einer abgebrochenen Migration verwirft der Start in
  diesem Fall, statt auf der Notseite hängen zu bleiben.
- **Wiederherstellung + abgebrochene Migration:** Bricht die Migration einer gerade eingespielten Sicherung ab, nimmt der nächste Start das Einspielen zurück; es gilt
  wieder genau der alte Stand (Datenbank, Schlüssel, Migrationsstand in `.boot/state.json`, `pre_restore`).
- **Notseite** (`python -m nodvard_deck.rescue`, nur Standardbibliothek, auf dem Port der Anwendung): `GET /api/v1/health` antwortet **503** `{"status":"rescue"}`
  (nie `ok`: der Docker-Healthcheck schlägt an, `scripts/deploy_pi.sh` schaltet zurück). Sie lauscht auf `--host`/`--port` aus dem Startbefehl (sonst `UVICORN_HOST`);
  ohne erkennbare Adresse nur auf `127.0.0.1`, wie uvicorn selbst (das Image setzt `--host 0.0.0.0`). Jede Verbindung endet nach 10 s, die Kopfzeilen müssen nach 3 s da sein (Anfragezeile und Kopfzeilen zusammen höchstens
  32 KiB, dabei unter 100 Kopfzeilen, sonst `431`). Je Rechner höchstens 8 Verbindungen gleichzeitig (bei IPv6 je /64-Netz), insgesamt 64; `127.0.0.1` und `::1`
  (Healthcheck im Container) haben 8 eigene Plätze.
  Ohne Code zeigt sie nur Allgemeines. Mit dem **Notfallcode**
  (steht als Banner im Protokoll des Containers: `sudo docker compose logs nodvard-deck | grep Notfallcode`, und in `.boot/rescue_code.txt` im Datenordner) zeigt sie Grund und
  bereinigtes Protokoll und bietet „Neu versuchen“ (Container endet mit 75, die Neustart-Regel startet ihn neu) und, wo es geht, „Stand vor dem Update
  wiederherstellen“. Die Eingabe des Codes ist gedrosselt (5 Fehlversuche je Rechner, insgesamt 25 in 10 Minuten);
  die Sperre weist nur falsche Codes ab, der richtige Code geht immer sofort durch. Fällt die Notseite selbst aus, **wartet der Container** (`sleep`) – er läuft nie in eine Neustart-Schleife;
  `docker stop` dauert dann die üblichen 10 Sekunden.
- **Zurück auf die alte Version** (nach einem Update, das nicht klappt): Image-Version in der Compose-Datei auf die vorherige zurückstellen und den Container neu starten
  (`deploy_pi.sh` und `pi_switch.sh rollback` tun das für das Image selbst).
  * Ist die neue Version **nie erfolgreich gestartet**, spielt die alte die Kopie **von selbst** wieder ein (die neueren Daten bleiben 30 Tage unter `restore/replaced-…`).
    Ob sie gestartet ist, hält die Anwendung beim Start fest (`started_ok`); klappt das nicht sofort (Speicher voll), versucht sie es jede Minute erneut.
  * Hat die neue Version schon gearbeitet, zeigt die alte die Notseite („Die Daten sind neuer als diese Version“). Dort: **Stand vor dem Update wiederherstellen**
    (Änderungen seit dem Update gehen verloren; die neueren Daten bleiben 30 Tage unter `restore/replaced-…`) oder wieder die neue Version eintragen.
  * **Der Rückweg funktioniert erst auf Versionen, die diese Funktion schon enthalten.** Eine ältere Version (bis 0.5.x) kann eine Kopie nicht selbst einspielen und kennt die Notseite nicht;
    die Vorversion muss also mindestens die Version mit „Kopie vor jeder Migration“ sein. Beim ersten Update **auf** eine solche Version legt diese die Kopie schon an; zurück geht es danach.
    Scheitert die Migration, steht die Datenbank allerdings sofort wieder auf dem alten Stand – dann läuft auch eine ältere Version weiter.
- **Zurück von 0.7 auf 0.6.x (Nodvard Shield heißt technisch seit 0.7 `shield` statt `nexus-soc`):** 0.7 bringt keine Migration, der Rückweg
  braucht also keine Kopie und keine Notseite. Was danach passiert, hängt davon ab, wie die Installation entstanden ist:
  * **Bestehende Installation** (schon vor 0.7 eingerichtet): Shield nutzt weiter die gespeicherte Zeile `nexus-soc`. Nach dem Rückweg ist alles
    da, Shield läuft. Nur Vorschläge, die unter 0.7 entstanden sind, zeigt das alte Image nicht auf den Shield-Seiten; unter „Aktionen“ stehen sie
    und lassen sich weiter freigeben. Ein erneutes Update auf 0.7 geht ohne Verlust.
  * **Neu mit 0.7 eingerichtet** (Zeile `shield`): Das alte Image kennt diese Kennung nicht. Unter 0.6.1 steht Shield als „Fehler“ (fehlt) da, dazu
    legt 0.6.1 eine ausgeschaltete Zeile `nexus-soc` ohne Einstellungen an; Shield ist im alten Image also aus. Nach dem erneuten Update auf 0.7 ist
    alles wieder da. 0.6.0 setzt die Zeile `shield` auf „Fehler“; nach dem erneuten Update auf 0.7 Shield dann einmal von Hand einschalten
    (Einstellungen → Erweiterungen). Was in der Zeit mit dem alten Image unter `nexus-soc` eingestellt wurde, kommt nicht mit.
  * **Sicherungen:** Eine Sicherung aus 0.5 oder 0.6.x lässt sich in 0.7 einspielen. Eine Sicherung aus einer mit 0.7 neu eingerichteten Installation
    lässt sich in 0.6.x einspielen, das alte Image warnt aber „Diese Erweiterungen aus der Sicherung gibt es hier nicht: shield …“ und zeigt die Einstellungen von
    Shield nicht; nach dem
    Update auf 0.7 sind sie wieder da.
- **Sperre:** Die laufende Anwendung hält `/app/data/.boot/app.lock`. Ein zweiter Container mit demselben Datenordner (z. B. `docker compose run nodvard-deck …` neben dem laufenden Dienst)
  ändert deshalb nichts: `boot` beendet sich mit Code 75. Für Wartungsbefehle lieber `docker compose exec`, oder `run --entrypoint …` bei gestoppter Anwendung (so machen es `backup.sh` und `restore.sh`).
- **Healthcheck:** `--start-period` ist **300 s** (Image und beide Compose-Dateien), damit eine lange Migration samt Kopie nicht als „unhealthy“ gilt.
  Ohne `user:` läuft der Check als root; deshalb ruft er `python -I` auf (isoliert: ohne den aktuellen Ordner im Suchpfad, ohne `PYTHON*`-Variablen und
  ohne Pakete aus dem Benutzerverzeichnis). Eine eigene Compose-Datei mit eigenem `healthcheck:` sollte das `-I` übernehmen.
- **Alter Stand einer Wiederherstellung oder eines Rückwegs** (`restore/replaced-…`, kann Konten und Schlüssel im Klartext enthalten): wird **nach 30 Tagen automatisch gelöscht**; die Oberfläche nennt den Tag und kann ihn früher löschen.

## Update-Helfer

Mit dem Update-Helfer spielst du Updates unter Einstellungen → System → „Updates“ per Knopf ein und kannst danach 7 Tage
lang einmal per Knopf zurück (Karte „Kopien vor Updates“). Der Helfer ist ein kleiner eigener Dienst `updater` in
derselben Compose-Datei wie Nodvard Deck. Er läuft nur, wenn du ihn **bewusst einschaltest**: mit
[`compose.standalone-mit-helfer.yml`](compose.standalone-mit-helfer.yml) statt `compose.standalone.yml`. Bedienung in der
Oberfläche: [docs/11-ERST-EINRICHTUNG.md §10.6](../docs/11-ERST-EINRICHTUNG.md#106-update-per-knopf-update-helfer).

> **Achtung:** Der Helfer bekommt Zugriff auf Docker (den Docker-Socket). Das ist so viel wie root auf diesem Rechner.
> Darum ist er streng gebaut: kein Netz, kein offener Port, nur lesbar, ohne Sonderrechte, mit Speicher- und
> Prozessgrenze. Vom Dashboard nimmt er nur Aufträge an (Aktion und Version), prüft alles selbst und tauscht nur den
> Dienst `nodvard-deck` im selben Compose-Projekt gegen eine neuere **offizielle** Version (`ghcr.io/nodvard/deck`).
> Willst du das nicht, bleib bei `compose.standalone.yml` und aktualisiere von Hand (siehe oben).

**Image:** `ghcr.io/nodvard/deck-updater:1`. Es entsteht bei jedem Versions-Tag im Job `updater-image` von
`.github/workflows/release.yml` (für `linux/amd64` und `linux/arm64`, Tag `:<version>`). Das Tag `1` bekommt eine Version
erst, wenn ihr Selbsttest auf beiden Plattformen bestanden ist, und nie eine Vorabversion; es gilt für alle Helfer mit
derselben Schnittstelle. Gebaut wird aus `deploy/updater/Dockerfile`, nur aus der Standardbibliothek von Python.

**Was er kann:**
- Update auf die neueste fertige Version, wenn Nodvard Deck mit dem offiziellen Image und beweglichem Tag läuft
  (`ghcr.io/nodvard/deck:latest` oder eine Reihe wie `:0.7`). Die Docker-Engine lädt die neue Version selbst, der Helfer
  hat kein Netz.
- Startet die neue Version nicht (beendet sich, startet immer wieder neu, zeigt die Notseite oder ist nach 15 Minuten
  nicht bereit), schaltet er von selbst auf die alte Version zurück.
- Rückweg per Knopf auf die Version davor: einmal, 7 Tage lang. Hat die neue Version die Datenbank umgebaut, gehen die
  Daten mit zurück (die Oberfläche sagt das vorher und verlangt eine Bestätigung).
- Nach einem Absturz oder Neustart des Rechners macht er einen angefangenen Vorgang zu Ende oder baut ihn zurück.
- Jedes Ergebnis steht genau einmal im Protokoll, wichtige kommen zusätzlich als Meldung.

**Was er (noch) nicht kann:**
- Nur Installationen mit Docker Compose (Kommandozeile, Docker Desktop, Portainer-Stack, Synology-Projekt, Unraid mit
  Compose-Plugin). Einzeln über eine Oberfläche angelegte Container gehen nicht.
- Kein Update bei fester Version in der Compose-Datei (`:0.7.0`, `@sha256:…`), bei Vorabversionen und bei einem selbst
  gebauten Image (etwa über `scripts/deploy_pi.sh`); die Karte sagt dann, warum der Knopf fehlt.
- Ziele erst ab Version 0.7.0: Das erste Update per Knopf geht von 0.7.0 auf die Version danach.
- Sich selbst aktualisieren: Das machst du von Hand (siehe unten).
- Die Aufträge des Dashboards sind noch nicht signiert; der Helfer prüft sie gegen seine eigenen Regeln und gegen das,
  was er bei Docker vorfindet.

**Voraussetzungen**
- Nodvard Deck ab 0.7.0, installiert wie unter „Installation ohne das Repo“.
- Der Dienst heißt `nodvard-deck`. Umbenannt? Dann beim Helfer `NODVARD_DECK_UPDATER_SERVICE` auf den neuen Namen setzen
  (Zeile in der Compose-Datei einkommentieren).

**Einrichten – Kommandozeile (Linux, Docker Desktop)**
1. In den Ordner mit deiner `compose.yml` wechseln und sie sichern: `cp compose.yml compose.yml.alt`. Eigene Änderungen
   merken: Port, Sicherungsordner, DNS und vor allem einen Ordner statt des Datenspeichers (z. B. `./daten:/app/data`).
   Fehlt der in der neuen Datei, startet Nodvard Deck mit leeren Daten (deine Daten bleiben im Ordner liegen).
2. Datei mit Helfer laden (ersetzt die alte; Projektname und Datenspeicher bleiben gleich), eigene Änderungen übernehmen
   und starten:

   ```bash
   curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone-mit-helfer.yml
   docker compose up -d
   ```

3. Unter Einstellungen → System → „Updates“ steht nach kurzer Zeit „Update-Helfer: bereit“. Bei „nicht bereit“ nennt die
   Karte den Grund und was hilft.

Rootless Docker: neben die `compose.yml` eine Datei `.env` mit `NODVARD_DECK_DOCKER_SOCKET=/run/user/1000/docker.sock`
legen (`echo $XDG_RUNTIME_DIR/docker.sock` zeigt deinen Pfad).

**Einrichten – Portainer und Synology:** den Inhalt deines Stacks bzw. Projekts durch `compose.standalone-mit-helfer.yml`
ersetzen, eigene Änderungen übernehmen (vor allem einen Ordner statt des Datenspeichers) und neu starten (Portainer:
„Update the stack“). Der Helfer muss im **selben** Stack bzw. Projekt stehen, nicht als eigener. Anderer Socket: in
Portainer unter „Environment variables“ `NODVARD_DECK_DOCKER_SOCKET` setzen.

**Selbsttest:** Ob das Image vollständig und lauffähig ist, prüft es ohne Docker-Zugang, Kanal und Zustand:

```bash
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  ghcr.io/nodvard/deck-updater:1 --selftest     # Ausgabe: eine Zeile mit "ok":true
```

Aus dem Quellcode geht dasselbe im Ordner `deploy/updater` mit `python -m nodvard_deck_updater --selftest` (im Image läuft
es als `python -I -m nodvard_deck_updater --selftest`).

**Helfer aktualisieren** (nicht, während er gerade ein Update macht): `docker compose pull updater && docker compose up -d updater`
(Synology: per SSH im Projektordner, mit `sudo` davor). Willst du genau festlegen, welcher Helfer läuft, häng den Digest
an: `image: ghcr.io/nodvard/deck-updater:1@sha256:…` (Wert: `docker buildx imagetools inspect ghcr.io/nodvard/deck-updater:1`,
Zeile „Digest“). Dann wechselt er nur, wenn du den Wert änderst. Die Service-Matrix bietet für das Helfer-Image kein
„Einspielen“ an.

**Helfer ausschalten oder entfernen**
- Vorübergehend: `docker compose stop updater`. Die Karte zeigt dann „antwortet nicht“ und die Anleitung für Updates von
  Hand. Beim nächsten `docker compose up -d` läuft er wieder.
- Entfernen: wieder `compose.standalone.yml` als `compose.yml` nehmen (eigene Änderungen übernehmen), dann
  `docker compose up -d --remove-orphans`. Danach dürfen die Volumes des Helfers weg:
  `docker volume rm nodvard-deck-updater nodvard-deck-updater-state`. Der Rückweg per Knopf ist damit weg, deine Daten
  (`nodvard-deck-data`) bleiben.

**Startet Nodvard Deck nach einem Update gar nicht mehr** (und der Helfer hat nicht von selbst zurückgeschaltet): in der
Compose-Datei die Vorversion eintragen (z. B. `ghcr.io/nodvard/deck:0.7.0`), `docker compose up -d`, dann auf der Notseite
„Stand vor dem Update wiederherstellen“ (siehe oben). Läuft alles wieder: zurück auf `ghcr.io/nodvard/deck:latest` und
`docker compose up -d`. Solange die feste Version eingetragen ist, bleibt der Knopf aus.

**Hinweis für die Veröffentlichung:** Die Pakete `deck` und `deck-updater` der Organisation `nodvard` in der
GitHub-Container-Registry (`ghcr.io/nodvard/deck`, `ghcr.io/nodvard/deck-updater`) müssen auf GitHub einmal auf
**Public** gestellt werden (Paket → „Package settings“ → „Change visibility“). Neue Pakete sind dort oft erst privat: Dann
kann ein fremder Rechner die Images nicht ziehen, und die Update-Suche bekommt kein anonymes Token. Nach dem ersten
Release mit dem Helfer also bei `deck-updater` nachsehen.

## Demo-Modus

`NODVARD_DECK_DEMO_MODE=1` (alt: `LATTICE_DEMO_MODE=1`) legt beim Start dieselben Beispieldaten an wie der
Knopf „Mit Beispieldaten ansehen“ im Cockpit (D-10): fünf Beispiel-Server aus dem Dokumentationsbereich
`192.0.2.0/24` (ohne Zugang, es wird nie etwas im Netz angefragt) und vier Meldungen. Das passiert nur,
solange es noch keinen echten Server gibt, und nur einmal. In der Seitenleiste steht „Demo-Modus“, oben
ein Band mit „Beispieldaten löschen“. Wer das Image nur zum Ansehen braucht, setzt das Flag; alle anderen
brauchen es nicht – den Knopf gibt es auf jeder frischen Installation. Siehe
[docs/11-ERST-EINRICHTUNG.md](../docs/11-ERST-EINRICHTUNG.md#5-server-und-ssh-zugang).

## Hardware

Nodvard Deck läuft auf einem Raspberry Pi 4 oder 5 mit 4 GB RAM (64-Bit-System, `linux/arm64`)
und auf jedem x86-64-Rechner mit Docker. Im Leerlauf braucht der Container rund 100 MB
Arbeitsspeicher und kaum CPU; das Image ist knapp 400 MB groß. Auf einem Pi bleibt damit genug
Luft für weitere Dienste. Nur das Bauen des Images ist für einen Pi zu schwer, dafür siehe
„Deploy auf einen eigenen Pi per SSH“.
