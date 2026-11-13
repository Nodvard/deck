from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("name, vault", [("18-SHIELD.md", "Tresor"), ("en/SHIELD.md", "vault")])
def test_provider_setup_documents_access_and_data_transfer(name, vault):
    text = (ROOT / "docs" / name).read_text(encoding="utf-8")
    for required in (
        "openai_compat",
        "Ollama",
        "OpenRouter",
        "vLLM",
        "llama.cpp",
        "http://127.0.0.1:8080/v1",
        "POST /v1/chat/completions",
        "GET /v1/models",
        "Verbindung testen",
        "Daten verlassen dein Netz",
    ):
        assert required in text, (name, required)
    assert vault in text
