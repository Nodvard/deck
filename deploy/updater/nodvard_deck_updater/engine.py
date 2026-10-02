"""Engine-API des Docker-Dienstes ueber `AF_UNIX`.

Die eigentliche Macht des Helfers ist nicht `subprocess`, sondern die Engine-API: `/exec`, `/build`, `/archive`
und Co. fuehren beliebige Befehle als root auf dem Rechner aus. Darum geht **jeder** Aufruf durch eine Funktion
(`Engine._request`) mit einer festen Tabelle (`ENDPOINTS`, Methode + Pfadmuster). Was dort nicht steht, wirft
`NotAllowed` -- auch fuer den Helfer selbst, nicht nur fuer Eingaben von aussen.

* **Pfadteile** kommen nur aus geprueften Werten: volle IDs (`^[0-9a-f]{64}\\Z`), Image-IDs (`sha256:<64 hex>`),
  die Konstante des Repositorys, ein bewegliches Tag (`latest`/`X.Y`) oder eine gepruefte Version -- und werden
  trotzdem URL-kodiert (`quote(..., safe="/:@")`).
* **Abfrageparameter** sind je Endpunkt festgelegt (z. B. `DELETE /containers/{id}` immer genau `v=0&force=0`,
  Pull nie ohne Digest als `tag`). **Bodies** ebenso: `POST /containers/{id}/update` nur
  `{"RestartPolicy": {"Name": ..., "MaximumRetryCount": ...}}`; `create` und `connect` nur mit den Schluesseln, die
  `clone.py` senden kann, **in genau dieser Schreibweise** (nie `Entrypoint`, nie `AutoRemove`; der Docker-Dienst ordnet
  JSON-Schluessel ohne Beachtung der Gross- und Kleinschreibung zu, ein Schluessel wie `entrypoint` oder ein zweites
  `image` wuerde sonst an einer Pruefung auf einzelne Namen vorbeigehen).
* **API-Version** wird ausgehandelt (`negotiate`, Spanne 1.41 bis 1.54), alle weiteren Aufrufe tragen `/v1.NN/`.
  Ohne ausgehandelte Version geht kein versionierter Aufruf hinaus (`EngineError("not_negotiated")`, ein Zustand der
  Engine-Anbindung und kein Programmfehler wie `NotAllowed`). Scheitert eine **erneute** Aushandlung nur am Transport
  (der Docker-Dienst startet gerade neu), bleibt die bisherige Version stehen; bei einer unverstaendlichen Antwort oder
  einer Ablehnung (Podman, Version ausser Spanne) wird sie verworfen.
* **Grenzen:** Antworten hoechstens 16 MiB, je Aufruf 60 s, Stop `t + 30` s, Pull insgesamt 30 min (der Strom darf
  dabei bis zu 300 s schweigen). Eine Verbindung je Anfrage, kein Keep-Alive.
* **Fehler** sind `EngineError` mit festem Bezeichner (`reason`) und ggf. HTTP-Status. Der Text der Engine steht nur
  bereinigt und gekuerzt in `message` und ist nur fuer das Helfer-Protokoll gedacht, nie fuer den Status.

Fuer Tests nimmt der Konstruktor den Socket-Pfad und das Repository (`repository=`) -- nie aus der Umgebung.

**Noch nicht umgesetzt: die Bindung der aendernden Endpunkte an bestimmte Container und Images.** Die Tabelle
oben prueft Form und feste Werte, aber noch nicht, **welcher** Container oder welches Image gemeint ist: `id` ist nur
eine gueltige volle ID, `name` nur ein gueltiger Containername, `tag_image` nimmt jede Image-ID. Das kommt als
erster Schritt des Ablaufs (`flow.py`, ein eigener Schritt), bevor ein aendernder Aufruf verdrahtet wird. Dann
bekommen `create`, `start`, `stop`, `rename`, `update`, `remove_container`, `connect`, `tag_image`,
`remove_protect_tag` und `pull` eine unveraenderliche Bindung als Pflichtparameter (z. B.
`Engine.bind(journal, own_id, target_name)`); ohne sie wirft jeder aendernde Aufruf `NotAllowed`. Mit ihr gilt:

* `id` nur der alte oder der neue Container aus dem Journal, nie die eigene ID des Helfers;
* `create`: `name` ist der Name des Ziels; `rename`: der Name des Ziels oder der Name des Ziels mit Endung `-previous`;
* `connect`: `Container` ist der neue Container aus dem Journal;
* `remove_container`: vor dem Commit nur der neue, danach nur der alte Container;
* `tag_image`: das Repository nur mit dem neuen Image (beim Rueckbau mit dem alten), `nodvard-deck-previous` nur mit dem
  alten Image und seiner Version als Tag; `remove_protect_tag` nur mit der Version aus dem Journal bzw. dem Slot;
* `pull` nur mit dem Digest aus der Aufloesung des Tags.

Die Bindung ist eine zweite Schranke gegen Fehler im Ablauf, keine gegen eine falsche Zielwahl: die gebundenen IDs
kommen aus `target.py`. Dagegen helfen nur die Zielwahl (genau ein Treffer, ohne eigene ID und ohne Journal-IDs) und die
erneute Pruefung vor jedem Schritt. Die Invarianten der Fake-Engine in den Tests pruefen dieselben Regeln mit, und ein
Negativtest schickt aendernde Aufrufe mit beliebiger ID, beliebigem Namen und beliebiger Image-ID ohne bzw. mit falscher
Bindung.
"""

from __future__ import annotations

import http.client
import re
import socket
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlencode

from . import clone, policy
from .policy import Refusal
from .state import CONTAINER_NAME_RE, RESTART_POLICIES

SOCKET_PATH = "/var/run/docker.sock"
API_MIN = (1, 41)
API_MAX = (1, 54)
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
CALL_TIMEOUT_S = 60.0
STOP_GRACE_S = 30
"""Zusaetzlich zu `t`: so lange wartet der Helfer auf die Antwort von `stop`."""
PULL_TIMEOUT_S = 30 * 60.0
PULL_IDLE_S = 300.0
"""So lange darf der Pull-Strom schweigen (Entpacken auf dem Pi)."""
PULL_STREAM_MAX_BYTES = 256 * 1024 * 1024
"""Obergrenze fuer den ganzen Pull-Strom (Fortschrittsmeldungen); gespeichert wird davon nichts."""
STREAM_OBJECT_MAX_BYTES = 1024 * 1024
MESSAGE_MAX_CHARS = 200
PREVIOUS_REPOSITORY = "nodvard-deck-previous"
"""Repository des Schutz-Tags fuer das alte Image."""
MAX_STOP_T = 3600

_CHUNK = 64 * 1024
_API_VERSION_RE = re.compile(r"1\.([0-9]{1,3})", re.ASCII)
_LABEL_FILTER_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}=[\x21-\x7e]{0,256}", re.ASCII)

ENDPOINTS: dict[str, tuple[str, str]] = {
    "ping": ("GET", "/_ping"),
    "version": ("GET", "/version"),
    "list_containers": ("GET", "/containers/json"),
    "inspect_container": ("GET", "/containers/{id}/json"),
    "create_container": ("POST", "/containers/create"),
    "start_container": ("POST", "/containers/{id}/start"),
    "stop_container": ("POST", "/containers/{id}/stop"),
    "rename_container": ("POST", "/containers/{id}/rename"),
    "update_container": ("POST", "/containers/{id}/update"),
    "remove_container": ("DELETE", "/containers/{id}"),
    "inspect_network": ("GET", "/networks/{id}"),
    "connect_network": ("POST", "/networks/{id}/connect"),
    "distribution": ("GET", "/distribution/{repository}:{tag}/json"),
    "pull_image": ("POST", "/images/create"),
    "inspect_image": ("GET", "/images/{ref}/json"),
    "tag_image": ("POST", "/images/{image_id}/tag"),
    "remove_protect_tag": ("DELETE", "/images/{protect}:{version}"),
}
"""Die Allowlist: Name -> (Methode, Pfadmuster). Nur diese Paare gehen an die Engine; jedes
Pfadmuster steht genau hier (Code-Waechter: Engine-Pfade gibt es nur in dieser Tabelle)."""
_ALLOWED = {value: name for name, value in ENDPOINTS.items()}
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


class NotAllowed(Exception):
    """Ein Aufruf ausserhalb der Allowlist oder mit ungeprueften Teilen. Ein Programmierfehler, kein Fehler der
    Engine: er wird nie an die Engine geschickt."""


class EngineError(Exception):
    """Fehler im Gespraech mit der Engine.

    * `reason`: fester Bezeichner -- `unreachable` (Socket fehlt, keine Rechte, verweigert), `timeout`,
      `disconnected` (Verbindung abgerissen), `too_large`, `bad_response` (kein gueltiges HTTP/JSON, unerwartete
      Form), `http` (Status ausserhalb des Erwarteten, siehe `status`), `stream_error` (Fehler im 200-Strom),
      `not_negotiated` (noch keine API-Version ausgehandelt oder die letzte Aushandlung wurde abgelehnt; es ging
      nichts an die Engine).
    * `status`: HTTP-Status, wenn es eine Antwort gab.
    * `message`: Text der Engine, bereinigt (Steuerzeichen und ANSI escaped) und gekuerzt -- nur fuers Log.
    """

    def __init__(self, reason: str, *, status: int | None = None, message: str | None = None) -> None:
        super().__init__(reason if status is None else f"{reason}:{status}")
        self.reason = reason
        self.status = status
        self.message = message

    @property
    def not_found(self) -> bool:
        return self.reason == "http" and self.status == 404

    @property
    def code(self) -> str:
        """Der Code fuer den Status: Transportfehler -> `engine_unreachable`, unverstaendliche Antworten ->
        `engine_unsupported`, HTTP-Fehler -> `engine_unreachable` (die Engine kann gerade nicht)."""
        if self.reason in ("too_large", "bad_response"):
            return policy.ENGINE_UNSUPPORTED
        return policy.ENGINE_UNREACHABLE


def sanitize(text: object, limit: int = MESSAGE_MAX_CHARS) -> str:
    """Fremdtext fuers Log: nur ASCII, Steuerzeichen/ANSI/Unicode escaped (`\\x1b`, `\\u2028`), gekuerzt."""
    if not isinstance(text, str):
        text = str(text) if isinstance(text, (int, float)) else ""
    return ascii(text[: limit * 2])[1:-1][:limit]


def parse_api_version(value: object) -> tuple[int, int] | None:
    """`"1.54"` -> `(1, 54)`; alles andere `None`."""
    if not isinstance(value, str):
        return None
    match = _API_VERSION_RE.fullmatch(value)
    return (1, int(match[1])) if match else None


class _UnixConnection(http.client.HTTPConnection):
    """HTTP ueber den Unix-Socket der Engine (nie TCP: `connect` ist ueberschrieben, ohne `super()`)."""

    socket_path = SOCKET_PATH

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            sock.connect(self.socket_path)
        except BaseException:
            sock.close()
            raise
        self.sock = sock


def _is_floating_tag(value: object) -> bool:
    return value == "latest" or (isinstance(value, str) and policy.MINOR_TAG_RE.fullmatch(value) is not None)


def _restart_policy_body(body: object) -> bool:
    if not isinstance(body, dict) or set(body) != {"RestartPolicy"}:
        return False
    rp = body["RestartPolicy"]
    return (isinstance(rp, dict) and set(rp) == {"Name", "MaximumRetryCount"} and rp["Name"] in RESTART_POLICIES
            and type(rp["MaximumRetryCount"]) is int and 0 <= rp["MaximumRetryCount"] <= 1_000_000)


class Engine:
    """Client fuer die Engine-API. Erst `negotiate()`, dann die Methoden (je Aufruf eine Verbindung)."""

    def __init__(self, socket_path: str = SOCKET_PATH, *, repository: str = policy.REPOSITORY,
                 call_timeout: float = CALL_TIMEOUT_S) -> None:
        self._path = socket_path
        self._repository = repository
        self._timeout = call_timeout
        self.api_version: tuple[int, int] | None = None

    # --- Aushandlung --------------------------------------------------------

    def negotiate(self) -> tuple[int, int]:
        """`GET /_ping` -> `Api-Version`; `v = min(Server, 1.54)`; `GET /v{v}/version` -> `MinAPIVersion`.

        `Refusal`: `api_too_old` (Server unter 1.41 oder `v` unter `MinAPIVersion`), `engine_unsupported` (kein
        `Api-Version`, Podman in `Components`/`Platform`, oder die Engine verlangt mehr als 1.54).
        `EngineError` bei Transportfehlern.

        `api_version` wird erst bei Erfolg gesetzt. Scheitert die Aushandlung am Transport (`unreachable`, `timeout`,
        `disconnected`, ein HTTP-Fehler), bleibt der bisherige Wert stehen; bei einer Ablehnung oder unverstaendlichen
        Antwort wird er verworfen (die Engine ist jetzt eine andere)."""
        try:
            version = self._negotiate()
        except EngineError as exc:
            if exc.code == policy.ENGINE_UNSUPPORTED:
                self.api_version = None
            raise
        except BaseException:
            self.api_version = None
            raise
        self.api_version = version
        return version

    def _negotiate(self) -> tuple[int, int]:
        status, headers, _ = self._request("GET", "/_ping", versioned=False, expect_json=False)
        if status != 200:
            raise EngineError("http", status=status)
        server = parse_api_version(headers.get("Api-Version"))
        if server is None:
            raise Refusal(policy.ENGINE_UNSUPPORTED, "no_api_version")
        if server < API_MIN:
            raise Refusal(policy.API_TOO_OLD)
        version = min(server, API_MAX)
        info = self._json("GET", "/version", api_version=version)
        if not isinstance(info, dict):
            raise EngineError("bad_response")
        minimum = parse_api_version(info.get("MinAPIVersion")) or (1, 12)
        if minimum > API_MAX:
            raise Refusal(policy.ENGINE_UNSUPPORTED, "api_too_new")
        if version < max(minimum, API_MIN):
            raise Refusal(policy.API_TOO_OLD)
        if _is_podman(info):
            raise Refusal(policy.ENGINE_UNSUPPORTED, "podman")
        return version

    # --- Lesen --------------------------------------------------------------

    def list_containers(self, labels: list[str] | None = None) -> list[dict[str, Any]]:
        """`GET /containers/json?all=1[&filters={"label": [...]}]` (auch gestoppte Container)."""
        query: dict[str, Any] = {"all": "1"}
        if labels is not None:
            query["filters"] = {"label": list(labels)}
        result = self._json("GET", "/containers/json", query=query)
        if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
            raise EngineError("bad_response")
        return result

    def inspect_container(self, container_id: str) -> dict[str, Any]:
        return self._object("GET", "/containers/{id}/json", params={"id": container_id})

    def inspect_image(self, ref: str) -> dict[str, Any]:
        """`ref`: Image-ID (`sha256:...`) oder `<repository>@sha256:...`."""
        return self._object("GET", "/images/{ref}/json", params={"ref": ref})

    def inspect_network(self, network_id: str) -> dict[str, Any]:
        return self._object("GET", "/networks/{id}", params={"id": network_id})

    def distribution(self, tag: str) -> dict[str, Any]:
        """`GET /distribution/<repository>:<tag>/json` -- loest das bewegliche Tag in der Registry auf."""
        return self._object("GET", "/distribution/{repository}:{tag}/json",
                            params={"repository": self._repository, "tag": tag})

    # --- Aendern (fuer den Ablauf) ------------------------------------------

    def create_container(self, name: str, body: dict[str, Any]) -> tuple[str, int]:
        """`POST /containers/create?name=<name>`; gibt `(Id, Anzahl Warnungen)` zurueck."""
        result = self._object("POST", "/containers/create", query={"name": name}, body=body, ok=(201,))
        new_id = result.get("Id")
        if not policy.is_container_id(new_id):
            raise EngineError("bad_response")
        warnings = result.get("Warnings")
        return new_id, len(warnings) if isinstance(warnings, list) else 0

    def connect_network(self, network_id: str, container_id: str, endpoint: dict[str, Any]) -> None:
        self._expect("POST", "/networks/{id}/connect", params={"id": network_id},
                     body={"Container": container_id, "EndpointConfig": endpoint}, ok=(200,))

    def start_container(self, container_id: str) -> bool:
        """`True`, wenn gestartet; `False` bei 304 (lief schon)."""
        return self._expect("POST", "/containers/{id}/start", params={"id": container_id}, ok=(204, 304)) == 204

    def stop_container(self, container_id: str, t: int) -> bool:
        """`True`, wenn gestoppt; `False` bei 304 (stand schon). Wartet bis `t + 30` s auf die Antwort."""
        timeout = float(t + STOP_GRACE_S) if type(t) is int else None  # ungueltiges `t` lehnt `_query_for` ab
        return self._expect("POST", "/containers/{id}/stop", params={"id": container_id}, query={"t": t},
                            ok=(204, 304), timeout=timeout) == 204

    def rename_container(self, container_id: str, name: str) -> None:
        self._expect("POST", "/containers/{id}/rename", params={"id": container_id}, query={"name": name},
                     ok=(204,))

    def set_restart_policy(self, container_id: str, name: str, maximum_retry_count: int = 0) -> None:
        body = {"RestartPolicy": {"Name": name, "MaximumRetryCount": maximum_retry_count}}
        self._expect("POST", "/containers/{id}/update", params={"id": container_id}, body=body, ok=(200,))

    def remove_container(self, container_id: str) -> None:
        """`DELETE /containers/{id}?v=0&force=0` -- nie mit Volumes, nie erzwungen."""
        self._expect("DELETE", "/containers/{id}", params={"id": container_id}, query={"v": "0", "force": "0"},
                     ok=(204,))

    def tag_image(self, image_id: str, repo: str, tag: str) -> None:
        self._expect("POST", "/images/{image_id}/tag", params={"image_id": image_id},
                     query={"repo": repo, "tag": tag}, ok=(200, 201))

    def remove_protect_tag(self, version: str) -> None:
        """Entfernt nur das eigene Schutz-Tag `nodvard-deck-previous:<version>` (ohne `force`)."""
        self._expect("DELETE", "/images/{protect}:{version}",
                     params={"protect": PREVIOUS_REPOSITORY, "version": version}, ok=(200,))

    def pull(self, digest: str) -> int:
        """`POST /images/create?fromImage=<repository>&tag=<digest>` und den Strom bis zum Ende lesen.

        Fehler kommen als HTTP-Status vor dem Strom **oder** als `error`/`errorDetail` mitten im 200-Strom
        (`EngineError("stream_error")`). Gibt die Zahl der gelesenen Meldungen zurueck."""
        deadline = time.monotonic() + PULL_TIMEOUT_S
        status, _, data = self._request(
            "POST", "/images/create", query={"fromImage": self._repository, "tag": digest},
            timeout=PULL_IDLE_S, deadline=deadline, stream=True)
        if status != 200:
            raise EngineError("http", status=status, message=_error_message(data))
        return data  # type: ignore[return-value]

    # --- Kern: die eine Funktion mit der Allowlist ---------------------------

    def _object(self, method: str, pattern: str, *, ok: tuple[int, ...] = (200,), **kwargs: Any) -> dict[str, Any]:
        result = self._json(method, pattern, ok=ok, **kwargs)
        if not isinstance(result, dict):
            raise EngineError("bad_response")
        return result

    def _json(self, method: str, pattern: str, *, ok: tuple[int, ...] = (200,), **kwargs: Any) -> Any:
        status, _, data = self._request(method, pattern, **kwargs)
        if status not in ok:
            raise EngineError("http", status=status, message=_error_message(data))
        if data is None:
            raise EngineError("bad_response")
        return data

    def _expect(self, method: str, pattern: str, *, ok: tuple[int, ...], **kwargs: Any) -> int:
        status, _, data = self._request(method, pattern, **kwargs)
        if status not in ok:
            raise EngineError("http", status=status, message=_error_message(data))
        return status

    def _request(
        self,
        method: str,
        pattern: str,
        *,
        params: Mapping[str, str] | None = None,
        query: Mapping[str, Any] | None = None,
        body: Any = None,
        timeout: float | None = None,
        deadline: float | None = None,
        versioned: bool = True,
        api_version: tuple[int, int] | None = None,
        expect_json: bool = True,
        stream: bool = False,
    ) -> tuple[int, dict[str, str], Any]:
        """Der einzige Weg zur Engine. Prueft Methode + Muster gegen `ENDPOINTS`, die Pfadteile, die Abfrage und
        den Body (`NotAllowed`), schickt die Anfrage und liest die Antwort mit Groessen- und Zeitgrenzen.

        Gibt `(status, headers, data)` zurueck: `data` ist das JSON der Antwort (oder `None` ohne Body bzw. ohne
        JSON), beim Strom die Zahl der Meldungen. `api_version` ersetzt fuer diesen einen Aufruf die ausgehandelte
        Version (nur die Aushandlung selbst braucht das, solange sie noch nicht abgeschlossen ist)."""
        name = _ALLOWED.get((method, pattern))
        if name is None:
            raise NotAllowed("Endpunkt nicht in der Allowlist")
        path = self._path_for(pattern, params or {})
        query_text = self._query_for(name, query or {})
        payload = self._body_for(name, body)
        if versioned:
            version = api_version or self.api_version
            if version is None:
                # Kein Verstoss gegen die Allowlist, sondern der Zustand der Anbindung: der Aufrufer fasst `EngineError`
                # ohnehin ab und handelt neu aus.
                raise EngineError("not_negotiated")
            path = f"/v{version[0]}.{version[1]}{path}"
        url = path + (f"?{query_text}" if query_text else "")
        call_timeout = self._timeout if timeout is None else timeout
        if deadline is None:
            deadline = time.monotonic() + max(call_timeout, self._timeout)
        headers = {"Host": "localhost", "Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        conn = _UnixConnection("localhost", timeout=call_timeout)
        conn.socket_path = self._path
        try:
            try:
                conn.request(method, url, body=payload, headers=headers)
                response = conn.getresponse()
            except (FileNotFoundError, ConnectionRefusedError, PermissionError):
                raise EngineError("unreachable") from None
            except TimeoutError:
                raise EngineError("timeout") from None
            except http.client.RemoteDisconnected:
                # Verbindung ohne Antwort geschlossen (z. B. der Docker-Dienst startet neu): nicht "unverstaendlich".
                raise EngineError("disconnected") from None
            except http.client.HTTPException:
                raise EngineError("bad_response") from None
            except OSError:
                raise EngineError("unreachable") from None
            response_headers = {key.title(): value for key, value in response.getheaders()}
            if stream and response.status == 200:
                return response.status, response_headers, _read_stream(response, deadline)
            raw = _read_body(response, deadline)
        finally:
            conn.close()
        data: Any = None
        content_type = response_headers.get("Content-Type", "")
        if raw and expect_json and content_type.split(";")[0].strip().lower() == "application/json":
            try:
                data = policy.loads_strict(raw, max_bytes=MAX_RESPONSE_BYTES)
            except ValueError:
                raise EngineError("bad_response", status=response.status) from None
        elif raw and response.status >= 400:
            data = {"message": raw[:MESSAGE_MAX_CHARS * 2].decode("utf-8", "replace")}
        return response.status, response_headers, data

    def _path_for(self, pattern: str, params: Mapping[str, str]) -> str:
        names = _PLACEHOLDER_RE.findall(pattern)
        if set(params) != set(names):
            raise NotAllowed("Pfadteile passen nicht zum Muster")
        path = pattern
        for key in names:
            value = params[key]
            if not self._param_ok(key, value):
                raise NotAllowed(f"Pfadteil {key} ungeprueft")
            path = path.replace("{" + key + "}", quote(value, safe="/:@"), 1)
        return path

    def _param_ok(self, key: str, value: object) -> bool:
        if key == "id":
            return policy.is_container_id(value)
        if key == "image_id":
            return policy.is_image_id(value)
        if key == "ref":
            prefix = self._repository + "@"
            return policy.is_image_id(value) or (
                isinstance(value, str) and value.startswith(prefix) and policy.is_digest(value[len(prefix):]))
        if key == "repository":
            return value == self._repository
        if key == "tag":
            return _is_floating_tag(value)
        if key == "protect":
            return value == PREVIOUS_REPOSITORY
        if key == "version":
            return policy.is_version(value)
        return False

    def _query_for(self, name: str, query: Mapping[str, Any]) -> str:
        """Feste Abfrage je Endpunkt. Gibt den kodierten Text zurueck."""
        keys = set(query)
        if name == "list_containers":
            if query.get("all") != "1" or not keys <= {"all", "filters"}:
                raise NotAllowed("containers/json: nur all=1 und filters")
            items = [("all", "1")]
            if "filters" in query:
                filters = query["filters"]
                if (not isinstance(filters, dict) or set(filters) != {"label"} or not isinstance(filters["label"], list)
                        or not all(isinstance(x, str) and _LABEL_FILTER_RE.fullmatch(x) for x in filters["label"])):
                    raise NotAllowed("containers/json: filters nur label")
                items.append(("filters", policy.dumps({"label": filters["label"]}).decode("ascii")))
            return urlencode(items)
        rules: dict[str, Any] = {
            "create_container": {"name": _container_name},
            "stop_container": {"t": lambda t: type(t) is int and 0 <= t <= MAX_STOP_T},
            "rename_container": {"name": _container_name},
            "remove_container": {"v": lambda v: v == "0", "force": lambda f: f == "0"},
            "pull_image": {"fromImage": lambda r: r == self._repository, "tag": policy.is_digest},
            "tag_image": {"repo": lambda r: r in (self._repository, PREVIOUS_REPOSITORY),
                          "tag": lambda t: _is_floating_tag(t) or policy.is_version(t)},
        }.get(name, {})
        if keys != set(rules):
            raise NotAllowed(f"{name}: Abfrage passt nicht")
        for key, check in rules.items():
            if not check(query[key]):
                raise NotAllowed(f"{name}: {key} ungeprueft")
        if name == "tag_image":
            # Das Repository bekommt nur das bewegliche Tag, das Schutz-Repository nur eine Version -- nie z. B.
            # `ghcr.io/nodvard/deck:0.6.0` (das saehe aus wie ein festes Tag von Compose).
            wanted = policy.is_version if query["repo"] == PREVIOUS_REPOSITORY else _is_floating_tag
            if not wanted(query["tag"]):
                raise NotAllowed("tag: falsche Art von Tag fuer dieses Repository")
        return urlencode([(key, str(query[key])) for key in rules])

    def _body_for(self, name: str, body: Any) -> bytes | None:
        if name == "update_container":
            if not _restart_policy_body(body):
                raise NotAllowed("update: nur RestartPolicy")
        elif name == "connect_network":
            if (not isinstance(body, dict) or set(body) != {"Container", "EndpointConfig"}
                    or not policy.is_container_id(body["Container"])):
                raise NotAllowed("connect: nur Container und EndpointConfig")
            _check_endpoint_config(body["EndpointConfig"], "connect")
        elif name == "create_container":
            self._check_create_body(body)
        elif body is not None:
            raise NotAllowed(f"{name}: kein Body erlaubt")
        return None if body is None else policy.dumps(body)

    def _check_create_body(self, body: Any) -> None:
        """Gurt und Hosentraeger zu `clone.py`: im Body stehen nur Schluessel, die `clone.build` senden kann, und
        zwar in genau dieser Schreibweise (`clone.CREATE_*_KEYS`). Damit gibt es nie `Entrypoint` und nie `AutoRemove`
        -- auch nicht als `entrypoint` oder `autoremove`, die der Docker-Dienst gleich lesen wuerde. Das Image ist der
        unveraenderte Tag-Text des Ziels (bewegliches Tag des Repositorys). Tiefere Ebenen (`RestartPolicy`, `LogConfig`,
        die Eintraege von `Mounts`, ...) kommen unveraendert aus dem Inspect des Docker-Dienstes."""
        if not isinstance(body, dict):
            raise NotAllowed("create: Body ist kein Objekt")
        _only_keys(body, clone.CREATE_CONFIG_KEYS, "create")
        host = body.get("HostConfig")
        if not isinstance(host, dict):
            raise NotAllowed("create: HostConfig fehlt")
        _only_keys(host, clone.CREATE_HOST_KEYS, "create HostConfig")
        if "Healthcheck" in body:
            health = body["Healthcheck"]
            if not isinstance(health, dict):
                raise NotAllowed("create: Healthcheck ist kein Objekt")
            _only_keys(health, frozenset(clone.HEALTH_FIELDS), "create Healthcheck")
        if "NetworkingConfig" in body:
            networking = body["NetworkingConfig"]
            if not isinstance(networking, dict) or set(networking) != {"EndpointsConfig"}:
                raise NotAllowed("create: NetworkingConfig nur mit EndpointsConfig")
            endpoints = networking["EndpointsConfig"]
            if not isinstance(endpoints, dict) or len(endpoints) > 1:
                raise NotAllowed("create: hoechstens ein Endpunkt")
            for endpoint in endpoints.values():
                _check_endpoint_config(endpoint, "create")
        try:
            policy.floating_tag(body.get("Image"), repository=self._repository)
        except Refusal:
            raise NotAllowed("create: Image ist kein bewegliches Tag des Repositorys") from None


def _only_keys(obj: Mapping[Any, Any], known: frozenset[str], what: str) -> None:
    """Nur bekannte Schluessel in genau der bekannten Schreibweise. Eine andere Schreibweise eines bekannten Namens
    ist ebenso abgelehnt wie ein unbekannter Name: der Docker-Dienst ordnet Schluessel ohne Beachtung der
    Gross-/Kleinschreibung zu."""
    folded = {key.casefold() for key in known}
    for key in obj:
        if not isinstance(key, str) or key not in known:
            spelling = isinstance(key, str) and key.casefold() in folded
            raise NotAllowed(f"{what}: Schluessel {'in anderer Schreibweise' if spelling else 'nicht erlaubt'}")


def _check_endpoint_config(endpoint: Any, what: str) -> None:
    if not isinstance(endpoint, dict):
        raise NotAllowed(f"{what}: EndpointConfig ist kein Objekt")
    _only_keys(endpoint, frozenset(clone.ENDPOINT_COPY), f"{what} EndpointConfig")


def _container_name(value: object) -> bool:
    return isinstance(value, str) and CONTAINER_NAME_RE.fullmatch(value) is not None


def _is_podman(info: Mapping[str, Any]) -> bool:
    names = []
    platform = info.get("Platform")
    if isinstance(platform, dict):
        names.append(platform.get("Name"))
    components = info.get("Components")
    if isinstance(components, list):
        names += [item.get("Name") for item in components if isinstance(item, dict)]
    return any(isinstance(n, str) and "podman" in n.lower() for n in names)


def _error_message(data: Any) -> str | None:
    if isinstance(data, dict) and isinstance(data.get("message"), str):
        return sanitize(data["message"])
    return None


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise EngineError("timeout")
    return left


def _read_body(response: http.client.HTTPResponse, deadline: float) -> bytes:
    """Liest den Body hoechstens bis `MAX_RESPONSE_BYTES` (eine laengere Antwort ist `too_large`)."""
    length = response.getheader("Content-Length")
    if length is not None and length.isdigit() and int(length) > MAX_RESPONSE_BYTES:
        raise EngineError("too_large", status=response.status)
    chunks: list[bytes] = []
    size = 0
    try:
        while True:
            _remaining(deadline)
            # `read1`: hoechstens ein Lesevorgang am Socket, damit die Gesamtfrist auch bei tropfenden Antworten greift.
            chunk = response.read1(min(_CHUNK, MAX_RESPONSE_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise EngineError("too_large", status=response.status)
    except TimeoutError:
        raise EngineError("timeout", status=response.status) from None
    except http.client.HTTPException:
        raise EngineError("disconnected", status=response.status) from None
    except OSError:
        raise EngineError("disconnected", status=response.status) from None
    if response.length:
        # `read(amt)` meldet ein vorzeitiges Ende bei `Content-Length` nicht (kein `IncompleteRead`): selbst pruefen.
        raise EngineError("disconnected", status=response.status)
    return b"".join(chunks)


def _read_stream(response: http.client.HTTPResponse, deadline: float) -> int:
    """Liest einen JSON-Strom (ein Objekt je Zeile, `raw_decode`) bis zum Ende. `error`/`errorDetail` in einem
    Objekt -> `EngineError("stream_error")`. Gibt die Zahl der Objekte zurueck."""
    decoder_buffer = ""
    total = count = 0
    pending = b""
    try:
        while True:
            _remaining(deadline)
            chunk = response.read1(_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > PULL_STREAM_MAX_BYTES:
                raise EngineError("too_large", status=200)
            pending += chunk
            try:
                text = pending.decode("utf-8")
                pending = b""
            except UnicodeDecodeError as exc:
                if exc.end < len(pending) - 4:  # nicht nur ein abgeschnittenes Zeichen am Ende
                    raise EngineError("bad_response", status=200) from None
                text, pending = pending[:exc.start].decode("utf-8"), pending[exc.start:]
            decoder_buffer += text
            decoder_buffer, found = _decode_objects(decoder_buffer)
            count += found
            if len(decoder_buffer) > STREAM_OBJECT_MAX_BYTES:
                raise EngineError("too_large", status=200)
    except TimeoutError:
        raise EngineError("timeout", status=200) from None
    except http.client.HTTPException:
        raise EngineError("disconnected", status=200) from None
    except OSError:
        raise EngineError("disconnected", status=200) from None
    if response.length:
        raise EngineError("disconnected", status=200)
    if pending or decoder_buffer.strip():
        raise EngineError("bad_response", status=200)
    return count


def _decode_objects(buffer: str) -> tuple[str, int]:
    """Nimmt alle vollstaendigen JSON-Objekte vorn aus `buffer`; gibt den Rest und ihre Zahl zurueck."""
    count = 0
    while True:
        stripped = buffer.lstrip()
        if not stripped:
            return "", count
        newline = stripped.find("\n")
        if newline < 0:
            return stripped, count  # Zeile noch nicht vollstaendig
        line, buffer = stripped[:newline], stripped[newline + 1:]
        if not line.strip():
            continue
        try:
            obj = policy.loads_strict(line.encode("utf-8"), max_bytes=STREAM_OBJECT_MAX_BYTES)
        except ValueError:
            raise EngineError("bad_response", status=200) from None
        if not isinstance(obj, dict):
            raise EngineError("bad_response", status=200)
        if obj.get("error") or obj.get("errorDetail"):
            detail = obj.get("errorDetail")
            text = obj.get("error") or (detail.get("message") if isinstance(detail, dict) else None)
            raise EngineError("stream_error", status=200, message=sanitize(text) if text else None)
        count += 1
