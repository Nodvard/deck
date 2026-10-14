"""Schutz vor dem Fehlschluss „Hostname = Containername“: die KI schlägt `docker restart <Host>` vor.

Nicht im Kern: Die Wortliste hängt von den Servernamen der Installation ab (siehe `scripts/check_core_purity.py`)."""

from __future__ import annotations

import re
import shlex
from typing import Any, NamedTuple

from .incident_text import cause_texts

DEFAULT_FORBIDDEN_HOST_KEYWORDS = (
    "pve", "proxmox", "host", "server",
    "node", "router", "gateway", "nas",
)
"""Startwert, kein Zwang: jede Installation ergänzt die Liste über `forbidden_host_keywords` (settings.schema.json) um ihre Servernamen."""

_DOCKER_RESTART_RE = re.compile(r"\bdocker\s+restart\s+([A-Za-z0-9_.-]+)", re.IGNORECASE)


def looks_like_host_not_container(command: str, *, forbidden_keywords: tuple[str, ...] | None = None) -> str | None:
    """Prüft, ob der Name in `docker restart <name>` wie ein Host-Name aussieht; andere Befehle haben keinen Containernamen.

    Gibt den Namen bei einem verbotenen Schlüsselwort zurück, sonst `None` (Teilstring: „pve1“ trifft „pve“, gewollt)."""
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
    """Ist `command` ein schlichter Neustart eines Containers, der in diesem Batch auf genau diesem Host als Vorfall vorkam?

    Streng: nur exakt `docker restart <name>`, ohne Flags und Shell-Zeichen. `batch_targets`: (Host-ID, Name)-Paare."""
    match = _PLAIN_RESTART_RE.fullmatch(command.strip())
    return match is not None and (host_id, match.group(1)) in batch_targets


def plain_restart_name(command: str) -> str | None:
    """Der Containername, wenn `command` ein schlichter `docker restart <name>` ist (`is_routine_restart`), sonst `None`."""
    match = _PLAIN_RESTART_RE.fullmatch(command.strip())
    return match.group(1) if match else None


def build_restart_command(container: str) -> str:
    """Der Neustart-Befehl, den der Code selbst baut, nie ein Befehl aus KI-Text.

    Der Name wird geprüft (`ValueError` bei Sonderzeichen, Leerzeichen, Anfang mit `-`) und zusätzlich quotiert."""
    if not _CONTAINER_NAME_RE.fullmatch(container):
        raise ValueError(f"Ungültiger Containername: {container!r}")
    return f"docker restart {shlex.quote(container)}"


def resolve_restart_target(
    command: str, ai_host_name: str | None, restart_targets: set[tuple[str, str]], host_id_by_name: dict[str, str]
) -> tuple[str, str] | None:
    """(Host-ID, Containername) für einen KI-Vorschlag, oder `None`: dann wird nichts angelegt.

    Nur ein schlichter `docker restart <name>`, dessen Paar in `restart_targets` (echte Abstürze des Batches) steht."""
    name = plain_restart_name(command)
    if name is None or not _CONTAINER_NAME_RE.fullmatch(name):
        return None  # was `build_restart_command` ablehnen würde, kommt gar nicht erst bis zum Vorschlag
    # Den Server bestimmt der Code aus dem Paar. Steht der Name auf mehreren Servern, wählt der von der KI genannte
    # Servername nur unter diesen, nie einen anderen (`host_id_by_name`: Kleinbuchstaben-Servername -> Host-ID).
    hosts = sorted(h for h, n in restart_targets if n == name)
    if len(hosts) > 1:
        wanted = host_id_by_name.get((ai_host_name or "").lower())
        hosts = [h for h in hosts if h == wanted]
    if len(hosts) != 1 or not is_routine_restart(command, hosts[0], restart_targets):
        return None
    return hosts[0], name


# Unsichtbare Zeichen (Richtung, Breite null, Unicode-Tags) schmuggeln sonst Text ans Modell; Umbruch und Tab bleiben.
_CONTROL_CHARS_RE = re.compile(
    r"[\x00-\x08\x0b-\x1f\x7f-\x9f\xad\u2028\u2029\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff"
    r"\U000e0000-\U000e007f]"
)


def sanitize_untrusted(text: str, max_len: int = 2000, *, keep_tail: bool = False) -> str:
    """Fremden Text (Container-Logs, KI-Vorschläge) für Prompt und Anzeige entschärfen: Steuerzeichen raus, Länge begrenzen.

    Überlanger Text behält den Anfang (dahinter `[gekürzt]`), mit `keep_tail=True` das Ende (davor), richtig für Logs."""
    cleaned = _CONTROL_CHARS_RE.sub("", (text or "").replace("\r\n", "\n").replace("\r", "\n"))
    if len(cleaned) > max_len:
        if keep_tail:
            cleaned = "[gekürzt]\n" + cleaned[len(cleaned) - max_len :]
        else:
            cleaned = cleaned[:max_len] + " [gekürzt]"
    return cleaned


def restart_risk_eligible(*, is_crash: bool, resumed: bool, attempts: int, fact: dict | None) -> bool:
    """Darf ein Neustart-Vorschlag für diesen Vorfall MITTEL statt HOCH sein? Streng, im Zweifel HOCH.

    Nur ein echter Absturz laut `watcher.parse_exit_facts`; nie bei manuellem Stopp, Pause, Wiederaufnahme, Wiederholung."""
    if not is_crash or resumed or attempts > 0 or fact is None:
        return False
    if fact.get("running"):  # läuft inzwischen wieder: der Neustart wäre unnötig
        return False
    code = fact.get("exit_code")
    if not isinstance(code, int) or code in (0, 143):  # sauber beendet bzw. SIGTERM, also absichtlich gestoppt
        return False
    if _is_signal(code):
        # Beendigung durch ein Signal (z. B. `docker kill`): nur bei OOMKilled MITTEL. Bewusst konservativ: auch 139
        # (SIGSEGV) ist ein echter Absturz, fällt aber unter diese Regel und bleibt HOCH.
        return bool(fact.get("oom_killed"))
    return True  # alles andere (z. B. 1, 2, auch 255 = exit(-1)) ist ein Absturz


class Cause(NamedTuple):
    """Feste Einordnung eines Vorfalls: Bezeichnung und ein Satz; `key` ist die Art (`incident_text.CAUSE_TEXTS`)."""

    label: str
    sentence: str
    key: str = ""

    @property
    def tag(self) -> str:
        """Nur der Kern der Bezeichnung ohne Klammerzusatz, z. B. "Speichermangel"."""
        return self.label.split(" (")[0]


NO_FACTS_CAUSE = Cause(
    "Ursache unklar (keine Beendigungs-Fakten)",
    "Die Beendigungs-Fakten (docker inspect) liegen nicht vor, die Ursache ist deshalb nicht gesichert.",
    "unknown",
)


_CRASH_SIGNAL_CODES = frozenset({132, 134, 135, 136, 139})
"""128 + SIGILL/SIGABRT/SIGBUS/SIGFPE/SIGSEGV: Signale, die ein Prozess selbst auslöst."""


def _is_signal(code: int) -> bool:
    """Exit-Code 128 + Signal; Linux kennt höchstens 64 Signale, 193 bis 255 sind normale Fehler (255 bei `exit(-1)`)."""
    return 128 < code <= 192


LOOP_CAUSE = Cause(
    "Absturzschleife",
    "Der Container beendet sich immer wieder, und Docker startet ihn jedes Mal neu (Neustart-Regel).",
    "loop",
)


def classify_exit_cause(details: dict[str, Any], fact: dict[str, Any] | None) -> Cause:
    """Die feste, vom Code ermittelte Einordnung eines Vorfalls; verbindlich, die KI darf sie nicht umdeuten.

    Gleiche Grenzen wie `restart_risk_eligible`; „pausiert“ braucht keine Fakten, ohne Fakten ist die Ursache unklar."""
    # Kleine Modelle deuten sonst Exit-Code 1 als „von außen beendet“ oder sehen bei OOMKilled=false Speichermangel.
    status = str(details.get("status") or "").lower()
    if details.get("state") == "paused" or "(paused)" in status:
        return Cause("pausiert", "Der Container wurde pausiert (docker pause), er ist nicht abgestürzt.", "paused")
    if details.get("restarts_since_last_check") or (fact is not None and fact.get("running") and fact.get("exit_code") == 0):
        # Absturzschleife: Docker startet den Container immer wieder. Läuft er beim Nachsehen gerade, steht der
        # Exit-Code auf 0, das ist kein „sauber gestoppt“.
        return LOOP_CAUSE
    if fact is None:
        return NO_FACTS_CAUSE
    code = fact.get("exit_code")
    if not isinstance(code, int):
        return NO_FACTS_CAUSE
    if fact.get("oom_killed"):
        return Cause(
            "Speichermangel (OOMKilled)",
            f"Der Container wurde beendet, weil der Speicher nicht reichte (OOMKilled=true, Exit-Code {code}).",
            "oom",
        )
    if code in (0, 143):
        return Cause(
            "manuell gestoppt",
            f"Der Container wurde sauber beendet (Exit-Code {code}), also absichtlich gestoppt.",
            "stopped",
        )
    if code in _CRASH_SIGNAL_CODES:
        # SIGSEGV/SIGABRT/SIGBUS/SIGFPE/SIGILL kommen aus dem Prozess selbst: ein Absturz, kein Eingriff von außen.
        # Das Risiko bleibt trotzdem HOCH (restart_risk_eligible: >128 ohne OOM).
        return Cause(
            f"echter Absturz (Signal {code - 128}, Exit-Code {code})",
            f"Der Prozess im Container ist mit einem Fehler-Signal abgestürzt (Exit-Code {code} = "
            f"Signal {code - 128}, z. B. Speicherzugriffsfehler), nicht von außen beendet (OOMKilled=false).",
            "crash_signal",
        )
    if _is_signal(code):
        return Cause(
            "von außen beendet (Signal/Kill)",
            f"Der Container wurde durch ein Signal beendet (Exit-Code {code} = Signal {code - 128}, "
            "z. B. docker kill), nicht wegen Speichermangel (OOMKilled=false).",
            "killed",
        )
    return Cause(
        f"echter Absturz (Exit-Code {code})",
        f"Der Prozess im Container hat sich mit Fehlercode {code} selbst beendet (OOMKilled=false, kein Signal).",
        "crash",
    )


def cause_prompt_line(target: str, host_name: str, cause: Cause) -> str:
    """Der feste Satz für den KI-Prompt."""
    return (
        f"FESTSTEHENDE EINORDNUNG (vom System ermittelt, nicht ändern): "
        f"{target} auf {host_name}: {cause.label}. {cause.sentence}"
    )


def cause_notice(causes: dict[tuple[str, str], tuple[str, str, Cause]]) -> str:
    """Die eigene Meldungszeile „Ursache laut System: ...“, unabhängig vom KI-Text; je Container eine Zeile.

    `causes`: (Host-ID, Container) -> (Container, Hostname, Einordnung). Bei genau einem Container ohne dessen Namen."""
    if not causes:
        return ""
    items = list(causes.values())
    if len(items) == 1:
        return f"Ursache laut System: {items[0][2].label}"
    return "\n".join(f"Ursache laut System ({target} @ {host}): {cause.label}" for target, host, cause in items)


def cause_tag_for_command(command: str, host_id: str, causes: dict[tuple[str, str], tuple[str, str, Cause]]) -> str | None:
    """Kurz-Tag für die Begründung eines Vorschlags: die Einordnung des Containers, den `docker restart <name>` trifft.

    Für jeden anderen Befehl `None`, die Einordnung darf nie an einem fremden Befehl hängen."""
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
    """Die Fakten aus `docker inspect` als feste Zeile vom Code (Exit-Code, OOMKilled, Neustarts, Image, Zeitpunkt)."""
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


def cause_advice(causes: dict[tuple[str, str], tuple[str, str, Cause]]) -> str:
    """Die festen Sätze „Was heißt das?“ und „Was kannst du tun?“ zu den Einordnungen (`incident_text.CAUSE_TEXTS`).

    Bei genau einer Art ohne Bezeichnung davor, sonst mit ihr; leer, wenn keine Art Texte hat."""
    kinds: dict[str, tuple[Cause, list[str]]] = {}
    for target, _host, cause in causes.values():
        if cause.key and cause_texts(cause.key):
            names = kinds.setdefault(cause.key, (cause, []))[1]
            if target not in names:
                names.append(target)
    if len(kinds) == 1:
        text = cause_texts(next(iter(kinds)))
        assert text is not None
        return f"Was heißt das? {text['what']}\nWas kannst du tun? {text['todo']}"
    lines = []
    for key, (cause, names) in kinds.items():
        text = cause_texts(key)
        assert text is not None
        lines.append(f"{cause.tag} ({', '.join(names)}): {text['what']} Was kannst du tun? {text['todo']}")
    return "\n".join(lines)


def facts_notice(
    causes: dict[tuple[str, str], tuple[str, str, Cause]], fact_map: dict[tuple[str, str], dict[str, Any]]
) -> str:
    """Feste Zeile(n) „Fakten (docker inspect): ...“ für alle Container im Batch mit Fakten; bei genau einem ohne Namen."""
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
        return False  # ohne vollständige Fakten lässt sich nichts abgleichen
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
    """Entfernt aus dem Modelltext die Sätze, die den Fakten aus `docker inspect` oder sich selbst widersprechen.

    Gibt Text und Zahl entfernter Sätze zurück; `expected`: Container im Batch (fehlen Fakten: nur Selbstwiderspruch)."""
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
