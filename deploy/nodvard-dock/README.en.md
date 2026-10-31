<div align="center">

# Nodvard Dock

**Docker management for your servers: over SSH, without an agent.**

Self-hosted · Source available (PolyForm Noncommercial) · free for personal use

[Deutsch](README.md) · [Getting started](#getting-started) · [Moving from Portainer](#moving-from-portainer) · [Discord](https://discord.gg/gTTs7qMabt)

[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/License-PolyForm%20Noncommercial%201.0.0-blue)](../../LICENSE)
![Status: Beta](https://img.shields.io/badge/Status-Beta-orange)

</div>

> **Language:** The web interface of Nodvard Dock is currently in German only. German labels are quoted below, with
> a translation in brackets where it helps.

---

Nodvard Dock manages the containers and Compose stacks on your servers from one web interface. It talks to every
server over SSH: no agent runs on your servers, and the Nodvard Dock container gets no Docker socket. You start
anything that changes something yourself or release it through an **approval**, and everything is logged. It is made
for homelabs with one or more servers, including people moving away from Portainer CE.

Nodvard Dock is the Docker module of [Nodvard Deck](https://github.com/nodvard/deck), as an image of its own. If you
later want Proxmox, backups or security in the same place, take Nodvard Deck.

> **Status:** Beta. Interfaces and data formats may still change. The ready-made image `ghcr.io/nodvard/dock` is
> available for regular PCs and NAS (amd64) and for the Raspberry Pi (arm64).

## At a glance

| | Nodvard Dock | Portainer CE |
|---|---|---|
| **Further servers** | over SSH with a user of its own, no agent | Portainer Agent, Edge Agent or Docker API per server |
| **Docker socket in the interface container** | no | yes, for the machine Portainer runs on |
| **Where Compose files live** | in a folder on the server (default `/opt/stacks/<name>`) | in Portainer's data volume |
| **Interface language** | German | English |
| **License** | PolyForm Noncommercial: free for personal use, source available | zlib (open source) |

The details on Portainer CE come from its public documentation and license (as of October 2026).

## What it does

- **All containers on one page:** state, start, stop, restart, console and details. Logs with time range, search and
  download. Stopped containers can be deleted.
- **Image updates with a way back:** Nodvard Dock finds new images and applies them to Compose services. The old image
  is kept as a backup. If the new container crashes on start, it goes back to the old one on its own, except for
  databases and other data stores (can be turned off in the module settings).
- **Stacks stay folders on the server:** you adopt existing stacks with one click, nothing changes on the server.
  After that you can apply them, stop them, take them down (volumes stay) and pull their images.
- **Compose editor with checks and history:** edit as text or in a form that only touches the changed spot. Docker
  Compose checks the file on the server before saving, and the latest versions stay in the history.
- **New stacks from templates:** Uptime Kuma, Vaultwarden, Nginx Proxy Manager and Gitea, or a file of your own. Every
  new stack gets a folder of its own (default `/opt/stacks`). Deleting a stack takes it down; Nodvard Dock deletes the
  folder only if you tick that box.
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

**You need** a computer with Docker and Docker Compose, for example a Raspberry Pi 4/5 with a 64-bit system or an
x86-64 machine.

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
- **Update:** `docker compose pull && docker compose up -d`. Your data stays.

### 2. Set up

In the browser, an assistant takes you through three steps:

1. **Administrator-Konto anlegen** (create the administrator account) and pick the time zone.
2. **Was willst du nutzen?** (what do you want to use): switch on the **Docker** module here.
3. **Zwei-Faktor-Anmeldung** (two-factor sign-in): optional, but strongly recommended.

### 3. Connect servers, including this one

Nodvard Dock uses no Docker socket. So it connects to every server over SSH, including the machine it runs on.

1. Under **Einstellungen → Server & Zugänge** (Settings → Servers & access), add the server with the tag `docker`.
   For the machine itself, enter its address in your home network, not `localhost` (that would be the container
   itself).
2. **Mit Passwort einrichten** (set up with password): enter an account with root or sudo rights once. Nodvard Dock
   creates its own user with an SSH key, adds it to the `docker` group and forgets the password.
3. The **Docker** page now shows the containers of this server.

**Its own data volume:** Nodvard Dock has its own volume `nodvard-dock-data`. It cannot take over the data volume of
a Nodvard Deck, because that holds data of modules Dock does not know.

> **Known in this beta:**
> - In some places the interface still calls itself "Nodvard Deck".
> - The setup page shows the command for the setup code with `nodvard-deck`. The right one is
>   `docker compose logs nodvard-dock`.
> - The page Einstellungen → System → Updates (Settings → System → Updates) does not fit Dock yet. Update with the
>   command under "Start".

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

## Security and privacy at a glance

- **No telemetry:** Nodvard Dock sends no usage or crash data anywhere.
- **The only thing that goes out on its own** is the update search: once a day Nodvard Dock asks GitHub for a new
  version, and GitHub sees your IP address and the time (can be turned off under Einstellungen → System → Updates).
  Everything else only goes where you set it up: your servers over SSH and the registries and Git repositories you
  enter. If you turn on the search for new images (off by default), your Docker servers ask the registries of their
  containers. Nodvard Dock contacts a Git repository only when you trigger it.
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
  [first setup, section 6](../../docs/en/FIRST-SETUP.md#6-root-rights-without-a-password-and-the-docker-group).
- **Keys in the vault:** the SSH keys are stored encrypted in Nodvard Dock. Whoever can use Nodvard Dock can control
  your servers. So: a strong password, two-factor on, and do not expose Nodvard Dock to the internet. Behind a
  reverse proxy with HTTPS: [docs/en/REVERSE-PROXY.md](../../docs/en/REVERSE-PROXY.md).
- **Roles:** administrator, operator and viewer decide who may do what.
- **Stacks from Git write with the folder's rights:** Nodvard Dock writes the files with the rights of the stack
  folder's owner. So a container that places a link inside the folder can only get files overwritten where the owner
  may write anyway. If the folder belongs to root, that is the whole machine, and then only the check right before
  each file protects you. So keep stacks in folders of a normal user, not under root.

Please do **not** report security issues publicly; see [SECURITY.md](SECURITY.md).

## Backing up and removing

- **Back up Nodvard Dock itself** under **Einstellungen → System → Sicherung** (Settings → System → Backup):
  encrypted, on a schedule if you like, and as a download. It holds accounts, settings, the vault with the SSH keys
  and the list of adopted stacks. For a folder outside the data volume (another drive, a NAS), uncomment the line
  `- ./sicherungen:/backups` in `compose.yml`; what else is needed is written there.
- **Restore** under **Einstellungen → System → Wiederherstellen** (Settings → System → Restore), or on a fresh
  installation in the first step of the setup assistant with **Oder: Sicherung einspielen** (or: restore a backup).
- **The volume backups taken before a stack update** are kept on the server in question, in the home folder of the
  Nodvard Dock user (default `nodvard`) under `nodvard-sicherungen/<stack>/<time>/`, one `.tar.gz` per volume. Apart
  from root, only that user can read them. Nodvard Dock does not delete them on its own; plan the space for them.
- **Removing:** `docker compose down` in the folder of `compose.yml` stops Nodvard Dock. The data stays in the volume
  `nodvard-dock-data`. Only `docker compose down -v` deletes the data as well, including all stored keys, so back up
  first. Your containers, stacks and volumes on the servers stay as they are either way. The user Nodvard Dock created
  on the servers is yours to remove there if you no longer need it.

## Build it yourself

In a clone of [nodvard/deck](https://github.com/nodvard/deck), preferably on a PC (on a Raspberry Pi the build takes
very long):

```bash
docker build -f deploy/Dockerfile --build-arg NODVARD_DECK_EXTENSIONS=docker -t nodvard-dock:selbst .
```

Then change the `image:` line in `compose.yml` to `nodvard-dock:selbst` and run `docker compose up -d`.

## Source code and bugs

- **Source code:** Nodvard Dock is built from [nodvard/deck](https://github.com/nodvard/deck), the core of Nodvard
  Deck with the module `extensions/docker`.
- **Bugs and ideas:** please open an issue in [nodvard/dock](https://github.com/nodvard/dock/issues).
- **More about Nodvard:** [nodvard.com](https://nodvard.com)

## License

Same as Nodvard Deck: **PolyForm Noncommercial License 1.0.0**, see [LICENSE](../../LICENSE). In short (only the
license text is binding): free for personal and other non-commercial use. Companies and paid services need separate
permission. Inquiries: **kontakt@nodvard.com**. The source code is open to read, but Nodvard Dock is not open source
software in the strict sense.

Docker and Portainer are trademarks of their respective owners. Nodvard Dock is not affiliated with them.
