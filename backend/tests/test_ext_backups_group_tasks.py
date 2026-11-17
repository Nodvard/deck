"""Ergebnisse je Gast aus dem Protokoll eines VZDump-Sammellaufs."""

from __future__ import annotations

import pytest
from test_ext_backups import (  # noqa: F401
    _FRESH,
    _auth_header,
    _backup_notifications,
    _cleanup_sys_path,
    _make_recent,
    _run_backup_watch,
    _setup_backups,
    mock_backup_api,
)

UPID = "UPID:pve2:0001A2B4:00C0FFEF:6703F1A1:vzdump::root@pam:"
OLDER = "UPID:pve2:0001A2B3:00C0FFEE:6703F1A0:vzdump::root@pam:"


def _group_job(state, selection):
    state["jobs"] = [{
        "id": "nightly", "node": "pve2", "storage": "backup-pve1",
        "schedule": "daily", "enabled": 1, **selection,
    }]
    state["resources"] = [
        {"vmid": 100, "node": "pve2", "type": "qemu", "pool": "daily"},
        {"vmid": 102, "node": "pve2", "type": "lxc", "pool": "daily"},
        {"vmid": 103, "node": "pve2", "type": "qemu", "pool": "daily"},
    ]
    state["tasks"] = [
        {"upid": UPID, "node": "pve2", "type": "vzdump", "id": "", "user": "root@pam",
         "status": "job errors", "starttime": 5000, "endtime": 5600},
        {"upid": OLDER, "node": "pve2", "type": "vzdump", "id": "", "user": "root@pam",
         "status": "OK", "starttime": 1000, "endtime": 1600},
    ]
    state["logs"] = {UPID: [
        "INFO: starting new backup job: vzdump 100 102 103 --storage backup-pve1 --mode snapshot",
        "INFO: Starting Backup of VM 100 (qemu)",
        "INFO: Backup started at 2026-11-14 02:00:00",
        "WARN: guest-agent is not running",
        "INFO: Finished Backup of VM 100 (00:03:20)",
        "INFO: Starting Backup of VM 102 (lxc)",
        "ERROR: Backup of VM 102 failed - unable to acquire lock '/run/lock/lxc/pve-config-102.lock' - got timeout",
        "INFO: Starting Backup of VM 103 (qemu)",
        "ERROR: interrupted by signal",
        "TASK ERROR: job errors",
    ]}
    _make_recent(state)


async def _jobs(client, headers):
    response = await client.get("/api/v1/ext/backups/jobs", headers=headers)
    assert response.status_code == 200, response.text
    return {row["vmid"]: row for row in response.json()}


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", [{"vmid": "100,102,103"}, {"all": 1}, {"pool": "daily"}])
async def test_group_failure_belongs_only_to_the_failed_guest(
    client, db_session, test_settings, mock_backup_api, selection,
):
    base_url, state = mock_backup_api
    _group_job(state, selection)
    token = await _setup_backups(client, db_session, test_settings, base_url)
    headers = _auth_header(token)

    jobs = await _jobs(client, headers)
    assert {vmid: job["last_status"] for vmid, job in jobs.items()} == {
        "100": "ok", "102": "failed", "103": "job_error",
    }
    assert jobs["100"]["last_ok_at"] == _FRESH + 5600
    assert jobs["102"]["last_ok_at"] == jobs["103"]["last_ok_at"] == _FRESH + 1600
    assert state["log_reads"] == [("pve2", UPID, "5000")]

    for vmid, status in [("100", "ok"), ("102", "failed"), ("103", "job_error")]:
        ref = jobs[vmid]["job_ref"]
        history = await client.get(f"/api/v1/ext/backups/jobs/{ref}/history", headers=headers)
        assert history.status_code == 200, history.text
        assert [run["status"] for run in history.json()] == [status, "ok"]
        cause = await client.get(f"/api/v1/ext/backups/jobs/{ref}/failure?upid={UPID}", headers=headers)
        if vmid == "102":
            assert cause.status_code == 200, cause.text
            assert cause.json()["key"] == "locked"
        else:
            assert cause.status_code == 404, cause.text

    assert (await _run_backup_watch())["notified"] == 1
    assert await _backup_notifications(db_session) == [("critical", "Backup fehlgeschlagen: VM 102")]
    assert (await _run_backup_watch())["notified"] == 0
    assert len(state["log_reads"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("log_status, lines", [
    (403, []),
    (500, []),
    (None, []),
    (None, ["ERROR: could not activate storage 'nas': mount error", "TASK ERROR: job errors"]),
    (None, ["INFO: Starting Backup of VM 100 (qemu)", "ERROR: write error - Broken pipe"]),
])
async def test_group_error_without_guest_result_is_unclear(
    client, db_session, test_settings, mock_backup_api, log_status, lines,
):
    base_url, state = mock_backup_api
    _group_job(state, {"vmid": "100,102,103"})
    state["logs"][UPID] = lines
    state["log_status"] = log_status
    token = await _setup_backups(client, db_session, test_settings, base_url)
    headers = _auth_header(token)

    jobs = await _jobs(client, headers)
    assert {job["last_status"] for job in jobs.values()} == {"job_error"}
    assert {job["last_ok_at"] for job in jobs.values()} == {_FRESH + 1600}
    assert len(state["log_reads"]) == 1
    assert (await _run_backup_watch())["notified"] == 0
    assert await _backup_notifications(db_session) == []
    ref = jobs["100"]["job_ref"]
    history = await client.get(f"/api/v1/ext/backups/jobs/{ref}/history", headers=headers)
    assert history.json()[0]["status"] == "job_error"

    widget = (await client.get("/api/v1/ext/backups/widgets/summary", headers=headers)).json()["data"]
    assert {(row["last_status_label"], row["tone"]) for row in widget} == {
        ("Job mit Fehlern, Gast unklar", "warn"),
    }
    assert len(state["log_reads"]) == 1


@pytest.mark.asyncio
async def test_group_logs_stop_after_the_first_success(
    client, db_session, test_settings, mock_backup_api,
):
    base_url, state = mock_backup_api
    _group_job(state, {"vmid": "100,102,103"})
    state["tasks"].append({
        "upid": "UPID:pve2:0001A2B2:00C0FFED:6703F19F:vzdump::root@pam:",
        "node": "pve2", "type": "vzdump", "id": "", "status": "job errors",
        "starttime": 500, "endtime": 900,
    })
    token = await _setup_backups(client, db_session, test_settings, base_url)

    await _jobs(client, _auth_header(token))

    assert state["log_reads"] == [("pve2", UPID, "5000")]


@pytest.mark.asyncio
async def test_group_log_failure_before_guest_start_has_an_explicit_vmid(
    client, db_session, test_settings, mock_backup_api,
):
    base_url, state = mock_backup_api
    _group_job(state, {"vmid": "100,102,103"})
    state["logs"][UPID] = [
        "ERROR: Backup of VM 100 failed - Configuration file 'nodes/pve2/qemu-server/100.conf' does not exist",
        "INFO: Starting Backup of VM 102 (lxc)",
        "INFO: Finished Backup of VM 102 (00:00:20)",
        "TASK ERROR: job errors",
    ]
    token = await _setup_backups(client, db_session, test_settings, base_url)
    jobs = await _jobs(client, _auth_header(token))
    assert {vmid: job["last_status"] for vmid, job in jobs.items()} == {
        "100": "failed", "102": "ok", "103": "job_error",
    }


@pytest.mark.asyncio
async def test_group_log_is_not_needed_for_running_or_successful_tasks(
    client, db_session, test_settings, mock_backup_api,
):
    base_url, state = mock_backup_api
    _group_job(state, {"vmid": "100,102,103"})
    state["tasks"][0].pop("endtime")
    token = await _setup_backups(client, db_session, test_settings, base_url)
    headers = _auth_header(token)
    assert {job["last_status"] for job in (await _jobs(client, headers)).values()} == {"running"}
    state["tasks"][0].update({"status": "WARNINGS: 1", "endtime": _FRESH + 5600})
    assert {job["last_status"] for job in (await _jobs(client, headers)).values()} == {"ok"}
    assert state.get("log_reads", []) == []
    state["tasks"][0]["status"] = "job errors"
    jobs = await _jobs(client, headers)
    assert jobs["100"]["last_status"] == "ok"
    assert jobs["102"]["last_status"] == "failed"
    assert len(state["log_reads"]) == 1
