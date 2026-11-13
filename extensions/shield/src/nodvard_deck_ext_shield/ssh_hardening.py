from __future__ import annotations

import asyncio
import re
import secrets
from http import HTTPStatus
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from nodvard_sdk import ActionRequest, ActionResult, ActionSpec, Actor, DryRunReport, Risk
from pydantic import BaseModel

from . import antivirus as av

ACTION = "shield.ssh_hardening"
VERSION = 26
NO_KEY = "Der Deck-Zugang nutzt nur ein Passwort. Bitte zuerst einen SSH-Schlüssel hinterlegen."
ROOT_WARNING = "Deck meldet sich als root an. Root bleibt mit Schlüssel erlaubt (PermitRootLogin prohibit-password)."
ROOT_KEYS_NOTE = ("Für root sind Schlüssel eingetragen. Damit niemand ausgesperrt wird, bleibt root mit Schlüssel erlaubt "
                  "(PermitRootLogin prohibit-password); gesperrt wird nur die Anmeldung mit Passwort.")
LOCKOUT_NOTE = ("Du kommst danach nur noch mit einem SSH-Schlüssel auf den Server. Halte einen eigenen Schlüssel "
                "oder einen Konsolenzugang (zum Beispiel über Proxmox) bereit.")
PASSWORD_LOGIN = ("Deck meldet sich an diesem Server noch mit Passwort an und würde sich selbst aussperren. "
                  "Bitte unter Einstellungen → Server & Zugänge zuerst den Schlüssel als Standard setzen.")
TWO_FACTOR = ("Dieser Server verlangt beim SSH-Login einen zweiten Faktor (PAM oder AuthenticationMethods). "
              "Die Härtung würde Schlüssel plus Code brechen und wird deshalb nicht angeboten.")
LABELS = {"on": "SSH härten", "off": "Härtung zurücknehmen", "add-key": "Deck-Schlüssel eintragen"}
SPEC = ActionSpec(action_type=ACTION, label="SSH-Härtung", default_risk=Risk.HIGH,
                  permissions=["hosts.execute"], host_bound=False, command_field="command",
                  fingerprint_exclude_fields=["token", "command"])


class _Change(BaseModel):
    mode: Literal["on", "off", "add-key"]
    credential_id: str | None = None
    key_credential_id: str | None = None


def command(operation: str, username: str, *args: str) -> str:
    return av.via_helper(["ssh-hardening", operation, username, *args], None, version=VERSION, helper_only=True)


def build(payload: dict) -> str:
    mode, username = payload.get("mode"), payload.get("username")
    key, token = payload.get("public_key"), payload.get("token")
    if mode not in LABELS or not isinstance(username, str) or not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*", username):
        raise ValueError("Ungültige SSH-Aktion.")
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError("Ungültige SSH-Aktion.")
    if mode != "off" and (not isinstance(key, str) or not re.fullmatch(
        r"(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)) [A-Za-z0-9+/=]{1,16384}", key,
    )):
        raise ValueError(NO_KEY)
    if mode == "on":
        return command("apply", username, token)
    if mode == "add-key":
        return command(mode, username, *key.split())
    return command(mode, username)


async def checked(deck_ssh, cmd: str, marker: str | None = None) -> str:
    result = await deck_ssh.run(cmd)
    out = result.stdout or ""
    if av.NO_ROOT in out:
        raise ValueError(av.no_root_message(out))
    if result.exit_code != 0 or (marker and marker not in out.splitlines()):
        raise ValueError("Das SSH-Hilfsskript konnte den Schritt nicht bestätigen. " + (result.stderr or out).strip()[-1000:])
    return out


async def inspect(deck_ssh) -> dict:
    key = deck_ssh.key_public_key
    out = await checked(deck_ssh, command("status", deck_ssh.username, *(key.split() if key else [])))
    return {"has_key": bool(key), "authorized": "@@authorized=yes" in out.splitlines(),
            "hardened": "@@hardened=yes" in out.splitlines(), "managed": "@@managed=yes" in out.splitlines(),
            "root": deck_ssh.username == "root", "warning": ROOT_WARNING if deck_ssh.username == "root" else None,
            "root_keys": "@@root_keys=yes" in out.splitlines(),
            "two_factor": "@@two_factor=yes" in out.splitlines(), "unconfirmed": "@@unconfirmed=yes" in out.splitlines(),
            "login_has_key": bool(deck_ssh.public_key), "credential_id": deck_ssh.credential_id,
            "key_credential_id": deck_ssh.key_credential_id}


def refuse_unsafe(deck_ssh, state: dict) -> None:
    """Härten nur, wenn Deck selbst mit dem Schlüssel hineinkommt und kein zweiter Faktor im Spiel ist."""
    if deck_ssh.public_key is None or deck_ssh.public_key != deck_ssh.key_public_key:
        raise ValueError(PASSWORD_LOGIN)
    if state["two_factor"]:
        raise ValueError(TWO_FACTOR)


class SshHardening:
    action_types = frozenset({ACTION})

    def __init__(self, ctx) -> None:
        self._ctx = ctx
        self._locks: dict[str, asyncio.Lock] = {}

    async def host(self, host_id):
        host = await self._ctx.hosts.get(host_id)
        if host is None or host.os_family != "linux" or not host.is_managed or not host.has_credential:
            raise ValueError("Kein verwalteter Linux-Server mit SSH-Zugang.")
        return host

    async def execute(self, req: ActionRequest) -> ActionResult:
        async with self._locks.setdefault(req.host_ref or "", asyncio.Lock()):
            try:
                result = await self._execute(req)
            except Exception:
                result = ActionResult(success=False, error="Der SSH-Zugang konnte nicht geprüft werden.")
            await self._ctx.audit.log(action=ACTION, outcome="success" if result.success else "failure",
                                      target_type="host", target_id=req.host_ref, actor=req.proposed_by,
                                      reason=result.error or req.reason, detail={"mode": req.payload.get("mode")})
            return result

    async def _execute(self, req: ActionRequest) -> ActionResult:
        try:
            cmd = build(req.payload)
            if req.payload.get("command") != cmd:
                raise ValueError("Der Vorschlag passt nicht mehr. Bitte neu auslösen.")
            host = await self.host(req.host_ref)
            mode = req.payload["mode"]
            access = {} if mode == "on" else {
                key: req.payload[key] for key in ("credential_id", "key_credential_id") if req.payload.get(key)
            }
            async with self._ctx.exec.ssh_session(host, **access) as deck_ssh:
                if (deck_ssh.username != req.payload["username"] or deck_ssh.key_public_key != req.payload["public_key"]
                    or any(key in req.payload and getattr(deck_ssh, key) != req.payload[key]
                           for key in ("credential_id", "key_credential_id"))
                    or ("login_public_key" in req.payload and deck_ssh.public_key != req.payload["login_public_key"])):
                    raise ValueError("Der SSH-Zugang hat sich geändert. Bitte neu auslösen.")
                if mode == "off":
                    await checked(deck_ssh, cmd, "@@hardened=no")
                elif mode == "add-key":
                    await checked(deck_ssh, cmd, "@@authorized=yes")
                else:
                    state = await inspect(deck_ssh)
                    if not state["has_key"]:
                        raise ValueError(NO_KEY)
                    if not state["authorized"]:
                        raise ValueError("Bitte zuerst den Deck-Schlüssel in authorized_keys eintragen.")
                    if state["managed"]:
                        raise ValueError("Die Härtungsdatei ist bereits vorhanden. Bitte zuerst zurücknehmen.")
                    refuse_unsafe(deck_ssh, state)
                    token = req.payload["token"]
                    try:
                        await checked(deck_ssh, cmd, "@@pending=yes")
                        await deck_ssh.verify_key()
                        await checked(deck_ssh, command("confirm", deck_ssh.username, token), "@@hardened=yes")
                    except BaseException as exc:
                        try:
                            await asyncio.shield(checked(deck_ssh, command("rollback", deck_ssh.username, token), "@@rollback=yes"))
                            error = "Die SSH-Härtung ist fehlgeschlagen und wurde zurückgenommen."
                        except Exception:
                            error = "Die SSH-Härtung ist fehlgeschlagen. Der Rückweg konnte nicht bestätigt werden. Bitte „SSH-Zustand prüfen“ ausführen."
                        if isinstance(exc, asyncio.CancelledError):
                            raise
                        return ActionResult(success=False, error=error)
                return ActionResult(success=True, detail={"note": f"{LABELS[mode]}: erledigt."})
        except ValueError as exc:
            return ActionResult(success=False, error=str(exc))

    async def dry_run(self, req: ActionRequest) -> DryRunReport:
        build(req.payload)
        return DryRunReport(would_change=True, summary=req.reason)

    def routers(self, manage_dependencies=()):
        read = APIRouter(prefix="/defender")
        manage = APIRouter(prefix="/defender", dependencies=list(manage_dependencies))

        @read.get(
            "/hosts/{host_id}/ssh-hardening",
            responses={code: {"description": HTTPStatus(code).phrase, "content": {"application/json": {}}} for code in (400,)},
        )
        async def state(host_id: str, credential_id: str | None = None, key_credential_id: str | None = None):
            try:
                host = await self.host(host_id)
                access = {key: value for key, value in {
                    "credential_id": credential_id, "key_credential_id": key_credential_id,
                }.items() if value is not None}
                async with self._ctx.exec.ssh_session(host, **access) as deck_ssh:
                    try:
                        return await inspect(deck_ssh)
                    except ValueError as exc:
                        return {"has_key": bool(deck_ssh.key_public_key), "authorized": False, "hardened": None,
                                "managed": False, "root": deck_ssh.username == "root",
                                "warning": ROOT_WARNING if deck_ssh.username == "root" else None, "error": str(exc),
                                "root_keys": False, "two_factor": False, "unconfirmed": False,
                                "login_has_key": bool(deck_ssh.public_key), "credential_id": deck_ssh.credential_id,
                                "key_credential_id": deck_ssh.key_credential_id}
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc

        @manage.post(
            "/hosts/{host_id}/ssh-hardening",
            responses={code: {"description": HTTPStatus(code).phrase, "content": {"application/json": {}}} for code in (400,)},
        )
        async def propose(host_id: str, change: _Change, actor: Actor = Depends(self._ctx.api.current_actor)):
            try:
                host = await self.host(host_id)
                access = {} if change.mode == "on" else {
                    key: value for key, value in {
                        "credential_id": change.credential_id, "key_credential_id": change.key_credential_id,
                    }.items() if value is not None
                }
                async with self._ctx.exec.ssh_session(host, **access) as deck_ssh:
                    if change.mode != "off" and not deck_ssh.key_public_key:
                        raise ValueError(NO_KEY)
                    current = None
                    if change.mode == "on":
                        current = await inspect(deck_ssh)
                        if not current["authorized"]:
                            raise ValueError("Bitte zuerst den Deck-Schlüssel in authorized_keys eintragen.")
                        if current["managed"]:
                            raise ValueError("Die Härtungsdatei ist bereits vorhanden. Bitte zuerst zurücknehmen.")
                        refuse_unsafe(deck_ssh, current)
                    fields = {"mode": change.mode, "username": deck_ssh.username,
                              "public_key": deck_ssh.key_public_key, "token": secrets.token_hex(16),
                              "login_public_key": deck_ssh.public_key, "credential_id": deck_ssh.credential_id,
                              "key_credential_id": deck_ssh.key_credential_id}
                    fields["command"] = build(fields)
                    reason = f"{LABELS[change.mode]} auf {host.display_name}."
                    if change.mode == "add-key":
                        reason += f" Anmeldung mit Zugang {deck_ssh.credential_id}; Schlüssel {deck_ssh.key_credential_id} für {deck_ssh.username}."
                    if change.mode == "on":
                        reason += " " + LOCKOUT_NOTE
                    if change.mode == "on" and deck_ssh.username == "root":
                        reason += " " + ROOT_WARNING
                    elif current is not None and current["root_keys"]:
                        reason += " " + ROOT_KEYS_NOTE
                decision = await self._ctx.actions.propose(ActionRequest(
                    action_type=ACTION, host_ref=host.id, payload=fields, risk=Risk.HIGH, proposed_by=actor, reason=reason,
                ))
                return {"action_id": decision.action_id, "status": decision.status.value, "risk": "high"}
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc

        return read, manage
