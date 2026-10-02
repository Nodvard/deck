# First-Time Setup – Step by Step

> The Nodvard Deck interface is currently available in German only; an English interface is planned. UI labels are given in English with the German original in quotes.

As of 1 October 2026. For a fresh installation, work through these steps in order. Where Nodvard
Deck still has a gap, the guide says so. Without the web interface (using only the API), see the
[appendix](#appendix-without-the-interface).

1. [Start the container](#1-start-the-container)
2. [Setup wizard](#2-setup-wizard-setup)
3. [Additional users](#3-additional-users)
4. [Enable extensions](#4-enable-extensions)
5. [Servers and SSH access](#5-servers-and-ssh-access)
6. [Root rights without a password and the docker group](#6-root-rights-without-a-password-and-the-docker-group)
7. [Connect Proxmox](#7-connect-proxmox) (only with Proxmox)
8. [Push notifications (ntfy)](#8-push-notifications-ntfy)
9. [Automation and maintenance windows](#9-automation-and-maintenance-windows)
10. [Back up the dashboard itself](#10-back-up-the-dashboard-itself)
11. [When something goes wrong](#11-when-something-goes-wrong)
12. [Locked out? Emergency commands](#12-locked-out-emergency-commands)
13. [Nodvard Deck does not start: the rescue page](#13-nodvard-deck-does-not-start-the-rescue-page)
14. [Appendix: Without the interface](#appendix-without-the-interface)

## 1. Start the container

### 1.1 Installation without the repo

If you only have Docker and do not know the repository, you need a single file: download
[`deploy/compose.standalone.yml`](../../deploy/compose.standalone.yml) as `compose.yml` into an empty folder and start
it there. The ready-made image comes from the GitHub Container Registry (`ghcr.io/nodvard/deck`, for x86
machines and the Raspberry Pi); nothing is built:

```bash
mkdir ~/nodvard-deck && cd ~/nodvard-deck
curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml
docker compose up -d
```

To check it afterwards, on the same machine:

```bash
curl -s http://localhost:8080/api/v1/health      # expected: "status":"ok"
```

The database is created or updated automatically when the container starts; there is nothing to do for
that. Then open `http://<address-of-the-machine>:8080` in your browser, e.g. **http://192.168.1.10:8080**
(for a different port, use the variable `NODVARD_DECK_PORT`). The data lives in its own Docker storage
(volume `nodvard-deck-data`). If you would rather use a folder on the NAS (`./daten:/app/data`), you do not
need to set any permissions: when it starts, the container sets the ownership of that folder itself. Afterwards
only the user with the number 1000 and root can read it (if the storage supports permissions); backup tools or
file managers that run as another user need `sudo` for it. To update (in the same folder): `docker compose pull && docker compose up -d`.
The setup code (next section) is shown by the wizard together with instructions on where to find it
(command line, Docker Desktop, Portainer, Synology, Unraid). The log line is in German and starts with
"Einrichtungscode" (setup code).

**All further commands in this guide** (setup code, log, emergency commands, restore) are run in the
folder that contains `compose.yml`, so here after `cd ~/nodvard-deck`. The examples use `sudo docker …`;
if your user is in the `docker` group (or you use Docker Desktop), leave out `sudo`.

### 1.2 Building from the repository (developers)

If you clone the repository and build the image yourself, you start it in the repository's `deploy/`
folder (`docker compose up -d --build`). Building, delivering to a Raspberry Pi (`scripts/deploy_pi.sh`),
the fallback route and rollback are covered in [deploy/README.md](../../deploy/README.md) (German). The check
afterwards is the same as above (`curl -s http://localhost:8080/api/v1/health`).

With this installation, the commands in this guide are run in the repository's `deploy/` folder (e.g.
`cd ~/deck/deploy`) instead of `~/nodvard-deck`. Sections that only apply to this route say so.

## 2. Setup wizard (/setup)

As long as there is no user yet, the login page automatically redirects to `/setup`.

**Already have a backup?** The first step offers "Or: restore a backup" („Oder: Sicherung einspielen“): upload the
file, enter the password, check the summary, restore – after that you log in with the account from the
backup and do not need to set anything up again ([10.2](#102-restore-import-a-backup)). The setup code (below) is
required there as well.

**Setup code:** So that not just anyone on the network who opens the page first gets the first account
(and with it the owner role), `/setup` asks for a code. Nodvard Deck generates it at startup
and writes it to the log – at **every** start until the first account has been created:

```bash
cd ~/nodvard-deck && sudo docker compose logs nodvard-deck | grep -A1 Einrichtungscode
# Einrichtungscode: K7MQ-X2VD-H9PA – im Browser eingeben
```

(The log line is in German: "Einrichtungscode" = setup code, "im Browser eingeben" = enter in the browser.)

- Upper/lower case, dashes and spaces do not matter.
- The code stays the same across restarts (it is stored as `setup_code.txt` in the data directory,
  readable only by Nodvard Deck) and is deleted once the account has been created.
- Wrongly entered codes are slowed down like wrong passwords (after 10 failed attempts a pause of a few
  minutes) and appear in the log as "Einrichtungscode falsch" (setup code wrong).
- If you would rather choose the code yourself (at least 8 characters), add it to the Compose file as
  **`NODVARD_DECK_SETUP_CODE: "…"`** under `environment:`. The `compose.yml` from
  [1.1](#11-installation-without-the-repo) has no `environment:` block yet; add one under
  `nodvard-deck:` (same indentation as `image:`) and apply it with `docker compose up -d`:

  ```yaml
      environment:
        NODVARD_DECK_SETUP_CODE: "my-own-setup-code"
  ```

  The repository's `deploy/docker-compose.yml` already has the block. Without this setting Nodvard Deck
  generates one itself.
- Existing installations that already have an account are not affected.

The log also shows the code large and on a line of its own (between blank lines, in a frame of `=`
characters). In the wizard, "Where do I find the code?" („Wo finde ich den Code?“) unfolds instructions.

The wizard has **six steps** and shows "Step X of 6" („Schritt X von 6“) at the top. Only the first is
mandatory; everything else can be skipped and done later in the settings. "Back" („Zurück“) is available
from step 3.

**Step 1 of 6 — Create the administrator account** („Administrator-Konto anlegen“)

- **Setup code** („Einrichtungscode“): see above.
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
(System, Terminal, Service matrix, Proxmox VE …), then security, tools, connections to other services.
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
the interface also calls them „Rettungscodes“, see [2.1](#21-two-factor-login-and-recovery-codes)) are then shown
**exactly once**: copy them or save them as a text file, then press "I have saved the codes" („Ich habe die Codes
gesichert“). "Skip" sets nothing up; you can do it later under Settings → My account
(„Einstellungen → Mein Konto“).

**Step 5 of 6 — Appearance (optional)** („Aussehen“)

Product name („Produktname“), subtitle („Untertitel“: the small line under the name; the same text is the app's name on
the phone's home screen, so keep it short), accent color („Akzentfarbe“) →
"Save" („Speichern“), or "Skip". The logo and the remaining colors are available later under
Settings → Appearance („Einstellungen → Aussehen“).

**Step 6 of 6 — Done** („Fertig“)

A pointer to the "First steps" card in the cockpit and the emergency command (see
[12.](#12-locked-out-emergency-commands)); the same command is shown on the login page behind "Forgot
password?" („Passwort vergessen?“). If you skipped two-factor login, you get the link to do it later here.
"Continue to the dashboard" („Weiter zum Dashboard“) opens the cockpit.

**Reloaded in the middle of the wizard?** The account already exists at that point, so `/setup` actually leads to
the login. But the wizard remembers the step it had reached in that browser tab: if you are still signed in, it
continues exactly there (time zone, modules, two-factor, appearance); otherwise you land on the login page, and
the "First steps" card takes over afterwards. The recovery codes cannot be shown a second time after a reload –
you create new ones under My account („Mein Konto“).

After that, `/setup` is gone (it only leads to the login page).

### 2.1 Two-factor login and recovery codes

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
- **Codes from the app** work only **once** per account; if one has already been used, wait for the next
  one. After 10 wrong codes in 15 minutes (or 20 in 24 hours), no matter from which device, code entry for the
  account is blocked for a while and Nodvard Deck sends a notification. Whoever guesses that often probably knows
  the password – if it was not you, change it. With a recovery code you still get in during the block.
- **How many are left** is shown under My account. If there are few or none: "Generate new recovery codes"
  („Neue Wiederherstellungs-Codes erzeugen“) (asks for the current password; the old codes become invalid
  immediately).
- Nodvard Deck stores only checksums, not the codes – a lost set cannot be looked up, only replaced.
- If you had already set up two-factor login before this feature existed, you do not have codes yet:
  generate them once under My account with "Generate new recovery codes".
- If someone loses their phone **and** the codes: see [12.](#12-locked-out-emergency-commands).

## 3. Additional users

Settings → Users → **"New user"** („Einstellungen → Benutzer → Neuer Benutzer“):

- **Username** („Benutzername“): the same rule as for the first account (at least 3 characters, only lowercase
  letters, digits, `.`, `-` and `_`, starting with a letter or a digit); uppercase letters are converted
  automatically. Upper or lower case does not matter at login. Older accounts whose name does not fit this rule
  (e.g. with spaces) keep signing in normally.
- **Password** („Passwort“): at least 8 characters. The new user can change it under "My
  account" („Mein Konto“). (The password of *other* users is set later under "Edit"
  („Bearbeiten“); your own and the owner's cannot be set there – your own is set under "My account".)
- **Display name** („Anzeigename“), **Email (optional)** („E-Mail (optional)“); an address you enter needs an `@`
  (e.g. `name@beispiel.de`).
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
for). The person is signed out everywhere, and the audit log records who did it. Afterwards, login works with
the password only, and the person can set up two-factor again. **This does not work for the owner** – the
owner's two-factor login can only be turned off by the owner personally (under My account) or by the
emergency command on the server ([12.](#12-locked-out-emergency-commands)).

**Deactivating a user:** Under Settings → Users → **"Deactivate"** („Deaktivieren“), a user with the
privilege `users.write` blocks another person's account (the list then shows "Blocked" („Gesperrt“); there
is no such button for the owner or for your own account). The person is signed out everywhere, and a page that
is already open stops getting live updates after half a minute at most. After **"Activate"** („Aktivieren“),
old devices do not sign in again by themselves; the person has to sign in again.

## 4. Enable extensions

On a fresh installation, **all extensions are off** – with one exception: **Terminal switches
itself on** as soon as you give the first server an SSH login (generate a key, password or your
own key; see [5.2](#52-set-up-ssh-access--three-ways)). The audit log records it
(„Erweiterung automatisch eingeschaltet“). This only happens as long as nobody has ever switched
Terminal on or off themselves: if you switch it off on purpose, it stays off. Existing
installations are not affected.

In the setup wizard (step 3, see [2.](#2-setup-wizard-setup)) you switch on the modules you want to use with a
click. Later you can do that at any time under Settings → Extensions („Einstellungen → Erweiterungen“), with the
switch on the right; "Configure" („Konfigurieren“) opens the extension's settings ("Setup required" –
„Einrichtung nötig“ – means that details are still missing or that the last connection test failed). If you open
the page of a module that is switched off (for example via a bookmark), it says so, and **"Switch on"** („Einschalten“)
switches it on right there (without the privilege for that, it says that an administrator can switch it on). A good
start for almost any home network: Terminal, System, Nodvard Shield and ntfy notifications, plus Service matrix if
Docker runs on a server, and Game servers if a game server runs there. **You only need Proxmox VE and Backups if
you use Proxmox** – otherwise leave them off (see [4.1](#41-without-proxmox)). The rest as needed.

**Credentials belong to their address.** In an extension's settings, first enter the address and click "Save"
(„Speichern“), then store the token or password in the "Connection" card („Verbindung“) – Nodvard Deck does not
accept them before that. If you change the address later (for example the ntfy server or a Proxmox address),
Nodvard Deck deletes the matching credentials when saving, and you enter them again; the page tells you
beforehand. A different spelling of the same address (upper/lower case in the host name, default port, trailing `/`,
missing `http://`) does not count as a change.

### 4.1 Without Proxmox

Nodvard Deck does not need Proxmox. A Raspberry Pi, a Debian VM, a NAS or a few Docker hosts are enough. What is
different then:

- **You create servers by hand** ([5.1](#51-create-a-server)) and give each one an SSH login
  ([5.2](#52-set-up-ssh-access--three-ways)). The modules Proxmox VE and Backups stay off; their entries then appear
  neither in the menu nor in the cockpit.
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
- **Utilization and history** (CPU, memory, disk, network, temperature) come from the **System** module via SSH, only
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
  as current. Servers without measurements (Windows, no SSH login, System module off, measurements turned off for the
  server) stay a single row, with a note on why where that fits.
- **Docker containers:** Service matrix, for servers with the tag `docker`.
- **Apps in the cockpit without the Service matrix:** In the "Apps" area you create your own tiles with **+ Add app**
  („+ App hinzufügen“) – name, address (`http://192.168.1.1`), icon (from a selection or an emoji), color, group and,
  if you like, the server it belongs to. That way routers, the NAS interface or Pi-hole also appear in the cockpit,
  without code and without container detection; detected containers appear alongside when the Service matrix is
  running. Via the menu (⋮) on the tile you edit, move or delete it, and at the top you filter by group. The owner
  and administrators may create and change them (privilege `apps.write`); everyone who can see servers can see them.
  Nodvard Deck never calls the addresses itself (so it shows no "running/not running"), allows only `http://` and
  `https://` and no credentials in the address; icons are never image URLs.
- **Updates, antivirus, intrusion protection:** Nodvard Shield, for all Linux servers with an SSH login. The AI container
  watch in it is optional; without an AI server it still shows crashed containers. With AI it proposes at most a
  restart of a crashed container, which you find under "Actions" („Aktionen“); other ideas from the AI only appear
  as text in the status report of the container watch. If it suggests another command, the status report only
  mentions that; you see the command itself in the audit log if you have server rights (`hosts.execute`).
- **Console in the browser** (a guest's screen) exists only for Proxmox guests. For every other server you use the
  **Terminal**.
- **Backups:** The Backups module shows only Proxmox backups. You back up the dashboard itself under Settings →
  System ([10.](#10-back-up-the-dashboard-itself)).

**The "First steps" card:** On the start page (cockpit), a checklist at the top walks you through exactly these
steps – create a server, store an SSH login, check the connection, set up modules, push notifications, first
widget – each with a button to the right place. Nodvard Deck ticks the boxes itself, based on what is already set up.
**"Store an SSH login"** („SSH-Zugang hinterlegen“) only counts a key once Nodvard Deck has actually logged in with it
(a password counts right away); until then, **"View command"** („Befehl ansehen“) leads to the server's page with the
setup command already expanded. **"Check the connection"** („Verbindung prüfen“) is only ticked once a login is
confirmed: the server merely answering is not enough.
Only steps for which you have the privilege are shown. The card disappears by itself when everything is done; with
**"Hide"** („Ausblenden“) you remove it earlier (applies to your account on every device). Empty pages (Terminal,
Files, server page, Service matrix, Game servers, Proxmox) now also say what is missing and offer the button for the
next step.

## 5. Servers and SSH access

Everything about this is on one page: **Settings → Servers & access** („Einstellungen → Server & Zugänge“). There
you create servers, set up their SSH login and check that the connection works. The page is designed for
the phone and is visible only to users with the privilege `hosts.write` (owner and `admin`).

**Where servers come from:**

- Proxmox nodes, VMs and containers are read in **automatically** by the "Proxmox VE" extension
  (every 5 minutes, see [7.](#7-connect-proxmox)). They appear in the list with the note "Read in
  automatically" („Automatisch eingelesen“).
- Everything else – above all the **machine Nodvard Deck runs on** (e.g. a Raspberry Pi), if it is not
  a Proxmox guest – you create by hand.
- Terminal, System, Service matrix, Nodvard Shield, Game servers and Scripts need an **SSH login** (user + key or
  password) for every server. Nodvard Deck stores it encrypted; the private key never leaves the vault and is
  never displayed anywhere.

**No server at hand yet?** On a fresh installation without servers, the cockpit (and this page) offers the button
**"View with sample data"** („Mit Beispieldaten ansehen“). One click creates five made-up servers (addresses from
`192.0.2.x`, nothing is ever contacted), a few notifications, three sample apps and – if modules bring widgets – a
sample dashboard, so you can see what everything looks like when filled. A yellow banner **"You are viewing sample
data"** („Du siehst Beispieldaten“) then appears at the top; **"Delete sample data"** („Beispieldaten löschen“)
(after confirmation) removes exactly these things again, restores your dashboard layout and leaves everything real
untouched. Only the owner and administrators see the button (`hosts.write` and `settings.write`). As soon as you
have created a real server, the button is gone; you can still delete the sample data at any time.

### 5.1 Create a server

1. Press **"Add server"** („Server hinzufügen“) (on an empty list the button is also in the middle). If you come via
   "Add server" in the cockpit or in the "First steps" card, the form is already open.
2. **Short name** („Kurzname“) – lowercase, no spaces, e.g. `pi`. It can **not** be changed later (Nodvard Shield and
   other extensions recognize the server by it).
3. **Display name** („Anzeigename“) (what the server is called in the lists; empty = short name) and **Address**
   („Adresse“) (IP or host name, without `http://` and without a port, e.g. `192.168.1.30`).
4. **Operating system** („Betriebssystem“): Linux or Windows. For Windows servers there is no setup command and no
   privilege check.
5. **Tags** („Markierungen“) (comma-separated): `docker` if Docker runs there – the Service matrix (setting "Server
   tag" – „Server-Markierung“) and the AI container watch of Nodvard Shield ("Monitored servers" – „Überwachte Server“)
   take exactly the servers with this tag. Game servers appear only with the tag `gameserver`. Tags can be changed at
   any time, also for Proxmox VMs (the ones set by Proxmox stay; they cannot be changed).
6. **"Create"** („Anlegen“). The new server's page opens and immediately offers the next step.

Wrong input is reported right at the field, in German (e.g. „Kurzname: nur Kleinbuchstaben, Ziffern, - und _ …“,
meaning "Short name: only lowercase letters, digits, - and _ …").

**Correcting the address:** Proxmox VMs without a running guest agent get the address of the Proxmox server as a
placeholder. Then, on the server's page, enter the real IP under **General → Edit** („Allgemein → Bearbeiten“) –
Nodvard Deck no longer overwrites an address set by hand with the placeholder, only with an address that the guest
itself reports. For Proxmox *nodes*, the extension overwrites the address again at the next sync.

If the server has a stored **SSH password**, changing the address deletes it; you enter it again afterwards. SSH
keys stay. This also applies when an extension's sync changes the address (e.g. of a Proxmox node); for VMs and
containers that report their address themselves and whose server key is already remembered, the password stays.

### 5.2 Set up SSH access – three ways

On the server's page, card **SSH access** („SSH-Zugang“):

- **Generate SSH key (recommended)** („SSH-Schlüssel erzeugen“). Nodvard Deck generates a key of its own just for this
  server. You only specify the **user on the server** (default `nodvard`, a user of its own just for Nodvard
  Deck; it is created if it does not exist yet –
  for Proxmox nodes use `root`) and the port (22). Older logins may still be called `lattice`; they keep working
  unchanged. Afterwards the page shows the **setup command** (5.3).
- **Enter password** („Passwort eingeben“). For a user that already exists on the server. The password is stored
  encrypted and never shown again. A key is more secure.
- **Paste your own key** („Eigenen Schlüssel einfügen“). Paste the private key (text beginning with `-----BEGIN …`,
  **without a passphrase**). Afterwards there is also a setup command for the server.

Whatever you type or paste is cleared from the form when you submit it.

### 5.3 Run the setup command on the server

The command is **a single one-liner**. Copy it to the clipboard with **"Copy"** („Kopieren“) (this also works on
`http://`, without a secure context), log in to the server (via `ssh`, at the screen, or via the console of your VM
management), paste it, press Enter. It

- creates the user if necessary (without a password, login only with the key),
- adds the public key to `~/.ssh/authorized_keys` (with `restrict,pty`: terminal, files and commands work,
  forwardings do not),
- optionally sets up **root rights without a password** and the **docker group** (see
  [6.](#6-root-rights-without-a-password-and-the-docker-group)).

Under "What does the command do?" („Was macht der Befehl?“) the same sequence is shown in readable form, line by
line. The command runs directly as root, otherwise via `sudo` – so it works on Proxmox (no sudo) as well as on
Debian or the Pi. It can safely be run several times: it recognizes a key that is already there, even with the older
comment `lattice@…`, and does not add it twice.

### 5.4 Check the connection

Card **Check connection** („Verbindung prüfen“) (up to 30 seconds). The page shows point by point what works and
what does not – with a hint on what to do:

1. **Server reachable** – address, port, firewall, is SSH running?
2. **Server key** – see below.
3. **Login** – does the server accept the key or the password?
4. **Root rights** – does `sudo` work without a password? (Only a hint, not an error: the lean variant without root
   rights is allowed.)
5. **What extensions need** – e.g. Docker without sudo for the Service matrix.
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
> Server-Schlüssel“). On new installations, **Only remember new server keys after my confirmation** („Neue
> Server-Schlüssel erst nach meiner Bestätigung merken“) is on: Nodvard Deck then remembers a key only through
> "Check connection", and no password and no key goes to the server before that. Installations that were set up
> before this setting existed stay at "off" and keep remembering by themselves until you switch it on. The
> environment variable `NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS` (`true` or `false`; in the Compose file under
> `environment:`, see the example in [2.](#2-setup-wizard-setup); the repository's `deploy/docker-compose.yml` has
> the line prepared) overrides the switch: the card then shows the variable's value, and saving reports an error.
> After **Forget** („Vergessen“) you always have to confirm the new fingerprint, no matter how this is set – until
> then Nodvard Deck no longer connects to the server.
> Keys that have already been remembered are never affected. The cleaner way is always: check and confirm first.

### 5.5 Replace access, delete, groups

- **Replace:** Card SSH access → **Replace** („Ersetzen“) creates a *new* login (generate key, password or your own
  key); the old one stays the default. Run the new login's command on the server, press **Check new access**
  („Neuen Zugang prüfen“) and then **Use new access** („Neuen Zugang verwenden“). Only after a successful, fresh check
  (at most 10 minutes old) is the old login deleted – that way you do not lock yourself out. If the old login was a
  key, **its public key stays on the server in `~/.ssh/authorized_keys`**; remove it there yourself if needed. For a
  password, nothing changes on the server. Checking the new login does not change the confirmation of the old one
  (5.4); only once you use the new one does its check count.
- **Delete access:** Terminal, updates and monitoring then no longer reach the server. A key stays registered on the
  server; for a password, nothing changes there.
- **Groups:** On the list under **Groups** („Gruppen“) you create, rename and delete them; on the server's page you
  assign them with a tick. "Scripts" („Skripte“) can run a script on all servers of a group.
- **Delete server:** Card "Danger zone" („Gefahrenbereich“) (or "Delete" („Löschen“) on the server page). For safety,
  type the short name. The SSH login and the remembered server key are deleted as well; histories in Nodvard Shield stay
  with the old name. Servers read in from Proxmox reappear at the next sync – then without an SSH login. If an
  action is currently running on the server, deletion is not possible.

**Testing:** In the "Terminal" menu the server now appears (the Terminal extension switches itself on with the first
SSH login, unless you had switched it off yourself). Open a session – if the login works, it works for all the other
extensions too. A terminal session without any input or output closes by itself after 30 minutes.

## 6. Root rights without a password and the docker group

If Nodvard Deck does **not** log in to a server as root (e.g. on the Pi), it runs root commands via `sudo -n`, that is,
without a password prompt. If sudo asks for a password, the function fails with "Keine root-Rechte: Das Dashboard
ist auf diesem Server nicht als root angemeldet und darf sudo nicht ohne Passwort nutzen." (no root rights: the
dashboard is not logged in as root on this server and may not use sudo without a password).

You do **not** have to change the setup command (5.3) by hand for this: the page sets the appropriate tick
boxes itself, because the extensions report what they need.

- **"Root rights without a password"** („Root-Rechte ohne Passwort“) – needed for (the page names it behind the
  tick box):
  - **Nodvard Shield, antivirus:** move a file to quarantine, restore or delete it, Lynis hardening audit, install tools
    (ClamAV, Lynis, signatures, Fail2ban, automatic updates). Scans also run without root, but then only check files
    that the user may read.
  - **Update center:** apply updates and reboot. Reloading the package lists before the check needs root – without
    it, you stay at the most recently loaded state. Updates run in the background on the server (with systemd as
    `lattice-upgrade-…`, otherwise via `setsid`) and keep running even if the connection drops or the dashboard
    restarts. The log of every run is kept for 30 days under `/var/lib/nexus-updates/`. The dashboard waits 45
    minutes for the result; if a run takes longer, it initially shows as "No response" („Keine Rückmeldung“) (it is
    never terminated), but the dashboard keeps asking for up to about 3 hours, then corrects the entry and reports the
    result by push. If the dashboard restarts during that time, the entry stays as it is – then look in the log on the
    server.
  - **Intrusion protection and file watcher:** SSH log, Fail2ban status, processes behind open ports, SSH settings,
    checksums of files such as `/etc/shadow`. Only limited without root – Nodvard Deck says so, instead of falsely
    giving the all-clear. Blocking/unblocking IP addresses via Fail2ban works only with root.
  - **System:** restart services (systemd).
- **"Add to the docker group"** („Zur Gruppe docker hinzufügen“) – needed for the Service matrix and the AI container
  watch of Nodvard Shield; they call `docker` without sudo. The group is only entered if it exists on the server.

Without either tick box you get the **lean variant**: its own user, key only, no root rights. That is enough for
Terminal, Files and Scripts.

**What the "Root rights" tick box does exactly:** It creates `/etc/sudoers.d/nodvard-<user>` with the line
`<user> ALL=(root) NOPASSWD: ALL`. The line is first written to a trial file and checked with `visudo -c`; only then
does it move into place with permissions `0440` (root), afterwards `visudo -c` checks everything once more, and on an
error the file is removed again immediately ("sudo-Regel zurückgenommen" – sudo rule withdrawn). The command deletes an
older rule `lattice-<user>` only after this check and only if it consists of exactly the line above; otherwise it
stays in place and you get a hint. If `sudo` is not
installed at all, the command says so (as root `apt install sudo`; on Proxmox best log in as `root` right away).

**This is practically root – and the page tells you so too:** Anyone who is in the `docker` group or may use sudo
without a password can do practically anything on the server. A more narrowly limited sudo rule does not work with
Nodvard Deck: it starts every root command as `sudo -n sh -c '…'`, and anyone who may start `sh` as root may do
anything. The security comes instead from the fact that **only** this one user gets the rights, can log in **only with
the key**, and the key is stored encrypted in Nodvard Deck.

**Proxmox nodes:** There, Nodvard Deck is easiest to log in directly as `root` (enter user `root` when generating).
Then it needs neither sudo nor tick boxes. The key ends up in `/root/.ssh/authorized_keys`, which on Proxmox is a link
into the cluster folder – so it then applies on **all nodes of the cluster**.

**Using an existing user** (on the Pi e.g. `admin`): enter that name instead of `nodvard` when generating. Running
`sudo -n true && echo geht-schon` on the server shows whether it may already use sudo without a password (on
Raspberry Pi OS this is often the case for the first user).

Do not be surprised: the file watcher of Nodvard Shield reports the new sudo file and the new `authorized_keys` once –
that was you. The same applies if you run the command again with the "Root rights" tick box on a server that still
has the old rule `lattice-<user>`: `nodvard-<user>` is added, and the old one goes away if it is unchanged (see above).

## 7. Connect Proxmox

**Only if you use Proxmox – otherwise skip this section** (see [4.1](#41-without-proxmox)). In detail in
**[PROXMOX-TOKEN.md](PROXMOX-TOKEN.md)**. Short version:

1. On every Proxmox server (in the example pve1 **and** pve2), create a user `nodvard@pve` with token `dashboard`
   and a suitable role.
2. Settings → Extensions → "Proxmox VE" → "Configure" → "Add server" („Einstellungen → Erweiterungen → Proxmox VE →
   Konfigurieren → Server hinzufügen“) (short name („Kurzname“) `pve1`/`pve2`, address („Adresse“) `https://…:8006`, API
   token ID („API-Token-ID“) `nodvard@pve!dashboard`) → "Save" („Speichern“) → enter the secret under "Credentials"
   („Zugangsdaten“).
3. Do the same under "Backups", with the same short names.
4. After 5 minutes at the latest, nodes, VMs and containers are there. VMs without a guest agent get the Proxmox
   address as a placeholder – enter the correct IP under Settings → Servers & access
   („Einstellungen → Server & Zugänge“) ([5.1](#51-create-a-server)).

## 8. Push notifications (ntfy)

Turn on Settings → Extensions → **"ntfy notifications"** („Einstellungen → Erweiterungen → ntfy-Benachrichtigungen“) →
"Configure" („Konfigurieren“):

- **ntfy server** („ntfy-Server“): e.g. `https://ntfy.sh` or your own.
- **Topic** („Thema (Topic)“): the channel you subscribe to in the ntfy app. On ntfy.sh choose one that is hard to
  guess, because there anyone who knows the name can read along.
- **Dashboard address (optional)** („Adresse des Dashboards (optional)“): `http://192.168.1.10:8080`. Then tapping a
  notification opens the matching page directly. It must start with `http://` or `https://`, otherwise it is ignored.
- "Save" („Speichern“).
- **Credentials → "Access token (optional)"** („Zugangsdaten → Zugriffstoken (optional)“): only if your ntfy server
  requires login.

In the ntfy app on your phone, subscribe to the same server and the same topic.
**Testing:** "Nodvard Shield" page → "Send briefing" („Briefing senden“). Creating it can take up to half a minute if a
server does not respond. Afterwards the page tells you whether the status report („Lagebericht – …“) was also delivered
as a push notification. If not, ntfy is not set up yet or the ntfy server cannot be reached. All messages are also in
the dashboard under "Notifications" („Meldungen“) – even if the push notification does not arrive.

## 9. Automation and maintenance windows

Settings → **Automation & security** („Einstellungen → Automatik & Sicherheit“):

- **Autonomy** („Selbstständigkeit“): "Suggest only" („Nur vorschlagen“) is the default and right for the beginning –
  every action waits for your approval under "Actions" („Aktionen“). "Act autonomously" („Selbstständig handeln“)
  lets actions up to the chosen risk level ("Allowed without asking up to risk level" – „Ohne Rückfrage erlaubt bis
  Risikostufe“) run without asking. Under "Actions" („Aktionen“), an action's command appears right in its row
  (without the privilege `hosts.execute` and without the right to approve its risk level, only a notice appears
  there); invisible or redirecting characters in it (such as a reversed writing direction) appear as a visible marker
  like `⟦U+202E⟧`. "Approve selected" („Ausgewählte freigeben“) shows all commands in full in its confirmation, but
  leaves out actions with high or critical risk; you confirm those one by one.
- **Scripts without a click**: Independently of this, an active script with a schedule can get a standing approval
  („Dauerfreigabe“): on the Scripts page („Skripte“), turn on the switch "Without approval on schedule" („Ohne Freigabe
  nach Zeitplan“) for the script (owner and `admin` only). Beforehand you see which servers, accounts and addresses it
  covers. Any change to content, parameters, target or schedule, and a different account, address or SSH port on one of
  these servers, cancels it; new servers that join a group or "All servers" („Alle Server“) still ask. If the person
  who granted it is deactivated or no longer has the rights for it, the runs wait for your click again as well. Every
  run still appears under "Actions" („Aktionen“) and in the audit log, and "Withdraw approval" („Freigabe
  zurückziehen“) takes effect immediately.
- **Blocked commands** („Gesperrte Befehle“): your own patterns (regular expressions) that are never executed. The
  dangerous default cases are always blocked.
- **Maintenance windows** („Wartungsfenster“): "Add window" („Fenster hinzufügen“) → start, duration, "Applies to"
  („Gilt für“) (All servers / Selected servers) → "Save". During the window, push notifications about these servers
  are muted; they still appear under "Notifications". The time applies in the configured time zone (Settings →
  System). Only notifications that belong to specific servers are muted: those from **Backups**, **Game servers**, the **Proxmox watcher** (nodes, disks,
  connection) and **Nodvard Shield** (updates, hardening audit, intrusion-protection warnings, status report of the
  container watch). A report covering several servers stays audible as long as even one of them is not in the window.
  Always audible: **malware findings** and **critical intrusion-protection events** (the deep scan runs on Sundays at
  03:30 by default, right in the middle of the suggested window), the **morning briefing**, and the notifications of
  the certificate watcher and scripts.
  Good to know: If something fails during the window for Backups, Game servers or the Proxmox watcher and is still
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

## 10. Back up the dashboard itself

### 10.1 In the interface (recommended)

**Settings → System → Backup** („Einstellungen → System → Sicherung“). Only the owner can change anything there.

1. **Set a backup password** („Sicherungspasswort festlegen“) (at least 12 characters, enter it twice, confirm with
   the login password). Nodvard Deck does not store the password, only a public key. Afterwards the *recovery key*
   (`AGE-SECRET-KEY-1…`) appears **once**: copy it or save it as a file and keep it safe.
   **Password and key gone = backups worthless.** Nobody can open them any more.
2. Turn on **Back up automatically** („Automatisch sichern“), choose the time and set how many backups should be kept
   (1 to 60, older ones are deleted).
3. **Folder** („Ordner“): the default is `/app/data/backups` in the data folder. That protects against operating
   mistakes, but not against a broken SD card. It is better to mount a folder from another drive or a NAS: the
   Compose file (`compose.yml` from [1.1](#11-installation-without-the-repo), or `deploy/docker-compose.yml` for an
   installation from the repository) already contains the line `# - ./sicherungen:/backups` under `volumes:`. Remove
   the `#` (or enter a NAS path instead of `./sicherungen`), run `mkdir sicherungen && sudo chown 1000:1000 sicherungen`
   in the same folder (the folder must belong to the user with the number 1000), apply it with `docker compose up -d`
   and choose `/backups` as the folder in the interface.
4. **Check** („Prüfen“) compares a backup with its checksum (detects broken files, without a password).
   **Download** („Herunterladen“) fetches a backup to your PC, **Download backup** („Sicherung herunterladen“) creates
   a fresh one immediately – either with the backup password or with a one-time password of your own just for this
   file.

Emergency without Nodvard Deck: A `.ndbak` file is an ordinary [age](https://age-encryption.org) file with two lines in
front of it.

```bash
tail -n +3 nodvard-deck-sicherung-20261001-023000.ndbak > sicherung.age
age -d -i wiederherstellungsschluessel.txt -o sicherung.tar.gz sicherung.age   # or: age -d (one-time password)
tar -xzf sicherung.tar.gz    # db/lattice.db, files/master.key, files/ext/ …
```

To restore: see [10.2](#102-restore-import-a-backup).

### 10.2 Restore (import a backup)

Two ways, same procedure: **Settings → System → Restore** („Einstellungen → System → Wiederherstellen“) (owner only,
with the login password) or, on a **fresh installation**, in the setup wizard under **"Or: restore a backup"**
(„Oder: Sicherung einspielen“) (there, instead of an account, with the **setup code** from the container's log – as
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
- With the environment variable `NODVARD_DECK_JWT_SECRET`, no login secret is in the backup.
- The history of the measurements (`metrics.db`) is not part of the backup and stays as it is.
- **Git settings** in the extensions' data (for example in "Scripts" („Skripte“)) are not part of the backup either, but
  the history of your scripts is; the extension creates the settings anew itself. If an older backup still contains some,
  they are skipped during the restore, and the summary names their number among the warnings.

**Emergency without the interface:** in the folder with the Compose file

```bash
cd ~/nodvard-deck
sudo docker compose exec nodvard-deck python -m nodvard_deck.admin restore-backup /app/data/backups/nodvard-deck-sicherung-….ndbak
sudo docker compose restart nodvard-deck      # the restore happens at startup
```

(The file must be visible inside the container; the command asks for the password and shows the summary. `compose exec`
runs in the existing container; a `compose run` alongside it would be rejected by the data folder's lock, see
[10.4](#104-updates-the-copy-before-the-migration).) With an installation from the repository,
`deploy/restore.sh sicherung.ndbak` does the same in one step
([10.3](#103-with-the-script-installation-from-the-repository-only)). If nothing works at all, the old state is under
`restore/replaced-…`: stop the container, copy its contents (`lattice.db`, `master.key`, `ext/` …) back into the data
folder, start.

### 10.3 With the script (installation from the repository only)

The scripts `deploy/backup.sh` and `deploy/restore.sh` belong to the installation from the repository
([1.2](#12-building-from-the-repository-developers)): they call Compose with the project name of
`deploy/docker-compose.yml` and do not work with the `compose.yml` from [1.1](#11-installation-without-the-repo).
There you back up through the interface ([10.1](#101-in-the-interface-recommended)).

`deploy/backup.sh` briefly stops the container and packs the entire data folder into one file: database,
`master.key`, `jwt_secret.key`, keyring, data of the extensions, scripts repository. Details and restoring:
[deploy/README.md](../../deploy/README.md#backup--restore) (German).

In the repository's `deploy/` folder (here, as an example, under `~/deck`):

```bash
cd ~/deck/deploy
sudo ./backup.sh ~/nodvard-deck-backups
```

To restore: `sudo ./restore.sh ~/nodvard-deck-backups/nodvard-deck-backup-<timestamp>.tar.gz`
(asks first).

If your user is in the `docker` group, leave out `sudo`. Without access to Docker, both scripts abort before they stop
or change anything (`backup.sh` reports "Docker is not responding" („Docker antwortet nicht …“)). Started with `sudo`,
the backup still belongs to you, not root, and only you can read it.

How restoring works:

- `restore.sh` checks the file, first unpacks it **next to** the current data and then swaps it in. For a short time
  this needs twice the space. If unpacking fails (disk full, Ctrl+C), Nodvard Deck keeps running on the old state.
- If the swap itself breaks off (for example due to a power failure), Nodvard Deck stays stopped. Then run the same
  command with the same file again: it finishes the swap and starts Nodvard Deck. Do not start it by hand before that;
  `backup.sh` refuses to run in the meantime.
- If the SSH connection drops after the confirmation, restoring carries on. How it ended is in `deploy/restore.log`.
- If a backup or a restore is already running, a second run stops without changing anything.

Important:

- **The backup contains the keys** (`master.key`, `vault_keyring.json`). With them, all passwords, SSH keys and
  tokens stored in Nodvard Deck can be decrypted – so lock the file away as carefully as the passwords themselves. Conversely:
  without these key files, the secrets are gone.
- The backup does **not** belong only on the same machine (an SD card can die). Fetch it from your PC:
  `scp admin@192.168.1.10:~/nodvard-deck-backups/*.tar.gz .`
- Regularly, e.g. Sundays at 04:30 (via `sudo crontab -e`, one line):

  ```
  30 4 * * 0 cd /home/admin/deck/deploy && ./backup.sh /home/admin/nodvard-deck-backups >> /home/admin/nodvard-deck-backup.log 2>&1
  ```

  The file then belongs to the owner of the target folder if that is not root (if the first `sudo ./backup.sh`
  created it, that is you), so fetching it with `scp` as above works. If a backup or a restore is already running,
  the run stops without changing anything (message in the log).

### 10.4 Updates: the copy before the migration

Before every update that rebuilds the database (a **migration**), Nodvard Deck automatically makes **a copy of the
database** at startup: `backups/vor-update/<time>_<from>_<to>.db` in the data folder. The **three newest** are kept.
You can see them under **Settings → System → "Copies before updates"** („Einstellungen → System → Kopien vor Updates“).

- **No copy, no migration.** If there is not enough space (a good 1.2 times the size of the database is needed),
  Nodvard Deck does not start but shows the rescue page with the amount that is missing (see
  [13](#13-nodvard-deck-does-not-start-the-rescue-page)). Free up space, press "Try again" („Neu versuchen“).
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
  everything else there is the backup ([10.1](#101-in-the-interface-recommended)).
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

### 10.5 Finding and installing a new version

Under **Settings → System → "Updates"** („Einstellungen → System → Updates“) you can see which version is running and
which one is the newest. Nodvard Deck looks **once a day** at ghcr.io to see which images exist; **"Search now"**
(„Jetzt suchen“) does it right away (at most once a minute).

- **Privacy:** ghcr.io belongs to **GitHub**. On every search, GitHub sees the IP address of your connection and the
  time; nothing else is sent, not even the installed version. **You can turn the search off:** switch off "Search for
  updates automatically every day" („Täglich automatisch nach Updates suchen“; permission `settings.write`), and the
  dashboard never looks on its own. "Search now" still asks when the switch is off – anyone with the permission
  `system.read` can press it, and GitHub sees that request too.
- **Which versions:** "Only finished versions" (default) or "Also pre-releases (beta)". Pre-releases (e.g. `0.7.0-rc1`)
  are not under `:latest`: to install one, put exactly `ghcr.io/nodvard/deck:0.7.0-rc1` at `image:`.
- **Without internet** the card says "Couldn't check (offline?)" and shows the last known state; that is not an error.
- **Installing** is done by your environment, not by the dashboard. With Compose (installed as in
  [1.1](#11-installation-without-the-repo)), in the folder with your `compose.yml`:

  ```bash
  docker compose pull
  docker compose up -d
  ```

  On Linux you may need `sudo` in front. If your file has a different name (e.g. `compose.standalone.yml` from an older guide), add `-f`
  and the file name (`docker compose -f compose.standalone.yml pull`).

  If a fixed version (e.g. `:0.5.0`) is set at `image:`, put the new version there first.
- **Going back:** write down the current version before the update (it is shown on the card). To go back, put it at
  `image:` again and restart. Details and the rescue page: [10.4](#104-updates-the-copy-before-the-migration).

## 11. When something goes wrong

**"HTTP 401" or "Not authenticated" („Nicht authentifiziert“) on an extension page** (Proxmox, Backups, Scripts …): In
the visible tab, Nodvard Deck renews the login by itself. If the tab sat in the background for a long time, it can
still happen – reload the page (F5).

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

- `All connection attempts failed` or a timeout: server off, wrong address or port. The address needs `https://` and
  `:8006`.
- `CERTIFICATE_VERIFY_FAILED`: the "Allow self-signed certificate" („Selbstsigniertes Zertifikat erlauben“) tick box is
  missing ([PROXMOX-TOKEN.md](PROXMOX-TOKEN.md#certificate-what-allow-self-signed-certificate-does)).
- `HTTP 401`: token ID or secret wrong, or token deleted in Proxmox.
- `HTTP 403 … Permission check failed`: privilege missing – table in
  [PROXMOX-TOKEN.md](PROXMOX-TOKEN.md#reference-which-call-needs-which-privilege).
  If individual VMs are missing entirely, they lack `VM.Audit`.
- Test from the machine running Nodvard Deck: the `curl` command in
  [PROXMOX-TOKEN.md](PROXMOX-TOKEN.md#check-before-you-enter-it-in-nodvard-deck).
- A failed Proxmox (e.g. pve2 off) no longer slows the others down; it simply shows as "unreachable". If it is off for
  longer: on the Proxmox and Backups pages, under "Manage connections" („Verbindungen verwalten“), switch it to
  "disabled" („deaktiviert“); then no notifications come either.

**Server missing in Terminal / "Noch kein Server prüfbar" (no server checkable yet) or "Keine Linux-Server mit SSH-Zugang
gefunden" (no Linux servers with an SSH login found) in Nodvard Shield:**
No SSH login stored: Settings → Servers & access ([5.2](#52-set-up-ssh-access--three-ways)). Nodvard Shield also takes only
servers that are entered as `linux` and – if "Only servers with tag" („Nur Server mit Markierung“) is filled in in its
settings – only servers with that tag.

**"Keine root-Rechte …" (no root rights):** sudo without a password is missing
([6.](#6-root-rights-without-a-password-and-the-docker-group)). The "Check connection" card shows it under "Root
rights" („Root-Rechte“).

**"Der Server-Schlüssel von diesem Server ist noch nicht bestätigt." (the server key of this server has not been
confirmed yet):** Nodvard Deck does not know this server's fingerprint yet (a new server while "Only remember new
server keys after my confirmation" is on), or you forgot it. Under Servers & access, press "Check connection" for the
server and confirm the fingerprint ([5.4](#54-check-the-connection)).

**"Host-Schlüssel … hat sich geändert" (host key … has changed):** Server reinstalled? Then, under Servers & access, on
the server's "Server key" card, forget the old one, check again and confirm the new fingerprint. If not: first find out
why – it can also be an attack.

**"Server antwortet nicht (Zeitüberschreitung …)" (server not answering, timeout), "Der Server lehnt die Verbindung ab
…" (the server refuses the connection), "Kein Weg zum Server …" (no route to the server) or "Den Namen … kennt das Netz
nicht." (the network does not know the name)** in the terminal or file manager (there with "Zugriff auf die Quelle
fehlgeschlagen:" (access to the source failed) in front): Nodvard Deck cannot reach the server. The sentence usually
names address and port. Check that the server is on, that the address under Servers & access is correct and that SSH
runs there. There is no traceback about it in the log.

**"Server nicht erreichbar – bitte gleich noch einmal versuchen." (server unreachable – please try again in a moment) at
login:** The dashboard is not answering. On the machine running Nodvard Deck, check
`curl -s http://localhost:8080/api/v1/health` and, in the folder with the Compose file, `sudo docker compose ps`.
If **"Server meldet: …"** (server reports: …) appears instead (also when reloading the page), the dashboard is
answering and names the reason, e.g. that the disk is full – then fix that reason. You are not signed out in the
process.

**"Gerade sind viele Anmeldungen gleichzeitig im Gange. Bitte versuche es in ein paar Sekunden noch einmal." (many
logins are in progress at the same time, please try again in a few seconds) at login:** Nodvard Deck checks only a few
passwords at the same time, so that many login attempts at once do not slow the dashboard down. Wait a few seconds and
try again; anyone who is already signed in keeps working normally. If the message appears often, there are very many
login attempts at once; failed ones are in the audit log („Anmeldung fehlgeschlagen“ – login failed). For a name that
does not exist, the "Who" column („Wer“) only shows "not signed in · unknown" („Nicht angemeldet · unbekannt“), never
the input itself (it could be a password typed into the name field). Only the owner sees an identifier (`username_ref`)
and the length of the input when expanding the row; the same input has the same identifier.

## 12. Locked out? Emergency commands

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
emergency command), the login ends immediately – but someone who still has an access key that was already issued in the
browser can keep working with it for **up to 15 minutes**, until it expires by itself. In case of serious suspicion,
therefore also change the password and wait a few minutes. Terminal and console do not wait for this period: open
sessions of an ended login close after at most 15 seconds, and new ones can no longer be used with it.

## 13. Nodvard Deck does not start: the rescue page

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
     [10.4](#104-updates-the-copy-before-the-migration)).
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

## Appendix: Without the interface

Normally you use the "Servers & access" page (5.). Only if the interface is not reachable (e.g. during the first setup
over SSH on the machine running Nodvard Deck) can everything also be done through the Nodvard Deck API with this small
helper script. There is no setup command and no connection check – you generate the key yourself, and you add the public part by hand to
`~/.ssh/authorized_keys`.

**Generate a key** (once, on the machine running Nodvard Deck):

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/nodvard_ed25519 -C nodvard
cat ~/.ssh/nodvard_ed25519.pub        # this one line goes onto the servers
```

Without a passphrase (`-N ""`), because Nodvard Deck does not ask for a passphrase when connecting. Nodvard Deck gets an
encrypted copy of the private key of its own; you only need the file on this machine for adding it and for testing.

**Save the helper script** (once, on the same machine) – paste the whole block into the shell; it creates
`~/lattice-server.py`. (The script's prompts and messages are in German, like the interface.)

```bash
cat > ~/lattice-server.py <<'EOF'
#!/usr/bin/env python3
"""Nodvard Deck: Server anlegen, Adresse korrigieren, SSH-Zugang hinterlegen (docs/11)."""
import getpass, json, os, urllib.error, urllib.request

API = os.environ.get("LATTICE_URL", "http://localhost:8080").rstrip("/") + "/api/v1"
token = None


class Fehler(Exception):
    pass


def call(method, path, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            raw = res.read()
            return res.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as err:
        raise Fehler(f"HTTP {err.code}: {err.read().decode(errors='replace')[:300]}") from None


def ask(text, default=""):
    value = input(f"{text}{f' [{default}]' if default else ''}: ").strip()
    return value or default


try:
    status, data = call("POST", "/auth/login", {
        "username": ask("Nodvard-Deck-Benutzer").lower(),
        "password": getpass.getpass("Passwort: "),
        "client_type": "cli",
    })
    if status == 202:  # Zwei-Faktor-Anmeldung ist eingeschaltet
        status, data = call("POST", "/auth/mfa", {"mfa_token": data["mfa_token"], "code": ask("Code aus der App")})
except Fehler as exc:
    raise SystemExit(f"Anmeldung fehlgeschlagen: {exc}")
token = data["access_token"]

while True:
    try:
        _, hosts = call("GET", "/hosts")
    except Fehler as exc:
        raise SystemExit(f"{exc} -- nach 15 Minuten läuft die Anmeldung ab, dann Skript neu starten.")
    print()
    for h in sorted(hosts, key=lambda h: h["name"]):
        print(f"  {h['id']}  {h['name']:<26} {h['address']:<16} {h['os_family']}")
    print("\n1 = Server anlegen  2 = Adresse ändern  3 = SSH-Zugang hinterlegen  4 = SSH-Zugänge zeigen")
    print("5 = gemerkten SSH-Host-Schlüssel vergessen  q = Ende")
    choice = ask("Auswahl", "q")
    try:
        if choice == "1":
            name = ask("Kurzname, klein und ohne Leerzeichen (z. B. pi-host)")
            _, h = call("POST", "/hosts", {
                "name": name,
                "display_name": ask("Anzeigename", name),
                "address": ask("IP-Adresse"),
                "os_family": ask("Betriebssystem (linux/windows)", "linux"),
                "tags": [t.strip() for t in ask("Markierungen mit Komma (z. B. docker), leer = keine").split(",") if t.strip()],
            })
            print("Angelegt, ID:", h["id"])
        elif choice == "2":
            host_id = ask("ID des Servers")
            call("PATCH", f"/hosts/{host_id}", {"address": ask("Neue IP-Adresse")})
            print("Gespeichert.")
        elif choice == "3":
            host_id = ask("ID des Servers")
            user = ask("SSH-Benutzer", "nodvard")
            port = int(ask("SSH-Port", "22"))
            if ask("Anmeldung mit Schlüssel (s) oder Passwort (p)?", "s") == "p":
                kind, secret = "ssh_password", getpass.getpass("SSH-Passwort: ")
            else:
                with open(os.path.expanduser(ask("Privater Schlüssel", "~/.ssh/nodvard_ed25519"))) as f:
                    kind, secret = "ssh_key", f.read()
            call("POST", f"/hosts/{host_id}/credentials",
                 {"kind": kind, "username": user, "port": port, "secret_value": secret, "is_default": True})
            print("SSH-Zugang gespeichert und ab jetzt der Standard für diesen Server.")
        elif choice == "4":
            _, creds = call("GET", f"/hosts/{ask('ID des Servers')}/credentials")
            for c in creds:
                print(f"  {c['kind']:<13} {c['username']} Port {c['port']}{'  (Standard)' if c['is_default'] else ''}")
            if not creds:
                print("  Noch kein SSH-Zugang hinterlegt.")
        elif choice == "5":
            host_id = ask("ID des Servers")
            call("DELETE", f"/hosts/{host_id}/known-hosts/{ask('Schlüsseltyp aus der Fehlermeldung', 'ssh-ed25519')}")
            print("Vergessen. Jetzt in der Oberfläche „Verbindung prüfen“ drücken und den neuen Fingerabdruck bestätigen – vorher verbindet sich Nodvard Deck nicht mehr mit dem Server.")
        elif choice.lower() == "q":
            break
    except (Fehler, OSError, ValueError) as exc:
        print("Fehler:", exc)
EOF
```

**Use it:**

```bash
python3 ~/lattice-server.py
```

Log in with a Nodvard Deck account that may manage servers (owner or `admin`). The script shows all servers with their
**ID** (which is also in the browser address of a server page: `/hosts/<ID>`). With 1 you create a server, with 2 you
change the address (a stored SSH password is deleted in the process; store it again with 3), with 3 you store an
SSH login (key `~/.ssh/nodvard_ed25519` or password), with 4 you show the
logins, and with 5 you forget a remembered server key (after the server has been reinstalled). The script does not
confirm fingerprints. After forgetting (5), Nodvard Deck only connects again once you have pressed "Check connection" in
the interface and confirmed the new fingerprint. The same applies to new servers as long as "Only remember new server
keys after my confirmation" is on (on new installations from the start).

The key on the server is then added by hand, e.g. for a user of its own:

```bash
sudo adduser --disabled-password --gecos "Nodvard Deck" nodvard
sudo install -d -m 700 -o nodvard -g nodvard /home/nodvard/.ssh
echo 'PASTE THE LINE FROM nodvard_ed25519.pub HERE' | sudo tee /home/nodvard/.ssh/authorized_keys
sudo chown nodvard:nodvard /home/nodvard/.ssh/authorized_keys
sudo chmod 600 /home/nodvard/.ssh/authorized_keys
sudo usermod -aG docker nodvard        # only where Docker runs
```

For root rights without a password (see [6.](#6-root-rights-without-a-password-and-the-docker-group) – practically
root!):

```bash
echo 'nodvard ALL=(root) NOPASSWD: ALL' > /tmp/nodvard-sudo
sudo visudo -cf /tmp/nodvard-sudo && sudo install -m 0440 -o root -g root /tmp/nodvard-sudo /etc/sudoers.d/nodvard-nodvard
rm /tmp/nodvard-sudo
sudo visudo -c
```

If the last `visudo -c` reports an error, delete the file again **immediately**
(`sudo rm /etc/sudoers.d/nodvard-nodvard`) – a broken sudo file can disable sudo. The file name must not contain a
dot, otherwise sudo ignores the file. To test, from the machine running Nodvard Deck:

```bash
ssh -i ~/.ssh/nodvard_ed25519 nodvard@SERVER-IP 'sudo -n true && echo sudo-ok; docker ps >/dev/null && echo docker-ok'
```
