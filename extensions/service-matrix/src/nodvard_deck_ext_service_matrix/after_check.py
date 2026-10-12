"""Prüfung nach dem Starten von Containern: laufen sie wirklich, und bleiben sie laufen?

Gemeinsam für Start, Neustart, Stack-Neustart (`capabilities`) und Image-Update samt Rücksprung (`applier`). Nur lesend.
"""

from __future__ import annotations

import asyncio
import shlex
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from . import image_apply as ia
from .capabilities import COMPOSE_PROJECT_RE, CONTAINER_NAME_RE

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

CHECK_TIMEOUT_S = 60
"""So lange wartet die Prüfung nach "Container starten/neu starten" höchstens auf ein klares Bild."""
CHECK_INTERVAL_S = 3
CHECK_STABLE_S = 15
CHECK_CALL_TIMEOUT_S = 20
_ID_RE = CONTAINER_NAME_RE  # volle IDs (64 hex) passen auch auf die Namensregel

Read = Callable[[], Awaitable["ia.Snapshot | None"]]
Judge = Callable[["ia.Snapshot", bool], "ia.Verdict | None"]
LoopText = Callable[["ia.Snapshot", "ia.Snapshot"], str]
ONE_RESTART_NOTE = " Dabei gab es einen Neustart (vielleicht war eine Abhängigkeit noch nicht bereit)."


# `docker start`/`restart` melden Erfolg, sobald der Prozess läuft; ein Absturz Sekunden später fiele nicht auf. Deshalb
# liest `wait_settled` den Zustand mehrmals:
# - Ohne Healthcheck reicht ein einzelnes "running" nicht: ein Absturz-Kreislauf steht zwischen zwei Neustarts kurz
#   auf "running". Ruhig heißt: zwei Lesungen mindestens `stable_s` auseinander, ohne neuen Start dazwischen
#   (`StartedAt`, `RestartCount`).
# - Mit Healthcheck entscheidet "healthy"/"unhealthy"; bleibt er bis zum Ende auf "starting", ist das ein Erfolg mit Hinweis.
async def wait_settled(
    read: Read, judge: Judge, *, timeout_s: float, interval_s: float, stable_s: float,
    restarts_allowed: int = 0, loop_text: LoopText | None = None,
) -> tuple[ia.Verdict, ia.Snapshot | None]:
    """Liest den Zustand, bis `judge(snap, final)` entschieden hat; mit `final=True` (Zeit um) muss es entscheiden.

    Ein Lesefehler zählt nicht; meldet sich der Host bis zum Ende nicht, ist der Zustand "nicht prüfbar".
    """
    deadline = time.monotonic() + timeout_s
    snap: ia.Snapshot | None = None
    first_ok: tuple[ia.Snapshot, float] | None = None
    restarts = 0
    while True:
        try:
            snap = await read()
        except Exception:  # noqa: BLE001 - kurzer Aussetzer: weiter versuchen
            snap = None
        now = time.monotonic()
        final = now >= deadline
        if snap is not None:
            if first_ok is not None and ia.restarted_since(first_ok[0], snap):
                # So viele Neustarts werden hingenommen (`restarts_allowed`), erst der nächste ist ein Absturz-Kreislauf.
                # Nach "Container starten/neu starten" ist einer erlaubt, das Image-Update ist strenger (0).
                if restarts >= restarts_allowed or final:
                    text = loop_text(first_ok[0], snap) if loop_text else ia.CRASH_LOOP_TEXT
                    return ia.Verdict(False, text, crashed=True), snap
                restarts += 1
                first_ok = None  # einmal neu gestartet: von hier an noch einmal ruhig beobachten
            verdict = judge(snap, final)
            if verdict is not None and verdict.ok and restarts:
                verdict = replace(verdict, text=verdict.text + ONE_RESTART_NOTE)
            if verdict is not None:
                if not (verdict.ok and ia.needs_stability_check(snap)):
                    return verdict, snap
                if first_ok is None:
                    first_ok = (snap, now)  # erste ruhige Lesung: mindestens eine zweite muss folgen
                    if final:
                        return verdict, snap
                elif now - first_ok[1] >= stable_s or final:
                    return verdict, snap
            else:
                first_ok = None  # zwischendurch nicht mehr "ok": die Beobachtung beginnt neu
        elif final:
            return ia.Verdict(False, "Zustand des Containers nicht prüfbar (Host antwortet nicht)."), None
        await asyncio.sleep(interval_s)


def cmd_state(refs: list[str]) -> str:
    """Zustand dieser Container (Namen oder volle IDs), eine JSON-Zeile je Container (Format wie beim Image-Update)."""
    if not refs or not all(_ID_RE.match(r) for r in refs):
        raise ValueError(f"Ungültige Container: {refs!r}")
    return f"docker container inspect --format {shlex.quote(ia.VERIFY_FORMAT)} " + " ".join(shlex.quote(r) for r in refs)


def cmd_stack_running(project: str) -> str:
    """Volle IDs der laufenden Container eines Compose-Projekts (vor dem Neustart gelesen)."""
    if not COMPOSE_PROJECT_RE.match(project):
        raise ValueError(f"Ungültiger Projektname: {project!r}")
    label = shlex.quote(f"label={ia.LABEL_PROJECT}={project}")
    # Ohne `compose run`-Container: die fasst `compose restart` nicht an, sie gehören nicht zum Stack.
    oneoff = shlex.quote(f"label={ia.LABEL_ONEOFF}=False")
    return f"docker ps -q --no-trunc --filter {label} --filter {oneoff}"


def judge_running(snap: ia.Snapshot, final: bool, *, expected: int) -> ia.Verdict | None:
    """Laufen alle `expected` Container? `None`: noch nicht entschieden.

    Wie `ia.judge_verify`, nur ohne den Vergleich der Images: es wurde ja nur gestartet.
    """
    members = snap.members

    def who(m: ia.Member) -> str:
        return "" if expected == 1 else f"{m.name}: "

    for m in members:
        if m.status == "dead" or (m.status == "exited" and m.exit_code != 0):
            code = f", Exit-Code {m.exit_code}" if m.exit_code is not None else ""
            return ia.Verdict(False, f"{who(m)}Container läuft nicht (beendet{code}). Logs über den Knopf „Logs“.", crashed=True)
    if len(members) < expected:
        if not final:
            return None
        return ia.Verdict(False, "Container nicht mehr vorhanden." if expected == 1 else
                          f"{expected - len(members)} von {expected} Containern nicht mehr vorhanden.", crashed=True)
    # Exit-Code 0 ist fertig, nicht kaputt: einmalige Aufgaben wie Migrationen oder certbot.
    done = [m for m in members if m.status == "exited"]
    live = [m for m in members if m.status != "exited"]
    settled = all(m.status == "running" and m.health in (None, "healthy") for m in live)
    if not settled:
        if not final:
            return None
        # "ungesund" gilt erst zum Ende, ein Healthcheck kann sich erholen.
        unhealthy = [m for m in live if m.health == "unhealthy"]
        if unhealthy:
            return ia.Verdict(False, f"{who(unhealthy[0])}Container meldet „ungesund“. Logs über den Knopf „Logs“.")
        restarting = [m for m in live if m.status == "restarting"]
        if restarting:
            return ia.Verdict(False, who(restarting[0]) + ia.CRASH_LOOP_TEXT, crashed=True)
        other = [m for m in live if m.status != "running"]
        if other:
            return ia.Verdict(False, f"{who(other[0])}Container läuft nicht (Zustand: {other[0].status}).")
        return ia.Verdict(True, "Container läuft, der Healthcheck meldet aber noch „startet“ – bitte später nachsehen.",
                          health="starting")
    healthy = any(m.health == "healthy" for m in live)
    health = "healthy" if healthy else None
    if expected == 1:
        if done:
            return ia.Verdict(True, "Container hat sich ohne Fehler beendet (Exit-Code 0). Bei einem Container für "
                              "einmalige Aufgaben ist das normal; soll er dauerhaft laufen, zeigen die Logs den Grund.")
        return ia.Verdict(True, "Container läuft" + (" und ist gesund." if healthy else " stabil."), health=health)
    if done:
        names = ", ".join(m.name for m in done)
        return ia.Verdict(True, f"{len(live)} von {expected} Containern laufen" + (" (gesund)" if healthy else " stabil")
                          + f"; ohne Fehler beendet (Exit-Code 0): {names}.", health=health)
    return ia.Verdict(True, f"Alle {expected} Container laufen" + (" (gesund)." if healthy else " stabil."), health=health)


def _loop_text(before: ia.Snapshot, after: ia.Snapshot) -> str:
    """Absturz-Kreislauf mit dem Namen des Containers, der wieder neu gestartet ist (bei mehreren)."""
    old = {m.name: m for m in before.members}
    names = [m.name for m in after.members if m.name not in old or ia.restarted_since(
        ia.Snapshot((old[m.name],), None), ia.Snapshot((m,), None))]
    if len(after.members) > 1 and names:
        return f"{names[0]}: {ia.CRASH_LOOP_TEXT}"
    return ia.CRASH_LOOP_TEXT


async def check_running(
    ctx: ExtensionContext, host: Any, refs: list[str], *, timeout_s: float | None = None,
) -> tuple[ia.Verdict, ia.Snapshot | None]:
    """Prüft nach einem Start, ob diese Container laufen und laufen bleiben."""

    async def read() -> ia.Snapshot:
        res = await ctx.exec.run(host, cmd_state(refs), timeout_s=CHECK_CALL_TIMEOUT_S)
        out = res.stdout or ""
        # Fehlt ein Container, schreibt Docker das nach stderr und endet mit 1 -- die anderen stehen trotzdem da. Ein
        # anderer Fehler ohne Ausgabe (Docker antwortet nicht) ist ein Aussetzer, kein "Container weg".
        if res.exit_code != 0 and not out.strip() and "No such" not in (res.stderr or ""):
            raise RuntimeError((res.stderr or "").strip() or f"docker inspect endete mit {res.exit_code}")
        return ia.parse_verify(out)

    return await wait_settled(
        read, lambda snap, final: judge_running(snap, final, expected=len(refs)),
        timeout_s=CHECK_TIMEOUT_S if timeout_s is None else timeout_s, interval_s=CHECK_INTERVAL_S, stable_s=CHECK_STABLE_S,
        restarts_allowed=1, loop_text=_loop_text,
    )


def result_line(verdict: ia.Verdict) -> str:
    """Die Zeile, die in der Aktion steht."""
    return f"Prüfung danach: {verdict.text}"
