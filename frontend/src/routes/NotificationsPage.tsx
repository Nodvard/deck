/**
 * Benachrichtigungs-Center: zeigt die Meldungen aus `api/v1/notifications.py` (z. B.
 * die Lageberichte von Nodvard Shield), die sonst nur in der Datenbank stuenden. Neue Meldungen kommen
 * live ueber den WS-Kanal `notifications` (services/notifications.py publiziert dort).
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../lib/api";
import { useExtensionLabel } from "../lib/extensionNames";
import { useWsSubscription } from "../lib/ws";
import { useAuthStore } from "../state/auth";

export interface NotificationOut {
  id: string;
  ts: string;
  severity: string;
  title: string;
  body: string;
  source_ext_id: string | null;
  correlation_id: string | null;
  read_at: string | null;
  /** Konvention: `path` = Seite im Dashboard, die zur Meldung gehoert. */
  payload?: { path?: string } & Record<string, unknown>;
}

export const SEVERITY_LABEL: Record<string, string> = { info: "Info", warning: "Warnung", critical: "Kritisch" };

const SEVERITY_CLASS: Record<string, string> = {
  info: "bg-sky-500/20 text-sky-300",
  warning: "bg-amber-500/20 text-amber-300",
  critical: "bg-red-500/20 text-red-300",
};

export const UNREAD_QUERY_KEY = ["notifications", "unread-count"];

/** Zaehler fuer das Menue -- alle 60s und sofort bei jeder neuen Meldung (WS). */
export function useUnreadNotifications(enabled: boolean): number {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: UNREAD_QUERY_KEY,
    queryFn: () => api.get<{ unread: number }>("/notifications/unread-count"),
    refetchInterval: 60_000,
    enabled,
  });
  useWsSubscription(enabled ? "notifications" : null, () => {
    void queryClient.invalidateQueries({ queryKey: ["notifications"] });
  });
  return data?.unread ?? 0;
}

function NotificationItem({ n, onRead, canMark }: { n: NotificationOut; onRead: (id: string) => void; canMark: boolean }) {
  const [open, setOpen] = useState(false);
  const sourceLabel = useExtensionLabel(Boolean(n.source_ext_id));
  const long = n.body.length > 240 || n.body.split("\n").length > 4;
  return (
    <li className={`rounded border p-3 text-sm ${n.read_at ? "border-white/5 opacity-70" : "border-white/15"}`} data-testid={`notification-${n.id}`}>
      <div className="flex flex-wrap items-start gap-2">
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${SEVERITY_CLASS[n.severity] ?? "bg-white/10"}`}>
          {SEVERITY_LABEL[n.severity] ?? n.severity}
        </span>
        <span className="min-w-0 flex-1 break-words font-medium">{n.title}</span>
        <span className="text-xs opacity-50">
          {new Date(n.ts).toLocaleString()}
          {n.source_ext_id ? ` · ${sourceLabel(n.source_ext_id)}` : ""}
        </span>
        {typeof n.payload?.path === "string" && n.payload.path.startsWith("/") && (
          <Link to={n.payload.path} onClick={() => { if (!n.read_at && canMark) onRead(n.id); }} className="rounded bg-white/10 px-2 py-0.5 text-xs hover:bg-white/20">
            Öffnen →
          </Link>
        )}
        {!n.read_at && canMark && (
          <button type="button" onClick={() => onRead(n.id)} className="rounded bg-white/10 px-2 py-0.5 text-xs hover:bg-white/20">
            Gelesen
          </button>
        )}
      </div>
      {n.body && (
        <p className={`mt-1.5 whitespace-pre-wrap break-words text-xs opacity-80 ${long && !open ? "line-clamp-4" : ""}`}>{n.body}</p>
      )}
      {long && (
        <button type="button" onClick={() => setOpen((o) => !o)} className="mt-1 text-xs opacity-60 hover:opacity-100">
          {open ? "Weniger" : "Mehr anzeigen"}
        </button>
      )}
    </li>
  );
}

export function NotificationsPage() {
  const queryClient = useQueryClient();
  // Der Lesestatus gilt fuer alle Nutzer gemeinsam; markieren duerfen nur Nutzer mit Schreibrecht.
  const canMark = useAuthStore((s) => s.hasPermission)("notifications.write");
  const [onlyUnread, setOnlyUnread] = useState(false);
  const { data, error, isLoading } = useQuery({
    queryKey: ["notifications", "list", onlyUnread],
    queryFn: () => api.get<NotificationOut[]>(`/notifications?limit=200${onlyUnread ? "&unread=true" : ""}`),
    // Beim Umschalten des Filters die alte Liste stehen lassen, bis die neue da ist.
    placeholderData: (previous) => previous,
  });

  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["notifications"] });
  const markRead = async (id: string) => {
    await api.post("/notifications/read", { ids: [id] });
    refresh();
  };
  const markAll = async () => {
    await api.post("/notifications/read-all");
    refresh();
  };

  const unread = (data ?? []).filter((n) => !n.read_at).length;

  return (
    <div className="p-4 sm:p-6">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold">Meldungen</h2>
        <label className="flex items-center gap-1 text-sm opacity-80">
          <input type="checkbox" checked={onlyUnread} onChange={(e) => setOnlyUnread(e.target.checked)} />
          Nur ungelesene
        </label>
        {unread > 0 && canMark && (
          <button type="button" onClick={() => void markAll()} className="rounded bg-white/10 px-2 py-1 text-xs hover:bg-white/20">
            Alle als gelesen markieren
          </button>
        )}
      </div>
      {isLoading && <p className="text-sm opacity-60">Lade …</p>}
      {error && <p className="text-sm text-red-400">Fehler: {error instanceof Error ? error.message : String(error)}</p>}
      {data && data.length === 0 && (
        <p className="text-sm opacity-60">
          {onlyUnread ? "Keine ungelesenen Meldungen." : "Noch keine Meldungen – hier landen z. B. Lageberichte und Warnungen der Module."}
        </p>
      )}
      <ul className="flex flex-col gap-2">
        {(data ?? []).map((n) => (
          <NotificationItem key={n.id} n={n} canMark={canMark} onRead={(id) => void markRead(id)} />
        ))}
      </ul>
    </div>
  );
}
