import { api } from "../lib/api";
import type { WidgetDataResponse, WidgetOut } from "./types";

/**
 * Abfrage der Widget-Daten (`{data, meta}`), gemeinsam für die Karte und die Startseite: Die Startseite liest daraus
 * `meta.state`, um nicht eingerichtete Widgets auszublenden; derselbe Schlüssel heißt, es geht nur eine Anfrage raus.
 */
export function widgetDataQuery(widget: WidgetOut) {
  return {
    queryKey: ["widget-data", widget.ext_id, widget.data_endpoint] as [string, string, string],
    queryFn: () => api.get<WidgetDataResponse>(`/ext/${widget.ext_id}/${widget.data_endpoint}`),
    refetchInterval: widget.refresh.interval_s ? widget.refresh.interval_s * 1000 : (false as const),
  };
}

/**
 * Das Modul ist noch nicht eingerichtet (`meta.state = "not_configured"`, docs/02-EXTENSION-API.md): Die Startseite
 * blendet das Widget aus, „Widgets verwalten“ zeigt den Hinweis mit Link zu den Einstellungen des Moduls.
 */
export function isNotConfigured(response: unknown): boolean {
  if (typeof response !== "object" || response === null) return false;
  const meta = (response as { meta?: unknown }).meta;
  return typeof meta === "object" && meta !== null && (meta as { state?: unknown }).state === "not_configured";
}
