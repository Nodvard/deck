/**
 * "Widgets verwalten": neben der automatischen Platzierung
 * (`appendMissingWidgets()` in DashboardPage.tsx, laeuft beim Aktivieren einer
 * Extension) der Weg, ein Widget manuell zu entfernen oder ein zuvor entferntes
 * wieder hinzuzufuegen, ohne die Extension selbst zu deaktivieren. Dieser Dialog
 * macht `DashboardLayoutItem[]` direkt bearbeitbar: ein Haekchen pro bekanntem Widget (`GET /widgets`-Katalog),
 * "aktiv" heisst "steht aktuell im gespeicherten Layout".
 *
 * Platzierung beim Hinzufuegen folgt derselben simplen Zeilen-Logik wie
 * `appendMissingWidgets()` (an die naechste freie Zeile anhaengen) --
 * bewusst keine gemeinsame Funktion daraus gemacht, um DashboardPage.tsx's
 * Auto-Platzierungs-Effekt nicht an eine UI-Komponente zu koppeln.
 */
import * as Dialog from "@radix-ui/react-dialog";

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
  /** Optional von aussen gesteuert (der Leerzustand der Startseite oeffnet den Dialog). */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

export function WidgetPickerDialog({ widgets, items, onChange, open, onOpenChange }: Props) {
  const canManageExtensions = useAuthStore((s) => s.hasPermission("extensions.manage"));
  const placedKeys = new Set(items.filter((i) => !isHiddenItem(i)).map(itemKey));

  function toggle(widget: WidgetOut, placed: boolean) {
    const key = itemKey({ ext_id: widget.ext_id, widget_id: widget.id });
    if (placed) {
      // Ausblenden statt loeschen (sonst kaemen entfernte Widgets beim
      // naechsten Laden zurueck, weil die Auto-Platzierung sie fuer neu hielt).
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
        <Dialog.Overlay className="fixed inset-0 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 w-[calc(100%-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 rounded-lg border border-white/10 bg-[var(--color-surface)] p-4 shadow-xl">
          <Dialog.Title className="mb-1 text-lg font-semibold">Widgets verwalten</Dialog.Title>
          <Dialog.Description className="mb-3 text-sm opacity-70">
            Welche Widgets auf dem Dashboard erscheinen.
          </Dialog.Description>
          <ul className="max-h-96 space-y-0.5 overflow-auto text-sm">
            {widgets.map((w) => {
              const key = itemKey({ ext_id: w.ext_id, widget_id: w.id });
              const placed = placedKeys.has(key);
              return (
                <li key={key}>
                  <label className="flex cursor-pointer items-center justify-between rounded px-2 py-1.5 hover:bg-white/5">
                    <span>
                      {w.title}
                      <span className="ml-1.5 opacity-50">({w.ext_id})</span>
                    </span>
                    <input type="checkbox" checked={placed} onChange={() => toggle(w, placed)} />
                  </label>
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
          <div className="mt-4 flex justify-end">
            <Dialog.Close asChild>
              <button type="button" className="rounded bg-white/10 px-3 py-1.5 text-sm hover:bg-white/20">
                Fertig
              </button>
            </Dialog.Close>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
