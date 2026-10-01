"""Fehlertypen des SDK."""

from __future__ import annotations

import typing

from ._legacy import legacy_getattr


class NodvardError(Exception):
    """Basis für alle SDK-Fehler. (Bis zur Umbenennung `LatticeError`: der alte Name bleibt als Alias.)"""


class PermissionDenied(NodvardError):
    """Die Extension hat diese Permission nicht im Manifest — oder sie wurde nicht gewährt."""

    def __init__(self, ext_id: str, permission: str) -> None:
        super().__init__(
            f"Extension '{ext_id}' hat die Permission '{permission}' nicht. "
            f"Ins Manifest aufnehmen und vom Admin bestätigen lassen."
        )
        self.ext_id = ext_id
        self.permission = permission


class ActionBlocked(NodvardError):
    """Das Gate hat die Aktion abgelehnt."""

    def __init__(self, rule: str, detail: str | None = None) -> None:
        super().__init__(
            f"Aktion abgelehnt durch Regel '{rule}'" + (f": {detail}" if detail else "")
        )
        self.rule = rule
        self.detail = detail


class SecretUnavailable(NodvardError):
    pass


class HostUnreachable(NodvardError):
    pass


class NotificationNotDelivered(NodvardError):
    """`ctx.notify.send(..., raise_on_failure=True)`: es gab Kanäle, aber keiner hat die
    Benachrichtigung zugestellt. Sie steht trotzdem im In-App-Verlauf; `failures` sind die
    fehlgeschlagenen Kanäle als (Kanal-ID, Fehlertext)."""

    def __init__(self, failures: list[tuple[str, str]]) -> None:
        super().__init__(
            "Benachrichtigung nicht zugestellt: "
            + "; ".join(f"{channel}: {error}" if error else channel for channel, error in failures)
        )
        self.failures = failures


class IncompatibleExtension(NodvardError):
    def __init__(self, ext_id: str, required: str, current: str) -> None:
        super().__init__(
            f"Extension '{ext_id}' verlangt SDK {required}, der Kern bietet {current}."
        )
        self.ext_id = ext_id
        self.required = required
        self.current = current


class InvalidRegistration(NodvardError):
    """Eine Registrierung verletzt eine Regel der Schnittstelle."""


# Alter Name vor der Umbenennung: dasselbe Objekt, mit DeprecationWarning (siehe _legacy.py).
_LEGACY_NAMES = {"LatticeError": "NodvardError"}
__getattr__ = legacy_getattr(__name__, _LEGACY_NAMES, globals())
if typing.TYPE_CHECKING:
    LatticeError = NodvardError
