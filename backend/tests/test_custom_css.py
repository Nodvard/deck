"""Eigenes CSS der Admins: Prüfung gegen fremde Adressen, Rechte, Auslieferung als Datei."""

from __future__ import annotations

import time

import pytest

from nodvard_deck.custom_css import MAX_CSS_BYTES, UnsafeCss, check_css


async def _login(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.parametrize("css", [
    ":root { --color-accent: #e11d48; }",
    ".panel { border-radius: 0; } /* @import url(https://x) im Kommentar */",
    ".logo { background: url(data:image/png;base64,iVBORw0KGgo=); }",
    ".logo { background: url('/api/v1/branding/logo'); }",
    ".a::before { content: '\\2014'; }",
    # Kommentarzeichen in einer Zeichenkette sind kein Kommentar, ein Kommentar am Ende darf offen bleiben.
    'a { content: "/* kein Kommentar */"; }',
    "a { color: red } /* bis zum Ende",
    "a { background: url( /api/v1/branding/logo ); }",
    "a { background: url(DATA:image/png;base64,iVBORw0KGgo=); }",
    "@font-face { font-family: x; src: url(/fonts/x.woff2) format('woff2'); }",
    # `url (` mit Leerzeichen ist keine Funktion, `-url(` und `#url(` auch nicht.
    "a { grid-area: url (x); } .b { x: -url(//e); } #url(x) {}",
    # Nach `)` ist eine Zeichenkette wieder nur Text, auch wenn sie wie eine Adresse aussieht.
    "a { background: url('/a.png') no-repeat; } a::after { content: 'https://example.com/'; }",
    # Variablen mit `--`, und `<!--`/`-->` in Zeichenketten und Kommentaren sind kein CDO/CDC.
    ":root { --x-y: 1px; --a--b: 2px; } a { width: calc(var(--x-y) - -1px); }",
    "a::after { content: '<!-- -->'; } /* <!-- --> */",
])
def test_allowed(css):
    check_css(css)


@pytest.mark.parametrize("css", [
    "@import url(/x.css);",
    "@IMPORT 'https://example.com/a.css';",
    "@\\69mport 'https://example.com/a.css';",
    "body { background: url(https://example.com/a.png); }",
    "body { background: URL( \"http://example.com/a.png\" ); }",
    "body { background: url(//example.com/a.png); }",
    "body { background: url('/\\\\example.com/a.png'); }",
    "body { background: u\\72l(https://example.com/a.png); }",
    "body { background: image-set('https://example.com/a.png' 1x); }",
    "@font-face { font-family: x; src: url(https://example.com/f.woff2); }",
    "body { background: url(https://example.com/a.png",
    "body { color: red }\x00",
    "a" * (MAX_CSS_BYTES + 1),
    # Kommentarzeichen in einer Zeichenkette öffnen keinen Kommentar, der das url() danach verstecken könnte.
    "a { content: '/*'; } b { background: url(//example.com/a.png); } /* '*/",
    # Ein Zeilenumbruch beendet die Zeichenkette im Browser; was danach kommt, gilt wieder als CSS. Ohne die Regel läse
    # die Prüfung bis zum zweiten `"` weiter und sähe das url() gar nicht (siehe test_a_line_break_ends_a_string).
    'a { content: "abc\n; background: url(//example.com/a.png); content: "; }',
    # Steuer- und Leerzeichen im Ziel, auch als Escape, werden abgelehnt statt umgeschrieben.
    "a { background: url('/a\\9 b.png'); }",
    "a { background: url(/a\\7f b.png); }",
    "a { background: url(/a b.png); }",
    "a { background: url('data:image/svg+xml,<svg xmlns=\"x\"/>'); }",
    "a { background: url(\\2f\\2f example.com/a.png); }",
    # CR LF nach einem Hex-Escape zählt wie LF als ein Leerzeichen (der Browser vereinheitlicht die Zeilenenden).
    "@\\69\r\nmport 'https://example.com/a.css';",
    "a { background: u\\72\r\nl(https://example.com/a.png); }",
    "a { background: u\\72\rl(https://example.com/a.png); }",
    "a { background: u\\72\fl(https://example.com/a.png); }",
    # Funktionen, die Zeichenketten als Adresse laden.
    "a { background: image('https://example.com/a.png'); }",
    "a { background: -webkit-image-set('https://example.com/a.png' 1x); }",
    "a { background: cross-fade(url(/a.png), 'https://example.com/b.png', 50%); }",
    "a { background: src('https://example.com/a.png'); }",
    "a { background: url(''); }",
    "a { content: 'offen",
    "\ud800",
    # CDO und CDC aus HTML-Zeiten.
    "<!-- a { color: red } -->",
    "a { color: red } -->",
    "a { color: red }<!--",
    ":root { --x: 1 } -->a { }",
])
def test_rejected(css):
    with pytest.raises(UnsafeCss):
        check_css(css)


@pytest.mark.parametrize("css", [
    "a { background: url(/x.png); }\n" * 2000,
    "url(" * 16000,
    "(" * 32000 + "'a'" * 10000,
    "\\" * 65000,
    "/*" * 32000,
    "'" + "a" * 65000 + "'",
    "a{b:" + "url('/x')" * 7000 + "}",
])
def test_64_kb_are_checked_quickly(css):
    """Die Prüfung liest jedes Zeichen einmal; auch 64 KB ungünstiger Eingaben dauern deutlich unter einer Sekunde."""
    css = css[:MAX_CSS_BYTES]
    started = time.perf_counter()
    try:
        check_css(css)
    except UnsafeCss:
        pass
    assert time.perf_counter() - started < 1.0


def test_a_line_break_ends_a_string():
    """Ohne die Regel für Zeilenumbrüche in Zeichenketten bliebe der Fall aus `test_rejected` unentdeckt: Die Zeichenkette
    reichte dann bis zum zweiten `"`, und das url() dazwischen sähe die Prüfung nicht."""
    css = 'a { content: "abc\n; background: url(//example.com/a.png); content: "; }'
    with pytest.raises(UnsafeCss, match="Zeichenkette"):
        check_css(css)


@pytest.mark.parametrize("css", [
    'a::after { content: "eins\\\nzwei"; }',
    'a::after { content: "eins\\\r\nzwei"; }',
    'a::after { content: "eins\\\rzwei"; }',
    'a::after { content: "eins\\\fzwei"; }',
])
def test_a_backslash_before_a_line_break_continues_the_string(css):
    """Backslash und Zeilenumbruch in einer Zeichenkette: Der Browser liest weiter (der Umbruch fällt weg), die Prüfung
    auch. Ohne Backslash beendet derselbe Umbruch die Zeichenkette (`test_a_line_break_ends_a_string`). Ein url() mit
    so fortgesetzter Adresse prüft sie als Ganzes."""
    check_css(css)
    continued = css.replace('content: "eins', 'background: url("/').replace('zwei"', '/example.com/a.png")')
    with pytest.raises(UnsafeCss, match="fremd|Adresse|url"):
        check_css(continued)


@pytest.mark.parametrize("css", [
    'a { background: url("/\\2f example.com/a.png"); }',
    "a { background: url('\\2f\\2f example.com/a.png'); }",
    'a { background: url("/\\/example.com/a.png"); }',
    "a { background: url(/\\2f example.com/a.png); }",
])
def test_a_backslash_escape_in_a_url_is_decoded_before_the_check(css):
    """Backslash-Escapes in der Adresse (in Anführungszeichen und ohne): `\\2f` ist ein `/`, aus `/\\2f example.com`
    wird also `//example.com`, eine fremde Adresse. Geprüft wird, was der Browser lädt, nicht der Text."""
    with pytest.raises(UnsafeCss):
        check_css(css)


@pytest.mark.asyncio
async def test_save_serve_and_reject(client):
    owner = await _login(client)
    empty = await client.get("/api/v1/branding/custom.css")
    assert (empty.status_code, empty.text) == (200, "")
    assert empty.headers["content-type"].startswith("text/css")
    assert empty.headers["x-content-type-options"] == "nosniff"

    css = ":root { --color-accent: #e11d48; }"
    saved = await client.put("/api/v1/branding/custom-css", json={"css": css}, headers=owner)
    assert saved.status_code == 200, saved.text
    assert (await client.get("/api/v1/branding/custom.css")).text == css
    assert (await client.get("/api/v1/branding/custom-css", headers=owner)).json() == {"css": css}

    bad = await client.put("/api/v1/branding/custom-css", json={"css": "@import 'https://example.com/a.css';"}, headers=owner)
    assert bad.status_code == 422
    assert "@import" in bad.json()["detail"]
    assert (await client.get("/api/v1/branding/custom.css")).text == css

    audit = (await client.get("/api/v1/audit", params={"action": "branding.custom_css.saved"}, headers=owner)).json()
    entries = audit["items"] if isinstance(audit, dict) else audit
    assert [e["detail"]["bytes"] for e in entries if e["action"] == "branding.custom_css.saved"] == [len(css)]


@pytest.mark.asyncio
async def test_needs_branding_write(client):
    owner = await _login(client)
    await client.post("/api/v1/users", json={"username": "gast", "password": "correct-horse-battery"}, headers=owner)
    login = await client.post("/api/v1/auth/login", json={"username": "gast", "password": "correct-horse-battery"})
    guest = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.put("/api/v1/branding/custom-css", json={"css": ""}, headers=guest)).status_code == 403
    assert (await client.get("/api/v1/branding/custom-css", headers=guest)).status_code == 403
    assert (await client.put("/api/v1/branding/custom-css", json={"css": ""})).status_code == 401


@pytest.mark.asyncio
async def test_a_stored_unsafe_value_is_not_served(client, db_session):
    from nodvard_deck.custom_css import SETTINGS_KEY
    from nodvard_deck.services import settings as settings_service

    await settings_service.set_global(db_session, SETTINGS_KEY, "@import 'https://example.com/a.css';")
    await db_session.commit()
    assert (await client.get("/api/v1/branding/custom.css")).text == ""


@pytest.mark.asyncio
async def test_the_served_file_is_checked_once_and_answers_304_while_unchanged(client):
    from nodvard_deck.custom_css import servable_css

    owner = await _login(client)
    css = ":root { --radius: 0px; }"
    await client.put("/api/v1/branding/custom-css", json={"css": css}, headers=owner)
    servable_css.cache_clear()

    first = await client.get("/api/v1/branding/custom.css")
    second = await client.get("/api/v1/branding/custom.css")
    assert first.text == second.text == css
    assert servable_css.cache_info().misses == 1, "geprüft wird nur beim ersten Abruf"

    etag = first.headers["etag"]
    assert etag == second.headers["etag"] and etag.startswith('"')
    unchanged = await client.get("/api/v1/branding/custom.css", headers={"If-None-Match": etag})
    assert (unchanged.status_code, unchanged.content) == (304, b"")
    assert unchanged.headers["etag"] == etag and unchanged.headers["cache-control"] == "no-cache"

    await client.put("/api/v1/branding/custom-css", json={"css": css + " a { color: red; }"}, headers=owner)
    changed = await client.get("/api/v1/branding/custom.css", headers={"If-None-Match": etag})
    assert changed.status_code == 200 and changed.headers["etag"] != etag
    assert changed.text.endswith("a { color: red; }")
