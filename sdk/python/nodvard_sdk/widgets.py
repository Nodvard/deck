"""Deklarative Widget-Spezifikation.

**Die Regel: kein Widget ohne deklarative Form.** Eine Extension liefert React; die
Flutter-App kann React nicht ausführen. Wäre das Widget-Format Code, sähe die Android-App
von jeder Extension nichts — und der Grundsatz "Web und App teilen sich eine API" wäre
mit der ersten Extension gebrochen.

Deshalb beschreibt eine Extension *was* angezeigt wird (Typ + Feldbindungen), nicht *wie*
es gezeichnet wird. React und Flutter implementieren beide denselben endlichen Satz an
View-Typen.

Siehe docs/02-EXTENSION-API.md §4.
"""

from __future__ import annotations

import enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Template-Ausdrücke
# ---------------------------------------------------------------------------
# Absichtlich winzig und ohne Logik: "{{ feld.pfad | filter }}".
# Keine Bedingungen, keine Schleifen, keine Ausdrücke — sonst bräuchte die Flutter-Seite
# einen Interpreter, und man hätte sich eine zweite Programmiersprache eingehandelt.

Template = str

ALLOWED_FILTERS = frozenset(
    {
        "relative",   # 2026-09-13T08:00Z -> "vor 3 Std."
        "datetime",
        "date",
        "bytes",
        "percent",
        "number",
        "duration",
        "tone",       # Wert -> good | warn | danger | neutral
        "truncate",
        "upper",
        "lower",
    }
)


class Tone(str, enum.Enum):
    NEUTRAL = "neutral"
    GOOD = "good"
    WARN = "warn"
    DANGER = "danger"
    ACCENT = "accent"


class GridSize(BaseModel):
    w: int = 1
    h: int = 1
    min_w: int = 1
    min_h: int = 1


class Refresh(BaseModel):
    """Wie das Widget aktuell bleibt.

    `interval_s` ist der Rückfallweg; `ws_channel` ist der bevorzugte — auf dem Telefon
    ist Polling Akkulaufzeit.
    """

    interval_s: int | None = None
    ws_channel: str | None = None


class WidgetAction(BaseModel):
    id: str
    label: str
    endpoint: Template          # relativ zu /api/v1/ext/<ext_id>/
    method: Literal["POST", "PUT", "DELETE"] = "POST"
    body: dict[str, Any] | None = None
    confirm: bool = False
    confirm_text: str | None = None
    style: Literal["primary", "secondary", "danger"] = "secondary"
    permissions: list[str] = Field(default_factory=list)
    show_if: Template | None = None
    """Knopf nur zeigen, wenn das Template etwas "Wahres" ergibt (nicht leer, nicht
    "false"/"0"/"none") -- z. B. `{{ can_start }}`, damit ein laufender Dienst keinen
    "Starten"-Knopf anbietet. Fehlt das Feld, ist der Knopf immer da (wie bisher)."""


class Badge(BaseModel):
    text: Template
    tone: Template | Tone = Tone.NEUTRAL


# ---------------------------------------------------------------------------
# View-Typen — endliche Menge. React und Flutter implementieren alle.
# ---------------------------------------------------------------------------


class StatView(BaseModel):
    kind: Literal["stat"] = "stat"
    value: Template
    label: Template | None = None
    delta: Template | None = None
    tone: Template | Tone = Tone.NEUTRAL
    sparkline_field: str | None = None


class ListItem(BaseModel):
    title: Template
    subtitle: Template | None = None
    icon: Template | None = None
    badge: Badge | None = None
    actions: list[WidgetAction] = Field(default_factory=list)


class ListView(BaseModel):
    kind: Literal["list"] = "list"
    item: ListItem
    empty_text: str = "Keine Einträge"
    max_items: int | None = None


class Column(BaseModel):
    field: str
    label: str
    template: Template | None = None
    align: Literal["left", "right", "center"] = "left"
    width: int | None = None


class TableView(BaseModel):
    kind: Literal["table"] = "table"
    columns: list[Column]
    row_actions: list[WidgetAction] = Field(default_factory=list)
    empty_text: str = "Keine Daten"


class Series(BaseModel):
    field: str
    label: str
    tone: Tone = Tone.ACCENT


class ChartView(BaseModel):
    kind: Literal["chart"] = "chart"
    chart: Literal["line", "bar", "area"] = "line"
    x_field: str = "ts"
    series: list[Series]
    y_unit: Literal["number", "percent", "bytes", "duration"] = "number"
    y_max: float | None = None


class StatusGridView(BaseModel):
    """Die Service-Matrix-Kacheln."""

    kind: Literal["status_grid"] = "status_grid"
    tile_title: Template
    tile_subtitle: Template | None = None
    tile_icon: Template | None = None
    tile_tone: Template | Tone = Tone.NEUTRAL
    tile_link: Template | None = None


class GaugeView(BaseModel):
    kind: Literal["gauge"] = "gauge"
    value_field: str
    max_field: str | None = None
    max_value: float = 100
    label: Template | None = None


class MarkdownView(BaseModel):
    kind: Literal["markdown"] = "markdown"
    content_field: str = "content"


class ActionsView(BaseModel):
    kind: Literal["actions"] = "actions"
    actions: list[WidgetAction]


class LogView(BaseModel):
    kind: Literal["log"] = "log"
    lines_field: str = "lines"
    follow: bool = True
    max_lines: int = 500


View = Annotated[
    Union[
        StatView,
        ListView,
        TableView,
        ChartView,
        StatusGridView,
        GaugeView,
        MarkdownView,
        ActionsView,
        LogView,
    ],
    Field(discriminator="kind"),
]


class WidgetSpec(BaseModel):
    """Was eine Extension über ctx.ui.register_widget() anmeldet."""

    id: str
    title: str
    icon: str | None = None
    description: str | None = None
    size: GridSize = Field(default_factory=GridSize)
    refresh: Refresh = Field(default_factory=Refresh)
    data_endpoint: str
    view: View
    permissions: list[str] = Field(default_factory=list)

    component: str | None = None
    """Optionale React-Komponente für eine reichere Web-Darstellung.

    **Aufwertung, kein Ersatz.** Ein Widget, das nur als `component` existiert, wird
    abgelehnt — sonst entsteht genau die Web-only-Schieflage, die §12 verbietet.
    Das erzwingt bereits der Typ: `view` ist ein Pflichtfeld.
    """

    default_enabled: bool = True

    @model_validator(mode="after")
    def _check_action_permissions(self) -> "WidgetSpec":
        """Widget-Aktionen dürfen keine Permission verlangen, die das Widget selbst
        nicht hat — sonst sieht ein Nutzer Knöpfe, die für ihn immer scheitern."""
        declared = set(self.permissions)
        actions = getattr(self.view, "actions", None) or []
        item = getattr(self.view, "item", None)
        if item is not None:
            actions = [*actions, *item.actions]
        for action in actions:
            missing = set(action.permissions) - declared
            if missing:
                raise ValueError(
                    f"Widget '{self.id}': Aktion '{action.id}' verlangt {sorted(missing)}, "
                    f"was das Widget nicht in `permissions` deklariert."
                )
        return self


class MobileFallback(str, enum.Enum):
    WIDGETS = "widgets"   # Standard: die App zeigt die Widgets dieser Extension
    WEBVIEW = "webview"   # Ausnahme, begründungspflichtig
    HIDDEN = "hidden"


HostToolCategory = Literal["control", "monitoring", "services", "data", "settings"]
"""Gruppen auf der Server-Seite. Bewusst Schluessel statt deutscher Woerter -- die
Oberflaeche uebersetzt sie (Produkt: mehrsprachig vorbereitet)."""


class HostToolSpec(BaseModel):
    """Ein Werkzeug auf der Server-Seite eines Hosts (Plesk-Stil "Werkzeuge &
    Einstellungen" je Server). Der Kern zeigt es als Kachel, wenn der Host passt, und
    kennt dabei keine Extension namentlich. `path` fuehrt auf eine Seite DIESER
    Extension (wie `PageSpec.path`), `{host_id}` wird durch die Host-ID ersetzt.

    Passt, wenn ALLE gesetzten Bedingungen zutreffen: `kinds` (Host.kind), `tags`
    (mindestens einer), `own_hosts_only` (Host stammt aus dieser Extension),
    `permissions` (Nutzer hat alle)."""

    id: str
    title: str
    path: str
    description: str | None = None
    icon: str | None = None
    category: HostToolCategory = "settings"
    kinds: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    own_hosts_only: bool = False
    permissions: list[str] = Field(default_factory=list)
    os_families: list[str] = Field(default_factory=list)
    """Nur fuer Hosts mit diesem `os_family` (z. B. ["linux"]); leer = alle."""
    order: int = 100


class HostRequirementSpec(BaseModel):
    """Was eine Extension auf einem Server braucht (Zugang, Rechte, Gruppen) -- damit der
    Kern beim Einrichten des SSH-Zugangs und bei "Verbindung pruefen" genau das anbieten
    bzw. pruefen kann, ohne eine Extension oder ein Werkzeug (Docker, ...) beim Namen zu
    kennen. Passt zu einem Host, wenn `tags` leer ist oder mindestens einer zutrifft UND
    sein `os_family` in `os_families` steht (Standard: nur Linux).

    - `check_command`: nur lesend; Exit 0 = in Ordnung, 127 = nicht installiert.
    - `ok_text` / `fail_hint`: Texte fuer die Pruefung; `fail_hint` darf `{user}` enthalten.
    - `unix_group`: der Einrichtungsbefehl bietet an, den SSH-Benutzer dieser Gruppe
      hinzuzufuegen (Name nach `^[a-z_][a-z0-9_-]{0,31}$`, sonst ignoriert der Kern ihn).
    - `needs_root`/`root_reason`: die Extension braucht root ohne Passwort (sudo);
      `root_reason` nennt in einem kurzen Satzteil wofuer."""

    id: str
    label: str
    check_command: str | None = None
    ok_text: str = ""
    fail_hint: str = ""
    unix_group: str | None = None
    needs_root: bool = False
    root_reason: str | None = None
    tags: list[str] = Field(default_factory=list)
    os_families: list[str] = Field(default_factory=lambda: ["linux"])
    order: int = 100


class PageSpec(BaseModel):
    """Eine volle UI-Ansicht. React — aber `mobile` legt fest, was die App stattdessen tut."""

    id: str
    path: str
    title: str
    icon: str | None = None
    nav_section: str | None = None
    nav_order: int = 100
    permissions: list[str] = Field(default_factory=list)
    component: str
    mobile: MobileFallback = MobileFallback.WIDGETS
    show_in_nav: bool = True
