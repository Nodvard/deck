"""Texterkennung: eingebetteter Text bei PDFs, sonst Tesseract (`pytesseract`, nicht der Paperless-ngx-Container)."""

from __future__ import annotations

import io
import threading

import pypdfium2 as pdfium
import pytesseract
from PIL import Image

# `deu+eng` zuerst (deutschsprachiges Homelab), sonst `eng` allein, falls das `deu`-Sprachpaket fehlt (etwa unter Windows):
# Tesseract wirft dafür einen harten Fehler, kein leises Leerergebnis.
_OCR_LANGS_PRIMARY = "deu+eng"
_OCR_LANGS_FALLBACK = "eng"

# Ab so vielen Zeichen gilt eingebetteter PDF-Text als echter Textlayer; ein leeres oder fast leeres Ergebnis
# (Scan-PDF, nur eingebettetes Bild) löst stattdessen Rasterung und OCR aus.
_MIN_EMBEDDED_TEXT_CHARS = 20

# 300 DPI: üblicher Mindestwert für brauchbare Tesseract-Erkennung bei Fließtext, ohne bei mehrseitigen PDFs
# unnötig große Zwischenbilder zu erzeugen.
_RASTER_DPI = 300

# PDFium-Seiten sind in Punkten (1/72 Zoll) bemessen; pypdfium2 rastert über einen Skalierungsfaktor statt über DPI.
_PDF_POINTS_PER_INCH = 72

# PDFium ist nicht thread-sicher (pypdfium2-Doku), und mehrere Uploads können gleichzeitig in `to_thread()`-Threads
# landen: jeder Zugriff läuft unter diesem Lock. Die langsame Tesseract-Erkennung bleibt außerhalb, und es wird immer
# nur eine Seite gleichzeitig gerastert (300 DPI A4 sind ~26 MB), sonst sprengte ein langes Scan-PDF den Speicher.
_PDFIUM_LOCK = threading.Lock()


def _ocr_image(image: Image.Image) -> str:
    try:
        return pytesseract.image_to_string(image, lang=_OCR_LANGS_PRIMARY)
    except pytesseract.TesseractError:
        return pytesseract.image_to_string(image, lang=_OCR_LANGS_FALLBACK)


def _embedded_text(doc: pdfium.PdfDocument) -> str:
    """Eingebetteter Text aller Seiten, durch Zeilenumbruch getrennt (PDFium liefert intern CRLF, zu LF normalisiert)."""
    parts = []
    for page in doc:
        textpage = page.get_textpage()
        try:
            parts.append(textpage.get_text_bounded().replace("\r\n", "\n"))
        finally:
            textpage.close()
            page.close()
    return "\n".join(parts)


def _render_page(doc: pdfium.PdfDocument, index: int) -> Image.Image:
    page = doc[index]
    try:
        bitmap = page.render(scale=_RASTER_DPI / _PDF_POINTS_PER_INCH)
        return bitmap.to_pil().convert("RGB")
    finally:
        page.close()


def extract_text(content: bytes, content_type: str) -> tuple[str, int | None]:
    """(erkannter Text, Seitenzahl); die Seitenzahl ist bei Einzelbildern None.

    Blockiert (Tesseract): nur über `asyncio.to_thread()`. Kaputte oder verschlüsselte PDFs werfen `PdfiumError`."""
    if content_type == "application/pdf":
        # Erst den eingebetteten Textlayer lesen (schnell, exakt; pypdfium2 statt PyMuPDF, das AGPL oder eine Kauflizenz
        # bräuchte). Nur ohne Textlayer (reines Scan-PDF) wird jede Seite gerastert und per Tesseract erkannt.
        with _PDFIUM_LOCK:
            doc = pdfium.PdfDocument(content)
            try:
                page_count = len(doc)
                embedded = _embedded_text(doc)
            except BaseException:
                doc.close()
                raise
        try:
            if len(embedded.strip()) >= _MIN_EMBEDDED_TEXT_CHARS:
                return embedded, page_count
            pages_text = []
            for index in range(page_count):
                with _PDFIUM_LOCK:
                    image = _render_page(doc, index)
                pages_text.append(_ocr_image(image))
            return "\n".join(pages_text), page_count
        finally:
            with _PDFIUM_LOCK:
                doc.close()

    image = Image.open(io.BytesIO(content))
    return _ocr_image(image), None
