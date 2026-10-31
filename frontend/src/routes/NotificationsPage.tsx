/**
 * Benachrichtigungs-Center: zeigt die Meldungen aus `api/v1/notifications.py` (z. B. die Lageberichte von Nodvard
 * Shield), die sonst nur in der Datenbank stünden. Neue Meldungen kommen live über den WS-Kanal `notifications`
 * (services/notifications.py publiziert dort).
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { LoadError } from "../components/LoadError";
import { ApiError, api } from "../lib/api";
import { internalPath } from "../lib/apps";
import { useExtensionLabel } from "../lib/extensionNames";
import { useWsSubscription } from "../lib/ws";
import { useAuthStore } from "../state/auth";
import { confirmDialog } from "../state/dialogs";

export interface NotificationOut {
  id: string;
  ts: string;
  severity: string;
  title: string;
  body: string;
  source_ext_id: string | null;
  correlation_id: string | null;
  read_at: string | null;
  /** Konvention: `path` = Seite im Dashboard, die zur Meldung gehört. */
  payload?: { path?: string } & Record<string, unknown>;
}

export const SEVERITY_LABEL: Record<string, string> = { info: "Info", warning: "Warnung", critical: "Kritisch" };

const SEVERITY_CLASS: Record<string, string> = {
  info: "bg-sky-500/20 text-sky-300",
  warning: "bg-amber-500/20 text-amber-300",
  critical: "bg-red-500/20 text-red-300",
};

export const UNREAD_QUERY_KEY = ["notifications", "unread-count"];

/** So viele Meldungen lädt die Seite höchstens auf einmal. */
const LIST_LIMIT = 200;

/**
 * Die Rückfrage vor „Alle gelesenen löschen“. Kennt die Seite nicht alle gelesenen (die
 * Liste ist am Limit), sagt sie das.
 */
export function deleteReadQuestion(readShown: number, listIsComplete: boolean): string {
  const tail = "Das lässt sich nicht rückgängig machen.";
  if (!listIsComplete) return `Alle gelesenen Meldungen löschen, auch die, die hier nicht mehr in der Liste stehen? ${tail}`;
  return `${readShown} gelesene ${readShown === 1 ? "Meldung" : "Meldungen"} löschen? ${tail}`;
}

/** Zähler für das Menü: alle 60 s und sofort bei jeder neuen Meldung (WS). */
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

function NotificationItem({
  n, onRead, onDelete, canWrite,
}: { n: NotificationOut; onRead: (id: string) => void; onDelete: (id: string) => void; canWrite: boolean }) {
  const [open, setOpen] = useState(false);
  const sourceLabel = useExtensionLabel(Boolean(n.source_ext_id));
  const long = n.body.length > 240 || n.body.split("\n").length > 4;
  const path = internalPath(n.payload?.path);
  return (
    <li className={`rounded border p-3 text-sm ${n.read_at ? "border-white/5 opacity-70" : "border-white/15"}`} data-testid={`notification-${n.id}`}>
      <div className="flex flex-wrap items-start gap-2">
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${SEVERITY_CLASS[n.severity] ?? "bg-white/10"}`}>
          {SEVERITY_LABEL[n.severity] ?? n.severity}
        </span>
        <span className="min-w-0 flex-1 break-words font-medium">{n.title}</span>
        <span className="text-xs opacity-50">
          {new Date(n.ts).toLocaleString("de-DE")}
          {n.source_ext_id ? ` · ${sourceLabel(n.source_ext_id)}` : ""}
        </span>
        {path && (
          <Link to={path} onClick={() => { if (!n.read_at && canWrite) onRead(n.id); }} className="rounded bg-white/10 px-2 py-0.5 text-xs hover:bg-white/20">
            Öffnen →
          </Link>
        )}
        {!n.read_at && canWrite && (
          <button type="button" onClick={() => onRead(n.id)} className="rounded bg-white/10 px-2 py-0.5 text-xs hover:bg-white/20">
            Gelesen
          </button>
        )}
        {canWrite && (
          <button
            type="button"
            onClick={() => onDelete(n.id)}
            aria-label={`Meldung „${n.title.trim() || "ohne Titel"}“ löschen`}
            title="Löschen"
            className="rounded bg-white/10 p-1 hover:bg-red-500/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <Trash2 size={14} aria-hidden />
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
  // Der Lesestatus gilt für alle Nutzer gemeinsam; markieren und löschen dürfen nur Nutzer mit Schreibrecht.
  const canWrite = useAuthStore((s) => s.hasPermission)("notifications.write");
  const [onlyUnread, setOnlyUnread] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const { data, error, isLoading, refetch } = useQuery({
    queryKey: ["notifications", "list", onlyUnread],
    queryFn: () => api.get<NotificationOut[]>(`/notifications?limit=${LIST_LIMIT}${onlyUnread ? "&unread=true" : ""}`),
    // Beim Umschalten des Filters die alte Liste stehen lassen, bis die neue da ist.
    placeholderData: (previous) => previous,
  });

  // Alles unter ["notifications"] neu laden: die Liste und den Zähler der ungelesenen (Glocke, Menü).
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["notifications"] });
  async function run(work: () => Promise<unknown>) {
    setActionError(null);
    try {
      await work();
    } catch (err) {
      setActionError(err instanceof Error && err.message ? err.message : "Das hat nicht geklappt. Bitte versuch es noch einmal.");
    }
    // Auch nach einem Fehler neu laden: die Meldung kann inzwischen von jemand anderem gelöscht worden sein.
    refresh();
  }
  const markRead = (id: string) => run(() => api.post("/notifications/read", { ids: [id] }));
  const markAll = () => run(() => api.post("/notifications/read-all"));
  // „Gibt es nicht mehr“ (404) heißt: jemand anderes war schneller. Das Ziel ist erreicht, kein Fehler.
  const deleteOne = (id: string) =>
    run(() => api.delete(`/notifications/${encodeURIComponent(id)}`).catch((err: unknown) => {
      if (!(err instanceof ApiError && err.status === 404)) throw err;
    }));

  const unread = (data ?? []).filter((n) => !n.read_at).length;
  const readShown = (data ?? []).length - unread;
  const deleteRead = async () => {
    const ok = await confirmDialog(deleteReadQuestion(readShown, (data ?? []).length < LIST_LIMIT), {
      title: "Gelesene Meldungen löschen?", danger: true, confirmLabel: "Löschen",
    });
    if (ok) await run(() => api.post("/notifications/delete-read"));
  };

  return (
    <div className="p-4 sm:p-6">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold">Meldungen</h2>
        <label className="flex items-center gap-1 text-sm opacity-80">
          <input type="checkbox" checked={onlyUnread} onChange={(e) => setOnlyUnread(e.target.checked)} />
          Nur ungelesene
        </label>
        {unread > 0 && canWrite && (
          <button type="button" onClick={() => void markAll()} className="rounded bg-white/10 px-2 py-1 text-xs hover:bg-white/20">
            Alle als gelesen markieren
          </button>
        )}
        {readShown > 0 && canWrite && (
          <button type="button" onClick={() => void deleteRead()} className="rounded bg-white/10 px-2 py-1 text-xs hover:bg-red-500/30">
            Alle gelesenen löschen
          </button>
        )}
      </div>
      {actionError && <p role="alert" className="mb-3 text-sm text-red-400">{actionError}</p>}
      {isLoading && <p className="text-sm opacity-60">Lade …</p>}
      {error && <LoadError what="Die Liste der Meldungen" error={error} onRetry={() => void refetch()} />}
      {/* Nach einem Ladefehler ist eine leere (oder vom anderen Filter stehengebliebene) Liste keine Auskunft. */}
      {!error && data && data.length === 0 && (
        <p className="text-sm opacity-60">
          {onlyUnread ? "Keine ungelesenen Meldungen." : "Noch keine Meldungen – hier landen z. B. Lageberichte und Warnungen der Module."}
        </p>
      )}
      <ul className="flex flex-col gap-2">
        {(data ?? []).map((n) => (
          <NotificationItem key={n.id} n={n} canWrite={canWrite} onRead={(id) => void markRead(id)} onDelete={(id) => void deleteOne(id)} />
        ))}
      </ul>
    </div>
  );
}
