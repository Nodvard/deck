"""ntfy: Mindest-Stufe, Lautstaerke (ntfy-Prioritaet) je Stufe und der Link zum Abonnieren (QR-Code der
Einstellungsseite). Gegen den lokalen Fake-Server aus test_ext_ntfy.py: echter HTTP-Roundtrip."""

from __future__ import annotations

import json

import pytest
from nodvard_sdk import Notification, Severity
from nodvard_sdk.capabilities import NotificationChannel
from sqlalchemy import select
from test_ext_ntfy import (  # noqa: F401 -- _cleanup_sys_path ist eine autouse-Fixture, fake_ntfy_server eine Fixture
    REPO_EXTENSIONS_DIR,
    _auth_header,
    _bootstrap_owner,
    _cleanup_sys_path,
    _ntfy_extension_class,
    _setup_ntfy_extension,
    fake_ntfy_server,
)

from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import NotificationDelivery
from nodvard_deck.services import notifications as notifications_service

SCHEMA_PATH = REPO_EXTENSIONS_DIR / "ntfy" / "settings.schema.json"


@pytest.fixture(autouse=True)
def _reset_runtime():
    reset_extension_runtime()
    yield
    reset_extension_runtime()


async def _channel(db_session, tmp_path, test_settings, server_url: str, **extra):
    """Der echte Kanal der Erweiterung, eingerichtet mit `extra` (z. B. `min_severity`)."""
    runtime, ctx, _ext = await _setup_ntfy_extension(db_session, tmp_path, test_settings, server_url=server_url)
    await ctx.settings.set({"server_url": server_url, "topic": "test-topic", **extra})
    return runtime.capabilities.query(NotificationChannel)[0], ctx


def _note(severity: Severity, **payload) -> Notification:
    return Notification(title="T", body="B", severity=severity, payload=payload)


# --- Mindest-Stufe ---------------------------------------------------------------------------------------------

SEVERITIES = [Severity.INFO, Severity.WARNING, Severity.CRITICAL]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("minimum", "expected"),
    [
        ("info", ["info", "warning", "critical"]),
        ("warning", ["warning", "critical"]),
        ("critical", ["critical"]),
    ],
)
async def test_messages_below_the_minimum_never_reach_ntfy(fake_ntfy_server, db_session, tmp_path, test_settings, minimum, expected):
    server_url, received, _ = fake_ntfy_server
    channel, _ctx = await _channel(db_session, tmp_path, test_settings, server_url, min_severity=minimum)
    from nodvard_sdk.errors import NotificationSkipped

    skipped = []
    for severity in SEVERITIES:
        try:
            await channel.send(_note(severity))
        except NotificationSkipped:  # unter der Stufe: keine Anfrage, und der Kern verbucht "uebersprungen"
            skipped.append(severity)
    assert len(skipped) == len(SEVERITIES) - len(expected)
    assert [r["body"]["tags"] for r in received] == [{"info": ["information_source"], "warning": ["warning"], "critical": ["rotating_light"]}[e] for e in expected]


@pytest.mark.asyncio
@pytest.mark.parametrize("minimum", ["critical", "warning"])
async def test_the_test_message_always_goes_out(fake_ntfy_server, db_session, tmp_path, test_settings, minimum):
    server_url, received, _ = fake_ntfy_server
    channel, _ctx = await _channel(db_session, tmp_path, test_settings, server_url, min_severity=minimum)
    await channel.send(_note(Severity.INFO, test=True))
    assert len(received) == 1


@pytest.mark.asyncio
async def test_the_test_message_of_the_settings_page_ignores_the_minimum(fake_ntfy_server, db_session, tmp_path, test_settings):
    """Der echte Weg der Einstellungsseite (`send_test_message`), nicht nur eine Meldung mit `test` im Payload."""
    from nodvard_deck.services.extension_test import send_test_message

    server_url, received, _ = fake_ntfy_server
    channel, _ctx = await _channel(db_session, tmp_path, test_settings, server_url, min_severity="critical")
    outcome = await send_test_message(channel=channel, settings={"server_url": server_url}, schema=None, secrets=[], product_name="Nodvard Deck")
    assert outcome.ok is True
    assert len(received) == 1 and received[0]["body"]["title"] == "Testnachricht"


@pytest.mark.asyncio
@pytest.mark.parametrize("settings", [{}, {"min_severity": "alles"}, {"min_severity": 2}, {"min_severity": None}, {"min_severity": ""}])
async def test_a_missing_or_unknown_minimum_sends_everything(fake_ntfy_server, db_session, tmp_path, test_settings, settings):
    server_url, received, _ = fake_ntfy_server
    channel, _ctx = await _channel(db_session, tmp_path, test_settings, server_url, **settings)
    for severity in SEVERITIES:
        await channel.send(_note(severity))
    assert len(received) == 3


@pytest.mark.asyncio
async def test_without_setup_a_skipped_message_still_says_so(db_session, tmp_path, test_settings):
    """Ein nicht eingerichteter Kanal sieht nie wie ein gesunder aus, auch wenn die Meldung sowieso uebersprungen wuerde."""
    channel, ctx = await _channel(db_session, tmp_path, test_settings, "http://127.0.0.1:1", min_severity="critical")
    await ctx.settings.set({"min_severity": "critical"})
    with pytest.raises(RuntimeError, match="noch nicht eingerichtet"):
        await channel.send(_note(Severity.INFO))


@pytest.mark.asyncio
async def test_a_skipped_message_stays_in_the_dashboard_and_is_no_failed_delivery(fake_ntfy_server, db_session, tmp_path, test_settings):
    """Durch den Kern: die Meldung steht unter "Meldungen", ntfy bekommt nichts, und im Zustellprotokoll steht
    kein Fehler -- ein Nutzer, der "Nur Kritisch" eingestellt hat, bekommt keine Fehlermeldungen dafuer."""
    server_url, received, _ = fake_ntfy_server
    channel, _ctx = await _channel(db_session, tmp_path, test_settings, server_url, min_severity="warning")
    get_extension_runtime().capabilities.provide("ntfy", NotificationChannel, channel)

    quiet = await notifications_service.send(db_session, title="Alles ruhig", body="B", severity="info")
    loud = await notifications_service.send(db_session, title="Platte voll", body="B", severity="warning")

    assert [r["body"]["title"] for r in received] == ["Platte voll"]
    assert {n.id for n in await notifications_service.list_notifications(db_session)} >= {quiet.id, loud.id}
    rows = (await db_session.execute(select(NotificationDelivery).where(NotificationDelivery.notification_id == quiet.id))).scalars().all()
    assert [(r.channel_id, r.status, r.error) for r in rows] == [("ntfy", "skipped", None)]


# --- Lautstaerke je Stufe --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_level_uses_its_own_priority_from_the_settings(fake_ntfy_server, db_session, tmp_path, test_settings):
    server_url, received, _ = fake_ntfy_server
    channel, _ctx = await _channel(
        db_session, tmp_path, test_settings, server_url,
        priority_info="min", priority_warning="low", priority_critical="default",
    )
    for severity in SEVERITIES:
        await channel.send(_note(severity))
    assert [r["body"]["priority"] for r in received] == [1, 2, 3]


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "number"), [("min", 1), ("low", 2), ("default", 3), ("high", 4), ("max", 5)])
async def test_every_priority_name_becomes_the_number_ntfy_wants(fake_ntfy_server, db_session, tmp_path, test_settings, name, number):
    server_url, received, _ = fake_ntfy_server
    channel, _ctx = await _channel(db_session, tmp_path, test_settings, server_url, priority_warning=name)
    await channel.send(_note(Severity.WARNING))
    assert received[0]["body"]["priority"] == number
    assert type(received[0]["body"]["priority"]) is int  # die JSON-API will eine Zahl, keinen Namen


@pytest.mark.asyncio
@pytest.mark.parametrize("settings", [{}, {"priority_info": None}, {"priority_info": ""}, {"priority_info": "laut"}, {"priority_info": 5}, {"priority_info": "7"}])
async def test_a_missing_or_unknown_priority_keeps_the_old_mapping(fake_ntfy_server, db_session, tmp_path, test_settings, settings):
    server_url, received, _ = fake_ntfy_server
    channel, _ctx = await _channel(db_session, tmp_path, test_settings, server_url, **settings)
    for severity in SEVERITIES:
        await channel.send(_note(severity))
    assert [r["body"]["priority"] for r in received] == [3, 4, 5]


@pytest.mark.asyncio
async def test_one_wrong_value_does_not_change_the_other_levels(fake_ntfy_server, db_session, tmp_path, test_settings):
    server_url, received, _ = fake_ntfy_server
    channel, _ctx = await _channel(db_session, tmp_path, test_settings, server_url, priority_info="kaputt", priority_warning="min")
    for severity in SEVERITIES:
        await channel.send(_note(severity))
    assert [r["body"]["priority"] for r in received] == [3, 1, 5]


def test_priorities_and_minimum_read_names_in_any_case():
    _ntfy_extension_class()
    from nodvard_deck_ext_ntfy import min_severity_from_settings, priorities_from_settings

    assert priorities_from_settings({"priority_info": " MAX ", "priority_warning": "Low"}) == {
        Severity.INFO: 5, Severity.WARNING: 2, Severity.CRITICAL: 5,
    }
    assert min_severity_from_settings({"min_severity": " Warning "}) is Severity.WARNING


def test_build_message_without_priorities_keeps_the_old_mapping():
    _ntfy_extension_class()
    from nodvard_deck_ext_ntfy import build_message

    assert [build_message(_note(s), "t", None)["priority"] for s in SEVERITIES] == [3, 4, 5]
    assert build_message(_note(Severity.WARNING), "t", None, {Severity.WARNING: 1})["priority"] == 1


# --- Schema der Einstellungen -----------------------------------------------------------------------------------


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_the_schema_offers_exactly_what_the_code_understands_and_defaults_to_the_old_behaviour():
    _ntfy_extension_class()
    from nodvard_deck_ext_ntfy import _PRIORITY_BY_SEVERITY, _PRIORITY_NUMBERS

    props = _schema()["properties"]
    assert props["min_severity"]["enum"] == ["info", "warning", "critical"]
    assert props["min_severity"]["default"] == "info"
    for key, severity in (("priority_info", Severity.INFO), ("priority_warning", Severity.WARNING), ("priority_critical", Severity.CRITICAL)):
        spec = props[key]
        assert spec["type"] == "string"
        assert spec["enum"] == list(_PRIORITY_NUMBERS)
        assert _PRIORITY_NUMBERS[spec["default"]] == _PRIORITY_BY_SEVERITY[severity]
    for key in ("min_severity", "priority_info", "priority_warning", "priority_critical"):
        assert set(props[key]["x-enum-labels"]) == set(props[key]["enum"]), key


def test_the_topic_field_offers_a_random_name_that_fits_its_pattern():
    import re

    topic = _schema()["properties"]["topic"]
    spec = topic["x-generate"]
    assert spec["label"] and spec["prefix"] and spec["length"] >= 16  # genug Zufall, damit niemand das Thema erraet
    for filler in ("a", "z", "0", "9"):
        assert re.search(topic["pattern"], spec["prefix"] + filler * spec["length"])
    assert "zufälligen Namen" in topic["description"]


# --- Link zum Abonnieren ------------------------------------------------------------------------------------------

NAME = "display=Nodvard%20Deck"


@pytest.mark.parametrize(
    ("server", "topic", "link"),
    [
        ("https://ntfy.sh", "mein-thema", f"ntfy://ntfy.sh/mein-thema?{NAME}"),
        ("https://ntfy.sh/", "mein-thema", f"ntfy://ntfy.sh/mein-thema?{NAME}"),
        ("http://192.168.2.40:8080", "alarm", f"ntfy://192.168.2.40:8080/alarm?secure=false&{NAME}"),
        ("https://deck.example.org:8443/ntfy/", "alarm", f"ntfy://deck.example.org:8443/ntfy/alarm?{NAME}"),
        ("http://bastel-pi/ntfy", "alarm", f"ntfy://bastel-pi/ntfy/alarm?secure=false&{NAME}"),
        ("HTTPS://Ntfy.Example.ORG", "alarm", f"ntfy://ntfy.example.org/alarm?{NAME}"),
        ("http://[fd00::1]:8080", "alarm", f"ntfy://[fd00::1]:8080/alarm?secure=false&{NAME}"),
        ("  https://ntfy.sh  ", "  mein-thema ", f"ntfy://ntfy.sh/mein-thema?{NAME}"),
        # Sonderzeichen im Thema stehen nie roh im Link (ein "/" wuerde sonst einen Pfad daraus machen)
        ("https://ntfy.sh", "a b/ü?x=1#y", f"ntfy://ntfy.sh/a%20b%2F%C3%BC%3Fx%3D1%23y?{NAME}"),
    ],
)
def test_the_subscribe_link_follows_the_ntfy_deep_link_format(server, topic, link):
    _ntfy_extension_class()
    from nodvard_deck_ext_ntfy import build_subscribe_link

    assert build_subscribe_link(server, topic) == (link, None)


@pytest.mark.parametrize(
    ("server", "link"),
    [
        ("https://name:geheimes-passwort@ntfy.example.org/ntfy?token=tk_geheim#frag", f"ntfy://ntfy.example.org/ntfy/alarm?{NAME}"),
        ("http://name:geheimes-passwort@192.168.2.40:8080", f"ntfy://192.168.2.40:8080/alarm?secure=false&{NAME}"),
    ],
)
def test_the_subscribe_link_carries_no_credentials_from_the_address(server, link):
    """Zugangsdaten, Abfrage und Fragment der eingetragenen Adresse landen weder im Link noch im QR-Code."""
    _ntfy_extension_class()
    from nodvard_deck_ext_ntfy import build_subscribe_link

    assert build_subscribe_link(server, "alarm") == (link, None)
    for secret in ("name:", "geheimes-passwort", "tk_geheim", "frag", "@"):
        assert secret not in link


@pytest.mark.parametrize(
    ("server", "topic", "part"),
    [
        (None, None, "Adresse des ntfy-Servers und das Thema"),
        ("", "", "Adresse des ntfy-Servers und das Thema"),
        (None, "alarm", "Es fehlt noch die Adresse des ntfy-Servers"),
        ("https://ntfy.sh", None, "Es fehlt noch das Thema"),
        ("https://ntfy.sh", "   ", "Es fehlt noch das Thema"),
        ("ntfy.sh", "alarm", "ungültig"),
        ("ftp://ntfy.sh", "alarm", "ungültig"),
        ("https://", "alarm", "ungültig"),
        ("http://ntfy.sh:99999", "alarm", "ungültig"),
        ("http://[fd00::1", "alarm", "ungültig"),
        (5, "alarm", "Es fehlt noch die Adresse des ntfy-Servers"),
    ],
)
def test_the_subscribe_link_says_in_a_sentence_what_is_missing(server, topic, part):
    _ntfy_extension_class()
    from nodvard_deck_ext_ntfy import build_subscribe_link

    link, error = build_subscribe_link(server, topic)
    assert link is None
    assert part in error and error.endswith(".")


@pytest.mark.asyncio
async def test_the_subscribe_link_route_serves_the_saved_settings_and_needs_the_settings_right(client, db_session, test_settings):
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service
    from nodvard_deck.services import extensions as extensions_service

    await extensions_service.discover_and_sync(db_session, test_settings.model_copy(update={"extensions_dir": REPO_EXTENSIONS_DIR}))
    owner = _auth_header(await _bootstrap_owner(client))
    assert (await client.post("/api/v1/extensions/ntfy/enable", headers=owner)).status_code == 200

    # Der Pfad, den das Schema nennt, ist wirklich die Route der Erweiterung.
    schema = (await client.get("/api/v1/extensions/ntfy/settings", headers=owner)).json()["schema"]
    route = "/api/v1" + schema["x-qr-url"]
    assert route == "/api/v1/ext/ntfy/subscribe-link"

    # Noch nichts eingerichtet: ein Satz statt eines Links, kein Fehlerstatus.
    empty = await client.get(route, headers=owner)
    assert empty.status_code == 200
    assert empty.json()["url"] is None and empty.json()["text"] is None
    assert "Adresse des ntfy-Servers und das Thema" in empty.json()["error"]

    saved = await client.put(
        "/api/v1/extensions/ntfy/settings",
        json={"values": {"server_url": "http://192.168.2.40:8080", "topic": "nodvard-abc123"}},
        headers=owner,
    )
    assert saved.status_code == 200, saved.text
    # Ein Zugriffstoken liegt im Tresor, nicht im Link.
    token = await client.post("/api/v1/ext/ntfy/token", json={"value": "tk_geheimes-token-4711"}, headers=owner)
    assert token.status_code == 204

    answer = await client.get(route, headers=owner)
    assert answer.status_code == 200
    body = answer.json()
    assert body["url"] == f"ntfy://192.168.2.40:8080/nodvard-abc123?secure=false&{NAME}"
    assert body["error"] is None and "ntfy-App" in body["text"]
    assert "tk_geheimes-token-4711" not in answer.text

    # Gerechnet wird aus dem GESPEICHERTEN Stand: eine neue Adresse gilt erst nach dem Speichern.
    await client.put("/api/v1/extensions/ntfy/settings", json={"values": {"server_url": "https://ntfy.example.org"}}, headers=owner)
    assert (await client.get(route, headers=owner)).json()["url"] == f"ntfy://ntfy.example.org/nodvard-abc123?{NAME}"

    assert (await client.get(route)).status_code == 401
    roles = await auth_service.ensure_builtin_roles(db_session)
    viewer = User(username="viewer1", password_hash=security.hash_password("whatever123"), is_active=True)
    viewer.roles.append(roles["viewer"])
    db_session.add(viewer)
    await db_session.flush()
    viewer_token = (await client.post("/api/v1/auth/login", json={"username": "viewer1", "password": "whatever123"})).json()["access_token"]
    assert (await client.get(route, headers=_auth_header(viewer_token))).status_code == 403


@pytest.mark.asyncio
async def test_the_real_schema_checks_the_new_fields(client, db_session, test_settings):
    """Durch `PUT /extensions/ntfy/settings`: gueltige Werte werden gespeichert, ein unbekannter Name nicht;
    `x-generate` und `x-qr-url` im Schema stoeren das Speichern nicht."""
    from nodvard_deck.services import extensions as extensions_service

    await extensions_service.discover_and_sync(db_session, test_settings.model_copy(update={"extensions_dir": REPO_EXTENSIONS_DIR}))
    owner = _auth_header(await _bootstrap_owner(client))
    url = "/api/v1/extensions/ntfy/settings"
    good = {"server_url": "https://ntfy.sh", "topic": "nodvard-k3j9x0a8s7d6f5g4h3j2", "min_severity": "warning", "priority_info": "min", "priority_warning": "high", "priority_critical": "max"}
    assert (await client.put(url, json={"values": good}, headers=owner)).status_code == 200
    assert (await client.get(url, headers=owner)).json()["values"] == good
    for key, bad in (("min_severity", "alles"), ("priority_critical", "5"), ("priority_info", "laut")):
        refused = await client.put(url, json={"values": {key: bad}}, headers=owner)
        assert refused.status_code == 422, (key, refused.text)
    assert (await client.get(url, headers=owner)).json()["values"] == good
