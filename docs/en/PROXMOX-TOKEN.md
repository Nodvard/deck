# Proxmox API token for Nodvard Deck – with as few privileges as possible

> The Nodvard Deck interface is currently available in German only; an English interface is planned. UI labels are given in English with the German original in quotes.

As of 29 September 2026. The privileges listed here are derived from the Proxmox calls in the Nodvard Deck code
(`extensions/proxmox`, `extensions/backups` – no other extension talks to Proxmox) and
checked against the privilege requirements of the Proxmox API (Proxmox VE 9, double-checked
against VE 8). Special cases for Proxmox VE 8 and older versions are listed under
[Notes](#notes) at the very bottom.

## In brief

- Nodvard Deck talks to Proxmox only through the web API (port 8006) and logs in with an
  **API token**, never with a password.
- Two extensions need the token: **Proxmox VE** (servers, VMs, containers) and
  **Backups** (backup jobs and backups). Each has its own server list and its own token
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

¹ Proxmox only returns the list of pending updates with `Sys.Modify` – even though Nodvard Deck
only reads it. The tile then shows "Not available: … HTTP 403" („Nicht abrufbar: … HTTP 403“).
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

Datacenter → Permissions („Rechenzentrum → Berechtigungen“) – menu names as in the English Proxmox
interface, with the German label in quotes; they can differ slightly depending on the version:

1. **Roles** („Rollen“) → "Create" („Erstellen“): name `NodvardRO` or `NodvardFull`, tick the
   privileges as above.
2. **Users** („Benutzer“) → "Add" („Hinzufügen“): user name `nodvard`, realm "Proxmox VE
   authentication server".
3. **API Tokens** („API-Token“) → "Add": user `nodvard@pve`, token ID `dashboard`, **leave** the
   "Privilege Separation" („Privilegientrennung“) checkbox **ticked**. Copy the secret that is shown
   right away.
4. **Permissions** („Berechtigungen“) → "Add" → "User Permission" („Benutzer-Berechtigung“): path `/`,
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
   - **Allow self-signed certificate** („Selbstsigniertes Zertifikat erlauben“): see the next
     section.
2. Top right: **"Save"** („Speichern“). Only after that does the row
   **"API token secret – pve1"** („API-Token-Geheimnis – pve1“) appear further down under
   "Credentials" („Zugangsdaten“).
3. Paste the secret there → "Save". The badge changes from "Missing" („Fehlt“) to
   "Stored" („Hinterlegt“).
4. Do the same for `pve2`.

Then turn on **Settings → Extensions → "Backups"** („Einstellungen → Erweiterungen → Backups“),
click "Configure" („Konfigurieren“) and, under "Proxmox servers for backups"
(„Proxmox-Server für Backups“), create the same entries – **with the same short names** as in
Proxmox VE. Otherwise the Backups page cannot find the guest names and shows only VM IDs.

Proxmox discovery runs every 5 minutes. After that at the latest, nodes, VMs and containers
show up on the "Proxmox" page and in the "Overview" („Übersicht“).

Good to know:

- **Replacing the token:** in the row "API token secret – …" („API-Token-Geheimnis – …“)
  click "Replace" („Ersetzen“), enter the new value and click "Replace" again. On the
  Proxmox or Backups page (under "Manage connections" („Verbindungen verwalten“)) the button is
  called "Replace token" („Token ersetzen“) when a token exists and "Set token" („Token setzen“)
  when it is missing.
- **Changing the address or removing a connection:** Nodvard Deck then deletes the token secret of
  this connection (also when removing it under "Manage connections" („Verbindungen verwalten“));
  after a new address you enter it again. The same applies to a new short name. A different spelling
  of the same address (upper/lower case in the host name, `:443` with `https://`, trailing `/`) does
  not count as a change.
- **No redirects:** Nodvard Deck never follows redirects when calling Proxmox, not even when opening
  the console. So enter the final address of the node (`https://…:8006`), not a reverse proxy that redirects.
  Otherwise queries and the console fail with an error message.
- **Temporarily switching a server off** without losing the token: Proxmox or Backups page →
  "Manage connections" („Verbindungen verwalten“) → click the "active" („aktiv“) button (it
  changes to "disabled" („deaktiviert“)).

## Certificate: what "Allow self-signed certificate" does

- Proxmox ships with a self-signed certificate. Nodvard Deck normally checks certificates
  strictly, so with the default certificate every connection fails (error with
  `CERTIFICATE_VERIFY_FAILED`).
- The checkbox (on the Proxmox page the column is called "Do not verify certificate"
  („Zertifikat nicht prüfen“))
  **turns the check off completely for exactly this connection**. Nodvard Deck cannot check against
  a certificate fingerprint instead – it is simply on or off.
- On your own network this is fine. The remaining risk: someone who gets into your network and
  impersonates Proxmox could read the token. If you want to leave the checkbox off, Proxmox
  needs a publicly trusted certificate (e.g. Let's Encrypt via Proxmox's ACME feature – that
  requires a domain of your own, e.g. `pve1.example.com`).

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
| Disks and SMART | `GET /nodes/{n}/disks/list`, `/disks/smart` | `Sys.Audit` (SMART on `/`) |
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

**"Backups" extension**

| What Nodvard Deck does | Proxmox call | Privilege |
|---|---|---|
| Show backup jobs | `GET /cluster/backup`, `/cluster/backup/{id}` | `Sys.Audit` on `/` |
| "Not backed up" list („nicht gesichert“) | `GET /cluster/backup-info/not-backed-up` | `Sys.Audit` on `/` |
| Backup runs (including the nightly ones) | `GET /nodes/{n}/tasks?typefilter=vzdump` | `Sys.Audit` |
| Resolve guests, disk sizes for the space check | `GET /cluster/resources`, `…/config` | `VM.Audit` |
| Backup storage | `GET /nodes/{n}/storage?content=backup` | `Datastore.Audit` |
| Existing backups | `GET …/storage/{s}/content?content=backup` | `VM.Backup` + `Datastore.AllocateSpace` |
| Back up now / retry | `POST /nodes/{n}/vzdump` | `VM.Backup` + `Datastore.AllocateSpace` + `Datastore.Allocate` on the storage (a retention is always sent, `keep-all=1` if the job has none); for jobs with a bandwidth limit or ionice additionally `Sys.Modify` on `/` |
| Create, change a job | `POST /cluster/backup`, `PUT /cluster/backup/{id}` | `Sys.Modify` on `/` + `Datastore.Allocate` on the storage |

## Notes

- **If something fails with `403`:** `pveum user token permissions nodvard@pve dashboard` shows
  which privileges the token really has; the tables above help to find the missing one.
  "Start a backup now / retry" names the required privileges itself on a `403`.
- **Proxmox VE 8:** `VM.GuestAgent.Audit` does not exist there yet, and `pveum role add`
  aborts with `invalid privilege 'VM.GuestAgent.Audit'`. In that case simply leave that
  privilege out (`pveversion` shows the version). Better **not** grant the VE 8 counterpart
  `VM.Monitor`: there it also allows running commands inside the VM and setting passwords.
  Enter the VM addresses by hand instead.
- **Backup jobs:** That Proxmox requires `Datastore.Allocate` (not just `AllocateSpace`) on
  the storage applies to VE 8 and 9. Older versions may have been more generous – the
  additional role does no harm there.
