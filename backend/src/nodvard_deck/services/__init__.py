"""Orchestrierende Anwendungslogik.

Der Unterschied zu `core/`: `core/` enthaelt reine, DB-freie Bausteine (Hashing, JWT,
RBAC-Auswertung). `services/` verdrahtet sie mit der Datenbank und bildet die
eigentlichen Anwendungsfaelle (Login, Token-Rotation, TOTP-Setup) ab, die Endpunkte in
`api/` dann nur noch aufrufen.
"""
