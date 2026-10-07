"""Update-Helfer, Seite des Dashboards: Zustand zeigen, Update und Rueckweg anfordern, Ergebnisse festhalten.

Die Leitung steht in `core/updater_client.py`. Verbindlich entscheidet immer der Helfer (er prueft Version, Grenzen,
Rueckweg und alles an seinem Ziel selbst); was hier geprueft wird, ist Bequemlichkeit fuer eine klare Antwort, bevor
etwas angefordert wird.

* `status_view()`: was `GET /system/updates/helper` zeigt. `helper_present()` ist dieselbe Quelle fuer
  `updater_available` (`GET /system/info`) und `helper` (`GET /system/updates`).
* `apply()` / `rollback()`: nur fuer den Owner, nachdem Passwort (und ggf. Zwei-Faktor-Code) bestaetigt sind. Beide
  laufen unter einer Sperre, damit zwei gleichzeitige Klicks nie zwei Anforderungen schreiben; dazu hoechstens eine
  Anforderung je 10 Minuten (`REQUEST_WINDOW_S`, die verbindliche Grenze hat der Helfer). Ablehnungen kommen als
  `HelperRefused` mit festem Code (`code`) und kurzem Text.
* **Rueckweg mit Daten:** Hat diese Version beim Start die Datenbank umgebaut (`.boot/state.json`, `last_migration`
  mit `state == "ok"`, `to_version` == diese Version, `from_version` == Vorgaengerversion des Helfers, Kopie
  vorhanden), wird ZUERST die Rueckweg-Vormerkung geschrieben (`bootstate.request_rollback`), dann die Anforderung.
  Die alte Version spielt beim Start die Kopie ein. Passt der Eintrag nicht genau (etwa eine andere Ausgangsversion),
  gibt es keinen Rueckweg ueber den Helfer (409 `data_unclear`): die alte Version zeigte sonst nur die Notseite.
* `record_results()`: jedes Ergebnis des Helfers genau einmal je Anforderungs-ID ins Audit-Protokoll (Akteur
  `system`/`updater`) und, bei den wichtigen Ausgaengen, als Meldung. Die IDs stehen in `<Datenordner>/updater_seen.json`
  (0600, ausserhalb der Datenbank): Das ueberlebt das Zurueckspielen einer Kopie, es gibt also keine doppelten
  Eintraege. Dort steht auch, welche Anforderungen dieses Dashboard selbst geschrieben hat (`requests`). Lehnt der
  Helfer einen Rueckweg ab oder bricht er ihn ab, bevor etwas veraendert wurde (`refused`, `aborted`), wird die
  Vormerkung sofort wieder geloescht (`reconcile_rollback`), damit sie nicht 24 Stunden herumliegt.

Kein Text aus dem Status geht unbereinigt weiter: `core.updater_client` laesst nur feste Codes, Versionen und IDs
durch, und Meldungstexte stehen hier.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
import weakref
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..core import bootstate, rate_limit, updates
from ..core import updater_client as client
from ..core.backup import premigrate
from ..core.backup import restore as _restore
from ..db.session import session_scope
from ..models import AuditEntry
from . import audit as audit_service
from . import update_check as update_check_service

logger = logging.getLogger("nodvard_deck.update_helper")

SEEN_NAME = "updater_seen.json"
SEEN_MAX = 200
REQUESTS_MAX = 20
REQUEST_WINDOW_S = 600
"""Hoechstens eine Anforderung je 10 Minuten (Update und Rueckweg zusammen), wie beim Helfer."""
PENDING_MAX_AGE_S = 30 * 60
"""So lange gilt eine eigene Anforderung ohne Ergebnis als offen (`pending` in der Ansicht)."""
FOLLOW_UP_INTERVAL_S = 30
FOLLOW_UP_MAX_S = 30 * 60
ROLLBACK_BY = "update-helfer"
"""`by` in der Rueckweg-Vormerkung (`bootstate.request_rollback`)."""

NOTIFY_OUTCOMES = frozenset({"applied", "reverted", "rolled_back", "failed_manual", "external_change"})
CLEAR_ROLLBACK_OUTCOMES = frozenset({"refused", "aborted"})
MARKER_SLACK_S = 10
"""Die Vormerkung eines Rueckwegs entsteht unmittelbar vor seiner Anforderung: so weit duerfen beide Zeiten auseinander
liegen, damit sie als zusammengehoerig gelten."""
SUCCESS_OUTCOMES = frozenset({"applied", "reverted"})

_request_window = rate_limit.SlidingWindow(1, REQUEST_WINDOW_S)
_WINDOW_KEY = "global"
_file_lock = threading.Lock()
_seen_in_process: set[str] = set()
"""Schon festgehaltene IDs dieses Prozesses -- falls `updater_seen.json` gerade nicht schreibbar ist."""


class HelperRefused(Exception):
    """Die Anforderung wird nicht geschrieben. `status_code` 409/422/429, `code` ein fester Bezeichner, `message` ein
    kurzer deutscher Satz fuer die Oberflaeche (die genaueren Texte zu den Codes hat das Frontend)."""

    def __init__(self, status_code: int, code: str, message: str, *, retry_after: int | None = None,
                 helper_reason: str | None = None) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retry_after = retry_after
        self.helper_reason = helper_reason


_lock_slots: dict[str, tuple[weakref.ReferenceType[asyncio.AbstractEventLoop], asyncio.Lock]] = {}


def _lock(name: str) -> asyncio.Lock:
    """Eine Sperre je Zweck fuer den laufenden Event-Loop (wie `core.updates._check_lock`)."""
    loop = asyncio.get_running_loop()
    slot = _lock_slots.get(name)
    if slot is None or slot[0]() is not loop:
        slot = (weakref.ref(loop), asyncio.Lock())
        _lock_slots[name] = slot
    return slot[1]


def reset_for_tests() -> None:
    _request_window.reset()
    _seen_in_process.clear()
    _lock_slots.clear()


# ---------------------------------------------------------------------------
# Eigene Datei: gesehene Ergebnisse und eigene Anforderungen
# ---------------------------------------------------------------------------


def _seen_path(settings: Settings) -> Path:
    return Path(settings.data_dir) / SEEN_NAME


def _load_book(settings: Settings) -> dict[str, Any]:
    raw = _restore.read_json(_seen_path(settings), max_bytes=256 * 1024)
    raw = raw if isinstance(raw, dict) else {}
    seen = [i for i in raw.get("seen", []) if client.is_request_id(i)] if isinstance(raw.get("seen"), list) else []
    requests: list[dict[str, Any]] = []
    for entry in raw.get("requests", []) if isinstance(raw.get("requests"), list) else []:
        if not isinstance(entry, dict) or not client.is_request_id(entry.get("id")):
            continue
        if entry.get("action") not in client.ACTIONS or not isinstance(entry.get("at"), int):
            continue
        copy = entry.get("copy")
        requests.append({
            "id": entry["id"], "action": entry["action"], "at": entry["at"],
            "from": entry.get("from") if client.is_version(entry.get("from")) else None,
            "to": entry.get("to") if client.is_version(entry.get("to")) else None,
            "data_revert": entry.get("data_revert") is True,
            "copy": copy if isinstance(copy, str) and bootstate.COPY_NAME_RE.match(copy) else None,
        })
    return {"seen": seen[-SEEN_MAX:], "requests": requests[-REQUESTS_MAX:]}


def _save_book(settings: Settings, book: dict[str, Any]) -> None:
    data = {"v": 1, "seen": book["seen"][-SEEN_MAX:], "requests": book["requests"][-REQUESTS_MAX:]}
    _restore.write_json_atomic(_seen_path(settings), data)


def _remember_request(settings: Settings, entry: dict[str, Any]) -> None:
    with _file_lock:
        book = _load_book(settings)
        book["requests"].append(entry)
        _save_book(settings, book)


def _mark_seen(settings: Settings, ids: list[str]) -> None:
    with _file_lock:
        book = _load_book(settings)
        for request_id in ids:
            if request_id not in book["seen"]:
                book["seen"].append(request_id)
        _save_book(settings, book)


def _seen_ids(settings: Settings) -> set[str]:
    with _file_lock:
        return set(_load_book(settings)["seen"]) | _seen_in_process


def _own_requests(settings: Settings) -> list[dict[str, Any]]:
    with _file_lock:
        return _load_book(settings)["requests"]


# ---------------------------------------------------------------------------
# Ansehen
# ---------------------------------------------------------------------------


def read_status(settings: Settings, *, now: float | None = None) -> client.HelperStatus:
    return client.read_status(settings.updater_dir, now=now)


def helper_present(settings: Settings) -> bool:
    """Gibt es einen Helfer, der gerade antwortet? (`updater_available` bzw. `helper`)"""
    return read_status(settings).present


def _last_result(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    results = (doc or {}).get("results") or []
    if not results:
        return None
    best = max(range(len(results)), key=lambda i: (results[i]["finished_at"], i))
    return dict(results[best])


def _pending(settings: Settings, doc: dict[str, Any] | None, now: float) -> dict[str, Any] | None:
    done = _seen_ids(settings) | {r["id"] for r in (doc or {}).get("results") or []}
    for entry in reversed(_own_requests(settings)):
        if entry["id"] in done or now - entry["at"] > PENDING_MAX_AGE_S:
            continue
        return {"id": entry["id"], "action": entry["action"], "to": entry["to"], "at": entry["at"]}
    return None


FINISHING = "finishing"
"""`ready_reason` der Ansicht, solange der Helfer nach dem Commit noch aufraeumt (siehe `finishing`)."""


def _committed(doc: dict[str, Any] | None) -> bool:
    busy = (doc or {}).get("busy")
    return bool(busy and busy["step"] == "committed")


def finishing(doc: dict[str, Any] | None) -> bool:
    """Der Vorgang ist entschieden, der Helfer raeumt nur noch auf: `busy.step == "committed"`, und das Ergebnis dieser
    Anforderung steht schon in `results`. Das gilt nicht mehr als "beschaeftigt"; eine neue Anforderung nimmt der
    Helfer aber erst nach dem Aufraeumen an.

    Der Helfer schreibt den Status zu `committed` erst mit dem Ergebnis (auch nach einem Absturz, bevor er die Engine
    fragt). Geprueft wird es hier trotzdem: Ein Status mit `committed`, aber ohne dieses Ergebnis gilt weiter als
    beschaeftigt."""
    busy = (doc or {}).get("busy")
    return bool(busy and busy["step"] == "committed" and any(r["id"] == busy["id"] for r in doc["results"]))


def rollback_data(settings: Settings, previous_version: str) -> tuple[bool | None, int | None]:
    """Was beim Rueckweg auf `previous_version` mit den Daten geschieht, fuer die Ansicht: `(True, seit)` -- sie gehen
    mit zurueck, alles seit `seit` (Unix-Zeit: Beginn des Umbaus, die Kopie ist von direkt davor; `None`, wenn der
    Zeitpunkt unlesbar ist) geht verloren; `(False, None)` -- sie bleiben; `(None, None)` -- unklar, der Rueckweg wird
    mit `data_unclear` abgelehnt (dieselbe Entscheidung wie `data_plan`)."""
    try:
        revert, _copy = data_plan(settings, previous_version)
    except HelperRefused:
        return None, None
    except Exception:  # noqa: BLE001 - nur eine Anzeige: dann eben unklar
        logger.warning("update_helper_rollback_data_unreadable")
        return None, None
    if not revert:
        return False, None
    since = _iso_seconds((bootstate.read_state(settings.data_dir).get("last_migration") or {}).get("at"))
    return True, int(since) if since is not None else None


def status_view_sync(settings: Settings, *, now: float | None = None) -> dict[str, Any]:
    """Die Ansicht fuer `GET /system/updates/helper`. Solange der Helfer nach dem Commit aufraeumt (`busy.step` =
    `committed`), zeigt sie keinen Rueckweg: `previous` im Status kann dann noch den alten Stand zeigen -- nach einem
    Update den Rueckweg, den der neue gleich ersetzt (der neue Slot wird erst nach dem Schutz-Tag des alten gespeichert),
    nach einem Rueckweg den gerade benutzten. Der neue erscheint, sobald der Helfer fertig ist. Zu `previous` kommt,
    was dabei mit den Daten geschieht (`data_revert`, `data_since`, siehe `rollback_data`)."""
    current = time.time() if now is None else now
    status = read_status(settings, now=current)
    doc = status.doc if status.present else None
    done = finishing(doc)
    previous = doc.get("previous") if doc and not _committed(doc) else None
    if previous is not None and previous["until"] <= current:
        previous = None
    if previous is not None:
        data_revert, data_since = rollback_data(settings, previous["version"])
        previous = {**previous, "data_revert": data_revert, "data_since": data_since}
    return {
        "present": status.present,
        "reason": status.reason,
        "ready": bool(doc and doc["ready"] and not done),
        "ready_reason": FINISHING if done else (doc["reason"] if doc else None),
        "state": ("idle" if done and doc["state"] == "busy" else doc["state"]) if doc else None,
        "helper_version": doc["helper_version"] if doc else None,
        "heartbeat_at": doc["heartbeat_at"] if doc else None,
        "target": dict(doc["target"]) if doc and doc["target"] else None,
        "busy": dict(doc["busy"]) if doc and doc["busy"] and not done else None,
        "previous": previous,
        "last_result": _last_result(status.doc),
        "pending": _pending(settings, status.doc, current),
    }


async def status_view(settings: Settings) -> dict[str, Any]:
    return await asyncio.to_thread(status_view_sync, settings)


# ---------------------------------------------------------------------------
# Anfordern
# ---------------------------------------------------------------------------


def _require_ready(status: client.HelperStatus) -> dict[str, Any]:
    if not status.present or status.doc is None:
        raise HelperRefused(409, "helper_missing", "Der Update-Helfer ist nicht eingerichtet oder antwortet nicht.",
                            helper_reason=status.reason)
    doc = status.doc
    if finishing(doc):
        raise HelperRefused(409, "helper_finishing",
                            "Der Update-Helfer räumt nach dem letzten Vorgang noch auf. Versuch es gleich noch einmal.")
    if doc["busy"] is not None or doc["state"] == "busy":
        raise HelperRefused(409, "helper_busy", "Der Update-Helfer ist gerade beschäftigt.")
    if not doc["ready"] or doc["target"] is None:
        raise HelperRefused(409, "helper_not_ready", "Der Update-Helfer ist nicht bereit.",
                            helper_reason=doc["reason"])
    return doc


def _require_no_pending(settings: Settings) -> None:
    try:
        open_ids = client.pending_requests(settings.updater_dir)
    except client.ChannelError as exc:
        raise HelperRefused(409, "channel_unsafe", "Der Kanal zum Update-Helfer ist nicht sicher eingerichtet.",
                            helper_reason=exc.code) from None
    if open_ids:
        raise HelperRefused(409, "request_pending", "Es wartet schon eine Anforderung auf den Update-Helfer.")


def _hit_window() -> None:
    wait = _request_window.hit(_WINDOW_KEY)
    if wait:
        raise HelperRefused(
            429, "rate_limited",
            f"Gerade erst angefordert. Bitte in {rate_limit.wait_text(wait)} erneut versuchen.", retry_after=wait,
        )


def _write(settings: Settings, *, action: str, version: str) -> dict[str, Any]:
    try:
        return client.write_request(settings.updater_dir, action=action, version=version)
    except client.ChannelError as exc:
        _request_window.reset()  # es wurde nichts angefordert
        raise HelperRefused(409, "channel_unsafe", "Der Kanal zum Update-Helfer ist nicht sicher eingerichtet.",
                            helper_reason=exc.code) from None


def _check_update(settings: Settings, doc: dict[str, Any], version: str, cached: dict[str, Any]) -> str:
    """Die Pruefungen vor einem Update; gibt die laufende Version zurueck."""
    target = doc["target"]
    if not updates.is_official_image(settings.image):
        raise HelperRefused(409, "not_official_image", "Diese Installation nutzt nicht das offizielle Image.")
    if target["pinned"] or target["floating_tag"] is None:
        raise HelperRefused(409, "pinned", "Die Version ist in der Compose-Datei fest eingetragen. Ändere sie dort.")
    if not client.is_version(version):
        if updates.is_version(version):
            raise HelperRefused(422, "prerelease", "Vorabversionen spielt der Update-Helfer nicht ein.")
        raise HelperRefused(422, "bad_version", "Das ist keine gültige Version.")
    if cached.get("latest") != version:
        raise HelperRefused(422, "not_latest", "Das ist nicht die neueste gefundene Version. Suche zuerst nach Updates.")
    running = cached.get("current") or updates.running_version(settings)
    current = target["current_version"] or running
    if not updates.is_newer(version, running) or not updates.is_newer(version, current):
        raise HelperRefused(422, "not_newer", "Diese Version ist nicht neuer als die laufende.")
    if not client.tag_fits_version(target["floating_tag"], version):
        raise HelperRefused(422, "tag_mismatch", "Diese Version passt nicht zum Tag in der Compose-Datei.")
    return current


async def apply(session: AsyncSession, settings: Settings, *, version: str) -> dict[str, Any]:
    """Update auf `version` anfordern. Gibt `{request_id, action, from, to}` zurueck oder wirft `HelperRefused`."""
    await record_results(settings)  # erst festhalten, was der Helfer schon entschieden hat
    async with _lock("request"):
        status = await asyncio.to_thread(read_status, settings)
        doc = _require_ready(status)
        cached = await update_check_service.current_status(session, settings)
        current = _check_update(settings, doc, version, cached)
        await asyncio.to_thread(_require_no_pending, settings)
        _hit_window()
        request = await asyncio.to_thread(_write, settings, action="update", version=version)
        entry = {"id": request["id"], "action": "update", "at": request["created_at"], "from": current, "to": version,
                 "data_revert": False, "copy": None}
        await _remember(settings, entry)
        logger.info("update_requested id=%s to=%s", request["id"], version)
        return {"request_id": request["id"], "action": "update", "from": current, "to": version}


def data_plan(settings: Settings, previous_version: str) -> tuple[bool, str | None]:
    """Muessen beim Rueckweg auf `previous_version` die Daten mit zurueck? `(True, kopie)`, `(False, None)` oder
    `HelperRefused(409, "data_unclear")`, wenn diese Version die Datenbank umgebaut hat, der Eintrag aber nicht genau
    zum Rueckweg passt oder die Kopie fehlt."""
    state = bootstate.read_state(settings.data_dir)
    migration = state.get("last_migration") or {}
    running = updates.running_version(settings)
    if not migration or migration.get("to_version") != running:
        return False, None  # diese Version hat beim Start nichts umgebaut: die Daten passen auch zur alten
    copy = migration.get("copy")
    if (migration.get("state") == "ok" and migration.get("from_version") == previous_version and copy
            and premigrate.copy_path(settings.data_dir, copy) is not None):
        return True, copy
    raise HelperRefused(
        409, "data_unclear",
        "Diese Version hat beim Start die Datenbank umgebaut, aber die passende Kopie von vorher fehlt. "
        "Ein Rückweg über den Update-Helfer ist deshalb nicht möglich.",
    )


async def rollback(settings: Settings, *, accept_data_loss: bool) -> dict[str, Any]:
    """Rueckweg auf die Vorgaengerversion anfordern. Gibt `{request_id, action, from, to, data_revert, copy}` zurueck
    oder wirft `HelperRefused`."""
    await record_results(settings)  # erst festhalten, was der Helfer schon entschieden hat
    async with _lock("request"):
        status = await asyncio.to_thread(read_status, settings)
        doc = _require_ready(status)
        previous = doc["previous"]
        if previous is None or previous["until"] <= time.time():
            raise HelperRefused(409, "no_previous", "Es gibt keine Vorgängerversion, auf die du zurück kannst.")
        to_version = previous["version"]
        current = doc["target"]["current_version"] or updates.running_version(settings)
        await asyncio.to_thread(_require_no_pending, settings)
        data_revert, copy = await asyncio.to_thread(data_plan, settings, to_version)
        if data_revert and not accept_data_loss:
            raise HelperRefused(
                422, "accept_data_loss",
                "Beim Rückweg geht alles verloren, was seit dem Update geändert wurde. Bitte bestätige das.",
            )
        _hit_window()
        if data_revert:
            assert copy is not None
            try:
                await asyncio.to_thread(bootstate.request_rollback, settings.data_dir, copy, by=ROLLBACK_BY)
            except OSError:
                # Ohne Vormerkung keine Anforderung (die alte Version zeigte sonst nur die Notseite). Angefordert wurde
                # nichts, das Fenster gilt also nicht als verbraucht (`write_json_atomic` schreibt ganz oder gar nicht).
                _request_window.reset()
                logger.warning("update_helper_rollback_marker_not_written")
                raise HelperRefused(
                    409, "marker_failed",
                    "Die Vormerkung für den Rückweg ließ sich nicht speichern (ist der Speicher voll?). "
                    "Es wurde nichts angefordert.",
                ) from None
        try:
            request = await asyncio.to_thread(_write, settings, action="rollback", version=to_version)
        except BaseException:
            if data_revert:
                await asyncio.to_thread(bootstate.clear_rollback, settings.data_dir)
            raise
        entry = {"id": request["id"], "action": "rollback", "at": request["created_at"], "from": current,
                 "to": to_version, "data_revert": data_revert, "copy": copy}
        await _remember(settings, entry)
        logger.info("rollback_requested id=%s to=%s data_revert=%s", request["id"], to_version, data_revert)
        return {"request_id": request["id"], "action": "rollback", "from": current, "to": to_version,
                "data_revert": data_revert, "copy": copy}


async def _remember(settings: Settings, entry: dict[str, Any]) -> None:
    try:
        await asyncio.to_thread(_remember_request, settings, entry)
    except OSError:
        logger.warning("update_helper_book_not_written")


# ---------------------------------------------------------------------------
# Ergebnisse
# ---------------------------------------------------------------------------

_FLOW_TEXTS = {
    "exited": "Die neue Version hat sich gleich wieder beendet.",
    "restart_loop": "Die neue Version ist immer wieder neu gestartet.",
    "rescue_page": "Die neue Version hat nur die Notseite gezeigt.",
    "timeout": "Die neue Version ist nicht rechtzeitig bereit geworden.",
    "create_failed": "Die neue Version ließ sich nicht anlegen.",
    "clone_mismatch": "Die neue Version hätte andere Einstellungen bekommen als die alte.",
    "start_failed": "Die neue Version ließ sich nicht starten.",
    "stop_failed": "Die alte Version ließ sich nicht anhalten.",
}


DATA_RESET = "reset"
DATA_KEPT = "kept"
DATA_UNCLEAR = "unclear"
_DATA_TEXTS = {
    DATA_RESET: " Die Datenbank wurde dabei aber schon auf den Stand vor dem Update zurückgesetzt. Was du seitdem "
                "geändert hast, liegt im Datenordner unter restore/replaced-….",
    DATA_UNCLEAR: " Die Datenbank wurde dabei vielleicht auf den Stand vor dem Update zurückgesetzt. Sieh im "
                  "Datenordner unter restore/replaced-… nach: Dort liegen dann deine Änderungen seit dem Update.",
}


def _iso_seconds(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def data_after_failed_rollback(settings: Settings, entry: dict[str, Any]) -> str:
    """Was ist nach einem gescheiterten eigenen Rueckweg mit Daten (`rolled_back`) mit der Datenbank? Die alte Version
    hat beim Start vielleicht schon die Kopie eingespielt; die neuere Version, die der Helfer danach wieder startet, hat
    sie dann erneut umgebaut (`last_migration` von der alten auf die neuere Version, nach der Anforderung). Steht dort
    noch der Umbau, zu dem die Kopie gehoert, sind die Daten unveraendert. Sonst ist es unklar."""
    migration = bootstate.read_state(settings.data_dir).get("last_migration") or {}
    at = _iso_seconds(migration.get("at"))
    if at is None:
        return DATA_UNCLEAR
    if (at >= entry["at"] - MARKER_SLACK_S and migration.get("from_version") == entry["to"]
            and migration.get("to_version") == entry["from"]):
        return DATA_RESET
    if at < entry["at"] and migration.get("copy") == entry["copy"]:
        return DATA_KEPT
    return DATA_UNCLEAR


def _message(result: dict[str, Any], data: str | None = None) -> tuple[str, str, str]:
    """(Titel, Text, Schwere) einer Meldung zu einem Ergebnis. `data`: siehe `data_after_failed_rollback` (nur bei einem
    eigenen Rueckweg mit Daten, der in `rolled_back` geendet hat)."""
    action, outcome = result["action"], result["outcome"]
    old, new = result["from"] or "?", result["to"] or "?"
    reason = _FLOW_TEXTS.get(result["code"] or "")
    why = f" {reason}" if reason else ""
    if outcome == "applied":
        return "Nodvard Deck ist aktualisiert", f"Version {new} läuft jetzt (vorher {old}).", "info"
    if outcome == "reverted":
        return "Zurück auf die vorige Version", f"Version {new} läuft wieder (vorher {old}).", "info"
    if outcome == "rolled_back":
        if action == "rollback":
            hint = _DATA_TEXTS.get(data or "", "")
            return ("Rückweg hat nicht geklappt",
                    f"Der Rückweg auf {new} hat nicht geklappt.{why} Version {old} läuft weiter.{hint}", "warning")
        return ("Update zurückgenommen", f"Das Update auf {new} hat nicht geklappt.{why} Version {old} läuft wieder.",
                "warning")
    if outcome == "failed_manual":
        before = f"Version {result['from']}, die vorher lief," if result["from"] else "die Version, die vorher lief,"
        text = (f"Der Update-Helfer konnte {before} nicht wieder starten und hat danach nichts mehr verändert. Was du "
                "jetzt auf dem Server tun kannst, steht unter Einstellungen → System → Updates.")
        return "Update: bitte von Hand nachsehen", text, "critical"
    text = ("Während des Vorgangs wurde von außen etwas am Container geändert. Der Update-Helfer hat deshalb "
            "nichts weiter getan. Sieh bitte nach, welche Version jetzt läuft.")
    return "Update abgebrochen", text, "warning"


async def _log_result(session: AsyncSession, settings: Settings, result: dict[str, Any],
                      own: dict[str, Any] | None = None) -> None:
    outcome = result["outcome"]
    data: str | None = None
    if own is not None and own["data_revert"] and result["action"] == "rollback" and outcome == "rolled_back":
        try:
            data = await asyncio.to_thread(data_after_failed_rollback, settings, own)
        except Exception:  # noqa: BLE001 - dann eben unklar
            data = DATA_UNCLEAR
    await audit_service.log(
        session, actor_type="system", actor_id="updater", action=f"system.update.{outcome}",
        outcome="success" if outcome in SUCCESS_OUTCOMES else "failure", target_type="system",
        target_id=result["id"], reason=_DATA_TEXTS[data].strip() if data in _DATA_TEXTS else None,
        detail={"request_id": result["id"], "action": result["action"], "from": result["from"],
                "to": result["to"], "code": result["code"]} | ({"data": data} if data else {}),
    )
    if outcome in NOTIFY_OUTCOMES:
        from . import notifications as notifications_service

        title, body, severity = _message(result, data)
        try:
            await notifications_service.send(
                session, title=title, body=body, severity=severity, correlation_id=result["id"],
                payload={"path": "/settings/system", "tags": ["update"]},
            )
        except Exception:
            logger.warning("update_helper_notification_failed id=%s", result["id"], exc_info=True)


def reconcile_rollback(settings: Settings, results: list[dict[str, Any]]) -> bool:
    """Loescht die Rueckweg-Vormerkung, wenn der Helfer genau den eigenen Rueckweg mit Daten abgelehnt oder vor jeder
    Aenderung abgebrochen hat. `True`, wenn geloescht wurde."""
    mine = {e["id"]: e for e in _own_requests(settings) if e["action"] == "rollback" and e["data_revert"]}
    for result in results:
        entry = mine.get(result["id"])
        if entry is None or result["action"] != "rollback" or result["outcome"] not in CLEAR_ROLLBACK_OUTCOMES:
            continue
        pending = bootstate.read_rollback(settings.data_dir)
        # Nur die Vormerkung GENAU dieser Anforderung: sie entsteht unmittelbar vor ihr. Eine spaetere (ein neuer
        # Rueckweg auf dieselbe Kopie) bleibt, auch wenn die alte Ablehnung erst jetzt gelesen wird.
        if (pending is not None and pending["by"] == ROLLBACK_BY and pending["copy"] == entry["copy"]
                and abs(pending["requested_at"] - entry["at"]) <= MARKER_SLACK_S):
            bootstate.clear_rollback(settings.data_dir)
            return True
    return False


async def _already_logged(session: AsyncSession, request_id: str) -> bool:
    """Steht das Ergebnis schon im Audit-Protokoll (falls `updater_seen.json` nicht geschrieben werden konnte)?"""
    found = await session.execute(
        select(AuditEntry.id).where(
            AuditEntry.actor_type == "system", AuditEntry.actor_id == "updater", AuditEntry.target_id == request_id,
            AuditEntry.action.startswith("system.update."),
        ).limit(1)
    )
    return found.first() is not None


async def record_results(settings: Settings) -> int:
    """Neue Ergebnisse des Helfers festhalten (siehe Modulkopf). Gibt die Zahl der neu eingetragenen zurueck. Nie ein
    Grund, nicht zu starten oder eine Anfrage scheitern zu lassen."""
    try:
        async with _lock("results"):
            status = await asyncio.to_thread(read_status, settings)
            if status.doc is None:
                return 0
            seen = await asyncio.to_thread(_seen_ids, settings)
            fresh: list[dict[str, Any]] = []
            for entry in status.doc["results"]:
                if entry["id"] not in seen and all(entry["id"] != other["id"] for other in fresh):
                    fresh.append(entry)  # je ID nur das erste, auch wenn der Status sie doppelt nennt
            if not fresh:
                return 0
            fresh.sort(key=lambda r: r["finished_at"])
            own = {e["id"]: e for e in await asyncio.to_thread(_own_requests, settings)}
            logged = 0
            async with session_scope() as session:
                for result in fresh:
                    if not await _already_logged(session, result["id"]):
                        await _log_result(session, settings, result, own.get(result["id"]))
                        logged += 1
            ids = [r["id"] for r in fresh]
            _seen_in_process.update(ids)
            try:
                await asyncio.to_thread(_mark_seen, settings, ids)
            except OSError:
                logger.warning("update_helper_seen_not_written")
            await asyncio.to_thread(reconcile_rollback, settings, fresh)
            if any(r["outcome"] == "refused" and r["id"] in own for r in fresh):
                _request_window.reset()  # eine abgelehnte Anforderung zaehlt beim Helfer nicht, hier auch nicht
            return logged
    except Exception:
        logger.exception("update_helper_record_results_failed")
        return 0


def follow_up_needed(settings: Settings) -> bool:
    """Ist beim Start noch etwas offen (Helfer beschaeftigt oder eigene Anforderung ohne Ergebnis)?"""
    status = read_status(settings)
    doc = status.doc
    if doc is not None and (doc["busy"] is not None or doc["state"] == "busy") and not finishing(doc):
        return True
    return _pending(settings, status.doc, time.time()) is not None


async def follow_up(settings: Settings, *, interval_s: float = FOLLOW_UP_INTERVAL_S,
                    max_s: float = FOLLOW_UP_MAX_S) -> None:
    """Nachlauf nach dem Start: alle `interval_s` Sekunden die Ergebnisse festhalten, bis nichts mehr offen ist,
    hoechstens `max_s` Sekunden."""
    deadline = time.monotonic() + max_s
    while time.monotonic() < deadline:
        await asyncio.sleep(interval_s)
        await record_results(settings)
        try:
            if not await asyncio.to_thread(follow_up_needed, settings):
                return
        except Exception:  # noqa: BLE001
            logger.warning("update_helper_follow_up_check_failed")


def start_follow_up(settings: Settings) -> asyncio.Task | None:
    """Startet den Nachlauf, wenn noetig. Fehler beim Pruefen heissen: kein Nachlauf."""
    try:
        needed = follow_up_needed(settings)
    except Exception:  # noqa: BLE001
        return None
    return asyncio.create_task(follow_up(settings)) if needed else None


async def stop_follow_up(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


__all__ = [
    "HelperRefused", "apply", "data_plan", "follow_up", "helper_present", "reconcile_rollback", "record_results",
    "rollback", "start_follow_up", "status_view", "stop_follow_up",
]
