"""Fehlertypen des SDK."""

from __future__ import annotations

import functools
import ssl
import typing
from typing import Any

from ._legacy import legacy_getattr


class NodvardError(Exception):
    """Basis für alle SDK-Fehler (der alte Name `LatticeError` bleibt als veralteter Alias)."""


class PermissionDenied(NodvardError):
    """Die Extension hat diese Permission nicht im Manifest — oder sie wurde nicht gewährt."""

    def __init__(self, ext_id: str, permission: str) -> None:
        super().__init__(
            f"Extension '{ext_id}' hat die Permission '{permission}' nicht. "
            f"Ins Manifest aufnehmen und vom Admin bestätigen lassen."
        )
        self.ext_id = ext_id
        self.permission = permission

    def __reduce__(self) -> tuple[Any, ...]:
        # `copy`/`pickle` bauen eine Ausnahme sonst aus `args` (dem fertigen Satz) neu und scheitern an der
        # eigenen Signatur (etwa beim Weiterreichen zwischen Prozessen oder in Test-Werkzeugen).
        return (self.__class__, (self.ext_id, self.permission), dict(self.__dict__))


# Eigene Geheimnisse sind die, die die Erweiterung unter ihrer Kennung oder einer alten Kennung (`legacy_ids`) angelegt
# hat, über `ctx.secrets.create()` oder ihre Einstellungsseite. Fremde stammen von einer anderen Erweiterung, vom Kern
# oder von Hand aus der allgemeinen Tresor-Verwaltung.
class SecretNotOwned(PermissionDenied):
    """`ctx.secrets.replace()`/`delete()` auf ein Geheimnis, das nicht dieser Erweiterung gehört (Lesen bleibt mit
    `secrets.read:<label>` erlaubt). `login_secret=True`: Geheimnis der Zwei-Faktor-Anmeldung eines Kontos, das auch
    `get_handle()` nicht liefert; ältere Kerne kennen den Fehler nicht und prüfen das nicht."""

    def __init__(self, ext_id: str, label: str, *, login_secret: bool = False) -> None:
        if login_secret:
            text = (
                f"Das Geheimnis „{label}“ gehört zur Zwei-Faktor-Anmeldung eines Kontos. Eine Erweiterung darf es "
                "weder lesen noch ändern oder löschen."
            )
        else:
            text = (
                f"Das Geheimnis „{label}“ gehört nicht der Erweiterung „{ext_id}“. Ersetzen oder löschen darf sie nur "
                "ihre eigenen."
            )
        NodvardError.__init__(self, text)
        self.ext_id = ext_id
        self.permission = f"secrets.read:{label}"
        self.label = label
        self.login_secret = login_secret

    def __reduce__(self) -> tuple[Any, ...]:
        return (
            functools.partial(self.__class__, login_secret=self.login_secret), (self.ext_id, self.label),
            dict(self.__dict__),
        )


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


# Ältere Kerne haben die Klasse nicht, dort scheitert schon der Import. Eine Erweiterung, die auch dort laufen soll,
# importiert sie in `try/except ImportError` und kehrt ohne sie einfach zurück (ältere Kerne verbuchen das als `sent`).
class NotificationSkipped(NodvardError):
    """Ein Kanal (`NotificationChannel.send`) gibt die Meldung bewusst nicht weiter, etwa unter seiner Mindest-Stufe.
    Kein Fehler: Der Kern verbucht `skipped` (nicht `failed`, nicht `sent`), `NotifyResult.skipped` sagt es dem
    Absender, und `raise_on_failure` wirft deswegen nicht."""


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


# Entsteht im TLS-Handshake, bevor ein Byte der Anfrage (Kopf, Zugangsdaten, Körper) gesendet wurde. Die HTTP-Schicht
# reicht sie meist eingepackt weiter (bei `httpx` als `__cause__`/`__context__` eines `ConnectError`); wer sie erkennen
# will, sucht in der Kette danach. Zugleich ein `ssl.SSLCertVerificationError` (damit ein `ssl.SSLError`): nur so
# behandeln die TLS-Schichten von asyncio und anyio sie als gescheiterten Handshake.
class CertificateMismatch(NodvardError, ssl.SSLCertVerificationError):
    """Ein Aufruf mit `pinned_sha256` (`ctx.http`) traf auf ein anderes Zertifikat als das bestätigte; es wurde vorher
    nichts gesendet. `expected`/`actual`: Fingerabdrücke (`AB:CD:...`), `actual` ist leer, wenn der Server kein
    Zertifikat gezeigt hat. Der Text ist ein fester deutscher Satz ohne Angaben vom Server."""

    _TEXT = "Das Zertifikat des Servers passt nicht zum bestätigten Fingerabdruck. Es wurde nichts gesendet."

    def __init__(self, expected: str, actual: str) -> None:
        super().__init__(self._TEXT)
        self.expected = expected
        self.actual = actual
        self.verify_message = "Fingerabdruck passt nicht"
        self.verify_code = 0

    def __str__(self) -> str:
        # `ssl.SSLError` zeigt ohne `strerror` das Tupel der Argumente.
        return self._TEXT


# Veralteter Name `LatticeError`: dasselbe Objekt, mit DeprecationWarning (siehe _legacy.py).
_LEGACY_NAMES = {"LatticeError": "NodvardError"}
__getattr__ = legacy_getattr(__name__, _LEGACY_NAMES, globals())
if typing.TYPE_CHECKING:
    LatticeError = NodvardError
