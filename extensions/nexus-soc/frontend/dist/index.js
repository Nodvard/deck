// src/SocPage.tsx
import { useCallback as useCallback5, useContext as useContext2, useEffect as useEffect7, useMemo as useMemo2, useState as useState6 } from "react";

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

// ../../../frontend/src/components/SchedulePicker.tsx
import { useEffect as useEffect3, useState as useState2 } from "react";

// ../../../frontend/src/lib/deckTimezone.ts
import { useSyncExternalStore } from "react";

// ../../../frontend/src/components/SchedulePicker.tsx
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
import { createContext, useContext } from "react";

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

// src/api.ts
var HostFilter = createContext(null);
function useForHost(rows) {
  const host = useContext(HostFilter);
  return host ? rows.filter((r) => r.host_id === host) : rows;
}
var API = "/ext/nexus-soc/defender";
async function call(path, init = {}) {
  const res = await authedFetch(path, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorFromBody(body, res.status));
  return body;
}
async function proposeAndApprove(path, body, risk, signal) {
  const { action, approved } = await runAction(path, { method: "POST", body: JSON.stringify(body) }, { risk, signal });
  const status = action.status ?? "";
  if (approved) {
    if (status === "succeeded") return "Erledigt.";
    if (isActionRunning(status)) return RUNNING_IN_BACKGROUND;
    return `Fehlgeschlagen: ${action.result?.error ?? status}`;
  }
  if (status === "proposed") return "Wartet auf Freigabe unter \u201EAktionen\u201C.";
  if (status === "succeeded") return "Erledigt.";
  if (isActionRunning(status)) return RUNNING_IN_BACKGROUND;
  return status === "denied" ? "Von der Sperrliste abgelehnt." : `Status: ${status}`;
}
function when(ts) {
  if (!ts) return "\u2013";
  return new Date(ts * 1e3).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}
function ago(ts) {
  if (!ts) return "nie";
  const s = Date.now() / 1e3 - ts;
  if (s < 90) return "gerade eben";
  if (s < 3600) return `vor ${Math.round(s / 60)} Min.`;
  if (s < 86400 * 2) return `vor ${Math.round(s / 3600)} Std.`;
  return `vor ${Math.round(s / 86400)} Tagen`;
}

// src/ContainerWatch.tsx
import { useCallback as useCallback2, useEffect as useEffect4, useState as useState3 } from "react";
import { Fragment as Fragment2, jsx as jsx3, jsxs as jsxs3 } from "react/jsx-runtime";
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
var AUDIT_ACTION_LABELS = {
  "nexus_soc.incident": "Vorfall erfasst und von Nodvard KI bewertet",
  "nexus_soc.incident_status": "Status ge\xE4ndert",
  "nexus_soc.proposal_rejected": "Vorschlag von Nodvard KI abgelehnt (Sperrliste)"
};
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
  const [entries, setEntries] = useState3(null);
  const [error, setError] = useState3(null);
  useEffect4(() => {
    authedFetch(`/audit?correlation_id=${encodeURIComponent(incidentId)}&limit=100`).then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    }).then((rows) => setEntries([...rows].sort((a, b) => a.ts.localeCompare(b.ts)))).catch((err) => setError(err instanceof Error ? err.message : String(err)));
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
    /* @__PURE__ */ jsx3("span", { className: "font-medium", children: AUDIT_ACTION_LABELS[e.action] ?? e.action }),
    " ",
    /* @__PURE__ */ jsxs3("span", { className: "opacity-60", children: [
      "(",
      OUTCOME_LABELS[e.outcome] ?? e.outcome,
      ")"
    ] }),
    e.reason && /* @__PURE__ */ jsxs3("span", { className: "opacity-80", children: [
      " -- ",
      e.reason
    ] })
  ] }, e.id)) });
}
function ContainerWatch() {
  const [page, setPage] = useState3(null);
  const [stats, setStats] = useState3(null);
  const [error, setError] = useState3(null);
  const [query, setQuery] = useState3("");
  const [appliedQuery, setAppliedQuery] = useState3("");
  const [statusFilter, setStatusFilter] = useState3("");
  const [hostFilter, setHostFilter] = useState3("");
  const [fromDay, setFromDay] = useState3("");
  const [toDay, setToDay] = useState3("");
  const [offset, setOffset] = useState3(0);
  const [openTrail, setOpenTrail] = useState3(null);
  const canReadAudit = deck().hasPermission("audit.read");
  const load = useCallback2(() => {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (appliedQuery) params.set("q", appliedQuery);
    if (statusFilter) params.set("status", statusFilter);
    if (hostFilter) params.set("host", hostFilter);
    if (fromDay) params.set("since", dayBoundary(fromDay, false));
    if (toDay) params.set("until", dayBoundary(toDay, true));
    Promise.all([
      authedFetch(`/ext/nexus-soc/history?${params.toString()}`).then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      }),
      authedFetch("/ext/nexus-soc/stats").then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
    ]).then(([historyData, statsData]) => {
      setPage(historyData);
      setStats(statsData);
      setError(null);
    }).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [appliedQuery, statusFilter, hostFilter, fromDay, toDay, offset]);
  useEffect4(() => {
    load();
    const interval = setInterval(load, 3e4);
    return () => clearInterval(interval);
  }, [load]);
  useEffect4(() => {
    setOffset(0);
  }, [appliedQuery, statusFilter, hostFilter, fromDay, toDay]);
  async function changeStatus(id, verb) {
    await authedFetch(`/ext/nexus-soc/incidents/${id}/${verb}`, { method: "POST" });
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
            incident.ai_summary && /* @__PURE__ */ jsx3("p", { className: "mt-2 whitespace-pre-wrap break-words rounded-lg border border-white/[0.06] bg-black/20 p-3 text-xs text-white/75", children: incident.ai_summary }),
            /* @__PURE__ */ jsxs3("p", { className: "mt-1.5 text-xs text-white/40", children: [
              formatTimestamp(incident.created_at),
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
import { useCallback as useCallback3, useEffect as useEffect5, useState as useState4 } from "react";

// src/ServerSetupLink.tsx
import { jsx as jsx4 } from "react/jsx-runtime";
function ServerSetupLink({ className = "" }) {
  if (!deck().hasPermission("hosts.write")) return null;
  return /* @__PURE__ */ jsx4("a", { href: "/settings/hosts", className: `underline underline-offset-2 ${className}`, children: "Zugang einrichten" });
}

// src/GuardTab.tsx
import { Fragment as Fragment3, jsx as jsx5, jsxs as jsxs4 } from "react/jsx-runtime";
function portName(p) {
  if (p.dynamic) return `wechselnde Ports/${p.proto}${p.count && p.count > 1 ? ` (${p.count})` : ""}`;
  return `${p.port}/${p.proto}`;
}
var SEVERITY = {
  critical: { label: "kritisch", tone: "bad" },
  warning: { label: "Warnung", tone: "warn" },
  info: { label: "Hinweis", tone: "info" }
};
var ACK_LABEL = { new_port: "Als bekannt \xFCbernehmen", file_changed: "\xC4nderung \xFCbernehmen" };
function EventDetail({ ev, onBan }) {
  const d = ev.detail;
  return /* @__PURE__ */ jsxs4("div", { className: "mt-2 rounded-lg bg-black/25 p-3 text-xs", children: [
    d.changes && /* @__PURE__ */ jsx5("ul", { className: "space-y-1", children: d.changes.map((c) => /* @__PURE__ */ jsxs4("li", { className: "flex flex-wrap items-center gap-2", children: [
      /* @__PURE__ */ jsx5(Badge, { tone: SEVERITY[c.severity]?.tone ?? "neutral", children: c.change === "added" ? "neu" : c.change === "removed" ? "gel\xF6scht" : "ge\xE4ndert" }),
      /* @__PURE__ */ jsx5("span", { className: "font-mono", children: c.path }),
      /* @__PURE__ */ jsx5("span", { className: "text-white/50", children: c.note })
    ] }, c.path)) }),
    d.attackers && /* @__PURE__ */ jsx5("ul", { className: "space-y-1", children: d.attackers.map((a) => /* @__PURE__ */ jsxs4("li", { className: "flex flex-wrap items-center gap-2", children: [
      /* @__PURE__ */ jsx5("span", { className: "font-mono", children: a.ip }),
      /* @__PURE__ */ jsxs4("span", { className: "text-white/50", children: [
        a.count,
        "\xD7 \xB7 Benutzer: ",
        a.users.join(", ") || "\u2013"
      ] }),
      /* @__PURE__ */ jsx5("button", { type: "button", onClick: () => onBan(a.ip), className: "text-[var(--color-accent)] hover:underline", children: "Sperren" })
    ] }, a.ip)) }),
    d.logins && /* @__PURE__ */ jsx5("ul", { className: "space-y-1", children: d.logins.map((l) => /* @__PURE__ */ jsxs4("li", { children: [
      /* @__PURE__ */ jsxs4("span", { className: "font-mono", children: [
        l.user,
        "@",
        l.ip
      ] }),
      " ",
      /* @__PURE__ */ jsxs4("span", { className: "text-white/50", children: [
        "\xB7 ",
        l.method,
        " \xB7 ",
        when(l.ts)
      ] })
    ] }, l.ip)) }),
    d.ports && /* @__PURE__ */ jsx5("ul", { className: "space-y-1", children: d.ports.map((p) => /* @__PURE__ */ jsxs4("li", { children: [
      /* @__PURE__ */ jsx5("span", { className: "font-mono", children: portName(p) }),
      " ",
      /* @__PURE__ */ jsxs4("span", { className: "text-white/50", children: [
        "\xB7 ",
        p.process ?? (p.dynamic ? "ohne Programm (Kernel)" : "unbekanntes Programm"),
        " \xB7 ",
        p.public ? `offen auf ${p.address}` : "nur lokal"
      ] })
    ] }, p.key)) })
  ] });
}
function GuardTab({ canManage }) {
  const unmountSignal = useUnmountSignal();
  const [data, setData] = useState4(null);
  const [events, setEvents] = useState4(null);
  const [showAll, setShowAll] = useState4(false);
  const [open, setOpen] = useState4(null);
  const [error, setError] = useState4(null);
  const [notice, setNotice] = useState4(null);
  const [checking, setChecking] = useState4(false);
  const load = useCallback3(() => {
    Promise.all([call(`${API}/guard`), call(`${API}/events${showAll ? "?all=true" : ""}`)]).then(([g, e]) => {
      setData(g);
      setEvents(e);
      setError(null);
    }).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [showAll]);
  useEffect5(() => {
    load();
  }, [load]);
  const busy = checking || !!data?.hosts.some((h) => h.checking);
  useEffect5(() => {
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
      const text = await fn();
      setNotice({ kind: text.startsWith("Fehlgeschlagen") ? "error" : "ok", text: `${label}: ${text}` });
    } catch (err) {
      setNotice({ kind: "error", text: `${label}: ${err instanceof Error ? err.message : String(err)}` });
    }
    load();
  }
  const checkNow = (hostIds = "all") => run("Pr\xFCfung", async () => {
    const r = await call(`${API}/guard/check`, { method: "POST", body: JSON.stringify({ host_ids: hostIds }) });
    setChecking(true);
    return r.hosts ? `${r.hosts} Server werden gepr\xFCft \u2026` : "l\xE4uft bereits.";
  });
  const acknowledge = (ev) => run(ev.kind_label, async () => {
    await call(`${API}/events/${ev.id}/acknowledge`, { method: "POST" });
    return ev.kind in ACK_LABEL ? "als bekannt \xFCbernommen." : "best\xE4tigt.";
  });
  const acknowledgeAll = () => run("Ereignisse", async () => {
    const ok = await deck().confirmDialog("Alle offenen Ereignisse best\xE4tigen? Neue Ports und ge\xE4nderte Dateien gelten danach als bekannt.", { confirmLabel: "Alle best\xE4tigen" });
    if (!ok) return "abgebrochen.";
    const r = await call(`${API}/events/acknowledge-all`, { method: "POST" });
    return `${r.acknowledged} best\xE4tigt.`;
  });
  const ban = (hostId, hostName, ip, unban = false) => run(`${ip} ${unban ? "entsperren" : "sperren"}`, async () => {
    const ok = await deck().confirmDialog(
      unban ? `${ip} auf \u201E${hostName}\u201C wieder freigeben?` : `${ip} auf \u201E${hostName}\u201C per Fail2ban sperren? Von dieser Adresse ist dann keine SSH-Verbindung mehr m\xF6glich.`,
      { confirmLabel: unban ? "Entsperren" : "Sperren", danger: !unban }
    );
    if (!ok) return "abgebrochen.";
    const text = await proposeAndApprove(`${API}/hosts/${hostId}/ban`, { ip, unban }, "low", unmountSignal());
    void call(`${API}/guard/check`, { method: "POST", body: JSON.stringify({ host_ids: [hostId] }) }).then(() => setChecking(true)).catch(() => void 0);
    return text;
  });
  const installFail2ban = (h) => run(`Fail2ban (${h.host_name})`, async () => {
    const ok = await deck().confirmDialog(
      `Fail2ban auf \u201E${h.host_name}\u201C installieren? Es sperrt Adressen automatisch, die zu oft ein falsches SSH-Passwort versuchen.`,
      { confirmLabel: "Installieren" }
    );
    if (!ok) return "abgebrochen.";
    const text = await proposeAndApprove(`${API}/hosts/${h.host_id}/install`, { package: "fail2ban" }, "medium", unmountSignal());
    void checkNow([h.host_id]);
    return text;
  });
  const hosts = useForHost(data?.hosts ?? []);
  const shownEvents = useForHost(events ?? []);
  if (error) return /* @__PURE__ */ jsx5(Notice, { text: `Fehler: ${error}` });
  if (!data || !events) return /* @__PURE__ */ jsx5("p", { className: "text-sm text-white/50", children: "Lade \u2026" });
  const sm = data.summary;
  if (hosts.length === 0) {
    return /* @__PURE__ */ jsx5(EmptyState, { icon: "eye", title: "Keine Server", text: "Sobald Linux-Server mit SSH-Zugang angelegt sind, \xFCberwacht der Einbruchschutz ihre SSH-Anmeldungen, Ports und wichtigen Dateien.", action: /* @__PURE__ */ jsx5(ServerSetupLink, { className: "text-sm" }) });
  }
  return /* @__PURE__ */ jsxs4(Fragment3, { children: [
    notice && /* @__PURE__ */ jsx5(Notice, { text: notice.text, kind: notice.kind, onClose: () => setNotice(null) }),
    /* @__PURE__ */ jsxs4("div", { className: "mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4", children: [
      /* @__PURE__ */ jsx5(Stat, { label: "Fehlgeschlagene SSH-Anmeldungen", value: sm.failed_24h, hint: "letzte 24 Stunden", tone: sm.failed_24h > 100 ? "warn" : void 0 }),
      /* @__PURE__ */ jsx5(Stat, { label: "Angreifende Adressen", value: sm.attackers }),
      /* @__PURE__ */ jsx5(Stat, { label: "Von Fail2ban gesperrt", value: sm.banned, hint: `Fail2ban l\xE4uft auf ${sm.fail2ban_running}/${sm.hosts} Servern${sm.fail2ban_unreadable ? ` \xB7 bei ${sm.fail2ban_unreadable} nicht lesbar` : ""}`, tone: sm.fail2ban_running < sm.hosts ? "warn" : "good" }),
      /* @__PURE__ */ jsx5(Stat, { label: "Offene Ereignisse", value: sm.open_events, tone: sm.open_events ? "bad" : "good" })
    ] }),
    /* @__PURE__ */ jsxs4("div", { className: "mb-4 flex flex-wrap items-center gap-2", children: [
      /* @__PURE__ */ jsxs4(Badge, { tone: data.config.enabled ? "info" : "neutral", children: [
        /* @__PURE__ */ jsx5(Icon, { name: "clock", size: 11 }),
        " ",
        data.config.enabled ? `Pr\xFCfung alle ${data.config.interval_min} Min.` : "Automatische Pr\xFCfung aus"
      ] }),
      /* @__PURE__ */ jsxs4(Badge, { children: [
        "Warnung ab ",
        data.config.threshold,
        " Fehlversuchen"
      ] }),
      /* @__PURE__ */ jsxs4(Badge, { tone: data.config.file_watch ? "good" : "neutral", children: [
        "Datei-W\xE4chter ",
        data.config.file_watch ? "an" : "aus"
      ] }),
      /* @__PURE__ */ jsx5("span", { className: "flex-1" }),
      canManage && /* @__PURE__ */ jsxs4(Button, { onClick: () => void checkNow(), children: [
        /* @__PURE__ */ jsx5(Icon, { name: "refresh", size: 14 }),
        " Alle jetzt pr\xFCfen"
      ] })
    ] }),
    /* @__PURE__ */ jsx5(
      Card,
      {
        title: "Sicherheitsereignisse",
        description: "Was seit der letzten Best\xE4tigung aufgefallen ist. Jede Auff\xE4lligkeit wird genau einmal gemeldet.",
        padded: false,
        className: "mb-5",
        actions: /* @__PURE__ */ jsxs4("div", { className: "flex items-center gap-2", children: [
          /* @__PURE__ */ jsxs4("label", { className: "flex items-center gap-1.5 text-xs text-white/55", children: [
            /* @__PURE__ */ jsx5("input", { type: "checkbox", checked: showAll, onChange: (e) => setShowAll(e.target.checked) }),
            " auch erledigte"
          ] }),
          canManage && shownEvents.some((e) => !e.acknowledged) && /* @__PURE__ */ jsx5(Button, { small: true, onClick: () => void acknowledgeAll(), children: "Alle best\xE4tigen" })
        ] }),
        children: /* @__PURE__ */ jsxs4("ul", { className: "divide-y divide-white/[0.05]", "data-testid": "events", children: [
          shownEvents.length === 0 && /* @__PURE__ */ jsx5("li", { className: "px-5 py-6 text-sm text-emerald-300/80", children: "Keine offenen Ereignisse \u2013 alles ruhig." }),
          shownEvents.map((ev) => {
            const sev = SEVERITY[ev.severity] ?? SEVERITY.info;
            return /* @__PURE__ */ jsxs4("li", { className: `px-5 py-3 text-sm ${ev.acknowledged ? "opacity-55" : ""}`, "data-testid": `event-${ev.id}`, children: [
              /* @__PURE__ */ jsxs4("div", { className: "flex flex-wrap items-center gap-2", children: [
                /* @__PURE__ */ jsx5(Badge, { tone: sev.tone, children: sev.label }),
                /* @__PURE__ */ jsx5("span", { className: "text-white/60", children: ev.host_name }),
                /* @__PURE__ */ jsx5("span", { className: "font-medium", children: ev.title }),
                /* @__PURE__ */ jsx5("span", { className: "text-xs text-white/40", children: when(ev.created_at) }),
                /* @__PURE__ */ jsx5("span", { className: "flex-1" }),
                /* @__PURE__ */ jsx5("button", { type: "button", onClick: () => setOpen(open === ev.id ? null : ev.id), className: "text-xs text-white/50 hover:text-white", children: open === ev.id ? "weniger" : "Details" }),
                canManage && !ev.acknowledged && /* @__PURE__ */ jsx5(Button, { small: true, onClick: () => void acknowledge(ev), children: ACK_LABEL[ev.kind] ?? "Zur Kenntnis genommen" })
              ] }),
              open === ev.id && /* @__PURE__ */ jsx5(EventDetail, { ev, onBan: (ip) => void ban(ev.host_id, ev.host_name, ip) })
            ] }, ev.id);
          })
        ] })
      }
    ),
    /* @__PURE__ */ jsx5("div", { className: "grid gap-4 xl:grid-cols-2", children: hosts.map((h) => {
      const v = h.view;
      const f2b = v?.fail2ban;
      return /* @__PURE__ */ jsx5(
        Card,
        {
          title: h.host_name,
          description: v?.checked_at ? `gepr\xFCft ${ago(v.checked_at)}` : "noch nicht gepr\xFCft",
          actions: /* @__PURE__ */ jsxs4("div", { className: "flex flex-wrap items-center gap-1.5", children: [
            h.checking && /* @__PURE__ */ jsx5(Badge, { tone: "info", children: "pr\xFCft \u2026" }),
            f2b === "running" && /* @__PURE__ */ jsx5(Badge, { tone: "good", children: "Fail2ban aktiv" }),
            f2b === "stopped" && /* @__PURE__ */ jsx5(Badge, { tone: "warn", children: "Fail2ban gestoppt" }),
            f2b === "none" && /* @__PURE__ */ jsx5(Badge, { tone: "warn", children: "ohne Fail2ban" }),
            f2b === "noaccess" && /* @__PURE__ */ jsx5(Badge, { tone: "neutral", children: "Fail2ban: Zustand nicht lesbar (root n\xF6tig)" }),
            h.open_events > 0 && /* @__PURE__ */ jsxs4(Badge, { tone: "bad", children: [
              h.open_events,
              " offen"
            ] }),
            canManage && /* @__PURE__ */ jsx5(Button, { small: true, variant: "ghost", ariaLabel: `${h.host_name} pr\xFCfen`, disabled: h.checking, onClick: () => void checkNow([h.host_id]), children: /* @__PURE__ */ jsx5(Icon, { name: "refresh", size: 12 }) })
          ] }),
          children: /* @__PURE__ */ jsxs4("div", { "data-testid": `guard-${h.host_id}`, children: [
            !v && /* @__PURE__ */ jsx5("p", { className: "text-sm text-white/50", children: "Noch keine Daten \u2013 \u201EAlle jetzt pr\xFCfen\u201C startet den ersten Blick." }),
            v?.error && /* @__PURE__ */ jsx5("p", { className: "text-sm text-amber-300", children: v.error }),
            v && !v.error && /* @__PURE__ */ jsxs4("div", { className: "space-y-4 text-sm", children: [
              (!v.is_root || !v.ssh_log_found) && /* @__PURE__ */ jsx5("p", { className: "rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-amber-200", children: !v.is_root ? "Ohne root-Rechte sind SSH-Protokoll, Fail2ban und Programmnamen der Ports nur eingeschr\xE4nkt lesbar." : "Kein SSH-Protokoll gefunden (journald/auth.log)." }),
              /* @__PURE__ */ jsxs4("section", { children: [
                /* @__PURE__ */ jsx5("p", { className: "mb-1.5 text-xs uppercase tracking-wider text-white/40", children: "SSH \u2013 letzte 24 Stunden" }),
                /* @__PURE__ */ jsxs4("p", { className: "mb-2", children: [
                  /* @__PURE__ */ jsxs4("span", { className: v.failed_24h ? "text-amber-300" : "text-emerald-300", children: [
                    v.failed_24h,
                    " Fehlversuch",
                    v.failed_24h === 1 ? "" : "e"
                  ] }),
                  /* @__PURE__ */ jsxs4("span", { className: "text-white/50", children: [
                    " von ",
                    v.attacker_count,
                    " Adresse",
                    v.attacker_count === 1 ? "" : "n"
                  ] }),
                  v.banned_count > 0 && /* @__PURE__ */ jsxs4("span", { className: "text-white/50", children: [
                    " \xB7 ",
                    v.banned_count,
                    " gesperrt"
                  ] })
                ] }),
                v.attackers.length > 0 && /* @__PURE__ */ jsx5("ul", { className: "space-y-1 text-xs", children: v.attackers.slice(0, 6).map((a) => /* @__PURE__ */ jsxs4("li", { className: "flex flex-wrap items-center gap-2", children: [
                  /* @__PURE__ */ jsx5("span", { className: "w-36 font-mono", children: a.ip }),
                  /* @__PURE__ */ jsxs4("span", { className: "tabular-nums text-white/70", children: [
                    a.count,
                    "\xD7"
                  ] }),
                  /* @__PURE__ */ jsx5("span", { className: "min-w-0 flex-1 truncate text-white/45", children: a.users.join(", ") }),
                  a.banned ? /* @__PURE__ */ jsxs4(Fragment3, { children: [
                    /* @__PURE__ */ jsx5(Badge, { tone: "good", children: "gesperrt" }),
                    canManage && /* @__PURE__ */ jsx5("button", { type: "button", onClick: () => void ban(h.host_id, h.host_name, a.ip, true), className: "text-white/45 hover:text-white", children: "entsperren" })
                  ] }) : canManage && f2b === "running" ? /* @__PURE__ */ jsx5("button", { type: "button", onClick: () => void ban(h.host_id, h.host_name, a.ip), className: "text-[var(--color-accent)] hover:underline", children: "Sperren" }) : null
                ] }, a.ip)) }),
                canManage && f2b === "none" && /* @__PURE__ */ jsx5("div", { className: "mt-2", children: /* @__PURE__ */ jsx5(Button, { small: true, onClick: () => void installFail2ban(h), children: "Fail2ban installieren" }) })
              ] }),
              v.ssh_findings === null && /* @__PURE__ */ jsxs4("section", { "data-testid": `sshd-${h.host_id}`, children: [
                /* @__PURE__ */ jsx5("p", { className: "mb-1.5 text-xs uppercase tracking-wider text-white/40", children: "SSH-Einstellungen" }),
                /* @__PURE__ */ jsx5("p", { className: "text-xs text-white/50", children: v.is_root ? "SSH-Einstellungen konnten nicht gelesen werden." : "SSH-Einstellungen nicht lesbar \u2013 daf\xFCr braucht Nodvard Deck root oder sudo ohne Passwort." })
              ] }),
              v.ssh_findings && /* @__PURE__ */ jsxs4("section", { "data-testid": `sshd-${h.host_id}`, children: [
                /* @__PURE__ */ jsxs4("p", { className: "mb-1.5 text-xs uppercase tracking-wider text-white/40", children: [
                  "SSH-Einstellungen",
                  v.ssh_port ? ` \xB7 Port ${v.ssh_port}` : ""
                ] }),
                v.ssh_findings.length === 0 ? /* @__PURE__ */ jsx5("p", { className: "text-xs text-emerald-300/80", children: "Sicher eingestellt \u2013 nur Schl\xFCssel, kein root mit Passwort." }) : /* @__PURE__ */ jsx5("ul", { className: "space-y-1 text-xs", children: v.ssh_findings.map((f) => /* @__PURE__ */ jsxs4("li", { className: "flex flex-wrap items-center gap-2", children: [
                  /* @__PURE__ */ jsx5(Badge, { tone: SEVERITY[f.severity]?.tone ?? "neutral", children: SEVERITY[f.severity]?.label ?? f.severity }),
                  /* @__PURE__ */ jsx5("span", { children: f.text }),
                  /* @__PURE__ */ jsxs4("span", { className: "text-white/45", children: [
                    "\u2192 besser: ",
                    /* @__PURE__ */ jsx5("code", { className: "font-mono text-white/70", children: f.advice })
                  ] })
                ] }, f.key)) })
              ] }),
              v.logins.length > 0 && /* @__PURE__ */ jsxs4("section", { children: [
                /* @__PURE__ */ jsx5("p", { className: "mb-1.5 text-xs uppercase tracking-wider text-white/40", children: "Letzte Anmeldungen" }),
                /* @__PURE__ */ jsx5("ul", { className: "space-y-0.5 text-xs", children: v.logins.slice(0, 5).map((l, i) => /* @__PURE__ */ jsxs4("li", { children: [
                  /* @__PURE__ */ jsxs4("span", { className: "font-mono", children: [
                    l.user,
                    "@",
                    l.ip
                  ] }),
                  " ",
                  /* @__PURE__ */ jsxs4("span", { className: "text-white/45", children: [
                    "\xB7 ",
                    l.method === "publickey" ? "Schl\xFCssel" : l.method === "password" ? "Passwort" : l.method,
                    " \xB7 ",
                    l.ts ? when(l.ts) : "\u2013"
                  ] })
                ] }, `${l.ip}-${i}`)) })
              ] }),
              /* @__PURE__ */ jsxs4("section", { children: [
                /* @__PURE__ */ jsxs4("p", { className: "mb-1.5 text-xs uppercase tracking-wider text-white/40", children: [
                  "Offene Ports (",
                  v.ports.length,
                  ")"
                ] }),
                /* @__PURE__ */ jsxs4("div", { className: "flex flex-wrap gap-1.5", children: [
                  v.ports.map((p) => /* @__PURE__ */ jsxs4(
                    "span",
                    {
                      title: p.dynamic ? `Zuf\xE4llige Ports, \xE4ndern sich nach jedem Neustart \xB7 ${p.process ?? "ohne Programm"}` : `${p.address}:${p.port} \xB7 ${p.process ?? "?"}${p.public ? "" : " \xB7 nur lokal"}`,
                      className: `rounded-md px-2 py-0.5 font-mono text-[11px] ${p.new ? "bg-red-500/20 text-red-200" : p.public ? "bg-white/[0.08] text-white/80" : "bg-white/[0.04] text-white/40"}`,
                      children: [
                        portName(p),
                        p.process ? ` ${p.process}` : "",
                        p.new ? " \xB7 neu" : ""
                      ]
                    },
                    p.key
                  )),
                  v.ports.length === 0 && /* @__PURE__ */ jsx5("span", { className: "text-xs text-white/45", children: "keine gefunden" })
                ] })
              ] }),
              /* @__PURE__ */ jsxs4("section", { children: [
                /* @__PURE__ */ jsx5("p", { className: "mb-1.5 text-xs uppercase tracking-wider text-white/40", children: "Datei-W\xE4chter" }),
                v.files_pending.length === 0 ? /* @__PURE__ */ jsxs4("p", { className: "text-xs text-emerald-300/80", children: [
                  v.files_watched,
                  " Dateien \xFCberwacht \u2013 keine unbest\xE4tigten \xC4nderungen."
                ] }) : /* @__PURE__ */ jsx5("ul", { className: "space-y-0.5 text-xs", children: v.files_pending.map((p) => /* @__PURE__ */ jsx5("li", { className: "font-mono text-amber-200", children: p }, p)) })
              ] })
            ] })
          ] })
        },
        h.host_id
      );
    }) })
  ] });
}

// src/UpdatesTab.tsx
import { useCallback as useCallback4, useEffect as useEffect6, useState as useState5 } from "react";
import { Fragment as Fragment4, jsx as jsx6, jsxs as jsxs5 } from "react/jsx-runtime";
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
function clock(ts) {
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
  const [data, setData] = useState5(null);
  const [error, setError] = useState5(null);
  const [notice, setNotice] = useState5(null);
  const [open, setOpen] = useState5(null);
  const [openRun, setOpenRun] = useState5(null);
  const [pending, setPending] = useState5({});
  const load = useCallback4(() => {
    call(`${API}/updates`).then((d) => {
      setData(d);
      setError(null);
    }).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);
  useEffect6(() => {
    load();
  }, [load]);
  const busy = Object.keys(pending).length > 0 || !!data?.hosts.some((h) => h.checking || h.busy);
  useEffect6(() => {
    if (!busy) return;
    const t = setInterval(load, 4e3);
    return () => clearInterval(t);
  }, [busy, load]);
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
  async function apply(h, mode) {
    const st = h.status;
    const distUpgrade = mode === "all" && st?.manager === "apt" && !!data?.config.proxmox_dist_upgrade;
    const questions = {
      security: `${st?.security_count ?? 0} Sicherheitsupdate(s) auf \u201E${h.host_name}\u201C jetzt einspielen?`,
      all: `Alle ${st?.count ?? 0} Update(s) auf \u201E${h.host_name}\u201C jetzt einspielen? Das kann einige Minuten dauern.${distUpgrade ? " Ist es ein Proxmox-Server, l\xE4uft dabei \u201Eapt-get dist-upgrade\u201C \u2013 das kann Pakete entfernen oder ersetzen." : ""}`,
      cleanup: `Auf \u201E${h.host_name}\u201C nicht mehr ben\xF6tigte Pakete und alte Kernel entfernen und den Paket-Cache leeren?`,
      reboot: `\u201E${h.host_name}\u201C jetzt neu starten? Der Server ist dann ein paar Minuten nicht erreichbar \u2013 alle Dienste darauf auch.`
    };
    const labels = { security: "Einspielen", all: "Alle einspielen", cleanup: "Aufr\xE4umen", reboot: "Neu starten" };
    const ok = await deck().confirmDialog(questions[mode], { confirmLabel: labels[mode], danger: mode === "reboot" || distUpgrade });
    if (!ok) return;
    setPending((p) => ({ ...p, [h.host_id]: mode }));
    setNotice({ kind: "ok", text: mode === "reboot" ? `Neustart von ${h.host_name} wird ausgel\xF6st \u2026` : `${h.host_name}: l\xE4uft \u2013 du kannst die Seite offen lassen oder weiterarbeiten.` });
    try {
      const text = await proposeAndApprove(`${API}/hosts/${h.host_id}/upgrade`, { mode }, mode === "reboot" ? "high" : "medium", unmountSignal());
      setNotice({ kind: text.startsWith("Fehlgeschlagen") ? "error" : "ok", text: `${h.host_name}: ${text}` });
    } catch (err) {
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
    try {
      const text = await proposeAndApprove(`${API}/hosts/${h.host_id}/install`, { package: "unattended" }, "medium", unmountSignal());
      setNotice({ kind: text.startsWith("Fehlgeschlagen") ? "error" : "ok", text: `${h.host_name}: ${text}` });
      void checkAll([h.host_id]);
    } catch (err) {
      setNotice({ kind: "error", text: err instanceof Error ? err.message : String(err) });
    }
  }
  const hosts = useForHost(data?.hosts ?? []);
  const runs = useForHost(data?.runs ?? []);
  if (error) return /* @__PURE__ */ jsx6(Notice, { text: `Fehler: ${error}` });
  if (!data) return /* @__PURE__ */ jsx6("p", { className: "text-sm text-white/50", children: "Lade \u2026" });
  const { summary: sm, config: c } = data;
  const unsupported = data.hosts.filter((h) => h.status?.unsupported).length;
  if (hosts.length === 0) {
    return /* @__PURE__ */ jsx6(EmptyState, { icon: "package", title: "Keine Server", text: "Sobald Linux-Server mit SSH-Zugang angelegt sind, zeigt die Update-Zentrale hier ihre anstehenden Updates.", action: /* @__PURE__ */ jsx6(ServerSetupLink, { className: "text-sm" }) });
  }
  return /* @__PURE__ */ jsxs5(Fragment4, { children: [
    notice && /* @__PURE__ */ jsx6(Notice, { text: notice.text, kind: notice.kind, onClose: () => setNotice(null) }),
    /* @__PURE__ */ jsxs5("div", { className: "mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4", children: [
      /* @__PURE__ */ jsx6(
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
      /* @__PURE__ */ jsx6(Stat, { label: "Offene Updates", value: sm.packages, tone: sm.packages ? "warn" : "good" }),
      /* @__PURE__ */ jsx6(Stat, { label: "Sicherheitsupdates", value: sm.security, tone: sm.security ? "bad" : "good" }),
      /* @__PURE__ */ jsx6(Stat, { label: "Neustart n\xF6tig", value: sm.reboot, tone: sm.reboot ? "warn" : void 0, hint: sm.errors ? `${sm.errors} Pr\xFCfung(en) fehlgeschlagen` : void 0 })
    ] }),
    /* @__PURE__ */ jsxs5("div", { className: "mb-4 flex flex-wrap items-center gap-2", children: [
      /* @__PURE__ */ jsxs5(Badge, { tone: c.check_enabled ? "info" : "neutral", children: [
        /* @__PURE__ */ jsx6(Icon, { name: "clock", size: 11 }),
        " Pr\xFCfung: ",
        c.check_enabled ? describeSchedule(c.check_cron, "aus") : "aus"
      ] }),
      /* @__PURE__ */ jsxs5(Badge, { tone: c.auto_enabled ? "good" : "neutral", children: [
        "Automatisch einspielen: ",
        c.auto_enabled ? `${c.auto_mode === "all" ? "alle Updates" : "Sicherheitsupdates"} \xB7 ${describeSchedule(c.auto_cron, "aus")}${c.auto_tag ? ` \xB7 nur \u201E${c.auto_tag}\u201C` : ""}` : "aus"
      ] }),
      c.auto_enabled && /* @__PURE__ */ jsxs5(Badge, { tone: c.auto_reboot ? "warn" : "neutral", children: [
        "Neustart danach: ",
        c.auto_reboot ? "automatisch" : "nein"
      ] }),
      /* @__PURE__ */ jsx6("span", { className: "flex-1" }),
      canManage && /* @__PURE__ */ jsxs5(Button, { onClick: () => void checkAll(), children: [
        /* @__PURE__ */ jsx6(Icon, { name: "refresh", size: 14 }),
        " Alle jetzt pr\xFCfen"
      ] })
    ] }),
    /* @__PURE__ */ jsx6("div", { className: "space-y-3", "data-testid": "update-hosts", children: hosts.map((h) => {
      const st = h.status;
      const state = hostState(h);
      const running = pending[h.host_id] ?? h.busy;
      const rebootSince = st?.reboot_pending_since;
      const needsReboot = !!st?.reboot_required && !rebootSince;
      const isOpen = open === h.host_id;
      return /* @__PURE__ */ jsxs5("div", { className: "panel px-4 py-3", "data-testid": `updates-${h.host_id}`, children: [
        /* @__PURE__ */ jsxs5("div", { className: "flex flex-wrap items-center gap-3", children: [
          /* @__PURE__ */ jsxs5("div", { className: "min-w-0 flex-1", children: [
            /* @__PURE__ */ jsxs5("p", { className: "flex flex-wrap items-center gap-2 text-sm font-medium", children: [
              h.host_name,
              /* @__PURE__ */ jsx6(Badge, { tone: state.tone, children: state.label }),
              needsReboot && !h.busy && /* @__PURE__ */ jsx6(Badge, { tone: "warn", children: "Neustart n\xF6tig" }),
              st?.unattended === true && /* @__PURE__ */ jsx6(Badge, { tone: "good", children: "Auto-Sicherheitsupdates an" })
            ] }),
            /* @__PURE__ */ jsxs5("p", { className: "mt-0.5 text-xs text-white/45", children: [
              st?.manager ? `${st.manager} \xB7 Kernel ${st.kernel ?? "?"} \xB7 l\xE4uft seit ${uptime(st.uptime_s)} \xB7 ` : "",
              "gepr\xFCft ",
              ago(st?.checked_at)
            ] }),
            st?.error && /* @__PURE__ */ jsx6("p", { className: `mt-1 text-xs ${st.unsupported ? "text-white/50" : "text-amber-300"}`, children: st.error }),
            st?.refresh_error && /* @__PURE__ */ jsx6("p", { className: "mt-1 text-xs text-amber-300/80", children: st.refresh_error }),
            needsReboot && st.reboot_reasons.length > 0 && /* @__PURE__ */ jsxs5("p", { className: "mt-1 text-xs text-white/50", children: [
              "Grund: ",
              st.reboot_reasons.slice(0, 3).join(", ")
            ] }),
            rebootSince && !h.busy && /* @__PURE__ */ jsxs5("p", { className: "mt-1 text-xs text-sky-300", children: [
              "Neustart ausgel\xF6st (",
              clock(rebootSince),
              ") \u2013 die n\xE4chste Pr\xFCfung zeigt den neuen Stand."
            ] }),
            h.last_run && /* @__PURE__ */ jsxs5("p", { className: "mt-1 text-xs text-white/45", children: [
              "Zuletzt: ",
              h.last_run.mode_label,
              " ",
              when(h.last_run.started_at),
              " \u2013",
              " ",
              /* @__PURE__ */ jsx6("span", { className: h.last_run.status === "error" ? "text-red-300" : "", children: h.last_run.summary ?? RUN_STATUS[h.last_run.status]?.label })
            ] })
          ] }),
          canManage && /* @__PURE__ */ jsxs5("div", { className: "flex flex-wrap gap-1", children: [
            !!st?.security_count && /* @__PURE__ */ jsx6(Button, { small: true, variant: "primary", disabled: !!running, onClick: () => void apply(h, "security"), children: "Sicherheitsupdates einspielen" }),
            !!st?.count && /* @__PURE__ */ jsx6(Button, { small: true, variant: st.security_count ? "secondary" : "primary", disabled: !!running, onClick: () => void apply(h, "all"), children: "Alle einspielen" }),
            needsReboot && /* @__PURE__ */ jsx6(Button, { small: true, variant: "danger", disabled: !!running, onClick: () => void apply(h, "reboot"), children: "Neu starten" }),
            /* @__PURE__ */ jsxs5(Button, { small: true, variant: "ghost", disabled: h.checking || !!running, onClick: () => void checkAll([h.host_id]), ariaLabel: `${h.host_name} pr\xFCfen`, children: [
              /* @__PURE__ */ jsx6(Icon, { name: "refresh", size: 12 }),
              " Pr\xFCfen"
            ] })
          ] })
        ] }),
        st && st.count > 0 && /* @__PURE__ */ jsxs5("button", { type: "button", onClick: () => setOpen(isOpen ? null : h.host_id), className: "mt-2 flex items-center gap-1 text-xs text-white/55 hover:text-white", children: [
          /* @__PURE__ */ jsx6(Icon, { name: isOpen ? "chevron-down" : "chevron-right", size: 12 }),
          " ",
          st.count,
          " Paket",
          st.count === 1 ? "" : "e",
          " anzeigen"
        ] }),
        isOpen && st && /* @__PURE__ */ jsx6("div", { className: "mt-2 max-h-80 overflow-auto rounded-lg border border-white/[0.06]", children: /* @__PURE__ */ jsxs5("table", { className: "w-full text-left text-xs", children: [
          /* @__PURE__ */ jsx6("thead", { className: "sticky top-0 bg-[#15171c] text-white/45", children: /* @__PURE__ */ jsxs5("tr", { children: [
            /* @__PURE__ */ jsx6("th", { className: "px-3 py-1.5 font-medium", children: "Paket" }),
            /* @__PURE__ */ jsx6("th", { className: "px-3 py-1.5 font-medium", children: "installiert" }),
            /* @__PURE__ */ jsx6("th", { className: "px-3 py-1.5 font-medium", children: "neu" }),
            /* @__PURE__ */ jsx6("th", { className: "px-3 py-1.5" })
          ] }) }),
          /* @__PURE__ */ jsx6("tbody", { className: "divide-y divide-white/[0.04]", children: st.packages.map((p) => /* @__PURE__ */ jsxs5("tr", { children: [
            /* @__PURE__ */ jsx6("td", { className: "px-3 py-1.5 font-mono", children: p.name }),
            /* @__PURE__ */ jsx6("td", { className: "px-3 py-1.5 font-mono text-white/50", children: p.current_version ?? "\u2013" }),
            /* @__PURE__ */ jsx6("td", { className: "px-3 py-1.5 font-mono", children: p.new_version }),
            /* @__PURE__ */ jsx6("td", { className: "px-3 py-1.5 text-right", children: p.security && /* @__PURE__ */ jsx6(Badge, { tone: "bad", children: "Sicherheit" }) })
          ] }, p.name)) })
        ] }) }),
        canManage && st?.manager === "apt" && st.unattended === false && /* @__PURE__ */ jsxs5("p", { className: "mt-2 flex flex-wrap items-center gap-2 text-xs text-white/50", children: [
          "Tipp: Der Server kann Sicherheitsupdates auch jeden Tag selbst einspielen.",
          /* @__PURE__ */ jsx6("button", { type: "button", onClick: () => void installUnattended(h), className: "text-[var(--color-accent)] hover:underline", children: "Einrichten" }),
          /* @__PURE__ */ jsx6("span", { className: "text-white/25", children: "\xB7" }),
          /* @__PURE__ */ jsx6("button", { type: "button", onClick: () => void apply(h, "cleanup"), className: "text-white/60 hover:text-white hover:underline", children: "Aufr\xE4umen" })
        ] })
      ] }, h.host_id);
    }) }),
    /* @__PURE__ */ jsx6(Card, { title: "Verlauf", description: "Eingespielte Updates, Aufr\xE4umen und Neustarts \u2013 manuell und automatisch.", className: "mt-5", padded: false, children: /* @__PURE__ */ jsxs5("ul", { className: "divide-y divide-white/[0.05]", "data-testid": "update-runs", children: [
      runs.length === 0 && /* @__PURE__ */ jsx6("li", { className: "px-5 py-6 text-sm text-white/50", children: "Noch nichts eingespielt." }),
      runs.map((r) => {
        const rs = RUN_STATUS[r.status] ?? { label: r.status, tone: "neutral" };
        return /* @__PURE__ */ jsxs5("li", { className: "px-5 py-2.5 text-sm", children: [
          /* @__PURE__ */ jsxs5("div", { className: "flex flex-wrap items-center gap-2", children: [
            /* @__PURE__ */ jsx6(Badge, { tone: rs.tone, children: rs.label }),
            /* @__PURE__ */ jsx6("span", { className: "font-medium", children: r.host_name }),
            /* @__PURE__ */ jsx6("span", { className: "text-white/60", children: r.mode_label }),
            r.trigger === "schedule" && /* @__PURE__ */ jsx6(Badge, { children: "automatisch" }),
            /* @__PURE__ */ jsx6("span", { className: "text-xs text-white/40", children: when(r.started_at) }),
            /* @__PURE__ */ jsx6("span", { className: "min-w-0 flex-1 truncate text-xs text-white/55", children: r.summary }),
            r.output_tail && /* @__PURE__ */ jsx6("button", { type: "button", onClick: () => setOpenRun(openRun === r.id ? null : r.id), className: "text-xs text-white/50 hover:text-white", children: openRun === r.id ? "Ausgabe ausblenden" : "Ausgabe" })
          ] }),
          openRun === r.id && r.output_tail && /* @__PURE__ */ jsx6("pre", { className: "mt-2 max-h-72 overflow-auto rounded-lg bg-black/40 p-3 font-mono text-[11px] text-white/70", children: r.output_tail })
        ] }, r.id);
      })
    ] }) })
  ] });
}

// src/SocPage.tsx
import { Fragment as Fragment5, jsx as jsx7, jsxs as jsxs6 } from "react/jsx-runtime";
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
var FINDING_STATUS = {
  detected: { label: "offen", tone: "bad" },
  quarantined: { label: "in Quarant\xE4ne", tone: "warn" },
  restored: { label: "wiederhergestellt", tone: "neutral" },
  deleted: { label: "gel\xF6scht", tone: "good" },
  ignored: { label: "ignoriert", tone: "neutral" }
};
function ScoreRing({ score }) {
  const r = 42;
  const c = 2 * Math.PI * r;
  const tone = score >= 80 ? "#34d399" : score >= 50 ? "#fbbf24" : "#f87171";
  const label = score >= 80 ? "Gut gesch\xFCtzt" : score >= 50 ? "Verbesserungsbedarf" : "Handlungsbedarf";
  return /* @__PURE__ */ jsxs6("div", { className: "flex items-center gap-5", children: [
    /* @__PURE__ */ jsxs6("svg", { width: "104", height: "104", viewBox: "0 0 104 104", role: "img", "aria-label": `Schutzwert ${score} von 100`, children: [
      /* @__PURE__ */ jsx7("circle", { cx: "52", cy: "52", r, fill: "none", stroke: "rgba(255,255,255,0.08)", strokeWidth: "9" }),
      /* @__PURE__ */ jsx7(
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
      /* @__PURE__ */ jsx7("text", { x: "52", y: "58", textAnchor: "middle", className: "fill-white text-[22px] font-semibold", children: score })
    ] }),
    /* @__PURE__ */ jsxs6("div", { children: [
      /* @__PURE__ */ jsx7("p", { className: "text-xs uppercase tracking-wider text-white/45", children: "Schutzwert" }),
      /* @__PURE__ */ jsx7("p", { className: "text-lg font-semibold", style: { color: tone }, children: label }),
      /* @__PURE__ */ jsx7("p", { className: "mt-1 max-w-xs text-xs text-white/45", children: "Aus Abdeckung (ClamAV), aktuellen Scans, offenen Funden und H\xE4rtungsindex." })
    ] })
  ] });
}
function SocPage() {
  const unmountSignal = useUnmountSignal();
  const [urlParams, updateUrl] = useUrlParams();
  const tabParam = urlParams.get("tab");
  const tab = TABS.find((x) => x.id === tabParam)?.id ?? "overview";
  const setTab = useCallback5((t) => updateUrl({ tab: t === "overview" ? null : t }), [updateUrl]);
  const [overview, setOverview] = useState6(null);
  const [error, setError] = useState6(null);
  const [notice, setNotice] = useState6(null);
  const canManage = deck().hasPermission("soc.manage");
  const hostFilter = urlParams.get("host") || null;
  const showAllHosts = () => updateUrl({ host: null });
  const loadOverview = useCallback5((refresh = false) => {
    call(`${API}/overview${refresh ? "?refresh=true" : ""}`).then((o) => {
      setOverview(o);
      setError(null);
    }).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);
  useEffect7(() => {
    loadOverview();
  }, [loadOverview]);
  useEffect7(() => {
    document.querySelector('[role="tab"][aria-selected="true"]')?.scrollIntoView?.({ block: "nearest", inline: "center" });
  }, [tab]);
  const [side, setSide] = useState6({ updates: null, guard: null });
  useEffect7(() => {
    call(`${API}/updates`).then((u) => setSide((p) => ({ ...p, updates: u.summary }))).catch(() => void 0);
    call(`${API}/guard`).then((g) => setSide((p) => ({ ...p, guard: g.summary }))).catch(() => void 0);
  }, [tab]);
  useEffect7(() => {
    const busy = overview?.hosts.some((h) => h.scanning || h.auditing);
    if (!busy) return;
    const t = setInterval(() => loadOverview(), 5e3);
    return () => clearInterval(t);
  }, [overview, loadOverview]);
  async function run(label, fn) {
    setNotice(null);
    try {
      const text = await fn();
      setNotice({ kind: text.startsWith("Fehlgeschlagen") ? "error" : "ok", text: `${label}: ${text}` });
      loadOverview();
    } catch (err) {
      setNotice({ kind: "error", text: `${label}: ${err instanceof Error ? err.message : String(err)}` });
    }
  }
  const startScan = (kind, hostIds = "all") => run(kind === "quick" ? "Schnellscan" : "Tiefenscan", async () => {
    const r = await call(`${API}/scans`, { method: "POST", body: JSON.stringify({ kind, host_ids: hostIds }) });
    return r.scans.length ? `${r.scans.length} Server werden gepr\xFCft.` : "L\xE4uft bereits oder kein Server verf\xFCgbar.";
  });
  const startAudit = (hostIds = "all") => run("H\xE4rtungs-Audit", async () => {
    const r = await call(`${API}/audits`, { method: "POST", body: JSON.stringify({ host_ids: hostIds }) });
    return `${r.hosts} Server werden gepr\xFCft (dauert einige Minuten).`;
  });
  const sendBriefing = () => run("Briefing", async () => {
    const r = await call(`${API}/briefing`, { method: "POST" });
    return `gesendet \u2013 \u201E${r.title}\u201C`;
  });
  const install = (host, pkg) => run(pkg === "signatures" ? `Signaturen (${host.host_name})` : `${pkg === "clamav" ? "ClamAV" : "Lynis"} installieren (${host.host_name})`, async () => {
    const ok = await deck().confirmDialog(
      pkg === "signatures" ? `Virensignaturen auf \u201E${host.host_name}\u201C jetzt aktualisieren? Das automatische Signatur-Update wird dabei eingeschaltet.` : `${pkg === "clamav" ? "ClamAV" : "Lynis"} auf \u201E${host.host_name}\u201C installieren?`,
      { confirmLabel: pkg === "signatures" ? "Aktualisieren" : "Installieren" }
    );
    if (!ok) return "abgebrochen.";
    const text = await proposeAndApprove(`${API}/hosts/${host.host_id}/install`, { package: pkg }, pkg === "signatures" ? "low" : "medium", unmountSignal());
    loadOverview(true);
    return text;
  });
  return /* @__PURE__ */ jsxs6(
    Page,
    {
      title: "Nodvard Shield",
      description: "Virenschutz, Quarant\xE4ne und H\xE4rtung f\xFCr alle Server \u2013 plus die KI-Container-Wache (Nodvard KI) f\xFCr Docker-Container.",
      actions: canManage && /* @__PURE__ */ jsxs6(Fragment5, { children: [
        /* @__PURE__ */ jsx7(Button, { onClick: () => void sendBriefing(), children: "Briefing senden" }),
        /* @__PURE__ */ jsxs6(Button, { onClick: () => void startAudit(), children: [
          /* @__PURE__ */ jsx7(Icon, { name: "clock", size: 14 }),
          " H\xE4rtungs-Audit"
        ] }),
        /* @__PURE__ */ jsxs6(Button, { onClick: () => void startScan("deep"), children: [
          /* @__PURE__ */ jsx7(Icon, { name: "search", size: 14 }),
          " Tiefenscan"
        ] }),
        /* @__PURE__ */ jsxs6(Button, { variant: "primary", onClick: () => void startScan("quick"), children: [
          /* @__PURE__ */ jsx7(Icon, { name: "play", size: 14 }),
          " Schnellscan starten"
        ] })
      ] }),
      children: [
        /* @__PURE__ */ jsx7("nav", { role: "tablist", "aria-label": "Bereiche", className: "mb-5 flex gap-1 overflow-x-auto border-b border-white/[0.08] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden", children: TABS.map((t) => /* @__PURE__ */ jsxs6(
          "button",
          {
            role: "tab",
            "aria-selected": tab === t.id,
            onClick: () => setTab(t.id),
            className: `-mb-px whitespace-nowrap border-b-2 px-3.5 py-2 text-sm transition ${tab === t.id ? "border-[var(--color-accent)] text-white" : "border-transparent text-white/55 hover:text-white"}`,
            children: [
              t.label,
              t.id === "quarantine" && overview && overview.summary.open_threats > 0 && /* @__PURE__ */ jsx7("span", { className: "ml-1.5 rounded-full bg-red-500/80 px-1.5 text-[10px] text-white", children: overview.summary.open_threats }),
              t.id === "updates" && !!side.updates?.security && /* @__PURE__ */ jsx7("span", { className: "ml-1.5 rounded-full bg-red-500/80 px-1.5 text-[10px] text-white", title: "Sicherheitsupdates", children: side.updates.security }),
              t.id === "guard" && !!side.guard?.open_events && /* @__PURE__ */ jsx7("span", { className: "ml-1.5 rounded-full bg-red-500/80 px-1.5 text-[10px] text-white", title: "offene Ereignisse", children: side.guard.open_events })
            ]
          },
          t.id
        )) }),
        hostFilter && /* @__PURE__ */ jsxs6("div", { className: "mb-4 flex flex-wrap items-center gap-2 rounded-lg border border-[color-mix(in_srgb,var(--color-accent)_35%,transparent)] bg-[color-mix(in_srgb,var(--color-accent)_10%,transparent)] px-3 py-2 text-sm", "data-testid": "host-filter", children: [
          /* @__PURE__ */ jsxs6("span", { children: [
            "Ansicht f\xFCr ",
            /* @__PURE__ */ jsx7("strong", { children: overview?.hosts.find((h) => h.host_id === hostFilter)?.host_name ?? "diesen Server" })
          ] }),
          /* @__PURE__ */ jsx7("span", { className: "flex-1" }),
          /* @__PURE__ */ jsx7("button", { type: "button", onClick: showAllHosts, className: "text-xs text-white/70 hover:text-white hover:underline", children: "Alle Server anzeigen" })
        ] }),
        /* @__PURE__ */ jsxs6(HostFilter.Provider, { value: hostFilter, children: [
          notice && /* @__PURE__ */ jsx7(Notice, { text: notice.text, kind: notice.kind, onClose: () => setNotice(null) }),
          error && ["overview", "scans", "quarantine", "hardening"].includes(tab) && /* @__PURE__ */ jsx7(Notice, { text: `Fehler: ${error}` }),
          tab === "overview" && overview && /* @__PURE__ */ jsx7(
            OverviewTab,
            {
              overview,
              canManage,
              onRefresh: () => loadOverview(true),
              onScan: (h) => void startScan("quick", [h.host_id]),
              onAudit: (h) => void startAudit([h.host_id]),
              onInstall: (h, p) => void install(h, p),
              onOpenThreats: () => setTab("quarantine"),
              side,
              onOpen: setTab
            }
          ),
          tab === "overview" && !overview && !error && /* @__PURE__ */ jsx7("p", { className: "text-sm text-white/50", children: "Lade \u2026" }),
          tab === "scans" && /* @__PURE__ */ jsx7(ScansTab, { hosts: overview?.hosts ?? [], canManage, onStarted: () => loadOverview() }),
          tab === "quarantine" && /* @__PURE__ */ jsx7(QuarantineTab, { canManage, onChanged: () => loadOverview() }),
          tab === "hardening" && /* @__PURE__ */ jsx7(HardeningTab, { canManage, onAudit: () => void startAudit() }),
          tab === "updates" && /* @__PURE__ */ jsx7(UpdatesTab, { canManage }),
          tab === "guard" && /* @__PURE__ */ jsx7(GuardTab, { canManage }),
          tab === "containers" && /* @__PURE__ */ jsx7(ContainerWatch, {})
        ] })
      ]
    }
  );
}
function SideLink({ title, text, tone, onClick }) {
  const dot = tone === "good" ? "bg-emerald-400" : tone === "warn" ? "bg-amber-400" : "bg-red-400";
  return /* @__PURE__ */ jsxs6("button", { type: "button", onClick, className: "panel flex items-center gap-3 px-4 py-3 text-left transition hover:bg-white/[0.04]", children: [
    /* @__PURE__ */ jsx7("span", { className: `h-2.5 w-2.5 flex-none rounded-full ${dot}` }),
    /* @__PURE__ */ jsxs6("span", { className: "min-w-0 flex-1", children: [
      /* @__PURE__ */ jsx7("span", { className: "block text-xs text-white/50", children: title }),
      /* @__PURE__ */ jsx7("span", { className: "block truncate text-sm", children: text })
    ] }),
    /* @__PURE__ */ jsx7("span", { className: "text-white/35", children: "\u2192" })
  ] });
}
function OverviewTab({
  overview,
  canManage,
  onRefresh,
  onScan,
  onAudit,
  onInstall,
  onOpenThreats,
  side,
  onOpen
}) {
  const s = overview.summary;
  const c = overview.config;
  const hosts = useForHost(overview.hosts);
  return /* @__PURE__ */ jsxs6(Fragment5, { children: [
    /* @__PURE__ */ jsxs6("div", { className: "mb-5 grid grid-cols-1 gap-4 lg:grid-cols-[auto_1fr]", children: [
      /* @__PURE__ */ jsx7("div", { className: "panel flex items-center px-6 py-4", children: /* @__PURE__ */ jsx7(ScoreRing, { score: s.score }) }),
      /* @__PURE__ */ jsxs6("div", { className: "grid grid-cols-2 gap-3 md:grid-cols-4", children: [
        /* @__PURE__ */ jsx7(Stat, { label: "Gesch\xFCtzte Server", value: `${s.protected} / ${s.hosts}`, hint: "ClamAV installiert", tone: s.protected < s.hosts ? "warn" : "good" }),
        /* @__PURE__ */ jsx7("button", { type: "button", onClick: onOpenThreats, className: "text-left [&>div]:h-full", children: /* @__PURE__ */ jsx7(Stat, { label: "Offene Bedrohungen", value: s.open_threats, hint: s.open_threats ? "Jetzt pr\xFCfen \u2192" : "keine", tone: s.open_threats ? "bad" : "good" }) }),
        /* @__PURE__ */ jsx7(Stat, { label: "Neutralisiert", value: s.neutralized_total, hint: `${s.quarantined} in Quarant\xE4ne \xB7 ${s.findings_30d} Funde in 30 Tagen` }),
        /* @__PURE__ */ jsx7(Stat, { label: "H\xE4rtung (\xD8 Lynis)", value: s.avg_hardening ?? "\u2013", hint: "von 100", tone: s.avg_hardening == null ? void 0 : s.avg_hardening >= 70 ? "good" : "warn" })
      ] })
    ] }),
    (side.updates?.checked || side.guard) && /* @__PURE__ */ jsxs6("div", { className: "mb-5 grid gap-3 md:grid-cols-2", "data-testid": "side-summary", children: [
      !!side.updates?.checked && /* @__PURE__ */ jsx7(
        SideLink,
        {
          title: "Updates",
          tone: side.updates.security ? "bad" : side.updates.packages || side.updates.reboot ? "warn" : "good",
          text: side.updates.security ? `${side.updates.security} Sicherheitsupdate(s) offen` : side.updates.packages ? `${side.updates.packages} Update(s) offen` : `Alle ${side.updates.hosts} Server aktuell`,
          onClick: () => onOpen("updates")
        }
      ),
      side.guard && /* @__PURE__ */ jsx7(
        SideLink,
        {
          title: "Einbruchschutz",
          tone: side.guard.open_events ? "bad" : side.guard.fail2ban_running < side.guard.hosts ? "warn" : "good",
          text: side.guard.open_events ? `${side.guard.open_events} offene(s) Ereignis(se)` : `ruhig \xB7 ${side.guard.failed_24h} SSH-Fehlversuche in 24 Std., ${side.guard.banned} gesperrt`,
          onClick: () => onOpen("guard")
        }
      )
    ] }),
    /* @__PURE__ */ jsxs6("div", { className: "mb-5 flex flex-wrap gap-2 text-xs", children: [
      /* @__PURE__ */ jsxs6(Badge, { tone: c.realtime_enabled ? "good" : "warn", children: [
        "Echtzeit-W\xE4chter ",
        c.realtime_enabled ? `aktiv (alle ${c.watch_interval_min} Min.)` : "aus"
      ] }),
      /* @__PURE__ */ jsxs6(Badge, { tone: c.auto_quarantine ? "good" : "warn", children: [
        "Automatische Quarant\xE4ne ",
        c.auto_quarantine ? "an" : "aus"
      ] }),
      /* @__PURE__ */ jsxs6(Badge, { children: [
        "Schnellscan: ",
        describeSchedule(c.quick_scan_cron, "aus")
      ] }),
      /* @__PURE__ */ jsxs6(Badge, { children: [
        "Tiefenscan: ",
        describeSchedule(c.deep_scan_cron, "aus")
      ] }),
      /* @__PURE__ */ jsxs6(Badge, { children: [
        "Audit: ",
        describeSchedule(c.audit_cron, "aus")
      ] }),
      /* @__PURE__ */ jsx7("a", { href: "/settings/extensions/nexus-soc", className: "text-white/50 underline-offset-2 hover:text-white hover:underline", children: "Einstellungen \xE4ndern" })
    ] }),
    /* @__PURE__ */ jsx7(Card, { title: "Server", padded: false, actions: /* @__PURE__ */ jsxs6(Button, { small: true, variant: "ghost", onClick: onRefresh, children: [
      /* @__PURE__ */ jsx7(Icon, { name: "refresh", size: 12 }),
      " Status neu pr\xFCfen"
    ] }), children: hosts.length === 0 ? /* @__PURE__ */ jsxs6("p", { className: "px-5 py-6 text-sm text-white/50", children: [
      "Keine Linux-Server mit SSH-Zugang gefunden. Einrichten unter",
      " ",
      deck().hasPermission("hosts.write") ? /* @__PURE__ */ jsx7("a", { href: "/settings/hosts", className: "underline underline-offset-2 hover:text-white", children: "Einstellungen \u2192 Server & Zug\xE4nge" }) : "Einstellungen \u2192 Server & Zug\xE4nge",
      "."
    ] }) : /* @__PURE__ */ jsx7("div", { className: "overflow-x-auto", children: /* @__PURE__ */ jsxs6("table", { className: "w-full text-left text-sm", children: [
      /* @__PURE__ */ jsx7("thead", { className: "text-xs uppercase tracking-wider text-white/40", children: /* @__PURE__ */ jsxs6("tr", { className: "border-b border-white/[0.06]", children: [
        /* @__PURE__ */ jsx7("th", { className: "px-5 py-2.5 font-medium", children: "Server" }),
        /* @__PURE__ */ jsx7("th", { className: "px-3 py-2.5 font-medium", children: "Virenschutz" }),
        /* @__PURE__ */ jsx7("th", { className: "px-3 py-2.5 font-medium", children: "Letzter Scan" }),
        /* @__PURE__ */ jsx7("th", { className: "px-3 py-2.5 font-medium", children: "H\xE4rtung" }),
        /* @__PURE__ */ jsx7("th", { className: "px-3 py-2.5 font-medium" })
      ] }) }),
      /* @__PURE__ */ jsx7("tbody", { className: "divide-y divide-white/[0.04]", children: hosts.map((h) => /* @__PURE__ */ jsxs6("tr", { "data-testid": `host-${h.host_id}`, children: [
        /* @__PURE__ */ jsxs6("td", { className: "px-5 py-3", children: [
          /* @__PURE__ */ jsx7("p", { className: "font-medium", children: h.host_name }),
          h.reachable === false && /* @__PURE__ */ jsxs6("p", { className: "text-xs text-amber-300", children: [
            "nicht erreichbar",
            h.error ? ` \u2013 ${h.error}` : ""
          ] })
        ] }),
        /* @__PURE__ */ jsx7("td", { className: "px-3 py-3", children: h.clamav_installed ? /* @__PURE__ */ jsxs6(Fragment5, { children: [
          /* @__PURE__ */ jsxs6(Badge, { tone: "good", children: [
            "ClamAV ",
            h.clamav_version
          ] }),
          /* @__PURE__ */ jsxs6("p", { className: "mt-0.5 text-xs text-white/45", children: [
            "Signaturen ",
            h.signature_version ?? "?",
            h.signature_date ? ` \xB7 ${h.signature_date}` : "",
            h.freshclam_active === false && /* @__PURE__ */ jsx7("span", { className: "text-amber-300", children: " \xB7 Auto-Update aus" })
          ] })
        ] }) : h.reachable === false ? /* @__PURE__ */ jsx7(Badge, { children: "unbekannt" }) : /* @__PURE__ */ jsx7(Badge, { tone: "bad", children: "nicht installiert" }) }),
        /* @__PURE__ */ jsx7("td", { className: "px-3 py-3", children: h.scanning ? /* @__PURE__ */ jsx7(Badge, { tone: "info", children: "Scan l\xE4uft \u2026" }) : h.last_scan ? /* @__PURE__ */ jsxs6(Fragment5, { children: [
          /* @__PURE__ */ jsx7(Badge, { tone: SCAN_STATUS[h.last_scan.status]?.tone ?? "neutral", children: SCAN_STATUS[h.last_scan.status]?.label ?? h.last_scan.status }),
          /* @__PURE__ */ jsxs6("p", { className: "mt-0.5 text-xs text-white/45", children: [
            h.last_scan.kind_label,
            " \xB7 ",
            ago(h.last_scan.started_at)
          ] })
        ] }) : /* @__PURE__ */ jsx7("span", { className: "text-xs text-white/40", children: "noch nie" }) }),
        /* @__PURE__ */ jsx7("td", { className: "px-3 py-3", children: h.auditing ? /* @__PURE__ */ jsx7(Badge, { tone: "info", children: "Audit l\xE4uft \u2026" }) : h.last_audit?.hardening_index != null ? /* @__PURE__ */ jsxs6("div", { className: "w-28", children: [
          /* @__PURE__ */ jsxs6("div", { className: "flex justify-between text-xs", children: [
            /* @__PURE__ */ jsx7("span", { children: h.last_audit.hardening_index }),
            /* @__PURE__ */ jsxs6("span", { className: "text-white/40", children: [
              h.last_audit.warnings,
              " Warn."
            ] })
          ] }),
          /* @__PURE__ */ jsx7("div", { className: "mt-1 h-1.5 rounded-full bg-white/10", children: /* @__PURE__ */ jsx7("div", { className: "h-1.5 rounded-full", style: { width: `${h.last_audit.hardening_index}%`, background: h.last_audit.hardening_index >= 70 ? "#34d399" : "#fbbf24" } }) })
        ] }) : h.last_audit?.error ? /* @__PURE__ */ jsx7("span", { className: "text-xs text-amber-300", children: h.last_audit.error }) : /* @__PURE__ */ jsx7("span", { className: "text-xs text-white/40", children: "\u2013" }) }),
        /* @__PURE__ */ jsx7("td", { className: "px-3 py-3", children: canManage && /* @__PURE__ */ jsxs6("div", { className: "flex flex-wrap justify-end gap-1", children: [
          h.clamav_installed && /* @__PURE__ */ jsx7(Button, { small: true, onClick: () => onScan(h), disabled: h.scanning, children: "Scannen" }),
          h.clamav_installed && /* @__PURE__ */ jsx7(Button, { small: true, variant: "ghost", onClick: () => onInstall(h, "signatures"), children: "Signaturen" }),
          !h.clamav_installed && h.reachable !== false && /* @__PURE__ */ jsx7(Button, { small: true, variant: "primary", onClick: () => onInstall(h, "clamav"), children: "ClamAV installieren" }),
          !h.lynis_installed && h.reachable !== false && /* @__PURE__ */ jsx7(Button, { small: true, onClick: () => onInstall(h, "lynis"), children: "Lynis installieren" }),
          h.lynis_installed && /* @__PURE__ */ jsx7(Button, { small: true, variant: "ghost", onClick: () => onAudit(h), disabled: h.auditing, children: "Audit" })
        ] }) })
      ] }, h.host_id)) })
    ] }) }) })
  ] });
}
function visibleScanOutput(tail) {
  return tail.split("\n").filter((line) => !line.startsWith("@@")).join("\n").trim();
}
function ScansTab({ hosts, canManage, onStarted }) {
  const [scans, setScans] = useState6(null);
  const [showWatch, setShowWatch] = useState6(false);
  const [open, setOpen] = useState6(null);
  const [kind, setKind] = useState6("quick");
  const [target, setTarget] = useState6("all");
  const [paths, setPaths] = useState6("");
  const [msg, setMsg] = useState6(null);
  const hostFilter = useContext2(HostFilter);
  useEffect7(() => {
    setTarget(hostFilter ?? "all");
  }, [hostFilter]);
  const load = useCallback5(() => {
    call(`${API}/scans?include_watch=${showWatch}${hostFilter ? `&host=${encodeURIComponent(hostFilter)}` : ""}`).then(setScans).catch(() => setScans([]));
  }, [showWatch, hostFilter]);
  useEffect7(() => {
    load();
  }, [load]);
  useEffect7(() => {
    if (!scans?.some((s) => s.status === "running")) return;
    const t = setInterval(load, 4e3);
    return () => clearInterval(t);
  }, [scans, load]);
  async function start() {
    setMsg(null);
    try {
      const body = { kind, host_ids: target === "all" ? "all" : [target], paths: kind === "custom" ? paths.split(/[\n,]/).map((p) => p.trim()).filter(Boolean) : null };
      const r = await call(`${API}/scans`, { method: "POST", body: JSON.stringify(body) });
      setMsg({ kind: "ok", text: r.scans.length ? `${r.scans.length} Scan(s) gestartet.` : "L\xE4uft bereits oder kein Server verf\xFCgbar." });
      load();
      onStarted();
    } catch (err) {
      setMsg({ kind: "error", text: err instanceof Error ? err.message : String(err) });
    }
  }
  return /* @__PURE__ */ jsxs6("div", { className: "space-y-5", children: [
    canManage && /* @__PURE__ */ jsxs6(Card, { title: "Scan starten", description: "Schnellscan pr\xFCft typische Ablageorte (tmp, home, root), der Tiefenscan das ganze System \u2013 das kann Stunden dauern.", children: [
      msg && /* @__PURE__ */ jsx7(Notice, { text: msg.text, kind: msg.kind, onClose: () => setMsg(null) }),
      /* @__PURE__ */ jsxs6("div", { className: "flex flex-wrap items-end gap-3", children: [
        /* @__PURE__ */ jsxs6("label", { className: "text-sm", children: [
          /* @__PURE__ */ jsx7("span", { className: "mb-1.5 block text-white/70", children: "Art" }),
          /* @__PURE__ */ jsxs6("select", { "aria-label": "Scan-Art", value: kind, onChange: (e) => setKind(e.target.value), className: `${inputClass} w-44`, children: [
            /* @__PURE__ */ jsx7("option", { value: "quick", children: "Schnellscan" }),
            /* @__PURE__ */ jsx7("option", { value: "deep", children: "Tiefenscan" }),
            /* @__PURE__ */ jsx7("option", { value: "custom", children: "Bestimmte Ordner" })
          ] })
        ] }),
        /* @__PURE__ */ jsxs6("label", { className: "text-sm", children: [
          /* @__PURE__ */ jsx7("span", { className: "mb-1.5 block text-white/70", children: "Server" }),
          /* @__PURE__ */ jsxs6("select", { "aria-label": "Scan-Ziel", value: target, onChange: (e) => setTarget(e.target.value), className: `${inputClass} w-52`, children: [
            /* @__PURE__ */ jsx7("option", { value: "all", children: "Alle Server" }),
            hosts.map((h) => /* @__PURE__ */ jsx7("option", { value: h.host_id, children: h.host_name }, h.host_id))
          ] })
        ] }),
        kind === "custom" && /* @__PURE__ */ jsxs6("label", { className: "min-w-[16rem] flex-1 text-sm", children: [
          /* @__PURE__ */ jsx7("span", { className: "mb-1.5 block text-white/70", children: "Ordner (Komma-getrennt)" }),
          /* @__PURE__ */ jsx7("input", { "aria-label": "Ordner", value: paths, placeholder: "/var/www, /srv/uploads", onChange: (e) => setPaths(e.target.value), className: `${inputClass} font-mono` })
        ] }),
        /* @__PURE__ */ jsxs6(Button, { variant: "primary", onClick: () => void start(), children: [
          /* @__PURE__ */ jsx7(Icon, { name: "play", size: 13 }),
          " Starten"
        ] })
      ] })
    ] }),
    /* @__PURE__ */ jsx7(
      Card,
      {
        title: "Verlauf",
        padded: false,
        actions: /* @__PURE__ */ jsxs6("label", { className: "flex items-center gap-2 text-xs text-white/55", children: [
          /* @__PURE__ */ jsx7("input", { type: "checkbox", checked: showWatch, onChange: (e) => setShowWatch(e.target.checked) }),
          " saubere W\xE4chter-L\xE4ufe zeigen"
        ] }),
        children: !scans ? /* @__PURE__ */ jsx7("p", { className: "px-5 py-4 text-sm text-white/50", children: "Lade \u2026" }) : scans.length === 0 ? /* @__PURE__ */ jsx7("p", { className: "px-5 py-6 text-sm text-white/50", children: "Noch keine Scans." }) : /* @__PURE__ */ jsx7("ul", { className: "divide-y divide-white/[0.05]", "data-testid": "scan-list", children: scans.map((s) => {
          const st = SCAN_STATUS[s.status] ?? { label: s.status, tone: "neutral" };
          const isOpen = open === s.id;
          const output = s.output_tail ? visibleScanOutput(s.output_tail) : "";
          return /* @__PURE__ */ jsxs6("li", { className: "px-5 py-3 text-sm", children: [
            /* @__PURE__ */ jsxs6("button", { type: "button", onClick: () => setOpen(isOpen ? null : s.id), className: "flex w-full flex-wrap items-center gap-2 text-left", children: [
              /* @__PURE__ */ jsx7(Icon, { name: isOpen ? "chevron-down" : "chevron-right", size: 14, className: "text-white/40" }),
              /* @__PURE__ */ jsx7(Badge, { tone: st.tone, children: st.label }),
              /* @__PURE__ */ jsx7("span", { className: "font-medium", children: s.host_name }),
              /* @__PURE__ */ jsx7("span", { className: "text-white/50", children: s.kind_label }),
              s.infected > 0 && /* @__PURE__ */ jsxs6("span", { className: "text-red-300", children: [
                s.infected,
                " Fund(e)"
              ] }),
              s.files_scanned != null && /* @__PURE__ */ jsxs6("span", { className: "text-xs text-white/40", children: [
                s.files_scanned.toLocaleString("de-DE"),
                " Dateien"
              ] }),
              /* @__PURE__ */ jsxs6("span", { className: "ml-auto text-xs text-white/40", children: [
                when(s.started_at),
                " \xB7 ",
                s.trigger === "schedule" ? "Zeitplan" : "manuell"
              ] })
            ] }),
            s.error && /* @__PURE__ */ jsx7("p", { className: "ml-6 mt-1 text-xs text-amber-300", children: s.error }),
            isOpen && /* @__PURE__ */ jsxs6("div", { className: "ml-6 mt-2 space-y-2", children: [
              /* @__PURE__ */ jsxs6("p", { className: "text-xs text-white/45", children: [
                "Ordner: ",
                /* @__PURE__ */ jsx7("span", { className: "font-mono", children: s.paths.join(", ") }),
                s.finished_at ? ` \xB7 Dauer ${Math.max(1, Math.round((s.finished_at - s.started_at) / 60))} Min.` : ""
              ] }),
              output && /* @__PURE__ */ jsx7("pre", { className: "max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-black/40 p-2.5 font-mono text-[11px] text-white/75", children: output })
            ] })
          ] }, s.id);
        }) })
      }
    )
  ] });
}
function QuarantineTab({ canManage, onChanged }) {
  const unmountSignal = useUnmountSignal();
  const [items, setItems] = useState6(null);
  const [filter, setFilter] = useState6("active");
  const [msg, setMsg] = useState6(null);
  const load = useCallback5(() => {
    call(`${API}/findings`).then(setItems).catch(() => setItems([]));
  }, []);
  useEffect7(() => {
    load();
  }, [load]);
  const hostItems = useForHost(items ?? []);
  const visible = useMemo2(
    () => hostItems.filter((f) => filter === "all" || f.status === "detected" || f.status === "quarantined"),
    [hostItems, filter]
  );
  async function act(f, action) {
    setMsg(null);
    const questions = {
      quarantine: `\u201E${f.path}\u201C in Quarant\xE4ne verschieben?`,
      ignore: `Fund \u201E${f.path}\u201C ignorieren? Die Datei bleibt, wo sie ist.`,
      restore: `\u201E${f.path}\u201C wiederherstellen? Nur tun, wenn sicher ist, dass es ein Fehlalarm war.`,
      delete: `\u201E${f.path}\u201C endg\xFCltig l\xF6schen?`
    };
    const ok = await deck().confirmDialog(questions[action], { danger: action !== "quarantine", confirmLabel: { quarantine: "Verschieben", ignore: "Ignorieren", restore: "Wiederherstellen", delete: "L\xF6schen" }[action] });
    if (!ok) return;
    try {
      let text = "Erledigt.";
      if (action === "quarantine" || action === "ignore") {
        await call(`${API}/findings/${f.id}/${action}`, { method: "POST" });
      } else {
        text = await proposeAndApprove(`${API}/findings/${f.id}/${action}`, {}, "medium", unmountSignal());
      }
      setMsg({ kind: text.startsWith("Fehlgeschlagen") ? "error" : "ok", text });
      load();
      onChanged();
    } catch (err) {
      setMsg({ kind: "error", text: err instanceof Error ? err.message : String(err) });
    }
  }
  if (!items) return /* @__PURE__ */ jsx7("p", { className: "text-sm text-white/50", children: "Lade \u2026" });
  if (items.length === 0) {
    return /* @__PURE__ */ jsx7(EmptyState, { icon: "package", title: "Keine Funde", text: "Bisher wurde keine Schadsoftware gefunden. Funde landen hier \u2013 bei aktivierter automatischer Quarant\xE4ne sind sie sofort unsch\xE4dlich gemacht." });
  }
  return /* @__PURE__ */ jsxs6(Fragment5, { children: [
    msg && /* @__PURE__ */ jsx7(Notice, { text: msg.text, kind: msg.kind, onClose: () => setMsg(null) }),
    /* @__PURE__ */ jsx7(
      Card,
      {
        title: "Funde & Quarant\xE4ne",
        description: "Dateien in Quarant\xE4ne sind unlesbar und nicht ausf\xFChrbar (Rechte 000) im Tresor auf dem Server, auf dem sie gefunden wurden.",
        padded: false,
        actions: /* @__PURE__ */ jsxs6("select", { "aria-label": "Filter", value: filter, onChange: (e) => setFilter(e.target.value), className: `${inputClass} w-auto py-1 text-xs`, children: [
          /* @__PURE__ */ jsx7("option", { value: "active", children: "Offen & in Quarant\xE4ne" }),
          /* @__PURE__ */ jsx7("option", { value: "all", children: "Alle, inkl. erledigt" })
        ] }),
        children: /* @__PURE__ */ jsxs6("ul", { className: "divide-y divide-white/[0.05]", "data-testid": "findings", children: [
          visible.length === 0 && /* @__PURE__ */ jsx7("li", { className: "px-5 py-6 text-sm text-white/50", children: "Nichts offen." }),
          visible.map((f) => {
            const st = FINDING_STATUS[f.status] ?? { label: f.status, tone: "neutral" };
            return /* @__PURE__ */ jsxs6("li", { className: "flex flex-wrap items-center gap-3 px-5 py-3 text-sm", children: [
              /* @__PURE__ */ jsxs6("div", { className: "min-w-0 flex-1", children: [
                /* @__PURE__ */ jsxs6("p", { className: "flex flex-wrap items-center gap-2", children: [
                  /* @__PURE__ */ jsx7(Badge, { tone: st.tone, children: st.label }),
                  /* @__PURE__ */ jsx7("span", { className: "font-medium text-red-200", children: f.signature })
                ] }),
                /* @__PURE__ */ jsxs6("p", { className: "mt-0.5 truncate font-mono text-xs text-white/70", children: [
                  f.host_name,
                  ": ",
                  f.path
                ] }),
                /* @__PURE__ */ jsxs6("p", { className: "text-xs text-white/40", children: [
                  "gefunden ",
                  when(f.detected_at),
                  f.status_changed_at ? ` \xB7 ge\xE4ndert ${when(f.status_changed_at)}` : ""
                ] }),
                f.note && /* @__PURE__ */ jsx7("p", { className: "text-xs text-amber-300", children: f.note })
              ] }),
              canManage && /* @__PURE__ */ jsxs6("div", { className: "flex gap-1", children: [
                f.status === "detected" && /* @__PURE__ */ jsx7(Button, { small: true, variant: "primary", onClick: () => void act(f, "quarantine"), children: "In Quarant\xE4ne" }),
                f.status === "detected" && /* @__PURE__ */ jsx7(Button, { small: true, variant: "ghost", onClick: () => void act(f, "ignore"), children: "Ignorieren" }),
                f.status === "quarantined" && /* @__PURE__ */ jsx7(Button, { small: true, onClick: () => void act(f, "restore"), children: "Wiederherstellen" }),
                f.status === "quarantined" && /* @__PURE__ */ jsx7(Button, { small: true, variant: "danger", onClick: () => void act(f, "delete"), children: "Endg\xFCltig l\xF6schen" })
              ] })
            ] }, f.id);
          })
        ] })
      }
    )
  ] });
}
function HardeningTab({ canManage, onAudit }) {
  const [audits, setAudits] = useState6(null);
  const [open, setOpen] = useState6(null);
  useEffect7(() => {
    call(`${API}/audits`).then((a) => setAudits(a.sort((x, y) => (x.hardening_index ?? 0) - (y.hardening_index ?? 0)))).catch(() => setAudits([]));
  }, []);
  const shown = useForHost(audits ?? []);
  if (!audits) return /* @__PURE__ */ jsx7("p", { className: "text-sm text-white/50", children: "Lade \u2026" });
  if (shown.length === 0) {
    return /* @__PURE__ */ jsx7(
      EmptyState,
      {
        icon: "clock",
        title: "Noch kein H\xE4rtungs-Audit",
        text: "Lynis pr\xFCft jeden Server auf unsichere Einstellungen (SSH, Passwortregeln, offene Dienste \u2026) und vergibt einen H\xE4rtungsindex von 0 bis 100.",
        action: canManage && /* @__PURE__ */ jsx7(Button, { variant: "primary", onClick: onAudit, children: "Audit jetzt starten" })
      }
    );
  }
  return /* @__PURE__ */ jsx7("div", { className: "grid gap-4 lg:grid-cols-2", children: shown.map((a) => /* @__PURE__ */ jsx7(
    Card,
    {
      title: a.host_name,
      description: `Gepr\xFCft ${when(a.created_at)}`,
      actions: a.hardening_index != null && /* @__PURE__ */ jsx7("span", { className: `text-2xl font-semibold ${a.hardening_index >= 70 ? "text-emerald-300" : "text-amber-300"}`, children: a.hardening_index }),
      children: a.status !== "ok" ? /* @__PURE__ */ jsx7("p", { className: "text-sm text-amber-300", children: a.error }) : /* @__PURE__ */ jsxs6(Fragment5, { children: [
        /* @__PURE__ */ jsxs6("p", { className: "mb-2 text-xs uppercase tracking-wider text-white/40", children: [
          "Warnungen (",
          a.warnings.length,
          ")"
        ] }),
        a.warnings.length === 0 ? /* @__PURE__ */ jsx7("p", { className: "mb-3 text-sm text-emerald-300", children: "Keine Warnungen." }) : /* @__PURE__ */ jsx7("ul", { className: "mb-3 space-y-1 text-sm", children: a.warnings.map((w) => /* @__PURE__ */ jsxs6("li", { className: "text-red-200", children: [
          "\u2022 ",
          w
        ] }, w)) }),
        /* @__PURE__ */ jsxs6("button", { type: "button", onClick: () => setOpen(open === a.id ? null : a.id), className: "flex items-center gap-1 text-xs text-white/55 hover:text-white", children: [
          /* @__PURE__ */ jsx7(Icon, { name: open === a.id ? "chevron-down" : "chevron-right", size: 12 }),
          " ",
          a.suggestions.length,
          " Verbesserungsvorschl\xE4ge"
        ] }),
        open === a.id && /* @__PURE__ */ jsx7("ul", { className: "mt-2 max-h-72 space-y-1 overflow-auto text-xs text-white/70", children: a.suggestions.map((s) => /* @__PURE__ */ jsxs6("li", { children: [
          "\u2022 ",
          s
        ] }, s)) })
      ] })
    },
    a.id
  )) });
}
export {
  SocPage
};
