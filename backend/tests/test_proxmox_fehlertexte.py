"""Fehlertexte der Clients von Proxmox und Backups: feste Saetze, nie Text aus der Antwort oder der Ausnahme.

Beide Erweiterungen haben ihre eigene kleine Hilfe (`transport.py`) und werden hier mit denselben Faellen
geprueft. Kaputte Antworten laufen ueber einen echten TCP-Server und die echte `ctx.http`-Schicht
(`HttpHandle`): nur so entstehen die Fehlertexte von h11, httpx und `websockets`, die fremde Zeilen zitieren.
"angreifer" steht in jeder fremden Antwort und darf in keiner Meldung und keiner Ursachenkette vorkommen."""

from __future__ import annotations

import socket
import ssl
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from nodvard_sdk.errors import PermissionDenied
from raw_http_helpers import (
    BROKEN_ANSWERS,
    REDIRECT_ANSWERS,
    WEBSOCKET_ANSWERS,
    raw_http_server,
    whole_chain,
)

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
KINDS = ["proxmox", "backups"]
GEHEIM = "geheim-1234"

REDIRECT_SENTENCE = (
    "Der Server hat die Verbindung auf eine andere Adresse umgeleitet. "
    "Solchen Umleitungen folgt Nodvard Deck aus Sicherheitsgründen nicht. "
    "Prüfe die eingetragene Adresse des Servers."
)
BROKEN_SENTENCE = (
    "Proxmox hat die Verbindung abgebrochen oder eine fehlerhafte Antwort geschickt. "
    "Prüfe die Adresse in den Einstellungen (https:// und Port 8006)."
)
NOT_SENT_SENTENCE = "Die Anfrage an Proxmox ließ sich nicht senden. Prüfe die Einstellungen (Adresse, Token-ID, Geheimnis)."
NOT_SENT_SHORT = "Die Anfrage an Proxmox ließ sich nicht senden. Prüfe die Einstellungen."
BAD_ADDRESS_SENTENCE = "Die eingetragene Adresse ist ungültig. Bitte in den Einstellungen prüfen."
TLS_SENTENCE = (
    "Das Zertifikat von Proxmox wird nicht akzeptiert. Bei einem selbstsignierten Zertifikat in den "
    "Einstellungen „Selbstsigniertes Zertifikat erlauben“ einschalten."
)


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


def _classes(kind: str):
    """Connector und Fehlerklasse aus demselben Import (andere Tests raeumen `sys.modules` auf)."""
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / kind / "src"))
    if kind == "proxmox":
        from nodvard_deck_ext_proxmox.connector import ProxmoxApiError, ProxmoxConnector

        return ProxmoxConnector, ProxmoxApiError
    from nodvard_deck_ext_backups.connector import (
        ProxmoxBackupApiError,
        ProxmoxBackupConnector,
    )

    return ProxmoxBackupConnector, ProxmoxBackupApiError


def _connector(kind: str, http, base_url: str = "https://pve.test:8006"):
    connector_cls, error_cls = _classes(kind)
    return connector_cls(SimpleNamespace(http=http), base_url=base_url, token_id="a@pve!t", token_secret=GEHEIM), error_cls


def _real_http():
    """Die echte `ctx.http`-Schicht mit allen Berechtigungen (kein Proxy, keine Weiterleitungen)."""
    from nodvard_deck.ext.context import HttpHandle, _PermissionChecker

    granted = ["net.outbound", "net.outbound.insecure_tls"]
    return HttpHandle(_PermissionChecker("proxmox", granted), granted)


class _Raises:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def request(self, method, url, **kwargs):
        raise self._exc


class _Answers:
    def __init__(self, status_code: int, content: bytes, headers: dict | None = None, extensions: dict | None = None) -> None:
        self._args = (status_code, content, headers, extensions)

    async def request(self, method, url, **kwargs):
        status_code, content, headers, extensions = self._args
        return httpx.Response(
            status_code, content=content, headers=headers, extensions=extensions, request=httpx.Request(method, url)
        )


async def _error_of(connector, method: str = "GET", path: str = "/version") -> BaseException:
    with pytest.raises(Exception) as caught:
        await connector._request(method, path)
    return caught.value


# -- kaputte Antworten ueber den echten Parser ---------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("answer", [*REDIRECT_ANSWERS, *BROKEN_ANSWERS])
async def test_broken_answers_of_the_real_parser_never_reach_a_text(kind, answer):
    """h11 zitiert kaputte Zeilen (auch einen `Location`-Header), httpx den Namen einer Weiterleitung. Die
    Meldung ist ein fester Satz, und an ihr haengt keine Ursache, die den fremden Text weitertruege."""
    answers = {**REDIRECT_ANSWERS, **BROKEN_ANSWERS}
    http = _real_http()
    try:
        async with raw_http_server(answers[answer]) as base:
            connector, error_cls = _connector(kind, http, base)
            exc = await _error_of(connector)
    finally:
        await http.aclose()
    assert isinstance(exc, error_cls)
    expected = REDIRECT_SENTENCE if answer in REDIRECT_ANSWERS else BROKEN_SENTENCE
    assert str(exc) == f"GET /version -> {expected}"
    chain = whole_chain(exc)
    assert "angreifer" not in chain.lower() and "bytearray" not in chain and "disconnected" not in chain
    assert exc.__cause__ is None and exc.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("raised", [httpx.InvalidURL("Invalid IDNA hostname: 'angreifer'"), ValueError("angreifer"), UnicodeError("angreifer")])
async def test_an_invalid_own_address_gets_its_own_sentence_without_the_address(kind, raised):
    """Ein kaputter Name in der eigenen Adresse (nicht in der Weiterleitung) ist ein Fehler der Einstellung."""
    connector, _ = _connector(kind, _Raises(raised), "http://xn--angreifer-.example")
    exc = await _error_of(connector)
    assert str(exc) == f"GET /version -> {BAD_ADDRESS_SENTENCE}"
    assert "angreifer" not in str(exc)


# -- Ausnahmen der HTTP-Schicht -----------------------------------------------------------------


def _ssl_error() -> BaseException:
    inner = ssl.SSLCertVerificationError("certificate verify failed: self-signed certificate angreifer")
    outer = httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed angreifer")
    outer.__cause__ = inner
    return outer


def _dns_error() -> BaseException:
    outer = httpx.ConnectError("[Errno -2] Name or service not known")
    outer.__cause__ = socket.gaierror(-2, "Name or service not known")
    return outer


TRANSPORT_CASES = [
    (httpx.LocalProtocolError(f"Illegal header value b'PVEAPIToken=a@pve!t={GEHEIM}'"), NOT_SENT_SENTENCE, True),
    (httpx.RemoteProtocolError("illegal header line: bytearray(b'Location: https://angreifer.example/')"), REDIRECT_SENTENCE, True),
    (httpx.RemoteProtocolError("Server disconnected without sending a response. angreifer"), BROKEN_SENTENCE, True),
    (httpx.InvalidURL("Invalid port: 'angreifer'"), NOT_SENT_SHORT, True),
    (ValueError("Location angreifer.example ist kaputt"), REDIRECT_SENTENCE, False),
    (_ssl_error(), TLS_SENTENCE, False),
    # So reicht `websockets` einen Zertifikatsfehler durch: ohne Huelle von httpx. Er ist zugleich ein
    # `ValueError` und darf nicht als "Anfrage ließ sich nicht senden" gelten.
    (ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: angreifer"), TLS_SENTENCE, False),
    # Kaputt gepackter Koerper (Content-Encoding): eine fehlerhafte Antwort, kein allgemeiner Fehlschlag.
    (httpx.DecodingError("Error -3 while decompressing data: angreifer"), BROKEN_SENTENCE, True),
    (
        _dns_error(),
        "Die Adresse ließ sich nicht auflösen (Namensauflösung fehlgeschlagen). Prüfe die Schreibweise der Adresse.",
        False,
    ),
    (httpx.ConnectTimeout(""), "Proxmox antwortet nicht (Zeitüberschreitung). Prüfe Adresse und Port.", False),
    (httpx.ReadTimeout("angreifer"), "Proxmox antwortet nicht (Zeitüberschreitung). Prüfe Adresse und Port.", False),
    (
        httpx.ConnectError("All connection attempts failed"),
        "Proxmox ist nicht erreichbar (Verbindung abgelehnt oder kein Weg dorthin). Läuft der Server, und stimmen Adresse und Port?",
        False,
    ),
    (RuntimeError("angreifer"), "Die Verbindung zu Proxmox ist fehlgeschlagen (RuntimeError).", False),
    (
        PermissionDenied("proxmox", "net.outbound.insecure_tls"),
        "Extension 'proxmox' hat die Permission 'net.outbound.insecure_tls' nicht. "
        "Ins Manifest aufnehmen und vom Admin bestätigen lassen.",
        False,
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(("raised", "sentence", "carries_foreign_text"), TRANSPORT_CASES)
async def test_exceptions_of_the_http_layer_give_a_fixed_sentence(kind, raised, sentence, carries_foreign_text):
    """Der Text der Ausnahme waehlt nur den Satz aus und steht nie selbst in der Meldung. Eine Ausnahme, deren
    Text fremde Zeilen oder die eigenen Zugangsdaten zitieren kann, bleibt auch nicht als Ursache haengen."""
    connector, error_cls = _connector(kind, _Raises(raised))
    exc = await _error_of(connector)
    assert isinstance(exc, error_cls)
    assert str(exc) == f"GET /version -> {sentence}"
    assert "angreifer" not in str(exc) and GEHEIM not in str(exc)
    if carries_foreign_text:
        chain = whole_chain(exc)
        assert "angreifer" not in chain.lower() and GEHEIM not in chain
        assert exc.__cause__ is None and exc.__context__ is None
    else:
        assert exc.__context__ is None  # nie aus dem `except`-Block heraus geworfen


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_an_empty_timeout_text_still_gives_a_sentence(kind):
    """httpx' Timeouts haben einen leeren Text: frueher stand dort nur "GET /x -> "."""
    connector, _ = _connector(kind, _Raises(httpx.ConnectTimeout("")))
    assert "Zeitüberschreitung" in str(await _error_of(connector, "POST", "/nodes/pve1/qemu/100/vncproxy"))


# -- Antworten mit Fehlerstatus -----------------------------------------------------------------


STATUS_SENTENCES = {
    400: "Proxmox hat die Angaben nicht angenommen.",
    401: "Proxmox hat den Zugang abgelehnt. Prüfe Token-ID und Geheimnis in den Einstellungen.",
    403: "Dem Token fehlt ein Recht für diese Abfrage.",
    404: "Das gibt es dort nicht (mehr), oder die Adresse gehört nicht zu Proxmox.",
    409: "Proxmox hat die Anfrage abgelehnt.",
    500: "Proxmox meldet einen Fehler.",
    502: "Proxmox oder ein Proxy davor meldet einen Fehler.",
    595: "Proxmox oder ein Proxy davor meldet einen Fehler.",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("status_code", STATUS_SENTENCES)
@pytest.mark.parametrize(
    "body",
    [
        b"<html><body>angreifer: neue Adresse https://angreifer.example</body></html>",
        b"angreifer sagt: Passwort eingeben",
        b'["angreifer"]',
        b"",
    ],
)
async def test_error_status_gives_a_fixed_sentence_never_the_body(kind, status_code, body):
    connector, _ = _connector(kind, _Answers(status_code, body))
    exc = await _error_of(connector)
    assert str(exc) == f"GET /version -> HTTP {status_code}: {STATUS_SENTENCES[status_code]}"
    assert "angreifer" not in whole_chain(exc)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("status_code", [301, 302, 308, 400, 401, 403, 404, 409, 500, 503])
async def test_the_api_error_carries_the_http_status(kind, status_code):
    """Beide Fehlerklassen tragen den Status der Antwort (`status_code`), damit ein Aufrufer ihn auswerten kann,
    ohne den Satz zu zerlegen."""
    connector, error_cls = _connector(kind, _Answers(status_code, b""))
    exc = await _error_of(connector)
    assert isinstance(exc, error_cls)
    assert exc.status_code == status_code


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_the_api_error_has_no_status_without_a_response(kind):
    """Bei einem Verbindungsfehler gibt es keine Antwort: `status_code` ist `None`, und der Text bleibt allein."""
    connector, error_cls = _connector(kind, _Raises(httpx.ConnectError("")))
    exc = await _error_of(connector)
    assert isinstance(exc, error_cls)
    assert exc.status_code is None
    assert error_cls("nur Text").status_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_error_status_with_a_deeply_nested_body_is_still_a_fixed_sentence(kind):
    connector, _ = _connector(kind, _Answers(500, b"[" * 200_000 + b"]" * 200_000))
    assert str(await _error_of(connector)) == "GET /version -> HTTP 500: Proxmox meldet einen Fehler."


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("depth", [20_000, 30_000])
async def test_error_status_with_a_nested_body_below_the_size_limit_is_a_fixed_sentence(kind, depth):
    """Der Test davor liegt mit 400 KB ueber der Grenze von 64 KiB, ab der der Koerper gar nicht erst gelesen
    wird: der Fang von `RecursionError` in `json_body()` wird dort nie erreicht. Dieser Koerper ist kleiner als
    die Grenze und so tief verschachtelt, dass `json.loads` mit `RecursionError` scheitert (wird unten geprueft,
    sonst prueft der Test den Weg nicht). Es bleibt bei dem festen Satz, ohne Grund und ohne Absturz."""
    import json

    body = b"[" * depth + b"]" * depth
    assert len(body) <= 65536
    with pytest.raises(RecursionError):
        json.loads(body)  # sonst die Tiefe erhoehen

    connector, _ = _connector(kind, _Answers(500, body))
    assert str(await _error_of(connector)) == "GET /version -> HTTP 500: Proxmox meldet einen Fehler."


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_error_status_with_a_huge_json_body_gives_no_reason(kind):
    body = b'{"message": "' + b"x" * 100_000 + b'"}'
    connector, _ = _connector(kind, _Answers(500, body))
    assert str(await _error_of(connector)) == "GET /version -> HTTP 500: Proxmox meldet einen Fehler."


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    ("body", "reason"),
    [
        # Der Grund, den Proxmox selbst nennt, bleibt: er sagt, welches Recht fehlt.
        (b'{"data": null, "message": "Permission check failed (/vms/100, VM.Console)"}', "Permission check failed (/vms/100, VM.Console)"),
        (
            b'{"data": null, "message": "Parameter verification failed.\\n", "errors": {"vmid": "value does not look like a valid VM ID"}}',
            "Parameter verification failed. vmid: value does not look like a valid VM ID",
        ),
        # Steuerzeichen und Zeilenumbrueche fliegen raus.
        (b'{"message": "erste Zeile\\n\\u001b[31mzweite\\u0000 Zeile\\r\\n"}', "erste Zeile [31mzweite Zeile"),
        # Ein Rechnername oder `IP:Port` ohne Schema bleibt mit Absicht (echte Gruende im Cluster nennen ihn);
        # nur eine Adresse mit Schema faellt weg (siehe unten).
        (b'{"data": null, "message": "Can\'t connect to 192.168.2.10:8006"}', "Can't connect to 192.168.2.10:8006"),
    ],
)
async def test_the_reason_from_the_json_of_proxmox_is_kept_cleaned(kind, body, reason):
    connector, _ = _connector(kind, _Answers(403, body))
    exc = await _error_of(connector)
    assert str(exc) == f"GET /version -> HTTP 403: {STATUS_SENTENCES[403]} Grund laut Proxmox: {reason}."


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_the_reason_may_come_from_the_status_line(kind):
    """Proxmox nennt den Grund auch in der Statuszeile (`HTTP/1.1 403 Permission check failed (...)`)."""
    connector, _ = _connector(
        kind, _Answers(403, b'{"data": null}', extensions={"reason_phrase": b"Permission check failed (/, Sys.Audit)"})
    )
    exc = await _error_of(connector)
    assert str(exc).endswith("Grund laut Proxmox: Permission check failed (/, Sys.Audit).")
    # Der Standardtext des Status sagt nichts und bleibt weg.
    connector, _ = _connector(kind, _Answers(403, b'{"data": null}'))
    assert "Grund laut" not in str(await _error_of(connector))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "body",
    [
        b'{"message": "<html>angreifer</html>"}',
        b'{"message": "Neue Adresse: https://angreifer.example/login"}',
        b'{"message": {"angreifer": 1}}',
        b'{"message": "   \\n  "}',
        b'{"errors": {"vmid": {"angreifer": 1}}}',
        b'{"errors": ["angreifer"]}',
        b'{"errors": {"vmid": "<b>angreifer</b>"}}',
    ],
)
async def test_a_reason_that_looks_like_html_an_address_or_is_no_text_is_dropped(kind, body):
    connector, _ = _connector(kind, _Answers(500, body))
    exc = await _error_of(connector)
    assert str(exc) == "GET /version -> HTTP 500: Proxmox meldet einen Fehler."


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_the_reason_is_shortened(kind):
    connector, _ = _connector(kind, _Answers(500, b'{"message": "' + b"lang " * 200 + b'"}'))
    text = str(await _error_of(connector))
    reason = text.split("Grund laut Proxmox: ", 1)[1]
    assert len(reason) <= 202 and "…" in reason


# -- Erfolg mit unbrauchbarem Koerper -------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "content",
    [b"", b"<html>angreifer</html>", b"[1, 2]", b"null", b'"angreifer"', b"[" * 200_000 + b"]" * 200_000, b'{"data": ' + b"[" * 200_000],
)
async def test_a_2xx_answer_that_is_no_json_object_gives_the_format_sentence(kind, content):
    """Auch ein absichtlich tief verschachteltes JSON (`RecursionError`) ist nur "falsches Format"."""
    connector, _ = _connector(kind, _Answers(200, content))
    exc = await _error_of(connector)
    assert str(exc) == "Die Antwort von Proxmox hat nicht das erwartete Format (HTTP 200). Stimmt die Adresse?"
    assert exc.__cause__ is None and exc.__context__ is None
    assert "angreifer" not in whole_chain(exc)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("status_code", [300, 301, 304, 399])
async def test_a_3xx_answer_names_no_address_and_has_no_cause(kind, status_code):
    connector, _ = _connector(
        kind, _Answers(status_code, b"", {"Location": "https://angreifer.example/neu"})
    )
    exc = await _error_of(connector)
    assert str(exc).startswith(f"Der Server hat die Verbindung auf eine andere Adresse umgeleitet (HTTP {status_code}).")
    assert "angreifer" not in whole_chain(exc)


# -- Konsole (nur Proxmox) ----------------------------------------------------------------------


class _WebsocketHttp:
    """`request()` beantwortet den vncproxy-Aufruf, `websocket()` ist die echte `ctx.http`-Schicht."""

    def __init__(self, real) -> None:
        self._real = real
        self.websocket = real.websocket

    async def request(self, method, url, **kwargs):
        body = b'{"data": {"port": "5900", "ticket": "PVEVNC:abc+/=geheim-ticket", "password": "pw"}}'
        return httpx.Response(200, content=body, request=httpx.Request(method, "http://pve.test/"))


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", WEBSOCKET_ANSWERS)
async def test_console_handshake_answers_give_a_fixed_sentence(answer):
    """`websockets` zitiert Kopfzeilen und Statuszeilen des Servers, und bei einer kaputten Adresse die ganze
    Adresse samt `vncticket`. Nichts davon steht in der Meldung (die in Oberflaeche und Protokoll geht)."""
    real = _real_http()
    try:
        async with raw_http_server(WEBSOCKET_ANSWERS[answer]) as base:
            connector, error_cls = _connector("proxmox", _WebsocketHttp(real), base)
            with pytest.raises(error_cls) as caught:
                await connector.open_vnc("pve1", "qemu", "100")
    finally:
        await real.aclose()
    exc = caught.value
    text = str(exc)
    assert text.startswith("vncwebsocket -> ")
    assert "angreifer" not in whole_chain(exc).lower() and "geheim-ticket" not in whole_chain(exc)
    assert exc.__cause__ is None and exc.__context__ is None
    if answer == "redirect":
        assert text == f"vncwebsocket -> {REDIRECT_SENTENCE.replace('umgeleitet.', 'umgeleitet (HTTP 302).')}"
    elif answer == "forbidden":
        assert text == f"vncwebsocket -> HTTP 403: {STATUS_SENTENCES[403]}"
    elif answer == "no_upgrade":
        assert text == "vncwebsocket -> Die Antwort von Proxmox hat nicht das erwartete Format (HTTP 200). Stimmt die Adresse?"
    else:
        assert text == f"vncwebsocket -> {BROKEN_SENTENCE}"


@pytest.mark.asyncio
async def test_console_with_an_invalid_address_does_not_repeat_the_ticket():
    from websockets.exceptions import InvalidURI

    class _Http(_WebsocketHttp):
        def __init__(self) -> None:
            pass

        def websocket(self, url, **kwargs):
            raise InvalidURI(url, "isn't a valid URI: angreifer")

    connector, error_cls = _connector("proxmox", _Http(), "http://xn--angreifer-.example")
    with pytest.raises(error_cls) as caught:
        await connector.open_vnc("pve1", "qemu", "100")
    assert str(caught.value) == f"vncwebsocket -> {BAD_ADDRESS_SENTENCE}"
    assert "geheim-ticket" not in whole_chain(caught.value) and "angreifer" not in whole_chain(caught.value).lower()


@pytest.mark.asyncio
async def test_console_with_an_untrusted_certificate_names_the_certificate():
    """Der WebSocket prueft das Zertifikat selbst; `websockets` reicht den Fehler von `ssl` ohne Huelle durch."""

    class _Http(_WebsocketHttp):
        def __init__(self) -> None:
            pass

        def websocket(self, url, **kwargs):
            raise ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: angreifer")

    connector, error_cls = _connector("proxmox", _Http())
    with pytest.raises(error_cls) as caught:
        await connector.open_vnc("pve1", "qemu", "100")
    assert str(caught.value) == f"vncwebsocket -> {TLS_SENTENCE}"


# -- Hilfen im Einzelnen ------------------------------------------------------------------------


def test_clean_text_removes_control_characters_and_shortens():
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "proxmox" / "src"))
    from nodvard_deck_ext_proxmox.transport import clean_text, task_outcome

    assert clean_text("a\nb\tc\x00d\u202ee  f") == "a b cde f"
    assert clean_text("   ") is None and clean_text(None) is None and clean_text(5) is None
    assert len(clean_text("x" * 500)) == 200
    assert task_outcome("Zeile 1\nZeile 2\x1b[0m") == "Zeile 1 Zeile 2[0m"


# -- Die beiden transport.py bleiben gleich -----------------------------------------------------

TRANSPORT_FILES = {
    "proxmox": REPO_EXTENSIONS_DIR / "proxmox" / "src" / "nodvard_deck_ext_proxmox" / "transport.py",
    "backups": REPO_EXTENSIONS_DIR / "backups" / "src" / "nodvard_deck_ext_backups" / "transport.py",
}

# Erweiterungen importieren einander nie, darum gibt es die Hilfe zweimal. Was in beiden Dateien steht, muss
# Zeichen fuer Zeichen derselbe Code sein (ohne Kommentare und Docstrings); sonst behebt jemand einen Fehler
# in der einen und vergisst die andere. Bewusst verschieden, mit Grund:
DIFFERENT_ON_PURPOSE = {
    # Nur Proxmox oeffnet WebSockets (Konsole); `websockets` zitiert fremde Zeilen, die Fehler davon gehoeren dazu.
    "_FOREIGN_TEXT_MODULES": "websockets nur bei Proxmox",
    "transport_text": "Fehler des WebSocket-Aufbaus (InvalidHandshake, InvalidURI, ...) nur bei Proxmox",
    "carries_foreign_text": "Fehler des WebSocket-Aufbaus nur bei Proxmox",
}
# Nur in einer der beiden Dateien (je Erweiterung eine Menge):
ONLY_IN_ONE = {"proxmox": {"task_outcome"}, "backups": set()}


def _definitions(path: Path) -> dict[str, str]:
    """Name -> Syntaxbaum jeder Funktion, Klasse und Konstante auf oberster Ebene, ohne Docstrings (Kommentare und
    Zeilennummern stecken nicht im Baum)."""
    import ast

    def without_docstring(node: ast.AST) -> ast.AST:
        body = getattr(node, "body", None)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and body:
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                node.body = body[1:] or [ast.Pass()]
        return node

    found: dict[str, str] = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found[node.name] = ast.dump(without_docstring(node))
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            found[node.targets[0].id] = ast.dump(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found[node.target.id] = ast.dump(node.value) if node.value is not None else ""
    return found


def test_the_two_transport_files_stay_the_same_apart_from_the_known_differences():
    proxmox = _definitions(TRANSPORT_FILES["proxmox"])
    backups = _definitions(TRANSPORT_FILES["backups"])
    shared = proxmox.keys() & backups.keys()

    # Die Pruefung vergleicht wirklich etwas (und nicht, weil ein Name umbenannt wurde, nichts mehr).
    assert {"reason_from", "status_text", "http_error_text", "json_body", "clean_text", "redirect_text"} <= shared
    assert DIFFERENT_ON_PURPOSE.keys() <= shared, "ein Name in DIFFERENT_ON_PURPOSE steht nicht mehr in beiden Dateien"

    drifted = sorted(name for name in shared - DIFFERENT_ON_PURPOSE.keys() if proxmox[name] != backups[name])
    assert not drifted, (
        f"{', '.join(drifted)} in extensions/proxmox/.../transport.py und extensions/backups/.../transport.py "
        "unterscheiden sich. Die Dateien sind Kopien (Erweiterungen importieren einander nie): die Aenderung in "
        "beiden machen, oder den Namen bewusst mit Grund in DIFFERENT_ON_PURPOSE eintragen."
    )

    assert {"proxmox": set(proxmox) - shared, "backups": set(backups) - shared} == ONLY_IN_ONE, (
        "Eine Funktion oder Konstante steht nur in einer der beiden transport.py: bewusst, dann in ONLY_IN_ONE "
        "eintragen, sonst in die andere Datei uebernehmen."
    )
