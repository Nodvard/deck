# Stacks from Git

[Deutsch](../../dock/stacks-from-git.md) · [Dock documentation](README.md) · [Quick start](../../../deploy/nodvard-dock/README.en.md#getting-started)

## Requirements and first stack

The host is connected over SSH and tagged `docker`, with Docker Compose installed.
Git sources can only be set on adopted stacks. For an existing stack, open **Docker → Stacks → Übernehmen** (adopt),
review the check and choose **Jetzt übernehmen** (adopt now); this does not change containers.

For the first attempt, use a repository with a single Compose file that needs no additional files:

1. Open **Neuer Stack** (new stack), choose an unused name and paste the Compose file from the repository.
2. Check the target folder, preferably in a regular user's home directory. Choose **Prüfen** (check) and **Anlegen**
   (create), then confirm the approval. This registers the stack without starting it yet.
3. Open **Aus Git** (from Git) on the stack and enter the source as described below.
4. If all files already match, there is nothing to apply from Git. Start the stack using **Anwenden** (apply) and approval.
   Otherwise review the comparison and approve **Einspielen** (apply from Git); this applies Compose.

For more complex stacks, local environment files and required additional files must exist on the host before the Compose
check. Set up such a stack on the host first, then adopt it. Keep secrets out of the repository.

## Enter the source

- **Adresse des Repositorys** (repository URL): the HTTPS clone URL, such as `https://git.example.org/team/apps.git`,
  without a token, username, query or fragment. Git over SSH is not supported.
- **Branch oder Tag** (branch or tag): for example `main` or a release tag.
- **Pfad zur Compose-Datei** (Compose file path): relative to the repository, for example `apps/web/compose.yaml`.
  Dock compares that file's folder with the stack folder; the Compose file maps to the local Compose file.
- **Zugang** (access): **Öffentlich, ohne Anmeldung** (public, no login) for public repositories. For private ones,
  store a token for the matching Git server under **Einstellungen → Erweiterungen → Docker → Erweitert → Git-Zugänge**
  (Settings → Extensions → Docker → Advanced → Git access), then select that access. The token is stored in the vault.

**Eintragen und holen** (save and fetch) stores the source and loads the comparison. Later, use **Speichern und holen**
(save and fetch) or **Holen und ansehen** (fetch and review). Dock fetches the repository itself over HTTPS; the Docker
host does not need Git. Fetching and comparing neither write files to the host nor run programs from the repository.

## Review and apply

The comparison shows the commit, new and changed files, problems and warnings.
Existing `.env` files and environment files known to the stack are never replaced.
If you choose **Volumes vorher sichern** (back up volumes first), Dock shows volumes and free space. Affected services
stop for the backup until Compose applies the stack. Restore archives under **Stacks → Sicherungen** (backups).

**Einspielen** requires confirmation and approval. Without approval rights, the proposal waits under **Aktionen**
(actions) for an administrator. Compose recreates services whose configuration changed; the approval lists additional
restarts for changed files. If other services still read older files, **Jetzt neu starten** (restart now) appears.
Files previously written from Git and since removed are deleted only if they are unchanged on the host.

**Zurück zum Stand davor** (return to the previous state) offers one step back for the most recently written files.
It does not restore application data or volumes; files changed locally since then are preserved.
**Repository vergessen** (forget repository) removes the source and fetched state in Dock, leaving the stack and host files intact.

## New commits and limits

**Auf neuen Stand prüfen** (check for a new state) queries the branch or tag's commit. Advanced Docker settings also
offer an optional scheduled Git check (`git_watch_enabled`, off by default).
It reports new commits; it does not automatically fetch or deploy them. A new commit may leave the stack folder unchanged.

The local stack must consist of exactly one Compose file directly inside its stack folder.
HTTPS only, without submodules, symlinks or Git LFS files in the compared folder.
References to Compose, environment or build files outside that folder can prevent deployment.
Private network addresses require **Git-Server im eigenen Netz erlauben** (allow Git servers on the local network);
loopback addresses remain blocked. Size and time limits bound fetching, including 300 files, 10 MiB of file contents
and 256 KiB for the Compose file. Dock does not replace a CI pipeline or a data backup plan.

Code references: [source and comparison](../../../extensions/docker/src/nodvard_deck_ext_docker/git_source.py),
[fetching and limits](../../../extensions/docker/src/nodvard_deck_ext_docker/git_fetch.py),
[applying and rollback](../../../extensions/docker/src/nodvard_deck_ext_docker/git_apply.py),
[scheduled checks](../../../extensions/docker/src/nodvard_deck_ext_docker/git_watch.py).
