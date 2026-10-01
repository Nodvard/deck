"""Profil "Valheim-Dedicated-Server unter Windows" (Windows-Dienst, z. B. per NSSM).

Live gegen einen echten Windows-Gameserver geprueft (nur lesend):
- Der Dienst heisst dort "ValheimServer", die alte Extension-Voreinstellung war
  "valheim" -- Start/Stop liefen damit ins Leere. Deshalb erkennt das Profil den
  Dienst selbst (Name/Anzeigename/Pfad enthaelt "valheim"), eine Einstellung ist nur
  noch ein Override.
- Log-Pfad kommt aus NSSMs `AppStdout`, der Weltordner aus Dienstkonto bzw. `-savedir`
  -- ebenfalls erkannt statt fest verdrahtet.
- Log-Zeilen, die ausgewertet werden (echte Beispiele im Test): "Connections N ZDOS:"
  (alle 10 min, aktuelle Spielerzahl), "Got character ZDOID from NAME", "Session ...
  registered with join code NNN", "Valheim version: 1.0.12", "World save (5/5) done".
- Die VM-Uhr stand auf Pazifik-Zeit und springt beim Booten -- Zeitstempel werden
  deshalb nur RELATIV zur Uhr der VM selbst ausgewertet (beides dieselbe Uhr).

Das Passwort verlaesst die VM nie: es wird schon im PowerShell-Skript aus den
Startparametern entfernt, Log-Zeilen mit "password" werden dort verworfen.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime
from typing import Any

from . import ConfigField, ServerConfig

_PRELUDE = r"""$ErrorActionPreference='SilentlyContinue'
$S='{S}';$P='{P}';$L='{L}';$W='{W}';$B='{B}'
$svc=$null
if($S){$svc=Get-Service -Name $S}
if(-not $svc){$c=Get-CimInstance Win32_Service|?{$_.Name -match 'valheim' -or $_.PathName -match 'valheim'}|select -First 1;if($c){$svc=Get-Service -Name $c.Name}}
$cim=if($svc){Get-CimInstance Win32_Service -Filter "Name='$($svc.Name)'"}
$pp=if($svc){Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\$($svc.Name)\Parameters"}
$a=if($pp.AppParameters){[string]$pp.AppParameters}else{[string]$cim.PathName}
$ra=[string]$cim.StartName
if(-not $L -and $pp.AppStdout){$L=[string]$pp.AppStdout}
if(-not $W){if($a -match '(?i)-savedir\s+("([^"]*)"|(\S+))'){$W=(@(@($Matches[2],$Matches[3])|?{$_})[0])+'\worlds_local'}elseif($ra -match 'LocalSystem'){$W='C:\Windows\System32\config\systemprofile\AppData\LocalLow\IronGate\Valheim\worlds_local'}elseif($ra){$u=($ra -split '\\')[-1];$W="C:\Users\$u\AppData\LocalLow\IronGate\Valheim\worlds_local"}}
if(-not $B -and $pp.AppDirectory){$B=Join-Path ([string]$pp.AppDirectory) 'backups'}
$wn=if($a -match '(?i)-world\s+("([^"]*)"|(\S+))'){@(@($Matches[2],$Matches[3])|?{$_})[0]}else{''}
"""

_STATUS = r"""$o=[ordered]@{now=(Get-Date).ToString('s');service_name=$(if($svc){$svc.Name}else{''});service=$(if($svc){[string]$svc.Status}else{'missing'})}
$o.has_password=$a -match '(?i)-password\s'
$o.args=[regex]::Replace($a,'(?i)-password\s+("[^"]*"|\S+)','')
$o.log_path=$L;$o.world_dir=$W;$o.backup_dir=$B;$o.world_name=$wn
$pr=Get-Process -Name $P|select -First 1
if($pr){$o.process=@{cpu_s=[math]::Round($pr.CPU);ram_mb=[math]::Round($pr.WorkingSet64/1MB);responding=$pr.Responding}}
if($L -and (Test-Path $L)){
$o.tail=@(Get-Content $L -Tail 250|?{$_ -notmatch '(?i)password'}|%{[string]$_})
$o.players=@(Select-String -Path $L -SimpleMatch 'Got character ZDOID from'|select -Last 40|%{$_.Line})
$o.join=(Select-String -Path $L -SimpleMatch 'registered with join code'|select -Last 1).Line
$o.version=(Select-String -Path $L -SimpleMatch 'Valheim version'|select -Last 1).Line
$o.save=(Select-String -Path $L -SimpleMatch 'World save (5/5) done'|select -Last 1).Line
$o.connections=(Select-String -Path $L -SimpleMatch ' Connections '|select -Last 1).Line}
if($W){$o.world_files=@(Get-ChildItem -Path $W|sort LastWriteTime -Descending|select -First 30|%{@{name=$_.Name;size=$(if($_.PSIsContainer){[long]((Get-ChildItem $_.FullName -File -Recurse|Measure-Object Length -Sum).Sum)}else{$_.Length});modified=$_.LastWriteTime.ToString('s')}})}
if($B -and (Test-Path $B)){$o.backups=@(Get-ChildItem -Path $B -Directory|sort Name -Descending|select -First 20|%{@{name=$_.Name;size=[long]((Get-ChildItem $_.FullName -File -Recurse|Measure-Object Length -Sum).Sum);modified=$_.LastWriteTime.ToString('s')}})}
$o|ConvertTo-Json -Depth 4 -Compress
"""

# Aktionen: Das Prelude schaltet Fehler stumm (SilentlyContinue) -- ein gescheitertes
# Start-Service/Copy-Item fiel dadurch nicht auf, der letzte Befehl gelang, Exit 0, und
# Nodvard Deck meldete "erledigt". Deshalb -ErrorAction Stop im try/catch und bei
# Diensten danach der Zustand geprueft. Write-Error bliebe unter SilentlyContinue ebenfalls
# stumm -- Meldungen gehen direkt nach stderr.
_NO_SERVICE = "if(-not $svc){[Console]::Error.WriteLine('Kein Valheim-Dienst gefunden');exit 2}\n"


def _service_action(command: str, verb: str, want: str) -> str:
    return _NO_SERVICE + (
        r"""try{CMD -ErrorAction Stop}catch{[Console]::Error.WriteLine("VERB fehlgeschlagen: $_");exit 1}
try{$svc.WaitForStatus('WANT',[TimeSpan]::FromSeconds(20))}catch{};$svc.Refresh();$st=[string]$svc.Status
if($st -ne 'WANT'){[Console]::Error.WriteLine("Dienst $($svc.Name) steht danach auf '$st' statt 'WANT'");exit 1};$st"""
        .replace("CMD", command)
        .replace("VERB", verb)
        .replace("WANT", want)
    )


_ACTIONS = {
    "start": _service_action("Start-Service -Name $svc.Name", "Starten", "Running"),
    "stop": _service_action("Stop-Service -Name $svc.Name -Force", "Stoppen", "Stopped"),
    "restart": _service_action("Restart-Service -Name $svc.Name -Force", "Neustart", "Running"),
    # Kopie des letzten Speicherstands in einen Zeitstempel-Ordner: der Weltordner
    # (aktuelle Valheim-Versionen, live gesehen) bzw. .db/.fwl (aeltere). Rein additiv --
    # ueberschreibt und loescht nichts; nur ein eigener, halb kopierter Ordner wird
    # wieder entfernt, damit er nicht als Sicherung in der Liste steht.
    "backup": r'''if(-not $W -or -not $wn){[Console]::Error.WriteLine('Weltordner oder Weltname unbekannt');exit 2};if(-not $B){[Console]::Error.WriteLine('Kein Sicherungsordner');exit 2}
$f=@(Get-ChildItem -Path $W|?{$_.Name -in @($wn,"$wn.db","$wn.fwl")})
if(-not $f){[Console]::Error.WriteLine("Keine Weltdaten fuer $wn in $W");exit 3}
$d=Join-Path $B ((Get-Date).ToString('yyyyMMdd-HHmmss'));$new=-not (Test-Path -LiteralPath $d)
try{New-Item -ItemType Directory -Path $d -Force -ErrorAction Stop|Out-Null;$f|Copy-Item -Destination $d -Recurse -ErrorAction Stop}catch{if($new){Remove-Item -LiteralPath $d -Recurse -Force};[Console]::Error.WriteLine("Sicherung fehlgeschlagen: $_");exit 1}
@{dir=$d;items=@($f|%{$_.Name})}|ConvertTo-Json -Compress''',
}

_SAFE_VALUE = re.compile(r"^[^\r\n\x00']*$")
_LOG_TS = re.compile(r"^(\d{2})/(\d{2})/(\d{4}) (\d{2}):(\d{2}):(\d{2}):")
_NOISE = re.compile(
    r"^(Lobby \S+ for world|Update PlayFab entity token|Unloading |Total: |Loaded Objects now|\s*$)"
    r"|: (Lobby \S+ for world|Update PlayFab entity token|Unloading unused assets)"
)


def _ps_literal(value: str) -> str:
    """Wert fuer ein einfach-quotiertes PowerShell-Literal. Zeilenumbrueche/Hochkommas
    sind in Pfaden/Dienstnamen nie legitim -- abgelehnt statt escaped."""
    if not _SAFE_VALUE.match(value):
        raise ValueError("Ungültiges Zeichen in der Gameserver-Konfiguration.")
    return value


def encode_powershell(script: str) -> str:
    """`-EncodedCommand` (UTF-16LE, Base64): kein Quoting-Problem zwischen OpenSSH, cmd.exe
    und PowerShell, egal welche Standard-Shell der SSH-Server hat."""
    b64 = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return f"powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand {b64}"


def _prelude(config: ServerConfig) -> str:
    return (
        _PRELUDE.replace("{S}", _ps_literal(config.get("service_name")))
        .replace("{P}", _ps_literal(config.get("process_name", "valheim_server")))
        .replace("{L}", _ps_literal(config.get("log_path")))
        .replace("{W}", _ps_literal(config.get("world_dir")))
        .replace("{B}", _ps_literal(config.get("backup_dir")))
    )


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    match = _LOG_TS.match(value.strip())
    if match:
        month, day, year, hh, mm, ss = (int(g) for g in match.groups())
        return datetime(year, month, day, hh, mm, ss)
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def _age(ts: datetime | None, now: datetime | None) -> int | None:
    """Sekunden, relativ zur Uhr der VM. Ein Zeitstempel "in der Zukunft" (die VM-Uhr
    springt beim Booten) ist keine Zeitangabe, der man trauen kann -> None."""
    if ts is None or now is None:
        return None
    delta = int((now - ts).total_seconds())
    return delta if delta >= 0 else None


def parse_server_args(args: str) -> dict[str, Any]:
    tokens = args.split()
    values: dict[str, list[str]] = {}
    modifiers: list[str] = []
    key: str | None = None
    for token in tokens[1:] if tokens and not tokens[0].startswith("-") and tokens[0].lower().endswith(".exe") else tokens:
        if token.startswith("-") and not re.match(r"^-?\d+$", token):
            key = token[1:].lower()
            values.setdefault(key, [])
            continue
        if key:
            values[key].append(token.strip('"'))
    for index, name in enumerate(values.get("modifier", [])):
        if index % 2 == 0:
            modifiers.append(name)
        else:
            modifiers[-1] = f"{modifiers[-1]}: {name}"
    port = " ".join(values.get("port", [])) or None
    public = values.get("public")
    return {
        "name": " ".join(values.get("name", [])) or None,
        "world": " ".join(values.get("world", [])) or None,
        "port": int(port) if port and port.isdigit() else None,
        "crossplay": "crossplay" in values,
        "public": (public[0] == "1") if public else None,
        "preset": " ".join(values.get("preset", [])) or None,
        "modifiers": modifiers,
    }


class ValheimWindowsProfile:
    id = "valheim-windows"
    label = "Valheim (Windows-Dienst)"
    fields = [
        ConfigField("service_name", "Dienstname", "Leer = automatisch erkennen (Dienst mit 'valheim' im Namen oder Pfad)."),
        ConfigField("process_name", "Prozessname", "Für CPU/RAM-Anzeige.", "valheim_server"),
        ConfigField("log_path", "Log-Datei", "Leer = aus der NSSM-Konfiguration (AppStdout)."),
        ConfigField("world_dir", "Weltordner", "Leer = aus Dienstkonto bzw. -savedir abgeleitet."),
        ConfigField("backup_dir", "Sicherungsordner", "Leer = Unterordner 'backups' im Programmordner."),
    ]

    def status_command(self, config: ServerConfig) -> str:
        return encode_powershell(_prelude(config) + _STATUS)

    def action_command(self, action: str, config: ServerConfig) -> str:
        if action not in _ACTIONS:
            raise ValueError(f"Unbekannte Gameserver-Aktion: {action}")
        return encode_powershell(_prelude(config) + _ACTIONS[action])

    def parse_status(self, stdout: str) -> dict[str, Any]:
        start = stdout.find("{")
        raw: dict[str, Any] = json.loads(stdout[start:]) if start >= 0 else {}
        now = _parse_ts(raw.get("now"))
        service_state = str(raw.get("service") or "missing").lower()

        join_line = raw.get("join") or ""
        join_match = re.search(r'Session "([^"]*)" registered with join code (\w+)', join_line)
        version_match = re.search(r"Valheim version: ([^\s(]+)", raw.get("version") or "")
        conn_match = re.search(r"Connections (\d+)", raw.get("connections") or "")

        seen: dict[str, int | None] = {}
        for line in reversed(raw.get("players") or []):
            match = re.search(r"Got character ZDOID from (.+?) : ", line)
            if match and match.group(1) not in seen:
                seen[match.group(1)] = _age(_parse_ts(line), now)
        recent = [{"name": name, "last_seen_age_s": age} for name, age in list(seen.items())[:10]]

        world_name = raw.get("world_name") or ""
        files = raw.get("world_files") or []
        main = [f for f in files if f.get("name") in (world_name, f"{world_name}.db", f"{world_name}.fwl")]
        auto = [f for f in files if "_backup_auto-" in str(f.get("name"))]
        server = parse_server_args(str(raw.get("args") or ""))
        if join_match:
            server["name"] = join_match.group(1)  # was der Server wirklich meldet

        def entry(f: dict[str, Any]) -> dict[str, Any]:
            return {"name": f.get("name"), "size": int(f.get("size") or 0), "age_s": _age(_parse_ts(f.get("modified")), now)}

        tail = [str(line) for line in raw.get("tail") or [] if not _NOISE.search(str(line))]
        return {
            "profile": self.id,
            "service_name": raw.get("service_name") or None,
            "service_state": service_state,
            "running": service_state == "running",
            "process": raw.get("process"),
            "server": {**server, "has_password": bool(raw.get("has_password"))},
            "version": version_match.group(1) if version_match else None,
            "join_code": join_match.group(2) if join_match else None,
            "join_code_age_s": _age(_parse_ts(join_line), now),
            "players_online": int(conn_match.group(1)) if conn_match else None,
            "recent_players": recent,
            "last_save_age_s": _age(_parse_ts(raw.get("save")), now),
            "world": {
                "name": world_name or None,
                "size": sum(int(f.get("size") or 0) for f in main),
                "age_s": min((a for a in (entry(f)["age_s"] for f in main) if a is not None), default=None),
            },
            "auto_backups": [entry(f) for f in auto][:10],
            "backups": [entry(f) for f in raw.get("backups") or []][:10],
            "log_tail": tail[-80:],
            "paths": {"log": raw.get("log_path") or None, "world": raw.get("world_dir") or None, "backup": raw.get("backup_dir") or None},
        }
