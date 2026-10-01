"""Der "Hostname-als-Containername"-Schutz. Faengt einen typischen Fehlschluss ab: die
KI sieht "Host X nicht erreichbar" und schlaegt faelschlich `docker restart X` vor,
weil sie X fuer einen Container haelt.

Die Richtung des Checks steht im Namen von `looks_like_host_not_container()`: er
prueft, ob ein *Container*-Name wie ein *Host*-Name aussieht, nicht umgekehrt.

Bewusst NICHT im Kern (`core/deny_patterns.py`): die Wortliste haengt von den
Servernamen der jeweiligen Installation ab und ist keine generische Kern-Regel --
`scripts/check_core_purity.py` haelt solche Listen aus dem Kern heraus."""

from __future__ import annotations

import re
from typing import Any, NamedTuple

DEFAULT_FORBIDDEN_HOST_KEYWORDS = (
    "pve", "proxmox", "host", "server",
    "node", "router", "gateway", "nas",
)
"""Allgemeiner Startwert, kein Zwang: jede Installation ersetzt bzw. ergaenzt diese
Liste ueber die Extension-Einstellungen (`forbidden_host_keywords`) um die Namen der
eigenen Server, siehe settings.schema.json."""

_DOCKER_RESTART_RE = re.compile(r"\bdocker\s+restart\s+([A-Za-z0-9_.-]+)", re.IGNORECASE)


def looks_like_host_not_container(command: str, *, forbidden_keywords: tuple[str, ...] | None = None) -> str | None:
    """Prueft NUR `docker restart <name>`-Befehle -- andere Befehle haben keinen
    "Container-Namen" im selben Sinne. Gibt den verdaechtigen Namen zurueck, wenn er
    ein verbotenes Schluesselwort enthaelt, sonst `None`. Case-insensitive
    Teilstring-Pruefung (`kw in name.lower()`), keine Wortgrenzen -- "pve1" wird von "pve" erkannt, das ist gewollt, nicht nur
    ein exaktes "pve"."""
    match = _DOCKER_RESTART_RE.search(command)
    if match is None:
        return None
    target = match.group(1).lower()
    keywords = forbidden_keywords or DEFAULT_FORBIDDEN_HOST_KEYWORDS
    if any(kw in target for kw in keywords):
        return target
    return None


_PLAIN_RESTART_RE = re.compile(r"docker restart ([A-Za-z0-9][A-Za-z0-9_.-]*)")


def is_routine_restart(command: str, host_id: str, batch_targets: set[tuple[str, str]]) -> bool:
    """Ist `command` ein schlichter Neustart eines Containers, der in DIESEM Batch auf
    GENAU diesem Host als Vorfall vorkam? Nur dann stuft der KI-Vorschlag sein Risiko auf
    MEDIUM herab -- sonst bleibt es HOCH. Streng: nach dem Trimmen exakt
    `docker restart <name>` (kleingeschrieben, ein Leerzeichen, keine Flags, keine weiteren
    Argumente, keine Shell-Zeichen), Name nach `^[A-Za-z0-9][A-Za-z0-9_.-]*$`.
    `batch_targets` sind (Host-ID, Containername)-Paare der Vorfaelle des Batches."""
    match = _PLAIN_RESTART_RE.fullmatch(command.strip())
    return match is not None and (host_id, match.group(1)) in batch_targets


def restart_risk_eligible(*, is_crash: bool, resumed: bool, attempts: int, fact: dict | None) -> bool:
    """Darf ein Neustart-Vorschlag fuer diesen Vorfall MITTEL statt HOCH sein? Streng, im
    Zweifel HOCH -- nur ein echter Absturz laut den Beendigungs-Fakten (`watcher.parse_exit_facts`):

    - Fakten muessen vorhanden sein (sonst HOCH), der Container darf inzwischen nicht wieder
      laufen (`running`: der Neustart waere unnoetig).
    - ExitCode 0 und 143 (sauber beendet / SIGTERM, also absichtlich gestoppt) -> HOCH.
    - ExitCode > 128 (Beendigung durch ein Signal, z. B. `docker kill`, `kill -s INT/HUP/...`):
      nur MITTEL, wenn OOMKilled true ist, sonst HOCH. Bewusst konservativ: auch 139 (SIGSEGV)
      ist ein echter Absturz, faellt aber unter diese Regel und bleibt HOCH.
    - Alles andere (z. B. 1, 2) -> MITTEL.

    Nie fuer Vorfaelle ohne Absturz (manueller Stopp, pausiert: `is_crash` False) und nie fuer
    nach einem Neustart wieder aufgenommene (`resumed`) oder wiederholte (`attempts`) Vorfaelle."""
    if not is_crash or resumed or attempts > 0 or fact is None:
        return False
    if fact.get("running"):
        return False
    code = fact.get("exit_code")
    if not isinstance(code, int) or code in (0, 143):
        return False
    if code > 128:
        return bool(fact.get("oom_killed"))
    return True


class Cause(NamedTuple):
    """Feste Einordnung eines Container-Vorfalls: kurze Bezeichnung + ein Satz."""

    label: str
    sentence: str

    @property
    def tag(self) -> str:
        """Nur der Kern der Bezeichnung ohne Klammerzusatz, z. B. "Speichermangel"."""
        return self.label.split(" (")[0]


NO_FACTS_CAUSE = Cause(
    "Ursache unklar (keine Beendigungs-Fakten)",
    "Die Beendigungs-Fakten (docker inspect) liegen nicht vor, die Ursache ist deshalb nicht gesichert.",
)


_CRASH_SIGNAL_CODES = frozenset({132, 134, 135, 136, 139})
"""128 + SIGILL/SIGABRT/SIGBUS/SIGFPE/SIGSEGV: Signale, die ein Prozess selbst ausloest."""


def classify_exit_cause(details: dict[str, Any], fact: dict[str, Any] | None) -> Cause:
    """Die feste, vom Code ermittelte Einordnung eines Vorfalls -- verbindlich, die KI darf sie
    nicht umdeuten (ein 7B-Modell hat exit 1 als "von aussen beendet" und bei OOMKilled=false einen
    Speichermangel beschrieben). Gleiche Grenzen wie `restart_risk_eligible`:

    - pausiert (Status/Zustand "paused") -> "pausiert", braucht keine Fakten,
    - ohne Fakten -> "Ursache unklar (keine Beendigungs-Fakten)",
    - OOMKilled=true -> "Speichermangel (OOMKilled)",
    - ExitCode 0/143 -> "manuell gestoppt",
    - ExitCode 132/134/135/136/139 (SIGILL/ABRT/BUS/FPE/SEGV) -> "echter Absturz (Signal …)",
    - ExitCode > 128 sonst (Signal, ohne OOM) -> "von außen beendet (Signal/Kill)",
    - alles andere (z. B. 1, 2) -> "echter Absturz (Exit-Code X)"."""
    status = str(details.get("status") or "").lower()
    if details.get("state") == "paused" or "(paused)" in status:
        return Cause("pausiert", "Der Container wurde pausiert (docker pause), er ist nicht abgestürzt.")
    if fact is None:
        return NO_FACTS_CAUSE
    code = fact.get("exit_code")
    if not isinstance(code, int):
        return NO_FACTS_CAUSE
    if fact.get("oom_killed"):
        return Cause(
            "Speichermangel (OOMKilled)",
            f"Der Container wurde beendet, weil der Speicher nicht reichte (OOMKilled=true, Exit-Code {code}).",
        )
    if code in (0, 143):
        return Cause(
            "manuell gestoppt",
            f"Der Container wurde sauber beendet (Exit-Code {code}), also absichtlich gestoppt.",
        )
    if code in _CRASH_SIGNAL_CODES:
        # SIGSEGV/SIGABRT/SIGBUS/SIGFPE/SIGILL kommen aus dem Prozess selbst -- ein Absturz, kein
        # Eingriff von aussen. Das Risiko bleibt trotzdem HOCH (restart_risk_eligible: >128 ohne OOM).
        return Cause(
            f"echter Absturz (Signal {code - 128}, Exit-Code {code})",
            f"Der Prozess im Container ist mit einem Fehler-Signal abgestürzt (Exit-Code {code} = "
            f"Signal {code - 128}, z. B. Speicherzugriffsfehler), nicht von außen beendet (OOMKilled=false).",
        )
    if code > 128:
        return Cause(
            "von außen beendet (Signal/Kill)",
            f"Der Container wurde durch ein Signal beendet (Exit-Code {code} = Signal {code - 128}, "
            "z. B. docker kill), nicht wegen Speichermangel (OOMKilled=false).",
        )
    return Cause(
        f"echter Absturz (Exit-Code {code})",
        f"Der Prozess im Container hat sich mit Fehlercode {code} selbst beendet (OOMKilled=false, kein Signal).",
    )


def cause_prompt_line(target: str, host_name: str, cause: Cause) -> str:
    """Der feste Satz fuer den KI-Prompt."""
    return (
        f"FESTSTEHENDE EINORDNUNG (vom System ermittelt, nicht ändern): "
        f"{target} auf {host_name}: {cause.label}. {cause.sentence}"
    )


def cause_notice(causes: dict[tuple[str, str], tuple[str, str, Cause]]) -> str:
    """Die eigene Meldungszeile(n) "Ursache laut System: ...", unabhaengig vom KI-Text.
    `causes`: (Host-ID, Container) -> (Container, Hostname, Einordnung). Bei genau einem
    Container ohne dessen Namen, sonst eine Zeile je Container."""
    if not causes:
        return ""
    items = list(causes.values())
    if len(items) == 1:
        return f"Ursache laut System: {items[0][2].label}"
    return "\n".join(f"Ursache laut System ({target} @ {host}): {cause.label}" for target, host, cause in items)


def cause_tag_for_command(command: str, host_id: str, causes: dict[tuple[str, str], tuple[str, str, Cause]]) -> str | None:
    """Kurz-Tag fuer die Begruendung eines Vorschlags: die Einordnung des Containers, den ein
    `docker restart <name>` trifft, sonst (anderer Befehl) die verschiedenen Tags des Batches."""
    if not causes:
        return None
    match = _DOCKER_RESTART_RE.search(command)
    if match is not None:
        entry = causes.get((host_id, match.group(1)))
        if entry is not None:
            return entry[2].tag
    tags = list(dict.fromkeys(entry[2].tag for entry in causes.values()))
    return " / ".join(tags)
