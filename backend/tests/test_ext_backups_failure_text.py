"""Backup-Fehler in Klartext (failure_text.py): feste Erklaerungen aus dem Protokoll eines fehlgeschlagenen Laufs,
`GET /ext/backups/jobs/{job_ref}/failure`, die Ursache in der Meldung des Waechters -- und "WARNINGS: n" zaehlt als
erfolgreich. Gegen denselben Proxmox-Mock wie test_ext_backups.py."""

from __future__ import annotations

import sys

import pytest
from test_ext_backups import (  # noqa: F401 - Fixtures
    _FRESH,
    REPO_EXTENSIONS_DIR,
    _auth_header,
    _backup_notifications,
    _body_of,
    _cleanup_sys_path,
    _make_recent,
    _run_backup_watch,
    _setup_backups,
    mock_backup_api,
)

UPID_100 = "UPID:pve2:0001A2B3:00C0FFEE:6703F1A0:vzdump:100:root@pam:"
UPID_GROUP = "UPID:pve2:0001A2B4:00C0FFEF:6703F1A1:vzdump::root@pam:"


def _explain():
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "backups" / "src"))  # _cleanup_sys_path raeumt auf
    from nodvard_deck_ext_backups.failure_text import explain

    return explain


def _log(*errors: str, vmid: str = "100") -> list[str]:
    return [
        "INFO: starting new backup job: vzdump 100 --mode snapshot --storage nas",
        f"INFO: Starting Backup of VM {vmid} (qemu)",
        "INFO: Backup started at 2026-10-08 01:00:01",
        "INFO: status = running",
        *errors,
        "INFO: Failed at 2026-10-08 01:03:12",
        "INFO: Backup job finished with errors",
        "TASK ERROR: job errors",
    ]


@pytest.mark.parametrize(
    ("line", "key"),
    [
        ("ERROR: Backup of VM 100 failed - vma_queue_write: write error - No space left on device", "no_space"),
        ("ERROR: Backup of VM 100 failed - vma_queue_write: write error - Broken pipe", "target_lost"),
        ("ERROR: Backup of VM 100 failed - job failed with err -116 - Stale file handle", "target_lost"),
        ("ERROR: Backup of VM 100 failed - storage 'nas' is not online", "storage_offline"),
        ("ERROR: Backup of VM 100 failed - unable to activate storage 'nas' - directory '/mnt/pve/nas' does not exist or is unreachable", "storage_offline"),
        ("ERROR: Backup of VM 100 failed - VM is locked (snapshot)", "locked"),
        ("ERROR: Backup of VM 100 failed - can't acquire lock '/var/run/vzdump.lock' - got timeout", "locked"),
        ("ERROR: Backup of VM 100 failed - backup connect failed: command error: authentication failed - invalid credentials", "pbs_auth"),
        ("ERROR: VM 100 qmp command 'guest-fsfreeze-freeze' failed - got timeout", "guest_agent"),
        ("ERROR: Backup of VM 100 failed - VM 100 qmp command 'backup' failed - got timeout", "vm_timeout"),
        ("ERROR: Backup of VM 100 failed - unable to create temporary directory '/mnt/pve/nas/dump/vzdump-qemu-100.tmp'", "permission"),
        ("ERROR: Backup of VM 100 failed - job failed with err -5 - Input/output error", "io_error"),
        ("ERROR: Backup of VM 100 failed - Configuration file 'nodes/pve2/qemu-server/100.conf' does not exist", "guest_missing"),
        ("ERROR: Backup of VM 100 failed - interrupted by signal", "aborted"),
        ("ERROR: Backup of VM 100 failed - error fetching datastores - 500 Can't connect to backup.example.org:8007 (Connection refused)", "unreachable"),
        ("ERROR: Backup of VM 100 failed - vma_queue_write: write error - Connection timed out", "target_lost"),
        ("ERROR: Backup of VM 100 failed - job failed with err -107 - Transport endpoint is not connected", "target_lost"),
        ("ERROR: Backup of VM 100 failed - cfs-lock 'storage-nas' error: got lock request timeout", "locked"),
        ("ERROR: Backup of VM 100 failed - storage 'nas' does not exist", "storage_offline"),
        ("ERROR: Backup of VM 100 failed - storage 'nas' is disabled", "storage_offline"),
        ("ERROR: Backup of VM 100 failed - vma_queue_write: write error - Read-only file system", "permission"),
        ("ERROR: Backup of VM 100 failed - error fetching datastores - Temporary failure in name resolution", "unreachable"),
        ("ERROR: Backup of VM 100 failed - unable to find VM '100'", "guest_missing"),
        ("ERROR: Backup of VM 100 failed - permission check failed (/vms/100, VM.Backup)", "missing_right"),
        ("ERROR: Backup of VM 100 failed - something nobody has seen before", "unknown"),
    ],
)
def test_common_errors_get_a_fixed_explanation(line, key):
    explain = _explain()
    result = explain("100", "job errors", _log(line))
    assert result["key"] == key
    assert result["what"] and result["todo"]
    assert result["detail"] == line.removeprefix("ERROR: ")


def test_every_key_has_both_sentences_in_one_collection():
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "backups" / "src"))
    from nodvard_deck_ext_backups.failure_text import RULES, TEXTS

    assert {f"backup.failure.{key}" for key, _ in RULES} | {"backup.failure.unknown", "backup.failure.other_guest"} == set(TEXTS)
    assert all(set(text) == {"what", "todo"} and all(text.values()) for text in TEXTS.values())


def test_in_a_group_job_only_the_lines_of_this_guest_count():
    explain = _explain()
    log = [
        "INFO: Starting Backup of VM 100 (qemu)",
        "ERROR: Backup of VM 100 failed - vma_queue_write: write error - No space left on device",
        "INFO: Starting Backup of VM 102 (qemu)",
        "INFO: skipping guest-agent 'fs-freeze', agent configured but not running?",
        "ERROR: Backup of VM 102 failed - vma_queue_write: write error - Broken pipe",
        "TASK ERROR: job errors",
    ]
    assert explain("102", "job errors", log)["key"] == "target_lost", "die Gast-Agent-Zeile (INFO) zaehlt nicht"
    assert explain("100", "job errors", log)["key"] == "no_space"


def test_a_reason_only_in_an_info_line_still_counts_for_a_few_kinds():
    """tar meldet "Permission denied" als INFO, die Fehlerzeile sagt dann nur "exit code 2"."""
    explain = _explain()
    log = _log(
        "INFO: tar: ./var/lib/private: Cannot open: Permission denied",
        "ERROR: Backup of VM 100 failed - command 'set -o pipefail && tar ...' failed: exit code 2",
    )
    result = explain("100", "job errors", log)
    assert result["key"] == "permission"
    assert "Permission denied" in result["detail"]


def test_without_a_log_the_task_status_is_used_and_long_lines_are_cut():
    explain = _explain()
    assert explain("100", "unexpected status", None)["key"] == "aborted"
    assert explain("100", "job errors", None) | {"what": "", "todo": ""} == {
        "key": "unknown", "what": "", "todo": "", "detail": "job errors",
    }
    long_line = "ERROR: Backup of VM 100 failed - " + "x" * 1000
    assert len(explain("100", "job errors", [long_line])["detail"]) == 300


@pytest.mark.asyncio
async def test_failure_endpoint_explains_the_newest_failed_run_and_a_chosen_one(
    client, db_session, test_settings, mock_backup_api,  # noqa: F811 -- Fixture
):
    base_url, state = mock_backup_api
    token = await _setup_backups(client, db_session, test_settings, base_url)
    headers = _auth_header(token)
    state["tasks"] = [
        {"upid": UPID_100, "node": "pve2", "type": "vzdump", "id": "100", "status": "job errors", "starttime": 1000, "endtime": 1300},
        {"upid": "UPID:pve2:2", "node": "pve2", "type": "vzdump", "id": "102", "status": "OK", "starttime": 2000, "endtime": 2300},
    ]
    state["logs"] = {UPID_100: _log("ERROR: Backup of VM 100 failed - vma_queue_write: write error - No space left on device")}
    job_ref = next(j["job_ref"] for j in (await client.get("/api/v1/ext/backups/jobs", headers=headers)).json() if j["vmid"] == "100")

    r = await client.get(f"/api/v1/ext/backups/jobs/{job_ref}/failure", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["key"], body["upid"], body["node"], body["log_readable"]) == ("no_space", UPID_100, "pve2", True)
    assert body["detail"].endswith("No space left on device")
    assert state["log_reads"] == [("pve2", UPID_100, "5000")]

    again = await client.get(f"/api/v1/ext/backups/jobs/{job_ref}/failure?upid={UPID_100}", headers=headers)
    assert again.json()["key"] == "no_space"
    assert len(state["log_reads"]) == 1, "ein beendeter Lauf aendert sein Protokoll nicht: aus dem Speicher"

    other = next(j["job_ref"] for j in (await client.get("/api/v1/ext/backups/jobs", headers=headers)).json() if j["vmid"] == "102")
    assert (await client.get(f"/api/v1/ext/backups/jobs/{other}/failure", headers=headers)).status_code == 404
    assert (await client.get(f"/api/v1/ext/backups/jobs/{job_ref}/failure?upid=UPID:pve2:2", headers=headers)).status_code == 404
    assert (await client.get("/api/v1/ext/backups/jobs/kaputt/failure", headers=headers)).status_code == 404
    assert (await client.get(f"/api/v1/ext/backups/jobs/{job_ref}/failure")).status_code == 401


@pytest.mark.asyncio
async def test_without_sys_audit_the_status_alone_is_explained(client, db_session, test_settings, mock_backup_api):  # noqa: F811 -- Fixture
    base_url, state = mock_backup_api
    token = await _setup_backups(client, db_session, test_settings, base_url)
    headers = _auth_header(token)
    state["tasks"][0]["upid"] = UPID_100  # Status im Mock: "vma_queue_write: write error - Broken pipe"
    state["log_status"] = 403
    job_ref = next(j["job_ref"] for j in (await client.get("/api/v1/ext/backups/jobs", headers=headers)).json() if j["vmid"] == "100")
    body = (await client.get(f"/api/v1/ext/backups/jobs/{job_ref}/failure", headers=headers)).json()
    assert (body["key"], body["log_readable"]) == ("target_lost", False)


@pytest.mark.asyncio
async def test_watch_puts_the_cause_into_the_alert_but_never_the_raw_line(client, db_session, test_settings, mock_backup_api):  # noqa: F811 -- Fixture
    base_url, state = mock_backup_api
    await _setup_backups(client, db_session, test_settings, base_url)
    state["tasks"][0].update({"upid": UPID_100, "status": "job errors"})
    state["logs"] = {UPID_100: _log(
        "ERROR: Backup of VM 100 failed - unable to activate storage 'nas' - directory '/mnt/pve/nas' does not exist or is unreachable"
    )}
    _make_recent(state)

    assert (await _run_backup_watch())["notified"] == 1
    title = (await _backup_notifications(db_session))[0][1]
    body = await _body_of(db_session, title)
    assert body.startswith("Ursache: Das Sicherungsziel war beim Start nicht erreichbar")
    assert "Was tun: In Proxmox unter Rechenzentrum" in body
    assert "/mnt/pve" not in body, "die Zeile von Proxmox (Pfade, Adressen) geht nicht mit dem Push nach aussen"

    reads = len(state["log_reads"])
    assert (await _run_backup_watch())["notified"] == 0
    assert len(state["log_reads"]) == reads, "keine erneute Meldung, also kein erneutes Lesen"


@pytest.mark.asyncio
async def test_watch_still_alerts_when_the_cause_is_unreadable(client, db_session, test_settings, mock_backup_api):  # noqa: F811 -- Fixture
    base_url, state = mock_backup_api
    await _setup_backups(client, db_session, test_settings, base_url)
    state["tasks"][0].update({"upid": UPID_100, "status": "job errors"})
    state["log_status"] = 500
    _make_recent(state)
    assert (await _run_backup_watch())["notified"] == 1
    title = (await _backup_notifications(db_session))[0][1]
    assert "Ursache" not in await _body_of(db_session, title)


@pytest.mark.asyncio
async def test_a_run_with_warnings_counts_as_successful(client, db_session, test_settings, mock_backup_api):  # noqa: F811 -- Fixture
    """Proxmox zeigt "WARNINGS: n" gelb, die Sicherung ist da. Vorher meldete der Waechter "Backup fehlgeschlagen"."""
    base_url, state = mock_backup_api
    token = await _setup_backups(client, db_session, test_settings, base_url)
    headers = _auth_header(token)
    state["tasks"][0].update({"status": "WARNINGS: 2"})
    _make_recent(state)

    jobs = {j["vmid"]: j for j in (await client.get("/api/v1/ext/backups/jobs", headers=headers)).json()}
    assert jobs["100"]["last_status"] == "ok"
    assert jobs["100"]["last_ok_at"] == _FRESH + 3280
    history = (await client.get(f"/api/v1/ext/backups/jobs/{jobs['100']['job_ref']}/history", headers=headers)).json()
    assert (history[0]["status"], history[0]["warnings"]) == ("ok", 2)
    assert (await _run_backup_watch())["notified"] == 0
    assert await _backup_notifications(db_session) == []


def test_a_guest_without_its_own_section_never_gets_a_neighbours_error():
    """Uebersprungener Gast oder abgeschnittenes Protokoll: nur der Kopf vor dem ersten Gast zaehlt."""
    explain = _explain()
    log = [
        "INFO: starting new backup job: vzdump 100 102 --storage nas",
        "INFO: Starting Backup of VM 100 (qemu)",
        "ERROR: Backup of VM 100 failed - vma_queue_write: write error - No space left on device",
        "TASK ERROR: job errors",
    ]
    result = explain("102", "job errors", log)
    assert result["key"] == "unknown" and "No space" not in (result["detail"] or "")
    head_error = ["ERROR: could not activate storage 'nas': mount error", "INFO: Starting Backup of VM 100 (qemu)"]
    assert explain("102", "job errors", head_error)["key"] == "storage_offline"


def test_a_guest_that_was_backed_up_while_another_failed_is_named_as_such():
    explain = _explain()
    log = [
        "INFO: Starting Backup of VM 100 (qemu)",
        "ERROR: Backup of VM 100 failed - interrupted by signal",
        "INFO: Starting Backup of VM 102 (qemu)",
        "INFO: Finished Backup of VM 102 (00:01:12)",
        "INFO: Backup job finished with errors",
        "TASK ERROR: job errors",
    ]
    result = explain("102", "job errors", log)
    assert (result["key"], result["detail"]) == ("other_guest", "job errors")
    assert explain("100", "job errors", log)["key"] == "aborted"


def test_only_the_needed_lines_are_kept():
    sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "backups" / "src"))
    from nodvard_deck_ext_backups.failure_text import relevant_lines

    log = [*_log("ERROR: Backup of VM 100 failed - Broken pipe"), "INFO:  42% (1.0 GiB of 2.4 GiB)", "INFO: tar: x: Permission denied"]
    kept = relevant_lines(log)
    assert "INFO:  42% (1.0 GiB of 2.4 GiB)" not in kept
    assert {"INFO: Starting Backup of VM 100 (qemu)", "ERROR: Backup of VM 100 failed - Broken pipe", "INFO: tar: x: Permission denied"} <= set(kept)
