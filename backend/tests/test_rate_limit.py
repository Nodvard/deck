"""core/rate_limit -- gleitendes Fenster je Schluessel, im Speicher."""

from __future__ import annotations

from nodvard_deck.core import rate_limit


def test_allows_up_to_the_limit_then_reports_seconds_to_wait(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(rate_limit, "_now", lambda: now[0])
    win = rate_limit.SlidingWindow(limit=3, window_s=300)
    assert [win.hit("u1") for _ in range(3)] == [0, 0, 0]
    assert win.hit("u1") == 300
    now[0] += 100
    assert win.hit("u1") == 200, "der Zaehler laeuft mit, der gesperrte Versuch zaehlt nicht mit"
    # Andere Schluessel sind unabhaengig.
    assert win.hit("u2") == 0
    now[0] += 200
    assert win.hit("u1") == 0, "der aelteste Treffer ist aus dem Fenster"
    assert [win.hit("u1") for _ in range(2)] == [0, 0]
    assert win.hit("u1") == 300


def test_rejected_attempts_do_not_extend_the_block(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(rate_limit, "_now", lambda: now[0])
    win = rate_limit.SlidingWindow(limit=1, window_s=60)
    assert win.hit("k") == 0
    for _ in range(50):
        now[0] += 1
        win.hit("k")
    now[0] = 60.5
    assert win.hit("k") == 0


def test_reset_all_clears_every_window():
    win = rate_limit.SlidingWindow(limit=1, window_s=60)
    assert win.hit("k") == 0 and win.hit("k") > 0
    rate_limit.reset_all()
    assert win.hit("k") == 0


def test_old_keys_are_swept(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(rate_limit, "_now", lambda: now[0])
    win = rate_limit.SlidingWindow(limit=5, window_s=10)
    for i in range(2000):
        win.hit(f"k{i}")
    now[0] = 100.0
    win.hit("neu")
    assert len(win._hits) == 1


def test_wait_message_uses_minutes():
    assert rate_limit.wait_text(1) == "1 Minute"
    assert rate_limit.wait_text(61) == "2 Minuten"
    assert rate_limit.wait_text(300) == "5 Minuten"
