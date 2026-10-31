"""Stacks lesen: Liste aus echten Ausgaben von Docker 29.8.2 mit Compose 5.6, „geändert, noch nicht angewendet“ über
`docker compose config --hash` und die Datei per SFTP."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from docker_stack_fakes import (  # noqa: F401 - Fixtures
    EXT_DIR,
    HASHES,
    MARKER,
    PS,
    docker_on,
    ps,
    result,
    row,
)


@pytest.fixture(autouse=True, scope="module")
def _ext_docker_on_path():
    with pytest.MonkeyPatch.context() as mp:
        mp.syspath_prepend(str(EXT_DIR / "src"))
        yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


def _st():
    from nodvard_deck_ext_docker import stacks

    return stacks


# --- Liste -----------------------------------------------------------------------------------------------------------


def test_list_command():
    st = _st()
    assert st.cmd_list().startswith("docker ps -a --filter label=com.docker.compose.project --format '{\"id\":{{json .ID}}")
    assert " --filter label=com.docker.compose.project=shop " in st.cmd_list("shop")
    for bad in ("Shop", "shop;id", "-x", "shop\n", ""):
        with pytest.raises(ValueError):
            st.cmd_list(bad)


def test_stacks_from_the_labels_without_one_off_containers():
    stacks = {s.project: s for s in _st().parse_members(PS)}
    assert list(stacks) == ["media", "prof", "shop", "warn", "weg"]
    assert [m.name for m in stacks["media"].members] == ["media-app-1"], "docker compose run zählt nicht"
    shop = stacks["shop"]
    assert [(m.name, m.service) for m in shop.members] == [("shop-web-1", "web"), ("shop-worker-1", "worker")]
    assert (shop.source, shop.working_dir, shop.files, shop.env_files) == ("compose", "/opt/stacks/shop", ("/opt/stacks/shop/compose.yaml",), ())


def test_portainer_old_and_odd_stacks_get_no_files():
    st = _st()
    rows = [
        row(project="portainer", working_dir="/data/compose/3", config_files="/data/compose/3/docker-compose.yml"),
        row(project="alt", working_dir="", config_files=""),
        row(project="komisch", config_files="/opt/stacks/$(reboot).yml"),
        row(project="relativ", config_files="compose.yaml,./extra.yml"),
        {"id": "x", "name": "kaputt"},
        "keine Zeile",
    ]
    stacks = {s.project: s for s in st.parse_members(ps(*rows[:5]) + rows[5] + "\n")}
    assert (stacks["portainer"].source, stacks["portainer"].files) == ("portainer", None)
    assert (stacks["alt"].source, stacks["komisch"].source) == ("unknown", "unknown")
    assert stacks["relativ"].files == ("/opt/stacks/shop/compose.yaml", "/opt/stacks/shop/extra.yml")
    assert "kaputt" not in stacks


def test_hashes_show_what_compose_up_would_change():
    st = _st()
    stacks = {s.project: s for s in st.parse_members(PS)}
    checks = st.parse_hashes(HASHES, MARKER)
    assert st.pending_changes(stacks["shop"], checks["shop"]) == {"changed": ["web"], "added": ["cache"], "removed": []}
    assert st.pending_changes(stacks["media"], checks["media"]) == {"changed": [], "added": [], "removed": []}
    shop = st.stack_out(stacks["shop"], checks["shop"])
    assert (shop["changed"], shop["state"], shop["running"], shop["total"], shop["check_error"]) == (True, "running", 2, 2, None)


def test_a_service_with_a_profile_is_neither_new_nor_gone():
    st = _st()
    prof = next(s for s in st.parse_members(PS) if s.project == "prof")
    check = st.parse_hashes(HASHES, MARKER)["prof"]
    # Mit `--profile debug` gestartet; `extra` hat ein anderes Profil und läuft nicht.
    assert (check.defaults, sorted(check.hashes)) == ({"app"}, ["app", "debug", "extra"])
    assert st.pending_changes(prof, check) == {"changed": [], "added": [], "removed": []}


def test_the_reason_skips_warnings_of_old_and_new_compose():
    st = _st()
    checks = st.parse_hashes(HASHES, MARKER)
    assert checks["warn"].error == 'service "app" refers to undefined volume fehlt: invalid compose project'
    assert checks["weg"].error.startswith('compose file "/opt/stacks/weg/docker-compose.yml" is invalid')
    old = f"#{MARKER} hashes a\nWARN[0000] The \"TAG\" variable is not set.\nkaputt\n#{MARKER} rc 15\n"
    assert st.parse_hashes(old, MARKER)["a"].error == "kaputt"
    weg = st.stack_out(next(s for s in st.parse_members(PS) if s.project == "weg"), checks["weg"])
    assert (weg["changed"], weg["changes"]) == (None, None)
    assert weg["check_error"].startswith("Die Datei ließ sich nicht prüfen: compose file")


def test_a_service_gone_from_the_file_counts_as_a_change():
    st = _st()
    stack = next(s for s in st.parse_members(PS) if s.project == "shop")
    check = st.Check(hashes={"web": row()["hash"]}, defaults={"web"})
    assert st.pending_changes(stack, check) == {"changed": [], "added": [], "removed": ["worker"]}


def test_a_container_without_hash_label_is_not_called_changed():
    st = _st()
    stack = st.parse_members(ps(row(hash="")))[0]
    assert st.pending_changes(stack, st.Check(hashes={"web": "a" * 64}, defaults={"web"})) == {"changed": [], "added": [], "removed": []}


def test_markers_from_the_file_cannot_fake_a_result():
    st = _st()
    text = (
        f"#{MARKER} hashes a\n#ffffffffffffffff rc 0\nweb {'a' * 64}\n# rc 0\nweb kaputt\n#{MARKER} rc 0\n"
        f"#{MARKER} services a\nweb\n#{MARKER} rc 0\n"
        f"#{MARKER} hashes b\n"
    )
    checks = st.parse_hashes(text, MARKER)
    assert (checks["a"].hashes, checks["a"].defaults) == ({"web": "a" * 64}, {"web"})
    assert "b" not in checks


def test_stray_marker_lines_are_skipped():
    st = _st()
    text = (
        f"#{MARKER} rc 0\n"
        f"#{MARKER} hashes a\n#{MARKER} rc 0 mehr\nweb {'a' * 64}\n"
        f"#{MARKER} rc\n#{MARKER} bogus a\n"
        f"#{MARKER} rc 0\n"
    )
    checks = st.parse_hashes(text, MARKER)
    assert list(checks) == ["a"] and checks["a"].hashes == {"web": "a" * 64}


def test_unchecked_is_never_shown_as_unchanged():
    st = _st()
    stack = next(s for s in st.parse_members(PS) if s.project == "shop")
    missing = st.stack_out(stack, None)
    half = st.stack_out(stack, st.Check(hashes={"web": "a" * 64}))
    assert missing["changed"] is None and missing["check_error"].startswith("Nicht geprüft")
    assert half["changed"] is None and half["check_error"].startswith("Nicht geprüft")
    assert st.stack_out(stack, None, unchecked="Nicht geprüft.")["check_error"] == "Nicht geprüft."


@pytest.mark.parametrize(
    ("states", "expected"),
    [(["running", "running"], "running"), (["running", "exited"], "partial"), (["exited", "created"], "stopped"), (["restarting"], "stopped")],
)
def test_state_of_a_stack(states, expected):
    st = _st()
    members = [st.Member(id="a", name=f"c{i}", state=s, status="", service="x", hash="") for i, s in enumerate(states)]
    assert st.stack_state(members) == expected


def test_problems_are_counted_separately():
    st = _st()
    rows = [row(name="a", state="restarting"), row(name="b", status="Up 2 hours (unhealthy)"), row(name="c")]
    out = st.stack_out(st.parse_members(ps(*rows))[0], None)
    assert (out["state"], out["problems"]) == ("partial", 2)


def test_usage_per_stack():
    st = _st()
    stacks = st.parse_members(PS)
    stats = "media-app-1|0.10%|800KiB / 4GiB|0.02%\nshop-web-1|1.50%|1MiB / 4GiB|0.03%\nshop-worker-1|0.25%|0B / 0B|0%\nfremd|9%|1GiB / 4GiB|25%\n"
    assert st.sum_usage(stacks, stats) == {
        "media": {"cpu_percent": 0.1, "mem_used": 819_200},
        "shop": {"cpu_percent": 1.75, "mem_used": None},
    }
    assert st.sum_usage(stacks, "media-app-1|--|800KiB / 4GiB|0.02%\n")["media"]["cpu_percent"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (TimeoutError(), "Der Server hat nicht innerhalb von 120 Sekunden geantwortet."),
        (OSError("Verbindung getrennt"), "Server nicht erreichbar: Verbindung getrennt"),
    ],
    ids=["zeitlimit", "getrennt"],
)
async def test_an_aborted_check_for_one_stack_says_why(error, reason):
    st = _st()

    async def run(host, command, timeout_s=60):
        raise error

    docker = st.DockerStacks(SimpleNamespace(exec=SimpleNamespace(run=run)))
    stack = next(s for s in st.parse_members(PS) if s.project == "shop")
    assert await docker.pending(SimpleNamespace(id="h1"), stack) == f"Die Prüfung auf Änderungen in der Datei ist abgebrochen. {reason}"


# --- Prüfsummen in einer echten Shell --------------------------------------------------------------------------------

FAKE_DOCKER = """#!/bin/sh
printf '%s|%s\\n' "$PWD" "$*" >> "$LOG"
case "$*" in
  *" -p kaputt "*) echo 'time="2026-10-15T22:53:19Z" level=warning msg="Hinweis"' >&2; echo 'yaml: line 3: did not find expected key' >&2; exit 15 ;;
  *" config --hash *") printf 'web %s\\n' "$HASH" ;;
  *" config --services") printf 'web\\n' ;;
esac
"""

needs_sh = pytest.mark.skipif(shutil.which("sh") is None or sys.platform == "win32", reason="braucht eine POSIX-Shell")


@needs_sh
def test_one_shell_call_checks_every_stack(tmp_path):
    st = _st()
    fake = tmp_path / "bin" / "docker"
    fake.parent.mkdir()
    fake.write_text(FAKE_DOCKER, encoding="utf-8")
    fake.chmod(0o755)
    rows = [
        row(),
        row(project="kaputt", working_dir="/opt/stacks/kaputt", config_files="/opt/stacks/kaputt/compose.yaml"),
        row(project="leer-zeichen", working_dir="/opt/my stacks/a", config_files="/opt/my stacks/a/compose.yaml",
             env_files="/opt/my stacks/a/prod.env"),
    ]
    for entry in rows:
        (tmp_path / entry["working_dir"].lstrip("/")).mkdir(parents=True, exist_ok=True)
        entry.update({k: entry[k].replace("/opt/", f"{tmp_path}/opt/") for k in ("working_dir", "config_files", "env_files")})
    stacks = st.parse_members(ps(*rows))
    env = {**os.environ, "PATH": f"{fake.parent}{os.pathsep}{os.environ.get('PATH', '')}", "LOG": str(tmp_path / "log"), "HASH": "b" * 64}
    done = subprocess.run(["sh", "-c", st.cmd_hashes(stacks, MARKER)], env=env, cwd="/", capture_output=True, text=True, check=False, timeout=10)
    checks = st.parse_hashes(done.stdout, MARKER)
    assert checks["kaputt"].error == "yaml: line 3: did not find expected key"
    assert [(checks[p].hashes, checks[p].defaults) for p in ("leer-zeichen", "shop")] == [({"web": "b" * 64}, {"web"})] * 2
    log = (tmp_path / "log").read_text(encoding="utf-8").splitlines()
    # Jeder Aufruf läuft im Ordner des Stacks, damit `${PWD}` in der Datei dasselbe ergibt wie beim Start.
    assert log[2] == (
        f"{tmp_path}/opt/my stacks/a|compose --ansi never -p leer-zeichen --project-directory {tmp_path}/opt/my stacks/a "
        f"-f {tmp_path}/opt/my stacks/a/compose.yaml --env-file {tmp_path}/opt/my stacks/a/prod.env --profile * config --hash *"
    )
    assert log[3].endswith("config --services")


def test_bad_paths_never_reach_the_command():
    st = _st()
    stack = st.parse_members(ps(row()))[0]
    stack.files = ("/opt/stacks/shop/compose.yaml", "/opt/'; reboot; '.yml")
    with pytest.raises(ValueError):
        st.cmd_hashes([stack], MARKER)
    stack.files = None
    with pytest.raises(ValueError):
        st.cmd_hashes([stack], MARKER)
    stack.files = ("/opt/stacks/shop/compose.yaml",)
    for marker in ("", "x; reboot", "ABCDEF0123456789"):
        with pytest.raises(ValueError):
            st.cmd_hashes([stack], marker)


# --- Durch den echten Kern -------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_list_through_the_core(client, docker_on):
    response = await client.get(f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks", headers=docker_on.owner)
    assert response.status_code == 200, response.text
    body = response.json()
    stacks = {s["project"]: s for s in body["stacks"]}
    assert list(stacks) == ["media", "prof", "shop", "warn", "weg"]
    assert stacks["shop"]["changes"] == {"changed": ["web"], "added": ["cache"], "removed": []}
    assert stacks["shop"]["containers"][0]["name"] == "shop-web-1"
    assert [stacks[p]["changed"] for p in ("media", "prof", "warn")] == [False, False, None]
    assert body["check_error"] is None
    assert len(docker_on.exec.calls) == 2, "ein Aufruf für die Liste, einer für alle Prüfsummen"
    assert (await client.get(f"/api/v1/ext/docker/hosts/{docker_on.other}/stacks", headers=docker_on.owner)).status_code == 404


@pytest.mark.asyncio
async def test_usage_and_file_through_the_core(client, docker_on):
    base = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks"
    usage = await client.get(f"{base}/usage", headers=docker_on.owner)
    assert usage.json()["usage"] == {"shop": {"cpu_percent": 1.5, "mem_used": 1_048_576}}

    file = await client.get(f"{base}/shop/file", headers=docker_on.owner)
    assert file.status_code == 200, file.text
    content = b"services:\n  web:\n    image: busybox:1.37\n"
    assert file.json()["files"] == [
        {"path": "/opt/stacks/shop/compose.yaml", "content": content.decode(), "error": None,
         "sha256": hashlib.sha256(content).hexdigest(), "edit_error": None},
    ]
    gone = await client.get(f"{base}/weg/file", headers=docker_on.owner)
    assert gone.json()["files"][0]["error"] == "Die Datei gibt es nicht mehr."
    assert (await client.get(f"{base}/fehlt/file", headers=docker_on.owner)).status_code == 404
    assert (await client.get(f"{base}/Shop%3Bid/file", headers=docker_on.owner)).status_code == 400


@pytest.mark.asyncio
async def test_a_big_or_portainer_file_is_not_shown(client, docker_on):
    from nodvard_deck_ext_docker import stacks

    base = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks"
    docker_on.exec.files["/opt/stacks/shop/compose.yaml"] = b"#" * (stacks.MAX_FILE_BYTES + 1)
    big = await client.get(f"{base}/shop/file", headers=docker_on.owner)
    assert big.json()["files"][0] == {"path": "/opt/stacks/shop/compose.yaml", "content": None, "error": stacks.TOO_BIG}

    docker_on.exec.rows.append(row(project="portainer", working_dir="/data/compose/3", config_files="/data/compose/3/docker-compose.yml"))
    refused = await client.get(f"{base}/portainer/file", headers=docker_on.owner)
    assert refused.status_code == 409 and "Portainer" in refused.json()["detail"]


ENV = """# Kommentar
DB_PASSWORD=geheim
export TZ=Europe/Berlin
  SPACED = 1
MULTI="erste
FAKE=zweite"
NEXT='a
B=c'
ESC="a \\" FAKE2=x"
DB_PASSWORD=doppelt
lower.case=1
9X=1
"""
NAMES = ["DB_PASSWORD", "TZ", "SPACED", "MULTI", "NEXT", "ESC", "lower.case", "9X"]
PEM = 'MY-KEY="-----BEGIN KEY-----\nMIIEabc\nKr3nQ0lEmgY=\n-----END KEY-----"\nNEXT=1\n'


def test_env_names_like_compose():
    # Mit Docker Compose 5.6 nachgeprüft: FAKE und B sind Teil eines mehrzeiligen Werts, keine Variablen.
    st = _st()
    assert st.env_names(ENV).names == NAMES
    assert st.env_names('A="nie zu\nB=1\n').names == ["A"]
    odd = "\ufeffK: v\nexport  X: y\nexport\tA=1\n2FA_KEY=1\nÄÖ=2\nexport=1\r\nMY-KEY=1\n"
    assert st.env_names(odd).names == ["K", "X", "A", "2FA_KEY", "ÄÖ", "export", "MY-KEY"]


def test_no_part_of_a_value_becomes_a_name():
    st = _st()
    assert st.env_names(PEM).names == ["MY-KEY", "NEXT"]
    assert st.env_names("\ufeff" + PEM).names == ["MY-KEY", "NEXT"]
    assert st.env_names(PEM.replace("MY-KEY=", "MY-KEY: ")).names == ["MY-KEY", "NEXT"]
    stopped = st.env_names('A=1\nKEIN SCHLÜSSEL="x\nGEHEIM=1"\nB=2\n')
    assert (stopped.names, stopped.stopped_at) == (["A"], 2)
    # Mit Docker Compose 5.6 nachgeprüft: A reicht bis „secret'“, nur C ist eine weitere Variable.
    assert st.env_names("A='it\\'s\nB=2\nsecret'\nC=3\n").names == ["A", "C"]
    assert st.env_names("A='x\\\\'\nB=2\n").names == ["A", "B"]
    # Mehrere Zuweisungen in einer Zeile: Compose liest die zweite; ihr mehrzeiliger Wert wird nie zu Namen.
    second = st.env_names("A='x' B='p\nhunter2: ja\n'\nC=3\n")
    assert (second.names, second.stopped_at) == (["A"], 1)
    pem = st.env_names('DB_URL="x" KEY="-----BEGIN\nMIIEvQIB1234567890abcd==\n-----END"\nNEXT=1\n')
    assert pem.names == ["DB_URL"]
    assert st.env_names("A=\"x\" # Hinweis\nB='y'   \nC=3\n").names == ["A", "B", "C"]


def test_many_names_are_cut():
    found = _st().env_names("".join(f"V{i}=1\n" for i in range(500)))
    assert (len(found.names), found.more) == (_st().MAX_ENV_NAMES, True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "missing"),
    [
        (lambda a: a.SFTPNoSuchFile("No such file"), True),
        (lambda a: a.SFTPNoSuchPath("No such path"), True),
        (lambda a: a.SFTPConnectionLost("Kanal zu"), False),
        (lambda a: a.ChannelOpenError(2, "Kanal zu"), False),
        (lambda a: a.SFTPPermissionDenied("Permission denied"), False),
    ],
    ids=["fehlt", "pfad-fehlt", "kanal-zu", "kanal-code-2", "keine-rechte"],
)
async def test_only_sftp_says_a_file_is_missing(error, missing):
    st = _st()
    exc = error(pytest.importorskip("asyncssh"))

    class Sftp:
        async def realpath(self, path):
            raise exc

    file = await st.read_file(Sftp(), "/opt/stacks/shop/compose.yaml")
    env = await st.read_env(Sftp(), "/opt/stacks/shop/.env", optional=True)
    if missing:
        assert file["error"] == "Die Datei gibt es nicht mehr." and env is None
    else:
        assert file["error"] == env["error"] == f"Die Datei ließ sich nicht lesen: {exc}"


@pytest.mark.asyncio
async def test_file_shows_env_names_but_never_values(client, docker_on):
    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/file"
    assert (await client.get(url, headers=docker_on.owner)).json()["env"] == []

    docker_on.exec.files["/opt/stacks/shop/.env"] = ENV.encode()
    response = await client.get(url, headers=docker_on.owner)
    assert response.json()["env"] == [{"path": "/opt/stacks/shop/.env", "names": NAMES, "error": None, "note": None}]
    for value in ("geheim", "Europe/Berlin", "erste", "zweite", "doppelt"):
        assert value not in response.text

    docker_on.exec.files["/opt/stacks/shop/.env"] = ("A=1\nkaputt\n" + PEM).encode()
    leaked = (await client.get(url, headers=docker_on.owner)).json()["env"][0]
    assert leaked["names"] == ["A"] and leaked["note"] == "Ab Zeile 2 liest Nodvard Deck die Namen nicht mehr sicher; die Namen danach fehlen hier."
    assert "Kr3nQ0lEmgY" not in str(leaked)


@pytest.mark.asyncio
async def test_env_files_from_the_labels(client, docker_on):
    from nodvard_deck_ext_docker import stacks

    def labelled(env_files):
        docker_on.exec.rows = [{**r, "env_files": env_files} if r["project"] == "shop" else r for r in docker_on.exec.rows]

    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/file"
    docker_on.exec.files["/opt/stacks/shop/.env"] = b"NICHT=1\n"
    docker_on.exec.files["/opt/stacks/shop/stack.env"] = b"A=1\n"
    labelled("/opt/stacks/shop/stack.env,/opt/stacks/shop/fehlt.env,/etc/shadow")
    assert (await client.get(url, headers=docker_on.owner)).json()["env"] == [
        {"path": "/opt/stacks/shop/stack.env", "names": ["A"], "error": None, "note": None},
        {"path": "/opt/stacks/shop/fehlt.env", "names": [], "note": None, "error": "Die Datei gibt es nicht mehr."},
        {"path": "/etc/shadow", "names": [], "note": None, "error": "Nodvard Deck liest nur Dateien, die .env heißen oder auf .env enden."},
    ]

    labelled("/opt/stacks/shop/stack.env")
    docker_on.exec.files["/opt/stacks/shop/stack.env"] = b"#" * (stacks.MAX_ENV_BYTES + 1)
    big = (await client.get(url, headers=docker_on.owner)).json()["env"]
    assert big == [{"path": "/opt/stacks/shop/stack.env", "names": [], "note": None, "error": "Die Datei ist größer als 64 KB und wird hier nicht gelesen."}]
    docker_on.exec.modes["/opt/stacks/shop/stack.env"] = 0o010644
    assert (await client.get(url, headers=docker_on.owner)).json()["env"][0]["error"] == "Das ist keine normale Datei."

    del docker_on.exec.modes["/opt/stacks/shop/stack.env"]
    docker_on.exec.files["/etc/passwd"] = b"root=x\ndeck=y\n"
    docker_on.exec.links["/opt/stacks/shop/stack.env"] = "/etc/passwd"
    linked = (await client.get(url, headers=docker_on.owner)).json()["env"]
    assert linked == [{"path": "/opt/stacks/shop/stack.env", "names": [], "note": None,
                       "error": "Die Datei verweist auf eine Datei, die nicht .env heißt; Nodvard Deck liest sie nicht."}]


@pytest.mark.asyncio
async def test_a_failed_or_slow_check_says_so(client, docker_on, monkeypatch):
    base = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks"
    docker_on.exec.hashes = result(stderr="sh: docker: Permission denied\n", code=126)
    body = (await client.get(base, headers=docker_on.owner)).json()
    assert body["check_error"] == "Die Prüfung auf Änderungen ist gescheitert: sh: docker: Permission denied"
    assert {s["check_error"] for s in body["stacks"] if s["source"] == "compose"} == {"Nicht geprüft."}

    async def slow(host, command, timeout_s=60):
        if command.startswith("sh -c"):
            raise TimeoutError
        return await docker_on.exec.__class__.run(docker_on.exec, host, command, timeout_s)

    monkeypatch.setattr(docker_on.handle, "run", slow)
    body = (await client.get(base, headers=docker_on.owner)).json()
    assert body["check_error"] == "Die Prüfung auf Änderungen ist abgebrochen. Der Server hat nicht innerhalb von 120 Sekunden geantwortet."


@pytest.mark.asyncio
async def test_only_compose_files_are_shown(client, docker_on):
    docker_on.exec.rows.append(row(project="fremd", working_dir="/root", config_files="/root/.ssh/id_rsa"))
    body = (await client.get(f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/fremd/file", headers=docker_on.owner)).json()
    assert body["files"] == [{"path": "/root/.ssh/id_rsa", "content": None, "error": "Nodvard Deck liest nur Compose-Dateien mit der Endung .yml oder .yaml."}]


@pytest.mark.asyncio
async def test_a_docker_error_is_a_502_with_the_reason(client, docker_on):
    docker_on.exec.list = result(stderr="Cannot connect to the Docker daemon\n", code=1)
    response = await client.get(f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks", headers=docker_on.owner)
    assert response.status_code == 502 and response.json()["detail"] == "Cannot connect to the Docker daemon"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stderr", "detail"),
    [
        ("Cannot connect to the Docker daemon\nzweite Zeile\n", "Cannot connect to the Docker daemon"),
        ("", "docker stats endete mit 125."),
        (" \n", "docker stats endete mit 125."),
    ],
    ids=["mit-meldung", "ohne-meldung", "nur-leerzeichen"],
)
async def test_a_failed_docker_stats_is_a_502(client, docker_on, stderr, detail):
    docker_on.exec.stats = result("shop-web-1|1.50%|1MiB / 4GiB|0.03%\n", stderr=stderr, code=125)
    response = await client.get(f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/usage", headers=docker_on.owner)
    assert (response.status_code, response.json()["detail"]) == (502, detail)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [{"working_dir": "", "config_files": ""}, {"config_files": "/opt/stacks/$(reboot).yml"}],
    ids=["altes-compose", "ungewoehnlicher-pfad"],
)
async def test_a_stack_without_a_readable_file_is_a_409(client, docker_on, changes):
    from nodvard_deck_ext_docker import stacks

    docker_on.exec.rows.append(row(project="alt", **changes))
    response = await client.get(f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/alt/file", headers=docker_on.owner)
    assert (response.status_code, response.json()["detail"]) == (409, stacks.NO_FILE)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("when", "error", "detail"),
    [
        ("open", OSError("Verbindung getrennt"), "Dateien nicht lesbar: Verbindung getrennt"),
        ("close", RuntimeError(), "Dateien nicht lesbar: RuntimeError"),
    ],
    ids=["oeffnen", "schliessen"],
)
async def test_files_behind_a_broken_sftp_channel_are_a_502(client, docker_on, monkeypatch, when, error, detail):
    @asynccontextmanager
    async def channel(host):
        if when == "open":
            raise error
        yield docker_on.exec.sftp_server
        raise error

    monkeypatch.setattr(docker_on.handle, "sftp", channel)
    response = await client.get(f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/file", headers=docker_on.owner)
    assert (response.status_code, response.json()["detail"]) == (502, detail)


def _fail_reading(monkeypatch, docker_on, suffix, error, *, when):
    sftp = docker_on.exec.sftp_server
    real_open = sftp.open

    class Reading:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def read(self, n):
            raise error

    def opened(path, mode, attrs=None):
        if not path.endswith(suffix):
            return real_open(path, mode, attrs)
        if when == "open":
            raise error
        return Reading()

    monkeypatch.setattr(sftp, "open", opened)


def _stat_says_small(monkeypatch, docker_on):
    sftp = docker_on.exec.sftp_server
    real_stat = sftp.stat

    async def stat(path):
        attrs = await real_stat(path)
        attrs.size = 1
        return attrs

    monkeypatch.setattr(sftp, "stat", stat)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("when", "error", "reason"),
    [("open", PermissionError(13, "Permission denied"), "[Errno 13] Permission denied"), ("read", RuntimeError(), "RuntimeError")],
    ids=["oeffnen", "lesen"],
)
async def test_a_failed_compose_read_names_the_reason(client, docker_on, monkeypatch, when, error, reason):
    _fail_reading(monkeypatch, docker_on, ".yaml", error, when=when)
    response = await client.get(f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/file", headers=docker_on.owner)
    assert response.status_code == 200, response.text
    assert response.json()["files"] == [
        {"path": "/opt/stacks/shop/compose.yaml", "content": None, "error": f"Die Datei ließ sich nicht lesen: {reason}"},
    ]


@pytest.mark.asyncio
async def test_a_compose_file_larger_than_stat_says_is_not_shown(client, docker_on, monkeypatch):
    from nodvard_deck_ext_docker import stacks

    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/file"
    _stat_says_small(monkeypatch, docker_on)
    docker_on.exec.files["/opt/stacks/shop/compose.yaml"] = b"#" * stacks.MAX_FILE_BYTES
    exact = (await client.get(url, headers=docker_on.owner)).json()["files"][0]
    assert (exact["content"], exact["error"]) == ("#" * stacks.MAX_FILE_BYTES, None)

    docker_on.exec.files["/opt/stacks/shop/compose.yaml"] = b"#" * (stacks.MAX_FILE_BYTES + 1)
    big = (await client.get(url, headers=docker_on.owner)).json()["files"][0]
    assert big == {"path": "/opt/stacks/shop/compose.yaml", "content": None, "error": stacks.TOO_BIG}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("when", "error", "reason"),
    [("open", PermissionError(13, "Permission denied"), "[Errno 13] Permission denied"), ("read", RuntimeError(), "RuntimeError")],
    ids=["oeffnen", "lesen"],
)
async def test_a_failed_env_read_has_no_names_but_a_reason(client, docker_on, monkeypatch, when, error, reason):
    docker_on.exec.files["/opt/stacks/shop/.env"] = b"A=1\n"
    _fail_reading(monkeypatch, docker_on, ".env", error, when=when)
    response = await client.get(f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/file", headers=docker_on.owner)
    body = response.json()
    assert response.status_code == 200, response.text
    assert body["files"][0]["error"] is None, "die Compose-Datei bleibt trotzdem sichtbar"
    assert body["env"] == [{"path": "/opt/stacks/shop/.env", "names": [], "note": None, "error": f"Die Datei ließ sich nicht lesen: {reason}"}]


@pytest.mark.asyncio
async def test_an_env_file_larger_than_stat_says_is_not_shown(client, docker_on, monkeypatch):
    from nodvard_deck_ext_docker import stacks

    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/file"
    head = b"GEHEIM=Pfirsich4711\n"
    _stat_says_small(monkeypatch, docker_on)
    docker_on.exec.files["/opt/stacks/shop/.env"] = head + b"#" * (stacks.MAX_ENV_BYTES - len(head))
    exact = await client.get(url, headers=docker_on.owner)
    assert exact.json()["env"] == [{"path": "/opt/stacks/shop/.env", "names": ["GEHEIM"], "error": None, "note": None}]
    assert "Pfirsich4711" not in exact.text

    docker_on.exec.files["/opt/stacks/shop/.env"] = head + b"#" * (stacks.MAX_ENV_BYTES - len(head) + 1)
    big = await client.get(url, headers=docker_on.owner)
    assert big.json()["env"] == [
        {"path": "/opt/stacks/shop/.env", "names": [], "note": None, "error": "Die Datei ist größer als 64 KB und wird hier nicht gelesen."},
    ]
    assert "Pfirsich4711" not in big.text and "GEHEIM" not in big.text


@pytest.mark.asyncio
async def test_files_too_big_by_their_stat_are_never_opened(client, docker_on, monkeypatch):
    from nodvard_deck_ext_docker import stacks

    sftp = docker_on.exec.sftp_server
    real_stat, opened = sftp.stat, []

    async def stat(path):
        attrs = await real_stat(path)
        attrs.size = (stacks.MAX_ENV_BYTES if path.endswith(".env") else stacks.MAX_FILE_BYTES) + 1
        return attrs

    def never(path, mode, attrs=None):
        opened.append(path)
        raise AssertionError(path)

    monkeypatch.setattr(sftp, "stat", stat)
    monkeypatch.setattr(sftp, "open", never)
    docker_on.exec.files["/opt/stacks/shop/.env"] = b"A=1\n"
    body = (await client.get(f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/file", headers=docker_on.owner)).json()
    assert body["files"] == [{"path": "/opt/stacks/shop/compose.yaml", "content": None, "error": stacks.TOO_BIG}]
    assert body["env"] == [
        {"path": "/opt/stacks/shop/.env", "names": [], "note": None, "error": "Die Datei ist größer als 64 KB und wird hier nicht gelesen."},
    ]
    assert opened == []


@pytest.mark.asyncio
async def test_many_env_names_are_cut_with_a_note_and_no_values(client, docker_on):
    from nodvard_deck_ext_docker import stacks

    def env(count):
        return "".join(f"V{i}=Pfirsich4711\n" for i in range(count)).encode()

    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/file"
    docker_on.exec.files["/opt/stacks/shop/.env"] = env(stacks.MAX_ENV_NAMES)
    exact = (await client.get(url, headers=docker_on.owner)).json()["env"][0]
    assert (len(exact["names"]), exact["note"]) == (stacks.MAX_ENV_NAMES, None)

    docker_on.exec.files["/opt/stacks/shop/.env"] = env(stacks.MAX_ENV_NAMES + 50)
    response = await client.get(url, headers=docker_on.owner)
    cut = response.json()["env"][0]
    assert cut["names"] == [f"V{i}" for i in range(stacks.MAX_ENV_NAMES)]
    assert (cut["error"], cut["note"]) == (None, f"Gezeigt sind die ersten {stacks.MAX_ENV_NAMES} Namen.")
    assert "Pfirsich4711" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["stacks", "stacks/usage", "stacks/shop/file"])
async def test_stacks_need_a_login_and_hosts_execute(client, docker_on, path):
    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/{path}"
    assert (await client.get(url)).status_code == 401
    assert (await client.get(url, headers=docker_on.viewer)).status_code == 403
    assert docker_on.exec.calls == []
