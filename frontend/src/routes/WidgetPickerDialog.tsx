/** Widgets ein- und ausblenden; neue Widgets kommen an die nächste freie Zeile, ohne die Erweiterung zu deaktivieren. */
import * as Dialog from "@radix-ui/react-dialog";
import { Link } from "react-router-dom";

import { ButtonLink, EmptyState } from "../components/EmptyState";
import { useAuthStore } from "../state/auth";

import { isHiddenItem } from "../lib/dashboard";
import type { DashboardLayoutItem } from "../lib/dashboard";
import type { WidgetOut } from "../widgets/types";

const COLS = 12;

function itemKey(item: { ext_id: string; widget_id: string }): string {
  return `${item.ext_id}:${item.widget_id}`;
}

interface Props {
  widgets: WidgetOut[];
  items: DashboardLayoutItem[];
  onChange: (items: DashboardLayoutItem[]) => void;
  /** Optional von außen gesteuert (der Leerzustand der Startseite öffnet den Dialog). */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Platzierte Widgets, deren Modul `meta.state = "not_configured"` meldet; die Startseite blendet sie aus. */
  notConfigured?: ReadonlySet<string>;
  /** Im Bearbeiten-Modus ändert der Dialog nur den Entwurf; gespeichert wird mit „Fertig“ auf der Startseite. */
  drafting?: boolean;
}

export function WidgetPickerDialog({ widgets, items, onChange, open, onOpenChange, notConfigured, drafting = false }: Props) {
  const canManageExtensions = useAuthStore((s) => s.hasPermission("extensions.manage"));
  const placedKeys = new Set(items.filter((i) => !isHiddenItem(i)).map(itemKey));

  function toggle(widget: WidgetOut, placed: boolean) {
    const key = itemKey({ ext_id: widget.ext_id, widget_id: widget.id });
    if (placed) {
      // Ausblenden statt löschen (sonst kämen entfernte Widgets beim
      // nächsten Laden zurück, weil die Auto-Platzierung sie für neu hielt).
      onChange(items.map((i) => (itemKey(i) === key ? { ...i, config: { ...i.config, hidden: true } } : i)));
      return;
    }
    const cursorY = items.filter((i) => !isHiddenItem(i)).reduce((max, i) => Math.max(max, i.y + i.h), 0);
    const existing = items.find((i) => itemKey(i) === key);
    if (existing) {
      const { hidden: _hidden, ...config } = existing.config;
      onChange(items.map((i) => (itemKey(i) === key ? { ...i, x: 0, y: cursorY, config } : i)));
      return;
    }
    const width = Math.min(COLS, Math.max(1, widget.size.w));
    const addition: DashboardLayoutItem = {
      widget_id: widget.id, ext_id: widget.ext_id, x: 0, y: cursorY,
      w: width, h: Math.max(1, widget.size.h), config: {},
    };
    onChange([...items, addition]);
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Trigger asChild>
        <button type="button" className="rounded bg-white/10 px-3 py-1.5 text-sm hover:bg-white/20">
          Widgets verwalten
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-scrim/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 w-full max-w-[min(100vw-2rem,28rem)] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-white/10 bg-[var(--color-surface)] p-4 shadow-xl">
          <Dialog.Title className="mb-1 text-lg font-semibold">Widgets verwalten</Dialog.Title>
          <Dialog.Description className="mb-3 text-sm opacity-70">
            Welche Widgets auf dem Dashboard erscheinen.
          </Dialog.Description>
          <ul className="max-h-96 space-y-0.5 overflow-auto text-sm">
            {widgets.map((w) => {
              const key = itemKey({ ext_id: w.ext_id, widget_id: w.id });
              const placed = placedKeys.has(key);
              const waiting = placed && notConfigured?.has(key);
              return (
                <li key={key}>
                  <label className="flex cursor-pointer items-center justify-between rounded px-2 py-1.5 hover:bg-white/5">
                    <span>
                      {w.title}
                      <span className="ml-1.5 opacity-50">({w.ext_id})</span>
                    </span>
                    <input type="checkbox" checked={placed} onChange={() => toggle(w, placed)} />
                  </label>
                  {waiting && (
                    <p className="px-2 pb-1.5 text-xs text-white/50" data-testid={`widget-not-configured-${key}`}>
                      Nicht eingerichtet, deshalb nicht auf der Startseite.{" "}
                      {canManageExtensions ? (
                        <Link
                          to={`/settings/extensions/${encodeURIComponent(w.ext_id)}`}
                          onClick={() => onOpenChange?.(false)}
                          className="underline underline-offset-2 hover:text-white"
                        >
                          Einrichten
                        </Link>
                      ) : (
                        "Ein Administrator kann das Modul einrichten."
                      )}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
          {widgets.length === 0 && (
            <EmptyState
              compact
              icon="layout-dashboard"
              title="Noch keine Widgets"
              text="Widgets bringen die Module mit. Schalte ein Modul ein, dann erscheinen seine Widgets hier zur Auswahl."
              action={canManageExtensions ? <ButtonLink to="/settings/extensions">Module ansehen</ButtonLink> : undefined}
            />
          )}
          <div className="mt-4 flex items-center justify-end gap-3">
            {drafting && (
              <p className="mr-auto text-xs text-white/55" data-testid="picker-draft-hint">
                Gespeichert wird erst mit „Fertig“ auf der Startseite.
              </p>
            )}
            <Dialog.Close asChild>
              <button type="button" className="rounded bg-white/10 px-3 py-1.5 text-sm hover:bg-white/20">
                {drafting ? "Schließen" : "Fertig"}
              </button>
            </Dialog.Close>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
