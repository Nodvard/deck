// src/InventoryPage.tsx
import { useCallback, useEffect as useEffect2, useState as useState2 } from "react";

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

// ../../_shared/frontend/src/AuthImage.tsx
import { useEffect, useState } from "react";

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
function Field({ label, children, className = "" }) {
  return /* @__PURE__ */ jsxs("label", { className: `block text-sm ${className}`, children: [
    /* @__PURE__ */ jsx("span", { className: "mb-1.5 block text-white/70", children: label }),
    children
  ] });
}
function Loading() {
  return /* @__PURE__ */ jsx("div", { className: "p-6 text-sm text-white/50", children: "Lade \u2026" });
}

// ../../_shared/frontend/src/AuthImage.tsx
import { jsx as jsx2 } from "react/jsx-runtime";
function AuthImage({ src, alt, className = "" }) {
  const [objectUrl, setObjectUrl] = useState(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    let created = null;
    setObjectUrl(null);
    setFailed(false);
    authedFetch(src.replace(/^\/api\/v1(?=\/)/, "")).then(async (res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      if (cancelled) return;
      created = URL.createObjectURL(blob);
      setObjectUrl(created);
    }).catch(() => {
      if (!cancelled) setFailed(true);
    });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [src]);
  if (objectUrl) return /* @__PURE__ */ jsx2("img", { src: objectUrl, alt, className });
  if (failed) {
    return /* @__PURE__ */ jsx2(
      "span",
      {
        role: "img",
        "aria-label": alt || "Foto konnte nicht geladen werden",
        title: "Foto konnte nicht geladen werden",
        className: `grid place-items-center bg-white/[0.05] text-white/35 ${className}`,
        children: /* @__PURE__ */ jsx2(Icon, { name: "x", size: 14 })
      }
    );
  }
  return /* @__PURE__ */ jsx2("span", { "aria-hidden": "true", className: `block animate-pulse bg-white/[0.05] ${className}` });
}

// src/InventoryPage.tsx
import { Fragment as Fragment2, jsx as jsx3, jsxs as jsxs2 } from "react/jsx-runtime";
var DAY_MS = 864e5;
function warrantyState(until, today = /* @__PURE__ */ new Date()) {
  if (!until) return "none";
  const days = ((/* @__PURE__ */ new Date(`${until}T00:00:00`)).getTime() - new Date(today.toDateString()).getTime()) / DAY_MS;
  if (days < 0) return "expired";
  return days <= 90 ? "soon" : "active";
}
function csvCell(value) {
  const text = value == null ? "" : String(value);
  return /[";\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}
function inventoryCsv(items, categoryName, locationName) {
  const header = ["Name", "Kategorie", "Standort", "Menge", "Kaufdatum", "Preis (EUR)", "Garantie bis", "Beschreibung", "Notizen"];
  const rows = items.map((i) => [
    i.name,
    categoryName(i.category_id),
    locationName(i.location_id),
    i.quantity,
    i.purchase_date,
    i.purchase_price_cents == null ? "" : (i.purchase_price_cents / 100).toFixed(2).replace(".", ","),
    i.warranty_until,
    i.description,
    i.notes
  ].map(csvCell).join(";"));
  return "\uFEFF" + [header.join(";"), ...rows].join("\r\n");
}
var EMPTY_FORM = {
  name: "",
  description: "",
  category_id: "",
  location_id: "",
  quantity: "1",
  purchase_date: "",
  purchase_price: "",
  warranty_until: "",
  notes: ""
};
function formatPrice(cents) {
  if (cents === null) return "\u2013";
  return (cents / 100).toLocaleString("de-DE", { style: "currency", currency: "EUR" });
}
var WARRANTY_BADGE = {
  active: { tone: "good", label: (d) => `Garantie bis ${formatDate(d)}` },
  soon: { tone: "warn", label: (d) => `Garantie endet ${formatDate(d)}` },
  expired: { tone: "bad", label: (d) => `Garantie abgelaufen ${formatDate(d)}` },
  none: { tone: "neutral", label: () => "" }
};
function formatDate(iso) {
  if (!iso) return "\u2013";
  const [y, m, d] = iso.split("-");
  return `${d}.${m}.${y}`;
}
function TaxonomyPanel({
  categories,
  locations,
  onChanged
}) {
  const [newCategory, setNewCategory] = useState2("");
  const [newLocation, setNewLocation] = useState2("");
  const [message, setMessage] = useState2(null);
  async function add(kind, name, reset) {
    if (!name.trim()) return;
    const res = await authedFetch(`/ext/inventory/${kind}`, { method: "POST", body: JSON.stringify({ name: name.trim() }) });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      setMessage(`Fehler: ${errorFromBody(body, res.status)}`);
      return;
    }
    reset();
    onChanged();
  }
  async function remove(kind, id, what) {
    const ok = await deck().confirmDialog(`${what} wirklich entfernen?`, { danger: true });
    if (!ok) return;
    await authedFetch(`/ext/inventory/${kind}/${id}`, { method: "DELETE" });
    onChanged();
  }
  const column = (title, icon, entries, kind, value, setValue, placeholder, what) => /* @__PURE__ */ jsxs2("div", { children: [
    /* @__PURE__ */ jsxs2("p", { className: "mb-2 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-white/45", children: [
      /* @__PURE__ */ jsx3(Icon, { name: icon, size: 13 }),
      " ",
      title
    ] }),
    /* @__PURE__ */ jsxs2("div", { className: "mb-3 flex flex-wrap gap-1.5", children: [
      entries.map((e) => /* @__PURE__ */ jsxs2("span", { className: "inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] py-1 pl-2.5 pr-1.5 text-sm", children: [
        e.name,
        /* @__PURE__ */ jsx3("button", { type: "button", "aria-label": `${e.name} entfernen`, onClick: () => void remove(kind, e.id, what), className: "rounded p-0.5 text-white/40 hover:bg-white/10 hover:text-white", children: /* @__PURE__ */ jsx3(Icon, { name: "x", size: 12 }) })
      ] }, e.id)),
      entries.length === 0 && /* @__PURE__ */ jsx3("span", { className: "text-sm text-white/40", children: "Noch keine." })
    ] }),
    /* @__PURE__ */ jsxs2("form", { onSubmit: (e) => {
      e.preventDefault();
      void add(kind, value, () => setValue(""));
    }, className: "flex gap-2", children: [
      /* @__PURE__ */ jsx3("input", { value, onChange: (e) => setValue(e.target.value), placeholder, className: inputClass }),
      /* @__PURE__ */ jsx3(Button, { type: "submit", ariaLabel: `${what} hinzuf\xFCgen`, children: /* @__PURE__ */ jsx3(Icon, { name: "plus", size: 14 }) })
    ] })
  ] });
  return /* @__PURE__ */ jsxs2(Card, { title: "Kategorien & Standorte", description: "Zum Ordnen und Filtern. Entfernen l\xE4sst die Gegenst\xE4nde selbst unver\xE4ndert.", className: "mb-5", children: [
    message && /* @__PURE__ */ jsx3(Notice, { text: message, onClose: () => setMessage(null) }),
    /* @__PURE__ */ jsxs2("div", { className: "grid gap-6 sm:grid-cols-2", children: [
      column("Kategorien", "tag", categories, "categories", newCategory, setNewCategory, "Neue Kategorie", "Kategorie"),
      column("Standorte", "map-pin", locations, "locations", newLocation, setNewLocation, "Neuer Standort", "Standort")
    ] })
  ] });
}
function InventoryPage() {
  const [items, setItems] = useState2(null);
  const [categories, setCategories] = useState2([]);
  const [locations, setLocations] = useState2([]);
  const [error, setError] = useState2(null);
  const [message, setMessage] = useState2(null);
  const [search, setSearch] = useState2("");
  const [categoryFilter, setCategoryFilter] = useState2("");
  const [locationFilter, setLocationFilter] = useState2("");
  const [warrantyFilter, setWarrantyFilter] = useState2("");
  const [showForm, setShowForm] = useState2(false);
  const [showTaxonomy, setShowTaxonomy] = useState2(false);
  const [editingId, setEditingId] = useState2(null);
  const [form, setForm] = useState2(EMPTY_FORM);
  const [expanded, setExpanded] = useState2(null);
  const [uploading, setUploading] = useState2(null);
  const load = useCallback(() => {
    setError(null);
    const params = search ? `?q=${encodeURIComponent(search)}` : "";
    Promise.all([
      authedFetch(`/ext/inventory/items${params}`).then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      }),
      authedFetch("/ext/inventory/categories").then((r) => r.json()),
      authedFetch("/ext/inventory/locations").then((r) => r.json())
    ]).then(([itemsData, categoriesData, locationsData]) => {
      setItems(itemsData);
      setCategories(categoriesData);
      setLocations(locationsData);
    }).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [search]);
  useEffect2(() => {
    load();
  }, [load]);
  function startCreate() {
    setForm(EMPTY_FORM);
    setEditingId(null);
    setShowForm(true);
  }
  function startEdit(item) {
    setForm({
      name: item.name,
      description: item.description ?? "",
      category_id: item.category_id ?? "",
      location_id: item.location_id ?? "",
      quantity: String(item.quantity),
      purchase_date: item.purchase_date ?? "",
      purchase_price: item.purchase_price_cents != null ? (item.purchase_price_cents / 100).toFixed(2) : "",
      warranty_until: item.warranty_until ?? "",
      notes: item.notes ?? ""
    });
    setEditingId(item.id);
    setShowForm(true);
    window.scrollTo?.({ top: 0, behavior: "smooth" });
  }
  async function submitForm(e) {
    e.preventDefault();
    setMessage(null);
    const payload = {
      name: form.name,
      description: form.description || null,
      category_id: form.category_id || null,
      location_id: form.location_id || null,
      quantity: parseInt(form.quantity, 10) || 1,
      purchase_date: form.purchase_date || null,
      purchase_price_cents: form.purchase_price ? Math.round(parseFloat(form.purchase_price) * 100) : null,
      warranty_until: form.warranty_until || null,
      notes: form.notes || null
    };
    const path = editingId ? `/ext/inventory/items/${editingId}` : "/ext/inventory/items";
    const method = editingId ? "PUT" : "POST";
    const res = await authedFetch(path, { method, body: JSON.stringify(payload) });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      setMessage(`Fehler: ${errorFromBody(body, res.status)}`);
      return;
    }
    setShowForm(false);
    load();
  }
  async function removeItem(item) {
    const ok = await deck().confirmDialog(`"${item.name}" wirklich l\xF6schen?`, { danger: true, confirmLabel: "L\xF6schen" });
    if (!ok) return;
    await authedFetch(`/ext/inventory/items/${item.id}`, { method: "DELETE" });
    load();
  }
  async function uploadImage(item, file) {
    setUploading(item.id);
    setMessage(null);
    try {
      const res = await authedFetch(`/ext/inventory/items/${item.id}/images`, {
        method: "POST",
        body: file,
        headers: { "Content-Type": file.type }
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(errorFromBody(body, res.status));
      }
      load();
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setUploading(null);
    }
  }
  async function removeImage(item, image) {
    await authedFetch(`/ext/inventory/items/${item.id}/images/${image.id}`, { method: "DELETE" });
    load();
  }
  const categoryName = (id) => categories.find((c) => c.id === id)?.name ?? "\u2013";
  const locationName = (id) => locations.find((l) => l.id === id)?.name ?? "\u2013";
  const visible = (items ?? []).filter((i) => (!categoryFilter || i.category_id === categoryFilter) && (!locationFilter || i.location_id === locationFilter) && (!warrantyFilter || warrantyState(i.warranty_until) === warrantyFilter));
  const totalCents = visible.reduce((sum, i) => sum + (i.purchase_price_cents ?? 0) * i.quantity, 0);
  const soonCount = visible.filter((i) => warrantyState(i.warranty_until) === "soon").length;
  const expiredCount = visible.filter((i) => warrantyState(i.warranty_until) === "expired").length;
  function exportCsv() {
    const blob = new Blob([inventoryCsv(visible, categoryName, locationName)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = window.document.createElement("a");
    a.href = url;
    a.download = `inventar-${(/* @__PURE__ */ new Date()).toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }
  if (!items && !error) return /* @__PURE__ */ jsx3(Loading, {});
  if (error) return /* @__PURE__ */ jsx3(Page, { title: "Inventar", children: /* @__PURE__ */ jsx3(Notice, { text: `Fehler: ${error}` }) });
  const input = (key, props = {}) => /* @__PURE__ */ jsx3("input", { value: form[key], onChange: (e) => setForm({ ...form, [key]: e.target.value }), className: inputClass, ...props });
  return /* @__PURE__ */ jsxs2(
    Page,
    {
      title: "Inventar",
      description: "Ger\xE4te und Gegenst\xE4nde mit Standort, Kaufpreis und Garantie.",
      actions: /* @__PURE__ */ jsxs2(Fragment2, { children: [
        /* @__PURE__ */ jsxs2(Button, { onClick: () => setShowTaxonomy((v) => !v), pressed: showTaxonomy, children: [
          /* @__PURE__ */ jsx3(Icon, { name: "tag", size: 14 }),
          " Kategorien & Standorte"
        ] }),
        /* @__PURE__ */ jsxs2(Button, { onClick: exportCsv, disabled: visible.length === 0, children: [
          /* @__PURE__ */ jsx3(Icon, { name: "download", size: 14 }),
          " Als CSV exportieren"
        ] }),
        /* @__PURE__ */ jsxs2(Button, { variant: "primary", onClick: startCreate, children: [
          /* @__PURE__ */ jsx3(Icon, { name: "plus", size: 14 }),
          " Neuer Gegenstand"
        ] })
      ] }),
      children: [
        showTaxonomy && /* @__PURE__ */ jsx3(TaxonomyPanel, { categories, locations, onChanged: load }),
        message && /* @__PURE__ */ jsx3(Notice, { text: message, onClose: () => setMessage(null) }),
        items && items.length > 0 && /* @__PURE__ */ jsxs2("div", { className: "mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4", "data-testid": "inventory-summary", children: [
          /* @__PURE__ */ jsx3(Stat, { label: "Gegenst\xE4nde", value: visible.length, hint: `${visible.length} Gegenstand/Gegenst\xE4nde angezeigt` }),
          /* @__PURE__ */ jsx3(Stat, { label: "Gesamtwert", value: (totalCents / 100).toLocaleString("de-DE", { style: "currency", currency: "EUR" }) }),
          /* @__PURE__ */ jsx3(Stat, { label: "Garantie endet bald", value: soonCount, hint: "in den n\xE4chsten 90 Tagen", tone: soonCount ? "warn" : void 0 }),
          /* @__PURE__ */ jsx3(Stat, { label: "Garantie abgelaufen", value: expiredCount, hint: `${expiredCount} abgelaufen`, tone: expiredCount ? "bad" : void 0 })
        ] }),
        showForm && /* @__PURE__ */ jsx3("form", { onSubmit: (e) => void submitForm(e), className: "mb-5", children: /* @__PURE__ */ jsxs2(
          Card,
          {
            title: editingId ? "Gegenstand bearbeiten" : "Neuer Gegenstand",
            actions: /* @__PURE__ */ jsx3(Button, { variant: "ghost", ariaLabel: "Formular schlie\xDFen", onClick: () => setShowForm(false), children: /* @__PURE__ */ jsx3(Icon, { name: "x", size: 14 }) }),
            children: [
              /* @__PURE__ */ jsxs2("div", { className: "grid gap-4 sm:grid-cols-2 lg:grid-cols-4", children: [
                /* @__PURE__ */ jsx3(Field, { label: "Name", className: "sm:col-span-2", children: input("name", { required: true }) }),
                /* @__PURE__ */ jsx3(Field, { label: "Menge", children: input("quantity", { type: "number", min: "0" }) }),
                /* @__PURE__ */ jsx3(Field, { label: "Preis (\u20AC)", children: input("purchase_price", { type: "number", step: "0.01", min: "0" }) }),
                /* @__PURE__ */ jsx3(Field, { label: "Kategorie", children: /* @__PURE__ */ jsxs2("select", { value: form.category_id, onChange: (e) => setForm({ ...form, category_id: e.target.value }), className: inputClass, children: [
                  /* @__PURE__ */ jsx3("option", { value: "", children: "\u2013" }),
                  categories.map((c) => /* @__PURE__ */ jsx3("option", { value: c.id, children: c.name }, c.id))
                ] }) }),
                /* @__PURE__ */ jsx3(Field, { label: "Standort", children: /* @__PURE__ */ jsxs2("select", { value: form.location_id, onChange: (e) => setForm({ ...form, location_id: e.target.value }), className: inputClass, children: [
                  /* @__PURE__ */ jsx3("option", { value: "", children: "\u2013" }),
                  locations.map((l) => /* @__PURE__ */ jsx3("option", { value: l.id, children: l.name }, l.id))
                ] }) }),
                /* @__PURE__ */ jsx3(Field, { label: "Kaufdatum", children: input("purchase_date", { type: "date" }) }),
                /* @__PURE__ */ jsx3(Field, { label: "Garantie bis", children: input("warranty_until", { type: "date" }) }),
                /* @__PURE__ */ jsx3(Field, { label: "Beschreibung", className: "sm:col-span-2", children: /* @__PURE__ */ jsx3("textarea", { value: form.description, onChange: (e) => setForm({ ...form, description: e.target.value }), className: inputClass, rows: 3 }) }),
                /* @__PURE__ */ jsx3(Field, { label: "Notizen", className: "sm:col-span-2", children: /* @__PURE__ */ jsx3("textarea", { value: form.notes, onChange: (e) => setForm({ ...form, notes: e.target.value }), className: inputClass, rows: 3 }) })
              ] }),
              /* @__PURE__ */ jsxs2("div", { className: "mt-4 flex justify-end gap-2", children: [
                /* @__PURE__ */ jsx3(Button, { variant: "ghost", onClick: () => setShowForm(false), children: "Abbrechen" }),
                /* @__PURE__ */ jsx3(Button, { type: "submit", variant: "primary", children: editingId ? "Speichern" : "Anlegen" })
              ] })
            ]
          }
        ) }),
        items && items.length === 0 ? /* @__PURE__ */ jsx3(
          EmptyState,
          {
            icon: "package",
            title: "Noch keine Gegenst\xE4nde erfasst.",
            text: "Erfasse Ger\xE4te, Werkzeug oder Ersatzteile mit Standort, Kaufpreis und Garantie \u2013 dann warnt das Dashboard rechtzeitig, bevor eine Garantie abl\xE4uft.",
            action: !showForm && /* @__PURE__ */ jsxs2(Button, { variant: "primary", onClick: startCreate, children: [
              /* @__PURE__ */ jsx3(Icon, { name: "plus", size: 14 }),
              " Ersten Gegenstand anlegen"
            ] })
          }
        ) : /* @__PURE__ */ jsxs2(Fragment2, { children: [
          /* @__PURE__ */ jsxs2("div", { className: "mb-3 flex flex-wrap gap-2", children: [
            /* @__PURE__ */ jsx3(SearchInput, { value: search, onChange: setSearch, placeholder: "Suchen \u2026", label: "Suchen" }),
            /* @__PURE__ */ jsxs2("select", { value: categoryFilter, onChange: (e) => setCategoryFilter(e.target.value), "aria-label": "Nach Kategorie filtern", className: `${inputClass} w-auto`, children: [
              /* @__PURE__ */ jsx3("option", { value: "", children: "Alle Kategorien" }),
              categories.map((c) => /* @__PURE__ */ jsx3("option", { value: c.id, children: c.name }, c.id))
            ] }),
            /* @__PURE__ */ jsxs2("select", { value: locationFilter, onChange: (e) => setLocationFilter(e.target.value), "aria-label": "Nach Standort filtern", className: `${inputClass} w-auto`, children: [
              /* @__PURE__ */ jsx3("option", { value: "", children: "Alle Standorte" }),
              locations.map((l) => /* @__PURE__ */ jsx3("option", { value: l.id, children: l.name }, l.id))
            ] }),
            /* @__PURE__ */ jsxs2("select", { value: warrantyFilter, onChange: (e) => setWarrantyFilter(e.target.value), "aria-label": "Nach Garantie filtern", className: `${inputClass} w-auto`, children: [
              /* @__PURE__ */ jsx3("option", { value: "", children: "Garantie: alle" }),
              /* @__PURE__ */ jsx3("option", { value: "active", children: "l\xE4uft noch" }),
              /* @__PURE__ */ jsx3("option", { value: "soon", children: "l\xE4uft in 90 Tagen ab" }),
              /* @__PURE__ */ jsx3("option", { value: "expired", children: "abgelaufen" }),
              /* @__PURE__ */ jsx3("option", { value: "none", children: "keine Angabe" })
            ] })
          ] }),
          /* @__PURE__ */ jsxs2("ul", { className: "panel divide-y divide-white/[0.06]", children: [
            visible.length === 0 && /* @__PURE__ */ jsx3("li", { className: "px-5 py-8 text-center text-sm text-white/45", children: "Nichts gefunden \u2013 Filter anpassen." }),
            visible.map((item) => {
              const isExpanded = expanded === item.id;
              const ws = warrantyState(item.warranty_until);
              return /* @__PURE__ */ jsxs2("li", { className: "px-4 py-3", children: [
                /* @__PURE__ */ jsxs2("div", { className: "flex items-center gap-3", children: [
                  /* @__PURE__ */ jsx3(
                    "button",
                    {
                      type: "button",
                      onClick: () => setExpanded(isExpanded ? null : item.id),
                      "aria-label": isExpanded ? "Details ausblenden" : "Details anzeigen",
                      "aria-expanded": isExpanded,
                      className: "rounded p-1 text-white/40 hover:bg-white/[0.06] hover:text-white",
                      children: /* @__PURE__ */ jsx3(Icon, { name: isExpanded ? "chevron-down" : "chevron-right", size: 15 })
                    }
                  ),
                  item.images[0] ? /* @__PURE__ */ jsx3(AuthImage, { src: item.images[0].url, alt: "", className: "h-11 w-11 flex-none rounded-lg object-cover" }) : /* @__PURE__ */ jsx3("span", { className: "grid h-11 w-11 flex-none place-items-center rounded-lg bg-white/[0.05] text-white/35", children: /* @__PURE__ */ jsx3(Icon, { name: "package", size: 18 }) }),
                  /* @__PURE__ */ jsxs2("div", { className: "min-w-0 flex-1", children: [
                    /* @__PURE__ */ jsx3("p", { className: "truncate text-sm font-medium", children: item.name }),
                    /* @__PURE__ */ jsxs2("p", { className: "truncate text-xs text-white/50", children: [
                      categoryName(item.category_id),
                      " \xB7 ",
                      locationName(item.location_id),
                      " \xB7 Menge ",
                      item.quantity
                    ] })
                  ] }),
                  /* @__PURE__ */ jsxs2("div", { className: "hidden flex-none text-right sm:block", children: [
                    /* @__PURE__ */ jsx3("p", { className: "text-sm tabular-nums", children: formatPrice(item.purchase_price_cents) }),
                    item.warranty_until && /* @__PURE__ */ jsx3(Badge, { tone: WARRANTY_BADGE[ws].tone, children: WARRANTY_BADGE[ws].label(item.warranty_until) })
                  ] }),
                  /* @__PURE__ */ jsxs2("div", { className: "flex flex-none items-center gap-1", children: [
                    /* @__PURE__ */ jsxs2(Button, { small: true, onClick: () => startEdit(item), children: [
                      /* @__PURE__ */ jsx3(Icon, { name: "edit", size: 13 }),
                      " Bearbeiten"
                    ] }),
                    /* @__PURE__ */ jsxs2(Button, { small: true, variant: "danger", onClick: () => void removeItem(item), children: [
                      /* @__PURE__ */ jsx3(Icon, { name: "trash", size: 13 }),
                      " L\xF6schen"
                    ] })
                  ] })
                ] }),
                isExpanded && /* @__PURE__ */ jsxs2("div", { className: "ml-10 mt-3 rounded-lg border border-white/[0.06] bg-black/15 p-4 text-sm", children: [
                  item.description && /* @__PURE__ */ jsx3("p", { className: "mb-3 text-white/80", children: item.description }),
                  /* @__PURE__ */ jsxs2("dl", { className: "mb-4 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4", children: [
                    /* @__PURE__ */ jsxs2("div", { children: [
                      /* @__PURE__ */ jsx3("dt", { className: "text-xs text-white/45", children: "Kaufdatum" }),
                      /* @__PURE__ */ jsx3("dd", { children: formatDate(item.purchase_date) })
                    ] }),
                    /* @__PURE__ */ jsxs2("div", { children: [
                      /* @__PURE__ */ jsx3("dt", { className: "text-xs text-white/45", children: "Preis" }),
                      /* @__PURE__ */ jsx3("dd", { children: formatPrice(item.purchase_price_cents) })
                    ] }),
                    /* @__PURE__ */ jsxs2("div", { children: [
                      /* @__PURE__ */ jsx3("dt", { className: "text-xs text-white/45", children: "Garantie bis" }),
                      /* @__PURE__ */ jsx3("dd", { children: formatDate(item.warranty_until) })
                    ] }),
                    /* @__PURE__ */ jsxs2("div", { children: [
                      /* @__PURE__ */ jsx3("dt", { className: "text-xs text-white/45", children: "Notizen" }),
                      /* @__PURE__ */ jsx3("dd", { children: item.notes ?? "\u2013" })
                    ] })
                  ] }),
                  /* @__PURE__ */ jsx3("p", { className: "mb-2 text-xs text-white/45", children: "Fotos" }),
                  /* @__PURE__ */ jsxs2("div", { className: "flex flex-wrap items-center gap-2", children: [
                    item.images.map((img) => /* @__PURE__ */ jsxs2("div", { className: "group relative", children: [
                      /* @__PURE__ */ jsx3(AuthImage, { src: img.url, alt: "", className: "h-20 w-20 rounded-lg object-cover" }),
                      /* @__PURE__ */ jsx3(
                        "button",
                        {
                          type: "button",
                          "aria-label": "Foto entfernen",
                          onClick: () => void removeImage(item, img),
                          className: "absolute right-1 top-1 rounded-full bg-black/70 p-1 text-white opacity-0 transition group-hover:opacity-100",
                          children: /* @__PURE__ */ jsx3(Icon, { name: "x", size: 11 })
                        }
                      )
                    ] }, img.id)),
                    /* @__PURE__ */ jsxs2("label", { className: "flex h-20 w-20 cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-white/20 text-[11px] text-white/50 hover:border-white/40 hover:text-white", children: [
                      uploading === item.id ? "L\xE4dt \u2026" : /* @__PURE__ */ jsxs2(Fragment2, { children: [
                        /* @__PURE__ */ jsx3(Icon, { name: "upload", size: 16 }),
                        " Foto"
                      ] }),
                      /* @__PURE__ */ jsx3(
                        "input",
                        {
                          type: "file",
                          accept: "image/png,image/jpeg,image/webp",
                          className: "hidden",
                          onChange: (e) => {
                            const file = e.target.files?.[0];
                            if (file) void uploadImage(item, file);
                            e.target.value = "";
                          }
                        }
                      )
                    ] })
                  ] })
                ] })
              ] }, item.id);
            })
          ] })
        ] })
      ]
    }
  );
}
export {
  InventoryPage,
  inventoryCsv,
  warrantyState
};
