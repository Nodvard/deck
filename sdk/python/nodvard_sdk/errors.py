"""Fehlertypen des SDK."""

from __future__ import annotations

import functools
import ssl
import typing
from typing import Any

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

    def __reduce__(self) -> tuple[Any, ...]:
        # `copy`/`pickle` bauen eine Ausnahme sonst aus `args` (dem fertigen Satz) neu und scheitern an der
        # eigenen Signatur (etwa beim Weiterreichen zwischen Prozessen oder in Test-Werkzeugen).
        return (self.__class__, (self.ext_id, self.permission), dict(self.__dict__))


class SecretNotOwned(PermissionDenied):
    """`ctx.secrets.replace()` oder `ctx.secrets.delete()` auf ein Geheimnis, das nicht dieser Erweiterung gehoert:
    angelegt von einer anderen Erweiterung, vom Kern oder von Hand ueber die allgemeine Tresor-Verwaltung. Lesen
    bleibt mit `secrets.read:<label>` erlaubt (etwa ein gemeinsam genutzter Zugang); ersetzen oder loeschen darf eine
    Erweiterung nur ihre eigenen. Als eigene gelten Geheimnisse, die sie unter ihrer Kennung oder einer frueheren
    (`legacy_ids`) angelegt hat, ueber `ctx.secrets.create()` oder ueber ihre Einstellungsseite.

    `login_secret=True`: das Geheimnis gehoert zur Zwei-Faktor-Anmeldung eines Kontos. Das darf eine Erweiterung
    auch nicht lesen (`get_handle()`). Aeltere Kerne kennen den Fehler nicht und pruefen das nicht."""

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


class NotificationSkipped(NodvardError):
    """Ein Benachrichtigungskanal (`NotificationChannel.send`) gibt diese Meldung bewusst nicht weiter, zum
    Beispiel weil sie unter der dort eingestellten Mindest-Stufe liegt. Kein Fehler: Der Kern verbucht die
    Zustellung als `skipped` (nicht `failed`, nicht `sent`), `NotifyResult.skipped` sagt es dem Absender, und
    `raise_on_failure` wirft deswegen nicht. Ältere Kerne haben die Klasse nicht, dort scheitert schon der Import.
    Eine Erweiterung, die auch dort laufen soll, importiert sie in `try/except ImportError` und kehrt ohne sie
    einfach zurück (ältere Kerne verbuchen das als `sent`), siehe docs/02-EXTENSION-API.md §2."""


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


class CertificateMismatch(NodvardError, ssl.SSLCertVerificationError):
    """Ein Aufruf mit `pinned_sha256` (`ctx.http`) traf auf ein anderes Zertifikat als das bestaetigte.

    Entsteht im TLS-Handshake, bevor ein Byte der Anfrage (Kopf, Zugangsdaten, Koerper) gesendet wurde. Die
    HTTP-Schicht reicht sie meist eingepackt weiter (bei `httpx` als `__cause__`/`__context__` eines
    `ConnectError`); wer sie erkennen will, sucht in der Kette danach. `expected`/`actual`: die Fingerabdruecke
    (`AB:CD:...`), `actual` ist leer, wenn der Server gar kein Zertifikat gezeigt hat. Der Text ist ein fester
    deutscher Satz ohne Angaben vom Server.

    Ist zugleich ein `ssl.SSLCertVerificationError` (und damit ein `ssl.SSLError`): nur so behandeln die
    TLS-Schichten von asyncio und anyio sie als gescheiterten Handshake."""

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


# Alter Name vor der Umbenennung: dasselbe Objekt, mit DeprecationWarning (siehe _legacy.py).
_LEGACY_NAMES = {"LatticeError": "NodvardError"}
__getattr__ = legacy_getattr(__name__, _LEGACY_NAMES, globals())
if typing.TYPE_CHECKING:
    LatticeError = NodvardError
