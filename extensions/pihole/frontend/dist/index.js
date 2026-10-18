// src/PiholePage.tsx
import { useCallback, useEffect as useEffect3, useState as useState2 } from "react";

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
function Notice({ text, kind = "error", onClose }) {
  const cls = kind === "ok" ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200" : "border-red-500/30 bg-red-500/10 text-red-200";
  return /* @__PURE__ */ jsxs("div", { role: kind === "ok" ? "status" : "alert", className: `mb-4 flex items-start justify-between gap-3 rounded-lg border px-3 py-2 text-sm ${cls}`, children: [
    /* @__PURE__ */ jsx("span", { children: text }),
    onClose && /* @__PURE__ */ jsx("button", { type: "button", "aria-label": "Hinweis schlie\xDFen", onClick: onClose, className: "opacity-60 hover:opacity-100", children: /* @__PURE__ */ jsx(Icon, { name: "x", size: 14 }) })
  ] });
}
function Field({ label, children, className = "" }) {
  return /* @__PURE__ */ jsxs("label", { className: `block text-sm ${className}`, children: [
    /* @__PURE__ */ jsx("span", { className: "mb-1.5 block text-white/70", children: label }),
    children
  ] });
}

// ../../_shared/frontend/src/links.tsx
import { useEffect as useEffect2, useState } from "react";
import { jsx as jsx2 } from "react/jsx-runtime";
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

// src/PiholePage.tsx
import { Fragment as Fragment2, jsx as jsx3, jsxs as jsxs2 } from "react/jsx-runtime";
var API = "/ext/pihole";
var SETTINGS_PATH = "/settings/extensions/pihole";
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
function ago(epoch) {
  if (!epoch) return "unbekannt";
  const s = Date.now() / 1e3 - epoch;
  if (s < 3600) return "vor weniger als einer Stunde";
  if (s < 86400) return `vor ${Math.round(s / 3600)} Std.`;
  const days = Math.round(s / 86400);
  return `vor ${days} ${days === 1 ? "Tag" : "Tagen"}`;
}
function domainProblem(raw) {
  const domain = raw.trim().toLowerCase().replace(/\.$/, "");
  if (!domain) return null;
  const labels = domain.split(".");
  const ok = domain.length >= 3 && domain.length <= 253 && labels.length >= 2 && labels.every((l) => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(l));
  return ok ? null : "Bitte eine Domain wie werbung.example.com eintragen (ohne http:// und ohne Pfad).";
}
var PROBLEM = {
  unreachable: { label: "nicht erreichbar", tone: "bad" },
  auth_failed: { label: "Anmeldung fehlgeschlagen", tone: "bad" },
  unsupported: { label: "nicht unterst\xFCtzt", tone: "warn" }
};
function SettingsHint() {
  if (deck().hasPermission("extensions.manage")) {
    return /* @__PURE__ */ jsx3("a", { href: SETTINGS_PATH, className: buttonClass("primary", true), children: "Jetzt einrichten" });
  }
  return /* @__PURE__ */ jsx3("p", { className: "text-xs text-white/45", children: "Ein Administrator kann das unter Einstellungen \u2192 Erweiterungen \u2192 Pi-hole einrichten." });
}
function BlockingCard({ data, canAct, busy, onPause, onResume }) {
  const enabled = data.blocking?.enabled;
  const minutesLeft = data.blocking?.timer_s ? Math.max(1, Math.round(data.blocking.timer_s / 60)) : null;
  const badge = enabled === true ? { label: "Blockierung aktiv", tone: "good" } : enabled === false ? minutesLeft ? { label: `pausiert \u2013 noch ${minutesLeft} Min.`, tone: "warn" } : { label: "Blockierung aus", tone: "bad" } : { label: "Zustand unklar", tone: "warn" };
  return /* @__PURE__ */ jsx3(Card, { title: "Blockierung", actions: /* @__PURE__ */ jsx3(Badge, { tone: badge.tone, children: badge.label }), children: /* @__PURE__ */ jsx3("div", { "data-testid": "blocking", className: "flex flex-wrap items-center gap-2", children: enabled === false ? /* @__PURE__ */ jsxs2(Fragment2, { children: [
    /* @__PURE__ */ jsx3("span", { className: "text-sm text-white/70", children: minutesLeft ? `Blockierung pausiert, noch ${minutesLeft} Min.` : "Blockierung ist ausgeschaltet \u2013 Werbung und Tracker kommen durch." }),
    canAct && /* @__PURE__ */ jsx3(Button, { variant: "primary", small: true, disabled: busy !== null, onClick: onResume, children: "Blockierung fortsetzen" })
  ] }) : /* @__PURE__ */ jsxs2(Fragment2, { children: [
    /* @__PURE__ */ jsx3("span", { className: "text-sm text-white/70", children: canAct ? "Blockierung pausieren:" : "Werbung und Tracker werden im ganzen Netz blockiert." }),
    canAct && PAUSE_MINUTES.map((m) => /* @__PURE__ */ jsxs2(Button, { small: true, disabled: busy !== null, onClick: () => onPause(m), ariaLabel: `${m} Minuten pausieren`, children: [
      m,
      " Min."
    ] }, m))
  ] }) }) });
}
function ClientsCard({ clients }) {
  return /* @__PURE__ */ jsx3(Card, { title: "Aktivste Ger\xE4te", description: "Die meisten Anfragen in den letzten 24 Stunden.", children: clients.length === 0 ? /* @__PURE__ */ jsx3("p", { className: "text-sm text-white/45", children: "Pi-hole meldet noch keine Ger\xE4te." }) : /* @__PURE__ */ jsx3("ul", { "data-testid": "top-clients", className: "divide-y divide-white/[0.06]", children: clients.map((c) => /* @__PURE__ */ jsxs2("li", { className: "flex flex-wrap items-center justify-between gap-2 py-2 first:pt-0 last:pb-0", children: [
    /* @__PURE__ */ jsxs2("div", { className: "min-w-0", children: [
      /* @__PURE__ */ jsx3("p", { className: "break-words text-sm font-medium", children: c.name }),
      c.ip && c.ip !== c.name && /* @__PURE__ */ jsx3("p", { className: "break-words text-xs text-white/50", children: c.ip })
    ] }),
    /* @__PURE__ */ jsxs2("span", { className: "text-sm tabular-nums text-white/70", children: [
      num(c.count),
      " Anfragen"
    ] })
  ] }, `${c.ip ?? ""}-${c.name}`)) }) });
}
function ListsCard({ data, canAct, busy, onGravity, onDomain }) {
  const [domain, setDomain] = useState2("");
  const problem = domainProblem(domain);
  const s = data.summary;
  async function submit(list) {
    if (!domain.trim() || problem) return;
    if (await onDomain(list, domain.trim().toLowerCase().replace(/\.$/, ""))) setDomain("");
  }
  return /* @__PURE__ */ jsx3(
    Card,
    {
      title: "Sperrlisten",
      description: `${num(s?.domains_blocked)} Domains \xB7 aktualisiert ${ago(s?.gravity_updated_at)}`,
      actions: canAct ? /* @__PURE__ */ jsx3(Button, { small: true, disabled: busy !== null, onClick: onGravity, children: "Listen aktualisieren" }) : void 0,
      children: canAct ? (
        // Enter löst nichts aus: ob erlauben oder sperren, entscheidet der Knopf.
        /* @__PURE__ */ jsxs2("form", { "data-testid": "domain-form", className: "space-y-2", onSubmit: (e) => e.preventDefault(), children: [
          /* @__PURE__ */ jsx3(Field, { label: "Eine Domain erlauben oder sperren", children: /* @__PURE__ */ jsx3(
            "input",
            {
              value: domain,
              onChange: (e) => setDomain(e.target.value),
              placeholder: "werbung.example.com",
              className: inputClass,
              "aria-invalid": problem ? true : void 0
            }
          ) }),
          problem && /* @__PURE__ */ jsx3("p", { className: "text-xs text-red-300", children: problem }),
          /* @__PURE__ */ jsxs2("div", { className: "flex flex-wrap items-center gap-2", children: [
            /* @__PURE__ */ jsx3(Button, { small: true, disabled: busy !== null || !domain.trim() || problem !== null, onClick: () => void submit("allow"), children: "Erlauben" }),
            /* @__PURE__ */ jsx3(Button, { small: true, variant: "danger", disabled: busy !== null || !domain.trim() || problem !== null, onClick: () => void submit("deny"), children: "Sperren" }),
            /* @__PURE__ */ jsx3("span", { className: "text-xs text-white/45", children: "Gilt genau f\xFCr diese Domain, nicht f\xFCr Unterdomains." })
          ] })
        ] })
      ) : /* @__PURE__ */ jsx3("p", { className: "text-sm text-white/45", children: "Listen \xE4ndern d\xFCrfen nur Konten, die auch Server steuern d\xFCrfen." })
    }
  );
}
function PiholePage() {
  const unmountSignal = useUnmountSignal();
  const [data, setData] = useState2(null);
  const [loadError, setLoadError] = useState2(null);
  const [message, setMessage] = useState2(null);
  const [busy, setBusy] = useState2(null);
  const canAct = deck().hasPermission("hosts.execute");
  const load = useCallback(() => {
    setLoadError(null);
    call(`${API}/summary`).then(setData).catch((err) => setLoadError(err instanceof Error ? err.message : String(err)));
  }, []);
  useEffect3(() => {
    load();
  }, [load]);
  async function run(key, path, body2) {
    setBusy(key);
    setMessage(null);
    let ok = false;
    try {
      const result = await proposeAndApprove(path, body2, unmountSignal());
      setMessage(result);
      ok = result?.kind === "ok";
    } catch (err) {
      setMessage({ kind: "error", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(null);
      load();
    }
    return ok;
  }
  async function pause(minutes) {
    const ok = await deck().confirmDialog(
      `Pi-hole f\xFCr ${minutes} Minuten pausieren? In dieser Zeit werden Werbung und Tracker im ganzen Netz nicht blockiert. Danach blockiert Pi-hole von selbst wieder.`,
      { confirmLabel: "Pausieren" }
    );
    if (!ok) return;
    await run("blocking", `${API}/pause`, { minutes });
  }
  async function gravity() {
    const ok = await deck().confirmDialog(
      "Alle Sperrlisten neu herunterladen? Das dauert ein paar Minuten; Pi-hole blockiert dabei weiter.",
      { title: "Sperrlisten aktualisieren", confirmLabel: "Aktualisieren" }
    );
    if (!ok) return;
    await run("gravity", `${API}/gravity`, void 0);
  }
  async function domain(list, value) {
    const ok = list === "allow" ? await deck().confirmDialog(`\u201E${value}\u201C erlauben? Pi-hole blockiert die Domain dann nicht mehr, auch wenn eine Sperrliste sie nennt.`, { title: "Domain erlauben", confirmLabel: "Erlauben" }) : await deck().confirmDialog(`\u201E${value}\u201C sperren? Ger\xE4te im Netz erreichen die Domain danach nicht mehr.`, { title: "Domain sperren", danger: true, confirmLabel: "Sperren" });
    if (!ok) return false;
    return run("domain", `${API}/domains`, { list, domain: value });
  }
  let body = null;
  if (data?.state === "not_configured") {
    body = /* @__PURE__ */ jsx3(Card, { title: "Pi-hole", children: /* @__PURE__ */ jsxs2("div", { "data-testid": "not-configured", className: "flex flex-col items-start gap-3", children: [
      /* @__PURE__ */ jsx3(Badge, { children: "nicht eingerichtet" }),
      /* @__PURE__ */ jsx3("p", { className: "text-sm text-white/60", children: data.message }),
      /* @__PURE__ */ jsx3(SettingsHint, {})
    ] }) });
  } else if (data && (data.state !== "ok" || !data.summary)) {
    const problem = PROBLEM[data.state] ?? PROBLEM.unreachable;
    body = /* @__PURE__ */ jsx3(Card, { title: "Pi-hole", actions: /* @__PURE__ */ jsx3(Badge, { tone: problem.tone, children: problem.label }), children: /* @__PURE__ */ jsxs2("div", { "data-testid": "problem", className: "flex flex-col items-start gap-3", children: [
      /* @__PURE__ */ jsx3("p", { className: "text-sm text-white/70", children: data.message }),
      /* @__PURE__ */ jsxs2("div", { className: "flex flex-wrap items-center gap-2", children: [
        /* @__PURE__ */ jsx3(Button, { small: true, onClick: load, children: "Erneut versuchen" }),
        (data.state === "auth_failed" || data.state === "unsupported") && deck().hasPermission("extensions.manage") && /* @__PURE__ */ jsx3("a", { href: SETTINGS_PATH, className: buttonClass("ghost", true), children: "Einstellungen" })
      ] })
    ] }) });
  } else if (data?.summary) {
    const s = data.summary;
    body = /* @__PURE__ */ jsxs2("div", { className: "space-y-4", children: [
      /* @__PURE__ */ jsxs2("div", { "data-testid": "stats", className: "grid grid-cols-2 gap-3 lg:grid-cols-4", children: [
        /* @__PURE__ */ jsx3(Stat, { label: "Anfragen (24 Std.)", value: num(s.queries_total), hint: s.clients_active != null ? `${num(s.clients_active)} aktive Ger\xE4te` : void 0 }),
        /* @__PURE__ */ jsx3(Stat, { label: "Blockiert", value: num(s.queries_blocked) }),
        /* @__PURE__ */ jsx3(Stat, { label: "Anteil blockiert", value: pct(s.percent_blocked) }),
        /* @__PURE__ */ jsx3(Stat, { label: "Domains auf der Sperrliste", value: num(s.domains_blocked), hint: `aktualisiert ${ago(s.gravity_updated_at)}` })
      ] }),
      /* @__PURE__ */ jsx3(BlockingCard, { data, canAct, busy, onPause: (m) => void pause(m), onResume: () => void run("blocking", `${API}/resume`, void 0) }),
      /* @__PURE__ */ jsxs2("div", { className: "grid gap-4 lg:grid-cols-2", children: [
        /* @__PURE__ */ jsx3(ClientsCard, { clients: data.top_clients ?? [] }),
        /* @__PURE__ */ jsx3(ListsCard, { data, canAct, busy, onGravity: () => void gravity(), onDomain: domain })
      ] })
    ] });
  } else if (!loadError) {
    body = /* @__PURE__ */ jsx3(Card, { title: "Pi-hole", children: /* @__PURE__ */ jsx3("p", { className: "text-sm text-white/50", children: "Lade \u2026" }) });
  }
  const adminHref = safeHref(data?.url);
  return /* @__PURE__ */ jsxs2(
    Page,
    {
      title: "Pi-hole",
      description: "Werbe- und Tracker-Blocker f\xFCr das ganze Netz.",
      actions: /* @__PURE__ */ jsxs2(Fragment2, { children: [
        adminHref && /* @__PURE__ */ jsx3("a", { href: `${adminHref}/admin/`, target: "_blank", rel: "noreferrer", className: buttonClass("ghost"), children: "Pi-hole \xF6ffnen" }),
        deck().hasPermission("extensions.manage") && /* @__PURE__ */ jsx3("a", { href: SETTINGS_PATH, className: buttonClass("ghost"), children: "Einstellungen" }),
        /* @__PURE__ */ jsx3(Button, { onClick: load, children: "Aktualisieren" })
      ] }),
      children: [
        loadError && /* @__PURE__ */ jsx3(Notice, { text: loadError, onClose: () => setLoadError(null) }),
        message && /* @__PURE__ */ jsx3(Notice, { text: message.text, kind: message.kind, onClose: () => setMessage(null) }),
        body
      ]
    }
  );
}
export {
  PiholePage,
  domainProblem
};
