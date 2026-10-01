"""`services/custom_apps.py` -- Pruefung der Eingaben fuer eigene App-Kacheln ("+ App hinzufuegen").

Reine Funktionen ohne Datenbank. Der Server ruft die Adressen nie selbst ab; trotzdem landen sie
als Link im Browser anderer Nutzer, also zaehlt hier vor allem: nur http/https, kein
`javascript:`/`data:`/`file:`, keine Zugangsdaten in der Adresse, keine Steuerzeichen."""

from __future__ import annotations

import pytest
from nodvard_deck.services import custom_apps as ca

# --- Adresse ---------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "http://192.168.2.1",
        "https://router.fritz.box/",
        "http://nas.local:5000/webman/index.cgi",
        "https://pihole.lan/admin?x=1#top",
        "HTTP://Example.COM",
        "http://[fe80::1]:8080/",
        "http://192.0.2.10:9000",
        "https://münchen.example/",
        "http://docker_host:3000",
    ],
)
def test_url_accepts_http_and_https(value):
    assert ca.clean_url(value) == value


def test_url_is_trimmed():
    assert ca.clean_url("  http://192.168.2.1/admin \n") == "http://192.168.2.1/admin"


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        " javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc/passwd",
        "ftp://nas.local/",
        "vbscript:msgbox(1)",
        "mailto:nico@example.org",
        "//evil.example/",
        "192.168.2.1",
        "router.fritz.box",
        "localhost:8080",
        "ssh://pi@192.168.2.72",
        "blob:http://example.org/uuid",
        "about:blank",
    ],
)
def test_url_rejects_everything_but_http_and_https(value):
    with pytest.raises(ca.CustomAppError, match="http://"):
        ca.clean_url(value)


@pytest.mark.parametrize(
    "value",
    [
        "http://",
        "https://",
        "http:///pfad",
        "http:evil.example",
        "http:/evil.example",
        "http://:8080",
    ],
)
def test_url_needs_a_host(value):
    with pytest.raises(ca.CustomAppError):
        ca.clean_url(value)


@pytest.mark.parametrize(
    "value",
    [
        "http://admin:geheim@192.168.2.1/",
        "http://admin@192.168.2.1/",
        "https://google.com@evil.example/",
        "http://:pw@host/",
    ],
)
def test_url_rejects_credentials_in_the_address(value):
    with pytest.raises(ca.CustomAppError, match="Benutzername"):
        ca.clean_url(value)


@pytest.mark.parametrize(
    "value",
    [
        "http://exa mple.org/",
        "http://example.org/a b",
        "http://example.org/a\nb",
        "http://example.org/\tx",
        "http://example.org/\x00",
        "http://example.org/\x1b[0m",
        "http://example.org/a\u2028b",
        "http://example.org\\@evil.example/",
        "http://evil.example\\.good.example/",
    ],
)
def test_url_rejects_whitespace_control_characters_and_backslashes(value):
    with pytest.raises(ca.CustomAppError):
        ca.clean_url(value)


@pytest.mark.parametrize("value", ["http://host:99999/", "http://host:abc/", "http://host:-1/"])
def test_url_rejects_a_bad_port(value):
    with pytest.raises(ca.CustomAppError, match="Port"):
        ca.clean_url(value)


def test_url_rejects_empty_and_too_long():
    with pytest.raises(ca.CustomAppError, match="Adresse"):
        ca.clean_url("   ")
    ca.clean_url("http://example.org/" + "a" * (ca.URL_MAX - len("http://example.org/")))  # genau am Limit
    with pytest.raises(ca.CustomAppError, match="zu lang"):
        ca.clean_url("http://example.org/" + "a" * ca.URL_MAX)


def test_url_origin_drops_path_query_and_fragment():
    assert ca.url_origin("https://nas.local:5001/webman?token=geheim#x") == "https://nas.local:5001"
    assert ca.url_origin("http://192.168.2.1/") == "http://192.168.2.1"
    assert ca.url_origin("http://[fe80::1]:8080/a") == "http://[fe80::1]:8080"


# --- Name und Gruppe ---------------------------------------------------------


def test_name_is_trimmed_and_limited():
    assert ca.clean_name("  Router  ") == "Router"
    assert ca.clean_name("A" * ca.NAME_MAX) == "A" * ca.NAME_MAX
    with pytest.raises(ca.CustomAppError, match="Name"):
        ca.clean_name("   ")
    with pytest.raises(ca.CustomAppError, match="zu lang"):
        ca.clean_name("A" * (ca.NAME_MAX + 1))


@pytest.mark.parametrize("value", ["Rou\nter", "Rou\x00ter", "a\tb", "x\u202ey"])
def test_name_rejects_control_characters(value):
    with pytest.raises(ca.CustomAppError):
        ca.clean_name(value)


@pytest.mark.parametrize(
    "value",
    [
        "Router\u2028Admin",  # Zeilentrenner (Zl)
        "Router\u2029Admin",  # Absatztrenner (Zp)
        "Router\u00a0Admin",  # geschuetztes Leerzeichen (Zs, nicht das normale)
        "Router\u3000Admin",  # ideographisches Leerzeichen
        "Router\u2003Admin",  # Geviert-Leerzeichen
        "Router\u202fAdmin",  # schmales geschuetztes Leerzeichen
        "Router\u200bAdmin",  # Leerzeichen der Breite null (Cf)
        "Router\U000e0000Admin",  # nicht vergebenes Zeichen ausserhalb der Emoji-Bloecke
        "Router\U0010ffffAdmin",  # Nichtzeichen am Ende des Unicode-Raums
        "Router\U0003ffffAdmin",
        "Router\U0001ffffAdmin",  # Nichtzeichen direkt hinter den Emoji-Bloecken
    ],
)
def test_name_rejects_line_separators_odd_spaces_and_unassigned_characters(value):
    with pytest.raises(ca.CustomAppError):
        ca.clean_name(value)


@pytest.mark.parametrize(
    "value",
    ["\u3164", "\u115f\u1160", "\uffa0", "\u3164\u3164\u3164", "\u200d", "\ufe0f", "\u200d\ufe0f", "\u2800", "\u034f"],
)
def test_name_needs_at_least_one_visible_character(value):
    """Ein Name nur aus Fuellzeichen/Verbindern waere eine Kachel ohne sichtbaren Namen."""
    with pytest.raises(ca.CustomAppError, match="Namen"):
        ca.clean_name(value)


def test_name_with_a_filler_next_to_real_text_is_fine():
    assert ca.clean_name("Router\u3164") == "Router\u3164"
    assert ca.clean_name("1") == "1"
    assert ca.clean_name("***") == "***"
    assert ca.clean_name("\U0001FAE9") == "\U0001FAE9"  # ein Emoji allein ist ein sichtbarer Name


def test_name_may_contain_emoji_sequences():
    assert ca.clean_name("🧑\u200d💻 Terminal") == "🧑\u200d💻 Terminal"
    assert ca.clean_name("Café Ärger") == "Café Ärger"
    assert ca.clean_name("Neu \U0001FAE9") == "Neu \U0001FAE9"  # auch ein Emoji, das diese Python-Version noch nicht kennt


def test_group_is_optional_trimmed_and_limited():
    assert ca.clean_group(None) is None
    assert ca.clean_group("") is None
    assert ca.clean_group("   ") is None
    assert ca.clean_group("  Netzwerk ") == "Netzwerk"
    with pytest.raises(ca.CustomAppError, match="zu lang"):
        ca.clean_group("G" * (ca.GROUP_MAX + 1))
    with pytest.raises(ca.CustomAppError):
        ca.clean_group("Netz\nwerk")


@pytest.mark.parametrize(
    "value",
    ["Netz\u2028werk", "Netz\u2029werk", "Netz\u00a0werk", "Netz\u3000werk", "Netz\U000e0000werk", "Netz\U0010ffffwerk"],
)
def test_group_rejects_line_separators_odd_spaces_and_unassigned_characters(value):
    with pytest.raises(ca.CustomAppError):
        ca.clean_group(value)


@pytest.mark.parametrize("value", ["\u3164", "\u115f\u1160", "\uffa0", "\u200d", "\ufe0f", "\u2800"])
def test_group_needs_at_least_one_visible_character_when_given(value):
    with pytest.raises(ca.CustomAppError, match="sichtbar"):
        ca.clean_group(value)


# --- Symbol und Farbe ----------------------------------------------------------


def test_icon_is_optional():
    assert ca.clean_icon(None) is None
    assert ca.clean_icon("") is None
    assert ca.clean_icon("   ") is None


def test_icon_accepts_names_from_the_fixed_list():
    assert "router" in ca.APP_ICONS and "globe" in ca.APP_ICONS
    for name in ca.APP_ICONS:
        assert ca.clean_icon(name) == name


def test_icon_list_has_no_duplicates_and_only_kebab_case():
    assert len(set(ca.APP_ICONS)) == len(ca.APP_ICONS)
    assert all(name == name.lower() and name.replace("-", "").isalnum() for name in ca.APP_ICONS)


@pytest.mark.parametrize("value", ["🚀", "🧑\u200d💻", "🇩🇪", "👍🏽", "❤\ufe0f", "☁\ufe0f", "🏠", "\U0001FAE9", "ℹ\ufe0f", "↔\ufe0f", "‼\ufe0f"])
def test_icon_accepts_a_single_emoji(value):
    assert ca.clean_icon(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "Router",  # Gross-/Kleinschreibung: kein gueltiger Name der Liste
        "gibt-es-nicht",
        "http://tracker.example/pixel.png",
        "https://tracker.example/logo.svg",
        "//tracker.example/x.png",
        "data:image/png;base64,AAAA",
        "javascript:alert(1)",
        "<img src=x onerror=alert(1)>",
        "../logo.png",
        "a",
        "1",
        "🚀 🚀",  # Leerzeichen
        "🚀a",
        "🚀" * 13,  # laenger als jede echte Emoji-Folge
        "\u200d",  # nur ein Verbindungszeichen
        "\ufe0f",
        "^",
        "`",
        "\x00",
        "\U0010ffff",  # nicht vergebene Zeichen ausserhalb der Emoji-Bloecke sind kein Emoji
        "\U0003ffff",
        "\U000e0000",
        "\U0001ffff",
        "\U0010ffff\ufe0f",
        "\u3164",
        "\u2800",  # Braille-Leerzeichen: ein "Symbol", das nichts anzeigt
    ],
)
def test_icon_rejects_everything_else(value):
    with pytest.raises(ca.CustomAppError, match="Symbol"):
        ca.clean_icon(value)


def test_color_is_optional_and_normalised():
    assert ca.clean_color(None) is None
    assert ca.clean_color("") is None
    assert ca.clean_color("#3B82F6") == "#3b82f6"
    assert ca.clean_color("  #10b981 ") == "#10b981"


@pytest.mark.parametrize(
    "value",
    ["red", "3b82f6", "#3b82f", "#3b82f6a", "#gggggg", "rgb(0,0,0)", "url(http://x)", "#3b82f6;background:url(x)", "var(--x)"],
)
def test_color_rejects_anything_but_six_hex_digits(value):
    with pytest.raises(ca.CustomAppError, match="Farbe"):
        ca.clean_color(value)
