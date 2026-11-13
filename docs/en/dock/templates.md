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
4. Fill in recognised variables under **Werte für diesen Stack** (values for this stack). Required fields must be filled before checking or creating.
5. **Prüfen** (check) validates through Docker Compose on the host. **Anlegen** (create) writes `compose.yaml` and the entered
   values to `.env` in the new stack folder and registers
   the stack; **Anlegen und starten** (create and start) also starts it. Both changes go through approval.
6. Check the result under **Aktionen** (actions) and complete the application's first setup.

Dock never overwrites an existing stack folder. You can also insert an example, upload your own Compose file or paste
text. Templates install applications; they do not enable server modules.

## Values for this stack

For variables without defaults, such as `${DB_PASSWORD}` or `$APP_USER`, **Neuer Stack** provides input fields.
`${DB_PASSWORD:?}` and `${DB_PASSWORD?}` mark required fields, including forms with a message after the question mark.
While a required field is empty, **Prüfen**, **Anlegen** and **Anlegen und starten** are disabled.
Fields without a required marker may remain empty. Variables with defaults such as `${TZ:-UTC}` need no field;
`$$NAME` is evaluated later by the container.

Names containing `PASSWORD`, `SECRET`, `TOKEN` or `KEY` are masked regardless of case.
The Compose file keeps its variables. After approval, Dock writes the entered values to a `.env` only in the new
stack folder, readable only by the SSH account. They are never stored in templates or substituted into the Compose
text. Switching templates clears the inputs; removing a variable from the Compose file also discards its entered value.

## Custom templates

Besides the bundled catalog, you can save and reuse your own templates. Custom templates are available to everyone who has permission to create stacks on Docker hosts.

### Saving

A custom template can be created from two places:

* **From "Neuer Stack" (new stack):** Under **Docker → Stacks → Neuer Stack**, enter or upload a Compose file. The button **Als Vorlage speichern** (save as template) opens a dialog for name and an optional description.
* **From a stack file:** Under **Docker → Stacks**, open the **Datei** (file) view of an existing stack. Next to the Compose file path, **Als Vorlage speichern** allows saving this file directly as a template.

Only the Compose file (`compose.yaml`) is stored. Associated `.env` files and inputs under **Werte für diesen Stack**
are never saved in templates. If a stack requires credentials or instance-specific values, place variables such as
`${DB_PASSWORD:?}` in the Compose file. Whoever selects the template for a new stack enters their own values in the
fields described above.

### Rejection of hardcoded passwords

Before saving, template validation checks the Compose file for hardcoded secrets as far as they can be recognised by known names and patterns. This lowers the risk of passwords or private keys ending up in shared templates, but it does not replace your own check before sharing:

* **Keys and variables:** Entries with known secret names (such as `password`, `secret`, `token`, or `credentials`) must not contain fixed literal values; other names are not checked by Dock. Variables like `${PASS}` or `$PASS`, references (`file://`, `env://`), and toggle values (`yes`, `true`, `1`) for switches like `MYSQL_RANDOM_ROOT_PASSWORD` are permitted.
* **URLs and commands:** URLs must not contain passwords (`user:pass@host`). Commands and parameters must not pass fixed passwords (for example `mysql -pSecret` or `--requirepass Secret`).
* **Detection of tokens and keys:** Private keys (`BEGIN ... PRIVATE KEY`), API keys, and tokens from GitHub, GitLab, AWS, Slack, Tailscale, or JSON Web Tokens (JWT) are rejected throughout the entire text, including comments.

If the file contains a detected secret, saving is rejected. The error message reports the line number and reason for up to five occurrences (identifying the affected service or key) without repeating the secret itself.

### Replacing and deleting

* **Replacing:** If you select an existing custom template under **Neuer Stack** and edit the Compose text, clicking **Als Vorlage speichern** lets you choose between updating the existing template (**Vorlage „…“ ersetzen**) or saving it as a new template under a different name (**Als neue Vorlage speichern**).
* **Deleting:** You can remove a selected custom template under **Neuer Stack** using the **Vorlage löschen** (delete template) button after confirming. Stacks previously created from that template remain untouched on the hosts.

Storage, limits (up to 100 templates, at most 64 KB per Compose file), and validation are handled by [own_templates.py](../../../extensions/docker/src/nodvard_deck_ext_docker/own_templates.py).

## Edition and maintenance

Dock and Deck use the same template catalog and custom template storage from the Docker module. Neither the
[catalog](../../../extensions/docker/src/nodvard_deck_ext_docker/templates.py), the
[custom template storage](../../../extensions/docker/src/nodvard_deck_ext_docker/own_templates.py) nor
[new stack implementation](../../../extensions/docker/src/nodvard_deck_ext_docker/new_stack.py) branches on edition.
The [edition differences](README.md#edition-and-scope) concern the Dock build and core tools.
An instantiated template remains a local Compose file; later catalog changes do not replace it automatically.
For your changes and updates, see [Image updates](image-updates.md) and [Stacks from Git](stacks-from-git.md).
