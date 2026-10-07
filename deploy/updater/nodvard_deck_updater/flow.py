"""Der Ablauf: Update, Rueckweg und die Wiederaufnahme nach einem Absturz. Nur hier veraendert der Helfer etwas an
der Engine.

Ein Vorgang laeuft in festen Schritten (`policy.STEPS`). **Jeder Schritt steht vorher im Journal**
(`/state/journal.json`, atomar mit fsync, `StateStore.write_journal`); erst danach holt der Ablauf eine neue Bindung
(`Engine.bind`) und macht den Aufruf. Die Bindung laesst nur die Aufrufe des Schritts durch, der im Journal steht --
und die Fake-Engine der Tests prueft dasselbe gegen die Datei auf der Platte.

**Update auf Version X** (`Flow.run` mit der Aktion `update`):

* *Vorab, ohne Aenderung:* Vorpruefung erneut (`target.preflight`: genau ein gesundes Ziel, Image des Repositorys mit
  beweglichem Tag und einem Digest dort, klonbar), das bewegliche Tag passt zu X (`check_tag_fits`), X ist neuer als die
  laufende Version (`check_newer`).
* `begin`: Journal anlegen, mit dem Digest des alten Images im Repository aus der Vorpruefung (fuer den Slot beim
  Commit; spaeter nennt die Engine ihn womoeglich nicht mehr). Beim containerd-Store kann das auch ein Digest sein, den
  nur dieser Rechner kennt (selbst gebautes Image, siehe `policy.require_registry_digest`); das Update selbst haengt
  nicht davon ab. Das bewegliche Tag in der Registry aufloesen (`/distribution`, ein Digest D), nach D ziehen, das
  Image per `<repository>@D` lesen: `RepoDigests` enthaelt D, das Versions-Label ist **genau** X (sonst
  `tag_not_on_version`), X ist neuer, Betriebssystem und Architektur wie beim laufenden Image (`platform_mismatch`).
  Scheitert das, ist nichts veraendert ausser einem gezogenen Image: Ergebnis `refused` (zaehlt nicht fuer die Grenze).
* `pulled`: das neue Image steht im Journal. Den `create`-Body bauen (`clone.build`). Ab hier zaehlt die Aktion fuer die
  Grenze von einer Aktion je 10 Minuten (`State.record_action`, gespeichert, bevor sich etwas aendert).
* `protected`: das alte Image bekommt das Schutz-Tag `nodvard-deck-previous:<alte Version>`.
* `renamed`: der alte Container (laeuft weiter) heisst jetzt `<name>-previous`.
* `tagged`: das bewegliche Tag zeigt auf das neue Image.
* `creating`: neuen Container unter `<name>` anlegen (Tag-Text unveraendert). Gleich danach `created` mit der neuen ID,
  dann die weiteren Netze verbinden (`connect`) und die Nachkontrolle (`clone.verify`, sonst `clone_mismatch`; dazu
  gehoert `.Image` gleich dem neuen Image, falls jemand das Tag dazwischen umgehaengt hat).
* `old_stopped`: alte Restart-Policy auf `no` (nach einem Neustart des Docker-Dienstes laeuft er nicht wieder an),
  alten stoppen (`t` mindestens 30 s), pruefen, dass er steht (`stop_failed`).
* `started`: neue Frist (15 min) im Journal, neuen Container starten (`start_failed`).
* *Beobachten* alle 5 s bis zum ersten `healthy` (laeuft, startet nicht neu, `RestartCount` unveraendert). Harte Fehler
  vor der Frist: `exited`, `restart_loop`, `rescue_page` (die letzten zwei Eintraege in `Health.Log` zeigen die
  Notseite mit 503). `unhealthy` allein ist **kein** Fehler: eine lange Migration darf lange brauchen. Nach der Frist:
  `timeout`.
* `committed` beim ersten `healthy`: Ab hier ist der Vorgang entschieden (Ergebnis `applied`, danach gibt es keinen
  automatischen Rueckweg mehr). Das Ergebnis kommt sofort in `state.json` und in den Status, noch vor jedem Aufruf an
  der Engine; der Status zu `committed` wird erst mit ihm geschrieben (`Flow._decide`). Solange der Status `busy.step`
  = `committed` zeigt, steht das Ergebnis dieser Anforderung also schon in `results` (nach einem Absturz ebenso: die
  Wiederaufnahme traegt es ein, bevor sie die Engine fragt). Dann das Schutz-Tag eines ersetzten Slots entfernen (solange dieser noch in `state.json` steht, nur dann
  laesst die Bindung es zu), den neuen Slot (Rueckweg 7 Tage) speichern und den Status gleich danach schreiben (bis
  dahin zeigt `previous` noch den ersetzten Slot), dann den alten Container entfernen (`v=0`, ohne `force`), Journal
  loeschen. Scheitert ein Teil des Aufraeumens an der Engine, bleibt das Journal stehen, und die naechste Runde macht
  weiter; nach `CLEANUP_ROUNDS` Runden bleibt der Teil liegen.

**Rueckweg auf Wunsch** (Aktion `rollback`): nur mit gueltigem Slot (`policy.check_rollback`: genau der installierte
Container, genau die Version davor, innerhalb der Frist). In `begin` wird das Image des Slots gelesen; fehlt es (nach
`image prune -a`), wird es nach dem Digest des Slots gezogen und muss genau die ID und die Version des Slots haben
(`previous_mismatch`). Kennt die Registry den Digest nicht (beim containerd-Store ein selbst gebautes Image), scheitert
das Ziehen: `pull_failed`, nichts veraendert, der Slot bleibt. Danach dieselben Schritte wie beim Update, Vorlage ist
der **aktuelle** Container. Beim Commit: das Ergebnis `reverted` wie beim Update sofort speichern und in den Status,
dann den Slot loeschen, die verlassene Version 24 h fuer Updates sperren und das speichern (bis dahin zeigt `previous`
noch den gerade benutzten Slot), dann den neueren Container und beide Schutz-Tags entfernen (die Bindung nimmt deren
Versionen aus dem Journal).

**Rueckbau vor dem Commit** (`Flow._undo`, mit `Engine.bind(..., undo=True)`): Zuerst kuendigt er sich im Journal an
(`Journal.start_undo`, mit dem Grund als `code`, write-ahead wie jeder Schritt). Ab dann laesst die Bindung nichts mehr
vorwaerts zu, und eine Wiederaufnahme erkennt einen halb zurueckgebauten Stand als den eigenen. Dann in umgekehrter
Reihenfolge nur, was laut Journal schon geschehen sein kann -- neuen Container stoppen (immer, auch wenn er gerade nicht
laeuft: ein Start, dessen Antwort ausblieb, kann noch nachkommen) und entfernen, bewegliches Tag zurueck, alten zurueck
umbenennen, seine Restart-Policy wiederherstellen, ihn starten und bis zu 15 min auf `healthy` warten (laeuft er noch,
obwohl ein Stopp hinausging, erst dessen Frist abwarten: der Docker-Dienst stoppt nach einer Fehlerantwort weiter),
zuletzt das Schutz-Tag entfernen (geht das nicht, bleibt es liegen: es haelt nur ein Image fest).
Jeder Teil ist wiederholbar (was schon erledigt ist, wird uebersprungen), damit eine Wiederaufnahme nach einem Absturz
denselben Weg gehen kann. Steht das Journal noch auf `creating` (etwa weil `create` am Zeitlimit scheiterte, der
Container aber angelegt wurde), sucht der Rueckbau den eigenen Container unter `<name>` (Zustand `created`, nie
gestartet, neues Image, gleiche Compose-Labels), traegt ihn erst als `created` ins Journal ein und entfernt ihn dann.
Ergebnis `aborted`, solange der neue Container nie gestartet wurde, sonst `rolled_back`; wird der alte Container nicht
wieder gesund, `failed_manual` (Code `rollback_failed`): kein weiterer Versuch, keine Schleife, das Schutz-Tag bleibt.
Ebenso bei einem dauerhaften Fehler der Engine (4xx, oder 5xx bis zur Frist). War die Engine dagegen nur nicht
erreichbar (Transportfehler bis zur Frist, etwa ein haengender Docker-Dienst) oder liess sich das Journal gerade nicht
schreiben, gibt der Rueckbau nicht endgueltig auf: Das Journal bleibt mit dem angekuendigten Rueckbau stehen
(`outcome=None`, `finished=False`), und die Wiederaufnahme baut zurueck, sobald es wieder geht -- sonst bliebe der alte
Container gestoppt und ohne Restart-Policy liegen, und niemand raeumte mehr auf.

**Von aussen geaendert:** Vor jedem aendernden Schritt und bei jedem Blick waehrend des Beobachtens liest der Ablauf die
Container erneut: Es darf kein weiterer Container mit den Ziel-Labels dazugekommen sein, und alter bzw. neuer Container
muessen mit ID, Image und Namen zum Journal passen. Sonst Ergebnis `external_change`: das Journal wird geloescht und
nichts mehr angefasst (nie gegen den Nutzer).

**Wiederaufnahme nach einem Absturz** (`Flow.recover`, beim Start des Helfers und bevor er Anforderungen liest): Der
Schritt im Journal ist angekuendigt, aber vielleicht noch nicht ausgefuehrt. Entschieden wird nur aus dem Journal und
der Engine -- nie aus den Grenzen in `state.json` (die Wiederherstellung wird nie gedrosselt, und die Aktion zaehlt kein
zweites Mal) und nie aus der Vorpruefung: Mit Journal meldet `target.preflight` nie ein Ziel, und in `creating` haelt
sie den eigenen, schon angelegten Container fuer fremd. Darum liest die Wiederaufnahme die Container mit den
Ziel-Labels selbst und gleicht sie mit den IDs im Journal ab.

* `begin`, `pulled`: nichts veraendert (hoechstens ein Image gezogen) -> Journal loeschen, `aborted`.
* `protected` bis `old_stopped`: pruefen, dann Rueckbau -> `aborted`. In `creating` sucht sie vorher den eigenen, schon
  angelegten Container und traegt ihn ein (wie der Rueckbau, siehe oben).
* `started`: Laeuft der neue Container und ist gesund -> Commit. Laeuft er und die Frist im Journal laeuft noch ->
  weiter beobachten, bis zu genau dieser Frist (ein Neustart nur des Helfers verlaengert sie nicht). Hat der
  Docker-Dienst ihn inzwischen neu gestartet (Stromausfall, Neustart des Rechners), beginnt eine Migration von vorn: dann
  eine ganze Frist ab diesem Neustart (`State.StartedAt`), nie mehr als eine ganze Frist ab jetzt; hat er ihn zusammen
  mit dem Helfer gestartet (beide Starts hoechstens `TOGETHER_S` auseinander), eine ganze Frist ab jetzt, denn die Uhr
  kann nach dem Hochfahren falsch gegangen sein; laeuft der Rechner selbst noch keine ganze Frist, mindestens den Rest
  einer Frist ab dem Hochfahren (seine Laufzeit haengt nicht an der Wanduhr; `Flow._resume_left`). Wurde er nie gestartet -> Rueckbau mit
  `start_failed`. Sonst Rueckbau mit dem Grund (`exited`, `restart_loop`, `rescue_page`, `timeout`) -> `rolled_back`.
* Rueckbau angekuendigt (`undo`): weiter zurueckbauen, mit dem Grund aus dem Journal.
* `committed`: zuerst das Ergebnis eintragen und speichern, ohne die Engine (alles steht im Journal; eine Engine, die
  nicht antwortet, haelt es so nicht auf). Dann das Aufraeumen fertig machen (in derselben Reihenfolge wie nach dem
  Commit: Schutz-Tag des ersetzten Slots, Slot bzw. Sperre und Ergebnis speichern, alten Container und beim Rueckweg
  die Schutz-Tags entfernen, Journal loeschen); was schon erledigt ist, wird uebersprungen (ein schon gespeichertes
  Ergebnis behaelt seinen Zeitpunkt) -> `applied` bzw. `reverted`.

Vorher prueft sie wie vor jedem Schritt (kein weiterer Container mit den Ziel-Labels, alter und neuer Container passen
zum Journal); nur im angekuendigten Schritt selbst gilt der Stand davor wie der danach (der alte Container schon
umbenannt oder noch nicht, schon gestoppt oder noch nicht). Passt etwas nicht: `external_change`, nichts anfassen. Ist
die Engine gerade nicht erreichbar, bleibt alles stehen (`Outcome` mit `outcome=None`, `finished=False`); der Aufrufer
versucht es spaeter erneut, ohne Grenze.

**Engine-Fehler:** Waehrend eines Vorgangs handelt der Ablauf die API-Version nicht nebenbei neu aus (die regelmaessige
Vorpruefung laeuft in derselben Schleife und ruht so lange). Beobachten, Rueckbau und Aufraeumen fangen `EngineError`
(auch `not_negotiated`) ab, warten 5 s, handeln selbst neu aus und versuchen es erneut, bis zur jeweiligen Frist. Ein
Fehler in einem Schritt vorwaerts fuehrt zum Rueckbau.

**Was der Ablauf nicht tut:** die Grenzen aus `/state` pruefen und die ID merken (das macht der Aufrufer vorher mit
`StateStore.check`), und `/app/data` beruehren (die Daten regelt das Dashboard beim Start selbst).

**Zwei Uhren:** Was in `/state` steht (Frist im Journal, Slot, Sperren, Ergebnisse), rechnet mit der Wanduhr (`clock`);
gewartet wird im laufenden Prozess nur mit der monotonen Uhr (`monotonic`). Ein Uhrsprung (NTP stellt die Uhr eines
Raspberry Pi ohne Echtzeituhr kurz nach dem Hochfahren um Stunden) bricht so kein gesundes Update ab und verlaengert
kein Warten. Bei der Wiederaufnahme wird die Restzeit einmal aus der Frist im Journal (bzw. aus `State.StartedAt` eines
vom Docker-Dienst neu gestarteten Containers, dazu aus der Laufzeit des Rechners, `boot_uptime`) berechnet und auf
hoechstens `policy.DEADLINE_S` begrenzt (die Uhr kann seit dem Schreiben zurueckgestellt worden sein).

Uhren und Warten kommen ueber den Konstruktor (`clock`, `monotonic`, `sleep`, `uptime`): die Tests stellen eine eigene
Uhr. Ebenso `checkpoint`: er wird vor und nach jedem Schreiben in `/state` aufgerufen, im Betrieb ist er leer; die Tests
lassen den Ablauf damit an genau dieser Stelle "abstuerzen" und nehmen ihn dann wieder auf.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from . import clone, policy, target
from .engine import (
    API_MIN,
    MAX_STOP_T,
    PREVIOUS_REPOSITORY,
    STOP_GRACE_S,
    Binding,
    Engine,
    EngineError,
)
from .policy import Refusal, Request, Slot
from .state import PREVIOUS_SUFFIX, Journal, NewImage, OldContainer, State, StateStore
from .target import NEVER_STARTED

POLL_INTERVAL_S = 5.0
"""So oft sieht der Ablauf nach: beim Beobachten, beim Warten auf den alten Container und vor jedem neuen Versuch."""
CLEANUP_RETRY_S = 60.0
"""So lange versucht der Ablauf nach dem Commit je Runde, den alten Container und ein altes Schutz-Tag zu entfernen.
Gelingt es nicht, bleibt das Journal auf `committed` stehen (die Wiederaufnahme raeumt in der naechsten Runde weiter
auf)."""
CLEANUP_ROUNDS = 5
"""So viele Runden lang versucht der Ablauf einen Teil des Aufraeumens nach dem Commit, den die Engine mit einem Fehler
beantwortet (5xx, unverstaendliche Antwort; etwa "device or resource busy" beim Entfernen). Danach bleibt dieser Teil
liegen wie bei einer endgueltigen Ablehnung (4xx), sonst bliebe der Helfer fuer immer beschaeftigt. Ist die Engine nur
nicht erreichbar, zaehlt die Runde nicht: dann kann ohnehin niemand etwas aendern. Gezaehlt wird im laufenden
Prozess.

Fuer das Speichern von Slot bzw. Sperre in `state.json` gibt es bewusst keine solche Grenze: Laesst sich die Datei
dauerhaft nicht schreiben (Datentraeger voll), bleibt der Helfer beschaeftigt, bis es wieder geht. Ginge das Journal
vorher, waeren Rueckweg bzw. Sperre verloren; das Ergebnis steht derweil im Status (aus dem geladenen Zustand)."""
STOP_MIN_S = 30
"""Mindestens so lange darf ein Container beim Stoppen brauchen, auch wenn sein `StopTimeout` kuerzer ist."""
RESCUE_MARKER = "HTTP Error 503"
"""So meldet der Healthcheck die Notseite des Dashboards. Nur ein Abbruchsignal: nie geloggt, nie ein Erfolg."""
RESCUE_CHECKS = 2
"""So viele letzte Eintraege in `Health.Log` muessen die Notseite zeigen."""
HEALTHY = "healthy"
"""Ergebnis von `judge`, wenn der Container gesund ist (kein Code aus `policy.CODES`)."""
OWN_START_S = 120
"""So lange nach dem Eintrag `started` startet der neue Container beim eigenen Start spaetestens (Zeitlimit des
Aufrufs plus Reserve). Startete er spaeter, hat ihn der Docker-Dienst neu gestartet."""
START_SLACK_S = 2
"""Spielraum beim Vergleich eines Starts (`State.StartedAt`) mit Zeitpunkten aus dem Journal (das Journal rechnet in
ganzen Sekunden). Etwas anderes als `policy.CLOCK_SLACK_S` (Spielraum fuer Uhrspruenge bei den Grenzen in `/state`)."""
TOGETHER_S = 120
"""So nah (davor oder danach) liegen die Starts des Helfers und des neuen Containers hoechstens, wenn der Docker-Dienst
beide zusammen gestartet hat (Neustart des Rechners oder des Dienstes: er startet die Container mit Restart-Policy kurz
nacheinander, auf einem langsamen Raspberry Pi kann das eine Weile dauern). Beide Zeitpunkte stammen dann aus derselben
Uhr kurz nach dem Hochfahren -- ihr Abstand stimmt, auch wenn die Uhr selbst falsch geht, solange NTP sie nicht
zwischen den beiden Starts stellt (dann zeigt die Laufzeit des Rechners den Neustart; `Flow._resume_left`)."""

_STEP_CODES = {
    "creating": policy.CREATE_FAILED, "created": policy.CREATE_FAILED, "old_stopped": policy.STOP_FAILED,
    "started": policy.START_FAILED,
}
"""Fehlercode, wenn ein Aufruf in diesem Schritt an der Engine scheitert. In den uebrigen Schritten gilt der Code
des Engine-Fehlers (`engine_unreachable`, `engine_unsupported`)."""


_TRANSPORT_ERRORS = frozenset({"unreachable", "timeout", "disconnected", "not_negotiated"})
"""Fehler auf dem Weg zur Engine (`EngineError.reason`), nicht in ihrer Antwort: Der Docker-Dienst war nicht da, hing
oder startete gerade neu. Ein spaeterer Versuch kann gelingen."""


def _index(step: str) -> int:
    return policy.STEPS.index(step)


def _refused_for_good(exc: EngineError) -> bool:
    """Hat die Engine den Aufruf endgueltig abgelehnt (4xx)? Dann aendert ein weiterer Versuch nichts."""
    return exc.reason == "http" and exc.status is not None and 400 <= exc.status < 500


# ---------------------------------------------------------------------------
# Ergebnis
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    """Ergebnis eines Vorgangs, fuer einen Eintrag in `status.results` (`result`).

    * `outcome`: einer aus `policy.OUTCOMES`, oder `None`, solange es noch nicht entschieden ist (das Journal steht
      noch vor dem Commit, etwa weil `committed` nicht geschrieben werden konnte oder der Rueckbau auf die Engine
      wartet; die Wiederaufnahme entscheidet).
    * `code`: ein fester Code oder `None` (bei `applied` und `reverted`).
    * `from_version`: die Version des Ziels vorher (soweit bekannt), `to_version`: die angeforderte.
    * `finished`: das Journal ist geloescht. `False`: es steht noch (Aufraeumen offen oder noch nicht entschieden),
      der Helfer bleibt beschaeftigt, bis die Wiederaufnahme fertig ist.
    """

    request_id: str
    action: str
    outcome: str | None
    code: str | None
    from_version: str | None
    to_version: str | None
    finished: bool = True

    def result(self, finished_at: float) -> dict[str, Any]:
        """Der Eintrag fuer `status.results`. Nur fuer ein entschiedenes Ergebnis."""
        if self.outcome is None:
            raise ValueError("noch kein Ergebnis")
        return {"id": self.request_id, "action": self.action, "from": self.from_version, "to": self.to_version,
                "outcome": self.outcome, "code": self.code, "finished_at": int(finished_at)}


def committed_result(journal: Journal, now: float) -> dict[str, Any] | None:
    """Das Ergebnis eines Vorgangs, dessen Journal auf `committed` steht (`applied` bzw. `reverted`), als Eintrag fuer
    `status.results` mit `finished_at` = `now`; sonst `None`. Braucht weder die Engine noch die eigene ID: ID, Aktion
    und beide Versionen stehen im Journal (derselbe Eintrag wie der des Ablaufs, `Flow._decide`)."""
    if journal.step != "committed" or journal.new is None:
        return None
    return Outcome(request_id=journal.request_id, action=journal.action, outcome=_committed_outcome(journal), code=None,
                   from_version=journal.old.version, to_version=journal.new.version).result(now)


def _committed_outcome(journal: Journal) -> str:
    return "applied" if journal.action == "update" else "reverted"


class _Abort(Exception):
    """Ein Schritt ist gescheitert (`code` aus `policy.CODES`; `None`, wenn die Wiederaufnahme ohne einen Fehler
    zurueckbaut): vor dem Commit zurueckbauen."""

    def __init__(self, code: str | None, detail: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class _External(Exception):
    """Jemand hat von aussen etwas geaendert (`detail`, ein fester Bezeichner): nichts mehr anfassen."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class _GaveUp(Exception):
    """Ein Aufruf beim Rueckbau oder Aufraeumen ging bis zur Frist nicht durch (`code`), oder der alte Container wurde
    nicht wieder gesund. `final`: die Engine hat ihn endgueltig abgelehnt (4xx), ein spaeterer Versuch aendert
    nichts. `transient`: es lag nur am Weg dorthin (die Engine war bis zur Frist nicht erreichbar, siehe
    `_TRANSPORT_ERRORS`) oder das Journal liess sich gerade nicht schreiben -- ein spaeterer Versuch kann gelingen."""

    def __init__(self, code: str | None, *, final: bool = False, transient: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.final = final
        self.transient = transient


class _WriteFailed(Exception):
    """Das Journal liess sich nicht schreiben (voller Datentraeger, ...)."""


@dataclass
class _Run:
    """Was ein Vorgang ueber seine Schritte mitnimmt. `journal` ist immer der Stand, der auf der Platte steht."""

    request_id: str
    action: str
    state: State
    to_version: str | None = None
    """Die angeforderte Version (bei der Wiederaufnahme die aus dem Journal, soweit sie dort schon steht)."""
    request: Request | None = None
    """Die Anforderung; bei der Wiederaufnahme gibt es sie nicht mehr."""
    from_version: str | None = None
    target: target.Target | None = None
    template: dict[str, Any] | None = None
    """Inspect des alten Containers: Vorlage fuer seine Compose-Labels und sein `StopTimeout`."""
    me: target.SelfInfo | None = None
    labels: tuple[tuple[str, str], ...] = ()
    slot: Slot | None = None
    """Beim Rueckweg der benutzte Slot."""
    journal: Journal | None = None
    binding: Binding | None = None
    new_image: dict[str, Any] | None = None
    plan: clone.Plan | None = None
    restart_base: int = 0
    """`RestartCount` des neuen Containers vor dem Start."""
    watch_until: float | None = None
    """Ende des Beobachtens auf der monotonen Uhr (aus der Frist im Journal)."""
    policy_restored: bool = False
    """Der Rueckbau hat die Restart-Policy des alten Containers in diesem Lauf wiederhergestellt."""
    stop_sent_at: float | None = None
    """Wann der Stopp des alten Containers hinausging (monotone Uhr); bei der Wiederaufnahme ab `old_stopped` der
    Zeitpunkt des Neustarts, denn ein Stopp kann noch laufen. `None`: es ging keiner hinaus."""


# ---------------------------------------------------------------------------
# Beobachten (reine Funktionen)
# ---------------------------------------------------------------------------


def shows_rescue_page(log: object) -> bool:
    """Zeigen die letzten `RESCUE_CHECKS` Eintraege in `Health.Log` die Notseite (503)? Die Ausgabe wird nur
    durchsucht, nie weitergegeben."""
    if not isinstance(log, list) or len(log) < RESCUE_CHECKS:
        return False
    last = log[-RESCUE_CHECKS:]
    return all(isinstance(entry, dict) and isinstance(entry.get("Output"), str) and RESCUE_MARKER in entry["Output"]
               for entry in last)


def judge(container: Mapping[str, Any], restart_base: int) -> str | None:
    """Was das Inspect eines gerade gestarteten Containers sagt: `HEALTHY`, ein harter Fehler (`restart_loop`,
    `exited`, `rescue_page`) oder `None` (weiter warten -- auch bei `unhealthy`).

    `restart_base` ist der `RestartCount` von vorher: steigt er, startet der Container neu. Ein Neustart zaehlt vor dem
    Rest, denn waehrend des Neustarts meldet der Docker-Dienst `Running` noch als wahr."""
    state = container.get("State")
    if not isinstance(state, dict):
        return None
    count = container.get("RestartCount")
    if state.get("Restarting") is True or (type(count) is int and count > restart_base):
        return policy.RESTART_LOOP
    if state.get("Running") is not True:
        return policy.EXITED
    health = state.get("Health")
    if not isinstance(health, dict):
        return None
    if shows_rescue_page(health.get("Log")):
        return policy.RESCUE_PAGE
    if health.get("Status") == "healthy" and state.get("Paused") is not True:
        return HEALTHY
    return None


def _running(container: Mapping[str, Any] | None) -> bool:
    state = container.get("State") if isinstance(container, Mapping) else None
    return isinstance(state, dict) and (state.get("Running") is True or state.get("Restarting") is True)


def _never_started(container: Mapping[str, Any]) -> bool:
    """Angelegt, aber nie gestartet (`State.StartedAt` ist der Nullwert des Docker-Dienstes)."""
    state = container.get("State")
    return (isinstance(state, dict) and state.get("Running") is not True and state.get("Restarting") is not True
            and state.get("StartedAt") == NEVER_STARTED)


def _restart_count(container: Mapping[str, Any]) -> int:
    count = container.get("RestartCount")
    return count if type(count) is int and count >= 0 else 0


def _platform(image: Mapping[str, Any]) -> tuple[Any, Any]:
    return image.get("Os"), image.get("Architecture")


# ---------------------------------------------------------------------------
# Der Ablauf
# ---------------------------------------------------------------------------


def _ignore(*_args: Any, **_fields: Any) -> None:
    return None


def boot_uptime() -> float | None:
    """Wie lange der Rechner seit dem Hochfahren laeuft, in Sekunden (`CLOCK_BOOTTIME`). Der Container teilt den Kernel
    und damit diese Uhr mit dem Rechner; sie haengt nicht an der Wanduhr (NTP stellt sie nie) und zaehlt einen
    Ruhezustand mit. `None`, wenn das System sie nicht nennt."""
    try:
        value = time.clock_gettime(time.CLOCK_BOOTTIME)
    except (AttributeError, OSError):
        return None
    return value if value >= 0 else None


class Flow:
    """Fuehrt Update und Rueckweg aus. Die Bausteine kommen ueber den Konstruktor:

    * `engine`, `store`: die Engine und der geoeffnete Zustand (`StateStore.open`, die Sperre haelt der Aufrufer).
    * `own_id`: die eigene Container-ID des Helfers (nie Teil eines Vorgangs).
    * `service`: der Compose-Dienst des Dashboards.
    * `clock`, `monotonic`, `sleep`: Wanduhr (fuer alles, was in `/state` steht), monotone Uhr (fuer Fristen und Warten
      im laufenden Prozess) und Warten. Die Tests stellen eine eigene Uhr.
    * `uptime`: wie lange der Rechner schon laeuft (`boot_uptime`; `None`, wenn unbekannt), fuer die Wiederaufnahme
      nach einem Neustart des Rechners (`_resume_left`). Die Tests stellen einen eigenen Startzeitpunkt.
    * `on_step`: wird nach jedem geschriebenen Journal-Schritt mit dem Journal aufgerufen (bei `committed` erst, wenn
      das Ergebnis im Zustand steht; danach noch einmal, sobald Slot bzw. Sperre gespeichert sind) und am Ende mit
      `None` (fuer den Status: `busy.step`). Ein Fehler darin unterbricht den Ablauf nicht.
    * `logger`: wie `__main__.log` (Ereignis + feste Felder).
    * `checkpoint`: nur fuer Tests, im Betrieb leer. Wird vor und nach jedem Schreiben in `/state` aufgerufen
      (`checkpoint(when, what)`: `when` ist `before` oder `after`, `what` der Schritt des Journals, `undo` fuer das
      Ankuendigen des Rueckbaus, `clear` fuer das Loeschen des Journals, `state` fuer `state.json`). Wirft er, endet
      der Ablauf genau dort wie bei einem Absturz.
    """

    def __init__(
        self,
        *,
        engine: Engine,
        store: StateStore,
        own_id: str,
        service: str,
        repository: str = policy.REPOSITORY,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        uptime: Callable[[], float | None] = boot_uptime,
        on_step: Callable[[Journal | None], None] | None = None,
        logger: Callable[..., None] | None = None,
        checkpoint: Callable[[str, str], None] | None = None,
    ) -> None:
        if not policy.is_container_id(own_id):
            raise ValueError("own_id")
        self._engine = engine
        self._store = store
        self._own_id = own_id
        self._service = policy.service_name(service)
        self._repository = repository
        self._clock = clock
        self._mono = monotonic
        self._sleep = sleep
        self._uptime = uptime
        self._on_step = on_step or _ignore
        self._log = logger or _ignore
        self._checkpoint = checkpoint or _ignore
        self._cleanup_misses: dict[str, int] = {}
        """Je Teil des Aufraeumens nach dem Commit: in wie vielen Runden die Engine ihn mit einem Fehler beantwortet hat
        (siehe `CLEANUP_ROUNDS`)."""

    # --- Einstieg -----------------------------------------------------------

    def run(self, request: Request, state: State) -> Outcome:
        """Fuehrt eine gepruefte Anforderung aus (Update oder Rueckweg) und gibt das Ergebnis zurueck.

        Der Aufrufer hat die Anforderung gelesen und gegen die Grenzen geprueft (`StateStore.check`, ID gemerkt), haelt
        die Sperre von `/state`, und es laeuft kein Vorgang. `state` ist der geladene Zustand: der Ablauf aendert ihn
        (Aktion zaehlen, Slot, Sperre nach einem Rueckweg) und speichert ihn selbst."""
        if request.action not in policy.ACTIONS:
            raise ValueError("unbekannte Aktion")
        run = _Run(request_id=request.id, action=request.action, state=state, to_version=request.version,
                   request=request)
        try:
            self._prepare(run)
            self._begin(run)
            if request.action == "update":
                self._fetch_update(run)
            else:
                self._fetch_previous(run)
            self._build(run)
            self._count(run)
        except Refusal as exc:
            return self._refuse(run, exc.code, exc.detail)
        except EngineError as exc:
            return self._refuse(run, exc.code, exc.reason)
        try:
            self._switch(run)
            code = self._observe(run)
            if code is not None:
                raise _Abort(code)
        except _Abort as exc:
            return self._undo(run, exc.code, exc.detail)
        except _External as exc:
            return self._external(run, exc.detail)
        return self._commit(run)

    def recover(self, state: State) -> Outcome | None:
        """Nimmt einen Vorgang wieder auf, der beim letzten Lauf nicht zu Ende kam (siehe Kopf des Moduls). `None`, wenn
        kein Journal da ist.

        Der Aufrufer haelt die Sperre von `/state` und uebergibt den geladenen Zustand; geprueft wird keine Grenze, und
        die Aktion wird nicht noch einmal gezaehlt. Ein ungueltiges oder gerade nicht lesbares Journal wirft
        `JournalCorrupt` bzw. `StateUnsafe` (aus `StateStore.load_journal`): dann handelt der Ablauf nicht. Bei
        `outcome=None` bzw. `finished=False` steht das Journal noch, der Aufrufer ruft `recover` spaeter erneut auf."""
        journal = self._store.load_journal(self._clock())
        if journal is None:
            return None
        run = _Run(request_id=journal.request_id, action=journal.action, state=state,
                   to_version=journal.new.version if journal.new is not None else None,
                   from_version=journal.old.version, journal=journal)
        self._log("recover", id=run.request_id, action=run.action, step=journal.step, undo=journal.undo)
        if _index(journal.step) >= _index("old_stopped"):
            run.stop_sent_at = self._mono()  # ob und wann gestoppt wurde, weiss nur der abgestuerzte Lauf
        if journal.step in ("begin", "pulled"):
            # Nichts veraendert, hoechstens ein Image gezogen. War der Rueckbau schon angekuendigt, bleibt sein Grund.
            return self._finish(run, "aborted", journal.code)
        if journal.step == "committed":
            self._decide(run)  # entschieden ist er schon: das Ergebnis braucht keine Engine
        try:
            self._context(run)
        except (EngineError, Refusal) as exc:
            return self._pending(run, exc.code)
        except _External as exc:
            return self._external(run, exc.detail)
        if journal.step == "committed":
            return self._finish_commit(run)
        if journal.undo:
            return self._undo(run, journal.code, "resumed")
        try:
            if journal.step == "creating":
                self._adopt(run, self._mono())  # ein Versuch; scheitert er an der Engine: spaeter erneut
            _old, created = self._check(run, undo=False, resume=True)
            if journal.step != "started":
                raise _Abort(None, "interrupted")
            if created is not None and _never_started(created):
                raise _Abort(policy.START_FAILED, "never_started")
            run.watch_until = self._mono() + self._resume_left(run, created)
            code = self._observe(run)
            if code is not None:
                raise _Abort(code)
        except _Abort as exc:
            return self._undo(run, exc.code, exc.detail)
        except _External as exc:
            return self._external(run, exc.detail)
        except EngineError as exc:
            return self._pending(run, exc.code)
        except _GaveUp as exc:
            return self._pending(run, exc.code)
        return self._commit(run)

    def _resume_left(self, run: _Run, created: Mapping[str, Any] | None) -> float:
        """Wie lange die Wiederaufnahme in `started` den neuen Container noch beobachtet (Sekunden, monotone Uhr).

        Grundsaetzlich bis zur Frist im Journal, aber nie laenger als eine ganze Frist: Wurde die Uhr seit dem
        Schreiben zurueckgestellt, laege die Frist sonst um den Sprung weiter in der Zukunft. Ein Neustart nur des
        Helfers verlaengert sie so nicht.

        Hat aber der Docker-Dienst den neuen Container inzwischen neu gestartet (Stromausfall, Neustart des Rechners
        oder des Dienstes), beginnt dessen Start und eine Migration von vorn: Er bekommt eine ganze Frist ab diesem
        Neustart, wie beim ersten Start. Die Wanduhr hilft dabei nur bedingt: Ein Raspberry Pi ohne Echtzeituhr startet
        mit der zuletzt gespeicherten Zeit, NTP stellt sie erst danach -- jeder Vergleich mit einem Zeitpunkt von vor
        dem Stellen stimmt dann nicht mehr. Darum drei Hinweise; es gilt der grosszuegigste, kuerzer als bis zur Frist
        im Journal und laenger als eine ganze Frist ab jetzt wird es nie:

        * **Laufzeit des Rechners** (`uptime`, `CLOCK_BOOTTIME`: haengt nicht an der Wanduhr, NTP stellt sie nie).
          Laeuft der Rechner noch keine ganze Frist, ist der neue Container erst nach dem Hochfahren gestartet worden
          (er laeuft ja): Er bekommt mindestens den Rest einer ganzen Frist ab dem Hochfahren. Das ist nie mehr, als ihm
          ab seinem eigenen Start zusteht. Nach einem Neustart nur des Helfers laeuft der Rechner laengst, dann zaehlt
          das nicht.
        * **Start des Helfers** (`SelfInfo.started_at`). Liegt `StartedAt` des neuen Containers hoechstens `TOGETHER_S`
          vor oder nach ihm, hat der Docker-Dienst beide zusammen gestartet, und beide Zeitpunkte stammen aus derselben
          Uhr (ihr Abstand stimmt, auch wenn sie falsch geht -- solange NTP sie nicht zwischen den beiden Starts stellt;
          dann hilft die Laufzeit des Rechners). Er bekommt eine ganze Frist ab jetzt, auch wenn seine Zeit zufaellig so
          aussieht wie der eigene Start (die gespeicherte Uhrzeit eines Pi lag kurz nach dem Eintrag `started`) oder wie
          ein Neustart vor langer Zeit. Wie lange er schon laeuft, ist mit einer falschen Uhr nicht zu sagen;
          wiederaufgenommen wird kurz nach dem gemeinsamen Start, die ganze Frist ab jetzt ist also hoechstens um diese
          kurze Zeit zu grosszuegig. Dieselbe Regel greift, wenn der Helfer zufaellig kurz vor oder nach dem eigenen
          Start des neuen Containers gestartet wurde (etwa nach einem Absturz gleich danach): Dann wird nur laenger
          gewartet, hoechstens eine ganze Frist ab der Wiederaufnahme statt bis zur Frist im Journal (folgt die
          Wiederaufnahme gleich dem Start, also nur etwa `TOGETHER_S` laenger). Ein vorschneller Rueckbau entsteht so
          nie. Startete der Helfer viel frueher oder viel spaeter als der neue Container (er allein wurde neu
          gestartet), gilt diese Regel nicht.
        * **Zeitpunkte aus dem Journal.** Der eigene Start folgt dem Eintrag `started` (write-ahead) binnen
          `OWN_START_S`: Liegt `StartedAt` dort, laeuft er seit dem eigenen Start, und es bleibt bei der Frist im
          Journal. Liegt es davor oder in der Zukunft, wurde die Uhr dazwischen gestellt -- wie lange er schon laeuft,
          ist dann unbekannt, und er bekommt eine ganze Frist ab jetzt. Liegt es dazwischen, eine ganze Frist ab
          `StartedAt`.

        Fehlt die Laufzeit des Rechners oder der Start des Helfers (`None`), entscheiden die uebrigen Hinweise."""
        journal = run.journal
        assert journal is not None
        whole = float(policy.DEADLINE_S)
        now = self._clock()
        left = min(max(journal.deadline - now, 0.0), whole)
        if not isinstance(created, Mapping) or not _running(created):
            return left
        uptime = self._uptime()
        fresh = whole - uptime if uptime is not None and 0.0 <= uptime < whole else 0.0
        started = target.started_at(created)
        written = journal.deadline - policy.DEADLINE_S
        helper_started = run.me.started_at if run.me is not None else None
        together = started is not None and helper_started is not None and abs(started - helper_started) <= TOGETHER_S
        if started is None or (not together and written - START_SLACK_S <= started <= written + OWN_START_S):
            pass  # Start unbekannt bzw. er laeuft seit dem eigenen Start: nur die Laufzeit des Rechners zaehlt
        elif together or started < written - START_SLACK_S or started > now + START_SLACK_S:
            fresh = whole  # mit dem Helfer zusammen gestartet, oder die Uhr wurde dazwischen gestellt
        else:
            fresh = max(fresh, started + whole - now)
        fresh = min(max(fresh, 0.0), whole)
        if fresh > left:
            self._log("resume_restarted", id=run.request_id, left=int(left), fresh=int(fresh), together=together,
                      uptime=None if uptime is None else int(uptime))
        return max(left, fresh)

    def _context(self, run: _Run) -> None:
        """Fuer die Wiederaufnahme neu lesen, was der erste Lauf aus der Vorpruefung kannte (nur lesend): die Labels des
        Ziels aus dem eigenen Projekt und den alten Container als Vorlage. Vor dem Commit muss der alte Container noch
        genau der aus dem Journal sein (`_External`)."""
        journal = run.journal
        assert journal is not None
        if self._engine.api_version is None:
            self._engine.negotiate()
        run.me = target.inspect_self(self._engine, self._own_id)
        run.labels = self._labels(run.me)
        old = journal.old
        current = self._inspect_or_none(old.id)
        if current is not None and current.get("Id") == old.id and current.get("Image") == old.image_id:
            run.template = current
        elif journal.step != "committed":
            raise _External("old_changed")

    def _pending(self, run: _Run, code: str | None) -> Outcome:
        """Die Engine antwortet gerade nicht: nichts entscheiden, das Journal bleibt stehen."""
        self._log("recover_pending", id=run.request_id, code=code)
        return self._outcome(run, None, None, finished=False)

    def drop_expired_slot(self, state: State) -> bool:
        """Raeumt einen abgelaufenen Slot weg: erst sein Schutz-Tag entfernen (solange der Slot noch in `state.json`
        steht), dann den Slot loeschen und speichern. Nur ohne laufenden Vorgang aufrufen. `True`, wenn er weg ist;
        scheitert die Engine voruebergehend, bleibt alles stehen (spaeter erneut). Lehnt sie endgueltig ab (4xx, etwa
        409, weil ein liegen gebliebener alter Container das Image noch benutzt), aendert ein weiterer Versuch nichts:
        Das Tag bleibt liegen (es haelt nur ein Image fest), der Slot geht trotzdem -- wie beim Aufraeumen nach dem
        Commit, sonst fragte der Helfer die Engine alle 30 s erneut, ohne Ende."""
        slot = state.slot
        if slot is None or self._now() < slot.until:
            return False
        binding = self._engine.bind(None, self._own_id, slot=slot)
        try:
            self._untag(slot.from_version, binding)
        except EngineError as exc:
            if not _refused_for_good(exc):
                self._log("slot_cleanup_failed", code=exc.code, detail=exc.reason)
                return False
            self._log("protect_tag_left", version=slot.from_version, code=exc.code, status=exc.status,
                      message=exc.message)
        state.slot = None
        try:
            self._save_state(state)
        except (OSError, ValueError):
            self._log("state_write_failed")
            return False
        self._log("slot_expired", version=slot.from_version)
        return True

    # --- Vorab: pruefen, Journal anlegen, Image holen ------------------------

    def _prepare(self, run: _Run) -> None:
        """Vorpruefung erneut und die Pruefungen der Anforderung, alles nur lesend."""
        request = run.request
        assert request is not None
        check = target.preflight(self._engine, self_id=self._own_id, service=self._service,
                                 repository=self._repository)
        run.from_version = check.current_version
        if not check.ready or check.target is None:
            raise Refusal(check.reason or policy.NO_TARGET, check.detail)
        found = check.target
        run.target = found
        run.template = found.container
        me = target.inspect_self(self._engine, self._own_id)
        if me.channel is None:
            raise Refusal(policy.CHANNEL_MISSING_IN_TARGET)
        run.me = me
        run.labels = self._labels(me)
        if request.action == "update":
            policy.check_tag_fits(found.floating_tag, request.version)
            policy.check_newer(request.version, found.version)
        else:
            run.slot = policy.check_rollback(request, run.state.slot, target_container_id=found.id,
                                             target_image_id=found.image_id, now=self._clock())
        if self._store.load_journal(self._clock()) is not None:
            raise Refusal(policy.BUSY)

    def _labels(self, me: target.SelfInfo) -> tuple[tuple[str, str], ...]:
        """Die Labels des Ziels: das Projekt des Helfers, der Dienst des Dashboards, kein One-off."""
        return ((target.LABEL_PROJECT, me.project), (target.LABEL_SERVICE, self._service),
                (target.LABEL_ONEOFF, "False"))

    def _begin(self, run: _Run) -> None:
        found = run.target
        assert found is not None
        now = self._now()
        journal = Journal(
            request_id=run.request_id, action=run.action, step="begin", started_at=now,
            deadline=now + policy.DEADLINE_S,
            old=OldContainer(id=found.id, name=found.name, image_id=found.image_id, version=found.version,
                             restart_policy=found.restart_policy, tag_text=found.tag_text,
                             repo_digest=found.repo_digests[0]),
            repository=self._repository,
        )
        try:
            self._write(run, journal)
        except _WriteFailed:
            raise Refusal(policy.STATE_UNSAFE, "journal_write") from None

    def _fetch_update(self, run: _Run) -> None:
        """Bewegliches Tag aufloesen, nach Digest ziehen, das gezogene Image pruefen; dann `pulled`."""
        journal = run.journal
        assert journal is not None and run.target is not None
        try:
            info = self._engine.distribution(journal.floating_tag)
        except EngineError as exc:
            self._log_engine("resolve_failed", run, exc)
            raise Refusal(policy.PULL_FAILED, "resolve") from None
        descriptor = info.get("Descriptor")
        digest = descriptor.get("digest") if isinstance(descriptor, dict) else None
        if not policy.is_digest(digest):
            raise Refusal(policy.PULL_FAILED, "digest")
        binding = self._engine.bind(journal, self._own_id, pull_digest=digest)
        ref = f"{self._repository}@{digest}"
        try:
            self._engine.pull(digest, binding=binding)
            image = self._engine.inspect_image(ref)
        except EngineError as exc:
            self._log_engine("pull_failed", run, exc)
            raise Refusal(policy.PULL_FAILED, exc.reason) from None
        if ref not in policy.registry_digests(image.get("RepoDigests"), repository=self._repository):
            raise Refusal(policy.PULL_FAILED, "repo_digest")
        image_id = image.get("Id")
        if not policy.is_image_id(image_id):
            raise Refusal(policy.PULL_FAILED, "image_id")
        labels = clone.image_config(image).get("Labels")
        try:
            label = policy.image_version(labels)
        except Refusal:
            raise Refusal(policy.TAG_NOT_ON_VERSION, "no_label") from None
        assert run.request is not None
        policy.check_label(label, run.request.version)
        policy.check_newer(label, journal.old.version)
        if _platform(image) != _platform(run.target.image) or not all(_platform(image)):
            raise Refusal(policy.PLATFORM_MISMATCH)
        run.new_image = image
        self._write_or_refuse(run, journal.advance("pulled", new=NewImage(image_id=image_id, digest=digest,
                                                                         version=label)))

    def _fetch_previous(self, run: _Run) -> None:
        """Rueckweg: das Image aus dem Slot. Fehlt es, nach dem Digest des Slots ziehen. Es muss genau die ID und die
        Version des Slots haben (`previous_mismatch`); dann `pulled`."""
        journal, slot = run.journal, run.slot
        assert journal is not None and slot is not None and run.target is not None
        prefix = self._repository + "@"
        digest = slot.repo_digest[len(prefix):]
        try:
            image = self._engine.inspect_image(slot.image_id)
        except EngineError as exc:
            if not exc.not_found:
                raise
            binding = self._engine.bind(journal, self._own_id, slot=slot, pull_digest=digest)
            try:
                self._engine.pull(digest, binding=binding)
                image = self._engine.inspect_image(slot.repo_digest)
            except EngineError as pull_exc:
                self._log_engine("pull_failed", run, pull_exc)
                raise Refusal(policy.PULL_FAILED, pull_exc.reason) from None
        if image.get("Id") != slot.image_id:
            raise Refusal(policy.PREVIOUS_MISMATCH, "image_id")
        try:
            label = policy.image_version(clone.image_config(image).get("Labels"))
        except Refusal:
            raise Refusal(policy.PREVIOUS_MISMATCH, "no_label") from None
        if label != slot.from_version:
            raise Refusal(policy.PREVIOUS_MISMATCH, "version")
        if _platform(image) != _platform(run.target.image) or not all(_platform(image)):
            raise Refusal(policy.PLATFORM_MISMATCH)
        run.new_image = image
        self._write_or_refuse(run, journal.advance("pulled", new=NewImage(image_id=slot.image_id, digest=digest,
                                                                         version=label)))

    def _build(self, run: _Run) -> None:
        """Den `create`-Body mit dem neuen Image bauen (reine Funktion; Ablehnungen wie in der Vorpruefung)."""
        found, journal = run.target, run.journal
        assert found is not None and journal is not None and journal.new is not None
        run.plan = clone.build(found.container, found.image, new_image_id=journal.new.image_id,
                               api_version=self._engine.api_version or API_MIN, network_drivers=found.network_drivers)

    def _count(self, run: _Run) -> None:
        """Ab hier aendert sich etwas: die Aktion zaehlt fuer die Grenze, gespeichert vor der ersten Aenderung."""
        run.state.record_action(self._now())
        try:
            self._save_state(run.state)
        except (OSError, ValueError):
            raise Refusal(policy.STATE_UNSAFE, "state_write") from None

    def _refuse(self, run: _Run, code: str, detail: str | None) -> Outcome:
        """Nichts veraendert (hoechstens ein Image gezogen): Journal loeschen, falls es eins gibt."""
        self._log("flow_refused", id=run.request_id, code=code, detail=detail)
        self._keep(run, "refused", code)
        finished = True
        if run.journal is not None:
            finished = self._clear(run)
        return self._outcome(run, "refused", code, finished=finished)

    # --- Vorwaerts ------------------------------------------------------------

    def _switch(self, run: _Run) -> None:
        """Von `protected` bis `started`. Jeder Fehler ist ein `_Abort` (Rueckbau) oder `_External`."""
        journal = run.journal
        assert journal is not None and journal.new is not None
        old, new = journal.old, journal.new
        engine = self._engine
        self._step(run, "protected",
                   lambda b: engine.tag_image(old.image_id, PREVIOUS_REPOSITORY, old.version, binding=b))
        self._step(run, "renamed",
                   lambda b: engine.rename_container(old.id, old.name + PREVIOUS_SUFFIX, binding=b))
        self._step(run, "tagged",
                   lambda b: engine.tag_image(new.image_id, self._repository, journal.floating_tag, binding=b))
        self._create(run)
        self._stop_old(run)
        self._start_new(run)

    def _step(self, run: _Run, step: str, call: Callable[[Binding], Any]) -> None:
        self._check_forward(run)
        binding = self._advance(run, step)
        try:
            call(binding)
        except EngineError as exc:
            self._log_engine("step_failed", run, exc)
            raise _Abort(self._code_for(step, exc), exc.reason) from None

    def _create(self, run: _Run) -> None:
        """`creating`, `create`, sofort `created` mit der neuen ID, dann `connect` und die Nachkontrolle."""
        journal, plan, found, me = run.journal, run.plan, run.target, run.me
        assert journal is not None and journal.new is not None and plan is not None and found is not None
        assert me is not None and me.channel is not None
        old = journal.old
        self._check_forward(run)
        binding = self._advance(run, "creating")
        try:
            new_id, warnings = self._engine.create_container(old.name, plan.body, binding=binding)
        except EngineError as exc:
            self._log_engine("create_failed", run, exc)
            raise _Abort(policy.CREATE_FAILED, exc.reason) from None
        if new_id in (old.id, self._own_id):
            raise _Abort(policy.CREATE_FAILED, "id")
        if warnings:
            self._log("create_warnings", id=run.request_id, count=warnings)
        binding = self._advance(run, "created", new=dataclasses.replace(journal.new, id=new_id))
        new_image_id = journal.new.image_id
        try:
            for network_id, endpoint in plan.connects:
                self._engine.connect_network(network_id, new_id, endpoint, binding=binding)
            created = self._engine.inspect_container(new_id)
        except EngineError as exc:
            self._log_engine("connect_failed", run, exc)
            raise _Abort(policy.CREATE_FAILED, exc.reason) from None
        try:
            clone.verify(found.container, created, plan, new_image=run.new_image or {}, new_image_id=new_image_id,
                         channel=me.channel.origin())
        except Refusal as exc:
            raise _Abort(policy.CLONE_MISMATCH, exc.detail) from None
        run.restart_base = _restart_count(created)

    def _stop_old(self, run: _Run) -> None:
        """`old_stopped`: Restart-Policy des alten auf `no`, stoppen, pruefen, dass er steht."""
        journal = run.journal
        assert journal is not None
        old = journal.old
        self._check_forward(run)
        binding = self._advance(run, "old_stopped")
        try:
            self._engine.set_restart_policy(old.id, "no", 0, binding=binding)
            run.stop_sent_at = self._mono()
            self._engine.stop_container(old.id, self._stop_t(run), binding=binding)
            stopped = self._engine.inspect_container(old.id)
        except EngineError as exc:
            self._log_engine("stop_failed", run, exc)
            raise _Abort(policy.STOP_FAILED, exc.reason) from None
        if _running(stopped) or not isinstance(stopped.get("State"), dict):
            raise _Abort(policy.STOP_FAILED, "still_running")

    def _start_new(self, run: _Run) -> None:
        """`started` mit neuer Frist, dann den neuen Container starten."""
        journal = run.journal
        assert journal is not None and journal.new is not None and journal.new.id is not None
        new_id = journal.new.id
        self._check_forward(run)
        binding = self._advance(run, "started", deadline=self._now() + policy.DEADLINE_S)
        run.watch_until = self._mono() + policy.DEADLINE_S
        try:
            self._engine.start_container(new_id, binding=binding)
        except EngineError as exc:
            self._log_engine("start_failed", run, exc)
            raise _Abort(policy.START_FAILED, exc.reason) from None

    def _observe(self, run: _Run) -> str | None:
        """Bis zum ersten `healthy` (`None`), einem harten Fehler oder der Frist (`timeout`). Engine-Fehler beenden das
        Beobachten nicht: neu aushandeln und beim naechsten Blick erneut."""
        journal, until = run.journal, run.watch_until
        assert journal is not None and until is not None
        while True:
            try:
                _old, created = self._check(run, undo=False)
            except EngineError as exc:
                self._log_engine("observe_retry", run, exc)
                self._renegotiate()
            else:
                verdict = judge(created or {}, run.restart_base)
                if verdict == HEALTHY:
                    return None
                if verdict is not None:
                    self._log("observe_failed", id=run.request_id, code=verdict)
                    return verdict
            if self._mono() >= until:
                return policy.TIMEOUT
            self._sleep(POLL_INTERVAL_S)

    # --- Commit ---------------------------------------------------------------

    def _commit(self, run: _Run) -> Outcome:
        """Beim ersten `healthy`: `committed` schreiben, das Ergebnis eintragen und erst dann den Status schreiben
        (`_decide`), dann aufraeumen. Ohne `committed` auf der Platte wird nichts mehr veraendert (das Journal bleibt
        auf `started`, die Wiederaufnahme entscheidet)."""
        try:
            self._advance(run, "committed", notify=False)
        except _Abort:
            self._log("commit_pending", id=run.request_id)
            return self._outcome(run, None, None, finished=False)
        self._decide(run)
        return self._finish_commit(run)

    def _decide(self, run: _Run) -> None:
        """Mit `committed` ist der Vorgang entschieden: das Ergebnis eintragen und speichern (`_keep`), dann den Status
        schreiben -- gleich nach dem Schreiben von `committed` (der Status dazu wartet darauf) und bei der
        Wiederaufnahme, jeweils vor jedem Aufruf an der Engine. Alles Noetige steht im Journal. So zeigt kein Status
        `busy.step` = `committed` ohne dieses Ergebnis, auch wenn das Aufraeumen danach an der Engine haengt oder sie
        gar nicht erreichbar ist. Scheitert das Speichern, steht das Ergebnis schon im geladenen Zustand (und damit im
        Status), und das Speichern mit dem Slot holt es nach. Eine Wiederaufnahme traegt es nicht noch einmal ein
        (dasselbe Ergebnis fuer dieselbe ID): Es behaelt seinen ersten Zeitpunkt."""
        journal = run.journal
        assert journal is not None
        self._keep(run, _committed_outcome(journal), None)
        self._notify(journal)

    def _finish_commit(self, run: _Run) -> Outcome:
        """Aufraeumen nach `committed`, auch bei der Wiederaufnahme. Jeder Teil ist wiederholbar (schon weg gilt als
        erledigt, ein schon geschriebener Slot wird nicht noch einmal geschrieben). Das Ergebnis steht schon im Zustand
        und im Status (`_decide`, vorher aufgerufen).

        Reihenfolge: Nach einem Update zuerst das Schutz-Tag des ersetzten Slots -- die Bindung laesst es nur zu,
        solange dieser Slot noch in `state.json` steht (das Speichern des Ergebnisses davor laesst den Slot
        unveraendert). Dann Slot bzw. Sperre und Ergebnis speichern, und zwar jedes Mal (der Inhalt ist derselbe):
        Scheiterte das Speichern in einer frueheren Runde, steht beides sonst nur im geladenen Zustand, und das Journal
        ginge, ohne dass es je auf der Platte stand. Gleich danach den Status schreiben (`previous` zeigt so sofort den
        neuen Slot). Erst danach den alten Container entfernen und beim Rueckweg die beiden Schutz-Tags (eins davon
        gehoert zum Image des alten Containers), zuletzt das Journal loeschen. So haengen Rueckweg-Slot und Ergebnis
        nie an einem Container, der nicht gehen will.

        Geht ein Teil bis `CLEANUP_RETRY_S` nicht durch, bleibt das Journal auf `committed` (`finished=False`, die
        naechste Runde macht weiter). Lehnt die Engine ihn endgueltig ab (4xx, etwa weil jemand den alten Container von
        Hand gestartet hat) oder scheitert er `CLEANUP_ROUNDS` Runden lang an ihr, bleibt dieser Teil liegen und es
        geht weiter -- sonst bliebe der Helfer fuer immer beschaeftigt."""
        journal = run.journal
        assert journal is not None and journal.new is not None and journal.new.id is not None
        old = journal.old
        state = run.state
        outcome = _committed_outcome(journal)
        update = journal.action == "update"
        needs_slot = update and not self._slot_written(run)
        # Die Bindung kennt den Slot, der jetzt ersetzt wird: nur dessen Schutz-Tag darf weg (beim Rueckweg beide
        # Versionen aus dem Journal). Ist der neue Slot schon gespeichert, bleibt sein Tag (die Bindung laesst die
        # Version des alten Images nach einem Update nie zu).
        binding = self._engine.bind(journal, self._own_id, slot=state.slot if update else None)
        tags = [("protect_tag_left", "untag:" + version, lambda v=version: self._untag(v, binding))
                for version in sorted(binding.protect_versions)]
        container = [("old_container_left", "remove", lambda: self._remove(old.id, binding))]
        before, after = (tags, container) if update else ([], container + tags)
        until = self._mono() + CLEANUP_RETRY_S
        if not self._cleanup(run, before, until):
            return self._outcome(run, outcome, None, finished=False)
        self._settle(run, needs_slot)
        try:
            self._save_state(state)
        except (OSError, ValueError):
            self._log("state_write_failed")
            return self._outcome(run, outcome, None, finished=False)
        self._notify(journal)
        if not self._cleanup(run, after, until):
            return self._outcome(run, outcome, None, finished=False)
        return self._outcome(run, outcome, None, finished=self._clear(run))

    def _cleanup(self, run: _Run, parts: list[tuple[str, str, Callable[[], None]]], until: float) -> bool:
        """Die Teile des Aufraeumens nacheinander, je `(Ereignis im Log, wenn er liegen bleibt; Schluessel fuer das
        Zaehlen der Runden; Aufruf)`. `False`, wenn einer in dieser Runde nicht durchging und es spaeter erneut versucht
        wird."""
        for left, key, call in parts:
            try:
                self._retry(call, until)
            except _GaveUp as exc:
                if not exc.final:
                    key = f"{run.request_id}:{key}"
                    misses = self._cleanup_misses.get(key, 0) + (0 if exc.transient else 1)
                    self._cleanup_misses[key] = misses
                    if misses < CLEANUP_ROUNDS:
                        self._log("commit_cleanup_pending", id=run.request_id, code=exc.code, rounds=misses)
                        return False
                self._log(left, id=run.request_id, code=exc.code)
        return True

    def _slot_written(self, run: _Run) -> bool:
        """Steht der Slot dieses Updates schon in `state.json` (Absturz nach dem Speichern, vor dem Loeschen des
        Journals)?"""
        journal, slot = run.journal, run.state.slot
        assert journal is not None and journal.new is not None
        return (slot is not None and slot.installed_container_id == journal.new.id
                and slot.installed_image_id == journal.new.image_id)

    def _settle(self, run: _Run, needs_slot: bool) -> None:
        """Slot nach einem Update bzw. Sperre nach einem Rueckweg in `run.state` eintragen, falls das noch nicht
        geschehen ist (ein schon eingetragener Slot und eine schon gesetzte Sperre bleiben, wie sie sind)."""
        journal = run.journal
        assert journal is not None and journal.new is not None and journal.new.id is not None
        old, new, state, now = journal.old, journal.new, run.state, self._now()
        if journal.action == "update":
            if not needs_slot:
                return
            # Der Digest stammt aus dem Journal, nicht aus der Engine: Ist das alte Image inzwischen weg (`image prune
            # -a`) oder nennt die Engine ihn nicht mehr (containerd-Store), zieht ein Rueckweg es nach genau diesem
            # Digest wieder und prueft ID und Version.
            state.commit_update(policy.new_slot(
                from_version=old.version, image_id=old.image_id, repo_digest=old.repo_digest,
                installed_container_id=new.id, installed_image_id=new.image_id, now=now,
                repository=self._repository))
            return
        if state.slot is None and state.blocked.get(old.version, 0) > now:
            return
        state.commit_rollback(old.version, now)

    # --- Rueckbau -------------------------------------------------------------

    def _undo(self, run: _Run, code: str | None, detail: str | None) -> Outcome:
        """Zurueck auf den alten Stand (siehe Kopf des Moduls). Jeder Teil ist wiederholbar; eine Wiederaufnahme mit
        angekuendigtem Rueckbau geht denselben Weg mit dem Grund aus dem Journal."""
        journal = run.journal
        assert journal is not None
        self._log("flow_undo", id=run.request_id, step=journal.step, code=code, detail=detail)
        until = self._mono() + policy.DEADLINE_S
        announced = False
        try:
            if journal.step == "creating":
                self._adopt(run, until)
            self._announce_undo(run, code)
            announced = True
            journal = run.journal
            assert journal is not None
            old, new = journal.old, journal.new
            done = _index(journal.step)
            binding = self._engine.bind(journal, self._own_id, undo=True)
            current, created = self._retry(lambda: self._check(run, undo=True), until)
            stop_t = self._stop_t(run)
            if created is not None and new is not None and new.id is not None:
                new_id = new.id
                self._retry(lambda: self._discard(new_id, stop_t, binding), until)
            if done >= _index("tagged"):
                self._retry(lambda: self._engine.tag_image(old.image_id, self._repository, journal.floating_tag,
                                                           binding=binding), until)
            if done >= _index("renamed") and current.get("Name") != "/" + old.name:
                self._retry(lambda: self._rename_back(old, binding), until)
            if done >= _index("old_stopped"):
                self._retry(lambda: self._engine.set_restart_policy(old.id, *old.restart_policy, binding=binding),
                            until)
                run.policy_restored = True
                if not _running(self._old_after_stop(run, until)):
                    self._retry(lambda: self._engine.start_container(old.id, binding=binding), until)
                verdict = self._watch(old.id, self._mono() + policy.DEADLINE_S)
                if verdict != HEALTHY:
                    raise _GaveUp(verdict)
            if done >= _index("protected") and not self._slot_keeps_tag(run):
                try:
                    self._retry(lambda: self._untag(old.version, binding), until)
                except _GaveUp:
                    # Der alte Container laeuft wieder: ein liegen gebliebenes Schutz-Tag haelt nur ein Image fest.
                    self._log("protect_tag_left", id=run.request_id, version=old.version)
        except _External as exc:
            return self._external(run, exc.detail)
        except _GaveUp as exc:
            current_journal = run.journal
            if exc.transient and current_journal is not None and (current_journal.undo or not announced):
                # Nur voruebergehend: Das Journal bleibt stehen -- mit angekuendigtem Rueckbau, oder noch vor dessen
                # erstem Teil --, und die Wiederaufnahme macht weiter, sobald die Engine bzw. der Datentraeger wieder
                # geht. Ohne Ankuendigung nach dem ersten Teil saehe sie den halben Rueckbau als Aenderung von aussen.
                self._log("undo_pending", id=run.request_id, code=exc.code)
                return self._outcome(run, None, None, finished=False)
            self._log("rollback_failed", id=run.request_id, code=code, detail=exc.code)
            return self._finish(run, "failed_manual", policy.ROLLBACK_FAILED)
        outcome = "aborted" if done < _index("started") else "rolled_back"
        return self._finish(run, outcome, code)

    def _announce_undo(self, run: _Run, code: str | None) -> None:
        """Den Rueckbau im Journal ankuendigen, bevor sein erster Teil laeuft. Geht das Schreiben nicht, wird trotzdem
        zurueckgebaut: der alte Container geht vor (eine Wiederaufnahme nach einem weiteren Absturz saehe den halben
        Rueckbau dann als Aenderung von aussen und fasste nichts an)."""
        journal = run.journal
        assert journal is not None
        if journal.undo:
            return
        try:
            self._write(run, journal.start_undo(code), what="undo")
        except _WriteFailed:
            self._log("undo_unannounced", id=run.request_id)

    def _adopt(self, run: _Run, until: float) -> None:
        """Rueckbau im Schritt `creating`: Wurde der neue Container angelegt, ohne dass seine ID im Journal steht (etwa
        `create` am Zeitlimit), wird er hier erkannt -- `<name>`, Zustand `created`, nie gestartet, das neue Image, die
        Compose-Labels des Ziels -- und erst als `created` ins Journal eingetragen, damit der Rueckbau ihn entfernen
        darf. Ein anderer Container unter `<name>` ist `external_change`."""
        journal = run.journal
        assert journal is not None and journal.new is not None
        wanted = "/" + journal.old.name
        listing = self._retry(lambda: self._engine.list_containers(), until)
        found = [item.get("Id") for item in listing
                 if isinstance(item.get("Names"), list) and wanted in item["Names"]]
        if not found:
            return
        candidate = found[0]
        if len(found) > 1 or not policy.is_container_id(candidate) or candidate in (journal.old.id, self._own_id):
            raise _External("name_taken")
        inspect = self._retry(lambda: self._engine.inspect_container(candidate), until)
        if not self._is_own_created(run, inspect):
            raise _External("name_taken")
        try:
            self._write(run, journal.advance("created", new=dataclasses.replace(journal.new, id=candidate)))
        except _WriteFailed:
            raise _GaveUp(policy.STATE_UNSAFE, transient=True) from None

    def _is_own_created(self, run: _Run, container: Mapping[str, Any]) -> bool:
        """Angelegt, nie gestartet, mit dem neuen Image und genau den Compose-Labels des Ziels (das Label
        `com.docker.compose.image` traegt beim Klon die neue Image-ID)."""
        journal, template = run.journal, run.template
        assert journal is not None and journal.new is not None and template is not None
        state = container.get("State")
        if not isinstance(state, dict) or state.get("Status") != "created" or state.get("Running") is not False:
            return False
        if state.get("StartedAt") != NEVER_STARTED or container.get("Image") != journal.new.image_id:
            return False
        if container.get("Name") != "/" + journal.old.name:
            return False
        wanted = _compose_labels(template)
        if clone.COMPOSE_IMAGE_LABEL in wanted:
            wanted[clone.COMPOSE_IMAGE_LABEL] = journal.new.image_id
        return _compose_labels(container) == wanted

    def _old_after_stop(self, run: _Run, until: float) -> dict[str, Any]:
        """Das Inspect des alten Containers, sobald ein Stopp von vorhin durch sein kann. Der Docker-Dienst stoppt nach
        einer Fehlerantwort auf `stop` (oder wenn die Antwort ausblieb) weiter und haelt den Container danach fuer von
        Hand gestoppt: `unless-stopped` startet ihn dann nie wieder, und sein Health-Status bleibt bis zum Ende auf
        `healthy`. Laeuft er noch, wird darum gewartet, bis er steht oder die Frist des Stopps (`t + STOP_GRACE_S` ab
        dem Aufruf) um ist; steht er, startet ihn der Rueckbau wie gewohnt. Laeuft er danach noch, laesst er sich
        wirklich nicht stoppen und gilt als laufend."""
        journal = run.journal
        assert journal is not None
        old_id = journal.old.id
        stop_until = None if run.stop_sent_at is None else run.stop_sent_at + self._stop_t(run) + STOP_GRACE_S
        waited = False
        while True:
            current = self._retry(lambda: self._engine.inspect_container(old_id), until)
            if not _running(current) or stop_until is None or self._mono() >= stop_until:
                return current
            if not waited:
                self._log("undo_waits_for_stop", id=run.request_id)
                waited = True
            self._sleep(POLL_INTERVAL_S)

    def _slot_keeps_tag(self, run: _Run) -> bool:
        """Das Schutz-Tag der alten Version gehoert zugleich dem Slot (das Ziel laeuft gerade mit der Version, auf die
        der Slot zurueckfuehrt): dann bleibt es liegen, sonst verloere der Slot seinen Schutz. Es geht mit dem Slot
        weg."""
        journal, slot = run.journal, run.state.slot
        return journal is not None and slot is not None and slot.from_version == journal.old.version

    def _watch(self, container_id: str, until: float) -> str | None:
        """Wartet nach dem Rueckbau auf den alten Container: `HEALTHY`, ein harter Fehler oder `timeout` (`until` auf
        der monotonen Uhr). War die Engine bis zur Frist nicht erreichbar, ist ueber den Container nichts bekannt:
        `_GaveUp` (voruebergehend), die Wiederaufnahme sieht spaeter nach."""
        base: int | None = None
        unreachable: EngineError | None = None
        while True:
            try:
                current = self._engine.inspect_container(container_id)
            except EngineError as exc:
                if exc.not_found:
                    return policy.EXITED
                unreachable = exc if exc.reason in _TRANSPORT_ERRORS else None
                self._renegotiate()
            else:
                unreachable = None
                if base is None:
                    base = _restart_count(current)
                verdict = judge(current, base)
                if verdict is not None:
                    return verdict
            if self._mono() >= until:
                if unreachable is not None:
                    raise _GaveUp(unreachable.code, transient=True)
                return policy.TIMEOUT
            self._sleep(POLL_INTERVAL_S)

    # --- Pruefen, ob jemand von aussen etwas geaendert hat ----------------------

    def _check_forward(self, run: _Run) -> None:
        try:
            self._check(run, undo=False)
        except EngineError as exc:
            self._log_engine("check_failed", run, exc)
            raise _Abort(exc.code, exc.reason) from None

    def _check(self, run: _Run, *, undo: bool, resume: bool = False) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Liest die Container erneut und haelt sie gegen das Journal. Gibt `(alter, neuer)` zurueck (der neue ist
        `None`, solange er nicht im Journal steht, oder im Rueckbau, wenn er schon entfernt ist). Wirft `_External`.

        * Kein weiterer Container mit den Ziel-Labels (Projekt, Dienst, kein One-off) ausser dem alten, dem neuen und
          dem Helfer selbst.
        * Der alte Container hat seine ID, sein Image und den Namen, den die erledigten Schritte erwarten (im Rueckbau
          einen der beiden); vorwaerts steht er ab `old_stopped`.
        * Der neue Container (ab `created`) hat seine ID und vorwaerts das neue Image und den Namen des Ziels.

        `resume` (Wiederaufnahme ohne angekuendigten Rueckbau): Der Schritt im Journal ist angekuendigt, aber vielleicht
        noch nicht ausgefuehrt. In `renamed` gilt darum jeder der beiden Namen, in `old_stopped` darf der alte noch
        laufen; fuer alle frueheren Schritte gilt dasselbe wie vorwaerts."""
        journal = run.journal
        assert journal is not None
        old, new = journal.old, journal.new
        new_id = new.id if new is not None else None
        allowed = {old.id} | ({new_id} if new_id is not None else set())
        filters = [f"{key}={value}" for key, value in run.labels]
        for item in self._engine.list_containers(filters):
            labels = item.get("Labels")
            if not isinstance(labels, dict) or any(labels.get(key) != value for key, value in run.labels):
                continue
            item_id = item.get("Id")
            if not policy.is_container_id(item_id):
                raise EngineError("bad_response")
            if item_id != self._own_id and item_id not in allowed:
                raise _External("foreign_container")
        current = self._inspect_or_none(old.id)
        if current is None or current.get("Id") != old.id or current.get("Image") != old.image_id:
            raise _External("old_changed")
        done = _index(journal.step)
        if undo or (resume and journal.step == "renamed"):
            names = {"/" + old.name, "/" + old.name + PREVIOUS_SUFFIX}
        else:
            names = {"/" + old.name + (PREVIOUS_SUFFIX if done >= _index("renamed") else "")}
        if current.get("Name") not in names:
            raise _External("old_renamed")
        stopped_from = _index("started") if resume else _index("old_stopped")
        if not undo and done >= stopped_from and _running(current):
            raise _External("old_running")
        created = None
        if new_id is not None:
            assert new is not None
            created = self._inspect_or_none(new_id)
            if created is None:
                if not undo:
                    raise _External("new_missing")
            elif created.get("Id") != new_id:
                raise _External("new_changed")
            elif not undo and (created.get("Image") != new.image_id or created.get("Name") != "/" + old.name):
                # Im Rueckbau genuegt die ID: sie stammt aus der Antwort auf das eigene `create` (oder aus der Pruefung
                # in `_adopt`), der Container gehoert also dem Ablauf -- auch wenn die Nachkontrolle ihn gerade wegen
                # eines falschen Images verworfen hat.
                raise _External("new_changed")
        return current, created

    def _inspect_or_none(self, container_id: str) -> dict[str, Any] | None:
        try:
            return self._engine.inspect_container(container_id)
        except EngineError as exc:
            if exc.not_found:
                return None
            raise

    def _external(self, run: _Run, detail: str) -> Outcome:
        """Von aussen geaendert: Journal loeschen, nichts mehr anfassen. Ab `old_stopped` hat der Helfer womoeglich
        die Restart-Policy des alten Containers auf `no` gesetzt und nicht wieder hergestellt (zuruecknehmen hiesse,
        doch etwas anzufassen): Das Log nennt dann die alte, damit sie sich von Hand setzen laesst."""
        self._log("external_change", id=run.request_id, detail=detail)
        journal = run.journal
        if (journal is not None and journal.step != "committed" and _index(journal.step) >= _index("old_stopped")
                and not run.policy_restored):
            name, retries = journal.old.restart_policy
            self._log("old_restart_policy_left", id=run.request_id, container=journal.old.id[:12], policy=name,
                      retries=retries)
        return self._finish(run, "external_change", policy.EXTERNAL_CHANGE)

    # --- Hilfen ---------------------------------------------------------------

    def _advance(self, run: _Run, step: str, *, notify: bool = True, **changes: Any) -> Binding:
        """Den naechsten Schritt ins Journal schreiben (write-ahead), dann neu binden. Scheitert das Schreiben, bleibt
        der vorige Stand gueltig und es geht in den Rueckbau (`state_unsafe`). `notify=False`: den Status schreibt der
        Aufrufer selbst (bei `committed` erst mit dem Ergebnis)."""
        journal = run.journal
        assert journal is not None
        try:
            self._write(run, journal.advance(step, **changes), notify=notify)
        except _WriteFailed:
            raise _Abort(policy.STATE_UNSAFE, "journal_write") from None
        binding = self._engine.bind(run.journal, self._own_id)
        run.binding = binding
        return binding

    def _write(self, run: _Run, journal: Journal, *, what: str | None = None, notify: bool = True) -> None:
        point = what or journal.step
        self._checkpoint("before", point)
        try:
            self._store.write_journal(journal)
        except (OSError, ValueError):
            self._log("journal_write_failed", id=run.request_id, step=journal.step)
            raise _WriteFailed() from None
        self._checkpoint("after", point)
        run.journal = journal
        self._log("flow_step", id=journal.request_id, action=journal.action, step=journal.step)
        if notify:
            self._notify(journal)

    def _write_or_refuse(self, run: _Run, journal: Journal) -> None:
        try:
            self._write(run, journal)
        except _WriteFailed:
            raise Refusal(policy.STATE_UNSAFE, "journal_write") from None

    def _clear(self, run: _Run) -> bool:
        self._checkpoint("before", "clear")
        try:
            self._store.clear_journal()
        except OSError:
            self._log("journal_clear_failed", id=run.request_id)
            return False
        self._checkpoint("after", "clear")
        run.journal = None
        self._cleanup_misses.clear()
        self._notify(None)
        return True

    def _save_state(self, state: State) -> None:
        self._checkpoint("before", "state")
        self._store.save_state(state)
        self._checkpoint("after", "state")

    def _finish(self, run: _Run, outcome: str, code: str | None) -> Outcome:
        self._keep(run, outcome, code)
        return self._outcome(run, outcome, code, finished=self._clear(run))

    def _entry(self, run: _Run, outcome: str, code: str | None) -> dict[str, Any]:
        return Outcome(request_id=run.request_id, action=run.action, outcome=outcome, code=code,
                       from_version=run.from_version, to_version=run.to_version).result(self._clock())

    def _keep(self, run: _Run, outcome: str, code: str | None) -> bool:
        """Das Ergebnis in `state.json` festhalten, bevor das Journal geloescht wird: So steht es nach einem Absturz
        dazwischen trotzdem im Status. Scheitert das Schreiben, geht es trotzdem weiter (das Ergebnis steht dann nur im
        Status dieses Laufs). `True`, wenn es neu eingetragen wurde (`False`: fuer diese ID stand dasselbe schon da)."""
        if not run.state.record_result(self._entry(run, outcome, code)):
            return False
        try:
            self._save_state(run.state)
        except (OSError, ValueError):
            self._log("state_write_failed")
        return True

    def _outcome(self, run: _Run, outcome: str | None, code: str | None, *, finished: bool) -> Outcome:
        """Das Ergebnis fuer den Aufrufer. `flow_done` kommt je Anforderung genau einmal ins Log: erst, wenn der Vorgang
        ganz fertig ist (das Journal ist geloescht). Bleibt das Aufraeumen nach dem Commit Runde um Runde offen, steht
        dort jedes Mal nur `commit_cleanup_pending`. So braucht es kein Gedaechtnis, das ueber einen Neustart verloren
        ginge oder begrenzt werden muesste: Nach dem Loeschen des Journals gibt es fuer diese ID keinen Ablauf mehr.

        Die Zeile `request` schreibt nicht der Ablauf, sondern `__main__`, einmal je ID und Prozess: sobald `run`
        zurueckkommt (beim Commit also erst nach der ersten Runde des Aufraeumens; `outcome` ist `None`, wenn noch
        nichts entschieden ist), nach einem Neustart des Helfers erst, wenn `recover` ein entschiedenes Ergebnis
        liefert. Sie steht also nie schon im Moment der Entscheidung im Log."""
        if outcome is not None and finished:
            self._log("flow_done", id=run.request_id, action=run.action, outcome=outcome, code=code)
        return Outcome(request_id=run.request_id, action=run.action, outcome=outcome, code=code,
                       from_version=run.from_version, to_version=run.to_version, finished=finished)

    def _notify(self, journal: Journal | None) -> None:
        try:
            self._on_step(journal)
        except Exception as exc:  # noqa: BLE001 - der Status ist Nebensache, der Ablauf geht vor
            self._log("on_step_failed", kind=type(exc).__name__[:60])

    def _retry(self, call: Callable[[], Any], until: float) -> Any:
        """Ruft `call` auf, bis es ohne `EngineError` durchgeht. Dazwischen 5 s warten und neu aushandeln. Ein
        Client-Fehler der Engine (4xx) ist endgueltig, ebenso das Erreichen von `until` (monotone Uhr): dann
        `_GaveUp`."""
        while True:
            try:
                return call()
            except EngineError as exc:
                final = _refused_for_good(exc)
                self._log("engine_retry", code=exc.code, detail=exc.reason, status=exc.status, final=final,
                          message=exc.message)
                if final or self._mono() >= until:
                    raise _GaveUp(exc.code, final=final, transient=exc.reason in _TRANSPORT_ERRORS) from None
                self._sleep(POLL_INTERVAL_S)
                self._renegotiate()

    def _renegotiate(self) -> None:
        try:
            self._engine.negotiate()
        except (EngineError, Refusal) as exc:
            self._log("negotiate_failed", code=exc.code)

    def _remove(self, container_id: str, binding: Binding) -> None:
        """Container entfernen (nie mit Volumes, nie erzwungen); schon weg gilt als erledigt."""
        try:
            self._engine.remove_container(container_id, binding=binding)
        except EngineError as exc:
            if not exc.not_found:
                raise

    def _discard(self, container_id: str, t: int, binding: Binding) -> None:
        """Den neuen Container im Rueckbau stoppen und entfernen. Gestoppt wird immer, auch wenn er gerade nicht laeuft
        (dann antwortet der Docker-Dienst mit 304): Ging die Antwort auf `start` verloren, kann er ihn noch danach
        starten. Lehnt der Docker-Dienst das Entfernen ab (409) und laeuft der Container inzwischen, wird er noch
        einmal gestoppt und dann entfernt."""
        self._stop(container_id, t, binding)
        try:
            self._remove(container_id, binding)
        except EngineError as exc:
            if exc.reason != "http" or exc.status != 409:
                raise
            current = self._inspect_or_none(container_id)
            if current is None:
                return
            if not _running(current):
                raise
            self._log("undo_stops_again", id=container_id[:12])
            self._stop(container_id, t, binding)
            self._remove(container_id, binding)

    def _stop(self, container_id: str, t: int, binding: Binding) -> None:
        try:
            self._engine.stop_container(container_id, t, binding=binding)
        except EngineError as exc:
            if not exc.not_found:
                raise

    def _rename_back(self, old: OldContainer, binding: Binding) -> None:
        """Den alten Container im Rueckbau zurueck auf den Namen des Ziels umbenennen. Als einziger Aufruf des Rueckbaus
        ist das nicht von sich aus wiederholbar: Ging die Antwort auf einen frueheren Versuch verloren (Zeitlimit,
        abgerissene Verbindung), hat der Docker-Dienst ihn womoeglich schon umbenannt und lehnt ein Umbenennen auf den
        aktuellen Namen mit 400 ab. Darum nach jedem Fehler nachsehen: traegt er den Namen schon, ist es erledigt."""
        try:
            self._engine.rename_container(old.id, old.name, binding=binding)
        except EngineError:
            current = self._engine.inspect_container(old.id)
            if current.get("Id") == old.id and current.get("Name") == "/" + old.name:
                return
            raise

    def _untag(self, version: str, binding: Binding) -> None:
        """Schutz-Tag entfernen; schon weg gilt als erledigt."""
        try:
            self._engine.remove_protect_tag(version, binding=binding)
        except EngineError as exc:
            if not exc.not_found:
                raise

    def _stop_t(self, run: _Run) -> int:
        template = run.template
        config = template.get("Config") if template is not None else None
        value = config.get("StopTimeout") if isinstance(config, dict) else None
        wanted = value if type(value) is int and value > 0 else 0
        return min(max(STOP_MIN_S, wanted), MAX_STOP_T)

    @staticmethod
    def _code_for(step: str, exc: EngineError) -> str:
        if step in _STEP_CODES:
            return _STEP_CODES[step]
        if step == "renamed" and exc.reason == "http" and exc.status == 409:
            return policy.NAME_TAKEN
        return exc.code

    def _log_engine(self, event: str, run: _Run, exc: EngineError) -> None:
        self._log(event, id=run.request_id, code=exc.code, detail=exc.reason, status=exc.status, message=exc.message)

    def _now(self) -> int:
        return int(self._clock())


def _compose_labels(container: Mapping[str, Any]) -> dict[str, str]:
    config = container.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if not isinstance(labels, dict):
        return {}
    return {key: value for key, value in labels.items()
            if isinstance(key, str) and key.startswith(clone.COMPOSE_PREFIX)}
