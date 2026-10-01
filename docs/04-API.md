# 04 — API: REST + WebSocket

Eine API für Web und Android. Die Web-Oberfläche bekommt **keinen**
Sonderpfad — wenn ein Feature nur im Web geht, ist es ein Fehler in der API, nicht eine
Eigenschaft der App.

Basis: `/api/v1`. Das OpenAPI-Dokument liefert `GET /api/v1/system/openapi.json` an angemeldete
Konten mit dem Recht `system.read` — die Flutter-Modelle werden daraus generiert, nicht von Hand
geschrieben. Ohne Anmeldung gibt es die API-Doku (`/docs`, `/redoc`, `/openapi.json`) nur im
Entwicklungsmodus (`NODVARD_DECK_ENV=dev`) oder mit `NODVARD_DECK_API_DOCS=1`; sonst antworten diese
Adressen mit 404, damit eine Installation nicht jedem Besucher alle Endpunkte samt Feldern zeigt.

---

## 1. Konventionen

| Thema | Regel |
|---|---|
| Auth | `Authorization: Bearer <access_token>` — identisch für Web und App |
| Fehler | RFC 7807 `application/problem+json`: `{type, title, status, detail, instance, errors[]}` |
| Paginierung | Cursor: `?limit=50&cursor=…` → `{items, next_cursor}`. Kein Offset. |
| Filter | explizite Query-Parameter, keine generische Filtersprache |
| Zeit | ISO-8601 mit `Z`. Immer UTC. Lokalzeit ist Client-Sache. |
| IDs | UUIDv7-artige Strings |
| Idempotenz | `Idempotency-Key`-Header auf allen Ausführungs-Endpunkten |
| Korrelation | `X-Request-Id` rein, in Antwort und Audit-Log wieder raus |
| Teilantworten | `?fields=` wird **nicht** unterstützt — Mobilfunk-Sparsamkeit läuft über schmale, zweckgebundene Endpunkte |

---

## 2. Ohne Authentifizierung

```
GET  /api/v1/health              → {status, version, uptime_s}
                                   (Notseite statt Anwendung: 503 {"status":"rescue"} – nie "ok", siehe „Kopie vor jeder Migration“)
GET  /api/v1/branding            → Name, Logo, Farben (Login-Seite braucht es)
POST /api/v1/auth/login          → {username, password}
                                   → 200 {access_token, expires_in, refresh_token?, user}
                                   → 202 {mfa_required: true, mfa_token}
POST /api/v1/auth/mfa            → {mfa_token, code} → wie 200 oben
                                   `code` = 6-stelliger Authenticator-Code ODER ein Wiederherstellungs-Code
                                   (`ABCDE-FGHJK`, Schreibweise egal); Letzterer wird dabei verbraucht.
                                   Falsche Codes zählen gegen das 5er-Limit je `mfa_token`.
GET  /api/v1/auth/bootstrap      → {needed}: gibt es noch keinen Nutzer? Solange keiner existiert, steht
                                   zusätzlich `restore: {ok: false, message, at}` da, wenn eine Wiederherstellung
                                   aus dem Assistenten NICHT geklappt hat (der Assistent erklärt dann warum).
POST /api/v1/auth/bootstrap      → {username, password, setup_code} → 201 {id, username, is_owner}
                                   Erstinbetriebnahme: legt den Owner an. Der `setup_code` steht im
                                   Container-Protokoll (oder `NODVARD_DECK_SETUP_CODE`). Fehlt/falsch: 403;
                                   zu viele Fehlversuche je IP: 429; es gibt schon einen Nutzer: 409.
                                   Nach Erfolg wird der Code gelöscht.
PUT/POST/DELETE /api/v1/auth/bootstrap/restore/…, POST /api/v1/auth/bootstrap/restart
                                 → Sicherung einspielen im Assistenten, siehe „Wiederherstellen“ unter §3
                                   „System und Sicherungen“. NUR solange es kein Konto gibt (sonst 409) und
                                   NUR mit dem Einrichtungscode im Kopf `X-Setup-Code` – bei JEDEM Aufruf,
                                   geprüft **vor** dem Lesen des Bodys, gedrosselt wie `POST /auth/bootstrap`
                                   (403 falsch/fehlt, 429 zu oft; gleicher Zähler je IP).
POST /api/v1/auth/refresh        → neuer Access-Token (+ rotierter Refresh-Token; in den 30 s nach
                                   einer Rotation liefert derselbe alte Token nur einen Access-Token,
                                   ohne Cookie und ohne `refresh_token` im Body)
POST /api/v1/auth/logout         → Refresh-Token widerrufen (bei Web: die Tokens beider Cookies) und beide Cookies löschen
GET  /api/v1/capabilities        → was dieser Server kann
```

Web erhält den Refresh-Token als `HttpOnly; Secure; SameSite=Strict`-Cookie und **nicht**
im Body. Android erhält ihn im Body und legt ihn in `flutter_secure_storage`. Der
Access-Token-Pfad ist für beide identisch — das ist der Kern der „eine API"-Zusage.

**Name des Refresh-Cookies (Übergangszeit der Umbenennung).** Das Cookie heißt `nodvard_deck_refresh`
(früher `lattice_refresh`), Pfad `/api/v1/auth`. Solange ein Rollback auf das alte Image möglich sein
soll, gilt für Web-Clients (der Browser muss dafür nichts tun, das Cookie ist `HttpOnly` und wird nur
vom Server gelesen):

- **Setzen** (Login, 2FA-Abschluss, Refresh mit neuem Token): beide Cookies, mit gleichem Wert und
  gleichen Attributen (`HttpOnly`, `SameSite=Strict`, `Path`, `Max-Age`, `Secure` wie bisher). In der
  Gnadenfrist (siehe oben) gibt es keinen neuen Token: dann kommt **kein** `Set-Cookie`, und es wird
  auch nichts gelöscht.
- **Lesen** (`POST /auth/refresh`): erst `nodvard_deck_refresh`, dann `lattice_refresh`; leere und
  doppelte Werte entfallen, der erste Wert, der gilt, gewinnt. Ein Fehlversuch ändert nichts in der
  Datenbank und widerruft keine Sitzungskette (eine Erkennung von Wiederverwendung mit Widerruf der
  Kette gibt es nicht): ein veralteter Wert in einem der beiden Cookies, etwa nach einem Rollback,
  meldet also niemanden ab. Ein `refresh_token` im Body (Android/CLI) gilt weiter allein; die Cookies
  werden dann nicht gelesen.
- **Abmelden** (`POST /auth/logout`): die Tokens aller Cookies werden widerrufen (gleiche Werte nur
  einmal), beide Cookies gelöscht, mit denselben Attributen wie beim Setzen. Ein `refresh_token` im
  Body gilt auch hier allein.

`lattice_refresh` fällt in einem späteren Aufräum-Schritt weg, frühestens 30 Tage (Laufzeit des
Refresh-Tokens) nach dem Deploy mit beiden Cookies und wenn kein Rollback auf ein älteres Image mehr
nötig ist. Für Android/CLI (Token im Body) ändert sich nichts.

`capabilities` listet aktivierte Extensions, verfügbare View-Typen, API-Version und
Feature-Flags. Die Android-App fragt das beim Verbinden ab und blendet aus, was der
Server nicht hat — dadurch funktioniert **eine** APK gegen unterschiedlich bestückte
Installationen: ein generischer Client, eigene Server-URL pro Nutzer.

---

## 3. Kern-Ressourcen

```
GET    /me                          PATCH /me            POST /me/password
                                     → GET /me enthält `totp_enabled`, `recovery_codes_remaining` und
                                       `timezone` (Zeitzone des Dashboards, für jeden Angemeldeten)
GET    /me/sessions                 DELETE /me/sessions/{id}
GET    /me/tokens                   POST /me/tokens      DELETE /me/tokens/{id}
POST   /me/totp/setup               POST /me/totp/confirm   DELETE /me/totp
                                     → confirm: 200 {recovery_codes: [10 Codes]} (einmalig sichtbar,
                                       gespeichert werden nur Argon2-Hashes); nur für eine noch nicht
                                       bestätigte Einrichtung (sonst 409 „Zwei-Faktor ist schon aktiv.“);
                                       falsche Codes: 400, gedrosselt (s. u.)
                                     → DELETE /me/totp: Body {current_password}; 204, löscht auch die Codes
                                       und meldet alle ANDEREN Anmeldungen ab (die aktuelle bleibt)
POST   /me/recovery-codes           → {current_password} → 200 {recovery_codes}; ersetzt alle alten Codes;
                                       409 ohne aktive 2FA
                                     → Sicherheitsabfragen (POST /me/password, DELETE /me/totp, POST /me/recovery-codes,
                                       POST /users/{id}/reset-2fa mit dem Passwort des Admins): falsches
                                       `current_password` = 400 „Das aktuelle Passwort stimmt nicht.“ + Audit
                                       `auth.password_check_failed`. Dazu zählt auch ein falscher Code bei
                                       POST /me/totp/confirm (Audit `auth.totp_confirm_failed`). Gedrosselt je
                                       Nutzer über alle zusammen: 10 Fehlversuche / 5 Min, dann 429 mit
                                       `Retry-After` (auch mit richtigem Passwort); der Beginn der Sperre steht als
                                       `auth.password_check_locked` im Protokoll.
                                     → Abmelden (Passwortwechsel, 2FA abschalten/zurücksetzen, Notfall-Befehl) widerruft
                                       die Refresh-Tokens sofort; bereits ausgestellte Access-Tokens laufen aber
                                       noch bis zu 15 Minuten weiter (stateless JWT, `access_token_ttl_seconds`).
POST   /me/devices                  → Push-Registrierung der App

GET    /app/changelog               → {current, build, unreleased[], versions[]}: Änderungsprotokoll
                                       (jeder angemeldete Nutzer; Dateien unter backend/src/nodvard_deck/changelog/)

GET    /users        POST /users     GET/PATCH/DELETE /users/{id}
                                     → PATCH: `password` setzt nur das Passwort ANDERER Nutzer; das eigene → 409
                                       (nur über /me/password mit Altpasswort), das des Owners → 403 (nur er selbst)
POST   /users/{id}/reset-2fa        → `users.write`, Body {current_password} = Passwort des handelnden Admins;
                                       schaltet 2FA eines ANDEREN Nutzers ab und meldet ihn
                                       überall ab (204). Owner: 403 (nur er selbst); eigenes Konto: 409
                                       (→ /me/totp); ohne 2FA: 409. GET /users enthält `totp_enabled`.
GET    /roles        POST /roles     GET/PATCH/DELETE /roles/{id}
GET    /permissions                  → Katalog inkl. der von Extensions beigesteuerten

GET    /hosts?tag=&group=&status=   POST /hosts
GET/PATCH/DELETE /hosts/{id}         → Server sind Kern-Daten (hosts.read / hosts.write)
GET    /hosts/{id}/status
GET    /hosts/{id}/actions           → welche Aktionen Extensions hier anbieten
POST   /hosts/{id}/actions/{action}  → geht durch das Gate, liefert ggf. 202 „proposed"
GET    /hosts/{id}/credentials       POST /hosts/{id}/credentials   DELETE /hosts/{id}/credentials/{cid}
POST   /hosts/{id}/credentials/generate-key           → SSH-Schlüssel erzeugen (privater Teil nur im Tresor)
GET    /hosts/{id}/credentials/{cid}/setup            → Einrichtungsbefehl für den Server
POST   /hosts/{id}/credentials/{cid}/make-default     → Standard-Zugang umstellen
GET    /hosts/{id}/requirements      → was Erweiterungen auf dem Server brauchen
POST   /hosts/{id}/check             → Verbindung prüfen (erreichbar, Server-Schlüssel, Anmeldung, Root, Erweiterungen, OS)
GET    /hosts/{id}/known-hosts       POST /hosts/{id}/known-hosts (Schlüssel bestätigen)
DELETE /hosts/{id}/known-hosts/{key_type}
GET    /host-groups   POST /host-groups   PATCH/DELETE /host-groups/{id}
POST/DELETE /host-groups/{id}/members/{host_id}

GET    /secrets                      → NUR Metadaten. Nie Werte.
POST   /secrets      PUT /secrets/{id}/value      DELETE /secrets/{id}
POST   /secrets/{id}/test            → Verwendbarkeit prüfen, ohne den Wert zu zeigen

GET    /audit?actor=&action=&outcome=&target=&from=&to=&correlation_id=
GET    /audit/{id}
GET    /audit/export                 → NDJSON-Stream

GET    /settings     PUT /settings/{key}
GET    /branding     PUT /branding   POST /branding/logo

GET    /extensions                   GET /extensions/{id}
POST   /extensions/{id}/enable       POST /extensions/{id}/disable
PUT    /extensions/{id}/settings     PUT /extensions/{id}/permissions
POST   /extensions/{id}/reload       DELETE /extensions/{id}
GET    /extensions/{id}/frontend/index.js     → ESM-Bundle
GET    /extensions/{id}/settings     → {schema, values, secrets: [{label, title, description, item, is_set, optional}]}
PUT    /extensions/{id}/secrets      → Geheimnis eines `x-secrets`-Labels setzen/ersetzen (nie lesen)
DELETE /extensions/{id}/secrets?label=…  → Geheimnis entfernen (idempotent, 204)
POST   /extensions/{id}/test         → Verbindung prüfen, Body optional {"mode": "connection"|"message"}

GET    /connectors                   POST /connectors
GET/PATCH/DELETE /connectors/{id}    POST /connectors/{id}/test

GET    /jobs         POST /jobs      GET/PATCH/DELETE /jobs/{id}
POST   /jobs/{id}/run                → sofort ausführen
GET    /jobs/{id}/runs               GET /runs/{id}      GET /runs/{id}/output
POST   /runs/{id}/cancel

GET    /notifications?unread=        POST /notifications/read
GET    /notifications/{id}
```

**Server, Zugänge und Gruppen** (`hosts.read` zum Lesen, `hosts.write` zum Ändern; jede Änderung steht im
Protokoll):

- `POST /hosts` und `PATCH /hosts/{id}` prüfen die Eingaben. Der Kurzname (`name`) wird getrimmt und klein
  geschrieben und muss `[a-z0-9][a-z0-9_-]{0,63}` entsprechen; die Adresse ist eine IP-Adresse (nicht `0.0.0.0`/`::`,
  keine Multicast-Adresse) oder ein Rechnername nach RFC 1123 – ohne Schema, `user@`, Pfad oder Port (ein einzelner Punkt am Ende wird entfernt); `tags` folgen
  den Markierungsregeln (höchstens 20). Ungültig → `422` mit deutscher Meldung. Der Kurzname lässt sich nicht ändern.
- `HostOut` enthält außerdem `credential` (der Standard-Zugang als `{id, kind, username, port}` oder `null`,
  nie ein Geheimnis) und `managed_tags` (die von einer Erweiterung verwalteten Markierungen, Teilmenge von `tags`).
- `DELETE /hosts/{id}` löscht auch die gespeicherten Zugangsdaten samt ihren Geheimnissen im Tresor und schließt offene
  SSH-Verbindungen. Läuft gerade eine Aktion auf dem Server (`executing`) → `409`.
- `POST /hosts/{id}/credentials`: `username` `[A-Za-z0-9_][A-Za-z0-9_.@\ -]{0,63}` (auch Windows-Namen wie `Max Mustermann`, `user@domain`, `DOMAIN\user`; keine Steuerzeichen), `port` 1–65535; bei `kind = ssh_key`
  muss `secret_value` ein privater Schlüssel ohne Passphrase sein (sonst `422`: „Der Schlüssel ist mit einer Passphrase
  geschützt …“ bzw. „Das ist kein gültiger privater SSH-Schlüssel.“). Schlüssel und Passwörter kommen nie in einer
  Antwort oder im Protokoll zurück – auch `422`-Antworten enthalten die Eingabe nicht (nur `type`, `loc`, `msg`).
  Zugang oder gemerkten Schlüssel zu löschen bzw. die Adresse zu ändern schließt offene SSH-Verbindungen des Servers.
- `GET /hosts/{id}/known-hosts` → `[{key_type, fingerprint, first_seen_at, accepted_by_user_id, accepted_by_label}]`,
  sortiert nach `key_type`. `accepted_by_label` ist der Benutzername (`null` bei automatischem Merken beim ersten
  Kontakt); fremde Namen sehen nur Nutzer mit `users.read` oder Freigaberecht, sonst steht `user/<id>` da.
- `GET /hosts/{id}/requirements` (`hosts.read`) → `[{ext_id, id, label, check_command, ok_text, fail_hint,
  unix_group, needs_root, root_reason, order}]`: was Erweiterungen über `ctx.ui.register_host_requirement()` für diesen
  Server melden (nach `tags` und `os_families` des Servers gefiltert, sortiert nach `order`). `unix_group` ist `null`,
  wenn der Name nicht `^[a-z_][a-z0-9_-]{0,31}$` entspricht.
- `POST /hosts/{id}/credentials/generate-key` `{username = "lattice", port = 22}` (`hosts.write`, `201`) erzeugt einen
  ed25519-Schlüssel nur für diesen Server (Kommentar `lattice@<Kurzname>`). Antwort: `{credential, public_key,
  fingerprint}` – **der private Schlüssel liegt nur im Tresor** und steht nie in einer Antwort (auch keiner `422`),
  im Protokoll oder im Log. `is_default` ist nur dann `true`, wenn der Server noch keinen SSH-Zugang (Schlüssel oder
  Passwort) hat; sonst bleibt der bisherige Standard, bis `make-default` ihn umstellt. `username` und `port` werden wie
  bei `POST .../credentials` geprüft. Höchstens 10 Schlüssel je 5 Minuten und Nutzer, sonst `429` mit `Retry-After`.
  Protokoll `host.key_generated` (`credential_id`, `username`, `port`, `fingerprint`).
- `GET /hosts/{id}/credentials/{cid}/setup?sudo=false&groups=` (`hosts.write`) → `{username, public_key, fingerprint,
  one_liner, script, notes, groups, sudo}`: der Befehl, den man auf dem Server ausführt (als root direkt, sonst über
  `sudo`; `script` ist dasselbe lesbar). Der öffentliche Schlüssel wird serverseitig aus dem Tresor abgeleitet, mit dem
  Kommentar `lattice@<Kurzname>` (nie dem im Schlüssel eingetragenen). Der Befehl legt den Benutzer an (falls es ihn
  nicht gibt, ohne Passwort-Anmeldung), trägt den Schlüssel als `restrict,pty …` ein und richtet auf Wunsch `sudo` ohne Passwort (`sudo=true`, geprüft mit `visudo`) und
  Gruppen (`groups=docker` oder `groups=a,b`) ein. Gruppen werden nur eingetragen, wenn Erweiterungen sie für diesen
  Server verlangen (`unix_group`; privilegierte Gruppen – `root`, `sudo`, `wheel`, `admin`, `shadow`, `disk`, `adm`,
  `staff`, `lxd`, `libvirt`, `kvm` – ignoriert der Kern und protokolliert das im Log; `docker` ist bewusst erlaubt und
  bedeutet praktisch root); für `root` gibt es weder Gruppen noch `sudo`. Bei einem Benutzer außer `root` läuft alles,
  was `~/.ssh` und `authorized_keys` anfasst, **als dieser Benutzer** (`runuser`, sonst `su`), nie als root mit `chown`;
  ist `~/.ssh` oder `authorized_keys` ein Link, bricht der Befehl mit `FEHLER` ab, statt ihm zu folgen. Bei `root`
  (z. B. Proxmox, wo `authorized_keys` ein Link ins Cluster-Dateisystem ist) bleibt der Link unangetastet. Lehnt
  `visudo` die sudo-Regel ab oder wird sie zurückgenommen, endet der Befehl mit Fehler statt „Fertig“. Jeder Wert im Befehl wird streng
  geprüft bzw. mit `shlex.quote` gequotet. `422` für Passwort-Zugänge, nicht-Linux-Server, einen Benutzernamen, der kein
  üblicher Linux-Name ist (`^[a-z_][a-z0-9_-]{0,31}$`), und einen nicht lesbaren Schlüssel. Ändert nichts (kein
  Protokolleintrag).
- `POST /hosts/{id}/credentials/{cid}/make-default` `{delete_previous = false}` (`hosts.write`; Body darf fehlen) macht
  den Zugang zum Standard. Mit `delete_previous` werden der bisherige Standard und sein Geheimnis in derselben
  Transaktion gelöscht und seine offenen SSH-Verbindungen sofort geschlossen; ohne `delete_previous` werden die
  Verbindungen nur ausgemustert (laufende Terminals laufen weiter, neue nehmen den neuen Standard). **`delete_previous`
  geht nur nach einer frischen, erfolgreichen Prüfung des NEUEN Zugangs:** `POST /hosts/{id}/check` mit dessen
  `credential_id` muss in den letzten 10 Minuten bei „Anmeldung“ `ok` ergeben haben (und Adresse und Port sind
  seitdem gleich), sonst `409` („Bitte zuerst „Verbindung prüfen“ …“), und es wird nichts gelöscht oder umgestellt.
  Gilt nur, wenn wirklich ein alter Zugang gelöscht würde. Der Zustand liegt im Speicher (nach einem Neustart einfach
  neu prüfen). Der alte öffentliche Schlüssel bleibt in `authorized_keys` auf dem Server
  stehen – Nodvard Deck entfernt ihn dort nicht (Antwortfeld `notice`, sonst `null`). Ist der Zugang schon Standard,
  passiert nichts. Protokoll `host.credential_made_default` (`credential_id`, `previous_credential_id`, `deleted_previous`) und
  bei `delete_previous` zusätzlich `host.credential_deleted`.
- `POST /hosts/{id}/check` `{credential_id?}` (`hosts.write`, Body darf fehlen) prüft Schritt für Schritt, ob sich
  Nodvard Deck mit dem Server verbinden kann, mit dem Standard-Zugang oder dem angegebenen (`404` bei einem Zugang eines
  anderen Servers, `422` bei `api_token`). Antwort `{ok, checked_at, items, host_key, os, credential_id}`; `ok` heißt:
  kein Punkt ist `fail` oder `confirm`. Punkte `{id, label, status, detail, hint}` mit `status` `ok` | `warn` | `fail` |
  `skipped` | `confirm`, in dieser Reihenfolge (nach einem Fehler in den ersten drei hört die Prüfung auf): `reachable`
  (TCP + SSH-Banner), `host_key`, `login`, `root` (`sudo -n true`; nur `warn`, weil „ohne root“ erlaubt ist), je Meldung einer
  Erweiterung mit `check_command` `req:<ext_id>:<id>` (Ausgang 0 → `ok`, 127 → `skipped`, sonst `warn` mit dem
  `fail_hint`, `{user}` ersetzt), `os`. Windows-Server: `root`, `os` und `req:*` werden übersprungen. `host_key`:
  `{status: known|new|changed, key_type, fingerprint, expected}`.
  **Ohne bestätigten Server-Schlüssel gehen keine Anmeldedaten an den Server:** ist der Schlüssel neu, meldet die
  Prüfung `host_key` als `confirm` (mit Fingerabdruck) und hört auf – schon der Schlüsseltausch bricht ab, bevor
  Benutzername, Passwort oder Schlüssel gesendet werden, und es wird nichts gemerkt. Ohne Zugang wird der Schlüssel nur
  gelesen (Port 22). Ein **geänderter** Schlüssel ist immer `fail` (auch wenn der Server nur einen anderen Schlüsseltyp zeigt als den
  gemerkten; bei einem Server mit mehreren Schlüsseln wird der gemerkte Typ bevorzugt) und wird nie zum Bestätigen angeboten (erst
  `DELETE .../known-hosts/{key_type}`, dann neu prüfen). Geprüft wird nur die gespeicherte Adresse und der Port des
  Zugangs. Die Prüfung nutzt eine eigene Verbindung (nicht aus dem Pool); nach gelungener Anmeldung werden die gepoolten
  Verbindungen des Servers ausgemustert (neue Gruppenrechte gelten, laufende Terminals nicht abgeschnitten) und
  `status`/`last_seen_at` des Servers aktualisiert (`down`: nicht erreichbar, `unknown`: Schlüsselproblem). Texte sind
  feste deutsche Sätze – nie Passwort, Schlüssel, Fehlerausgaben des Servers oder Ausnahme-Texte. Zeitgrenzen: 35 s
  insgesamt, 8 s je Befehl; höchstens zwei Prüfungen gleichzeitig. `429` (mit `Retry-After`, wo es eine Wartezeit gibt)
  bei mehr als 12 Prüfungen je 5 Minuten und Nutzer, bei einer schon laufenden Prüfung desselben Servers und bei
  ausgelastetem Prüfen. Protokoll `host.connection_checked` (`outcome` `success`/`failure`, `detail`: `credential_id`
  und `items` als `{id: status}`, sonst nichts).
- `POST /hosts/{id}/known-hosts` `{key_type, fingerprint}` (`hosts.write`, `201`) merkt einen Server-Schlüssel
  (Antwort wie ein Eintrag aus `GET`, `accepted_by_user_id` ist der Bestätigende). Nur der Schlüssel, den die **letzte
  Prüfung dieses Servers** (höchstens 15 Minuten her, Adresse und Port – der des Standard-Zugangs, ohne Zugang 22 –
  seitdem gleich) gesehen hat, und nur einmal; alles andere → `409` („Bitte zuerst „Verbindung prüfen“ – der
  Fingerabdruck muss frisch vom Server kommen.“). Ist für den Server schon **irgendein** Schlüssel gemerkt (auch eines
  anderen Typs) → `409` („… Erst den alten vergessen.“): ein weiterer Schlüsseltyp wird nie per Bestätigen
  hinzugefügt. Protokoll `host.known_key_pinned`
  (`key_type`, `fingerprint`).
- `PATCH /host-groups/{id}` (`name`, `description`; Name vergeben → `409`, unbekannt → `404`) und
  `DELETE /host-groups/{id}` (löscht nur die Gruppe und ihre Zuordnungen, nie Server). `POST
  /host-groups/{id}/members/{host_id}` ist idempotent und liefert `404` für eine unbekannte Gruppe oder einen
  unbekannten Server.
- Protokoll-Einträge (`target_type` `host`, bei Gruppen `host_group`): `host.created`, `host.updated` (`changed` mit
  `from`/`to` je Feld), `host.deleted`, `host.credential_added`, `host.credential_deleted`, `host.key_generated`,
  `host.credential_made_default`, `host.known_key_pinned`, `host.known_key_forgotten`, `host.connection_checked`,
  `host.group_changed` (`change`: `created`, `renamed`, `description_changed`, `member_added`, `member_removed`,
  `deleted`).

**Einstellungen** (`GET /settings`, `PUT /settings/{key}` mit `{"value": ...}`; beides `settings.write`). Verwaltete
Schlüssel, Wert ungültig → `422`:

| Schlüssel | Wert | Vorgabe |
|---|---|---|
| `autonomy.mode` | `propose` oder `full` | `propose` |
| `autonomy.max_risk` | `low`, `medium`, `high`, `critical` | `low` |
| `security.deny_patterns` | Liste von Regex-Strings | `[]` |
| `maintenance.windows` | Liste aus `{cron, duration_minutes, host_ids}` | `[]` |
| `system.timezone` | IANA-Name aus der Zonendatenbank (`Europe/Berlin`) | `NODVARD_DECK_TIMEZONE`, sonst `TZ`, sonst `Europe/Berlin` |
| `audit.retention_days` | ganze Zahl 7 bis 3650 | `NODVARD_DECK_AUDIT_RETENTION_DAYS` (90) |
| `jobs.run_retention_days` | ganze Zahl 1 bis 3650 | `30` |
| `hosts.reachability.enabled` | `true` oder `false` (nur echte Wahrheitswerte) | `true` |
| `hosts.reachability.interval_minutes` | ganze Zahl 1 bis 60 | `2` |
| `system.update_check.enabled` | `true` oder `false` | `true` |
| `system.update_check.channel` | `stable` (nur fertige Versionen) oder `beta` (auch Vorabversionen) | `stable` |

- `system.timezone` ist die Zeitzone aller Zeitpläne (`Job.timezone`, auch die der Erweiterungen) und der
  Wartungsfenster. Beim Setzen bekommen alle Jobs mit der bisherigen Standardzone die neue (eine ausdrücklich andere
  bleibt) und werden neu geplant, `next_run_at` zieht sofort mit. Ein Job, der neu angemeldet wird (Erweiterung neu
  geladen), behält seine Zone. Die Uhrzeit bleibt, der Zeitpunkt wandert; rund um die Zeitumstellung fällt ein Lauf
  einmal aus oder läuft doppelt (APScheduler entscheidet). Angezeigte Zeitstempel formatiert der Server nie, die
  Oberfläche zeigt sie in der Zeit des Geräts.
- `audit.retention_days`: Der nächtliche Lauf `audit-retention-purge` löscht Protokolleinträge, die älter sind.
  Die Einstellung gewinnt, die Umgebungsvariable ist der Rückfall (auch bei einem ungültigen gespeicherten Wert).
- `jobs.run_retention_days`: Der nächtliche Kern-Job `job-runs-retention` (03:20 Uhr) löscht beendete Job-Läufe
  (`job_runs`, alles außer `running`), die vor mehr als so vielen Tagen begannen, samt ihrer Protokolldatei
  (`output_ref`). Ausnahmen: **laufende Läufe** bleiben immer, und **je Job bleiben die letzten 20 beendeten Läufe**
  stehen (ein monatlicher Job behält seinen Verlauf); Läufe ohne Job gehen nur nach Alter. Dateien werden nur
  innerhalb des `runs`-Ordners gelöscht (aufgelöster Pfad, Symlinks werden nicht verfolgt), eine fehlende Datei ist
  kein Fehler. Dazu gehen Dateien im `runs`-Ordner weg, zu denen keine Zeile gehört und die älter als die
  Aufbewahrung sind. Gelöscht wird in Häppchen zu 5000 Zeilen mit Commit dazwischen; kein `VACUUM` (siehe
  docs/03-DATA-MODEL.md §7). Das eigene, schärfere Aufräumen von `host-reachability` (24 Stunden) bleibt bestehen.
  Der Lauf meldet `{retention_days, runs_deleted, files_deleted, orphans_deleted, chunks}`. Ein gespeicherter Wert
  außerhalb von 1 bis 3650 zählt als nicht gesetzt (Vorgabe 30).
- `hosts.reachability.*`: Schalter und Abstand des Kern-Jobs `host-reachability` (Erreichbarkeitsprüfung, siehe
  docs/11-ERST-EINRICHTUNG.md §4.1). Jede Änderung plant den Job sofort neu (`Job.schedule` = `*/N * * * *` ab
  voller Stunde, 60 = stündlich; aus = der Job ist abgemeldet, die Zustände der Server bleiben stehen). Der Job gehört
  der Einstellung: `PATCH`/`DELETE /jobs/{id}` auf ihn antworten mit `409`. Geprüft werden nur Server, deren Zustand
  kein Modul pflegt (`provider_ext_id` leer), nie Beispiel-Server. Ein Wechsel erzeugt das Ereignis
  `host.status_changed` (`{host_id, status, previous}`, WS-Kanal `events`, sichtbar mit `hosts.read`) und, bei
  Ausfall und Rückkehr, eine Meldung mit `payload.path = /hosts/{id}`. Der interne Merker
  `hosts.reachability.state` (welche Ausfälle schon gemeldet sind) steht nicht in `GET /settings`.
- `system.update_check.*`: Schalter des täglichen Kern-Jobs `system-update-check` („Nach Updates suchen“, 04:41 Uhr in
  der eingestellten Zone) und welche Versionen zählen. Der Schalter plant den Job sofort neu; `PATCH`/`DELETE /jobs/{id}`
  und `POST /jobs/{id}/run` auf ihn → `409` (Knopf „Jetzt suchen“ = `POST /system/updates/check`, mit Minuten-Drossel; ein Weg
  über die Jobs würde sie umgehen). Datenschutz: ghcr.io gehört zu GitHub, das bei jeder Prüfung die IP-Adresse und den Zeitpunkt
  sieht, sonst nichts (der User-Agent `nodvard-deck` nennt keine Version); die tägliche Prüfung lässt sich abschalten. Siehe
  „System und Sicherungen“.
- Alle Änderungen dieser Tabelle ab `system.timezone` stehen mit altem und neuem Wert im Protokoll
  (`system.settings.changed`, `target_id` = Schlüssel).

### System und Sicherungen

Alle Pfade unter `/system`. **Ansehen** braucht `system.read` (Rolle `admin` hat es über `*`). Alles, was
ändert oder Daten herausgibt, darf **nur der Owner** (`require_owner`: die Rolle allein reicht nicht, auch `*`
nicht, sonst `403`). Mit **[PW]** markierte Aufrufe verlangen zusätzlich `current_password` im Body: fehlt es →
`403`, falsch → `400`, zu oft falsch → `429` (dieselbe Drosselung wie bei `/me/password`). Passwörter stehen nie
in der Adresse und nie im Protokoll.

| Methode, Pfad | Recht | Zweck |
|---|---|---|
| `GET /system/info` | `system.read` | `version`, `build` (genaue Version des Release-Images bzw. `NODVARD_DECK_BUILD`, sonst `null`), `image` (Herkunft ohne Tag, aus der Datei `/app/image-info.json` im Image bzw. `NODVARD_DECK_IMAGE`; `null` bei einem selbst gebauten Image), `timezone`, `data_dir`, `data_free_bytes`, `database` (`sqlite`/`postgresql`), `updater_available` (noch `false`), `pre_update_copies[]` (die neuesten, höchstens drei **Kopien der Datenbank von vor einer Migration**, neueste zuerst: `name`, `created_at`, `from_version`, `to_version`, `size`; ohne Pfade; leer, solange es noch keine gibt) |
| `GET /system/updates` | `system.read` | Stand von „Nach Updates suchen“, **ohne** Anfrage ins Netz (aus `<Datenordner>/update_check.json`): `current` (laufende Version: beim offiziellen Image dessen genaue Version aus `/app/image-info.json`, auch eine Vorabversion wie `0.6.0-rc1`; sonst `version`), `latest` (neueste Version im Kanal laut letzter gelungener Prüfung; `null`, wenn noch nie geprüft oder geprüft, aber keine passende Version gefunden – dann ist `checked_at` gesetzt), `latest_digest` (sha256 des Manifests, wenn bekannt), `available` (`latest` neuer als `current`, Semver), `channel` (`stable`/`beta`), `enabled` (tägliche Prüfung an), `checked_at` (letzte gelungene Prüfung), `attempted_at` (letzter Versuch), `source` (`cache`; `offline`, wenn der letzte Versuch scheiterte), `error` (dann „Konnte nicht prüfen (offline?).“; `latest`, `checked_at`, `attempted_at` und `error` gelten nur für den eingestellten Kanal), `official_image` (das Image stammt laut `image` genau aus `ghcr.io/nodvard/deck`), `image`, `official_image_name`, `helper` (noch immer `false`), `release_notes_url` (`https://github.com/nodvard/deck/blob/v<latest>/CHANGELOG.md`) |
| `POST /system/updates/check` | `system.read` | Jetzt nachsehen; Antwort wie `GET`, mit `source: live`. Scheitert die Abfrage (offline, `401`/`429`/`5xx` der Registry, kaputtes JSON, Zeitlimit), kommt **kein** Fehlercode, sondern der letzte Stand mit `source: offline` und `error`. Höchstens **einmal pro Minute** für alle zusammen, sonst `429` „Gerade erst nachgesehen …“ mit `Retry-After` |
| `GET /system/openapi.json` | `system.read` | Das vollständige OpenAPI-Dokument (alle Endpunkte samt mitgelieferter Erweiterungen); ersetzt das öffentliche `/openapi.json`, das es nur im Entwicklungsmodus oder mit `NODVARD_DECK_API_DOCS=1` gibt |
| `GET /system/backups` | `system.read` | `config`, `key` (`key_id`, `created_at` – nie Schlüssel oder Passwort), `target` (`dir`, `default_dir`, `external_root`, `external_available`, `same_storage_as_data`, `free_bytes`, `error`), `backups[]` (`name`, `size`, `created_at`, `app_version`, `key_id`, `mode`, `status` `ok`/`ungeprueft`/`beschaedigt`, `checked_at`, `check_ok`, `key_current`), `last_run`, `running`, `sqlite`, `limits` |
| `PUT /system/backups/config` | Owner, [PW] nur bei gefährlicher Änderung | `{enabled, schedule, keep, dir, include_runs, current_password?}`; `current_password` ist **optional** und nur nötig, wenn `keep` kleiner wird als bisher, `enabled` von an auf aus geht oder `dir` sich ändert (sonst `403` mit Text, welche Änderung das Passwort braucht; falsch `400`, zu oft `429` wie bei den anderen [PW]-Endpunkten; geprüft **vor** der Eingabeprüfung, ohne dass ein Ordner angelegt wird); `keep` 1–60, `schedule` Cron, `dir` nur `<Datenordner>/backups` oder `/backups` bzw. darunter (absolut, kein `..`, kein Symlink, `realpath` gleich, beschreibbar) → sonst `422`; `enabled` ohne Sicherungspasswort → `409`. Antwort wie `GET /system/backups` |
| `PUT /system/backups/key` | Owner [PW] | `{current_password, password}` (≥ 12 Zeichen, sonst `422`). Antwort **einmalig** `{key_id, recipient, recovery_key}` (`Cache-Control: no-store`) |
| `POST /system/backups/run` | Owner | Jetzt sichern, als Kern-Job `system-backup` im Hintergrund → `202`; läuft schon eine Sicherung oder fehlt der Schlüssel → `409` |
| `POST /system/backups/download` | Owner [PW] | `{current_password, mode: "schluessel"\|"passwort", password?}` → `202` mit Ticket `{ticket, job_id, status, filename, size, error, expires_in, url}`; baut die Datei im Hintergrund (kein Job: dessen Protokoll enthielte die Parameter). `409` bei laufender Sicherung, fehlendem Schlüssel (`schluessel`) oder ohne SQLite-Datei; `507` bei zu wenig Platz |
| `GET /system/backups/download-jobs/{job_id}` | Owner, nur eigener Download | Stand `building`/`ready`/`failed`, Antwort wie beim Ticket (`404` für fremde, unbekannte oder schon abgeholte Downloads). `job_id` ist eine eigene Zufalls-ID und taugt **nicht** zum Herunterladen: die Oberfläche fragt sie jede Sekunde ab, sie steht also in der Adresse (und im Zugriffsprotokoll), das Ticket nicht |
| `GET /system/backups/download/{ticket}/status` | Owner, nur eigenes Ticket | **Veraltet** (`deprecated`), bleibt als Übergang: dasselbe über das Ticket in der Adresse. Neu: `download-jobs/{job_id}` |
| `GET /system/backups/download/{ticket}` | Ticket | Liefert die Datei als normalen Download (Streaming). Gilt **einmal**, 5 Minuten ab Fertigstellung, nur solange der Nutzer noch aktiver Owner ist; sonst `404`, während des Baus `409`. Eine eigens gebaute Datei wird danach gelöscht. Ein Filter am uvicorn-Zugriffsprotokoll (`core/log_filters.py`) ersetzt das Ticket in Adressen unter `…/system/backups/download/` durch `…` |
| `POST /system/backups/{name}/ticket` | Owner [PW] | Ticket für eine vorhandene automatische Sicherung (die Datei bleibt liegen) |
| `POST /system/backups/{name}/verify` | Owner | sha256 der Datei gegen die Prüfsummen-Datei `<name>.json` (ohne Passwort) → `{ok, detail}` |
| `DELETE /system/backups/{name}` | Owner [PW] | Body `{current_password}`; nur `nodvard-deck-sicherung-*.ndbak` mit eigenem Kopf, die `.json` daneben verschwindet mit → `204`; unbekannt → `404` |

- **Datei** `nodvard-deck-sicherung-YYYYMMDD-HHMMSS.ndbak` (Ortszeit der eingestellten Zone): Zeile 1
  `NODVARD-DECK-BACKUP/1`, Zeile 2 JSON-Kopf (`format`, `created_at`, `app_version`, `mode`, bei `schluessel`
  zusätzlich `key_id` und `kdf` = Argon2id-Parameter mit Salz), danach eine normale age-Datei mit einem tar.gz:
  `db/lattice.db` (Online-Kopie, `integrity_check`), `files/master.key`, `files/vault_keyring.json`,
  `files/jwt_secret.key` (nur ohne `NODVARD_DECK_JWT_SECRET`), `files/ext/**`, `files/branding/**`, optional
  `files/runs/**`, zuletzt `manifest.json` (Kopie des Kopfes, Alembic-Köpfe, Erweiterungen, sha256 jeder Datei).
  Nicht enthalten: `*-wal`/`-shm`/`-journal`, `setup_code.txt`, `backups/`, `restore/`, `.boot/`, Temp-Dateien,
  Symlinks und der Verlauf `metrics.db`. Übersprungene Symlinks werden nicht verschwiegen: `last_run.warnings`
  (Liste deutscher Sätze, älteren Einträgen fehlt das Feld) nennt Anzahl und höchstens fünf Pfade
  („2 Verknüpfungen in Erweiterungsdaten nicht gesichert: ext/a/link, …“); die Sicherung gilt trotzdem als gelungen.
- Eine neuere Formatversion wird mit Hinweis abgelehnt. Beim Lesen gelten Deckel: scrypt log2 N ≤ 18 (geschrieben
  wird mit 18; 2^20 wären 1 GiB und werfen einen Pi mit 1 GB aus dem Speicher), Argon2id m ≤ 128 MiB (geschrieben
  mit 64 MiB), t ≤ 10, p ≤ 4. Mit `age`/`rage` selbst verschlüsselte Dateien mit größerem Arbeitsfaktor werden
  abgelehnt.
- Protokoll: `system.backup.created`, `.failed`, `.config_changed`, `.key_set` (`key_id`), `.download_prepared`,
  `.downloaded`, `.verified`, `.deleted`. Eine fehlgeschlagene automatische Sicherung meldet sich zusätzlich als
  Benachrichtigung („Sicherung fehlgeschlagen“, `payload.path` = `/settings/system`).
- **Nach Updates suchen** (`core/updates.py`): einzige Quelle ist ghcr.io. Anonymes Token
  (`GET https://ghcr.io/token?scope=repository:nodvard/deck:pull&service=ghcr.io`, klappt nur bei einem öffentlichen Paket),
  Tags (`GET /v2/nodvard/deck/tags/list?n=100`, weitere Seiten über den `Link`-Kopf, höchstens 10; eine Folgeseite auf einem anderen
  Host oder Pfad gilt als Fehler, der Token geht nur an ghcr.io). Es zählen nur Tags `1.2.3`, im Kanal `beta` auch `1.2.3-rc1`;
  verglichen wird nach Semver mit der laufenden Version (`current`); abweichend davon werden Teile einer Vorabversion mit Zahl am Ende
  natürlich sortiert (`rc1 < rc2 < rc9 < rc10`). Danach optional das Manifest der neuesten Version (OCI-Index),
  dessen sha256 `latest_digest` ist. Je Anfrage höchstens 5 s; Token und Tags zusammen höchstens 15 s, das Manifest bekommt nur die
  restliche Zeit (reicht sie nicht, bleibt `latest_digest` `null`, die Prüfung gilt trotzdem). Jede Antwort wird ungepackt angefordert
  (`Accept-Encoding: identity`, eine gepackte gilt als Fehler) und höchstens 1 MiB gelesen (eine größere `Content-Length` bricht vor dem
  Lesen ab). User-Agent `nodvard-deck`, ohne Version. Es läuft immer nur eine Prüfung zur Zeit. Eine Antwort ohne Tag-Liste gilt als
  Fehler (der letzte Stand bleibt); eine vollständige Antwort ohne passende Version ist ein Ergebnis (`latest: null`). Täglich als
  Kern-Job `system-update-check` (Schalter `system.update_check.enabled`), dazu der Knopf „Jetzt suchen“. Das Repository steht als eine Konstante im Kern
  (`core.updates.REPOSITORY`). Das Release-Image trägt Herkunft und Version als Datei `/app/image-info.json` (Build-Argumente
  `IMAGE` und `VERSION` in `release.yml`/`deploy/Dockerfile`, `nodvard_deck.image_info`; keine Umgebungsvariable, die beim Neuanlegen
  eines Containers mitwandern könnte). Daran erkennt die Oberfläche ein selbst gebautes Image. Vorrang für `image` und `build`
  in `GET /system/info`: nicht leere Variable `NODVARD_DECK_IMAGE`/`NODVARD_DECK_BUILD` (alt `LATTICE_*`), dann die Datei, sonst `null`.
  `current` der Update-Suche (und `from_version`/`to_version` der Kopien vor Updates) nimmt beim offiziellen Image dagegen zuerst die
  Version aus der Datei, dann `build` (jeweils nur, wenn es eine Version ist), sonst `version`: eine veraltete Variable verfälscht den
  Vergleich nicht.
- Den Kern-Job `system-backup` steuert nur diese Karte: `PATCH`/`DELETE /jobs/{id}` auf ihn → `409`
  (sonst könnte ein Admin über `jobs.write` die automatischen Sicherungen still abschalten).

**Wiederherstellen** (Owner, `core/backup/restore.py`, `boot.py`): Hochladen → Prüfen → Vormerken → Neustart; eingespielt
wird beim Start, **vor** der Migration. Der Zwischenstand liegt unter `<Datenordner>/restore/<id>/`; es gibt höchstens einen.

| Endpunkt | Recht | Zweck |
|---|---|---|
| `GET /system/restore/status` | `system.read` | `{pending, staged, result, replaced, limits, busy}`: Vormerkung, Zwischenstand (**nur der Owner**), Ergebnis des letzten Einspielens (ohne IP-Adressen), alter Stand (`replaced`: Name, Größe, `expires_at` = wann er von selbst gelöscht wird), Grenzen |
| `PUT /system/restore/upload` | Owner [PW im Kopf] | Roher Strom (`Content-Type: application/octet-stream`, **kein** multipart; sonst `415`). Kopf `X-Confirm-Password`: Anmeldepasswort **prozentkodiert** (UTF-8, `encodeURIComponent`; Kopfzeilen kennen kein Unicode). Owner und Passwort werden geprüft, **bevor** der Body gelesen wird (`403` fehlt, `400` falsch, `429` zu oft). `Content-Length` über `restore_max_upload_bytes` (4 GiB, `NODVARD_DECK_RESTORE_MAX_UPLOAD_BYTES`) → `413` ohne Lesen, ebenso bei Überschreitung mitten im Strom; zu wenig Platz → `507` vor dem Lesen; läuft schon ein Upload/eine Prüfung → `409`; ist etwas vorgemerkt → `409`; keine Sicherung (Kopf) → `422`. `201` `{id, state: "uploaded", size, header: {mode, created_at, app_version}, expires_in}` |
| `POST /system/restore/{id}/inspect` | Owner | `{password}` **oder** `{recovery_key}` (genau eines, sonst `422`). Entschlüsselt in den Staging-Ordner und prüft alles. `200` `{id, state: "ready", summary}`; `summary`: `created_at`, `app_version`, `instance_id`, `mode`, `owner_name`, `users`, `hosts`, `extensions[]`, `includes`, `warnings[]` (u. a. andere Installation, ältere/neuere Version, fehlende Erweiterungen, kein Konto in der Sicherung). Falsches Geheimnis `400` (Upload bleibt, noch ein Versuch), zu wenig Platz `507` (bleibt), `409` bei laufender Arbeit; jeder andere Fehler (beschädigt, zu neu, Erweiterung fehlt, zu groß `413`, nicht einspielbar) `422` und der Zwischenstand ist gelöscht; unbekannte oder fremde `id` `404`. Entschlüsselt wird immer nur **eine** Sicherung zugleich (teilt die Sperre mit den Sicherungen) |
| `POST /system/restore/{id}/schedule` | Owner [PW] | `{current_password, sign_out_all: true}` → `200` Vormerkung `{id, source, scheduled_at, expires_in, sign_out_all, backup}`; `restore/pending.json`. Vorher nicht geprüft oder schon etwas vorgemerkt → `409`. Gilt eine Stunde |
| `DELETE /system/restore/pending` | Owner | Verwirft Vormerkung **und** jeden Zwischenstand → `204` |
| `DELETE /system/restore/replaced` | Owner [PW] | Body `{current_password}`; löscht den alten Stand (`restore/replaced-…`, enthält alte Konten und Schlüssel) → `204` |
| `POST /system/restart` | Owner [PW] | Body `{current_password}`. Beendet den Prozess nach der Antwort mit dem Rückgabewert **75**; der Container startet ihn neu (Regel `restart: unless-stopped`; ohne Regel bleibt der Dienst aus). → `202` `{restarting, exit_code: 75}` |
| `PUT /auth/bootstrap/restore/upload`, `POST /auth/bootstrap/restore/{id}/inspect`, `POST …/{id}/schedule`, `DELETE …/restore/pending` | Einrichtungscode | Wie oben, aber ohne Konto und ohne Passwort-Kopf: **nur ohne Konto** (sonst `409`), Kopf `X-Setup-Code` bei jedem Aufruf. `schedule` nimmt `{sign_out_all}` |
| `POST /auth/bootstrap/restart` | Einrichtungscode | wie `POST /system/restart`; nur wenn etwas vorgemerkt ist (sonst `409`) |

- **Härtung beim Entpacken:** tar nur als Strom (`tarfile.data_filter` pro Eintrag, jede Datei schreiben wir selbst mit
  `O_EXCL|O_NOFOLLOW`), Namen nur aus einer Erlaubnisliste (`manifest.json`, `db/lattice.db`,
  `files/{master.key,vault_keyring.json,jwt_secret.key}`, `files/ext/**`, `files/branding/**`, `files/runs/**`),
  nur normale Dateien und Ordner (keine Links, Hardlinks, Geräte, FIFOs), jede Datei einmal, das Manifest zuletzt.
  Grenzen am **entpackten** Strom: Einträge (`…_MAX_ENTRIES`, 200 000), Einzeldatei und Gesamtmenge
  (`…_MAX_UNPACKED_BYTES`, 16 GiB, nie mehr als der freie Platz abzüglich 128 MiB) – gegen gzip-Bomben.
  Bei **jedem** Fehler wird der Staging-Ordner gelöscht. Kopf und Manifest müssen zusammenpassen, sha256 jeder Datei.
- **Die SQLite-Datei ist nicht vertrauenswürdig:** nur lesend und unveränderlich geöffnet (`mode=ro&immutable=1`),
  `trusted_schema=OFF`, ein Autorisierer, der nur Lesen zulässt, keine Trigger, Ansichten oder virtuellen Tabellen
  (die legt Nodvard Deck nie an), `integrity_check`, und `alembic_version` darf nur Revisionen enthalten, die dieses Image
  kennt (sonst `422` „aus einer neueren Version“ bzw. „Erweiterung … fehlt“). Die Angaben des Manifests zu den Köpfen müssen
  mit der Datenbank übereinstimmen. Ältere bekannte Stände sind in Ordnung (die Migration folgt beim Start).
- **Einspielen beim Start** (`python -m nodvard_deck.boot`, von `entrypoint.sh` statt `nodvard_deck.migrate`): Zwischenstand erneut prüfen (sha256
  aller Dateien, Datenbank gegen die Revisionen **dieses** Images), den aktuellen Stand nach `restore/replaced-<Zeit>/` **verschieben**
  (`rename`, auch `-wal`/`-shm`/`-journal`), den neuen an seinen Platz, bei `sign_out_all` alle Refresh-Tokens löschen,
  `setup_code.txt` löschen, wenn es Konten gibt. Ein Journal (`restore/journal.json`) macht jeden Fehler **und** jeden Absturz mitten
  drin rückgängig; **endgültig** ist es erst, wenn danach auch die Migration gelungen ist (sonst geht der alte Stand zurück).
  `restore/result.json` hält das Ergebnis fest; `main.lifespan` schreibt danach `system.restore.applied`/`.failed` in die jetzt gültige
  Datenbank. Nur noch ein `replaced-…` bleibt; **nach 30 Tagen** löscht es der Kern-Job `restore-cleanup` (täglich 03:40) und beim Start
  `restore.sweep` von selbst (`replaced.expires_at` nennt den Tag). Aufgegebene Zwischenstände und abgelaufene Vormerkungen werden nach einer Stunde gelöscht.
- **Rechte:** nur der Owner (Admin ohne Owner `403`), wo oben [PW] steht zusätzlich das aktuelle Passwort; ohne Konten nur mit Einrichtungscode.
  Eine Vormerkung aus dem Assistenten wird beim Start verworfen, wenn inzwischen ein Konto existiert.
- Protokoll: `system.restore.uploaded`/`.upload_failed`/`.inspected`/`.inspect_failed`/`.scheduled`/`.schedule_failed`/`.cancelled`/
  `.replaced_deleted`, `system.restart.requested`, nach dem Start `system.restore.applied`/`.failed`. Nie ein Passwort, Schlüssel oder Code.
- Ohne Oberfläche: `python -m nodvard_deck.admin restore-backup <datei.ndbak>` prüft und merkt vor (Passwort am Terminal oder aus der
  Standardeingabe mit `--yes`); `deploy/restore.sh sicherung.ndbak` ruft ihn auf.

#### Kopie vor jeder Migration, Sperre und Notseite (`nodvard_deck.boot`, `nodvard_deck.rescue`)

Zusätzlich zum Einspielen (siehe oben) macht `python -m nodvard_deck.boot` vor der Anwendung Folgendes (`backend/src/nodvard_deck/boot.py`,
Zustand in `<Datenordner>/.boot/`, Kopien in `<Datenordner>/backups/vor-update/`; weder `.boot/` noch `backups/` sind Teil einer Sicherung):

- **Sperre** (`.boot/app.lock`, `flock`): Die laufende Anwendung hält sie, `boot` und `admin restore-backup` prüfen sie. Hält jemand sie, endet
  `boot` mit **75** und ändert nichts (so spielt ein `compose run` ohne `--entrypoint` nie unter der laufenden Anwendung etwas ein);
  `admin restore-backup` räumt dann keinen laufenden Upload der Anwendung weg. Wo `flock` nicht geht, gibt es eine Sperre ohne Wirkung.
- **Kopie vor jeder Migration** (nur SQLite): Datenbank online kopieren (`sqlite3.backup`, danach `integrity_check`) nach
  `backups/vor-update/<Zeit>_<von>_<nach>.db` (+ `.json`), vorher mindestens das **1,2-fache** der Datenbank samt WAL frei, es bleiben **drei**.
  **Ohne Kopie keine Migration** (`no_space`, `no_copy`). Notausgang `NODVARD_DECK_SKIP_PRE_MIGRATE_BACKUP=1` (alter Name `LATTICE_…` gilt weiter).
  Scheitert die Migration, geht die Datenbank auf die Kopie zurück (die Kopie wird dabei umbenannt, nicht kopiert); `state.json` hält
  `last_migration` vorher als `running` fest, ein Absturz mitten drin wird beim nächsten Start genauso behandelt. Den halben Stand verwirft der Rückweg im
  selben Start; nach einem Absturz legt er ihn nach `restore/replaced-…` (außer die Datenbank liegt auf einem anderen Laufwerk und im Datenordner fehlt der Platz).
  Neuere Daten (Downgrade, ausdrücklicher Rückweg) werden nie verworfen: fehlt der Platz, kommt `no_space`.
- **`state.json`:** `app_version`, `db_heads`, `started_ok` (setzt `main.lifespan` nach einem gelungenen Start; `boot` setzt es mit jeder Migration auf
  `false`), `last_migration` (`from_version`, `to_version`, `from_heads`, `to_heads`, `copy`, `state`), `revert` (Journal des Zurücksetzens) und
  `failure` (Art, Grund, bereinigtes Protokoll der letzten 50 Zeilen: keine Pfade, Passwörter, SQL-Parameter).
- **Downgrade-Erkennung:** Kennt das Image Revisionen der Datenbank nicht (sie ist neuer), kommt die Kopie nur automatisch zurück, wenn (1) die letzte
  Migration von Ständen ausging, die dieses Image kennt, und genau den jetzigen Stand erzeugt hat, (2) die Kopie existiert, heil ist und diese Stände
  hat und (3) die neue Version nie erfolgreich gestartet ist (`started_ok=false`). Oder eine ausdrückliche Vormerkung (`.boot/rollback.json`, 24 Stunden gültig,
  von der Notseite) liegt vor: dann auch nach einem guten Start; die neueren Daten bleiben unter `restore/replaced-…` (30 Tage). Sonst: Notseite.
- **Fehler führen zur Notseite**, nie zu einer Neustart-Schleife: `boot` gibt 1 zurück, `deploy/entrypoint.sh` startet `python -m nodvard_deck.rescue`; fällt die aus, wartet
  der Container (`exec sleep`). Das gilt auch für einen unlesbaren Journal-Eintrag oder einen gescheiterten Rückweg einer Wiederherstellung (`rollback_failed`).

**Die Notseite** (`backend/src/nodvard_deck/rescue.py`, **nur Standardbibliothek**, ein Test prüft das über den Syntaxbaum und startet sie ohne installierte Pakete) antwortet
auf dem Port der Anwendung:

| Methode, Pfad | Antwort |
|---|---|
| `GET /api/v1/health` | **`503`** `{"status":"rescue"}` (nie `ok`: Healthcheck, `scripts/deploy_pi.sh` und ein Helfer erkennen so, dass etwas nicht stimmt). Jeder andere Pfad unter `/api/` ebenso `503` mit `{"status":"rescue","detail":…}` |
| `GET <alles andere>` | Die Seite (`503`, Deutsch, ohne fremde Dateien). **Ohne Code** nur Allgemeines |
| `POST /rescue/unlock` | Notfallcode (`.boot/rescue_code.txt`, wie der Einrichtungscode; steht als Banner im Protokoll) → `303` + Cookie `rescue_session` (HttpOnly, SameSite=Strict, 15 Minuten). Je Absender 5 Fehlversuche, insgesamt 25 in 10 Minuten, dann `429` |
| `GET /rescue/status`, `GET /rescue/log` | mit Cookie: Grund und bereinigtes Protokoll; ohne: nur `{"status":"rescue","unlocked":false}` bzw. `401` |
| `POST /rescue/retry` | mit Cookie: Prozessende mit 75, der Container startet neu (Restart-Regel) und `boot` läuft noch einmal |
| `POST /rescue/rollback` | mit Cookie, nur wenn `boot` eine brauchbare Kopie gefunden hat (sonst `409`): „Stand vor dem Update wiederherstellen“ vormerken, dann wie `retry`. Die Kopie kommt aus dem Zustand, nie aus der Anfrage |
| `POST /rescue/lock` | Sitzung beenden |

POST-Anfragen prüfen `Origin` gegen `Host`. Eine Aktion „Kopie herunterladen“ gibt es **bewusst nicht**: Eine unverschlüsselte Datenbank über HTTP herauszugeben ist
unverantwortlich, und die Verschlüsselung (age/`pyrage`) gehört nicht zur Standardbibliothek. Die Kopie liegt im Datenordner.

### Erweiterungen einrichten und testen

Alle drei Endpunkte brauchen `extensions.manage`, wie das Bearbeiten der Einstellungen.

- `GET /extensions` und `GET /extensions/{id}` liefern zusätzlich (nur additiv, alte Felder
  unverändert): `needs_setup` (bool, nur bei eingeschalteten Erweiterungen: Pflichtfelder
  oder Pflicht-Geheimnisse fehlen, oder der letzte Verbindungstest ist fehlgeschlagen),
  `setup_reasons` (Liste deutscher Sätze, leer wenn alles in Ordnung) und `last_test`
  (`{ok, message, at}` oder `null`; wird nach jeder Änderung der Einstellungen oder
  Zugangsdaten gelöscht). Ohne `extensions.manage` fehlt der Text: `last_test` enthält dann nur
  `ok` und `at`, und `setup_reasons` nennt den Fehlschlag ohne Fehlergrund.
- `POST /extensions/{id}/test` ruft `health()` der Erweiterung auf (bei
  Benachrichtigungskanälen zusätzlich `test()`); mit `{"mode": "message"}` sendet es stattdessen
  eine Testnachricht über den Kanal (`422`, wenn die Erweiterung keinen hat). Antwort
  `{ok, message, details?}`: `message` ist ein verständlicher deutscher Satz („Keine Antwort
  von 192.168.1.20:8006 – Adresse und Port prüfen.“, „Zugangsdaten abgelehnt …“, „Zertifikat
  wird nicht vertraut …“, „Adresse nicht gefunden …“), `details` bei mehreren Verbindungen
  `[{name, ok, message}]`. Geheimnisse stehen nie im Text. Ein fehlgeschlagener Test ist
  `200` mit `ok: false`. `409`: Erweiterung ausgeschaltet, `404` unbekannt, `429` nach mehr als
  10 Tests pro Minute und Nutzer (mit `Retry-After`). Jeder Test steht im Protokoll
  (`extension.test`, ohne Text).
- `DELETE /extensions/{id}/secrets?label=…` entfernt ein Geheimnis aus `x-secrets` (`422` für
  fremde Labels); Protokoll `extension.secret_removed`.
- Das alte `POST /ext/proxmox|backups/connections/{name}/token` legt ein Token nur an
  (`409`, wenn schon eines existiert). Zum Setzen **und Ersetzen** dient
  `PUT /extensions/{id}/secrets` mit `{label: "proxmox-token:<name>", value}`; die Oberfläche
  nutzt nur noch diesen Weg.

### Aktionen — der Bestätigungs-Workflow

```
GET    /actions?status=proposed          → die Karten im Incident-Feed
GET    /actions/{id}                     → Stand einer (laufenden) Aktion abfragen
POST   /actions/{id}/approve?wait=0..20  → startet die Ausführung (Idempotency-Key!)
POST   /actions/{id}/reject              → {reason}
POST   /actions/{id}/dismiss
```

Ein `POST` auf eine zustandsverändernde Ressource antwortet:

- `200` — ausgeführt (Autonomie-Modus `full`, Risiko unter Schwelle)
- `202` — **als Vorschlag angelegt**, Body enthält die `action`-Ressource
- `403` — vom Gate abgelehnt, Body nennt die greifende Regel
- `409` — Vorschlag abgelaufen

Dass `202` der Normalfall ist, folgt aus dem Aktions-Gate
([01 §4](01-ARCHITECTURE.md#4-das-aktions-gate)): Vorschlagen und Ausführen sind getrennte
Schritte. Clients müssen `202` behandeln — die Flutter-App genauso wie das Web.

**Ausführung im Hintergrund.** Genehmigte Aktionen laufen nicht mehr
innerhalb der HTTP-Anfrage (ein Update dauert bis zu 45 min). `approve` und
`POST /hosts/{id}/actions/{type}` (im Modus `full`) starten die Ausführung und warten
höchstens `wait` Sekunden (Standard 20) auf das Ergebnis:

- fertig → `200` mit der `action`-Ressource wie bisher (`succeeded`/`failed`)
- noch nicht fertig → `202` mit `status = "executing"`, Header `Location:
  /api/v1/actions/{id}` und `Retry-After: 3`; der Client fragt `GET /actions/{id}` ab,
  bis ein Endstatus da ist. `wait=0` antwortet sofort (für Sammel-Freigaben).

Ein Fehler im Hintergrund endet immer in `failed` mit `result.error`; wird das Dashboard
währenddessen beendet, vermerkt der Kern die Aktion als abgebrochen, und beim nächsten
Start setzt er übrig gebliebene `executing`-Zeilen auf `failed`. Das Ereignis
`action.executed` kommt erst, nachdem Ergebnis und Audit-Zeile gespeichert sind.

**Namen statt Kennungen.** Die `action`-Ressource trägt neben den rohen
Feldern (`proposed_by_type`/`proposed_by_id`, `approved_by_user_id`) zwei lesbare:
`proposed_by_label` (Benutzername, Name der Erweiterung, `System`, `Zeitplan` oder `KI`)
und `approved_by_label` (Benutzername des Entscheiders; `null`, solange niemand
entschieden hat). Nicht auflösbare Akteure (gelöschter Nutzer, entfernte Erweiterung)
bekommen bei `proposed_by_label` den rohen Wert `art/kennung`, bei `approved_by_label` die
Nutzer-Kennung so, wie sie gespeichert ist (`approved_by_user_id`, ohne `user/`-Vorsatz).
Aufgelöst wird nur der Name, mit je einer Abfrage für alle Zeilen einer Liste.
Benutzernamen **anderer** Nutzer bekommt nur, wer `users.read` hat oder Aktionen
entscheiden darf (irgendein `actions.approve:<risiko>`, z. B. Bediener); der Betrachter
(viewer) sieht dort weiter `user/<uuid>` (bzw. bei `approved_by_label` die nackte
`<uuid>`) -- `GET /users` bleibt ihm ja auch verwehrt. Den
eigenen Namen sieht jeder; Namen von Erweiterungen sowie `System`/`Zeitplan`/`KI` sind
keine Nutzerdaten und stehen jedem offen.
Extensions bekommen dasselbe für ihre eigenen Zeilen über `ctx.actions.proposer_labels(rows)`
(nachgeschlagen wird nach der `id`, nicht nach den übergebenen Zeilen). Dort prüft der Kern
nicht, wer die Seite aufruft -- die Route der Extension muss selbst abgesichert sein.

### Dashboard und Widgets

```
GET    /dashboard/layouts            GET/PUT /dashboard/layouts/{id}
GET    /widgets                      → Katalog aller WidgetSpecs aktivierter Extensions
GET    /ext/{ext_id}/{data_endpoint} → Widget-Daten, {data, meta}
```

Die Android-App holt `/widgets`, rendert die deklarativen Specs und zieht die Daten von
denselben Endpunkten wie das Web. Eine neue Extension erscheint dadurch in der App,
ohne dass die App aktualisiert wird — das ist der praktische Gewinn aus
[02 §4](02-EXTENSION-API.md#4-widgets--deklarativ-nicht-als-code).

### Eigene Apps („+ App hinzufügen“)

```
GET    /overview                     → u. a. `apps`: eigene Apps (zuerst) + erkannte Dienste, je mit `source`
GET    /apps                         → [App] (hosts.read)
POST   /apps                         → 201 App   {name, url, icon?, color?, group?, open_in_new_tab?, host_id?, sort_order?}
PATCH  /apps/{id}                    → App       nur die genannten Felder; icon/color/group/host_id mit null leeren
DELETE /apps/{id}                    → 204
PUT    /apps/order                   → [App]     {ids: [...]}: diese zuerst, in dieser Reihenfolge; Rest dahinter
                                                 (bis 50 mehr als die Obergrenze, denn das Cockpit schickt immer alle)
```

App = `{id, name, url, icon, color, group, sort_order, open_in_new_tab, host_id, host, created_at, updated_at}`
(`host` = Anzeigename des Servers). Rechte: Lesen `hosts.read` (wie die erkannten Apps im Cockpit); Schreiben
**`apps.write`** – neu und additiv: nur Administrator und Inhaber haben es von selbst. Die Kacheln stehen auf der
Startseite aller Nutzer, ein Link dort ist für jeden ein Klick-Ziel; `hosts.write` (Server und SSH-Zugänge) und
`settings.write` (ganze Installation) wären dafür deutlich zu viel. Jede Änderung steht im Protokoll
(`app.created`, `app.updated`, `app.deleted`, `app.reordered`; mit Name und `https://host:port`, nie mit Pfad oder
Parametern der Adresse).

Prüfung (422 mit deutscher Meldung am Feld `loc`): `name` 1–60 Zeichen, getrimmt, ohne Steuerzeichen, Zeilen-/Absatztrenner
(U+2028/U+2029), Sonderleerzeichen (nur das normale Leerzeichen ist erlaubt) und nicht vergebene Zeichen (nur in den
Emoji-Blöcken gelten sie als neues Emoji) und mit mindestens einem sichtbaren Zeichen (Buchstabe, Ziffer, Symbol oder
Satzzeichen – Füllzeichen wie U+3164 und Verbinder allein sind kein Name); `url` nur
`http://`/`https://` mit Rechnername oder IP, höchstens 1000 Zeichen, ohne Benutzer/Passwort, Leer-/Steuerzeichen
und Rückwärts-Schrägstrich (also nie `javascript:`, `data:`, `file:` …); `group` höchstens 40 Zeichen, mit denselben Zeichenregeln wie `name` (leer = keine Gruppe); `color`
`#rrggbb`; `icon` ein Name aus der festen Liste (`services/custom_apps.APP_ICONS`, deckungsgleich mit
`frontend/src/lib/appIcons.ts`) oder ein einzelnes Emoji – **keine Bild-Adresse** (sonst würde der Browser jedes
Nutzers beim Anzeigen der Startseite einen fremden Server anfragen: Tracking, Mixed Content); `host_id` muss ein
vorhandener Server sein; höchstens 200 Apps (`409`; die Beispieldaten halten die Grenze ebenfalls ein). **Der Server ruft die Adressen nie selbst ab** – es gibt keine
Statusabfrage für eigene Apps (kein SSRF); eine solche Prüfung wäre ein eigenes Paket.

`GET /overview` ist rein additiv erweitert: `services` und `services_running` bleiben wie sie waren (nur erkannte
Dienste, die Kennzahl „Dienste“ zählt keine Links); neu ist `apps` – eigene Apps (`source: "custom"`, nach Position,
immer frisch aus der Datenbank) vor den erkannten Diensten (`source: "detected"`, aus dem 30-s-Zwischenspeicher).
Felder: `id, source, name, url, host, host_id, state, tone, image, icon, color, group, open_in_new_tab, sort_order`
(`null`, wo es für die Art nichts gibt). Wird ein Server gelöscht, bleibt seine App stehen, nur ohne `host_id`.

### Beispieldaten

```
GET    /demo                         → {active, hosts, notifications, apps, layout_replaced, hosts_with_access}
POST   /demo/seed                    → wie GET, dazu {created}; legt Beispieldaten an (idempotent)
DELETE /demo                         → {removed_hosts, kept_hosts, removed_notifications, layout_restored, removed_apps, kept_apps}
```

„Mit Beispieldaten ansehen“: fünf Beispiel-Server (Adressen aus `192.0.2.0/24`, RFC 5737, ohne
Zugang, damit nie etwas angefragt wird), vier Meldungen unterschiedlicher Schwere mit `payload.path`
drei Beispiel-Apps (eigene Kacheln mit Adressen aus `192.0.2.0/24`, die mit den Beispieldaten wieder verschwinden;
eine, die jemand auf eine echte Adresse umgestellt hat, bleibt stehen: `kept_apps`; sie kommen hinter schon vorhandene eigene Apps) und, falls Module Widgets mitbringen,
ein Beispiel-Layout im Dashboard des aufrufenden Nutzers
(das bisherige wird gesichert und beim Löschen zurückgesetzt). Die IDs der angelegten Zeilen stehen in
der globalen Einstellung `demo.seeded_ids` – `DELETE /demo` entfernt genau diese und sonst nichts.
Ein Beispiel-Server, den jemand zu einem echten umgebaut hat (Adresse außerhalb von `192.0.2.0/24`
oder von einem Modul eingelesen), bleibt stehen (`kept_hosts`). Hängt an einem Beispiel-Server ein
Zugang, wird er mitgelöscht; `hosts_with_access` nennt solche Server vorab (für die Rückfrage).

Rechte: `GET` braucht `hosts.read`; `POST` und `DELETE` brauchen `hosts.write` **und**
`settings.write` (sie legen/entfernen Server, schreiben Meldungen für alle Nutzer und ändern das
Dashboard). `POST` antwortet `409`, sobald es echte Server gibt (außer Beispielen); ein zweiter
`POST` ändert nichts (`created: false`). `DELETE` ohne Beispieldaten ist ein harmloses Nichts;
läuft gerade eine Aktion auf einem Beispiel-Server, antwortet er `409` und ändert nichts.
Protokoll: `system.demo.seeded`, `system.demo.removed`. `NODVARD_DECK_DEMO_MODE=1` ruft beim Start
dieselbe Funktion auf (Akteur `system`).

### Dateien

```
GET    /files/sources                            → alle FileSources aller Extensions
GET    /files/{source}/list?path=&cursor=
GET    /files/{source}/stat?path=
GET    /files/{source}/download?path=             → Streaming, Range-fähig
POST   /files/{source}/upload?path=               → Streaming
POST   /files/{source}/mkdir | /rename | /remove
POST   /files/transfer                            → {from:{source,path}, to:{source,path}}
                                                    → job_run-ID, Fortschritt über WS
GET    /files/search?q=&sources=                  → Fan-out, Ergebnisse gemischt
```

`/files/transfer` ist das Drag-&-Drop zwischen Quellen im Dateimanager: der Server streamt
`open_read(A) → open_write(B)`, ohne dass die Datei über den Client läuft.

---

## 4. WebSocket

### Multiplexiert: `GET /ws`

Eine Verbindung, viele Kanäle. Auf dem Mobilgerät ist jede zusätzliche Verbindung
Akkulaufzeit — deshalb bewusst nicht ein Socket pro Feature.

Authentifizierung: erste Nachricht nach dem Verbinden ist
`{"type":"auth","token":"<access_token>"}`. Keine Tokens in der URL (sie landen in
Proxy-Logs).

Umschlag, in beide Richtungen:

```json
{ "type": "...", "channel": "...", "id": "...", "payload": { } }
```

| type | Richtung | Zweck |
|---|---|---|
| `auth` / `auth_ok` / `auth_error` | ↔ | |
| `subscribe` / `unsubscribe` / `subscribed` | ↔ | |
| `event` | ← | Nutzlast auf einem Kanal |
| `ping` / `pong` | ↔ | 30 s, App erkennt tote Verbindungen |
| `error` | ← | RFC-7807-Körper |

Kanäle:

```
events                     alles, was der Event-Bus publiziert (gefiltert nach RBAC)
notifications              neue Benachrichtigungen
actions                    Vorschlag angelegt / bestätigt / ausgeführt
hosts                      Statuswechsel
jobs.{job_id}              Laufstatus
runs.{run_id}              Live-Ausgabe eines Laufs
files.transfer.{id}        Fortschritt
ext.{ext_id}.{beliebig}    Extension-eigene Kanäle (ctx.ws.broadcast)
```

Verhalten bei Verbindungsabbruch: der Client abonniert nach dem Reconnect neu und holt
den verpassten Zustand per REST nach. Es gibt **keine** Nachrichten-Wiederholung über
WS — ein Ereignisstrom mit Zustellgarantie wäre eine Message-Queue, und die soll es laut
[00 D-09](00-DECISIONS.md#d-09-hintergrundarbeit--queue) nicht geben. Jedes Ereignis ist
ein Hinweis „schau nach", der Wahrheitsstand liegt in der REST-Ressource.

### Terminal: `GET /ws/terminal/{session_id}` — eigener Socket

Absichtlich getrennt vom Multiplex: Tastatureingaben sind latenzkritisch und Ausgaben
sind binär und voluminös; beides würde den Steuerkanal verstopfen.

```
GET  /terminal/hosts                                    → [host_id, …]   (Hosts, für die eine Sitzung möglich ist)
POST /terminal/sessions   {host_id, cols, rows, user?}  → {session_id, ws_url}
```

Die Kern-Seite `/terminal` (mehrere Tabs, je Tab eine Sitzung) baut darauf auf.
Scheitert das Öffnen, kommt vor dem Close-Frame `{"type":"error","message":…}`.

Auf dem Socket: **binäre** Frames = rohe PTY-Bytes in beide Richtungen.
**Text**-Frames = JSON-Steuerung:

```json
{"type":"resize","cols":120,"rows":40}
{"type":"signal","name":"SIGINT"}
{"type":"exit","code":0}
```

Sitzungen haben ein Leerlauf-Timeout, werden im Audit-Log als `terminal.open` /
`terminal.close` mit Dauer vermerkt, und optional (Einstellung) mitgeschnitten.

### Konsole: `GET /ws/console/{session_id}` — Bildschirm einer VM/eines Containers

```
GET  /console/hosts                  → [host_id, …]   (Hosts mit verfügbarer Konsole)
POST /console/sessions {host_id}     → {session_id, ws_url, protocol, password}
```

Anders als beim Terminal öffnet der `POST` die Konsole **sofort** (über die
`ConsoleTarget`-Capability, z. B. Proxmox' vncproxy/vncwebsocket): das Einmal-Kennwort
für die protokolleigene Authentifizierung (`protocol: "vnc"` → RFB-VNC-Auth) entsteht
erst dabei, und der Browser-Client (noVNC) braucht es vor dem WS-Aufbau. Nicht innerhalb
von `terminal_session_ttl_s` abgeholte Sitzungen werden geschlossen.

Auf dem Socket: nur **binäre** Frames, rohe Protokoll-Bytes in beide Richtungen; ein
angefragtes Subprotokoll `binary` wird bestätigt. Der Browser spricht nur mit Nodvard Deck,
nie mit dem Hypervisor — dessen Token bleibt im Vault. Jede Öffnung (auch eine
gescheiterte) steht im Audit-Log als `console.open`.

---

## 5. Was die Android-App zusätzlich braucht

Damit die App nicht auf API-Nachbesserungen warten muss, sind diese vier Dinge schon Teil
der API:

1. **`/capabilities`** — eine APK, viele unterschiedlich bestückte Server.
2. **Deklarative Widgets** — sonst ist die App bei jeder neuen Extension blind.
3. **`202`-Semantik** — der Bestätigungs-Workflow muss auf dem Telefon funktionieren,
   das ist sogar der wahrscheinlichste Ort dafür (Meldung kommt aufs Handy, Bestätigung
   mit einem Daumen).
4. **Push:** `POST /me/devices {platform, push_token}`. Der Versand läuft über einen
   `NotificationChannel`, bewusst nicht über einen zentralen Push-Relay. Praktisch heißt
   das: ntfy-Topic-Abo in der App (funktioniert ohne eigene Infrastruktur und ohne
   Google-Konto-Bindung), FCM optional für alle, die es wollen.

---

## 6. Versionierung

`/api/v1` bleibt kompatibel: nur additive Änderungen. Brechende Änderungen bekommen
`/api/v2`, beide laufen parallel, bis die App nachgezogen ist. Bis die erste APK
existiert, ist `v1` faktisch instabil — deshalb entsteht die App erst nach der
Web-Oberfläche und nicht parallel.

---

## 7. Kompatibilität

Das Dashboard heißt „Nodvard Deck“. Alle Nodvard-Produkte sollen später über einen
versionierten Vertrag („Nodvard Link“) mit Deck und der geplanten App zusammenarbeiten, und
die App spricht nur mit Deck. Deshalb gilt ab sofort für `/api/v1` (Kern **und** Extension-Routen
unter `/api/v1/ext/<id>/…`): nur abwärtskompatible Änderungen.

| Änderung | Erlaubt? |
|---|---|
| Neuer Endpunkt | ja |
| Neues optionales Feld / neuer optionaler Parameter in einer Anfrage | ja |
| Neues Feld in einer Antwort | ja |
| Endpunkt (Pfad oder Methode) entfernen oder umbenennen | **nein** |
| Antwortfeld entfernen oder umbenennen | **nein** |
| Neues Pflichtfeld / neuer Pflicht-Parameter in einer Anfrage (auch: Body, der vorher fehlte) | **nein** |
| Typ eines Feldes ändern | **nein** |
| Wert einer festen Werteliste (Enum) entfernen | **nein** |

**Umbenennen** geht nur so: Der alte Name bleibt mehrere Releases parallel bestehen und ist
als `deprecated` markiert (`deprecated=True` am Endpunkt bzw. Feld, dazu ein Hinweis im
Änderungsprotokoll). Erst wenn App und Extensions den neuen Namen nutzen, darf der alte
später entfallen. Wirklich unvermeidbare Brüche brauchen `/api/v2` (siehe §6).

**Automatischer Schutz:** `backend/tests/contract/api_v1.json` ist ein eingecheckter
Schnappschuss des Vertrags (je Pfad und Methode: Parameter, Body-Felder mit Pflicht-Kennzeichen
und Typ, Felder der 2xx-Antwort samt Enum-Werten). Die Tests in `backend/tests/contract/`
vergleichen die aktuelle `app.openapi()` (mit allen mitgelieferten Extensions) damit:

- Ein **Bruch** lässt den Test fehlschlagen; die Meldung listet alle Brüche auf.
- Ein **Zuwachs** (erlaubt) lässt einen zweiten Test fehlschlagen, bis der Schnappschuss
  nachgezogen ist: `python scripts/update_api_contract.py`, danach die geänderte JSON einchecken.
  Das Skript verweigert das Schreiben, wenn dabei ein Bruch entstünde.
- **Ausnahmen** sind nur mit Begründung möglich: Eintrag in
  `backend/tests/contract/breaking_exceptions.toml` (Pfad, Methode, Art, Begründung), dann
  `python scripts/update_api_contract.py --allow-breaking`. Die Liste ist anfangs leer und soll
  es möglichst bleiben.
