"""Private Registries: Zugangsdaten im Tresor, Anmelden je Server mit dem Passwort über die Standardeingabe."""

from __future__ import annotations

import json
import re
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from docker_stack_fakes import (  # noqa: F401 - Fixtures
    EXT_DIR,
    FakeExec,
    docker_on,
    result,
)

# Zur Laufzeit zusammengesetzt, damit kein Scanner die Attrappe für ein echtes Token hält.
TOKEN_PREFIX = "ghp"
PASSWORD = f"{TOKEN_PREFIX}_geheim-0123456789"


@pytest.fixture(autouse=True)
def _ext_docker_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_DIR / "src"))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


def _mod():
    from nodvard_deck_ext_docker import registries

    return registries


ENTRIES = [
    {"name": "ghcr", "address": "ghcr.io", "username": "nico"},
    {"name": "hub", "address": "docker.io", "username": "nico"},
    {"name": "lab", "address": "registry.lab:5000", "username": "-deck"},
]


class Secrets:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    async def exists(self, label):
        return label in self.values

    async def get_handle(self, label):
        from nodvard_sdk.errors import SecretUnavailable

        if label not in self.values:
            raise SecretUnavailable(label)
        return label


def _ctx(entries=ENTRIES, secrets=None, exec_=None):
    secrets = Secrets({"docker-registry-pw:ghcr": PASSWORD} if secrets is None else secrets)

    @asynccontextmanager
    async def vault_use(handle):
        yield secrets.values[handle]

    fake = exec_ or _without_config(FakeExec())
    hosts = SimpleNamespace(get=lambda host_id: _async(SimpleNamespace(id=host_id, tags=["docker"]) if host_id in ("h1", "nas") else None))
    settings = SimpleNamespace(get=lambda: _async({"registries": entries}))
    return SimpleNamespace(exec=fake, hosts=hosts, settings=settings, secrets=secrets, vault_use=vault_use)


async def _async(value):
    return value


def _request(action="docker.registry_login", registry="ghcr", address="ghcr.io", actor="user", host="h1", username="nico"):
    from nodvard_sdk.actions import ActionRequest
    from nodvard_sdk.types import Actor, Risk

    payload = {"registry": registry, "address": address, **({} if username is None else {"username": username})}
    return ActionRequest(action_type=action, host_ref=host, risk=Risk.MEDIUM, proposed_by=Actor(type=actor, id="u1"), reason="Test",
                         payload=payload)


class SFTPNoSuchFile(Exception):
    """Wie asyncssh: kein OSError; erkannt wird die Klasse, denn Code 2 hat auch ein abgerissener Kanal."""

    code = 2


def _without_config(fake: FakeExec) -> FakeExec:
    reader = fake.sftp_server._reader

    async def read(path):
        if path not in fake.files:
            raise SFTPNoSuchFile(path)
        return await reader(path)

    fake.sftp_server._reader = read
    return fake


def _recording(fake: FakeExec, answer=None):
    seen: list[tuple[list[str], bytes | None]] = []

    async def run_argv(host, argv, *, stdin=None, timeout_s=60, log_command=None):
        seen.append((list(argv), stdin))
        return answer or result("Login Succeeded\n")

    fake.run_argv = run_argv
    return seen


def test_commands_keep_the_password_off_the_command_line():
    r = _mod()
    ghcr, hub, lab = (r.Registry(**e) for e in ENTRIES)
    assert r.cmd_login(ghcr) == ["docker", "login", "--username=nico", "--password-stdin", "ghcr.io"]
    assert r.cmd_login(hub) == ["docker", "login", "--username=nico", "--password-stdin"]
    assert r.cmd_login(lab) == ["docker", "login", "--username=-deck", "--password-stdin", "registry.lab:5000"]
    assert r.cmd_logout(ghcr) == ["docker", "logout", "ghcr.io"] and r.cmd_logout(hub) == ["docker", "logout"]
    for bad in (r.Registry("x", "https://ghcr.io", "nico"), r.Registry("x", "-H", "nico"), r.Registry("x", "ghcr.io", "a b")):
        with pytest.raises(ValueError):
            r.cmd_login(bad)


def test_logout_refuses_a_bad_address():
    r = _mod()
    for address in ("https://ghcr.io", "-H", "ghcr.io/pfad", "GHCR.io", ""):
        with pytest.raises(ValueError):
            r.cmd_logout(r.Registry("x", address, "deck"))


def test_only_valid_entries_count():
    r = _mod()
    raw = [*ENTRIES, {"name": "mit leer", "address": "a.io", "username": "u"}, {"name": "x", "address": "GHCR.io", "username": "u"},
           {"name": "y", "address": "a.io/pfad", "username": "u"}, {"name": "z", "address": "a.io", "username": ""}, "kaputt"]
    assert [e.name for e in r.registries({"registries": raw})] == ["ghcr", "hub", "lab"]
    assert r.registries({}) == []


def test_a_port_lies_between_1_and_65535():
    r = _mod()
    field = json.loads((EXT_DIR / "settings.schema.json").read_text())["properties"]["registries"]["items"]["properties"]["address"]
    assert field["pattern"].removesuffix("$") == r._ADDRESS_RE.pattern.removesuffix("\\Z")
    schema = re.compile(field["pattern"])
    for port in [*map(str, range(70000)), "", "00", "05000", "065535", "+80", " 80", "80 ", "\u0668\u0660"]:
        ok = port.isascii() and port.isdigit() and not port.startswith("0") and 1 <= int(port) <= 65535
        address = f"registry.lab:{port}"
        assert bool(r._ADDRESS_RE.match(address)) is ok and bool(schema.fullmatch(address)) is ok, port
    assert [e.name for e in r.registries({"registries": [{"name": "lab", "address": "registry.lab:65536", "username": "u"}]})] == []


def test_logged_in_reads_only_the_keys():
    r = _mod()
    config = {
        "auths": {"https://index.docker.io/v1/": {"auth": "c2VjcmV0"}, "ghcr.io": {}, "registry.lab:5000": {"auth": "eA=="}},
        "credHelpers": {"123.dkr.ecr.eu-central-1.amazonaws.com": "ecr-login"},
        "credsStore": "pass",
    }
    found, helped, store = r.logged_in(config)
    assert found == {"docker.io", "registry.lab:5000"} and store == "pass"
    assert helped == {"123.dkr.ecr.eu-central-1.amazonaws.com"}
    assert r.logged_in({"auths": "kaputt"}) == (set(), set(), None)


@pytest.mark.parametrize("key", ["https://index.docker.io/v1/", "index.docker.io", "docker.io", "registry-1.docker.io", "https://docker.io"])
def test_every_way_docker_writes_the_hub(key):
    assert _mod().logged_in({"auths": {key: {"auth": "eA=="}}})[0] == {"docker.io"}


def test_an_empty_entry_is_no_login():
    r = _mod()
    auths = {"https://index.docker.io/v1/": {}, "docker.io": {"auth": ""}, "ghcr.io": {"identitytoken": "x"}, "lab.io": "kaputt"}
    assert r.logged_in({"auths": auths}) == ({"ghcr.io"}, set(), None)
    assert r.logged_in({"auths": auths, "credsStore": "pass"}) == ({"ghcr.io"}, set(), "pass")


def test_a_name_twice_drops_all_its_entries():
    r = _mod()
    settings = {"registries": [{"name": "ghcr", "address": "evil.example", "username": "nico"},
                               {"name": "ghcr", "address": "ghcr.io", "username": "nico"},
                               {"name": "lab", "address": "registry.lab:5000", "username": "deck"}]}
    assert [e.name for e in r.registries(settings)] == ["lab"] and r.duplicate_names(settings) == {"ghcr"}


@pytest.mark.asyncio
async def test_a_name_twice_never_logs_in_and_says_why():
    r = _mod()
    entries = [{"name": "ghcr", "address": "evil.example", "username": "nico"}, *ENTRIES]
    ctx = _ctx(entries=entries)
    seen = _recording(ctx.exec)
    done = await r.DockerRegistryExecutor(ctx, r.Registries(ctx)).execute(_request(address="evil.example"))
    again = await r.DockerRegistryExecutor(ctx, r.Registries(ctx)).execute(_request())
    assert done.success is again.success is False and seen == []
    state = await r.Registries(ctx).state(SimpleNamespace(id="h1"))
    assert "ghcr" not in [e["name"] for e in state["registries"]]
    assert state["warnings"] == ["Der Name „ghcr“ steht mehrmals in den Einstellungen. Deck nutzt keinen dieser Einträge, bis jeder Name nur einmal vorkommt."]


@pytest.mark.asyncio
async def test_a_registry_with_its_own_helper_is_unknown():
    r = _mod()
    fake = _without_config(FakeExec())
    fake.files["/home/deck/.docker/config.json"] = json.dumps({"credHelpers": {"ghcr.io": "gcloud"}}).encode()
    state = await r.Registries(_ctx(exec_=fake)).state(SimpleNamespace(id="h1"))
    assert [e["logged_in"] for e in state["registries"]] == [None, False, False]


@pytest.mark.asyncio
async def test_a_server_without_the_docker_mark_is_refused():
    r = _mod()
    ctx = _ctx()
    ctx.hosts = SimpleNamespace(get=lambda host_id: _async(SimpleNamespace(id=host_id, tags=["nas"])))
    seen = _recording(ctx.exec)
    done = await r.DockerRegistryExecutor(ctx, r.Registries(ctx)).execute(_request())
    assert done.error == "Dieser Server ist kein Docker-Server (Markierung fehlt)." and seen == []


@pytest.mark.asyncio
async def test_a_server_switched_to_operate_only_says_so():
    r = _mod()
    ctx = _ctx()
    ctx.hosts = SimpleNamespace(get=lambda host_id: _async(SimpleNamespace(id=host_id, tags=["docker-bedienen"])))
    seen = _recording(ctx.exec)
    done = await r.DockerRegistryExecutor(ctx, r.Registries(ctx)).execute(_request())
    assert done.error.startswith("Auf diesem Server ist Docker im Modus „Nur bedienen“") and seen == []


@pytest.mark.asyncio
async def test_the_state_per_server(tmp_path):
    r = _mod()
    fake = _without_config(FakeExec())
    ctx = _ctx(exec_=fake)
    state = await r.Registries(ctx).state(SimpleNamespace(id="h1"))
    assert [(e["name"], e["logged_in"], e["secret_set"]) for e in state["registries"]] == [("ghcr", False, True), ("hub", False, False), ("lab", False, False)]
    config = {"auths": {"ghcr.io": {"auth": "c2VjcmV0"}, "https://index.docker.io/v1/": {}}}
    fake.files["/home/deck/.docker/config.json"] = json.dumps(config).encode()
    state = await r.Registries(ctx).state(SimpleNamespace(id="h1"))
    assert [e["logged_in"] for e in state["registries"]] == [True, False, False] and state["error"] is None
    assert "c2VjcmV0" not in json.dumps(state)
    fake.files["/home/deck/.docker/config.json"] = json.dumps({**config, "credsStore": "pass"}).encode()
    state = await r.Registries(ctx).state(SimpleNamespace(id="h1"))
    assert [e["logged_in"] for e in state["registries"]] == [None, None, None] and state["creds_store"] == "pass"


@pytest.mark.asyncio
async def test_an_unreadable_vault_is_no_missing_password():
    r = _mod()
    ctx = _ctx()

    async def locked(label):
        raise RuntimeError("database is locked")

    ctx.secrets.exists = locked
    with pytest.raises(r.SecretsUnreadable):
        await r.Registries(ctx).state(SimpleNamespace(id="h1"))


@pytest.mark.asyncio
async def test_entries_deck_cannot_use_are_named():
    r = _mod()
    entries = [*ENTRIES, {"name": "x" * 65, "address": "a.io", "username": "u"}, {"name": "..", "address": "a.io", "username": "u"},
               {"name": "neu", "address": "https://a.io", "username": "u"}, {"address": "a.io", "username": "u"}]
    state = await r.Registries(_ctx(entries=entries)).state(SimpleNamespace(id="h1"))
    assert [e["name"] for e in state["registries"]] == ["ghcr", "hub", "lab"]
    assert [w.split(" ist ")[0] for w in state["warnings"]] == [
        f"Der Eintrag „{'x' * 65}“", "Der Eintrag „..“", "Der Eintrag „neu“", "Der Eintrag ohne Kurznamen"]


def test_the_settings_check_the_name_like_the_module():
    r = _mod()
    field = json.loads((EXT_DIR / "settings.schema.json").read_text())["properties"]["registries"]["items"]["properties"]["name"]
    assert field["pattern"].rstrip("$") == r._NAME_RE.pattern.removesuffix("\\Z") and field["maxLength"] == 64
    for name, ok in (("ghcr", True), ("a" * 64, True), ("a" * 65, False), ("..", False), (".", False), ("-x", False), ("my.reg_1", True)):
        assert bool(r._NAME_RE.match(name)) is ok and bool(re.fullmatch(field["pattern"], name)) is ok


@pytest.mark.asyncio
@pytest.mark.parametrize(("content", "why"), [
    (b"{kaputt", "Expecting property name"),
    (b"{}" + b" " * (256 * 1024), "~/.docker/config.json ist größer als 256 KB"),
    (b"[]", "~/.docker/config.json hat ein unbekanntes Format"),
])
async def test_an_unreadable_config_is_unknown_not_logged_out(content, why):
    r = _mod()
    fake = FakeExec()
    fake.files["/home/deck/.docker/config.json"] = content
    state = await r.Registries(_ctx(exec_=fake)).state(SimpleNamespace(id="h1"))
    assert state["error"].startswith("Die Anmeldungen von Docker ließen sich nicht lesen") and why in state["error"]
    assert {e["logged_in"] for e in state["registries"]} == {None}


@pytest.mark.asyncio
@pytest.mark.parametrize(("error", "reason"), [
    (lambda: PermissionError(13, "Permission denied"), "[Errno 13] Permission denied"),
    (lambda: pytest.importorskip("asyncssh").ChannelOpenError(2, "Kanal zu"), "Kanal zu"),
], ids=["rechte", "kanal-code-2"])
async def test_a_read_error_after_connecting_is_not_an_unreachable_server(error, reason):
    r = _mod()
    fake = FakeExec()
    real_open = fake.sftp_server.open

    def denied(path, mode, attrs=None):
        if path.endswith("config.json"):
            raise error()
        return real_open(path, mode, attrs)

    fake.sftp_server.open = denied
    state = await r.Registries(_ctx(exec_=fake)).state(SimpleNamespace(id="h1"))
    assert state["error"] == f"Die Anmeldungen von Docker ließen sich nicht lesen: {reason}"


@pytest.mark.asyncio
async def test_login_sends_the_password_only_through_stdin():
    r = _mod()
    ctx = _ctx()
    seen = _recording(ctx.exec)
    done = await r.DockerRegistryExecutor(ctx, r.Registries(ctx)).execute(_request())
    assert done.success is True and done.output.startswith("Docker auf diesem Server ist bei ghcr.io angemeldet.")
    assert seen == [(["docker", "login", "--username=nico", "--password-stdin", "ghcr.io"], PASSWORD.encode() + b"\n")]
    assert PASSWORD not in json.dumps(done.model_dump(mode="json"))


CHANGED = ("Adresse oder Benutzername von „ghcr“ haben sich seit dem Vorschlag geändert. "
           "Freigegeben wird nur, was vorgeschlagen war; schlag es mit den neuen Angaben noch einmal vor.")


@pytest.mark.asyncio
@pytest.mark.parametrize(("request_", "secrets", "error"), [
    ({"actor": "ai"}, None, "Bei einer Registry meldet nur ein Mensch über die Docker-Seite an."),
    ({"registry": "weg"}, None, "Diese Registry ist nicht (mehr) eingetragen. Bitte die Einstellungen prüfen."),
    ({"address": "andere.io"}, None, CHANGED),
    ({"username": "admin"}, None, CHANGED),
    ({"username": None}, None, ("Dieser Vorschlag stammt aus einer älteren Version von Nodvard Deck und nennt den Benutzernamen nicht. "
                                "Schlag es bitte noch einmal vor.")),
    ({"username": ""}, None, CHANGED),
    ({"action": "docker.registry_logout", "username": "admin"}, None, CHANGED),
    ({"host": "h9"}, None, "Server nicht gefunden."),
    ({}, {}, "Für „ghcr“ ist kein Passwort im Tresor. Trag es unter Einstellungen → Docker ein."),
    ({}, {"docker-registry-pw:ghcr": ""}, "Für „ghcr“ ist kein Passwort im Tresor. Trag es unter Einstellungen → Docker ein."),
])
async def test_refusals_send_nothing(request_, secrets, error):
    r = _mod()
    ctx = _ctx(secrets=secrets)
    seen = _recording(ctx.exec)
    done = await r.DockerRegistryExecutor(ctx, r.Registries(ctx)).execute(_request(**request_))
    assert done.success is False and done.error == error and seen == []


class _Stale:
    """Liefert einen Eintrag, den die Prüfung der Einstellungen nie durchließe."""

    def __init__(self, registry) -> None:
        self.registry = registry

    async def find(self, name):
        return self.registry


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["docker.registry_login", "docker.registry_logout"])
@pytest.mark.parametrize(("address", "username"), [("ghcr.io", "a b"), ("https://ghcr.io", "deck"), ("-H", "nico")],
                         ids=["benutzer", "adresse", "option"])
async def test_an_invalid_entry_sends_nothing(action, address, username):
    r = _mod()
    ctx = _ctx()
    seen = _recording(ctx.exec)
    stale = _Stale(r.Registry("ghcr", address, username))
    done = await r.DockerRegistryExecutor(ctx, stale).execute(_request(action=action, address=address))
    assert done.success is False and seen == []
    assert done.error == "Diese Registry ist nicht (mehr) eingetragen. Bitte die Einstellungen prüfen."


@pytest.mark.asyncio
async def test_a_vault_error_never_shows_its_text():
    r = _mod()
    ctx = _ctx()
    _recording(ctx.exec)

    @asynccontextmanager
    async def broken(handle):
        raise RuntimeError(f"Wert {PASSWORD} passt nicht")
        yield

    ctx.vault_use = broken
    done = await r.DockerRegistryExecutor(ctx, r.Registries(ctx)).execute(_request())
    assert done.error == "Das Passwort für „ghcr“ ließ sich nicht aus dem Tresor lesen (RuntimeError)."

    @asynccontextmanager
    async def unreadable(handle):
        raise PermissionError(13, "Permission denied", "/app/data/vault.key")
        yield

    ctx.vault_use = unreadable
    done = await r.DockerRegistryExecutor(ctx, r.Registries(ctx)).execute(_request())
    assert done.error == "Das Passwort für „ghcr“ ließ sich nicht aus dem Tresor lesen (PermissionError)."


def _ssh_error(name, *args):
    from nodvard_deck.core import ssh

    return getattr(ssh, name)(*args)


@pytest.mark.asyncio
@pytest.mark.parametrize(("action", "error", "reason"), [
    ("docker.registry_login", lambda: pytest.importorskip("asyncssh").ConnectionLost("Connection lost"),
     ("Die Verbindung zum Server ist beim Anmelden abgebrochen; docker login kann schon durchgelaufen sein. "
      "Den Stand zeigt die Liste nach dem Neuladen.")),
    ("docker.registry_logout", lambda: pytest.importorskip("asyncssh").ConnectionLost("Connection lost"),
     ("Die Verbindung zum Server ist beim Abmelden abgebrochen; docker logout kann schon durchgelaufen sein. "
      "Den Stand zeigt die Liste nach dem Neuladen.")),
    ("docker.registry_login", lambda: TimeoutError(),
     "Nach 2 Minuten ohne Ende abgebrochen; docker login kann auf dem Server weiterlaufen. Den Stand zeigt die Liste nach dem Neuladen."),
    ("docker.registry_login", lambda: _ssh_error("SshAuthError", "Der Server hat die Anmeldung abgelehnt."),
     "Der Server hat die Anmeldung abgelehnt."),
    ("docker.registry_login", lambda: _ssh_error("HostKeyMismatch", "h1", "ssh-ed25519", "SHA256:a", "SHA256:b"),
     "Host-Schlüssel (ssh-ed25519) für Host 'h1' hat sich geändert"),
    ("docker.registry_logout", lambda: __import__("nodvard_sdk.errors", fromlist=["x"]).HostUnreachable(
        "Für „h1“ ist noch kein SSH-Zugang eingerichtet (Einstellungen → Server & Zugänge)."),
     "Für „h1“ ist noch kein SSH-Zugang eingerichtet (Einstellungen → Server & Zugänge)."),
    ("docker.registry_login", lambda: ValueError("innen"), "Anmelden ist gescheitert (ValueError)."),
], ids=["abgerissen", "abgerissen-abmelden", "zeitlimit", "anmeldung", "schluessel", "kein-zugang", "sonst"])
async def test_a_broken_call_on_the_server_is_no_vault_error(action, error, reason):
    r = _mod()
    ctx = _ctx()

    async def run_argv(host, argv, **kwargs):
        raise error()

    ctx.exec.run_argv = run_argv
    done = await r.DockerRegistryExecutor(ctx, r.Registries(ctx)).execute(_request(action=action))
    verb = "Anmelden" if action.endswith("login") else "Abmelden"
    assert done.success is False and done.error.startswith(f"{verb} bei ghcr.io ist gescheitert: {reason}")
    assert "Tresor" not in done.error and PASSWORD not in done.error


@pytest.mark.asyncio
@pytest.mark.parametrize(("code", "reason"), [
    (-1, "Die Verbindung endete, bevor docker {verb} fertig war. Den Stand zeigt die Liste nach dem Neuladen."),
    (137, "docker {verb} endete ohne Meldung (Code 137). Den Stand zeigt die Liste nach dem Neuladen."),
], ids=["abgerissen", "code-137"])
@pytest.mark.parametrize("verb", ["login", "logout"])
async def test_an_end_without_output_says_so(code, reason, verb):
    r = _mod()
    ctx = _ctx()
    _recording(ctx.exec, result(code=code))
    done = await r.DockerRegistryExecutor(ctx, r.Registries(ctx)).execute(_request(action=f"docker.registry_{verb}"))
    assert done.error.endswith(f"ist gescheitert: {reason.format(verb=verb)}") and "Compose" not in done.error


@pytest.mark.asyncio
async def test_failed_login_and_logout_say_why():
    r = _mod()
    ctx = _ctx()
    _recording(ctx.exec, result(stderr="Error response from daemon: Get \"https://ghcr.io/v2/\": denied: denied\n", code=1))
    executor = r.DockerRegistryExecutor(ctx, r.Registries(ctx))
    done = await executor.execute(_request())
    assert done.error == "Anmelden bei ghcr.io ist gescheitert: Error response from daemon: Get \"https://ghcr.io/v2/\": denied: denied"
    seen = _recording(ctx.exec, result("Removing login credentials for ghcr.io\n"))
    out = await executor.execute(_request(action="docker.registry_logout"))
    assert out.success is True and out.output == "Docker auf diesem Server ist bei ghcr.io abgemeldet."
    assert seen == [(["docker", "logout", "ghcr.io"], None)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "verb"), [("docker.registry_login", "anmelden"), ("docker.registry_logout", "abmelden")], ids=["anmelden", "abmelden"],
)
async def test_the_dry_run_names_the_registry_and_touches_nothing(action, verb):
    r = _mod()
    ctx = _ctx()
    seen = _recording(ctx.exec)
    executor = r.DockerRegistryExecutor(ctx, r.Registries(ctx))
    report = await executor.dry_run(_request(action=action))
    assert (report.would_change, report.summary) == (True, f"Würde Docker bei ghcr.io {verb}.")
    bare = await executor.dry_run(_request(action=action).model_copy(update={"payload": {}}))
    assert bare.summary == f"Würde Docker bei ? {verb}."
    assert seen == []


# --- Durch den echten Kern -------------------------------------------------------------------------------------------


async def _with_registry(client, docker_on):
    saved = await client.put("/api/v1/extensions/docker/settings", headers=docker_on.owner,
                             json={"current_password": "correct-horse-battery", "values": {"registries": [ENTRIES[0]]}})
    assert saved.status_code == 200, saved.text
    stored = await client.put("/api/v1/extensions/docker/secrets", headers=docker_on.owner,
                              json={"current_password": "correct-horse-battery", "label": "docker-registry-pw:ghcr", "value": PASSWORD})
    assert stored.status_code == 204, stored.text


@pytest.mark.asyncio
async def test_login_through_the_gate(client, docker_on, monkeypatch):
    _without_config(docker_on.exec)
    await _with_registry(client, docker_on)
    seen = _recording(docker_on.exec)
    monkeypatch.setattr(docker_on.handle, "run_argv", docker_on.exec.run_argv, raising=False)
    base = f"/api/v1/ext/docker/hosts/{docker_on.host}/registries"
    state = await client.get(base, headers=docker_on.owner)
    assert state.status_code == 200 and state.json()["registries"][0] == {
        "name": "ghcr", "address": "ghcr.io", "username": "nico", "logged_in": False, "secret_set": True}
    proposed = await client.post(f"{base}/ghcr/login", headers=docker_on.owner)
    assert proposed.status_code == 202 and proposed.json()["risk"] == "medium" and seen == []
    approved = await client.post(f"/api/v1/actions/{proposed.json()['action_id']}/approve", headers=docker_on.owner)
    assert approved.status_code == 200 and approved.json()["status"] == "succeeded", approved.text
    assert seen == [(["docker", "login", "--username=nico", "--password-stdin", "ghcr.io"], PASSWORD.encode() + b"\n")]
    action = await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)
    assert PASSWORD not in action.text and PASSWORD not in state.text
    assert (await client.post(f"{base}/weg/login", headers=docker_on.owner)).status_code == 404
    assert (await client.post(f"{base}/a;b/login", headers=docker_on.owner)).status_code == 400


@pytest.mark.asyncio
async def test_an_unreadable_vault_is_a_503_without_its_text(client, docker_on, monkeypatch):
    from nodvard_deck.ext.runtime import get_extension_runtime

    await _with_registry(client, docker_on)

    async def locked(label):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(get_extension_runtime().loaded["docker"].ctx.secrets, "exists", locked)
    response = await client.get(f"/api/v1/ext/docker/hosts/{docker_on.host}/registries", headers=docker_on.owner)
    assert response.status_code == 503
    assert response.json()["detail"] == "Ob die Passwörter im Tresor liegen, ließ sich gerade nicht lesen. Versuch es gleich noch einmal."
    assert "database is locked" not in response.text


@pytest.mark.asyncio
async def test_a_new_address_drops_the_password(client, docker_on):
    await _with_registry(client, docker_on)
    moved = await client.put("/api/v1/extensions/docker/settings", headers=docker_on.owner, json={
        "current_password": "correct-horse-battery", "values": {"registries": [{**ENTRIES[0], "address": "andere.example"}]}})
    assert moved.status_code == 200 and moved.json()["secrets_cleared"] == ["docker-registry-pw:ghcr"]


@pytest.mark.asyncio
async def test_not_offered_on_the_server_page_and_only_with_hosts_execute(client, docker_on):
    for action in ("docker.registry_login", "docker.registry_logout"):
        refused = await client.post(f"/api/v1/hosts/{docker_on.host}/actions/{action}",
                                    json={"payload": {"registry": "ghcr", "address": "ghcr.io"}, "reason": "Test"}, headers=docker_on.owner)
        assert refused.status_code == 404
    base = f"/api/v1/ext/docker/hosts/{docker_on.host}/registries"
    for method, url in (("get", base), ("post", f"{base}/ghcr/login"), ("post", f"{base}/ghcr/logout")):
        assert (await getattr(client, method)(url)).status_code == 401
        assert (await getattr(client, method)(url, headers=docker_on.viewer)).status_code == 403
