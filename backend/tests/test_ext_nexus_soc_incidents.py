from __future__ import annotations

import sys
import time
from pathlib import Path

NEXUS_SOC_SRC = Path(__file__).resolve().parents[2] / "extensions" / "nexus-soc" / "src"
if str(NEXUS_SOC_SRC) not in sys.path:
    sys.path.insert(0, str(NEXUS_SOC_SRC))

from nodvard_deck_ext_nexus_soc.incidents import Incident, IncidentStore, new_incident_id  # noqa: E402


def _incident(host_name="docker", target="nginx") -> Incident:
    return Incident(id=new_incident_id(), host_id="h1", host_name=host_name, target=target, message="Absturz")


def test_enqueue_reports_first_event_of_a_batch():
    store = IncidentStore()
    assert store.enqueue(_incident()) is True
    assert store.enqueue(_incident(target="redis")) is False
    assert store.enqueue(_incident(target="postgres")) is False


def test_take_batch_drains_and_resets_first_event_flag():
    store = IncidentStore()
    store.enqueue(_incident())
    store.enqueue(_incident(target="redis"))
    batch = store.take_batch()
    assert len(batch) == 2
    assert store.has_pending() is False

    # Nach dem Leeren zaehlt das naechste Ereignis wieder als "erstes" -- Fund G:
    # der naechste Batch braucht einen eigenen neuen Timer.
    assert store.enqueue(_incident(target="mongo")) is True


def test_cooldown_blocks_the_same_host_target_pair_only():
    store = IncidentStore()
    store.enqueue(_incident(host_name="docker", target="nginx"))
    assert store.is_in_cooldown("docker", "nginx", cooldown_s=1800) is True
    assert store.is_in_cooldown("docker", "redis", cooldown_s=1800) is False
    assert store.is_in_cooldown("pi_host", "nginx", cooldown_s=1800) is False


def test_cooldown_expires_after_the_configured_window():
    store = IncidentStore()
    incident = _incident()
    store.enqueue(incident)
    store._cooldowns[("docker", "nginx")] = time.time() - 10  # simuliert Ablauf
    assert store.is_in_cooldown("docker", "nginx", cooldown_s=5) is False


def test_restore_puts_open_incidents_back_with_cooldown_and_reports_first_event():
    store = IncidentStore()
    old = _incident(target="nginx")
    old.created_at = time.time() - 120
    assert store.restore([old, _incident(target="redis")]) is True
    assert store.pending_count() == 2
    assert store.is_in_cooldown("docker", "nginx", cooldown_s=1800) is True
    assert store.oldest_created_at() == old.created_at
    # Nichts zum Wiederaufnehmen -> kein Timer noetig.
    assert IncidentStore().restore([]) is False
    assert IncidentStore().oldest_created_at() is None
