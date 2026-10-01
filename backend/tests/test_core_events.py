"""In-Process-Event-Bus mit Pattern-Abo (docs/02-EXTENSION-API.md
`ctx.events`).
"""

from __future__ import annotations

import pytest
from nodvard_sdk.types import Event

from nodvard_deck.core.events import EventBus


@pytest.mark.asyncio
async def test_exact_pattern_match_delivers_event():
    bus = EventBus()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    bus.subscribe("host.down", handler)
    await bus.publish(Event(name="host.down"))
    await bus.publish(Event(name="host.up"))

    assert [e.name for e in received] == ["host.down"]


@pytest.mark.asyncio
async def test_wildcard_suffix_pattern_matches_prefix():
    bus = EventBus()
    received: list[str] = []

    async def handler(event: Event) -> None:
        received.append(event.name)

    bus.subscribe("action.*", handler)
    await bus.publish(Event(name="action.executed"))
    await bus.publish(Event(name="action.denied"))
    await bus.publish(Event(name="host.down"))

    assert received == ["action.executed", "action.denied"]


@pytest.mark.asyncio
async def test_multiple_handlers_all_receive_matching_event():
    bus = EventBus()
    calls: list[str] = []

    async def handler_a(event: Event) -> None:
        calls.append(f"a:{event.name}")

    async def handler_b(event: Event) -> None:
        calls.append(f"b:{event.name}")

    bus.subscribe("*", handler_a)
    bus.subscribe("*", handler_b)
    await bus.publish(Event(name="anything"))

    assert set(calls) == {"a:anything", "b:anything"}


@pytest.mark.asyncio
async def test_failing_handler_does_not_break_other_handlers_or_publisher():
    bus = EventBus()
    calls: list[str] = []

    async def broken(event: Event) -> None:
        raise RuntimeError("kaputte Extension")

    async def healthy(event: Event) -> None:
        calls.append(event.name)

    bus.subscribe("*", broken)
    bus.subscribe("*", healthy)

    await bus.publish(Event(name="test.event"))  # darf NICHT raisen

    assert calls == ["test.event"]


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery():
    bus = EventBus()
    received: list[str] = []

    async def handler(event: Event) -> None:
        received.append(event.name)

    bus.subscribe("x.*", handler)
    await bus.publish(Event(name="x.one"))
    bus.unsubscribe("x.*", handler)
    await bus.publish(Event(name="x.two"))

    assert received == ["x.one"]


def test_unsubscribe_unknown_pair_is_a_noop():
    bus = EventBus()

    async def handler(event: Event) -> None:  # pragma: no cover - nie aufgerufen
        pass

    bus.unsubscribe("never.subscribed", handler)  # darf nicht raisen


@pytest.mark.asyncio
async def test_pattern_matching_is_case_sensitive_regardless_of_platform():
    """`fnmatchcase` statt `fnmatch` -- unter Windows (Entwicklung) wuerde `fnmatch`
    Gross-/Kleinschreibung ignorieren, unter Linux (Produktion) nicht. Regression gegen
    genau diesen Plattformunterschied."""
    bus = EventBus()
    received: list[str] = []

    async def handler(event: Event) -> None:
        received.append(event.name)

    bus.subscribe("Host.Down", handler)
    await bus.publish(Event(name="host.down"))

    assert received == []
