# Proxmox API token for Nodvard Deck – with as few privileges as possible

> The Nodvard Deck interface is currently available in German only; an English interface is planned. UI labels are given in English with the German original in quotes.

As of 29 September 2026. The privileges listed here are derived from the Proxmox calls in the Nodvard Deck code
(`extensions/proxmox`, `extensions/backups` – no other extension talks to Proxmox) and
checked against the privilege requirements of the Proxmox API (Proxmox VE 9, double-checked
against VE 8). Special cases for Proxmox VE 8 and older versions are listed under
[Notes](#notes) at the very bottom.

## Quick way: connect with a password

Instead of the commands below, Nodvard Deck can set up the access itself. On the **"Proxmox"** page click
**"Connect with password"** („Mit Passwort verbinden“; admin rights in Nodvard Deck needed), enter the address
(`https://…:8006`; `http://` is refused) and **compare the SHA-256 fingerprint** of the certificate with the one in
Proxmox (node → System → Certificates, column "Fingerprint"; `pveproxy-ssl.pem` if present, otherwise `pve-ssl.pem`).
Then enter user (default `root@pam`), password, an optional short name and "view and control" (default) or "view
only". If the "Proxmox backups" module is on, "Also connect Proxmox backups" („Auch Proxmox-Backups verbinden“, on by
default) creates a separate access for it. If your account asks for a second factor, Nodvard Deck asks for the code
(TOTP app, recovery key or YubiKey OTP; WebAuthn/U2F keys are not supported – use the manual way). Proxmox VE 7.1 or
newer is required.

The password is used for exactly one login, only towards a server presenting exactly the confirmed certificate, and
is never stored or logged. If the certificate changes after the login, Nodvard Deck stops at once and says so (the
password went only to the confirmed server) and shows the new fingerprint. Nodvard Deck then creates (idempotently) the
role `NodvardDeck` (or `NodvardDeckRO`), the user `nodvard@pve` without password, the API token `nodvard@pve!deck`
(`deck-2`, … if it exists; existing tokens are never deleted) with privilege separation, and ACLs on `/` for user and
token. Role privileges: `Sys.Audit`, `VM.Audit`, `VM.GuestAgent.Audit` (VE 9 only), `Datastore.Audit`; for "view and
control" additionally `VM.PowerMgmt`, `VM.Snapshot`, `VM.Console`, `VM.Config.CPU`, `VM.Config.Memory`,
`VM.Config.Options`, `Sys.PowerMgmt`, `Sys.Modify` and `VM.Config.Disk` (the last two are also needed just to read
pending updates and guest disks). No backup privileges for this token.

With "Also connect Proxmox backups" the backups module gets its own user `nodvard-backup@pve` (no password) and token
`nodvard-backup@pve!deck` (privilege separation) with the role `NodvardDeckBackup` on `/` (`Sys.Audit`, `VM.Audit`,
`Datastore.Audit`, `VM.Backup`, `Sys.Modify`, `Datastore.AllocateSpace`, `SDN.Use`; "view only": `NodvardDeckBackupRO`
with the first three), and for "view and control" the role `NodvardDeckBackupStore` (`Datastore.Audit`,
`Datastore.AllocateSpace`, `Datastore.Allocate`) only on storages with content "backup", plus the pool `nodvard-kopien`
with the role `NodvardDeckRestore` (`VM.Allocate`, `VM.Audit`, `VM.Config.Options`, `VM.Config.Network`) only on that
pool (for restoring a backup as a copy). Same ACLs for `nodvard-backup@pve`; `nodvard@pve` gets none of them. Proxmox
also lets this access delete backups and restore a backup over an existing guest – Nodvard Deck never does either. If
`nodvard-backup@pve` already has a token without privilege separation, no backup access is created. An existing pool
`nodvard-kopien` is only used if its comment is exactly "Nodvard Deck: wiederhergestellte Kopien" and it holds nothing
foreign (no storage; guests only if `nodvard-backup@pve` already had `NodvardDeckRestore` there). An existing backups
connection to the same address never gets a weaker certificate check (it remembers the confirmed fingerprint like the
Proxmox connection) and stays switched off if it was. If that part fails,
the Proxmox connection still stays.

Token secrets are stored as `proxmox-token:<name>` and `backups-token:<name>`. A secret under that name that the
extension did not create (e.g. added by hand through the secrets API) is never changed or deleted: connecting stops
before anything is stored and says so (HTTP 409, code `secret_foreign`).

Nodvard Deck reads once with each new token and only then stores connections and token secrets; right after that it
reads in nodes, VMs and containers in the background. Every error message lists what was already created in Proxmox.
Old tokens of Nodvard Deck that no connection uses any more are listed on the Proxmox page in the "Connections" tab →
"Old tokens" („Verbindungen“ → „Alte Tokens“) with the command to delete them (`pveum user token remove nodvard@pve
deck-2`, `pveum user token remove nodvard-backup@pve deck`); Nodvard Deck does not delete them itself, because a token
needs `User.Modify` for that, which would let it give itself all privileges of its user. To undo everything: remove
the connections in Nodvard Deck, then on the Proxmox server `pveum user delete nodvard@pve` and
`pveum user delete nodvard-backup@pve`, `pveum role delete` for `NodvardDeck`, `NodvardDeckBackup`,
`NodvardDeckBackupStore`, `NodvardDeckRestore` and `pveum pool delete nodvard-kopien`. Details in the German version:
[10-PROXMOX-TOKEN.md](../10-PROXMOX-TOKEN.md#schneller-weg-mit-passwort-verbinden).

## In brief

- Nodvard Deck talks to Proxmox only through the web API (port 8006) and logs in with an
  **API token**, never with a password.
- Two extensions need the token: **Proxmox VE** (servers, VMs, containers) and
  **Proxmox-Backups** (backup jobs and backups). Each has its own server list and its own token
  field. You can enter the same token in both.
- The examples assume two separate Proxmox servers without a cluster: `pve1` (192.168.1.20)
  and `pve2` (192.168.1.21). Users and tokens then exist **per server**: run the commands
  below on **each** of them. The token has the same name everywhere (`nodvard@pve!dashboard`),
  but the secret is different on each server. In a cluster, users, roles and tokens apply to
  all nodes, so doing it once is enough.
- Do **not** use `root@pam`. A dedicated user `nodvard@pve` with its own role may do only
  what Nodvard Deck needs, and can be locked or deleted at any time.

## Which variant?

| Function in Nodvard Deck | A: view only | B: everything |
|---|---|---|
| Discover nodes, VMs and containers; status, utilization, history | yes | yes |
| View hardware and snapshots, disks/SMART, task history, storage usage | yes | yes |
| Real IP addresses of the VMs (via the guest agent) | yes | yes |
| Backup jobs, last run, "Not backed up" list („nicht gesichert“) | yes | yes |
| "Proxmox updates" tile („Proxmox-Updates“; pending packages) | no ¹ | yes |
| Storage overview: which guest disks are on which storage | no ² | yes |
| Backups: existing backups per guest | only with A+ ³ | yes |
| Start, hard power off, reboot | no | yes |
| Create, delete, roll back snapshots | no | yes |
| Change hardware (cores, RAM, autostart, start order) | no | yes |
| Console | no | yes |
| Refresh package lists, reboot node | no | yes |
| Start a backup now / retry | no | yes, with an extra role for the backup storage |
| Create and change backup jobs | no | yes, with an extra role for the backup storage |
| Restore a backup as a copy („Als Kopie wiederherstellen“) | no | yes, with its own pool and the extra role "NodvardDeckRestore" ([below](#restore-as-a-copy-backups)) |

¹ Proxmox only returns the list of pending updates with `Sys.Modify` – even though Nodvard Deck
only reads it. The tile then shows "Not available: … HTTP 403: the token lacks a privilege for this query"
(„Nicht abrufbar: … HTTP 403: Dem Token fehlt ein Recht für diese Abfrage.“), followed by the reason Proxmox gives
(„Grund laut Proxmox: …“).
² Proxmox only shows guest disks in the storage content with `VM.Config.Disk`.
³ Proxmox only shows existing backups to someone who may back up the guest (`VM.Backup`) and
may allocate space on the storage (`Datastore.AllocateSpace`). With read-only privileges the
list stays empty.

## How permissions work (read once, then it just works)

- **Role** = a list of privileges. **Permission** = "this user/token gets this role on this
  path". Path `/` with "Propagate" applies to everything below it.
- **Privilege separation** (`--privsep 1`, the default): the token may do only what **the user
  and the token both** may do. That is why both always get the same permission below. If the
  line for the user is missing, the token may do nothing at all (HTTP 403).
- A permission on a deeper path (e.g. `/storage/local`) **replaces** the privileges inherited
  from `/` there; it does not add to them. This matters for variant B.

All commands run on the Proxmox server as root: in the Proxmox web interface click the node
on the left → "Shell", or use `ssh root@192.168.1.20` (second server: `192.168.1.21`).

## Variant A: view only

```bash
pveum role add NodvardRO --privs "Sys.Audit VM.Audit VM.GuestAgent.Audit Datastore.Audit"
pveum user add nodvard@pve --comment "Nodvard Deck (API token only)"
pveum user token add nodvard@pve dashboard --privsep 1 --comment "Nodvard Deck"
pveum acl modify / --users nodvard@pve --roles NodvardRO
pveum acl modify / --tokens 'nodvard@pve!dashboard' --roles NodvardRO
```

`pveum user token add` shows a table with `full-tokenid` (`nodvard@pve!dashboard`) and
`value` – **`value` is the secret and is shown only this one time.** Copy it right away.
`nodvard@pve` does not need a password; without a password nobody can log in to the Proxmox
web interface with it either.

**A+ – additionally the list of existing backups.** Note: with these two privileges the token
can also trigger backups through the Proxmox API and **delete** existing backups of the guests.
The same applies to variant B. Nodvard Deck itself never deletes a backup directly. But if you start a
backup in Nodvard Deck ("Start a backup now / retry", variant B only), Proxmox may prune afterwards
according to the job's retention.

```bash
pveum role modify NodvardRO --append 1 --privs "VM.Backup Datastore.AllocateSpace"
```

## Variant B: everything Nodvard Deck can do

```bash
pveum role add NodvardFull --privs "Sys.Audit Sys.Modify Sys.PowerMgmt VM.Audit VM.GuestAgent.Audit VM.PowerMgmt VM.Console VM.Snapshot VM.Config.CPU VM.Config.Memory VM.Config.Options VM.Config.Disk VM.Backup Datastore.Audit Datastore.AllocateSpace"
pveum user add nodvard@pve --comment "Nodvard Deck (API token only)"
pveum user token add nodvard@pve dashboard --privsep 1 --comment "Nodvard Deck"
pveum acl modify / --users nodvard@pve --roles NodvardFull
pveum acl modify / --tokens 'nodvard@pve!dashboard' --roles NodvardFull
```

**Creating and changing backup jobs** additionally requires `Datastore.Allocate` in Proxmox on
the job's backup storage (when changing a job, also on its previous storage). The same applies to
**starting a backup now / retry**: Nodvard Deck sends the job's settings and retention along. If the
job has a retention, Proxmox may prune afterwards and delete older backups of the guest on that
storage, including manual ones and those of other jobs; the confirmation prompt says so. Without a
retention of its own, `keep-all=1` is sent and everything is kept.
On that storage `Datastore.Allocate` also allows deleting files – so grant it only there, not on `/`. And because the
deeper path replaces the inherited privileges, Audit and AllocateSpace are part of this role:

```bash
pvesm status                      # shows the names of your storages
pveum role add NodvardBackupStore --privs "Datastore.Audit Datastore.AllocateSpace Datastore.Allocate"
pveum acl modify /storage/STORAGE_NAME --users nodvard@pve --roles NodvardBackupStore
pveum acl modify /storage/STORAGE_NAME --tokens 'nodvard@pve!dashboard' --roles NodvardBackupStore
```

Replace `STORAGE_NAME` with the storage your backup jobs write to; if you have several backup
storages, repeat the two `acl` lines for each storage.

### Restore as a copy (Backups)

„Als Kopie wiederherstellen“ creates a **new** guest from a backup: new number, new MAC addresses, autostart off,
network cards disconnected if you want. Nodvard Deck does not touch the original; it reads its settings from the
backup. Autostart is switched off right when the copy is created; disconnecting the network cards and setting the note
only works once Proxmox has finished the restore. If the action ends earlier (dashboard restart, more than 45 minutes),
the backup watch catches up (every 15 minutes) and reports back; do not start the copy until then. There is exactly one
message at the end: finished setting up, refused or failed by Proxmox, or the copy could not be found. The watch only
sets up guests that are in the pool `nodvard-kopien` (see below). Before creating anything, Nodvard Deck checks the
token's privileges (`GET /access/permissions`) and the Proxmox version, and creates nothing without the required ones.

**Only in its own pool.** Every copy is created in the pool `nodvard-kopien` – the name is fixed. The token needs the
privilege to create guests (`VM.Allocate`) only on this pool and nowhere else: Nodvard Deck cannot create a guest
outside the pool, and guests you create in Proxmox yourself stay out of reach. As a member of the pool the copy inherits
the pool's privileges, which is how Nodvard Deck can finish setting it up afterwards. The setup "connect Proxmox with a
password" creates pool and privileges itself as soon as it is in your version (the roles `NodvardDeckBackup` on `/`,
`NodvardDeckBackupStore` on the backup storage and `NodvardDeckRestore` on `/pool/nodvard-kopien`; `Mapping.Use` is not
part of it). If you created the token by hand, you need these privileges:

| Privilege | Where | What for |
|---|---|---|
| `VM.Allocate` | on the pool `nodvard-kopien` | create the new guest |
| `VM.Audit` | on the pool | read the copy back afterwards |
| `VM.Config.Options` | on the pool | name, note and "autostart off" |
| `VM.Config.Network` | on the pool | disconnect the copy's network cards; for containers also set the new name |
| `SDN.Use` (Proxmox VE 8 and later) | on `/` | attach the copy to the same bridges as the original |
| `Datastore.AllocateSpace` (already in B) | on the target storage | create the copy's disks |
| `VM.Backup` for the backed-up guest and `Datastore.AllocateSpace` (already in B or `NodvardBackupStore`) | backup storage | read the backup |
| `Sys.Modify` (already in B) | on `/` | only for privileged containers |
| `Mapping.Use` | on the mapping (`/mapping/pci/…`, `/mapping/usb/…`) | only if the VM passes a device through via a mapping; only to be granted by you |
| `Sys.Audit` (already in B) | on `/` | check that no HA rule exists for the new number (it would start the copy) |

You create pool and role on the Proxmox server as root (the user AND the token get the role, as with the other roles
above):

```bash
pveum pool add nodvard-kopien --comment "Nodvard Deck: wiederhergestellte Kopien"
pveum role add NodvardDeckRestore --privs "VM.Allocate VM.Audit VM.Config.Options VM.Config.Network"
pveum acl modify /pool/nodvard-kopien --users nodvard@pve --roles NodvardDeckRestore
pveum acl modify /pool/nodvard-kopien --tokens 'nodvard@pve!dashboard' --roles NodvardDeckRestore
# Proxmox VE 8 and later only: the copy may use the original's bridges
pveum role add NodvardDeckNet --privs "SDN.Use"
pveum acl modify / --users nodvard@pve --roles NodvardDeckNet
pveum acl modify / --tokens 'nodvard@pve!dashboard' --roles NodvardDeckNet
```

If the pool or the privilege on it is missing, the dialog „Als Kopie wiederherstellen“ stays locked, and Nodvard Deck
shows these commands (with your token ID) right there. Without `Pool.Audit` the token cannot see whether the pool
exists; Nodvard Deck concludes it from `VM.Allocate` on the pool, because Proxmox removes the privileges together with
the pool.

**Keeping a copy.** The copy stays a member of the pool until you take it out – and the token keeps its privileges on
it. If you want to keep it, take it out of the pool: in Proxmox under Datacenter → Permissions → Pools →
`nodvard-kopien` → Members select the copy and click „Remove“ (this does not delete it, it only leaves the pool; in the
German interface: Rechenzentrum → Rechte → Pools → Mitglieder → Entfernen). There is deliberately no button for it in
Nodvard Deck: it would need the `Pool.Allocate` privilege.

**Proxmox version.** Restoring a **VM** as a copy works from **Proxmox VE 7.2**: only there does Proxmox take `name` and
`onboot` from the call when restoring (qemu-server 7.2-1). On an older server Nodvard Deck creates nothing and says so
(„Eine VM als Kopie wiederherstellen braucht Proxmox VE 7.2 oder neuer. Dein Server hat …“). On VE 7.0 and 7.1 the
normal updates (Shield → Updates) bring it up to date. On VE 6 and older a big update to a new major version is
needed, which the update center does not apply; Proxmox describes the way in its upgrade guide in the Proxmox wiki, and
Nodvard Deck says so too. **Containers** also work on older versions, with two limits: up to Proxmox VE 5 `unique`
(new MAC addresses) is missing, so Nodvard Deck creates nothing there. And up to VE 7.0 Proxmox additionally required
`Permissions.Modify` or `Pool.Allocate` on the pool when creating a container in a pool (pve-container before 4.1-4);
Nodvard Deck does not give the token that, so there it only works after the updates (on VE 6 after the update to VE 7).
If the version cannot be read, Nodvard Deck creates the copy anyway (Proxmox checks itself) and points this out.

**Caution:** In Proxmox `VM.Allocate` also allows **deleting** guests – in the pool `nodvard-kopien`, that means the
copies. Nodvard Deck never deletes a guest, but the token could. If you do not need the feature, leave pool and role out:
the restore then stays locked and creates nothing. Proxmox VE 7 has no `SDN.Use` yet; leave it out there. Containers that
bind folders or devices of the server (bind mounts) can only be restored by `root@pam`; Nodvard Deck therefore refuses
such a backup right away. The same applies to VMs that pass through PCI or USB devices without a mapping.

### What you can leave out in B

| Privilege | What Nodvard Deck needs it for | If it is missing … |
|---|---|---|
| `Sys.PowerMgmt` | "Reboot node" („Knoten neu starten“) | only this button fails |
| `Sys.Modify` (on `/`) | "Proxmox updates" tile („Proxmox-Updates“), "Refresh package lists" („Paketlisten aktualisieren“), creating/changing backup jobs, starting a backup now / retry for jobs with a bandwidth limit or ionice, start order for VMs | the updates tile shows HTTP 403, these actions fail |
| `VM.Console` | Console | no console |
| `VM.Config.Disk` | Guest disks in the storage overview (Nodvard Deck only reads – but with this privilege Proxmox also allows growing/moving disks) | this list is missing |
| `VM.GuestAgent.Audit` | Real IP addresses of the VMs | Nodvard Deck enters the address of the Proxmox server as a placeholder; then set the correct address by hand ([FIRST-SETUP.md](FIRST-SETUP.md#5-servers-and-ssh-access)) |

If you want to add `Sys.Modify` or the privileges for making changes later: changing the role
is enough, the token and the Nodvard Deck settings stay, e.g.
`pveum role modify NodvardRO --append 1 --privs "VM.PowerMgmt"`.

## The same in the Proxmox web interface

Datacenter → Permissions („Rechenzentrum → Rechte“) – menu names as in the English Proxmox
interface, with the German label in quotes; they can differ slightly depending on the version:

1. **Roles** („Rollen“) → "Create" („Erstellen“): name `NodvardRO` or `NodvardFull`, tick the
   privileges as above.
2. **Users** („Benutzer“) → "Add" („Hinzufügen“): user name `nodvard`, realm "Proxmox VE
   authentication server".
3. **API Tokens** („API-Token“) → "Add": user `nodvard@pve`, token ID `dashboard`, **leave** the
   "Privilege Separation" („Privilegientrennung“) checkbox **ticked**. Copy the secret that is shown
   right away.
4. **Permissions** („Rechte“) → "Add" → "User Permission" („Benutzer-Berechtigung“): path `/`,
   user `nodvard@pve`, role as above, "Propagate" („Vererben“) on. Then "Add" once more → "API Token
   Permission" („API-Token-Berechtigung“): path `/`, token `nodvard@pve!dashboard`, same role.
5. Only for variant B, for backup jobs and "Start a backup now / retry": the same with path
   `/storage/STORAGE_NAME` and role `NodvardBackupStore`, again for the user **and** the token.

## Check before you enter it in Nodvard Deck

On the Proxmox server – shows what the token can really do:

```bash
pveum user token permissions nodvard@pve dashboard
```

From the machine running Nodvard Deck (checks network, token and privileges in one go;
`NODE_NAME` is the name shown in the Proxmox web interface on the left under "Datacenter"):

```bash
curl -sk -H 'Authorization: PVEAPIToken=nodvard@pve!dashboard=YOUR-SECRET-HERE' \
  https://192.168.1.20:8006/api2/json/nodes/NODE_NAME/status
```

Keep the **single** quotes – otherwise bash trips over the `!`.
A response with `{"data":{...}}` = all good. `401` = token ID or secret wrong.
`403 … Permission check failed` = permission missing (usually the line for the user).

## Enter it in Nodvard Deck

Turn on **Settings → Extensions → "Proxmox VE"** („Einstellungen → Erweiterungen → Proxmox VE“;
switch on the right), then "Configure" („Konfigurieren“):

1. Under "Proxmox servers" („Proxmox-Server“) click **"Add server"** („Server hinzufügen“) and
   fill in:
   - **Short name** („Kurzname“): `pve1` (or `pve2`). Do not change it after the first
     discovery – the short name is part of the server names (e.g. `proxmox-pve1-vm-100`).
   - **Address** („Adresse“): `https://192.168.1.20:8006` (second server:
     `https://192.168.1.21:8006`)
   - **API token ID** („API-Token-ID“): `nodvard@pve!dashboard`
   - **Certificate fingerprint (SHA-256)** („Fingerabdruck des Zertifikats (SHA-256)“): enter it here, or confirm it
     after saving on the Proxmox page in the "Connections" tab („Verbindungen“) → **"Certificate"** („Zertifikat“), see the next
     section. "Allow self-signed certificate" („Selbstsigniertes Zertifikat erlauben“) is deprecated and no longer
     needed.
2. Top right: **"Save"** („Speichern“). Only after that does the row
   **"API token secret – pve1"** („API-Token-Geheimnis – pve1“) appear further down under
   "Credentials" („Zugangsdaten“).
3. Paste the secret there → "Save". The badge changes from "Missing" („Fehlt“) to
   "Stored" („Hinterlegt“).
4. Do the same for `pve2`.

Then turn on **Settings → Extensions → "Proxmox-Backups"** („Einstellungen → Erweiterungen → Proxmox-Backups“),
click "Configure" („Konfigurieren“) and, under "Proxmox servers for backups"
(„Proxmox-Server für Backups“), create the same entries – **with the same short names** as in
Proxmox VE. Otherwise the Proxmox-Backups page cannot find the guest names and shows only VM IDs.

Proxmox discovery runs every 5 minutes. After that at the latest, nodes, VMs and containers
show up on the "Proxmox" page and in the "Overview" („Übersicht“).

Good to know:

- **Replacing the token:** in the row "API token secret – …" („API-Token-Geheimnis – …“)
  click "Replace" („Ersetzen“), enter the new value and click "Replace" again. On the
  Proxmox page ("Connections" tab, „Verbindungen“) or Proxmox-Backups page (under "Manage connections"
  („Verbindungen verwalten“)) the button is
  called "Replace token" („Token ersetzen“) when a token exists and "Set token" („Token setzen“)
  when it is missing.
- **Changing the address or removing a connection:** Nodvard Deck then deletes the token secret of
  this connection (also when removing it under "Manage connections" („Verbindungen verwalten“));
  after a new address you enter it again. The same applies to a new short name. A different spelling
  of the same address (upper/lower case in the host name, `:443` with `https://`, trailing `/`) does
  not count as a change.
- **No redirects:** Nodvard Deck never follows redirects when calling Proxmox, not even when opening
  the console. So enter the final address of the node (`https://…:8006`, not `http://`), not a reverse proxy that
  redirects. Otherwise queries, "Test connection" („Verbindung testen“) and the console fail with "Der Server hat die
  Verbindung auf eine andere Adresse umgeleitet …" (the server redirected the connection to another address).
- **No proxy from the environment:** Nodvard Deck always connects directly to the address you entered, also when
  opening the console. A proxy set in the environment of Nodvard Deck (`HTTP_PROXY`, `HTTPS_PROXY` etc.) is never used
  for this, so the address must be directly reachable from the machine running Nodvard Deck.
- **Temporarily switching a server off** without losing the token: Proxmox page → "Connections" tab
  („Verbindungen“), or Proxmox-Backups page → "Manage connections" („Verbindungen verwalten“) → click the "active" („aktiv“) button (it
  changes to "disabled" („deaktiviert“)).

## Certificate: remember the fingerprint

- Proxmox ships with a self-signed certificate. Nodvard Deck normally checks certificates against recognised
  certificate authorities, so with the default certificate this fails ("Das Zertifikat von Proxmox wird nicht
  akzeptiert. …" – the Proxmox certificate is not accepted; with a self-signed certificate, compare its fingerprint
  and remember it under "Manage connections", button "Certificate").
- **Remember the fingerprint:** Proxmox page → "Connections" tab („Verbindungen“; Backups page: "Manage connections")
  → at the connection **"Certificate"** („Zertifikat“). Nodvard Deck only reads the certificate (nothing is sent) and shows
  its fingerprint. Compare it in Proxmox: node → **System** → **Certificates**, column "Fingerprint" (`pveproxy-ssl.pem`
  if the row exists, otherwise `pve-ssl.pem`), or on the server with
  `openssl x509 -noout -fingerprint -sha256 -in /etc/pve/local/pve-ssl.pem`. If it matches, tick the box and press
  **"Remember fingerprint"** („Fingerabdruck merken“). From then on the connection only talks to the server that shows
  exactly this certificate – if it shows another one, the connection fails before the token goes out. "Allow
  self-signed certificate" is switched off at the same time. You can also enter the value by hand (extension settings,
  field "Certificate fingerprint (SHA-256)", with or without colons). The [quick way](#quick-way-connect-with-a-password)
  does this by itself.
- **Certificate changed** (new certificate in Proxmox, e.g. after a reinstall): the connection reports "Der Server
  zeigt ein anderes Zertifikat als das gemerkte …" (the server shows a different certificate than the remembered one)
  and stops. Under "Certificate" you then see the new and the previously remembered fingerprint. Compare the new one
  in Proxmox and press **"Confirm new fingerprint"** („Neuen Fingerabdruck bestätigen“). Only what the server shows at
  that moment is stored. If you did not change the certificate yourself, confirm nothing – another machine may be
  answering at that address. Every confirmation made here is in the log („Proxmox: Fingerabdruck des Zertifikats bestätigt“ or
  „Proxmox-Backups: …“, with old and new fingerprint). If the address of a connection changes, the remembered
  fingerprint is dropped.
- If the field holds something that is not a fingerprint, Nodvard Deck does not connect to the server (it never falls
  back to "do not verify"); the table then shows "Fingerabdruck ungültig" (fingerprint invalid).
- **"Allow self-signed certificate"** (deprecated; shown in the table as „wird nicht geprüft“, not verified) turns the
  check off completely for this connection. It stays for older settings but does not apply while a fingerprint is set.
  The remaining risk with it: someone who gets into your network and impersonates Proxmox could read the token. With a
  remembered fingerprint that is no longer possible.
- A publicly trusted certificate (e.g. Let's Encrypt via Proxmox's ACME feature – requires a domain of your own) is
  checked normally without a fingerprint. Leave the field empty then: Let's Encrypt renews the certificate every few
  weeks, and with a remembered fingerprint you would have to confirm it again each time.

## Proxmox without a subscription

- The API works fully without a subscription; the token has nothing to do with it.
- Without a subscription the **enterprise package repository answers with `401`**. Nodvard Deck
  shows this in the Nodvard Shield update center („Update-Zentrale“) as a notice, not as an error
  ("The Proxmox enterprise package repository requires a subscription (401) …" –
  „Die Proxmox-Enterprise-Paketquelle verlangt ein Abo (401) …“).
- Remedy in Proxmox: node → Updates → Repositories: disable the `enterprise` repositories
  (PVE and Ceph) and add the "No-Subscription" repository via "Add".

## Reference: which call needs which privilege

**"Proxmox VE" extension**

| What Nodvard Deck does | Proxmox call | Privilege |
|---|---|---|
| Check the connection | `GET /version` | none |
| List nodes | `GET /nodes` | none (utilization only with `Sys.Audit`) |
| Node status, utilization, history | `GET /nodes/{n}/status`, `/rrddata` | `Sys.Audit` |
| Disks, SMART and ZFS pools | `GET /nodes/{n}/disks/list`, `/disks/smart`, `/disks/zfs` | `Sys.Audit` (SMART on `/`) |
| Task history, log, result of an action | `GET /nodes/{n}/tasks`, `…/tasks/{upid}/status`, `…/log` | `Sys.Audit` (for tasks started by others, e.g. nightly backups) |
| Installed kernels | `GET /nodes/{n}/apt/versions` | `Sys.Audit` |
| Pending updates | `GET /nodes/{n}/apt/update` | `Sys.Modify` |
| Refresh package lists | `POST /nodes/{n}/apt/update` | `Sys.Modify` |
| Reboot node | `POST /nodes/{n}/status` | `Sys.PowerMgmt` |
| List VMs/containers, status | `GET …/qemu`, `…/lxc`, `…/status/current` | `VM.Audit` (guests without this privilege are simply missing) |
| View configuration, pending changes, snapshots, history | `GET …/config`, `…/pending`, `…/snapshot`, `…/rrddata` | `VM.Audit` |
| IP address of a container | `GET …/lxc/{id}/interfaces` | `VM.Audit` |
| IP address of a VM (guest agent) | `GET …/qemu/{id}/agent/network-get-interfaces` | `VM.GuestAgent.Audit` (PVE 9), `VM.Monitor` (PVE 8) |
| Storage usage | `GET /nodes/{n}/storage` | `Datastore.Audit` per storage (without it: storage invisible) |
| Storage content | `GET …/storage/{s}/content` | `Datastore.Audit`; guest disks additionally `VM.Config.Disk`, backups `VM.Backup` + `Datastore.AllocateSpace` |
| Start, hard power off, reboot | `POST …/status/start`, `/stop`, `/reboot` | `VM.PowerMgmt` |
| Create, delete snapshot | `POST …/snapshot`, `DELETE …/snapshot/{name}` | `VM.Snapshot` |
| Roll back snapshot | `POST …/snapshot/{name}/rollback` | `VM.Snapshot` or `VM.Snapshot.Rollback` (either is enough, B has `VM.Snapshot`) |
| Hardware: cores, sockets | `PUT …/config` | `VM.Config.CPU` |
| Hardware: RAM, balloon, swap | `PUT …/config` | `VM.Config.Memory` |
| Hardware: autostart | `PUT …/config` | `VM.Config.Options` |
| Hardware: start order | `PUT …/config` | `VM.Config.Options`, for VMs additionally `Sys.Modify` on `/` |
| Console | `POST …/vncproxy`, `GET …/vncwebsocket` | `VM.Console` |

**"Proxmox-Backups" extension**

| What Nodvard Deck does | Proxmox call | Privilege |
|---|---|---|
| Show backup jobs | `GET /cluster/backup`, `/cluster/backup/{id}` | `Sys.Audit` on `/` |
| "Not backed up" list („nicht gesichert“) | `GET /cluster/backup-info/not-backed-up` | `Sys.Audit` on `/` |
| Backup runs (including the nightly ones) | `GET /nodes/{n}/tasks?typefilter=vzdump` | `Sys.Audit` |
| Reason for a failed run (plain language) | `GET /nodes/{n}/tasks/{upid}/log` | `Sys.Audit` (without it: only the status of the run) |
| Resolve guests, disk sizes for the space check | `GET /cluster/resources`, `…/config` | `VM.Audit` |
| Backup storage | `GET /nodes/{n}/storage?content=backup` | `Datastore.Audit` |
| Existing backups | `GET …/storage/{s}/content?content=backup` | `VM.Backup` + `Datastore.AllocateSpace` |
| Back up now / retry | `POST /nodes/{n}/vzdump` | `VM.Backup` + `Datastore.AllocateSpace` + `Datastore.Allocate` on the storage (a retention is always sent, `keep-all=1` if the job has none); for jobs with a bandwidth limit or ionice additionally `Sys.Modify` on `/` |
| Create, change a job | `POST /cluster/backup`, `PUT /cluster/backup/{id}` | `Sys.Modify` on `/` + `Datastore.Allocate` on the storage |
| Restore as a copy: check the backup | `GET …/storage/{s}/content?content=backup&vmid=…`, `GET /nodes/{n}/vzdump/extractconfig` | `VM.Backup` + `Datastore.AllocateSpace` |
| Restore as a copy: free number, target storage | `GET /cluster/nextid`, `GET /nodes/{n}/storage?content=images`, `…/storage/{s}/status` | none; `Datastore.Audit` or `Datastore.AllocateSpace` |
| Restore as a copy | `POST /nodes/{n}/qemu` or `/lxc` (always with `pool=nodvard-kopien`, `unique=1` and `onboot=0`, never with `force` or `start`) | `VM.Allocate`, `VM.Config.Options` (containers: also `VM.Config.Network`) on the pool `nodvard-kopien`, `Datastore.AllocateSpace` on the target, reading the backup as above, VE 8 and later `SDN.Use`, privileged containers `Sys.Modify` |
| Restore as a copy: own privileges (also on the pool), version, HA rules | `GET /access/permissions?path=…`, `GET /version`, `GET /cluster/ha/resources` | none (own privileges, version); `Sys.Audit` on `/` for HA |
| Progress of the restore | `GET /nodes/{n}/tasks/{upid}/status`, `GET /nodes/{n}/tasks?typefilter=qmrestore\|vzrestore` | none (own task) |
| Finish the copy (disconnect network cards, note) | `GET`/`PUT /nodes/{n}/qemu\|lxc/{new number}/config` | `VM.Audit`, `VM.Config.Network`, `VM.Config.Options` (they come through the pool) |

## Notes

- **If something fails with `403`:** `pveum user token permissions nodvard@pve dashboard` shows
  which privileges the token really has; the tables above help to find the missing one.
  "Start a backup now / retry" names the required privileges itself on a `403`, and so does restoring as a copy.
- **Proxmox VE 8:** `VM.GuestAgent.Audit` does not exist there yet, and `pveum role add`
  aborts with `invalid privilege 'VM.GuestAgent.Audit'`. In that case simply leave that
  privilege out (`pveversion` shows the version). Better **not** grant the VE 8 counterpart
  `VM.Monitor`: there it also allows running commands inside the VM and setting passwords.
  Enter the VM addresses by hand instead.
- **Backup jobs:** That Proxmox requires `Datastore.Allocate` (not just `AllocateSpace`) on
  the storage applies to VE 8 and 9. Older versions may have been more generous – the
  additional role does no harm there.
