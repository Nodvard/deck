from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dns_fallback_gets_tailscale_ip_on_target_server():
    text = (ROOT / "docs/13-TAILSCALE.md").read_text(encoding="utf-8")
    paragraph = next(
        paragraph for paragraph in text.split("\n\n")
        if paragraph.startswith("Scheitert nur die Namensauflösung")
    )
    assert "`tailscale ip -4` **auf dem Zielserver**" in " ".join(paragraph.split())
