r"""Die Sperrliste des Gates.

**Herkunft: wörtlich übernommen aus dem Vorgängersystem.**

Das sind Daten, keine Struktur — jede Umformulierung riskiert, einen bereits bezahlten
Bug erneut einzubauen. Der auffälligste Fall: `rm -rf /` wurde in einer früheren Fassung
NICHT geblockt, weil das Muster ein Zeichen nach dem Schrägstrich verlangte (`\s`), der
Befehl aber direkt danach endete. Gefunden wurde das nur durch einen echten
Funktionstest, nicht durch Lesen. Deshalb hier unverändert: `(\s|$)`.

Philosophie (ausdrückliche Vorgabe des Betreibers): **schmale Sperrliste statt
Fähigkeits-Whitelist.** Die KI soll alles können; Sicherheit entsteht über
Nachvollziehbarkeit, nicht über Einschränkung. Bewusst NICHT gesperrt sind deshalb
reboot, shutdown und Service-Neustarts.

Diese Liste ist der Default-Seed für die Einstellung `security.deny_patterns`.
Extensions dürfen ergänzen, niemand darf sie leeren.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DenyPattern:
    id: str
    pattern: re.Pattern[str]
    description: str
    origin: str = "ported"      # "ported" = 1:1 aus dem Vorgängersystem | "added" = neu


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# ---------------------------------------------------------------------------
# 1:1 aus dem Vorgängersystem — Reihenfolge und Regex-Text unverändert.
# Beim Ändern hier: erst den zugehörigen Regressionstest anfassen, nie umgekehrt.
# ---------------------------------------------------------------------------
PORTED_DENY_PATTERNS: tuple[DenyPattern, ...] = (
    DenyPattern("rm-rf-root", _p(r"rm\s+-rf\s+/(\s|$)"),
                "Löschen des Wurzelverzeichnisses"),
    DenyPattern("rm-rf-root-glob", _p(r"rm\s+-rf\s+/\*"),
                "Löschen aller Einträge unter /"),
    DenyPattern("rm-rf-system", _p(r"rm\s+-rf\s+/(bin|boot|etc|home|lib|opt|root|sbin|srv|usr|var)(\s|/?$)"),
                "Löschen eines Systemverzeichnisses"),
    DenyPattern("mkfs", _p(r"\bmkfs(\.\w+)?\b"),
                "Dateisystem neu anlegen"),
    DenyPattern("dd-to-device", _p(r"\bdd\s+[^\n]*of=/dev/(?!null)"),
                "Direktes Überschreiben eines Blockgeräts (/dev/null ausgenommen)"),
    DenyPattern("fork-bomb", _p(r":\(\)\s*\{[^}]*:\|:[^}]*\}\s*;\s*:"),
                "Fork-Bomb"),
    DenyPattern("redirect-to-disk", _p(r">\s*/dev/sd[a-z]\d*\b"),
                "Umleitung direkt auf eine Festplatte"),
    DenyPattern("mkswap", _p(r"\bmkswap\b"),
                "Swap-Signatur schreiben"),
    DenyPattern("userdel-root", _p(r"\buserdel\s+root\b"),
                "Root-Benutzer löschen"),
    DenyPattern("iptables-flush", _p(r"iptables\s+-F\b"),
                "Alle Firewall-Regeln verwerfen"),
    DenyPattern("ufw-disable", _p(r"ufw\s+disable\b"),
                "Firewall abschalten"),
    DenyPattern("kill-ssh-access", _p(r"systemctl\s+(disable|stop|mask)\s+(ssh|sshd)\b"),
                "SSH-Zugang kappen"),
    DenyPattern("chmod-777-root", _p(r"chmod\s+-R\s+777\s+/\s*$"),
                "Rechte auf / vollständig öffnen"),
    DenyPattern("curl-pipe-shell", _p(r"curl[^|;]*\|\s*(sudo\s+)?(ba)?sh\b"),
                "Skript aus dem Netz direkt in eine Shell pipen"),
    DenyPattern("wget-pipe-shell", _p(r"wget[^|;]*\|\s*(sudo\s+)?(ba)?sh\b"),
                "Skript aus dem Netz direkt in eine Shell pipen"),
)

# ---------------------------------------------------------------------------
# Ergänzungen, die im Bestand fehlen. Bewusst getrennt gehalten, damit beim Vergleich
# von altem und neuem Verhalten (Schattenbetrieb) jede Abweichung
# eindeutig zuordenbar bleibt: trifft ein "added"-Muster, ist das kein Portierungsfehler.
# ---------------------------------------------------------------------------
ADDED_DENY_PATTERNS: tuple[DenyPattern, ...] = (
    DenyPattern("wipefs", _p(r"\bwipefs\b"),
                "Dateisystem-Signaturen löschen", origin="added"),
    DenyPattern("zpool-destroy", _p(r"\bzpool\s+destroy\b"),
                "ZFS-Pool zerstören", origin="added"),
    DenyPattern("lvm-force-remove", _p(r"\b(lvremove|vgremove|pvremove)\b[^\n]*-[a-zA-Z]*f"),
                "LVM-Struktur erzwungen entfernen (local-lvm hält alle VM-Disks)", origin="added"),
    DenyPattern("authorized-keys-truncate", _p(r"(?<!>)>\s*\S*authorized_keys\b"),
                "authorized_keys überschreiben statt anhängen (>> bleibt erlaubt)", origin="added"),
)

DEFAULT_DENY_PATTERNS: tuple[DenyPattern, ...] = (
    *PORTED_DENY_PATTERNS,
    *ADDED_DENY_PATTERNS,
)

# Bewusst NICHT gesperrt — dokumentiert, damit niemand sie "aus Versehen" ergänzt:
#   reboot, shutdown, systemctl restart, docker restart, docker stop,
#   pct stop, qm stop, apt upgrade
# Begründung: die Betreibervorgabe lautet ausdrücklich, dass die KI handlungsfähig
# bleiben muss. Diese Befehle sind reversibel und gehören zum Alltagsgeschäft der
# Remediation. Der Schutz dagegen ist das Anti-Flapping, nicht ein Verbot.
#
# Ebenfalls bewusst NICHT hier: die Vendor-spezifische Erweiterung von "kill-ssh-access"
# um einen konkreten Access-Gateway-Prozessnamen. Der generische, verkaufbare Kern
# schuetzt universell vor Selbstaussperrung (ssh/sshd) -- ein Betreiber, der zusaetzlich
# einen bestimmten dritten Zugangsdienst schuetzen will, ergaenzt das ueber
# `security.deny_patterns` (docs/03-DATA-MODEL.md §9: "Extensions duerfen ergaenzen"),
# nicht ueber einen hartcodierten Produktnamen im Kern.


# ---------------------------------------------------------------------------
# Was hier bewusst NICHT steht: die "Hostname-als-Containername"-Pruefung des Vorgaengersystems
# (FORBIDDEN_KEYWORDS -- fest verdrahtete Flotten-Hostnamen). Sie faengt den real
# beobachteten Fehlschluss "Host offline -> docker restart <hostname>_server", ist aber
# an konkrete Hostnamen EINER Installation gebunden -- keine generische Kern-Regel.
# Sie gehoert in die nexus-soc-Extension,
# nicht in den Kern. Eine erste Fassung stand faelschlich in diesem Modul --
# scripts/check_core_purity.py hat den Platzierungsfehler aufgedeckt, bevor er sich
# festsetzen konnte.


def match_deny_patterns(
    command: str, extra: tuple[DenyPattern, ...] = ()
) -> DenyPattern | None:
    """Erstes greifendes Muster oder None."""
    for dp in (*DEFAULT_DENY_PATTERNS, *extra):
        if dp.pattern.search(command):
            return dp
    return None
