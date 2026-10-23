// src/SocPage.tsx
import { useCallback as useCallback6, useContext as useContext4, useEffect as useEffect8, useMemo as useMemo2, useRef as useRef3, useState as useState9 } from "react";

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

// ../../_shared/frontend/src/location.ts
import { useCallback, useEffect as useEffect2, useMemo, useState } from "react";

// ../../../frontend/src/lib/deckGlobal.ts
var EVENT_PREFIX = "nodvard-deck:";
var LEGACY_EVENT_PREFIX = "lattice:";
function findDeck() {
  if (typeof window === "undefined") return void 0;
  return window.__nodvardDeck ?? window.__lattice;
}
function deck() {
  return findDeck();
}
function deckEventName(event) {
  const modern = typeof window !== "undefined" && Boolean(window.__nodvardDeck);
  return (modern ? EVENT_PREFIX : LEGACY_EVENT_PREFIX) + event;
}

// ../../_shared/frontend/src/location.ts
function useUrlParams() {
  const [search, setSearch] = useState(() => window.location.search);
  const [visits, setVisits] = useState(0);
  useEffect2(() => {
    const sync = () => setSearch(window.location.search);
    const visit = () => {
      sync();
      setVisits((n) => n + 1);
    };
    const onPopState = () => {
      if (!findDeck()?.navigateEvents) visit();
    };
    sync();
    const navigateEvent = deckEventName("navigate");
    window.addEventListener("popstate", onPopState);
    window.addEventListener(navigateEvent, visit);
    return () => {
      window.removeEventListener("popstate", onPopState);
      window.removeEventListener(navigateEvent, visit);
    };
  }, []);
  const params = useMemo(() => new URLSearchParams(search), [search]);
  const update = useCallback((changes) => {
    const url = new URL(window.location.href);
    for (const [key, value] of Object.entries(changes)) {
      if (value === null) url.searchParams.delete(key);
      else url.searchParams.set(key, value);
    }
    window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
    setSearch(window.location.search);
  }, []);
  return [params, update, visits];
}

// ../../_shared/frontend/src/SchedulePicker.tsx
import { useEffect as useEffect3, useState as useState2 } from "react";

// ../../../frontend/src/lib/deckTimezone.ts
import { useSyncExternalStore } from "react";

// ../../_shared/frontend/src/SchedulePicker.tsx
import { jsx as jsx2, jsxs as jsxs2 } from "react/jsx-runtime";
var DAY_SHORT = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
var DAY_LONG = ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"];
var WEEK_ORDER = [1, 2, 3, 4, 5, 6, 0];
var pad = (n) => String(n).padStart(2, "0");
var isInt = (s) => /^\d+$/.test(s);
function parseCron(cron) {
  const base = { mode: "daily", hour: 3, minute: 0, days: [0], dayOfMonth: 1, cron: cron ?? "" };
  if (!cron || !cron.trim()) return { ...base, mode: "off" };
  const p = cron.trim().split(/\s+/);
  if (p.length !== 5) return { ...base, mode: "custom" };
  const [mi, h, dom, mon, dow] = p;
  if (!isInt(mi) || mon !== "*") return { ...base, mode: "custom" };
  const minute = Number(mi);
  if (h === "*" && dom === "*" && dow === "*") return { ...base, mode: "hourly", minute };
  if (!isInt(h)) return { ...base, mode: "custom" };
  const hour = Number(h);
  if (dom === "*" && dow === "*") return { ...base, mode: "daily", hour, minute };
  if (dom === "*" && /^[0-7](,[0-7])*$/.test(dow)) {
    const days = [...new Set(dow.split(",").map((d) => Number(d) % 7))].sort();
    return { ...base, mode: days.length === 7 ? "daily" : "weekly", hour, minute, days };
  }
  if (isInt(dom) && dow === "*") return { ...base, mode: "monthly", hour, minute, dayOfMonth: Number(dom) };
  return { ...base, mode: "custom" };
}
function describeSchedule(cron, offLabel = "Manuell") {
  const s = parseCron(cron);
  const time = `${pad(s.hour)}:${pad(s.minute)}`;
  switch (s.mode) {
    case "off":
      return offLabel;
    case "hourly":
      return s.minute === 0 ? "St\xFCndlich zur vollen Stunde" : `St\xFCndlich um :${pad(s.minute)}`;
    case "daily":
      return `T\xE4glich um ${time}`;
    case "weekly": {
      const ordered = WEEK_ORDER.filter((d) => s.days.includes(d));
      if (ordered.join() === "1,2,3,4,5") return `Werktags um ${time}`;
      if (ordered.join() === "6,0") return `Am Wochenende um ${time}`;
      if (ordered.length === 1) return `Jeden ${DAY_LONG[ordered[0]]} um ${time}`;
      return `${ordered.map((d) => DAY_SHORT[d]).join(", ")} um ${time}`;
    }
    case "monthly":
      return `Monatlich am ${s.dayOfMonth}. um ${time}`;
    default:
      return `Eigener Zeitplan (${cron})`;
  }
}

// src/api.ts
import { createContext, useCallback as useCallback2, useContext, useEffect as useEffect4, useRef as useRef2, useState as useState3 } from "react";

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

// ../../_shared/frontend/src/actions.ts
var RUNNING_IN_BACKGROUND = "L\xE4uft im Hintergrund \u2013 das Ergebnis steht unter \u201EAktionen\u201C.";
function isActionRunning(status) {
  return status === "approved" || status === "executing";
}
var ACTION_POLL_INTERVAL_MS = 3e3;
var ACTION_POLL_MAX_MS = 60 * 60 * 1e3;
var OUTPUT_HIDDEN_HINT = "Ausgabe nur f\xFCr Nutzer mit Server-Rechten sichtbar";
function nonEmpty(value) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}
function actionReason(a) {
  if (a.status === "failed") return nonEmpty(a.result?.error);
  if (a.status === "denied") return nonEmpty(a.gate_decision?.detail) ?? nonEmpty(a.gate_decision?.user_reason) ?? nonEmpty(a.detail);
  return null;
}
function describeActionOutcome(a) {
  const reason = actionReason(a);
  switch (a.status) {
    case "succeeded":
      return { tone: "success", text: "Ausgef\xFChrt" };
    case "failed":
      if (!reason && a.output_hidden) return { tone: "error", text: `Fehlgeschlagen \u2013 ${OUTPUT_HIDDEN_HINT}` };
      return { tone: "error", text: `Fehlgeschlagen: ${reason ?? "unbekannter Fehler"}` };
    case "denied":
      if (a.gate_decision?.rule === "user:reject") return { tone: "neutral", text: reason ? `Abgelehnt: ${reason}` : "Abgelehnt" };
      return { tone: "error", text: reason ? `Gesperrt: ${reason}` : "Von einer Sicherheitsregel gesperrt" };
    case "approved":
    case "executing":
      return { tone: "pending", text: "L\xE4uft noch \u2026" };
    case "proposed":
      return { tone: "pending", text: "Wartet auf Freigabe" };
    case "expired":
      return { tone: "neutral", text: "Abgelaufen" };
    case "dismissed":
      return { tone: "neutral", text: "Verworfen" };
    default:
      return { tone: "neutral", text: a.status ? `Unbekannter Zustand (${a.status})` : "Unbekannter Zustand" };
  }
}
function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Abgebrochen", "AbortError"));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    function onAbort() {
      clearTimeout(timer);
      reject(new DOMException("Abgebrochen", "AbortError"));
    }
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}
async function waitForAction(actionId, last = {}, { signal, intervalMs = ACTION_POLL_INTERVAL_MS, maxMs = ACTION_POLL_MAX_MS } = {}) {
  const deadline = Date.now() + maxMs;
  let state = last;
  for (; ; ) {
    await sleep(intervalMs, signal);
    if (typeof document !== "undefined" && document.hidden) {
      if (Date.now() >= deadline) return state;
      continue;
    }
    try {
      const res = await authedFetch(`/actions/${encodeURIComponent(actionId)}`, { signal });
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        state = { ...state, ...body };
        if (!isActionRunning(state.status)) return state;
      } else if (res.status < 500) {
        throw new Error(errorFromBody(body, res.status));
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") throw err;
      if (!(err instanceof ServerUnavailableError)) throw err;
    }
    if (Date.now() >= deadline) return state;
  }
}
async function readJson(res) {
  return res.json().catch(() => ({}));
}
async function settleAction(proposal, options = {}) {
  let action = proposal;
  const id = action.id ?? action.action_id;
  let approved = false;
  if (action.status === "proposed" && id && options.approve !== false) {
    const risk = action.risk ?? options.risk;
    if (risk && deck().hasPermission(`actions.approve:${risk}`)) {
      const approveRes = await authedFetch(`/actions/${id}/approve`, { method: "POST" });
      const approveBody = await readJson(approveRes);
      if (!approveRes.ok) throw new Error(errorFromBody(approveBody, approveRes.status));
      action = { ...action, ...approveBody };
      approved = true;
    }
  }
  if (id && isActionRunning(action.status)) {
    try {
      action = await waitForAction(id, action, {
        signal: options.signal,
        intervalMs: options.pollIntervalMs,
        maxMs: options.pollMaxMs
      });
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) throw err;
      return { tone: "pending", text: RUNNING_IN_BACKGROUND, action, approved };
    }
  }
  return { ...describeActionOutcome(action), action, approved };
}
async function runAction(path, init = {}, options = {}) {
  const res = await authedFetch(path, init);
  const body = await readJson(res);
  if (!res.ok) throw new Error(errorFromBody(body, res.status));
  return settleAction(body, options);
}

// src/ids.ts
var EXT_ID = "shield";
var EXT_API = `/ext/${EXT_ID}`;

// src/api.ts
var HostFilter = createContext(null);
function useForHost(rows) {
  const host = useContext(HostFilter);
  return host ? rows.filter((r) => r.host_id === host) : rows;
}
var API = `${EXT_API}/defender`;
async function call(path, init = {}) {
  const res = await authedFetch(path, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorFromBody(body, res.status));
  return body;
}
function outcome(kind, text, links = []) {
  return links.length ? { kind, text, links } : { kind, text };
}
async function withReason(action) {
  const id = action.id ?? action.action_id;
  if (!id || actionReason(action)) return action;
  try {
    const res = await authedFetch(`/actions/${encodeURIComponent(id)}`);
    return res.ok ? { ...action, ...await res.json() } : action;
  } catch {
    return action;
  }
}
async function proposeAndApprove(path, body, risk, signal) {
  const { action } = await runAction(path, { method: "POST", body: JSON.stringify(body) }, { risk, signal });
  const id = action.id ?? action.action_id;
  switch (action.status) {
    case "succeeded":
      return outcome("ok", "Erledigt.");
    case "proposed":
      return outcome("warn", "Wartet auf Freigabe unter \u201EAktionen\u201C.", id ? [{ href: `/actions?action=${encodeURIComponent(id)}`, label: "Zur Freigabe" }] : []);
    case "failed":
    case "denied": {
      const { tone, text } = describeActionOutcome(await withReason(action));
      const needsAccess = text.includes("root-Rechte") && deck().hasPermission("hosts.write");
      return outcome(tone === "error" ? "error" : "warn", text, needsAccess ? [{ href: "/settings/hosts", label: "Zugang einrichten" }] : []);
    }
  }
  if (isActionRunning(action.status)) return outcome("info", RUNNING_IN_BACKGROUND);
  return outcome("warn", describeActionOutcome(action).text);
}
function useBusyKeys() {
  const [busy, setBusy] = useState3(/* @__PURE__ */ new Set());
  const running = useRef2(/* @__PURE__ */ new Set());
  const begin = useCallback2((key) => {
    if (running.current.has(key)) return false;
    running.current.add(key);
    setBusy(new Set(running.current));
    return true;
  }, []);
  const end = useCallback2((key) => {
    running.current.delete(key);
    setBusy(new Set(running.current));
  }, []);
  return { busy, begin, end };
}
function useActionNotices() {
  const [notices, setNotices] = useState3([]);
  const report = useCallback2((key, notice) => {
    setNotices((prev) => {
      const at = prev.findIndex(([k]) => k === key);
      if (!notice) return at < 0 ? prev : prev.filter((_, i) => i !== at);
      return at < 0 ? [...prev, [key, notice]] : prev.map((entry, i) => i === at ? [key, notice] : entry);
    });
  }, []);
  return { notices, report };
}
var PageActionsContext = createContext(null);
function usePageActions() {
  const keys = useBusyKeys();
  const signal = useUnmountSignal();
  const own = useActionNotices();
  const page = useContext(PageActionsContext);
  return page ? { ...page, notices: null } : { keys, signal, report: own.report, notices: own.notices };
}
async function track(page, key, label, progress, fn) {
  if (!page.keys.begin(key)) return;
  page.report(key, { kind: "info", text: progress });
  try {
    const result = await fn();
    page.report(key, { ...result, text: `${label}: ${result.text}` });
  } catch (err) {
    page.report(key, { kind: "error", text: `${label}: ${err instanceof Error ? err.message : String(err)}` });
  } finally {
    page.keys.end(key);
  }
}
function useReloadWhenSettled(busy, load) {
  const before = useRef2(busy.size);
  useEffect4(() => {
    if (busy.size < before.current) load();
    before.current = busy.size;
  }, [busy, load]);
}
function when(ts) {
  if (!ts) return "\u2013";
  return new Date(ts * 1e3).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}
function clock(ts) {
  if (!ts) return "\u2013";
  const d = new Date(ts * 1e3);
  if (d.toDateString() === (/* @__PURE__ */ new Date()).toDateString()) return d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  return when(ts);
}
function ago(ts) {
  if (!ts) return "nie";
  const s = Date.now() / 1e3 - ts;
  if (s < 90) return "gerade eben";
  if (s < 3600) return `vor ${Math.round(s / 60)} Min.`;
  if (s < 86400 * 2) return `vor ${Math.round(s / 3600)} Std.`;
  return `vor ${Math.round(s / 86400)} Tagen`;
}
function count(n, one, many) {
  return `${n.toLocaleString("de-DE")} ${n === 1 ? one : many}`;
}

// src/ContainerWatch.tsx
import { useCallback as useCallback3, useEffect as useEffect5, useState as useState4 } from "react";
import { Fragment as Fragment2, jsx as jsx3, jsxs as jsxs3 } from "react/jsx-runtime";
function withoutRelativeTime(text) {
  return text.replace(/\s*(?:about\s+|less than\s+)?(?:an?|\d+)\s*(?:second|minute|hour|day|week|month|year)s? ago\b/gi, "");
}
var STATUS_LABELS = {
  open: "Offen",
  proposed: "Aktion vorgeschlagen",
  reviewed: "Gepr\xFCft",
  resolved: "Erledigt",
  dismissed: "Verworfen"
};
var STATUS_TONE = {
  open: "bad",
  proposed: "warn",
  reviewed: "info",
  resolved: "good",
  dismissed: "neutral"
};
var LABEL_INCIDENT = "Vorfall erfasst und von Nodvard KI bewertet";
var LABEL_STATUS = "Status ge\xE4ndert";
var LABEL_REJECTED = "Vorschlag von Nodvard KI abgelehnt (Sperrliste)";
var AUDIT_ACTION_LABELS = {
  [`${EXT_ID}.incident`]: LABEL_INCIDENT,
  [`${EXT_ID}.incident_status`]: LABEL_STATUS,
  [`${EXT_ID}.proposal_rejected`]: LABEL_REJECTED,
  "nexus_soc.incident": LABEL_INCIDENT,
  "nexus_soc.incident_status": LABEL_STATUS,
  "nexus_soc.proposal_rejected": LABEL_REJECTED
};
function auditActionLabel(action) {
  return AUDIT_ACTION_LABELS[action] ?? action;
}
var OUTCOME_LABELS = {
  success: "ok",
  failure: "Fehler",
  denied: "abgelehnt",
  proposed: "vorgeschlagen"
};
var PAGE_SIZE = 25;
function formatTimestamp(unixSeconds) {
  return new Date(unixSeconds * 1e3).toLocaleString();
}
function dayBoundary(day, end) {
  const d = /* @__PURE__ */ new Date(`${day}T${end ? "23:59:59.999" : "00:00:00"}`);
  return d.toISOString();
}
function AuditTrail({ incidentId }) {
  const [entries, setEntries] = useState4(null);
  const [error, setError] = useState4(null);
  useEffect5(() => {
    call(`/audit?correlation_id=${encodeURIComponent(incidentId)}&limit=100`).then((rows) => setEntries([...rows].sort((a, b) => a.ts.localeCompare(b.ts)))).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [incidentId]);
  if (error) return /* @__PURE__ */ jsxs3("p", { className: "text-xs text-red-400", children: [
    "Verlauf nicht abrufbar: ",
    error
  ] });
  if (!entries) return /* @__PURE__ */ jsx3("p", { className: "text-xs opacity-60", children: "Lade Verlauf \u2026" });
  if (entries.length === 0) return /* @__PURE__ */ jsx3("p", { className: "text-xs opacity-60", children: "Kein Audit-Eintrag zu diesem Vorfall." });
  return /* @__PURE__ */ jsx3("ol", { className: "space-y-1 border-l border-white/10 pl-3 text-xs", "data-testid": `trail-${incidentId}`, children: entries.map((e) => /* @__PURE__ */ jsxs3("li", { children: [
    /* @__PURE__ */ jsx3("span", { className: "opacity-50", children: new Date(e.ts).toLocaleString() }),
    " ",
    /* @__PURE__ */ jsx3("span", { className: "font-medium", children: auditActionLabel(e.action) }),
    " ",
    /* @__PURE__ */ jsxs3("span", { className: "opacity-60", children: [
      "(",
      OUTCOME_LABELS[e.outcome] ?? e.outcome,
      ")"
    ] }),
    e.reason && /* @__PURE__ */ jsxs3("span", { className: "opacity-80", children: [
      " -- ",
      withoutRelativeTime(e.reason)
    ] })
  ] }, e.id)) });
}
function ContainerWatch() {
  const [page, setPage] = useState4(null);
  const [stats, setStats] = useState4(null);
  const [actionError, setActionError] = useState4(null);
  const [error, setError] = useState4(null);
  const [query, setQuery] = useState4("");
  const [appliedQuery, setAppliedQuery] = useState4("");
  const [statusFilter, setStatusFilter] = useState4("");
  const [hostFilter, setHostFilter] = useState4("");
  const [fromDay, setFromDay] = useState4("");
  const [toDay, setToDay] = useState4("");
  const [offset, setOffset] = useState4(0);
  const [openTrail, setOpenTrail] = useState4(null);
  const canReadAudit = deck().hasPermission("audit.read");
  const load = useCallback3(() => {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (appliedQuery) params.set("q", appliedQuery);
    if (statusFilter) params.set("status", statusFilter);
    if (hostFilter) params.set("host", hostFilter);
    if (fromDay) params.set("since", dayBoundary(fromDay, false));
    if (toDay) params.set("until", dayBoundary(toDay, true));
    Promise.all([
      call(`${EXT_API}/history?${params.toString()}`),
      call(`${EXT_API}/stats`)
    ]).then(([historyData, statsData]) => {
      setPage(historyData);
      setStats(statsData);
      setError(null);
    }).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [appliedQuery, statusFilter, hostFilter, fromDay, toDay, offset]);
  useEffect5(() => {
    load();
    const interval = setInterval(load, 3e4);
    return () => clearInterval(interval);
  }, [load]);
  useEffect5(() => {
    setOffset(0);
  }, [appliedQuery, statusFilter, hostFilter, fromDay, toDay]);
  async function changeStatus(id, verb) {
    setActionError(null);
    try {
      await call(`${EXT_API}/incidents/${id}/${verb}`, { method: "POST" });
    } catch (err) {
      setActionError(`Status lie\xDF sich nicht \xE4ndern: ${err instanceof Error ? err.message : String(err)}`);
    }
    load();
  }
  function resetFilters() {
    setQuery("");
    setAppliedQuery("");
    setStatusFilter("");
    setHostFilter("");
    setFromDay("");
    setToDay("");
  }
  if (!page && !stats && !error) return /* @__PURE__ */ jsx3("div", { className: "text-sm text-white/50", children: "Lade \u2026" });
  const total = page?.total ?? 0;
  const incidents = page?.items ?? [];
  const filtersActive = Boolean(appliedQuery || statusFilter || hostFilter || fromDay || toDay);
  return /* @__PURE__ */ jsxs3("div", { className: "space-y-5", children: [
    error && /* @__PURE__ */ jsx3(Notice, { text: `Fehler: ${error}` }),
    actionError && /* @__PURE__ */ jsx3(Notice, { text: actionError, onClose: () => setActionError(null) }),
    stats && /* @__PURE__ */ jsxs3("div", { className: "grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7", children: [
      ["open", "proposed", "reviewed", "resolved", "dismissed"].map((s) => /* @__PURE__ */ jsxs3(
        "button",
        {
          type: "button",
          onClick: () => setStatusFilter(statusFilter === s ? "" : s),
          "aria-pressed": statusFilter === s,
          className: `panel px-4 py-3 text-left transition hover:bg-white/[0.04] ${statusFilter === s ? "ring-2 ring-[var(--color-accent)]" : ""}`,
          children: [
            /* @__PURE__ */ jsx3("div", { className: `text-xl font-semibold tabular-nums ${s === "open" && (stats.by_status[s] ?? 0) > 0 ? "text-red-300" : ""}`, children: stats.by_status[s] ?? 0 }),
            /* @__PURE__ */ jsx3("div", { className: "text-xs text-white/50", children: STATUS_LABELS[s] })
          ]
        },
        s
      )),
      /* @__PURE__ */ jsx3(Stat, { label: "Wartet auf B\xFCndelung", value: stats.pending_batch }),
      /* @__PURE__ */ jsx3(Stat, { label: "Beobachtete Hosts", value: stats.watched_hosts })
    ] }),
    stats && (stats.ai_configured === false ? /* @__PURE__ */ jsxs3("div", { className: "flex items-center gap-2 text-xs", "data-testid": "ai-not-configured", children: [
      /* @__PURE__ */ jsx3("span", { className: "h-2 w-2 rounded-full bg-white/30" }),
      /* @__PURE__ */ jsx3("span", { className: "text-white/60", children: "Nodvard KI ist nicht eingerichtet (optional). Die Wache zeigt abgest\xFCrzte Container trotzdem an; Einsch\xE4tzungen und Vorschl\xE4ge gibt es mit einem KI-Server (Ollama) unter Einstellungen \u2192 Erweiterungen \u2192 Nodvard Shield." })
    ] }) : /* @__PURE__ */ jsxs3("div", { className: "flex items-center gap-2 text-xs", children: [
      /* @__PURE__ */ jsx3("span", { className: `h-2 w-2 rounded-full ${stats.ai_healthy ? "bg-emerald-400" : "bg-red-400"}` }),
      /* @__PURE__ */ jsxs3("span", { className: "text-white/60", children: [
        "Nodvard KI: ",
        stats.ai_healthy ? "erreichbar" : "nicht erreichbar",
        stats.ai_message ? ` -- ${stats.ai_message}` : ""
      ] })
    ] })),
    /* @__PURE__ */ jsx3(Card, { padded: true, children: /* @__PURE__ */ jsxs3(
      "form",
      {
        className: "flex flex-wrap items-end gap-3 text-sm",
        onSubmit: (e) => {
          e.preventDefault();
          setAppliedQuery(query.trim());
        },
        children: [
          /* @__PURE__ */ jsxs3("label", { className: "min-w-[14rem] flex-1", children: [
            /* @__PURE__ */ jsx3("span", { className: "mb-1.5 block text-xs text-white/55", children: "Suche" }),
            /* @__PURE__ */ jsx3(
              "input",
              {
                value: query,
                onChange: (e) => setQuery(e.target.value),
                placeholder: "Container, Meldung, KI-Text \u2026",
                "aria-label": "Historie durchsuchen",
                className: inputClass
              }
            )
          ] }),
          /* @__PURE__ */ jsxs3("label", { htmlFor: "soc-status-filter", children: [
            /* @__PURE__ */ jsx3("span", { className: "mb-1.5 block text-xs text-white/55", children: "Status" }),
            /* @__PURE__ */ jsxs3("select", { id: "soc-status-filter", value: statusFilter, onChange: (e) => setStatusFilter(e.target.value), className: `${inputClass} w-44`, children: [
              /* @__PURE__ */ jsx3("option", { value: "", children: "Alle" }),
              Object.entries(STATUS_LABELS).map(([value, label]) => /* @__PURE__ */ jsx3("option", { value, children: label }, value))
            ] })
          ] }),
          /* @__PURE__ */ jsxs3("label", { children: [
            /* @__PURE__ */ jsx3("span", { className: "mb-1.5 block text-xs text-white/55", children: "Host" }),
            /* @__PURE__ */ jsxs3("select", { value: hostFilter, onChange: (e) => setHostFilter(e.target.value), "aria-label": "Host-Filter", className: `${inputClass} w-40`, children: [
              /* @__PURE__ */ jsx3("option", { value: "", children: "Alle" }),
              (page?.hosts ?? []).map((h) => /* @__PURE__ */ jsx3("option", { value: h, children: h }, h))
            ] })
          ] }),
          /* @__PURE__ */ jsxs3("label", { children: [
            /* @__PURE__ */ jsx3("span", { className: "mb-1.5 block text-xs text-white/55", children: "Von" }),
            /* @__PURE__ */ jsx3("input", { type: "date", value: fromDay, onChange: (e) => setFromDay(e.target.value), "aria-label": "Von", className: `${inputClass} w-40` })
          ] }),
          /* @__PURE__ */ jsxs3("label", { children: [
            /* @__PURE__ */ jsx3("span", { className: "mb-1.5 block text-xs text-white/55", children: "Bis" }),
            /* @__PURE__ */ jsx3("input", { type: "date", value: toDay, onChange: (e) => setToDay(e.target.value), "aria-label": "Bis", className: `${inputClass} w-40` })
          ] }),
          /* @__PURE__ */ jsxs3(Button, { type: "submit", children: [
            /* @__PURE__ */ jsx3(Icon, { name: "search", size: 13 }),
            " Suchen"
          ] }),
          filtersActive && /* @__PURE__ */ jsx3(Button, { variant: "ghost", onClick: resetFilters, children: "Filter zur\xFCcksetzen" })
        ]
      }
    ) }),
    /* @__PURE__ */ jsx3("p", { className: "text-xs text-white/50", children: total === 0 ? "Keine Treffer." : `${offset + 1}\u2013${Math.min(offset + PAGE_SIZE, total)} von ${total} Vorf\xE4llen` }),
    incidents.length > 0 && /* @__PURE__ */ jsx3("div", { className: "panel divide-y divide-white/[0.06]", children: incidents.map((incident) => {
      const active = incident.status === "open" || incident.status === "proposed";
      return /* @__PURE__ */ jsxs3("div", { "data-testid": `incident-${incident.id}`, className: "px-5 py-4 text-sm", children: [
        /* @__PURE__ */ jsxs3("div", { className: "flex flex-wrap items-start justify-between gap-3", children: [
          /* @__PURE__ */ jsxs3("div", { className: "min-w-0 flex-1 basis-64", children: [
            /* @__PURE__ */ jsxs3("p", { className: "flex flex-wrap items-center gap-2", children: [
              /* @__PURE__ */ jsx3("span", { className: "font-medium", children: incident.host_name }),
              /* @__PURE__ */ jsxs3("span", { className: "text-white/50", children: [
                "/ ",
                incident.target
              ] }),
              /* @__PURE__ */ jsx3(Badge, { tone: STATUS_TONE[incident.status] ?? "neutral", children: STATUS_LABELS[incident.status] ?? incident.status_label ?? incident.status }),
              incident.is_crash && /* @__PURE__ */ jsx3(Badge, { tone: "bad", children: "Absturz" })
            ] }),
            /* @__PURE__ */ jsx3("p", { className: "mt-1 break-words text-white/80", children: incident.message }),
            incident.auto_resolved && /* @__PURE__ */ jsx3("p", { className: "mt-1 text-xs text-emerald-300", "data-testid": `auto-resolved-${incident.id}`, children: incident.auto_resolved }),
            incident.ai_summary && /* @__PURE__ */ jsx3("p", { className: "mt-2 whitespace-pre-wrap break-words rounded-lg border border-white/[0.06] bg-black/20 p-3 text-xs text-white/75", children: incident.ai_summary }),
            /* @__PURE__ */ jsxs3("p", { className: "mt-1.5 text-xs text-white/40", children: [
              formatTimestamp(incident.created_at),
              (incident.occurrences ?? 1) > 1 ? ` \xB7 ${incident.occurrences}\xD7 aufgetreten, zuletzt ${formatTimestamp(incident.last_seen ?? incident.created_at)}` : "",
              incident.status_changed_at ? ` \xB7 Status ge\xE4ndert ${formatTimestamp(incident.status_changed_at)}` : ""
            ] })
          ] }),
          /* @__PURE__ */ jsxs3("div", { className: "flex flex-wrap gap-1", children: [
            active && /* @__PURE__ */ jsxs3(Fragment2, { children: [
              /* @__PURE__ */ jsx3(Button, { small: true, onClick: () => void changeStatus(incident.id, "confirm"), children: "Best\xE4tigen" }),
              /* @__PURE__ */ jsx3(Button, { small: true, onClick: () => void changeStatus(incident.id, "resolve"), children: "Erledigt" }),
              /* @__PURE__ */ jsx3(Button, { small: true, variant: "ghost", onClick: () => void changeStatus(incident.id, "dismiss"), children: "Verwerfen" })
            ] }),
            !active && /* @__PURE__ */ jsx3(Button, { small: true, onClick: () => void changeStatus(incident.id, "reopen"), children: "Wieder \xF6ffnen" }),
            canReadAudit && /* @__PURE__ */ jsx3(
              "button",
              {
                type: "button",
                onClick: () => setOpenTrail(openTrail === incident.id ? null : incident.id),
                "aria-expanded": openTrail === incident.id,
                className: buttonClass("ghost", true),
                children: "Verlauf"
              }
            )
          ] })
        ] }),
        openTrail === incident.id && /* @__PURE__ */ jsx3("div", { className: "mt-3", children: /* @__PURE__ */ jsx3(AuditTrail, { incidentId: incident.id }) })
      ] }, incident.id);
    }) }),
    total > PAGE_SIZE && /* @__PURE__ */ jsxs3("div", { className: "flex items-center gap-2", children: [
      /* @__PURE__ */ jsx3(Button, { small: true, disabled: offset === 0, onClick: () => setOffset(Math.max(0, offset - PAGE_SIZE)), children: "\u2190 Neuere" }),
      /* @__PURE__ */ jsx3(Button, { small: true, disabled: offset + PAGE_SIZE >= total, onClick: () => setOffset(offset + PAGE_SIZE), children: "\xC4ltere \u2192" })
    ] })
  ] });
}

// src/GuardTab.tsx
import { useCallback as useCallback4, useContext as useContext2, useEffect as useEffect6, useState as useState5 } from "react";

// src/RunNotice.tsx
import { Fragment as Fragment3, jsx as jsx4, jsxs as jsxs4 } from "react/jsx-runtime";
var NOTICE_STYLE = {
  ok: "border-emerald-500/30 bg-emerald-500/10 text-emerald-200",
  error: "border-red-500/30 bg-red-500/10 text-red-200",
  warn: "border-amber-500/30 bg-amber-500/10 text-amber-200",
  info: "border-sky-500/30 bg-sky-500/10 text-sky-200"
};
function RunNotice({ notice, onClose }) {
  return /* @__PURE__ */ jsxs4("div", { role: notice.kind === "error" ? "alert" : "status", className: `mb-4 flex items-start justify-between gap-3 rounded-lg border px-3 py-2 text-sm ${NOTICE_STYLE[notice.kind]}`, children: [
    /* @__PURE__ */ jsxs4("span", { className: "min-w-0", children: [
      notice.text,
      notice.links?.map((l) => /* @__PURE__ */ jsx4("a", { href: l.href, className: "ml-2 whitespace-nowrap underline underline-offset-2 hover:text-white", children: l.label }, l.href))
    ] }),
    onClose && /* @__PURE__ */ jsx4("button", { type: "button", "aria-label": "Hinweis schlie\xDFen", onClick: onClose, className: "flex-none opacity-60 hover:opacity-100", children: /* @__PURE__ */ jsx4(Icon, { name: "x", size: 14 }) })
  ] });
}
function RunNotices({ notices, onClose }) {
  return /* @__PURE__ */ jsx4(Fragment3, { children: notices.map(([key, notice]) => /* @__PURE__ */ jsx4(RunNotice, { notice, onClose: () => onClose(key) }, key)) });
}

// src/ServerSetupLink.tsx
import { jsx as jsx5 } from "react/jsx-runtime";
function ServerSetupLink({ className = "" }) {
  if (!deck().hasPermission("hosts.write")) return null;
  return /* @__PURE__ */ jsx5("a", { href: "/settings/hosts", className: `underline underline-offset-2 ${className}`, children: "Zugang einrichten" });
}

// src/GuardTab.tsx
import { Fragment as Fragment4, jsx as jsx6, jsxs as jsxs5 } from "react/jsx-runtime";
function noProcessText(p) {
  if (p.unreadable) return "Programm nicht lesbar (kein root)";
  return p.dynamic ? "ohne Programm (Kernel)" : "unbekanntes Programm";
}
function portName(p) {
  const count2 = p.count && p.count > 1 ? ` (${p.count})` : "";
  if (p.span) return `${p.span[0] === p.span[1] ? `Port ${p.span[0]}` : `Ports ${p.span[0]}\u2013${p.span[1]}`}/${p.proto}${count2}`;
  if (p.dynamic) return `wechselnde Ports/${p.proto}${count2}`;
  return `${p.port}/${p.proto}`;
}
var SPAN_NOTE = "Ports in eigenen Bereichen meldet der Einbruchschutz nicht einzeln. Den Programmnamen kann sich jedes Programm selbst geben, auch eine Schadsoftware.";
function portTitle(p) {
  if (p.span) return `Eigener Bereich aus den Einstellungen \xB7 ${p.process ?? "?"} \xB7 ${SPAN_NOTE}`;
  if (p.dynamic) return `Zuf\xE4llige Ports, \xE4ndern sich nach jedem Neustart \xB7 ${p.process ?? (p.unreadable ? "Programm nicht lesbar (kein root)" : "ohne Programm")}`;
  return `${p.address}:${p.port} \xB7 ${p.process ?? "?"}${p.public ? "" : " \xB7 nur lokal"}`;
}
var SEVERITY = {
  critical: { label: "kritisch", tone: "bad" },
  warning: { label: "Warnung", tone: "warn" },
  info: { label: "Hinweis", tone: "info" }
};
var ACK_TAKES_OVER = {
  new_port: "Der Port gilt danach als bekannt und meldet sich nicht wieder.",
  file_changed: "Der neue Stand der Datei gilt danach als richtig."
};
var ACK_HINT_OTHER = "Das Ereignis wird abgehakt. Gesperrt oder ge\xE4ndert wird dadurch nichts.";
var ACK_ONLY_IF = {
  new_port: "Nur best\xE4tigen, wenn du den Dienst dahinter kennst.",
  file_changed: "Sp\xE4tere \xC4nderungen werden mit diesem Stand verglichen. Nur best\xE4tigen, wenn du die \xC4nderung selbst gemacht hast oder kennst."
};
var banKey = (hostId, ip) => `${hostId}:${ip}`;
var fail2banKey = (hostId) => `${hostId}:fail2ban`;
function EventDetail({ ev, onBan, banning }) {
  const d = ev.detail;
  return /* @__PURE__ */ jsxs5("div", { className: "mt-2 rounded-lg bg-black/25 p-3 text-xs", children: [
    d.changes && /* @__PURE__ */ jsx6("ul", { className: "space-y-1", children: d.changes.map((c) => /* @__PURE__ */ jsxs5("li", { className: "flex flex-wrap items-center gap-2", children: [
      /* @__PURE__ */ jsx6(Badge, { tone: SEVERITY[c.severity]?.tone ?? "neutral", children: c.change === "added" ? "neu" : c.change === "removed" ? "gel\xF6scht" : "ge\xE4ndert" }),
      /* @__PURE__ */ jsx6("span", { className: "font-mono", children: c.path }),
      /* @__PURE__ */ jsx6("span", { className: "text-white/50", children: c.note })
    ] }, c.path)) }),
    d.attackers && /* @__PURE__ */ jsx6("ul", { className: "space-y-1", children: d.attackers.map((a) => /* @__PURE__ */ jsxs5("li", { className: "flex flex-wrap items-center gap-2", children: [
      /* @__PURE__ */ jsx6("span", { className: "font-mono", children: a.ip }),
      /* @__PURE__ */ jsxs5("span", { className: "text-white/50", children: [
        a.count,
        "\xD7 \xB7 Benutzer: ",
        a.users.join(", ") || "\u2013"
      ] }),
      /* @__PURE__ */ jsx6("button", { type: "button", disabled: banning(a.ip), onClick: () => onBan(a.ip), className: "text-[var(--color-accent)] hover:underline disabled:opacity-50 disabled:no-underline", children: banning(a.ip) ? "Sperre \u2026" : "Sperren" })
    ] }, a.ip)) }),
    d.logins && /* @__PURE__ */ jsx6("ul", { className: "space-y-1", children: d.logins.map((l) => /* @__PURE__ */ jsxs5("li", { children: [
      /* @__PURE__ */ jsxs5("span", { className: "font-mono", children: [
        l.user,
        "@",
        l.ip
      ] }),
      " ",
      /* @__PURE__ */ jsxs5("span", { className: "text-white/50", children: [
        "\xB7 ",
        l.method,
        " \xB7 ",
        when(l.ts)
      ] })
    ] }, l.ip)) }),
    d.ports && /* @__PURE__ */ jsx6("ul", { className: "space-y-1", children: d.ports.map((p) => /* @__PURE__ */ jsxs5("li", { children: [
      /* @__PURE__ */ jsx6("span", { className: "font-mono", children: portName(p) }),
      " ",
      /* @__PURE__ */ jsxs5("span", { className: "text-white/50", children: [
        "\xB7 ",
        p.process ?? noProcessText(p),
        " \xB7 ",
        p.public ? `offen auf ${p.address}` : "nur lokal"
      ] })
    ] }, p.key)) })
  ] });
}
function GuardTab({ canManage }) {
  const page = usePageActions();
  const { keys: { busy: acting }, signal: unmountSignal } = page;
  const [data, setData] = useState5(null);
  const [events, setEvents] = useState5(null);
  const [showAll, setShowAll] = useState5(false);
  const [open, setOpen] = useState5(null);
  const [error, setError] = useState5(null);
  const [notice, setNotice] = useState5(null);
  const [checking, setChecking] = useState5(false);
  const load = useCallback4(() => {
    Promise.all([call(`${API}/guard`), call(`${API}/events${showAll ? "?all=true" : ""}`)]).then(([g, e]) => {
      setData(g);
      setEvents(e);
      setError(null);
    }).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [showAll]);
  useEffect6(() => {
    load();
  }, [load]);
  useReloadWhenSettled(acting, load);
  const busy = checking || !!data?.hosts.some((h) => h.checking);
  useEffect6(() => {
    if (!busy) return;
    const t = setInterval(() => {
      load();
      setChecking(false);
    }, 4e3);
    return () => clearInterval(t);
  }, [busy, load]);
  async function run(label, fn) {
    setNotice(null);
    try {
      const result = await fn();
      if (result) setNotice({ ...result, text: `${label}: ${result.text}` });
    } catch (err) {
      setNotice({ kind: "error", text: `${label}: ${err instanceof Error ? err.message : String(err)}` });
    }
    load();
  }
  const checkNow = (hostIds = "all") => run("Pr\xFCfung", async () => {
    const r = await call(`${API}/guard/check`, { method: "POST", body: JSON.stringify({ host_ids: hostIds }) });
    setChecking(true);
    return { kind: "ok", text: r.hosts ? `${r.hosts} Server werden gepr\xFCft \u2026` : "l\xE4uft bereits." };
  });
  const recheck = (hostId) => void call(`${API}/guard/check`, { method: "POST", body: JSON.stringify({ host_ids: [hostId] }) }).then(() => setChecking(true)).catch(() => void 0);
  const acknowledge = (ev) => run(ev.kind_label, async () => {
    if (ev.kind in ACK_TAKES_OVER) {
      const ok = await deck().confirmDialog(
        `\u201E${ev.title}\u201C auf \u201E${ev.host_name}\u201C als in Ordnung markieren? ${ACK_TAKES_OVER[ev.kind]} ${ACK_ONLY_IF[ev.kind]}`,
        { confirmLabel: "Ist in Ordnung" }
      );
      if (!ok) return null;
    }
    await call(`${API}/events/${ev.id}/acknowledge`, { method: "POST" });
    return { kind: "ok", text: ev.kind in ACK_TAKES_OVER ? "als in Ordnung markiert." : "zur Kenntnis genommen." };
  });
  const acknowledgeAll = (host) => run("Ereignisse", async () => {
    const ok = await deck().confirmDialog(
      `${host ? `Alle offenen Ereignisse von \u201E${host.name}\u201C abhaken?` : "Alle offenen Ereignisse abhaken?"} Neue Ports und ge\xE4nderte Dateien gelten danach als bekannt. Gesperrt oder ge\xE4ndert wird dadurch nichts.`,
      { confirmLabel: "Alle abhaken" }
    );
    if (!ok) return null;
    const scope = host ? `?host=${encodeURIComponent(host.id)}` : "";
    const r = await call(`${API}/events/acknowledge-all${scope}`, { method: "POST" });
    const kept = r.kept_open ?? 0;
    if (!kept) return { kind: "ok", text: `${r.acknowledged} abgehakt.` };
    throw new Error(
      `${r.acknowledged} abgehakt, ${kept} ${kept === 1 ? "bleibt" : "bleiben"} offen, weil ein Server den Stand seiner Dateien gerade nicht geliefert hat. Bitte gleich noch einmal versuchen.`
    );
  });
  async function ban(hostId, hostName, ip, unban = false) {
    const ok = await deck().confirmDialog(
      unban ? `${ip} auf \u201E${hostName}\u201C wieder freigeben?` : `${ip} auf \u201E${hostName}\u201C per Fail2ban sperren? Von dieser Adresse ist dann keine SSH-Verbindung mehr m\xF6glich.`,
      { confirmLabel: unban ? "Entsperren" : "Sperren", danger: !unban }
    );
    if (!ok) return;
    const progress = unban ? `${ip} wird auf \u201E${hostName}\u201C entsperrt \u2026` : `${ip} wird auf \u201E${hostName}\u201C gesperrt \u2026`;
    await track(page, banKey(hostId, ip), `${ip} ${unban ? "entsperren" : "sperren"}`, progress, async () => {
      const result = await proposeAndApprove(`${API}/hosts/${hostId}/ban`, { ip, unban }, "low", unmountSignal());
      recheck(hostId);
      return result;
    });
    load();
  }
  async function installFail2ban(h) {
    const ok = await deck().confirmDialog(
      `Fail2ban auf \u201E${h.host_name}\u201C installieren? Es sperrt Adressen automatisch, die zu oft ein falsches SSH-Passwort versuchen.`,
      { confirmLabel: "Installieren" }
    );
    if (!ok) return;
    const progress = `Fail2ban wird auf \u201E${h.host_name}\u201C installiert. Das kann einige Minuten dauern.`;
    await track(page, fail2banKey(h.host_id), `Fail2ban (${h.host_name})`, progress, async () => {
      const result = await proposeAndApprove(`${API}/hosts/${h.host_id}/install`, { package: "fail2ban" }, "medium", unmountSignal());
      recheck(h.host_id);
      return result;
    });
    load();
  }
  const hosts = useForHost(data?.hosts ?? []);
  const shownEvents = useForHost(events ?? []);
  const hostFilter = useContext2(HostFilter);
  const shownHost = hostFilter ? { id: hostFilter, name: hosts[0]?.host_name ?? shownEvents[0]?.host_name ?? hostFilter } : null;
  if (error) return /* @__PURE__ */ jsx6(Notice, { text: `Fehler: ${error}` });
  if (!data || !events) return /* @__PURE__ */ jsx6("p", { className: "text-sm text-white/50", children: "Lade \u2026" });
  const sm = data.summary;
  if (hosts.length === 0) {
    return /* @__PURE__ */ jsx6(EmptyState, { icon: "eye", title: "Keine Server", text: "Sobald Linux-Server mit SSH-Zugang angelegt sind, \xFCberwacht der Einbruchschutz ihre SSH-Anmeldungen, Ports und wichtigen Dateien.", action: /* @__PURE__ */ jsx6(ServerSetupLink, { className: "text-sm" }) });
  }
  return /* @__PURE__ */ jsxs5(Fragment4, { children: [
    page.notices && /* @__PURE__ */ jsx6(RunNotices, { notices: page.notices, onClose: (key) => page.report(key, null) }),
    notice && /* @__PURE__ */ jsx6(RunNotice, { notice, onClose: () => setNotice(null) }),
    /* @__PURE__ */ jsx6("p", { className: "mb-4 text-sm text-white/55", "data-testid": "guard-intro", children: "Der Einbruchschutz zeigt, wer sich von au\xDFen auf deinen Servern anzumelden versucht (per SSH, dem Fernzugang) und was sich auf ihnen ver\xE4ndert. Fail2ban ist ein kleines Programm, das Adressen sperrt, die zu oft ein falsches Passwort probieren." }),
    /* @__PURE__ */ jsxs5("div", { className: "mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4", children: [
      /* @__PURE__ */ jsx6(Stat, { label: "Fehlgeschlagene SSH-Anmeldungen", value: sm.failed_24h, hint: "letzte 24 Stunden", tone: sm.failed_24h > 100 ? "warn" : void 0 }),
      /* @__PURE__ */ jsx6(Stat, { label: "Angreifende Adressen", value: sm.attackers }),
      /* @__PURE__ */ jsx6(Stat, { label: "Von Fail2ban gesperrt", value: sm.banned, hint: `Fail2ban l\xE4uft auf ${sm.fail2ban_running}/${sm.hosts} Servern${sm.fail2ban_unreadable ? ` \xB7 bei ${sm.fail2ban_unreadable} nicht lesbar` : ""}`, tone: sm.fail2ban_running < sm.hosts ? "warn" : "good" }),
      /* @__PURE__ */ jsx6(Stat, { label: "Offene Ereignisse", value: sm.open_events, tone: sm.open_events ? "bad" : "good" })
    ] }),
    /* @__PURE__ */ jsxs5("div", { className: "mb-4 flex flex-wrap items-center gap-2", children: [
      /* @__PURE__ */ jsxs5(Badge, { tone: data.config.enabled ? "info" : "neutral", children: [
        /* @__PURE__ */ jsx6(Icon, { name: "clock", size: 11 }),
        " ",
        data.config.enabled ? `Pr\xFCfung alle ${data.config.interval_min} Min.` : "Automatische Pr\xFCfung aus"
      ] }),
      /* @__PURE__ */ jsxs5(Badge, { children: [
        "Warnung ab ",
        data.config.threshold,
        " Fehlversuchen"
      ] }),
      /* @__PURE__ */ jsxs5(Badge, { tone: data.config.file_watch ? "good" : "neutral", children: [
        "Datei-W\xE4chter ",
        data.config.file_watch ? "an" : "aus"
      ] }),
      /* @__PURE__ */ jsx6("span", { className: "flex-1" }),
      canManage && /* @__PURE__ */ jsxs5(Button, { onClick: () => void checkNow(), children: [
        /* @__PURE__ */ jsx6(Icon, { name: "refresh", size: 14 }),
        " Alle jetzt pr\xFCfen"
      ] })
    ] }),
    /* @__PURE__ */ jsx6(
      Card,
      {
        title: "Sicherheitsereignisse",
        description: "Was seit der letzten Best\xE4tigung aufgefallen ist. Jede Auff\xE4lligkeit wird genau einmal gemeldet.",
        padded: false,
        className: "mb-5",
        actions: /* @__PURE__ */ jsxs5("div", { className: "flex items-center gap-2", children: [
          /* @__PURE__ */ jsxs5("label", { className: "flex items-center gap-1.5 text-xs text-white/55", children: [
            /* @__PURE__ */ jsx6("input", { type: "checkbox", checked: showAll, onChange: (e) => setShowAll(e.target.checked) }),
            " auch erledigte"
          ] }),
          canManage && shownEvents.some((e) => !e.acknowledged) && /* @__PURE__ */ jsx6(Button, { small: true, onClick: () => void acknowledgeAll(shownHost), children: "Alle abhaken" })
        ] }),
        children: /* @__PURE__ */ jsxs5("ul", { className: "divide-y divide-white/[0.05]", "data-testid": "events", children: [
          shownEvents.length === 0 && /* @__PURE__ */ jsx6("li", { className: "px-5 py-6 text-sm text-emerald-300/80", children: "Keine offenen Ereignisse \u2013 alles ruhig." }),
          shownEvents.map((ev) => {
            const sev = SEVERITY[ev.severity] ?? SEVERITY.info;
            return /* @__PURE__ */ jsxs5("li", { className: `px-5 py-3 text-sm ${ev.acknowledged ? "opacity-55" : ""}`, "data-testid": `event-${ev.id}`, children: [
              /* @__PURE__ */ jsxs5("div", { className: "flex flex-wrap items-center gap-2", children: [
                /* @__PURE__ */ jsx6(Badge, { tone: sev.tone, children: sev.label }),
                /* @__PURE__ */ jsx6("span", { className: "text-white/60", children: ev.host_name }),
                /* @__PURE__ */ jsx6("span", { className: "font-medium", children: ev.title }),
                /* @__PURE__ */ jsx6("span", { className: "text-xs text-white/40", children: when(ev.created_at) }),
                /* @__PURE__ */ jsx6("span", { className: "flex-1" }),
                /* @__PURE__ */ jsx6("button", { type: "button", onClick: () => setOpen(open === ev.id ? null : ev.id), className: "text-xs text-white/50 hover:text-white", children: open === ev.id ? "weniger" : "Details" }),
                canManage && !ev.acknowledged && /* @__PURE__ */ jsx6(Button, { small: true, onClick: () => void acknowledge(ev), title: ACK_TAKES_OVER[ev.kind] ?? ACK_HINT_OTHER, children: ev.kind in ACK_TAKES_OVER ? "Ist in Ordnung" : "Zur Kenntnis genommen" })
              ] }),
              open === ev.id && /* @__PURE__ */ jsx6(EventDetail, { ev, onBan: (ip) => void ban(ev.host_id, ev.host_name, ip), banning: (ip) => acting.has(banKey(ev.host_id, ip)) })
            ] }, ev.id);
          })
        ] })
      }
    ),
    /* @__PURE__ */ jsx6("div", { className: "grid gap-4 xl:grid-cols-2", children: hosts.map((h) => {
      const v = h.view;
      const f2b = v?.fail2ban;
      return /* @__PURE__ */ jsx6(
        Card,
        {
          title: h.host_name,
          description: v?.checked_at ? `gepr\xFCft ${ago(v.checked_at)}` : "noch nicht gepr\xFCft",
          actions: /* @__PURE__ */ jsxs5("div", { className: "flex flex-wrap items-center gap-1.5", children: [
            h.checking && /* @__PURE__ */ jsx6(Badge, { tone: "info", children: "pr\xFCft \u2026" }),
            f2b === "running" && /* @__PURE__ */ jsx6(Badge, { tone: "good", children: "Fail2ban aktiv" }),
            f2b === "stopped" && /* @__PURE__ */ jsx6(Badge, { tone: "warn", children: "Fail2ban gestoppt" }),
            f2b === "none" && /* @__PURE__ */ jsx6(Badge, { tone: "warn", children: "ohne Fail2ban" }),
            f2b === "noaccess" && /* @__PURE__ */ jsx6(Badge, { tone: "neutral", children: "Fail2ban: Zustand nicht lesbar (root n\xF6tig)" }),
            h.open_events > 0 && /* @__PURE__ */ jsxs5(Badge, { tone: "bad", children: [
              h.open_events,
              " offen"
            ] }),
            canManage && /* @__PURE__ */ jsx6(Button, { small: true, variant: "ghost", ariaLabel: `${h.host_name} pr\xFCfen`, disabled: h.checking, onClick: () => void checkNow([h.host_id]), children: /* @__PURE__ */ jsx6(Icon, { name: "refresh", size: 12 }) })
          ] }),
          children: /* @__PURE__ */ jsxs5("div", { "data-testid": `guard-${h.host_id}`, children: [
            !v && /* @__PURE__ */ jsx6("p", { className: "text-sm text-white/50", children: "Noch keine Daten \u2013 \u201EAlle jetzt pr\xFCfen\u201C startet den ersten Blick." }),
            v?.error && /* @__PURE__ */ jsx6("p", { className: "text-sm text-amber-300", children: v.error }),
            v && !v.error && /* @__PURE__ */ jsxs5("div", { className: "space-y-4 text-sm", children: [
              (!v.is_root || !v.ssh_log_found) && /* @__PURE__ */ jsx6("p", { className: "rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-amber-200", children: !v.is_root ? "Ohne root-Rechte sind SSH-Protokoll, Fail2ban und Programmnamen der Ports nur eingeschr\xE4nkt lesbar. Zuf\xE4llige UDP-Ports ohne Programmnamen z\xE4hlen dann zusammen als ein Eintrag \u2013 ein neues Programm darunter f\xE4llt nicht auf." : "Kein SSH-Protokoll gefunden (journald/auth.log)." }),
              /* @__PURE__ */ jsxs5("section", { children: [
                /* @__PURE__ */ jsx6("p", { className: "mb-1.5 text-xs text-white/40", children: "SSH \u2013 letzte 24 Stunden" }),
                /* @__PURE__ */ jsxs5("p", { className: "mb-2", children: [
                  /* @__PURE__ */ jsxs5("span", { className: v.failed_24h ? "text-amber-300" : "text-emerald-300", children: [
                    v.failed_24h,
                    " Fehlversuch",
                    v.failed_24h === 1 ? "" : "e"
                  ] }),
                  /* @__PURE__ */ jsxs5("span", { className: "text-white/50", children: [
                    " von ",
                    v.attacker_count,
                    " Adresse",
                    v.attacker_count === 1 ? "" : "n"
                  ] }),
                  v.banned_count > 0 && /* @__PURE__ */ jsxs5("span", { className: "text-white/50", children: [
                    " \xB7 ",
                    v.banned_count,
                    " gesperrt"
                  ] })
                ] }),
                v.attackers.length > 0 && /* @__PURE__ */ jsx6("ul", { className: "space-y-1 text-xs", children: v.attackers.slice(0, 6).map((a) => /* @__PURE__ */ jsxs5("li", { className: "flex flex-wrap items-center gap-2", children: [
                  /* @__PURE__ */ jsx6("span", { className: "w-36 font-mono", children: a.ip }),
                  /* @__PURE__ */ jsxs5("span", { className: "tabular-nums text-white/70", children: [
                    a.count,
                    "\xD7"
                  ] }),
                  /* @__PURE__ */ jsx6("span", { className: "min-w-0 flex-1 truncate text-white/45", children: a.users.join(", ") }),
                  a.banned ? /* @__PURE__ */ jsxs5(Fragment4, { children: [
                    /* @__PURE__ */ jsx6(Badge, { tone: "good", children: "gesperrt" }),
                    canManage && /* @__PURE__ */ jsx6("button", { type: "button", disabled: acting.has(banKey(h.host_id, a.ip)), onClick: () => void ban(h.host_id, h.host_name, a.ip, true), className: "text-white/45 hover:text-white disabled:opacity-50", children: acting.has(banKey(h.host_id, a.ip)) ? "Entsperre \u2026" : "entsperren" })
                  ] }) : canManage && f2b === "running" ? /* @__PURE__ */ jsx6("button", { type: "button", disabled: acting.has(banKey(h.host_id, a.ip)), onClick: () => void ban(h.host_id, h.host_name, a.ip), className: "text-[var(--color-accent)] hover:underline disabled:opacity-50 disabled:no-underline", children: acting.has(banKey(h.host_id, a.ip)) ? "Sperre \u2026" : "Sperren" }) : null
                ] }, a.ip)) }),
                canManage && f2b === "none" && /* @__PURE__ */ jsx6("div", { className: "mt-2", children: /* @__PURE__ */ jsx6(Button, { small: true, disabled: acting.has(fail2banKey(h.host_id)), onClick: () => void installFail2ban(h), children: acting.has(fail2banKey(h.host_id)) ? "Installiere \u2026" : "Fail2ban installieren" }) })
              ] }),
              v.ssh_findings === null && /* @__PURE__ */ jsxs5("section", { "data-testid": `sshd-${h.host_id}`, children: [
                /* @__PURE__ */ jsx6("p", { className: "mb-1.5 text-xs text-white/40", children: "SSH-Einstellungen" }),
                /* @__PURE__ */ jsx6("p", { className: "text-xs text-white/50", children: v.is_root ? "SSH-Einstellungen konnten nicht gelesen werden." : "SSH-Einstellungen nicht lesbar \u2013 daf\xFCr braucht Nodvard Deck root oder sudo ohne Passwort." })
              ] }),
              v.ssh_findings && /* @__PURE__ */ jsxs5("section", { "data-testid": `sshd-${h.host_id}`, children: [
                /* @__PURE__ */ jsxs5("p", { className: "mb-1.5 text-xs text-white/40", children: [
                  "SSH-Einstellungen",
                  v.ssh_port ? ` \xB7 Port ${v.ssh_port}` : ""
                ] }),
                v.ssh_findings.length === 0 ? /* @__PURE__ */ jsx6("p", { className: "text-xs text-emerald-300/80", children: "Sicher eingestellt \u2013 nur Schl\xFCssel, kein root mit Passwort." }) : /* @__PURE__ */ jsx6("ul", { className: "space-y-1 text-xs", children: v.ssh_findings.map((f) => /* @__PURE__ */ jsxs5("li", { className: "flex flex-wrap items-center gap-2", children: [
                  /* @__PURE__ */ jsx6(Badge, { tone: SEVERITY[f.severity]?.tone ?? "neutral", children: SEVERITY[f.severity]?.label ?? f.severity }),
                  /* @__PURE__ */ jsx6("span", { children: f.text }),
                  /* @__PURE__ */ jsxs5("span", { className: "text-white/45", children: [
                    "\u2192 besser: ",
                    /* @__PURE__ */ jsx6("code", { className: "font-mono text-white/70", children: f.advice })
                  ] })
                ] }, f.key)) })
              ] }),
              v.logins.length > 0 && /* @__PURE__ */ jsxs5("section", { children: [
                /* @__PURE__ */ jsx6("p", { className: "mb-1.5 text-xs text-white/40", children: "Letzte Anmeldungen" }),
                /* @__PURE__ */ jsx6("ul", { className: "space-y-0.5 text-xs", children: v.logins.slice(0, 5).map((l, i) => /* @__PURE__ */ jsxs5("li", { children: [
                  /* @__PURE__ */ jsxs5("span", { className: "font-mono", children: [
                    l.user,
                    "@",
                    l.ip
                  ] }),
                  " ",
                  /* @__PURE__ */ jsxs5("span", { className: "text-white/45", children: [
                    "\xB7 ",
                    l.method === "publickey" ? "Schl\xFCssel" : l.method === "password" ? "Passwort" : l.method,
                    " \xB7 ",
                    l.ts ? when(l.ts) : "\u2013"
                  ] })
                ] }, `${l.ip}-${i}`)) })
              ] }),
              /* @__PURE__ */ jsxs5("section", { children: [
                /* @__PURE__ */ jsxs5("p", { className: "mb-1.5 text-xs text-white/40", children: [
                  "Offene Ports (",
                  v.ports.length,
                  ")"
                ] }),
                /* @__PURE__ */ jsxs5("div", { className: "flex flex-wrap gap-1.5", children: [
                  v.ports.map((p) => /* @__PURE__ */ jsxs5(
                    "span",
                    {
                      title: portTitle(p),
                      className: `rounded-md px-2 py-0.5 font-mono text-[11px] ${p.new ? "bg-red-500/20 text-red-200" : p.public ? "bg-white/[0.08] text-white/80" : "bg-white/[0.04] text-white/40"}`,
                      children: [
                        portName(p),
                        p.process ? ` ${p.process}` : "",
                        p.new ? " \xB7 neu" : ""
                      ]
                    },
                    p.key
                  )),
                  v.ports.length === 0 && /* @__PURE__ */ jsx6("span", { className: "text-xs text-white/45", children: "keine gefunden" })
                ] }),
                v.ports.some((p) => p.span) && /* @__PURE__ */ jsx6("p", { className: "mt-1.5 text-xs text-white/45", children: SPAN_NOTE })
              ] }),
              /* @__PURE__ */ jsxs5("section", { children: [
                /* @__PURE__ */ jsx6("p", { className: "mb-1.5 text-xs text-white/40", children: "Datei-W\xE4chter" }),
                v.files_pending.length === 0 ? /* @__PURE__ */ jsxs5("p", { className: "text-xs text-emerald-300/80", children: [
                  v.files_watched,
                  " Dateien \xFCberwacht \u2013 keine offenen \xC4nderungen."
                ] }) : /* @__PURE__ */ jsx6("ul", { className: "space-y-0.5 text-xs", children: v.files_pending.map((p) => /* @__PURE__ */ jsx6("li", { className: "font-mono text-amber-200", children: p }, p)) })
              ] })
            ] })
          ] })
        },
        h.host_id
      );
    }) })
  ] });
}

// src/LynisFinding.tsx
import { useState as useState6 } from "react";
import { jsx as jsx7, jsxs as jsxs6 } from "react/jsx-runtime";
var ID_RE = /^\s*([A-Z]{2,5}-\d{4}|LYNIS)\b/;
function lynisId(entry) {
  return ID_RE.exec(entry)?.[1] ?? null;
}
function LynisFinding({
  entry,
  help,
  tone
}) {
  const [open, setOpen] = useState6(false);
  const text = help?.[lynisId(entry) ?? ""];
  const color = tone === "warning" ? "text-red-200" : "text-white/70";
  if (!text) return /* @__PURE__ */ jsxs6("li", { className: color, children: [
    "\u2022 ",
    entry
  ] });
  return /* @__PURE__ */ jsxs6("li", { className: color, children: [
    /* @__PURE__ */ jsxs6(
      "button",
      {
        type: "button",
        onClick: () => setOpen(!open),
        "aria-expanded": open,
        className: "text-left hover:text-white",
        children: [
          "\u2022 ",
          text.title
        ]
      }
    ),
    /* @__PURE__ */ jsx7("span", { className: "ml-3 block text-[11px] text-white/40", children: entry }),
    open && /* @__PURE__ */ jsxs6("div", { className: "ml-3 mt-1 space-y-1 text-xs text-white/70", children: [
      /* @__PURE__ */ jsxs6("p", { children: [
        /* @__PURE__ */ jsx7("span", { className: "font-medium text-white/85", children: "Was hei\xDFt das?" }),
        " ",
        text.what
      ] }),
      /* @__PURE__ */ jsxs6("p", { children: [
        /* @__PURE__ */ jsx7("span", { className: "font-medium text-white/85", children: "Was kannst du tun?" }),
        " ",
        text.todo
      ] })
    ] })
  ] });
}
function topIssues(audits, limit = 5) {
  const byId = /* @__PURE__ */ new Map();
  for (const audit of audits) {
    if (audit.status !== "ok") continue;
    const entries = [
      ...audit.warnings.map((w) => [w, true]),
      ...audit.suggestions.map((s) => [s, false])
    ];
    for (const [entry, warning] of entries) {
      const id = lynisId(entry);
      const help = id ? audit.help?.[id] : void 0;
      if (!id || !help || (help.weight ?? 1) < 2) continue;
      const issue = byId.get(id) ?? { id, help, warning: false, hosts: [], hostIds: [] };
      issue.warning = issue.warning || warning;
      const hostKey = audit.host_id ?? audit.host_name;
      if (!issue.hostIds.includes(hostKey)) {
        issue.hostIds.push(hostKey);
        issue.hosts.push(audit.host_name);
      }
      byId.set(id, issue);
    }
  }
  return [...byId.values()].sort((a, b) => (b.help.weight ?? 1) - (a.help.weight ?? 1) || Number(b.warning) - Number(a.warning) || b.hostIds.length - a.hostIds.length || a.id.localeCompare(b.id)).slice(0, limit);
}
function TopIssues({ issues }) {
  const [open, setOpen] = useState6(null);
  if (issues.length === 0) return null;
  return /* @__PURE__ */ jsx7("ol", { className: "space-y-2 text-sm", "data-testid": "top-issues", children: issues.map((issue, i) => /* @__PURE__ */ jsxs6("li", { children: [
    /* @__PURE__ */ jsxs6(
      "button",
      {
        type: "button",
        onClick: () => setOpen(open === issue.id ? null : issue.id),
        "aria-expanded": open === issue.id,
        className: "text-left hover:text-white",
        children: [
          /* @__PURE__ */ jsxs6("span", { className: "mr-1.5 text-white/40", children: [
            i + 1,
            "."
          ] }),
          /* @__PURE__ */ jsx7("span", { className: issue.warning ? "text-red-200" : "", children: issue.help.title })
        ]
      }
    ),
    /* @__PURE__ */ jsxs6("span", { className: "ml-5 block text-xs text-white/45", children: [
      issue.help.weight === 3 ? "wichtig" : "sinnvoll",
      " \xB7 auf ",
      issue.hosts.join(", ")
    ] }),
    open === issue.id && /* @__PURE__ */ jsxs6("div", { className: "ml-5 mt-1 space-y-1 text-xs text-white/70", children: [
      /* @__PURE__ */ jsxs6("p", { children: [
        /* @__PURE__ */ jsx7("span", { className: "font-medium text-white/85", children: "Was hei\xDFt das?" }),
        " ",
        issue.help.what
      ] }),
      /* @__PURE__ */ jsxs6("p", { children: [
        /* @__PURE__ */ jsx7("span", { className: "font-medium text-white/85", children: "Was kannst du tun?" }),
        " ",
        issue.help.todo
      ] })
    ] })
  ] }, issue.id)) });
}

// src/UpdatesTab.tsx
import { useCallback as useCallback5, useContext as useContext3, useEffect as useEffect7, useState as useState7 } from "react";
import { Fragment as Fragment5, jsx as jsx8, jsxs as jsxs7 } from "react/jsx-runtime";
var ACTIONS_PATH = "/actions";
var SINGLE_APPROVAL_RISKS = ["high", "critical"];
var reasonText = (r) => (r ?? "").replace(/\.$/, "");
var RETRY_MS = 5e3;
var FOLLOW_KEY = "nodvard-shield-updates-follow";
var FOLLOW_START_MS = 10 * 60 * 1e3;
var FOLLOW_MAX_MS = 3 * 3600 * 1e3;
function readFollows() {
  try {
    const raw = JSON.parse(window.sessionStorage.getItem(FOLLOW_KEY) ?? "{}");
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
    const out = {};
    for (const [id, f] of Object.entries(raw)) {
      if (f && typeof f.host_name === "string" && typeof f.since === "number" && (f.prev_run === null || typeof f.prev_run === "string")) {
        out[id] = { host_name: f.host_name, prev_run: f.prev_run, since: f.since };
      }
    }
    return out;
  } catch {
    return {};
  }
}
function writeFollows(follows) {
  try {
    if (Object.keys(follows).length) window.sessionStorage.setItem(FOLLOW_KEY, JSON.stringify(follows));
    else window.sessionStorage.removeItem(FOLLOW_KEY);
  } catch {
  }
}
function without(record, keys) {
  const next = { ...record };
  for (const k of keys) delete next[k];
  return next;
}
function runNotice(name, run) {
  return run.status === "ok" ? { kind: "ok", text: `${name}: ${run.summary ?? "Erledigt."}` } : { kind: "error", text: `${name}: Fehlgeschlagen: ${run.summary ?? "unbekannter Fehler"}` };
}
var unattendedKey = (hostId) => `${hostId}:unattended`;
var RESUMED_TEXT = "Nodvard Deck wurde zwischendurch neu gestartet. Das Einspielen l\xE4uft auf dem Server weiter, das Ergebnis erscheint hier.";
function deckRestartHint(st, mode) {
  const names = mode === "security" ? st?.docker_packages?.security : mode === "all" ? st?.docker_packages?.all : void 0;
  if (!names?.length || st?.deck_host === false) return "";
  const which = names.length === 1 ? `ist das Docker-Paket ${names[0]}` : `sind die Docker-Pakete ${names.join(", ")}`;
  return st?.deck_host ? ` Dabei ${which}, und hier l\xE4uft Nodvard Deck: Es ist danach kurz nicht erreichbar und l\xE4dt von selbst neu.` : ` Dabei ${which}. L\xE4uft hier Nodvard Deck, ist es danach kurz nicht erreichbar und l\xE4dt von selbst neu.`;
}
var RUN_STATUS = {
  running: { label: "l\xE4uft", tone: "info" },
  ok: { label: "erfolgreich", tone: "good" },
  error: { label: "Fehler", tone: "bad" }
};
function uptime(s) {
  if (s == null) return "\u2013";
  const d = Math.floor(s / 86400);
  const h = Math.floor(s % 86400 / 3600);
  return d > 0 ? `${d} T ${h} Std.` : `${h} Std. ${Math.floor(s % 3600 / 60)} Min.`;
}
function clock2(ts) {
  return new Date(ts * 1e3).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
}
function hostState(h) {
  if (h.busy) return { label: h.busy === "reboot" ? "startet neu \u2026" : "spielt ein \u2026", tone: "info" };
  if (h.checking) return { label: "pr\xFCft \u2026", tone: "info" };
  const st = h.status;
  if (!st) return { label: "noch nicht gepr\xFCft", tone: "neutral" };
  if (st.unsupported) return { label: "nicht unterst\xFCtzt", tone: "neutral" };
  if (st.error && !st.manager) return { label: "Fehler", tone: "warn" };
  if (st.security_count > 0) return { label: `${st.security_count} Sicherheitsupdate${st.security_count === 1 ? "" : "s"}`, tone: "bad" };
  if (st.count > 0) return { label: `${st.count} Update${st.count === 1 ? "" : "s"}`, tone: "warn" };
  return { label: "aktuell", tone: "good" };
}
function UpdatesTab({ canManage }) {
  const unmountSignal = useUnmountSignal();
  const [data, setData] = useState7(null);
  const [error, setError] = useState7(null);
  const [notice, setNotice] = useState7(null);
  const page = usePageActions();
  const { keys: { busy: acting }, signal: pageSignal } = page;
  const [open, setOpen] = useState7(null);
  const [openRun, setOpenRun] = useState7(null);
  const [pending, setPending] = useState7({});
  const [proposing, setProposing] = useState7(null);
  const [proposed, setProposed] = useState7(null);
  const onlyHost = useContext3(HostFilter);
  const [follows, setFollows] = useState7(readFollows);
  const [unreachable, setUnreachable] = useState7(0);
  const load = useCallback5(() => {
    call(`${API}/updates`).then((d) => {
      setData(d);
      setError(null);
      setUnreachable(0);
    }).catch((err) => {
      if (err instanceof ServerUnavailableError) setUnreachable((n) => n + 1);
      else setError(err instanceof Error ? err.message : String(err));
    });
  }, []);
  useEffect7(() => {
    load();
  }, [load]);
  useReloadWhenSettled(acting, load);
  useEffect7(() => {
    if (!unreachable) return;
    const t = setTimeout(load, RETRY_MS);
    return () => clearTimeout(t);
  }, [unreachable, load]);
  useEffect7(() => writeFollows(follows), [follows]);
  const busy = Object.keys(pending).length > 0 || Object.keys(follows).length > 0 || !!data?.hosts.some((h) => h.checking || h.busy);
  useEffect7(() => {
    if (!busy) return;
    const t = setInterval(load, 4e3);
    return () => clearInterval(t);
  }, [busy, load]);
  useEffect7(() => {
    if (!data) return;
    const now = Date.now();
    const done = [];
    for (const [hostId, f] of Object.entries(follows)) {
      if (pending[hostId]) continue;
      const run = data.hosts.find((h) => h.host_id === hostId)?.last_run;
      if (run && run.id !== f.prev_run) {
        if (run.status !== "running") {
          setNotice(runNotice(f.host_name, run));
          done.push(hostId);
        } else if (now - f.since > FOLLOW_MAX_MS) done.push(hostId);
      } else if (now - f.since > FOLLOW_START_MS) done.push(hostId);
    }
    if (done.length) setFollows((fs) => without(fs, done));
  }, [data, follows, pending]);
  async function checkAll(hostIds = "all") {
    setNotice(null);
    try {
      const r = await call(`${API}/updates/check`, { method: "POST", body: JSON.stringify({ host_ids: hostIds }) });
      setNotice({ kind: "ok", text: r.hosts ? `${r.hosts} Server werden gepr\xFCft \u2026` : "Pr\xFCfung l\xE4uft bereits." });
      load();
    } catch (err) {
      setNotice({ kind: "error", text: err instanceof Error ? err.message : String(err) });
    }
  }
  const recheck = (hostId) => void call(`${API}/updates/check`, { method: "POST", body: JSON.stringify({ host_ids: [hostId] }) }).catch(() => void 0).finally(() => load());
  function proposable(mode) {
    return (data?.hosts ?? []).filter((h) => {
      const st = h.status;
      return !!st && !st.unsupported && !!st.manager && !h.busy && (mode === "security" ? st.security_count : st.count) > 0;
    });
  }
  async function proposeAll(mode) {
    const targets = proposable(mode);
    if (targets.length === 0) return;
    const n = targets.length;
    const where = n === 1 ? "1 Server" : `${n} Servern`;
    const distUpgrade = mode === "all" && !!data?.config.proxmox_dist_upgrade && targets.some((h) => h.status?.manager === "apt");
    const single = distUpgrade ? targets.filter((h) => h.status?.manager === "apt").map((h) => h.host_name) : [];
    const approval = single.length === 0 ? " Dort kannst du sie auch gesammelt freigeben." : single.length === n ? " Mit \u201Eapt-get dist-upgrade\u201C (kann Pakete entfernen oder ersetzen) haben sie hohes Risiko: Du gibst sie einzeln frei, sie stehen nicht in der Sammel-Freigabe." : ` Mit \u201Eapt-get dist-upgrade\u201C (kann Pakete entfernen oder ersetzen) haben die Vorschl\xE4ge f\xFCr ${single.join(", ")} hohes Risiko: Die gibst du einzeln frei, die \xFCbrigen auch gesammelt.`;
    const text = `Vorschl\xE4ge f\xFCr ${mode === "security" ? "die Sicherheitsupdates" : "alle Updates"} auf ${where} anlegen (${targets.map((h) => h.host_name).join(", ")})? Eingespielt wird erst nach deiner Freigabe unter \u201EAktionen\u201C \u2013 au\xDFer unter Einstellungen \u2192 Automatik steht \u201ESelbstst\xE4ndig handeln\u201C: Dann startet es bei passender Risikostufe sofort.` + approval;
    const ok = await deck().confirmDialog(text, { confirmLabel: "Vorschl\xE4ge anlegen", danger: distUpgrade });
    if (!ok) return;
    setNotice(null);
    setProposed(null);
    setProposing(mode);
    try {
      const r = await call(`${API}/updates/propose-all`, {
        method: "POST",
        body: JSON.stringify({ mode, host_ids: targets.map((h) => h.host_id) }),
        signal: unmountSignal()
      });
      setProposed(r);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setNotice({ kind: "error", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setProposing(null);
      load();
    }
  }
  async function apply(h, mode) {
    const st = h.status;
    const distUpgrade = mode === "all" && st?.manager === "apt" && !!data?.config.proxmox_dist_upgrade;
    const questions = {
      security: `${count(st?.security_count ?? 0, "Sicherheitsupdate", "Sicherheitsupdates")} auf \u201E${h.host_name}\u201C jetzt einspielen?${deckRestartHint(st, "security")}`,
      all: `${(st?.count ?? 0) === 1 ? "Das Update" : `Alle ${st?.count ?? 0} Updates`} auf \u201E${h.host_name}\u201C jetzt einspielen? Das kann einige Minuten dauern.${distUpgrade ? " Ist es ein Proxmox-Server, l\xE4uft dabei \u201Eapt-get dist-upgrade\u201C \u2013 das kann Pakete entfernen oder ersetzen." : ""}${deckRestartHint(st, "all")}`,
      cleanup: `Auf \u201E${h.host_name}\u201C nicht mehr ben\xF6tigte Pakete und alte Kernel entfernen und den Paket-Cache leeren?`,
      reboot: `\u201E${h.host_name}\u201C jetzt neu starten? Der Server ist dann ein paar Minuten nicht erreichbar \u2013 alle Dienste darauf auch.`
    };
    const labels = { security: "Einspielen", all: "Alle einspielen", cleanup: "Aufr\xE4umen", reboot: "Neu starten" };
    const ok = await deck().confirmDialog(questions[mode], { confirmLabel: labels[mode], danger: mode === "reboot" || distUpgrade });
    if (!ok) return;
    setPending((p) => ({ ...p, [h.host_id]: mode }));
    setNotice({ kind: "info", text: mode === "reboot" ? `Neustart von ${h.host_name} wird ausgel\xF6st \u2026` : `${h.host_name}: l\xE4uft \u2013 du kannst die Seite offen lassen oder weiterarbeiten.` });
    const prevRun = h.last_run?.id ?? null;
    if (mode !== "reboot") setFollows((fs) => ({ ...fs, [h.host_id]: { host_name: h.host_name, prev_run: prevRun, since: Date.now() } }));
    try {
      const result = await proposeAndApprove(`${API}/hosts/${h.host_id}/upgrade`, { mode }, mode === "reboot" ? "high" : "medium", unmountSignal());
      let notice2 = { ...result, text: `${h.host_name}: ${result.text}` };
      let keepFollowing = false;
      if (mode !== "reboot" && result.kind !== "ok") {
        const fresh = await call(`${API}/updates`).catch(() => null);
        if (fresh) setData(fresh);
        const run = fresh?.hosts.find((x) => x.host_id === h.host_id)?.last_run;
        if (run && run.id !== prevRun) {
          if (run.status === "running") {
            notice2 = { kind: "info", text: `${h.host_name}: ${RESUMED_TEXT}` };
            keepFollowing = true;
          } else notice2 = runNotice(h.host_name, run);
        }
      }
      if (!keepFollowing) setFollows((fs) => without(fs, [h.host_id]));
      setNotice(notice2);
    } catch (err) {
      if (!(err instanceof ServerUnavailableError)) setFollows((fs) => without(fs, [h.host_id]));
      setNotice({ kind: "error", text: `${h.host_name}: ${err instanceof Error ? err.message : String(err)}` });
    } finally {
      setPending((p) => {
        const next = { ...p };
        delete next[h.host_id];
        return next;
      });
      load();
    }
  }
  async function installUnattended(h) {
    const ok = await deck().confirmDialog(
      `Auf \u201E${h.host_name}\u201C automatische Sicherheitsupdates des Systems (unattended-upgrades) einrichten? Der Server spielt Sicherheitsupdates dann jeden Tag selbst ein.`,
      { confirmLabel: "Einrichten" }
    );
    if (!ok) return;
    const progress = `Automatische Sicherheitsupdates werden auf \u201E${h.host_name}\u201C eingerichtet. Das kann einige Minuten dauern.`;
    await track(page, unattendedKey(h.host_id), h.host_name, progress, async () => {
      const result = await proposeAndApprove(`${API}/hosts/${h.host_id}/install`, { package: "unattended" }, "medium", pageSignal());
      recheck(h.host_id);
      return result;
    });
  }
  const hosts = useForHost(data?.hosts ?? []);
  const runs = useForHost(data?.runs ?? []);
  const offline = unreachable > 0 && /* @__PURE__ */ jsx8("div", { "data-testid": "updates-offline", children: /* @__PURE__ */ jsx8(Notice, { text: "Nodvard Deck ist gerade nicht erreichbar, zum Beispiel weil es nach einem Update neu startet. Die Seite versucht es in ein paar Sekunden selbst wieder." }) });
  if (error) return /* @__PURE__ */ jsx8(Notice, { text: `Fehler: ${error}` });
  if (!data) return offline || /* @__PURE__ */ jsx8("p", { className: "text-sm text-white/50", children: "Lade \u2026" });
  const { summary: sm, config: c } = data;
  const unsupported = data.hosts.filter((h) => h.status?.unsupported).length;
  if (hosts.length === 0) {
    return /* @__PURE__ */ jsx8(EmptyState, { icon: "package", title: "Keine Server", text: "Sobald Linux-Server mit SSH-Zugang angelegt sind, zeigt die Update-Zentrale hier ihre anstehenden Updates.", action: /* @__PURE__ */ jsx8(ServerSetupLink, { className: "text-sm" }) });
  }
  return /* @__PURE__ */ jsxs7(Fragment5, { children: [
    offline,
    page.notices && /* @__PURE__ */ jsx8(RunNotices, { notices: page.notices, onClose: (key) => page.report(key, null) }),
    notice && /* @__PURE__ */ jsx8(RunNotice, { notice, onClose: () => setNotice(null) }),
    /* @__PURE__ */ jsxs7("div", { className: "mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4", children: [
      /* @__PURE__ */ jsx8(
        Stat,
        {
          label: "Server aktuell",
          value: `${sm.up_to_date}/${sm.hosts}`,
          tone: sm.up_to_date === sm.hosts ? "good" : void 0,
          hint: [
            sm.checked + unsupported < sm.hosts ? `${sm.hosts - sm.checked - unsupported} noch nicht gepr\xFCft` : "",
            unsupported ? `${unsupported} nicht unterst\xFCtzt` : ""
          ].filter(Boolean).join(" \xB7 ") || void 0
        }
      ),
      /* @__PURE__ */ jsx8(Stat, { label: "Offene Updates", value: sm.packages, tone: sm.packages ? "warn" : "good" }),
      /* @__PURE__ */ jsx8(Stat, { label: "Sicherheitsupdates", value: sm.security, tone: sm.security ? "bad" : "good" }),
      /* @__PURE__ */ jsx8(Stat, { label: "Neustart n\xF6tig", value: sm.reboot, tone: sm.reboot ? "warn" : void 0, hint: sm.errors ? `${count(sm.errors, "Pr\xFCfung", "Pr\xFCfungen")} fehlgeschlagen` : void 0 })
    ] }),
    /* @__PURE__ */ jsxs7("div", { className: "mb-4 flex flex-wrap items-center gap-2", children: [
      /* @__PURE__ */ jsxs7(Badge, { tone: c.check_enabled ? "info" : "neutral", children: [
        /* @__PURE__ */ jsx8(Icon, { name: "clock", size: 11 }),
        " Pr\xFCfung: ",
        c.check_enabled ? describeSchedule(c.check_cron, "aus") : "aus"
      ] }),
      /* @__PURE__ */ jsxs7(Badge, { tone: c.auto_enabled ? "good" : "neutral", children: [
        "Automatisch einspielen: ",
        c.auto_enabled ? `${c.auto_mode === "all" ? "alle Updates" : "Sicherheitsupdates"} \xB7 ${describeSchedule(c.auto_cron, "aus")}${c.auto_tag ? ` \xB7 nur \u201E${c.auto_tag}\u201C` : ""}` : "aus"
      ] }),
      c.auto_enabled && /* @__PURE__ */ jsxs7(Badge, { tone: c.auto_reboot ? "warn" : "neutral", children: [
        "Neustart danach: ",
        c.auto_reboot ? "automatisch" : "nein",
        c.auto_reboot && c.auto_reboot_skip?.length ? `, au\xDFer ${c.auto_reboot_skip.join(", ")}` : ""
      ] }),
      /* @__PURE__ */ jsx8("span", { className: "flex-1" }),
      canManage && !onlyHost && ["security", "all"].map((mode) => {
        const n = proposable(mode).length;
        if (n === 0) return null;
        return /* @__PURE__ */ jsx8(
          Button,
          {
            variant: mode === "security" ? "primary" : "secondary",
            disabled: proposing !== null,
            onClick: () => void proposeAll(mode),
            title: "Legt f\xFCr jeden dieser Server einen Vorschlag an. Freigegeben wird danach unter \u201EAktionen\u201C.",
            children: proposing === mode ? "Lege Vorschl\xE4ge an \u2026" : `${mode === "security" ? "Alle Sicherheitsupdates vorschlagen" : "Alle Updates vorschlagen"} (${n})`
          },
          mode
        );
      }),
      canManage && /* @__PURE__ */ jsxs7(Button, { onClick: () => void checkAll(), children: [
        /* @__PURE__ */ jsx8(Icon, { name: "refresh", size: 14 }),
        " Alle jetzt pr\xFCfen"
      ] })
    ] }),
    /* @__PURE__ */ jsx8("p", { className: "-mt-2 mb-4 text-xs text-white/50", "data-testid": "auto-explain", children: "Updates automatisch einspielen geht auf zwei Wegen: Nodvard Deck spielt sie nach seinem Zeitplan ein (\u201EUpdates automatisch einspielen\u201C in den Einstellungen von Nodvard Shield), oder ein Debian- oder Ubuntu-Server spielt Sicherheitsupdates jeden Tag selbst ein (unattended-upgrades). Einer der beiden Wege reicht." }),
    proposed && /* @__PURE__ */ jsx8(ProposedSummary, { result: proposed, onClose: () => setProposed(null) }),
    /* @__PURE__ */ jsx8("div", { className: "space-y-3", "data-testid": "update-hosts", children: hosts.map((h) => {
      const st = h.status;
      const state = hostState(h);
      const running = pending[h.host_id] ?? h.busy;
      const rebootSince = st?.reboot_pending_since;
      const needsReboot = !!st?.reboot_required && !rebootSince;
      const isOpen = open === h.host_id;
      return /* @__PURE__ */ jsxs7("div", { className: "panel px-4 py-3", "data-testid": `updates-${h.host_id}`, children: [
        /* @__PURE__ */ jsxs7("div", { className: "flex flex-wrap items-center gap-3", children: [
          /* @__PURE__ */ jsxs7("div", { className: "min-w-0 flex-1", children: [
            /* @__PURE__ */ jsxs7("p", { className: "flex flex-wrap items-center gap-2 text-sm font-medium", children: [
              h.host_name,
              /* @__PURE__ */ jsx8(Badge, { tone: state.tone, children: state.label }),
              needsReboot && !h.busy && /* @__PURE__ */ jsx8(Badge, { tone: "warn", children: "Neustart n\xF6tig" }),
              st?.unattended === true && /* @__PURE__ */ jsx8(Badge, { tone: "good", children: "Auto-Sicherheitsupdates an" })
            ] }),
            /* @__PURE__ */ jsxs7("p", { className: "mt-0.5 text-xs text-white/45", children: [
              st?.manager ? `${st.manager} \xB7 Kernel ${st.kernel ?? "?"} \xB7 l\xE4uft seit ${uptime(st.uptime_s)} \xB7 ` : "",
              st?.checked_at ? `gepr\xFCft ${ago(st.checked_at)}` : "noch nie erfolgreich gepr\xFCft",
              st?.error && !st.unsupported && st.attempted_at ? ` \xB7 letzter Versuch ${ago(st.attempted_at)} fehlgeschlagen` : ""
            ] }),
            h.check_overdue && /* @__PURE__ */ jsx8("p", { className: "mt-1 text-xs text-amber-300", "data-testid": `updates-overdue-${h.host_id}`, children: "Die geplante Pr\xFCfung ist hier ausgeblieben (das Dashboard lief zur Pr\xFCfzeit nicht). Der Stand kann veraltet sein \u2013 \u201EPr\xFCfen\u201C holt ihn nach." }),
            st?.error && /* @__PURE__ */ jsx8("p", { className: `mt-1 text-xs ${st.unsupported ? "text-white/50" : "text-amber-300"}`, children: st.error }),
            st?.refresh_error && /* @__PURE__ */ jsx8("p", { className: "mt-1 text-xs text-amber-300/80", children: st.refresh_error }),
            needsReboot && st.reboot_reasons.length > 0 && /* @__PURE__ */ jsxs7("p", { className: "mt-1 text-xs text-white/50", children: [
              "Grund: ",
              st.reboot_reasons.slice(0, 3).join(", ")
            ] }),
            rebootSince && !h.busy && /* @__PURE__ */ jsxs7("p", { className: "mt-1 text-xs text-sky-300", children: [
              "Neustart ausgel\xF6st (",
              clock2(rebootSince),
              ") \u2013 die n\xE4chste Pr\xFCfung zeigt den neuen Stand."
            ] }),
            h.last_run && /* @__PURE__ */ jsxs7("p", { className: "mt-1 text-xs text-white/45", children: [
              "Zuletzt: ",
              h.last_run.mode_label,
              " ",
              when(h.last_run.started_at),
              " \u2013",
              " ",
              /* @__PURE__ */ jsx8("span", { className: h.last_run.status === "error" ? "text-red-300" : "", children: h.last_run.summary ?? RUN_STATUS[h.last_run.status]?.label })
            ] })
          ] }),
          canManage && /* @__PURE__ */ jsxs7("div", { className: "flex flex-wrap gap-1", children: [
            !!st?.security_count && /* @__PURE__ */ jsx8(Button, { small: true, variant: "primary", disabled: !!running, onClick: () => void apply(h, "security"), children: "Sicherheitsupdates einspielen" }),
            !!st?.count && /* @__PURE__ */ jsx8(Button, { small: true, variant: st.security_count ? "secondary" : "primary", disabled: !!running, onClick: () => void apply(h, "all"), children: "Alle einspielen" }),
            needsReboot && /* @__PURE__ */ jsx8(Button, { small: true, variant: "danger", disabled: !!running, onClick: () => void apply(h, "reboot"), children: "Neu starten" }),
            /* @__PURE__ */ jsxs7(Button, { small: true, variant: "ghost", disabled: h.checking || !!running, onClick: () => void checkAll([h.host_id]), ariaLabel: `${h.host_name} pr\xFCfen`, children: [
              /* @__PURE__ */ jsx8(Icon, { name: "refresh", size: 12 }),
              " Pr\xFCfen"
            ] })
          ] })
        ] }),
        st && st.count > 0 && /* @__PURE__ */ jsxs7("button", { type: "button", onClick: () => setOpen(isOpen ? null : h.host_id), className: "mt-2 flex items-center gap-1 text-xs text-white/55 hover:text-white", children: [
          /* @__PURE__ */ jsx8(Icon, { name: isOpen ? "chevron-down" : "chevron-right", size: 12 }),
          " ",
          st.count,
          " Paket",
          st.count === 1 ? "" : "e",
          " anzeigen"
        ] }),
        isOpen && st && /* @__PURE__ */ jsx8("div", { className: "mt-2 max-h-80 overflow-auto rounded-lg border border-white/[0.06]", children: /* @__PURE__ */ jsxs7("table", { className: "w-full text-left text-xs", children: [
          /* @__PURE__ */ jsx8("thead", { className: "sticky top-0 bg-[var(--color-surface-head)] text-white/45", children: /* @__PURE__ */ jsxs7("tr", { children: [
            /* @__PURE__ */ jsx8("th", { className: "px-3 py-1.5 font-medium", children: "Paket" }),
            /* @__PURE__ */ jsx8("th", { className: "px-3 py-1.5 font-medium", children: "installiert" }),
            /* @__PURE__ */ jsx8("th", { className: "px-3 py-1.5 font-medium", children: "neu" }),
            /* @__PURE__ */ jsx8("th", { className: "px-3 py-1.5" })
          ] }) }),
          /* @__PURE__ */ jsx8("tbody", { className: "divide-y divide-white/[0.04]", children: st.packages.map((p) => /* @__PURE__ */ jsxs7("tr", { children: [
            /* @__PURE__ */ jsx8("td", { className: "px-3 py-1.5 font-mono", children: p.name }),
            /* @__PURE__ */ jsx8("td", { className: "px-3 py-1.5 font-mono text-white/50", children: p.current_version ?? "\u2013" }),
            /* @__PURE__ */ jsx8("td", { className: "px-3 py-1.5 font-mono", children: p.new_version }),
            /* @__PURE__ */ jsx8("td", { className: "px-3 py-1.5 text-right", children: p.security && /* @__PURE__ */ jsx8(Badge, { tone: "bad", children: "Sicherheit" }) })
          ] }, p.name)) })
        ] }) }),
        canManage && st?.manager === "apt" && /* @__PURE__ */ jsxs7("p", { className: "mt-2 flex flex-wrap items-center gap-2 text-xs text-white/50", children: [
          st.unattended === false && !(c.auto_enabled && !c.auto_tag) && /* @__PURE__ */ jsxs7(Fragment5, { children: [
            "Tipp: Der Server kann Sicherheitsupdates auch jeden Tag selbst einspielen.",
            /* @__PURE__ */ jsx8("button", { type: "button", disabled: acting.has(unattendedKey(h.host_id)), onClick: () => void installUnattended(h), className: "text-[var(--color-accent)] hover:underline disabled:opacity-50 disabled:no-underline", children: acting.has(unattendedKey(h.host_id)) ? "Richte ein \u2026" : "Einrichten" }),
            /* @__PURE__ */ jsx8("span", { className: "text-white/25", children: "\xB7" })
          ] }),
          /* @__PURE__ */ jsx8("button", { type: "button", onClick: () => void apply(h, "cleanup"), className: "text-white/60 hover:text-white hover:underline", children: "Aufr\xE4umen" })
        ] })
      ] }, h.host_id);
    }) }),
    /* @__PURE__ */ jsx8(Card, { title: "Verlauf", description: "Eingespielte Updates, Aufr\xE4umen und Neustarts \u2013 manuell und automatisch.", className: "mt-5", padded: false, children: /* @__PURE__ */ jsxs7("ul", { className: "divide-y divide-white/[0.05]", "data-testid": "update-runs", children: [
      runs.length === 0 && /* @__PURE__ */ jsx8("li", { className: "px-5 py-6 text-sm text-white/50", children: "Noch nichts eingespielt." }),
      runs.map((r) => {
        const rs = RUN_STATUS[r.status] ?? { label: r.status, tone: "neutral" };
        return /* @__PURE__ */ jsxs7("li", { className: "px-5 py-2.5 text-sm", children: [
          /* @__PURE__ */ jsxs7("div", { className: "flex flex-wrap items-center gap-2", children: [
            /* @__PURE__ */ jsx8(Badge, { tone: rs.tone, children: rs.label }),
            /* @__PURE__ */ jsx8("span", { className: "font-medium", children: r.host_name }),
            /* @__PURE__ */ jsx8("span", { className: "text-white/60", children: r.mode_label }),
            r.trigger === "schedule" && /* @__PURE__ */ jsx8(Badge, { children: "automatisch" }),
            /* @__PURE__ */ jsx8("span", { className: "text-xs text-white/40", children: when(r.started_at) }),
            /* @__PURE__ */ jsx8("span", { className: "min-w-0 flex-1 break-words text-xs text-white/55", children: r.summary }),
            r.output_tail && /* @__PURE__ */ jsx8("button", { type: "button", onClick: () => setOpenRun(openRun === r.id ? null : r.id), className: "text-xs text-white/50 hover:text-white", children: openRun === r.id ? "Ausgabe ausblenden" : "Ausgabe" })
          ] }),
          openRun === r.id && r.output_tail && /* @__PURE__ */ jsx8("pre", { className: "mt-2 max-h-72 overflow-auto rounded-lg bg-black/40 p-3 font-mono text-[11px] text-white/70", children: r.output_tail })
        ] }, r.id);
      })
    ] }) })
  ] });
}
function ProposedSummary({ result, onClose }) {
  const { counts: c, results } = result;
  const list = (kind) => results.filter((r) => r.result === kind).map((r) => `${r.name} (${reasonText(r.reason)})`).join("; ");
  const open = results.filter((r) => r.result === "proposed" && r.status === "proposed");
  const waiting = open.length;
  const singleOnly = open.filter((r) => SINGLE_APPROVAL_RISKS.includes(r.risk ?? "")).length;
  const collective = waiting - singleOnly;
  const started = c.proposed - waiting;
  const parts = [`${c.proposed} vorgeschlagen`, ...c.skipped ? [`${c.skipped} \xFCbersprungen`] : [], ...c.failed ? [`${c.failed} fehlgeschlagen`] : []];
  const tone = c.failed > 0 ? "border-amber-500/30 bg-amber-500/10 text-amber-200" : "border-emerald-500/30 bg-emerald-500/10 text-emerald-200";
  return /* @__PURE__ */ jsxs7("div", { role: "status", "data-testid": "propose-all-result", className: `mb-4 flex items-start justify-between gap-3 rounded-lg border px-3 py-2 text-sm ${tone}`, children: [
    /* @__PURE__ */ jsxs7("div", { className: "min-w-0 space-y-1", children: [
      /* @__PURE__ */ jsx8("p", { children: c.proposed === 0 ? "Es wurde kein Vorschlag angelegt." : `${parts.join(", ")}.` }),
      c.skipped > 0 && /* @__PURE__ */ jsxs7("p", { className: "break-words opacity-90", children: [
        "\xDCbersprungen: ",
        list("skipped"),
        "."
      ] }),
      c.failed > 0 && /* @__PURE__ */ jsxs7("p", { className: "break-words opacity-90", children: [
        "Fehlgeschlagen: ",
        list("failed"),
        "."
      ] }),
      started > 0 && /* @__PURE__ */ jsxs7("p", { className: "opacity-90", children: [
        started === 1 ? "Bei 1 Server hat" : `Bei ${started} Servern hat`,
        " die Automatik schon freigegeben \u2013 das Einspielen l\xE4uft bereits."
      ] }),
      waiting > 0 && /* @__PURE__ */ jsxs7("p", { children: [
        /* @__PURE__ */ jsx8("a", { href: ACTIONS_PATH, className: "font-medium underline", children: "Zu \u201EAktionen\u201C zum Freigeben" }),
        singleOnly === 0 ? collective > 1 ? " \u2013 dort geht es auch gesammelt." : "." : collective === 0 ? ` \u2013 ${singleOnly === 1 ? "dieser Vorschlag hat" : `diese ${singleOnly} Vorschl\xE4ge haben`} hohes Risiko und ${singleOnly === 1 ? "wird" : "werden"} einzeln freigegeben, nicht gesammelt.` : ` \u2013 ${collective === 1 ? "1 Vorschlag" : `${collective} Vorschl\xE4ge`} kannst du gesammelt freigeben, ${singleOnly === 1 ? "1 mit hohem Risiko" : `${singleOnly} mit hohem Risiko`} nur einzeln.`
      ] })
    ] }),
    /* @__PURE__ */ jsx8("button", { type: "button", "aria-label": "Hinweis schlie\xDFen", onClick: onClose, className: "opacity-60 hover:opacity-100", children: /* @__PURE__ */ jsx8(Icon, { name: "x", size: 14 }) })
  ] });
}

// src/WatchTemplates.tsx
import { useState as useState8 } from "react";
import { jsx as jsx9, jsxs as jsxs8 } from "react/jsx-runtime";
var KIND_LABEL = {
  samba: "Samba-Freigabe",
  nfs: "NFS-Freigabe",
  nextcloud: "Nextcloud-Daten"
};
function WatchTemplates() {
  const [data, setData] = useState8(null);
  const [busy, setBusy] = useState8(null);
  const [message, setMessage] = useState8(null);
  const canChange = deck().hasPermission("settings.write");
  async function search() {
    setBusy("search");
    setMessage(null);
    try {
      setData(await call(`${API}/watch-templates`));
    } catch (err) {
      setMessage({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(null);
    }
  }
  async function add(path) {
    setBusy(path);
    setMessage(null);
    try {
      const r = await call(`${API}/watch-paths`, {
        method: "POST",
        body: JSON.stringify({ path })
      });
      setData((d) => d && {
        ...d,
        suggestions: d.suggestions.map((s) => s.path === path ? { ...s, covered_by: r.covered_by ?? path } : s)
      });
      setMessage({
        ok: true,
        text: r.added ? `${path} ist jetzt im Echtzeit-W\xE4chter. Neue und hineinkopierte Dateien pr\xFCft er ab dem n\xE4chsten Lauf.` : `${path} wird schon \xFCberwacht (\xFCber ${r.covered_by}).`
      });
    } catch (err) {
      setMessage({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(null);
    }
  }
  return /* @__PURE__ */ jsx9(
    Card,
    {
      title: "W\xE4chter-Vorlagen",
      description: "Ordner, in denen fremde Dateien landen: Samba- und NFS-Freigaben, die Daten einer Nextcloud. Die Suche liest nur.",
      className: "mb-4",
      actions: /* @__PURE__ */ jsx9(Button, { small: true, onClick: () => void search(), disabled: busy !== null, children: busy === "search" ? "Suche \u2026" : data ? "Neu suchen" : "Auf den Servern suchen" }),
      children: /* @__PURE__ */ jsxs8("div", { className: "space-y-2 text-sm", "data-testid": "watch-templates", children: [
        message && /* @__PURE__ */ jsx9("p", { role: message.ok ? "status" : "alert", className: message.ok ? "text-emerald-300" : "text-red-300", children: message.text }),
        data && data.suggestions.length === 0 && /* @__PURE__ */ jsx9("p", { className: "text-white/55", children: "Keine Freigaben und keine Nextcloud gefunden. Ohne root bzw. \u201Esudo\u201C ohne Passwort fehlt auf einem Server meist die Nextcloud." }),
        data && data.suggestions.length > 0 && /* @__PURE__ */ jsx9("ul", { className: "divide-y divide-white/[0.06]", children: data.suggestions.map((s) => /* @__PURE__ */ jsxs8("li", { className: "flex flex-wrap items-center gap-2 py-2", children: [
          /* @__PURE__ */ jsxs8("div", { className: "min-w-0 flex-1", children: [
            /* @__PURE__ */ jsx9("p", { className: "break-all font-mono text-xs", children: s.path }),
            /* @__PURE__ */ jsxs8("p", { className: "text-xs text-white/50", children: [
              s.kinds.map((k) => KIND_LABEL[k] ?? k).join(", "),
              " \xB7 auf ",
              s.hosts.join(", ")
            ] })
          ] }),
          s.covered_by ? /* @__PURE__ */ jsx9("span", { className: "text-xs text-emerald-300", children: s.covered_by === s.path ? "wird \xFCberwacht" : `wird \xFCberwacht (\xFCber ${s.covered_by})` }) : s.excluded_by ? /* @__PURE__ */ jsxs8("span", { className: "text-xs text-amber-300", children: [
            "ausgenommen (\xFCber ",
            s.excluded_by,
            ") \u2013 dort pr\xFCft der W\xE4chter nicht"
          ] }) : canChange ? /* @__PURE__ */ jsx9(Button, { small: true, variant: "primary", disabled: busy !== null, onClick: () => void add(s.path), children: busy === s.path ? "F\xFCge hinzu \u2026" : "Zum W\xE4chter hinzuf\xFCgen" }) : /* @__PURE__ */ jsx9("span", { className: "text-xs text-white/45", children: "Hinzuf\xFCgen braucht das Recht, Einstellungen zu \xE4ndern." })
        ] }, s.path)) }),
        data && data.errors.length > 0 && /* @__PURE__ */ jsx9("ul", { className: "text-xs text-amber-300", children: data.errors.map((e) => /* @__PURE__ */ jsx9("li", { children: e }, e)) }),
        /* @__PURE__ */ jsx9("p", { className: "text-xs text-white/45", children: "Der W\xE4chter sieht bei jedem Lauf alle Dateien eines Ordners an. Ein sehr gro\xDFer Ordner (etwa eine Nextcloud mit vielen Tausend Dateien) kostet deshalb jedes Mal etwas Zeit. Entfernen geht unter \u201EEinstellungen \xE4ndern\u201C \u2192 \u201E\xDCberwachte Ordner\u201C." })
      ] })
    }
  );
}

// src/SocPage.tsx
import { Fragment as Fragment6, jsx as jsx10, jsxs as jsxs9 } from "react/jsx-runtime";
var AUDIT_DURATION_HINT = "Lynis braucht je nach Server einige Minuten bis \xFCber eine Stunde.";
function auditRunText(run) {
  if (!run) return "Audit l\xE4uft \u2026";
  if (run.waiting) return `Audit wartet seit ${clock(run.requested_at)}`;
  return `Audit l\xE4uft seit ${clock(run.started_at ?? run.requested_at)}`;
}
function AuditRunning({ run }) {
  const title = run?.waiting ? "Es laufen gerade schon mehrere Audits; dieses startet, sobald eines fertig ist." : `${AUDIT_DURATION_HINT} Das Ergebnis erscheint hier, sobald es fertig ist.`;
  return /* @__PURE__ */ jsx10("span", { title, "data-testid": "audit-running", children: /* @__PURE__ */ jsx10(Badge, { tone: "info", children: auditRunText(run) }) });
}
function auditStartNotice(r) {
  const audits = r.audits ?? [];
  const fresh = audits.filter((a) => !a.already_running);
  const running = audits.filter((a) => a.already_running);
  if (audits.length === 0) {
    return { kind: r.hosts ? "info" : "warn", text: r.hosts ? `H\xE4rtungs-Audit: ${r.hosts} Server werden gepr\xFCft. ${AUDIT_DURATION_HINT}` : "H\xE4rtungs-Audit: Kein Server verf\xFCgbar." };
  }
  const parts = [];
  if (fresh.length === 1) parts.push(`H\xE4rtungs-Audit auf \u201E${fresh[0].host_name}\u201C: ${auditRunText(fresh[0])}.`);
  else if (fresh.length > 1) parts.push(`H\xE4rtungs-Audit auf ${fresh.length} Servern: ${auditRunText(fresh[0])}.`);
  if (running.length === 1) {
    const r0 = running[0];
    parts.push(`Auf \u201E${r0.host_name}\u201C ${r0.waiting ? "wartet" : "l\xE4uft"} schon ein Audit (seit ${clock(r0.waiting ? r0.requested_at : r0.started_at ?? r0.requested_at)}); es wurde kein zweites gestartet.`);
  } else if (running.length > 1) {
    parts.push(`Auf ${running.length} Servern lief schon ein Audit; dort wurde kein zweites gestartet.`);
  }
  if (fresh.length) parts.push(`${AUDIT_DURATION_HINT} Das Ergebnis erscheint dann unter \u201EH\xE4rtung\u201C.`);
  return { kind: "info", text: parts.join(" ") };
}
var TABS = [
  { id: "overview", label: "\xDCbersicht" },
  { id: "scans", label: "Scans" },
  { id: "quarantine", label: "Quarant\xE4ne" },
  { id: "hardening", label: "H\xE4rtung" },
  { id: "updates", label: "Updates" },
  { id: "guard", label: "Einbruchschutz" },
  { id: "containers", label: "Container-Wache" }
];
var SCAN_STATUS = {
  running: { label: "l\xE4uft", tone: "info" },
  clean: { label: "sauber", tone: "good" },
  infected: { label: "Bedrohung gefunden", tone: "bad" },
  error: { label: "Fehler", tone: "warn" }
};
function MoreActions({ items }) {
  const ref = useRef3(null);
  const [open, setOpen] = useState9(false);
  const close = (refocus) => {
    const details = ref.current;
    if (!details) return;
    if (refocus) details.querySelector("summary")?.focus();
    details.open = false;
  };
  useEffect8(() => {
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
  return /* @__PURE__ */ jsxs9(
    "details",
    {
      ref,
      className: "relative",
      "data-testid": "shield-more",
      onToggle: (e) => setOpen(e.currentTarget.open),
      onBlur: (e) => {
        if (ref.current?.open && e.relatedTarget && !ref.current.contains(e.relatedTarget)) close(false);
      },
      children: [
        /* @__PURE__ */ jsxs9("summary", { title: "Weitere Aktionen", className: `${buttonClass("secondary")} cursor-pointer list-none [&::-webkit-details-marker]:hidden`, children: [
          "Mehr ",
          /* @__PURE__ */ jsx10(Icon, { name: "chevron-down", size: 14 })
        ] }),
        /* @__PURE__ */ jsx10("div", { className: "absolute left-0 z-20 mt-1 flex w-60 max-w-[calc(100vw-2rem)] flex-col rounded-xl border border-white/10 bg-[var(--color-surface)] p-1 shadow-xl", children: items.map((a) => /* @__PURE__ */ jsxs9(
          "button",
          {
            type: "button",
            disabled: a.disabled,
            title: a.title,
            onClick: () => {
              close(true);
              a.run();
            },
            className: "flex items-center gap-2 rounded-md px-3 py-2 text-left text-sm hover:bg-white/[0.06] disabled:opacity-50",
            children: [
              a.icon ? /* @__PURE__ */ jsx10(Icon, { name: a.icon, size: 14 }) : /* @__PURE__ */ jsx10("span", { className: "inline-block w-[14px]", "aria-hidden": true }),
              " ",
              a.label
            ]
          },
          a.id
        )) })
      ]
    }
  );
}
function findingsLast30Days(n) {
  return n ? `${count(n, "Fund", "Funde")} in 30 Tagen` : "keine Funde in 30 Tagen";
}
function hostScanBadge(h) {
  const open = h.open_findings ?? 0;
  if (open > 0) return { label: count(open, "offene Bedrohung", "offene Bedrohungen"), tone: "bad" };
  if (!h.last_scan) return null;
  if (h.last_scan.status === "infected" && h.last_scan_outcome === "quarantined") return { label: "in Quarant\xE4ne", tone: "warn" };
  if (h.last_scan.status === "infected" && h.last_scan_outcome === "accepted") return { label: "als harmlos markiert", tone: "neutral" };
  if (h.last_scan.status === "infected" && h.last_scan_outcome === "cleaned") return { label: "bereinigt", tone: "good" };
  return SCAN_STATUS[h.last_scan.status] ?? { label: h.last_scan.status, tone: "neutral" };
}
function ScanCell({ host: h }) {
  if (h.scanning) return /* @__PURE__ */ jsx10(Badge, { tone: "info", children: "Scan l\xE4uft \u2026" });
  const badge = hostScanBadge(h);
  if (!badge) return /* @__PURE__ */ jsx10("span", { className: "text-xs text-white/40", children: "noch nie" });
  return /* @__PURE__ */ jsxs9(Fragment6, { children: [
    /* @__PURE__ */ jsx10(Badge, { tone: badge.tone, children: badge.label }),
    /* @__PURE__ */ jsx10("p", { className: "mt-0.5 text-xs text-white/45", children: (h.open_findings ?? 0) > 0 ? "Details unter \u201EQuarant\xE4ne\u201C" : h.last_scan ? `${h.last_scan.kind_label} \xB7 ${ago(h.last_scan.started_at)}` : "" })
  ] });
}
var FINDING_STATUS = {
  detected: { label: "offen", tone: "bad" },
  quarantined: { label: "in Quarant\xE4ne", tone: "warn" },
  restored: { label: "wiederhergestellt", tone: "neutral" },
  deleted: { label: "gel\xF6scht", tone: "good" },
  ignored: { label: "ignoriert", tone: "neutral" }
};
function ageText(days) {
  if (days >= 14) return `${Math.floor(days / 7)} Wochen`;
  return days === 1 ? "1 Tag" : `${days} Tage`;
}
function HardeningCell({ host: h }) {
  const shown = h.last_audit?.hardening_index != null ? h.last_audit : h.last_ok_audit?.hardening_index != null ? h.last_ok_audit : null;
  const failed = h.last_audit && h.last_audit.status !== "ok" ? h.last_audit : null;
  return /* @__PURE__ */ jsxs9(Fragment6, { children: [
    shown && shown.hardening_index != null && /* @__PURE__ */ jsxs9("div", { className: "w-28", children: [
      /* @__PURE__ */ jsxs9("div", { className: "flex justify-between text-xs", children: [
        /* @__PURE__ */ jsx10("span", { children: shown.hardening_index }),
        /* @__PURE__ */ jsxs9("span", { className: "text-white/40", children: [
          shown.warnings,
          " Warn."
        ] })
      ] }),
      /* @__PURE__ */ jsx10("div", { className: "mt-1 h-1.5 rounded-full bg-white/10", children: /* @__PURE__ */ jsx10("div", { className: "h-1.5 rounded-full", style: { width: `${shown.hardening_index}%`, background: shown.hardening_index >= 70 ? "rgb(var(--emerald-400))" : "rgb(var(--amber-400))" } }) })
    ] }),
    failed && /* @__PURE__ */ jsxs9("p", { className: `${shown ? "mt-1 " : ""}text-xs text-amber-300`, title: failed.error ?? void 0, "data-testid": "audit-failed", children: [
      "Letztes Audit fehlgeschlagen (",
      ago(failed.created_at),
      ")"
    ] }),
    !shown && !failed && /* @__PURE__ */ jsx10("span", { className: "text-xs text-white/40", children: "\u2013" })
  ] });
}
function LoadError({ what, error, onRetry }) {
  return /* @__PURE__ */ jsxs9("div", { role: "alert", "data-testid": "load-error", className: "mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm", children: [
    /* @__PURE__ */ jsxs9("div", { className: "min-w-0 flex-1", children: [
      /* @__PURE__ */ jsxs9("p", { className: "text-red-200", children: [
        what,
        " lie\xDF sich gerade nicht laden: ",
        error
      ] }),
      /* @__PURE__ */ jsx10("p", { className: "text-xs text-white/55", children: "Das hei\xDFt nicht, dass alles in Ordnung ist." })
    ] }),
    /* @__PURE__ */ jsx10(Button, { small: true, onClick: onRetry, children: "Erneut versuchen" })
  ] });
}
function errorText(err) {
  return err instanceof Error ? err.message : String(err);
}
function briefingNotice(r) {
  const stored = `Briefing erstellt: \u201E${r.title}\u201C. Es steht unter \u201EMeldungen\u201C`;
  const inbox = { href: "/notifications", label: "Meldungen \xF6ffnen" };
  switch (r.push) {
    case "sent":
      return { kind: "ok", text: `${stored} und wurde auch als Push-Nachricht zugestellt.`, links: [inbox] };
    case "suppressed":
      return { kind: "warn", text: `${stored}. Als Push-Nachricht kommt es nicht an, weil gerade ein Wartungsfenster l\xE4uft.`, links: [inbox] };
    case "skipped": {
      const links = [inbox];
      if (deck().hasPermission("extensions.manage")) links.push({ href: "/settings/extensions/ntfy", label: "Mindest-Stufe \xE4ndern" });
      return {
        kind: "ok",
        text: `${stored}. Als Push-Nachricht kommt es nicht an, weil es unter der in den Push-Einstellungen gew\xE4hlten Mindest-Stufe liegt.`,
        links
      };
    }
    case "not_delivered": {
      const links = [inbox];
      if (deck().hasPermission("extensions.manage")) links.push({ href: "/settings/extensions/ntfy", label: "Push-Nachrichten einrichten" });
      return {
        kind: "warn",
        text: `${stored}. Aufs Handy kommt es erst, wenn Push-Nachrichten (ntfy) eingerichtet sind und der ntfy-Dienst erreichbar ist (nicht einer deiner Server).`,
        links
      };
    }
    default:
      return { kind: "ok", text: `${stored}.`, links: [inbox] };
  }
}
function ScoreRing({ score, reasons = [] }) {
  const r = 42;
  const c = 2 * Math.PI * r;
  const tone = score >= 80 ? "rgb(var(--emerald-400))" : score >= 50 ? "rgb(var(--amber-400))" : "rgb(var(--red-400))";
  const label = score >= 80 ? "Gut gesch\xFCtzt" : score >= 50 ? "Verbesserungsbedarf" : "Handlungsbedarf";
  return /* @__PURE__ */ jsxs9("div", { className: "flex min-w-0 flex-wrap items-center gap-5", children: [
    /* @__PURE__ */ jsxs9("svg", { width: "104", height: "104", viewBox: "0 0 104 104", role: "img", "aria-label": `Schutzwert ${score} von 100`, className: "flex-none", children: [
      /* @__PURE__ */ jsx10("circle", { cx: "52", cy: "52", r, fill: "none", stroke: "var(--color-track)", strokeWidth: "9" }),
      /* @__PURE__ */ jsx10(
        "circle",
        {
          cx: "52",
          cy: "52",
          r,
          fill: "none",
          stroke: tone,
          strokeWidth: "9",
          strokeLinecap: "round",
          strokeDasharray: `${c * score / 100} ${c}`,
          transform: "rotate(-90 52 52)"
        }
      ),
      /* @__PURE__ */ jsx10("text", { x: "52", y: "58", textAnchor: "middle", className: "fill-white text-[22px] font-semibold", children: score })
    ] }),
    /* @__PURE__ */ jsxs9("div", { className: "min-w-0 flex-1 basis-48", children: [
      /* @__PURE__ */ jsx10("p", { className: "text-xs text-white/45", children: "Schutzwert" }),
      /* @__PURE__ */ jsx10("p", { className: "text-lg font-semibold", style: { color: tone }, children: label }),
      /* @__PURE__ */ jsx10("p", { className: "mt-1 max-w-[16rem] text-xs text-white/45", children: "Berechnet aus: Virenscanner installiert (ClamAV), aktuelle Signaturen und Scans, offene Funde, wie sicher die Server eingestellt sind (H\xE4rtung), offene Sicherheitsupdates, Fail2ban, SSH-Einstellungen und offene Sicherheitsereignisse." }),
      reasons.length > 0 && /* @__PURE__ */ jsx10("ul", { className: "mt-2 max-w-[20rem] space-y-0.5 text-xs text-amber-200/80", "data-testid": "score-reasons", "aria-label": "Was den Schutzwert senkt", children: reasons.map((r2) => /* @__PURE__ */ jsxs9("li", { children: [
        "\u2013 ",
        r2
      ] }, r2)) })
    ] })
  ] });
}
function SocPage() {
  const unmountSignal = useUnmountSignal();
  const [urlParams, updateUrl] = useUrlParams();
  const tabParam = urlParams.get("tab");
  const tab = TABS.find((x) => x.id === tabParam)?.id ?? "overview";
  const setTab = useCallback6((t) => updateUrl({ tab: t === "overview" ? null : t }), [updateUrl]);
  const [overview, setOverview] = useState9(null);
  const [error, setError] = useState9(null);
  const [notice, setNotice] = useState9(null);
  const [briefingBusy, setBriefingBusy] = useState9(false);
  const briefingLock = useRef3(false);
  const pageBusy = useBusyKeys();
  const actionNotices = useActionNotices();
  const pageActions = useMemo2(
    () => ({ keys: pageBusy, signal: unmountSignal, report: actionNotices.report }),
    [pageBusy, unmountSignal, actionNotices.report]
  );
  const canManage = deck().hasPermission("soc.manage");
  const hostFilter = urlParams.get("host") || null;
  const showAllHosts = () => updateUrl({ host: null });
  const loadOverview = useCallback6((refresh = false) => {
    call(`${API}/overview${refresh ? "?refresh=true" : ""}`).then((o) => {
      setOverview(o);
      setError(null);
    }).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);
  useEffect8(() => {
    loadOverview();
  }, [loadOverview]);
  useEffect8(() => {
    document.querySelector('[role="tab"][aria-selected="true"]')?.scrollIntoView?.({ block: "nearest", inline: "center" });
  }, [tab]);
  const [side, setSide] = useState9({ updates: null, guard: null });
  useEffect8(() => {
    call(`${API}/updates`).then((u) => setSide((p) => ({ ...p, updates: u.summary }))).catch(() => void 0);
    call(`${API}/guard`).then((g) => setSide((p) => ({ ...p, guard: g.summary }))).catch(() => void 0);
  }, [tab]);
  const scanning = !!overview?.hosts.some((h) => h.scanning);
  const auditing = !!overview?.hosts.some((h) => h.auditing);
  useEffect8(() => {
    if (!scanning && !auditing) return;
    const t = setInterval(() => loadOverview(), scanning ? 5e3 : 15e3);
    return () => clearInterval(t);
  }, [scanning, auditing, loadOverview]);
  async function run(label, fn) {
    setNotice(null);
    try {
      const result = await fn();
      if (!result) return;
      setNotice({ ...result, text: `${label}: ${result.text}` });
      loadOverview();
    } catch (err) {
      setNotice({ kind: "error", text: `${label}: ${err instanceof Error ? err.message : String(err)}` });
    }
  }
  const startScan = (kind, hostIds = "all") => run(kind === "quick" ? "Schnellscan" : "Tiefenscan", async () => {
    const r = await call(`${API}/scans`, { method: "POST", body: JSON.stringify({ kind, host_ids: hostIds }) });
    return r.scans.length ? { kind: "ok", text: `${r.scans.length} Server werden gepr\xFCft.` } : { kind: "warn", text: "L\xE4uft bereits oder kein Server verf\xFCgbar." };
  });
  async function startAudit(hostIds = "all") {
    setNotice(null);
    try {
      const r = await call(`${API}/audits`, { method: "POST", body: JSON.stringify({ host_ids: hostIds }) });
      setNotice(auditStartNotice(r));
      const runs = new Map((r.audits ?? []).map((a) => [a.host_id, a]));
      if (runs.size) {
        setOverview((o) => o && {
          ...o,
          hosts: o.hosts.map((h) => {
            const a = runs.get(h.host_id);
            return a ? { ...h, auditing: true, audit_run: { run_id: a.run_id, requested_at: a.requested_at, started_at: a.started_at, trigger: a.trigger, waiting: a.waiting } } : h;
          })
        });
      }
      loadOverview();
    } catch (err) {
      setNotice({ kind: "error", text: `H\xE4rtungs-Audit: ${err instanceof Error ? err.message : String(err)}` });
    }
  }
  async function sendBriefing() {
    if (briefingLock.current) return;
    briefingLock.current = true;
    setBriefingBusy(true);
    setNotice({ kind: "info", text: "Briefing wird erstellt \u2026 Das kann bis zu einer halben Minute dauern, wenn ein Server nicht antwortet." });
    try {
      setNotice(briefingNotice(await call(`${API}/briefing`, { method: "POST" })));
      loadOverview();
    } catch (err) {
      setNotice({ kind: "error", text: `Briefing: ${err instanceof Error ? err.message : String(err)}` });
    } finally {
      briefingLock.current = false;
      setBriefingBusy(false);
    }
  }
  async function confirmAll(kind) {
    if (!await confirmOnAllServers(kind, overview?.hosts.length ?? 0)) return;
    if (kind === "deep") await startScan("deep");
    else await startAudit();
  }
  async function install(host, pkg) {
    const ok = await deck().confirmDialog(
      pkg === "signatures" ? `Virensignaturen auf \u201E${host.host_name}\u201C jetzt aktualisieren? Das automatische Signatur-Update wird dabei eingeschaltet.` : `${INSTALL_NAME[pkg]} auf \u201E${host.host_name}\u201C installieren?`,
      { confirmLabel: pkg === "signatures" ? "Aktualisieren" : "Installieren" }
    );
    if (!ok) return;
    const label = pkg === "signatures" ? `Signaturen (${host.host_name})` : `${INSTALL_NAME[pkg]} installieren (${host.host_name})`;
    await track(pageActions, installKey(host.host_id, pkg), label, installProgress(pkg, host.host_name), () => proposeAndApprove(`${API}/hosts/${host.host_id}/install`, { package: pkg }, pkg === "signatures" ? "low" : "medium", unmountSignal()));
    loadOverview(true);
  }
  const headerActions = [
    {
      id: "briefing",
      label: briefingBusy ? "Briefing wird erstellt \u2026" : "Briefing senden",
      disabled: briefingBusy,
      run: () => void sendBriefing(),
      title: "Schickt dir jetzt den Lagebericht: Server, Virenschutz, Updates und Einbruchschutz auf einen Blick."
    },
    {
      id: "audit",
      label: "H\xE4rtungs-Audit",
      icon: "clock",
      run: () => void confirmAll("audit"),
      title: "Lynis pr\xFCft, wie sicher deine Server eingestellt sind (dauert einige Minuten)."
    },
    {
      id: "deep",
      label: "Tiefenscan",
      icon: "search",
      run: () => void confirmAll("deep"),
      title: "Pr\xFCft das ganze System auf Schadsoftware \u2013 das kann Stunden dauern."
    },
    {
      id: "quick",
      label: "Schnellscan starten",
      icon: "play",
      primary: true,
      run: () => void startScan("quick"),
      title: "Pr\xFCft typische Ablageorte auf Schadsoftware \u2013 in wenigen Minuten fertig."
    }
  ];
  return /* @__PURE__ */ jsxs9(
    Page,
    {
      title: "Nodvard Shield",
      description: "Sch\xFCtzt deine Server: Virenscans, Sicherheitsupdates, Einbruchschutz und eine Pr\xFCfung, wie sicher alles eingestellt ist \u2013 plus die KI-Wache (Nodvard KI) f\xFCr abgest\xFCrzte Docker-Container.",
      actions: canManage && // Die vier Knöpfe gehören zu Übersicht und Scans. Auf den anderen Reitern schoben sie am Handy den Inhalt weit
      // nach unten: dort stehen sie unter „Mehr“.
      (tab === "overview" || tab === "scans" ? headerActions.map((a) => /* @__PURE__ */ jsxs9(Button, { variant: a.primary ? "primary" : "secondary", onClick: a.run, disabled: a.disabled, title: a.title, children: [
        a.icon && /* @__PURE__ */ jsx10(Icon, { name: a.icon, size: 14 }),
        " ",
        a.label
      ] }, a.id)) : /* @__PURE__ */ jsx10(MoreActions, { items: headerActions })),
      children: [
        /* @__PURE__ */ jsx10("nav", { role: "tablist", "aria-label": "Bereiche", className: "mb-5 flex gap-1 overflow-x-auto border-b border-white/[0.08] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden", children: TABS.map((t) => /* @__PURE__ */ jsxs9(
          "button",
          {
            role: "tab",
            "aria-selected": tab === t.id,
            onClick: () => setTab(t.id),
            className: `-mb-px whitespace-nowrap border-b-2 px-3.5 py-2 text-sm transition ${tab === t.id ? "border-[var(--color-accent)] text-white" : "border-transparent text-white/55 hover:text-white"}`,
            children: [
              t.label,
              t.id === "quarantine" && overview && overview.summary.open_threats > 0 && /* @__PURE__ */ jsx10("span", { className: "ml-1.5 rounded-full bg-red-500/80 px-1.5 text-[10px] text-on-accent", children: overview.summary.open_threats }),
              t.id === "updates" && !!side.updates?.security && /* @__PURE__ */ jsx10("span", { className: "ml-1.5 rounded-full bg-red-500/80 px-1.5 text-[10px] text-on-accent", title: "Sicherheitsupdates", children: side.updates.security }),
              t.id === "guard" && !!side.guard?.open_events && /* @__PURE__ */ jsx10("span", { className: "ml-1.5 rounded-full bg-red-500/80 px-1.5 text-[10px] text-on-accent", title: "offene Ereignisse", children: side.guard.open_events })
            ]
          },
          t.id
        )) }),
        hostFilter && /* @__PURE__ */ jsxs9("div", { className: "mb-4 flex flex-wrap items-center gap-2 rounded-lg border border-[color-mix(in_srgb,var(--color-accent)_35%,transparent)] bg-[color-mix(in_srgb,var(--color-accent)_10%,transparent)] px-3 py-2 text-sm", "data-testid": "host-filter", children: [
          /* @__PURE__ */ jsxs9("span", { children: [
            "Ansicht f\xFCr ",
            /* @__PURE__ */ jsx10("strong", { children: overview?.hosts.find((h) => h.host_id === hostFilter)?.host_name ?? "diesen Server" })
          ] }),
          /* @__PURE__ */ jsx10("span", { className: "flex-1" }),
          /* @__PURE__ */ jsx10("button", { type: "button", onClick: showAllHosts, className: "text-xs text-white/70 hover:text-white hover:underline", children: "Alle Server anzeigen" })
        ] }),
        /* @__PURE__ */ jsx10(HostFilter.Provider, { value: hostFilter, children: /* @__PURE__ */ jsxs9(PageActionsContext.Provider, { value: pageActions, children: [
          notice && /* @__PURE__ */ jsx10(RunNotice, { notice, onClose: () => setNotice(null) }),
          /* @__PURE__ */ jsx10(RunNotices, { notices: actionNotices.notices, onClose: (key) => actionNotices.report(key, null) }),
          error && ["overview", "scans", "quarantine", "hardening"].includes(tab) && /* @__PURE__ */ jsx10(Notice, { text: `Fehler: ${error}` }),
          tab === "overview" && overview && /* @__PURE__ */ jsx10(
            OverviewTab,
            {
              overview,
              canManage,
              onRefresh: () => loadOverview(true),
              onScan: (h) => void startScan("quick", [h.host_id]),
              onAudit: (h) => void startAudit([h.host_id]),
              onInstall: (h, p) => void install(h, p),
              installing: pageBusy.busy,
              onOpenThreats: () => setTab("quarantine"),
              side,
              onOpen: setTab
            }
          ),
          tab === "overview" && !overview && !error && /* @__PURE__ */ jsx10("p", { className: "text-sm text-white/50", children: "Lade \u2026" }),
          tab === "scans" && /* @__PURE__ */ jsx10(ScansTab, { hosts: overview?.hosts ?? [], canManage, onStarted: () => loadOverview() }),
          tab === "quarantine" && /* @__PURE__ */ jsx10(QuarantineTab, { canManage, onChanged: () => loadOverview() }),
          tab === "hardening" && /* @__PURE__ */ jsx10(HardeningTab, { canManage, hosts: overview?.hosts ?? [], onAudit: (ids) => void (ids === "all" ? confirmAll("audit") : startAudit(ids)) }),
          tab === "updates" && /* @__PURE__ */ jsx10(UpdatesTab, { canManage }),
          tab === "guard" && /* @__PURE__ */ jsx10(GuardTab, { canManage }),
          tab === "containers" && /* @__PURE__ */ jsx10(ContainerWatch, {})
        ] }) })
      ]
    }
  );
}
function SideLink({ title, text, tone, onClick }) {
  const dot = tone === "good" ? "bg-emerald-400" : tone === "warn" ? "bg-amber-400" : "bg-red-400";
  return /* @__PURE__ */ jsxs9("button", { type: "button", onClick, className: "panel flex w-full min-w-0 items-center gap-3 px-4 py-3 text-left transition hover:bg-white/[0.04]", children: [
    /* @__PURE__ */ jsx10("span", { className: `h-2.5 w-2.5 flex-none rounded-full ${dot}` }),
    /* @__PURE__ */ jsxs9("span", { className: "min-w-0 flex-1", children: [
      /* @__PURE__ */ jsx10("span", { className: "block text-xs text-white/50", children: title }),
      /* @__PURE__ */ jsx10("span", { className: "block truncate text-sm", title: text, children: text })
    ] }),
    /* @__PURE__ */ jsx10("span", { className: "flex-none text-white/35", children: "\u2192" })
  ] });
}
var INSTALL_NAME = { clamav: "ClamAV", lynis: "Lynis", signatures: "Signaturen" };
var installKey = (hostId, pkg) => `${hostId}:${pkg}`;
var installRunningLabel = (pkg) => pkg === "signatures" ? "Aktualisiere \u2026" : "Installiere \u2026";
function installProgress(pkg, hostName) {
  const what = pkg === "signatures" ? `Die Virensignaturen auf \u201E${hostName}\u201C werden aktualisiert` : `${INSTALL_NAME[pkg]} wird auf \u201E${hostName}\u201C installiert`;
  return `${what}. Das kann einige Minuten dauern.`;
}
function nextStep(hosts, openThreats, canManage, on) {
  if (openThreats > 0) {
    return {
      text: `${openThreats === 1 ? "Es wurde eine Bedrohung gefunden" : `Es wurden ${openThreats} Bedrohungen gefunden`}. Schau sie dir an und entscheide, was damit passiert.`,
      action: { label: "Funde ansehen", run: on.threats }
    };
  }
  if (hosts.length === 0) return null;
  if (hosts.every((h) => h.reachable === false)) {
    return {
      text: hosts.length === 1 ? `\u201E${hosts[0].host_name}\u201C antwortet nicht. Pr\xFCfe, ob der Server l\xE4uft und ob Adresse und Zugang stimmen.` : "Kein Server antwortet. Pr\xFCfe, ob die Server laufen und ob Adresse und Zugang stimmen.",
      link: deck().hasPermission("hosts.write") ? { href: "/settings/hosts", label: "Server & Zug\xE4nge \xF6ffnen" } : void 0
    };
  }
  const reachable = hosts.filter((h) => h.reachable !== false);
  const noClam = reachable.find((h) => !h.clamav_installed);
  if (noClam) {
    const more = reachable.filter((h) => !h.clamav_installed).length - 1;
    return {
      text: `Installiere ClamAV (den Virenscanner) auf \u201E${noClam.host_name}\u201C${more > 0 ? ` und ${more} weiteren Server${more > 1 ? "n" : ""}` : ""}. Ohne ihn wird dort nichts gepr\xFCft.`,
      action: canManage ? { label: "ClamAV installieren", run: () => on.install(noClam, "clamav"), busyKey: installKey(noClam.host_id, "clamav") } : void 0
    };
  }
  const neverScanned = reachable.find((h) => h.clamav_installed && !h.last_scan && !h.scanning);
  if (neverScanned) {
    return {
      text: `\u201E${neverScanned.host_name}\u201C wurde noch nie gepr\xFCft. Starte den ersten Schnellscan.`,
      action: canManage ? { label: "Schnellscan starten", run: () => on.scan(neverScanned) } : void 0
    };
  }
  const noLynis = reachable.find((h) => !h.lynis_installed);
  if (noLynis) {
    return {
      text: `Installiere Lynis auf \u201E${noLynis.host_name}\u201C. Es pr\xFCft, wie sicher der Server eingestellt ist (H\xE4rtung).`,
      action: canManage ? { label: "Lynis installieren", run: () => on.install(noLynis, "lynis"), busyKey: installKey(noLynis.host_id, "lynis") } : void 0
    };
  }
  return null;
}
function coverageBadge(on, name, onWord, covered, total, interval) {
  if (!on) return { tone: "warn", text: `${name} aus` };
  if (covered === 0) return { tone: "warn", text: `${name}: an, aber noch auf keinem Server aktiv` };
  if (covered < total) return { tone: "warn", text: `${name}: ${onWord} auf ${covered} von ${total} Servern${interval ?? ""}` };
  return { tone: "good", text: `${name} ${onWord}${interval ?? ""}` };
}
var GLOSSARY = [
  ["ClamAV", "der Virenscanner. Er l\xE4uft kostenlos auf deinen Servern und sucht nach bekannten Schadprogrammen."],
  ["Signaturen", "die Liste bekannter Schadprogramme, mit der ClamAV vergleicht. Sie sollte t\xE4glich aktualisiert werden."],
  ["Schnellscan / Tiefenscan", "der Schnellscan pr\xFCft typische Ablageorte und ist in Minuten fertig; der Tiefenscan pr\xFCft das ganze System und kann Stunden dauern."],
  ["Echtzeit-W\xE4chter", "pr\xFCft alle paar Minuten neue und ge\xE4nderte Dateien."],
  ["Quarant\xE4ne", "gefundene Schadsoftware wird weggesperrt statt gel\xF6scht. Du kannst sie zur\xFCckholen oder endg\xFCltig l\xF6schen."],
  ["Lynis, H\xE4rtung, H\xE4rtungs-Audit", "Lynis pr\xFCft, wie sicher ein Server eingestellt ist \u2013 das ist das H\xE4rtungs-Audit. \u201EH\xE4rtung\u201C hei\xDFt: den Server sicherer einstellen. Der Wert geht von 0 (unsicher) bis 100."],
  ["Fail2ban", "sperrt Adressen, die zu oft ein falsches Passwort probieren (Reiter \u201EEinbruchschutz\u201C)."]
];
function Glossary() {
  return /* @__PURE__ */ jsxs9("details", { className: "mb-5 rounded-lg border border-white/[0.06] px-3 py-2 text-xs text-white/55", "data-testid": "glossary", children: [
    /* @__PURE__ */ jsx10("summary", { className: "cursor-pointer select-none text-white/70 hover:text-white", children: "Was bedeuten die Begriffe?" }),
    /* @__PURE__ */ jsx10("dl", { className: "mt-2 space-y-1.5", children: GLOSSARY.map(([term, text]) => /* @__PURE__ */ jsxs9("div", { children: [
      /* @__PURE__ */ jsx10("dt", { className: "inline font-medium text-white/80", children: term }),
      " ",
      /* @__PURE__ */ jsxs9("dd", { className: "inline", children: [
        "\u2013 ",
        text
      ] })
    ] }, term)) })
  ] });
}
function OverviewTab({
  overview,
  canManage,
  onRefresh,
  onScan,
  onAudit,
  onInstall,
  installing,
  onOpenThreats,
  side,
  onOpen
}) {
  const s = overview.summary;
  const c = overview.config;
  const hosts = useForHost(overview.hosts);
  const attention = useForHost(overview.attention ?? []);
  const noServers = s.hosts === 0;
  const step = nextStep(hosts, s.open_threats, canManage, {
    threats: onOpenThreats,
    install: onInstall,
    scan: onScan
  });
  const realtime = coverageBadge(c.realtime_enabled, "Echtzeit-W\xE4chter", "aktiv", s.protected, s.hosts, ` (alle ${c.watch_interval_min} Min.)`);
  const quarantine = coverageBadge(c.auto_quarantine, "Automatische Quarant\xE4ne", "an", s.protected, s.hosts);
  const stepBusy = !!step?.action?.busyKey && installing.has(step.action.busyKey);
  const installRunning = (hostId, pkg) => installing.has(installKey(hostId, pkg));
  return /* @__PURE__ */ jsxs9(Fragment6, { children: [
    step && /* @__PURE__ */ jsxs9("div", { className: "mb-5 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-[color-mix(in_srgb,var(--color-accent)_35%,transparent)] bg-[color-mix(in_srgb,var(--color-accent)_10%,transparent)] px-4 py-3 text-sm", "data-testid": "next-step", children: [
      /* @__PURE__ */ jsxs9("p", { className: "min-w-0 flex-1", children: [
        /* @__PURE__ */ jsx10("strong", { className: "font-semibold", children: "N\xE4chster Schritt:" }),
        " ",
        step.text
      ] }),
      step.action && /* @__PURE__ */ jsx10(Button, { variant: "primary", small: true, disabled: stepBusy, onClick: step.action.run, children: stepBusy ? "Installiere \u2026" : step.action.label }),
      step.link && /* @__PURE__ */ jsx10("a", { href: step.link.href, className: "text-xs underline underline-offset-2 hover:text-white", children: step.link.label })
    ] }),
    /* @__PURE__ */ jsxs9("div", { className: "mb-5 grid grid-cols-1 gap-4 lg:grid-cols-[auto_1fr]", children: [
      noServers ? /* @__PURE__ */ jsxs9("div", { className: "panel px-6 py-4", "data-testid": "no-servers", children: [
        /* @__PURE__ */ jsx10("p", { className: "text-xs text-white/45", children: "Schutzwert" }),
        (s.hosts_known ?? 0) > 0 ? /* @__PURE__ */ jsxs9(Fragment6, { children: [
          /* @__PURE__ */ jsx10("p", { className: "text-lg font-semibold", children: "Noch kein Server pr\xFCfbar \u2013 nichts zu bewerten" }),
          /* @__PURE__ */ jsxs9("p", { className: "mt-1 max-w-xs text-xs text-white/55", children: [
            "Du hast ",
            s.hosts_known === 1 ? "einen Server" : `${s.hosts_known} Server`,
            " angelegt, aber Nodvard Shield kann noch keinen pr\xFCfen. Daf\xFCr braucht er einen Linux-Server mit SSH-Zugang (und, falls eingestellt, der passenden Markierung).",
            deck().hasPermission("hosts.write") && /* @__PURE__ */ jsxs9(Fragment6, { children: [
              " ",
              /* @__PURE__ */ jsx10("a", { href: "/settings/hosts", className: "underline underline-offset-2 hover:text-white", children: "Zugang einrichten" })
            ] })
          ] })
        ] }) : /* @__PURE__ */ jsxs9(Fragment6, { children: [
          /* @__PURE__ */ jsx10("p", { className: "text-lg font-semibold", children: "Noch kein Server \u2013 nichts zu bewerten" }),
          /* @__PURE__ */ jsxs9("p", { className: "mt-1 max-w-xs text-xs text-white/55", children: [
            "F\xFCge zuerst einen Server hinzu.",
            deck().hasPermission("hosts.write") && /* @__PURE__ */ jsxs9(Fragment6, { children: [
              " ",
              /* @__PURE__ */ jsx10("a", { href: "/settings/hosts", className: "underline underline-offset-2 hover:text-white", children: "Server hinzuf\xFCgen" })
            ] })
          ] })
        ] })
      ] }) : /* @__PURE__ */ jsx10("div", { className: "panel flex min-w-0 items-center px-6 py-4", children: /* @__PURE__ */ jsx10(ScoreRing, { score: s.score, reasons: s.score_reasons }) }),
      /* @__PURE__ */ jsxs9("div", { className: "grid grid-cols-2 gap-3 md:grid-cols-4", children: [
        /* @__PURE__ */ jsx10(Stat, { label: "Gesch\xFCtzte Server", value: `${s.protected} / ${s.hosts}`, hint: "mit Virenscanner (ClamAV)", tone: noServers ? void 0 : s.protected < s.hosts ? "warn" : "good" }),
        /* @__PURE__ */ jsx10("button", { type: "button", onClick: onOpenThreats, className: "text-left [&>div]:h-full", children: /* @__PURE__ */ jsx10(Stat, { label: "Offene Bedrohungen", value: s.open_threats, hint: s.open_threats ? "Jetzt pr\xFCfen \u2192" : "keine", tone: s.open_threats ? "bad" : "good" }) }),
        /* @__PURE__ */ jsx10(Stat, { label: "In Quarant\xE4ne", value: s.quarantined, hint: findingsLast30Days(s.findings_30d) }),
        /* @__PURE__ */ jsx10(Stat, { label: "H\xE4rtung (\xD8)", value: s.avg_hardening ?? "\u2013", hint: "von 100 \xB7 wie sicher die Server eingestellt sind (Lynis)", tone: s.avg_hardening == null ? void 0 : s.avg_hardening >= 70 ? "good" : "warn" })
      ] })
    ] }),
    attention.length > 0 && /* @__PURE__ */ jsx10("div", { className: "mb-5", "data-testid": "shield-attention", children: /* @__PURE__ */ jsx10(Card, { title: "Braucht Aufmerksamkeit", padded: false, children: /* @__PURE__ */ jsx10("ul", { className: "divide-y divide-white/[0.06]", children: attention.map((a) => {
      const host = overview.hosts.find((h) => h.host_id === a.host_id);
      return /* @__PURE__ */ jsxs9("li", { className: "flex flex-wrap items-center gap-3 px-5 py-3 text-sm", children: [
        /* @__PURE__ */ jsx10("span", { "aria-hidden": "true", className: `h-2 w-2 flex-none rounded-full ${a.tone === "warn" ? "bg-amber-300" : "bg-white/40"}` }),
        /* @__PURE__ */ jsxs9("div", { className: "min-w-0 flex-1", children: [
          /* @__PURE__ */ jsx10("p", { className: "break-words font-medium", children: a.title }),
          /* @__PURE__ */ jsx10("p", { className: "text-xs text-white/50", children: a.hint })
        ] }),
        canManage && host && a.kind === "signatures" && /* @__PURE__ */ jsx10(Button, { small: true, disabled: installRunning(host.host_id, "signatures"), onClick: () => onInstall(host, "signatures"), children: installRunning(host.host_id, "signatures") ? installRunningLabel("signatures") : a.action_label })
      ] }, `${a.kind}-${a.host_id}`);
    }) }) }) }),
    (side.updates?.checked || side.guard) && /* @__PURE__ */ jsxs9("div", { className: "mb-5 grid grid-cols-1 gap-3 md:grid-cols-2", "data-testid": "side-summary", children: [
      !!side.updates?.checked && /* @__PURE__ */ jsx10(
        SideLink,
        {
          title: "Updates",
          tone: side.updates.security ? "bad" : side.updates.packages || side.updates.reboot ? "warn" : "good",
          text: side.updates.security ? `${count(side.updates.security, "Sicherheitsupdate", "Sicherheitsupdates")} offen` : side.updates.packages ? `${count(side.updates.packages, "Update", "Updates")} offen` : `Alle ${side.updates.hosts} Server aktuell`,
          onClick: () => onOpen("updates")
        }
      ),
      side.guard && /* @__PURE__ */ jsx10(
        SideLink,
        {
          title: "Einbruchschutz",
          tone: side.guard.open_events ? "bad" : side.guard.fail2ban_running < side.guard.hosts ? "warn" : "good",
          text: side.guard.open_events ? count(side.guard.open_events, "offenes Ereignis", "offene Ereignisse") : `ruhig \xB7 ${side.guard.failed_24h} SSH-Fehlversuche in 24 Std., ${side.guard.banned} gesperrt`,
          onClick: () => onOpen("guard")
        }
      )
    ] }),
    /* @__PURE__ */ jsxs9("div", { className: "mb-5 flex flex-wrap gap-2 text-xs", children: [
      /* @__PURE__ */ jsx10("span", { className: "max-w-full [&>span]:whitespace-normal", children: /* @__PURE__ */ jsx10(Badge, { tone: realtime.tone, children: realtime.text }) }),
      /* @__PURE__ */ jsx10("span", { className: "max-w-full [&>span]:whitespace-normal", children: /* @__PURE__ */ jsx10(Badge, { tone: quarantine.tone, children: quarantine.text }) }),
      /* @__PURE__ */ jsxs9(Badge, { children: [
        "Schnellscan: ",
        describeSchedule(c.quick_scan_cron, "aus")
      ] }),
      /* @__PURE__ */ jsxs9(Badge, { children: [
        "Tiefenscan: ",
        describeSchedule(c.deep_scan_cron, "aus")
      ] }),
      /* @__PURE__ */ jsxs9(Badge, { children: [
        "H\xE4rtungs-Audit: ",
        describeSchedule(c.audit_cron, "aus")
      ] }),
      /* @__PURE__ */ jsx10("a", { href: `/settings/extensions/${EXT_ID}`, className: "text-white/50 underline-offset-2 hover:text-white hover:underline", children: "Einstellungen \xE4ndern" })
    ] }),
    /* @__PURE__ */ jsx10(Glossary, {}),
    /* @__PURE__ */ jsx10(Card, { title: "Server", padded: false, actions: /* @__PURE__ */ jsxs9(Button, { small: true, variant: "ghost", onClick: onRefresh, children: [
      /* @__PURE__ */ jsx10(Icon, { name: "refresh", size: 12 }),
      " Status neu pr\xFCfen"
    ] }), children: hosts.length === 0 ? /* @__PURE__ */ jsxs9("p", { className: "px-5 py-6 text-sm text-white/50", children: [
      "Keine Linux-Server mit SSH-Zugang gefunden. Einrichten unter",
      " ",
      deck().hasPermission("hosts.write") ? /* @__PURE__ */ jsx10("a", { href: "/settings/hosts", className: "underline underline-offset-2 hover:text-white", children: "Einstellungen \u2192 Server & Zug\xE4nge" }) : "Einstellungen \u2192 Server & Zug\xE4nge",
      "."
    ] }) : /* @__PURE__ */ jsx10("div", { className: "overflow-x-auto", children: /* @__PURE__ */ jsxs9("table", { className: "w-full text-left text-sm", children: [
      /* @__PURE__ */ jsx10("thead", { className: "text-xs text-white/40", children: /* @__PURE__ */ jsxs9("tr", { className: "border-b border-white/[0.06]", children: [
        /* @__PURE__ */ jsx10("th", { className: "px-5 py-2.5 font-medium", children: "Server" }),
        /* @__PURE__ */ jsx10("th", { className: "px-3 py-2.5 font-medium", title: "ClamAV ist der Virenscanner auf dem Server", children: "Virenschutz" }),
        /* @__PURE__ */ jsx10("th", { className: "px-3 py-2.5 font-medium", children: "Letzter Scan" }),
        /* @__PURE__ */ jsx10("th", { className: "px-3 py-2.5 font-medium", title: "Wie sicher der Server eingestellt ist (gepr\xFCft von Lynis), 0 bis 100", children: "H\xE4rtung" }),
        /* @__PURE__ */ jsx10("th", { className: "px-3 py-2.5 font-medium" })
      ] }) }),
      /* @__PURE__ */ jsx10("tbody", { className: "divide-y divide-white/[0.04]", children: hosts.map((h) => /* @__PURE__ */ jsxs9("tr", { "data-testid": `host-${h.host_id}`, children: [
        /* @__PURE__ */ jsxs9("td", { className: "px-5 py-3", children: [
          /* @__PURE__ */ jsx10("p", { className: "font-medium", children: h.host_name }),
          h.reachable === false && /* @__PURE__ */ jsxs9("p", { className: "text-xs text-amber-300", children: [
            "nicht erreichbar",
            h.error ? ` \u2013 ${h.error}` : ""
          ] })
        ] }),
        /* @__PURE__ */ jsx10("td", { className: "px-3 py-3", children: h.clamav_installed ? /* @__PURE__ */ jsxs9(Fragment6, { children: [
          /* @__PURE__ */ jsxs9(Badge, { tone: "good", children: [
            "ClamAV ",
            h.clamav_version
          ] }),
          /* @__PURE__ */ jsxs9("p", { className: "mt-0.5 text-xs text-white/45", children: [
            "Signaturen ",
            h.signature_version ?? "?",
            h.signature_date ? ` \xB7 ${h.signature_date}` : "",
            h.signature_stale === true && /* @__PURE__ */ jsxs9("span", { className: "text-amber-300", children: [
              " \xB7 ",
              ageText(h.signature_age_days ?? 0),
              " alt"
            ] }),
            h.signature_stale == null && /* @__PURE__ */ jsx10("span", { className: "text-amber-300", children: " \xB7 Alter unbekannt" }),
            h.freshclam_active === false && /* @__PURE__ */ jsx10("span", { className: "text-amber-300", children: " \xB7 Auto-Update aus" })
          ] })
        ] }) : h.reachable === false ? /* @__PURE__ */ jsx10(Badge, { children: "unbekannt" }) : /* @__PURE__ */ jsx10(Badge, { tone: "bad", children: "nicht installiert" }) }),
        /* @__PURE__ */ jsx10("td", { className: "px-3 py-3", children: /* @__PURE__ */ jsx10(ScanCell, { host: h }) }),
        /* @__PURE__ */ jsx10("td", { className: "px-3 py-3", children: h.auditing ? /* @__PURE__ */ jsx10(AuditRunning, { run: h.audit_run }) : /* @__PURE__ */ jsx10(HardeningCell, { host: h }) }),
        /* @__PURE__ */ jsx10("td", { className: "px-3 py-3", children: canManage && /* @__PURE__ */ jsxs9("div", { className: "flex flex-wrap justify-end gap-1", children: [
          h.clamav_installed && /* @__PURE__ */ jsx10(Button, { small: true, onClick: () => onScan(h), disabled: h.scanning, children: "Scannen" }),
          h.clamav_installed && /* @__PURE__ */ jsx10(Button, { small: true, variant: "ghost", title: "Die Liste bekannter Schadprogramme jetzt aktualisieren", disabled: installRunning(h.host_id, "signatures"), onClick: () => onInstall(h, "signatures"), children: installRunning(h.host_id, "signatures") ? installRunningLabel("signatures") : "Signaturen" }),
          !h.clamav_installed && h.reachable !== false && /* @__PURE__ */ jsx10(Button, { small: true, variant: "primary", title: "ClamAV ist ein kostenloser Virenscanner. Er wird auf dem Server installiert.", disabled: installRunning(h.host_id, "clamav"), onClick: () => onInstall(h, "clamav"), children: installRunning(h.host_id, "clamav") ? installRunningLabel("clamav") : "ClamAV installieren" }),
          !h.lynis_installed && h.reachable !== false && /* @__PURE__ */ jsx10(Button, { small: true, title: "Lynis pr\xFCft, wie sicher der Server eingestellt ist.", disabled: installRunning(h.host_id, "lynis"), onClick: () => onInstall(h, "lynis"), children: installRunning(h.host_id, "lynis") ? installRunningLabel("lynis") : "Lynis installieren" }),
          h.lynis_installed && /* @__PURE__ */ jsx10(
            Button,
            {
              small: true,
              variant: "ghost",
              onClick: () => onAudit(h),
              title: h.auditing ? "Das Audit l\xE4uft schon \u2013 ein Klick zeigt, seit wann." : "Pr\xFCft, wie sicher der Server eingestellt ist (H\xE4rtung)",
              children: "Audit"
            }
          )
        ] }) })
      ] }, h.host_id)) })
    ] }) }) })
  ] });
}
function visibleScanOutput(tail) {
  return tail.split("\n").filter((line) => !line.startsWith("@@")).join("\n").trim();
}
function confirmOnAllServers(kind, n) {
  const where = n === 1 ? "dem Server" : n > 1 ? `allen ${n} Servern` : "allen Servern";
  return deck().confirmDialog(
    kind === "deep" ? `Tiefenscan auf ${where} starten? Er pr\xFCft das ganze System und kann Stunden dauern. Die Server sind in der Zeit sp\xFCrbar ausgelastet.` : `H\xE4rtungs-Audit auf ${where} starten? Lynis braucht je Server einige Minuten, auf gro\xDFen Servern auch l\xE4nger.`,
    { confirmLabel: kind === "deep" ? "Tiefenscan starten" : "Audit starten" }
  );
}
function ScansTab({ hosts, canManage, onStarted }) {
  const [scans, setScans] = useState9(null);
  const [showWatch, setShowWatch] = useState9(false);
  const [open, setOpen] = useState9(null);
  const [kind, setKind] = useState9("quick");
  const [target, setTarget] = useState9("all");
  const [paths, setPaths] = useState9("");
  const [msg, setMsg] = useState9(null);
  const [loadError, setLoadError] = useState9(null);
  const hostFilter = useContext4(HostFilter);
  useEffect8(() => {
    setTarget(hostFilter ?? "all");
  }, [hostFilter]);
  const load = useCallback6(() => {
    call(`${API}/scans?include_watch=${showWatch}${hostFilter ? `&host=${encodeURIComponent(hostFilter)}` : ""}`).then((rows) => {
      setScans(rows);
      setLoadError(null);
    }).catch((err) => setLoadError(errorText(err)));
  }, [showWatch, hostFilter]);
  useEffect8(() => {
    load();
  }, [load]);
  useEffect8(() => {
    if (!scans?.some((s) => s.status === "running")) return;
    const t = setInterval(load, 4e3);
    return () => clearInterval(t);
  }, [scans, load]);
  async function start() {
    setMsg(null);
    if (kind === "deep" && target === "all" && !await confirmOnAllServers("deep", hosts.length)) return;
    try {
      const body = { kind, host_ids: target === "all" ? "all" : [target], paths: kind === "custom" ? paths.split(/[\n,]/).map((p) => p.trim()).filter(Boolean) : null };
      const r = await call(`${API}/scans`, { method: "POST", body: JSON.stringify(body) });
      setMsg(r.scans.length ? { kind: "ok", text: `${count(r.scans.length, "Scan", "Scans")} gestartet.` } : { kind: "warn", text: "L\xE4uft bereits oder kein Server verf\xFCgbar." });
      load();
      onStarted();
    } catch (err) {
      setMsg({ kind: "error", text: err instanceof Error ? err.message : String(err) });
    }
  }
  return /* @__PURE__ */ jsxs9("div", { className: "space-y-5", children: [
    canManage && /* @__PURE__ */ jsxs9(Card, { title: "Scan starten", description: "Schnellscan pr\xFCft typische Ablageorte (tmp, home, root), der Tiefenscan das ganze System \u2013 das kann Stunden dauern.", children: [
      msg && /* @__PURE__ */ jsx10(RunNotice, { notice: msg, onClose: () => setMsg(null) }),
      /* @__PURE__ */ jsxs9("div", { className: "flex flex-wrap items-end gap-3", children: [
        /* @__PURE__ */ jsxs9("label", { className: "text-sm", children: [
          /* @__PURE__ */ jsx10("span", { className: "mb-1.5 block text-white/70", children: "Art" }),
          /* @__PURE__ */ jsxs9("select", { "aria-label": "Scan-Art", value: kind, onChange: (e) => setKind(e.target.value), className: `${inputClass} w-44`, children: [
            /* @__PURE__ */ jsx10("option", { value: "quick", children: "Schnellscan" }),
            /* @__PURE__ */ jsx10("option", { value: "deep", children: "Tiefenscan" }),
            /* @__PURE__ */ jsx10("option", { value: "custom", children: "Bestimmte Ordner" })
          ] })
        ] }),
        /* @__PURE__ */ jsxs9("label", { className: "text-sm", children: [
          /* @__PURE__ */ jsx10("span", { className: "mb-1.5 block text-white/70", children: "Server" }),
          /* @__PURE__ */ jsxs9("select", { "aria-label": "Scan-Ziel", value: target, onChange: (e) => setTarget(e.target.value), className: `${inputClass} w-52`, children: [
            /* @__PURE__ */ jsx10("option", { value: "all", children: "Alle Server" }),
            hosts.map((h) => /* @__PURE__ */ jsx10("option", { value: h.host_id, children: h.host_name }, h.host_id))
          ] })
        ] }),
        kind === "custom" && /* @__PURE__ */ jsxs9("label", { className: "min-w-[16rem] flex-1 text-sm", children: [
          /* @__PURE__ */ jsx10("span", { className: "mb-1.5 block text-white/70", children: "Ordner (Komma-getrennt)" }),
          /* @__PURE__ */ jsx10("input", { "aria-label": "Ordner", value: paths, placeholder: "/var/www, /srv/uploads", onChange: (e) => setPaths(e.target.value), className: `${inputClass} font-mono` })
        ] }),
        /* @__PURE__ */ jsxs9(Button, { variant: "primary", onClick: () => void start(), children: [
          /* @__PURE__ */ jsx10(Icon, { name: "play", size: 13 }),
          " Starten"
        ] })
      ] })
    ] }),
    canManage && /* @__PURE__ */ jsx10(WatchTemplates, {}),
    /* @__PURE__ */ jsxs9(
      Card,
      {
        title: "Verlauf",
        padded: false,
        actions: /* @__PURE__ */ jsxs9("label", { className: "flex items-center gap-2 text-xs text-white/55", children: [
          /* @__PURE__ */ jsx10("input", { type: "checkbox", checked: showWatch, onChange: (e) => setShowWatch(e.target.checked) }),
          " saubere W\xE4chter-L\xE4ufe zeigen"
        ] }),
        children: [
          loadError && /* @__PURE__ */ jsx10("div", { className: "px-5 pt-4", children: /* @__PURE__ */ jsx10(LoadError, { what: "Der Verlauf", error: loadError, onRetry: load }) }),
          !scans ? !loadError && /* @__PURE__ */ jsx10("p", { className: "px-5 py-4 text-sm text-white/50", children: "Lade \u2026" }) : scans.length === 0 ? /* @__PURE__ */ jsx10("p", { className: "px-5 py-6 text-sm text-white/50", children: "Noch keine Scans." }) : /* @__PURE__ */ jsx10("ul", { className: "divide-y divide-white/[0.05]", "data-testid": "scan-list", children: scans.map((s) => {
            const st = SCAN_STATUS[s.status] ?? { label: s.status, tone: "neutral" };
            const isOpen = open === s.id;
            const output = s.output_tail ? visibleScanOutput(s.output_tail) : "";
            return /* @__PURE__ */ jsxs9("li", { className: "px-5 py-3 text-sm", children: [
              /* @__PURE__ */ jsxs9("button", { type: "button", onClick: () => setOpen(isOpen ? null : s.id), className: "flex w-full flex-wrap items-center gap-2 text-left", children: [
                /* @__PURE__ */ jsx10(Icon, { name: isOpen ? "chevron-down" : "chevron-right", size: 14, className: "text-white/40" }),
                /* @__PURE__ */ jsx10(Badge, { tone: st.tone, children: st.label }),
                /* @__PURE__ */ jsx10("span", { className: "font-medium", children: s.host_name }),
                /* @__PURE__ */ jsx10("span", { className: "text-white/50", children: s.kind_label }),
                s.infected > 0 && /* @__PURE__ */ jsx10("span", { className: "text-red-300", children: count(s.infected, "Fund", "Funde") }),
                s.files_scanned != null && /* @__PURE__ */ jsxs9("span", { className: "text-xs text-white/40", children: [
                  s.files_scanned.toLocaleString("de-DE"),
                  " Dateien"
                ] }),
                /* @__PURE__ */ jsxs9("span", { className: "ml-auto text-xs text-white/40", children: [
                  when(s.started_at),
                  " \xB7 ",
                  s.trigger === "schedule" ? "Zeitplan" : "manuell"
                ] })
              ] }),
              s.error && /* @__PURE__ */ jsx10("p", { className: "ml-6 mt-1 text-xs text-amber-300", children: s.error }),
              isOpen && /* @__PURE__ */ jsxs9("div", { className: "ml-6 mt-2 space-y-2", children: [
                /* @__PURE__ */ jsxs9("p", { className: "text-xs text-white/45", children: [
                  "Ordner: ",
                  /* @__PURE__ */ jsx10("span", { className: "font-mono", children: s.paths.join(", ") }),
                  s.finished_at ? ` \xB7 Dauer ${Math.max(1, Math.round((s.finished_at - s.started_at) / 60))} Min.` : ""
                ] }),
                output && /* @__PURE__ */ jsx10("pre", { className: "max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-black/40 p-2.5 font-mono text-[11px] text-white/75", children: output })
              ] })
            ] }, s.id);
          }) })
        ]
      }
    )
  ] });
}
var FINDING_ACTIONS = ["quarantine", "ignore", "reopen", "restore", "delete"];
var FINDING_PROGRESS = {
  quarantine: "in Quarant\xE4ne verschoben",
  ignore: "ignoriert",
  reopen: "wieder beachtet",
  restore: "wiederhergestellt",
  delete: "gel\xF6scht"
};
var FINDING_RUNNING_LABEL = {
  quarantine: "Verschiebe \u2026",
  ignore: "Ignoriere \u2026",
  reopen: "Beachte wieder \u2026",
  restore: "Stelle wieder her \u2026",
  delete: "L\xF6sche \u2026"
};
var FINDING_CONFIRM_LABEL = {
  quarantine: "Verschieben",
  ignore: "Ignorieren",
  reopen: "Wieder beachten",
  restore: "Wiederherstellen",
  delete: "L\xF6schen"
};
var KEPT_FOR_GOOD = "Findet ein Scan dieselbe Datei mit demselben Fund wieder, meldet Shield sie nicht mehr. R\xFCckg\xE4ngig geht das unter \u201EAlle, inkl. erledigt\u201C mit \u201EWieder beachten\u201C.";
var findingKey = (id, action) => `${id}:${action}`;
function QuarantineTab({ canManage, onChanged }) {
  const page = usePageActions();
  const { keys: { busy }, signal: unmountSignal } = page;
  const [items, setItems] = useState9(null);
  const [filter, setFilter] = useState9("active");
  const [loadError, setLoadError] = useState9(null);
  const load = useCallback6(() => {
    call(`${API}/findings`).then((rows) => {
      setItems(rows);
      setLoadError(null);
    }).catch((err) => setLoadError(errorText(err)));
  }, []);
  useEffect8(() => {
    load();
  }, [load]);
  useReloadWhenSettled(busy, load);
  const hostItems = useForHost(items ?? []);
  const visible = useMemo2(
    () => hostItems.filter((f) => filter === "all" || f.status === "detected" || f.status === "quarantined"),
    [hostItems, filter]
  );
  async function act(f, action) {
    const questions = {
      quarantine: `\u201E${f.path}\u201C in Quarant\xE4ne verschieben?`,
      ignore: `Fund \u201E${f.path}\u201C ignorieren? Die Datei bleibt, wo sie ist. ${KEPT_FOR_GOOD}`,
      reopen: `\u201E${f.path}\u201C wieder als Fund beachten? Er steht dann wieder offen. Der n\xE4chste Scan meldet die Datei wieder und verschiebt sie, wenn die automatische Quarant\xE4ne an ist.`,
      restore: `\u201E${f.path}\u201C wiederherstellen? Nur tun, wenn sicher ist, dass es ein Fehlalarm war. ${KEPT_FOR_GOOD}`,
      delete: `\u201E${f.path}\u201C endg\xFCltig l\xF6schen?`
    };
    const ok = await deck().confirmDialog(questions[action], { danger: action !== "quarantine" && action !== "reopen", confirmLabel: FINDING_CONFIRM_LABEL[action] });
    if (!ok) return;
    const progress = `\u201E${f.path}\u201C wird ${FINDING_PROGRESS[action]} \u2026`;
    await track(page, findingKey(f.id, action), `\u201E${f.path}\u201C`, progress, async () => {
      if (action === "quarantine" || action === "ignore" || action === "reopen") {
        await call(`${API}/findings/${f.id}/${action}`, { method: "POST" });
        return { kind: "ok", text: action === "reopen" ? "Der Fund ist wieder offen. Der n\xE4chste Scan meldet die Datei wieder." : "Erledigt." };
      }
      return proposeAndApprove(`${API}/findings/${f.id}/${action}`, {}, "medium", unmountSignal());
    });
    load();
    onChanged();
  }
  const failed = loadError ? /* @__PURE__ */ jsx10(LoadError, { what: "Die Liste der Funde", error: loadError, onRetry: load }) : null;
  if (!items) return failed ?? /* @__PURE__ */ jsx10("p", { className: "text-sm text-white/50", children: "Lade \u2026" });
  if (failed) {
    return /* @__PURE__ */ jsxs9(Fragment6, { children: [
      failed,
      items.length > 0 && /* @__PURE__ */ jsxs9("p", { className: "text-xs text-white/45", children: [
        "Zuletzt geladener Stand: ",
        count(items.length, "Eintrag", "Eintr\xE4ge"),
        "."
      ] })
    ] });
  }
  if (items.length === 0) {
    return /* @__PURE__ */ jsx10(EmptyState, { icon: "package", title: "Keine Funde", text: "Bisher wurde keine Schadsoftware gefunden. Funde landen hier \u2013 bei aktivierter automatischer Quarant\xE4ne sind sie sofort unsch\xE4dlich gemacht." });
  }
  return /* @__PURE__ */ jsx10(Fragment6, { children: /* @__PURE__ */ jsx10(
    Card,
    {
      title: "Funde & Quarant\xE4ne",
      description: "Dateien in Quarant\xE4ne sind unlesbar und nicht ausf\xFChrbar (Rechte 000) im Tresor auf dem Server, auf dem sie gefunden wurden.",
      padded: false,
      actions: /* @__PURE__ */ jsxs9("select", { "aria-label": "Filter", value: filter, onChange: (e) => setFilter(e.target.value), className: `${inputClass} w-auto py-1 text-xs`, children: [
        /* @__PURE__ */ jsx10("option", { value: "active", children: "Offen & in Quarant\xE4ne" }),
        /* @__PURE__ */ jsx10("option", { value: "all", children: "Alle, inkl. erledigt" })
      ] }),
      children: /* @__PURE__ */ jsxs9("ul", { className: "divide-y divide-white/[0.05]", "data-testid": "findings", children: [
        visible.length === 0 && /* @__PURE__ */ jsx10("li", { className: "px-5 py-6 text-sm text-white/50", children: "Nichts offen." }),
        visible.map((f) => {
          const st = FINDING_STATUS[f.status] ?? { label: f.status, tone: "neutral" };
          const running = FINDING_ACTIONS.find((a) => busy.has(findingKey(f.id, a)));
          const label = (action, text) => running === action ? FINDING_RUNNING_LABEL[action] : text;
          return /* @__PURE__ */ jsxs9("li", { className: "flex flex-wrap items-center gap-3 px-5 py-3 text-sm", children: [
            /* @__PURE__ */ jsxs9("div", { className: "min-w-0 flex-1", children: [
              /* @__PURE__ */ jsxs9("p", { className: "flex flex-wrap items-center gap-2", children: [
                /* @__PURE__ */ jsx10(Badge, { tone: st.tone, children: st.label }),
                /* @__PURE__ */ jsx10("span", { className: "font-medium text-red-200", children: f.signature })
              ] }),
              /* @__PURE__ */ jsxs9("p", { className: "mt-0.5 truncate font-mono text-xs text-white/70", children: [
                f.host_name,
                ": ",
                f.path
              ] }),
              /* @__PURE__ */ jsxs9("p", { className: "text-xs text-white/40", children: [
                "gefunden ",
                when(f.detected_at),
                f.status_changed_at ? ` \xB7 ge\xE4ndert ${when(f.status_changed_at)}` : ""
              ] }),
              f.note && /* @__PURE__ */ jsx10("p", { className: "text-xs text-amber-300", children: f.note })
            ] }),
            canManage && /* @__PURE__ */ jsxs9("div", { className: "flex gap-1", children: [
              f.status === "detected" && /* @__PURE__ */ jsx10(Button, { small: true, variant: "primary", disabled: !!running, onClick: () => void act(f, "quarantine"), children: label("quarantine", "In Quarant\xE4ne") }),
              f.status === "detected" && /* @__PURE__ */ jsx10(Button, { small: true, variant: "ghost", disabled: !!running, onClick: () => void act(f, "ignore"), children: label("ignore", "Ignorieren") }),
              f.status === "quarantined" && /* @__PURE__ */ jsx10(Button, { small: true, disabled: !!running, onClick: () => void act(f, "restore"), children: label("restore", "Wiederherstellen") }),
              f.status === "quarantined" && /* @__PURE__ */ jsx10(Button, { small: true, variant: "danger", disabled: !!running, onClick: () => void act(f, "delete"), children: label("delete", "Endg\xFCltig l\xF6schen") }),
              (f.status === "ignored" || f.status === "restored") && /* @__PURE__ */ jsx10(Button, { small: true, variant: "ghost", disabled: !!running, onClick: () => void act(f, "reopen"), children: label("reopen", "Wieder beachten") })
            ] })
          ] }, f.id);
        })
      ] })
    }
  ) });
}
function HardeningTab({ canManage, hosts, onAudit }) {
  const hostFilter = useContext4(HostFilter);
  const [audits, setAudits] = useState9(null);
  const [open, setOpen] = useState9(null);
  const runningHosts = useForHost(hosts.filter((h) => h.auditing));
  const runningKey = hosts.filter((h) => h.auditing).map((h) => h.host_id).sort().join(",");
  const [loadError, setLoadError] = useState9(null);
  const [reload, setReload] = useState9(0);
  useEffect8(() => {
    call(`${API}/audits`).then((a) => {
      setAudits(a.sort((x, y) => (x.hardening_index ?? 0) - (y.hardening_index ?? 0)));
      setLoadError(null);
    }).catch((err) => setLoadError(errorText(err)));
  }, [runningKey, reload]);
  const shown = useForHost(audits ?? []);
  const runningBox = runningHosts.length > 0 && /* @__PURE__ */ jsxs9(Card, { title: "L\xE4uft gerade", className: "mb-4", children: [
    /* @__PURE__ */ jsx10("ul", { className: "space-y-1.5 text-sm", "data-testid": "audits-running", children: runningHosts.map((h) => /* @__PURE__ */ jsxs9("li", { className: "flex flex-wrap items-center gap-2", children: [
      /* @__PURE__ */ jsx10("span", { className: "font-medium", children: h.host_name }),
      /* @__PURE__ */ jsx10(AuditRunning, { run: h.audit_run })
    ] }, h.host_id)) }),
    /* @__PURE__ */ jsxs9("p", { className: "mt-2 text-xs text-white/45", children: [
      AUDIT_DURATION_HINT,
      " Das Ergebnis erscheint hier, sobald es fertig ist."
    ] })
  ] });
  const failed = loadError ? /* @__PURE__ */ jsx10(LoadError, { what: "Die Liste der H\xE4rtungs-Audits", error: loadError, onRetry: () => setReload((n) => n + 1) }) : null;
  if (!audits) return /* @__PURE__ */ jsxs9(Fragment6, { children: [
    runningBox,
    failed ?? /* @__PURE__ */ jsx10("p", { className: "text-sm text-white/50", children: "Lade \u2026" })
  ] });
  if (failed) return /* @__PURE__ */ jsxs9(Fragment6, { children: [
    runningBox,
    failed
  ] });
  if (shown.length === 0) {
    return /* @__PURE__ */ jsxs9(Fragment6, { children: [
      runningBox,
      !runningBox && /* @__PURE__ */ jsx10(
        EmptyState,
        {
          icon: "clock",
          title: "Noch kein H\xE4rtungs-Audit",
          text: "Lynis ist ein kostenloses Pr\xFCfprogramm. Es sucht auf jedem Server nach unsicheren Einstellungen (SSH, Passwortregeln, offene Dienste \u2026) und vergibt einen H\xE4rtungswert von 0 bis 100 \u2013 je h\xF6her, desto sicherer.",
          action: canManage && /* @__PURE__ */ jsx10(Button, { variant: "primary", onClick: () => onAudit(hostFilter ? [hostFilter] : "all"), children: "Audit jetzt starten" })
        }
      )
    ] });
  }
  const top = topIssues(shown);
  return /* @__PURE__ */ jsxs9(Fragment6, { children: [
    runningBox,
    top.length > 0 && /* @__PURE__ */ jsx10(Card, { title: "Deine wichtigsten Baustellen", description: "Die Hinweise mit der gr\xF6\xDFten Wirkung auf den angezeigten Servern.", className: "mb-4", children: /* @__PURE__ */ jsx10(TopIssues, { issues: top }) }),
    /* @__PURE__ */ jsx10("div", { className: "grid gap-4 lg:grid-cols-2", children: shown.map((a) => /* @__PURE__ */ jsx10(
      Card,
      {
        title: a.host_name,
        description: `${a.status === "ok" ? "Gepr\xFCft" : "Versucht"} ${when(a.created_at)}`,
        actions: a.hardening_index != null && /* @__PURE__ */ jsx10("span", { className: `text-2xl font-semibold ${a.hardening_index >= 70 ? "text-emerald-300" : "text-amber-300"}`, children: a.hardening_index }),
        children: a.status !== "ok" ? /* @__PURE__ */ jsxs9(Fragment6, { children: [
          /* @__PURE__ */ jsxs9("p", { className: "text-sm text-amber-300", children: [
            "Letztes Audit fehlgeschlagen (",
            ago(a.created_at),
            ")"
          ] }),
          a.error && /* @__PURE__ */ jsx10("p", { className: "mt-1 break-words text-xs text-white/50", children: a.error })
        ] }) : /* @__PURE__ */ jsxs9(Fragment6, { children: [
          /* @__PURE__ */ jsxs9("p", { className: "mb-2 text-xs text-white/40", children: [
            "Warnungen (",
            a.warnings.length,
            ")"
          ] }),
          a.warnings.length === 0 ? /* @__PURE__ */ jsx10("p", { className: "mb-3 text-sm text-emerald-300", children: "Keine Warnungen." }) : /* @__PURE__ */ jsx10("ul", { className: "mb-3 space-y-1 text-sm", children: a.warnings.map((w, i) => /* @__PURE__ */ jsx10(LynisFinding, { entry: w, help: a.help, tone: "warning" }, `${i}:${w}`)) }),
          /* @__PURE__ */ jsxs9("button", { type: "button", onClick: () => setOpen(open === a.id ? null : a.id), className: "flex items-center gap-1 text-xs text-white/55 hover:text-white", children: [
            /* @__PURE__ */ jsx10(Icon, { name: open === a.id ? "chevron-down" : "chevron-right", size: 12 }),
            " ",
            a.suggestions.length,
            " Verbesserungsvorschl\xE4ge"
          ] }),
          open === a.id && /* @__PURE__ */ jsx10("ul", { className: "mt-2 max-h-72 space-y-1 overflow-auto text-xs text-white/70", children: a.suggestions.map((s, i) => /* @__PURE__ */ jsx10(LynisFinding, { entry: s, help: a.help, tone: "suggestion" }, `${i}:${s}`)) })
        ] })
      },
      a.id
    )) })
  ] });
}
export {
  SocPage,
  findingsLast30Days,
  hostScanBadge
};
