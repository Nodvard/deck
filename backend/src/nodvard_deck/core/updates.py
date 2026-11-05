"""Nach Updates suchen: Quelle ist die Container-Registry von GitHub (ghcr.io), dazu die Freigabe am Release auf
github.com; beide sehen nur IP-Adresse und Zeitpunkt."""

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
from typing import Any, NamedTuple

import httpx

from .. import edition, image_info
from ..version import __version__

logger = logging.getLogger("nodvard_deck.updates")

REPOSITORY = "nodvard/deck"
"""Repository von Nodvard Deck (Image `ghcr.io/<REPOSITORY>`, Quelltext auf GitHub), gleich `edition.DECK.repository`.
Als Text, weil der Update-Helfer ihn hier ohne Import liest. Gesucht wird nach dem Image der laufenden Ausgabe."""

REGISTRY_HOST = "ghcr.io"
OFFICIAL_IMAGE = f"{REGISTRY_HOST}/{REPOSITORY}"
# Release-Notizen: CHANGELOG.md im Stand des Tags. Das Tag gibt es sofort, die Release-Seite erst nach dem
# Veröffentlichen.
RELEASE_NOTES_URL = f"https://github.com/{REPOSITORY}/blob/v{{version}}/CHANGELOG.md"

RELEASE_FILE_URL = f"https://github.com/{REPOSITORY}/releases/download/v{{version}}/{{name}}"
GRANT_NAME = "freigabe.json"
GRANT_SIG_NAME = "freigabe.json.sig"
GRANT_MAX_BYTES = 4096
GRANT_HOSTS = frozenset({"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"})
"""Downloads am Release leitet GitHub auf seine Speicher-Hosts weiter; jedes andere Ziel gilt als Fehler."""
MAX_REDIRECTS = 3
GRANT_KEYS = frozenset({"fmt", "repo", "version", "digest", "key_id", "issued_at", "expires_at"})
GRANT_FORMAT = 1
GRANT_MAX_S = 366 * 24 * 3600
GRANT_CLOCK_SLACK_S = 60

CHANNELS = ("stable", "beta")
DEFAULT_CHANNEL = "stable"

TIMEOUT_S = 5.0
"""Höchstdauer je Anfrage."""
TOTAL_TIMEOUT_S = 15.0
"""Höchstdauer der ganzen Prüfung: Token und alle Seiten müssen darin fertig sein, das Manifest (Digest)
bekommt nur den Rest."""
PAGE_SIZE = 100
MAX_PAGES = 10
MAX_BODY_BYTES = 1024 * 1024
"""Größte Antwort der Registry, die gelesen wird (Token, eine Tag-Seite, Manifest)."""
MAX_TAG_LENGTH = 64

KINDS_ANNOTATION = "io.nodvard.deck.release.kinds"
"""Annotation am Image-Index, die `release.yml` aus dem Änderungsprotokoll der Version schreibt."""
KINDS = ("sicherheit", "behoben", "verbessert", "neu", "dokumentation")
MAX_KIND_LOOKUPS = 10
"""So viele Versionen ab der neuesten werden höchstens nach ihren Arten gefragt; was darüber hinausgeht, bleibt
unbekannt."""

CACHE_NAME = "update_check.json"
STALE_TMP_S = 3600
"""Eine liegengebliebene Zwischendatei des Caches gilt nach dieser Zeit als Rest und wird gelöscht."""
MAX_CACHE_BYTES = 64 * 1024

OFFLINE_TEXT = "Konnte nicht prüfen (offline?)."

USER_AGENT = "nodvard-deck"
"""Bewusst ohne Versionsnummer, damit ghcr.io nicht erfährt, welche Version läuft; nicht weglassen, sonst schickt
httpx seinen eigenen Namen."""

_ACCEPT_MANIFEST = "application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json"
_STABLE_RE = re.compile(r"(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})")
_PRE_RE = re.compile(r"(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)")
_PRE_PART_RE = re.compile(r"([A-Za-z-]*?)([0-9]*)")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_LENGTH_RE = re.compile(r"[0-9]{1,15}")
_KEY_ID_RE = re.compile(r"[0-9a-f]{16}")
_SIGNATURE_RE = re.compile(r"[0-9a-f]{128}")


class CheckFailed(Exception):
    """Die Prüfung hat kein verlässliches Ergebnis gebracht (Grund nur für das Protokoll)."""


def _version_key(text: str, *, allow_pre: bool = True) -> tuple | None:
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
    # Abweichend von Semver werden Teile wie `rc10` natürlich sortiert (rc1 < rc2 < rc9 < rc10), sonst bekäme ein
    # Beta-Nutzer auf `rc10` die ältere `rc9` als Update angeboten.
    parts = tuple(_pre_part_key(p) for p in match[4].split("."))
    return (int(match[1]), int(match[2]), int(match[3]), 0, parts)


def _pre_part_key(part: str) -> tuple[int, int, str, int]:
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
    """`candidate` ist nach Semver größer als `current`, unbekannte Formate sind nie neuer."""
    a, b = _version_key(candidate), _version_key(current)
    return a is not None and b is not None and a > b


def pick_latest(tags: list[Any], channel: str) -> str | None:
    """Höchste Version unter den Tags; Vorabversionen nur im Kanal "beta"."""
    allow_pre = channel == "beta"
    best: tuple[tuple, str] | None = None
    for tag in tags:
        key = _version_key(tag, allow_pre=allow_pre) if isinstance(tag, str) else None
        if key is not None and (best is None or key > best[0]):
            best = (key, tag)
    return best[1] if best else None


def versions_up_to(tags: list[Any], latest: str, channel: str) -> list[str]:
    """Die Versionen im Kanal bis einschließlich `latest`, die neueste zuerst."""
    allow_pre = channel == "beta"
    top = _version_key(latest)
    found: dict[tuple, str] = {}
    for tag in tags:
        key = _version_key(tag, allow_pre=allow_pre) if isinstance(tag, str) else None
        if key is not None and top is not None and key <= top:
            found[key] = tag
    return [found[key] for key in sorted(found, reverse=True)]


def kinds_of(manifest: bytes) -> tuple[list[str], bool] | None:
    """Die bekannten Arten aus der Annotation am Image-Index in fester Reihenfolge und ob es nur bekannte waren;
    `None`, wenn die Annotation fehlt oder unlesbar ist."""
    try:
        data = json.loads(manifest)
    except (ValueError, RecursionError):
        return None
    annotations = data.get("annotations") if isinstance(data, dict) else None
    value = annotations.get(KINDS_ANNOTATION) if isinstance(annotations, dict) else None
    if not isinstance(value, str):
        return None
    named = {part.strip() for part in value.split(",")} - {""}
    # Leer oder mit einer Art, die dieser Stand nicht kennt, ist die Angabe unvollständig: nie „harmlos“.
    return [kind for kind in KINDS if kind in named], bool(named) and named <= set(KINDS)


# Das Release-Image trägt `/app/image-info.json` mit `"image": "ghcr.io/<repository>"` (`nodvard_deck.image_info`,
# Build-Argument `IMAGE` in `deploy/Dockerfile`); ein selbst gebautes Image hat die Datei nicht, die Oberfläche zeigt
# dann den passenden Weg.
def is_official_image(image: str | None, repository: str = REPOSITORY) -> bool:
    return bool(image) and image.strip() == f"{REGISTRY_HOST}/{repository}"


def running_version(settings: Any, fallback: str | None = None) -> str:
    """Die genaue Version dieses Containers für die Update-Suche und die Kopien vor Updates (`boot`)."""
    if is_official_image(getattr(settings, "image", None), edition.of(settings).repository):
        # Die Datei im Image geht vor der Variable `NODVARD_DECK_BUILD`: sie kommt immer aus dem laufenden Image, die
        # Variable könnte aus einem alten Container stammen. Auch eine Vorabversion wie `0.6.0-rc1` zählt, die
        # `version.__version__` nie sein kann.
        path = getattr(settings, "image_info_path", image_info.PATH)
        for candidate in (image_info.read(path).get("version"), getattr(settings, "build", None)):
            candidate = (candidate or "").strip()
            if is_version(candidate):
                return candidate
    # Selbst gebautes Image, eine Kennung wie ein Commit oder nichts gesetzt.
    return fallback or __version__


def normalize_channel(channel: str | None) -> str:
    return channel if channel in CHANNELS else DEFAULT_CHANNEL


def _make_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    # Der Proxy aus der Umgebung (HTTP_PROXY/HTTPS_PROXY) bleibt hier gewollt: Die Prüfung fragt nur den festen Ort
    # ghcr.io, nie ein Ziel einer Erweiterung, und hinter einem Proxy für ausgehende Verbindungen käme sie sonst nicht
    # ins Netz. (Anders als `ctx.http` in ext/context.py.)
    return httpx.AsyncClient(
        timeout=httpx.Timeout(TIMEOUT_S),
        follow_redirects=False,
        transport=transport,
        # Ungepackt: Eine gepackte Antwort könnte beim Entpacken weit über MAX_BODY_BYTES wachsen.
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
    )


async def _read_limited(response: httpx.Response, limit: int) -> bytes:
    if response.status_code != 200:
        raise CheckFailed(f"HTTP {response.status_code} von {response.url.path}")
    encoding = response.headers.get("content-encoding", "").strip().lower()
    if encoding not in ("", "identity"):
        raise CheckFailed("gepackte Antwort")
    # Eine zu große Content-Length bricht vor dem Lesen ab; sonst wird beim Lesen mitgezählt.
    length = response.headers.get("content-length")
    if length is not None and (not _LENGTH_RE.fullmatch(length.strip()) or int(length) > limit):
        raise CheckFailed("Antwort zu gross")
    body = bytearray()
    async for chunk in response.aiter_bytes():  # ungepackt (oben geprüft), also gleich den Rohdaten
        body += chunk
        if len(body) > limit:
            raise CheckFailed("Antwort zu gross")
    return bytes(body)


async def _get_limited(client: httpx.AsyncClient, url: str, **kwargs: Any) -> tuple[bytes, httpx.Response]:
    async with client.stream("GET", url, **kwargs) as response:
        body = await _read_limited(response, MAX_BODY_BYTES)
    return body, response


async def _get_json(client: httpx.AsyncClient, url: str, **kwargs: Any) -> tuple[Any, httpx.Response]:
    body, response = await _get_limited(client, url, **kwargs)
    try:
        return json.loads(body), response
    except ValueError as exc:
        raise CheckFailed("kein JSON") from exc


async def _token(client: httpx.AsyncClient, repository: str) -> str:
    # Anonymes Token: klappt nur, solange das Paket öffentlich ist.
    body, _ = await _get_json(
        client, f"https://{REGISTRY_HOST}/token",
        params={"scope": f"repository:{repository}:pull", "service": REGISTRY_HOST},
    )
    token = body.get("token") or body.get("access_token") if isinstance(body, dict) else None
    if not isinstance(token, str) or not token:
        raise CheckFailed("kein Token")
    return token


def _next_page(response: httpx.Response, repository: str) -> str | None:
    link = response.links.get("next", {}).get("url")
    if not link:
        return None
    # Der Token geht nur an ghcr.io: eine Folgeseite woanders hin gilt als Fehler.
    url = response.url.join(link)
    if url.scheme != "https" or url.host != REGISTRY_HOST or url.path != f"/v2/{repository}/tags/list":
        raise CheckFailed("Folgeseite zeigt woanders hin")
    return str(url)


async def _tags(client: httpx.AsyncClient, token: str, repository: str) -> tuple[list[str], bool]:
    headers = {"Authorization": f"Bearer {token}"}
    url: str | None = f"https://{REGISTRY_HOST}/v2/{repository}/tags/list?n={PAGE_SIZE}"
    found: list[str] = []
    for _ in range(MAX_PAGES):
        if url is None:
            return found, True
        body, response = await _get_json(client, url, headers=headers)
        if not isinstance(body, dict) or "tags" not in body:
            raise CheckFailed("Tag-Liste fehlt")
        tags = body["tags"] if body["tags"] is not None else []  # `null`: noch keine Tags
        if not isinstance(tags, list):
            raise CheckFailed("Tag-Liste kaputt")
        found.extend(t for t in tags if isinstance(t, str))
        url = _next_page(response, repository)
    if url is not None:
        logger.warning("update_check_page_limit pages=%s", MAX_PAGES)
    return found, url is None


async def _manifest(client: httpx.AsyncClient, token: str, tag: str, repository: str) -> bytes | None:
    try:
        body, _ = await _get_limited(
            client, f"https://{REGISTRY_HOST}/v2/{repository}/manifests/{tag}",
            headers={"Authorization": f"Bearer {token}", "Accept": _ACCEPT_MANIFEST},
        )
    except (CheckFailed, httpx.HTTPError):
        return None
    return body or None


async def _release_file(client: httpx.AsyncClient, version: str, name: str) -> bytes | None:
    """Eine Datei am Release `v<version>`, `None`, solange es sie nicht gibt; Weiterleitungen nur zu `GRANT_HOSTS`."""
    url = httpx.URL(RELEASE_FILE_URL.format(version=version, name=name))
    for _ in range(MAX_REDIRECTS + 1):
        async with client.stream("GET", url) as response:
            if response.is_redirect:
                url = url.join(response.headers.get("location", ""))
                if url.scheme != "https" or url.host not in GRANT_HOSTS or url.port not in (None, 443) or url.userinfo:
                    raise CheckFailed("Weiterleitung zeigt woanders hin")
                continue
            if response.status_code == 404:
                return None
            return await _read_limited(response, GRANT_MAX_BYTES)
    raise CheckFailed("zu viele Weiterleitungen")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("doppelter Schluessel")
    return result


def parse_grant(raw: bytes, raw_sig: bytes, *, version: str, digest: str) -> dict[str, Any] | None:
    """Freigabe und Signatur (`{"grant": ..., "sig": ...}`), wenn Form und Ziel passen; die Signatur prüft der
    Helfer."""
    try:
        grant = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
        sig = raw_sig.decode("ascii").strip()
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return {"grant": grant, "sig": sig} if grant_fits(grant, sig, version=version, digest=digest) else None


def grant_fits(grant: Any, sig: Any, *, version: str, digest: str | None) -> bool:
    """Ob `grant` und `sig` die Form einer Freigabe haben und genau zu Version (fertig, keine Vorabversion) und Digest
    passen."""
    if not isinstance(grant, dict) or set(grant) != GRANT_KEYS or not isinstance(sig, str):
        return False
    if not version.isascii() or _STABLE_RE.fullmatch(version) is None:
        return False
    times_ok = (all(type(grant[k]) is int and grant[k] >= 0 for k in ("issued_at", "expires_at"))
                and grant["issued_at"] < grant["expires_at"] <= grant["issued_at"] + GRANT_MAX_S)
    return (
        type(grant["fmt"]) is int and grant["fmt"] == GRANT_FORMAT
        and grant["repo"] == OFFICIAL_IMAGE and grant["version"] == version
        and digest is not None and grant["digest"] == digest
        and isinstance(grant["key_id"], str) and _KEY_ID_RE.fullmatch(grant["key_id"]) is not None
        and times_ok and _SIGNATURE_RE.fullmatch(sig) is not None
    )


async def _fetch_grant(client: httpx.AsyncClient, version: str, digest: str) -> dict[str, Any] | None:
    raw = await _release_file(client, version, GRANT_NAME)
    raw_sig = await _release_file(client, version, GRANT_SIG_NAME) if raw is not None else None
    if raw is None or raw_sig is None:
        return None
    found = parse_grant(raw, raw_sig, version=version, digest=digest)
    if found is None:
        logger.warning("update_check_grant_unusable version=%s", version)
    return found


async def _find_latest(client: httpx.AsyncClient, channel: str, repository: str) -> tuple[str, list[str], str | None, bool]:
    token = await _token(client, repository)
    tags, complete = await _tags(client, token, repository)
    return token, tags, pick_latest(tags, channel), complete


class Found(NamedTuple):
    latest: str | None
    digest: str | None
    grant: dict[str, Any] | None
    kinds: list[str]
    """Die Arten aller Versionen nach `current` bis `latest`, soweit bekannt."""
    kinds_complete: bool
    """`False`, wenn Tags oder Arten nicht vollständig ermittelt werden konnten."""


async def fetch_latest(
    channel: str, *, current: str = __version__, repository: str = REPOSITORY,
    transport: httpx.AsyncBaseTransport | None = None, previous_grant: Any = None,
) -> Found:
    """Neueste Version, Digest, Freigabe und Arten seit `current`; bei Abruffehlern bleibt eine passende Freigabe.
    Wirft `CheckFailed`, wenn Token oder Tags nicht rechtzeitig kommen. Freigaben gibt es nur für Deck."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + TOTAL_TIMEOUT_S
    async with _make_client(transport) as client:
        try:
            token, tags, latest, tags_complete = await asyncio.wait_for(_find_latest(client, channel, repository), TOTAL_TIMEOUT_S)
        except TimeoutError as exc:
            raise CheckFailed("Zeitlimit") from exc
        except httpx.HTTPError as exc:
            raise CheckFailed(type(exc).__name__) from exc
        if not latest:
            return Found(None, None, None, [], True)
        # Digest und Arten sind nur eine Zugabe: Sie bekommen die Zeit, die übrig ist. Was fehlt, bleibt `None`
        # bzw. unbekannt, und das Ergebnis gilt trotzdem. Gefragt werden immer die neuesten Versionen im Kanal, nicht
        # nur die nach der laufenden: Sonst verriete die älteste Anfrage ghcr.io, welche Version hier läuft.
        listed = versions_up_to(tags, latest, channel)
        wanted = listed[:MAX_KIND_LOOKUPS]
        newer = [tag for tag in listed if is_newer(tag, current)]
        digest, kinds, complete = None, set(), tags_complete and len(newer) <= MAX_KIND_LOOKUPS
        grant = None
        for tag in wanted:
            try:
                body = await asyncio.wait_for(_manifest(client, token, tag, repository), max(0.0, deadline - loop.time()))
            except TimeoutError:
                logger.info("update_check_manifest_skipped tag=%s reason=Zeitlimit", tag)
                body = None
            if tag == latest and body:
                # Ein Digest ist per Definition der sha256 über die Bytes des Manifests.
                digest = "sha256:" + hashlib.sha256(body).hexdigest()
                if repository == REPOSITORY:
                    try:
                        grant = await asyncio.wait_for(_fetch_grant(client, latest, digest), max(0.0, deadline - loop.time()))
                    except Exception as exc:  # noqa: BLE001 - die Freigabe ist nur eine Zugabe
                        logger.info("update_check_grant_skipped reason=%s", str(exc) or type(exc).__name__)
                        fits = isinstance(previous_grant, dict) and grant_fits(
                            previous_grant.get("grant"), previous_grant.get("sig"), version=latest, digest=digest)
                        grant = previous_grant if fits else None
            if tag not in newer:
                continue
            found = kinds_of(body) if body else None
            if found is None or not found[1]:
                complete = False
            if found is not None:
                kinds.update(found[0])
    return Found(latest, digest, grant, [kind for kind in KINDS if kind in kinds], complete)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_cache(data_dir: Path) -> dict[str, Any]:
    """Inhalt von `update_check.json` oder `{}` (fehlt, zu groß, kaputt)."""
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
    """Schreibt den Cache ganz oder gar nicht: eigene Zwischendatei im selben Ordner, dann `os.replace`."""
    path = Path(data_dir) / CACHE_NAME
    # `mkstemp` legt die Datei exklusiv unter zufälligem Namen an, so kommen sich zwei Schreiber nicht in die Quere.
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
    # Nach einem harten Abbruch (SIGKILL, Stromausfall) bleibt eine Zwischendatei liegen; sie wird hier weggeräumt.
    _sweep_stale_tmp(path)


def _sweep_stale_tmp(path: Path) -> None:
    prefix = f".{CACHE_NAME}."
    limit = time.time() - STALE_TMP_S
    # Links werden nie verfolgt; jeder Fehler wird ignoriert, das Speichern ist da schon gelungen.
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
    # Wechselt der Loop (Tests), gibt es eine neue Sperre; der Platz hält den Loop nur schwach.
    global _lock_slot
    loop = asyncio.get_running_loop()
    if _lock_slot is None or _lock_slot[0]() is not loop:
        _lock_slot = (weakref.ref(loop), asyncio.Lock())
    return _lock_slot[1]


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _same_search(cache: dict[str, Any], channel: str, repository: str) -> bool:
    # Alles im Cache gehört zu Kanal und Image, für die geprüft wurde, auch der Fehler eines Versuchs.
    return cache.get("channel") == channel and cache.get("repository", REPOSITORY) == repository


def _usable_grant(cache: dict[str, Any], latest: str | None, digest: str | None, now: float) -> dict[str, Any] | None:
    found = cache.get("grant")
    if not latest or not isinstance(found, dict):
        return None
    grant, sig = found.get("grant"), found.get("sig")
    if not grant_fits(grant, sig, version=latest, digest=digest):
        return None
    if grant["issued_at"] > now + GRANT_CLOCK_SLACK_S or now >= grant["expires_at"]:
        return None
    return {"grant": grant, "sig": sig}


def cached_grant(data_dir: Path, *, channel: str | None, version: str, digest: str | None,
                 now: float | None = None) -> dict[str, Any] | None:
    """Die Freigabe aus der letzten Update-Suche, wenn sie genau zu Version und Digest passt und gerade gilt."""
    cache = load_cache(data_dir)
    if not _same_search(cache, normalize_channel(channel), REPOSITORY) or cache.get("latest") != version:
        return None
    return _usable_grant(cache, version, digest, time.time() if now is None else now)


def _result(
    cache: dict[str, Any], *, channel: str, image: str | None, current: str, source: str, repository: str,
) -> dict[str, Any]:
    own = cache if _same_search(cache, channel, repository) else {}
    latest = _str_or_none(own.get("latest"))
    if latest is not None and _version_key(latest, allow_pre=channel == "beta") is None:
        latest = None
    digest = _str_or_none(own.get("latest_digest")) if latest else None
    if digest is not None and not _DIGEST_RE.fullmatch(digest):
        digest = None
    available = bool(latest) and is_newer(latest, current)
    # Die Arten gelten nur für die Version, von der aus gesucht wurde; nach einem Update sind sie bis zur nächsten
    # Suche unbekannt.
    raw_kinds = own.get("kinds") if own.get("kinds_since") == current else None
    known = isinstance(raw_kinds, list) and all(isinstance(kind, str) for kind in raw_kinds)
    kinds = [kind for kind in KINDS if known and kind in raw_kinds]
    kinds_complete = known and own.get("kinds_complete") is True
    error = _str_or_none(own.get("error"))
    if source == "cache" and error:
        source = "offline"
    grant = _usable_grant(own, latest, digest, time.time()) if repository == REPOSITORY else None
    return {
        "current": current,
        "latest": latest,
        "latest_digest": digest,
        "available": available,
        "kinds": kinds if available else [],
        "kinds_complete": kinds_complete if available else True,
        "channel": channel,
        "checked_at": _str_or_none(own.get("checked_at")),
        "attempted_at": _str_or_none(own.get("attempted_at")),
        "source": source,
        "error": OFFLINE_TEXT if error else None,
        "official_image": is_official_image(image, repository),
        "image": (image or "").strip() or None,
        "official_image_name": f"{REGISTRY_HOST}/{repository}",
        "helper": False,
        "release_notes_url": RELEASE_NOTES_URL.format(version=latest) if latest else None,
        "approved": grant is not None,
        "approved_key": grant["grant"]["key_id"] if grant else None,
    }


def status(
    data_dir: Path, *, channel: str | None, image: str | None, current: str = __version__, repository: str = REPOSITORY,
) -> dict[str, Any]:
    """Letzter bekannter Stand aus dem Cache, ohne Netz; `source` ist "cache", nach einem gescheiterten Versuch "offline"."""
    return _result(
        load_cache(data_dir), channel=normalize_channel(channel), image=image, current=current, source="cache",
        repository=repository,
    )


async def check(
    data_dir: Path,
    *,
    channel: str | None,
    image: str | None,
    current: str = __version__,
    repository: str = REPOSITORY,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Fragt die Registry jetzt, schreibt den Cache und liefert das Ergebnis wie `status`, wirft nie; eine zweite
    Prüfung wartet."""
    channel = normalize_channel(channel)
    async with _check_lock():
        cache = await asyncio.to_thread(load_cache, data_dir)
        now = _now_iso()
        try:
            previous = cache.get("grant") if _same_search(cache, channel, repository) else None
            found = await fetch_latest(
                channel, current=current, repository=repository, transport=transport, previous_grant=previous,
            )
        except Exception as exc:  # noqa: BLE001 - nie eine Ausnahme nach außen (CheckFailed, Zeitlimit, alles andere)
            logger.info("update_check_failed reason=%s", str(exc) or type(exc).__name__)
            # Der letzte gute Stand bleibt im Cache stehen, nur der Fehler kommt dazu.
            if not _same_search(cache, channel, repository):
                cache = {"channel": channel, "repository": repository}
            cache.update(attempted_at=now, error="offline")
            source = "offline"
        else:
            # Auch "keine passende Version" (nur Vorabversionen im Kanal stable, noch keine Tags) ist ein gültiges
            # Ergebnis: `latest` ist leer, `checked_at` gesetzt, ein zuvor gefundener Stand wird bewusst ersetzt.
            cache = {
                "channel": channel, "repository": repository, "latest": found.latest, "latest_digest": found.digest,
                "grant": found.grant, "kinds": found.kinds, "kinds_complete": found.kinds_complete,
                "kinds_since": current, "checked_at": now, "attempted_at": now, "error": None,
            }
            source = "live"
        try:
            await asyncio.to_thread(save_cache, data_dir, cache)
        except OSError:
            logger.warning("update_check_cache_not_written dir=%s", data_dir)
    return _result(cache, channel=channel, image=image, current=current, source=source, repository=repository)
