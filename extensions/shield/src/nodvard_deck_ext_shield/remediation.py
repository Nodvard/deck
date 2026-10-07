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
import shlex
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
_CONTAINER_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def is_routine_restart(command: str, host_id: str, batch_targets: set[tuple[str, str]]) -> bool:
    """Ist `command` ein schlichter Neustart eines Containers, der in DIESEM Batch auf
    GENAU diesem Host als Vorfall vorkam? Streng: nach dem Trimmen exakt
    `docker restart <name>` (kleingeschrieben, ein Leerzeichen, keine Flags, keine weiteren
    Argumente, keine Shell-Zeichen), Name nach `^[A-Za-z0-9][A-Za-z0-9_.-]*$`.
    `batch_targets` sind (Host-ID, Containername)-Paare der Vorfaelle des Batches."""
    match = _PLAIN_RESTART_RE.fullmatch(command.strip())
    return match is not None and (host_id, match.group(1)) in batch_targets


def plain_restart_name(command: str) -> str | None:
    """Der Containername, wenn `command` ein schlichter `docker restart <name>` ist (siehe
    `is_routine_restart`), sonst `None`."""
    match = _PLAIN_RESTART_RE.fullmatch(command.strip())
    return match.group(1) if match else None


def build_restart_command(container: str) -> str:
    """Der Neustart-Befehl, den der Code selbst baut -- nie ein Befehl aus KI-Text. Der Name wird
    geprueft (`ValueError` bei Sonderzeichen, Leerzeichen, Anfang mit `-` usw.) und zusaetzlich
    quotiert."""
    if not _CONTAINER_NAME_RE.fullmatch(container):
        raise ValueError(f"Ungültiger Containername: {container!r}")
    return f"docker restart {shlex.quote(container)}"


def resolve_restart_target(
    command: str, ai_host_name: str | None, restart_targets: set[tuple[str, str]], host_id_by_name: dict[str, str]
) -> tuple[str, str] | None:
    """(Host-ID, Containername) fuer einen KI-Vorschlag, oder `None`: dann wird nichts angelegt.

    Nur ein schlichter `docker restart <name>` kommt in Frage, und das Paar muss in
    `restart_targets` stehen (echte Abstuerze dieses Batches). Den Server bestimmt der Code aus
    diesem Paar. Steht derselbe Containername auf mehreren Servern des Batches, entscheidet der
    von der KI genannte Servername nur zwischen diesen -- einen anderen Server kann er nie
    ansprechen (`host_id_by_name`: Kleinbuchstaben-Servername -> Host-ID der Ziele)."""
    name = plain_restart_name(command)
    if name is None or not _CONTAINER_NAME_RE.fullmatch(name):
        return None  # was `build_restart_command` ablehnen wuerde, kommt gar nicht erst bis zum Vorschlag
    hosts = sorted(h for h, n in restart_targets if n == name)
    if len(hosts) > 1:
        wanted = host_id_by_name.get((ai_host_name or "").lower())
        hosts = [h for h in hosts if h == wanted]
    if len(hosts) != 1 or not is_routine_restart(command, hosts[0], restart_targets):
        return None
    return hosts[0], name


_CONTROL_CHARS_RE = re.compile(
    r"[\x00-\x08\x0b-\x1f\x7f-\x9f\xad\u2028\u2029\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff"
    r"\U000e0000-\U000e007f]"
)


def sanitize_untrusted(text: str, max_len: int = 2000, *, keep_tail: bool = False) -> str:
    """Fremden Text (Container-Logs, KI-Vorschlaege) fuer Prompt und Anzeige entschaerfen:
    Steuerzeichen (ausser Zeilenumbruch und Tab), unsichtbare Zeichen (Richtungszeichen, Breite null,
    Unicode-Tag-Zeichen, mit denen sich unsichtbarer Text an das Modell schmuggeln laesst) raus, Laenge
    begrenzen. Ueberlanger Text behaelt standardmaessig den Anfang (Marke `[gekürzt]` dahinter); mit
    `keep_tail=True` das Ende (Marke davor) -- richtig fuer Logs, bei denen die neuesten Zeilen unten stehen."""
    cleaned = _CONTROL_CHARS_RE.sub("", (text or "").replace("\r\n", "\n").replace("\r", "\n"))
    if len(cleaned) > max_len:
        if keep_tail:
            cleaned = "[gekürzt]\n" + cleaned[len(cleaned) - max_len :]
        else:
            cleaned = cleaned[:max_len] + " [gekürzt]"
    return cleaned


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
    `docker restart <name>` auf diesem Host trifft. Fuer jeden anderen Befehl `None` -- die
    System-Einordnung darf nie an einem fremden Befehl haengen."""
    name = plain_restart_name(command)
    if name is None:
        return None
    entry = causes.get((host_id, name))
    return entry[2].tag if entry is not None else None


# ---------------------------------------------------------------------------
# Feste Fakten im Lagebericht und Abgleich mit dem Modelltext
# ---------------------------------------------------------------------------


def format_finished_at(value: Any) -> str:
    """"2026-09-30T10:00:01.123456789Z" -> "2026-09-30 10:00:01 UTC"; Unlesbares bleibt leer."""
    match = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})", str(value or ""))
    return f"{match.group(1)} {match.group(2)} UTC" if match else ""


def facts_line(fact: dict[str, Any]) -> str:
    """Die Fakten aus `docker inspect` als feste Zeile: Exit-Code, OOMKilled, Neustarts, Image,
    Zeitpunkt. Vom Code geschrieben, nie vom Modell."""
    parts = [f"Exit-Code {fact.get('exit_code')}", f"OOMKilled={str(bool(fact.get('oom_killed'))).lower()}"]
    restarts = fact.get("restart_count")
    if isinstance(restarts, int) and not isinstance(restarts, bool):
        parts.append(f"{restarts} Neustart{'' if restarts == 1 else 's'}")
    if fact.get("image"):
        parts.append(f"Image {fact['image']}")
    finished = format_finished_at(fact.get("finished_at"))
    if finished:
        parts.append(f"beendet {finished}")
    return " · ".join(parts)


def facts_notice(
    causes: dict[tuple[str, str], tuple[str, str, Cause]], fact_map: dict[tuple[str, str], dict[str, Any]]
) -> str:
    """Feste Zeile(n) "Fakten (docker inspect): ..." fuer alle Container des Batches mit Fakten.
    Bei genau einem Container ohne dessen Namen, sonst eine Zeile je Container."""
    entries = [(target, host, fact_map[key]) for key, (target, host, _cause) in causes.items() if key in fact_map]
    if not entries:
        return ""
    if len(entries) == 1:
        return f"Fakten (docker inspect): {facts_line(entries[0][2])}"
    return "\n".join(f"Fakten zu {target} @ {host}: {facts_line(fact)}" for target, host, fact in entries)


_OOM_CLAIM_RE = re.compile(r"oomkilled\s*(?:=|:|ist|is)?\s*[\"']?(true|false)\b", re.IGNORECASE)
_EXIT_CLAIM_RE = re.compile(r"(?:exit[\s_-]*code|exitcode|exit)\s*[=:]?\s*(-?\d+)", re.IGNORECASE)
_MEMORY_WORDS_RE = re.compile(
    r"speichermangel|out of memory|arbeitsspeicher|\boom\b|nicht genug speicher|speicher\s+(?:war\s+)?(?:voll|knapp|erschöpft)",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(r"\b(?:kein|keine|keinen|nicht|ohne|not|no|without)\b", re.IGNORECASE)
_EXTERNAL_RE = re.compile(
    r"von au(?:ss|ß|s)en (?:beendet|gestoppt|gekillt)|extern beendet|docker (?:kill|stop)|manuell gestoppt", re.IGNORECASE
)
_LABEL_PREFIX_RE = re.compile(r"^(\s*Lagebericht\s*:\s*)(.*)$", re.IGNORECASE)
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")


def _contradicts(sentence: str, facts: list[dict[str, Any]], *, complete: bool, tags: set[str]) -> bool:
    claims = {m.lower() for m in _OOM_CLAIM_RE.findall(sentence)}
    oom_values = {str(bool(f.get("oom_killed"))).lower() for f in facts}
    if len(claims) == 2 and len(oom_values) < 2:
        return True  # "OOMKilled=true ... da OOMKilled=false": widerspricht sich selbst
    if not (facts and complete):
        return False  # ohne vollstaendige Fakten laesst sich nichts abgleichen
    if len(oom_values) == 1 and claims and not claims <= oom_values:
        return True
    codes = {f.get("exit_code") for f in facts}
    if any(int(c) not in codes for c in _EXIT_CLAIM_RE.findall(sentence)):
        return True
    without_claims = _OOM_CLAIM_RE.sub("", sentence)
    if oom_values == {"false"} and _MEMORY_WORDS_RE.search(without_claims) and not _NEGATION_RE.search(sentence):
        return True
    if oom_values == {"true"} and re.search(r"kein(?:en)?\s+(?:speichermangel|oom)", sentence, re.IGNORECASE):
        return True
    return tags == {"echter Absturz"} and bool(_EXTERNAL_RE.search(sentence)) and not _NEGATION_RE.search(sentence)


def drop_contradictions(
    text: str, facts: list[dict[str, Any]], *, expected: int, tags: set[str]
) -> tuple[str, int]:
    """Entfernt aus dem Modelltext die Saetze, die den Fakten aus `docker inspect` widersprechen
    (falscher OOMKilled-Wert, falscher Exit-Code, Speichermangel trotz OOMKilled=false, "von aussen
    beendet" bei einem echten Absturz) oder sich selbst widersprechen. Gibt den bereinigten Text und
    die Zahl der entfernten Saetze zurueck. `expected`: Zahl der Container im Batch; liegen nicht
    fuer alle Fakten vor, wird nur auf Selbstwiderspruch geprueft."""
    complete = bool(facts) and len(facts) >= expected
    dropped = 0
    lines_out: list[str] = []
    for line in (text or "").splitlines():
        match = _LABEL_PREFIX_RE.match(line)
        prefix, body = (match.group(1), match.group(2)) if match else ("", line)
        kept: list[str] = []
        for sentence in (s for s in _SENTENCE_END_RE.split(body) if s.strip()):
            if _contradicts(sentence, facts, complete=complete, tags=tags):
                dropped += 1
            else:
                kept.append(sentence.strip())
        if kept:
            lines_out.append(prefix + " ".join(kept) if match else " ".join(kept))
        elif not body.strip():
            lines_out.append(line)  # Leerzeilen bleiben
    return "\n".join(lines_out).strip(), dropped


def contradiction_notice(dropped: int) -> str:
    if dropped <= 0:
        return ""
    if dropped == 1:
        return "Hinweis: Eine Aussage von Nodvard KI widersprach den Fakten oben und wurde entfernt."
    return f"Hinweis: {dropped} Aussagen von Nodvard KI widersprachen den Fakten oben und wurden entfernt."
