"""Verbindungen, die die Erweiterung "Proxmox VE" beim Verbinden mit Passwort fuer "Proxmox-Backups" anlegt
(`nodvard_sdk.ConnectionReceiver`, Art `"proxmox"`).

Die beiden Erweiterungen importieren einander nicht: "Proxmox VE" findet diesen Empfaenger ueber
`ctx.capabilities.query()` (es nennt "backups" in `requires`) und uebergibt Adresse, Token-ID und Token-Geheimnis in
genau einem Aufruf. Eingetragen wird wie im Einstellungsformular dieser Erweiterung: die Verbindung in
`settings.connections`, das Geheimnis im eigenen Tresor-Eintrag `backups-token:<name>`. Nie ueber den Ereignis-Bus,
nie zurueck an den Aufrufer. Jede Uebernahme steht im Protokoll (`backups.connection_received`, nur Name, Adresse und
Token-IDs).

Angenommen wird nur, was auch eine Verbindung von Hand sein koennte, und strenger: `https://` mit Rechner (ohne
Zugangsdaten, Pfad, Anfrage), Token-ID `benutzer@bereich!name`, Kurzname wie im Formular. Gibt es zur Adresse schon
eine Verbindung, bekommt sie den neuen Token und behaelt ihren Namen, ihr "aus" (`enabled: false`) und eine strenge
Zertifikatspruefung: Eine Verbindung, die das Zertifikat prueft (normal oder ueber einen gemerkten Fingerabdruck),
wird nie still auf "Selbstsigniertes Zertifikat erlauben" umgestellt -- das waere eine Abschwaechung, die nur die
Person entscheiden soll (sie sieht es dann an der Verbindung). Ein bestaetigter Fingerabdruck (`pinned_sha256`) wird
gemerkt und ersetzt dabei einen alten: Der Anbieter hat ihn die Person beim Verbinden ausdruecklich bestaetigen
lassen. Ohne neuen Fingerabdruck bleibt ein gemerkter stehen. Ihr Geheimnis wird in einem Schritt ersetzt (`ctx.secrets.replace`). Ein fremdes Geheimnis unter dem
Label (nicht von dieser Erweiterung, etwa von Hand im Tresor) wird nie angefasst: bei einer neuen Verbindung faellt
das vor jeder Aenderung auf, bei einer vorhandenen beim Ersetzen -- dann kommen die Einstellungen auf den alten Stand
zurueck (`SecretNotOwned`). Scheitert sonst etwas, ebenso.
"""

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
            name = str(existing.get("name"))
            replaced = str(existing.get("token_id") or "") or None
            old_pin, old_pin_invalid = connection_pin(existing)
            if pin is not None:
                if old_pin is not None and old_pin != pin:
                    replaced_pin = old_pin
                existing[FINGERPRINT_FIELD] = pin  # ausdruecklich bestaetigt: ersetzt einen alten
            elif old_pin is not None or old_pin_invalid:
                insecure = False  # ein gemerkter (auch ein kaputter, der sperrt) bleibt, nie "nicht pruefen"
            else:
                # Nie still abschwaechen: prueft die Verbindung das Zertifikat, bleibt es so.
                insecure = insecure and bool(existing.get("tls_insecure_skip_verify"))
            # Eine ausgeschaltete Verbindung bleibt aus; fehlt das Feld (aeltere Eintraege), gilt sie als an.
            enabled = existing.get("enabled") is not False
            existing.update({"token_id": token_id, "tls_insecure_skip_verify": insecure, "enabled": enabled})
        label = f"{TOKEN_LABEL}:{name}"
        keep_secret = existing is not None and await self._ctx.secrets.exists(label)
        if not keep_secret:
            # Ein Geheimnis unter diesem Namen gehoert dann zu einer frueher entfernten Verbindung -- weg, bevor sich
            # etwas aendert. Ein fremdes (`SecretNotOwned`) bricht hier ab, ohne Aenderung.
            await self._ctx.secrets.delete(label)
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
        except Exception as exc:  # noqa: BLE001 - erst den alten Stand zurueck, dann melden
            failure = exc
        if failure is not None:
            try:
                await self._ctx.settings.set({**settings, "connections": before})
            except Exception as exc:  # noqa: BLE001 - der urspruengliche Fehler zaehlt
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
