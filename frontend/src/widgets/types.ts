/**
 * TypeScript-Spiegel von sdk/python/nodvard_sdk/widgets.py -- Feldnamen und
 * Discriminator-Werte MUESSEN exakt uebereinstimmen, sonst validiert die Flutter-/
 * React-Seite gegen ein anderes Schema als das, was der Kern tatsaechlich validiert
 * (docs/02-EXTENSION-API.md §4: "Kein Widget ohne deklarative Form").
 */

export type Template = string;

export type Tone = "neutral" | "good" | "warn" | "danger" | "accent";

export interface GridSize {
  w: number;
  h: number;
  min_w: number;
  min_h: number;
}

export interface Refresh {
  interval_s: number | null;
  ws_channel: string | null;
}

export interface WidgetAction {
  id: string;
  label: string;
  endpoint: Template;
  method: "POST" | "PUT" | "DELETE";
  body: Record<string, unknown> | null;
  confirm: boolean;
  confirm_text: string | null;
  style: "primary" | "secondary" | "danger";
  permissions: string[];
  /** Knopf nur zeigen, wenn das Template etwas Wahres ergibt (siehe SDK `WidgetAction.show_if`). */
  show_if?: Template | null;
}

export interface Badge {
  text: Template;
  tone: Template | Tone;
}

export interface StatView {
  kind: "stat";
  value: Template;
  label: Template | null;
  delta: Template | null;
  tone: Template | Tone;
  sparkline_field: string | null;
}

export interface ListItem {
  title: Template;
  subtitle: Template | null;
  icon: Template | null;
  badge: Badge | null;
  actions: WidgetAction[];
}

export interface ListView {
  kind: "list";
  item: ListItem;
  empty_text: string;
  max_items: number | null;
}

export interface Column {
  field: string;
  label: string;
  template: Template | null;
  align: "left" | "right" | "center";
  width: number | null;
}

export interface TableView {
  kind: "table";
  columns: Column[];
  row_actions: WidgetAction[];
  empty_text: string;
}

export interface Series {
  field: string;
  label: string;
  tone: Tone;
}

export interface ChartView {
  kind: "chart";
  chart: "line" | "bar" | "area";
  x_field: string;
  series: Series[];
  y_unit: "number" | "percent" | "bytes" | "duration";
  y_max: number | null;
}

export interface StatusGridView {
  kind: "status_grid";
  tile_title: Template;
  tile_subtitle: Template | null;
  tile_icon: Template | null;
  tile_tone: Template | Tone;
  tile_link: Template | null;
}

export interface GaugeView {
  kind: "gauge";
  value_field: string;
  max_field: string | null;
  max_value: number;
  label: Template | null;
}

export interface MarkdownView {
  kind: "markdown";
  content_field: string;
}

export interface ActionsView {
  kind: "actions";
  actions: WidgetAction[];
}

export interface LogView {
  kind: "log";
  lines_field: string;
  follow: boolean;
  max_lines: number;
}

export type View =
  | StatView
  | ListView
  | TableView
  | ChartView
  | StatusGridView
  | GaugeView
  | MarkdownView
  | ActionsView
  | LogView;

export interface WidgetSpec {
  id: string;
  title: string;
  icon: string | null;
  description: string | null;
  size: GridSize;
  refresh: Refresh;
  data_endpoint: string;
  view: View;
  permissions: string[];
  component: string | null;
  default_enabled: boolean;
}

export interface WidgetOut extends WidgetSpec {
  ext_id: string;
  /** Frühere Kennungen der Erweiterung (nach einer Umbenennung), damit Dashboard-Einträge mit der alten
   * `ext_id` dem Widget zugeordnet werden können. Fehlt bei einem älteren Backend. */
  legacy_ext_ids?: string[];
}

export type MobileFallback = "widgets" | "webview" | "hidden";

export interface PageSpec {
  id: string;
  path: string;
  title: string;
  icon: string | null;
  nav_section: string | null;
  nav_order: number;
  permissions: string[];
  component: string;
  mobile: MobileFallback;
  show_in_nav: boolean;
}

export interface PageOut extends PageSpec {
  ext_id: string;
  /** Frühere Kennungen der Erweiterung: Adressen `/ext/<alt>/…` leitet `ExtensionPage` auf `/ext/<ext_id>/…`
   * um. Fehlt bei einem älteren Backend. */
  legacy_ext_ids?: string[];
}

/** docs/02-EXTENSION-API.md §4: GET /api/v1/ext/<id>/<data_endpoint> -> {data, meta}. */
export interface WidgetDataResponse<T = unknown> {
  data: T;
  meta: Record<string, unknown>;
}
