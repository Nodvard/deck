# Root Rights without a Password and the Docker Group

> The Nodvard Deck interface is currently available in German only; an English interface is planned. UI labels are given in English with the German original in quotes.
>
> Deutsche Fassung: [../14-ROOT-RECHTE.md](../14-ROOT-RECHTE.md)

The short version is in
[first setup, appendix F](FIRST-SETUP.md#f-root-rights-without-a-password-and-the-docker-group). This page has
everything in detail: what the modules need root for, what the setup command sets up on the server for it, what the
helper scripts may do and how to get rid of a full sudo rule again.

- [What the tick boxes are needed for](#what-the-tick-boxes-are-needed-for)
- [What the "Root rights" tick box does exactly](#what-the-root-rights-tick-box-does-exactly)
- [The full sudo rule](#the-full-sudo-rule)
- [This is practically root](#this-is-practically-root)
- [Helper scripts](#helper-scripts) and [what they may do](#what-the-helper-scripts-may-do)
- [Switching Docker to "operate only"](#switching-docker-to-operate-only)
- [Proxmox nodes and existing users](#proxmox-nodes-and-existing-users)
- [What the file watcher reports after the setup](#what-the-file-watcher-reports-after-the-setup)

## What the tick boxes are needed for

If Nodvard Deck does **not** log in to a server as root (e.g. on the Pi), it runs root commands via `sudo -n`, that is,
without a password prompt. If sudo asks for a password, the function fails with "Keine root-Rechte: Das Dashboard
ist auf diesem Server nicht als root angemeldet und darf sudo nicht ohne Passwort nutzen." (no root rights: the
dashboard is not logged in as root on this server and may not use sudo without a password).

You do **not** have to change the setup command ([first setup, 3.3](FIRST-SETUP.md#33-run-the-setup-command-on-the-server)) by hand for this: the page sets the appropriate tick
boxes itself, because the extensions report what they need. The same tick boxes apply when you set up the server with
a password (Nodvard Deck then runs the same command).

- **"Root rights without a password"** („Root-Rechte ohne Passwort“) – needed for (the page names it behind the
  tick box):
  - **Nodvard Shield, antivirus:** move a file to quarantine, restore or delete it, Lynis hardening audit, install tools
    (ClamAV, Lynis, signatures, Fail2ban, automatic updates). Scans also run without root, but then only check files
    that the user may read. Nodvard Shield detects whether ClamAV and Lynis are installed without root as well (on
    Debian and the Pi's operating system, Lynis lives in `/usr/sbin`); after an installation it asks the server again right
    away. The **hardening audit** runs in the background on the server with low priority and can take from a few
    minutes to over an hour, depending on the server (limit 3 hours). Right after the click the server shows
    "Audit running since …" („Audit läuft seit …“), a second click does not start a second audit, and the dashboard
    may restart in the meantime. The most common Lynis findings get a German title in the result; a click shows what it
    means and what you can do (much of it is deliberately not needed in a home network, and the text says so). At the top,
    "Deine wichtigsten Baustellen" lists at most five findings with the biggest effect on the servers shown. The logs
    are kept on the server under `/var/lib/nodvard-shield-audit/`. Scans and the
    real-time guard never run at the same time on a server; for this lock Shield creates a small folder there:
    `/run/nodvard-shield` (as root) or `~/.nodvard-shield`. A file only goes into quarantine if ClamAV itself reports
    it again while it is moved, first where it is and then in a check folder only root can write to; this applies with
    and without the helper script. If ClamAV is missing or does not answer, the file stays where it is. A restore only
    puts the file back where Shield recorded it on the server when moving it, with the permissions it had (special ones
    such as `4755` or `044` included), again with and without the helper script. The question and the approval
    therefore name the path from the finding only "according to the finding" („laut Fund“); after the restore Shield
    names the place the file really went to, and if it differs from the finding, that is noted at the finding too. If
    the record is missing or invalid (for example for a file that went into quarantine with Nodvard Deck 0.8 or older),
    the file stays there, and you put it back or delete it on the server by hand. A finding that
    you ignored or restored is not moved to quarantine again by the next scan (unless ClamAV reports the same file
    with a different signature). To undo
    this, open "Findings & quarantine" („Funde & Quarantäne“), choose the filter "All, including done" („Alle, inkl.
    erledigt“) and click "Watch again" („Wieder beachten“): the finding is open again and the next scan reports the
    file again.
  - **Protection score** (0 to 100, on the overview): counts the antivirus (ClamAV, signatures, scans, open findings),
    the hardening and also open security updates, pending reboots, Fail2ban, the SSH settings (root or password login,
    in proportion to the number of affected servers) and open critical security events (5 points each, at most 15).
    Below it you see what currently lowers it and by how many points. If a server's last update or intrusion check
    failed or is older than three days, its old state does not count; it is then listed as „zuletzt nicht geprüft
    (ohne Abzug)“ (not checked recently, no deduction).
  - **Update center:** apply updates and reboot. Reloading the package lists before the check needs root – without
    it, you stay at the most recently loaded state. Updates run in the background on the server (with systemd as
    `lattice-upgrade-…`, an
    [old name that stays](FIRST-SETUP.md#m-old-names-on-older-installations), otherwise via `setsid`) and keep running even if the connection drops or the dashboard
    restarts. The log of every run is kept for 30 days under `/var/lib/nexus-updates/`. The dashboard waits 45
    minutes for the result; if a run takes longer, it initially shows as "No response" („Keine Rückmeldung“) (it is
    never terminated), but the dashboard keeps asking for up to about 3 hours, then corrects the entry and reports the
    result by push. If the dashboard restarts during that time, the entry stays as it is – then look in the log on the
    server.
  - **Intrusion protection and file watcher:** SSH log, Fail2ban status, processes behind open ports, SSH settings,
    checksums of files such as `/etc/shadow`. Only limited without root – Nodvard Deck says so, instead of falsely
    giving the all-clear. Blocking/unblocking IP addresses via Fail2ban works only with root.
    While Fail2ban runs, every server lists the banned IPs under "Banned addresses" („Gesperrte Adressen“), sorted by
    jail (the protection rule for one service, such as `sshd` for SSH), with "Unban" („Entsperren“) next to each
    address. From 20 addresses on there is a search; at most 200 are shown at once. Below it you ban a single address
    by hand, in the jail `sshd` or another one the server has; Shield knows which ones only after checking the server.
    A ban usually ends on its own after the jail's ban time. Shield does not ban addresses that belong to no other
    machine (such as `127.0.0.1`), the address of your browser (behind a reverse proxy reliably only once it is listed
    under "Trusted proxy", „Vertrauenswürdiger Proxy“; otherwise Shield also compares what the proxy wrote into
    `X-Forwarded-For`), nor the address Nodvard Deck uses to reach the server; otherwise you would lock yourself out. The server checks the last
    one itself as well before it bans (from `SSH_CLIENT` or `SSH_CONNECTION`); if it cannot tell, it bans nothing. If
    the server has no jail for SSH, Shield offers no "Ban" („Sperren“) for attackers. If an address was already free
    when you unban it, Shield says so instead of reporting an error.
    Intrusion protection reports each new open port on its own ("New open port", „Neuer offener Port“). Programs that
    pick random ports on every start or for every request (NFS, Tailscale, Pi-hole and other DNS services) count as one
    entry. For a program with a port range of its own, such as a game server, enter the program, protocol and range
    under Settings → Extensions → "Nodvard Shield" → "Advanced" („Erweitert“) → "Own port ranges per program"
    („Eigene Portbereiche je Programm“). New ports in that range are then no longer reported one by one. Any program
    can give itself any name, malware included: only enter programs you trust and keep the ranges small. Ports below
    1024 only count with the tick box "Also ports below 1024" („Auch Ports unter 1024“). A range covers at most 1000
    ports (a larger one only counts up to there), more only with the tick box "Wide range" („Breiter Bereich“); a wide
    range always starts at 1024 at the earliest, so SSH and other fixed services stay visible. At most 50 ranges count.
  - **Server status:** restart services (systemd). Afterwards Deck checks for up to 45 seconds whether the service runs and
    keeps running (twice, 10 seconds apart, without a new start; the check needs no root). If it fails right away or
    keeps restarting, the action counts as failed and says so.
  - **Docker in the "operate only" mode** („Nur bedienen“, servers tagged only `docker-bedienen`): view containers, read
    logs, start, stop and restart them through the helper script `docker-root`, without the docker group.
- **"Add to the docker group"** („Zur Gruppe docker hinzufügen“) – needed for the Docker module and the AI container
  watch of Nodvard Shield; they call `docker` without sudo. The group is only entered if it exists on the server. For a
  server tagged only `docker-bedienen` the page does not offer the group; the helper script is enough there.

Without either tick box you get the **lean variant**: its own user, key only, no root rights. That is enough for
Terminal, Files and Scripts.

## What the "Root rights" tick box does exactly

That depends on which modules need root on this server.

- **Only the helper scripts (default):** If at least one module needs root here and each of them has a helper script
  (Nodvard Shield, Server status and Docker in the "operate only" mode have one), the tick box sets up only those
  scripts (see "Helper scripts" below). Each gets its own rule
  `<user> ALL=(root) NOPASSWD: sha256:<checksum> /usr/local/lib/nodvard-deck/<module>-root`; there is no full sudo rule.
  With the checksum, sudo starts the script only as long as it is byte for byte the one the rule was set up for. If sudo
  on the server does not know checksums in rules or does not show them in `sudo -l` (sudo-rs, the default from Ubuntu
  25.10 on, and older sudo versions), the rule applies without it, and the command says so. There is nothing to do
  then: only root can still change the script. Servers that are already set up get the checksum the next time the setup
  command runs. The user may
  start exactly these scripts with sudo, and what they do is written in the scripts themselves (such as installing
  updates, quarantine, restarting services). Under "Was macht der Befehl?" (what does the command do?) you can read
  every script. First the command checks that the scripts can be set up safely: `sudo`, `base64`, `sha256sum` and
  `stat` must be present, every folder above `/usr/local/lib/nodvard-deck` must be a real folder owned by root that
  nobody but root may write to: no group, not even the root group, and nobody else. An ACL can only grant writing if
  the group bits allow it (they are then the mask), so looking at them is enough. If `/usr/local/lib/nodvard-deck`
  already exists, the same applies to it (otherwise the command creates it). If that fails, it changes **nothing**,
  names the folder, the reason and the fix (such as `chmod g-w /usr/local/lib` or `mkdir -p /usr/local/lib`) and ends
  with an error; "Mit Passwort einrichten" (set up with password) shows the same
  lines. Older Debian lets the group staff write to `/usr/local` and `/usr/local/lib`; there, as root,
  `chmod g-w /usr/local /usr/local/lib` helps. Fix the reason or, unless sudo is missing, choose "Volle
  Rechte" and set up again. "Volle Rechte" does not help Docker in the "operate only" mode: it works only through its
  helper script, and the command says so.
- **Full rights:** If a module needs root and has no helper script, or if you tick "Volle Rechte" (full rights) under
  "Erweitert" (advanced), the full rule is added. You only need that tick box if you start your own commands with sudo
  through Nodvard Deck, for example in scripts.
- **No module needs root:** Then the tick box sets up no sudo at all, not even the full rule. The page says so below the
  tick box. If you still want sudo, for example for your own commands in scripts, tick "Volle Rechte" under "Erweitert".

## The full sudo rule

The command only takes back a full rule that is already there if you explicitly ask for it: a server that has one
keeps running as before, even if you run the command there again. To get rid of it, run the command once more first and
press "Verbindung prüfen". Once every helper script has its own rule, "Root-Rechte" turns yellow with "sudo ohne
Passwort für alles ist hier nicht nötig. Die Hilfsskripte reichen." (sudo without a password for everything is not
needed here; the helper scripts are enough), and a subtle hint appears under "Braucht Aufmerksamkeit" on the overview
(checked every 6 hours, hidden with ×). The hint leads to the setup command, where "Volle sudo-Regel entfernen" (remove
the full sudo rule) is already ticked under "Erweitert". Run the command (paste it yourself or use "Mit Passwort
einrichten") and check again: "Root-Rechte" then says "sudo erlaubt nur die Hilfsskripte der Module, keine volle Regel.
Das reicht." (sudo allows only the modules' helper scripts, no full rule; that is enough).

This is how the command goes about it, so you do not lock yourself out:

- It first sets up the helper scripts and their rules as always. If anything goes wrong there, it leaves the full rule
  alone.
- If there is no rule from Nodvard Deck (only links, other content or none at all), it says so and changes nothing.
- It only deletes `/etc/sudoers.d/nodvard-<user>` and `lattice-<user>`, and only if the file contains exactly the line
  `<user> ALL=(root) NOPASSWD: ALL`. Links and files with other content stay, with a hint.
- Before that it asks every script for its version through sudo. That only shows that it runs (still with the full
  rule); if one does not answer, the full rule stays.
- After deleting, it checks the sudo configuration with `visudo -c` (if it was fine before) and asks every script
  again, now without the full rule. If any of that fails or the run is cut off (for example because the connection
  drops), it puts the rule back right away. It never changes the key or the login.
- If the user can still do everything through a rule from somewhere else (for example `010_pi-nopasswd` on Raspberry Pi
  OS or the group `sudo` with NOPASSWD), it says so. That rule stays; whether it can go is your call. As long as it
  applies, the check cannot show whether the helper scripts are enough on their own.

Afterwards your own commands with sudo through Nodvard Deck no longer work. If an update of Nodvard Deck brings new
helper scripts, the modules only get root there again once you set the server up once more; the overview tells you.

If you want the full rule back, set the server up again with "Volle Rechte". If you need it for your own commands with
sudo, tick "Volle sudo-Regel gewollt" (full sudo rule wanted) under "Bearbeiten", "Erweitert" for that server:
"Root-Rechte" then stays green and the hint does not appear. If no module needs root on the server at all, the command
brings no helper scripts and therefore cannot remove the rule. If you then do not need sudo for your own commands through
Nodvard Deck, delete it yourself (`sudo rm /etc/sudoers.d/nodvard-<user>`, on older servers `lattice-<user>`).
"Verbindung prüfen" then shows under "Root-Rechte": "Kein eingeschaltetes Modul braucht hier root, sudo ohne Passwort
ist nicht nötig." (no enabled module needs root here, sudo without a password is not needed).

The full rule is `/etc/sudoers.d/nodvard-<user>` with the line
`<user> ALL=(root) NOPASSWD: ALL`. The line is first written to a trial file and checked with `visudo -c`; only then
does it move into place with permissions `0440` (root). Afterwards `visudo` checks the installed file and the whole
configuration once more. On an error the file is removed again immediately ("sudo-Regel zurückgenommen" – sudo rule
withdrawn), followed by what `visudo` reports. If `visudo -c` already reports an error in someone else's file
**before** the setup (e.g. "bad permissions, should be mode 0440" because a file in `/etc/sudoers.d` has permissions
`0644`), only the check of the command's own files counts. The command then names that file in a hint;
`sudo chmod 0440 <file>` and `sudo chown root:root <file>` fix it. The command deletes an
older rule `lattice-<user>` only after this check and only if it consists of exactly the line above; otherwise it
stays in place and you get a hint. If `sudo` is not
installed at all, the command says so (as root `apt install sudo`; on Proxmox best log in as `root` right away).

## This is practically root

The page tells you so too: anyone who is in the `docker` group or has the full
sudo rule can do practically anything on the server. Without a helper script Nodvard Deck starts some root commands as
`sudo -n sh -c '…'`, so it then needs the full sudo rule, and anyone who may start `sh` as root may do anything. The
security comes from the fact that **only** this one user gets the rights, can log in **only with the key**, and the key
is stored encrypted in Nodvard Deck.

## Helper scripts

With the "root rights" tick box the command puts the modules' helper scripts (Nodvard Shield,
Server status and Docker in the "operate only" mode) into `/usr/local/lib/nodvard-deck` and allows each one with its own narrow sudo rule. If that fails
for a script, the command names the reason (folder or file not safe, tool missing, or `visudo` rejected the rule). With
the full rule that is a hint, and the module then uses the full rule; only Docker never does, so for `docker-root` it
is always an error. Without the full rule it is an error, because the module would then have no root at all: there the
pre-check covers folders and tools before anything changes (see above); a damaged script or a rejected rule is reported along the way with "FEHLER: …" and at the end "Nicht alles hat
geklappt" (not everything worked). The
modules call a script only if the one on the server is byte for byte the one that ships with Nodvard Deck. The
exception is Docker: it also calls an older version. If only root can change it and sudo allows it, Docker keeps root
with it until you run the setup again ("Veraltet" then comes with "Docker ruft es trotzdem auf …", Docker still calls
it); the rule with its checksum belongs to that older version. If the content on the server is not the one the rule was
written for, sudo does not start the script, and "Verbindung prüfen" does not count the rule as its own.
"Verbindung prüfen" (check connection) shows for each script whether it matches, and under "Root-Rechte" it says
"sudo erlaubt nur die Hilfsskripte der Module, keine volle Regel. Das reicht." (sudo allows only the modules' helper
scripts, no full rule; that is enough) when all of them match. If a script is missing, it shows "Fehlt auf dem Server"
(missing on the server, yellow); without the full rule, and for Docker always, it adds that the module has no root
here. If `docker-root` is missing next to the full rule, "Root-Rechte" is yellow too.
After an update that brings a new script it says "Veraltet" (outdated): then run the setup command once more.
You do not have to spot this yourself: if a script is missing or outdated while sudo may run scripts there (full rule
or a script's own rule), the overview shows a quiet hint under "Braucht Aufmerksamkeit" (needs attention) with a link
to the setup. Nodvard Deck checks this
every 6 hours and on every "Verbindung prüfen". The × hides the hint for that server until an update brings newer
scripts.

## What the helper scripts may do

The dashboard's user can run these subcommands as root through sudo, also without
going through the dashboard. Each one checks its own arguments and stops on anything else before it does anything. A
test runs every script in `sh` and `dash` with more than 200 invalid inputs (options instead of names, spaces, line
breaks, `;`, `$(…)`, `..`, relative paths, overlong values) and requires that no program with side effects starts.
The full sudo rule, in contrast, allows everything.

| Script | Subcommand | What it does as root | Limits |
|---|---|---|---|
| all | `version` | prints the version | no arguments |
| `system-root` | `service-restart` | restarts a systemd service and reports its state | only `.service`, `.socket`, `.timer`; never SSH, D-Bus, `systemd-*`, `networking`, `NetworkManager`, `dhcpcd`, `wpa_supplicant` or Nodvard Deck; other network services such as `firewalld` or `wg-quick@…` are allowed. Docker and containerd on the dashboard's own machine are blocked by the dashboard only; the script cannot tell which machine that is |
| `docker-root` | `ps`, `stats` | lists containers and their load | no arguments |
| `docker-root` | `inspect`, `id`, `id-state`, `state`, `ports` | reads container details in fixed formats | only valid names or IDs, at most 200 |
| `docker-root` | `logs` | reads a container's log | at most 5000 lines at the start, then following on request; time span only in minutes or hours |
| `docker-root` | `start`, `stop`, `restart` | starts, stops or restarts a container | exactly one valid name; there is no delete |
| `shield-root` | `guard` | reads logins from the SSH log, the effective sshd settings (port, root login, password and key login and similar), Fail2ban, open ports with the range for outgoing connections and the checksum, mode, owner, modification time and size of the watched files and programs | read only; absolute paths without `..`, `*` only for files |
| `shield-root` | `detect-templates`, `refresh-packages` | reads the Samba, NFS and Nextcloud settings or refreshes the package list | no arguments |
| `shield-root` | `quarantine`, `restore`, `delete`, `quarantine-state` | moves a file reported by ClamAV into quarantine, puts it back, deletes it there or reports its state | ClamAV checks the file again first; back only to the place the script noted itself; no links |
| `shield-root` | `ban`, `unban` | bans or unbans an address in Fail2ban | address only hex digits, `:` and `.`, at most 45 characters; jail only letters, digits, `_`, `.` and `-`, at most 64 characters. Fail2ban rejects anything that is not a valid address |
| `shield-root` | `install` | installs ClamAV, Lynis, Fail2ban or unattended-upgrades, fetches new virus signatures | only these five |
| `shield-root` | `reboot` | restarts the server in one minute (after 5 seconds without `shutdown`) | no arguments |
| `shield-root` | `upgrade-start`, `upgrade-run`, `upgrade-poll` | installs updates with apt, dnf or apk and reports progress | fixed kinds; single packages only for security updates with apt, names from a fixed character set |
| `shield-root` | `audit-start`, `audit-run`, `audit-poll`, `audit-stop` | starts, follows or stops a Lynis audit | run ID in a fixed form, fixed time limit, stops only its own run |
| `shield-root` | `scan`, `watch` (internally `scan-run`, `watch-run`, `odd-scan`) | scans folders with ClamAV | absolute paths, numbers with an upper limit, one run at a time, time limit |
| `shield-root` | `realtime-on`, `realtime-off`, `realtime-log`, `realtime-confirm` | turns ClamAV's real-time protection on or off, reads its log, checks reported files again | writes only fixed files under `/etc/clamav` and a systemd drop-in; folders absolute without `..`, at most 50 files |

Which files `guard` reads comes from the settings of Nodvard Shield's file watcher ("Überwachte Dateien", watched
files). Whoever may change that setting also decides for which files root reads the checksum, mode, owner, modification time
and size. The script never prints the content of those files.

## Switching Docker to "operate only"

If a server that is already set up with the docker group or the full sudo rule
gets the tag `docker-bedienen` instead of `docker`, it keeps both. The setup command takes back neither the group nor the
rule, so the login is still practically root there. "Verbindung prüfen" shows this in the items "Nur bedienen: Zugang nicht
in der Gruppe docker" and "Nur bedienen: keine volle sudo-Regel" (yellow). If the login is `root`, "operate only"
restricts nothing; then "Nur bedienen: Zugang ist nicht root" is yellow, and the login needs a user of its own. If a server
has both tags (`docker` and `docker-bedienen`), it is a full Docker server: the items "Nur bedienen: …" and the helper
script `docker-root` do not apply there, Docker runs through the docker group.
To switch:

1. Run the setup command once more with "root rights without a password". It puts `docker-root` in place and no longer
   offers the docker group.
2. "Verbindung prüfen": "Docker nur bedienen" must say that the helper script answers.
3. Take the login out of the group: `sudo gpasswd -d <user> docker` (takes effect at the next login).
4. If no other module needs full rights here, remove the full rule as described above.
5. "Verbindung prüfen" once more: the check logs in afresh for this, so the "Nur bedienen: …" items turn green right
   away, and from then on Nodvard Deck uses only new connections elsewhere too.

Back to a full Docker server: set the tag `docker` again and run the setup command with "Add to the docker group".

## Proxmox nodes and existing users

**Proxmox nodes:** There, Nodvard Deck is easiest to log in directly as `root` (enter user `root` when generating).
Then it needs neither sudo nor tick boxes. The key ends up in `/root/.ssh/authorized_keys`, which on Proxmox is a link
into the cluster folder – so it then applies on **all nodes of the cluster**.

**Using an existing user** (on the Pi e.g. `admin`): enter that name instead of `nodvard` when generating. Running
`sudo -n true && echo geht-schon` on the server shows whether it may already use sudo without a password (on
Raspberry Pi OS this is often the case for the first user).

## What the file watcher reports after the setup

Do not be surprised: if you copy the command and run it yourself, the file watcher of Nodvard Shield reports the new
sudo file and the new `authorized_keys` once – that was you. If the rules of the helper scripts have a checksum, it
reports them again when you run the command again after an update: the checksum changes with every new version of a script.
With **"Set up with password"** („Mit Passwort
einrichten“) the command tells Nodvard Deck exactly what it changed: checksum, permissions and owner before and after
for the user and group files and the `authorized_keys` of the Deck user. Nodvard Deck knows its own sudo rules
(`nodvard-<user>`, the rules of the helper scripts) anyway, since it writes them itself. At its next check the watcher
silently accepts only what matches exactly: the confirmed state is the one from before, the current one is the one
from after, and the command reported the matching action (user created, added to a group, key added, old rule
replaced). The history shows this as „Nach der Einrichtung durch Nodvard Deck um HH:MM übernommen“ (accepted after the
setup by Nodvard Deck at HH:MM), already done and without a notification. Everything else it reports as always: other
files, foreign sudo rules (including ones starting with `nodvard` or `lattice`), only changed permissions or owner, a
content different from what was reported, and every later change. Each run counts only once, at the first check after
it. The same applies if you run the command again with the "Root rights" tick box on a server that still
has the old rule `lattice-<user>`: `nodvard-<user>` is added, and the old one goes away if it is unchanged (see above).
