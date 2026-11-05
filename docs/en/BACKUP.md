# Backups in Detail

> The Nodvard Deck interface is currently available in German only; an English interface is planned. UI labels are given in English with the German original in quotes.
>
> Deutsche Fassung: [../16-SICHERUNG.md](../16-SICHERUNG.md)

How to set up backups in the interface and restore them is in
[first setup, section 6](FIRST-SETUP.md#6-back-up-the-dashboard-itself). This page has the details: backups to a NAS or
NFS, opening a backup without Nodvard Deck, what is not in the backup, the last resort and the scripts for installations
from the repository.

## Backups to a NAS or NFS

**Which folder works:** Nodvard Deck only accepts `/app/data/backups` in the data folder or the mounted folder
`/backups` including subfolders (such as `/backups/deck`). This is always the path **inside the container**, not the
one on the host or on the NAS.

**How to:**

1. Mount the NAS folder on the machine Nodvard Deck runs on, for example at `/srv/backup/deck`. For NFS in
   `/etc/fstab`:

   ```
   nas:/volume1/backup/deck  /srv/backup/deck  nfs  defaults,_netdev,x-systemd.before=docker.service  0  0
   ```

   The NFS share has to be mounted before Docker starts. Otherwise Docker mounts the empty folder on the local disk,
   and the backups end up there instead of on the NAS. `_netdev` tells the system it is a network drive,
   `x-systemd.before=docker.service` makes Docker wait for it.
2. In the Compose file under `volumes:`, uncomment the prepared line and enter the path:
   `- /srv/backup/deck:/backups`. Apply it with `docker compose up -d`. In Portainer: edit the stack and
   "Update the stack".
3. **Write access for user 1000:** Inside the container Nodvard Deck runs as user and group 1000. The folder on the
   NAS has to be writable for 1000:
   - `chown 1000:1000` for the folder on the NAS, or
   - map all access to 1000 in the NFS export: `all_squash,anonuid=1000,anongid=1000`.

   With NFS a `chown` on the host usually does nothing, because the NAS does not let root through (`root_squash`).
4. **Test** before you save in the interface (`docker ps` shows the container's name):

   ```bash
   docker exec -u 1000 <container> sh -c 'touch /backups/.test && rm /backups/.test && echo ok'
   ```

   If it prints `ok`, choose `/backups` (or a subfolder) as the folder in the interface.

**Error messages when saving** (shown in German):

| Message | What helps |
|---|---|
| „Der Ordner /backups existiert nicht (ist er eingebunden?).“ (the folder does not exist, is it mounted?) | The line `- …:/backups` is missing or still commented out, or the container was not recreated afterwards (`docker compose up -d`, in Portainer "Update the stack"). |
| „Sicherungen dürfen nur nach /app/data/backups oder in den eingebundenen Ordner /backups geschrieben werden.“ (backups may only go to …) | Enter the path inside the container (`/backups` or `/backups/…`), not the path on the host such as `/srv/backup/…`. |
| „In /backups darf Nodvard Deck nicht schreiben.“ (Nodvard Deck may not write to /backups) | Write access for 1000 is missing (step 3), or the NFS share was not mounted yet when Docker started (step 1). |

## Emergency: opening a backup without Nodvard Deck

A `.ndbak` file is an ordinary [age](https://age-encryption.org) file with two lines in
front of it.

```bash
tail -n +3 nodvard-deck-sicherung-20261001-023000.ndbak > sicherung.age
age -d -i wiederherstellungsschluessel.txt -o sicherung.tar.gz sicherung.age   # or: age -d (one-time password)
tar -xzf sicherung.tar.gz    # db/lattice.db, files/master.key, files/ext/ …
```

To restore: see [first setup, 6.2](FIRST-SETUP.md#62-restore-import-a-backup).

`lattice.db` is the name of the database from the time before the renaming; it stays
([old names](FIRST-SETUP.md#m-old-names-on-older-installations)).

## What is not in the backup

- With the environment variable `NODVARD_DECK_JWT_SECRET`, no login secret is in the backup.
- The history of the measurements (`metrics.db`) is not part of the backup and stays as it is.
- **Git settings** in the extensions' data (for example in "Scripts" („Skripte“)) are not part of the backup either, but
  the history of your scripts is; the extension creates the settings anew itself. If an older backup still contains some,
  they are skipped during the restore, and the summary names their number among the warnings.

## If nothing works at all

If nothing works at all, the old state is under `restore/replaced-…`: stop the container, copy its contents
(`lattice.db`, `master.key`, `ext/` …) back into the data folder, start.

## With the script (installation from the repository only)

The scripts `deploy/backup.sh` and `deploy/restore.sh` belong to the installation from the repository
([first setup, appendix A.2](FIRST-SETUP.md#a2-building-from-the-repository-developers)): they call Compose with the project name of
`deploy/docker-compose.yml` and do not work with the `compose.yml` from [first setup, section 1](FIRST-SETUP.md#1-quick-start).
There you back up through the interface ([first setup, 6.1](FIRST-SETUP.md#61-in-the-interface-recommended)).

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
