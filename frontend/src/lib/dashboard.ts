import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./api";
import type { DashboardGroupState } from "../widgets/groups";

export interface DashboardLayoutItem {
  widget_id: string;
  ext_id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  /**
   * `config.hidden === true`: vom Nutzer entfernt. Der Eintrag bleibt bewusst stehen: Ein gelöschter wäre für die
   * Auto-Platzierung (`appendMissingWidgets`) "neu" und käme beim nächsten Laden zurück. Gilt für Web und die
   * geplante App (gleiches Layout).
   */
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
  /** Reihenfolge, Namen und Zuklappen der Gruppen; fehlt bei einem älteren Backend, leer heißt Vorgabe. */
  groups?: DashboardGroupState[];
  created_at: string;
  updated_at: string;
}

/**
 * GET /dashboard/layouts liefert eine Liste (mehrere benannte Layouts sind im Schema vorgesehen), legt aber beim
 * ersten Aufruf automatisch ein Standard-Layout an (api/v1/dashboard.py); die Oberfläche zeigt nur dieses eine Layout
 * pro Nutzer, ein Layout-Umschalter ist ein offener Punkt.
 */
export function useDashboardLayout() {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["dashboard-layouts"],
    queryFn: () => api.get<LayoutOut[]>("/dashboard/layouts"),
  });

  const pick = (list: LayoutOut[] | undefined) => list?.find((l) => l.is_default) ?? list?.[0] ?? null;
  const layout = pick(query.data);

  async function save(patch: { items?: DashboardLayoutItem[]; groups?: DashboardGroupState[] }) {
    if (!layout) return;
    const updated = await api.put<LayoutOut>(`/dashboard/layouts/${layout.id}`, patch);
    queryClient.setQueryData<LayoutOut[]>(["dashboard-layouts"], (prev) =>
      prev ? prev.map((l) => (l.id === updated.id ? updated : l)) : [updated],
    );
  }

  const saveItems = (items: DashboardLayoutItem[]) => save({ items });
  const saveGroups = (groups: DashboardGroupState[]) => save({ groups });
  // Eine Anfrage für beides: Der Bearbeiten-Modus speichert seinen Entwurf nur ganz oder gar nicht.
  const saveLayout = (items: DashboardLayoutItem[], groups?: DashboardGroupState[]) => save({ items, groups });
  // Der Stand vom Server, nicht aus dem Zwischenspeicher: Ein anderes Gerät kann inzwischen gespeichert haben.
  const fetchLatest = async () => pick((await query.refetch()).data);

  return {
    layout, isLoading: query.isLoading, error: query.error, refetch: query.refetch, fetchLatest,
    saveItems, saveGroups, saveLayout,
  };
}
