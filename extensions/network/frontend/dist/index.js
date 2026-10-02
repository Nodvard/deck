// src/NetworkPage.tsx
import { useCallback, useEffect as useEffect2, useMemo, useState } from "react";

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

// src/NetworkPage.tsx
import { Fragment as Fragment2, jsx as jsx2, jsxs as jsxs2 } from "react/jsx-runtime";
var API = "/ext/network";
var SETTINGS_PATH = "/settings/extensions/network";
var PAUSE_MINUTES = [5, 15, 60];
async function call(path, init = {}) {
  const res = await authedFetch(path, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorFromBody(body, res.status));
  return body;
}
async function proposeAndApprove(path, body, signal) {
  const { action, approved } = await runAction(path, { method: "POST", body: body === void 0 ? void 0 : JSON.stringify(body) }, { signal });
  const status = action.status ?? "";
  if (approved) {
    if (status === "succeeded") return { kind: "ok", text: action.result?.output || "Erledigt." };
    if (isActionRunning(status)) return { kind: "ok", text: RUNNING_IN_BACKGROUND };
    return { kind: "error", text: action.result?.error || `Fehlgeschlagen (${status}).` };
  }
  if (status === "proposed") return { kind: "ok", text: "Vorgeschlagen \u2013 wartet auf Freigabe unter \u201EAktionen\u201C." };
  if (status === "succeeded") return { kind: "ok", text: action.result?.output || "Erledigt." };
  if (status === "failed") return { kind: "error", text: action.result?.error || "Fehlgeschlagen." };
  if (isActionRunning(status)) return { kind: "ok", text: RUNNING_IN_BACKGROUND };
  if (status === "denied") return { kind: "error", text: `Abgelehnt${action.detail ? `: ${action.detail}` : "."}` };
  return { kind: "ok", text: `Status: ${status}` };
}
function num(value) {
  return value == null ? "\u2013" : value.toLocaleString("de-DE");
}
function pct(value) {
  return value == null ? "\u2013" : `${value.toLocaleString("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} %`;
}
function date(iso) {
  if (!iso) return "unbekannt";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "unbekannt" : d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });
}
function ago(epoch) {
  if (!epoch) return "unbekannt";
  const s = Date.now() / 1e3 - epoch;
  if (s < 3600) return "vor weniger als einer Stunde";
  if (s < 86400) return `vor ${Math.round(s / 3600)} Std.`;
  const days = Math.round(s / 86400);
  return `vor ${days} ${days === 1 ? "Tag" : "Tagen"}`;
}
var CERT_TONE = { ok: "good", warn: "warn", expired: "bad", unknown: "neutral" };
var PROBLEM = {
  unreachable: { label: "nicht erreichbar", tone: "bad" },
  auth_failed: { label: "Anmeldung fehlgeschlagen", tone: "bad" },
  unsupported: { label: "nicht unterst\xFCtzt", tone: "warn" }
};
function SettingsHint() {
  if (deck().hasPermission("extensions.manage")) {
    return /* @__PURE__ */ jsx2("a", { href: SETTINGS_PATH, className: buttonClass("primary", true), children: "Jetzt einrichten" });
  }
  return /* @__PURE__ */ jsx2("p", { className: "text-xs text-white/45", children: "Ein Administrator kann das unter Einstellungen \u2192 Erweiterungen \u2192 Netzwerk einrichten." });
}
function NotSetUp({ title, message, testId }) {
  return /* @__PURE__ */ jsx2(Card, { title, children: /* @__PURE__ */ jsxs2("div", { "data-testid": testId, className: "flex flex-col items-start gap-3", children: [
    /* @__PURE__ */ jsx2(Badge, { children: "nicht eingerichtet" }),
    /* @__PURE__ */ jsx2("p", { className: "text-sm text-white/60", children: message }),
    /* @__PURE__ */ jsx2(SettingsHint, {})
  ] }) });
}
function Problem({ title, state, message, onRetry, testId }) {
  const problem = PROBLEM[state] ?? PROBLEM.unreachable;
  return /* @__PURE__ */ jsx2(Card, { title, actions: /* @__PURE__ */ jsx2(Badge, { tone: problem.tone, children: problem.label }), children: /* @__PURE__ */ jsxs2("div", { "data-testid": testId, className: "flex flex-col items-start gap-3", children: [
    /* @__PURE__ */ jsx2("p", { className: "text-sm text-white/70", children: message }),
    /* @__PURE__ */ jsxs2("div", { className: "flex flex-wrap items-center gap-2", children: [
      /* @__PURE__ */ jsx2(Button, { small: true, onClick: onRetry, children: "Erneut versuchen" }),
      (state === "auth_failed" || state === "unsupported") && deck().hasPermission("extensions.manage") && /* @__PURE__ */ jsx2("a", { href: SETTINGS_PATH, className: buttonClass("ghost", true), children: "Einstellungen" })
    ] })
  ] }) });
}
function PiholeSection({ data, canAct, busy, onPause, onResume, onRetry }) {
  if (data.state === "not_configured") return /* @__PURE__ */ jsx2(NotSetUp, { title: "Pi-hole", message: data.message, testId: "pihole-not-configured" });
  if (data.state !== "ok" || !data.summary) return /* @__PURE__ */ jsx2(Problem, { title: "Pi-hole", state: data.state, message: data.message, onRetry, testId: "pihole-problem" });
  const s = data.summary;
  const blocking = data.blocking;
  const enabled = blocking?.enabled;
  const minutesLeft = blocking?.timer_s ? Math.max(1, Math.round(blocking.timer_s / 60)) : null;
  const badge = enabled === true ? { label: "Blockierung aktiv", tone: "good" } : enabled === false ? minutesLeft ? { label: `pausiert \u2013 noch ${minutesLeft} Min.`, tone: "warn" } : { label: "Blockierung aus", tone: "bad" } : { label: "Zustand unklar", tone: "warn" };
  return /* @__PURE__ */ jsx2(
    Card,
    {
      title: "Pi-hole",
      description: data.url ? /* @__PURE__ */ jsx2("a", { href: `${data.url}/admin/`, target: "_blank", rel: "noreferrer", className: "hover:text-white hover:underline", children: "Pi-hole \xF6ffnen" }) : void 0,
      actions: /* @__PURE__ */ jsx2(Badge, { tone: badge.tone, children: badge.label }),
      children: /* @__PURE__ */ jsxs2("div", { "data-testid": "pihole", className: "space-y-4", children: [
        /* @__PURE__ */ jsxs2("div", { className: "grid grid-cols-2 gap-3 lg:grid-cols-4", children: [
          /* @__PURE__ */ jsx2(Stat, { label: "Anfragen (24 Std.)", value: num(s.queries_total), hint: s.clients_active != null ? `${num(s.clients_active)} aktive Ger\xE4te` : void 0 }),
          /* @__PURE__ */ jsx2(Stat, { label: "Blockiert", value: num(s.queries_blocked) }),
          /* @__PURE__ */ jsx2(Stat, { label: "Anteil blockiert", value: pct(s.percent_blocked) }),
          /* @__PURE__ */ jsx2(Stat, { label: "Domains auf der Sperrliste", value: num(s.domains_blocked), hint: `aktualisiert ${ago(s.gravity_updated_at)}` })
        ] }),
        canAct && /* @__PURE__ */ jsx2("div", { className: "flex flex-wrap items-center gap-2 border-t border-white/[0.06] pt-3", children: enabled === false ? /* @__PURE__ */ jsxs2(Fragment2, { children: [
          /* @__PURE__ */ jsx2("span", { className: "text-sm text-white/70", children: minutesLeft ? `Blockierung pausiert, noch ${minutesLeft} Min.` : "Blockierung ist ausgeschaltet." }),
          /* @__PURE__ */ jsx2(Button, { variant: "primary", small: true, disabled: busy !== null, onClick: onResume, children: "Blockierung fortsetzen" })
        ] }) : /* @__PURE__ */ jsxs2(Fragment2, { children: [
          /* @__PURE__ */ jsx2("span", { className: "text-sm text-white/70", children: "Blockierung pausieren:" }),
          PAUSE_MINUTES.map((m) => /* @__PURE__ */ jsxs2(Button, { small: true, disabled: busy !== null, onClick: () => onPause(m), ariaLabel: `${m} Minuten pausieren`, children: [
            m,
            " Min."
          ] }, m))
        ] }) })
      ] })
    }
  );
}
function CertBadge({ cert }) {
  if (!cert) return /* @__PURE__ */ jsx2(Badge, { children: "ohne Zertifikat" });
  return /* @__PURE__ */ jsx2(Badge, { tone: CERT_TONE[cert.status], children: cert.status === "ok" ? `g\xFCltig bis ${date(cert.expires_at)}` : `${cert.status_label} \xB7 ${cert.days_text}` });
}
function hostUrl(host) {
  const domain = host.domains[0];
  if (!domain || domain.includes("*")) return null;
  return `${host.certificate_id ? "https" : "http"}://${domain}`;
}
function NpmSection({ data, canAct, busy, onToggle, onRetry }) {
  const [search, setSearch] = useState("");
  const hosts = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return data.hosts;
    return data.hosts.filter((h) => h.domains.some((d) => d.toLowerCase().includes(q)) || (h.target ?? "").toLowerCase().includes(q));
  }, [data.hosts, search]);
  if (data.state === "not_configured") return /* @__PURE__ */ jsx2(NotSetUp, { title: "Nginx Proxy Manager", message: data.message, testId: "npm-not-configured" });
  if (data.state !== "ok" || !data.summary) {
    return /* @__PURE__ */ jsx2(Problem, { title: "Nginx Proxy Manager", state: data.state, message: data.message, onRetry, testId: "npm-problem" });
  }
  const s = data.summary;
  const urgent = data.certificates.filter((c) => c.status === "warn" || c.status === "expired");
  return /* @__PURE__ */ jsxs2("div", { className: "space-y-4", children: [
    /* @__PURE__ */ jsx2(
      Card,
      {
        title: "Nginx Proxy Manager",
        description: data.url ? /* @__PURE__ */ jsx2("a", { href: data.url, target: "_blank", rel: "noreferrer", className: "hover:text-white hover:underline", children: "Verwaltung \xF6ffnen" }) : void 0,
        children: /* @__PURE__ */ jsxs2("div", { className: "grid grid-cols-2 gap-3 lg:grid-cols-4", children: [
          /* @__PURE__ */ jsx2(Stat, { label: "Proxy-Hosts", value: `${s.hosts_enabled} / ${s.hosts}`, hint: "eingeschaltet / gesamt" }),
          /* @__PURE__ */ jsx2(Stat, { label: "Zertifikate", value: s.certificates }),
          /* @__PURE__ */ jsx2(Stat, { label: "Laufen bald ab", value: s.certificates_warn, hint: "in den n\xE4chsten 14 Tagen", tone: s.certificates_warn > 0 ? "warn" : void 0 }),
          /* @__PURE__ */ jsx2(Stat, { label: "Abgelaufen", value: s.certificates_expired, tone: s.certificates_expired > 0 ? "bad" : void 0 })
        ] })
      }
    ),
    urgent.length > 0 ? /* @__PURE__ */ jsx2(Card, { title: "Zertifikate, die Aufmerksamkeit brauchen", description: "Nginx Proxy Manager erneuert Let's-Encrypt-Zertifikate normalerweise selbst. Steht eins hier, klemmt meist die Erneuerung.", children: /* @__PURE__ */ jsx2("ul", { "data-testid": "urgent-certificates", className: "divide-y divide-white/[0.06]", children: urgent.map((c) => /* @__PURE__ */ jsxs2("li", { "data-testid": `cert-${c.id}`, className: "flex flex-wrap items-center justify-between gap-2 py-2 first:pt-0 last:pb-0", children: [
      /* @__PURE__ */ jsxs2("div", { className: "min-w-0", children: [
        /* @__PURE__ */ jsx2("p", { className: "break-words text-sm font-medium", children: c.name }),
        /* @__PURE__ */ jsxs2("p", { className: "break-words text-xs text-white/50", children: [
          c.domains.join(", "),
          " \xB7 ",
          c.provider_label,
          " \xB7 ",
          c.status === "expired" ? "abgelaufen am" : "l\xE4uft ab am",
          " ",
          date(c.expires_at)
        ] })
      ] }),
      /* @__PURE__ */ jsxs2(Badge, { tone: CERT_TONE[c.status], children: [
        c.status_label,
        " \xB7 ",
        c.days_text
      ] })
    ] }, c.id)) }) }) : data.certificates.length > 0 && /* @__PURE__ */ jsx2("p", { className: "text-sm text-emerald-300", children: "Alle Zertifikate sind noch mindestens 14 Tage g\xFCltig." }),
    /* @__PURE__ */ jsx2(Card, { title: "Proxy-Hosts", padded: false, actions: data.hosts.length > 5 ? /* @__PURE__ */ jsx2(SearchInput, { value: search, onChange: setSearch, placeholder: "Domain oder Ziel suchen", label: "Proxy-Hosts durchsuchen" }) : void 0, children: hosts.length === 0 ? /* @__PURE__ */ jsx2("p", { className: "px-5 py-6 text-center text-sm text-white/45", children: data.hosts.length === 0 ? "Noch keine Proxy-Hosts angelegt." : "Kein Proxy-Host passt zur Suche." }) : /* @__PURE__ */ jsx2("ul", { className: "divide-y divide-white/[0.06]", children: hosts.map((h) => {
      const warn = h.certificate && (h.certificate.status === "warn" || h.certificate.status === "expired");
      const url = hostUrl(h);
      return /* @__PURE__ */ jsxs2(
        "li",
        {
          "data-testid": `host-${h.id}`,
          className: `flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3 ${warn ? h.certificate?.status === "expired" ? "bg-red-500/[0.06]" : "bg-amber-500/[0.06]" : ""}`,
          children: [
            /* @__PURE__ */ jsxs2("div", { className: "min-w-0 flex-1 basis-60", children: [
              /* @__PURE__ */ jsx2("p", { className: "break-words text-sm font-medium", children: url ? /* @__PURE__ */ jsx2("a", { href: url, target: "_blank", rel: "noreferrer", className: "hover:underline", children: h.domains.join(", ") }) : h.domains.join(", ") }),
              /* @__PURE__ */ jsxs2("p", { className: "break-words text-xs text-white/50", children: [
                "\u2192 ",
                h.target ?? "kein Ziel",
                h.nginx_online === false ? " \xB7 Nginx meldet einen Fehler" : ""
              ] })
            ] }),
            /* @__PURE__ */ jsxs2("div", { className: "flex flex-wrap items-center gap-1.5", children: [
              /* @__PURE__ */ jsx2(CertBadge, { cert: h.certificate }),
              h.enabled ? /* @__PURE__ */ jsx2(Badge, { tone: "good", children: "an" }) : /* @__PURE__ */ jsx2(Badge, { tone: "neutral", children: "aus" }),
              canAct && /* @__PURE__ */ jsx2(Button, { small: true, variant: h.enabled ? "danger" : "secondary", disabled: busy !== null, onClick: () => onToggle(h), children: h.enabled ? "Ausschalten" : "Einschalten" })
            ] })
          ]
        },
        h.id
      );
    }) }) })
  ] });
}
function NetworkPage() {
  const unmountSignal = useUnmountSignal();
  const [pihole, setPihole] = useState(null);
  const [npm, setNpm] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(null);
  const canAct = deck().hasPermission("hosts.execute");
  const loadPihole = useCallback(() => {
    call(`${API}/pihole`).then(setPihole).catch((err) => setLoadError(err instanceof Error ? err.message : String(err)));
  }, []);
  const loadNpm = useCallback(() => {
    call(`${API}/npm`).then(setNpm).catch((err) => setLoadError(err instanceof Error ? err.message : String(err)));
  }, []);
  const loadAll = useCallback(() => {
    setLoadError(null);
    loadPihole();
    loadNpm();
  }, [loadPihole, loadNpm]);
  useEffect2(() => {
    loadAll();
  }, [loadAll]);
  async function run(key, path, body, after) {
    setBusy(key);
    setMessage(null);
    try {
      setMessage(await proposeAndApprove(path, body, unmountSignal()));
    } catch (err) {
      setMessage({ kind: "error", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(null);
      after();
    }
  }
  async function pause(minutes) {
    const ok = await deck().confirmDialog(
      `Pi-hole f\xFCr ${minutes} Minuten pausieren? In dieser Zeit werden Werbung und Tracker im ganzen Netz nicht blockiert. Danach blockiert Pi-hole von selbst wieder.`,
      { confirmLabel: "Pausieren" }
    );
    if (!ok) return;
    await run("pihole", `${API}/pihole/pause`, { minutes }, loadPihole);
  }
  async function resume() {
    await run("pihole", `${API}/pihole/resume`, void 0, loadPihole);
  }
  async function toggle(host) {
    const name = host.domains.join(", ") || `Proxy-Host ${host.id}`;
    const ok = host.enabled ? await deck().confirmDialog(
      `\u201E${name}\u201C ausschalten? Die Seite ist danach nicht mehr erreichbar, bis du den Proxy-Host wieder einschaltest.`,
      { title: "Proxy-Host ausschalten", danger: true, confirmLabel: "Ausschalten" }
    ) : await deck().confirmDialog(`\u201E${name}\u201C wieder einschalten?`, { title: "Proxy-Host einschalten", confirmLabel: "Einschalten" });
    if (!ok) return;
    await run(`host-${host.id}`, `${API}/npm/hosts/${host.id}/${host.enabled ? "disable" : "enable"}`, void 0, loadNpm);
  }
  return /* @__PURE__ */ jsxs2(
    Page,
    {
      title: "Netzwerk",
      description: "Pi-hole und Nginx Proxy Manager auf einen Blick.",
      actions: /* @__PURE__ */ jsxs2(Fragment2, { children: [
        deck().hasPermission("extensions.manage") && /* @__PURE__ */ jsx2("a", { href: SETTINGS_PATH, className: buttonClass("ghost"), children: "Einstellungen" }),
        /* @__PURE__ */ jsx2(Button, { onClick: loadAll, children: "Aktualisieren" })
      ] }),
      children: [
        loadError && /* @__PURE__ */ jsx2(Notice, { text: loadError, onClose: () => setLoadError(null) }),
        message && /* @__PURE__ */ jsx2(Notice, { text: message.text, kind: message.kind, onClose: () => setMessage(null) }),
        /* @__PURE__ */ jsxs2("div", { className: "space-y-6", children: [
          pihole ? /* @__PURE__ */ jsx2(PiholeSection, { data: pihole, canAct, busy, onPause: (m) => void pause(m), onResume: () => void resume(), onRetry: loadPihole }) : !loadError && /* @__PURE__ */ jsx2(Card, { title: "Pi-hole", children: /* @__PURE__ */ jsx2("p", { className: "text-sm text-white/50", children: "Lade \u2026" }) }),
          npm ? /* @__PURE__ */ jsx2(NpmSection, { data: npm, canAct, busy, onToggle: (h) => void toggle(h), onRetry: loadNpm }) : !loadError && /* @__PURE__ */ jsx2(Card, { title: "Nginx Proxy Manager", children: /* @__PURE__ */ jsx2("p", { className: "text-sm text-white/50", children: "Lade \u2026" }) })
        ] })
      ]
    }
  );
}
export {
  NetworkPage
};
