"""Fake-Engine fuer die Tests des Helfers: ein echter HTTP-Server auf einem Unix-Socket.

Der echte Client (`nodvard_deck_updater.engine`) spricht mit ihm wie mit dem Docker-Dienst. Antworten kommen aus
einer **Welt** (`World`: Container-, Image- und Netz-Inspects, `/version`, `Api-Version`), die aus einer
aufgezeichneten Fixture (`fixtures/engines/*.json`, `tools/record_engine.py`) geladen oder im Test gebaut wird.
Einzelne Antworten lassen sich ueberschreiben (`route`): Status, Body, Kopfzeilen, Ströme in Stuecken, Verzoegerung,
abgebrochene Verbindung, rohe Bytes.

Jede Anfrage wird aufgezeichnet (`requests`). Pfade ausserhalb der Allowlist des Helfers werden als Verstoss
festgehalten (`violations`) und mit 599 beantwortet -- die Tests pruefen, dass es keine gibt.

Der Socket liegt unter `/tmp/ndu...` (kurz: Unix-Sockets duerfen hoechstens 107 Byte lang sein).
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import shutil
import socketserver
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Self
from urllib.parse import parse_qs, unquote, urlsplit

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "engines"
_VERSION_PREFIX = re.compile(r"/v(1\.[0-9]{1,3})(/.*)")
_ALLOWED_PATHS = [
    ("GET", r"/_ping"), ("GET", r"/version"), ("GET", r"/containers/json"),
    ("GET", r"/containers/[0-9a-f]{64}/json"), ("POST", r"/containers/create"),
    ("POST", r"/containers/[0-9a-f]{64}/(?:start|stop|rename|update)"), ("DELETE", r"/containers/[0-9a-f]{64}"),
    ("GET", r"/networks/[0-9a-f]{64}"), ("POST", r"/networks/[0-9a-f]{64}/connect"),
    ("GET", r"/distribution/.+/json"), ("POST", r"/images/create"), ("GET", r"/images/.+/json"),
    ("POST", r"/images/sha256:[0-9a-f]{64}/tag"), ("DELETE", r"/images/nodvard-deck-previous:[0-9.]+"),
]


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="ascii"))


@dataclass
class Request:
    method: str
    path: str
    """Pfad ohne Versionspraefix und ohne Abfrage (dekodiert)."""
    raw_path: str
    """Pfad, wie er ankam (mit `/v1.NN`, kodiert)."""
    version: str | None
    query: dict[str, list[str]]
    body: Any
    headers: dict[str, str]


@dataclass
class Response:
    """Eine vorgegebene Antwort. `body`: JSON (wird kodiert) oder `bytes`; `chunks`: Strom in Stuecken
    (chunked); `delay`: so lange vor der Antwort warten; `drop`: Verbindung nach den Kopfzeilen schliessen."""

    status: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    chunks: Iterable[bytes] | None = None
    delay: float = 0.0
    chunk_delay: float = 0.0
    drop: bool = False
    content_type: str | None = "application/json"
    raw: bytes | None = None
    """Ganze Antwort roh (Statuszeile, Kopfzeilen, Body) -- fuer kaputtes HTTP."""


Handler = Callable[[Request], Response]


class World:
    """Was die Engine weiss: Container (Inspect je ID), Images (je ID), Netze (je ID), `/version`, `Api-Version`."""

    def __init__(self, *, api: str = "1.54", version: dict[str, Any] | None = None,
                 containers: dict[str, dict[str, Any]] | None = None, images: dict[str, dict[str, Any]] | None = None,
                 networks: dict[str, dict[str, Any]] | None = None) -> None:
        self.api = api
        self.version = version if version is not None else {
            "Version": "29.3.1", "ApiVersion": api, "MinAPIVersion": "1.40", "Os": "linux", "Arch": "amd64",
            "Components": [{"Name": "Engine", "Version": "29.3.1"}], "Platform": {"Name": "Docker Engine - Community"},
        }
        self.containers = containers or {}
        self.images = images or {}
        self.networks = networks or {}
        self.created: list[dict[str, Any]] = []

    @classmethod
    def from_fixture(cls, name: str) -> World:
        data = copy.deepcopy(load_fixture(name))
        return cls(api=data["ping"]["Api-Version"], version=data["version"], containers=data["inspect"],
                   images=data["images"], networks=data["networks"])

    def copy(self) -> World:
        return World(api=self.api, version=copy.deepcopy(self.version), containers=copy.deepcopy(self.containers),
                     images=copy.deepcopy(self.images), networks=copy.deepcopy(self.networks))

    # --- Hilfen fuer Tests ---------------------------------------------------

    def by_service(self, service: str) -> dict[str, Any]:
        found = [c for c in self.containers.values()
                 if (c.get("Config") or {}).get("Labels", {}).get("com.docker.compose.service") == service]
        assert len(found) == 1, service
        return found[0]

    def add_container(self, inspect: dict[str, Any]) -> dict[str, Any]:
        self.containers[inspect["Id"]] = inspect
        return inspect

    # --- Antworten -----------------------------------------------------------

    def summary(self, inspect: dict[str, Any]) -> dict[str, Any]:
        config = inspect.get("Config") or {}
        host = inspect.get("HostConfig") or {}
        state = inspect.get("State") or {}
        return {
            "Id": inspect.get("Id"), "Names": [inspect.get("Name")], "Image": config.get("Image"),
            "ImageID": inspect.get("Image"), "Labels": config.get("Labels") or {}, "State": state.get("Status"),
            "Status": state.get("Status"), "HostConfig": {"NetworkMode": host.get("NetworkMode")},
        }

    def list_containers(self, query: dict[str, list[str]]) -> list[dict[str, Any]]:
        labels: list[str] = []
        if "filters" in query:
            filters = json.loads(query["filters"][0])
            labels = filters.get("label", [])
        out = []
        for inspect in self.containers.values():
            have = (inspect.get("Config") or {}).get("Labels") or {}
            if all(have.get(key) == value for key, _, value in (item.partition("=") for item in labels)):
                out.append(self.summary(inspect))
        return out

    def image(self, ref: str) -> dict[str, Any] | None:
        if ref in self.images:
            return self.images[ref]
        for image in self.images.values():
            if ref in (image.get("RepoDigests") or []):
                return image
        return None

    def answer(self, request: Request) -> Response:
        method, path = request.method, request.path
        if (method, path) == ("GET", "/_ping"):
            return Response(body=b"OK", headers={"Api-Version": self.api}, content_type="text/plain; charset=utf-8")
        if (method, path) == ("GET", "/version"):
            return Response(body=self.version)
        if (method, path) == ("GET", "/containers/json"):
            return Response(body=self.list_containers(request.query))
        match = re.fullmatch(r"/containers/([0-9a-f]{64})/json", path)
        if method == "GET" and match:
            found = self.containers.get(match[1])
            return Response(body=found) if found else _not_found(f"No such container: {match[1]}")
        match = re.fullmatch(r"/images/(.+)/json", path)
        if method == "GET" and match:
            found = self.image(match[1])
            return Response(body=found) if found else _not_found(f"No such image: {match[1]}")
        match = re.fullmatch(r"/networks/([0-9a-f]{64})", path)
        if method == "GET" and match:
            found = self.networks.get(match[1])
            return Response(body=found) if found else _not_found(f"network {match[1]} not found")
        if (method, path) == ("POST", "/containers/create"):
            self.created.append({"name": request.query.get("name", [None])[0], "body": request.body})
            return Response(status=201, body={"Id": "f" * 64, "Warnings": []})
        return Response(status=501, body={"message": "not implemented in fake"})


def _not_found(message: str) -> Response:
    return Response(status=404, body={"message": message})


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError, TimeoutError)):
            return  # der Client hat aufgelegt (Tests mit Zeitlimits): kein Traceback im Testlauf
        super().handle_error(request, client_address)


class FakeEngine:
    """Startet den Server in einem Thread; als Kontextmanager benutzen."""

    def __init__(self, world: World | None = None) -> None:
        self.world = world or World()
        self.requests: list[Request] = []
        self.violations: list[str] = []
        self._routes: list[tuple[str, re.Pattern[str], Handler]] = []
        self._dir = tempfile.mkdtemp(prefix="ndu", dir="/tmp")
        self.path = os.path.join(self._dir, "e.sock")
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # --- Steuerung -----------------------------------------------------------

    def route(self, method: str, pattern: str, handler: Handler | Response) -> None:
        """Ueberschreibt die Antwort fuer `method` + `pattern` (regulaerer Ausdruck auf den Pfad ohne Version)."""
        func = handler if callable(handler) else (lambda _req, r=handler: r)
        self._routes.insert(0, (method, re.compile(pattern), func))

    def calls(self, method: str | None = None, path: str | None = None) -> list[Request]:
        return [r for r in self.requests if (method is None or r.method == method)
                and (path is None or re.fullmatch(path, r.path))]

    @property
    def methods(self) -> set[str]:
        return {r.method for r in self.requests}

    def start(self) -> FakeEngine:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:  # client_address ist bei AF_UNIX leer
                pass

            def address_string(self) -> str:
                return "unix"

            def _handle(self) -> None:
                fake._serve(self)

            do_GET = do_POST = do_DELETE = do_PUT = do_HEAD = _handle

        self._server = _Server(self.path, Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # --- Bedienung einer Anfrage --------------------------------------------

    def _serve(self, handler: BaseHTTPRequestHandler) -> None:
        split = urlsplit(handler.path)
        version = None
        path = split.path
        match = _VERSION_PREFIX.fullmatch(path)
        if match:
            version, path = match[1], match[2]
        path = unquote(path)
        length = int(handler.headers.get("Content-Length") or 0)
        raw_body = handler.rfile.read(length) if length else b""
        body: Any = None
        if raw_body:
            try:
                body = json.loads(raw_body)
            except ValueError:
                body = raw_body
        request = Request(method=handler.command, path=path, raw_path=handler.path, version=version,
                          query=parse_qs(split.query, keep_blank_values=True), body=body,
                          headers={k: v for k, v in handler.headers.items()})
        with self._lock:
            self.requests.append(request)
        if not any(method == request.method and re.fullmatch(pattern, path) for method, pattern in _ALLOWED_PATHS):
            self.violations.append(f"{request.method} {path}")
            self._send(handler, Response(status=599, body={"message": "outside the allowlist"}))
            return
        if path != "/_ping" and version is None:
            self.violations.append(f"ohne Version: {request.method} {path}")
        response = None
        for method, pattern, func in self._routes:
            if method == request.method and pattern.fullmatch(path):
                response = func(request)
                break
        if response is None:
            response = self.world.answer(request)
        self._send(handler, response)

    def _send(self, handler: BaseHTTPRequestHandler, response: Response) -> None:
        if response.delay:
            time.sleep(response.delay)
        out = handler.wfile
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            if response.raw is not None:
                out.write(response.raw)
                out.flush()
                handler.close_connection = True
                return
            handler.send_response(response.status)
            if response.content_type:
                handler.send_header("Content-Type", response.content_type)
            for key, value in response.headers.items():
                handler.send_header(key, value)
            if response.chunks is not None:
                handler.send_header("Transfer-Encoding", "chunked")
                handler.end_headers()
                for chunk in response.chunks:
                    if response.chunk_delay:
                        time.sleep(response.chunk_delay)
                    out.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                    out.flush()
                    if response.drop:
                        handler.close_connection = True
                        return
                out.write(b"0\r\n\r\n")
                out.flush()
                return
            payload = b""
            if response.body is not None:
                payload = response.body if isinstance(response.body, bytes) else json.dumps(response.body).encode()
            if response.status in (204, 304):
                payload = b""
            handler.send_header("Content-Length", str(len(payload)) if not response.drop else str(len(payload) + 100))
            handler.end_headers()
            out.write(payload)
            out.flush()
            if response.drop:
                handler.close_connection = True
