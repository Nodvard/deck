"""Attrappen für die Stack-Tests des Docker-Moduls: Docker und SFTP eines Servers, dazu ein eingeschaltetes Modul im Kern."""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

EXT_DIR = Path(__file__).resolve().parents[2] / "extensions" / "docker"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "docker"
PS = (FIXTURES / "stacks_ps.jsonl").read_text(encoding="utf-8")
HASHES = (FIXTURES / "stacks_hashes.txt").read_text(encoding="utf-8")
ROWS = [json.loads(line) for line in PS.splitlines()]
MARKER = "0123456789abcdef"


def ps(*rows: dict) -> str:
    return "\n".join(json.dumps(r) for r in rows) + "\n"


def row(**changes) -> dict:
    return {**next(r for r in ROWS if r["name"] == "shop-web-1"), **changes}


def result(stdout: str = "", stderr: str = "", code: int = 0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, exit_code=code, duration_ms=3)


class FakeSettings:
    """Einstellungen des Moduls; ohne Angaben gelten die Vorgaben (Markierung `docker`)."""

    def __init__(self, values: dict | None = None) -> None:
        self.values = values or {}

    async def get(self) -> dict:
        return dict(self.values)


class FakeExec:
    """Antwortet auf die Befehle des Moduls wie ein Server mit den Stacks aus den Testdaten."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.rows = list(ROWS)
        self.files: dict[str, bytes] = {"/opt/stacks/shop/compose.yaml": b"services:\n  web:\n    image: busybox:1.37\n"}
        self.links: dict[str, str] = {}
        """Verweise auf dem Server: Pfad -> Ziel, wie `realpath` es auflöst."""
        self.list = result(PS)
        self.hashes = result(HASHES)
        self.stats = result("shop-web-1|1.50%|1MiB / 4GiB|0.03%\n")
        self.validate = result()
        self.argv_calls: list[list[str]] = []
        self.verb = result(stderr="Container shop-web-1  Started\n")
        self.services = result("web\nworker\n")
        self.config = result(json.dumps({"name": "shop", "services": {"web": {"image": "busybox:1.37"}, "worker": {"image": "busybox:1.37"}}}))
        self.stdin: list[bytes] = []
        self.containers: list[dict] = []
        """Für die Mount-Prüfung beim Löschen: je Container `id`, `name`, `project` und `mounts`."""
        self.rm_probe = result("Usage: rm [OPTION]... [FILE]...\n")
        self.login_uid = 1000
        """Als wer sich Deck anmeldet (`id -u`); wie der Besitzer der Stack-Ordner in `FakeSftp`."""
        self.sftp_server = FakeSftp(self.files, links=self.links)
        self.modes = self.sftp_server.modes

    async def run(self, host, command, timeout_s=60):
        self.calls.append(command)
        if command.startswith("docker ps -a --filter label=com.docker.compose.project"):
            project = command.split("label=com.docker.compose.project", 1)[1].split(" ", 1)[0].lstrip("=")
            if self.list.exit_code != 0:
                return self.list
            return result(ps(*(r for r in self.rows if not project or r["project"] == project)))
        if command.startswith("sh -c") and "config --quiet" in command:
            return self.validate
        if command.startswith("sh -c"):
            marker = re.search(r"#([0-9a-f]{16}) hashes", command).group(1)
            return result(self.hashes.stdout.replace(MARKER, marker), self.hashes.stderr, self.hashes.exit_code)
        if command.startswith("docker stats"):
            return self.stats
        raise AssertionError(command)

    async def run_argv(self, host, argv, *, stdin=None, timeout_s=60, log_command=None):
        self.argv_calls.append(list(argv))
        if argv[:3] == ["docker", "ps", "-aq"]:
            return result("".join(f"{c['id']}\n" for c in self.containers))
        if argv[:3] == ["docker", "container", "inspect"]:
            return result("".join(json.dumps({k: v for k, v in c.items() if k != "id"}) + "\n" for c in self.containers))
        if argv == ["id", "-u"]:
            return result(f"{self.login_uid}\n")
        if argv == ["rm", "--one-file-system", "--help"]:
            return self.rm_probe
        if argv[:2] == ["rm", "-rf"]:
            folder, sftp = argv[-1], self.sftp_server
            for path in [p for p in sftp.files if p.startswith(folder + "/")]:
                del sftp.files[path]
            sftp.dirs -= {d for d in sftp.dirs if d == folder or d.startswith(folder + "/")}
            return result()
        if argv[-2:] == ["down", "--remove-orphans"] and self.verb.exit_code == 0:
            project = argv[argv.index("-p") + 1]
            self.rows = [r for r in self.rows if r["project"] != project]
        if argv[-3:] == ["config", "--format", "json"]:
            self.stdin.append(stdin)
            return self.config
        return self.services if argv[-2:] == ["config", "--services"] else self.verb

    @asynccontextmanager
    async def sftp(self, host):
        yield self.sftp_server


class SftpUnsupported(Exception):
    code = 8


class SftpDenied(PermissionError):
    code = 3


class _Opening:
    def __init__(self, coro) -> None:
        self._coro = coro
        self._handle = None

    def __await__(self):
        return self._coro.__await__()

    async def __aenter__(self):
        self._handle = await self._coro
        return await self._handle.__aenter__()

    async def __aexit__(self, *exc):
        return await self._handle.__aexit__(*exc)


class FakeSftp:
    """Liest und schreibt in `files`; `modes` hält `st_mode` (Art und Rechte), `owners` Besitzer und Gruppe."""

    def __init__(self, files: dict[str, bytes], modes: dict[str, int] | None = None, owners: dict | None = None,
                 links: dict[str, str] | None = None) -> None:
        self.files = files
        self.modes = {} if modes is None else modes
        self.owners = {} if owners is None else owners
        self.links = {} if links is None else links
        self.dirs: set[str] = {"/opt", "/opt/stacks", "/opt/stacks/shop", "/home/deck"}
        self.dir_modes: dict[str, int] = {}
        self.dir_owners: dict[str, tuple[int, int]] = {}
        self.posix_rename_missing = False
        self.chown_denied = False
        self.denied_paths: set[str] = set()
        self.failing_renames: set[str] = set()
        self.open_ignores_mode = False
        """Wie mancher SFTP-Server: Rechte beim Anlegen gelten nicht, die Datei bekommt 0644."""
        self.seen_modes: list[tuple[str, int]] = []
        """(Pfad, Rechte) jeder Datei in dem Moment, in dem Inhalt hineingeschrieben wird."""

    def _attrs(self, path, follow=False):
        if path in self.dirs:
            uid, gid = self.dir_owners.get(path, (1000, 1000))
            return SimpleNamespace(size=4096, permissions=0o040000 | self.dir_modes.get(path, 0o755), uid=uid, gid=gid)
        if path not in self.files:
            raise FileNotFoundError(path)
        uid, gid = self.owners.get(path, (1000, 1000))
        mode = self.modes.get(path, 0o100644)
        if follow and mode & 0o170000 == 0o120000:
            mode = 0o100644  # ein Verweis auf eine normale Datei
        return SimpleNamespace(size=len(self.files[path]), permissions=mode, uid=uid, gid=gid)

    async def realpath(self, path):
        return "/home/deck" if path == "." else self.links.get(path, path)

    async def stat(self, path):
        return self._attrs(path, follow=True)

    async def lstat(self, path):
        return self._attrs(path)

    def open(self, path, mode, attrs=None):
        """Wie asyncssh: zum Abwarten (`await`) und für `async with`."""
        return _Opening(self._writer(path, mode, attrs) if "w" in mode or "x" in mode else self._reader(path))

    async def _reader(self, path):
        data = self.files[path]

        class Reader:
            async def read(self, n):
                return data[:n]

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        return Reader()

    async def _writer(self, path, mode, attrs):
        if any(path.startswith(denied) for denied in self.denied_paths):
            raise SftpDenied(f"permission denied: {path}")
        if "x" in mode and path in self.files:
            raise FileExistsError(path)
        if path.rsplit("/", 1)[0] not in self.dirs:
            raise FileNotFoundError(path)
        fresh = path not in self.files
        self.files[path] = b""
        if fresh:
            given = None if self.open_ignores_mode or attrs is None else attrs.permissions
            self.modes[path] = 0o100000 | (0o644 if given is None else given)
            self.owners[path] = (0, 0)
        sftp = self

        class Handle:
            async def write(self, data):
                sftp.seen_modes.append((path, sftp.modes.get(path, 0o100644) & 0o777))
                sftp.files[path] += data

            async def chown(self, uid, gid):
                if sftp.chown_denied:
                    raise SftpDenied("chown")
                sftp.owners[path] = (uid, gid)

            async def chmod(self, mode):
                sftp.modes[path] = 0o100000 | mode

            async def stat(self):
                return sftp._attrs(path)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        return Handle()

    async def chmod(self, path, mode):
        self._attrs(path)
        self.modes[path] = 0o100000 | mode

    async def posix_rename(self, old, new):
        if self.posix_rename_missing:
            raise SftpUnsupported("posix-rename@openssh.com")
        await self.rename(old, new)

    async def rename(self, old, new):
        if new in self.failing_renames:
            raise OSError(f"rename to {new} failed")
        self.files[new] = self.files.pop(old)
        self.modes[new] = self.modes.pop(old, 0o100644)
        self.owners[new] = self.owners.pop(old, (1000, 1000))

    async def mkdir(self, path, attrs=None):
        if any(path.startswith(denied) for denied in self.denied_paths):
            raise SftpDenied(f"permission denied: {path}")
        if path in self.dirs or path in self.files:
            raise FileExistsError(path)
        if path.rsplit("/", 1)[0] not in self.dirs:
            raise FileNotFoundError(path)
        self.dirs.add(path)
        self.dir_modes[path] = getattr(attrs, "permissions", None) or 0o777

    async def rmdir(self, path):
        if path not in self.dirs:
            raise FileNotFoundError(path)
        if any(p.startswith(path + "/") for p in (*self.files, *self.dirs)):
            raise OSError(f"directory not empty: {path}")
        self.dirs.remove(path)

    async def listdir(self, path):
        if path not in self.dirs:
            raise FileNotFoundError(path)
        prefix = path.rstrip("/") + "/"
        return [".", "..", *sorted({p[len(prefix):].split("/", 1)[0] for p in (*self.files, *self.dirs) if p.startswith(prefix)})]

    async def remove(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        self.files.pop(path)
        self.modes.pop(path, None)
        self.owners.pop(path, None)


async def login(client, db_session, username: str, role: str | None) -> dict[str, str]:
    """Ohne `role` der Inhaber aus der Ersteinrichtung, sonst ein Konto mit dieser eingebauten Rolle."""
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    if role is None:
        await client.post(
            "/api/v1/auth/bootstrap",
            json={"username": username, "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"},
        )
        password = "correct-horse-battery"
    else:
        roles = await auth_service.ensure_builtin_roles(db_session)
        user = User(username=username, password_hash=security.hash_password("whatever123"), is_active=True)
        user.roles.append(roles[role])
        db_session.add(user)
        await db_session.commit()
        password = "whatever123"
    response = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
async def docker_on(client, db_session, test_settings, monkeypatch):
    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_deck.services import extensions as extensions_service
    from nodvard_deck.services import hosts as hosts_service

    owner = await login(client, db_session, "owner1", None)
    viewer = await login(client, db_session, "nur-viewer", "viewer")
    docker = await hosts_service.create_host(db_session, name="pi-host", address="10.0.0.5", tags=["docker"])
    other = await hosts_service.create_host(db_session, name="nas", address="10.0.0.6", tags=[])
    await db_session.commit()
    test_settings.extensions_dir = EXT_DIR.parent
    await extensions_service.discover_and_sync(db_session, test_settings)
    assert (await client.post("/api/v1/extensions/docker/enable", headers=owner)).status_code == 200
    loaded = get_extension_runtime().loaded["docker"]
    fake = FakeExec()
    monkeypatch.setattr(loaded.ctx.exec, "run", fake.run)
    monkeypatch.setattr(loaded.ctx.exec, "sftp", fake.sftp)
    monkeypatch.setattr(loaded.ctx.exec, "run_argv", fake.run_argv, raising=False)
    return SimpleNamespace(
        owner=owner, viewer=viewer, host=docker.id, other=other.id, exec=fake, handle=loaded.ctx.exec, data_dir=Path(str(loaded.ctx.data_dir)),
    )


@pytest.fixture
async def real_sftp(tmp_path):
    asyncssh = pytest.importorskip("asyncssh")
    root = tmp_path / "server"
    (root / "opt" / "stacks" / "shop").mkdir(parents=True)

    class Server(asyncssh.SSHServer):
        def begin_auth(self, username):
            return True

        def password_auth_supported(self):
            return True

        def validate_password(self, username, password):
            return True

    server = await asyncssh.listen(
        "127.0.0.1", 0, server_factory=Server, server_host_keys=[asyncssh.generate_private_key("ssh-ed25519")],
        sftp_factory=lambda chan: asyncssh.SFTPServer(chan, chroot=str(root).encode()),
    )
    port = server.sockets[0].getsockname()[1]

    @asynccontextmanager
    async def sftp(host):
        async with asyncssh.connect("127.0.0.1", port=port, username="x", password="y", known_hosts=None) as conn, conn.start_sftp_client() as client:
            yield client

    yield SimpleNamespace(root=root, file=root / "opt" / "stacks" / "shop" / "compose.yaml", sftp=sftp)
    server.close()
    await server.wait_closed()
