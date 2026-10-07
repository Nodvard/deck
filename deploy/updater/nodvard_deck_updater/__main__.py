"""Hauptprogramm des Helfers: `python -I -m nodvard_deck_updater`.

* **Schleife alle 5 s** (`Helper.tick`), immer in dieser Reihenfolge:

  1. Einrichtung bzw. Zustand und Journal neu laden, falls noetig.
  2. **Wiederaufnahme vor allem anderen:** Steht ein Journal in `/state` (ein Vorgang kam beim letzten Lauf nicht zu
     Ende, etwa nach einem Absturz oder einem Neustart des Rechners), nimmt `Flow.recover` ihn wieder auf -- vor der
     Vorpruefung und bevor eine Anforderung gelesen wird. Keine Grenze bremst das, und die Aktion zaehlt nicht noch
     einmal. Ist die Engine gerade nicht erreichbar (oder die eigene ID unbekannt), bleibt das Journal stehen, und die
     naechste Runde versucht es erneut; Anforderungen bekommen bis dahin `busy`. Steht es auf `committed`, ist das
     Ergebnis trotzdem schon eingetragen: Das geschieht beim Laden des Journals, ohne Engine. Auch ein Fehler im
     Programm haelt die Runde dabei nicht auf (ebenso beim Aufraeumen des Slots).
  3. Ohne Journal: die **Vorpruefung**, falls faellig (beim Start, danach alle 5 min, solange nicht bereit alle 30 s,
     nach jedem Vorgang sofort), und einen abgelaufenen Rueckweg-Slot aufraeumen (`Flow.drop_expired_slot`).
  4. Kanal lesen (`Channel.poll`), Anforderungen pruefen und ausfuehren, Status schreiben.

* **Eine Anforderung** durchlaeuft: Format (`policy.parse_request`), die Grenzen aus `/state` (`StateStore.check`:
  Wiederholung derselben ID, Sperre nach kaputtem Zustand, eine Aktion je 10 min, 24 h gesperrte Version). Dann wird die
  ID gemerkt und gespeichert (auch fuer abgelehnte), dann: kein laufender Vorgang, eine frische Vorpruefung (hoechstens
  eine je Runde), Tag und Version bzw. der Rueckweg-Slot. Erst dann fuehrt `Flow.run` sie aus (Update oder Rueckweg).
  **Hoechstens ein Ablauf je Runde:** weitere Anforderungen derselben Runde bekommen `busy`.
* **Ergebnisse** stehen in `state.json` (`State.results`: die letzten 10, eins je Anforderungs-ID) und von dort im
  Status, so ueberstehen sie einen Neustart des Helfers. Ablehnungen vor dem Ablauf traegt die Schleife ein, die
  Ergebnisse eines Ablaufs (auch seine Ablehnungen) `Flow` selbst. **Das erste Ergebnis einer ID bleibt:** eine
  Wiederholung derselben ID (`replay`) ueberschreibt es nicht, sonst saehe das Dashboard ein eingespieltes Update
  ploetzlich als abgelehnt.
* **Waehrend eines Vorgangs** haengt die Schleife im Ablauf (bis zu 15 min Beobachten, dazu ein Rueckbau). Die
  regelmaessige Vorpruefung ruht dabei von selbst, weil sie im selben Thread laeuft: Niemand handelt nebenbei die
  API-Version auf demselben `Engine`-Objekt neu aus. Nach jedem Schritt im Journal schreibt der Ablauf den Status
  (`busy` mit `step`, `target` ist dann `null`); das Ergebnis der letzten Vorpruefung gilt ab dann als veraltet. Den
  Status zu `committed` schreibt er erst, wenn das Ergebnis eingetragen ist (vor jedem Aufruf an der Engine), und noch
  einmal, sobald Slot bzw. Sperre gespeichert sind: `busy.step` = `committed` steht nie ohne das Ergebnis im Status.
* **Heartbeat-Thread alle 30 s:** schreibt den Status mit frischem `heartbeat_at` (ohne fsync), auch wenn die
  Schleife gerade an einem langen Engine-Aufruf oder in einem Ablauf haengt -- sonst zeigte das Dashboard "antwortet
  nicht". Der Status am Ende einer langen Runde traegt nicht den Zeitpunkt vom Rundenbeginn (er wuerde den frischeren
  Heartbeat ueberschreiben und `heartbeat_at` rueckwaerts laufen lassen), sondern die Wanduhr im Moment des Schreibens
  -- dieselbe Uhr wie beim Heartbeat, auch wenn sie waehrend der Runde springt (Raspberry Pi ohne Echtzeituhr).
* **Zweite Instanz** (`flock` in `/state` belegt): liest den Kanal nicht, nimmt nichts wieder auf und schreibt keinen
  Status, versucht es alle 30 s erneut.
* **Zustand gerade nicht lesbar** (`state_read`/`journal_read`): diese Runde nichts lesen, nichts loeschen, nichts
  ausfuehren, spaeter erneut -- nie wie "kein Journal" behandeln.

Protokoll: JSON-Zeilen auf stdout mit festen Codes (`event`, `code`, IDs). Nie Env, HostConfig, Kanal-Rohdaten
oder Ausgaben von Healthchecks; Fremdtexte nur bereinigt (`engine.sanitize`). Je Anforderungs-ID eine Zeile `request`
mit ihrem Ergebnis. Der Status enthaelt nur feste Codes, Versionen, Zeiten und IDs (`channel.validate_status`), nie
einen Text der Engine.

`--selftest` prueft ohne Kanal, Zustand und Engine, ob das Paket vollstaendig und lauffaehig ist (Image-Test).
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from . import __version__, clone, flow, policy, target
from .channel import Channel, ChannelUnsafe, Incoming, encode_status
from .engine import Engine, sanitize
from .flow import Flow
from .policy import Refusal
from .state import (
    Journal,
    JournalCorrupt,
    SecondInstance,
    State,
    StateStore,
    StateUnsafe,
)

POLL_INTERVAL_S = 5.0
HEARTBEAT_INTERVAL_S = 30.0
PREFLIGHT_INTERVAL_S = 300.0
PREFLIGHT_RETRY_S = 30.0
"""Solange die Vorpruefung nicht bereit ist (z. B. das Dashboard startet gerade), oefter nachsehen."""
RETRY_SETUP_S = 30.0
SLOT_RETRY_S = 30.0
"""Scheitert das Aufraeumen eines abgelaufenen Slots an der Engine, so lange bis zum naechsten Versuch."""
LOGGED_IDS_MAX = 1024

_UNSAFE_REASONS = frozenset({policy.CHANNEL_UNSAFE, policy.STATE_UNSAFE, policy.UNSAFE_TARGET})
_ERROR_REASONS = frozenset({policy.ENGINE_UNREACHABLE, policy.ENGINE_UNSUPPORTED, policy.API_TOO_OLD,
                            policy.SELF_UNKNOWN, policy.NOT_COMPOSE})

_log_lock = threading.Lock()


def log(event: str, **fields: Any) -> None:
    """Eine JSON-Zeile auf stdout. `fields` sind feste Codes, IDs, Zahlen oder schon bereinigte Texte."""
    line = policy.dumps({"ts": int(time.time()), "event": event, **fields}) + b"\n"
    with _log_lock:
        try:
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
        except (OSError, ValueError):
            pass


class Helper:
    """Der Helfer. Die Bausteine kommen ueber den Konstruktor (Tests geben eigene mit, nie ueber die Umgebung).

    * `clock`, `monotonic`, `sleep`: Wanduhr, monotone Uhr und Warten, auch fuer den Ablauf (die Tests stellen eine
      eigene Uhr). Fristen und Warten im Ablauf laufen auf der monotonen Uhr, alles in `/state` auf der Wanduhr.
    * `uptime`: Laufzeit des Rechners fuer den Ablauf (`flow.boot_uptime`; die Tests stellen eine eigene).
    * `checkpoint`: nur fuer Tests, im Betrieb leer. Geht unveraendert an `Flow`: die Tests lassen den Ablauf damit an
      einer bestimmten Stelle "abstuerzen" und starten den Helfer dann neu.
    """

    def __init__(
        self,
        *,
        channel: Channel,
        store: StateStore,
        engine: Engine,
        service: str,
        own_id: Callable[[], str] = target.own_container_id,
        repository: str = policy.REPOSITORY,
        logger: Callable[..., None] = log,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        uptime: Callable[[], float | None] = flow.boot_uptime,
        checkpoint: Callable[[str, str], None] | None = None,
    ) -> None:
        self._clock = clock
        self._uptime = uptime
        self._monotonic = monotonic
        self._sleep = sleep
        self._checkpoint = checkpoint
        self._channel = channel
        self._store = store
        self._engine = engine
        self._service = policy.service_name(service)
        self._own_id = own_id
        self._repository = repository
        self._log = logger
        self._lock = threading.RLock()
        self._seq = 0
        self._logged: dict[str, None] = {}
        self._channel_ok = False
        self._store_open = False
        self._store_reason: str | None = None
        self._second_instance = False
        self._state: State | None = None
        self._journal: Journal | None = None
        self._journal_known = False
        self._cluttered = False
        self._preflight: target.Preflight | None = None
        self._preflight_at: float | None = None
        self._self_id: str | None = None
        self._flow: Flow | None = None
        self._flow_in_round = False
        """In dieser Runde lief schon ein Ablauf: weitere Anforderungen derselben Runde bekommen `busy`."""
        self._results_dirty = False
        """Ein Ergebnis steht schon in `self._state`, aber noch nicht in `state.json`."""
        self._recover_waits = False
        self._slot_failed_at: float | None = None
        self._last_setup_at: float | None = None
        self._status_failed = False

    # --- Einrichten ---------------------------------------------------------

    def setup(self, now: float) -> None:
        """Zustand oeffnen (Sperre), Zustand und Journal laden, Kanal einrichten. Fehler fuehren nie zum Abbruch,
        sondern in den Status; `tick` versucht es spaeter erneut."""
        self._last_setup_at = now
        if not self._store_open:
            try:
                self._store.open()
            except SecondInstance:
                if not self._second_instance:
                    self._log("second_instance")
                self._second_instance = True
                return
            except StateUnsafe as exc:
                self._set_store_problem(exc)  # den Kanal trotzdem einrichten: der Status meldet `state_unsafe`
            else:
                self._store_open = True
                self._second_instance = False
                self._store_reason = None
        if self._store_open:
            self._load_state(now)
        if not self._channel_ok:
            # Unter der Sperre: `Channel.setup` schliesst und oeffnet den Deskriptor der Wurzel neu; der Heartbeat-Thread
            # darf nicht gleichzeitig ueber eine (womoeglich neu vergebene) Deskriptornummer schreiben.
            with self._lock:
                try:
                    self._channel.setup()
                except ChannelUnsafe as exc:
                    self._log("channel_unsafe", detail=exc.detail)
                else:
                    self._channel_ok = True

    def _set_store_problem(self, exc: Refusal) -> None:
        if self._store_reason != exc.code:
            self._log("state_unsafe", detail=exc.detail)
        self._store_reason = exc.code

    def _load_state(self, now: float) -> None:
        """Zustand und Journal (neu) laden. Gerade nicht lesbar: alter Stand bleibt, spaeter erneut."""
        try:
            state = self._store.load_state(now)
        except StateUnsafe as exc:
            self._set_store_problem(exc)
            if exc.detail != "state_read":
                with self._lock:
                    self._state = None
            return
        with self._lock:
            self._state = state
        if self._store.problem:
            self._log("state_corrupt", detail=self._store.problem)
        if self._read_journal(now):
            self._store_reason = None

    def _read_journal(self, now: float) -> bool:
        """Liest das Journal von der Platte, der einzigen Quelle dafuer. `False`, wenn es gerade nicht lesbar war: Dann
        gilt es als unbekannt, und bis es wieder lesbar ist, wird nichts gelesen und nichts ausgefuehrt.

        Steht es auf `committed` (Absturz gleich nach dem Commit), ist der Vorgang entschieden: Sein Ergebnis kommt in den
        Zustand, bevor der Status das Journal zeigt, und wird gleich gespeichert. Dafuer braucht es weder die eigene ID
        noch die Engine (`flow.committed_result`); so zeigt auch der erste Herzschlag nach dem Neustart `committed` nie
        ohne das Ergebnis, und ebenso nicht, solange die Wiederaufnahme wartet."""
        try:
            journal = self._store.load_journal(now)
        except JournalCorrupt as exc:
            self._journal_corrupt(exc)
            return True
        except StateUnsafe as exc:  # journal_read/journal_write: nie wie "kein Journal" behandeln
            self._set_store_problem(exc)
            self._journal_known = False
            return False
        entry = flow.committed_result(journal, self._clock()) if journal is not None else None
        with self._lock:
            state = self._state
            decided = entry is not None and state is not None and state.record_result(entry)
            self._journal = journal
            self._journal_known = True
        if decided:
            self._results_dirty = True
            self._save_results()
        return True

    def _journal_corrupt(self, exc: JournalCorrupt) -> None:
        """Kaputtes Journal: die Sperre ist gesetzt, das Journal liegt beiseite. Kein automatischer Schritt."""
        self._log("journal_corrupt")
        with self._lock:
            self._state = exc.state or self._state
            self._journal = None
            self._journal_known = True

    def _ensure_flow(self) -> Flow | None:
        """Der Ablauf, sobald die eigene ID bekannt ist (`Flow` braucht sie: der Helfer ist nie Teil eines Vorgangs)."""
        if self._flow is not None:
            return self._flow
        if self._self_id is None:
            try:
                self._self_id = self._own_id()
            except Refusal:
                return None
        try:
            self._flow = Flow(engine=self._engine, store=self._store, own_id=self._self_id, service=self._service,
                              repository=self._repository, clock=self._clock, monotonic=self._monotonic,
                              sleep=self._sleep, uptime=self._uptime, on_step=self._on_step, logger=self._log,
                              checkpoint=self._checkpoint)
        except ValueError:
            self._self_id = None
            return None
        return self._flow

    def _on_step(self, journal: Journal | None) -> None:
        """Vom Ablauf nach jedem geschriebenen Schritt (und mit `None` nach dem Loeschen des Journals): `busy.step` sofort
        im Status zeigen. Ab der ersten Aenderung ist das Ergebnis der letzten Vorpruefung veraltet."""
        with self._lock:
            self._journal = journal
            self._journal_known = True
            self._preflight = None
            self._preflight_at = None
        self.write_status(self._clock(), durable=False)

    # --- Schleife -----------------------------------------------------------

    def tick(self, now: float) -> None:
        """Eine Runde: ggf. Einrichtung wiederholen, einen liegen gebliebenen Vorgang wieder aufnehmen, Vorpruefung
        falls faellig, Kanal lesen und Anforderungen ausfuehren, Status schreiben.

        `now` ist der Zeitpunkt vom Rundenbeginn (er steuert, was faellig ist). Die Runde kann lange an der Engine oder
        in einem Ablauf haengen, waehrenddessen schreibt der Heartbeat-Thread frische Zeitstempel; der Status am Ende
        bekommt darum die Wanduhr im Moment des Schreibens (`clock`, wie der Heartbeat) und nie den alten Wert -- auch
        dann nicht, wenn die Uhr waehrend der Runde springt."""
        incomplete = self._second_instance or not self._store_open or not self._channel_ok or self._store_reason
        wait = RETRY_SETUP_S if self._second_instance else POLL_INTERVAL_S
        if incomplete and (self._last_setup_at is None or not 0 <= now - self._last_setup_at < wait):
            self.setup(now)
        if self._second_instance:
            return
        if self._store_open and self._store_reason is None and (self._state is None or not self._journal_known):
            self._load_state(now)
        recovered = self._recover() if self._state_ready() and self._journal is not None else False
        if self._journal is None:
            if self._preflight_due(now):
                self.run_preflight(now)
            self._tidy_slot(now)
        handled = self._poll(now) if self._can_read_requests() else False
        if self._results_dirty:
            self._save_results()
        self.write_status(self._clock(), durable=handled or recovered)

    def _state_ready(self) -> bool:
        """Zustand geladen und Journal gelesen (ob eins da ist oder nicht)."""
        return (self._store_open and self._store_reason is None and self._state is not None
                and self._journal_known)

    def _can_read_requests(self) -> bool:
        return self._channel_ok and self._state_ready()

    def _preflight_due(self, now: float) -> bool:
        if self._preflight is None or self._preflight_at is None:
            return True
        interval = PREFLIGHT_INTERVAL_S if self._preflight.ready else PREFLIGHT_RETRY_S
        return now - self._preflight_at >= interval or now < self._preflight_at

    def run_preflight(self, now: float) -> target.Preflight:
        if self._self_id is None:
            try:
                self._self_id = self._own_id()
            except Refusal:
                self._self_id = None
        exclude = self._journal_ids()
        result = target.preflight(self._engine, self_id=self._self_id, service=self._service, exclude=exclude,
                                  repository=self._repository)
        with self._lock:
            previous = self._preflight
            self._preflight = result
            self._preflight_at = now
        if previous is None or (previous.ready, previous.reason) != (result.ready, result.reason):
            self._log("preflight", ready=result.ready, code=result.reason, detail=result.detail,
                      api=None if self._engine.api_version is None else "{}.{}".format(*self._engine.api_version))
        return result

    def _journal_ids(self) -> tuple[str, ...]:
        journal = self._journal
        if journal is None:
            return ()
        ids = [journal.old.id]
        if journal.new is not None and journal.new.id is not None:
            ids.append(journal.new.id)
        return tuple(ids)

    # --- Wiederaufnahme und Aufraeumen ----------------------------------------

    def _recover(self) -> bool:
        """Nimmt den Vorgang im Journal wieder auf (`Flow.recover`): ohne Grenze und ohne die Aktion noch einmal zu
        zaehlen, entschieden nur aus dem Journal und der Engine. `True`, wenn danach kein Journal mehr steht. Bleibt es
        stehen (Engine gerade nicht erreichbar, eigene ID unbekannt), versucht die naechste Runde es erneut."""
        state = self._state
        current = self._ensure_flow()
        if current is None or state is None:
            if not self._recover_waits:
                self._log("recover_waiting", code=policy.SELF_UNKNOWN)
            self._recover_waits = True
            return False
        self._recover_waits = False
        try:
            outcome = current.recover(state)
        except JournalCorrupt as exc:
            self._journal_corrupt(exc)
            return True
        except StateUnsafe as exc:
            self._set_store_problem(exc)
            self._journal_known = False
            return False
        except Exception as exc:  # noqa: BLE001 - das Journal bleibt; Anforderungen bekommen derweil `busy`
            self._log("internal_error", kind=sanitize(type(exc).__name__, 60))
            outcome = None
        if self._read_journal(self._clock()) and outcome is not None and outcome.outcome is not None:
            self._log_request(outcome.request_id, outcome.action, outcome.outcome, outcome.code)
        return self._journal_known and self._journal is None

    def _tidy_slot(self, now: float) -> None:
        """Ohne laufenden Vorgang: einen abgelaufenen Rueckweg-Slot samt Schutz-Tag aufraeumen. Scheitert die Engine
        voruebergehend, bleibt er stehen, und es geht nach `SLOT_RETRY_S` erneut (eine endgueltige Ablehnung beendet
        das, siehe `Flow.drop_expired_slot`)."""
        state = self._state
        slot = state.slot if state is not None else None
        if state is None or slot is None or not self._state_ready() or self._journal is not None \
                or int(now) < slot.until:
            return
        if self._slot_failed_at is not None and 0 <= now - self._slot_failed_at < SLOT_RETRY_S:
            return
        current = self._ensure_flow()
        if current is None:
            return
        try:
            removed = current.drop_expired_slot(state)
        except Exception as exc:  # noqa: BLE001 - Aufraeumen darf die Anforderungen dieser Runde nie aufhalten
            self._log("internal_error", kind=sanitize(type(exc).__name__, 60))
            removed = False
        self._slot_failed_at = None if removed or state.slot is None else now

    # --- Anforderungen ------------------------------------------------------

    def _poll(self, now: float) -> bool:
        try:
            result = self._channel.poll(now=now)
        except ChannelUnsafe as exc:
            self._log("channel_unsafe", detail=exc.detail)
            self._channel_ok = False
            return False
        self._cluttered = result.cluttered
        self._flow_in_round = False
        for item in result.items:
            # Die Anforderungen sind schon aus dem Kanal genommen: ein Fehler an einer darf die uebrigen dieser Runde
            # nicht mitreissen (jede Ausnahme gilt nur fuer diese eine).
            try:
                self._handle(item, now)
            except Exception as exc:  # noqa: BLE001
                self._log("internal_error", id=item.id, kind=sanitize(type(exc).__name__, 60))
        return bool(result.items)

    def _handle(self, item: Incoming, now: float) -> None:
        """Prueft eine Anforderung vollstaendig und fuehrt sie aus (Reihenfolge im Kopf des Moduls)."""
        if item.raw is None:
            self._refuse(item.id, _guess_action(None), None, None, item.code or policy.BAD_REQUEST)
            return
        try:
            request = policy.parse_request(item.raw, now=now, file_id=item.id)
        except Refusal as exc:
            self._refuse(item.id, _guess_action(item.raw), None, None, exc.code)
            return
        to_version = request.version
        last = self._preflight
        known_version = last.current_version if last is not None else None
        state = self._state
        if state is None:
            self._refuse(request.id, request.action, known_version, to_version, policy.STATE_UNSAFE)
            return
        try:
            self._store.check(state, request, now)
        except Refusal as exc:
            self._refuse(request.id, request.action, known_version, to_version, exc.code)
            self._remember(state, request, now)
            return
        if not self._remember(state, request, now):
            self._refuse(request.id, request.action, known_version, to_version, policy.STATE_UNSAFE)
            return
        if self._journal is not None or self._flow_in_round:
            self._refuse(request.id, request.action, known_version, to_version, policy.BUSY)
            return
        # Frische Vorpruefung vor jeder Anforderung, aber hoechstens eine je Runde (sonst kostete eine Flut von
        # Anforderungen je Runde bis zu 32 Vorpruefungen an der Engine).
        check = self._preflight
        if check is None or self._preflight_at != now:
            check = self.run_preflight(now)
        from_version = check.current_version
        if not check.ready or check.target is None:
            self._refuse(request.id, request.action, from_version, to_version, check.reason or policy.NO_TARGET)
            return
        try:
            if request.action == "update":
                policy.check_tag_fits(check.target.floating_tag, request.version)
                policy.check_newer(request.version, check.target.version)
            else:
                policy.check_rollback(request, state.slot, target_container_id=check.target.id,
                                      target_image_id=check.target.image_id, now=now)
        except Refusal as exc:
            self._refuse(request.id, request.action, from_version, to_version, exc.code)
            return
        current = self._ensure_flow()
        if current is None:  # die Vorpruefung war bereit, kannte die eigene ID also; nur zur Sicherheit
            self._refuse(request.id, request.action, from_version, to_version, policy.SELF_UNKNOWN)
            return
        self._flow_in_round = True
        self._execute(current, request, state)

    def _execute(self, current: Flow, request: policy.Request, state: State) -> None:
        """Fuehrt die Anforderung aus. Das Ergebnis haelt der Ablauf selbst in `state.json` fest. Steht danach noch ein
        Journal (Aufraeumen offen, Commit nicht schreibbar), bleibt der Helfer beschaeftigt, und die naechste Runde nimmt
        den Vorgang wieder auf."""
        try:
            outcome = current.run(request, state)
        finally:
            self._read_journal(self._clock())
        self._log_request(request.id, request.action, outcome.outcome, outcome.code)
        if self._journal_known and self._journal is None:
            self.run_preflight(self._clock())  # der Status zeigt gleich die neue Lage, nicht die von vorher

    def _remember(self, state: State, request: policy.Request, now: float) -> bool:
        """Merkt die ID (Replay-Schutz, auch fuer abgelehnte Anforderungen) und speichert den Zustand, mit den
        Ergebnissen, die bis hierhin dazugekommen sind."""
        try:
            state.mark_seen(request.id, now)
            self._store.save_state(state)
        except (OSError, ValueError):
            self._log("state_write_failed")
            return False
        self._results_dirty = False
        return True

    def _refuse(self, request_id: str, action: str | None, from_version: str | None, to_version: str | None,
                code: str) -> None:
        """Eine Ablehnung vor dem Ablauf: eine Logzeile je ID und ein Ergebnis im Zustand (gespeichert mit dem naechsten
        `save_state`) -- ausser fuer diese ID steht schon eins da: eine Wiederholung ueberschreibt nie das erste."""
        self._log_request(request_id, action, "refused", code)
        state = self._state
        if action is None or state is None:
            return  # ohne gueltige Aktion gibt es keinen Eintrag im Status (das Schema verlangt eine)
        if any(entry["id"] == request_id for entry in state.results):
            return
        entry = {"id": request_id, "action": action, "from": from_version, "to": to_version, "outcome": "refused",
                 "code": code, "finished_at": max(0, int(self._clock()))}
        with self._lock:
            state.record_result(entry)
        self._results_dirty = True

    def _save_results(self) -> None:
        state = self._state
        if state is None:
            return
        try:
            self._store.save_state(state)
        except (OSError, ValueError):
            self._log("state_write_failed")
            return
        self._results_dirty = False

    def _log_request(self, request_id: str, action: str | None, outcome: str | None, code: str | None) -> None:
        """Die eine Logzeile je Anforderungs-ID (`outcome` ist `None`, solange der Vorgang noch nicht entschieden ist)."""
        if request_id in self._logged:
            return
        self._logged[request_id] = None
        while len(self._logged) > LOGGED_IDS_MAX:
            self._logged.pop(next(iter(self._logged)))
        self._log("request", id=request_id, action=action, outcome=outcome, code=code)

    # --- Status -------------------------------------------------------------

    def status(self, now: float) -> dict[str, Any]:
        """Der Status (nur feste Codes). `results` kommt aus dem Zustand, `target` gibt es nur ohne laufenden Vorgang."""
        with self._lock:
            state_name, ready, reason = self._summary(now)
            check = self._preflight
            journal = self._journal
            state = self._state
            slot = state.slot if state is not None else None
            busy = None
            if journal is not None:
                busy = {"id": journal.request_id, "action": journal.action, "step": journal.step,
                        "since": journal.started_at}
            previous = None
            if slot is not None and slot.until > int(now):
                previous = {"version": slot.from_version, "until": slot.until}
            return {
                "proto": policy.PROTOCOL,
                "helper_version": __version__,
                "request_versions": list(policy.REQUEST_VERSIONS),
                "seq": self._seq,
                "heartbeat_at": max(0, int(now)),
                "state": state_name,
                "ready": ready,
                "reason": reason,
                "target": check.status_target() if check is not None and journal is None else None,
                "busy": busy,
                "previous": previous,
                "results": [dict(entry) for entry in state.results] if state is not None else [],
            }

    def _summary(self, now: float) -> tuple[str, bool, str | None]:
        if not self._channel_ok:
            return "unsafe", False, policy.CHANNEL_UNSAFE
        if self._store_reason is not None or not self._store_open:
            return "unsafe", False, policy.STATE_UNSAFE
        state = self._state
        if state is None or not self._journal_known:
            return "error", False, policy.STATE_UNSAFE
        if state.hold_until is not None and state.hold_until > int(now):
            return "unsafe", False, policy.STATE_UNSAFE
        if self._journal is not None:
            return "busy", False, policy.BUSY
        check = self._preflight
        if check is None:
            return "idle", False, None
        if not check.ready:
            reason = check.reason
            if reason in _UNSAFE_REASONS:
                return "unsafe", False, reason
            if reason in _ERROR_REASONS:
                return "error", False, reason
            return "idle", False, reason
        return "idle", True, policy.CHANNEL_CLUTTERED if self._cluttered else None

    def write_status(self, now: float, *, durable: bool = True) -> bool:
        """Schreibt den Status (atomar). Gibt `False` zurueck, wenn das nicht ging (wird nur einmal je Serie
        geloggt). Eine zweite Instanz schreibt nie."""
        if self._second_instance:
            return False
        with self._lock:
            self._seq += 1
            doc = self.status(now)
            try:
                self._channel.write_status(doc, durable=durable)
            except (OSError, ValueError, Refusal):
                if not self._status_failed:
                    self._log("status_write_failed")
                self._status_failed = True
                return False
        self._status_failed = False
        return True

    def heartbeat(self, now: float) -> None:
        self.write_status(now, durable=False)

    # --- Lauf ---------------------------------------------------------------

    def run(self, stop: threading.Event) -> None:
        """Bis `stop` gesetzt ist: Einrichtung, dann alle 5 s `tick`; daneben der Heartbeat-Thread. Ein laufender
        Vorgang wird dafuer nicht abgebrochen; endet der Prozess mittendrin, nimmt der naechste Start ihn wieder auf."""
        self._log("start", version=__version__, service=self._service)
        self._guarded(self.setup)
        # Der Heartbeat startet vor der ersten Runde: haengt schon die erste Vorpruefung (oder die Wiederaufnahme) an der
        # Engine, zeigt der Status trotzdem "lebt" (bzw. "beschaeftigt").
        beat = threading.Thread(target=self._beat, args=(stop,), name="heartbeat", daemon=True)
        beat.start()
        self._guarded(self.heartbeat)
        self._guarded(self.tick)
        while not stop.wait(POLL_INTERVAL_S):
            self._guarded(self.tick)
        self._log("stop")

    def _beat(self, stop: threading.Event) -> None:
        while not stop.wait(HEARTBEAT_INTERVAL_S):
            self._guarded(self.heartbeat)

    def _guarded(self, step: Callable[[float], Any]) -> None:
        try:
            step(self._clock())
        except Exception as exc:  # noqa: BLE001 - eine Runde darf den Helfer nie beenden
            self._log("internal_error", kind=sanitize(type(exc).__name__, 60))


def _guess_action(raw: bytes | None) -> str | None:
    """Fuer den Status-Eintrag einer ungueltigen Anforderung: die Aktion, wenn sie sich lesen laesst."""
    if raw is None:
        return None
    try:
        obj = policy.loads_strict(raw, max_bytes=policy.REQUEST_MAX_BYTES)
    except ValueError:
        return None
    action = obj.get("action") if isinstance(obj, dict) else None
    return action if isinstance(action, str) and action in policy.ACTIONS else None


# ---------------------------------------------------------------------------
# Selbsttest und Einstieg
# ---------------------------------------------------------------------------


def selftest() -> int:
    """Ohne Kanal, Zustand und Engine: Paket vollstaendig, reine Funktionen arbeiten wie erwartet."""
    checks: list[tuple[str, Callable[[], bool]]] = [
        ("python", lambda: sys.version_info >= (3, 11)),
        ("repository", lambda: policy.floating_tag(policy.REPOSITORY) == "latest"),
        ("version", lambda: policy.is_version(__version__) and not policy.is_version("0.7.١")),
        ("request", _selftest_request),
        ("status", _selftest_status),
        ("clone", _selftest_clone),
        ("flow", _selftest_flow),
        ("mountinfo", _selftest_mountinfo),
        ("environment", lambda: policy.service_name(os.environ.get(policy.SERVICE_ENV)) != ""),
    ]
    for name, check in checks:
        try:
            ok = check()
        except Exception:  # noqa: BLE001 - jeder Fehler ist ein fehlgeschlagener Selbsttest
            ok = False
        if not ok:
            log("selftest", ok=False, check=name)
            return 1
    log("selftest", ok=True, version=__version__)
    return 0


def _selftest_request() -> bool:
    raw = policy.dumps({"v": 1, "id": "6f1c2b0e-9a4d-4c1e-8f00-1b2c3d4e5f60", "action": "update",
                        "version": "0.7.1", "created_at": 1790812345})
    ok = policy.parse_request(raw, now=1790812400).version == "0.7.1"
    try:
        policy.parse_request(b'{"v": 1, "v": 1}', now=1790812400)
    except Refusal as exc:
        return ok and exc.code == policy.BAD_REQUEST
    return False


def _selftest_status() -> bool:
    doc = {"proto": policy.PROTOCOL, "helper_version": __version__, "request_versions": list(policy.REQUEST_VERSIONS),
           "seq": 0, "heartbeat_at": 0, "state": "idle", "ready": False, "reason": None, "target": None,
           "busy": None, "previous": None, "results": []}
    return len(encode_status(doc)) < policy.STATUS_MAX_BYTES


def _selftest_clone() -> bool:
    image_id = "sha256:" + "a" * 64
    container = {
        "Id": "b" * 64, "Name": "/deck", "Image": image_id,
        "Config": {"Image": policy.REPOSITORY, "Entrypoint": ["/entrypoint.sh"], "Cmd": ["serve"],
                   "Env": ["A=1", "B=2"], "Hostname": "b" * 12, "Labels": {"x": "1"}},
        "HostConfig": {"NetworkMode": "none", "Binds": ["data:/app/data"]},
        "Mounts": [{"Type": "volume", "Name": "anon", "Destination": "/anon", "RW": True}],
    }
    image = {"Config": {"Entrypoint": ["/entrypoint.sh"], "Cmd": ["serve"], "Env": ["A=1"], "Labels": {"x": "1"}}}
    plan = clone.build(container, image, new_image_id=image_id, api_version=(1, 41), network_drivers={})
    body = plan.body
    return ("Entrypoint" not in body and body.get("Env") == ["B=2"] and "Labels" not in body
            and body["HostConfig"]["Mounts"][0]["Source"] == "anon" and plan.connects == ())


def _selftest_flow() -> bool:
    """Das Urteil beim Beobachten (gesund; `unhealthy` allein ist kein Fehler; zweimal 503 ist die Notseite) und ein
    Ergebnis, das in den Status passt."""
    def inspect(status: str, outputs: list[str]) -> dict[str, Any]:
        return {"State": {"Running": True, "Restarting": False, "Health": {
            "Status": status, "Log": [{"Output": text} for text in outputs]}}, "RestartCount": 0}

    rescue = "HTTPError: " + flow.RESCUE_MARKER
    outcome = flow.Outcome(request_id="6f1c2b0e-9a4d-4c1e-8f00-1b2c3d4e5f60", action="update", outcome="rolled_back",
                           code=policy.EXITED, from_version="0.7.0", to_version="0.7.1")
    return (flow.judge(inspect("healthy", [""]), 0) == flow.HEALTHY
            and flow.judge(inspect("unhealthy", ["refused"]), 0) is None
            and flow.judge(inspect("unhealthy", [rescue, rescue]), 0) == policy.RESCUE_PAGE
            and policy.is_result(outcome.result(1790812400)))


def _selftest_mountinfo() -> bool:
    line = (f"1 0 0:1 /var/lib/docker/containers/{'c' * 64}/hostname /etc/hostname rw - ext4 /dev/x rw\n")
    return target.parse_mountinfo(line.encode("ascii")) == "c" * 64


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--selftest"]:
        return selftest()
    if args:
        log("usage", code="bad_arguments")
        return 2
    try:
        service = policy.service_name(os.environ.get(policy.SERVICE_ENV))
    except ValueError:
        log("config_invalid", code="bad_service")
        return 2
    helper = Helper(channel=Channel(), store=StateStore(), engine=Engine(), service=service)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    helper.run(stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
