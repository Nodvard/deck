import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./api";

export interface DashboardLayoutItem {
  widget_id: string;
  ext_id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  /** `config.hidden === true`: vom Nutzer entfernt. Der Eintrag bleibt bewusst stehen --
   * ein geloeschter waere fuer die Auto-Platzierung (`appendMissingWidgets`) "neu" und
   * kaeme beim naechsten Laden zurueck. Gilt fuer Web UND die geplante App (gleiches Layout). */
  config: Record<string, unknown>;
}

export function isHiddenItem(item: DashboardLayoutItem): boolean {
  return item.config?.hidden === true;
}

export interface LayoutOut {
  id: string;
  name: string;
  is_default: boolean;
  items: DashboardLayoutItem[];
  created_at: string;
  updated_at: string;
}

/**
 * GET /dashboard/layouts liefert eine Liste (mehrere benannte Layouts sind im Schema
 * vorgesehen), legt aber beim ersten Aufruf automatisch ein Standard-Layout an
 * (api/v1/dashboard.py) -- die Oberflaeche zeigt nur dieses eine Standard-Layout pro Nutzer,
 * ein Layout-Umschalter ist ein offener Punkt.
 */
export function useDashboardLayout() {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["dashboard-layouts"],
    queryFn: () => api.get<LayoutOut[]>("/dashboard/layouts"),
  });

  const layout = query.data?.find((l) => l.is_default) ?? query.data?.[0] ?? null;

  async function saveItems(items: DashboardLayoutItem[]) {
    if (!layout) return;
    const updated = await api.put<LayoutOut>(`/dashboard/layouts/${layout.id}`, { items });
    queryClient.setQueryData<LayoutOut[]>(["dashboard-layouts"], (prev) =>
      prev ? prev.map((l) => (l.id === updated.id ? updated : l)) : [updated],
    );
  }

  return { layout, isLoading: query.isLoading, error: query.error, saveItems };
}
