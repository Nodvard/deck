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

**Bindung der aendernden Aufrufe.** Die Tabelle prueft Form und feste Werte, aber nicht, *welcher* Container oder
welches Image gemeint ist. Das regelt die Bindung (`Binding`, unveraenderlich, angelegt mit
`Engine.bind(journal, own_id, ...)`): Jeder Endpunkt ausser `GET` -- `create`, `start`, `stop`, `rename`, `update`,
`remove_container`, `connect`, `tag_image`, `remove_protect_tag` und `pull` -- verlangt sie als Pflichtparameter
`binding=`. Ohne sie (auch mit `None` oder einem anderen Objekt) wirft der Aufruf `NotBound` (eine Art von
`NotAllowed`), bevor etwas an die Engine geht. Es gilt nur die **zuletzt** mit `Engine.bind` angelegte Bindung
derselben Engine; eine aeltere, eine Kopie (`dataclasses.replace`) oder eine ohne `Engine.bind` gebaute wirft ebenso
`NotBound`. Mit ihr gilt:

* `id` nur der alte oder (ab `created`) der neue Container aus dem Journal, nie die eigene ID des Helfers.
  `Engine.bind` lehnt ein Journal ab, das die eigene ID nennt oder dessen neuer Container der alte ist.
* **Vorwaerts** (`undo=False`) geht jeder aendernde Aufruf nur in dem Schritt des Journals, der ihn ankuendigt
  (das Journal wird *vor* dem Schritt geschrieben, die Wiederaufnahme verlaesst sich darauf):

  * `begin`: `pull` mit dem Digest aus der Aufloesung des Tags (`pull_digest`; beim Rueckweg der Digest des Slots);
  * `protected`: `tag_image` auf `nodvard-deck-previous` -- das alte Image mit dessen Version als Tag;
  * `renamed`: `rename` des alten Containers auf `<name>-previous`;
  * `tagged`: `tag_image` auf das Repository -- das bewegliche Tag des Ziels auf das neue Image;
  * `creating`: `create` mit dem Namen des Ziels (`old.name`) und dessen unveraendertem Tag-Text als `Image`;
  * `created`: `connect` des neuen Containers (der Ablauf schreibt `created` mit der neuen ID darum gleich nach
    `create`, vor `connect` und der Nachkontrolle);
  * `old_stopped`: `update` des alten Containers auf `no` (er darf nach einem Neustart des Docker-Dienstes nicht
    wieder anlaufen), dann `stop` des alten;
  * `started`: `start` des neuen Containers;
  * `committed`: `remove_container` des alten Containers.

  So startet der neue Container nie, solange das Journal noch sagt, dass der alte laeuft, und der alte wird nie
  gestoppt, bevor das Journal es ankuendigt.
* **Rueckbau** (`undo=True`, nur vor dem Commit) in jedem Schritt, aber nur zurueck auf den alten Stand. Hat das Journal
  den Rueckbau schon angekuendigt (`Journal.undo`), gibt es nur noch diese Bindung: `Engine.bind` ohne `undo` wirft
  dann `NotBound`, vorwaerts geht nichts mehr. Erlaubt sind `stop` und
  `remove_container` des neuen Containers, das bewegliche Tag auf das alte Image, `rename` des alten Containers auf
  den Namen des Ziels, `update` des alten auf genau seine Restart-Policy aus dem Journal, `start` des alten und
  `remove_protect_tag` seiner Version. Kein `create`, kein `connect`, kein `pull`, kein neues Schutz-Tag, nie `stop`
  des alten oder `start` des neuen. Einen Container, der nicht im Journal steht, entfernt die Bindung nie: Wer nach
  einem Absturz im Schritt `creating` den eigenen, schon angelegten Container findet, traegt ihn zuerst ins Journal
  ein (`created`) und baut dann zurueck.
* `remove_protect_tag` nur mit einer Version aus Journal bzw. Slot: vor dem Commit nur im Rueckbau die des alten
  Images (das Schutz-Tag dieses Vorgangs); nach dem Commit eines Updates die des ersetzten Slots (nie die des alten
  Images, denn das Tag schuetzt jetzt den neuen Slot); nach dem Commit eines Rueckwegs beide Versionen aus dem Journal;
  ohne Journal nur die des Slots.
* Nach dem Commit gibt es nur noch `remove_container` des alten Containers und `remove_protect_tag`.

Die Bindung gilt fuer genau einen Stand des Journals: Nach jedem geschriebenen Schritt holt der Ablauf eine neue
(`Engine.bind` mit dem Journal, wie es jetzt auf der Platte steht); die vorige gilt damit nicht mehr. Scheitert
`Engine.bind`, gilt keine. Nach dem Commit eines Updates und beim Ablauf eines Slots gibt er den Slot, dessen
Schutz-Tag entfernt wird, mit, solange dieser noch in `state.json` steht -- erst das Tag entfernen, dann den Slot
ersetzen (Update) bzw. loeschen (Ablauf), sonst ginge die Version bei einem Absturz verloren und das Tag bliebe fuer
immer liegen. Beim Rueckweg ist es umgekehrt: Beide Versionen stehen im Journal, darum geht dort zuerst der Slot, und
die Tags folgen danach (ohne Slot in der Bindung). Die Fake-Engine der Tests prueft dieselben Regeln (und die Form der
Aufrufe) auf ihrer Seite gegen das Journal, das gerade gilt (`tests/fake_engine.py`, `JournalGuard`).

**Was die Bindung nicht kann:** Sie ist eine zweite Schranke gegen Fehler im Ablauf, keine gegen eine falsche
Zielwahl. Die gebundenen IDs kommen aus der Zielwahl in `target.py`; zeigt diese auf den falschen Container, bindet
die Bindung eben an ihn. Dagegen helfen nur die Zielwahl selbst (genau ein Treffer, ohne die eigene ID, mit Journal
nie ein Ziel) und die erneute Pruefung der Container vor jedem Schritt. Ebenso wenig prueft sie, an welches Netz
`connect` geht (das kommt aus dem Inspect des Ziels), oder den Body von `create` ueber das Image hinaus (das ist
Sache von `clone.py` und der Nachkontrolle).

**Ein `Engine`-Objekt fuer alles:** Vorpruefung, Ablauf und Wiederaufnahme benutzen im Helfer dasselbe Objekt. Die
Vorpruefung handelt bei jedem Lauf die API-Version neu aus; sie laeuft aber in derselben Schleife wie der Ablauf und ruht
darum, solange ein Vorgang laeuft (`__main__`). Waehrend eines Vorgangs handelt nur der Ablauf selbst neu aus, und nur
nach einem Fehler der Engine (`flow`).
"""

from __future__ import annotations

import http.client
import re
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from . import clone, policy
from .policy import Refusal, Slot
from .state import CONTAINER_NAME_RE, PREVIOUS_SUFFIX, RESTART_POLICIES, Journal

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
BOUND_ENDPOINTS = frozenset(name for name, (method, _pattern) in ENDPOINTS.items() if method != "GET")
"""Die aendernden Endpunkte: jeder ausser `GET` verlangt eine Bindung (`Binding`). Ein neuer Endpunkt, der etwas
aendert, faellt so von selbst darunter -- und wird abgelehnt, bis `Engine._check_binding` eine Regel fuer ihn hat."""


class NotAllowed(Exception):
    """Ein Aufruf ausserhalb der Allowlist oder mit ungeprueften Teilen. Ein Programmierfehler, kein Fehler der
    Engine: er wird nie an die Engine geschickt."""


class NotBound(NotAllowed):
    """Ein aendernder Aufruf ohne Bindung, mit einer ungueltigen Bindung oder mit Teilen, die nicht zu ihr passen
    (Container, Name, Image, Version, Digest oder Schritt). Wie `NotAllowed` ein Programmierfehler; die eigene Klasse
    trennt nur in Tests, welche Pruefung gegriffen hat."""


@dataclass(frozen=True)
class Binding:
    """Bindet die aendernden Aufrufe an einen Stand des Journals (Regeln im Kopf des Moduls). Unveraenderlich; angelegt
    mit `Engine.bind`, die Felder werden beim Anlegen geprueft (sonst `NotBound`). Gueltig ist nur die zuletzt
    angelegte Bindung der Engine, die sie angelegt hat -- eine Kopie mit denselben Werten nicht.

    * `own_id`: die eigene Container-ID des Helfers (aus `/proc/self/mountinfo`).
    * `journal`: der Vorgang, wie er jetzt auf der Platte steht; `None` nur fuer das Aufraeumen des Slots.
    * `slot`: der Slot, dessen Schutz-Tag entfernt werden darf (der abgelaufene bzw. beim Commit ersetzte); beim
      Rueckweg der benutzte, dessen Digest allein `pull` nachladen darf.
    * `pull_digest`: der Digest aus der Aufloesung des beweglichen Tags (beim Rueckweg der des Slots), nur fuer `pull`.
    * `undo`: Rueckbau vor dem Commit -- alles geht zurueck auf den alten Container und das alte Image, nichts wird
      angelegt.
    * `repository`: das Repository der Engine, die die Bindung angelegt hat; eine andere Engine lehnt sie ab.
    """

    own_id: str
    journal: Journal | None
    slot: Slot | None
    pull_digest: str | None
    undo: bool
    repository: str

    def __post_init__(self) -> None:
        if not policy.is_container_id(self.own_id):
            raise NotBound("Bindung: eigene ID ungueltig")
        if type(self.undo) is not bool:
            raise NotBound("Bindung: undo ist kein Wahrheitswert")
        if self.pull_digest is not None and not policy.is_digest(self.pull_digest):
            raise NotBound("Bindung: pull_digest ungueltig")
        if self.slot is not None:
            if type(self.slot) is not Slot:
                raise NotBound("Bindung: slot ist kein Slot")
            try:
                self.slot.validate(repository=self.repository)
            except (ValueError, TypeError):
                raise NotBound("Bindung: slot ungueltig") from None
        journal = self.journal
        if journal is None:
            if self.undo or self.pull_digest is not None:
                raise NotBound("Bindung: ohne Journal nur das Schutz-Tag des Slots")
            return
        if type(journal) is not Journal:
            raise NotBound("Bindung: journal ist kein Journal")
        try:
            journal.validate(repository=self.repository)
        except (ValueError, TypeError):
            raise NotBound("Bindung: Journal ungueltig") from None
        new_id = journal.new.id if journal.new is not None else None
        if self.own_id in (journal.old.id, new_id):
            raise NotBound("Bindung: das Journal nennt den Helfer selbst")
        if new_id == journal.old.id:
            raise NotBound("Bindung: neuer und alter Container sind derselbe")
        if self.undo and journal.step == "committed":
            raise NotBound("Bindung: nach dem Commit gibt es keinen Rueckbau")
        if journal.undo and not self.undo:
            raise NotBound("Bindung: der Rueckbau ist angekuendigt, vorwaerts geht nichts mehr")

    @property
    def container_ids(self) -> frozenset[str]:
        """Die Container, die aendernde Aufrufe ansprechen duerfen: der alte und (ab `created`) der neue."""
        journal = self.journal
        if journal is None:
            return frozenset()
        ids = {journal.old.id}
        if journal.new is not None and journal.new.id is not None:
            ids.add(journal.new.id)
        return frozenset(ids) - {self.own_id}

    @property
    def protect_versions(self) -> frozenset[str]:
        """Die Versionen, deren Schutz-Tag `remove_protect_tag` entfernen darf."""
        journal, slot = self.journal, self.slot
        from_slot = {slot.from_version} if slot is not None else set()
        if journal is None:
            return frozenset(from_slot)
        if journal.step != "committed":
            # Vor dem Commit nur im Rueckbau: vorwaerts bleibt das Schutz-Tag dieses Vorgangs liegen.
            return frozenset({journal.old.version}) if self.undo else frozenset()
        if journal.action == "rollback":
            return frozenset({journal.old.version} | ({journal.new.version} if journal.new is not None else set()))
        return frozenset(from_slot - {journal.old.version})


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
        self._binding: Binding | None = None
        """Die zuletzt mit `bind` angelegte Bindung: nur sie gilt."""

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

    # --- Aendern (fuer den Ablauf; jeder Aufruf mit Bindung) -----------------

    def bind(self, journal: Journal | None, own_id: str, *, slot: Slot | None = None, pull_digest: str | None = None,
             undo: bool = False) -> Binding:
        """Die Bindung fuer die aendernden Aufrufe dieses Stands des Journals (Regeln im Kopf des Moduls). Wirft
        `NotBound`, wenn die Teile nicht zusammenpassen (ungueltiges Journal oder Slot, die eigene ID im Journal,
        neuer gleich altem Container, Rueckbau nach dem Commit, `pull_digest` oder `undo` ohne Journal).

        Der Name des Ziels kommt aus dem Journal (`old.name`), nicht als eigener Parameter: zwei Quellen fuer denselben
        Namen koennten auseinanderlaufen.

        Ab jetzt gilt nur die neue Bindung; jede fruehere dieser Engine ist ungueltig, auch wenn `bind` scheitert."""
        self._binding = None
        binding = Binding(own_id=own_id, journal=journal, slot=slot, pull_digest=pull_digest, undo=undo,
                          repository=self._repository)
        self._binding = binding
        return binding

    def create_container(self, name: str, body: dict[str, Any], *, binding: Binding) -> tuple[str, int]:
        """`POST /containers/create?name=<name>`; gibt `(Id, Anzahl Warnungen)` zurueck."""
        result = self._object("POST", "/containers/create", query={"name": name}, body=body, ok=(201,),
                              binding=binding)
        new_id = result.get("Id")
        if not policy.is_container_id(new_id):
            raise EngineError("bad_response")
        warnings = result.get("Warnings")
        return new_id, len(warnings) if isinstance(warnings, list) else 0

    def connect_network(self, network_id: str, container_id: str, endpoint: dict[str, Any], *,
                        binding: Binding) -> None:
        self._expect("POST", "/networks/{id}/connect", params={"id": network_id},
                     body={"Container": container_id, "EndpointConfig": endpoint}, ok=(200,), binding=binding)

    def start_container(self, container_id: str, *, binding: Binding) -> bool:
        """`True`, wenn gestartet; `False` bei 304 (lief schon)."""
        return self._expect("POST", "/containers/{id}/start", params={"id": container_id}, ok=(204, 304),
                            binding=binding) == 204

    def stop_container(self, container_id: str, t: int, *, binding: Binding) -> bool:
        """`True`, wenn gestoppt; `False` bei 304 (stand schon). Wartet bis `t + 30` s auf die Antwort."""
        timeout = float(t + STOP_GRACE_S) if type(t) is int else None  # ungueltiges `t` lehnt `_query_for` ab
        return self._expect("POST", "/containers/{id}/stop", params={"id": container_id}, query={"t": t},
                            ok=(204, 304), timeout=timeout, binding=binding) == 204

    def rename_container(self, container_id: str, name: str, *, binding: Binding) -> None:
        self._expect("POST", "/containers/{id}/rename", params={"id": container_id}, query={"name": name},
                     ok=(204,), binding=binding)

    def set_restart_policy(self, container_id: str, name: str, maximum_retry_count: int = 0, *,
                           binding: Binding) -> None:
        body = {"RestartPolicy": {"Name": name, "MaximumRetryCount": maximum_retry_count}}
        self._expect("POST", "/containers/{id}/update", params={"id": container_id}, body=body, ok=(200,),
                     binding=binding)

    def remove_container(self, container_id: str, *, binding: Binding) -> None:
        """`DELETE /containers/{id}?v=0&force=0` -- nie mit Volumes, nie erzwungen."""
        self._expect("DELETE", "/containers/{id}", params={"id": container_id}, query={"v": "0", "force": "0"},
                     ok=(204,), binding=binding)

    def tag_image(self, image_id: str, repo: str, tag: str, *, binding: Binding) -> None:
        self._expect("POST", "/images/{image_id}/tag", params={"image_id": image_id},
                     query={"repo": repo, "tag": tag}, ok=(200, 201), binding=binding)

    def remove_protect_tag(self, version: str, *, binding: Binding) -> None:
        """Entfernt nur das eigene Schutz-Tag `nodvard-deck-previous:<version>` (ohne `force`)."""
        self._expect("DELETE", "/images/{protect}:{version}",
                     params={"protect": PREVIOUS_REPOSITORY, "version": version}, ok=(200,), binding=binding)

    def pull(self, digest: str, *, binding: Binding) -> int:
        """`POST /images/create?fromImage=<repository>&tag=<digest>` und den Strom bis zum Ende lesen.

        Fehler kommen als HTTP-Status vor dem Strom **oder** als `error`/`errorDetail` mitten im 200-Strom
        (`EngineError("stream_error")`). Gibt die Zahl der gelesenen Meldungen zurueck."""
        deadline = time.monotonic() + PULL_TIMEOUT_S
        status, _, data = self._request(
            "POST", "/images/create", query={"fromImage": self._repository, "tag": digest},
            timeout=PULL_IDLE_S, deadline=deadline, stream=True, binding=binding)
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
        binding: Binding | None = None,
    ) -> tuple[int, dict[str, str], Any]:
        """Der einzige Weg zur Engine. Prueft Methode + Muster gegen `ENDPOINTS`, die Pfadteile, die Abfrage und
        den Body (`NotAllowed`) und bei aendernden Endpunkten danach die Bindung (`NotBound`), schickt die Anfrage und
        liest die Antwort mit Groessen- und Zeitgrenzen.

        Gibt `(status, headers, data)` zurueck: `data` ist das JSON der Antwort (oder `None` ohne Body bzw. ohne
        JSON), beim Strom die Zahl der Meldungen. `api_version` ersetzt fuer diesen einen Aufruf die ausgehandelte
        Version (nur die Aushandlung selbst braucht das, solange sie noch nicht abgeschlossen ist): erlaubt nur fuer
        `/version` und nur innerhalb der Spanne; ohne Versionspraefix (`versioned=False`) geht nur `/_ping`."""
        name = _ALLOWED.get((method, pattern))
        if name is None:
            raise NotAllowed("Endpunkt nicht in der Allowlist")
        if versioned != (name != "ping"):
            # Ohne Praefix antwortet der Docker-Dienst mit seiner neuesten API-Version: das braucht nur `/_ping`.
            raise NotAllowed("nur /_ping geht ohne Versionspraefix")
        if api_version is not None and (name != "version" or not isinstance(api_version, tuple)
                                        or not API_MIN <= api_version <= API_MAX):
            raise NotAllowed("eigene API-Version nur fuer /version in der Aushandlung")
        path = self._path_for(pattern, params or {})
        query_text = self._query_for(name, query or {})
        payload = self._body_for(name, body)
        if name in BOUND_ENDPOINTS:
            self._check_binding(name, binding, params or {}, query or {}, body)
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

    def _check_binding(self, name: str, binding: object, params: Mapping[str, Any], query: Mapping[str, Any],
                       body: Any) -> None:
        """Die Regeln der Bindung (Kopf des Moduls). Laeuft nach den Pruefungen der Form: Pfadteile, Abfrage und Body
        haben hier schon die erwartete Gestalt."""
        if type(binding) is not Binding:
            raise NotBound(f"{name}: ohne Bindung")
        if binding is not self._binding:
            raise NotBound(f"{name}: nicht die zuletzt angelegte Bindung dieser Engine")
        if binding.repository != self._repository:
            raise NotBound(f"{name}: Bindung einer anderen Engine")
        if name == "remove_protect_tag":
            if params["version"] not in binding.protect_versions:
                raise NotBound("remove_protect_tag: Version nicht aus Journal bzw. Slot")
            return
        journal = binding.journal
        if journal is None:
            raise NotBound(f"{name}: ohne Journal")
        old, new, step, undo = journal.old, journal.new, journal.step, binding.undo
        new_id = new.id if new is not None else None

        def forward(announced: str) -> bool:
            """Vorwaerts nur in dem Schritt, den das Journal schon angekuendigt hat."""
            return not undo and step == announced

        if name == "pull_image":
            digest = binding.pull_digest
            if journal.action == "rollback":
                # Der Rueckweg zieht nur den Vorgaenger aus dem Slot nach (nach `image prune -a`).
                slot = binding.slot
                prefix = binding.repository + "@"
                if slot is None or not slot.repo_digest.startswith(prefix) or digest != slot.repo_digest[len(prefix):]:
                    raise NotBound("pull: beim Rueckweg nur der Digest des Slots")
            if not forward("begin") or digest is None or query["tag"] != digest:
                raise NotBound("pull: nur im Schritt begin und nur der aufgeloeste Digest")
        elif name == "tag_image":
            image_id, repo, tag = params["image_id"], query["repo"], query["tag"]
            if repo == PREVIOUS_REPOSITORY:
                allowed = forward("protected") and image_id == old.image_id and tag == old.version
            else:
                # Gegen das Repository der Bindung: dagegen ist das Journal beim Anlegen geprueft worden.
                floating = policy.floating_tag(old.tag_text, repository=binding.repository)
                if undo:
                    allowed = image_id == old.image_id
                else:
                    allowed = forward("tagged") and new is not None and image_id == new.image_id
                allowed = allowed and tag == floating
            if not allowed:
                raise NotBound("tag: Image, Tag oder Schritt nicht aus dem Journal")
        elif name == "create_container":
            if not forward("creating") or query["name"] != old.name or body.get("Image") != old.tag_text:
                raise NotBound("create: nur im Schritt creating, mit Name und Tag-Text des Ziels")
        elif name == "connect_network":
            if not forward("created") or new_id is None or body["Container"] != new_id:
                raise NotBound("connect: nur der neue Container aus dem Journal, nur im Schritt created")
        elif name in ("start_container", "stop_container", "rename_container", "update_container",
                      "remove_container"):
            container_id = params["id"]
            if container_id == binding.own_id or container_id not in binding.container_ids:
                raise NotBound(f"{name}: Container nicht aus dem Journal")
            if name == "start_container":
                # Nie zwei Dashboards zugleich: der neue erst, wenn das Journal den alten als gestoppt fuehrt; der
                # alte nur im Rueckbau.
                allowed = container_id == old.id if undo else (forward("started") and container_id == new_id)
            elif name == "stop_container":
                allowed = container_id == new_id if undo else (forward("old_stopped") and container_id == old.id)
            elif name == "rename_container":
                wanted_name = old.name if undo else old.name + PREVIOUS_SUFFIX
                allowed = (container_id == old.id and query["name"] == wanted_name
                           and (undo or forward("renamed")))
            elif name == "update_container":
                restart = body["RestartPolicy"]
                wanted_policy = (restart["Name"], restart["MaximumRetryCount"])
                expected = old.restart_policy if undo else ("no", 0)
                allowed = container_id == old.id and wanted_policy == expected and (undo or forward("old_stopped"))
            else:
                # Vor dem Commit nur der neue (im Rueckbau), ab dem Commit nur der alte Container.
                allowed = (container_id == old.id if step == "committed"
                           else undo and new_id is not None and container_id == new_id)
            if not allowed:
                raise NotBound(f"{name}: Container, Name, Restart-Policy oder Schritt passen nicht zum Journal")
        else:
            raise NotBound(f"{name}: keine Regel fuer die Bindung")

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
