"""Lynis auf Deutsch (lynis_text.py): feste Texte je Lynis-Kennung, im Ergebnis des Audits als `help`, und die Angabe im
dritten Feld des Berichts bleibt erhalten (sonst standen mehrere SSH-Hinweise wortgleich da)."""

from __future__ import annotations

import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "shield" / "src"))

from nodvard_deck_ext_shield import antivirus as av
from nodvard_deck_ext_shield.defender import audit_out
from nodvard_deck_ext_shield.lynis_text import (
    LYNIS_TEXTS,
    LYNIS_WEIGHT,
    lynis_help,
    lynis_id,
)
from nodvard_deck_ext_shield.models import AuditRecord


def test_every_text_has_a_title_and_both_sentences_and_a_real_lynis_id():
    assert len(LYNIS_TEXTS) >= 20
    for key, text in LYNIS_TEXTS.items():
        assert re.fullmatch(r"[A-Z]{2,5}-\d{4}|LYNIS", key), key
        assert set(text) == {"title", "what", "todo"}, key
        assert all(value.strip() for value in text.values()), key
        assert not re.search(r"\b(192\.168|10\.\d+\.\d+|nico|benks)", " ".join(text.values()), re.IGNORECASE), key


def test_ids_are_read_from_the_stored_findings():
    assert lynis_id("AUTH-9286: Configure maximum password age") == "AUTH-9286"
    assert lynis_id("LYNIS: Version of Lynis is very old") == "LYNIS"
    assert lynis_id("irgendwas ohne Kennung") is None
    help_ = lynis_help(["SSH-7408: Consider hardening SSH configuration (MaxAuthTries (set 6 to 3))", "ABCD-0000: unbekannt", "kaputt"])
    assert list(help_) == ["SSH-7408"]
    assert "Nodvard Deck meldet sich selbst per SSH an" in help_["SSH-7408"]["todo"]
    assert "MaxSessions nicht senken" in help_["SSH-7408"]["todo"]
    assert "kernel.modules_disabled" in LYNIS_TEXTS["KRNL-6000"]["todo"]
    assert "Port 22" in LYNIS_TEXTS["FIRE-4512"]["todo"]


def test_the_detail_of_a_finding_is_kept():
    res = av.parse_lynis(
        "suggestion[]=SSH-7408|Consider hardening SSH configuration|MaxAuthTries (set 6 to 3)|-|\n"
        "suggestion[]=SSH-7408|Consider hardening SSH configuration|X11Forwarding (set YES to NO)|-|\n"
        "suggestion[]=AUTH-9286|Configure maximum password age|-|-|\n"
        "suggestion[]=SSH-7408|Consider hardening SSH configuration|PermitRootLogin (set YES to (FORCED-COMMANDS-ONLY|NO|PROHIBIT-PASSWORD|WITHOUT-PASSWORD))|-|\n"
        "suggestion[]=FILE-7524|Check file permissions|" + "x" * 300 + "|-|\n"
        "suggestion[]=KRNL-6000|One or more sysctl values differ from the scan profile and could be tweaked||Change sysctl value|\n"
        "hardening_index=68\n@@done"
    )
    assert res.suggestions == [
        "SSH-7408: Consider hardening SSH configuration (MaxAuthTries (set 6 to 3))",
        "SSH-7408: Consider hardening SSH configuration (X11Forwarding (set YES to NO))",
        "AUTH-9286: Configure maximum password age",
        "SSH-7408: Consider hardening SSH configuration (PermitRootLogin (set YES to (FORCED-COMMANDS-ONLY|NO|PROHIBIT-PASSWORD|WITHOUT-PASSWORD)))",
        "FILE-7524: Check file permissions (" + "x" * 199 + "…)",
        "KRNL-6000: One or more sysctl values differ from the scan profile and could be tweaked",
    ]


def test_the_audit_result_carries_the_german_texts_for_its_findings():
    record = AuditRecord(
        id="a1", host_id="h1", host_name="docker-vm", status="ok", hardening_index=64,
        warnings=["PKGS-7392: Found one or more vulnerable packages"],
        suggestions=["KRNL-6000: One or more sysctl values differ", "ZZZZ-1234: unbekannt"],
        error=None, created_at=datetime(2026, 10, 8, tzinfo=UTC),
    )
    out = audit_out(record)
    assert set(out["help"]) == {"PKGS-7392", "KRNL-6000"}
    assert "Docker und Proxmox" in out["help"]["KRNL-6000"]["todo"]
    assert out["warnings"] == record.warnings and out["suggestions"] == record.suggestions


def test_weights_only_name_known_ids_and_reach_the_help():
    assert set(LYNIS_WEIGHT) <= set(LYNIS_TEXTS)
    assert set(LYNIS_WEIGHT.values()) <= {2, 3}
    help_ = lynis_help(["PKGS-7392: Found one or more vulnerable packages", "BANN-7126: Add a legal banner to /etc/issue"])
    assert (help_["PKGS-7392"]["weight"], help_["BANN-7126"]["weight"]) == (3, 1)
