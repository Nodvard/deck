"""Tests für Dock-FAQ, Hilfeabschnitt der Update-Zentrale und Changelog-Eintrag."""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_dock_faq_german_exists_and_valid():
    faq_path = REPO_ROOT / "docs" / "dock" / "FAQ.md"
    assert faq_path.is_file(), "docs/dock/FAQ.md muss existieren"

    text = faq_path.read_text(encoding="utf-8")
    assert "\u2013" not in text, "Keine Gedankenstriche (en-dash) im Text"
    assert "\u2014" not in text, "Keine Gedankenstriche (em-dash) im Text"
    assert "open source" not in text.lower(), "Keine Erwähnung von open source"
    assert "freie software" not in text.lower(), "Keine Erwähnung von freier Software"
    assert "foss" not in text.lower(), "Keine Erwähnung von FOSS"
    assert "PolyForm Noncommercial" in text, "Lizenzangabe erforderlich"

    # Alle 12 Fragen müssen vorhanden sein
    for i in range(1, 13):
        assert f"## {i}." in text, f"FAQ-Eintrag {i} fehlt"

    # Belegte Grenzwerte und Aussagen
    assert "63 Zeichen" in text
    assert "bridge, host, none oder default" in text
    assert "Nur intern" in text
    assert "Compose-Stack" in text
    assert "Sicherheitsabfrage" in text
    assert "Nodvard Deck selbst, bleibt verbunden" in text or "bleibt zum Schutz der Verbindung" in text
    assert "Laufende Container ändern sich nicht" in text or "laufen unterbrechungsfrei weiter" in text
    assert "nginx:1.27" in text
    assert "Gestoppte Dienste" in text or "gestoppte Dienste" in text
    assert "Images holen" in text and "Anwenden" in text
    assert "keine .env" in text or "niemals aber eine zugehörige .env-Datei" in text
    assert "${NAME:?}" in text
    assert "$$NAME" in text
    assert "Katalog" in text
    assert "HTTPS" in text
    assert "SSH wird nicht unterstützt" in text or "Git über SSH wird nicht unterstützt" in text
    assert "300 Dateien" in text and "10 MiB" in text and "256 KiB" in text


def test_dock_faq_english_exists_and_valid():
    faq_path = REPO_ROOT / "docs" / "en" / "dock" / "FAQ.md"
    assert faq_path.is_file(), "docs/en/dock/FAQ.md muss existieren"

    text = faq_path.read_text(encoding="utf-8")
    assert "open source" not in text.lower()
    assert "free software" not in text.lower()
    assert "foss" not in text.lower()
    assert "PolyForm Noncommercial" in text

    for i in range(1, 13):
        assert f"## {i}." in text, f"Englischer FAQ-Eintrag {i} fehlt"

    assert "63 characters" in text
    assert "bridge, host, none, or default" in text
    assert "internal only" in text
    assert "Image holen" in text
    assert "nginx:1.27" in text
    assert "Images holen" in text and "Anwenden" in text
    assert "300 files" in text and "10 MiB" in text and "256 KiB" in text


def test_update_help_section_exists_and_valid():
    doc_path = REPO_ROOT / "docs" / "11-ERST-EINRICHTUNG.md"
    assert doc_path.is_file(), "docs/11-ERST-EINRICHTUNG.md muss existieren"

    text = doc_path.read_text(encoding="utf-8")
    assert "was-die-update-zentrale-meldet-und-was-es-heisst" in text
    match = re.search(r"### I\.4 Was die Update-Zentrale meldet.*?(?=## J\.)", text, re.DOTALL)
    assert match is not None, "Abschnitt I.4 muss vorhanden sein"

    section = match.group(0)
    assert "\u2013" not in section, "Keine Gedankenstriche (en-dash) im neuen Abschnitt"
    assert "\u2014" not in section, "Keine Gedankenstriche (em-dash) im neuen Abschnitt"

    # Belegte Fehlerzustände und Handlungsschritte
    assert "keine gefunden" in section
    assert "Es gibt noch keine fertige Version zum Herunterladen." in section
    assert "latest_digest" in section or "Prüfsumme" in section
    assert "Freigabe" in section
    assert "security_notice_failed" in section or "notify_security" in section
    assert "Shield" in section
    assert "dpkg returned an error code" in section
