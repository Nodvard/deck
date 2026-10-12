"""Waechter-Vorlagen (watch_templates.py): Upload-Ordner auf den Servern finden (nur lesend) und mit einem Klick zum
Echtzeit-Waechter hinzufuegen."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "shield" / "src"))

from nodvard_deck_ext_shield import antivirus as av
from nodvard_deck_ext_shield import watch_templates as wt
from nodvard_sdk import Actor

OUTPUT = """@@samba
[global]
   path = /srv/global-gilt-nicht
[familie]
   path = /srv/samba/familie
[home]
   Path = /home/%U
[wurzel]
   path = /
[leer]
   path = "/srv/samba/mit leer"
[printers]
   path = /var/spool/samba
[alt]
   directory = /srv/alt
@@nfs
/srv/nfs/backup 10.0.0.0/24(rw,sync)
/srv/samba/familie *(ro)
"/srv/nfs/mit leer" 10.0.0.1(rw)
/srv/nfs/oct\\040space *(ro)
@@nextcloud
/var/www/nextcloud-data
/var/lib/docker/volumes/nextcloud_data/_data/data
/srv/../etc
@@end
"""


def test_detection_output_becomes_clean_folders_per_kind():
    found, complete = wt.parse_detection(OUTPUT)
    assert complete
    assert found == [
        ("samba", "/srv/samba/familie"),
        ("samba", "/srv/samba/mit leer"),
        ("samba", "/srv/alt"),
        ("nfs", "/srv/nfs/backup"),
        ("nfs", "/srv/samba/familie"),
        ("nfs", "/srv/nfs/mit leer"),
        ("nfs", "/srv/nfs/oct space"),
        ("nextcloud", "/var/www/nextcloud-data"),
        ("nextcloud", "/var/lib/docker/volumes/nextcloud_data/_data/data"),
    ]
    assert wt.parse_detection("/vor/dem/ersten/marker\n@@unbekannt\n/srv/x\n@@end\n") == ([], True)


def test_a_cut_off_answer_is_marked_incomplete():
    found, complete = wt.parse_detection("@@samba\n[a]\npath = /srv/a\n@@nfs\n")
    assert found == [("samba", "/srv/a")] and complete is False


def test_a_broken_quoted_export_line_suggests_nothing():
    assert wt.parse_detection('@@nfs\n"/srv/halb 10.0.0.1(rw)\n@@end\n') == ([], True)


@pytest.mark.parametrize(("raw", "expected"), [
    ("/srv/share/", "/srv/share"),
    ("  /srv//share  ", "/srv/share"),
    ("relativ/pfad", None),
    ("/home/%U", None),
    ("/", None),
    ("/etc", None),
    ("/var/lib/docker", None),
    ("/srv/../etc", None),
    ("/srv/a\tb", None),
    ("/proc/1", None),
    ("/sys/kernel", None),
    ("/run/user/1000", None),
    ("/boot/efi", None),
    ("/devel", "/devel"),
])
def test_clean_path(raw, expected):
    assert wt.clean_path(raw) == expected


def test_covered_by_finds_the_folder_itself_or_a_parent():
    watched = ["/home", "/srv/samba/familie/", "/opt"]
    assert wt.covered_by("/home/nico/Downloads", watched) == "/home"
    assert wt.covered_by("/srv/samba/familie", watched) == "/srv/samba/familie"
    assert wt.covered_by("/srv/samba/familie2", watched) is None
    assert wt.covered_by("/homes", watched) is None


def test_the_detection_only_reads():
    script = wt.DETECT_SCRIPT
    for forbidden in (" rm ", "mv ", "tee ", "chmod", "chown", "> /", ">/", ">>", "sed -i", "dd "):
        assert forbidden not in script.replace("2>/dev/null", ""), forbidden
    assert wt.detect_command().startswith('if [ "$(id -u)" -eq 0 ]')
    assert "sudo -n" in wt.detect_command() and "exit 126" not in wt.detect_command()  # ohne root: als normaler Benutzer


def _run_detection(tmp_path: Path, files: dict[str, str]) -> str:
    """Fuehrt DETECT_SCRIPT mit sh aus, die festen Pfade auf `tmp_path` umgebogen."""
    script = wt.DETECT_SCRIPT
    for real in ("/etc/samba/smb.conf", "/etc/exports", "/var/www/nextcloud/config/config.php",
                 "/var/www/html/nextcloud/config/config.php", "/var/www/html/config/config.php", "/var/lib/docker/volumes"):
        script = script.replace(real, str(tmp_path / real.lstrip("/")))
    for rel, content in files.items():
        target = tmp_path / rel.lstrip("/")
        if content == "<dir>":
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
    return subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=20, check=True).stdout


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine POSIX-Shell")
def test_the_script_reads_real_files_correctly(tmp_path):
    vol = "/var/lib/docker/volumes"
    out = _run_detection(tmp_path, {
        "/etc/samba/smb.conf": (
            "[global]\n  workgroup = WG\n; path = /auskommentiert\n# path = /auch\n"
            "[Daten]\n\tPath = /srv/daten\n[printers]\n  path = /var/spool/samba\n[alt]\n directory=/srv/alt\n"
        ),
        "/etc/exports": '# Kommentar\n\n/srv/nfs 10.0.0.0/24(rw)\n"/srv/nfs/mit leer" *(ro)\n/srv/oct\\040x *(ro)\n',
        "/var/www/nextcloud/config/config.php": (
            "<?php\n$CONFIG = array (\n  // 'datadirectory' => '/alt',\n  'datadirectory' => '/mnt/ncdata',\n);\n"
        ),
        f"{vol}/nextcloud_html/_data/data/appdata_oc1": "<dir>",
        f"{vol}/nextcloud_aio_nextcloud_data/_data/appdata_oc2": "<dir>",
        f"{vol}/nextcloud_leer/_data/data": "<dir>",
        f"{vol}/anderes/_data/appdata_x": "<dir>",
    })
    found, complete = wt.parse_detection(out)
    assert complete
    volumes = str(tmp_path / vol.lstrip("/"))
    assert found == [
        ("samba", "/srv/daten"),
        ("samba", "/srv/alt"),
        ("nfs", "/srv/nfs"),
        ("nfs", "/srv/nfs/mit leer"),
        ("nfs", "/srv/oct x"),
        ("nextcloud", "/mnt/ncdata"),
        ("nextcloud", f"{volumes}/nextcloud_aio_nextcloud_data/_data"),
        ("nextcloud", f"{volumes}/nextcloud_html/_data/data"),
    ]


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine POSIX-Shell")
def test_the_script_without_samba_nfs_or_nextcloud_says_nothing_but_finishes(tmp_path):
    assert wt.parse_detection(_run_detection(tmp_path, {})) == ([], True)


class _Settings:
    def __init__(self, value: dict) -> None:
        self.value = value

    async def get(self) -> dict:
        return dict(self.value)

    async def set(self, value: dict) -> None:
        self.value = dict(value)


def _host(host_id: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(id=host_id, name=name, display_name=None)


def _ctx(settings: dict, outputs: dict[str, str | Exception]) -> SimpleNamespace:
    calls: list[str] = []
    audit: list[dict] = []

    async def run(host, command, timeout_s=60):
        calls.append(command)
        result = outputs[host.id]
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(exit_code=0, stdout=result, stderr="", duration_ms=1)

    async def log(**kwargs):
        audit.append(kwargs)

    return SimpleNamespace(
        settings=_Settings(settings), exec=SimpleNamespace(run=run), calls=calls, audit_entries=audit,
        audit=SimpleNamespace(log=log), api=SimpleNamespace(current_actor=lambda: Actor.user("u1", "nico")),
    )


class _Defender:
    def __init__(self, hosts) -> None:
        self._hosts = hosts

    async def target_hosts(self):
        return self._hosts


@pytest.mark.asyncio
async def test_find_templates_merges_servers_and_names_the_unreachable():
    ctx = _ctx({"watch_paths": ["/home", "/srv/nfs"]}, {
        "h1": OUTPUT,
        "h2": "@@samba\n[f]\npath = /srv/samba/familie\n@@nfs\n@@nextcloud\n@@end\n",
        "h3": OSError("weg"),
    })
    result = await wt.find_templates(ctx, _Defender([_host("h1", "nas"), _host("h2", "pi"), _host("h3", "aus")]))
    by_path = {s["path"]: s for s in result["suggestions"]}
    assert by_path["/srv/samba/familie"]["hosts"] == ["nas", "pi"]
    assert by_path["/srv/samba/familie"]["kinds"] == ["samba", "nfs"]
    assert by_path["/srv/nfs/backup"]["covered_by"] == "/srv/nfs"
    covered = [s["covered_by"] is not None for s in result["suggestions"]]
    assert covered == sorted(covered) and covered[-1], "schon ueberwachte stehen hinten"
    assert result["errors"] == ["aus: keine Antwort (nicht erreichbar, kein Zugang oder Zeitlimit), dort wurde nicht gesucht."]
    assert result["watch_paths"] == ["/home", "/srv/nfs"]
    assert all(c == wt.detect_command() for c in ctx.calls)


@pytest.mark.asyncio
async def test_without_own_setting_the_defaults_count():
    ctx = _ctx({}, {"h1": "@@samba\n[f]\npath = /home/familie\n@@end\n"})
    result = await wt.find_templates(ctx, _Defender([_host("h1", "nas")]))
    assert result["suggestions"][0]["covered_by"] == "/home"
    assert result["watch_paths"] == av.WATCH_PATHS


@pytest.mark.asyncio
async def test_suggestions_in_excluded_folders_are_marked_and_cannot_be_added():
    ctx = _ctx({"exclude_paths": ["/srv/medien/*"]}, {"h1": "@@nfs\n/srv/medien/upload *(rw)\n/srv/anders *(rw)\n@@end\n"})
    result = await wt.find_templates(ctx, _Defender([_host("h1", "nas")]))
    assert [(s["path"], s["excluded_by"]) for s in result["suggestions"]] == [("/srv/anders", None), ("/srv/medien/upload", "/srv/medien")]
    async with await _client(ctx) as client:
        r = await client.post("/defender/watch-paths", json={"path": "/srv/medien/upload"})
        assert r.status_code == 409 and "ausgenommenen Ordner (/srv/medien)" in r.json()["detail"]
    assert "watch_paths" not in ctx.settings.value and ctx.audit_entries == []


@pytest.mark.asyncio
async def test_a_cut_off_answer_is_named():
    ctx = _ctx({}, {"h1": "@@samba\n[a]\npath = /srv/a\n"})
    result = await wt.find_templates(ctx, _Defender([_host("h1", "nas")]))
    assert result["errors"] == ["nas: Antwort unvollständig, dort fehlt vielleicht etwas."]
    assert result["suggestions"][0]["path"] == "/srv/a"


async def _client(ctx) -> httpx.AsyncClient:
    app = FastAPI()
    read, change = wt.build_routers(ctx, _Defender([]))
    app.include_router(read)
    app.include_router(change)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_adding_a_folder_appends_it_once_and_writes_the_log():
    ctx = _ctx({"realtime_enabled": True}, {})
    async with await _client(ctx) as client:
        r = await client.post("/defender/watch-paths", json={"path": "/srv/samba/familie/"})
        assert r.status_code == 200, r.text
        assert r.json() == {"added": True, "covered_by": None, "watch_paths": [*av.WATCH_PATHS, "/srv/samba/familie"]}
        assert ctx.settings.value["watch_paths"][-1] == "/srv/samba/familie"
        assert ctx.settings.value["realtime_enabled"] is True, "andere Einstellungen bleiben"
        assert ctx.audit_entries[0]["action"] == "shield.watch_path_added"
        assert ctx.audit_entries[0]["detail"] == {"path": "/srv/samba/familie"}

        again = await client.post("/defender/watch-paths", json={"path": "/srv/samba/familie/unter"})
        assert again.json()["added"] is False and again.json()["covered_by"] == "/srv/samba/familie"
        assert len(ctx.audit_entries) == 1

        for bad in ("relativ", "/etc", "/", "/home/%U", "/srv/../etc"):
            assert (await client.post("/defender/watch-paths", json={"path": bad})).status_code == 400, bad
        assert ctx.settings.value["watch_paths"].count("/srv/samba/familie") == 1
