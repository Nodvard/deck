/**
 * Seite „Apps“ (N7, Teil 1): alle App-Kacheln an einem Ort.
 *
 * - Eigene Apps stehen in **Ordnern** (ihre Gruppe; ohne Gruppe unter „Ohne Ordner“), jeder Ordner lässt sich
 *   zuklappen.
 * - Mit dem Recht `apps.write` lassen sich Kacheln **ziehen**: auf eine andere Kachel (davor einsortiert, auch in einen
 *   anderen Ordner) oder auf die Überschrift eines Ordners (ans Ende dieses Ordners). Ohne Maus geht dasselbe über
 *   das Menü der Kachel (Nach vorn/hinten, Bearbeiten → Gruppe).
 * - **Vorschläge** (`GET /apps/suggestions`): Adressen, die Erweiterungen schon kennen (z. B. Proxy-Hosts im Nginx
 *   Proxy Manager, erkannte Dienste mit Web-Oberfläche) und die es als App noch nicht gibt. „Übernehmen“ öffnet den
 *   Dialog vorbelegt; erst „Speichern“ legt die App an.
 * - Erkannte Dienste stehen darunter wie im Cockpit.
 *
 * Noch ohne Logos und ohne Erreichbarkeits-Punkt (eigene Schritte). Nodvard Deck ruft keine der Adressen selbst auf.
 */
import { Boxes, FolderClosed, Plus, Search, Sparkles } from "lucide-react";
import { type DragEvent, useMemo, useRef, useState } from "react";

import { AppDialog } from "../components/AppDialog";
import { AppTile, CustomAppTile } from "../components/AppTile";
import { EmptyState } from "../components/EmptyState";
import { LoadError } from "../components/LoadError";
import {
  type AppFormValues,
  type AppSuggestionOut,
  existingGroups,
  formFromApp,
  moveBefore,
  moveToEndOf,
  urlHost,
  useAppActions,
  useAppSuggestions,
} from "../lib/apps";
import { type AppTileOut, useHosts, useOverview } from "../lib/overview";
import { useAuthStore } from "../state/auth";
import { confirmDialog } from "../state/dialogs";

const UNGROUPED = "";
const UNGROUPED_LABEL = "Ohne Ordner";

const BUTTON =
  "inline-flex flex-none items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium accent-gradient text-on-accent transition hover:bg-accent-strong";
const GRID = "grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4";

interface Folder {
  key: string;
  label: string;
  apps: AppTileOut[];
}

const byPosition = (a: AppTileOut, b: AppTileOut) =>
  (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.name.toLowerCase().localeCompare(b.name.toLowerCase()) || a.id.localeCompare(b.id);

/** Ordner in fester Reihenfolge: „Ohne Ordner“ zuerst, dann nach Name. Innerhalb eines Ordners nach Position. */
export function foldersOf(apps: AppTileOut[]): Folder[] {
  const map = new Map<string, AppTileOut[]>();
  for (const app of [...apps].sort(byPosition)) {
    const key = app.group ?? UNGROUPED;
    map.set(key, [...(map.get(key) ?? []), app]);
  }
  return [...map.entries()]
    .sort(([a], [b]) => (a === UNGROUPED ? -1 : b === UNGROUPED ? 1 : a.toLowerCase().localeCompare(b.toLowerCase())))
    .map(([key, list]) => ({ key, label: key === UNGROUPED ? UNGROUPED_LABEL : key, apps: list }));
}

function matches(app: { name: string; url?: string | null; host?: string | null; group?: string | null }, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return `${app.name} ${app.host ?? ""} ${app.group ?? ""} ${urlHost(app.url ?? "")}`.toLowerCase().includes(q);
}

export function AppsPage() {
  const canReadHosts = useAuthStore((s) => s.hasPermission("hosts.read"));
  const canWrite = useAuthStore((s) => s.hasPermission("apps.write"));
  const overview = useOverview(canReadHosts);
  const { data: hosts } = useHosts(canReadHosts);
  const suggestions = useAppSuggestions(canWrite);
  const actions = useAppActions();

  const [query, setQuery] = useState("");
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const [dialog, setDialog] = useState<{ app: AppTileOut | null; initial?: Partial<AppFormValues> } | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const [dropOn, setDropOn] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const addRef = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  const overviewFailed = overview.isError && !overview.data;
  const apps = useMemo(() => overview.data?.apps ?? [], [overview.data]);
  const custom = useMemo(() => apps.filter((a) => a.source === "custom").sort(byPosition), [apps]);
  const detected = useMemo(() => apps.filter((a) => a.source === "detected" && a.url), [apps]);
  const groups = useMemo(() => existingGroups(apps), [apps]);
  const folders = useMemo(() => foldersOf(custom.filter((a) => matches(a, query))), [custom, query]);
  const shownDetected = detected.filter((a) => matches(a, query));
  const shownSuggestions = (suggestions.data?.suggestions ?? []).filter((s) => matches(s, query));
  const searching = query.trim() !== "";

  function open(app: AppTileOut | null, initial?: Partial<AppFormValues>) {
    const active = document.activeElement;
    returnFocus.current = active instanceof HTMLElement && active !== document.body ? active : null;
    setDialog({ app, initial });
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
      `Die Kachel „${app.name}“ verschwindet für alle Benutzer. Die Adresse selbst und das, was dahinter läuft, bleiben unberührt.`,
      { title: "App löschen?", danger: true, confirmLabel: "Löschen" },
    );
    if (ok) await run(() => actions.remove(app.id));
  }

  /** Legt `dragged` in den Ordner `group` (falls anders) und speichert die neue Reihenfolge. */
  async function place(draggedId: string, group: string, ids: string[]) {
    const app = custom.find((a) => a.id === draggedId);
    if (!app) return;
    await run(async () => {
      if ((app.group ?? UNGROUPED) !== group) await actions.update(app.id, { ...formFromApp(app), group });
      await actions.reorder(ids);
    });
  }

  function menuFor(app: AppTileOut, folder: Folder) {
    const at = folder.apps.findIndex((a) => a.id === app.id);
    const swap = (direction: "up" | "down") => {
      const neighbour = folder.apps[direction === "up" ? at - 1 : at + 1];
      if (!neighbour) return;
      const ids = custom.map((a) => a.id);
      const i = ids.indexOf(app.id);
      const j = ids.indexOf(neighbour.id);
      [ids[i], ids[j]] = [ids[j], ids[i]];
      void run(() => actions.reorder(ids));
    };
    return {
      onEdit: () => open(app),
      onDelete: () => void remove(app),
      onMove: swap,
      canMoveUp: at > 0,
      canMoveDown: at >= 0 && at < folder.apps.length - 1,
    };
  }

  // --- Ziehen ------------------------------------------------------------------------------------------------------

  const canDrag = canWrite && !searching;

  function dragStart(e: DragEvent, id: string) {
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", id);
    setDragging(id);
  }

  function dragOver(e: DragEvent, target: string) {
    if (!dragging) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (dropOn !== target) setDropOn(target);
  }

  function dragEnd() {
    setDragging(null);
    setDropOn(null);
  }

  function dropOnTile(e: DragEvent, target: AppTileOut) {
    e.preventDefault();
    const dragged = dragging;
    dragEnd();
    if (!dragged || dragged === target.id) return;
    void place(dragged, target.group ?? UNGROUPED, moveBefore(custom.map((a) => a.id), dragged, target.id));
  }

  function dropOnFolder(e: DragEvent, folder: Folder) {
    e.preventDefault();
    const dragged = dragging;
    dragEnd();
    if (!dragged) return;
    void place(dragged, folder.key, moveToEndOf(custom.map((a) => a.id), dragged, folder.apps.map((a) => a.id)));
  }

  function toggle(key: string) {
    setClosed((now) => {
      const next = new Set(now);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const addButton = canWrite && (
    <button ref={addRef} type="button" className={BUTTON} onClick={() => open(null)}>
      <Plus size={15} aria-hidden /> App hinzufügen
    </button>
  );

  if (!canReadHosts) {
    return <p className="text-sm text-white/60">Für die Apps fehlt dir das Recht „Server ansehen“.</p>;
  }

  return (
    <div className="space-y-6" data-testid="apps-page">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-white/60"><Boxes size={18} aria-hidden /></span>
        <h1 className="text-xl font-semibold">Apps</h1>
        <span className="ml-auto flex items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1">
          <Search size={13} className="text-white/45" aria-hidden />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Apps suchen"
            aria-label="Apps suchen"
            className="w-40 bg-transparent text-sm outline-none placeholder:text-white/35"
          />
        </span>
        {addButton}
      </div>

      {canWrite && custom.length > 1 && (
        <p className="text-xs text-white/45">
          {searching
            ? "Zum Sortieren erst die Suche leeren."
            : "Ziehe eine Kachel auf eine andere, um sie davor einzusortieren, oder auf die Überschrift eines Ordners. Ohne Maus: Menü der Kachel."}
        </p>
      )}

      {error && <p role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200">{error}</p>}
      {overview.isLoading && <p className="text-sm text-white/50">Lade die Apps …</p>}
      {/* Eine gescheiterte Abfrage ist kein „Noch keine Apps“. */}
      {overviewFailed && <LoadError what="Die Liste der Apps" error={overview.error} onRetry={() => void overview.refetch()} />}

      {!overview.isLoading && !overviewFailed && custom.length === 0 && detected.length === 0 && (
        <EmptyState
          icon="boxes"
          testId="apps-page-empty"
          title="Noch keine Apps"
          text="Leg Kacheln für das an, was du oft aufrufst – zum Beispiel Router, NAS-Oberfläche oder Pi-hole. Vorschläge aus deinen Modulen stehen weiter unten."
          action={addButton || undefined}
        />
      )}

      {folders.map((folder) => {
        const isOpen = !closed.has(folder.key);
        return (
          <section key={folder.key || "__ohne__"} aria-label={`Ordner ${folder.label}`} data-testid={`folder-${folder.key || "ohne"}`}>
            <h2
              onDragOver={canDrag ? (e) => dragOver(e, `folder:${folder.key}`) : undefined}
              onDragLeave={() => setDropOn(null)}
              onDrop={canDrag ? (e) => dropOnFolder(e, folder) : undefined}
              className={`mb-2 rounded-lg ${dropOn === `folder:${folder.key}` ? "ring-2 ring-[var(--color-accent)]" : ""}`}
            >
              <button
                type="button"
                aria-expanded={isOpen}
                onClick={() => toggle(folder.key)}
                className="flex items-center gap-2 text-sm font-semibold hover:text-white"
              >
                <FolderClosed size={15} aria-hidden /> {folder.label}
                <span className="tabular-nums font-normal normal-case tracking-normal text-white/40">{folder.apps.length}</span>
              </button>
            </h2>
            {isOpen && (
              <div className={GRID}>
                {folder.apps.map((app) => (
                  <div
                    key={app.id}
                    draggable={canDrag}
                    onDragStart={canDrag ? (e) => dragStart(e, app.id) : undefined}
                    onDragOver={canDrag ? (e) => dragOver(e, `app:${app.id}`) : undefined}
                    onDrop={canDrag ? (e) => dropOnTile(e, app) : undefined}
                    onDragEnd={dragEnd}
                    data-testid={`drag-${app.id}`}
                    className={`rounded-lg ${dragging === app.id ? "opacity-40" : ""} ${dropOn === `app:${app.id}` && dragging !== app.id ? "ring-2 ring-[var(--color-accent)]" : ""}`}
                  >
                    <CustomAppTile app={app} actions={canWrite ? menuFor(app, folder) : undefined} />
                  </div>
                ))}
              </div>
            )}
          </section>
        );
      })}

      {shownDetected.length > 0 && (
        <section aria-label="Erkannte Dienste" data-testid="detected">
          <h2 className="mb-2 text-sm font-semibold">Erkannte Dienste</h2>
          <div className={GRID}>
            {shownDetected.map((tile) => (
              <AppTile
                key={tile.id}
                service={{ id: tile.id, name: tile.name, host: tile.host, host_id: tile.host_id, state: tile.state, tone: tile.tone, url: tile.url, image: tile.image }}
              />
            ))}
          </div>
        </section>
      )}

      {canWrite && (
        <section aria-label="Vorschläge" data-testid="suggestions">
          <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold">
            <Sparkles size={15} aria-hidden /> Vorschläge
          </h2>
          <p className="mb-2 text-xs text-white/45">
            Adressen, die deine Module schon kennen und die es als App noch nicht gibt. „Übernehmen“ füllt den Dialog vor;
            angelegt wird erst mit „Speichern“.
          </p>
          {suggestions.isLoading && <p className="text-sm text-white/50">Suche Vorschläge …</p>}
          {suggestions.isError && <p className="text-sm text-amber-300">Die Vorschläge ließen sich gerade nicht laden.</p>}
          {(suggestions.data?.errors ?? []).map((text) => (
            <p key={text} className="text-xs text-amber-300">{text}</p>
          ))}
          {suggestions.data && shownSuggestions.length === 0 && (
            <p className="text-sm text-white/50">{searching ? "Keine passenden Vorschläge." : "Gerade keine neuen Vorschläge."}</p>
          )}
          {shownSuggestions.length > 0 && (
            <ul className={GRID}>
              {shownSuggestions.map((s: AppSuggestionOut) => (
                <li key={s.url} className="panel flex items-center gap-3 px-3 py-2.5" data-testid="suggestion">
                  <span className="min-w-0 flex-1">
                    <span className="block break-words text-sm font-medium">{s.name}</span>
                    <span className="block break-words text-[11px] text-white/45">
                      {urlHost(s.url)} · {s.source}{s.host ? ` · ${s.host}` : ""}
                    </span>
                  </span>
                  <button
                    type="button"
                    onClick={() => open(null, { name: s.name, url: s.url })}
                    className="flex-none rounded-lg border border-white/10 bg-white/[0.06] px-2.5 py-1 text-xs hover:bg-white/[0.12]"
                    aria-label={`${s.name} übernehmen`}
                  >
                    Übernehmen
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {canWrite && (
        <AppDialog
          open={dialog !== null}
          onOpenChange={(next) => { if (!next) setDialog(null); }}
          app={dialog?.app ?? null}
          initial={dialog?.initial}
          groups={groups}
          hosts={hosts ?? []}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            (returnFocus.current?.isConnected ? returnFocus.current : addRef.current)?.focus();
          }}
        />
      )}
    </div>
  );
}
