# Templates for new stacks

[Deutsch](../../dock/templates.md) · [Dock documentation](README.md) · [Stacks from Git](stacks-from-git.md)

## Bundled catalog

The bundled catalog contains exactly these four templates. Ports refer to the unchanged template on the Docker host.

| Template | ID | Host ports | After startup |
|---|---|---|---|
| Uptime Kuma | `uptime-kuma` | `3001` | Open port 3001, choose a database and create your account |
| Vaultwarden | `vaultwarden` | `8081` | Create an account, then set `SIGNUPS_ALLOWED` to `false`; the browser vault and apps require HTTPS |
| Nginx Proxy Manager | `nginx-proxy-manager` | `80`, `443`, `81` | Keep ports 80 and 443 free, open administration on port 81 and create an account |
| Gitea | `gitea` | `3000`, `2222` | Complete setup on port 3000 and set its SSH port to 2222 |

Catalog images use fixed version tags. The exact Compose files and current tags are in the
[template source](../../../extensions/docker/src/nodvard_deck_ext_docker/templates.py), served to the interface through
`/ext/docker/stack-templates`. A fixed tag does not guarantee automatic discovery of higher version numbers.
Follow the first-account instructions immediately so nobody on the reachable network takes over setup.

## Use a template

1. Open **Docker → Stacks → Neuer Stack** (new stack) and select an application under **Vorlage** (template).
2. Review the name, folder, Compose file, ports and setup note. A name you chose yourself stays when switching
   templates; Dock asks before replacing edited text.
3. For port conflicts, change the host port in the Compose file. The initial folder for new stacks is `/opt/stacks`;
   change it per host, including through **Im Heimatordner** (in the home folder).
4. **Prüfen** (check) validates through Docker Compose on the host. **Anlegen** (create) writes `compose.yaml` and registers
   the stack; **Anlegen und starten** (create and start) also starts it. Both changes go through approval.
5. Check the result under **Aktionen** (actions) and complete the application's first setup.

Dock never overwrites an existing stack folder. You can also insert an example, upload your own Compose file or paste
text. Templates install applications; they do not enable server modules.

## Edition and maintenance

Dock and Deck use the same template catalog from the Docker module. Neither the
[catalog](../../../extensions/docker/src/nodvard_deck_ext_docker/templates.py) nor
[new stack implementation](../../../extensions/docker/src/nodvard_deck_ext_docker/new_stack.py) branches on edition.
The [edition differences](README.md#edition-and-scope) concern the Dock build and core tools.
An instantiated template remains a local Compose file; later catalog changes do not replace it automatically.
For your changes and updates, see [Image updates](image-updates.md) and [Stacks from Git](stacks-from-git.md).
