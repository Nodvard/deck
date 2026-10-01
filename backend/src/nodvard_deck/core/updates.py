"""Nach Updates suchen: welche Version von Nodvard Deck ist die neueste?

Einzige Quelle ist die Container-Registry von GitHub (ghcr.io), es gibt keinen eigenen Dienst. Ablauf:

1. anonymes Token holen (`GET https://ghcr.io/token?scope=repository:<REPOSITORY>:pull&service=ghcr.io`),
   das klappt nur, solange das Paket oeffentlich ist;
2. Tags lesen (`GET /v2/<REPOSITORY>/tags/list?n=100`), weitere Seiten ueber den `Link`-Kopf, hoechstens
   `MAX_PAGES` Seiten. Der Token geht nur an ghcr.io: eine Folgeseite auf einem anderen Host oder Pfad gilt
   als Fehler;
3. nur Tags der Form `1.2.3` zaehlen, im Kanal "beta" auch Vorabversionen (`1.2.3-rc1`). Das hoechste nach
   Semver wird mit der laufenden Version `current` verglichen (`running_version`: beim offiziellen Image
   dessen genaue Version, sonst `version.__version__`);
4. optional der Digest der neuesten Version: das Manifest (OCI-Index) wird geladen und selbst gehasht
   (sha256 ueber die Bytes, genau so ist ein Digest definiert). Klappt das nicht oder reicht die Zeit nicht,
   bleibt er leer, die Pruefung gilt trotzdem.

Jede Anfrage hat hoechstens `TIMEOUT_S` Sekunden (je Schritt), Token und Tags zusammen hoechstens
`TOTAL_TIMEOUT_S`; der Digest bekommt nur die Zeit, die davon noch uebrig ist. Jede Antwort wird ungepackt
angefordert (`Accept-Encoding: identity`, eine gepackte wird verworfen) und hoechstens `MAX_BODY_BYTES` gross
gelesen: Eine zu grosse `Content-Length` bricht vor dem Lesen ab, sonst wird beim Lesen mitgezaehlt.

Das Ergebnis liegt im Datenordner (`update_check.json`, atomar geschrieben). Es laeuft immer nur eine Pruefung
zur Zeit (taeglicher Job und Knopf "Jetzt suchen" warten aufeinander), damit keine die andere ueberschreibt.
Scheitert eine Pruefung (offline, 401, 429, 5xx, kaputtes JSON, Antwort ohne Tag-Liste), bleibt der letzte gute
Stand stehen, dazu `error` mit `OFFLINE_TEXT`; nach aussen gibt es dabei nie eine Ausnahme. Der Cache gehoert
zu einem Kanal: Stand, Zeitpunkte und Fehler eines anderen Kanals gelten nicht.

Antwortet die Registry vollstaendig, aber ohne passende Version (Kanal "stable" und bisher nur Vorabversionen,
oder noch gar keine Tags), ist das ein gueltiges Ergebnis: `latest` ist dann `null`, `checked_at` gesetzt (die
Oberflaeche sagt "keine gefunden" statt "noch nicht geprueft"). Ein frueher gefundener Stand wird dabei bewusst
ersetzt: Die Registry hat gerade gesagt, dass es ihn nicht (mehr) gibt.

Datenschutz: Bei jeder Pruefung sieht ghcr.io (GitHub) die IP-Adresse und den Zeitpunkt. Sonst geht nichts mit,
auch nicht die installierte Version (`USER_AGENT` ohne Versionsnummer). Der taegliche Kern-Job ist abschaltbar
(`services.update_check`).

`official_image` sagt, ob der Container aus dem offiziellen Image stammt: Das Release-Image traegt die Datei
`/app/image-info.json` mit `"image": "<OFFICIAL_IMAGE>"` (`nodvard_deck.image_info`, Build-Argument `IMAGE` in
`deploy/Dockerfile`). Ein selbst gebautes Image (z. B. ueber `scripts/deploy_pi.sh`) hat sie nicht; die
Oberflaeche zeigt dann den passenden Weg.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import stat
import tempfile
import time
import weakref
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .. import image_info
from ..version import __version__

logger = logging.getLogger("nodvard_deck.updates")

REPOSITORY = "nodvard/deck"
"""Das offizielle Repository -- die EINZIGE Stelle im Kern. Image `ghcr.io/<REPOSITORY>`, Quelltext und
Release-Notizen unter `github.com/<REPOSITORY>`."""

REGISTRY_HOST = "ghcr.io"
OFFICIAL_IMAGE = f"{REGISTRY_HOST}/{REPOSITORY}"
# Was sich geaendert hat: CHANGELOG.md im Stand des Tags (release.yml legt kein GitHub-Release an, das Tag gibt es immer).
RELEASE_NOTES_URL = f"https://github.com/{REPOSITORY}/blob/v{{version}}/CHANGELOG.md"

CHANNELS = ("stable", "beta")
DEFAULT_CHANNEL = "stable"

TIMEOUT_S = 5.0
"""Hoechstdauer je Anfrage."""
TOTAL_TIMEOUT_S = 15.0
"""Hoechstdauer der ganzen Pruefung: Token und alle Seiten muessen darin fertig sein, das Manifest (Digest)
bekommt nur den Rest."""
PAGE_SIZE = 100
MAX_PAGES = 10
MAX_BODY_BYTES = 1024 * 1024
"""Groesste Antwort der Registry, die gelesen wird (Token, eine Tag-Seite, Manifest)."""
MAX_TAG_LENGTH = 64

CACHE_NAME = "update_check.json"
STALE_TMP_S = 3600
"""Eine liegengebliebene Zwischendatei des Caches gilt nach dieser Zeit als Rest und wird geloescht."""
MAX_CACHE_BYTES = 64 * 1024

OFFLINE_TEXT = "Konnte nicht prüfen (offline?)."

USER_AGENT = "nodvard-deck"
"""Bewusst ohne Versionsnummer: ghcr.io soll nicht erfahren, welche Version hier laeuft. Nicht weglassen, sonst
schickt httpx seinen eigenen Namen samt Version."""

_ACCEPT_MANIFEST = "application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json"
_STABLE_RE = re.compile(r"(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})")
_PRE_RE = re.compile(r"(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)")
_PRE_PART_RE = re.compile(r"([A-Za-z-]*?)([0-9]*)")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_LENGTH_RE = re.compile(r"[0-9]{1,15}")


class CheckFailed(Exception):
    """Die Pruefung hat kein verlaessliches Ergebnis gebracht (Grund nur fuer das Protokoll)."""


# ---------------------------------------------------------------------------
# Versionen
# ---------------------------------------------------------------------------


def _version_key(text: str, *, allow_pre: bool = True) -> tuple | None:
    """Sortierschluessel nach Semver oder `None`, wenn `text` keine Version ist. Eine Vorabversion liegt
    vor ihrer fertigen Version; Teile aus Ziffern zaehlen als Zahl und liegen vor Teilen mit Buchstaben.
    Abweichend von Semver werden gemischte Teile wie `rc10` natuerlich sortiert (Buchstaben und Zahl getrennt):
    `rc1 < rc2 < rc9 < rc10`, sonst bekaeme ein Beta-Nutzer auf `rc10` die aeltere `rc9` als Update angeboten."""
    if not isinstance(text, str) or len(text) > MAX_TAG_LENGTH:
        return None
    match = _STABLE_RE.fullmatch(text)
    if match:
        return (int(match[1]), int(match[2]), int(match[3]), 1, ())
    if not allow_pre:
        return None
    match = _PRE_RE.fullmatch(text)
    if not match:
        return None
    parts = tuple(_pre_part_key(p) for p in match[4].split("."))
    return (int(match[1]), int(match[2]), int(match[3]), 0, parts)


def _pre_part_key(part: str) -> tuple[int, int, str, int]:
    """Schluessel eines Teils der Vorabversion: reine Zahl vor allem mit Buchstaben; `rc10` wird zu
    ("rc", 10), ein Teil ohne Zahl (`rc`, `beta`) liegt vor demselben Wort mit Zahl (`rc` vor `rc1`)."""
    if part.isdigit():
        return (0, int(part), "", 0)
    match = _PRE_PART_RE.fullmatch(part)
    if match and match[2]:
        return (1, 0, match[1], int(match[2]))
    return (1, 0, part, -1)


def is_version(text: Any) -> bool:
    """`text` ist eine Version im Sinne dieses Moduls (`1.2.3` oder `1.2.3-rc1`)."""
    return _version_key(text) is not None


def is_newer(candidate: str, current: str) -> bool:
    """`candidate` ist nach Semver groesser als `current`. Unbekannte Formate sind nie neuer."""
    a, b = _version_key(candidate), _version_key(current)
    return a is not None and b is not None and a > b


def pick_latest(tags: list[Any], channel: str) -> str | None:
    """Hoechste Version unter den Tags; Vorabversionen nur im Kanal "beta"."""
    allow_pre = channel == "beta"
    best: tuple[tuple, str] | None = None
    for tag in tags:
        key = _version_key(tag, allow_pre=allow_pre) if isinstance(tag, str) else None
        if key is not None and (best is None or key > best[0]):
            best = (key, tag)
    return best[1] if best else None


def is_official_image(image: str | None) -> bool:
    return bool(image) and image.strip() == OFFICIAL_IMAGE


def running_version(settings: Any, fallback: str | None = None) -> str:
    """Die genaue Version, die in diesem Container laeuft -- fuer den Vergleich der Update-Suche und fuer die
    Kopien vor Updates (`boot`).

    Laeuft das offizielle Image, gilt dessen Version aus der Datei im Image (`image_info`, `settings.image_info_path`),
    sonst `settings.build`, sofern sie eine Version im Sinne dieses Moduls ist -- auch eine Vorabversion wie
    `0.6.0-rc1`, die `version.__version__` nie sein kann. Die Datei geht vor der Variable `NODVARD_DECK_BUILD`: Sie
    kommt immer aus dem Image, das gerade laeuft, die Variable koennte aus einem alten Container stammen. Sonst
    (selbst gebautes Image, eine Kennung wie ein Commit, nichts gesetzt) gilt `fallback`, sonst `version.__version__`.
    `settings` ist `config.Settings` (Felder `image`, `build`, `image_info_path`)."""
    if is_official_image(getattr(settings, "image", None)):
        path = getattr(settings, "image_info_path", image_info.PATH)
        for candidate in (image_info.read(path).get("version"), getattr(settings, "build", None)):
            candidate = (candidate or "").strip()
            if is_version(candidate):
                return candidate
    return fallback or __version__


def normalize_channel(channel: str | None) -> str:
    return channel if channel in CHANNELS else DEFAULT_CHANNEL


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def _make_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(TIMEOUT_S),
        follow_redirects=False,
        transport=transport,
        # Ungepackt: Eine gepackte Antwort koennte beim Entpacken weit ueber MAX_BODY_BYTES wachsen.
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
    )


async def _get_limited(client: httpx.AsyncClient, url: str, **kwargs: Any) -> tuple[bytes, httpx.Response]:
    """GET mit Groessengrenze: nur Status 200, nur ungepackt (`Content-Encoding` fehlt oder `identity`), eine
    `Content-Length` ueber `MAX_BODY_BYTES` bricht vor dem Lesen ab, sonst wird beim Lesen mitgezaehlt und beim
    ersten Byte zu viel abgebrochen. Nie liegt mehr als `MAX_BODY_BYTES` (plus ein Netzblock) im Speicher."""
    async with client.stream("GET", url, **kwargs) as response:
        if response.status_code != 200:
            raise CheckFailed(f"HTTP {response.status_code} von {httpx.URL(url).path}")
        encoding = response.headers.get("content-encoding", "").strip().lower()
        if encoding not in ("", "identity"):
            raise CheckFailed("gepackte Antwort")
        length = response.headers.get("content-length")
        if length is not None and (not _LENGTH_RE.fullmatch(length.strip()) or int(length) > MAX_BODY_BYTES):
            raise CheckFailed("Antwort zu gross")
        body = bytearray()
        async for chunk in response.aiter_bytes():  # ungepackt (oben geprueft), also gleich den Rohdaten
            body += chunk
            if len(body) > MAX_BODY_BYTES:
                raise CheckFailed("Antwort zu gross")
    return bytes(body), response


async def _get_json(client: httpx.AsyncClient, url: str, **kwargs: Any) -> tuple[Any, httpx.Response]:
    body, response = await _get_limited(client, url, **kwargs)
    try:
        return json.loads(body), response
    except ValueError as exc:
        raise CheckFailed("kein JSON") from exc


async def _token(client: httpx.AsyncClient) -> str:
    body, _ = await _get_json(
        client, f"https://{REGISTRY_HOST}/token",
        params={"scope": f"repository:{REPOSITORY}:pull", "service": REGISTRY_HOST},
    )
    token = body.get("token") or body.get("access_token") if isinstance(body, dict) else None
    if not isinstance(token, str) or not token:
        raise CheckFailed("kein Token")
    return token


def _next_page(response: httpx.Response) -> str | None:
    """Adresse der naechsten Seite aus dem `Link`-Kopf -- nur auf demselben Host und Pfad."""
    link = response.links.get("next", {}).get("url")
    if not link:
        return None
    url = response.url.join(link)
    if url.scheme != "https" or url.host != REGISTRY_HOST or url.path != f"/v2/{REPOSITORY}/tags/list":
        raise CheckFailed("Folgeseite zeigt woanders hin")
    return str(url)


async def _tags(client: httpx.AsyncClient, token: str) -> list[str]:
    headers = {"Authorization": f"Bearer {token}"}
    url: str | None = f"https://{REGISTRY_HOST}/v2/{REPOSITORY}/tags/list?n={PAGE_SIZE}"
    found: list[str] = []
    for _ in range(MAX_PAGES):
        if url is None:
            return found
        body, response = await _get_json(client, url, headers=headers)
        if not isinstance(body, dict) or "tags" not in body:
            raise CheckFailed("Tag-Liste fehlt")
        tags = body["tags"] if body["tags"] is not None else []  # `null`: noch keine Tags
        if not isinstance(tags, list):
            raise CheckFailed("Tag-Liste kaputt")
        found.extend(t for t in tags if isinstance(t, str))
        url = _next_page(response)
    if url is not None:
        logger.warning("update_check_page_limit pages=%s", MAX_PAGES)
    return found


async def _digest(client: httpx.AsyncClient, token: str, tag: str) -> str | None:
    """sha256 ueber das Manifest der Version `tag` (OCI-Index) -- `None`, wenn das nicht klappt."""
    try:
        body, _ = await _get_limited(
            client, f"https://{REGISTRY_HOST}/v2/{REPOSITORY}/manifests/{tag}",
            headers={"Authorization": f"Bearer {token}", "Accept": _ACCEPT_MANIFEST},
        )
    except (CheckFailed, httpx.HTTPError):
        return None
    return "sha256:" + hashlib.sha256(body).hexdigest() if body else None


async def _find_latest(client: httpx.AsyncClient, channel: str) -> tuple[str, str | None]:
    token = await _token(client)
    return token, pick_latest(await _tags(client, token), channel)


async def fetch_latest(channel: str, *, transport: httpx.AsyncBaseTransport | None = None) -> tuple[str | None, str | None]:
    """Neueste Version im Kanal und ihr Digest. Wirft `CheckFailed` (auch bei Netz- und Zeitfehlern), wenn Token
    oder Tags nicht innerhalb von `TOTAL_TIMEOUT_S` kommen. Der Digest ist nur eine Zugabe: Er bekommt die Zeit,
    die danach noch uebrig ist; reicht sie nicht (oder scheitert er), bleibt er `None`, das Ergebnis gilt trotzdem."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + TOTAL_TIMEOUT_S
    async with _make_client(transport) as client:
        try:
            token, latest = await asyncio.wait_for(_find_latest(client, channel), TOTAL_TIMEOUT_S)
        except TimeoutError as exc:
            raise CheckFailed("Zeitlimit") from exc
        except httpx.HTTPError as exc:
            raise CheckFailed(type(exc).__name__) from exc
        digest = None
        if latest:
            try:
                digest = await asyncio.wait_for(_digest(client, token, latest), max(0.0, deadline - loop.time()))
            except TimeoutError:
                logger.info("update_check_digest_skipped reason=Zeitlimit")
    return latest, digest


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_cache(data_dir: Path) -> dict[str, Any]:
    """Inhalt von `update_check.json` oder `{}` (fehlt, zu gross, kaputt)."""
    path = Path(data_dir) / CACHE_NAME
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_CACHE_BYTES + 1)
        if len(raw) > MAX_CACHE_BYTES:
            return {}
        data = json.loads(raw)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_cache(data_dir: Path, data: dict[str, Any]) -> None:
    """Schreibt den Cache ganz oder gar nicht: eigene Zwischendatei im selben Ordner (`mkstemp`, exklusiv
    angelegt, kein fester Name), dann `os.replace`. Zwei Schreiber kommen sich so nie in die Quere. Bleibt nach
    einem harten Abbruch (SIGKILL, Stromausfall) eine Zwischendatei liegen, raeumt der naechste Schreibvorgang
    sie weg, sobald sie aelter als `STALE_TMP_S` ist."""
    path = Path(data_dir) / CACHE_NAME
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{CACHE_NAME}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    _sweep_stale_tmp(path)


def _sweep_stale_tmp(path: Path) -> None:
    """Loescht alte Zwischendateien (`.update_check.json.*.tmp`, normale Dateien, aelter als `STALE_TMP_S`) neben
    `path`. Links werden nie verfolgt; jeder Fehler wird ignoriert, das Speichern ist da schon gelungen."""
    prefix = f".{CACHE_NAME}."
    limit = time.time() - STALE_TMP_S
    with contextlib.suppress(OSError):
        for entry in os.scandir(path.parent):
            if not (entry.name.startswith(prefix) and entry.name.endswith(".tmp")):
                continue
            with contextlib.suppress(OSError):
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISREG(info.st_mode) and info.st_mtime < limit:
                    os.unlink(entry.path)


_lock_slot: tuple[weakref.ReferenceType[asyncio.AbstractEventLoop], asyncio.Lock] | None = None


def _check_lock() -> asyncio.Lock:
    """Die Sperre fuer den laufenden Event-Loop. Im Betrieb gibt es nur einen; wechselt der Loop (Tests), gibt
    es eine neue. Es bleibt immer hoechstens ein Platz belegt, und der haelt den Loop nur schwach."""
    global _lock_slot
    loop = asyncio.get_running_loop()
    if _lock_slot is None or _lock_slot[0]() is not loop:
        _lock_slot = (weakref.ref(loop), asyncio.Lock())
    return _lock_slot[1]


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _result(cache: dict[str, Any], *, channel: str, image: str | None, current: str, source: str) -> dict[str, Any]:
    # Alles im Cache gehoert zu dem Kanal, in dem geprueft wurde -- auch der Fehler eines Versuchs.
    own = cache if cache.get("channel") == channel else {}
    latest = _str_or_none(own.get("latest"))
    if latest is not None and _version_key(latest, allow_pre=channel == "beta") is None:
        latest = None
    digest = _str_or_none(own.get("latest_digest")) if latest else None
    if digest is not None and not _DIGEST_RE.fullmatch(digest):
        digest = None
    error = _str_or_none(own.get("error"))
    if source == "cache" and error:
        source = "offline"
    return {
        "current": current,
        "latest": latest,
        "latest_digest": digest,
        "available": bool(latest) and is_newer(latest, current),
        "channel": channel,
        "checked_at": _str_or_none(own.get("checked_at")),
        "attempted_at": _str_or_none(own.get("attempted_at")),
        "source": source,
        "error": OFFLINE_TEXT if error else None,
        "official_image": is_official_image(image),
        "image": (image or "").strip() or None,
        "official_image_name": OFFICIAL_IMAGE,
        "helper": False,
        "release_notes_url": RELEASE_NOTES_URL.format(version=latest) if latest else None,
    }


def status(data_dir: Path, *, channel: str | None, image: str | None, current: str = __version__) -> dict[str, Any]:
    """Letzter bekannter Stand aus dem Cache, ohne Netz. `source` ist "cache", nach einem gescheiterten
    Versuch "offline"."""
    return _result(load_cache(data_dir), channel=normalize_channel(channel), image=image, current=current, source="cache")


async def check(
    data_dir: Path,
    *,
    channel: str | None,
    image: str | None,
    current: str = __version__,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Fragt die Registry jetzt, schreibt den Cache und liefert das Ergebnis wie `status`. Wirft nie.

    Lesen, Nachfragen und Schreiben laufen unter einer Sperre: Eine zweite Pruefung (Job und Knopf zugleich)
    wartet und liest danach den frischen Stand, statt ihn mit einem aelteren zu ueberschreiben."""
    channel = normalize_channel(channel)
    async with _check_lock():
        cache = await asyncio.to_thread(load_cache, data_dir)
        now = _now_iso()
        try:
            latest, digest = await fetch_latest(channel, transport=transport)
        except Exception as exc:  # noqa: BLE001 - nie eine Ausnahme nach aussen (CheckFailed, Zeitlimit, alles andere)
            logger.info("update_check_failed reason=%s", str(exc) or type(exc).__name__)
            if cache.get("channel") != channel:
                cache = {"channel": channel}
            cache.update(attempted_at=now, error="offline")
            source = "offline"
        else:
            cache = {"channel": channel, "latest": latest, "latest_digest": digest, "checked_at": now, "attempted_at": now, "error": None}
            source = "live"
        try:
            await asyncio.to_thread(save_cache, data_dir, cache)
        except OSError:
            logger.warning("update_check_cache_not_written dir=%s", data_dir)
    return _result(cache, channel=channel, image=image, current=current, source=source)
