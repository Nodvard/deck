"""Dauerfreigabe für geplante Skripte: Ein Owner oder Admin gibt ein Skript einmal frei, der Zeitplan läuft dann ohne
Klick."""

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

# Eigene JSON-Datei im Datenverzeichnis, bewusst nicht in `meta.json`: Ein Zurücksetzen des Skripts auf eine alte Fassung
# darf eine erloschene Freigabe nicht wiederbeleben. Ist die Datei nicht lesbar, gilt keine Freigabe.


def port_label(port: int | None) -> str:
    return "keiner" if port is None else str(port)


def targets_fingerprint(targets: list[SdkHost]) -> str:
    """Fingerabdruck der Zielserver (Kennung, Konto, Adresse, SSH-Port), wie die Seite sie vor dem Erteilen zeigt."""
    # Passt er beim Erteilen nicht mehr, wird nicht freigegeben: Es gilt nur, was die Person gesehen hat.
    rows = sorted(
        json.dumps([h.id, h.credential_username, h.address, h.credential_port], ensure_ascii=False)
        for h in targets
    )
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def script_fingerprint(meta: ScriptMeta, content: str) -> str:
    """Fingerabdruck der Teile, die bestimmen, was wo wann läuft."""
    # Name, Beschreibung und „aktiv“ zählen nicht (ein pausiertes Skript läuft ohnehin nicht). Parameter zählen samt
    # Vorgabewerten, denn ein geplanter Lauf nutzt genau die; von geheimen nur die Namen, die Werte liegen im Tresor.
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
    granted_at: str  # ISO-Zeitstempel (UTC)
    # Zielserver zur Zeit der Freigabe: Konto, Name (nur zur Anzeige), Adresse und SSH-Port, je nach Kennung.
    hosts: dict[str, str | None] = field(default_factory=dict)
    host_names: dict[str, str] = field(default_factory=dict)
    addresses: dict[str, str] = field(default_factory=dict)
    # Fehlt eine Kennung (ältere Einträge), wird der Port nicht verglichen; None heißt: Der Server hatte keinen Zugang.
    ports: dict[str, int | None] = field(default_factory=dict)

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
    problem: str | None = None  # warum die Freigabe nicht mehr gilt; dann erlischt sie
    covered: list[SdkHost] = field(default_factory=list)  # laufen ohne Klick
    uncovered: list[SdkHost] = field(default_factory=list)  # bei der Freigabe nicht dabei, brauchen die normale Freigabe


def evaluate(record: StandingRecord, meta: ScriptMeta, content: str, targets: list[SdkHost]) -> Evaluation:
    """Prüft bei jedem Lauf, ob die Freigabe noch gilt; ein alter Eintrag mit anderem Fingerabdruck startet nichts."""
    # Erloschen ist sie, wenn sich Inhalt, Parameter, Ziel oder Zeitplan ändern oder ein freigegebener Server unter anderem
    # Konto, anderer Adresse oder anderem SSH-Port erreicht wird. Ein neuer Server bekommt die normale Freigabe, die übrigen
    # laufen weiter; ein weggefallener macht sie nicht ungültig, weniger Ziele bedeuten nie mehr Risiko.
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
    logged: bool = False  # Protokollzeile geschrieben, beim Nachholen nicht noch einmal
    notified: bool = False  # Push-Nachricht raus oder nicht gewünscht


# {Pfad der Datei: {script_id: PendingExpiry}}, im Speicher, weil die Datei gerade dann nicht beschreibbar sein kann.
# Nach einem Neustart ist der Merker weg, der Vergleich beim nächsten Lauf lässt den Eintrag erneut erlöschen.
_BLOCKED: dict[str, dict[str, PendingExpiry]] = {}


class StandingApprovals:
    """Dauerfreigaben je Skript; erlöschen wirkt sofort, auch wenn die Datei nicht beschreibbar ist."""

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
        """Die Freigabe, die gilt; None ohne Eintrag oder wenn sie erloschen ist (auch wenn sich der Eintrag nicht
        löschen ließ)."""
        record = self.get_raw(script_id)
        if record is None:
            return None
        pending = self._blocked().get(script_id)
        if pending is not None and pending.granted_at == record.granted_at:
            return None
        return record

    def pending_expiry(self, script_id: str) -> PendingExpiry | None:
        """Erloschen, aber Protokoll oder Löschen stehen noch aus; der nächste Lauf holt sie nach."""
        record = self.get_raw(script_id)
        pending = self._blocked().get(script_id)
        if pending is None:
            return None
        if record is None or record.granted_at != pending.granted_at:
            # Inzwischen gelöscht oder neu erteilt: nichts mehr nachzuholen.
            self.unblock(script_id)
            return None
        return pending

    def block(self, script_id: str, record: StandingRecord, reason: str) -> PendingExpiry:
        """Ab sofort gilt `record` nicht mehr, egal ob sich der Eintrag löschen lässt."""
        blocked = self._blocked()
        pending = blocked.get(script_id)
        if pending is None or pending.granted_at != record.granted_at:
            pending = PendingExpiry(granted_at=record.granted_at, reason=reason)
            blocked[script_id] = pending
        return pending

    def unblock(self, script_id: str) -> None:
        self._blocked().pop(script_id, None)

    def set(self, script_id: str, record: StandingRecord) -> None:
        """Wirft OSError, wenn nicht gespeichert werden kann; eine nur scheinbar erteilte Freigabe wäre schlimmer."""
        state = self._read()
        state[script_id] = asdict(record)
        self._write(state)
        self.unblock(script_id)

    def remove(self, script_id: str) -> StandingRecord | None:
        """Entfernt die Freigabe und gibt sie zurück (None, wenn es keine gab); vorher `block()`, damit sie sicher nicht
        mehr gilt."""
        state = self._read()
        raw = state.pop(script_id, None)
        if raw is None:
            return None
        try:
            self._write(state)
        except OSError:
            # Fail closed: Sonst gälte die Freigabe nach einem Neustart wieder, die Sperre (`block`) liegt nur im Speicher.
            # Löschen geht auch bei voller Platte; dann müssen alle Dauerfreigaben neu erteilt werden.
            _log.error(
                "Dauerfreigaben: Datei nicht beschreibbar -- alle Dauerfreigaben werden geloescht.",
                exc_info=True,
            )
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass
        return StandingRecord.from_json(raw)
