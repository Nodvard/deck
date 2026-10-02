"""login_protokoll_namen (eingetippte Anmeldenamen aus alten Protokoll-Eintraegen entfernen)

Aeltere Versionen schrieben bei einer Anmeldung mit unbekanntem Namen den eingetippten Text
ins Protokoll (`audit_log`): bei `login.failed` als Akteur, bei `login.locked` zusaetzlich in
`detail.username`. Wer sein Passwort ins Namensfeld getippt hat, hinterliess es dort im Klartext.
Neue Eintraege enthalten den Text nicht mehr; diese Migration bereinigt die alten, einmalig:
Akteur wird `anonymous` / `unbekannt`, `detail.username` faellt weg. Eintraege zu echten Konten
(Akteur ist eine Konto-Kennung aus `users`) bleiben unberuehrt.

Konten, die es nicht mehr gibt, zaehlen wie unbekannte Namen: aus der Tabelle laesst sich nicht
mehr ablesen, ob der Akteur ein Konto war oder ein eingetippter Text.

Die Aenderung ist nicht umkehrbar (der Text ist danach weg); `downgrade` tut nichts.

Revision ID: f3a9c6d18e24
Revises: d4b7a2c91e63
Create Date: 2026-10-02 10:00:00.000000

"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f3a9c6d18e24'
down_revision: Union[str, Sequence[str], None] = 'd4b7a2c91e63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Gruende, mit denen `login.failed` den eingetippten Text als Akteur trug.
_UNKNOWN_NAME_REASONS = ("Unbekannter Benutzername.", "Eingabe zu lang.")

# Gleiche Antwort wie bei neuen Eintraegen (`services.auth.UNKNOWN_ACTOR`).
_ANONYMOUS_TYPE = "anonymous"
_ANONYMOUS_ID = "unbekannt"

# Nur die Spalten, die hier gebraucht werden (die Migration haengt nicht an den Modellen,
# die sich spaeter aendern duerfen).
_audit_log = sa.table(
    "audit_log",
    sa.column("id", sa.String),
    sa.column("action", sa.String),
    sa.column("actor_type", sa.String),
    sa.column("actor_id", sa.String),
    sa.column("reason", sa.Text),
    sa.column("detail", sa.JSON),
)
# Zum Lesen als Text: eine Zeile mit kaputtem JSON soll die Migration nicht abbrechen.
_audit_detail_text = sa.column("detail", sa.Text)
_users = sa.table("users", sa.column("id", sa.String))


def anonymize_typed_login_names(connection) -> int:
    """Bereinigt die alten Eintraege und gibt zurueck, wie viele geaendert wurden.

    Eigene Funktion, damit ein Test sie auch auf einer Datenbank mit Altdaten aufrufen kann."""
    if connection.dialect.name == "sqlite":
        # Geloeschte Inhalte in der Datenbankdatei mit Nullen ueberschreiben (gilt nur fuer diese
        # Verbindung); sonst koennte der alte Text in freigewordenen Seitenresten stehen bleiben.
        connection.exec_driver_sql("PRAGMA secure_delete = ON")
    not_an_account = _audit_log.c.actor_id.not_in(sa.select(_users.c.id))

    result = connection.execute(
        sa.update(_audit_log)
        .where(
            _audit_log.c.action == "login.failed",
            _audit_log.c.actor_type == "user",
            _audit_log.c.reason.in_(_UNKNOWN_NAME_REASONS),
            not_an_account,
        )
        .values(actor_type=_ANONYMOUS_TYPE, actor_id=_ANONYMOUS_ID)
    )
    changed = max(result.rowcount or 0, 0)

    # `detail` zeilenweise in Python: so geht es auf jeder Datenbank, und eine Zeile, deren
    # `detail` kein Objekt mit `username` ist (oder gar kein JSON), bleibt einfach stehen.
    rows = connection.execute(
        sa.select(_audit_log.c.id, _audit_detail_text).select_from(_audit_log).where(
            _audit_log.c.action == "login.locked",
            _audit_log.c.actor_type == "user",
            not_an_account,
        )
    ).all()
    for row_id, raw in rows:
        try:
            detail = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        except ValueError:
            continue
        if not isinstance(detail, dict) or "username" not in detail:
            continue
        cleaned = {key: value for key, value in detail.items() if key != "username"}
        connection.execute(
            sa.update(_audit_log)
            .where(_audit_log.c.id == row_id)
            .values(actor_type=_ANONYMOUS_TYPE, actor_id=_ANONYMOUS_ID, detail=cleaned)
        )
        changed += 1
    return changed


def upgrade() -> None:
    """Upgrade data."""
    anonymize_typed_login_names(op.get_bind())


def downgrade() -> None:
    """Der gespeicherte Text ist weg und kommt nicht zurueck."""
