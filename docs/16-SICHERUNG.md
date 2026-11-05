# Sicherung im Detail

> English version: [en/BACKUP.md](en/BACKUP.md)

Wie du die Sicherung in der Oberfläche einrichtest und wieder einspielst, steht in der
[Erst-Einrichtung, Abschnitt 6](11-ERST-EINRICHTUNG.md#6-das-dashboard-selbst-sichern). Hier stehen die Einzelheiten:
Sicherungen auf ein NAS oder NFS, eine Sicherung ohne Nodvard Deck öffnen, was nicht in der Sicherung steckt, der letzte
Ausweg und die Skripte für Installationen aus dem Repository.

## Sicherungen auf ein NAS oder NFS

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

## Notfall: eine Sicherung ohne Nodvard Deck öffnen

Eine `.ndbak`-Datei ist eine normale [age](https://age-encryption.org)-Datei
mit zwei Zeilen davor.

```bash
tail -n +3 nodvard-deck-sicherung-20261001-023000.ndbak > sicherung.age
age -d -i wiederherstellungsschluessel.txt -o sicherung.tar.gz sicherung.age   # oder: age -d (Einmal-Passwort)
tar -xzf sicherung.tar.gz    # db/lattice.db, files/master.key, files/ext/ …
```

Einspielen: siehe [Erst-Einrichtung, 6.2](11-ERST-EINRICHTUNG.md#62-wiederherstellen-sicherung-einspielen).

`lattice.db` ist der Name der Datenbank aus der Zeit vor der Umbenennung; er bleibt
([Alte Namen](11-ERST-EINRICHTUNG.md#m-alte-namen-bei-älteren-installationen)).

## Was nicht in der Sicherung steckt

- Mit der Umgebungsvariable `NODVARD_DECK_JWT_SECRET` liegt kein Anmelde-Geheimnis in der Sicherung.
- Der Verlauf der Messwerte (`metrics.db`) gehört nicht zur Sicherung und bleibt, wie er ist.
- **Git-Einstellungen** in den Daten der Erweiterungen (etwa bei „Skripte“) gehören ebenfalls nicht zur Sicherung, der
  Verlauf deiner Skripte schon; die Einstellungen legt die Erweiterung selbst neu an. Enthält eine ältere Sicherung noch
  welche, werden sie beim Einspielen übersprungen, und die Zusammenfassung nennt ihre Zahl unter den Warnungen.

## Wenn gar nichts mehr geht

Geht gar nichts mehr, liegt der alte Stand unter `restore/replaced-…`: Container anhalten, dessen Inhalt
(`lattice.db`, `master.key`, `ext/` …) zurück in den Datenordner kopieren, starten.

## Mit dem Skript (nur Installation aus dem Repository)

Die Skripte `deploy/backup.sh` und `deploy/restore.sh` gehören zur Installation aus dem Repository
([Erst-Einrichtung, Anhang A.2](11-ERST-EINRICHTUNG.md#a2-aus-dem-repository-bauen-für-entwickler)): Sie rufen Compose mit dem Projektnamen von
`deploy/docker-compose.yml` auf und funktionieren mit der `compose.yml` aus
[Erst-Einrichtung, Abschnitt 1](11-ERST-EINRICHTUNG.md#1-schnellstart) nicht. Dort sicherst du über die Oberfläche ([Erst-Einrichtung, 6.1](11-ERST-EINRICHTUNG.md#61-in-der-oberfläche-empfohlen)).

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
