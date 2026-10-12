/**
 * Vorschau einer Datei im Dateimanager (N4): Text und Bilder, direkt auf der Seite -- ohne neuen Endpunkt, über
 * `GET /files/{source}/download` (bei Text nur der Anfang, per `Range`).
 *
 * Bewusst NICHT: HTML, SVG, PDF. Eine fremde Datei darf im Dashboard keinen Code ausführen -- Text steht als reiner
 * Text in `<pre>`, Bilder kommen über `<img>` aus einer Blob-Adresse, deren Typ die Vorschau selbst festlegt (nie den
 * des Servers). SVG fehlt, weil seine Blob-Adresse, in einem neuen Tab geöffnet, Skripte im Dashboard ausführen
 * könnte. Alles andere: „Laden“.
 */
import { useEffect, useRef, useState } from "react";

import { apiErrorFromResponse, apiFetchResponse } from "../lib/api";

export const TEXT_LIMIT = 256 * 1024;
/** Mehr liest die Vorschau einer Textdatei nicht (der Rest bleibt für „Laden“). */
export const IMAGE_LIMIT = 10 * 1024 * 1024;
/** Größere Bilder zeigt die Vorschau nicht (das Laden im Browser würde zäh). */

const IMAGE_TYPES: Record<string, string> = {
  png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp", bmp: "image/bmp",
  ico: "image/x-icon", avif: "image/avif",
};
const TEXT_EXT = new Set([
  "txt", "log", "md", "markdown", "rst", "csv", "tsv", "json", "jsonl", "yml", "yaml", "toml", "ini", "cfg", "conf", "env",
  "properties", "xml", "html", "htm", "css", "scss", "js", "mjs", "cjs", "ts", "tsx", "jsx", "py", "sh", "bash", "zsh",
  "fish", "ps1", "bat", "go", "rs", "java", "kt", "c", "h", "cpp", "hpp", "cs", "rb", "php", "pl", "lua", "sql", "service",
  "timer", "socket", "rules", "list", "sources", "lock", "gitignore", "dockerignore", "editorconfig", "nginx", "vhost",
]);
const TEXT_NAMES = new Set(["dockerfile", "makefile", "readme", "license", "changelog", "hosts", "fstab", "crontab", "authorized_keys", "known_hosts"]);

export type PreviewKind = "text" | "image";

export interface PreviewEntry {
  path: string;
  name: string;
  size: number | null;
}

function extension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : name.startsWith(".") ? name.slice(1).toLowerCase() : "";
}

/** Was die Vorschau mit dieser Datei anfangen kann -- oder `null` (dann nur „Laden“). */
export function previewKind(entry: PreviewEntry): PreviewKind | null {
  const ext = extension(entry.name);
  if (ext in IMAGE_TYPES) return entry.size != null && entry.size > IMAGE_LIMIT ? null : "image";
  if (TEXT_EXT.has(ext) || TEXT_NAMES.has(entry.name.toLowerCase())) return "text";
  return null;
}

/**
 * Höchstens `limit` Bytes einer Antwort lesen und den Rest abbrechen. Nötig, weil der Server bei `Range` nur den
 * Anfang beachtet und sonst bis zum Ende der Datei schickt -- eine riesige Logdatei käme sonst ganz in den Browser.
 * `more`: es gab mehr als `limit`.
 */
export async function readHead(res: Response, limit: number): Promise<{ bytes: Uint8Array; more: boolean }> {
  if (!res.body) {
    const all = new Uint8Array(await res.arrayBuffer());
    return { bytes: all.slice(0, limit), more: all.length > limit };
  }
  const reader = res.body.getReader();
  const parts: Uint8Array[] = [];
  let total = 0;
  let more = false;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    parts.push(value);
    total += value.length;
    if (total > limit) {
      more = true;
      await reader.cancel().catch(() => undefined);
      break;
    }
  }
  const bytes = new Uint8Array(Math.min(total, limit));
  let at = 0;
  for (const part of parts) {
    const take = Math.min(part.length, bytes.length - at);
    bytes.set(part.subarray(0, take), at);
    at += take;
    if (at >= bytes.length) break;
  }
  return { bytes, more };
}

/** Sieht der Anfang nach Text aus? Ein Nullbyte heißt: eher nicht (Programm, Archiv, Datenbank). */
export function looksLikeText(text: string): boolean {
  return !text.slice(0, 8000).includes("\u0000");
}

type State =
  | { kind: "loading" }
  | { kind: "text"; text: string; truncated: boolean }
  | { kind: "image"; url: string }
  | { kind: "binary" }
  | { kind: "error"; message: string };

export function FilePreview({
  sourceId, entry, kind, onClose, onDownload,
}: {
  sourceId: string;
  entry: PreviewEntry;
  kind: PreviewKind;
  onClose: () => void;
  onDownload: () => void;
}): JSX.Element {
  const [state, setState] = useState<State>({ kind: "loading" });
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const sectionRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    let url: string | null = null;
    let active = true;
    // Beim Schließen oder Wechsel der Datei den Abruf abbrechen, statt ihn bis zur Grenze im Hintergrund weiterlaufen zu lassen.
    const controller = new AbortController();
    setState({ kind: "loading" });
    const path = `/files/${encodeURIComponent(sourceId)}/download?path=${encodeURIComponent(entry.path)}`;
    const init: RequestInit = kind === "text"
      ? { headers: { Range: `bytes=0-${TEXT_LIMIT - 1}` }, signal: controller.signal }
      : { signal: controller.signal };
    (async () => {
      const res = await apiFetchResponse(path, init);
      if (!res.ok) throw await apiErrorFromResponse(res);
      const { bytes, more } = await readHead(res, kind === "image" ? IMAGE_LIMIT : TEXT_LIMIT);
      if (!active) return;
      if (kind === "image") {
        if (more) throw new Error(`Das Bild ist größer als ${IMAGE_LIMIT / 1024 / 1024} MB.`);
        url = URL.createObjectURL(new Blob([bytes as Uint8Array<ArrayBuffer>], { type: IMAGE_TYPES[extension(entry.name)] }));
        setState({ kind: "image", url });
        return;
      }
      // `fatal: false`: ein am Ende abgeschnittenes Zeichen wird zu „�“ statt zu einem Fehler.
      const text = new TextDecoder("utf-8", { fatal: false }).decode(bytes);
      if (!looksLikeText(text)) {
        setState({ kind: "binary" });
        return;
      }
      setState({ kind: "text", text, truncated: more || (entry.size != null && entry.size > TEXT_LIMIT) });
    })().catch((err: unknown) => {
      if (active) setState({ kind: "error", message: err instanceof Error ? err.message : String(err) });
    });
    return () => {
      active = false;
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [sourceId, entry.path, entry.size, kind]);

  useEffect(() => {
    // Fokus auf „Schließen“ und danach zurück dorthin, wo er vorher war (meist der Knopf „Ansehen“).
    const before = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      // Esc in einem Eingabefeld (z. B. Umbenennen) oder in einem anderen offenen Dialog gilt dem, nicht der Vorschau.
      const target = e.target instanceof Element ? e.target : null;
      if (target?.closest("input, textarea, select, [contenteditable='true']")) return;
      const others = Array.from(document.querySelectorAll('[role="dialog"], [role="alertdialog"]'))
        .some((el) => el !== sectionRef.current);
      if (others) return;
      onCloseRef.current();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      // Nur, wenn der Fokus mit der Vorschau verschwunden ist -- nicht, wenn man schon woanders hingeklickt hat.
      const lost = !document.activeElement || document.activeElement === document.body;
      if (lost && before?.isConnected) before.focus();
    };
  }, []);

  return (
    <section
      ref={sectionRef}
      role="dialog"
      aria-label={`Vorschau: ${entry.name}`}
      className="mb-3 rounded-lg border border-white/10 bg-black/30 p-3 text-sm"
      data-testid="file-preview"
    >
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h3 className="min-w-0 flex-1 break-all font-medium">{entry.name}</h3>
        <button type="button" onClick={onDownload} className="rounded bg-white/10 px-2 py-0.5 text-xs hover:bg-white/20">
          Laden
        </button>
        <button ref={closeRef} type="button" onClick={onClose} className="rounded bg-white/10 px-2 py-0.5 text-xs hover:bg-white/20">
          Schließen
        </button>
      </div>
      {state.kind === "loading" && <p className="opacity-60">Lade Vorschau …</p>}
      {state.kind === "error" && <p role="alert" className="text-red-400">Vorschau nicht möglich: {state.message}</p>}
      {state.kind === "binary" && (
        <p className="opacity-70">Die Datei sieht nicht nach Text aus. Mit „Laden“ holst du sie auf deinen Rechner.</p>
      )}
      {state.kind === "image" && (
        <img src={state.url} alt={entry.name} className="max-h-[70vh] max-w-full rounded bg-white/5 object-contain" />
      )}
      {state.kind === "text" && (
        <>
          {state.truncated && (
            <p className="mb-1 text-xs text-amber-300">
              Nur der Anfang ({Math.round(TEXT_LIMIT / 1024)} KB) – die ganze Datei mit „Laden“.
            </p>
          )}
          <pre className="max-h-[70vh] overflow-auto whitespace-pre-wrap break-words rounded bg-black/40 p-2 font-mono text-xs" data-testid="file-preview-text">
            {state.text}
          </pre>
        </>
      )}
    </section>
  );
}
