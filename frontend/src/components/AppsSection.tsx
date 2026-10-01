/**
 * Bereich „Apps“ im Cockpit: eigene Kacheln („+ App hinzufuegen“) und die automatisch erkannten Dienste in einem
 * Raster, mit Suche, Gruppen-Filter und -- nur mit dem Recht `apps.write` -- Knopf zum Hinzufuegen und einem kleinen
 * Menue an jeder eigenen Kachel (Bearbeiten, nach vorn/hinten, Loeschen).
 *
 * Wer nichts angelegt hat und nichts erkannt wird, sieht (mit Recht) einen Leerzustand mit dem Knopf; ohne Recht
 * bleibt der Bereich dann ganz weg (wie bisher ohne Dienste).
 */
import { Boxes, Plus, Search } from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { type AppTileOut, type HostOut } from "../lib/overview";
import {
  existingGroups,
  filterApps,
  FILTER_ALL,
  type GroupFilter,
  groupChips,
  useAppActions,
} from "../lib/apps";
import { confirmDialog } from "../state/dialogs";
import { AppDialog } from "./AppDialog";
import { AppTile, CustomAppTile } from "./AppTile";
import { EmptyState } from "./EmptyState";

/** Aus der Kachel das Dienst-Objekt fuer `AppTile` (der erkannte Dienst sieht aus wie bisher). */
function asService(tile: AppTileOut) {
  return {
    id: tile.id, name: tile.name, host: tile.host, host_id: tile.host_id, state: tile.state, tone: tile.tone,
    url: tile.url, image: tile.image,
  };
}

const ADD_BUTTON =
  "inline-flex flex-none items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium accent-gradient text-white shadow-md shadow-black/30 transition hover:brightness-110";

export function AppsSection({ apps, hosts, canWrite }: { apps: AppTileOut[]; hosts: HostOut[]; canWrite: boolean }) {
  const actions = useAppActions();
  const [query, setQuery] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [filter, setFilter] = useState<GroupFilter>(FILTER_ALL);
  const [dialog, setDialog] = useState<{ app: AppTileOut | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const section = useRef<HTMLDivElement>(null);
  const addRef = useRef<HTMLButtonElement>(null);
  /** Wer den Dialog geoeffnet hat (Menue-Knopf einer Kachel oder „App hinzufuegen“): dorthin geht der Fokus zurueck. */
  const returnFocus = useRef<HTMLElement | null>(null);

  const hasHidden = apps.some((a) => a.source === "detected" && !a.url);
  // Ein Filter, den es nicht mehr gibt (letzte App der Gruppe geloescht), gilt als „Alle“.
  const baseVisible = useMemo(() => filterApps(apps, { query, filter: FILTER_ALL, showAll }), [apps, query, showAll]);
  const chips = useMemo(() => groupChips(apps, baseVisible, showAll), [apps, baseVisible, showAll]);
  const activeFilter = chips.some((c) => c.key === filter) ? filter : FILTER_ALL;
  const visible = useMemo(() => filterApps(apps, { query, filter: activeFilter, showAll }), [apps, query, activeFilter, showAll]);

  const custom = useMemo(() => apps.filter((a) => a.source === "custom"), [apps]);
  const visibleCustomIds = visible.filter((a) => a.source === "custom").map((a) => a.id);
  const groups = useMemo(() => existingGroups(apps), [apps]);

  function openDialog(app: AppTileOut | null) {
    const active = document.activeElement;
    returnFocus.current = active instanceof HTMLElement && active !== document.body ? active : null;
    setDialog({ app });
  }

  /** Fokus an eine sinnvolle Stelle, wenn die Aktion ihn verloren hat (Kachel verschoben oder geloescht): der
   * Menue-Knopf der Kachel, sonst „App hinzufuegen“. Hat die Person den Fokus inzwischen woanders, bleibt er dort. */
  function keepFocus(appId: string | null) {
    setTimeout(() => {
      const active = document.activeElement;
      if (active && active !== document.body && active.isConnected) return;
      const menu = appId
        ? section.current?.querySelector<HTMLElement>(`[data-testid="app-custom-${CSS.escape(appId)}"] [aria-haspopup="menu"]`)
        : null;
      (menu ?? addRef.current)?.focus();
    }, 0);
  }

  function restoreDialogFocus(event: Event) {
    event.preventDefault(); // sonst setzt Radix den Fokus auf den (nicht vorhandenen) Ausloeser, also auf <body>
    const target = returnFocus.current?.isConnected ? returnFocus.current : addRef.current;
    target?.focus();
  }

  async function run(work: () => Promise<unknown>) {
    setError(null);
    try {
      await work();
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : "Das hat nicht geklappt. Bitte versuch es noch einmal.");
    }
  }

  async function remove(app: AppTileOut) {
    const ok = await confirmDialog(
      `Die Kachel „${app.name}“ verschwindet für alle Benutzer aus dem Cockpit. Die Adresse selbst und das, was dahinter läuft, bleiben unberührt.`,
      { title: "App löschen?", danger: true, confirmLabel: "Löschen" },
    );
    if (ok) {
      await run(() => actions.remove(app.id));
      keepFocus(null);
    }
  }

  /** Eine Stelle nach vorn/hinten: tauscht mit der naechsten SICHTBAREN eigenen App (bei aktivem Filter die der Gruppe). */
  async function move(app: AppTileOut, direction: "up" | "down") {
    const at = visibleCustomIds.indexOf(app.id);
    const neighbour = visibleCustomIds[direction === "up" ? at - 1 : at + 1];
    if (at < 0 || neighbour === undefined) return;
    const ids = custom.map((a) => a.id);
    const a = ids.indexOf(app.id);
    const b = ids.indexOf(neighbour);
    [ids[a], ids[b]] = [ids[b], ids[a]];
    await run(() => actions.reorder(ids));
    keepFocus(app.id);
  }

  function menuFor(app: AppTileOut) {
    const at = visibleCustomIds.indexOf(app.id);
    return {
      onEdit: () => openDialog(app),
      onDelete: () => void remove(app),
      onMove: (direction: "up" | "down") => void move(app, direction),
      canMoveUp: at > 0,
      canMoveDown: at >= 0 && at < visibleCustomIds.length - 1,
    };
  }

  const addButton = canWrite && (
    <button ref={addRef} type="button" className={ADD_BUTTON} onClick={() => openDialog(null)}>
      <Plus size={15} aria-hidden /> App hinzufügen
    </button>
  );

  return (
    <div ref={section} data-testid="apps-section">
      <div className="mb-3 flex items-center gap-2">
        <span className="text-white/60"><Boxes size={15} /></span>
        <h2 className="text-sm font-semibold uppercase tracking-[0.12em] text-white/70">Apps</h2>
        <div className="ml-auto">{apps.length > 0 && addButton}</div>
      </div>

      {apps.length === 0 ? (
        <EmptyState
          icon="boxes"
          testId="apps-empty"
          title="Noch keine Apps"
          text="Leg Kacheln für das an, was du oft aufrufst – zum Beispiel Router, NAS-Oberfläche oder Pi-hole. Läuft dazu die Container-Erkennung, erscheinen deine Dienste hier außerdem von selbst."
          action={addButton || undefined}
        />
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-2">
            {hasHidden && (
              <label className="flex items-center gap-1.5 text-xs text-white/55">
                <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />
                auch ohne Web-Oberfläche
              </label>
            )}
            <span className="flex items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1">
              <Search size={13} className="text-white/45" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Apps filtern"
                aria-label="Apps filtern"
                className="w-32 bg-transparent text-xs outline-none placeholder:text-white/35"
              />
            </span>
          </div>

          {chips.length > 0 && (
            <div className="mb-3 flex flex-wrap gap-1.5" role="group" aria-label="Gruppen" data-testid="app-groups">
              {chips.map((chip) => (
                <button
                  key={chip.key}
                  type="button"
                  aria-pressed={chip.key === activeFilter}
                  onClick={() => setFilter(chip.key)}
                  className={`rounded-full border px-3 py-1 text-xs transition ${
                    chip.key === activeFilter
                      ? "border-[var(--color-accent)] bg-[color-mix(in_srgb,var(--color-accent)_22%,transparent)] text-white"
                      : "border-white/10 bg-white/[0.04] text-white/65 hover:bg-white/10 hover:text-white"
                  }`}
                >
                  {chip.label} <span className="tabular-nums text-white/45">{chip.count}</span>
                </button>
              ))}
            </div>
          )}

          {error && (
            <p role="alert" className="mb-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200">{error}</p>
          )}

          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4" data-testid="apps">
            {visible.map((tile) =>
              tile.source === "custom" ? (
                <CustomAppTile key={`custom-${tile.id}`} app={tile} actions={canWrite ? menuFor(tile) : undefined} />
              ) : (
                <AppTile key={`detected-${tile.id}`} service={asService(tile)} />
              ),
            )}
          </div>
          {visible.length === 0 && <p className="text-sm text-white/50">Keine passenden Apps.</p>}
        </>
      )}

      {canWrite && (
        <AppDialog
          open={dialog !== null}
          onOpenChange={(open) => { if (!open) setDialog(null); }}
          app={dialog?.app ?? null}
          groups={groups}
          hosts={hosts}
          onCloseAutoFocus={restoreDialogFocus}
        />
      )}
    </div>
  );
}
