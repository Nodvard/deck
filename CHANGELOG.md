# Changelog

All notable changes to Nodvard Deck are documented here. The project uses
[Semantic Versioning](https://semver.org/); until 1.0, minor versions may contain breaking
changes.

The detailed release notes (in German) live in
[`backend/src/nodvard_deck/changelog/versions/`](backend/src/nodvard_deck/changelog/versions/) and are
shown inside Nodvard Deck under *Settings → About Nodvard Deck*. This file is the short English summary.

## [Unreleased]

## [0.12.0] – 2026-10-31

Docker stacks from Git, Fail2ban in Nodvard Shield and tighter limits for modules and logins.

The web interface is in German only for now, so names of buttons and pages are given in German as they appear, with a
translation in brackets.

### Upgrade notes

- Servers that get root rights only through helper scripts: run the setup command again. Until then, Nodvard Shield
  blocks actions that need root on those servers. Servers with a full sudo rule or root login need no action.
- Rename `LATTICE_*` variables to `NODVARD_DECK_*`. In the `.env` file next to the Compose file: `LATTICE_PORT` →
  `NODVARD_DECK_PORT`, `LATTICE_DNS_1` → `NODVARD_DECK_DNS_1`, `LATTICE_DNS_2` → `NODVARD_DECK_DNS_2`.
- Remove the old full sudo rule `<user> ALL=(root) NOPASSWD: ALL` where the overview suggests it. The release notes and
  the setup guide, section 6, show how.
- Check third-party extensions and your own scripts: the Python packages `lattice` and `lattice_sdk`, the classes
  `LatticeExtension` and `LatticeError` and the entry point group `lattice.extensions` are gone. Change `import lattice`
  or `python -m lattice.…` to `nodvard_deck`, and `window.__lattice` to `window.__nodvardDeck` in extension interfaces.
- No database migration.

### Added

- Docker: a stack that Deck manages (created in Deck or adopted with “Übernehmen”) can use a Git repository as its
  source (`https://` only).
- Docker: before applying, you see which files would be new or changed, including the Compose diff and hints about
  anything that does not come from the repository.
- Docker: changes from Git are applied after a confirmation and an approval (“Freigabe”), with the permissions of the
  stack folder's owner.
- Docker: if Compose rejects the new files, the previous state is restored immediately.
- Docker: “Zurück zum Stand davor” (back to previous state) restores the files from before the last apply and only
  removes files Deck wrote itself that nobody has changed since.
- Docker: Deck never replaces existing environment files such as `.env`, and only removes one it created itself when you
  use “Zurück zum Stand davor” (back to previous state).
- Docker: only the services named in the approval are restarted, and “Jetzt neu starten” (restart now) handles any
  others with its own approval.
- Docker: after a restart, Deck checks that the services keep running, including ones that were stopped before.
- Docker: stack volumes can be backed up before applying, which stops the services using them during the backup.
- Docker: those backups are restored in the Stacks tab under “Sicherungen” (backups).
- Docker: Git credentials for private repositories, with the token kept in the vault (“Tresor”) and only sent to the
  server it is for.
- Nodvard Shield: the intrusion protection shows the addresses Fail2ban has banned on each server, and you can unban
  them or ban addresses yourself.
- Nodvard Shield: your browser's IP address and the address Deck uses to reach the server cannot be banned, so nobody
  locks themselves out.
- Nodvard Shield: the update centre shows how many Ubuntu updates are held back by phased rollout, with a short
  explanation.
- Nodvard Shield: “Alle einspielen” (install all) only installs those updates once the server's turn comes, and the
  confirmation points this out beforehand.
- Server setup: “Volle sudo-Regel entfernen” (remove full sudo rule) under “Erweitert” (advanced) checks every helper
  script before and after, and restores the rule if anything breaks.
- Servers: the new option “Volle sudo-Regel gewollt” (full sudo rule wanted) is for people who run their own sudo
  commands through Deck.
- Settings: a hint when the owner logs in through a home network proxy that is not listed as trusted, because without
  that entry Deck cannot see the real addresses.

### Changed

- First setup: two-factor login can be skipped with “Später einrichten” (set up later), even after starting it.
- Modules: a module that crashes on start shows the reason and offers “Neu starten” (restart) and “Ausschalten” (turn
  off).
- Updates: the “Updates” card shows whether a new version is approved and whether your update helper checks approvals
  itself.
- Update helper: its Compose file is attached to every release and names the checksum of the verified helper.
- sudo: “Verbindung prüfen” (check connection) and the overview flag a full sudo rule that is no longer needed and link
  to the right setup command.
- Backups: if Deck cannot write to the backup folder, “Einstellungen → System” (Settings → System) shows the user, the
  folder's owner and the command to fix it.
- Backups: in that case, push notifications and job logs also say where to find help.
- Pi-hole and Nginx Proxy Manager: when unreachable, they explain in plain German why and what to do next, instead of
  showing an English error message.
- Home page: widgets only show buttons you are allowed to use.
- Home page: the owner sees which `LATTICE_*` settings are still in use and what they are called now.
- Logins: logins that expired or ended long ago are deleted every night, including their address and browser.
- Audit log: when a module proposes an action on behalf of the AI, a schedule or a person, the entry also names the
  module.
- Interface: Deck recognises a new version of its start page by its content instead of its file size and modification
  time, and allows exactly its inline scripts right away.
- Interface: only the Nodvard Deck names are used, so a browser tab opened before the update needs one reload when it
  first opens a module page (the page offers a button).
- Licences: the third-party licence list names the chosen licence for dual-licensed packages and includes the image's
  operating system packages.
- Docs: the setup guide has a table of what the helper scripts may do as root, and the file watcher section says what
  root reads from watched files.

### Deprecated

- `LATTICE_*` environment variables still work in 0.12 and will be removed in one of the next versions, 1.0 at the
  latest.

### Removed

- The Python packages `lattice` and `lattice_sdk`, the classes `LatticeExtension` and `LatticeError` and the entry point
  group `lattice.extensions`: extensions that use them are no longer loaded, and the extension list shows which ones,
  why and what helps.
- Extensions whose interface uses the old names no longer show their page until their author ships a new version.
- Your own scripts with `import lattice` or `python -m lattice.…` must be changed to use `nodvard_deck`.

### Fixed

- Overview: with many widgets it now loads reliably instead of failing with a database error after half a
  minute. Thanks for the report!
- Vault: a new password, token or key now takes effect immediately for qBittorrent, Synology DSM, Pi-hole, Nginx Proxy
  Manager, Home Assistant, Jellyfin, Plex, Nextcloud and Uptime Kuma, and a pause after a rejected login ends right
  away. Before, depending on the module, the old values stayed in use for up to 30 minutes or until someone saved the
  settings or pressed “Verbindung testen” (test connection).
- Pi-hole and Nginx Proxy Manager: “Verbindung testen” (test connection) logs in again every time, so a wrong new
  password shows up.
- Approvals: expired approvals are labelled “Abgelaufen” (expired) and can only be dismissed.
- Docker: a server tagged both “docker” and “docker-bedienen” counts as a full Docker server.
- Docker: setup, “Verbindung prüfen” (check connection) and the overview now report a missing Docker helper script as a
  problem.
- Docker: “Verbindung prüfen” and the overview now show correctly that an older Docker helper script keeps working until
  the setup command runs again.
- Docker: on a server that lost its “docker” tag, image updates, stack actions, Portainer migration and registry login
  now say why instead of just “Kein Docker-Host.” (no Docker host).
- Docker: if a volume restore aborts before the data swap, Deck starts the stack again and removes the half-extracted
  folder.
- Docker: if a stack stays stopped after such an abort, a message gives the reason and the next step.
- Docker: an unexpected registry response only makes that image uncheckable, not the whole server.
- Helper scripts: for an ACL on the helper script folder, setup now suggests `setfacl -b` followed by `chmod g-w`
  (`setfacl -b` alone was not enough).
- Nodvard Shield: files with permissions such as 044 or 000 can be restored from quarantine again (on servers with
  helper scripts only, after running the setup command again).
- Synology DSM: Hyper Backup integrity checks no longer show up as errors.
- Server setup: if you disabled Deck's key on a server with a leading `#`, running the setup command again puts back a
  working key.
- Jobs: the nightly cleanup of old job runs no longer stops on an unusable file name from a restored backup.
- Display: Docker stacks with many buttons wrap, dates and times are always in German, and the wording of inventory and
  proxy hints is fixed.

### Security

- Updates: Deck only offers “Jetzt aktualisieren” (update now) for versions approved at their release with the
  project's signing key.
- Updates: a current update helper verifies the signature of that approval itself and rejects a wrong one. Until 1.0
  the helper also accepts a request without an approval, for example from an older Deck version; from 1.0 the
  signature is mandatory for every update.
- Updates: with a current update helper, “Jetzt aktualisieren” (update now) installs exactly the version the update
  check verified.
- Updates: if the update has changed since, the button downloads nothing and asks you to check for updates again.
- Logins: access tokens stop working immediately on logout, on password change (for the other logins) and when an
  account is deactivated and activated again, instead of up to 15 minutes later.
- Logins: after 10 failed logins for one user name within 24 hours, each further attempt from the same network waits
  twice as long as the previous one, up to 5 minutes, and you get an alert.
- Logins: the throttling applies per network, so you can still log in right away from your home network, a VPN or any
  other network.
- Logins: against attackers with very many addresses, a long password and two-factor login still help.
- Browser: camera, microphone, location and other devices Deck never needs are blocked.
- Browser: other sites can no longer embed Deck, except for the logo, icons and the reachability check.
- Browser: the interface only loads images, data and connections from its own server, and the browser now blocks other
  addresses instead of only reporting them.
- Modules: database access only reaches each module's own tables, not accounts, the vault or the audit log.
- Modules: a module only gets a vault secret with the right for that exact secret, not even indirectly, and never the
  accounts' two-factor keys.
- Modules: from the dashboard settings, modules only read the time zone and maintenance window, not for example the
  approval rules or the list of trusted proxies.
- Modules: a module limited to certain address ranges can no longer get around that through host names.
- Modules: only the proposing module and modules allowed to run commands learn which command an action ran.
- Modules: the audit log only names a person as the actor if they were actually involved.
- Modules: an extension that wants to take over the data of a removed module needs an admin's permission under
  “Einstellungen → Erweiterungen” (Settings → Extensions).
- Helper scripts: they are only installed in folders that nobody but root can write to, not even a group.
- Helper scripts: if the folder is too open, setup names the reason and the fix, and “Verbindung prüfen” (check
  connection) warns about servers already set up.
- Docker: a proposed action no longer runs once the server has been switched to “Nur bedienen” (operate only), except
  starting, stopping and restarting containers.
- Docker: a proposed Docker action no longer runs on a server that is no longer tagged as a Docker server.
- Docker: after running the setup command again, “Nur bedienen” (operate only) always uses Docker on the server itself,
  even if root points to a different Docker daemon there through `DOCKER_HOST` or a Docker context.
- Nodvard Shield: without the helper script, a file is only quarantined if ClamAV reports it again at that moment.
- Nodvard Shield: restore only puts a file back where it was, with its old permissions, also without the helper script.
- Nodvard Shield: the confirmation and approval for a restore say “an den hinterlegten Ort” (to the stored location),
  and the result names where the file actually went.
- Nodvard Shield: files quarantined with Deck 0.8 or older have to be put back or deleted by hand, because their
  original location is unknown.
- Nodvard Shield: managing incidents requires the `soc.manage` right, and with only `soc.read` they stay readable.
- Backups: the user ID and fix command for a failed backup only appear under “Einstellungen → System” (Settings →
  System), not in logs and notifications.
- Dependencies: document text recognition (Tesseract) gets the latest security updates with every release.
- Dependencies: the bundled Git library is up to date and no longer crashes on deliberately broken Git data.

## [0.11.1] – 2026-10-24

Security update: narrower rights during server setup and protection against modified backups.

- **Security:** if no enabled module needs root, the "root without password" option no longer sets up sudo (previously
  full sudo). Helper scripts are only placed in folders that nobody but root and the root group can write to,
  including extended ACLs. A Deck key already present without restrictions gets them during setup. Documents,
  inventory images, job run output and the Shield quarantine only use files where Deck stores them, so a modified
  backup cannot make Deck read, move or delete other files; the document preview only shows PDFs and images. Pages
  only run scripts served by Deck. A disabled job can no longer be started through the API. Docker registry login
  approvals are bound to the proposed user name, and a volume restore checks the archive is still the verified one.
- **Fixed:** "Check connection" no longer asks for sudo when no module needs root; an expired proposal is logged when
  someone approves it; Docker restore, backup list, Portainer migration, registry list and file manager fixes.
- **Note:** no database migration. On older Debian, where the group "staff" can write to `/usr/local`, run
  `chmod g-w /usr/local /usr/local/lib` as root before setting up a server with helper scripts only.

## [0.11.0] – 2026-10-23

Editable home page, Nodvard Dock as a ready-made image and setup with helper scripts only.

- **New:** widget groups on the home page that can be collapsed and show a dot when something needs attention, and an
  edit mode for position, height, width, group and group names.
- **New:** Nodvard Dock is published as `ghcr.io/nodvard/dock` (amd64, arm64). Private Docker registries with
  credentials in the vault and a "Registries" tab per server; volume backups of a stack can be restored (after
  checking archive and free space) and deleted.
- **New:** Docker mode "Operate only" (tag `docker-bedienen`): list, start, stop and restart containers and read their
  logs without adding the login user to the docker group, through the fixed helper script `docker-root`; no stacks,
  images, deleting or console there. "Check connection" warns if such a server's login can still do everything.
- **New:** Synology DSM (beta) supports several devices with their own name, account, password and certificate; an
  existing setup is taken over automatically.
- **New:** custom port ranges per program in intrusion detection, capped at 1000 ports unless marked as wide.
- **Security:** new servers get only the helper scripts' narrow sudo rules when those are enough, full rights stay
  available under "Advanced"; the helper folder check includes ACLs and new users' groups. Updated Pillow, certifi
  and markdown-it. The "Operate only" helper checks every call itself. Duplicate short names in module settings no
  longer send a password to another entry's address. Docker hides transformed variable values in errors.
- **Improved:** "Check connection" recognises helper-only servers and keys rejected by Dropbear; one alert for a
  degraded Synology pool with a failed volume; Docker volume backups record their volumes.
- **Fixed:** password setup, Shield updates and audit on Dropbear servers (DietPi); a Proxmox node that stops
  answering for 4 minutes no longer counts as online; random DNS ports of Pi-hole and similar services; many Shield,
  Docker and Synology fixes; no tracebacks on shutdown during scheduled tasks.
- **Note:** one database migration (home page groups), applied automatically.

## [0.10.0] – 2026-10-18

Docker stacks, Synology DSM (beta) and servers grouped by type.

- **New:** a "Stacks" tab in the Docker module. Stacks can be adopted, applied, stopped, taken down, edited as text or
  as a form (validated by Compose, with a diff, approval and the last 20 versions), created from a template, updated as
  a whole with an automatic rollback (except for databases) and an optional volume backup, and deleted. Stacks from
  Portainer move into their own folder under the same name, and a container started without Compose can be turned
  into a stack.
- **New:** container logs with a time range, search and download; the container list filters by server, stack and
  state and counts how often Docker restarted a container on its own.
- **New:** Synology DSM module (beta, read-only) with model, DSM version, load, volumes, storage pools, disks, updates
  and Hyper Backup tasks, three widgets and alerts when a pool, a disk, the temperature or a backup is not okay.
- **New:** servers are grouped into physical servers, VMs and LXC containers; a guest deleted in Proxmox is marked and
  can be removed with one click, and the overview points out servers that should be set up again for narrow
  permissions. "About Nodvard Deck" has a "Community and credits" section.
- **Security:** app tiles, search, widgets and module pages only open real web addresses. Errors from a broken .env
  file no longer quote its content, which can contain a password.
- **Improved:** widgets are as tall as their content, hide when their module isn't set up yet and look calmer. Docker
  shows when it runs in an LXC or VM and allows one change per stack at a time. Synology DSM works with older and newer
  DSM versions and can be pinned to its certificate. The installation guide covers many more platforms.
- **Fixed:** guests switched off in Proxmox no longer count as faults; pages fit at half screen width; detected apps no
  longer show twice and app addresses with a sub-page work. Nodvard Shield no longer reboots a server after a failed
  update, keeps file-watcher confirmations, keeps checking a slow quarantine move and handles a real-time watcher that
  fails to start. Helper script messages are no longer cut off, charts with many or invalid values work, and new
  database IDs are real UUIDs.
- **Note:** no database migration in this release. The ready-made Nodvard Dock image (`ghcr.io/nodvard/dock`) is only
  published from 0.11.0 on; until then it can be built from `deploy/nodvard-dock/`.

## [0.9.0] – 2026-10-16

A new look, Docker as its own module, a vault page and new integrations.

- **New:** a calmer look with three themes (classic light, plain dark as the default, pro dark) and a
  fourth one, "Bisher", with the look up to 0.8. After the update Nodvard Deck asks once whether you
  want to keep the new look; the choice applies to your account on every device. Admins can add
  their own CSS under *Settings → Appearance*.
- **New:** Docker is a module of its own (formerly "Service Matrix"). Per server it shows images,
  volumes and networks with their size and the containers using them; anything unused can be
  removed after approval.
- **New:** Pi-hole and Nginx Proxy Manager are modules of their own with their own pages. Settings,
  tiles, credentials and open suggestions move over by themselves. Pi-hole adds the most active
  clients, updating block lists and allowing or blocking a domain.
- **New:** read-only beta modules for Home Assistant, Jellyfin, Plex, qBittorrent, Uptime Kuma and
  Vaultwarden, each with a page and a tile, plus a Nextcloud tile with version, updates, storage
  and load.
- **New:** a "Vault" page under Settings lists all stored passwords, keys and tokens with their
  expiry date and what uses them, with reminders 14 and 3 days before a token expires.
- **New:** Nodvard Shield checks whether a server is suited for real-time virus scanning and can
  switch it on after approval (no button in the interface yet). A server that always runs warmer
  can get its own temperature limit.
- **Security:** Nodvard Shield and Server status bring their own small helper, each with a narrow
  sudo rule. Quarantine, updates, scans, audits and service restarts use it once it is installed on
  the server, and it is only called if it is exactly the shipped version. Replacing or deleting
  stored credentials needs your password and, with two-factor login on, the code. Only the owner
  can change how independently Nodvard Deck acts. Logo and favicon only come from Nodvard Deck
  itself, and images from other addresses in Markdown tiles show as links. Nextcloud files in the
  file manager are for admins only, and every access is logged. Other sites can no longer embed
  Nodvard Deck, and outgoing links no longer reveal which page was open. Image update checks no
  longer send the names of self-built images to Docker Hub. Lists return at most 1000 entries per
  request, the emergency page is local-only by default, and the backup script on the server
  encrypts when a backup key is set.
- **Improved:** the overview shows the servers in a table with load bars. The installation guide has
  one expandable section per way: command line, Portainer and Raspberry Pi. After Nodvard Deck
  restarts during an update, the update center shows the real result. File manager uploads are
  limited to 10 GB, with a clear message when the target is full. A temperature warning needs two
  hot readings in a row. Without its own setting, the container uses the host's name server.
- **Fixed:** a line break in "Watched files" no longer stops intrusion detection and the file
  watcher. Shield messages no longer repeat the event type. The metric history is stored reliably
  with several servers. "Test connection" in Uptime Kuma really asks again. The operating system is
  detected on regular servers too, not only on the Raspberry Pi.

## [0.8.0] – 2026-10-12

Proxmox in one step, an Apps page, the inventory as a homelab builder, and security updates.

- **New:** connect Proxmox in one step: enter address and password, Nodvard Deck sets up the rest
  and remembers the certificate. Alerts for nearly full Proxmox storage, missing recent backups,
  degraded ZFS pools and hot disks. Restore a guest from a backup as a copy, with a summary and
  checks before anything is created.
- **New:** an "Apps" page with all tiles in one place, folders and drag and drop, plus suggestions
  from Nginx Proxy Manager and detected containers. The address behind "Open" can be set per container.
- **New:** the inventory as a homelab builder: manufacturer, model, serial number and product link
  per device, build projects with what has been spent, racks with height units, reading the parts
  of a Linux server, and importing an order list (for example your Amazon order history). Documents
  can be attached to inventory items and picked up from a Nextcloud folder.
- **New:** push notifications also via Telegram, Discord, Gotify, Pushover, Matrix, Slack and
  Mattermost, with a minimum level. Servers can be set up once with a password instead of a command
  on the server. A shell into running containers, a file viewer and "important places" per server.
- **New:** the vault shows which module, server or account uses a secret and when it was last
  used, with an optional expiry date.
- **Security:** script IDs from requests can no longer point outside the script store. SVG uploads
  and Nextcloud (WebDAV) answers are read with a hardened XML parser. Checking a crafted backup
  before a restore always finishes quickly. Proxmox errors no longer show internal details in the
  browser. The password for server setup only goes to a host whose key or certificate is confirmed.
  With two-factor login on, changing your password needs the code; the login lockout counts IPv6
  addresses per /64 network; only the owner can change trusted proxies; extensions can only replace
  their own vault entries. Build tool dependencies updated.
- **Improved:** a clearer Proxmox page, picking servers by tiles, a Shield score that also counts
  open security updates, Fail2ban and SSH settings, restarts that are checked to keep running, and
  clearer module names ("Proxmox backups", "Push notifications", "Server status").
- **Fixed:** lists that fail to load show the reason and "Try again" instead of looking empty. App
  tiles open the right address. The Shield score no longer drops during a scan. A backup with
  warnings no longer counts as failed.

## [0.7.0] – 2026-10-07

Update helper, Shield under its own name and more protection.

- **New:** an optional update helper (switched on with its own Compose file). With it, the owner
  installs future versions from Settings → System → Updates with password and two-factor code,
  follows every step, and goes back once within 7 days if needed. A new version that does not
  start is rolled back automatically. The first update by button is the step from 0.7.0 to the
  next version.
- **New:** "Propose all updates" and "Propose all security updates" in the Nodvard Shield update
  center, each still approved under "Actions".
- **Security:** turning off two-factor login and creating new recovery codes need password and
  code; with two-factor on, downloading and restoring a backup need the code too. The lockout after
  wrong codes survives a restart. User accounts, autonomy, blocked commands and maintenance windows
  are written to the audit log. Extensions follow no redirects and cannot write the core's own
  audit entries.
- **Fixed:** redirects and unexpected answers from Pi-hole, Nginx Proxy Manager, ntfy, Nextcloud,
  Proxmox and the AI server show a clear sentence instead of raw text or a false "works". The
  hardening audit also finishes on servers where Lynis needs more than an hour, shows "running
  since …" right away and never starts twice. Ignored or restored findings stay out of quarantine.
  Scripts keep `$HOME`, `$(date)` and similar, and a failed scheduled run counts as failed. Broken
  schedules can no longer be saved and no longer stop Shield or Scripts. Automatic updates restart
  the dashboard's own server last.
- **Improved:** Nodvard Shield uses `shield` as its internal name; old links, settings and
  findings keep working, and extensions can be renamed later without losing data. Names instead
  of internal IDs on the actions page, in notifications and in the audit log.

## [0.6.2] – 2026-10-02

Security and polish release.

- **Security:** fixes from a full security review:
  - Files on servers (SSH) only for users who may run commands on servers. File access, actions
    and failed logins land in the audit log; login names that do not exist are never stored in
    plain text.
  - Commands and output of actions only for users with server rights. The live connection
    re-checks account, session and rights and ends with its token.
  - Two-factor codes and login steps are single-use, with a per-account lockout; setting up 2FA
    asks for the current password. Deactivating an account or changing a password signs it out
    everywhere, terminal and console included.
  - Extension routes require a login by default; extension secrets are bound to their target
    address. Console and extension requests never follow redirects or use proxies from the
    environment.
  - Request size limit (1 MiB, with separate limits for uploads), SVG logos checked and served
    sandboxed, hardened rescue page, backups no longer carry Git settings, hardened image
    (code owned by root, data readable only by its owner).
  - The AI container watch only ever proposes a fixed container restart.
  - New installations ask you to confirm the fingerprint of a new server before any password
    or key is sent.
- **Fixed:** `backup.sh`/`restore.sh` work with sudo, with a lock and a safe swap; "Retry" on
  Proxmox backups keeps the job's retention; copying a file onto itself no longer empties it;
  quarantine restore follows no links; the virus-scan watcher no longer skips files silently;
  update check with reason and catch-up; no port alarms for random ports; the Pi deploy script no
  longer rolls back after a database migration.
- **New:** scheduled scripts can run without a click (per script, owner or admin only, ends with
  any change); new SSH logins are called `nodvard`; clearer error pages and hints; Nodvard Shield
  explains the next step and its terms; a server only turns green after a real login.

## [0.6.1] – 2026-10-02

- **Fixed:** every SSH connection failed in the Docker image with "Permission denied: '/root/.ssh/crt'"
  (the service ran as its own user but with root's home folder). Server pages, Service Matrix,
  Nodvard Shield and the file manager reach your servers again.
- **Security:** the antivirus scan can no longer be tricked by crafted file names.
- **New:** update check under Settings → System → Updates (daily, can be switched off), with a
  "What's new?" link to this changelog.
- The whole interface now uses informal German ("du"); API documentation only for signed-in admins
  or in development mode; a missing extension stays enabled; more complete third-party license notices.

## [0.6.0] – 2026-10-01

First public beta.

- **Set up without code:** published container image (`ghcr.io/nodvard/deck`), setup
  assistant with time zone, module choice and two-factor login, "First steps" card, empty
  states with next steps, demo data you can remove with one click.
- **Servers & access:** add servers in the web interface, Nodvard Deck generates the SSH key
  and shows a one-line setup command, "Test connection" explains what is missing, regular
  reachability checks with alerts.
- **Backup & restore:** encrypted backups (manual, scheduled, download), restore in the web
  interface or during setup, automatic copy before every database migration and a rescue
  page if a start fails, recovery codes and emergency commands for lost logins.
- **Custom apps:** your own tiles and links on the dashboard, next to discovered containers.
- **Monitoring:** disk-usage and temperature warnings, CPU/RAM/disk cards in the cockpit,
  Nodvard Shield incidents survive restarts and name the cause determined by the system.
- **Module settings:** "Test connection" for every module, secrets editable in place.
- **Renamed to Nodvard Deck:** packages `nodvard_deck`, SDK `nodvard_sdk`, extensions
  `nodvard_deck_ext_*`, environment variables `NODVARD_DECK_*`. Old names keep working.
- Project documents for the public release: PolyForm Noncommercial 1.0.0 license (source available,
  free for noncommercial use, commercial use only with permission), third-party licenses, README in
  German and English, contribution guide, security policy, code of conduct, English guides.

## [0.5.0] – 2026-09-30

- Apply container image updates from the Service Matrix with a preview, automatic health
  check and a kept backup image for rollback.
- Many fixes across the web interface and backend.

## [0.4.0] – 2026-09-30

- Image update check for Docker containers with optional daily push notification.
- New Nodvard Shield options, readable "proposed by" names in the gate, maintenance windows
  also mute watcher alerts, in-app changelog, many fixes.

## [0.3.0] – 2026-09-30

- Approved actions run in the background; updates keep running on the server even if the
  connection drops.
- Bulk approval in the gate, graceful VM/container shutdown, service restart, editable
  server tags, secret script parameters are no longer shown in plain text.

## [0.2.0] – 2026-09-29

- Security hardening: command injection in Nodvard Shield closed, login rate limiting, sessions
  revoked on password change.
- Reliability fixes across core and extensions, Network extension (Pi-hole, Nginx Proxy
  Manager), first-setup and Proxmox token guides.

## [0.1.0] – 2026-09-26

- First version: core with dashboard, server pages, approval gate, users and 2FA, audit
  log, secrets vault, web terminal and file manager, plus extensions for Proxmox VE,
  backups, Docker, Linux systems, security (Nodvard Shield), game servers, scripts, documents,
  inventory, Nextcloud and ntfy.
