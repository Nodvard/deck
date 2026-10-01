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

## Starten aus dem Repo

```bash
cd deploy
docker compose up -d
```

Danach `http://<host>:8080/` öffnen. Eine frische Installation (kein Nutzer in der
Datenbank) zeigt automatisch den **Einrichtungsassistenten** (`/setup` statt `/login`,
solange `GET /api/v1/auth/bootstrap` `{"needed": true}` liefert): Administrator-Konto,
Zeitzone, Auswahl der Module, optional Zwei-Faktor-Anmeldung und Aussehen (Produktname,
Kurzname, Akzentfarbe). Logo, alle fünf Farben und Support-Link stellt man danach unter
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
   die Daten weg). Dann die Logs der letzten 5 Minuten auf `Traceback` prüfen. Scheitert diese Log-Prüfung selbst
   (Verbindung weg), ist der Zustand unklar: `DEPLOY-FAIL`, ebenfalls ohne automatischen Rollback.
7. Scheitert einer der Schritte, wird **automatisch zurückgeschaltet** (`pi_switch.sh rollback`):
   Beim ersten Übergang wird der neue Container entfernt und der alte mit `docker start`
   wieder gestartet (ohne Compose), aber nur, wenn sein Image dasselbe ist wie
   `nodvard-deck:previous`; sonst, und bei späteren Deploys, läuft `nodvard-deck:previous` per Compose.
   Danach wartet das Skript, bis `/api/v1/health` wieder antwortet. Klappt das nicht, steht
   **`ROLLBACK-FAIL`** in der Ausgabe (Dienst vermutlich aus), sonst `DEPLOY-FAIL: Rollback
   ausgeführt`. Gibt es nichts zum Zurückschalten (frische Maschine), steht das ehrlich dort.
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
git archive -o /tmp/nodvard-deck.tar HEAD && scp /tmp/nodvard-deck.tar "$PI_HOST":~/nodvard-deck.tar
ssh "$PI_HOST" 'tar -xf ~/nodvard-deck.tar -C ~/lattice-deploy-test && cd ~/lattice-deploy-test/deploy \
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
`NODVARD_DECK_DATA_DIR` und `NODVARD_DECK_DNS_1`/`_2` (nur Compose).

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
wird dann beim Start. **Nach einem Rückweg (`pi_switch.sh rollback`) auf ein Image von vor der Paket-Umbenennung**
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

**Backup ohne Stopp (fortgeschritten, nicht in `backup.sh` automatisiert):** SQLites
eigene Online-Backup-API erzeugt eine konsistente Momentaufnahme auch bei laufendem
Betrieb:

```bash
# Das Image hat keine sqlite3-Kommandozeile (python:3.12-slim), Pythons sqlite3-Modul reicht:
docker compose exec -u lattice nodvard-deck python -c "import sqlite3; s=sqlite3.connect('/app/data/lattice.db'); d=sqlite3.connect('/app/data/_snapshot.db'); s.backup(d); d.close(); s.close()"
docker compose cp nodvard-deck:/app/data/_snapshot.db ./lattice-db-snapshot.db
docker compose exec -u lattice nodvard-deck rm /app/data/_snapshot.db
```

Beide Skripte lassen den Hilfscontainer bewusst **nicht** durch `/entrypoint.sh` laufen
(`--entrypoint tar` bzw. `--entrypoint sh`): sonst würde vor dem Sichern oder Einspielen
die Datenbank-Migration laufen. Ein „Vorher-Backup“ nach einem neuen Build wäre dann
schon migriert, und `restore.sh` würde an genau der kaputten Datenbank scheitern, die es
ersetzen soll. Ein `trap` startet den Dienst auch bei einem Fehler wieder.

Das sichert nur `lattice.db` selbst, nicht `master.key`/`jwt_secret.key`/
Extension-Daten/Script-Repo — für einen vollständigen Restore-Punkt weiterhin
`backup.sh` verwenden.

## Aktualisieren: Kopie vor der Migration, Notseite, Rückweg

**Gibt es eine neue Version?** Steht unter Einstellungen → System → „Updates“. Das Dashboard fragt einmal am Tag bei ghcr.io nach
(abschaltbar; ghcr.io gehört zu GitHub, das dabei die IP-Adresse und den Zeitpunkt sieht, sonst nichts, auch nicht die installierte Version), „Jetzt suchen“ geht
jederzeit. Dort stehen auch die Anleitungen für Docker Compose, Docker Desktop, Portainer, Synology, Unraid und `scripts/deploy_pi.sh`.
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
  ohne erkennbare Adresse nur auf `127.0.0.1`, wie uvicorn selbst (das Image setzt `--host 0.0.0.0`). Jede Verbindung endet nach 10 s, je Rechner höchstens 8 gleichzeitig.
  Ohne Code zeigt sie nur Allgemeines. Mit dem **Notfallcode**
  (steht als Banner im Protokoll des Containers: `sudo docker compose logs nodvard-deck | grep Notfallcode`, und in `.boot/rescue_code.txt` im Datenordner) zeigt sie Grund und
  bereinigtes Protokoll und bietet „Neu versuchen“ (Container endet mit 75, die Neustart-Regel startet ihn neu) und, wo es geht, „Stand vor dem Update
  wiederherstellen“. Die Eingabe des Codes ist gedrosselt. Fällt die Notseite selbst aus, **wartet der Container** (`sleep`) – er läuft nie in eine Neustart-Schleife;
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
- **Sperre:** Die laufende Anwendung hält `/app/data/.boot/app.lock`. Ein zweiter Container mit demselben Datenordner (z. B. `docker compose run nodvard-deck …` neben dem laufenden Dienst)
  ändert deshalb nichts: `boot` beendet sich mit Code 75. Für Wartungsbefehle lieber `docker compose exec`, oder `run --entrypoint …` bei gestoppter Anwendung (so machen es `backup.sh` und `restore.sh`).
- **Healthcheck:** `--start-period` ist **300 s** (Image und beide Compose-Dateien), damit eine lange Migration samt Kopie nicht als „unhealthy“ gilt.
- **Alter Stand einer Wiederherstellung oder eines Rückwegs** (`restore/replaced-…`, kann Konten und Schlüssel im Klartext enthalten): wird **nach 30 Tagen automatisch gelöscht**; die Oberfläche nennt den Tag und kann ihn früher löschen.

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
