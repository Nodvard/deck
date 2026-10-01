"""Nach Updates suchen (`core/updates.py`): Abfrage der Registry, Versionsvergleich, Cache.

Kein echter Netzzugriff: jede Anfrage geht an einen `httpx.MockTransport`."""

from __future__ import annotations

import asyncio
import gzip
import itertools
import json
import time

import httpx
import pytest
from nodvard_deck.core import updates

TOKEN_URL = "https://ghcr.io/token"
TAGS_PATH = "/v2/nodvard/deck/tags/list"


def _registry(tags_pages: list[list[str]], *, token_status: int = 200, tags_status: int = 200, manifest: bytes | None = b'{"schemaVersion":2}',
              next_links: list[str | None] | None = None, calls: list[httpx.Request] | None = None):
    """Attrappe fuer ghcr.io: Token, Tag-Seiten (Link-Header), Manifest."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        url = request.url
        if url.host != "ghcr.io":
            raise AssertionError(f"fremder Host: {url}")
        if url.path == "/token":
            assert url.params["scope"] == "repository:nodvard/deck:pull"
            assert url.params["service"] == "ghcr.io"
            if token_status != 200:
                return httpx.Response(token_status, json={"errors": []})
            return httpx.Response(200, json={"token": "anon-token"})
        assert request.headers.get("authorization") == "Bearer anon-token"
        if url.path == TAGS_PATH:
            if tags_status != 200:
                return httpx.Response(tags_status, text="kaputt")
            page = int(url.params.get("page", "0"))
            headers = {}
            links = next_links if next_links is not None else [
                f'<{TAGS_PATH}?n=100&last=x&page={i + 1}>; rel="next"' if i + 1 < len(tags_pages) else None for i in range(len(tags_pages))
            ]
            if links[page]:
                headers["Link"] = links[page]
            return httpx.Response(200, json={"name": "nodvard/deck", "tags": tags_pages[page]}, headers=headers)
        if url.path.startswith("/v2/nodvard/deck/manifests/"):
            assert "application/vnd.oci.image.index.v1+json" in request.headers["accept"]
            if manifest is None:
                return httpx.Response(404)
            return httpx.Response(200, content=manifest, headers={"Content-Type": "application/vnd.oci.image.index.v1+json"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def _check(tmp_path, transport, *, channel="stable", current="0.5.0", image=updates.OFFICIAL_IMAGE):
    return await updates.check(tmp_path, channel=channel, image=image, current=current, transport=transport)


# ---------------------------------------------------------------------------
# Versionen
# ---------------------------------------------------------------------------


def test_semver_sorting_is_numeric_and_prereleases_come_before_the_release():
    tags = ["0.9.0", "0.10.0", "0.10.0-rc.2", "0.10.0-rc.10", "0.2.11", "latest", "0.10", "1.0.0-beta"]
    assert updates.pick_latest(tags, "stable") == "0.10.0"
    assert updates.pick_latest(tags, "beta") == "1.0.0-beta"
    assert updates.pick_latest(["0.10.0-rc.2", "0.10.0-rc.10", "0.9.0"], "beta") == "0.10.0-rc.10"
    assert updates.pick_latest(["0.10.0-rc.1", "0.10.0"], "beta") == "0.10.0"
    assert updates.is_newer("0.10.0", "0.9.9")
    assert updates.is_newer("0.6.0", "0.6.0-rc1")
    assert not updates.is_newer("0.6.0-rc1", "0.6.0")
    assert not updates.is_newer("0.5.0", "0.5.0")


def test_prerelease_parts_with_digits_sort_naturally():
    """`rc10` kommt nach `rc9` (Buchstaben und Zahl getrennt), sonst bekaeme ein Beta-Nutzer auf rc10 die rc9 angeboten."""
    order = ["0.6.0-rc1", "0.6.0-rc2", "0.6.0-rc9", "0.6.0-rc10", "0.6.0-rc11", "0.6.0"]
    for older, newer in itertools.pairwise(order):
        assert updates.is_newer(newer, older), (newer, older)
        assert not updates.is_newer(older, newer), (older, newer)
    assert updates.pick_latest(["0.6.0-rc9", "0.6.0-rc10", "0.6.0-rc2"], "beta") == "0.6.0-rc10"
    assert updates.pick_latest(["0.6.0-rc10", "0.6.0-rc9"], "beta") == "0.6.0-rc10"
    assert not updates.is_newer("0.6.0-rc9", "0.6.0-rc10")
    assert updates.is_newer("0.6.0-rc10", "0.6.0-rc9")


def test_prerelease_words_and_dotted_parts_keep_their_order():
    assert updates.is_newer("0.6.0-beta", "0.6.0-alpha")
    assert updates.is_newer("0.6.0-rc", "0.6.0-beta")
    assert updates.is_newer("0.6.0-beta1", "0.6.0-alpha9"), "erst das Wort, dann die Zahl"
    assert updates.is_newer("0.6.0-rc1", "0.6.0-rc"), "ohne Zahl liegt vor mit Zahl"
    assert updates.is_newer("0.6.0-rc.10", "0.6.0-rc.9")
    assert updates.is_newer("0.6.0-rc.1", "0.6.0-rc"), "SemVer: mehr Teile sind groesser"
    assert updates.is_newer("0.6.0-rc", "0.6.0-2"), "reine Zahlen liegen vor Buchstaben"
    assert updates.is_newer("0.6.0-rc2", "0.6.0-rc1"), "wie bisher"
    assert not updates.is_newer("0.6.0-rc1", "0.6.0-rc1")
    assert updates.is_newer("0.6.0-1a", "0.6.0-1"), "unbekannte Form: als Text, aber stabil sortiert"


def test_is_version_accepts_releases_and_prereleases_only():
    for good in ("0.6.0", "10.0.1", "0.6.0-rc1", "1.0.0-beta.2"):
        assert updates.is_version(good), good
    for bad in (None, "", "abc123", "v0.6.0", "0.6", "latest", " 0.6.0", 5):
        assert not updates.is_version(bad), bad


def test_only_strict_version_tags_count():
    tags = ["latest", "0.6", "v0.7.0", "0.7.0\n", " 0.7.0", "0.7.0.1", "sha-abc", "0.6.1", "9" * 200 + ".0.0"]
    assert updates.pick_latest(tags, "stable") == "0.6.1"
    assert updates.pick_latest([], "stable") is None
    assert updates.pick_latest(["latest"], "beta") is None


def test_official_image_only_for_the_exact_repository():
    assert updates.is_official_image("ghcr.io/nodvard/deck")
    assert updates.is_official_image(" ghcr.io/nodvard/deck ")
    for other in (None, "", "nodvard-deck", "nodvard-deck:latest", "ghcr.io/nodvard/deck-fork", "ghcr.io/someone/deck", "evil.io/nodvard/deck"):
        assert not updates.is_official_image(other), other


# ---------------------------------------------------------------------------
# Live-Abfrage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_check_uses_anonymous_token_and_finds_the_newest_version(tmp_path):
    calls: list[httpx.Request] = []
    result = await _check(tmp_path, _registry([["0.5.0", "0.6.0", "latest", "0.6"]], calls=calls))
    assert result["source"] == "live"
    assert result["current"] == "0.5.0"
    assert result["latest"] == "0.6.0"
    assert result["available"] is True
    assert result["error"] is None
    assert result["channel"] == "stable"
    assert result["official_image"] is True
    assert result["helper"] is False
    assert result["checked_at"]
    assert result["release_notes_url"] == "https://github.com/nodvard/deck/blob/v0.6.0/CHANGELOG.md"
    import hashlib

    assert result["latest_digest"] == "sha256:" + hashlib.sha256(b'{"schemaVersion":2}').hexdigest()
    assert calls[0].url.path == "/token"
    assert calls[1].url.path == TAGS_PATH and calls[1].url.params["n"] == "100"
    assert calls[-1].url.path == "/v2/nodvard/deck/manifests/0.6.0"


@pytest.mark.asyncio
async def test_follows_link_header_pages(tmp_path):
    pages = [["0.1.0", "0.2.0"], ["0.3.0", "0.7.1"], ["0.4.0"]]
    calls: list[httpx.Request] = []
    result = await _check(tmp_path, _registry(pages, calls=calls))
    assert result["latest"] == "0.7.1"
    assert [c.url.params.get("page") for c in calls if c.url.path == TAGS_PATH] == [None, "1", "2"]


@pytest.mark.asyncio
async def test_page_count_is_limited(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "MAX_PAGES", 3)
    pages = [[f"0.{i}.0"] for i in range(6)]
    calls: list[httpx.Request] = []
    result = await _check(tmp_path, _registry(pages, calls=calls))
    assert len([c for c in calls if c.url.path == TAGS_PATH]) == 3
    assert result["latest"] == "0.2.0"


@pytest.mark.asyncio
async def test_next_link_to_another_host_is_refused(tmp_path):
    links = ['<https://evil.example/v2/nodvard/deck/tags/list?page=1>; rel="next"', None]
    result = await _check(tmp_path, _registry([["0.6.0"], ["9.9.9"]], next_links=links))
    # Der Token geht nie an einen fremden Host; die Pruefung gilt als gescheitert.
    assert result["source"] == "offline"
    assert result["latest"] is None


@pytest.mark.asyncio
async def test_prereleases_only_in_beta_channel(tmp_path):
    tags = [["0.5.0", "0.6.0-rc1"]]
    stable = await _check(tmp_path, _registry(tags))
    assert stable["latest"] == "0.5.0" and stable["available"] is False
    beta = await _check(tmp_path, _registry(tags), channel="beta")
    assert beta["latest"] == "0.6.0-rc1" and beta["available"] is True and beta["channel"] == "beta"


@pytest.mark.asyncio
async def test_unknown_channel_falls_back_to_stable(tmp_path):
    result = await _check(tmp_path, _registry([["0.5.0", "0.6.0-rc1"]]), channel="nightly")
    assert result["channel"] == "stable" and result["latest"] == "0.5.0"


@pytest.mark.asyncio
async def test_current_newer_than_registry_is_not_an_update(tmp_path):
    result = await _check(tmp_path, _registry([["0.5.0"]]), current="0.6.0-dev")
    assert result["available"] is False


@pytest.mark.asyncio
async def test_missing_digest_does_not_fail_the_check(tmp_path):
    result = await _check(tmp_path, _registry([["0.6.0"]], manifest=None))
    assert result["source"] == "live" and result["latest"] == "0.6.0" and result["latest_digest"] is None


@pytest.mark.asyncio
async def test_self_built_image_is_reported(tmp_path):
    for image in (None, "nodvard-deck"):
        result = await _check(tmp_path, _registry([["0.6.0"]]), image=image)
        assert result["official_image"] is False
        assert result["latest"] == "0.6.0", "auch selbst gebaut lohnt der Hinweis auf eine neue Version"


# ---------------------------------------------------------------------------
# Groesse, Kompression, Zeit
# ---------------------------------------------------------------------------


def _with_tags_answer(make_tags_response, *, manifest_response=None, calls: list[httpx.Request] | None = None):
    """Registry, deren Tag-Seite (und wahlweise das Manifest) `make_*_response()` liefert."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if request.url.path == "/token":
            return httpx.Response(200, json={"token": "anon-token"})
        if request.url.path == TAGS_PATH:
            return await make_tags_response()
        if manifest_response is not None:
            return await manifest_response()
        return httpx.Response(200, content=b'{"schemaVersion":2}')

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_requests_ask_for_uncompressed_answers(tmp_path):
    calls: list[httpx.Request] = []
    await _check(tmp_path, _registry([["0.6.0"]], calls=calls))
    assert len(calls) == 3
    assert all(c.headers["accept-encoding"] == "identity" for c in calls)


@pytest.mark.asyncio
async def test_user_agent_does_not_reveal_the_installed_version(tmp_path):
    """Datenschutz-Hinweis der Karte: ghcr.io sieht IP-Adresse und Zeitpunkt, sonst nichts."""
    from nodvard_deck.version import __version__

    calls: list[httpx.Request] = []
    await _check(tmp_path, _registry([["0.6.0"]], calls=calls))
    assert {c.headers["user-agent"] for c in calls} == {"nodvard-deck"}
    assert all(__version__ not in value for c in calls for value in c.headers.values())


@pytest.mark.asyncio
async def test_huge_streamed_answer_is_cut_off_early(tmp_path):
    chunk = b"x" * 65536
    sent = {"bytes": 0}

    async def endless():
        for _ in range(800):  # 50 MiB
            sent["bytes"] += len(chunk)
            yield chunk

    async def tags():
        return httpx.Response(200, content=endless())

    result = await _check(tmp_path, _with_tags_answer(tags))
    assert result["source"] == "offline"
    assert sent["bytes"] <= updates.MAX_BODY_BYTES + len(chunk), "Abbruch beim ersten Block ueber der Grenze"


@pytest.mark.asyncio
async def test_compressed_answer_is_refused_without_unpacking(tmp_path):
    """Auch eine harmlose gepackte Antwort gilt nicht: entpackt wird nie (eine kleine Bombe wuerde riesig)."""
    packed = gzip.compress(b'{"tags": ["0.6.0"]}')

    async def tags():
        return httpx.Response(200, content=packed, headers={"Content-Encoding": "gzip"})

    result = await _check(tmp_path, _with_tags_answer(tags))
    assert result["source"] == "offline" and result["error"] == updates.OFFLINE_TEXT


@pytest.mark.asyncio
async def test_compressed_manifest_only_leaves_the_digest_empty(tmp_path):
    async def tags():
        return httpx.Response(200, json={"tags": ["0.6.0"]})

    async def manifest():
        return httpx.Response(200, content=gzip.compress(b"{}"), headers={"Content-Encoding": "gzip"})

    result = await _check(tmp_path, _with_tags_answer(tags, manifest_response=manifest))
    assert result["source"] == "live" and result["latest"] == "0.6.0" and result["latest_digest"] is None


@pytest.mark.asyncio
async def test_too_large_content_length_is_refused_before_reading(tmp_path):
    read = {"started": False}

    async def body():
        read["started"] = True
        yield b'{"tags": ["0.6.0"]}'

    async def tags():
        return httpx.Response(200, content=body(), headers={"Content-Length": "10000000"})

    result = await _check(tmp_path, _with_tags_answer(tags))
    assert result["source"] == "offline"
    assert read["started"] is False, "nichts gelesen"


@pytest.mark.asyncio
async def test_slow_digest_does_not_fail_the_check(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "TOTAL_TIMEOUT_S", 0.3)

    async def tags():
        return httpx.Response(200, json={"tags": ["0.5.0", "0.6.0"]})

    async def manifest():
        await asyncio.sleep(5)
        return httpx.Response(200, content=b"{}")

    started = time.monotonic()
    result = await _check(tmp_path, _with_tags_answer(tags, manifest_response=manifest))
    assert time.monotonic() - started < 2, "der Digest hat nur die restliche Zeit"
    assert result["source"] == "live" and result["latest"] == "0.6.0" and result["error"] is None
    assert result["latest_digest"] is None
    assert updates.status(tmp_path, channel="stable", image=None, current="0.5.0")["latest"] == "0.6.0"


@pytest.mark.asyncio
async def test_slow_tags_fail_within_the_total_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "TOTAL_TIMEOUT_S", 0.3)

    async def tags():
        await asyncio.sleep(5)
        return httpx.Response(200, json={"tags": ["0.6.0"]})

    started = time.monotonic()
    result = await _check(tmp_path, _with_tags_answer(tags))
    assert time.monotonic() - started < 2
    assert result["source"] == "offline"


# ---------------------------------------------------------------------------
# Fehler und Cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("where,status", [("token", 401), ("token", 429), ("tags", 401), ("tags", 429), ("tags", 503)])
async def test_http_errors_never_raise_and_keep_the_last_result(tmp_path, where, status):
    first = await _check(tmp_path, _registry([["0.6.0"]]))
    assert first["source"] == "live"
    kwargs = {"token_status": status} if where == "token" else {"tags_status": status}
    result = await _check(tmp_path, _registry([["0.7.0"]], **kwargs))
    assert result["source"] == "offline"
    assert result["error"] == updates.OFFLINE_TEXT
    assert result["latest"] == "0.6.0", "letzter bekannter Stand bleibt"
    assert result["checked_at"] == first["checked_at"]
    assert result["attempted_at"] >= first["checked_at"]


@pytest.mark.asyncio
async def test_offline_without_cache(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("kein Netz", request=request)

    result = await _check(tmp_path, httpx.MockTransport(handler))
    assert result["source"] == "offline"
    assert result["latest"] is None and result["available"] is False and result["checked_at"] is None
    assert result["error"] == updates.OFFLINE_TEXT


@pytest.mark.asyncio
async def test_timeout_counts_as_offline(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("zu langsam", request=request)

    result = await _check(tmp_path, httpx.MockTransport(handler))
    assert result["source"] == "offline"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"kein json", b"[]", b'{"tags": "0.6.0"}', b'{"token": 5}'])
async def test_invalid_json_counts_as_offline(tmp_path, body):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token" and body != b'{"token": 5}':
            return httpx.Response(200, json={"token": "anon-token"})
        return httpx.Response(200, content=body)

    result = await _check(tmp_path, httpx.MockTransport(handler))
    assert result["source"] == "offline"
    assert result["error"] == updates.OFFLINE_TEXT


@pytest.mark.asyncio
async def test_status_reads_the_cache_without_network(tmp_path):
    await _check(tmp_path, _registry([["0.6.0"]]))
    cached = updates.status(tmp_path, channel="stable", image=updates.OFFICIAL_IMAGE, current="0.5.0")
    assert cached["source"] == "cache"
    assert cached["latest"] == "0.6.0" and cached["available"] is True and cached["error"] is None
    # Nach einem Update ist dieselbe Version nicht mehr "neu".
    assert updates.status(tmp_path, channel="stable", image=None, current="0.6.0")["available"] is False


@pytest.mark.asyncio
async def test_status_after_a_failed_attempt_says_offline(tmp_path):
    await _check(tmp_path, _registry([["0.6.0"]]))
    await _check(tmp_path, _registry([["0.6.0"]], tags_status=500))
    cached = updates.status(tmp_path, channel="stable", image=None, current="0.5.0")
    assert cached["source"] == "offline" and cached["error"] == updates.OFFLINE_TEXT and cached["latest"] == "0.6.0"


@pytest.mark.asyncio
async def test_cache_of_another_channel_is_not_used(tmp_path):
    await _check(tmp_path, _registry([["0.6.0-rc1"]]), channel="beta")
    cached = updates.status(tmp_path, channel="stable", image=None, current="0.5.0")
    assert cached["latest"] is None and cached["available"] is False and cached["checked_at"] is None


@pytest.mark.asyncio
async def test_failure_of_another_channel_is_not_shown(tmp_path):
    """Stabil gescheitert, dann auf Beta umgestellt: fuer Beta wurde noch nie geprueft -- kein "offline"."""
    await _check(tmp_path, _registry([["0.6.0"]]))
    await _check(tmp_path, _registry([["0.6.0"]], tags_status=503))
    beta = updates.status(tmp_path, channel="beta", image=None, current="0.5.0")
    assert beta["source"] == "cache" and beta["error"] is None
    assert beta["attempted_at"] is None and beta["checked_at"] is None and beta["latest"] is None
    stable = updates.status(tmp_path, channel="stable", image=None, current="0.5.0")
    assert stable["source"] == "offline" and stable["error"] == updates.OFFLINE_TEXT and stable["attempted_at"]


@pytest.mark.asyncio
async def test_successful_check_without_a_matching_version(tmp_path):
    """Im Kanal "stable" gibt es nur Vorabversionen: gueltiges Ergebnis "keine gefunden", kein Fehler."""
    result = await _check(tmp_path, _registry([["0.6.0-rc1", "latest", "0.6"]]))
    assert result["source"] == "live" and result["error"] is None
    assert result["latest"] is None and result["available"] is False and result["release_notes_url"] is None
    assert result["checked_at"], "geprueft -- die Oberflaeche zeigt dann nicht \"noch nicht geprueft\""
    cached = updates.status(tmp_path, channel="stable", image=None, current="0.5.0")
    assert cached["source"] == "cache" and cached["latest"] is None and cached["checked_at"] == result["checked_at"]


@pytest.mark.asyncio
async def test_empty_tag_list_is_an_answer_but_a_missing_one_is_a_failure(tmp_path):
    def answer(body: dict):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/token":
                return httpx.Response(200, json={"token": "anon-token"})
            return httpx.Response(200, json=body)

        return httpx.MockTransport(handler)

    first = await _check(tmp_path, _registry([["0.6.0"]]))
    broken = await _check(tmp_path, answer({"name": "nodvard/deck"}))
    assert broken["source"] == "offline" and broken["latest"] == "0.6.0", "ohne Tag-Liste bleibt der letzte Stand"
    assert broken["checked_at"] == first["checked_at"]
    # Eine vollstaendige Antwort ohne Tags ersetzt den alten Stand: die Version gibt es dort nicht mehr.
    empty = await _check(tmp_path, answer({"name": "nodvard/deck", "tags": None}))
    assert empty["source"] == "live" and empty["latest"] is None and empty["error"] is None and empty["checked_at"]


def test_status_without_cache_or_with_a_broken_one(tmp_path):
    empty = updates.status(tmp_path, channel="stable", image=None, current="0.5.0")
    assert empty["source"] == "cache" and empty["latest"] is None and empty["checked_at"] is None and empty["error"] is None
    (tmp_path / updates.CACHE_NAME).write_text("{kaputt", encoding="utf-8")
    assert updates.status(tmp_path, channel="stable", image=None, current="0.5.0")["latest"] is None
    (tmp_path / updates.CACHE_NAME).write_text(json.dumps({"channel": "stable", "latest": "nicht-semver"}), encoding="utf-8")
    assert updates.status(tmp_path, channel="stable", image=None, current="0.5.0")["latest"] is None


@pytest.mark.asyncio
async def test_cache_is_written_atomically_in_the_data_dir(tmp_path):
    await _check(tmp_path, _registry([["0.6.0"]]))
    data = json.loads((tmp_path / updates.CACHE_NAME).read_text(encoding="utf-8"))
    assert data["latest"] == "0.6.0" and data["channel"] == "stable" and data["error"] is None
    assert [p.name for p in tmp_path.iterdir()] == [updates.CACHE_NAME], "keine Reste von Zwischendateien"


@pytest.mark.asyncio
async def test_two_checks_at_once_run_one_after_the_other(tmp_path):
    """Job und Knopf zugleich: keine gleichzeitigen Anfragen, und ein spaeter Fehlschlag loescht den frischen Stand nicht."""
    active = {"now": 0, "max": 0}
    fail = {"next": False}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
            failing, fail["next"] = fail["next"], True  # der erste Lauf klappt, der zweite scheitert
            await asyncio.sleep(0.05)
            active["now"] -= 1
            if failing:
                return httpx.Response(503)
            return httpx.Response(200, json={"token": "anon-token"})
        if request.url.path == TAGS_PATH:
            return httpx.Response(200, json={"tags": ["0.6.0"]})
        return httpx.Response(200, content=b"{}")

    transport = httpx.MockTransport(handler)
    first, second = await asyncio.gather(_check(tmp_path, transport), _check(tmp_path, transport))
    assert active["max"] == 1, "nie zwei Pruefungen gleichzeitig"
    assert first["source"] == "live" and second["source"] == "offline"
    assert second["latest"] == "0.6.0", "der zweite sieht den Stand des ersten"
    cached = json.loads((tmp_path / updates.CACHE_NAME).read_text(encoding="utf-8"))
    assert cached["latest"] == "0.6.0" and cached["checked_at"] == first["checked_at"]
    assert [p.name for p in tmp_path.iterdir()] == [updates.CACHE_NAME]


def test_save_cache_uses_its_own_temporary_file(tmp_path, monkeypatch):
    """Die Zwischendatei hat keinen festen Namen: eine liegengebliebene (oder fremde) wird nie mitbenutzt."""
    import os

    stale = tmp_path / f".{updates.CACHE_NAME}.{os.getpid()}.tmp"
    stale.write_text("alt", encoding="utf-8")
    updates.save_cache(tmp_path, {"channel": "stable", "latest": "0.6.0"})
    assert updates.load_cache(tmp_path)["latest"] == "0.6.0"
    assert stale.read_text(encoding="utf-8") == "alt"
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([updates.CACHE_NAME, stale.name])


def test_save_cache_removes_old_leftovers_of_the_temporary_file(tmp_path):
    """Ein harter Abbruch laesst `.update_check.json.<zufall>.tmp` liegen: der naechste Schreibvorgang raeumt sie nach
    einer Stunde weg -- und nur diese, nie eine frische, eine fremde Datei oder einen Ordner."""
    import os

    old = time.time() - updates.STALE_TMP_S - 60
    leftover = tmp_path / f".{updates.CACHE_NAME}.abc123.tmp"
    fresh = tmp_path / f".{updates.CACHE_NAME}.def456.tmp"
    foreign = tmp_path / "andere.tmp"
    wrong_end = tmp_path / f".{updates.CACHE_NAME}.ghi789"
    folder = tmp_path / f".{updates.CACHE_NAME}.dir.tmp"
    for path in (leftover, fresh, foreign, wrong_end):
        path.write_text("x", encoding="utf-8")
    folder.mkdir()
    for path in (leftover, foreign, wrong_end, folder):
        os.utime(path, (old, old))
    link = tmp_path / f".{updates.CACHE_NAME}.link.tmp"
    target = tmp_path / "ziel.txt"
    target.write_text("ziel", encoding="utf-8")
    os.utime(target, (old, old))
    try:
        link.symlink_to(target)
    except OSError:  # kein Symlink-Recht (Windows)
        link = None
    updates.save_cache(tmp_path, {"channel": "stable", "latest": "0.6.0"})
    assert not leftover.exists()
    assert fresh.exists() and foreign.exists() and wrong_end.exists() and folder.is_dir() and target.read_text(encoding="utf-8") == "ziel"
    if link is not None:
        assert link.is_symlink(), "ein Link wird nie verfolgt oder geloescht"
    assert updates.load_cache(tmp_path)["latest"] == "0.6.0"


def test_save_cache_sweep_never_fails_the_save(tmp_path, monkeypatch):
    import os

    def broken(_path):
        raise PermissionError("nicht lesbar")

    monkeypatch.setattr(os, "scandir", broken)
    updates.save_cache(tmp_path, {"channel": "stable", "latest": "0.6.0"})
    assert updates.load_cache(tmp_path)["latest"] == "0.6.0"


def test_check_lock_is_one_slot_per_loop_and_does_not_keep_old_loops_alive():
    """Auch eine umkaempfte Sperre (sie merkt sich dann ihren Loop) haelt keinen geschlossenen Loop fest, und es
    bleibt hoechstens ein Platz belegt."""
    import gc
    import weakref

    refs: list = []

    async def contend() -> None:
        loop = asyncio.get_running_loop()
        refs.append(weakref.ref(loop))
        lock = updates._check_lock()
        assert updates._check_lock() is lock, "im selben Loop immer dieselbe Sperre"

        async def hold() -> None:
            async with updates._check_lock():
                await asyncio.sleep(0.01)

        await asyncio.gather(hold(), hold())  # die zweite wartet: die Sperre merkt sich den Loop

    for _ in range(3):
        asyncio.run(contend())
    # Der letzte Loop haengt noch an der Sperre, die frueheren sind frei.
    updates._lock_slot = None
    gc.collect()
    assert all(ref() is None for ref in refs)

    async def other_loop_gets_its_own_lock() -> asyncio.Lock:
        return updates._check_lock()

    first = asyncio.run(other_loop_gets_its_own_lock())
    second = asyncio.run(other_loop_gets_its_own_lock())
    assert first is not second
    gc.collect()
    assert updates._lock_slot is not None and updates._lock_slot[1] is second


class _Settings:
    def __init__(self, image=None, build=None, image_info_path=None):
        self.image, self.build, self.image_info_path = image, build, image_info_path


def test_running_version_of_the_official_image_comes_from_the_file(tmp_path):
    info = tmp_path / "image-info.json"
    info.write_text(json.dumps({"image": updates.OFFICIAL_IMAGE, "version": "0.6.0-rc2"}), encoding="utf-8")
    # Die Datei geht vor einer (womoeglich veralteten) Variable.
    assert updates.running_version(_Settings(updates.OFFICIAL_IMAGE, "0.4.0", info)) == "0.6.0-rc2"
    # Ohne Datei: die Angabe in `build`, wenn sie eine Version ist.
    missing = tmp_path / "gibt-es-nicht.json"
    assert updates.running_version(_Settings(updates.OFFICIAL_IMAGE, "0.6.0-rc1", missing)) == "0.6.0-rc1"
    # Sonst die Version des Codes bzw. der Rueckfall.
    for build in (None, "abc123", ""):
        assert updates.running_version(_Settings(updates.OFFICIAL_IMAGE, build, missing)) == updates.__version__
    assert updates.running_version(_Settings(updates.OFFICIAL_IMAGE, None, missing), "9.9.9") == "9.9.9"
    # Eine kaputte Version in der Datei zaehlt nicht.
    info.write_text(json.dumps({"image": updates.OFFICIAL_IMAGE, "version": "kaputt"}), encoding="utf-8")
    assert updates.running_version(_Settings(updates.OFFICIAL_IMAGE, "0.6.0", info)) == "0.6.0"


@pytest.mark.parametrize("image", [None, "", "nodvard-deck", "ghcr.io/fremd/deck"])
def test_running_version_of_other_images_is_the_code_version(tmp_path, image):
    info = tmp_path / "image-info.json"
    info.write_text(json.dumps({"version": "0.6.0-rc2"}), encoding="utf-8")
    assert updates.running_version(_Settings(image, "9.9.9", info)) == updates.__version__


@pytest.mark.asyncio
async def test_unwritable_data_dir_does_not_raise(tmp_path):
    missing = tmp_path / "gibt-es-nicht" / "auch-nicht"
    result = await _check(missing, _registry([["0.6.0"]]))
    assert result["source"] == "live" and result["latest"] == "0.6.0"


def test_repository_is_one_constant():
    import inspect

    source = inspect.getsource(updates)
    assert source.count('"nodvard/deck"') == 1
