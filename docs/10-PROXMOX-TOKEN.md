# Proxmox-API-Token für Nodvard Deck – mit möglichst wenig Rechten

Stand 29.09.2026. Die Rechte hier sind aus den Proxmox-Aufrufen im Nodvard-Deck-Code
abgeleitet (`extensions/proxmox`, `extensions/backups` – sonst spricht keine Erweiterung
mit Proxmox) und mit den Rechte-Angaben der Proxmox-API abgeglichen (Proxmox VE 9, Gegenprobe
VE 8). Besonderheiten von Proxmox VE 8 und älteren Versionen stehen unter
[Hinweise](#hinweise) ganz unten.

## Schneller Weg: mit Passwort verbinden

Statt der Befehle weiter unten kannst du Nodvard Deck den Zugang selbst anlegen lassen. Du gibst **einmal** Adresse
und Passwort eines Proxmox-Administrators ein (meist `root@pam`). Nodvard Deck benutzt das Passwort für genau eine
Anmeldung und vergisst es sofort – es landet weder in der Datenbank noch im Tresor oder im Protokoll. Für die
laufende Verbindung legt es einen eigenen Benutzer mit API-Token an, wie unten von Hand beschrieben.

1. Seite **„Proxmox“** → **„Mit Passwort verbinden“** (ohne Server groß in der Mitte, sonst im Reiter
   „Verbindungen“). Dafür brauchst du in Nodvard Deck Admin-Rechte.
2. **Adresse** eingeben, z. B. `https://192.168.2.10:8006` (ohne Port nimmt Nodvard Deck 8006). `http://` geht hier
   nicht: Das Passwort ginge sonst unverschlüsselt durchs Netz.
3. **Fingerabdruck vergleichen.** Proxmox hat meist ein selbst ausgestelltes Zertifikat. Nodvard Deck liest es erst
   nur (es sendet dabei nichts) und zeigt den SHA-256-Fingerabdruck. In Proxmox findest du ihn so: links den Knoten
   anklicken → **System** → **Zertifikate**, Spalte „Fingerabdruck“ – bei `pveproxy-ssl.pem`, falls es die Zeile
   gibt, sonst bei `pve-ssl.pem`. Auf dem Server geht auch
   `openssl x509 -noout -fingerprint -sha256 -in /etc/pve/local/pve-ssl.pem` (bzw. `pveproxy-ssl.pem`). Stimmen beide
   überein, setzt du den Haken. Das Passwort geht danach nur an einen Server mit **genau diesem** Zertifikat; zeigt er
   inzwischen ein anderes, bricht Nodvard Deck ab, bevor das Passwort gesendet ist, und zeigt den neuen Fingerabdruck.
   Wechselt das Zertifikat erst **nach** der Anmeldung (während Nodvard Deck den Zugang anlegt), bricht es ebenfalls
   sofort ab und sagt das ehrlich: Das Passwort ging nur an den Server mit dem bestätigten Zertifikat; die Meldung
   nennt den neuen Fingerabdruck und was in Proxmox schon angelegt war. Stimmt der Fingerabdruck nicht, gib nichts
   ein. Ist das Zertifikat von einer anerkannten Stelle bestätigt (z. B. Let's Encrypt über die ACME-Funktion von
   Proxmox), entfällt dieser Schritt.
4. **Benutzer** (Vorgabe `root@pam`, immer mit Bereich), **Passwort**, freiwillig einen **Kurznamen** (leer: Name des
   Clusters bzw. des Knotens) und **„Ansehen und steuern“** (Vorgabe) oder **„Nur ansehen“**. Ist das Modul
   **„Proxmox-Backups“** eingeschaltet, steht darunter **„Auch Proxmox-Backups verbinden“** (Vorgabe an): Dann bekommt
   es gleich einen eigenen Zugang (siehe unten). → „Verbinden“.
5. **Zwei-Faktor:** Verlangt dein Konto einen zweiten Faktor, fragt Nodvard Deck nach dem Code – aus der App (TOTP),
   ein Wiederherstellungsschlüssel oder ein YubiKey-Einmal-Code. Das Passwort geht dabei nicht noch einmal hinaus. Ein
   Sicherheitsschlüssel (WebAuthn, U2F) funktioniert nur im Browser bei Proxmox selbst; dann nimm den Weg mit Befehlen
   unten. Nodvard Deck braucht dafür Proxmox VE 7.1 oder neuer.

Gleich danach liest Nodvard Deck Knoten, VMs und Container ein (im Hintergrund, meist nach wenigen Sekunden auf der
Seite); klappt das nicht, holt der Abgleich alle 5 Minuten es nach.

**Was Nodvard Deck in Proxmox anlegt** (ein zweiter Lauf legt nichts doppelt an):

- Rolle `NodvardDeck` („Ansehen und steuern“) bzw. `NodvardDeckRO` („Nur ansehen“) mit den Rechten unten – nur die,
  die es in deiner Proxmox-Version gibt. Gibt es die Rolle schon, kommen fehlende Rechte dazu; weggenommen wird nichts.
  Erlaubt eine vorhandene Rolle mehr, als Nodvard Deck braucht, nennt Nodvard Deck diese Rechte nach dem Verbinden
  (und im Protokoll) – der neue Token bekommt sie mit; nimm sie in Proxmox weg, wenn du sie nicht bewusst vergeben hast.
- Benutzer `nodvard@pve` ohne Passwort (damit kann sich niemand an der Oberfläche anmelden). Ist er gesperrt, schaltet
  Nodvard Deck ihn wieder ein.
- API-Token `nodvard@pve!deck` mit Privilegientrennung. Gibt es `deck` schon, heißt der neue `deck-2`, `deck-3` …
  Ein vorhandener Token wird nie gelöscht oder ersetzt – er könnte noch woanders stecken.
- Berechtigung auf `/` mit der Rolle, für den Benutzer **und** den Token (mit Privilegientrennung darf der Token nur,
  was beide dürfen – so bekommt er genau die Rolle).
- Mit **„Auch Proxmox-Backups verbinden“** zusätzlich ein eigener Zugang dafür (Tabelle unten), mit **eigenem
  Benutzer** `nodvard-backup@pve` (ohne Passwort, wie oben): Rolle `NodvardDeckBackup` (bei „Nur ansehen“
  `NodvardDeckBackupRO`) auf `/`, Token `nodvard-backup@pve!deck` (`deck-2` …) mit Privilegientrennung, bei „Ansehen
  und steuern“ dazu die Rolle `NodvardDeckBackupStore` **nur** auf den Speichern mit Inhalt „Backup“ (alle, die es beim
  Verbinden gibt) und der Pool `nodvard-kopien` (Kommentar „Nodvard Deck: wiederhergestellte Kopien“) mit der Rolle
  `NodvardDeckRestore` **nur** auf diesem Pool. Jeweils für `nodvard-backup@pve` und seinen Token. `nodvard@pve` und
  der Proxmox-Token bekommen davon nichts – auch ein Token von `nodvard@pve` ohne Privilegientrennung nicht. Hat
  `nodvard-backup@pve` schon einen Token **ohne** Privilegientrennung, legt Nodvard Deck dafür keinen Zugang an (der
  Token bekäme alle Backup-Rechte mit) und nennt ihn. Die Verbindung steht danach unter „Proxmox-Backups“ (gleicher
  Kurzname; gibt es die Adresse dort schon, bekommt diese Verbindung den neuen Token und bleibt sonst, wie sie ist:
  Prüft sie das Zertifikat, wird das nie abgeschwächt; den bestätigten Fingerabdruck merkt sie sich wie die
  Proxmox-Verbindung, und ist sie ausgeschaltet, bleibt sie aus). Klappt dieser Teil
  nicht, bleibt die Proxmox-Verbindung trotzdem stehen, und die Meldung sagt, was fehlt.

Danach liest Nodvard Deck einmal mit dem neuen Token (Knotenliste und Status eines Knotens; beim Backup-Token die
Backup-Jobs). Erst wenn das klappt, stehen Verbindung und Token-Geheimnis in Nodvard Deck – genau so, als hättest du
sie unter „Einstellungen → Erweiterungen → Proxmox VE“ eingetragen. Klappt es nicht, löscht Nodvard Deck den neuen
Token gleich wieder; geht auch das nicht (oder ist die Verbindung direkt nach dem Anlegen abgerissen), nennt die
Meldung den Token, damit du ihn in Proxmox selbst löschst. Was sonst schon angelegt war (Rollen, Benutzer,
Berechtigungen, Pool), nennt jede Fehlermeldung („In Proxmox angelegt: …“); ein neuer Versuch nutzt es weiter. Ist
die Adresse schon als Verbindung eingetragen, bekommt diese den neuen Token – ihr Geheimnis wird in einem Schritt
ersetzt, klappt das nicht, arbeitet sie mit dem alten weiter. Der alte Token bleibt in Proxmox bestehen (siehe „Alte
Tokens“).

| Recht | Wofür | Nur ansehen |
|---|---|---|
| `Sys.Audit` | Knoten: Status, Verlauf, Datenträger, Aufgaben, Kernel | ja |
| `VM.Audit` | VMs und Container: Liste, Status, Hardware, Snapshots | ja |
| `VM.GuestAgent.Audit` | echte IP-Adressen der VMs (gibt es erst ab Proxmox VE 9; in VE 8 lässt Nodvard Deck es weg und nimmt nie das dortige `VM.Monitor`) | ja |
| `Datastore.Audit` | Speicherbelegung | ja |
| `VM.PowerMgmt`, `VM.Snapshot`, `VM.Console` | Starten, Herunterfahren, Neustarten, Snapshots, Konsole | – |
| `VM.Config.CPU`, `VM.Config.Memory`, `VM.Config.Options` | Hardware ändern (Kerne, RAM, Autostart, Startreihenfolge) | – |
| `Sys.PowerMgmt` | Knoten neu starten | – |
| `Sys.Modify` | wartende Updates **lesen**, Paketlisten aktualisieren, Startreihenfolge bei VMs – erlaubt in Proxmox aber auch, Systemeinstellungen zu ändern | – |
| `VM.Config.Disk` | **lesen**, welche Gast-Disks auf welchem Speicher liegen – erlaubt in Proxmox aber auch, Disks zu vergrößern oder zu verschieben | – |

Nicht dabei sind `VM.Backup` und `Datastore.AllocateSpace`: Die braucht nur „Proxmox-Backups“, und mit ihnen ließen
sich Sicherungen löschen. Deshalb bekommt „Proxmox-Backups“ seinen eigenen Zugang:

| Rolle (Pfad) | Rechte | Wofür |
|---|---|---|
| `NodvardDeckBackup` (`/`) | `Sys.Audit`, `VM.Audit`, `Datastore.Audit` (das ist auch `NodvardDeckBackupRO`) | Backup-Jobs, „nicht gesichert“, nächtliche Läufe, Gäste und Plattengrößen, Backup-Speicher |
| | `VM.Backup` | Backup jetzt starten, vorhandene Sicherungen sehen, eine Sicherung zum Wiederherstellen lesen. Proxmox erlaubt damit auch, eine Sicherung **über** den vorhandenen Gast zurückzuspielen und Sicherungen zu löschen – Nodvard Deck tut beides nie |
| | `Sys.Modify` | Backup-Jobs anlegen und ändern (verlangt Proxmox auf `/`), Läufe mit Bandbreitengrenze oder ionice, privilegierte Container wiederherstellen – erlaubt in Proxmox aber auch, Systemeinstellungen zu ändern (wie bei der Rolle `NodvardDeck`) |
| | `Datastore.AllocateSpace` | Platz auf dem Zielspeicher beim Wiederherstellen als Kopie, Fleecing-Speicher eines Jobs |
| | `SDN.Use` (ab Proxmox VE 8) | die Bridges einer wiederhergestellten Kopie |
| `NodvardDeckBackupStore` (`/storage/<Backup-Speicher>`) | `Datastore.Audit`, `Datastore.AllocateSpace`, `Datastore.Allocate` | Jobs mit diesem Speicher anlegen und ändern, Läufe mit Aufbewahrung (Nodvard Deck schickt immer eine mit). `Datastore.Allocate` darf auf dem Speicher auch Dateien löschen – deshalb nur dort |
| `NodvardDeckRestore` (`/pool/nodvard-kopien`) | `VM.Allocate`, `VM.Audit`, `VM.Config.Options`, `VM.Config.Network` | eine Kopie aus einer Sicherung anlegen, die in diesem Pool landet, und fertig einrichten (Name, Autostart, Netzwerkkarten trennen). Gäste im Pool darf der Zugang auch ändern und löschen – leg dort keine anderen Gäste ab |

Einen vorhandenen Pool `nodvard-kopien` übernimmt Nodvard Deck nur, wenn sein Kommentar genau „Nodvard Deck:
wiederhergestellte Kopien“ lautet und darin nichts Fremdes liegt: kein Speicher, und Gäste nur, wenn
`nodvard-backup@pve` dort schon die Rolle `NodvardDeckRestore` hatte (dann sind es Kopien von Nodvard Deck). Sonst
bekommt der Zugang dort keine Rechte – er dürfte die Gäste darin ändern und löschen –, und die Meldung sagt das. Kommt
später ein Backup-Speicher dazu, verbinde noch einmal mit Passwort oder gib dem Benutzer und dem Backup-Token dort
die Rolle selbst:

```bash
pveum acl modify /storage/SPEICHERNAME --users nodvard-backup@pve --roles NodvardDeckBackupStore
pveum acl modify /storage/SPEICHERNAME --tokens 'nodvard-backup@pve!deck' --roles NodvardDeckBackupStore
```

Gut zu wissen:

- Bei einem selbst ausgestellten Zertifikat merkt sich Nodvard Deck den bestätigten Fingerabdruck und prüft ihn bei
  jeder späteren Anfrage, auch für Proxmox-Backups ([siehe unten](#zertifikat-fingerabdruck-merken)).
  „Selbstsigniertes Zertifikat erlauben“ bleibt aus. Bei einem anerkannten Zertifikat gilt die normale Prüfung.
- Läuft Nodvard Deck ohne HTTPS (z. B. `http://…:8080`), geht das Passwort unverschlüsselt von deinem Browser bis zu
  Nodvard Deck; die Seite weist darauf hin. Im eigenen Heimnetz ist das meist in Ordnung, in einem fremden Netz nicht.
- Je Proxmox-Server höchstens 5 Versuche in 10 Minuten (je Nutzer 10), und immer nur einer gleichzeitig. Gezählt wird
  je Adresse und je Zertifikat – eine zweite Schreibweise derselben Adresse bringt keine neuen Versuche. Jeder Versuch
  steht unter Einstellungen → Protokoll als „Proxmox mit Passwort verbinden“ – mit Adresse, Benutzer, Token-IDs,
  Fingerabdruck und was angelegt wurde, nie mit dem Passwort.
- Das Proxmox-Konto muss Rollen, Benutzer, Berechtigungen, Pools und Tokens anlegen dürfen; `root@pam` darf das.
- Gibt es für `nodvard@pve` (oder `nodvard-backup@pve`) einen Token **ohne** Privilegientrennung, nennt Nodvard Deck
  ihn nach dem Verbinden: Er darf alles, was sein Benutzer darf.
- Nodvard Deck legt die Token-Geheimnisse im eigenen Tresor unter `proxmox-token:<Kurzname>` bzw.
  `backups-token:<Kurzname>` ab. Liegt dort unter dem Namen schon ein Geheimnis, das nicht von der Erweiterung stammt
  (etwa von Hand über die Tresor-Schnittstelle angelegt), ändert und löscht Nodvard Deck es nie: Das Verbinden bricht
  vor dem Passwort ab (bei einem selbst gewählten Kurznamen) bzw. bevor etwas gespeichert ist, und die Meldung sagt,
  was zu tun ist – einen anderen Kurznamen nehmen oder das Geheimnis selbst löschen: unter Einstellungen → Tresor, mit
  dem eigenen Passwort und, wenn Zwei-Faktor an ist, dem Code aus der App (API: `DELETE /api/v1/secrets/{id}` mit
  `current_password` und gegebenenfalls `totp_code`).

**Alte Tokens.** Jedes Verbinden mit Passwort legt neue Tokens an, die alten bleiben stehen. Das Token in Nodvard Deck ersetzt das Verbinden deshalb ohne Rückfrage nach dem Deck-Passwort (die einzige Ausnahme dieser Art, siehe [04-API.md](04-API.md) §3, „Tresor“): Es braucht das Passwort des Proxmox-Servers, und das alte Token bleibt in Proxmox gültig. Welche Tokens von
Nodvard Deck (`deck…`) keine Verbindung mehr benutzt, zeigt die Proxmox-Seite im Reiter „Verbindungen“ →
„Alte Tokens“ (auch gleich nach dem Verbinden), mit dem Befehl zum Löschen, z. B.
`pveum user token remove nodvard@pve deck-2` bzw. `pveum user token remove nodvard-backup@pve deck`. Andere Tokens
der beiden Benutzer zeigt sie nie; die von `nodvard-backup@pve` nur, wenn „Proxmox-Backups“ an ist. Löschen musst du
sie selbst (in Proxmox unter Rechenzentrum → Rechte → API-Token oder mit dem Befehl auf dem Server): Ein Token
darf die Tokens seines eigenen Benutzers nur mit dem Recht `User.Modify` löschen, und damit könnte er sich alle Rechte
seines Benutzers geben (einen Token ohne Privilegientrennung anlegen) oder den Benutzer löschen. Benutzt ein zweites Nodvard Deck dieselben Tokens, lass sie
stehen.

**Rückgängig machen:** In Nodvard Deck auf der Proxmox-Seite im Reiter „Verbindungen“ → „Entfernen“ (das
Token-Geheimnis geht mit), ebenso auf der Seite „Backups“. In Proxmox unter Rechenzentrum → Rechte die
API-Tokens, dann die Benutzer `nodvard@pve` und `nodvard-backup@pve`, die Rollen und den Pool löschen – oder auf
dem Server:

```bash
pveum user token remove nodvard@pve deck
pveum user token remove nodvard-backup@pve deck
pveum user delete nodvard@pve              # nimmt auch seine Tokens und Berechtigungen mit
pveum user delete nodvard-backup@pve       # ebenso
pveum role delete NodvardDeck              # bzw. NodvardDeckRO
pveum role delete NodvardDeckBackup        # bzw. NodvardDeckBackupRO
pveum role delete NodvardDeckBackupStore
pveum role delete NodvardDeckRestore
pveum pool delete nodvard-kopien           # erst, wenn keine Kopie mehr darin liegt (ältere Versionen: pvesh delete /pools/nodvard-kopien)
```

## Kurz vorweg

- Nodvard Deck redet mit Proxmox nur über die Web-API (Port 8006) und meldet sich mit einem
  **API-Token** an, nie mit Passwort. (Beim schnellen Weg oben dient das Passwort nur einmal zum Anlegen des Tokens.)
- Zwei Erweiterungen brauchen den Token: **Proxmox VE** (Server, VMs, Container) und
  **Proxmox-Backups** (Backup-Jobs und Sicherungen). Jede hat ihre eigene Server-Liste und ihr
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
| Sicherung als Kopie wiederherstellen | nein | ja, mit eigenem Pool und Zusatzrolle „NodvardDeckRestore“ ([unten](#wiederherstellen-als-kopie-backups)) |

¹ Proxmox gibt die Liste wartender Updates nur mit `Sys.Modify` heraus – obwohl Nodvard Deck
sie nur liest. Die Kachel zeigt dann „Nicht abrufbar: … HTTP 403: Dem Token fehlt ein Recht für diese
Abfrage.“, dahinter den Grund, den Proxmox nennt („Grund laut Proxmox: …“).
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

### Wiederherstellen als Kopie (Backups)

„Als Kopie wiederherstellen“ legt aus einer Sicherung einen **neuen** Gast an: neue Nummer, neue MAC-Adressen,
Autostart aus, Netzwerkkarten auf Wunsch getrennt. Das Original fasst Nodvard Deck dabei nicht an; seine Einstellungen
liest es aus der Sicherung. Den Autostart schaltet Nodvard Deck schon beim Anlegen ab; Netzwerkkarten trennen und Notiz
setzen geht erst, wenn Proxmox mit der Wiederherstellung fertig ist. Endet die Aktion vorher (Neustart des Dashboards,
mehr als 45 Minuten), holt der Backup-Wächter das nach (alle 15 Minuten) und meldet sich; bis dahin die Kopie nicht
starten. Am Ende kommt genau eine Meldung: fertig eingerichtet, von Proxmox abgelehnt oder gescheitert, oder die Kopie
ließ sich nicht finden. Der Wächter richtet dabei nur Gäste ein, die im Pool `nodvard-kopien` liegen (s. u.). Vor dem
Anlegen prüft Nodvard Deck die Rechte des Tokens (`GET /access/permissions`) und die Proxmox-Version und legt ohne die
nötigen gar nichts an.

**Nur in einem eigenen Pool.** Jede Kopie entsteht im Pool `nodvard-kopien` – der Name ist fest. Das Recht, Gäste
anzulegen (`VM.Allocate`), braucht der Zugang nur auf diesem Pool, nirgends sonst: Nodvard Deck kann so keinen Gast
außerhalb des Pools anlegen, und ein Gast, den du selbst in Proxmox anlegst, bleibt außer Reichweite. Die Kopie erbt als
Mitglied des Pools die Rechte des Pools, so kann Nodvard Deck sie danach fertig einrichten. Die Einrichtung „Proxmox mit
Passwort verbinden“ legt Pool und Rechte selbst an, sobald sie in deiner Version da ist (die Rollen `NodvardDeckBackup`
auf `/`, `NodvardDeckBackupStore` auf dem Backup-Speicher und `NodvardDeckRestore` auf `/pool/nodvard-kopien`;
`Mapping.Use` gehört nicht dazu). Hast du den Token von Hand angelegt, brauchst du diese Rechte:

| Recht | Wo | Wofür |
|---|---|---|
| `VM.Allocate` | auf dem Pool `nodvard-kopien` | den neuen Gast anlegen |
| `VM.Audit` | auf dem Pool | die Kopie danach nachlesen |
| `VM.Config.Options` | auf dem Pool | Name, Notiz und „Autostart aus“ |
| `VM.Config.Network` | auf dem Pool | die Netzwerkkarten der Kopie trennen, bei Containern auch den neuen Namen setzen |
| `SDN.Use` (ab Proxmox VE 8) | auf `/` | die Kopie an dieselben Bridges hängen wie das Original |
| `Datastore.AllocateSpace` (schon in B) | auf dem Zielspeicher | die Platten der Kopie anlegen |
| `VM.Backup` für den gesicherten Gast und `Datastore.AllocateSpace` (schon in B bzw. `NodvardBackupStore`) | Backup-Speicher | die Sicherung lesen |
| `Sys.Modify` (schon in B) | auf `/` | nur bei privilegierten Containern |
| `Mapping.Use` | auf der Zuordnung (`/mapping/pci/…`, `/mapping/usb/…`) | nur, wenn die VM ein Gerät über eine Zuordnung durchreicht; nur selbst zu vergeben |
| `Sys.Audit` (schon in B) | auf `/` | prüfen, dass für die neue Nummer keine HA-Regel besteht (sie würde die Kopie starten) |

Pool und Rolle legst du auf dem Proxmox-Server als root an (der Benutzer UND der Token bekommen die Rolle, wie bei den
anderen Rollen oben):

```bash
pveum pool add nodvard-kopien --comment "Nodvard Deck: wiederhergestellte Kopien"
pveum role add NodvardDeckRestore --privs "VM.Allocate VM.Audit VM.Config.Options VM.Config.Network"
pveum acl modify /pool/nodvard-kopien --users nodvard@pve --roles NodvardDeckRestore
pveum acl modify /pool/nodvard-kopien --tokens 'nodvard@pve!dashboard' --roles NodvardDeckRestore
# nur Proxmox VE 8 und neuer: die Kopie darf an die Bridges des Originals
pveum role add NodvardDeckNet --privs "SDN.Use"
pveum acl modify / --users nodvard@pve --roles NodvardDeckNet
pveum acl modify / --tokens 'nodvard@pve!dashboard' --roles NodvardDeckNet
```

Fehlt der Pool oder das Recht darauf, bleibt der Dialog „Als Kopie wiederherstellen“ gesperrt, und Nodvard Deck nennt diese
Befehle (mit deiner Token-ID) gleich dort. Ob es den Pool gibt, sieht der Zugang ohne `Pool.Audit` nicht; Nodvard Deck schließt das aus
`VM.Allocate` auf dem Pool, denn Proxmox entfernt die Rechte mit dem Pool.

**Eine Kopie behalten.** Die Kopie bleibt Mitglied des Pools, solange du sie nicht herausnimmst – und der Token behält
darauf seine Rechte. Willst du sie behalten, nimm sie aus dem Pool: in Proxmox unter Rechenzentrum → Rechte → Pools →
`nodvard-kopien` → Mitglieder die Kopie auswählen und „Entfernen“ klicken (das löscht sie nicht, sie verlässt nur den
Pool). Einen Knopf dafür gibt es in Nodvard Deck bewusst nicht: Er bräuchte das Recht `Pool.Allocate`.

**Proxmox-Version.** Eine **VM** als Kopie wiederherstellen geht ab **Proxmox VE 7.2**: Erst dort übernimmt Proxmox
`name` und `onboot` beim Wiederherstellen aus dem Aufruf (qemu-server 7.2-1). Bei einem älteren Server legt Nodvard Deck
nichts an und sagt es: „Eine VM als Kopie wiederherstellen braucht Proxmox VE 7.2 oder neuer. Dein Server hat …“. Bei VE
7.0 und 7.1 holen die normalen Updates (Shield → Updates) das nach. Bei VE 6 und älter ist ein großes Update auf eine neue
Hauptversion nötig, das die Update-Zentrale nicht einspielt; Proxmox beschreibt den Weg in seiner Upgrade-Anleitung im
Proxmox-Wiki, und Nodvard Deck sagt das auch so. **Container** gehen auch auf älteren Versionen, mit zwei Grenzen: Bis
Proxmox VE 5 fehlt `unique` (neue MAC-Adressen), dort legt Nodvard Deck nichts an. Und bis VE 7.0 verlangte Proxmox beim
Anlegen eines Containers in einem Pool zusätzlich `Permissions.Modify` oder `Pool.Allocate` auf dem Pool (pve-container vor
4.1-4); das gibt Nodvard Deck dem Zugang nicht, dort geht es erst nach den Updates (bei VE 6 nach dem Update auf VE 7).
Lässt sich die Version nicht lesen, legt Nodvard Deck trotzdem an (Proxmox prüft selbst) und weist darauf hin.

**Achtung:** `VM.Allocate` erlaubt in Proxmox auch, Gäste zu **löschen** – im Pool `nodvard-kopien` also auch die Kopien.
Nodvard Deck löscht nie einen Gast, der Token könnte es aber. Brauchst du die Funktion nicht, lass Pool und Rolle weg:
Die Wiederherstellung bleibt dann gesperrt und legt nichts an. Unter Proxmox VE 7 gibt es `SDN.Use` noch nicht, dort das
Recht weglassen. Container, die Ordner oder Geräte des Servers einbinden (Bind-Mounts), lässt Proxmox nur `root@pam`
wiederherstellen; Nodvard Deck lehnt so eine Sicherung deshalb gleich ab. Dasselbe gilt für VMs, die PCI- oder
USB-Geräte ohne Zuordnung durchreichen.

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

Rechenzentrum → Rechte (die deutschen Namen können je nach Version leicht abweichen,
in Klammern der englische):

1. **Rollen** (Roles) → „Erstellen“: Name `NodvardRO` bzw. `NodvardFull`, Rechte wie oben
   anhaken.
2. **Benutzer** (Users) → „Hinzufügen“: Benutzername `nodvard`, Realm „Proxmox VE
   authentication server“.
3. **API-Token** (API Tokens) → „Hinzufügen“: Benutzer `nodvard@pve`, Token-ID `dashboard`,
   Haken bei „Privilegientrennung“ (Privilege Separation) **gesetzt lassen**. Das angezeigte
   Geheimnis sofort kopieren.
4. **Rechte** (Permissions) → „Hinzufügen“ → „Benutzer-Berechtigung“: Pfad `/`,
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
   - **Fingerabdruck des Zertifikats (SHA-256):** hier eintragen oder nach dem Speichern auf der Proxmox-Seite im Reiter
     „Verbindungen“ → **„Zertifikat“** bestätigen, siehe nächster Abschnitt. „Selbstsigniertes Zertifikat
     erlauben“ ist veraltet und nicht mehr nötig.
2. Oben rechts **„Speichern“**. Erst danach erscheint unten unter „Zugangsdaten“ die Zeile
   **„API-Token-Geheimnis – pve1“**.
3. Dort das Geheimnis einfügen → „Speichern“. Das Schild wechselt von „Fehlt“ auf
   „Hinterlegt“.
4. Dasselbe für `pve2`.

Danach **Einstellungen → Erweiterungen → „Proxmox-Backups“** einschalten, „Konfigurieren“ und unter
„Proxmox-Server für Backups“ dieselben Einträge anlegen – **mit denselben Kurznamen** wie
bei Proxmox VE. Sonst findet die Seite „Proxmox-Backups“ die Gast-Namen nicht und zeigt nur VMIDs.

Der Proxmox-Abgleich läuft alle 5 Minuten. Spätestens dann stehen Knoten, VMs und
Container auf der Seite „Proxmox“ und in der Übersicht.

Gut zu wissen:

- **Token austauschen:** in der Zeile „API-Token-Geheimnis – …“ auf „Ersetzen“, neuen Wert eintragen und wieder „Ersetzen“. Auf der
  Seite „Proxmox“ (Reiter „Verbindungen“) bzw. „Proxmox-Backups“ (unter „Verbindungen verwalten“) heißt der Knopf „Token ersetzen“, wenn
  schon ein Token da ist, und „Token setzen“, wenn es fehlt.
- **Adresse ändern oder Verbindung entfernen:** Dabei löscht Nodvard Deck das Token-Geheimnis
  dieser Verbindung (auch beim Entfernen unter „Verbindungen verwalten“); nach einer neuen Adresse
  trägst du es neu ein. Dasselbe gilt für einen neuen Kurznamen. Eine andere Schreibweise derselben
  Adresse (Groß-/Kleinschreibung im Rechnernamen, `:443` bei `https://`, `/` am Ende) zählt nicht
  als Änderung.
- **Keine Weiterleitungen:** Nodvard Deck folgt bei Aufrufen an Proxmox keinen Weiterleitungen, auch nicht
  beim Aufbau der Konsole. Trag deshalb die endgültige Adresse des Knotens ein (`https://…:8006`, nicht
  `http://`), keinen Reverse-Proxy, der weiterleitet. Sonst scheitern Abfragen, „Verbindung testen“ und Konsole
  mit „Der Server hat die Verbindung auf eine andere Adresse umgeleitet …“.
- **Kein Proxy aus der Umgebung:** Nodvard Deck verbindet sich immer direkt mit der eingetragenen Adresse, auch beim
  Aufbau der Konsole. Ein Proxy, der in der Umgebung von Nodvard Deck eingetragen ist (`HTTP_PROXY`, `HTTPS_PROXY` usw.),
  wird dafür nie benutzt; die Adresse muss vom Rechner mit Nodvard Deck also direkt erreichbar sein.
- **Einen Server vorübergehend abschalten**, ohne das Token zu verlieren: Seite „Proxmox“ → Reiter
  „Verbindungen“ (bzw. „Proxmox-Backups“ → „Verbindungen verwalten“) → Knopf „aktiv“ anklicken (wird zu
  „deaktiviert“).

## Zertifikat: Fingerabdruck merken

- Proxmox bringt ein selbst ausgestelltes Zertifikat mit. Nodvard Deck prüft Zertifikate normalerweise gegen
  anerkannte Zertifizierungsstellen; mit dem Standardzertifikat scheitert das („Das Zertifikat von Proxmox wird nicht
  akzeptiert. Bei einem selbstsignierten Zertifikat vergleiche seinen Fingerabdruck und merk ihn dir unter
  „Verbindungen verwalten“ (Knopf „Zertifikat“).“).
- **Fingerabdruck merken:** Proxmox-Seite → Reiter „Verbindungen“ (Backups-Seite: „Verbindungen verwalten“) → bei der Verbindung
  **„Zertifikat“**. Nodvard Deck liest nur das Zertifikat (es sendet dabei nichts) und zeigt den Fingerabdruck.
  Vergleiche ihn in Proxmox: Knoten → **System** → **Zertifikate**, Spalte „Fingerabdruck“ (`pveproxy-ssl.pem`, falls
  es die Zeile gibt, sonst `pve-ssl.pem`), oder auf dem Server mit
  `openssl x509 -noout -fingerprint -sha256 -in /etc/pve/local/pve-ssl.pem`. Stimmt er, Haken setzen und
  **„Fingerabdruck merken“**. Ab dann spricht die Verbindung nur mit dem Server, der genau dieses Zertifikat zeigt –
  zeigt er ein anderes, scheitert schon der Verbindungsaufbau, bevor das Token hinausgeht. „Selbstsigniertes
  Zertifikat erlauben“ geht dabei aus. Den Wert kannst du auch von Hand eintragen (Einstellungen der Erweiterung, Feld
  „Fingerabdruck des Zertifikats (SHA-256)“, mit oder ohne Doppelpunkte). Beim
  [schnellen Weg](#schneller-weg-mit-passwort-verbinden) passiert das von selbst.
- **Zertifikat gewechselt** (neues Zertifikat in Proxmox, etwa nach einer Neuinstallation): Die Verbindung meldet
  „Der Server zeigt ein anderes Zertifikat als das gemerkte …“ und hält an. Unter „Zertifikat“ stehen dann der neue
  und der bisher gemerkte Fingerabdruck. Vergleiche den neuen in Proxmox und drück **„Neuen Fingerabdruck
  bestätigen“**. Gespeichert wird nur, was der Server in diesem Moment zeigt. Hast du das Zertifikat nicht selbst
  gewechselt, bestätige nichts – dann antwortet unter der Adresse vielleicht ein anderer Rechner. Jede Bestätigung hier
  steht im Protokoll („Proxmox: Fingerabdruck des Zertifikats bestätigt“ bzw. „Proxmox-Backups: …“, mit altem und
  neuem Fingerabdruck). Ändert sich die Adresse einer Verbindung, fällt der gemerkte Fingerabdruck weg.
- Steht im Feld etwas, das kein Fingerabdruck ist, verbindet sich Nodvard Deck nicht mit dem Server (es fällt nie
  still auf „nicht prüfen“ zurück); die Tabelle zeigt dann „Fingerabdruck ungültig“.
- **„Selbstsigniertes Zertifikat erlauben“** (veraltet; in der Tabelle „wird nicht geprüft“) schaltet die Prüfung für
  diese Verbindung ganz ab. Es bleibt für ältere Einstellungen, gilt aber nicht, solange ein Fingerabdruck eingetragen
  ist. Das Restrisiko damit: Wer sich in dein Netz hängt und sich als Proxmox ausgibt, könnte den Token mitlesen. Mit
  gemerktem Fingerabdruck geht das nicht mehr.
- Ein öffentlich anerkanntes Zertifikat (z. B. Let's Encrypt über die ACME-Funktion von Proxmox, braucht eine eigene
  Domain) wird ohne Fingerabdruck normal geprüft. Lass das Feld dann leer: Let's Encrypt erneuert das Zertifikat alle
  paar Wochen, mit gemerktem Fingerabdruck müsstest du jedes Mal neu bestätigen.

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
| Datenträger, SMART und ZFS-Pools | `GET /nodes/{n}/disks/list`, `/disks/smart`, `/disks/zfs` | `Sys.Audit` (SMART auf `/`) |
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

**Erweiterung „Proxmox-Backups“**

| Was Nodvard Deck tut | Proxmox-Aufruf | Recht |
|---|---|---|
| Backup-Jobs anzeigen | `GET /cluster/backup`, `/cluster/backup/{id}` | `Sys.Audit` auf `/` |
| Liste „nicht gesichert“ | `GET /cluster/backup-info/not-backed-up` | `Sys.Audit` auf `/` |
| Backup-Läufe (auch die nächtlichen) | `GET /nodes/{n}/tasks?typefilter=vzdump` | `Sys.Audit` |
| Grund eines fehlgeschlagenen Laufs (Klartext) | `GET /nodes/{n}/tasks/{upid}/log` | `Sys.Audit` (ohne: nur der Status des Laufs) |
| Gäste auflösen, Plattengrößen für die Platzprüfung | `GET /cluster/resources`, `…/config` | `VM.Audit` |
| Backup-Speicher | `GET /nodes/{n}/storage?content=backup` | `Datastore.Audit` |
| Vorhandene Sicherungen | `GET …/storage/{s}/content?content=backup` | `VM.Backup` + `Datastore.AllocateSpace` |
| Backup jetzt / erneut versuchen | `POST /nodes/{n}/vzdump` | `VM.Backup` + `Datastore.AllocateSpace` + `Datastore.Allocate` auf dem Speicher (eine Aufbewahrung geht immer mit, ohne eigene des Jobs `keep-all=1`); bei Jobs mit Bandbreitengrenze oder ionice zusätzlich `Sys.Modify` auf `/` |
| Job anlegen, ändern | `POST /cluster/backup`, `PUT /cluster/backup/{id}` | `Sys.Modify` auf `/` + `Datastore.Allocate` auf dem Speicher |
| Als Kopie wiederherstellen: Sicherung prüfen | `GET …/storage/{s}/content?content=backup&vmid=…`, `GET /nodes/{n}/vzdump/extractconfig` | `VM.Backup` + `Datastore.AllocateSpace` |
| Als Kopie wiederherstellen: freie Nummer, Zielspeicher | `GET /cluster/nextid`, `GET /nodes/{n}/storage?content=images`, `…/storage/{s}/status` | keins; `Datastore.Audit` oder `Datastore.AllocateSpace` |
| Als Kopie wiederherstellen | `POST /nodes/{n}/qemu` bzw. `/lxc` (immer mit `pool=nodvard-kopien`, `unique=1` und `onboot=0`, nie mit `force` oder `start`) | `VM.Allocate`, `VM.Config.Options` (Container: zusätzlich `VM.Config.Network`) auf dem Pool `nodvard-kopien`, `Datastore.AllocateSpace` auf dem Ziel, Lesen der Sicherung wie oben, ab VE 8 `SDN.Use`, privilegierte Container `Sys.Modify` |
| Als Kopie wiederherstellen: eigene Rechte (auch auf dem Pool), Version, HA-Regeln | `GET /access/permissions?path=…`, `GET /version`, `GET /cluster/ha/resources` | keins (eigene Rechte, Version); `Sys.Audit` auf `/` für HA |
| Stand der Wiederherstellung | `GET /nodes/{n}/tasks/{upid}/status`, `GET /nodes/{n}/tasks?typefilter=qmrestore\|vzrestore` | keins (eigene Aufgabe) |
| Kopie fertig einrichten (Netzwerkkarten trennen, Notiz) | `GET`/`PUT /nodes/{n}/qemu\|lxc/{neue Nummer}/config` | `VM.Audit`, `VM.Config.Network`, `VM.Config.Options` (kommen über den Pool) |

## Hinweise

- **Wenn etwas mit `403` scheitert:** `pveum user token permissions nodvard@pve dashboard` zeigt,
  welche Rechte der Token wirklich hat; mit den Tabellen oben lässt sich das fehlende Recht finden.
  „Backup jetzt starten / erneut versuchen“ nennt bei `403` die nötigen Rechte selbst, ebenso „Als Kopie wiederherstellen“.
- **Proxmox VE 8:** Dort gibt es `VM.GuestAgent.Audit` noch nicht, `pveum role add` bricht
  mit `invalid privilege 'VM.GuestAgent.Audit'` ab. Dann das Recht einfach weglassen
  (Version zeigt `pveversion`). Das VE-8-Gegenstück `VM.Monitor` besser **nicht** vergeben:
  Es erlaubt dort auch, Befehle in der VM auszuführen und Passwörter zu setzen.
  Die VM-Adressen dann von Hand eintragen.
- **Backup-Jobs:** Dass Proxmox `Datastore.Allocate` (nicht nur `AllocateSpace`) auf dem
  Speicher verlangt, gilt für VE 8 und 9. Ältere Versionen waren eventuell großzügiger – die
  Zusatzrolle schadet dort nicht.
