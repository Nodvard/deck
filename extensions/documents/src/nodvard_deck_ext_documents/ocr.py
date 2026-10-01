"""OCR-Pipeline -- die Tesseract-BIBLIOTHEK (`pytesseract`, ruft das
`tesseract`-Kommandozeilenprogramm auf), NICHT der separate Paperless-ngx-
Container (bewusst so entschieden).

PDFs zuerst per pypdfium2 (PDFium, Apache-2.0/BSD -- bewusst NICHT PyMuPDF, das
AGPL oder eine kommerzielle Artifex-Lizenz braeuchte) auf einen eingebetteten
Textlayer pruefen (schnell, exakt, kein Bildrauschen) -- nur wenn das nichts
liefert (reines Scan-PDF ohne Textlayer) wird jede Seite gerastert und per
Tesseract erkannt. Dasselbe Verhalten wie ein echtes Paperless-ngx bei digital erzeugten vs. gescannten
PDFs, ohne dafuer Seiten doppelt zu erkennen, deren Text schon vorliegt.

`pytesseract.image_to_string()` ruft `subprocess.run()` synchron/blockierend
auf -- der Aufrufer in `__init__.py` MUSS das ueber `asyncio.to_thread()`
starten, sonst haengt der Event-Loop fuer die Dauer der Erkennung fest (bei
einem mehrseitigen PDF durchaus mehrere Sekunden).

PDFium ist NICHT thread-sicher (pypdfium2-Doku): jeder Zugriff darauf laeuft unter
`_PDFIUM_LOCK`, weil mehrere Uploads gleichzeitig in `to_thread()`-Threads landen
koennen. Die (langsame) Tesseract-Erkennung bleibt bewusst AUSSERHALB des Locks, und
es wird immer nur EINE Seite gleichzeitig gerastert (300 DPI A4 sind ~26 MB) --
sonst wuerde ein langes Scan-PDF den Speicher sprengen."""

from __future__ import annotations

import io
import threading

import pypdfium2 as pdfium
import pytesseract
from PIL import Image

# `deu+eng` zuerst (Homelab-Kontext ist deutschsprachig), Fallback auf `eng`
# allein, falls das `deu`-Sprachpaket auf dieser Instanz nicht installiert ist
# (z. B. lokale Windows-Dev-Umgebung ohne zusaetzliches Sprachpaket) --
# Tesseract wirft dafuer einen harten Fehler, kein leises Leerergebnis.
_OCR_LANGS_PRIMARY = "deu+eng"
_OCR_LANGS_FALLBACK = "eng"

# Ab wie vielen Zeichen eingebetteter PDF-Text als "hat einen echten Textlayer"
# gilt -- ein leeres/fast leeres Ergebnis (Scan-PDF, nur eingebettetes Bild)
# loest stattdessen die Rasterung+OCR-Route aus.
_MIN_EMBEDDED_TEXT_CHARS = 20

# 300 DPI -- ueblicher Mindestwert fuer brauchbare Tesseract-Erkennungsraten
# bei Fliesstext, ohne bei mehrseitigen PDFs unnoetig grosse Zwischenbilder zu
# erzeugen.
_RASTER_DPI = 300

# PDFium-Seiten sind in Punkten (1/72 Zoll) bemessen; pypdfium2 rastert ueber einen
# Skalierungsfaktor statt ueber DPI.
_PDF_POINTS_PER_INCH = 72

_PDFIUM_LOCK = threading.Lock()


def _ocr_image(image: Image.Image) -> str:
    try:
        return pytesseract.image_to_string(image, lang=_OCR_LANGS_PRIMARY)
    except pytesseract.TesseractError:
        return pytesseract.image_to_string(image, lang=_OCR_LANGS_FALLBACK)


def _embedded_text(doc: pdfium.PdfDocument) -> str:
    """Eingebetteter Text aller Seiten, durch Zeilenumbruch getrennt (PDFium liefert
    intern CRLF -- zu LF normalisiert)."""
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
    """Gibt (erkannter_text, seitenzahl) zurueck -- seitenzahl ist None fuer
    Einzelbilder (nur PDFs haben Seiten). Ein kaputtes/verschluesseltes PDF wirft
    `pypdfium2.PdfiumError` (der Aufrufer in `__init__.py::_run_ocr` faengt jeden
    Fehler und speichert das Dokument trotzdem)."""
    if content_type == "application/pdf":
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
