// src/SystemPage.tsx
import { useCallback as useCallback2, useEffect as useEffect5, useRef as useRef3, useState as useState4 } from "react";

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

// ../../_shared/frontend/src/links.tsx
import { useEffect as useEffect2, useState } from "react";

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

// ../../_shared/frontend/src/links.tsx
import { jsx as jsx2 } from "react/jsx-runtime";
function SettingsLink({
  to,
  permission,
  variant = "primary",
  children
}) {
  if (!deck().hasPermission(permission)) return null;
  return /* @__PURE__ */ jsx2("a", { href: to, className: buttonClass(variant), children });
}

// ../../_shared/frontend/src/location.ts
import { useCallback, useEffect as useEffect3, useMemo, useState as useState2 } from "react";
function useUrlParams() {
  const [search, setSearch] = useState2(() => window.location.search);
  const [visits, setVisits] = useState2(0);
  useEffect3(() => {
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

// ../../_shared/frontend/src/format.ts
function formatBytes(n, options = {}) {
  if (n == null) return "?";
  const units = ["B", "KB", "MB", "GB", "TB"];
  if (options.fixed) {
    if (n <= 0) return "0 B";
    const i2 = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
    return `${(n / 1024 ** i2).toFixed(1)} ${units[i2]}`;
  }
  let value = n;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(value < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}

// src/format.ts
function formatUptime(seconds) {
  if (seconds == null) return "?";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor(seconds % 86400 / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  return days > 0 ? `${days} d ${hours} h` : hours > 0 ? `${hours} h ${minutes} min` : `${minutes} min`;
}

// src/LiveView.tsx
import { useEffect as useEffect4, useRef as useRef2, useState as useState3 } from "react";
import { Fragment as Fragment2, jsx as jsx3, jsxs as jsxs2 } from "react/jsx-runtime";
var POLL_MS = 3e3;
var HISTORY = 60;
var BLUE = "#3987e5";
var ORANGE = "#d95926";
var rate = (v) => v < 1024 ? `${Math.round(v)} B/s` : `${formatBytes(v)}/s`;
var pct = (v) => v == null ? "\u2013" : `${v.toFixed(v < 10 ? 1 : 0)} %`;
function tempTone(c) {
  return c >= 80 ? "text-red-300" : c >= 70 ? "text-amber-300" : "text-white";
}
function barColor(percent) {
  return percent >= 90 ? "#e66767" : percent >= 75 ? "#c98500" : BLUE;
}
function Spark({ values, max, color = BLUE }) {
  const w = 160;
  const h = 36;
  const top = max ?? Math.max(1, ...values) * 1.15;
  const offset = HISTORY - values.length;
  const shifted = values.map((v, i) => `${(i + offset) / Math.max(1, HISTORY - 1) * w},${h - Math.min(v, top) / top * h}`);
  return /* @__PURE__ */ jsxs2("svg", { viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: "none", className: "mt-1 h-9 w-full", "aria-hidden": "true", children: [
    /* @__PURE__ */ jsx3("line", { x1: 0, x2: w, y1: h - 0.5, y2: h - 0.5, stroke: "rgba(255,255,255,0.1)" }),
    shifted.length > 1 && /* @__PURE__ */ jsxs2(Fragment2, { children: [
      /* @__PURE__ */ jsx3("polygon", { points: `${shifted[0].split(",")[0]},${h} ${shifted.join(" ")} ${w},${h}`, fill: color, opacity: 0.15 }),
      /* @__PURE__ */ jsx3("polyline", { points: shifted.join(" "), fill: "none", stroke: color, strokeWidth: 1.5, vectorEffect: "non-scaling-stroke" })
    ] })
  ] });
}
function Tile({ label, value, sub, spark }) {
  return /* @__PURE__ */ jsxs2("div", { className: "panel p-3", children: [
    /* @__PURE__ */ jsx3("p", { className: "text-[11px] uppercase tracking-wider opacity-50", children: label }),
    /* @__PURE__ */ jsx3("p", { className: "text-xl font-semibold tabular-nums", children: value }),
    sub && /* @__PURE__ */ jsx3("p", { className: "truncate text-[11px] opacity-60", children: sub }),
    spark
  ] });
}
function Meter({ percent }) {
  return /* @__PURE__ */ jsx3("div", { className: "h-1.5 w-full overflow-hidden rounded bg-white/10", children: /* @__PURE__ */ jsx3("div", { className: "h-full rounded", style: { width: `${Math.min(100, Math.max(0, percent))}%`, background: barColor(percent) } }) });
}
function Section({ title, children, extra }) {
  return /* @__PURE__ */ jsxs2("section", { className: "panel p-4", children: [
    /* @__PURE__ */ jsxs2("div", { className: "mb-2 flex items-center gap-2", children: [
      /* @__PURE__ */ jsx3("h3", { className: "text-xs font-semibold uppercase tracking-wider opacity-70", children: title }),
      extra
    ] }),
    children
  ] });
}
var EMPTY_HISTORY = { cpu: [], mem: [], disk: [], netIn: [], netOut: [], temp: [] };
function push(list, value) {
  if (value === void 0) return list;
  const next = [...list, value];
  return next.length > HISTORY ? next.slice(next.length - HISTORY) : next;
}
function LiveView({ hostId, fetchLive }) {
  const [data, setData] = useState3(null);
  const [error, setError] = useState3(null);
  const [paused, setPaused] = useState3(false);
  const [updatedAt, setUpdatedAt] = useState3(null);
  const [history, setHistory] = useState3(EMPTY_HISTORY);
  const [procSort, setProcSort] = useState3("cpu");
  const [showVirtual, setShowVirtual] = useState3(false);
  const busy = useRef2(false);
  useEffect4(() => {
    setData(null);
    setHistory(EMPTY_HISTORY);
  }, [hostId]);
  useEffect4(() => {
    if (paused) return;
    let cancelled = false;
    async function tick() {
      if (busy.current || typeof document !== "undefined" && document.hidden) return;
      busy.current = true;
      try {
        const live = await fetchLive(hostId);
        if (cancelled) return;
        setData(live);
        setError(null);
        setUpdatedAt(/* @__PURE__ */ new Date());
        const physical2 = live.network.filter((n) => !n.virtual);
        setHistory((h) => ({
          cpu: push(h.cpu, live.cpu.total?.percent),
          mem: push(h.mem, live.memory ? 100 * live.memory.used / live.memory.total : void 0),
          disk: push(h.disk, live.disks.length ? Math.max(...live.disks.map((d) => d.busy_percent)) : void 0),
          netIn: push(h.netIn, physical2.reduce((a, n) => a + n.rx_bps, 0)),
          netOut: push(h.netOut, physical2.reduce((a, n) => a + n.tx_bps, 0)),
          temp: push(h.temp, live.temperatures.length ? Math.max(...live.temperatures.map((t) => t.celsius)) : void 0)
        }));
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      } finally {
        busy.current = false;
      }
    }
    void tick();
    const timer = setInterval(() => void tick(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [hostId, paused, fetchLive]);
  const controls = /* @__PURE__ */ jsxs2("div", { className: "mb-3 flex flex-wrap items-center gap-3 text-xs", children: [
    /* @__PURE__ */ jsx3("button", { type: "button", onClick: () => setPaused((p) => !p), className: "rounded bg-white/10 px-2 py-1 hover:bg-white/20", children: paused ? "Fortsetzen" : "Pausieren" }),
    /* @__PURE__ */ jsxs2("span", { className: "opacity-50", children: [
      paused ? "angehalten" : "aktualisiert alle 3 s",
      updatedAt ? ` \xB7 Stand ${updatedAt.toLocaleTimeString()}` : ""
    ] }),
    error && /* @__PURE__ */ jsxs2("span", { className: "text-red-400", children: [
      "Fehler: ",
      error
    ] })
  ] });
  if (!data) {
    return /* @__PURE__ */ jsxs2("div", { "data-testid": "live-view", children: [
      controls,
      !error && /* @__PURE__ */ jsx3("p", { className: "text-sm opacity-60", children: "Messe \u2026 (die Raten brauchen eine Sekunde Messzeit)" })
    ] });
  }
  const { cpu, memory } = data;
  const physical = data.network.filter((n) => !n.virtual);
  const nics = showVirtual ? data.network : physical;
  const netIn = physical.reduce((a, n) => a + n.rx_bps, 0);
  const netOut = physical.reduce((a, n) => a + n.tx_bps, 0);
  const diskBusy = data.disks.length ? Math.max(...data.disks.map((d) => d.busy_percent)) : null;
  const hottest = data.temperatures.length ? Math.max(...data.temperatures.map((t) => t.celsius)) : null;
  const procs = [...data.processes].sort((a, b) => procSort === "cpu" ? b.cpu_percent - a.cpu_percent || b.mem_bytes - a.mem_bytes : b.mem_bytes - a.mem_bytes);
  const freqs = cpu.per_core.map((c) => c.freq_mhz).filter((f) => f != null);
  return /* @__PURE__ */ jsxs2("div", { className: "space-y-4", "data-testid": "live-view", children: [
    controls,
    data.throttled && data.throttled.flags.length > 0 && /* @__PURE__ */ jsxs2("p", { className: "panel px-3 py-2 text-sm text-amber-300", "data-testid": "throttled", children: [
      "Raspberry Pi meldet: ",
      data.throttled.flags.join(", "),
      " (",
      data.throttled.raw,
      ") \u2013 meist ein zu schwaches Netzteil oder Hitze."
    ] }),
    /* @__PURE__ */ jsxs2("div", { className: "grid grid-cols-2 gap-3 lg:grid-cols-5", children: [
      /* @__PURE__ */ jsx3(Tile, { label: "CPU", value: pct(cpu.total?.percent), sub: freqs.length ? `${Math.max(...freqs)} MHz \xB7 ${cpu.cores} Kerne` : `${cpu.cores} Kerne`, spark: /* @__PURE__ */ jsx3(Spark, { values: history.cpu, max: 100 }) }),
      /* @__PURE__ */ jsx3(Tile, { label: "Arbeitsspeicher", value: memory ? pct(100 * memory.used / memory.total) : "\u2013", sub: memory ? `${formatBytes(memory.used)} von ${formatBytes(memory.total)}` : void 0, spark: /* @__PURE__ */ jsx3(Spark, { values: history.mem, max: 100 }) }),
      /* @__PURE__ */ jsx3(Tile, { label: "Datentr\xE4ger aktiv", value: pct(diskBusy), sub: data.disks.map((d) => d.name).join(", ") || "keine", spark: /* @__PURE__ */ jsx3(Spark, { values: history.disk, max: 100 }) }),
      /* @__PURE__ */ jsx3(Tile, { label: "Netzwerk", value: rate(netIn + netOut), sub: `\u2193 ${rate(netIn)} \xB7 \u2191 ${rate(netOut)}`, spark: /* @__PURE__ */ jsx3(Spark, { values: history.netIn, color: BLUE }) }),
      /* @__PURE__ */ jsx3(Tile, { label: "Temperatur", value: hottest != null ? `${hottest.toFixed(1)} \xB0C` : "\u2013", sub: hottest != null ? "h\xF6chster Sensor" : "keine Sensoren", spark: /* @__PURE__ */ jsx3(Spark, { values: history.temp, color: ORANGE }) })
    ] }),
    /* @__PURE__ */ jsxs2("div", { className: "grid gap-4 xl:grid-cols-2", children: [
      /* @__PURE__ */ jsxs2(Section, { title: "Prozessor", extra: /* @__PURE__ */ jsx3("span", { className: "text-[11px] opacity-50", children: cpu.model ?? "" }), children: [
        cpu.total && /* @__PURE__ */ jsxs2("p", { className: "mb-3 text-xs opacity-80", "data-testid": "cpu-breakdown", children: [
          "Benutzer ",
          pct(cpu.total.user),
          " \xB7 System ",
          pct(cpu.total.system),
          " \xB7 Warten auf I/O ",
          pct(cpu.total.iowait),
          cpu.total.steal > 0 ? ` \xB7 Steal ${pct(cpu.total.steal)}` : "",
          " \xB7 Last ",
          cpu.load.map((l) => l.toFixed(2)).join(" / "),
          " \xB7 l\xE4uft seit ",
          formatUptime(data.uptime_s)
        ] }),
        /* @__PURE__ */ jsx3("div", { className: "grid grid-cols-2 gap-2 sm:grid-cols-4", "data-testid": "cores", children: cpu.per_core.map((core) => /* @__PURE__ */ jsxs2("div", { className: "rounded-lg bg-white/[0.04] p-2", children: [
          /* @__PURE__ */ jsxs2("div", { className: "flex items-baseline justify-between text-[11px]", children: [
            /* @__PURE__ */ jsxs2("span", { className: "opacity-60", children: [
              "Kern ",
              core.id
            ] }),
            /* @__PURE__ */ jsx3("span", { className: "font-semibold tabular-nums", children: pct(core.percent) })
          ] }),
          /* @__PURE__ */ jsx3(Meter, { percent: core.percent ?? 0 }),
          core.freq_mhz != null && /* @__PURE__ */ jsxs2("p", { className: "mt-1 text-[10px] tabular-nums opacity-50", children: [
            core.freq_mhz,
            " MHz"
          ] })
        ] }, core.id)) })
      ] }),
      /* @__PURE__ */ jsx3(Section, { title: "Arbeitsspeicher", children: memory ? /* @__PURE__ */ jsxs2(Fragment2, { children: [
        /* @__PURE__ */ jsxs2("div", { className: "mb-2 flex h-3 w-full overflow-hidden rounded bg-white/10", "data-testid": "mem-bar", "aria-label": "Aufteilung des Arbeitsspeichers", children: [
          /* @__PURE__ */ jsx3("div", { style: { width: `${100 * memory.used / memory.total}%`, background: BLUE }, title: "belegt" }),
          /* @__PURE__ */ jsx3("div", { style: { width: `${100 * (memory.cached ?? 0) / memory.total}%`, background: "#199e70" }, title: "Cache" }),
          /* @__PURE__ */ jsx3("div", { style: { width: `${100 * (memory.buffers ?? 0) / memory.total}%`, background: "#c98500" }, title: "Puffer" })
        ] }),
        /* @__PURE__ */ jsxs2("dl", { className: "grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3", children: [
          /* @__PURE__ */ jsxs2("div", { children: [
            /* @__PURE__ */ jsxs2("dt", { className: "opacity-50", children: [
              /* @__PURE__ */ jsx3("span", { className: "mr-1 inline-block h-2 w-2 rounded-sm", style: { background: BLUE } }),
              "belegt"
            ] }),
            /* @__PURE__ */ jsx3("dd", { className: "tabular-nums", children: formatBytes(memory.used) })
          ] }),
          /* @__PURE__ */ jsxs2("div", { children: [
            /* @__PURE__ */ jsxs2("dt", { className: "opacity-50", children: [
              /* @__PURE__ */ jsx3("span", { className: "mr-1 inline-block h-2 w-2 rounded-sm", style: { background: "#199e70" } }),
              "Cache"
            ] }),
            /* @__PURE__ */ jsx3("dd", { className: "tabular-nums", children: formatBytes(memory.cached) })
          ] }),
          /* @__PURE__ */ jsxs2("div", { children: [
            /* @__PURE__ */ jsxs2("dt", { className: "opacity-50", children: [
              /* @__PURE__ */ jsx3("span", { className: "mr-1 inline-block h-2 w-2 rounded-sm", style: { background: "#c98500" } }),
              "Puffer"
            ] }),
            /* @__PURE__ */ jsx3("dd", { className: "tabular-nums", children: formatBytes(memory.buffers) })
          ] }),
          /* @__PURE__ */ jsxs2("div", { children: [
            /* @__PURE__ */ jsx3("dt", { className: "opacity-50", children: "verf\xFCgbar" }),
            /* @__PURE__ */ jsx3("dd", { className: "tabular-nums", children: formatBytes(memory.available) })
          ] }),
          /* @__PURE__ */ jsxs2("div", { children: [
            /* @__PURE__ */ jsx3("dt", { className: "opacity-50", children: "gemeinsam" }),
            /* @__PURE__ */ jsx3("dd", { className: "tabular-nums", children: formatBytes(memory.shared) })
          ] }),
          /* @__PURE__ */ jsxs2("div", { children: [
            /* @__PURE__ */ jsx3("dt", { className: "opacity-50", children: "gesamt" }),
            /* @__PURE__ */ jsx3("dd", { className: "tabular-nums", children: formatBytes(memory.total) })
          ] })
        ] }),
        memory.swap_total ? /* @__PURE__ */ jsxs2("div", { className: "mt-3 text-xs", children: [
          /* @__PURE__ */ jsxs2("div", { className: "mb-1 flex justify-between", children: [
            /* @__PURE__ */ jsx3("span", { className: "opacity-60", children: "Swap" }),
            /* @__PURE__ */ jsxs2("span", { className: "tabular-nums", children: [
              formatBytes(memory.swap_used),
              " von ",
              formatBytes(memory.swap_total)
            ] })
          ] }),
          /* @__PURE__ */ jsx3(Meter, { percent: 100 * (memory.swap_used ?? 0) / memory.swap_total })
        ] }) : /* @__PURE__ */ jsx3("p", { className: "mt-3 text-xs opacity-50", children: "Kein Swap eingerichtet." })
      ] }) : /* @__PURE__ */ jsx3("p", { className: "text-sm opacity-50", children: "Keine Angaben." }) }),
      /* @__PURE__ */ jsx3(Section, { title: "Sensoren", children: data.temperatures.length === 0 && data.fans.length === 0 ? /* @__PURE__ */ jsx3("p", { className: "text-sm opacity-50", children: "Dieser Server meldet keine Sensoren (bei VMs normal)." }) : /* @__PURE__ */ jsx3("table", { className: "w-full text-sm", "data-testid": "sensors", children: /* @__PURE__ */ jsxs2("tbody", { className: "divide-y divide-white/5", children: [
        data.temperatures.map((t) => /* @__PURE__ */ jsxs2("tr", { children: [
          /* @__PURE__ */ jsx3("td", { className: "py-1 opacity-70", children: t.label }),
          /* @__PURE__ */ jsx3("td", { className: "py-1 text-xs opacity-40", children: t.source }),
          /* @__PURE__ */ jsxs2("td", { className: `py-1 text-right font-semibold tabular-nums ${tempTone(t.celsius)}`, children: [
            t.celsius.toFixed(1),
            " \xB0C"
          ] })
        ] }, `${t.source}-${t.label}`)),
        data.fans.map((f) => /* @__PURE__ */ jsxs2("tr", { children: [
          /* @__PURE__ */ jsx3("td", { className: "py-1 opacity-70", children: f.label }),
          /* @__PURE__ */ jsx3("td", { className: "py-1 text-xs opacity-40", children: "L\xFCfter" }),
          /* @__PURE__ */ jsxs2("td", { className: "py-1 text-right font-semibold tabular-nums", children: [
            f.rpm,
            " U/min"
          ] })
        ] }, f.label))
      ] }) }) }),
      /* @__PURE__ */ jsx3(Section, { title: "Datentr\xE4ger", children: data.disks.length === 0 ? /* @__PURE__ */ jsx3("p", { className: "text-sm opacity-50", children: "Keine physischen Datentr\xE4ger erkannt." }) : /* @__PURE__ */ jsxs2("table", { className: "w-full text-sm", "data-testid": "live-disks", children: [
        /* @__PURE__ */ jsx3("thead", { children: /* @__PURE__ */ jsxs2("tr", { className: "text-left text-[11px] uppercase opacity-50", children: [
          /* @__PURE__ */ jsx3("th", { className: "py-1 font-normal", children: "Ger\xE4t" }),
          /* @__PURE__ */ jsx3("th", { className: "py-1 text-right font-normal", children: "Lesen" }),
          /* @__PURE__ */ jsx3("th", { className: "py-1 text-right font-normal", children: "Schreiben" }),
          /* @__PURE__ */ jsx3("th", { className: "py-1 text-right font-normal", children: "IOPS l/s" }),
          /* @__PURE__ */ jsx3("th", { className: "w-28 py-1 pl-3 font-normal", children: "Aktiv" })
        ] }) }),
        /* @__PURE__ */ jsx3("tbody", { className: "divide-y divide-white/5", children: data.disks.map((d) => /* @__PURE__ */ jsxs2("tr", { children: [
          /* @__PURE__ */ jsx3("td", { className: "py-1 font-mono text-xs", children: d.name }),
          /* @__PURE__ */ jsx3("td", { className: "py-1 text-right tabular-nums", children: rate(d.read_bps) }),
          /* @__PURE__ */ jsx3("td", { className: "py-1 text-right tabular-nums", children: rate(d.write_bps) }),
          /* @__PURE__ */ jsxs2("td", { className: "py-1 text-right tabular-nums", children: [
            d.read_iops.toFixed(0),
            " / ",
            d.write_iops.toFixed(0)
          ] }),
          /* @__PURE__ */ jsx3("td", { className: "py-1 pl-3", children: /* @__PURE__ */ jsxs2("div", { className: "flex items-center gap-2", children: [
            /* @__PURE__ */ jsx3(Meter, { percent: d.busy_percent }),
            /* @__PURE__ */ jsx3("span", { className: "w-14 whitespace-nowrap text-right text-xs tabular-nums", children: pct(d.busy_percent) })
          ] }) })
        ] }, d.name)) })
      ] }) })
    ] }),
    /* @__PURE__ */ jsx3(
      Section,
      {
        title: "Netzwerk",
        extra: data.network.some((n) => n.virtual) ? /* @__PURE__ */ jsxs2("label", { className: "ml-auto flex items-center gap-1 text-[11px] opacity-70", children: [
          /* @__PURE__ */ jsx3("input", { type: "checkbox", checked: showVirtual, onChange: (e) => setShowVirtual(e.target.checked) }),
          " virtuelle Schnittstellen zeigen"
        ] }) : void 0,
        children: /* @__PURE__ */ jsxs2("table", { className: "w-full text-sm", "data-testid": "live-network", children: [
          /* @__PURE__ */ jsx3("thead", { children: /* @__PURE__ */ jsxs2("tr", { className: "text-left text-[11px] uppercase opacity-50", children: [
            /* @__PURE__ */ jsx3("th", { className: "py-1 font-normal", children: "Schnittstelle" }),
            /* @__PURE__ */ jsx3("th", { className: "py-1 font-normal", children: "Status" }),
            /* @__PURE__ */ jsx3("th", { className: "py-1 text-right font-normal", children: "Empfangen" }),
            /* @__PURE__ */ jsx3("th", { className: "py-1 text-right font-normal", children: "Gesendet" }),
            /* @__PURE__ */ jsx3("th", { className: "py-1 text-right font-normal", children: "Gesamt \u2193 / \u2191" }),
            /* @__PURE__ */ jsx3("th", { className: "py-1 text-right font-normal", title: "\xDCbertragungsfehler / vom System verworfene Pakete", children: "Fehler / verworfen" })
          ] }) }),
          /* @__PURE__ */ jsx3("tbody", { className: "divide-y divide-white/5", children: nics.map((n) => /* @__PURE__ */ jsxs2("tr", { className: n.virtual ? "opacity-60" : "", children: [
            /* @__PURE__ */ jsx3("td", { className: "py-1 font-mono text-xs", children: n.name }),
            /* @__PURE__ */ jsxs2("td", { className: "py-1 text-xs", children: [
              n.state === "up" ? "verbunden" : n.state ?? "?",
              n.speed_mbps ? ` \xB7 ${n.speed_mbps >= 1e3 ? `${n.speed_mbps / 1e3} Gbit/s` : `${n.speed_mbps} Mbit/s`}` : ""
            ] }),
            /* @__PURE__ */ jsx3("td", { className: "py-1 text-right tabular-nums", children: rate(n.rx_bps) }),
            /* @__PURE__ */ jsx3("td", { className: "py-1 text-right tabular-nums", children: rate(n.tx_bps) }),
            /* @__PURE__ */ jsxs2("td", { className: "py-1 text-right text-xs tabular-nums opacity-70", children: [
              formatBytes(n.rx_total),
              " / ",
              formatBytes(n.tx_total)
            ] }),
            /* @__PURE__ */ jsxs2("td", { className: "py-1 text-right text-xs tabular-nums", children: [
              /* @__PURE__ */ jsx3("span", { className: n.errors > 0 ? "text-amber-300" : "opacity-50", children: n.errors }),
              /* @__PURE__ */ jsxs2("span", { className: "opacity-40", children: [
                " / ",
                n.drops
              ] })
            ] })
          ] }, n.name)) })
        ] })
      }
    ),
    /* @__PURE__ */ jsx3(Section, { title: "Dateisysteme", children: /* @__PURE__ */ jsx3("table", { className: "w-full text-sm", "data-testid": "live-filesystems", children: /* @__PURE__ */ jsx3("tbody", { className: "divide-y divide-white/5", children: data.filesystems.map((f) => /* @__PURE__ */ jsxs2("tr", { children: [
      /* @__PURE__ */ jsx3("td", { className: "py-1 font-mono text-xs", children: f.mount }),
      /* @__PURE__ */ jsxs2("td", { className: "py-1 text-xs opacity-50", children: [
        f.device,
        " \xB7 ",
        f.fstype
      ] }),
      /* @__PURE__ */ jsx3("td", { className: "w-48 py-1", children: /* @__PURE__ */ jsx3(Meter, { percent: f.percent }) }),
      /* @__PURE__ */ jsxs2("td", { className: "py-1 pl-3 text-right text-xs tabular-nums", children: [
        formatBytes(f.used),
        " / ",
        formatBytes(f.size),
        " (",
        f.percent.toFixed(0),
        " %)"
      ] })
    ] }, `${f.device}-${f.mount}`)) }) }) }),
    /* @__PURE__ */ jsxs2(
      Section,
      {
        title: `Prozesse (${data.process_count})`,
        extra: /* @__PURE__ */ jsxs2("div", { className: "ml-auto flex gap-1 text-[11px]", role: "group", "aria-label": "Sortierung", children: [
          /* @__PURE__ */ jsx3("button", { type: "button", "aria-pressed": procSort === "cpu", onClick: () => setProcSort("cpu"), className: `rounded px-2 py-0.5 ${procSort === "cpu" ? "bg-white/15" : "opacity-60 hover:opacity-100"}`, children: "nach CPU" }),
          /* @__PURE__ */ jsx3("button", { type: "button", "aria-pressed": procSort === "mem", onClick: () => setProcSort("mem"), className: `rounded px-2 py-0.5 ${procSort === "mem" ? "bg-white/15" : "opacity-60 hover:opacity-100"}`, children: "nach RAM" })
        ] }),
        children: [
          /* @__PURE__ */ jsxs2("table", { className: "w-full text-sm", "data-testid": "processes", children: [
            /* @__PURE__ */ jsx3("thead", { children: /* @__PURE__ */ jsxs2("tr", { className: "text-left text-[11px] uppercase opacity-50", children: [
              /* @__PURE__ */ jsx3("th", { className: "py-1 font-normal", children: "PID" }),
              /* @__PURE__ */ jsx3("th", { className: "py-1 font-normal", children: "Name" }),
              /* @__PURE__ */ jsx3("th", { className: "py-1 font-normal", children: "Benutzer" }),
              /* @__PURE__ */ jsx3("th", { className: "py-1 text-right font-normal", children: "CPU" }),
              /* @__PURE__ */ jsx3("th", { className: "py-1 text-right font-normal", children: "RAM" })
            ] }) }),
            /* @__PURE__ */ jsx3("tbody", { className: "divide-y divide-white/5", children: procs.slice(0, 15).map((p) => /* @__PURE__ */ jsxs2("tr", { children: [
              /* @__PURE__ */ jsx3("td", { className: "py-1 font-mono text-xs opacity-60", children: p.pid }),
              /* @__PURE__ */ jsx3("td", { className: "py-1", children: p.name }),
              /* @__PURE__ */ jsx3("td", { className: "py-1 text-xs opacity-60", children: p.user ?? "?" }),
              /* @__PURE__ */ jsxs2("td", { className: "py-1 text-right tabular-nums", children: [
                p.cpu_percent.toFixed(1),
                " %"
              ] }),
              /* @__PURE__ */ jsx3("td", { className: "py-1 text-right tabular-nums", children: formatBytes(p.mem_bytes) })
            ] }, p.pid)) })
          ] }),
          /* @__PURE__ */ jsxs2("p", { className: "mt-2 text-[11px] opacity-40", children: [
            "CPU wie im Task-Manager: Anteil an allen ",
            cpu.cores,
            " Kernen, gemessen \xFCber ",
            data.interval_s.toFixed(1),
            " s."
          ] })
        ]
      }
    )
  ] });
}

// src/SystemPage.tsx
import { jsx as jsx4, jsxs as jsxs3 } from "react/jsx-runtime";
var TONE_TEXT = { good: "text-emerald-300", warn: "text-amber-300", danger: "text-red-300" };
var TONE_BAR = { good: "bg-emerald-400/70", warn: "bg-amber-400/80", danger: "bg-red-400/80" };
function Bar({ percent, tone }) {
  return /* @__PURE__ */ jsx4("div", { className: "h-1.5 w-full overflow-hidden rounded bg-white/10", children: /* @__PURE__ */ jsx4("div", { className: `h-full ${TONE_BAR[tone] ?? TONE_BAR.good}`, style: { width: `${Math.min(100, Math.max(0, percent))}%` } }) });
}
function Stat({ label, value, sub, tone }) {
  return /* @__PURE__ */ jsxs3("div", { className: "panel p-3", children: [
    /* @__PURE__ */ jsx4("p", { className: "text-[11px] uppercase tracking-wider opacity-50", children: label }),
    /* @__PURE__ */ jsx4("p", { className: `text-lg font-semibold tabular-nums ${tone && tone !== "good" ? TONE_TEXT[tone] ?? "" : ""}`, children: value }),
    sub && /* @__PURE__ */ jsx4("p", { className: "text-xs opacity-60", children: sub })
  ] });
}
function UnitRow({ unit, tone, canRestart, busy, onRestart }) {
  return /* @__PURE__ */ jsxs3("li", { className: "flex items-center justify-between gap-2 py-0.5", children: [
    /* @__PURE__ */ jsx4("span", { className: `break-words ${tone ?? ""}`, children: unit }),
    canRestart && /* @__PURE__ */ jsx4(Button, { small: true, ariaLabel: `${unit} neu starten`, disabled: busy !== null, onClick: () => onRestart(unit), children: busy === unit ? "Startet neu \u2026" : "Neu starten" })
  ] });
}
function InfoView({ info, canRestart, busyUnit, onRestart }) {
  const memUsed = info.mem_total != null && info.mem_available != null ? info.mem_total - info.mem_available : null;
  const memPct = memUsed != null && info.mem_total ? 100 * memUsed / info.mem_total : null;
  const swapPct = info.swap_total ? 100 * (info.swap_used ?? 0) / info.swap_total : null;
  const security = (info.updates ?? []).filter((u) => u.security).length;
  const restartable = new Set(info.restartable_units ?? []);
  return /* @__PURE__ */ jsxs3("div", { "data-testid": "system-info", children: [
    /* @__PURE__ */ jsxs3("p", { className: "mb-4 text-sm opacity-70", children: [
      info.os ?? "Linux",
      " \xB7 Kernel ",
      info.kernel ?? "?",
      " \xB7 ",
      info.arch ?? "?",
      " \xB7 Hostname ",
      info.hostname ?? "?"
    ] }),
    info.findings.length > 0 ? /* @__PURE__ */ jsx4("ul", { className: "panel mb-4 divide-y divide-white/5 text-sm", "data-testid": "findings", children: info.findings.map((f) => /* @__PURE__ */ jsx4("li", { className: `px-3 py-2 ${TONE_TEXT[f.tone] ?? ""}`, children: f.text }, f.text)) }) : /* @__PURE__ */ jsx4("p", { className: "panel mb-4 px-3 py-2 text-sm text-emerald-300", "data-testid": "findings", children: "Alles in Ordnung \u2013 nichts braucht Aufmerksamkeit." }),
    /* @__PURE__ */ jsxs3("div", { className: "mb-6 grid grid-cols-2 gap-3 lg:grid-cols-5", children: [
      /* @__PURE__ */ jsx4(Stat, { label: "L\xE4uft seit", value: formatUptime(info.uptime_s) }),
      /* @__PURE__ */ jsx4(Stat, { label: "CPU", value: info.cpu_percent != null ? `${info.cpu_percent.toFixed(0)} %` : "?", sub: `${info.cpus ?? "?"} Kerne \xB7 Last ${info.load.map((l) => l.toFixed(2)).join(" / ")}` }),
      /* @__PURE__ */ jsx4(Stat, { label: "RAM", value: memPct != null ? `${memPct.toFixed(0)} %` : "?", sub: `${formatBytes(memUsed)} von ${formatBytes(info.mem_total)}` }),
      /* @__PURE__ */ jsx4(Stat, { label: "Swap", value: swapPct != null ? `${swapPct.toFixed(0)} %` : "keiner", sub: info.swap_total ? `${formatBytes(info.swap_used)} von ${formatBytes(info.swap_total)}` : void 0 }),
      /* @__PURE__ */ jsx4(Stat, { label: "Temperatur", value: info.temperature_c != null ? `${info.temperature_c.toFixed(0)} \xB0C` : "\u2013", tone: info.temperature_tone })
    ] }),
    /* @__PURE__ */ jsx4("h3", { className: "mb-2 text-sm font-semibold uppercase tracking-wider opacity-70", children: "Dateisysteme" }),
    /* @__PURE__ */ jsxs3("table", { className: "mb-6 w-full text-sm", "data-testid": "disks", children: [
      /* @__PURE__ */ jsx4("thead", { children: /* @__PURE__ */ jsxs3("tr", { className: "border-b border-white/10 text-left text-xs uppercase opacity-60", children: [
        /* @__PURE__ */ jsx4("th", { className: "py-1", children: "Einh\xE4ngepunkt" }),
        /* @__PURE__ */ jsx4("th", { className: "py-1", children: "Ger\xE4t" }),
        /* @__PURE__ */ jsx4("th", { className: "py-1 w-1/3", children: "Belegung" }),
        /* @__PURE__ */ jsx4("th", { className: "py-1", children: "Frei" })
      ] }) }),
      /* @__PURE__ */ jsx4("tbody", { className: "divide-y divide-white/5", children: info.disks.map((d) => /* @__PURE__ */ jsxs3("tr", { children: [
        /* @__PURE__ */ jsx4("td", { className: "py-1.5 break-words", children: d.mount }),
        /* @__PURE__ */ jsxs3("td", { className: "py-1.5 break-words opacity-70", children: [
          d.device,
          " \xB7 ",
          d.fstype
        ] }),
        /* @__PURE__ */ jsxs3("td", { className: "py-1.5 pr-4", children: [
          /* @__PURE__ */ jsxs3("div", { className: `mb-0.5 text-xs ${TONE_TEXT[d.tone] ?? ""}`, children: [
            d.percent.toFixed(0),
            " % von ",
            formatBytes(d.size)
          ] }),
          /* @__PURE__ */ jsx4(Bar, { percent: d.percent, tone: d.tone })
        ] }),
        /* @__PURE__ */ jsx4("td", { className: "py-1.5 whitespace-nowrap opacity-80", children: formatBytes(d.available) })
      ] }, `${d.device}${d.mount}`)) })
    ] }),
    /* @__PURE__ */ jsxs3("div", { className: "grid gap-6 lg:grid-cols-2", children: [
      /* @__PURE__ */ jsxs3("div", { children: [
        /* @__PURE__ */ jsx4("h3", { className: "mb-2 text-sm font-semibold uppercase tracking-wider opacity-70", children: "Dienste" }),
        info.failed_units.length === 0 ? /* @__PURE__ */ jsx4("p", { className: "text-sm opacity-70", children: "Keine fehlgeschlagenen systemd-Dienste." }) : /* @__PURE__ */ jsx4("ul", { className: "text-sm", "data-testid": "failed-units", children: info.failed_units.map((u) => /* @__PURE__ */ jsx4(UnitRow, { unit: u, tone: "text-red-300", canRestart: canRestart && restartable.has(u), busy: busyUnit, onRestart }, u)) }),
        (info.running_units?.length ?? 0) > 0 && /* @__PURE__ */ jsxs3("details", { className: "mt-3", "data-testid": "running-units", children: [
          /* @__PURE__ */ jsxs3("summary", { className: "cursor-pointer text-sm", children: [
            info.running_units?.length,
            " laufende Dienste"
          ] }),
          /* @__PURE__ */ jsx4("ul", { className: "mt-1 max-h-72 overflow-y-auto text-xs opacity-90", children: info.running_units?.map((u) => /* @__PURE__ */ jsx4(UnitRow, { unit: u, canRestart: canRestart && restartable.has(u), busy: busyUnit, onRestart }, u)) })
        ] })
      ] }),
      /* @__PURE__ */ jsxs3("div", { children: [
        /* @__PURE__ */ jsx4("h3", { className: "mb-2 text-sm font-semibold uppercase tracking-wider opacity-70", children: "Updates" }),
        info.updates == null ? /* @__PURE__ */ jsx4("p", { className: "text-sm opacity-70", children: "Paketverwaltung nicht unterst\xFCtzt (nur apt)." }) : info.updates.length === 0 ? /* @__PURE__ */ jsx4("p", { className: "text-sm opacity-70", children: "Auf dem neuesten Stand (laut letzter Paketlisten-Aktualisierung)." }) : /* @__PURE__ */ jsxs3("details", { "data-testid": "updates", children: [
          /* @__PURE__ */ jsxs3("summary", { className: "cursor-pointer text-sm", children: [
            info.updates.length,
            " Update(s) ausstehend",
            security ? `, davon ${security} Sicherheits-Update(s)` : ""
          ] }),
          /* @__PURE__ */ jsx4("ul", { className: "mt-1 columns-2 text-xs opacity-80", children: info.updates.map((u) => /* @__PURE__ */ jsx4("li", { className: `break-words ${u.security ? "text-amber-300" : ""}`, children: u.package }, u.package)) })
        ] }),
        info.reboot_required && /* @__PURE__ */ jsx4("p", { className: "mt-2 text-sm text-amber-300", children: "Neustart n\xF6tig, damit installierte Updates greifen." })
      ] })
    ] })
  ] });
}
function SystemPage() {
  const [urlParams, updateUrl] = useUrlParams();
  const hostId = urlParams.get("host") || null;
  const [hosts, setHosts] = useState4(null);
  const [info, setInfo] = useState4(null);
  const [error, setError] = useState4(null);
  const [loading, setLoading] = useState4(false);
  const [tab, setTab] = useState4("live");
  const [busy, setBusy] = useState4(null);
  const [message, setMessage] = useState4(null);
  const unmountSignal = useUnmountSignal();
  const canRestart = deck().hasPermission("hosts.execute");
  const hostIdRef = useRef3(hostId);
  const tabRef = useRef3(tab);
  hostIdRef.current = hostId;
  tabRef.current = tab;
  const busyUnit = busy && busy.hostId === hostId ? busy.unit : null;
  const fetchLive = useCallback2(async (id) => {
    const res = await authedFetch(`/ext/system/hosts/${id}/live`);
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(errorFromBody(body, res.status));
    return body;
  }, []);
  useEffect5(() => {
    authedFetch("/hosts").then((res) => res.ok ? res.json() : []).then((list) => setHosts(list.filter((h) => h.os_family === "linux"))).catch(() => setHosts([]));
  }, []);
  const load = useCallback2(async (id) => {
    setLoading(true);
    setError(null);
    try {
      const res = await authedFetch(`/ext/system/hosts/${id}/info`);
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      if (hostIdRef.current === id) setInfo(body);
    } catch (err) {
      if (hostIdRef.current === id) {
        setInfo(null);
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect5(() => {
    setInfo(null);
    if (hostId && tab === "info") void load(hostId);
  }, [hostId, tab, load]);
  const hostName = info?.host.name ?? (hosts ?? []).find((h) => h.id === hostId)?.display_name;
  useEffect5(() => {
    setMessage(null);
  }, [hostId]);
  async function restartUnit(unit) {
    if (!hostId) return;
    const ok = await deck().confirmDialog(
      `Dienst ${unit} auf ${hostName ?? "diesem Server"} neu starten? Er ist dabei kurz nicht erreichbar.`,
      { title: "Dienst neu starten", confirmLabel: "Neu starten" }
    );
    if (!ok) return;
    setBusy({ hostId, unit });
    setMessage(null);
    const show = (m) => {
      if (hostIdRef.current === hostId) setMessage(m);
    };
    try {
      const run = await runAction(
        `/ext/system/hosts/${encodeURIComponent(hostId)}/services/restart`,
        { method: "POST", body: JSON.stringify({ unit }) },
        { signal: unmountSignal() }
      );
      const status = run.action.status ?? "";
      if (status === "succeeded") show({ kind: "ok", text: run.action.result?.output || `${unit} neu gestartet.` });
      else if (status === "proposed") show({ kind: "ok", text: "Vorgeschlagen \u2013 wartet auf Freigabe unter \u201EAktionen\u201C." });
      else if (isActionRunning(status)) show({ kind: "ok", text: RUNNING_IN_BACKGROUND });
      else show({ kind: "error", text: run.text });
    } catch (err) {
      show({ kind: "error", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy((b) => b && b.hostId === hostId && b.unit === unit ? null : b);
      if (hostIdRef.current === hostId && tabRef.current === "info") void load(hostId);
    }
  }
  return /* @__PURE__ */ jsxs3("div", { className: "p-4 sm:p-6", children: [
    /* @__PURE__ */ jsxs3("div", { className: "mb-4 flex flex-wrap items-center gap-3", children: [
      /* @__PURE__ */ jsxs3("h2", { className: "text-2xl font-semibold tracking-tight", children: [
        "System",
        hostName ? `: ${hostName}` : ""
      ] }),
      /* @__PURE__ */ jsxs3(
        "select",
        {
          "aria-label": "Server w\xE4hlen",
          value: hostId ?? "",
          onChange: (e) => updateUrl({ host: e.target.value || null }),
          className: "rounded bg-white/10 px-2 py-1 text-sm",
          children: [
            /* @__PURE__ */ jsx4("option", { value: "", children: "Server w\xE4hlen \u2026" }),
            (hosts ?? []).map((h) => /* @__PURE__ */ jsxs3("option", { value: h.id, children: [
              h.display_name,
              " (",
              h.address,
              ")",
              h.credential === null ? " \u2013 ohne SSH-Zugang" : ""
            ] }, h.id))
          ]
        }
      ),
      hostId && /* @__PURE__ */ jsx4("div", { className: "flex overflow-hidden rounded-lg border border-white/10 text-xs", role: "tablist", "aria-label": "Ansicht", children: [["live", "Live"], ["info", "Zustand & Updates"]].map(([key, label]) => /* @__PURE__ */ jsx4(
        "button",
        {
          type: "button",
          role: "tab",
          "aria-selected": tab === key,
          onClick: () => setTab(key),
          className: `px-3 py-1 ${tab === key ? "bg-white/15" : "opacity-60 hover:bg-white/5"}`,
          children: label
        },
        key
      )) }),
      hostId && tab === "info" && /* @__PURE__ */ jsx4("button", { type: "button", onClick: () => void load(hostId), disabled: loading, className: "rounded bg-white/10 px-2 py-1 text-xs hover:bg-white/20 disabled:opacity-40", children: loading ? "Frage ab \u2026" : "Aktualisieren" })
    ] }),
    !hostId && hosts !== null && hosts.length === 0 && /* @__PURE__ */ jsx4(
      EmptyState,
      {
        icon: "eye",
        title: "Noch kein Linux-Server",
        text: "Lege unter Server & Zug\xE4nge einen Linux-Server mit SSH-Zugang an. Dann siehst du hier seine Auslastung, Dienste und Updates.",
        action: /* @__PURE__ */ jsx4(SettingsLink, { to: "/settings/hosts", permission: "hosts.write", children: "Server & Zug\xE4nge \xF6ffnen" })
      }
    ),
    !hostId && hosts !== null && hosts.length > 0 && /* @__PURE__ */ jsx4("p", { className: "text-sm opacity-60", children: "Einen Linux-Server w\xE4hlen \u2013 oder \xFCber dessen Server-Seite \u201ESystem-Monitor\u201C \xF6ffnen." }),
    hostId && tab === "live" && /* @__PURE__ */ jsx4(LiveView, { hostId, fetchLive }),
    tab === "info" && error && /* @__PURE__ */ jsxs3("p", { className: "text-sm text-red-400", children: [
      "Fehler: ",
      error
    ] }),
    hostId && tab === "info" && loading && !info && /* @__PURE__ */ jsx4("p", { className: "text-sm opacity-60", children: "Frage den Server ab \u2026 (dauert gut eine Sekunde, die CPU-Last wird \xFCber 1 s gemessen)" }),
    tab === "info" && message && /* @__PURE__ */ jsx4(Notice, { kind: message.kind, text: message.text, onClose: () => setMessage(null) }),
    tab === "info" && info && /* @__PURE__ */ jsx4(InfoView, { info, canRestart, busyUnit, onRestart: (u) => void restartUnit(u) })
  ] });
}
export {
  SystemPage,
  formatBytes,
  formatUptime
};
