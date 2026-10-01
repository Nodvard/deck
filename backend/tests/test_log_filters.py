"""Download-Tickets gehoeren nicht ins Zugriffsprotokoll von uvicorn (core/log_filters.py)."""

from __future__ import annotations

import logging

import pytest
from nodvard_deck.core import log_filters

TICKET = "Zx3-secret-ticket_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abc"
ACCESS_FORMAT = '%s - "%s %s HTTP/%s" %d'


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (f"/api/v1/system/backups/download/{TICKET}", "/api/v1/system/backups/download/…"),
        (f"/api/v1/system/backups/download/{TICKET}/status", "/api/v1/system/backups/download/…/status"),
        (f"/api/v1/system/backups/download/{TICKET}?x=1", "/api/v1/system/backups/download/…"),
        (f"/proxy/api/v1/system/backups/download/{TICKET}", "/proxy/api/v1/system/backups/download/…"),
        ("/api/v1/system/backups/download-jobs/abc", "/api/v1/system/backups/download-jobs/abc"),
        ("/api/v1/system/backups/download", "/api/v1/system/backups/download"),
        ("/api/v1/system/backups", "/api/v1/system/backups"),
        ("/api/v1/health", "/api/v1/health"),
    ],
)
def test_redact_path(path, expected):
    assert log_filters.redact_path(path) == expected


def _record(path: str) -> logging.LogRecord:
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, ACCESS_FORMAT, ("10.0.0.5:51234", "GET", path, "1.1", 200), None)


def test_filter_rewrites_uvicorn_access_arguments():
    record = _record(f"/api/v1/system/backups/download/{TICKET}")
    assert log_filters.TicketRedactFilter().filter(record) is True
    line = record.getMessage()
    assert TICKET not in line
    assert line == '10.0.0.5:51234 - "GET /api/v1/system/backups/download/… HTTP/1.1" 200'
    other = _record("/api/v1/health")
    log_filters.TicketRedactFilter().filter(other)
    assert other.getMessage() == '10.0.0.5:51234 - "GET /api/v1/health HTTP/1.1" 200'


def test_filter_ignores_records_with_other_shapes():
    for record in (
        logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "ohne Argumente", None, None),
        logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "%s", ("nur eins",), None),
        logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "%s %s %s", ("a", "b", 3), None),
    ):
        assert log_filters.TicketRedactFilter().filter(record) is True


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


def test_installed_filter_keeps_the_ticket_out_of_the_access_log():
    logger = logging.getLogger(log_filters.ACCESS_LOGGER)
    before = list(logger.filters)
    handler = _ListHandler()
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        log_filters.install_access_log_filters()
        log_filters.install_access_log_filters()  # idempotent
        assert sum(isinstance(f, log_filters.TicketRedactFilter) for f in logger.filters) == 1
        logger.info(ACCESS_FORMAT, "10.0.0.5:1", "GET", f"/api/v1/system/backups/download/{TICKET}", "1.1", 200)
        logger.info(ACCESS_FORMAT, "10.0.0.5:1", "GET", f"/api/v1/system/backups/download/{TICKET}/status", "1.1", 200)
        logger.info(ACCESS_FORMAT, "10.0.0.5:1", "GET", "/api/v1/system/backups/download-jobs/jobid123", "1.1", 200)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        logger.filters[:] = before
    assert len(handler.lines) == 3
    assert all(TICKET not in line for line in handler.lines)
    assert "download/…" in handler.lines[0] and "download/…/status" in handler.lines[1] and "jobid123" in handler.lines[2]


@pytest.mark.asyncio
async def test_create_app_installs_the_filter(client):
    # Die Test-App kommt aus create_app(): der Filter haengt danach am Logger.
    logger = logging.getLogger(log_filters.ACCESS_LOGGER)
    assert any(isinstance(f, log_filters.TicketRedactFilter) for f in logger.filters)
