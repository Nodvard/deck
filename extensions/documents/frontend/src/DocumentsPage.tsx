/**
 * Dokumente-Seite (Dokumentenarchiv im Stil von Paperless-ngx) --
 * PageSpec `component="DocumentsPage"` (siehe nodvard_deck_ext_documents/__init__.py).
 * Ruft ausschliesslich die eigenen `/ext/documents/...`-Endpunkte auf.
 *
 * Authentifizierung wie jede andere Extension-Seite: `window.__nodvardDeck.
 * getAccessToken()` (kein Zugriff auf state/auth.ts ueber den Import-Map-Shim).
 */
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";

import { authedFetch, errorFromBody } from "../../../_shared/frontend/src/api";
import { Badge, Button, Card, EmptyState, Icon, Loading, Notice, Page, SearchInput, Stat, buttonClass, inputClass } from "../../../_shared/frontend/src/ui";
import { deck } from "../../../_shared/frontend/src/deck";

interface Tag {
  id: string;
  name: string;
  match_keyword: string | null;
}

interface Doc {
  id: string;
  original_filename: string;
  content_type: string;
  size_bytes: number;
  page_count: number | null;
  ocr_text: string | null;
  ocr_status: string;
  ocr_error: string | null;
  tags: Tag[];
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function typeLabel(contentType: string): string {
  if (contentType === "application/pdf") return "PDF";
  if (contentType.startsWith("image/")) return contentType.slice(6).toUpperCase();
  return contentType;
}

function TagPanel({ tags, onChanged }: { tags: Tag[]; onChanged: () => void }): JSX.Element {
  const [name, setName] = useState("");
  const [matchKeyword, setMatchKeyword] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  async function addTag(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    const res = await authedFetch("/ext/documents/tags", {
      method: "POST", body: JSON.stringify({ name: name.trim(), match_keyword: matchKeyword.trim() || null }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) { setMessage(`Fehler: ${errorFromBody(body, res.status)}`); return; }
    setName("");
    setMatchKeyword("");
    onChanged();
  }

  async function removeTag(id: string) {
    const ok = await deck().confirmDialog("Tag wirklich entfernen?", { danger: true });
    if (!ok) return;
    await authedFetch(`/ext/documents/tags/${id}`, { method: "DELETE" });
    onChanged();
  }

  return (
    <Card
      title="Tags"
      description="Mit Auto-Stichwort wird ein Tag automatisch vergeben, sobald das Wort im erkannten Text vorkommt (z. B. „Rechnung“)."
      className="mb-5"
    >
      {message && <Notice text={message} onClose={() => setMessage(null)} />}
      <div className="mb-4 flex flex-wrap gap-1.5">
        {tags.map((t) => (
          <span key={t.id} className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] py-1 pl-2.5 pr-1.5 text-sm">
            <Icon name="tag" size={12} className="text-[var(--color-accent)]" />
            {t.name}
            {t.match_keyword && <span className="text-xs text-white/40">„{t.match_keyword}“</span>}
            <button type="button" aria-label={`Tag ${t.name} löschen`} onClick={() => void removeTag(t.id)} className="rounded p-0.5 text-white/40 hover:bg-white/10 hover:text-white">
              <Icon name="x" size={12} />
            </button>
          </span>
        ))}
        {tags.length === 0 && <span className="text-sm text-white/40">Noch keine Tags.</span>}
      </div>
      <form onSubmit={(e) => void addTag(e)} className="flex flex-wrap gap-2">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Neuer Tag" aria-label="Neuer Tag" className={`${inputClass} flex-1`} />
        <input
          value={matchKeyword} onChange={(e) => setMatchKeyword(e.target.value)}
          placeholder="Auto-Stichwort (optional)" aria-label="Auto-Stichwort" className={`${inputClass} flex-1`}
        />
        <Button type="submit"><Icon name="plus" size={14} /> Tag anlegen</Button>
      </form>
    </Card>
  );
}

export function DocumentsPage(): JSX.Element {
  const [documents, setDocuments] = useState<Doc[] | null>(null);
  const [tags, setTags] = useState<Tag[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const [showTags, setShowTags] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ id: string; url: string; type: string } | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [attachTagId, setAttachTagId] = useState<Record<string, string>>({});

  const load = useCallback(() => {
    setError(null);
    const params = new URLSearchParams();
    if (search) params.set("q", search);
    if (tagFilter) params.set("tag_id", tagFilter);
    const qs = params.toString() ? `?${params.toString()}` : "";
    Promise.all([
      authedFetch(`/ext/documents/documents${qs}`).then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() as Promise<Doc[]>; }),
      authedFetch("/ext/documents/tags").then((r) => r.json() as Promise<Tag[]>),
    ])
      .then(([docsData, tagsData]) => { setDocuments(docsData); setTags(tagsData); })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, [search, tagFilter]);

  useEffect(() => { load(); }, [load]);

  async function uploadFile(file: File) {
    setUploading(true);
    setMessage(null);
    try {
      const res = await authedFetch(`/ext/documents/documents?filename=${encodeURIComponent(file.name)}`, {
        method: "POST", body: file, headers: { "Content-Type": file.type || "application/octet-stream" },
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(errorFromBody(body, res.status));
      }
      load();
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setUploading(false);
    }
  }

  async function removeDocument(doc: Doc) {
    const ok = await deck().confirmDialog(`"${doc.original_filename}" wirklich löschen?`, { danger: true, confirmLabel: "Löschen" });
    if (!ok) return;
    await authedFetch(`/ext/documents/documents/${doc.id}`, { method: "DELETE" });
    load();
  }

  async function attachTag(doc: Doc) {
    const tagId = attachTagId[doc.id];
    if (!tagId) return;
    await authedFetch(`/ext/documents/documents/${doc.id}/tags/${tagId}`, { method: "POST" });
    setAttachTagId({ ...attachTagId, [doc.id]: "" });
    load();
  }

  async function detachTag(doc: Doc, tagId: string) {
    await authedFetch(`/ext/documents/documents/${doc.id}/tags/${tagId}`, { method: "DELETE" });
    load();
  }

  async function fetchBlob(doc: Doc): Promise<Blob | null> {
    const res = await authedFetch(`/ext/documents/documents/${doc.id}/download`);
    if (!res.ok) { setMessage(`Fehler: HTTP ${res.status}`); return null; }
    return res.blob();
  }

  /** Vorschau im Browser (PDF/Bild) statt erst herunterladen -- ueber eine Blob-URL,
   * weil der Download-Endpunkt den Bearer-Token braucht (kein Cookie-Login). */
  async function togglePreview(doc: Doc) {
    if (preview?.id === doc.id) {
      URL.revokeObjectURL(preview.url);
      setPreview(null);
      return;
    }
    const blob = await fetchBlob(doc);
    if (!blob) return;
    if (preview) URL.revokeObjectURL(preview.url);
    setPreview({ id: doc.id, url: URL.createObjectURL(blob), type: doc.content_type });
    setExpanded(doc.id);
  }

  async function renameDocument(doc: Doc) {
    const name = await deck().promptDialog(`Neuer Name für „${doc.original_filename}“:`);
    if (!name || name.trim() === doc.original_filename) return;
    const res = await authedFetch(`/ext/documents/documents/${doc.id}`, {
      method: "PATCH", body: JSON.stringify({ original_filename: name.trim() }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setMessage(`Fehler: ${errorFromBody(body, res.status)}`);
      return;
    }
    load();
  }

  async function copyText(doc: Doc) {
    try {
      await navigator.clipboard.writeText(doc.ocr_text ?? "");
      setCopied(doc.id);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      setMessage("Kopieren nicht möglich (Browser erlaubt die Zwischenablage nur über HTTPS).");
    }
  }

  async function downloadDocument(doc: Doc) {
    const res = await authedFetch(`/ext/documents/documents/${doc.id}/download`);
    if (!res.ok) { setMessage(`Fehler: HTTP ${res.status}`); return; }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = window.document.createElement("a");
    a.href = url;
    a.download = doc.original_filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  if (!documents && !error) return <Loading />;
  if (error) return <Page title="Dokumente"><Notice text={`Fehler: ${error}`} /></Page>;

  const uploadButton = (label: string) => (
    <label className={`${buttonClass("primary")} cursor-pointer`}>
      <Icon name="upload" size={14} /> {uploading ? "Lädt hoch …" : label}
      <input
        type="file" accept="application/pdf,image/png,image/jpeg,image/webp" className="hidden"
        onChange={(e) => { const file = e.target.files?.[0]; if (file) void uploadFile(file); e.target.value = ""; }}
      />
    </label>
  );

  const docs = documents ?? [];
  const totalBytes = docs.reduce((sum, d) => sum + d.size_bytes, 0);
  const ocrFailed = docs.filter((d) => d.ocr_status === "error").length;
  const untagged = docs.filter((d) => d.tags.length === 0).length;
  const filtering = Boolean(search || tagFilter);

  return (
    <Page
      title="Dokumente"
      description="Rechnungen, Verträge und Handbücher als PDF oder Scan – mit Texterkennung und Volltextsuche."
      actions={
        <>
          <Button onClick={() => setShowTags((v) => !v)} pressed={showTags}><Icon name="tag" size={14} /> Tags verwalten</Button>
          {uploadButton("Dokument hochladen")}
        </>
      }
    >
      {showTags && <TagPanel tags={tags} onChanged={load} />}
      {message && <Notice text={message} onClose={() => setMessage(null)} />}

      {docs.length === 0 && !filtering ? (
        <EmptyState
          icon="file"
          title="Noch keine Dokumente erfasst."
          text="Lade PDFs oder Fotos von Dokumenten hoch. Der Text wird automatisch erkannt, damit du später per Suche alles wiederfindest."
          action={uploadButton("Erstes Dokument hochladen")}
        />
      ) : (
        <>
          <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Dokumente" value={docs.length} />
            <Stat label="Speicher" value={formatSize(totalBytes)} />
            <Stat label="Ohne Tag" value={untagged} />
            <Stat label="Texterkennung fehlgeschlagen" value={ocrFailed} tone={ocrFailed ? "warn" : undefined} />
          </div>
          <div className="mb-3 flex flex-wrap gap-2">
            <SearchInput value={search} onChange={setSearch} placeholder="Volltextsuche …" label="Volltextsuche" />
            <select value={tagFilter} onChange={(e) => setTagFilter(e.target.value)} aria-label="Nach Tag filtern" className={`${inputClass} w-auto`}>
              <option value="">Alle Tags</option>
              {tags.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </div>

          <ul className="panel divide-y divide-white/[0.06]">
            {docs.length === 0 && <li className="px-5 py-8 text-center text-sm text-white/45">Keine Treffer.</li>}
            {docs.map((doc) => {
              const isExpanded = expanded === doc.id;
              const attachableTags = tags.filter((t) => !doc.tags.some((dt) => dt.id === t.id));
              return (
                <li key={doc.id} data-testid={`document-${doc.id}`} className="px-4 py-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <button
                      type="button" onClick={() => setExpanded(isExpanded ? null : doc.id)}
                      aria-label={isExpanded ? "Details ausblenden" : "Details anzeigen"} aria-expanded={isExpanded}
                      className="rounded p-1 text-white/40 hover:bg-white/[0.06] hover:text-white"
                    >
                      <Icon name={isExpanded ? "chevron-down" : "chevron-right"} size={15} />
                    </button>
                    <span className="grid h-11 w-11 flex-none place-items-center rounded-lg bg-white/[0.05] text-[var(--color-accent)]">
                      <Icon name="file" size={18} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{doc.original_filename}</p>
                      <p className="text-xs text-white/50">
                        {typeLabel(doc.content_type)}{doc.page_count ? ` · ${doc.page_count} Seite(n)` : ""} · {formatSize(doc.size_bytes)}
                      </p>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {doc.ocr_status === "error" && <Badge tone="warn">Texterkennung fehlgeschlagen</Badge>}
                        {doc.tags.map((t) => (
                          <Badge key={t.id} tone="accent">
                            {t.name}
                            <button type="button" onClick={() => void detachTag(doc, t.id)} aria-label={`Tag ${t.name} entfernen`} className="opacity-60 hover:opacity-100">
                              <Icon name="x" size={10} />
                            </button>
                          </Badge>
                        ))}
                      </div>
                    </div>
                    <div className="flex w-full flex-wrap items-center gap-1 sm:w-auto sm:flex-none">
                      <Button small onClick={() => void togglePreview(doc)} pressed={preview?.id === doc.id}>
                        <Icon name="eye" size={13} /> {preview?.id === doc.id ? "Vorschau schließen" : "Ansehen"}
                      </Button>
                      <Button small onClick={() => void renameDocument(doc)}><Icon name="edit" size={13} /> Umbenennen</Button>
                      <Button small onClick={() => void downloadDocument(doc)}><Icon name="download" size={13} /> Herunterladen</Button>
                      <Button small variant="danger" onClick={() => void removeDocument(doc)}><Icon name="trash" size={13} /> Löschen</Button>
                    </div>
                  </div>
                  {isExpanded && (
                    <div className="ml-10 mt-3 space-y-3 rounded-lg border border-white/[0.06] bg-black/15 p-4 text-sm">
                      {preview?.id === doc.id && (
                        <div data-testid={`preview-${doc.id}`}>
                          {preview.type.startsWith("image/") ? (
                            <img src={preview.url} alt={doc.original_filename} className="max-h-[70vh] rounded-lg border border-white/10" />
                          ) : (
                            <iframe src={preview.url} title={doc.original_filename} className="h-[70vh] w-full rounded-lg border border-white/10 bg-white" />
                          )}
                        </div>
                      )}
                      {doc.ocr_error && <p className="text-amber-300">Texterkennung: {doc.ocr_error}</p>}
                      <div className="flex max-w-md items-center gap-2">
                        <select
                          value={attachTagId[doc.id] ?? ""} aria-label={`Tag zu ${doc.original_filename} hinzufügen`}
                          onChange={(e) => setAttachTagId({ ...attachTagId, [doc.id]: e.target.value })}
                          className={inputClass}
                        >
                          <option value="">Tag hinzufügen …</option>
                          {attachableTags.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                        </select>
                        <Button onClick={() => void attachTag(doc)} disabled={!attachTagId[doc.id]}>Anhängen</Button>
                      </div>
                      <div>
                        <div className="mb-1.5 flex items-center justify-between">
                          <p className="text-xs text-white/45">Erkannter Text</p>
                          {doc.ocr_text && (
                            <Button small variant="ghost" onClick={() => void copyText(doc)}>
                              <Icon name="copy" size={12} /> {copied === doc.id ? "Kopiert ✓" : "Text kopieren"}
                            </Button>
                          )}
                        </div>
                        <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-black/30 p-3 font-mono text-xs text-white/75">
                          {doc.ocr_text || "(kein Text erkannt)"}
                        </pre>
                      </div>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </Page>
  );
}
