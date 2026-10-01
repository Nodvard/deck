/**
 * Ordner-Auswahl fuer Transfers zwischen Quellen. Ohne diesen Dialog gaebe es fuer
 * eine ANDERE Quelle nur deren Wurzel `/` als Ziel (siehe FilesPage.tsx-
 * Modul-Docstring: "ohne zweites Panel gibt es keinen Weg, einen tieferen Zielordner
 * einer anderen Quelle zu waehlen"). Dieser Dialog ist das zweite Panel: Quelle
 * waehlen, durch deren Ordner navigieren, bei Bedarf einen neuen anlegen, bestaetigen.
 *
 * Zeigt nur Ordner (Dateien sind kein Ziel) und nur beschreibbare Quellen. Der Name
 * der Datei bleibt erhalten -- gewaehlt wird der Zielordner, nicht der Zieldateiname.
 */
import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";

export interface PickerSource {
  source_id: string;
  label: string;
  caps: { write: boolean; mkdir: boolean };
}

interface DirEntry {
  name: string;
  path: string;
  is_dir: boolean;
}

export interface FolderChoice {
  sourceId: string;
  dirPath: string;
  move: boolean;
}

function joinPath(base: string, name: string): string {
  return base === "/" ? `/${name}` : `${base}/${name}`;
}

function parentOf(path: string): string {
  const idx = path.lastIndexOf("/");
  return idx <= 0 ? "/" : path.slice(0, idx);
}

export function FolderPickerDialog({
  open,
  onOpenChange,
  sources,
  fileName,
  initialSourceId,
  allowMove,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  sources: PickerSource[];
  fileName: string;
  initialSourceId: string | null;
  /** Nur, wenn die Herkunftsquelle Loeschen kann -- sonst waere "Verschieben" gelogen. */
  allowMove: boolean;
  onConfirm: (choice: FolderChoice) => void;
}) {
  const writable = sources.filter((s) => s.caps.write);
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [path, setPath] = useState("/");
  const [dirs, setDirs] = useState<DirEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newFolder, setNewFolder] = useState<string | null>(null);
  const [move, setMove] = useState(false);

  useEffect(() => {
    if (!open) return;
    const start = writable.find((s) => s.source_id === initialSourceId) ?? writable[0];
    setSourceId(start?.source_id ?? null);
    setPath("/");
    setMove(false);
    setNewFolder(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- nur beim Oeffnen neu setzen
  }, [open, initialSourceId]);

  useEffect(() => {
    if (!open || !sourceId) return;
    let cancelled = false;
    setDirs(null);
    setError(null);
    api
      .get<{ items: DirEntry[] }>(`/files/${encodeURIComponent(sourceId)}/list?path=${encodeURIComponent(path)}`)
      .then((res) => {
        if (!cancelled) setDirs(res.items.filter((e) => e.is_dir).sort((a, b) => a.name.localeCompare(b.name)));
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [open, sourceId, path]);

  const source = writable.find((s) => s.source_id === sourceId) ?? null;

  async function createFolder() {
    if (!sourceId || !newFolder?.trim()) return;
    const target = joinPath(path, newFolder.trim());
    try {
      await api.post(`/files/${encodeURIComponent(sourceId)}/mkdir`, { path: target });
      setNewFolder(null);
      setPath(target);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  const crumbs = [{ label: source?.label ?? "/", path: "/" }];
  let acc = "";
  for (const part of path.split("/").filter(Boolean)) {
    acc += `/${part}`;
    crumbs.push({ label: part, path: acc });
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 flex max-h-[85vh] w-[calc(100%-2rem)] max-w-2xl -translate-x-1/2 -translate-y-1/2 flex-col rounded-lg border border-white/10 bg-[var(--color-surface)] p-4 shadow-xl">
          <Dialog.Title className="mb-1 text-lg font-semibold">Zielordner wählen</Dialog.Title>
          <Dialog.Description className="mb-3 text-sm opacity-70">
            Wohin soll '{fileName}'? Quelle links, Ordner rechts.
          </Dialog.Description>

          <div className="flex min-h-0 flex-1 gap-3">
            <ul className="w-44 shrink-0 space-y-0.5 overflow-auto text-sm" aria-label="Zielquelle">
              {writable.map((s) => (
                <li key={s.source_id}>
                  <button
                    type="button"
                    onClick={() => {
                      setSourceId(s.source_id);
                      setPath("/");
                    }}
                    aria-pressed={s.source_id === sourceId}
                    className={`w-full break-words rounded px-2 py-1 text-left ${s.source_id === sourceId ? "bg-white/10 font-medium" : "opacity-80 hover:opacity-100"}`}
                  >
                    {s.label}
                  </button>
                </li>
              ))}
            </ul>

            <div className="flex min-w-0 flex-1 flex-col">
              <nav className="mb-2 flex flex-wrap items-center gap-1 text-sm" aria-label="Pfad im Ziel">
                {crumbs.map((c, i) => (
                  <span key={c.path}>
                    {i > 0 && <span className="opacity-40"> / </span>}
                    <button type="button" onClick={() => setPath(c.path)} className="opacity-80 hover:underline hover:opacity-100">
                      {c.label}
                    </button>
                  </span>
                ))}
              </nav>
              <ul className="min-h-40 flex-1 overflow-auto rounded border border-white/10 text-sm" aria-label="Ordner">
                {path !== "/" && (
                  <li>
                    <button type="button" onClick={() => setPath(parentOf(path))} className="w-full px-2 py-1 text-left opacity-70 hover:bg-white/5">
                      ↑ ..
                    </button>
                  </li>
                )}
                {!dirs && !error && <li className="px-2 py-1 opacity-60">Lade …</li>}
                {error && <li className="px-2 py-1 text-red-400">{error}</li>}
                {dirs?.length === 0 && <li className="px-2 py-1 opacity-60">Keine Unterordner.</li>}
                {dirs?.map((d) => (
                  <li key={d.path}>
                    <button type="button" onClick={() => setPath(d.path)} className="w-full break-words px-2 py-1 text-left hover:bg-white/5">
                      📁 {d.name}
                    </button>
                  </li>
                ))}
              </ul>
              {source?.caps.mkdir && (
                <div className="mt-2 flex items-center gap-2 text-xs">
                  {newFolder === null ? (
                    <button type="button" onClick={() => setNewFolder("")} className="rounded bg-white/10 px-2 py-1 hover:bg-white/20">
                      + Neuer Ordner hier
                    </button>
                  ) : (
                    <>
                      <input
                        autoFocus
                        value={newFolder}
                        onChange={(e) => setNewFolder(e.target.value)}
                        onKeyDown={(e) => e.key === "Enter" && void createFolder()}
                        placeholder="Ordnername"
                        aria-label="Name des neuen Ordners"
                        className="rounded bg-white/10 px-2 py-1"
                      />
                      <button type="button" onClick={() => void createFolder()} className="rounded bg-white/10 px-2 py-1 hover:bg-white/20">
                        Anlegen
                      </button>
                      <button type="button" onClick={() => setNewFolder(null)} className="opacity-60 hover:opacity-100">
                        Abbrechen
                      </button>
                    </>
                  )}
                </div>
              )}
            </div>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-white/10 pt-3 text-sm">
            <p className="min-w-0 flex-1 break-all text-xs opacity-70">
              Ziel: {source?.label ?? "?"} · {joinPath(path, fileName)}
            </p>
            {allowMove && (
              <label className="flex items-center gap-1 text-xs">
                <input type="checkbox" checked={move} onChange={(e) => setMove(e.target.checked)} />
                Verschieben (Original danach löschen)
              </label>
            )}
            <Dialog.Close asChild>
              <button type="button" className="rounded bg-white/10 px-3 py-1.5 hover:bg-white/20">
                Abbrechen
              </button>
            </Dialog.Close>
            <button
              type="button"
              disabled={!sourceId}
              onClick={() => {
                if (!sourceId) return;
                onConfirm({ sourceId, dirPath: path, move });
                onOpenChange(false);
              }}
              className="rounded bg-[var(--color-accent)] px-3 py-1.5 font-medium text-[var(--color-background)] disabled:opacity-40"
            >
              {move ? "Hierher verschieben" : "Hierher kopieren"}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
