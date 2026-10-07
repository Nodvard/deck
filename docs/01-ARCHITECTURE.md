# 01 — Architektur

> Hinweis: Das Produkt heißt **Nodvard Deck**, im Code `nodvard_deck`. Früher hieß es „Lattice“;
> welche alten Namen weiter gelten und welche bewusst bleiben, steht in [§ Alte und neue Namen](#alte-und-neue-namen).

## 1. Die drei Schichten

```
┌──────────────────────────────────────────────────────────────┐
│  CLIENTS                                                     │
│  React-Web (statisch vom Backend) │ Flutter-Android (APK)    │
└────────────────────┬─────────────────────────────────────────┘
                     │  eine REST/WS-API  (/api/v1, /ws)
┌────────────────────▼─────────────────────────────────────────┐
│  CORE — generisch, umbenennbar, kennt keine Extension        │
│                                                              │
│  Auth/RBAC · Vault · Audit · Events · Scheduler · Gate       │
│  Execution (SSH) · Hosts · Settings/Branding · Ext-Host      │
└────────────────────┬─────────────────────────────────────────┘
                     │  nodvard_sdk  (Python-Vertrag)
                     │  window.__nodvardDeck + UI-Kit (React-Vertrag)
┌────────────────────▼─────────────────────────────────────────┐
│  EXTENSIONS — alles Infrastruktur-Spezifische                │
│  proxmox · shield · terminal · files · scripts ·             │
│  service-matrix · backups · gameserver · …                   │
└──────────────────────────────────────────────────────────────┘
```

**Die eine Regel, aus der alles andere folgt:** Abhängigkeiten zeigen nur nach oben.
Eine Extension importiert `nodvard_sdk`. Der Kern importiert **nie** eine Extension und
kennt keinen Extension-Namen. Der Kern kennt nur *Fähigkeiten* (Protokolle), nie
*Anbieter*.

Das ist maschinell prüfbar und wird geprüft — `scripts/check_core_purity.py` läuft in CI
und schlägt fehl, wenn im Code von `backend/src/nodvard_deck/` ein Wort aus einer Sperrliste
(`proxmox`, `nexus`, `ollama`, `ntfy`, `nextcloud`, `truenas`, `syncthing`,
`teleport`, `docker`, dazu `shield` als eigenes Wort) oder das Paket-Präfix der Extensions
(`nodvard_deck_ext_`) auftaucht. Bei `shield` zählen `asyncio.shield(…)` und Symbolnamen mit
Bindestrich wie `shield-check` nicht; jede dieser Ausnahmen ist in `backend/tests/test_core_purity.py`
an ihrer Fundstelle belegt.
Kommentare und Docstrings sind ausgenommen: verboten ist die *Abhängigkeit*, nicht die
Erwähnung — ein Protokoll darf und soll in seiner Dokumentation Beispiele nennen.

Die Prüfung läuft automatisch, weil eine Regel allein nicht reicht: Ohne sie erodiert
die Grenze innerhalb weniger Wochen, weil "nur diese eine Sonderbehandlung" immer
plausibel klingt.

---

## 2. Prozessmodell

**Ein Prozess.** Extensions sind Python-Pakete, die in den Kern-Prozess geladen werden —
keine Sidecars, keine Subprozesse, kein gRPC.

Begründung: Zielhardware ist ein Raspberry Pi oder ein kleiner x86-Rechner; ein Prozess pro Extension
wäre bei acht Extensions der teuerste Teil des Systems. Und: es gibt genau einen
Administrator, der alle Extensions selbst schreibt oder bewusst installiert.

**Was dazu offen gesagt werden muss:** eine Extension läuft mit den
vollen Rechten des Kerns. Das Berechtigungsmodell (Manifest-Permissions, siehe
[02](02-EXTENSION-API.md)) ist eine *Struktur- und Nachvollziehbarkeitsmaßnahme*, keine
Sandbox — es verhindert Versehen und macht Zugriffe im Audit sichtbar, es hält keinen
bösartigen Code auf. Das ist dieselbe Vertrauensposition wie bei WordPress-Plugins,
Home-Assistant-Custom-Components oder Proxmox-Hooks. Sie muss benannt, nicht versteckt
werden.

Damit ein späterer Wechsel möglich bleibt, ist der `ExtensionContext` bewusst so
geschnitten, dass jeder Aufruf serialisierbar ist (Daten rein, Daten raus, keine
geteilten Objekte über die Grenze). Wer später Isolation braucht, tauscht die
Context-Implementierung gegen einen RPC-Proxy aus, ohne dass eine Extension sich ändert.

### Laufzeit im Prozess

| Teil | Technik |
|---|---|
| HTTP/WS | FastAPI auf uvicorn, **ein** Worker |
| Hintergrundschleifen (Watcher) | `asyncio.Task`, vom Extension-Host verwaltet und beim Stop sauber abgebrochen |
| Geplante Jobs | ein APScheduler-Objekt, SQLAlchemy-Jobstore |
| Blockierendes | `asyncio.to_thread` / dedizierter Executor, nie im Loop |
| SSH | ein `asyncssh`-Verbindungspool, geteilt von Terminal, Scripts und KI |

---

## 3. Der Lebenszyklus eines Starts

```
1. Config laden (.env → Settings)
2. Vault entsperren (Master-Key-Datei oder Passphrase)
3. DB verbinden, Alembic-Migrationen prüfen (Kern + je Extension ein Branch)
4. Kern-Dienste konstruieren (Events, Audit, Scheduler, Gate, Execution, Notify)
5. Extensions entdecken → Manifeste lesen (ohne Code-Import!)
6. Je aktivierte Extension: importieren, Version prüfen, Permissions prüfen,
   setup(ctx) aufrufen  → sie registriert Routen/Widgets/Connectors/Jobs/Capabilities
7. Router montieren, Widget-Katalog einfrieren
8. Scheduler starten
9. Je Extension: on_start(ctx) → Hintergrundtasks anlaufen lassen
10. HTTP-Server bereit
```

**Umgebungsvariablen (Schritt 1).** Jede Einstellung aus `config.py` heißt als Variable
`NODVARD_DECK_<FELD>` (z. B. `NODVARD_DECK_ENV`, `NODVARD_DECK_DATABASE_URL`). Die früheren
Namen `LATTICE_<FELD>` gelten als Rückfall weiter. Reihenfolge, vorne gewinnt: Argumente im
Code, Umgebung (neu), Umgebung (alt), `.env` (neu), `.env` (alt), Secret-Dateien. Die Prozess-
Umgebung schlägt also immer die `.env`, und der neue Name schlägt den alten. Beim Start
steht einmalig eine Warnung im Log, welche alten Namen noch benutzt werden (nur die Namen,
nie die Werte).

Schritt 5 vor Schritt 6 ist wichtig: das Manifest muss lesbar sein, **ohne** den
Extension-Code auszuführen. Nur so kann eine deaktivierte oder inkompatible Extension in
der Registry-UI erscheinen, ohne zu laufen — und nur so kann eine kaputte Extension den
Start nicht verhindern.

**Fehlerverhalten:** wirft eine Extension in `setup`/`on_start`, wird sie als
`state=error` mit der Exception in der Registry markiert und **übersprungen**. Der Kern
startet trotzdem. Eine defekte Erweiterung darf nie das Dashboard lahmlegen — ein
bekannter Fehlermodus von Dashboards, die Module per Iframe einbinden (ein Iframe-Fehler =
ein toter Tab).

---

## 4. Das Aktions-Gate

Jede zustandsverändernde Aktion einer Extension läuft durch **eine** Stelle im Kern.
Das ist kein Feature von Nodvard Shield, sondern ein Kern-Dienst, den auch das
Script-Repository, das Gameserver-Modul und der Dateimanager benutzen — es ist bewusst
als generischer Baustein angelegt.

```
Extension                Core Gate                        Ergebnis
   │                         │
   ├─ propose(ActionRequest)─▶│
   │                         ├─ 1. Extension-Permission?      → deny
   │                         ├─ 2. RBAC des Akteurs?          → deny
   │                         ├─ 3. Sperrliste (Muster)?       → deny
   │                         ├─ 4. Anti-Flapping / Rate?      → deny
   │                         ├─ 5. Autonomie-Modus?
   │                         │     propose  → require_confirmation   (DEFAULT)
   │                         │     full     → allow, falls risk ≤ Schwelle
   │                         ├─ 6. IMMER: Audit-Zeile schreiben
   │                         │            (inkl. reason / BEGRUENDUNG)
   │                         ▼
   │                   actions-Tabelle: status = proposed | approved | denied
   │                         │
   │                         ├─ proposed → WS-Event → Karte in der UI → Klick
   │                         └─ approved → ActionExecutor läuft → Ergebnis + Audit
```

Der entscheidende Punkt: **Vorschlagen und Ausführen sind getrennte Schritte mit einem
persistenten Datensatz dazwischen.** Wo ein einziger Aufruf eine Abhilfe zugleich
auswertet *und* ausführt, lässt sich "erst bestätigen" nachträglich nicht sauber
einbauen. Durch die Trennung ist der Bestätigungsmodus der *natürliche* Zustand und volle
Autonomie die Ausnahme, die einen Schalter braucht.

Nebeneffekt: der Nutzer bekommt keine Meldung über eine Aktion, die gar nicht
stattgefunden hat. Der Text in Benachrichtigung und UI wird aus dem `actions`-Datensatz
gebaut, nicht aus der Absicht eines Modells.

**Ausführung im Hintergrund.** Ist eine Aktion genehmigt (Klick, Modus `full` oder Dauerfreigabe),
schreibt das Gate `executing` fest und startet den `ActionExecutor` als eigenen
`asyncio.Task` mit eigener Datenbank-Session (`core/gate.py`, `start_execution`). Die
HTTP-Anfrage wartet höchstens 20 s darauf und antwortet sonst mit `202 executing`; das
Ergebnis samt Audit-Zeile schreibt der Task selbst. Er endet immer mit `succeeded` oder
`failed` — auch wenn der Executor wirft, nach 60 min noch hängt (`EXECUTE_TIMEOUT_S`,
Notbremse über den Grenzen der Extensions) oder das Dashboard beendet wird. Was ein harter
Absturz auf `executing` stehen lässt, setzt der Kern beim nächsten Start auf `failed`.
`ctx.actions.propose()` wartet ohne Angabe bis zum Ende (Hintergrund-Jobs); Extension-Routen
geben `wait_s=REQUEST_WAIT_S` mit und melden danach ggf. `executing`.

**Dauerfreigabe.** Ein Vorschlag kann sich auf eine Freigabe berufen, die ein Mensch vorab erteilt hat
(`ActionRequest.standing_approval`, [02 §3](02-EXTENSION-API.md#3-capabilities--der-kern-der-entkopplung); bisher nur
geplante Skripte). Nach Sperrliste und Anti-Flapping prüft das Gate bei jedem Vorschlag selbst, ob diese Person noch
aktiv ist und Dauerfreigaben sowie Aktionen dieser Risikostufe freigeben darf; Vorschläge einer KI laufen nie darüber.
Gilt sie, ersetzt sie den Klick, unabhängig von `autonomy.mode`, und die Person steht als Freigebende an der Aktion;
sonst wird ein normaler Vorschlag daraus. Was passiert ist, hängt das Gate selbst an `reason` an, das Datum der
Freigabe in der eingestellten Zeitzone des Dashboards.

**Einstellungen (global, pro Extension überschreibbar):**

```
autonomy.mode          = "propose" | "full"        default: propose
autonomy.max_risk      = "low" | "medium" | "high" default: low
autonomy.window        = optional Zeitfenster für "full"
```

---

## 5. Hosts gehören dem Kern, nicht der Extension

Das ist die zweite Stelle, an der die Extension-Grenze leicht falsch gezogen wird.

Der Kern besitzt die Tabelle `hosts`. Extensions **entdecken** Hosts
(`HostProvider`-Capability) und schreiben sie dort hinein, mit Rückverweis
(`provider_ext_id`, `provider_ref`). Ein Host kann auch manuell angelegt werden.

Damit adressieren Terminal, Script-Ausführung, Dateimanager, Audit-Log und
Berechtigungen alle **dieselbe** Host-Identität — unabhängig davon, ob sie aus Proxmox,
aus einer Docker-API, aus einer Inventar-Datei oder aus einem Formular kam. Ohne diese
Regel hätte jede Extension ihre eigene Host-Liste: eine im Monitoring-Skript, eine für
die Web-Links, eine im SSH-Zugangsdienst, eine im Kachel-Dashboard — vier Wahrheiten,
die auseinanderdriften.

---

## 6. Branding als Konfigurationswert

```
GET /api/v1/branding      (ohne Auth — die Login-Seite braucht es)
→ { "product_name": "...", "short_name": "...", "logo_url": "...",
    "favicon_url": "...", "colors": { "accent": "#…", … },
    "login_subtitle": "...", "support_url": "..." }
```

Das Frontend schreibt die Farben beim Start in CSS-Custom-Properties auf `:root`.
Kein Rebuild, kein Neustart, keine Umgebungsvariable. Logos liegen als Upload im
Daten-Volume. Ein SVG-Logo wird beim Hochladen geprüft (keine Skripte, keine Verweise
aus der Datei hinaus), jedes hochgeladene Logo wird mit einer Sandbox-CSP ausgeliefert;
der Support-Link nimmt nur `http:`, `https:` und `mailto:` an ([04-API](04-API.md)).

Im Code gilt: **kein Literal für Produktname oder Farbe** außerhalb von
`branding.py` (Backend-Defaults) und der CSS-Variablen-Definition (Frontend). Auch
`<title>`, E-Mail-Absender, Push-Titel und APK-Label ziehen aus derselben Quelle.
Der CI-Purity-Check deckt das mit ab. Bewusste Ausnahme: Die Anmeldeseite zeigt immer
„© 2026 Nico Benks · Nodvard Deck“ (`copyrightLine()` in `frontend/src/lib/branding.ts`, ab 2027 mit
Jahresspanne). Eigenes Branding ändert, wie die Oberfläche heißt, nicht, wem die Software gehört.

---

## 7. Sicherheitsposition, zusammengefasst

| Bereich | Position |
|---|---|
| Extensions | Vertrauenswürdiger Code. Permissions = Struktur + Audit, keine Sandbox. |
| KI-Aktionen | Aus KI-Text legt Nodvard Shield höchstens eine feste Aktion an: den Neustart eines Containers, dessen Absturz der Vorfall meldet. Befehl und Begründung baut der Code selbst, der Server kommt aus dem Vorfall; alles andere bleibt Text, der Chat legt nie eine Aktion an. Container-Logs und Docker-Fehlermeldungen gehen als fremde Daten (keine Anweisungen) an die KI. Dazu: Sperrliste für katastrophale Muster, Begründungspflicht, Anti-Flapping, Default = Bestätigung, nie über eine Dauerfreigabe. |
| Secrets | Nie im Klartext in DB, Antwort oder Log. Extensions bekommen Handles. |
| SSH | Eine Identität aus dem Vault, Known-Hosts-Pinning statt `StrictHostKeyChecking=no`. |
| API | Alles authentifiziert außer `/branding`, `/health`, `/auth/login`. Routen von Erweiterungen verlangen ohne Angabe eine Anmeldung; ohne Anmeldung nur mit `public=True` und der Manifest-Berechtigung `api.public` (steht beim Einschalten im Protokoll). Mehr als 1 MiB Body bekommt kein Endpunkt zu lesen, auch ohne Anmeldung nicht (darüber `413`; Uploads haben eigene, höhere Grenzen). |
| Audit | Append-only, jede Aktion, auch die abgelehnten und die nur vorgeschlagenen. |
| Netz | Kein Port nach außen nötig (geht auch hinter DS-Lite/CGNAT); Zugriff über Reverse-Proxy im LAN oder ein VPN wie Tailscale/WireGuard. |

**Bewusst nicht gelöst:** Multi-Tenancy, horizontale Skalierung,
Extension-Sandboxing, SSO/OIDC. Alle vier sind später additiv möglich und keiner davon
hat in einem Ein-Administrator-Homelab einen Nutzen, der die Komplexität rechtfertigt.

## Alte und neue Namen

Das Projekt hieß früher „Lattice“. Die technischen Namen sind auf **Nodvard Deck** umgestellt.
Damit bestehende Installationen, alte Browser-Tabs und Erweiterungen von Dritten weiterlaufen,
gelten viele alte Namen **parallel** weiter; einige bleiben **bewusst** ganz stehen. Der Wächter
`scripts/check_legacy_names.py` (läuft als Test) sorgt dafür, dass kein alter Name neu eingeführt
wird. Er hat zwei Positivlisten, beide mit Begründung je Eintrag: `KEPT_NAMES` für „Lattice“ und
`KEPT_SHIELD` für die alte Kennung von Nodvard Shield (dort nennt jeder Eintrag zusätzlich die Dateien, in denen er
gilt; ein Test prüft, dass jede Angabe noch gebraucht wird). Diese beiden Listen sind maßgeblich.

**Umbenannt – der alte Name funktioniert weiter (Übergang):**

| Neu | Alt | Wie der alte weiterlebt |
|---|---|---|
| Python-Paket `nodvard_deck` | `lattice` | `python -m lattice.admin`/`boot`/`migrate`/`rescue` starten weiter (kleine Startdateien) |
| SDK `nodvard_sdk`, `NodvardExtension`, `NodvardError` | `lattice_sdk`, `LatticeExtension`, `LatticeError` | Alias: dieselben Objekte, `import lattice_sdk` geht weiter |
| Erweiterungs-Pakete `nodvard_deck_ext_<id>` | `lattice_ext_<id>` | Kein Alias für die eingebauten (der `entrypoint` kommt frisch aus `extension.toml`); fremde Pakete mit altem Namen laden weiter |
| Entry-Point-Gruppe `nodvard_deck.extensions` | `lattice.extensions` | Wird weiter gelesen |
| Umgebungsvariablen `NODVARD_DECK_*` | `LATTICE_*` | Rückfall, wenn die neue nicht gesetzt ist |
| Refresh-Cookie `nodvard_deck_refresh` | `lattice_refresh` | Beide werden geschrieben, gelesen und beim Abmelden gelöscht ([04-API](04-API.md)) |
| Frontend: `window.__nodvardDeck`, Ereignisse `nodvard-deck:*`, CSS `.nodvard-deck-focus` | `window.__lattice`, `lattice:*`, `.lattice-focus` | Laufen parallel ([02-EXTENSION-API](02-EXTENSION-API.md)) |
| Browser-Speicher `nodvard-deck.*` | `lattice.*` | Werden beim ersten Laden übernommen |
| Image, Compose-Dienst, Container `nodvard-deck`, `deploy-nodvard-deck-1` | `lattice`, `deploy-lattice-1` | Beim ersten Deploy übernommen ([deploy/README](../deploy/README.md)) |

**Bleiben bewusst:**

| Name | Warum |
|---|---|
| Datenbankdatei `lattice.db` (auch `db/lattice.db` in Sicherungen) | Umbenennen bräuchte eine Migration; nach einem Rollback öffnete das alte Image eine leere Datenbank |
| Docker-Volume `deploy_lattice_data` | Sonst startet das Dashboard leer |
| Zielordner `~/lattice-deploy-test` von `scripts/deploy_pi.sh` (Standardwert von `DEPLOY_ROOT`) | So ausgelieferte Installationen hängen daran; über `DEPLOY_ROOT` frei wählbar |
| Linux-Benutzer `lattice` (UID 1000) im Container | Dateirechte im Datenordner |
| Vorgabe `lattice` für `username` bei `POST /hosts/{id}/credentials/generate-key` (die Oberfläche schlägt beim Erzeugen `nodvard` vor und schickt den Namen immer mit); auf bestehenden Servern der Benutzer `lattice`, ältere Schlüssel mit Kommentar `lattice@<Name>` und alte sudo-Dateien `/etc/sudoers.d/lattice-<benutzer>` | Ältere Aufrufer der API schicken oft keinen Namen, ein anderer Standard wäre für sie eine stille Verhaltensänderung; bestehende Server sind so eingerichtet. Neue Schlüssel bekommen `nodvard@<Name>`, neue sudo-Regeln `/etc/sudoers.d/nodvard-<benutzer>`; eine alte Regel entfernt der Einrichtungsbefehl (mit sudo) nur, wenn sie genau die früher von Nodvard Deck angelegte ist |
| Namen auf verwalteten Servern (systemd-Einheiten `lattice-upgrade-*`, Rollback-Tags, Zustandsordner) | Fortsetzen und Zurückrollen von Updates hängen daran |
| Import-Map-Pfad `/lattice-shim/` | Alte Tabs verweisen darauf |
| API-Wert `source: "lattice"` | Dokumentierter Wert, Clients werten ihn aus |

### Nodvard Shield: `nexus-soc` → `shield` (seit 0.7)

Die Erweiterung **Nodvard Shield** (bis 0.6 „Nexus SOC“) hatte die technische Kennung `nexus-soc`. Seit 0.7
heißt sie `shield`, im Manifest steht `legacy_ids = ["nexus-soc"]` (Mechanik:
[02 „Erweiterung umbenennen“](02-EXTENSION-API.md#erweiterung-umbenennen-legacy_ids)). Es gibt **keine Migration**:
Gespeichertes wird weder kopiert noch umbenannt, ein Rückweg aufs alte Image findet alles unverändert vor.

**Umbenannt – der alte Name funktioniert weiter (Übergang, in allen 1.x):**

| Neu | Alt | Wie der alte weiterlebt |
|---|---|---|
| Kennung `shield` (`extension.toml`) | `nexus-soc` | `legacy_ids`. Bei bestehenden Installationen bleibt die Registry-Zeile `nexus-soc` die **Speicher-Kennung**: Einstellungen, Zustand, Rechte und Zeitpläne hängen weiter an ihr. Neue Installationen legen `shield` an |
| Ordner `extensions/shield/`, Paket `nodvard_deck_ext_shield` | `extensions/nexus-soc/`, `nodvard_deck_ext_nexus_soc` | Kein Alias (Erweiterungen importieren einander nicht). Liegt der alte Ordner noch da, wird Shield nicht geladen ([deploy/README](../deploy/README.md)) |
| Adressen `/api/v1/ext/shield/…` | `/api/v1/ext/nexus-soc/…` | Dieselben Routen hängen zusätzlich unter der alten Adresse, gleiche Anmeldeprüfung, im OpenAPI-Schema `deprecated` ([04 §7](04-API.md#7-kompatibilität)) |
| Kern-Adressen `/api/v1/extensions/shield/…` (Einstellungen, Geheimnisse, Test, Bundle) | `/api/v1/extensions/nexus-soc/…` | Die alte Kennung wird aufgelöst, Antworten nennen in `id` die neue |
| Seiten `/ext/shield/soc`, `/settings/extensions/shield` | `/ext/nexus-soc/soc`, `/settings/extensions/nexus-soc` | Die Oberfläche leitet weiter (Abfrage und Anker bleiben). Gespeicherte Links in Meldungen, Push und Lesezeichen gehen so weiter |
| Dashboard-Kacheln unter `shield` | Einträge unter `nexus-soc` | Behalten Platz, Größe und Sichtbarkeit; der alte Eintrag bleibt für einen Rückweg im Layout |
| Neue Protokoll-Einträge `shield.*` (z. B. `shield.incident`), Akteur `shield` | `nexus_soc.*`, `nexus-soc` | Alte Einträge bleiben, die Oberfläche zeigt bei beiden den Namen. Der Filter `GET /audit?action=shield.incident` findet die älteren `nexus_soc.incident` nicht |
| Logger `nodvard_deck.ext.shield`, Live-Kanäle `ext.shield.*`, Links in neuen Meldungen (`/ext/shield/soc`) | `…nexus-soc…` | Live-Nachrichten gehen zusätzlich an die alten Kanäle, damit offene Tabs mit altem Stand sie bekommen |

**Bleiben bewusst** (gespeichert oder auf den Servern; ein Rückweg hängt daran, wie bei `lattice.db`):

| Name | Warum |
|---|---|
| Registry-Zeile `nexus-soc` bestehender Installationen | Darin liegen alle Einstellungen; das alte Image findet sie nach einem Rückweg unverändert |
| Tabellen `ext_nexus_soc_*` und ihre Indizes `ix_ext_nexus_soc_*` | Umbenennen bräuchte eine Migration; der Rückweg ginge dann nur noch über die Notseite |
| Alembic-Revisionen der Erweiterung, ihre Dateinamen und der Zweig `nexus-soc` (Kopf `b8c9d0e1f2a3`) | Ausgelieferte Revisionen ändern sich nie; nur der Ordner ist mit umgezogen |
| Aktionsarten `nexus_soc.{restore,delete,install,upgrade,reboot,ban}` | Offene Vorschläge und Freigaben tragen sie, und das Gate findet den Ausführer nur über die Art. Die Oberfläche zeigt statt der Art ihren Namen |
| Geheimnis `nexus-soc-ollama-key` und das Recht `secrets.read:nexus-soc-*` | Der Schlüssel für Nodvard KI wird nur über sein Label gefunden |
| Rechte `soc.read` und `soc.manage` | Können in eigenen Rollen stehen; die Rechteprüfung kennt keinen Alias |
| Ordner auf den Servern: `/var/lib/nexus-quarantine` (Quarantäne), `/var/lib/nexus-updates` (laufende Updates) | Die Pfade stehen in gespeicherten Funden, und laufende Updates werden über den Ordner fortgesetzt |
| Alte Einträge in Protokoll, Meldungen, Läufen und Aktionen | Verlauf, wird nicht umgeschrieben |
