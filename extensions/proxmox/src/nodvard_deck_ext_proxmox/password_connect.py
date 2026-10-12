"""Proxmox in einem Schritt verbinden: Adresse und Passwort eines Proxmox-Administrators (meist `root@pam`) EINMAL
eingeben, Nodvard Deck legt den Zugang selbst an und traegt die Verbindung ein.

Was in Proxmox entsteht (idempotent, ein zweiter Lauf legt nichts doppelt an):

- Rolle `NodvardDeck` (bzw. `NodvardDeckRO` fuer "nur ansehen") mit den Rechten aus `PRIVILEGES` -- nur die, die es in
  dieser Proxmox-Version gibt (abgelesen an der eingebauten Rolle `Administrator`). Gibt es die Rolle schon, kommen
  fehlende Rechte dazu (`append`), entfernt wird nichts: eine von Hand erweiterte Rolle bleibt, wie sie ist. Hat sie
  mehr Rechte als noetig, nennen Antwort und Protokoll sie (der Token bekommt sie mit).
- Benutzer `nodvard@pve` ohne Passwort (damit meldet sich niemand an der Oberflaeche an); ist er gesperrt, wird er
  wieder eingeschaltet -- wer neu verbindet, will ihn benutzen.
- API-Token `deck` (gibt es den schon: `deck-2`, `deck-3` ...) mit Privilegientrennung. Ein vorhandener Token wird
  nie geloescht oder ersetzt: Er kann noch woanders stecken (ein Skript, ein zweites Nodvard Deck), und eine laufende
  Verbindung darf nicht kaputtgehen, bevor die neue steht. Welche alten Tokens hier nicht mehr benutzt werden, nennt
  die Antwort (`old_tokens`, mit dem `pveum`-Befehl zum Loeschen); loeschen kann Nodvard Deck sie nicht selbst (siehe
  `old_tokens_of`).
- Berechtigung auf `/` mit der Rolle, fuer den Benutzer UND den Token. Mit Privilegientrennung darf der Token nur,
  was beide haben -- so bekommt er genau die Rolle, auch wenn `nodvard@pve` spaeter (oder schon) mehr darf.
- Ist die Erweiterung "Proxmox-Backups" an (und nicht abgewaehlt), ein eigener Zugang fuer sie mit eigenem Benutzer
  `nodvard-backup@pve`: Rolle `NodvardDeckBackup` (bzw. `NodvardDeckBackupRO`) auf `/`, Token `deck` (`deck-2` ...)
  mit Privilegientrennung, bei "Ansehen und steuern" dazu die Rolle `NodvardDeckBackupStore` NUR auf den Speichern mit
  Inhalt `backup` und der Pool `nodvard-kopien` (Rolle `NodvardDeckRestore` nur dort) fuer das Wiederherstellen als
  Kopie. `nodvard@pve` und seine Tokens bekommen davon nichts.
  "Proxmox-Backups" bekommt die Verbindung ueber `nodvard_sdk.ConnectionReceiver` (die Erweiterungen importieren
  einander nicht, das Geheimnis geht nicht ueber den Ereignis-Bus). Klappt dieser Teil nicht, bleibt die
  Proxmox-Verbindung trotzdem stehen; die Antwort sagt, was fehlt.

Danach liest Nodvard Deck einmal mit dem neuen Token (`/nodes` und den Status eines Knotens); erst wenn das klappt,
landen Verbindung (`settings.connections[]`, dieselben Felder wie im Einstellungsformular) und Token-Geheimnis
(Tresor, Label `proxmox-token:<name>`) in Nodvard Deck, und der Proxmox-Abgleich startet sofort im Hintergrund (die
Antwort wartet nicht darauf). Bekommt eine vorhandene Verbindung einen neuen Token, wird ihr Geheimnis in einem
Schritt ersetzt (`ctx.secrets.replace`): scheitert das, bleibt das alte. Klappt etwas nach dem Anlegen eines Tokens
nicht, wird genau dieser neue Token in Proxmox wieder geloescht; gelingt das nicht (oder ist nach einer abgebrochenen
Verbindung unklar, ob es ihn gibt), nennt die Meldung seine ID, damit man ihn selbst loescht. Was sonst schon in
Proxmox angelegt war (Rollen, Benutzer, Berechtigungen, Pool), nennt jede Fehlermeldung (`created`): ein neuer
Versuch nutzt es weiter.

**Regeln fuer das Passwort** -- dieselben wie beim Einrichten eines Servers mit Passwort im Kern. Eine Erweiterung
darf den Kern nicht importieren (`scripts/check_core_purity.py`); das SDK ist der dauerhafte Vertrag mit allen
Erweiterungen, und fuer einen einzigen Nutzer lohnt dort kein neuer Baustein. Deshalb hier nachgebaut, klein und an
einer Stelle:

1. **Einmal benutzen, nie behalten.** Das Passwort kommt als `SecretStr` und geht in genau EINE Anfrage
   (`POST /access/ticket`). Keine Datenbank, kein Tresor, keine Datei, kein Zwischenspeicher, kein zweiter Versuch.
   Bei Zwei-Faktor-Anmeldung geht im zweiten Schritt nur die Aufforderung von Proxmox (`tfa-challenge`) mit dem Code
   hinaus, nie noch einmal das Passwort; gemerkt wird dafuer nur die Aufforderung, im Prozessspeicher, kurz.
2. **Nie im Protokoll.** Fehler sind `PasswordConnectError` mit festem `code` und festem deutschem Satz. Text von
   Proxmox oder einer Ausnahme der HTTP-Schicht wird nie weitergegeben (die Ausnahmen von httpx tragen die Anfrage
   samt Koerper), geworfen wird ausserhalb der `except`-Bloecke (keine angehaengte Ursache). Ins Protokoll kommen
   Adresse, Anmeldename, Token-IDs, Fingerabdruck und was angelegt wurde -- nie Passwort, Ticket, Token-Geheimnis
   oder Code.
3. **Nur an das bestaetigte Zertifikat.** `http://` wird abgelehnt. Zuerst wird nur das Zertifikat gelesen
   (`ctx.http.peer_certificate`, ohne etwas zu senden). Gilt es nach den Zertifizierungsstellen (samt Rechnername),
   reicht das als Bestaetigung; sonst muss die Person genau diesen Fingerabdruck bestaetigen. In beiden Faellen ist
   JEDE Anfrage des Ablaufs an diesen Fingerabdruck gebunden (`pinned_sha256`): Zeigt der Server inzwischen ein
   anderes Zertifikat, scheitert der TLS-Handshake, bevor etwas gesendet ist -- vor der Anmeldung
   `certificate_changed` (das Passwort ging nicht hinaus), danach `certificate_changed_after_login` (das Passwort
   ging nur an den bestaetigten Server; der Ablauf bricht ab). Beide nennen den neuen Fingerabdruck. Gepinnte Clients
   merken sich keine Cookies -- das Ticket geht nur mit, wo der Ablauf es selbst setzt, nie mit der Probe eines
   neuen Tokens.
4. **Nur das Ziel aus dem Formular.** Keine Weiterleitung (`ctx.http` folgt keiner; eine 3xx-Antwort ist `redirect`),
   kein Proxy aus der Umgebung.
5. **Begrenzt.** Je Nutzer und je Ziel nur wenige Versuche in 10 Minuten (ein Versuch = das Passwort oder ein Code
   geht hinaus; "Ziel" zaehlt doppelt: je Adresse und je Zertifikat, damit eine zweite Schreibweise derselben
   Adresse die Grenze nicht umgeht), davor eine grobe Drossel je Nutzer fuer jeden Aufruf, und nur ein Lauf
   gleichzeitig. Eine Sperre steht je Nutzer einmal im Zeitfenster im Protokoll.

Zustand (Zaehler, "laeuft gerade", wartende Zwei-Faktor-Anmeldungen) nur im Prozessspeicher, nach einem Neustart weg.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import secrets as token_bytes
import time
import unicodedata
from collections import deque
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote, unquote, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from fastapi.responses import JSONResponse
from nodvard_sdk import (
    Actor,
    CertificateMismatch,
    ConnectionReceiver,
    PeerCertificate,
    SecretNotOwned,
    same_target,
    sha256_fingerprint,
)
from pydantic import BaseModel, Field, SecretStr

from .certificate import FIELD as FINGERPRINT_FIELD
from .config import build_connector
from .connector import ProxmoxApiError

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

TOKEN_LABEL = "proxmox-token"
AUDIT_ACTION = "proxmox.connect_with_password"

PVE_USER = "nodvard@pve"
BACKUP_USER = "nodvard-backup@pve"
"""Eigener Benutzer fuer den Zugang von Proxmox-Backups (siehe `_set_up_backups`)."""
TOKEN_BASE = "deck"
DECK_TOKEN_PATTERN = re.compile(r"^deck(?:-[0-9]{1,4})?$")
"""Namen der Tokens, die Nodvard Deck fuer `nodvard@pve` bzw. `nodvard-backup@pve` anlegt -- nur solche gelten als
"alt" (`old_tokens_of`)."""
BACKUP_LABEL = re.compile(r"^backups-token:[A-Za-z0-9_.-]{1,64}$")
ROLES = {"full": "NodvardDeck", "read": "NodvardDeckRO"}

READ_PRIVILEGES = ("Sys.Audit", "VM.Audit", "VM.GuestAgent.Audit", "Datastore.Audit")
"""Lesen: Knoten (Status, Verlauf, Datentraeger, Aufgaben, Kernel), Gaeste (Liste, Status, Konfiguration, Snapshots),
echte VM-Adressen ueber den Gast-Agenten (gibt es erst ab Proxmox VE 9 -- in VE 8 bewusst NICHT das dortige
`VM.Monitor`, das auch Befehle in der VM erlaubt), Speicherbelegung."""

CONTROL_PRIVILEGES = (
    "VM.PowerMgmt", "VM.Snapshot", "VM.Config.CPU", "VM.Config.Memory", "VM.Config.Options", "VM.Console",
    "Sys.PowerMgmt", "Sys.Modify", "VM.Config.Disk",
)
"""Steuern: Starten/Herunterfahren/Neustarten, Snapshots, Hardware (Kerne, RAM, Autostart, Startreihenfolge),
Konsole, Knoten neu starten. Zwei davon braucht Nodvard Deck schon zum LESEN, Proxmox erlaubt damit aber mehr:
`Sys.Modify` (wartende Updates, Paketlisten aktualisieren, bei VMs die Startreihenfolge) und `VM.Config.Disk`
(welche Gast-Disks auf welchem Speicher liegen). Nicht dabei: `VM.Backup` und `Datastore.AllocateSpace` -- die
braucht nur die Erweiterung "Backups" (eigener Zugang, unten), und mit ihnen liessen sich Sicherungen loeschen."""

PRIVILEGES = {"read": READ_PRIVILEGES, "full": READ_PRIVILEGES + CONTROL_PRIVILEGES}

BACKUP_KIND = "proxmox"
"""`ConnectionReceiver.connection_kind` der Erweiterung "Proxmox-Backups"."""
BACKUP_ROLES = {"full": "NodvardDeckBackup", "read": "NodvardDeckBackupRO"}
BACKUP_READ_PRIVILEGES = ("Sys.Audit", "VM.Audit", "Datastore.Audit")
"""Proxmox-Backups lesen (auf `/`): Backup-Jobs und "nicht gesichert" (`/cluster/backup...`, `Sys.Audit`), die
naechtlichen Laeufe anderer Benutzer (`/nodes/{n}/tasks`, `Sys.Audit`), Gaeste und ihre Plattengroessen
(`/cluster/resources`, `.../config`, `VM.Audit`), die Backup-Speicher (`Datastore.Audit`)."""
BACKUP_CONTROL_PRIVILEGES = ("VM.Backup", "Sys.Modify", "Datastore.AllocateSpace", "SDN.Use")
"""Fuer die Knoepfe von Proxmox-Backups (auf `/`): `VM.Backup` (Backup jetzt, vorhandene Sicherungen sehen und zum
Wiederherstellen lesen), `Sys.Modify` (Backup-Jobs anlegen und aendern verlangt es auf `/`, ebenso ein Lauf mit
Bandbreitengrenze oder ionice und das Wiederherstellen eines privilegierten Containers), `Datastore.AllocateSpace`
(Zielspeicher beim Wiederherstellen als Kopie, Fleecing-Speicher eines Jobs) und `SDN.Use` (die Bridges der Kopie,
ab Proxmox VE 8). Wo Sicherungen liegen, gilt statt dessen die Speicher-Rolle (`STORE_PRIVILEGES`)."""
BACKUP_PRIVILEGES = {"read": BACKUP_READ_PRIVILEGES, "full": BACKUP_READ_PRIVILEGES + BACKUP_CONTROL_PRIVILEGES}
STORE_ROLE = "NodvardDeckBackupStore"
STORE_PRIVILEGES = ("Datastore.Audit", "Datastore.AllocateSpace", "Datastore.Allocate")
"""NUR auf `/storage/<Speicher mit Inhalt backup>`: Proxmox verlangt `Datastore.Allocate` auf dem Speicher fuer
Backup-Jobs (anlegen, aendern) und fuer jeden Lauf mit Aufbewahrung (Proxmox-Backups schickt immer eine mit). Weil
eine Berechtigung auf einem tieferen Pfad die geerbte ersetzt, stecken `Datastore.Audit` und
`Datastore.AllocateSpace` mit drin -- so behaelt auch der Proxmox-Token dort sein `Datastore.Audit`."""
RESTORE_POOL = "nodvard-kopien"
RESTORE_POOL_COMMENT = "Nodvard Deck: wiederhergestellte Kopien"
RESTORE_ROLE = "NodvardDeckRestore"
RESTORE_PRIVILEGES = ("VM.Allocate", "VM.Audit", "VM.Config.Options", "VM.Config.Network")
"""NUR auf `/pool/nodvard-kopien`: eine Kopie aus einer Sicherung anlegen, die in diesem Pool landet (Proxmox prueft
`VM.Allocate` dann auf dem Pool statt auf der neuen Nummer), und sie fertig einrichten (Name, Autostart,
Netzwerkkarten trennen). Fuer Gaeste im Pool gilt die Rolle zusaetzlich; Originale liegen nicht darin."""

DEFAULT_PORT = 8006
CALL_TIMEOUT_S = 20.0
CERT_TIMEOUT_S = 10.0
TFA_TTL_S = 180
MAX_TOKEN_TRIES = 50

ATTEMPTS_PER_TARGET = 5
"""Hoechstens so viele Versuche je Ziel (Adresse und Port) in `ATTEMPT_WINDOW_S` -- egal, wer es versucht."""
ATTEMPTS_PER_USER = 10
"""Hoechstens so viele Versuche je Nutzer in `ATTEMPT_WINDOW_S` -- egal, an welchem Server."""
ATTEMPT_WINDOW_S = 10 * 60
REQUESTS_PER_USER = 30
"""Grobe Drossel: hoechstens so viele Aufrufe je Nutzer in `REQUEST_WINDOW_S`, ob sie als Versuch zaehlen oder
nicht (Zertifikat lesen, unbestaetigtes Zertifikat, Server nicht erreichbar, "laeuft gerade")."""
REQUEST_WINDOW_S = 5 * 60

NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
USER_PATTERN = re.compile(r"^[^\s@:!]{1,64}@[A-Za-z0-9_.-]{1,32}$")
SAFE_TOKEN_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")
"""So darf ein Token-Name aus einer Antwort von Proxmox in einen Satz (Proxmox selbst erlaubt nicht mehr)."""
STORAGE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,99}$")
PRIVILEGE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9._]{0,63}$")
MAX_PRIVILEGES = 200
"""Hoechstens so viele Rechte einer Rolle aus einer Antwort von Proxmox (`_privs_of`)."""


# ---------------------------------------------------------------------------
# Fehler: feste Saetze, feste Codes
# ---------------------------------------------------------------------------

_ERRORS: dict[str, tuple[int, str]] = {
    "invalid_address": (400, "Die Adresse ist ungültig. Beispiel: https://192.168.2.10:8006"),
    "https_required": (
        400,
        ("Für diesen Weg braucht Nodvard Deck eine https://-Adresse – über http:// ginge dein Passwort unverschlüsselt "
         "durchs Netz. Proxmox selbst ist unter https://…:8006 erreichbar."),
    ),
    "invalid_name": (
        400, "Der Kurzname darf nur Buchstaben, Zahlen, Punkt, Bindestrich und Unterstrich enthalten (höchstens 40 Zeichen).",
    ),
    "invalid_user": (400, "Der Benutzer braucht den Bereich dahinter, zum Beispiel root@pam."),
    "invalid_password": (400, "Das Passwort fehlt oder enthält ein Zeichen, das sich nicht senden lässt."),
    "invalid_code": (400, "Der Code fehlt oder enthält ein Zeichen, das sich nicht senden lässt."),
    "invalid_fingerprint": (400, "Der bestätigte Fingerabdruck ist kein SHA-256-Fingerabdruck."),
    "name_taken": (409, "Der Kurzname „{name}“ gehört schon zu einem anderen Proxmox-Server. Nimm einen anderen."),
    "already_connected": (
        409,
        ("Diese Adresse ist schon als „{name}“ eingetragen. Lass den Kurznamen leer oder nimm „{name}“ – dann bekommt "
         "diese Verbindung einen neuen Zugang."),
    ),
    "rate_limited": (429, "Zu viele Versuche mit Passwort. Bitte in {wait} erneut versuchen."),
    "busy": (429, "Gerade läuft schon eine Verbindung mit Passwort. Bitte warte, bis sie fertig ist."),
    "unreachable": (502, "Proxmox ist nicht erreichbar. Stimmen Adresse und Port (meist 8006), und ist der Server an?"),
    "no_tls": (502, "Unter dieser Adresse antwortet kein Server mit https. Stimmt der Port (meist 8006)?"),
    "certificate_unconfirmed": (
        409,
        ("Nodvard Deck kennt das Zertifikat dieses Servers noch nicht. Vergleiche den Fingerabdruck und bestätige ihn – "
         "dein Passwort wurde nicht gesendet."),
    ),
    "certificate_changed": (
        409,
        ("Achtung: Der Server zeigt ein anderes Zertifikat als das, das du bestätigt hast. Dein Passwort wurde nicht "
         "gesendet. Vergleiche den neuen Fingerabdruck in Proxmox, bevor du ihn bestätigst."),
    ),
    "certificate_changed_after_login": (
        409,
        ("Achtung: Der Server zeigt inzwischen ein anderes Zertifikat als das, das du bestätigt hast. Dein Passwort "
         "ging vorher nur an den Server mit dem bestätigten Zertifikat; danach hat Nodvard Deck abgebrochen und nichts "
         "gespeichert. Vergleiche den neuen Fingerabdruck in Proxmox (Knoten → System → Zertifikate), bevor du es noch "
         "einmal versuchst."),
    ),
    "wrong_password": (400, "Proxmox hat die Anmeldung abgelehnt: Benutzer oder Passwort stimmt nicht."),
    "login_rejected": (
        400,
        ("Proxmox hat die Anmeldung nicht angenommen. Stimmt der Benutzer samt Bereich (zum Beispiel root@pam)? "
         "Nodvard Deck braucht Proxmox VE 7.1 oder neuer."),
    ),
    "tfa_unsupported": (
        409,
        ("Dein Proxmox-Konto verlangt einen zweiten Faktor, den Nodvard Deck nicht abfragen kann (etwa einen "
         "Sicherheitsschlüssel). Nimm den Weg mit API-Token aus der Anleitung – oder ein Konto mit einer Code-App."),
    ),
    "tfa_wrong": (400, "Der Code stimmt nicht oder ist abgelaufen. Gib Passwort und einen neuen Code noch einmal ein."),
    "tfa_expired": (409, "Die Anmeldung ist abgelaufen. Gib das Passwort noch einmal ein."),
    "not_allowed": (
        403,
        ("Dieses Proxmox-Konto darf keine Benutzer, Rollen oder API-Token anlegen. Nimm root@pam oder ein Konto mit "
         "vollen Administratorrechten."),
    ),
    "redirect": (
        502,
        ("Proxmox hat auf eine andere Adresse umgeleitet. Solchen Umleitungen folgt Nodvard Deck nicht – trag die "
         "direkte Adresse des Knotens ein (https://…:8006)."),
    ),
    "proxmox_error": (502, "Proxmox hat beim Anlegen des Zugangs einen Fehler gemeldet. In Nodvard Deck wurde nichts gespeichert."),
    "probe_failed": (502, "Der neue Zugang ließ sich nicht benutzen. In Nodvard Deck wurde nichts gespeichert."),
    "save_failed": (500, "Der Zugang ließ sich in Nodvard Deck nicht speichern. Bitte versuche es noch einmal."),
    "secret_foreign": (
        409,
        ("Im Tresor liegt schon ein Geheimnis „{name}“, das nicht von Proxmox VE stammt (etwa von Hand angelegt). "
         "Nodvard Deck ändert und löscht es nicht – nimm einen anderen Kurznamen oder lösch das Geheimnis selbst über "
         "die Tresor-Schnittstelle (DELETE /api/v1/secrets/{id}). In Nodvard Deck wurde nichts gespeichert."),
    ),
    "connection_failed": (502, "Die Verbindung zu Proxmox ist fehlgeschlagen. Bitte versuche es noch einmal."),
}
CODES = frozenset(_ERRORS)
_CERTIFICATE_CODES = ("certificate_changed", "certificate_unconfirmed", "certificate_changed_after_login")
_ALWAYS_AUDITED = ("rate_limited", "certificate_changed_after_login")
"""Steht im Protokoll, auch wenn in diesem Aufruf weder Passwort noch Code hinausging."""


def _created_note(created: Sequence[str]) -> str:
    return (
        "In Proxmox angelegt: " + ", ".join(created) + ". Das bleibt bestehen, ein neuer Versuch nutzt es weiter."
    )


class PasswordConnectError(Exception):
    """Der Versuch ging nicht. `str(exc)` ist ein fester deutscher Satz (dazu, was in Proxmox schon angelegt war, und
    Saetze zu neuen Tokens), `code` ein fester Bezeichner, `status` der passende HTTP-Status. Nie mit Text von Proxmox
    oder einer Ausnahme."""

    def __init__(
        self, code: str, *, name: str | None = None, retry_after: int | None = None,
        certificate: PeerCertificate | None = None, audit: bool = True, notes: Sequence[str] = (),
        leftover_tokens: Sequence[str] = (), created: Sequence[str] = (),
    ) -> None:
        status, text = _ERRORS[code]
        if retry_after is not None:
            text = text.replace("{wait}", _wait_text(retry_after))
        if name is not None:
            text = text.replace("{name}", name)
        self.code = code
        self.name = name
        self.status = status
        self.retry_after = retry_after
        self.certificate = certificate
        self.audit = audit
        self.notes = list(notes)
        self.leftover_tokens = list(leftover_tokens)
        """Token-IDs neuer Tokens, die in Proxmox (vielleicht) stehen geblieben sind -- zum Selbstloeschen."""
        self.created = list(created)
        """Was in Proxmox schon angelegt war und bleibt (Rollen, Benutzer, Berechtigungen, Pool)."""
        parts = [text]
        if self.created:
            parts.append(_created_note(self.created))
        parts.extend(self.notes)
        super().__init__(" ".join(parts))

    @property
    def leftover_token(self) -> str | None:
        return self.leftover_tokens[0] if self.leftover_tokens else None

    def extended(
        self, *, notes: Sequence[str] = (), leftover: Sequence[str] = (), created: Sequence[str] | None = None,
    ) -> PasswordConnectError:
        return PasswordConnectError(
            self.code, name=self.name, retry_after=self.retry_after, certificate=self.certificate, audit=self.audit,
            notes=[*self.notes, *notes], leftover_tokens=[*self.leftover_tokens, *leftover],
            created=self.created if created is None else created,
        )

    def response(self) -> JSONResponse:
        body: dict[str, Any] = {"detail": str(self), "code": self.code}
        if self.certificate is not None:
            body["certificate"] = _certificate_out(self.certificate)
        if self.leftover_tokens:
            body["leftover_token"] = self.leftover_tokens[0]
            body["leftover_tokens"] = list(self.leftover_tokens)
        if self.created:
            body["created"] = list(self.created)
        headers: dict[str, str] = {}
        if self.retry_after is not None:
            body["retry_after"] = self.retry_after
            headers["Retry-After"] = str(self.retry_after)
        return JSONResponse(body, status_code=self.status, headers=headers)


def _wait_text(seconds: int) -> str:
    minutes = max(1, math.ceil(seconds / 60))
    return f"{minutes} {'Minute' if minutes == 1 else 'Minuten'}"


# ---------------------------------------------------------------------------
# Grenzen
# ---------------------------------------------------------------------------


class _SlidingWindow:
    """Wie `core.rate_limit.SlidingWindow`: `hit()` liefert 0 (erlaubt und gezaehlt) oder die Sekunden bis zum
    naechsten freien Versuch; ein abgelehnter Aufruf zaehlt nicht mit."""

    def __init__(self, limit: int, window_s: int) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> int:
        now = time.monotonic()
        cutoff = now - self.window_s
        for k in list(self._hits):
            hits = self._hits[k]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if not hits:
                del self._hits[k]
        hits = self._hits.get(key)
        if hits is not None and len(hits) >= self.limit:
            return max(1, math.ceil(hits[0] + self.window_s - now))
        self._hits.setdefault(key, deque()).append(now)
        return 0

    def reset(self) -> None:
        self._hits.clear()


_per_target = _SlidingWindow(ATTEMPTS_PER_TARGET, ATTEMPT_WINDOW_S)
_per_user = _SlidingWindow(ATTEMPTS_PER_USER, ATTEMPT_WINDOW_S)
_requests_per_user = _SlidingWindow(REQUESTS_PER_USER, REQUEST_WINDOW_S)
_refusals_logged = _SlidingWindow(1, ATTEMPT_WINDOW_S)
_busy: set[str] = set()


def _refused(wait: int, user_id: str) -> PasswordConnectError:
    return PasswordConnectError("rate_limited", retry_after=wait, audit=_refusals_logged.hit(user_id) == 0)


def _throttle(user_id: str) -> None:
    wait = _requests_per_user.hit(user_id)
    if wait:
        raise _refused(wait, user_id)


def _claim_attempt(user_id: str, target_key: str, fingerprint: str) -> None:
    """Erst die Grenze des Nutzers: wer schon darueber ist, verbraucht nichts vom Budget des Ziels. Das Ziel zaehlt je
    Adresse UND je Zertifikat: eine zweite Schreibweise (Rechnername statt IP, anderer DNS-Name) fuehrt zum selben
    Zertifikat und damit an dieselbe Grenze."""
    for window, key in ((_per_user, user_id), (_per_target, target_key), (_per_target, f"zertifikat:{fingerprint}")):
        wait = window.hit(key)
        if wait:
            raise _refused(wait, user_id)


@contextmanager
def _exclusive() -> Iterator[None]:
    if "connect" in _busy:
        raise PasswordConnectError("busy", audit=False)
    _busy.add("connect")  # kein await zwischen Abfrage und Eintrag
    try:
        yield
    finally:
        _busy.discard("connect")


def reset_state() -> None:
    """Nur fuer Tests."""
    _busy.clear()
    _pending.clear()
    for window in (_per_target, _per_user, _requests_per_user, _refusals_logged):
        window.reset()


# ---------------------------------------------------------------------------
# Eingaben
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Target:
    base_url: str
    """`https://rechner:port`, ohne Pfad."""
    key: str
    """Fuer die Grenze je Ziel: Rechner (klein, ohne Punkt am Ende) und Port."""


def normalize_address(raw: str) -> _Target:
    """`https://` Pflicht (ohne Schema gilt `https://`), ohne Port gilt 8006, ohne Pfad, Anfrage oder Zugangsdaten."""
    text = str(raw or "").strip()
    if not text or len(text) > 300 or any(unicodedata.category(ch).startswith(("C", "Z")) and ch != " " for ch in text):
        raise PasswordConnectError("invalid_address")
    if " " in text:
        raise PasswordConnectError("invalid_address")
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise PasswordConnectError("invalid_address") from None
    scheme = parts.scheme.lower()
    if scheme in ("http", "ws"):
        raise PasswordConnectError("https_required")
    if scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise PasswordConnectError("invalid_address")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise PasswordConnectError("invalid_address")
    host = parts.hostname.rstrip(".").lower()
    if not host or not re.fullmatch(r"[a-z0-9.:\-\[\]_]+", host):
        raise PasswordConnectError("invalid_address")
    port = port or DEFAULT_PORT
    shown = f"[{host}]" if ":" in host else host
    return _Target(base_url=f"https://{shown}:{port}", key=f"{host}:{port}")


def _check_name(raw: str | None) -> str | None:
    name = (raw or "").strip()
    if not name:
        return None
    if not NAME_PATTERN.fullmatch(name):
        raise PasswordConnectError("invalid_name")
    return name


def _check_user(raw: str) -> str:
    user = str(raw or "").strip()
    if not USER_PATTERN.fullmatch(user):
        raise PasswordConnectError("invalid_user")
    return user


def _sendable(value: str, code: str, *, limit: int) -> None:
    if not value or len(value) > limit:
        raise PasswordConnectError(code)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:  # z. B. ein einzelnes Surrogat aus JSON
        raise PasswordConnectError(code) from None


# ---------------------------------------------------------------------------
# Verbindungen in Nodvard Deck
# ---------------------------------------------------------------------------


@dataclass
class _Plan:
    name: str | None
    """Kurzname; `None`: erst nach der Anmeldung aus Proxmox ableiten. Gibt es schon eine Verbindung mit diesem
    Namen (zum selben Ziel), bekommt sie einen neuen Zugang."""


def _plan(connections: list[dict[str, Any]], target: _Target, name: str | None) -> _Plan:
    """Prueft VOR dem Senden des Passworts, ob Kurzname und Adresse zusammenpassen."""
    same = [c for c in connections if same_target(str(c.get("base_url") or ""), target.base_url)]
    if name is not None:
        by_name = next((c for c in connections if c.get("name") == name), None)
        if by_name is not None and not same_target(str(by_name.get("base_url") or ""), target.base_url):
            raise PasswordConnectError("name_taken", name=name)
        if same and same[0].get("name") != name:
            raise PasswordConnectError("already_connected", name=str(same[0].get("name")))
        return _Plan(name=name)
    if same:
        return _Plan(name=str(same[0].get("name")))
    return _Plan(name=None)


def _derive_name(candidate: str, connections: list[dict[str, Any]]) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]+", "-", candidate).strip("-.")[:32] or "proxmox"
    taken = {str(c.get("name")) for c in connections}
    if base not in taken:
        return base
    for n in range(2, 100):
        name = f"{base}-{n}"
        if name not in taken:
            return name
    return f"{base}-{token_bytes.token_hex(3)}"


def _certificate_out(cert: PeerCertificate) -> dict[str, Any]:
    return {
        "sha256": cert.sha256, "trusted": cert.trusted, "subject": cert.subject, "issuer": cert.issuer,
        "not_after": cert.not_after, "alt_names": list(cert.alt_names),
    }


async def _backup_receivers(ctx: Any) -> list[Any]:
    """Der Empfaenger von "Proxmox-Backups" (leer, wenn die Erweiterung aus ist oder es sie nicht gibt)."""
    try:
        receivers = await ctx.capabilities.query(ConnectionReceiver)
    except Exception as exc:  # noqa: BLE001 - ohne Empfaenger einfach ohne Backup-Zugang
        ctx.logger.warning("Proxmox mit Passwort verbinden: Proxmox-Backups nicht abfragbar (%s)", type(exc).__name__)
        return []
    return [r for r in receivers if getattr(r, "connection_kind", None) == BACKUP_KIND]


# ---------------------------------------------------------------------------
# Proxmox-Aufrufe (nur ueber ctx.http, Fehler nur als Code)
# ---------------------------------------------------------------------------


def _find(exc: BaseException, kind: type | tuple[type, ...]) -> bool:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(seen) < 8:
        if isinstance(current, kind):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def _transport_code(exc: BaseException) -> str:
    """Waehlt nur einen festen Code; der Text der Ausnahme bleibt draussen."""
    import ssl

    if _find(exc, CertificateMismatch):
        return "certificate_changed"
    if _find(exc, ssl.SSLCertVerificationError):
        return "certificate_unconfirmed"
    if _find(exc, (httpx.TimeoutException, TimeoutError)):
        return "unreachable"
    if _find(exc, ssl.SSLError):
        return "no_tls"
    if _find(exc, (httpx.ConnectError, OSError)):
        return "unreachable"
    return "connection_failed"


@dataclass
class _Proxmox:
    ctx: Any = field(repr=False)
    base_url: str
    pin: str | None
    """Bestaetigter Fingerabdruck; `None`: das Zertifikat gilt nach den Zertifizierungsstellen (strenge Pruefung)."""
    auth: dict[str, str] = field(default_factory=dict, repr=False)
    """Ticket und CSRF-Schutz nach der Anmeldung (bzw. der Token bei einer Probe) -- nur im Speicher, nur fuer diesen
    Lauf, nie in `repr` (Fehlermeldungen, Protokolle von Test-Werkzeugen)."""
    password_sent: bool = False
    """Das Passwort ging schon an den Server (Anmeldung beantwortet, auch mit der Aufforderung zum zweiten Faktor)."""

    async def call(
        self, method: str, path: str, *, data: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        url = f"{self.base_url}/api2/json{path}"
        problem: str | None = None
        response: Any = None
        try:
            response = await self.ctx.http.request(
                method, url, data=data, headers={**self.auth, **(headers or {})}, pinned_sha256=self.pin,
                timeout=CALL_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 - jeder Fehler wird zu einem festen Code
            problem = _transport_code(exc)
        if problem is not None:
            certificate: PeerCertificate | None = None
            if problem in _CERTIFICATE_CODES:
                # Nach der Anmeldung ist "dein Passwort wurde nicht gesendet" falsch: es ging (nur) an das bestaetigte
                # Zertifikat. Der neue Fingerabdruck kommt in beiden Faellen mit, zum Vergleichen in Proxmox.
                if problem == "certificate_changed" and (self.auth or self.password_sent):
                    problem = "certificate_changed_after_login"
                certificate = await _fresh_certificate(self.ctx, self.base_url)
            raise PasswordConnectError(problem, certificate=certificate)  # ausserhalb des except: keine Ursache im Anhang
        status = int(response.status_code)
        if 300 <= status < 400:
            raise PasswordConnectError("redirect")
        try:
            body = response.json()
        except (ValueError, RecursionError):
            body = None
        return status, body.get("data") if isinstance(body, dict) else None

    async def ok(self, method: str, path: str, *, data: dict[str, Any] | None = None) -> Any:
        """Wie `call`, aber jede Fehlerantwort ist ein fester Code (403: das Konto darf das nicht)."""
        status, payload = await self.call(method, path, data=data)
        if status == 403:
            raise PasswordConnectError("not_allowed")
        if status >= 400:
            raise PasswordConnectError("proxmox_error")
        return payload


def _tfa_kinds(ticket: str) -> tuple[str, ...]:
    """Was die Zwei-Faktor-Aufforderung anbietet (Format ab Proxmox VE 7.1: `PVE:!tfa!<JSON, URL-kodiert>:...`).
    Nur die Arten mit einem eintippbaren Code; WebAuthn/U2F braucht den Browser bei Proxmox selbst."""
    try:
        part = ticket.split(":")[1]
        challenge = json.loads(unquote(part[len("!tfa!"):])) if part.startswith("!tfa!") else {}
    except (IndexError, ValueError, RecursionError):
        challenge = {}
    if not isinstance(challenge, dict):
        return ()
    return tuple(kind for kind in ("totp", "yubico", "recovery") if challenge.get(kind))


def _ticket_auth(data: Any) -> dict[str, str] | None:
    if not isinstance(data, dict):
        return None
    ticket, csrf = data.get("ticket"), data.get("CSRFPreventionToken")
    if not isinstance(ticket, str) or not isinstance(csrf, str) or not ticket or ticket.startswith("PVE:!tfa!"):
        return None
    if any(ch in ticket + csrf for ch in "\r\n;"):
        return None
    return {"Cookie": f"PVEAuthCookie={ticket}", "CSRFPreventionToken": csrf}


@dataclass
class _Pending:
    user_id: str
    api: _Proxmox = field(repr=False)
    target: _Target
    trusted: bool
    username: str
    plan: _Plan
    access: str
    backups: bool | None
    challenge: str = field(repr=False)
    """Die Zwei-Faktor-Aufforderung von Proxmox (ein halbes Ticket) -- nie in `repr`."""
    kinds: tuple[str, ...]
    expires: float


_pending: dict[str, _Pending] = {}


def _drop_expired() -> None:
    now = time.monotonic()
    for key in [k for k, p in _pending.items() if p.expires <= now]:
        del _pending[key]


# ---------------------------------------------------------------------------
# Der Ablauf
# ---------------------------------------------------------------------------


@dataclass
class _Run:
    """Was ins Protokoll kommt (nie Geheimes) und ob das Passwort bzw. ein Code hinausging."""

    detail: dict[str, Any] = field(default_factory=dict)
    sent: bool = False


def _plain(value: Any) -> Any:
    """Fuer das Protokoll: Listen (auch `_Created`) als einfache Listen."""
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


async def _audit(ctx: Any, actor: Actor, run: _Run, *, outcome: str, code: str | None = None) -> None:
    detail = _plain(dict(run.detail))
    if code is not None:
        detail["code"] = code
    try:
        await ctx.audit.log(
            action=AUDIT_ACTION, outcome=outcome, target_type="extension", target_id=ctx.ext_id, reason=code,
            detail=detail, actor=actor,
        )
    except Exception as exc:  # noqa: BLE001 - das Protokoll darf den Ablauf nicht kippen
        ctx.logger.warning("Proxmox mit Passwort verbinden: Protokolleintrag fehlgeschlagen (%s)", type(exc).__name__)


async def _read_certificate(ctx: Any, base_url: str) -> PeerCertificate:
    import ssl

    code: str | None = None
    cert: PeerCertificate | None = None
    try:
        cert = await ctx.http.peer_certificate(base_url, timeout_s=CERT_TIMEOUT_S)
    except TimeoutError:
        code = "unreachable"
    except ssl.SSLError:
        code = "no_tls"
    except OSError:
        code = "unreachable"
    except Exception as exc:  # noqa: BLE001 - feste Codes, nie der Text
        code = "connection_failed"
        ctx.logger.info("Proxmox mit Passwort verbinden: Zertifikat nicht lesbar (%s)", type(exc).__name__)
    if code is not None or cert is None:
        raise PasswordConnectError(code or "connection_failed")
    return cert


async def certificate(ctx: Any, actor: Actor, base_url: str) -> dict[str, Any]:
    """Nur das Zertifikat lesen (nichts senden), zum Vergleichen und Bestaetigen. `backups_available`: ob die
    Erweiterung "Proxmox-Backups" an ist (dann bietet das Formular an, sie gleich mitzuverbinden)."""
    _throttle(actor.id)
    _drop_expired()
    target = normalize_address(base_url)
    cert = await _read_certificate(ctx, target.base_url)
    return {
        "base_url": target.base_url, "certificate": _certificate_out(cert),
        "backups_available": bool(await _backup_receivers(ctx)),
    }


async def _guarded(ctx: Any, actor: Actor, run: _Run, step: Any) -> dict[str, Any]:
    """Fuehrt einen Schritt aus und macht aus JEDEM Fehler einen festen Code (auch aus einem unerwarteten, nie ein
    500 mit Traceback). Die Meldung nennt, was in Proxmox schon angelegt war. Protokolliert wird jeder Lauf, bei dem
    das Passwort oder ein Code hinausging, ein Zertifikatswechsel nach der Anmeldung und eine Sperre einmal je
    Zeitfenster -- nie mit Geheimem."""
    problem: PasswordConnectError | None = None
    result: dict[str, Any] | None = None
    try:
        result = await step
    except PasswordConnectError as exc:
        problem = exc
    except Exception as exc:  # noqa: BLE001 - nie ein 500 mit Traceback (Ausnahmen der HTTP-Schicht tragen die Anfrage)
        ctx.logger.warning("Proxmox mit Passwort verbinden: unerwarteter Fehler (%s)", type(exc).__name__)
        problem = PasswordConnectError("connection_failed")
    if problem is not None:
        created = list(run.detail.get("created") or [])
        if created and not problem.created:
            problem = problem.extended(created=created)
        ctx.logger.info("Proxmox mit Passwort verbinden: %s", problem.code)
        if (run.sent or problem.code in _ALWAYS_AUDITED) and problem.audit:
            await _audit(ctx, actor, run, outcome="failure", code=problem.code)
        raise problem
    assert result is not None
    if result.get("status") == "tfa_required":
        await _audit(ctx, actor, run, outcome="success", code="tfa_required")
    else:
        await _audit(ctx, actor, run, outcome="success")
    return result


async def start(
    ctx: Any, actor: Actor, *, base_url: str, username: str, password: SecretStr, name: str | None,
    fingerprint: str | None, access: str, backups: bool | None = None,
) -> dict[str, Any]:
    """Anmelden und alles anlegen -- oder, wenn Proxmox einen zweiten Faktor verlangt, `tfa_required` mit einer
    Vorgangsnummer zurueck (`finish_tfa`)."""
    run = _Run()
    return await _guarded(ctx, actor, run, _start(
        ctx, actor, run, base_url=base_url, username=username, password=password, name=name, fingerprint=fingerprint,
        access=access, backups=backups,
    ))


async def _start(
    ctx: Any, actor: Actor, run: _Run, *, base_url: str, username: str, password: SecretStr, name: str | None,
    fingerprint: str | None, access: str, backups: bool | None,
) -> dict[str, Any]:
    _throttle(actor.id)
    with _exclusive():
        target = normalize_address(base_url)
        wanted_name = _check_name(name)
        user = _check_user(username)
        pin: str | None = None
        if fingerprint:
            try:
                pin = sha256_fingerprint(fingerprint)
            except ValueError:
                raise PasswordConnectError("invalid_fingerprint") from None
        _sendable(password.get_secret_value(), "invalid_password", limit=512)
        run.detail.update({"address": target.base_url, "login_user": user, "access": access, "backups": backups})
        settings = await ctx.settings.get()
        connections = list(settings.get("connections") or [])
        plan = _plan(connections, target, wanted_name)
        if plan.name is not None:
            await _clear_orphan_secret(ctx, plan.name, connections)  # vor dem Passwort: ein fremdes Geheimnis stoppt hier
        cert = await _read_certificate(ctx, target.base_url)
        if pin is not None and pin != cert.sha256:
            raise PasswordConnectError("certificate_changed", certificate=cert)
        if pin is None and not cert.trusted:
            raise PasswordConnectError("certificate_unconfirmed", certificate=cert)
        run.detail["certificate"] = _CERT_PINNED if pin is not None else _CERT_TRUSTED
        run.detail["fingerprint"] = cert.sha256
        _claim_attempt(actor.id, target.key, cert.sha256)
        # Immer gepinnt, auch bei einem anerkannten Zertifikat (dann auf das eben streng gepruefte): so laeuft der
        # ganze Ablauf ueber Clients ohne Cookie-Speicher und an genau dieses Zertifikat.
        api = _Proxmox(ctx, target.base_url, pin or cert.sha256)
        run.sent = True
        status, data = await _send_once(
            api, run, {"username": user, "password": password.get_secret_value(), "new-format": "1"},
        )
        if status == 401:
            raise PasswordConnectError("wrong_password")
        if status >= 400 or not isinstance(data, dict):
            raise PasswordConnectError("login_rejected" if 400 <= status < 500 else "proxmox_error")
        if data.get("NeedTFA"):
            ticket = data.get("ticket")
            kinds = _tfa_kinds(ticket) if isinstance(ticket, str) else ()
            if not kinds:
                raise PasswordConnectError("tfa_unsupported")
            _drop_expired()
            for key in [k for k, p in _pending.items() if p.user_id == actor.id]:
                del _pending[key]
            flow = token_bytes.token_urlsafe(24)
            _pending[flow] = _Pending(
                user_id=actor.id, api=api, target=target, trusted=cert.trusted,
                username=str(data.get("username") or user), plan=plan, access=access, backups=backups,
                challenge=str(ticket), kinds=kinds, expires=time.monotonic() + TFA_TTL_S,
            )
            run.detail["tfa"] = True
            return {"status": "tfa_required", "tfa": {"flow": flow, "kinds": list(kinds), "expires_in_s": TFA_TTL_S}}
        auth = _ticket_auth(data)
        if auth is None:
            raise PasswordConnectError("proxmox_error")
        api.auth = auth
        return await _set_up(ctx, api, target, trusted=cert.trusted, plan=plan, access=access, backups=backups, run=run)


_CERT_PINNED = "Fingerabdruck bestätigt"
_CERT_TRUSTED = "von einer Zertifizierungsstelle bestätigt"


async def _send_once(api: _Proxmox, run: _Run, form: dict[str, Any]) -> tuple[int, Any]:
    """Die eine Anfrage mit Passwort bzw. Code. Scheitert schon der Handshake am Zertifikat, ging nichts hinaus
    (`run.sent` zurueck); der Fehler traegt das Zertifikat, das der Server jetzt zeigt, zum Vergleichen."""
    try:
        status, data = await api.call("POST", "/access/ticket", data=form)
    except PasswordConnectError as exc:
        if exc.code in _CERTIFICATE_CODES:
            run.sent = False
        raise
    finally:
        form.clear()
    api.password_sent = True
    return status, data


async def _fresh_certificate(ctx: Any, base_url: str) -> PeerCertificate | None:
    try:
        return await _read_certificate(ctx, base_url)
    except PasswordConnectError:
        return None


async def finish_tfa(ctx: Any, actor: Actor, *, flow: str, code: SecretStr, kind: str) -> dict[str, Any]:
    """Zweiter Schritt: Code zur gemerkten Aufforderung schicken (nie noch einmal das Passwort)."""
    run = _Run()
    return await _guarded(ctx, actor, run, _finish_tfa(ctx, actor, run, flow=flow, code=code, kind=kind))


async def _finish_tfa(ctx: Any, actor: Actor, run: _Run, *, flow: str, code: SecretStr, kind: str) -> dict[str, Any]:
    _throttle(actor.id)
    with _exclusive():
        _drop_expired()
        pending = _pending.get(str(flow))
        if pending is None or pending.user_id != actor.id:
            # Ein fremder (oder geratener) Vorgang bleibt fuer seinen Besitzer stehen.
            raise PasswordConnectError("tfa_expired")
        del _pending[str(flow)]
        run.detail.update({
            "address": pending.target.base_url, "login_user": pending.username, "access": pending.access,
            "backups": pending.backups, "tfa": True,
            "certificate": _CERT_TRUSTED if pending.trusted else _CERT_PINNED, "fingerprint": pending.api.pin,
        })
        if kind not in pending.kinds:
            raise PasswordConnectError("tfa_unsupported")
        value = code.get_secret_value().strip()
        if kind == "totp":
            value = value.replace(" ", "")
        _sendable(value, "invalid_code", limit=256)
        if any(unicodedata.category(ch).startswith(("C", "Z")) for ch in value):
            raise PasswordConnectError("invalid_code")
        _claim_attempt(actor.id, pending.target.key, str(pending.api.pin))
        run.sent = True
        api = pending.api
        form = {"username": pending.username, "tfa-challenge": pending.challenge, "password": f"{kind}:{value}"}
        del value
        status, data = await _send_once(api, run, form)
        if status in (400, 401):
            raise PasswordConnectError("tfa_wrong")
        auth = _ticket_auth(data) if status < 400 else None
        if auth is None:
            raise PasswordConnectError("proxmox_error")
        api.auth = auth
        return await _set_up(
            ctx, api, pending.target, trusted=pending.trusted, plan=pending.plan, access=pending.access,
            backups=pending.backups, run=run,
        )


# ---------------------------------------------------------------------------
# Anlegen in Proxmox
# ---------------------------------------------------------------------------


class _Created(list):
    """Was in diesem Lauf in Proxmox angelegt wurde, als Saetze ("Rolle NodvardDeck", ...) -- fortlaufend, damit auch
    eine Fehlermeldung (und das Protokoll) sagt, was liegen blieb. Eintraege eines Tokens (der Token und seine
    Berechtigungen) verschwinden, wenn der Token wieder geloescht wurde: Proxmox nimmt seine Berechtigungen mit."""

    def __init__(self) -> None:
        super().__init__()
        self._by_token: dict[str, list[str]] = {}

    def add(self, text: str, *, token: str | None = None) -> None:
        self.append(text)
        if token is not None:
            self._by_token.setdefault(token, []).append(text)

    def drop_token(self, token: str) -> None:
        for text in self._by_token.pop(token, []):
            if text in self:
                self.remove(text)


@dataclass
class _Role:
    name: str
    privileges: list[str]
    """Was die Rolle danach erlaubt (die gewuenschten, soweit es sie gibt, und was sie vorher schon hatte)."""
    missing: list[str]
    """Gewuenscht, aber in dieser Proxmox-Version unbekannt (weggelassen)."""
    extra: list[str]
    """Hatte die Rolle schon, gewuenscht war es nicht (wird nicht weggenommen)."""


def _privs_of(entry: Any) -> set[str]:
    """Rechte einer Rolle aus einer Antwort von Proxmox. Sie gehen in Antwort, Hinweise und Protokoll -- deshalb nur
    Namen, die wie ein Recht aussehen (`PRIVILEGE_NAME`), und hoechstens `MAX_PRIVILEGES`."""
    privs = entry.get("privs") if isinstance(entry, dict) else None
    if isinstance(privs, str):
        raw = [p.strip() for p in privs.split(",")]
    elif isinstance(privs, dict):
        raw = [str(k) for k, v in privs.items() if v]
    elif isinstance(privs, list):
        raw = [str(p) for p in privs]
    else:
        raw = []
    return set(sorted({p for p in raw if PRIVILEGE_NAME.fullmatch(p)})[:MAX_PRIVILEGES])


async def _ensure_role(
    api: _Proxmox, by_id: dict[str, Any], role: str, wanted: Sequence[str], available: set[str] | None,
    created: _Created,
) -> _Role:
    """Rolle anlegen bzw. fehlende Rechte ergaenzen -- nur Rechte, die es in dieser Version gibt (die Rolle
    Administrator hat alle). Weggenommen wird nie etwas."""
    privileges = [p for p in wanted if available is None or p in available]
    missing = [p for p in wanted if p not in privileges]
    had = _privs_of(by_id[role]) if role in by_id else set()
    if role not in by_id:
        await api.ok("POST", "/access/roles", data={"roleid": role, "privs": ",".join(privileges)})
        created.add(f"Rolle {role}")
        by_id[role] = {"roleid": role, "privs": ",".join(privileges)}
    else:
        lacking = [p for p in privileges if p not in had]
        if lacking:
            await api.ok("PUT", f"/access/roles/{quote(role, safe='')}", data={"privs": ",".join(lacking), "append": 1})
            created.add(f"Rolle {role} ergänzt ({', '.join(lacking)})")
    return _Role(role, sorted(set(privileges) | had), missing, sorted(had - set(wanted)))


async def _read_acl(api: _Proxmox) -> set[tuple[str, str, str, str]]:
    """Vorhandene Berechtigungen (Pfad, Art, Benutzer/Token, Rolle) -- damit nur NEUE im Ergebnis stehen. Laesst die
    Liste sich nicht lesen, gilt sie als leer (gesetzt wird ohnehin mit `PUT`, das doppelt nichts anlegt)."""
    status, rows = await api.call("GET", "/access/acl")
    if status >= 400 or not isinstance(rows, list):
        return set()
    return {
        (str(r.get("path")), str(r.get("type")), str(r.get("ugid")), str(r.get("roleid")))
        for r in rows if isinstance(r, dict)
    }


async def _grant(
    api: _Proxmox, acl: set[tuple[str, str, str, str]], created: _Created, *, path: str, role: str,
    user: str | None = None, token: str | None = None,
) -> None:
    kind, who = ("user", user) if user is not None else ("token", str(token))
    if (path, kind, who, role) in acl:
        return
    await api.ok("PUT", "/access/acl", data={
        "path": path, "roles": role, ("users" if kind == "user" else "tokens"): who, "propagate": 1,
    })
    acl.add((path, kind, who, role))
    created.add(f"Berechtigung auf {path} für {who} (Rolle {role})", token=token)


def _user_path(user: str) -> str:
    return f"/access/users/{quote(user, safe='@')}"


async def _ensure_user(api: _Proxmox, users: list[Any], user: str, *, comment: str, created: _Created) -> None:
    """Benutzer ohne Passwort (damit meldet sich niemand an der Oberflaeche an); ein gesperrter wird wieder
    eingeschaltet -- wer neu verbindet, will ihn benutzen."""
    row = next((u for u in users if isinstance(u, dict) and u.get("userid") == user), None)
    if row is None:
        await api.ok("POST", "/access/users", data={"userid": user, "comment": comment, "enable": 1})
        created.add(f"Benutzer {user}")
        users.append({"userid": user, "enable": 1})
    elif str(row.get("enable", 1)) in ("0", "False", "false"):
        await api.ok("PUT", _user_path(user), data={"enable": 1})
        created.add(f"Benutzer {user} wieder eingeschaltet")


def _free_token_name(base: str, taken: set[str]) -> str | None:
    return next((n for n in [base] + [f"{base}-{i}" for i in range(2, MAX_TOKEN_TRIES)] if n not in taken), None)


def _privsep(row: dict[str, Any]) -> bool:
    return str(row.get("privsep", 1)).strip().lower() not in ("0", "false", "no")


def _token_rows(raw: Any) -> list[dict[str, Any]]:
    """Token-Liste aus `GET .../token` (Liste) oder `GET /access/users/{id}` (`tokens` als Objekt)."""
    if isinstance(raw, dict):
        return [{**(v if isinstance(v, dict) else {}), "tokenid": k} for k, v in raw.items()]
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    return []


def _privsep_off(rows_by_user: dict[str, Sequence[dict[str, Any]]]) -> list[str]:
    """Token-IDs ohne Privilegientrennung (nur Namen, die Proxmox selbst erlaubt -- sie stehen in Saetzen)."""
    return [
        f"{user}!{row.get('tokenid')}"
        for user, rows in rows_by_user.items() for row in rows
        if not _privsep(row) and SAFE_TOKEN_NAME.fullmatch(str(row.get("tokenid") or ""))
    ]


@dataclass
class _NewToken:
    user: str
    name: str
    """Name ohne Benutzer (`deck`, `deck-2`)."""
    certain: bool
    """`False`: die Verbindung brach beim Anlegen ab -- ob Proxmox ihn angelegt hat, weiss niemand."""

    @property
    def token_id(self) -> str:
        return f"{self.user}!{self.name}"


async def _create_token(
    api: _Proxmox, user: str, name: str, *, comment: str, created: _Created, new_tokens: list[_NewToken],
) -> str:
    """Neuer Token mit Privilegientrennung; liefert sein Geheimnis (nur im Speicher). Schon VOR dem Anlegen steht er
    in `new_tokens` (unsicher), damit ein Abbruch ihn nicht vergisst."""
    entry = _NewToken(user, name, certain=False)
    new_tokens.append(entry)
    try:
        data = await api.ok(
            "POST", f"{_user_path(user)}/token/{quote(name, safe='')}",
            data={"privsep": 1, "comment": comment, "expire": 0},
        )
    except PasswordConnectError as exc:
        if exc.code in ("not_allowed", "proxmox_error", *_CERTIFICATE_CODES):
            # Proxmox hat abgelehnt, oder schon der Handshake scheiterte (nichts gesendet): es gibt keinen neuen Token.
            new_tokens.remove(entry)
        else:
            # Verbindung abgebrochen oder Zeit abgelaufen: Ob Proxmox den Token angelegt hat, weiss hier niemand.
            api.ctx.logger.warning("Proxmox mit Passwort verbinden: unklar, ob der Token %s angelegt wurde", entry.token_id)
        raise
    except asyncio.CancelledError:
        api.ctx.logger.warning("Proxmox mit Passwort verbinden: abgebrochen, der Token %s bleibt evtl. in Proxmox", entry.token_id)
        raise
    entry.certain = True
    created.add(f"API-Token {entry.token_id}", token=entry.token_id)
    value = data.get("value") if isinstance(data, dict) else None
    if not isinstance(value, str) or not value or any(ch in value for ch in "\r\n"):
        raise PasswordConnectError("proxmox_error")
    return value


async def _delete_token(api: _Proxmox, token: _NewToken) -> bool:
    """Den eben angelegten Token wieder loeschen. `False`, wenn das nicht sicher geklappt hat."""
    code: str | None = None
    try:
        status, _data = await api.call("DELETE", f"{_user_path(token.user)}/token/{quote(token.name, safe='')}")
        if status >= 400:
            code = f"HTTP {status}"
    except PasswordConnectError as exc:
        code = exc.code
    if code is not None:
        api.ctx.logger.warning("Proxmox mit Passwort verbinden: neuer Token liess sich nicht entfernen (%s)", code)
        return False
    return True


async def _undo_tokens(
    api: _Proxmox, tokens: Sequence[_NewToken], created: _Created, run: _Run,
) -> tuple[list[str], list[str]]:
    """Die neuen Tokens wieder loeschen -> (Saetze dazu, Token-IDs, die (vielleicht) stehen blieben)."""
    notes: list[str] = []
    leftover: list[str] = []
    removed: list[str] = []
    for token in tokens:
        if not token.certain:
            notes.append(
                f"Vielleicht hat Proxmox den Token „{token.token_id}“ trotzdem angelegt – sieh unter Rechenzentrum → "
                "Rechte → API-Token nach und lösch ihn dort."
            )
            leftover.append(token.token_id)
        elif await _delete_token(api, token):
            created.drop_token(token.token_id)
            removed.append(token.token_id)
        else:
            notes.append(
                f"Den neuen Token „{token.token_id}“ konnte Nodvard Deck in Proxmox nicht wieder entfernen – lösch ihn "
                "dort selbst (Rechenzentrum → Rechte → API-Token)."
            )
            leftover.append(token.token_id)
    if removed:
        notes.insert(0, "Der neue Token wurde in Proxmox wieder entfernt." if len(removed) == 1 else
                     "Die neuen Tokens wurden in Proxmox wieder entfernt.")
    if leftover:
        known = list(run.detail.get("leftover_tokens") or [])
        run.detail["leftover_tokens"] = known + [t for t in leftover if t not in known]
        run.detail.setdefault("leftover_token", run.detail["leftover_tokens"][0])
    return notes, leftover


async def _probe(ctx: Any, target: _Target, pin: str | None, token_id: str, token_value: str) -> list[str]:
    """Einmal mit dem NEUEN Token lesen: Knotenliste und Status eines Knotens (braucht `Sys.Audit`)."""
    probe = _Proxmox(ctx, target.base_url, pin, auth={"Authorization": f"PVEAPIToken={token_id}={token_value}"})
    status, nodes = await probe.call("GET", "/nodes")
    names = [str(n.get("node")) for n in nodes if isinstance(n, dict) and n.get("node")] if isinstance(nodes, list) else []
    if status >= 400 or not names:
        raise PasswordConnectError("probe_failed")
    online = next((str(n.get("node")) for n in nodes if isinstance(n, dict) and n.get("status") == "online"), names[0])
    status, _data = await probe.call("GET", f"/nodes/{quote(online, safe='')}/status")
    if status >= 400:
        raise PasswordConnectError("probe_failed")
    return names


async def _proxmox_name(api: _Proxmox) -> str:
    """Name des Clusters, sonst des (einzigen) Knotens -- Vorschlag fuer den Kurznamen."""
    try:
        entries = await api.ok("GET", "/cluster/status")
    except PasswordConnectError as exc:
        if exc.code == "certificate_changed_after_login":
            raise
        entries = None
    entries = entries if isinstance(entries, list) else []
    for kind in ("cluster", "node"):
        for entry in entries:
            if isinstance(entry, dict) and entry.get("type") == kind and isinstance(entry.get("name"), str):
                return entry["name"]
    return "proxmox"


async def _clear_orphan_secret(ctx: Any, name: str, connections: Sequence[Any]) -> None:
    """Eine NEUE Verbindung startet ohne Geheimnis, wie beim Anlegen von Hand (`add_connection`): ein Geheimnis unter
    ihrem Label gehoert zu einer frueher entfernten Verbindung und geht weg. Gehoert es nicht dieser Erweiterung (etwa
    von Hand im Tresor angelegt), bricht der Ablauf hier ab, bevor etwas gespeichert ist (`secret_foreign`) -- beim
    angegebenen Kurznamen schon, bevor das Passwort hinausgeht."""
    if any(isinstance(c, dict) and c.get("name") == name for c in connections):
        return
    label = f"{TOKEN_LABEL}:{name}"
    foreign = False
    try:
        await ctx.secrets.delete(label)
    except SecretNotOwned:
        foreign = True
    if foreign:
        raise PasswordConnectError("secret_foreign", name=label)


# --- Zugang fuer Proxmox-Backups ---------------------------------------------------------------------------------


@dataclass
class _BackupAccess:
    receiver: Any
    token: _NewToken
    token_value: str = field(repr=False)
    role: _Role = field(repr=False)
    storages: list[str]
    pool: str | None


_BACKUP_FAILED = {
    "not_allowed": "Den Zugang für Proxmox-Backups durfte dieses Proxmox-Konto nicht anlegen.",
    "probe_failed": "Der neue Zugang für Proxmox-Backups ließ sich nicht benutzen.",
}
_BACKUP_FAILED_DEFAULT = "Den Zugang für Proxmox-Backups konnte Nodvard Deck in Proxmox nicht anlegen."


def _clean_sentence(text: Any, limit: int = 300) -> str:
    value = "".join(ch for ch in str(text or "") if not unicodedata.category(ch).startswith("C")).strip()
    return value[:limit]


async def _backup_storages(api: _Proxmox) -> list[str]:
    """Alle Speicher mit Inhalt `backup` (gemeinsame zaehlen einmal: die Liste kommt aus der Cluster-Konfiguration)."""
    rows = await api.ok("GET", "/storage")
    names: set[str] = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        content = {c.strip() for c in str(row.get("content") or "").split(",")}
        storage = row.get("storage")
        if "backup" in content and isinstance(storage, str) and STORAGE_ID.fullmatch(storage):
            names.add(storage)
    return sorted(names)


_POOL_NOT_OURS = (
    f"Den Pool „{RESTORE_POOL}“ gibt es in Proxmox schon, angelegt nicht von Nodvard Deck – dort bekommt der Zugang für "
    "Proxmox-Backups keine Rechte. Wiederherstellen als Kopie geht dann nicht."
)


async def _ensure_pool(
    api: _Proxmox, acl: set[tuple[str, str, str, str]], created: _Created, notes: list[str],
) -> str | None:
    """Pool fuer wiederhergestellte Kopien. Ein vorhandener wird nur uebernommen, wenn Nodvard Deck ihn angelegt hat
    (Kommentar genau `RESTORE_POOL_COMMENT`) und nichts Fremdes darin liegt: kein Speicher, und Gaeste nur, wenn der
    Backup-Zugang dort schon Rechte hatte (dann sind es seine Kopien). Sonst bekaeme der Zugang Rechte an fremden
    Gaesten (anlegen, aendern, loeschen) -- dann keine Rechte und ein Hinweis."""
    rows = await api.ok("GET", "/pools")
    existing = next(
        (r for r in rows if isinstance(r, dict) and r.get("poolid") == RESTORE_POOL), None,
    ) if isinstance(rows, list) else None
    if existing is None:
        await api.ok("POST", "/pools", data={"poolid": RESTORE_POOL, "comment": RESTORE_POOL_COMMENT})
        created.add(f"Pool {RESTORE_POOL}")
        return RESTORE_POOL
    if str(existing.get("comment") or "") != RESTORE_POOL_COMMENT:
        notes.append(_POOL_NOT_OURS)
        return None
    status, data = await api.call("GET", f"/pools/{quote(RESTORE_POOL, safe='')}")
    members = data.get("members") if status < 400 and isinstance(data, dict) else None
    if not isinstance(members, list):
        notes.append(
            f"Was im Pool „{RESTORE_POOL}“ liegt, ließ sich nicht lesen – dort bekommt der Zugang für Proxmox-Backups "
            "keine Rechte. Wiederherstellen als Kopie geht dann nicht."
        )
        return None
    had_rights = (f"/pool/{RESTORE_POOL}", "user", BACKUP_USER, RESTORE_ROLE) in acl
    foreign = [m for m in members if not isinstance(m, dict) or m.get("type") not in ("qemu", "lxc") or not had_rights]
    if foreign:
        notes.append(
            f"Im Pool „{RESTORE_POOL}“ liegt schon etwas, das nicht von Nodvard Deck stammt – dort bekommt der Zugang "
            "für Proxmox-Backups keine Rechte. Wiederherstellen als Kopie geht dann nicht. Nimm es aus dem Pool und "
            "verbinde noch einmal."
        )
        return None
    return RESTORE_POOL


async def _probe_backup(ctx: Any, target: _Target, pin: str | None, token_id: str, token_value: str) -> None:
    """Einmal mit dem neuen Backup-Token lesen, was Proxmox-Backups zuerst braucht: die Backup-Jobs (`Sys.Audit`)."""
    probe = _Proxmox(ctx, target.base_url, pin, auth={"Authorization": f"PVEAPIToken={token_id}={token_value}"})
    status, _data = await probe.call("GET", "/cluster/backup")
    if status >= 400:
        raise PasswordConnectError("probe_failed")


async def _set_up_backups(
    ctx: Any, api: _Proxmox, target: _Target, receiver: Any, *, access: str, by_id: dict[str, Any],
    available: set[str] | None, acl: set[tuple[str, str, str, str]], users: list[Any],
    rows_by_user: dict[str, list[dict[str, Any]]], created: _Created, new_tokens: list[_NewToken], notes: list[str],
    roles: list[_Role], run: _Run,
) -> _BackupAccess | None:
    """Eigener Zugang fuer Proxmox-Backups mit eigenem Benutzer `nodvard-backup@pve` -- nach bestem Bemuehen:
    scheitert etwas, kommt ein Satz in `notes`, ein neuer Backup-Token geht wieder weg, und der Proxmox-Teil laeuft
    weiter. Nur ein Zertifikatswechsel bricht alles ab.

    Eigener Benutzer, damit `nodvard@pve` (und jeder seiner Tokens, auch einer ohne Privilegientrennung) keine
    Backup-Rechte bekommt, und damit Rechte eines gescheiterten Backup-Teils nicht am Proxmox-Zugang haengen bleiben.
    Hat `nodvard-backup@pve` schon einen Token ohne Privilegientrennung, bekaeme der sofort alle Backup-Rechte: dann
    legt Nodvard Deck nichts an und sagt es."""
    storages: list[str] = []
    pool: str | None = None
    try:
        backup_rows: list[dict[str, Any]] = []
        if any(isinstance(u, dict) and u.get("userid") == BACKUP_USER for u in users):
            backup_rows = _token_rows(await api.ok("GET", f"{_user_path(BACKUP_USER)}/token"))
        rows_by_user[BACKUP_USER] = backup_rows
        unseparated = _privsep_off({BACKUP_USER: backup_rows})
        if unseparated or any(not _privsep(r) for r in backup_rows):
            shown = ", ".join(f"„{t}“" for t in unseparated) or "ein Token"
            notes.append(
                f"Für {BACKUP_USER} gibt es einen Token ohne Privilegientrennung ({shown}) – er bekäme sofort alle "
                "Rechte für Proxmox-Backups. Deshalb hat Nodvard Deck dafür keinen Zugang angelegt. Lösch den Token in "
                "Proxmox (Rechenzentrum → Rechte → API-Token) und verbinde noch einmal."
            )
            return None
        token_name = _free_token_name(TOKEN_BASE, {str(r.get("tokenid")) for r in backup_rows})
        if token_name is None:
            notes.append(_BACKUP_FAILED_DEFAULT)
            return None
        full_token_id = f"{BACKUP_USER}!{token_name}"
        role = await _ensure_role(api, by_id, BACKUP_ROLES[access], BACKUP_PRIVILEGES[access], available, created)
        roles.append(role)
        if access == "full":
            roles.append(await _ensure_role(api, by_id, STORE_ROLE, STORE_PRIVILEGES, available, created))
            storages = await _backup_storages(api)
            pool = await _ensure_pool(api, acl, created, notes)
            if pool is not None:
                roles.append(await _ensure_role(api, by_id, RESTORE_ROLE, RESTORE_PRIVILEGES, available, created))
        await _ensure_user(api, users, BACKUP_USER, comment="Nodvard Deck: Proxmox-Backups (nur API-Token)", created=created)
        grants = [("/", role.name)] + [(f"/storage/{s}", STORE_ROLE) for s in storages]
        if pool is not None:
            grants.append((f"/pool/{pool}", RESTORE_ROLE))
        for path, role_name in grants:
            await _grant(api, acl, created, path=path, role=role_name, user=BACKUP_USER)
        token_value = await _create_token(
            api, BACKUP_USER, token_name, comment="Nodvard Deck (Proxmox-Backups)", created=created, new_tokens=new_tokens,
        )
        for path, role_name in grants:
            await _grant(api, acl, created, path=path, role=role_name, token=full_token_id)
        await _probe_backup(ctx, target, api.pin, full_token_id, token_value)
    except PasswordConnectError as exc:
        if exc.code == "certificate_changed_after_login":
            raise
        notes.append(_BACKUP_FAILED.get(exc.code, _BACKUP_FAILED_DEFAULT))
        mine = [t for t in new_tokens if t.user == BACKUP_USER]
        for token in mine:
            new_tokens.remove(token)
        token_notes, _leftover = await _undo_tokens(api, mine, created, run)
        notes.extend(token_notes)
        return None
    if access == "full" and not storages:
        notes.append(
            "In Proxmox gibt es noch keinen Speicher für Backups. Kommt einer dazu, braucht der Zugang für "
            f"Proxmox-Backups ({BACKUP_USER}) dort die Rolle {STORE_ROLE} (oder du verbindest noch einmal mit Passwort)."
        )
    token = next(t for t in new_tokens if t.user == BACKUP_USER)
    return _BackupAccess(receiver, token, token_value, role, storages, pool)


def _foreign_backup_secret_note(label: Any) -> str:
    shown = label if isinstance(label, str) and BACKUP_LABEL.fullmatch(label) else "backups-token:…"
    return (
        f"Für Proxmox-Backups liegt im Tresor schon ein Geheimnis „{shown}“, das nicht von Proxmox-Backups stammt (etwa "
        "von Hand angelegt) – dort hat Nodvard Deck deshalb nichts gespeichert. Lösch es selbst über die "
        "Tresor-Schnittstelle (DELETE /api/v1/secrets/{id}) oder nimm einen anderen Kurznamen."
    )


# --- Alte Tokens ---------------------------------------------------------------------------------------------------


def remove_command(user: str, token_name: str) -> str:
    """Der Befehl zum Loeschen (auf dem Proxmox-Server als root). Nur fuer Namen nach `DECK_TOKEN_PATTERN`."""
    return f"pveum user token remove {user} {token_name}"


async def tokens_in_use(ctx: Any) -> tuple[set[str], bool]:
    """Token-IDs, die Nodvard Deck noch benutzt: alle Proxmox-Verbindungen und (wenn die Erweiterung an ist) alle von
    Proxmox-Backups. Zweiter Wert: ob Proxmox-Backups gefragt werden konnte."""
    settings = await ctx.settings.get()
    in_use = {
        str(c.get("token_id")) for c in settings.get("connections") or [] if isinstance(c, dict) and c.get("token_id")
    }
    asked = False
    for receiver in await _backup_receivers(ctx):
        try:
            in_use |= {str(t) for t in await receiver.received_token_ids()}
            asked = True
        except Exception as exc:  # noqa: BLE001 - dann gelten Backup-Tokens als benutzt (siehe old_tokens_of)
            ctx.logger.warning("Proxmox-Backups: Token-Liste nicht lesbar (%s)", type(exc).__name__)
    return in_use, asked


def old_tokens_of(
    rows_by_user: dict[str, Sequence[dict[str, Any]]], in_use: set[str], *, backups_asked: bool,
) -> list[dict[str, Any]]:
    """Tokens von `nodvard@pve` und `nodvard-backup@pve`, die Nodvard Deck angelegt hat (`DECK_TOKEN_PATTERN`) und die
    keine Verbindung mehr benutzt. Fremde Tokens nie. Konnte Proxmox-Backups nicht gefragt werden (aus), zaehlen die
    Tokens von `nodvard-backup@pve` nie als alt -- sie koennten dort noch stecken.

    Loeschen kann Nodvard Deck sie nicht selbst: ein API-Token darf Tokens seines eigenen Benutzers nur mit
    `User.Modify` loeschen (`userid-param self` gilt fuer die Token-ID nicht). Mit diesem Recht koennte der Token aber
    auch Tokens ohne Privilegientrennung anlegen oder die eines anderen umstellen und so alle Rechte des Benutzers
    bekommen, oder den Benutzer loeschen. Deshalb nur die Liste mit dem `pveum`-Befehl."""
    out: list[dict[str, Any]] = []
    for user in (PVE_USER, BACKUP_USER):
        if user == BACKUP_USER and not backups_asked:
            continue
        for row in rows_by_user.get(user) or ():
            name = str(row.get("tokenid") or "")
            if not DECK_TOKEN_PATTERN.fullmatch(name):
                continue
            token_id = f"{user}!{name}"
            if token_id in in_use:
                continue
            out.append({
                "token_id": token_id, "user": user, "name": name, "privsep": _privsep(row),
                "command": remove_command(user, name),
            })
    return sorted(out, key=lambda t: (t["user"] != PVE_USER, len(t["name"]), t["name"]))


def _privsep_off_note(token_ids: Sequence[str]) -> str | None:
    if not token_ids:
        return None
    shown = ", ".join(f"„{t}“" for t in token_ids)
    which = f"den Token {shown}" if len(token_ids) == 1 else f"die Tokens {shown}"
    return (
        f"Für {PVE_USER} gibt es {which} ohne Privilegientrennung: Er darf alles, was der Benutzer darf – mehr als der "
        "neue Token. Brauchst du ihn nicht, lösch ihn in Proxmox (Rechenzentrum → Rechte → API-Token)."
    )


# --- Einlesen nach dem Verbinden -------------------------------------------------------------------------------------


def _start_discovery(ctx: Any) -> bool:
    """Den Proxmox-Abgleich sofort anstossen, im Hintergrund (die Antwort wartet nicht darauf). Scheitert er, bleibt die
    Verbindung trotzdem gespeichert; der Zeitplan (alle 5 Minuten) holt es nach."""

    async def discover() -> None:
        try:
            await ctx.scheduler.trigger("discovery")
        except Exception as exc:  # noqa: BLE001 - das Einlesen darf das Verbinden nicht kippen
            ctx.logger.warning("Proxmox mit Passwort verbinden: Einlesen danach fehlgeschlagen (%s)", type(exc).__name__)

    job = discover()
    try:
        ctx.spawn(job, name="proxmox-einlesen-nach-verbinden", restart=False)
    except Exception as exc:  # noqa: BLE001
        job.close()
        ctx.logger.warning("Proxmox mit Passwort verbinden: Einlesen nicht gestartet (%s)", type(exc).__name__)
        return False
    return True


# --- Der eigentliche Ablauf nach der Anmeldung ------------------------------------------------------------------------


async def _set_up(
    ctx: Any, api: _Proxmox, target: _Target, *, trusted: bool, plan: _Plan, access: str, backups: bool | None,
    run: _Run,
) -> dict[str, Any]:
    """`backups`: `True` -- auch Proxmox-Backups verbinden (ist es aus, sagt die Antwort das), `None` -- nur, wenn es
    an ist (still, wenn nicht), `False` -- nicht."""
    created = _Created()
    run.detail["created"] = created
    notes: list[str] = []
    roles: list[_Role] = []

    # Rolle: nur Rechte, die es in dieser Version gibt (die Rolle Administrator hat alle).
    role_rows = await api.ok("GET", "/access/roles")
    role_rows = role_rows if isinstance(role_rows, list) else []
    by_id = {str(r.get("roleid")): r for r in role_rows if isinstance(r, dict)}
    available = _privs_of(by_id["Administrator"]) if "Administrator" in by_id else None
    deck_role = await _ensure_role(api, by_id, ROLES[access], PRIVILEGES[access], available, created)
    roles.append(deck_role)
    if deck_role.extra:
        run.detail["extra_privileges"] = deck_role.extra

    # Benutzer ohne Passwort.
    users = await api.ok("GET", "/access/users")
    users = list(users) if isinstance(users, list) else []
    await _ensure_user(api, users, PVE_USER, comment="Nodvard Deck (nur API-Token)", created=created)
    acl = await _read_acl(api)
    await _grant(api, acl, created, path="/", role=deck_role.name, user=PVE_USER)

    # Token: immer ein neuer, nie einen vorhandenen ersetzen.
    token_rows = _token_rows(await api.ok("GET", f"{_user_path(PVE_USER)}/token"))
    rows_by_user: dict[str, list[dict[str, Any]]] = {PVE_USER: token_rows}
    taken = {str(t.get("tokenid")) for t in token_rows}
    token_name = _free_token_name(TOKEN_BASE, taken)
    if token_name is None:
        raise PasswordConnectError("proxmox_error")
    privsep_off = _privsep_off({PVE_USER: token_rows})
    if privsep_off:
        run.detail["privsep_off"] = privsep_off

    # Name der Verbindung (vor dem Anlegen des Tokens, damit es danach nichts mehr zu entscheiden gibt).
    settings = await ctx.settings.get()
    connections = list(settings.get("connections") or [])
    name = plan.name or _derive_name(await _proxmox_name(api), connections)
    full_token_id = f"{PVE_USER}!{token_name}"
    run.detail.update({"connection": name, "token_id": full_token_id})
    await _clear_orphan_secret(ctx, name, connections)

    # Proxmox-Backups: erst fragen, ob es den Zugang uebernehmen kann -- angelegt wird dafuer erst danach.
    receiver: Any = None
    if backups is not False:
        receivers = await _backup_receivers(ctx)
        if not receivers:
            if backups:
                notes.append("Proxmox-Backups ist nicht eingeschaltet – dafür hat Nodvard Deck keinen Zugang angelegt.")
        else:
            try:
                conflict = await receivers[0].connection_conflict(name=name, base_url=target.base_url)
            except Exception as exc:  # noqa: BLE001 - dann eben ohne Backup-Zugang
                ctx.logger.warning("Proxmox-Backups: Pruefung fehlgeschlagen (%s)", type(exc).__name__)
                conflict = "Ob Proxmox-Backups den Zugang übernehmen kann, ließ sich nicht prüfen."
            if conflict:
                notes.append(f"{_clean_sentence(conflict)} Für Proxmox-Backups hat Nodvard Deck deshalb keinen Zugang angelegt.")
            else:
                receiver = receivers[0]

    new_tokens: list[_NewToken] = []
    backup: _BackupAccess | None = None
    # Ein bestaetigtes (nicht anerkanntes) Zertifikat bleibt gemerkt: die Verbindung prueft danach genau diesen
    # Fingerabdruck statt gar nichts. Ein anerkanntes braucht das nicht (normale Pruefung).
    pin = None if trusted else api.pin
    try:
        token_value = await _create_token(
            api, PVE_USER, token_name, comment="Nodvard Deck", created=created, new_tokens=new_tokens,
        )
        await _grant(api, acl, created, path="/", role=deck_role.name, token=full_token_id)
        nodes = await _probe(ctx, target, api.pin, full_token_id, token_value)
        if receiver is not None:
            backup = await _set_up_backups(
                ctx, api, target, receiver, access=access, by_id=by_id, available=available, acl=acl, users=users,
                rows_by_user=rows_by_user, created=created, new_tokens=new_tokens, notes=notes, roles=roles, run=run,
            )
        try:
            replaced = await _save(
                ctx, name=name, base_url=target.base_url, token_id=full_token_id, token_value=token_value, pin=pin,
            )
        except PasswordConnectError:
            raise
        except Exception as exc:  # noqa: BLE001 - nie ein 500 mit Traceback; die neuen Tokens gehen wieder weg
            ctx.logger.warning("Proxmox mit Passwort verbinden: Speichern fehlgeschlagen (%s)", type(exc).__name__)
            raise PasswordConnectError("save_failed") from None
        finally:
            del token_value
    except asyncio.CancelledError:
        ctx.logger.warning("Proxmox mit Passwort verbinden: abgebrochen, neue Tokens bleiben evtl. in Proxmox")
        raise
    except Exception as exc:  # noqa: BLE001 - jeder Fehler nach dem Anlegen raeumt die neuen Tokens weg
        problem = exc if isinstance(exc, PasswordConnectError) else PasswordConnectError("connection_failed")
        if problem is not exc:
            ctx.logger.warning("Proxmox mit Passwort verbinden: unerwarteter Fehler (%s)", type(exc).__name__)
        token_notes, leftover = await _undo_tokens(api, list(new_tokens), created, run)
        raise problem.extended(notes=token_notes, leftover=leftover) from None

    # Proxmox-Backups: erst jetzt eintragen (die Proxmox-Verbindung steht). Scheitert es, geht nur der Backup-Token weg.
    backups_out: dict[str, Any] | None = None
    if backup is not None:
        try:
            received = await backup.receiver.receive_connection(
                name=name, base_url=target.base_url, token_id=backup.token.token_id,
                token_secret=SecretStr(backup.token_value), tls_insecure_skip_verify=False, pinned_sha256=pin,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - der Text bleibt draussen; die Proxmox-Verbindung bleibt
            ctx.logger.warning("Proxmox-Backups: Zugang nicht gespeichert (%s)", type(exc).__name__)
            notes.append(
                _foreign_backup_secret_note(getattr(exc, "label", None)) if isinstance(exc, SecretNotOwned)
                else "Den Zugang für Proxmox-Backups konnte Nodvard Deck nicht speichern."
            )
            token_notes, _leftover = await _undo_tokens(api, [backup.token], created, run)
            notes.extend(token_notes)
        else:
            received = received if isinstance(received, dict) else {}
            backup_name = _clean_sentence(received.get("name") or name, 64)
            backup_replaced = received.get("replaced_token_id")
            backup_replaced = _clean_sentence(backup_replaced, 200) if backup_replaced else None
            backups_out = {
                "name": backup_name, "token_id": backup.token.token_id, "replaced_token_id": backup_replaced,
                "privileges": backup.role.privileges, "storages": backup.storages, "pool": backup.pool,
                "tls_insecure_skip_verify": bool(received.get("tls_insecure_skip_verify", False)),
                "tls_fingerprint_sha256": _clean_sentence(received.get("tls_fingerprint_sha256"), 100) or None,
                "enabled": received.get("enabled") is not False,
            }
            run.detail.update({
                "backup_connection": backup_name, "backup_token_id": backup.token.token_id,
                "backup_storages": backup.storages, "restore_pool": backup.pool,
            })
            if backup_replaced:
                notes.append(
                    f"In Proxmox-Backups nutzt „{backup_name}“ jetzt den neuen Token. Der bisherige ({backup_replaced}) "
                    "bleibt in Proxmox bestehen."
                )
            backup_pin = backups_out["tls_fingerprint_sha256"]
            if pin and backup_pin == pin and received.get("replaced_fingerprint"):
                notes.append(
                    f"In Proxmox-Backups merkt sich „{backup_name}“ jetzt den neuen Fingerabdruck des Zertifikats; der "
                    "bisherige gilt nicht mehr."
                )
            if (pin and backup_pin != pin) or (not pin and backup_pin):
                notes.append(
                    f"In Proxmox-Backups prüft „{backup_name}“ das Zertifikat weiter so, wie dort eingestellt. Klappt die "
                    "Verbindung damit nicht, bestätige dort unter „Verbindungen verwalten“ den Fingerabdruck."
                )
            if not backups_out["enabled"]:
                notes.append(
                    f"In Proxmox-Backups ist „{backup_name}“ ausgeschaltet und bleibt es – einschalten kannst du sie dort "
                    "unter „Verbindungen verwalten“."
                )
        finally:
            backup.token_value = ""

    if replaced:
        notes.insert(0, (
            f"Die Verbindung „{name}“ nutzt jetzt den neuen Token. Der bisherige ({replaced}) bleibt in Proxmox "
            "bestehen – lösch ihn dort unter Rechenzentrum → Rechte → API-Token, wenn ihn nichts mehr braucht."
        ))
    missing = sorted({p for r in roles for p in r.missing})
    if missing:
        notes.append(
            "Diese Rechte gibt es in deiner Proxmox-Version nicht und wurden weggelassen: " + ", ".join(missing) + "."
            + (" Echte VM-Adressen trägst du dann von Hand ein." if "VM.GuestAgent.Audit" in missing else "")
        )
    for role in roles:
        if role.extra:
            notes.append(
                f"Die Rolle {role.name} gab es schon, und sie erlaubt mehr, als Nodvard Deck braucht: "
                + ", ".join(role.extra)
                + ". Der neue Token bekommt diese Rechte mit. Hast du sie nicht bewusst vergeben, nimm sie in Proxmox "
                "unter Rechenzentrum → Rechte → Rollen weg."
            )
    privsep_note = _privsep_off_note(privsep_off)
    if privsep_note:
        notes.append(privsep_note)
    if pin:
        notes.append(
            "Nodvard Deck hat sich den Fingerabdruck des Zertifikats gemerkt und spricht nur mit dem Server, der genau "
            "dieses Zertifikat zeigt. Tauschst du das Zertifikat in Proxmox, bestätigst du den neuen Fingerabdruck "
            "unter „Verbindungen verwalten“."
        )

    try:  # die Verbindung steht schon: eine fehlende Liste alter Tokens darf daran nichts aendern
        in_use, backups_asked = await tokens_in_use(ctx)
        fresh = {full_token_id} | ({backup.token.token_id} if backup is not None else set())
        old = old_tokens_of(rows_by_user, in_use | fresh, backups_asked=backups_asked)
    except Exception as exc:  # noqa: BLE001
        ctx.logger.warning("Proxmox mit Passwort verbinden: alte Tokens nicht ermittelt (%s)", type(exc).__name__)
        old = []
    if old:
        run.detail["old_tokens"] = [t["token_id"] for t in old]
    discovery = _start_discovery(ctx)
    run.detail.update({"replaced": bool(replaced), "created": list(created)})
    return {
        "status": "connected",
        "connection": {
            "name": name, "base_url": target.base_url, "token_id": full_token_id, "tls_insecure_skip_verify": False,
            "tls_fingerprint_sha256": pin, "tls_fingerprint_invalid": False, "enabled": True, "has_token": True,
        },
        "created": list(created),
        "nodes": nodes,
        "access": access,
        "privileges": deck_role.privileges,
        "replaced_token_id": replaced,
        "backups": backups_out,
        "old_tokens": old,
        "discovery": "started" if discovery else "not_started",
        "notes": notes,
    }


async def _save(ctx: Any, *, name: str, base_url: str, token_id: str, token_value: str, pin: str | None) -> str | None:
    """Verbindung und Token-Geheimnis eintragen, wie das Einstellungsformular: erst die Verbindung, dann das
    Geheimnis. Liefert die bisherige Token-ID, wenn eine vorhandene Verbindung einen neuen Zugang bekam.

    `pin`: der bestaetigte Fingerabdruck eines nicht anerkannten Zertifikats -- er wird gemerkt, "Selbstsigniertes
    Zertifikat erlauben" geht aus. Ohne (anerkanntes Zertifikat) gilt die normale Pruefung, ein alter Fingerabdruck
    faellt weg (der Server zeigt jetzt ein anderes Zertifikat, die Verbindung kaeme sonst nicht mehr durch).

    Ein fremdes Geheimnis unter dem Label (nicht von dieser Erweiterung, etwa von Hand im Tresor angelegt) wird nie
    angefasst: bei einer neuen Verbindung faellt das vor jeder Aenderung der Einstellungen auf, bei einer vorhandenen
    beim Ersetzen -- dann kommen die Einstellungen auf den alten Stand zurueck (`secret_foreign`).

    Eine vorhandene Verbindung mit Geheimnis bekommt den neuen Wert in einem Schritt (`ctx.secrets.replace`):
    scheitert das, bleibt der alte Wert, und die Einstellungen kommen auf den alten Stand zurueck -- die Verbindung
    arbeitet weiter wie vorher. Der Aufrufer loescht dann den neuen Token in Proxmox."""
    label = f"{TOKEN_LABEL}:{name}"
    settings = await ctx.settings.get()
    before = [dict(c) for c in settings.get("connections") or [] if isinstance(c, dict)]
    connections = [dict(c) for c in before]
    existing = next((c for c in connections if c.get("name") == name), None)
    if existing is not None and not same_target(str(existing.get("base_url") or ""), base_url):
        # Inzwischen (waehrend der Anmeldung) hat jemand den Namen fuer einen anderen Server vergeben.
        raise PasswordConnectError("name_taken", name=name)
    replaced: str | None = None
    tls: dict[str, Any] = {"tls_insecure_skip_verify": False}
    if pin:
        tls[FINGERPRINT_FIELD] = pin
    if existing is None:
        connections.append({"name": name, "base_url": base_url, "token_id": token_id, **tls, "enabled": True})
    else:
        replaced = str(existing.get("token_id") or "") or None
        existing.pop(FINGERPRINT_FIELD, None)
        existing.update({"token_id": token_id, **tls, "enabled": True})
    keep_secret = existing is not None and await ctx.secrets.exists(label)
    if not keep_secret:
        # Ein Geheimnis unter diesem Namen gehoert zu einer frueher entfernten Verbindung (anderes Ziel) -- weg, bevor
        # sich etwas aendert; ein fremdes bricht hier ab.
        foreign = False
        try:
            await ctx.secrets.delete(label)
        except SecretNotOwned:
            foreign = True
        if foreign:
            raise PasswordConnectError("secret_foreign", name=label)
    problem: BaseException | None = None
    try:
        await ctx.settings.set({**settings, "connections": connections})
        if keep_secret:
            await ctx.secrets.replace(label=label, value=token_value)
        else:
            await ctx.secrets.create(
                label=label, kind="generic", value=token_value, description="API-Token (mit Passwort verbunden)",
            )
    except Exception as exc:  # noqa: BLE001 - erst den alten Stand zurueck, dann melden
        problem = exc
    if problem is not None:
        try:
            await ctx.settings.set({**settings, "connections": before})
        except Exception as exc:  # noqa: BLE001 - der urspruengliche Fehler zaehlt
            ctx.logger.warning("Proxmox mit Passwort verbinden: alter Stand nicht wiederhergestellt (%s)", type(exc).__name__)
        if isinstance(problem, SecretNotOwned):
            raise PasswordConnectError("secret_foreign", name=label)
        raise problem
    return replaced if replaced != token_id else None


# ---------------------------------------------------------------------------
# Routen
# ---------------------------------------------------------------------------


class CertificateIn(BaseModel):
    base_url: str = Field(max_length=300)


class ConnectIn(BaseModel):
    base_url: str = Field(max_length=300)
    username: str = Field(default="root@pam", max_length=128)
    password: SecretStr = Field(max_length=512)
    name: str | None = Field(default=None, max_length=64)
    fingerprint: str | None = Field(default=None, max_length=200)
    """Der Fingerabdruck, den die Person mit dem in Proxmox verglichen hat (bei einem nicht anerkannten Zertifikat)."""
    access: Literal["full", "read"] = "full"
    """Vorgabe "Ansehen und steuern"."""
    backups: bool | None = None
    """Auch einen Zugang fuer Proxmox-Backups anlegen: `true` (ist die Erweiterung aus, sagt die Antwort das), `false`
    (nicht), ohne Angabe: wenn die Erweiterung an ist."""


class TfaIn(BaseModel):
    flow: str = Field(max_length=64)
    code: SecretStr = Field(max_length=256)
    kind: Literal["totp", "recovery", "yubico"] = "totp"


def build_router(ctx: ExtensionContext) -> APIRouter:
    """`POST /connect/certificate`, `POST /connect`, `POST /connect/tfa` und `GET /connections/{name}/old-tokens` --
    eingehaengt mit derselben Berechtigung wie die uebrigen Verbindungs-Routen (`secrets.write`, Admin/Owner): sie
    schreiben dasselbe bzw. zeigen Zugaenge."""
    router = APIRouter()

    @router.post("/connect/certificate")
    async def connect_certificate(payload: CertificateIn, actor: Actor = Depends(ctx.api.current_actor)) -> Any:  # noqa: B008
        try:
            return await certificate(ctx, actor, payload.base_url)
        except PasswordConnectError as exc:
            return exc.response()

    @router.post("/connect")
    async def connect_with_password(payload: ConnectIn, actor: Actor = Depends(ctx.api.current_actor)) -> Any:  # noqa: B008
        try:
            return await start(
                ctx, actor, base_url=payload.base_url, username=payload.username, password=payload.password,
                name=payload.name, fingerprint=payload.fingerprint, access=payload.access, backups=payload.backups,
            )
        except PasswordConnectError as exc:
            return exc.response()

    @router.post("/connect/tfa")
    async def connect_tfa(payload: TfaIn, actor: Actor = Depends(ctx.api.current_actor)) -> Any:  # noqa: B008
        try:
            return await finish_tfa(ctx, actor, flow=payload.flow, code=payload.code, kind=payload.kind)
        except PasswordConnectError as exc:
            return exc.response()

    @router.get("/connections/{name}/old-tokens")
    async def old_tokens(name: str) -> dict[str, Any]:
        """Tokens, die Nodvard Deck fuer `nodvard@pve` und `nodvard-backup@pve` angelegt hat und die keine Verbindung
        mehr benutzt -- mit dem `pveum`-Befehl zum Loeschen (siehe `old_tokens_of`, warum es keinen Knopf gibt). Gelesen
        mit dem Token dieser Verbindung (`GET /access/users/<id>`, braucht `Sys.Audit`)."""
        try:
            connector = await build_connector(ctx, name)
        except RuntimeError:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=f"Verbindung '{name}' existiert nicht.") from None
        problem: str | None = None
        rows_by_user: dict[str, list[dict[str, Any]]] = {}
        try:
            rows_by_user[PVE_USER] = _token_rows(await connector.user_tokens(PVE_USER))
        except ProxmoxApiError as exc:
            problem = str(exc)
        if problem is not None:
            raise HTTPException(status_code=http_status.HTTP_502_BAD_GATEWAY, detail=problem)
        in_use, backups_asked = await tokens_in_use(ctx)
        if backups_asked:
            try:
                rows_by_user[BACKUP_USER] = _token_rows(await connector.user_tokens(BACKUP_USER))
            except ProxmoxApiError:
                rows_by_user[BACKUP_USER] = []  # den Benutzer gibt es (noch) nicht
        return {
            "connection": name, "users": [PVE_USER, BACKUP_USER] if backups_asked else [PVE_USER],
            "tokens": old_tokens_of(rows_by_user, in_use, backups_asked=backups_asked),
            "privsep_off": _privsep_off(rows_by_user),
            "backups_checked": backups_asked,
        }

    return router
