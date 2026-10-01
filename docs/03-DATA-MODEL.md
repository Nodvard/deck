# 03 — Kern-Datenmodell

SQLAlchemy 2.0, dialektneutral (SQLite-Default, Postgres optional — siehe
D-02 in [00-DECISIONS](00-DECISIONS.md)).

Konventionen:
- PK: `id` als UUIDv7-artiger String(36) (sortierbar, dialektneutral, in Logs lesbar).
- Zeitstempel: `DateTime(timezone=True)`, UTC, in Python gesetzt.
- Strukturierte Felder: `sa.JSON` (nicht `JSONB`).
- Listen: Assoziationstabelle, nie Array-Spalte.
- Soft-Delete nur da, wo es fachlich nötig ist (`revoked_at`, `disabled_at`).
- Extension-Tabellen: Präfix `ext_<id>_`, eigener Alembic-Branch, hier nicht beschrieben.

---

## 1. Identität und Zugriff

### `users`
| Spalte | Typ | Anmerkung |
|---|---|---|
| id | str(36) PK | |
| username | str(64) unique | Login |
| email | str(255) null | optional, nur für Benachrichtigungen |
| display_name | str(128) | |
| password_hash | str(255) | Argon2id |
| totp_secret_id | FK secrets.id null | 2FA-Secret liegt im Vault, nicht hier |
| is_active | bool | |
| is_owner | bool | genau einer; umgeht RBAC, kann nicht entzogen werden |
| locale | str(8) | `de` default — UI-Sprache ist Nutzereinstellung |
| created_at, last_login_at | ts | |

### `roles`, `user_roles`, `role_permissions`
`roles(id, name unique, description, is_builtin)`.
Eingebaut: `owner`, `admin`, `operator`, `viewer`.
Berechtigungen sind **Strings**, keine Tabelle — Extensions bringen eigene mit, ohne
Migration:

```
<domäne>.<aktion>[:<scope>]

hosts.read            hosts.write         hosts.execute
secrets.read:ssh-*    secrets.write
audit.read            settings.write      extensions.manage
system.read           apps.write
actions.approve       actions.approve:high
files.read:<source>   files.write:<source>
ext.nexus-soc.read    ext.nexus-soc.remediate
```

`*` als Suffix-Wildcard im Scope ist erlaubt (`secrets.read:ssh-*`), sonst exakter
Vergleich. Auswertung in `core/rbac.py`, reine Funktionen, ohne DB testbar.

### `refresh_tokens`
| Spalte | Anmerkung |
|---|---|
| id, user_id | |
| token_hash | SHA-256 des opaken Werts; der Wert selbst wird nie gespeichert |
| client_type | `web` \| `android` \| `cli` — steuert Cookie vs. Bearer |
| device_name, user_agent, ip | für „Aktive Sitzungen" in den Einstellungen |
| expires_at, revoked_at, last_used_at | |
| replaced_by_id | Nachfolger-Sitzung, nur bei Rotation gesetzt (nicht bei Logout/Passwortwechsel), ohne Fremdschlüssel. Grundlage der 30-s-Gnadenfrist beim Erneuern: ein eben rotierter Token bekommt noch einen Access-Token der Nachfolger-Sitzung, aber keinen neuen Refresh-Token (zwei Tabs, App + Tab) |

### `api_tokens`
Für Automatisierung und die Android-App im Langzeitbetrieb.
`id, user_id, name, token_hash, scopes (JSON), expires_at, revoked_at, last_used_at`.
Auch Aufrufe per Cron brauchen damit ein Token; ungeschützte Endpunkte für Automatisierung gibt es nicht.

---

## 2. Hosts

Der Kern besitzt die Host-Identität — siehe
[01 §5](01-ARCHITECTURE.md#5-hosts-gehören-dem-kern-nicht-der-extension).

### `hosts`
| Spalte | Anmerkung |
|---|---|
| id | |
| name | str(64) unique, technisch (`pve1`, `bastel-pi`) |
| display_name | str(128) |
| address | str(255) — IP oder DNS |
| os_family | `linux` \| `windows` \| `other` |
| kind | frei, von der Extension gesetzt (`vm`, `container`, `bare-metal`) |
| provider_ext_id | null bei manuell angelegten. Das ist auch das Merkmal „kein Modul pflegt den Zustand“: nur solche Server prüft der Kern-Job `host-reachability` |
| provider_ref | Fremdschlüssel beim Anbieter (z. B. `node/qemu/110`) |
| is_managed | ob Aktionen erlaubt sind |
| enabled | |
| last_seen_at | letzte erfolgreiche Prüfung (Modul, „Verbindung prüfen“ oder `host-reachability`); leer = noch nie gesehen, dann gibt es beim ersten Ausfall keine Meldung |
| status | `up` \| `down` \| `unknown` \| `maintenance`. Von Hand angelegte Server führt `host-reachability` nach (TCP zum SSH-Port, „down“ erst nach 2 Fehlschlägen hintereinander); `maintenance` fasst der Job nicht an |
| metadata | JSON, anbieterspezifisch — der Kern liest es nie inhaltlich |
| created_at, updated_at | |

`host_tags(host_id, tag)` — Tags sind das generische Ersatzkonstrukt für fest verdrahtete
Sonderlisten (etwa „alle Docker-Hosts“ oder „kritische Container“). Eine Extension fragt
„alle Hosts mit Tag `docker`" statt eine eigene Liste zu pflegen.

`host_groups(id, name, description)` + `host_group_members(group_id, host_id)` —
Zielgruppen für Skripte (Script-Repository, siehe [02 §8](02-EXTENSION-API.md#8-wie-drei-module-gegen-die-schnittstelle-implementiert-werden)).

### `host_credentials`
| Spalte | Anmerkung |
|---|---|
| id, host_id | |
| kind | `ssh_key` \| `ssh_password` \| `api_token` |
| username | |
| port | |
| secret_id | FK `secrets.id` — der Wert liegt ausschließlich im Vault |
| is_default | genau eine pro Host |

### `known_hosts`
`(host_id, key_type, fingerprint, first_seen_at, accepted_by_user_id)` —
ersetzt `StrictHostKeyChecking=no`. Abweichung ⇒ Verbindung scheitert sichtbar.

---

## 3. Secrets-Vault

### `secrets`
| Spalte | Anmerkung |
|---|---|
| id | |
| label | str(128) unique — der Name, den Extensions referenzieren |
| kind | `password` \| `ssh_private_key` \| `api_token` \| `totp` \| `generic` |
| ciphertext | LargeBinary, Fernet |
| key_version | int — ermöglicht Rotation ohne Big-Bang |
| owner_ext_id | null = gehört dem Kern/Admin |
| description | |
| created_by_user_id, created_at, rotated_at, last_used_at | |
| metadata | JSON, **nie sensibel** (z. B. Public-Key-Fingerprint) |

`secret_grants(secret_id, grantee_type ext|role, grantee_id)` — wer darf lesen.

**Invarianten, die im Code erzwungen werden:**
1. Kein API-Endpunkt gibt `ciphertext` oder Klartext zurück. Nie. Auch nicht für `owner`.
   Schreiben und Ersetzen ja, Lesen nein.
2. Extensions bekommen ein `SecretHandle`; der Klartext existiert nur innerhalb eines
   `async with ctx.vault_use(handle) as value:`-Blocks.
3. Jede Materialisierung schreibt eine Audit-Zeile (`secret.used`, mit Label und
   Verwender, ohne Wert).
4. Der Master-Key liegt nie in der DB.

---

## 4. Audit-Log

### `audit_log` — append-only
| Spalte | Anmerkung |
|---|---|
| id | |
| ts | indiziert |
| actor_type | `user` \| `extension` \| `ai` \| `scheduler` \| `system` |
| actor_id | User-ID, Extension-ID oder Modellname |
| action | `host.restart`, `secret.used`, `action.denied`, `login.failed` … |
| target_type, target_id | |
| outcome | `success` \| `failure` \| `denied` \| `proposed` |
| reason | **Text** — hier landet `BEGRUENDUNG:` |
| detail | JSON — Befehl, Exit-Code, Gate-Entscheidung, gekürzte Ausgabe |
| correlation_id | verbindet Incident → Vorschlag → Ausführung → Benachrichtigung |
| ip, user_agent | |

Kein `UPDATE`, kein `DELETE` aus der Anwendung; nur ein Retention-Job löscht jenseits von
`audit.retention_days`. Die Trennung `outcome=proposed` von `success` ist der Unterschied
zwischen „die KI wollte" und „die KI tat" — ohne sie lässt sich im Nachhinein nicht
sagen, was wirklich ausgeführt wurde.

---

## 5. Aktionen — das Gate-Journal

### `actions`
| Spalte | Anmerkung |
|---|---|
| id | |
| ext_id | wer vorschlägt |
| action_type | `shell.exec`, `docker.restart`, `vm.start`, `script.run`, `file.delete` |
| host_id | null bei nicht host-gebundenen Aktionen |
| payload | JSON |
| risk | `low` \| `medium` \| `high` \| `critical` |
| status | `proposed` → `approved` → `executing` → `succeeded`/`failed`, oder `denied`/`expired`/`dismissed` |
| proposed_by_type / _id | `ai` + Modell, `user`, `scheduler`, `event` |
| reason | Pflichtfeld, NOT NULL |
| gate_decision | JSON: welche Regel griff, welche Muster trafen |
| approved_by_user_id, approved_at | |
| executed_at, finished_at | |
| result | JSON: exit_code, stdout/stderr (gekürzt), Dauer |
| correlation_id | |
| idempotency_key | verhindert Doppelausführung bei Retry/Doppelklick |
| expires_at | unbestätigte Vorschläge verfallen (Default 24 h) |

Diese Tabelle trennt Vorschlagen und Ausführen (siehe
[01 §4](01-ARCHITECTURE.md#4-das-aktions-gate)). Die Bestätigungs-Karte in der
UI ist eine Zeile mit `status=proposed`; „volle Autonomie" heißt, dass das Gate direkt auf
`approved` setzt — derselbe Datensatz, dieselbe Nachvollziehbarkeit, nur ohne Klick.

`action_flap_history(host_id, fingerprint, ts, blocked)` — generalisiertes Anti-Flapping
über einen Hash aus (host, action_type, normalisiertem Payload), nicht nur über
Docker-Restarts. Statt fester Grenzwerte in einem einzelnen Watcher gilt es
für **alle** Extensions, weil es im Kern sitzt.

---

## 6. Extension-Registry

### `extensions`
| Spalte | Anmerkung |
|---|---|
| id | = Manifest-ID, PK |
| version, api_version | |
| state | `enabled` \| `disabled` \| `error` \| `incompatible` \| `uninstalling` |
| source | `bundled` \| `local` \| `pip` \| `marketplace` |
| manifest | JSON, wie eingelesen |
| granted_permissions | JSON — was der Admin bestätigt hat, kann weniger sein als beantragt |
| settings | JSON, gegen das deklarierte Schema validiert |
| last_error, last_error_at | |
| installed_at, updated_at | |

### `connector_instances`
`id, ext_id, type_id, name, config (JSON, Secrets als `{"$secret": "<label>"}`-Referenz),
enabled, health_status, health_message, last_check_at`.

---

## 7. Zeitplan und Läufe

### `jobs`
`id, ext_id (null = Kern), name, kind, schedule (Cron), timezone, target (JSON: host/group),
params (JSON), enabled, next_run_at, created_by_user_id`

`schedule` ist normales 5-Felder-Crontab (Minute Stunde Tag Monat Wochentag) in der Zeitzone des Jobs.
Im Wochentagsfeld sind **0 und 7 beide Sonntag**, 1 = Montag … 6 = Samstag – wie im Zeitplan-Wähler.
Die Übersetzung auf APSchedulers eigene Zählung (0 = Montag) macht `nodvard_deck.core.cron.cron_trigger`;
dasselbe gilt für die Cron-Ausdrücke der Wartungsfenster.

**Eine** Tabelle für alle geplanten Arbeiten — Audits von Nodvard Shield und Skripte gleichermaßen.
Die „Fleet-weiter Zeitplan"-Ansicht ist ein `SELECT`. Ein versehentlich doppelt angelegter
Job fällt darin als Doppelzeile auf.

### `job_runs`
`id, job_id (null bei Ad-hoc), ext_id, trigger (manual|schedule|ai|event), status
(running|succeeded|failed|interrupted|skipped), started_at, finished_at, exit_code,
host_id, output_ref, error, actor, correlation_id`

`output_ref` zeigt auf eine Datei unter `/data/runs/<id>.log`, nicht in die DB —
Skript-Ausgaben sind unbegrenzt groß, DB-Zeilen sollten es nicht sein.
`interrupted` existiert, damit ein Neustart mitten im Lauf sichtbar wird statt zu
verschwinden.

**Aufräumen** (`services/job_retention.py`, Kern-Job `job-runs-retention`, nachts 03:20): beendete Läufe
(`status` ≠ `running`), die vor mehr als `jobs.run_retention_days` Tagen (Vorgabe 30, 1–3650) begannen, werden samt
Protokolldatei gelöscht. Pro `job_id` bleiben immer die 20 neuesten beendeten Läufe stehen, Läufe mit `job_id` leer
(Job gelöscht, Ad-hoc) gehen nur nach Alter, laufende nie. Gelöscht wird in Häppchen zu 5000 Zeilen (Commit dazwischen,
damit SQLite nicht lange sperrt); Dateien erst nach dem Commit und nur innerhalb von `<Datenordner>/runs`.
Zusätzlich verschwinden `*.log`-Dateien dort, zu denen keine Zeile gehört und die älter als die Aufbewahrung sind.
`host-reachability` räumt seine Läufe weiterhin selbst nach 24 Stunden auf (`services.jobs.prune_runs`).
Die Datenbank läuft im WAL-Modus mit `auto_vacuum=NONE`; freie Seiten füllt SQLite mit neuen Zeilen wieder auf, die
Dateigröße pendelt sich ein. Ein `VACUUM` (schreibt alles neu, hält die Schreibsperre, braucht bis zum Doppelten an
Platz) und `incremental_vacuum` (wirkt nur nach einem solchen Umstellen) laufen bewusst nicht im Betrieb.

---

## 8. Benachrichtigungen

`notifications(id, ts, severity (info|warning|critical), title, body, source_ext_id,
correlation_id, read_at, payload)` — die Historie im Dashboard
(„Notification-Center").

Versandkanäle sind **Connector-Instanzen** einer Extension, die `NotificationChannel`
erfüllt, keine eigene Tabelle. `notification_deliveries(notification_id, channel_id,
status, error, sent_at)` protokolliert, ob der Versand geklappt hat — ohne diese
Rückmeldung fällt ein Kanal, der ins Leere meldet, unter Umständen wochenlang nicht auf.

---

## 9. Einstellungen und Branding

`settings(key, scope (global|user), user_id, value JSON, updated_at, updated_by_user_id)`

Reservierte Schlüssel:

```
branding.product_name      branding.short_name     branding.logo_url
branding.favicon_url       branding.colors         branding.login_subtitle
autonomy.mode              autonomy.max_risk       autonomy.window
audit.retention_days       maintenance.windows     security.deny_patterns
jobs.run_retention_days
locale.default             notifications.defaults     system.timezone
backup.config              backup.key              backup.last_run
system.instance_id         hosts.reachability.enabled
hosts.reachability.interval_minutes                hosts.reachability.state
```

Sicherungen (`services/backups.py`, nur über `/system/backups/...`, nicht über `/settings`; Ändern nur der Owner):

| Schlüssel | Inhalt |
|---|---|
| `backup.config` | `{enabled, schedule, keep, dir, include_runs}`; `dir = null` heißt `<Datenordner>/backups` |
| `backup.key` | `{recipient ("age1…"), key_id, kdf {alg: "argon2id", t, m_kib, p, salt}, created_at}` – **kein** Passwort, **kein** privater Schlüssel. Aus Passwort + `kdf` entsteht der Schlüssel bei Bedarf neu |
| `backup.last_run` | `{at, ok, trigger, name, size, error, deleted[]}` |
| `system.instance_id` | zufällige Kennung dieser Installation (steht im Manifest jeder Sicherung) |

`system.read` (Einstellungen → System ansehen) hat nur `admin` über `*`; Kritisches dort verlangt den Owner
selbst, siehe docs/04-API.md „System und Sicherungen“.

`system.timezone` (IANA-Name, Vorgabe aus `NODVARD_DECK_TIMEZONE`/`TZ`, sonst Europe/Berlin) und
`audit.retention_days` (7–3650, Rückfall die Umgebungsvariable) und `jobs.run_retention_days` (1–3650, Vorgabe 30;
Aufbewahrung der Job-Läufe, siehe §7 `job_runs`) sind über `PUT /settings/{key}` setzbar, siehe
docs/04-API.md §3 „Einstellungen“.

Erreichbarkeitsprüfung (`services/reachability.py`): `hosts.reachability.enabled` (Vorgabe `true`) und
`hosts.reachability.interval_minutes` (1–60, Vorgabe 2) sind über `PUT /settings/{key}` setzbar. `hosts.reachability.state`
ist ein interner Merker `{host_id: {"muted": bool}}` für Server, deren Ausfall schon gemeldet wurde (`muted` = im
Wartungsfenster nur im Verlauf, wird danach einmal hörbar nachgeholt); er steht nicht in `GET /settings`.
Der Kern-Job `host-reachability` hängt wie die anderen Kern-Jobs in `jobs` (`ext_id` null); seine Läufe in `job_runs`
bleiben nur 24 Stunden, weil er bis zu 1440 mal am Tag läuft.

`maintenance.windows` ist eine Liste aus (Cron-Ausdruck, Dauer, betroffene Hosts, was
unterdrückt wird) statt eines fest eingebauten Fensters. Wichtig: die Unterdrückung gilt
**symmetrisch** für Offline- *und* Online-Meldungen — sonst kommt nach einem still
gemeldeten Ausfall eine hörbare Entwarnung ohne Vorgeschichte.

Welche Meldung ein Fenster erfasst, steht im Payload der Meldung: `host_id` (ein Server)
oder `host_ids` (Sammelmeldung über mehrere Server, nur still, wenn **alle** in einem
laufenden Fenster liegen). Eine Meldung ohne beides wird nie unterdrückt.

`security.deny_patterns` ist die Sperrliste des Aktions-Gates
([01 §4](01-ARCHITECTURE.md#4-das-aktions-gate)) als Konfiguration statt als
Konstante: der Kern liefert einen Satz Defaults mit, Extensions dürfen ergänzen,
niemand darf sie leeren.

### `dashboard_layouts`
`id, user_id, name, is_default, items (JSON: [{widget_id, ext_id, x, y, w, h, config}])` —
identisch für Web und App, damit die Anordnung auf dem Telefon nicht neu erfunden wird.

### `custom_apps`
Eigene App-Kacheln im Cockpit („+ App hinzufügen“) – von Hand angelegte Links für alles, was der
Container-Erkennung nicht auffällt (Router, NAS-Oberfläche, Pi-hole …).

| Spalte | Anmerkung |
|---|---|
| id | |
| name | str(60), getrimmt; ohne Steuerzeichen, Zeilen-/Absatztrenner und Sonderleerzeichen, mit mindestens einem sichtbaren Zeichen |
| url | str(1000), nur `http://`/`https://`, ohne Benutzer/Passwort und ohne Leer-/Steuerzeichen |
| icon | str(32) null: Name aus der festen lucide-Auswahl (`services/custom_apps.APP_ICONS`) oder ein einzelnes Emoji – nie eine Bild-Adresse |
| color | str(7) null: `#rrggbb`; leer = Farbe nach dem Namen |
| group_name | str(40) null: Gruppe für den Filter im Cockpit (in der API `group`), gleiche Zeichenregeln wie `name` |
| sort_order | int, Index: Position (neue Apps kommen ans Ende; `PUT /apps/order` zählt neu durch) |
| open_in_new_tab | bool, Standard `true` |
| host_id | FK hosts.id null, `ON DELETE SET NULL`: optionaler Server-Bezug, nur zur Anzeige |
| created_by_user_id | str(36) null, ohne Fremdschlüssel (wie `jobs`) |
| created_at, updated_at | ts |

Der Server ruft die Adresse **nie selbst** ab (keine Statusabfrage → kein SSRF); sie ist nur ein Link im Browser.
Höchstens 200 Apps (das Anlegen gibt sonst `409`; die Beispieldaten halten die Grenze ein, und beim Verschieben darf die Liste etwas länger sein, falls zwei Anlegen gleichzeitig durchkamen). Alembic-Revision `d4b7a2c91e63` im Kern-Zweig (Downgrade löscht Tabelle und Indizes).
Die Berechtigung `apps.write` ist additiv (rein ein neuer String, keine Migration): Administrator und Inhaber
haben sie, Bediener und Betrachter nicht von selbst; Lesen braucht wie die erkannten Apps `hosts.read`.
