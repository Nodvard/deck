# Installation per Platform

> The Nodvard Deck interface is currently available in German only; an English interface is planned. UI labels are given in English with the German original in quotes.
>
> Deutsche Fassung: [../13-INSTALLATION.md](../13-INSTALLATION.md)

The short way with three commands is in [first setup, section 1](FIRST-SETUP.md#1-quick-start). This page has every way,
sorted by kind: in a terminal, with a web interface, on a NAS and on particular devices. Each way says how to open the
page, use another port, find the setup code and update. Afterwards the first setup continues with the
[setup wizard](FIRST-SETUP.md#2-setup-wizard-setup).

To connect servers through your tailnet, follow the [Tailscale guide](TAILSCALE.md).
It covers installation on the Deck host and server, the MagicDNS address and SSH access.

If you only have Docker and do not know the repository, you need a single file:
[`deploy/compose.standalone.yml`](../../deploy/compose.standalone.yml). The finished image comes from the GitHub
Container Registry (`ghcr.io/nodvard/deck`, for x86 machines and Raspberry Pi); nothing is built.

- [Good to know, whichever way you pick](#good-to-know-whichever-way-you-pick) and
  [Short version for web interfaces](#short-version-for-web-interfaces)
- [In a terminal](#in-a-terminal): [Docker Compose](#docker-compose-in-a-terminal),
  [`docker run`](#a-single-docker-run-command-no-compose), [Podman](#podman)
- [With a web interface](#with-a-web-interface): [Portainer, way 1](#portainer-way-1-from-the-repository),
  [Portainer, way 2](#portainer-way-2-web-editor), [Dockge](#dockge)
- [On a NAS](#on-a-nas): [Synology](#synology-container-manager-dsm-72-or-newer), [UGREEN](#ugreen-ugos-pro-docker-app),
  [QNAP](#qnap-container-station-3), [Unraid](#unraid), [TrueNAS SCALE](#truenas-scale-2410-or-newer),
  [OpenMediaVault](#openmediavault-compose-plugin), [ZimaOS and CasaOS](#zimaos-and-casaos)
- [Raspberry Pi, Proxmox and Docker Desktop](#raspberry-pi-proxmox-and-docker-desktop): [Raspberry Pi](#raspberry-pi),
  [Proxmox](#proxmox-vm-or-lxc-container), [Docker Desktop](#docker-desktop-windows-macos-for-trying-it-out)
- [After the installation](#after-the-installation)

## Good to know, whichever way you pick

- **First start:** The image (just under 400 MB) is downloaded, then the container creates its database.
  Interfaces such as Portainer or the container section of a NAS show it as "starting" during that time, or
  not yet as "healthy". Up to five minutes of this is normal. If it says "unhealthy" after that, the log
  tells you why.
- **Port:** Inside the container Nodvard Deck answers on port 8080. If 8080 is already taken on the machine,
  change only the number **left** of the colon (`"8081:8080"`); the right-hand side always stays `8080`. Some
  NAS systems use 8080 out of the box; the ways below say which.
- **HTTP and reachability:** By default Nodvard Deck listens on `0.0.0.0:8080` inside the container, meaning
  all IPv4 addresses, and serves unencrypted HTTP. The `"8080:8080"` port mapping in the examples publishes
  that port on all network addresses of the host. For HTTPS you need a [reverse proxy](REVERSE-PROXY.md).
  The following section explains how to restrict direct access.
- **Data:** Everything lives in the Docker volume `nodvard-deck-data`. That is the recommended way on a NAS
  too for new installations. Without a `user:` override, the image starts briefly as root, assigns
  `/app/data` and its contents to **1000:1000**, then switches to that user before migration and the
  application. Existing root-owned data folders therefore keep working after an update. The number does
  not have to belong to a user of the NAS. The supplied Compose files instead set `user: "1000:1000"`
  and `cap_drop: ALL`, which disable automatic ownership changes. If existing data still belongs to root,
  run the commands below once. NAS shares with their own access rules (ACLs) or network drives may also
  need write access for user 1000. If `/app/data` is not writable, startup stops with
  "FEHLER: /app/data ist für Benutzer … nicht beschreibbar." and the repair command. If it works, only user
  1000 and root can read the folder afterwards; file managers or backup tools running as a different user
  can no longer read the files. So back up Nodvard Deck with its built-in backup
  ([first setup, section 6](FIRST-SETUP.md#6-back-up-the-dashboard-itself)).
- **Setup code:** It is in the container's log, inside a frame of `=` characters. The log line is in German
  and starts with "Einrichtungscode" (setup code). Each way says where to find the log.

With `user: "1000:1000"`, migrate existing root-owned data once from the folder containing `compose.yml`:

```bash
docker compose stop nodvard-deck
docker compose run --rm --no-deps --user 0:0 --cap-add CHOWN --cap-add DAC_OVERRIDE --entrypoint /usr/local/bin/migrate-data nodvard-deck
docker compose up -d nodvard-deck
```

The temporary container only changes ownership within `/app/data`. Data and volume names are preserved.

### Restrict access to this host or the LAN

The examples use host port 8080. With Compose, `docker run` and `podman run`, keep your existing host port:
`8081:8080` becomes `127.0.0.1:8081:8080` or `192.168.1.10:8081:8080`. Adjust the browser address or proxy
target too: `http://127.0.0.1:8081` or `http://192.168.1.10:8081`. The container port on the right stays `8080`.

**Reverse proxy running directly on the same host:** Replace the existing list under `ports:` in the
`nodvard-deck` service with this line so that port 8080 is reachable only through `127.0.0.1` on this host:

```yaml
services:
  nodvard-deck:
    ports:
      - "127.0.0.1:8080:8080"
```

The proxy then forwards to `http://127.0.0.1:8080`. If it runs as a container in the same Docker network,
leave out `ports:` for Nodvard Deck entirely and forward to `http://nodvard-deck:8080`. Also enter the
proxy's address as a [trusted proxy](REVERSE-PROXY.md#4-enter-the-address).

**LAN only:** Instead, replace the existing port mapping with the host's LAN address, using
`192.168.1.10` as an example:

```yaml
services:
  nodvard-deck:
    ports:
      - "192.168.1.10:8080:8080"
```

Then open `http://192.168.1.10:8080`. Use your host's actual LAN address, preferably a fixed one. Binding
selects the destination address but does not restrict senders: do not forward port 8080 through your
router, and allow access in the firewall only from your LAN.

Edit the existing Compose file directly, then run `docker compose up -d` in that directory. The snippets
replace only `ports:`; keep the other settings and the data volume. For `docker run` or `podman run`,
replace `-p 8080:8080` with `-p 127.0.0.1:8080:8080` or `-p 192.168.1.10:8080:8080` and recreate the
container as described in the respective instructions.

## Short version for web interfaces

`compose.standalone.yml` reads the port from a variable (`${NODVARD_DECK_PORT:-${LATTICE_PORT:-8080}}`,
the inner part is an [old name](FIRST-SETUP.md#m-old-names-on-older-installations)). Not every interface
understands this nested form. In your own configuration files, always use the current name
`NODVARD_DECK_PORT`. The deployment script `deploy_pi.sh` automatically renames old `LATTICE_*` entries in
`deploy/.env`; when deploying manually, replace old names yourself. For the NAS ways there is this
version without variables; change the port directly in the line under `ports:`:

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

The health check is built into the image, so nothing is missing here.

## In a terminal

### Docker Compose in a terminal

Download the file as `compose.yml` into an empty folder and start it there:

```bash
mkdir ~/nodvard-deck && cd ~/nodvard-deck
curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml
docker compose up -d
```

On Windows these are commands for a Linux shell (also WSL or Git Bash); in PowerShell, write `curl.exe`
instead of `curl` and run the commands one by one.

To check it afterwards, on the same machine:

```bash
curl -s http://localhost:8080/api/v1/health      # expected: "status":"ok"
```

- **Open:** `http://<address-of-the-machine>:8080`, e.g. **http://192.168.1.10:8080**.
- **Port already in use?** (message "port is already allocated" or "address already in use"): put a file
  `.env` with the line `NODVARD_DECK_PORT=8081` next to `compose.yml` and run `docker compose up -d` again.
  The address is then `…:8081`, also for the check above.
- **Setup code:** `docker compose logs nodvard-deck | grep -A1 Einrichtungscode` in the same folder (more
  in [first setup, section 2](FIRST-SETUP.md#2-setup-wizard-setup)). The log line is in German and starts with "Einrichtungscode".
- **Updating:** in the same folder, `docker compose pull && docker compose up -d`. Your data is kept.

### A single `docker run` command (no Compose)

```bash
docker run -d --name nodvard-deck --restart unless-stopped \
  -p 8080:8080 \
  -v nodvard-deck-data:/app/data \
  --log-opt max-size=10m --log-opt max-file=3 \
  ghcr.io/nodvard/deck:latest
```

The volume has the same name as in the Compose way, `nodvard-deck-data`; the health check is built into the image.

- **Open:** `http://<address-of-the-machine>:8080`. Check on the same machine:
  `curl -s http://localhost:8080/api/v1/health` shows `"status":"ok"`.
- **Port already in use?** Write `-p 8081:8080` in the command. If the container is already running, first
  `docker stop nodvard-deck && docker rm nodvard-deck`, then run the command again; the data stays in the volume.
- **Setup code:** `docker logs nodvard-deck 2>&1 | grep -A1 Einrichtungscode`
- **Update:**

  ```bash
  docker pull ghcr.io/nodvard/deck:latest
  docker stop nodvard-deck && docker rm nodvard-deck
  ```

  and then the same `docker run` command as above. Your data is kept.
- **Commands in this guide:** Where it says `docker compose logs nodvard-deck`, use `docker logs nodvard-deck`;
  `docker compose exec nodvard-deck …` becomes `docker exec nodvard-deck …`.

### Podman

Podman does not need Compose. The command is the same as with Docker:

```bash
podman run -d --name nodvard-deck --restart always \
  -p 8080:8080 \
  -v nodvard-deck-data:/app/data \
  ghcr.io/nodvard/deck:latest
```

- **Without root (rootless):** works. A normal user may not open ports below 1024, so 8080 is fine.
- **After the machine reboots:** Podman does not restart containers by itself; the `podman-restart.service` unit
  does, and reliably only for containers with `--restart always` (that is why it says so above instead of
  `unless-stopped`). Enable it once, as root:
  `sudo systemctl enable --now podman-restart.service`; rootless, as your user:
  `systemctl --user enable --now podman-restart.service` plus `loginctl enable-linger $USER`, so the unit also
  starts without a login.
- **With Compose:** `podman compose up -d` in the folder with the `compose.yml` from the terminal way. This only
  works if a Compose program is installed (`docker-compose` or `podman-compose`); `podman compose` just calls it.
  In the `compose.yml`, set `restart: always` instead of `restart: unless-stopped`, otherwise the container does
  not come back after the machine reboots. If it reports an error in the `ports` line, use the
  [short version](#short-version-for-web-interfaces).
- **A folder instead of a volume** on a system with SELinux (Fedora, RHEL and relatives): append `:Z` to the
  line, e.g. `-v ./data:/app/data:Z`.
- **Open:** `http://<address-of-the-machine>:8080`
- **Port already in use?** Write `-p 8081:8080` in the command (remove an existing container first with
  `podman stop nodvard-deck && podman rm nodvard-deck`; the data stays).
- **Setup code:** `podman logs nodvard-deck 2>&1 | grep -A1 Einrichtungscode`
- **Update:** `podman pull ghcr.io/nodvard/deck:latest`, then `podman stop nodvard-deck && podman rm nodvard-deck`
  and the `podman run` command from above once more.
- **Commands in this guide:** replace `docker` with `podman`; `docker compose exec nodvard-deck …` becomes
  `podman exec nodvard-deck …`.

## With a web interface

### Portainer, way 1: from the repository

1. **Stacks → Add stack**, name e.g. `nodvard-deck` (lowercase letters, digits, `-` and `_` only).
2. Choose **Repository** as the build method and enter:
   - **Repository URL:** `https://github.com/nodvard/deck`
   - **Repository reference:** `refs/heads/main`
   - **Compose path:** `deploy/compose.standalone.yml`

   You do not need to log in; the repository is public.
3. **Deploy the stack**.

- **Open:** `http://<address-of-the-machine>:8080`. As a check,
  `http://<address-of-the-machine>:8080/api/v1/health` shows `"status":"ok"` in the browser.
- **Port already in use?** Under **Environment variables**, add the variable `NODVARD_DECK_PORT` with e.g.
  `8081`: for a new stack before "Deploy the stack", for an existing one followed by a redeploy
  ("Pull and redeploy"). The address is then `…:8081`.
- **Setup code:** **Containers** → `nodvard-deck-nodvard-deck-1` (in general
  `<stack-name>-nodvard-deck-1`) → **Logs**; the line starts with "Einrichtungscode".
- **Updating:** in the stack, **Pull and redeploy** with **Re-pull image** switched on. You can optionally
  switch on Portainer's automatic updates ("Automatic updates", in newer versions "GitOps updates"): they
  pick up changes to the Compose file in the repository on their own. A new version of Nodvard Deck,
  however, is a new image and not a change to the file; you get it with "Pull and redeploy".

### Portainer, way 2: web editor

1. Open the file [`deploy/compose.standalone.yml`](../../deploy/compose.standalone.yml)
   ([as plain text](https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml))
   and copy its **entire content**. It starts with `# Nodvard Deck ohne das Repository installieren` and
   contains `services:` further down.
2. **Stacks → Add stack**, name e.g. `nodvard-deck`, choose **Web editor** as the build method, paste the
   content, **Deploy the stack**.

Do **not** paste the three commands from the terminal way (`mkdir …`, `curl …`, `docker compose up -d`).
They are shell commands, not a Compose file; Portainer then reports "top-level object must be a mapping".

- **Open:** `http://<address-of-the-machine>:8080`. As a check,
  `http://<address-of-the-machine>:8080/api/v1/health` shows `"status":"ok"` in the browser.
- **Port already in use?** Under **Environment variables**, add the variable `NODVARD_DECK_PORT` with e.g.
  `8081`: for a new stack before "Deploy the stack", for an existing one followed by "Update the stack".
  The address is then `…:8081`.
- **Setup code:** **Containers** → `nodvard-deck-nodvard-deck-1` (in general
  `<stack-name>-nodvard-deck-1`) → **Logs**; the line starts with "Einrichtungscode".
- **Updating:** open the stack → **Editor** → **Update the stack** with **Re-pull image and redeploy**
  switched on. You do not need to change anything in the editor for that.

### Dockge

1. At the top, **+ Compose**.
2. Under **Stack Name** enter e.g. `nodvard-deck` (lowercase only).
3. Paste the whole content of [`deploy/compose.standalone.yml`](../../deploy/compose.standalone.yml)
   ([as plain text](https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml)) into the
   editor.
4. **Deploy**.

Dockge stores the file as `compose.yaml` in its stacks folder (default `/opt/stacks/nodvard-deck/`); you can also
use `docker compose` there.

- **Open:** `http://<address-of-the-machine>:8080`
- **Port already in use?** In the stack choose **Edit**, enter the line `NODVARD_DECK_PORT=8081` in the **.env**
  field, then **Deploy**. The address is then `…:8081`.
- **Setup code:** On the stack's page the log runs under **Terminal**; the line starts with "Einrichtungscode".
- **Update:** On the stack's page, **Update**. Dockge pulls the new image and restarts the stack; your data is kept.

## On a NAS

Menu names are given as they appear in the vendor's English interface and documentation.

### Synology (Container Manager, DSM 7.2 or newer)

1. Install the **Container Manager** package (from DSM 7.2; there it replaces the old "Docker" package).
2. In Container Manager, **Project** → **Create**.
3. **Project Name** e.g. `nodvard-deck`; for **Path** pick or create an empty folder.
4. For **Source**, create a new `docker-compose.yml` and paste the
   [short version](#short-version-for-web-interfaces).
5. Continue to **Done** (you can skip the web portal step).

- **Open:** `http://<address-of-the-NAS>:8080`. DSM itself uses 5000 and 5001, so 8080 is normally free.
- **Port already in use?** Select the project → **YAML Configurations**, change the left number under
  `ports:` and start the project with the new settings.
- **Setup code:** **Container** → select the Nodvard Deck container → **Action** → **Details** → **Log**.
- **Update:** **Image** → select `ghcr.io/nodvard/deck` → **Action** → **Update**. Settings → About
  Nodvard Deck („Einstellungen → Über Nodvard Deck“) then shows whether the new version is running.
- **Careful with "Clean":** According to Synology this project action also removes the project's volumes,
  i.e. Nodvard Deck's data. Back up first ([first setup, section 6](FIRST-SETUP.md#6-back-up-the-dashboard-itself)).
- **Data:** in the volume `nodvard-deck-data`. Prefer a folder, e.g. `/volume1/docker/nodvard-deck:/app/data`?
  Without a `user:` override, the entrypoint takes over existing root-owned data during the update. With
  `user: "1000:1000"`, stop the project and run the migration commands above over SSH in the project
  folder. If the file is named `docker-compose.yml`, use `docker compose -f docker-compose.yml` in each
  command. If DSM ACLs still prevent access, give user 1000 write access to this folder only.

### UGREEN (UGOS Pro, "Docker" app)

1. Install the **Docker** app from the **App Center**.
2. In the app, **Project** → **Create**.
3. Project name e.g. `nodvard-deck`; keep the suggested storage path.
4. Paste the [short version](#short-version-for-web-interfaces) → **Deploy**.

- **Open:** `http://<address-of-the-NAS>:8080`
- **Port already in use?** Change the left number in the project (not on the container; containers that
  belong to a project cannot be edited there) and **Deploy** again.
- **Setup code:** **Container** → select the Nodvard Deck container → **Log**.
- **Update:** When there is a newer version of the image, UGOS shows it in the project list; update the
  project from there. Your data stays in the volume.
- **Data:** in the volume `nodvard-deck-data`. Prefer a folder? It must exist before **Deploy**, otherwise
  UGOS aborts. A relative path such as `./data:/app/data` ends up in the project's storage path.
  Without a `user:` override, the entrypoint takes over root-owned data automatically during the update.
  With `user: "1000:1000"`, stop the project and run the migration commands above over SSH in the project's
  storage path. Keep the existing folder and its mapping to `/app/data`.

### QNAP (Container Station 3)

Container Station 3 needs QTS 5.1.0 or QuTS hero h5.1.0 or newer and is installed from the **App Center**.

**Important:** The QTS web interface itself uses port **8080**. So pick another, free port, e.g.
`"8090:8080"` instead of `"8080:8080"` in the short version.

1. In Container Station, **Applications** → **Create**.
2. Name e.g. `nodvard-deck` (only `a`–`z`, `0`–`9`, `-` and `_`, at most 32 characters).
3. Paste the [short version](#short-version-for-web-interfaces) with the changed port, optionally
   **Validate**, then **Create**.

- **Open:** `http://<address-of-the-NAS>:8090` (i.e. with the port you entered on the left).
- **Change the port:** **Applications** → the application → menu next to **Edit** → **Recreate**, change the
  left number, **Update**. Recreating keeps only what is in volumes or folders; Nodvard Deck's data is in
  the volume, so it stays.
- **Setup code:** **Containers** → open the Nodvard Deck container → **Logs** tab (not "Event Logs", which is
  Container Station's own log).
- **Update:** **Images** → **Pull**, enter `ghcr.io/nodvard/deck:latest`. Then, as for the port:
  **Recreate** → **Update**.
- **Data:** in the volume `nodvard-deck-data`. Prefer a folder (`/share/<share>/…`)? According to QNAP the
  **Advanced Folder Permissions** (under **Shared Folders**) can interfere with containers. If the warning
  about the ownership appears, check there.

### Unraid

Unraid ships Docker but not Docker Compose. The easiest way is a template of your own:

Before the first start, prepare the data folder as root in the Unraid terminal:

```bash
mkdir -p /mnt/user/appdata/nodvard-deck
chown -R 1000:1000 /mnt/user/appdata/nodvard-deck
chmod 700 /mnt/user/appdata/nodvard-deck
```

1. **Docker** tab → at the very bottom, **Add Container**.
2. **Name** `nodvard-deck`, **Repository** `ghcr.io/nodvard/deck:latest`, **Network Type** `Bridge`.
3. **Add another Path, Port, Variable, Label or Device** → **Config Type** `Port`, **Container Port** `8080`,
   **Host Port** `8080` → **Add**.
4. Again **Add another Path, Port, Variable, Label or Device** → **Config Type** `Path`, **Container Path**
   `/app/data`, **Host Path** `/mnt/user/appdata/nodvard-deck` → **Add**.
5. **Apply**.

- **Open:** `http://<address-of-the-server>:8080`
- **Port already in use?** Click the container's icon → **Edit**, change only the **Host Port** (the
  Container Port stays 8080) → **Apply**.
- **Setup code:** the container's icon → **Logs**.
- **Update:** switch to **Advanced View** at the top right, then **force update** on the container.
- **Data:** in the folder `/mnt/user/appdata/nodvard-deck`. The image starts as root, sets the folder's
  ownership to `1000:1000` once, and then continues without root. If one of Unraid's permission tools resets
  it later, the container sets them again at the next start.
- **Prefer Compose?** The **Compose Manager Plus** plugin (search for it in the **Apps** tab) brings Docker
  Compose. There, under **Docker** → **Compose**, create a stack with **Add Stack**, paste the
  [short version](#short-version-for-web-interfaces) and choose **Compose Up** in the stack's menu; the log is
  under **View Logs**. **Update** updates it; the button only shows when there is a newer version, otherwise it
  is called **Force Update**.

### TrueNAS SCALE (24.10 or newer)

1. **Apps** → **Discover Apps** → ⋮ menu → **Install via YAML**.
2. **Name** `nodvard-deck` (lowercase letters, digits and `-`, starting with a letter).
3. Paste the [short version](#short-version-for-web-interfaces) into **Custom Config** → **Save**.

TrueNAS only checks the YAML syntax, not the content. Use the short version here, not the file with
variables.

- **Open:** `http://<address-of-the-NAS>:8080`. TrueNAS itself uses 80 and 443.
- **Port already in use?** **Apps** → **Installed** → the app → **Edit**, change the left number, save.
- **Setup code:** **Apps** → **Installed** → the app → **View Logs** on the **Workloads** card.
- **Update:** When the app shows **Update available**, choose **Update** in the ⋮ menu of the **Application
  Info** card.
- **Data:** in the volume `nodvard-deck-data`, which lives in the hidden `ix-apps` dataset of the apps pool.
  TrueNAS recommends separate datasets for app data; for that, use the line
  `- /mnt/<pool>/<dataset>:/app/data` instead of `- nodvard_deck_data:/app/data`. If the warning about the
  ownership then appears, give the user with the number 1000 write access under **Datasets** → the dataset →
  **Permissions** card → **Edit**.

### OpenMediaVault ("compose" plugin)

The plugin comes from **omv-extras**. Set up omv-extras, the `openmediavault-compose` plugin and Docker
following the [omv-extras guide](https://wiki.omv-extras.org/). Then:

1. **Services** → **Compose** → **Settings**: under **Compose Files** pick a **Shared folder** (the plugin
   stores its files there) and save.
2. **Services** → **Compose** → **Files** → **Add** → **Add**: **Name** `nodvard-deck`, paste the
   [short version](#short-version-for-web-interfaces) into **File**, **Save**.
3. Select the file in the list → **up**.

- **Open:** `http://<address-of-the-NAS>:8080`
- **Port already in use?** Select the file → **Edit**, change the left number, **Save**, then **up**.
- **Setup code:** Select the file → **Tools** (cog) → **logs**.
- **Update:** Select the file → **pull**, then **up**.
- **Data:** in the volume `nodvard-deck-data`, i.e. in the NAS's Docker storage (default `/var/lib/docker`,
  configurable under **Settings** → **Docker storage**).

### ZimaOS and CasaOS

On import, the interface turns a named volume into a folder mapping and cannot handle the entry under
`volumes:`. So use this version with a fixed folder here:

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

Before the first start, prepare the data folder via SSH or in the host's terminal:

```bash
sudo mkdir -p /DATA/AppData/nodvard-deck
sudo chown -R 1000:1000 /DATA/AppData/nodvard-deck
sudo chmod 700 /DATA/AppData/nodvard-deck
```

1. In the **Apps** area, **+** → **Install a customized app**.
2. At the top, **Import**, tab **Docker Compose**, paste the version above → **Submit**.
3. Check the values it took over → **Install**.

- **Open:** `http://<address-of-the-device>:8080`
- **Port already in use?** In the app's menu, **Settings**, change the left port number, **Save**.
- **Setup code:** In the app's menu, **Settings** → **Terminal and Logs** at the top → **Logs** tab.
- **Update:** In the app's menu, **Check then update**.
- **Data:** in the folder `/DATA/AppData/nodvard-deck`. The image starts as root, sets the folder's
  ownership to `1000:1000` once, and then continues without root. If it is reset later,
  the container sets them again at the next start.

## Raspberry Pi, Proxmox and Docker Desktop

### Raspberry Pi

- A Raspberry Pi 4 or 5 with 4 GB RAM and a **64-bit system** (`uname -m` shows `aarch64`); the image is
  available for `linux/arm64`. When idle, Nodvard Deck needs about 100 MB of memory; the image is just
  under 400 MB. Docker with Docker Compose has to be installed.
- An SD card can die: do not keep backups only on the Pi ([first setup, section 6](FIRST-SETUP.md#6-back-up-the-dashboard-itself));
  a USB SSD lasts longer than an SD card.
- If Portainer runs on the Pi, the two Portainer ways above work the same.

```bash
mkdir ~/nodvard-deck && cd ~/nodvard-deck
curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml
sudo docker compose up -d
curl -s http://localhost:8080/api/v1/health      # expected: "status":"ok"
```

If your user is in the `docker` group, leave out `sudo`. The first start takes a while because the image
has to be downloaded first.

- **Open:** `http://<address-of-the-pi>:8080`
- **Port already in use?** Put a file `.env` with the line `NODVARD_DECK_PORT=8081` next to `compose.yml`
  and run `sudo docker compose up -d` again. The address is then `…:8081`.
- **Setup code:** `sudo docker compose logs nodvard-deck | grep -A1 Einrichtungscode` in the same folder.
- **Updating:** in the same folder, `sudo docker compose pull && sudo docker compose up -d`. Your data is
  kept.

### Proxmox (VM or LXC container)

Docker does not run directly on the Proxmox host but in a VM or an LXC container. Install Docker there
([Docker's guide for Debian](https://docs.docker.com/engine/install/debian/)) and then follow **Docker Compose in a
terminal** above.

- **VM (the safe way):** Proxmox itself recommends a VM for application containers such as Docker when isolation
  and live migration matter. A small Debian VM is enough; Nodvard Deck itself needs about 100 MB of RAM when idle.
- **LXC container:** leaner, but needs two switches. Docker runs in an **unprivileged** container (the default
  when creating one) only with **Nesting** and **keyctl**:
  - **Nesting** is already ticked when you create the container with **Create CT**.
  - Enable **keyctl** afterwards: select the container → **Options** → **Features** → tick **keyctl**.
  - Or in the shell of the Proxmox host: `pct set <CT-ID> --features nesting=1,keyctl=1`
  - Then restart the container.
- **Open, port, setup code, update:** as in the terminal way, inside the VM or container (in the container's
  **Console** or via SSH). The address is that of the VM or container, not that of the Proxmox host.

### Docker Desktop (Windows, macOS) for trying it out

For a test on your own computer. For continuous use a machine that is always on (NAS, Raspberry Pi, small server)
is better.

1. Install and start [Docker Desktop](https://docs.docker.com/desktop/).
2. Open a terminal (Windows: PowerShell, macOS: Terminal) and run:

   ```bash
   mkdir nodvard-deck
   cd nodvard-deck
   curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml
   docker compose up -d
   ```

   In PowerShell write `curl.exe` instead of `curl`.

- **Open:** **http://localhost:8080**
- **Port already in use?** As in the terminal way: a file `.env` with `NODVARD_DECK_PORT=8081` next to
  `compose.yml`, then `docker compose up -d` again.
- **Setup code:** In Docker Desktop, **Containers** on the left → select the Nodvard Deck container → **Logs** tab.
- **Update:** in the same folder `docker compose pull` and `docker compose up -d`.

## After the installation

The database is created or updated automatically when the container starts; there is nothing to do for
that. The setup code (next section) is shown by the wizard together with instructions on where to find it
(command line, Docker Desktop, Portainer, Synology, Unraid).

**All further commands in this guide** (setup code, log, emergency commands, restore) are run in the
folder that contains `compose.yml`, so here after `cd ~/nodvard-deck`. The examples use `sudo docker …`;
if your user is in the `docker` group (or you use Docker Desktop), leave out `sudo`. **With Portainer or a
NAS interface** there is no such folder: you find the log and the restart for the container in the
interface, and you enter commands such as the emergency commands in its console
([first setup, section 7.3](FIRST-SETUP.md#73-locked-out-emergency-commands)). For `docker run` and Podman, each way says what the
commands look like there.
