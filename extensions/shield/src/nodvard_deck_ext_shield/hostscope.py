"""Host-Bezug fuer Meldungen: das Wartungsfenster des Kerns unterdrueckt eine Meldung
nur ueber den Payload (eine Meldung ohne Host-Bezug wird nie still). Betrifft sie einen
Server, steht `host_id`; ein Sammelbericht ueber mehrere Server traegt `host_ids` und ist
nur still, wenn ALLE diese Server in einem laufenden Fenster liegen.

Kritische Sicherheitsmeldungen (Schadsoftware-Fund, kritische Einbruchschutz-Ereignisse)
tragen bewusst keinen Host-Bezug: ein Fenster soll Lärm durch geplante Arbeiten
stummschalten, nie einen Fund. Der Tiefenscan läuft ab Werk sonntags um 03:30 -- mitten
im Fenster, das der Zeitplan-Wähler vorschlägt."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def host_scope(host_ids: Iterable[str]) -> dict[str, Any]:
    """Payload-Teil fuer die Server, die eine Meldung betrifft: `{"host_id": ...}` bei
    genau einem, `{"host_ids": [...]}` bei mehreren, sonst `{}`."""
    distinct = sorted(set(host_ids))
    if len(distinct) == 1:
        return {"host_id": distinct[0]}
    return {"host_ids": distinct} if distinct else {}
