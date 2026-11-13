"""Nutzungsdaten sind ausschließlich durch den Inhaber einsehbar und änderbar."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ...telemetry import service
from ...telemetry.schema import Choices
from ..deps import SessionDep, SettingsDep, require_owner
from ..responses import error_responses

router = APIRouter(
    prefix="/telemetry", tags=["telemetry"], dependencies=[Depends(require_owner)], responses=error_responses(401, 403),
)


@router.get("")
async def get_telemetry(session: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    return await service.status(session, settings)


@router.post("/preview")
async def preview(payload: Choices, session: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    return await service.status(session, settings, choices=payload)


@router.put("", responses=error_responses(409))
async def configure(payload: Choices, session: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    try:
        return await service.configure(session, settings, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Das Teilen von Nutzungsdaten ist für diese Installation gesperrt.") from exc


@router.post("/send")
async def send(session: SessionDep, settings: SettingsDep) -> dict[str, str]:
    return {"result": await service.send(session, settings)}


@router.delete("")
async def delete_data(session: SessionDep, settings: SettingsDep) -> dict[str, str]:
    return {"result": await service.delete_data(session, settings)}
