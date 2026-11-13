# Optional usage data in Deck and Dock

Only the installation owner can enable Settings → Usage data („Einstellungen → Nutzungsdaten“).
All three switches default to off, including in the setup wizard. “Continue without sharing” and “Share”
have equal sizes. Without consent there is no usage report or startup ping.

The three stages can be selected independently. Each selected stage adds its own block; unselected blocks are absent.

| Stage | Contents |
|---|---|
| 1: Basis | Program version, `deck` or `dock`, installation type `docker`, `pi` or `sonstiges`, `amd64` or `arm64` architecture, owner's language `de` or `en` |
| 2: Usage | IDs of enabled bundled modules, count class for enabled third-party modules, server and active-user count classes, whether any active user has two-factor authentication, whether automatic backups of Deck itself are enabled |
| 3: Errors | Counts of `ssh.timeout` during SSH connection setup and `backup.ziel_nicht_beschreibbar` for missing write access during a Deck backup, capped at 0 to 99 per type |

Server classes: `0`, `1`, `2-5`, `6-20`, `21+`. Active-user classes: `1`, `2-5`, `6+`.
Third-party modules appear only as `fremd` with `0`, `1`, `2-5` or `6+`, never by name.
Module usage means enabled modules; individual clicks are not tracked. Error counters start only when the error
stage is enabled, reset after each sending attempt and are discarded when that stage is turned off.
Historical error messages are not inspected.

Every report also contains `schema: 1` and a locally generated random UUIDv4, unrelated to hardware.
The ID stays the same until deletion, allowing the receiver to link reports from one installation.
The reports therefore are not completely unlinkable.

“Das wird gesendet” shows the real JSON for your selection before saving. “Auswahl speichern” records consent;
“Jetzt senden” becomes available afterwards. Scheduled reports use the same builder with current counts at sending time.
See the [data format and API contract](../04-API.md#nutzungsdaten).

The JSON contains no names, hostnames, IP addresses, credentials, paths, error messages, timestamps,
time zones or uptime. The receiver technically sees the public IP address when accepting the connection.
Its handling of IP addresses, reports and retention must be explained in its own privacy notice;
Deck cannot guarantee what the receiver does.

## Sending and local history

At most one report attempt is made in 24 hours, including manual sends. This limit survives restarts,
failures and ID rotation. The first automatic attempt is scheduled randomly within the next 24 hours;
later attempts occur at least 24 hours after the previous attempt, plus up to one additional random hour.
A background task checks the deadline every minute. Each request has a ten-second total deadline.
An offline or rejecting receiver is tried again only at the next scheduled deadline. There is no separate
connectivity probe and no immediate retry loop.

The last 30 sending attempts and deletion requests are stored locally in the database with their date,
result and JSON. An interrupted process may leave an entry marked as started; that does not prove delivery.

“Meine Daten löschen” disables all stages and creates a new ID and local deletion secret.
Deck asks the configured receiver to delete the previous ID. If this fails,
the old ID and secret remain locally so you can click the deletion button again. Pending requests are shown
explicitly; there are no automatic deletion retries. Local history remains. A successful response confirms
the receiver accepted the request; actual deletion is the receiver's responsibility.

## Administrator configuration

Sharing stays locked until the privacy notice is published and the operator sets
`NODVARD_DECK_TELEMETRY_PRIVACY_READY=true` and a HTTPS base address in `NODVARD_DECK_TELEMETRY_URL`.
The URL defaults to empty. `https://telemetrie.example.invalid` is only a placeholder.
With Compose, pass the variables into the container's `environment`; putting them in Compose's `.env` alone
does not pass them through automatically.

`NODVARD_DECK_TELEMETRY_INSTALLATION` selects `docker`, `pi` or `sonstiges`, defaulting to `sonstiges`.
Unknown installation values fall back to `sonstiges`. Unknown architectures prevent basis reports from sending.
`NODVARD_TELEMETRIE=aus` or `NODVARD_DECK_TELEMETRIE=aus` blocks opt-in and all sending, including deletion requests.
Existing consent does not override these locks.

The receiver, public statistics and their privacy notice are separate components and are not provided here.
