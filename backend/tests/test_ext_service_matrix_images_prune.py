"""Image-Update-Stand: Hosts, die keine Docker-Hosts mehr sind, fallen aus dem Stand."""

from __future__ import annotations

import pytest
from test_ext_service_matrix_images import FakeDocker, dg, make


def _two_hosts(tmp_path):
    h1, h2 = FakeDocker(), FakeDocker()
    h1.add("web", "nginx:1.27", image_id=dg("1"), repo_digests=["nginx@" + dg("a")], remote=dg("a"))
    h2.add("db", "postgres:16", image_id=dg("2"), repo_digests=["postgres@" + dg("c")], remote=dg("d"))
    return make(tmp_path, {"h1": h1, "h2": h2})


@pytest.mark.asyncio
async def test_removed_docker_host_disappears_from_the_image_state(tmp_path):
    service, ctx = _two_hosts(tmp_path)
    await service.check_hosts(ctx.host_objects)
    ctx.host_objects.pop()  # h2 ist kein Docker-Host mehr
    hosts = await service.docker_hosts()
    assert [h.id for h in hosts] == ["h1"]
    await service.check_hosts(hosts)
    await service.scheduled_check()
    snap = service.snapshot()
    assert "h2" not in snap["hosts"], snap["hosts"]
    assert "h2:db" not in snap["data"], sorted(snap["data"])


@pytest.mark.asyncio
async def test_listing_docker_hosts_is_enough_to_drop_a_removed_host(tmp_path):
    """Auch ohne neue Pruefung (Seite laedt nur den Stand): sobald die Host-Liste bekannt ist."""
    service, ctx = _two_hosts(tmp_path)
    await service.check_hosts(ctx.host_objects)
    ctx.host_objects.pop()
    await service.docker_hosts()
    snap = service.snapshot()
    assert sorted(snap["hosts"]) == ["h1"]
    assert sorted(snap["data"]) == ["h1:web"]


@pytest.mark.asyncio
async def test_host_that_did_not_answer_keeps_its_old_state(tmp_path):
    service, ctx = _two_hosts(tmp_path)
    await service.check_hosts(ctx.host_objects)
    ctx.docker["h2"].down = ConnectionError("no route to host")
    hosts = await service.docker_hosts()  # h2 ist weiter ein Docker-Host
    await service.check_hosts(hosts, force=True)
    snap = service.snapshot()
    assert sorted(snap["hosts"]) == ["h1", "h2"]
    assert snap["hosts"]["h2"]["error"]
    assert "h2:db" in snap["data"]
