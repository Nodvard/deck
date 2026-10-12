"""Host-Bezug im Payload einer Meldung; nur darüber schaltet das Wartungsfenster des Kerns sie still."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


# Ein Sammelbericht über mehrere Server (`host_ids`) ist nur still, wenn alle in einem laufenden Fenster liegen.
# Kritische Meldungen (Fund, Einbruchschutz) tragen bewusst keinen Host-Bezug: Ein Fenster darf nie einen Fund
# stummschalten, und der Tiefenscan liegt ab Werk (sonntags 03:30) mitten im vorgeschlagenen Fenster.
def host_scope(host_ids: Iterable[str]) -> dict[str, Any]:
    """Payload-Teil für die betroffenen Server: `host_id` bei genau einem, `host_ids` bei mehreren, sonst `{}`."""
    distinct = sorted(set(host_ids))
    if len(distinct) == 1:
        return {"host_id": distinct[0]}
    return {"host_ids": distinct} if distinct else {}
