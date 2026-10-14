"""Proxmox-Backups: Status der VZDump-Jobs pro VM und Wiederholen über das Aktions-Gate."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from nodvard_sdk import (
    Actor,
    Badge,
    ExtensionContext,
    GridSize,
    HealthReport,
    HostToolSpec,
    ListItem,
    ListView,
    NodvardExtension,
    PageSpec,
    Refresh,
    Risk,
    SecretNotOwned,
    WidgetAction,
    WidgetSpec,
    same_target,
)
from nodvard_sdk.actions import REQUEST_WAIT_S, ActionRequest, ActionSpec
from nodvard_sdk.errors import NodvardError
from pydantic import BaseModel, Field, field_validator

from .capabilities import BackupActionExecutor, ProxmoxBackupProvider, job_vmids
from .certificate import FIELD as FINGERPRINT_FIELD
from .certificate import MAX_INPUT as MAX_FINGERPRINT_INPUT
from .certificate import build_certificate_router, connection_pin, normalize_fingerprint
from .config import TOKEN_LABEL, build_connectors, foreign_secret_text
from .connector import ProxmoxBackupApiError
from .job_edit import JobEditError, build_job_create, build_job_update, current_values, job_snapshot
from .receiver import ProxmoxConnectionReceiver
from .restore import follow_up_open_copies
from .restore import register as register_restore
from .space import check_backup_space
from .watch import InventoryCache, run_backup_watch


_LAST_STATUS_LABEL = {
    "ok": "erfolgreich", "failed": "fehlgeschlagen", "running": "läuft", "unknown": "unbekannt",
    "unprotected": "kein Backup-Job", "unreachable": "nicht erreichbar",
}
"""Anzeigetext für `last_status` im Widget; die Seite hat dieselbe Zuordnung in BackupsPage.tsx."""

_LAST_STATUS_TONE = {
    "ok": "good", "failed": "danger", "running": "good", "unknown": "neutral", "unprotected": "warn",
    "unreachable": "danger",
}
"""Die Farbe hängt am Maschinenwert und wird im Backend entschieden (Badge-Regel, docs/02)."""


def _space_warning_response(warning: dict[str, Any]) -> JSONResponse:
    """409 statt Vorschlag: die Seite fragt nach und schickt bei „trotzdem“ dieselbe Anfrage mit `ignore_space: true`."""
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": warning["message"], "space_warning": warning},
    )


class _TokenIn(BaseModel):
    value: str


class _ConnectionIn(BaseModel):
    """Das Token selbst kommt nie hierher, sondern nur über `POST .../{name}/token` in den Tresor."""

    name: str
    base_url: str
    token_id: str
    tls_insecure_skip_verify: bool = False
    tls_fingerprint_sha256: str | None = Field(default=None, max_length=MAX_FINGERPRINT_INPUT)
    enabled: bool = True

    @field_validator("tls_fingerprint_sha256")
    @classmethod
    def _fingerprint(cls, value: str | None) -> str | None:
        return normalize_fingerprint(value)


class _AcknowledgeIn(BaseModel):
    note: str = ""


class _ConnectionPatch(BaseModel):
    base_url: str | None = None
    token_id: str | None = None
    tls_insecure_skip_verify: bool | None = None
    tls_fingerprint_sha256: str | None = Field(default=None, max_length=MAX_FINGERPRINT_INPUT)
    """Leer oder `null` nimmt den gemerkten Fingerabdruck weg."""
    enabled: bool | None = None

    @field_validator("tls_fingerprint_sha256")
    @classmethod
    def _fingerprint(cls, value: str | None) -> str | None:
        return normalize_fingerprint(value)


class _ConnectionOut(BaseModel):
    name: str
    base_url: str
    token_id: str
    tls_insecure_skip_verify: bool
    enabled: bool
    has_token: bool
    tls_fingerprint_sha256: str | None = None
    """Der gemerkte Fingerabdruck (`AB:CD:...`), sonst `None`."""
    tls_fingerprint_invalid: bool = False
    """Im Feld steht kein Fingerabdruck; die Verbindung bleibt gesperrt, bis es stimmt."""


class _BackupWatchJobSpec:
    """Backup-Wächter: alle 15 Minuten, meldet nur Zustandswechsel und richtet offene Kopien (restore.py) fertig ein."""

    id = "watch"
    name = "Backup-Wächter"
    schedule = "*/15 * * * *"
    params: dict[str, Any] = {}
    enabled = True

    def __init__(self, ctx: ExtensionContext, provider: ProxmoxBackupProvider, connectors_factory: Any = None) -> None:  # noqa: ANN401
        self._ctx = ctx
        self._provider = provider
        self._connectors_factory = connectors_factory
        self._inventory_cache = InventoryCache()

    async def handler(self, **_: Any) -> dict[str, int]:
        if self._connectors_factory is not None:
            await follow_up_open_copies(self._ctx, self._connectors_factory)
        return await run_backup_watch(self._ctx, self._provider, self._inventory_cache)


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx

        async def _connectors_factory():
            return await build_connectors(ctx)

        self._provider = ProxmoxBackupProvider(ctx, _connectors_factory)
        executor = BackupActionExecutor(_connectors_factory)
        ctx.capabilities.provide(self._provider)
        ctx.capabilities.provide(executor)
        # Die Proxmox-Erweiterung legt beim Verbinden per Passwort gleich einen Backup-Zugang an (receiver.py).
        ctx.capabilities.provide(ProxmoxConnectionReceiver(ctx))

        ctx.actions.register(
            ActionSpec(
                action_type="backup.run",
                label="Backup jetzt ausführen",
                description="Startet einen sofortigen VZDump-Lauf für diese VM über die Proxmox-API.",
                default_risk=Risk.MEDIUM,
                permissions=["hosts.execute"],
                host_bound=False,
                confirm_text="Startet sofort ein Backup dieser VM mit den Einstellungen des Jobs. Hat der Job eine Aufbewahrung, können danach ältere Sicherungen dieser VM auf dem Speicher gelöscht werden, auch manuelle und die anderer Jobs. Ohne eigene Aufbewahrung bleibt alles erhalten. Fortfahren?",
            )
        )
        ctx.actions.register(
            ActionSpec(
                action_type="backup.job_create",
                label="Backup-Job anlegen",
                description="Legt einen Backup-Job für einen Gast ohne Backup an.",
                default_risk=Risk.LOW,
                permissions=["hosts.execute"],
                host_bound=False,
            )
        )
        ctx.actions.register(
            ActionSpec(
                action_type="backup.job_update",
                label="Backup-Job ändern",
                description="Zeitplan, Aktiv, Speicher, Modus, Aufbewahrung eines Backup-Jobs.",
                default_risk=Risk.MEDIUM,
                # Der Nutzer braucht settings.write (Route); die Erweiterung schlägt nur vor und
                # braucht die Aktionsrechte selbst, wie bei backup.run.
                permissions=["hosts.execute"],
                host_bound=False,
            )
        )

        ctx.settings.declare(
            {
                "type": "object",
                "properties": {
                    "connections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "base_url": {"type": "string"},
                                "token_id": {"type": "string"},
                                "tls_insecure_skip_verify": {"type": "boolean"},
                                "tls_fingerprint_sha256": {"type": ["string", "null"]},
                                "enabled": {"type": "boolean"},
                            },
                            "required": ["name", "base_url", "token_id"],
                        },
                    },
                    "backup_max_age_days": {"type": "integer", "minimum": 2, "maximum": 60, "default": 3},
                    "backup_age_skip": {"type": "array", "items": {"type": "string"}},
                    "acknowledged_unprotected": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "connection": {"type": "string"},
                                "vmid": {"type": "string"},
                                "note": {"type": "string"},
                            },
                            "required": ["connection", "vmid"],
                        },
                    },
                },
                "required": ["connections"],
            }
        )

        router = APIRouter()

        @router.post("/connections/{name}/token", status_code=status.HTTP_204_NO_CONTENT)
        async def set_token(name: str, payload: _TokenIn) -> None:
            label = f"{TOKEN_LABEL}:{name}"
            if await ctx.secrets.exists(label):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Ein Token für '{name}' existiert bereits -- zuerst über DELETE /api/v1/secrets/{{id}} entfernen.",
                )
            await ctx.secrets.create(label=label, kind="generic", value=payload.value)

        async def _drop_secret_or_refuse(label: str) -> None:
            """Löscht das Geheimnis einer Verbindung; gehört es nicht dieser Erweiterung (etwa von Hand im Tresor
            angelegt), bleibt es und die Anfrage endet mit 409."""
            foreign = False
            try:
                await ctx.secrets.delete(label)
            except SecretNotOwned:
                foreign = True
            if foreign:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=foreign_secret_text(label))

        async def _connection_out(conn: dict[str, Any]) -> _ConnectionOut:
            name = str(conn.get("name") or "")
            pin, pin_invalid = connection_pin(conn)
            return _ConnectionOut(
                name=name,
                base_url=str(conn.get("base_url") or ""),
                token_id=str(conn.get("token_id") or ""),
                tls_insecure_skip_verify=bool(conn.get("tls_insecure_skip_verify") or False),
                enabled=conn.get("enabled") is not False,
                has_token=await ctx.secrets.exists(f"{TOKEN_LABEL}:{name}") if name else False,
                tls_fingerprint_sha256=pin,
                tls_fingerprint_invalid=pin_invalid,
            )

        @router.get("/connections")
        async def list_connections() -> list[_ConnectionOut]:
            """Die Verbindungen dieser Erweiterung (eigene Liste, unabhängig von der Proxmox-Erweiterung)."""
            settings = await ctx.settings.get()
            connections: list[dict[str, Any]] = settings.get("connections") or []
            return [await _connection_out(c) for c in connections]

        @router.post("/connections", status_code=status.HTTP_201_CREATED)
        async def add_connection(payload: _ConnectionIn) -> _ConnectionOut:
            settings = await ctx.settings.get()
            connections: list[dict[str, Any]] = settings.get("connections") or []
            if any(c.get("name") == payload.name for c in connections):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Verbindung '{payload.name}' existiert bereits.",
                )
            # Neue Verbindung startet ohne Token: ein altes Geheimnis gleichen Namens gehörte zu einer anderen
            # Adresse. Ein fremdes (nicht von dieser Erweiterung) bleibt, dann wird nichts angelegt.
            await _drop_secret_or_refuse(f"{TOKEN_LABEL}:{payload.name}")
            entry = payload.model_dump()
            if entry.get(FINGERPRINT_FIELD) is None:
                entry.pop(FINGERPRINT_FIELD, None)
            else:
                entry["tls_insecure_skip_verify"] = False  # der Fingerabdruck ersetzt den Schalter
            connections.append(entry)
            settings["connections"] = connections
            await ctx.settings.set(settings)
            return await _connection_out(entry)

        @router.put("/connections/{name}")
        async def update_connection(name: str, payload: _ConnectionPatch) -> _ConnectionOut:
            settings = await ctx.settings.get()
            connections: list[dict[str, Any]] = settings.get("connections") or []
            for conn in connections:
                if conn.get("name") != name:
                    continue
                updates = payload.model_dump(exclude_unset=True)
                old_url = conn.get("base_url")
                conn.update(updates)
                if FINGERPRINT_FIELD in updates:
                    if updates[FINGERPRINT_FIELD] is None:
                        conn.pop(FINGERPRINT_FIELD, None)
                    elif "tls_insecure_skip_verify" not in updates:
                        conn["tls_insecure_skip_verify"] = False  # der Fingerabdruck ersetzt den Schalter
                if "base_url" in updates and not same_target(old_url, conn.get("base_url")):
                    # Das Token gehört zur alten Adresse und darf nicht an die neue gehen. Vor dem Speichern:
                    # bei einem fremden Geheimnis bleibt die Adresse unverändert.
                    await _drop_secret_or_refuse(f"{TOKEN_LABEL}:{name}")
                    if FINGERPRINT_FIELD not in updates:
                        # Der gemerkte Fingerabdruck gehört zum alten Server.
                        conn.pop(FINGERPRINT_FIELD, None)
                settings["connections"] = connections
                await ctx.settings.set(settings)
                return await _connection_out(conn)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Verbindung '{name}' existiert nicht.")

        @router.delete("/connections/{name}", status_code=status.HTTP_204_NO_CONTENT)
        async def remove_connection(name: str) -> None:
            settings = await ctx.settings.get()
            connections: list[dict[str, Any]] = settings.get("connections") or []
            filtered = [c for c in connections if c.get("name") != name]
            if len(filtered) == len(connections):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Verbindung '{name}' existiert nicht.")
            settings["connections"] = filtered
            await ctx.settings.set(settings)
            try:
                await ctx.secrets.delete(f"{TOKEN_LABEL}:{name}")
            except SecretNotOwned:
                # Die Verbindung ist weg; ein fremdes Geheimnis (etwa von Hand im Tresor) bleibt unberührt.
                ctx.logger.warning("Backups: Verbindung %s entfernt, fremdes Geheimnis im Tresor bleibt", name)

        ctx.api.include_router(router, permission="secrets.write")
        # Fingerabdruck des Zertifikats ansehen und bestätigen (certificate.py); ändert die Verbindung wie PUT oben.
        ctx.api.include_router(build_certificate_router(ctx), permission="secrets.write")

        data_router = APIRouter()

        @data_router.get("/jobs")
        async def list_jobs() -> list[dict[str, Any]]:
            try:
                return await self._provider.list_jobs()
            except (RuntimeError, NodvardError, ProxmoxBackupApiError) as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

        @data_router.get("/jobs/{job_ref}/history")
        async def job_history(job_ref: str) -> list[dict[str, Any]]:
            try:
                return await self._provider.job_history(job_ref)
            except (RuntimeError, NodvardError, ProxmoxBackupApiError) as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

        @data_router.get("/jobs/{job_ref}/failure")
        async def job_failure(job_ref: str, upid: str | None = None) -> dict[str, Any]:
            """Warum der neueste (oder der genannte) Lauf fehlschlug, in Klartext (failure_text.py) samt Zeile von
            Proxmox; 404, wenn es keinen solchen Lauf gibt."""
            if job_ref.count("--") < 2:  # kein gültiger job_ref (<Verbindung>--<Job>--<VMID>)
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Backup-Job.")
            try:
                cause = await self._provider.failure_cause(job_ref, upid)
            except (RuntimeError, NodvardError, ProxmoxBackupApiError) as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
            if cause is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kein fehlgeschlagener Lauf gefunden.")
            return cause

        @data_router.get("/unprotected")
        async def unprotected_guests() -> dict[str, Any]:
            """Gäste, die kein Backup-Job erfasst (Proxmox' eigene Auswertung)."""
            try:
                return await self._provider.unprotected()
            except (RuntimeError, NodvardError) as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

        @data_router.get("/inventory")
        async def backup_inventory_overview() -> dict[str, Any]:
            """Was tatsächlich auf den Backup-Speichern liegt, je Gast. Nicht im Widget (alle 60 s), weil das Lesen
            der NFS-Verzeichnisse dauert."""
            try:
                return await self._provider.inventory()
            except (RuntimeError, NodvardError) as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

        edit_router = APIRouter()

        async def _job_for(job_ref: str):  # noqa: ANN202 - (connector, connection, job_id, job)
            try:
                connection, job_id, _vmid = job_ref.split("--", 2)
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Job.") from exc
            connector = (await build_connectors(ctx)).get(connection)
            if connector is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Verbindung '{connection}' unbekannt.")
            try:
                job = await connector.get_job(job_id)
            except ProxmoxBackupApiError as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc) or type(exc).__name__) from exc
            if not job:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Backup-Job '{job_id}' existiert nicht (mehr).")
            return connection, job_id, job

        @edit_router.get("/jobs/{job_ref}/config")
        async def job_config(job_ref: str) -> dict[str, Any]:
            """Die editierbaren Werte eines Jobs."""
            _connection, job_id, job = await _job_for(job_ref)
            return {"job_id": job_id, **current_values(job)}

        @edit_router.post("/jobs/{job_ref}/edit")
        async def edit_job(
            job_ref: str,
            changes: dict[str, Any] = Body(..., embed=True),
            ignore_space: bool = Body(False, embed=True),
            actor: Actor = Depends(ctx.api.current_actor),
        ) -> Any:  # noqa: ANN401 - dict oder die 409-Platzwarnung
            """Schlägt die Änderung dem Gate vor. Weniger Aufbewahrung oder Abschalten ist hohes Risiko (Proxmox
            löscht beim nächsten Lauf, was über der Grenze liegt). Bei neuem Speicher oder Wiedereinschalten wird
            vorher der Platz geprüft (space.py), sonst 409 mit `space_warning`."""
            connection, job_id, job = await _job_for(job_ref)
            try:
                _params, diff, destructive = build_job_update(job, changes)
            except JobEditError as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
            changed = {d["key"]: d["new"] for d in diff}
            if not ignore_space and ("storage" in changed or changed.get("enabled") is True):
                connector = (await build_connectors(ctx)).get(connection)
                if connector is not None:
                    try:
                        resources = await connector.guest_resources()
                    except ProxmoxBackupApiError:
                        resources = []
                    target = changed.get("storage") or job.get("storage")
                    warning = await check_backup_space(connector, job_vmids(job, resources), target)
                    if warning is not None:
                        return _space_warning_response(warning)
            request = ActionRequest(
                action_type="backup.job_update",
                payload={"connection": connection, "job_id": job_id, "changes": changes, "expected": job_snapshot(job)},
                risk=Risk.HIGH if destructive else Risk.MEDIUM,
                proposed_by=actor,
                reason=f"Backup-Job '{job_id}' über die Seite „Proxmox-Backups“ geändert: " + ", ".join(d["label"] for d in diff),
                correlation_id=job_ref,
            )
            decision = await ctx.actions.propose(request, wait_s=REQUEST_WAIT_S)
            return {"action_id": decision.action_id, "status": decision.status.value, "risk": request.risk.value}

        @edit_router.post("/unprotected/{connection}/{vmid}/job")
        async def create_job_for_guest(
            connection: str,
            vmid: str,
            values: dict[str, Any] = Body(..., embed=True),
            ignore_space: bool = Body(False, embed=True),
            actor: Actor = Depends(ctx.api.current_actor),
        ) -> Any:  # noqa: ANN401 - dict oder die 409-Platzwarnung
            """Neuer Job nur für einen Gast ohne Backup, niedriges Risiko (ein zusätzliches Backup nimmt nichts weg).
            Passt der Gast nicht auf den Speicher (space.py), kommt 409 mit `space_warning`; `ignore_space` heißt
            „trotzdem“."""
            connector = (await build_connectors(ctx)).get(connection)
            if connector is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Verbindung '{connection}' unbekannt.")
            try:
                build_job_create(vmid, values)
            except JobEditError as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
            if not ignore_space:
                warning = await check_backup_space(connector, vmid, str(values.get("storage") or ""))
                if warning is not None:
                    return _space_warning_response(warning)
            request = ActionRequest(
                action_type="backup.job_create",
                payload={"connection": connection, "vmid": vmid, "values": values},
                risk=Risk.LOW,
                proposed_by=actor,
                reason=f"Backup-Job für VMID {vmid} über die Seite „Proxmox-Backups“ angelegt.",
                correlation_id=f"{connection}/{vmid}",
            )
            decision = await ctx.actions.propose(request, wait_s=REQUEST_WAIT_S)
            return {"action_id": decision.action_id, "status": decision.status.value, "risk": request.risk.value}

        ack_router = APIRouter()

        async def _set_acknowledged(connection: str, vmid: str, note: str | None) -> None:
            if not vmid.isdigit():
                raise HTTPException(status_code=422, detail="VMID muss eine Zahl sein.")
            settings = await ctx.settings.get()
            acks = [
                a for a in settings.get("acknowledged_unprotected") or []
                if not (a.get("connection") == connection and str(a.get("vmid")) == vmid)
            ]
            if note is not None:
                acks.append({"connection": connection, "vmid": vmid, "note": note.strip()[:200]})
            settings["acknowledged_unprotected"] = acks
            await ctx.settings.set(settings)

        @ack_router.put("/unprotected/{connection}/{vmid}/acknowledged", status_code=status.HTTP_204_NO_CONTENT)
        async def acknowledge_unprotected(connection: str, vmid: str, payload: _AcknowledgeIn) -> None:
            """„Bewusst ohne Backup“: eine Konfigurationsentscheidung, daher `settings.write`. Der Gast bleibt auf
            der Seite sichtbar, warnt aber nicht mehr im Widget."""
            await _set_acknowledged(connection, vmid, payload.note)

        @ack_router.delete("/unprotected/{connection}/{vmid}/acknowledged", status_code=status.HTTP_204_NO_CONTENT)
        async def unacknowledge_unprotected(connection: str, vmid: str) -> None:
            await _set_acknowledged(connection, vmid, None)

        retry_router = APIRouter()

        @retry_router.post("/jobs/{job_ref}/retry")
        async def retry_job(
            job_ref: str, ignore_space: bool = Body(False, embed=True), actor: Actor = Depends(ctx.api.current_actor),
        ) -> Any:  # noqa: ANN401
            try:
                request = (await self._provider.retry(job_ref)).model_copy(update={"proposed_by": actor})
            except NodvardError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
            except (RuntimeError, ProxmoxBackupApiError) as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
            if not ignore_space:
                connector = (await build_connectors(ctx)).get(str(request.payload.get("connection")))
                warning = (
                    await check_backup_space(connector, str(request.payload.get("vmid")), request.payload.get("storage"))
                    if connector is not None else None
                )
                if warning is not None:
                    return _space_warning_response(warning)
            decision = await ctx.actions.propose(request, wait_s=REQUEST_WAIT_S)
            # `risk` dazu: die Seite prüft bei status="proposed", ob der Nutzer actions.approve:<risk> hat,
            # bevor sie automatisch nachbestätigt.
            return {"action_id": decision.action_id, "status": decision.status.value, "risk": request.risk.value}

        # ProxmoxBackupApiError muss mit abgefangen werden: ein echter API-Fehler (z. B. ein von Proxmox
        # abgelehnter Parameter) soll eine leere Liste ergeben, keinen rohen 500.
        @data_router.get("/widgets/summary")
        async def summary_widget_data() -> dict[str, Any]:
            try:
                jobs = await self._provider.list_jobs()
            except (RuntimeError, NodvardError, ProxmoxBackupApiError):
                jobs = []
            try:
                unprotected = (await self._provider.unprotected())["guests"]
            except (RuntimeError, NodvardError):
                unprotected = []
            # Nicht erreichbare Verbindungen und ungesicherte Gäste zuerst, sonst gehen sie auf der kleinen
            # Kachel unter. Ohne `job_ref` gibt es keinen Sicherungs-Knopf.
            unreachable = [j for j in jobs if j.get("last_status") == "unreachable"]
            rows = unreachable + [
                {**g, "job_ref": "", "storage": "kein Backup-Job", "last_status": "unprotected"}
                for g in unprotected
                if not g.get("acknowledged")
            ] + [j for j in jobs if j.get("last_status") != "unreachable"]
            # Angezeigt wird das deutsche Wort; die Farbe hängt am Maschinenwert.
            for row in rows:
                row["last_status_label"] = _LAST_STATUS_LABEL.get(row.get("last_status"), row.get("last_status"))
                row["tone"] = _LAST_STATUS_TONE.get(row.get("last_status"), "neutral")
                # Beide Knöpfe starten dasselbe Backup, heißen aber je nach Lage anders: „Erneut versuchen“ gibt
                # es nur nach einem Fehlschlag. Ohne `job_ref` und während eines Laufs gibt es keinen.
                has_job = bool(row.get("job_ref"))
                row["can_retry"] = has_job and row.get("last_status") == "failed"
                row["can_backup_now"] = has_job and row.get("last_status") in ("ok", "unknown")
            return {"data": rows, "meta": {}}

        # Lesen braucht hosts.read, alles Auslösende hosts.execute; ohne Berechtigung wäre ein Router
        # öffentlich, ohne Login erreichbar.
        ctx.api.include_router(data_router, permission="hosts.read")
        ctx.api.include_router(retry_router, permission="hosts.execute")
        ctx.api.include_router(ack_router, permission="settings.write")
        ctx.api.include_router(edit_router, permission="settings.write")
        # Gast aus einer Sicherung als Kopie wiederherstellen (restore.py): eigene Aktion, eigene Routen.
        register_restore(ctx, _connectors_factory)

        ctx.ui.register_page(
            PageSpec(
                id="backups",
                path="/backups",
                title="Proxmox-Backups",
                icon="database-backup",
                nav_section="Infrastruktur",
                nav_order=20,
                component="BackupsPage",
            )
        )

        ctx.ui.register_widget(
            WidgetSpec(
                id="summary",
                title="Backup-Center",
                icon="database-backup",
                size=GridSize(w=2, h=2),
                refresh=Refresh(interval_s=60),
                data_endpoint="widgets/summary",
                permissions=["hosts.execute"],
                view=ListView(
                    item=ListItem(
                        title="{{ name }}",
                        subtitle="{{ storage }} · {{ connection }}",
                        badge=Badge(text="{{ last_status_label }}", tone="{{ tone }}"),
                        actions=[
                            WidgetAction(
                                id="retry", label="Erneut versuchen", endpoint="jobs/{{ job_ref }}/retry",
                                method="POST", confirm=True,
                                confirm_text="Startet sofort ein Backup dieser VM mit den Einstellungen des Jobs. Hat der Job eine Aufbewahrung, können danach ältere Sicherungen dieser VM auf dem Speicher gelöscht werden, auch manuelle und die anderer Jobs. Ohne eigene Aufbewahrung bleibt alles erhalten. Fortfahren?",
                                style="danger", permissions=["hosts.execute"],
                                show_if="{{ can_retry }}",
                            ),
                            WidgetAction(
                                id="backup_now", label="Jetzt sichern", endpoint="jobs/{{ job_ref }}/retry",
                                method="POST", confirm=True,
                                confirm_text="Startet sofort ein Backup dieser VM mit den Einstellungen des Jobs. Hat der Job eine Aufbewahrung, können danach ältere Sicherungen dieser VM auf dem Speicher gelöscht werden, auch manuelle und die anderer Jobs. Ohne eigene Aufbewahrung bleibt alles erhalten. Fortfahren?",
                                style="secondary", permissions=["hosts.execute"],
                                show_if="{{ can_backup_now }}",
                            ),
                        ],
                    ),
                    empty_text="Keine Backup-Jobs konfiguriert",
                ),
            )
        )

        ctx.ui.register_host_tool(HostToolSpec(
            id="backups", title="Proxmox-Backups", icon="database-backup", category="data",
            description="Backup-Jobs, letzter Lauf, vorhandene Sicherungen", path="/backups?host={host_id}",
            kinds=["vm", "lxc"],
        ))

        await ctx.scheduler.register_job(_BackupWatchJobSpec(ctx, self._provider, _connectors_factory))

    async def on_start(self, ctx: ExtensionContext) -> None:
        return None

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        """Gesund nur, wenn alle Verbindungen erreichbar sind, damit ein Ausfall nicht hinter einer funktionierenden
        verschwindet; `details[name]` zeigt jede einzeln, `message` fasst alle zusammen."""
        try:
            connectors = await build_connectors(ctx)
        except Exception as exc:  # noqa: BLE001 - Health darf nie werfen
            return HealthReport(healthy=False, message=str(exc))
        if not connectors:
            return HealthReport(healthy=False, message="Keine Verbindung konfiguriert.")

        details: dict[str, Any] = {}
        summaries: list[str] = []
        all_healthy = True
        for name, connector in connectors.items():
            try:
                await connector.list_jobs()
                details[name] = {"healthy": True}
                summaries.append(f"{name}: ok")
            except Exception as exc:  # noqa: BLE001 - Health darf nie werfen
                details[name] = {"healthy": False, "error": str(exc)}
                code = getattr(exc, "code", None) if isinstance(exc, ProxmoxBackupApiError) else None
                if code:
                    details[name]["code"] = code  # z. B. "certificate_changed": die Oberfläche bietet das Bestätigen an
                summaries.append(f"{name}: FEHLER: {exc}")
                all_healthy = False

        return HealthReport(healthy=all_healthy, message="; ".join(summaries), details=details)
