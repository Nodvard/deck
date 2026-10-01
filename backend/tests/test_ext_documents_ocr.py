"""ocr.py direkt, ohne HTTP -- das PDF-Verhalten auf pypdfium2-Basis (die frueher
genutzte AGPL-Bibliothek ist raus, siehe scripts/check_licenses.py).

Festgenagelt wird alles, was vor der Umstellung schon galt: Seitenzahl, Textlayer-
Schnellpfad, Rasterung mit 300 DPI, Fehler bei kaputten PDFs, Bilder ohne Seitenzahl.
Tesseract laeuft wirklich; nur die Groessen-/Aufruf-Tests fangen `_ocr_image` ab."""

from __future__ import annotations

import io
import sys
import threading
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from pdf_fixtures import A4, pdf_with_image_pages, pdf_with_text_pages
from PIL import Image, ImageDraw, ImageFont

SRC = Path(__file__).resolve().parents[2] / "extensions" / "documents" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nodvard_deck_ext_documents import ocr


def _text_image(text: str) -> Image.Image:
    img = Image.new("RGB", (900, 150), "white")
    ImageDraw.Draw(img).text((20, 40), text, fill="black", font=ImageFont.load_default(size=48))
    return img


def test_text_layer_is_extracted_without_rasterizing(monkeypatch):
    def _no_ocr(_image):
        raise AssertionError("Seite darf nicht gerastert/erkannt werden, der Text liegt schon vor")

    monkeypatch.setattr(ocr, "_ocr_image", _no_ocr)
    text, pages = ocr.extract_text(pdf_with_text_pages(["Rechnung Nummer 4711 Testfirma"]), "application/pdf")
    assert pages == 1
    assert "Rechnung Nummer 4711 Testfirma" in text


def test_multi_page_text_is_joined_in_order_and_counts_pages():
    pdf = pdf_with_text_pages(["Erste Seite Alpha", "Zweite Seite Beta", "Dritte Seite Gamma"])
    text, pages = ocr.extract_text(pdf, "application/pdf")
    assert pages == 3
    assert text.index("Alpha") < text.index("Beta") < text.index("Gamma")
    assert "\r" not in text


def test_umlauts_survive_the_text_layer():
    text, _ = ocr.extract_text(pdf_with_text_pages(["Grüße aus München, Ärger öfter"]), "application/pdf")
    assert "Grüße aus München" in text
    assert "Ärger öfter" in text


def test_short_text_layer_falls_back_to_ocr(monkeypatch):
    """Weniger als _MIN_EMBEDDED_TEXT_CHARS Zeichen gelten nicht als Textlayer."""
    seen = []
    monkeypatch.setattr(ocr, "_ocr_image", lambda image: seen.append(image.size) or "ocr")
    text, pages = ocr.extract_text(pdf_with_text_pages(["Kurz"]), "application/pdf")
    assert (text, pages) == ("ocr", 1)
    assert len(seen) == 1


def test_scanned_pdf_is_rasterized_at_300_dpi_per_page(monkeypatch):
    seen = []
    monkeypatch.setattr(ocr, "_ocr_image", lambda image: seen.append(image) or f"seite{len(seen)}")
    scans = [Image.new("RGB", (100, 100), "white"), Image.new("RGB", (100, 100), "white")]
    pdf = pdf_with_image_pages(scans, size=A4)  # A4 = 595 x 842 pt -> 2480 x 3509 px, aufgerundet

    text, pages = ocr.extract_text(pdf, "application/pdf")

    assert pages == 2
    assert text == "seite1\nseite2"
    assert [img.size for img in seen] == [(2480, 3509)] * 2
    assert all(img.mode == "RGB" for img in seen)


def test_raster_keeps_white_background_and_content():
    red = Image.new("RGB", (40, 40), (255, 0, 0))
    pdf = pdf_with_image_pages([red], size=(200, 100))
    doc = pdfium.PdfDocument(pdf)
    image = ocr._render_page(doc, 0)
    doc.close()
    assert image.size == (834, 417)  # 200x100 pt bei 300 DPI, aufgerundet
    assert image.getpixel((10, 10)) == (255, 0, 0)


def test_scanned_pdf_runs_real_tesseract():
    pdf = pdf_with_image_pages([_text_image("SCAN ABC987")])
    text, pages = ocr.extract_text(pdf, "application/pdf")
    assert pages == 1
    assert "ABC987" in text.upper()


def test_image_has_no_page_count():
    buf = io.BytesIO()
    _text_image("BILD QWE321").save(buf, format="PNG")
    text, pages = ocr.extract_text(buf.getvalue(), "image/png")
    assert pages is None
    assert "QWE321" in text.upper()


@pytest.mark.parametrize("junk", [b"das ist kein pdf", b"", b"%PDF-1.4\nkaputt"])
def test_broken_pdf_raises_so_the_upload_reports_ocr_error(junk):
    with pytest.raises(pdfium.PdfiumError):
        ocr.extract_text(junk, "application/pdf")


def test_broken_pdf_is_reported_as_ocr_error_not_as_failed_upload():
    from nodvard_deck_ext_documents import _run_ocr

    text, pages, status, error = _run_ocr(b"kein pdf", "application/pdf")
    assert (text, pages, status) == (None, None, "error")
    assert error


def test_pdfium_is_serialized_across_threads():
    """PDFium ist nicht thread-sicher: parallele Uploads duerfen sich nicht
    ueberlappen. Viele Threads gleichzeitig muessen saubere Ergebnisse liefern."""
    pdf = pdf_with_text_pages(["Paralleler Zugriff Seite eins", "Paralleler Zugriff Seite zwei"])
    results, errors = [], []

    def work():
        try:
            results.append(ocr.extract_text(pdf, "application/pdf"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert len(results) == 12
    assert all(pages == 2 and "Seite zwei" in text for text, pages in results)


def test_lock_is_released_after_an_error():
    with pytest.raises(pdfium.PdfiumError):
        ocr.extract_text(b"kein pdf", "application/pdf")
    assert ocr._PDFIUM_LOCK.acquire(timeout=1)
    ocr._PDFIUM_LOCK.release()
