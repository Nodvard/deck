"""`new_id()`: echte UUIDv7 mit 36 Zeichen, damit sie in jede `String(36)`-Spalte passt (auch unter PostgreSQL)."""

from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import String

import nodvard_deck.models  # noqa: F401  (alle Kern-Tabellen in Base.metadata)
from nodvard_deck.db.base import Base, new_id


def test_new_id_is_a_uuid7_with_36_characters():
    value = new_id()
    assert len(value) == 36
    parsed = uuid.UUID(value)
    assert str(parsed) == value
    assert parsed.version == 7
    assert parsed.variant == uuid.RFC_4122


def test_new_id_starts_with_the_current_milliseconds():
    before = time.time_ns() // 1_000_000
    value = new_id()
    after = time.time_ns() // 1_000_000
    assert before <= int(value.replace("-", "")[:12], 16) <= after


def test_new_ids_are_unique_and_sort_by_time():
    first = new_id()
    time.sleep(0.002)
    later = [new_id() for _ in range(1000)]
    assert len(set(later)) == 1000
    assert all(first < value for value in later)


@pytest.mark.parametrize("table", sorted(Base.metadata.tables.values(), key=lambda t: t.name), ids=lambda t: t.name)
def test_every_string_primary_key_fits_a_new_id(table):
    for column in table.primary_key.columns:
        if isinstance(column.type, String) and column.type.length is not None and column.name == "id":
            assert column.type.length >= len(new_id()), f"{table.name}.{column.name}"
