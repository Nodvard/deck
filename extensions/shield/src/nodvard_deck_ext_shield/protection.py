"""Alter der Virensignaturen: einlesen, bewerten, als Hinweis aufbereiten.

Das Datum kommt aus `clamscan --version` (``ClamAV 1.0.7/27410/Wed Sep 24 08:23:12 2026``): Es ist
der Bauzeitpunkt der neuesten Signaturdatei, also das Alter der Signaturen selbst und nicht nur der
Zeitpunkt der letzten Abfrage. Der Befehl braucht kein root und steht schon im Status-Befehl.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

# Das Signatur-Update von ClamAV laeuft normalerweise mehrmals taeglich, die Signaturen werden
# taeglich neu gebaut. Eine Woche Spielraum deckt einen kurzen Ausfall oder einen ausgeschalteten
# Server ab; was aelter ist, erkennt neue Schaedlinge nicht mehr.
SIGNATURE_STALE_DAYS = 7

# Gewichtung im Schutzwert (siehe `signature_penalty`):
# - Veraltete Signaturen ziehen bis zu 30 Punkte ab, anteilig nach der Zahl aller Server (wie die
#   Abdeckung). Im Schutzwert zaehlt "ClamAV installiert" 40 Punkte; ein Scanner mit Signaturen von vor
#   Wochen erkennt neue Schaedlinge kaum noch und erfuellt diesen Anteil nur zum Teil. 30 von 40
#   Punkten sind deshalb Absicht: Sind alle Server betroffen, faellt der Wert um 30 und kann nicht mehr
#   "Gut geschuetzt" (ab 80) sein. Ein einzelner veralteter Server unter zehn kostet 3 Punkte, steht
#   aber in der Hinweisliste. Weil der Abzug je Server (30) unter seinem Abdeckungs-Anteil (40) bleibt,
#   steht ein Server mit alten Signaturen nie schlechter da als einer ganz ohne ClamAV.
# - Ein unbekanntes Alter (Datum fehlt in der Ausgabe oder ist nicht lesbar) kostet nur bis zu 5 Punkte:
#   Es ist kein Beleg fuer "veraltet", aber auch keiner fuer "frisch" -- meist fehlt dann nur die
#   Signaturdatei oder die Ausgabe ist ungewoehnlich. Als Hinweis steht es trotzdem da.
STALE_PENALTY_POINTS = 30
UNKNOWN_PENALTY_POINTS = 5

_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
# "Wed Sep 24 08:23:12 2026", "Wed Sep  4 08:23 2026", "Wed Sep 24 2026", auch mit Zeitzone vor dem Jahr.
_DATE = re.compile(
    r"^(?:[A-Za-z]{3},?\s+)?(?P<mon>[A-Za-z]{3})[a-z]*\s+(?P<day>\d{1,2})"
    r"(?:\s+(?P<h>\d{1,2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?)?(?:\s+[A-Z]{2,5})?\s+(?P<year>\d{4})\s*$"
)


def parse_signature_date(text: str | None) -> float | None:
    """Zeitstempel (Sekunden) aus dem Datum der Signaturen; `None`, wenn es fehlt oder unlesbar ist.

    `clamscan --version` nennt das Datum in der Ortszeit des Servers ohne Zeitzone. Es wird hier trotzdem
    als UTC gelesen: Die Abweichung betraegt je nach Zeitzone hoechstens einen Tag und faellt gegen die
    Grenze von sieben Tagen nicht ins Gewicht."""
    if not text:
        return None
    match = _DATE.match(" ".join(text.split()))
    if not match:
        return None
    month = _MONTHS.get(match["mon"].lower())
    if month is None:
        return None
    try:
        return datetime(
            int(match["year"]), month, int(match["day"]), int(match["h"] or 0), int(match["m"] or 0), int(match["s"] or 0), tzinfo=UTC,
        ).timestamp()
    except ValueError:
        return None


def signature_info(timestamp: float | None, now: float) -> dict[str, Any]:
    """`signature_age_days` (ganze Tage, `None` = unbekannt) und `signature_stale` (`None` = unbekannt)."""
    if timestamp is None:
        return {"signature_age_days": None, "signature_stale": None}
    age = max(0.0, now - timestamp)
    return {"signature_age_days": int(age // 86400), "signature_stale": age > SIGNATURE_STALE_DAYS * 86400}


def age_text(days: int) -> str:
    if days >= 14:
        return f"{days // 7} Wochen"
    return "1 Tag" if days == 1 else f"{days} Tage"


def signature_penalty(rows: list[dict[str, Any]]) -> int:
    """Punkte, die der Schutzwert wegen veralteter oder unbekannter Signaturen verliert."""
    if not rows:
        return 0
    with_clam = [r for r in rows if r.get("clamav_installed")]
    stale = sum(1 for r in with_clam if r.get("signature_stale") is True)
    unknown = sum(1 for r in with_clam if r.get("signature_stale") is None and r.get("reachable") is not False)
    return round(STALE_PENALTY_POINTS * stale / len(rows) + UNKNOWN_PENALTY_POINTS * unknown / len(rows))


def signature_problem(row: dict[str, Any]) -> str | None:
    """Kurzer Satz fuer das Morgen-Briefing, `None`, wenn nichts zu tun ist. Ein unbekanntes Alter ist nur ein
    Hinweis auf der Seite und loest keine Morgenmeldung aus."""
    if row.get("clamav_installed") and row.get("signature_stale") is True:
        return f"{row['host_name']}: Signaturen {age_text(int(row.get('signature_age_days') or 0))} alt"
    return None


def attention_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Was auf der Uebersicht unter "Braucht Aufmerksamkeit" steht. `kind` sagt der Seite, welcher Knopf dazugehoert
    (`signatures` = Signaturen aktualisieren)."""
    items: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("clamav_installed"):
            continue
        if row.get("signature_stale") is True:
            items.append({
                "kind": "signatures", "tone": "warn", "host_id": row["host_id"], "host_name": row["host_name"],
                "title": f"{row['host_name']}: Virensignaturen sind {age_text(int(row.get('signature_age_days') or 0))} alt",
                "hint": "Mit alten Signaturen erkennt ClamAV neue Schadprogramme nicht. Signaturen aktualisieren – "
                        "dabei wird auch das automatische Update eingeschaltet.",
                "action_label": "Signaturen aktualisieren",
            })
        elif row.get("signature_stale") is None and row.get("reachable") is not False:
            items.append({
                "kind": "signatures", "tone": "info", "host_id": row["host_id"], "host_name": row["host_name"],
                "title": f"{row['host_name']}: Alter der Virensignaturen unbekannt",
                "hint": "Das Datum der Signaturen fehlt oder ist nicht lesbar, vielleicht fehlen die Signaturen. Aktualisieren behebt das meist.",
                "action_label": "Signaturen aktualisieren",
            })
    return items
