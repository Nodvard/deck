// src/UptimeKumaPage.tsx
import { useCallback, useEffect as useEffect3, useMemo, useRef as useRef2, useState as useState2 } from "react";

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
var EVENT_PREFIX = "nodvard-deck:";
function findDeck() {
  if (typeof window === "undefined") return void 0;
  return window.__nodvardDeck;
}
function deck() {
  return findDeck();
}
function deckEventName(event) {
  return EVENT_PREFIX + event;
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
function SearchInput({ value, onChange, placeholder, label }) {
  return /* @__PURE__ */ jsxs("span", { className: "relative block min-w-[14rem] flex-1", children: [
    /* @__PURE__ */ jsx(Icon, { name: "search", size: 15, className: "pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-white/35" }),
    /* @__PURE__ */ jsx("input", { "aria-label": label, value, placeholder, onChange: (e) => onChange(e.target.value), className: `${inputClass} pl-9` })
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

// ../../../frontend/src/lib/deckTimezone.ts
import { useSyncExternalStore } from "react";
function getDeckTimezone() {
  const zone = findDeck()?.timezone;
  return typeof zone === "string" && zone ? zone : null;
}
function subscribe(onChange) {
  const name = deckEventName("timezone");
  window.addEventListener(name, onChange);
  return () => window.removeEventListener(name, onChange);
}
function useDeckTimezone() {
  return useSyncExternalStore(subscribe, getDeckTimezone, () => null);
}

// src/UptimeKumaPage.tsx
import { Fragment as Fragment2, jsx as jsx4, jsxs as jsxs3 } from "react/jsx-runtime";
var API = "/ext/uptime-kuma";
var SETTINGS_PATH = "/settings/extensions/uptime-kuma";
var CERT_WARN_DAYS = 14;
var SEARCH_FROM = 8;
var STATUS = {
  up: { label: "l\xE4uft", tone: "good" },
  down: { label: "ausgefallen", tone: "bad" },
  pending: { label: "ausstehend", tone: "warn" },
  maintenance: { label: "Wartung", tone: "info" },
  unknown: { label: "unbekannt", tone: "neutral" }
};
var PROBLEM = {
  auth_failed: { label: "Anmeldung fehlgeschlagen", tone: "bad" },
  unreachable: { label: "nicht erreichbar", tone: "bad" },
  error: { label: "Fehler", tone: "warn" }
};
function statusOf(status) {
  return STATUS[status] ?? STATUS.unknown;
}
function percent(value) {
  return `${value.toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} %`;
}
function dayText(days) {
  return `${days} ${days === 1 ? "Tag" : "Tagen"}`;
}
function formatTime(iso, timeZone) {
  if (!iso) return "Zeit unbekannt";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "Zeit unbekannt";
  const options = { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" };
  try {
    return date.toLocaleString("de-DE", timeZone ? { ...options, timeZone } : options);
  } catch {
    return date.toLocaleString("de-DE", options);
  }
}
function CertInfo({ days, valid }) {
  if (days != null && days < 0) return /* @__PURE__ */ jsx4(Badge, { tone: "bad", children: "Zertifikat abgelaufen" });
  if (valid === false) return /* @__PURE__ */ jsx4(Badge, { tone: "bad", children: "Zertifikat ung\xFCltig" });
  if (days == null) return null;
  if (days === 0) return /* @__PURE__ */ jsx4(Badge, { tone: "warn", children: "Zertifikat l\xE4uft heute ab" });
  if (days <= CERT_WARN_DAYS) return /* @__PURE__ */ jsxs3(Badge, { tone: "warn", children: [
    "Zertifikat l\xE4uft in ",
    dayText(days),
    " ab"
  ] });
  return /* @__PURE__ */ jsxs3("span", { className: "text-xs text-white/45", children: [
    "Zertifikat noch ",
    days,
    " Tage"
  ] });
}
function MonitorRow({ monitor }) {
  const status = statusOf(monitor.status);
  const target = [monitor.type, monitor.url].filter(Boolean).join(" \xB7 ");
  const tint = monitor.status === "down" ? "bg-red-500/[0.06]" : monitor.status === "pending" ? "bg-amber-500/[0.06]" : "";
  return /* @__PURE__ */ jsxs3("li", { "data-testid": "monitor", className: `flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 sm:px-5 ${tint}`, children: [
    /* @__PURE__ */ jsxs3("div", { className: "min-w-0 flex-1 basis-48", children: [
      /* @__PURE__ */ jsx4("p", { className: "break-words text-sm font-medium", children: monitor.name }),
      target && /* @__PURE__ */ jsx4("p", { className: "break-all text-xs text-white/50", children: target })
    ] }),
    /* @__PURE__ */ jsxs3("div", { className: "flex flex-wrap items-center gap-x-3 gap-y-1.5", children: [
      /* @__PURE__ */ jsx4(CertInfo, { days: monitor.cert_days_left, valid: monitor.cert_valid }),
      monitor.uptime_24h != null && /* @__PURE__ */ jsxs3("span", { className: "text-xs text-white/60", title: "Verf\xFCgbarkeit der letzten 24 Stunden", children: [
        percent(monitor.uptime_24h),
        " (24 Std.)"
      ] }),
      monitor.response_ms != null && /* @__PURE__ */ jsxs3("span", { className: "text-xs text-white/60", children: [
        monitor.response_ms.toLocaleString("de-DE"),
        " ms"
      ] }),
      /* @__PURE__ */ jsx4(Badge, { tone: status.tone, children: status.label })
    ] })
  ] });
}
function MonitorList({ monitors }) {
  const [search, setSearch] = useState2("");
  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return monitors;
    return monitors.filter((m) => m.name.toLowerCase().includes(q) || (m.url ?? "").toLowerCase().includes(q));
  }, [monitors, search]);
  return /* @__PURE__ */ jsxs3("div", { children: [
    monitors.length >= SEARCH_FROM && /* @__PURE__ */ jsx4("div", { className: "mb-3 flex", children: /* @__PURE__ */ jsx4(SearchInput, { value: search, onChange: setSearch, placeholder: "Monitor suchen", label: "Monitore durchsuchen" }) }),
    shown.length === 0 ? /* @__PURE__ */ jsx4("p", { className: "py-3 text-center text-sm text-white/45", children: "Kein Monitor passt zur Suche." }) : /* @__PURE__ */ jsx4("ul", { className: "-mx-5 divide-y divide-white/[0.06] border-t border-white/[0.06]", children: shown.map((m, i) => /* @__PURE__ */ jsx4(MonitorRow, { monitor: m }, `${m.name}-${i}`)) })
  ] });
}
function Outages({ outages, state }) {
  const zone = useDeckTimezone();
  if (outages.length === 0) {
    return state === "ok" ? /* @__PURE__ */ jsx4("p", { className: "text-sm text-white/50", children: "In den letzten Messungen gab es keinen Ausfall." }) : null;
  }
  return /* @__PURE__ */ jsx4("ul", { "data-testid": "outages", className: "divide-y divide-white/[0.06]", children: outages.map((o, i) => /* @__PURE__ */ jsxs3("li", { className: "py-2 first:pt-0 last:pb-0", children: [
    /* @__PURE__ */ jsx4("p", { className: "break-words text-sm font-medium", children: o.monitor }),
    /* @__PURE__ */ jsxs3("p", { className: "break-words text-xs text-white/50", children: [
      formatTime(o.time, zone),
      o.message ? ` \xB7 ${o.message}` : ""
    ] })
  ] }, `${o.monitor}-${o.time}-${i}`)) });
}
function headline(connection) {
  if (connection.state === "not_configured") return { label: "nicht eingerichtet", tone: "neutral" };
  if (connection.state !== "ok" && !connection.summary) return PROBLEM[connection.state] ?? PROBLEM.error;
  const s = connection.summary;
  if (s && s.down > 0) return { label: `${s.down} ausgefallen`, tone: "bad" };
  if (connection.state !== "ok") return PROBLEM[connection.state] ?? PROBLEM.error;
  if (s && s.total === 0) return { label: "keine Monitore", tone: "neutral" };
  if (s && s.up < s.total) return { label: `${s.up} von ${s.total} laufen`, tone: s.pending > 0 ? "warn" : "neutral" };
  return { label: "alles in Ordnung", tone: "good" };
}
function ConnectionCard({ connection, onRetry }) {
  const head = headline(connection);
  const s = connection.summary;
  const problem = connection.state !== "ok" && connection.state !== "not_configured";
  const canManage = deck().hasPermission("extensions.manage");
  const openHref = safeHref(connection.url);
  return /* @__PURE__ */ jsx4(
    Card,
    {
      title: connection.name,
      description: openHref ? /* @__PURE__ */ jsx4("a", { href: openHref, target: "_blank", rel: "noreferrer", className: "break-all hover:text-white hover:underline", children: "Uptime Kuma \xF6ffnen" }) : void 0,
      actions: /* @__PURE__ */ jsx4(Badge, { tone: head.tone, children: head.label }),
      children: /* @__PURE__ */ jsxs3("div", { "data-testid": `connection-${connection.name}`, className: "space-y-4", children: [
        connection.state === "not_configured" && /* @__PURE__ */ jsxs3("div", { "data-testid": "not-configured", className: "flex flex-col items-start gap-3", children: [
          /* @__PURE__ */ jsx4("p", { className: "text-sm text-white/60", children: connection.message }),
          canManage ? /* @__PURE__ */ jsx4("a", { href: SETTINGS_PATH, className: buttonClass("primary", true), children: "Jetzt einrichten" }) : /* @__PURE__ */ jsx4("p", { className: "text-xs text-white/45", children: "Ein Administrator kann das unter Einstellungen \u2192 Erweiterungen \u2192 Uptime Kuma einrichten." })
        ] }),
        problem && /* @__PURE__ */ jsxs3("div", { "data-testid": "problem", role: "alert", className: "flex flex-col items-start gap-3", children: [
          /* @__PURE__ */ jsx4("p", { className: "break-words text-sm text-red-300", children: connection.message }),
          /* @__PURE__ */ jsxs3("div", { className: "flex flex-wrap items-center gap-2", children: [
            /* @__PURE__ */ jsx4(Button, { small: true, onClick: onRetry, children: "Erneut versuchen" }),
            canManage && /* @__PURE__ */ jsx4("a", { href: SETTINGS_PATH, className: buttonClass("ghost", true), children: "Einstellungen" })
          ] }),
          s && connection.monitors.length > 0 && /* @__PURE__ */ jsx4("p", { className: "text-xs text-white/45", children: "Angezeigt wird, was trotzdem zu lesen war." })
        ] }),
        s && /* @__PURE__ */ jsxs3("div", { className: "grid grid-cols-2 gap-3 lg:grid-cols-4", children: [
          /* @__PURE__ */ jsx4(Stat, { label: "Laufen", value: s.up, tone: s.total > 0 && s.up === s.total ? "good" : void 0 }),
          /* @__PURE__ */ jsx4(Stat, { label: "Ausgefallen", value: s.down, tone: s.down > 0 ? "bad" : void 0 }),
          /* @__PURE__ */ jsx4(Stat, { label: "Ausstehend", value: s.pending, tone: s.pending > 0 ? "warn" : void 0 }),
          /* @__PURE__ */ jsx4(Stat, { label: "In Wartung", value: s.maintenance })
        ] }),
        connection.monitors.length > 0 && /* @__PURE__ */ jsx4(MonitorList, { monitors: connection.monitors }),
        connection.state === "ok" && s && s.total === 0 && /* @__PURE__ */ jsx4("p", { className: "text-sm text-white/50", children: "In Uptime Kuma ist noch kein Monitor angelegt." }),
        connection.state === "ok" && !connection.status_page && connection.monitors.length > 0 && /* @__PURE__ */ jsx4("p", { "data-testid": "status-page-hint", className: "text-xs text-white/45", children: "F\xFCr die Verf\xFCgbarkeit der letzten 24 Stunden und die letzten Ausf\xE4lle trage unter Einstellungen \u2192 Erweiterungen \u2192 Uptime Kuma den Kurznamen einer Status-Seite ein." }),
        connection.status_page && (connection.outages.length > 0 || connection.state === "ok") && /* @__PURE__ */ jsxs3("div", { className: "border-t border-white/[0.06] pt-3", children: [
          /* @__PURE__ */ jsx4("h4", { className: "mb-2 text-xs font-semibold uppercase tracking-wide text-white/50", children: "Letzte Ausf\xE4lle" }),
          /* @__PURE__ */ jsx4(Outages, { outages: connection.outages, state: connection.state })
        ] })
      ] })
    }
  );
}
function UptimeKumaPage() {
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
  const connections = data?.connections ?? [];
  return /* @__PURE__ */ jsxs3(
    Page,
    {
      title: "Uptime Kuma",
      description: "Zeigt, welche Dienste laut Uptime Kuma laufen und welche ausgefallen sind.",
      actions: /* @__PURE__ */ jsxs3(Fragment2, { children: [
        deck().hasPermission("extensions.manage") && /* @__PURE__ */ jsx4("a", { href: SETTINGS_PATH, className: buttonClass("ghost"), children: "Einstellungen" }),
        /* @__PURE__ */ jsx4(Button, { onClick: reload, disabled: loading, children: "Aktualisieren" })
      ] }),
      children: [
        error && /* @__PURE__ */ jsx4(LoadError, { what: "Der Zustand von Uptime Kuma", error, onRetry: reload }),
        !data && !error && /* @__PURE__ */ jsx4(Card, { children: /* @__PURE__ */ jsx4("p", { className: "text-sm text-white/50", children: "Lade \u2026" }) }),
        data && connections.length === 0 && /* @__PURE__ */ jsx4(
          EmptyState,
          {
            icon: "clock",
            title: "Noch keine Verbindung",
            text: deck().hasPermission("extensions.manage") ? "Trage Uptime Kuma unter Einstellungen \u2192 Erweiterungen \u2192 Uptime Kuma ein." : "Ein Administrator kann Uptime Kuma unter Einstellungen \u2192 Erweiterungen \u2192 Uptime Kuma eintragen.",
            action: /* @__PURE__ */ jsx4(SettingsLink, { to: SETTINGS_PATH, permission: "extensions.manage", children: "Jetzt einrichten" })
          }
        ),
        connections.length > 0 && /* @__PURE__ */ jsx4("div", { className: "space-y-6", children: connections.map((c, i) => /* @__PURE__ */ jsx4(ConnectionCard, { connection: c, onRetry: reload }, `${c.name}-${i}`)) })
      ]
    }
  );
}
export {
  CertInfo,
  UptimeKumaPage
};
