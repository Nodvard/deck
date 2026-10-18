"""Alter der Virensignaturen und weitere Abzüge im Schutzwert."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any

# ClamAV aktualisiert mehrmals täglich; eine Woche Spielraum deckt einen kurzen Ausfall oder einen ausgeschalteten Server ab.
SIGNATURE_STALE_DAYS = 7

# Abzug im Schutzwert, anteilig nach Servern. Veraltete Signaturen kosten höchstens 30 Punkte, weniger als der
# ClamAV-Anteil (40): Ein Server mit alten Signaturen steht nie schlechter da als einer ohne ClamAV, und sind alle
# betroffen, ist „Gut geschützt“ (ab 80) unerreichbar. Unbekanntes Alter (meist fehlt nur die Datei): 5 Punkte.
STALE_PENALTY_POINTS = 30
UNKNOWN_PENALTY_POINTS = 5

_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
# Formen: "Wed Sep 24 08:23:12 2026", "Wed Sep  4 08:23 2026", "Wed Sep 24 2026", auch mit Zeitzone vor dem Jahr.
_DATE = re.compile(
    r"^(?:[A-Za-z]{3},?\s+)?(?P<mon>[A-Za-z]{3})[a-z]*\s+(?P<day>\d{1,2})"
    r"(?:\s+(?P<h>\d{1,2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?)?(?:\s+[A-Z]{2,5})?\s+(?P<year>\d{4})\s*$"
)


def parse_signature_date(text: str | None) -> float | None:
    """Zeitstempel (Sekunden) aus dem Datum der Signaturen; `None`, wenn es fehlt oder unlesbar ist.

    `clamscan --version` nennt den Bauzeitpunkt der neuesten Signaturdatei, also das Alter der Signaturen selbst."""
    if not text:
        return None
    match = _DATE.match(" ".join(text.split()))
    if not match:
        return None
    month = _MONTHS.get(match["mon"].lower())
    if month is None:
        return None
    # Das Datum steht in der Ortszeit des Servers ohne Zeitzone. Als UTC gelesen weicht es höchstens einen Tag ab,
    # gegen die Grenze von sieben Tagen unerheblich.
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
    """Der Abzug für Signaturen als kurzer Satz für die Liste der Gründe (leer, wenn nichts abgezogen wird)."""
    points = signature_penalty(rows)
    if not points:
        return []
    stale = sum(1 for r in rows if r.get("clamav_installed") and r.get("signature_stale") is True)
    text = f"Virensignaturen veraltet auf {_servers(stale, len(rows))}" if stale else "Alter der Virensignaturen unbekannt"
    return [f"{text} (−{points})"]


def signature_problem(row: dict[str, Any]) -> str | None:
    """Kurzer Satz für das Morgen-Briefing, `None`, wenn nichts zu tun ist."""
    # Ein unbekanntes Alter ist nur ein Hinweis auf der Seite und löst keine Morgenmeldung aus.
    if row.get("clamav_installed") and row.get("signature_stale") is True:
        return f"{row['host_name']}: Signaturen {age_text(int(row.get('signature_age_days') or 0))} alt"
    return None


def attention_items(rows: list[dict[str, Any]], held_reboots: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """Einträge unter „Braucht Aufmerksamkeit“ auf der Übersicht; `kind` sagt der Seite, welcher Knopf dazugehört.

    `held_reboots`: Server, deren automatischer Neustart auf ein sauberes Update wartet."""
    items: list[dict[str, Any]] = [
        {
            "kind": "reboot_held", "tone": "warn", "host_id": row["host_id"], "host_name": row["host_name"],
            "title": f"{row['host_name']}: Neustart wartet auf ein sauberes Update",
            "hint": "Das letzte Update ist nicht sauber durchgelaufen, deshalb starten die automatischen Updates den Server "
                    "nicht neu. In der Update-Zentrale nachsehen und das Update erneut einspielen. Ist nichts mehr offen, "
                    "den Server selbst neu starten.",
            "action_label": "",
        }
        for row in rows if row["host_id"] in held_reboots
    ]
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


# Was der Schutzwert außer dem Virenschutz mitzählt (`posture_penalty`), anteilig nach Servern. Gezählt wird nur
# Aktuelles: Ohne Prüfung kostet ein Server nichts. War die letzte Prüfung gescheitert oder älter als `STALE_AFTER_S`,
# steht das als Grund ohne Abzug da, damit ein dauerhaft scheiternder Server den Wert nicht still verbessert.
UPDATES_PENALTY_POINTS = 15
"""Sicherheitsupdates offen."""
REBOOT_PENALTY_POINTS = 5
"""Neustart nach Updates nötig (neuer Kernel oder neue Bibliotheken laufen noch nicht)."""
FAIL2BAN_PENALTY_POINTS = 10
"""Fail2ban fehlt oder läuft nicht; „nicht lesbar“ (ohne root) zählt nicht."""
SSH_ROOT_PASSWORD_POINTS = 10
"""root darf sich per Passwort anmelden (SSH-Prüfung: kritisch)."""
SSH_PASSWORD_POINTS = 5
"""Anmeldung per Passwort erlaubt (SSH-Prüfung: Warnung)."""
STALE_AFTER_S = 3 * 24 * 3600
"""Älter als drei Tage: der gespeicherte Stand zählt nicht mehr (Updates werden täglich geprüft)."""
EVENT_POINTS = 5
EVENT_MAX_POINTS = 15
"""Je offenes kritisches Sicherheitsereignis (z. B. Angriff auf SSH), höchstens so viel."""


def _share(points: int, count: int, total: int) -> int:
    """Anteilig nach der Zahl der Server, aber mindestens 1 Punkt, sobald ein Server betroffen ist."""
    if not total or count <= 0:
        return 0
    # Sonst fiele er bei vielen Servern durch die Rundung weg und stünde auch nicht unter den Gründen.
    return max(1, round(points * count / total))


def _fresh(state: Any, now: float) -> bool:
    """Zählt dieser gespeicherte Stand? Nur ohne Fehler und nicht älter als `STALE_AFTER_S`."""
    if not isinstance(state, dict) or state.get("error"):
        return False
    at = state.get("checked_at")
    # Stände ohne Zeitstempel (ältere Versionen) zählen.
    return not isinstance(at, int | float) or now - at <= STALE_AFTER_S


def _unchecked(states: dict[str, dict[str, Any]], host_ids: list[str], now: float) -> int:
    """Server mit einem Stand, der nicht zählt (Prüfung gescheitert oder veraltet).

    Ohne Stand (nie geprüft) und „wird nicht unterstützt“ zählen hier nicht mit."""
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
    """Abzug für offene Updates, Einbruchschutz und SSH-Einstellungen samt Gründen als kurzen Sätzen mit den Punkten.

    `updates`/`guard`: gespeicherter Stand je Host (Baselines „updates“ und „guard“)."""
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
