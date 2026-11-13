from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_pihole_resume_expects_pause_controls():
    text = (ROOT / "docs/abnahme/ABNAHME-PIHOLE.md").read_text(encoding="utf-8")
    step = text.split("### 5. Manuelles Fortsetzen der Blockierung\n", 1)[1].split("\n### ", 1)[0]
    assert "`Blockierung fortsetzen`" in step
    assert "`Blockierung aktiv`" in step
    assert "`Blockierung pausieren:`" in step
    assert "`Werbung und Tracker werden im ganzen Netz blockiert.`" not in step
