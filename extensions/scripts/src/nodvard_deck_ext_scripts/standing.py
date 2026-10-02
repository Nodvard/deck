"""Dauerfreigabe fuer geplante Skripte: Ein Owner/Admin gibt ein Skript EINMAL frei, danach
laufen die Zeitplan-Laeufe ohne Klick, solange sich nichts aendert.

Was die Freigabe festhaelt (und was sie erloeschen laesst):

- `fingerprint`: SHA-256 ueber Inhalt, Parameter (samt Vorgabewerten -- ein geplanter Lauf
  nutzt genau die), Ziel (Server/Gruppe/Alle) und Zeitplan. Name, Beschreibung und der
  Schalter "aktiv" zaehlen nicht: sie aendern weder was, noch wo, noch wann etwas laeuft
  (ein pausiertes Skript laeuft ohnehin nicht). Geheime Werte stehen nie darin, nur die Namen
  der geheimen Parameter (die Werte liegen im Tresor).
- `hosts`: die Zielserver zum Zeitpunkt der Freigabe, je mit dem Konto, unter dem das Dashboard
  sich dort anmeldet (z. B. "root"), dazu (`addresses`) ihre Adresse und (`ports`) den SSH-Port.
  Wird ein freigegebener Server spaeter unter einem anderen Konto, einer anderen Adresse oder
  einem anderen Port angesprochen, erlischt die ganze Freigabe -- das ist eine Aenderung an
  etwas Freigegebenem (WO und ALS WER).
  Welche Server mit welchem Konto die Freigabe deckt, sieht die freigebende Person vorher; die
  Seite schickt dazu `targets_fingerprint` mit, und passt der nicht mehr, wird nicht freigegeben. Kommt ein Server neu dazu (Gruppe, "Alle Server"), laeuft das Skript dort
  NICHT ohne Klick, sondern bekommt eine normale Freigabe; die anderen laufen weiter. Faellt ein
  Server weg, laufen die uebrigen weiter -- weniger Ziele heisst nie mehr Risiko.

Der Stand liegt als kleine JSON-Datei im Datenverzeichnis der Extension (neben
`skip-notices.json`, ausserhalb des Git-Repos `repo/`): keine eigene Tabelle, also keine
Migration. Bewusst NICHT in `meta.json`: Ein Zuruecksetzen des Skripts auf eine alte Fassung
darf eine erloschene Freigabe nicht wiederbeleben. Kann die Datei nicht gelesen werden, gilt
keine Freigabe (dann wird eben wieder geklickt). Ob eine Freigabe gilt, entscheidet zudem bei
jedem Lauf der Vergleich hier -- auch ein nicht geloeschter Eintrag mit altem Fingerabdruck
startet nichts.

Erloeschen wirkt sofort, auch wenn die Datei gerade nicht beschreibbar ist oder das Protokoll
scheitert: `block()` merkt sich die erloschene Freigabe im Speicher, `get()` liefert sie dann
nicht mehr, und der naechste Lauf holt Protokoll und Loeschen nach (`pending_expiry()`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from nodvard_sdk import Host as SdkHost

from .repo import ScriptMeta

_log = logging.getLogger(__name__)

_FINGERPRINT_VERSION = 1


def port_label(port: int | None) -> str:
    return "keiner" if port is None else str(port)


def targets_fingerprint(targets: list[SdkHost]) -> str:
    """Fingerabdruck der Zielserver, wie die Seite sie vor dem Erteilen zeigt: je Server
    Kennung, Konto, Adresse und SSH-Port. Damit wird nur freigegeben, was man gesehen hat."""
    rows = sorted(
        json.dumps([h.id, h.credential_username, h.address, h.credential_port], ensure_ascii=False)
        for h in targets
    )
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def script_fingerprint(meta: ScriptMeta, content: str) -> str:
    """Fingerabdruck der Teile, die bestimmen, WAS WO WANN laeuft (siehe Modul-Docstring)."""
    data = {
        "v": _FINGERPRINT_VERSION,
        "content": content,
        "params_schema": meta.params_schema,
        "target": meta.target,
        "schedule": meta.schedule,
    }
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class StandingRecord:
    fingerprint: str
    granted_by_user_id: str
    granted_by_label: str
    granted_at: str
    """ISO-Zeitstempel (UTC)."""
    hosts: dict[str, str | None] = field(default_factory=dict)
    """{host_id: Konto} der Zielserver bei der Freigabe."""
    host_names: dict[str, str] = field(default_factory=dict)
    """{host_id: Name} nur fuer die Anzeige."""
    addresses: dict[str, str] = field(default_factory=dict)
    """{host_id: Adresse} der Zielserver bei der Freigabe."""
    ports: dict[str, int | None] = field(default_factory=dict)
    """{host_id: SSH-Port} der Zielserver bei der Freigabe. Fehlt die Kennung (Eintrag aus der Zeit
    vor diesem Feld), wird der Port nicht verglichen; steht dort None, galt ein Server ohne Zugang."""

    @classmethod
    def from_json(cls, data: Any) -> "StandingRecord | None":
        if not isinstance(data, dict):
            return None
        try:
            hosts = data.get("hosts") or {}
            names = data.get("host_names") or {}
            addresses = data.get("addresses") or {}
            ports = data.get("ports") or {}
            return cls(
                fingerprint=str(data["fingerprint"]),
                granted_by_user_id=str(data["granted_by_user_id"]),
                granted_by_label=str(data.get("granted_by_label") or data["granted_by_user_id"]),
                granted_at=str(data["granted_at"]),
                hosts={str(k): (None if v is None else str(v)) for k, v in dict(hosts).items()},
                host_names={str(k): str(v) for k, v in dict(names).items()},
                addresses={str(k): str(v) for k, v in dict(addresses).items()},
                ports={str(k): (None if v is None else int(v)) for k, v in dict(ports).items()},
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass
class Evaluation:
    valid: bool
    problem: str | None = None
    """Warum die Freigabe nicht (mehr) gilt -- dann erlischt sie."""
    covered: list[SdkHost] = field(default_factory=list)
    """Ziele, die ohne Klick laufen duerfen."""
    uncovered: list[SdkHost] = field(default_factory=list)
    """Neue Ziele, die bei der Freigabe nicht dabei waren: normale Freigabe."""


def evaluate(record: StandingRecord, meta: ScriptMeta, content: str, targets: list[SdkHost]) -> Evaluation:
    if record.fingerprint != script_fingerprint(meta, content):
        return Evaluation(valid=False, problem="Das Skript wurde seit der Freigabe geändert.")
    covered: list[SdkHost] = []
    uncovered: list[SdkHost] = []
    for host in targets:
        if host.id not in record.hosts:
            uncovered.append(host)
            continue
        before, now = record.hosts[host.id], host.credential_username
        if before != now:
            name = host.display_name or host.name
            return Evaluation(
                valid=False,
                problem=(
                    f"Auf „{name}“ meldet sich das Dashboard jetzt unter einem anderen Konto an "
                    f"({before or 'keins'} → {now or 'keins'})."
                ),
            )
        address = record.addresses.get(host.id)
        if address is not None and address != host.address:
            name = host.display_name or host.name
            return Evaluation(
                valid=False,
                problem=f"„{name}“ hat eine neue Adresse ({address} → {host.address}).",
            )
        if host.id in record.ports and record.ports[host.id] != host.credential_port:
            name = host.display_name or host.name
            return Evaluation(
                valid=False,
                problem=(
                    f"Auf „{name}“ meldet sich das Dashboard jetzt über einen anderen SSH-Port an "
                    f"({port_label(record.ports[host.id])} → {port_label(host.credential_port)})."
                ),
            )
        covered.append(host)
    return Evaluation(valid=True, covered=covered, uncovered=uncovered)


@dataclass
class PendingExpiry:
    """Eine erloschene Freigabe, deren Eintrag (noch) in der Datei steht."""

    granted_at: str
    reason: str
    logged: bool = False
    """Die Protokollzeile ist geschrieben (beim Nachholen nicht noch einmal)."""
    notified: bool = False
    """Die Push-Nachricht ist raus (oder war nicht gewuenscht)."""


_BLOCKED: dict[str, dict[str, PendingExpiry]] = {}
"""{Pfad der Datei: {script_id: PendingExpiry}} -- im Speicher, weil die Datei in genau diesem
Fall nicht beschreibbar sein kann. Nach einem Neustart ist der Merker weg; dann laesst der
Vergleich (Fingerabdruck, Konto, Adresse) bzw. das Gate (Person, Rechte) den Eintrag erneut
erloeschen."""


class StandingApprovals:
    def __init__(self, path: Path) -> None:
        self._path = path

    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            _log.warning("Dauerfreigaben: Datei nicht lesbar -- es gilt keine Freigabe.", exc_info=True)
            return {}
        return raw if isinstance(raw, dict) else {}

    def _write(self, state: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._path)

    def _blocked(self) -> dict[str, PendingExpiry]:
        return _BLOCKED.setdefault(str(self._path), {})

    def get_raw(self, script_id: str) -> StandingRecord | None:
        """Der Eintrag in der Datei, auch wenn er schon erloschen ist (siehe `get`)."""
        return StandingRecord.from_json(self._read().get(script_id))

    def get(self, script_id: str) -> StandingRecord | None:
        """Die Freigabe, die gilt -- None ohne Eintrag oder wenn sie erloschen ist, ihr
        Eintrag sich aber noch nicht loeschen liess (`block`)."""
        record = self.get_raw(script_id)
        if record is None:
            return None
        pending = self._blocked().get(script_id)
        if pending is not None and pending.granted_at == record.granted_at:
            return None
        return record

    def pending_expiry(self, script_id: str) -> PendingExpiry | None:
        """Erloschen, aber Protokoll oder Loeschen stehen noch aus."""
        record = self.get_raw(script_id)
        pending = self._blocked().get(script_id)
        if pending is None:
            return None
        if record is None or record.granted_at != pending.granted_at:
            # Inzwischen geloescht oder neu erteilt: nichts mehr nachzuholen.
            self.unblock(script_id)
            return None
        return pending

    def block(self, script_id: str, record: StandingRecord, reason: str) -> PendingExpiry:
        """Ab sofort gilt `record` nicht mehr, egal ob sich der Eintrag loeschen laesst."""
        blocked = self._blocked()
        pending = blocked.get(script_id)
        if pending is None or pending.granted_at != record.granted_at:
            pending = PendingExpiry(granted_at=record.granted_at, reason=reason)
            blocked[script_id] = pending
        return pending

    def unblock(self, script_id: str) -> None:
        self._blocked().pop(script_id, None)

    def set(self, script_id: str, record: StandingRecord) -> None:
        """Wirft OSError, wenn nicht gespeichert werden kann -- eine Freigabe, die nur
        scheinbar erteilt wurde, waere schlimmer als eine Fehlermeldung."""
        state = self._read()
        state[script_id] = asdict(record)
        self._write(state)
        self.unblock(script_id)

    def remove(self, script_id: str) -> StandingRecord | None:
        """Entfernt die Freigabe und gibt sie zurueck (None, wenn es keine gab). Wirft
        OSError, wenn nicht gespeichert werden kann -- dann vorher `block()` aufrufen, damit
        sie trotzdem nicht mehr gilt."""
        state = self._read()
        raw = state.pop(script_id, None)
        if raw is None:
            return None
        try:
            self._write(state)
        except OSError:
            # Fail closed: Laesst sich die Datei nicht neu schreiben (z. B. Platte voll), wuerde
            # die Freigabe nach einem Neustart wieder gelten -- die Sperre (`block`) liegt nur im
            # Speicher. Dann lieber die ganze Datei loeschen (geht auch bei voller Platte): es
            # gilt keine Dauerfreigabe mehr, alle muessen neu erteilt werden.
            _log.error(
                "Dauerfreigaben: Datei nicht beschreibbar -- alle Dauerfreigaben werden geloescht.",
                exc_info=True,
            )
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass
        return StandingRecord.from_json(raw)
