"""Push-Nachrichten: weitere Kanaele (Telegram, Discord, Gotify, Pushover, Matrix, Slack, Mattermost, Apprise-API).

Adressen lesen und Anfragen bauen gegen eine aufzeichnende HTTP-Schicht; die Kanaele mit eigenem Server (Gotify,
Mattermost, Apprise-API, Matrix) dazu gegen einen echten lokalen Server -- wie ntfy in test_ext_ntfy.py."""

from __future__ import annotations

import asyncio
import contextlib
import sys

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from local_server_helpers import serve_app
from nodvard_deck.ext.context import build_context
from nodvard_deck.ext.runtime import (
    ExtensionRuntime,
    LoadedExtension,
    reset_extension_runtime,
)
from nodvard_sdk import Notification, Severity
from nodvard_sdk.capabilities import NotificationChannel
from nodvard_sdk.errors import NotificationSkipped
from test_ext_ntfy import (  # noqa: F401 -- _cleanup_sys_path ist eine autouse-Fixture, fake_ntfy_server eine Fixture
    REPO_EXTENSIONS_DIR,
    _cleanup_sys_path,
    _ntfy_extension_class,
    fake_ntfy_server,
)

TG_TOKEN = "123456789:" + "A" * 35
DC_ID = "112233445566778899"
DC_TOKEN = "geheimer-webhook-token_xyz"
PO_USER = "u" * 30
PO_APP = "a" * 30
# Zusammengesetzt, damit Geheimnis-Scanner die erfundene Webhook-Adresse nicht für echt halten.
SLACK_PATH = "T0123ABCD/B0123ABCD/XYZxyz123456"
SLACK_HOOK = "https://hooks.slack.com/services/" + SLACK_PATH
# Wie lange eine Anfrage auf die anderen wartet, bevor sie ohne sie weiterläuft (nur im Fehlerfall relevant).
TOGETHER_WAIT_S = 3.0


def _channels():
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "ntfy" / "src"))
    from nodvard_deck_ext_ntfy import channels

    return channels


def _note(severity: Severity = Severity.WARNING, **payload) -> Notification:
    return Notification(title="Backup fehlgeschlagen", body="VM 100 @everyone", severity=severity, payload=payload)


@pytest.fixture(autouse=True)
def _reset_runtime():
    reset_extension_runtime()
    yield
    reset_extension_runtime()


# ---------------------------------------------------------------------------
# Adressen lesen
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        (f"tgram://{TG_TOKEN}/-1001234567", "telegram"),
        (f"tgram://{TG_TOKEN}/12345/@meinkanal", "telegram"),
        (f"discord://{DC_ID}/{DC_TOKEN}", "discord"),
        (f"https://discord.com/api/webhooks/{DC_ID}/{DC_TOKEN}", "discord"),
        ("gotify://gotify.example:8080/AbCdEf123", "gotify"),
        ("gotifys://push.example/unterpfad/AbCdEf123", "gotify"),
        (f"pover://{PO_USER}@{PO_APP}", "pushover"),
        (f"pover://{PO_USER}@{PO_APP}/handy", "pushover"),
        ("matrixs://syt_token_123@matrix.example/!raum123:matrix.example", "matrix"),
        ("matrixs://syt_token_123@matrix.example/#warnungen", "matrix"),
        ("matrix://syt_token_123@192.168.2.5:8008/#warnungen:matrix.example", "matrix"),
        (f"slack://{SLACK_PATH}", "slack"),
        (SLACK_HOOK, "slack"),
        ("mmosts://chat.example/abcd1234efgh", "mattermost"),
        ("apprise://apprise.example:8000/meinschluessel", "apprise"),
        ("apprises://apprise.example/meinschluessel", "apprise"),
    ],
)
def test_supported_addresses_are_read(url, kind):
    target = _channels().parse_channel_url(url)
    assert target.kind == kind
    # Die Zugangsdaten stehen nie in der Darstellung.
    assert "token" not in repr(target).lower() or "geheim" not in repr(target)
    assert TG_TOKEN not in repr(target) and DC_TOKEN not in repr(target)


@pytest.mark.parametrize(
    "url",
    [
        "", "tgram://kein-token/123", f"tgram://{TG_TOKEN}", f"tgram://{TG_TOKEN}/kein chat",
        f"discord://{DC_ID}", "discord://abc/def", "gotify://host-ohne-token", "gotify://user:pw@host/tok12345",
        f"pover://{PO_USER}", "slack://a/b", "mmost://", "apprise://host/", "mailto://x@example.org",
        "https://example.org/hook/abc", "ftp://host/x", "tgram://" + TG_TOKEN + "/12\n3", 42, None,
        "x" * 3000,
    ],
)
def test_bad_addresses_are_refused_without_quoting_them(url):
    channels = _channels()
    with pytest.raises(channels.ChannelError) as caught:
        channels.parse_channel_url(url)
    text = str(caught.value)
    assert "ungültig" in text or "Zugriffstoken" in text
    if isinstance(url, str) and len(url) > 8:
        assert url.strip() not in text


def test_matrix_needs_an_access_token_not_a_password():
    channels = _channels()
    with pytest.raises(channels.ChannelError, match="Zugriffstoken"):
        channels.parse_channel_url("matrixs://nico:geheim@matrix.example/#raum")
    with pytest.raises(channels.ChannelError, match="Zugriffstoken"):
        channels.parse_channel_url("matrixs://matrix.example/#raum")


# ---------------------------------------------------------------------------
# Anfragen (aufzeichnende HTTP-Schicht)
# ---------------------------------------------------------------------------


class _Http:
    def __init__(self, status: int = 200, json_body=None, fail: BaseException | None = None) -> None:
        self.calls: list[dict] = []
        self.status = status
        self.json_body = json_body if json_body is not None else {"ok": True}
        self.fail = fail

    async def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if self.fail is not None:
            raise self.fail
        return httpx.Response(self.status, json=self.json_body, request=httpx.Request(method, url))


@pytest.mark.asyncio
async def test_telegram_sends_plain_text_to_each_chat():
    channels = _channels()
    http = _Http()
    target = channels.parse_channel_url(f"tgram://{TG_TOKEN}/-100123/@kanalname")
    await channels.send_to(http, target, _note(path="/ext/backups"), "http://deck.example:8080/")
    assert [c["url"] for c in http.calls] == [f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"] * 2
    assert [c["json"]["chat_id"] for c in http.calls] == ["-100123", "@kanalname"]
    text = http.calls[0]["json"]["text"]
    assert text.startswith("⚠️ Backup fehlgeschlagen") and "Öffnen: http://deck.example:8080/ext/backups" in text
    assert "parse_mode" not in http.calls[0]["json"]


@pytest.mark.asyncio
async def test_discord_never_pings_anyone():
    channels = _channels()
    http = _Http(status=204)
    await channels.send_to(http, channels.parse_channel_url(f"discord://{DC_ID}/{DC_TOKEN}"), _note(), None)
    (call,) = http.calls
    assert call["url"] == f"https://discord.com/api/webhooks/{DC_ID}/{DC_TOKEN}?wait=true"
    assert call["json"]["allowed_mentions"] == {"parse": []}
    assert "@everyone" in call["json"]["content"]  # der Text bleibt, nur ohne Ping


@pytest.mark.asyncio
async def test_pushover_slack_and_the_rest_send_what_the_service_expects():
    channels = _channels()
    http = _Http()
    await channels.send_to(http, channels.parse_channel_url(f"pover://{PO_USER}@{PO_APP}/handy/tablet"), _note(Severity.CRITICAL), "http://deck.example")
    # Eine Anfrage fuer alle Geraete: scheitert sie, bekommt keines die Meldung doppelt.
    assert http.calls[0]["data"] == {
        "token": PO_APP, "user": PO_USER, "title": "Backup fehlgeschlagen", "message": "VM 100 @everyone",
        "priority": "1", "url": "http://deck.example/notifications", "device": "handy,tablet",
    }
    assert len(http.calls) == 1
    http = _Http()
    await channels.send_to(http, channels.parse_channel_url(f"slack://{SLACK_PATH}"), _note(), None)
    assert http.calls[0]["url"] == SLACK_HOOK
    assert "Backup fehlgeschlagen" in http.calls[0]["json"]["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "words"),
    [(401, "abgelehnt"), (403, "Mitglied im Chat"), (404, "kennt das Ziel nicht"), (429, "später"), (400, "nicht angenommen"),
     (500, "Fehler"), (302, "leitet")],
)
async def test_errors_are_fixed_sentences_without_the_token(status, words):
    channels = _channels()
    http = _Http(status=status, json_body={"description": f"bot{TG_TOKEN} kaputt"})
    with pytest.raises(channels.ChannelError) as caught:
        await channels.send_to(http, channels.parse_channel_url(f"tgram://{TG_TOKEN}/123"), _note(), None)
    assert words in str(caught.value) and TG_TOKEN not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError(f"cannot connect to https://api.telegram.org/bot{TG_TOKEN}/sendMessage"),
        httpx.ReadTimeout(f"timeout bot{TG_TOKEN}"),
        httpx.RemoteProtocolError(f"illegal header bot{TG_TOKEN}"),
        ValueError(f"komisch bot{TG_TOKEN}"),
    ],
)
async def test_transport_errors_never_carry_the_address(exc):
    channels = _channels()
    with pytest.raises(channels.ChannelError) as caught:
        await channels.send_to(_Http(fail=exc), channels.parse_channel_url(f"tgram://{TG_TOKEN}/123"), _note(), None)
    assert TG_TOKEN not in str(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_below_minimum_and_the_test_message():
    channels = _channels()
    assert channels.below_minimum(_note(Severity.INFO), "warning") is True
    assert channels.below_minimum(_note(Severity.WARNING), "warning") is False
    assert channels.below_minimum(_note(Severity.INFO, test=True), "critical") is False
    assert channels.below_minimum(_note(Severity.INFO), "quatsch") is False
    # Fehlt die Stufe, gilt die Vorgabe des Formulars ("Ab Warnung"): das Formular zeigt sie, speichert sie aber nicht.
    assert channels.below_minimum(_note(Severity.INFO), None) is True
    assert channels.below_minimum(_note(Severity.INFO), "") is True
    assert channels.below_minimum(_note(Severity.WARNING), None) is False


# ---------------------------------------------------------------------------
# Durchgehend: Erweiterung mit echtem lokalem Server
# ---------------------------------------------------------------------------


@pytest.fixture
async def fake_services():
    """Gotify, Mattermost, Apprise-API und Matrix auf einem lokalen Server (echter HTTP-Roundtrip)."""
    app = FastAPI()
    received: list[dict] = []
    # Steuern: status, delay (s), together (so viele Anfragen warten aufeinander). Messen: in_flight, peak (Höchststand).
    answer = {"status": 200, "in_flight": 0, "peak": 0}

    async def record(request: Request, kind: str):
        answer["in_flight"] += 1
        answer["peak"] = max(answer["peak"], answer["in_flight"])
        try:
            if answer.get("together"):
                await _until_peak(answer)
            if answer.get("delay"):
                await asyncio.sleep(answer["delay"])
            body = await request.json() if request.method != "GET" else None
            received.append({"kind": kind, "path": request.url.path, "headers": dict(request.headers), "body": body})
            return JSONResponse({"ok": True}, status_code=answer["status"])
        finally:
            answer["in_flight"] -= 1

    @app.post("/message")
    async def gotify(request: Request):
        return await record(request, "gotify")

    @app.post("/hooks/{hook}")
    async def mattermost(request: Request, hook: str):
        return await record(request, "mattermost")

    @app.post("/notify/{key}")
    async def apprise(request: Request, key: str):
        return await record(request, "apprise")

    @app.get("/_matrix/client/v3/directory/room/{alias}")
    async def matrix_alias(request: Request, alias: str):
        received.append({"kind": "matrix-alias", "path": request.url.path, "headers": dict(request.headers), "body": None})
        if alias != "#warnungen:127.0.0.1":
            return JSONResponse({"errcode": "M_NOT_FOUND"}, status_code=404)
        return {"room_id": "!aufgeloest:127.0.0.1"}

    @app.put("/_matrix/client/v3/rooms/{room}/send/m.room.message/{txn}")
    async def matrix_send(request: Request, room: str, txn: str):
        return await record(request, "matrix")

    # Proben fuer "Verbindung testen" (senden nichts).
    @app.get("/version")
    async def gotify_version(request: Request):
        received.append({"kind": "probe-gotify", "path": request.url.path, "headers": dict(request.headers), "body": None})
        return JSONResponse({"version": "2.5.0"}, status_code=answer.get("probe", 200))

    @app.get("/api/v4/system/ping")
    async def mattermost_ping(request: Request):
        received.append({"kind": "probe-mattermost", "path": request.url.path, "headers": dict(request.headers), "body": None})
        return JSONResponse({"status": "OK"}, status_code=answer.get("probe", 200))

    @app.get("/json/urls/{key}")
    async def apprise_urls(request: Request, key: str):
        received.append({"kind": "probe-apprise", "path": request.url.path, "headers": dict(request.headers), "body": None})
        if key == "leer":
            return Response(status_code=204)
        return JSONResponse({"urls": [{"url": "x"}]}, status_code=answer.get("probe", 200))

    @app.get("/_matrix/client/v3/account/whoami")
    async def matrix_whoami(request: Request):
        received.append({"kind": "probe-matrix", "path": request.url.path, "headers": dict(request.headers), "body": None})
        if request.headers.get("authorization") != "Bearer syt_zugang":
            return JSONResponse({"errcode": "M_UNKNOWN_TOKEN"}, status_code=401)
        return {"user_id": "@deck:127.0.0.1"}

    async with serve_app(app) as url:
        yield url.removeprefix("http://"), received, answer


async def _until_peak(answer: dict) -> None:
    """Hält die Antwort, bis `together` Anfragen zugleich liefen; wer die Kanäle nacheinander abarbeitet, erreicht das nie."""
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(TOGETHER_WAIT_S):
            while answer["peak"] < answer["together"]:
                await asyncio.sleep(0.01)


async def _extension(db_session, tmp_path, test_settings, settings: dict, secrets: dict[str, str]):
    from nodvard_deck.models import ExtensionRecord
    from nodvard_sdk import ExtensionManifest

    manifest = ExtensionManifest(
        id="ntfy", name="Push-Nachrichten", version="0.1.0", api_version="0.1.0",
        entrypoint="nodvard_deck_ext_ntfy:Extension",
        permissions=["net.outbound", "secrets.read:ntfy-token", "secrets.read:push-kanal:*"],
    )
    runtime = ExtensionRuntime()
    loaded = LoadedExtension(manifest=manifest, instance=None, ctx=None, granted_permissions=manifest.permissions)
    ctx = build_context(runtime, loaded, manifest, manifest.permissions, tmp_path / "data", test_settings)
    loaded.ctx = ctx
    db_session.add(ExtensionRecord(id="ntfy", version="0.1.0", api_version="0.1.0", state="enabled"))
    await db_session.flush()
    extension = _ntfy_extension_class()()
    await extension.setup(ctx)
    await ctx.settings.set(settings)
    for label, value in secrets.items():
        await ctx.secrets.create(label=label, kind="generic", value=value)
    loaded.instance = extension
    return runtime.capabilities.query(NotificationChannel)[0], ctx, extension


@pytest.mark.asyncio
async def test_only_extra_channels_work_without_ntfy_and_each_has_its_own_minimum(
    fake_services, db_session, tmp_path, test_settings,
):
    host, received, _answer = fake_services
    channel, ctx, extension = await _extension(db_session, tmp_path, test_settings, {
        "dashboard_url": "http://deck.example",
        "channels": [
            {"name": "gotify", "min_severity": "info"},
            {"name": "mattermost", "min_severity": "critical"},
            {"name": "apprise", "min_severity": "warning"},
            {"name": "matrix", "min_severity": "warning"},
            {"name": "aus", "min_severity": "info", "enabled": False},
        ],
    }, {
        "push-kanal:gotify": f"gotify://{host}/AppToken123",
        "push-kanal:mattermost": f"mmost://{host}/hook12345",
        "push-kanal:apprise": f"apprise://{host}/schluessel",
        "push-kanal:matrix": f"matrix://syt_zugang@{host}/!direkt:127.0.0.1/#warnungen",
        "push-kanal:aus": f"gotify://{host}/NieBenutzt1",
    })
    assert channel.label == "Push-Nachrichten"

    await channel.send(_note(Severity.WARNING))
    kinds = [r["kind"] for r in received]
    assert sorted(kinds) == ["apprise", "gotify", "matrix", "matrix", "matrix-alias"]
    gotify = next(r for r in received if r["kind"] == "gotify")
    assert gotify["headers"]["x-gotify-key"] == "AppToken123"
    assert gotify["body"] == {
        "title": "Backup fehlgeschlagen", "message": "VM 100 @everyone\n\nÖffnen: http://deck.example/notifications", "priority": 6,
    }
    assert next(r for r in received if r["kind"] == "apprise")["body"]["type"] == "warning"
    matrix_paths = sorted(r["path"] for r in received if r["kind"] == "matrix")
    assert matrix_paths[0].startswith("/_matrix/client/v3/rooms/!aufgeloest:127.0.0.1/send/m.room.message/")
    assert all(r["headers"]["authorization"] == "Bearer syt_zugang" for r in received if r["kind"].startswith("matrix"))

    # Info liegt unter der Stufe aller ausser Gotify; nichts unter jeder Stufe -> uebersprungen.
    received.clear()
    await channel.send(_note(Severity.INFO))
    assert [r["kind"] for r in received] == ["gotify"]
    await ctx.settings.set({**(await ctx.settings.get()), "channels": [{"name": "mattermost", "min_severity": "critical"}]})
    with pytest.raises(NotificationSkipped):
        await channel.send(_note(Severity.INFO))

    report = await extension.health(ctx)
    assert report.healthy is True
    assert report.details["mattermost"]["kind"] == "Mattermost"
    assert "zeigt erst die Testnachricht" in report.details["mattermost"]["note"]


@pytest.mark.asyncio
async def test_a_failing_channel_does_not_stop_the_others_and_the_test_message_names_it(
    fake_ntfy_server, fake_services, db_session, tmp_path, test_settings,  # noqa: F811 -- Fixture
):
    ntfy_url, ntfy_received, _ = fake_ntfy_server
    host, received, _answer = fake_services
    channel, ctx, extension = await _extension(db_session, tmp_path, test_settings, {
        "server_url": ntfy_url, "topic": "t",
        "channels": [{"name": "kaputt", "min_severity": "info"}, {"name": "fehlt"}, {"name": "gotify"}],
    }, {
        "push-kanal:kaputt": f"apprise://{host}/schluessel/../../x",
        "push-kanal:gotify": f"gotify://{host}/AppToken123",
    })
    # Normale Meldung: ntfy und Gotify bekommen sie, die beiden kaputten Kanaele stehen im Zustand.
    await channel.send(_note(Severity.WARNING))
    assert len(ntfy_received) == 1 and [r["kind"] for r in received] == ["gotify"]
    report = await extension.health(ctx)
    assert report.healthy is False
    assert report.details["kaputt"]["healthy"] is False and "ungültig" in report.details["kaputt"]["error"]
    assert "fehlt noch die Adresse" in report.details["fehlt"]["error"]
    assert report.details["gotify"]["healthy"] is True and report.details["ntfy"]["healthy"] is True

    # Die Testnachricht nennt jeden Fehler (auch wenn andere Kanaele sie bekommen haben), nie eine Adresse.
    with pytest.raises(Exception) as caught:
        await channel.send(_note(Severity.INFO, test=True))
    text = str(caught.value)
    assert "„kaputt“" in text and "„fehlt“" in text and "schluessel" not in text and host not in text


@pytest.mark.asyncio
async def test_nothing_set_up_is_still_an_error(db_session, tmp_path, test_settings):
    channel, _ctx, extension = await _extension(db_session, tmp_path, test_settings, {"channels": []}, {})
    with pytest.raises(RuntimeError, match="noch nicht eingerichtet"):
        await channel.send(_note())
    assert (await extension.health(_ctx)).healthy is False


@pytest.mark.asyncio
async def test_a_refusing_service_is_reported_when_nothing_arrived(fake_services, db_session, tmp_path, test_settings):
    host, _received, answer = fake_services
    answer["status"] = 401
    channel, _ctx, _extension_obj = await _extension(db_session, tmp_path, test_settings, {
        "channels": [{"name": "gotify", "min_severity": "info"}],
    }, {"push-kanal:gotify": f"gotify://{host}/AppToken123"})
    with pytest.raises(Exception, match="Gotify hat den Zugang abgelehnt") as caught:
        await channel.send(_note())
    assert "AppToken123" not in str(caught.value)


# ---------------------------------------------------------------------------
# Nach der Gegenpruefung: Teilfehler, Nebenlaeufigkeit, Proben, Erwaehnungen
# ---------------------------------------------------------------------------


class _ByUrl(_Http):
    """Antwortet je nach Empfaenger (`chat_id`) bzw. Pfad mit einem anderen Status."""

    def __init__(self, statuses: dict[str, int]) -> None:
        super().__init__()
        self.statuses = statuses

    async def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        key = str((kwargs.get("json") or {}).get("chat_id") or url)
        status = next((v for k, v in self.statuses.items() if k in key), 200)
        return httpx.Response(status, json={"ok": status == 200}, request=httpx.Request(method, url))


@pytest.mark.asyncio
async def test_one_refusing_chat_does_not_stop_the_others():
    channels = _channels()
    http = _ByUrl({"111": 403})
    partial = await channels.send_to(http, channels.parse_channel_url(f"tgram://{TG_TOKEN}/111/222/333"), _note(), None)
    assert [c["json"]["chat_id"] for c in http.calls] == ["111", "222", "333"]
    assert partial.startswith("1 von 3 Empfängern nicht erreicht") and "Mitglied im Chat" in partial
    assert TG_TOKEN not in partial

    with pytest.raises(channels.ChannelError) as caught:
        await channels.send_to(_ByUrl({"": 500}), channels.parse_channel_url(f"tgram://{TG_TOKEN}/111/222"), _note(), None)
    assert str(caught.value).count("Telegram meldet einen Fehler") == 1  # gleiche Saetze nur einmal


@pytest.mark.asyncio
async def test_matrix_unknown_alias_does_not_stop_the_other_room_and_never_pings_the_room():
    channels = _channels()
    http = _ByUrl({"directory/room": 404})
    target = channels.parse_channel_url("matrixs://syt_zugang@matrix.example/!direkt:matrix.example/#weg:matrix.example")
    partial = await channels.send_to(http, target, _note(), None)
    sent = [c for c in http.calls if c["method"] == "PUT"]
    assert len(sent) == 1 and "/rooms/%21direkt%3Amatrix.example/" in sent[0]["url"]
    assert sent[0]["json"]["msgtype"] == "m.text" and sent[0]["json"]["m.mentions"] == {}
    assert partial.startswith("1 von 2 Empfängern") and "Matrix kennt das Ziel nicht" in partial


@pytest.mark.asyncio
async def test_slack_and_mattermost_mentions_are_defused():
    channels = _channels()
    note = Notification(title="Login von <!channel>", body="Benutzer @all & <@U123>", severity=Severity.WARNING)
    http = _Http()
    await channels.send_to(http, channels.parse_channel_url(f"slack://{SLACK_PATH}"), note, None)
    text = http.calls[0]["json"]["text"]
    assert "<!channel>" not in text and "&lt;!channel&gt;" in text and "&amp;" in text
    http = _Http()
    await channels.send_to(http, channels.parse_channel_url("mmosts://chat.example/abcd1234efgh"), note, None)
    text = http.calls[0]["json"]["text"]
    assert "@all" not in text and "@\u200ball" in text


def test_lenient_spellings_are_read():
    channels = _channels()
    assert channels.parse_channel_url(f"tgram://bot{TG_TOKEN}/123").parts["token"] == TG_TOKEN
    assert channels.parse_channel_url("gotify://gotify_server:80/AbCdEf123").parts["host"] == "gotify_server"


class _Telegram(_Http):
    """Bot-API mit getMe/getChatMember: `members` legt den Status je Chat fest."""

    def __init__(self, members: dict[str, dict]) -> None:
        super().__init__()
        self.members = members

    async def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if url.endswith("/getMe"):
            body = {"ok": True, "result": {"id": 4242, "is_bot": True}}
        else:
            chat = str(kwargs["json"]["chat_id"])
            assert kwargs["json"]["user_id"] == 4242
            body = {"ok": True, "result": self.members.get(chat, {"status": "left"})}
        return httpx.Response(200, json=body, request=httpx.Request(method, url))


@pytest.mark.asyncio
async def test_telegram_probe_checks_that_the_bot_may_post_in_each_group():
    channels = _channels()
    http = _Telegram({"-100111": {"status": "member"}, "@kanal": {"status": "administrator"}, "-100333": {"status": "left"}})
    with pytest.raises(channels.ChannelError, match="1 von 3 Gruppen") as caught:
        await channels.probe(http, channels.parse_channel_url(f"tgram://{TG_TOKEN}/-100111/@kanal/-100333"))
    assert "darf in diesen Chat nicht schreiben" in str(caught.value) and TG_TOKEN not in str(caught.value)
    assert [c["url"].rsplit("/", 1)[1] for c in http.calls] == ["getMe"] + ["getChatMember"] * 3
    assert not any("sendMessage" in c["url"] for c in http.calls)

    restricted = _Telegram({"-100111": {"status": "restricted", "can_send_messages": False}})
    with pytest.raises(channels.ChannelError):
        await channels.probe(restricted, channels.parse_channel_url(f"tgram://{TG_TOKEN}/-100111"))
    assert await channels.probe(_Telegram({"-100111": {"status": "member"}}), channels.parse_channel_url(f"tgram://{TG_TOKEN}/-100111")) is None
    # Private Chats laesst Telegram so nicht pruefen: Hinweis statt Entwarnung.
    note = await channels.probe(_Telegram({}), channels.parse_channel_url(f"tgram://{TG_TOKEN}/12345"))
    assert "Testnachricht" in note


@pytest.mark.asyncio
async def test_other_probes_check_tokens_without_sending():
    channels = _channels()
    http = _Http()
    assert await channels.probe(http, channels.parse_channel_url(f"pover://{PO_USER}@{PO_APP}/handy/tablet")) is None
    assert [c["url"] for c in http.calls] == ["https://api.pushover.net/1/users/validate.json"] * 2
    assert sorted(c["data"]["device"] for c in http.calls) == ["handy", "tablet"]  # je Geraet eine Pruefung

    http = _Http(status=401)
    with pytest.raises(channels.ChannelError, match="Discord hat den Zugang abgelehnt"):
        await channels.probe(http, channels.parse_channel_url(f"discord://{DC_ID}/{DC_TOKEN}"))
    assert http.calls[0]["method"] == "GET"

    http = _Http()
    await channels.probe(http, channels.parse_channel_url("apprise://apprise.example/schluessel"))
    assert http.calls[0]["url"] == "http://apprise.example/json/urls/schluessel?privacy=1"

    note = await channels.probe(_Http(), channels.parse_channel_url(f"slack://{SLACK_PATH}"))
    assert "Testnachricht" in note


@pytest.mark.asyncio
async def test_matrix_probe_checks_membership_in_each_room():
    channels = _channels()

    class _Matrix(_Http):
        async def request(self, method, url, **kwargs):
            self.calls.append({"method": method, "url": url, **kwargs})
            if "/directory/room/" in url:
                body = {"room_id": "!aus-alias:matrix.example"}
            elif url.endswith("/joined_rooms"):
                body = {"joined_rooms": ["!direkt:matrix.example"]}
            else:
                body = {"user_id": "@deck:matrix.example"}
            return httpx.Response(200, json=body, request=httpx.Request(method, url))

    target = channels.parse_channel_url("matrixs://syt_zugang@matrix.example/!direkt:matrix.example/#warnungen:matrix.example")
    with pytest.raises(channels.ChannelError, match="in 1 Raum/Räumen nicht Mitglied"):
        await channels.probe(_Matrix(), target)
    ok = channels.parse_channel_url("matrixs://syt_zugang@matrix.example/!direkt:matrix.example")
    assert "in allen Räumen" in await channels.probe(_Matrix(), ok)


def test_the_name_ntfy_is_taken():
    channels = _channels()
    assert not channels.NAME_PATTERN.fullmatch("ntfy") and not channels.NAME_PATTERN.fullmatch("NTFY")
    assert channels.NAME_PATTERN.fullmatch("ntfy2") and channels.NAME_PATTERN.fullmatch("telegram")


@pytest.mark.asyncio
async def test_a_slow_recipient_does_not_turn_a_partial_delivery_into_a_failure(monkeypatch):
    """Die Empfaenger laufen nebeneinander, jede Anfrage hat ihre Frist: ein haengender Chat kostet nur sich selbst,
    der Kanal gilt als zugestellt (sonst schickte ein Wiederholversuch den anderen doppelt)."""
    channels = _channels()
    monkeypatch.setattr(channels, "REQUEST_TIMEOUT_S", 0.2)

    class _Hanging(_Http):
        async def request(self, method, url, **kwargs):
            self.calls.append({"method": method, "url": url, **kwargs})
            if kwargs["json"]["chat_id"] == "333":
                await asyncio.Event().wait()  # antwortet nie
            return httpx.Response(200, json={"ok": True}, request=httpx.Request(method, url))

    # Ohne Frist je Anfrage käme nie eine Antwort; die Obergrenze liegt weit über 0,2 s und unter der echten Frist (7 s).
    async with asyncio.timeout(3):
        partial = await channels.send_to(_Hanging(), channels.parse_channel_url(f"tgram://{TG_TOKEN}/111/222/333"), _note(), None)
    assert partial.startswith("1 von 3 Empfängern nicht erreicht") and "Zeitüberschreitung" in partial


@pytest.mark.asyncio
async def test_channels_run_side_by_side_each_with_its_own_time_limit(
    fake_services, db_session, tmp_path, test_settings, monkeypatch,
):
    host, received, answer = fake_services
    channel, _ctx, _extension_obj = await _extension(db_session, tmp_path, test_settings, {
        "channels": [{"name": n, "min_severity": "info"} for n in ("gotify", "mattermost", "apprise")],
    }, {
        "push-kanal:gotify": f"gotify://{host}/AppToken123",
        "push-kanal:mattermost": f"mmost://{host}/hook12345",
        "push-kanal:apprise": f"apprise://{host}/schluessel",
    })
    answer["together"] = 3  # jede Antwort wartet auf die beiden anderen Anfragen
    await channel.send(_note())
    assert answer["peak"] == 3, "die Kanäle liefen nicht nebeneinander"
    assert len(received) == 3

    # Die Frist läuft schon während der Tresor-Abfrage; ein Abbruch dort ließe die geteilte Test-Datenbankverbindung
    # hängen. Darum liegen die Adressen vor der Frist bereit.
    targets = {name: await type(channel)._target(channel, name) for name in ("gotify", "mattermost", "apprise")}

    async def resolved(self, name):
        return targets[name]

    monkeypatch.setattr(type(channel), "_target", resolved)
    answer.update(together=None, delay=0.5)
    ntfy_module = sys.modules["nodvard_deck_ext_ntfy"]
    monkeypatch.setattr(ntfy_module, "CHANNEL_TIMEOUT_S", 0.1)
    with pytest.raises(Exception, match="Zeitüberschreitung") as caught:
        await channel.send(_note())
    assert caught.value.verbatim is True and str(caught.value).count("Zeitüberschreitung") == 3


@pytest.mark.asyncio
async def test_health_probes_and_forgets_an_old_error_after_a_new_address(fake_services, db_session, tmp_path, test_settings):
    host, received, answer = fake_services
    channel, ctx, extension = await _extension(db_session, tmp_path, test_settings, {
        "channels": [{"name": "gotify", "min_severity": "info"}, {"name": "leer", "min_severity": "info"}],
    }, {
        "push-kanal:gotify": f"gotify://{host}/AppToken123",
        "push-kanal:leer": f"apprise://{host}/leer",
    })
    answer["status"] = 500
    with pytest.raises(Exception, match="Gotify meldet einen Fehler"):
        await channel.send(_note())
    answer["status"] = 200
    report = await extension.health(ctx)
    assert "Beim letzten Senden" in report.details["gotify"]["error"]
    assert "keine Ziele" in report.details["leer"]["error"]
    assert not any(r["kind"] in ("gotify", "apprise") for r in received if r["body"] is None)

    # Neue Adresse: der alte Fehler gilt nicht mehr.
    secret = await ctx.secrets.get_handle("push-kanal:gotify")
    from nodvard_deck.db.session import session_scope
    from nodvard_deck.models import Secret
    async with session_scope() as session:
        await session.delete(await session.get(Secret, secret.id))
    await ctx.secrets.create(label="push-kanal:gotify", kind="generic", value=f"gotify://{host}/NeuesToken1")
    report = await extension.health(ctx)
    assert report.details["gotify"]["healthy"] is True and "note" in report.details["gotify"]


def test_subscribe_link_does_not_nag_when_only_channels_are_used():
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "ntfy" / "src"))
    from nodvard_deck_ext_ntfy import subscribe_link_for

    only_channels = subscribe_link_for({"channels": [{"name": "telegram"}]})
    assert only_channels.url is None and "brauchen ihn nicht" in only_channels.error
    assert "fehlen noch" in (subscribe_link_for({}).error or "") or subscribe_link_for({}).error
    assert subscribe_link_for({"server_url": "https://ntfy.example", "topic": "t"}).url


def test_setup_needs_either_ntfy_or_a_channel():
    import json

    from nodvard_deck.services.extension_setup import missing_fields

    schema = json.loads((REPO_EXTENSIONS_DIR / "ntfy" / "settings.schema.json").read_text(encoding="utf-8"))
    empty = missing_fields(schema, {})
    assert len(empty) == 1 and "– oder einen Eintrag bei „Weitere Kanäle“" in empty[0]
    assert missing_fields(schema, {"topic": "t"}) == empty
    assert missing_fields(schema, {"server_url": "https://ntfy.example", "topic": "t"}) == []
    assert missing_fields(schema, {"channels": [{"name": "telegram"}]}) == []


def test_settings_refuse_more_channels_than_allowed():
    from fastapi import HTTPException
    from nodvard_deck.api.v1.extension_settings import _check

    schema = {"type": "object", "properties": {"channels": {"type": "array", "title": "Weitere Kanäle", "maxItems": 2, "items": {"type": "object"}}}}
    _check({"channels": [{}, {}]}, schema, "settings")
    with pytest.raises(HTTPException) as caught:
        _check({"channels": [{}, {}, {}]}, schema, "settings")
    assert caught.value.status_code == 422 and "höchstens 2" in caught.value.detail


def test_verbatim_errors_and_notes_reach_the_user_unchanged():
    from nodvard_deck.services.extension_test import translate_exception

    class _Many(RuntimeError):
        readable = True
        verbatim = True

    text = "„a“: Gotify kennt das Ziel nicht (HTTP 404). „b“: Telegram antwortet nicht (Zeitüberschreitung)."
    result = translate_exception(_Many(text), host="deck.example", tls_hint=None, secrets=[])
    assert result.message == text


@pytest.mark.asyncio
async def test_connection_test_keeps_verbatim_details_and_scrubs_notes():
    from types import SimpleNamespace

    from nodvard_deck.services.extension_test import run_connection_test
    from nodvard_sdk import HealthReport

    sentence = "Telegram lässt den Bot nicht in diesen Chat schreiben (HTTP 403). Ist der Bot Mitglied im Chat?"

    class _Ext:
        async def health(self, ctx):  # noqa: ANN001
            return HealthReport(healthy=False, message="x", details={
                "telegram": {"healthy": False, "error": sentence, "verbatim": True},
                "gotify": {"healthy": True, "note": "Gotify ist erreichbar, Token geheim-123456 prüft die Testnachricht."},
                "alt": {"healthy": False, "error": "HTTP 403"},
            })

    outcome = await run_connection_test(
        SimpleNamespace(instance=_Ext(), ctx=None), settings={}, schema=None, secrets=["geheim-123456"], channel=None,
    )
    by_name = {d.name: d.message for d in outcome.details}
    assert by_name["telegram"] == sentence  # nicht „dem Konto fehlt ein Recht“
    assert "geheim-123456" not in by_name["gotify"] and "***" in by_name["gotify"]
    assert by_name["alt"].startswith("Zugriff verweigert")  # ohne `verbatim` wie bisher übersetzt


def test_verbatim_sentences_are_not_cut_after_300_characters():
    from nodvard_deck.services.extension_test import translate_exception

    class _Many(RuntimeError):
        readable = True
        verbatim = True

    text = " ".join(f"„kanal{i}“: Gotify kennt das Ziel nicht (HTTP 404). Prüfe die Adresse des Kanals." for i in range(6))
    assert len(text) > 300
    assert translate_exception(_Many(text), host=None, tls_hint=None, secrets=[]).message == text
