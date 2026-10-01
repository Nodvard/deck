"""Portainer-Ersatz: Container-Details, Docker-Speicher, Aufraeumen.

Die df-/images-Zeilen sind echte Ausgaben von Docker 29.8 auf einem Raspberry Pi,
nur die Image-Liste gekuerzt."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "service-matrix" / "src"))

from nodvard_deck_ext_service_matrix.docker_details import (  # noqa: E402
    parse_images,
    parse_system_df,
    summarize_inspect,
)

DF = """{"Active":"5","Reclaimable":"393.1MB (10%)","Size":"3.866GB","TotalCount":"7","Type":"Images"}
{"Active":"5","Reclaimable":"0B (0%)","Size":"131MB","TotalCount":"5","Type":"Containers"}
{"Active":"1","Reclaimable":"155.1MB (97%)","Size":"159MB","TotalCount":"4","Type":"Local Volumes"}
{"Active":"0","Reclaimable":"9.113GB","Size":"10.15GB","TotalCount":"388","Type":"Build Cache"}
"""

IMAGES = """{"Containers":"1","CreatedSince":"2 minutes ago","ID":"f6ded99b9c2f","Repository":"lattice","Size":"658MB","Tag":"latest"}
{"Containers":"0","CreatedSince":"7 days ago","ID":"87b0f9cd90fc","Repository":"lattice","Size":"379MB","Tag":"pi-test"}
{"Containers":"0","CreatedSince":"7 days ago","ID":"294b683cb724","Repository":"alpine","Size":"13.6MB","Tag":"latest"}
{"Containers":"0","CreatedSince":"3 weeks ago","ID":"0badc0ffee00","Repository":"<none>","Size":"1.2GB","Tag":"<none>"}
kaputte Zeile
"""


def test_system_df_shows_size_and_what_can_be_freed():
    rows = {r["type"]: r for r in parse_system_df(DF)}
    assert rows["Build Cache"] == {
        "type": "Build Cache", "label": "Build-Cache", "total": 388, "active": 0,
        "size": 10_150_000_000, "reclaimable": 9_113_000_000,
    }
    assert rows["Images"]["reclaimable"] == 393_100_000
    assert rows["Containers"]["reclaimable"] == 0
    assert rows["Local Volumes"]["label"] == "Volumes"


def test_images_unused_first_largest_first_and_old_docker_without_counts():
    images = parse_images(IMAGES)
    assert [(i["name"], i["in_use"]) for i in images] == [
        (None, False), ("lattice:pi-test", False), ("alpine:latest", False), ("lattice:latest", True),
    ]
    assert images[0]["dangling"] is True and images[0]["size"] == 1_200_000_000
    old = parse_images('{"Containers":"N/A","ID":"abc","Repository":"nginx","Size":"187MB","Tag":"1.27"}')
    assert old[0]["in_use"] is None


def test_inspect_is_a_whitelist_env_values_never_leave():
    raw = {
        "Name": "/nextcloud-db",
        "Image": "sha256:5f3a9c1b7d4e8f00112233445566778899aabbccddeeff",
        "Created": "2026-09-01T10:00:00.000Z",
        "RestartCount": 2,
        "State": {"Running": True, "StartedAt": "2026-09-24T07:00:00Z", "FinishedAt": "0001-01-01T00:00:00Z",
                  "ExitCode": 0, "OOMKilled": False, "Health": {"Status": "healthy"}},
        "Config": {
            "Image": "postgres:16",
            "Env": ["POSTGRES_PASSWORD=GEHEIM", "DATABASE_URL=postgres://u:GEHEIM@db/x", "TZ=Europe/Berlin", "PATH=/usr/bin"],
            "Cmd": ["postgres", "--password=GEHEIM"],
            "Labels": {"com.docker.compose.project": "nextcloud", "com.docker.compose.service": "db",
                       "com.docker.compose.project.working_dir": "/opt/nextcloud"},
        },
        "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}, "Privileged": False, "NetworkMode": "nextcloud_default", "Memory": 0},
        "NetworkSettings": {
            "Ports": {"5432/tcp": [{"HostIp": "0.0.0.0", "HostPort": "5432"}, {"HostIp": "::", "HostPort": "5432"}],
                      "9187/tcp": None},
            "Networks": {"nextcloud_default": {"IPAddress": "172.20.0.2"}},
        },
        "Mounts": [
            {"Type": "volume", "Name": "nextcloud_db", "Source": "/var/lib/docker/volumes/nextcloud_db/_data", "Destination": "/var/lib/postgresql/data", "RW": True},
            {"Type": "bind", "Source": "/etc/localtime", "Destination": "/etc/localtime", "RW": False},
        ],
    }
    out = summarize_inspect(raw)
    assert "GEHEIM" not in repr(out), "weder Env-Werte noch Befehlszeile duerfen die Extension verlassen"
    assert out["env_keys"] == ["DATABASE_URL", "PATH", "POSTGRES_PASSWORD", "TZ"]
    assert out["name"] == "nextcloud-db"
    assert out["image_id"] == "5f3a9c1b7d4e"
    assert (out["finished_at"], out["exit_code"], out["health"]) == (None, None, "healthy")
    assert (out["restart_policy"], out["restart_count"], out["memory_limit"]) == ("unless-stopped", 2, None)
    assert out["ports"] == [{"container": "5432/tcp", "published": ["5432"]}, {"container": "9187/tcp", "published": []}]
    assert out["mounts"] == [
        {"type": "volume", "source": "nextcloud_db", "destination": "/var/lib/postgresql/data", "read_only": False},
        {"type": "bind", "source": "/etc/localtime", "destination": "/etc/localtime", "read_only": True},
    ]
    assert out["networks"] == [{"name": "nextcloud_default", "ip": "172.20.0.2"}]
    assert out["compose"] == {"project": "nextcloud", "service": "db", "working_dir": "/opt/nextcloud"}


def test_stopped_container_shows_exit_and_oom():
    out = summarize_inspect({
        "State": {"Running": False, "StartedAt": "2026-09-24T07:00:00Z", "FinishedAt": "2026-09-24T08:00:00Z", "ExitCode": 137, "OOMKilled": True},
        "Config": {}, "HostConfig": {}, "NetworkSettings": {},
    })
    assert (out["finished_at"], out["exit_code"], out["oom_killed"], out["compose"]) == ("2026-09-24T08:00:00Z", 137, True, None)
