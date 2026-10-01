"""Steht NICHT unter backend/src/nodvard_deck -- reiner Dev-/Demo-Helfer, kein Teil des
Kerns oder seiner Tests (die haben ihre eigene, gleich gebaute `local_ssh_server`-
Fixture in backend/tests/conftest.py). Startet einen echten lokalen SSH/SFTP-Server
fuer die manuelle Live-Verifikation von terminals `_SshFileSource` (WP-11) auf einem
festen Port, mit einem vorbefuellten Verzeichnis.

Seit WP-12 zusaetzlich befehls-bewusst fuer zwei weitere Live-Verifikationen, die
denselben SSH-Layer nutzen: `docker ps -a ...` (service-matrix) liefert eine
realistische Container-Liste, ein Befehl mit "join code" darin (gameserver, siehe
dortiger Docstring zur "log_command als Literal"-Testtechnik) liefert eine
realistische Log-Zeile zurueck. Jeder andere Befehl faellt auf das bisherige
`ran:<befehl>`-Echo zurueck (wie `conftest.py`s Test-Mock).

    python scripts/dev_mock_sftp_host.py --port 8897

Zugangsdaten: Benutzername "devuser", Passwort "dev-password-123".
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import asyncssh

_DOCKER_PS_OUTPUT = "\n".join(
    [
        "nginx-proxy|running|Up 3 hours|0.0.0.0:8080->80/tcp",
        "grafana|running|Up 2 days|0.0.0.0:3000->3000/tcp",
        "old-worker|exited|Exited (1) 5 minutes ago|",
    ]
)


class _DevSshServer(asyncssh.SSHServer):
    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password

    def begin_auth(self, username: str) -> bool:
        return True

    def password_auth_supported(self) -> bool:
        return True

    async def validate_password(self, username: str, password: str) -> bool:
        return username == self.username and password == self.password


async def _handle_process(process) -> None:  # noqa: ANN001 - asyncssh.SSHServerProcess
    if process.command:
        command = process.command
        if "docker ps" in command:
            output = _DOCKER_PS_OUTPUT
        elif "join code" in command.lower():
            output = command  # siehe Modul-Docstring: der Befehl selbst traegt die Log-Zeile
        else:
            output = f"ran:{command}"
        process.stdout.write(f"{output}\n".encode())
        await process.stdout.drain()
        process.exit(0)
        return
    # Interaktive Shell (kein Befehl) -- fuer terminals eigene Live-Verifikation
    # (WP-4), von service-matrix/gameserver (WP-12) nicht gebraucht, aber nicht
    # regressieren, falls dieses Skript weiter fuer terminal genutzt wird.
    process.stdout.write(b"shell-ready\n")
    await process.stdout.drain()
    try:
        async for line in process.stdin:
            text = line.decode() if isinstance(line, bytes) else line
            if text.strip() == "exit":
                break
            process.stdout.write(f"echo:{text}".encode())
            await process.stdout.drain()
    except Exception:  # noqa: BLE001 - Verbindungsabbruch ist kein Skriptfehler
        pass
    process.exit(0)


async def _main(port: int) -> None:
    username, password = "devuser", "dev-password-123"
    sftp_root = Path(__file__).resolve().parent / ".dev_sftp_root"
    sftp_root.mkdir(exist_ok=True)
    (sftp_root / "willkommen.txt").write_text("Willkommen ueber SFTP (Dev-Mock)!\n", encoding="utf-8")

    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server = await asyncssh.listen(
        "127.0.0.1", port,
        server_host_keys=[host_key],
        server_factory=lambda: _DevSshServer(username, password),
        process_factory=_handle_process,
        sftp_factory=lambda conn: asyncssh.SFTPServer(conn, chroot=str(sftp_root)),
        encoding=None,
    )
    print(f"SFTP-Mock auf 127.0.0.1:{port} -- Nutzer '{username}', Passwort '{password}', Root: {sftp_root}")
    async with server:
        await server.wait_closed()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8897)
    args = parser.parse_args()
    asyncio.run(_main(args.port))
