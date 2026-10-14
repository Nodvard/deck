"""Verbindungen, die die Erweiterung "Proxmox VE" beim Verbinden mit Passwort für "Proxmox-Backups" anlegt."""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from nodvard_sdk import same_target

from .certificate import FIELD as FINGERPRINT_FIELD
from .certificate import connection_pin, normalize_fingerprint
from .config import TOKEN_LABEL

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext
    from pydantic import SecretStr

AUDIT_ACTION = "backups.connection_received"
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
"""Wie das Feld "Kurzname" im Einstellungsformular (`settings.schema.json`)."""
TOKEN_ID_PATTERN = re.compile(r"^[^\s@!:]{1,64}@[A-Za-z0-9_.-]{1,32}![A-Za-z][A-Za-z0-9._-]{0,63}$")
"""`benutzer@bereich!tokenname` (wie im Formular, mit den Zeichen, die Proxmox erlaubt)."""


class ConnectionRefused(Exception):
    """Die Verbindung passt nicht zu den eingetragenen (fester deutscher Satz)."""


def _valid_base_url(base_url: str) -> bool:
    """`https://rechner[:port]` -- ohne Zugangsdaten, Pfad, Anfrage, Fragment oder Leer-/Steuerzeichen."""
    # Strenger als eine von Hand eingetragene Adresse, weil sie von einer anderen Erweiterung kommt.
    text = str(base_url or "")
    if not text or len(text) > 300 or any(unicodedata.category(ch).startswith(("C", "Z")) for ch in text):
        return False
    try:
        parts = urlsplit(text)
        _port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https" and bool(parts.hostname) and not parts.username and not parts.password
        and parts.path in ("", "/") and not parts.query and not parts.fragment
    )


# "Proxmox VE" findet den Empfänger über `ctx.capabilities.query()` und übergibt Adresse, Token-ID und Geheimnis in
# einem Aufruf (Erweiterungen importieren einander nicht). Das Geheimnis geht nie über den Ereignis-Bus, nie zurück und
# nie ins Protokoll. Angenommen wird nur, was auch von Hand eingetragen werden könnte, und strenger.
class ProxmoxConnectionReceiver:
    connection_kind = "proxmox"

    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx

    async def _connections(self) -> list[dict[str, Any]]:
        settings = await self._ctx.settings.get()
        return [dict(c) for c in settings.get("connections") or [] if isinstance(c, dict)]

    @staticmethod
    def _problem(connections: list[dict[str, Any]], name: str, base_url: str) -> str | None:
        if not _valid_base_url(base_url):
            return "Für Proxmox-Backups ist die Adresse ungültig (nur https:// mit Rechner, ohne Pfad)."
        if any(same_target(str(c.get("base_url") or ""), base_url) for c in connections):
            return None  # Diese Verbindung bekommt den neuen Token.
        if not NAME_PATTERN.fullmatch(name):
            return "Für Proxmox-Backups ist der Kurzname ungültig."
        if any(c.get("name") == name for c in connections):
            return f"In Proxmox-Backups gehört der Kurzname „{name}“ schon zu einem anderen Server."
        return None

    async def connection_conflict(self, *, name: str, base_url: str) -> str | None:
        return self._problem(await self._connections(), name, base_url)

    async def receive_connection(
        self, *, name: str, base_url: str, token_id: str, token_secret: SecretStr, tls_insecure_skip_verify: bool,
        pinned_sha256: str | None = None,
    ) -> dict[str, Any]:
        settings = await self._ctx.settings.get()
        before = [dict(c) for c in settings.get("connections") or [] if isinstance(c, dict)]
        connections = [dict(c) for c in before]
        problem = self._problem(connections, name, base_url)
        if problem is None and not TOKEN_ID_PATTERN.fullmatch(str(token_id or "")):
            problem = "Für Proxmox-Backups ist die Token-ID ungültig."
        pin: str | None = None
        if problem is None and pinned_sha256 is not None:
            try:
                pin = normalize_fingerprint(pinned_sha256)
            except ValueError:
                problem = "Für Proxmox-Backups ist der Fingerabdruck des Zertifikats ungültig."
        if problem is not None:
            raise ConnectionRefused(problem)
        existing = next((c for c in connections if same_target(str(c.get("base_url") or ""), base_url)), None)
        replaced: str | None = None
        replaced_pin: str | None = None
        # Ein Fingerabdruck ersetzt "Selbstsigniertes Zertifikat erlauben" (der Pin ist strenger).
        insecure = bool(tls_insecure_skip_verify) and pin is None
        enabled = True
        if existing is None:
            entry: dict[str, Any] = {
                "name": name, "base_url": base_url, "token_id": token_id, "tls_insecure_skip_verify": insecure,
                "enabled": True,
            }
            if pin is not None:
                entry[FINGERPRINT_FIELD] = pin
            connections.append(entry)
        else:
            # Die vorhandene Verbindung bekommt nur den neuen Token und behält Namen, "aus" und Zertifikatsprüfung.
            name = str(existing.get("name"))
            replaced = str(existing.get("token_id") or "") or None
            old_pin, old_pin_invalid = connection_pin(existing)
            if pin is not None:
                if old_pin is not None and old_pin != pin:
                    replaced_pin = old_pin
                existing[FINGERPRINT_FIELD] = pin  # ausdrücklich bestätigt: ersetzt einen alten
            elif old_pin is not None or old_pin_invalid:
                insecure = False  # ein gemerkter (auch ein kaputter, der sperrt) bleibt, nie "nicht prüfen"
            else:
                # Nie still abschwächen: prüft die Verbindung das Zertifikat, bleibt es so. Auf "Selbstsigniertes
                # Zertifikat erlauben" umzustellen soll nur die Person entscheiden.
                insecure = insecure and bool(existing.get("tls_insecure_skip_verify"))
            # Eine ausgeschaltete Verbindung bleibt aus; fehlt das Feld (ältere Einträge), gilt sie als an.
            enabled = existing.get("enabled") is not False
            existing.update({"token_id": token_id, "tls_insecure_skip_verify": insecure, "enabled": enabled})
        label = f"{TOKEN_LABEL}:{name}"
        keep_secret = existing is not None and await self._ctx.secrets.exists(label)
        if not keep_secret:
            # Ein Geheimnis unter diesem Namen gehört dann zu einer entfernten Verbindung und muss weg, bevor sich
            # etwas ändert. Ein fremdes (`SecretNotOwned`) bricht hier ab, ohne Änderung.
            await self._ctx.secrets.delete(label)
        # Scheitert etwas (auch `SecretNotOwned` beim Ersetzen eines fremden Geheimnisses), kommen die Einstellungen
        # auf den alten Stand zurück.
        failure: BaseException | None = None
        try:
            await self._ctx.settings.set({**settings, "connections": connections})
            if keep_secret:
                await self._ctx.secrets.replace(label=label, value=token_secret.get_secret_value())
            else:
                await self._ctx.secrets.create(
                    label=label, kind="generic", value=token_secret.get_secret_value(),
                    description="API-Token (beim Verbinden von Proxmox VE mit Passwort angelegt)",
                )
        except Exception as exc:  # noqa: BLE001 - erst den alten Stand zurück, dann melden
            failure = exc
        if failure is not None:
            try:
                await self._ctx.settings.set({**settings, "connections": before})
            except Exception as exc:  # noqa: BLE001 - der ursprüngliche Fehler zählt
                self._ctx.logger.warning("Backups: alter Stand nicht wiederhergestellt (%s)", type(exc).__name__)
            raise failure
        saved = existing if existing is not None else connections[-1]
        result = {
            "name": name, "replaced_token_id": replaced if replaced != token_id else None,
            "tls_insecure_skip_verify": insecure, "enabled": enabled,
            "tls_fingerprint_sha256": connection_pin(saved)[0],
            "replaced_fingerprint": replaced_pin,
        }
        await self._audit(name=name, base_url=base_url, token_id=token_id, result=result)
        return result

    async def _audit(self, *, name: str, base_url: str, token_id: str, result: dict[str, Any]) -> None:
        try:
            await self._ctx.audit.log(
                action=AUDIT_ACTION, outcome="success", target_type="extension", target_id=self._ctx.ext_id,
                detail={
                    "connection": name, "address": base_url, "token_id": token_id,
                    "replaced_token_id": result["replaced_token_id"], "from": "proxmox",
                    "fingerprint": result["tls_fingerprint_sha256"],
                    "previous_fingerprint": result["replaced_fingerprint"],
                },
            )
        except Exception as exc:  # noqa: BLE001 - das Protokoll darf die schon gespeicherte Verbindung nicht kippen
            self._ctx.logger.warning("Backups: Protokolleintrag fehlgeschlagen (%s)", type(exc).__name__)

    async def received_token_ids(self) -> list[str]:
        return [str(c["token_id"]) for c in await self._connections() if c.get("token_id")]
