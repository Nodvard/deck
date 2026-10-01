# 00 — Architekturentscheidungen

Dieses Dokument hält die grundlegenden Technik- und Architekturentscheidungen von
Nodvard Deck fest, jeweils mit Begründung und Konsequenzen. Code, Kommentare und andere
Dokumente verweisen mit der Kennung `D-<n>` darauf; die Nummern bleiben stabil.

---

## Zusammenfassung

| Bereich | Entscheidung | Anmerkung |
|---|---|---|
| Backend | Python 3.12 · FastAPI | genau ein Worker-Prozess (D-01) |
| Frontend | React 18 · Vite · TypeScript | Tailwind + Radix (D-03) |
| Mobile | Flutter | Widgets nur deklarativ (D-04) |
| Datenbank | SQLite als Default | PostgreSQL optional (D-02) |
| Terminal | xterm.js + SSH | `asyncssh`, nicht `paramiko` (D-05) |
| Auslieferung | ein Multi-Arch-Docker-Image | ein Container, ein Volume (D-10) |

---

## D-01 Backend: Python 3.12 + FastAPI

**Entscheidung:** Das Backend ist in Python 3.12 mit FastAPI geschrieben.

**Begründung:** async für WebSockets und Terminal. Außerdem senkt Python das Risiko bei
der Ablösung des Vorgängersystems, das ebenfalls in Python geschrieben war: bewährte
Regex-Muster (z. B. die Sperrliste für Befehle), Prompts und Heuristiken (z. B. gegen
Neustart-Schleifen) sind aus dem Vorgängersystem übernommen, statt in eine andere
Sprache übersetzt zu werden. Das Ökosystem passt: `asyncssh`, `proxmoxer`, `docker`,
`httpx`, `cryptography`, `pyotp`.

**Auflage 1: genau ein Worker-Prozess** (`uvicorn --workers 1`), kein
Gunicorn-Multiprocessing. Der Kern hält prozessinternen Zustand, der nicht dupliziert
werden darf: Extension-Registry, Event-Bus, WebSocket-Hubs, Scheduler,
SSH-Verbindungspool, Anti-Flapping-Historie. Skalierung erfolgt bei Bedarf über
`asyncio`, nicht über Prozesse. Wer mehrere Prozesse will, muss Event-Bus und Scheduler
vorher externalisieren — das ist ein bewusster, dokumentierter Bruch, kein Versehen.

**Auflage 2: kein blockierender Code im Event-Loop.** Alles, was blockiert
(`subprocess`, `paramiko`, Tesseract, ClamAV), läuft über `asyncio.to_thread` / einen
expliziten Executor. Das ist die häufigste Fehlerquelle in FastAPI-Projekten dieser Größe
und der Grund für die `asyncssh`-Entscheidung (D-05).

**Verworfene Alternative:** Go. Wäre auf einem Raspberry Pi sparsamer und erlaubte eine
Auslieferung als einzelne Binärdatei. Unterliegt aber bei der Übernahme aus dem
Vorgängersystem und beim Python-Ökosystem für die Infrastruktur-Anbindung. Die
Auslieferungsfrage löst stattdessen ein Multi-Arch-Docker-Image (D-10).

---

<a id="d-02-datenbank-sqlite-als-default-postgresql-optional--abweichung-von-9"></a>

## D-02 Datenbank: SQLite als Default, PostgreSQL optional

**Entscheidung:** SQLite ist die Standard-Datenbank. PostgreSQL funktioniert ohne
Codeänderung, ist aber optional.

**Begründung:**

1. **Zielhardware.** Ein typisches Hosting-Ziel ist ein Raspberry Pi mit wenigen GB RAM,
   auf dem bereits andere Dienste laufen. Ein Postgres-Server kostet dort dauerhaft
   100–200 MB RSS plus einen zweiten Dienst, der gepatcht, gesichert und überwacht werden
   will. Mit SQLite bleibt der Pi ein realistisches Hosting-Ziel.
2. **Betriebsaufwand.** Nodvard Deck soll sich ohne Begleitung betreiben lassen: offline,
   ohne Lizenzserver, mit Dokumentation statt Support-Kontakt. Jede zusätzliche
   Pflicht-Komponente ist eine weitere Hürde bei der Installation und eine weitere
   Fehlerquelle. `docker run` mit einem Volume muss reichen.
3. **Die Last rechtfertigt Postgres nicht.** Eine Handvoll Nutzer, ein paar Dutzend Hosts,
   Audit- und Run-Logs im Bereich einiger tausend Zeilen pro Woche. SQLite im WAL-Modus
   liegt Größenordnungen über diesem Bedarf.

**Umsetzung:** SQLAlchemy 2.0 (async) + Alembic. `DATABASE_URL` entscheidet;
`sqlite+aiosqlite:///./data/lattice.db` ist der Default,
`postgresql+asyncpg://…` funktioniert ohne Codeänderung.

**Disziplin-Regeln, die die Wahlfreiheit erhalten — für jeden neuen Code verbindlich:**

- Kein `JSONB`, kein `ARRAY`, kein `ILIKE`, kein `ON CONFLICT … DO UPDATE`-Dialektcode,
  keine Postgres-Funktionen. Nur `sa.JSON`, `sa.String`, portable Ausdrücke.
- Listen (z. B. Host-Tags) als Assoziationstabelle, nicht als Array-Spalte.
- Zeitstempel immer UTC-aware `DateTime(timezone=True)`, Default in Python gesetzt
  (nicht `server_default=func.now()`, das driftet zwischen den Dialekten).
- SQLite-Pragmas beim Connect: `journal_mode=WAL`, `foreign_keys=ON`,
  `busy_timeout=5000`, `synchronous=NORMAL`.
- Schreibzugriffe laufen über **einen** Session-Pfad; keine parallelen Writer-Tasks, die
  sich selbst blockieren. Lange Schreibvorgänge (Log-Import) in Batches.

**Grenze, die SQLite nicht überschreiten darf:** Zeitreihen-Telemetrie (CPU-/RAM-Kurven
pro Host im Sekundentakt) gehört **nicht** in die relationale DB — weder in SQLite noch in
Postgres. Live-Werte liegen nur im Speicher. Wer lange Metrik-Historie will, bindet ein
vorhandenes Prometheus/Grafana über einen Connector an.

**Nachtrag: Metrik-Verlauf.** Einen Verlauf gibt es trotzdem, ohne diese Grenze zu
verletzen. Hat die Quelle selbst Historie (Proxmox führt RRD-Daten), liefert der Anbieter
sie über `MetricsProvider.history()` durch — Nodvard Deck speichert nichts. Nur für Hosts
ohne solche Quelle (z. B. ein per SSH angebundener Raspberry Pi) sammelt
`core/metrics_history.py` alle 30 s — in eine **eigene** SQLite-Datei `data/metrics.db`
mit eigener Verbindung und genau einem Schreiber, nicht in `lattice.db`, ohne Alembic,
jederzeit löschbar (dann fehlt nur der Verlauf). Rohwerte 48 h, 5-Minuten-Verdichtung
(Mittel/Min/Max) 35 Tage. Der Sekundentakt bleibt der Live-Ansicht vorbehalten und wird
nie gespeichert.

---

<a id="d-03-frontend-react-18--vite--typescript--bestätigt"></a>

## D-03 Frontend: React 18 + Vite + TypeScript

**Entscheidung:** React 18 mit Vite und TypeScript, ergänzt um folgende Festlegungen:

- **UI-Basis: Tailwind CSS + Radix UI Primitives.** Bewusst *kein* fertiges
  Komponenten-Framework (MUI, Mantine, Ant). Grund: White-Labeling muss **zur Laufzeit**
  funktionieren — ein Admin ändert Farben in den Einstellungen, ohne das Frontend neu zu
  bauen. Das geht sauber nur über CSS-Custom-Properties, die aus `GET /api/v1/branding`
  gespeist werden. Fertige Frameworks bringen eigene, teils zur Build-Zeit fixierte
  Theming-Systeme mit und arbeiten dagegen.
- **Server-State: TanStack Query.** Client-State: Zustand. Kein Redux.
- **Routing: React Router v6** mit Lazy-Routes — Extension-Seiten hängen sich dort ein.
- **Terminal: xterm.js** + `@xterm/addon-fit`, `@xterm/addon-webgl`.
- **Editor (Script-Repository): CodeMirror 6**, nicht Monaco. Monaco ist ~4 MB und zieht
  Web-Worker nach; CodeMirror 6 ist modular und für ein auf einem Pi ausgeliefertes Bundle
  spürbar günstiger. Der Funktionsumfang reicht für Shell, Python und YAML.
- **Build-Ziel:** Das Frontend wird statisch gebaut und vom Backend ausgeliefert
  (`StaticFiles`), kein separater Webserver. Ein Port, ein Dienst, ein
  Reverse-Proxy-Eintrag.

---

<a id="d-04-mobile-flutter--bestätigt-mit-einer-konsequenz-die-9-nicht-zieht"></a>

## D-04 Mobile: Flutter, Widgets deklarativ

**Entscheidung:** Die geplante App wird mit Flutter gebaut: echte APK, natives Gefühl,
iOS bleibt möglich.

**Konsequenz:** Eine Extension kann keine Flutter-UI ausliefern, sie liefert React. Wären
Widgets React-Komponenten, sähe die App von den Extensions **nichts** — und der
Grundsatz, dass Web und App sich eine API teilen und keine Logik doppelt gebaut wird,
wäre mit der ersten Extension gebrochen.

Deshalb ist die Widget-Schnittstelle **deklarativ**: Eine Extension beschreibt ein Widget
als Datenstruktur (Typ + Feldbindungen), nicht als Code. React und Flutter implementieren
beide denselben, endlichen Satz von Widget-Typen. Details in
[02-EXTENSION-API §4](02-EXTENSION-API.md#4-widgets--deklarativ-nicht-als-code).

Eine Extension darf zusätzlich eine React-Komponente für eine reichere Web-Darstellung
mitliefern — aber nur als *Aufwertung* über der deklarativen Basis, nie als deren Ersatz.
Regel: **kein Widget ohne deklarative Form.**

Die App wird erst gebaut, wenn die API stabil ist.

---

## D-05 Terminal: xterm.js + asyncssh

**Entscheidung:** Das Web-Terminal ist kein lokales PTY, sondern ein entferntes PTY über
SSH; der Browser zeigt es mit xterm.js an. SSH spricht der Kern über `asyncssh`.

**`asyncssh` statt `paramiko`.** Paramiko ist blockierend und bräuchte pro Sitzung einen
Thread plus Brücke in den Event-Loop. Bei mehreren offenen Terminals, Script-Läufen und
Watcher-Loops auf schwacher Hardware wie einem Raspberry Pi ist das genau die
Architektur, die später unerklärliche Hänger produziert. `asyncssh` ist async-nativ, kann
Verbindungen und Kanäle poolen und passt zum Objektmodell des restlichen Kerns.

Derselbe SSH-Layer bedient **drei** Verbraucher: Web-Terminal, Script-Ausführung (es gibt
keinen zweiten Ausführungsweg) und KI-Remediation. Ein Layer, ein Audit-Punkt, ein
Fehlerbild.

**Sicherheitskorrektur gegenüber dem Vorgängersystem:** Dort lief SSH mit
`-o StrictHostKeyChecking=no`. Der neue Layer pflegt eine eigene Known-Hosts-Tabelle
(TOFU beim ersten Kontakt, danach Pinning; eine Änderung erzeugt eine sichtbare Warnung
statt stiller Annahme).

**Eigene SSH-Schlüssel:** Zugangsdaten gehören in den Vault (D-06); Rotation und Widerruf
sind Plattform-Funktionen, keine Handarbeit in `authorized_keys`. Nodvard Deck kann je
Server ein eigenes ed25519-Schlüsselpaar erzeugen: Der private Teil geht direkt in den
Vault, der öffentliche wird einmal in `authorized_keys` des Servers eingetragen. Ein
bereits vorhandener Zugang bleibt Standard, bis der neue geprüft und bewusst umgestellt
ist.

---

## Weitere Festlegungen

### D-06 Secrets-Vault
`cryptography` / Fernet (AES-128-CBC + HMAC-SHA256). Der Master-Key liegt in
`data/master.key` (0600, Pfad über `NODVARD_DECK_MASTER_KEY_PATH` einstellbar) und wird
beim ersten Start erzeugt, nie in der Datenbank abgelegt. Keine Eigenbau-Krypto, keine
Secrets im Klartext in der DB, keine Secret-Werte in irgendeiner API-Antwort —
Extensions bekommen ein `SecretHandle`, nie den String. Die Schlüsselversion wird
mitgespeichert (`data/vault_keyring.json`), damit Rotation ohne Neuverschlüsselung aller
Secrets auf einmal möglich ist. Ein Entsperren per Passphrase (Argon2id-KDF) beim Start
ist vorgesehen, aber noch nicht umgesetzt.

### D-07 Auth
- Passwörter: **Argon2id** (`argon2-cffi`).
- Access-Token: JWT, 15 min, im Speicher des Clients.
- Refresh-Token: opaker Zufallswert, gehasht in der DB, **widerrufbar**, 30 Tage.
  Web bekommt ihn als `HttpOnly; Secure; SameSite=Strict`-Cookie, Android als Wert für
  `flutter_secure_storage`. Der Access-Token-Pfad ist für beide identisch
  (`Authorization: Bearer`) — deshalb ist die API wirklich eine gemeinsame.
- 2FA: TOTP (`pyotp`), das Secret liegt im Vault.
- Zusätzlich `api_tokens` für Automatisierung (gescopt, widerrufbar) — statt
  ungeschützter Endpunkte, die von außen per Cron aufgerufen werden.

### D-08 Scheduler
**Ein** Scheduler im Kern, auf Basis von **APScheduler 3.x**. Sicherheits-Audits und das
Script-Repository registrieren ihre Jobs dagegen. Keine Cron-Einträge auf den Hosts:
Verstreute Cron-Einträge führen leicht zu doppelt ausgeführten Jobs, die niemand sieht.

Ursprünglich war APSchedulers `SQLAlchemyJobStore` vorgesehen. Umgesetzt ist der
`MemoryJobStore`; die einzige dauerhafte Quelle ist die eigene `jobs`-Tabelle. Grund: Der
SQLAlchemy-Jobstore persistiert per Pickle, Extension-Handler sind aber Methoden von
Instanzen, die bei jedem Start neu entstehen, und zwei persistente Quellen könnten
auseinanderlaufen. Details in `core/scheduler.py`.

Das Script-Repository selbst ist eine mitgelieferte Extension; seine Bausteine
(Ausführung, Scheduler, Gate, Vault, Run-Log) gehören zum Kern — siehe
[02-EXTENSION-API §9](02-EXTENSION-API.md#9-warum-das-script-repository-eine-mitgelieferte-extension-ist-und-kein-core-modul).

### D-09 Hintergrundarbeit / Queue
**Kein Celery, kein Redis, kein RabbitMQ.** Hintergrundarbeit läuft als
`asyncio.Task` mit einem in der DB persistierten `job_runs`-Datensatz. Begründung
wie bei D-02: Jede zusätzliche Pflicht-Komponente ist Betriebs- und Supportlast, und
die Last rechtfertigt sie nicht. Persistenz gibt es trotzdem — ein beim Neustart
unterbrochener Lauf wird als `interrupted` markiert, nicht stillschweigend vergessen.

### D-10 Auslieferung
Multi-Arch-Docker-Image (`linux/arm64` für Raspberry Pi und andere ARM-Rechner,
`linux/amd64` für x86-Rechner), ein Container, ein Volume (`/app/data`: SQLite,
Master-Key, Extension-Daten, Script-Git-Repo). Ein Demo-Image zum Ausprobieren braucht
keinen eigenen Build: Dasselbe Image startet mit `NODVARD_DECK_DEMO_MODE=1` mit
Beispieldaten.

Der Raspberry Pi ist als Hosting-Ziel bestätigt: In einem 24-Stunden-Lauf auf einem Pi,
der nebenbei andere Dienste trägt, blieb der Container deutlich unter den
Abbruchkriterien (Load-Average über 4,0 oder mehr als 400 MB RSS); ein x86-Rechner ist
das Ausweichziel bei wachsender Last.

**Nachtrag: Beispieldaten („Mit Beispieldaten ansehen“).** Der Demo-Modus legt dieselben
Beispieldaten an wie der Knopf im Cockpit (`POST /demo/seed`, `core/demo_seed.py`): fünf
Server aus dem Dokumentationsbereich `192.0.2.0/24` (RFC 5737, ohne Zugang – kein
Hintergrundjob und keine Verbindungsprüfung kann sie anfassen), vier Meldungen und, falls
Module Widgets liefern, ein Beispiel-Layout im Dashboard des Nutzers (das alte wird
gesichert und zurückgespielt). Dazu kommen drei Beispiel-Apps: *eigene Apps* (von Hand
angelegte Links, ein Kern-Konzept) mit Adressen ebenfalls aus `192.0.2.0/24`, zwei davon
mit einem Beispiel-Server als Bezug; Nodvard Deck ruft diese Adressen nie ab.
**Beispieldaten löschen** entfernt sie wieder. *Erkannte* Apps gibt es als Beispiel nicht,
denn sie stammen aus der Container-Übersicht eines Moduls; sie ohne Modul anzulegen, wäre
entweder irreführend oder zöge Modul-Logik in den Kern.
- **Markierung ohne Migration:** Die IDs aller angelegten Zeilen stehen in der globalen
  Einstellung `demo.seeded_ids`. Das Löschen entfernt genau diese IDs; echte Daten (auch
  solche, die später daneben entstehen) bleiben unberührt. Zusätzlich bleibt ein
  Beispiel-Server stehen, der zu einem echten umgebaut wurde (Adresse nicht mehr aus
  `192.0.2.0/24` oder von einem Modul eingelesen); ebenso eine Beispiel-App, deren Adresse
  nicht mehr in `192.0.2.0/24` liegt.
- **Zugang an einem Beispiel-Server:** Der Server wird trotzdem mitgelöscht (er ist
  erkennbar ein Beispiel; sonst bliebe ein Rest, der nie mehr verschwindet), aber die
  Rückfrage nennt ihn vorher.
- **Rechte:** Anlegen/Löschen braucht `hosts.write` **und** `settings.write` (Server,
  Meldungen für alle, Dashboard – die ganze Installation); den Status (Band) sieht, wer
  `hosts.read` hat.
- **Nur auf einer Installation ohne echte Server** (sonst `409`); Meldungen werden direkt
  als Zeilen angelegt, nie über die Kanäle verschickt.

### D-11 Beobachtbarkeit
`structlog` als JSON-Logs auf stdout, `X-Request-Id`/`correlation_id` durch alle
Schichten inkl. Audit-Log. Keine eigene Metrik-Pipeline — ein `/metrics`-Endpunkt im
Prometheus-Format reicht; ein vorhandenes Prometheus kann ihn abfragen.

Stand: `correlation_id` ist in Audit-Log, Aktionen und Job-Läufen umgesetzt. JSON-Logs
über `structlog`, `X-Request-Id` und der `/metrics`-Endpunkt sind noch nicht umgesetzt.

### D-12 Async-SQLAlchemy: `relationship()` auf frisch angelegten Objekten

**Regel:** Nach `session.add(obj)` (+ ggf. Flush) darf keine `relationship()`-Spalte auf
`obj` gelesen ODER per `.append()` beschrieben werden, ohne vorher
`await db.refresh_relationships(session, obj, "spaltenname")` aufzurufen
(`backend/src/nodvard_deck/db/base.py`).

**Begründung:** `lazy="selectin"` (die einzige in diesem Schema verwendete
Ladestrategie, siehe `models/*.py`) lädt eine Collection nur dann eager, wenn das
Elternobjekt über eine **awaitete Query** in die Session kam. Ein Objekt, das gerade erst
per `Model(...)` konstruiert und mit `session.add()` hinzugefügt wurde, kam nie über eine
Query — seine Collections sind nicht geladen. Der erste Zugriff (lesend wie schreibend)
löst SQLAlchemys synchronen Fallback-Lazy-Load aus, der in einer async Session ohne
laufenden Greenlet-Kontext mit `MissingGreenlet` abstürzt.

**Bekannte Fälle** (alle im laufenden Code aufgetreten, nicht beim Lesen gefunden):
1. `services/auth.ensure_builtin_roles()` — `Role.permissions`.
2. Lesen von `User.roles` in einem Test — derselbe Fallstrick.
3. `ext/context.py` `HostsHandle.upsert_discovered()` — `Host.tags`.
4. `services/hosts.create_host()` — `Host.tags`, in einer anderen Ausprägung: Der Code
   rief `refresh_relationships()` bereits auf, aber nur `if tags:`. Der häufigere Fall
   ohne Tags rutschte durch, weil eine leere, aber ungeladene Collection genauso einen
   Lazy-Load auslöst wie eine befüllte.

**Daraus folgt:** `refresh_relationships()` steht **nie hinter einer Bedingung, gleich
welcher Art.** Die Begründung für eine Ausnahme kann jedes Mal anders klingen
("vermutlich leer", "wird eh sofort gelesen", "nur beim Bulk-Import nötig"). Jeder
`if`/`else`, der zwischen `session.add()` und dem Refresh-Aufruf entscheidet, ob
refresht wird, ist deshalb per Definition ein Bug-Kandidat — unabhängig vom Inhalt der
Bedingung. Der Refresh selbst ist billig (ein zusätzliches `SELECT` bei leerer
Collection); das Risiko eines übersehenen Guards ist es nicht.

**Erwogen und bewusst NICHT gebaut: ein automatischer Lint-/AST-Check** (analog zu
`scripts/check_core_purity.py`). Die Erkennung bräuchte echte Datenfluss-Analyse (welcher
Name ist ein frisch konstruiertes Objekt, welches Attribut ist eine Relationship, wurde
dazwischen refresht). Rein syntaktisches Scannen hätte hier eine hohe
Falsch-Positiv-/Negativ-Rate, für derzeit nur vier betroffene Spalten im gesamten Schema
(`Host.tags`, `Host.credentials`, `User.roles`, `Role.permissions`). Der Aufwand für
einen zuverlässigen Checker steht in keinem Verhältnis zum Nutzen, solange die Tests
echte Codepfade gegen eine echte Datenbank ausführen und der Fehler dort sofort laut
auftritt.

**Stattdessen:** `db.refresh_relationships()` als benannter, grep-barer Helper (macht
den Fix an einer Stelle auffindbar) plus diese Dokumentation. Jede Funktion, die ein
Modell mit eigenen `relationship()`-Spalten anlegt, sollte diese Regel im Docstring
zitieren, wie es `ensure_builtin_roles()` und `HostsHandle.upsert_discovered()` tun.

---

### D-13 Singleton-/DI-Escape: Code, das an `app.dependency_overrides` vorbeigreift

**Regel:** Jeder Code, der auf einen Wert angewiesen ist, den der Aufrufer (ein
FastAPI-Request, ein Test, ein Extension-Load) potenziell ÜBERSCHREIBEN will
(`Settings`, die DB-`Session`, die Override-Anbindung eines ganzen `APIRouter`), MUSS
diesen Wert explizit durch den Aufrufpfad gereicht bekommen (Parameter, `Depends()`, an
`build_context()` übergebene Settings). Ein bequemer Griff zu einem Prozess-Singleton
(`config.get_settings()` direkt aufgerufen, ein frisch konstruierter `APIRouter()` ohne
Anbindung an die App) sieht im Erfolgsfall genauso aus wie der korrekte Weg — bis ein
Aufrufer (ein Test, ein zweiter Request-Kontext) etwas anderes erwartet und der
Singleton lautlos das Falsche liefert.

**Bekannte Fälle** (dieselbe Wurzelursache an drei verschiedenen Stellen):

1. **`ext/context.py` `SecretsHandle.create()`/`Context.vault_use()`.** Beide riefen
   `config.get_settings()` direkt auf statt der beim Laden der Extension übergebenen
   Settings. In Tests griff `app.dependency_overrides[get_settings]` deshalb nicht (es
   wirkt nur auf die DI-Auflösung von Endpunkten); der Aufruf landete beim echten,
   gecachten Prozess-Singleton und damit bei den echten Projektpfaden
   (`data/master.key`). Selbst der Test für genau diese Isolation
   (`test_vault_use_isolation_survives_a_real_extension_request_failure`) bemerkte es
   nicht, weil er nur Statuscode und Audit-Eintrag prüfte, nicht die benutzte
   Keyring-Datei. **Strukturell behoben:** `settings` wird explizit durch
   `build_context()` gereicht und auf `Context` gespeichert (siehe Docstring von
   `Context._app_settings`).
2. **Verstreute Stellen im Kern** (`api/v1/auth.py::_cookies_require_https()`,
   `db/session.py::get_engine()`, der Lifespan in `main.py`). Diese Stellen rufen
   `get_settings()` weiterhin direkt auf — bewusst nicht einzeln umgebaut (siehe unten).
   Stattdessen setzt die Autouse-Fixture `_reset_settings_singleton` in
   `tests/conftest.py` `config._settings` vor UND nach jedem Test auf `None`, damit kein
   Test vom Singleton eines vorherigen Tests kontaminiert wird. **Nur abgeschirmt:** Das
   schützt die Tests, ändert aber nichts am Produktionscode.
3. **`ext/runtime.py` `ExtensionRuntime.mount_router()`.** Ein frisch konstruierter
   `APIRouter()` (`scratch`) hat `dependency_overrides_provider=None`; FastAPI (0.141)
   übernimmt diesen Wert beim `include_router()`-Aufruf UNVERÄNDERLICH in jede daraus
   entstehende Route (`_RouterIncludeContext.for_include()`). Ohne
   `scratch.dependency_overrides_provider = app` VOR dem `include_router()`-Aufruf hätte
   jede über eine Extension montierte Route, die `Depends(get_settings)` oder
   `Depends(get_session)` nutzt (direkt oder über `CurrentUser`),
   `app.dependency_overrides` stillschweigend ignoriert. **Strukturell behoben** für
   alle Extensions, weil `mount_router()` der einzige Aufrufpfad ist, über den eine
   Extension-Route montiert wird. Ein Regressionstest
   (`test_include_router_permission_param_protects_route_default_stays_open`) prüft es
   gegen einen echten, über die Registry geladenen Aufrufer.

**Der Unterschied zwischen Fall 3 und den Fällen 1+2 ist die eigentliche Lehre:**
Fall 3 ist strukturell behoben, weil es GENAU EINEN Aufrufpfad gibt, den der Fix zentral
abdeckt. Fall 2 ist nur ABGESCHIRMT (Test-Fixture), weil die betroffenen Stellen
verstreut sind und jede einzeln umgebaut werden müsste, ohne dass bisher ein Aufrufer
Schaden davon hatte (sie laufen alle früh im Prozesslebenszyklus, nie pro Test
wiederholt mit unterschiedlichen Settings). Ob ein neuer Fall "strukturell" oder nur
"abgeschirmt" behoben ist, ist deshalb jedes Mal ausdrücklich zu prüfen und zu benennen —
nicht anzunehmen.

**Erwogen und zurückgestellt: ein Lint-/AST-Check gegen direkte `get_settings()`-/
`get_session()`-Aufrufe außerhalb von `Depends(...)`.** Anders als bei D-12 wäre das
syntaktisch machbar. Zurückgestellt, nicht verworfen: Die verstreuten Stellen aus Fall 2
sind bekannt, ungefährlich (siehe oben) und wenige; ein Checker wäre derzeit mehr
Wartungsaufwand als Nutzen. Tritt eine vierte, andere Instanz dieser Fehlerklasse auf,
wird der Checker gebaut.

**Stattdessen:** diese Dokumentation. Jeder neue Fall dieser Klasse wird hier als
weiterer nummerierter Punkt ergänzt (wie bei D-12), damit die Frage "ist das schon die
vierte Instanz?" eine Grep-Anfrage bleibt.

---

### D-14 SQLite-Ein-Schreiber-Deadlock durch verschachtelte, unabhängige Sessions

**Regel:** Jeder Kern-Aufrufer, der eine DB-`Session` über einen Aufruf in
Extension-Code hinweg offen hält, MUSS seine eigenen anstehenden Schreibvorgänge VOR
diesem Aufruf committen.

**Begründung:** Fast jeder DB-berührende `ExtensionContext`-Handle (`ctx.hosts`,
`ctx.exec`, `ctx.secrets`, `ctx.settings`, `ctx.scheduler`, `ctx.notify`, `ctx.audit`,
`ctx.actions`, `ctx.vault_use`, `ctx.connectors`) öffnet bei jedem Aufruf eine eigene,
unabhängige `session_scope()` (`ext/context.py`) — absichtlich, damit jeder
Handle-Aufruf für sich atomar ist, unabhängig vom Aufrufer. Hält der AUFRUFER aber schon
eine offene, ungeflushte Schreib-Transaktion, blockiert SQLites Ein-Schreiber-Regel
(D-02) die verschachtelte zweite Transaktion, bis `busy_timeout` (5 s) abläuft; dann
schlägt sie mit "database is locked" fehl — kein Zufall, sondern ein struktureller
Deadlock.

**Bekannte Fälle** (dieselbe Wurzelursache an zwei Stellen, plus eine wahrscheinliche
Erklärung für einen dritten):

1. **`core/gate.py::execute_action()`.** Der Aufrufer
   (`api/v1/actions.py::approve_action()`, Session über die gesamte Request-Dauer
   offen) ruft `executor.execute()` auf — Extension-Code, der z. B. `ctx.vault_use()`
   aufruft (`build_connector()` der Proxmox-Extension). Aufgetreten beim ersten
   `vm.stop` gegen einen Proxmox-API-Mock: 5 Sekunden Wartezeit, dann "database is
   locked" beim Audit-Eintrag `secret.used`. **Strukturell behoben:**
   `execute_action()` committet VOR dem Aufruf des Executors; jede Aktionsausführung
   läuft ausschließlich über diesen Pfad. Geprüft gegen eine echte Datei-DB
   (`test_execute_action_does_not_deadlock_when_executor_opens_nested_session`); die
   In-Memory-Testfixture mit `StaticPool` teilt eine physische Verbindung und hätte das
   Problem nie gezeigt — dasselbe Argument wie bei
   `test_vault_use_audit_survives_caller_session_rollback`.
2. **`services/extensions.py::enable_extension()`.** Hält `session` mit einem
   anstehenden Schreibvorgang (`record.granted_permissions`) offen, während
   `instance.setup()`/`instance.on_start()` — Extension-Code — selbst schreibende
   Handles aufrufen (bei hello-world `ctx.scheduler.register_job()` in `setup()` und
   `ctx.audit.log()` in `on_start()`). Gegen eine echte Datei-DB reproduziert
   (`test_enable_extension_does_not_deadlock_with_caller_session_held_open`).
   **Strukturell behoben:** `enable_extension()` committet vor jedem Übergang in
   Extension-Code (`setup()`, `on_start()`); `disable_extension()` bekam denselben
   Schutz vor `on_stop()`, auch ohne einen derzeit bekannten anstehenden Schreibvorgang
   an dieser Stelle — struktureller Schutz statt einer Annahme über den jeweiligen
   Aufrufer.
3. **Wahrscheinlich, nicht bewiesen:** vereinzelte "database is locked"-Fehler in
   `on_start()` einer Extension beim Neustart. Der Boot-Pfad
   (`load_enabled_from_registry()` → `enable_extension()` → `on_start()`) ist derselbe
   wie in Fall 2, was diese Fehler plausibel erklärt. Eine unabhängige Reproduktion der
   ursprünglichen Fehler gibt es nicht; die Erklärung ist deshalb nicht als bewiesen zu
   behandeln.

**Nicht zentral lösbar: eigene Routen von Extensions.** Eine über
`ctx.api.include_router()` montierte Route, die selbst `session: SessionDep` deklariert
UND einen schreibenden `ctx.*`-Handle aufruft, trägt dasselbe Risiko. Der Kern kann das
nicht verhindern, weil er nicht kontrolliert, was eine Extension-Route mit einer selbst
angeforderten Session macht. Für Extension-Autoren gilt deshalb dieselbe Regel: vor
einem schreibenden `ctx.*`-Aufruf die eigene Session committen. Kein Kern-Aufrufer ist
davon betroffen.

**Erwogen und bewusst NICHT gebaut:** ein genereller Schutz in `session_scope()`/
`ext/context.py` selbst (z. B. jeder Handle-Aufruf committet automatisch eine im
selben Task-Kontext offene Aufrufer-Session). Das würde jeden Aufrufer unsichtbar
beeinflussen, auch außerhalb der beiden behobenen Fälle, und wäre schwerer zu verstehen
als zwei gezielte Fixes. Zurückgestellt, bis ein dritter, unabhängig reproduzierter
KERN-Aufrufer (nicht der Fall der Extension-Routen oben) dieses Muster zeigt — dieselbe
Haltung wie D-13 gegenüber einem Lint-Check.

---

### D-15 Extension-Routen kennen den aufrufenden Nutzer nicht

**Status: gelöst.** `ctx.api.current_actor` ist eine FastAPI-Dependency
(`actor: Actor = Depends(ctx.api.current_actor)`), die `Actor.user(id, username)` des
angemeldeten Nutzers liefert (401 ohne gültigen Token, wie jede Kern-Route). Eine
Extension setzt `actor` als `proposed_by` im `ActionRequest` für `ctx.actions.propose()`
und als `actor` bei `ctx.audit.log(..., actor=...)`. Im Audit-Log steht dann der Mensch hinter
dem Klick, die Extension nur als `detail.via`.

**Ausgangsproblem:** `ApiHandle.include_router()` (`ext/context.py`) injizierte für
eine mit `ctx.api.include_router(router, permission=...)` montierte Route nur eine
Permission-Prüfung (`Depends(require_permission(permission))`). `require_permission()`
löst intern zwar den `CurrentUser` auf, um die RBAC-Prüfung zu machen, gibt ihn aber nur
als `dependencies=[...]`-Deklaration zurück, nie an die Route-Funktion selbst. Eine
Extension-Route konnte den aufrufenden Nutzer deshalb nicht identifizieren — anders als
eine Kern-Route (`trigger_host_action()` in `api/v1/hosts.py` deklariert
`user: CurrentUser` direkt und nutzt `Actor.user(user.id, user.username)`).

Aufgefallen ist das an zwei Stellen:

1. **nexus-soc (Nodvard Shield).** Die Endpunkte `/confirm`/`/dismiss` des
   Incident-Widgets änderten `incident.status`, ohne festhalten zu können, WER geklickt
   hat. (`/chat` war nicht betroffen: Dort nennt `Actor.ai(model=...)` korrekt die KI als
   Urheber, der Nutzer hat nur gefragt.)
2. **scripts.** `POST .../scripts/{id}/run` schlägt eine `script.run`-Aktion über
   `ctx.actions.propose()` vor, musste aber `Actor.extension("scripts")` statt des
   klickenden Admins angeben. Das Audit-Log konnte nicht zeigen, wer den Lauf ausgelöst
   hat.

Host-Aktionen der Proxmox-Extension waren nicht betroffen: Sie laufen über die
Kern-Route `POST /hosts/{id}/actions/{type}`, die `CurrentUser` bereits kennt.

**Umsetzung:** Zunächst zurückgestellt, weil eine Änderung am Extension-Vertrag alle
Extensions betrifft und eine eigene Entscheidung verdient. Umgesetzt als zusätzliche,
optionale Dependency: Bestehende Extensions laufen unverändert weiter, ohne `actor`
bleibt die Extension selbst der Urheber. Umgestellt sind unter anderem scripts
(manueller Lauf), nexus-soc (Bestätigen, Verwerfen, Erledigt, Wieder öffnen), backups
(Job anlegen/bearbeiten, Erneut versuchen) und gameserver (Start, Stopp, Neustart, Welt
sichern).
