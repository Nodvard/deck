"""Fingerabdruck des Zertifikats je Verbindung (Proxmox VE und Proxmox-Backups): merken, pruefen, nach einem Wechsel neu
bestaetigen -- gegen den TLS-Nachbau von Proxmox aus `test_ext_proxmox_password_connect.py` (echter Handshake mit
selbstsigniertem Zertifikat, das der Test mitten im Lauf tauschen kann)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import httpx
import pytest
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import ExtensionRecord
from nodvard_deck.models.security import AuditEntry
from nodvard_deck.services import extension_test
from nodvard_sdk import CertificateMismatch
from sqlalchemy import select
from test_ext_proxmox_password_connect import (  # noqa: F401 -- _cleanup_sys_path ist autouse, fake_pve ein Fixture
    _cleanup_sys_path,
    _connect,
    _h,
    _owner_with_proxmox,
    _settings,
    _user_token,
    fake_pve,
    trusted_ca,
)

REPO = Path(__file__).resolve().parents[2]
EXTENSIONS = ("proxmox", "backups")
PIN_A = ":".join(["AB"] * 32)


def _module(ext_id: str, name: str):
    sys.path.insert(0, str(REPO / "extensions" / ext_id / "src"))
    package = "nodvard_deck_ext_proxmox" if ext_id == "proxmox" else "nodvard_deck_ext_backups"
    return __import__(f"{package}.{name}", fromlist=["_"])


async def _new_handshakes(*ext_ids: str) -> None:
    """Gepinnte Clients schliessen: die naechste Anfrage baut eine neue TLS-Verbindung auf (wie nach dem Neustart von
    pveproxy, den ein neues Zertifikat in Proxmox ausloest)."""
    for ext_id in ext_ids:
        loaded = get_extension_runtime().loaded.get(ext_id)
        if loaded is None:
            continue
        http = loaded.ctx.http
        while http._pinned_clients:
            _key, client = http._pinned_clients.popitem(last=False)
            await client.aclose()


async def _ext_settings(db_session, ext_id: str) -> dict:
    record = await db_session.get(ExtensionRecord, ext_id)
    await db_session.refresh(record)
    return dict(record.settings or {})


async def _health(ext_id: str):
    loaded = get_extension_runtime().loaded[ext_id]
    return await loaded.instance.health(loaded.ctx)


async def _confirmed_rows(db_session, ext_id: str) -> list[AuditEntry]:
    return list((await db_session.execute(
        select(AuditEntry).where(AuditEntry.action == f"{ext_id}.certificate_confirmed").order_by(AuditEntry.ts)
    )).scalars())


# ---------------------------------------------------------------------------
# Schreibweise
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ext_id", EXTENSIONS)
def test_fingerprints_are_normalized_and_garbage_is_refused(ext_id):
    certificate = _module(ext_id, "certificate")
    for value in ("ab" * 32, PIN_A.lower(), " ".join(["AB"] * 32), "sha256 " + "AB" * 32, f"  {PIN_A}  "):
        assert certificate.normalize_fingerprint(value) == PIN_A, value
    for empty in (None, "", "   "):
        assert certificate.normalize_fingerprint(empty) is None
    for bad in ("AB" * 31, "AB" * 33, "ZZ" * 32, 42, "AB" * 32 + ":" * 200):
        with pytest.raises(ValueError, match="ungültig"):
            certificate.normalize_fingerprint(bad)
    assert certificate.connection_pin({"tls_fingerprint_sha256": "ab" * 32}) == (PIN_A, False)
    assert certificate.connection_pin({}) == (None, False)
    assert certificate.connection_pin({"tls_fingerprint_sha256": "kaputt"}) == (None, True)


@pytest.mark.parametrize("ext_id", EXTENSIONS)
def test_the_settings_field_accepts_what_the_extension_accepts(ext_id):
    schema = json.loads((REPO / "extensions" / ext_id / "settings.schema.json").read_text(encoding="utf-8"))
    props = schema["properties"]["connections"]["items"]["properties"]
    field = props["tls_fingerprint_sha256"]
    assert field["title"] == "Fingerabdruck des Zertifikats (SHA-256)"
    assert "Veraltet" in props["tls_insecure_skip_verify"]["description"]
    assert props["tls_insecure_skip_verify"]["x-advanced"] is True
    pattern = re.compile(field["pattern"])
    for ok in (PIN_A, "ab" * 32, PIN_A.lower(), "", "  "):
        assert pattern.search(ok), ok
    for bad in ("AB" * 31, "ZZ" * 32, "AB" * 33, "nicht-hex"):
        assert not pattern.search(bad), bad


# ---------------------------------------------------------------------------
# Connectoren (HTTP-Schicht nachgebaut)
# ---------------------------------------------------------------------------


class _RecordingHttp:
    def __init__(self, *, fail: BaseException | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail

    async def request(self, method, url, **kwargs):
        self.calls.append(("request", kwargs))
        if self.fail is not None:
            raise self.fail
        if url.endswith("/vncproxy"):
            return httpx.Response(200, json={"data": {"ticket": "t", "port": 5900}}, request=httpx.Request(method, url))
        return httpx.Response(200, json={"data": {"version": "9.0.3"}}, request=httpx.Request(method, url))

    def websocket(self, url, **kwargs):
        self.calls.append(("websocket", kwargs))

        class _Ws:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *exc):
                return None

        return _Ws()


def _connector(ext_id: str, http: _RecordingHttp, **tls):
    module = _module(ext_id, "connector")
    cls = module.ProxmoxConnector if ext_id == "proxmox" else module.ProxmoxBackupConnector
    error = module.ProxmoxApiError if ext_id == "proxmox" else module.ProxmoxBackupApiError
    ctx = type("C", (), {"http": http})()
    return cls(ctx, base_url="https://pve.example:8006", token_id="a@pve!t", token_secret="geheim-1234", **tls), error


@pytest.mark.asyncio
@pytest.mark.parametrize("ext_id", EXTENSIONS)
async def test_a_remembered_fingerprint_goes_into_every_call_instead_of_skipping_the_check(ext_id):
    http = _RecordingHttp()
    connector, _error = _connector(ext_id, http, tls_insecure_skip_verify=True, tls_fingerprint_sha256="ab" * 32)
    assert connector.pinned is True
    await connector._request("GET", "/version")
    if ext_id == "proxmox":
        session = await connector.open_vnc("pve1", "qemu", "100")
        await session.close()
    assert http.calls, "nichts aufgerufen"
    for kind, kwargs in http.calls:
        assert kwargs.get("pinned_sha256") == PIN_A, kind
        assert "insecure_tls" not in kwargs, kind
    if ext_id == "proxmox":
        assert [kind for kind, _ in http.calls] == ["request", "request", "websocket"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ext_id", EXTENSIONS)
@pytest.mark.parametrize("insecure", [True, False])
async def test_without_a_fingerprint_the_old_switch_still_applies(ext_id, insecure):
    http = _RecordingHttp()
    connector, _error = _connector(ext_id, http, tls_insecure_skip_verify=insecure)
    assert connector.pinned is False
    await connector._request("GET", "/version")
    ((_kind, kwargs),) = http.calls
    assert kwargs.get("insecure_tls") is insecure
    assert "pinned_sha256" not in kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("ext_id", EXTENSIONS)
async def test_an_invalid_fingerprint_blocks_the_connection_instead_of_skipping_the_check(ext_id):
    """Steht im Feld kein Fingerabdruck, geht nichts hinaus -- nie still "nicht pruefen", auch nicht mit dem alten
    Schalter."""
    http = _RecordingHttp()
    connector, error = _connector(ext_id, http, tls_insecure_skip_verify=True, tls_fingerprint_sha256="kaputt")
    with pytest.raises(error) as caught:
        await connector._request("GET", "/version")
    assert caught.value.code == "certificate_invalid"
    assert "Fingerabdruck" in str(caught.value) and "ungültig" in str(caught.value)
    if ext_id == "proxmox":
        with pytest.raises(error):
            await connector.open_vnc("pve1", "qemu", "100")
    assert http.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("ext_id", EXTENSIONS)
async def test_a_different_certificate_is_a_fixed_error_with_the_way_out(ext_id):
    mismatch = CertificateMismatch(PIN_A, ":".join(["CD"] * 32))
    wrapped = httpx.ConnectError("[SSL] handshake")
    wrapped.__cause__ = mismatch
    connector, error = _connector(ext_id, _RecordingHttp(fail=wrapped), tls_fingerprint_sha256=PIN_A)
    with pytest.raises(error) as caught:
        await connector._request("GET", "/version")
    assert caught.value.code == "certificate_changed"
    text = str(caught.value)
    assert "anderes Zertifikat als das gemerkte" in text
    assert "Knoten → System → Zertifikate" in text and "Verbindungen verwalten" in text
    assert "Selbstsigniertes Zertifikat erlauben" not in text


@pytest.mark.parametrize("ext_id", EXTENSIONS)
def test_an_untrusted_certificate_points_to_the_fingerprint_not_to_the_switch(ext_id):
    import ssl

    transport = _module(ext_id, "transport")
    text = transport.transport_text(ssl.SSLCertVerificationError("certificate verify failed"), "https://pve.example:8006")
    assert "Fingerabdruck" in text
    assert "Selbstsigniertes Zertifikat erlauben" not in text


# ---------------------------------------------------------------------------
# Durchgehend: verbinden, Zertifikat wechselt, neu bestaetigen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_changed_certificate_stops_the_connection_until_the_new_one_is_confirmed(
    client, db_session, test_settings, fake_pve,  # noqa: F811 -- Fixture
):
    token = await _owner_with_proxmox(client, db_session, test_settings, backups=[])
    r = await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])
    assert r.status_code == 200, r.text
    old = fake_pve["fingerprint"]
    assert (await _health("proxmox")).healthy is True

    # Proxmox bekommt ein neues Zertifikat: beide Verbindungen halten an, bevor das Token hinausgeht.
    fake_pve["state"]["swap"]()
    new = fake_pve["swapped"][-1]
    await _new_handshakes("proxmox", "backups")
    requests_before = len(fake_pve["state"]["requests"])
    for ext_id in EXTENSIONS:
        report = await _health(ext_id)
        assert report.healthy is False
        assert report.details["pve1"]["code"] == "certificate_changed", ext_id
        assert "anderes Zertifikat als das gemerkte" in report.details["pve1"]["error"]
    assert len(fake_pve["state"]["requests"]) == requests_before

    for ext_id in EXTENSIONS:
        shown = await client.get(f"/api/v1/ext/{ext_id}/connections/pve1/certificate", headers=_h(token))
        assert shown.status_code == 200, shown.text
        assert shown.json() | {"subject": None, "issuer": None, "not_after": None} == {
            "sha256": new, "trusted": False, "stored": old, "stored_invalid": False, "matches": False,
            "subject": None, "issuer": None, "not_after": None,
        }
        # Der alte Wert (oder ein vertippter) wird nicht gespeichert: 409 mit dem, was der Server jetzt zeigt.
        refused = await client.post(
            f"/api/v1/ext/{ext_id}/connections/pve1/certificate", json={"sha256": old}, headers=_h(token),
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["code"] == "certificate_mismatch" and refused.json()["sha256"] == new
        assert (await _ext_settings(db_session, ext_id))["connections"][0]["tls_fingerprint_sha256"] == old

        confirmed = await client.post(
            f"/api/v1/ext/{ext_id}/connections/pve1/certificate",
            json={"sha256": new.replace(":", "").lower()}, headers=_h(token),
        )
        assert confirmed.status_code == 200, confirmed.text
        assert (confirmed.json()["stored"], confirmed.json()["matches"]) == (new, True)
        conn = (await _ext_settings(db_session, ext_id))["connections"][0]
        assert (conn["tls_fingerprint_sha256"], conn["tls_insecure_skip_verify"]) == (new, False)
        (row,) = await _confirmed_rows(db_session, ext_id)
        assert row.outcome == "success" and row.actor_type == "user"
        assert row.detail == {
            "connection": "pve1", "address": fake_pve["base_url"], "fingerprint": new, "previous_fingerprint": old,
            "trusted": False, "via": ext_id,
        }
        assert (await _health(ext_id)).healthy is True, ext_id


@pytest.mark.asyncio
async def test_an_old_unchecked_connection_can_remember_its_fingerprint(client, db_session, test_settings, fake_pve):  # noqa: F811 -- Fixture
    """Eine Verbindung von frueher mit "Selbstsigniertes Zertifikat erlauben": Fingerabdruck merken schaltet die
    Pruefung wieder ein (genau dieses Zertifikat)."""
    token = await _owner_with_proxmox(client, db_session, test_settings)
    assert (await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])).status_code == 200
    record = await db_session.get(ExtensionRecord, "proxmox")
    conn = dict(record.settings["connections"][0])
    conn.pop("tls_fingerprint_sha256")
    conn["tls_insecure_skip_verify"] = True
    record.settings = {"connections": [conn]}
    await db_session.flush()

    listed = (await client.get("/api/v1/ext/proxmox/connections", headers=_h(token))).json()
    assert (listed[0]["tls_insecure_skip_verify"], listed[0]["tls_fingerprint_sha256"]) == (True, None)
    shown = (await client.get("/api/v1/ext/proxmox/connections/pve1/certificate", headers=_h(token))).json()
    assert (shown["sha256"], shown["stored"], shown["matches"], shown["trusted"]) == (fake_pve["fingerprint"], None, False, False)
    saved = await client.post(
        "/api/v1/ext/proxmox/connections/pve1/certificate", json={"sha256": fake_pve["fingerprint"]}, headers=_h(token),
    )
    assert saved.status_code == 200, saved.text
    conn = (await _settings(db_session))["connections"][0]
    assert (conn["tls_fingerprint_sha256"], conn["tls_insecure_skip_verify"]) == (fake_pve["fingerprint"], False)
    (row,) = await _confirmed_rows(db_session, "proxmox")
    assert row.detail["previous_fingerprint"] is None
    assert (await _health("proxmox")).healthy is True


@pytest.mark.asyncio
@pytest.mark.parametrize("ext_id", EXTENSIONS)
async def test_only_admins_may_look_at_or_confirm_the_certificate(client, db_session, test_settings, fake_pve, ext_id):  # noqa: F811 -- Fixture
    token = await _owner_with_proxmox(client, db_session, test_settings, backups=[])
    assert (await _connect(client, token, fake_pve["base_url"], fingerprint=fake_pve["fingerprint"])).status_code == 200
    operator = await _user_token(client, db_session, username="op1", role="operator")
    path = f"/api/v1/ext/{ext_id}/connections/pve1/certificate"
    assert (await client.get(path, headers=_h(operator))).status_code == 403
    assert (await client.post(path, json={"sha256": PIN_A}, headers=_h(operator))).status_code == 403
    assert await _confirmed_rows(db_session, ext_id) == []
    # Unbekannte Verbindung und kein Fingerabdruck: kein Handshake, klare Antwort.
    missing = await client.get(f"/api/v1/ext/{ext_id}/connections/gibts-nicht/certificate", headers=_h(token))
    assert missing.status_code == 404
    bad = await client.post(path, json={"sha256": "kein-fingerabdruck"}, headers=_h(token))
    assert bad.status_code == 400 and "ungültig" in bad.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ext_id", EXTENSIONS)
async def test_connections_routes_take_normalize_and_drop_the_fingerprint(client, db_session, test_settings, fake_pve, ext_id):  # noqa: F811 -- Fixture
    token = await _owner_with_proxmox(client, db_session, test_settings, backups=[])
    base = f"/api/v1/ext/{ext_id}/connections"
    created = await client.post(
        base, json={
            "name": "neu", "base_url": "https://192.168.2.20:8006", "token_id": "a@pve!t",
            "tls_insecure_skip_verify": True, "tls_fingerprint_sha256": "ab" * 32,
        }, headers=_h(token),
    )
    assert created.status_code == 201, created.text
    assert (created.json()["tls_fingerprint_sha256"], created.json()["tls_insecure_skip_verify"]) == (PIN_A, False)
    refused = await client.post(
        base, json={"name": "kaputt", "base_url": "https://192.168.2.21:8006", "token_id": "a@pve!t", "tls_fingerprint_sha256": "xyz"},
        headers=_h(token),
    )
    assert refused.status_code == 422
    # Neue Adresse: der gemerkte Fingerabdruck gehoert zum alten Server und faellt weg.
    moved = await client.put(f"{base}/neu", json={"base_url": "https://192.168.2.22:8006"}, headers=_h(token))
    assert moved.status_code == 200, moved.text
    assert moved.json()["tls_fingerprint_sha256"] is None
    pinned = await client.put(f"{base}/neu", json={"tls_fingerprint_sha256": PIN_A.lower()}, headers=_h(token))
    assert pinned.json()["tls_fingerprint_sha256"] == PIN_A
    cleared = await client.put(f"{base}/neu", json={"tls_fingerprint_sha256": ""}, headers=_h(token))
    assert cleared.json()["tls_fingerprint_sha256"] is None
    stored = next(c for c in (await _ext_settings(db_session, ext_id))["connections"] if c["name"] == "neu")
    assert "tls_fingerprint_sha256" not in stored
    # Steht im Einstellungsfeld etwas Ungueltiges, zeigt die Liste es an (die Verbindung ist dann gesperrt).
    record = await db_session.get(ExtensionRecord, ext_id)
    record.settings = {"connections": [{**stored, "tls_fingerprint_sha256": "kaputt"}]}
    await db_session.flush()
    listed = (await client.get(base, headers=_h(token))).json()
    assert (listed[0]["tls_fingerprint_sha256"], listed[0]["tls_fingerprint_invalid"]) == (None, True)


@pytest.mark.asyncio
async def test_a_trusted_certificate_drops_an_old_fingerprint_when_connecting_again(
    client, db_session, test_settings, trusted_ca, fake_pve,  # noqa: F811
):
    """Zeigt der Server jetzt ein anerkanntes Zertifikat (etwa Let's Encrypt), faellt ein alter Fingerabdruck beim
    neuen Verbinden weg: die normale Pruefung reicht, und der alte Wert wuerde die Verbindung sperren."""
    existing = {
        "name": "pve1", "base_url": fake_pve["base_url"], "token_id": "x@pve!y", "tls_insecure_skip_verify": True,
        "tls_fingerprint_sha256": PIN_A, "enabled": True,
    }
    token = await _owner_with_proxmox(client, db_session, test_settings, [existing])
    r = await _connect(client, token, fake_pve["base_url"])
    assert r.status_code == 200, r.text
    (conn,) = (await _settings(db_session))["connections"]
    assert "tls_fingerprint_sha256" not in conn and conn["tls_insecure_skip_verify"] is False
    assert (await _health("proxmox")).healthy is True


# ---------------------------------------------------------------------------
# Verbindungstest des Kerns
# ---------------------------------------------------------------------------


def test_the_connection_test_names_the_fingerprint_when_the_extension_has_one():
    schema = json.loads((REPO / "extensions" / "proxmox" / "settings.schema.json").read_text(encoding="utf-8"))
    pin = extension_test.tls_pin_title(schema)
    assert pin == "Fingerabdruck des Zertifikats (SHA-256)"
    untrusted = extension_test.translate(
        "SSLCertVerificationError", tls_hint=extension_test.tls_option_title(schema), tls_pin=pin,
    ).message
    assert "Fingerabdruck" in untrusted and "Selbstsigniertes Zertifikat erlauben" not in untrusted
    changed = extension_test.translate(
        "pve1: FEHLER: GET /version -> Der Server zeigt ein anderes Zertifikat als das gemerkte. Vergleiche …",
        tls_hint="Selbstsigniertes Zertifikat erlauben", tls_pin=pin,
    )
    assert changed.kind == "tls"
    assert "neuen Fingerabdruck" in changed.message and "einschalten" not in changed.message
    # Ohne Feld fuer den Fingerabdruck bleibt der alte Hinweis.
    assert "„Selbstsigniertes Zertifikat erlauben“ einschalten" in extension_test.translate(
        "SSLCertVerificationError", tls_hint="Selbstsigniertes Zertifikat erlauben",
    ).message
