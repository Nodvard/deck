// src/SynologyPage.tsx
import { useCallback, useEffect as useEffect3, useRef as useRef2, useState as useState2 } from "react";

// ../../_shared/frontend/src/lifecycle.ts
import { useEffect, useRef } from "react";
function useUnmountSignal() {
  const ref = useRef(null);
  if (!ref.current) ref.current = new AbortController();
  useEffect(() => {
    if (ref.current?.signal.aborted) ref.current = new AbortController();
    return () => ref.current?.abort();
  }, []);
  return useRef(() => ref.current.signal).current;
}

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

// ../../_shared/frontend/src/loadError.tsx
import { jsx as jsx2, jsxs as jsxs2 } from "react/jsx-runtime";
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
      hint && /* @__PURE__ */ jsx2("p", { className: "text-xs text-white/50", children: hint }),
      onRetry && /* @__PURE__ */ jsx2("button", { type: "button", onClick: onRetry, className: "mt-1 text-xs text-white/70 underline underline-offset-2 hover:text-white", children: "Erneut versuchen" })
    ] });
  }
  return /* @__PURE__ */ jsxs2("div", { role: "alert", "data-testid": "load-error", className: "mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm", children: [
    /* @__PURE__ */ jsxs2("div", { className: "min-w-0 flex-1", children: [
      /* @__PURE__ */ jsxs2("p", { className: "text-red-200", children: [
        what,
        " lie\xDF sich gerade nicht laden: ",
        error
      ] }),
      hint && /* @__PURE__ */ jsx2("p", { className: "text-xs text-white/55", children: hint })
    ] }),
    onRetry && /* @__PURE__ */ jsx2(Button, { small: true, onClick: onRetry, children: "Erneut versuchen" })
  ] });
}

// ../../_shared/frontend/src/links.tsx
import { useEffect as useEffect2, useState } from "react";
import { jsx as jsx3 } from "react/jsx-runtime";
function SettingsLink({
  to,
  permission,
  variant = "primary",
  children
}) {
  if (!deck().hasPermission(permission)) return null;
  return /* @__PURE__ */ jsx3("a", { href: to, className: buttonClass(variant), children });
}
function safeHref(url) {
  const text = url?.trim();
  if (!text || !/^https?:\/\//i.test(text)) return void 0;
  try {
    const parsed = new URL(text);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? text : void 0;
  } catch {
    return void 0;
  }
}

// src/SynologyPage.tsx
import { Fragment as Fragment2, jsx as jsx4, jsxs as jsxs3 } from "react/jsx-runtime";
var API = "/ext/synology";
var SETTINGS_PATH = "/settings/extensions/synology";
var FULL_PERCENT = 90;
var PROBLEM = {
  auth_failed: { label: "Anmeldung fehlgeschlagen", tone: "bad" },
  unreachable: { label: "nicht erreichbar", tone: "bad" },
  error: { label: "Fehler", tone: "warn" }
};
var UNITS = ["B", "KB", "MB", "GB", "TB", "PB"];
function toneOf(tone) {
  if (tone === "good") return "good";
  if (tone === "warn") return "warn";
  if (tone === "danger") return "bad";
  return "neutral";
}
function sizeText(count) {
  let value = count;
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit > 0 && value < 10 ? 1 : 0;
  const rounded = Math.floor(value * 10 ** digits + 0.5) / 10 ** digits;
  return `${rounded.toFixed(digits).replace(".", ",")} ${UNITS[unit]}`;
}
function durationText(seconds) {
  let minutes = Math.floor(seconds / 60);
  if (minutes < 1) return "< 1 Min.";
  if (minutes < 60) return `${minutes} Min.`;
  let hours = Math.floor(minutes / 60);
  minutes %= 60;
  if (hours < 24) return minutes ? `${hours} Std. ${minutes} Min.` : `${hours} Std.`;
  const days = Math.floor(hours / 24);
  hours %= 24;
  const dayText = days === 1 ? "1 Tag" : `${days} Tage`;
  return hours ? `${dayText} ${hours} Std.` : dayText;
}
function percentText(value) {
  return `${Math.floor(value + 0.5)} %`;
}
function usageText(volume) {
  if (volume.used === null || !volume.total) return "unbekannt";
  const percent = volume.percent !== null ? ` (${volume.percent} %)` : "";
  return `${sizeText(volume.used)} von ${sizeText(volume.total)}${percent}`;
}
var DSM_TIME = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/;
function dsmTime(value) {
  if (!value) return null;
  const m = DSM_TIME.exec(value);
  return m ? `${m[3]}.${m[2]}.${m[1]}, ${m[4]}:${m[5]}` : value;
}
function urgent(data) {
  return (data.problems ?? []).filter((p) => p.severity === "critical" || p.severity === "warning");
}
function headline(data) {
  const tones = [...(data.volumes ?? []).map((v) => v.tone), ...(data.disks ?? []).map((d) => d.tone)];
  const severities = urgent(data).map((p) => p.severity);
  if (tones.includes("danger") || severities.includes("critical") || data.system?.temperature_warn === true) return { label: "Problem", tone: "bad" };
  if (tones.includes("warn") || severities.includes("warning")) return { label: "Hinweis", tone: "warn" };
  const unknown = [data.system_note, data.load_note, data.storage_note, data.update_note, data.backup_note].some((n) => n && n.kind !== "unavailable");
  if (unknown) return { label: "unvollst\xE4ndig", tone: "warn" };
  return { label: "in Ordnung", tone: "good" };
}
var TONE_TEXT = { warn: "text-amber-300", danger: "text-red-300" };
function PartNote({ note, testId }) {
  if (!note) return null;
  const color = note.kind === "unavailable" ? "text-white/50" : note.kind === "no_rights" || note.kind === "partial" ? "text-amber-300" : "text-red-300";
  return /* @__PURE__ */ jsx4("p", { "data-testid": testId, role: note.kind === "unavailable" ? void 0 : "alert", className: `break-words text-sm ${color}`, children: note.message });
}
function UsageBar({ volume }) {
  if (volume.percent === null) return /* @__PURE__ */ jsx4("span", { className: "text-white/50", children: "unbekannt" });
  const full = volume.percent > FULL_PERCENT;
  return /* @__PURE__ */ jsxs3("div", { className: "min-w-[10rem] space-y-1", children: [
    /* @__PURE__ */ jsx4(
      "div",
      {
        role: "progressbar",
        "aria-label": `Belegung von ${volume.name}`,
        "aria-valuemin": 0,
        "aria-valuemax": 100,
        "aria-valuenow": volume.percent,
        className: "h-1.5 w-full overflow-hidden rounded bg-white/10",
        children: /* @__PURE__ */ jsx4("div", { className: `h-full rounded ${full ? "bg-amber-400" : "bg-[var(--color-accent)]"}`, style: { width: `${volume.percent}%` } })
      }
    ),
    /* @__PURE__ */ jsx4("p", { className: `text-xs ${full ? "text-amber-300" : "text-white/60"}`, children: usageText(volume) })
  ] });
}
function AttentionCard({ data }) {
  const items = urgent(data);
  if (items.length === 0) return null;
  return /* @__PURE__ */ jsx4(Card, { title: "Braucht Aufmerksamkeit", padded: false, children: /* @__PURE__ */ jsx4("ul", { "data-testid": "attention", className: "divide-y divide-white/[0.06]", children: items.map((problem) => /* @__PURE__ */ jsxs3("li", { "data-testid": "attention-item", className: "flex flex-wrap items-start justify-between gap-x-4 gap-y-1 px-5 py-3", children: [
    /* @__PURE__ */ jsxs3("div", { className: "min-w-0 flex-1 basis-56", children: [
      /* @__PURE__ */ jsx4("p", { className: "break-words text-sm font-semibold", children: problem.subject }),
      /* @__PURE__ */ jsx4("p", { className: "break-words text-sm text-white/60", children: problem.text })
    ] }),
    /* @__PURE__ */ jsx4(Badge, { tone: problem.severity === "critical" ? "bad" : "warn", children: problem.severity === "critical" ? "kritisch" : "Warnung" })
  ] }, problem.key)) }) });
}
function PoolsCard({ data }) {
  const pools = data.pools;
  if (!pools || pools.length === 0) return null;
  return /* @__PURE__ */ jsx4(Card, { title: "Speicherpools", padded: false, children: /* @__PURE__ */ jsx4("div", { className: "overflow-x-auto", children: /* @__PURE__ */ jsxs3("table", { "data-testid": "pools", className: "w-full text-sm", children: [
    /* @__PURE__ */ jsx4("thead", { children: /* @__PURE__ */ jsxs3("tr", { className: "border-b border-white/10 text-left text-xs text-white/50", children: [
      /* @__PURE__ */ jsx4("th", { className: "px-5 py-2 font-medium", children: "Name" }),
      /* @__PURE__ */ jsx4("th", { className: "px-3 py-2 font-medium", children: "RAID" }),
      /* @__PURE__ */ jsx4("th", { className: "px-3 py-2 font-medium", children: "Zustand" }),
      /* @__PURE__ */ jsx4("th", { className: "px-3 py-2 font-medium", children: "Festplatten" }),
      /* @__PURE__ */ jsx4("th", { className: "px-3 py-2 font-medium", children: "Volumes" }),
      /* @__PURE__ */ jsx4("th", { className: "px-5 py-2 font-medium", children: "Gr\xF6\xDFe" })
    ] }) }),
    /* @__PURE__ */ jsx4("tbody", { className: "divide-y divide-white/[0.06]", children: pools.map((pool) => /* @__PURE__ */ jsxs3("tr", { "data-testid": "pool", children: [
      /* @__PURE__ */ jsxs3("td", { className: "break-words px-5 py-2.5 align-top", children: [
        /* @__PURE__ */ jsx4("span", { className: "font-medium", children: pool.name }),
        pool.description && /* @__PURE__ */ jsx4("p", { className: "text-xs text-white/45", children: pool.description })
      ] }),
      /* @__PURE__ */ jsx4("td", { className: "whitespace-nowrap px-3 py-2.5 align-top", children: pool.raid ?? "\u2013" }),
      /* @__PURE__ */ jsx4("td", { className: "px-3 py-2.5 align-top", children: /* @__PURE__ */ jsx4(Badge, { tone: toneOf(pool.tone), children: pool.status_label }) }),
      /* @__PURE__ */ jsx4("td", { className: "px-3 py-2.5 align-top tabular-nums", children: pool.disks ?? "\u2013" }),
      /* @__PURE__ */ jsx4("td", { className: "break-words px-3 py-2.5 align-top text-white/70", children: pool.volumes.length > 0 ? pool.volumes.join(", ") : "\u2013" }),
      /* @__PURE__ */ jsx4("td", { className: "whitespace-nowrap px-5 py-2.5 align-top tabular-nums", children: pool.total !== null ? sizeText(pool.total) : "\u2013" })
    ] }, pool.id)) })
  ] }) }) });
}
function VolumesCard({ data }) {
  const volumes = data.volumes;
  return /* @__PURE__ */ jsxs3(Card, { title: "Volumes", padded: false, children: [
    data.storage_note && /* @__PURE__ */ jsx4("div", { className: "px-5 py-4", children: /* @__PURE__ */ jsx4(PartNote, { note: data.storage_note, testId: "note-storage" }) }),
    volumes && volumes.length === 0 && /* @__PURE__ */ jsx4("p", { "data-testid": "no-volumes", className: "px-5 py-4 text-sm text-white/50", children: "DSM meldet keine Volumes." }),
    volumes && volumes.length > 0 && /* @__PURE__ */ jsx4("div", { className: "overflow-x-auto", children: /* @__PURE__ */ jsxs3("table", { "data-testid": "volumes", className: "w-full text-sm", children: [
      /* @__PURE__ */ jsx4("thead", { children: /* @__PURE__ */ jsxs3("tr", { className: "border-b border-white/10 text-left text-xs text-white/50", children: [
        /* @__PURE__ */ jsx4("th", { className: "px-5 py-2 font-medium", children: "Name" }),
        /* @__PURE__ */ jsx4("th", { className: "px-3 py-2 font-medium", children: "Status" }),
        /* @__PURE__ */ jsx4("th", { className: "px-5 py-2 font-medium", children: "Belegung" })
      ] }) }),
      /* @__PURE__ */ jsx4("tbody", { className: "divide-y divide-white/[0.06]", children: volumes.map((volume) => /* @__PURE__ */ jsxs3("tr", { "data-testid": "volume", children: [
        /* @__PURE__ */ jsxs3("td", { className: "break-words px-5 py-2.5 align-top", children: [
          /* @__PURE__ */ jsx4("span", { className: "font-medium", children: volume.name }),
          volume.fs_type && /* @__PURE__ */ jsx4("span", { className: "ml-2 text-xs text-white/45", children: volume.fs_type })
        ] }),
        /* @__PURE__ */ jsx4("td", { className: "px-3 py-2.5 align-top", children: /* @__PURE__ */ jsx4(Badge, { tone: toneOf(volume.tone), children: volume.status_label }) }),
        /* @__PURE__ */ jsx4("td", { className: "px-5 py-2.5 align-top", children: /* @__PURE__ */ jsx4(UsageBar, { volume }) })
      ] }, volume.id)) })
    ] }) })
  ] });
}
function DisksCard({ data }) {
  const disks = data.disks;
  if (!disks) return null;
  return /* @__PURE__ */ jsxs3(Card, { title: "Festplatten", padded: false, children: [
    disks.length === 0 && /* @__PURE__ */ jsx4("p", { "data-testid": "no-disks", className: "px-5 py-4 text-sm text-white/50", children: "DSM meldet keine Festplatten." }),
    disks.length > 0 && /* @__PURE__ */ jsx4("div", { className: "overflow-x-auto", children: /* @__PURE__ */ jsxs3("table", { "data-testid": "disks", className: "w-full text-sm", children: [
      /* @__PURE__ */ jsx4("thead", { children: /* @__PURE__ */ jsxs3("tr", { className: "border-b border-white/10 text-left text-xs text-white/50", children: [
        /* @__PURE__ */ jsx4("th", { className: "px-5 py-2 font-medium", children: "Name" }),
        /* @__PURE__ */ jsx4("th", { className: "px-3 py-2 font-medium", children: "Modell" }),
        /* @__PURE__ */ jsx4("th", { className: "px-3 py-2 font-medium", children: "Status" }),
        /* @__PURE__ */ jsx4("th", { className: "whitespace-nowrap px-3 py-2 font-medium", children: "Temperatur" }),
        /* @__PURE__ */ jsx4("th", { className: "px-5 py-2 font-medium", children: "SMART" })
      ] }) }),
      /* @__PURE__ */ jsx4("tbody", { className: "divide-y divide-white/[0.06]", children: disks.map((disk) => /* @__PURE__ */ jsxs3("tr", { "data-testid": "disk", children: [
        /* @__PURE__ */ jsxs3("td", { className: "break-words px-5 py-2.5 align-top", children: [
          /* @__PURE__ */ jsx4("span", { className: "font-medium", children: disk.name }),
          disk.warning && /* @__PURE__ */ jsx4("p", { "data-testid": "disk-warning", className: "text-xs text-amber-300", children: disk.warning })
        ] }),
        /* @__PURE__ */ jsx4("td", { className: "break-words px-3 py-2.5 align-top text-white/70", children: disk.model ?? "\u2013" }),
        /* @__PURE__ */ jsx4("td", { className: "px-3 py-2.5 align-top", children: /* @__PURE__ */ jsx4(Badge, { tone: toneOf(disk.tone), children: disk.status_label }) }),
        /* @__PURE__ */ jsx4("td", { className: "whitespace-nowrap px-3 py-2.5 align-top tabular-nums", children: disk.temp === null ? "\u2013" : disk.temp_warn === true ? /* @__PURE__ */ jsxs3("span", { className: "text-amber-300", title: "\xFCber der Grenze", children: [
          `${disk.temp} \xB0C`,
          /* @__PURE__ */ jsx4("span", { className: "sr-only", children: " (\xFCber der Grenze)" })
        ] }) : `${disk.temp} \xB0C` }),
        /* @__PURE__ */ jsx4("td", { className: "px-5 py-2.5 align-top", children: disk.smart_label ? /* @__PURE__ */ jsx4(Badge, { tone: toneOf(disk.smart_tone), children: disk.smart_label }) : /* @__PURE__ */ jsx4("span", { className: "text-white/50", children: "\u2013" }) })
      ] }, disk.id)) })
    ] }) })
  ] });
}
function BackupsCard({ data }) {
  const backups = data.backups ?? null;
  const note = data.backup_note ?? null;
  if (!backups && !note) return null;
  return /* @__PURE__ */ jsxs3(Card, { title: "Hyper Backup", padded: false, children: [
    note && /* @__PURE__ */ jsx4("div", { className: "px-5 py-4", children: /* @__PURE__ */ jsx4(PartNote, { note, testId: "backup-note" }) }),
    backups && backups.length === 0 && /* @__PURE__ */ jsx4("p", { "data-testid": "no-backups", className: "px-5 py-4 text-sm text-white/50", children: "In Hyper Backup ist noch keine Sicherung eingerichtet." }),
    backups && backups.length > 0 && /* @__PURE__ */ jsxs3(Fragment2, { children: [
      /* @__PURE__ */ jsx4("div", { className: "overflow-x-auto", children: /* @__PURE__ */ jsxs3("table", { "data-testid": "backups", className: "w-full text-sm", children: [
        /* @__PURE__ */ jsx4("thead", { children: /* @__PURE__ */ jsxs3("tr", { className: "border-b border-white/10 text-left text-xs text-white/50", children: [
          /* @__PURE__ */ jsx4("th", { className: "px-5 py-2 font-medium", children: "Aufgabe" }),
          /* @__PURE__ */ jsx4("th", { className: "px-3 py-2 font-medium", children: "Ergebnis" }),
          /* @__PURE__ */ jsx4("th", { className: "whitespace-nowrap px-3 py-2 font-medium", children: "Letzter Lauf" }),
          /* @__PURE__ */ jsx4("th", { className: "whitespace-nowrap px-5 py-2 font-medium", children: "N\xE4chster Lauf" })
        ] }) }),
        /* @__PURE__ */ jsx4("tbody", { className: "divide-y divide-white/[0.06]", children: backups.map((task) => /* @__PURE__ */ jsxs3("tr", { "data-testid": "backup", children: [
          /* @__PURE__ */ jsxs3("td", { className: "break-words px-5 py-2.5 align-top", children: [
            /* @__PURE__ */ jsx4("span", { className: "font-medium", children: task.name }),
            task.problem && /* @__PURE__ */ jsx4("p", { "data-testid": "backup-problem", className: `text-xs ${TONE_TEXT[task.tone] ?? "text-white/60"}`, children: task.problem })
          ] }),
          /* @__PURE__ */ jsx4("td", { className: "px-3 py-2.5 align-top", children: /* @__PURE__ */ jsx4(Badge, { tone: toneOf(task.tone), children: task.result_label }) }),
          /* @__PURE__ */ jsx4("td", { className: "whitespace-nowrap px-3 py-2.5 align-top tabular-nums", children: dsmTime(task.last_run) ?? /* @__PURE__ */ jsx4("span", { className: "text-white/50", children: "\u2013" }) }),
          /* @__PURE__ */ jsx4("td", { className: "whitespace-nowrap px-5 py-2.5 align-top tabular-nums", children: dsmTime(task.next_run) ?? /* @__PURE__ */ jsx4("span", { className: "text-white/50", children: "kein Zeitplan" }) })
        ] }, task.id)) })
      ] }) }),
      /* @__PURE__ */ jsx4("p", { className: "border-t border-white/[0.06] px-5 py-2.5 text-xs text-white/45", children: "Zeiten in Ortszeit der Synology." })
    ] })
  ] });
}
function Tiles({ data }) {
  const { system, load } = data;
  const busy = (value) => value !== null && value !== void 0 && value > FULL_PERCENT ? "warn" : void 0;
  return /* @__PURE__ */ jsxs3("div", { className: "space-y-2", children: [
    /* @__PURE__ */ jsxs3("div", { "data-testid": "tiles", className: "grid grid-cols-2 gap-3 lg:grid-cols-4", children: [
      /* @__PURE__ */ jsx4(Stat, { label: "CPU", value: load?.cpu_percent != null ? percentText(load.cpu_percent) : "\u2013", tone: busy(load?.cpu_percent) }),
      /* @__PURE__ */ jsx4(
        Stat,
        {
          label: "Arbeitsspeicher",
          value: load?.ram_percent != null ? percentText(load.ram_percent) : "\u2013",
          hint: system?.ram_mb != null ? `von ${sizeText(system.ram_mb * 1024 * 1024)}` : void 0,
          tone: busy(load?.ram_percent)
        }
      ),
      /* @__PURE__ */ jsx4(
        Stat,
        {
          label: "Temperatur",
          value: system?.temperature != null ? `${system.temperature} \xB0C` : "\u2013",
          hint: system?.temperature_warn === true ? "DSM meldet: zu warm" : void 0,
          tone: system?.temperature_warn === true ? "bad" : void 0
        }
      ),
      /* @__PURE__ */ jsx4(Stat, { label: "Laufzeit", value: system?.uptime_seconds != null ? durationText(system.uptime_seconds) : "\u2013" })
    ] }),
    /* @__PURE__ */ jsx4(PartNote, { note: data.system_note, testId: "note-system" }),
    /* @__PURE__ */ jsx4(PartNote, { note: data.load_note, testId: "note-load" })
  ] });
}
function Device({ data }) {
  const system = data.system;
  const head = headline(data);
  const openHref = safeHref(data.url);
  const facts = [system?.dsm_version, system?.ram_mb != null ? `${sizeText(system.ram_mb * 1024 * 1024)} Arbeitsspeicher` : null].filter(Boolean);
  return /* @__PURE__ */ jsxs3("div", { className: "space-y-6", children: [
    /* @__PURE__ */ jsx4(AttentionCard, { data }),
    /* @__PURE__ */ jsx4(
      Card,
      {
        title: system?.model ?? "Synology",
        description: openHref ? /* @__PURE__ */ jsx4("a", { href: openHref, target: "_blank", rel: "noreferrer", className: "break-all hover:text-white hover:underline", children: "DSM \xF6ffnen" }) : void 0,
        actions: /* @__PURE__ */ jsx4(Badge, { tone: head.tone, children: head.label }),
        children: /* @__PURE__ */ jsxs3("div", { "data-testid": "device", className: "space-y-3", children: [
          facts.length > 0 && /* @__PURE__ */ jsx4("p", { "data-testid": "facts", className: "break-words text-sm text-white/70", children: facts.join(" \xB7 ") }),
          data.update?.available && /* @__PURE__ */ jsxs3("p", { "data-testid": "update", role: "status", className: "break-words text-sm text-amber-300", children: [
            "Update verf\xFCgbar",
            data.update.version ? `: ${data.update.version}` : ""
          ] }),
          data.update && !data.update.available && /* @__PURE__ */ jsx4("p", { "data-testid": "up-to-date", className: "text-sm text-white/50", children: "DSM ist aktuell." }),
          /* @__PURE__ */ jsx4(PartNote, { note: data.update_note, testId: "note-update" })
        ] })
      }
    ),
    /* @__PURE__ */ jsx4(Tiles, { data }),
    /* @__PURE__ */ jsx4(PoolsCard, { data }),
    /* @__PURE__ */ jsx4(VolumesCard, { data }),
    /* @__PURE__ */ jsx4(DisksCard, { data }),
    /* @__PURE__ */ jsx4(BackupsCard, { data })
  ] });
}
function StateProblem({ data, onRetry }) {
  const canManage = deck().hasPermission("extensions.manage");
  const head = PROBLEM[data.state] ?? PROBLEM.error;
  return /* @__PURE__ */ jsx4(Card, { title: "Synology DSM", actions: /* @__PURE__ */ jsx4(Badge, { tone: head.tone, children: head.label }), children: /* @__PURE__ */ jsxs3("div", { "data-testid": "problem", role: "alert", className: "flex flex-col items-start gap-3", children: [
    /* @__PURE__ */ jsx4("p", { className: "break-words text-sm text-red-300", children: data.message }),
    /* @__PURE__ */ jsxs3("div", { className: "flex flex-wrap items-center gap-2", children: [
      data.state !== "auth_failed" && /* @__PURE__ */ jsx4(Button, { small: true, onClick: onRetry, children: "Erneut versuchen" }),
      canManage && /* @__PURE__ */ jsx4("a", { href: SETTINGS_PATH, className: buttonClass("ghost", true), children: "Einstellungen" })
    ] })
  ] }) });
}
function NotConfigured({ data }) {
  const canManage = deck().hasPermission("extensions.manage");
  return /* @__PURE__ */ jsx4(
    EmptyState,
    {
      icon: "clock",
      title: "Noch nicht eingerichtet",
      text: canManage ? data.message ?? void 0 : "Ein Administrator kann Synology DSM unter Einstellungen \u2192 Erweiterungen \u2192 Synology DSM einrichten.",
      action: /* @__PURE__ */ jsx4(SettingsLink, { to: SETTINGS_PATH, permission: "extensions.manage", children: "Jetzt einrichten" })
    }
  );
}
function SynologyPage() {
  const unmountSignal = useUnmountSignal();
  const [data, setData] = useState2(null);
  const [error, setError] = useState2(null);
  const [loading, setLoading] = useState2(true);
  const seq = useRef2(0);
  const load = useCallback((refresh) => {
    const mine = ++seq.current;
    setLoading(true);
    fetchJson(`${API}/overview${refresh ? "?refresh=true" : ""}`, { signal: unmountSignal() }).then((body) => {
      if (mine !== seq.current) return;
      setData(body);
      setError(null);
    }).catch((err) => {
      if (mine !== seq.current || err instanceof DOMException && err.name === "AbortError") return;
      setError(errorMessage(err));
    }).finally(() => {
      if (mine === seq.current) setLoading(false);
    });
  }, [unmountSignal]);
  useEffect3(() => {
    load(false);
  }, [load]);
  const reload = useCallback(() => load(true), [load]);
  return /* @__PURE__ */ jsxs3(
    Page,
    {
      title: "Synology DSM",
      description: "Zeigt den Zustand deiner Synology: Auslastung, Speicherpools, Volumes, Festplatten, Hyper Backup und ob ein Update bereitsteht. Nur lesend.",
      actions: /* @__PURE__ */ jsxs3(Fragment2, { children: [
        deck().hasPermission("extensions.manage") && /* @__PURE__ */ jsx4("a", { href: SETTINGS_PATH, className: buttonClass("ghost"), children: "Einstellungen" }),
        /* @__PURE__ */ jsx4(Button, { onClick: reload, disabled: loading, children: "Aktualisieren" })
      ] }),
      children: [
        error && /* @__PURE__ */ jsx4(LoadError, { what: "Der Zustand der Synology", error, onRetry: reload }),
        !data && !error && /* @__PURE__ */ jsx4(Card, { children: /* @__PURE__ */ jsx4("p", { className: "text-sm text-white/50", children: "Lade \u2026" }) }),
        data?.state === "not_configured" && /* @__PURE__ */ jsx4(NotConfigured, { data }),
        data && data.state !== "ok" && data.state !== "not_configured" && /* @__PURE__ */ jsx4(StateProblem, { data, onRetry: reload }),
        data?.state === "ok" && /* @__PURE__ */ jsx4(Device, { data })
      ]
    }
  );
}
export {
  SynologyPage,
  dsmTime,
  durationText,
  percentText,
  sizeText,
  toneOf,
  usageText
};
