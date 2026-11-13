import { useQuery, type QueryClient } from "@tanstack/react-query";

import { api } from "./api";
import type { PageOut, WidgetOut } from "../widgets/types";

/** GET /api/v1/pages: bereits serverseitig auf sichtbare und berechtigte Seiten gefiltert (api/v1/catalog.py). */
export function usePages() {
  return useQuery({ queryKey: ["pages"], queryFn: () => api.get<PageOut[]>("/pages") });
}

/** GET /api/v1/widgets: nur Widgets aktuell geladener, aktivierter Extensions (ext/runtime.py UiRegistry). */
export function useWidgets() {
  return useQuery({ queryKey: ["widgets"], queryFn: () => api.get<WidgetOut[]>("/widgets") });
}

interface Capabilities {
  api_version: string;
  view_types: string[];
  extensions: { id: string; name: string; version: string; icon: string | null }[];
  feature_flags: Record<string, boolean>;
}

/** Öffentlich verfügbare Funktionen der aktuell geladenen Module. */
export function useCapabilities() {
  return useQuery({ queryKey: ["capabilities"], queryFn: () => api.get<Capabilities>("/capabilities") });
}

export function useCoreFeatures() {
  const { data } = useCapabilities();
  return { terminal: data?.feature_flags.terminal === true, files: data?.feature_flags.files === true };
}

/** Inaktive Kataloge vor dem Seitenwechsel laden, damit ausgeschaltete Kacheln nicht kurz wieder erscheinen. */
export function refreshExtensionQueries(queryClient: QueryClient) {
  return Promise.all(
    ["pages", "widgets", "capabilities", "extensions", "overview", "terminal", "hosts"].map((key) =>
      queryClient.invalidateQueries({ queryKey: [key], refetchType: key === "hosts" ? "active" : "all" }),
    ),
  );
}
