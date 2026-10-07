# 02 — Die Extension-Schnittstelle

Der Kern kennt keine Extension. Er kennt **Fähigkeiten**. Eine Extension meldet an, welche
davon sie erfüllt, und der Kern benutzt sie generisch.

Dieses Dokument ist die Spezifikation. Der zugehörige, importierbare Vertrag liegt in
`sdk/python/nodvard_sdk/`.

---

## 1. Anatomie einer Extension

```
extensions/shield/
├─ extension.toml            ← Manifest, ohne Code-Import lesbar
├─ pyproject.toml            ← optional; für pip-installierbare Extensions
├─ src/nodvard_deck_ext_shield/
│  ├─ __init__.py            ← enthält  class Extension(NodvardExtension)
│  ├─ api.py                 ← APIRouter
│  ├─ connectors.py          ← Ollama-, ntfy-Connector-Typen
│  ├─ capabilities.py        ← ActionExecutor, AIProvider …
│  ├─ widgets.py             ← deklarative WidgetSpecs
│  ├─ pipeline.py            ← Incident-Queue/Batching
│  ├─ models.py              ← eigene Tabellen (Präfix ext_shield_; ältere: ext_nexus_soc_)
│  └─ migrations/            ← eigener Alembic-Branch
└─ frontend/
   ├─ package.json
   └─ src/index.tsx          ← registerPage/registerWidget, ESM-Bundle
```

### Manifest (`extension.toml`)

```toml
[extension]
id          = "shield"             # stabil, kleinschreibung, Namensraum für alles
legacy_ids  = ["nexus-soc"]        # optional: frühere Kennungen, siehe „Erweiterung umbenennen“
name        = "Nodvard Shield"
version     = "0.1.0"
api_version = "1.0"                # SemVer gegen nodvard_sdk.API_VERSION
author      = "…"
description = "Autonome Diagnose und Remediation für die eigene Flotte."
icon        = "shield-check"
entrypoint  = "nodvard_deck_ext_shield:Extension"
frontend    = "frontend/dist/index.js"    # optional
requires    = ["terminal>=0.1"]           # andere Extensions, optional
enable_on   = ["host_credential"]         # optional: siehe unten
category    = "servers"                   # optional: Gruppe in der Modul-Auswahl des Einrichtungsassistenten
sort_order  = 10                          # optional: Reihenfolge in der Gruppe (Standard 100)

# Was die Extension vom Kern verlangt. Der Admin bestätigt das bei der Installation.
# Der Kern erzwingt es bei JEDEM ctx-Aufruf.
permissions = [
  "hosts.read",
  "hosts.execute",          # Befehle auf Hosts ausführen (nur über das Gate)
  "secrets.read:ssh-fleet",
  "audit.write",
  "notify.send",
  "schedule.register",
  "net.outbound:192.168.1.0/24",
]

[settings]                  # JSON-Schema; der Kern rendert daraus die Settings-Seite
schema = "settings.schema.json"
```

**Paketname (`entrypoint`):** Die Python-Pakete der mitgelieferten Erweiterungen heißen
`nodvard_deck_ext_<id>` (Bindestriche der ID werden zu `_`, aus `service-matrix` wird `nodvard_deck_ext_service_matrix`) und
liegen unter `extensions/<id>/src/`. Das ist die Konvention für neue Erweiterungen, der Kern
erzwingt sie nicht: Er importiert genau das Modul, das `entrypoint` nennt, und liest das Manifest
bei jedem Start frisch von der Platte. **Erweiterungen von Dritten mit der alten Konvention
`lattice_ext_<id>` (oder mit einem ganz anderen Paketnamen) laden deshalb unverändert weiter**; sie
müssen nichts umbenennen. Die mitgelieferten Erweiterungen gibt es unter dem alten Namen nicht
mehr (auch nicht als Alias): Erweiterungen importieren einander ohnehin nicht (andere Erweiterungen
erreicht man nur über `ctx.events` und `requires` + `ctx.capabilities.query()`, siehe „Nicht am
Kontext“ weiter unten). Wer doch einmal ein Modul einer mitgelieferten Erweiterung direkt importiert
hat, nimmt den neuen Namen.

**SDK-Abhängigkeit (pip-installierbare Erweiterungen):** Die SDK-Distribution heißt jetzt
`nodvard-sdk` (Importname `nodvard_sdk`). Der alte Importname `lattice_sdk` steckt in derselben
Distribution und funktioniert weiter, eine Distribution `lattice-sdk` gibt es aber nicht mehr. Wer in
seiner `pyproject.toml` `lattice-sdk` als Abhängigkeit einträgt, stellt sie auf `nodvard-sdk` um
(oder lässt sie weg: das SDK bringt der Kern ohnehin mit).

**`category` und `sort_order` (optional):** Der Einrichtungsassistent zeigt die installierten Module
als Kacheln, gruppiert nach `category` (bekannt: `servers`, `security`, `tools`, `connections`,
`example`; alles andere und „fehlt“ landet unter „Weitere Module“) und innerhalb der Gruppe nach
`sort_order` (kleiner = weiter oben, bei Gleichstand nach Name). Die Werte stehen auch in
`GET /extensions`. Module mit `example` (Beispiele für Entwickler wie hello-world) zeigt der Assistent nicht; sie
stehen nur unter Einstellungen → Erweiterungen.

**`enable_on` (optional):** Anlässe, bei denen der Kern die Erweiterung von selbst einschaltet,
damit sie für Einsteiger nicht erst in den Einstellungen gesucht werden muss. Bekannt ist
`host_credential` (ein Server hat einen SSH-Zugang bekommen; so schaltet sich Terminal ein). Der
Kern kennt dafür keine Erweiterung beim Namen, er liest nur diese Kennzeichnung. Eingeschaltet
wird **nur, wenn niemand die Erweiterung je bewusst ein- oder ausgeschaltet hat** – ein
Nutzer, der sie ausgeschaltet hat, bleibt unangetastet. Das Protokoll hält
`extension.auto_enabled` fest. Bei Installationen, die es schon vor dieser Funktion gab, bleiben
bereits angelegte Erweiterungen unberührt; nur neu hinzugekommene zählen als „unberührt“.

**Warum das Manifest eine Datei und keine Python-Konstante ist:** Der Kern muss
Manifeste aller — auch der deaktivierten und der kaputten — Extensions lesen können,
ohne deren Code zu importieren. Ein Import ist Codeausführung; eine defekte oder
abgeschaltete Extension darf nicht laufen, nur weil sie in der Registry angezeigt wird.

### Erweiterung umbenennen: `legacy_ids`

Die Kennung (`id`) ist der Namensraum für alles. Soll eine Erweiterung trotzdem eine neue Kennung bekommen,
trägt sie die frühere in `legacy_ids` ein (so ist Nodvard Shield seit 0.7 von `nexus-soc` zu `shield` umgezogen):

```toml
[extension]
id         = "neuer-name"
legacy_ids = ["alter-name"]
```

Nach mehreren Umbenennungen stehen alle früheren Kennungen darin, die jüngste zuerst
(`["mittlerer-name", "alter-name"]`). Ohne `legacy_ids` (der Normalfall) ändert sich nichts.

**Grundidee: Die Kennung wechselt, die gespeicherten Daten nicht.** Nichts wird kopiert, umbenannt oder migriert.
Ein Rückweg aufs alte Image findet seine Daten unverändert vor.

**Regeln im Manifest:** Jede alte Kennung ist eine gültige Kennung (`a-z`, `0-9`, `-`), keine kommt doppelt vor, und
die eigene `id` steht nicht darin. Sonst lädt die Erweiterung nicht.

**Speicher-Kennung.** Der Kern sucht die Registry-Zeile so:
1. Gibt es eine Zeile mit der neuen Kennung, gilt sie.
2. Sonst gilt die erste vorhandene Zeile in der Reihenfolge von `legacy_ids`. Das Protokoll meldet
   „Erweiterung 'neuer-name' nutzt den gespeicherten Stand von 'alter-name'.“
3. Gibt es keine, entsteht wie bei jeder neuen Erweiterung eine Zeile mit der neuen Kennung.

Die `id` dieser Zeile ist die **Speicher-Kennung**. Daran hängt alles, was dauerhaft gespeichert ist und nach einem
Rückweg gelesen wird:
- Einstellungen, Zustand und Rechte (die Registry-Zeile selbst),
- die Merker `extension.untouched.<Speicher-Kennung>` und `extension.test.<Speicher-Kennung>`,
- die Zeitpläne (`jobs.ext_id` und damit der Laufverlauf); es entstehen keine doppelten Jobs,
- der Datenordner (siehe unten).

Alles andere läuft unter der **neuen** Kennung: Adressen `/api/v1/ext/<id>/…`, Seiten, Kacheln, Fähigkeiten, neue
Aktionen, Meldungen, Protokolleinträge, Logger und Live-Kanäle.

**Was mit dem alten Namen weiter gilt:**
- **Adressen:** Der Kern hängt die Routen zusätzlich unter jeder alten Kennung ein (`/api/v1/ext/<alt>/…`, gleiche
  Anmeldeprüfung, im OpenAPI-Schema als veraltet markiert, in allen 1.x). Die Kern-Adressen `/api/v1/extensions/<alt>/…`
  und das Bundle lösen die alte Kennung auf. Gespeicherte Links, offene Tabs mit altem Bundle und die App laufen so ohne
  Änderung weiter. `request.url_for` baut immer die neue Adresse. Neuer Code nutzt nur die neue Kennung; die alten nennt
  die API in `legacy_ids` bzw. `legacy_ext_ids` ([04 §7](04-API.md#7-kompatibilität)).
- **Oberfläche:** Die Web-Oberfläche folgt der Umbenennung selbst. `/ext/<alt>/<Seite>` und
  `/settings/extensions/<alt>` leiten ersetzend auf die neue Kennung weiter (Abfrage und Anker bleiben). Dashboard-Kacheln
  behalten Platz, Größe und Sichtbarkeit. Meldungen, Protokoll und Aktionen zeigen den Namen der Erweiterung, auch zu
  Einträgen unter der alten Kennung.
- **Tabellen:** Erlaubt sind die Präfixe aller Kennungen (`ext_neuer_name_*`, `ext_alter_name_*`). Bestehende Tabellen
  behalten ihren Namen, neue bekommen den neuen Präfix. Die Tabellen gehören allen Kennungen gemeinsam.
- **Migrationen:** Ausgelieferte Revisionen, ihr Zweigname und ihre Dateinamen bleiben unverändert, nur der Ordner zieht
  mit um.
- **Aktionen:** `ctx.actions.list()`, `result()` und `proposer_labels()` sehen auch Vorschläge unter einer alten Kennung.
  Aktionsarten ändern sich nicht; ausgeführt wird über die Art.
- **Live-Nachrichten:** `ctx.ws.broadcast` sendet zusätzlich auf `ext.<alt>.<kanal>`, damit offene Seiten mit altem
  Stand weiter Nachrichten bekommen.
- **`requires`:** Nennt eine andere Erweiterung noch die alte Kennung, sieht sie die Fähigkeiten der umbenannten weiter.
- **Datenordner:** `data/ext/<Speicher-Kennung>`. Fehlt er, gilt der erste vorhandene Ordner der neuen oder einer alten
  Kennung. Gibt es keinen, wird `data/ext/<Speicher-Kennung>` angelegt. Verschoben wird nichts.
- **Sicherungen:** Erweiterungen werden auch unter ihren alten Kennungen erkannt. Eine Sicherung mit der alten Zeile
  ergibt beim Einspielen keine Warnung „gibt es hier nicht“.
- **Geheimnisse:** Gefunden werden sie über ihr Label. Labels und Rechte-Muster wie `secrets.read:alter-name-*` bleiben
  stehen.

**Konflikte:**
- **Alte Erweiterung noch installiert:** Liegt noch ein Ordner mit der alten Kennung da (auch mit kaputtem Manifest) oder
  ist die alte Erweiterung als Paket mit lesbarem Manifest installiert, wird die neue nicht geladen. Im Protokoll steht
  „Erweiterung '…' wird nicht geladen: Die alte Kennung „…“ gehört noch zu einer installierten Erweiterung …“. Die alte
  lädt weiter, solange ihr Manifest lesbar ist. Lösung: alten Ordner bzw. altes Paket entfernen.
- **Doppelte Migrationen:** Liegen dieselben Migrationen in zwei Erweiterungsordnern (etwa nach dem Auspacken über einen
  alten Stand), bricht der Start mit „Die Migrationen der Erweiterungsordner „…“ und „…“ sind doppelt …“ ab. Die Meldung
  steht auch auf der Notseite. Vor dem Auspacken über einen alten Stand deshalb `extensions/` leeren.
- **Gemeinsame alte Kennung:** Nennen zwei Erweiterungen dieselbe alte Kennung, werden beide nicht geladen.

**Verwaister Zwilling.** Nach „Neuinstallation mit neuer Kennung → Rückweg aufs alte Image → wieder neu“ oder bei
mehreren alten Zeilen gilt nur eine Zeile (Reihenfolge wie oben). Jede weitere bleibt unverändert liegen und wird nie
geladen, nie automatisch eingeschaltet, nie geändert und nicht in `GET /extensions` gelistet. Ändernde Aufrufe über die
alte Kern-Adresse (`/api/v1/extensions/<alt>/…`) antworten dann mit `409`, lesen geht weiter. Das Protokoll meldet:
„Erweiterung 'neuer-name' nutzt die Zeile '…'; die Zeile der alten Kennung '…' bleibt unverändert liegen und wird nicht
geladen.“ Was in der Rückweg-Zeit unter der alten Kennung eingestellt wurde (Einstellungen, Zeitpläne), kommt nicht mit.

**Grenzen:**
- **Server-Anbieter:** Eine Erweiterung, die Server anlegt (`hosts.write`, `ctx.hosts.upsert_discovered()`), kann noch
  nicht umbenannt werden: Die Server und ihre Markierungen tragen die Kennung des Anbieters (`provider_ext_id`,
  `managed_by_ext_id`). Der Kern lehnt `legacy_ids` zusammen mit `hosts.write` beim Entdecken ab („Erweiterungen, die
  Server anlegen, können noch nicht umbenannt werden (legacy_ids zusammen mit hosts.write).“); die Erweiterung wird nicht
  geladen, der Grund steht im Protokoll und in der Zeile der Erweiterung. Alle anderen Berechtigungen gehen.
- `GET /jobs?ext_id=` filtert nach der Speicher-Kennung, `GET /capabilities` nennt nur die neue Kennung.

### Einstellungs-Schema: `settings.schema.json` und die `x-*`-Zusätze

Der Kern baut aus dem JSON-Schema die Einstellungsseite der Extension (Einstellungen →
Erweiterungen). Unterstützt sind `type` (`string`, `integer`, `number`, `boolean`, `array`,
`object`), `title`, `description`, `default`, `enum`, `required`, `properties`, `items` und
`pattern`. Dazu diese Zusätze (alle optional, unbekannte werden ignoriert):

| Zusatz | Wo | Wirkung |
|---|---|---|
| `x-advanced` | Feld | Steht unter „Erweitert“, eingeklappt. |
| `x-hidden` | Feld | Wird nicht angezeigt (interner Zustand). Auf der obersten Ebene übernimmt `PUT /extensions/{id}/settings` das Feld nie; es gehört den eigenen Routen der Extension (`ctx.settings.set()`). |
| `x-item-title` | Liste von Objekten | Wie ein Eintrag heißt („Server hinzufügen“). |
| `x-enum-labels` | Feld mit `enum` | Lesbare Texte je Wert: `{"all": "Alle Updates"}`. |
| `x-widget: "schedule"` | Text (Cron) | Zeitplan-Wähler statt Cron-Feld. `PUT /extensions/{id}/settings` prüft den Ausdruck wie beim Anmelden des Jobs und lehnt einen ungültigen mit `422` ab; leer bleibt erlaubt (dann gilt der Standard der Erweiterung). |
| `x-widget: "host"` | Text oder Liste von Texten | Auswahl aus den Servern (Wert = Servername, aus `GET /hosts`). Liste: Chips mit „Server hinzufügen“. Ohne Serverliste: Textfeld bzw. Wortliste. |
| `x-widget: "host-tag"` | Text oder Liste von Texten | Wie `host`, aber Auswahl aus den Markierungen (Tags) der Server, mit Anzahl. Eine gespeicherte Markierung, die es nicht mehr gibt, bleibt sichtbar. |
| `x-widget: "remote-select"` | Text | Auswahlliste, die die Extension selbst liefert (z. B. die Modelle eines KI-Servers). Braucht `x-options-url`. Schlägt die Abfrage fehl oder ist die Liste leer, erscheint ein Textfeld und der Grund. |
| `x-options-url` | `remote-select` | Pfad unter `/api/v1`, z. B. `/ext/shield/ai/models?which=primary`. Die Oberfläche schickt keine Formularwerte mit; die Extension fragt nur, was gespeichert ist (kein Adress-Parameter – sonst wäre die Route ein Weg für Anfragen an beliebige Adressen, samt Schlüssel). Nach einer neuen Adresse: speichern, dann „Neu laden“. Antwort: `{"options": [{"value": "…", "label": "…"}], "error": null}`; bei Problemen `options: []` und `error` als deutscher Satz (kein Fehlerstatus). |
| `x-empty-label` | Auswahl | Text für „nichts gewählt“ (Standard „– nicht gesetzt –“), z. B. „alle Server“. |
| `pattern` | Text, Wörter einer Liste | Regulärer Ausdruck (wie JSON-Schema, nicht verankert). Die Oberfläche zeigt die Meldung am Feld und sperrt „Speichern“; das Backend prüft dasselbe (`422`). Leere Werte sind immer erlaubt. |
| `x-pattern-message` | neben `pattern` | Deutsche Meldung, wenn das Muster nicht passt (Standard: „Das Format stimmt nicht.“). |
| `x-test-message` | Kopfebene | `true`, wenn die Extension einen `NotificationChannel` bereitstellt: die Verbindungskarte zeigt „Testnachricht senden“. |
| `x-secrets` | Kopfebene | Liste der Geheimnisse (Tresor-Labels): `{"label": "proxmox-token:{name}", "title": "…", "description": "…", "per_item": "connections", "optional": true}`. `per_item` erzeugt je Eintrag der genannten Liste ein Geheimnis (über dessen `name`); fällt ein Eintrag beim Speichern über `PUT /extensions/{id}/settings` weg oder wird umbenannt, löscht der Kern sein Geheimnis, und ein neu angelegter Eintrag startet ohne (ein altes Geheimnis unter demselben Namen wird gelöscht). `optional: true`: die Extension arbeitet auch ohne; fehlt ein nicht-optionales Geheimnis oder ein `required`-Feld, meldet `GET /extensions` `needs_setup`. |
| `x-secret-bound-to` | Eintrag in `x-secrets` | Felder, an die das Geheimnis gebunden ist: bei `per_item` Feldnamen des Eintrags (`["base_url"]`), sonst Pfade wie `pihole.url`. Ändert sich eines davon über `PUT /extensions/{id}/settings`, löscht der Kern das Geheimnis (Antwort `secrets_cleared`, Protokoll `detail.secrets_cleared`), auch bei der ersten Adresse (leer → Wert); ohne gespeicherten Wert zählt der `default` des Felds. Keine Änderung sind andere Schreibweisen derselben Adresse: Groß-/Kleinschreibung von Schema und Rechnername, Standardport (`:80` bei http, `:443` bei https), `/` am Ende, Fragment (`#…`), fehlendes Schema (= `http://`). Ein anderer Pfad oder eine andere Abfrage (`?…`) zählt als Änderung. `PUT /extensions/{id}/secrets` nimmt das Geheimnis erst an, wenn eines der Felder gespeichert einen Wert oder `default` hat, sonst `409` („Erst die Adresse eintragen und die Einstellungen speichern, danach die Zugangsdaten hinterlegen.“). |

**Eigene Routen für Verbindungen.** Speichert eine Extension Einstellungen selbst (`ctx.settings.set()`,
etwa über eigene `/connections`-Routen), löscht der Kern keine Geheimnisse. Dann ruft sie
`ctx.secrets.delete(label)` selbst auf, wenn eine Verbindung neu angelegt oder entfernt wird oder ihre
Adresse wechselt. Für den Vergleich gibt es `nodvard_sdk.same_target(alt, neu)` (`True` = dasselbe Ziel,
nach denselben Regeln wie bei `x-secret-bound-to`; die Oberfläche rechnet dasselbe in
`frontend/src/lib/targetAddress.ts`). So machen es Proxmox VE und Backups.

**Verbindung testen.** Jede Extension mit Einstellungen bekommt in der Oberfläche den Knopf
„Verbindung testen“ (`POST /extensions/{id}/test`). Der Kern ruft dafür `health()` der
Extension auf (und `test()` ihres `NotificationChannel`, falls sie einen bereitstellt) und
übersetzt technische Fehler in verständliche deutsche Sätze (keine Antwort, Zugangsdaten
abgelehnt, Zertifikat, Adresse nicht gefunden, Weiterleitung (3xx) …). Für gute Meldungen genügt es, in
`HealthReport.message` den Fehlertext des HTTP-Clients zu lassen; bei mehreren Verbindungen
liefert `HealthReport.details` je Verbindung `{"name": {"healthy": bool, "error": "…"}}` – daraus
entsteht die Liste „Ergebnis je Verbindung“. Geheimnisse gehören nie in `message` oder
`details`; der Kern schwärzt bekannte Werte trotzdem.

### Einstiegsklasse

```python
from nodvard_sdk import NodvardExtension, ExtensionContext

class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        """Nur registrieren. Kein I/O, keine Netzwerkzugriffe, kein DB-Schreiben."""

    async def on_start(self, ctx: ExtensionContext) -> None:
        """Hintergrundtasks starten. ctx.spawn() statt asyncio.create_task()."""

    async def on_stop(self, ctx: ExtensionContext) -> None:
        """Aufräumen. Über ctx.spawn gestartete Tasks werden automatisch beendet."""

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        """Optional. Speist die Registry-UI und das Notification-Center."""

    async def on_settings_changed(self, ctx: ExtensionContext, values: dict) -> None:
        """Optional. Nach jedem ctx.settings.set() -- nach dem Speichern, get() liefert
        schon die neuen Werte. Ein set() hier drin loest den Hook nicht erneut aus; eine
        Ausnahme wird geloggt und macht das Speichern nicht rueckgaengig."""
```

Die Trennung `setup` / `on_start` ist keine Kosmetik: sie erlaubt dem Kern, erst **alle**
Registrierungen einzusammeln (und damit Router, Navigation und Widget-Katalog in einem
Rutsch zu bauen), bevor irgendein Hintergrundtask läuft und Ereignisse in einen noch
halbfertigen Bus schiebt.

---

## 2. Der `ExtensionContext`

Das einzige Objekt, das eine Extension vom Kern bekommt. Alles daran ist
permission-geprüft und schreibt bei Bedarf ins Audit-Log.

| Handle | Zweck | Permission |
|---|---|---|
| `ctx.api` | `include_router(r, permission=…, public=…)` → `/api/v1/ext/<id>/…`, ohne weitere Angabe nur mit Anmeldung (siehe „Routen und Anmeldung“ unten). Fängt eine Route `HostUnreachable` oder einen SSH-Fehler nicht selbst ab, antwortet der Kern mit `502` und dem Grund in `detail` (nicht `500`, kein Traceback im Container-Protokoll) | —; `public=True`: `api.public` |
| `ctx.ui` | `register_page()`, `register_widget()`, `register_nav()` | — |
| `ctx.connectors` | `register_type(ConnectorType)` | — |
| `ctx.capabilities` | `provide(protokoll_instanz)` | je Protokoll |
| `ctx.actions` | `register(ActionSpec)`, `propose(ActionRequest)` (optional mit `standing_approval`, siehe §3), `check_standing_approval(granted_by_user_id, risk=…)` (gälte eine Dauerfreigabe dieser Person heute noch? `None` = ja, sonst der Grund) | `hosts.execute` u. a.; Dauerfreigabe: `actions.standing_approval` |
| `ctx.hosts` | `list()`, `get()`, `upsert_discovered()` | `hosts.read` / `hosts.write` |
| `ctx.exec` | `run(host, command)`, `stream(host, command)` (laufende Ausgabe, z. B. Live-Logs), `open_shell(host)`, `sftp(host)` | `hosts.execute` |
| `ctx.secrets` | `get_handle(label)`, `create()`, `delete(label)` (löscht das Geheimnis; `True`, wenn es eines gab, sonst `False`; Protokoll `extension.secret_removed`; ältere Kerne haben es nicht) | `secrets.read:<label>` |
| `ctx.vault_use` | Context-Manager, der einen Wert nur im Speicher materialisiert | dito |
| `ctx.db` | AsyncSession-Factory, **nur** auf `ext_<id>_*`-Tabellen (nach einer Umbenennung auch auf die Präfixe der alten Kennungen, siehe §1 „Erweiterung umbenennen“) | — |
| `ctx.settings` | `declare(schema)`, `get()`, `set()` | — |
| `ctx.scheduler` | `register_job(JobSpec)`; `validate_schedule(schedule)` prüft einen Cron-Ausdruck genau so wie `register_job()`, ohne etwas anzumelden (`ValueError` mit deutscher Meldung): für Zeitpläne, die eine Erweiterung selbst speichert (etwa den eines Skripts), erst prüfen, dann speichern, denn ein gespeicherter kaputter Zeitplan ließe `register_job()` beim nächsten Start scheitern. Ältere Kerne haben die Methode nicht. Meldet ein Job-Handler einen erwarteten Fehlschlag mit `NodvardError`, steht der Lauf als fehlgeschlagen mit diesem Text da, ohne Traceback (im Lauf-Protokoll und im Container-Log); jede andere Ausnahme kommt mit Traceback | `schedule.register` |
| `ctx.events` | `publish(Event)`, `subscribe(pattern, handler)` | — |
| `ctx.notify` | `send(Notification, raise_on_failure=False)` → `NotifyResult` (`notification_id`, `suppressed`, `delivered`) – mit `raise_on_failure=True` wirft `NotificationNotDelivered`, wenn es Kanäle gab und keiner zugestellt hat (für „erneut versuchen“); `would_suppress(host_id=…, host_ids=…)` fragt nur, ob ein Wartungsfenster gerade still schalten würde (siehe unten) | `notify.send` |
| `ctx.audit` | `log(entry)`. Erweiterungen schreiben unter eigenem Namen, zum Beispiel `<kennung>.<ereignis>`. Aktionen, deren Name mit `mfa.`, `auth.`, `login.`, `system.` oder `user.` beginnt (Groß-/Kleinschreibung und Leerzeichen am Rand zählen nicht), schreibt nur der Kern, weil er sie wieder liest und danach entscheidet: Der Aufruf wirft dann `ValueError` und schreibt nichts | `audit.write` |
| `ctx.http` | vorkonfigurierter `httpx.AsyncClient` mit Zielprüfung, dazu `websocket(url, …)`. Die Zielprüfung gilt nur für die Start-Adresse: `websocket()` folgt Weiterleitungen (3xx) beim Verbindungsaufbau nie (`async with` wirft dann ein `ConnectionError` mit deutschem Text, eine zweite Verbindung entsteht nicht); `get()`, `post()`, `request()` und `stream()` folgen ihnen ebenfalls nie: Eine Antwort mit Status 3xx kommt unverändert zurück, und die Erweiterung fragt die neue Adresse selbst ab. `follow_redirects=True` (oder ein anderer Wahrheitswert) wirft `ValueError`, bevor eine Anfrage hinausgeht, auch mit `insecure_tls=True`; `follow_redirects=False` bleibt erlaubt. Grund: Die Prüfung der erlaubten Adressen (`net.outbound`) kennt nur die Adresse des Aufrufs, das Ziel einer Weiterleitung sähe sie nie. Einen Proxy aus der Umgebung (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, bei `websocket()` auch `WS_PROXY`, `WSS_PROXY`, `SOCKS_PROXY`) benutzt `ctx.http` nie, jede Verbindung geht direkt an die geprüfte Adresse; `SSL_CERT_FILE` und `SSL_CERT_DIR` gelten weiter | `net.outbound:<cidr>` |
| `ctx.ws` | `broadcast(channel, payload)` auf `ext.<id>.*` (nach einer Umbenennung zusätzlich auf `ext.<alt>.*`) | — |
| `ctx.spawn` | überwachter Hintergrundtask mit Neustart-Politik | — |
| `ctx.logger` | strukturierter Logger, vorgetaggt mit der Extension-ID | — |
| `ctx.data_dir` | eigener, beschreibbarer Ordner der Extension (im Image `/app/data/ext/<id>/`, nach einer Umbenennung der Ordner der Speicher-Kennung, siehe §1). Dateien gehören hierher: Der Code im Image gehört root und ist schreibgeschützt, auch der eigene Paketordner. Neue Dateien sind nur für den Benutzer `lattice` lesbar | — |

Nicht am Kontext und bewusst nicht verfügbar: direkter Zugriff auf Kern-Tabellen, das
`asyncio`-Loop-Objekt, andere Extensions (nur über `ctx.events` und deklarierte
`requires` + `ctx.capabilities.query()`).

### Routen und Anmeldung

`ctx.api.include_router(router, prefix=…, permission=…, public=…)` hängt einen FastAPI-Router unter
`/api/v1/ext/<id><prefix>` ein. Die Prüfung gilt für **alle** Routen dieses Routers:

| Aufruf | Wer durchkommt |
|---|---|
| `include_router(r)` | jedes angemeldete Konto, ohne bestimmte Berechtigung (ohne Token `401`) |
| `include_router(r, permission="hosts.read")` | angemeldet **und** mit dieser Berechtigung (ohne Token `401`, ohne die Berechtigung `403`) |
| `include_router(r, public=True)` | jeder, auch ohne Anmeldung; braucht `api.public` im Manifest |

**Anmeldung ist keine Berechtigung.** Ohne `permission=` kommt jedes angemeldete Konto durch, egal
mit welcher Rolle. Routen, die Daten zeigen oder etwas ändern, setzen deshalb `permission=` oder
prüfen selbst. Früher blieb eine Route ohne `permission=` ganz ohne Prüfung; wer eine Adresse
bewusst ohne Anmeldung anbietet (z. B. für einen Webhook), stellt auf `public=True` um.

**`public=True`** geht nur mit der Berechtigung `api.public` im Manifest, sonst wirft der Aufruf
`PermissionDenied` und die Erweiterung steht auf Fehler. Zusammen mit `permission=` ist es ebenfalls
ein Fehler (`InvalidRegistration`). Beim Einschalten stehen die öffentlichen Adressen
(`/api/v1/ext/<id><prefix>`) im Protokoll, in `detail.public_routes` von `extension.enabled` bzw.
`extension.auto_enabled`; dazu kommt eine Warnung im Log.

**Grenzen der Prüfung.** Sie greift nur bei normalen FastAPI-Routen (`@router.get` usw.).
WebSocket-Routen, `router.add_route()` und `router.mount()` (z. B. `StaticFiles`) lehnt der Kern
ohne `public=True` ab (`InvalidRegistration`), die Erweiterung lässt sich dann nicht einschalten.
Mit `public=True` sichert die Erweiterung sie selbst ab. Geprüft wird nach `setup()` und nach
`on_start()`; hängt `on_start()` solche Routen an, nimmt der Kern die Erweiterung wieder heraus.
Was erst später zur Laufzeit an einen schon eingehängten Router kommt (etwa aus einem
Hintergrundtask), sieht der Kern nicht mehr: Solche Routen sichern sich selbst ab.

**Größe der Anfrage.** Der Kern nimmt an jeder Route höchstens 1 MiB an (`NODVARD_DECK_MAX_BODY_BYTES`), darüber
antwortet er mit `413` ([04 §1](04-API.md#1-konventionen)). Eine Route, die bewusst größere Uploads annimmt, erklärt das
mit `nodvard_sdk.max_body_bytes(limit)`: `@router.post(...)` ganz außen, `@max_body_bytes(...)` direkt über der Funktion.
`limit` ist eine Obergrenze in Bytes oder eine Funktion ohne Argumente, die sie bei jeder Anfrage liefert (für einstellbare
Grenzen). Der Kern prüft die `Content-Length` und zählt bei Anfragen ohne Längenangabe mit. Die Angabe hebt die Grenze
nur an; unter die allgemeine Grenze senken lässt sie sich nicht. Die Route sollte ihre Daten trotzdem als Strom lesen oder
eigene Prüfungen behalten. Mitgeliefert nutzen das `documents` (20 MB je Dokument) und `inventory` (5 MB je Bild).

Seiten im Frontend-Bundle schicken für diese Routen das Token mit (`Authorization: Bearer …` mit
dem Token aus `window.__nodvardDeck.getAccessToken()`). Die mitgelieferten Erweiterungen nehmen dafür `authedFetch`,
das das Token bei 401 erneuert (siehe §5); `HelloPage.tsx` in hello-world zeigt es als Vorlage.

### Meldungen und Wartungsfenster

Eine Meldung mit `payload["host_id"]` (ein Server) oder `payload["host_ids"]`
(Sammelmeldung, still nur, wenn **alle** Server in einem laufenden Fenster liegen)
schaltet ein Wartungsfenster stumm: Sie steht im Verlauf, geht aber an keinen Kanal
(docs/03 §9). Ohne Host-Bezug ist eine Meldung nie still.

- `send()` gibt `NotifyResult(notification_id, suppressed, delivered)` zurück. `suppressed=True`
  heißt: nur im Verlauf, kein Push. `delivered=True` heißt: mindestens ein Kanal hat die
  Meldung zugestellt; `False`: keiner – es ist kein Kanal eingerichtet, alle sind
  ausgefallen oder ein Wartungsfenster hat den Push unterdrückt; `None`: unbekannt
  (ältere Kerne). Ältere Kerne und Test-Doubles liefern `None` statt `NotifyResult` –
  Extensions werten das wie `suppressed=False` und `delivered=None` aus (z. B.
  `getattr(result, "suppressed", False) is True`, `getattr(result, "delivered", None)`).
  Wer den Rückgabewert nicht braucht, ändert nichts.
- `would_suppress(host_id=None, host_ids=None) -> bool` prüft dieselbe Regel, legt aber
  nichts an. Ein einzelner String als `host_ids` gilt wie in `send()` als kaputte Liste
  (nie still). Braucht ebenfalls `notify.send`.

**Muster für Zustandswächter** (Proxmox, Backups, Gameserver): Ein Wächter meldet nur
Zustandswechsel und merkt sich „gemeldet“. War diese Meldung stumm, merkt er sich das
zusätzlich („stumm gemeldet“) und fragt bei den folgenden Prüfungen nur
`would_suppress()` – so entsteht im Fenster genau **ein** stummer Verlaufseintrag, nicht
einer pro Prüfung. Ist das Fenster vorbei und der Zustand noch da, kommt die Meldung
**einmal hörbar** nach, mit „(seit dem Wartungsfenster)“ im Titel. Ist der Zustand schon
im Fenster wieder gut, gibt es keine nachgeholte Ausfallmeldung; die Entwarnung („wieder
online“) geht wie jede Meldung durch dieselbe Fensterprüfung – im Fenster also ebenfalls
nur in den Verlauf. Wird die Entwarnung erst nach dem Fenster bemerkt (zwischen zwei
Prüfungen), kommt sie hörbar, mit dem Hinweis, dass die Störung im Wartungsfenster lag.

**Die Entwarnung richtet sich nach dem Ausfall.** War der Ausfall hörbar (kam vor dem
Fenster oder als Nachmeldung) und fällt nur die Entwarnung ins Fenster, bleibt sie im
Fenster still (niemand wird nachts geweckt) und kommt nach dem Fenster **einmal hörbar**
nach, mit „(im Wartungsfenster)“ im Titel – einen gehörten Alarm schließt immer eine
gehörte Entwarnung, sonst bliebe er auf dem Handy offen. Fallen Ausfall und Entwarnung
beide ins Fenster, bleiben beide still. Der Wächter merkt sich dafür „Entwarnung
ausstehend“ (samt Host) im selben Zustand; fällt der Zustand vorher wieder aus, ist sie
überholt und entfällt. Die Nachmeldung selbst fällt in ein Fenster? Dann bleibt sie still
und kommt nach diesem Fenster.

Der Zustand („stumm gemeldet“, „Entwarnung ausstehend“, dazu der Host, für den das
Fenster galt) liegt dort, wo der Wächter ohnehin seinen Zustand hält (bei allen drei
`data_dir/watch-state.json`), und übersteht so einen Neustart des Dashboards. Ist der Host
gerade nicht lesbar, bleibt der Wächter beim gemerkten Host, statt sofort hörbar
nachzumelden. Geht die Zustandsdatei verloren, gibt es höchstens eine Meldung zu viel –
außer beim Join-Code des Gameservers: Er wird danach wie beim allerersten Lauf nur
gemerkt, ein im Fenster stumm gebliebener Code kommt dann nicht mehr nach.

---

## 3. Capabilities — der Kern der Entkopplung

Der Kern definiert eine kleine, endliche Menge von `Protocol`-Klassen. Er ruft sie auf,
ohne zu wissen, wer sie erfüllt.

```python
ctx.capabilities.provide(ProxmoxHostProvider(ctx))
```

| Protokoll | Wer implementiert es | Was der Kern damit baut |
|---|---|---|
| `HostProvider` | proxmox, docker, statisches Inventar | die Host-Liste, Node-Seite, Start/Stop-Aktionen |
| `TerminalTarget` | terminal (SSH), künftig serielle/Container-Shells | das Web-Terminal |
| `ConsoleTarget` | proxmox (vncproxy/vncwebsocket) | die grafische Konsole (`/console/:hostId`, noVNC) |
| `FileSource` | files-sftp, nextcloud, truenas, syncthing | den **einen** Dateimanager mit Seitenleiste |
| `ActionExecutor` | terminal (SSH-Exec), proxmox (API-Aktionen) | die Ausführung hinter dem Gate |
| `AIProvider` | shield (Ollama), künftig andere | Chat, Diagnose, Zusammenfassungen |
| `NotificationChannel` | ntfy, E-Mail, Webhook | das Notification-Center |
| `MetricsProvider` | proxmox, docker, node-exporter | Kacheln und Verlaufsgrafiken |
| `ServiceCatalog` | service-matrix (Docker-API) | den App-Launcher |
| `BackupProvider` | backups (Proxmox-Jobs, restic …) | das Backup-Center |
| `SearchProvider` | beliebig | die globale Suche |

### Beispiel: `FileSource` — die Schnittstelle hinter dem Dateimanager

```python
@runtime_checkable
class FileSource(Protocol):
    source_id: str          # eindeutig innerhalb der Extension
    label: str              # "Nextcloud", "TrueNAS · tank/media", "root@docker"
    icon: str
    caps: FileSourceCaps    # write/rename/search/range_read/sync_status/quota/trash

    async def stat(self, path: PurePosixPath) -> FileEntry: ...
    async def list_dir(self, path, *, cursor=None) -> Page[FileEntry]: ...
    async def open_read(self, path, *, offset=0) -> AsyncIterator[bytes]: ...
    async def open_write(self, path, stream, *, size) -> FileEntry: ...
    async def mkdir(self, path) -> FileEntry: ...
    async def remove(self, path, *, recursive: bool) -> None: ...
    async def rename(self, src, dst) -> FileEntry: ...
    async def search(self, query: str, *, root) -> AsyncIterator[FileEntry]: ...
    async def info(self) -> SourceInfo: ...   # Quota, Health, Deep-Link fürs Info-Panel
```

Der Kern liefert daraus, **ohne eine einzige Zeile über Nextcloud oder TrueNAS zu
wissen**:

- die Seitenleiste (alle registrierten Quellen aller Extensions),
- die Explorer-Ansicht,
- die globale Suche als Fan-out über alle Quellen mit `caps.search`,
- **Drag & Drop zwischen Quellen** als generisches `open_read(A) → open_write(B)` mit
  Fortschritt über WS,
- Sync-Status-Icons für Quellen mit `caps.sync_status` (das OneDrive-Wolken-Verhalten),
- das Info-Panel aus `info()` (Speicherverbrauch, Pool-Health, SMART-Warnungen,
  Deep-Link in die native UI).

Eine neue Quelle hinzufügen heißt: ein Objekt registrieren. Kein Kern-Code ändert sich.

`open_write` sollte das Ziel erst ersetzen, wenn alles angekommen ist, und es nie vorab abschneiden: sonst wäre
bei einem Abbruch der alte Inhalt weg, und zeigen Quelle und Ziel auf dieselbe Datei, schnitte das Schreiben die
Quelle selbst ab (`files-sftp` schreibt dafür, wo es geht, in eine Temp-Datei im Zielordner und benennt sie
danach um).

Optional, bewusst **kein** Pflichtmitglied des Protokolls: `async file_identity(path) -> dict | None`. Damit
erkennt der Kern vor einem Transfer, ob Quelle und Ziel dieselbe Datei sind (Link, `..`-Umweg, zweiter Eintrag
für denselben Rechner), und `/files/transfer` antwortet dann `409`. Rückgabe: `path` (der aufgelöste, absolute
Pfad) und, soweit bekannt, `size`, `mtime`, `uid`, `gid`, `mode` sowie `machine` (eine feste Kennung des
Rechners, etwa `/etc/machine-id`); `None`, wenn es die Datei nicht gibt oder die Quelle es nicht ermitteln kann.
Eine Ausnahme oder ein Rückgabewert, der kein `dict` ist, zählt wie `None`. Innerhalb derselben Quelle
entscheidet `path`. Über zwei Quellen hinweg sind zwei verschiedene `machine`-Kennungen immer zwei Dateien;
sonst müssen außer `path` auch alle fünf Werte vorhanden sein und übereinstimmen (geklonte Rechner teilen oft
dieselbe Kennung). Ohne `file_identity` erkennt der Kern nur den gleichen Pfad in derselben Quelle. Der Kern
liest es mit `getattr(source, "file_identity", None)`; ältere Quellen ohne die Methode bleiben gültig.

**Quellen mit eigener Berechtigung:** Optional nennt eine Quelle `required_permission: str | None`
(fehlt das Attribut, gilt `None`). Dann zeigt und öffnet der Kern sie nur Nutzern, die diese
Berechtigung selbst haben; `files.read`/`files.write` allein reichen nicht. Gedacht ist das für
Quellen, die mit fremden Zugangsdaten arbeiten: Die SSH-Quellen der terminal-Extension verlangen
`hosts.execute`, weil der Zugriff mit dem Zugang des Servers läuft (oft root), nicht mit dem Konto
des Nutzers. Herunterladen, Suchen, Schreiben, Kopieren und verweigerte Zugriffe stehen für solche
Quellen im Audit-Log ([04 §3](04-API.md#dateien)). Das Attribut ist bewusst kein Mitglied des
Protokolls (`@runtime_checkable` würde sonst ältere Quellen ohne es abweisen); der Kern liest es
mit `getattr`. Ist der Wert kein Text oder leer, sehen und öffnen die Quelle nur Owner und `admin`.

**Fehler einer Quelle:** Die Quelle muss ihre Fehler nicht selbst übersetzen. Der Kern antwortet bei
`FileNotFoundError` mit `404`, bei jeder anderen Ausnahme mit `502` und `Zugriff auf die Quelle fehlgeschlagen: <Grund>`
([04 §3](04-API.md#dateien)). Netzfehler (Zeitüberschreitung, Verbindung abgelehnt oder abgebrochen, Name unbekannt,
kein Weg zum Server) werden zu einem deutschen Satz, von einem anderen `OSError` kommt nur der Name der Ausnahme an
(sein Text kann Pfade enthalten), sonst ihr Text. Ist der Text einer eigenen Ausnahme schon ein fertiger Satz für die
Oberfläche, setzt die Quelle an der Klasse `readable = True` (wie `WebDavError` der Nextcloud-Quelle): Dann kommt ein
nicht leerer Text unverändert an, und der Fall gilt wie ein Netzfehler als erwartbar (kein Traceback im
Container-Protokoll). Das Attribut ist wie `required_permission` kein Mitglied des Protokolls, der Kern liest es mit
`getattr`. Dasselbe gilt für Ausnahmen aus `TerminalTarget.open()`; dort steht der Grund in der Fehlermeldung des
Terminals ([04 §4](04-API.md#4-websocket)).

### Beispiel: `ActionExecutor` — die Schnittstelle hinter Nodvard Shield und dem Script-Repository

```python
@runtime_checkable
class ActionExecutor(Protocol):
    action_types: frozenset[str]     # z. B. {"shell.exec", "docker.restart"}
    async def execute(self, req: ActionRequest) -> ActionResult: ...
    async def dry_run(self, req: ActionRequest) -> DryRunReport | None: ...
```

Extensions rufen **nie** selbst aus. Sie schlagen vor:

```python
decision = await ctx.actions.propose(ActionRequest(
    action_type="shell.exec",
    host_ref=host.id,
    payload={"command": "docker restart nextcloud-app"},
    risk=Risk.MEDIUM,
    proposed_by=Actor.ai(model="qwen2.5:7b"),
    reason=begruendung,                  # Pflichtfeld, sonst ValidationError
    correlation_id=incident.id,
))
```

Der Kern entscheidet (Gate, siehe [01 §4](01-ARCHITECTURE.md#4-das-aktions-gate)),
auditiert, und führt gegebenenfalls über den passenden `ActionExecutor` aus.
`reason` ist im Datentyp als Pflichtfeld verankert — die Begründungspflicht
lässt sich damit nicht versehentlich umgehen.

Gibt der Kern die Aktion sofort frei (Modus `full`), läuft sie im Hintergrund.
`propose()` wartet ohne weitere Angabe bis zum Ende — richtig für Hintergrund-Jobs, die
das Ergebnis brauchen. Eine HTTP-Route gibt `wait_s=REQUEST_WAIT_S` (20 s, aus
`nodvard_sdk`) mit; ist die Aktion dann noch nicht fertig, kommt `status = executing`
zurück, und die Seite verweist auf „Aktionen".

`ctx.actions.result(action_id)` und `ctx.actions.list(correlation_id=…, limit=…)` liefern nur
Aktionen der eigenen Extension, aber mit vollem `result` und vollem `payload`, also samt Befehl sowie
Ausgabe und Fehlertext vom Server. Wer die Route aufruft, prüft der Kern dort nicht (seine eigene
API zeigt Ausgabe und Fehlertext nur mit `hosts.execute`, den vollen `payload` nur mit `hosts.execute`
oder dem passenden `actions.approve:<risiko>`, siehe [04, Aktionen](04-API.md#aktionen--der-bestätigungs-workflow)). Eine
Route, die sie anzeigt, verlangt deshalb selbst `hosts.execute`, z. B. mit
`ctx.api.include_router(router, permission="hosts.execute")` wie die scripts-Extension. Ins
Protokoll (`action.executed`) übernimmt der Kern vom Ergebnis keinen Text vom Server, nur Erfolg,
Exitcode, Dauer und die Länge von Ausgabe und Fehlertext (dazu einen festen Satz, wenn das Gate
den Grund selbst festlegt). Felder `output`, `stdout`, `stderr` und `command` in `detail` eigener
Einträge (`ctx.audit.log`) leert er für Leser ohne `hosts.execute`, `reason` und alle übrigen Felder
nicht – Text vom Server gehört deshalb weder in `reason` noch in andere Felder von `detail` noch
in Meldungen, ein Befehl nur in `detail.command` (Meldungen sieht auch, wer nur lesen darf).
Ereignisse `action.*` (etwa `action.executed` mit dem `payload`) bekommen Handler im Prozess
(`ctx.events.subscribe`) vollständig; über den WebSocket bekommen Nutzer ohne `hosts.execute` den
`payload` nur gekürzt ([04 §4](04-API.md#4-websocket)).

**Dauerfreigabe (ohne Klick).** Ein Vorschlag kann sich auf eine Freigabe berufen, die ein Mensch vorab
erteilt hat: `ActionRequest.standing_approval = StandingApproval(granted_by_user_id=…, granted_at=…, label=…)`.
Das geht nur mit der Berechtigung `actions.standing_approval` im Manifest. Ob die Freigabe noch zum Vorschlag passt
(bei Skripten: nichts geändert), prüft die Extension selbst. Das Gate prüft bei jedem Vorschlag, ob die Person noch
aktiv ist und Dauerfreigaben sowie Aktionen dieser Risikostufe freigeben darf (`actions.standing_approval` und
`actions.approve:<risiko>`; eingebaut: Owner und `admin`). Vorschläge einer KI laufen nie über eine Dauerfreigabe.
Gilt sie, läuft die Aktion ohne Klick an, auch im Modus `propose` (Sperrliste und Anti-Flapping greifen weiter);
`GateDecision.rule` ist dann `standing_approval`, und das Gate hängt an `reason` „– ohne Klick, lief mit
Dauerfreigabe vom <Datum> durch <Benutzername>“ an (Datum in der eingestellten Zeitzone des Dashboards). Sonst wird
ein normaler Vorschlag daraus: `reason` bekommt „– Dauerfreigabe vom … durch … gilt nicht: <Grund>“, und wartet die
Aktion auf einen Klick, nennt `GateDecision.detail` den Grund. Der Name einer fehlenden Berechtigung steht nur in
`gate_decision.standing_approval_rejected.permission` (und damit im `detail` der Protokollzeile), nie im Text.
`reason` der Extension nennt also nur, was laufen soll.

Der Executor bekommt `req.standing_approval` nur, wenn die Aktion wirklich ohne Klick anlief (nach einem Klick ist es
`None`); so kann er direkt vor dem Befehl prüfen, ob die Freigabe noch gilt. Für Anzeigen wie „Dauerfreigabe gilt“
prüft `ctx.actions.check_standing_approval()` die Person genauso wie das Gate, nur lesend. Weitere Bausteine dafür:
`nodvard_sdk.current_job_trigger()` sagt einem `JobSpec.handler`, ob er nach Zeitplan (`"schedule"`), von Hand
(`"manual"`: `POST /jobs/{id}/run` oder `ctx.scheduler.trigger()`) oder außerhalb eines Jobs (`None`) läuft;
`Host.credential_username` und `Host.credential_port` nennen Konto und SSH-Port des Standard-Zugangs (nie das
Geheimnis; `None` ohne Zugang oder bei älteren Kernen).

---

## 4. Widgets — deklarativ, nicht als Code

**Die Regel:** Kein Widget ohne deklarative Form. Grund siehe
D-04 in [00-DECISIONS](00-DECISIONS.md):
Extensions liefern React; die Flutter-App kann React nicht ausführen. Wäre das
Widget-Format Code, sähe die Android-App von jeder Extension nichts.

```python
ctx.ui.register_widget(WidgetSpec(
    id="incidents",
    title="Vorfälle",
    icon="siren",
    size=GridSize(w=2, h=2, min_w=1, min_h=1),
    refresh=Refresh(interval_s=60, ws_channel="ext.shield.incidents"),
    data_endpoint="widgets/incidents",        # relativ zu /api/v1/ext/shield/
    permissions=["soc.read"],
    view=ListView(
        item=ListItem(
            title="{{ title }}",
            subtitle="{{ host }} · {{ ts | relative }}",
            badge=Badge(text="{{ status_label }}", tone="{{ tone }}"),   # Text deutsch, Farbe explizit
            actions=[
                WidgetAction(id="confirm", label="Bestätigen",
                             endpoint="incidents/{{ id }}/confirm",
                             method="POST", confirm=True, style="primary"),
                WidgetAction(id="dismiss", label="Verwerfen",
                             endpoint="incidents/{{ id }}/dismiss", method="POST"),
            ],
        ),
        empty_text="Keine offenen Vorfälle",
    ),
    component="IncidentFeed",   # optional, NUR Web: reichere Darstellung
))
```

**View-Typen** (endliche Menge; React und Flutter implementieren beide alle):

`StatView` · `ListView` · `TableView` · `ChartView` (line/bar/area) · `StatusGridView`
(die Service-Matrix-Kacheln) · `GaugeView` · `MarkdownView` · `ActionsView` (reine
Knopfleiste) · `LogView` (monospace, auto-scroll)

**Template-Ausdrücke** sind absichtlich winzig und ohne Logik: `{{ feld.pfad }}` plus
eine feste Filterliste (`relative`, `datetime`, `date`, `bytes`, `percent`, `number`,
`duration`, `tone`, `truncate`, `upper`, `lower`). Kein Ausdruck, keine Bedingung, keine
Schleife — sonst müsste die Flutter-Seite einen Interpreter mitbringen, und man hätte
sich eine zweite Programmiersprache eingehandelt.

**Knöpfe je Zeile ein-/ausblenden:** `WidgetAction.show_if="{{ can_start }}"` zeigt den
Knopf nur, wenn der gerenderte Wert „wahr“ ist (nicht leer, nicht `false`/`0`/`none`).
Die Entscheidung trifft das BACKEND (es liefert `can_start` als fertiges Feld) — auch
das ist keine Bedingung im Template, nur ein Ja/Nein-Feld.

**Badge-Farben nicht aus dem Anzeigetext ableiten:** `| tone` rät die Farbe aus
Stichwörtern („critical“, „warning“ …). Für deutsche Anzeigewörter den Text aus einem
`*_label`-Feld nehmen und die Farbe aus dem Maschinenwert (`{{ status | tone }}`) oder
einem expliziten `tone`-Feld.

**Datenvertrag:** `GET /api/v1/ext/<id>/<data_endpoint>` liefert
`{"data": …, "meta": {…}}`, geformt passend zum View-Typ. Der Kern validiert die Form
gegen den View-Typ und meldet Abweichungen als Extension-Fehler statt als kaputtes UI.

**Grenze klar benennen:** `component` ist eine Aufwertung, kein Ersatz. Ein Widget, das
nur als `component` existiert, wird beim Registrieren abgelehnt — sonst entsteht genau
die Web-only-Schieflage, die diese Regel verhindern soll.

---

## 5. Seiten (volle UI-Ansichten)

```python
ctx.ui.register_page(PageSpec(
    id="soc",
    path="/soc",                      # wird zu /ext/shield/soc
    title="Nodvard Shield",
    icon="shield-check",
    nav_section="Sicherheit",
    nav_order=20,
    permissions=["soc.read"],
    component="SocPage",              # Export aus dem Frontend-Bundle
    mobile=MobileFallback.WIDGETS,    # WIDGETS | WEBVIEW | HIDDEN
))
```

Ganze Seiten sind React — hier ist das in Ordnung, weil `mobile` explizit festlegt, was
die Android-App stattdessen tut. Der Standard `WIDGETS` heißt: die App zeigt die Widgets
dieser Extension statt der Seite. `WEBVIEW` ist erlaubt für Ausnahmen (z. B. den
Script-Editor), aber begründungspflichtig im Review — eine Webview ist der Anfang vom
Ende des nativen Gefühls, das der Grund für Flutter ist.

### Werkzeuge auf der Server-Seite (`register_host_tool`)

Jeder Host hat im Kern eine eigene Seite (`/hosts/<id>`, Plesk-Stil: alles zu EINEM
Server an einer Stelle). Der Kern kennt dabei keine Extension namentlich — was eine
Extension für einen Host kann, meldet sie als Werkzeug-Kachel an:

```python
ctx.ui.register_host_tool(HostToolSpec(
    id="guest",
    title="Hardware, Netzwerk & Snapshots",
    description="Kerne, RAM, Disks mit Speicherort, Netzwerk, Snapshots",
    icon="server",
    category="settings",              # control | monitoring | services | data | settings
    path="/nodes?host={host_id}",     # Seite DIESER Extension, {host_id} wird ersetzt
    kinds=["vm", "lxc"],              # nur fuer diese Host-Arten (leer = alle)
    tags=[],                          # mindestens einer dieser Tags (leer = egal)
    own_hosts_only=True,              # nur Hosts, die diese Extension selbst anlegt
    os_families=[],                   # z. B. ["linux"] (leer = alle)
    permissions=[],                   # Nutzer braucht alle (leer = hosts.read reicht)
))
```

Alle gesetzten Bedingungen müssen zutreffen. `GET /hosts/{id}/tools` liefert die
passenden Kacheln sortiert nach Bereich, `order`, Titel. Dieselbe `id` erneut zu
registrieren **ersetzt** das Werkzeug — so kann eine Extension es in
`on_settings_changed` mit neuen Bedingungen melden (Beispiel: service-matrix, deren
Docker-Tag einstellbar ist).

### Was eine Extension auf einem Server braucht (`register_host_requirement`)

Manche Extensions brauchen auf dem Server mehr als nur einen SSH-Zugang: root ohne Passwort
(`sudo`), die Mitgliedschaft in einer Gruppe (Container ohne sudo), ein Programm. Der Kern
kennt dabei keine Extension und kein Werkzeug namentlich — die Extension meldet es an, der
Kern bietet es im Einrichtungsbefehl an und prüft es bei „Verbindung prüfen":

```python
ctx.ui.register_host_requirement(HostRequirementSpec(
    id="docker-group",
    label="Docker ohne sudo (Service-Matrix)",
    check_command="docker ps -q",         # nur lesend; Exit 0 = ok, 127 = nicht installiert
    ok_text="Docker lässt sich ohne sudo benutzen.",
    fail_hint="Benutzer zur Gruppe docker hinzufügen: sudo usermod -aG docker {user}",
    unix_group="docker",                  # Einrichtungsbefehl bietet "zur Gruppe hinzufügen" an
    tags=["docker"],                      # nur Server mit einem dieser Tags (leer = alle)
    os_families=["linux"],                # Standard; leer = jedes System
))
ctx.ui.register_host_requirement(HostRequirementSpec(
    id="root", label="Root-Rechte", needs_root=True, root_reason="Updates einspielen, Quarantäne",
))
```

Dieselbe `id` erneut zu registrieren **ersetzt** die Meldung (so folgt sie einem
einstellbaren Tag in `on_settings_changed`); beim Deaktivieren der Extension verschwindet
sie. `unix_group` muss `^[a-z_][a-z0-9_-]{0,31}$` entsprechen, sonst ignoriert der Kern den
Namen (er landet in einem Shell-Befehl). Privilegierte Gruppen (`root`, `sudo`, `wheel`, `admin`, `shadow`,
`disk`, `adm`, `staff`, `lxd`, `libvirt`, `kvm`) ignoriert er ebenfalls und schreibt einen Log-Eintrag;
`docker` ist erlaubt. `GET /hosts/{id}/requirements` liefert die
passenden Meldungen. Additiv: `API_VERSION` bleibt.

Die Zielseite liest `?host=` über den Hook `useUrlParams()` (unten) und hebt den Host
hervor oder filtert auf ihn — mit sichtbarem, entfernbarem Filter. Für die Markierung gibt
es die Kern-Klasse `nodvard-deck-focus`; der alte Name `lattice-focus` gilt weiter (Übergang),
beide stehen im Kern in derselben CSS-Regel. Ändert sich die Query aus Sicht des Routers, baut der
Kern die Seite außerdem neu auf.

Darauf allein kann sich eine Seite nicht verlassen, sobald sie ihre **eigene** Adresse per
`replaceState` pflegt (Reiter im Streifen ↔ `?tab=`, Filter entfernen, anderen Server
wählen): für den Router ändert sich die Query dann nicht, obwohl die Adresszeile eine
andere ist, und ein Link auf dieselbe Adresse baut nichts neu auf. Der Kern feuert deshalb
nach jeder Navigation innerhalb einer Erweiterungsseite das Ereignis `nodvard-deck:navigate` auf
`window` (`ExtensionPage.tsx`; `pushState` meldet der Browser sonst bei niemandem), auch nach
Zurück/Vor, und setzt dafür `window.__nodvardDeck.navigateEvents`. Den alten Namen
`lattice:navigate` feuert er zusätzlich weiter (Übergang, siehe „Namen im Frontend-Vertrag“
unten). Der Hook `useUrlParams()`
(`extensions/_shared/frontend/src/location.ts`) folgt diesem Ereignis — und nur ohne diese
Marke (Tests, Vorschau) zusätzlich `popstate`: in der Kern-Shell würde die alte Seite sonst
kurz die neue Adresse zeigen und den Server abfragen, bevor der Kern sie neu aufbaut. Er
liefert die aktuelle Query samt Setter (`replaceState`, andere Werte, Anker und
Verlaufszustand bleiben). Der dritte Rückgabewert ist ein Auslöser: er ändert sich bei jeder
Navigation auf die Seite (auch auf genau dieselbe Adresse), nicht aber bei eigenen Änderungen
über den Setter — als Effekt-Abhängigkeit für das, was jeder Link neu auslösen soll, obwohl
es nicht in der Adresse steht (hinscrollen, aufklappen). Auf seinen Wert kommt es nicht an.

Für diese Seiten ist die Adresszeile die Wahrheit: was die Seite filtert oder zeigt, steht
in der Query, und „Filter entfernen“ nimmt nur diesen einen Wert heraus. Ein Link ohne Query
(Menüeintrag) auf die schon offene Seite setzt sie auf den Ausgangsstand zurück (Nodvard Shield:
Übersicht, kein Server-Filter), auch wenn der Router diese Adresse schon für aktuell hielt.

**Alle mitgelieferten Seiten, die ihre Query lesen, nutzen den Hook:** Nodvard Shield (`?tab=`,
`?host=`), Backups und Skripte (`?host=`), Service-Matrix (`?host=`, `?logs=`), System
(`?host=`, auch die Server-Auswahl schreibt es), Gameserver (`?host=`: nach vorn, hinscrollen)
und der Proxmox-Knoten (`?host=`, `&tasks=1`: markieren, aufklappen, Verlauf filtern,
hinscrollen). Eine neue Seite, die ihre Query nur einmal beim Mount liest
(`new URLSearchParams(window.location.search)`), folgt der Adresszeile dagegen nicht: ein
Link auf dieselbe Adresse wirkt dort nicht mehr, sobald sich die Seite zwischendurch selbst
verstellt hat. Ende-zu-Ende-Tests mit echtem Router: `*.navigation.test.tsx` der Seiten,
Hilfen in `extensions/_shared/frontend/src/testShell.tsx`.

Aktionen erscheinen auf der Server-Seite automatisch (`GET /hosts/{id}/actions`): was
ein `HostProvider` über `host_actions()` für genau diesen Host anbietet, steht als Knopf
oben (`source="host"`); für jeden Host registrierte Aktionen mit Eingabefeld
(`params_schema.required`, `command_field`) liegen unter „Weitere Aktionen“.
`ActionSpec.host_tags` begrenzt eine global registrierte Aktion auf Hosts mit mindestens
einem dieser Tags — Liste UND Auslösen prüfen das (Beispiel: `container.*`/`docker.prune_*`
nur auf Docker-Hosts). Aktionen mit `host_bound=False` erscheinen dort nie und lassen
sich auch nicht per `POST /hosts/{id}/actions/{typ}` auslösen (404) — nur die Extension
selbst schlägt sie über `ctx.actions.propose` vor. Pflichtfelder vom Typ Objekt/Liste bekommen dort kein Formular;
solche Aktionen brauchen ein eigenes in der Extension (Beispiel: `vm.config_set`).

### Frontend-Bundle laden

Das Bundle ist ein **ESM-Modul**, gebaut mit React, ReactDOM und dem SDK als
`external`. Der Kern liefert es unter `/api/v1/ext/<id>/frontend/index.js` aus und lädt
es per `import(url)`. Geteilte Singletons hängen an `window.__nodvardDeck` (alter Name
`window.__lattice`, gilt weiter — Übergang), ein Import-Map-Shim
löst `react`, `react/jsx-runtime` und `react-dom` dorthin auf.

**Namen im Frontend-Vertrag (Übergang).** Mit der Umbenennung zu Nodvard Deck gibt es für alles,
woran Bundles hängen, einen neuen Namen. Die alten gelten weiter, bis eine Major-Version sie
ausdrücklich entfernt:

| Neu | Alt (gilt weiter, Übergang) |
|---|---|
| `window.__nodvardDeck` | `window.__lattice` — **dasselbe Objekt**, Änderungen an einem sieht man am anderen |
| Ereignis `nodvard-deck:navigate` | `lattice:navigate` |
| Ereignis `nodvard-deck:timezone` | `lattice:timezone` |
| CSS-Klasse `nodvard-deck-focus` | `lattice-focus` (beide in einer Regel) |
| TypeScript-Typ `NodvardDeckTokenRefreshResult` | `LatticeTokenRefreshResult` (Alias) |

Die Kern-Shell setzt beide Namen und feuert jedes Ereignis unter beiden Namen. Ein Bundle
oder Kit liest das Objekt als `window.__nodvardDeck ?? window.__lattice` (das UI-Kit tut das über
`deck()` aus `extensions/_shared/frontend/src/deck.ts`) und hört auf **genau ein** Ereignis:
auf `nodvard-deck:…`, wenn `window.__nodvardDeck` existiert, sonst auf `lattice:…`. So reagiert es nie
doppelt. Der Grund für beides: Ein Browser-Tab, der vor einem Update offen war, lädt neue Bundles
in seinen alten Kern (er kennt nur `__lattice`), und Erweiterungen von Dritten mit älterem UI-Kit
laufen an einem neuen Kern. Auch die Import-Map-Shims (`/lattice-shim/react.js` u. a.) lesen beide
Namen; ihr **Pfad** `/lattice-shim/` bleibt unverändert, denn die Seite eines alten Tabs verweist
darauf.

Für Aufrufe gegen die Dashboard-API haben die Seiten der mitgelieferten Erweiterungen
ein gemeinsames UI-Kit (`extensions/_shared/frontend/src/`): `authedFetch` setzt das
Token aus `window.__nodvardDeck.getAccessToken()` und erneuert es bei 401 einmal über
`window.__nodvardDeck.refreshAccessTokenResult()`. Das liefert `{status: "ok", token}`,
`{status: "rejected"}` (der Server lehnt die Anmeldung ab) oder `{status: "unavailable"}`
(er antwortet gerade nicht oder mit einem Fehler, die Anmeldung bleibt) und wirft nie. Hat der
Server mit einem echten Fehler geantwortet (z. B. 507 „Speicherplatz voll“, 500), enthält
`unavailable` zusätzlich `httpStatus` und `message` (sein Status und Grund); ohne diese
Felder (Netzwerkfehler, Zeitlimit, 502/503/504 ohne lesbaren Grund) gibt es keine Aussage des
Servers. Beide Felder sind optional, ältere Kerne liefern sie nicht. Ältere Kerne haben es
noch nicht; dann nimmt `authedFetch` `window.__nodvardDeck.refreshAccessToken()`, das wie
bisher das neue Token oder bei jedem Fehlschlag `null` liefert und ebenfalls nie wirft.
Antwortet der Server nicht — Netzwerkfehler, 502/503/504 ohne eigenen Grund, Erneuern gerade unmöglich —,
wirft `authedFetch` `ServerUnavailableError` mit derselben Meldung wie der Kern („Server
gerade nicht erreichbar – bitte gleich noch einmal versuchen.“). Antwortet der Server beim
Erneuern mit einem echten Fehler und nennt einen Grund, trägt der Fehler `status` und die
Meldung „Server meldet: <Grund>“; ältere Kerne ohne `message` bleiben bei der ersten Meldung. Nur eine wirklich
abgelehnte Anmeldung kommt als 401 („Nicht authentifiziert.“) zurück. `runAction` schlägt
eine Aktion vor, gibt sie bei vorhandenem Recht selbst frei und fragt bei 202 „executing“
alle 3 s `GET /actions/{id}` ab, bis sie fertig ist (Aussetzer des Servers zählen dabei
nicht als Ergebnis).

Bewusst **kein** Module Federation: es koppelt die Extension an das Build-Werkzeug und
die exakte Bundler-Version des Kerns. Ein schlichtes ESM-Modul mit Externals ist
werkzeugneutral — eine Extension kann mit Vite, esbuild oder `tsc` gebaut werden.

```tsx
// extensions/<id>/frontend/src/index.tsx (Beispiel)
// Jede `component` aus dem Manifest ist ein benannter Export des Bundles.
export { SocPage } from "./SocPage";
export { IncidentFeed } from "./IncidentFeed";
```

Ein echtes, minimales Beispiel steht in `extensions/hello-world/frontend/src/`: eine Seite
(`HelloPage.tsx`, mit `authedFetch` und dem UI-Kit), gebaut mit `extensions/hello-world/frontend/build.mjs`.

---

## 6. Connectors — konfigurierbare Verbindungen zu Fremdsystemen

Eine Extension registriert einen **Typ**; der Nutzer legt in der UI **Instanzen** an.

```python
ctx.connectors.register_type(ConnectorType(
    id="ollama",
    label="Ollama",
    icon="cpu",
    schema=OLLAMA_SCHEMA,             # JSON-Schema → Formular
    secret_fields=["api_key"],        # wandern automatisch in den Vault
    test=test_ollama,                 # async (config) -> ConnectorHealth
    factory=build_ollama_client,
))
```

Der Kern übernimmt: Formular, Speichern, Secret-Auslagerung, „Verbindung testen"-Knopf,
periodischer Health-Check, Fehleranzeige im Notification-Center.

Das ersetzt strukturell von Hand gepflegte Listen für Hosts, Web-Adressen und kritische
Container: ein neuer Gameserver ist eine Connector-Instanz bzw. ein entdeckter Host mit
Tag, nicht ein JSON-Eintrag, der unbemerkt veraltet.

---

## 7. Daten, Migrationen, Namensräume

- Tabellen einer Extension heißen zwingend `ext_<id>_<name>` (mit `-` → `_`; nach einer Umbenennung ist auch der
  Präfix jeder alten Kennung erlaubt, siehe §1 „Erweiterung umbenennen“).
  Der Kern prüft das beim Laden und verweigert die Extension sonst.
- Jede Extension hat einen **eigenen Alembic-Branch**
  (`alembic upgrade heads` fährt Kern + alle Extensions hoch).
- `ctx.db` liefert Sessions, deren Zugriff auf den eigenen Präfix beschränkt ist.
  Kern-Daten werden nie direkt gelesen, sondern über `ctx.hosts`, `ctx.audit` usw. —
  sonst wäre das Datenmodell des Kerns eingefroren, sobald die erste Extension existiert.
- Deinstallation: `state=uninstalling` → `on_stop` → optionaler
  `drop_data`-Haken → Tabellen entfernen → Registry-Zeile löschen.

---

## 8. Wie drei Module gegen die Schnittstelle implementiert werden

Das ist der Nachweis, dass die Schnittstelle für sie ausreicht.

### Nodvard Shield

| Baustein | Umsetzung gegen die Schnittstelle |
|---|---|
| Ollama-Chat | `ConnectorType("ollama")` + `AIProvider`-Capability |
| Freitext-Chat-UI | `register_page("soc")`, Streaming über `ctx.ws` |
| Incident-Queue/Batching | eigene Tabellen `ext_nexus_soc_incidents`, Hintergrundtask via `ctx.spawn` |
| Docker-Watcher (8 s) | `ctx.spawn` + `ctx.exec.run()` gegen Hosts mit Tag `docker` |
| Remediation | `ctx.actions.propose(...)` — **nie** `ctx.exec` direkt. Aus KI-Text entsteht nur der Neustart eines abgestürzten Containers (`shell.exec`, den Befehl baut die Extension selbst), alles andere bleibt Text |
| Sperrliste / Anti-Flapping | Kern-Gate; Extension liefert nur zusätzliche Muster |
| Begründungspflicht | `ActionRequest.reason` ist Pflichtfeld |
| Zwei Betriebsmodi | Kern-Einstellung `autonomy.mode`, nicht Extension-Code |
| Geplante Audits (z. B. Lynis nachts) | `ctx.scheduler.register_job(...)` — derselbe Scheduler wie beim Script-Repository |
| ntfy-Meldungen | `ctx.notify.send(...)` → `NotificationChannel` der ntfy-Extension |
| Incident-Feed-Widget | `WidgetSpec` mit `ListView` → erscheint automatisch auch in der Android-App |

Was der Kern dabei **nicht** weiß: dass es Ollama gibt, dass Hosts Proxmox-Gäste sind,
was ein Container ist.

### Script-Repository

| Baustein | Umsetzung |
|---|---|
| Versionierung | eigenes Git-Repo unter `/data/ext/scripts/repo`, `pygit2`/`dulwich`; die Git-Einstellungen setzt die Extension bei jedem Start selbst, und Hooks, Filter und Signieren laufen nie (aus `.git` bringt eine Sicherung nur den Verlauf mit) |
| Metadaten, Parameter | eigene Tabellen + JSON-Schema pro Skript. Im Skript stehen Parameter als `$name` oder `${name}`. Ersetzt werden nur deklarierte Parameter (Wert per `shlex.quote`), `$$` wird zu `$`, jedes andere `$` (`$HOME`, `"$f"`, `$(date)`) bleibt für die Shell; die Prozessnummer der Shell schreibt man deshalb `$$$$` |
| „Jetzt ausführen" | `ctx.actions.propose(ActionSpec("script.run"))` → derselbe SSH-Layer wie das Terminal (kein zweiter Ausführungsweg) |
| Fleet-weiter Zeitplan | `ctx.scheduler` — **eine** Ansicht, weil es **einen** Scheduler gibt |
| Geplante Läufe ohne Klick | Dauerfreigabe je Skript, gespeichert in `ctx.data_dir` (nicht im Git-Repo: ein Zurücksetzen des Skripts belebt eine erloschene Freigabe nicht wieder). Nur echte Zeitplan-Läufe (`current_job_trigger() == "schedule"`) schicken sie als `ActionRequest.standing_approval` mit. Die Extension prüft vorher, ob Inhalt, Parameter, Ziel, Zeitplan sowie Konto, Adresse und SSH-Port der Server noch wie bei der Freigabe sind, das Kern-Gate, ob die freigebende Person noch darf |
| Run-Historie | Kern-Tabellen `jobs`/`job_runs` + Audit |
| Secrets | `ctx.secrets.get_handle(...)`, nie Klartext im Skript |
| In-Browser-Editor | `register_page`, CodeMirror 6 |
| Sicherheits-Gate | dasselbe Kern-Gate wie bei Nodvard Shield — kein zweites Bestätigungs-UI |
| KI „befördert" wiederkehrenden Fix | `ctx.events.subscribe("action.executed")` in der scripts-Extension; Vorschlag als Entwurf im Repo. Die beiden Extensions reden über den Event-Bus, nicht miteinander. |

### Dateimanager-Quellen

Jede Quelle ist ein `FileSource`. `files-sftp` (Host-Admin-Zugriff) liegt in der
terminal-Extension, weil sie den SSH-Layer schon hat; `nextcloud` (WebDAV),
`truenas` (API + SMB/NFS), `syncthing` (REST) sind je eigene Extensions mit je einer
Connector-Instanz pro Server. Der Kern rendert einen Explorer über alle.

---

## 9. Warum das Script-Repository eine *mitgelieferte* Extension ist und kein Core-Modul

Naheliegend wäre, das Script-Repository in den Kern zu legen. Der Nutzen-Gedanke stimmt — das
Repository ist generisch und für jeden Selbsthoster wertvoll. Die Ablage ist trotzdem
eine mitgelieferte Extension, aus drei Gründen:

1. **Es macht die Schnittstelle ehrlich.** Das Script-Repository ist das anspruchsvollste
   generische Modul: Ausführung, Zeitplan, Secrets, Gate, Run-Logs, eigener Editor. Wenn
   es *gegen* die Schnittstelle baubar ist, ist sie tragfähig. Baut man es daneben in den
   Kern, bleibt unbemerkt, welche Lücken die Schnittstelle hat — bis eine Fremd-Extension
   darüber stolpert.
2. **Es hält den Kern leer.** Ein leerer, umbenennbarer Kern
   ([01 §1](01-ARCHITECTURE.md#1-die-drei-schichten)) ist nur dann leer, wenn er wirklich
   nichts Infrastruktur-Spezifisches enthält. Ein Script-Runner im Kern ist eine Funktion,
   die jede Installation mitbringt, ob sie sie braucht oder nicht — und die sich dann nicht
   abschalten ließe.
3. **Für den Nutzer ändert sich nichts.** Mitgeliefert und per Default aktiviert fühlt
   sich an wie Kern. Der Unterschied ist nur, dass sie abschaltbar ist.

Was **wirklich** in den Kern gehört, ist das, was das Script-Repository voraussetzt:
Ausführungs-Layer, Scheduler, Gate, Vault, Run-Log, Audit. Genau die sind Kern-Dienste.

---

## 10. Versionierung und Kompatibilität

`nodvard_sdk.API_VERSION` ist SemVer. Eine Extension deklariert `api_version`.

| Änderung | Version | Verhalten |
|---|---|---|
| Neues optionales Feld, neues Protokoll | Minor | Alte Extensions laufen weiter |
| Feldbedeutung ändert sich, Protokoll-Methode entfällt | Major | Kern lädt alte Extensions nicht, zeigt „inkompatibel (verlangt 1.x, Kern bietet 2.x)" |
| Bugfix | Patch | — |

**SDK-Stabilität (ab sofort):** Auch solange die Version `0.x` ist, ändert sich `nodvard_sdk`
nur abwärtskompatibel. Öffentliche Namen (`__all__` und die öffentlichen Klassen/Funktionen der
Module) werden nicht entfernt oder umbenannt, Parameter nicht gestrichen, und neue Parameter
bekommen einen Vorgabewert. Umbenennen geht nur mit Übergangslösung: der alte Name bleibt mehrere
Releases parallel bestehen (als deprecated dokumentiert). Hintergrund: Nodvard Link, siehe
`docs/04-API.md` §7. Ein Test (`backend/tests/contract/test_sdk_contract.py`) vergleicht gegen
die eingecheckte Liste `sdk_public.json`; neue Namen werden mit
`python scripts/update_api_contract.py` nachgezogen, Ausnahmen nur mit Begründung in
`backend/tests/contract/breaking_exceptions.toml`. Die Major-Version wird auf `1.0` gehoben,
wenn die mitgelieferten Extensions stabil sind; die Regeln der Tabelle oben gelten dann unverändert.

**Bewusst strenger aus Sicherheitsgründen:** `ctx.api.include_router()` ohne `permission=` verlangt eine Anmeldung
(früher war so eine Route ohne Anmeldung erreichbar), und WebSocket-Routen, `add_route()` und `mount()` lehnt der Kern
ohne `public=True` ab; die Erweiterung lässt sich dann nicht einschalten (siehe §2). Die Signatur bleibt
abwärtskompatibel, `public=` ist neu und optional. Ältere Kerne kennen `api.public` und `public=` nicht (eine Erweiterung
mit `api.public` im Manifest lädt dort nicht) und lassen Routen ohne `permission=` offen; wer sie mit unterstützt, setzt
`permission=`.

Ebenso bei der Größe einer Anfrage: Der Kern weist an Erweiterungsrouten jede Anfrage über 1 MiB mit `413` ab; eine
Route, die größere Uploads annimmt, braucht dafür `max_body_bytes` (siehe §2). Ältere Kerne kennen den Namen nicht (der
Import scheitert dort) und haben keine allgemeine Grenze.

Ebenso bei Weiterleitungen: `ctx.http` lehnt `follow_redirects=True` mit `ValueError` ab (siehe §2); ältere Kerne reichen
die Angabe an httpx weiter und folgen dann der Weiterleitung. Und beim Protokoll: `ctx.audit.log()` lehnt Aktionen mit
`mfa.`, `auth.`, `login.`, `system.` oder `user.` am Anfang ab; ältere Kerne schreiben sie.

Ebenso beim Netz: `ctx.http` benutzt keinen Proxy aus der Umgebung (siehe §2). Ein Ziel, das der Rechner nur über so
einen Proxy erreicht, erreicht die Erweiterung damit nicht. Ältere Kerne nehmen bei `get()`, `post()`, `request()` und
`stream()` einen Proxy aus `HTTP_PROXY`, `HTTPS_PROXY` oder `ALL_PROXY`, bei `websocket()` (ab websockets 15) einen aus
`HTTP_PROXY`, `HTTPS_PROXY`, `WS_PROXY`, `WSS_PROXY` oder `SOCKS_PROXY`.
