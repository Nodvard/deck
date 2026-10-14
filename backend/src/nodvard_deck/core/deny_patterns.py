"""Die Sperrliste des Gates (Standard von `security.deny_patterns`); sie darf ergänzt, aber nicht geleert werden."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DenyPattern:
    id: str
    pattern: re.Pattern[str]
    description: str
    origin: str = "ported"      # "ported" = Grundbestand | "added" = Ergänzung


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# Schmale Sperrliste statt Fähigkeits-Whitelist: Die KI soll handlungsfähig bleiben, Sicherheit kommt aus
# Nachvollziehbarkeit (Protokoll, Freigabe) und nicht aus Einschränkung. Die Muster sind eng gefasst und getestet; beim
# Ändern erst den Regressionstest anfassen.
PORTED_DENY_PATTERNS: tuple[DenyPattern, ...] = (
    # `(\s|$)`: Auch `rm -rf /` am Ende des Befehls muss greifen, ein Pflichtzeichen nach dem Schrägstrich ließ es
    # durch.
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

# Ergänzungen zum Grundbestand, bewusst getrennt gehalten.
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

# Bewusst nicht gesperrt: reboot, shutdown, systemctl restart, docker restart/stop, pct stop, qm stop, apt upgrade.
# Sie sind reversibel und gehören zur Remediation; der Schutz dagegen ist das Anti-Flapping, nicht ein Verbot.
#
# Ebenfalls nicht hier: der Name eines bestimmten Zugangsdienstes in "kill-ssh-access". Wer einen weiteren Dienst
# schützen will, ergänzt das über `security.deny_patterns`.


# Ebenfalls nicht hier: die Prüfung auf feste Hostnamen einer Installation (Fehlschluss "Host offline -> restart
# <hostname>_server"). Sie ist keine generische Regel und gehört in eine Erweiterung.


def match_deny_patterns(
    command: str, extra: tuple[DenyPattern, ...] = ()
) -> DenyPattern | None:
    """Erstes greifendes Muster oder None."""
    for dp in (*DEFAULT_DENY_PATTERNS, *extra):
        if dp.pattern.search(command):
            return dp
    return None
