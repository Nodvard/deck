# First-Time Setup – Step by Step

> The Nodvard Deck interface is currently available in German only; an English interface is planned. UI labels are given in English with the German original in quotes.
>
> Deutsche Fassung: [../11-ERST-EINRICHTUNG.md](../11-ERST-EINRICHTUNG.md)

As of version 0.12.

From zero to a running dashboard in a few minutes. All you need is a machine with Docker that keeps running: a Raspberry
Pi, a NAS, a small server or a VM. Sections 1 to 7 walk you through the setup in this order. Technical details, special
cases and everything for looking things up are in the [appendix](#appendix) and in separate guides.

In the dashboard, **"Guide"** („Anleitung“) opens the German version of this page in a new tab: at the bottom of the
settings navigation and via the search (Ctrl+K, „Anleitung“, „Hilfe“ or „Doku“). Many places link straight to the
matching section.

<!-- The <a id="…"></a> markers keep links from older versions working (they link to this file on main). Do not remove. -->

**Setting up**

1. [Quick start](#1-quick-start): start Nodvard Deck with three commands
2. [Setup wizard](#2-setup-wizard-setup): create the account, time zone, modules
3. [Connect your first server](#3-connect-your-first-server): add a server, SSH access, check the connection
4. [Enable extensions](#4-enable-extensions): a good start, with or without [Proxmox](#42-connect-proxmox)
5. [Push notifications (ntfy)](#5-push-notifications-ntfy): messages on your phone
6. [Back up the dashboard itself](#6-back-up-the-dashboard-itself): encrypted and on a schedule
7. [Common stumbling blocks](#7-common-stumbling-blocks), including
   [Locked out? Emergency commands](#73-locked-out-emergency-commands)

**Looking things up:** [Appendix](#appendix) A to M and the separate guides
[Installation per platform](INSTALLATION.md), [Root rights and helper scripts](ROOT-RIGHTS.md),
[Synology DSM](SYNOLOGY-DSM.md), [Backups in detail](BACKUP.md), [Without the interface](WITHOUT-INTERFACE.md),
[Proxmox token](PROXMOX-TOKEN.md) and [Reverse proxy](REVERSE-PROXY.md).

## 1. Quick start

<a id="1-start-the-container"></a>

Nodvard Deck runs as a Docker container. The finished image comes from the GitHub Container Registry
(`ghcr.io/nodvard/deck`, for x86 machines and Raspberry Pi); nothing is built. On a machine with Docker and Docker
Compose it takes three commands:

```bash
mkdir ~/nodvard-deck && cd ~/nodvard-deck
curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml
docker compose up -d
```

Then open **`http://<address-of-the-machine>:8080`** in your browser, e.g. **http://192.168.1.10:8080** (on the same
machine **http://localhost:8080**). The setup wizard is waiting there ([section 2](#2-setup-wizard-setup)).
It first asks for a setup code from the container's log; [2.1](#21-finding-the-setup-code) shows where to find it.

- **First start:** The image (just under 400 MB) is downloaded, then the container creates the database. Up to five
  minutes is normal. To check on the same machine: `curl -s http://localhost:8080/api/v1/health` shows
  `"status":"ok"`.
- **No Docker yet?** [Docker's own guide](https://docs.docker.com/engine/install/) shows how to install it on your
  system; Docker Compose is part of it.
- **"permission denied" on Linux:** Put `sudo` in front of `docker`. If your user is in the `docker` group, it works
  without. The examples in this guide write `sudo docker …`.
- **Windows or macOS for trying it out:** install and start [Docker Desktop](https://docs.docker.com/desktop/), then
  enter the three commands in a terminal (Windows: PowerShell, macOS: Terminal). In PowerShell write `curl.exe` instead
  of `curl` and run the commands one by one. For continuous use a machine that is always on is better.
- **Port 8080 already taken?** (message "port is already allocated" or "address already in use"): Put a file `.env`
  with the line `NODVARD_DECK_PORT=8081` next to `compose.yml` and run `docker compose up -d` again. The address is
  then `…:8081`, also for the check above.
- **Updating:** in the same folder `docker compose pull && docker compose up -d`. Your data is kept
  ([appendix I](#i-updates)).

All further commands in this guide (setup code, log, emergency commands, restore) are run in the folder with
`compose.yml`, here after `cd ~/nodvard-deck`.

### 1.1 Other ways: NAS, Portainer and more

<a id="11-installation-without-the-repo"></a>
<a id="in-a-terminal"></a>
<a id="with-a-web-interface"></a>
<a id="on-a-nas"></a>
<a id="raspberry-pi-proxmox-and-docker-desktop"></a>

No terminal at hand, or do you already run an interface for containers? Each of these ways has a step-by-step guide of
its own in **[Installation per platform](INSTALLATION.md)**, each with opening the page, another port, the setup code
and updating:

| You have … | Guide |
|---|---|
| a web interface for containers | [Portainer](INSTALLATION.md#portainer-way-1-from-the-repository), [Dockge](INSTALLATION.md#dockge) |
| a NAS | [Synology](INSTALLATION.md#synology-container-manager-dsm-72-or-newer), [UGREEN](INSTALLATION.md#ugreen-ugos-pro-docker-app), [QNAP](INSTALLATION.md#qnap-container-station-3), [Unraid](INSTALLATION.md#unraid), [TrueNAS SCALE](INSTALLATION.md#truenas-scale-2410-or-newer), [OpenMediaVault](INSTALLATION.md#openmediavault-compose-plugin), [ZimaOS and CasaOS](INSTALLATION.md#zimaos-and-casaos) |
| a particular device | [Raspberry Pi](INSTALLATION.md#raspberry-pi), [Proxmox (VM or LXC)](INSTALLATION.md#proxmox-vm-or-lxc-container), [Docker Desktop](INSTALLATION.md#docker-desktop-windows-macos-for-trying-it-out) |
| Docker without Compose, or Podman | [`docker run`](INSTALLATION.md#a-single-docker-run-command-no-compose), [Podman](INSTALLATION.md#podman) |

With Portainer or a NAS interface there is no folder with `compose.yml`: you find the log and restart with the
container in the interface, and you enter commands such as the emergency commands in its console
([7.3](#73-locked-out-emergency-commands)). Data in a folder of your own and building it yourself:
[appendix A](#a-installation-in-detail).

### Secure your connection

Access through `http://<computer-IP>:8080` is unencrypted. Someone on your home network could read your
login token. A warning therefore appears in step 1 of setup and under **Settings → System → Trusted
proxy** („Einstellungen → System → Vertrauenswürdiger Proxy“). You can dismiss it for the session.
It returns at the next login while you still use HTTP. It does not appear with HTTPS or when accessing
Deck through `localhost`, `127.0.0.1` or `::1`.

There are two ways to protect the connection:

- **HTTPS through a reverse proxy:** Set up a certificate and HTTPS with **Nginx Proxy Manager** or
  **Caddy**, then access Deck only through the HTTPS address. Add your proxy's address under
  **Settings → System → Trusted proxy** (`system.trusted_proxies`) so Deck recognises its HTTPS
  information. The [reverse proxy guide](REVERSE-PROXY.md) covers both options. Restrict direct access
  to port 8080 to the proxy so other devices cannot keep using the unencrypted route.
- **Access only through Tailscale or a VPN:** Allow access to Deck exclusively through the encrypted
  network and block direct connections to port 8080 from your home network and the internet. Then
  use the Tailscale or VPN address. When using HTTP inside the tunnel, the warning remains visible
  because Deck cannot detect the VPN protection.

## 2. Setup wizard (/setup)

As long as there is no user yet, the login page automatically redirects to `/setup`. The wizard has six steps; only the
first is mandatory, everything else can be skipped and done later in the settings.

**Already have a backup?** The first step offers "Restore a backup" („Sicherung einspielen“): upload the
file, enter the password, check the summary, restore – after that you log in with the account from the
backup and do not need to set anything up again ([6.2](#62-restore-import-a-backup)). The setup code (below) is
required there as well.

### 2.1 Finding the setup code

So that not just anyone on the network who opens the page first gets the first account (and with it the owner role),
`/setup` asks for a **setup code** („Einrichtungscode“). Nodvard Deck generates it at startup and writes it to the
**container's log**: in a frame of `=` characters, the code additionally on a line of its own, e.g. `K7MQ-X2VD-H9PA`.
Upper/lower case, dashes and spaces do not matter when you type it. In the wizard, "Where do I find the code?" („Wo
finde ich den Code?“) unfolds the same help.

- **In a terminal** (in the folder with `compose.yml`):

  ```bash
  sudo docker compose logs nodvard-deck | grep -A1 Einrichtungscode
  # Einrichtungscode: K7MQ-X2VD-H9PA – im Browser eingeben
  ```

  (The log line is in German: "Einrichtungscode" = setup code, "im Browser eingeben" = enter in the browser.) Windows
  PowerShell has no `grep`; there use
  `docker compose logs nodvard-deck | Select-String -Context 0,4 Einrichtungscode`. It also works without any filter:
  `docker compose logs nodvard-deck` shows the whole log, and the code is in the frame.
- **Docker Desktop:** on the left **Containers** → select the Nodvard Deck container → **Logs** tab.
- **Portainer:** **Containers** → `nodvard-deck-nodvard-deck-1` (in general `<stack-name>-nodvard-deck-1`) → **Logs**.
- **Synology Container Manager:** **Container** → select the Nodvard Deck container → **Action** → **Details** →
  **Log**.
- **Unraid:** container icon → **Logs**.
- **Other NAS systems and interfaces:** see the matching way in [Installation per platform](INSTALLATION.md).

The code stays the same across restarts and disappears once the first account has been created. After a restart the
same code appears once more further down in the log.

**Choosing the code yourself:** If you would rather set the code (at least 8 characters), add it to the Compose file as
**`NODVARD_DECK_SETUP_CODE: "…"`** under `environment:`. The `compose.yml` from [section 1](#1-quick-start) has no
`environment:` block yet; add one under `nodvard-deck:` (same indentation as `image:`) and apply it with
`docker compose up -d`:

```yaml
    environment:
      NODVARD_DECK_SETUP_CODE: "my-own-setup-code"
```

The repository's `deploy/docker-compose.yml` already has the block. Without this setting Nodvard Deck generates one
itself. More about the code (failed attempts, where it is stored): [appendix B.1](#b1-the-setup-code).

### 2.2 The six steps

1. **Create the administrator account** (mandatory): setup code, username (at least 3 characters, only lowercase
   letters, digits and `.`, `-` and `_`, e.g. `admin`) and a password with at least 8 characters. This first account is
   the **owner** („Inhaber“). The owner may always do everything, even if roles are changed later – so you cannot lock
   yourself out.
2. **Time zone:** The time zone of your device is preselected. Schedules (such as the morning briefing) and maintenance
   windows follow it.
3. **What do you want to use?** Switch modules on with a click; none is mandatory. Which ones are worth it for a start
   is in [section 4](#4-enable-extensions). Modules marked "Needs more details afterwards" („Braucht danach noch
   Angaben“) are set up after the wizard under Settings → Extensions.
4. **Two-factor login** (optional, but recommended): "Set up now" („Jetzt einrichten“), confirm your password, scan
   the QR code with your authenticator app and enter the six-digit code. Then the **recovery codes** appear **exactly
   once**: copy them or save them as a text file, ideally in your password manager or on paper. You can do this later
   under Settings → My account ([appendix C](#c-two-factor-login-and-recovery-codes)).
5. **Appearance** (optional): product name, subtitle and accent colour. Logo, theme and more are available later under
   Settings → Appearance.
6. **Done:** This step shows the emergency command for a forgotten password ([7.3](#73-locked-out-emergency-commands)).
   "Continue to the dashboard" opens the cockpit.

Each step in detail, including custom CSS and its emergency exit: [appendix B](#b-setup-wizard-in-detail).

**The "First steps" card:** On the start page (cockpit), a checklist at the top walks you through exactly these
steps – create a server, store an SSH login, check the connection, set up modules, push notifications, first
widget – each with a button to the right place. Nodvard Deck ticks the boxes itself, based on what is already set up.
**"Store an SSH login"** („SSH-Zugang hinterlegen“) only counts a key once Nodvard Deck has actually logged in with it
(a password counts right away); until then, **"View command"** („Befehl ansehen“) leads to the server's page with the
setup command already expanded. **"Check the connection"** („Verbindung prüfen“) is only ticked once a login is
confirmed: the server merely answering is not enough.
Only steps for which you have the privilege are shown. The card disappears by itself when everything is done; with
**"Hide"** („Ausblenden“) you remove it earlier (applies to your account on every device). Empty pages (Terminal,
Files, server page, Docker, Game servers, Proxmox) now also say what is missing and offer the button for the
next step.

## 3. Connect your first server

<a id="5-servers-and-ssh-access"></a>

For Nodvard Deck to monitor and operate a server, you add it and give it SSH access. Everything for this is on one
page: **Settings → Servers & access** („Einstellungen → Server & Zugänge“). There you create servers, set up their
SSH access and check that the connection works. The page is designed for phones and only visible to users with the
privilege `hosts.write` (owner and `admin`).

- You add the **machine Nodvard Deck runs on** (e.g. a Raspberry Pi) by hand in the same way, unless it is a Proxmox
  guest.
- **Proxmox nodes, VMs and containers** are read in automatically by the "Proxmox VE" module
  ([4.2](#42-connect-proxmox)).
- Terminal, Server status, Docker, Nodvard Shield, Game servers and Scripts need an **SSH login** for each server (user +
  key or password). Nodvard Deck stores it encrypted; the private key never leaves the vault and is never shown
  anywhere.

**No server at hand yet?** In the cockpit, **"View with sample data"** („Mit Beispieldaten ansehen“) shows made-up
servers so you can see what everything looks like when filled ([appendix E.2](#e2-sample-data)).

### 3.1 Create a server

1. Press **"Add server"** („Server hinzufügen“) (on an empty list the button is also in the middle). If you come via
   "Add server" in the cockpit or in the "First steps" card, the form is already open.
2. **Short name** („Kurzname“) – lowercase, no spaces, e.g. `pi`. It can **not** be changed later (Nodvard Shield and
   other extensions recognize the server by it).
3. **Display name** („Anzeigename“) (what the server is called in the lists; empty = short name) and **Address**
   („Adresse“) (IP or host name, without `http://` and without a port, e.g. `192.168.1.30`).
4. **Operating system** („Betriebssystem“): Linux or Windows. For Windows servers there is no setup command and no
   privilege check. Really choose Windows for a Windows server (such as a game server): otherwise Nodvard Deck sends
   long commands the Linux way, and they fail on Windows.
5. **Tags** („Markierungen“) (comma-separated): `docker` if Docker runs there – the Docker module (setting "Server
   tag" – „Server-Markierung“) and the AI container watch of Nodvard Shield ("Monitored servers" – „Überwachte Server“)
   take exactly the servers with this tag. Game servers appear only with the tag `gameserver`. Tags can be changed at
   any time, also for Proxmox VMs (the ones set by Proxmox stay; they cannot be changed).
6. **"Create"** („Anlegen“). The new server's page opens and immediately offers the next step.

Wrong input is reported right at the field, in German (e.g. „Kurzname: nur Kleinbuchstaben, Ziffern, - und _ …“,
meaning "Short name: only lowercase letters, digits, - and _ …").

Correcting the address later (e.g. for Proxmox VMs without a guest agent): [appendix E.3](#e3-correcting-the-address).

### 3.2 Set up SSH access – three ways

On the server's page, card **SSH access** („SSH-Zugang“):

- **Generate SSH key (recommended)** („SSH-Schlüssel erzeugen“). Nodvard Deck generates a key of its own just for this
  server. You only specify the **user on the server** (default `nodvard`, a user of its own just for Nodvard
  Deck; it is created if it does not exist yet –
  for Proxmox nodes use `root`) and the port (22). Afterwards the page shows the **setup command** (3.3).
- **Enter password** („Passwort eingeben“). For a user that already exists on the server. The password is stored
  encrypted and never shown again. A key is more secure.
- **Paste your own key** („Eigenen Schlüssel einfügen“). Paste the private key (text beginning with `-----BEGIN …`,
  **without a passphrase**). Afterwards there is also a setup command for the server.

Whatever you type or paste is cleared from the form when you submit it.

### 3.3 Run the setup command on the server

The command is **a single one-liner**. Copy it to the clipboard with **"Copy"** („Kopieren“) (this also works on
`http://`, without a secure context), log in to the server (via `ssh`, at the screen, or via the console of your VM
management), paste it, press Enter. It

- creates the user if necessary (without a password, login only with the key),
- adds the public key to `~/.ssh/authorized_keys` (with `restrict,pty`: terminal, files and commands work,
  forwardings do not; Dropbear gets matching options, see [appendix E.4](#e4-what-the-setup-command-does-exactly)),
- optionally sets up **root rights without a password** and the **docker group** (see
  [appendix F](#f-root-rights-without-a-password-and-the-docker-group)).

Under "What does the command do?" („Was macht der Befehl?“) the same sequence is shown in readable form, line by line.
The command runs directly as root, otherwise via `sudo` – so it works on Proxmox (no sudo) as well as on Debian or the
Pi. It can safely be run several times.

**Root rights and the docker group:** You do not have to change the command by hand for this. The page sets the tick
boxes "Root rights without a password" („Root-Rechte ohne Passwort“) and "Add to the docker group" („Zur Gruppe docker
hinzufügen“) itself, because the switched-on modules report what they need. Without either tick box you get the **lean
variant**: a user of its own, key only, no root rights. That is enough for Terminal, Files and Scripts. What the tick
boxes allow: [appendix F](#f-root-rights-without-a-password-and-the-docker-group).

#### Set up with a password (Nodvard Deck runs the command)

You do not have to paste the command yourself: in the box **"Set up the server for this key"** („Server für diesen
Schlüssel einrichten“) you choose, instead of "Paste the command yourself" („Befehl selbst einfügen“), the second way,
**"Set up with a password (Nodvard Deck runs the command)"** („Mit Passwort einrichten (Nodvard Deck führt den Befehl
aus)“). This exists for logins with a key (generated or your own) on Linux servers. It is something different from the
login "Enter password" in [3.2](#32-set-up-ssh-access--three-ways): here you need the password only for this one
setup, and it is never stored. Afterwards Nodvard Deck logs in with the key.

- **What you enter:** once the account (default `root`) and its password. The account must be allowed to log in via
  SSH with a password: `root` or an account with sudo. Debian and Ubuntu usually do not let `root` in with a password;
  then use an account with sudo (the form says so at the field). For an account other than `root`, sudo uses the same
  password; if you untick "Use the same password for sudo" („Für sudo dasselbe Passwort nehmen“), the account must be
  allowed to use sudo without a password.
- **What happens:** Nodvard Deck logs in with it **once**, runs **exactly the same command** as in 3.3 (the tick boxes
  "Root rights without a password" and "docker group" above apply here too), closes the connection and then checks
  with the new key like "Check connection" ([3.4](#34-check-the-connection)). Below you see the command's messages and
  the result of the check. If the server had no default login yet, this login becomes the default. When replacing a
  login ([appendix E.7](#e7-replace-access-delete-groups)), the check counts like "Check new login" („Neuen Zugang prüfen“);
  afterwards you can press "Use new login" („Neuen Zugang verwenden“).
- **The password is never stored:** not in the database, not in the vault, not in the audit log and not in the log.
  The field is cleared the moment you submit, and the browser is told not to remember it. The audit log records that
  there was an attempt ("Server mit Passwort einrichten", `host.setup_with_password`) with server, account and result
  – never the password.
- **The server key must be known.** The password never goes to a server whose fingerprint Nodvard Deck does not know
  yet or which has changed. If Nodvard Deck does not know it yet, the form shows the fingerprint to compare and
  confirm (as in [3.4](#34-check-the-connection)); afterwards you enter the password once more. With a changed key
  there is only the warning, without a confirm button.

Limits, the warning without HTTPS and what is not possible yet:
[appendix E.5](#e5-set-up-with-a-password-limits-and-security).

### 3.4 Check the connection

Card **Check connection** („Verbindung prüfen“) (up to 30 seconds). The page shows point by point what works and
what does not – with a hint on what to do:

1. **Server reachable** – address, port, firewall, is SSH running?
2. **Server key** – see below.
3. **Login** – does the server accept the key or the password?
4. **Root rights** – does `sudo` work without a password? (Only a hint, not an error: the lean variant without root
   rights is allowed.)
5. **What extensions need** – e.g. Docker without sudo for the Docker module.
6. **Operating system** – e.g. "Debian 12 · aarch64 · Raspberry Pi 4 Model B".

**The server key (fingerprint):** Every SSH server has a fingerprint of its own.

- **New:** The first time, the check shows the fingerprint and stops. Until then, **no password and no key** has gone
  to the server. Compare the fingerprint with the one on the server (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`;
  the page shows the command to copy) and press **"Fingerprint matches – confirm"** („Fingerabdruck stimmt –
  bestätigen“). The page then continues checking right away. In your own home network and for a freshly set up
  server, confirming is fine; for a server on the internet, really compare the fingerprint.
- **Changed:** If the server later shows a different fingerprint, a clear warning appears – **without** a confirm
  button. This happens after a reinstallation of the server, but also when someone puts themselves in between. If it
  was a reinstallation: card **Server key** („Server-Schlüssel“) → **Forget** („Vergessen“), then check again and
  confirm the new fingerprint. If not: first find out why.
- Too many checks in a row slow the page down ("Zu viele Prüfungen. Bitte in N Minuten …" – too many checks, please
  wait N minutes).

From the list it is also quicker: **Check** („Prüfen“) next to each server shows the result briefly ("4 of 5 OK" –
„4 von 5 in Ordnung“), and the server page (`/hosts/<ID>`) has a card **Access** („Zugang“) with "Check
connection".

When the access badge turns green and how new server keys are remembered:
[appendix E.6](#e6-server-keys-and-the-green-badge). Replacing or deleting access, groups, deleting a server:
[appendix E.7](#e7-replace-access-delete-groups).

**Testing:** In the "Terminal" menu the server now appears (the Terminal extension switches itself on with the first
SSH login, unless you had switched it off yourself). Open a session – if the login works, it works for all the other
extensions too. A terminal session without any input or output closes by itself after 30 minutes.

## 4. Enable extensions

On a fresh installation, **all extensions are off** – with one exception: **Terminal switches
itself on** as soon as you give the first server an SSH login (generate a key, password or your
own key; see [3.2](#32-set-up-ssh-access--three-ways)). The audit log records it
(„Erweiterung automatisch eingeschaltet“). This only happens as long as nobody has ever switched
Terminal on or off themselves: if you switch it off on purpose, it stays off. Existing
installations are not affected.

In the setup wizard (step 3, see [2.](#2-setup-wizard-setup)) you switch on the modules you want to use with a
click. Later you can do that at any time under Settings → Extensions („Einstellungen → Erweiterungen“), with the
switch on the right; "Configure" („Konfigurieren“) opens the extension's settings ("Setup required" –
„Einrichtung nötig“ – means that details are still missing or that the last connection test failed). If you open
the page of a module that is switched off (for example via a bookmark), it says so, and **"Switch on"** („Einschalten“)
switches it on right there (without the privilege for that, it says that an administrator can switch it on). A good
start for almost any home network: Terminal, Server status, Nodvard Shield and Push notifications, plus the Docker module if
Docker runs on a server, and Game servers if a game server runs there. **You only need Proxmox VE and Proxmox-Backups if
you use Proxmox** – otherwise leave them off (see [4.1](#41-without-proxmox)). The rest as needed.

**Update server rights:** Switching on a module alone does not add rights on the server. If you enable Docker or
Nodvard Shield after setting up SSH, the docker group and helper scripts may be missing. Under **Settings → Servers & Access**
(„Einstellungen → Server & Zugänge“), open the existing login and, for each affected server with an existing key login,
run the newly displayed setup command again ([3.3](#33-run-the-setup-command-on-the-server)).
Then check the connection again ([3.4](#34-check-the-connection)).

**"Beta":** Game servers, Home Assistant, Jellyfin, Network, Nginx Proxy Manager, Pi-hole, Plex, qBittorrent, Synology DSM,
Uptime Kuma and Vaultwarden carry a "Beta" badge (in the settings, in the wizard, in the menu and above the module's
page). They work, but are not yet as thoroughly tested as the rest. Feedback helps.

**Synology DSM** (beta, read-only) shows the state, storage, drives and Hyper Backup of one or several Synology devices.
Create a separate DSM account just for Nodvard Deck for it (in the "administrators" group for most details) and enter
each device under Settings → Extensions → Synology DSM with a short name, address and account; the password goes into
the vault. Everything else (two-factor, certificate, several devices, notifications, Hyper Backup):
[Synology DSM](SYNOLOGY-DSM.md).

**Credentials belong to their address.** In an extension's settings, first enter the address and click "Save"
(„Speichern“), then store the token or password in the "Connection" card („Verbindung“) – Nodvard Deck does not
accept them before that. If you change the address later (for example the ntfy server or a Proxmox address),
Nodvard Deck deletes the matching credentials when saving, and you enter them again; the page tells you
beforehand. A different spelling of the same address (upper/lower case in the host name, default port, trailing `/`,
missing `http://`) does not count as a change.

More about individual modules (Docker, Nodvard Shield, apps, files, the address check in Nginx Proxy Manager, widgets) is
in [appendix G](#g-modules-in-detail).

### 4.1 Without Proxmox

Nodvard Deck does not need Proxmox. A Raspberry Pi, a Debian VM, a NAS or a few Docker hosts are enough. What is
different then:

- **You create servers by hand** ([3.1](#31-create-a-server)) and give each one an SSH login
  ([3.2](#32-set-up-ssh-access--three-ways)). The modules Proxmox VE and Proxmox-Backups stay off; their entries then appear
  neither in the menu nor in the cockpit.
- **Online or offline:** Nodvard Deck checks servers created by hand by itself every 2 minutes. If one goes down or
  comes back, you get a notification, a push message too if you like.
- **Utilization and history** (CPU, memory, disk, network, temperature) come from the **Server status** module
  („Server-Zustand“) via SSH, for Linux servers with an SSH login.
- **Docker containers** are started, stopped and updated with the **Docker** module, for servers with the tag `docker`.
- **Updates, antivirus, intrusion protection:** Nodvard Shield, for all Linux servers with an SSH login.
- **Tiles of your own** for the router, the NAS interface or Pi-hole are created in the "Apps" area of the cockpit with
  **+ Add app** („+ App hinzufügen“).
- **Console in the browser** (a guest's screen) exists only for Proxmox guests. For every other server you use the
  **Terminal**.

Everything in detail: [appendix G.1](#g1-without-proxmox-in-detail).

### 4.2 Connect Proxmox

<a id="7-connect-proxmox"></a>

**Only if you use Proxmox – otherwise skip this section** (see [4.1](#41-without-proxmox)).

1. Switch on the **"Proxmox VE"** extension.
2. On the **"Proxmox"** page click **"Connect with password"** („Mit Passwort verbinden“). You need admin rights in
   Nodvard Deck for this.
3. Enter the address (`https://…:8006`) and the password of a Proxmox administrator (default `root@pam`). If Proxmox has
   a self-issued certificate, compare its fingerprint first and confirm it.

Nodvard Deck creates user, role and token in Proxmox itself, reads in nodes, VMs and containers right away and forgets
the password at once. It remembers the fingerprint and checks it on every later request. If "Proxmox-Backups" is on, it
gets its own access at the same time ("Also connect Proxmox backups", „Auch Proxmox-Backups verbinden“). Once per
Proxmox server.

Step by step, with two-factor, or rather with a token of your own with exactly defined privileges:
**[PROXMOX-TOKEN.md](PROXMOX-TOKEN.md)**. The manual way in brief and what the modules report by themselves:
[appendix G.2](#g2-proxmox-in-detail).

## 5. Push notifications (ntfy)

<a id="8-push-notifications-ntfy"></a>

Turn on Settings → Extensions → **"Push notifications"** („Einstellungen → Erweiterungen → Push-Nachrichten“, formerly
„ntfy-Benachrichtigungen“) → "Configure" („Konfigurieren“). For ntfy:

- **ntfy server** („ntfy-Server“): e.g. `https://ntfy.sh` or your own. Nodvard Deck always connects to it directly, never
  through a proxy from the environment (`HTTP_PROXY`, `HTTPS_PROXY`). If the machine running Nodvard Deck only reaches the
  internet through a proxy, `ntfy.sh` cannot be reached; then use an ntfy server in your own network.
- **Topic** („Thema (Topic)“): the channel you subscribe to in the ntfy app. On ntfy.sh choose one that is hard to
  guess, because there anyone who knows the name can read along. The **"Choose randomly"** button („Zufällig wählen“)
  next to the field enters a random name.
- **Dashboard address (optional)** („Adresse des Dashboards (optional)“): `http://192.168.1.10:8080`. Then tapping a
  notification opens the matching page directly. It must start with `http://` or `https://`, otherwise it is ignored.
- **Minimum level for push notifications** („Mindest-Stufe für Push-Nachrichten“): "Everything (info, warning, critical)"
  („Alles (Info, Warnung, Kritisch)“) is the default. With "From warning" („Ab Warnung“) or "Critical only"
  („Nur Kritisch“), smaller messages no longer reach the phone; they stay under "Notifications" („Meldungen“). The test
  message always arrives.
- **Volume for info, warning and critical** („Lautstärke bei Info, Warnung und Kritisch“): how noticeable a message of
  that level is on the phone, from "1 – no sound" to "5 – very loud". Whether level 5 also gets through "Do not
  disturb" is decided by your phone.
- "Save" („Speichern“).
- **Credentials → "Access token (optional)"** („Zugangsdaten → Zugriffstoken (optional)“): only if your ntfy server
  requires login.

In the ntfy app on your phone, subscribe to the same server and the same topic. You can skip typing: after saving, the
page shows a **QR code** below the settings. Point your phone's camera at it, then the ntfy app opens and offers to
subscribe to the topic. The code contains only server and topic, never the access token.

**Testing:** "Nodvard Shield" page → "Send briefing" („Briefing senden“). Creating it can take up to half a minute if a
server does not respond. Afterwards the page tells you whether the status report („Lagebericht – …“) was also delivered
as a push notification. If not, ntfy is not set up yet or the ntfy server cannot be reached. All messages are also in
the dashboard under "Notifications" („Meldungen“) – even if the push notification does not arrive. There you can delete
a message with the bin icon or tidy up with „Alle gelesenen löschen“ (delete all read ones). Nodvard Deck deletes
messages older than 90 days on its own every night (unread critical messages stay until you read them); you change
the number under Settings → System → „Zeit & Protokoll“ (time & log).

**What the status report contains:** servers, protection score, threats, hardening, updates, intrusion protection and –
if any – how many proposals (from all modules) wait for your confirmation under "Actions" and which containers crashed
in the last 24 hours. A waiting approval or a crash that is still open turns it into „es gibt etwas zu tun“ (something to
do). It also arrives when everything is fine; if it does not arrive, Deck itself is probably down.

If you set the minimum level to "From warning" or "Critical only", test with "Send test message" („Testnachricht senden“,
"Connection" card under the ntfy settings) instead: a briefing without problems is only an info message and then does not
reach the phone.

### More channels (Telegram, Discord, Gotify, Pushover, Matrix, Slack, Mattermost)

Instead of ntfy or in addition (up to 10 channels): under **"More channels"** („Weitere Kanäle“) click "Add channel"
(„Kanal hinzufügen“), choose a **short name** (e.g. `telegram`), the **minimum level** (default "From warning") and
whether it is **active** → "Save". Then, under **Credentials**, enter the address in the entry "Channel address"
(„Adresse des Kanals“) with that short name. It contains credentials (bot token, webhook key) and is therefore kept only
in the vault. If you rename a channel, its address is gone and has to be entered again. It is written like in Apprise:

| Service | Address |
|---|---|
| Telegram | `tgram://BOT-TOKEN/CHAT-ID` (several chats separated by `/`; channels as `@name`) |
| Discord | `discord://WEBHOOK-ID/WEBHOOK-TOKEN` or the webhook URL Discord shows |
| Gotify | `gotifys://SERVER/APP-TOKEN` (`gotify://` without HTTPS, port with `:`) |
| Pushover | `pover://USER-KEY@APP-TOKEN` (optionally `/DEVICE`) |
| Matrix | `matrixs://ACCESS-TOKEN@SERVER/!ROOM-ID:SERVER` or `…/#roomname` (access token only, no password) |
| Slack | `slack://T…/B…/X…` or Slack's webhook URL |
| Mattermost | `mmosts://SERVER/WEBHOOK-ID` |
| anything else | `apprises://SERVER/KEY` – your own [Apprise API](https://github.com/caronc/apprise-api) (separate container), which forwards to every service Apprise supports |

Nodvard Deck sends the messages itself, directly to the service (no extra software, no proxy from the environment, no
redirects). Telegram, Discord, Slack and Pushover need direct internet access for this, and the text of each message
(e.g. server names, file paths, addresses) goes to that service. Group pings like `@everyone`, `@channel`, `@all` or
`@room` from a message stay plain text. If the "Dashboard address" above is set, each message ends with a link to it.

- **"Send test message"** sends to ntfy and all active channels at the same time and names each one that failed.
- **"Test connection"** asks each service without sending anything: for Telegram, Discord, Pushover, Matrix and an
  Apprise API this checks the token or key, for Telegram also whether the bot may post in each group and channel, for
  Matrix whether the account is in each room. Slack, Gotify and Mattermost do not allow that (nor do private Telegram
  chats); there you see a note, and an error from the last sending counts as well.
- The short name "ntfy" is not available; it stands for the ntfy server above.
- If one channel (or one chat of a channel) fails for a normal message, the others still get it.

## 6. Back up the dashboard itself

<a id="10-back-up-the-dashboard-itself"></a>

Nodvard Deck backs itself up: encrypted, on a schedule and, if you like, to another drive or a NAS. Set this up right at
the start; without the password and the recovery key no backup can be opened any more.

### 6.1 In the interface (recommended)

<a id="101-in-the-interface-recommended"></a>

**Settings → System → Backup** („Einstellungen → System → Sicherung“). Only the owner can change anything there.

1. **Set a backup password** („Sicherungspasswort festlegen“) (at least 12 characters, enter it twice, confirm with
   the login password). Nodvard Deck does not store the password, only a public key. Afterwards the *recovery key*
   (`AGE-SECRET-KEY-1…`) appears **once**: copy it or save it as a file and keep it safe.
   **Password and key gone = backups worthless.** Nobody can open them any more.
2. Turn on **Back up automatically** („Automatisch sichern“), choose the time and set how many backups should be kept
   (1 to 60, older ones are deleted).
3. **Folder** („Ordner“): the default is `/app/data/backups` in the data folder. That protects against operating
   mistakes, but not against a broken SD card. It is better to mount a folder from another drive or a NAS: the
   Compose file (`compose.yml` from [section 1](#1-quick-start), or `deploy/docker-compose.yml` for an
   installation from the repository) already contains the line `# - ./sicherungen:/backups` under `volumes:`. Remove
   the `#` (or enter a NAS path instead of `./sicherungen`), run `mkdir sicherungen && sudo chown 1000:1000 sicherungen`
   in the same folder (the folder must belong to the user with the number 1000), apply it with `docker compose up -d`
   and choose `/backups` as the folder in the interface. For a NAS or NFS see
   [Backups to a NAS or NFS](#backups-to-a-nas-or-nfs).
4. **Check** („Prüfen“) compares a backup with its checksum (detects broken files, without a password).
   **Download** („Herunterladen“) fetches a backup to your PC, **Download backup** („Sicherung herunterladen“) creates
   a fresh one immediately – either with the backup password or with a one-time password of your own just for this
   file.

#### Backups to a NAS or NFS

Nodvard Deck only accepts `/app/data/backups` in the data folder or the mounted folder `/backups` and its subfolders
(e.g. `/backups/deck`) as a target. This always means the path **inside the container**, not the one on the host or the
NAS. In brief: mount the NAS folder on the host (with NFS so that it is ready before Docker), mount it into the container
as `/backups` in the Compose file, give user 1000 write access, test it and choose `/backups` in the interface. Step by
step, with the line for `/etc/fstab`, the permissions and the error messages when saving:
[Backups in detail](BACKUP.md#backups-to-a-nas-or-nfs).

### 6.2 Restore (import a backup)

<a id="102-restore-import-a-backup"></a>

Two ways, same procedure: **Settings → System → Restore** („Einstellungen → System → Wiederherstellen“) (owner only,
with the login password) or, on a **fresh installation**, in the setup wizard under **"Restore a backup"**
(„Sicherung einspielen“) (there, instead of an account, with the **setup code** from the container's log – as
when creating the first account).

1. **Choose the file** (`.ndbak`) and upload it. There is a progress indicator; the file is streamed to disk, not held
   in memory. Upper limit 4 GiB (`NODVARD_DECK_RESTORE_MAX_UPLOAD_BYTES`).
2. Enter the **password or recovery key** (for a file "with its own one-time password", only the one-time password).
   Nodvard Deck decrypts and checks everything in a separate staging folder; the backup's database is only read and
   never executed.
3. **Look at the summary:** created on, version, identifier of the installation, name of the owner, number of users and
   servers, extensions, warnings. **age does not verify who created a backup** – anyone who knows the password can
   build one. Only restore backups that you created yourself.
4. **"Restore and restart"** („Einspielen und neu starten“). This replaces **everything, including the accounts**;
   afterwards everyone logs in again (with the accounts from the backup). Nodvard Deck shuts down and restarts; the
   restore happens at startup, then it migrates as usual. The page waits and then leads to the login.

Good to know:

- **A restart policy is required.** The restart only works if the container has a restart policy
  (`restart: unless-stopped`; the bundled Compose files have it). With `docker run` without `--restart`, it stays off
  afterwards: start it by hand (`docker start …`), the backup is restored at startup.
- **The old state is kept** under `restore/replaced-<time>` in the data folder (with old accounts and keys), exactly
  one, and is **deleted automatically after 30 days** (the card names the day). Under *Restore* you can delete it
  earlier with the login password.
- **If the old installation is still running**, for example on another device, both have the same keys and
  credentials. After that, run only one.
- **If something fails** (broken or too new backup, error while moving, migration fails), the old state comes back and
  the card names the reason. A crash in the middle of restoring is rolled back at the next start. A backup from a
  **newer** version or with an extension that is missing here is rejected with a notice. A pending restore request is
  valid for one hour; an abandoned intermediate state is deleted after one hour.
- What is not in the backup (measurements, the extensions' Git settings, a login secret from the environment):
  [Backups in detail](BACKUP.md#what-is-not-in-the-backup).

**Emergency without the interface:** in the folder with the Compose file

```bash
cd ~/nodvard-deck
sudo docker compose exec nodvard-deck python -m nodvard_deck.admin restore-backup /app/data/backups/nodvard-deck-sicherung-….ndbak
sudo docker compose restart nodvard-deck      # the restore happens at startup
```

(The file must be visible inside the container; the command asks for the password and shows the summary. `compose exec`
runs in the existing container; a `compose run` alongside it would be rejected by the data folder's lock, see
[appendix I.3](#i3-the-copy-before-the-migration).) With an installation from the repository,
`deploy/restore.sh sicherung.ndbak` does the same in one step
([Backing up with the script](BACKUP.md#with-the-script-installation-from-the-repository-only)).
If nothing works at all, the old state is still under `restore/replaced-…`:
[Backups in detail](BACKUP.md#if-nothing-works-at-all). In an emergency a `.ndbak` file can also be opened entirely
without Nodvard Deck: [Backups in detail](BACKUP.md#emergency-opening-a-backup-without-nodvard-deck).

## 7. Common stumbling blocks

The most common hurdles at the start, each with the next step. All other error messages (Proxmox, modules, login) are in
[appendix J](#j-when-something-goes-wrong).

### 7.1 The dashboard cannot be reached

- **Right after the start:** The first start takes up to five minutes (downloading the image, creating the database).
  Until then, interfaces such as Portainer or a NAS's container area show the container as "starting" or not yet as
  "healthy".
- **"port is already allocated" or "address already in use":** Port 8080 is already taken. Use another one
  ([section 1](#1-quick-start); for interfaces and NAS systems it is described with each way in
  [Installation per platform](INSTALLATION.md)). On a QNAP, the NAS's own interface uses port 8080.
- **"unhealthy", or the container does not start:** The log names the reason, in the folder with `compose.yml`:
  `sudo docker compose logs --since 15m nodvard-deck` (in interfaces under "Logs" for the container). If the browser
  shows a **rescue page** instead of the dashboard, see [appendix K](#k-nodvard-deck-does-not-start-the-rescue-page).
- **Is it running at all?** On the machine running Nodvard Deck, `curl -s http://localhost:8080/api/v1/health` shows
  `"status":"ok"`, and `sudo docker compose ps` shows the state of the container. If that works there but not from
  another device, the address or the port is usually wrong.
- **"Server nicht erreichbar – bitte gleich noch einmal versuchen." (server not reachable, please try again shortly)
  at login:** The dashboard does not respond; the same checks as above. If it says **"Server meldet: …"** (server
  reports) instead (also when reloading the page), the dashboard responds and names the reason, e.g. that the disk is
  full – then fix that. You are not logged out by this.
- **The setup code is not accepted:** It is in the container's log ([2.1](#21-finding-the-setup-code)). Wrongly entered
  codes are slowed down: after 10 failed attempts there is a pause of a few minutes.
- **"Die Seite konnte nicht geladen werden" (the page could not be loaded)** usually appears right after an update:
  reload the page (F5).

### 7.2 The server will not connect

**"Check connection"** („Verbindung prüfen“) on the server's page shows point by point what is wrong
([3.4](#34-check-the-connection)). The most common messages:

**"Der Server hat den Schlüssel nicht akzeptiert." (the server did not accept the key):** Usually the setup command has
not been run on the server yet ([3.3](#33-run-the-setup-command-on-the-server)); running it is safe, even several
times. If Dropbear runs there and the server was set up with an older version:
[appendix E.4](#e4-what-the-setup-command-does-exactly).

**Server missing in Terminal / "Noch kein Server prüfbar" (no server checkable yet) or "Keine Linux-Server mit SSH-Zugang
gefunden" (no Linux servers with an SSH login found) in Nodvard Shield:**
No SSH login stored: Settings → Servers & access ([3.2](#32-set-up-ssh-access--three-ways)). Nodvard Shield also takes only
servers that are entered as `linux` and – if "Only servers with tag" („Nur Server mit Markierung“) is filled in in its
settings – only servers with that tag.

**"Keine root-Rechte …" (no root rights):** sudo without a password is missing
([appendix F](#f-root-rights-without-a-password-and-the-docker-group)). The "Check connection" card shows it under "Root
rights" („Root-Rechte“).

**"Der Server-Schlüssel von diesem Server ist noch nicht bestätigt." (the server key of this server has not been
confirmed yet):** Nodvard Deck does not know this server's fingerprint yet (a new server while "Only remember new
server keys after my confirmation" is on), or you forgot it. Under Servers & access, press "Check connection" for the
server and confirm the fingerprint ([3.4](#34-check-the-connection)).

**"Host-Schlüssel … hat sich geändert" (host key … has changed):** Server reinstalled? Then, under Servers & access, on
the server's "Server key" card, forget the old one, check again and confirm the new fingerprint. If not: first find out
why – it can also be an attack.

**"Server antwortet nicht (Zeitüberschreitung …)" (server not answering, timeout), "Der Server lehnt die Verbindung ab
…" (the server refuses the connection), "Kein Weg zum Server …" (no route to the server) or "Den Namen … kennt das Netz
nicht." (the network does not know the name)** in the terminal or file manager (there with "Zugriff auf die Quelle
fehlgeschlagen:" (access to the source failed) in front): Nodvard Deck cannot reach the server. The sentence usually
names address and port. Check that the server is on, that the address under Servers & access is correct and that SSH
runs there. There is no traceback about it in the log.

### 7.3 Locked out? Emergency commands

<a id="12-locked-out-emergency-commands"></a>

If someone – above all the owner – has lost their password or two-factor login and cannot get in through the web
interface any more either, there are commands **on the machine Nodvard Deck runs on** (e.g. the Raspberry Pi), run in
the folder with the Compose file. Anyone who can run them has access to everything in the data directory anyway; that
is why no login is needed, but every change is recorded in the audit log (actor `system/cli`).

```bash
cd ~/nodvard-deck
sudo docker compose exec nodvard-deck python -m nodvard_deck.admin list-users
sudo docker compose exec nodvard-deck python -m nodvard_deck.admin reset-password admin
sudo docker compose exec nodvard-deck python -m nodvard_deck.admin disable-2fa admin
```

(`admin` stands for the username that `list-users` shows; an older name with spaces goes in quotation marks. With an
installation from the repository, change to the repository's `deploy/` folder instead of `cd ~/nodvard-deck`.)

- **`list-users`** shows username, owner/user, locked or active, and whether two-factor is on.
- **`reset-password <username>`** sets a new, random password and **prints it** (e.g. `k7mq-x2vd-h9pa-tn4c`). All of
  this user's existing logins are ended. Nothing else changes (two-factor stays). After logging in, set a password of
  your own under My account.
- **`disable-2fa <username>`** turns off two-factor login, deletes the recovery codes and ends all logins.
  Afterwards login works with the password only; two-factor can be set up again under My account.

On errors (user does not exist, database not reachable) an understandable message is shown. The command changes only
users – no servers, no keys, no settings.

**Without a command line** (Portainer, Docker Desktop, NAS): open the console of the Nodvard Deck container and enter
the command there **without** `sudo docker compose exec nodvard-deck` in front, e.g.
`python -m nodvard_deck.admin reset-password admin`. You recognize the container by "nodvard-deck" in its name; it is
usually called `nodvard-deck-nodvard-deck-1`. This is where the console is:

- **Portainer:** Containers → the Nodvard Deck container → "Exec Console" icon → "Connect".
- **Docker Desktop:** "Containers" → expand the group "nodvard-deck" → click the container in it → "Exec" tab.
- **Synology Container Manager:** Container → select the Nodvard Deck one → "Details" → "Terminal" tab → "Create".
- **Unraid:** "Docker" tab → icon of the Nodvard Deck container → "Console".

The same instructions are on the login page under "Forgot password?" („Passwort vergessen?“).

**Good to know:** When Nodvard Deck "signs someone out everywhere" (password changed, two-factor reset or turned off,
emergency command) or you sign out, the login ends immediately, also for an access key already issued in the browser:
the next request with it is refused. Open terminal and console sessions of an ended login close after at most
15 seconds, the live updates after at most 30 seconds.

## Appendix

For looking things up: technical details, special cases and anything you do not need for the start.

| | Section | What it is about |
|---|---|---|
| A | [Installation in detail](#a-installation-in-detail) | data in a folder of your own, building from the repository |
| B | [Setup wizard in detail](#b-setup-wizard-in-detail) | setup code, every step, custom CSS |
| C | [Two-factor login and recovery codes](#c-two-factor-login-and-recovery-codes) | using, switching off, lock after failed attempts |
| D | [Additional users](#d-additional-users) | roles, resetting two-factor, deactivating accounts |
| E | [Servers and access in detail](#e-servers-and-access-in-detail) | small SSH servers, sample data, server keys, replacing access |
| F | [Root rights without a password and the docker group](#f-root-rights-without-a-password-and-the-docker-group) | what the tick boxes allow |
| G | [Modules in detail](#g-modules-in-detail) | without Proxmox, Proxmox by hand, address check, widgets |
| H | [Automation and maintenance windows](#h-automation-and-maintenance-windows) | approvals, scripts without a click, automatic updates |
| I | [Updates](#i-updates) | new version, update with one button, copy before the migration |
| J | [When something goes wrong](#j-when-something-goes-wrong) | error messages from Proxmox, modules and login |
| K | [Nodvard Deck does not start: the rescue page](#k-nodvard-deck-does-not-start-the-rescue-page) | rescue code, state before the update |
| L | [Without the interface](#l-without-the-interface) | setting up servers through the API |
| M | [Old names on older installations](#m-old-names-on-older-installations) | why "lattice" still shows up here and there |

## A. Installation in detail

Every way per platform (terminal, web interface, NAS, Raspberry Pi, Proxmox, Docker Desktop) and the short Compose
version for web interfaces: [Installation per platform](INSTALLATION.md).

### A.1 Data: volume or your own folder

The default is the Docker volume `nodvard-deck-data`. It needs no permission settings, not even on a NAS. A folder of
your own (`<folder>:/app/data`) works too: the container then makes the user with the number 1000 the owner of that
folder at startup. When that fails on NAS shares and what helps then:
[Installation per platform, "Good to know"](INSTALLATION.md#good-to-know-whichever-way-you-pick). The database is
created or updated automatically when the container starts; there is nothing to do.

### A.2 Building from the repository (developers)

If you clone the repository and build the image yourself, you start it in the repository's `deploy/`
folder (`docker compose build --pull && docker compose up -d`; `--pull` fetches fresh base images). Building, delivering to a Raspberry Pi (`scripts/deploy_pi.sh`),
the fallback route and rollback are covered in [deploy/README.md](../../deploy/README.md) (German). The check
afterwards is the same as above (`curl -s http://localhost:8080/api/v1/health`).

With this installation, the commands in this guide are run in the repository's `deploy/` folder (e.g.
`cd ~/deck/deploy`) instead of `~/nodvard-deck`. Sections that only apply to this route say so.

## B. Setup wizard in detail

### B.1 The setup code

- Upper/lower case, dashes and spaces do not matter.
- The code stays the same across restarts (it is stored as `setup_code.txt` in the data directory,
  readable only by Nodvard Deck) and is deleted once the account has been created.
- Wrongly entered codes are slowed down like wrong passwords (after 10 failed attempts a pause of a few
  minutes) and appear in the log as "Einrichtungscode falsch" (setup code wrong).
- Existing installations that already have an account are not affected.

### B.2 The steps one by one

The wizard has **six steps** and shows "Step X of 6" („Schritt X von 6“) at the top. Only the first is
mandatory; everything else can be skipped and done later in the settings. "Back" („Zurück“) is available
from step 3.

**Step 1 of 6 — Create the administrator account** („Administrator-Konto anlegen“)

- **Setup code** („Einrichtungscode“): see [2.1](#21-finding-the-setup-code).
- **Username** („Benutzername“): at least 3 characters, only lowercase letters, digits and `.`, `-` and `_`,
  no spaces, starting with a letter or a digit (e.g. `admin`). Uppercase letters are converted automatically.
- **Password** („Passwort“) and **Confirm password** („Passwort bestätigen“): at least
  8 characters.
- "Create account" („Konto anlegen“). If an entry does not fit, the wizard says right away which one, in German
  (e.g. „Benutzername: Mindestens 3 Zeichen.“, meaning "Username: at least 3 characters.").

This first account is the **owner** („Inhaber“). The owner may always do everything, even
if roles are changed later – so you cannot lock yourself out.

**Step 2 of 6 — Time zone** („Zeitzone“)

Schedules (for example the morning briefing at 7:00) and maintenance windows follow this time zone. It is
preselected with the time zone of the device you are using in the browser; if the browser does not know it,
the current setting stays. With the search (for example "Berlin", "New York", also German names such as "Wien" or
"Mitteleuropa") or the list you pick a different
one. "Continue" („Weiter“) saves it, "Skip" („Überspringen“) leaves the setting as it is. You can change it
later under Settings → System („Einstellungen → System“). Without a search, the list shows the selected zone
first, then the common ones (Berlin, Vienna, Zurich …); known zones show their German name next to them (e.g.
„Österreich, Wien“ for `Europe/Vienna`).

**Step 3 of 6 — What do you want to use?** („Was willst du nutzen?“)

All bundled modules as tiles with a switch (except the sample module "Hello World" for developers, which only appears
under Settings → Extensions), sorted by topic: first "For your servers" („Für deine Server“)
(Server status („Server-Zustand“), Terminal, Docker, Proxmox VE …), then security, tools, connections to other services.
One click on the tile or the switch turns a module on or off. No module is mandatory ("Skip"). Modules that
still need details afterwards (address, credentials, token) carry the note "Needs details afterwards"
(„Braucht danach noch Angaben“); you enter these details **only after the wizard** under Settings →
Extensions („Einstellungen → Erweiterungen“), and the "First steps" („Erste Schritte“) card in the cockpit
reminds you (see [4.](#4-enable-extensions)). Order and group come from the module's manifest (`category`,
`sort_order`).

**Step 4 of 6 — Two-factor login (optional)** („Zwei-Faktor-Anmeldung“)

"Set up now" („Jetzt einrichten“) first asks for your current password for safety ("Continue" („Weiter“)), then
shows a **QR code** for the authenticator app (it is drawn in the browser,
nothing is sent to an external service) plus the key as text in case scanning does not work. Enter the
six-digit code from the app and press "Confirm" („Bestätigen“). The **recovery codes** („Wiederherstellungs-Codes“;
the interface also calls them „Rettungscodes“, see [appendix C](#c-two-factor-login-and-recovery-codes)) are then shown
**exactly once**: copy them or save them as a text file, then press "I have saved the codes" („Ich habe die Codes
gesichert“). "Set up later" („Später einrichten“) is there at the choice, at the password prompt and next to
"Confirm", sets nothing up and goes on to step 5; you can do it later under Settings → My account
(„Einstellungen → Mein Konto“). "Back" („Zurück“) within the step returns to the choice.

**Step 5 of 6 — Appearance (optional)** („Aussehen“)

Product name („Produktname“), subtitle („Untertitel“: the small line under the name; the same text is the app's name on
the phone's home screen, so keep it short), accent color („Akzentfarbe“) →
"Save" („Speichern“), or "Skip". The logo, the favicon and the theme („Klassisch hell“, „Schlicht dunkel“,
„Profi dunkel“, „Bisher“) are available later under Settings → Display („Einstellungen → Darstellung“). Logo and
favicon are **uploaded** there; Nodvard Deck does not load images from other sites. If an older setup pointed to
another site, the „Marke“ card shows a notice until you upload the image or remove it.
There, admins (permission `branding.write`) can also add **custom CSS** for everyone („Eigenes CSS“), ideally by
overriding theme variables, e.g. `:root { --radius: 0px; }`. Embedded images (`data:`) and paths on Nodvard Deck itself
are allowed, `@import` and other sites are not. **Escape hatch:** if the custom CSS makes the interface unusable, add
`?ohne-css=1` to the address (e.g. `http://deck:8080/settings/appearance?ohne-css=1`). The tab then loads without the
custom CSS, also after clicking on; the „Eigenes CSS“ card says so and has a button „Wieder einschalten“ (turn back
on). Everyone else keeps the CSS until you fix and save it.

**Step 6 of 6 — Done** („Fertig“)

A pointer to the "First steps" card in the cockpit and the emergency command (see
[7.3](#73-locked-out-emergency-commands)); the same command is shown on the login page behind "Forgot
password?" („Passwort vergessen?“). If you skipped two-factor login, you get the link to do it later here.
"Continue to the dashboard" („Weiter zum Dashboard“) opens the cockpit.

**Reloaded in the middle of the wizard?** The account already exists at that point, so `/setup` actually leads to
the login. But the wizard remembers the step it had reached in that browser tab: if you are still signed in, it
continues exactly there (time zone, modules, two-factor, appearance); otherwise you land on the login page, and
the "First steps" card takes over afterwards. The recovery codes cannot be shown a second time after a reload –
you create new ones under My account („Mein Konto“).

After that, `/setup` is gone (it only leads to the login page).

## C. Two-factor login and recovery codes

<a id="21-two-factor-login-and-recovery-codes"></a>

In the wizard (step 4) or later under Settings → My account → "Two-factor login"
(„Einstellungen → Mein Konto → Zwei-Faktor-Anmeldung“) → "Set up" („Einrichten“): enter your current password
for safety ("Continue" („Weiter“)), then scan the QR code with the
authenticator app (or enter the key by hand) and confirm the six-digit code. As soon as two-factor login is
active, Nodvard Deck shows **ten recovery codes, once** (format `ABCDE-FGHJK`). Save them with "Copy"
(„Kopieren“) or "Save as text file" („Als Textdatei speichern“) – ideally in a password manager or
printed out, **not** only on the phone, because the phone is exactly what is supposed to be lost.

- **Using one:** At login, in the "Confirmation" („Bestätigung“) step, click "Phone not at hand? Use a
  recovery code" („Handy nicht zur Hand? Wiederherstellungs-Code verwenden“) and type in a code. Each code works
  **exactly once**; Nodvard Deck records the use in the audit log and sends a
  notification (Notifications page („Meldungen“), also as a push if ntfy is set up).
- **Codes from the app** work only **once** per account (including the one you used to confirm the setup); if one
  has already been used, wait for the next one. After 10 wrong codes in 15 minutes (or 20 in 24 hours), no matter from
  which device, code entry for the account is blocked for a while and Nodvard Deck sends a notification. The block
  stays in place after a restart of Nodvard Deck. Whoever guesses that often probably knows the password. If it was not
  you, change your password right away. For that and for signing in you need a recovery code during that time, because
  the codes from the app are blocked. Confirmations with the code from the app (for example for an update) only work
  again once the block is over.
- **How many are left** is shown under My account. If there are few or none: "Generate new recovery codes"
  („Neue Wiederherstellungs-Codes erzeugen“). This needs your password and a code: the current one from the app or one
  of your existing recovery codes. The old codes become invalid immediately.
- **Switching it off** (My account → "Switch off" („Abschalten“)) only works with your password **and** a code: the
  current one from the app or a recovery code, which is used up in the process. Someone who only knows your password
  cannot switch two-factor login off this way. Older apps do not ask for the code yet; there, switching off, new codes,
  changing the password and backups only work after an app update, in the browser right away.
- **Changing the password** (My account → "Password" („Passwort“)) also asks for the code from the app or a recovery
  code when two-factor login is on; the field for it is shown right away. Otherwise someone who only knows your old
  password could set a new one and lock you out. An older phone app shows a note to update the app first.
- If a recovery code is used to switch off, to change the password or to generate new codes, this is recorded in the
  audit log just like at sign-in, and Nodvard Deck sends a notification.
- **Downloading or restoring a backup** also asks for the code from the app when two-factor login is on (no recovery
  code here): a backup contains all keys, including the one for two-factor login. The field for it appears right away.
  If you mistype, the passwords stay filled in; only the code has to be entered again.
- Nodvard Deck stores only checksums, not the codes – a lost set cannot be looked up, only replaced.
- If you had already set up two-factor login before this feature existed, you do not have codes yet:
  generate them once under My account with "Generate new recovery codes" (with your password and the code from the
  app).
- If someone loses their phone **and** the codes: see [7.3](#73-locked-out-emergency-commands).

## D. Additional users

<a id="3-additional-users"></a>

Settings → Users → **"New user"** („Einstellungen → Benutzer → Neuer Benutzer“):

- **Username** („Benutzername“): the same rule as for the first account (at least 3 characters, only lowercase
  letters, digits, `.`, `-` and `_`, starting with a letter or a digit); uppercase letters are converted
  automatically. Upper or lower case does not matter at login. Older accounts whose name does not fit this rule
  (e.g. with spaces) keep signing in normally.
- **Password** („Passwort“): at least 8 characters. The new user can change it under "My
  account" („Mein Konto“). (The password of *other* users is set later under "Edit"
  („Bearbeiten“); your own and the owner's cannot be set there – your own is set under "My account".)
- **Display name** („Anzeigename“), **Email (optional)** („E-Mail (optional)“); an address you enter needs an `@`
  (e.g. `name@beispiel.de`). Nodvard Deck does not send emails: the address is only a detail of the account
  (notifications arrive as a push via ntfy).
- **Roles** („Rollen“):
  - **Administrator** (`admin`) – full administration, including users and settings
  - **Operator** („Bediener“, `operator`) – operate servers, approve actions up to medium risk
  - **Viewer** („Betrachter“, `viewer`) – view only, change nothing. Without the privilege `hosts.execute` (as for commands), the viewer
    sees neither files on servers nor the commands and output of actions (text from the server), not even in the audit
    log. `viewer` can read notifications but cannot mark them as read (the read status is shared by all users)
- "Create" („Anlegen“). If an entry does not fit, a notice at the top says right away which one, in German
  (e.g. „Passwort: Mindestens 8 Zeichen.“, meaning "Password: at least 8 characters.").

**Resetting a user's two-factor login:** If someone has lost their phone and their recovery codes, a user
with the privilege `users.write` (role `admin`) turns off that person's two-factor login under Settings →
Users → **"Reset two-factor"** („Zwei-Faktor zurücksetzen“) (for safety, the admin's own password is asked
for and, if the admin has two-factor login on, the admin's own code from the app). The same applies when an admin
sets a new password for someone else while editing, creates an account with management rights (role `admin`) or gives
an account that role. The person is signed out everywhere, the audit log records who
did it, and "Notifications" shows "Access of … changed" („Zugang von … geändert“). Afterwards, login works with
the password only, and the person can set up two-factor again. **This does not work for the owner** – the
owner's two-factor login can only be turned off by the owner personally (under My account) or by the
emergency command on the server ([7.3](#73-locked-out-emergency-commands)).

**Deactivating a user:** Under Settings → Users → **"Deactivate"** („Deaktivieren“), a user with the
privilege `users.write` blocks another person's account (the list then shows "Blocked" („Gesperrt“); there
is no such button for the owner or for your own account). The person is signed out everywhere, and a page that
is already open stops getting live updates after half a minute at most. After **"Activate"** („Aktivieren“),
old devices do not sign in again by themselves; the person has to sign in again.

## E. Servers and access in detail

### E.1 Where servers come from

- Proxmox nodes, VMs and containers are read in **automatically** by the "Proxmox VE" extension
  (every 5 minutes, see [4.2](#42-connect-proxmox)). They appear in the list with the note "Read in
  automatically" („Automatisch eingelesen“).
- Everything else – above all the **machine Nodvard Deck runs on** (e.g. a Raspberry Pi), if it is not
  a Proxmox guest – you create by hand.
- Terminal, Server status, Docker, Nodvard Shield, Game servers and Scripts need an **SSH login** (user + key or
  password) for every server. Nodvard Deck stores it encrypted; the private key never leaves the vault and is
  never displayed anywhere.
- **Small SSH servers** such as Dropbear from version 2015.68 on (e.g. on DietPi, routers and some NAS devices) work
  too: Nodvard Deck sends long commands (over 8000 bytes, e.g. the setup and the updates, hardening audit and
  intrusion check of Nodvard Shield) through standard input instead of as a command line. For that, the account's
  login shell has to be a POSIX shell (sh, bash, dash or BusyBox ash); with fish or tcsh as the login shell, these
  long commands do not work. Where standard input is not possible (console, live logs), Nodvard Deck reports a command
  that is too long in plain words beforehand and does not send it. Older Dropbear versions (e.g. on OpenWrt 15.05)
  only accept commands up to 1024 bytes, so many commands fail there.

### E.2 Sample data

**No server at hand yet?** On a fresh installation without servers, the cockpit (and this page) offers the button
**"View with sample data"** („Mit Beispieldaten ansehen“). One click creates five made-up servers (addresses from
`192.0.2.x`, nothing is ever contacted), a few notifications, three sample apps and – if modules bring widgets – a
sample dashboard, so you can see what everything looks like when filled. A yellow banner **"You are viewing sample
data"** („Du siehst Beispieldaten“) then appears at the top; **"Delete sample data"** („Beispieldaten löschen“)
(after confirmation) removes exactly these things again, restores your dashboard layout and leaves everything real
untouched. Only the owner and administrators see the button (`hosts.write` and `settings.write`). As soon as you
have created a real server, the button is gone; you can still delete the sample data at any time.

### E.3 Correcting the address

**Correcting the address:** Proxmox VMs without a running guest agent get the address of the Proxmox server as a
placeholder. Then, on the server's page, enter the real IP under **General → Edit** („Allgemein → Bearbeiten“) –
Nodvard Deck no longer overwrites an address set by hand with the placeholder, only with an address that the guest
itself reports. For Proxmox *nodes*, the extension overwrites the address again at the next sync.

If the server has a stored **SSH password**, changing the address deletes it; you enter it again afterwards. SSH
keys stay. This also applies when an extension's sync changes the address (e.g. of a Proxmox node); for VMs and
containers that report their address themselves and whose server key is already remembered, the password stays.

### E.4 What the setup command does exactly

If Dropbear runs on the server (e.g. on DietPi), the command adds the key with
`no-port-forwarding,no-agent-forwarding,no-X11-forwarding` instead of `restrict,pty`: Dropbear does not know `pty` and
would otherwise discard the whole line.

It recognizes a key that is already there, even with the older comment `lattice@…`
([appendix M](#m-old-names-on-older-installations)), and does not add it twice. A commented-out line (`# …`) does not count: if you locked Nodvard Deck out
that way, setting it up again adds a valid line, and the commented one stays. If the key is in the file without
restrictions (for example added by hand), the command puts `restrict,pty` or the options for Dropbear in front of it
and reports „Der schon eingetragene Schlüssel für Nodvard Deck ist jetzt eingeschränkt …“ (the key already there is
now restricted). Lines with options of their own, such as `from="…"`, stay as they are. To check what it wrote, it
needs `cmp` (package diffutils); if that is missing, it changes nothing and names the fix.

If you set up a server with Dropbear with an older version and "Check connection" („Verbindung prüfen“) reports that
the server did not accept the key („Der Server hat den Schlüssel nicht akzeptiert“), simply set it up again (with a
password or with the command). The command then replaces exactly its own old line `restrict,pty …` with the line for
Dropbear (message „Schlüssel für Dropbear angepasst.“); other lines with the same key and options of their own stay as
they are.

### E.5 Set up with a password: limits and security

- **Limits:** You need the privileges `hosts.write` **and** `hosts.execute`, because the command runs on the server as
  root, like the terminal. Nodvard Deck slows attempts down: at most 5 per address and port, 10 per user in 10 minutes
  (plus 30 calls per user in 5 minutes), and only one setup runs per server at a time; then you see "Zu viele Versuche
  mit Passwort …" (too many attempts with a password) or "Für diesen Server läuft gerade schon eine Einrichtung …" (a
  setup is already running for this server). The counters live in memory and start over after a restart. The command
  gets up to 90 seconds. If the connection drops or the time runs out, the command may still have run partly on the
  server: "Check connection" shows what is already there, and trying again is harmless because the command can be run
  several times.
- **Without HTTPS** the form warns you before you submit the password: it then travels unencrypted from your browser
  to Nodvard Deck. On your own home network that is usually fine, on a foreign network it is not; "Paste the command
  yourself" is safer then. When you open the page directly on the machine (`localhost`) the warning does not appear.
  Nothing is blocked.
- **Not possible yet:** Proxmox with a password, Windows servers (there is no setup command for them) and the setup
  wizard.

### E.6 Server keys and the green badge

**When the access turns green:** In the list and on the server page, the SSH access badge only turns green once the
server answers and Nodvard Deck has actually logged in there with this login, for example via "Check connection" or
when a module queries the server via SSH. The server answering only means that its SSH port is open. Until then the
badge stays grey, with a yellow note next to it saying what is missing: „noch nicht geprüft“ (not checked yet), „noch
keine Verbindung“ (no connection yet: the server has never answered), „Anmeldung noch nicht bestätigt“ (login not
confirmed yet: with a key, the setup command is usually still missing), „keine Antwort“ (no answer) or „Anmeldung
klappt nicht“ (login does not work). After a new address, a rejected login, a changed server key or a new default
login without a fresh check, the login counts as unconfirmed again until it works the next time.

> **Note:** Whether Nodvard Deck remembers a new server key by itself on the very first connection (Terminal,
> monitoring, …) is set under **Settings → Servers & access** in the card **New server keys** („Neue
> Server-Schlüssel“). **Only remember new server keys after my confirmation** („Neue Server-Schlüssel erst nach
> meiner Bestätigung merken“) is on, also on installations set up before this setting existed: Nodvard Deck then
> remembers a key only through "Check connection", and no password and no key goes to the server before that. If
> Deck does not know a server's key yet, press "Check connection" there once and confirm the fingerprint. "Off"
> (not recommended) remembers new keys by itself again; that only applies if you set it that way explicitly. The
> environment variable `NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS` (`true` or `false`; in the Compose file under
> `environment:`, see the example in [2.1](#21-finding-the-setup-code); the repository's `deploy/docker-compose.yml` has
> the line prepared) overrides the switch: the card then shows the variable's value, and saving reports an error.
> After **Forget** („Vergessen“) you always have to confirm the new fingerprint, no matter how this is set – until
> then Nodvard Deck no longer connects to the server.
> Keys that have already been remembered are never affected. The cleaner way is always: check and confirm first.

### E.7 Replace access, delete, groups

- **Replace:** Card SSH access → **Replace** („Ersetzen“) creates a *new* login (generate key, password or your own
  key); the old one stays the default. Run the new login's command on the server, press **Check new access**
  („Neuen Zugang prüfen“) and then **Use new access** („Neuen Zugang verwenden“). Only after a successful, fresh check
  (at most 10 minutes old) is the old login deleted – that way you do not lock yourself out. If the old login was a
  key, **its public key stays on the server in `~/.ssh/authorized_keys`**; remove it there yourself if needed. For a
  password, nothing changes on the server. Checking the new login does not change the confirmation of the old one
  (3.4); only once you use the new one does its check count.
- **Delete access:** Terminal, updates and monitoring then no longer reach the server. A key stays registered on the
  server; for a password, nothing changes there.
- **Groups:** On the list under **Groups** („Gruppen“) you create, rename and delete them; on the server's page you
  assign them with a tick. "Scripts" („Skripte“) can run a script on all servers of a group.
- **Delete server:** Card "Danger zone" („Gefahrenbereich“) (or "Delete" („Löschen“) on the server page). For safety,
  type the short name. The SSH login and the remembered server key are deleted as well; histories in Nodvard Shield stay
  with the old name. Servers read in from Proxmox reappear at the next sync – then without an SSH login. If an
  action is currently running on the server, deletion is not possible.

## F. Root rights without a password and the docker group

<a id="6-root-rights-without-a-password-and-the-docker-group"></a>

If Nodvard Deck does **not** log in to a server as root (e.g. on the Pi), it runs root commands via `sudo -n`, that is,
without a password prompt. If sudo asks for a password, the function fails with "Keine root-Rechte: Das Dashboard
ist auf diesem Server nicht als root angemeldet und darf sudo nicht ohne Passwort nutzen." (no root rights: the
dashboard is not logged in as root on this server and may not use sudo without a password).

You do **not** have to change the setup command ([3.3](#33-run-the-setup-command-on-the-server)) by hand for this: the page sets the appropriate tick
boxes itself, because the extensions report what they need. The same tick boxes apply when you set up the server with
a password (Nodvard Deck then runs the same command).

- **"Root rights without a password"** („Root-Rechte ohne Passwort“) – needed for (the page names it behind the tick
  box): parts of Nodvard Shield (moving files into quarantine, restoring or deleting them, the hardening audit with
  Lynis, installing tools such as ClamAV and Fail2ban, installing updates and rebooting, blocking addresses with
  Fail2ban; without root, intrusion protection and the file watcher can read only partly), restarting services (Server
  status) and Docker in "operate only" mode (servers with the tag `docker-bedienen`).
- **"Add to the docker group"** („Zur Gruppe docker hinzufügen“) – needed for the Docker module and the AI container
  watch of Nodvard Shield; they call `docker` without sudo. The group is only entered if it exists on the server. For a
  server tagged only `docker-bedienen` the page does not offer the group; the helper script is enough there.

Without either tick box you get the **lean variant**: its own user, key only, no root rights. That is enough for
Terminal, Files and Scripts.

**What the "Root rights" tick box sets up:** By default only the modules' helper scripts (Nodvard Shield, Server status
and Docker in "operate only" mode), each with a narrow sudo rule of its own; there is no full sudo rule then. The full
rule (`<user> ALL=(root) NOPASSWD: ALL`) is only added if a module needs root and has no helper script, or if you tick
"Full rights" („Volle Rechte“) under "Advanced" („Erweitert“). If no module needs root on the server, the tick box
sets up no sudo at all.

**This is practically root – and the page tells you so too:** Anyone who is in the `docker` group or has the full
sudo rule can do practically anything on the server. Without a helper script Nodvard Deck starts some root commands as
`sudo -n sh -c '…'`, so it then needs the full sudo rule, and anyone who may start `sh` as root may do anything. The
security comes from the fact that **only** this one user gets the rights, can log in **only with the key**, and the key
is stored encrypted in Nodvard Deck.

**Proxmox nodes:** There, Nodvard Deck is easiest to log in directly as `root` (enter user `root` when generating).
Then it needs neither sudo nor tick boxes. The key ends up in `/root/.ssh/authorized_keys`, which on Proxmox is a link
into the cluster folder – so it then applies on **all nodes of the cluster**.

**Using an existing user** (on the Pi e.g. `admin`): enter that name instead of `nodvard` when generating. Running
`sudo -n true && echo geht-schon` on the server shows whether it may already use sudo without a password (on
Raspberry Pi OS this is often the case for the first user).

Everything in detail – what each module does as root, what the helper scripts may do (with a table), how the sudo rule
is checked, how to get rid of a full rule again, switching Docker to "operate only" and what the file watcher reports
after the setup: **[Root rights and helper scripts](ROOT-RIGHTS.md)**.

## G. Modules in detail

### G.1 Without Proxmox in detail

- **Status (online/offline):** Nodvard Deck checks servers created by hand **by itself every 2 minutes**
  (Settings → Servers & access → "Check reachability" („Einstellungen → Server & Zugänge → Erreichbarkeit prüfen“);
  switch and interval from 1 to 60 minutes). It only checks whether the server's SSH port (default 22, otherwise the
  port of the login) accepts a connection: without logging in, also for servers without a login. A server is only
  considered "unreachable" („nicht erreichbar“) after **two failed attempts in a row**; "back again" counts immediately.
  When a server goes down (or comes back), there is **one notification** with a link to the server page, optionally
  also as a push; the cockpit shows it under "Needs attention" („Braucht Aufmerksamkeit“). During a **maintenance
  window** there is no push (only the history); if the outage continues after the window, the notification is
  delivered once afterwards. A server that has **never answered** (for example a typo in the address) simply shows as
  "unreachable", without a notification. Not checked are servers that a module reads in itself (such as Proxmox
  guests), sample servers, servers in the "Maintenance" („Wartung“) state, and servers whose default login is not SSH.
  A server without an SSH service (for example a device with SSH turned off) therefore permanently shows
  "unreachable"; if you do not want that, turn off the check on its page. "Check connection" („Verbindung prüfen“)
  still exists for the precise test including login. For unchecked servers, the cockpit shows "not checked
  yet" („noch nicht geprüft“).
- **Utilization and history** (CPU, memory, disk, network, temperature) come from the **Server status** module („Server-Zustand“, called "System" up to version 0.8.0) via SSH, only
  for Linux servers with an SSH login. Without this module the server page shows a notice instead of the rings and
  curves. The history is measured every 30 seconds and is still empty for the first few minutes. If the server has
  never answered or the login is not confirmed yet, the server page says so instead of letting you wait for curves
  (if you may change servers, with the link "To access" („Zum Zugang“)). In the cockpit, a server with an SSH login
  that is "unreachable" („nicht erreichbar“) and has never answered shows „Noch keine Verbindung: prüfe zuerst den
  Zugang“ (no connection yet: check the access first).
- **Utilization in the cockpit:** In the "Infrastructure" („Infrastruktur“) area, every Linux server with measurements
  gets a card with rings for CPU, memory and disk, just like the Proxmox nodes. The numbers come from the history that
  is already being kept (latest measurement, every 30 seconds); the cockpit triggers **no** SSH connection when it
  loads and asks for all servers in a single query (`GET /hosts/metrics/latest`). If the latest measurement is older
  than 2 minutes, it says "stale" („veraltet“) with the age underneath and the rings are muted; after 10 minutes
  without a measurement the server becomes a plain row again. A server that is "unreachable" does not show old values
  as current. Servers without measurements (Windows, no SSH login, Server status module off, measurements turned off for the
  server) stay a single row, with a note on why where that fits. Running Proxmox guests (VMs and LXC) show CPU, memory
  and "running since" („Läuft seit“). LXC containers and Proxmox nodes also show the disk. For VMs it stays empty,
  because Proxmox reports no used space for them. Stopped guests stay a single row. The collector fetches the values
  every 30 seconds with one query per Proxmox connection.
- **Docker containers:** Docker module, for servers with the tag `docker`. There you start, stop and delete
  containers (only stopped ones are deleted; volumes and the image stay), read their logs live, open a shell in the
  container with "Console" („Konsole“) and apply newer images ("Apply" – „Einspielen“, only for Docker Compose
  services). If the new version crashes on start, the Docker module usually goes back to the old one by itself –
  never for databases and other data stores; can be switched off under "Configure" („Bei Fehlstart automatisch zurück“).
  After "Start", "Restart" and "Restart stack" Deck checks for up to a minute whether the containers really
  run and keep running (without a healthcheck: twice, 15 seconds apart, without a new start). If a container crashes
  right away, the action counts as failed and says why (e.g. „beendet, Exit-Code 1“). For a stack only the
  containers that ran before count. A single restart (for example because a dependency was not ready yet) and an end
  with exit code 0 (containers for one-off tasks) do not count as failures, they are only mentioned; "unhealthy" only
  counts if it stays that way until the end.
- **Apps in the cockpit without the Docker module:** In the "Apps" area you create your own tiles with **+ Add app**
  („+ App hinzufügen“) – name, address (`http://192.168.1.1`), icon (from a selection or an emoji), color, group and,
  if you like, the server it belongs to. That way routers, the NAS interface or Pi-hole also appear in the cockpit,
  without code and without container detection; detected containers appear alongside when the Docker module is
  running. Via the menu (⋮) on the tile you edit, move or delete it, and at the top you filter by group. The owner
  and administrators may create and change them (privilege `apps.write`); everyone who can see servers can see them.
  Nodvard Deck never calls the addresses itself (so it shows no "running/not running"), allows only `http://` and
  `https://` and no credentials in the address; icons are never image URLs.
- **"Apps" page** (in the menu on the left): all tiles in one place, your own apps in **folders** (their group) that
  you can collapse. With `apps.write` you drag a tile onto another one (sorted in before it) or onto a folder heading
  (to the end of that folder); without a mouse via the menu (⋮). Under **"Suggestions"** („Vorschläge“) you find
  addresses your modules already know – for example the proxy hosts from Nginx Proxy Manager – that are not an app
  yet; **"Take over"** („Übernehmen“) pre-fills the dialog, the app is only created with "Save". Detected containers
  with a web interface are not listed there but under **"Detected services"** („Erkannte Dienste“); there, **"Add as
  app"** („Als App übernehmen“, only for running containers) turns one into a tile of its own. Both lists come from
  the same read of the Docker servers. Only while the tile of a container is not visible (for example because the page
  has not loaded it yet) does it show up briefly under "Suggestions".
- **Updates, antivirus, intrusion protection:** Nodvard Shield, for all Linux servers with an SSH login. The AI container
  watch in it is optional; without an AI server it still shows crashed containers, with the cause determined by the
  system (for example out of memory or killed from outside) and two fixed sentences: what it means and what you can
  do. With AI it proposes at most a
  restart of a crashed container, which you find under "Actions" („Aktionen“); other ideas from the AI only appear
  as text in the status report of the container watch. If it suggests another command, the status report only
  mentions that; you see the command itself in the audit log if you have server rights (`hosts.execute`).
  A tap on the push message of the status report opens exactly this proposal under "Actions" (highlighted, even if it
  is already done); without a proposal it opens the "Container watch" tab („Container-Wache“). You still have to log in.
  If a container runs quietly for five minutes after a crash, the container watch sets its incident to „Erledigt“
  (resolved) by itself („Läuft wieder seit 04:12 – von selbst erledigt.“, no push message, logged in the audit log).
  If a proposed restart is still waiting under "Actions", the note says it is no longer needed; you reject it there
  yourself. If the container crashes again within the quiet period, the same incident is open again, as long as nobody
  had handled it and no restart for it has run yet; otherwise there is a new incident with a message, even within the
  quiet period (the last word was "running again"). The five quiet minutes
  are only counted while the dashboard runs: after a dashboard restart an open incident stays open until the container
  had another disruption and then ran quietly, or until you tick it off.
  If a container crashes again within six hours after an approved restart of exactly this container (and did not run
  quietly in between), the next message says „nginx auf docker ist nach dem freigegebenen Neustart um 03:10 wieder
  abgestürzt.“ (crashed again after the approved restart) – even within the quiet period in which no new message would
  come otherwise.
- **Choosing servers, files:** On the "Server status" and "Terminal" pages the servers appear as tiles with name, address,
  system and status (traffic-light dot). On "Server status" they also show the load ("CPU 12 % · RAM 48 %", up to 5 minutes
  old) and a finding such as an almost full disk as a hint; the server chosen last is preselected on the next visit
  (the browser remembers it). In the file manager every server with an SSH login appears as a source (only with the
  privilege `hosts.execute`); the sidebar shows next to it whether it is reachable and which system runs on it.
  Sources without a server, such as Nextcloud, have neither. Without the right to see servers (`hosts.read`) only the
  dot and the system are missing. Text files (configuration, logs, scripts) and images open with **"View"** („Ansehen“)
  right above the list; for large files the preview shows the first 256 KB. HTML appears as text; SVG and PDF files you
  fetch with "Download" („Laden“).
- **Console in the browser** (a guest's screen) exists only for Proxmox guests. For every other server you use the
  **Terminal**.
- **Backups:** The Proxmox-Backups module shows only the backups of your Proxmox servers. You back up the dashboard itself under Settings →
  System ([6.](#6-back-up-the-dashboard-itself)).
  With „Als Kopie wiederherstellen“ (restore as a copy) you get a guest back from a backup there: as a new guest with a
  new number that does not start by itself; the original stays untouched. Every copy is created in the pool
  `nodvard-kopien` (only there does the token need the privilege to create guests; VMs need Proxmox VE 7.2 or later).
  Nodvard Deck disconnects the network cards once Proxmox has finished (otherwise the backup watch catches up and
  reports back). The pool and the token privileges are in
  [PROXMOX-TOKEN](PROXMOX-TOKEN.md#restore-as-a-copy-backups).

### G.2 Proxmox in detail

In detail in **[PROXMOX-TOKEN.md](PROXMOX-TOKEN.md)**, the way with a password there under
[quick way](PROXMOX-TOKEN.md#quick-way-connect-with-a-password). If you connected with a password and left "Also connect
Proxmox backups" on, step 3 below is not needed. By hand:

1. On every Proxmox server (in the example pve1 **and** pve2), create a user `nodvard@pve` with token `dashboard`
   and a suitable role.
2. Settings → Extensions → "Proxmox VE" → "Configure" → "Add server" („Einstellungen → Erweiterungen → Proxmox VE →
   Konfigurieren → Server hinzufügen“) (short name („Kurzname“) `pve1`/`pve2`, address („Adresse“) `https://…:8006`, API
   token ID („API-Token-ID“) `nodvard@pve!dashboard`) → "Save" („Speichern“) → enter the secret under "Credentials"
   („Zugangsdaten“).
3. Do the same under "Proxmox-Backups", with the same short names.
4. After 5 minutes at the latest, nodes, VMs and containers are there. VMs without a guest agent get the Proxmox
   address as a placeholder – enter the correct IP under Settings → Servers & access
   („Einstellungen → Server & Zugänge“) ([3.1](#31-create-a-server)).
5. The modules also raise an alert on their own when a storage is almost full (Proxmox VE, from 90 %, adjustable) and
   when the last successful backup of a VM is older than 3 days (Backups, adjustable, at least 2 days); both under
   "Configure" („Konfigurieren“) of the module. If the backup job of a VM runs less often (for example only on
   Saturdays), the gap between its runs applies automatically when it is longer; 6 hours of slack are added, and
   no alert is raised while a backup is running. Proxmox VE also reports a ZFS pool that is no longer ONLINE (degraded
   as a warning, faulted as "critical") and a disk that stays too hot for 10 to 15 minutes (hard disks from 55 °C, SSD
   from 65 °C, NVMe from 70 °C, adjustable under "Advanced"). A pool that disappears completely (for example not imported
   after a reboot) is reported as well. The ZFS pools also appear on the node under "Node details".

### G.3 Managing extensions

Under Settings → Extensions the modules are listed in the same groups as in the wizard; the sample modules for
developers ("Hello World") are at the very bottom under "For developers" („Für Entwickler“). If a module has read in
servers (Proxmox, for example), the confirmation when switching it off names their number: the servers stay in the
list, in the cockpit they carry "Module off" („Modul aus“), and their state is no longer updated. You can delete them
under Settings → Servers & access.

### G.4 Address check (Nginx Proxy Manager)

On the "Nginx Proxy Manager" page each proxy host shows which Docker container
it leads to and whether it runs, e.g. „Führt zum Container immich_server auf docker – der ist gestoppt.“ (leads to the
container immich_server on docker, which is stopped). If it is stopped, the button „Container starten“ starts it through
the approval (for users with `hosts.execute`). The mapping uses the server address with port (also for stopped
containers; ports bound only to `127.0.0.1` do not count), `host.docker.internal` on the server Nginx Proxy Manager
itself runs on (`localhost` only if it runs in host network mode, otherwise that is Nginx Proxy Manager itself), and the
container name or Compose service.
Names only count on the server of Nginx Proxy Manager (Docker resolves them only in its own network); if that server is
unknown, the result says „vermutlich“ (probably), without red and without a button. The Docker module must be switched
on for this, it knows the containers. A target that matches no container (e.g. the Proxmox web UI) is shown in grey and
is not an error. Not detected: containers in host network mode (they show no ports), network aliases and additional
locations of a proxy host.

### G.5 Synology DSM

Setting up, several devices, notifications and Hyper Backup: [Synology DSM](SYNOLOGY-DSM.md).

### G.6 The widgets on the start page

Modules bring their widgets along; they are arranged in groups (Servers and Proxmox,
Security, Docker and apps, Backups, Services – „Server und Proxmox“, „Sicherheit“, „Docker und Apps“, „Backups“,
„Dienste“). Clicking a group's name collapses it; a coloured dot next to it then reports a warning, a fault or a widget
that currently does not load. With **"Edit"** („Bearbeiten“) above the widgets you arrange everything: drag cards by their
handle, choose height (Automatic, Small, Medium, Large – „Automatisch“, „Klein“, „Mittel“, „Groß“), width (Narrow, Wide)
and group, remove widgets and rename groups. Only **"Done"** („Fertig“) saves everything at once, **"Cancel"**
(„Abbrechen“) discards the changes; you bring a removed widget back via **"Manage widgets"** („Widgets verwalten“). On a
phone the cards are stacked: height, group and removing work there too, moving and width only on a larger screen.

## H. Automation and maintenance windows

<a id="9-automation-and-maintenance-windows"></a>

Settings → **Automation & security** („Einstellungen → Automatik & Sicherheit“):

- **Autonomy** („Selbstständigkeit“): "Suggest only" („Nur vorschlagen“) is the default and right for the beginning –
  every action Nodvard Deck proposes on its own waits for your approval under "Actions" („Aktionen“). "Act autonomously" („Selbstständig handeln“)
  lets actions up to the chosen risk level ("Allowed without asking up to risk level" – „Ohne Rückfrage erlaubt bis
  Risikostufe“) run without asking. Under "Actions" („Aktionen“), an action's command appears right in its row
  (without the privilege `hosts.execute` and without the right to approve its risk level, only a notice appears
  there); invisible or redirecting characters in it (such as a reversed writing direction) appear as a visible marker
  like `⟦U+202E⟧`. "Approve selected" („Ausgewählte freigeben“) shows all commands in full in its confirmation, but
  leaves out actions with high or critical risk; you confirm those one by one.
- **Triggered by you:** If you click a button such as "Delete permanently" („Endgültig löschen“, Nodvard Shield) or
  "Restart" („Neustart“, Docker) yourself and you have the approval right for the action's risk, confirming the
  dialog already is the approval: the action runs right away. It still appears under "Actions" and in the log, just not
  under "Waiting for confirmation" („Wartet auf Bestätigung“) but under "All" („Alle“), "Succeeded" or "Failed".
  Without that right it waits under "Waiting for confirmation" for someone who has it. Exceptions: "Propose all
  updates" („Alle Updates vorschlagen“) deliberately only proposes (the proposals wait), when applying an image update
  the overview is the confirmation, and "Quarantine" („In Quarantäne“), "Ignore" („Ignorieren“) and "Watch again"
  („Wieder beachten“) in Nodvard Shield run without an action (log only).
- **Scripts without a click**: Independently of this, an active script with a schedule can get a standing approval
  („Dauerfreigabe“): on the Scripts page („Skripte“), turn on the switch "Without approval on schedule" („Ohne Freigabe
  nach Zeitplan“) for the script (owner and `admin` only). Beforehand you see which servers, accounts and addresses it
  covers. Any change to content, parameters, target or schedule, and a different account, address or SSH port on one of
  these servers, cancels it; new servers that join a group or "All servers" („Alle Server“) still ask. If the person
  who granted it is deactivated or no longer has the rights for it, the runs wait for your click again as well. Every
  run still appears under "Actions" („Aktionen“) and in the audit log, and "Withdraw approval" („Freigabe
  zurückziehen“) takes effect immediately.
- **Failed runs on a schedule:** If a scheduled script run fails (on the server with an error, rejected by the
  dashboard, or not started at all), it appears in the script's history as failed, and you get a notification (warning
  level, with a link to the Scripts page) – **once per error**: the same notification only comes again when the error
  changes or the script has run through in between, not on every run. The text names the servers but never the output;
  that is under "Actions" („Aktionen“). If you start a script by hand you see the result yourself, so there is no
  notification for that. If a script ran nowhere because all servers were skipped, the existing notification about
  skipped servers already says so.
- **Blocked commands** („Gesperrte Befehle“): your own patterns (regular expressions) that are never executed. The
  dangerous default cases are always blocked.
- **Maintenance windows** („Wartungsfenster“): "Add window" („Fenster hinzufügen“) → start, duration, "Applies to"
  („Gilt für“) (All servers / Selected servers) → "Save". During the window, push notifications about these servers
  are muted; they still appear under "Notifications". The time applies in the configured time zone (Settings →
  System). Only notifications that belong to specific servers are muted: those from **Proxmox-Backups**, **Game servers**, the **Proxmox watcher** (nodes, disks,
  connection) and **Nodvard Shield** (updates, hardening audit, intrusion-protection warnings, status report of the
  container watch). A report covering several servers stays audible as long as even one of them is not in the window.
  Always audible: **malware findings** and **critical intrusion-protection events** (the deep scan runs on Sundays at
  03:30 by default, right in the middle of the suggested window), the **morning briefing**, and the notifications of
  the certificate watcher and scripts.
  Good to know: If something fails during the window for Proxmox-Backups, Game servers or the Proxmox watcher and is still
  failing after the window, the notification is **delivered once afterwards as a push**, with "(seit dem
  Wartungsfenster)" (since the maintenance window) in the title. This also applies to a new join code of the game
  server, which changes for example at a nightly restart. If everything is already back to normal within the
  window, nothing is delivered afterwards; under "Notifications" the outage and the all-clear then appear silently. If the all-clear is
  only noticed after the window, it comes as a push with a note that the problem occurred during the maintenance
  window. If the outage came as a push and only the "back to normal" falls into the window, it stays silent in the
  window and is then delivered once as a push, so that the notification does not stay open on your phone.
  Intrusion-protection warnings from Nodvard Shield are not delivered afterwards – they remain as open events in the
  intrusion protection, and the morning briefing mentions them.

**Automatic updates** (Nodvard Shield): Settings → Extensions → "Nodvard Shield" → "Apply updates automatically"
(„Updates automatisch einspielen“) is **off** by default, as is "Then restart automatically if necessary"
(„Danach automatisch neu starten, wenn nötig“). Only turn it on once the update center has run cleanly for a while.
The round handles the server that Nodvard Deck itself runs on last; its restart only comes after the summary has been
sent (with a short notification of its own).
You can exempt single servers from the automatic restart: directly below "Then restart automatically if necessary" you
choose the servers under "Never restart these servers automatically" („Diese Server nie automatisch neu starten“), for
example a NAS or a game server. They still get their updates, only the restart is skipped. If such a server needs a
restart afterwards, the summary says so ("Neustart nötig, für diesen Server aber ausgeschaltet – bitte selbst neu
starten", i.e. restart needed, but switched off for this server – please restart it yourself), and you restart it
yourself. The update center names the exemptions on its badge ("Neustart danach: automatisch, außer …", i.e. restart
afterwards: automatic, except …). If the server Nodvard Deck itself runs on is in the list, it does not restart
either.
If the last update on a server failed, was cut off or has not finished yet, Deck does not restart it either, not in the
following nights as well: half-configured packages and a restart do not go well together. The summary then says
„Neustart nötig, aber ausgelassen“ (restart needed, but skipped). It comes as a warning, and so under "Needs attention"
(„Braucht Aufmerksamkeit“) in the cockpit, only once per failed run; in the following nights the sentence stays in the
summary as info. If the run failed, the Nodvard Shield overview also shows „Neustart
wartet auf ein sauberes Update“ (restart waits for a clean update) there until a run works again. Check in the update
center what went wrong; after the next clean run, Deck restarts the server by itself again. If nothing is left to
install, restart the server yourself.

**Propose all updates at once:** in the update center, "Propose all security updates" („Alle Sicherheitsupdates
vorschlagen“) or "Propose all updates" („Alle Updates vorschlagen“) creates a proposal for every matching server;
a confirmation lists the servers first. Servers with an open update proposal or an update that is being applied are
skipped. You then approve under "Actions" („Aktionen“), together with "Approve selected" („Ausgewählte
freigeben“); proposals with high risk (with "Proxmox: all updates as dist-upgrade") one by one. If autonomy is set to
"Act autonomously" („Selbstständig handeln“), the proposals start without asking up to the configured risk level.

**Updates delayed by phasing (Ubuntu):** Ubuntu rolls out some updates gradually, first to a share of machines, then to
all. Until a server's turn comes, apt holds the update back, also with "Apply all" („Alle einspielen“). The update center
does not count these updates as installable and adds "X delayed by phasing" („X durch Phasing verzögert“); clicking it explains
it, and the package list marks them as "delayed" („verzögert“). This is intended, not an error: once the server's turn
comes, they install like any other update. The check needs no root and does not depend on the server's language; Debian and
Proxmox have no phasing, so the note does not appear there. If the state shown still comes from a version without this
check, it says "Phasing not checked yet" („Phasing noch nicht geprüft“) until "Check" („Prüfen“) or the daily check
renews it. If apt cannot work out the trial installation (for example because of missing dependencies), it says
"Phasing check not available" („Phasing-Prüfung nicht verfügbar“) instead of a number.

**Kept-back and held updates:** "Apply all" („Alle einspielen“) also installs updates with new dependencies, but never
removes a package. If an update needs that (for example when one package replaces another, like the camera packages of
Raspberry Pi OS), apt keeps it back. The update center shows such updates as "kept back, needs full-upgrade"
(„zurückgehalten, braucht full-upgrade“) and does not count them as installable. You can install them on the server with
`sudo apt full-upgrade`; read the list apt shows first, because whatever is listed under "REMOVED" („ENTFERNT“ in German) will be removed. On
Proxmox with the `dist-upgrade` setting, "Apply all" includes them. Packages held with `apt-mark hold` show as "held"
(„gesperrt“); `sudo apt-mark unhold <package name>` lifts the hold. "Apply security updates" („Sicherheitsupdates
einspielen“) leaves out all three kinds, including updates delayed by phasing: installed directly, a kept-back security
update would remove another package, a held one would abort the whole run and a delayed one would bypass phasing. For
this, the update center checks the server once more right before the run. If it says "Phasing check not available", it
is unknown what apt keeps back; "Apply security updates" is then not offered until the next check gets through. If
nothing is installable and several reasons are mixed, the badge says "not installable" („nicht einspielbar“), with each
reason listed next to it.

**Updates on the server that runs Nodvard Deck:** if the updates include a package that restarts Docker (`docker-ce`,
`docker.io`, `containerd` and similar), the check also looks whether Nodvard Deck runs there. The confirmation before
applying then says so: Nodvard Deck is briefly unreachable afterwards and reloads on its own. Applying continues on the
server even if Nodvard Deck restarts meanwhile. The page remembers the run and shows its result once it is done, also
after a reload. Under "Actions" („Aktionen“) the action then shows as "interrupted by restart"; what really happened is
in the update center's history and in the push message. While Nodvard Deck is unreachable, the tab says so and retries
on its own every few seconds.

## I. Updates

### I.1 Finding and installing a new version

<a id="105-finding-and-installing-a-new-version"></a>

Under **Settings → System → "Updates"** („Einstellungen → System → Updates“) you can see which version is running and
which one is the newest. Nodvard Deck looks **once a day** at ghcr.io to see which images exist; **"Search now"**
(„Jetzt suchen“) does it right away (at most once a minute).

- **Privacy:** ghcr.io belongs to **GitHub**, and so does github.com, where the search fetches the approval of a version
  (downloads go through `objects.githubusercontent.com` or `release-assets.githubusercontent.com`; if you filter,
  allow these addresses). On every search, GitHub sees the IP address of your connection and the
  time; nothing else is sent, not even the installed version. **You can turn the search off:** switch off "Search for
  updates automatically every day" („Täglich automatisch nach Updates suchen“; permission `settings.write`), and the
  dashboard never looks on its own. "Search now" still asks when the switch is off – anyone with the permission
  `system.read` can press it, and GitHub sees that request too.
- **Which versions:** "Only finished versions" (default) or "Also pre-releases (beta)". Pre-releases (e.g. `0.7.0-rc1`)
  are not under `:latest`: to install one, put exactly `ghcr.io/nodvard/deck:0.7.0-rc1` at `image:`.
- **Without internet** the card says "Couldn't check (offline?)" and shows the last known state; that is not an error.
  "None found" („Keine gefunden“) means the query worked, but there is no version in the chosen channel yet.
- **Installing** is done by your environment, not by the dashboard, unless you use the update helper (one button, see
  [appendix I.2](#i2-update-with-one-button-update-helper)). The card has instructions as tabs: Docker Compose, Docker
  Desktop, Portainer ("Update the stack" with "Re-pull image"), Synology Container Manager, Unraid ("Check for
  Updates" → "Apply update") and `scripts/deploy_pi.sh`. With Compose (installed as in [section 1](#1-quick-start)),
  in the folder with your `compose.yml`:

  ```bash
  docker compose pull
  docker compose up -d
  ```

  On Linux you may need `sudo` in front. If your file has a different name (e.g. `compose.standalone.yml` from an older guide), add `-f`
  and the file name (`docker compose -f compose.standalone.yml pull`).

  If a fixed version (e.g. `:0.5.0`) or a series like `:0.5` is set at `image:` instead of `:latest`, put the new
  version there first.
- **Going back:** write down the current version before the update (it is shown on the card). To go back, put it at
  `image:` again and restart (`docker compose up -d`). Before rebuilding the database, Nodvard Deck automatically makes
  a copy at start (only with SQLite, the default database). If the new version never started properly, the old one
  restores this copy by itself. If the new version already ran and rebuilt the database, the old one shows a
  **rescue page**: there, with the emergency code (in the container's log), choose „Stand vor dem Update
  wiederherstellen“ (restore the state before the update); whatever was changed since the update is lost. Or put the
  new version back. Details in [appendix I.3](#i3-the-copy-before-the-migration) and
  [appendix K](#k-nodvard-deck-does-not-start-the-rescue-page).
- If a **self-built** image is running (e.g. from the PC via `scripts/deploy_pi.sh`), the card says so; it is then
  updated the same way.

### I.2 Update with one button (update helper)

<a id="106-update-with-one-button-update-helper"></a>

With the **update helper** you install updates under Settings → System → "Updates" („Einstellungen → System →
Updates“) with one button and can go back once within 7 days, also with one button. The helper is a small service of
its own in the same Compose file, which you switch on deliberately. It has access to Docker, which is as much as root on
this machine. Setting it up, updating and removing it: [deploy/README.md, section "Update-Helfer"](../../deploy/README.md#update-helfer)
(in German).

- **State:** the "Updates" card shows "Update helper: not set up" („Update-Helfer: nicht eingerichtet“, with a link to
  the guide), "ready" („bereit“, with version and last sign of life), "not ready" („nicht bereit“, with the reason and
  what helps) or "not responding" („antwortet nicht“).
- **Requirements for the button:** the helper is ready, a newer finished version exists, the official image
  `ghcr.io/nodvard/deck` runs with `:latest` or a matching series such as `:0.7` (not a fixed version), and you are the
  owner of the installation. The helper only accepts versions from 0.7.0 on, so the first update with the button goes
  from 0.7.0 to the version after it. If something is missing, the card says why, where that helps.
- **Release approval:** the button only offers a version that is approved at its release (signed with the project's
  key); until then the card says there is no valid approval for version X yet („Für Version X liegt noch keine gültige
  Freigabe vor …“), and updating by hand still works. A current helper checks the signature itself, the card then shows
  that an approval is present („Freigabe liegt vor (Schlüssel …)“). With an older helper the card asks you to renew it.
- **Update:** "Update now to X" („Jetzt aktualisieren auf X“), then enter your password and, if two-factor login is on,
  the code from the app. If the new version rebuilds the database, Nodvard Deck makes a copy first as always
  ([appendix I.3](#i3-the-copy-before-the-migration)). The card shows the steps; you can close the page, the helper
  keeps working without it. While switching over, the dashboard is gone for a few minutes; the page keeps asking and
  reloads after success. If it takes longer than 30 minutes (slow download), the card keeps asking and shows the result
  as soon as it is there.
- **If it fails** (the new version exits, keeps restarting, shows the rescue page or is not ready after 15 minutes), the
  helper switches back to the old version by itself. The card says why and what you can do; important results also
  arrive as a notification.
- **Going back:** for 7 days the "Copies before updates" („Kopien vor Updates“) card shows "Back to version X"
  („Zurück zu Version X“), once. If the new version rebuilt the database, the data goes back too: the card first names
  the time since which everything is lost and asks for a checkbox. The replaced state stays under `restore/replaced-…`
  for 30 days. If it is unclear what happens to the data, there is no button, only the explanation. After going back,
  the helper does not install the version you left for 24 hours; the card says from when it works again. If a way back
  that should have taken the data along failed, the notification (bell) says whether the database had already been reset.
- Update and way back together work at most once in 10 minutes. Without the helper, the guides from
  [appendix I.1](#i1-finding-and-installing-a-new-version) apply.

### I.3 The copy before the migration

<a id="104-updates-the-copy-before-the-migration"></a>

Before every update that rebuilds the database (a **migration**), Nodvard Deck automatically makes **a copy of the
database** at startup: `backups/vor-update/<time>_<from>_<to>.db` in the data folder. The **three newest** are kept.
You can see them under **Settings → System → "Copies before updates"** („Einstellungen → System → Kopien vor Updates“).

- **No copy, no migration.** If there is not enough space (a good 1.2 times the size of the database is needed),
  Nodvard Deck does not start but shows the rescue page with the amount that is missing (see
  [appendix K](#k-nodvard-deck-does-not-start-the-rescue-page)). Free up space, press "Try again" („Neu versuchen“).
- **If the migration fails,** Nodvard Deck resets the database to the copy by itself. Afterwards the rescue page runs;
  the data is as it was before the update. The half-finished state is discarded in the process; it contains nothing
  that is not also in the copy.
- **If the migration breaks off midway** (power loss, container restarted), the next start first resets the database
  to the copy and then migrates again. The half-finished state is kept for 30 days under `restore/replaced-…` (it is
  only discarded if the database is on a drive of its own and there is not enough space for it in the data folder). If
  the **very first** migration of a new installation breaks off (still without an account), the next start puts the
  half-finished state aside there and begins with a fresh database.
- **Going back to the old version** (set the image version in the Compose file back, restart the container):
  - If the new version **never started successfully**, the old one restores the copy **by itself**. No data was lost, after
    all; the newer data is nevertheless kept for 30 days under `restore/replaced-…` (newer data is never deleted when
    resetting).
  - If the new version **has already been working**, the old one shows the rescue page ("The data is newer than this
    version" – „Die Daten sind neuer als diese Version“). With the rescue code you can choose **"Restore the state before
    the update"** („Stand vor dem Update wiederherstellen“) there: everything since the update is lost (the newer data is
    kept for 30 days under `restore/replaced-…`).
- **The way back only works from a version with this feature.** An old version (up to 0.5.x) cannot restore a copy by
  itself and does not know the rescue page. So both the previous version and the new version must be at least the
  version with "copy before every migration" for the way back to be open. The first update **to** this version already
  makes the copy.
- **Not in the copy** are files outside the database (data of the extensions, keys). Migrations do not touch them. For
  everything else there is the backup ([6.1](#61-in-the-interface-recommended)).
- **Emergency exit** (not recommended): `NODVARD_DECK_SKIP_PRE_MIGRATE_BACKUP=1` in the Compose file turns the copy off,
  so that migration also runs when there is too little space. If the migration then fails, there is no way back.
- What was put aside (`restore/replaced-…`: the old state from a restore, or a database that a rollback replaced) is
  deleted automatically after 30 days.
- **Database on a drive of its own** (your own `NODVARD_DECK_DATABASE_URL`, e.g. a USB SSD): to put it aside, it is
  copied into the data folder. That requires as much free space there as the database is large; otherwise the rescue
  page shows "not enough space" with the numbers, and nothing was changed. In this case only the half-finished state
  of an aborted migration is discarded by the start, instead of getting stuck on the rescue page.
- **Restoring a backup whose migration breaks off** (power loss in the middle): the next start rolls back the restore,
  and exactly the old state with its keys applies again. It is never replaced by the backup afterwards.
- With a database other than SQLite there is no copy (please back up yourself beforehand).

## J. When something goes wrong

<a id="11-when-something-goes-wrong"></a>

The most common hurdles at the start (dashboard not reachable, server will not connect, locked out) are in
[section 7](#7-common-stumbling-blocks). This is the rest.

**"HTTP 401" or "Not authenticated" („Nicht authentifiziert“) on an extension page** (Proxmox, Proxmox-Backups, Scripts …): In
the visible tab, Nodvard Deck renews the login by itself. Extension pages, downloading and uploading on the Files page,
uploading a backup and exporting the audit log also renew an expired login by themselves and try once more. If the
message still appears, reload the page (F5).

**"HTTP 500" somewhere:** Look at the log (in the folder with the Compose file), the reason is there:

```bash
cd ~/nodvard-deck && sudo docker compose logs --since 15m nodvard-deck | grep -iE -A5 "error|traceback"
```

**"Seite nicht gefunden" (page not found):** The address does not exist (any more), e.g. an old bookmark; "Zur
Übersicht" (to the overview) leads to the cockpit. **"Die Seite konnte nicht geladen werden" (the page could not be
loaded)** usually appears right after an update: reload the page (F5). With **"Hier ist etwas schiefgegangen"
(something went wrong here)** reload as well; if it stays, "Technische Einzelheiten" (technical details) names the
error, and the log (see above) helps further.

**Proxmox unreachable** (push "Proxmox 'pve1' nicht erreichbar" (unreachable), tiles with "nicht erreichbar" or "Nicht
abrufbar: …" (not available)) – the error message usually gives the reason:

- "Proxmox ist nicht erreichbar (Verbindung abgelehnt oder kein Weg dorthin) …" (Proxmox is not reachable) or "Proxmox
  antwortet nicht (Zeitüberschreitung) …" (Proxmox does not answer, timeout): server off, wrong address or port. The
  address needs `https://` and `:8006`.
- "Das Zertifikat von Proxmox wird nicht akzeptiert …" (the Proxmox certificate is not accepted): the fingerprint is
  not remembered yet – Proxmox page, "Connections" tab („Verbindungen“) → "Certificate" („Zertifikat“)
  ([PROXMOX-TOKEN.md](PROXMOX-TOKEN.md#certificate-remember-the-fingerprint)).
- "Der Server zeigt ein anderes Zertifikat als das gemerkte …" (the server shows a different certificate than the
  remembered one): the certificate in Proxmox changed. Compare the new fingerprint and confirm it in the "Connections"
  tab → "Certificate" – only if you know about the change.
- "HTTP 401: Proxmox hat den Zugang abgelehnt …" (Proxmox refused access): token ID or secret wrong, or token deleted in
  Proxmox.
- "HTTP 403: Dem Token fehlt ein Recht für diese Abfrage. Grund laut Proxmox: Permission check failed (…)" (the token
  lacks a privilege; reason according to Proxmox): privilege missing – table in
  [PROXMOX-TOKEN.md](PROXMOX-TOKEN.md#reference-which-call-needs-which-privilege). The part "Grund laut Proxmox" only
  appears if Proxmox gives a reason. "Test connection" („Verbindung testen“) says more generally "Zugriff verweigert – dem
  Konto oder Token fehlt ein Recht auf dem Server …" (access denied). If individual VMs are missing entirely, they lack
  `VM.Audit`.
- "Der Server hat die Verbindung auf eine andere Adresse umgeleitet …" (the server redirected the connection to
  another address): usually the address has `http://` instead of `https://`, or a proxy in front of it redirects.
  Nodvard Deck does not follow redirects – enter the node's address directly: `https://<address>:8006`.
- "Die Antwort von Proxmox hat nicht das erwartete Format (HTTP 200). Stimmt die Adresse?" (the answer from Proxmox
  does not have the expected format – is the address right?): something other than the Proxmox API answers at the address, such as a
  proxy's login page. Check the address. "Test connection" („Verbindung testen“) shows both sentences the same way.
- Test from the machine running Nodvard Deck: the `curl` command in
  [PROXMOX-TOKEN.md](PROXMOX-TOKEN.md#check-before-you-enter-it-in-nodvard-deck).
- A failed Proxmox (e.g. pve2 off) no longer slows the others down; it simply shows as "unreachable". On the start
  page its node then no longer counts as online: after the first sync without an answer it shows as "unbekannt"
  (unknown, so a short reboot causes no false alarm). It only counts as "nicht erreichbar" (unreachable) once it has
  not answered any sync for at least 4 minutes in a row, so usually at the next scheduled sync (after about 5 to 10
  minutes); a sync started by hand does not shorten this. An answer or a refusal in between starts it over. If the
  connection points to a cluster or Proxmox only refuses (such as an expired
  token), the nodes stay "unbekannt", because the machines may still be running. Its VMs and containers show as
  "unbekannt" until it answers again. If it is off for longer: on the Proxmox page ("Connections" tab, „Verbindungen“)
  and the Proxmox-Backups page (under "Manage connections", „Verbindungen verwalten“), switch it to
  "disabled" („deaktiviert“); then no notifications come either, and its servers show as "unbekannt" until you switch
  the connection back on.

**"… leitet auf eine andere Adresse um"** (… redirects to another address; Pi-hole, Nginx Proxy Manager,
ntfy, Nextcloud): instead of an answer, the server sent a redirect (HTTP 301, 302, 303, 307 or 308). Nodvard Deck never
follows it, because otherwise a password or token would go to an address nobody entered, and it does not name the target
address either. Usually the settings have `http://` instead of `https://` (or the other way round), or a proxy in front
of it redirects. Fix: enter the final address in the settings, as the browser shows it after the redirect. Other
Nextcloud errors appear as a fixed sentence, for example "Nextcloud ist gerade nicht bereit (HTTP 503), zum Beispiel im
Wartungsmodus." (maintenance mode) or "In der Nextcloud ist kein Speicherplatz mehr frei (HTTP 507)." (no space left).

**"Der Zeitplan „…“ ist ungültig"** (the schedule … is invalid) when saving (an extension's settings, scripts): a
custom schedule (choice "Custom" („Eigener“)) needs exactly five fields: minute (0–59), hour (0–23), day (1–31), month (1–12) and
weekday (0–7), for example `0 2 * * *` for daily at 2 a.m. Shortcuts like `@daily` do not work. If a broken schedule
still comes from an older version, Nodvard Shield keeps running with its default schedule, or the script does not run
on a schedule for now; a notification "Zeitplan … ist ungültig" says which one it is.

**Backup failed** (Proxmox-Backups): on the "Proxmox-Backups" page, open the job's history ("Verlauf"). At the top,
two sentences say what happened and what you can do, below them the line Proxmox wrote. The most common reasons are
known: target full, connection to the network share lost, target not mounted, guest locked, backup server rejects the
login, guest agent not answering, missing permissions (on the target or in Proxmox), disk error, guest deleted, run
aborted, target unreachable. For a job with several guests it also says when this guest was backed up and only another
one failed. For
an older failure, "Warum?" next to the run shows its reason. The backup watcher's alert contains the same two sentences
but not the Proxmox line (it may contain paths and addresses). Without the `Sys.Audit` permission Nodvard Deck does not
read the run's log and only explains its result. A run with warnings ("WARNINGS" in Proxmox) counts as successful, the
backup exists; the history shows "OK, mit Warnungen".

**Nodvard Shield virus scan failed:** the list of scans gives an understandable reason: whether the server was not
reachable, the connection broke off in the middle of the scan, the scan exceeded its time limit (with the duration) or
which message ClamAV wrote, for example "Input/output error" on a failing disk. New files that were deleted again before
the real-time guard's scan do not count as an error. If a scan is running on a server, the guard quietly skips and checks
the skipped time on its next run (up to 6 hours back); if it cannot check for longer, this shows up as an error in the
list. A scan waits up to 6 minutes for a running guard, and a hanging run is ended by the server shortly before the time
limit. This needs `flock` and `timeout` on the server (util-linux, coreutils); without them, everything runs as before.

**Excluding folders from the virus scan** (Nodvard Shield, for example a large media archive that slows the scan
down): Settings → Extensions → "Nodvard Shield" → "Advanced" („Erweitert“) → "Excluded folders" („Ausgenommene
Ordner“), with full paths such as `/srv/medien`. They apply to the quick and the deep scan **and** to the real-time
guard, which neither looks for new files there nor checks anything. An entry applies to exactly this folder including
its contents: `/srv/backup` does not also exclude `/srv/backups-wichtig`, and the built-in exclusion of `/dev` does
not hit `/devel`. There are no wildcards such as `*`; they count as ordinary characters. Only a trailing `/*` or `/**`
is allowed and means the same folder (`/srv/backup/*` works like `/srv/backup`). Double slashes, `/./` and a trailing
slash are dropped. Entries that do not apply are skipped: paths that do not start with `/`, paths with a line break,
paths with `..` (the entry would hit a completely different folder) and `/` on its own; for the virus scan also paths
longer than 511 bytes. If a folder the guard watches is itself a link (for example `/home` → `/data/home`), enter the
exclusion with the path below it (`/home/…`), not with the link's target. If an excluded folder lies above a checked
folder, that folder's check is dropped entirely.

**Have shares and Nextcloud checked by the real-time guard** (Nodvard Shield → tab "Scans" → "Watch templates" –
„Wächter-Vorlagen“): By itself the guard only checks places where malware is typically dropped (`/tmp`, `/home` …). What
others upload often lands elsewhere. "Search the servers" („Auf den Servern suchen“) reads on every checkable server the
Samba shares (`path =` or `directory =` in `/etc/samba/smb.conf`, without printers and `[homes]`), the NFS exports
(`/etc/exports`, also with quotes or `\040`) and the data folder of a Nextcloud (from its `config.php` or a Docker volume
with "nextcloud" in its name, Nextcloud AIO included), and changes nothing. Without root or `sudo -n` the Nextcloud is
usually missing. "Add to the guard" („Zum Wächter hinzufügen“) appends the folder to "Watched folders" („Überwachte
Ordner“; needs the right to change settings; logged). The list applies to all servers; on a server without that folder it
costs nothing. System folders (`/etc`, `/var` …, `/proc`, `/sys`, `/dev`, `/run`, `/boot` with everything below) and
Samba placeholders such as `%U` are not suggested. A find inside an excluded folder shows as "excluded" („ausgenommen“)
and cannot be added (the guard does not look there). A very large folder costs some time on every guard run; remove it
under "Watched folders" in the settings. The guard checks new and changed files, including files copied in with an old
modification time (Samba, Nextcloud sync, `cp -p`, `rsync -a`): besides the modification time it also looks at when the
file was created there.

**"Gerade sind viele Anmeldungen gleichzeitig im Gange. Bitte versuche es in ein paar Sekunden noch einmal." (many
logins are in progress at the same time, please try again in a few seconds) at login:** Nodvard Deck checks only a few
passwords at the same time, so that many login attempts at once do not slow the dashboard down. Wait a few seconds and
try again; anyone who is already signed in keeps working normally. If the message appears often, there are very many
login attempts at once; failed ones are in the audit log („Anmeldung fehlgeschlagen“ – login failed). For a name that
does not exist, the "Who" column („Wer“) only shows "not signed in · unknown" („Nicht angemeldet · unbekannt“), never
the input itself (it could be a password typed into the name field). Only the owner sees an identifier (`username_ref`)
and the length of the input when expanding the row; the same input has the same identifier.

## K. Nodvard Deck does not start: the rescue page

<a id="13-nodvard-deck-does-not-start-the-rescue-page"></a>

If startup fails (the migration, not enough space for the copy, data newer than the version, an unexpected error),
Nodvard Deck does **not** start; instead a small **rescue page** runs on the same port (e.g.
`http://192.168.1.10:8080`). It needs nothing except Python itself and therefore also runs when something is missing in
the new image. The container stays up and does **not restart endlessly**. It listens on the same address as the
application (`--host` in the start command, otherwise `UVICORN_HOST`); if none can be determined (no `--host`, a host
name such as `localhost`), only locally on `127.0.0.1` – just like uvicorn itself. The image starts with
`--host 0.0.0.0`, so normally it is reachable in the home network.

1. **Without a code,** the page only shows that Nodvard Deck has not started, and the general ways back.
2. **Get the rescue code:** It is in the container's log, as a line "Notfallcode: XXXX-XXXX-XXXX" (rescue code) in a
   conspicuous block, and in the file `.boot/rescue_code.txt` in the data folder:

   ```bash
   cd ~/nodvard-deck && sudo docker compose logs nodvard-deck | grep -A3 Notfallcode
   ```

   It stays the same until the next successful start. Wrong entries are slowed down (5 per machine, 25 in total in 10
   minutes, then "Too many failed attempts" („Zu viele Fehlversuche“)). The right code still always works at once: you
   do not have to wait, even if someone else in the network has entered wrong codes many times before.
3. **With the code,** the reason and a cleaned-up log (without paths, passwords, SQL parameters) appear, together with
   the steps that fit the reason:
   - **Try again** („Neu versuchen“): ends the container process; the restart policy (`restart: unless-stopped`)
     restarts it, and startup runs once more. Without a restart policy, please start it by hand.
   - **Restore the state before the update** („Stand vor dem Update wiederherstellen“) (only if the data is newer and an
     intact copy exists): marks the way back and restarts; at startup, the copy is restored.
     Changes since the update are lost.
   - **Back to the old version:** set the image version in the Compose file back and restart the container (see
     [appendix I.3](#i3-the-copy-before-the-migration)).
4. `GET /api/v1/health` answers on the rescue page with **503** `{"status":"rescue"}`; the container's healthcheck
   therefore shows "unhealthy". If you deliver from the repository with `scripts/deploy_pi.sh`, this makes the script
   switch back to the old image automatically (without the full waiting time).

What the rescue page can **not** do: offer a copy to download. The copy is in the data folder under
`backups/vor-update/` and can be saved from there (or with `docker cp`).
If the rescue page itself fails, the container waits instead of exiting – find the cause in the log, then restart by
hand.

Important: A second container with the same data folder (`docker compose run` alongside the running service) changes
nothing; `nodvard_deck.boot` exits there with code 75, because the running application (or the rescue page) holds the
data folder's lock.

## L. Without the interface

<a id="appendix-without-the-interface"></a>

Normally you use the "Servers & access" page ([section 3](#3-connect-your-first-server)). If the interface is not
reachable (e.g. during the first setup over SSH on the machine running Nodvard Deck), everything can also be done
through the API with a small helper script: create a server, change the address, store an SSH login, show the logins and
forget a remembered server key. Script, key and setting up the server by hand:
**[Without the interface](WITHOUT-INTERFACE.md)**.

## M. Old names on older installations

Nodvard Deck used to be called "Lattice". A few old names still show up, mostly on installations and servers that have
been running for a while. They stay on purpose, because existing setups and the way back to an older version depend on
them:

| Name | Where | Why it stays |
|---|---|---|
| User `lattice` | SSH logins created by an older version | The servers are set up that way, the logins keep working unchanged. For new keys the page suggests `nodvard` |
| Comment `lattice@…` | older keys in `~/.ssh/authorized_keys` | The setup command recognizes it and does not add the key twice. New keys get `nodvard@…` |
| `/etc/sudoers.d/lattice-<user>` | older full sudo rule on servers | The setup command replaces it with `nodvard-<user>` if it is exactly the one Nodvard Deck created earlier ([Root rights](ROOT-RIGHTS.md#the-full-sudo-rule)) |
| systemd units `lattice-upgrade-…` | Nodvard Shield updates on your servers | Resuming and rolling back updates depend on them |
| Folders `/var/lib/nexus-updates` and `/var/lib/nexus-quarantine` | on your servers (Nodvard Shield had a different identifier up to 0.6) | The paths are stored in saved findings, and running updates are resumed via the folder |
| Database file `lattice.db` (in backups `db/lattice.db`) | data folder | Renaming it would need a migration; after a way back an older image would otherwise open an empty database |
| `LATTICE_PORT` and the other `LATTICE_*` variables | Compose file, `.env` | Fallback if the new name `NODVARD_DECK_*` is not set, only for a transition period (until 1.0 at the latest). Nodvard Deck points the owner to it in the overview |

The complete list with all reasons is in [ARCHITECTURE.md, "Old and new names"](ARCHITECTURE.md#old-and-new-names).
