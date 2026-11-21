"""Shield: einen Ordner mit vielen Funden ausnehmen und die offenen Funde darin schließen."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

SRC = Path(__file__).resolve().parents[2] / "extensions" / "shield" / "src"
BIN = "/usr/share/exploitdb-bin-sploits/bin-sploits"
CHAIN = [BIN, "/usr/share/exploitdb-bin-sploits", "/usr/share", "/usr", "/"]
ALL_ROOT = "755 0 directory\n" * 5
MOUNTINFO = (
    "22 28 0:21 / /sys rw,nosuid,nodev,noexec,relatime shared:7 - sysfs sysfs rw\n"
    "28 1 8:2 / / rw,relatime shared:1 - ext4 /dev/sda2 rw,errors=remount-ro\n"
    "35 28 0:30 / /run rw,nosuid,nodev shared:5 - tmpfs tmpfs rw,size=401932k,mode=755\n"
    "41 28 8:1 / /boot/efi rw,relatime shared:12 - vfat /dev/sda1 rw,fmask=0077\n"
)


@pytest.fixture
async def shield(monkeypatch):
    global ExcludeRefused
    monkeypatch.syspath_prepend(str(SRC))
    from nodvard_deck_ext_shield.defender import Defender, ExcludeRefused
    from nodvard_deck_ext_shield.models import Base, FindingRecord

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    stored: dict = {"exclude_paths": ["/srv/medien"], "auto_quarantine": True}

    @asynccontextmanager
    async def session():
        async with maker() as s:
            yield s
            await s.commit()

    async def get():
        return dict(stored)

    async def update(key, transform):
        if stored.get("fail"):
            raise RuntimeError("Einstellungen gesperrt")
        stored[key] = transform(stored.get(key))

    known = {"h1": SimpleNamespace(id="h1"), "h2": SimpleNamespace(id="h2")}

    async def hosts():
        return list(known.values())

    async def host(host_id):
        return known.get(host_id)

    # Antwort von `stat -c '%a %u %F'` für BIN und alle Elternordner bis `/`; ein Test ersetzt sie, `argv` merkt sich den letzten Aufruf.
    stat: dict = {"out": ALL_ROOT, "code": 0, "raise": False, "argv": None, "find": "", "find_code": 0,
            "mounts": MOUNTINFO, "mounts_code": 0}

    @contextmanager
    def read_access(_host):
        yield

    async def run_argv(_host, argv, **_kw):
        if argv[2] == "find":
            return SimpleNamespace(exit_code=stat["find_code"], stdout=stat["find"], stderr="")
        if argv[2] == "cat":
            return SimpleNamespace(exit_code=stat["mounts_code"], stdout=stat["mounts"], stderr="")
        stat["argv"] = argv
        if stat["raise"]:
            raise OSError("keine Verbindung")
        return SimpleNamespace(exit_code=stat["code"], stdout=stat["out"], stderr="")

    ctx = SimpleNamespace(
        db=SimpleNamespace(session=session), settings=SimpleNamespace(get=get, update=update),
        hosts=SimpleNamespace(list=hosts, get=host), exec=SimpleNamespace(read_access=read_access, run_argv=run_argv),
    )
    rows = [
        ("a", "h1", f"{BIN}/40990.zip"), ("b", "h1", f"{BIN}/sub/41000.zip"), ("c", "h1", f"{BIN}-alt/1.zip"),
        ("d", "h2", f"{BIN}/40990.zip"), ("e", "h1", "/srv/upload/evil.sh"), ("f", "h1", "/srv/*/1.zip"),
    ]
    async with maker() as s:
        for fid, host, path in rows:
            s.add(FindingRecord(id=fid, host_id=host, host_name=host, path=path, signature="X", status="detected",
                                detected_at=datetime.now(UTC)))
        await s.commit()
    defender = Defender(ctx)
    try:
        stored["stat"] = stat
        yield defender, stored
    finally:
        await engine.dispose()
        for name in list(sys.modules):
            if name.startswith("nodvard_deck_ext_shield"):
                del sys.modules[name]


async def _status(defender) -> dict[str, str]:
    return {f["id"]: f["status"] for f in await defender.list_findings()}


async def test_closes_findings_below_the_folder_and_adds_it_to_the_exclusions(shield):
    defender, stored = shield
    assert await defender.exclude_folder_for_findings("h1", BIN) == (BIN, 2)
    assert await _status(defender) == {"a": "ignored", "b": "ignored", "c": "detected", "d": "detected", "e": "detected", "f": "detected"}
    assert stored["exclude_paths"] == ["/srv/medien", BIN]
    assert stored["auto_quarantine"] is True


async def test_a_folder_already_excluded_is_not_added_twice(shield):
    defender, stored = shield
    stored["exclude_paths"] = [BIN + "/"]
    assert await defender.exclude_folder_for_findings("h1", BIN) == (BIN, 2)
    assert stored["exclude_paths"] == [BIN + "/"]


@pytest.mark.parametrize("folder", ["/", "relativ", "/usr/../srv/upload", f"{BIN}\n", "/leer/ohne/funde", BIN + "x", "/usr", "/usr/share", "/srv"])
async def test_refuses_folders_that_are_invalid_or_without_open_findings(shield, folder):
    defender, stored = shield
    with pytest.raises(ExcludeRefused):
        await defender.exclude_folder_for_findings("h1", folder)
    assert set((await _status(defender)).values()) == {"detected"}
    assert stored["exclude_paths"] == ["/srv/medien"]


async def test_findings_stay_open_when_saving_the_exclusion_fails(shield):
    defender, stored = shield
    stored["fail"] = True
    with pytest.raises(RuntimeError):
        await defender.exclude_folder_for_findings("h1", BIN)
    assert set((await _status(defender)).values()) == {"detected"}


async def test_the_exclusion_counts_for_every_server_but_only_the_chosen_ones_findings_close(shield):
    defender, stored = shield
    await defender.exclude_folder_for_findings("h1", BIN)
    assert (await _status(defender))["d"] == "detected"
    assert BIN in stored["exclude_paths"]


async def test_a_literal_star_folder_does_not_widen_to_its_parent(shield):
    defender, stored = shield
    with pytest.raises(ExcludeRefused):
        await defender.exclude_folder_for_findings("h1", "/srv/*")
    assert (await _status(defender))["f"] == "detected"
    assert stored["exclude_paths"] == ["/srv/medien"]


async def test_a_literal_star_folder_with_findings_in_the_parent_changes_nothing(shield):
    defender, stored = shield
    async with defender._ctx.db.session() as s:
        from nodvard_deck_ext_shield.models import FindingRecord

        for fid, path in [("g", "/srv/*/2.zip"), ("h", "/srv/direkt.zip")]:
            s.add(FindingRecord(id=fid, host_id="h1", host_name="h1", path=path, signature="X", status="detected",
                                detected_at=datetime.now(UTC)))
    for folder in ("/srv/*", "/srv/*/", "/srv/**"):
        with pytest.raises(ExcludeRefused):
            await defender.exclude_folder_for_findings("h1", folder)
    assert set((await _status(defender)).values()) == {"detected"}
    assert stored["exclude_paths"] == ["/srv/medien"]


@pytest.mark.parametrize("folder", ["/srv/*/.", "/srv/*", "/srv/./", "/srv//x", "/srv/x/..", "/srv/upload/", "/srv/./upload", "/srv//upload"])
async def test_only_a_folder_in_normal_form_is_accepted(shield, folder):
    defender, stored = shield
    async with defender._ctx.db.session() as s:
        from nodvard_deck_ext_shield.models import FindingRecord

        s.add(FindingRecord(id="g", host_id="h1", host_name="h1", path="/srv/*/1.zip", signature="X", status="detected",
                            detected_at=datetime.now(UTC)))
    with pytest.raises(ExcludeRefused):
        await defender.exclude_folder_for_findings("h1", folder)
    assert set((await _status(defender)).values()) == {"detected"}
    assert stored["exclude_paths"] == ["/srv/medien"]


PROTECTED = [
    "/tmp", "/var/tmp", "/dev/shm", "/run", "/etc", "/root", "/home", "/boot", "/bin", "/sbin", "/lib", "/lib64",
    "/usr", "/usr/bin", "/usr/sbin", "/usr/lib", "/usr/lib64", "/usr/local/bin", "/var/www", "/srv",
    "/tmp/ablage", "/var/tmp/x", "/dev/shm/x", "/etc/cron.d", "/root/.cache", "/home/anna", "/var/www/html",
    "/usr/share", "/usr/lib/x86_64-linux-gnu", "/srv/upload", "/lib/modules", "/usr/local/bin/tools",
]


@pytest.mark.parametrize("folder", PROTECTED)
async def test_system_folders_and_their_first_level_are_refused(shield, folder):
    defender, stored = shield
    async with defender._ctx.db.session() as s:
        from nodvard_deck_ext_shield.models import FindingRecord

        s.add(FindingRecord(id="g", host_id="h1", host_name="h1", path=f"{folder}/1.zip", signature="X", status="detected",
                            detected_at=datetime.now(UTC)))
    with pytest.raises(ExcludeRefused, match="Systemordner"):
        await defender.exclude_folder_for_findings("h1", folder)
    assert stored["stat"]["argv"] is None
    assert stored["exclude_paths"] == ["/srv/medien"]
    assert (await _status(defender))["g"] == "detected"


async def test_the_folder_from_the_user_report_is_still_accepted(shield):
    defender, stored = shield
    assert await defender.exclude_folder_for_findings("h1", BIN) == (BIN, 2)
    assert stored["stat"]["argv"] == ["env", "LC_ALL=C", "stat", "-c", "%a %u %F", "--", *CHAIN]


def _chain(*lines: str) -> str:
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("answer", [
    {"out": _chain("1777 0 directory", *["755 0 directory"] * 4)},
    {"out": _chain("777 0 directory", *["755 0 directory"] * 4)},
    {"out": _chain("757 0 directory", *["755 0 directory"] * 4)},
    {"out": _chain("775 0 directory", *["755 0 directory"] * 4)},
    {"out": _chain("770 0 directory", *["755 0 directory"] * 4)},
    {"out": _chain("755 33 directory", *["755 0 directory"] * 4)},
    {"out": _chain("755 1000 directory", *["755 0 directory"] * 4)},
    {"out": _chain("755 0 directory", "755 0 directory", "775 0 directory", "755 0 directory", "755 0 directory")},
    {"out": _chain("755 0 directory", "755 0 directory", "755 0 directory", "755 1000 directory", "755 0 directory")},
    {"out": _chain("755 0 directory", "755 0 directory", "755 0 directory", "755 0 directory", "1777 0 directory")},
    {"out": _chain("755 0 symbolic link", *["755 0 directory"] * 4)},
    {"out": _chain("755 0 directory", "755 0 symbolic link", "755 0 directory", "755 0 directory", "755 0 directory")},
    {"out": _chain("755 0 directory", "755 0 directory", "755 0 directory", "755 0 symbolic link", "755 0 directory")},
    {"out": _chain("755 0 directory", "755 0 directory", "755 0 directory", "755 0 directory", "755 0 symbolic link")},
    {"out": _chain("644 0 regular file", *["755 0 directory"] * 4)},
    {"out": _chain("755 0 directory", *["755 0 directory"] * 3)},
    {"out": "", "code": 1}, {"out": "stat: cannot statx: No such file or directory\n", "code": 1},
    {"out": "755 directory\n" * 5}, {"out": "755\n"}, {"out": "Ordner\n"}, {"raise": True},
])
async def test_a_folder_others_can_write_to_or_with_unknown_rights_is_refused(shield, answer):
    defender, stored = shield
    stored["stat"].update(answer)
    with pytest.raises(ExcludeRefused):
        await defender.exclude_folder_for_findings("h1", BIN)
    assert set((await _status(defender)).values()) == {"detected"}
    assert stored["exclude_paths"] == ["/srv/medien"]


async def test_a_folder_owned_by_a_service_account_is_refused_with_its_path(shield):
    defender, _ = shield
    folder = "/data/uploads/a"
    async with defender._ctx.db.session() as s:
        from nodvard_deck_ext_shield.models import FindingRecord

        s.add(FindingRecord(id="g", host_id="h1", host_name="h1", path=f"{folder}/1.zip", signature="X", status="detected",
                            detected_at=datetime.now(UTC)))
    defender_stat = shield[1]["stat"]
    defender_stat["out"] = _chain("755 33 directory", "755 0 directory", "755 0 directory", "755 0 directory")
    with pytest.raises(ExcludeRefused, match=r"/data/uploads/a gehört nicht root"):
        await defender.exclude_folder_for_findings("h1", folder)
    assert defender_stat["argv"][-4:] == [folder, "/data/uploads", "/data", "/"]


async def test_a_nested_folder_below_a_protected_one_is_still_refused_by_the_new_list(shield):
    defender, _ = shield
    for folder in ["/var/lib/x", "/var/log/x", "/var/spool/x", "/var/mail/x", "/opt/app", "/mnt/disk", "/media/usb",
                   "/snap/x", "/usr/local/sbin/x", "/proc/1", "/sys/x", "/var/lib", "/opt"]:
        with pytest.raises(ExcludeRefused, match="Systemordner"):
            await defender.exclude_folder_for_findings("h1", folder)


def test_the_protected_lists_of_backend_and_page_match(shield):
    import re

    from nodvard_deck_ext_shield import antivirus as av

    page = (SRC.parent / "frontend" / "src" / "SocPage.tsx").read_text(encoding="utf-8")
    block = re.search(r"const PROTECTED_FOLDERS = new Set\(\[(.*?)\]\);", page, re.DOTALL).group(1)
    assert set(re.findall(r'"(/[^"]*)"', block)) == set(av._PROTECTED_FOLDERS)
    lib = re.search(r"const PROTECTED_LIB = /(.*?)/;", page).group(1).replace("\\/", "/")
    assert lib == av._PROTECTED_LIB.pattern


async def test_an_unknown_host_is_refused(shield):
    defender, _ = shield
    with pytest.raises(ExcludeRefused):
        await defender.exclude_folder_for_findings("h9", BIN)


async def test_a_writable_subfolder_blocks_the_exclusion(shield):
    defender, stored = shield
    stored["stat"]["find"] = f"{BIN}/sub\n"
    with pytest.raises(ExcludeRefused, match="Unterordner"):
        await defender.exclude_folder_for_findings("h1", BIN)
    assert set((await _status(defender)).values()) == {"detected"}
    assert stored["exclude_paths"] == ["/srv/medien"]


async def test_an_unreadable_subfolder_check_blocks_the_exclusion(shield):
    defender, stored = shield
    stored["stat"]["find_code"] = 1
    with pytest.raises(ExcludeRefused, match="nicht prüfen"):
        await defender.exclude_folder_for_findings("h1", BIN)
    assert stored["exclude_paths"] == ["/srv/medien"]


async def test_the_count_only_includes_findings_closed_by_this_call(shield, monkeypatch):
    defender, _ = shield
    from nodvard_deck_ext_shield.models import FindingRecord

    original = defender._ctx.db.session

    from contextlib import asynccontextmanager

    calls = {"n": 0}

    @asynccontextmanager
    async def session():
        calls["n"] += 1
        async with original() as s:
            if calls["n"] == 2:  # zweiter Block: der Fund `a` wurde zwischendurch erledigt
                (await s.get(FindingRecord, "a")).status = "ignored"
            yield s

    monkeypatch.setattr(defender._ctx.db, "session", session)
    assert await defender.exclude_folder_for_findings("h1", BIN) == (BIN, 1)


async def test_reopen_folder_reopens_every_finding_the_exclusion_closed(shield):
    defender, stored = shield
    from nodvard_deck_ext_shield.models import FindingRecord

    async with defender._ctx.db.session() as s:
        for i in range(250):
            s.add(FindingRecord(id=f"m{i}", host_id="h1", host_name="h1", path=f"{BIN}/m{i}.zip", signature="X",
                                status="detected", detected_at=datetime.now(UTC)))
    await defender.exclude_folder_for_findings("h1", BIN)
    assert await defender.reopen_folder("h1", BIN) == 252
    status = await _status_all(defender)
    assert {k: v for k, v in status.items() if k not in ("c", "d", "e", "f")} == {k: "detected" for k in status if k not in ("c", "d", "e", "f")}
    assert BIN in stored["exclude_paths"]


async def _status_all(defender) -> dict[str, str]:
    from nodvard_deck_ext_shield.models import FindingRecord
    from sqlalchemy import select

    async with defender._ctx.db.session() as s:
        return {r.id: r.status for r in (await s.execute(select(FindingRecord))).scalars()}


async def test_reopen_folder_leaves_other_hosts_and_manually_ignored_findings_alone(shield):
    defender, _ = shield
    await defender.exclude_folder_for_findings("h1", BIN)
    assert await defender.ignore("d")
    assert await defender.reopen_folder("h1", BIN) == 2
    status = await _status_all(defender)
    assert status["d"] == "ignored" and status["a"] == "detected"
    assert await defender.reopen_folder("h1", BIN) == 0


async def test_reopen_folder_skips_a_file_that_already_has_an_open_finding(shield):
    defender, _ = shield
    from nodvard_deck_ext_shield.models import FindingRecord

    await defender.exclude_folder_for_findings("h1", BIN)
    async with defender._ctx.db.session() as s:
        s.add(FindingRecord(id="neu", host_id="h1", host_name="h1", path=f"{BIN}/40990.zip", signature="X",
                            status="detected", detected_at=datetime.now(UTC)))
    assert await defender.reopen_folder("h1", BIN) == 1


@pytest.mark.parametrize("folder", ["relativ", "/usr/../srv", BIN + "/", "/srv/*"])
async def test_reopen_folder_refuses_invalid_paths(shield, folder):
    defender, _ = shield
    with pytest.raises(ExcludeRefused):
        await defender.reopen_folder("h1", folder)


async def test_unclear_rights_point_to_the_settings_page(shield):
    defender, _ = shield
    shield[1]["stat"]["raise"] = True
    with pytest.raises(ExcludeRefused, match="Ausgenommene Ordner"):
        await defender.exclude_folder_for_findings("h1", BIN)


def _mounted(path: str) -> str:
    return MOUNTINFO + f"77 28 0:52 / {path} rw,relatime shared:40 - nfs4 nas:/export rw,vers=4.2\n"


@pytest.mark.parametrize("sub", [BIN + "/usb", BIN + "/mein\\040nas", BIN + "/a/b"])
async def test_a_mount_below_the_folder_blocks_the_exclusion(shield, sub):
    defender, stored = shield
    stored["stat"]["mounts"] = _mounted(sub)
    with pytest.raises(ExcludeRefused, match="eingehängt"):
        await defender.exclude_folder_for_findings("h1", BIN)
    assert stored["exclude_paths"] == ["/srv/medien"]
    assert set((await _status(defender)).values()) == {"detected"}


async def test_a_mount_beside_or_at_the_folder_does_not_block(shield):
    defender, stored = shield
    stored["stat"]["mounts"] = _mounted(BIN + "-alt") + _mounted(BIN)
    assert await defender.exclude_folder_for_findings("h1", BIN) == (BIN, 2)


@pytest.mark.parametrize("code,out", [(1, MOUNTINFO), (0, "")])
async def test_unreadable_mounts_block_the_exclusion(shield, code, out):
    defender, stored = shield
    stored["stat"]["mounts_code"], stored["stat"]["mounts"] = code, out
    with pytest.raises(ExcludeRefused, match="nicht prüfen"):
        await defender.exclude_folder_for_findings("h1", BIN)


async def test_reopen_folder_keeps_hand_ignored_findings_with_another_note(shield):
    defender, _ = shield
    from nodvard_deck_ext_shield.models import FindingRecord

    await defender.exclude_folder_for_findings("h1", BIN)
    async with defender._ctx.db.session() as s:
        (await s.get(FindingRecord, "a")).note = "von Hand ignoriert"
    assert await defender.reopen_folder("h1", BIN) == 1
    status = await _status_all(defender)
    assert status["a"] == "ignored" and status["b"] == "detected"


async def test_reopen_folder_keeps_findings_that_are_no_longer_ignored(shield):
    defender, _ = shield
    from nodvard_deck_ext_shield.models import FindingRecord

    await defender.exclude_folder_for_findings("h1", BIN)
    async with defender._ctx.db.session() as s:
        (await s.get(FindingRecord, "a")).status = "quarantined"
    assert await defender.reopen_folder("h1", BIN) == 1
    assert (await _status_all(defender))["a"] == "quarantined"


async def test_reopen_folder_ignores_the_same_note_on_other_hosts_and_outside_the_folder(shield):
    defender, _ = shield
    from nodvard_deck_ext_shield.defender import FOLDER_EXCLUDED_NOTE
    from nodvard_deck_ext_shield.models import FindingRecord

    note = FOLDER_EXCLUDED_NOTE.format(folder=BIN)
    async with defender._ctx.db.session() as s:
        s.add(FindingRecord(id="x", host_id="h2", host_name="h2", path=f"{BIN}/nur-h2.zip", signature="X",
                            status="ignored", note=note, detected_at=datetime.now(UTC)))
        (await s.get(FindingRecord, "c")).status = "ignored"
        (await s.get(FindingRecord, "c")).note = note
    assert await defender.reopen_folder("h1", BIN) == 0
    status = await _status_all(defender)
    assert status["c"] == "ignored" and status["x"] == "ignored"


async def test_a_single_reopen_drops_the_folder_mark_so_a_later_hand_ignore_stays_closed(shield):
    defender, _ = shield
    await defender.exclude_folder_for_findings("h1", BIN)
    assert await defender.reopen("a") == (True, "")
    assert await defender.ignore("a")
    assert await defender.reopen_folder("h1", BIN) == 1
    assert (await _status_all(defender))["a"] == "ignored"
    assert (await _status_all(defender))["b"] == "detected"
