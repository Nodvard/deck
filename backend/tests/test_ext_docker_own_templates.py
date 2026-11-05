"""Eigene Vorlagen: nur die Compose-Datei, feste Geheimnisse werden abgelehnt, Speichern, Ändern und Löschen über die API."""

from __future__ import annotations

import json
import sys
import time

import pytest
from docker_stack_fakes import EXT_DIR, docker_on  # noqa: F401 - Fixtures


@pytest.fixture(autouse=True, scope="module")
def _ext_docker_on_path():
    with pytest.MonkeyPatch.context() as mp:
        mp.syspath_prepend(str(EXT_DIR / "src"))
        yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


URL = "/api/v1/ext/docker/own-templates"

PLAIN = """services:
  app:
    image: nginx:1.27
    restart: unless-stopped
    ports:
      - "8080:80"
"""


def _check(text: str) -> str | None:
    from nodvard_deck_ext_docker.own_templates import check_compose

    try:
        check_compose(text)
    except ValueError as exc:
        return str(exc)
    return None


def _service(*lines: str) -> str:
    return "services:\n  db:\n    image: postgres:17\n" + "".join(f"    {line}\n" for line in lines)


def test_catalog_templates_pass():
    from nodvard_deck_ext_docker.templates import TEMPLATES

    assert [t.id for t in TEMPLATES if _check(t.compose)] == []


def test_fixed_password_is_refused_without_its_value():
    problem = _check(_service("environment:", "  POSTGRES_USER: deck", "  POSTGRES_PASSWORD: geheim123"))
    assert problem and "Zeile 6: POSTGRES_PASSWORD („db“) hat einen festen Wert" in problem
    assert "geheim123" not in problem and "${DB_PASSWORD}" in problem


@pytest.mark.parametrize(
    "lines",
    [
        ("environment:", "  POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}"),
        ("environment:", "  POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?fehlt}"),
        ("environment:", "  POSTGRES_PASSWORD: $POSTGRES_PASSWORD"),
        ("environment:", '  POSTGRES_PASSWORD: ""'),
        ("environment:", "  POSTGRES_PASSWORD:"),
        ("environment:", "  - POSTGRES_PASSWORD"),
        ("environment:", "  POSTGRES_PASSWORD_FILE: /run/secrets/db"),
        ("environment:", "  PASSWORD_MIN_LENGTH: 12", "  SIGNUPS_ALLOWED: 'true'", "  TOKEN_TTL: 3600"),
        ("environment:", "  DATABASE_URL: postgres://deck:${DB_PASSWORD}@db/deck"),
        ("environment:", "  DATABASE_URL: postgres://" + "${DB_USER:-app}:${DB_PASSWORD}@db/app"),
        ("environment:", '  MYSQL_RANDOM_ROOT_PASSWORD: "yes"', '  ALLOW_EMPTY_PASSWORD: "1"', "  MYSQL_ALLOW_EMPTY_PASSWORD: 'Yes'"),
        ("environment:", "  MARIADB_RANDOM_ROOT_PASSWORD: yes", "  DISABLE_PASSWORD: 1"),
        ("environment:", "  - ALLOW_EMPTY_PASSWORD=true", "  - MYSQL_RANDOM_ROOT_PASSWORD=1"),
        ("# MYSQL_RANDOM_ROOT_PASSWORD: yes",),
        ("environment:", "  AUTHENTIK_POSTGRESQL__PASSWORD: file:///run/secrets/db", "  AUTHENTIK_SECRET_KEY: env://SECRET"),
        ("environment:", "  POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-}"),
        ("healthcheck:", '  test: ["CMD-SHELL", "mysqladmin ping --password=$$MYSQL_ROOT_PASSWORD"]'),
        ("command: redis-server --requirepass $${REDIS_PASSWORD}",),
        ('command: redis-server --requirepass ""',),
        ("command: redis-server --bypass-cache yes",),
        ("secrets:", "  - db_password"),
        ("# Passwort: siehe .env im Ordner",),
    ],
)
def test_variables_and_harmless_names_pass(lines):
    assert _check(_service(*lines)) is None


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (("environment:", "  POSTGRES_PASSWORD: ${PW:-geheim}"), "POSTGRES_PASSWORD („db“) hat einen festen Wert"),
        (("environment:", "  - DB_PASS=geheim"), "DB_PASS („db“) hat einen festen Wert"),
        (("environment:", "  GITEA__security__SECRET_KEY: abc"), "GITEA__security__SECRET_KEY („db“)"),
        (("environment:", "  adminPassword: 1234"), "adminPassword („db“)"),
        (("build:", "  context: .", "  args:", "    NPM_TOKEN: abc"), "NPM_TOKEN („db“)"),
        (("command: redis-server --requirepass geheim",), "bei --requirepass einen festen Wert"),
        (("command: [redis-server, --requirepass, geheim]",), "bei --requirepass einen festen Wert"),
        (("command: [redis-server, --requirepass=geheim]",), "--requirepass („db“) hat einen festen Wert"),
        (("environment:", "  UPSTREAM: https://admin:" + "geheim" + "@example.org/"), "eine Adresse enthält ein Passwort"),
        (("environment:", "  CACHE: redis://:" + "geheim" + "@cache:6379"), "eine Adresse enthält ein Passwort"),
        (("environment:", "  TS_AUTHKEY: geheim", "  SECRET_KEY_BASE: geheim", "  privateKey: geheim"), "SECRET_KEY_BASE („db“)"),
        (("# POSTGRES_PASSWORD: geheim",), "ein Kommentar nennt einen Wert für POSTGRES_PASSWORD"),
        (("environment:", "  #- DB_PASSWORD=geheim", "  TZ: Europe/Berlin"), "ein Kommentar nennt einen Wert für DB_PASSWORD"),
        (("environment:", '  #- "DB_PASSWORD=geheim"', "  TZ: Europe/Berlin"), "ein Kommentar nennt einen Wert für DB_PASSWORD"),
        (("environment:", "  - DB_PASSWORD = geheim"), "DB_PASSWORD („db“) hat einen festen Wert"),
        (("environment:", "  DB_PASSWORD: $$geheim1"), "DB_PASSWORD („db“) hat einen festen Wert"),
        (("environment:", '  POSTGRES_PASSWORD: "1"'), "POSTGRES_PASSWORD („db“) hat einen festen Wert"),
        (("environment:", "  POSTGRES_PASSWORD: 1"), "POSTGRES_PASSWORD („db“) hat einen festen Wert"),
        (("environment:", "  MYSQL_ROOT_PASSWORD: yes"), "MYSQL_ROOT_PASSWORD („db“) hat einen festen Wert"),
        (("environment:", "  - DB_PASSWORD=true"), "DB_PASSWORD („db“) hat einen festen Wert"),
        (("command: redis-server --requirepass 1",), "bei --requirepass einen festen Wert"),
        (("# POSTGRES_PASSWORD: 0",), "ein Kommentar nennt einen Wert für POSTGRES_PASSWORD"),
        (("environment:", "  CACHE: redis://:" + "1" + "@cache:6379"), "eine Adresse enthält ein Passwort"),
    ],
)
def test_fixed_secrets_are_found(lines, expected):
    problem = _check(_service(*lines))
    assert problem and expected in problem and "geheim" not in problem.replace("Geheim", "")


def _tokens() -> list[tuple[str, str]]:
    return [
        ("ts" + "key-auth-" + "kQ7X2M4K9P3R8T6V", "Tailscale-Schlüssel"),
        ("gh" + "p_" + "A1b2C3d4E5" * 4, "GitHub-Token"),
        ("-----BEGIN " + "OPENSSH PRIVATE KEY-----", "privaten Schlüssel"),
        ("AK" + "IA" + "Q7XW2M4K9P3R8T6V", "AWS-Zugangsschlüssel"),
        ("xo" + "xb-" + "1234567890-abcdef", "Slack-Token"),
        ("gl" + "pat-" + "Ab1Cd2Ef3Gh4Ij5Kl6Mn", "GitLab-Token"),
        ("s" + "k-" + "proj-A1B2C3d4e5F6g7H8i9J0", "API-Schlüssel"),
        ("ey" + "JhbGciOiJIUzI1NiJ9." + "ey" + "JzdWIiOiJkZWNrIn0." + "c2lnbmF0dXJlMTIz", "JWT"),
    ]


@pytest.mark.parametrize("index", range(8))
def test_tokens_are_found_even_in_comments(index):
    token, kind = _tokens()[index]
    problem = _check(PLAIN + f"# alter Zugang: {token}\n")
    assert problem and "Zeile 7: sieht aus wie" in problem and kind in problem and token not in problem


@pytest.mark.parametrize(
    "lines",
    [
        ('test: ["CMD-SHELL", "mysql -uroot -phunter2pw --password=x"]',),
        ('command: ["curl -u admin:hunter2pw https://x/?token=abc"]',),
        ('command: ["-a hunter2pw --token", "x"]',),
    ],
)
def test_a_value_never_shows_up_in_the_message(lines):
    problem = _check(_service(*lines))
    assert problem is None or "hunter2pw" not in problem


@pytest.mark.parametrize(
    "lines",
    [
        ("command: mysql -phunter2pwpassword db",),
        ('command: ["mysql", "-phunter2pwpassword", "db"]',),
        ("healthcheck:", '  test: ["CMD", "mysqladmin", "ping", "-h", "localhost", "-uroot", "-phunter2pw"]'),
        ("healthcheck:", '  test: ["CMD-SHELL", "mysqladmin ping -uroot -phunter2pw || exit 1"]'),
        ("command: /usr/bin/mariadb-dump -h db -P 3306 '-phunter2pw' shop",),
        ("command: sh -c 'sleep 5; mariadb -uroot -phunter2pw -e \"select 1\"'",),
    ],
)
def test_a_short_mysql_password_is_refused_without_its_value(lines):
    problem = _check(_service(*lines))
    assert problem and "gibt bei -p ein festes Passwort mit" in problem and "hunter2pw" not in problem


@pytest.mark.parametrize(
    "lines",
    [
        ("command: mysql -p db",),
        ('command: ["mysql", "-p", "db"]',),
        ("healthcheck:", '  test: ["CMD-SHELL", "mysqladmin ping -p$$MYSQL_ROOT_PASSWORD"]'),
        ("healthcheck:", '  test: ["CMD-SHELL", "mysqladmin ping -p\\"$${MYSQL_ROOT_PASSWORD}\\""]'),
        ("command: mysql -h db -P3306 shop",),
        ("command: redis-server -p6379",),
        ('command: ["iperf3", "-s", "-p5201"]',),
        ("command: sh -c 'mysql shop; redis-server -p6379'",),
    ],
)
def test_other_p_switches_pass(lines):
    assert _check(_service(*lines)) is None


def test_all_findings_are_counted():
    problem = _check(_service("environment:", *(f"  DB{n}_PASSWORD: geheim" for n in range(7))))
    assert problem and problem.count("hat einen festen Wert") == 5 and "und 2 weitere" in problem


def test_too_deep_is_named():
    assert _check("services:\n  app:\n    x: " + "[" * 5000 + "\n") == "Die Datei ist zu tief verschachtelt."


@pytest.mark.parametrize("word", [
    "a" * 200_000, "-a" * 100_000, "-eyJ" * 50_000, "sk-" * 60_000, "x://" * 50_000, "# a:" * 50_000,
    "a://$" + "a" * 65_000, " " * 65_000 + "x", " " * 32_000 + "-" + " " * 32_000 + "x", "a://${" * 10_000,
])
def test_long_words_stay_fast(word):
    started = time.monotonic()
    _check(PLAIN + "#" + word + "\n")
    assert time.monotonic() - started < 1


def test_long_variables_stay_fast():
    started = time.monotonic()
    _check(_service("environment:", "  A_TOKEN: '" + "${a?" * 16_000 + "'", "  B_TOKEN: '" + "${a:?" * 13_000 + "'"))
    assert time.monotonic() - started < 1


def test_image_names_are_no_api_keys():
    assert _check(PLAIN.replace("nginx:1.27", "example/sk-backup-service-container:1")) is None


def test_broken_yaml_names_only_the_line():
    problem = _check("services:\n  app:\n    image: nginx\n\tpassword: geheim\n")
    assert problem and problem.startswith("Die Datei ist kein gültiges YAML (Zeile 4)") and "geheim" not in problem


@pytest.mark.parametrize("text", ["foo: 1\n", "services: {}\n", "- a\n", ""])
def test_a_template_needs_services(text):
    assert _check(text) == "Eine Vorlage braucht unter services mindestens einen Dienst."


def test_one_document_only():
    assert _check(PLAIN + "---\n" + PLAIN) == "Die Datei ist kein gültiges YAML (Zeile 7)."


def test_aliases_are_walked_once():
    levels = ["a0: &a0 [x, x, x, x, x, x, x, x, x, x]"]
    levels += [f"a{i}: &a{i} [{', '.join([f'*a{i - 1}'] * 10)}]" for i in range(1, 9)]
    text = "x-bombe:\n" + "".join(f"  {line}\n" for line in levels) + PLAIN
    started = time.monotonic()
    assert _check(text) is None
    assert time.monotonic() - started < 2


@pytest.mark.asyncio
async def test_save_list_change_delete(client, docker_on):
    owner = docker_on.owner
    created = await client.post(URL, headers=owner, json={"name": "  Mein   Web  ", "summary": "nginx für Tests", "compose": PLAIN.replace("\n", "\r\n")})
    assert created.status_code == 201
    entry = created.json()
    assert entry["name"] == "Mein Web" and entry["compose"] == PLAIN and len(entry["id"]) == 16
    store = docker_on.data_dir / "own_templates.json"
    assert store.stat().st_mode & 0o777 == 0o600

    listed = (await client.get(URL, headers=owner)).json()
    assert listed["max"] == 100 and listed["templates"] == [{k: entry[k] for k in ("id", "name", "summary", "updated_at")}]
    assert (await client.get(f"{URL}/{entry['id']}", headers=owner)).json() == entry

    changed = await client.put(f"{URL}/{entry['id']}", headers=owner, json={"name": "Mein Web", "compose": PLAIN.replace("8080", "8081")})
    assert changed.status_code == 200 and "8081:80" in changed.json()["compose"] and changed.json()["summary"] == ""

    assert (await client.delete(f"{URL}/{entry['id']}", headers=owner)).json() == {"deleted": True}
    assert (await client.get(URL, headers=owner)).json()["templates"] == []
    assert (await client.delete(f"{URL}/{entry['id']}", headers=owner)).status_code == 404


@pytest.mark.asyncio
async def test_rights(client, docker_on):
    assert (await client.get(URL)).status_code == 401
    assert (await client.get(URL, headers=docker_on.viewer)).status_code == 403
    body = {"name": "x", "compose": PLAIN}
    assert (await client.post(URL, headers=docker_on.viewer, json=body)).status_code == 403
    assert not (docker_on.data_dir / "own_templates.json").exists()


@pytest.mark.asyncio
async def test_secret_is_refused_and_not_stored(client, docker_on):
    answer = await client.post(URL, headers=docker_on.owner, json={"name": "db", "compose": _service("environment:", "  POSTGRES_PASSWORD: geheim123")})
    assert answer.status_code == 400 and "POSTGRES_PASSWORD" in answer.json()["detail"] and "geheim123" not in answer.text
    assert not (docker_on.data_dir / "own_templates.json").exists()


@pytest.mark.asyncio
async def test_only_the_compose_file_is_taken(client, docker_on):
    answer = await client.post(URL, headers=docker_on.owner, json={"name": "web", "compose": PLAIN, "env": "DB_PASSWORD=geheim"})
    assert answer.status_code == 422


@pytest.mark.asyncio
async def test_names_ids_and_limits(client, docker_on, monkeypatch):
    from nodvard_deck_ext_docker import own_templates

    owner = docker_on.owner
    first = (await client.post(URL, headers=owner, json={"name": "Web", "compose": PLAIN})).json()
    twice = await client.post(URL, headers=owner, json={"name": "web", "compose": PLAIN})
    assert twice.status_code == 409 and "Es gibt schon eine Vorlage „web“" in twice.json()["detail"]
    other = (await client.post(URL, headers=owner, json={"name": "Zweite", "compose": PLAIN})).json()
    taken = await client.put(f"{URL}/{other['id']}", headers=owner, json={"name": "WEB", "compose": PLAIN})
    assert taken.status_code == 409
    assert (await client.put(f"{URL}/{first['id']}", headers=owner, json={"name": "Web", "compose": PLAIN})).status_code == 200

    monkeypatch.setattr(own_templates, "MAX_TEMPLATES", 2)
    full = await client.post(URL, headers=owner, json={"name": "Dritte", "compose": PLAIN})
    assert full.status_code == 409 and "2 eigene Vorlagen" in full.json()["detail"]

    for bad in ("../etc", "ABCDEF0123456789", "0" * 15):
        assert (await client.get(f"{URL}/{bad}", headers=owner)).status_code == 404
    assert (await client.put(f"{URL}/{'0' * 16}", headers=owner, json={"name": "x", "compose": PLAIN})).status_code == 404
    assert (await client.post(URL, headers=owner, json={"name": "a\x07b", "compose": PLAIN})).status_code == 400
    assert (await client.post(URL, headers=owner, json={"name": " ", "compose": PLAIN})).status_code == 400
    big = await client.post(URL, headers=owner, json={"name": "gross", "compose": PLAIN + "# " + "ä" * own_templates.MAX_COMPOSE + "\n"})
    assert big.status_code == 422
    near = await client.post(URL, headers=owner, json={"name": "gross", "compose": PLAIN + "# " + "ä" * (own_templates.MAX_COMPOSE // 2) + "\n"})
    assert near.status_code == 413 and near.json()["detail"] == "Eine Vorlage darf höchstens 64 KB groß sein."


@pytest.mark.asyncio
async def test_broken_store_is_kept_aside(client, docker_on):
    store = docker_on.data_dir / "own_templates.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("{kaputt", encoding="utf-8")
    listed = (await client.get(URL, headers=docker_on.owner)).json()
    assert listed["templates"] == [] and "beschädigt" in listed["problem"]
    assert (await client.get(f"{URL}/{'0' * 16}", headers=docker_on.owner)).status_code == 503
    assert (await client.post(URL, headers=docker_on.owner, json={"name": "Web", "compose": PLAIN})).status_code == 201
    assert [p.name for p in docker_on.data_dir.glob("own_templates.json.kaputt-*")]
    assert [t["name"] for t in json.loads(store.read_text(encoding="utf-8"))["templates"].values()] == ["Web"]


@pytest.mark.asyncio
async def test_unreadable_store_is_no_empty_list(client, docker_on):
    (docker_on.data_dir / "own_templates.json").mkdir(parents=True)
    answer = await client.get(URL, headers=docker_on.owner)
    assert answer.status_code == 503 and "ließen sich gerade nicht lesen" in answer.json()["detail"]
    assert (await client.post(URL, headers=docker_on.owner, json={"name": "Web", "compose": PLAIN})).status_code == 503
    assert (await client.delete(f"{URL}/{'0' * 16}", headers=docker_on.owner)).status_code == 503


@pytest.mark.asyncio
async def test_a_secret_does_not_replace_the_old_template(client, docker_on):
    owner = docker_on.owner
    entry = (await client.post(URL, headers=owner, json={"name": "Web", "compose": PLAIN})).json()
    refused = await client.put(f"{URL}/{entry['id']}", headers=owner, json={"name": "Web", "compose": _service("environment:", "  DB_PASSWORD: geheim")})
    assert refused.status_code == 400
    assert (await client.get(f"{URL}/{entry['id']}", headers=owner)).json()["compose"] == PLAIN


@pytest.mark.asyncio
async def test_a_failed_write_keeps_nothing(client, docker_on, monkeypatch):
    from nodvard_deck_ext_docker import own_templates

    def broken(path, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(own_templates, "_write_private", broken)
    answer = await client.post(URL, headers=docker_on.owner, json={"name": "Web", "compose": PLAIN})
    assert answer.status_code == 503 and "No space left on device" in answer.json()["detail"]
    assert (await client.get(URL, headers=docker_on.owner)).json()["templates"] == []


@pytest.mark.asyncio
async def test_invalid_entries_in_the_file_are_left_out(client, docker_on):
    good = {"name": "Web", "summary": "", "compose": PLAIN, "updated_at": ""}
    store = docker_on.data_dir / "own_templates.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps({"templates": {
        "a" * 16: good, "../etc": good, "b" * 16: {**good, "name": ""}, "c" * 16: {**good, "compose": 5}, "d" * 16: "kaputt",
    }}), encoding="utf-8")
    assert [t["id"] for t in (await client.get(URL, headers=docker_on.owner)).json()["templates"]] == ["a" * 16]


@pytest.mark.asyncio
async def test_the_same_name_twice_at_once(client, docker_on):
    import asyncio

    answers = await asyncio.gather(*(client.post(URL, headers=docker_on.owner, json={"name": "Web", "compose": PLAIN}) for _ in range(2)))
    assert sorted(a.status_code for a in answers) == [201, 409]
