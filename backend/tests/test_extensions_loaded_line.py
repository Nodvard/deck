"""Die Zeile `Erweiterungen geladen: ...`, die jeder Start (genau eine) und jedes Ein- oder Ausschalten danach
ins Container-Protokoll schreibt.

`deploy/pi_switch.sh` und `scripts/deploy_pi.sh` vergleichen die letzte davon vor und nach dem Umschalten: Fehlt
eine Erweiterung, die vorher geladen war, wird zurueckgeschaltet (`backend/tests/test_deploy_switch.py`).
Hier wird die Seite des Dienstes geprueft: welche Kennungen drinstehen, in welcher Form, dass der Start genau
eine Zeile schreibt und dass die letzte Zeile auch nach einem Ein- oder Ausschalten sagt, was gerade laeuft.

Ausgabe per `print` statt `logging` (siehe `services.extensions._announce_loaded`): Der Dienst richtet
fuer seine eigenen Logger nichts ein, eine `log.info`-Zeile kaeme nie im Container-Protokoll an. Die Tests
lesen deshalb die Standardausgabe (`capsys`) und haengen nicht an Loggern, die ein frueherer Alembic-Lauf
im selben Prozess abgeschaltet haben koennte.
"""

from __future__ import annotations

import builtins
import re
import shutil
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from nodvard_deck.core.scheduler import reset_scheduler_service
from nodvard_deck.ext.discovery import discover_all
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import ExtensionRecord
from nodvard_deck.services import extensions as extensions_service
from nodvard_deck.services.extensions import LOADED_LINE_NONE, LOADED_LINE_PREFIX

ROOT = Path(__file__).resolve().parents[2]
REPO_EXTENSIONS_DIR = ROOT / "extensions"


@pytest.fixture(autouse=True)
def _reset_runtime_and_sys_path():
    reset_extension_runtime()
    reset_scheduler_service()
    before = list(sys.path)
    yield
    reset_extension_runtime()
    reset_scheduler_service()
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


def _write_ext(
    root: Path, ext_id: str, *, legacy_ids: list[str] | None = None, on_start_raises: bool = False,
    enable_on: list[str] | None = None,
) -> Path:
    module = "nodvard_deck_ext_" + ext_id.replace("-", "_")
    ext_dir = root / ext_id
    (ext_dir / "src" / module).mkdir(parents=True)
    legacy = "legacy_ids = [" + ", ".join(f'"{x}"' for x in legacy_ids) + "]\n" if legacy_ids else ""
    legacy += "enable_on = [" + ", ".join(f'"{x}"' for x in enable_on) + "]\n" if enable_on else ""
    (ext_dir / "extension.toml").write_text(
        f'[extension]\nid = "{ext_id}"\nname = "Test {ext_id}"\nversion = "0.2.0"\napi_version = "0.1"\n'
        f'entrypoint = "{module}:Extension"\npermissions = []\n{legacy}',
        encoding="utf-8",
    )
    start = "raise RuntimeError('on_start kaputt')" if on_start_raises else "pass"
    (ext_dir / "src" / module / "__init__.py").write_text(
        "class Extension:\n"
        "    async def setup(self, ctx):\n        pass\n"
        f"    async def on_start(self, ctx):\n        {start}\n"
        "    async def on_stop(self, ctx):\n        pass\n",
        encoding="utf-8",
    )
    return ext_dir


def _settings(test_settings, tmp_path):
    return test_settings.model_copy(update={"extensions_dir": tmp_path / "extensions", "ext_data_dir": tmp_path / "ext-data"})


def _app() -> FastAPI:
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)
    return app


async def _boot(app, db_session, settings) -> None:
    """Ein Start des Dashboards: frischer Prozess, Entdecken, eingeschaltete Erweiterungen laden."""
    reset_extension_runtime()
    reset_scheduler_service()
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_"):
            del sys.modules[name]
    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.load_enabled_from_registry(app, db_session, settings)
    await db_session.commit()


async def _switch_on(db_session, *ext_ids: str) -> None:
    for ext_id in ext_ids:
        (await db_session.get(ExtensionRecord, ext_id)).state = "enabled"
    await db_session.commit()


def _loaded_lines(capsys) -> list[str]:
    out = capsys.readouterr().out
    return [line for line in out.splitlines() if LOADED_LINE_PREFIX in line]


async def _first_boot_then_enable(tmp_path, db_session, test_settings, *ext_ids):
    """Erweiterungen entdecken (Zeilen entstehen ausgeschaltet), dann einschalten und neu starten."""
    settings = _settings(test_settings, tmp_path)
    app = _app()
    await _boot(app, db_session, settings)
    await _switch_on(db_session, *ext_ids)
    return app, settings


@pytest.mark.asyncio
async def test_boot_writes_exactly_one_line_with_the_loaded_ids_sorted(tmp_path, db_session, test_settings, capsys):
    # Reihenfolge der Ordner und der Zeilen ist nicht die Reihenfolge der Ausgabe.
    ids = ("zeta-ext", "echo-ext", "alpha-ext", "mid-ext", "bravo-ext")
    for ext_id in ids:
        _write_ext(tmp_path / "extensions", ext_id)
    app, settings = await _first_boot_then_enable(tmp_path, db_session, test_settings, *ids)
    capsys.readouterr()

    await _boot(app, db_session, settings)

    lines = _loaded_lines(capsys)
    assert lines == ["Erweiterungen geladen: alpha-ext,bravo-ext,echo-ext,mid-ext,zeta-ext"]
    assert set(get_extension_runtime().loaded) == set(ids)


@pytest.mark.asyncio
async def test_boot_without_a_loaded_extension_writes_the_none_line(tmp_path, db_session, test_settings, capsys):
    _write_ext(tmp_path / "extensions", "off-ext")  # entdeckt, aber ausgeschaltet
    settings = _settings(test_settings, tmp_path)

    await _boot(_app(), db_session, settings)

    assert _loaded_lines(capsys) == ["Erweiterungen geladen: (keine)"]
    assert LOADED_LINE_NONE == "(keine)"


@pytest.mark.asyncio
async def test_a_disabled_extension_is_not_listed(tmp_path, db_session, test_settings, capsys):
    _write_ext(tmp_path / "extensions", "on-ext")
    _write_ext(tmp_path / "extensions", "off-ext")
    app, settings = await _first_boot_then_enable(tmp_path, db_session, test_settings, "on-ext")
    capsys.readouterr()

    await _boot(app, db_session, settings)

    assert _loaded_lines(capsys) == ["Erweiterungen geladen: on-ext"]


@pytest.mark.asyncio
async def test_an_extension_that_is_enabled_but_gone_is_not_listed(tmp_path, db_session, test_settings, capsys):
    # Genau der Fall, den die Auslieferung abfangen soll: Die Zeile steht auf "eingeschaltet", der Dienst
    # findet die Erweiterung aber nicht (Ordner umbenannt, Paket fehlt). Nichts meldet einen Fehler, nur die
    # Zeile zeigt, dass sie nicht laeuft.
    ext_dir = _write_ext(tmp_path / "extensions", "gone-ext")
    _write_ext(tmp_path / "extensions", "stays-ext")
    app, settings = await _first_boot_then_enable(tmp_path, db_session, test_settings, "gone-ext", "stays-ext")
    capsys.readouterr()
    await _boot(app, db_session, settings)
    assert _loaded_lines(capsys) == ["Erweiterungen geladen: gone-ext,stays-ext"]

    shutil.rmtree(ext_dir)
    await _boot(app, db_session, settings)

    assert _loaded_lines(capsys) == ["Erweiterungen geladen: stays-ext"]
    record = await db_session.get(ExtensionRecord, "gone-ext")
    assert record.state == "enabled", "die Entscheidung 'eingeschaltet' bleibt, nur geladen ist sie nicht"


@pytest.mark.asyncio
async def test_an_extension_whose_start_fails_is_not_listed(tmp_path, db_session, test_settings, capsys):
    _write_ext(tmp_path / "extensions", "broken-ext", on_start_raises=True)
    _write_ext(tmp_path / "extensions", "fine-ext")
    app, settings = await _first_boot_then_enable(tmp_path, db_session, test_settings, "broken-ext", "fine-ext")
    capsys.readouterr()

    await _boot(app, db_session, settings)

    assert _loaded_lines(capsys) == ["Erweiterungen geladen: fine-ext"]
    record = await db_session.get(ExtensionRecord, "broken-ext")
    assert record.state == "error", "Zustand wie bisher; nur die Zeile sagt, dass sie nicht laeuft"
    assert "broken-ext" in get_extension_runtime().loaded, "bleibt zum Aufraeumen geladen, zaehlt aber nicht als laufend"


@pytest.mark.asyncio
async def test_a_renamed_extension_is_listed_with_the_id_of_its_stored_row(tmp_path, db_session, test_settings, capsys):
    # Nach einer Umbenennung (`legacy_ids`) nutzt die Erweiterung die Zeile der alten Kennung weiter: In der
    # Zeile steht diese Speicher-Kennung. So bleibt der Vergleich zwischen altem und neuem Image eindeutig.
    _write_ext(tmp_path / "extensions", "new-ext", legacy_ids=["old-ext"])
    db_session.add(ExtensionRecord(id="old-ext", version="0.1.0", api_version="0.1", state="enabled", source="bundled", manifest={}))
    await db_session.commit()

    await _boot(_app(), db_session, _settings(test_settings, tmp_path))

    assert _loaded_lines(capsys) == ["Erweiterungen geladen: old-ext"]
    assert get_extension_runtime().loaded["new-ext"].store_id == "old-ext"
    assert [r for r in (await db_session.execute(select(ExtensionRecord.id))).scalars().all() if r == "new-ext"] == []


@pytest.mark.asyncio
async def test_a_fresh_installation_of_the_renamed_extension_lists_the_new_id(tmp_path, db_session, test_settings, capsys):
    _write_ext(tmp_path / "extensions", "new-ext", legacy_ids=["old-ext"])
    app, settings = await _first_boot_then_enable(tmp_path, db_session, test_settings, "new-ext")
    capsys.readouterr()

    await _boot(app, db_session, settings)

    assert _loaded_lines(capsys) == ["Erweiterungen geladen: new-ext"]


@pytest.mark.asyncio
async def test_a_stale_twin_row_is_not_listed_twice(tmp_path, db_session, test_settings, capsys):
    # Beide Zeilen stehen auf "eingeschaltet" (Neuinstallation, Rueckweg, erneutes Update): geladen wird einmal,
    # unter der Zeile, die gilt.
    _write_ext(tmp_path / "extensions", "new-ext", legacy_ids=["old-ext"])
    db_session.add_all([
        ExtensionRecord(id="new-ext", version="0.2.0", api_version="0.1", state="enabled", source="bundled", manifest={}),
        ExtensionRecord(id="old-ext", version="0.1.0", api_version="0.1", state="enabled", source="bundled", manifest={}),
    ])
    await db_session.commit()

    await _boot(_app(), db_session, _settings(test_settings, tmp_path))

    assert _loaded_lines(capsys) == ["Erweiterungen geladen: new-ext"]


@pytest.mark.asyncio
async def test_a_stale_twin_row_does_not_list_an_extension_whose_start_failed(tmp_path, db_session, test_settings, capsys):
    # Die Zeile, die die Erweiterung nutzt, steht nach dem Absturz von on_start() auf "error"; der verwaiste Zwilling steht
    # weiter auf "eingeschaltet". Er darf sie nicht als laufend melden, sonst uebersieht das Ausliefern genau diesen Fehler.
    _write_ext(tmp_path / "extensions", "new-ext", legacy_ids=["old-ext"], on_start_raises=True)
    db_session.add_all([
        ExtensionRecord(id="new-ext", version="0.2.0", api_version="0.1", state="enabled", source="bundled", manifest={}),
        ExtensionRecord(id="old-ext", version="0.1.0", api_version="0.1", state="enabled", source="bundled", manifest={}),
    ])
    await db_session.commit()

    await _boot(_app(), db_session, _settings(test_settings, tmp_path))

    assert _loaded_lines(capsys) == ["Erweiterungen geladen: (keine)"]
    assert (await db_session.get(ExtensionRecord, "new-ext")).state == "error"
    assert (await db_session.get(ExtensionRecord, "old-ext")).state == "enabled", "der Zwilling bleibt unberuehrt"


@pytest.mark.asyncio
async def test_switching_off_and_on_while_running_writes_what_runs_now(tmp_path, db_session, test_settings, capsys):
    # Die letzte Zeile im Protokoll sagt, was gerade laeuft, nicht nur, was beim Start lief: Sonst hielte das Ausliefern
    # eine seit dem Start ausgeschaltete Erweiterung fuer verloren und schaltete einen gesunden Stand zurueck.
    _write_ext(tmp_path / "extensions", "a-ext")
    _write_ext(tmp_path / "extensions", "b-ext")
    app, settings = await _first_boot_then_enable(tmp_path, db_session, test_settings, "a-ext", "b-ext")
    await _boot(app, db_session, settings)
    assert _loaded_lines(capsys)[-1] == "Erweiterungen geladen: a-ext,b-ext"

    await extensions_service.disable_extension(app, db_session, "b-ext")
    await db_session.commit()
    assert _loaded_lines(capsys) == ["Erweiterungen geladen: a-ext"]

    await extensions_service.enable_extension(app, db_session, settings, "b-ext")
    await db_session.commit()
    assert _loaded_lines(capsys) == ["Erweiterungen geladen: a-ext,b-ext"]

    await extensions_service.disable_extension(app, db_session, "a-ext")
    await extensions_service.disable_extension(app, db_session, "b-ext")
    await db_session.commit()
    assert _loaded_lines(capsys) == ["Erweiterungen geladen: b-ext", "Erweiterungen geladen: (keine)"]

    # Nach einem Neustart steht dasselbe drin wie zuletzt.
    await _boot(app, db_session, settings)
    assert _loaded_lines(capsys) == ["Erweiterungen geladen: (keine)"]


@pytest.mark.asyncio
async def test_switching_on_an_extension_whose_start_fails_writes_the_list_without_it(tmp_path, db_session, test_settings, capsys):
    _write_ext(tmp_path / "extensions", "fine-ext")
    _write_ext(tmp_path / "extensions", "broken-ext", on_start_raises=True)
    app, settings = await _first_boot_then_enable(tmp_path, db_session, test_settings, "fine-ext")
    await _boot(app, db_session, settings)
    capsys.readouterr()

    await extensions_service.enable_extension(app, db_session, settings, "broken-ext")
    await db_session.commit()

    assert _loaded_lines(capsys) == ["Erweiterungen geladen: fine-ext"]
    assert (await db_session.get(ExtensionRecord, "broken-ext")).state == "error"


@pytest.mark.asyncio
async def test_switching_on_by_itself_writes_the_new_list(tmp_path, db_session, test_settings, capsys):
    # Automatisch eingeschaltet (`enable_on`, z. B. beim ersten SSH-Zugang): auch das aendert, was laeuft.
    _write_ext(tmp_path / "extensions", "auto-ext", enable_on=["host_credential"])
    settings = _settings(test_settings, tmp_path)
    app = _app()
    await _boot(app, db_session, settings)
    assert _loaded_lines(capsys) == ["Erweiterungen geladen: (keine)"]

    enabled = await extensions_service.auto_enable_for(app, db_session, settings, "host_credential")
    await db_session.commit()

    assert enabled == ["auto-ext"]
    assert _loaded_lines(capsys) == ["Erweiterungen geladen: auto-ext"]


@pytest.mark.asyncio
async def test_switching_while_running_writes_nothing_to_the_database_before_the_caller_commits(tmp_path, db_session, test_settings, capsys):
    # Die Zeile liest den Zustand im Speicher, ohne vorzeitig zu schreiben: Ein Schreibvorgang mitten im Ein- oder
    # Ausschalten hielte die Sperre der Datenbank, waehrend noch Code der Erweiterung laeuft (siehe `enable_extension`).
    _write_ext(tmp_path / "extensions", "a-ext")
    app, settings = await _first_boot_then_enable(tmp_path, db_session, test_settings, "a-ext")
    await _boot(app, db_session, settings)
    capsys.readouterr()

    await extensions_service.disable_extension(app, db_session, "a-ext")

    assert _loaded_lines(capsys) == ["Erweiterungen geladen: (keine)"]
    record = await db_session.get(ExtensionRecord, "a-ext")
    assert record in db_session.dirty, "der neue Zustand ist noch nicht geschrieben, das macht der Aufrufer"
    await db_session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [OSError("Rohr kaputt"), ValueError("I/O operation on closed file")])
async def test_a_failing_output_never_stops_the_boot(tmp_path, db_session, test_settings, monkeypatch, failure):
    _write_ext(tmp_path / "extensions", "some-ext")
    app, settings = await _first_boot_then_enable(tmp_path, db_session, test_settings, "some-ext")
    real_print = builtins.print

    def broken_print(*args, **kwargs):
        if args and str(args[0]).startswith(LOADED_LINE_PREFIX):
            raise failure
        return real_print(*args, **kwargs)

    monkeypatch.setattr(builtins, "print", broken_print)

    await _boot(app, db_session, settings)  # wirft nicht

    assert "some-ext" in get_extension_runtime().loaded


def test_every_id_of_the_bundled_extensions_is_safe_inside_the_comma_separated_list():
    # Das Shell-Skript zerlegt die Liste an Kommas und prueft jede Kennung gegen dieses Muster.
    found = [d for d in discover_all(REPO_EXTENSIONS_DIR) if d.ok]
    assert found, "die mitgelieferten Erweiterungen muessen gefunden werden"
    safe = re.compile(r"^[a-z][a-z0-9-]*$")
    for d in found:
        assert d.manifest is not None
        for ident in (d.manifest.id, *d.manifest.legacy_ids):
            assert safe.match(ident), ident
            assert "," not in ident and ident != LOADED_LINE_NONE
