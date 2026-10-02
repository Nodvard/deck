"""terminal-Extension -- Web-Terminal, Shell-Ausfuehrung, SFTP-Dateizugriff
(docs/02-EXTENSION-API.md §3).

Kennt asyncssh nicht direkt -- alles laeuft ueber `ctx.exec`, den Kern-Handle, der auf
`core/ssh.py` (dem gemeinsamen SSH-Layer aus docs/00 D-05) sitzt. Diese Extension ist
nur der duenne Adapter, der die drei SDK-Capability-Protokolle (`ActionExecutor`,
`TerminalTarget`, `FileSource`) darauf abbildet.
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
    """Erfuellt `nodvard_sdk.capabilities.TerminalSession` strukturell."""

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
    """Erfuellt `nodvard_sdk.capabilities.TerminalTarget`."""

    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx

    async def can_open(self, host) -> bool:  # noqa: ANN001 - nodvard_sdk.types.Host
        # Frueher immer True --
        # ein Host ohne SSH-Zugangsdaten erschien als Terminal-Ziel, scheiterte aber
        # bei jedem echten Verbindungsversuch. `host.has_credential` (SDK-Nachtrag)
        # macht den Vorab-Check jetzt moeglich, ohne den Credential-Wert selbst zu
        # kennen (docs/03 §3 Invariante 1 bleibt unangetastet).
        return host.has_credential

    async def open(self, host, *, user: str | None, cols: int, rows: int) -> _SshTerminalSession:  # noqa: ANN001
        stack = AsyncExitStack()
        process = await stack.enter_async_context(self._ctx.exec.open_shell(host, cols=cols, rows=rows))
        return _SshTerminalSession(process, stack)


SHELL_TIMEOUT_S = 15 * 60
"""Obergrenze fuer einen freigegebenen Shell-Befehl. Die 60 s Vorgabe von
`ctx.exec.run()` liess `apt-get upgrade` oder `docker compose pull` als
Zeitueberschreitung scheitern, obwohl sie auf dem Server noch liefen --
dieselbe Loesung wie SCRIPT_TIMEOUT_S in der scripts-Extension."""


class _ShellActionExecutor:
    """Erfuellt `nodvard_sdk.capabilities.ActionExecutor` fuer `shell.exec`.

    Extensions rufen das NIE direkt auf -- nur der Kern, NACHDEM das Gate (WP-5)
    einen Vorschlag genehmigt hat (docs/02 §3)."""

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


_TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".log",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".h", ".cpp",
    ".sh", ".bash", ".ps1", ".sql", ".html", ".htm", ".css", ".xml", ".svg",
}
"""Identisch zu nextclouds `capabilities.py` (dort die volle Begruendung) -- SFTP
liefert hier ohnehin nie einen `mime`-Wert (`_attrs_to_entry` unten setzt ihn immer
auf `None`), diese Allowlist ist deshalb der EINZIGE Weg, text- von binaerartigen
Dateien zu unterscheiden."""

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
    """Toter Link oder Ziel nicht lesbar: als Datei ohne Groesse zeigen."""
    return FileEntry(
        name=path.name, path=str(path), is_dir=False, size=None,
        modified_at=_mtime(attrs), metadata={"symlink": True, "broken": True},
    )


async def _entry_after_rename(sftp: Any, path: PurePosixPath) -> FileEntry:
    """Eintrag nach dem Umbenennen. `stat()` folgt Links und wirft bei einem
    toten Link SFTPNoSuchFile, obwohl die Aktion schon geklappt hat (das Umbenennen
    eines toten Links endete so mit 502, der Link hiess aber schon anders). Dann
    zaehlt der Link selbst (`lstat()`); alles andere bleibt ein echter Fehler."""
    try:
        attrs = await sftp.stat(str(path))
    except Exception:  # nur ein toter Link ist hier kein Fehler, alles andere wird weitergereicht
        link_attrs = await sftp.lstat(str(path))
        if not _is_symlink(link_attrs):
            raise
        return _broken_link_entry(path, link_attrs)
    return _attrs_to_entry(path, attrs)


async def _write_target(sftp: Any, path: PurePosixPath) -> str:
    """Der Pfad, der beim Schreiben ersetzt wird. Ein Link wird bis zur echten Datei verfolgt
    (wie beim direkten Schreiben), sonst wuerde das Ersetzen den Link selbst austauschen."""
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
    except Exception:  # noqa: BLE001 - neue Datei: es gibt nichts zu uebernehmen
        return None


_PRIVATE_MODE = 0o600
"""Rechte der Temp-Datei, solange sie eine bestehende Datei ersetzen soll und geschrieben wird:
die alte Datei kann geheim sein, und wer die Temp-Datei in dieser Zeit oeffnet, liest sonst mit."""


async def _take_over_owner(f: Any, old: Any) -> bool:
    """Besitzer und Gruppe der bestehenden Datei auf die frisch angelegte Temp-Datei uebertragen,
    BEVOR geschrieben wird. `True`, wenn die neue Datei danach genau diese Werte traegt (oder es
    nichts zu uebertragen gibt); `False`, wenn nicht: Ein Zugang ohne root darf den Besitzer nicht
    aendern, die ersetzte Datei gehoerte dann dem SSH-Benutzer, und wer sie vorher schreiben durfte
    (etwa ein Webserver-Benutzer), verlaere das Recht. Der Aufrufer schreibt dann direkt ins Ziel.

    Nur ueber den OFFENEN Dateigriff, nie ueber den Pfad: SETSTAT auf einen Pfad folgt Links. Wer
    im Zielordner schreiben darf, koennte die Temp-Datei gegen einen Link auf eine fremde Datei
    tauschen, und die bekaeme dann (als root) Besitzer und Rechte des Ziels. Das Ergebnis wird am
    Griff nachgelesen, nicht aus dem Erfolg von `chown` geschlossen (manche Server melden Erfolg,
    ohne etwas zu aendern). Kann es nicht gelesen werden, gilt es als nicht uebertragen."""
    if old.uid is None or old.gid is None:
        return True
    with contextlib.suppress(Exception):  # scheitert ohne root, das Ergebnis wird unten geprueft
        await f.chown(old.uid, old.gid)
    try:
        now = await f.stat()
    except Exception:  # noqa: BLE001 - nicht pruefbar heisst nicht "uebertragen"
        return False
    return (getattr(now, "uid", None), getattr(now, "gid", None)) == (old.uid, old.gid)


async def _copy_mode(f: Any, old: Any) -> None:
    """Die Rechte der bestehenden Datei auf die neue uebertragen, soweit der Server es erlaubt.
    Nur ueber den offenen Dateigriff (siehe `_take_over_owner`). Kommt NACH dem Besitzerwechsel,
    weil der setuid/setgid loescht."""
    if old.permissions is not None:
        with contextlib.suppress(Exception):
            await f.chmod(statmod.S_IMODE(old.permissions))


async def _write_in_place(sftp: Any, target: str, stream: Any) -> None:
    """Direkt ins Ziel schreiben (der Weg von frueher), fuer Ziele, die sich nicht ersetzen lassen.
    Das erste Stueck wird VOR dem Abschneiden gelesen: scheitert die Quelle gleich zu Beginn
    (fehlt, nicht lesbar), bleibt das Ziel unberuehrt."""
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


_SPOOL_IN_MEMORY = 8 * 1024 * 1024
"""So viel der Quelle bleibt im Arbeitsspeicher, bevor `_write_in_place_from_copy` sie in eine
lokale Temp-Datei auslagert."""


async def _write_in_place_from_copy(sftp: Any, target: str, stream: Any) -> None:
    """Wie `_write_in_place`, liest aber die GANZE Quelle, bevor das Ziel geoeffnet wird. Das erste
    Stueck zu lesen reicht nicht: meint die Quelle dieselbe Datei wie das Ziel (Hardlink,
    eingehaengter Ordner unter zwei Pfaden -- das erkennt der Kern nicht immer), schneidet das
    Oeffnen zum Schreiben die Quelle ab, und es bliebe nur das erste Stueck. So bleibt auch bei
    einer Quelle, die mittendrin abbricht, das Ziel unberuehrt. Die Kopie liegt in einer lokalen,
    sofort entfernten Temp-Datei (nur der Prozess selbst kann sie oeffnen)."""
    with tempfile.SpooledTemporaryFile(max_size=_SPOOL_IN_MEMORY) as spool:
        async for chunk in stream:
            await asyncio.to_thread(spool.write, chunk)
        await asyncio.to_thread(spool.seek, 0)

        async def replay():  # noqa: ANN202 - AsyncIterator[bytes]
            while piece := await asyncio.to_thread(spool.read, 65536):
                yield piece

        await _write_in_place(sftp, target, replay())


_SFTP_PERMISSION_DENIED = 3
"""SFTP-Statuscode "Zugriff verweigert" (`SSH_FX_PERMISSION_DENIED`)."""

_SFTP_OP_UNSUPPORTED = 8
"""SFTP-Statuscode "Vorgang nicht unterstuetzt" (`SSH_FX_OP_UNSUPPORTED`). Die Extension kennt
asyncssh nicht, erkennt den Fall also am Code der Ausnahme."""


async def _replace(sftp: Any, tmp: str, target: str) -> None:
    """Ersetzt `target` in einem Schritt (`posix-rename@openssh.com`). Kennt der Server das nicht,
    wird das alte Ziel erst beiseite gelegt und bei einem Fehler zurueckgeholt."""
    try:
        await sftp.posix_rename(tmp, target)
        return
    except Exception as exc:  # noqa: BLE001 - nur "nicht unterstuetzt" hat den Rueckweg
        if getattr(exc, "code", None) != _SFTP_OP_UNSUPPORTED:
            raise
    try:
        await sftp.lstat(target)
    except Exception:  # noqa: BLE001 - nichts zu ersetzen: ein einfaches Umbenennen genuegt
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
    """`/etc/machine-id`: fest je Rechner, egal unter welcher Adresse er im Deck steht. So erkennt
    der Kern zwei Eintraege fuer denselben Rechner, ohne zwei per rsync gleich gehaltene Dateien
    auf verschiedenen Rechnern fuer eine zu halten. `None`, wenn sie fehlt (etwa in einem
    SFTP-chroot) oder nicht wie eine Kennung aussieht."""
    try:
        async with sftp.open("/etc/machine-id", "rb") as f:
            data = await f.read(64)
    except Exception:  # noqa: BLE001 - ohne Kennung entscheidet der Kern ueber die Dateiwerte
        return None
    value = bytes(data).strip().decode("ascii", "replace")
    return value if _MACHINE_ID.fullmatch(value) else None


class _SshFileSource:
    """Erfuellt `nodvard_sdk.capabilities.FileSource` ueber SFTP -- die Grundlage,
    auf der WP-11 den Dateimanager baut (docs/02 §7).

    **Live gefunden (WP-11-Boot-Test im echten Browser):** ohne `label` zeigte die
    Seitenleiste des Dateimanagers bei mehreren Hosts mehrfach den IDENTISCHEN Text
    "SSH (SFTP)" -- fachlich korrekt (jede Instanz hat eine eigene `source_id`),
    aber fuer einen Menschen ununterscheidbar, welcher Eintrag zu welchem Host
    gehoert. `label` traegt deshalb jetzt den Host-Anzeigenamen, wenn der Aufrufer
    ihn kennt (siehe `_TerminalFileSourceProvider.file_sources()` unten)."""

    _MAX_RESULTS = 200
    _MAX_DIRS_VISITED = 500
    _MAX_CONCURRENCY = 8

    def __init__(self, ctx: ExtensionContext, host_id: str, *, label: str | None = None) -> None:
        self.source_id = f"ssh-sftp:{host_id}"
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
        """EIN `readdir()` statt `stat()` pro Name: die Attribute kommen
        wie bei lstat gleich mit, das spart z. B. in /usr/bin ~1500 Netz-Umlaeufe.
        Nur Symlinks werden per `stat()` aufgeloest (gleichzeitig), damit ein Link auf
        einen Ordner weiter oeffnbar bleibt. Ein toter Link (z. B. /lib/modules/<ver>/
        build ohne Header) bleibt als Eintrag sichtbar, statt den ganzen Ordner mit
        SFTPNoSuchFile scheitern zu lassen."""
        entries: list[tuple[PurePosixPath, Any]] = []
        for name in await sftp.readdir(str(path) or "."):
            if name.filename in (".", ".."):
                continue
            entries.append((path / name.filename, name.attrs))

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
        """Schreibt in eine temporaere Datei im Zielordner und ersetzt das Ziel erst, wenn alles
        fehlerfrei angekommen ist. Direkt ins Ziel zu schreiben schnitte es sofort auf 0 Byte ab,
        bevor die Quelle auch nur gelesen wurde: zeigt sie auf dieselbe Datei (Link, `..`,
        zweiter Server-Eintrag) oder scheitert das Lesen, waere der Inhalt weg."""
        host = await self._host()
        async with self._ctx.exec.sftp(host) as sftp:
            target = await _write_target(sftp, path)
            old = await _existing_attrs(sftp, target)
            if old is not None and old.permissions is not None and not statmod.S_ISREG(old.permissions):
                # Pipe, Geraet (etwa /dev/null) oder Ordner: ersetzen tauschte sie gegen eine normale
                # Datei aus, also hineinschreiben wie frueher (ein Ordner scheitert dabei sofort).
                await _write_in_place(sftp, target, stream)
            else:
                await self._write_replacing(sftp, target, old, stream)
            attrs = await sftp.stat(target)
            return _attrs_to_entry(path, attrs)

    @staticmethod
    async def _write_replacing(sftp: Any, target: str, old: Any | None, stream: Any) -> None:
        tmp = _temp_path(target)
        try:
            # Ersetzt die Datei eine bestehende, ist sie bis zum Ende nur fuer den Zugang lesbar.
            f = await (sftp.open(tmp, "xb", type(old)(permissions=_PRIVATE_MODE)) if old is not None else sftp.open(tmp, "xb"))
        except Exception as exc:
            # Die Datei darf geschrieben werden, im Ordner aber nichts angelegt (etwa eine
            # gruppen-schreibbare Datei in /etc): dann wie frueher direkt hinein.
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
            # Ohne root laesst sich der Besitzer nicht halten: ersetzen gaebe die Datei dem SSH-Benutzer.
            await _discard(sftp, tmp)
            await _write_in_place_from_copy(sftp, target, stream)

    async def file_identity(self, path: PurePosixPath) -> dict[str, Any] | None:
        """Woran der Kern erkennt, ob zwei Pfade dieselbe Datei meinen (siehe `FileSource`):
        aufgeloester Pfad plus Groesse, Aenderungszeit, Besitzer und Rechte, dazu `machine`, die
        Kennung des Rechners (`/etc/machine-id`), wenn sie lesbar ist. `None`, wenn die Datei nicht
        existiert oder der Server den Pfad nicht aufloest -- im Zweifel gilt sie als andere."""
        host = await self._host()
        async with self._ctx.exec.sftp(host) as sftp:
            try:
                attrs = await sftp.stat(str(path))
                resolved = await sftp.realpath(str(path))
            except Exception:  # noqa: BLE001 - nicht pruefbar heisst nicht "gleich"
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
            # `rmtree` lehnt Links ab ("must not be a symlink"): ein Link auf einen
            # Ordner wird nur als Link entfernt, das Ziel bleibt unangetastet.
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
            # (z. B. Rechte) darf die Suche der anderen nicht abbrechen, dieselbe
            # Haltung wie `api/v1/files.py::search()`s "eine kaputte Quelle darf die
            # Suche der anderen nicht verhindern", hier eine Ebene tiefer angewendet.
            return []

    async def _content_matches(self, sftp: Any, path: str, needle: str) -> bool:
        """Nutzt die BEREITS OFFENE `sftp`-Sitzung von `search()` (nicht `open_read()`,
        das eine eigene, neue `ctx.exec.sftp()`-Sitzung pro Aufruf oeffnen wuerde --
        genau die Kosten, die `search()`s einzelne geteilte Sitzung laut Docstring
        oben vermeiden soll). Liest hoechstens `_MAX_CONTENT_BYTES_PER_FILE`, wie
        nextclouds Pendant."""
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
        """`caps.search=True` (Nachtrag, dasselbe Muster wie nextclouds `search()`):
        SFTP hat KEIN server-seitiges Suchprimitiv (Standard-SFTP-Protokoll kennt
        nur `listdir`/`stat`) -- deshalb eine echte, aber bewusst begrenzte
        Breitensuche ab `root`, dieselben Grenzen/`_MAX_CONCURRENCY`-Begruendung wie
        bei nextcloud. Unterschied zu nextcloud: SFTP ist eine STATEFUL Sitzung
        (kein zustandsloses HTTP) -- EINE `ctx.exec.sftp()`-Sitzung fuer die ganze
        Suche offen gehalten (statt einer pro Verzeichnis, wie `list_dir()` es fuer
        einen einzelnen Aufruf tut), `asyncio.gather()` fuer mehrere gleichzeitige
        `listdir`/`stat`-Anfragen darueber -- asyncssh multiplext das serverseitig
        auf DEMSELBEN Kanal (Pipelining ist Teil des SFTP-Protokolls), kein zweiter
        Kanal noetig.

        **Volltextsuche in Dateiinhalten:** wie bei nextcloud jetzt Name ODER Inhalt, `_looks_like_text`/
        `_MAX_CONTENT_READS` begrenzen die zusaetzlichen Lese-Aufrufe."""
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
    """Erfuellt `nodvard_sdk.capabilities.FileSourceProvider` (WP-11): eine
    `_SshFileSource` pro AKTUELL bekanntem Host mit Zugangsdaten, frisch bei jedem
    Aufruf ermittelt.

    **Nur Hosts mit Zugangsdaten:** frueher erschien
    JEDER Host als Quelle, auch ohne SSH-Zugangsdaten -- deren erster Zugriff
    (`list_dir()`/`stat()`) scheiterte dann sauber mit einem 502
    (`api/v1/files.py::_call()`), aber erst nach dem Klick, nicht vorher sichtbar.
    `host.has_credential` (SDK-Nachtrag, `services/hosts.py::host_to_sdk()`) filtert
    das jetzt vorab -- ohne den Credential-WERT zu kennen (docs/03 §3 Invariante 1
    bleibt unangetastet, nur die blosse Existenz ist jetzt sichtbar)."""

    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx

    async def file_sources(self) -> list[_SshFileSource]:
        hosts = await self._ctx.hosts.list()
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
        """Seit WP-11 nicht mehr der einzige Zugang: `_TerminalFileSourceProvider`
        (siehe oben) registriert automatisch eine Quelle pro bekanntem Host. Diese
        Methode bleibt als direkter Erweiterungspunkt bestehen, falls ein Aufrufer
        (z. B. ein Test) gezielt EINE `_SshFileSource` fuer einen einzelnen Host
        braucht, ohne den ganzen Provider-Umweg zu gehen."""
        return _SshFileSource(self._ctx, host_id)
