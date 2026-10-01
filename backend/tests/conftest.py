"""Gemeinsame Fixtures.

`db_session` und `client` laufen gegen eine **In-Memory-SQLite-Datenbank pro Test**.
Kein Netzwerk, kein SSH, kein Ollama, keine geteilte Datei zwischen Tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import asyncssh
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# WICHTIG: von nodvard_deck.models importieren, nicht nur von nodvard_deck.db.base.
# `Base` selbst ist in beiden Faellen dasselbe Objekt, aber `nodvard_deck.models`
# importiert dabei alle ORM-Klassen und registriert sie erst dadurch auf
# `Base.metadata`. branding.py importiert `nodvard_deck.models` bewusst lazy (innerhalb
# der Funktion, um einen Zirkelimport zu vermeiden) -- ohne diesen Import hier waere
# `Base.metadata` beim ersten `create_all()` je nach Testreihenfolge noch leer.
from nodvard_deck.models import Base
from nodvard_deck.db.session import reset_engine_cache, set_engine_for_testing


@pytest.fixture(autouse=True)
def _reset_settings_singleton():
    """`config.get_settings()` cacht ein Modul-Singleton (siehe config.py) -- FastAPIs
    `app.dependency_overrides[get_settings]` (siehe `client`-Fixture) ueberschreibt
    das NUR fuer die DI-Aufloesung in Endpunkten, nicht fuer Code, das `get_settings()`
    DIREKT aufruft (z. B. `ExecHandle`/`SecretsHandle` in ext/context.py,
    der WS-Handler in api/v1/terminal.py). Live gefunden: laeuft die volle Suite,
    nicht nur eine einzelne Datei, cacht IRGENDEIN Test als erster einen echten
    `Settings()`, und JEDER spaetere Test, der `get_settings()` unpatched aufruft,
    beruehrt danach echte Projektpfade (`data/master.key`, `data/vault_keyring.json`)
    -- exakt der Fallstrick aus dem `test_settings`-Fixture-Docstring, nur ueber die
    gesamte Suite hinweg statt in einem einzelnen Test. Generischer Fix statt
    Einzelfall-Jagd: vor UND nach jedem Test auf `None` zurueck, damit IMMER ein
    frischer `Settings()` noetig ist, den ein Test entweder gar nicht beruehrt oder
    bewusst per `monkeypatch.setattr(config, "_settings", test_settings)` ersetzt."""
    from nodvard_deck import config

    config._settings = None
    yield
    config._settings = None


@pytest.fixture(autouse=True)
def _isolate_timezone():
    """Die Standard-Zeitzone kommt aus `NODVARD_DECK_TIMEZONE`, `TZ` oder Europe/Berlin
    (`config.default_timezone`). Viele Tests rechnen mit Berlin (`LOCAL_TIMEZONE`) -- eine
    `TZ` der Entwicklermaschine oder der CI darf das nicht kippen. Dazu der Prozess-Cache
    der eingestellten Zone (`core.timezone`), der sonst von Test zu Test durchsickert.

    Bewusst OHNE das `monkeypatch`-Fixture: ein autouse-Fixture, das es anfordert, baut es vor
    `db_session` auf und riesse es erst danach wieder ab -- Tests, die damit `core.ssh._pool`
    ersetzen, liessen ihre Attrappe dann noch im Abbau von `db_session` stehen
    (`reset_ssh_pool()` -> `close_all()` fehlt)."""
    import os

    from nodvard_deck.core import timezone as tz_service

    names = ("NODVARD_DECK_TIMEZONE", "LATTICE_TIMEZONE", "TZ")
    saved = {name: os.environ.pop(name, None) for name in names}
    tz_service.reset_timezone_cache()
    yield
    tz_service.reset_timezone_cache()
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture(autouse=True)
def _reset_login_limit():
    """`core.login_limit` zaehlt Fehlversuche im Prozessspeicher -- ohne
    Reset wuerden falsche Logins aus einem Test (alle vom selben Test-Client
    127.0.0.1) im naechsten Test weiterzaehlen und irgendwann 429 ausloesen."""
    from nodvard_deck.core import login_limit, rate_limit

    login_limit.reset()
    rate_limit.reset_all()
    yield
    login_limit.reset()
    rate_limit.reset_all()


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    # `poolclass` bewusst nicht StaticPool: jeder Test bekommt seine eigene
    # in-memory-Engine, keine geteilte Verbindung zwischen Tests noetig, solange wir
    # innerhalb eines Tests dieselbe Session-Instanz weiterreichen.
    # StaticPool ist bei SQLite ":memory:" Pflicht, nicht Optimierung: ohne ihn
    # oeffnet der Verbindungs-Pool fuer jeden Checkout eine NEUE, leere In-Memory-DB --
    # `Base.metadata.create_all()` liefe dann gegen eine andere DB als die Session, die
    # der Test hinterher benutzt ("no such table"). StaticPool haelt genau eine
    # Verbindung fuer die Lebensdauer der Engine offen, die sich alle Checkouts teilen.
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # core.vault.vault_use() oeffnet intern eine zweite, unabhaengige Session
    # ueber get_sessionmaker() (siehe dortiger Docstring). Ohne diese Bindung wuerde
    # dieser Pfad in Tests unbemerkt die echte data/lattice.db beruehren, nicht die
    # In-Memory-Test-DB -- dasselbe Muster wie der `get_settings()`-Fallstrick
    # (siehe `test_settings` unten). Wichtig: mit StaticPool teilen sich beide Sessions
    # dieselbe physische Verbindung, das beweist also NICHT die echte
    # Verbindungs-Unabhaengigkeit -- das prueft eigens
    # test_vault_use_audit_survives_caller_session_rollback mit einer echten
    # Datei-DB (siehe dort).
    set_engine_for_testing(engine)

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        yield session

    # Aktionen laufen als Hintergrund-Tasks im Event-Loop des Tests. Ein
    # noch laufender Task wuerde sonst nach dem Test gegen eine geschlossene Engine
    # schreiben oder in den Loop des naechsten Tests hineinragen.
    from nodvard_deck.core.gate import shutdown_running

    await shutdown_running()

    await engine.dispose()
    reset_engine_cache()

    # core.ssh.get_ssh_pool() haelt einen Modul-Singleton offener
    # asyncssh-Verbindungen. pytest-asyncio erzeugt pro Testfunktion einen NEUEN
    # Event-Loop -- eine Verbindung aus dem Loop des VORHERIGEN Tests ist in einem
    # neuen Loop nicht mehr sicher benutzbar. Live gefunden: ohne dieses Reset hing
    # der naechste Test, der ueber ctx.exec denselben Pool beruehrte, unbestimmt
    # lange (kein Fehler, kein Traceback -- ein echtes Deadlock-Bild, keine
    # Zeitueberschreitung), weil `conn.is_closed()`/`connect()` auf einem Objekt aus
    # einem toten Loop nie zurueckkehrten.
    from nodvard_deck.core.ssh import reset_ssh_pool

    await reset_ssh_pool()

    # core.scheduler.get_scheduler_service() haelt einen Modul-Singleton
    # mit einem eigenen AsyncIOScheduler UND laufenden asyncio.Tasks (Job-
    # Ausfuehrungen). Dasselbe Muster wie core.ssh's Pool oben: ein Objekt aus dem
    # Event-Loop des VORHERIGEN Tests ist im neuen Loop des naechsten Tests nicht
    # mehr sicher benutzbar.
    from nodvard_deck.core.scheduler import get_scheduler_service, reset_scheduler_service

    await get_scheduler_service().shutdown()
    reset_scheduler_service()

    from nodvard_deck.core.ws_hub import reset_ws_hub

    reset_ws_hub()


@pytest.fixture
def test_settings(tmp_path: Path):
    """Eigene `Settings`-Instanz pro Test, isoliert in `tmp_path`.

    Ohne dieses Fixture wuerde `get_settings()` (ein Prozess-Singleton, siehe
    config.py) die ECHTEN Projektpfade verwenden -- Auth-Endpunkte riefen dann
    `get_or_create_jwt_secret()`/`core.vault.load_or_create_master_key()` gegen
    `data/jwt_secret.key`/`data/master.key` im echten Repository auf und wuerden bei
    jedem Testlauf reale Dateien anlegen/lesen. Gefunden beim Verifizieren des
    TOTP-Flows: der manuelle Boot-Test schrieb genau dorthin (gewollt fuer einen
    manuellen Test, aber falsch fuer automatisierte, isolierte Tests)."""
    from nodvard_deck.config import Settings

    return Settings(
        env="dev",
        data_dir=tmp_path,
        database_url="sqlite+aiosqlite:///:memory:",  # ungenutzt: Tests haengen an db_session
        master_key_path=tmp_path / "master.key",
        vault_keyring_path=tmp_path / "vault_keyring.json",
        jwt_secret_path=tmp_path / "jwt_secret.key",
        # Einrichtungscode vorgegeben (LATTICE_SETUP_CODE-Weg): Tests, die das erste Konto
        # ueber POST /auth/bootstrap anlegen, schicken genau diesen Wert mit.
        setup_code="TEST-CODE-2345",
        access_token_ttl_seconds=900,
        refresh_token_ttl_days=30,
        mfa_token_ttl_seconds=300,
        # Extension-Host: standardmaessig ein LEERES, isoliertes
        # Verzeichnis -- sonst wuerde jeder Test, der discover_and_sync() aufruft,
        # unbemerkt die echte extensions/hello-world-Extension aus dem Repository
        # mitladen, statt kontrolliert zu testen. Tests, die genau DAS wollen (die
        # echte Referenz-Extension pruefen), ueberschreiben `extensions_dir` explizit
        # mit dem echten Repo-Pfad (siehe test_ext_hello_world.py).
        extensions_dir=tmp_path / "extensions",
        ext_data_dir=tmp_path / "ext-data",
        # Nie die echte Datei eines Images (/app/image-info.json), falls die Tests in einem Container laufen.
        image_info_path=tmp_path / "image-info.json",
    )


@pytest_asyncio.fixture
async def client(db_session: AsyncSession, test_settings) -> AsyncIterator[AsyncClient]:  # noqa: ANN001
    """FastAPI-TestClient, dessen `get_session`- und `get_settings`-Dependencies auf die
    Test-Session bzw. isolierte Test-Settings umgeleitet sind -- die App selbst wird
    nie gegen eine echte Datei-DB oder echte Schluesseldateien gestartet."""
    from nodvard_deck.api.deps import get_session
    from nodvard_deck.config import get_settings
    from nodvard_deck.ext.runtime import reset_extension_runtime
    from nodvard_deck.main import app

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    def _override_get_settings():  # noqa: ANN202
        return test_settings

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_settings] = _override_get_settings
    app.state.started_at = __import__("time").monotonic()

    # core.scheduler.get_scheduler_service() ist ein Prozess-Singleton mit
    # einem eigenen Lauf-Log-Verzeichnis, das `main.py`s Lifespan normalerweise per
    # configure() setzt -- die `client`-Fixture durchlaeuft die Lifespan NIE (ASGI-
    # Transport). Ohne diese Zeile faellt der Singleton auf seinen bloss relativen
    # Default `./data/runs` zurueck, den ein ueber die API ausgeloester Job dann
    # tatsaechlich im echten Projektverzeichnis anlegt -- derselbe `data/`-
    # Pollution-Fallstrick wie der `get_settings()`-Singleton.
    from nodvard_deck.core.scheduler import get_scheduler_service

    get_scheduler_service().configure(test_settings.data_dir / "runs")

    # `app` ist ein Modul-Singleton, ueber alle Tests hinweg dasselbe Objekt (siehe
    # nodvard_deck.main). Dabei kann `services.extensions.enable_extension()` Routen
    # in `app.router.routes` einfuegen -- ohne dieses Snapshot/Restore wuerde eine in
    # EINEM Test aktivierte Extension in JEDEM folgenden Test weiter gemountet sein.
    original_routes = list(app.router.routes)
    reset_extension_runtime()

    # `raise_app_exceptions=False`: httpx' Default lässt eine unbehandelte Exception
    # aus einer Route bis in den Test durchschlagen, statt sie -- wie ein echter Server
    # -- als 500-Antwort zu beantworten (Starlettes ServerErrorMiddleware faengt sie,
    # sendet die Antwort, wirft sie danach erneut, genau fuer Test-Clients gedacht, die
    # das auswerten wollen). Dabei loest hello-worlds `/vault-use-and-fail` genau
    # das absichtlich aus -- ohne dieses Flag wuerde der Test die Exception fangen
    # muessen, statt den 500 zu sehen, den ein echter Client bekaeme.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    app.router.routes[:] = original_routes
    reset_extension_runtime()


@pytest_asyncio.fixture
async def file_db(client, tmp_path):
    """Zusatz zu `client`: echte Datei-SQLite mit der echten get_session() (eine
    Session je Anfrage, Commit am Ende). Liefert einen Sessionmaker zum Vorbereiten
    und Pruefen. Noetig fuer Aktionen im Hintergrund -- die
    StaticPool-Fixture teilt sich EINE Verbindung mit dem Hintergrund-Task und
    koennte Sperren oder fehlende Commits nie zeigen."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from nodvard_deck.api.deps import get_session
    from nodvard_deck.config import Settings
    from nodvard_deck.core import gate
    from nodvard_deck.db.session import create_engine_for, reset_engine_cache, set_engine_for_testing
    from nodvard_deck.main import app
    from nodvard_deck.models import Base

    engine = create_engine_for(Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'lattice-file.db'}"))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    set_engine_for_testing(engine)
    app.dependency_overrides.pop(get_session, None)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await gate.shutdown_running()
        await engine.dispose()
        reset_engine_cache()


class _TestSshServer(asyncssh.SSHServer):
    """Minimaler asyncssh-Server fuer SSH-Tests. Laeuft auf `127.0.0.1` mit
    Zufallsport -- kein externes Netzwerk, keine echte Flotte noetig, deckt sich mit
    dem Grundsatz "Tests ohne Netzwerk, ohne SSH": gemeint ist kein Zugriff auf
    reale Infrastruktur, ein lokaler Loopback-Server ist dasselbe Prinzip wie die
    In-Memory-SQLite-Fixture fuer die DB."""

    def __init__(self, username: str, password: str, authorized_keys: list[str] | None = None) -> None:
        self.username = username
        self.password = password
        # `None`: nur Passwort (wie bisher). Eine Liste schaltet die Anmeldung mit Schluessel ein; sie
        # enthaelt die Fingerabdruecke (`SHA256:...`) der erlaubten OEFFENTLICHEN Schluessel und darf
        # im Test nachtraeglich veraendert werden ("Einrichtungsbefehl ist gelaufen").
        self.authorized_keys = authorized_keys

    def begin_auth(self, username: str) -> bool:
        # Beginnt die Anmeldung, hat der Client den Server-Schluessel schon akzeptiert. Dieser Zaehler
        # beweist, dass an einen unbestaetigten Server nie Anmeldedaten gehen (`ssh_server_stats`).
        SSH_SERVER_STATS["begin_auth"] += 1
        return True

    def password_auth_supported(self) -> bool:
        return bool(self.password)

    async def validate_password(self, username: str, password: str) -> bool:
        SSH_SERVER_STATS["password_tries"] += 1
        return username == self.username and password == self.password

    def public_key_auth_supported(self) -> bool:
        return self.authorized_keys is not None

    def validate_public_key(self, username: str, key) -> bool:  # noqa: ANN001 - asyncssh.SSHKey
        SSH_SERVER_STATS["public_key_tries"] += 1
        return username == self.username and key.get_fingerprint() in (self.authorized_keys or [])


SSH_SERVER_STATS: dict[str, int] = {"begin_auth": 0, "password_tries": 0, "public_key_tries": 0}
"""Zaehler der Test-SSH-Server (siehe Fixture `ssh_server_stats`)."""


@pytest.fixture
def ssh_server_stats():
    """`{"begin_auth", "password_tries", "public_key_tries"}` -- wie oft ein Client beim Test-SSH-Server
    mit der Anmeldung begonnen, ein Passwort geschickt bzw. einen Schluessel angeboten hat. Beginnt bei 0."""
    SSH_SERVER_STATS.update(begin_auth=0, password_tries=0, public_key_tries=0)
    return SSH_SERVER_STATS


FAKE_PROBE: dict[str, tuple[int, str, str]] = {}
"""Feste Antworten des Test-SSH-Servers auf genau diese Befehle: `{befehl: (exit, stdout, stderr)}`.
Alles andere antwortet wie bisher `ran:<befehl>` mit Exit 0. Fuer die Pruefungen von "Verbindung pruefen"
(`fake_probe`-Fixture leert das nach jedem Test)."""


@pytest.fixture
def fake_probe():
    FAKE_PROBE.clear()
    yield FAKE_PROBE
    FAKE_PROBE.clear()


FAKE_DOCKER: dict = {}
"""Container-Verwaltung: ein kleines, aber ECHT reagierendes `docker` hinter
dem Test-SSH-Server -- `ps` spiegelt den Zustand, `start/stop/restart` aendern ihn,
`logs --follow` schreibt Zeilen und haelt den Kanal offen, bis der Client ihn
schliesst (`log_streams[i]["closed"]` beweist, dass der entfernte Prozess wirklich
endet). Tests setzen den Zustand ueber `reset_fake_docker()`."""


def reset_fake_docker() -> dict:
    FAKE_DOCKER.clear()
    FAKE_DOCKER.update({
        "containers": {"web": "running", "idle": "exited"},
        "ids": {"web": "aaaaaaaaaaaa1111", "idle": "bbbbbbbbbbbb2222"},
        "calls": [],
        "log_streams": [],
        "build_cache": "9.113GB",
        # Image-Updates: was `docker image inspect` lokal (RepoDigest) und die Registry
        # (`buildx imagetools inspect`) fuer nginx:1.27 melden. Gleich = aktuell.
        "local_digest": "sha256:" + "a" * 64,
        "remote_digest": "sha256:" + "a" * 64,
    })
    return FAKE_DOCKER


@pytest.fixture
def fake_docker() -> dict:
    return reset_fake_docker()


async def _fake_docker(process) -> None:  # noqa: ANN001
    import shlex

    if not FAKE_DOCKER:
        reset_fake_docker()
    parts = shlex.split(process.command)
    FAKE_DOCKER["calls"].append(process.command)
    sub = parts[1] if len(parts) > 1 else ""
    containers: dict = FAKE_DOCKER["containers"]
    name = parts[-1]

    async def _out(text: str, code: int = 0, err: str = "") -> None:
        if text:
            process.stdout.write(text.encode())
        if err:
            process.stderr.write(err.encode())
        await process.stdout.drain()
        process.exit(code)

    if sub == "ps" and "-q" in parts:
        await _out("".join(f"{FAKE_DOCKER['ids'][c][:12]}\n" for c, st in containers.items() if st == "running"))
        return
    if parts[1:3] == ["container", "inspect"]:  # --format FORMAT id...
        by_id = {FAKE_DOCKER["ids"][c][:12]: c for c in containers}
        await _out("".join(f"/{by_id[i]}|nginx:1.27|sha256:{'c' * 64}\n" for i in parts[5:] if i in by_id))
        return
    if parts[1:3] == ["image", "inspect"]:  # --format FORMAT ziel
        import json as _json

        await _out(_json.dumps({"repo_digests": ["nginx@" + FAKE_DOCKER["local_digest"]], "id": parts[-1]}) + "\n")
        return
    if parts[1:4] == ["buildx", "imagetools", "inspect"]:
        import json as _json

        await _out(_json.dumps(FAKE_DOCKER["remote_digest"]) + "\n")
        return
    if sub == "ps":
        lines = []
        for cname, state in containers.items():
            status = "Up 5 minutes" if state == "running" else "Exited (0) 1 hour ago"
            ports = "0.0.0.0:8080->80/tcp" if cname == "web" else ""
            lines.append(f"{FAKE_DOCKER['ids'][cname][:12]}|{cname}|{state}|{status}|nginx:1.27|{'webstack' if cname == 'web' else ''}|{ports}")
        await _out("\n".join(lines) + "\n")
        return
    if sub == "stats":
        lines = [f"{c}|1.50%|45.3MiB / 3.7GiB|1.19%" for c, st in containers.items() if st == "running"]
        await _out("\n".join(lines) + "\n")
        return
    if parts[1:3] == ["system", "df"]:
        await _out(
            '{"Active":"1","Reclaimable":"1.2GB (60%)","Size":"2GB","TotalCount":"3","Type":"Images"}\n'
            f'{{"Active":"0","Reclaimable":"{FAKE_DOCKER["build_cache"]}","Size":"{FAKE_DOCKER["build_cache"]}","TotalCount":"9","Type":"Build Cache"}}\n'
        )
        return
    if sub == "images":
        await _out('{"Containers":"1","ID":"aaa111","Repository":"nginx","Size":"187MB","Tag":"1.27","CreatedSince":"2 weeks ago"}\n'
                   '{"Containers":"0","ID":"bbb222","Repository":"<none>","Size":"1.2GB","Tag":"<none>","CreatedSince":"3 weeks ago"}\n')
        return
    if parts[1:3] == ["builder", "prune"]:
        FAKE_DOCKER["build_cache"] = "0B"
        await _out("Deleted build cache objects:\nabc\n\nTotal reclaimed space: 9.113GB\n")
        return
    if parts[1:3] == ["image", "prune"]:
        await _out("Total reclaimed space: 1.2GB\n")
        return
    if sub == "inspect" and "--type" in parts:
        if name not in containers:
            await _out("[]\n", 1, f"Error: No such container: {name}\n")
            return
        import json as _json

        await _out(_json.dumps([{
            "Name": f"/{name}", "Image": "sha256:" + "c" * 64, "RestartCount": 0,
            "State": {"Running": containers[name] == "running", "StartedAt": "2026-09-24T07:00:00Z"},
            "Config": {"Image": "nginx:1.27", "Env": ["API_TOKEN=GEHEIM", "TZ=Europe/Berlin"], "Labels": {}},
            "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}},
            "NetworkSettings": {"Ports": {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]}, "Networks": {"bridge": {"IPAddress": "172.17.0.2"}}},
            "Mounts": [],
        }]) + "\n")
        return
    if name not in containers:
        await _out("", 1, f"Error response from daemon: No such container: {name}\n")
        return
    if sub in ("start", "restart"):
        containers[name] = "running"
        await _out(f"{name}\n")
        return
    if sub == "stop":
        containers[name] = "exited"
        await _out(f"{name}\n")
        return
    if sub == "inspect":
        await _out(FAKE_DOCKER["ids"][name] + "\n")
        return
    if sub == "logs":
        process.stdout.write(f"2026-09-24T07:00:00Z {name} gestartet\r\n2026-09-24T07:00:01Z {name} bereit\r\n".encode())
        await process.stdout.drain()
        if "--follow" not in parts and "-f" not in parts:
            process.exit(0)
            return
        stream = {"container": name, "closed": False}
        FAKE_DOCKER["log_streams"].append(stream)
        try:
            # Laeuft wie ein echtes `docker logs -f`: endet erst, wenn der Client den
            # Kanal schliesst (PTY-Hangup) -- dann liefert stdin EOF bzw. bricht ab.
            await process.stdin.read()
        except Exception:  # noqa: BLE001
            pass
        stream["closed"] = True
        try:
            process.exit(0)
        except Exception:  # noqa: BLE001
            pass
        return
    await _out(f"ran:{process.command}\n")


FAKE_POWERSHELL: dict = {}
"""Gameserver-Profile: eine kleine Windows-Attrappe hinter dem Test-SSH-Server.
`powershell ... -EncodedCommand <b64>` wird dekodiert und aufgezeichnet
(`scripts`); ein Status-Skript bekommt `status_json` zurueck, Dienst-/Kopier-Aktionen
aendern `service` bzw. `backups` wie auf einem echten Windows-Host."""


def reset_fake_powershell() -> dict:
    FAKE_POWERSHELL.clear()
    FAKE_POWERSHELL.update({"scripts": [], "service": "Running", "status_json": None, "backups": [], "fail": None})
    return FAKE_POWERSHELL


@pytest.fixture
def fake_powershell() -> dict:
    return reset_fake_powershell()


@pytest.fixture(autouse=True)
def _fresh_fake_powershell():
    """Jeder Test beginnt mit einem laufenden Dienst -- sonst erbt ein Test den
    "Stopped"-Zustand des vorigen (live im Testlauf gesehen)."""
    reset_fake_powershell()


async def _fake_powershell(process) -> None:  # noqa: ANN001
    import base64
    import json

    if not FAKE_POWERSHELL:
        reset_fake_powershell()
    b64 = process.command.rsplit(" ", 1)[-1]
    script = base64.b64decode(b64).decode("utf-16-le")
    FAKE_POWERSHELL["scripts"].append(script)
    out, code, err = "", 0, ""
    if FAKE_POWERSHELL.get("fail"):
        out, code, err = "", 1, FAKE_POWERSHELL["fail"]
    elif "ConvertTo-Json -Depth 4" in script:
        status = dict(FAKE_POWERSHELL["status_json"] or {})
        status["service"] = FAKE_POWERSHELL["service"]
        status["backups"] = FAKE_POWERSHELL["backups"]
        out = json.dumps(status)
    elif "Restart-Service" in script or "Start-Service" in script:
        FAKE_POWERSHELL["service"] = "Running"
        out = "Running"
    elif "Stop-Service" in script:
        FAKE_POWERSHELL["service"] = "Stopped"
        out = "Stopped"
    elif "Copy-Item" in script:
        name = f"20260924-{len(FAKE_POWERSHELL['backups']) + 1:06d}"
        FAKE_POWERSHELL["backups"].insert(0, {"name": name, "size": 1876559, "modified": "2026-09-24T10:10:00"})
        out = json.dumps({"dir": "C:/valheim/backups/" + name, "items": ["TestWelt"]})
    if out:
        process.stdout.write(out.encode())
    if err:
        process.stderr.write(err.encode())
    await process.stdout.drain()
    process.exit(code)


async def _handle_ssh_process(process) -> None:  # noqa: ANN001 - asyncssh.SSHServerProcess
    """Live gefunden: Client und Server MUESSEN sich auf dasselbe Encoding einigen
    (hier ueberall rohe Bytes, `encoding=None` bei `asyncssh.listen()` unten) --
    ein Str/Bytes-Mismatch wirft serverseitig eine uncaught TypeError, die nicht nur
    den Kanal, sondern die GESAMTE Verbindung mit "Connection lost" absterben laesst
    (core/ssh.py `run()` hat denselben Fund dokumentiert). `drain()` vor `exit()`,
    weil `exit()` den Kanal sofort schliesst -- ohne Drain kann der zuvor
    geschriebene Puffer noch unversendet sein."""
    if process.command in FAKE_PROBE:
        exit_code, out, err = FAKE_PROBE[process.command]
        if out:
            process.stdout.write(out.encode())
        if err:
            process.stderr.write(err.encode())
        await process.stdout.drain()
        process.exit(exit_code)
        return
    if process.command and process.command.startswith("docker "):
        await _fake_docker(process)
        return
    if process.command and process.command.startswith("powershell ") and "-EncodedCommand" in process.command:
        await _fake_powershell(process)
        return
    if process.command:
        process.stdout.write(f"ran:{process.command}\n".encode())
        await process.stdout.drain()
        process.exit(0)
        return
    process.stdout.write(b"shell-ready\n")
    await process.stdout.drain()
    try:
        async for line in process.stdin:
            text = line.decode() if isinstance(line, bytes) else line
            if text.strip() == "exit":
                break
            process.stdout.write(f"echo:{text}".encode())
            await process.stdout.drain()
    except Exception:  # noqa: BLE001 - Verbindungsabbruch ist kein Testfehler
        pass
    process.exit(0)


@pytest_asyncio.fixture
async def local_ssh_server(tmp_path: Path):
    """Startet einen echten (lokalen) SSH-Server. Liefert
    `(host, port, username, password, sftp_root)`. `sftp_root` ist ein chroot-
    Verzeichnis fuer SFTP-Tests."""
    username, password = "testuser", "test-password-123"
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    sftp_root = tmp_path / "sftp-root"
    sftp_root.mkdir()

    server = await asyncssh.listen(
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        server_factory=lambda: _TestSshServer(username, password),
        process_factory=_handle_ssh_process,
        sftp_factory=lambda conn: asyncssh.SFTPServer(conn, chroot=str(sftp_root)),
        encoding=None,
    )
    port = server.sockets[0].getsockname()[1]
    try:
        yield "127.0.0.1", port, username, password, sftp_root
    finally:
        server.close()
        # Live gefunden: `wait_closed()` kann unbestimmt lange haengen, wenn eine
        # ANDERE Fixture (core.ssh's Verbindungspool) noch eine offene Verbindung zu
        # diesem Server haelt und deren Teardown erst SPAETER (in `db_session`)
        # laeuft -- pytest garantiert hier keine bestimmte Reihenfolge zwischen
        # unabhaengigen Fixtures. Ein Zeitlimit verhindert, dass ein einzelner Test
        # die ganze Suite blockiert; die eigentliche Ursache (Pool-Reset) behebt
        # `db_session`s eigenes Teardown weiterhin zuverlaessig fuer NACHFOLGENDE Tests.
        try:
            await asyncio.wait_for(server.wait_closed(), timeout=5.0)
        except asyncio.TimeoutError:
            pass


@pytest_asyncio.fixture
async def local_ssh_key_server(tmp_path: Path):
    """Wie `local_ssh_server`, aber NUR Anmeldung mit Schluessel. Liefert
    `(host, port, username, authorized_keys)`; `authorized_keys` ist die veraenderbare Liste der
    Fingerabdruecke erlaubter oeffentlicher Schluessel (leer = niemand darf rein)."""
    username = "lattice"
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    authorized: list[str] = []
    server = await asyncssh.listen(
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        server_factory=lambda: _TestSshServer(username, "", authorized),
        process_factory=_handle_ssh_process,
        encoding=None,
    )
    port = server.sockets[0].getsockname()[1]
    try:
        yield "127.0.0.1", port, username, authorized
    finally:
        server.close()
        try:
            await asyncio.wait_for(server.wait_closed(), timeout=5.0)
        except asyncio.TimeoutError:
            pass


@pytest_asyncio.fixture
async def ssh_server_factory():
    """Startet beliebig viele lokale SSH-Server mit frei waehlbaren Server-Schluesseln:
    `await start(("ssh-ed25519", "ecdsa-sha2-nistp256"), offer_keys=False)` liefert
    `(host, port, username, password, fingerprints)`; `fingerprints` ist `{Schluesseltyp: SHA256:...}`.
    `offer_keys=True`: der Server akzeptiert neben dem Passwort auch Schluessel-Anmeldung (keiner ist
    erlaubt) und zaehlt angebotene Schluessel in `ssh_server_stats["public_key_tries"]`."""
    servers: list = []

    async def start(key_types=("ssh-ed25519",), *, offer_keys: bool = False):
        keys = []
        for key_type in key_types:
            if key_type == "ssh-rsa":
                keys.append(asyncssh.generate_private_key("ssh-rsa", key_size=2048))
            else:
                keys.append(asyncssh.generate_private_key(key_type))
        server = await asyncssh.listen(
            "127.0.0.1",
            0,
            server_host_keys=keys,
            server_factory=lambda: _TestSshServer("testuser", "test-password-123", [] if offer_keys else None),
            process_factory=_handle_ssh_process,
            encoding=None,
        )
        servers.append(server)
        fingerprints = {k.get_algorithm(): k.get_fingerprint() for k in keys}
        return "127.0.0.1", server.sockets[0].getsockname()[1], "testuser", "test-password-123", fingerprints

    yield start
    for server in servers:
        server.close()
        try:
            await asyncio.wait_for(server.wait_closed(), timeout=5.0)
        except asyncio.TimeoutError:
            pass


@pytest_asyncio.fixture
async def running_app(db_session: AsyncSession, test_settings):  # noqa: ANN001
    """Startet die ECHTE FastAPI-App auf einem echten TCP-Port ueber `uvicorn.Server`
    -- im SELBEN Event-Loop wie der Test (`lifespan="off"`, damit `main.py`s Lifespan
    nicht zusaetzlich gegen die Test-Engine laeuft; Extensions werden im Test bei
    Bedarf manuell ueber `services.extensions` geladen, wie in den anderen
    Extension-Tests auch). Noetig fuer WS-Tests: `httpx.ASGITransport` (die `client`-
    Fixture) kann keine echten WebSocket-Verbindungen bedienen, ein einfacher
    In-Process-Server im selben Loop schon -- kein Thread-/Event-Loop-Mismatch mit
    der async Test-Engine (anders als Starlettes synchroner `TestClient`)."""
    import socket

    import uvicorn

    from nodvard_deck.api.deps import get_session
    from nodvard_deck.config import get_settings
    from nodvard_deck.ext.runtime import reset_extension_runtime
    from nodvard_deck.main import app

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    def _override_get_settings():  # noqa: ANN202
        return test_settings

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_settings] = _override_get_settings
    app.state.started_at = 0.0
    original_routes = list(app.router.routes)
    reset_extension_runtime()

    # Siehe dieselbe Zeile in der `client`-Fixture oben -- `lifespan="off"` heisst,
    # main.py's configure()-Aufruf faellt auch hier weg.
    from nodvard_deck.core.scheduler import get_scheduler_service

    get_scheduler_service().configure(test_settings.data_dir / "runs")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    serve_task = asyncio.ensure_future(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.01)

    try:
        yield f"http://127.0.0.1:{port}", f"ws://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(serve_task, timeout=10.0)
        except asyncio.TimeoutError:
            serve_task.cancel()
        app.dependency_overrides.clear()
        app.router.routes[:] = original_routes
        reset_extension_runtime()
