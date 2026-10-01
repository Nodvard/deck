"""documents-Extension -- Paperless-ngx-artiges Dokumentenarchiv.
Zweite Extension mit echten eigenen
Datenbank-Tabellen (docs/02-EXTENSION-API.md §7, eigener Alembic-Branch) -- der
ECHTE Migrationsweg ist bereits in `test_migrate.py` bewiesen (dort inzwischen
gegen BEIDE Extensions, siehe dortige Tabellen-Assertion); dieser Testlauf
erschafft `ext_documents_*` nur in der schnellen In-Memory-Test-DB, wie
`test_ext_inventory.py` es fuer `ext_inventory_*` tut.

**Kein gemocktes OCR** -- jeder Upload-Test hier ruft den echten `tesseract`-
Prozess ueber `pytesseract` auf (siehe ocr.py). Testbilder werden mit
`PIL.ImageFont.load_default(size=48)` gross genug gerendert, dass Tesseract sie
zuverlaessig erkennt (klein/Standardgroesse erzeugte beim manuellen Test
Fehlerkennungen wie "Halloechnung" statt "Hallo Rechnung"); PDFs werden echt
von Hand gebaut (pdf_fixtures.py, ohne PDF-Bibliothek), einmal mit eingebettetem Textlayer (schneller Pfad in
ocr.py::extract_text) und einmal als reines Bild ohne Textlayer (loest die
Rasterung+OCR-Fallback-Route aus) -- deckt beide Zweige in ocr.py wirklich ab,
nicht nur die Bild-Route."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from nodvard_deck.services import extensions as extensions_service
from pdf_fixtures import pdf_with_image_pages, pdf_with_text_pages
from PIL import Image, ImageDraw, ImageFont

SRC = Path(__file__).resolve().parents[2] / "extensions" / "documents" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nodvard_deck_ext_documents.models import Base as DocumentsBase  # noqa: E402

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


@pytest.fixture(autouse=True)
async def _create_documents_tables(db_session):
    conn = await db_session.connection()
    await conn.run_sync(DocumentsBase.metadata.create_all)


def _png_bytes(text: str) -> bytes:
    img = Image.new("RGB", (900, 150), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=48)
    draw.text((20, 40), text, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _pdf_bytes_with_text_layer(text: str) -> bytes:
    return pdf_with_text_pages([text])


def _pdf_bytes_scanned_image(text: str) -> bytes:
    """Ein PDF, dessen einzige Seite ein eingebettetes BILD ist -- kein
    Textlayer, loest in ocr.py::extract_text den Rasterung+OCR-Fallback aus."""
    return pdf_with_image_pages([Image.open(io.BytesIO(_png_bytes(text)))])


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _enable_documents(client, db_session, test_settings) -> str:
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    token = await _bootstrap_owner(client)
    enabled = await client.post("/api/v1/extensions/documents/enable", headers=_auth_header(token))
    assert enabled.status_code == 200, enabled.text
    return token


@pytest.mark.asyncio
async def test_documents_page_is_registered_after_enabling(client, db_session, test_settings):
    token = await _enable_documents(client, db_session, test_settings)
    pages = await client.get("/api/v1/pages", headers=_auth_header(token))
    assert pages.status_code == 200
    page = next(p for p in pages.json() if p["ext_id"] == "documents")
    assert page["path"] == "/documents"
    assert page["component"] == "DocumentsPage"

    bundle = await client.get("/api/v1/extensions/documents/frontend/index.js")
    assert bundle.status_code == 200
    assert "DocumentsPage" in bundle.text


@pytest.mark.asyncio
async def test_tag_crud_round_trip(client, db_session, test_settings):
    token = await _enable_documents(client, db_session, test_settings)
    headers = _auth_header(token)

    empty = await client.get("/api/v1/ext/documents/tags", headers=headers)
    assert empty.status_code == 200
    assert empty.json() == []

    created = await client.post("/api/v1/ext/documents/tags", json={"name": "Rechnungen", "match_keyword": "rechnung"}, headers=headers)
    assert created.status_code == 201, created.text
    assert created.json()["match_keyword"] == "rechnung"
    tag_id = created.json()["id"]

    deleted = await client.delete(f"/api/v1/ext/documents/tags/{tag_id}", headers=headers)
    assert deleted.status_code == 204
    after = await client.get("/api/v1/ext/documents/tags", headers=headers)
    assert after.json() == []


@pytest.mark.asyncio
async def test_duplicate_tag_name_is_rejected(client, db_session, test_settings):
    token = await _enable_documents(client, db_session, test_settings)
    headers = _auth_header(token)
    first = await client.post("/api/v1/ext/documents/tags", json={"name": "Vertraege"}, headers=headers)
    assert first.status_code == 201
    second = await client.post("/api/v1/ext/documents/tags", json={"name": "Vertraege"}, headers=headers)
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_document_upload_runs_real_ocr_on_an_image(client, db_session, test_settings):
    token = await _enable_documents(client, db_session, test_settings)
    headers = _auth_header(token)
    content = _png_bytes("RECHNUNG TESTFIRMA123")

    uploaded = await client.post(
        "/api/v1/ext/documents/documents?filename=scan.png",
        content=content, headers={**headers, "Content-Type": "image/png"},
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["original_filename"] == "scan.png"
    assert body["ocr_status"] == "done"
    assert body["page_count"] is None
    assert "RECHNUNG" in body["ocr_text"].upper()
    assert "TESTFIRMA123" in body["ocr_text"].upper()


@pytest.mark.asyncio
async def test_document_upload_extracts_embedded_pdf_text_without_rasterizing(client, db_session, test_settings):
    """Ein digital erzeugtes PDF hat schon einen Textlayer -- extract_text() soll
    den direkt lesen, nicht jede Seite unnoetig rastern+OCR'en (schnellerer,
    exakterer Pfad, siehe ocr.py)."""
    token = await _enable_documents(client, db_session, test_settings)
    headers = _auth_header(token)
    content = _pdf_bytes_with_text_layer("DIGITALER TEXTLAYER ABC123")

    uploaded = await client.post(
        "/api/v1/ext/documents/documents?filename=vertrag.pdf",
        content=content, headers={**headers, "Content-Type": "application/pdf"},
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["ocr_status"] == "done"
    assert body["page_count"] == 1
    assert "DIGITALER TEXTLAYER ABC123" in body["ocr_text"]


@pytest.mark.asyncio
async def test_document_upload_ocrs_a_scanned_pdf_with_no_text_layer(client, db_session, test_settings):
    """Ein PDF ohne Textlayer (reines eingebettetes Bild, wie ein echter Scan)
    muss ueber die Rasterung+Tesseract-Route erkannt werden."""
    token = await _enable_documents(client, db_session, test_settings)
    headers = _auth_header(token)
    content = _pdf_bytes_scanned_image("GESCANNT XYZ789")

    uploaded = await client.post(
        "/api/v1/ext/documents/documents?filename=scan.pdf",
        content=content, headers={**headers, "Content-Type": "application/pdf"},
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["ocr_status"] == "done"
    assert body["page_count"] == 1
    assert "GESCANNT" in body["ocr_text"].upper()
    assert "XYZ789" in body["ocr_text"].upper()


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_content_type(client, db_session, test_settings):
    token = await _enable_documents(client, db_session, test_settings)
    res = await client.post(
        "/api/v1/ext/documents/documents?filename=x.exe",
        content=b"whatever", headers={**_auth_header(token), "Content-Type": "application/octet-stream"},
    )
    assert res.status_code == 415


@pytest.mark.asyncio
async def test_document_search_finds_by_filename_and_by_ocr_text(client, db_session, test_settings):
    token = await _enable_documents(client, db_session, test_settings)
    headers = _auth_header(token)

    await client.post(
        "/api/v1/ext/documents/documents?filename=stromrechnung.png",
        content=_png_bytes("STROMANBIETER GMBH"), headers={**headers, "Content-Type": "image/png"},
    )
    await client.post(
        "/api/v1/ext/documents/documents?filename=sonstiges.png",
        content=_png_bytes("ANDERER INHALT"), headers={**headers, "Content-Type": "image/png"},
    )

    by_filename = await client.get("/api/v1/ext/documents/documents?q=stromrechnung", headers=headers)
    assert [d["original_filename"] for d in by_filename.json()] == ["stromrechnung.png"]

    by_ocr_text = await client.get("/api/v1/ext/documents/documents?q=STROMANBIETER", headers=headers)
    assert [d["original_filename"] for d in by_ocr_text.json()] == ["stromrechnung.png"]

    all_docs = await client.get("/api/v1/ext/documents/documents", headers=headers)
    assert {d["original_filename"] for d in all_docs.json()} == {"stromrechnung.png", "sonstiges.png"}


@pytest.mark.asyncio
async def test_auto_tagging_attaches_matching_tag_on_upload(client, db_session, test_settings):
    token = await _enable_documents(client, db_session, test_settings)
    headers = _auth_header(token)

    tag = await client.post(
        "/api/v1/ext/documents/tags", json={"name": "Strom", "match_keyword": "stromanbieter"}, headers=headers,
    )
    assert tag.status_code == 201
    unrelated_tag = await client.post(
        "/api/v1/ext/documents/tags", json={"name": "Versicherung", "match_keyword": "versicherung"}, headers=headers,
    )
    assert unrelated_tag.status_code == 201

    uploaded = await client.post(
        "/api/v1/ext/documents/documents?filename=rechnung.png",
        content=_png_bytes("STROMANBIETER GMBH"), headers={**headers, "Content-Type": "image/png"},
    )
    assert uploaded.status_code == 201, uploaded.text
    tag_names = {t["name"] for t in uploaded.json()["tags"]}
    assert tag_names == {"Strom"}


@pytest.mark.asyncio
async def test_manual_tag_attach_and_detach(client, db_session, test_settings):
    token = await _enable_documents(client, db_session, test_settings)
    headers = _auth_header(token)

    doc = await client.post(
        "/api/v1/ext/documents/documents?filename=x.png",
        content=_png_bytes("BELANGLOS"), headers={**headers, "Content-Type": "image/png"},
    )
    doc_id = doc.json()["id"]
    assert doc.json()["tags"] == []

    tag = await client.post("/api/v1/ext/documents/tags", json={"name": "Manuell"}, headers=headers)
    tag_id = tag.json()["id"]

    attached = await client.post(f"/api/v1/ext/documents/documents/{doc_id}/tags/{tag_id}", headers=headers)
    assert attached.status_code == 204
    after_attach = await client.get(f"/api/v1/ext/documents/documents/{doc_id}", headers=headers)
    assert [t["name"] for t in after_attach.json()["tags"]] == ["Manuell"]

    detached = await client.delete(f"/api/v1/ext/documents/documents/{doc_id}/tags/{tag_id}", headers=headers)
    assert detached.status_code == 204
    after_detach = await client.get(f"/api/v1/ext/documents/documents/{doc_id}", headers=headers)
    assert after_detach.json()["tags"] == []


@pytest.mark.asyncio
async def test_document_filter_by_tag(client, db_session, test_settings):
    token = await _enable_documents(client, db_session, test_settings)
    headers = _auth_header(token)

    tag = await client.post("/api/v1/ext/documents/tags", json={"name": "Wichtig"}, headers=headers)
    tag_id = tag.json()["id"]

    tagged = await client.post(
        "/api/v1/ext/documents/documents?filename=a.png",
        content=_png_bytes("A"), headers={**headers, "Content-Type": "image/png"},
    )
    untagged = await client.post(
        "/api/v1/ext/documents/documents?filename=b.png",
        content=_png_bytes("B"), headers={**headers, "Content-Type": "image/png"},
    )
    await client.post(f"/api/v1/ext/documents/documents/{tagged.json()['id']}/tags/{tag_id}", headers=headers)

    filtered = await client.get(f"/api/v1/ext/documents/documents?tag_id={tag_id}", headers=headers)
    assert [d["id"] for d in filtered.json()] == [tagged.json()["id"]]
    assert untagged.json()["id"] not in [d["id"] for d in filtered.json()]


@pytest.mark.asyncio
async def test_deleting_a_tag_detaches_it_but_leaves_the_document(client, db_session, test_settings):
    token = await _enable_documents(client, db_session, test_settings)
    headers = _auth_header(token)

    tag = await client.post("/api/v1/ext/documents/tags", json={"name": "Temp"}, headers=headers)
    doc = await client.post(
        "/api/v1/ext/documents/documents?filename=x.png",
        content=_png_bytes("X"), headers={**headers, "Content-Type": "image/png"},
    )
    await client.post(f"/api/v1/ext/documents/documents/{doc.json()['id']}/tags/{tag.json()['id']}", headers=headers)

    await client.delete(f"/api/v1/ext/documents/tags/{tag.json()['id']}", headers=headers)

    still_there = await client.get(f"/api/v1/ext/documents/documents/{doc.json()['id']}", headers=headers)
    assert still_there.status_code == 200
    assert still_there.json()["tags"] == []


@pytest.mark.asyncio
async def test_download_returns_original_bytes_content_type_and_filename(client, db_session, test_settings):
    token = await _enable_documents(client, db_session, test_settings)
    headers = _auth_header(token)
    content = _png_bytes("DOWNLOAD TEST")

    uploaded = await client.post(
        "/api/v1/ext/documents/documents", params={"filename": "mein dokument.png"},
        content=content, headers={**headers, "Content-Type": "image/png"},
    )
    doc_id = uploaded.json()["id"]

    downloaded = await client.get(f"/api/v1/ext/documents/documents/{doc_id}/download", headers=headers)
    assert downloaded.status_code == 200
    assert downloaded.content == content
    assert downloaded.headers["content-type"] == "image/png"
    assert "mein dokument.png" in downloaded.headers["content-disposition"]


def _filename_from_disposition(header: str) -> str:
    """Liest `filename*=UTF-8''...` (RFC 6266/5987) aus einem Content-Disposition-Header."""
    from urllib.parse import unquote

    for part in header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == "filename*":
            charset, _, encoded = value.partition("''")
            assert charset == "UTF-8"
            return unquote(encoded, encoding="utf-8")
    raise AssertionError(f"kein filename* in {header!r}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        "Rechnung 49€ – Kopie.png",
        "Bildschirmfoto 2026-09-29 um 10.15.03\u202fPM.png",
    ],
)
async def test_download_works_with_non_latin1_filenames(client, db_session, test_settings, name):
    """Starlette kodiert Header als latin-1 -- €, – oder U+202F im
    Dateinamen liessen Vorschau und Download mit HTTP 500 scheitern."""
    token = await _enable_documents(client, db_session, test_settings)
    headers = _auth_header(token)
    uploaded = await client.post(
        "/api/v1/ext/documents/documents", params={"filename": name},
        content=_png_bytes("X"), headers={**headers, "Content-Type": "image/png"},
    )
    assert uploaded.status_code == 201, uploaded.text
    doc_id = uploaded.json()["id"]

    downloaded = await client.get(f"/api/v1/ext/documents/documents/{doc_id}/download", headers=headers)
    assert downloaded.status_code == 200
    disposition = downloaded.headers["content-disposition"]
    assert disposition.startswith("inline; ")
    assert disposition.isascii(), "Ersatzname in filename= nur ASCII"
    assert _filename_from_disposition(disposition) == name

    # Umbenennen auf einen solchen Namen darf den Download ebenfalls nicht brechen.
    renamed = await client.patch(
        f"/api/v1/ext/documents/documents/{doc_id}", json={"original_filename": "Vertrag – März 2026.png"}, headers=headers,
    )
    assert renamed.status_code == 200, renamed.text
    downloaded = await client.get(f"/api/v1/ext/documents/documents/{doc_id}/download", headers=headers)
    assert downloaded.status_code == 200
    assert _filename_from_disposition(downloaded.headers["content-disposition"]) == "Vertrag – März 2026.png"


@pytest.mark.asyncio
async def test_deleting_a_document_removes_its_file_from_disk(client, db_session, test_settings):
    from nodvard_deck_ext_documents.storage import documents_dir

    token = await _enable_documents(client, db_session, test_settings)
    headers = _auth_header(token)

    uploaded = await client.post(
        "/api/v1/ext/documents/documents?filename=x.png",
        content=_png_bytes("X"), headers={**headers, "Content-Type": "image/png"},
    )
    doc_id = uploaded.json()["id"]

    ext_data_dir = test_settings.ext_data_dir / "documents"
    files_before = list(documents_dir(ext_data_dir).glob("*"))
    assert len(files_before) == 1

    deleted = await client.delete(f"/api/v1/ext/documents/documents/{doc_id}", headers=headers)
    assert deleted.status_code == 204

    files_after = list(documents_dir(ext_data_dir).glob("*"))
    assert files_after == []

    missing = await client.get(f"/api/v1/ext/documents/documents/{doc_id}", headers=headers)
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_get_unknown_document_returns_404(client, db_session, test_settings):
    token = await _enable_documents(client, db_session, test_settings)
    res = await client.get("/api/v1/ext/documents/documents/does-not-exist", headers=_auth_header(token))
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_widget_untagged_lists_only_documents_without_any_tag(client, db_session, test_settings):
    token = await _enable_documents(client, db_session, test_settings)
    headers = _auth_header(token)

    tag = await client.post("/api/v1/ext/documents/tags", json={"name": "Erledigt"}, headers=headers)
    tagged = await client.post(
        "/api/v1/ext/documents/documents?filename=erledigt.png",
        content=_png_bytes("A"), headers={**headers, "Content-Type": "image/png"},
    )
    untagged = await client.post(
        "/api/v1/ext/documents/documents?filename=offen.png",
        content=_png_bytes("B"), headers={**headers, "Content-Type": "image/png"},
    )
    await client.post(f"/api/v1/ext/documents/documents/{tagged.json()['id']}/tags/{tag.json()['id']}", headers=headers)

    widget = await client.get("/api/v1/ext/documents/widgets/untagged", headers=headers)
    assert widget.status_code == 200, widget.text
    names = {row["name"] for row in widget.json()["data"]}
    assert names == {"offen.png"}
    assert untagged.json()["original_filename"] == "offen.png"


@pytest.mark.asyncio
async def test_reads_and_writes_require_their_respective_permission(client, db_session, test_settings):
    """Derselbe read_router/write_router-Split wie inventory (⚪-dokumentierte
    Ein-Permission-pro-Router-Grenze) -- beweist, dass `documents.read` allein
    lesen, aber nicht schreiben darf."""
    from nodvard_deck.core import security
    from nodvard_deck.db.base import refresh_relationships
    from nodvard_deck.models import Role, RolePermission, User

    await _enable_documents(client, db_session, test_settings)

    role = Role(name="documents-reader-test", is_builtin=False, description="Testrolle")
    db_session.add(role)
    await db_session.flush()
    db_session.add(RolePermission(role_id=role.id, permission="documents.read"))
    await db_session.flush()
    await refresh_relationships(db_session, role, "permissions")

    reader = User(username="reader", password_hash=security.hash_password("correct-horse-battery"), is_active=True)
    reader.roles.append(role)
    db_session.add(reader)
    await db_session.flush()
    await db_session.commit()

    login = await client.post("/api/v1/auth/login", json={"username": "reader", "password": "correct-horse-battery"})
    assert login.status_code == 200, login.text
    reader_headers = _auth_header(login.json()["access_token"])

    read_ok = await client.get("/api/v1/ext/documents/tags", headers=reader_headers)
    assert read_ok.status_code == 200, read_ok.text

    write_forbidden = await client.post("/api/v1/ext/documents/tags", json={"name": "X"}, headers=reader_headers)
    assert write_forbidden.status_code == 403


@pytest.mark.asyncio
async def test_rename_changes_only_the_display_name(client, db_session, test_settings):
    token = await _enable_documents(client, db_session, test_settings)
    headers = _auth_header(token)
    doc = (await client.post(
        "/api/v1/ext/documents/documents?filename=scan_0042.png",
        content=_png_bytes("TEST"), headers={**headers, "Content-Type": "image/png"},
    )).json()

    r = await client.patch(f"/api/v1/ext/documents/documents/{doc['id']}", json={"original_filename": "  Stromrechnung 2026.png "}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["original_filename"] == "Stromrechnung 2026.png"
    download = await client.get(f"/api/v1/ext/documents/documents/{doc['id']}/download", headers=headers)
    assert download.status_code == 200, "die Datei auf der Platte behaelt ihren internen Namen"
    assert 'filename="Stromrechnung 2026.png"' in download.headers["content-disposition"]

    for bad in ("", "../x.png", "a\nb.png"):
        assert (await client.patch(f"/api/v1/ext/documents/documents/{doc['id']}", json={"original_filename": bad}, headers=headers)).status_code == 422
    assert (await client.patch("/api/v1/ext/documents/documents/nope", json={"original_filename": "x"}, headers=headers)).status_code == 404
