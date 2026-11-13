"""CVE-Abgleich und Außenprüfung je Server."""

from __future__ import annotations

import asyncio
import logging
import time
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool

from . import cve, external
from .defender_api import _JobSpec, _register_job
from .ids import LOGGER
from .patching import load_baseline, load_baselines, save_baseline

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext, Host

    from .defender import Defender

DEFAULT_CRON = "30 6 * * *"


class CveFinding(BaseModel):
    package: str
    installed_version: str
    cve: str
    severity: Literal["unknown", "low", "medium", "high", "critical"]
    fixed_version: str | None


class CveCounts(BaseModel):
    high: int
    critical: int


class CveResult(BaseModel):
    status: Literal["ok", "error", "unsupported"]
    checked_at: float
    findings: list[CveFinding]
    counts: CveCounts
    error: str | None


class CertificateOut(BaseModel):
    hostname: str
    port: int
    status: Literal["ok", "warning", "unavailable"]
    issuer: str | None
    expires_at: float | None
    days_remaining: float | None
    hostname_matches: bool | None
    self_signed: bool | None
    trusted: bool | None
    protocol: str | None
    weak_protocols: list[str]
    protocol_check_available: bool
    warnings: list[str]
    error: str | None


class ExternalResult(BaseModel):
    status: Literal["ok", "warning", "error", "unavailable"]
    checked_at: float
    open_ports: list[int]
    new_ports: list[int]
    certificates: list[CertificateOut]
    warnings: list[str]
    error: str | None


class CheckOut(BaseModel):
    host_id: str
    host_name: str
    running: bool
    external_enabled: bool
    cve: CveResult | None
    external: ExternalResult | None


class CheckList(BaseModel):
    hosts: list[CheckOut]


class CheckSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_enabled: StrictBool


class CheckRunning(Exception):
    pass


class ServerChecks:
    def __init__(self, ctx: ExtensionContext, defender: Defender) -> None:
        self._ctx = ctx
        self._defender = defender
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._slots = asyncio.Semaphore(2)

    async def host(self, host_id: str) -> Host | None:
        return next((h for h in await self._defender.target_hosts() if h.id == host_id), None)

    def _row(self, host: Host, data: dict, config: dict, settings: dict) -> dict:
        return {
            "host_id": host.id, "host_name": host.display_name or host.name,
            "running": host.id in self._tasks or bool(self._locks.get(host.id) and self._locks[host.id].locked()),
            "external_enabled": bool(settings.get("external_checks_enabled", True) and config.get("external_enabled", True)),
            "cve": data.get("cve"), "external": data.get("external"),
        }

    async def overview(self) -> dict:
        hosts = await self._defender.target_hosts()
        data = await load_baselines(self._ctx, "checks")
        configs = await load_baselines(self._ctx, "checks-config")
        settings = await self._ctx.settings.get()
        return {"hosts": [self._row(h, data.get(h.id, {}), configs.get(h.id, {}), settings) for h in hosts]}

    async def set_external(self, host: Host, enabled: bool) -> dict:
        lock = self._locks.setdefault(host.id, asyncio.Lock())
        if lock.locked() or host.id in self._tasks:
            raise CheckRunning
        async with lock:
            settings = await self._ctx.settings.get()
            if enabled and not settings.get("external_checks_enabled", True):
                raise ValueError("Die Außenprüfung ist in den Shield-Einstellungen ausgeschaltet.")
            config = {"external_enabled": enabled}
            await save_baseline(self._ctx, host.id, "checks-config", config)
            data = await load_baseline(self._ctx, host.id, "checks") or {}
        return self._row(host, data, config, settings)

    async def start_host(self, host: Host) -> dict:
        data = await load_baseline(self._ctx, host.id, "checks") or {}
        config = await load_baseline(self._ctx, host.id, "checks-config") or {}
        settings = await self._ctx.settings.get()
        if host.id in self._tasks or (host.id in self._locks and self._locks[host.id].locked()):
            raise CheckRunning
        self._tasks[host.id] = asyncio.create_task(self._manual(host))
        return self._row(host, data, config, settings)

    async def _manual(self, host: Host) -> None:
        try:
            await self.run_host(host)
        except CheckRunning:
            pass
        except Exception:
            logging.getLogger(LOGGER).exception("shield_checks_failed host=%s", host.id)
        finally:
            self._tasks.pop(host.id, None)

    def cancel_tasks(self) -> None:
        for task in self._tasks.values():
            task.cancel()

    async def run_host(self, host: Host) -> dict:
        lock = self._locks.setdefault(host.id, asyncio.Lock())
        if lock.locked():
            raise CheckRunning
        async with lock, self._slots:
            config = await load_baseline(self._ctx, host.id, "checks-config") or {}
            data: dict[str, Any] = {}
            try:
                data["cve"] = await cve.check_host(self._ctx, host.id)
            except Exception:
                logging.getLogger(LOGGER).exception("shield_cve_check_failed host=%s", host.id)
                data["cve"] = {"status": "error", "checked_at": time.time(), "findings": [],
                               "counts": {"high": 0, "critical": 0}, "error": "Der CVE-Abgleich ist fehlgeschlagen."}
            settings = await self._ctx.settings.get()
            if settings.get("external_checks_enabled", True) and config.get("external_enabled", True):
                previous = await load_baseline(self._ctx, host.id, "external")
                try:
                    data["external"] = await external.check_host(self._ctx, host.id, previous)
                except Exception:
                    logging.getLogger(LOGGER).exception("shield_external_check_failed host=%s", host.id)
                    data["external"] = {"status": "error", "checked_at": time.time(), "open_ports": [],
                                        "new_ports": [], "certificates": [], "warnings": [],
                                        "error": "Die Außenprüfung ist fehlgeschlagen."}
                if data["external"]["status"] in ("ok", "warning"):
                    await save_baseline(self._ctx, host.id, "external", data["external"])
            else:
                saved = await load_baseline(self._ctx, host.id, "checks") or {}
                data["external"] = saved.get("external")
            await save_baseline(self._ctx, host.id, "checks", data)
        return self._row(host, data, config, settings)

    async def run(self) -> dict:
        if not (await self._ctx.settings.get()).get("checks_enabled", True):
            return {"hosts": 0, "finished": 0}
        hosts = await self._defender.target_hosts()

        async def one(host: Host) -> bool:
            try:
                if host.id in self._tasks:
                    return False
                await self.run_host(host)
                return True
            except CheckRunning:
                return False
            except Exception:
                logging.getLogger(LOGGER).exception("shield_checks_failed host=%s", host.id)
                return False

        finished = await asyncio.gather(*(one(h) for h in hosts))
        return {"hosts": len(hosts), "finished": sum(finished)}


def _errors(*codes: int) -> dict:
    return {code: {"description": HTTPStatus(code).phrase, "content": {"application/json": {}}} for code in codes}


def build_routers(service: ServerChecks) -> tuple[APIRouter, APIRouter]:
    read, manage = APIRouter(prefix="/defender"), APIRouter(prefix="/defender")

    async def host_for(host_id: str) -> Host:
        host = await service.host(host_id)
        if host is None:
            raise HTTPException(status_code=404, detail="Server unbekannt oder nicht für Shield verfügbar.")
        return host

    @read.get("/checks", response_model=CheckList)
    async def overview() -> dict:
        return await service.overview()

    @manage.post("/checks/{host_id}/run", response_model=CheckOut, status_code=202, responses=_errors(404, 409))
    async def run(host_id: str) -> dict:
        host = await host_for(host_id)
        try:
            return await service.start_host(host)
        except CheckRunning as exc:
            raise HTTPException(status_code=409, detail="Dieser Server wird gerade geprüft.") from exc

    @manage.patch("/checks/{host_id}", response_model=CheckOut, responses=_errors(404, 409))
    async def configure(host_id: str, payload: CheckSettings) -> dict:
        host = await host_for(host_id)
        try:
            return await service.set_external(host, payload.external_enabled)
        except CheckRunning as exc:
            raise HTTPException(status_code=409, detail="Dieser Server wird gerade geprüft.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return read, manage


async def register_job(ctx: ExtensionContext, service: ServerChecks) -> None:
    settings = await ctx.settings.get()
    await _register_job(ctx, _JobSpec("server-checks", "Shield: CVE-Abgleich und Außenprüfung",
                                    settings.get("checks_cron") or DEFAULT_CRON, settings.get("checks_enabled", True),
                                    service.run, fallback=DEFAULT_CRON))
