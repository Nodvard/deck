"""Alter der Virensignaturen: einlesen, bewerten, als Hinweis aufbereiten.

Das Datum kommt aus `clamscan --version` (``ClamAV 1.0.7/27410/Wed Sep 24 08:23:12 2026``): Es ist
der Bauzeitpunkt der neuesten Signaturdatei, also das Alter der Signaturen selbst und nicht nur der
Zeitpunkt der letzten Abfrage. Der Befehl braucht kein root und steht schon im Status-Befehl.
"""

from __future__ import annotations

import re
import time
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


def signature_reasons(rows: list[dict[str, Any]]) -> list[str]:
    """Der Abzug fuer Signaturen als kurzer Satz fuer die Liste der Gruende (leer, wenn nichts abgezogen wird)."""
    points = signature_penalty(rows)
    if not points:
        return []
    stale = sum(1 for r in rows if r.get("clamav_installed") and r.get("signature_stale") is True)
    text = f"Virensignaturen veraltet auf {_servers(stale, len(rows))}" if stale else "Alter der Virensignaturen unbekannt"
    return [f"{text} (−{points})"]


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


# Was der Schutzwert ausser dem Virenschutz mitzaehlt (`posture_penalty`), je anteilig nach der Zahl der Server. Ohne
# diese Abzuege stand ein Server mit offenen Sicherheitsupdates, root-Anmeldung per Passwort und ohne Fail2ban als "Gut
# geschuetzt" da, solange ClamAV lief. Gezaehlt wird nur, was aktuell gelesen wurde: ein Server ohne Update-Pruefung
# oder ohne Blick des Einbruchschutzes kostet nichts (er steht dort ohnehin als "noch nicht geprueft"). Ist die letzte
# Pruefung gescheitert oder aelter als `STALE_AFTER_S`, zaehlt der alte Stand nicht als Tatsache; das steht dann als
# Grund ohne Abzug da, damit ein dauerhaft scheiternder Server den Wert nicht still verbessert.
UPDATES_PENALTY_POINTS = 15
"""Sicherheitsupdates offen."""
REBOOT_PENALTY_POINTS = 5
"""Neustart nach Updates noetig (der neue Kernel oder die neuen Bibliotheken laufen noch nicht)."""
FAIL2BAN_PENALTY_POINTS = 10
"""Fail2ban fehlt oder laeuft nicht. "nicht lesbar" (ohne root) zaehlt nicht."""
SSH_ROOT_PASSWORD_POINTS = 10
"""root darf sich per Passwort anmelden (SSH-Befund "kritisch")."""
SSH_PASSWORD_POINTS = 5
"""Anmeldung per Passwort erlaubt (SSH-Befund "Warnung")."""
STALE_AFTER_S = 3 * 24 * 3600
"""Aelter als drei Tage: der gespeicherte Stand zaehlt nicht mehr (Updates werden taeglich geprueft)."""
EVENT_POINTS = 5
EVENT_MAX_POINTS = 15
"""Je offenes kritisches Sicherheitsereignis (z. B. Angriff auf SSH), hoechstens so viel."""


def _share(points: int, count: int, total: int) -> int:
    """Anteilig nach der Zahl der Server, aber mindestens 1 Punkt, sobald ein Server betroffen ist (bei vielen Servern
    fiele er sonst durch die Rundung ganz weg und stuende auch nicht unter den Gruenden)."""
    if not total or count <= 0:
        return 0
    return max(1, round(points * count / total))


def _fresh(state: Any, now: float) -> bool:
    """Zaehlt dieser gespeicherte Stand? Nur ohne Fehler und nicht aelter als `STALE_AFTER_S` (ohne Zeitstempel:
    ja, aeltere Staende hatten keinen)."""
    if not isinstance(state, dict) or state.get("error"):
        return False
    at = state.get("checked_at")
    return not isinstance(at, int | float) or now - at <= STALE_AFTER_S


def _unchecked(states: dict[str, dict[str, Any]], host_ids: list[str], now: float) -> int:
    """Server mit einem Stand, der nicht zaehlt (Pruefung gescheitert oder veraltet). Ohne Stand (noch nie geprueft)
    und "wird nicht unterstuetzt" zaehlen hier nicht."""
    return sum(
        1 for h in host_ids
        if isinstance(states.get(h), dict) and not states[h].get("unsupported") and not _fresh(states[h], now)
    )


def _servers(count: int, total: int) -> str:
    return f"{count} von {total} Servern" if total > 1 else "dem Server"


def posture_penalty(
    host_ids: list[str],
    updates: dict[str, dict[str, Any]],
    guard: dict[str, dict[str, Any]],
    critical_events: int,
    *,
    now: float | None = None,
) -> tuple[int, list[str]]:
    """Abzug fuer offene Updates, Einbruchschutz und SSH-Einstellungen (siehe oben) und die Gruende als kurze Saetze mit
    den Punkten. `updates`/`guard`: der gespeicherte Stand je Host (Baselines "updates" und "guard")."""
    total = len(host_ids)
    if not total:
        return 0, []
    now = time.time() if now is None else now
    upd = [updates[h] for h in host_ids if _fresh(updates.get(h), now)]
    grd = [guard[h] for h in host_ids if _fresh(guard.get(h), now)]
    security = sum(1 for s in upd if int(s.get("security_count") or 0) > 0)
    reboot = sum(1 for s in upd if s.get("reboot_required") and not s.get("reboot_pending_since"))
    no_fail2ban = sum(1 for v in grd if v.get("fail2ban") in ("none", "stopped"))

    def worst_ssh(view: dict[str, Any]) -> str | None:
        severities = {f.get("severity") for f in view.get("ssh_findings") or [] if isinstance(f, dict)}
        return "critical" if "critical" in severities else "warning" if "warning" in severities else None

    ssh = [worst_ssh(v) for v in grd]
    root_password, password = ssh.count("critical"), ssh.count("warning")
    parts = [
        (_share(UPDATES_PENALTY_POINTS, security, total), f"Sicherheitsupdates offen auf {_servers(security, total)}"),
        (_share(REBOOT_PENALTY_POINTS, reboot, total), f"Neustart nötig auf {_servers(reboot, total)}"),
        (_share(FAIL2BAN_PENALTY_POINTS, no_fail2ban, total), f"Fail2ban fehlt oder läuft nicht auf {_servers(no_fail2ban, total)}"),
        (_share(SSH_ROOT_PASSWORD_POINTS, root_password, total),
         f"Gefährliche SSH-Anmeldung erlaubt (root mit Passwort oder leeres Passwort) auf {_servers(root_password, total)}"),
        (_share(SSH_PASSWORD_POINTS, password, total), f"Anmeldung per Passwort erlaubt auf {_servers(password, total)}"),
        (min(EVENT_MAX_POINTS, EVENT_POINTS * max(0, critical_events)),
         f"{critical_events} offene{'s' if critical_events == 1 else ''} kritische{'s' if critical_events == 1 else ''} Sicherheitsereignis{'' if critical_events == 1 else 'se'}"),
    ]
    points = sum(p for p, _text in parts)
    reasons = [f"{text} (−{p})" for p, text in parts if p > 0]
    for count, what in ((_unchecked(updates, host_ids, now), "Updates"), (_unchecked(guard, host_ids, now), "Einbruchschutz")):
        if count:
            reasons.append(f"{what} auf {_servers(count, total)} zuletzt nicht geprüft (ohne Abzug)")
    return points, reasons
