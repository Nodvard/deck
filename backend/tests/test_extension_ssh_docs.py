from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("path,heading,limits,exceptions", [
    (
        "docs/02-EXTENSION-API.md", "### SSH und Freigabe",
        ("keine Sicherheitsgrenze", "Ohne Host-Bezug gibt es keinen SSH-Zugriff", "erteilt keine SSH-Freigabe", "Pfad oder Query"),
        ("Quarantäne", "Lynis", "Rückweg", "automatischen Updates"),
    ),
    (
        "docs/en/EXTENSION-API.md", "### SSH and approval",
        ("not a security boundary", "without a host grants no SSH access", "grants no SSH access", "path or query"),
        ("quarantine", "Lynis", "rollback", "automatic updates"),
    ),
])
def test_ssh_contract_explains_contexts_and_limits(path, heading, limits, exceptions):
    document = (ROOT / path).read_text(encoding="utf-8")
    section = document.split(heading + "\n", 1)[1].split("\n### ", 1)[0]
    for term in (
        "ActionBlocked", "action_gate", "ctx.actions.propose()", "ActionExecutor.execute()",
        "GET", "HEAD", "POST", "host_id", "ssh_preflight=True", "ssh_read=True",
        "hosts.read", "hosts.execute", "ctx.scheduler.register_job()", "ctx.spawn",
        "read_access(host, timeout_s=30)", "0 < timeout_s <= 60", "open_shell", "sftp",
        *limits, *exceptions,
    ):
        assert term in section, term
    watcher = next(line for line in document.splitlines() if line.startswith(("| Docker-Watcher", "| Docker watcher")))
    assert "read_access(host)" in watcher
    remediation = next(line for line in document.splitlines() if line.startswith("| Remediation"))
    assert "ActionExecutor.execute()" in remediation


@pytest.mark.parametrize("path", ["scripts/release_grant.py", "deploy/README.md"])
def test_release_signing_names_the_project_owner(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    if path.endswith(".py"):
        assert "auf dem Rechner des Projektinhabers" in text
    else:
        assert "die der Projektinhaber mit seinem" in text
