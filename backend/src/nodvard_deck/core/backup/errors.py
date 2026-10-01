"""Fehler rund um Sicherungen. Jede Meldung ist fuer Menschen geschrieben und enthaelt nie
ein Passwort, einen Schluessel oder sonst etwas Geheimes."""

from __future__ import annotations


class BackupError(Exception):
    """Oberklasse. `str(exc)` ist eine deutsche Meldung fuer die Oberflaeche."""


class DamagedBackup(BackupError):
    """Datei kaputt, abgeschnitten, veraendert oder kein Sicherungsformat."""

    def __init__(self, detail: str = "") -> None:
        text = "Die Sicherung ist beschädigt oder unvollständig."
        super().__init__(f"{text} ({detail})" if detail else text)


class WrongSecret(BackupError):
    def __init__(self) -> None:
        super().__init__("Passwort oder Wiederherstellungsschlüssel passt nicht zu dieser Sicherung.")


class NewerBackup(BackupError):
    def __init__(self, found: int, supported: int) -> None:
        super().__init__(
            f"Die Sicherung stammt aus einer neueren Version (Format {found}, unterstützt bis {supported}). "
            "Bitte zuerst Nodvard Deck aktualisieren."
        )


class TooExpensive(BackupError):
    """Die Datei verlangt eine Schluesselberechnung jenseits unserer Deckel (Schutz vor
    absichtlich teuren Dateien, die den Rechner minutenlang beschaeftigen wuerden)."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"Die Sicherung verlangt eine ungewöhnlich aufwendige Schlüsselberechnung ({detail}) und wird abgelehnt.")


class BackupTooLarge(BackupError):
    """Eine Obergrenze (Upload, entpackte Groesse, Zahl der Dateien) wurde ueberschritten."""


class NotEnoughSpace(BackupError):
    """Nicht genug freier Speicher im Datenordner oder Zielordner."""


class UnusableBackup(BackupError):
    """Die Sicherung ist in Ordnung, laesst sich hier aber nicht einspielen (zu neue oder
    unbekannte Datenbankstaende, fehlende Erweiterung, nicht von uns angelegte Datenbank-
    Objekte). Die Meldung sagt, woran es liegt."""


class NotSqlite(BackupError):
    def __init__(self) -> None:
        super().__init__("Sicherungen gibt es nur, wenn die Datenbank eine SQLite-Datei ist.")
