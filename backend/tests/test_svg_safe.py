"""SVG-Prüfung (svg_safe.py): Entitäten und externe Verweise werden nie aufgelöst."""

from __future__ import annotations

from types import SimpleNamespace
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
