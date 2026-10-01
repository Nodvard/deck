# 02 — Die Extension-Schnittstelle

Der Kern kennt keine Extension. Er kennt **Fähigkeiten**. Eine Extension meldet an, welche
davon sie erfüllt, und der Kern benutzt sie generisch.

Dieses Dokument ist die Spezifikation. Der zugehörige, importierbare Vertrag liegt in
`sdk/python/nodvard_sdk/`.

---

## 1. Anatomie einer Extension

```
extensions/nexus-soc/
├─ extension.toml            ← Manifest, ohne Code-Import lesbar
├─ pyproject.toml            ← optional; für pip-installierbare Extensions
├─ src/nodvard_deck_ext_nexus_soc/
│  ├─ __init__.py            ← enthält  class Extension(NodvardExtension)
│  ├─ api.py                 ← APIRouter
│  ├─ connectors.py          ← Ollama-, ntfy-Connector-Typen
│  ├─ capabilities.py        ← ActionExecutor, AIProvider …
│  ├─ widgets.py             ← deklarative WidgetSpecs
│  ├─ pipeline.py            ← Incident-Queue/Batching
│  ├─ models.py              ← eigene Tabellen (Präfix ext_nexus_soc_)
│  └─ migrations/            ← eigener Alembic-Branch
└─ frontend/
   ├─ package.json
   └─ src/index.tsx          ← registerPage/registerWidget, ESM-Bundle
```

### Manifest (`extension.toml`)

```toml
[extension]
id          = "nexus-soc"          # stabil, kleinschreibung, Namensraum für alles
name        = "Nodvard Shield"
version     = "0.1.0"
api_version = "1.0"                # SemVer gegen nodvard_sdk.API_VERSION
author      = "…"
description = "Autonome Diagnose und Remediation für die eigene Flotte."
icon        = "shield-check"
entrypoint  = "nodvard_deck_ext_nexus_soc:Extension"
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
`nodvard_deck_ext_<id>` (Bindestriche der ID werden zu `_`, also `nodvard_deck_ext_nexus_soc`) und
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
`GET /extensions`.

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

### Einstellungs-Schema: `settings.schema.json` und die `x-*`-Zusätze

Der Kern baut aus dem JSON-Schema die Einstellungsseite der Extension (Einstellungen →
Erweiterungen). Unterstützt sind `type` (`string`, `integer`, `number`, `boolean`, `array`,
`object`), `title`, `description`, `default`, `enum`, `required`, `properties`, `items` und
`pattern`. Dazu diese Zusätze (alle optional, unbekannte werden ignoriert):

| Zusatz | Wo | Wirkung |
|---|---|---|
| `x-advanced` | Feld | Steht unter „Erweitert“, eingeklappt. |
| `x-hidden` | Feld | Wird nicht angezeigt (interner Zustand). |
| `x-item-title` | Liste von Objekten | Wie ein Eintrag heißt („Server hinzufügen“). |
| `x-enum-labels` | Feld mit `enum` | Lesbare Texte je Wert: `{"all": "Alle Updates"}`. |
| `x-widget: "schedule"` | Text (Cron) | Zeitplan-Wähler statt Cron-Feld. |
| `x-widget: "host"` | Text oder Liste von Texten | Auswahl aus den Servern (Wert = Servername, aus `GET /hosts`). Liste: Chips mit „Server hinzufügen“. Ohne Serverliste: Textfeld bzw. Wortliste. |
| `x-widget: "host-tag"` | Text oder Liste von Texten | Wie `host`, aber Auswahl aus den Markierungen (Tags) der Server, mit Anzahl. Eine gespeicherte Markierung, die es nicht mehr gibt, bleibt sichtbar. |
| `x-widget: "remote-select"` | Text | Auswahlliste, die die Extension selbst liefert (z. B. die Modelle eines KI-Servers). Braucht `x-options-url`. Schlägt die Abfrage fehl oder ist die Liste leer, erscheint ein Textfeld und der Grund. |
| `x-options-url` | `remote-select` | Pfad unter `/api/v1`, z. B. `/ext/nexus-soc/ai/models?which=primary`. Die Oberfläche schickt keine Formularwerte mit; die Extension fragt nur, was gespeichert ist (kein Adress-Parameter – sonst wäre die Route ein Weg für Anfragen an beliebige Adressen, samt Schlüssel). Nach einer neuen Adresse: speichern, dann „Neu laden“. Antwort: `{"options": [{"value": "…", "label": "…"}], "error": null}`; bei Problemen `options: []` und `error` als deutscher Satz (kein Fehlerstatus). |
| `x-empty-label` | Auswahl | Text für „nichts gewählt“ (Standard „– nicht gesetzt –“), z. B. „alle Server“. |
| `pattern` | Text, Wörter einer Liste | Regulärer Ausdruck (wie JSON-Schema, nicht verankert). Die Oberfläche zeigt die Meldung am Feld und sperrt „Speichern“; das Backend prüft dasselbe (`422`). Leere Werte sind immer erlaubt. |
| `x-pattern-message` | neben `pattern` | Deutsche Meldung, wenn das Muster nicht passt (Standard: „Das Format stimmt nicht.“). |
| `x-test-message` | Kopfebene | `true`, wenn die Extension einen `NotificationChannel` bereitstellt: die Verbindungskarte zeigt „Testnachricht senden“. |
| `x-secrets` | Kopfebene | Liste der Geheimnisse (Tresor-Labels): `{"label": "proxmox-token:{name}", "title": "…", "description": "…", "per_item": "connections", "optional": true}`. `per_item` erzeugt je Eintrag der genannten Liste ein Geheimnis (über dessen `name`). `optional: true`: die Extension arbeitet auch ohne; fehlt ein nicht-optionales Geheimnis oder ein `required`-Feld, meldet `GET /extensions` `needs_setup`. |

**Verbindung testen.** Jede Extension mit Einstellungen bekommt in der Oberfläche den Knopf
„Verbindung testen“ (`POST /extensions/{id}/test`). Der Kern ruft dafür `health()` der
Extension auf (und `test()` ihres `NotificationChannel`, falls sie einen bereitstellt) und
übersetzt technische Fehler in verständliche deutsche Sätze (keine Antwort, Zugangsdaten
abgelehnt, Zertifikat, Adresse nicht gefunden …). Für gute Meldungen genügt es, in
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
| `ctx.api` | `include_router(r)` → `/api/v1/ext/<id>/…` | — |
| `ctx.ui` | `register_page()`, `register_widget()`, `register_nav()` | — |
| `ctx.connectors` | `register_type(ConnectorType)` | — |
| `ctx.capabilities` | `provide(protokoll_instanz)` | je Protokoll |
| `ctx.actions` | `register(ActionSpec)`, `propose(ActionRequest)` | `hosts.execute` u. a. |
| `ctx.hosts` | `list()`, `get()`, `upsert_discovered()` | `hosts.read` / `hosts.write` |
| `ctx.exec` | `run(host, command)`, `stream(host, command)` (laufende Ausgabe, z. B. Live-Logs), `open_shell(host)`, `sftp(host)` | `hosts.execute` |
| `ctx.secrets` | `get_handle(label)`, `create()` | `secrets.read:<label>` |
| `ctx.vault_use` | Context-Manager, der einen Wert nur im Speicher materialisiert | dito |
| `ctx.db` | AsyncSession-Factory, **nur** auf `ext_<id>_*`-Tabellen | — |
| `ctx.settings` | `declare(schema)`, `get()`, `set()` | — |
| `ctx.scheduler` | `register_job(JobSpec)` | `schedule.register` |
| `ctx.events` | `publish(Event)`, `subscribe(pattern, handler)` | — |
| `ctx.notify` | `send(Notification, raise_on_failure=False)` → `NotifyResult` (`notification_id`, `suppressed`) – mit `raise_on_failure=True` wirft `NotificationNotDelivered`, wenn es Kanäle gab und keiner zugestellt hat (für „erneut versuchen“); `would_suppress(host_id=…, host_ids=…)` fragt nur, ob ein Wartungsfenster gerade still schalten würde (siehe unten) | `notify.send` |
| `ctx.audit` | `log(entry)` | `audit.write` |
| `ctx.http` | vorkonfigurierter `httpx.AsyncClient` mit Zielprüfung, dazu `websocket(url, …)` | `net.outbound:<cidr>` |
| `ctx.ws` | `broadcast(channel, payload)` auf `ext.<id>.*` | — |
| `ctx.spawn` | überwachter Hintergrundtask mit Neustart-Politik | — |
| `ctx.logger` | strukturierter Logger, vorgetaggt mit der Extension-ID | — |

Nicht am Kontext und bewusst nicht verfügbar: direkter Zugriff auf Kern-Tabellen, das
`asyncio`-Loop-Objekt, andere Extensions (nur über `ctx.events` und deklarierte
`requires` + `ctx.capabilities.query()`).

### Meldungen und Wartungsfenster

Eine Meldung mit `payload["host_id"]` (ein Server) oder `payload["host_ids"]`
(Sammelmeldung, still nur, wenn **alle** Server in einem laufenden Fenster liegen)
schaltet ein Wartungsfenster stumm: Sie steht im Verlauf, geht aber an keinen Kanal
(docs/03 §9). Ohne Host-Bezug ist eine Meldung nie still.

- `send()` gibt `NotifyResult(notification_id, suppressed)` zurück. `suppressed=True`
  heißt: nur im Verlauf, kein Push. Ältere Kerne und Test-Doubles liefern `None` –
  Extensions werten das wie `suppressed=False` aus (z. B.
  `getattr(result, "suppressed", False) is True`). Wer den Rückgabewert nicht braucht,
  ändert nichts.
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
| `AIProvider` | nexus-soc (Ollama), künftig andere | Chat, Diagnose, Zusammenfassungen |
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
    refresh=Refresh(interval_s=60, ws_channel="ext.nexus-soc.incidents"),
    data_endpoint="widgets/incidents",        # relativ zu /api/v1/ext/nexus-soc/
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
    path="/soc",                      # wird zu /ext/nexus-soc/soc
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
(`HelloPage.tsx`), gebaut mit `extensions/hello-world/frontend/build.mjs`.

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

- Tabellen einer Extension heißen zwingend `ext_<id>_<name>` (mit `-` → `_`).
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
| Remediation | `ctx.actions.propose(...)` — **nie** `ctx.exec` direkt |
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
| Versionierung | eigenes Git-Repo unter `/data/ext/scripts/repo`, `pygit2`/`dulwich` |
| Metadaten, Parameter | eigene Tabellen + JSON-Schema pro Skript |
| „Jetzt ausführen" | `ctx.actions.propose(ActionSpec("script.run"))` → derselbe SSH-Layer wie das Terminal (kein zweiter Ausführungsweg) |
| Fleet-weiter Zeitplan | `ctx.scheduler` — **eine** Ansicht, weil es **einen** Scheduler gibt |
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
