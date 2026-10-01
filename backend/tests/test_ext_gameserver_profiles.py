"""Gameserver mit Spiel-Profilen. Die Testdaten sind echten Zeilen eines Windows-
Spielservers nachgebildet (Format live geprueft), aber anonymisiert: keine echte IP,
keine echten Spielernamen, kein echter Weltname."""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import ExtensionRecord, Notification
from nodvard_deck.services import extensions as extensions_service
from nodvard_deck.services import hosts as hosts_service
from sqlalchemy import select

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "gameserver" / "src"))

from nodvard_deck_ext_gameserver.profiles import ServerConfig, get_profile  # noqa: E402
from nodvard_deck_ext_gameserver.profiles.valheim import parse_server_args  # noqa: E402


def _status(**overrides):
    """Form wie die echte Antwort des Status-Skripts, Werte anonymisiert."""
    data = {
        "now": "2026-09-24T10:10:00",
        "service_name": "ValheimServer",
        "has_password": True,
        "args": "-name Valheim Server -port 2456 -world TestWelt  -crossplay -public 1 -modifier portals casual",
        "log_path": "C:\\valheim\\logs\\service-out.log",
        "world_dir": "C:\\Windows\\System32\\config\\systemprofile\\AppData\\LocalLow\\IronGate\\Valheim\\worlds_local",
        "backup_dir": "C:\\valheim\\backups",
        "world_name": "TestWelt",
        "process": {"ram_mb": 1392, "cpu_s": 1082, "responding": True},
        "tail": [
            "09/24/2026 10:05:04: World save (5/5) done. Total time [35ms]",
            "09/24/2026 10:05:13:  Connections 2 ZDOS:47618  sent:0 recv:0",
            "09/24/2026 10:06:27: Lobby 8e1ab597.r-1 for world 'Valheim' and network abc|def",
            "09/24/2026 10:06:54: Update PlayFab entity token",
            "",
            "09/24/2026 10:07:00: Got character ZDOID from Spielerin : 123:1",
        ],
        "players": [
            "09/14/2026 10:39:25: Got character ZDOID from Spieler_A : 951918106:1",
            "09/20/2026 13:29:16: Got character ZDOID from Spielerin : 1527349707:1",
            "09/24/2026 10:07:00: Got character ZDOID from Spielerin : 123:1",
            # Zeitstempel "in der Zukunft": die VM-Uhr springt beim Booten (live gesehen).
            "09/24/2026 19:35:00: Got character ZDOID from Spieler_B : 5:1",
        ],
        "join": "09/24/2026 04:35:15: Session \"Valheim\" registered with join code 413749",
        "version": "09/24/2026 04:35:07: Valheim version: 1.0.12 (network version 40)",
        "save": "09/24/2026 10:05:04: World save (5/5) done. Total time [35ms]",
        "connections": "09/24/2026 10:05:13:  Connections 2 ZDOS:47618  sent:0 recv:0",
        "world_files": [
            {"name": "TestWelt", "size": 1876559, "modified": "2026-09-24T10:05:04"},
            {"name": "TestWelt_backup_auto-20260924-093504", "size": 1876559, "modified": "2026-09-24T09:35:04"},
            {"name": "TestWelt_backup_auto-20260924-000507", "size": 1876500, "modified": "2026-09-24T00:05:07"},
        ],
        "backups": [],
    }
    data.update(overrides)
    return data


def test_valheim_status_is_parsed_relative_to_the_vms_own_clock():
    import json

    s = get_profile("valheim-windows").parse_status("Warnung vorneweg\n" + json.dumps({**_status(), "service": "Running"}))
    assert (s["service_name"], s["running"], s["version"], s["join_code"], s["players_online"]) == ("ValheimServer", True, "1.0.12", "413749", 2)
    assert s["join_code_age_s"] == 5 * 3600 + 34 * 60 + 45
    assert s["server"] == {
        "name": "Valheim", "world": "TestWelt", "port": 2456, "crossplay": True, "public": True, "preset": None,
        "modifiers": ["portals: casual"], "has_password": True,
    }
    assert s["recent_players"][0] == {"name": "Spieler_B", "last_seen_age_s": None}, "Zukunfts-Zeitstempel -> keine Altersangabe"
    assert s["recent_players"][1] == {"name": "Spielerin", "last_seen_age_s": 180}
    assert [p["name"] for p in s["recent_players"]] == ["Spieler_B", "Spielerin", "Spieler_A"]
    assert s["last_save_age_s"] == 296
    assert s["world"] == {"name": "TestWelt", "size": 1876559, "age_s": 296}
    assert [b["name"] for b in s["auto_backups"]] == ["TestWelt_backup_auto-20260924-093504", "TestWelt_backup_auto-20260924-000507"]
    assert all("Lobby" not in line and "PlayFab entity" not in line and line.strip() for line in s["log_tail"])
    assert len(s["log_tail"]) == 3


def test_server_args_and_invalid_config_values():
    assert parse_server_args('-name "Mein Server" -world W -port 2457 -preset hard -modifier raids none -modifier portals casual')["modifiers"] == ["raids: none", "portals: casual"]
    assert parse_server_args("C:\\valheim\\valheim_server.exe -world W -public 0")["public"] is False
    profile = get_profile("valheim-windows")
    with pytest.raises(ValueError):
        profile.status_command(ServerConfig(profile="valheim-windows", values={"log_path": "C:\\x'; Remove-Item C:\\ -Recurse #"}))
    with pytest.raises(ValueError):
        profile.action_command("format-c", ServerConfig(profile="valheim-windows"))


def test_commands_fit_the_windows_command_line_and_never_carry_the_password():
    """cmd.exe (Standard-Shell von Windows-OpenSSH) nimmt hoechstens 8191 Zeichen."""
    profile = get_profile("valheim-windows")
    config = ServerConfig(profile="valheim-windows", values={"log_path": "D:\\Spiele\\Valheim Dedicated Server\\logs\\service-out.log"})
    command = profile.status_command(config)
    assert len(command) < 8000
    script = base64.b64decode(command.rsplit(" ", 1)[-1]).decode("utf-16-le")
    assert "$L='D:\\Spiele\\Valheim Dedicated Server\\logs\\service-out.log'" in script
    assert "-password\\s+" in script and "notmatch '(?i)password'" in script
    for action in ("start", "stop", "restart", "backup"):
        assert len(profile.action_command(action, config)) < 8000


def _script(command: str) -> str:
    return base64.b64decode(command.rsplit(" ", 1)[-1]).decode("utf-16-le")


def test_service_actions_and_backup_stop_on_errors_and_check_the_state_afterwards():
    """Das Prelude setzt $ErrorActionPreference='SilentlyContinue' -- ein
    gescheitertes Start-Service/Copy-Item fiel dadurch nicht auf, der letzte Befehl
    gelang, Exit 0, Nodvard Deck meldete "erledigt"."""
    profile = get_profile("valheim-windows")
    config = ServerConfig(profile="valheim-windows")
    for action, cmdlet, want in (("start", "Start-Service", "Running"), ("stop", "Stop-Service", "Stopped"), ("restart", "Restart-Service", "Running")):
        script = _script(profile.action_command(action, config))
        assert re.search(r"try\{" + cmdlet + r" -Name \$svc\.Name[^}]*-ErrorAction Stop\}catch\{[^}]*exit 1\}", script), action
        assert f"if($st -ne '{want}')" in script, action
    backup = _script(profile.action_command("backup", config))
    assert "Copy-Item -Destination $d -Recurse -ErrorAction Stop" in backup
    assert "Write-Error" not in backup, "Write-Error bliebe unter SilentlyContinue stumm"


# Windows-Attrappe fuer pwsh (auf GitHub-Runnern vorinstalliert): Dienst-Cmdlets und
# Registry als Funktionen, damit die echten Aktions-Skripte laufen -- auch unter Linux.
_FAKE_WINDOWS = r"""$global:state='{STATE}';$global:mode='{MODE}'
function Get-CimInstance{[CmdletBinding()]param($ClassName,$Filter)}
function Get-ItemProperty{[CmdletBinding()]param($Path)[pscustomobject]@{AppParameters='-name Test -world TestWelt'}}
function Get-Service{[CmdletBinding()]param($Name)
if($global:mode -eq 'missing'){return}
$o=[pscustomobject]@{Name='ValheimServer';Status=$global:state}
$o|Add-Member ScriptMethod Refresh {$this.Status=$global:state}
$o|Add-Member ScriptMethod WaitForStatus {param($want,$timeout)$this.Status=$global:state;if($global:state -ne $want){throw 'Zeitueberschreitung'}}
$o}
function Set-FakeState([string]$target){
if($global:mode -eq 'error'){Write-Error 'Der Dienst kann nicht gestartet werden: Zugriff verweigert';return}
if($global:mode -eq 'silent'){return}
$global:state=$target}
function Start-Service{[CmdletBinding()]param($Name)Set-FakeState 'Running'}
function Stop-Service{[CmdletBinding()]param($Name,[switch]$Force)Set-FakeState 'Stopped'}
function Restart-Service{[CmdletBinding()]param($Name,[switch]$Force)Set-FakeState 'Running'}
if($global:mode -eq 'copyfail'){function Copy-Item{[CmdletBinding()]param([Parameter(ValueFromPipeline)]$InputObject,$Destination,[switch]$Recurse)process{Write-Error 'Datei wird von einem anderen Prozess verwendet'}}}
"""

_PWSH = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(_PWSH is None, reason="PowerShell (pwsh) nicht installiert")


def _run_on_fake_windows(action: str, *, state: str = "Stopped", mode: str = "ok", values: dict | None = None):
    config = ServerConfig(profile="valheim-windows", values={"service_name": "ValheimServer", **(values or {})})
    script = _FAKE_WINDOWS.replace("{STATE}", state).replace("{MODE}", mode) + _script(get_profile("valheim-windows").action_command(action, config))
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return subprocess.run([_PWSH, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], capture_output=True, text=True, timeout=120, check=False)


@needs_pwsh
@pytest.mark.parametrize(
    ("action", "state", "mode", "exit_code", "stdout", "stderr"),
    [
        ("start", "Stopped", "ok", 0, "Running", ""),
        ("start", "Stopped", "error", 1, "", "Zugriff verweigert"),
        # Cmdlet ohne Fehler, Dienst laeuft trotzdem nicht (z. B. NSSM-Startfehler).
        ("start", "Stopped", "silent", 1, "", "Stopped"),
        ("stop", "Running", "ok", 0, "Stopped", ""),
        ("stop", "Running", "silent", 1, "", "Running"),
        ("restart", "Running", "error", 1, "", "Zugriff verweigert"),
        ("start", "Stopped", "missing", 2, "", "Kein Valheim-Dienst gefunden"),
    ],
)
def test_service_actions_report_failure_on_a_fake_windows(action, state, mode, exit_code, stdout, stderr):
    result = _run_on_fake_windows(action, state=state, mode=mode)
    assert result.returncode == exit_code, (result.stdout, result.stderr)
    assert result.stdout.strip() == stdout
    assert stderr in result.stderr


@needs_pwsh
def test_world_backup_fails_when_copying_fails_and_leaves_no_half_backup(tmp_path):
    world, backups = tmp_path / "worlds_local", tmp_path / "backups"
    world.mkdir()
    backups.mkdir()
    (world / "TestWelt.db").write_bytes(b"welt")
    (world / "TestWelt.fwl").write_bytes(b"meta")
    values = {"world_dir": str(world), "backup_dir": str(backups)}

    failed = _run_on_fake_windows("backup", mode="copyfail", values=values)
    assert failed.returncode == 1, (failed.stdout, failed.stderr)
    assert "anderen Prozess" in failed.stderr
    assert list(backups.iterdir()) == [], "halbe Sicherung darf nicht als Sicherung stehen bleiben"

    ok = _run_on_fake_windows("backup", values=values)
    assert ok.returncode == 0, (ok.stdout, ok.stderr)
    (made,) = backups.iterdir()
    assert sorted(p.name for p in made.iterdir()) == ["TestWelt.db", "TestWelt.fwl"]
    assert json.loads(ok.stdout)["items"]


async def _setup(client, db_session, test_settings, local_ssh_server, *, host_status: str = "up"):
    host_addr, port, username, password, _root = local_ssh_server
    host = await hosts_service.create_host(db_session, name="game-win", address=host_addr, tags=["gameserver"])
    host.status = host_status
    await hosts_service.add_credential(db_session, test_settings, host_id=host.id, kind="ssh_password", username=username, port=port, secret_value=password)
    await db_session.commit()
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.post("/api/v1/extensions/gameserver/enable", headers=headers)).status_code == 200
    return host, headers


@pytest.mark.asyncio
async def test_details_show_players_world_and_join_code_over_real_ssh(client, db_session, test_settings, local_ssh_server, fake_powershell):
    fake_powershell["status_json"] = _status()
    host, headers = await _setup(client, db_session, test_settings, local_ssh_server)

    r = await client.get(f"/api/v1/ext/gameserver/servers/{host.id}", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["running"], body["service_label"], body["tone"], body["join_code"], body["players_display"]) == (True, "läuft", "good", "413749", "2 Spieler online")
    d = body["details"]
    assert (d["world"]["name"], d["server"]["has_password"], d["version"]) == ("TestWelt", True, "1.0.12")
    assert body["config"] == {"profile": "valheim-windows", "values": {}}
    assert "password" not in r.text.replace("has_password", "")

    # Zwischengespeichert: Liste + Widget + Details teilen sich eine SSH-Abfrage.
    await client.get("/api/v1/ext/gameserver/servers", headers=headers)
    await client.get("/api/v1/ext/gameserver/widgets/servers", headers=headers)
    assert len(fake_powershell["scripts"]) == 1
    # Frisch (am Cache vorbei) nur ueber die Detail-Route fuer `hosts.execute`.
    full = await client.get(f"/api/v1/ext/gameserver/servers/{host.id}/details", params={"fresh": True}, headers=headers)
    assert full.status_code == 200, full.text
    assert len(fake_powershell["scripts"]) == 2
    assert full.json()["details"]["log_tail"][0].endswith("World save (5/5) done. Total time [35ms]")

    profiles = (await client.get("/api/v1/ext/gameserver/profiles", headers=headers)).json()
    assert profiles[0]["id"] == "valheim-windows"
    assert [f["key"] for f in profiles[0]["fields"]] == ["service_name", "process_name", "log_path", "world_dir", "backup_dir"]


async def _viewer_headers(client, db_session) -> dict:
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username="viewer1", password_hash=security.hash_password("whatever123"), is_active=True)
    user.roles.append(roles["viewer"])
    db_session.add(user)
    await db_session.commit()
    login = await client.post("/api/v1/auth/login", json={"username": "viewer1", "password": "whatever123"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.mark.asyncio
async def test_untagged_host_is_no_gameserver_and_triggers_no_ssh(client, db_session, test_settings, local_ssh_server, fake_powershell):
    """Die Detail-, Konfig- und Aktions-Routen nahmen JEDEN Host -- ein
    Betrachter loeste so das Statusskript per SSH auf jedem beliebigen Server aus."""
    fake_powershell["status_json"] = _status()
    _host, headers = await _setup(client, db_session, test_settings, local_ssh_server)
    host_addr, port, username, password, _root = local_ssh_server
    other = await hosts_service.create_host(db_session, name="docker", address=host_addr)
    other.status = "up"
    await hosts_service.add_credential(db_session, test_settings, host_id=other.id, kind="ssh_password", username=username, port=port, secret_value=password)
    await db_session.commit()

    base = f"/api/v1/ext/gameserver/servers/{other.id}"
    for method, url, body in [
        ("GET", base, None), ("GET", f"{base}/details", None), ("POST", f"{base}/start", None),
        ("PUT", f"{base}/config", {"profile": "valheim-windows", "values": {}}),
    ]:
        r = await client.request(method, url, json=body, headers=headers)
        assert r.status_code == 404, (method, url, r.text)
    assert fake_powershell["scripts"] == []
    record = await db_session.get(ExtensionRecord, "gameserver")
    await db_session.refresh(record)
    assert other.id not in (record.settings or {}).get("servers", {})


@pytest.mark.asyncio
async def test_viewer_sees_no_server_log_and_cannot_bypass_the_cache(client, db_session, test_settings, local_ssh_server, fake_powershell):
    """Betrachter (nur `hosts.read`) sehen Status, Spieler und Welt, aber
    nicht das Server-Log (Spielernamen, Verbindungsdaten) -- und `fresh=true` loest
    fuer sie keine neue SSH-Abfrage aus."""
    fake_powershell["status_json"] = _status()
    host, _owner = await _setup(client, db_session, test_settings, local_ssh_server)
    viewer = await _viewer_headers(client, db_session)

    r = await client.get(f"/api/v1/ext/gameserver/servers/{host.id}", params={"fresh": True}, headers=viewer)
    assert r.status_code == 200, r.text
    assert r.json()["join_code"] == "413749" and r.json()["details"]["world"]["name"] == "TestWelt"
    assert "log_tail" not in r.json()["details"]
    assert "Lobby 8e1ab597" not in r.text  # Log-Zeile mit Netzwerk-Kennung
    assert len(fake_powershell["scripts"]) == 1
    again = await client.get(f"/api/v1/ext/gameserver/servers/{host.id}", params={"fresh": True}, headers=viewer)
    assert again.status_code == 200
    assert len(fake_powershell["scripts"]) == 1, "fresh=true darf fuer Betrachter nicht am Cache vorbei"
    assert (await client.get(f"/api/v1/ext/gameserver/servers/{host.id}/details", headers=viewer)).status_code == 403


@pytest.mark.asyncio
async def test_config_per_server_is_validated_saved_and_used(client, db_session, test_settings, local_ssh_server, fake_powershell):
    fake_powershell["status_json"] = _status()
    host, headers = await _setup(client, db_session, test_settings, local_ssh_server)
    url = f"/api/v1/ext/gameserver/servers/{host.id}/config"

    assert (await client.put(url, json={"profile": "minecraft-bedrock", "values": {}}, headers=headers)).status_code == 422
    assert (await client.put(url, json={"profile": "valheim-windows", "values": {"hacker": "x"}}, headers=headers)).status_code == 422
    assert (await client.put(url, json={"profile": "valheim-windows", "values": {"service_name": "a'b"}}, headers=headers)).status_code == 422
    ok = await client.put(url, json={"profile": "valheim-windows", "values": {"service_name": " ValheimServer ", "log_path": ""}}, headers=headers)
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"profile": "valheim-windows", "values": {"service_name": "ValheimServer"}}

    record = await db_session.get(ExtensionRecord, "gameserver")
    await db_session.refresh(record)
    assert record.settings["servers"][host.id] == {"profile": "valheim-windows", "service_name": "ValheimServer"}
    await client.get(f"/api/v1/ext/gameserver/servers/{host.id}", headers=headers)
    assert "$S='ValheimServer'" in fake_powershell["scripts"][-1]
    assert (await client.put(url, json={"profile": "valheim-windows", "values": {}})).status_code == 401


@pytest.mark.asyncio
async def test_restart_and_world_backup_go_through_the_gate(client, db_session, test_settings, local_ssh_server, fake_powershell):
    fake_powershell["status_json"] = _status()
    host, headers = await _setup(client, db_session, test_settings, local_ssh_server)

    backup = await client.post(f"/api/v1/ext/gameserver/servers/{host.id}/backup", headers=headers)
    assert (backup.json()["status"], backup.json()["risk"]) == ("proposed", "low")
    done = await client.post(f"/api/v1/actions/{backup.json()['action_id']}/approve", headers=headers)
    assert done.json()["status"] == "succeeded", done.text
    assert "Copy-Item -Destination $d -Recurse" in fake_powershell["scripts"][-1]
    details = (await client.get(f"/api/v1/ext/gameserver/servers/{host.id}", headers=headers)).json()
    assert [b["name"] for b in details["details"]["backups"]] == ["20260924-000001"], "Cache nach der Aktion verworfen"

    restart = await client.post(f"/api/v1/ext/gameserver/servers/{host.id}/restart", headers=headers)
    assert restart.json()["risk"] == "medium"
    done = await client.post(f"/api/v1/actions/{restart.json()['action_id']}/approve", headers=headers)
    assert done.json()["status"] == "succeeded"
    assert "Restart-Service -Name $svc.Name -Force" in fake_powershell["scripts"][-1]


@pytest.mark.asyncio
async def test_manual_server_without_checked_status_is_still_queried_and_watched(client, db_session, test_settings, local_ssh_server, fake_powershell):
    """Ohne Proxmox: ein von Hand angelegter Gameserver hat den Zustand „unknown“, solange niemand
    „Verbindung prüfen“ gedrückt hat (nichts führt ihn sonst nach). Er muss trotzdem abgefragt werden:
    Seite und Widget zeigen Spieler und Join-Code, der Wächter meldet einen neuen Code. Nur „down“
    bleibt ungefragt."""
    fake_powershell["status_json"] = _status()
    host, headers = await _setup(client, db_session, test_settings, local_ssh_server, host_status="unknown")

    body = (await client.get(f"/api/v1/ext/gameserver/servers/{host.id}", headers=headers)).json()
    assert (body["status"], body["running"], body["service_label"], body["join_code"]) == ("unknown", True, "läuft", "413749")
    assert body["players_display"] == "2 Spieler online"
    assert len(fake_powershell["scripts"]) == 1

    assert (await _run_watch())["checked"] == 1
    fake_powershell["status_json"] = _status(join="09/25/2026 04:35:15: Session \"Valheim\" registered with join code 777001")
    assert (await _run_watch())["notified"] == 1
    assert await _notes(db_session) == [("info", "game-win: neuer Join-Code 777001")]

    # Als ausgeschaltet bekannt: keine Abfrage, kein Wächter-Lauf für diesen Server.
    scripts_before = len(fake_powershell["scripts"])
    host.status = "down"
    await db_session.commit()
    assert (await _run_watch())["checked"] == 0
    assert len(fake_powershell["scripts"]) == scripts_before


async def _run_watch():
    handler = get_extension_runtime().scheduler.get("gameserver", "watch")
    assert handler is not None
    return await handler()


async def _notes(db_session):
    rows = (await db_session.execute(select(Notification).where(Notification.source_ext_id == "gameserver").order_by(Notification.ts))).scalars().all()
    return [(n.severity, n.title) for n in rows]


@pytest.mark.asyncio
async def test_watch_pushes_a_new_join_code_and_a_stopped_server(client, db_session, test_settings, local_ssh_server, fake_powershell):
    fake_powershell["status_json"] = _status()
    await _setup(client, db_session, test_settings, local_ssh_server)

    assert (await _run_watch())["notified"] == 0, "erster Lauf merkt sich den Code nur"
    assert (await _run_watch())["notified"] == 0

    fake_powershell["status_json"] = _status(join="09/25/2026 04:35:15: Session \"Valheim\" registered with join code 777001")
    assert (await _run_watch())["notified"] == 1

    fake_powershell["service"] = "Stopped"
    assert (await _run_watch())["notified"] == 0, "einmal gestoppt kann ein Neustart sein"
    assert (await _run_watch())["notified"] == 1
    assert (await _run_watch())["notified"] == 0
    fake_powershell["service"] = "Running"
    assert (await _run_watch())["notified"] == 1

    assert await _notes(db_session) == [
        ("info", "game-win: neuer Join-Code 777001"),
        ("warning", "game-win: Gameserver läuft nicht"),
        ("info", "game-win läuft wieder"),
    ]


# Wartungsfenster: stumme Meldung nach dem Fenster einmal nachholen (PR #41, bekannte Grenze)


class _RecordingChannel:
    channel_id = "test-kanal"
    label = "Test-Kanal"

    def __init__(self) -> None:
        self.received: list = []

    async def send(self, notification) -> None:
        self.received.append(notification)

    async def test(self):
        raise NotImplementedError


async def _set_window(db_session, host_id: str, *, minutes_ago: int) -> None:
    """Fenster von 60 Minuten, Start vor `minutes_ago` Minuten (Ortszeit wie der Waehler):
    5 -> laeuft gerade, 90 -> schon vorbei."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from nodvard_deck.config import LOCAL_TIMEZONE
    from nodvard_deck.db import utcnow
    from nodvard_deck.services import settings as settings_service

    start = (utcnow() - timedelta(minutes=minutes_ago)).astimezone(ZoneInfo(LOCAL_TIMEZONE))
    await settings_service.set_global(
        db_session, "maintenance.windows",
        [{"cron": f"{start.minute} {start.hour} * * *", "duration_minutes": 60, "host_ids": [host_id]}],
    )


def _provide_channel() -> _RecordingChannel:
    from nodvard_sdk.capabilities import NotificationChannel

    channel = _RecordingChannel()
    get_extension_runtime().capabilities.provide("test-ext", NotificationChannel, channel)
    return channel


async def _deliveries(db_session) -> list[tuple[str, str]]:
    from nodvard_deck.models import NotificationDelivery

    rows = (await db_session.execute(
        select(Notification.title, NotificationDelivery.status)
        .join(NotificationDelivery, NotificationDelivery.notification_id == Notification.id)
        .where(Notification.source_ext_id == "gameserver").order_by(Notification.ts, NotificationDelivery.id)
    )).all()
    return [(title, status) for title, status in rows]


@pytest.mark.asyncio
async def test_watch_announces_a_stopped_server_from_the_window_once_after_it(
    client, db_session, test_settings, local_ssh_server, fake_powershell
):
    fake_powershell["status_json"] = _status()
    host, _headers = await _setup(client, db_session, test_settings, local_ssh_server)
    channel = _provide_channel()
    await _run_watch()  # Code merken

    await _set_window(db_session, host.id, minutes_ago=5)
    fake_powershell["service"] = "Stopped"
    for _ in range(4):
        await _run_watch()
    assert channel.received == []
    assert await _deliveries(db_session) == [("game-win: Gameserver läuft nicht", "suppressed")]

    await _set_window(db_session, host.id, minutes_ago=90)
    assert (await _run_watch())["notified"] == 1
    assert (await _run_watch())["notified"] == 0, "nach dem Fenster genau einmal"
    assert [n.title for n in channel.received] == ["game-win: Gameserver läuft nicht (seit dem Wartungsfenster)"]
    assert "Wartungsfenster" in channel.received[0].body

    fake_powershell["service"] = "Running"
    await _run_watch()
    assert [n.title for n in channel.received][1:] == ["game-win läuft wieder"]


@pytest.mark.asyncio
async def test_watch_server_back_within_the_window_brings_no_push_afterwards(
    client, db_session, test_settings, local_ssh_server, fake_powershell
):
    fake_powershell["status_json"] = _status()
    host, _headers = await _setup(client, db_session, test_settings, local_ssh_server)
    channel = _provide_channel()
    await _run_watch()

    await _set_window(db_session, host.id, minutes_ago=5)
    fake_powershell["service"] = "Stopped"
    await _run_watch()
    await _run_watch()
    fake_powershell["service"] = "Running"
    await _run_watch()

    await _set_window(db_session, host.id, minutes_ago=90)
    assert (await _run_watch())["notified"] == 0
    assert channel.received == []
    assert await _deliveries(db_session) == [
        ("game-win: Gameserver läuft nicht", "suppressed"),
        ("game-win läuft wieder", "suppressed"),
    ]


@pytest.mark.asyncio
async def test_watch_announces_a_join_code_from_the_window_once_after_it(
    client, db_session, test_settings, local_ssh_server, fake_powershell
):
    """Der Join-Code wechselt beim naechtlichen Neustart -- also mitten im Fenster. Gilt er
    nach dem Fenster noch, kommt er einmal als Push (sonst wuesste niemand den neuen)."""
    fake_powershell["status_json"] = _status()
    host, _headers = await _setup(client, db_session, test_settings, local_ssh_server)
    channel = _provide_channel()
    await _run_watch()

    await _set_window(db_session, host.id, minutes_ago=5)
    fake_powershell["status_json"] = _status(join="09/25/2026 04:35:15: Session \"Valheim\" registered with join code 777001")
    for _ in range(3):
        await _run_watch()
    assert channel.received == []
    assert await _deliveries(db_session) == [("game-win: neuer Join-Code 777001", "suppressed")]

    await _set_window(db_session, host.id, minutes_ago=90)
    assert (await _run_watch())["notified"] == 1
    assert (await _run_watch())["notified"] == 0, "nach dem Fenster genau einmal"
    assert [(n.title, n.payload["join_code"]) for n in channel.received] == [
        ("game-win: neuer Join-Code 777001 (seit dem Wartungsfenster)", "777001"),
    ]


@pytest.mark.asyncio
async def test_watch_all_clear_for_a_heard_alarm_comes_after_the_window(
    client, db_session, test_settings, local_ssh_server, fake_powershell
):
    """Aus dem Review: der Ausfall kam hoerbar, "laeuft wieder" faellt ins Fenster. Im
    Fenster bleibt es still, nach dem Fenster kommt es genau einmal -- sonst bliebe der
    gehoerte Alarm auf dem Handy offen."""
    fake_powershell["status_json"] = _status()
    host, _headers = await _setup(client, db_session, test_settings, local_ssh_server)
    channel = _provide_channel()
    await _run_watch()
    fake_powershell["service"] = "Stopped"
    await _run_watch()
    await _run_watch()
    assert [n.title for n in channel.received] == ["game-win: Gameserver läuft nicht"]

    await _set_window(db_session, host.id, minutes_ago=5)
    fake_powershell["service"] = "Running"
    await _run_watch()  # die stumme Entwarnung im Verlauf
    for _ in range(2):
        assert (await _run_watch())["notified"] == 0, "im Fenster keine weitere Meldung"
    assert [n.title for n in channel.received] == ["game-win: Gameserver läuft nicht"]

    await _set_window(db_session, host.id, minutes_ago=90)
    assert (await _run_watch())["notified"] == 1
    assert (await _run_watch())["notified"] == 0, "nach dem Fenster genau einmal"
    assert [n.title for n in channel.received][1:] == ["game-win läuft wieder (im Wartungsfenster)"]
    assert await _deliveries(db_session) == [
        ("game-win: Gameserver läuft nicht", "sent"),
        ("game-win läuft wieder", "suppressed"),
        ("game-win läuft wieder (im Wartungsfenster)", "sent"),
    ]


@pytest.mark.asyncio
async def test_watch_pending_all_clear_is_dropped_when_the_server_stops_again(
    client, db_session, test_settings, local_ssh_server, fake_powershell
):
    fake_powershell["status_json"] = _status()
    host, _headers = await _setup(client, db_session, test_settings, local_ssh_server)
    channel = _provide_channel()
    await _run_watch()
    fake_powershell["service"] = "Stopped"
    await _run_watch()
    await _run_watch()

    await _set_window(db_session, host.id, minutes_ago=5)
    fake_powershell["service"] = "Running"
    await _run_watch()
    fake_powershell["service"] = "Stopped"
    await _run_watch()
    await _run_watch()  # neuer Ausfall, still im Fenster

    await _set_window(db_session, host.id, minutes_ago=90)
    await _run_watch()
    assert [n.title for n in channel.received] == [
        "game-win: Gameserver läuft nicht",
        "game-win: Gameserver läuft nicht (seit dem Wartungsfenster)",
    ]


@pytest.mark.asyncio
async def test_watch_runs_against_an_older_core_without_the_new_answers(
    client, db_session, test_settings, local_ssh_server, fake_powershell, monkeypatch
):
    """Aelterer Kern: `send()` liefert nichts, `would_suppress()` gibt es nicht. Kein
    Absturz, der Waechter laeuft wie vor der Nachmeldung."""
    from nodvard_deck.ext.context import NotifyHandle

    real_send = NotifyHandle.send

    async def old_send(self, notification, *, raise_on_failure=False):
        await real_send(self, notification, raise_on_failure=raise_on_failure)

    monkeypatch.setattr(NotifyHandle, "send", old_send)
    monkeypatch.delattr(NotifyHandle, "would_suppress")

    fake_powershell["status_json"] = _status()
    host, _headers = await _setup(client, db_session, test_settings, local_ssh_server)
    _provide_channel()
    await _run_watch()
    await _set_window(db_session, host.id, minutes_ago=5)
    fake_powershell["service"] = "Stopped"
    await _run_watch()
    await _run_watch()
    fake_powershell["service"] = "Running"
    assert (await _run_watch())["notified"] == 1
    assert await _deliveries(db_session) == [
        ("game-win: Gameserver läuft nicht", "suppressed"), ("game-win läuft wieder", "suppressed"),
    ]
