"""Gemeinsame Werkzeuge für die Gleichlauf-Tests des Hilfsskripts `shield-root` (entkoppelte Läufe).

Kein Import aus der Erweiterung: Die Tests holen deren Module selbst (siehe Fixture `_ext_shield_on_path`)."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

EXT_DIR = Path(__file__).resolve().parents[2] / "extensions" / "shield"
SCRIPT = EXT_DIR / "root-helper" / "shield-root.sh"
SH = shutil.which("sh") or "sh"


def stubs(base: Path, scripts: dict[str, str]) -> Path:
    """Attrappen unter `base/bin`; ein Text ohne `#!` bekommt `#!/bin/sh`."""
    bin_dir = base / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, body in scripts.items():
        (bin_dir / name).write_text(body if body.startswith("#!") else "#!/bin/sh\n" + body)
        (bin_dir / name).chmod(0o755)
    return bin_dir


def lynis_pid_files(base: Path) -> tuple[str, str]:
    """Die beiden PID-Dateien von Lynis, im Test unter `base` statt unter /run und /var/run."""
    return str(base / "run-lynis.pid"), str(base / "var-run-lynis.pid")


def helper_copy(base: Path, *, path: str, sdmark: Path, rt_settle: int = 0) -> Path:
    """Kopie des echten Skripts unter `base`: fester PATH auf `path`, alle Orte unter `base`, `SELF` auf die Kopie.

    Lauf-Ordner `base/jobs` (Updates) und `base/audit` (Lynis), Quarantäne `base/q`, Lynis-Bericht
    `base/lynis-report.dat`, Lynis-PID-Dateien `lynis_pid_files(base)`, Sperre der Scans `base/lock`, Hilfsdateien des
    Wächters `base/run` (wird angelegt), `/etc` des Echtzeit-Wächters `base/etc`, dessen Wartezeit 4 Sekunden, Nachschau
    nach dem Start nach `rt_settle` Sekunden (sonst sofort), sein Protokoll `base/onaccess.log` und 3 Zeilen je Abruf."""
    helper = base / "shield-root"
    (base / "run").mkdir(parents=True, exist_ok=True)
    text = SCRIPT.read_text(encoding="ascii").replace("PATH=/usr/sbin:/usr/bin:/sbin:/bin", f"PATH={path}")
    values = {
        "JOBDIR": base / "jobs", "SELF": helper, "SDMARK": sdmark, "QDIR": base / "q", "AUDITDIR": base / "audit",
        "LYNIS_REPORT": base / "lynis-report.dat", "LYNIS_PIDS": "'" + " ".join(lynis_pid_files(base)) + "'",
        "LOCKDIR": base / "lock", "TMPRUN": base / "run", "ETCDIR": base / "etc", "RT_WAIT": 4, "RT_SETTLE": rt_settle,
        "RT_LOG": base / "onaccess.log", "RT_BATCH": 3,
    }
    for name, value in values.items():
        text, n = re.subn(rf"(?m)^{name}=.*$", f"{name}={value}", text)
        assert n == 1, name
    helper.write_text(text)
    helper.chmod(0o755)
    return helper


def run_sh(args: list[str], path: str) -> tuple[int, str]:
    done = subprocess.run(args, capture_output=True, text=True, timeout=60, check=False, env={"PATH": path, "LC_ALL": "C"})
    return done.returncode, done.stdout


def wait_for(path: Path, timeout_s: float = 20) -> None:
    end = time.monotonic() + timeout_s
    while not path.exists():
        assert time.monotonic() < end, f"{path} kam nicht"
        time.sleep(0.05)


def pid_free(text: str) -> str:
    return re.sub(r"@@pid=\d+", "@@pid=N", text)


SYSTEMD_RUN = """#!{python}
# Wie systemd-run: Optionen merken, `$$` wird `$`, der Befehl laeuft abgekoppelt weiter.
import subprocess, sys
args = sys.argv[1:]
i = 0
while i < len(args) and args[i].startswith("--"):
    i += 1
with open({log!r}, "a") as fh:
    fh.write(" ".join(args[:i]) + "\\n")
if {fail}:
    sys.exit(1)
subprocess.Popen([a.replace("$$", "$") for a in args[i:]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL, start_new_session=True)
"""


def side(base: Path, tools: dict[str, str], *, systemd: str | None) -> tuple[Path, Path, str]:
    """Ordner einer Seite: Attrappen (mit `systemd`: auch `systemd-run`, "ok" oder "fail"), das systemd-Merkmal (gibt es
    nur mit `systemd`) und der PATH dazu."""
    scripts = dict(tools)
    if systemd is not None:
        scripts["systemd-run"] = SYSTEMD_RUN.format(python=sys.executable, log=str(base / "systemd-run.log"),
                                                    fail=systemd == "fail")
    bin_dir = stubs(base, scripts)
    sdmark = base / "sd"
    if systemd is not None:
        sdmark.mkdir()
    return bin_dir, sdmark, f"{bin_dir}:/usr/bin:/bin"
