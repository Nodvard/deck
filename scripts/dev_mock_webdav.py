"""Steht NICHT unter backend/src/nodvard_deck -- reiner Dev-/Demo-Helfer, kein Teil des
Kerns oder seiner Tests (die haben ihren eigenen, gleich gebauten Mock direkt in
backend/tests/test_ext_nextcloud.py). Startet einen realistischen lokalen WebDAV-Mock
fuer die manuelle Live-Verifikation der nextcloud-Extension (WP-11) auf einem festen
Port.

    python scripts/dev_mock_webdav.py --port 8898

Zugangsdaten: Benutzername "alice", App-Passwort "dev-app-password".
"""

from __future__ import annotations

import argparse
import base64

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import Response

USERNAME = "alice"
PASSWORD = "dev-app-password"
EXPECTED_AUTH = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()

fs: dict[str, dict] = {
    "/": {"is_dir": True},
    "/Dokumente": {"is_dir": True},
    "/Dokumente/willkommen.txt": {"is_dir": False, "content": b"Willkommen bei Nextcloud (Dev-Mock)!\n", "mime": "text/plain"},
}

app = FastAPI(title="WebDAV-Mock (Dev)")


def _check_auth(authorization: str | None = Header(default=None)) -> None:
    if authorization != EXPECTED_AUTH:
        raise HTTPException(status_code=401, detail="Ungueltige Basic-Auth.")


def _norm(path: str) -> str:
    return "/" + path.strip("/")


def _direct_children(path: str) -> list[str]:
    base = path.rstrip("/") or "/"
    out = []
    for p in fs:
        if p == path:
            continue
        parent = p.rsplit("/", 1)[0] or "/"
        if parent == base:
            out.append(p)
    return out


def _entry_xml(path: str) -> str:
    entry = fs[path]
    href = f"/remote.php/dav/files/{USERNAME}{path}" + ("/" if entry["is_dir"] and path != "/" else "")
    if entry["is_dir"]:
        return f"<d:response><d:href>{href}</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
    size = len(entry["content"])
    return (
        f"<d:response><d:href>{href}</d:href><d:propstat><d:prop><d:resourcetype/>"
        f"<d:getcontentlength>{size}</d:getcontentlength><d:getcontenttype>{entry['mime']}</d:getcontenttype>"
        "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
    )


@app.api_route("/remote.php/dav/files/{username}/{path:path}", methods=["PROPFIND"])
async def propfind(username: str, path: str, depth: str = Header(default="1", alias="depth"), _: None = Depends(_check_auth)) -> Response:
    p = _norm(path)
    if p not in fs:
        raise HTTPException(status_code=404, detail="Nicht gefunden.")
    parts = [_entry_xml(p)]
    if depth == "1" and fs[p]["is_dir"]:
        parts.extend(_entry_xml(c) for c in _direct_children(p))
    body = '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">' + "".join(parts) + "</d:multistatus>"
    return Response(content=body, media_type="application/xml", status_code=207)


@app.get("/remote.php/dav/files/{username}/{path:path}")
async def get_file(username: str, path: str, _: None = Depends(_check_auth)) -> Response:
    p = _norm(path)
    entry = fs.get(p)
    if entry is None or entry["is_dir"]:
        raise HTTPException(status_code=404, detail="Nicht gefunden.")
    return Response(content=entry["content"], media_type=entry["mime"])


@app.put("/remote.php/dav/files/{username}/{path:path}")
async def put_file(username: str, path: str, request: Request, _: None = Depends(_check_auth)) -> Response:
    p = _norm(path)
    fs[p] = {"is_dir": False, "content": await request.body(), "mime": "application/octet-stream"}
    return Response(status_code=201)


@app.api_route("/remote.php/dav/files/{username}/{path:path}", methods=["MKCOL"])
async def mkcol(username: str, path: str, _: None = Depends(_check_auth)) -> Response:
    fs[_norm(path)] = {"is_dir": True}
    return Response(status_code=201)


@app.delete("/remote.php/dav/files/{username}/{path:path}")
async def delete_entry(username: str, path: str, _: None = Depends(_check_auth)) -> Response:
    p = _norm(path)
    for key in [k for k in fs if k == p or k.startswith(p.rstrip("/") + "/")]:
        del fs[key]
    return Response(status_code=204)


@app.api_route("/remote.php/dav/files/{username}/{path:path}", methods=["MOVE"])
async def move_entry(username: str, path: str, destination: str = Header(alias="destination"), _: None = Depends(_check_auth)) -> Response:
    src = _norm(path)
    dst = _norm(destination.split(f"/remote.php/dav/files/{username}", 1)[-1])
    fs[dst] = fs.pop(src)
    return Response(status_code=201)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8898)
    args = parser.parse_args()
    print(f"WebDAV-Mock auf http://127.0.0.1:{args.port} -- Nutzer '{USERNAME}', App-Passwort '{PASSWORD}'")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
