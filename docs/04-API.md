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
| Fehler | JSON `{"detail": …}`: meist ein deutscher Satz, bei `422` aus der Eingabeprüfung eine Liste `[{type, loc, msg}]` |
| Paginierung | Cursor: `?limit=50&cursor=…` → `{items, next_cursor}`. Kein Offset. |
| Filter | explizite Query-Parameter, keine generische Filtersprache |
| Zeit | ISO-8601 mit `Z`. Immer UTC. Lokalzeit ist Client-Sache. |
| IDs | UUIDv7-artige Strings |
| Idempotenz | `Idempotency-Key`-Header auf allen Ausführungs-Endpunkten |
| Korrelation | `X-Request-Id` rein, in Antwort und Audit-Log wieder raus |
| Teilantworten | `?fields=` wird **nicht** unterstützt — Mobilfunk-Sparsamkeit läuft über schmale, zweckgebundene Endpunkte |

Lehnt die Eingabeprüfung eine Anfrage ab (`422`), nennt die Antwort je Feld nur `type`, `loc` und `msg`, nie die
Eingabe selbst. `msg` ist auch bei den häufigen Standardprüfungen ein deutscher Satz (Pflichtangabe fehlt, zu kurz
oder zu lang, Zahl zu klein oder zu groß, falscher Typ bei Zahlen, Text oder ja/nein, Wert nicht erlaubt, kein
gültiges JSON, unbekannte Angabe), etwa „Mindestens 8 Zeichen.“; seltenere Prüfungen (etwa eine Liste statt eines
Objekts) behalten den englischen Text von pydantic. Eigene Prüfungen (`type` `value_error`) liefern ihren eigenen
Satz, der das Feld oft schon nennt („Benutzername: …“). Wirft ein Endpunkt einen SSH-Fehler (Server nicht erreichbar,
Anmeldung abgelehnt, Server-Schlüssel nicht bestätigt) oder `HostUnreachable` (auch aus einer Erweiterung) und fängt
ihn nicht selbst ab, kommt `502` mit dem Grund in `detail` statt `500`, ohne Traceback im Container-Protokoll. Ein
anderer Netzfehler (etwa einer HTTP-Verbindung), den der Endpunkt nicht abfängt, bleibt `500`.

Der Body einer Anfrage ist standardmäßig auf 1 MiB begrenzt (`NODVARD_DECK_MAX_BODY_BYTES`), auch ohne Anmeldung. Ist die
`Content-Length` größer, antwortet der Server mit `413` (dazu `Connection: close`), sobald der Endpunkt den Body liest,
und liest kein Byte davon; ein Endpunkt, der ihn gar nicht liest (etwa ein Upload, dessen Anmeldung oder Berechtigung
schon scheitert), antwortet wie gewohnt. Ohne Längenangabe (`Transfer-Encoding: chunked`) zählt der Server mit und bricht
beim Überschreiten mit `413` ab. `detail` nennt die erlaubte Größe
(„Die Anfrage ist zu groß (erlaubt sind höchstens 1 MB).“). Uploads haben eigene, höhere Grenzen: Sicherung hochladen
4 GiB (`NODVARD_DECK_RESTORE_MAX_UPLOAD_BYTES`), Logo 2 MB, Dokumente (Erweiterung `documents`) 20 MB, Bilder im Inventar
(Erweiterung `inventory`) 5 MB; `POST /files/{source}/upload` ist standardmäßig unbegrenzt
(`NODVARD_DECK_FILES_MAX_UPLOAD_BYTES`, siehe „Dateien“). Erweiterungen erklären eine höhere Grenze mit `max_body_bytes`
aus dem SDK ([02 §2](02-EXTENSION-API.md#routen-und-anmeldung)).

---

## 2. Ohne Authentifizierung

```
GET  /api/v1/health              → {status, version, uptime_s}
                                   (Notseite statt Anwendung: 503 {"status":"rescue"} – nie "ok", siehe „Kopie vor jeder Migration“)
GET  /api/v1/branding            → Name, Logo, Farben (Login-Seite braucht es)
GET  /api/v1/branding/logo       → das hochgeladene Logo (404, wenn keins da ist). Antwortet mit
                                   `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; sandbox`
                                   und `X-Content-Type-Options: nosniff`: Wer die Adresse direkt öffnet, bekommt kein
                                   Skript ausgeführt; als `<img>` eingebunden ändert sich nichts.
POST /api/v1/auth/login          → {username, password}
                                   → 200 {access_token, expires_in, refresh_token?, user}
                                   → 202 {mfa_required: true, mfa_token}
                                   → 401 „Ungültiger Benutzername oder Passwort.“, auch bei deaktiviertem Konto
                                   und zu langer Eingabe (Name über 64, Passwort über 1024 Zeichen). An der
                                   Antwortzeit lässt sich nicht erkennen, ob es das Konto gibt oder ob es
                                   deaktiviert ist. Einen unbekannten Namen (eine zu lange Eingabe zählt genauso) nimmt das
                                   Protokoll (`login.failed`, `login.locked`) nicht auf, die Einträge gibt es aber: Akteur ist
                                   `anonymous`/`unbekannt`, dazu nur Kennung und Länge des Namens (siehe `/audit`).
                                   → 503 mit `Retry-After: 5`: gerade laufen zu viele Passwortprüfungen (höchstens
                                   2 zugleich, 16 wartend); zählt nicht als Fehlversuch. Wird das Konto während
                                   der Prüfung deaktiviert oder sein Passwort geändert: 401 wie bei falschem Passwort.
POST /api/v1/auth/mfa            → {mfa_token, code} → wie 200 oben
                                   `code` = 6-stelliger Authenticator-Code ODER ein Wiederherstellungs-Code
                                   (`ABCDE-FGHJK`, Schreibweise egal); Letzterer wird dabei verbraucht.
                                   Falsche Codes zählen gegen das 5er-Limit je `mfa_token`. Ein `mfa_token` gilt
                                   nur für eine Anmeldung (danach 401), ein Authenticator-Code je Konto nur einmal
                                   (wiederholt: 401 „Dieser Code wurde schon benutzt. …“). Falsche 6-stellige
                                   Codes zählen außerdem je Konto, egal von welcher IP und zusammen mit den
                                   falschen Codes bei Bestätigungen (Zwei-Faktor abschalten, neue
                                   Wiederherstellungs-Codes, Sicherung laden und einspielen, Update und Rückweg;
                                   siehe §3 und „System und
                                   Sicherungen“): nach 10 in 15 Min oder 20 in 24 Std 429 mit `Retry-After` (auch
                                   mit richtigem Code), `login.locked` (beginnt die Sperre bei einer Bestätigung:
                                   `auth.password_check_locked`) im
                                   Protokoll und eine Meldung „Zwei-Faktor-Code wird durchprobiert“. Für
                                   Wiederherstellungs-Codes gilt diese Sperre nicht. Mit Wiederherstellungs-Code
                                   auch 503 wie bei `/auth/login`; der Code wird dann nicht verbraucht, der Versuch
                                   zählt nicht.
                                   Die Zählung je Konto übersteht einen Neustart: Beim Start trägt Nodvard Deck die
                                   falschen Codes der letzten 24 Stunden aus dem Protokoll wieder ein
                                   (`mfa.failed`, `auth.totp_check_failed`, ohne `via: recovery_code`; Zeitstempel
                                   bis 24 Stunden in der Zukunft zählen als „jetzt“, spätere gar nicht). Die Sperren
                                   je Adresse und Name und je `mfa_token` beginnen nach einem Neustart neu.
GET  /api/v1/auth/bootstrap      → {needed}: gibt es noch keinen Nutzer? Solange keiner existiert, steht
                                   zusätzlich `restore: {ok: false, message, at}` da, wenn eine Wiederherstellung
                                   aus dem Assistenten NICHT geklappt hat (der Assistent erklärt dann warum).
POST /api/v1/auth/bootstrap      → {username, password, setup_code} → 201 {id, username, is_owner}
                                   Erstinbetriebnahme: legt den Owner an. Der `setup_code` steht im
                                   Container-Protokoll (oder `NODVARD_DECK_SETUP_CODE`). Fehlt/falsch: 403;
                                   zu viele Fehlversuche je IP: 429; es gibt schon einen Nutzer: 409;
                                   zu viele Passwortprüfungen gleichzeitig: 503 wie bei `/auth/login`.
                                   Nach Erfolg wird der Code gelöscht.
                                   `username` wird getrimmt und klein geschrieben, hat 3 bis 64 Zeichen,
                                   beginnt mit Buchstabe oder Ziffer und enthält nur `a-z`, `0-9`, `.`, `-`
                                   und `_`; `password` 8 bis 255 Zeichen. Sonst 422 mit deutscher Meldung
                                   („Benutzername: Nur Kleinbuchstaben, Ziffern sowie . - und _ erlaubt …“).
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

Routen von Erweiterungen (`/api/v1/ext/<id>/…`) gehören nicht dazu: Sie verlangen eine Anmeldung, außer
eine Erweiterung bietet sie mit der Berechtigung `api.public` ausdrücklich ohne an
([02 §2](02-EXTENSION-API.md#routen-und-anmeldung)).

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
                                     → setup: Body {current_password} → 200 {secret, otpauth_uri}; ohne Passwort
                                       400 „Zum Einschalten der Zwei-Faktor-Anmeldung brauchst du dein aktuelles
                                       Passwort.“ (der Body ist im Schema optional, damit ältere Aufrufer diese
                                       Antwort statt 422 bekommen); falsches Passwort wie unten
                                     → confirm: 200 {recovery_codes: [10 Codes]} (einmalig sichtbar,
                                       gespeichert werden nur Argon2-Hashes); nur für eine noch nicht
                                       bestätigte Einrichtung (sonst 409 „Zwei-Faktor ist schon aktiv.“);
                                       falsche Codes: 400, gedrosselt (s. u.); der bestätigende Code gilt danach je
                                       Konto nicht noch einmal (Anmeldung, Bestätigung)
                                     → DELETE /me/totp: Body {current_password, totp_code}; 204, löscht auch die Codes
                                       und meldet alle ANDEREN Anmeldungen ab (die aktuelle bleibt). Ist Zwei-Faktor an,
                                       ist `totp_code` Pflicht: der aktuelle Code aus der App (gilt je Konto nur einmal)
                                       oder ein Wiederherstellungs-Code (`ABCDE-FGHJK`, Schreibweise egal; wird
                                       verbraucht). Erst das Passwort (falsch: 400 ohne `code`, der Code bleibt
                                       unangetastet), dann der Code: fehlt er 403 {detail, code: "totp_missing"} (kein
                                       Fehlversuch); falsch 400 {detail, code: "totp_wrong"}, App-Code schon benutzt 400
                                       {detail, code: "totp_used"}; zu viele 429 mit `Retry-After`. Falsche App-Codes
                                       zählen auf die Grenze je Konto wie bei /auth/mfa (10 in 15 Min, 20 in 24 Std,
                                       zusammen mit Anmeldung und Bestätigungen); greift sie, sagt die 429-Meldung, dass
                                       ein Wiederherstellungs-Code weiter geht. Falsche Wiederherstellungs-Codes zählen
                                       wie bei der Anmeldung auf einen eigenen Zähler (10 in 5 Min, je Konto statt je
                                       Adresse, das richtige Passwort setzt ihn nicht zurück) und auf den aller
                                       Sicherheitsabfragen des Kontos (30 in 5 Min). Ohne Zwei-Faktor reicht das Passwort.
                                       Audit `auth.2fa_disabled` mit `detail.via` (`totp`/`recovery_code`); falsche Codes
                                       `auth.totp_check_failed` (`aktion`, ggf. `via`, `replayed`), nie der Code. Mit
                                       Wiederherstellungs-Code zusätzlich `mfa.recovery_used` ({remaining, aktion}) und
                                       eine Meldung wie bei der Anmeldung.
POST   /me/recovery-codes           → {current_password, totp_code} → 200 {recovery_codes}; ersetzt alle alten Codes;
                                       409 ohne aktive 2FA; `totp_code` (App-Code oder einer der bisherigen
                                       Wiederherstellungs-Codes), Antworten, Audit (`auth.recovery_codes_generated` mit
                                       `detail.via`) und Meldung wie bei DELETE /me/totp.
                                     → Sicherheitsabfragen (POST /me/password, POST /me/totp/setup, DELETE /me/totp,
                                       POST /me/recovery-codes, POST /users/{id}/reset-2fa mit dem Passwort des Admins):
                                       falsches `current_password` = 400 „Das aktuelle Passwort stimmt nicht.“ + Audit
                                       `auth.password_check_failed`. Dazu zählt auch ein falscher Code bei
                                       POST /me/totp/confirm (Audit `auth.totp_confirm_failed`). Gedrosselt je
                                       Nutzer über alle zusammen: 10 Fehlversuche / 5 Min, dann 429 mit
                                       `Retry-After` (auch mit richtigem Passwort); der Beginn der Sperre steht als
                                       `auth.password_check_locked` im Protokoll.
                                     → Abmelden (Passwortwechsel, Konto deaktivieren, 2FA abschalten/zurücksetzen,
                                       Notfall-Befehl) widerruft die Refresh-Tokens sofort; bereits ausgestellte
                                       Access-Tokens laufen aber noch bis zu 15 Minuten weiter (stateless JWT,
                                       `access_token_ttl_seconds`). Ein deaktiviertes Konto bekommt trotzdem sofort
                                       401. Bei `/ws` kann sich eine beendete Anmeldung nicht neu verbinden, offene
                                       Verbindungen enden nach spätestens 30 s (siehe §4).
                                       Terminal und Konsole einer abgemeldeten Anmeldung enden dagegen nach
                                       spätestens 15 s (siehe §4, „Terminal“).
POST   /me/devices                  → Push-Registrierung der App

GET    /app/changelog               → {current, build, unreleased[], versions[]}: Änderungsprotokoll
                                       (jeder angemeldete Nutzer; Dateien unter backend/src/nodvard_deck/changelog/)

GET    /users        POST /users     GET/PATCH/DELETE /users/{id}
                                     → POST: `username` wie bei `POST /auth/bootstrap` (getrimmt, klein, 3 bis 64
                                       Zeichen, nur `a-z 0-9 . - _`, vorne Buchstabe oder Ziffer; sonst 422). Die Regel
                                       gilt nur für neue Konten: `/auth/login` und der Notfall-Befehl nehmen weiter
                                       jeden Namen an, ältere Konten (etwa mit Leerzeichen im Namen) bleiben nutzbar.
                                       `email` (POST und PATCH) prüft der Server nicht.
                                     → PATCH: `password` setzt nur das Passwort ANDERER Nutzer; das eigene → 409
                                       (nur über /me/password mit Altpasswort), das des Owners → 403 (nur er selbst)
                                     → PATCH `is_active: false` (Owner → 409) und ein neues `password` melden den Nutzer
                                       überall ab (alle Refresh-Tokens widerrufen). Nach dem Wieder-Aktivieren gilt keine
                                       alte Anmeldung mehr, er muss sich neu anmelden; nur ein vorher ausgestellter, noch
                                       nicht abgelaufener Access-Token gilt bei HTTP-Anfragen bis zu seinem Ablauf wieder
                                       (nicht bei `/ws`, siehe §4).
                                     → Protokoll: `user.created`, `user.updated`, `user.deleted` (wer handelt,
                                       `target_id` = Konto). `detail` nennt `username`, Rollen, bei PATCH nur die
                                       Änderungen (`roles`/`is_active` mit altem und neuem Wert,
                                       `display_name_changed`, `email_changed`, `password_reset`); nie ein Passwort
                                       oder eine E-Mail-Adresse.
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
                                     → PUT …/value und DELETE: 409, wenn das Secret der Zwei-Faktor-Schlüssel eines
                                       Kontos ist (abschalten nur über DELETE /me/totp, POST /users/{id}/reset-2fa
                                       oder den Notfall-Befehl)
POST   /secrets/{id}/test            → Verwendbarkeit prüfen, ohne den Wert zu zeigen

GET    /audit?actor_type=&action=&outcome=&target_type=&target_id=&correlation_id=&since=&until=
               &exclude_action=…&limit=100&offset=0   (limit höchstens 1000)
GET    /audit/export                 → NDJSON-Stream
                                     → /audit und /audit/export: Ausgabe in `detail` (Ergebnis von `action.executed`,
                                       Felder `output`/`stdout`/`stderr`) und Befehle (Felder `command`) nur mit
                                       `hosts.execute`, auch in alten Einträgen; siehe „Aktionen“
                                     → /audit und /audit/export: eingetippte Anmeldenamen stehen in keiner Antwort.
                                       `detail.username_ref` und `detail.username_length` bekommt nur der Owner
                                       (für alle anderen, auch Admins, fehlen sie). Alte Einträge zu unbekannten
                                       Namen zeigt die Antwort für alle mit Akteur `anonymous`/`unbekannt`; bei
                                       `login.locked` fehlt `detail.username` immer, auch bei bekannten Konten.
                                       Das Antwortformat bleibt gleich.

GET    /settings     PUT /settings/{key}
GET    /branding     PUT /branding   POST /branding/logo
                                     → PUT: `support_url` braucht das Schema `http:`, `https:` oder `mailto:`
                                       (Groß-/Kleinschreibung egal, dahinter nicht leer) und keine Leer- oder
                                       Steuerzeichen mittendrin, sonst 422. Leerraum am Rand fällt weg, leer = kein
                                       Link (`null`). Ein früher gespeicherter ungültiger Wert kommt in
                                       `GET /branding` als `null` zurück.
                                     → POST /branding/logo: roher Body, `Content-Type` `image/png`, `image/jpeg`,
                                       `image/svg+xml` oder `image/webp` (sonst 415), höchstens 2 MB (sonst 413).
                                       Ein SVG wird vor dem Speichern geprüft (Erlaubnisliste): keine Skripte,
                                       Ereignis-Attribute (`on…`), Animationen, `<foreignObject>` oder
                                       Verarbeitungsanweisungen (`<?xml-stylesheet …?>`), keine DOCTYPE-Angabe mit
                                       `[…]` (eigene Entitäten), keine fremden Namensräume (außer denen von
                                       Inkscape/Illustrator), Verweise (`href`, `url(…)`) nur auf Teile derselben
                                       Datei oder eingebettete PNG-/JPEG-/GIF-/WebP-Bilder (`data:…;base64,…`).
                                       Sonst, auch bei einer kaputten Datei, 422 „SVG-Logo abgelehnt: …“.

GET    /extensions                   GET /extensions/{id}
POST   /extensions/{id}/enable       POST /extensions/{id}/disable
PUT    /extensions/{id}/settings     PUT /extensions/{id}/permissions
POST   /extensions/{id}/reload       DELETE /extensions/{id}
GET    /extensions/{id}/frontend/index.js     → ESM-Bundle
GET    /extensions/{id}/settings     → {schema, values, secrets: [{label, title, description, item, is_set, optional}], secrets_cleared}
PUT    /extensions/{id}/secrets      → Geheimnis eines `x-secrets`-Labels setzen/ersetzen (nie lesen); `409`, solange keines
                                       der Felder aus `x-secret-bound-to` einen gespeicherten Wert oder `default` hat
DELETE /extensions/{id}/secrets?label=…  → Geheimnis entfernen (idempotent, 204)
POST   /extensions/{id}/test         → Verbindung prüfen, Body optional {"mode": "connection"|"message"}

GET    /connectors                   POST /connectors
GET/PATCH/DELETE /connectors/{id}    POST /connectors/{id}/test

GET    /jobs         POST /jobs      GET/PATCH/DELETE /jobs/{id}
POST   /jobs/{id}/run                → sofort ausführen
GET    /jobs/{id}/runs               GET /runs/{id}      GET /runs/{id}/output
POST   /runs/{id}/cancel

GET    /notifications?unread=        POST /notifications/read
GET    /notifications/{id}           POST /notifications/read-all
GET    /notifications/unread-count   → {unread}
```

**Meldungen** (`notifications.read` zum Lesen und Zählen): Der Lesestatus (`read_at`) gilt für alle Nutzer gemeinsam.
Als gelesen markieren (`POST /notifications/read` mit `{ids}`, `POST /notifications/read-all`) verlangt deshalb
zusätzlich `notifications.write` (eingebaut: Bediener, Admin, Owner), sonst `403`.

**Server, Zugänge und Gruppen** (`hosts.read` zum Lesen, `hosts.write` zum Ändern; jede Änderung steht im
Protokoll):

- `POST /hosts` und `PATCH /hosts/{id}` prüfen die Eingaben. Der Kurzname (`name`) wird getrimmt und klein
  geschrieben und muss `[a-z0-9][a-z0-9_-]{0,63}` entsprechen; die Adresse ist eine IP-Adresse (nicht `0.0.0.0`/`::`,
  keine Multicast-Adresse) oder ein Rechnername nach RFC 1123 – ohne Schema, `user@`, Pfad oder Port (ein einzelner Punkt am Ende wird entfernt); `tags` folgen
  den Markierungsregeln (höchstens 20). Ungültig → `422` mit deutscher Meldung. Der Kurzname lässt sich nicht ändern.
- `HostOut` enthält außerdem `credential` (der Standard-Zugang als `{id, kind, username, port}` oder `null`,
  nie ein Geheimnis), `managed_tags` (die von einer Erweiterung verwalteten Markierungen, Teilmenge von `tags`) und
  `login_ok_at`: wann sich Nodvard Deck mit dem aktuellen Standard-Zugang an dieser Adresse und diesem Port
  nachweislich angemeldet hat. `last_seen_at` heißt dagegen nur, dass der Server geantwortet hat (dem Kern-Job
  `host-reachability` reicht dafür, dass der SSH-Port Verbindungen annimmt). `login_ok_at` setzt eine gelungene
  Anmeldung mit dem Standard-Zugang bei `POST /hosts/{id}/check` oder `GET /hosts/{id}/status` (jedes Mal neu) oder
  eine SSH-Verbindung einer Erweiterung (`ctx.exec`; nur, wenn noch kein Beleg da ist), nie der Kern-Job
  `host-reachability`. `null` heißt: nicht belegt – noch nie, oder seit der Server die Anmeldung mit dem
  Standard-Zugang abgelehnt bzw. einen anderen Server-Schlüssel gezeigt hat oder seit einer neuen Adresse oder einem
  neuen Standard-Zugang, jeweils bis zur nächsten gelungenen Anmeldung (Ausnahme: `make-default` nach frischer
  Prüfung, siehe unten).
- `DELETE /hosts/{id}` löscht auch die gespeicherten Zugangsdaten samt ihren Geheimnissen im Tresor und schließt offene
  SSH-Verbindungen. Läuft gerade eine Aktion auf dem Server (`executing`) → `409`.
- `GET /hosts/{id}/metrics` (`hosts.read`) fragt die aktuellen Messwerte beim `MetricsProvider` des Servers ab →
  `{values, sampled_at}` (unbekannter Server oder kein Provider: `404`). Scheitert die Abfrage an der Verbindung
  (SSH-Fehler wie Server nicht erreichbar, Anmeldung abgelehnt oder Schlüssel nicht bestätigt, `HostUnreachable`,
  Zeitüberschreitung oder ein anderer Netzfehler), kommt `502` mit dem Grund in `detail`. Jeder andere Fehler des
  Providers bleibt `500`, auch ein Dateifehler oder ein eigener Fehler einer Erweiterung (so meldet die
  Proxmox-Erweiterung einen nicht erreichbaren Proxmox).
- `POST /hosts/{id}/credentials`: `username` `[A-Za-z0-9_][A-Za-z0-9_.@\ -]{0,63}` (auch Windows-Namen wie `Max Mustermann`, `user@domain`, `DOMAIN\user`; keine Steuerzeichen), `port` 1–65535; bei `kind = ssh_key`
  muss `secret_value` ein privater Schlüssel ohne Passphrase sein (sonst `422`: „Der Schlüssel ist mit einer Passphrase
  geschützt …“ bzw. „Das ist kein gültiger privater SSH-Schlüssel.“). Schlüssel und Passwörter kommen nie in einer
  Antwort oder im Protokoll zurück – auch `422`-Antworten enthalten die Eingabe nicht (nur `type`, `loc`, `msg`).
  Zugang oder gemerkten Schlüssel zu löschen bzw. die Adresse zu ändern schließt offene SSH-Verbindungen des Servers.
  Eine neue Adresse löscht außerdem die gespeicherten SSH-Passwörter des Servers samt Geheimnis im Tresor (Schlüssel
  bleiben): Ein Passwort gehört zu dem Server, bei dem es eingegeben wurde. Das gilt auch, wenn der Abgleich einer
  Erweiterung (z. B. Proxmox) die Adresse ändert; ausgenommen sind dort VMs und LXC-Container, deren Adresse der Gast
  selbst meldet und für die schon ein Server-Schlüssel gemerkt ist.
- `GET /hosts/{id}/known-hosts` → `[{key_type, fingerprint, first_seen_at, accepted_by_user_id, accepted_by_label}]`,
  sortiert nach `key_type`. `accepted_by_label` ist der Benutzername (`null` bei automatischem Merken beim ersten
  Kontakt); fremde Namen sehen nur Nutzer mit `users.read` oder Freigaberecht, sonst steht `user/<id>` da.
- `GET /hosts/{id}/requirements` (`hosts.read`) → `[{ext_id, id, label, check_command, ok_text, fail_hint,
  unix_group, needs_root, root_reason, order}]`: was Erweiterungen über `ctx.ui.register_host_requirement()` für diesen
  Server melden (nach `tags` und `os_families` des Servers gefiltert, sortiert nach `order`). `unix_group` ist `null`,
  wenn der Name nicht `^[a-z_][a-z0-9_-]{0,31}$` entspricht.
- `POST /hosts/{id}/credentials/generate-key` `{username = "lattice", port = 22}` (`hosts.write`, `201`) erzeugt einen
  ed25519-Schlüssel nur für diesen Server (Kommentar `nodvard@<Kurzname>`). Der Standard `username = "lattice"` bleibt
  aus Kompatibilitätsgründen (ältere Aufrufer schicken oft keinen Namen); die Oberfläche schlägt `nodvard` vor und
  schickt den Namen immer mit. Antwort: `{credential, public_key,
  fingerprint}` – **der private Schlüssel liegt nur im Tresor** und steht nie in einer Antwort (auch keiner `422`),
  im Protokoll oder im Log. `is_default` ist nur dann `true`, wenn der Server noch keinen SSH-Zugang (Schlüssel oder
  Passwort) hat; sonst bleibt der bisherige Standard, bis `make-default` ihn umstellt. `username` und `port` werden wie
  bei `POST .../credentials` geprüft. Höchstens 10 Schlüssel je 5 Minuten und Nutzer, sonst `429` mit `Retry-After`.
  Protokoll `host.key_generated` (`credential_id`, `username`, `port`, `fingerprint`).
- `GET /hosts/{id}/credentials/{cid}/setup?sudo=false&groups=` (`hosts.write`) → `{username, public_key, fingerprint,
  one_liner, script, notes, groups, sudo}`: der Befehl, den man auf dem Server ausführt (als root direkt, sonst über
  `sudo`; `script` ist dasselbe lesbar). Der öffentliche Schlüssel wird serverseitig aus dem Tresor abgeleitet, mit dem
  Kommentar `nodvard@<Kurzname>` (nie dem im Schlüssel eingetragenen). Der Befehl legt den Benutzer an (falls es ihn
  nicht gibt, ohne Passwort-Anmeldung), trägt den Schlüssel als `restrict,pty …` ein (einen schon eingetragenen erkennt
  er an Art und Schlüsseltext, auch mit älterem Kommentar `lattice@…`) und richtet auf Wunsch `sudo` ohne Passwort
  (`sudo=true`, Datei `/etc/sudoers.d/nodvard-<benutzer>`, geprüft mit `visudo`) und
  Gruppen (`groups=docker` oder `groups=a,b`) ein. Gruppen werden nur eingetragen, wenn Erweiterungen sie für diesen
  Server verlangen (`unix_group`; privilegierte Gruppen – `root`, `sudo`, `wheel`, `admin`, `shadow`, `disk`, `adm`,
  `staff`, `lxd`, `libvirt`, `kvm` – ignoriert der Kern und protokolliert das im Log; `docker` ist bewusst erlaubt und
  bedeutet praktisch root); für `root` gibt es weder Gruppen noch `sudo`. Bei einem Benutzer außer `root` läuft alles,
  was `~/.ssh` und `authorized_keys` anfasst, **als dieser Benutzer** (`runuser`, sonst `su`), nie als root mit `chown`;
  ist `~/.ssh` oder `authorized_keys` ein Link, bricht der Befehl mit `FEHLER` ab, statt ihm zu folgen. Bei `root`
  (z. B. Proxmox, wo `authorized_keys` ein Link ins Cluster-Dateisystem ist) bleibt der Link unangetastet. Eine ältere
  Regel `/etc/sudoers.d/lattice-<benutzer>` entfernt der Befehl erst, wenn die neue eingetragen und die ganze
  Konfiguration geprüft ist, und nur, wenn sie Byte für Byte der Regel entspricht, die er früher selbst angelegt hat;
  sonst (auch als Link) bleibt sie mit Hinweis stehen. Lehnt `visudo` die sudo-Regel ab oder wird sie zurückgenommen,
  endet der Befehl mit Fehler statt „Fertig“, ebenso, wenn er die alte Regel wiederherstellen muss. Jeder Wert im Befehl wird streng
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
  neu prüfen). Wird ein alter Zugang gelöscht, steht in `notice` ein Hinweis, sonst
  `null`: bei einem Schlüssel, dass er in `authorized_keys` auf dem Server stehen bleibt (Nodvard Deck entfernt ihn dort
  nicht), bei einem Passwort, dass sich auf dem Server nichts ändert. Hat `POST /hosts/{id}/check` mit dem neuen Zugang
  in den letzten 10 Minuten die Anmeldung geschafft (Adresse und Port seitdem gleich), übernimmt `login_ok_at` den
  Zeitpunkt dieser Prüfung; sonst ist es `null` bis zur nächsten gelungenen Anmeldung. Ist der Zugang schon Standard,
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
  `DELETE .../known-hosts/{key_type}`, dann neu prüfen und bestätigen). Geprüft wird nur die gespeicherte Adresse und der Port des
  Zugangs. Die Prüfung nutzt eine eigene Verbindung (nicht aus dem Pool); nach gelungener Anmeldung werden die gepoolten
  Verbindungen des Servers ausgemustert (neue Gruppenrechte gelten, laufende Terminals nicht abgeschnitten) und
  `status`/`last_seen_at` des Servers aktualisiert (`down`: nicht erreichbar, `unknown`: Schlüsselproblem). Mit dem
  Standard-Zugang setzt eine gelungene Anmeldung `login_ok_at`, eine abgelehnte löscht es; ein geänderter
  Server-Schlüssel löscht es immer, ein nicht erreichbarer Server nie. Die Prüfung eines anderen Zugangs ändert daran
  sonst nichts. Texte sind
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
- `DELETE /hosts/{id}/known-hosts/{key_type}` (`hosts.write`, `204`; kein gemerkter Schlüssel dieses Typs → `404`)
  vergisst einen gemerkten Server-Schlüssel und schließt offene SSH-Verbindungen des Servers. Danach merkt Nodvard Deck
  für diesen Server keinen Schlüssel mehr von allein (Terminal, Jobs, Status), egal wie `ssh.confirm_new_host_keys`
  oder `NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS` steht: Erst `POST /hosts/{id}/known-hosts` nach einer neuen Prüfung
  hebt das auf (oder `DELETE /hosts/{id}`). Protokoll `host.known_key_forgotten` (`key_type`, `fingerprint`).
- `PATCH /host-groups/{id}` (`name`, `description`; Name vergeben → `409`, unbekannt → `404`) und
  `DELETE /host-groups/{id}` (löscht nur die Gruppe und ihre Zuordnungen, nie Server). `POST
  /host-groups/{id}/members/{host_id}` ist idempotent und liefert `404` für eine unbekannte Gruppe oder einen
  unbekannten Server.
- Protokoll-Einträge (`target_type` `host`, bei Gruppen `host_group`): `host.created`, `host.updated` (`changed` mit
  `from`/`to` je Feld; hat eine neue Adresse SSH-Passwörter gelöscht, zusätzlich `password_credentials_removed` mit
  deren Anzahl, auch beim Abgleich einer Erweiterung, dann als Eintrag der Erweiterung), `host.deleted`,
  `host.credential_added`, `host.credential_deleted`, `host.key_generated`,
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
| `ssh.confirm_new_host_keys` | `true` oder `false` | `false`; neue Installationen `true` (gesetzt beim ersten Konto) |

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
- `ssh.confirm_new_host_keys`: `true` = Terminal, Jobs und Status merken den Schlüssel eines Servers, für den noch
  keiner gemerkt ist, nicht still; die Verbindung scheitert, bevor Anmeldedaten gesendet werden, bis der Fingerabdruck
  über `POST /hosts/{id}/check` und `POST /hosts/{id}/known-hosts` bestätigt ist. `false` = der Schlüssel wird beim
  ersten Verbinden gemerkt. `POST /auth/bootstrap` (erstes Konto) speichert `true`; Installationen von davor haben
  keinen Wert, es gilt `false`. Gemerkte Schlüssel betrifft das nie; nach `DELETE /hosts/{id}/known-hosts/{key_type}`
  ist die Bestätigung für diesen Server immer nötig. Ist `NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS` gesetzt
  (`true`/`false`, leer zählt als nicht gesetzt), meldet `GET /settings` deren Wert, und `PUT` antwortet mit `409`.
- Alle Änderungen dieser Tabelle ab `system.timezone` stehen mit altem und neuem Wert im Protokoll
  (`system.settings.changed`, `target_id` = Schlüssel). Ebenso die vier Schlüssel des Freigabe-Gates
  (`autonomy.mode`, `autonomy.max_risk`, `security.deny_patterns`, `maintenance.windows`). Eine Liste, die als JSON
  länger als 8000 Zeichen ist, steht dort nur mit ihrer Länge (`{"truncated": true, "count": n}`).

### System und Sicherungen

Alle Pfade unter `/system`. **Ansehen** braucht `system.read` (Rolle `admin` hat es über `*`). Alles, was
ändert oder Daten herausgibt, darf **nur der Owner** (`require_owner`: die Rolle allein reicht nicht, auch `*`
nicht, sonst `403`). Mit **[PW]** markierte Aufrufe verlangen zusätzlich `current_password` im Body: fehlt es →
`403`, falsch → `400`, zu oft falsch → `429` (dieselbe Drosselung wie bei `/me/password`). Passwörter stehen nie
in der Adresse und nie im Protokoll. Mit **[2FA]** markierte Aufrufe verlangen bei eingeschalteter Zwei-Faktor-Anmeldung
zusätzlich `totp_code`, den aktuellen sechsstelligen Code aus der App (Wiederherstellungs-Codes gelten hier nicht): fehlt er →
`403` (`code` `totp_missing`), falsch → `400` (`code` `totp_wrong`), schon benutzt → `400` (`code` `totp_used`), zu viele falsche →
`429` mit `Retry-After` (siehe „Zwei-Faktor-Code bei Update und Rückweg“ unten). Ohne Zwei-Faktor wird `totp_code` nicht beachtet.

| Methode, Pfad | Recht | Zweck |
|---|---|---|
| `GET /system/info` | `system.read` | `version`, `build` (genaue Version des Release-Images bzw. `NODVARD_DECK_BUILD`, sonst `null`), `image` (Herkunft ohne Tag, aus der Datei `/app/image-info.json` im Image bzw. `NODVARD_DECK_IMAGE`; `null` bei einem selbst gebauten Image), `timezone`, `data_dir`, `data_free_bytes`, `database` (`sqlite`/`postgresql`), `updater_available` (ein Update-Helfer ist eingerichtet und antwortet; dieselbe Quelle wie `present` in `GET /system/updates/helper`), `pre_update_copies[]` (die neuesten, höchstens drei **Kopien der Datenbank von vor einer Migration**, neueste zuerst: `name`, `created_at`, `from_version`, `to_version`, `size`; ohne Pfade; leer, solange es noch keine gibt) |
| `GET /system/updates` | `system.read` | Stand von „Nach Updates suchen“, **ohne** Anfrage ins Netz (aus `<Datenordner>/update_check.json`): `current` (laufende Version: beim offiziellen Image dessen genaue Version aus `/app/image-info.json`, auch eine Vorabversion wie `0.6.0-rc1`; sonst `version`), `latest` (neueste Version im Kanal laut letzter gelungener Prüfung; `null`, wenn noch nie geprüft oder geprüft, aber keine passende Version gefunden – dann ist `checked_at` gesetzt), `latest_digest` (sha256 des Manifests, wenn bekannt), `available` (`latest` neuer als `current`, Semver), `channel` (`stable`/`beta`), `enabled` (tägliche Prüfung an), `checked_at` (letzte gelungene Prüfung), `attempted_at` (letzter Versuch), `source` (`cache`; `offline`, wenn der letzte Versuch scheiterte), `error` (dann „Konnte nicht prüfen (offline?).“; `latest`, `checked_at`, `attempted_at` und `error` gelten nur für den eingestellten Kanal), `official_image` (das Image stammt laut `image` genau aus `ghcr.io/nodvard/deck`), `image`, `official_image_name`, `helper` (wie `updater_available` in `GET /system/info`), `release_notes_url` (`https://github.com/nodvard/deck/blob/v<latest>/CHANGELOG.md`) |
| `POST /system/updates/check` | `system.read` | Jetzt nachsehen; Antwort wie `GET`, mit `source: live`. Scheitert die Abfrage (offline, `401`/`429`/`5xx` der Registry, kaputtes JSON, Zeitlimit), kommt **kein** Fehlercode, sondern der letzte Stand mit `source: offline` und `error`. Höchstens **einmal pro Minute** für alle zusammen, sonst `429` „Gerade erst nachgesehen …“ mit `Retry-After` |
| `GET /system/updates/helper` | `system.read` | Zustand des Update-Helfers, ohne Anfrage ins Netz (siehe „Update-Helfer“ unten): `present` (eingerichtet und antwortet, Herzschlag höchstens 90 s alt), `reason` (warum nicht: `missing` nicht eingerichtet, `stale` antwortet nicht, `unsafe` Kanal nicht sicher, `proto` andere Protokollversion, `invalid` unlesbarer Status; sonst `null`), `ready` (könnte jetzt ein Update einspielen), `ready_reason` (fester Bezeichner des Helfers: warum er nicht bereit ist, oder ein Hinweis wie `channel_cluttered`; `finishing`: der letzte Vorgang ist entschieden, der Helfer räumt nur noch auf, `ready` ist dann `false` und `busy` `null`), `state` (`idle`/`busy`/`unsafe`/`error`), `helper_version`, `heartbeat_at`, `target` (`current_version`, `floating_tag` = `latest` oder `X.Y`, `null` bei fester Version; `pinned`: die Version steht fest in der Compose-Datei, `X.Y.Z` oder Digest), `busy` (`id`, `action` `update`/`rollback`, `step`, `since`), `previous` (Rückweg: `version`, `until`; nur solange er gilt; dazu `data_revert`: `true` = die laufende Version hat beim Start die Datenbank umgebaut, beim Rückweg geht alles seit `data_since` verloren und `accept_data_loss` ist Pflicht, `false` = die Daten bleiben, `null` = unklar, der Rückweg wird dann mit `data_unclear` abgelehnt – dieselbe Entscheidung wie bei `POST /system/updates/rollback`; `data_since`: Unix-Zeit des Umbaus, nur bei `data_revert: true`), `last_result` (`id`, `action`, `from`, `to`, `outcome`, `code`, `finished_at`), `pending` (eigene Anforderung ohne Ergebnis, höchstens 30 Minuten alt: `id`, `action`, `to`, `at`). Zeiten als Unix-Zeit in Sekunden, Gründe und Codes nur feste Bezeichner, nie Freitext. Ist `present` `false`, ist `ready` `false` und alle übrigen Felder außer `reason` sind `null`; nur `last_result` (bei `stale`) und `pending` können trotzdem gesetzt sein. Hält nebenbei neue Ergebnisse des Helfers im Protokoll fest |
| `POST /system/updates/apply` | Owner [PW] [2FA] | `{version, current_password, totp_code?}`: Update auf `version` beim Update-Helfer anfordern → `202` `{request_id, action: "update", from, to, data_revert: false}` (`Cache-Control: no-store`); das Ergebnis steht danach in `GET /system/updates/helper`. `version` muss die neueste gefundene sein (`latest` aus `GET /system/updates`) und neuer als die laufende. `409`: Helfer fehlt oder antwortet nicht (`helper_missing`), räumt noch auf (`helper_finishing`), ist beschäftigt (`helper_busy`) oder nicht bereit (`helper_not_ready`), Kanal nicht sicher (`channel_unsafe`), schon eine Anforderung offen (`request_pending`), nicht das offizielle Image (`not_official_image`), Version in der Compose-Datei fest eingetragen (`pinned`); `422`: keine gültige Version (`bad_version`), Vorabversion (`prerelease`), nicht die neueste gefundene (`not_latest`), nicht neuer als die laufende (`not_newer`), passt nicht zum Tag der Compose-Datei (`tag_mismatch`); `429` (`rate_limited`) mit `Retry-After` |
| `POST /system/updates/rollback` | Owner [PW] [2FA] | `{current_password, accept_data_loss?, totp_code?}`: Rückweg auf die Version vor dem letzten Update (`previous` in `GET /system/updates/helper`) anfordern → `202` wie bei `apply`, mit `action: "rollback"`. Hat die laufende Version beim Start die Datenbank umgebaut, gehen die Daten mit zurück (`data_revert: true`: alles seit dem Update geht für das Dashboard verloren, der ersetzte Stand liegt danach im Datenordner unter `restore/replaced-…`): dann ist `accept_data_loss: true` Pflicht (sonst `422` `accept_data_loss`), und vor der Anforderung wird die Vormerkung `.boot/rollback.json` geschrieben; scheitert danach das Schreiben der Anforderung oder endet der Rückweg mit `refused` oder `aborted`, wird sie wieder gelöscht. `409` wie bei `apply` außer `not_official_image` und `pinned`, dazu `no_previous` (kein Rückweg, oder er gilt nicht mehr), `data_unclear` (die Datenbank wurde umgebaut, aber der Eintrag passt nicht genau zu diesem Rückweg oder die Kopie fehlt), `marker_failed` (die Vormerkung ließ sich nicht speichern, nichts angefordert); `429` wie bei `apply` |
| `GET /system/openapi.json` | `system.read` | Das vollständige OpenAPI-Dokument (alle Endpunkte samt mitgelieferter Erweiterungen); ersetzt das öffentliche `/openapi.json`, das es nur im Entwicklungsmodus oder mit `NODVARD_DECK_API_DOCS=1` gibt |
| `GET /system/backups` | `system.read` | `config`, `key` (`key_id`, `created_at` – nie Schlüssel oder Passwort), `target` (`dir`, `default_dir`, `external_root`, `external_available`, `same_storage_as_data`, `free_bytes`, `error`), `backups[]` (`name`, `size`, `created_at`, `app_version`, `key_id`, `mode`, `status` `ok`/`ungeprueft`/`beschaedigt`, `checked_at`, `check_ok`, `key_current`), `last_run`, `running`, `sqlite`, `limits` |
| `PUT /system/backups/config` | Owner, [PW] nur bei gefährlicher Änderung | `{enabled, schedule, keep, dir, include_runs, current_password?}`; `current_password` ist **optional** und nur nötig, wenn `keep` kleiner wird als bisher, `enabled` von an auf aus geht oder `dir` sich ändert (sonst `403` mit Text, welche Änderung das Passwort braucht; falsch `400`, zu oft `429` wie bei den anderen [PW]-Endpunkten; geprüft **vor** der Eingabeprüfung, ohne dass ein Ordner angelegt wird); `keep` 1–60, `schedule` Cron, `dir` nur `<Datenordner>/backups` oder `/backups` bzw. darunter (absolut, kein `..`, kein Symlink, `realpath` gleich, beschreibbar) → sonst `422`; `enabled` ohne Sicherungspasswort → `409`. Antwort wie `GET /system/backups` |
| `PUT /system/backups/key` | Owner [PW] | `{current_password, password}` (≥ 12 Zeichen, sonst `422`). Antwort **einmalig** `{key_id, recipient, recovery_key}` (`Cache-Control: no-store`) |
| `POST /system/backups/run` | Owner | Jetzt sichern, als Kern-Job `system-backup` im Hintergrund → `202`; läuft schon eine Sicherung oder fehlt der Schlüssel → `409` |
| `POST /system/backups/download` | Owner [PW] [2FA] | `{current_password, mode: "schluessel"\|"passwort", password?, totp_code?}` (der Code, weil die Datei alle Schlüssel enthält, auch den der Zwei-Faktor-Anmeldung) → `202` mit Ticket `{ticket, job_id, status, filename, size, error, expires_in, url}`; baut die Datei im Hintergrund (kein Job: dessen Protokoll enthielte die Parameter). `409` bei laufender Sicherung, fehlendem Schlüssel (`schluessel`) oder ohne SQLite-Datei; `507` bei zu wenig Platz |
| `GET /system/backups/download-jobs/{job_id}` | Owner, nur eigener Download | Stand `building`/`ready`/`failed`, Antwort wie beim Ticket (`404` für fremde, unbekannte oder schon abgeholte Downloads). `job_id` ist eine eigene Zufalls-ID und taugt **nicht** zum Herunterladen: die Oberfläche fragt sie jede Sekunde ab, sie steht also in der Adresse (und im Zugriffsprotokoll), das Ticket nicht |
| `GET /system/backups/download/{ticket}/status` | Owner, nur eigenes Ticket | **Veraltet** (`deprecated`), bleibt als Übergang: dasselbe über das Ticket in der Adresse. Neu: `download-jobs/{job_id}` |
| `GET /system/backups/download/{ticket}` | Ticket | Liefert die Datei als normalen Download (Streaming). Gilt **einmal**, 5 Minuten ab Fertigstellung, nur solange der Nutzer noch aktiver Owner ist; sonst `404`, während des Baus `409`. Eine eigens gebaute Datei wird danach gelöscht. Ein Filter am uvicorn-Zugriffsprotokoll (`core/log_filters.py`) ersetzt das Ticket in Adressen unter `…/system/backups/download/` durch `…` |
| `POST /system/backups/{name}/ticket` | Owner [PW] [2FA] | `{current_password, totp_code?}`: Ticket für eine vorhandene automatische Sicherung (die Datei bleibt liegen) |
| `POST /system/backups/{name}/verify` | Owner | sha256 der Datei gegen die Prüfsummen-Datei `<name>.json` (ohne Passwort) → `{ok, detail}` |
| `DELETE /system/backups/{name}` | Owner [PW] | Body `{current_password}`; nur `nodvard-deck-sicherung-*.ndbak` mit eigenem Kopf, die `.json` daneben verschwindet mit → `204`; unbekannt → `404` |

- **Datei** `nodvard-deck-sicherung-YYYYMMDD-HHMMSS.ndbak` (Ortszeit der eingestellten Zone): Zeile 1
  `NODVARD-DECK-BACKUP/1`, Zeile 2 JSON-Kopf (`format`, `created_at`, `app_version`, `mode`, bei `schluessel`
  zusätzlich `key_id` und `kdf` = Argon2id-Parameter mit Salz), danach eine normale age-Datei mit einem tar.gz:
  `db/lattice.db` (Online-Kopie, `integrity_check`), `files/master.key`, `files/vault_keyring.json`,
  `files/jwt_secret.key` (nur ohne `NODVARD_DECK_JWT_SECRET`), `files/ext/**`, `files/branding/**`, optional
  `files/runs/**`, zuletzt `manifest.json` (Kopie des Kopfes, Alembic-Köpfe, Erweiterungen, sha256 jeder Datei).
  Nicht enthalten: `*-wal`/`-shm`/`-journal`, `setup_code.txt`, `backups/`, `restore/`, `.boot/`, Temp-Dateien,
  Symlinks, der Verlauf `metrics.db` und Git-Einstellungen in `ext/`, `branding/`, `runs/` (`core/backup/format.py`):
  `.gitattributes`, `.gitmodules`, `.gitconfig`, eine Datei `.git` und unter `.git/` alles außer Objekten und Packs
  (`objects/`), `refs/`, `HEAD`, `index` und `packed-refs` – also weder `config` noch `hooks/`, `info/`, `logs/`. Das
  gilt unabhängig von Groß- und Kleinschreibung, auch für Windows-Formen wie `.git.` oder den Kurznamen `GIT~1`;
  `.gitignore` bleibt, der Git-Verlauf selbst ist weiter in der Sicherung. Übersprungene Symlinks werden nicht verschwiegen: `last_run.warnings`
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
  Lesen ab). User-Agent `nodvard-deck`, ohne Version. Ein Proxy aus der Umgebung (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`) gilt hier, anders als bei Erweiterungen (`ctx.http`). Es läuft immer nur eine Prüfung zur Zeit. Eine Antwort ohne Tag-Liste gilt als
  Fehler (der letzte Stand bleibt); eine vollständige Antwort ohne passende Version ist ein Ergebnis (`latest: null`). Täglich als
  Kern-Job `system-update-check` (Schalter `system.update_check.enabled`), dazu der Knopf „Jetzt suchen“. Das Repository steht als eine Konstante im Kern
  (`core.updates.REPOSITORY`). Das Release-Image trägt Herkunft und Version als Datei `/app/image-info.json` (Build-Argumente
  `IMAGE` und `VERSION` in `release.yml`/`deploy/Dockerfile`, `nodvard_deck.image_info`; keine Umgebungsvariable, die beim Neuanlegen
  eines Containers mitwandern könnte). Daran erkennt die Oberfläche ein selbst gebautes Image. Vorrang für `image` und `build`
  in `GET /system/info`: nicht leere Variable `NODVARD_DECK_IMAGE`/`NODVARD_DECK_BUILD` (alt `LATTICE_*`), dann die Datei, sonst `null`.
  `current` der Update-Suche (und `from_version`/`to_version` der Kopien vor Updates) nimmt beim offiziellen Image dagegen zuerst die
  Version aus der Datei, dann `build` (jeweils nur, wenn es eine Version ist), sonst `version`: eine veraltete Variable verfälscht den
  Vergleich nicht.
- **Update-Helfer** (`services/update_helper.py`, `core/updater_client.py`): ein eigenes kleines Programm neben dem Dashboard, das
  Update und Rückweg selbst einspielt. Beide teilen sich nur einen Ordner (`/app/updater`, Einstellung `updater_dir`, nur für Tests
  ändern), ein eigenes Volume, das der Helfer einrichtet und das root gehört: `status.json` schreibt nur der Helfer, das Dashboard
  legt Anforderungen (Aktion und Version) unter `requests/` ab. Fehlt der Ordner, gibt es keinen Helfer (`present: false`,
  `reason: missing`). Eingeschaltet wird der Helfer mit einer eigenen Compose-Datei
  ([deploy/README „Update-Helfer“](../deploy/README.md#update-helfer)). Verbindlich entscheidet immer der Helfer;
  was das Dashboard vorher prüft, sorgt für eine klare Antwort. Höchstens **eine Anforderung je 10 Minuten**, Update und Rückweg
  zusammen, sonst `429` „Gerade erst angefordert …“ mit `Retry-After`; was nicht angefordert wurde (Kanal nicht sicher, Vormerkung
  nicht gespeichert) oder der Helfer abgelehnt hat (`refused`), zählt nicht. Ablehnungen von `apply`/`rollback` tragen neben
  `detail` einen festen `code` (siehe Tabelle, dazu `403` `not_owner`, `password_missing` und `totp_missing`), bei `helper_missing` und
  `channel_unsafe` auch `helper_reason`, bei `helper_not_ready`, wenn der Helfer einen Grund nennt. Ein falsches Passwort antwortet
  wie bei den anderen [PW]-Endpunkten nur mit `detail`, ein falscher oder schon benutzter Zwei-Faktor-Code mit `code` `totp_wrong`
  bzw. `totp_used`.
- **Zwei-Faktor-Code bei Update und Rückweg** ([2FA]): dieselben Bausteine wie bei `POST /auth/mfa`. Ein Code gilt je Konto nur
  einmal, auch quer zwischen Anmeldung und Bestätigung. Falsche Codes zählen gegen dieselbe Grenze je Konto (10 in 15 Minuten,
  20 in 24 Stunden, Anmeldung und Bestätigung zusammen); danach `429` auch mit richtigem Code. Beginnt die Sperre bei einer
  Bestätigung, steht sie als `auth.password_check_locked` im Protokoll, dazu kommt die Meldung „Zwei-Faktor-Code wird
  durchprobiert“. Jeder falsche Code, der nicht schon an der Sperre scheitert, steht als `auth.totp_check_failed` im
  Protokoll (`detail.aktion` `update_einspielen` bzw. `update_zurueck`, bei einem schon benutzten Code `replayed: true`; nie
  der Code). Während einer Sperre wird nicht mehr geprüft: Dann gibt es `429` und keinen `auth.totp_check_failed`-Eintrag.
  Die Ablehnung steht trotzdem als `system.update.request_refused` bzw. `system.update.rollback_refused` mit `detail.code`
  `confirm_429` im Protokoll. Die Grenze je Konto gilt auch über einen Neustart hinweg (siehe `POST /auth/mfa`), ebenso die für
  falsche Wiederherstellungs-Codes bei Bestätigungen (10 in 5 Minuten je Konto). Nach dem Einspielen einer Sicherung zählt
  deren Protokoll. Dasselbe gilt für Sicherung laden und einspielen ([2FA] in der Tabelle oben).
- Protokoll des Update-Helfers: `system.update.requested` bzw. `system.update.rollback_requested` (`request_id`, `from`, `to`, beim
  Rückweg auch `copy` und `data_revert`); jede Ablehnung als `system.update.request_refused` bzw. `system.update.rollback_refused`
  mit `detail.code` (bei der Bestätigung `outcome` `denied` und `not_owner`, `password_missing` oder `confirm_<Statuscode>`, sonst
  `failure` und der Code der Antwort). Ergebnisse des Helfers stehen **genau einmal je Anforderung** als `system.update.<outcome>`
  im Protokoll (Akteur `system`/`updater`, `target_id` = Anforderung, `outcome` `success` bei `applied` und `reverted`, sonst
  `failure`): `applied` (Update eingespielt), `reverted` (Rückweg eingespielt), `rolled_back` (hat nicht geklappt, die
  Ausgangsversion läuft weiter bzw. wieder), `refused` (vom Helfer abgelehnt), `aborted` (abgebrochen, bevor die neue Version
  gestartet wurde; schon Geändertes ist zurückgebaut), `failed_manual` (die vorige Version ließ sich nicht wieder starten, bitte
  von Hand nachsehen), `external_change` (von außen wurde etwas am Container geändert). Festgehalten wird beim Start (ist danach
  noch etwas offen, alle 30 s, höchstens 30 Minuten), bei `GET /system/updates/helper` und vor jeder neuen Anforderung; welche
  schon eingetragen sind, steht in `<Datenordner>/updater_seen.json` (außerhalb der Datenbank, übersteht also das Zurückspielen
  einer Kopie). Bei `applied`, `reverted`, `rolled_back`, `failed_manual` und `external_change` kommt zusätzlich eine Meldung
  (`payload.path` = `/settings/system`).
- Den Kern-Job `system-backup` steuert nur diese Karte: `PATCH`/`DELETE /jobs/{id}` auf ihn → `409`
  (sonst könnte ein Admin über `jobs.write` die automatischen Sicherungen still abschalten).

**Wiederherstellen** (Owner, `core/backup/restore.py`, `boot.py`): Hochladen → Prüfen → Vormerken → Neustart; eingespielt
wird beim Start, **vor** der Migration. Der Zwischenstand liegt unter `<Datenordner>/restore/<id>/`; es gibt höchstens einen.

| Endpunkt | Recht | Zweck |
|---|---|---|
| `GET /system/restore/status` | `system.read` | `{pending, staged, result, replaced, limits, busy}`: Vormerkung, Zwischenstand (**nur der Owner**), Ergebnis des letzten Einspielens (ohne IP-Adressen), alter Stand (`replaced`: Name, Größe, `expires_at` = wann er von selbst gelöscht wird), Grenzen |
| `PUT /system/restore/upload` | Owner [PW im Kopf] | Roher Strom (`Content-Type: application/octet-stream`, **kein** multipart; sonst `415`). Kopf `X-Confirm-Password`: Anmeldepasswort **prozentkodiert** (UTF-8, `encodeURIComponent`; Kopfzeilen kennen kein Unicode). Owner und Passwort werden geprüft, **bevor** der Body gelesen wird (`403` fehlt, `400` falsch, `429` zu oft). `Content-Length` über `restore_max_upload_bytes` (4 GiB, `NODVARD_DECK_RESTORE_MAX_UPLOAD_BYTES`) → `413` ohne Lesen, ebenso bei Überschreitung mitten im Strom; zu wenig Platz → `507` vor dem Lesen; läuft schon ein Upload/eine Prüfung → `409`; ist etwas vorgemerkt → `409`; keine Sicherung (Kopf) → `422`. `201` `{id, state: "uploaded", size, header: {mode, created_at, app_version}, expires_in}` |
| `POST /system/restore/{id}/inspect` | Owner | `{password}` **oder** `{recovery_key}` (genau eines, sonst `422`). Entschlüsselt in den Staging-Ordner und prüft alles. `200` `{id, state: "ready", summary}`; `summary`: `created_at`, `app_version`, `instance_id`, `mode`, `owner_name`, `users`, `hosts`, `extensions[]`, `includes`, `warnings[]` (u. a. andere Installation, ältere/neuere Version, fehlende Erweiterungen, kein Konto in der Sicherung, übersprungene Git-Einstellungen). Falsches Geheimnis `400` (Upload bleibt, noch ein Versuch), zu wenig Platz `507` (bleibt), `409` bei laufender Arbeit; jeder andere Fehler (beschädigt, zu neu, Erweiterung fehlt, zu groß `413`, nicht einspielbar) `422` und der Zwischenstand ist gelöscht; unbekannte oder fremde `id` `404`. Entschlüsselt wird immer nur **eine** Sicherung zugleich (teilt die Sperre mit den Sicherungen) |
| `POST /system/restore/{id}/schedule` | Owner [PW] [2FA] | `{current_password, sign_out_all: true, totp_code?}` → `200` Vormerkung `{id, source, scheduled_at, expires_in, sign_out_all, backup}`; `restore/pending.json`. Vorher nicht geprüft oder schon etwas vorgemerkt → `409`. Gilt eine Stunde |
| `DELETE /system/restore/pending` | Owner | Verwirft Vormerkung **und** jeden Zwischenstand → `204` |
| `DELETE /system/restore/replaced` | Owner [PW] | Body `{current_password}`; löscht den alten Stand (`restore/replaced-…`, enthält alte Konten und Schlüssel) → `204` |
| `POST /system/restart` | Owner [PW] | Body `{current_password}`. Beendet den Prozess nach der Antwort mit dem Rückgabewert **75**; der Container startet ihn neu (Regel `restart: unless-stopped`; ohne Regel bleibt der Dienst aus). → `202` `{restarting, exit_code: 75}` |
| `PUT /auth/bootstrap/restore/upload`, `POST /auth/bootstrap/restore/{id}/inspect`, `POST …/{id}/schedule`, `DELETE …/restore/pending` | Einrichtungscode | Wie oben, aber ohne Konto und ohne Passwort-Kopf: **nur ohne Konto** (sonst `409`), Kopf `X-Setup-Code` bei jedem Aufruf. `schedule` nimmt `{sign_out_all}` |
| `POST /auth/bootstrap/restart` | Einrichtungscode | wie `POST /system/restart`; nur wenn etwas vorgemerkt ist (sonst `409`) |

- **Härtung beim Entpacken:** tar nur als Strom (`tarfile.data_filter` pro Eintrag, jede Datei schreiben wir selbst mit
  `O_EXCL|O_NOFOLLOW`), Namen nur aus einer Erlaubnisliste (`manifest.json`, `db/lattice.db`,
  `files/{master.key,vault_keyring.json,jwt_secret.key}`, `files/ext/**`, `files/branding/**`, `files/runs/**`),
  nur normale Dateien und Ordner (keine Links, Hardlinks, Geräte, FIFOs), jede Datei einmal, das Manifest zuletzt.
  Git-Einstellungen darin (dieselbe Regel wie unter „Nicht enthalten“ oben, etwa aus älteren Sicherungen) werden nicht
  abgelehnt, sondern **übersprungen**: gelesen und per sha256 gegen das Manifest geprüft, aber nicht geschrieben. Ihre Zahl
  steht in `summary.warnings` („3 Git-Einstellungen aus der Sicherung wurden nicht übernommen, der Verlauf bleibt.“), bis zu
  fünf Namen im Log (`restore_skipped_vcs_entries`).
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
  von der Notseite oder von `POST /system/updates/rollback`) liegt vor: dann auch nach einem guten Start; die neueren Daten bleiben unter `restore/replaced-…` (30 Tage). Sonst: Notseite.
- **Fehler führen zur Notseite**, nie zu einer Neustart-Schleife: `boot` gibt 1 zurück, `deploy/entrypoint.sh` startet `python -m nodvard_deck.rescue`; fällt die aus, wartet
  der Container (`exec sleep`). Das gilt auch für einen unlesbaren Journal-Eintrag oder einen gescheiterten Rückweg einer Wiederherstellung (`rollback_failed`).

**Die Notseite** (`backend/src/nodvard_deck/rescue.py`, **nur Standardbibliothek**, ein Test prüft das über den Syntaxbaum und startet sie ohne installierte Pakete) antwortet
auf dem Port der Anwendung:

| Methode, Pfad | Antwort |
|---|---|
| `GET /api/v1/health` | **`503`** `{"status":"rescue"}` (nie `ok`: Healthcheck, `scripts/deploy_pi.sh` und ein Helfer erkennen so, dass etwas nicht stimmt). Jeder andere Pfad unter `/api/` ebenso `503` mit `{"status":"rescue","detail":…}` |
| `GET <alles andere>` | Die Seite (`503`, Deutsch, ohne fremde Dateien). **Ohne Code** nur Allgemeines |
| `POST /rescue/unlock` | Notfallcode (`.boot/rescue_code.txt`, wie der Einrichtungscode; steht als Banner im Protokoll) → `303` + Cookie `rescue_session` (HttpOnly, SameSite=Strict, 15 Minuten). Je Adresse 5 Fehlversuche, insgesamt 25 in 10 Minuten, dann `429` (mit `Retry-After`, Antwort erst nach 0,5 s) für jeden weiteren **falschen** Code. Der richtige Code geht immer durch, auch während einer Sperre; Versuche während einer Sperre verlängern sie nicht |
| `GET /rescue/status`, `GET /rescue/log` | mit Cookie: Grund und bereinigtes Protokoll; ohne: nur `{"status":"rescue","unlocked":false}` bzw. `401` |
| `POST /rescue/retry` | mit Cookie: Prozessende mit 75, der Container startet neu (Restart-Regel) und `boot` läuft noch einmal |
| `POST /rescue/rollback` | mit Cookie, nur wenn `boot` eine brauchbare Kopie gefunden hat (sonst `409`): „Stand vor dem Update wiederherstellen“ vormerken, dann wie `retry`. Die Kopie kommt aus dem Zustand, nie aus der Anfrage |
| `POST /rescue/lock` | Sitzung beenden |

POST-Anfragen prüfen `Origin` gegen `Host`. Eine Aktion „Kopie herunterladen“ gibt es **bewusst nicht**: Eine unverschlüsselte Datenbank über HTTP herauszugeben ist
unverantwortlich, und die Verschlüsselung (age/`pyrage`) gehört nicht zur Standardbibliothek. Die Kopie liegt im Datenordner.

Grenzen: Inhalt höchstens 4 KiB (sonst `413`), Anfragezeile und Kopfzeilen zusammen höchstens 32 KiB, dabei unter 100 Kopfzeilen (sonst `431`); 3 s für die Kopfzeilen,
10 s für die ganze Verbindung, danach wird sie geschlossen. Höchstens 64 Verbindungen gleichzeitig, davon 8 je Absender (bei IPv6 je /64-Netz); `127.0.0.1` und `::1`
(Healthcheck im Container) haben 8 eigene Plätze, die andere nicht belegen können.

### Erweiterungen einrichten und testen

`GET`/`PUT /extensions/{id}/settings`, `PUT`/`DELETE /extensions/{id}/secrets` und `POST /extensions/{id}/test` brauchen
`extensions.manage`.

- `GET /extensions` und `GET /extensions/{id}` liefern zusätzlich (nur additiv, alte Felder
  unverändert): `needs_setup` (bool, nur bei eingeschalteten Erweiterungen: Pflichtfelder
  oder Pflicht-Geheimnisse fehlen, oder der letzte Verbindungstest ist fehlgeschlagen),
  `setup_reasons` (Liste deutscher Sätze, leer wenn alles in Ordnung) und `last_test`
  (`{ok, message, at}` oder `null`; wird nach jeder Änderung der Einstellungen oder
  Zugangsdaten gelöscht). Ohne `extensions.manage` fehlt der Text: `last_test` enthält dann nur
  `ok` und `at`, und `setup_reasons` nennt den Fehlschlag ohne Fehlergrund.
- Jeder Eintrag von `GET /extensions` enthält zusätzlich `bundled` (`true` bei Erweiterungen, die mit Nodvard Deck
  ausgeliefert werden, also `source == "bundled"`) und `display_version` (bei mitgelieferten die Programmversion,
  sonst die eigene `version`). `version` bleibt die Version aus `extension.toml`. `/capabilities` ist unverändert.
- `POST /extensions/{id}/test` ruft `health()` der Erweiterung auf (bei
  Benachrichtigungskanälen zusätzlich `test()`); mit `{"mode": "message"}` sendet es stattdessen
  eine Testnachricht über den Kanal (`422`, wenn die Erweiterung keinen hat). Antwort
  `{ok, message, details?}`: `message` ist ein verständlicher deutscher Satz („Keine Antwort
  von 192.168.1.20:8006 – Adresse und Port prüfen.“, „Zugangsdaten abgelehnt …“ bei 401, „Zugriff verweigert – dem
  Konto oder Token fehlt ein Recht auf dem Server …“ bei 403, „Zertifikat wird nicht vertraut …“, „Adresse nicht
  gefunden …“). Aus Fehlern von httpcore, h11, idna und websockets in der Ursachenkette übernimmt der Test keinen
  Text (sie können Teile fremder Antworten enthalten), feste Sätze der Erweiterung stehen ohne den Vorspann
  „Technische Meldung“. `details` bei mehreren Verbindungen
  `[{name, ok, message}]`. Geheimnisse stehen nie im Text. Ein fehlgeschlagener Test ist
  `200` mit `ok: false`. `409`: Erweiterung ausgeschaltet, `404` unbekannt, `429` nach mehr als
  10 Tests pro Minute und Nutzer (mit `Retry-After`). Jeder Test steht im Protokoll
  (`extension.test`, ohne Text).
- `DELETE /extensions/{id}/secrets?label=…` entfernt ein Geheimnis aus `x-secrets` (`422` für
  fremde Labels); Protokoll `extension.secret_removed`.
- `PUT /extensions/{id}/settings` `{values}` übernimmt nur Felder, die das Schema kennt und die
  nicht `x-hidden` sind (die pflegt die Erweiterung selbst). Nicht mitgeschickte Felder bleiben
  wie gespeichert, `null` entfernt einen Wert (dann gilt wieder der `default`); Pflichtfelder
  prüft der Kern am Stand danach (ein `default` zählt als gesetzt). Ändert sich ein Feld, an das
  ein Geheimnis gebunden ist (`x-secret-bound-to`, siehe [02 §1](02-EXTENSION-API.md#1-anatomie-einer-extension)),
  löscht der Kern dieses Geheimnis, auch bei der ersten Adresse (leer → Wert); eine andere
  Schreibweise derselben Adresse zählt nicht als Änderung. Die Antwort nennt die gelöschten
  Labels in `secrets_cleared` (sonst `[]`, bei `GET` immer `[]`), das Protokoll
  `extension.settings` zusätzlich zu `changed` in `detail.secrets_cleared` (nur wenn etwas
  gelöscht wurde).
  Felder mit `x-widget: "schedule"` müssen ein gültiger Cron-Ausdruck sein (fünf Angaben, dieselbe Prüfung wie beim
  Anmelden des Jobs); sonst `422` „„<Feldtitel>“: Der Zeitplan „…“ ist ungültig. Erlaubt sind fünf Angaben: …“, und
  nichts wird gespeichert. Leer oder `null` bleibt erlaubt (dann gilt der Standard der Erweiterung).
- `PUT /extensions/{id}/secrets` antwortet für ein gebundenes Geheimnis mit `409` („Erst die
  Adresse eintragen und die Einstellungen speichern, danach die Zugangsdaten hinterlegen.“),
  solange keines der Felder gespeichert einen Wert oder `default` hat.
- Das alte `POST /ext/proxmox|backups/connections/{name}/token` legt ein Token nur an
  (`409`, wenn schon eines existiert). Zum Setzen **und Ersetzen** dient
  `PUT /extensions/{id}/secrets` mit `{label: "proxmox-token:<name>", value}`; die Oberfläche
  nutzt nur noch diesen Weg. Das Token der Verbindung löschen die Routen der beiden Erweiterungen selbst:
  `POST /ext/proxmox|backups/connections` ein altes unter demselben Namen, `PUT …/connections/{name}` eines, wenn
  `base_url` auf ein anderes Ziel zeigt (Vergleich mit `nodvard_sdk.same_target`, eine andere Schreibweise derselben
  Adresse zählt nicht), und `DELETE …/connections/{name}` das der entfernten Verbindung. Gab es eines, steht
  `extension.secret_removed` im Protokoll.

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

Ein Fehler im Hintergrund endet immer in `failed` mit `result.error` (den Text sieht nur, wer
`hosts.execute` hat, siehe unten); wird das Dashboard
währenddessen beendet, vermerkt der Kern die Aktion als abgebrochen, und beim nächsten
Start setzt er übrig gebliebene `executing`-Zeilen auf `failed`. Das Ereignis
`action.executed` kommt erst, nachdem Ergebnis und Audit-Zeile gespeichert sind.

**Ausgabe nur mit `hosts.execute`.** Ausgabe und Fehlertext einer Ausführung können beliebigen
Text vom Server enthalten (z. B. das Ergebnis eines Shell-Befehls). Die `action`-Ressource
liefert sie deshalb nur an Nutzer mit `hosts.execute` (eingebaut: Bediener, Admin, Owner) – in
`GET /actions`, `GET /actions/{id}`, den Antworten von `approve`, `reject` und `dismiss` und
von `POST /hosts/{id}/actions/{action}`. Alle anderen, z. B. der Betrachter (viewer), sehen
Status, Zeiten und Beteiligte; von `result` bleiben nur `success`, `exit_code` und
`duration_ms`, die übrigen Felder, die das Ergebnis hat, bleiben stehen, aber leer (`output`
und `error` als `null`, `detail` als `{}`). Das Feld `output_hidden` ist dann `true`, wenn
tatsächlich etwas weggelassen wurde; mit `hosts.execute` ist es immer `false`.

**Befehl nur mit `hosts.execute` oder Freigaberecht.** Der `payload` einer Aktion (Shell-Befehl, Skript-Inhalt,
Parameter) kann Zugangsdaten enthalten. Vollständig liefert ihn die `action`-Ressource (in denselben Antworten wie
oben) nur an Nutzer mit `hosts.execute` oder mit `actions.approve:<risiko>` für das Risiko dieser Aktion: Wer eine
Aktion freigibt, muss ihren Befehl sehen. Alle anderen bekommen aus dem `payload` nur Kennungen einer festen Liste
(`host_id`, `vmid`, `node`, `connection`, `job_id`, `script_id`, `unit`, `container`, `project`, `service`, `image`,
`snapname`, `storage`, `minutes`), und auch die nur als Text bis 200 Zeichen, Zahl oder `true`/`false`; Befehl,
Skript-Inhalt und Parameter fehlen. Das Feld `payload_hidden` ist dann `true`, wenn tatsächlich etwas weggelassen
wurde, sonst `false`. Für den Kanal `events` gilt eine strengere Regel (§4).

Ins Protokoll (`action.executed`) schreibt der Kern vom Ergebnis unter `detail.result` nur
`success`, `exit_code`, `duration_ms` und die Länge von Ausgabe und Fehlertext
(`output_length`, `error_length`), nie den Text. Legt das Gate den Grund selbst fest (kein
Executor, Zeitüberschreitung, abgebrochen, beim Start unterbrochen oder eine Ausnahme), steht
dazu ein fester Satz in `gate_error`; bei einer Ausnahme nennt er nur deren Art, den vollen Text
hat die Aktion. `GET /audit` und `GET /audit/export` bereinigen für Nutzer ohne `hosts.execute`
auch ältere Einträge, die noch Ausgabe enthalten: Bei `action.executed` behält `detail.result`
nur die genannten Werte, alle anderen Felder sind leer (`output` und `error` stehen immer als
`null` darin, `detail` als `{}`); in allen Einträgen werden Felder `output`, `stdout`, `stderr`
und `command` (auch verschachtelt) zu `null`, denn ein Befehl kann wie der `payload` einer Aktion Zugangsdaten
enthalten. Wurde dabei etwas entfernt, setzt `GET /audit`
`output_hidden: true`; der Export hat dieses Feld nicht.

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

**Ohne Klick über eine Dauerfreigabe.** Ist eine Aktion über eine Dauerfreigabe angelaufen
([01 §4](01-ARCHITECTURE.md#4-das-aktions-gate)), ist `gate_decision.rule` `standing_approval`, `approved_by_user_id`
bzw. `approved_by_label` nennen die Person, die sie erteilt hat, und `reason` endet mit „– ohne Klick, lief mit
Dauerfreigabe vom <Datum> durch <Benutzername>“. Hat das Gate sie nicht anerkannt, ist es ein normaler Vorschlag mit
dem Grund in `reason` und in `gate_decision.standing_approval_rejected` (Felder:
[03 §5](03-DATA-MODEL.md#5-aktionen--das-gate-journal)).

### Dauerfreigabe für geplante Skripte (Erweiterung `scripts`)

```
POST   /ext/scripts/scripts/{id}/standing-approval   → 200 Skript (wie GET)   {fingerprint, targets_fingerprint}
DELETE /ext/scripts/scripts/{id}/standing-approval   → 204, Freigabe zurückziehen (wirkt sofort)
```

Beide brauchen `actions.standing_approval` (eingebaut: Admin und Owner; Bediener und Betrachter bekommen `403`).
Erteilen geht nur für aktive Skripte mit Zeitplan. Beide Felder im Body sind Pflicht und kommen aus der letzten Antwort
von `GET /ext/scripts/scripts` bzw. `GET /ext/scripts/scripts/{id}`: `fingerprint` (Inhalt, Parameter, Ziel,
Zeitplan) und `targets_fingerprint` (die Zielserver mit Konto, Adresse und SSH-Port). Hat sich seitdem etwas davon
geändert, antwortet `POST` mit `409` und gibt nichts frei; ebenso ohne Zeitplan, bei ausgeschaltetem Skript oder ohne
Server, auf dem es laufen kann. `DELETE` ohne Freigabe: `404`. Erteilen, Zurückziehen und Erlöschen stehen im
Protokoll (`scripts.standing_approval.granted`, `scripts.standing_approval.revoked`,
`scripts.standing_approval.expired`).

`PUT /ext/scripts/scripts/{id}` (Skript speichern) prüft einen Zeitplan vor dem Speichern: Ein ungültiger ergibt `422`
„Der Zeitplan „…“ ist ungültig: …“, und nichts landet im Versionsverlauf. Ein ungültiger Zeitplan aus einer älteren
Version legt die Erweiterung beim Start nicht mehr lahm: Das Skript läuft dann vorerst nicht nach Zeitplan, dazu kommt
eine Meldung. Ebenso nutzt Nodvard Shield für einen ungültigen gespeicherten Zeitplan vorerst seinen Standard und meldet
das.

`GET /ext/scripts/scripts` und `GET`/`PUT /ext/scripts/scripts/{id}` liefern zusätzlich `fingerprint`, bei aktiven
Skripten mit Zeitplan `standing_preview` (die Server, die eine Freigabe jetzt decken würde, je `id`, `name`, `account`,
`address`, `port`) und `targets_fingerprint`, dazu `standing_approval`: `null` ohne Freigabe, sonst `active`, `problem`
(warum sie nicht mehr gilt), `granted_by_label`, `granted_at`, `hosts` (wie bei `standing_preview`) und `new_hosts`
(Namen neuer Zielserver, für die sie nicht gilt). Einträge von `GET /ext/scripts/scripts/{id}/runs` tragen
zusätzlich `standing_approval` (lief ohne Klick) und `reason`.

### Update-Vorschläge für viele Server und Härtungs-Audit (Erweiterung `shield`)

```
POST /ext/shield/defender/updates/propose-all   → 200 {mode, results[], counts}   Recht: soc.manage
POST /ext/shield/defender/audits                → 200 {hosts, started, running, audits}   Recht: soc.manage
```

**`propose-all`.** Body: `{"mode": "security" | "all", "host_ids": ["…"]}`. `host_ids` ist optional; ohne Angabe gilt
jeder Server der Update-Übersicht, bei dem für `mode` etwas offen ist (`security`: Sicherheitsupdates, `all`: Updates).
Aufräumen und Neustart gibt es hier nicht (`422`); beides bleibt eine Entscheidung je Server
(`POST /ext/shield/defender/hosts/{id}/upgrade`).

Die Route legt nur Vorschläge an (Gate, Aktion „System-Updates einspielen“); freigegeben wird wie beim Einzelweg unter
„Aktionen“, gesammelt, außer bei hohem Risiko (dist-upgrade). Steht die Automatik auf „Selbstständig handeln“, laufen
freigegebene Aktionen von selbst an. Jeder Vorschlag entsteht genau wie der Einzelvorschlag (Plan aus dem gespeicherten
Update-Stand, Befehl aus den Feldern, bei der Einstellung „Proxmox: Alle Updates als dist-upgrade“ Risiko `high`). Die
Vorschläge entstehen nacheinander und ohne Warten auf freigegebene Aktionen; die Route öffnet keine SSH-Verbindung.

`results[]`: je Server `host_id`, `name`, `result` (`proposed`, `skipped`, `failed`), bei `skipped`/`failed` `reason`
(deutscher Satz; bei einem unerwarteten Fehler ein fester Satz, Einzelheiten im Protokoll des Dashboards), bei `proposed`
`action_id`, `status` (`proposed`, bei automatischer Freigabe `executing`) und `risk`. `skipped`: nichts offen, nie geprüft,
nicht unterstützt, Einspiel-Lauf aktiv oder für den Server liegt schon ein offener Update-Vorschlag vor. `failed`: Plan
oder Gate sind gescheitert; die anderen Server sind davon unberührt. `counts`: `proposed`, `skipped`, `failed`, `total`.
Läuft schon ein Sammel-Vorschlag, gibt die Route `409`. Im Protokoll steht ein Eintrag `shield.updates_proposed_all`
(Modus, Zählung, vorgeschlagene Server). Offene Vorschläge findet die Route unter den letzten 200 Aktionen der Erweiterung.

**`audits`.** Body `{"host_ids": [...]}` oder `{"host_ids": "all"}` (Standard: alle Server von Shield). Die Antwort kommt sofort:
`started` (neu gestartet), `running` (lief schon, es startet kein zweites) und `audits` (je Server `requested_at`,
`started_at`, `waiting` – es laufen schon drei Audits –, `already_running`). Lynis läuft auf dem Server vom SSH-Kanal
entkoppelt, mit niedriger Priorität und höchstens 3 Stunden; das Ergebnis kommt später in `GET /ext/shield/defender/audits`,
auch nach einem Neustart des Dashboards. `GET /ext/shield/defender/overview` nennt je Server ein laufendes Audit
(`audit_run`).

Die alten Adressen `/ext/nexus-soc/…` antworten gleich (veraltet, siehe §7).

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
**`apps.write`** – neu und additiv: nur Administrator und Owner haben es von selbst. Die Kacheln stehen auf der
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
Ebenfalls neu: `services[].unreachable` (`true` bei der Platzhalterkachel eines Servers, dessen Container nicht gelesen werden
konnten – sie ist kein Dienst und zählt nicht als „läuft nicht“) und `services_unreachable_hosts` (Namen dieser Server, sonst leer).
Der Platzhalter kommt auch, wenn der Server antwortet, Docker aber nicht (Dienst aus, keine Rechte).

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
GET    /files/sources                            → alle FileSources aller Extensions (ohne gesperrte, s. u.)
GET    /files/{source}/list?path=&cursor=
GET    /files/{source}/stat?path=
GET    /files/{source}/info                       → SourceInfo (Quota, Health, Deep-Link)
GET    /files/{source}/download?path=             → Streaming, Range-fähig
POST   /files/{source}/upload?path=               → Streaming
POST   /files/{source}/mkdir | /rename | /remove
POST   /files/transfer                            → {from:{source,path}, to:{source,path}}
                                                    → job_run-ID, Fortschritt über WS
GET    /files/search?q=&sources=                  → Fan-out, Ergebnisse gemischt
```

`/files/transfer` ist das Drag-&-Drop zwischen Quellen im Dateimanager: der Server streamt
`open_read(A) → open_write(B)`, ohne dass die Datei über den Client läuft.

Meinen Quelle und Ziel dieselbe Datei, antwortet `/files/transfer` mit `409` („Quelle und Ziel sind dieselbe
Datei.“) und startet keinen Lauf. In derselben Quelle zählt der gleiche Pfad, bei einer Quelle mit
`file_identity()` ([02 §3](02-EXTENSION-API.md#3-capabilities--der-kern-der-entkopplung), bisher die
SFTP-Quellen der Server) auch der gleiche aufgelöste Pfad (Link, `..`-Umweg). Über zwei Quellen hinweg (etwa
zwei Einträge für denselben Server) nur, wenn beide `file_identity()` liefern, der aufgelöste Pfad gleich ist
und dazu Größe, Änderungszeit, Besitzer, Gruppe und Rechte übereinstimmen; zwei verschiedene Rechner-Kennungen
sind immer zwei Dateien. Im Zweifel gilt die Datei als eine andere. Nicht erkannt wird ein Ordner, der unter
zwei Pfaden eingehängt ist: eine Datei dort auf ihren eigenen zweiten Pfad zu verschieben, löscht sie.
Verschieben ist im Dateimanager ein Transfer mit anschließendem `remove` der Quelle; das Original wird nur
gelöscht, wenn genau so viele Bytes angekommen sind, wie die Quelle beim Anzeigen hatte, sonst bleibt es und
der Dateimanager sagt das.

Die SFTP-Quellen der Server (terminal-Extension) schreiben bei `upload` und `/files/transfer` erst in eine
Temp-Datei im Zielordner (`.<name>.nodvard-tmp-…`) und ersetzen das Ziel erst, wenn alles angekommen ist. Ein
abgebrochener Upload oder Transfer lässt die vorhandene Datei dann unberührt. Ersetzt die Temp-Datei eine
bestehende Datei, ist sie bis zum Ende nur für ihren Besitzer lesbar (`0600`); die neue Datei behält Besitzer,
Gruppe und Rechte der alten. Ein Link wird bis zur echten Datei verfolgt und bleibt ein Link. Direkt ins Ziel
geschrieben wird, wenn der Zugang im Zielordner nichts anlegen darf oder den Besitzer der alten Datei nicht
übernehmen kann (etwa ohne root bei einer fremden Datei). Dann liest Nodvard Deck erst die ganze Quelle (über
8 MiB in eine lokale Temp-Datei), bevor es das Ziel öffnet: bricht die Quelle ab, bleibt das Ziel unberührt,
scheitert erst das Schreiben, kann es unvollständig sein. Pipes und Geräte (etwa `/dev/null`) werden direkt
beschrieben. Weil beim Ersetzen eine neue Datei entsteht, sieht ein Container, in den die Datei einzeln per
Bind-Mount eingebunden ist, den neuen Inhalt erst nach einem Neustart des Containers; ein eingebundener Ordner
ist nicht betroffen.

`upload` hat standardmäßig keine Größengrenze, der Body geht als Strom an die Quelle. `NODVARD_DECK_FILES_MAX_UPLOAD_BYTES`
(Bytes, `0` = keine Grenze) setzt eine. Ist die `Content-Length` größer, kommt `413` mit `Connection: close`, bevor die
Quelle das Ziel öffnet. Ohne Längenangabe (`chunked`) bricht erst das Mitzählen ab (siehe §1), bei der eingestellten
Grenze, mindestens aber bei der allgemeinen (1 MiB); dann hat die Quelle schon zu schreiben begonnen (die SFTP-Quellen
lassen eine vorhandene Datei dann wie oben unberührt).

Scheitert ein Zugriff an der Quelle (Server aus, Zugang abgelehnt, Zeitüberschreitung), antworten `info`, `list`,
`stat`, `download`, `upload`, `mkdir`, `rename` und `remove` mit `502` und dem Text
`Zugriff auf die Quelle fehlgeschlagen: <Grund>`, fehlt die Datei, mit `404`. Bei Netzfehlern ist der Grund ein
deutscher Satz („Server antwortet nicht (Zeitüberschreitung …)“); die Nextcloud-Quelle nennt einen eigenen festen Satz je
Fall, nie den Antworttext des Servers (etwa „Nextcloud antwortet nicht (Zeitüberschreitung).“, „Nextcloud findet … nicht
(HTTP 404). …“, „In der Nextcloud ist kein Speicherplatz mehr frei (HTTP 507).“, „Nextcloud ist gerade nicht bereit (HTTP
503), zum Beispiel im Wartungsmodus. …“). Eine Weiterleitung (3xx) ist bei ihr nie ein Erfolg: „Nextcloud leitet auf eine
andere Adresse um (HTTP n). …“, ohne die Zieladresse. Bei einem Dateifehler (`OSError` wie `PermissionError`, kein Netzfehler) steht nur der Name des
Fehlers da, nie ein Pfad; den vollen Text schreibt der Server nur ins Container-Protokoll. Ein gescheiterter
`/files/transfer` trägt denselben Grund im Lauf (`error`).

`download` holt den ersten Teil aus der Quelle, bevor die Antwort beginnt. Scheitert die Quelle dabei (Weiterleitung, Datei
fehlt, Server nicht erreichbar), kommt ein HTTP-Fehler statt einer leeren oder abgebrochenen Datei: `404` mit
„'<pfad>' nicht gefunden.“, sonst `502` wie oben. Eine leere Datei ist weiterhin `200` ohne Inhalt. Bei Quellen mit eigener
Berechtigung steht ein gescheiterter Download im Protokoll als `files.download` mit Ergebnis `failure` und dem Status.

Rechte: `sources`, `info`, `list`, `stat`, `download` und `search` brauchen `files.read`; `upload`, `mkdir`,
`rename`, `remove` und `transfer` brauchen `files.write`. Eine Quelle kann zusätzlich eine eigene Berechtigung
verlangen (`required_permission`, [02 §3](02-EXTENSION-API.md#3-capabilities--der-kern-der-entkopplung)): Die
SSH-Quellen der terminal-Extension verlangen `hosts.execute`, weil sie mit dem Zugang des Servers arbeiten und
nicht mit dem des Nutzers. Wer sie nicht hat, sieht die Quelle in `/files/sources` und `/files/search` nicht;
alle anderen Endpunkte antworten für sie mit `403`, `/files/transfer` auch dann, wenn nur Quelle oder Ziel
gesperrt ist.

Protokoll, nur für Quellen mit eigener Berechtigung (`target_type` `file_source`, immer nur Quelle und Pfad, nie
Inhalte): `files.access` (verweigert, `outcome` `denied`), `files.download`, `files.upload`, `files.mkdir`,
`files.rename`, `files.remove` (die vier schreibenden auch, wenn sie scheitern), `files.search` (je durchsuchter
Quelle, ohne Suchbegriff), `files.transfer_start` und `files.transfer` (Ergebnis mit `bytes`, beide mit `run_id`).
`list`, `stat` und `info` stehen nicht im Protokoll.

Bei der Nextcloud-Quelle bleibt jeder Pfad im Dateibereich des eingerichteten Benutzers. Ein Pfad, der ihn
verlassen könnte (ein `..`-Teil, auch URL-kodiert wie `%2e%2e` oder mehrfach kodiert, ein Backslash, ein
NUL-Zeichen oder ein kodierter Schrägstrich wie `%2F`), wird abgelehnt, bevor eine Anfrage mit diesem Pfad
an den Nextcloud-Server geht: `list`, `stat`, `download`, `upload`, `mkdir`, `rename` und `remove` antworten
mit `404`, ein `/files/transfer` mit so einem Pfad endet als fehlgeschlagener Lauf.

---

## 4. WebSocket

Das Image startet uvicorn mit `--ws-max-size 1048576`: Eine einzelne Nachricht vom Client an einen der Sockets darf
höchstens 1 MiB groß sein, eine größere beendet die Verbindung (ohne den Schalter gilt der uvicorn-Standard von 16 MiB).

### Multiplexiert: `GET /ws`

Eine Verbindung, viele Kanäle. Auf dem Mobilgerät ist jede zusätzliche Verbindung
Akkulaufzeit — deshalb bewusst nicht ein Socket pro Feature.

Authentifizierung: erste Nachricht nach dem Verbinden ist
`{"type":"auth","token":"<access_token>"}`. Keine Tokens in der URL (sie landen in
Proxy-Logs). Der Token einer schon beendeten Anmeldung (abgemeldet, widerrufen) bekommt
`auth_error`, auch solange er noch nicht abgelaufen ist.

Eine offene Verbindung wird bei jedem `ping` (alle 30 s) erneut geprüft: Ist das Konto
deaktiviert, das Passwort geändert oder die Anmeldung beendet, kommt ein `error` mit dem
Grund in `payload.title`, danach schließt der Server mit Code `4401`. Geänderte Rollen gelten
für die Filterung nach RBAC ab dieser Prüfung, ohne neue Verbindung. Außerdem endet die Verbindung, sobald der
Access-Token abläuft, mit dem sie sich angemeldet hat (Standard 15 Minuten, `access_token_ttl_seconds`): ebenfalls
`error` mit dem Grund, dann Code `4401`. Ein Token ohne `exp` bekommt schon beim Aufbau `auth_error`. Der Client holt
sich per `POST /auth/refresh` einen neuen Access-Token, verbindet sich neu und abonniert seine Kanäle wieder. Eine so
beendete Verbindung bekommt bis zum Schließen nur noch dieses `error`, aber keine Ereignisse mehr, auch nicht auf
Kanälen ohne Rechteprüfung.

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
| `error` | ← | `payload.title` = Grund als deutscher Satz |

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

`action.*`-Ereignisse im Kanal `events` (Recht `hosts.read`, z. B. `action.executed`) enthalten den `payload` der
Aktion (im Umschlag unter `payload.data.payload`) vollständig nur für Verbindungen mit `hosts.execute`;
`actions.approve:<risiko>` reicht hier anders als bei der REST-Ressource nicht. Alle anderen bekommen dieselbe
Nachricht mit den Kennungen wie in §3 „Aktionen“ und `payload.data.payload_hidden` (`true`, wenn etwas weggelassen
wurde).

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
Scheitert das Öffnen, kommt vor dem Close-Frame `{"type":"error","message":…}`. `message` nennt den Grund
und ist nie leer, bei Netzfehlern ein deutscher Satz („Server antwortet nicht (Zeitüberschreitung bei …). Ist er
eingeschaltet und im Netz?“); derselbe Grund steht in `terminal.open` mit Ergebnis `failure` (in `reason`).

Auf dem Socket: **binäre** Frames = rohe PTY-Bytes in beide Richtungen.
**Text**-Frames = JSON-Steuerung:

```json
{"type":"resize","cols":120,"rows":40}
{"type":"signal","name":"SIGINT"}
{"type":"exit","code":0}
```

Sitzungen werden im Audit-Log als `terminal.open` / `terminal.close` mit Dauer vermerkt.

Eine Sitzung hängt an Konto, Recht `hosts.execute`, Passwort und genau der Anmeldung, aus der
das Ticket stammt. Der Server prüft das beim Öffnen und danach alle 15 s
(`terminal_recheck_interval_s`): Ist das Konto deaktiviert, das Recht entzogen, das Passwort
geändert oder die Anmeldung beendet (abgemeldet oder widerrufen), endet die Sitzung. Ohne jede
Eingabe **und** Ausgabe endet sie nach 30 Minuten (`terminal_idle_timeout_s`, 0 = aus); ein
Befehl, der noch Text liefert, hält sie offen. Der Grund kommt jeweils als
`{"type":"error","message":…}` vor dem Close-Frame. Im Protokoll steht er beim Öffnen in
`terminal.open` mit `denied`, bei einer laufenden Sitzung in `terminal.close` (`reason`, dazu
`detail.ended_by`: `access_ended` bzw. `idle`).

| Close-Code | Bedeutung |
|---|---|
| `1000` | normales Ende |
| `4401` | Konto, Recht, Passwort oder Anmeldung gelten nicht mehr |
| `4404` | Ticket unbekannt oder abgelaufen, Host unbekannt |
| `4408` | Leerlauf |
| `4500` | Öffnen gescheitert |
| `4501` | keine Erweiterung bietet für diesen Host ein Terminal an |

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

Unbekannter Host oder keine Konsole für ihn: `404`. Scheitert danach das Öffnen, antwortet der `POST`
mit `502` und `detail` „Konsole konnte nicht geöffnet werden: …“; derselbe Grund steht in
`console.open` mit Ergebnis `failure` (in `reason`). Das gilt auch, wenn der Hypervisor den
WebSocket-Aufbau mit einer Weiterleitung (3xx) beantwortet: Nodvard Deck folgt ihr nicht, es entsteht
keine zweite Verbindung, und der Grund nennt die Umleitung samt Statuscode
(z. B. „… umgeleitet (HTTP 302) …“). Einen Proxy aus der Umgebung von Nodvard Deck (`HTTP_PROXY`,
`HTTPS_PROXY`, `WSS_PROXY` usw.) benutzt die Konsole nie, ihre Verbindungen gehen immer direkt an die eingetragene
Adresse des Hypervisors.

Wie beim Terminal endet die Konsole, sobald Konto, Recht `hosts.execute`, Passwort oder
Anmeldung nicht mehr gelten (Prüfung beim Aufbau und alle 15 s): Close-Code `4401`, sonst
`1000`. Ein unbekanntes oder abgelaufenes Ticket: `4404`.

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

**Bewusst strenger aus Sicherheitsgründen:** `DELETE /me/totp` und `POST /me/totp/setup` verlangen `current_password`,
das sie früher nicht brauchten (sonst könnte eine gestohlene Sitzung allein Zwei-Faktor ab- oder mit einem eigenen
Authenticator einschalten). Bei `DELETE /me/totp` ist der Body dadurch Pflicht (Eintrag in `breaking_exceptions.toml`),
bei `POST /me/totp/setup` bleibt er im Vertrag optional; ohne Passwort gibt es dort `400` statt `422` (siehe §3).
Ebenso bei Erweiterungsrouten: Was eine Erweiterung ohne `permission=` einhängt, verlangt eine Anmeldung und antwortet
ohne Token mit `401` (früher war es ohne Anmeldung erreichbar). Ohne Anmeldung geht nur, was sie ausdrücklich mit
`public=True` und der Manifest-Berechtigung `api.public` einhängt
([02 §2](02-EXTENSION-API.md#2-der-extensioncontext)). Bei hello-world verlangen `POST /notify-test` und
`POST /vault-use-and-fail` dazu `extensions.manage`. Ebenso verlangen `POST /notifications/read` und
`POST /notifications/read-all` `notifications.write` (früher genügte `notifications.read`), und wer weder
`hosts.execute` noch `actions.approve:<risiko>` für das Risiko einer Aktion hat, bekommt ihren `payload` gekürzt
(`payload_hidden`, siehe §3). Ebenso bei der Größe: Einen Body über 1 MiB (siehe §1) beantwortet ein
Endpunkt, der ihn liest, mit `413` (früher gab es keine allgemeine Grenze); Uploads haben eigene, höhere Grenzen.
Ebenso verlangen `DELETE /me/totp` und `POST /me/recovery-codes` bei eingeschalteter Zwei-Faktor-Anmeldung zusätzlich
`totp_code` (App-Code oder Wiederherstellungs-Code), `POST /system/backups/download`, `POST /system/backups/{name}/ticket`
und `POST /system/restore/{id}/schedule` den App-Code. Im Vertrag ist das Feld optional (kein Schema-Bruch). Ältere
Clients, z. B. eine ältere Android-App, bekommen `403` mit `code: "totp_missing"` und können diese Aktionen erst nach
einem Update ausführen. Fehler beim Code tragen zusätzlich `code` (`totp_wrong`, `totp_used`), auch bei Update und Rückweg.
Und bei Erweiterungen: `ctx.http` folgt keiner Weiterleitung mehr, auch nicht mit `follow_redirects=True`, und
`ctx.audit.log()` schreibt keine Kern-Einträge (`mfa.*`, `auth.*`, `login.*`, `system.*`, `user.*`); beides in
[02 §2](02-EXTENSION-API.md#2-der-extensioncontext).

**Umbenennen** geht nur so: Der alte Name bleibt mehrere Releases parallel bestehen und ist
als `deprecated` markiert (`deprecated=True` am Endpunkt bzw. Feld, dazu ein Hinweis im
Änderungsprotokoll). Erst wenn App und Extensions den neuen Namen nutzen, darf der alte
später entfallen. Wirklich unvermeidbare Brüche brauchen `/api/v2` (siehe §6).

**Umbenannte Erweiterungen (`legacy_ids`).** Bekommt eine Erweiterung eine neue Kennung
([02 §1](02-EXTENSION-API.md#erweiterung-umbenennen-legacy_ids)), gelten ihre alten Adressen weiter. Diese bleiben in allen
1.x erhalten (als veraltet markiert) und fallen frühestens mit 2.0 weg. Es sind keine Weiterleitungen (kein `3xx`, kein
eigener Kopf in der Antwort), sondern dieselben Routen unter einer zweiten Adresse:
- `/api/v1/ext/<alt>/…`: dieselben Routen wie unter `/api/v1/ext/<neu>/…`, mit derselben Anmeldeprüfung und denselben
  Antworten. Im OpenAPI-Schema sind sie `deprecated` und stehen hinter der neuen Adresse. Öffentliche Routen stehen mit
  beiden Präfixen im Protokoll.
- `/api/v1/extensions/<alt>/…` (Abruf, enable, disable, `frontend/index.js`, settings, secrets, test) meint dieselbe
  Erweiterung und dieselbe gespeicherte Zeile. Antworten nennen in `id` die neue Kennung. Ausnahme: Liegt unter der alten
  Kennung noch ein verwaister Stand (ein Zwilling, z. B. nach Neuinstallation, Rückweg und erneutem Update), antworten
  ändernde Aufrufe darüber mit `409`. Lesen geht weiter.
- Neue optionale Felder: `ExtensionOut.legacy_ids`, `PageOut.legacy_ext_ids`, `WidgetOut.legacy_ext_ids` (jeweils `[]`
  ohne Umbenennung), `ActionOut.action_label` (Name der angemeldeten Aktionsart, sonst `null`) und `ActionOut.ext_name`
  (Name der installierten Erweiterung, auch bei alter Kennung, sonst `null`).
- `GET /extensions` listet jede Erweiterung einmal unter ihrer neuen Kennung; verwaiste Zwillinge fehlen.
  `GET /jobs?ext_id=` filtert nach der Speicher-Kennung (bei Nodvard Shield auf bestehenden Installationen
  `nexus-soc`), `GET /capabilities` nennt nur die neue Kennung.

**Nodvard Shield** heißt seit 0.7 `shield` (früher `nexus-soc`). Veraltet sind damit `/api/v1/ext/nexus-soc/**` (alle
Routen der Erweiterung, heute 34 Pfade, jeder auch unter `/api/v1/ext/shield/…`) und `/api/v1/extensions/nexus-soc/**`.
Beide antworten wie die neuen. Neue Einträge im Protokoll heißen `shield.*`, ältere `nexus_soc.*` bleiben stehen; ein
Filter `GET /audit?action=shield.…` findet die älteren nicht. Die Aktionsarten bleiben `nexus_soc.*`
([01 „Alte und neue Namen“](01-ARCHITECTURE.md#alte-und-neue-namen)).

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
