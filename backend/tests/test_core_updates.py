"""Nach Updates suchen (`core/updates.py`): Abfrage der Registry, Versionsvergleich, Cache.

Kein echter Netzzugriff: jede Anfrage geht an einen `httpx.MockTransport`."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import itertools
import json
import time

import httpx
import pytest
from nodvard_deck.core import updates

TOKEN_URL = "https://ghcr.io/token"
TAGS_PATH = "/v2/nodvard/deck/tags/list"


def _no_release(_url: httpx.URL) -> httpx.Response:
    return httpx.Response(404)


def _registry(tags_pages: list[list[str]], *, token_status: int = 200, tags_status: int = 200, manifest: bytes | None = b'{"schemaVersion":2}',
              next_links: list[str | None] | None = None, calls: list[httpx.Request] | None = None,
              manifests: dict[str, bytes] | None = None,
              repository: str = "nodvard/deck", release=_no_release):
    """Attrappe für ghcr.io: Token, Tag-Seiten (Link-Header), Manifest; Dateien am Release beantwortet `release`."""
    tags_path = f"/v2/{repository}/tags/list"

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        url = request.url
        if url.host in updates.GRANT_HOSTS:
            return release(url)
        if url.host != "ghcr.io":
            raise AssertionError(f"fremder Host: {url}")
        if url.path == "/token":
            assert url.params["scope"] == f"repository:{repository}:pull"
            assert url.params["service"] == "ghcr.io"
            if token_status != 200:
                return httpx.Response(token_status, json={"errors": []})
            return httpx.Response(200, json={"token": "anon-token"})
        assert request.headers.get("authorization") == "Bearer anon-token"
        if url.path == tags_path:
            if tags_status != 200:
                return httpx.Response(tags_status, text="kaputt")
            page = int(url.params.get("page", "0"))
            headers = {}
            links = next_links if next_links is not None else [
                f'<{tags_path}?n=100&last=x&page={i + 1}>; rel="next"' if i + 1 < len(tags_pages) else None for i in range(len(tags_pages))
            ]
            if links[page]:
                headers["Link"] = links[page]
            return httpx.Response(200, json={"name": repository, "tags": tags_pages[page]}, headers=headers)
        if url.path.startswith(f"/v2/{repository}/manifests/"):
            assert "application/vnd.oci.image.index.v1+json" in request.headers["accept"]
            if manifests is not None:
                body = manifests.get(url.path.rsplit("/", 1)[-1])
                if body is None:
                    return httpx.Response(404)
                index_type = {"Content-Type": "application/vnd.oci.image.index.v1+json"}
                return httpx.Response(200, content=body, headers=index_type)
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


def test_official_image_belongs_to_the_edition():
    assert updates.is_official_image("ghcr.io/nodvard/dock", "nodvard/dock")
    assert not updates.is_official_image("ghcr.io/nodvard/dock")
    assert not updates.is_official_image("ghcr.io/nodvard/deck", "nodvard/dock")


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
    assert result["latest_digest"] == "sha256:" + hashlib.sha256(b'{"schemaVersion":2}').hexdigest()
    assert calls[0].url.path == "/token"
    assert calls[1].url.path == TAGS_PATH and calls[1].url.params["n"] == "100"
    assert calls[2].url.path == "/v2/nodvard/deck/manifests/0.6.0", "die neueste zuerst, für den Digest"
    assert [c.url.path for c in calls[3:]] == [
        "/nodvard/deck/releases/download/v0.6.0/freigabe.json", "/v2/nodvard/deck/manifests/0.5.0",
    ]
    assert result["approved"] is False and result["approved_key"] is None


@pytest.mark.asyncio
async def test_dock_searches_its_own_image(tmp_path):
    calls: list[httpx.Request] = []
    transport = _registry([["0.5.0", "0.6.0"]], calls=calls, repository="nodvard/dock")
    result = await updates.check(
        tmp_path, channel="stable", image="ghcr.io/nodvard/dock", current="0.5.0", repository="nodvard/dock", transport=transport,
    )
    assert result["source"] == "live" and result["latest"] == "0.6.0" and result["latest_digest"]
    assert result["official_image"] is True and result["official_image_name"] == "ghcr.io/nodvard/dock"
    assert result["release_notes_url"] == "https://github.com/nodvard/deck/blob/v0.6.0/CHANGELOG.md"
    assert [c.url.path for c in calls] == ["/token", "/v2/nodvard/dock/tags/list", "/v2/nodvard/dock/manifests/0.6.0",
                                           "/v2/nodvard/dock/manifests/0.5.0"]


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
    await _check(tmp_path, _registry([["0.6.0"]], calls=calls, release=_release(_files())))
    assert len(calls) == 7  # Token, Tags, Manifest, je zwei für Freigabe und Signatur (Weiterleitung)
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
async def test_cache_of_another_image_is_not_used(tmp_path):
    await _check(tmp_path, _registry([["0.6.0"]]))
    cached = updates.status(tmp_path, channel="stable", image=None, current="0.5.0", repository="nodvard/dock")
    assert cached["latest"] is None and cached["checked_at"] is None
    await _check(tmp_path, _registry([["0.6.0"]], tags_status=503))
    assert updates.status(tmp_path, channel="stable", image=None, current="0.5.0", repository="nodvard/dock")["error"] is None


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
    def __init__(self, image=None, build=None, image_info_path=None, edition=None):
        self.image, self.build, self.image_info_path, self.edition = image, build, image_info_path, edition


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


def test_running_version_of_dock_comes_from_the_file_only_for_its_own_image(tmp_path):
    info = tmp_path / "image-info.json"
    info.write_text(json.dumps({"version": "0.6.0-rc2"}), encoding="utf-8")
    assert updates.running_version(_Settings("ghcr.io/nodvard/dock", None, info, "dock")) == "0.6.0-rc2"
    assert updates.running_version(_Settings(updates.OFFICIAL_IMAGE, None, info, "dock")) == updates.__version__
    assert updates.running_version(_Settings("ghcr.io/nodvard/dock", None, info)) == updates.__version__


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


def test_repository_is_one_constant_and_matches_the_edition_table():
    import inspect

    from nodvard_deck import edition

    source = inspect.getsource(updates)
    assert source.count('"nodvard/deck"') == 1 and '"nodvard/dock"' not in source
    assert updates.REPOSITORY == edition.DECK.repository


# ---------------------------------------------------------------------------
# Arten der Änderungen (Annotation am Image-Index)
# ---------------------------------------------------------------------------


def _index(kinds: str | None) -> bytes:
    body: dict = {"schemaVersion": 2, "manifests": []}
    if kinds is not None:
        body["annotations"] = {updates.KINDS_ANNOTATION: kinds}
    return json.dumps(body).encode()


def test_documentation_is_a_known_update_kind():
    assert updates.kinds_of(_index("dokumentation")) == (["dokumentation"], True)


def _manifest_calls(calls: list[httpx.Request]) -> list[str]:
    return [c.url.path.rsplit("/", 1)[-1] for c in calls if "/manifests/" in c.url.path]


@pytest.mark.asyncio
async def test_kinds_of_all_versions_since_the_running_one_are_collected(tmp_path):
    calls: list[httpx.Request] = []
    manifests = {"0.6.0": _index("neu,behoben"), "0.5.1": _index("sicherheit"), "0.5.0": _index("neu")}
    result = await _check(tmp_path, _registry([["0.5.0", "0.5.1", "0.6.0"]], calls=calls, manifests=manifests))
    assert result["kinds"] == ["sicherheit", "behoben", "neu"] and result["kinds_complete"] is True
    assert _manifest_calls(calls) == ["0.6.0", "0.5.1", "0.5.0"]
    assert result["latest_digest"] == "sha256:" + hashlib.sha256(manifests["0.6.0"]).hexdigest()
    assert updates.status(tmp_path, channel="stable", image=None, current="0.5.0")["kinds"] == result["kinds"]


@pytest.mark.asyncio
@pytest.mark.parametrize("more_pages", [True, False], ids=["abgeschnitten", "vollständig"])
async def test_kinds_at_the_tag_page_limit(tmp_path, more_pages):
    tags = ["0.10.0"] + [f"0.10.0-rc{i}" for i in range(1, 1000)]
    pages = [tags[i:i + 100] for i in range(0, len(tags), 100)]
    if more_pages:
        pages.append(["0.9.0"])
    manifests = {"0.10.0": _index("behoben"), "0.9.0": _index("sicherheit")}
    calls: list[httpx.Request] = []
    result = await _check(tmp_path, _registry(pages, calls=calls, manifests=manifests), current="0.8.0")
    assert result["source"] == "live" and result["latest"] == "0.10.0"
    assert result["latest_digest"] == "sha256:" + hashlib.sha256(manifests["0.10.0"]).hexdigest()
    assert len([c for c in calls if c.url.path == TAGS_PATH]) == 10
    assert _manifest_calls(calls) == ["0.10.0"]
    for view in (result, updates.status(tmp_path, channel="stable", image=None, current="0.8.0")):
        assert view["kinds"] == ["behoben"]
        assert view["kinds_complete"] is (not more_pages)


@pytest.mark.asyncio
async def test_a_version_without_kinds_makes_them_incomplete(tmp_path):
    manifests = {"0.6.0": _index("behoben"), "0.5.1": _index(None)}
    result = await _check(tmp_path, _registry([["0.5.0", "0.5.1", "0.6.0"]], manifests=manifests))
    assert result["kinds"] == ["behoben"] and result["kinds_complete"] is False


@pytest.mark.asyncio
async def test_a_missing_manifest_makes_the_kinds_incomplete_and_keeps_the_digest_empty(tmp_path):
    result = await _check(tmp_path, _registry([["0.5.0", "0.6.0"]], manifests={}))
    assert result["available"] is True and result["latest_digest"] is None
    assert result["kinds"] == [] and result["kinds_complete"] is False


@pytest.mark.asyncio
async def test_at_most_ten_versions_are_asked_the_rest_stays_unknown(tmp_path):
    tags = ["0.5.0"] + [f"0.5.{i}" for i in range(1, 13)]
    manifests = {tag: _index("behoben") for tag in tags}
    calls: list[httpx.Request] = []
    result = await _check(tmp_path, _registry([tags], calls=calls, manifests=manifests))
    assert _manifest_calls(calls) == [f"0.5.{i}" for i in range(12, 2, -1)]
    assert result["kinds"] == ["behoben"] and result["kinds_complete"] is False


@pytest.mark.asyncio
async def test_without_an_update_there_are_no_kinds(tmp_path):
    manifests = {"0.5.0": _index("sicherheit"), "0.4.0": _index("neu")}
    result = await _check(tmp_path, _registry([["0.4.0", "0.5.0"]], manifests=manifests), current="0.5.0")
    assert result["available"] is False and result["kinds"] == [] and result["kinds_complete"] is True


@pytest.mark.asyncio
async def test_the_registry_cannot_tell_the_running_version_from_the_requests(tmp_path):
    tags = [["0.4.0", "0.5.0", "0.5.1", "0.6.0"]]
    manifests = {tag: _index("behoben") for tag in tags[0]}
    asked = []
    for current in ("0.4.0", "0.5.0", "0.5.1", "0.6.0"):
        calls: list[httpx.Request] = []
        (tmp_path / current).mkdir()
        await _check(tmp_path / current, _registry(tags, calls=calls, manifests=manifests), current=current)
        asked.append([(c.method, str(c.url), c.headers.get("user-agent")) for c in calls])
    assert all(requests == asked[0] for requests in asked)


@pytest.mark.asyncio
async def test_an_unknown_kind_shows_the_known_ones_but_counts_as_incomplete(tmp_path):
    manifests = {"0.6.0": _index("sicherheit,kritisch")}
    result = await _check(tmp_path, _registry([["0.5.0", "0.6.0"]], manifests=manifests))
    assert result["kinds"] == ["sicherheit"] and result["kinds_complete"] is False


@pytest.mark.asyncio
async def test_a_slow_manifest_leaves_the_kinds_incomplete_and_keeps_the_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "TOTAL_TIMEOUT_S", 0.3)
    latest = _index("behoben")
    digest = "sha256:" + hashlib.sha256(latest).hexdigest()
    registry = _registry(
        [["0.5.0", "0.5.1", "0.6.0"]], manifests={"0.6.0": latest},
        release=_release(_files(_grant(digest=digest))),
    )

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/0.5.1"):
            await asyncio.sleep(5)
        return registry.handler(request)

    started = time.monotonic()
    result = await _check(tmp_path, httpx.MockTransport(slow_handler))
    assert time.monotonic() - started < 2
    assert result["latest_digest"] == digest
    assert result["approved"] is True
    assert result["kinds"] == ["behoben"] and result["kinds_complete"] is False


@pytest.mark.asyncio
async def test_stable_skips_prereleases_beta_asks_them_too(tmp_path):
    manifests = {"0.6.0": _index("neu"), "0.6.0-rc1": _index("sicherheit")}
    tags = [["0.5.0", "0.6.0-rc1", "0.6.0"]]
    stable = await _check(tmp_path / "s", _registry(tags, manifests=manifests))
    beta = await _check(tmp_path / "b", _registry(tags, manifests=manifests), channel="beta")
    assert stable["kinds"] == ["neu"] and stable["kinds_complete"] is True
    assert beta["kinds"] == ["sicherheit", "neu"] and beta["kinds_complete"] is True


@pytest.mark.asyncio
async def test_beta_with_a_prerelease_as_latest_still_collects_kinds(tmp_path):
    manifests = {"0.6.0-rc2": _index("behoben"), "0.6.0-rc1": _index("sicherheit")}
    result = await _check(tmp_path, _registry([["0.5.0", "0.6.0-rc1", "0.6.0-rc2"]], manifests=manifests),
                          channel="beta")
    assert result["latest"] == "0.6.0-rc2"
    assert result["kinds"] == ["sicherheit", "behoben"] and result["kinds_complete"] is True


@pytest.mark.asyncio
async def test_kinds_found_for_another_running_version_are_unknown(tmp_path):
    manifests = {"0.6.0": _index("neu"), "0.5.1": _index("neu")}
    await _check(tmp_path, _registry([["0.5.0", "0.5.1", "0.6.0"]], manifests=manifests))
    after_update = updates.status(tmp_path, channel="stable", image=None, current="0.5.1")
    assert after_update["available"] is True
    assert after_update["kinds"] == [] and after_update["kinds_complete"] is False


def test_a_cache_without_kinds_counts_as_unknown(tmp_path):
    updates.save_cache(tmp_path, {"channel": "stable", "latest": "0.6.0", "checked_at": "2026-10-28T10:00:00+00:00"})
    result = updates.status(tmp_path, channel="stable", image=None, current="0.5.0")
    assert result["available"] is True and result["kinds"] == [] and result["kinds_complete"] is False


@pytest.mark.parametrize(("body", "kinds"), [
    (_index("sicherheit, behoben"), (["sicherheit", "behoben"], True)),
    (_index("neu,wartung"), (["neu"], False)),
    (_index(""), ([], False)),
    (_index(None), None),
    (b"kein json", None),
    (b"[" * 100_000 + b"]" * 100_000, None),
    (json.dumps({"annotations": {updates.KINDS_ANNOTATION: ["neu"]}}).encode(), None),
    (json.dumps({"annotations": "neu"}).encode(), None),
], ids=["zwei", "unbekannte-art", "leer", "ohne", "kaputt", "zu-tief", "liste", "keine-tabelle"])
def test_kinds_are_read_only_from_a_text_annotation(body, kinds):
    assert updates.kinds_of(body) == kinds


# ---------------------------------------------------------------------------
# Freigabe am Release
# ---------------------------------------------------------------------------

MANIFEST_DIGEST = "sha256:" + hashlib.sha256(b'{"schemaVersion":2}').hexdigest()
SIG = "ab" * 64
KEY_ID = "21fe31dfa154a261"


def _grant(**changes) -> dict:
    now = int(time.time())
    doc = {"fmt": 1, "repo": "ghcr.io/nodvard/deck", "version": "0.6.0", "digest": MANIFEST_DIGEST, "key_id": KEY_ID,
           "issued_at": now - 60, "expires_at": now + 86400}
    doc.update(changes)
    return doc


def _release(files: dict[str, bytes], *, via: str | None = "release-assets.githubusercontent.com"):
    """GitHub leitet Downloads am Release auf einen Speicher-Host weiter (`via`), der liefert die Datei."""

    def respond(url: httpx.URL) -> httpx.Response:
        name = url.path.rsplit("/", 1)[-1]
        if url.host == "github.com" and via is not None:
            return httpx.Response(302, headers={"location": f"https://{via}/blob/{name}?token=x"})
        body = files.get(name)
        return httpx.Response(404) if body is None else httpx.Response(200, content=body)

    return respond


def _files(grant: dict | None = None, sig: str = SIG + "\n") -> dict[str, bytes]:
    return {"freigabe.json": json.dumps(grant or _grant()).encode(), "freigabe.json.sig": sig.encode()}


@pytest.mark.asyncio
async def test_kinds_and_grant_are_kept_together_in_the_cache(tmp_path):
    manifests = {"0.6.0": _index("neu"), "0.5.1": _index("sicherheit"), "0.5.0": _index("behoben")}
    digest = "sha256:" + hashlib.sha256(manifests["0.6.0"]).hexdigest()
    grant = _grant(digest=digest)
    result = await _check(tmp_path, _registry(
        [["0.5.0", "0.5.1", "0.6.0"]], manifests=manifests, release=_release(_files(grant)),
    ))
    for view in (result, updates.status(tmp_path, channel="stable", image=None, current="0.5.0")):
        assert view["kinds"] == ["sicherheit", "neu"] and view["kinds_complete"] is True
        assert view["approved"] is True and view["approved_key"] == KEY_ID
        assert view["latest_digest"] == digest
    assert updates.cached_grant(tmp_path, channel="stable", version="0.6.0", digest=digest) == {
        "grant": grant, "sig": SIG,
    }


@pytest.mark.asyncio
async def test_dock_collects_its_own_kinds_without_a_grant(tmp_path):
    calls: list[httpx.Request] = []
    manifests = {"0.6.0": _index("behoben"), "0.5.1": _index("sicherheit")}
    result = await updates.check(
        tmp_path, channel="stable", image="ghcr.io/nodvard/dock", current="0.5.0", repository="nodvard/dock",
        transport=_registry([["0.5.1", "0.6.0"]], repository="nodvard/dock", manifests=manifests, calls=calls),
    )
    assert result["source"] == "live" and result["official_image"] is True
    assert result["kinds"] == ["sicherheit", "behoben"] and result["kinds_complete"] is True
    assert result["approved"] is False and result["approved_key"] is None
    assert [c.url.path for c in calls] == [
        "/token", "/v2/nodvard/dock/tags/list",
        "/v2/nodvard/dock/manifests/0.6.0", "/v2/nodvard/dock/manifests/0.5.1",
    ]
    assert updates.status(tmp_path, channel="stable", image=None, current="0.5.0")["kinds"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("via", [None, "objects.githubusercontent.com", "release-assets.githubusercontent.com"])
async def test_a_grant_at_the_release_makes_the_update_signed(tmp_path, via):
    calls: list[httpx.Request] = []
    result = await _check(tmp_path, _registry([["0.6.0"]], calls=calls, release=_release(_files(), via=via)))
    assert result["source"] == "live" and result["approved"] is True and result["approved_key"] == KEY_ID
    found = updates.cached_grant(tmp_path, channel="stable", version="0.6.0", digest=MANIFEST_DIGEST)
    assert found == {"grant": _grant(issued_at=found["grant"]["issued_at"], expires_at=found["grant"]["expires_at"]),
                     "sig": SIG}
    to_github = [c for c in calls if c.url.host != "ghcr.io"]
    assert to_github and all("authorization" not in c.headers for c in to_github), "der Token geht nur an ghcr.io"
    without_digest = await _check(tmp_path, _registry([["0.6.0"]], manifest=None, release=_release(_files())))
    assert without_digest["approved"] is False


@pytest.mark.parametrize("files", [
    _files(_grant(version="0.6.1")),
    _files(_grant(digest="sha256:" + "5e" * 64)),
    _files(_grant(repo="ghcr.io/nodvard/deck-fork")),
    _files(_grant(fmt=2)),
    _files(_grant(fmt=True)),
    _files({**_grant(), "note": "x"}),
    _files(_grant(key_id=KEY_ID.upper())),
    _files(_grant(issued_at=1.5)),
    _files(sig="ab" * 63),
    _files(sig="AB" * 64),
    _files(_grant(issued_at=0, expires_at=2**40)),
    {"freigabe.json": b"{kaputt", "freigabe.json.sig": SIG.encode()},
    {"freigabe.json": b'{"fmt":1,"fmt":1}', "freigabe.json.sig": SIG.encode()},
    {"freigabe.json": b"[" * 2000 + b"]" * 2000, "freigabe.json.sig": SIG.encode()},
    {"freigabe.json": json.dumps(_grant()).encode() + b" " * updates.GRANT_MAX_BYTES,
     "freigabe.json.sig": SIG.encode()},
    {"freigabe.json": json.dumps(_grant()).encode()},
    {"freigabe.json.sig": SIG.encode()},
], ids=["version", "digest", "repo", "fmt", "fmt_bool", "extra", "key_id", "float", "sig_short", "sig_upper", "forever",
        "json",
        "duplicate", "deep", "too_large", "no_sig", "no_grant"])
@pytest.mark.asyncio
async def test_an_unusable_grant_leaves_the_update_unsigned_but_the_check_counts(tmp_path, files):
    result = await _check(tmp_path, _registry([["0.6.0"]], release=_release(files)))
    assert result["source"] == "live" and result["latest"] == "0.6.0" and result["approved"] is False


@pytest.mark.asyncio
async def test_a_grant_outside_its_time_is_not_used(tmp_path):
    now = int(time.time())
    for grant in (_grant(expires_at=now - 1), _grant(issued_at=now + 3600)):
        result = await _check(tmp_path, _registry([["0.6.0"]], release=_release(_files(grant))))
        assert result["approved"] is False
        assert updates.cached_grant(tmp_path, channel="stable", version="0.6.0", digest=MANIFEST_DIGEST) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["https://evil.example/x", "http://objects.githubusercontent.com/x",
                                      "https://github.com.evil.example/x", "http://[::1", "",
                                      "https://objects.githubusercontent.com:8443/x",
                                      "https://user:" + "pw@objects.githubusercontent.com/x"])
async def test_a_redirect_away_from_github_is_not_followed(tmp_path, location):
    calls: list[httpx.Request] = []

    def respond(url: httpx.URL) -> httpx.Response:
        return httpx.Response(302, headers={"location": location})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host not in updates.GRANT_HOSTS | {"ghcr.io"}:
            calls.append(request)
            raise AssertionError(f"fremder Host: {request.url}")
        return registry.handler(request)

    registry = _registry([["0.6.0"]], release=respond)
    result = await _check(tmp_path, httpx.MockTransport(handler))
    assert result["source"] == "live" and result["approved"] is False and calls == []


@pytest.mark.asyncio
async def test_endless_redirects_stop(tmp_path):
    seen: list[str] = []

    def respond(url: httpx.URL) -> httpx.Response:
        seen.append(str(url))
        return httpx.Response(302, headers={"location": f"https://objects.githubusercontent.com/{len(seen)}"})

    result = await _check(tmp_path, _registry([["0.6.0"]], release=respond))
    assert result["approved"] is False and len(seen) == updates.MAX_REDIRECTS + 1


@pytest.mark.asyncio
async def test_other_editions_ask_for_no_grant(tmp_path):
    calls: list[httpx.Request] = []
    transport = _registry([["0.6.0"]], calls=calls, repository="nodvard/dock", release=_release(_files()))
    result = await updates.check(
        tmp_path, channel="stable", image="ghcr.io/nodvard/dock", current="0.5.0", repository="nodvard/dock", transport=transport,
    )
    assert result["latest"] == "0.6.0" and result["approved"] is False
    assert {c.url.host for c in calls} == {"ghcr.io"}
    assert updates.cached_grant(tmp_path, channel="stable", version="0.6.0", digest=MANIFEST_DIGEST) is None


@pytest.mark.asyncio
async def test_a_prerelease_never_counts_as_approved(tmp_path):
    files = _files(_grant(version="0.6.1-rc1"))
    result = await _check(tmp_path, _registry([["0.6.0", "0.6.1-rc1"]], release=_release(files)), channel="beta")
    assert result["latest"] == "0.6.1-rc1" and result["approved"] is False


@pytest.mark.asyncio
async def test_a_failed_fetch_keeps_the_approval_already_found_a_missing_one_drops_it(tmp_path):
    assert (await _check(tmp_path, _registry([["0.6.0"]], release=_release(_files()))))["approved"] is True

    def broken(url: httpx.URL) -> httpx.Response:
        raise httpx.ConnectError("github.com nicht erreichbar")

    kept = await _check(tmp_path, _registry([["0.6.0"]], release=broken))
    assert kept["source"] == "live" and kept["approved"] is True
    other = await _check(tmp_path, _registry([["0.6.0"]], manifest=b'{"schemaVersion":3}', release=broken))
    assert other["approved"] is False, "neuer Digest: die alte Freigabe passt nicht mehr"
    assert (await _check(tmp_path, _registry([["0.6.0"]], release=_release(_files()))))["approved"] is True
    assert (await _check(tmp_path, _registry([["0.6.0"]], release=_no_release)))["approved"] is False


@pytest.mark.asyncio
async def test_a_grant_counts_only_after_a_search_for_deck(tmp_path):
    updates.save_cache(tmp_path, {"channel": "stable", "repository": "nodvard/dock", "latest": "0.6.0", "latest_digest": MANIFEST_DIGEST,
                                  "grant": {"grant": _grant(), "sig": SIG}})
    assert updates.cached_grant(tmp_path, channel="stable", version="0.6.0", digest=MANIFEST_DIGEST) is None
    dock = updates.status(tmp_path, channel="stable", image="ghcr.io/nodvard/dock", current="0.5.0", repository="nodvard/dock")
    assert dock["latest"] == "0.6.0" and dock["approved"] is False

    def broken(url: httpx.URL) -> httpx.Response:
        raise httpx.ConnectError("github.com nicht erreichbar")

    assert (await _check(tmp_path, _registry([["0.6.0"]], release=broken)))["approved"] is False


def test_the_cached_grant_belongs_to_channel_version_and_digest(tmp_path):
    updates.save_cache(tmp_path, {"channel": "stable", "latest": "0.6.0", "latest_digest": MANIFEST_DIGEST,
                                  "grant": {"grant": _grant(), "sig": SIG}})
    assert updates.cached_grant(tmp_path, channel="stable", version="0.6.0", digest=MANIFEST_DIGEST) is not None
    assert updates.cached_grant(tmp_path, channel="beta", version="0.6.0", digest=MANIFEST_DIGEST) is None
    assert updates.cached_grant(tmp_path, channel="stable", version="0.6.1", digest=MANIFEST_DIGEST) is None
    assert updates.cached_grant(tmp_path, channel="stable", version="0.6.0", digest="sha256:" + "5e" * 64) is None
    assert updates.cached_grant(tmp_path, channel="stable", version="0.6.0", digest=None) is None
    assert updates.status(tmp_path, channel="stable", image=None, current="0.5.0")["approved"] is True
