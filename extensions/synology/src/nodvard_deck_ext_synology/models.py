"""Antwortformen der Erweiterung; alle Texte stammen aus DSM und sind gekürzt. Die Seriennummer wird nie übernommen."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Note(BaseModel):
    """Hinweis zu einem Teil, der sich nicht lesen ließ; die anderen Teile bleiben davon unberührt."""

    kind: str = Field(description="error | no_rights | unavailable | partial (nur ein Teil ließ sich lesen)")
    message: str = Field(description="Deutscher Satz: was los ist und was man tun kann.")


class SystemInfo(BaseModel):
    model: str | None = Field(description="Modell, z. B. DS220+.")
    dsm_version: str | None = Field(description="Version von DSM, z. B. DSM 7.2.1-69057.")
    uptime_seconds: int | None = Field(description="Laufzeit seit dem letzten Start in Sekunden.")
    temperature: int | None = Field(description="Temperatur des Geräts in °C; leer, wenn DSM keine meldet.")
    temperature_warn: bool | None = Field(description="Ob DSM die Temperatur als zu hoch meldet.")
    ram_mb: int | None = Field(description="Eingebauter Arbeitsspeicher in MB.")
    clock: str | None = Field(None, description="Uhrzeit der Synology beim Abruf (Ortszeit, ohne Zone), z. B. 2026-10-17T14:05.")


class Load(BaseModel):
    cpu_percent: float | None = Field(description="Auslastung des Prozessors in Prozent (Benutzer + System + Sonstiges).")
    ram_percent: float | None = Field(description="Belegter Arbeitsspeicher in Prozent.")


class Volume(BaseModel):
    id: str = Field(description="Kennung in DSM, z. B. volume_1.")
    name: str = Field(description="Anzeigename, z. B. Volume 1.")
    status: str = Field(description="Zustand in DSM, z. B. normal, degraded, crashed.")
    status_label: str = Field(description="Zustand auf Deutsch.")
    tone: str = Field(description="good | warn | danger | neutral")
    fs_type: str | None = Field(description="Dateisystem, z. B. btrfs.")
    used: int | None = Field(description="Belegt in Byte.")
    total: int | None = Field(description="Gesamtgröße in Byte.")
    percent: int | None = Field(description="Belegung von 0 bis 100; leer, wenn Größe unbekannt oder 0.")
    pool_id: str | None = Field(None, description="Kennung des Speicherpools, z. B. reuse_1.")


class Pool(BaseModel):
    id: str = Field(description="Kennung in DSM, z. B. reuse_1.")
    name: str = Field(description="Anzeigename, z. B. Speicherpool 1.")
    description: str | None = Field(description="Beschreibung aus DSM, falls vergeben.")
    raid: str | None = Field(description="Art des Verbunds, z. B. SHR, SHR-2, RAID 5.")
    status: str = Field(description="Zustand in DSM, z. B. normal, degraded, crashed.")
    status_label: str = Field(description="Zustand auf Deutsch.")
    tone: str = Field(description="good | warn | danger | neutral")
    disks: int | None = Field(description="Anzahl der Festplatten im Pool.")
    volumes: list[str] = Field(description="Namen der Volumes im Pool, z. B. Volume 1.")
    total: int | None = Field(description="Größe in Byte.")


class Disk(BaseModel):
    id: str
    name: str = Field(description="Anzeigename, z. B. Laufwerk 1.")
    model: str | None = Field(description="Hersteller und Modell.")
    kind: str = Field("hdd", description="hdd | ssd | nvme")
    status: str = Field(description="Zustand in DSM, z. B. normal, warning, crashed, not_use.")
    status_label: str = Field(description="Zustand auf Deutsch.")
    tone: str = Field(description="good | warn | danger | neutral; schlechter, wenn SMART oder eine Grenze anschlägt.")
    temp: int | None = Field(description="Temperatur in °C.")
    temp_warn: bool = Field(False, description="Ob die Temperatur die Grenze aus den Einstellungen erreicht.")
    smart_status: str | None = Field(description="SMART-Zustand in DSM, z. B. normal.")
    smart_label: str | None = Field(description="SMART-Zustand auf Deutsch.")
    smart_tone: str = Field(description="good | warn | danger | neutral")
    warning: str | None = Field(description="Deutscher Satz, wenn eine Grenze überschritten ist (defekte Sektoren, Restlebensdauer).")


class Update(BaseModel):
    available: bool
    version: str | None = Field(description="Version des bereitstehenden Updates, z. B. DSM 7.2.2-72806.")


class BackupTask(BaseModel):
    id: str
    name: str
    state: str | None = Field(description="Zustand der Aufgabe in DSM, z. B. backupable, unauth, broken.")
    running: bool = Field(description="Ob gerade gesichert wird.")
    status_read: bool = Field(True, description="Ob sich der Stand der Aufgabe lesen ließ.")
    last_result: str | None = Field(description="Ergebnis des letzten Laufs in DSM, z. B. done, failed, partial.")
    result_label: str = Field(description="Ergebnis oder Zustand auf Deutsch.")
    tone: str = Field(description="good | warn | danger | neutral")
    last_run: str | None = Field(description="Beginn des letzten Laufs, Ortszeit der Synology, z. B. 2026-10-16T03:00.")
    last_success: str | None = Field(description="Letzter erfolgreicher Lauf, Ortszeit der Synology.")
    next_run: str | None = Field(description="Nächster geplanter Lauf, Ortszeit der Synology; leer ohne Zeitplan.")
    overdue: bool = Field(description="Ob ein geplanter Lauf ausgeblieben ist oder lange keiner gelang.")
    problem: str | None = Field(description="Deutscher Satz, wenn mit der Aufgabe etwas nicht stimmt.")


class Problem(BaseModel):
    """Etwas, das Aufmerksamkeit braucht; dieselbe Liste speist die Seite und die Meldungen."""

    key: str = Field(description="Feste Kennung, z. B. pool:reuse_1 oder backup-failed:3.")
    subject: str = Field(description="Worum es geht, z. B. Speicherpool 1 oder Hyper Backup „Nacht“.")
    severity: str = Field(description="info | warning | critical")
    title: str = Field(description="Kurzer deutscher Titel.")
    text: str = Field(description="Deutscher Satz: was los ist und was man tun kann.")


class Overview(BaseModel):
    state: str = Field(description="ok | not_configured | auth_failed | unreachable | error")
    message: str | None = Field(description="Hinweis auf Deutsch; bei einem anderen Zustand als ok der Grund, sonst leer.")
    url: str | None = Field(description="Die eingetragene Adresse, ohne Pfad der Oberfläche.")
    system: SystemInfo | None
    load: Load | None
    volumes: list[Volume] | None = Field(description="null, wenn der Speicher sich nicht lesen ließ.")
    disks: list[Disk] | None = Field(description="null, wenn der Speicher sich nicht lesen ließ.")
    pools: list[Pool] | None = Field(None, description="null, wenn der Speicher sich nicht lesen ließ.")
    update: Update | None
    backups: list[BackupTask] | None = Field(None, description="Aufgaben von Hyper Backup; null, wenn sie sich nicht lesen ließen.")
    system_note: Note | None
    load_note: Note | None
    storage_note: Note | None
    update_note: Note | None
    backup_note: Note | None = None
    problems: list[Problem] = Field(default_factory=list, description="Was gerade Aufmerksamkeit braucht, das Wichtigste zuerst.")
