"""Der Wächter `scripts/check_secret_lookalikes.py`: nichts im Repository sieht für einen Geheimnis-Scanner echt aus.

Die Beispiele entstehen zur Laufzeit aus Teilen, damit diese Datei selbst sauber bleibt.
"""

from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_secret_lookalikes.py"

_spec = importlib.util.spec_from_file_location("check_secret_lookalikes_script", SCRIPT)
guard = importlib.util.module_from_spec(_spec)
sys.modules["check_secret_lookalikes_script"] = guard
_spec.loader.exec_module(guard)

KIND = "OPENSSH " + "PRIVATE KEY"
KEY_BLOCK = f"-----BEGIN {KIND}-----\nAAAA\n-----END {KIND}-----"


def rules(text: str) -> list[str]:
    return [f.rule for f in guard.check_text("x.py", text)]


def test_the_repository_has_no_secret_lookalikes():
    findings = guard.find_violations()
    assert not findings, "\n" + "\n".join(f"  {f}" for f in findings)


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("Authorization: Bear" + "er abcdef0123456789abcdef0123", "Bearer-Wert"),
        ("authorization: bear" + "er abcdef0123456789", "Bearer-Wert"),
        ("Authorization: Bas" + "ic dXNlcjpwYXNzd29yZDEyMw==", "Basic-Anmeldung"),
        ("token = 'gh" + "p_" + "A1b2C3d4" * 5 + "'", "GitHub-Token"),
        ("github" + "_pat_" + "A1b2C3d4" * 4, "GitHub-Token"),
        ("glp" + "at-" + "a1b2c3d4" * 3, "GitLab-Token"),
        ("AKI" + "A" + "ABCDEFGH12345678", "AWS-Schlüssel"),
        ("xo" + "xb-" + "1234567890-abcdef", "Slack-Token"),
        ("https://discord" + ".com/api/webhooks/" + "1234/abcdef", "Discord-Webhook"),
        ("https://discord" + ".com/api/v10/webhooks/" + "1234/abcdef", "Discord-Webhook"),
        ("https://hooks.slack" + ".com/workflows/" + "T0123/A0456/789", "Slack-Token"),
        ("np" + "m_" + "a1B2c3D4e5" * 3 + "F6g7H8", "npm-Token"),
        ("s" + "k-" + "a1B2c3D4" * 3, "API-Schlüssel"),
        ("sk" + "_live_" + "a1B2c3D4e5", "API-Schlüssel"),
        ("AI" + "za" + "A" * 35, "Google-Schlüssel"),
        ("ey" + "JhbGciOi.ey" + "JzdWIiOi.c2lnbmF0dXJl", "JWT"),
        ("gAAA" + "AA" + "B" * 40, "Fernet-Token"),
        ("AGE-SECRET-" + "KEY-1" + "Q" * 58, "age-Schlüssel"),
        ("t" + "k_" + "a1b2c3d4" * 3, "ntfy-Token"),
        ("PVEAPI" + "Token=root@pam!x=" + "0123abcd-4567-89ab", "Proxmox-Token"),
        ("postgresql" + "://admin:" + "geheim@db/deck", "Adresse mit Passwort"),
        ("mongodb+srv" + "://u:" + "pw@cluster", "Adresse mit Passwort"),
        ("postgresql+asyncpg" + "://deck:" + "geheim@db/deck", "Adresse mit Passwort"),
        ("mysql+pymysql" + "://root:" + "geheim@db", "Adresse mit Passwort"),
        ("redis" + "://:" + "geheim@cache:6379", "Adresse mit Passwort"),
        ("mssql+pyodbc" + "://sa:" + "geheim@db/deck", "Adresse mit Passwort"),
        ("oracle+cx_oracle" + "://scott:" + "geheim@db", "Adresse mit Passwort"),
        ("https" + "://nutzer:" + "geheim@nas.example.org:5001", "Adresse mit Passwort"),
    ],
)
def test_forms_that_scanners_report_are_found(text, rule):
    assert rules(text) == [rule]


def test_a_complete_private_key_is_found_but_not_the_header_alone():
    assert rules(f'pasted = "{KEY_BLOCK}"') == ["privater Schlüssel"]
    assert rules(f'pasted = """\n{KEY_BLOCK}\n"""') == ["privater Schlüssel"]
    assert rules(f'placeholder="-----BEGIN {KIND}-----"') == []


@pytest.mark.parametrize(
    "text",
    [
        'headers={"Authorization": f"Bear' + 'er {token}"}',
        "Bear" + "er kurz-0000",
        "postgres" + "://db/deck",
        "postgres" + "://u@db/x",
        'kind = "OPENSSH " + "PRIVATE KEY"',
        '"-----BEGIN OPENSSH " + "PRIVATE KEY-----"',
        "Basic Authentication mit Benutzer und Passwort",
        "Basic Authentifizierung mit Benutzer und Passwort",
        "volume = 'disk-usage-" + "sk-" + "a1b2c3d4e5f6g7h8i9j0'",
        "Fuer Postgres: postgresql+asyncpg" + "://<benutzer>:<passwort>@host/db",
        "PVEAPI" + "Token=lattice@pve!dashboard=HIER-DAS-GEHEIMNIS",
        "Bearer Authentifizierung mit Benutzer-Token",
        "postgresql" + "://deck:${DB_PASSWORD}@db/deck",
        "https" + "://nutzer:{passwort}@nas.example.org",
        "mysql" + "://root:%(pw)s@db",
        "http" + "://User:@Host:8443/",
        "npm_config_cache_directory_for_the_tests",
    ],
)
def test_composed_and_harmless_text_passes(text):
    assert rules(text) == []


def test_a_folder_is_searched_without_git(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "leak.txt").write_text("x\n" + "AGE-SECRET-" + "KEY-1" + "Q" * 58 + "\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "leak.txt").write_text("redis" + "://:" + "geheim@cache\n", encoding="utf-8")
    assert guard.main([str(tmp_path)]) == 1
    findings = guard.find_violations(tmp_path, tracked=False)
    assert [(Path(f.path).as_posix(), f.line, f.rule) for f in findings] == [("sub/leak.txt", 2, "age-Schlüssel")]


def test_the_script_reports_findings_and_fails(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "leak.py").write_text("x = 'postgresql" + "://admin:" + "geheim@db/deck'\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "leak.py"], check=True)
    findings = guard.find_violations(tmp_path)
    assert [(f.path, f.line, f.rule) for f in findings] == [("leak.py", 1, "Adresse mit Passwort")]


def test_findings_with_special_characters_print_on_a_windows_console(tmp_path, monkeypatch):
    (tmp_path / "leak.md").write_text("Ablauf → redis" + "://:" + "geheim@cache\n", encoding="utf-8")
    console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", console)
    assert guard.main([str(tmp_path)]) == 1
    console.flush()
    assert b"leak.md:1: Adresse mit Passwort: Ablauf ? redis" in console.buffer.getvalue()
