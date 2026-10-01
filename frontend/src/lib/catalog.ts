import { useQuery } from "@tanstack/react-query";

import { api } from "./api";
import type { PageOut, WidgetOut } from "../widgets/types";

/** GET /api/v1/pages -- bereits server-seitig auf sichtbare + berechtigte Seiten gefiltert (api/v1/catalog.py). */
export function usePages() {
  return useQuery({ queryKey: ["pages"], queryFn: () => api.get<PageOut[]>("/pages") });
}

/** GET /api/v1/widgets -- nur Widgets aktuell geladener, aktivierter Extensions (ext/runtime.py UiRegistry). */
export function useWidgets() {
  return useQuery({ queryKey: ["widgets"], queryFn: () => api.get<WidgetOut[]>("/widgets") });
}

interface Capabilities {
  api_version: string;
  view_types: string[];
  extensions: { id: string; name: string; version: string; icon: string | null }[];
  feature_flags: Record<string, boolean>;
}

/** GET /api/v1/capabilities -- oeffentlich, kein Login noetig (public.py). Bisher nur
 * fuer die Android-App gedacht (docs/04 §2); `demo_mode` ist
 * der erste Web-Aufrufer -- zeigt sichtbar, wenn eine Installation eine Demo mit
 * Fake-Daten ist, nicht echte Infrastruktur (AppShell.tsx). */
export function useCapabilities() {
  return useQuery({ queryKey: ["capabilities"], queryFn: () => api.get<Capabilities>("/capabilities") });
}
