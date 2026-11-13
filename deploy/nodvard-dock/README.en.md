<div align="center">

# Nodvard Dock

**Docker management for your servers: over SSH, without an agent.**

Self-hosted, source available (PolyForm Noncommercial), free for personal use

[Deutsch](README.md) · [Getting started](#getting-started) · [Documentation](../../docs/en/dock/README.md) · [Moving from Portainer](#moving-from-portainer) · [Discord](https://discord.gg/gTTs7qMabt)

[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/License-PolyForm%20Noncommercial%201.0.0-blue)](../../LICENSE)
![Status: Beta](https://img.shields.io/badge/Status-Beta-orange)

</div>

> **Language:** The web interface of Nodvard Dock is currently in German only. German labels are quoted below, with
> a translation in brackets where it helps.

---

## What Dock is: the difference from Deck in five sentences

Nodvard Dock is the Docker edition of [Nodvard Deck](../../README.en.md), built with `EDITION=dock` and `NODVARD_DECK_EXTENSIONS=docker`.
Dock provides only Docker management for containers, images, volumes, networks and Compose stacks.
It connects to Docker hosts over SSH, without an agent on the hosts or a Docker socket in the Dock container.
The shared core provides accounts, the vault, approvals, the audit log and backups of Dock settings.
Server modules such as Proxmox, Server-Zustand and Nodvard Shield belong to Deck and are not included in Dock.

The [edition overview with code references](../../docs/en/dock/README.md#edition-and-scope) also explains the fixed module selection and update method.

> **Status:** Beta. Interfaces and data formats may still change. The ready-made image `ghcr.io/nodvard/dock` is
> available for regular PCs and NAS (amd64) and for the Raspberry Pi (arm64).

## What it does

- **All containers on one page:** state, start, stop, restart, console and details. Logs with time range, search and
  download. Stopped containers can be deleted.
- **Image updates with a way back:** Nodvard Dock finds new images and applies them to Compose services. The old image
  is kept as a backup. If the new container crashes on start, Dock attempts automatic rollback, except for
  detected databases and other data stores (can be turned off in the module settings).
- **Stacks stay folders on the server:** you adopt existing stacks with one click, nothing changes on the server.
  After that you can apply them, stop them, take them down (volumes stay) and pull their images.
- **Compose editor with checks and history:** edit as text or in a form that only touches the changed spot. Docker
  Compose checks the file on the server before saving, and the latest versions stay in the history.
- **New stacks from templates:** Uptime Kuma, Vaultwarden, Nginx Proxy Manager and Gitea, or a file of your own. A
  Compose file can also be saved as a template of your own: the file only, without `.env` and without hard-coded
  passwords. Every new stack gets a folder of its own (default `/opt/stacks`). Deleting a stack takes it down; Nodvard
  Dock deletes the folder only if you tick that box.
- **Stack updates with a backup:** the old images are kept, and "Zurück zur vorigen Version" (back to the previous
  version) is ready afterwards. On request Nodvard Dock backs up the volumes first, after checking the free space; you
  can restore or delete them in the Stacks tab under "Sicherungen" (backups).
- **Stacks from Git:** a stack gets a Git repository as its source (`https://` only, private ones too). Before applying
  you see which files are new or would change. Nodvard Dock never replaces your `.env` on the server.
- **Turn `docker run` into a stack:** a container started on its own becomes a stack with a Compose file. If the new
  one does not run, the old one comes back. If it runs, Nodvard Dock deletes the old one; anything kept only inside
  the container is gone then.
- **Images, volumes and networks:** view them and delete them one by one; clean up unused images and the build cache
  in one go.
- **Private registries:** the password is kept in the vault. Logging in happens per server through approval, and the
  password never appears on the command line.
- **Approval and audit log:** every change to containers, stacks, images, volumes and networks is an action with a
  risk level. The owner decides what runs without asking.

<details>
<summary><b>Stacks from Git: what exactly happens when applying</b></summary>

- Nodvard Dock fetches the state and shows the comparison first. Applying goes through approval.
- Nodvard Dock never replaces environment files on the server (`.env` and those named when adopting).
- On its own it restarts only the services named in the approval. Services whose settings change in the Compose file
  are recreated by Compose anyway when it applies the stack; the comparison shows this beforehand.
- If other services may still read the old state, the result names them with a "Jetzt neu starten" (restart now)
  button.
- Right after applying, "Zurück zum Stand davor" (back to the previous state) is available. It only brings back the
  files, not the data.
- On request Nodvard Dock first backs up the stack's volumes as for a stack update. The services using them stay
  stopped until Compose applies the stack. Restoring is done in the Stacks tab under "Sicherungen" (backups).

</details>

## Getting started

### Quick start in 5 minutes

**You need** a computer with Docker and Docker Compose, for example a Raspberry Pi 4/5 with a 64-bit system or an
x86-64 machine, a Linux Docker host reachable over SSH and a prepared HTTPS Git repository containing a Compose file.
The five minutes assume Docker, SSH and the repository are ready; downloading the image may take longer.
The commands use a Linux shell; in PowerShell use `curl.exe` and run each command separately.

### 1. Start

```bash
mkdir ~/nodvard-dock && cd ~/nodvard-dock
curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/nodvard-dock/compose.yml
docker compose up -d
```

- **Open:** `http://<your-server>:8080`
- **Setup code:** `docker compose logs nodvard-dock` in the same folder, the line starts with "Einrichtungscode".
- **Port already in use?** Put a file `.env` next to `compose.yml` with the line `NODVARD_DOCK_PORT=8081` and run
  `docker compose up -d` again.

### 2. Set up and first login

In the browser, enter the setup code from the container log. There is no default password.
Setup covers the account, time zone and further settings:

1. **Administrator-Konto anlegen** (create the administrator account): choose a username and your own password.
   This signs you in.
2. **Zeitzone** (time zone): choose the time zone. Dock then enables the **Docker** module automatically.
3. **Zwei-Faktor-Anmeldung (optional)** (two-factor sign-in): optional, but strongly recommended.
4. **Aussehen (optional)** (appearance): adjust the product name, short name and accent color, or skip this step.
5. **Fertig** (done): choose **Weiter zum Dashboard** to open Dock.

If enabling Docker fails, an extra module step offers a retry.
Use the account you created for later sign-ins.

### 3. Connect servers, including this one

Nodvard Dock uses no Docker socket. So it connects to every server over SSH, including the machine it runs on.

1. Under **Einstellungen → Server & Zugänge** (Settings → Servers & access), add the server with the tag `docker`.
   For the machine itself, enter its address in your home network, not `localhost` (that would be the container
   itself).
2. **Mit Passwort einrichten** (set up with password): enter an account with root or sudo rights once. Nodvard Dock
   creates its own user with an SSH key, adds it to the `docker` group and forgets the password.
3. The **Docker** page now shows the containers of this server.

### 4. Deploy your first stack from Git

1. Open **Docker → Stacks → Neuer Stack** (new stack), choose a name such as `meine-app` and paste the Compose file
   from your repository. For **Ordner für neue Stacks** (folder for new stacks), first choose **Ändern** (change) →
   **Im Heimatordner** (in the home folder) → **Übernehmen** (save). This uses the SSH user's writable home folder
   instead of `/opt/stacks`. Then choose **Prüfen** (check), then **Anlegen** (create) and confirm the approval. This does not
   start the stack yet. For this first attempt, use a Compose file that needs no additional files.
2. Open **Aus Git** (from Git) on the stack. Enter the repository HTTPS URL, **Branch oder Tag** (branch or tag,
   such as `main`) and the Compose file path (such as `compose.yaml`), then choose **Eintragen und holen** (save and fetch).
3. Review the file comparison. If all files match, start the stack with **Anwenden** (apply); otherwise choose
   **Einspielen** (apply from Git). Confirm the approval and check the result under **Aktionen** (actions);
   the stack should then appear under **Stacks** with its containers.

For private repositories, first store a token under **Einstellungen → Erweiterungen → Docker → Erweitert → Git-Zugänge**
(Settings → Extensions → Docker → Advanced → Git access). Details and limits: [Stacks from Git](../../docs/en/dock/stacks-from-git.md).

**Its own data volume:** Nodvard Dock has its own volume `nodvard-dock-data`. It cannot take over the data volume of
a Nodvard Deck, because that holds data of modules Dock does not know.

> **Known in this beta:** If the title or sign-in page still says "Nodvard Deck", an older beta saved that name:
> change it under Einstellungen → Darstellung (Settings → Appearance).

## Moving from Portainer

1. **Run both side by side:** set up Nodvard Dock while Portainer keeps running. Nodvard Dock changes nothing unless
   you trigger it, and once set up it shows all containers, including the ones from Portainer.
2. **Adopt the stacks:** stacks from a folder on the server are adopted in the **Stacks** tab with **Übernehmen**
   (adopt). Stacks created by Portainer itself carry the badge "Portainer". **Von Portainer übernehmen** (move from
   Portainer) fetches the file and variables from Portainer's data volume, puts them in a folder of their own
   (default `/opt/stacks/<name>`) and redeploys the stack under the same name. The volumes stay the same.
3. **Turn Portainer off** once all stacks have moved. Your other containers keep running.

**Important:** do not delete a moved stack in Portainer and do not redeploy it there. It has the same name, and
Portainer would remove the new containers or replace them with its old file. Just leave the entry as it is.

**When Nodvard Dock does not move a stack:** if the file points to data at Portainer with relative paths, consists of
several files (for example from Git) or does not match the running services. Nodvard Dock then says why, for relative
paths also where the data is.

## Difference to Deck

| Feature | Nodvard Dock | Nodvard Deck |
|---|---|---|
| Purpose | Docker and Compose management | Dashboard for more areas of your homelab |
| Modules in the official image | Only `docker` | Additional modules, such as Proxmox, Backups, Server-Zustand and Shield |
| Edition | `EDITION=dock` | `EDITION=deck` |
| Image | `ghcr.io/nodvard/dock` | `ghcr.io/nodvard/deck` |
| Data volume in the supplied Compose file | `nodvard-dock-data` | `nodvard-deck-data` |
| Setup | Enable Docker automatically | Choose the modules you need |

Use [Nodvard Deck](https://github.com/nodvard/deck) if you want additional modules.
Keep a separate data volume for each installation. This guide does not assume a shared volume or automatic
data migration when switching editions.

## Security and privacy at a glance

Dock starts as `1000:1000`. Compose sets `read_only`, a tmpfs at `/tmp`, `cap_drop: ALL` and
`no-new-privileges`. Port 8080 and management over SSH need no additional capabilities.
A fresh `nodvard-dock-data` volume gets its permissions from the image. Bind mounts must belong to UID/GID 1000
before starting. For existing installations with different ownership, create a backup, pull the new image and
run this once with Dock stopped:

```bash
docker compose stop nodvard-dock
docker compose run --rm --no-deps --user 0:0 --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --entrypoint /usr/local/bin/migrate-data nodvard-dock
docker compose up -d nodvard-dock
```

Keep volume names and bind mounts, remove old root overrides before restarting, and apply the current Compose
security settings. Only the migration needs `CHOWN` to change ownership and `DAC_OVERRIDE` to traverse protected
folders. It does not follow links and changes only `/app/data` on its filesystem. No other container may use that
folder during migration. On NAS shares without `chown` support, grant `1000:1000` access on the NAS.

- **No telemetry by default:** Nodvard Dock sends no usage or crash data by default.
- **By default, the only automatic outgoing connection** is the update search: once a day Nodvard Dock asks `ghcr.io` for a new
  version, and the registry sees your IP address and the time (can be turned off under Einstellungen → System → Updates).
  Other connections go to the configured servers over SSH, registries and Git repositories. If you turn on image
  checks (off by default), your Docker servers contact their containers' registries. The optional scheduled Git check
  is also off by default; it reports new commits and does not apply them. Without a schedule, Dock contacts the Git
  repository when you click.
- **No Docker socket in the container:** whoever has the socket is root on the machine, and that would be anyone who
  gets into the interface. So Nodvard Dock works over SSH with a user of its own.
- **The `docker` group is as good as root:** whoever controls Docker can mount any folder of the machine into a
  container. This is true for every tool of this kind, Portainer included.
- **"Operate only" mode without the group:** for servers tagged `docker-bedienen`, a dedicated helper script allows
  only viewing, logs, start, stop and restart through sudo. Stacks, updates and the console are not available there.
  A `docker` group membership set up earlier stays in place, though, and so does a full sudo rule until you take it
  back during setup with "Volle sudo-Regel entfernen" (remove the full sudo rule) under "Erweitert" (advanced).
  "Verbindung prüfen" (check connection) shows this, as well as a login as `root`. `sudo gpasswd -d <user> docker`
  takes the login out of the group; the rest is in
  [guide to root rights](../../docs/en/ROOT-RIGHTS.md#switching-docker-to-operate-only).
- **Keys in the vault:** the SSH keys are stored encrypted in Nodvard Dock. Whoever can use Nodvard Dock can control
  your servers. So: a strong password, two-factor on, and do not expose Nodvard Dock to the internet. Behind a
  reverse proxy with HTTPS: [docs/en/REVERSE-PROXY.md](../../docs/en/REVERSE-PROXY.md).
- **Roles:** administrator, operator and viewer decide who may do what.
- **Stacks from Git write with the folder's rights:** Nodvard Dock writes the files with the rights of the stack
  folder's owner. So a container that places a link inside the folder can only get files overwritten where the owner
  may write anyway. If the folder belongs to root, that is the whole machine, and then only the check right before
  each file protects you. So keep stacks in folders of a normal user, not under root.

Please do **not** report security issues publicly; see [SECURITY.md](SECURITY.md).

## Updating Dock

Before updating, download a Dock backup and read the release notes. In the folder containing `compose.yml`:

```bash
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail=100 nodvard-dock
```

Short form: `docker compose pull && docker compose up -d`. The data volume `nodvard-dock-data` is preserved.
Einstellungen → System → Updates (Settings → System → Updates) shows available Dock versions. Dock uses neither the
Deck update helper nor `scripts/deploy_pi.sh`. A container image alone does not undo a database migration; keep
the backup and its matching image version for recovery.
Updating your applications is covered separately: [Image updates](../../docs/en/dock/image-updates.md).

## Backup and restore

- **Back up Nodvard Dock itself** under **Einstellungen → System → Sicherung** (Settings → System → Backup):
  encrypted, on a schedule if you like, and as a download. It holds accounts, settings, the vault with the SSH keys
  and the list of adopted stacks. For a folder outside the data volume (another drive, a NAS), uncomment the line
  `- ./sicherungen:/backups` in `compose.yml`; what else is needed is written there.
- **Restore** under **Einstellungen → System → Wiederherstellen** (Settings → System → Restore), or on a fresh
  installation in the first step of the setup assistant with **Sicherung einspielen** (restore a backup).
- **Verify recovery:** keep the backup file and its backup password or recovery key outside the Dock volume.
  A download encrypted with a one-time password requires that exact password. After restoring,
  sign in with the restored account, check host connections and the stack list. Dock backups do not contain
  application data from Docker hosts; back up their volumes and bind mounts separately. Details on encrypted
  backups: [Backup guide](../../docs/en/BACKUP.md).
- **The volume backups taken before a stack update** are kept on the server in question, in the home folder of the
  Nodvard Dock user (default `nodvard`) under `nodvard-sicherungen/<stack>/<time>/`, one `.tar.gz` per volume. Apart
  from root, only that user can read them. Nodvard Dock does not delete them on its own; plan the space for them.
- **Removing:** `docker compose down` in the folder of `compose.yml` stops Nodvard Dock. The data stays in the volume
  `nodvard-dock-data`. Only `docker compose down -v` deletes the data as well, including all stored keys, so back up
  first. Your containers, stacks and volumes on the servers stay as they are either way. The user Nodvard Dock created
  on the servers is yours to remove there if you no longer need it.

## Limits

- Beta, interface currently in German only; no server modules or free module selection in Dock.
- Every Docker host needs SSH and Docker Compose for stacks. The `docker-bedienen` tag allows only viewing,
  logs, start, stop and restart, without stacks, image updates or the console.
- Git supports HTTPS with a branch or tag, without SSH Git URLs, submodules or Git LFS files.
  The optional check reports new commits; applying them needs your action and approval.
- An image rollback or Git rollback does not restore application data. Automatic image rollback is excluded
  for detected databases and data stores and does not replace a backup.
- The `docker` group grants extensive host privileges. Dock backups and application backups are separate tasks.
- The image check compares the digest under the same tag and does not look for higher version tags. Automatic
  rollback does not apply to a bare `unhealthy` status or to detected databases and data stores such as Redis or MinIO.
- Bind mounts, that is data in host folders, are not included in the volume backup before a stack update.
- Logs load at most 5000 lines, and the search covers only those. The Compose editor history keeps up to 20 older
  versions per stack.

Further reading: [Stacks from Git](../../docs/en/dock/stacks-from-git.md), [Image updates](../../docs/en/dock/image-updates.md),
[Templates](../../docs/en/dock/templates.md), [Troubleshooting](../../docs/en/dock/troubleshooting.md).

## Build it yourself

In a clone of [nodvard/deck](https://github.com/nodvard/deck), preferably on a PC (on a Raspberry Pi the build takes
very long):

```bash
docker build -f deploy/Dockerfile --build-arg NODVARD_DECK_EXTENSIONS=docker --build-arg EDITION=dock -t nodvard-dock:selbst .
```

`EDITION=dock` sets the edition, `NODVARD_DECK_EXTENSIONS=docker` the module selection.
Then change the `image:` line in `compose.yml` to `nodvard-dock:selbst` and run `docker compose up -d`.

## Source code and bugs

- **Source code:** Nodvard Dock is built from [nodvard/deck](https://github.com/nodvard/deck), the core of Nodvard
  Deck with the module `extensions/docker`.
- **Bugs and ideas:** please open an issue in [nodvard/dock](https://github.com/nodvard/dock/issues).
- **More about Nodvard:** [nodvard.com](https://nodvard.com)

## Contributing

Report bugs, feature requests and unclear documentation using the [issue templates](https://github.com/nodvard/dock/issues).
Include the version, your installation method and steps to reproduce.
External pull requests or patches are not accepted. Describe suggestions in words in an issue.
Private modifications and builds are allowed under the PolyForm Noncommercial License terms.
See [CONTRIBUTING.md](../../CONTRIBUTING.md) for the policy.

## Support

For setup questions and community discussion, visit the help forum on
[Discord](https://discord.gg/gTTs7qMabt). More about Nodvard: [nodvard.com](https://nodvard.com).

## License

Same as Nodvard Deck: **PolyForm Noncommercial License 1.0.0**, see [LICENSE](../../LICENSE). In short (only the
license text is binding): free for personal and other non-commercial use. Companies and paid services need separate
permission. Inquiries: **kontakt@nodvard.com**. The source code is available to read.

Docker and Portainer are trademarks of their respective owners. Nodvard Dock is not affiliated with them.
