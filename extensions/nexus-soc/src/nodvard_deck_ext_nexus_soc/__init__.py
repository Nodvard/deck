"""nexus-soc-Extension -- KI-gestuetzte Docker-Wache. Uebernimmt Regeln/Heuristiken
aus dem Skript des Vorgaengersystems (Prompt-Regeln, Sperrwortliste,
Regex-Fixes), baut die Struktur aber neu: PARSEN (`parsing.parse_ai_response`,
rein) -> VORSCHLAGEN
(`ctx.actions.propose()`, geht durchs Kern-Gate) -> MELDEN (aus dem `ActionResult`,
nie aus der KI-Absicht).

**Bewusste Abgrenzung gegen den Chat-Endpunkt des Vorgaengersystems:**
kein Schluesselwort-Sonderpfad ("morning"/"update"/"backup" -> eigener Codezweig,
teils AUSSERHALB der Sperrlisten-Pruefung). Jede Chat-Antwort UND jede Watcher-
Zusammenfassung durchlaeuft denselben `parse_ai_response()` -> `ctx.actions.propose()`
-Pfad, ausnahmslos.

**Aus KI-Text entsteht hoechstens ein Container-Neustart.** Er wird als `action_type="shell.exec"`
vorgeschlagen (kein eigener `docker.restart`-Typ -- der ActionExecutor dafuer gehoert der
`terminal`-Extension), aber Server, Befehl und Begruendung baut der Code selbst aus dem Vorfall
(`_propose_from_response`); von der KI kommt nur der Wunsch. Jeder andere KI-Vorschlag wird nur als
Text gemeldet. Container-Logs gehen als nicht vertrauenswuerdige Daten in den Prompt.
**Ehrlich abgegrenzt:** ohne aktivierte `terminal`-Extension findet das Gate keinen
Executor, der Vorschlag bleibt bis zum Ablauf `proposed`/schlaegt sichtbar mit "Kein
ActionExecutor" fehl -- kein deklariertes `requires`, weil `core.gate.find_executor()`
ohnehin uneingeschraenkt sucht (siehe dortigen Docstring).

**Vorfalls-Historie:** Vorfaelle liegen jetzt dauerhaft in `ext_nexus_soc_incidents`
(models.py/history.py, eigener Alembic-Branch) statt in einer `deque` im Prozess --
nach einem Neustart ist die Historie noch da, `GET /history` durchsucht sie (Text,
Status, Host, Zeitraum), und jeder Statuswechsel steht zusaetzlich im Kern-Audit-Log
(`nexus_soc.incident_status`, `correlation_id` = Vorfall-ID)."""

from __future__ import annotations

import logging
import shlex
import time
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from nodvard_sdk import (
    Actor,
    ActionRequest,
    Badge,
    ExtensionContext,
    GridSize,
    HealthReport,
    HostRequirementSpec,
    HostToolSpec,
    NodvardExtension,
    ListItem,
    ListView,
    Notification,
    PageSpec,
    Refresh,
    Risk,
    Severity,
    WidgetAction,
    WidgetSpec,
)
from pydantic import BaseModel

from .defender import Defender
from .defender_api import ACTION_SPECS, DefenderExecutor, build_routers, register_jobs
from .guard import Guard
from .hostscope import host_scope
from .patching import UpdateCenter
from .history import STATUS_LABEL, STATUSES, IncidentRepository
from .incident_queue import MAX_ATTEMPTS, IncidentQueueRepository
from .incidents import Incident, IncidentStore, new_incident_id
from .models import Base as HistoryBase
from .remediation import (
    cause_notice,
    cause_prompt_line,
    build_restart_command,
    cause_tag_for_command,
    classify_exit_cause,
    contradiction_notice,
    drop_contradictions,
    facts_notice,
    resolve_restart_target,
    restart_risk_eligible,
    sanitize_untrusted,
)
from .ollama import OllamaConnectorType, OllamaProvider
from .parsing import ParsedActionKind, parse_ai_response, strip_decision_block
from .prompts import MAX_INSPECT_TEXT_CHARS, MAX_LOG_CHARS, SYSTEM_PROMPT, build_chat_prompt, build_incident_prompt
from .watcher import (
    BaselineStateStore,
    ContainerTransition,
    DockerWatcher,
    inspect_exit_command,
    parse_exit_facts,
)

RETRY_BACKOFF_S = 300.0
"""Wartezeit, bis ein fehlgeschlagener Batch erneut versucht wird."""
INCIDENT_MERGE_WINDOW_S = 24 * 3600.0
"""Ein neuer Absturz desselben Containers zaehlt bis so lange nach dem ERSTEN Auftreten am
noch unbearbeiteten Vorfall mit; danach beginnt ein neuer Vorfall (und eine neue Meldung).
Hoechstens eine Meldung je Container und Tag, aber nie dauerhaft still."""
_WAITING_ACTION_STATUSES = frozenset({"proposed", "approved", "executing"})


def _flush_backoff_s(failures: int) -> float:
    """Pause der Sammelschleife nach `failures` Fehlern in Folge: 5 s, verdoppelt bis 5 min."""
    return float(min(300, 5 * 2 ** max(0, failures - 1)))


_SEVERITY_BY_STATUS = {"open": "critical", "proposed": "warning", "reviewed": "warning", "resolved": "info", "dismissed": "info"}


class _ChatIn(BaseModel):
    message: str


class _TokenIn(BaseModel):
    value: str


class StatsOut(BaseModel):
    by_status: dict[str, int]
    pending_batch: int
    watched_hosts: int
    ai_healthy: bool
    ai_message: str | None
    ai_configured: bool = True
    """`False`: es ist gar kein KI-Server eingetragen. Die KI ist optional, das ist kein Fehler (die Seite zeigt es grau)."""


class Extension(NodvardExtension):
    def __init__(self) -> None:
        self._store = IncidentStore()
        self._history: IncidentRepository | None = None
        self._queue: IncidentQueueRepository | None = None
        self._ai: OllamaProvider | None = None
        self._watcher: DockerWatcher | None = None
        self._batch_ready: Any = None  # asyncio.Event, erst in setup() gebaut (braucht den Event-Loop des Hosts)
        self._ctx: ExtensionContext | None = None
        self._defender: Defender | None = None
        self._updates: UpdateCenter | None = None
        self._guard: Guard | None = None
        self._retry_tasks: set[Any] = set()  # wartende Wiederholungen (werden bei on_stop abgebrochen)

    async def setup(self, ctx: ExtensionContext) -> None:
        import asyncio

        self._ctx = ctx
        self._batch_ready = asyncio.Event()
        ctx.db.declare_tables(HistoryBase.metadata)
        self._history = IncidentRepository(ctx)
        self._queue = IncidentQueueRepository(ctx)
        self._ai = OllamaProvider(ctx)
        ctx.capabilities.provide(self._ai)
        ctx.connectors.register_type(OllamaConnectorType())

        ctx.settings.declare(
            {
                "type": "object",
                "properties": {
                    "ollama_url": {"type": "string"},
                    "ollama_model": {"type": "string"},
                    "ollama_failover_url": {"type": "string"},
                    "ollama_failover_model": {"type": "string"},
                    "incident_batch_delay_s": {"type": "number"},
                    "host_target_cooldown_s": {"type": "number"},
                    "docker_host_tag": {"type": "string"},
                    "forbidden_host_keywords": {"type": "array", "items": {"type": "string"}},
                    "suppressed_hosts": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["ollama_url"],
            }
        )

        self._watcher = DockerWatcher(ctx, on_transition=self._on_transition, state_store=BaselineStateStore(ctx))

        # Virenschutz: ClamAV, Quarantaene, Lynis.
        self._defender = Defender(ctx)
        self._updates = UpdateCenter(ctx, self._defender)
        self._guard = Guard(ctx, self._defender)
        ctx.capabilities.provide(DefenderExecutor(ctx, self._defender, self._updates))
        for spec in ACTION_SPECS:
            ctx.actions.register(spec)
        await register_jobs(ctx, self._defender, self._updates, self._guard)
        defender_read, defender_manage = build_routers(ctx, self._defender, self._updates, self._guard)
        ctx.api.include_router(defender_read, permission="soc.read")
        ctx.api.include_router(defender_manage, permission="soc.manage")

        router = APIRouter()

        @router.post("/chat")
        async def chat(payload: _ChatIn) -> dict:
            settings = await ctx.settings.get()
            tag = settings.get("docker_host_tag") or "docker"
            hosts = await ctx.hosts.list(tag=tag)
            hosts_summary = "\n".join(f"- {h.display_name}: {h.status.value}" for h in hosts) or "(keine bekannt)"
            prompt = build_chat_prompt(user_message=payload.message, hosts_summary=hosts_summary)
            reply = await self._ai.complete(prompt, system=SYSTEM_PROMPT)
            proposals, _outcome, _action_id = await self._propose_from_response(
                ctx, reply, correlation_id=None, show_unchecked_command=True
            )
            display = strip_decision_block(reply)
            return {"reply": display or reply, "proposals": proposals}

        @router.get("/widgets/incidents")
        async def incidents_widget_data() -> dict:
            items, _total = await self._history.search(exclude_status=("dismissed",), limit=50)
            rows = [
                {
                    "id": r["id"],
                    "title": r["message"] if r["occurrences"] <= 1 else f"{r['message']} ({r['occurrences']}x)",
                    "host": r["host_name"],
                    "target": r["target"],
                    "ts": r["created_at"],
                    # `severity` bleibt als maschinenlesbarer Wert erhalten; ANGEZEIGT
                    # wird `status_label` (deutsch), gefaerbt ueber `tone` (explizit,
                    # siehe history.STATUS_TONE) -- Badge-Text und Farbe sind entkoppelt.
                    "severity": _SEVERITY_BY_STATUS.get(r["status"], "warning"),
                    "status_label": r["status_label"],
                    "tone": r["tone"],
                    "summary": r["ai_summary"] or "",
                }
                for r in items
            ]
            return {"data": rows, "meta": {}}

        async def _change_status(incident_id: str, new_status: str, actor: Actor) -> dict:
            changed = await self._history.set_status(incident_id, new_status)
            if changed is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Vorfall.")
            # Vorher nur im Prozessspeicher -- wer wann bestaetigt/
            # verworfen hat, war nach einem Neustart weg und nirgends nachvollziehbar.
            await ctx.audit.log(
                action="nexus_soc.incident_status",
                outcome="success",
                target_type="host",
                target_id=changed["host_id"],
                reason=f"{STATUS_LABEL.get(changed['previous_status'], changed['previous_status'])} -> {STATUS_LABEL.get(new_status, new_status)}",
                detail={"from": changed["previous_status"], "to": new_status, "target": changed["target"]},
                correlation_id=incident_id,
                actor=actor,
            )
            await ctx.ws.broadcast("incidents", {"changed": incident_id})
            return {"ok": True, "status": new_status}

        @router.post("/incidents/{incident_id}/confirm")
        async def confirm_incident(incident_id: str, actor: Actor = Depends(ctx.api.current_actor)) -> dict:
            return await _change_status(incident_id, "reviewed", actor)

        @router.post("/incidents/{incident_id}/dismiss")
        async def dismiss_incident(incident_id: str, actor: Actor = Depends(ctx.api.current_actor)) -> dict:
            return await _change_status(incident_id, "dismissed", actor)

        @router.post("/incidents/{incident_id}/resolve")
        async def resolve_incident(incident_id: str, actor: Actor = Depends(ctx.api.current_actor)) -> dict:
            return await _change_status(incident_id, "resolved", actor)

        @router.post("/incidents/{incident_id}/reopen")
        async def reopen_incident(incident_id: str, actor: Actor = Depends(ctx.api.current_actor)) -> dict:
            return await _change_status(incident_id, "open", actor)

        @router.get("/history")
        async def search_history(
            q: str | None = None,
            status_filter: str | None = Query(default=None, alias="status"),
            host: str | None = None,
            since: datetime | None = None,
            until: datetime | None = None,
            limit: int = Query(default=50, ge=1, le=200),
            offset: int = Query(default=0, ge=0),
        ) -> dict:
            """Durchsuchbare Vorfalls-Historie: freier Text trifft
            Nachricht, Container, Host und KI-Zusammenfassung; dazu Status, Host,
            Zeitraum; neueste zuerst, seitenweise. `hosts` liefert die Auswahl fuer
            den Host-Filter gleich mit."""
            if status_filter and status_filter not in STATUSES:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unbekannter Status '{status_filter}'.")
            items, total = await self._history.search(
                q=q, status=status_filter, host=host, since=since, until=until, limit=limit, offset=offset
            )
            return {"items": items, "total": total, "hosts": await self._history.hosts()}

        # Nachtrag: `SocPage.tsx` war bisher fast ausschliesslich
        # ein Chat-UI -- der eigentliche Zweck der Seite (echtes SOC-Monitoring:
        # Live-Vorfall-Feed, Status/Statistiken, was die KI erkannt/vorgeschlagen
        # hat) war nicht abgebildet. `GET /widgets/incidents` liefert absichtlich nur
        # das WIDGET-Format (gefiltert, auf `severity` verdichtet) -- die Detailseite
        # braucht den vollen Datensatz (Status, KI-Zusammenfassung, verknuepfte
        # Aktion) inkl. dismissed-Vorfaellen, deshalb ein eigener Endpunkt statt den
        # Widget-Endpunkt zweckzuentfremden.
        @router.get("/incidents")
        async def list_incidents(status_filter: str | None = None) -> list[dict]:
            items, _total = await self._history.search(status=status_filter, limit=200)
            return items

        @router.get("/stats")
        async def get_stats() -> StatsOut:
            by_status = await self._history.count_by_status()
            settings = await ctx.settings.get()
            tag = settings.get("docker_host_tag") or "docker"
            hosts = await ctx.hosts.list(tag=tag)
            ai_health = await self._ai.health()
            ai_configured = bool(settings.get("ollama_url") or settings.get("ollama_failover_url"))
            return StatsOut(
                by_status=by_status, pending_batch=self._store.pending_count(), watched_hosts=len(hosts),
                ai_healthy=ai_health.ok, ai_message=ai_health.message, ai_configured=ai_configured,
            )

        @router.get("/ai/models")
        async def get_ai_models(which: Literal["primary", "failover"] = Query(default="primary")) -> dict:
            """Auswahlliste fuer die Einstellung "KI-Modell" (`x-widget: "remote-select"`). Fragt
            nur die GESPEICHERTEN Adressen; es gibt keinen Adress-Parameter (siehe `list_models`)."""
            from .ollama import list_models

            return await list_models(ctx, which)

        token_router = APIRouter()

        @token_router.post("/token", status_code=status.HTTP_204_NO_CONTENT)
        async def set_token(payload: _TokenIn) -> None:
            """Optionales Ollama-API-Token (z. B. hinter einem Reverse-Proxy mit
            Auth) -- wie bei ntfy/proxmox im Vault, nie im Klartext."""
            from .ollama import _TOKEN_LABEL

            if await ctx.secrets.exists(_TOKEN_LABEL):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Ein Token existiert bereits -- zuerst über DELETE /api/v1/secrets/{id} entfernen.",
                )
            await ctx.secrets.create(label=_TOKEN_LABEL, kind="generic", value=payload.value)

        # "soc.read" wie im docs/02-EXTENSION-API.md §4/§5-Beispiel woertlich --
        # deckt Chat, Vorfalls-Feed UND Bestaetigen/Verwerfen ab (keine feinere
        # Aufteilung vorgesehen). Das Token-Setzen braucht separat
        # `secrets.write`, wie bei ntfy/proxmox.
        ctx.api.include_router(router, permission="soc.read")
        ctx.api.include_router(token_router, permission="secrets.write")

        ctx.ui.register_host_tool(HostToolSpec(
            id="security", title="Sicherheit", icon="shield-check", category="monitoring",
            description="Virenschutz, Updates und Einbruchschutz für diesen Server", path="/soc?host={host_id}",
            permissions=["soc.read"], os_families=["linux"], order=50,
        ))

        # Update-Zentrale, Quarantaene, Haertung und Fail2ban laufen ueber `sudo -n` (antivirus.as_root).
        ctx.ui.register_host_requirement(HostRequirementSpec(
            id="root", label="Root-Rechte (Nodvard Shield)", needs_root=True, order=10,
            root_reason="Updates einspielen, Quarantäne, Härtungs-Audit, Fail2ban",
        ))

        ctx.ui.register_page(
            PageSpec(
                id="soc",
                path="/soc",
                title="Nodvard Shield",
                icon="shield-check",
                nav_section="Sicherheit",
                nav_order=20,
                permissions=["soc.read"],
                component="SocPage",
            )
        )

        ctx.ui.register_widget(
            WidgetSpec(
                id="incidents",
                title="Vorfälle",
                icon="siren",
                size=GridSize(w=2, h=2),
                refresh=Refresh(interval_s=60, ws_channel="ext.nexus-soc.incidents"),
                data_endpoint="widgets/incidents",
                permissions=["soc.read"],
                view=ListView(
                    item=ListItem(
                        title="{{ title }}",
                        subtitle="{{ host }} · {{ ts | relative }}",
                        # Deutsch statt "critical/warning/info"; die Farbe kommt aus
                        # dem expliziten `tone`-Feld, nicht mehr aus dem Wort selbst.
                        badge=Badge(text="{{ status_label }}", tone="{{ tone }}"),
                        actions=[
                            WidgetAction(id="confirm", label="Bestätigen", endpoint="incidents/{{ id }}/confirm", method="POST", style="primary"),
                            WidgetAction(id="dismiss", label="Verwerfen", endpoint="incidents/{{ id }}/dismiss", method="POST"),
                        ],
                    ),
                    empty_text="Keine offenen Vorfälle",
                ),
            )
        )

        ctx.ui.register_widget(
            WidgetSpec(
                id="protection",
                title="Virenschutz",
                icon="shield-check",
                size=GridSize(w=2, h=2),
                refresh=Refresh(interval_s=120, ws_channel="ext.nexus-soc.defender"),
                data_endpoint="defender/widgets/protection",
                permissions=["soc.read"],
                view=ListView(
                    item=ListItem(title="{{ title }}", subtitle="{{ subtitle }}", badge=Badge(text="{{ label }}", tone="{{ tone }}")),
                    empty_text="Keine Server",
                ),
            )
        )

        ctx.ui.register_widget(
            WidgetSpec(
                id="security",
                title="Sicherheitslage",
                icon="shield-alert",
                size=GridSize(w=2, h=2),
                refresh=Refresh(interval_s=120, ws_channel="ext.nexus-soc.defender"),
                data_endpoint="defender/widgets/security",
                permissions=["soc.read"],
                view=ListView(
                    item=ListItem(title="{{ title }}", subtitle="{{ subtitle }}", badge=Badge(text="{{ label }}", tone="{{ tone }}")),
                    empty_text="Noch keine Daten",
                ),
            )
        )

    @staticmethod
    def _register_docker_requirement(ctx: ExtensionContext, tag: str) -> None:
        """Die KI-Container-Wache liest `docker ps` auf den getaggten Servern -- ohne sudo,
        also braucht der SSH-Benutzer die Gruppe "docker". Folgt dem Tag (`docker_host_tag`)."""
        ctx.ui.register_host_requirement(HostRequirementSpec(
            id="docker-group", label="Docker ohne sudo (KI-Container-Wache)",
            check_command="docker ps -q", ok_text="Docker lässt sich ohne sudo benutzen.",
            fail_hint="Benutzer zur Gruppe docker hinzufügen: sudo usermod -aG docker {user}",
            unix_group="docker", tags=[tag], order=40,
        ))

    async def on_start(self, ctx: ExtensionContext) -> None:
        # Nicht in setup(): das liest die Einstellungen (I/O).
        self._register_docker_requirement(ctx, (await ctx.settings.get()).get("docker_host_tag") or "docker")
        # Laeufe vom letzten Dashboard-Start sind mit dem Prozess gestorben -- nicht
        # fuer immer auf "laeuft" stehen lassen (entkoppelte Updates ausgenommen). Ein Fehler hier darf den
        # Start nicht verhindern.
        try:
            await self._defender.abort_interrupted_scans()
            await self._updates.abort_interrupted_runs()
        except Exception:
            logging.getLogger("nodvard_deck.ext.nexus-soc").exception("nexus_soc_abort_interrupted_failed")
        # Updates, die entkoppelt auf dem Server weiterlaufen, weiter abfragen
        # und abschliessen -- einmalig, nicht als Dauerschleife.
        ctx.spawn(self._updates.resume_interrupted(), name="nexus-soc-update-resume")
        # Vorfaelle, die beim letzten Stopp noch im Sammelfenster lagen, wieder aufnehmen.
        await self._resume_open_incidents()
        ctx.spawn(self._watcher.run_forever(), name="nexus-soc-docker-watcher")
        ctx.spawn(self._batch_flush_loop(ctx), name="nexus-soc-batch-flush")

    async def on_stop(self, ctx: ExtensionContext) -> None:
        if self._updates is not None:
            self._updates.cancel_catch_up()
        for task in list(self._retry_tasks):
            task.cancel()
        self._retry_tasks.clear()

    async def on_settings_changed(self, ctx: ExtensionContext, new: dict[str, Any]) -> None:
        # Zeitplaene (Scans, Waechter, Audit) sofort an die neuen Einstellungen anpassen.
        self._register_docker_requirement(ctx, new.get("docker_host_tag") or "docker")
        await register_jobs(ctx, self._defender, self._updates, self._guard)

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        result = await self._ai.health()
        return HealthReport(healthy=result.ok, message=result.message, details={"pending_incidents": self._store.pending_count()})

    async def _on_transition(self, transition: ContainerTransition) -> None:
        settings = await self._ctx.settings.get()
        log = logging.getLogger("nodvard_deck.ext.nexus-soc")
        is_crash = bool(transition.details.get("is_crash"))
        seen_at = time.time()
        # Derselbe Container, dessen Vorfall noch niemand bearbeitet hat: nur mitzaehlen, kein neuer
        # Vorfall und keine neue Meldung (sonst gaebe es bei einem Absturz-Container alle 30 Minuten
        # einen weiteren Eintrag). Zuerst der offene Batch, dann die gespeicherten Vorfaelle.
        pending = self._store.merge_pending(transition.host.name, transition.target, is_crash=is_crash, seen_at=seen_at)
        if pending is not None:
            try:
                await self._queue.add(pending)
            except Exception:  # noqa: BLE001 - der Zaehler ist eine Zugabe, kein Pflichtschritt
                log.exception("nexus_soc_incident_persist_failed")
            return
        try:
            merged = await self._history.merge_into_active(
                host_name=transition.host.name, target=transition.target, is_crash=is_crash,
                seen_at=seen_at, window_s=INCIDENT_MERGE_WINDOW_S, action_waiting=self._action_waiting,
            )
        except Exception:  # noqa: BLE001 - lieber ein weiterer Vorfall als ein verlorenes Ereignis
            log.exception("nexus_soc_incident_merge_failed")
            merged = None
        if merged is not None:
            await self._ctx.ws.broadcast("incidents", {"changed": merged["id"]})
            return
        cooldown_s = settings.get("host_target_cooldown_s") or 1800
        if self._store.is_in_cooldown(transition.host.name, transition.target, cooldown_s=cooldown_s):
            return
        incident = Incident(
            id=new_incident_id(),
            host_id=transition.host.id,
            host_name=transition.host.name,
            target=transition.target,
            message=transition.message,
            details={**transition.details, "occurrences": 1, "last_seen": seen_at},
        )
        # Sofort dauerhaft merken: ein Neustart im Sammelfenster darf den Vorfall nicht verlieren.
        try:
            await self._queue.add(incident)
        except Exception:  # noqa: BLE001 - die Meldung selbst geht auch ohne die Absicherung weiter
            logging.getLogger("nodvard_deck.ext.nexus-soc").exception("nexus_soc_incident_persist_failed")
        is_first = self._store.enqueue(incident)
        if is_first:
            self._batch_ready.set()

    async def _action_waiting(self, action_id: str) -> bool:
        """Wartet die Aktion eines Vorfalls noch auf Freigabe oder laeuft sie gerade?"""
        row = await self._ctx.actions.result(action_id)
        return row is not None and str(getattr(row, "status", "")) in _WAITING_ACTION_STATUSES

    async def _resume_open_incidents(self) -> None:
        log = logging.getLogger("nodvard_deck.ext.nexus-soc")
        try:
            await self._queue.purge_processed()
            expired = await self._queue.expire_stale()
            if expired:
                log.warning("nexus_soc_incidents_expired count=%d", expired)
            open_incidents = await self._queue.load_open()
        except Exception:  # noqa: BLE001 - ein Fehler hier darf den Start nicht verhindern
            log.exception("nexus_soc_incident_resume_failed")
            return
        if open_incidents:
            log.info("nexus_soc_incidents_resumed count=%d", len(open_incidents))
        if self._store.restore(open_incidents):
            self._batch_ready.set()

    async def _batch_flush_loop(self, ctx: ExtensionContext) -> None:
        import asyncio

        log = logging.getLogger("nodvard_deck.ext.nexus-soc")
        failures = 0
        while True:
            await self._batch_ready.wait()
            try:
                settings = await ctx.settings.get()
                delay_s = settings.get("incident_batch_delay_s") or 60
                # Wieder aufgenommene Vorfaelle haben einen Teil des Fensters schon hinter sich.
                oldest = self._store.oldest_created_at()
                if oldest is not None:
                    delay_s = min(delay_s, max(0.0, delay_s - (time.time() - oldest)))
                await asyncio.sleep(delay_s)
                batch = self._store.take_batch()
                self._batch_ready.clear()
                if batch:
                    try:
                        await self._process_batch(ctx, batch)
                    except Exception:  # noqa: BLE001 - der Batch wird erneut eingeplant, die Schleife laeuft weiter
                        log.exception("nexus_soc_batch_process_failed")
                        await self._handle_batch_failure(ctx, batch)
                failures = 0
            except Exception:  # noqa: BLE001 - ein Fehler darf die Schleife nie beenden
                # Fehler VOR der Verarbeitung (z. B. Einstellungen nicht lesbar): Pause mit
                # wachsendem Abstand, den vollen Traceback nur beim ersten Fehler einer Serie.
                failures += 1
                if failures == 1:
                    log.exception("nexus_soc_batch_flush_failed")
                else:
                    log.warning("nexus_soc_batch_flush_failed_again count=%d", failures)
                await asyncio.sleep(_flush_backoff_s(failures))

    async def _handle_batch_failure(self, ctx: ExtensionContext, batch: list[Incident]) -> None:
        """Ein Batch konnte nicht verarbeitet werden: Versuch zaehlen, nach `MAX_ATTEMPTS`
        als "fehlgeschlagen" aufgeben (mit einfacher Meldung ohne KI), sonst nach
        `RETRY_BACKOFF_S` erneut einplanen. Nie werfen -- sonst ginge der Batch verloren."""
        log = logging.getLogger("nodvard_deck.ext.nexus-soc")
        try:
            attempts = await self._queue.bump_attempts([i.id for i in batch])
            retry: list[Incident] = []
            failed: list[Incident] = []
            for incident in batch:
                n = attempts.get(incident.id)
                if n is None:  # schon verarbeitet (z. B. eine Teilgruppe des Batches)
                    continue
                incident.attempts = n
                (failed if n >= MAX_ATTEMPTS else retry).append(incident)
            if failed:
                await self._queue.mark_failed([i.id for i in failed])
                await self._notify_gave_up(ctx, failed)
            if retry:
                import asyncio

                # Bewusst kein `ctx.spawn`: der Kern haengt jeden Task dauerhaft an die Liste der
                # Extension; hier raeumt sich der Task nach dem Ende selbst weg.
                task = asyncio.ensure_future(self._requeue_later(retry))
                self._retry_tasks.add(task)
                task.add_done_callback(self._retry_tasks.discard)
        except Exception:  # noqa: BLE001 - die Eintraege bleiben "offen" und kommen beim naechsten Start wieder
            log.exception("nexus_soc_batch_failure_handling_failed")

    async def _requeue_later(self, incidents: list[Incident]) -> None:
        import asyncio

        await asyncio.sleep(RETRY_BACKOFF_S)
        if self._store.restore(incidents):
            self._batch_ready.set()

    async def _notify_gave_up(self, ctx: ExtensionContext, failed: list[Incident]) -> None:
        lines = "\n".join(f"- [{i.host_name.upper()}] {i.target}: {i.message}" for i in failed)
        await ctx.notify.send(
            Notification(
                title=f"Lagebericht der Container-Wache: Verarbeitung gescheitert ({len(failed)} Ereignis{'se' if len(failed) != 1 else ''})",
                body=(
                    f"Die Auswertung dieser Ereignisse ist nach {MAX_ATTEMPTS} Versuchen gescheitert "
                    "(keine Einschätzung von Nodvard KI). Bitte im Dashboard und im Protokoll nachsehen:\n" + lines
                ),
                severity=Severity.WARNING,
                payload=host_scope(i.host_id for i in failed),
            )
        )

    async def _process_batch(self, ctx: ExtensionContext, batch: list[Incident]) -> None:
        # Vorfaelle, fuer die bei einem frueheren Versuch schon ein Aktionsvorschlag angelegt
        # wurde (`action_id`) oder begonnen wurde (`proposal_started`, Ausgang unklar, etwa weil
        # der Prozess mittendrin starb), werden nur noch fertig gemeldet -- ohne erneute
        # KI-Frage und ohne zweiten Vorschlag. Jede Gruppe wird getrennt behandelt, damit
        # frische Vorfaelle nicht den alten KI-Text bekommen.
        carried = [i for i in batch if i.action_id]
        unclear = [i for i in batch if not i.action_id and i.proposal_started]
        fresh = [i for i in batch if not i.action_id and not i.proposal_started]
        if fresh:
            await self._process_group(ctx, fresh, mode="fresh")
        if carried:
            await self._process_group(ctx, carried, mode="carried")
        if unclear:
            await self._process_group(ctx, unclear, mode="unclear")

    async def _process_group(self, ctx: ExtensionContext, batch: list[Incident], *, mode: str) -> None:
        if mode == "carried":
            ai_summary = batch[0].ai_summary or ""
            action_id: str | None = batch[0].action_id
            outcome = "proposed"
            proposed = True
        elif mode == "unclear":
            ai_summary = (
                "Vorschlag unklar: Das Dashboard wurde beendet, während Nodvard KI eine Aktion vorschlug. "
                "Ob sie angelegt (oder bei voller Autonomie sogar schon ausgeführt) wurde, ist nicht sicher - "
                "bitte unter Aktionen prüfen. Es wird bewusst kein zweiter Vorschlag angelegt."
            )
            action_id = None
            outcome = "failure"
            proposed = False
        else:
            ai_summary, action_id, outcome, proposed = await self._ask_ai_and_propose(ctx, batch)
            if action_id is not None:
                # Sofort am Queue-Eintrag vermerken: scheitert etwas spaeter, gibt es beim
                # erneuten Versuch keinen zweiten Vorschlag.
                await self._queue.save_outcome([i.id for i in batch], action_id=action_id, ai_summary=ai_summary)
        await self._finish_group(ctx, batch, ai_summary=ai_summary, action_id=action_id, outcome=outcome, proposed=proposed)

    async def _collect_exit_facts(
        self, ctx: ExtensionContext, batch: list[Incident]
    ) -> tuple[list[str], dict[tuple[str, str], dict[str, Any]]]:
        """OOMKilled/ExitCode/FinishedAt/Error der betroffenen Container, ein `docker inspect`
        je Host. Gibt die Textzeilen fuer den Prompt UND die strukturierten Fakten
        ((Host-ID, Container) -> Fakten) zurueck. Die Fehlermeldung (`State.Error`) steht bewusst
        nicht in den Zeilen: sie kann aus dem Image oder Entrypoint stammen und geht als fremde Daten in
        den Rahmen (siehe `_ask_ai_and_propose`). Reine Anreicherung: jeder Fehler wird ignoriert."""
        by_host: dict[str, list[str]] = {}
        for incident in batch:
            names = by_host.setdefault(incident.host_id, [])
            if incident.target not in names:
                names.append(incident.target)
        lines: list[str] = []
        structured: dict[tuple[str, str], dict[str, Any]] = {}
        for host_id, names in by_host.items():
            try:
                host = await ctx.hosts.get(host_id)
                if host is None:
                    continue
                result = await ctx.exec.run(host, inspect_exit_command(names), timeout_s=10)
                facts = parse_exit_facts(result.stdout)
            except Exception:  # noqa: BLE001
                continue
            for name in names:
                fact = facts.get(name)
                if fact is None:
                    continue
                structured[(host_id, name)] = fact
                line = f"{name} @ {host.name}: OOMKilled={str(fact['oom_killed']).lower()}, ExitCode={fact['exit_code']}"
                if fact.get("restart_count") is not None:
                    line += f", Neustarts={fact['restart_count']}"
                if fact.get("image"):
                    line += f", Image={sanitize_untrusted(fact['image'], MAX_INSPECT_TEXT_CHARS)}"
                if fact["finished_at"]:
                    line += f", beendet={fact['finished_at']}"
                lines.append(line)
        return lines, structured

    async def _ask_ai_and_propose(
        self, ctx: ExtensionContext, batch: list[Incident]
    ) -> tuple[str, str | None, str, bool]:
        summary_lines = [f"- [{i.host_name.upper()}] {i.target}: {i.message}" for i in batch]
        exit_facts, fact_map = await self._collect_exit_facts(ctx, batch)
        logs: list[str] = []
        for incident in batch:
            if not incident.details.get("is_crash"):
                continue
            host = await ctx.hosts.get(incident.host_id)
            if host is None:
                continue
            try:
                result = await ctx.exec.run(host, f"docker logs --tail 25 {shlex.quote(incident.target)} 2>&1", timeout_s=10)
                if result.stdout.strip():
                    # Das Ende behalten: `docker logs --tail` liefert die neuesten Zeilen zuletzt, und die
                    # Absturzursache steht meist in den letzten.
                    logs.append(
                        f"({sanitize_untrusted(incident.target, 100)} @ {sanitize_untrusted(incident.host_name, 100)})\n"
                        + sanitize_untrusted(result.stdout, MAX_LOG_CHARS, keep_tail=True)
                    )
            except Exception:  # noqa: BLE001 - Log-Abruf ist eine Anreicherung, kein Pflichtschritt
                pass
        # Die Fehlermeldung aus `docker inspect` (State.Error) kann aus dem Image oder Entrypoint stammen:
        # wie eine Logzeile behandeln (gekuerzt, entschaerft, im Rahmen der fremden Daten), nicht bei den Fakten.
        error_seen: set[tuple[str, str]] = set()
        for incident in batch:
            key = (incident.host_id, incident.target)
            fact = fact_map.get(key)
            if key in error_seen or fact is None or not fact.get("error"):
                continue
            error_seen.add(key)
            logs.append(
                f"({sanitize_untrusted(incident.target, 100)} @ {sanitize_untrusted(incident.host_name, 100)}, Docker-Fehlermeldung)\n"
                + sanitize_untrusted(fact["error"], MAX_INSPECT_TEXT_CHARS)
            )

        settings = await ctx.settings.get()
        batch_delay_s = settings.get("incident_batch_delay_s") or 60
        # Die Ursache legt der Code fest (nicht die KI): feste Einordnung je Container aus den
        # Beendigungs-Fakten, verbindlich im Prompt, in der Meldung und in der Aktions-Begruendung.
        causes: dict[tuple[str, str], tuple[str, str, Any]] = {}
        for i in batch:
            key = (i.host_id, i.target)
            if key not in causes:
                causes[key] = (i.target, i.host_name, classify_exit_cause(i.details, fact_map.get(key)))
        cause_lines = [cause_prompt_line(target, host_name, cause) for target, host_name, cause in causes.values()]
        prompt = build_incident_prompt(
            summary_lines=summary_lines, logs=logs, batch_window_s=batch_delay_s,
            exit_facts=exit_facts, cause_lines=cause_lines,
        )
        try:
            ai_reply = await self._ai.complete(prompt, system=SYSTEM_PROMPT)
        except Exception:  # noqa: BLE001 - lieber ein Bericht ohne KI-Text als gar keiner
            logging.getLogger("nodvard_deck.ext.nexus-soc").exception("nexus_soc_ai_complete_failed")
            ai_reply = (
                "⚠️ Nodvard KI nicht erreichbar (Fehler beim Abfragen). Dies ist ein Platzhalter, KEIN "
                "geprüftes Ergebnis -- bitte manuell prüfen."
            )

        # Neustart-Ziele: jeder Absturz-Vorfall dieses Batches (Host-ID aus dem Vorfall). MITTEL nur
        # fuer echte Abstuerze (nicht von aussen per kill beendet, nicht wieder aufgenommen/wiederholt,
        # Fakten vorhanden), siehe `remediation.restart_risk_eligible`; die uebrigen Absturz-Ziele HOCH.
        crash_targets = {(i.host_id, i.target) for i in batch if i.details.get("is_crash")}
        eligible = {
            (i.host_id, i.target)
            for i in batch
            if restart_risk_eligible(
                is_crash=bool(i.details.get("is_crash")),
                resumed=i.resumed,
                attempts=i.attempts,
                fact=fact_map.get((i.host_id, i.target)),
            )
        }

        async def before_propose() -> None:
            # Vermerk VOR dem Vorschlag (Vollautonomie fuehrt ihn sofort aus): stirbt der Prozess
            # jetzt, wird beim Wiederaufnehmen nur gemeldet, nie ein zweiter Vorschlag angelegt.
            await self._queue.mark_proposal_started([i.id for i in batch])
            for i in batch:
                i.proposal_started = True

        action_summaries, outcome, action_id = await self._propose_from_response(
            ctx, ai_reply, correlation_id=batch[0].id, restart_targets=crash_targets | eligible,
            medium_targets=eligible, before_propose=before_propose, causes=causes,
        )
        display_text = strip_decision_block(ai_reply)
        # Saetze, die den Fakten widersprechen, fliegen raus (Beispiel: "OOMKilled=true, da OOMKilled=false").
        display_text, dropped = drop_contradictions(
            display_text, list(fact_map.values()), expected=len(causes), tags={c.tag for _t, _h, c in causes.values()}
        )
        # Ursache und Fakten laut System stehen als feste Zeilen VOR dem KI-Text, was immer die KI schreibt.
        fixed = "\n".join(part for part in (cause_notice(causes), facts_notice(causes, fact_map)) if part)
        display_text = fixed + ("\n\n" + display_text if display_text else "")
        notice = contradiction_notice(dropped)
        if notice:
            display_text += "\n\n" + notice
        ai_summary = display_text if not action_summaries else display_text + "\n\n" + " | ".join(action_summaries)

        return ai_summary, action_id, outcome, action_id is not None

    async def _finish_group(
        self,
        ctx: ExtensionContext,
        batch: list[Incident],
        *,
        ai_summary: str,
        action_id: str | None,
        outcome: str,
        proposed: bool,
    ) -> None:
        has_crash = any(i.details.get("is_crash") for i in batch)
        for incident in batch:
            incident.ai_summary = ai_summary
            incident.action_id = action_id
            if incident.status == "open":
                incident.status = "proposed" if proposed else "reviewed"
            await self._history.save(incident)
            # Live gefunden (vor WP-10): ohne diesen Eintrag war ein
            # "AKTION: KEINE"-Vorfall NIRGENDS dauerhaft sichtbar, und ein
            # abgeschlossener Vorschlag nur ueber eine rein prozessinterne
            # `correlation_id` mit dem Vorfall verknuepft -- nach einem Neustart
            # waehrend des Schattenbetriebs waere der Zusammenhang
            # verloren. Ein Audit-Eintrag PRO Vorfall (nicht nur bei Ablehnung) macht
            # `GET /audit?action=nexus_soc.incident` zur vollstaendigen, dauerhaften
            # Vorfalls-Historie -- ohne eine eigene Tabelle/Alembic-Branch zu brauchen.
            await ctx.audit.log(
                action="nexus_soc.incident",
                outcome=outcome,
                target_type="host",
                target_id=incident.host_id,
                reason=incident.message,
                detail={
                    "host_name": incident.host_name,
                    "target": incident.target,
                    "is_crash": bool(incident.details.get("is_crash")),
                    "ai_summary": ai_summary,
                    "action_id": action_id,
                },
                correlation_id=incident.id,
            )

        resumed = sum(1 for i in batch if i.resumed)
        notify_body = ai_summary
        if resumed:
            notify_body += (
                "\n\nHinweis: Nach einem Neustart des Dashboards wieder aufgenommen "
                f"({resumed} von {len(batch)} Ereignissen wurden schon vor dem Neustart erkannt)."
            )

        await ctx.notify.send(
            Notification(
                title=f"Lagebericht der Container-Wache ({len(batch)} Ereignis{'se' if len(batch) != 1 else ''})",
                body=notify_body,
                severity=Severity.WARNING if has_crash else Severity.INFO,
                payload=host_scope(i.host_id for i in batch),
            )
        )
        # Erst jetzt ist der Batch gemeldet: die dauerhafte Warteschlange freigeben.
        try:
            await self._queue.mark_processed([i.id for i in batch])
        except Exception:  # noqa: BLE001
            logging.getLogger("nodvard_deck.ext.nexus-soc").exception("nexus_soc_incident_mark_processed_failed")
        await ctx.ws.broadcast("incidents", {"count": len(batch)})

    async def _propose_from_response(
        self,
        ctx: ExtensionContext,
        ai_text: str,
        *,
        correlation_id: str | None,
        restart_targets: set[tuple[str, str]] | None = None,
        medium_targets: set[tuple[str, str]] | None = None,
        before_propose: Any = None,
        causes: dict[tuple[str, str], tuple[str, str, Any]] | None = None,
        show_unchecked_command: bool = False,
    ) -> tuple[list[str], str, str | None]:
        """Der VORSCHLAGEN-Schritt -- gemeinsam fuer den Chat-Endpunkt
        UND die Batch-Verarbeitung.

        Aus KI-Text entsteht hoechstens EINE feste Aktion: der Neustart eines Containers, der in
        `restart_targets` steht ((Host-ID, Name) der Absturz-Vorfaelle dieses Batches). Risiko MITTEL,
        wenn das Paar auch in `medium_targets` steht (echter Absturz laut
        `remediation.restart_risk_eligible`), sonst HOCH. Den Server nimmt der Code aus diesem Paar, den
        Befehl baut er selbst (`remediation.build_restart_command`), die Begruendung auch. Von
        der KI kommt nur der Wunsch "diesen Container neu starten". Alles andere (anderer
        Befehl, fremder Container, anderer Server, Chat ohne Batch) wird NICHT als Aktion
        angelegt, sondern nur als Text gemeldet.

        Gibt zusaetzlich zu den Anzeige-Texten eine grobe `outcome`-Klassifikation
        ("proposed"/"denied"/"success") und die entstandene `action_id` zurueck --
        nur `_process_batch()` braucht das (fuer den Vorfalls-Audit-Eintrag, siehe
        dort), der Chat-Endpunkt ignoriert beides.

        `causes` (nur Batch): die feste Einordnung je Container (`remediation.classify_exit_cause`).
        Sie steht als "[Speichermangel] ..." vorn in der Begruendung (`reason`) des Vorschlags, damit
        unter Aktionen die vom System ermittelte Ursache steht, nicht die Deutung der KI.

        `show_unchecked_command` (nur Chat): Der nicht angelegte Vorschlag nennt den Befehl. Die
        Chat-Antwort geht nur an den Fragenden, und die KI kennt dort nur seine Nachricht und die
        Serverliste, keine Logs. Im Bericht fehlt der Befehl (siehe unten)."""
        settings = await ctx.settings.get()
        forbidden = settings.get("forbidden_host_keywords")
        forbidden_keywords = tuple(forbidden) if forbidden else None

        batch_targets = restart_targets or set()
        host_id_by_name = await self._batch_host_ids(ctx, batch_targets)
        summaries: list[str] = []
        # "success"/"proposed"/"denied" -- MUSS `core.audit.VALID_OUTCOMES` treffen
        # (`{"success","failure","denied","proposed"}`), "none" (kein AKTION-Feld/
        # KEINE) bildet auf "success" ab: der Vorfall wurde geprueft, es gab nichts zu
        # tun, kein Fehler.
        outcome = "success"
        action_id: str | None = None
        for parsed in parse_ai_response(ai_text, forbidden_keywords=forbidden_keywords):
            if parsed.kind == ParsedActionKind.REJECTED:
                await ctx.audit.log(
                    action="nexus_soc.proposal_rejected",
                    outcome="denied",
                    reason=parsed.rejection_reason,
                    detail={"command": sanitize_untrusted(parsed.command or "", 300), "host": parsed.host},
                    correlation_id=correlation_id,
                )
                summaries.append(f"Abgelehnt: {parsed.rejection_reason}")
                outcome = "denied"
                continue

            target = resolve_restart_target(parsed.command or "", parsed.host, batch_targets, host_id_by_name)
            host = await ctx.hosts.get(target[0]) if target is not None else None
            if target is None or host is None:
                shown = sanitize_untrusted(parsed.command or "", 300)
                await ctx.audit.log(
                    action="nexus_soc.proposal_rejected",
                    outcome="denied",
                    reason="Kein Neustart eines abgestürzten Containers dieses Berichts: nur als Text angezeigt.",
                    detail={"command": shown, "host": sanitize_untrusted(parsed.host or "", 100)},
                    correlation_id=correlation_id,
                )
                if show_unchecked_command:
                    summaries.append(f"Vorschlag der KI, nicht geprüft – nicht automatisch angelegt: {shown}")
                else:
                    # Im Bericht kann der Befehl Zugangsdaten aus den Container-Logs tragen. Bericht,
                    # Meldung und Vorfalls-Liste sehen auch reine Leser: dort ohne Befehl. Er steht
                    # nur im Protokoll-Eintrag oben (`command`), den nur Leute mit Server-Recht sehen.
                    summaries.append(
                        "Vorschlag der KI, nicht geprüft – nicht automatisch angelegt. "
                        "Den Befehl findest du im Protokoll, wenn du Server-Rechte hast."
                    )
                outcome = "denied"
                continue

            host_id, container = target
            if before_propose is not None:
                await before_propose()
            command = build_restart_command(container)
            tag = cause_tag_for_command(command, host_id, causes or {})
            reason = f"Container {container} neu starten nach Absturz"
            if tag:
                reason = f"[{tag}] {reason}"
            decision = await ctx.actions.propose(
                ActionRequest(
                    action_type="shell.exec",
                    host_ref=host.id,
                    payload={"command": command},
                    risk=Risk.MEDIUM if target in (medium_targets or set()) else Risk.HIGH,
                    proposed_by=Actor.ai(model=self._ai.model),
                    reason=reason,
                    correlation_id=correlation_id,
                )
            )
            # Der Befehl steht nur in der Aktion selbst (dort sieht ihn, wer ihn ausfuehren oder
            # bestaetigen darf). Die Zusammenfassung landet in Meldung, Vorfalls-Liste und
            # Protokoll, die auch Leute mit reinem Leserecht sehen -- ohne Befehl.
            summaries.append(f"Vorschlag ({decision.status.value}) auf {host.display_name}")
            outcome = "proposed"
            action_id = decision.action_id
        return summaries, outcome, action_id

    @staticmethod
    async def _batch_host_ids(ctx: ExtensionContext, targets: set[tuple[str, str]]) -> dict[str, str]:
        """Kleinbuchstaben-Servername -> Host-ID fuer die Server der Ziele (nur diese Server
        kann ein KI-Vorschlag ueberhaupt treffen)."""
        result: dict[str, str] = {}
        for host_id in sorted({h for h, _ in targets}):
            host = await ctx.hosts.get(host_id)
            if host is not None:
                result[host.name.lower()] = host.id
        return result
