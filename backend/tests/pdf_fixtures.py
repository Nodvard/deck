"""Kleine PDF-Bausteine fuer Tests -- von Hand geschrieben, ohne PDF-Bibliothek.

Bis zur Lizenz-Bereinigung baute der Test die PDFs mit einer AGPL-Bibliothek. Fuer die
Tests brauchen wir nur zwei Sorten: Seiten mit eingebettetem Textlayer und Seiten,
die nur ein Bild enthalten (Scan, kein Textlayer). Beides ist in wenigen Zeilen
valides PDF (Kreuzverweistabelle mit echten Offsets, damit kein Reader "reparieren"
muss)."""

from __future__ import annotations

import io
import zlib

from PIL import Image

A4 = (595, 842)


def _assemble(objects: list[bytes]) -> bytes:
    """Setzt nummerierte Objekte (Nummer 1 = Katalog) zu einem PDF mit xref zusammen."""
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_at = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode())
    return out.getvalue()


def _stream(dictionary: str, data: bytes) -> bytes:
    return f"<< {dictionary} /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def pdf_with_text_pages(texts: list[str], size: tuple[int, int] = A4) -> bytes:
    """Ein PDF mit je einer Zeile Helvetica-Text pro Seite (nur ASCII/Latin-1)."""
    width, height = size
    n = len(texts)
    # Nummern: 1 Katalog, 2 Pages, 3 Font, dann je Seite (Page, Content)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    for i, text in enumerate(texts):
        content = f"BT /F1 24 Tf 72 {height - 100} Td ({_escape(text)}) Tj ET".encode("latin-1")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>".encode()
        )
        objects.append(_stream("", content))
    return _assemble(objects)


def pdf_with_image_pages(images: list[Image.Image], size: tuple[int, int] | None = None) -> bytes:
    """Ein PDF, dessen Seiten nur je ein Bild tragen (kein Textlayer). Ohne `size`
    hat die Seite die Pixelmasse des Bildes in PDF-Punkten."""
    n = len(images)
    kids = " ".join(f"{3 + 3 * i} 0 R" for i in range(n))
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode(),
    ]
    for i, image in enumerate(images):
        rgb = image.convert("RGB")
        width, height = size or rgb.size
        page_no = 3 + 3 * i
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
            f"/Resources << /XObject << /Im0 {page_no + 2} 0 R >> >> /Contents {page_no + 1} 0 R >>".encode()
        )
        objects.append(_stream("", f"q {width} 0 0 {height} 0 0 cm /Im0 Do Q".encode()))
        objects.append(
            _stream(
                f"/Type /XObject /Subtype /Image /Width {rgb.width} /Height {rgb.height} "
                "/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode",
                zlib.compress(rgb.tobytes()),
            )
        )
    return _assemble(objects)
