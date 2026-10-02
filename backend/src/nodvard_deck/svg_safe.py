"""Prüfung hochgeladener SVG-Dateien.

Ein SVG ist ein XML-Dokument und kann Skripte enthalten. Die Auslieferung schützt
zusätzlich mit einer Sandbox (siehe `UNTRUSTED_FILE_HEADERS` in `branding.py`); diese
Prüfung hält gefährliche Dateien schon beim Hochladen fern.

Es gilt eine **Erlaubnisliste**: nur bekannte Zeichen-Elemente, keine Skripte,
keine Animationen (`<set>`/`<animate>` können Adressen umschreiben), kein `<foreignObject>`.
Verweise (`href`, `url(...)`) sind nur auf Teile derselben Datei erlaubt, dazu eingebettete
PNG-/JPEG-/GIF-/WebP-Bilder als `data:`-Adresse. Eine DOCTYPE-Angabe mit eigenen Entitäten
wird abgelehnt (Schutz vor Entity-Bomben), dafür reicht die Standardbibliothek.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from xml.parsers import expat

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"

# Namensräume, die Zeichenprogramme (Inkscape, Illustrator) mitschreiben. Sie werden
# nirgends ausgewertet; nur die Elemente und Attribute selbst sind harmlos.
_EDITOR_NS = frozenset({
    "http://www.inkscape.org/namespaces/inkscape",
    "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "http://purl.org/dc/elements/1.1/",
    "http://creativecommons.org/ns#",
    "http://ns.adobe.com/AdobeIllustrator/10.0/",
    "http://ns.adobe.com/SaveForWeb/1.0/",
    "http://ns.adobe.com/Extensibility/1.0/",
    "http://ns.adobe.com/Variables/1.0/",
    "http://ns.adobe.com/Flows/1.0/",
    "http://ns.adobe.com/GenericCustomNamespace/1.0/",
    "http://www.w3.org/2000/xmlns/",
})

ALLOWED_ELEMENTS = frozenset({
    "svg", "g", "defs", "title", "desc", "metadata", "symbol", "use", "style",
    "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
    "text", "tspan", "textPath",
    "linearGradient", "radialGradient", "stop", "pattern", "clipPath", "mask", "marker",
    "image",
    "filter", "feBlend", "feColorMatrix", "feComponentTransfer", "feComposite",
    "feConvolveMatrix", "feDiffuseLighting", "feDisplacementMap", "feDistantLight",
    "feDropShadow", "feFlood", "feFuncA", "feFuncB", "feFuncG", "feFuncR",
    "feGaussianBlur", "feMerge", "feMergeNode", "feMorphology", "feOffset",
    "fePointLight", "feSpecularLighting", "feSpotLight", "feTile", "feTurbulence",
})

_DATA_IMAGE = re.compile(r"^data:image/(?:png|jpeg|jpg|gif|webp);base64,[A-Za-z0-9+/=\s]*$", re.IGNORECASE)
_URL_REF = re.compile(r"url\(\s*(['\"]?)\s*([^)'\"]*)", re.IGNORECASE)
# `\\` steht für CSS-Escapes (`\\75rl(` ist `url(`), mit denen sich die übrigen Prüfungen
# umgehen ließen; in Logos kommt das praktisch nicht vor. `image-set()` nimmt Adressen
# auch ohne `url(` an.
_CSS_BAD = re.compile(
    r"\\|@import|image-set|expression\s*\(|javascript:|behaviou?r\s*:|-moz-binding", re.IGNORECASE,
)
_CONTROL = re.compile(r"[\x00-\x20\x7f]+")


class UnsafeSvg(ValueError):
    """Das SVG enthält etwas, das beim Hochladen nicht erlaubt ist (Text auf Deutsch)."""


def _local(name: str) -> tuple[str, str]:
    if name.startswith("{"):
        ns, _, local = name[1:].partition("}")
        return ns, local
    return "", name


def _check_css(text: str, where: str) -> None:
    if _CSS_BAD.search(text):
        raise UnsafeSvg(f"{where} enthält nicht erlaubte Anweisungen.")
    for match in _URL_REF.finditer(text):
        target = match.group(2).strip()
        if not target.startswith("#") and not _DATA_IMAGE.match(target):
            raise UnsafeSvg(f"{where} verweist auf eine Adresse außerhalb der Datei.")


def _check_attribute(name: str, value: str) -> None:
    ns, local = _local(name)
    if ns and ns not in (SVG_NS, XLINK_NS, XML_NS) and ns not in _EDITOR_NS:
        raise UnsafeSvg("Das SVG enthält unbekannte Zusätze.")
    if local.lower().startswith("on"):
        raise UnsafeSvg("Das SVG enthält Ereignis-Attribute (onclick o. ä.).")
    if ns in _EDITOR_NS:
        return
    if ns == XML_NS and local == "base":
        raise UnsafeSvg("Das SVG verweist auf eine Adresse außerhalb der Datei.")
    if local == "href":
        target = value.strip()
        if not target.startswith("#") and not _DATA_IMAGE.match(target):
            raise UnsafeSvg("Das SVG verweist auf eine Adresse außerhalb der Datei.")
        return
    compact = _CONTROL.sub("", value).lower()
    if "javascript:" in compact or "vbscript:" in compact or "data:text" in compact:
        raise UnsafeSvg("Das SVG enthält ein Skript in einem Verweis.")
    if "url(" in value.lower() or "\\" in value or local == "style":
        _check_css(value, "Das SVG")


def check_svg(content: bytes) -> None:
    """Wirft `UnsafeSvg` mit einer deutschen Meldung, wenn das SVG nicht sicher aussieht."""
    # Erster Durchlauf nur mit expat: eigene Entitäten (DOCTYPE mit [...]) werden abgelehnt,
    # bevor irgendetwas ersetzt wird. Der C-beschleunigte ElementTree-Parser bietet dafür
    # keinen Einstieg.
    def _entities_not_allowed(*_args) -> None:
        raise UnsafeSvg("Das SVG enthält eigene Entitäten (DOCTYPE) und wird nicht angenommen.")

    guard = expat.ParserCreate()
    guard.StartDoctypeDeclHandler = lambda _name, _system, _public, has_subset: (
        _entities_not_allowed() if has_subset else None
    )
    guard.EntityDeclHandler = _entities_not_allowed

    # `<?xml-stylesheet href=...?>` lädt beim direkten Öffnen ein Stylesheet oder XSLT
    # (XSLT kann Skripte erzeugen). ElementTree lässt solche Anweisungen stillschweigend weg,
    # deshalb hier. Die Kopfzeile `<?xml version=...?>` zählt nicht dazu.
    def _pi_not_allowed(_target, _data) -> None:
        raise UnsafeSvg("Das SVG enthält eine Verarbeitungsanweisung (<?...?>) und wird nicht angenommen.")

    guard.ProcessingInstructionHandler = _pi_not_allowed
    try:
        guard.Parse(content, True)
        root = ET.fromstring(content)
    except UnsafeSvg:
        raise
    # `LookupError`: unbekannte Zeichenkodierung in der Kopfzeile (`encoding='bogus'`).
    except (expat.ExpatError, ET.ParseError, ValueError, UnicodeError, LookupError) as exc:
        raise UnsafeSvg("Das ist keine gültige SVG-Datei.") from exc

    if _local(root.tag) != (SVG_NS, "svg"):
        raise UnsafeSvg("Das ist keine SVG-Datei (Wurzelelement <svg> im SVG-Namensraum fehlt).")

    for el in root.iter():
        if not isinstance(el.tag, str):  # Kommentare / Verarbeitungsanweisungen
            continue
        ns, local = _local(el.tag)
        if ns == SVG_NS:
            if local not in ALLOWED_ELEMENTS:
                raise UnsafeSvg(f"Das SVG enthält das nicht erlaubte Element <{local}>.")
        elif ns not in _EDITOR_NS:
            raise UnsafeSvg("Das SVG enthält unbekannte Zusätze.")
        for attr, value in el.attrib.items():
            _check_attribute(attr, value)
        if local == "style" and ns == SVG_NS:
            _check_css("".join(el.itertext()), "Das SVG")
