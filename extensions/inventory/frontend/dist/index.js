// src/InventoryPage.tsx
import { useCallback, useEffect as useEffect3, useState as useState3 } from "react";

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
  primary: "accent-gradient text-on-accent hover:bg-accent-strong",
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

// ../../_shared/frontend/src/loadError.tsx
import { jsx as jsx3, jsxs as jsxs2 } from "react/jsx-runtime";
var UNREADABLE_ANSWER_TEXT = "Die Antwort des Servers lie\xDF sich nicht lesen.";
async function fetchJson(path, init) {
  const res = await authedFetch(path, init);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(errorFromBody(body, res.status));
  }
  try {
    return await res.json();
  } catch {
    throw new Error(UNREADABLE_ANSWER_TEXT);
  }
}
function errorMessage(err) {
  return err instanceof Error ? err.message : String(err);
}
function LoadError({ what, error, onRetry, hint, compact = false }) {
  if (compact) {
    return /* @__PURE__ */ jsxs2("div", { role: "alert", "data-testid": "load-error", className: "text-sm", children: [
      /* @__PURE__ */ jsxs2("p", { className: "text-red-300", children: [
        what,
        " lie\xDF sich gerade nicht laden: ",
        error
      ] }),
      hint && /* @__PURE__ */ jsx3("p", { className: "text-xs text-white/50", children: hint }),
      onRetry && /* @__PURE__ */ jsx3("button", { type: "button", onClick: onRetry, className: "mt-1 text-xs text-white/70 underline underline-offset-2 hover:text-white", children: "Erneut versuchen" })
    ] });
  }
  return /* @__PURE__ */ jsxs2("div", { role: "alert", "data-testid": "load-error", className: "mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm", children: [
    /* @__PURE__ */ jsxs2("div", { className: "min-w-0 flex-1", children: [
      /* @__PURE__ */ jsxs2("p", { className: "text-red-200", children: [
        what,
        " lie\xDF sich gerade nicht laden: ",
        error
      ] }),
      hint && /* @__PURE__ */ jsx3("p", { className: "text-xs text-white/55", children: hint })
    ] }),
    onRetry && /* @__PURE__ */ jsx3(Button, { small: true, onClick: onRetry, children: "Erneut versuchen" })
  ] });
}

// ../../_shared/frontend/src/menu.tsx
import { useEffect as useEffect2, useRef, useState as useState2 } from "react";
import { jsx as jsx4, jsxs as jsxs3 } from "react/jsx-runtime";
function MoreMenu({
  items,
  label = "Mehr",
  title = "Weitere Aktionen",
  align = "left",
  small = false,
  testId
}) {
  const ref = useRef(null);
  const [open, setOpen] = useState2(false);
  const close = (refocus) => {
    const details = ref.current;
    if (!details) return;
    if (refocus) details.querySelector("summary")?.focus();
    details.open = false;
  };
  useEffect2(() => {
    if (!open) return;
    const outside = (e) => {
      if (ref.current && !ref.current.contains(e.target)) close(false);
    };
    const escape = (e) => {
      if (e.key === "Escape") close(true);
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  return /* @__PURE__ */ jsxs3(
    "details",
    {
      ref,
      className: "relative",
      "data-testid": testId,
      onToggle: (e) => setOpen(e.currentTarget.open),
      onBlur: (e) => {
        if (ref.current?.open && e.relatedTarget && !ref.current.contains(e.relatedTarget)) close(false);
      },
      children: [
        /* @__PURE__ */ jsxs3("summary", { title, "aria-label": `${label}: ${title}`, className: `${buttonClass("secondary", small)} cursor-pointer list-none [&::-webkit-details-marker]:hidden`, children: [
          label,
          " ",
          /* @__PURE__ */ jsx4(Icon, { name: "chevron-down", size: small ? 12 : 14 })
        ] }),
        /* @__PURE__ */ jsx4(
          "div",
          {
            className: `absolute ${align === "right" ? "right-0" : "left-0"} z-20 mt-1 flex w-56 max-w-[calc(100vw-2rem)] flex-col rounded-xl border border-white/10 bg-[var(--color-surface)] p-1 shadow-xl`,
            children: items.map((item) => /* @__PURE__ */ jsx4(
              "button",
              {
                type: "button",
                disabled: item.disabled,
                title: item.title,
                onClick: () => {
                  close(true);
                  item.run();
                },
                className: `rounded-md px-3 py-2 text-left text-sm disabled:opacity-50 ${item.danger ? "text-red-300 hover:bg-red-500/15" : "hover:bg-white/[0.06]"}`,
                children: item.label
              },
              item.id
            ))
          }
        )
      ]
    }
  );
}

// src/importCsv.ts
var MAX_IMPORT_ROWS = 5e3;
var MAX_IMPORT_BYTES = 10 * 1024 * 1024;
var MAX_QUANTITY = 1e5;
var MAX_PRICE_CENTS = 1e9;
function decodeCsv(bytes) {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    try {
      return new TextDecoder("windows-1252").decode(bytes);
    } catch {
      return new TextDecoder("utf-8").decode(bytes);
    }
  }
}
function separatorCounts(line) {
  const counts = { "	": 0, ";": 0, ",": 0 };
  let quoted = false;
  for (const ch of line) {
    if (ch === '"') quoted = !quoted;
    else if (!quoted && ch in counts) counts[ch] += 1;
  }
  return counts;
}
var NO_SEPARATOR = "\0";
function detectSeparator(lines) {
  const [head, ...rest] = lines.map(separatorCounts);
  if (!head) return NO_SEPARATOR;
  const top = Math.max(...Object.values(head));
  if (top === 0) return NO_SEPARATOR;
  const tied = Object.keys(head).filter((sep) => head[sep] === top);
  return tied.find((sep) => rest.every((counts) => counts[sep] === top)) ?? tied[0];
}
function parseCsvDetailed(text) {
  let clean = text.replace(/^﻿/, "");
  const sepLine = clean.match(/^sep=(.)\r?\n/i);
  if (sepLine) clean = clean.slice(sepLine[0].length);
  const sep = sepLine ? sepLine[1] : detectSeparator(clean.split(/\r?\n/, 6).filter((l) => l.trim() !== "").slice(0, 5));
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  let fieldStart = true;
  for (let i = 0; i < clean.length; i += 1) {
    const ch = clean[i];
    if (quoted) {
      if (ch === '"' && clean[i + 1] === '"') {
        field += '"';
        i += 1;
      } else if (ch === '"') quoted = false;
      else field += ch;
      continue;
    }
    if (ch === '"' && fieldStart) {
      quoted = true;
      fieldStart = false;
      field = "";
    } else if (ch === sep) {
      row.push(field);
      field = "";
      fieldStart = true;
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && clean[i + 1] === "\n") i += 1;
      row.push(field);
      field = "";
      fieldStart = true;
      if (row.some((f) => f.trim() !== "")) rows.push(row);
      row = [];
    } else {
      field += ch;
      if (ch !== " ") fieldStart = false;
    }
  }
  row.push(field);
  if (row.some((f) => f.trim() !== "")) rows.push(row);
  return { rows, unclosed: quoted };
}
var COLUMNS = {
  name: ["product name", "produktname", "name", "artikel", "artikelname", "bezeichnung", "title", "titel", "produkt"],
  quantity: ["quantity", "menge", "anzahl", "st\xFCck", "stueck"],
  purchase_date: ["order date", "bestelldatum", "kaufdatum", "datum", "date", "purchase date"],
  // Preis je Stück ...
  unit_price: ["unit price", "purchase price per unit", "preis (eur)", "preis", "einzelpreis", "st\xFCckpreis", "price"],
  // ... oder Summe der Zeile (wird durch die Menge geteilt).
  total_price: ["item total", "total owed", "gesamtpreis", "betrag", "summe", "total"],
  currency: ["currency", "w\xE4hrung", "waehrung"],
  warranty_until: ["garantie bis", "warranty until", "warranty"],
  description: ["beschreibung", "description"],
  manufacturer: ["hersteller", "manufacturer", "marke", "brand"],
  model: ["modell", "model"],
  serial_number: ["item serial number", "seriennummer", "serial number", "serial"],
  product_url: ["produktlink", "product url", "url", "link"],
  notes: ["notizen", "notes", "bemerkung"],
  category: ["kategorie", "category"],
  location: ["standort", "lagerort", "location"],
  asin: ["asin", "asin/isbn"],
  website: ["website"],
  status: ["order status", "bestellstatus"]
};
function findColumn(header, names) {
  const norm = header.map((h) => h.trim().toLowerCase());
  for (const name of names) {
    const i = norm.indexOf(name);
    if (i >= 0) return i;
  }
  return -1;
}
function isoDate(year, month, day) {
  const d = new Date(Date.UTC(year, month - 1, day));
  if (d.getUTCFullYear() !== year || d.getUTCMonth() !== month - 1 || d.getUTCDate() !== day) return null;
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}
function fullYear(y, future, today = /* @__PURE__ */ new Date()) {
  if (y.length !== 2) return Number(y);
  const year = 2e3 + Number(y);
  return year > today.getFullYear() + (future ? 30 : 1) ? year - 100 : year;
}
function slashOrder(values) {
  let dayFirst = false;
  let monthFirst = false;
  for (const value of values) {
    const m = value.trim().match(/^(\d{1,2})\/(\d{1,2})\//);
    if (!m) continue;
    if (Number(m[1]) > 12) dayFirst = true;
    if (Number(m[2]) > 12) monthFirst = true;
  }
  return dayFirst && !monthFirst ? "day-first" : monthFirst && !dayFirst ? "month-first" : void 0;
}
function parseDate(value, order, future = false) {
  const v = value.trim();
  let m = v.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return isoDate(Number(m[1]), Number(m[2]), Number(m[3]));
  m = v.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})(?!\d)/);
  if (m) return isoDate(fullYear(m[3], future), Number(m[2]), Number(m[1]));
  m = v.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4}|\d{2})(?!\d)/);
  if (m) {
    const [a, b] = [Number(m[1]), Number(m[2])];
    const dayFirst = order ? order === "day-first" : a > 12;
    return dayFirst ? isoDate(fullYear(m[3], future), b, a) : isoDate(fullYear(m[3], future), a, b);
  }
  return null;
}
var CURRENCY_CODES = [
  "USD",
  "GBP",
  "CHF",
  "PLN",
  "CZK",
  "SEK",
  "DKK",
  "NOK",
  "JPY",
  "CAD",
  "AUD",
  "CNY",
  "HUF",
  "TRY",
  "BRL",
  "INR",
  "MXN",
  "AED",
  "SAR",
  "SGD",
  "RON",
  "ZAR",
  "KRW",
  "HKD",
  "NZD",
  "BGN",
  "ILS"
];
function foreignCurrency(price, column = "") {
  const col = column.trim().toUpperCase();
  if (/^[A-Z]{3}$/.test(col) && col !== "EUR") return col;
  const v = price.toUpperCase();
  if (/€|\bEUR\b|\bEURO/.test(v)) return null;
  const code = v.match(new RegExp(`\\b(${CURRENCY_CODES.join("|")})\\b`));
  if (code) return code[1];
  const symbols = [
    [/R\$/, "BRL"],
    [/CA?\$/, "CAD"],
    [/A\$/, "AUD"],
    [/₹/, "INR"],
    [/₩/, "KRW"],
    [/ZŁ/, "PLN"],
    [/KČ/, "CZK"],
    [/\$/, "USD"],
    [/£/, "GBP"],
    [/¥/, "JPY"],
    [/\bFR\./, "CHF"],
    [/\bKR\b/, "Kronen"]
  ];
  return symbols.find(([re]) => re.test(v))?.[1] ?? null;
}
function parsePriceCents(value) {
  if (/[-−–‒]\s*\d|\d\s*[-−–‒]|^\s*\(.*\)\s*$/.test(value)) return null;
  const joined = value.replace(/(\d)[\s\u00a0\u202f](?=\d{3}(?!\d))/g, "$1");
  let v = (joined.match(/[.,]?\d[\d.,]*/)?.[0] ?? "").replace(/[.,]+$/, "");
  if (!v) return null;
  const lastComma = v.lastIndexOf(",");
  const lastDot = v.lastIndexOf(".");
  if (lastComma >= 0 && lastDot >= 0) {
    v = lastComma > lastDot ? v.replace(/\./g, "").replace(",", ".") : v.replace(/,/g, "");
  } else {
    const mark = lastComma >= 0 ? "," : ".";
    const parts = v.split(mark);
    const thousands = parts.length > 1 && parts.slice(1).every((p) => p.length === 3) && parts[0] !== "" && parts[0] !== "0";
    v = thousands ? parts.join("") : parts.length > 2 ? "" : parts.join(".");
  }
  const num = Number(v);
  if (!v || !Number.isFinite(num)) return null;
  const cents = Math.round(num * 100);
  return cents <= MAX_PRICE_CENTS ? cents : null;
}
function cleanUrl(value) {
  if (!value || value.length > 2e3 || /[\s\u0000-\u001f\u007f]/.test(value)) return null;
  if (!/^https?:\/\/[^/\s]/i.test(value)) return null;
  if (/^https?:\/\/[^/?#]*@/i.test(value)) return null;
  try {
    const url = new URL(value);
    return (url.protocol === "http:" || url.protocol === "https:") && url.hostname && !url.username && !url.password ? value : null;
  } catch {
    return null;
  }
}
function cut(text, max) {
  return text.length <= max ? text : Array.from(text).slice(0, max).join("");
}
var EMPTY = /* @__PURE__ */ new Set(["-", "\u2013", "\u2014", "n/a", "not available"]);
function rowsFromCsv(text) {
  const { rows: table, unclosed } = parseCsvDetailed(text);
  const [header, ...lines] = table;
  if (!header) return { rows: [], source: "andere", skipped: 0, notes: [] };
  const col = Object.fromEntries(Object.entries(COLUMNS).map(([key, names]) => [key, findColumn(header, names)]));
  if (col.name < 0) return { rows: [], source: "andere", skipped: lines.length, notes: [] };
  const source = col.asin >= 0 ? "amazon" : findColumn(header, ["garantie bis"]) >= 0 ? "inventar" : "andere";
  const get = (line, key) => {
    let value = col[key] >= 0 ? (line[col[key]] ?? "").trim() : "";
    if (/^'[=+\-@]/.test(value)) value = value.slice(1);
    return EMPTY.has(value.toLowerCase()) ? "" : value;
  };
  const dateOrder = slashOrder(lines.map((line) => get(line, "purchase_date")));
  const warrantyOrder = slashOrder(lines.map((line) => get(line, "warranty_until")));
  const rows = [];
  const currencies = /* @__PURE__ */ new Map();
  let skipped = 0;
  let cutOff = 0;
  for (const line of lines) {
    const name = get(line, "name");
    if (!name || /cancel|storniert/i.test(get(line, "status"))) {
      skipped += 1;
      continue;
    }
    if (rows.length >= MAX_IMPORT_ROWS) {
      cutOff += 1;
      continue;
    }
    const quantityMatch = get(line, "quantity").match(/^(\d{1,9})(?:[.,]0+)?(?![\d.,])/);
    const quantityRaw = quantityMatch ? Number(quantityMatch[1]) : NaN;
    const quantity = quantityRaw > 0 && quantityRaw <= MAX_QUANTITY ? quantityRaw : 1;
    const unitText = get(line, "unit_price");
    const priceText = unitText || get(line, "total_price");
    const currency = priceText ? foreignCurrency(priceText, get(line, "currency")) : null;
    let price = null;
    if (currency) currencies.set(currency, (currencies.get(currency) ?? 0) + 1);
    else {
      price = parsePriceCents(priceText);
      if (price != null && !unitText) price = Math.round(price / quantity);
    }
    const asin = get(line, "asin").toUpperCase();
    const website = get(line, "website").toLowerCase().replace(/^www\./, "");
    const shop = /^amazon\.[a-z]{2,3}(\.[a-z]{2})?$/.test(website) ? website : "amazon.de";
    rows.push({
      name: cut(name, 300),
      quantity,
      purchase_date: parseDate(get(line, "purchase_date"), dateOrder),
      purchase_price_cents: price,
      warranty_until: parseDate(get(line, "warranty_until"), warrantyOrder, true),
      description: cut(get(line, "description"), 2e3) || null,
      manufacturer: cut(get(line, "manufacturer"), 200) || null,
      model: cut(get(line, "model"), 200) || null,
      serial_number: cut(get(line, "serial_number"), 200) || null,
      // Bei Amazon gibt es keinen Link, aber die ASIN: daraus wird der Produktlink (im Shop der Bestellung).
      product_url: cleanUrl(get(line, "product_url")) ?? (/^[A-Z0-9]{10}$/.test(asin) ? `https://www.${shop}/dp/${asin}` : null),
      notes: cut(get(line, "notes"), 5e3) || null,
      category: get(line, "category") || null,
      location: get(line, "location") || null
    });
  }
  const notes = [];
  for (const [code, count] of currencies) {
    notes.push(`${count === 1 ? "1 Preis" : `${count} Preise`} in ${code}: nicht \xFCbernommen (das Inventar rechnet in Euro).`);
  }
  if (cutOff > 0) {
    notes.push(`Die Datei hat mehr als ${MAX_IMPORT_ROWS} Eintr\xE4ge: angezeigt werden die ersten ${MAX_IMPORT_ROWS}, ${cutOff} weitere nicht.`);
  }
  if (unclosed) {
    notes.push("In der Datei fehlt ein schlie\xDFendes Anf\xFChrungszeichen: die letzten Eintr\xE4ge sind wom\xF6glich unvollst\xE4ndig. Pr\xFCfe sie vor dem \xDCbernehmen.");
  }
  return { rows, source, skipped, notes };
}

// src/InventoryPage.tsx
import { Fragment as Fragment2, jsx as jsx5, jsxs as jsxs4 } from "react/jsx-runtime";
function itemPayload(item, changes = {}) {
  const i = { ...item, ...changes };
  return {
    name: i.name,
    description: i.description,
    category_id: i.category_id,
    location_id: i.location_id,
    quantity: i.quantity,
    purchase_date: i.purchase_date,
    purchase_price_cents: i.purchase_price_cents,
    warranty_until: i.warranty_until,
    notes: i.notes,
    ...Object.fromEntries(Object.keys(changes).map((key) => [key, i[key] ?? null]))
  };
}
function rackRows(height, items) {
  const rows = [];
  let unit = height;
  while (unit >= 1) {
    const item = items.find((i) => i.rack_position != null && i.rack_position <= unit && unit <= i.rack_position + (i.rack_units ?? 1) - 1);
    if (item) {
      const bottom = item.rack_position;
      rows.push({ kind: "device", top: unit, bottom, item });
      unit = bottom - 1;
    } else {
      rows.push({ kind: "free", unit });
      unit -= 1;
    }
  }
  return rows;
}
var PROJECT_STATUS = {
  planned: { label: "geplant", tone: "neutral" },
  building: { label: "im Bau", tone: "info" },
  done: { label: "fertig", tone: "good" }
};
function priceQuery(item) {
  const exact = [item.manufacturer, item.model].map((v) => v?.trim()).filter(Boolean).join(" ");
  return item.model?.trim() ? exact : item.name.trim();
}
function priceLinks(item) {
  const q = encodeURIComponent(priceQuery(item));
  return [
    { label: "Geizhals", href: `https://geizhals.de/?fs=${q}` },
    { label: "idealo", href: `https://www.idealo.de/preisvergleich/MainSearchProductCategory.html?q=${q}` }
  ];
}
var DAY_MS = 864e5;
var inlineInput = inputClass.replace("w-full ", "");
function warrantyState(until, today = /* @__PURE__ */ new Date()) {
  if (!until) return "none";
  const days = ((/* @__PURE__ */ new Date(`${until}T00:00:00`)).getTime() - new Date(today.toDateString()).getTime()) / DAY_MS;
  if (days < 0) return "expired";
  return days <= 90 ? "soon" : "active";
}
function csvCell(value) {
  let text = value == null ? "" : String(value);
  if (typeof value === "string" && /^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return /[";\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}
function inventoryCsv(items, categoryName, locationName) {
  const header = [
    "Name",
    "Kategorie",
    "Standort",
    "Menge",
    "Kaufdatum",
    "Preis (EUR)",
    "Garantie bis",
    "Beschreibung",
    "Notizen",
    "Hersteller",
    "Modell",
    "Seriennummer",
    "Produktlink"
  ];
  const rows = items.map((i) => [
    i.name,
    categoryName(i.category_id),
    locationName(i.location_id),
    i.quantity,
    i.purchase_date,
    i.purchase_price_cents == null ? "" : (i.purchase_price_cents / 100).toFixed(2).replace(".", ","),
    i.warranty_until,
    i.description,
    i.notes,
    i.manufacturer,
    i.model,
    i.serial_number,
    i.product_url
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
  notes: "",
  manufacturer: "",
  model: "",
  serial_number: "",
  product_url: "",
  host_id: "",
  project_id: ""
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
  const [newCategory, setNewCategory] = useState3("");
  const [newLocation, setNewLocation] = useState3("");
  const [message, setMessage] = useState3(null);
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
  const column = (title, icon, entries, kind, value, setValue, placeholder, what) => /* @__PURE__ */ jsxs4("div", { children: [
    /* @__PURE__ */ jsxs4("p", { className: "mb-2 flex items-center gap-1.5 text-xs font-medium text-white/45", children: [
      /* @__PURE__ */ jsx5(Icon, { name: icon, size: 13 }),
      " ",
      title
    ] }),
    /* @__PURE__ */ jsxs4("div", { className: "mb-3 flex flex-wrap gap-1.5", children: [
      entries.map((e) => /* @__PURE__ */ jsxs4("span", { className: "inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] py-1 pl-2.5 pr-1.5 text-sm", children: [
        e.name,
        /* @__PURE__ */ jsx5("button", { type: "button", "aria-label": `${e.name} entfernen`, onClick: () => void remove(kind, e.id, what), className: "rounded p-0.5 text-white/40 hover:bg-white/10 hover:text-white", children: /* @__PURE__ */ jsx5(Icon, { name: "x", size: 12 }) })
      ] }, e.id)),
      entries.length === 0 && /* @__PURE__ */ jsx5("span", { className: "text-sm text-white/40", children: "Noch keine." })
    ] }),
    /* @__PURE__ */ jsxs4("form", { onSubmit: (e) => {
      e.preventDefault();
      void add(kind, value, () => setValue(""));
    }, className: "flex gap-2", children: [
      /* @__PURE__ */ jsx5("input", { value, onChange: (e) => setValue(e.target.value), placeholder, className: inputClass }),
      /* @__PURE__ */ jsx5(Button, { type: "submit", ariaLabel: `${what} hinzuf\xFCgen`, children: /* @__PURE__ */ jsx5(Icon, { name: "plus", size: 14 }) })
    ] })
  ] });
  return /* @__PURE__ */ jsxs4(Card, { title: "Kategorien & Standorte", description: "Zum Ordnen und Filtern. Entfernen l\xE4sst die Gegenst\xE4nde selbst unver\xE4ndert.", className: "mb-5", children: [
    message && /* @__PURE__ */ jsx5(Notice, { text: message, onClose: () => setMessage(null) }),
    /* @__PURE__ */ jsxs4("div", { className: "grid gap-6 sm:grid-cols-2", children: [
      column("Kategorien", "tag", categories, "categories", newCategory, setNewCategory, "Neue Kategorie", "Kategorie"),
      column("Standorte", "map-pin", locations, "locations", newLocation, setNewLocation, "Neuer Standort", "Standort")
    ] })
  ] });
}
function ProjectsPanel({
  projects,
  onChanged,
  onShowItems,
  activeId
}) {
  const [name, setName] = useState3("");
  const [budget, setBudget] = useState3("");
  const [message, setMessage] = useState3(null);
  async function send(path, method, body) {
    const res = await authedFetch(path, { method, ...body === void 0 ? {} : { body: JSON.stringify(body) } });
    if (!res.ok && res.status !== 204) {
      const data = await res.json().catch(() => ({}));
      setMessage(`Fehler: ${errorFromBody(data, res.status)}`);
      return false;
    }
    setMessage(null);
    onChanged();
    return true;
  }
  async function create(e) {
    e.preventDefault();
    if (!name.trim()) return;
    const cents = budget ? Math.round(parseFloat(budget) * 100) : null;
    if (await send("/ext/inventory/projects", "POST", { name: name.trim(), budget_cents: cents })) {
      setName("");
      setBudget("");
    }
  }
  async function remove(project) {
    const ok = await deck().confirmDialog(
      `Bauprojekt \u201E${project.name}\u201C entfernen? Die Teile bleiben im Inventar, sie geh\xF6ren dann nur zu keinem Projekt mehr.`,
      { danger: true, confirmLabel: "Entfernen" }
    );
    if (ok) await send(`/ext/inventory/projects/${project.id}`, "DELETE");
  }
  return /* @__PURE__ */ jsxs4(Card, { title: "Bauprojekte", description: "Plane, was du bauen willst, und sieh, welche Teile daf\xFCr da sind und was es kostet.", className: "mb-5", children: [
    message && /* @__PURE__ */ jsx5(Notice, { text: message, onClose: () => setMessage(null) }),
    projects.length === 0 && /* @__PURE__ */ jsx5("p", { className: "mb-3 text-sm text-white/45", children: "Noch kein Bauprojekt." }),
    /* @__PURE__ */ jsx5("ul", { className: "mb-4 divide-y divide-white/[0.06]", "data-testid": "projects", children: projects.map((p) => {
      const over = p.budget_cents != null && p.spent_cents > p.budget_cents;
      return /* @__PURE__ */ jsxs4("li", { className: "flex flex-wrap items-center gap-2 py-2 text-sm", "data-testid": `project-${p.id}`, children: [
        /* @__PURE__ */ jsx5("span", { className: "font-medium", children: p.name }),
        /* @__PURE__ */ jsx5(Badge, { tone: PROJECT_STATUS[p.status]?.tone ?? "neutral", children: PROJECT_STATUS[p.status]?.label ?? p.status }),
        /* @__PURE__ */ jsxs4("span", { className: "text-white/55", children: [
          p.item_count === 1 ? "1 Teil" : `${p.item_count} Teile`,
          " \xB7 ",
          formatPrice(p.spent_cents),
          " ausgegeben",
          p.budget_cents != null && /* @__PURE__ */ jsxs4(Fragment2, { children: [
            " von ",
            formatPrice(p.budget_cents)
          ] }),
          !!p.unpriced_count && /* @__PURE__ */ jsxs4(Fragment2, { children: [
            " \xB7 ",
            p.unpriced_count === 1 ? "1 Teil ohne Preis" : `${p.unpriced_count} Teile ohne Preis`
          ] })
        ] }),
        over && /* @__PURE__ */ jsx5(Badge, { tone: "bad", children: "\xFCber Budget" }),
        /* @__PURE__ */ jsx5("span", { className: "flex-1" }),
        /* @__PURE__ */ jsx5(
          "select",
          {
            value: p.status,
            "aria-label": `Status von ${p.name}`,
            className: `${inlineInput} w-auto py-1 text-xs`,
            onChange: (e) => void send(`/ext/inventory/projects/${p.id}`, "PUT", {
              name: p.name,
              status: e.target.value,
              description: p.description,
              budget_cents: p.budget_cents
            }),
            children: Object.keys(PROJECT_STATUS).map((st) => /* @__PURE__ */ jsx5("option", { value: st, children: PROJECT_STATUS[st].label }, st))
          }
        ),
        /* @__PURE__ */ jsx5(Button, { small: true, pressed: activeId === p.id, onClick: () => onShowItems(p.id), children: "Teile zeigen" }),
        /* @__PURE__ */ jsx5(Button, { small: true, variant: "danger", ariaLabel: `${p.name} entfernen`, onClick: () => void remove(p), children: /* @__PURE__ */ jsx5(Icon, { name: "trash", size: 13 }) })
      ] }, p.id);
    }) }),
    /* @__PURE__ */ jsxs4("form", { onSubmit: (e) => void create(e), className: "flex flex-wrap gap-2", children: [
      /* @__PURE__ */ jsx5("input", { value: name, onChange: (e) => setName(e.target.value), placeholder: "Neues Bauprojekt, z. B. NAS bauen", "aria-label": "Name des Bauprojekts", className: `${inlineInput} min-w-[12rem] flex-1` }),
      /* @__PURE__ */ jsx5("input", { value: budget, onChange: (e) => setBudget(e.target.value), type: "number", min: "0", max: "10000000", step: "0.01", placeholder: "Budget (\u20AC, optional)", "aria-label": "Budget in Euro", className: `${inlineInput} w-44` }),
      /* @__PURE__ */ jsxs4(Button, { type: "submit", variant: "primary", disabled: !name.trim(), children: [
        /* @__PURE__ */ jsx5(Icon, { name: "plus", size: 14 }),
        " Anlegen"
      ] })
    ] })
  ] });
}
function RacksPanel({ items, onChanged }) {
  const [racks, setRacks] = useState3(null);
  const [loadError, setLoadError] = useState3(null);
  const [selected, setSelected] = useState3("");
  const [message, setMessage] = useState3(null);
  const [name, setName] = useState3("");
  const [height, setHeight] = useState3("42");
  const [placing, setPlacing] = useState3(null);
  const [placeItem, setPlaceItem] = useState3("");
  const [placeUnits, setPlaceUnits] = useState3("1");
  const [rackItems, setRackItems] = useState3(null);
  const loadRackItems = useCallback((rackId) => {
    if (!rackId) {
      setRackItems([]);
      return;
    }
    fetchJson(`/ext/inventory/items?rack_id=${encodeURIComponent(rackId)}`).then(setRackItems).catch((err) => setMessage(`Fehler: ${errorMessage(err)}`));
  }, []);
  useEffect3(() => {
    loadRackItems(selected);
  }, [selected, loadRackItems, items]);
  const loadRacks = useCallback(() => {
    setLoadError(null);
    fetchJson("/ext/inventory/racks").then((data) => {
      setRacks(data);
      setSelected((current) => data.some((r) => r.id === current) ? current : data[0]?.id ?? "");
    }).catch((err) => setLoadError(errorMessage(err)));
  }, []);
  useEffect3(() => {
    loadRacks();
  }, [loadRacks]);
  async function send(path, method, body) {
    const res = await authedFetch(path, { method, ...body === void 0 ? {} : { body: JSON.stringify(body) } });
    if (!res.ok && res.status !== 204) {
      const data = await res.json().catch(() => ({}));
      setMessage(`Fehler: ${errorFromBody(data, res.status)}`);
      return false;
    }
    setMessage(null);
    return true;
  }
  async function createRack(e) {
    e.preventDefault();
    if (!name.trim()) return;
    const res = await authedFetch("/ext/inventory/racks", {
      method: "POST",
      body: JSON.stringify({ name: name.trim(), height_units: parseInt(height, 10) || 42 })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setMessage(`Fehler: ${errorFromBody(data, res.status)}`);
      return;
    }
    setMessage(null);
    setName("");
    setSelected(data.id);
    loadRacks();
  }
  async function removeRack(rack2) {
    const ok = await deck().confirmDialog(
      `Rack \u201E${rack2.name}\u201C entfernen? Die Ger\xE4te bleiben im Inventar, sie stecken dann nur in keinem Rack mehr.`,
      { danger: true, confirmLabel: "Entfernen" }
    );
    if (!ok) return;
    if (await send(`/ext/inventory/racks/${rack2.id}`, "DELETE")) {
      loadRacks();
      onChanged();
    }
  }
  async function place(rack2, unit) {
    const item = items.find((i) => i.id === placeItem);
    if (!item) return;
    const units = Math.max(1, parseInt(placeUnits, 10) || 1);
    if (unit - units + 1 < 1) {
      setMessage(`Unter HE ${unit} ist nicht genug Platz f\xFCr ${units} HE. W\xE4hle eine kleinere H\xF6he oder eine h\xF6here HE.`);
      return;
    }
    const changes = { rack_id: rack2.id, rack_position: unit - units + 1, rack_units: units };
    if (await send(`/ext/inventory/items/${item.id}`, "PUT", itemPayload(item, changes))) {
      setPlacing(null);
      setPlaceItem("");
      setPlaceUnits("1");
      loadRackItems(rack2.id);
      onChanged();
    }
  }
  async function takeOut(item) {
    if (await send(`/ext/inventory/items/${item.id}`, "PUT", itemPayload(item, { rack_id: null, rack_position: null, rack_units: null }))) {
      loadRackItems(selected);
      onChanged();
    }
  }
  if (loadError) return /* @__PURE__ */ jsx5(LoadError, { what: "Die Liste der Racks", error: loadError, onRetry: loadRacks });
  if (!racks) return /* @__PURE__ */ jsx5(Card, { title: "Racks", className: "mb-5", children: /* @__PURE__ */ jsx5(Loading, {}) });
  const rack = racks.find((r) => r.id === selected) ?? null;
  const inRack = rack ? (rackItems ?? []).filter((i) => i.rack_id === rack.id) : [];
  const unplaced = inRack.filter((i) => i.rack_position == null);
  const candidates = items.filter((i) => i.rack_id !== rack?.id);
  return /* @__PURE__ */ jsxs4(Card, { title: "Racks", description: "Wo welches Ger\xE4t im Serverschrank steckt. H\xF6heneinheiten (HE) von oben nach unten.", className: "mb-5", children: [
    message && /* @__PURE__ */ jsx5(Notice, { text: message, onClose: () => setMessage(null) }),
    /* @__PURE__ */ jsxs4("div", { className: "mb-4 flex flex-wrap items-center gap-2", children: [
      racks.map((r) => /* @__PURE__ */ jsxs4(Button, { small: true, pressed: r.id === selected, onClick: () => setSelected(r.id), children: [
        r.name,
        " \xB7 ",
        r.height_units,
        " HE"
      ] }, r.id)),
      racks.length === 0 && /* @__PURE__ */ jsx5("span", { className: "text-sm text-white/45", children: "Noch kein Rack." })
    ] }),
    rack && /* @__PURE__ */ jsxs4("div", { className: "mb-4", children: [
      /* @__PURE__ */ jsxs4("div", { className: "mb-2 flex items-center gap-2", children: [
        /* @__PURE__ */ jsx5("span", { className: "text-sm font-medium", children: rack.name }),
        /* @__PURE__ */ jsx5("span", { className: "flex-1" }),
        /* @__PURE__ */ jsx5(Button, { small: true, variant: "danger", ariaLabel: `Rack ${rack.name} entfernen`, onClick: () => void removeRack(rack), children: /* @__PURE__ */ jsx5(Icon, { name: "trash", size: 13 }) })
      ] }),
      /* @__PURE__ */ jsx5("ol", { className: "max-w-xl overflow-hidden rounded-lg border border-white/15 bg-black/25", "data-testid": `rack-${rack.id}`, children: rackRows(rack.height_units, inRack).map(
        (row) => row.kind === "device" ? /* @__PURE__ */ jsxs4(
          "li",
          {
            className: "flex items-center gap-2 border-b border-white/[0.06] bg-[color-mix(in_srgb,var(--color-accent)_14%,transparent)] px-2 text-xs",
            style: { minHeight: `${(row.top - row.bottom + 1) * 1.75}rem` },
            "data-testid": `rack-device-${row.item.id}`,
            children: [
              /* @__PURE__ */ jsx5("span", { className: "w-14 flex-none tabular-nums text-white/45", children: row.top === row.bottom ? `HE ${row.top}` : `HE ${row.bottom}\u2013${row.top}` }),
              /* @__PURE__ */ jsx5("span", { className: "min-w-0 flex-1 truncate font-medium", children: row.item.name }),
              /* @__PURE__ */ jsx5(Button, { small: true, variant: "ghost", onClick: () => void takeOut(row.item), children: "Herausnehmen" })
            ]
          },
          `d-${row.item.id}`
        ) : /* @__PURE__ */ jsxs4("li", { className: "flex min-h-[1.75rem] flex-wrap items-center gap-2 border-b border-white/[0.04] px-2 text-xs text-white/35", children: [
          /* @__PURE__ */ jsxs4("span", { className: "w-14 flex-none tabular-nums", children: [
            "HE ",
            row.unit
          ] }),
          placing === row.unit ? /* @__PURE__ */ jsxs4("form", { onSubmit: (e) => {
            e.preventDefault();
            void place(rack, row.unit);
          }, className: "flex flex-1 flex-wrap items-center gap-1.5 py-1 text-white", children: [
            /* @__PURE__ */ jsxs4("select", { value: placeItem, onChange: (e) => setPlaceItem(e.target.value), "aria-label": "Ger\xE4t", className: `${inlineInput} w-auto flex-1 py-1 text-xs`, children: [
              /* @__PURE__ */ jsx5("option", { value: "", children: "Ger\xE4t w\xE4hlen \u2026" }),
              candidates.map((i) => /* @__PURE__ */ jsx5("option", { value: i.id, children: i.name }, i.id))
            ] }),
            /* @__PURE__ */ jsxs4("label", { className: "flex items-center gap-1 text-white/60", children: [
              "H\xF6he",
              /* @__PURE__ */ jsx5("input", { value: placeUnits, onChange: (e) => setPlaceUnits(e.target.value), type: "number", min: "1", max: rack.height_units, "aria-label": "H\xF6he in HE", className: `${inlineInput} w-16 py-1 text-xs` }),
              "HE"
            ] }),
            /* @__PURE__ */ jsx5(Button, { small: true, type: "submit", variant: "primary", disabled: !placeItem, children: "Einsetzen" }),
            /* @__PURE__ */ jsx5(Button, { small: true, variant: "ghost", onClick: () => setPlacing(null), children: "Abbrechen" })
          ] }) : /* @__PURE__ */ jsxs4(Fragment2, { children: [
            /* @__PURE__ */ jsx5("span", { className: "flex-1", children: "frei" }),
            /* @__PURE__ */ jsx5("button", { type: "button", onClick: () => {
              setPlacing(row.unit);
              setPlaceItem("");
              setPlaceUnits("1");
            }, className: "rounded px-1.5 py-0.5 text-white/50 hover:bg-white/[0.06] hover:text-white", "aria-label": `Ger\xE4t in HE ${row.unit} einsetzen`, title: "Ger\xE4t hier einsetzen", children: /* @__PURE__ */ jsx5(Icon, { name: "plus", size: 12 }) })
          ] })
        ] }, `f-${row.unit}`)
      ) }),
      unplaced.length > 0 && /* @__PURE__ */ jsxs4("p", { className: "mt-2 text-xs text-white/55", children: [
        "Im Rack, aber ohne festen Platz: ",
        unplaced.map((i) => i.name).join(", ")
      ] })
    ] }),
    /* @__PURE__ */ jsxs4("form", { onSubmit: (e) => void createRack(e), className: "flex flex-wrap gap-2", children: [
      /* @__PURE__ */ jsx5("input", { value: name, onChange: (e) => setName(e.target.value), placeholder: "Neues Rack, z. B. Keller", "aria-label": "Name des Racks", className: `${inlineInput} min-w-[12rem] flex-1` }),
      /* @__PURE__ */ jsx5("select", { value: height, onChange: (e) => setHeight(e.target.value), "aria-label": "H\xF6he des Racks", className: `${inlineInput} w-auto`, children: ["6", "9", "12", "15", "18", "22", "27", "32", "42", "47"].map((h) => /* @__PURE__ */ jsxs4("option", { value: h, children: [
        h,
        " HE"
      ] }, h)) }),
      /* @__PURE__ */ jsxs4(Button, { type: "submit", variant: "primary", disabled: !name.trim(), children: [
        /* @__PURE__ */ jsx5(Icon, { name: "plus", size: 14 }),
        " Anlegen"
      ] })
    ] })
  ] });
}
async function createItems(entries, onProgress) {
  const outcome = { created: [], failed: [], stopped: false, untried: 0 };
  let inRow = 0;
  for (const [i, entry] of entries.entries()) {
    onProgress(i);
    try {
      const res = await authedFetch("/ext/inventory/items", { method: "POST", body: JSON.stringify(entry.body) });
      if (res.ok) {
        outcome.created.push(entry.key);
        inRow = 0;
        continue;
      }
      const body = await res.json().catch(() => ({}));
      outcome.failed.push({ label: entry.label, reason: errorFromBody(body, res.status) });
    } catch (err) {
      outcome.failed.push({ label: entry.label, reason: err instanceof Error ? err.message : String(err) });
    }
    inRow += 1;
    if (inRow >= 5 && i < entries.length - 1) {
      outcome.stopped = true;
      outcome.untried = entries.length - 1 - i;
      break;
    }
  }
  onProgress(entries.length);
  return outcome;
}
function progressText(progress) {
  if (!progress || progress[1] === 0) return "";
  return `\xDCbernehme \u2026 ${Math.floor(progress[0] / progress[1] * 10) * 10} %`;
}
function FailureList({ outcome, what, onClose }) {
  const n = outcome.failed.length;
  return /* @__PURE__ */ jsxs4("div", { role: "alert", className: "mb-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200", children: [
    /* @__PURE__ */ jsxs4("div", { className: "flex items-start justify-between gap-3", children: [
      /* @__PURE__ */ jsxs4("p", { children: [
        outcome.created.length > 0 && `${outcome.created.length} \xFCbernommen. `,
        n === 1 ? `1 ${what[0]} ging nicht` : `${n} ${what[1]} gingen nicht`,
        outcome.stopped ? ` \u2013 nach mehreren Fehlern in Folge abgebrochen, ${outcome.untried} nicht versucht` : "",
        ". Diese bleiben angekreuzt, du kannst es noch einmal versuchen."
      ] }),
      /* @__PURE__ */ jsx5("button", { type: "button", "aria-label": "Hinweis schlie\xDFen", onClick: onClose, className: "opacity-60 hover:opacity-100", children: /* @__PURE__ */ jsx5(Icon, { name: "x", size: 14 }) })
    ] }),
    /* @__PURE__ */ jsxs4("ul", { className: "mt-1 list-disc pl-5", children: [
      outcome.failed.slice(0, 8).map((f, i) => /* @__PURE__ */ jsxs4("li", { children: [
        "\u201E",
        f.label,
        "\u201C: ",
        f.reason
      ] }, i)),
      n > 8 && /* @__PURE__ */ jsxs4("li", { children: [
        "\u2026 und ",
        n - 8,
        " weitere"
      ] })
    ] })
  ] });
}
var MISSING_TOOLS = {
  lspci: "lspci (Paket \u201Epciutils\u201C) fehlt: Grafik- und Netzwerkkarten stehen deshalb nicht in der Liste.",
  lsblk: "lsblk (Paket \u201Eutil-linux\u201C) fehlt: Datentr\xE4ger stehen deshalb nicht in der Liste.",
  lscpu: "lscpu (Paket \u201Eutil-linux\u201C) fehlt: der Prozessor steht deshalb nicht in der Liste."
};
function ScanPanel({
  hosts,
  categories,
  locations,
  projects,
  onDone
}) {
  const usable = (h) => (h.os_family ?? "linux") === "linux" && h.credential !== null;
  const [hostId, setHostId] = useState3(() => hosts.find(usable)?.id ?? "");
  const [busy, setBusy] = useState3(false);
  const [error, setError] = useState3(null);
  const [result, setResult] = useState3(null);
  const [known, setKnown] = useState3(null);
  const [chosen, setChosen] = useState3(() => /* @__PURE__ */ new Set());
  const [taken, setTaken] = useState3(() => /* @__PURE__ */ new Set());
  const [categoryId, setCategoryId] = useState3("");
  const [locationId, setLocationId] = useState3("");
  const [projectId, setProjectId] = useState3("");
  const [progress, setProgress] = useState3(null);
  const [outcome, setOutcome] = useState3(null);
  const norm = (value) => (value ?? "").trim().toLowerCase();
  const isKnownIn = (k, hostId2, sg) => !!k && (sg.serial_number ? k.serials.has(norm(sg.serial_number)) : k.hostNames.has(`${hostId2}
${norm(sg.name)}`));
  const isKnown = (sg) => !!result && isKnownIn(known, result.host_id, sg);
  async function scan() {
    setBusy(true);
    setError(null);
    setResult(null);
    setOutcome(null);
    setTaken(/* @__PURE__ */ new Set());
    try {
      const [res, items] = await Promise.all([
        authedFetch(`/ext/inventory/hosts/${encodeURIComponent(hostId)}/scan`, { method: "POST" }),
        fetchJson("/ext/inventory/items").catch(() => null)
      ]);
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      const data = body;
      const found = items && {
        serials: new Set(items.map((i) => norm(i.serial_number)).filter(Boolean)),
        hostNames: new Set(items.filter((i) => i.host_id).map((i) => `${i.host_id}
${norm(i.name)}`))
      };
      setKnown(found);
      setResult(data);
      setChosen(new Set(found ? data.suggestions.filter((sg) => sg.preselect && !isKnownIn(found, data.host_id, sg)).map((sg) => sg.key) : []));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  async function take() {
    if (!result) return;
    const picked = result.suggestions.filter((x) => chosen.has(x.key) && !taken.has(x.key));
    setOutcome(null);
    setProgress([0, picked.length]);
    const done = await createItems(
      picked.map((sg) => ({
        key: sg.key,
        label: sg.name,
        body: {
          name: sg.name,
          quantity: sg.quantity,
          description: sg.detail,
          manufacturer: sg.manufacturer,
          model: sg.model,
          serial_number: sg.serial_number,
          host_id: result.host_id,
          category_id: categoryId || null,
          location_id: locationId || null,
          project_id: projectId || null
        }
      })),
      (n) => setProgress([n, picked.length])
    );
    setProgress(null);
    setTaken((prev) => /* @__PURE__ */ new Set([...prev, ...done.created]));
    setChosen((prev) => new Set([...prev].filter((key) => !done.created.includes(key))));
    const count = done.created.length;
    if (count) onDone(count === 1 ? "1 Teil \xFCbernommen." : `${count} Teile \xFCbernommen.`);
    if (done.failed.length) setOutcome(done);
    else setResult(null);
  }
  const toggle = (key) => setChosen((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  });
  const saving = progress !== null;
  const open = result ? result.suggestions.filter((sg) => chosen.has(sg.key) && !taken.has(sg.key)).length : 0;
  return /* @__PURE__ */ jsxs4(
    Card,
    {
      title: "Teile eines Servers auslesen",
      description: "Nodvard Deck fragt den Server per SSH, was drinsteckt: Prozessor, Arbeitsspeicher, Datentr\xE4ger, Mainboard, Netzwerk- und Grafikkarten. Es liest nur, es \xE4ndert nichts.",
      className: "mb-5",
      children: [
        hosts.length === 0 ? /* @__PURE__ */ jsx5("p", { className: "text-sm text-white/55", children: "Noch kein Server in Nodvard Deck. F\xFCge zuerst unter Einstellungen \u2192 Server & Zug\xE4nge einen hinzu." }) : /* @__PURE__ */ jsxs4("div", { className: "mb-3 flex flex-wrap gap-2", children: [
          /* @__PURE__ */ jsxs4(
            "select",
            {
              value: hostId,
              disabled: busy || saving,
              onChange: (e) => {
                setHostId(e.target.value);
                setResult(null);
                setOutcome(null);
              },
              "aria-label": "Server zum Auslesen",
              className: `${inlineInput} min-w-[12rem]`,
              children: [
                /* @__PURE__ */ jsx5("option", { value: "", children: "Server w\xE4hlen \u2026" }),
                hosts.map((h) => /* @__PURE__ */ jsxs4("option", { value: h.id, disabled: !usable(h), children: [
                  h.display_name,
                  (h.os_family ?? "linux") !== "linux" ? " (nur Linux)" : h.credential === null ? " (ohne SSH-Zugang)" : ""
                ] }, h.id))
              ]
            }
          ),
          /* @__PURE__ */ jsx5(Button, { variant: "primary", onClick: () => void scan(), disabled: !hostId || busy || saving, children: busy ? "Lese aus \u2026 (bis zu 30 Sekunden)" : "Auslesen" })
        ] }),
        /* @__PURE__ */ jsx5("p", { className: "sr-only", role: "status", children: busy ? "Lese den Server aus \u2026" : progressText(progress) }),
        error && /* @__PURE__ */ jsx5(Notice, { text: `Fehler: ${error}`, onClose: () => setError(null) }),
        result && /* @__PURE__ */ jsxs4("div", { "data-testid": "scan-result", children: [
          result.virtual && /* @__PURE__ */ jsxs4("p", { className: "mb-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-200", children: [
            "\u201E",
            result.host_name,
            "\u201C ist eine virtuelle Maschine oder ein Container: Die Teile geh\xF6ren dem Wirt (etwa dem Proxmox-Knoten) oder sind nur nachgebildet. Darum ist nichts vorab angekreuzt. Lies am besten den Wirt aus."
          ] }),
          !known && /* @__PURE__ */ jsx5("p", { className: "mb-3 text-sm text-amber-200", children: "Konnte nicht pr\xFCfen, was schon im Inventar steht. Darum ist nichts vorab angekreuzt \u2013 sieh die Liste durch." }),
          (result.missing ?? []).filter((tool) => MISSING_TOOLS[tool]).map((tool) => /* @__PURE__ */ jsx5("p", { className: "mb-3 text-sm text-white/55", children: MISSING_TOOLS[tool] }, tool)),
          outcome && /* @__PURE__ */ jsx5(FailureList, { outcome, what: ["Teil", "Teile"], onClose: () => setOutcome(null) }),
          result.suggestions.length === 0 ? /* @__PURE__ */ jsx5("p", { className: "text-sm text-white/55", children: "Nichts gefunden, was sich als Teil anbietet." }) : /* @__PURE__ */ jsxs4(Fragment2, { children: [
            /* @__PURE__ */ jsx5("ul", { className: "mb-3 divide-y divide-white/[0.06]", children: result.suggestions.map((sg) => {
              const known2 = isKnown(sg);
              const done = taken.has(sg.key);
              return /* @__PURE__ */ jsx5("li", { children: /* @__PURE__ */ jsxs4("label", { className: "flex cursor-pointer flex-wrap items-start gap-2 py-2 text-sm", children: [
                /* @__PURE__ */ jsx5(
                  "input",
                  {
                    type: "checkbox",
                    checked: done || chosen.has(sg.key),
                    disabled: done || saving,
                    onChange: () => toggle(sg.key),
                    "aria-label": `${sg.name} \xFCbernehmen${known2 ? " (schon im Inventar)" : ""}${done ? " (\xFCbernommen)" : ""}`,
                    className: "mt-1"
                  }
                ),
                /* @__PURE__ */ jsxs4("span", { className: "min-w-0 flex-1", children: [
                  /* @__PURE__ */ jsxs4("span", { className: "block", children: [
                    sg.name,
                    sg.quantity > 1 ? ` \xD7 ${sg.quantity}` : ""
                  ] }),
                  /* @__PURE__ */ jsx5("span", { className: "block text-xs text-white/45", children: [sg.kind, sg.manufacturer, sg.serial_number ? `Seriennummer ${sg.serial_number}` : null, sg.detail, sg.hint].filter(Boolean).join(" \xB7 ") })
                ] }),
                done ? /* @__PURE__ */ jsx5(Badge, { tone: "good", children: "\xFCbernommen" }) : known2 && /* @__PURE__ */ jsx5(Badge, { tone: "info", children: "schon im Inventar" })
              ] }) }, sg.key);
            }) }),
            /* @__PURE__ */ jsxs4("div", { className: "flex flex-wrap items-center gap-2", children: [
              categories.length > 0 && /* @__PURE__ */ jsxs4("select", { value: categoryId, onChange: (e) => setCategoryId(e.target.value), "aria-label": "Kategorie f\xFCr die neuen Teile", className: `${inlineInput} w-auto`, children: [
                /* @__PURE__ */ jsx5("option", { value: "", children: "Kategorie: \u2013" }),
                categories.map((c) => /* @__PURE__ */ jsx5("option", { value: c.id, children: c.name }, c.id))
              ] }),
              /* @__PURE__ */ jsxs4("select", { value: locationId, onChange: (e) => setLocationId(e.target.value), "aria-label": "Standort f\xFCr die neuen Teile", className: `${inlineInput} w-auto`, children: [
                /* @__PURE__ */ jsx5("option", { value: "", children: "Standort: \u2013" }),
                locations.map((l) => /* @__PURE__ */ jsx5("option", { value: l.id, children: l.name }, l.id))
              ] }),
              projects.length > 0 && /* @__PURE__ */ jsxs4("select", { value: projectId, onChange: (e) => setProjectId(e.target.value), "aria-label": "Bauprojekt f\xFCr die neuen Teile", className: `${inlineInput} w-auto`, children: [
                /* @__PURE__ */ jsx5("option", { value: "", children: "Bauprojekt: \u2013" }),
                projects.map((pr) => /* @__PURE__ */ jsx5("option", { value: pr.id, children: pr.name }, pr.id))
              ] }),
              /* @__PURE__ */ jsx5(Button, { variant: "primary", onClick: () => void take(), disabled: open === 0 || saving, children: progress ? `\xDCbernehme \u2026 ${progress[0]} von ${progress[1]}` : open === 1 ? "1 Teil \xFCbernehmen" : `${open} Teile \xFCbernehmen` })
            ] })
          ] })
        ] })
      ]
    }
  );
}
function ImportPanel({
  categories,
  locations,
  onDone
}) {
  const [result, setResult] = useState3(null);
  const [existing, setExisting] = useState3([]);
  const [chosen, setChosen] = useState3(() => /* @__PURE__ */ new Set());
  const [taken, setTaken] = useState3(() => /* @__PURE__ */ new Set());
  const [filter, setFilter] = useState3("");
  const [locationId, setLocationId] = useState3("");
  const [error, setError] = useState3(null);
  const [progress, setProgress] = useState3(null);
  const [outcome, setOutcome] = useState3(null);
  const norm = (value) => (value ?? "").trim().toLowerCase();
  const byName = (list, name) => (name ? list.find((x) => norm(x.name) === norm(name))?.id : void 0) ?? null;
  const known = {
    names: new Set((existing ?? []).map((i) => norm(i.name))),
    serials: new Set((existing ?? []).map((i) => norm(i.serial_number)).filter(Boolean)),
    urls: new Set((existing ?? []).map((i) => norm(i.product_url)).filter(Boolean))
  };
  const isKnown = (row) => known.names.has(norm(row.name)) || !!row.serial_number && known.serials.has(norm(row.serial_number)) || !!row.product_url && known.urls.has(norm(row.product_url));
  async function readFile(file) {
    setError(null);
    setOutcome(null);
    if (file.size > MAX_IMPORT_BYTES) {
      setResult(null);
      setError(`Die Datei ist gr\xF6\xDFer als ${MAX_IMPORT_BYTES / 1024 / 1024} MB. Teile sie auf oder exportiere einen k\xFCrzeren Zeitraum.`);
      return;
    }
    try {
      const [parsed, items] = await Promise.all([
        file.arrayBuffer().then((bytes) => rowsFromCsv(decodeCsv(bytes))),
        // Die ganze Liste, nicht nur die gerade gefilterte: sonst wäre „schon im Inventar“ unvollständig.
        fetchJson("/ext/inventory/items").catch(() => null)
      ]);
      if (parsed.rows.length === 0) {
        setResult(null);
        setError("In der Datei steht nichts, was sich \xFCbernehmen l\xE4sst. Die Datei braucht eine Spalte mit dem Namen (z. B. \u201EName\u201C oder \u201EProduct Name\u201C).");
        return;
      }
      setExisting(items);
      setResult({ ...parsed, file: file.name });
      setTaken(/* @__PURE__ */ new Set());
      setFilter("");
      const seen = {
        names: new Set((items ?? []).map((i) => norm(i.name))),
        serials: new Set((items ?? []).map((i) => norm(i.serial_number)).filter(Boolean)),
        urls: new Set((items ?? []).map((i) => norm(i.product_url)).filter(Boolean))
      };
      const dupe = (row) => seen.names.has(norm(row.name)) || !!row.serial_number && seen.serials.has(norm(row.serial_number)) || !!row.product_url && seen.urls.has(norm(row.product_url));
      setChosen(new Set(items ? parsed.rows.map((r, i) => dupe(r) ? -1 : i).filter((i) => i >= 0) : []));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }
  async function take() {
    if (!result) return;
    const picked = [...result.rows.entries()].filter(([i]) => chosen.has(i) && !taken.has(i));
    setOutcome(null);
    setProgress([0, picked.length]);
    const done = await createItems(
      picked.map(([i, row]) => {
        const { category, location, ...fields } = row;
        return {
          key: i,
          label: row.name,
          body: { ...fields, category_id: byName(categories, category), location_id: byName(locations, location) ?? (locationId || null) }
        };
      }),
      (n) => setProgress([n, picked.length])
    );
    setProgress(null);
    setTaken((prev) => /* @__PURE__ */ new Set([...prev, ...done.created]));
    setChosen((prev) => new Set([...prev].filter((i) => !done.created.includes(i))));
    const count = done.created.length;
    if (count) onDone(count === 1 ? "1 Gegenstand \xFCbernommen." : `${count} Gegenst\xE4nde \xFCbernommen.`);
    if (done.failed.length) setOutcome(done);
    else setResult(null);
  }
  const toggle = (i) => setChosen((prev) => {
    const next = new Set(prev);
    if (next.has(i)) next.delete(i);
    else next.add(i);
    return next;
  });
  const sourceText = { amazon: "Amazon-Bestellverlauf", inventar: "Inventar-Export", andere: "Liste" };
  const saving = progress !== null;
  const shown = result ? [...result.rows.entries()].filter(([, row]) => !filter.trim() || norm(row.name).includes(norm(filter))) : [];
  const open = result ? [...chosen].filter((i) => !taken.has(i)).length : 0;
  const unknownNames = (list, key) => result ? [...new Set(result.rows.map((r) => r[key]).filter((n) => !!n && !byName(list, n)))] : [];
  const unknownCategories = unknownNames(categories, "category");
  const unknownLocations = unknownNames(locations, "location");
  const setAll = (on) => setChosen((prev) => {
    const next = new Set(prev);
    for (const [i] of shown) {
      if (taken.has(i)) continue;
      if (on) next.add(i);
      else next.delete(i);
    }
    return next;
  });
  return /* @__PURE__ */ jsxs4(
    Card,
    {
      title: "Bestellliste einlesen",
      description: "Eine CSV-Datei mit K\xE4ufen ins Inventar holen, z. B. den Amazon-Bestellverlauf (bei Amazon unter \u201EMeine Daten anfordern\u201C, Datei \u201ERetail.OrderHistory.1.csv\u201C) oder einen fr\xFCheren Export dieses Inventars. Die Datei bleibt in deinem Browser.",
      className: "mb-5",
      children: [
        /* @__PURE__ */ jsxs4("label", { className: `${buttonClass("secondary")} cursor-pointer focus-within:ring-2 focus-within:ring-[var(--color-accent)]`, children: [
          /* @__PURE__ */ jsx5(Icon, { name: "upload", size: 14 }),
          " CSV-Datei w\xE4hlen",
          /* @__PURE__ */ jsx5(
            "input",
            {
              type: "file",
              accept: ".csv,.tsv,.txt,text/csv",
              className: "sr-only",
              "aria-label": "CSV-Datei w\xE4hlen",
              disabled: saving,
              onChange: (e) => {
                const file = e.target.files?.[0];
                if (file) void readFile(file);
                e.target.value = "";
              }
            }
          )
        ] }),
        /* @__PURE__ */ jsx5("p", { className: "sr-only", role: "status", children: progressText(progress) }),
        error && /* @__PURE__ */ jsx5("div", { className: "mt-3", children: /* @__PURE__ */ jsx5(Notice, { text: `Fehler: ${error}`, onClose: () => setError(null) }) }),
        result && /* @__PURE__ */ jsxs4("div", { className: "mt-4", "data-testid": "import-result", children: [
          /* @__PURE__ */ jsxs4("p", { className: "mb-2 text-sm text-white/60", children: [
            "Erkannt: ",
            sourceText[result.source],
            " \u201E",
            result.file,
            "\u201C, ",
            result.rows.length === 1 ? "1 Eintrag" : `${result.rows.length} Eintr\xE4ge`,
            result.skipped > 0 && ` (${result.skipped} ohne Namen oder storniert \xFCbersprungen)`,
            "."
          ] }),
          [
            ...result.notes,
            ...unknownCategories.length ? [`Diese Kategorien gibt es hier nicht, die Gegenst\xE4nde bleiben ohne Kategorie: ${unknownCategories.slice(0, 5).join(", ")}${unknownCategories.length > 5 ? " \u2026" : ""}.`] : [],
            ...unknownLocations.length ? [`Diese Standorte gibt es hier nicht, es gilt der Standort unten: ${unknownLocations.slice(0, 5).join(", ")}${unknownLocations.length > 5 ? " \u2026" : ""}.`] : []
          ].map((note) => /* @__PURE__ */ jsx5("p", { className: "mb-2 text-sm text-amber-200", children: note }, note)),
          !existing && /* @__PURE__ */ jsx5("p", { className: "mb-2 text-sm text-amber-200", children: "Konnte nicht pr\xFCfen, was schon im Inventar steht. Darum ist nichts vorab angekreuzt \u2013 sieh die Liste durch." }),
          outcome && /* @__PURE__ */ jsx5(FailureList, { outcome, what: ["Gegenstand", "Gegenst\xE4nde"], onClose: () => setOutcome(null) }),
          /* @__PURE__ */ jsxs4("div", { className: "mb-2 flex flex-wrap items-center gap-2", children: [
            result.rows.length > 8 && /* @__PURE__ */ jsx5(
              "input",
              {
                type: "search",
                value: filter,
                onChange: (e) => setFilter(e.target.value),
                placeholder: "In der Liste suchen \u2026",
                "aria-label": "In der eingelesenen Liste suchen",
                className: `${inlineInput} min-w-[12rem] flex-1`
              }
            ),
            /* @__PURE__ */ jsx5(Button, { small: true, onClick: () => setAll(true), disabled: saving, children: filter.trim() ? "Alle gefundenen ankreuzen" : "Alle ankreuzen" }),
            /* @__PURE__ */ jsx5(Button, { small: true, onClick: () => setAll(false), disabled: saving, children: filter.trim() ? "Gefundene abw\xE4hlen" : "Keine" })
          ] }),
          /* @__PURE__ */ jsxs4("ul", { className: "mb-3 max-h-96 divide-y divide-white/[0.06] overflow-y-auto", children: [
            shown.slice(0, 500).map(([i, row]) => {
              const dupe = isKnown(row);
              const done = taken.has(i);
              return /* @__PURE__ */ jsx5("li", { children: /* @__PURE__ */ jsxs4("label", { className: "flex cursor-pointer flex-wrap items-start gap-2 py-2 text-sm", children: [
                /* @__PURE__ */ jsx5(
                  "input",
                  {
                    type: "checkbox",
                    checked: done || chosen.has(i),
                    disabled: done || saving,
                    onChange: () => toggle(i),
                    "aria-label": `${row.name} \xFCbernehmen${dupe ? " (schon im Inventar?)" : ""}${done ? " (\xFCbernommen)" : ""}`,
                    className: "mt-1"
                  }
                ),
                /* @__PURE__ */ jsxs4("span", { className: "min-w-0 flex-1", children: [
                  /* @__PURE__ */ jsxs4("span", { className: "block", children: [
                    row.name,
                    row.quantity > 1 ? ` \xD7 ${row.quantity}` : ""
                  ] }),
                  /* @__PURE__ */ jsx5("span", { className: "block text-xs text-white/45", children: [formatDate(row.purchase_date), row.purchase_price_cents != null ? formatPrice(row.purchase_price_cents) : null].filter((v) => v && v !== "\u2013").join(" \xB7 ") })
                ] }),
                done ? /* @__PURE__ */ jsx5(Badge, { tone: "good", children: "\xFCbernommen" }) : dupe && /* @__PURE__ */ jsx5(Badge, { tone: "info", children: "schon im Inventar?" })
              ] }) }, i);
            }),
            shown.length > 500 && /* @__PURE__ */ jsxs4("li", { className: "py-2 text-xs text-white/45", children: [
              "Angezeigt werden die ersten 500 von ",
              shown.length,
              ". Die Kn\xF6pfe oben gelten f\xFCr alle ",
              shown.length,
              "; mit der Suche findest du einzelne."
            ] })
          ] }),
          /* @__PURE__ */ jsxs4("div", { className: "flex flex-wrap items-center gap-2", children: [
            /* @__PURE__ */ jsxs4("select", { value: locationId, onChange: (e) => setLocationId(e.target.value), "aria-label": "Standort f\xFCr die neuen Gegenst\xE4nde", className: `${inlineInput} w-auto`, children: [
              /* @__PURE__ */ jsx5("option", { value: "", children: "Standort: \u2013" }),
              locations.map((l) => /* @__PURE__ */ jsx5("option", { value: l.id, children: l.name }, l.id))
            ] }),
            /* @__PURE__ */ jsx5(Button, { variant: "primary", onClick: () => void take(), disabled: open === 0 || saving, children: progress ? `\xDCbernehme \u2026 ${progress[0]} von ${progress[1]}` : open === 1 ? "1 Gegenstand \xFCbernehmen" : `${open} Gegenst\xE4nde \xFCbernehmen` })
          ] })
        ] })
      ]
    }
  );
}
function ItemDocuments({ item }) {
  const canRead = deck().hasPermission("documents.read");
  const canWrite = deck().hasPermission("documents.write");
  const [docs, setDocs] = useState3(null);
  const [hidden, setHidden] = useState3(false);
  const [error, setError] = useState3(null);
  const [actionError, setActionError] = useState3(null);
  const [busy, setBusy] = useState3(false);
  const load = useCallback(async () => {
    setError(null);
    try {
      const res = await authedFetch(`/ext/documents/documents?item_id=${encodeURIComponent(item.id)}`);
      if (res.status === 404 || res.status === 403) {
        setHidden(true);
        return;
      }
      const body = await res.json().catch(() => null);
      if (!res.ok) throw new Error(errorFromBody(body ?? {}, res.status));
      setDocs(body);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [item.id]);
  useEffect3(() => {
    if (canRead) void load();
  }, [canRead, load]);
  async function upload(file) {
    setBusy(true);
    setActionError(null);
    try {
      const res = await authedFetch(
        `/ext/documents/documents?filename=${encodeURIComponent(file.name)}&item_id=${encodeURIComponent(item.id)}`,
        { method: "POST", body: file, headers: { "Content-Type": file.type || "application/octet-stream" } }
      );
      if (!res.ok) throw new Error(errorFromBody(await res.json().catch(() => ({})), res.status));
      await load();
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  async function open(doc) {
    setActionError(null);
    try {
      const res = await authedFetch(`/ext/documents/documents/${doc.id}/download`);
      if (!res.ok) throw new Error(errorFromBody(await res.json().catch(() => ({})), res.status));
      const url = URL.createObjectURL(await res.blob());
      const a = window.document.createElement("a");
      a.href = url;
      a.download = doc.original_filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setActionError(errorMessage(err));
    }
  }
  async function unlink(doc) {
    setActionError(null);
    try {
      const res = await authedFetch(`/ext/documents/documents/${doc.id}/items/${encodeURIComponent(item.id)}`, { method: "DELETE" });
      if (!res.ok) throw new Error(errorFromBody(await res.json().catch(() => ({})), res.status));
      await load();
    } catch (err) {
      setActionError(errorMessage(err));
    }
  }
  if (!canRead || hidden) return null;
  return /* @__PURE__ */ jsxs4("div", { className: "mb-4", "data-testid": `documents-${item.id}`, children: [
    /* @__PURE__ */ jsx5("p", { className: "mb-2 text-xs text-white/45", children: "Dokumente (Rechnung, Handbuch \u2026)" }),
    error && /* @__PURE__ */ jsx5(LoadError, { compact: true, what: "Die Liste der Dokumente", error, onRetry: () => void load() }),
    actionError && /* @__PURE__ */ jsx5(Notice, { text: `Fehler: ${actionError}`, onClose: () => setActionError(null) }),
    docs && docs.length === 0 && /* @__PURE__ */ jsx5("p", { className: "mb-2 text-xs text-white/45", children: "Noch keine. Lade die Rechnung oder das Handbuch hier hoch." }),
    docs && docs.length > 0 && /* @__PURE__ */ jsx5("ul", { className: "mb-2 space-y-1", children: docs.map((doc) => /* @__PURE__ */ jsxs4("li", { className: "flex flex-wrap items-center gap-2", children: [
      /* @__PURE__ */ jsxs4("span", { className: "flex min-w-0 basis-full items-center gap-2 sm:flex-1 sm:basis-0", children: [
        /* @__PURE__ */ jsx5(Icon, { name: "file", size: 13, className: "flex-none text-[var(--color-accent)]" }),
        /* @__PURE__ */ jsx5("span", { className: "truncate", children: doc.original_filename })
      ] }),
      /* @__PURE__ */ jsxs4(Button, { small: true, onClick: () => void open(doc), ariaLabel: `${doc.original_filename} herunterladen`, children: [
        /* @__PURE__ */ jsx5(Icon, { name: "download", size: 12 }),
        " Herunterladen"
      ] }),
      canWrite && /* @__PURE__ */ jsx5(
        Button,
        {
          small: true,
          variant: "ghost",
          onClick: () => void unlink(doc),
          title: "Das Dokument selbst bleibt erhalten.",
          ariaLabel: `Zuordnung von ${doc.original_filename} zu diesem Gegenstand l\xF6sen`,
          children: "Zuordnung l\xF6sen"
        }
      )
    ] }, doc.id)) }),
    canWrite && /* @__PURE__ */ jsxs4("label", { className: `${buttonClass("secondary", true)} cursor-pointer focus-within:ring-2 focus-within:ring-[var(--color-accent)]`, children: [
      /* @__PURE__ */ jsx5(Icon, { name: "upload", size: 12 }),
      " ",
      busy ? "L\xE4dt hoch \u2026" : "Dokument hochladen",
      /* @__PURE__ */ jsx5(
        "input",
        {
          type: "file",
          accept: "application/pdf,image/png,image/jpeg,image/webp",
          className: "sr-only",
          disabled: busy,
          "aria-label": `Dokument zu ${item.name} hochladen`,
          onChange: (e) => {
            const file = e.target.files?.[0];
            if (file) void upload(file);
            e.target.value = "";
          }
        }
      )
    ] })
  ] });
}
function InventoryPage() {
  const [items, setItems] = useState3(null);
  const [categories, setCategories] = useState3([]);
  const [locations, setLocations] = useState3([]);
  const [projects, setProjects] = useState3([]);
  const [hosts, setHosts] = useState3(null);
  const [projectsError, setProjectsError] = useState3(null);
  const [projectFilter, setProjectFilter] = useState3("");
  const [showProjects, setShowProjects] = useState3(false);
  const [showRacks, setShowRacks] = useState3(false);
  const [showScan, setShowScan] = useState3(false);
  const [showImport, setShowImport] = useState3(false);
  const [info, setInfo] = useState3(null);
  const [error, setError] = useState3(null);
  const [message, setMessage] = useState3(null);
  const [search, setSearch] = useState3("");
  const [categoryFilter, setCategoryFilter] = useState3("");
  const [locationFilter, setLocationFilter] = useState3("");
  const [warrantyFilter, setWarrantyFilter] = useState3("");
  const [showForm, setShowForm] = useState3(false);
  const [showTaxonomy, setShowTaxonomy] = useState3(false);
  const [editingId, setEditingId] = useState3(null);
  const [form, setForm] = useState3(EMPTY_FORM);
  const [techOpen, setTechOpen] = useState3(false);
  const [expanded, setExpanded] = useState3(null);
  const [uploading, setUploading] = useState3(null);
  const load = useCallback(() => {
    setError(null);
    const params = search ? `?q=${encodeURIComponent(search)}` : "";
    Promise.all([
      // Alle drei mit Statusprüfung: sonst landete bei einem Fehler die Fehlerantwort als „Liste“ in der Seite.
      fetchJson(`/ext/inventory/items${params}`),
      fetchJson("/ext/inventory/categories"),
      fetchJson("/ext/inventory/locations")
    ]).then(([itemsData, categoriesData, locationsData]) => {
      setItems(itemsData);
      setCategories(categoriesData);
      setLocations(locationsData);
    }).catch((err) => setError(errorMessage(err)));
    fetchJson("/ext/inventory/projects").then((data) => {
      setProjects(data);
      setProjectsError(null);
    }).catch((err) => setProjectsError(errorMessage(err)));
  }, [search]);
  useEffect3(() => {
    load();
  }, [load]);
  useEffect3(() => {
    if (projectFilter && !projects.some((p) => p.id === projectFilter)) setProjectFilter("");
  }, [projects, projectFilter]);
  useEffect3(() => {
    fetchJson("/hosts").then(setHosts).catch(() => setHosts(null));
  }, []);
  function startCreate() {
    setForm(EMPTY_FORM);
    setTechOpen(false);
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
      notes: item.notes ?? "",
      manufacturer: item.manufacturer ?? "",
      model: item.model ?? "",
      serial_number: item.serial_number ?? "",
      product_url: item.product_url ?? "",
      host_id: item.host_id ?? "",
      project_id: item.project_id ?? ""
    });
    setTechOpen(Boolean(item.manufacturer || item.model || item.serial_number || item.product_url || item.host_id || item.project_id));
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
      notes: form.notes || null,
      manufacturer: form.manufacturer.trim() || null,
      model: form.model.trim() || null,
      serial_number: form.serial_number.trim() || null,
      product_url: form.product_url.trim() || null,
      host_id: form.host_id || null,
      project_id: form.project_id || null
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
    const res = await authedFetch(`/ext/inventory/items/${item.id}`, { method: "DELETE" });
    if (!res.ok) await showError(res);
    load();
  }
  async function showError(res) {
    const body = await res.json().catch(() => ({}));
    setMessage(`Fehler: ${errorFromBody(body, res.status)}`);
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
    const res = await authedFetch(`/ext/inventory/items/${item.id}/images/${image.id}`, { method: "DELETE" });
    if (!res.ok) await showError(res);
    load();
  }
  const categoryName = (id) => categories.find((c) => c.id === id)?.name ?? "\u2013";
  const locationName = (id) => locations.find((l) => l.id === id)?.name ?? "\u2013";
  const projectName = (id) => projects.find((p) => p.id === id)?.name ?? null;
  const hostName = (id) => {
    if (!id) return null;
    const host = hosts?.find((h) => h.id === id);
    return host ? host.display_name || host.name || id : "unbekannter Server";
  };
  const visible = (items ?? []).filter((i) => (!categoryFilter || i.category_id === categoryFilter) && (!locationFilter || i.location_id === locationFilter) && (!projectFilter || i.project_id === projectFilter) && (!warrantyFilter || warrantyState(i.warranty_until) === warrantyFilter));
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
  if (!items && !error) return /* @__PURE__ */ jsx5(Loading, {});
  if (error && !items) return /* @__PURE__ */ jsx5(Page, { title: "Inventar", children: /* @__PURE__ */ jsx5(LoadError, { what: "Das Inventar", error, onRetry: load }) });
  const input = (key, props = {}) => /* @__PURE__ */ jsx5("input", { value: form[key], onChange: (e) => setForm({ ...form, [key]: e.target.value }), className: inputClass, ...props });
  return /* @__PURE__ */ jsxs4(
    Page,
    {
      title: "Inventar",
      description: "Ger\xE4te und Gegenst\xE4nde mit Standort, Kaufpreis und Garantie.",
      actions: /* @__PURE__ */ jsxs4(Fragment2, { children: [
        /* @__PURE__ */ jsxs4(Button, { onClick: () => setShowProjects((v) => !v), pressed: showProjects, children: [
          /* @__PURE__ */ jsx5(Icon, { name: "package", size: 14 }),
          " Bauprojekte"
        ] }),
        /* @__PURE__ */ jsxs4(Button, { onClick: () => setShowRacks((v) => !v), pressed: showRacks, children: [
          /* @__PURE__ */ jsx5(Icon, { name: "folder", size: 14 }),
          " Racks"
        ] }),
        /* @__PURE__ */ jsx5(
          MoreMenu,
          {
            testId: "inventory-more",
            title: "Weitere Funktionen des Inventars",
            align: "right",
            items: [
              // Auslesen führt einen Befehl auf dem Server aus: nur mit dem Recht dazu (sonst lehnt der Server ab).
              ...hosts && deck().hasPermission("hosts.execute") ? [{ id: "scan", label: showScan ? "Teile auslesen ausblenden" : "Teile eines Servers auslesen", run: () => setShowScan((v) => !v) }] : [],
              { id: "import", label: showImport ? "Bestellliste ausblenden" : "Bestellliste einlesen (CSV)", run: () => setShowImport((v) => !v) },
              { id: "taxonomy", label: showTaxonomy ? "Kategorien & Standorte ausblenden" : "Kategorien & Standorte", run: () => setShowTaxonomy((v) => !v) },
              { id: "export", label: "Als CSV exportieren", run: exportCsv, disabled: visible.length === 0 }
            ]
          }
        ),
        /* @__PURE__ */ jsxs4(Button, { variant: "primary", onClick: startCreate, children: [
          /* @__PURE__ */ jsx5(Icon, { name: "plus", size: 14 }),
          " Neuer Gegenstand"
        ] })
      ] }),
      children: [
        showProjects && projectsError && /* @__PURE__ */ jsx5(LoadError, { what: "Die Liste der Bauprojekte", error: projectsError, onRetry: load }),
        showProjects && !projectsError && /* @__PURE__ */ jsx5(
          ProjectsPanel,
          {
            projects,
            onChanged: load,
            activeId: projectFilter,
            onShowItems: (id) => setProjectFilter((current) => current === id ? "" : id)
          }
        ),
        showScan && hosts && deck().hasPermission("hosts.execute") && /* @__PURE__ */ jsx5(
          ScanPanel,
          {
            hosts,
            categories,
            locations,
            projects,
            onDone: (text) => {
              setInfo(text);
              load();
            }
          }
        ),
        showImport && /* @__PURE__ */ jsx5(ImportPanel, { categories, locations, onDone: (text) => {
          setInfo(text);
          load();
        } }),
        showRacks && /* @__PURE__ */ jsx5(RacksPanel, { items: items ?? [], onChanged: load }),
        showTaxonomy && /* @__PURE__ */ jsx5(TaxonomyPanel, { categories, locations, onChanged: load }),
        error && /* @__PURE__ */ jsx5(LoadError, { what: "Das Inventar", error, onRetry: load, hint: "Angezeigt wird der letzte geladene Stand." }),
        info && /* @__PURE__ */ jsx5(Notice, { kind: "ok", text: info, onClose: () => setInfo(null) }),
        message && /* @__PURE__ */ jsx5(Notice, { text: message, onClose: () => setMessage(null) }),
        items && items.length > 0 && /* @__PURE__ */ jsxs4("div", { className: "mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4", "data-testid": "inventory-summary", children: [
          /* @__PURE__ */ jsx5(Stat, { label: "Gegenst\xE4nde", value: visible.length, hint: `${visible.length} Gegenstand/Gegenst\xE4nde angezeigt` }),
          /* @__PURE__ */ jsx5(Stat, { label: "Gesamtwert", value: (totalCents / 100).toLocaleString("de-DE", { style: "currency", currency: "EUR" }) }),
          /* @__PURE__ */ jsx5(Stat, { label: "Garantie endet bald", value: soonCount, hint: "in den n\xE4chsten 90 Tagen", tone: soonCount ? "warn" : void 0 }),
          /* @__PURE__ */ jsx5(Stat, { label: "Garantie abgelaufen", value: expiredCount, hint: `${expiredCount} abgelaufen`, tone: expiredCount ? "bad" : void 0 })
        ] }),
        showForm && /* @__PURE__ */ jsx5("form", { onSubmit: (e) => void submitForm(e), className: "mb-5", children: /* @__PURE__ */ jsxs4(
          Card,
          {
            title: editingId ? "Gegenstand bearbeiten" : "Neuer Gegenstand",
            actions: /* @__PURE__ */ jsx5(Button, { variant: "ghost", ariaLabel: "Formular schlie\xDFen", onClick: () => setShowForm(false), children: /* @__PURE__ */ jsx5(Icon, { name: "x", size: 14 }) }),
            children: [
              /* @__PURE__ */ jsxs4("div", { className: "grid gap-4 sm:grid-cols-2 lg:grid-cols-4", children: [
                /* @__PURE__ */ jsx5(Field, { label: "Name", className: "sm:col-span-2", children: input("name", { required: true }) }),
                /* @__PURE__ */ jsx5(Field, { label: "Menge", children: input("quantity", { type: "number", min: "0", max: "100000" }) }),
                /* @__PURE__ */ jsx5(Field, { label: "Preis (\u20AC)", children: input("purchase_price", { type: "number", step: "0.01", min: "0", max: "10000000" }) }),
                /* @__PURE__ */ jsx5(Field, { label: "Kategorie", children: /* @__PURE__ */ jsxs4("select", { value: form.category_id, onChange: (e) => setForm({ ...form, category_id: e.target.value }), className: inputClass, children: [
                  /* @__PURE__ */ jsx5("option", { value: "", children: "\u2013" }),
                  categories.map((c) => /* @__PURE__ */ jsx5("option", { value: c.id, children: c.name }, c.id))
                ] }) }),
                /* @__PURE__ */ jsx5(Field, { label: "Standort", children: /* @__PURE__ */ jsxs4("select", { value: form.location_id, onChange: (e) => setForm({ ...form, location_id: e.target.value }), className: inputClass, children: [
                  /* @__PURE__ */ jsx5("option", { value: "", children: "\u2013" }),
                  locations.map((l) => /* @__PURE__ */ jsx5("option", { value: l.id, children: l.name }, l.id))
                ] }) }),
                /* @__PURE__ */ jsx5(Field, { label: "Kaufdatum", children: input("purchase_date", { type: "date" }) }),
                /* @__PURE__ */ jsx5(Field, { label: "Garantie bis", children: input("warranty_until", { type: "date" }) }),
                /* @__PURE__ */ jsx5(Field, { label: "Beschreibung", className: "sm:col-span-2", children: /* @__PURE__ */ jsx5("textarea", { value: form.description, onChange: (e) => setForm({ ...form, description: e.target.value }), className: inputClass, rows: 3 }) }),
                /* @__PURE__ */ jsx5(Field, { label: "Notizen", className: "sm:col-span-2", children: /* @__PURE__ */ jsx5("textarea", { value: form.notes, onChange: (e) => setForm({ ...form, notes: e.target.value }), className: inputClass, rows: 3 }) })
              ] }),
              /* @__PURE__ */ jsxs4(
                "details",
                {
                  className: "mt-4 rounded-lg border border-white/[0.06] p-3",
                  open: techOpen,
                  onToggle: (e) => setTechOpen(e.currentTarget.open),
                  "data-testid": "tech-fields",
                  children: [
                    /* @__PURE__ */ jsx5("summary", { className: "cursor-pointer text-sm text-white/70", children: "Technik: Hersteller, Modell, Server, Bauprojekt" }),
                    /* @__PURE__ */ jsxs4("div", { className: "mt-3 grid gap-4 sm:grid-cols-2 lg:grid-cols-4", children: [
                      /* @__PURE__ */ jsx5(Field, { label: "Hersteller", children: input("manufacturer", { maxLength: 200 }) }),
                      /* @__PURE__ */ jsx5(Field, { label: "Modell", children: input("model", { maxLength: 200 }) }),
                      /* @__PURE__ */ jsx5(Field, { label: "Seriennummer", children: input("serial_number", { maxLength: 200 }) }),
                      /* @__PURE__ */ jsx5(Field, { label: "Produktlink", children: input("product_url", { type: "url", placeholder: "https://\u2026", maxLength: 2e3 }) }),
                      hosts && /* @__PURE__ */ jsx5(Field, { label: "Geh\xF6rt zu Server", children: /* @__PURE__ */ jsxs4("select", { value: form.host_id, onChange: (e) => setForm({ ...form, host_id: e.target.value }), className: inputClass, children: [
                        /* @__PURE__ */ jsx5("option", { value: "", children: "\u2013" }),
                        form.host_id && !hosts.some((h) => h.id === form.host_id) && /* @__PURE__ */ jsx5("option", { value: form.host_id, children: form.host_id }),
                        hosts.map((h) => /* @__PURE__ */ jsx5("option", { value: h.id, children: h.display_name || h.name }, h.id))
                      ] }) }),
                      /* @__PURE__ */ jsx5(Field, { label: "Bauprojekt", children: /* @__PURE__ */ jsxs4("select", { value: form.project_id, onChange: (e) => setForm({ ...form, project_id: e.target.value }), className: inputClass, children: [
                        /* @__PURE__ */ jsx5("option", { value: "", children: "\u2013" }),
                        projects.map((p) => /* @__PURE__ */ jsx5("option", { value: p.id, children: p.name }, p.id))
                      ] }) })
                    ] })
                  ]
                }
              ),
              /* @__PURE__ */ jsxs4("div", { className: "mt-4 flex justify-end gap-2", children: [
                /* @__PURE__ */ jsx5(Button, { variant: "ghost", onClick: () => setShowForm(false), children: "Abbrechen" }),
                /* @__PURE__ */ jsx5(Button, { type: "submit", variant: "primary", children: editingId ? "Speichern" : "Anlegen" })
              ] })
            ]
          }
        ) }),
        items && items.length === 0 ? /* @__PURE__ */ jsx5(
          EmptyState,
          {
            icon: "package",
            title: "Noch keine Gegenst\xE4nde erfasst.",
            text: "Erfasse Ger\xE4te, Werkzeug oder Ersatzteile mit Standort, Kaufpreis und Garantie \u2013 dann warnt das Dashboard rechtzeitig, bevor eine Garantie abl\xE4uft.",
            action: !showForm && /* @__PURE__ */ jsxs4(Button, { variant: "primary", onClick: startCreate, children: [
              /* @__PURE__ */ jsx5(Icon, { name: "plus", size: 14 }),
              " Ersten Gegenstand anlegen"
            ] })
          }
        ) : /* @__PURE__ */ jsxs4(Fragment2, { children: [
          /* @__PURE__ */ jsxs4("div", { className: "mb-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-5", children: [
            /* @__PURE__ */ jsx5(SearchInput, { value: search, onChange: setSearch, placeholder: "Suchen \u2026", label: "Suchen" }),
            /* @__PURE__ */ jsxs4("select", { value: categoryFilter, onChange: (e) => setCategoryFilter(e.target.value), "aria-label": "Nach Kategorie filtern", className: `${inputClass} w-auto`, children: [
              /* @__PURE__ */ jsx5("option", { value: "", children: "Alle Kategorien" }),
              categories.map((c) => /* @__PURE__ */ jsx5("option", { value: c.id, children: c.name }, c.id))
            ] }),
            /* @__PURE__ */ jsxs4("select", { value: locationFilter, onChange: (e) => setLocationFilter(e.target.value), "aria-label": "Nach Standort filtern", className: `${inputClass} w-auto`, children: [
              /* @__PURE__ */ jsx5("option", { value: "", children: "Alle Standorte" }),
              locations.map((l) => /* @__PURE__ */ jsx5("option", { value: l.id, children: l.name }, l.id))
            ] }),
            projects.length > 0 && /* @__PURE__ */ jsxs4("select", { value: projectFilter, onChange: (e) => setProjectFilter(e.target.value), "aria-label": "Nach Bauprojekt filtern", className: `${inputClass} w-auto`, children: [
              /* @__PURE__ */ jsx5("option", { value: "", children: "Alle Bauprojekte" }),
              projects.map((p) => /* @__PURE__ */ jsx5("option", { value: p.id, children: p.name }, p.id))
            ] }),
            /* @__PURE__ */ jsxs4("select", { value: warrantyFilter, onChange: (e) => setWarrantyFilter(e.target.value), "aria-label": "Nach Garantie filtern", className: `${inputClass} w-auto`, children: [
              /* @__PURE__ */ jsx5("option", { value: "", children: "Garantie: alle" }),
              /* @__PURE__ */ jsx5("option", { value: "active", children: "l\xE4uft noch" }),
              /* @__PURE__ */ jsx5("option", { value: "soon", children: "l\xE4uft in 90 Tagen ab" }),
              /* @__PURE__ */ jsx5("option", { value: "expired", children: "abgelaufen" }),
              /* @__PURE__ */ jsx5("option", { value: "none", children: "keine Angabe" })
            ] })
          ] }),
          /* @__PURE__ */ jsxs4("ul", { className: "panel divide-y divide-white/[0.06]", children: [
            visible.length === 0 && /* @__PURE__ */ jsx5("li", { className: "px-5 py-8 text-center text-sm text-white/45", children: "Nichts gefunden \u2013 Filter anpassen." }),
            visible.map((item) => {
              const isExpanded = expanded === item.id;
              const ws = warrantyState(item.warranty_until);
              return /* @__PURE__ */ jsxs4("li", { className: "px-4 py-3", children: [
                /* @__PURE__ */ jsxs4("div", { className: "flex items-center gap-3", children: [
                  /* @__PURE__ */ jsx5(
                    "button",
                    {
                      type: "button",
                      onClick: () => setExpanded(isExpanded ? null : item.id),
                      "aria-label": isExpanded ? "Details ausblenden" : "Details anzeigen",
                      "aria-expanded": isExpanded,
                      className: "rounded p-1 text-white/40 hover:bg-white/[0.06] hover:text-white",
                      children: /* @__PURE__ */ jsx5(Icon, { name: isExpanded ? "chevron-down" : "chevron-right", size: 15 })
                    }
                  ),
                  item.images[0] ? /* @__PURE__ */ jsx5(AuthImage, { src: item.images[0].url, alt: "", className: "h-11 w-11 flex-none rounded-lg object-cover" }) : /* @__PURE__ */ jsx5("span", { className: "grid h-11 w-11 flex-none place-items-center rounded-lg bg-white/[0.05] text-white/35", children: /* @__PURE__ */ jsx5(Icon, { name: "package", size: 18 }) }),
                  /* @__PURE__ */ jsxs4("div", { className: "min-w-0 flex-1", children: [
                    /* @__PURE__ */ jsx5("p", { className: "truncate text-sm font-medium", children: item.name }),
                    /* @__PURE__ */ jsxs4("p", { className: "truncate text-xs text-white/50", children: [
                      [item.manufacturer, item.model].filter(Boolean).join(" ") || categoryName(item.category_id),
                      " \xB7 ",
                      locationName(item.location_id),
                      " \xB7 Menge ",
                      item.quantity,
                      hostName(item.host_id) && /* @__PURE__ */ jsxs4(Fragment2, { children: [
                        " \xB7 ",
                        hostName(item.host_id)
                      ] })
                    ] })
                  ] }),
                  /* @__PURE__ */ jsxs4("div", { className: "hidden flex-none text-right sm:block", children: [
                    /* @__PURE__ */ jsx5("p", { className: "text-sm tabular-nums", children: formatPrice(item.purchase_price_cents) }),
                    item.warranty_until && /* @__PURE__ */ jsx5(Badge, { tone: WARRANTY_BADGE[ws].tone, children: WARRANTY_BADGE[ws].label(item.warranty_until) })
                  ] }),
                  /* @__PURE__ */ jsxs4("div", { className: "flex flex-none items-center gap-1", children: [
                    /* @__PURE__ */ jsxs4(Button, { small: true, onClick: () => startEdit(item), title: "Bearbeiten", children: [
                      /* @__PURE__ */ jsx5(Icon, { name: "edit", size: 13 }),
                      /* @__PURE__ */ jsx5("span", { className: "sr-only sm:not-sr-only", children: " Bearbeiten" })
                    ] }),
                    /* @__PURE__ */ jsxs4(Button, { small: true, variant: "danger", onClick: () => void removeItem(item), title: "L\xF6schen", children: [
                      /* @__PURE__ */ jsx5(Icon, { name: "trash", size: 13 }),
                      /* @__PURE__ */ jsx5("span", { className: "sr-only sm:not-sr-only", children: " L\xF6schen" })
                    ] })
                  ] })
                ] }),
                isExpanded && /* @__PURE__ */ jsxs4("div", { className: "ml-10 mt-3 rounded-lg border border-white/[0.06] bg-black/15 p-4 text-sm", children: [
                  item.description && /* @__PURE__ */ jsx5("p", { className: "mb-3 text-white/80", children: item.description }),
                  /* @__PURE__ */ jsxs4("dl", { className: "mb-4 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4", children: [
                    /* @__PURE__ */ jsxs4("div", { children: [
                      /* @__PURE__ */ jsx5("dt", { className: "text-xs text-white/45", children: "Kaufdatum" }),
                      /* @__PURE__ */ jsx5("dd", { children: formatDate(item.purchase_date) })
                    ] }),
                    /* @__PURE__ */ jsxs4("div", { children: [
                      /* @__PURE__ */ jsx5("dt", { className: "text-xs text-white/45", children: "Preis" }),
                      /* @__PURE__ */ jsx5("dd", { children: formatPrice(item.purchase_price_cents) })
                    ] }),
                    /* @__PURE__ */ jsxs4("div", { children: [
                      /* @__PURE__ */ jsx5("dt", { className: "text-xs text-white/45", children: "Garantie bis" }),
                      /* @__PURE__ */ jsx5("dd", { children: formatDate(item.warranty_until) })
                    ] }),
                    /* @__PURE__ */ jsxs4("div", { children: [
                      /* @__PURE__ */ jsx5("dt", { className: "text-xs text-white/45", children: "Notizen" }),
                      /* @__PURE__ */ jsx5("dd", { children: item.notes ?? "\u2013" })
                    ] }),
                    item.manufacturer && /* @__PURE__ */ jsxs4("div", { children: [
                      /* @__PURE__ */ jsx5("dt", { className: "text-xs text-white/45", children: "Hersteller" }),
                      /* @__PURE__ */ jsx5("dd", { children: item.manufacturer })
                    ] }),
                    item.model && /* @__PURE__ */ jsxs4("div", { children: [
                      /* @__PURE__ */ jsx5("dt", { className: "text-xs text-white/45", children: "Modell" }),
                      /* @__PURE__ */ jsx5("dd", { children: item.model })
                    ] }),
                    item.serial_number && /* @__PURE__ */ jsxs4("div", { children: [
                      /* @__PURE__ */ jsx5("dt", { className: "text-xs text-white/45", children: "Seriennummer" }),
                      /* @__PURE__ */ jsx5("dd", { className: "font-mono text-xs", children: item.serial_number })
                    ] }),
                    hostName(item.host_id) && /* @__PURE__ */ jsxs4("div", { children: [
                      /* @__PURE__ */ jsx5("dt", { className: "text-xs text-white/45", children: "Server" }),
                      /* @__PURE__ */ jsx5("dd", { children: hostName(item.host_id) })
                    ] }),
                    projectName(item.project_id) && /* @__PURE__ */ jsxs4("div", { children: [
                      /* @__PURE__ */ jsx5("dt", { className: "text-xs text-white/45", children: "Bauprojekt" }),
                      /* @__PURE__ */ jsx5("dd", { children: projectName(item.project_id) })
                    ] }),
                    item.rack_id && /* @__PURE__ */ jsxs4("div", { children: [
                      /* @__PURE__ */ jsx5("dt", { className: "text-xs text-white/45", children: "Im Rack" }),
                      /* @__PURE__ */ jsx5("dd", { children: item.rack_position == null ? "ohne festen Platz" : (item.rack_units ?? 1) > 1 ? `HE ${item.rack_position}\u2013${item.rack_position + (item.rack_units ?? 1) - 1}` : `HE ${item.rack_position}` })
                    ] })
                  ] }),
                  /* @__PURE__ */ jsxs4("div", { className: "mb-4 flex flex-wrap items-center gap-2 text-xs", "data-testid": `links-${item.id}`, children: [
                    item.product_url && /* @__PURE__ */ jsx5("a", { href: item.product_url, target: "_blank", rel: "noopener noreferrer", className: "accent-soft rounded-lg px-2.5 py-1 hover:brightness-125", children: "Zum Produkt" }),
                    /* @__PURE__ */ jsx5("span", { className: "text-white/45", children: "Preise vergleichen:" }),
                    priceLinks(item).map((link) => /* @__PURE__ */ jsx5("a", { href: link.href, target: "_blank", rel: "noopener noreferrer", className: "rounded-lg border border-white/10 bg-white/[0.06] px-2.5 py-1 hover:bg-white/[0.12]", children: link.label }, link.label))
                  ] }),
                  /* @__PURE__ */ jsx5("p", { className: "mb-2 text-xs text-white/45", children: "Fotos" }),
                  /* @__PURE__ */ jsxs4("div", { className: "flex flex-wrap items-center gap-2", children: [
                    item.images.map((img) => /* @__PURE__ */ jsxs4("div", { className: "group relative", children: [
                      /* @__PURE__ */ jsx5(AuthImage, { src: img.url, alt: "", className: "h-20 w-20 rounded-lg object-cover" }),
                      /* @__PURE__ */ jsx5(
                        "button",
                        {
                          type: "button",
                          "aria-label": "Foto entfernen",
                          onClick: () => void removeImage(item, img),
                          className: "absolute right-1 top-1 rounded-full bg-black/70 p-1 text-white opacity-0 transition group-hover:opacity-100",
                          children: /* @__PURE__ */ jsx5(Icon, { name: "x", size: 11 })
                        }
                      )
                    ] }, img.id)),
                    /* @__PURE__ */ jsxs4("label", { className: "flex h-20 w-20 cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-white/20 text-[11px] text-white/50 hover:border-white/40 hover:text-white", children: [
                      uploading === item.id ? "L\xE4dt \u2026" : /* @__PURE__ */ jsxs4(Fragment2, { children: [
                        /* @__PURE__ */ jsx5(Icon, { name: "upload", size: 16 }),
                        " Foto"
                      ] }),
                      /* @__PURE__ */ jsx5(
                        "input",
                        {
                          type: "file",
                          accept: "image/png,image/jpeg,image/webp",
                          className: "hidden",
                          "aria-label": `Foto zu ${item.name} hochladen`,
                          onChange: (e) => {
                            const file = e.target.files?.[0];
                            if (file) void uploadImage(item, file);
                            e.target.value = "";
                          }
                        }
                      )
                    ] })
                  ] }),
                  /* @__PURE__ */ jsx5("div", { className: "mt-4", children: /* @__PURE__ */ jsx5(ItemDocuments, { item }) })
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
  itemPayload,
  priceLinks,
  priceQuery,
  rackRows,
  warrantyState
};
