"""Start vor der Anwendung (`python -m nodvard_deck.boot`, aus `deploy/entrypoint.sh`): bringt die Datenbank auf den Stand
dieses Images oder hält den Grund für die Notseite fest."""

from __future__ import annotations

import contextlib
import io
import logging
import os
import sqlite3
import sys
import traceback
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from . import migrate
from .config import Settings, get_settings
from .core import bootstate, updates
from .core.backup import premigrate, restore, snapshot
from .core.backup.errors import BackupError, DamagedBackup, UnusableBackup
from .version import __version__

logger = logging.getLogger("nodvard_deck.boot")

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_LOCKED = 75
LOCK_WAIT_S = 10.0
"""So lange wartet `boot` auf eine belegte Sperre (z. B. ein Container, der gerade endet), dann Code 75."""

_LOG: deque[str] = deque(maxlen=400)
"""Die Zeilen dieses Starts (eigene Meldungen, Ausgabe der Migration, Traceback) als Grundlage des Fehlerprotokolls."""


def _say(text: str) -> None:
    print(f"[boot] {text}", flush=True)
    _LOG.append(f"[boot] {text}")


class BootFailure(Exception):
    """Der Start kann nicht weitergehen, es kommt die Notseite. `kind` aus `bootstate.FAILURE_KINDS`, `reason` ist ein
    ganzer deutscher Satz ohne Pfade."""

    def __init__(
        self, kind: str, reason: str, *, rollback: dict | None = None, needed_bytes: int | None = None, free_bytes: int | None = None,
    ) -> None:
        super().__init__(reason)
        self.kind = kind
        self.reason = reason
        self.rollback = rollback
        self.needed_bytes = needed_bytes
        self.free_bytes = free_bytes


def _settle_pre_restore(data_dir, layout: restore.Layout) -> None:
    """Wurde eine Sicherung zurückgenommen, gilt wieder der Migrationsstand der alten Datenbank (`pre_restore`)."""
    # Sonst hielte `decide` den Eintrag `running` der Sicherung für den der alten Datenbank und setzte die Kopie der
    # Sicherung ein: die alte Datenbank wäre weg.
    pre = bootstate.read_state(data_dir).get("pre_restore")
    if pre is None or os.path.lexists(layout.restore_dir / restore.JOURNAL_NAME):
        return  # noch nicht entschieden
    result = restore.read_result(layout)
    if result and result.get("id") == pre["id"] and result.get("rolled_back"):
        bootstate.update_state(data_dir, last_migration=pre["last_migration"], started_ok=pre["started_ok"], revert=None, pre_restore=None)
    else:
        bootstate.update_state(data_dir, pre_restore=None)


def restore_step(settings: Settings) -> restore.Applied | None:
    """Spielt eine vorgemerkte Sicherung ein und gibt das noch nicht endgültige Einspielen zurück (oder `None`)."""
    try:
        layout = restore.Layout.from_settings(settings)
    except Exception:  # noqa: BLE001 - keine SQLite-Datenbank: es gibt nichts einzuspielen
        return None
    if layout.restore_dir.is_dir():
        recovered = restore.recover_interrupted(layout)
        if recovered is not None:
            _say(recovered.get("message") or "Eine unterbrochene Wiederherstellung wurde abgeschlossen.")
    _settle_pre_restore(settings.data_dir, layout)
    if not layout.restore_dir.is_dir() or not restore.pending_exists(layout):
        return None
    _say("Vorgemerkte Wiederherstellung gefunden, wird eingespielt …")
    try:
        known, _heads = migrate.known_revisions()
    except Exception:
        logger.exception("boot_known_revisions_failed")
        known = None
    if known is None:
        # Ohne die Liste bekannter Stände könnte eine fremde Datenbank durchrutschen.
        restore.clear_pending(layout)
        restore.finish_failed(layout, None, "Die Migrationen dieser Installation sind nicht lesbar; es wurde nichts eingespielt.")
        return None
    try:
        applied = restore.apply_pending(layout, known=known, current_version=__version__)
    except restore.RestoreFailed as exc:
        # Kein Grund, nicht zu starten: der alte Stand ist ja zurück, `restore/result.json` und das Protokoll sagen warum.
        _say(f"Wiederherstellung nicht eingespielt: {exc}")
        return None
    if applied is not None:
        _say("Sicherung eingespielt. Jetzt folgt die Migration; erst danach gilt sie als endgültig.")
    return applied


@dataclass
class Plan:
    action: str
    """`noop` (schon aktuell), `migrate`, `revert` (Kopie wiederherstellen) oder `refuse` (Notseite)."""
    copy: bool = False
    """`migrate`: vorher eine Kopie anlegen (es gibt Daten, die schützenswert sind)."""
    copy_name: str | None = None
    keep_discarded: bool = False
    set_aside: bool = False
    """`migrate`: die halbe Datenbank einer abgebrochenen ERSTEN Migration vorher beiseitelegen, dann frisch migrieren."""
    reason: str = ""
    kind: str = ""
    message: str = ""
    rollback: dict | None = None


def decide(
    live: premigrate.LiveDb, known: set[str], script_heads: set[str], state: dict,
    *, copy_heads: Callable[[str], list[str] | None], rollback: dict | None,
) -> Plan:
    """Was tun mit dieser Datenbank? Reine Entscheidung; die Platte fragt nur `copy_heads` (Stände einer Kopie, `None`
    wenn sie fehlt oder kaputt ist)."""
    migration = state.get("last_migration") or {}
    from_heads = migration.get("from_heads") or []
    copy = migration.get("copy")

    # Migration mittendrin gestorben: Die Datenbank kann halb migriert sein, es lief seitdem keine Anwendung, also gingen
    # keine Daten verloren. Zurück auf die Kopie und neu versuchen; der halbe Stand wird beiseitegelegt (`_revert`).
    if migration.get("state") == "running" and copy:
        heads = copy_heads(copy)
        if heads is not None and heads == sorted(from_heads) and all(h in known for h in heads):
            return Plan("revert", copy_name=copy, reason="interrupted", keep_discarded=True)
        return Plan(
            "refuse", kind="copy_unusable",
            message="Ein früheres Update ist mittendrin abgebrochen, und die Kopie von vor dem Update fehlt oder ist beschädigt. "
                    "Die Datenbank kann in einem halben Zustand sein.",
        )

    unknown = [h for h in live.heads if h not in known]
    # Allererste Migration einer neuen Installation abgebrochen: nichts zu schützen, die Anwendung lief nie, es gibt kein
    # Konto. SQLite legt Tabellen sofort an, `alembic_version` kommt erst am Ende der Revision; ein neuer Versuch auf dem
    # halben Stand scheiterte an "table already exists". Darum beiseitelegen (nicht löschen) und frisch migrieren.
    if (migration.get("state") in ("running", "failed") and not copy and migration.get("had_data") is False
            and live.has_data and live.accounts is False and not unknown):
        return Plan("migrate", copy=False, set_aside=True)
    if not unknown:
        if not live.has_data or not live.heads:
            return Plan("migrate", copy=live.has_data)
        if set(live.heads) == script_heads:
            return Plan("noop")
        return Plan("migrate", copy=True)

    # Die Datenbank hat Stände, die dieses Image nicht kennt: sie ist neuer. Die Kopie von vor dem Update kommt nur zurück,
    # wenn (1) die letzte Migration von bekannten Ständen aus lief und genau den jetzigen Stand erzeugte, (2) die Kopie heil
    # ist und genau diese Stände hat und (3) die neue Version nie erfolgreich gestartet ist (`started_ok` false), es also
    # keine Nutzerdaten zu verlieren gibt. Oder eine ausdrückliche, noch gültige Vormerkung der Notseite (`rollback.json`)
    # liegt vor; dann auch nach einem guten Start. Sonst Notseite.
    condition_one = bool(
        migration.get("state") == "ok" and from_heads and all(h in known for h in from_heads)
        and sorted(migration.get("to_heads") or []) == sorted(live.heads)
    )
    heads = copy_heads(copy) if (condition_one and copy) else None
    condition_two = heads is not None and heads == sorted(from_heads)
    never_started = state.get("started_ok") is False
    requested = bool(rollback and copy and rollback.get("copy") == copy)
    if condition_two:
        if never_started:
            # `started_ok` wird nur nach bestem Bemühen gesetzt: die neueren Daten deshalb nie löschen, nur beiseitelegen.
            return Plan("revert", copy_name=copy, reason="downgrade", keep_discarded=True)
        if requested:
            return Plan("revert", copy_name=copy, reason="rollback_requested", keep_discarded=True)
        return Plan(
            "refuse", kind="newer_data",
            rollback={
                "copy": copy, "from_version": migration.get("from_version"), "to_version": migration.get("to_version"),
                "started_ok": state.get("started_ok"), "started_at": state.get("started_at"),
            },
            message="Die Daten dieser Installation sind neuer als diese Version von Nodvard Deck. "
                    "Die Datenbank enthält Änderungen, die diese Version nicht kennt.",
        )
    if condition_one:
        return Plan(
            "refuse", kind="copy_unusable",
            message="Die Daten dieser Installation sind neuer als diese Version von Nodvard Deck, und die Kopie von vor dem Update "
                    "fehlt oder ist beschädigt. Ein automatischer Rückweg ist nicht möglich.",
        )
    return Plan(
        "refuse", kind="newer_data",
        message="Die Daten dieser Installation sind neuer als diese Version von Nodvard Deck. "
                "Die Datenbank enthält Änderungen, die diese Version nicht kennt.",
    )


class _Tee(io.TextIOBase):
    """Leitet Ausgabe weiter und merkt sich die Zeilen für das Fehlerprotokoll."""

    def __init__(self, target) -> None:
        self._target = target
        self._partial = ""

    def write(self, text: str) -> int:
        with contextlib.suppress(Exception):
            self._target.write(text)
        self._partial += text
        *lines, self._partial = self._partial.split("\n")
        _LOG.extend(lines)
        return len(text)

    def flush(self) -> None:
        with contextlib.suppress(Exception):
            self._target.flush()

    def finish(self) -> None:
        if self._partial:
            _LOG.append(self._partial)
            self._partial = ""


def _run_migration() -> None:
    out, err = _Tee(sys.stdout), _Tee(sys.stderr)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            migrate.main()
    except Exception as exc:
        _LOG.extend(line for chunk in traceback.format_exception(exc) for line in chunk.splitlines())
        raise
    finally:
        out.finish()
        err.finish()


def _revert(settings: Settings, layout: restore.Layout, plan: Plan) -> None:
    texts = {
        "interrupted": "Ein früheres Update ist mittendrin abgebrochen. Die Datenbank wird auf den Stand von vor dem Update zurückgesetzt "
                       "(der halbe Stand bleibt 30 Tage unter restore/ liegen) …",
        "downgrade": "Die neuere Version ist nie erfolgreich gestartet. Die Datenbank wird auf den Stand von vor ihrem Update zurückgesetzt "
                     "(die neueren Daten bleiben 30 Tage unter restore/ liegen) …",
        "rollback_requested": "Rückweg ausdrücklich vorgemerkt: Die Datenbank wird auf den Stand von vor dem Update zurückgesetzt. "
                              "Änderungen seit dem Update gehen verloren (die neueren Daten bleiben unter restore/ liegen) …",
    }
    keep = plan.keep_discarded
    if keep and plan.reason == "interrupted":
        # Den halben Stand aufzuheben ist nur ein Sicherheitsnetz (seit dem Update lief keine Anwendung). Fehlt im
        # Datenordner der Platz dafür (Datenbank auf anderem Laufwerk), wird er verworfen, sonst endete jeder Start auf der
        # Notseite. Neuere Daten (`downgrade`, `rollback_requested`) werden nie verworfen.
        try:
            premigrate.check_room_to_set_aside(layout)
        except premigrate.NoSpaceToSetAside as exc:
            keep = False
            texts["interrupted"] = (
                "Ein früheres Update ist mittendrin abgebrochen. Die Datenbank wird auf den Stand von vor dem Update zurückgesetzt. "
                f"Der halbe Stand wird nicht aufgehoben: Die Datenbank liegt auf einem anderen Laufwerk, und im Datenordner ist dafür "
                f"kein Platz (nötig: {premigrate.size_text(exc.needed)}, frei: {premigrate.size_text(exc.free)}) …"
            )
    _say(texts.get(plan.reason, "Die Datenbank wird auf die Kopie von vor dem Update zurückgesetzt …"))
    assert plan.copy_name is not None
    try:
        premigrate.restore_copy(layout, plan.copy_name, keep_discarded=keep, reason=plan.reason)
    except UnusableBackup as exc:
        raise BootFailure("copy_unusable", str(exc)) from exc
    except DamagedBackup as exc:
        raise BootFailure("copy_unusable", "Die Kopie von vor dem Update ist beschädigt und wird nicht eingespielt.") from exc
    except premigrate.NoSpaceToSetAside as exc:
        raise BootFailure("no_space", str(exc), needed_bytes=exc.needed, free_bytes=exc.free) from exc
    except (premigrate.RevertFailed, BackupError, OSError, sqlite3.Error) as exc:
        raise BootFailure(
            "revert_failed", f"Das Zurücksetzen auf die Kopie von vor dem Update ist gescheitert ({type(exc).__name__}). "
                             "Die Kopie bleibt erhalten; der nächste Start versucht es noch einmal.",
        ) from exc
    state = bootstate.read_state(settings.data_dir)
    migration = state.get("last_migration")
    if migration:
        migration["state"] = "reverted"
    bootstate.update_state(settings.data_dir, last_migration=migration, started_ok=True)
    bootstate.clear_rollback(settings.data_dir)
    _say("Die Datenbank ist wieder auf dem Stand von vor dem Update.")


def _running_version(settings: Settings) -> str:
    """Die genaue Version dieses Containers (beim offiziellen Image auch eine Vorabversion wie `0.6.0-rc1`), sonst
    `version.__version__`; Kopien, Startzustand und Notseite nennen so dieselbe wie die Update-Suche."""
    return updates.running_version(settings, __version__)


def _copy_before_migration(settings: Settings, layout: restore.Layout, live: premigrate.LiveDb, script_heads: set[str], state: dict):
    """Kopie der Datenbank nach `data/backups/vor-update/` (Platz mindestens 1,2 x Datenbank, drei bleiben,
    `integrity_check`). Ohne Kopie keine Migration; Notausgang ist `NODVARD_DECK_SKIP_PRE_MIGRATE_BACKUP=1`."""
    if settings.skip_pre_migrate_backup:
        _say("ACHTUNG: Die Kopie vor der Migration ist ausgeschaltet (NODVARD_DECK_SKIP_PRE_MIGRATE_BACKUP). Scheitert die Migration, gibt es keinen Rückweg.")
        return None
    needed = premigrate.required_bytes(layout.db_path)
    _say(f"Kopie der Datenbank vor der Migration (Platz nötig: {needed // (1024 * 1024)} MB) …")
    try:
        info = premigrate.make_copy(
            layout, from_version=state.get("app_version"), to_version=_running_version(settings), from_heads=live.heads,
            to_heads=sorted(script_heads),
        )
    except premigrate.NoSpaceForCopy as exc:
        raise BootFailure("no_space", str(exc), needed_bytes=exc.needed, free_bytes=exc.free) from exc
    except (BackupError, OSError, sqlite3.Error) as exc:
        logger.exception("boot_copy_failed")
        raise BootFailure(
            "no_copy", f"Die Kopie vor dem Update ließ sich nicht anlegen ({type(exc).__name__}). Ohne Kopie wird nicht migriert; "
                       "es wurde nichts verändert.",
        ) from exc
    _say(f"Kopie angelegt: {info.name}")
    return info


def _migrate(settings: Settings, layout: restore.Layout, live: premigrate.LiveDb, script_heads: set[str], state: dict, plan: Plan) -> None:
    data_dir = settings.data_dir
    if plan.set_aside:
        try:
            premigrate.set_aside(layout)
        except premigrate.NoSpaceToSetAside as exc:
            raise BootFailure("no_space", str(exc), needed_bytes=exc.needed, free_bytes=exc.free) from exc
        _say("Die erste Migration dieser Installation ist nicht fertig geworden. Der halbe Stand liegt jetzt unter restore/; "
             "es wird frisch migriert …")
        live = premigrate.LiveDb(False, [], False)
    copy = _copy_before_migration(settings, layout, live, script_heads, state) if plan.copy else None
    # `last_migration.at` und `started_ok` liest `scripts/deploy_pi.sh`: Hat das neue Image die Datenbank schon umgebaut und
    # ist gestartet, rollt das Skript nicht aufs alte Image zurück, das die Daten ablehnen würde.
    migration = {
        "at": bootstate.now_iso(), "from_version": state.get("app_version"), "to_version": _running_version(settings), "from_heads": live.heads,
        "to_heads": sorted(script_heads), "copy": copy.name if copy else None, "state": "running", "had_data": live.has_data,
    }
    # Vor der Migration als `running` festhalten: Stirbt der Prozess mitten drin, setzt der nächste Start die Datenbank
    # auf die Kopie zurück (`decide`).
    bootstate.update_state(data_dir, last_migration=migration, started_ok=False)
    try:
        _run_migration()
    except Exception as exc:
        logger.exception("boot_migration_failed")
        name = type(exc).__name__
        if copy is None:
            migration["state"] = "failed"
            bootstate.update_state(data_dir, last_migration=migration)
            raise BootFailure(
                "migration_failed",
                f"Das Update der Datenbank ist gescheitert ({name}). Es gab keine Kopie von vor dem Update; "
                "die Datenbank kann in einem halben Zustand sein.",
            ) from exc
        _say(f"Die Migration ist gescheitert ({name}). Die Datenbank geht auf die Kopie von vor dem Update zurück.")
        try:
            premigrate.restore_copy(layout, copy.name, keep_discarded=False, reason="migration_failed")
        except Exception as revert_exc:  # noqa: BLE001 - alles ist hier gleich ernst
            logger.exception("boot_revert_failed")
            migration["state"] = "failed"
            bootstate.update_state(data_dir, last_migration=migration)
            raise BootFailure(
                "revert_failed",
                f"Das Update der Datenbank ist gescheitert ({name}), und das Zurücksetzen auf die Kopie auch "
                f"({type(revert_exc).__name__}). Die Kopie bleibt erhalten; der nächste Start versucht es noch einmal.",
            ) from exc
        migration["state"] = "reverted"
        bootstate.update_state(data_dir, last_migration=migration, started_ok=True)
        raise BootFailure(
            "migration_failed",
            f"Das Update der Datenbank ist gescheitert ({name}). Die Datenbank wurde auf den Stand von vor dem Update zurückgesetzt; "
            "deine Daten sind unverändert.",
        ) from exc
    migration["state"] = "ok"
    after = premigrate.read_live(layout.db_path)
    bootstate.update_state(data_dir, last_migration=migration, db_heads=after.heads)


def _heads_of_copy(settings: Settings, name: str) -> list[str] | None:
    path = premigrate.copy_path(settings.data_dir, name)
    if path is None:
        return None
    try:
        snapshot.integrity_check(path)
        return premigrate.read_heads(path)
    except (BackupError, sqlite3.Error, OSError):
        return None


def ensure_current(settings: Settings, layout: restore.Layout) -> None:
    """Bringt die Datenbank auf die Stände dieses Images oder wirft `BootFailure`."""
    data_dir = settings.data_dir
    for _attempt in range(3):  # nach einem Rückweg wird neu entschieden
        state = bootstate.read_state(data_dir)
        try:
            known, script_heads = migrate.known_revisions()
        except migrate.DuplicateRevisions as exc:
            raise BootFailure("unexpected", str(exc)) from exc
        except Exception as exc:
            raise BootFailure("unexpected", f"Die Migrationen dieses Images ließen sich nicht lesen ({type(exc).__name__}).") from exc
        try:
            live = premigrate.read_live(layout.db_path)
        except DamagedBackup as exc:
            raise BootFailure(
                "db_unreadable", "Die Datenbank ist nicht lesbar (beschädigt?). Es wurde nichts verändert. "
                                 "Eine Sicherung lässt sich über das Wiederherstellen einspielen.",
            ) from exc
        plan = decide(
            live, known, script_heads, state,
            copy_heads=lambda name: _heads_of_copy(settings, name),
            rollback=bootstate.read_rollback(data_dir),
        )
        if plan.action == "refuse":
            raise BootFailure(plan.kind, plan.message, rollback=plan.rollback)
        if plan.action == "revert":
            _revert(settings, layout, plan)
            continue
        if plan.action == "migrate":
            _migrate(settings, layout, live, script_heads, state, plan)
            bootstate.clear_rollback(data_dir)
        else:  # noop: schon aktuell; das (leere) `upgrade` läuft trotzdem als Prüfung
            try:
                _run_migration()
            except Exception as exc:
                logger.exception("boot_migration_failed")
                raise BootFailure(
                    "migration_failed", f"Die Prüfung der Datenbank beim Start ist gescheitert ({type(exc).__name__}). Es wurde nichts verändert.",
                ) from exc
            bootstate.clear_rollback(data_dir)
            bootstate.update_state(data_dir, db_heads=live.heads, started_ok=state.get("started_ok", True))
        return
    raise BootFailure("unexpected", "Der Start kommt nicht zu einem Ergebnis (der Rückweg wurde mehrfach wiederholt).")


def _record_failure(settings: Settings, exc: BootFailure) -> None:
    """Schreibt Grund und die letzten 50 Zeilen, bereinigt, für die Notseite nach `state.json`; ein Fehler hierbei verdeckt
    nie den eigentlichen."""
    data_dir = settings.data_dir
    try:
        state = bootstate.read_state(data_dir)
        migration = state.get("last_migration") or {}
        state["failure"] = {
            "kind": exc.kind,
            "at": bootstate.now_iso(),
            "reason": bootstate.sanitize_text(exc.reason, data_dir=data_dir, limit=1000),
            "app_version": _running_version(settings),
            "data_version": state.get("app_version"),
            "previous_version": migration.get("from_version"),
            "log": bootstate.sanitize_lines(list(_LOG), data_dir=data_dir),
            "rollback": exc.rollback,
            "needed_bytes": exc.needed_bytes,
            "free_bytes": exc.free_bytes,
        }
        bootstate.write_state(data_dir, state)
    except Exception:  # noqa: BLE001
        logger.exception("boot_record_failure_failed")
        print("[boot] Der Grund des Fehlers ließ sich nicht festhalten (Datenordner nicht beschreibbar?).", file=sys.stderr, flush=True)


def _boot(settings: Settings) -> None:
    """Rückweg zu Ende führen, unterbrochenes und vorgemerktes Einspielen, dann den Migrationsstand prüfen."""
    data_dir = settings.data_dir
    try:
        layout = restore.Layout.from_settings(settings)
    except Exception:  # noqa: BLE001 - keine SQLite-Datenbank (oder :memory:)
        layout = None

    if layout is None:
        # Keine Kopie möglich: nur migrieren, ein Fehler führt zur Notseite.
        _say("Die Datenbank ist keine SQLite-Datei: Es gibt keine Kopie vor der Migration. Bitte vorher selbst sichern.")
        try:
            _run_migration()
        except Exception as exc:
            logger.exception("boot_migration_failed")
            raise BootFailure(
                "migration_failed", f"Die Migration ist gescheitert ({type(exc).__name__}). Es gibt hier keine Kopie zum Zurücksetzen."
            ) from exc
        return

    # Ein Absturz mitten im Zurücksetzen auf eine Kopie darf nie einen halben Stand hinterlassen.
    try:
        premigrate.finish_revert(layout)
    except premigrate.RevertFailed as exc:
        raise BootFailure("revert_failed", f"Der unterbrochene Rückweg auf die Kopie ließ sich nicht abschließen: {exc}") from exc
    except (BackupError, OSError, sqlite3.Error) as exc:
        raise BootFailure("revert_failed", f"Der unterbrochene Rückweg auf die Kopie ließ sich nicht abschließen ({type(exc).__name__}).") from exc

    try:
        applied = restore_step(settings)
    except restore.RollbackFailed as exc:
        raise BootFailure("rollback_failed", str(exc)) from exc
    pre: dict = {}
    if applied is not None:
        # Die letzte Migration (und `started_ok`) gehörte zur alten Datenbank. Sie liegt auch auf der Platte: Stirbt der
        # Prozess mitten in der Migration der Sicherung, nimmt der nächste Start das Einspielen zurück und braucht sie
        # wieder (`_settle_pre_restore`).
        before = bootstate.read_state(data_dir)
        pre = {"id": applied.pending["id"], "last_migration": before.get("last_migration"), "started_ok": before.get("started_ok")}
        bootstate.update_state(data_dir, last_migration=None, pre_restore=pre)

    try:
        ensure_current(settings, layout)
    except BootFailure as exc:
        if applied is None:
            raise
        _say(f"Die Migration der eingespielten Sicherung ist gescheitert ({exc.kind}). Der alte Stand kommt zurück.")
        # Eine gescheiterte Migration direkt nach dem Einspielen nimmt es zurück. Erst der Zustand der alten Datenbank, dann
        # sie selbst: Ein noch stehendes Rückweg-Journal zeigt auf eine Kopie der Sicherung und ersetzte beim nächsten Start
        # die alte Datenbank durch diese.
        bootstate.update_state(data_dir, last_migration=pre["last_migration"], started_ok=pre["started_ok"], revert=None)
        try:
            applied.rollback(
                "Die Sicherung ließ sich nicht auf den Stand dieser Version bringen "
                f"({exc.kind}). Alles ist wie vorher."
            )
        except restore.RollbackFailed as rollback_exc:
            raise BootFailure("rollback_failed", str(rollback_exc)) from rollback_exc
        bootstate.update_state(data_dir, pre_restore=None)
        ensure_current(settings, layout)  # den alten Stand wie gewohnt migrieren; scheitert das, gibt es die Notseite
        return

    if applied is not None:
        try:
            applied.commit()
        except Exception:
            logger.exception("boot_commit_failed")
            raise BootFailure(
                "unexpected", "Das Ergebnis der Wiederherstellung ließ sich nicht festhalten (Speicher voll?)."
            ) from None
        bootstate.update_state(data_dir, pre_restore=None)
        _say("Wiederherstellung abgeschlossen.")


# Ein Importfehler beim Laden dieses Moduls (fehlende Abhängigkeit im Image) kommt nicht bis hierher; die Notseite zeigt
# dann ohne Einzelheiten, dass der Start abgebrochen ist, und das Protokoll des Containers hat die Meldung.
def main(argv: Sequence[str] | None = None) -> int:
    """Rückgabe: 0 weiter mit der Anwendung, 75 die Sperre gehört einem anderen Prozess (nichts geändert, keine Notseite),
    1 Notseite statt Anwendung; auch ein unerwarteter Fehler endet so statt in einer Neustart-Schleife."""
    settings = get_settings()
    settings.ensure_data_dir()
    data_dir = settings.data_dir

    # Hält die laufende Anwendung die Sperre (z. B. ein `compose run` neben dem laufenden Container), ändert `boot` nichts.
    try:
        lock = bootstate.acquire_lock(data_dir, purpose="boot", wait_s=LOCK_WAIT_S)
    except bootstate.LockHeld:
        _say("Der Datenordner wird gerade von einem anderen Prozess benutzt (läuft Nodvard Deck schon?). Es wurde nichts verändert.")
        return EXIT_LOCKED
    if not lock.held:
        _say("Hinweis: Der Datenordner ließ sich nicht sperren (Dateisystem ohne Sperren?). Bitte nie zwei Container mit demselben Datenordner starten.")

    try:
        try:
            _boot(settings)
        except BootFailure as exc:
            # Der Grund steht bereinigt in `state.json` (`failure`); `deploy/entrypoint.sh` startet dann die Notseite.
            _say(f"FEHLER ({exc.kind}): {exc.reason}")
            _record_failure(settings, exc)
            return EXIT_FAILED
        except Exception as exc:  # noqa: BLE001 - nie als Absturz mit Neustart-Schleife enden
            traceback.print_exc()
            _LOG.extend(line for chunk in traceback.format_exception(exc) for line in chunk.splitlines())
            failure = BootFailure("unexpected", f"Beim Start ist etwas Unerwartetes schiefgegangen ({type(exc).__name__}).")
            _say(f"FEHLER (unexpected): {failure.reason}")
            _record_failure(settings, failure)
            return EXIT_FAILED
        # Geschafft: Ein vorheriger Fehler ist erledigt, der Notfallcode wird nicht mehr gebraucht.
        with contextlib.suppress(Exception):
            bootstate.update_state(data_dir, app_version=_running_version(settings), failure=None)
            bootstate.clear_rescue_code(data_dir)
        return EXIT_OK
    finally:
        lock.release()  # die Anwendung (gleich, im selben Container) bekommt sie danach


if __name__ == "__main__":
    # Auch ohne den Entrypoint (`compose exec` erbt dessen umask nicht): neue Dateien nur für den Besitzer.
    os.umask(0o077)
    sys.exit(main())
