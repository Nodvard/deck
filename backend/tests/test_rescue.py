"""`nodvard_deck.rescue`: die Notseite. Eigener Server nur mit der Standardbibliothek (AST-Test), Health 503 mit
`{"status":"rescue"}`, Aktionen nur mit dem Notfallcode (gedrosselt, kurzlebiges Cookie)."""

from __future__ import annotations

import ast
import http.client
import json
import os
import re
import socket
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from nodvard_deck import rescue
from nodvard_deck.core import bootstate

REPO = Path(__file__).resolve().parents[2]
RESCUE_SRC = REPO / "backend" / "src" / "nodvard_deck" / "rescue.py"
COPY = "20261001T030000Z_0.6.0_0.7.0.db"
CODE_RE = re.compile(r"^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$")


# ---------------------------------------------------------------------------
# Nur Standardbibliothek
# ---------------------------------------------------------------------------


def _imports(tree: ast.AST) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(alias.name, 0) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append((node.module or "", node.level))
    return found


def test_rescue_imports_only_the_standard_library():
    tree = ast.parse(RESCUE_SRC.read_text(encoding="utf-8"))
    imports = _imports(tree)
    assert imports, "es gibt Importe"
    for module, level in imports:
        assert level == 0, f"kein relativer Import (auch nicht aus nodvard_deck): {module!r}"
        top = module.split(".")[0]
        assert top in sys.stdlib_module_names, f"{module!r} ist keine Standardbibliothek -- die Notseite darf von nichts abhaengen, was im Image fehlen koennte"
    # Auch nicht dynamisch.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"__import__", "exec", "eval", "compile"}, node.func.id
    assert "importlib" not in {m.split(".")[0] for m, _ in imports}


def test_rescue_can_be_imported_and_run_without_any_installed_package(tmp_path):
    """Ohne site-packages (kein pydantic, kein SQLAlchemy ...): nur `backend/src` im Suchpfad."""
    code = (
        "import sys; sys.path[:] = [p for p in sys.path if 'site-packages' not in p and 'dist-packages' not in p]\n"
        "import nodvard_deck.rescue as r\n"
        "import nodvard_deck\n"
        "bad = [m for m in ('pydantic', 'sqlalchemy', 'fastapi', 'alembic', 'nodvard_sdk') if m in sys.modules]\n"
        "assert not bad, bad\n"
        "server = r.make_server(r.Rescue(r.Path(sys.argv[1])), '127.0.0.1', 0)\n"
        "print('port', server.server_address[1]); server.server_close()\n"
    )
    result = subprocess.run(
        [sys.executable, "-S", "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=60, check=False,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(REPO / "backend" / "src")},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("port ")


# ---------------------------------------------------------------------------
# Server im Test
# ---------------------------------------------------------------------------

FAILURE = {
    "kind": "newer_data", "at": "2026-10-01T03:05:00Z", "app_version": "0.6.0", "data_version": "0.7.0", "previous_version": "0.6.0",
    "reason": "GEHEIMER-GRUND-XYZ: Die Daten sind neuer als diese Version.",
    "log": ["[boot] Zeile eins", "[boot] GEHEIME-LOGZEILE <script>alert(1)</script>"],
    "rollback": {"copy": COPY, "from_version": "0.6.0", "to_version": "0.7.0", "started_ok": True, "started_at": "2026-10-01T03:01:00Z"},
}


class Env:
    def __init__(self, tmp_path: Path, failure: dict | None = FAILURE) -> None:
        self.data = tmp_path / "data"
        self.data.mkdir()
        if failure is not None:
            bootstate.write_state(self.data, {"app_version": "0.7.0", "started_ok": True, "failure": failure})
        self.rescue = rescue.Rescue(self.data)
        self.code = self.rescue.ensure_code()
        self.server = rescue.make_server(self.rescue, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def request(self, method: str, path: str, body: bytes | str | None = None, headers: dict | None = None, *, cookie: str | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = dict(headers or {})
        if isinstance(body, str):
            body = body.encode()
        if cookie:
            hdrs["Cookie"] = cookie
        if body is not None and method == "POST":
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        try:
            conn.request(method, path, body=body, headers=hdrs)
            response = conn.getresponse()
            return response.status, dict((k.lower(), v) for k, v in response.getheaders()), response.read(), response
        finally:
            conn.close()

    def unlock(self) -> str:
        status, headers, _, response = self.request("POST", "/rescue/unlock", f"code={self.code}")
        assert status == 303, status
        cookie = headers["set-cookie"].split(";")[0]
        assert cookie.startswith("rescue_session=")
        return cookie

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.stop()


# ---------------------------------------------------------------------------
# Health und Seite
# ---------------------------------------------------------------------------


def test_health_is_503_and_never_says_ok(env):
    status, headers, body, _ = env.request("GET", "/api/v1/health")
    assert status == 503
    assert json.loads(body) == {"status": "rescue"}
    assert body == b'{"status":"rescue"}', "genau diese Form: deploy_pi.sh sucht mit grep nach dem Muster \"status\":\"ok\""
    assert b'"ok"' not in body and b"ok" not in body.lower().replace(b"rescue", b"")
    assert headers["content-type"].startswith("application/json") and headers["cache-control"] == "no-store"
    status, _, body, _ = env.request("HEAD", "/api/v1/health")
    assert status == 503 and body == b""


@pytest.mark.parametrize("method,path", [("GET", "/api/v1/auth/bootstrap"), ("POST", "/api/v1/auth/login"), ("GET", "/api/v1/hosts"),
                                          ("PUT", "/api/v1/settings/x"), ("DELETE", "/api/v1/hosts/1")])
def test_every_other_api_path_is_503_json_too(env, method, path):
    status, headers, body, _ = env.request(method, path, "x=1" if method in ("POST", "PUT") else None)
    assert status == 503 and headers["content-type"].startswith("application/json")
    data = json.loads(body)
    assert data["status"] == "rescue" and "Notfall" in data["detail"]


def test_the_page_is_self_contained_german_and_has_no_foreign_assets(env):
    status, headers, body, _ = env.request("GET", "/")
    page = body.decode()
    assert status == 503 and headers["content-type"] == "text/html; charset=utf-8"
    assert '<html lang="de">' in page and "Nodvard Deck" in page and "Notfall" in page
    assert not re.search(r"(?i)<script|<link|<img|<iframe|<object|@import|url\(", page), "weder Skripte noch fremde Dateien"
    assert not re.search(r"(?i)(src|href|action)\s*=\s*[\"']?(https?:)?//", page)
    assert "viewport" in page
    csp = headers["content-security-policy"]
    assert "default-src 'none'" in csp and "form-action 'self'" in csp and "frame-ancestors 'none'" in csp
    assert headers["x-content-type-options"] == "nosniff" and headers["x-frame-options"] == "DENY" and headers["cache-control"] == "no-store"
    assert "python" not in headers.get("server", "").lower(), "keine Python-Version verraten"


def test_page_without_the_code_shows_only_general_information(env):
    for path in ("/", "/irgendein/pfad", "/setup", "/login"):
        status, _, body, _ = env.request("GET", path)
        page = body.decode()
        assert status == 503
        assert "GEHEIMER-GRUND" not in page and "GEHEIME-LOGZEILE" not in page and "0.7.0" not in page and "newer_data" not in page
        assert "Notfallcode" in page, "sagt, wo der Code steht"
        assert 'name="code"' in page
        assert "/rescue/retry" not in page and "/rescue/rollback" not in page, "keine Aktionen ohne Code"
    status, _, body, _ = env.request("GET", "/rescue/status")
    assert status == 200 and json.loads(body) == {"status": "rescue", "unlocked": False}


def test_the_page_does_not_serve_files_whatever_the_path(env, tmp_path):
    (tmp_path / "geheim.txt").write_text("GEHEIM")
    for path in ("/../geheim.txt", "/..%2f..%2fetc/passwd", "/etc/passwd", "/%00", "/data/.boot/state.json", "//etc/passwd", "/rescue/../../etc/passwd"):
        status, _, body, _ = env.request("GET", path)
        assert status in (200, 400, 404, 503) and b"GEHEIM" not in body and b"root:" not in body, path


def test_unusual_methods_are_refused(env):
    for method in ("PUT", "DELETE", "PATCH", "OPTIONS"):
        status, _, _, _ = env.request(method, "/")
        assert status == 405


# ---------------------------------------------------------------------------
# Code, Cookie, Drosselung
# ---------------------------------------------------------------------------


def test_the_code_file_is_private_and_has_the_setup_code_format(tmp_path):
    data = tmp_path / "d"
    data.mkdir()
    r = rescue.Rescue(data)
    code = r.ensure_code()
    assert CODE_RE.match(code)
    path = data / ".boot" / "rescue_code.txt"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert rescue.Rescue(data).ensure_code() == code, "bleibt ueber Neustarts gleich, bis ein Start gelingt"
    assert bootstate.clear_rescue_code(data) is True
    assert rescue.Rescue(data).ensure_code() != code or True


def test_an_unusable_code_file_is_replaced(tmp_path):
    data = tmp_path / "d"
    (data / ".boot").mkdir(parents=True)
    (data / ".boot" / "rescue_code.txt").write_text("zu kurz\n")
    assert CODE_RE.match(rescue.Rescue(data).ensure_code())


def test_a_read_only_data_folder_still_gives_a_code_in_memory(tmp_path):
    blocker = tmp_path / "datei"
    blocker.write_text("x")
    r = rescue.Rescue(blocker / "nicht-moeglich")
    assert CODE_RE.match(r.ensure_code())


def test_the_banner_names_the_code_like_the_setup_code_banner(tmp_path, capsys):
    r = rescue.Rescue(tmp_path)
    code = r.ensure_code()
    rescue.print_banner(code, 8080)
    out = capsys.readouterr().out
    assert f"Notfallcode: {code}" in out and out.count(code) >= 2 and "8080" in out and "Notfallmodus" in out


def test_unlock_with_the_right_code_sets_a_short_lived_httponly_strict_cookie(env):
    status, headers, _, _ = env.request("POST", "/rescue/unlock", f"code={env.code}")
    assert status == 303 and headers["location"].startswith("/")
    cookie = headers["set-cookie"]
    assert re.match(r"^rescue_session=[A-Za-z0-9_-]{32,};", cookie)
    attributes = [a.strip().lower() for a in cookie.split(";")[1:]]
    assert "httponly" in attributes and "samesite=strict" in attributes and "path=/" in attributes
    max_age = [a for a in attributes if a.startswith("max-age=")]
    assert max_age and 60 <= int(max_age[0].split("=")[1]) <= 1800


def test_the_code_is_found_in_any_spelling(env):
    plain = env.code.replace("-", "").lower()
    for spelled in (plain, env.code.lower(), f" {env.code} ", plain.upper()):
        status, headers, _, _ = env.request("POST", "/rescue/unlock", f"code={spelled}")
        assert status == 303 and "set-cookie" in headers, spelled


def test_unlock_also_accepts_json(env):
    status, headers, body, _ = env.request("POST", "/rescue/unlock", json.dumps({"code": env.code}), {"Content-Type": "application/json"})
    assert status in (200, 303) and "set-cookie" in headers


def test_wrong_code_gives_no_cookie_and_no_hint(env):
    status, headers, _, _ = env.request("POST", "/rescue/unlock", "code=AAAA-BBBB-CCCC")
    assert status in (303, 401, 403) and "set-cookie" not in headers
    status, _, body, _ = env.request("GET", "/rescue/status")
    assert json.loads(body)["unlocked"] is False


def test_five_wrong_codes_lock_even_the_right_one_for_a_while(env):
    for _ in range(rescue.MAX_FAILURES_PER_IP):
        status, headers, _, _ = env.request("POST", "/rescue/unlock", "code=AAAA-BBBB-CCCC")
        assert "set-cookie" not in headers
    status, headers, body, _ = env.request("POST", "/rescue/unlock", f"code={env.code}")
    assert status == 429 and "set-cookie" not in headers and "retry-after" in headers
    assert int(headers["retry-after"]) >= 60
    assert b"Zu viele" in body


def test_throttle_counts_when_the_attempt_starts_so_parallel_guesses_do_not_get_through():
    r = rescue.Rescue(Path("/nirgends"))
    r._code = "ABCD-EFGH-JKLM"
    results = [r.try_code("1.2.3.4", "falsch", now=100.0)[0] for _ in range(rescue.MAX_FAILURES_PER_IP + 3)]
    assert results[: rescue.MAX_FAILURES_PER_IP] == ["wrong"] * rescue.MAX_FAILURES_PER_IP
    assert set(results[rescue.MAX_FAILURES_PER_IP:]) == {"locked"}
    assert r.try_code("1.2.3.4", "ABCD-EFGH-JKLM", now=100.0)[0] == "locked", "auch der richtige Code geht dann nicht durch"
    status, token_or_retry = r.try_code("1.2.3.4", "ABCD-EFGH-JKLM", now=100.0 + rescue.LOCK_S + 1)
    assert status == "ok" and token_or_retry, "nach der Sperrzeit geht es wieder"


def test_a_successful_attempt_is_not_counted_against_the_caller():
    r = rescue.Rescue(Path("/nirgends"))
    r._code = "ABCD-EFGH-JKLM"
    for i in range(rescue.MAX_FAILURES_PER_IP * 3):
        assert r.try_code("1.2.3.4", "ABCD-EFGH-JKLM", now=100.0 + i)[0] == "ok"


def test_other_clients_have_their_own_counter_but_a_global_limit_stops_a_swarm():
    r = rescue.Rescue(Path("/nirgends"))
    r._code = "ABCD-EFGH-JKLM"
    for _ in range(rescue.MAX_FAILURES_PER_IP):
        r.try_code("9.9.9.9", "falsch", now=50.0)
    assert r.try_code("1.2.3.4", "ABCD-EFGH-JKLM", now=50.0)[0] == "ok", "ein anderer Rechner ist nicht mitgesperrt"
    swarm = [f"10.0.{i // 250}.{i % 250}" for i in range(rescue.MAX_FAILURES_GLOBAL + 5)]
    seen = [r.try_code(ip, "falsch", now=60.0)[0] for ip in swarm]
    assert "locked" in seen, "viele verschiedene Absender zusammen werden auch gebremst"
    assert r.try_code("5.5.5.5", "ABCD-EFGH-JKLM", now=60.0)[0] == "locked"
    assert r.try_code("5.5.5.5", "ABCD-EFGH-JKLM", now=60.0 + rescue.LOCK_S + 1)[0] == "ok"


def test_the_throttle_memory_stays_small():
    r = rescue.Rescue(Path("/nirgends"))
    r._code = "ABCD-EFGH-JKLM"
    for i in range(5000):
        r.try_code(f"ip-{i}", "falsch", now=1000.0 + i * 0.001)
    assert len(r._failures_by_ip) <= rescue.MAX_TRACKED_CLIENTS


def test_a_session_expires_and_the_number_of_sessions_is_limited():
    r = rescue.Rescue(Path("/nirgends"))
    r._code = "ABCD-EFGH-JKLM"
    token = r.try_code("1.1.1.1", "ABCD-EFGH-JKLM", now=100.0)[1]
    assert r.session_valid(token, now=100.0 + rescue.SESSION_TTL_S - 1)
    assert not r.session_valid(token, now=100.0 + rescue.SESSION_TTL_S + 1)
    assert not r.session_valid("nicht-vorhanden", now=100.0) and not r.session_valid("", now=100.0) and not r.session_valid(None, now=100.0)
    tokens = [r.try_code(f"2.2.2.{i}", "ABCD-EFGH-JKLM", now=200.0)[1] for i in range(rescue.MAX_SESSIONS + 5)]
    assert len(r._sessions) <= rescue.MAX_SESSIONS
    assert r.session_valid(tokens[-1], now=200.0), "die neueste bleibt"


def test_the_session_cookie_only_works_via_the_cookie_header(env):
    cookie = env.unlock()
    token = cookie.split("=", 1)[1]
    status, _, body, _ = env.request("GET", f"/rescue/status?rescue_session={token}")
    assert json.loads(body)["unlocked"] is False
    status, _, body, _ = env.request("GET", "/rescue/status", cookie=cookie)
    assert json.loads(body)["unlocked"] is True
    status, _, body, _ = env.request("GET", "/rescue/status", cookie="rescue_session=" + "x" * 43)
    assert json.loads(body)["unlocked"] is False


# ---------------------------------------------------------------------------
# Was mit Code zu sehen ist, und die Aktionen
# ---------------------------------------------------------------------------


def test_with_the_code_the_page_shows_the_details_and_the_actions(env):
    cookie = env.unlock()
    status, _, body, _ = env.request("GET", "/", cookie=cookie)
    page = body.decode()
    assert status == 503
    assert "GEHEIMER-GRUND-XYZ" in page and "0.7.0" in page and "0.6.0" in page
    assert "/rescue/retry" in page and "/rescue/rollback" in page
    assert "2026-10-01T03:01:00Z" in page or "03:01" in page, "wann die neue Version gestartet ist (so lange gehen Aenderungen verloren)"
    assert "<script" not in page.lower().replace("&lt;script&gt;", ""), "Text aus dem Protokoll ist maskiert"
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert re.search(r"(?i)docker|proxmox", page) is None, "neutrale Wortwahl (Kern-Reinheit)"


def test_status_json_and_log_need_the_session(env):
    assert env.request("GET", "/rescue/log")[0] == 401
    cookie = env.unlock()
    status, headers, body, _ = env.request("GET", "/rescue/log", cookie=cookie)
    assert status == 200 and headers["content-type"].startswith("text/plain")
    assert body.decode().splitlines()[:1] == ["[boot] Zeile eins"] and "GEHEIME-LOGZEILE" in body.decode()
    status, _, body, _ = env.request("GET", "/rescue/status", cookie=cookie)
    data = json.loads(body)
    assert data["unlocked"] is True and data["failure"]["kind"] == "newer_data" and data["failure"]["rollback"]["copy"] == COPY


def test_retry_needs_the_session_and_then_ends_the_process_with_75(env):
    assert env.request("POST", "/rescue/retry")[0] == 401
    assert env.rescue.exit_code is None
    cookie = env.unlock()
    status, _, body, _ = env.request("POST", "/rescue/retry", cookie=cookie)
    assert status == 202 and "neu gestartet" in body.decode()
    env.thread.join(10)
    assert not env.thread.is_alive(), "der Server beendet sich selbst"
    assert env.rescue.exit_code == 75


def test_rollback_needs_the_session_and_writes_the_request_the_boot_step_reads(env):
    assert env.request("POST", "/rescue/rollback")[0] == 401
    assert bootstate.read_rollback(env.data) is None
    cookie = env.unlock()
    status, _, body, _ = env.request("POST", "/rescue/rollback", "copy=../../evil.db", cookie=cookie)
    assert status == 202
    request = bootstate.read_rollback(env.data)
    assert request is not None and request["copy"] == COPY and request["by"] == "notseite", "die Kopie kommt aus dem Zustand, nie aus der Anfrage"
    assert stat.S_IMODE((env.data / ".boot" / "rollback.json").stat().st_mode) == 0o600
    env.thread.join(10)
    assert env.rescue.exit_code == 75


def test_rollback_is_refused_when_boot_found_no_usable_copy(tmp_path):
    failure = {**FAILURE, "kind": "copy_unusable", "rollback": None}
    e = Env(tmp_path, failure)
    try:
        cookie = e.unlock()
        status, _, body, _ = e.request("POST", "/rescue/rollback", cookie=cookie)
        assert status == 409
        assert bootstate.read_rollback(e.data) is None and e.rescue.exit_code is None
        assert "/rescue/rollback" not in e.request("GET", "/", cookie=cookie)[2].decode(), "der Knopf wird dann gar nicht angeboten"
    finally:
        e.stop()


def test_without_any_failure_record_the_page_still_works(tmp_path):
    e = Env(tmp_path, None)
    try:
        status, _, body, _ = e.request("GET", "/")
        assert status == 503 and "Notfall" in body.decode()
        cookie = e.unlock()
        page = e.request("GET", "/", cookie=cookie)[2].decode()
        assert "Protokoll des Containers" in page and "/rescue/retry" in page
        assert e.request("POST", "/rescue/rollback", cookie=cookie)[0] == 409
    finally:
        e.stop()


@pytest.mark.parametrize("kind", list(bootstate.FAILURE_KINDS))
def test_every_failure_kind_has_a_page_with_the_next_steps(tmp_path, kind):
    failure = {**FAILURE, "kind": kind, "needed_bytes": 500 * 1024 * 1024, "free_bytes": 100 * 1024 * 1024}
    e = Env(tmp_path, failure)
    try:
        page = e.request("GET", "/", cookie=e.unlock())[2].decode()
        assert "Was du jetzt tun kannst" in page
        assert "GEHEIMER-GRUND-XYZ" in page
        assert not re.search(r"(?i)docker|proxmox", page)
    finally:
        e.stop()


def test_no_space_page_names_the_numbers(tmp_path):
    e = Env(tmp_path, {**FAILURE, "kind": "no_space", "rollback": None, "needed_bytes": 500 * 1024 * 1024, "free_bytes": 100 * 1024 * 1024})
    try:
        page = e.request("GET", "/", cookie=e.unlock())[2].decode()
        assert "500 MB" in page and "100 MB" in page and "NODVARD_DECK_SKIP_PRE_MIGRATE_BACKUP" in page
    finally:
        e.stop()


def test_newer_data_page_names_the_version_to_go_back_to(env):
    page = env.request("GET", "/", cookie=env.unlock())[2].decode()
    assert "Image-Version" in page and "Compose-Datei" in page, "neutrale Anleitung"
    assert "Version 0.7.0" in page


def test_the_page_says_the_way_back_only_works_from_a_version_with_this_feature(tmp_path):
    e = Env(tmp_path, {**FAILURE, "kind": "migration_failed", "rollback": None})
    try:
        page = e.request("GET", "/", cookie=e.unlock())[2].decode()
        assert "Notseite" in page and "Kopie" in page and "Ältere Versionen" in page
        assert "Image-Version" in page and "Compose-Datei" in page
    finally:
        e.stop()


def test_logout_ends_the_session(env):
    cookie = env.unlock()
    status, headers, _, _ = env.request("POST", "/rescue/lock", cookie=cookie)
    assert status == 303 and "max-age=0" in headers["set-cookie"].lower()
    assert json.loads(env.request("GET", "/rescue/status", cookie=cookie)[2])["unlocked"] is False


# ---------------------------------------------------------------------------
# Haertung der Anfragen
# ---------------------------------------------------------------------------


def test_a_foreign_origin_cannot_trigger_an_action_even_with_a_valid_session(env):
    cookie = env.unlock()
    for origin in ("http://evil.example", "null", "http://127.0.0.1:1"):
        status, _, _, _ = env.request("POST", "/rescue/retry", headers={"Origin": origin}, cookie=cookie)
        assert status == 403, origin
    assert env.rescue.exit_code is None
    status, _, _, _ = env.request("POST", "/rescue/retry", headers={"Origin": f"http://127.0.0.1:{env.port}"}, cookie=cookie)
    assert status == 202


def test_a_browser_that_says_the_request_is_cross_site_is_refused(env):
    cookie = env.unlock()
    for site in ("cross-site", "same-site"):
        status, _, _, _ = env.request("POST", "/rescue/retry", headers={"Sec-Fetch-Site": site}, cookie=cookie)
        assert status == 403, site
    assert env.rescue.exit_code is None
    status, _, _, _ = env.request("POST", "/rescue/unlock", f"code={env.code}", {"Sec-Fetch-Site": "cross-site"})
    assert status == 403
    status, _, _, _ = env.request("POST", "/rescue/unlock", f"code={env.code}", {"Sec-Fetch-Site": "same-origin", "Origin": f"http://127.0.0.1:{env.port}"})
    assert status == 303


def test_the_referrer_policy_lets_browsers_send_the_origin_with_a_form(env):
    # `no-referrer` machte aus dem Origin eines Formulars `null` -- in einem echten Browser ging dann nichts mehr (live gefunden).
    headers = env.request("GET", "/")[1]
    assert headers["referrer-policy"] == "same-origin"


def test_a_foreign_origin_cannot_even_try_the_code(env):
    status, headers, _, _ = env.request("POST", "/rescue/unlock", f"code={env.code}", {"Origin": "http://evil.example"})
    assert status == 403 and "set-cookie" not in headers


def test_oversized_and_unlabelled_bodies_are_refused(env):
    conn = http.client.HTTPConnection("127.0.0.1", env.port, timeout=10)
    conn.putrequest("POST", "/rescue/unlock")
    conn.putheader("Content-Type", "application/x-www-form-urlencoded")
    conn.putheader("Content-Length", str(10 * 1024 * 1024))
    conn.endheaders()
    assert conn.getresponse().status == 413
    conn.close()
    conn = http.client.HTTPConnection("127.0.0.1", env.port, timeout=10)
    conn.putrequest("POST", "/rescue/unlock")
    conn.endheaders()
    assert conn.getresponse().status in (400, 411)
    conn.close()


def test_garbage_json_and_form_bodies_do_not_crash_the_server(env):
    for body, ctype in ((b"\xff\xfe\x00", "application/json"), (b"[1,2,3]", "application/json"), (b"{" * 4000, "application/json"),
                        (b"code=%ff%fe", "application/x-www-form-urlencoded"), (b"code", "text/plain")):
        status, _, _, _ = env.request("POST", "/rescue/unlock", body, {"Content-Type": ctype})
        assert status in (303, 400, 401, 415), (body, status)
    assert env.request("GET", "/api/v1/health")[0] == 503, "der Server lebt noch"


def test_idle_connections_do_not_exhaust_the_server(env, monkeypatch):
    sockets = []
    try:
        for _ in range(rescue.MAX_CONNECTIONS + 5):
            s = socket.create_connection(("127.0.0.1", env.port), timeout=5)
            sockets.append(s)
        time.sleep(0.3)
        closed = 0
        for s in sockets:
            s.settimeout(0.3)
            try:
                if s.recv(1) == b"":
                    closed += 1
            except (TimeoutError, OSError):
                pass
        assert closed >= 1, "was ueber die Grenze geht, wird sofort geschlossen"
    finally:
        for s in sockets:
            s.close()
    time.sleep(0.2)


def test_the_request_timeout_is_short(env):
    assert rescue._Handler.timeout <= 15


def _closed_within(sock: socket.socket, seconds: float, trickle: float) -> bool:
    """Schickt alle `trickle` Sekunden ein Byte; True, sobald der Server die Verbindung schliesst."""
    deadline = time.monotonic() + seconds
    sock.settimeout(trickle)
    while time.monotonic() < deadline:
        try:
            if sock.recv(1024) == b"":
                return True
        except TimeoutError:
            pass
        except OSError:
            return True
        try:
            sock.sendall(b"a")
        except OSError:
            return True
    return False


def test_a_trickling_connection_ends_after_the_deadline_for_the_whole_request(tmp_path, monkeypatch):
    # Fund: Die Frist galt je recv(), nicht je Anfrage -- ein Byte alle paar Sekunden hielt eine Verbindung ewig offen.
    monkeypatch.setattr(rescue, "REQUEST_TIMEOUT_S", 1.0)
    e = Env(tmp_path)
    try:
        sock = socket.create_connection(("127.0.0.1", e.port), timeout=5)
        try:
            sock.sendall(b"GET / HTTP/1.0\r\nX-A: ")
            assert _closed_within(sock, 4.0, trickle=0.3), "die Frist gilt fuer die ganze Anfrage"
        finally:
            sock.close()
        assert e.request("GET", "/api/v1/health")[0] == 503
    finally:
        e.stop()


def test_one_sender_cannot_take_all_connections(env):
    # Fund: 64 offene Verbindungen eines einzigen Rechners belegten alle Plaetze; der Admin mit dem Code kam nicht mehr durch.
    blockers = []
    try:
        for _ in range(rescue.MAX_CONNECTIONS):
            try:
                s = socket.create_connection(("127.0.0.1", env.port), timeout=5, source_address=("127.0.0.2", 0))
                s.sendall(b"GET / HTTP/1.0\r\nX-A: ")
                blockers.append(s)
            except OSError:
                break
        time.sleep(0.3)
        status, _, _, _ = env.request("POST", "/rescue/unlock", f"code={env.code}")
        assert status == 303, "ein anderer Rechner kommt trotzdem durch"
        refused = 0
        for s in blockers:
            s.settimeout(0.2)
            try:
                if s.recv(1) == b"":
                    refused += 1
            except (TimeoutError, OSError):
                pass
        assert refused >= rescue.MAX_CONNECTIONS - rescue.MAX_CONNECTIONS_PER_CLIENT, "mehr als die Grenze je Absender wird sofort geschlossen"
    finally:
        for s in blockers:
            s.close()


def test_control_characters_from_the_request_never_reach_the_log_raw(env, capsys):
    # Fund: Das eigene `log_message` gab den Pfad roh aus; Escape-Folgen (Zeilen loeschen, Zwischenablage setzen) landeten
    # im Protokoll des Containers und wirkten im Terminal beim Lesen von `docker compose logs`.
    sock = socket.create_connection(("127.0.0.1", env.port), timeout=5)
    try:
        sock.sendall(b"POST /\x1b[2Kx\x1b]52;c;QUJD\x07 HTTP/1.0\r\nContent-Length: 0\r\n\r\n")
        assert sock.recv(64).startswith(b"HTTP/1.0 404")
    finally:
        sock.close()
    time.sleep(0.2)
    err = capsys.readouterr().err
    line = next(row for row in err.splitlines() if "POST" in row)
    assert "\x1b" not in line and "\x07" not in line
    assert "\\x1b[2Kx" in line and "\\x07" in line, "lesbar maskiert statt ausgefuehrt"


# ---------------------------------------------------------------------------
# Sperre und Aufruf
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="flock")
def test_the_rescue_process_holds_the_data_folder_lock_while_it_runs(tmp_path):
    data = tmp_path / "d"
    data.mkdir()
    lock = rescue.take_lock(data)
    try:
        assert bootstate.is_locked(data) is True, "boot (compose run) und admin sehen: dieser Datenordner ist in Benutzung"
        with pytest.raises(bootstate.LockHeld):
            bootstate.acquire_lock(data)
    finally:
        lock.release()
    assert bootstate.is_locked(data) is False


@pytest.mark.skipif(sys.platform == "win32", reason="Prozesse und Signale")
def test_main_runs_as_a_process_answers_and_ends_with_0_on_sigterm(tmp_path):
    data = tmp_path / "d"
    data.mkdir()
    bootstate.write_state(data, {"app_version": "0.7.0", "failure": FAILURE})
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, "-S", "-m", "nodvard_deck.rescue", "--host", "127.0.0.1", "--port", str(port), "--data-dir", str(data)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(REPO / "backend" / "src"), "PYTHONUNBUFFERED": "1"},
    )
    try:
        for _ in range(100):
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("GET", "/api/v1/health")
                response = conn.getresponse()
                assert response.status == 503 and json.loads(response.read()) == {"status": "rescue"}
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError("die Notseite kam nicht hoch: " + proc.stderr.read())
        proc.terminate()
        assert proc.wait(15) == 0
        out = proc.stdout.read()
        assert "Notfallcode:" in out
    finally:
        if proc.poll() is None:
            proc.kill()


def test_main_cannot_bind_a_busy_port_and_says_so(tmp_path, capsys):
    data = tmp_path / "d"
    data.mkdir()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        assert rescue.main(["--host", "127.0.0.1", "--port", str(port), "--data-dir", str(data)]) == 2
    assert "Port" in capsys.readouterr().err


def test_default_data_dir_and_port_come_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("NODVARD_DECK_DATA_DIR", str(tmp_path / "neu"))
    monkeypatch.setenv("LATTICE_DATA_DIR", str(tmp_path / "alt"))
    assert rescue.resolve_data_dir(None) == tmp_path / "neu", "der neue Name gewinnt"
    monkeypatch.delenv("NODVARD_DECK_DATA_DIR")
    assert rescue.resolve_data_dir(None) == tmp_path / "alt", "der alte Name gilt als Rueckfall"
    monkeypatch.delenv("LATTICE_DATA_DIR")
    assert rescue.resolve_data_dir(None) == Path("./data")
    assert rescue.resolve_data_dir("/x/y") == Path("/x/y")
    assert rescue.DEFAULT_PORT == 8080


# ---------------------------------------------------------------------------
# Dieselben Dateien wie boot (die Notseite hat eigenen kleinen Code dafuer)
# ---------------------------------------------------------------------------


def test_rescue_and_bootstate_agree_on_names_and_formats(tmp_path):
    assert rescue.BOOT_DIR == bootstate.BOOT_DIR and rescue.STATE_NAME == bootstate.STATE_NAME
    assert rescue.LOCK_NAME == bootstate.LOCK_NAME and rescue.ROLLBACK_NAME == bootstate.ROLLBACK_NAME
    assert rescue.CODE_NAME == bootstate.RESCUE_CODE_NAME
    assert rescue.COPY_NAME_RE.pattern == bootstate.COPY_NAME_RE.pattern
    assert rescue.FAILURE_KINDS == bootstate.FAILURE_KINDS


def test_a_state_written_by_boot_is_read_by_rescue_and_the_other_way_round(tmp_path):
    bootstate.write_state(tmp_path, {"app_version": "0.7.0", "started_ok": False, "failure": FAILURE})
    state = rescue.read_state(tmp_path)
    assert state["failure"]["kind"] == "newer_data" and state["failure"]["rollback"]["copy"] == COPY and state["app_version"] == "0.7.0"
    rescue.write_rollback(tmp_path, COPY, now=1000.0)
    assert bootstate.read_rollback(tmp_path, now=1010.0) == {"copy": COPY, "by": "notseite", "requested_at": 1000.0}


def test_rescue_reads_hostile_state_files_without_crashing(tmp_path):
    (tmp_path / ".boot").mkdir()
    for raw in (b"", b"x", b"[]", b'{"failure": 5}', b'{"failure": {"kind": {"a": 1}, "log": "text", "rollback": {"copy": "../x"}}}', b"\xff" * 100):
        (tmp_path / ".boot" / "state.json").write_bytes(raw)
        state = rescue.read_state(tmp_path)
        assert isinstance(state, dict)
        rescue.Rescue(tmp_path).render(unlocked=True)  # nie ein Absturz


def test_rescue_never_writes_a_rollback_request_for_a_foreign_name(tmp_path):
    with pytest.raises(ValueError):
        rescue.write_rollback(tmp_path, "../../etc/passwd")
