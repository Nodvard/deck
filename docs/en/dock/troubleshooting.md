# Troubleshooting

[Deutsch](../../dock/troubleshooting.md) · [Dock documentation](README.md) · [Dock quick start](../../../deploy/nodvard-dock/README.en.md#getting-started)

## Dock does not start or the page is missing

In the folder containing `compose.yml`, check:

```bash
docker compose ps
docker compose logs --tail=100 nodvard-dock
```

The database migration runs at first startup. The healthcheck allows a 300-second startup period.
If Dock remains unreachable afterward, check the log for errors.
For "port is already allocated", create `.env` next to `compose.yml` with `NODVARD_DOCK_PORT=8090`,
run `docker compose up -d` and open `http://<host-address>:8090`.
For name resolution problems, review the commented `dns` option in the
[Compose file](../../../deploy/nodvard-dock/compose.yml).

## First login or wrong product name

Read the setup code with `docker compose logs nodvard-dock` and enter it in the browser.
There is no default password. The code is for creating the first account; sign in with that account afterward.
If the title or login page still says "Nodvard Deck", change the saved name under **Einstellungen → Darstellung**
(Settings → Appearance). Missing server modules are expected: [Dock contains only Docker](README.md#edition-and-scope).

## Docker host is empty or SSH fails

- Under **Einstellungen → Server & Zugänge** (Settings → Servers & access), check the address, SSH port, access and
  `docker` tag. Use the machine's LAN address even for Dock's own host; `localhost` refers to the Dock container.
- Run **Verbindung prüfen** (check connection). For missing rights, set up SSH access again; membership in the
  `docker` group grants extensive host privileges. Details: [Root rights](../ROOT-RIGHTS.md).
- On the target host, check that Docker is running and `docker compose version` works.
- The `docker-bedienen` tag limits the host to viewing, logs and start/stop/restart.
  Missing stacks, updates and console are intentional in this mode.

## Git fails to fetch or apply is missing

| Observation | Check and next step |
|---|---|
| **Aus Git** (from Git) missing | Adopt the stack or create a new one first; the host needs full Docker management |
| Repository unreachable | Check HTTPS clone URL, branch or tag, Compose path and, for private repositories, the token and matching Git server |
| LAN Git server blocked | Enable **Git-Server im eigenen Netz erlauben** (allow Git servers on the local network) in advanced Docker settings; avoid `localhost` |
| Comparison reports problems | Keep additional files in the Compose folder; submodules, symlinks and Git LFS are not fetched; observe size limits |
| Files match, no **Einspielen** (apply from Git) | Nothing needs writing; use **Anwenden** (apply) to start a stack that has not started yet |
| Only `.env` differs | Existing environment files stay local and are never replaced from Git |
| Proposal waiting | Under **Aktionen** (actions), check the status and required administrator approval |
| New commit, no changed files | The commit may affect other folders; **Holen und ansehen** (fetch and review) shows the stack comparison |

After an unclear result, check **Aktionen** before applying again.
Details: [Stacks from Git](stacks-from-git.md).

## Image update fails

The update plan gives the reason. For registry problems, check credentials and the Docker host's registry login.
An unchanged fixed version tag does not move to a higher version number; change the Compose file for that.
For a stopped service or a Compose file changed in the meantime, check the state and reload the plan first.
Do not start a second update for the same Compose project while a run is active.
An image rollback cannot repair application data already changed. See [Image updates](image-updates.md).

## Recovery and missing data

Have the Dock backup and backup password or recovery key ready; a backup encrypted with a one-time password requires
that password. Restore under **Einstellungen → System → Wiederherstellen**
(Settings → System → Restore) or **Sicherung einspielen** (restore a backup) in a fresh setup assistant.
Then verify login, host connections and the stack list. For missing application data, check volumes or bind mounts on
the Docker host: they are outside the Dock backup. Volume archives from stack updates or Git deployment are under
**Stacks → Sicherungen** (backups) on the relevant host.
`docker compose down -v` deletes Dock's data volume; it is not a repair step.

For bug reports, include the version, affected step and a shortened log without tokens, passwords or keys:
[Dock issues](https://github.com/nodvard/dock/issues). Report vulnerabilities through the
[security policy](../../../deploy/nodvard-dock/SECURITY.md).
