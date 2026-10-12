"""Fingerabdruck des Zertifikats je Verbindung: merken, prüfen und nach einem Wechsel neu bestätigen."""

from __future__ import annotations

import ssl
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from fastapi.responses import JSONResponse
from nodvard_sdk import Actor, PeerCertificate, same_target, sha256_fingerprint
from nodvard_sdk.errors import PermissionDenied
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

# Mit gemerktem Fingerabdruck spricht die Verbindung nur mit dem Server, der genau dieses Zertifikat zeigt: bei einem
# anderen scheitert schon der TLS-Handshake, bevor das Token hinausgeht. Das ersetzt "Selbstsigniertes Zertifikat
# erlauben", das jede Prüfung abschaltet (bleibt für ältere Einstellungen, gilt aber nicht neben einem Fingerabdruck).
FIELD = "tls_fingerprint_sha256"
CHANGED_CODE = "certificate_changed"
INVALID_CODE = "certificate_invalid"
AUDIT_ACTION = "proxmox.certificate_confirmed"
READ_TIMEOUT_S = 10.0
MAX_INPUT = 200

INVALID_TEXT = (
    "Der eingetragene Fingerabdruck des Zertifikats ist ungültig (64 Zeichen aus 0–9 und A–F, gern mit "
    "Doppelpunkten). Bis er stimmt, verbindet sich Nodvard Deck nicht mit diesem Server."
)
MISMATCH_TEXT = (
    "Der Server zeigt gerade ein anderes Zertifikat als das, das du bestätigen wolltest. Vergleiche den Fingerabdruck "
    "noch einmal in Proxmox (Knoten → System → Zertifikate)."
)


def normalize_fingerprint(value: Any) -> str | None:
    """`None` für "nicht eingetragen" (fehlt, leer), sonst `AB:CD:...` (32 Paare, wie Proxmox sie zeigt); Groß- und
    Kleinschreibung und Doppelpunkte sind egal. `ValueError`, wenn es kein SHA-256-Fingerabdruck ist."""
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > MAX_INPUT:
        raise ValueError(INVALID_TEXT)
    if not value.strip():
        return None
    try:
        return sha256_fingerprint(value)
    except ValueError:
        raise ValueError(INVALID_TEXT) from None


def connection_pin(conn: dict[str, Any]) -> tuple[str | None, bool]:
    """(Fingerabdruck oder `None`, ob der Eintrag ungültig ist). Ungültig heißt gesperrt, nie still "nicht prüfen"."""
    try:
        return normalize_fingerprint(conn.get(FIELD)), False
    except ValueError:
        return None, True


async def read_certificate(ctx: Any, base_url: str) -> PeerCertificate:
    """Nur der TLS-Handshake (`ctx.http.peer_certificate`), es wird nichts gesendet. Fehler als `HTTPException` mit
    festem Satz, nie mit dem Text der Ausnahme (der Interna enthalten kann)."""
    problem: str | None = None
    cert: PeerCertificate | None = None
    try:
        cert = await ctx.http.peer_certificate(base_url, timeout_s=READ_TIMEOUT_S)
    except PermissionDenied as exc:
        problem = str(exc)  # eigener Satz des Kerns (welche Berechtigung fehlt)
    except TimeoutError:
        problem = "Der Server antwortet nicht (Zeitüberschreitung). Prüfe Adresse und Port."
    except ssl.SSLError:  # vor ValueError: ein Zertifikatsfehler ist beides
        problem = "Der Server spricht an dieser Adresse kein HTTPS. Prüfe Adresse und Port (meist 8006)."
    except ValueError:
        problem = "Für die eingetragene Adresse gibt es kein Zertifikat (nur https:// mit Rechner)."
    except OSError:
        problem = "Der Server ist nicht erreichbar. Läuft er, und stimmen Adresse und Port?"
    except Exception as exc:  # noqa: BLE001 - feste Sätze, nie der Text
        ctx.logger.info("Zertifikat nicht lesbar (%s)", type(exc).__name__)
        problem = "Das Zertifikat des Servers ließ sich nicht lesen."
    if problem is not None or cert is None:
        raise HTTPException(status_code=http_status.HTTP_502_BAD_GATEWAY, detail=problem)
    return cert


def _certificate_out(cert: PeerCertificate, stored: str | None, *, invalid: bool) -> dict[str, Any]:
    return {
        "sha256": cert.sha256, "trusted": cert.trusted, "subject": cert.subject, "issuer": cert.issuer,
        "not_after": cert.not_after, "stored": stored, "stored_invalid": invalid,
        "matches": stored is not None and stored == cert.sha256,
    }


class CertificateConfirmIn(BaseModel):
    sha256: str = Field(max_length=MAX_INPUT)
    """Der Fingerabdruck, den die Person mit dem beim Server verglichen hat."""


def build_certificate_router(ctx: ExtensionContext) -> APIRouter:
    """`GET` und `POST /connections/{name}/certificate`, mit derselben Berechtigung wie die übrigen Verbindungs-Routen
    (`secrets.write`)."""
    router = APIRouter()

    async def _connection(name: str) -> dict[str, Any]:
        settings = await ctx.settings.get()
        for conn in settings.get("connections") or []:
            if isinstance(conn, dict) and conn.get("name") == name:
                return conn
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=f"Verbindung '{name}' existiert nicht.")

    @router.get("/connections/{name}/certificate")
    async def show_certificate(name: str) -> dict[str, Any]:
        """Das Zertifikat, das der Server gerade zeigt, neben dem gemerkten Fingerabdruck."""
        conn = await _connection(name)
        stored, invalid = connection_pin(conn)
        cert = await read_certificate(ctx, str(conn.get("base_url") or ""))
        return _certificate_out(cert, stored, invalid=invalid)

    @router.post("/connections/{name}/certificate")
    async def confirm_certificate(
        name: str, payload: CertificateConfirmIn, actor: Actor = Depends(ctx.api.current_actor),  # noqa: B008
    ) -> Any:
        """Merkt den Fingerabdruck, aber nur, wenn er genau dem Zertifikat entspricht, das der Server jetzt zeigt (sonst
        409 mit dem aktuellen Wert). Schaltet dabei "Selbstsigniertes Zertifikat erlauben" aus."""
        try:
            wanted = normalize_fingerprint(payload.sha256)
        except ValueError as exc:
            raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
        if wanted is None:
            raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=INVALID_TEXT)
        conn = await _connection(name)
        base_url = str(conn.get("base_url") or "")
        cert = await read_certificate(ctx, base_url)
        # Gemerkt wird nur, was der Server in diesem Moment wirklich zeigt, nie ein beliebig übergebener Wert.
        if cert.sha256 != wanted:
            return JSONResponse(
                status_code=http_status.HTTP_409_CONFLICT,
                content={"detail": MISMATCH_TEXT, "code": "certificate_mismatch", "sha256": cert.sha256},
            )
        # Erst jetzt speichern, und nur, wenn die Verbindung in der Zwischenzeit nicht auf einen anderen Server zeigt.
        settings = await ctx.settings.get()
        connections = [dict(c) for c in settings.get("connections") or [] if isinstance(c, dict)]
        current = next((c for c in connections if c.get("name") == name), None)
        if current is None or not same_target(str(current.get("base_url") or ""), base_url):
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail="Die Verbindung hat sich in der Zwischenzeit geändert. Lade die Seite neu und prüfe noch einmal.",
            )
        old = current.get(FIELD)
        current.update({FIELD: cert.sha256, "tls_insecure_skip_verify": False})
        await ctx.settings.set({**settings, "connections": connections})
        try:
            await ctx.audit.log(
                action="proxmox.certificate_confirmed", outcome="success", target_type="extension", target_id=ctx.ext_id,
                detail={
                    "connection": name, "address": base_url, "fingerprint": cert.sha256,
                    "previous_fingerprint": old if isinstance(old, str) and old else None,
                    "trusted": cert.trusted,
                },
                actor=actor,
            )
        except Exception as exc:  # noqa: BLE001 - der Fingerabdruck ist schon gespeichert
            ctx.logger.warning("Zertifikat bestätigt, Protokolleintrag fehlgeschlagen (%s)", type(exc).__name__)
        stored, invalid = connection_pin(current)
        return _certificate_out(cert, stored, invalid=invalid)

    return router
