/**
 * Beispieldaten („Mit Beispieldaten ansehen“): Status, Anlegen und Entfernen
 * (`GET /demo`, `POST /demo/seed`, `DELETE /demo`, backend/src/nodvard_deck/api/v1/demo.py).
 *
 * Anlegen und Entfernen brauchen `hosts.write` UND `settings.write` (siehe Begruendung im Backend);
 * den Status sieht, wer Server sehen darf. Ohne Recht zeigt die Oberflaeche keine Knoepfe.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, ApiError } from "./api";
import { confirmDialog } from "../state/dialogs";

export interface DemoStatus {
  active: boolean;
  hosts: number;
  notifications: number;
  /** Beispiel-Apps (eigene Kacheln im Cockpit); fehlt bei einer aelteren Antwort. */
  apps?: number;
  /** Das Dashboard-Layout wurde ersetzt und wird beim Loeschen zurueckgesetzt. */
  layout_replaced: boolean;
  /** Beispiel-Server, an denen inzwischen ein Zugang haengt (werden mitgeloescht). */
  hosts_with_access: string[];
}

export const DEMO_QUERY_KEY = ["demo"] as const;

export function canManageDemo(can: (permission: string) => boolean): boolean {
  return can("hosts.write") && can("settings.write");
}

export function useDemoStatus(enabled = true) {
  return useQuery({
    queryKey: DEMO_QUERY_KEY,
    queryFn: () => api.get<DemoStatus>("/demo"),
    enabled,
    retry: false,
  });
}

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : "Das hat nicht geklappt. Bitte versuch es noch einmal.";
}

/** Nach dem Anlegen/Loeschen aendert sich fast alles auf dem Bildschirm (Server, Meldungen, Layout). */
function useRefreshEverything() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries();
}

/** Anlegen: `run()` meldet `true`, wenn es geklappt hat; sonst steht der Grund in `error`. */
export function useSeedDemo() {
  const refresh = useRefreshEverything();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      await api.post("/demo/seed");
      await refresh();
      return true;
    } catch (err) {
      setError(errorText(err));
      return false;
    } finally {
      setBusy(false);
    }
  }
  return { run, busy, error };
}

/** Der Text der Rueckfrage: nennt, was geloescht wird, und warnt vor dem, was man sonst nicht erwartet. */
export function removeQuestion(status: DemoStatus): string {
  const parts = [
    `Alle Beispieldaten werden entfernt (${status.hosts} Beispiel-Server, ${status.notifications} Beispiel-Meldungen${status.apps ? `, ${status.apps} Beispiel-Apps` : ""}). Deine eigenen Daten bleiben unberührt.`,
  ];
  if (status.layout_replaced) parts.push("Dein Dashboard-Layout wird auf den Stand von vorher zurückgesetzt.");
  if (status.hosts_with_access.length > 0) {
    const names = status.hosts_with_access.join(", ");
    parts.push(
      status.hosts_with_access.length === 1
        ? `Achtung: Bei „${names}“ hast du inzwischen einen Zugang hinterlegt. Er wird mit gelöscht.`
        : `Achtung: Bei ${names} hast du inzwischen Zugänge hinterlegt. Sie werden mit gelöscht.`,
    );
  }
  return parts.join(" ");
}

/** Entfernen mit Rueckfrage: `run(status)` meldet `true`, wenn wirklich geloescht wurde. */
export function useRemoveDemo() {
  const refresh = useRefreshEverything();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(status: DemoStatus): Promise<boolean> {
    const ok = await confirmDialog(removeQuestion(status), {
      title: "Beispieldaten löschen?", danger: true, confirmLabel: "Beispieldaten löschen",
    });
    if (!ok) return false;
    setBusy(true);
    setError(null);
    try {
      await api.delete("/demo");
      await refresh();
      return true;
    } catch (err) {
      setError(errorText(err));
      return false;
    } finally {
      setBusy(false);
    }
  }
  return { run, busy, error };
}
