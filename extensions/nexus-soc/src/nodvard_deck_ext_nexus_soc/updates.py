"""Update-Zentrale (Nachfolger von `run_system_updates_check` im Vorgaengersystem): anstehende
Paket-Updates je Server, Sicherheitsupdates, Neustart-Bedarf -- und die Befehle, um
sie einzuspielen.

Nur die REINEN Teile: Befehle bauen und Ausgaben auswerten. Ausfuehren, Speichern
und Melden steht in `patching.py`.

Unterschiede zum Original, bewusst:
- Das Original hat bei jedem gefundenen Update sofort `apt-get upgrade -y` gestartet.
  Hier ist Pruefen und Einspielen getrennt; automatisch eingespielt wird nur, wenn
  das in den Einstellungen eingeschaltet ist (Standard: aus).
- `upgrade --with-new-pkgs` statt `upgrade`: zieht neue Abhaengigkeiten (z. B. einen
  neuen Kernel) mit, entfernt aber nie Pakete. Nur wenn es in den Einstellungen
  eingeschaltet ist (`proxmox_dist_upgrade`, Standard: aus), nimmt eine von
  Hand ausgeloeste "Alle Updates"-Aktion auf Proxmox `dist-upgrade` (Proxmox verlangt
  das; es darf Pakete ersetzen oder entfernen). Automatische Laeufe nehmen nie
  dist-upgrade -- sie haben niemanden, der vorher auf den Befehl schaut.
- Neustart-Bedarf wird auch ohne `/var/run/reboot-required` erkannt (Debian/Proxmox
  legen die Datei nicht an): laufender Kernel != neuester installierter Kernel
  derselben Art (auf dem Pi sind z. B. rpi-v8 und rpi-2712 nebeneinander installiert).
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Any

from .antivirus import as_root
from .detached import KEPT_BACK_MARK

_SECURITY_RE = re.compile(r"security", re.I)
_PKG_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._:~-]*$")


def status_command(*, refresh: bool) -> str:
    """Liest den Update-Stand. Mit `refresh` werden vorher die Paketlisten neu geladen
    (braucht root -- ohne root bleibt es beim zuletzt geladenen Stand)."""
    refresh_part = ""
    if refresh:
        inner = (
            "if command -v apt-get >/dev/null 2>&1; then apt-get update -qq 2>&1 | grep -E '^(E|Err|W):' | head -n 20; "
            "elif command -v dnf >/dev/null 2>&1; then dnf -q makecache 2>&1 | tail -n 5; "
            "elif command -v apk >/dev/null 2>&1; then apk update -q 2>&1 | tail -n 5; fi"
        )
        refresh_part = f"echo @@refresh; {as_root(inner, required=False)}; "
    return (
        "echo @@pm; if command -v apt-get >/dev/null 2>&1; then echo apt; "
        "elif command -v dnf >/dev/null 2>&1; then echo dnf; elif command -v apk >/dev/null 2>&1; then echo apk; else echo none; fi; "
        + refresh_part
        + "echo @@list; if command -v apt-get >/dev/null 2>&1; then apt list --upgradable 2>/dev/null | grep -v '^Listing'; "
        "elif command -v dnf >/dev/null 2>&1; then dnf -q check-update 2>/dev/null; "
        "elif command -v apk >/dev/null 2>&1; then apk version -l '<' 2>/dev/null | tail -n +2; fi; "
        "echo @@security; if command -v dnf >/dev/null 2>&1; then dnf -q updateinfo list --security 2>/dev/null; fi; "
        "echo @@reboot; if [ -f /var/run/reboot-required ]; then echo yes; cat /var/run/reboot-required.pkgs 2>/dev/null; "
        "elif command -v needs-restarting >/dev/null 2>&1 && ! needs-restarting -r >/dev/null 2>&1; then echo yes; else echo no; fi; "
        "echo @@kernel; uname -r; "
        "echo @@kernels; ls -1 /boot/vmlinuz-* 2>/dev/null | sed 's#.*/vmlinuz-##'; "
        "echo @@uptime; cut -d' ' -f1 /proc/uptime; "
        "echo @@unattended; if command -v apt-get >/dev/null 2>&1; then "
        "if dpkg -s unattended-upgrades >/dev/null 2>&1; then echo yes; else echo no; fi; else echo n/a; fi; "
        "echo @@end"
    )


@dataclass
class Package:
    name: str
    new_version: str
    current_version: str | None = None
    repo: str | None = None
    security: bool = False

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "new_version": self.new_version, "current_version": self.current_version,
                "repo": self.repo, "security": self.security}


@dataclass
class UpdateStatus:
    manager: str | None
    packages: list[Package] = field(default_factory=list)
    reboot_required: bool = False
    reboot_reasons: list[str] = field(default_factory=list)
    kernel: str | None = None
    latest_kernel: str | None = None
    uptime_s: float | None = None
    unattended: bool | None = None
    refresh_error: str | None = None
    error: str | None = None
    unsupported: bool = False
    """Der Server hat keinen Paketmanager, den die Update-Zentrale kennt (z. B. ein NAS mit eigener Firmware).
    Das ist kein Fehler: er steht als „nicht unterstützt“ da und löst weder Warnungen noch Fehlermeldungen aus."""

    @property
    def security_count(self) -> int:
        return sum(1 for p in self.packages if p.security)

    def as_dict(self) -> dict[str, object]:
        return {
            "manager": self.manager, "packages": [p.as_dict() for p in self.packages], "count": len(self.packages),
            "security_count": self.security_count, "reboot_required": self.reboot_required,
            "reboot_reasons": self.reboot_reasons, "kernel": self.kernel, "latest_kernel": self.latest_kernel,
            "uptime_s": self.uptime_s, "unattended": self.unattended, "refresh_error": self.refresh_error, "error": self.error,
            "unsupported": self.unsupported,
        }


def _sections(output: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in output.splitlines():
        line = raw.rstrip()
        if re.fullmatch(r"@@[a-z]+", line.strip()):
            current = line.strip()[2:]
            sections[current] = []
        elif current is not None and line.strip():
            sections[current].append(line.strip())
    return sections


# "openssl/stable-security 3.0.14-1~deb12u2 amd64 [upgradable from: 3.0.13-1~deb12u1]"
_APT_RE = re.compile(r"^(?P<name>[^/\s]+)/(?P<repo>\S+)\s+(?P<new>\S+)\s+\S+(?:\s+\[upgradable from: (?P<old>[^\]]+)\])?")
# "kernel-core.x86_64   6.8.9-300.fc40   updates"
_DNF_RE = re.compile(r"^(?P<name>[^\s]+)\.(?P<arch>[A-Za-z0-9_]+)\s+(?P<new>\S+)\s+(?P<repo>\S+)$")
# "busybox-1.36.1-r15 < 1.36.1-r16"
_APK_RE = re.compile(r"^(?P<full>\S+)\s+<\s+(?P<new>\S+)")


def _apk_name(full: str) -> tuple[str, str | None]:
    m = re.match(r"^(?P<name>.+?)-(?P<ver>\d[^-]*-r\d+)$", full)
    return (m.group("name"), m.group("ver")) if m else (full, None)


def parse_status(output: str) -> UpdateStatus:
    s = _sections(output)
    manager = (s.get("pm") or ["none"])[0]
    if manager == "none":
        return UpdateStatus(manager=None, error="Kein unterstützter Paketmanager (apt, dnf, apk) gefunden.", unsupported=True)

    packages: list[Package] = []
    security_names: set[str] = set()
    for line in s.get("security", []):
        # "FEDORA-2024-1a2b3c Important/Sec. openssl-libs-1:3.2.1-2.fc40.x86_64"
        parts = line.split()
        if len(parts) >= 3:
            security_names.add(re.sub(r"-\d.*$", "", parts[-1]))

    for line in s.get("list", []):
        if manager == "apt":
            m = _APT_RE.match(line)
            if m:
                repo = m.group("repo")
                packages.append(Package(name=m.group("name"), new_version=m.group("new"), current_version=m.group("old"),
                                        repo=repo, security=bool(_SECURITY_RE.search(repo))))
        elif manager == "dnf":
            if line.startswith(("Obsoleting", "Last metadata", "Security:")):
                continue
            m = _DNF_RE.match(line)
            if m:
                name = m.group("name")
                packages.append(Package(name=name, new_version=m.group("new"), repo=m.group("repo"), security=name in security_names))
        elif manager == "apk":
            m = _APK_RE.match(line)
            if m:
                name, old = _apk_name(m.group("full"))
                packages.append(Package(name=name, new_version=m.group("new"), current_version=old))

    reboot_lines = s.get("reboot", [])
    reboot = bool(reboot_lines) and reboot_lines[0] == "yes"
    reasons = [p for p in reboot_lines[1:] if p != "yes"][:20]
    kernel = (s.get("kernel") or [None])[0]
    latest = _latest_kernel(kernel, s.get("kernels", [])) if kernel else None
    if kernel and latest and latest != kernel and _version_key(latest) > _version_key(kernel):
        reboot = True
        reasons.insert(0, f"neuer Kernel {latest} (läuft: {kernel})")
    try:
        uptime = float((s.get("uptime") or ["x"])[0])
    except ValueError:
        uptime = None
    unattended_raw = (s.get("unattended") or ["n/a"])[0]
    refresh_error = None
    refresh_lines = s.get("refresh", [])
    bad = [ln for ln in refresh_lines if ln.startswith(("E:", "Err:", "Error", "ERROR")) or "Permission denied" in ln]
    if any("Permission denied" in ln or "are you root" in ln for ln in bad):
        refresh_error = "Paketlisten konnten nicht neu geladen werden (fehlen root-Rechte?) – angezeigt wird der zuletzt geladene Stand."
    elif any("enterprise.proxmox.com" in ln for ln in bad):
        refresh_error = ("Die Proxmox-Enterprise-Paketquelle verlangt ein Abo (401). Ohne Abo die Quelle "
                         "„pve-no-subscription“ nutzen und die Enterprise-Quelle abschalten – die übrigen Quellen wurden geladen.")
    elif bad:
        refresh_error = f"Nicht alle Paketquellen erreichbar: {bad[0][:200]}"

    packages.sort(key=lambda p: (not p.security, p.name))
    return UpdateStatus(
        manager=manager, packages=packages, reboot_required=reboot, reboot_reasons=reasons, kernel=kernel,
        latest_kernel=latest, uptime_s=uptime, unattended=True if unattended_raw == "yes" else False if unattended_raw == "no" else None,
        refresh_error=refresh_error,
    )


def _version_key(version: str) -> tuple[tuple[int, int | str], ...]:
    """Grober Versionsvergleich fuer Kernel-Namen ("6.8.12-4-pve" < "6.8.12-10-pve").
    Zahl und Text werden nie direkt verglichen ((0, Zahl) vor (1, Text)) -- sonst
    TypeError, z. B. "rpi-v8" gegen "rpi-2712"."""
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in re.split(r"[.\-+~]", version) if p)


# "6.8.12-4-pve" -> Art "-pve", "6.1.0-25-amd64" -> "-amd64", "6.12.47+rpt-rpi-2712" -> "+rpt-rpi-2712"
_KERNEL_RE = re.compile(r"^\d+(?:\.\d+)*(?:-\d+)?(?P<flavor>.*)$")


def _kernel_flavor(version: str) -> str | None:
    m = _KERNEL_RE.match(version)
    return m.group("flavor") if m else None


def _latest_kernel(running: str, installed: list[str]) -> str | None:
    """Neuester installierter Kernel derselben Art wie der laufende. Andere Arten
    (Pi: rpi-v8 neben rpi-2712) bootet dieser Server nicht -- sie zaehlen nicht."""
    flavor = _kernel_flavor(running)
    same = [k for k in installed if flavor is not None and _kernel_flavor(k) == flavor]
    return max(same, key=_version_key) if same else None


# --- Einspielen ---------------------------------------------------------------

UPGRADE_DONE = "@@upgrade-done"
REBOOT_MARK = "@@reboot-required"

_APT_OPTS = "-y -q -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold"


def wants_dist_upgrade(settings: dict[str, Any], manager: str, mode: str) -> bool:
    """Soll diese von Hand ausgeloeste Aktion `dist-upgrade` nehmen? Nur bei
    apt und "Alle Updates" und nur, wenn die Einstellung an ist. Ob der Server wirklich
    Proxmox ist, entscheidet der Befehl selbst auf dem Server (`command -v pveversion`).

    Das Ergebnis gehoert in den Plan der Aktion (Payload `dist_upgrade`): Vorschlag und
    Ausfuehrung bauen den Befehl beide daraus, eine spaeter geaenderte Einstellung
    aendert einen schon vorgeschlagenen Befehl nicht. Automatische Laeufe fragen das
    nicht -- sie nehmen nie dist-upgrade."""
    return bool(settings.get("proxmox_dist_upgrade")) and manager == "apt" and mode == "all"


def upgrade_command(manager: str, mode: str, security_packages: list[str] | None = None, *, dist_upgrade: bool = False) -> str:
    """mode: "all" (alles), "security" (nur Sicherheitsupdates), "cleanup" (Altlasten weg).

    Bei apt ist "security" ein gezieltes `install --only-upgrade` der zuletzt als
    Sicherheitsupdate erkannten Pakete -- apt selbst kennt keinen Sicherheits-Filter.

    `dist_upgrade` gilt nur fuer apt und "all": auf einem Proxmox-Server (`pveversion`
    vorhanden) laeuft dann `apt-get dist-upgrade`, sonst wie immer `upgrade --with-new-pkgs`.
    Ohne die Angabe bleibt der Befehl Zeichen fuer Zeichen wie bisher."""
    tail = f"; R=$?; echo '{UPGRADE_DONE}'; [ -f /var/run/reboot-required ] && echo '{REBOOT_MARK}'; exit $R"
    if manager == "apt":
        prefix = "export DEBIAN_FRONTEND=noninteractive; apt-get update -q"
        if mode == "all":
            if dist_upgrade:
                return (f"{prefix} && if command -v pveversion >/dev/null 2>&1; then apt-get {_APT_OPTS} dist-upgrade; "
                        f"else apt-get {_APT_OPTS} upgrade --with-new-pkgs; fi{tail}")
            return f"{prefix} && apt-get {_APT_OPTS} upgrade --with-new-pkgs{tail}"
        if mode == "security":
            names = [n for n in (security_packages or []) if _PKG_NAME_RE.match(n)]
            if not names:
                raise ValueError("Keine Sicherheitsupdates offen.")
            return f"{prefix} && apt-get {_APT_OPTS} install --only-upgrade {' '.join(shlex.quote(n) for n in names)}{tail}"
        if mode == "cleanup":
            return f"export DEBIAN_FRONTEND=noninteractive; apt-get -y -q autoremove --purge && apt-get clean{tail}"
    if manager == "dnf":
        if mode == "all":
            return f"dnf -y upgrade{tail}"
        if mode == "security":
            return f"dnf -y upgrade --security{tail}"
        if mode == "cleanup":
            return f"dnf -y autoremove && dnf clean packages{tail}"
    if manager == "apk":
        if mode in ("all", "security"):
            return f"apk update -q && apk upgrade{tail}"
        if mode == "cleanup":
            return f"apk cache clean 2>/dev/null; true{tail}"
    raise ValueError(f"Nicht unterstützt: {manager}/{mode}")


# Eine Minute Vorlauf: die SSH-Sitzung kann sauber antworten, bevor der Server weg ist.
REBOOT_COMMAND = (
    "if command -v shutdown >/dev/null 2>&1; then shutdown -r +1 'Neustart durch Nodvard Deck nach Updates' 2>&1; "
    "else (sleep 5; reboot) >/dev/null 2>&1 & echo 'Neustart in 5 Sekunden'; fi"
)

MODE_LABEL = {"all": "Alle Updates", "security": "Sicherheitsupdates", "cleanup": "Aufräumen", "reboot": "Neustart"}


@dataclass
class UpgradeResult:
    ok: bool
    upgraded: int = 0
    removed: int = 0
    reboot_required: bool = False
    summary: str = ""
    # Das Dashboard hat aufgehoert zu warten, der Lauf kann auf dem Server noch laufen.
    timed_out: bool = False
    # Pakete, die apt "zurueckgehalten" hat (Update braucht Ersetzen/Entfernen anderer Pakete).
    kept_back: list[str] = field(default_factory=list)


_APT_SUMMARY_RE = re.compile(r"(\d+) upgraded, (\d+) newly installed, (\d+) to remove")
_APT_SUMMARY_DE_RE = re.compile(r"(\d+) aktualisiert, (\d+) neu installiert, (\d+) zu entfernen")


# "The following packages have been kept back:" / "Die folgenden Pakete wurden zurückgehalten:",
# darunter die Namen, eingerueckt.
_KEPT_HEADER_RE = re.compile(r"(?:kept back|zur\S*ckgehalten)\s*:$", re.IGNORECASE)
KEPT_BACK_SHOWN = 5


def parse_kept_back(output: str) -> list[str]:
    """Namen der Pakete, die apt zurueckgehalten hat -- aus dem apt-Abschnitt oder aus der
    Zeile `@@kept-back: a b c`, die der Abfrage-Befehl (`detached.poll_command`) aus dem
    Protokoll zieht, weil der Abschnitt bei langen Laeufen nicht mehr im Ende des
    Protokolls steht. Nur echte Paketnamen, ohne Doppelte, in der Reihenfolge von apt."""
    names: list[str] = []
    lines = output.splitlines()
    for i, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith(KEPT_BACK_MARK):
            names += line[len(KEPT_BACK_MARK):].split()
        elif _KEPT_HEADER_RE.search(line):
            for nxt in lines[i + 1:]:
                if not nxt[:1].isspace() or not nxt.strip():
                    break
                names += nxt.split()
    return list(dict.fromkeys(n for n in names if _PKG_NAME_RE.match(n)))


def parse_upgrade_output(output: str, exit_code: int | None) -> UpgradeResult:
    upgraded = removed = 0
    m = _APT_SUMMARY_RE.search(output) or _APT_SUMMARY_DE_RE.search(output)
    if m:
        upgraded = int(m.group(1)) + int(m.group(2))
        removed = int(m.group(3))
    reboot = REBOOT_MARK in output
    ok = exit_code == 0
    if not ok:
        err = [ln.strip() for ln in output.splitlines() if ln.strip().startswith(("E:", "Error", "error:"))]
        summary = err[-1] if err else (output.strip().splitlines() or ["Fehlgeschlagen"])[-1]
    elif m:
        summary = f"{upgraded} Paket(e) aktualisiert" + (f", {removed} entfernt" if removed else "")
    else:
        summary = "Fertig."
    kept = parse_kept_back(output) if ok else []
    if kept:
        more = f" und {len(kept) - KEPT_BACK_SHOWN} weitere" if len(kept) > KEPT_BACK_SHOWN else ""
        summary += f" – {len(kept)} zurückgehalten ({', '.join(kept[:KEPT_BACK_SHOWN])}{more})"
    if reboot and ok:
        summary += " – Neustart nötig"
    return UpgradeResult(ok=ok, upgraded=upgraded, removed=removed, reboot_required=reboot, summary=summary[:300], kept_back=kept)
