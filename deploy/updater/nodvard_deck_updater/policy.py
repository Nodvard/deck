"""Politik des Update-Helfers: reine Funktionen, keine Ein-/Ausgabe.

Hier steht alles, was der Helfer *entscheidet*, bevor er etwas veraendert:

* **Feste Codes** (`CODES`): jede Ablehnung und jeder Grund im Status ist einer davon, nie Freitext
  (`^[a-z_]{1,40}$`). Abgelehnt wird mit `Refusal(code)`.
* **Image-Referenz und Tag:** nur `ghcr.io/nodvard/deck` mit beweglichem Tag (`latest`, `X.Y`, ohne Tag
  = `latest`). `X.Y.Z` oder `@sha256:` -> `pinned_version` ("dort aendern"), alles andere -> `foreign_image`.
* **Versionen:** `X.Y.Z` nur aus ASCII-Ziffern, ohne fuehrende Nullen, je Teil hoechstens 9 Stellen,
  ohne Vorabversion. So gibt es fuer jede Version genau eine Schreibweise, und `"0.6.\\u0661"` (arabische
  Ziffer) ist keine.
* **Anforderung:** genau die fuenf Schluessel, doppelte Schluessel abgelehnt, `v == 1`, `id` als UUID4,
  Zeitfenster 600 s alt / 60 s Zukunft. Jede Ausnahme beim Lesen (auch `RecursionError`) betrifft nur diese
  eine Anforderung.
* **Grenzen:** eine angenommene Aktion je 10 min, 24 h kein Update auf eine Version, von der aus
  zurueckgegangen wurde, verarbeitete IDs 1 h gesperrt, Mindestversion `MIN_VERSION`.
* **Rueckweg-Slot:** genau ein Slot, nur fuer genau den installierten Container und genau die Version
  davor, 7 Tage, einmal benutzbar, nie verkettet.

Die Funktionen bekommen die Uhrzeit (`now`) immer als Parameter; nichts hier liest Uhr, Umgebung oder Dateien.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Feste Werte
# ---------------------------------------------------------------------------

REPOSITORY = "ghcr.io/nodvard/deck"
"""Das einzige Repository, das der Helfer anfasst. Eine Konstante: Tests geben ein anderes nur als Parameter
mit (`repository=`), nie ueber die Umgebung (`tests/test_code_guard.py` prueft beides)."""

VERSION_LABEL = "org.opencontainers.image.version"
"""Label im Image, an dem die Version haengt. Nie aus Container-Labels, Kanal oder Dashboard."""

MIN_VERSION = "0.7.0"
"""Mindestversion des Ziels und des Rueckwegs: das erste Release mit dem Update-Helfer. Aeltere Versionen
koennen eine Vorher-Kopie nicht selbst einspielen. Bis das Release feststeht, bewusst die hoehere Annahme
(sicherer): lieber einmal von Hand aktualisieren als auf eine Version ohne diese Faehigkeit zurueck."""

PROTOCOL = 1
"""Version des Status (`status.json`, Feld `proto`)."""
REQUEST_VERSIONS = (1,)
"""Versionen der Anforderung, die dieser Helfer versteht (Feld `v`). Die Signatur kommt spaeter als `v: 2`."""

SERVICE_ENV = "NODVARD_DECK_UPDATER_SERVICE"
"""Die einzige Einstellung aus der Umgebung: Name des Compose-Dienstes des Dashboards."""
DEFAULT_SERVICE = "nodvard-deck"

REQUEST_MAX_BYTES = 4096
REQUEST_MAX_AGE_S = 600
REQUEST_MAX_FUTURE_S = 60
ACTION_INTERVAL_S = 600
"""Hoechstens eine angenommene Aktion je 10 Minuten (abgelehnte zaehlen nicht)."""
BLOCK_AFTER_ROLLBACK_S = 24 * 3600
"""Nach einem Rueckweg von Version X: so lange kein Update auf X."""
SEEN_TTL_S = 3600
"""So lange bleibt eine verarbeitete Anforderungs-ID gesperrt (Replay). Laenger als das Zeitfenster der
Anforderung (600 s): eine aeltere Wiederholung scheitert ohnehin an `expired`."""
SLOT_TTL_S = 7 * 24 * 3600
"""So lange gilt der Rueckweg-Slot nach einem Update."""
CLOCK_SLACK_S = 60
"""Spielraum fuer Uhrsprung: ein Slot, der laenger als `SLOT_TTL_S + CLOCK_SLACK_S` in der Zukunft endet, gilt
nicht (die Uhr ist zurueckgesprungen oder die Datei ist falsch) -- fail-closed."""
DEADLINE_S = 15 * 60
"""Frist fuer einen Ablauf bis zum ersten `healthy` (fest, nicht einstellbar)."""
STATUS_MAX_BYTES = 16 * 1024
RESULTS_MAX = 10

# ---------------------------------------------------------------------------
# Codes -- alle passen auf CODE_RE
# ---------------------------------------------------------------------------

CODE_RE = re.compile(r"[a-z_]{1,40}", re.ASCII)

# Einrichtung
CHANNEL_UNSAFE = "channel_unsafe"
CHANNEL_CLUTTERED = "channel_cluttered"
STATE_UNSAFE = "state_unsafe"
SECOND_INSTANCE = "second_instance"
SELF_UNKNOWN = "self_unknown"
NOT_COMPOSE = "not_compose"
ENGINE_UNREACHABLE = "engine_unreachable"
ENGINE_UNSUPPORTED = "engine_unsupported"
API_TOO_OLD = "api_too_old"
SETUP_CODES = (
    CHANNEL_UNSAFE, CHANNEL_CLUTTERED, STATE_UNSAFE, SECOND_INSTANCE, SELF_UNKNOWN, NOT_COMPOSE, ENGINE_UNREACHABLE,
    ENGINE_UNSUPPORTED, API_TOO_OLD,
)

# Ziel
NO_TARGET = "no_target"
MULTIPLE_TARGETS = "multiple_targets"
TARGET_NOT_RUNNING = "target_not_running"
TARGET_UNHEALTHY = "target_unhealthy"
NO_HEALTHCHECK = "no_healthcheck"
SWARM = "swarm"
FOREIGN_IMAGE = "foreign_image"
PINNED_VERSION = "pinned_version"
NOT_FROM_REGISTRY = "not_from_registry"
"""Die Engine nennt fuer das laufende Image keinen Digest im Repository. Beim alten Store heisst das: nicht aus der
Registry gezogen; beim containerd-Store nur: das Image hat im Repository keinen Namen (mehr). Umgekehrt beweist ein
Digest beim containerd-Store nicht, dass das Image aus der Registry stammt (siehe `require_registry_digest`)."""
NO_VERSION_LABEL = "no_version_label"
VERSION_TOO_OLD = "version_too_old"
IMAGE_CONFIG_MISSING = "image_config_missing"
AUTO_REMOVE = "auto_remove"
CUSTOM_ENTRYPOINT = "custom_entrypoint"
UNSAFE_TARGET = "unsafe_target"
CHANNEL_MISSING_IN_TARGET = "channel_missing_in_target"
DEPENDENT_CONTAINERS = "dependent_containers"
MACVLAN = "macvlan"
NETWORK_UNCLEAR = "network_unclear"
UNKNOWN_FIELD = "unknown_field"
NAME_TAKEN = "name_taken"
TARGET_CODES = (
    NO_TARGET, MULTIPLE_TARGETS, TARGET_NOT_RUNNING, TARGET_UNHEALTHY, NO_HEALTHCHECK, SWARM, FOREIGN_IMAGE,
    PINNED_VERSION, NOT_FROM_REGISTRY, NO_VERSION_LABEL, VERSION_TOO_OLD, IMAGE_CONFIG_MISSING, AUTO_REMOVE,
    CUSTOM_ENTRYPOINT, UNSAFE_TARGET, CHANNEL_MISSING_IN_TARGET, DEPENDENT_CONTAINERS, MACVLAN, NETWORK_UNCLEAR,
    UNKNOWN_FIELD, NAME_TAKEN,
)

# Anforderung
BAD_REQUEST = "bad_request"
EXPIRED = "expired"
REPLAY = "replay"
BUSY = "busy"
RATE_LIMITED = "rate_limited"
BLOCKED_VERSION = "blocked_version"
NOT_NEWER = "not_newer"
TAG_NOT_ON_VERSION = "tag_not_on_version"
PLATFORM_MISMATCH = "platform_mismatch"
PULL_FAILED = "pull_failed"
NO_PREVIOUS = "no_previous"
PREVIOUS_MISMATCH = "previous_mismatch"
NOT_IMPLEMENTED = "not_implemented"
"""Die Anforderung ist gueltig, diese Helfer-Version fuehrt die Aktion aber nicht aus. Fuer Update und Rueckweg kommt
er nicht vor (beides fuehrt der Helfer aus). Er bleibt im Protokoll, denn Codes kommen nur dazu und fallen nie weg:
eine spaetere Protokoll-Funktion, die ein aelterer Helfer nicht kennt, beantwortet dieser damit."""
REQUEST_CODES = (
    BAD_REQUEST, EXPIRED, REPLAY, BUSY, RATE_LIMITED, BLOCKED_VERSION, NOT_NEWER, TAG_NOT_ON_VERSION,
    PLATFORM_MISMATCH, PULL_FAILED, NO_PREVIOUS, PREVIOUS_MISMATCH, NOT_IMPLEMENTED,
)

# Ablauf
CREATE_FAILED = "create_failed"
CLONE_MISMATCH = "clone_mismatch"
STOP_FAILED = "stop_failed"
START_FAILED = "start_failed"
EXITED = "exited"
RESTART_LOOP = "restart_loop"
RESCUE_PAGE = "rescue_page"
TIMEOUT = "timeout"
EXTERNAL_CHANGE = "external_change"
ROLLBACK_FAILED = "rollback_failed"
FLOW_CODES = (
    CREATE_FAILED, CLONE_MISMATCH, STOP_FAILED, START_FAILED, EXITED, RESTART_LOOP, RESCUE_PAGE, TIMEOUT,
    EXTERNAL_CHANGE, ROLLBACK_FAILED,
)

CODES = frozenset(SETUP_CODES + TARGET_CODES + REQUEST_CODES + FLOW_CODES)

ACTIONS = ("update", "rollback")
STATES = ("idle", "busy", "unsafe", "error")
"""Werte von `state` im Status."""
OUTCOMES = ("applied", "reverted", "rolled_back", "refused", "aborted", "failed_manual", "external_change")
"""Werte von `outcome` in den Ergebnissen."""
STEPS = (
    "begin", "pulled", "protected", "renamed", "tagged", "creating", "created", "old_stopped", "started",
    "committed",
)
"""Schritte des Journals in ihrer Reihenfolge; auch `busy.step` im Status."""


class Refusal(Exception):
    """Ablehnung mit einem festen Code aus `CODES`.

    Der Text der Ausnahme ist nur der Code (und ein optionales `detail`, ebenfalls ein fester Bezeichner nach
    `CODE_RE`) -- nie Inhalt aus dem Kanal oder von der Engine. So kann er ohne Bereinigung ins Protokoll.
    """

    def __init__(self, code: str, detail: str | None = None) -> None:
        if code not in CODES:
            raise ValueError(f"unbekannter Code: {code!r}")
        if detail is not None and not (isinstance(detail, str) and CODE_RE.fullmatch(detail)):
            raise ValueError("detail muss ein fester Bezeichner sein")
        super().__init__(code if detail is None else f"{code}:{detail}")
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# Muster
# ---------------------------------------------------------------------------

_NUM = r"(0|[1-9][0-9]{0,8})"
VERSION_RE = re.compile(rf"{_NUM}\.{_NUM}\.{_NUM}", re.ASCII)
"""Version `X.Y.Z`. Immer mit `fullmatch` benutzen. `[0-9]` statt `\\d`: `\\d` traefe ohne `re.ASCII`
auch Unicode-Ziffern."""
MINOR_TAG_RE = re.compile(rf"{_NUM}\.{_NUM}", re.ASCII)
"""Bewegliches Tag `X.Y`. Wie die Version hoechstens 9 Stellen je Teil."""
UUID4_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", re.ASCII)
"""Anforderungs-ID: UUID4, nur Kleinbuchstaben (eine Schreibweise je ID, sonst liefe der Replay-Schutz ins Leere)."""
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}", re.ASCII)
"""Digest bzw. Image-ID (`sha256:<64 hex>`)."""
CONTAINER_ID_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
"""Volle Container-ID; Kurz-IDs gibt es im Helfer nicht."""
SERVICE_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,62}", re.ASCII)

_MAX_REF_LENGTH = 512
_MAX_VERSION_LENGTH = 32


def _is_int(value: object) -> bool:
    """Echte Ganzzahl: `True`/`False` sind in Python zwar `int`, gelten hier aber nicht."""
    return type(value) is int


def is_container_id(value: object) -> bool:
    return isinstance(value, str) and CONTAINER_ID_RE.fullmatch(value) is not None


def is_digest(value: object) -> bool:
    """`sha256:<64 hex>` -- gilt fuer Digests und Image-IDs."""
    return isinstance(value, str) and DIGEST_RE.fullmatch(value) is not None


is_image_id = is_digest


def is_request_id(value: object) -> bool:
    return isinstance(value, str) and UUID4_RE.fullmatch(value) is not None


def is_code(value: object) -> bool:
    return isinstance(value, str) and value in CODES


RESULT_KEYS = ("id", "action", "from", "to", "outcome", "code", "finished_at")
"""Ein Eintrag in `status.results` (und in `state.json`, damit er einen Neustart des Helfers uebersteht)."""


def is_result(entry: object) -> bool:
    """Ein gueltiger Eintrag fuer `status.results`: genau die sieben Schluessel, nur feste Werte (dieselben Regeln wie
    `channel.validate_status`)."""
    if not isinstance(entry, dict) or set(entry) != set(RESULT_KEYS):
        return False
    return (is_request_id(entry["id"]) and isinstance(entry["action"], str) and entry["action"] in ACTIONS
            and (entry["from"] is None or is_version(entry["from"]))
            and (entry["to"] is None or is_version(entry["to"]))
            and isinstance(entry["outcome"], str) and entry["outcome"] in OUTCOMES
            and (entry["code"] is None or is_code(entry["code"]))
            and _is_int(entry["finished_at"]) and entry["finished_at"] >= 0)


# ---------------------------------------------------------------------------
# Versionen
# ---------------------------------------------------------------------------


def parse_version(text: object) -> tuple[int, int, int] | None:
    """`(X, Y, Z)` oder `None`, wenn `text` keine Version im Sinne dieses Moduls ist (auch fuer Nicht-Texte)."""
    if not isinstance(text, str) or len(text) > _MAX_VERSION_LENGTH:
        return None
    match = VERSION_RE.fullmatch(text)
    if match is None:
        return None
    return int(match[1]), int(match[2]), int(match[3])


def is_version(text: object) -> bool:
    return parse_version(text) is not None


def is_newer(candidate: object, current: object) -> bool:
    """`candidate` ist groesser als `current`. Ist eins von beiden keine Version: nie neuer."""
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


def image_version(labels: object) -> str:
    """Version aus den Labels eines **Images** (`Config.Labels`). Fehlt sie oder ist sie keine Version
    (auch eine Vorabversion): `no_version_label`."""
    if not isinstance(labels, Mapping):
        raise Refusal(NO_VERSION_LABEL)
    value = labels.get(VERSION_LABEL)
    if not is_version(value):
        raise Refusal(NO_VERSION_LABEL)
    return value  # type: ignore[return-value]


def check_target_version(current: str) -> None:
    """Die laufende Version des Ziels ist mindestens `MIN_VERSION` (sonst `version_too_old`)."""
    version = parse_version(current)
    if version is None:
        raise Refusal(NO_VERSION_LABEL)
    if version < parse_version(MIN_VERSION):  # type: ignore[operator]
        raise Refusal(VERSION_TOO_OLD)


def check_newer(requested: str, current: str) -> None:
    """Ein Update geht nur nach vorn: `requested > current`, sonst `not_newer`."""
    if not is_newer(requested, current):
        raise Refusal(NOT_NEWER)


def check_label(label_version: str, requested: str) -> None:
    """Nach dem Pull: das Label im gezogenen Image ist **genau** die angeforderte Version. Sonst zeigt das
    bewegliche Tag auf etwas anderes (umgehaengt, Backport, zu frueh gefragt) -> `tag_not_on_version`."""
    if not is_version(label_version) or label_version != requested:
        raise Refusal(TAG_NOT_ON_VERSION)


# ---------------------------------------------------------------------------
# Image-Referenz und Tag
# ---------------------------------------------------------------------------


def floating_tag(image: object, *, repository: str = REPOSITORY) -> str:
    """Das bewegliche Tag aus `Config.Image` des Ziels: `latest` oder `X.Y`.

    * genau `repository` ohne Tag -> `latest`
    * `repository:latest` / `repository:X.Y` -> das Tag
    * `repository:X.Y.Z`, `repository@sha256:...`, `repository:<tag>@sha256:...` -> `pinned_version`
    * alles andere (anderes Repository, Grossschreibung, `ghcr.io:443/...`, `docker.io/...`, `deck-evil`,
      `deck/sub`, Vorabversion, Leerzeichen, Steuerzeichen, Nicht-Text) -> `foreign_image`

    Der Text wird nie normalisiert (kein `strip`, kein `lower`): was nicht exakt passt, ist fremd (fail-closed).
    """
    if not isinstance(image, str) or not image or len(image) > _MAX_REF_LENGTH:
        raise Refusal(FOREIGN_IMAGE)
    if image == repository:
        return "latest"
    if image.startswith(repository + "@"):
        if DIGEST_RE.fullmatch(image[len(repository) + 1:]):
            raise Refusal(PINNED_VERSION)
        raise Refusal(FOREIGN_IMAGE)
    if not image.startswith(repository + ":"):
        raise Refusal(FOREIGN_IMAGE)
    tag, at, digest = image[len(repository) + 1:].partition("@")
    is_full_version = VERSION_RE.fullmatch(tag) is not None
    if not (tag == "latest" or MINOR_TAG_RE.fullmatch(tag) or is_full_version):
        raise Refusal(FOREIGN_IMAGE)
    if at:
        if DIGEST_RE.fullmatch(digest):
            raise Refusal(PINNED_VERSION)
        raise Refusal(FOREIGN_IMAGE)
    if is_full_version:
        raise Refusal(PINNED_VERSION)
    return tag


def tag_fits_version(tag: str, version: str) -> bool:
    """Kann das bewegliche Tag ueberhaupt auf `version` zeigen? `latest` immer, `X.Y` nur bei `X.Y.*`."""
    parsed = parse_version(version)
    if parsed is None:
        return False
    if tag == "latest":
        return True
    match = MINOR_TAG_RE.fullmatch(tag) if isinstance(tag, str) else None
    return match is not None and (int(match[1]), int(match[2])) == parsed[:2]


def check_tag_fits(tag: str, version: str) -> None:
    """Vorab (ohne Registry): ein Ziel auf `0.7` bekommt nie `0.8.0` -> `tag_not_on_version`."""
    if not tag_fits_version(tag, version):
        raise Refusal(TAG_NOT_ON_VERSION)


def registry_digests(repo_digests: object, *, repository: str = REPOSITORY) -> list[str]:
    """Die Eintraege `repository@sha256:<hex>` aus `RepoDigests` eines Images (sortiert, ohne Doppelte).
    Andere Repositories und kaputte Eintraege fallen weg."""
    if not isinstance(repo_digests, list):
        return []
    prefix = repository + "@"
    found = {
        entry for entry in repo_digests
        if isinstance(entry, str) and entry.startswith(prefix) and DIGEST_RE.fullmatch(entry[len(prefix):])
    }
    return sorted(found)


def require_registry_digest(repo_digests: object, *, repository: str = REPOSITORY) -> list[str]:
    """Die Engine nennt fuer das laufende Image einen Digest im Repository: mindestens ein `RepoDigests`-Eintrag
    `repository@sha256:...`, sonst `not_from_registry`. Den braucht der Rueckweg-Slot (`Slot.repo_digest`).

    Ob das Image damit aus der Registry stammt, haengt vom Image-Store des Docker-Dienstes ab:

    * **Alter Store** (Graphdriver, etwa overlay2): `RepoDigests` schreibt nur ein Pull (oder Push). Ein selbst gebautes
      und nur als `repository` getaggtes Image hat keinen Eintrag -> `not_from_registry`.
    * **containerd-Store** (Standard einer neuen Installation ab Docker 29): `RepoDigests` wird aus den Namen des Images
      abgeleitet, je Name `<repository>@<Digest des Images>`. Auch ein selbst gebautes oder aus einem anderen Repository
      umgetaggtes Image hat dann einen Eintrag, nur kennt die Registry diesen Digest nicht. Das erkennt die Pruefung
      **nicht**: Unterscheiden koennte es die Registry, die wertet die Vorpruefung aber nicht aus (sie fragt sie
      nie).
      `not_from_registry` heisst hier nur: das Image hat keinen Namen im Repository (mehr), etwa weil
      `docker compose pull` das bewegliche Tag schon auf ein neueres Image gelegt hat.

    Was beim containerd-Store daraus folgt, ist abgesichert: Ein Update zieht das offizielle Image nach dem Digest, den
    die Registry fuer das bewegliche Tag nennt, prueft es wie immer und ersetzt das selbst gebaute. Der Rueckweg-Slot
    traegt danach einen Digest, den nur dieser Rechner kennt. Solange das alte Image noch da ist (Schutz-Tag), braucht
    der Rueckweg die Registry nicht; nach `docker image prune -a` scheitert das Ziehen nach diesem Digest
    (`pull_failed`, nichts veraendert, der Slot bleibt)."""
    found = registry_digests(repo_digests, repository=repository)
    if not found:
        raise Refusal(NOT_FROM_REGISTRY)
    return found


# ---------------------------------------------------------------------------
# Strenges JSON
# ---------------------------------------------------------------------------


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("doppelter Schluessel")
        result[key] = value
    return result


def _no_constant(_name: str) -> Any:
    raise ValueError("NaN/Infinity sind nicht erlaubt")


def _finite_float(text: str) -> float:
    """`parse_float`: `1e999` ist nach der JSON-Grammatik eine Zahl, der Standard-Parser macht daraus aber still
    `inf`. Das gilt hier wie `Infinity`: abgelehnt."""
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("Zahl ausserhalb des Bereichs (wuerde Unendlich)")
    return value


def loads_strict(data: bytes, *, max_bytes: int) -> Any:
    """JSON aus `data` (UTF-8, hoechstens `max_bytes`): doppelte Schluessel, `NaN`/`Infinity`/`-Infinity` und Zahlen,
    die beim Lesen zu Unendlich ueberlaufen (`1e999`), werden abgelehnt. (Die Leser verlangen ohnehin Ganzzahlen;
    der Parser selbst liefert aber nie `inf` oder `nan`.)

    Wirft **nur** `ValueError` mit festem Text -- auch fuer `RecursionError` (tiefe Verschachtelung),
    `MemoryError` und alles andere. Der Inhalt landet nie in der Meldung (ein `JSONDecodeError` traegt das
    ganze Dokument mit sich, darum `from None`).
    """
    if not isinstance(data, (bytes, bytearray)) or len(data) > max_bytes:
        raise ValueError("zu gross oder kein Bytes-Wert")
    try:
        text = bytes(data).decode("utf-8")
        return json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constant,
                          parse_float=_finite_float)
    except Exception:  # noqa: BLE001 - jede Ausnahme, auch RecursionError/MemoryError, ist "unlesbar"
        raise ValueError("kein gueltiges JSON") from None


def dumps(obj: Any) -> bytes:
    """Kompaktes, sortiertes ASCII-JSON (fuer Status und Zustand)."""
    return json.dumps(obj, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


# ---------------------------------------------------------------------------
# Anforderung
# ---------------------------------------------------------------------------

REQUEST_KEYS = frozenset({"v", "id", "action", "version", "created_at"})


@dataclass(frozen=True)
class Request:
    """Eine gepruefte Anforderung aus `requests/<id>.json`."""

    v: int
    id: str
    action: str
    version: str
    created_at: int


def _request_from(raw: bytes, file_id: str | None) -> Request:
    obj = loads_strict(raw, max_bytes=REQUEST_MAX_BYTES)
    if not isinstance(obj, dict) or set(obj) != REQUEST_KEYS:
        raise Refusal(BAD_REQUEST)
    v, rid, action, version, created_at = (obj[k] for k in ("v", "id", "action", "version", "created_at"))
    if not _is_int(v) or v not in REQUEST_VERSIONS:
        raise Refusal(BAD_REQUEST)
    if not is_request_id(rid) or (file_id is not None and rid != file_id):
        raise Refusal(BAD_REQUEST)
    if not isinstance(action, str) or action not in ACTIONS:
        raise Refusal(BAD_REQUEST)
    if not is_version(version):
        raise Refusal(BAD_REQUEST)
    if not _is_int(created_at):
        raise Refusal(BAD_REQUEST)
    return Request(v=v, id=rid, action=action, version=version, created_at=created_at)


def parse_request(raw: object, *, now: float, file_id: str | None = None) -> Request:
    """Prueft eine Anforderung streng und gibt sie zurueck, sonst `Refusal`.

    * `bad_request`: kein UTF-8-JSON-Objekt bis 4096 Byte, nicht genau die fuenf Schluessel, doppelter
      Schluessel, falscher Typ (`true` ist keine Zahl, `1.0` auch nicht), `v != 1`, `id` keine UUID4 (oder nicht
      gleich dem Dateinamen `file_id`), unbekannte `action`, ungueltige `version`, `created_at` mehr als 60 s in
      der Zukunft -- und **jede** andere Ausnahme beim Lesen (auch `RecursionError`).
    * `expired`: `created_at` mehr als 600 s alt.

    Die Ausnahme gilt nur fuer diese eine Anforderung; der Aufrufer macht mit der naechsten weiter.
    """
    try:
        request = _request_from(raw, file_id)  # type: ignore[arg-type]
    except Refusal:
        raise
    except Exception:  # noqa: BLE001 - auch RecursionError/MemoryError: nur diese Anforderung ist kaputt
        raise Refusal(BAD_REQUEST) from None
    now_s = int(now)
    if request.created_at > now_s + REQUEST_MAX_FUTURE_S:
        raise Refusal(BAD_REQUEST)
    if request.created_at < now_s - REQUEST_MAX_AGE_S:
        raise Refusal(EXPIRED)
    return request


# ---------------------------------------------------------------------------
# Grenzen
# ---------------------------------------------------------------------------


def check_limits(
    request: Request,
    *,
    now: float,
    actions: Iterable[int],
    blocked: Mapping[str, int],
    seen: Mapping[str, int],
    hold_until: int | None = None,
) -> None:
    """Grenzen aus dem eigenen Zustand (`/state`), in dieser Reihenfolge:

    1. `replay`: die ID wurde in der letzten Stunde schon verarbeitet.
    2. `state_unsafe`: der Zustand war kaputt und ist noch gesperrt (`hold_until`, siehe `state.py`).
    3. `rate_limited`: in den letzten 10 Minuten wurde schon eine Aktion angenommen.
    4. `blocked_version`: Update auf eine Version, von der aus in den letzten 24 h zurueckgegangen wurde.

    Zeitstempel in der Zukunft (Uhr zurueckgesprungen) zaehlen als "gerade eben" -- also sperrend. Hier werden sie
    nur so gewertet, nicht verkuerzt: `state.State.prune()` (von `StateStore.check` und `StateStore.load_state` aufgerufen)
    kappt sie vorher auf die Hoechstfrist, damit ein Uhrsprung nicht fuer Jahre sperrt.
    """
    now_s = int(now)
    seen_at = seen.get(request.id)
    if seen_at is not None and now_s - seen_at < SEEN_TTL_S:
        raise Refusal(REPLAY)
    if hold_until is not None and now_s < hold_until:
        raise Refusal(STATE_UNSAFE)
    if any(now_s - at < ACTION_INTERVAL_S for at in actions):
        raise Refusal(RATE_LIMITED)
    if request.action == "update" and blocked.get(request.version, 0) > now_s:
        raise Refusal(BLOCKED_VERSION)


# ---------------------------------------------------------------------------
# Rueckweg-Slot
# ---------------------------------------------------------------------------

SLOT_KEYS = ("from_version", "image_id", "repo_digest", "installed_container_id", "installed_image_id", "until")


@dataclass(frozen=True)
class Slot:
    """Der eine Rueckweg nach einem gelungenen Update.

    * `from_version`, `image_id`, `repo_digest`: die Version davor, ihr Image und dessen Digest im Repository, wie die
      Engine ihn vor dem Update nannte (zum erneuten Ziehen nach `image prune -a`). Beim containerd-Store kennt die
      Registry ihn nicht, wenn das Image selbst gebaut war; dann geht der Rueckweg nur, solange es noch da ist (siehe
      `require_registry_digest`).
    * `installed_container_id`, `installed_image_id`: der Container, den der Helfer dabei angelegt hat.
    * `until`: bis dahin gilt der Slot (7 Tage).
    """

    from_version: str
    image_id: str
    repo_digest: str
    installed_container_id: str
    installed_image_id: str
    until: int

    def to_json(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in SLOT_KEYS}

    @classmethod
    def from_json(cls, obj: object, *, repository: str = REPOSITORY) -> Slot:
        """Streng: genau die sechs Schluessel mit gueltigen Werten, sonst `ValueError`."""
        if not isinstance(obj, dict) or set(obj) != set(SLOT_KEYS):
            raise ValueError("slot: Schluessel")
        slot = cls(**obj)
        slot.validate(repository=repository)
        return slot

    def validate(self, *, repository: str = REPOSITORY) -> None:
        if not is_version(self.from_version):
            raise ValueError("slot: from_version")
        if not is_image_id(self.image_id) or not is_image_id(self.installed_image_id):
            raise ValueError("slot: image_id")
        if registry_digests([self.repo_digest], repository=repository) != [self.repo_digest]:
            raise ValueError("slot: repo_digest")
        if not is_container_id(self.installed_container_id):
            raise ValueError("slot: installed_container_id")
        if not _is_int(self.until) or self.until < 0:
            raise ValueError("slot: until")


def new_slot(
    *,
    from_version: str,
    image_id: str,
    repo_digest: str,
    installed_container_id: str,
    installed_image_id: str,
    now: float,
    repository: str = REPOSITORY,
) -> Slot:
    """Slot nach dem Abschluss eines **Updates**. Ein Rueckweg legt nie einen an (nie verkettet,
    siehe `state.State.commit_rollback`)."""
    slot = Slot(
        from_version=from_version,
        image_id=image_id,
        repo_digest=repo_digest,
        installed_container_id=installed_container_id,
        installed_image_id=installed_image_id,
        until=int(now) + SLOT_TTL_S,
    )
    slot.validate(repository=repository)
    return slot


def check_rollback(
    request: Request,
    slot: Slot | None,
    *,
    target_container_id: str,
    target_image_id: str,
    now: float,
) -> Slot:
    """Darf diese Anforderung `rollback` den Slot benutzen? Gibt den Slot zurueck, sonst `Refusal`:

    * `no_previous`: kein Slot, abgelaufen, oder `until` unplausibel weit in der Zukunft.
    * `previous_mismatch`: das Ziel ist nicht **genau** der installierte Container mit dem installierten Image,
      oder die Anforderung nennt nicht genau `from_version`.
    * `version_too_old`: der Vorgaenger liegt unter `MIN_VERSION`.
    """
    if request.action != "rollback":
        raise ValueError("check_rollback nur fuer rollback")
    now_s = int(now)
    if slot is None or now_s >= slot.until or slot.until > now_s + SLOT_TTL_S + CLOCK_SLACK_S:
        raise Refusal(NO_PREVIOUS)
    if target_container_id != slot.installed_container_id or target_image_id != slot.installed_image_id:
        raise Refusal(PREVIOUS_MISMATCH)
    if request.version != slot.from_version:
        raise Refusal(PREVIOUS_MISMATCH)
    if parse_version(slot.from_version) < parse_version(MIN_VERSION):  # type: ignore[operator]
        raise Refusal(VERSION_TOO_OLD)
    return slot


# ---------------------------------------------------------------------------
# Einstellung aus der Umgebung
# ---------------------------------------------------------------------------


def service_name(value: object) -> str:
    """Name des Compose-Dienstes aus dem Wert von `NODVARD_DECK_UPDATER_SERVICE` (leer/fehlend = Vorgabe).
    Ungueltig -> `ValueError` (der Helfer startet dann nicht)."""
    if value is None or value == "":
        return DEFAULT_SERVICE
    if not isinstance(value, str) or not SERVICE_RE.fullmatch(value):
        raise ValueError("ungueltiger Dienstname")
    return value
