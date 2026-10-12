"""terminal-Erweiterung: Web-Terminal, Shell-Ausführung und SFTP-Dateizugriff.

Dünner Adapter über `ctx.exec` (den Kern-Handle auf den gemeinsamen SSH-Layer) für `ActionExecutor`, `TerminalTarget` und `FileSource`.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import secrets
import stat as statmod
import tempfile
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from nodvard_sdk import (
    ActionResult,
    ActionSpec,
    DryRunReport,
    ExtensionContext,
    FileEntry,
    FileSourceCaps,
    HealthReport,
    NodvardExtension,
    Page,
    Risk,
)
from nodvard_sdk.actions import ActionRequest
from nodvard_sdk.errors import HostUnreachable


class _SshTerminalSession:
    """Erfüllt `nodvard_sdk.capabilities.TerminalSession` strukturell."""

    def __init__(self, process: Any, stack: AsyncExitStack) -> None:
        self._process = process
        self._stack = stack

    async def read(self):  # noqa: ANN201 - AsyncIterator[bytes], siehe Protokoll
        while True:
            data = await self._process.stdout.read(65536)
            if not data:
                return
            yield data

    async def write(self, data: bytes) -> None:
        self._process.stdin.write(data)
        await self._process.stdin.drain()

    async def resize(self, cols: int, rows: int) -> None:
        self._process.change_terminal_size(cols, rows)

    async def close(self) -> None:
        await self._stack.aclose()

    @property
    def exit_code(self) -> int | None:
        return self._process.exit_status


class _SshTerminalTarget:
    """Erfüllt `nodvard_sdk.capabilities.TerminalTarget`."""

    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx

    async def can_open(self, host) -> bool:  # noqa: ANN001 - nodvard_sdk.types.Host
        # Ein Host ohne SSH-Zugangsdaten wäre sonst ein Terminal-Ziel, das bei jedem echten Verbindungsversuch scheitert.
        # `host.has_credential` erlaubt den Vorab-Check, ohne den Wert der Zugangsdaten zu kennen.
        return host.has_credential

    async def open(self, host, *, user: str | None, cols: int, rows: int) -> _SshTerminalSession:  # noqa: ANN001
        stack = AsyncExitStack()
        process = await stack.enter_async_context(self._ctx.exec.open_shell(host, cols=cols, rows=rows))
        return _SshTerminalSession(process, stack)


# Obergrenze für einen freigegebenen Shell-Befehl. Die 60 s Vorgabe von `ctx.exec.run()` ließen `apt-get upgrade` oder
# `docker compose pull` als Zeitüberschreitung scheitern, obwohl sie auf dem Server noch liefen (wie SCRIPT_TIMEOUT_S in
# der scripts-Erweiterung).
SHELL_TIMEOUT_S = 15 * 60


class _ShellActionExecutor:
    """Erfüllt `nodvard_sdk.capabilities.ActionExecutor` für `shell.exec`.

    Läuft nur über den Kern, nachdem das Gate den Vorschlag genehmigt hat; Erweiterungen rufen es nie direkt auf."""

    action_types = frozenset({"shell.exec"})

    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx

    async def execute(self, req: ActionRequest) -> ActionResult:
        if not req.host_ref:
            return ActionResult(success=False, error="ActionRequest.host_ref fehlt.")
        host = await self._ctx.hosts.get(req.host_ref)
        if host is None:
            return ActionResult(success=False, error=f"Host '{req.host_ref}' nicht gefunden.")
        command = req.payload.get("command")
        if not command:
            return ActionResult(success=False, error="payload.command fehlt.")

        result = await self._ctx.exec.run(host, command, timeout_s=SHELL_TIMEOUT_S)
        return ActionResult(
            success=result.exit_code == 0,
            exit_code=result.exit_code,
            output=result.stdout,
            error=result.stderr or None,
            duration_ms=result.duration_ms,
        )

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        command = req.payload.get("command", "")
        return DryRunReport(would_change=True, summary=f"Würde ausführen: {command}")


# Dieselbe Liste wie in der nextcloud-Erweiterung. SFTP liefert nie einen `mime`-Wert (`_attrs_to_entry` setzt ihn immer auf
# `None`), diese Allowlist ist deshalb der einzige Weg, text- von binärartigen Dateien zu unterscheiden.
_TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".log",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".h", ".cpp",
    ".sh", ".bash", ".ps1", ".sql", ".html", ".htm", ".css", ".xml", ".svg",
}

_MAX_CONTENT_BYTES_PER_FILE = 64 * 1024
_MAX_CONTENT_READS = 50


def _looks_like_text(name: str) -> bool:
    return PurePosixPath(name).suffix.lower() in _TEXT_EXTENSIONS


def _mtime(attrs: Any) -> datetime | None:
    mtime = getattr(attrs, "mtime", None)
    return datetime.fromtimestamp(mtime, UTC) if mtime is not None else None


def _attrs_to_entry(path: PurePosixPath, attrs: Any) -> FileEntry:
    is_dir = bool(attrs.permissions and statmod.S_ISDIR(attrs.permissions))
    return FileEntry(
        name=path.name or str(path),
        path=str(path),
        is_dir=is_dir,
        size=None if is_dir else attrs.size,
        modified_at=_mtime(attrs),
        mime=None,
    )


def _is_symlink(attrs: Any) -> bool:
    return attrs.permissions is not None and statmod.S_ISLNK(attrs.permissions)


def _broken_link_entry(path: PurePosixPath, attrs: Any) -> FileEntry:
    """Toter Link oder Ziel nicht lesbar: als Datei ohne Größe zeigen."""
    return FileEntry(
        name=path.name, path=str(path), is_dir=False, size=None,
        modified_at=_mtime(attrs), metadata={"symlink": True, "broken": True},
    )


async def _entry_after_rename(sftp: Any, path: PurePosixPath) -> FileEntry:
    """Eintrag nach dem Umbenennen; bei einem toten Link zählt der Link selbst (`lstat()`)."""
    # `stat()` folgt Links und wirft bei einem toten Link SFTPNoSuchFile, obwohl die Aktion schon geklappt hat.
    try:
        attrs = await sftp.stat(str(path))
    except Exception:  # nur ein toter Link ist hier kein Fehler, alles andere wird weitergereicht
        link_attrs = await sftp.lstat(str(path))
        if not _is_symlink(link_attrs):
            raise
        return _broken_link_entry(path, link_attrs)
    return _attrs_to_entry(path, attrs)


async def _write_target(sftp: Any, path: PurePosixPath) -> str:
    """Der Pfad, der beim Schreiben ersetzt wird. Ein Link wird bis zur echten Datei verfolgt (wie beim direkten
    Schreiben), sonst würde das Ersetzen den Link selbst austauschen."""
    target = str(path)
    with contextlib.suppress(Exception):  # gibt es das Ziel (noch) nicht, wird es neu angelegt
        if _is_symlink(await sftp.lstat(target)):
            target = str(await sftp.realpath(target))
    return target


def _temp_path(target: str, kind: str = "tmp") -> str:
    p = PurePosixPath(target)
    return str(p.with_name(f".{p.name[:100]}.nodvard-{kind}-{secrets.token_hex(6)}"))


async def _existing_attrs(sftp: Any, target: str) -> Any | None:
    try:
        return await sftp.stat(target)
    except Exception:  # noqa: BLE001 - neue Datei: es gibt nichts zu übernehmen
        return None


# Rechte der Temp-Datei, solange sie eine bestehende Datei ersetzen soll und geschrieben wird: Die alte Datei kann geheim
# sein, und wer die Temp-Datei in dieser Zeit öffnet, liest sonst mit.
_PRIVATE_MODE = 0o600


async def _take_over_owner(f: Any, old: Any) -> bool:
    """Besitzer und Gruppe der bestehenden Datei vor dem Schreiben auf die Temp-Datei übertragen. `True`, wenn die neue Datei
    danach genau diese Werte trägt (oder nichts zu übertragen ist), sonst `False`; dann schreibt der Aufrufer direkt ins Ziel."""
    # Ein Zugang ohne root darf den Besitzer nicht ändern: Die ersetzte Datei gehörte dann dem SSH-Benutzer, und wer sie vorher
    # schreiben durfte (etwa ein Webserver-Benutzer), verlöre das Recht.
    if old.uid is None or old.gid is None:
        return True
    # Nur über den offenen Dateigriff, nie über den Pfad: SETSTAT auf einen Pfad folgt Links. Wer im Zielordner schreiben darf,
    # könnte die Temp-Datei gegen einen Link auf eine fremde Datei tauschen, und die bekäme dann (als root) Besitzer und Rechte
    # des Ziels.
    with contextlib.suppress(Exception):  # scheitert ohne root, das Ergebnis wird unten geprüft
        await f.chown(old.uid, old.gid)
    # Das Ergebnis wird am Griff nachgelesen, nicht aus dem Erfolg von `chown` geschlossen (manche Server melden Erfolg, ohne
    # etwas zu ändern).
    try:
        now = await f.stat()
    except Exception:  # noqa: BLE001 - nicht prüfbar heißt nicht „übertragen“
        return False
    return (getattr(now, "uid", None), getattr(now, "gid", None)) == (old.uid, old.gid)


async def _copy_mode(f: Any, old: Any) -> None:
    """Die Rechte der bestehenden Datei auf die neue übertragen, soweit der Server es erlaubt (nur über den offenen
    Dateigriff, siehe `_take_over_owner`); kommt nach dem Besitzerwechsel, weil der setuid/setgid löscht."""
    if old.permissions is not None:
        with contextlib.suppress(Exception):
            await f.chmod(statmod.S_IMODE(old.permissions))


async def _write_in_place(sftp: Any, target: str, stream: Any) -> None:
    """Direkt ins Ziel schreiben, für Ziele, die sich nicht ersetzen lassen.

    Das erste Stück wird vor dem Abschneiden gelesen: Scheitert die Quelle gleich zu Beginn (fehlt, nicht lesbar), bleibt das Ziel unberührt."""
    chunks = stream.__aiter__()
    try:
        first = await chunks.__anext__()
    except StopAsyncIteration:
        first = b""
    async with sftp.open(target, "wb") as f:
        if first:
            await f.write(first)
        async for chunk in chunks:
            await f.write(chunk)


# So viel der Quelle bleibt im Arbeitsspeicher, bevor `_write_in_place_from_copy` sie in eine lokale Temp-Datei auslagert.
_SPOOL_IN_MEMORY = 8 * 1024 * 1024


async def _write_in_place_from_copy(sftp: Any, target: str, stream: Any) -> None:
    """Wie `_write_in_place`, liest aber die ganze Quelle, bevor das Ziel geöffnet wird."""
    # Das erste Stück zu lesen reicht nicht: Meint die Quelle dieselbe Datei wie das Ziel (Hardlink, eingehängter Ordner unter
    # zwei Pfaden; das erkennt der Kern nicht immer), schneidet das Öffnen zum Schreiben die Quelle ab, und es bliebe nur das
    # erste Stück. So bleibt auch bei einer Quelle, die mittendrin abbricht, das Ziel unberührt. Die Kopie liegt in einer
    # lokalen, sofort entfernten Temp-Datei (nur der Prozess selbst kann sie öffnen).
    with tempfile.SpooledTemporaryFile(max_size=_SPOOL_IN_MEMORY) as spool:
        async for chunk in stream:
            await asyncio.to_thread(spool.write, chunk)
        await asyncio.to_thread(spool.seek, 0)

        async def replay():  # noqa: ANN202 - AsyncIterator[bytes]
            while piece := await asyncio.to_thread(spool.read, 65536):
                yield piece

        await _write_in_place(sftp, target, replay())


# SFTP-Statuscode „Zugriff verweigert“ (`SSH_FX_PERMISSION_DENIED`).
_SFTP_PERMISSION_DENIED = 3

# SFTP-Statuscode „Vorgang nicht unterstützt“ (`SSH_FX_OP_UNSUPPORTED`). Die Erweiterung kennt asyncssh nicht und erkennt den
# Fall am Code der Ausnahme.
_SFTP_OP_UNSUPPORTED = 8


async def _replace(sftp: Any, tmp: str, target: str) -> None:
    """Ersetzt `target` in einem Schritt (`posix-rename@openssh.com`). Kennt der Server das nicht, wird das alte Ziel erst
    beiseite gelegt und bei einem Fehler zurückgeholt."""
    try:
        await sftp.posix_rename(tmp, target)
        return
    except Exception as exc:  # noqa: BLE001 - nur „nicht unterstützt“ hat den Rückweg
        if getattr(exc, "code", None) != _SFTP_OP_UNSUPPORTED:
            raise
    try:
        await sftp.lstat(target)
    except Exception:  # noqa: BLE001 - nichts zu ersetzen: ein einfaches Umbenennen genügt
        await sftp.rename(tmp, target)
        return
    backup = _temp_path(target, "old")
    await sftp.rename(target, backup)
    try:
        await sftp.rename(tmp, target)
    except BaseException:
        with contextlib.suppress(Exception):
            await sftp.rename(backup, target)
        raise
    with contextlib.suppress(Exception):
        await sftp.remove(backup)


async def _discard(sftp: Any, tmp: str) -> None:
    with contextlib.suppress(Exception):
        await sftp.remove(tmp)


_MACHINE_ID = re.compile(r"[0-9a-f]{32}")


async def _machine_id(sftp: Any) -> str | None:
    """`/etc/machine-id`: fest je Rechner, egal unter welcher Adresse er im Deck steht (`None`, wenn sie fehlt oder nicht wie
    eine Kennung aussieht)."""
    # So erkennt der Kern zwei Einträge für denselben Rechner, ohne zwei per rsync gleich gehaltene Dateien auf verschiedenen
    # Rechnern für eine zu halten.
    try:
        async with sftp.open("/etc/machine-id", "rb") as f:
            data = await f.read(64)
    except Exception:  # noqa: BLE001 - ohne Kennung entscheidet der Kern über die Dateiwerte
        return None
    value = bytes(data).strip().decode("ascii", "replace")
    return value if _MACHINE_ID.fullmatch(value) else None


class _SshFileSource:
    """Erfüllt `nodvard_sdk.capabilities.FileSource` über SFTP, die Grundlage des Dateimanagers.

    `label` trägt den Host-Anzeigenamen (siehe `_TerminalFileSourceProvider`), damit die Seitenleiste mehrere Hosts unterscheidet."""

    _MAX_RESULTS = 200
    _MAX_DIRS_VISITED = 500
    _MAX_CONCURRENCY = 8

    def __init__(self, ctx: ExtensionContext, host_id: str, *, label: str | None = None) -> None:
        self.source_id = f"ssh-sftp:{host_id}"
        self.host_id = host_id  # optional laut SDK: der Dateimanager zeigt Status und System des Servers
        self.label = label or "SSH (SFTP)"
        self.icon = "server"
        self.caps = FileSourceCaps(write=True, rename=True, remove=True, mkdir=True, search=True, range_read=True)
        # Der SFTP-Zugriff läuft mit den Zugangsdaten des Servers (oft root), nicht mit dem Konto
        # des Nutzers im Deck. Der Kern verlangt darum für jede Dateiaktion dieselbe Berechtigung
        # wie für Befehle auf dem Server.
        self.required_permission = "hosts.execute"
        self._ctx = ctx
        self._host_id = host_id

    async def _host(self):
        host = await self._ctx.hosts.get(self._host_id)
        if host is None:
            raise HostUnreachable(f"Host '{self._host_id}' nicht gefunden.")
        return host

    async def stat(self, path: PurePosixPath) -> FileEntry:
        host = await self._host()
        async with self._ctx.exec.sftp(host) as sftp:
            attrs = await sftp.stat(str(path))
            return _attrs_to_entry(path, attrs)

    async def _list_children(self, sftp: Any, path: PurePosixPath) -> list[FileEntry]:
        """Die Einträge eines Ordners mit einem `readdir()` (Attribute wie bei lstat gleich dabei) statt `stat()` pro Name;
        spart z. B. in /usr/bin ~1500 Netz-Umläufe."""
        entries: list[tuple[PurePosixPath, Any]] = []
        for name in await sftp.readdir(str(path) or "."):
            if name.filename in (".", ".."):
                continue
            entries.append((path / name.filename, name.attrs))

        # Nur Symlinks werden per `stat()` aufgelöst (gleichzeitig), damit ein Link auf einen Ordner weiter zu öffnen ist.
        link_paths = [p for p, attrs in entries if _is_symlink(attrs)]
        targets = await asyncio.gather(*(sftp.stat(str(p)) for p in link_paths), return_exceptions=True)
        resolved = dict(zip(link_paths, targets, strict=True))

        items = []
        for entry_path, attrs in entries:
            if entry_path not in resolved:
                items.append(_attrs_to_entry(entry_path, attrs))
                continue
            target = resolved[entry_path]
            if isinstance(target, BaseException):
                # Ein toter Link (z. B. /lib/modules/<ver>/build ohne Header) bleibt als Eintrag sichtbar, statt den ganzen
                # Ordner mit SFTPNoSuchFile scheitern zu lassen.
                if not isinstance(target, Exception):
                    raise target
                items.append(_broken_link_entry(entry_path, attrs))
                continue
            entry = _attrs_to_entry(entry_path, target)
            entry.metadata["symlink"] = True
            items.append(entry)
        return items

    async def list_dir(self, path: PurePosixPath, *, cursor: str | None = None) -> Page[FileEntry]:
        host = await self._host()
        async with self._ctx.exec.sftp(host) as sftp:
            return Page(items=await self._list_children(sftp, path))

    async def open_read(self, path: PurePosixPath, *, offset: int = 0):  # noqa: ANN201
        host = await self._host()
        async with self._ctx.exec.sftp(host) as sftp:
            async with sftp.open(str(path), "rb") as f:
                if offset:
                    await f.seek(offset)
                while True:
                    chunk = await f.read(65536)
                    if not chunk:
                        return
                    yield chunk

    async def open_write(self, path: PurePosixPath, stream, *, size: int | None = None) -> FileEntry:  # noqa: ANN001
        """Schreibt in eine temporäre Datei im Zielordner und ersetzt das Ziel erst, wenn alles fehlerfrei angekommen ist."""
        # Direkt ins Ziel zu schreiben schnitte es sofort auf 0 Byte ab, bevor die Quelle gelesen wurde: Zeigt sie auf dieselbe
        # Datei (Link, `..`, zweiter Server-Eintrag) oder scheitert das Lesen, wäre der Inhalt weg.
        host = await self._host()
        async with self._ctx.exec.sftp(host) as sftp:
            target = await _write_target(sftp, path)
            old = await _existing_attrs(sftp, target)
            if old is not None and old.permissions is not None and not statmod.S_ISREG(old.permissions):
                # Pipe, Gerät (etwa /dev/null) oder Ordner: Ersetzen tauschte sie gegen eine normale Datei aus, also hineinschreiben
                # (ein Ordner scheitert dabei sofort).
                await _write_in_place(sftp, target, stream)
            else:
                await self._write_replacing(sftp, target, old, stream)
            attrs = await sftp.stat(target)
            return _attrs_to_entry(path, attrs)

    @staticmethod
    async def _write_replacing(sftp: Any, target: str, old: Any | None, stream: Any) -> None:
        tmp = _temp_path(target)
        try:
            # Ersetzt die Datei eine bestehende, ist sie bis zum Ende nur für den Zugang lesbar.
            f = await (sftp.open(tmp, "xb", type(old)(permissions=_PRIVATE_MODE)) if old is not None else sftp.open(tmp, "xb"))
        except Exception as exc:
            # Die Datei darf geschrieben werden, im Ordner aber nichts angelegt (etwa eine gruppen-schreibbare Datei in /etc):
            # dann direkt hinein.
            if old is None or getattr(exc, "code", None) != _SFTP_PERMISSION_DENIED:
                raise
            await _write_in_place_from_copy(sftp, target, stream)
            return
        keeps_owner = True
        try:
            async with f:
                # Gleich nach dem Anlegen, nicht erst am Ende: scheitert es, wird nichts geschrieben.
                keeps_owner = old is None or await _take_over_owner(f, old)
                if keeps_owner:
                    async for chunk in stream:
                        await f.write(chunk)
                    if old is not None:
                        await _copy_mode(f, old)
            if keeps_owner:
                await _replace(sftp, tmp, target)
        except BaseException:
            await _discard(sftp, tmp)
            raise
        if not keeps_owner:
            # Ohne root lässt sich der Besitzer nicht halten: Ersetzen gäbe die Datei dem SSH-Benutzer.
            await _discard(sftp, tmp)
            await _write_in_place_from_copy(sftp, target, stream)

    async def file_identity(self, path: PurePosixPath) -> dict[str, Any] | None:
        """Woran der Kern erkennt, ob zwei Pfade dieselbe Datei meinen: aufgelöster Pfad, Größe, Änderungszeit, Besitzer, Rechte
        und `machine` (Kennung des Rechners). `None`, wenn die Datei nicht existiert oder der Server den Pfad nicht auflöst."""
        host = await self._host()
        async with self._ctx.exec.sftp(host) as sftp:
            try:
                attrs = await sftp.stat(str(path))
                resolved = await sftp.realpath(str(path))
            except Exception:  # noqa: BLE001 - nicht prüfbar heißt nicht „gleich“ (im Zweifel gilt die Datei als andere)
                return None
            return {
                "path": str(resolved),
                "size": attrs.size,
                "mtime": getattr(attrs, "mtime", None),
                "uid": getattr(attrs, "uid", None),
                "gid": getattr(attrs, "gid", None),
                "mode": getattr(attrs, "permissions", None),
                "machine": await _machine_id(sftp),
            }

    async def mkdir(self, path: PurePosixPath) -> FileEntry:
        host = await self._host()
        async with self._ctx.exec.sftp(host) as sftp:
            await sftp.mkdir(str(path))
            attrs = await sftp.stat(str(path))
            return _attrs_to_entry(path, attrs)

    async def remove(self, path: PurePosixPath, *, recursive: bool = False) -> None:
        host = await self._host()
        async with self._ctx.exec.sftp(host) as sftp:
            # `rmtree` lehnt Links ab („must not be a symlink“): Ein Link auf einen Ordner wird nur als Link entfernt, das Ziel
            # bleibt unangetastet.
            if recursive and not _is_symlink(await sftp.lstat(str(path))):
                await sftp.rmtree(str(path))
            else:
                await sftp.remove(str(path))

    async def rename(self, src: PurePosixPath, dst: PurePosixPath) -> FileEntry:
        host = await self._host()
        async with self._ctx.exec.sftp(host) as sftp:
            await sftp.rename(str(src), str(dst))
            return await _entry_after_rename(sftp, dst)

    async def _list_children_or_empty(self, sftp: Any, path: PurePosixPath) -> list[FileEntry]:
        try:
            return await self._list_children(sftp, path)
        except Exception:  # noqa: BLE001 - ein einzelnes nicht lesbares Unterverzeichnis
            # (z. B. Rechte) darf die Suche der anderen nicht abbrechen.
            return []

    async def _content_matches(self, sftp: Any, path: str, needle: str) -> bool:
        """Liest höchstens `_MAX_CONTENT_BYTES_PER_FILE` über die offene Sitzung von `search()`; `open_read()` öffnete je Aufruf
        eine neue."""
        read = 0
        buf = bytearray()
        try:
            async with sftp.open(path, "rb") as f:
                while read < _MAX_CONTENT_BYTES_PER_FILE:
                    chunk = await f.read(65536)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    read += len(chunk)
        except Exception:  # noqa: BLE001 - eine einzelne nicht lesbare Datei darf die Suche nicht abbrechen
            return False
        return needle in buf.decode("utf-8", errors="ignore").lower()

    async def search(self, query: str, *, root: PurePosixPath) -> AsyncIterator[FileEntry]:  # noqa: ANN201
        """Sucht `query` in Dateinamen und (bei Textdateien) Inhalten, per begrenzter Breitensuche ab `root`."""
        # SFTP kennt nur `listdir`/`stat` und kein serverseitiges Suchen, deshalb die bewusst begrenzte Breitensuche (Grenzen wie
        # bei nextcloud). Eine `ctx.exec.sftp()`-Sitzung bleibt für die ganze Suche offen (statt einer pro Verzeichnis),
        # `asyncio.gather()` schickt mehrere `listdir`/`stat`-Anfragen gleichzeitig darüber; asyncssh multiplext das auf demselben
        # Kanal (Pipelining ist Teil des SFTP-Protokolls). `_looks_like_text` und `_MAX_CONTENT_READS` begrenzen die Lesezugriffe
        # für die Suche im Inhalt.
        host = await self._host()
        needle = query.lower()
        async with self._ctx.exec.sftp(host) as sftp:
            frontier: list[PurePosixPath] = [root]
            visited_dirs = 0
            results = 0
            content_reads_used = 0
            while frontier and results < self._MAX_RESULTS and visited_dirs < self._MAX_DIRS_VISITED:
                take = min(self._MAX_CONCURRENCY, self._MAX_DIRS_VISITED - visited_dirs)
                batch, frontier = frontier[:take], frontier[take:]
                listings = await asyncio.gather(*(self._list_children_or_empty(sftp, p) for p in batch))
                visited_dirs += len(batch)
                for entries in listings:
                    for entry in entries:
                        matched = needle in entry.name.lower()
                        if (
                            not matched
                            and not entry.is_dir
                            and content_reads_used < _MAX_CONTENT_READS
                            and _looks_like_text(entry.name)
                        ):
                            content_reads_used += 1
                            matched = await self._content_matches(sftp, entry.path, needle)
                        if matched:
                            yield entry
                            results += 1
                            if results >= self._MAX_RESULTS:
                                return
                        if entry.is_dir:
                            frontier.append(PurePosixPath(entry.path))

    async def info(self):  # noqa: ANN201
        from nodvard_sdk import SourceInfo

        host = await self._host()
        async with self._ctx.exec.sftp(host) as sftp:
            healthy = True
            try:
                await sftp.stat(".")
            except Exception:  # noqa: BLE001
                healthy = False
            return SourceInfo(healthy=healthy)


class _TerminalFileSourceProvider:
    """Erfüllt `nodvard_sdk.capabilities.FileSourceProvider`: eine `_SshFileSource` pro aktuell bekanntem Host mit
    Zugangsdaten, bei jedem Aufruf frisch ermittelt."""

    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx

    async def file_sources(self) -> list[_SshFileSource]:
        hosts = await self._ctx.hosts.list()
        # Nur Hosts mit Zugangsdaten: Sonst scheiterte der erste Zugriff erst nach dem Klick mit einem 502.
        # `host.has_credential` zeigt nur, dass es Zugangsdaten gibt, nicht deren Wert.
        return [
            _SshFileSource(self._ctx, host.id, label=f"SSH (SFTP): {host.display_name or host.name}")
            for host in hosts
            if host.has_credential
        ]


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx
        ctx.capabilities.provide(_SshTerminalTarget(ctx))
        ctx.capabilities.provide(_ShellActionExecutor(ctx))
        ctx.capabilities.provide(_TerminalFileSourceProvider(ctx))
        # Ein Shell-Befehl auf dem Server ist hohes Risiko und braucht `hosts.execute`; ausgeführt wird er erst nach der Freigabe
        # durch das Gate.
        ctx.actions.register(
            ActionSpec(
                action_type="shell.exec",
                label="Shell-Befehl ausführen",
                description="Führt einen Befehl per SSH auf dem Host aus.",
                default_risk=Risk.HIGH,
                permissions=["hosts.execute"],
                host_bound=True,
                command_field="command",
            )
        )

    async def on_start(self, ctx: ExtensionContext) -> None:
        return None

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        return HealthReport(healthy=True)

    def file_source_for(self, host_id: str) -> _SshFileSource:
        """Eine `_SshFileSource` für genau einen Host, ohne den Umweg über den Provider (z. B. für Tests)."""
        return _SshFileSource(self._ctx, host_id)
