import time

import pytest

from nodvard_deck.core import ssh_retry
from test_host_check_api import _auth, _bootstrap_owner, _make_host, _role_token


@pytest.mark.asyncio
async def test_retry_status_and_reset_are_per_host(client, db_session):
    token = await _bootstrap_owner(client)
    first = await _make_host(client, token)
    second = await _make_host(client, token, name="second")
    state = ssh_retry.RetryState(3, time.time() + 1800)
    for host in (first, second):
        await ssh_retry.save(host["id"], state)
    url = f"/api/v1/hosts/{first['id']}/ssh-retry"
    response = await client.get(url, headers=_auth(token))
    assert response.status_code == 200
    assert response.json() == {"failures": 3, "retry_at": state.retry_at}
    assert (await client.post(url, headers=_auth(token))).status_code == 204
    assert (await client.get(url, headers=_auth(token))).json() == {"failures": 0, "retry_at": 0}
    other = await client.get(f"/api/v1/hosts/{second['id']}/ssh-retry", headers=_auth(token))
    assert other.json()["failures"] == 3
    for method in (client.get, client.post):
        assert (await method("/api/v1/hosts/missing/ssh-retry", headers=_auth(token))).status_code == 404


@pytest.mark.asyncio
async def test_reset_requires_hosts_write(client, db_session):
    owner = await _bootstrap_owner(client)
    host = await _make_host(client, owner)
    viewer = await _role_token(client, db_session, "viewer", "viewer")
    await ssh_retry.save(host["id"], ssh_retry.RetryState(3, time.time() + 1800))
    url = f"/api/v1/hosts/{host['id']}/ssh-retry"
    assert (await client.post(url)).status_code == 401
    assert (await client.post(url, headers=_auth(viewer))).status_code == 403
    response = await client.get(url, headers=_auth(viewer))
    assert response.status_code == 200 and response.json()["failures"] == 3
