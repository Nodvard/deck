"""Verbindungstest: feste Saetze der Erweiterungen ohne Vorspann, und nie fremder Text aus einer Ursachenkette.

Direkt gegen `run_connection_test` und `translate` (ohne Datenbank und Anmeldung). "angreifer" steht in jeder
fremden Antwort und darf in keiner Meldung vorkommen."""

from __future__ import annotations

import ssl
from types import SimpleNamespace

import httpx
import pytest
from nodvard_deck.services import extension_test
from nodvard_sdk import HealthReport
from raw_http_helpers import BROKEN_ANSWERS, REDIRECT_ANSWERS, raw_http_server

SETTINGS = {"connections": [{"name": "pve1", "base_url": "https://pve.test:8006"}]}
REDIRECT_NO_CODE = (
    "Der Server hat die Verbindung auf eine andere Adresse umgeleitet. "
    "Solchen Umleitungen folgt Nodvard Deck aus Sicherheitsgründen nicht. "
    "Prüfe die eingetragene Adresse des Servers."
)
BROKEN = (
    "Der Server hat die Verbindung abgebrochen oder eine fehlerhafte Antwort geschickt. "
    "Prüfe die Adresse in den Einstellungen (http:// oder https://, Port)."
)


def _loaded(health):
    return SimpleNamespace(instance=SimpleNamespace(health=health), ctx=None)


async def _run(health, *, secrets=None) -> extension_test.CheckOutcome:
    return await extension_test.run_connection_test(
        _loaded(health), settings=SETTINGS, schema=None, secrets=secrets or [], channel=None
    )


def _reports(report: HealthReport):
    async def health(ctx):
        return report

    return health


def _raises(exc: BaseException):
    async def health(ctx):
        raise exc

    return health


# -- feste Saetze ohne Vorspann ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind", "message"),
    [
        # Weiterleitung ohne Statuscode (kaputter Location-Header): Satz des Kerns, nicht "Technische Meldung".
        (
            "Pi-hole leitet auf eine andere Adresse um. Solchen Umleitungen folgt Nodvard Deck aus Sicherheitsgründen nicht. "
            "Meist ist http:// statt https:// eingetragen (oder umgekehrt), oder ein Proxy davor leitet um. "
            "Trage in den Einstellungen die endgültige Adresse ein.",
            "redirect",
            REDIRECT_NO_CODE,
        ),
        ("Nextcloud leitet auf eine andere Adresse um (HTTP 302). Trage die Adresse ein.", "redirect", None),
        (
            "Nextcloud hat die Verbindung abgebrochen oder eine fehlerhafte Antwort geschickt. Prüfe die Adresse in den Einstellungen.",
            "invalid_answer",
            BROKEN,
        ),
        (
            "Der ntfy-Server hat die Verbindung abgebrochen oder eine fehlerhafte Antwort geschickt. Prüfe die Adresse.",
            "invalid_answer",
            BROKEN,
        ),
        ("Die Anfrage an ntfy ließ sich nicht senden. Prüfe die Adresse und das Zugriffstoken.", "invalid_request", None),
        ("Pi-hole: Die eingetragene Adresse ist ungültig. Bitte in den Einstellungen prüfen.", "invalid_address", None),
        ("Die Adresse der Nextcloud ist ungültig. Prüfe sie in den Einstellungen.", "invalid_address", None),
        ("Die Adresse des ntfy-Servers ist ungültig. Prüfe sie in den Einstellungen.", "invalid_address", None),
        # Sätze der Proxmox-Clients: Weiterleitung, kaputte Antwort, Adresse, Zertifikat, Zeit, kein Weg
        ("GET /version -> Die Anfrage an Proxmox ließ sich nicht senden. Prüfe die Einstellungen.", "invalid_request", None),
        (
            "GET /version -> Proxmox ist nicht erreichbar (Verbindung abgelehnt oder kein Weg dorthin). "
            "Läuft der Server, und stimmen Adresse und Port?",
            "refused",
            None,
        ),
        ("GET /version -> Proxmox antwortet nicht (Zeitüberschreitung). Prüfe Adresse und Port.", "timeout", None),
        ("GET /version -> Das Zertifikat von Proxmox wird nicht akzeptiert. Bei einem selbstsignierten …", "tls", None),
        ("GET /version -> Die Adresse ließ sich nicht auflösen (Namensauflösung fehlgeschlagen). Prüfe …", "dns", None),
    ],
)
def test_fixed_sentences_of_extensions_have_no_technical_prefix(text, kind, message):
    t = extension_test.translate(text)
    assert t.kind == kind
    assert "Technische Meldung" not in t.message
    if message is not None:
        assert t.message == message


FORBIDDEN = "Zugriff verweigert – dem Konto oder Token fehlt ein Recht auf dem Server. Rechte und Zugangsdaten prüfen."


@pytest.mark.parametrize(
    "text",
    [
        "GET /cluster/backup -> HTTP 403: Dem Token fehlt ein Recht für diese Abfrage. "
        "Grund laut Proxmox: Permission check failed (/, Sys.Audit).",
        "PROPFIND / -> HTTP 403: Forbidden",
    ],
)
def test_a_missing_right_is_not_reported_as_rejected_credentials(text):
    """Bei 403 stimmen die Zugangsdaten meist, es fehlt ein Recht (z. B. die ACL-Zeile des Proxmox-Tokens)."""
    t = extension_test.translate(text)
    assert (t.kind, t.message) == ("forbidden", FORBIDDEN)
    assert extension_test.translate(text.replace("403", "401")).kind == "auth"


@pytest.mark.asyncio
async def test_the_connection_test_names_a_missing_right_for_a_403_of_a_connection():
    report = HealthReport(
        healthy=False,
        message="",
        details={
            "pve1": {
                "healthy": False,
                "error": "GET /cluster/backup -> HTTP 403: Dem Token fehlt ein Recht für diese Abfrage. "
                "Grund laut Proxmox: Permission check failed (/, Sys.Audit).",
            }
        },
    )
    outcome = await _run(_reports(report))
    assert outcome.details[0].message == FORBIDDEN
    assert "Zugangsdaten abgelehnt" not in outcome.message


def test_the_fallback_sentence_of_an_extension_comes_without_prefix_and_alone():
    sentence = "Die Verbindung zu Proxmox ist fehlgeschlagen (UnsupportedProtocol)."
    t = extension_test.translate(f"GET /version -> {sentence}")
    assert t.message == sentence and "Technische Meldung" not in t.message
    # Nur ein schlichter Typname in der Klammer, sonst gilt der Satz nicht als bekannt.
    t = extension_test.translate("Die Verbindung zu Proxmox ist fehlgeschlagen (siehe https://angreifer.example).")
    assert t.kind == "unknown"


def test_the_matched_sentence_is_all_that_comes_back_for_an_unexpected_format():
    """Nur der getroffene Satz, nie der Rest des Textes (der fremde Zeilen enthalten koennte)."""
    sentence = "Die Antwort von Proxmox hat nicht das erwartete Format (HTTP 200). Stimmt die Adresse?"
    t = extension_test.translate(f"pve1: FEHLER: {sentence} Dazu: https://angreifer.example/login?x=1")
    assert t.kind == "invalid_answer" and t.message == sentence
    assert "angreifer" not in t.message
    # Der Name des Dienstes ist kurz und schlicht, sonst gilt der Satz nicht als bekannt.
    t = extension_test.translate("Die Antwort von <script>angreifer</script> hat nicht das erwartete Format. Stimmt die Adresse?")
    assert t.kind == "unknown"
    t = extension_test.translate(f"Die Antwort von {'x' * 60} hat nicht das erwartete Format. Stimmt die Adresse?")
    assert t.kind == "unknown"


@pytest.mark.asyncio
async def test_connection_test_shows_fixed_sentences_per_connection_without_prefix():
    report = HealthReport(
        healthy=False,
        message="",
        details={
            "pve1": {"healthy": False, "error": "GET /version -> " + BROKEN.replace("Der Server", "Proxmox")},
            "pve2": {"healthy": False, "error": "Die Antwort von Proxmox hat nicht das erwartete Format (HTTP 200). Stimmt die Adresse?"},
            "pve3": {"healthy": True},
        },
    )
    outcome = await _run(_reports(report))
    by_name = {d.name: d.message for d in outcome.details}
    assert by_name["pve1"] == BROKEN
    assert by_name["pve2"] == "Die Antwort von Proxmox hat nicht das erwartete Format (HTTP 200). Stimmt die Adresse?"
    assert "Technische Meldung" not in outcome.message and all("Technische Meldung" not in m for m in by_name.values())


# -- der Status haengt nicht am Ende des abgeschnittenen Textes ----------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [301, 302, 307, 399])
async def test_a_redirect_is_recognised_even_when_the_text_is_cut_off(code):
    """`scrub()` schneidet bei 300 Zeichen ab; "| HTTP 302" steht ganz hinten. Frueher fiel die Meldung dann in
    "Technische Meldung: ..." zurueck und nannte das Weiterleitungsziel."""
    location = "https://angreifer.example/" + "a" * 400
    request = httpx.Request("GET", "https://pve.test:8006/api2/json/version")
    response = httpx.Response(code, headers={"Location": location}, request=request)
    exc = httpx.HTTPStatusError(
        f"Redirect response '{code} Found' for url '{request.url}'\nRedirect location: '{location}'", request=request, response=response
    )
    outcome = await _run(_raises(exc))
    assert outcome.kind == "redirect"
    assert outcome.message == extension_test.redirect_message(code)
    assert "angreifer" not in outcome.message and "Technische Meldung" not in outcome.message


@pytest.mark.asyncio
async def test_the_status_of_an_error_with_a_long_text_still_decides():
    class Failing(Exception):
        def __init__(self) -> None:
            super().__init__("x" * 500)
            self.response = SimpleNamespace(status_code=401)

    outcome = await _run(_raises(Failing()))
    assert outcome.kind == "auth"


def test_describe_exception_keeps_only_the_type_of_texts_from_foreign_answers():
    request = httpx.Request("GET", "https://pve.test:8006/")
    response = httpx.Response(302, headers={"Location": "https://angreifer.example/"}, request=request)
    cases = [
        httpx.HTTPStatusError("Redirect location: 'https://angreifer.example/'", request=request, response=response),
        httpx.RemoteProtocolError("illegal header line: bytearray(b'Location: https://angreifer.example/')"),
        httpx.LocalProtocolError("Illegal header value b'PVEAPIToken=a@pve!t=geheim'"),
        httpx.InvalidURL("Invalid IDNA hostname: 'angreifer'"),
        httpx.DecodingError("angreifer"),
    ]
    for exc in cases:
        text = extension_test.describe_exception(exc)
        assert "angreifer" not in text and "geheim" not in text and "bytearray" not in text, text
        assert type(exc).__name__ in text
    assert "HTTP 302" in extension_test.describe_exception(cases[0])


# -- die Ursachenkette bringt keinen fremden Text mit ---------------------------------------------


def _wrapped(inner: BaseException, *, as_cause: bool) -> BaseException:
    """Eine Meldung der Erweiterung ("fester Satz") ueber einer Ausnahme mit fremdem Text."""
    try:
        try:
            raise inner
        except BaseException as caught:
            if as_cause:
                raise RuntimeError("GET /version -> Verbindung nicht moeglich") from caught
            raise RuntimeError("GET /version -> Verbindung nicht moeglich")
    except RuntimeError as outer:
        return outer


@pytest.mark.asyncio
@pytest.mark.parametrize("as_cause", [True, False])
@pytest.mark.parametrize(
    "inner",
    [
        httpx.RemoteProtocolError("illegal header line: bytearray(b'Location: https://angreifer.example/')"),
        httpx.RemoteProtocolError("Server disconnected angreifer"),
        httpx.LocalProtocolError("Illegal header value b'angreifer'"),
        httpx.InvalidURL("Invalid IDNA hostname: 'angreifer'"),
        httpx.DecodingError("angreifer"),
    ],
)
async def test_text_of_a_foreign_answer_in_the_cause_chain_never_reaches_the_test(inner, as_cause):
    outcome = await _run(_raises(_wrapped(inner, as_cause=as_cause)))
    assert outcome.ok is False
    assert "angreifer" not in outcome.message and "bytearray" not in outcome.message and "Illegal header" not in outcome.message
    # Die Meldung ist ein fester Satz, kein "Technische Meldung: ..." mit abgeschriebener Kette.
    assert "Technische Meldung" not in outcome.message


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [*REDIRECT_ANSWERS, *BROKEN_ANSWERS])
async def test_a_broken_answer_of_the_real_parser_gives_a_fixed_sentence_in_the_test(name):
    """Die echte Ausnahme von h11/httpx (nicht nachgebaut), ungefangen aus `health()` heraus."""
    answers = {**REDIRECT_ANSWERS, **BROKEN_ANSWERS}
    async with raw_http_server(answers[name]) as base:

        async def health(ctx):
            async with httpx.AsyncClient(trust_env=False) as client:
                await client.get(f"{base}/api2/json/version")
            return HealthReport(healthy=True)

        outcome = await _run(health)
    assert outcome.ok is False
    assert "angreifer" not in outcome.message and "bytearray" not in outcome.message
    assert "Technische Meldung" not in outcome.message
    expected = REDIRECT_NO_CODE if name in REDIRECT_ANSWERS else BROKEN
    # Ein Status aus der Ausnahme (hier ein 302 mit kaputter Kopfzeile) hat bei h11 keine Antwort mehr: es bleibt
    # beim Satz zur Art der Ausnahme.
    assert outcome.message == expected, outcome.message


# Kaputte Zeilen, die einen festen Satz oder einen Status vortaeuschen. h11 zitiert sie, und unter dem
# `httpx.RemoteProtocolError` haengen die Fehler von httpcore und h11 mit derselben Zeile.
INJECTED_ANSWERS = {
    "format_header": (
        b"HTTP/1.1 200 OK\r\nDie Antwort von angreifer.example hat nicht das erwartete Format. Stimmt die Adresse?\r\n"
        b"Content-Length: 0\r\n\r\n"
    ),
    "format_status": (
        b"HTTP/1.1 200 Die Antwort von angreifer.example hat nicht das erwartete Format. Stimmt die Adresse?\x00\r\n"
        b"Content-Length: 0\r\n\r\n"
    ),
    "status_401": b"HTTP/1.1 200 OK\r\nX HTTP 401 angreifer\r\nContent-Length: 0\r\n\r\n",
    "redirect_word": b"HTTP/1.1 200 OK\r\nX leitet auf eine andere Adresse um angreifer\r\nContent-Length: 0\r\n\r\n",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", INJECTED_ANSWERS)
async def test_text_of_httpcore_and_h11_in_the_chain_neither_shows_nor_decides(name):
    """Die ganze Kette eines echten Parserfehlers: kein Glied gibt seinen Text an die Erkennung weiter, auch
    nicht die Fehler von httpcore und h11 unter dem von httpx. Sonst kaeme ein vorgetaeuschter Satz als
    Meldung an, oder eine Zeile mit "HTTP 401" machte aus der kaputten Antwort "Zugangsdaten abgelehnt"."""
    async with raw_http_server(INJECTED_ANSWERS[name]) as base:

        async def health(ctx):
            async with httpx.AsyncClient(trust_env=False) as client:
                await client.get(f"{base}/api2/json/version")
            return HealthReport(healthy=True)

        with pytest.raises(httpx.RemoteProtocolError) as caught:
            await health(None)
        described = extension_test.describe_exception(caught.value)
        outcome = await _run(health)
    assert "angreifer" not in described and "bytearray" not in described, described
    assert "HTTP 401" not in described
    assert (outcome.kind, outcome.message) == ("invalid_answer", BROKEN)


@pytest.mark.asyncio
async def test_a_readable_exception_keeps_its_sentence_and_not_its_chain():
    class Fertig(Exception):
        readable = True

    try:
        try:
            raise ValueError("angreifer zitiert hier")
        except ValueError as inner:
            raise Fertig("Der Dienst ist gerade im Wartungsmodus.") from inner
    except Fertig as exc:
        error = exc
    outcome = await _run(_raises(error))
    assert outcome.message == "Der Dienst ist gerade im Wartungsmodus."
    assert "angreifer" not in outcome.message and "Technische Meldung" not in outcome.message


@pytest.mark.asyncio
async def test_other_chains_still_get_translated_from_their_causes():
    """Die Ursachenkette bleibt fuer alles, was keinen fremden Text zitiert (hier ein TLS-Fehler)."""
    try:
        try:
            raise ssl.SSLCertVerificationError("certificate verify failed: self-signed certificate")
        except ssl.SSLError as inner:
            raise httpx.ConnectError("") from inner
    except httpx.ConnectError as exc:
        error = exc
    outcome = await _run(_raises(error))
    assert outcome.kind == "tls"


@pytest.mark.asyncio
async def test_the_message_of_a_failed_test_never_contains_secrets():
    outcome = await _run(_raises(RuntimeError("Anmeldung mit hunter2-sehr-geheim gescheitert")), secrets=["hunter2-sehr-geheim"])
    assert "hunter2" not in outcome.message
