/**
 * Der Kern-Dateimanager (docs/04-API.md Paragraph 3 "Dateien") -- eine CORE-Seite
 * wie DashboardPage.tsx, KEINE
 * Extension-Seite: der Explorer selbst kennt keine Quelle namentlich, er rendert
 * generisch ueber `GET /files/sources` (gespeist aus `FileSourceProvider`-
 * Implementierungen der terminal- und nextcloud-Extension).
 *
 * Rename/Remove/Mkdir gehen ueber `promptDialog()`/
 * `confirmDialog()` (state/dialogs.ts, echte Radix-Dialoge) statt `window.prompt()`/
 * `confirm()` -- ersetzt die fruehere Pragmatik (ScriptsPage.tsx's `<textarea>` statt
 * CodeMirror gilt als bewusste Vereinfachung weiterhin, das war ein anderer Tausch).
 *
 * **Kopieren zwischen Quellen per echtem HTML5-Drag&Drop**, ersetzt
 * den frueheren "Kopieren"-Button + zwei `window.prompt()`s -- genau der Weg, den
 * `nodvard_sdk.capabilities.FileSource`s eigener Docstring immer schon nannte
 * ("Drag & Drop zwischen Quellen (open_read(A) -> open_write(B), serverseitig)").
 * Serverseitig unveraendert derselbe `POST /files/transfer`.
 *
 * Drop-Ziele: ein Ordner in der aktuellen Liste (Ziel = dieser Ordner) oder ein
 * Breadcrumb (Ziel = dieser uebergeordnete Pfad) kopieren sofort. Eine Quelle in der
 * Seitenleiste oeffnet den `FolderPickerDialog`
 * (sonst bliebe nur deren Wurzel `/` -- ohne zweites Panel gab es keinen Weg, einen
 * tieferen Zielordner einer ANDEREN Quelle zu waehlen). Derselbe Dialog steckt hinter
 * "Kopieren nach …" je Datei und kann auch VERSCHIEBEN: erst kopieren, das Original
 * erst loeschen, wenn der Transfer-Lauf `succeeded` meldet -- nie vorher.
 *
 * **Nur Dateien sind Zugriffs-Quelle fuer den Zug** (`entry.is_dir` ist NICHT
 * `draggable`) -- `open_read()`/`open_write()` sind Einzeldatei-Streams (siehe
 * SDK-Protokoll), ein Ordner-Transfer wuerde je nach Connector entweder hart
 * fehlschlagen (SFTP: "ist ein Verzeichnis") oder still STILLE FALSCHE DATEN
 * schreiben (WebDAV: `GET` auf eine Kollektion liefert eine HTML-Liste statt eines
 * Fehlers, die als "Dateiinhalt" durchgereicht wuerde) -- der alte Button hatte
 * dieses Risiko nie ausgeschlossen, es wurde beim Umbau bewusst mitgefixt.
 */
import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";

import { ButtonLink, EmptyState } from "../components/EmptyState";
import { FolderPickerDialog, type FolderChoice } from "../components/FolderPickerDialog";
import { api, ApiError } from "../lib/api";
import { useWsSubscription } from "../lib/ws";
import { useAuthStore } from "../state/auth";
import { confirmDialog, promptDialog } from "../state/dialogs";

interface FileSourceOut {
  source_id: string;
  label: string;
  icon: string;
  caps: { write: boolean; rename: boolean; remove: boolean; mkdir: boolean; search: boolean; range_read: boolean };
}

interface FileEntryOut {
  name: string;
  path: string;
  is_dir: boolean;
  size: number | null;
  modified_at: string | null;
  mime: string | null;
  /** Von der Quelle mitgegeben; die SFTP-Quelle markiert Links mit `symlink`, Links ins
   * Leere zusaetzlich mit `broken`. */
  metadata?: { symlink?: boolean; broken?: boolean };
}

interface SearchHit {
  source_id: string;
  entry: FileEntryOut;
}

function joinPath(base: string, name: string): string {
  return base === "/" ? `/${name}` : `${base}/${name}`;
}

/** Ein Link ins Leere hat kein lesbares Ziel: kein Download, keine Kopie, kein Ziehen
 * (das endete beim Laden mit einem 502) -- nur Umbenennen/Löschen bleiben sinnvoll. */
function isBrokenLink(entry: FileEntryOut): boolean {
  return entry.metadata?.symlink === true && entry.metadata?.broken === true;
}

/** Herunterladen, Kopieren und Ziehen gibt es nur für echte Dateien (keine Ordner, keine
 * Links ins Leere). */
function canTransfer(entry: FileEntryOut): boolean {
  return !entry.is_dir && !isBrokenLink(entry);
}

function LinkMark(): JSX.Element {
  return (
    <span role="img" aria-label="Link" title="Link (verweist auf ein anderes Ziel)" className="ml-1.5 opacity-70">
      🔗
    </span>
  );
}

function formatSize(size: number | null): string {
  if (size === null) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`;
  return `${(size / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

/**
 * `fetch()` kann Upload-
 * Fortschritt fuer den Request-Body nicht melden (kein `onprogress` fuer den
 * Request, nur `ReadableStream`-Tricks mit duenner Browser-Unterstuetzung) --
 * `XMLHttpRequest.upload.onprogress` ist dafuer der etablierte Weg, deshalb hier
 * bewusst XHR statt `fetch()`, nur fuer diesen einen Aufruf.
 */
function uploadWithProgress(sourceId: string, dirPath: string, file: File, onProgress: (percent: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const token = useAuthStore.getState().accessToken;
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/v1/files/${encodeURIComponent(sourceId)}/upload?path=${encodeURIComponent(joinPath(dirPath, file.name))}`);
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else reject(new Error(`HTTP ${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error("Netzwerkfehler beim Hochladen."));
    xhr.send(file);
  });
}

export function FilesPage(): JSX.Element {
  const canManageExtensions = useAuthStore((s) => s.hasPermission("extensions.manage"));
  const canWriteHosts = useAuthStore((s) => s.hasPermission("hosts.write"));
  const [sources, setSources] = useState<FileSourceOut[] | null>(null);
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [path, setPath] = useState("/");
  const [entries, setEntries] = useState<FileEntryOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [searchHits, setSearchHits] = useState<SearchHit[] | null>(null);
  const [transferRunId, setTransferRunId] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<{ name: string; percent: number } | null>(null);
  const [draggingPath, setDraggingPath] = useState<string | null>(null);
  const [dropTargetKey, setDropTargetKey] = useState<string | null>(null);
  const [picker, setPicker] = useState<{ fromSourceId: string; entry: { path: string; name: string; size?: number | null }; targetSourceId: string | null } | null>(null);
  const [pendingMove, setPendingMove] = useState<{ runId: string; sourceId: string; path: string; name: string; size: number | null } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const activeSource = sources?.find((s) => s.source_id === sourceId) ?? null;

  const loadSources = useCallback(() => {
    api
      .get<FileSourceOut[]>("/files/sources")
      .then((list) => {
        setSources(list);
        if (list.length > 0 && !sourceId) setSourceId(list[0].source_id);
      })
      .catch((err: unknown) => setError(err instanceof ApiError ? err.message : String(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- laeuft bewusst nur beim Mount
  }, []);

  useEffect(() => {
    loadSources();
  }, [loadSources]);

  const loadDir = useCallback((sid: string, p: string) => {
    setError(null);
    setEntries(null);
    api
      .get<{ items: FileEntryOut[] }>(`/files/${encodeURIComponent(sid)}/list?path=${encodeURIComponent(p)}`)
      .then((res) => setEntries(res.items))
      .catch((err: unknown) => setError(err instanceof ApiError ? err.message : String(err)));
  }, []);

  useEffect(() => {
    if (sourceId) loadDir(sourceId, path);
  }, [sourceId, path, loadDir]);

  useWsSubscription(transferRunId ? `runs.${transferRunId}` : null, (payload) => {
    const data = payload as { status?: string; bytes?: number; error?: string };
    if (data.status === "succeeded") {
      setMessage(`Transfer abgeschlossen (${formatSize(data.bytes ?? 0)}).`);
      setTransferRunId(null);
      if (pendingMove && pendingMove.runId === transferRunId) {
        const move = pendingMove;
        setPendingMove(null);
        // Das Original nur loeschen, wenn genau so viele Bytes angekommen sind, wie die Quelle beim
        // Anzeigen hatte -- sonst waere nach einer leeren oder halben Kopie alles weg.
        if (move.size === null || data.bytes !== move.size) {
          setMessage(
            move.size === null
              ? `'${move.name}' kopiert, aber das Original bleibt: Die Größe der Quelle war nicht bekannt, ob alles angekommen ist, lässt sich nicht prüfen.`
              : `'${move.name}' kopiert, aber das Original bleibt: Es kamen ${formatSize(data.bytes ?? 0)} an, die Quelle hat ${formatSize(move.size)}. Bitte prüfe die Kopie.`,
          );
          if (sourceId) loadDir(sourceId, path);
          return;
        }
        void api
          .post(`/files/${encodeURIComponent(move.sourceId)}/remove`, { path: move.path, recursive: false })
          .then(() => {
            setMessage(`'${move.name}' verschoben (${formatSize(data.bytes ?? 0)}).`);
            if (sourceId) loadDir(sourceId, path);
          })
          .catch((err: unknown) =>
            setMessage(`Kopiert, aber Original nicht gelöscht: ${err instanceof ApiError ? err.message : String(err)}`),
          );
        return;
      }
      if (sourceId) loadDir(sourceId, path);
    } else if (data.status === "failed") {
      setMessage(`Transfer fehlgeschlagen: ${data.error ?? "unbekannter Fehler"}`);
      setTransferRunId(null);
      setPendingMove(null);
    } else if (typeof data.bytes === "number") {
      setMessage(`Transfer läuft … (${formatSize(data.bytes)})`);
    }
  });

  function breadcrumbs(): { label: string; path: string }[] {
    const parts = path.split("/").filter(Boolean);
    const crumbs = [{ label: activeSource?.label ?? "/", path: "/" }];
    let acc = "";
    for (const part of parts) {
      acc += `/${part}`;
      crumbs.push({ label: part, path: acc });
    }
    return crumbs;
  }

  async function download(entry: FileEntryOut) {
    if (!sourceId) return;
    setBusy(true);
    try {
      const token = useAuthStore.getState().accessToken;
      const res = await fetch(
        `/api/v1/files/${encodeURIComponent(sourceId)}/download?path=${encodeURIComponent(entry.path)}`,
        { headers: token ? { Authorization: `Bearer ${token}` } : {} },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = entry.name;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  }

  async function upload(file: File) {
    if (!sourceId) return;
    setBusy(true);
    setMessage(null);
    setUploadProgress({ name: file.name, percent: 0 });
    try {
      await uploadWithProgress(sourceId, path, file, (percent) => setUploadProgress({ name: file.name, percent }));
      setMessage(`'${file.name}' hochgeladen.`);
      loadDir(sourceId, path);
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
      setUploadProgress(null);
    }
  }

  async function mkdir() {
    if (!sourceId) return;
    const name = await promptDialog("Name des neuen Ordners:");
    if (!name) return;
    try {
      await api.post(`/files/${encodeURIComponent(sourceId)}/mkdir`, { path: joinPath(path, name) });
      loadDir(sourceId, path);
    } catch (err) {
      setMessage(`Fehler: ${err instanceof ApiError ? err.message : String(err)}`);
    }
  }

  async function rename(entry: FileEntryOut) {
    if (!sourceId) return;
    const newName = await promptDialog(`Neuer Name für '${entry.name}':`, entry.name);
    if (!newName || newName === entry.name) return;
    try {
      await api.post(`/files/${encodeURIComponent(sourceId)}/rename`, { src: entry.path, dst: joinPath(path, newName) });
      loadDir(sourceId, path);
    } catch (err) {
      setMessage(`Fehler: ${err instanceof ApiError ? err.message : String(err)}`);
    }
  }

  async function remove(entry: FileEntryOut) {
    if (!sourceId) return;
    const ok = await confirmDialog(`'${entry.name}' wirklich löschen?`, { danger: true, confirmLabel: "Löschen" });
    if (!ok) return;
    try {
      // Ein Link auf einen Ordner ist auch ein Ordner (`is_dir`), wird aber nur als Link
      // entfernt: rekursives Löschen lehnt die Quelle bei Links ab, das Ziel bleibt.
      const recursive = entry.is_dir && entry.metadata?.symlink !== true;
      await api.post(`/files/${encodeURIComponent(sourceId)}/remove`, { path: entry.path, recursive });
      loadDir(sourceId, path);
    } catch (err) {
      setMessage(`Fehler: ${err instanceof ApiError ? err.message : String(err)}`);
    }
  }

  const DRAG_MIME = "application/x-nodvard-deck-file-entry";

  function onEntryDragStart(e: DragEvent, entry: FileEntryOut) {
    if (!sourceId || !canTransfer(entry)) return; // Ordner (siehe Modul-Docstring) und Links ins Leere sind keine Transfer-Quelle
    e.dataTransfer.effectAllowed = "copy";
    e.dataTransfer.setData(DRAG_MIME, JSON.stringify({ sourceId, path: entry.path, name: entry.name, size: entry.size }));
    setDraggingPath(entry.path);
  }

  function onEntryDragEnd() {
    setDraggingPath(null);
    setDropTargetKey(null);
  }

  function canDropAt(targetSourceId: string, key: string) {
    return (e: DragEvent) => {
      if (!e.dataTransfer.types.includes(DRAG_MIME)) return;
      const target = sources?.find((s) => s.source_id === targetSourceId);
      if (!target?.caps.write) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
      setDropTargetKey(key);
    };
  }

  async function startTransfer(
    dragged: { sourceId: string; path: string; name: string; size?: number | null },
    targetSourceId: string,
    targetDirPath: string,
    move = false,
  ) {
    const destPath = joinPath(targetDirPath, dragged.name);
    if (dragged.sourceId === targetSourceId && destPath === dragged.path) {
      setMessage("Ziel ist identisch mit der Quelle – nichts zu tun.");
      return;
    }
    try {
      const res = await api.post<{ run_id: string }>("/files/transfer", {
        from: { source: dragged.sourceId, path: dragged.path },
        to: { source: targetSourceId, path: destPath },
      });
      setTransferRunId(res.run_id);
      if (move) setPendingMove({ runId: res.run_id, sourceId: dragged.sourceId, path: dragged.path, name: dragged.name, size: dragged.size ?? null });
      const targetLabel = sources?.find((s) => s.source_id === targetSourceId)?.label ?? targetSourceId;
      setMessage(`${move ? "Verschiebe" : "Kopiere"} '${dragged.name}' nach ${targetLabel} · ${destPath} …`);
    } catch (err) {
      setMessage(`Fehler: ${err instanceof ApiError ? err.message : String(err)}`);
    }
  }

  function readDragged(e: DragEvent): { sourceId: string; path: string; name: string; size?: number | null } | null {
    e.preventDefault();
    setDropTargetKey(null);
    const raw = e.dataTransfer.getData(DRAG_MIME);
    return raw ? (JSON.parse(raw) as { sourceId: string; path: string; name: string; size?: number | null }) : null;
  }

  async function handleDrop(e: DragEvent, targetSourceId: string, targetDirPath: string) {
    const dragged = readDragged(e);
    if (dragged) await startTransfer(dragged, targetSourceId, targetDirPath);
  }

  /** Ablegen auf eine Quelle in der Seitenleiste: Zielordner im Dialog waehlen,
   * statt blind in deren Wurzel zu kopieren. */
  function handleSourceDrop(e: DragEvent, targetSourceId: string) {
    const dragged = readDragged(e);
    if (dragged) setPicker({ fromSourceId: dragged.sourceId, entry: dragged, targetSourceId });
  }

  function onPicked(choice: FolderChoice) {
    if (!picker) return;
    void startTransfer({ sourceId: picker.fromSourceId, ...picker.entry }, choice.sourceId, choice.dirPath, choice.move);
  }

  async function runSearch() {
    if (!query.trim()) return;
    try {
      const hits = await api.get<SearchHit[]>(`/files/search?q=${encodeURIComponent(query)}`);
      setSearchHits(hits);
    } catch (err) {
      setMessage(`Fehler: ${err instanceof ApiError ? err.message : String(err)}`);
    }
  }

  return (
    // Handy: Quellen ueber der Liste statt fest 192 px daneben.
    <div className="flex h-full flex-col gap-4 p-4 sm:p-6 lg:flex-row">
      <div className={`w-full shrink-0 ${sources?.length === 0 ? "lg:max-w-xl" : "lg:w-48"}`}>
        <h2 className="mb-2 text-lg font-semibold">Dateien</h2>
        {error && <p className="text-sm text-red-400">Fehler: {error}</p>}
        {!sources && !error && <p className="text-sm opacity-60">Lade …</p>}
        {sources && sources.length === 0 && (
          <EmptyState
            compact
            icon="folder-open"
            testId="files-no-sources"
            title="Noch keine Dateiquellen"
            text={
              canManageExtensions
                ? "Dateien kommen von Modulen. Schalte unter Einstellungen → Erweiterungen eines ein, zum Beispiel Terminal (Dateien auf deinen Servern, dafür braucht der Server einen SSH-Zugang) oder Nextcloud."
                : "Dateien kommen von Modulen. Sobald ein Administrator eines einschaltet, erscheinen sie hier. Dateien auf Servern sieht nur, wer auch Befehle auf Servern ausführen darf."
            }
            action={
              canManageExtensions || canWriteHosts ? (
                <>
                  {canManageExtensions && <ButtonLink to="/settings/extensions">Module ansehen</ButtonLink>}
                  {canWriteHosts && <ButtonLink to="/settings/hosts" variant={canManageExtensions ? "secondary" : "primary"}>Server &amp; Zugänge</ButtonLink>}
                </>
              ) : undefined
            }
          />
        )}
        <ul className="text-sm">
          {sources?.map((s) => (
            <li key={s.source_id}>
              <button
                type="button"
                onClick={() => {
                  setSourceId(s.source_id);
                  setPath("/");
                  setSearchHits(null);
                }}
                onDragOver={canDropAt(s.source_id, `side:${s.source_id}`)}
                onDragLeave={() => setDropTargetKey((k) => (k === `side:${s.source_id}` ? null : k))}
                onDrop={(e) => handleSourceDrop(e, s.source_id)}
                title={s.caps.write ? `Datei hier ablegen, um sie in einen Ordner von '${s.label}' zu kopieren` : undefined}
                className={`w-full rounded px-2 py-1 text-left ${sourceId === s.source_id ? "bg-white/10 font-medium" : "opacity-80 hover:opacity-100"} ${dropTargetKey === `side:${s.source_id}` ? "outline outline-2 outline-[var(--color-accent)]" : ""}`}
              >
                {s.label}
              </button>
            </li>
          ))}
        </ul>
      </div>

      {/* Ohne Quelle gibt es nichts zu durchsuchen oder zu zeigen -- der Leerzustand links reicht. */}
      <div className={`min-w-0 flex-1 ${sources?.length === 0 ? "hidden" : ""}`}>
        <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
          {breadcrumbs().map((c, i) => (
            <span key={c.path}>
              {i > 0 && <span className="opacity-40"> / </span>}
              <button
                type="button"
                className={`opacity-80 hover:opacity-100 hover:underline ${dropTargetKey === `crumb:${c.path}` ? "rounded outline outline-2 outline-[var(--color-accent)]" : ""}`}
                onClick={() => setPath(c.path)}
                onDragOver={sourceId ? canDropAt(sourceId, `crumb:${c.path}`) : undefined}
                onDragLeave={() => setDropTargetKey((k) => (k === `crumb:${c.path}` ? null : k))}
                onDrop={(e) => sourceId && void handleDrop(e, sourceId, c.path)}
              >
                {c.label}
              </button>
            </span>
          ))}
        </div>

        <div className="mb-3 flex flex-wrap items-center gap-2">
          <input
            className="rounded bg-white/10 px-2 py-1 text-sm"
            placeholder="Suche über alle Quellen …"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void runSearch()}
          />
          <button type="button" onClick={() => void runSearch()} className="rounded bg-white/10 px-2 py-1 text-xs hover:bg-white/20">
            Suchen
          </button>
          {activeSource?.caps.mkdir && (
            <button type="button" onClick={() => void mkdir()} className="rounded bg-white/10 px-2 py-1 text-xs hover:bg-white/20">
              + Ordner
            </button>
          )}
          {activeSource?.caps.write && (
            <>
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) void upload(file);
                  e.target.value = "";
                }}
              />
              <button
                type="button"
                disabled={busy}
                onClick={() => fileInputRef.current?.click()}
                className="rounded bg-white/10 px-2 py-1 text-xs hover:bg-white/20 disabled:opacity-40"
              >
                Hochladen
              </button>
            </>
          )}
        </div>
        {message && <p className="mb-2 text-sm opacity-80">{message}</p>}
        {uploadProgress && (
          <div className="mb-2">
            <p className="mb-1 text-xs opacity-70">
              '{uploadProgress.name}' wird hochgeladen … {uploadProgress.percent}%
            </p>
            <div role="progressbar" aria-valuenow={uploadProgress.percent} aria-valuemin={0} aria-valuemax={100} className="h-1.5 w-full overflow-hidden rounded bg-white/10">
              <div className="h-full bg-[var(--color-accent)] transition-[width]" style={{ width: `${uploadProgress.percent}%` }} />
            </div>
          </div>
        )}

        {searchHits && (
          <div className="mb-4">
            <div className="mb-1 flex items-center justify-between">
              <h3 className="text-sm font-semibold opacity-80">Suchergebnisse für '{query}'</h3>
              <button type="button" onClick={() => setSearchHits(null)} className="text-xs opacity-60 hover:opacity-100">
                schliessen
              </button>
            </div>
            <ul className="text-sm">
              {searchHits.length === 0 && <li className="opacity-60">Keine Treffer.</li>}
              {searchHits.map((hit) => (
                <li key={`${hit.source_id}:${hit.entry.path}`} className="py-0.5">
                  <span className="opacity-50">{hit.source_id}</span> {hit.entry.path}
                </li>
              ))}
            </ul>
          </div>
        )}

        {!entries && !error && <p className="text-sm opacity-60">Lade …</p>}
        {entries && entries.length === 0 && <p className="text-sm opacity-60">Leer.</p>}
        {entries && entries.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/10 text-left text-xs uppercase opacity-60">
                  <th className="py-1 w-1/2">Name</th>
                  <th className="py-1 whitespace-nowrap">Größe</th>
                  <th className="py-1 whitespace-nowrap">Geändert</th>
                  <th className="py-1 whitespace-nowrap">Aktionen</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {entries.map((entry) => (
                  <tr
                    key={entry.path}
                    draggable={canTransfer(entry)}
                    onDragStart={(e) => onEntryDragStart(e, entry)}
                    onDragEnd={onEntryDragEnd}
                    onDragOver={entry.is_dir && sourceId ? canDropAt(sourceId, `dir:${entry.path}`) : undefined}
                    onDragLeave={entry.is_dir ? () => setDropTargetKey((k) => (k === `dir:${entry.path}` ? null : k)) : undefined}
                    onDrop={entry.is_dir && sourceId ? (e) => void handleDrop(e, sourceId, entry.path) : undefined}
                    title={canTransfer(entry) ? "Ziehen, um in eine andere Quelle oder einen Ordner zu kopieren" : undefined}
                    className={[
                      canTransfer(entry) && "cursor-grab active:cursor-grabbing",
                      draggingPath === entry.path && "opacity-40",
                      dropTargetKey === `dir:${entry.path}` && "outline outline-2 outline-[var(--color-accent)]",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                  >
                    <td className="py-1.5 break-words">
                      {entry.is_dir ? (
                        <button type="button" className="hover:underline" onClick={() => setPath(entry.path)}>
                          📁 {entry.name}
                        </button>
                      ) : (
                        <span>{isBrokenLink(entry) ? "" : "⠿ "}{entry.name}</span>
                      )}
                      {entry.metadata?.symlink === true && <LinkMark />}
                      {isBrokenLink(entry) && (
                        <span className="ml-2 text-xs text-amber-400" title="Das Ziel des Links fehlt oder lässt sich nicht lesen.">
                          Link ins Leere
                        </span>
                      )}
                    </td>
                    <td className="py-1.5 whitespace-nowrap opacity-70">{entry.is_dir ? "" : formatSize(entry.size)}</td>
                    <td className="py-1.5 whitespace-nowrap opacity-70">{entry.modified_at ? new Date(entry.modified_at).toLocaleString() : ""}</td>
                    <td className="py-1.5 whitespace-nowrap">
                      <div className="flex gap-1.5">
                        {canTransfer(entry) && (
                          <button type="button" disabled={busy} onClick={() => void download(entry)} className="rounded bg-white/10 px-1.5 py-0.5 text-xs hover:bg-white/20">
                            Laden
                          </button>
                        )}
                        {canTransfer(entry) && sourceId && (
                          <button
                            type="button"
                            onClick={() => setPicker({ fromSourceId: sourceId, entry: { path: entry.path, name: entry.name, size: entry.size }, targetSourceId: null })}
                            className="rounded bg-white/10 px-1.5 py-0.5 text-xs hover:bg-white/20"
                          >
                            Kopieren nach …
                          </button>
                        )}
                        {activeSource?.caps.rename && (
                          <button type="button" onClick={() => void rename(entry)} className="rounded bg-white/10 px-1.5 py-0.5 text-xs hover:bg-white/20">
                            Umbenennen
                          </button>
                        )}
                        {activeSource?.caps.remove && (
                          <button type="button" onClick={() => void remove(entry)} className="rounded bg-red-500/20 px-1.5 py-0.5 text-xs hover:bg-red-500/30">
                            Löschen
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <FolderPickerDialog
        open={picker !== null}
        onOpenChange={(open) => !open && setPicker(null)}
        sources={sources ?? []}
        fileName={picker?.entry.name ?? ""}
        initialSourceId={picker?.targetSourceId ?? null}
        allowMove={Boolean(sources?.find((s) => s.source_id === picker?.fromSourceId)?.caps.remove)}
        onConfirm={onPicked}
      />
    </div>
  );
}
