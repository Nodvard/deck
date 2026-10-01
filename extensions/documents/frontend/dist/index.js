// src/DocumentsPage.tsx
import { useCallback, useEffect, useState } from "react";

// ../../../frontend/src/lib/deckGlobal.ts
function findDeck() {
  if (typeof window === "undefined") return void 0;
  return window.__nodvardDeck ?? window.__lattice;
}
function deck() {
  return findDeck();
}

// ../../_shared/frontend/src/api.ts
var SERVER_UNAVAILABLE_TEXT = "Server gerade nicht erreichbar \u2013 bitte gleich noch einmal versuchen.";
var ServerUnavailableError = class extends Error {
  status;
  constructor(answer) {
    super(answer ? `Server meldet: ${answer.message}` : SERVER_UNAVAILABLE_TEXT);
    this.name = "ServerUnavailableError";
    this.status = answer?.status;
  }
};
var SERVER_DOWN_STATUS = /* @__PURE__ */ new Set([502, 503, 504]);
async function authedFetch(path, init = {}) {
  const send = async (token2) => {
    const headers = new Headers(init.headers);
    if (token2) headers.set("Authorization", `Bearer ${token2}`);
    if (typeof init.body === "string" && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    let res2;
    try {
      res2 = await fetch(`/api/v1${path}`, { ...init, headers });
    } catch (err) {
      if (err instanceof TypeError) throw new ServerUnavailableError();
      throw err;
    }
    if (SERVER_DOWN_STATUS.has(res2.status) && bodyDetail(await res2.clone().json().catch(() => null)) === null) {
      throw new ServerUnavailableError();
    }
    return res2;
  };
  const shell = deck();
  const sent = shell.getAccessToken();
  const res = await send(sent);
  if (res.status !== 401 || !shell.refreshAccessTokenResult && !shell.refreshAccessToken) return res;
  let token = shell.getAccessToken();
  if (!token || token === sent) token = await renewToken();
  if (!token) return res;
  return send(token);
}
async function renewToken() {
  const shell = deck();
  if (shell.refreshAccessTokenResult) {
    const result = await shell.refreshAccessTokenResult().catch(() => ({ status: "unavailable" }));
    if (result.status === "unavailable") {
      const reason = typeof result.message === "string" && result.message.trim() !== "" ? result.message : void 0;
      if (typeof result.httpStatus === "number" && reason !== void 0) {
        throw new ServerUnavailableError({ status: result.httpStatus, message: reason });
      }
      throw new ServerUnavailableError();
    }
    return result.status === "ok" ? result.token : null;
  }
  return shell.refreshAccessToken ? shell.refreshAccessToken().catch(() => null) : null;
}
function bodyDetail(body) {
  const b = typeof body === "object" && body !== null ? body : {};
  if (typeof b.detail === "string" && b.detail) return b.detail;
  if (Array.isArray(b.detail)) {
    const parts = b.detail.map((item) => typeof item === "string" ? item : item?.msg).filter((part) => typeof part === "string" && part !== "");
    if (parts.length > 0) return parts.join("; ");
  }
  const gate = b.gate_decision?.detail;
  if (typeof gate === "string" && gate) return gate;
  return null;
}
function errorFromBody(body, status) {
  return bodyDetail(body) ?? (SERVER_DOWN_STATUS.has(status) ? SERVER_UNAVAILABLE_TEXT : `HTTP ${status}`);
}

// ../../_shared/frontend/src/ui.tsx
import { Fragment, jsx, jsxs } from "react/jsx-runtime";
var inputClass = "w-full rounded-lg border border-white/10 bg-black/25 px-3 py-2 text-sm text-white placeholder:text-white/30 outline-none transition focus:border-[var(--color-accent)] focus:ring-2 focus:ring-[color-mix(in_srgb,var(--color-accent)_30%,transparent)] disabled:opacity-50";
var PATHS = {
  plus: /* @__PURE__ */ jsx("path", { d: "M12 5v14M5 12h14" }),
  search: /* @__PURE__ */ jsxs(Fragment, { children: [
    /* @__PURE__ */ jsx("circle", { cx: "11", cy: "11", r: "7" }),
    /* @__PURE__ */ jsx("path", { d: "m20 20-3.5-3.5" })
  ] }),
  trash: /* @__PURE__ */ jsxs(Fragment, { children: [
    /* @__PURE__ */ jsx("path", { d: "M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" }),
    /* @__PURE__ */ jsx("path", { d: "M10 11v6M14 11v6" })
  ] }),
  edit: /* @__PURE__ */ jsx("path", { d: "M4 20h4L19 9l-4-4L4 16v4zM14 6l4 4" }),
  download: /* @__PURE__ */ jsx("path", { d: "M12 4v11m0 0-4-4m4 4 4-4M5 20h14" }),
  upload: /* @__PURE__ */ jsx("path", { d: "M12 20V9m0 0-4 4m4-4 4 4M5 4h14" }),
  "chevron-right": /* @__PURE__ */ jsx("path", { d: "m9 6 6 6-6 6" }),
  "chevron-down": /* @__PURE__ */ jsx("path", { d: "m6 9 6 6 6-6" }),
  play: /* @__PURE__ */ jsx("path", { d: "M7 4v16l13-8z" }),
  x: /* @__PURE__ */ jsx("path", { d: "M6 6l12 12M18 6 6 18" }),
  tag: /* @__PURE__ */ jsxs(Fragment, { children: [
    /* @__PURE__ */ jsx("path", { d: "M3 12V3h9l9 9-9 9z" }),
    /* @__PURE__ */ jsx("circle", { cx: "7.5", cy: "7.5", r: "1.5" })
  ] }),
  folder: /* @__PURE__ */ jsx("path", { d: "M3 6h6l2 2h10v11H3z" }),
  package: /* @__PURE__ */ jsxs(Fragment, { children: [
    /* @__PURE__ */ jsx("path", { d: "m12 3 9 5v8l-9 5-9-5V8z" }),
    /* @__PURE__ */ jsx("path", { d: "m3 8 9 5 9-5M12 13v8" })
  ] }),
  file: /* @__PURE__ */ jsxs(Fragment, { children: [
    /* @__PURE__ */ jsx("path", { d: "M6 3h8l4 4v14H6z" }),
    /* @__PURE__ */ jsx("path", { d: "M14 3v4h4M9 13h6M9 17h6" })
  ] }),
  copy: /* @__PURE__ */ jsxs(Fragment, { children: [
    /* @__PURE__ */ jsx("rect", { x: "8", y: "8", width: "12", height: "12", rx: "2" }),
    /* @__PURE__ */ jsx("path", { d: "M16 8V4H4v12h4" })
  ] }),
  eye: /* @__PURE__ */ jsxs(Fragment, { children: [
    /* @__PURE__ */ jsx("path", { d: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z" }),
    /* @__PURE__ */ jsx("circle", { cx: "12", cy: "12", r: "3" })
  ] }),
  refresh: /* @__PURE__ */ jsx("path", { d: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7" }),
  clock: /* @__PURE__ */ jsxs(Fragment, { children: [
    /* @__PURE__ */ jsx("circle", { cx: "12", cy: "12", r: "9" }),
    /* @__PURE__ */ jsx("path", { d: "M12 7v5l3 2" })
  ] }),
  code: /* @__PURE__ */ jsx("path", { d: "m8 7-5 5 5 5M16 7l5 5-5 5" }),
  "map-pin": /* @__PURE__ */ jsxs(Fragment, { children: [
    /* @__PURE__ */ jsx("path", { d: "M12 21s-7-6.5-7-12a7 7 0 0 1 14 0c0 5.5-7 12-7 12z" }),
    /* @__PURE__ */ jsx("circle", { cx: "12", cy: "9", r: "2.5" })
  ] })
};
function Icon({ name, size = 16, className = "" }) {
  return /* @__PURE__ */ jsx(
    "svg",
    {
      width: size,
      height: size,
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      strokeWidth: 1.8,
      strokeLinecap: "round",
      strokeLinejoin: "round",
      "aria-hidden": "true",
      className: `flex-none ${className}`,
      children: PATHS[name]
    }
  );
}
function Page({ title, description, actions, children }) {
  return /* @__PURE__ */ jsxs("div", { className: "mx-auto w-full max-w-7xl p-4 sm:p-6", children: [
    /* @__PURE__ */ jsxs("div", { className: "mb-6 flex flex-wrap items-end justify-between gap-3", children: [
      /* @__PURE__ */ jsxs("div", { children: [
        /* @__PURE__ */ jsx("h2", { className: "text-xl font-semibold tracking-tight", children: title }),
        description && /* @__PURE__ */ jsx("p", { className: "mt-1 text-sm text-white/55", children: description })
      ] }),
      actions && /* @__PURE__ */ jsx("div", { className: "flex flex-wrap items-center gap-2", children: actions })
    ] }),
    children
  ] });
}
function Card({ title, description, actions, children, className = "", padded = true }) {
  return /* @__PURE__ */ jsxs("section", { className: `panel overflow-hidden ${className}`, children: [
    (title || actions) && /* @__PURE__ */ jsxs("header", { className: "flex flex-wrap items-center justify-between gap-2 border-b border-white/[0.06] px-5 py-3.5", children: [
      /* @__PURE__ */ jsxs("div", { children: [
        title && /* @__PURE__ */ jsx("h3", { className: "text-sm font-semibold", children: title }),
        description && /* @__PURE__ */ jsx("p", { className: "mt-0.5 text-xs text-white/50", children: description })
      ] }),
      actions && /* @__PURE__ */ jsx("div", { className: "flex items-center gap-2", children: actions })
    ] }),
    /* @__PURE__ */ jsx("div", { className: padded ? "px-5 py-4" : "", children })
  ] });
}
var BUTTON_STYLES = {
  primary: "accent-gradient text-white shadow-md shadow-black/30 hover:brightness-110",
  secondary: "border border-white/10 bg-white/[0.06] text-white hover:bg-white/[0.12]",
  danger: "border border-red-500/40 bg-red-500/15 text-red-200 hover:bg-red-500/25",
  ghost: "text-white/60 hover:bg-white/[0.06] hover:text-white"
};
var buttonClass = (variant = "secondary", small = false) => `inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${small ? "px-2.5 py-1 text-xs" : "px-3 py-1.5 text-sm"} ${BUTTON_STYLES[variant]}`;
function Button({
  children,
  onClick,
  variant = "secondary",
  type = "button",
  disabled,
  small,
  title,
  ariaLabel,
  pressed
}) {
  return /* @__PURE__ */ jsx("button", { type, onClick, disabled, title, "aria-label": ariaLabel, "aria-pressed": pressed, className: buttonClass(variant, small), children });
}
var TONES = {
  neutral: "bg-white/[0.08] text-white/70",
  good: "bg-emerald-500/15 text-emerald-300",
  warn: "bg-amber-500/15 text-amber-300",
  bad: "bg-red-500/15 text-red-300",
  info: "bg-sky-500/15 text-sky-300",
  accent: "bg-[color-mix(in_srgb,var(--color-accent)_20%,transparent)] text-white"
};
function Badge({ children, tone = "neutral" }) {
  return /* @__PURE__ */ jsx("span", { className: `inline-flex items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-medium ${TONES[tone]}`, children });
}
function Stat({ label, value, hint, tone }) {
  const valueTone = tone === "warn" ? "text-amber-300" : tone === "bad" ? "text-red-300" : tone === "good" ? "text-emerald-300" : "text-white";
  return /* @__PURE__ */ jsxs("div", { className: "panel px-4 py-3", children: [
    /* @__PURE__ */ jsx("p", { className: "text-xs text-white/50", children: label }),
    /* @__PURE__ */ jsx("p", { className: `mt-1 text-xl font-semibold tabular-nums ${valueTone}`, children: value }),
    hint && /* @__PURE__ */ jsx("p", { className: "mt-0.5 text-[11px] text-white/40", children: hint })
  ] });
}
function EmptyState({ icon, title, text, action }) {
  return /* @__PURE__ */ jsxs("div", { className: "panel flex flex-col items-center px-6 py-14 text-center", children: [
    /* @__PURE__ */ jsx("span", { className: "mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-white/[0.06] text-[var(--color-accent)]", children: /* @__PURE__ */ jsx(Icon, { name: icon, size: 22 }) }),
    /* @__PURE__ */ jsx("p", { className: "text-sm font-medium", children: title }),
    text && /* @__PURE__ */ jsx("p", { className: "mt-1 max-w-md text-sm text-white/50", children: text }),
    action && /* @__PURE__ */ jsx("div", { className: "mt-5", children: action })
  ] });
}
function Notice({ text, kind = "error", onClose }) {
  const cls = kind === "ok" ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200" : "border-red-500/30 bg-red-500/10 text-red-200";
  return /* @__PURE__ */ jsxs("div", { role: kind === "ok" ? "status" : "alert", className: `mb-4 flex items-start justify-between gap-3 rounded-lg border px-3 py-2 text-sm ${cls}`, children: [
    /* @__PURE__ */ jsx("span", { children: text }),
    onClose && /* @__PURE__ */ jsx("button", { type: "button", "aria-label": "Hinweis schlie\xDFen", onClick: onClose, className: "opacity-60 hover:opacity-100", children: /* @__PURE__ */ jsx(Icon, { name: "x", size: 14 }) })
  ] });
}
function SearchInput({ value, onChange, placeholder, label }) {
  return /* @__PURE__ */ jsxs("span", { className: "relative block min-w-[14rem] flex-1", children: [
    /* @__PURE__ */ jsx(Icon, { name: "search", size: 15, className: "pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-white/35" }),
    /* @__PURE__ */ jsx("input", { "aria-label": label, value, placeholder, onChange: (e) => onChange(e.target.value), className: `${inputClass} pl-9` })
  ] });
}
function Loading() {
  return /* @__PURE__ */ jsx("div", { className: "p-6 text-sm text-white/50", children: "Lade \u2026" });
}

// src/DocumentsPage.tsx
import { Fragment as Fragment2, jsx as jsx2, jsxs as jsxs2 } from "react/jsx-runtime";
function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
function typeLabel(contentType) {
  if (contentType === "application/pdf") return "PDF";
  if (contentType.startsWith("image/")) return contentType.slice(6).toUpperCase();
  return contentType;
}
function TagPanel({ tags, onChanged }) {
  const [name, setName] = useState("");
  const [matchKeyword, setMatchKeyword] = useState("");
  const [message, setMessage] = useState(null);
  async function addTag(e) {
    e.preventDefault();
    if (!name.trim()) return;
    const res = await authedFetch("/ext/documents/tags", {
      method: "POST",
      body: JSON.stringify({ name: name.trim(), match_keyword: matchKeyword.trim() || null })
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      setMessage(`Fehler: ${errorFromBody(body, res.status)}`);
      return;
    }
    setName("");
    setMatchKeyword("");
    onChanged();
  }
  async function removeTag(id) {
    const ok = await deck().confirmDialog("Tag wirklich entfernen?", { danger: true });
    if (!ok) return;
    await authedFetch(`/ext/documents/tags/${id}`, { method: "DELETE" });
    onChanged();
  }
  return /* @__PURE__ */ jsxs2(
    Card,
    {
      title: "Tags",
      description: "Mit Auto-Stichwort wird ein Tag automatisch vergeben, sobald das Wort im erkannten Text vorkommt (z. B. \u201ERechnung\u201C).",
      className: "mb-5",
      children: [
        message && /* @__PURE__ */ jsx2(Notice, { text: message, onClose: () => setMessage(null) }),
        /* @__PURE__ */ jsxs2("div", { className: "mb-4 flex flex-wrap gap-1.5", children: [
          tags.map((t) => /* @__PURE__ */ jsxs2("span", { className: "inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] py-1 pl-2.5 pr-1.5 text-sm", children: [
            /* @__PURE__ */ jsx2(Icon, { name: "tag", size: 12, className: "text-[var(--color-accent)]" }),
            t.name,
            t.match_keyword && /* @__PURE__ */ jsxs2("span", { className: "text-xs text-white/40", children: [
              "\u201E",
              t.match_keyword,
              "\u201C"
            ] }),
            /* @__PURE__ */ jsx2("button", { type: "button", "aria-label": `Tag ${t.name} l\xF6schen`, onClick: () => void removeTag(t.id), className: "rounded p-0.5 text-white/40 hover:bg-white/10 hover:text-white", children: /* @__PURE__ */ jsx2(Icon, { name: "x", size: 12 }) })
          ] }, t.id)),
          tags.length === 0 && /* @__PURE__ */ jsx2("span", { className: "text-sm text-white/40", children: "Noch keine Tags." })
        ] }),
        /* @__PURE__ */ jsxs2("form", { onSubmit: (e) => void addTag(e), className: "flex flex-wrap gap-2", children: [
          /* @__PURE__ */ jsx2("input", { value: name, onChange: (e) => setName(e.target.value), placeholder: "Neuer Tag", "aria-label": "Neuer Tag", className: `${inputClass} flex-1` }),
          /* @__PURE__ */ jsx2(
            "input",
            {
              value: matchKeyword,
              onChange: (e) => setMatchKeyword(e.target.value),
              placeholder: "Auto-Stichwort (optional)",
              "aria-label": "Auto-Stichwort",
              className: `${inputClass} flex-1`
            }
          ),
          /* @__PURE__ */ jsxs2(Button, { type: "submit", children: [
            /* @__PURE__ */ jsx2(Icon, { name: "plus", size: 14 }),
            " Tag anlegen"
          ] })
        ] })
      ]
    }
  );
}
function DocumentsPage() {
  const [documents, setDocuments] = useState(null);
  const [tags, setTags] = useState([]);
  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);
  const [search, setSearch] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const [showTags, setShowTags] = useState(false);
  const [expanded, setExpanded] = useState(null);
  const [preview, setPreview] = useState(null);
  const [copied, setCopied] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [attachTagId, setAttachTagId] = useState({});
  const load = useCallback(() => {
    setError(null);
    const params = new URLSearchParams();
    if (search) params.set("q", search);
    if (tagFilter) params.set("tag_id", tagFilter);
    const qs = params.toString() ? `?${params.toString()}` : "";
    Promise.all([
      authedFetch(`/ext/documents/documents${qs}`).then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      }),
      authedFetch("/ext/documents/tags").then((r) => r.json())
    ]).then(([docsData, tagsData]) => {
      setDocuments(docsData);
      setTags(tagsData);
    }).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [search, tagFilter]);
  useEffect(() => {
    load();
  }, [load]);
  async function uploadFile(file) {
    setUploading(true);
    setMessage(null);
    try {
      const res = await authedFetch(`/ext/documents/documents?filename=${encodeURIComponent(file.name)}`, {
        method: "POST",
        body: file,
        headers: { "Content-Type": file.type || "application/octet-stream" }
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
  async function removeDocument(doc) {
    const ok = await deck().confirmDialog(`"${doc.original_filename}" wirklich l\xF6schen?`, { danger: true, confirmLabel: "L\xF6schen" });
    if (!ok) return;
    await authedFetch(`/ext/documents/documents/${doc.id}`, { method: "DELETE" });
    load();
  }
  async function attachTag(doc) {
    const tagId = attachTagId[doc.id];
    if (!tagId) return;
    await authedFetch(`/ext/documents/documents/${doc.id}/tags/${tagId}`, { method: "POST" });
    setAttachTagId({ ...attachTagId, [doc.id]: "" });
    load();
  }
  async function detachTag(doc, tagId) {
    await authedFetch(`/ext/documents/documents/${doc.id}/tags/${tagId}`, { method: "DELETE" });
    load();
  }
  async function fetchBlob(doc) {
    const res = await authedFetch(`/ext/documents/documents/${doc.id}/download`);
    if (!res.ok) {
      setMessage(`Fehler: HTTP ${res.status}`);
      return null;
    }
    return res.blob();
  }
  async function togglePreview(doc) {
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
  async function renameDocument(doc) {
    const name = await deck().promptDialog(`Neuer Name f\xFCr \u201E${doc.original_filename}\u201C:`);
    if (!name || name.trim() === doc.original_filename) return;
    const res = await authedFetch(`/ext/documents/documents/${doc.id}`, {
      method: "PATCH",
      body: JSON.stringify({ original_filename: name.trim() })
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      setMessage(`Fehler: ${errorFromBody(body, res.status)}`);
      return;
    }
    load();
  }
  async function copyText(doc) {
    try {
      await navigator.clipboard.writeText(doc.ocr_text ?? "");
      setCopied(doc.id);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      setMessage("Kopieren nicht m\xF6glich (Browser erlaubt die Zwischenablage nur \xFCber HTTPS).");
    }
  }
  async function downloadDocument(doc) {
    const res = await authedFetch(`/ext/documents/documents/${doc.id}/download`);
    if (!res.ok) {
      setMessage(`Fehler: HTTP ${res.status}`);
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = window.document.createElement("a");
    a.href = url;
    a.download = doc.original_filename;
    a.click();
    URL.revokeObjectURL(url);
  }
  if (!documents && !error) return /* @__PURE__ */ jsx2(Loading, {});
  if (error) return /* @__PURE__ */ jsx2(Page, { title: "Dokumente", children: /* @__PURE__ */ jsx2(Notice, { text: `Fehler: ${error}` }) });
  const uploadButton = (label) => /* @__PURE__ */ jsxs2("label", { className: `${buttonClass("primary")} cursor-pointer`, children: [
    /* @__PURE__ */ jsx2(Icon, { name: "upload", size: 14 }),
    " ",
    uploading ? "L\xE4dt hoch \u2026" : label,
    /* @__PURE__ */ jsx2(
      "input",
      {
        type: "file",
        accept: "application/pdf,image/png,image/jpeg,image/webp",
        className: "hidden",
        onChange: (e) => {
          const file = e.target.files?.[0];
          if (file) void uploadFile(file);
          e.target.value = "";
        }
      }
    )
  ] });
  const docs = documents ?? [];
  const totalBytes = docs.reduce((sum, d) => sum + d.size_bytes, 0);
  const ocrFailed = docs.filter((d) => d.ocr_status === "error").length;
  const untagged = docs.filter((d) => d.tags.length === 0).length;
  const filtering = Boolean(search || tagFilter);
  return /* @__PURE__ */ jsxs2(
    Page,
    {
      title: "Dokumente",
      description: "Rechnungen, Vertr\xE4ge und Handb\xFCcher als PDF oder Scan \u2013 mit Texterkennung und Volltextsuche.",
      actions: /* @__PURE__ */ jsxs2(Fragment2, { children: [
        /* @__PURE__ */ jsxs2(Button, { onClick: () => setShowTags((v) => !v), pressed: showTags, children: [
          /* @__PURE__ */ jsx2(Icon, { name: "tag", size: 14 }),
          " Tags verwalten"
        ] }),
        uploadButton("Dokument hochladen")
      ] }),
      children: [
        showTags && /* @__PURE__ */ jsx2(TagPanel, { tags, onChanged: load }),
        message && /* @__PURE__ */ jsx2(Notice, { text: message, onClose: () => setMessage(null) }),
        docs.length === 0 && !filtering ? /* @__PURE__ */ jsx2(
          EmptyState,
          {
            icon: "file",
            title: "Noch keine Dokumente erfasst.",
            text: "Lade PDFs oder Fotos von Dokumenten hoch. Der Text wird automatisch erkannt, damit du sp\xE4ter per Suche alles wiederfindest.",
            action: uploadButton("Erstes Dokument hochladen")
          }
        ) : /* @__PURE__ */ jsxs2(Fragment2, { children: [
          /* @__PURE__ */ jsxs2("div", { className: "mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4", children: [
            /* @__PURE__ */ jsx2(Stat, { label: "Dokumente", value: docs.length }),
            /* @__PURE__ */ jsx2(Stat, { label: "Speicher", value: formatSize(totalBytes) }),
            /* @__PURE__ */ jsx2(Stat, { label: "Ohne Tag", value: untagged }),
            /* @__PURE__ */ jsx2(Stat, { label: "Texterkennung fehlgeschlagen", value: ocrFailed, tone: ocrFailed ? "warn" : void 0 })
          ] }),
          /* @__PURE__ */ jsxs2("div", { className: "mb-3 flex flex-wrap gap-2", children: [
            /* @__PURE__ */ jsx2(SearchInput, { value: search, onChange: setSearch, placeholder: "Volltextsuche \u2026", label: "Volltextsuche" }),
            /* @__PURE__ */ jsxs2("select", { value: tagFilter, onChange: (e) => setTagFilter(e.target.value), "aria-label": "Nach Tag filtern", className: `${inputClass} w-auto`, children: [
              /* @__PURE__ */ jsx2("option", { value: "", children: "Alle Tags" }),
              tags.map((t) => /* @__PURE__ */ jsx2("option", { value: t.id, children: t.name }, t.id))
            ] })
          ] }),
          /* @__PURE__ */ jsxs2("ul", { className: "panel divide-y divide-white/[0.06]", children: [
            docs.length === 0 && /* @__PURE__ */ jsx2("li", { className: "px-5 py-8 text-center text-sm text-white/45", children: "Keine Treffer." }),
            docs.map((doc) => {
              const isExpanded = expanded === doc.id;
              const attachableTags = tags.filter((t) => !doc.tags.some((dt) => dt.id === t.id));
              return /* @__PURE__ */ jsxs2("li", { "data-testid": `document-${doc.id}`, className: "px-4 py-3", children: [
                /* @__PURE__ */ jsxs2("div", { className: "flex flex-wrap items-center gap-3", children: [
                  /* @__PURE__ */ jsx2(
                    "button",
                    {
                      type: "button",
                      onClick: () => setExpanded(isExpanded ? null : doc.id),
                      "aria-label": isExpanded ? "Details ausblenden" : "Details anzeigen",
                      "aria-expanded": isExpanded,
                      className: "rounded p-1 text-white/40 hover:bg-white/[0.06] hover:text-white",
                      children: /* @__PURE__ */ jsx2(Icon, { name: isExpanded ? "chevron-down" : "chevron-right", size: 15 })
                    }
                  ),
                  /* @__PURE__ */ jsx2("span", { className: "grid h-11 w-11 flex-none place-items-center rounded-lg bg-white/[0.05] text-[var(--color-accent)]", children: /* @__PURE__ */ jsx2(Icon, { name: "file", size: 18 }) }),
                  /* @__PURE__ */ jsxs2("div", { className: "min-w-0 flex-1", children: [
                    /* @__PURE__ */ jsx2("p", { className: "truncate text-sm font-medium", children: doc.original_filename }),
                    /* @__PURE__ */ jsxs2("p", { className: "text-xs text-white/50", children: [
                      typeLabel(doc.content_type),
                      doc.page_count ? ` \xB7 ${doc.page_count} Seite(n)` : "",
                      " \xB7 ",
                      formatSize(doc.size_bytes)
                    ] }),
                    /* @__PURE__ */ jsxs2("div", { className: "mt-1 flex flex-wrap gap-1", children: [
                      doc.ocr_status === "error" && /* @__PURE__ */ jsx2(Badge, { tone: "warn", children: "Texterkennung fehlgeschlagen" }),
                      doc.tags.map((t) => /* @__PURE__ */ jsxs2(Badge, { tone: "accent", children: [
                        t.name,
                        /* @__PURE__ */ jsx2("button", { type: "button", onClick: () => void detachTag(doc, t.id), "aria-label": `Tag ${t.name} entfernen`, className: "opacity-60 hover:opacity-100", children: /* @__PURE__ */ jsx2(Icon, { name: "x", size: 10 }) })
                      ] }, t.id))
                    ] })
                  ] }),
                  /* @__PURE__ */ jsxs2("div", { className: "flex w-full flex-wrap items-center gap-1 sm:w-auto sm:flex-none", children: [
                    /* @__PURE__ */ jsxs2(Button, { small: true, onClick: () => void togglePreview(doc), pressed: preview?.id === doc.id, children: [
                      /* @__PURE__ */ jsx2(Icon, { name: "eye", size: 13 }),
                      " ",
                      preview?.id === doc.id ? "Vorschau schlie\xDFen" : "Ansehen"
                    ] }),
                    /* @__PURE__ */ jsxs2(Button, { small: true, onClick: () => void renameDocument(doc), children: [
                      /* @__PURE__ */ jsx2(Icon, { name: "edit", size: 13 }),
                      " Umbenennen"
                    ] }),
                    /* @__PURE__ */ jsxs2(Button, { small: true, onClick: () => void downloadDocument(doc), children: [
                      /* @__PURE__ */ jsx2(Icon, { name: "download", size: 13 }),
                      " Herunterladen"
                    ] }),
                    /* @__PURE__ */ jsxs2(Button, { small: true, variant: "danger", onClick: () => void removeDocument(doc), children: [
                      /* @__PURE__ */ jsx2(Icon, { name: "trash", size: 13 }),
                      " L\xF6schen"
                    ] })
                  ] })
                ] }),
                isExpanded && /* @__PURE__ */ jsxs2("div", { className: "ml-10 mt-3 space-y-3 rounded-lg border border-white/[0.06] bg-black/15 p-4 text-sm", children: [
                  preview?.id === doc.id && /* @__PURE__ */ jsx2("div", { "data-testid": `preview-${doc.id}`, children: preview.type.startsWith("image/") ? /* @__PURE__ */ jsx2("img", { src: preview.url, alt: doc.original_filename, className: "max-h-[70vh] rounded-lg border border-white/10" }) : /* @__PURE__ */ jsx2("iframe", { src: preview.url, title: doc.original_filename, className: "h-[70vh] w-full rounded-lg border border-white/10 bg-white" }) }),
                  doc.ocr_error && /* @__PURE__ */ jsxs2("p", { className: "text-amber-300", children: [
                    "Texterkennung: ",
                    doc.ocr_error
                  ] }),
                  /* @__PURE__ */ jsxs2("div", { className: "flex max-w-md items-center gap-2", children: [
                    /* @__PURE__ */ jsxs2(
                      "select",
                      {
                        value: attachTagId[doc.id] ?? "",
                        "aria-label": `Tag zu ${doc.original_filename} hinzuf\xFCgen`,
                        onChange: (e) => setAttachTagId({ ...attachTagId, [doc.id]: e.target.value }),
                        className: inputClass,
                        children: [
                          /* @__PURE__ */ jsx2("option", { value: "", children: "Tag hinzuf\xFCgen \u2026" }),
                          attachableTags.map((t) => /* @__PURE__ */ jsx2("option", { value: t.id, children: t.name }, t.id))
                        ]
                      }
                    ),
                    /* @__PURE__ */ jsx2(Button, { onClick: () => void attachTag(doc), disabled: !attachTagId[doc.id], children: "Anh\xE4ngen" })
                  ] }),
                  /* @__PURE__ */ jsxs2("div", { children: [
                    /* @__PURE__ */ jsxs2("div", { className: "mb-1.5 flex items-center justify-between", children: [
                      /* @__PURE__ */ jsx2("p", { className: "text-xs text-white/45", children: "Erkannter Text" }),
                      doc.ocr_text && /* @__PURE__ */ jsxs2(Button, { small: true, variant: "ghost", onClick: () => void copyText(doc), children: [
                        /* @__PURE__ */ jsx2(Icon, { name: "copy", size: 12 }),
                        " ",
                        copied === doc.id ? "Kopiert \u2713" : "Text kopieren"
                      ] })
                    ] }),
                    /* @__PURE__ */ jsx2("pre", { className: "max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-black/30 p-3 font-mono text-xs text-white/75", children: doc.ocr_text || "(kein Text erkannt)" })
                  ] })
                ] })
              ] }, doc.id);
            })
          ] })
        ] })
      ]
    }
  );
}
export {
  DocumentsPage
};
