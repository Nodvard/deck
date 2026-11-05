"""SVG-Prüfung (svg_safe.py): Entitäten und externe Verweise werden nie aufgelöst."""

from __future__ import annotations

import base64
import hashlib
from types import SimpleNamespace
from xml.etree import ElementTree
from xml.parsers import expat

import pytest
from nodvard_deck import svg_safe
from nodvard_deck.svg_safe import UnsafeSvg, check_svg

PLAIN = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'
ILLUSTRATOR = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n'
    + PLAIN
)
XXE = (
    b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
    b'<svg xmlns="http://www.w3.org/2000/svg"><text>&x;</text></svg>'
)
BOMB = (
    b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
    b'<svg xmlns="http://www.w3.org/2000/svg"><text>&b;</text></svg>'
)


def test_plain_and_doctype_without_own_entities_are_accepted():
    check_svg(PLAIN)
    check_svg(ILLUSTRATOR)


@pytest.mark.parametrize("content", [XXE, BOMB, b"kein XML"])
def test_nonce_rendering_does_not_resolve_entities(content):
    assert svg_safe.nonce_svg(content, "testnonce") is None


@pytest.mark.parametrize(("rule", "inline"), [
    ("#r {fill:red}", "fill:blue"),
    ("#r {fill:red !important}", "fill:blue"),
    ("#r {fill:red !important}", "fill:blue !important"),
    ("@layer colors {#r {fill:red !important}}", "fill:blue !important"),
])
def test_nonce_rendering_preserves_inline_cascade(rule, inline):
    content = (
        f'<svg xmlns="{svg_safe.SVG_NS}"><style>{rule}</style>'
        f'<rect id="r" class="shape" style="{inline}"/></svg>'
    ).encode()
    check_svg(content)
    rendered, hashes = svg_safe.nonce_svg(content, "testnonce")
    root = ElementTree.fromstring(rendered)
    style, rect = root
    assert style.tag == f"{{{svg_safe.SVG_NS}}}style"
    assert style.attrib == {"nonce": "testnonce"}
    assert style.text == rule
    assert rect.tag == f"{{{svg_safe.SVG_NS}}}rect"
    assert rect.attrib == {"id": "r", "class": "shape", "style": inline}
    digest = base64.b64encode(hashlib.sha256(inline.encode()).digest()).decode()
    assert hashes == [f"'sha256-{digest}'"]


def test_nonce_rendering_hashes_decoded_attribute_values_once():
    content = (
        f'<svg xmlns="{svg_safe.SVG_NS}"><rect style="fill:blue; --text: &quot;Grün &amp; Blau&quot;"/>'
        '<circle style="fill:blue; --text: &quot;Grün &amp; Blau&quot;"/></svg>'
    ).encode()
    rendered, hashes = svg_safe.nonce_svg(content, "testnonce")
    value = 'fill:blue; --text: "Grün & Blau"'
    assert all(element.get("style") == value for element in ElementTree.fromstring(rendered))
    digest = base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()
    assert hashes == [f"'sha256-{digest}'"]


@pytest.mark.parametrize("count", [74, 5000])
@pytest.mark.parametrize("operation", [check_svg, lambda content: svg_safe.nonce_svg(content, "x" * 43)])
def test_many_unique_styles_are_rejected(count, operation):
    content = (
        f'<svg xmlns="{svg_safe.SVG_NS}">'
        + "".join(f'<rect style="fill:#{i:06x}"/>' for i in range(count))
        + '</svg>'
    ).encode()
    with pytest.raises(UnsafeSvg, match="zu viele unterschiedliche Stile"):
        operation(content)


def test_many_repeated_styles_are_accepted():
    content = (
        f'<svg xmlns="{svg_safe.SVG_NS}">'
        + '<rect style="fill:blue"/>' * 5000
        + '</svg>'
    ).encode()
    check_svg(content)
    rendered, hashes = svg_safe.nonce_svg(content, "x" * 43)
    assert len(ElementTree.fromstring(rendered)) == 5000
    assert len(hashes) == 1


@pytest.mark.parametrize("content", [XXE, BOMB])
def test_entities_are_rejected(content):
    with pytest.raises(UnsafeSvg):
        check_svg(content)


class _BlindGuard:
    """Eine Vorprüfung, die alles durchlässt -- so zeigt der Test, dass der Baum selbst sicher gelesen wird."""

    def Parse(self, _content, _final):  # Name wie bei expat
        return 1


@pytest.mark.parametrize("content", [XXE, BOMB])
def test_the_tree_is_read_with_defusedxml_even_if_the_guard_lets_it_through(monkeypatch, content):
    # Nur die Vorprüfung in svg_safe austauschen, nicht expat für alle (darauf baut auch der Parser auf).
    monkeypatch.setattr(svg_safe, "expat", SimpleNamespace(ParserCreate=lambda *a, **k: _BlindGuard(), ExpatError=expat.ExpatError))
    with pytest.raises(UnsafeSvg, match="keine gültige SVG-Datei"):
        check_svg(content)
    # Ohne Entität geht es auch mit der blinden Vorprüfung weiter wie gewohnt.
    check_svg(PLAIN)
