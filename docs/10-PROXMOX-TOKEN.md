# Proxmox-API-Token für Nodvard Deck – mit möglichst wenig Rechten

Stand 29.09.2026. Die Rechte hier sind aus den Proxmox-Aufrufen im Nodvard-Deck-Code
abgeleitet (`extensions/proxmox`, `extensions/backups` – sonst spricht keine Erweiterung
mit Proxmox) und mit den Rechte-Angaben der Proxmox-API abgeglichen (Proxmox VE 9, Gegenprobe
VE 8). Besonderheiten von Proxmox VE 8 und älteren Versionen stehen unter
[Hinweise](#hinweise) ganz unten.

## Kurz vorweg

- Nodvard Deck redet mit Proxmox nur über die Web-API (Port 8006) und meldet sich mit einem
  **API-Token** an, nie mit Passwort.
- Zwei Erweiterungen brauchen den Token: **Proxmox VE** (Server, VMs, Container) und
  **Backups** (Backup-Jobs und Sicherungen). Jede hat ihre eigene Server-Liste und ihr
  eigenes Token-Feld. Du kannst in beiden denselben Token eintragen.
- Die Beispiele gehen von zwei getrennten Proxmox-Servern ohne Cluster aus: `pve1`
  (192.168.1.20) und `pve2` (192.168.1.21). Benutzer und Token gibt es dann **je Server**:
  Die Befehle unten auf **jedem** ausführen. Der Token heißt überall gleich
  (`nodvard@pve!dashboard`), das Geheimnis ist aber je Server ein anderes. In einem Cluster
  gelten Benutzer, Rollen und Token für alle Knoten, dort reicht es einmal.
- Nimm **nicht** `root@pam`. Ein eigener Benutzer `nodvard@pve` mit eigener Rolle darf nur,
  was Nodvard Deck braucht, und lässt sich jederzeit sperren oder löschen.

## Welche Variante?

| Funktion in Nodvard Deck | A: nur ansehen | B: alles |
|---|---|---|
| Knoten, VMs und Container einlesen, Status, Auslastung, Verlauf | ja | ja |
| Hardware und Snapshots ansehen, Datenträger/SMART, Aufgabenverlauf, Speicherbelegung | ja | ja |
| Echte IP-Adressen der VMs (über den Gast-Agenten) | ja | ja |
| Backup-Jobs, letzter Lauf, Liste „nicht gesichert“ | ja | ja |
| Kachel „Proxmox-Updates“ (wartende Pakete) | nein ¹ | ja |
| Speicher-Übersicht: welche Gast-Disks auf welchem Speicher liegen | nein ² | ja |
| Backups: vorhandene Sicherungen je Gast | nur mit A+ ³ | ja |
| Starten, Hart ausschalten, Neustarten | nein | ja |
| Snapshot anlegen, löschen, zurückrollen | nein | ja |
| Hardware ändern (Kerne, RAM, Autostart, Startreihenfolge) | nein | ja |
| Konsole | nein | ja |
| Paketlisten aktualisieren, Knoten neu starten | nein | ja |
| Backup jetzt starten / erneut versuchen | nein | ja, mit Zusatzrolle für den Backup-Speicher |
| Backup-Jobs anlegen und ändern | nein | ja, mit Zusatzrolle für den Backup-Speicher |

¹ Proxmox gibt die Liste wartender Updates nur mit `Sys.Modify` heraus – obwohl Nodvard Deck
sie nur liest. Die Kachel zeigt dann „Nicht abrufbar: … HTTP 403“.
² Gast-Disks im Speicherinhalt zeigt Proxmox nur mit `VM.Config.Disk`.
³ Vorhandene Sicherungen zeigt Proxmox nur, wer den Gast sichern darf (`VM.Backup`) und auf
dem Speicher Platz belegen darf (`Datastore.AllocateSpace`). Mit reinen Leserechten bleibt
die Liste leer.

## So läuft das mit den Rechten (einmal lesen, dann klappt es)

- **Rolle** = Liste von Rechten. **Berechtigung** = „dieser Benutzer/Token bekommt diese
  Rolle auf diesem Pfad“. Pfad `/` mit „Vererben“ gilt für alles darunter.
- **Privilegientrennung** (`--privsep 1`, der Standard): Der Token darf nur, was **Benutzer
  und Token beide** haben. Deshalb bekommen unten immer beide dieselbe Berechtigung. Fehlt
  die Zeile für den Benutzer, darf der Token gar nichts (HTTP 403).
- Eine Berechtigung auf einem tieferen Pfad (z. B. `/storage/local`) **ersetzt** dort die
  von `/` geerbten Rechte, sie ergänzt sie nicht. Das ist bei Variante B wichtig.

Alle Befehle laufen auf dem Proxmox-Server als root: in der Proxmox-Oberfläche links den
Knoten anklicken → „Shell“, oder per `ssh root@192.168.1.20` (zweiter Server: `192.168.1.21`).

## Variante A: nur ansehen

```bash
pveum role add NodvardRO --privs "Sys.Audit VM.Audit VM.GuestAgent.Audit Datastore.Audit"
pveum user add nodvard@pve --comment "Nodvard Deck (nur API-Token)"
pveum user token add nodvard@pve dashboard --privsep 1 --comment "Nodvard Deck"
pveum acl modify / --users nodvard@pve --roles NodvardRO
pveum acl modify / --tokens 'nodvard@pve!dashboard' --roles NodvardRO
```

`pveum user token add` zeigt eine Tabelle mit `full-tokenid` (`nodvard@pve!dashboard`) und
`value` – **`value` ist das Geheimnis und wird nur dieses eine Mal angezeigt.** Gleich
kopieren. Ein Passwort braucht `nodvard@pve` nicht; ohne Passwort kann sich damit auch
niemand in der Proxmox-Oberfläche anmelden.

**A+ – zusätzlich die Liste vorhandener Sicherungen.** Achtung: Mit diesen zwei Rechten
kann der Token über die Proxmox-API auch Backups anstoßen und vorhandene Sicherungen der Gäste
**löschen**. Das gilt genauso für Variante B. Nodvard Deck selbst löscht keine Sicherung direkt.
Startest du in Nodvard Deck ein Backup („Backup jetzt starten / erneut versuchen“, nur Variante B), kann
Proxmox danach nach der Aufbewahrung des Jobs aufräumen.

```bash
pveum role modify NodvardRO --append 1 --privs "VM.Backup Datastore.AllocateSpace"
```

## Variante B: alles, was Nodvard Deck kann

```bash
pveum role add NodvardFull --privs "Sys.Audit Sys.Modify Sys.PowerMgmt VM.Audit VM.GuestAgent.Audit VM.PowerMgmt VM.Console VM.Snapshot VM.Config.CPU VM.Config.Memory VM.Config.Options VM.Config.Disk VM.Backup Datastore.Audit Datastore.AllocateSpace"
pveum user add nodvard@pve --comment "Nodvard Deck (nur API-Token)"
pveum user token add nodvard@pve dashboard --privsep 1 --comment "Nodvard Deck"
pveum acl modify / --users nodvard@pve --roles NodvardFull
pveum acl modify / --tokens 'nodvard@pve!dashboard' --roles NodvardFull
```

**Backup-Jobs anlegen und ändern** verlangt in Proxmox zusätzlich `Datastore.Allocate` auf
dem Backup-Speicher des Jobs (beim Ändern auch auf dem bisherigen Speicher). Dasselbe gilt für
**Backup jetzt starten / erneut versuchen**: Nodvard Deck schickt dabei die Einstellungen und die
Aufbewahrung des Jobs mit. Hat der Job eine Aufbewahrung, kann Proxmox danach aufräumen und ältere
Sicherungen des Gastes auf dem Speicher löschen, auch manuelle und die anderer Jobs; die Rückfrage
vor dem Start sagt das. Ohne eigene Aufbewahrung geht `keep-all=1` mit, dann bleibt alles erhalten.
`Datastore.Allocate` darf auf dem Speicher auch Dateien löschen – deshalb nur dort, nicht auf `/`. Und weil der
tiefere Pfad die geerbten Rechte ersetzt, stecken Audit und AllocateSpace mit in der Rolle:

```bash
pvesm status                      # zeigt die Namen deiner Speicher
pveum role add NodvardBackupStore --privs "Datastore.Audit Datastore.AllocateSpace Datastore.Allocate"
pveum acl modify /storage/SPEICHERNAME --users nodvard@pve --roles NodvardBackupStore
pveum acl modify /storage/SPEICHERNAME --tokens 'nodvard@pve!dashboard' --roles NodvardBackupStore
```

`SPEICHERNAME` durch den Speicher ersetzen, auf den deine Backup-Jobs schreiben; bei
mehreren Backup-Speichern die zwei `acl`-Zeilen je Speicher wiederholen.

### Was du bei B weglassen kannst

| Recht | Wofür Nodvard Deck es braucht | Fehlt es … |
|---|---|---|
| `Sys.PowerMgmt` | „Knoten neu starten“ | scheitert nur dieser Knopf |
| `Sys.Modify` (auf `/`) | Kachel „Proxmox-Updates“, „Paketlisten aktualisieren“, Backup-Jobs anlegen/ändern, Backup jetzt starten / erneut versuchen bei Jobs mit Bandbreitengrenze oder ionice, bei VMs die Startreihenfolge | Updates-Kachel zeigt HTTP 403, diese Aktionen scheitern |
| `VM.Console` | Konsole | keine Konsole |
| `VM.Config.Disk` | Gast-Disks in der Speicher-Übersicht (Nodvard Deck liest nur – Proxmox erlaubt mit dem Recht aber auch Disks vergrößern/verschieben) | diese Liste fehlt |
| `VM.GuestAgent.Audit` | echte IP-Adressen der VMs | Nodvard Deck trägt die Adresse des Proxmox-Servers als Platzhalter ein; die richtige Adresse dann von Hand setzen ([docs/11](11-ERST-EINRICHTUNG.md#5-server-und-ssh-zugang)) |

Wer Sys.Modify oder die Rechte zum Ändern später nachrüsten will: Rolle ändern reicht,
Token und Nodvard-Deck-Einstellungen bleiben, z. B.
`pveum role modify NodvardRO --append 1 --privs "VM.PowerMgmt"`.

## Dasselbe in der Proxmox-Oberfläche

Rechenzentrum → Berechtigungen (die deutschen Namen können je nach Version leicht abweichen,
in Klammern der englische):

1. **Rollen** (Roles) → „Erstellen“: Name `NodvardRO` bzw. `NodvardFull`, Rechte wie oben
   anhaken.
2. **Benutzer** (Users) → „Hinzufügen“: Benutzername `nodvard`, Realm „Proxmox VE
   authentication server“.
3. **API-Token** (API Tokens) → „Hinzufügen“: Benutzer `nodvard@pve`, Token-ID `dashboard`,
   Haken bei „Privilegientrennung“ (Privilege Separation) **gesetzt lassen**. Das angezeigte
   Geheimnis sofort kopieren.
4. **Berechtigungen** (Permissions) → „Hinzufügen“ → „Benutzer-Berechtigung“: Pfad `/`,
   Benutzer `nodvard@pve`, Rolle wie oben, „Vererben“ (Propagate) an. Dann noch einmal
   „Hinzufügen“ → „API-Token-Berechtigung“: Pfad `/`, Token `nodvard@pve!dashboard`, gleiche
   Rolle.
5. Nur bei Variante B, für Backup-Jobs und „Backup jetzt starten / erneut versuchen“: dasselbe
   mit Pfad `/storage/SPEICHERNAME` und Rolle `NodvardBackupStore`, wieder für Benutzer **und** Token.

## Prüfen, bevor du es in Nodvard Deck einträgst

Auf dem Proxmox-Server – zeigt, was der Token wirklich darf:

```bash
pveum user token permissions nodvard@pve dashboard
```

Vom Rechner mit Nodvard Deck aus (prüft Netz, Token und Rechte in einem; `KNOTENNAME` ist der Name, der in
der Proxmox-Oberfläche links unter „Rechenzentrum“ steht):

```bash
curl -sk -H 'Authorization: PVEAPIToken=nodvard@pve!dashboard=HIER-DAS-GEHEIMNIS' \
  https://192.168.1.20:8006/api2/json/nodes/KNOTENNAME/status
```

Die **einfachen** Anführungszeichen lassen – sonst stolpert die Bash über das `!`.
Antwort mit `{"data":{...}}` = alles gut. `401` = Token-ID oder Geheimnis falsch.
`403 … Permission check failed` = Berechtigung fehlt (meist die Zeile für den Benutzer).

## In Nodvard Deck eintragen

**Einstellungen → Erweiterungen → „Proxmox VE“** einschalten (Schalter rechts), dann
„Konfigurieren“:

1. Unter „Proxmox-Server“ auf **„Server hinzufügen“** und ausfüllen:
   - **Kurzname:** `pve1` (bzw. `pve2`). Nach dem ersten Einlesen nicht mehr ändern – der
     Kurzname steckt in den Server-Namen (z. B. `proxmox-pve1-vm-100`).
   - **Adresse:** `https://192.168.1.20:8006` (pve2: `https://192.168.1.21:8006`)
   - **API-Token-ID:** `nodvard@pve!dashboard`
   - **Selbstsigniertes Zertifikat erlauben:** siehe nächster Abschnitt.
2. Oben rechts **„Speichern“**. Erst danach erscheint unten unter „Zugangsdaten“ die Zeile
   **„API-Token-Geheimnis – pve1“**.
3. Dort das Geheimnis einfügen → „Speichern“. Das Schild wechselt von „Fehlt“ auf
   „Hinterlegt“.
4. Dasselbe für `pve2`.

Danach **Einstellungen → Erweiterungen → „Backups“** einschalten, „Konfigurieren“ und unter
„Proxmox-Server für Backups“ dieselben Einträge anlegen – **mit denselben Kurznamen** wie
bei Proxmox VE. Sonst findet die Backups-Seite die Gast-Namen nicht und zeigt nur VMIDs.

Der Proxmox-Abgleich läuft alle 5 Minuten. Spätestens dann stehen Knoten, VMs und
Container auf der Seite „Proxmox“ und in der Übersicht.

Gut zu wissen:

- **Token austauschen:** in der Zeile „API-Token-Geheimnis – …“ auf „Ersetzen“, neuen Wert eintragen und wieder „Ersetzen“. Auf der
  Proxmox- bzw. Backups-Seite (unter „Verbindungen verwalten“) heißt der Knopf „Token ersetzen“, wenn
  schon ein Token da ist, und „Token setzen“, wenn es fehlt.
- **Adresse ändern oder Verbindung entfernen:** Dabei löscht Nodvard Deck das Token-Geheimnis
  dieser Verbindung (auch beim Entfernen unter „Verbindungen verwalten“); nach einer neuen Adresse
  trägst du es neu ein. Dasselbe gilt für einen neuen Kurznamen. Eine andere Schreibweise derselben
  Adresse (Groß-/Kleinschreibung im Rechnernamen, `:443` bei `https://`, `/` am Ende) zählt nicht
  als Änderung.
- **Keine Weiterleitungen:** Nodvard Deck folgt bei Aufrufen an Proxmox keinen Weiterleitungen, auch nicht
  beim Aufbau der Konsole. Trag deshalb die endgültige Adresse des Knotens ein (`https://…:8006`), keinen
  Reverse-Proxy, der weiterleitet. Sonst scheitern Abfragen und Konsole mit einer Fehlermeldung.
- **Einen Server vorübergehend abschalten**, ohne das Token zu verlieren: Proxmox- bzw.
  Backups-Seite → „Verbindungen verwalten“ → Knopf „aktiv“ anklicken (wird zu
  „deaktiviert“).

## Zertifikat: was „Selbstsigniertes Zertifikat erlauben“ macht

- Proxmox bringt ein selbst ausgestelltes Zertifikat mit. Nodvard Deck prüft Zertifikate
  normalerweise streng, mit dem Standardzertifikat scheitert jede Verbindung (Fehler mit
  `CERTIFICATE_VERIFY_FAILED`).
- Der Haken (auf der Proxmox-Seite heißt die Spalte „Zertifikat nicht prüfen“) schaltet die Prüfung
  **für genau diese Verbindung ganz ab**. Einen Abgleich mit einem Fingerabdruck gibt es in
  Nodvard Deck nicht – nur an oder aus.
- Im eigenen Netz ist das in Ordnung. Das Restrisiko: Wer sich in dein Netz hängt und sich
  als Proxmox ausgibt, könnte den Token mitlesen. Wer den Haken weglassen will, braucht auf
  Proxmox ein öffentlich anerkanntes Zertifikat (z. B. Let's Encrypt über die ACME-Funktion
  von Proxmox – dafür braucht es eine eigene Domain).

## Proxmox ohne Abo

- Die API funktioniert ohne Abo vollständig, der Token hat damit nichts zu tun.
- Ohne Abo antwortet die **Enterprise-Paketquelle mit `401`**. Nodvard Deck zeigt das in der
  Update-Zentrale von Nodvard Shield als Hinweis, nicht als Fehler („Die
  Proxmox-Enterprise-Paketquelle verlangt ein Abo (401) …“).
- Abhilfe in Proxmox: Knoten → Updates → Paketquellen (Repositories): die
  `enterprise`-Quellen (PVE und Ceph) deaktivieren und über „Hinzufügen“ die Quelle
  „No-Subscription“ ergänzen.

## Nachschlagen: welcher Aufruf welches Recht braucht

**Erweiterung „Proxmox VE“**

| Was Nodvard Deck tut | Proxmox-Aufruf | Recht |
|---|---|---|
| Verbindung prüfen | `GET /version` | keins |
| Knoten auflisten | `GET /nodes` | keins (Auslastung nur mit `Sys.Audit`) |
| Knoten-Status, Auslastung, Verlauf | `GET /nodes/{n}/status`, `/rrddata` | `Sys.Audit` |
| Datenträger und SMART | `GET /nodes/{n}/disks/list`, `/disks/smart` | `Sys.Audit` (SMART auf `/`) |
| Aufgabenverlauf, Protokoll, Ergebnis einer Aktion | `GET /nodes/{n}/tasks`, `…/tasks/{upid}/status`, `…/log` | `Sys.Audit` (für fremde Aufgaben, z. B. nächtliche Backups) |
| Installierte Kernel | `GET /nodes/{n}/apt/versions` | `Sys.Audit` |
| Wartende Updates | `GET /nodes/{n}/apt/update` | `Sys.Modify` |
| Paketlisten aktualisieren | `POST /nodes/{n}/apt/update` | `Sys.Modify` |
| Knoten neu starten | `POST /nodes/{n}/status` | `Sys.PowerMgmt` |
| VMs/Container auflisten, Status | `GET …/qemu`, `…/lxc`, `…/status/current` | `VM.Audit` (Gäste ohne dieses Recht fehlen einfach) |
| Konfiguration, vorgemerkte Änderungen, Snapshots ansehen, Verlauf | `GET …/config`, `…/pending`, `…/snapshot`, `…/rrddata` | `VM.Audit` |
| IP-Adresse eines Containers | `GET …/lxc/{id}/interfaces` | `VM.Audit` |
| IP-Adresse einer VM (Gast-Agent) | `GET …/qemu/{id}/agent/network-get-interfaces` | `VM.GuestAgent.Audit` (PVE 9), `VM.Monitor` (PVE 8) |
| Speicherbelegung | `GET /nodes/{n}/storage` | `Datastore.Audit` je Speicher (ohne: Speicher unsichtbar) |
| Speicherinhalt | `GET …/storage/{s}/content` | `Datastore.Audit`; Gast-Disks zusätzlich `VM.Config.Disk`, Sicherungen `VM.Backup` + `Datastore.AllocateSpace` |
| Starten, Hart ausschalten, Neustarten | `POST …/status/start`, `/stop`, `/reboot` | `VM.PowerMgmt` |
| Snapshot anlegen, löschen | `POST …/snapshot`, `DELETE …/snapshot/{name}` | `VM.Snapshot` |
| Snapshot zurückrollen | `POST …/snapshot/{name}/rollback` | `VM.Snapshot` oder `VM.Snapshot.Rollback` (eins reicht, B hat `VM.Snapshot`) |
| Hardware: Kerne, Sockel | `PUT …/config` | `VM.Config.CPU` |
| Hardware: RAM, Ballon, Swap | `PUT …/config` | `VM.Config.Memory` |
| Hardware: Autostart | `PUT …/config` | `VM.Config.Options` |
| Hardware: Startreihenfolge | `PUT …/config` | `VM.Config.Options`, bei VMs zusätzlich `Sys.Modify` auf `/` |
| Konsole | `POST …/vncproxy`, `GET …/vncwebsocket` | `VM.Console` |

**Erweiterung „Backups“**

| Was Nodvard Deck tut | Proxmox-Aufruf | Recht |
|---|---|---|
| Backup-Jobs anzeigen | `GET /cluster/backup`, `/cluster/backup/{id}` | `Sys.Audit` auf `/` |
| Liste „nicht gesichert“ | `GET /cluster/backup-info/not-backed-up` | `Sys.Audit` auf `/` |
| Backup-Läufe (auch die nächtlichen) | `GET /nodes/{n}/tasks?typefilter=vzdump` | `Sys.Audit` |
| Gäste auflösen, Plattengrößen für die Platzprüfung | `GET /cluster/resources`, `…/config` | `VM.Audit` |
| Backup-Speicher | `GET /nodes/{n}/storage?content=backup` | `Datastore.Audit` |
| Vorhandene Sicherungen | `GET …/storage/{s}/content?content=backup` | `VM.Backup` + `Datastore.AllocateSpace` |
| Backup jetzt / erneut versuchen | `POST /nodes/{n}/vzdump` | `VM.Backup` + `Datastore.AllocateSpace` + `Datastore.Allocate` auf dem Speicher (eine Aufbewahrung geht immer mit, ohne eigene des Jobs `keep-all=1`); bei Jobs mit Bandbreitengrenze oder ionice zusätzlich `Sys.Modify` auf `/` |
| Job anlegen, ändern | `POST /cluster/backup`, `PUT /cluster/backup/{id}` | `Sys.Modify` auf `/` + `Datastore.Allocate` auf dem Speicher |

## Hinweise

- **Wenn etwas mit `403` scheitert:** `pveum user token permissions nodvard@pve dashboard` zeigt,
  welche Rechte der Token wirklich hat; mit den Tabellen oben lässt sich das fehlende Recht finden.
  „Backup jetzt starten / erneut versuchen“ nennt bei `403` die nötigen Rechte selbst.
- **Proxmox VE 8:** Dort gibt es `VM.GuestAgent.Audit` noch nicht, `pveum role add` bricht
  mit `invalid privilege 'VM.GuestAgent.Audit'` ab. Dann das Recht einfach weglassen
  (Version zeigt `pveversion`). Das VE-8-Gegenstück `VM.Monitor` besser **nicht** vergeben:
  Es erlaubt dort auch, Befehle in der VM auszuführen und Passwörter zu setzen.
  Die VM-Adressen dann von Hand eintragen.
- **Backup-Jobs:** Dass Proxmox `Datastore.Allocate` (nicht nur `AllocateSpace`) auf dem
  Speicher verlangt, gilt für VE 8 und 9. Ältere Versionen waren eventuell großzügiger – die
  Zusatzrolle schadet dort nicht.
