"""Zulässige Fehlerarten ohne Fehlertexte."""

from enum import StrEnum


class ErrorCode(StrEnum):
    SSH_TIMEOUT = "ssh.timeout"
    BACKUP_UNWRITABLE = "backup.ziel_nicht_beschreibbar"
