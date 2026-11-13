import json

import contract_lib as cl
import pytest


@pytest.fixture
def snapshot():
    return json.loads(cl.API_SNAPSHOT.read_text(encoding="utf-8"))


def test_schnappschuss_beschreibt_antworttypen(snapshot):
    fehlende = [key for key, endpoint in snapshot["endpoints"].items() if "response_content_types" not in endpoint]
    assert not fehlende, "Fehlende Antworttypen: " + ", ".join(fehlende)


@pytest.mark.parametrize("key", [
    "PUT /api/v1/telemetry",
    "POST /api/v1/telemetry/preview",
    "POST /api/v1/ext/shield/defender/hosts/{host_id}/fail2ban",
])
def test_schnappschuss_beschreibt_geschlossene_anfragen(snapshot, key):
    schema = snapshot["endpoints"][key]["body"]["schema"]
    assert schema.get("additional_properties") is False, key
