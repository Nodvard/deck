import json

import contract_lib as cl
import pytest


@pytest.mark.parametrize("ext_id", ["shield", "nexus-soc"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_fail2ban_routen_sind_im_schnappschuss(method, ext_id):
    endpoints = json.loads(cl.API_SNAPSHOT.read_text(encoding="utf-8"))["endpoints"]
    assert f"{method} /api/v1/ext/{ext_id}/defender/hosts/{{host_id}}/fail2ban" in endpoints


def test_fail2ban_alias_beschreibt_geschlossene_anfragen():
    endpoints = json.loads(cl.API_SNAPSHOT.read_text(encoding="utf-8"))["endpoints"]
    key = "POST /api/v1/ext/nexus-soc/defender/hosts/{host_id}/fail2ban"
    assert endpoints[key]["body"]["schema"].get("additional_properties") is False, key
