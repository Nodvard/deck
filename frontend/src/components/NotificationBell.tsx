/**
 * Glocke in der Kopfzeile mit Schnellansicht: ein kleines Fenster unter der Glocke zeigt
 * die neuesten Meldungen, statt jedes Mal auf die volle Meldungsseite zu springen.
 *
 * Daten: dieselben Endpunkte und derselbe Live-Weg wie die Meldungsseite. Den Zaehler
 * und die WS-Anbindung liefert `useUnreadNotifications` (AppShell); jede neue Meldung
 * macht alle Abfragen unter dem Schluessel ["notifications"] ungueltig -- die Liste hier
 * zieht also von selbst nach, solange das Fenster offen ist. Beim Oeffnen wird immer neu geladen.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertOctagon, AlertTriangle, Bell, BellOff, Info } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { api } from "../lib/api";
import { relativeTime } from "../lib/overview";
import { SEVERITY_LABEL, type NotificationOut } from "../routes/NotificationsPage";

const MAX_ITEMS = 8;

const SEVERITY_ICON: Record<string, { Icon: typeof Info; className: string }> = {
  info: { Icon: Info, className: "text-sky-300" },
  warning: { Icon: AlertTriangle, className: "text-amber-300" },
  critical: { Icon: AlertOctagon, className: "text-red-300" },
};

/** Titel; ohne Titel die erste Zeile des Textes. */
export function notificationHeadline(n: Pick<NotificationOut, "title" | "body">): string {
  return n.title.trim() || n.body.split("\n").find((line) => line.trim())?.trim() || "Meldung";
}

/** Nur Ziele innerhalb des Dashboards ("/…", nicht "//host") -- wie auf der Meldungsseite. */
function targetPath(n: NotificationOut): string | null {
  const path = n.payload?.path;
  return typeof path === "string" && path.startsWith("/") && !path.startsWith("//") ? path : null;
}

function BellRow({ n, onOpen }: { n: NotificationOut; onOpen: (n: NotificationOut) => void }) {
  const unread = !n.read_at;
  const sev = SEVERITY_ICON[n.severity] ?? SEVERITY_ICON.info;
  return (
    <li data-testid={`bell-item-${n.id}`} data-unread={unread ? "true" : "false"}>
      <button
        type="button"
        onClick={() => onOpen(n)}
        className={`relative flex w-full items-start gap-3 py-2.5 pl-4 pr-4 text-left transition-colors hover:bg-white/[0.05] focus-visible:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent ${
          unread ? "bell-unread" : ""
        }`}
      >
        {unread && <span aria-hidden className="absolute inset-y-0 left-0 w-[3px] bg-accent" />}
        <sev.Icon size={16} aria-hidden className={`mt-0.5 flex-none ${sev.className} ${unread ? "" : "opacity-60"}`} />
        <span className="min-w-0 flex-1">
          <span className={`line-clamp-2 break-words text-sm leading-snug ${unread ? "font-semibold text-white" : "text-white/60"}`}>
            {unread && <span className="sr-only">Ungelesen: </span>}
            <span className="sr-only">{SEVERITY_LABEL[n.severity] ?? n.severity}: </span>
            {notificationHeadline(n)}
          </span>
          <span className="mt-0.5 block truncate text-[11px] text-white/45">
            {relativeTime(n.ts)}
            {n.source_ext_id ? ` · ${n.source_ext_id}` : ""}
          </span>
        </span>
        {unread && <span aria-hidden className="mt-1.5 h-2 w-2 flex-none rounded-full bg-accent" />}
      </button>
    </li>
  );
}

export function NotificationBell({ unread }: { unread: number }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const bellRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();

  const close = useCallback((returnFocus: boolean) => {
    setOpen(false);
    if (returnFocus) bellRef.current?.focus();
  }, []);

  // Nach jeder Navigation zu (auch Tippen auf die schon offene Seite: neuer `location.key`).
  // Ohne Fokus-Rueckgabe: der gehoert dann der neuen Seite.
  useEffect(() => setOpen(false), [location.key]);

  // Beim Oeffnen den Fokus ins Fenster holen.
  useEffect(() => {
    if (open) panelRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close(true);
    };
    const onPointer = (e: Event) => {
      if (!wrapRef.current?.contains(e.target as Node)) close(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    // Wer mit Tab aus dem Fenster hinaus wandert, will es nicht mehr offen haben.
    document.addEventListener("focusin", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("focusin", onPointer);
    };
  }, [open, close]);

  // Das Fenster existiert nur geoeffnet -- jedes Oeffnen mountet die Abfrage neu und
  // laedt deshalb frisch ("always": auch wenn noch Daten im Cache liegen).
  const { data, error, isLoading } = useQuery({
    queryKey: ["notifications", "bell"],
    queryFn: () => api.get<NotificationOut[]>(`/notifications?limit=${MAX_ITEMS}`),
    enabled: open,
    refetchOnMount: "always",
  });

  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["notifications"] });

  const openItem = (n: NotificationOut) => {
    if (!n.read_at) void api.post("/notifications/read", { ids: [n.id] }).then(refresh, () => undefined);
    setOpen(false);
    navigate(targetPath(n) ?? "/notifications");
  };

  const markAll = async () => {
    await api.post("/notifications/read-all");
    refresh();
  };

  const items = (data ?? []).slice(0, MAX_ITEMS);

  return (
    <div ref={wrapRef} className="relative">
      <button
        ref={bellRef}
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="relative rounded-lg p-2 text-white/70 hover:bg-white/5 hover:text-white"
        aria-label={unread > 0 ? `Meldungen, ${unread} ungelesen` : "Meldungen"}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? "meldungen-fenster" : undefined}
      >
        <Bell size={18} />
        {unread > 0 && (
          <span aria-hidden className="accent-gradient absolute -right-0.5 -top-0.5 min-w-[1.1rem] rounded-full px-1 text-center text-[10px] font-semibold leading-[1.1rem] text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div
          ref={panelRef}
          id="meldungen-fenster"
          role="dialog"
          aria-label="Meldungen"
          tabIndex={-1}
          className="bell-popover fixed inset-x-3 top-[3.75rem] z-50 flex max-h-[70vh] flex-col overflow-hidden rounded-xl border border-white/10 bg-surface shadow-2xl shadow-black/60 outline-none sm:absolute sm:inset-x-auto sm:right-0 sm:top-full sm:mt-2 sm:w-[24rem]"
        >
          <div className="flex flex-none items-center gap-2 border-b border-white/[0.07] px-4 py-3">
            <h2 className="text-sm font-semibold">Meldungen</h2>
            <span className="text-xs text-white/50" data-testid="bell-unread">
              {unread > 0 ? `${unread} ungelesen` : "alles gelesen"}
            </span>
            {unread > 0 && (
              <button
                type="button"
                onClick={() => void markAll()}
                className="ml-auto rounded-md px-2 py-1 text-xs text-white/70 hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                Alle als gelesen
              </button>
            )}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
            {isLoading && !data && <p className="px-4 py-6 text-center text-sm text-white/50">Lade …</p>}
            {error && !data && (
              <p className="px-4 py-6 text-center text-sm text-red-300">Die Meldungen ließen sich nicht laden.</p>
            )}
            {data && items.length === 0 && (
              <div className="flex flex-col items-center gap-2 px-4 py-9 text-center" data-testid="bell-empty">
                <span className="grid h-10 w-10 place-items-center rounded-full bg-emerald-400/10 text-emerald-300">
                  <BellOff size={18} />
                </span>
                <p className="text-sm font-medium">Alles ruhig</p>
                <p className="text-xs text-white/50">Keine neuen Meldungen – hier erscheint, was deine Server zu sagen haben.</p>
              </div>
            )}
            {items.length > 0 && (
              <ul className="divide-y divide-white/[0.05]">
                {items.map((n) => (
                  <BellRow key={n.id} n={n} onOpen={openItem} />
                ))}
              </ul>
            )}
          </div>

          <div className="flex-none border-t border-white/[0.07]">
            <Link
              to="/notifications"
              className="block px-4 py-2.5 text-center text-xs font-medium text-white/70 hover:bg-white/[0.05] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent"
            >
              Alle Meldungen anzeigen →
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
