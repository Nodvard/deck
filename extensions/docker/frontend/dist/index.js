// src/ContainersPage.tsx
import { Fragment as Fragment11, useCallback as useCallback3, useEffect as useEffect13, useState as useState14 } from "react";

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
function errorFromBody(body, status2) {
  return bodyDetail(body) ?? (SERVER_DOWN_STATUS.has(status2) ? SERVER_UNAVAILABLE_TEXT : `HTTP ${status2}`);
}
async function errorText(res) {
  const body = await res.json().catch(() => ({}));
  return errorFromBody(body, res.status);
}

// ../../_shared/frontend/src/actions.ts
var RUNNING_IN_BACKGROUND = "L\xE4uft im Hintergrund \u2013 das Ergebnis steht unter \u201EAktionen\u201C.";
function isActionRunning(status2) {
  return status2 === "approved" || status2 === "executing";
}
var ACTION_POLL_INTERVAL_MS = 3e3;
var ACTION_POLL_MAX_MS = 60 * 60 * 1e3;
var ACTION_STATUS_LABEL = {
  proposed: "wartet auf Freigabe",
  approved: "genehmigt",
  executing: "l\xE4uft",
  succeeded: "abgeschlossen",
  failed: "fehlgeschlagen",
  denied: "abgelehnt",
  expired: "abgelaufen",
  dismissed: "verworfen"
};
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
function Icon({ name, size: size2 = 16, className = "" }) {
  return /* @__PURE__ */ jsx(
    "svg",
    {
      width: size2,
      height: size2,
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
  primary: "accent-gradient text-on-accent hover:bg-accent-strong",
  secondary: "border border-white/10 bg-white/[0.06] text-white hover:bg-white/[0.12]",
  danger: "border border-red-500/40 bg-red-500/15 text-red-200 hover:bg-red-500/25",
  ghost: "text-white/60 hover:bg-white/[0.06] hover:text-white"
};
var buttonClass = (variant = "secondary", small2 = false) => `inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${small2 ? "px-2.5 py-1 text-xs" : "px-3 py-1.5 text-sm"} ${BUTTON_STYLES[variant]}`;
function Button({
  children,
  onClick,
  variant = "secondary",
  type = "button",
  disabled,
  small: small2,
  title,
  ariaLabel,
  pressed
}) {
  return /* @__PURE__ */ jsx("button", { type, onClick, disabled, title, "aria-label": ariaLabel, "aria-pressed": pressed, className: buttonClass(variant, small2), children });
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
function useSettingText(extId, key, fallback) {
  const [value, setValue] = useState(fallback);
  useEffect2(() => {
    let cancelled = false;
    authedFetch(`/extensions/${extId}/settings`).then((res) => res.ok ? res.json() : null).then((body) => {
      const text = body?.values?.[key];
      if (!cancelled && typeof text === "string" && text.trim()) setValue(text.trim());
    }).catch(() => {
    });
    return () => {
      cancelled = true;
    };
  }, [extId, key]);
  return value;
}

// ../../_shared/frontend/src/location.ts
import { useCallback, useEffect as useEffect3, useMemo, useState as useState2 } from "react";
function useUrlParams() {
  const [search, setSearch] = useState2(() => window.location.search);
  const [visits, setVisits] = useState2(0);
  useEffect3(() => {
    const sync = () => setSearch(window.location.search);
    const visit3 = () => {
      sync();
      setVisits((n) => n + 1);
    };
    const onPopState = () => {
      if (!findDeck()?.navigateEvents) visit3();
    };
    sync();
    const navigateEvent = deckEventName("navigate");
    window.addEventListener("popstate", onPopState);
    window.addEventListener(navigateEvent, visit3);
    return () => {
      window.removeEventListener("popstate", onPopState);
      window.removeEventListener(navigateEvent, visit3);
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

// src/BulkPropose.tsx
import { useState as useState3 } from "react";
import { Fragment as Fragment2, jsx as jsx4, jsxs as jsxs3 } from "react/jsx-runtime";
var ACTIONS_PATH = "/actions";
var BUTTON = "px-2 py-0.5 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition disabled:opacity-40";
var isDatabaseWarning = (w) => w.startsWith("Datenbank");
function endpoint(c) {
  return `/ext/docker/containers/${c.hostId}/${encodeURIComponent(c.container)}/image-update`;
}
function BulkProposeButton({
  hostId,
  hostName,
  candidates,
  onDone
}) {
  const unmountSignal = useUnmountSignal();
  const [progress, setProgress] = useState3(null);
  const [result, setResult] = useState3(null);
  const [error, setError] = useState3(null);
  const n = candidates.length;
  async function run() {
    setResult(null);
    setError(null);
    const signal = unmountSignal();
    const ready = [];
    const skipped = [];
    try {
      for (const [i, c] of candidates.entries()) {
        if (signal.aborted) return;
        setProgress(`Pr\xFCfe ${i + 1}/${n} \u2026`);
        try {
          const res = await authedFetch(`${endpoint(c)}/plan`, { signal });
          if (!res.ok) throw new Error(await errorText(res));
          const plan = await res.json();
          if (plan.ok) ready.push({ candidate: c, plan });
          else skipped.push({ name: c.name, reason: plan.reason });
        } catch (err) {
          if (err instanceof DOMException && err.name === "AbortError") return;
          skipped.push({ name: c.name, reason: err instanceof Error ? err.message : String(err) });
        }
      }
      if (ready.length > 0) {
        const databases = ready.filter((r) => r.plan.risk === "high" || r.plan.warnings.some(isDatabaseWarning)).map((r) => r.candidate.name);
        const parts = [
          `F\xFCr ${ready.length} Container auf \u201E${hostName}\u201C Update-Vorschl\xE4ge anlegen: ${ready.map((r) => r.candidate.name).join(", ")}.`,
          "Es wird noch nichts eingespielt \u2013 freigegeben wird gesammelt unter \u201EAktionen\u201C; beim Einspielen sind die Container kurz nicht erreichbar."
        ];
        if (databases.length > 0) {
          parts.push(`Achtung Datenbank (${databases.join(", ")}): ein neues Image kann die Datenbank-Dateien auf eine neue Version umstellen \u2013 vorher ein Backup machen.`);
        }
        if (skipped.length > 0) parts.push(`\xDCbersprungen: ${skipped.map((s) => `${s.name} (${s.reason})`).join("; ")}.`);
        const ok = await deck().confirmDialog(parts.join(" "), { confirmLabel: "Vorschl\xE4ge anlegen", danger: databases.length > 0 });
        if (!ok || signal.aborted) return;
      }
      let created = 0;
      for (const [i, { candidate, plan }] of ready.entries()) {
        if (signal.aborted) return;
        setProgress(`Lege Vorschlag ${i + 1}/${ready.length} an \u2026`);
        try {
          const res = await authedFetch(endpoint(candidate), { method: "POST", body: JSON.stringify({ plan_id: plan.plan_id }), signal });
          const body = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(errorFromBody(body, res.status));
          created += 1;
        } catch (err) {
          if (err instanceof DOMException && err.name === "AbortError") return;
          skipped.push({ name: candidate.name, reason: err instanceof Error ? err.message : String(err) });
        }
      }
      setResult({ created, skipped });
      onDone();
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setProgress(null);
    }
  }
  if (n < 2 && !result && !error && !progress) return null;
  return /* @__PURE__ */ jsxs3(Fragment2, { children: [
    (n >= 2 || progress) && /* @__PURE__ */ jsx4(
      "button",
      {
        type: "button",
        disabled: progress !== null,
        onClick: () => void run(),
        title: "Legt f\xFCr jeden Container mit neuer Version einen Vorschlag an. Es wird nichts eingespielt, bevor ein Admin unter \u201EAktionen\u201C freigibt.",
        className: BUTTON,
        children: progress ?? `Alle Updates vorschlagen (${n})`
      }
    ),
    (result || error) && /* @__PURE__ */ jsxs3("p", { className: "basis-full text-xs", role: "status", "data-testid": `bulk-result-${hostId}`, children: [
      error && /* @__PURE__ */ jsxs3("span", { className: "text-red-400", children: [
        "Fehler: ",
        error
      ] }),
      result && (result.created > 0 ? /* @__PURE__ */ jsxs3("span", { className: "text-emerald-300", children: [
        result.created === 1 ? "1 Vorschlag" : `${result.created} Vorschl\xE4ge`,
        " angelegt \u2013 freigeben unter \u201E",
        /* @__PURE__ */ jsx4("a", { href: ACTIONS_PATH, className: "underline", children: "Aktionen" }),
        "\u201C"
      ] }) : /* @__PURE__ */ jsx4("span", { className: "text-amber-300", children: "Keine Vorschl\xE4ge angelegt." })),
      result && result.skipped.length > 0 && /* @__PURE__ */ jsxs3("span", { className: "block break-words opacity-80", children: [
        "\xDCbersprungen: ",
        result.skipped.map((s) => `${s.name} (${s.reason})`).join("; ")
      ] })
    ] })
  ] });
}

// src/hostAction.ts
async function hostAction(hostId, actionType, reason, signal, payload = {}) {
  const { action, approved } = await runAction(
    `/hosts/${hostId}/actions/${actionType}`,
    { method: "POST", body: JSON.stringify({ payload, reason }) },
    { signal }
  );
  const status2 = action.status ?? "?";
  if (!approved) {
    if (status2 === "proposed") return { text: `vorgeschlagen \u2013 Freigabe durch einen Admin n\xF6tig, siehe "Aktionen".`, waiting: true };
    return { text: action.result?.output ?? ACTION_STATUS_LABEL[status2] ?? status2, waiting: false };
  }
  if (status2 === "succeeded") return { text: action.result?.output ?? "erledigt", waiting: false };
  return { text: `${ACTION_STATUS_LABEL[status2] ?? status2}${action.result?.error ? ` (${action.result.error})` : ""}`, waiting: false };
}
async function runHostAction(hostId, actionType, reason, signal, payload = {}) {
  return (await hostAction(hostId, actionType, reason, signal, payload)).text;
}

// src/ImageUpdateApply.tsx
import { useEffect as useEffect4, useState as useState4 } from "react";
import { Fragment as Fragment3, jsx as jsx5, jsxs as jsxs4 } from "react/jsx-runtime";
var PHASE_TEXT = {
  start: "startet \u2026",
  pull: "l\xE4dt Image \u2026",
  up: "erstellt Container neu \u2026",
  verify: "pr\xFCft Start \u2026",
  rollback: "neue Version startet nicht \u2013 zur\xFCck zur alten \u2026"
};
var DOWNTIME_TEXT = "Der Container wird neu erstellt und ist dabei kurz nicht erreichbar (meist unter einer Minute; das Herunterladen vorher kann dauern).";
function timeOf(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
var BUTTON2 = "px-2 py-1 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition disabled:opacity-40";
function ImageUpdateAction({
  entryId,
  offer,
  apply,
  applying,
  applied,
  open,
  onToggle
}) {
  const parts = [];
  if (applying) {
    parts.push(/* @__PURE__ */ jsxs4("span", { className: "block text-amber-300", role: "status", children: [
      "Update l\xE4uft \u2013 ",
      PHASE_TEXT[applying.phase] ?? PHASE_TEXT.start
    ] }, "run"));
  } else {
    if (applied) {
      const at = timeOf(applied.finished_at);
      parts.push(
        applied.ok ? /* @__PURE__ */ jsxs4("span", { className: "block text-emerald-300", title: applied.summary, children: [
          "\u2713 eingespielt ",
          at
        ] }, "done") : /* @__PURE__ */ jsxs4("span", { className: "block text-red-300", title: applied.summary, children: [
          "Update fehlgeschlagen ",
          at
        ] }, "failed")
      );
    }
    if (offer && apply?.mode === "none") {
      parts.push(/* @__PURE__ */ jsx5("span", { className: "mt-0.5 block max-w-[15rem] whitespace-normal break-words text-[11px] opacity-60", title: apply.why ?? void 0, children: apply.why }, "why"));
    } else if (offer) {
      parts.push(
        /* @__PURE__ */ jsx5(
          "button",
          {
            type: "button",
            "aria-expanded": open,
            onClick: onToggle,
            title: "Neues Image laden und Container neu erstellen \u2013 vorher kommt eine \xDCbersicht",
            className: `mt-1 ${BUTTON2} ${open ? "bg-white/20" : ""}`,
            children: "Einspielen"
          },
          "btn"
        )
      );
    }
  }
  if (parts.length === 0) return null;
  return /* @__PURE__ */ jsx5("div", { className: "mt-1", "data-testid": `image-action-${entryId}`, children: parts });
}
function UpdatePanel({
  hostId,
  container,
  applying,
  onStarted,
  onFinished,
  onClose
}) {
  const unmountSignal = useUnmountSignal();
  const [plan, setPlan] = useState4(null);
  const [error, setError] = useState4(null);
  const [run, setRun] = useState4({ state: "idle" });
  const [reload, setReload] = useState4(0);
  useEffect4(() => {
    if (run.state !== "posting") return;
    const interval = setInterval(onStarted, 2e3);
    return () => clearInterval(interval);
  }, [run.state, onStarted]);
  const base = `/ext/docker/containers/${hostId}/${encodeURIComponent(container)}/image-update`;
  useEffect4(() => {
    let cancelled = false;
    setPlan(null);
    setError(null);
    setRun({ state: "idle" });
    authedFetch(`${base}/plan`).then(async (res) => {
      if (!res.ok) throw new Error(await errorText(res));
      return res.json();
    }).then((body) => {
      if (!cancelled) setPlan(body);
    }).catch((err) => {
      if (!cancelled) setError(err instanceof Error ? err.message : String(err));
    });
    return () => {
      cancelled = true;
    };
  }, [base, reload]);
  async function start(p) {
    setRun({ state: "posting" });
    try {
      const res = await authedFetch(base, { method: "POST", body: JSON.stringify({ plan_id: p.plan_id }), signal: unmountSignal() });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setRun({ state: "error", message: errorFromBody(body, res.status), conflict: res.status === 409 });
        return;
      }
      onStarted();
      let action = body;
      let approvedHere = false;
      const id = action.id ?? action.action_id;
      if (action.status === "proposed" && id && deck().hasPermission(`actions.approve:${action.risk ?? p.risk}`)) {
        const approveRes = await authedFetch(`/actions/${id}/approve`, { method: "POST", signal: unmountSignal() });
        const approveBody = await approveRes.json().catch(() => ({}));
        if (!approveRes.ok) throw new Error(errorFromBody(approveBody, approveRes.status));
        action = { ...action, ...approveBody };
        approvedHere = true;
        onStarted();
      }
      const outcome = await settleAction(action, { risk: p.risk, approve: false, signal: unmountSignal() });
      const status2 = outcome.action.status;
      if (!approvedHere && status2 === "proposed") {
        setRun({ state: "proposed" });
        return;
      }
      if (status2 === "succeeded") {
        const output = outcome.action.result?.output ?? null;
        setRun({ state: "done", ok: true, text: `Fertig: ${(output ?? "").split("\n")[0] || "eingespielt"}`, output });
      } else if (status2 === "failed") {
        setRun({ state: "done", ok: false, text: `Fehlgeschlagen: ${outcome.action.result?.error ?? "unbekannter Fehler"}`, output: outcome.action.result?.output ?? null });
      } else {
        setRun({ state: "done", ok: outcome.tone !== "error", text: outcome.text, output: null });
      }
      onFinished();
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setRun({ state: "error", message: err instanceof Error ? err.message : String(err), conflict: false });
    }
  }
  return /* @__PURE__ */ jsxs4("div", { className: "sticky left-0 max-w-[calc(100vw-3rem)] text-xs sm:max-w-none", "data-testid": `update-panel-${hostId}:${container}`, children: [
    error && /* @__PURE__ */ jsxs4("p", { className: "text-red-400", children: [
      "\xDCbersicht nicht abrufbar: ",
      error
    ] }),
    !plan && !error && /* @__PURE__ */ jsx5("p", { className: "opacity-60", children: "Lade \xDCbersicht \u2026" }),
    plan && !plan.ok && /* @__PURE__ */ jsxs4("div", { children: [
      /* @__PURE__ */ jsx5("p", { className: "opacity-90", children: plan.reason }),
      /* @__PURE__ */ jsx5("button", { type: "button", onClick: onClose, className: `mt-2 ${BUTTON2}`, children: "Schlie\xDFen" })
    ] }),
    plan?.ok && /* @__PURE__ */ jsxs4("div", { className: "flex flex-col gap-2", children: [
      /* @__PURE__ */ jsxs4("p", { className: "text-sm font-medium", children: [
        "Update f\xFCr \u201E",
        plan.container,
        "\u201C einspielen"
      ] }),
      /* @__PURE__ */ jsxs4("dl", { className: "grid gap-x-6 gap-y-1 opacity-85 sm:grid-cols-2", children: [
        /* @__PURE__ */ jsxs4("div", { children: [
          /* @__PURE__ */ jsx5("dt", { className: "opacity-60", children: "Image" }),
          /* @__PURE__ */ jsx5("dd", { className: "break-words", children: plan.image })
        ] }),
        /* @__PURE__ */ jsxs4("div", { children: [
          /* @__PURE__ */ jsx5("dt", { className: "opacity-60", children: "Version" }),
          /* @__PURE__ */ jsxs4("dd", { children: [
            "Image-ID ",
            plan.current_short,
            " \xB7 neue Version bei der Registry (Pr\xFCfsumme ",
            plan.remote_short || "?",
            ")",
            plan.registry_at ? `, Stand ${new Date(plan.registry_at).toLocaleString()}` : ""
          ] })
        ] }),
        /* @__PURE__ */ jsxs4("div", { children: [
          /* @__PURE__ */ jsx5("dt", { className: "opacity-60", children: "Compose" }),
          /* @__PURE__ */ jsxs4("dd", { className: "break-words", children: [
            plan.project,
            "/",
            plan.service
          ] })
        ] }),
        plan.affected.length > 1 && /* @__PURE__ */ jsxs4("div", { children: [
          /* @__PURE__ */ jsx5("dt", { className: "opacity-60", children: "Betrifft" }),
          /* @__PURE__ */ jsx5("dd", { className: "break-words", children: plan.affected.join(", ") })
        ] })
      ] }),
      /* @__PURE__ */ jsxs4("div", { className: "rounded border border-amber-500/40 bg-amber-500/10 p-2 text-amber-200", children: [
        /* @__PURE__ */ jsx5("p", { children: DOWNTIME_TEXT }),
        plan.auto_rollback && /* @__PURE__ */ jsx5("p", { className: "mt-1", children: "St\xFCrzt die neue Version beim Start ab oder l\xE4sst sich der Container nicht erstellen, geht Nodvard Deck in der Regel automatisch auf die alte zur\xFCck." }),
        plan.warnings.length > 0 && /* @__PURE__ */ jsx5("ul", { className: "mt-1 list-disc space-y-0.5 pl-4", children: plan.warnings.map((w) => /* @__PURE__ */ jsx5("li", { className: w.startsWith("Datenbank") ? "font-medium text-red-300" : void 0, children: w }, w)) })
      ] }),
      /* @__PURE__ */ jsxs4("details", { open: true, children: [
        /* @__PURE__ */ jsx5("summary", { className: "cursor-pointer opacity-70", children: "Befehl auf dem Server" }),
        /* @__PURE__ */ jsx5("pre", { className: "mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-black/60 p-2 font-mono text-[11px]", children: plan.command })
      ] }),
      /* @__PURE__ */ jsxs4("details", { children: [
        /* @__PURE__ */ jsx5("summary", { className: "cursor-pointer opacity-70", children: "Zur\xFCck zur alten Version" }),
        /* @__PURE__ */ jsx5("pre", { className: "mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-black/60 p-2 font-mono text-[11px]", children: plan.rollback })
      ] }),
      run.state === "idle" && /* @__PURE__ */ jsxs4("div", { className: "flex flex-col gap-2 sm:flex-row sm:items-center", children: [
        deck().hasPermission(`actions.approve:${plan.risk}`) ? /* @__PURE__ */ jsx5(
          "button",
          {
            type: "button",
            onClick: () => void start(plan),
            className: `rounded px-3 py-1.5 text-xs font-medium ${plan.risk === "high" ? "bg-red-500/80 hover:bg-red-500" : "bg-[var(--color-accent)] hover:opacity-90"}`,
            children: "Jetzt einspielen"
          }
        ) : /* @__PURE__ */ jsxs4(Fragment3, { children: [
          /* @__PURE__ */ jsx5("button", { type: "button", onClick: () => void start(plan), className: BUTTON2, children: "Vorschlagen" }),
          /* @__PURE__ */ jsx5("span", { className: "opacity-70", children: "Ein Admin muss unter \u201EAktionen\u201C freigeben." })
        ] }),
        /* @__PURE__ */ jsx5("button", { type: "button", onClick: onClose, className: BUTTON2, children: "Abbrechen" })
      ] }),
      run.state === "posting" && /* @__PURE__ */ jsxs4("p", { className: "text-amber-300", role: "status", children: [
        "L\xE4uft \u2026 ",
        applying ? PHASE_TEXT[applying.phase] : "wird gestartet \u2026"
      ] }),
      run.state === "proposed" && /* @__PURE__ */ jsx5("p", { role: "status", children: "Vorgeschlagen \u2013 Freigabe durch einen Admin n\xF6tig, siehe \u201EAktionen\u201C." }),
      run.state === "done" && /* @__PURE__ */ jsxs4("div", { role: "status", children: [
        /* @__PURE__ */ jsx5("p", { className: run.ok ? "text-emerald-300" : "text-red-300", children: run.text }),
        run.output && (run.ok ? /* @__PURE__ */ jsxs4("details", { className: "mt-1", children: [
          /* @__PURE__ */ jsx5("summary", { className: "cursor-pointer opacity-70", children: "Protokoll und R\xFCckweg" }),
          /* @__PURE__ */ jsx5("pre", { className: "mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-black/60 p-2 font-mono text-[11px]", children: run.output })
        ] }) : /* @__PURE__ */ jsx5("pre", { className: "mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-black/60 p-2 font-mono text-[11px]", children: run.output }))
      ] }),
      run.state === "error" && /* @__PURE__ */ jsxs4("div", { role: "alert", children: [
        /* @__PURE__ */ jsx5("p", { className: "text-red-300", children: run.message }),
        run.conflict && /* @__PURE__ */ jsx5("button", { type: "button", onClick: () => setReload((n) => n + 1), className: `mt-2 ${BUTTON2}`, children: "Neu laden" })
      ] }),
      (run.state === "proposed" || run.state === "done" || run.state === "error") && /* @__PURE__ */ jsx5("div", { children: /* @__PURE__ */ jsx5("button", { type: "button", onClick: onClose, className: BUTTON2, children: "Schlie\xDFen" }) })
    ] })
  ] });
}

// src/Logs.tsx
import { Fragment as Fragment4, useEffect as useEffect5, useMemo as useMemo2, useRef as useRef2, useState as useState5 } from "react";
import { Fragment as Fragment5, jsx as jsx6, jsxs as jsxs5 } from "react/jsx-runtime";
var MAX_LINES = 5e3;
var LOG_RANGES = [
  { value: "100", label: "Letzte 100 Zeilen", tail: 100 },
  { value: "200", label: "Letzte 200 Zeilen", tail: 200 },
  { value: "500", label: "Letzte 500 Zeilen", tail: 500 },
  { value: "2000", label: "Letzte 2000 Zeilen", tail: 2e3 },
  { value: "10m", label: "Letzte 10 Minuten", tail: MAX_LINES, since: "10m" },
  { value: "1h", label: "Letzte Stunde", tail: MAX_LINES, since: "1h" },
  { value: "4h", label: "Letzte 4 Stunden", tail: MAX_LINES, since: "4h" },
  { value: "24h", label: "Letzte 24 Stunden", tail: MAX_LINES, since: "24h" },
  { value: "168h", label: "Letzte 7 Tage", tail: MAX_LINES, since: "168h" }
];
var ANSI_RE = /\x1b\[[0-9;?]*[ -/]*[@-~]/g;
var SMALL_BUTTON = "px-2 py-0.5 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition disabled:opacity-40";
var FIELD = "px-1 py-0.5 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]";
function searchPattern(text) {
  return text.trim() ? new RegExp(`(${text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "i") : null;
}
function Marked({ line, split }) {
  const parts = line.split(split);
  return /* @__PURE__ */ jsx6(Fragment5, { children: parts.map((part, i) => i % 2 === 1 ? /* @__PURE__ */ jsx6("mark", { className: "rounded-sm bg-amber-400/40 text-inherit", children: part }, i) : part) });
}
function logFileName(container, now = /* @__PURE__ */ new Date()) {
  const pad = (n) => String(n).padStart(2, "0");
  const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}`;
  return `${container}-logs-${stamp}.txt`;
}
function download(name, lines) {
  const url = URL.createObjectURL(new Blob([`${lines.join("\n")}
`], { type: "text/plain;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1e3);
}
function LogPanel({ entry, onClose }) {
  const [lines, setLines] = useState5([]);
  const [range, setRange] = useState5("200");
  const [running, setRunning] = useState5(true);
  const [follow, setFollow] = useState5(true);
  const [search, setSearch] = useState5("");
  const [error, setError] = useState5(null);
  const [ended, setEnded] = useState5(false);
  const boxRef = useRef2(null);
  const chosen = LOG_RANGES.find((r) => r.value === range) ?? LOG_RANGES[1];
  useEffect5(() => {
    if (!running || !entry.host_id || !entry.container) return;
    const controller = new AbortController();
    setLines([]);
    setError(null);
    setEnded(false);
    (async () => {
      try {
        const params = new URLSearchParams({ tail: String(chosen.tail) });
        if (chosen.since) params.set("since", chosen.since);
        const res = await authedFetch(
          `/ext/docker/containers/${entry.host_id}/${encodeURIComponent(entry.container)}/logs?${params}`,
          { signal: controller.signal }
        );
        if (!res.ok || !res.body) throw new Error(await errorText(res));
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let rest = "";
        for (; ; ) {
          const { value, done } = await reader.read();
          if (done) break;
          rest += decoder.decode(value, { stream: true });
          const parts = rest.split("\n");
          rest = parts.pop() ?? "";
          if (parts.length > 0) {
            const clean = parts.map((l) => l.replace(ANSI_RE, ""));
            setLines((prev) => {
              const next = prev.concat(clean);
              return next.length > MAX_LINES ? next.slice(next.length - MAX_LINES) : next;
            });
          }
        }
        if (rest) setLines((prev) => prev.concat(rest.replace(ANSI_RE, "")));
        setEnded(true);
      } catch (err) {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => controller.abort();
  }, [entry.host_id, entry.container, chosen, running]);
  useEffect5(() => {
    if (follow && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight;
  }, [lines, follow]);
  const pattern = useMemo2(() => searchPattern(search), [search]);
  const split = useMemo2(() => pattern ? new RegExp(pattern.source, "gi") : null, [pattern]);
  const shown2 = useMemo2(() => pattern ? lines.filter((l) => pattern.test(l)) : lines, [lines, pattern]);
  return /* @__PURE__ */ jsxs5("div", { className: "flex flex-col gap-2", "data-testid": "log-panel", children: [
    /* @__PURE__ */ jsxs5("div", { className: "flex flex-wrap items-center gap-2 text-xs", children: [
      /* @__PURE__ */ jsxs5("span", { className: "font-medium", children: [
        "Logs: ",
        entry.name
      ] }),
      /* @__PURE__ */ jsx6("span", { className: `rounded-full px-2 py-0.5 ${running && !ended && !error ? "bg-emerald-500/20 text-emerald-300" : "bg-white/10 opacity-70"}`, children: error ? "Fehler" : !running ? "angehalten" : ended ? "beendet" : "live" }),
      /* @__PURE__ */ jsx6("select", { value: range, onChange: (e) => setRange(e.target.value), "aria-label": "Zeitraum", className: FIELD, children: LOG_RANGES.map((r) => /* @__PURE__ */ jsx6("option", { value: r.value, children: r.label }, r.value)) }),
      /* @__PURE__ */ jsx6(
        "input",
        {
          value: search,
          onChange: (e) => setSearch(e.target.value),
          placeholder: "Suchen \u2026",
          "aria-label": "In den Logs suchen",
          className: `w-40 ${FIELD} px-2`
        }
      ),
      pattern && /* @__PURE__ */ jsxs5("span", { "data-testid": "log-hits", className: "opacity-70", children: [
        shown2.length,
        " von ",
        lines.length,
        " Zeilen"
      ] }),
      /* @__PURE__ */ jsxs5("label", { className: "flex items-center gap-1", children: [
        /* @__PURE__ */ jsx6("input", { type: "checkbox", checked: follow, onChange: (e) => setFollow(e.target.checked) }),
        "Mitlaufen"
      ] }),
      /* @__PURE__ */ jsxs5("div", { className: "ml-auto flex flex-wrap gap-1.5", children: [
        /* @__PURE__ */ jsx6("button", { type: "button", onClick: () => setRunning((r) => !r), className: SMALL_BUTTON, children: running ? "Anhalten" : "Fortsetzen" }),
        /* @__PURE__ */ jsx6("button", { type: "button", onClick: () => setLines([]), className: SMALL_BUTTON, children: "Leeren" }),
        /* @__PURE__ */ jsx6(
          "button",
          {
            type: "button",
            disabled: shown2.length === 0,
            onClick: () => download(logFileName(entry.container ?? entry.name), shown2),
            title: "Speichert die Zeilen als Textdatei auf deinem Ger\xE4t, mit Uhrzeit vor jeder Zeile.",
            className: SMALL_BUTTON,
            children: pattern ? "Treffer herunterladen" : "Herunterladen"
          }
        ),
        /* @__PURE__ */ jsx6("button", { type: "button", onClick: onClose, className: SMALL_BUTTON, children: "Schlie\xDFen" })
      ] })
    ] }),
    error && /* @__PURE__ */ jsx6("p", { className: "text-xs text-red-400", children: error }),
    lines.length >= MAX_LINES && /* @__PURE__ */ jsxs5("p", { className: "text-xs opacity-70", "data-testid": "log-cut", children: [
      "Gezeigt werden nur die letzten ",
      MAX_LINES,
      " Zeilen. \xC4ltere stehen weiter im Log des Containers, hier aber nicht."
    ] }),
    /* @__PURE__ */ jsx6(
      "pre",
      {
        ref: boxRef,
        className: "h-72 overflow-auto whitespace-pre-wrap break-all rounded bg-black/60 p-2 font-mono text-[11px] leading-snug",
        children: shown2.length === 0 ? pattern ? "Keine passenden Zeilen unter den geladenen." : chosen.since && ended ? "In diesem Zeitraum gibt es keine Ausgabe." : "Noch keine Ausgabe \u2026" : split ? shown2.map((line, i) => /* @__PURE__ */ jsxs5(Fragment4, { children: [
          i > 0 && "\n",
          /* @__PURE__ */ jsx6(Marked, { line, split })
        ] }, i)) : shown2.join("\n")
      }
    )
  ] });
}

// src/operate.tsx
import { jsx as jsx7, jsxs as jsxs6 } from "react/jsx-runtime";
var OPERATE_CAN = "Nodvard Deck darf dort Container ansehen, ihre Logs lesen und sie starten, stoppen und neu starten, aber nicht mehr.";
function operateUpgrade(fullTag) {
  return `gib dem Server unter Einstellungen, Server & Zug\xE4nge die Markierung \u201E${fullTag}\u201C: Dann gilt er als voller Docker-Server, und die Einrichtung nimmt den Zugang in die Gruppe docker auf, die praktisch Root-Rechte gibt.`;
}
var OPERATE_NO_IMAGE_CHECK = "Auf Servern im Modus \u201ENur bedienen\u201C pr\xFCft Nodvard Deck nicht auf neue Images.";
function OperateBadge({ hostId }) {
  const fullTag = useSettingText("docker", "docker_host_tag", "docker");
  return /* @__PURE__ */ jsx7(
    "span",
    {
      className: "rounded bg-white/10 px-1.5 py-0.5 text-[11px] opacity-80",
      "data-testid": `operate-${hostId}`,
      title: `${OPERATE_CAN} F\xFCr alles andere ${operateUpgrade(fullTag)}`,
      children: "Nur bedienen"
    }
  );
}
function OperateNote({ hostId, name }) {
  const fullTag = useSettingText("docker", "docker_host_tag", "docker");
  return /* @__PURE__ */ jsxs6("p", { className: "text-sm opacity-80", "data-testid": `operate-${hostId}`, children: [
    "Auf ",
    name,
    " ist Docker im Modus \u201ENur bedienen\u201C eingerichtet: ",
    OPERATE_CAN,
    " Stacks, Images, Volumes, Netze und Registries gibt es dort nicht. Daf\xFCr ",
    operateUpgrade(fullTag)
  ] });
}

// src/Registries.tsx
import { useEffect as useEffect8, useState as useState9 } from "react";

// src/Resources.tsx
import { useEffect as useEffect6, useState as useState6 } from "react";
import { Fragment as Fragment6, jsx as jsx8, jsxs as jsxs7 } from "react/jsx-runtime";
var RESOURCE_TABS = [
  { kind: "images", label: "Images" },
  { kind: "volumes", label: "Volumes" },
  { kind: "networks", label: "Netze" }
];
var TEXT = {
  images: {
    what: "Die Liste der Images",
    noun: "Images",
    action: "docker.image_remove",
    label: "Image l\xF6schen",
    empty: "Auf diesem Server liegen keine Images.",
    hint: "Gel\xF6scht werden kann nur ein Image, das kein Container nutzt, auch kein gestoppter.",
    confirm: (title) => `Image \u201E${title}\u201C l\xF6schen? Braucht es sp\xE4ter ein Container, l\xE4dt Docker es neu herunter; ein selbst gebautes muss neu gebaut werden.`
  },
  volumes: {
    what: "Die Liste der Volumes",
    noun: "Volumes",
    action: "docker.volume_remove",
    label: "Volume l\xF6schen",
    empty: "Auf diesem Server gibt es keine Volumes.",
    hint: "Volumes enthalten die Daten deiner Container. Gel\xF6scht werden kann nur, was kein Container mehr nutzt.",
    confirm: (title) => `Volume \u201E${title}\u201C l\xF6schen? Die Daten darin sind dann weg, das l\xE4sst sich nicht r\xFCckg\xE4ngig machen.`
  },
  networks: {
    what: "Die Liste der Netze",
    noun: "Netze",
    action: "docker.network_remove",
    label: "Netz l\xF6schen",
    empty: "Auf diesem Server gibt es keine Netze.",
    hint: "Die eingebauten Netze von Docker bleiben immer. Ein Netz, an dem noch ein Container h\xE4ngt, auch ein gestoppter, bleibt ebenfalls.",
    confirm: (title) => `Netz \u201E${title}\u201C l\xF6schen? Ein Compose-Projekt legt es beim n\xE4chsten Start wieder an.`
  }
};
var size = (bytes) => bytes == null ? "Gr\xF6\xDFe unbekannt" : formatBytes(bytes);
function day(text) {
  const [y, m, d] = text.slice(0, 10).split("-").map(Number);
  return y && m && d ? `${d}.${m}.${y}` : text.slice(0, 10);
}
function imageRow(i) {
  return {
    key: i.id,
    title: i.names.length ? i.names.join(", ") : `ohne Namen (${i.short_id})`,
    muted: i.dangling,
    facts: [size(i.size_bytes), ...i.created_at ? [`erstellt am ${day(i.created_at)}`] : []],
    builtin: false,
    usedBy: i.used_by,
    inUse: i.in_use,
    removable: i.removable,
    payload: { image: i.id },
    kept: !i.in_use && !i.removable ? keptImage(i) : void 0
  };
}
function keptImage(i) {
  if (i.rollback) {
    return "Bleibt als R\xFCckweg f\xFCr Nodvard Deck: Ohne dieses Image gibt es nach einem misslungenen Update kein Zur\xFCck.";
  }
  if (i.names.length > 1) {
    return `Hat mehrere Namen; Docker l\xF6scht es erst, wenn nur noch einer \xFCbrig ist. Einen Namen entfernst du im Terminal mit: docker rmi ${i.names[0]}`;
  }
  return `Docker nennt eine ungewohnte Kennung, deshalb gibt es hier keinen L\xF6schen-Knopf. L\xF6schen kannst du es im Terminal mit: docker rmi ${i.short_id}`;
}
function volumeRow(v) {
  return {
    key: v.name,
    title: v.anonymous ? `anonym (${v.name.slice(0, 12)})` : v.name,
    muted: v.anonymous,
    facts: [size(v.size_bytes), ...v.compose_project ? [`Stack ${v.compose_project}`] : [], ...v.driver && v.driver !== "local" ? [v.driver] : []],
    builtin: false,
    usedBy: v.used_by,
    inUse: v.in_use,
    removable: v.removable,
    payload: { volume: v.name }
  };
}
function networkRow(n) {
  return {
    key: n.id,
    title: n.name,
    muted: n.builtin,
    facts: [n.driver ?? "?", ...n.compose_project ? [`Stack ${n.compose_project}`] : [], ...n.internal ? ["nur intern"] : []],
    builtin: n.builtin,
    usedBy: n.used_by,
    inUse: n.in_use || n.builtin,
    removable: n.removable,
    payload: { network: n.id }
  };
}
function usage(row) {
  if (row.usedBy.length) return `benutzt von ${row.usedBy.join(", ")}`;
  if (row.inUse) return row.builtin ? "" : "von einem Container benutzt";
  return /* @__PURE__ */ jsxs7(Fragment6, { children: [
    /* @__PURE__ */ jsx8("span", { className: "text-amber-300", children: "von keinem Container benutzt" }),
    row.kept && /* @__PURE__ */ jsx8("span", { className: "block opacity-70", children: row.kept })
  ] });
}
function toRows(kind, body) {
  if (kind === "images") return (body.images ?? []).map(imageRow);
  if (kind === "volumes") return (body.volumes ?? []).map(volumeRow);
  return (body.networks ?? []).map(networkRow);
}
function ResourceList({ hostId, kind }) {
  const unmountSignal = useUnmountSignal();
  const [rows, setRows] = useState6(null);
  const [error, setError] = useState6(null);
  const [busy, setBusy] = useState6(null);
  const [message, setMessage] = useState6(null);
  const [waiting, setWaiting] = useState6(/* @__PURE__ */ new Set());
  const [reload, setReload] = useState6(0);
  const text = TEXT[kind];
  useEffect6(() => {
    const controller = new AbortController();
    setRows(null);
    setError(null);
    fetchJson(`/ext/docker/hosts/${encodeURIComponent(hostId)}/${kind}`, { signal: controller.signal }).then((body) => {
      if (controller.signal.aborted) return;
      if (kind === "networks" && typeof body.error === "string" && body.error) setError(body.error);
      else setRows(toRows(kind, body));
    }).catch((err) => {
      if (!controller.signal.aborted) setError(errorMessage(err));
    });
    return () => controller.abort();
  }, [hostId, kind, reload]);
  async function remove(row) {
    const ok = await deck().confirmDialog(text.confirm(row.title), { title: text.label, danger: true, confirmLabel: "L\xF6schen" });
    if (!ok) return;
    setBusy(row.key);
    setMessage(null);
    try {
      const result = await hostAction(hostId, text.action, `${text.label} \xFCber die Docker-Seite.`, unmountSignal(), row.payload);
      setMessage(`${text.label}: ${result.text}`);
      if (result.waiting) setWaiting((keys) => new Set(keys).add(row.key));
      setReload((n) => n + 1);
    } catch (err) {
      setMessage(`Fehler: ${errorMessage(err)}`);
    } finally {
      setBusy(null);
    }
  }
  if (error) return /* @__PURE__ */ jsx8(LoadError, { what: text.what, error, onRetry: () => setReload((n) => n + 1) });
  if (!rows) return /* @__PURE__ */ jsxs7("p", { className: "text-sm opacity-60", children: [
    "Lade die ",
    text.noun,
    " \u2026 Das dauert ein paar Sekunden."
  ] });
  const canRemove = deck().hasPermission("hosts.execute");
  return /* @__PURE__ */ jsxs7("div", { "data-testid": `resources-${kind}`, children: [
    /* @__PURE__ */ jsx8("p", { className: "mb-2 text-xs opacity-60", children: text.hint }),
    message && /* @__PURE__ */ jsx8("p", { className: "mb-2 text-sm opacity-90", role: "status", children: message }),
    rows.length === 0 && /* @__PURE__ */ jsx8("p", { className: "text-sm opacity-70", children: text.empty }),
    /* @__PURE__ */ jsx8("ul", { className: "divide-y divide-white/5 text-sm", children: rows.map((row) => /* @__PURE__ */ jsxs7("li", { className: "flex flex-wrap items-center gap-x-3 gap-y-1 py-2", "data-testid": `resource-${row.key}`, children: [
      /* @__PURE__ */ jsxs7("div", { className: "min-w-0 flex-1", children: [
        /* @__PURE__ */ jsxs7("p", { className: `break-words ${row.muted ? "opacity-70" : ""}`, children: [
          row.title,
          " ",
          row.builtin && /* @__PURE__ */ jsx8(Badge, { children: "eingebaut" })
        ] }),
        /* @__PURE__ */ jsx8("p", { className: "text-xs opacity-60", children: row.facts.join(" \xB7 ") })
      ] }),
      /* @__PURE__ */ jsx8("p", { className: "text-xs opacity-80 md:w-64", children: usage(row) }),
      canRemove && /* @__PURE__ */ jsxs7("div", { className: "w-20 shrink-0 text-right", children: [
        row.removable && waiting.has(row.key) && /* @__PURE__ */ jsx8("span", { className: "text-xs opacity-70", children: "wartet auf Freigabe" }),
        row.removable && !waiting.has(row.key) && /* @__PURE__ */ jsx8(Button, { small: true, variant: "danger", disabled: busy !== null, onClick: () => void remove(row), ariaLabel: `${text.label}: ${row.title}`, children: busy === row.key ? "\u2026" : "L\xF6schen" })
      ] })
    ] }, row.key)) })
  ] });
}
function ResourcesSection({ kind, hostId, onHost, empty }) {
  return /* @__PURE__ */ jsx8(HostSection, { hostId, onHost, empty, children: (id) => /* @__PURE__ */ jsx8(ResourceList, { hostId: id, kind }, `${id}:${kind}`) });
}
function HostSection({ hostId, onHost, empty, children }) {
  const [hosts, setHosts] = useState6(null);
  const [error, setError] = useState6(null);
  const [reload, setReload] = useState6(0);
  useEffect6(() => {
    const controller = new AbortController();
    setError(null);
    fetchJson("/ext/docker/hosts", { signal: controller.signal }).then((body) => {
      if (!controller.signal.aborted) setHosts(body.hosts ?? []);
    }).catch((err) => {
      if (!controller.signal.aborted) setError(errorMessage(err));
    });
    return () => controller.abort();
  }, [reload]);
  if (error) return /* @__PURE__ */ jsx8(LoadError, { what: "Die Liste der Docker-Server", error, onRetry: () => setReload((n) => n + 1) });
  if (!hosts) return /* @__PURE__ */ jsx8("p", { className: "text-sm opacity-60", children: "Lade die Docker-Server \u2026" });
  const current = hosts.find((h) => h.id === hostId) ?? hosts[0];
  if (!current) return /* @__PURE__ */ jsx8(Fragment6, { children: empty });
  return /* @__PURE__ */ jsxs7("section", { children: [
    hosts.length > 1 && /* @__PURE__ */ jsxs7("label", { className: "mb-3 flex items-center gap-2 text-sm", children: [
      "Server",
      /* @__PURE__ */ jsx8(
        "select",
        {
          value: current.id,
          onChange: (e) => onHost(e.target.value),
          className: "rounded-lg border border-white/10 bg-black/25 px-2 py-1 text-sm outline-none focus:border-[var(--color-accent)]",
          children: hosts.map((h) => /* @__PURE__ */ jsxs7("option", { value: h.id, children: [
            h.name,
            h.mode === "operate" ? " (nur bedienen)" : ""
          ] }, h.id))
        }
      )
    ] }),
    current.mode === "operate" ? /* @__PURE__ */ jsx8(OperateNote, { hostId: current.id, name: current.name }) : children(current.id)
  ] });
}

// src/StackEditor.tsx
import { useEffect as useEffect7, useRef as useRef4, useState as useState8 } from "react";

// src/DiffView.tsx
import { useMemo as useMemo3 } from "react";

// src/lineDiff.ts
var MAX_CELLS = 2e6;
function counts(a, b) {
  const left = /* @__PURE__ */ new Map();
  for (const line of a) left.set(line, (left.get(line) ?? 0) + 1);
  let added = 0;
  for (const line of b) {
    const n = left.get(line) ?? 0;
    if (n > 0) left.set(line, n - 1);
    else added += 1;
  }
  return { added, removed: a.length - (b.length - added) };
}
function lineDiff(before, after) {
  const a = before.split("\n");
  const b = after.split("\n");
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start += 1;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA -= 1;
    endB -= 1;
  }
  const midA = a.slice(start, endA);
  const midB = b.slice(start, endB);
  const n = midA.length;
  const m = midB.length;
  if ((n + 1) * (m + 1) > MAX_CELLS) {
    const found = counts(midA, midB);
    const moved = found.added || found.removed ? 0 : midA.filter((line, i2) => line !== midB[i2]).length;
    return { added: found.added || moved, removed: found.removed || moved, lines: null };
  }
  const width = m + 1;
  const table = new Uint32Array((n + 1) * width);
  for (let i2 = n - 1; i2 >= 0; i2 -= 1) {
    for (let j2 = m - 1; j2 >= 0; j2 -= 1) {
      table[i2 * width + j2] = midA[i2] === midB[j2] ? table[(i2 + 1) * width + j2 + 1] + 1 : Math.max(table[(i2 + 1) * width + j2], table[i2 * width + j2 + 1]);
    }
  }
  const lines = a.slice(0, start).map((text) => ({ kind: " ", text }));
  let i = 0;
  let j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && midA[i] === midB[j]) {
      lines.push({ kind: " ", text: midA[i] });
      i += 1;
      j += 1;
    } else if (i < n && (j === m || table[(i + 1) * width + j] >= table[i * width + j + 1])) {
      lines.push({ kind: "-", text: midA[i] });
      i += 1;
    } else {
      lines.push({ kind: "+", text: midB[j] });
      j += 1;
    }
  }
  for (const text of a.slice(endA)) lines.push({ kind: " ", text });
  const added = lines.filter((l) => l.kind === "+").length;
  return { added, removed: lines.filter((l) => l.kind === "-").length, lines };
}
function hunks(lines, context = 2) {
  const keep = new Array(lines.length).fill(false);
  lines.forEach((line, index) => {
    if (line.kind === " ") return;
    for (let k = Math.max(0, index - context); k <= Math.min(lines.length - 1, index + context); k += 1) keep[k] = true;
  });
  const out = [];
  lines.forEach((line, index) => {
    if (keep[index]) out.push(line);
    else if (out.length === 0 || out[out.length - 1] !== null) out.push(null);
  });
  return out;
}

// src/DiffView.tsx
import { jsx as jsx9, jsxs as jsxs8 } from "react/jsx-runtime";
var TONE = { "+": "bg-emerald-500/15 text-emerald-200", "-": "bg-red-500/15 text-red-200", " ": "opacity-70" };
function DiffView({ before, after, open }) {
  const diff = useMemo3(() => lineDiff(before, after), [before, after]);
  if (!diff.added && !diff.removed) return null;
  return /* @__PURE__ */ jsxs8("details", { open, "data-testid": "stack-diff", children: [
    /* @__PURE__ */ jsxs8("summary", { className: "cursor-pointer select-none opacity-80", children: [
      "Unterschied zur Datei auf dem Server: +",
      diff.added,
      " \u2212",
      diff.removed,
      " Zeilen"
    ] }),
    diff.lines ? /* @__PURE__ */ jsx9("pre", { className: "mt-1 max-h-72 overflow-auto rounded bg-black/60 p-2 font-mono text-[11px] leading-snug", children: hunks(diff.lines).map((line, index) => line ? /* @__PURE__ */ jsxs8("div", { className: TONE[line.kind], children: [
      line.kind,
      " ",
      line.text
    ] }, index) : /* @__PURE__ */ jsx9("div", { className: "opacity-40", children: "\u2026" }, index)) }) : /* @__PURE__ */ jsx9("p", { className: "opacity-70", children: "So viele \xC4nderungen lassen sich hier nicht Zeile f\xFCr Zeile zeigen." })
  ] });
}

// src/StackForm.tsx
import { useMemo as useMemo4, useRef as useRef3, useState as useState7 } from "react";

// ../../../frontend/node_modules/yaml/browser/dist/nodes/identity.js
var ALIAS = /* @__PURE__ */ Symbol.for("yaml.alias");
var DOC = /* @__PURE__ */ Symbol.for("yaml.document");
var MAP = /* @__PURE__ */ Symbol.for("yaml.map");
var PAIR = /* @__PURE__ */ Symbol.for("yaml.pair");
var SCALAR = /* @__PURE__ */ Symbol.for("yaml.scalar");
var SEQ = /* @__PURE__ */ Symbol.for("yaml.seq");
var NODE_TYPE = /* @__PURE__ */ Symbol.for("yaml.node.type");
var isAlias = (node) => !!node && typeof node === "object" && node[NODE_TYPE] === ALIAS;
var isDocument = (node) => !!node && typeof node === "object" && node[NODE_TYPE] === DOC;
var isMap = (node) => !!node && typeof node === "object" && node[NODE_TYPE] === MAP;
var isPair = (node) => !!node && typeof node === "object" && node[NODE_TYPE] === PAIR;
var isScalar = (node) => !!node && typeof node === "object" && node[NODE_TYPE] === SCALAR;
var isSeq = (node) => !!node && typeof node === "object" && node[NODE_TYPE] === SEQ;
function isCollection(node) {
  if (node && typeof node === "object")
    switch (node[NODE_TYPE]) {
      case MAP:
      case SEQ:
        return true;
    }
  return false;
}
function isNode(node) {
  if (node && typeof node === "object")
    switch (node[NODE_TYPE]) {
      case ALIAS:
      case MAP:
      case SCALAR:
      case SEQ:
        return true;
    }
  return false;
}
var hasAnchor = (node) => (isScalar(node) || isCollection(node)) && !!node.anchor;

// ../../../frontend/node_modules/yaml/browser/dist/visit.js
var BREAK = /* @__PURE__ */ Symbol("break visit");
var SKIP = /* @__PURE__ */ Symbol("skip children");
var REMOVE = /* @__PURE__ */ Symbol("remove node");
function visit(node, visitor) {
  const visitor_ = initVisitor(visitor);
  if (isDocument(node)) {
    const cd = visit_(null, node.contents, visitor_, Object.freeze([node]));
    if (cd === REMOVE)
      node.contents = null;
  } else
    visit_(null, node, visitor_, Object.freeze([]));
}
visit.BREAK = BREAK;
visit.SKIP = SKIP;
visit.REMOVE = REMOVE;
function visit_(key, node, visitor, path) {
  const ctrl = callVisitor(key, node, visitor, path);
  if (isNode(ctrl) || isPair(ctrl)) {
    replaceNode(key, path, ctrl);
    return visit_(key, ctrl, visitor, path);
  }
  if (typeof ctrl !== "symbol") {
    if (isCollection(node)) {
      path = Object.freeze(path.concat(node));
      for (let i = 0; i < node.items.length; ++i) {
        const ci = visit_(i, node.items[i], visitor, path);
        if (typeof ci === "number")
          i = ci - 1;
        else if (ci === BREAK)
          return BREAK;
        else if (ci === REMOVE) {
          node.items.splice(i, 1);
          i -= 1;
        }
      }
    } else if (isPair(node)) {
      path = Object.freeze(path.concat(node));
      const ck = visit_("key", node.key, visitor, path);
      if (ck === BREAK)
        return BREAK;
      else if (ck === REMOVE)
        node.key = null;
      const cv = visit_("value", node.value, visitor, path);
      if (cv === BREAK)
        return BREAK;
      else if (cv === REMOVE)
        node.value = null;
    }
  }
  return ctrl;
}
async function visitAsync(node, visitor) {
  const visitor_ = initVisitor(visitor);
  if (isDocument(node)) {
    const cd = await visitAsync_(null, node.contents, visitor_, Object.freeze([node]));
    if (cd === REMOVE)
      node.contents = null;
  } else
    await visitAsync_(null, node, visitor_, Object.freeze([]));
}
visitAsync.BREAK = BREAK;
visitAsync.SKIP = SKIP;
visitAsync.REMOVE = REMOVE;
async function visitAsync_(key, node, visitor, path) {
  const ctrl = await callVisitor(key, node, visitor, path);
  if (isNode(ctrl) || isPair(ctrl)) {
    replaceNode(key, path, ctrl);
    return visitAsync_(key, ctrl, visitor, path);
  }
  if (typeof ctrl !== "symbol") {
    if (isCollection(node)) {
      path = Object.freeze(path.concat(node));
      for (let i = 0; i < node.items.length; ++i) {
        const ci = await visitAsync_(i, node.items[i], visitor, path);
        if (typeof ci === "number")
          i = ci - 1;
        else if (ci === BREAK)
          return BREAK;
        else if (ci === REMOVE) {
          node.items.splice(i, 1);
          i -= 1;
        }
      }
    } else if (isPair(node)) {
      path = Object.freeze(path.concat(node));
      const ck = await visitAsync_("key", node.key, visitor, path);
      if (ck === BREAK)
        return BREAK;
      else if (ck === REMOVE)
        node.key = null;
      const cv = await visitAsync_("value", node.value, visitor, path);
      if (cv === BREAK)
        return BREAK;
      else if (cv === REMOVE)
        node.value = null;
    }
  }
  return ctrl;
}
function initVisitor(visitor) {
  if (typeof visitor === "object" && (visitor.Collection || visitor.Node || visitor.Value)) {
    return Object.assign({
      Alias: visitor.Node,
      Map: visitor.Node,
      Scalar: visitor.Node,
      Seq: visitor.Node
    }, visitor.Value && {
      Map: visitor.Value,
      Scalar: visitor.Value,
      Seq: visitor.Value
    }, visitor.Collection && {
      Map: visitor.Collection,
      Seq: visitor.Collection
    }, visitor);
  }
  return visitor;
}
function callVisitor(key, node, visitor, path) {
  if (typeof visitor === "function")
    return visitor(key, node, path);
  if (isMap(node))
    return visitor.Map?.(key, node, path);
  if (isSeq(node))
    return visitor.Seq?.(key, node, path);
  if (isPair(node))
    return visitor.Pair?.(key, node, path);
  if (isScalar(node))
    return visitor.Scalar?.(key, node, path);
  if (isAlias(node))
    return visitor.Alias?.(key, node, path);
  return void 0;
}
function replaceNode(key, path, node) {
  const parent = path[path.length - 1];
  if (isCollection(parent)) {
    parent.items[key] = node;
  } else if (isPair(parent)) {
    if (key === "key")
      parent.key = node;
    else
      parent.value = node;
  } else if (isDocument(parent)) {
    parent.contents = node;
  } else {
    const pt = isAlias(parent) ? "alias" : "scalar";
    throw new Error(`Cannot replace node with ${pt} parent`);
  }
}

// ../../../frontend/node_modules/yaml/browser/dist/doc/directives.js
var escapeChars = {
  "!": "%21",
  ",": "%2C",
  "[": "%5B",
  "]": "%5D",
  "{": "%7B",
  "}": "%7D"
};
var escapeTagName = (tn) => tn.replace(/[!,[\]{}]/g, (ch) => escapeChars[ch]);
var Directives = class _Directives {
  constructor(yaml, tags) {
    this.docStart = null;
    this.docEnd = false;
    this.yaml = Object.assign({}, _Directives.defaultYaml, yaml);
    this.tags = Object.assign({}, _Directives.defaultTags, tags);
  }
  clone() {
    const copy = new _Directives(this.yaml, this.tags);
    copy.docStart = this.docStart;
    return copy;
  }
  /**
   * During parsing, get a Directives instance for the current document and
   * update the stream state according to the current version's spec.
   */
  atDocument() {
    const res = new _Directives(this.yaml, this.tags);
    switch (this.yaml.version) {
      case "1.1":
        this.atNextDocument = true;
        break;
      case "1.2":
        this.atNextDocument = false;
        this.yaml = {
          explicit: _Directives.defaultYaml.explicit,
          version: "1.2"
        };
        this.tags = Object.assign({}, _Directives.defaultTags);
        break;
    }
    return res;
  }
  /**
   * @param onError - May be called even if the action was successful
   * @returns `true` on success
   */
  add(line, onError) {
    if (this.atNextDocument) {
      this.yaml = { explicit: _Directives.defaultYaml.explicit, version: "1.1" };
      this.tags = Object.assign({}, _Directives.defaultTags);
      this.atNextDocument = false;
    }
    const parts = line.trim().split(/[ \t]+/);
    const name = parts.shift();
    switch (name) {
      case "%TAG": {
        if (parts.length !== 2) {
          onError(0, "%TAG directive should contain exactly two parts");
          if (parts.length < 2)
            return false;
        }
        const [handle, prefix] = parts;
        this.tags[handle] = prefix;
        return true;
      }
      case "%YAML": {
        this.yaml.explicit = true;
        if (parts.length !== 1) {
          onError(0, "%YAML directive should contain exactly one part");
          return false;
        }
        const [version] = parts;
        if (version === "1.1" || version === "1.2") {
          this.yaml.version = version;
          return true;
        } else {
          const isValid = /^\d+\.\d+$/.test(version);
          onError(6, `Unsupported YAML version ${version}`, isValid);
          return false;
        }
      }
      default:
        onError(0, `Unknown directive ${name}`, true);
        return false;
    }
  }
  /**
   * Resolves a tag, matching handles to those defined in %TAG directives.
   *
   * @returns Resolved tag, which may also be the non-specific tag `'!'` or a
   *   `'!local'` tag, or `null` if unresolvable.
   */
  tagName(source, onError) {
    if (source === "!")
      return "!";
    if (source[0] !== "!") {
      onError(`Not a valid tag: ${source}`);
      return null;
    }
    if (source[1] === "<") {
      const verbatim = source.slice(2, -1);
      if (verbatim === "!" || verbatim === "!!") {
        onError(`Verbatim tags aren't resolved, so ${source} is invalid.`);
        return null;
      }
      if (source[source.length - 1] !== ">")
        onError("Verbatim tags must end with a >");
      return verbatim;
    }
    const [, handle, suffix] = source.match(/^(.*!)([^!]*)$/s);
    if (!suffix)
      onError(`The ${source} tag has no suffix`);
    const prefix = this.tags[handle];
    if (prefix) {
      try {
        return prefix + decodeURIComponent(suffix);
      } catch (error) {
        onError(String(error));
        return null;
      }
    }
    if (handle === "!")
      return source;
    onError(`Could not resolve tag: ${source}`);
    return null;
  }
  /**
   * Given a fully resolved tag, returns its printable string form,
   * taking into account current tag prefixes and defaults.
   */
  tagString(tag) {
    for (const [handle, prefix] of Object.entries(this.tags)) {
      if (tag.startsWith(prefix))
        return handle + escapeTagName(tag.substring(prefix.length));
    }
    return tag[0] === "!" ? tag : `!<${tag}>`;
  }
  toString(doc) {
    const lines = this.yaml.explicit ? [`%YAML ${this.yaml.version || "1.2"}`] : [];
    const tagEntries = Object.entries(this.tags);
    let tagNames;
    if (doc && tagEntries.length > 0 && isNode(doc.contents)) {
      const tags = {};
      visit(doc.contents, (_key, node) => {
        if (isNode(node) && node.tag)
          tags[node.tag] = true;
      });
      tagNames = Object.keys(tags);
    } else
      tagNames = [];
    for (const [handle, prefix] of tagEntries) {
      if (handle === "!!" && prefix === "tag:yaml.org,2002:")
        continue;
      if (!doc || tagNames.some((tn) => tn.startsWith(prefix)))
        lines.push(`%TAG ${handle} ${prefix}`);
    }
    return lines.join("\n");
  }
};
Directives.defaultYaml = { explicit: false, version: "1.2" };
Directives.defaultTags = { "!!": "tag:yaml.org,2002:" };

// ../../../frontend/node_modules/yaml/browser/dist/doc/anchors.js
function anchorIsValid(anchor) {
  if (/[\x00-\x19\s,[\]{}]/.test(anchor)) {
    const sa = JSON.stringify(anchor);
    const msg = `Anchor must not contain whitespace or control characters: ${sa}`;
    throw new Error(msg);
  }
  return true;
}
function anchorNames(root) {
  const anchors = /* @__PURE__ */ new Set();
  visit(root, {
    Value(_key, node) {
      if (node.anchor)
        anchors.add(node.anchor);
    }
  });
  return anchors;
}
function findNewAnchor(prefix, exclude) {
  for (let i = 1; true; ++i) {
    const name = `${prefix}${i}`;
    if (!exclude.has(name))
      return name;
  }
}
function createNodeAnchors(doc, prefix) {
  const aliasObjects = [];
  const sourceObjects = /* @__PURE__ */ new Map();
  let prevAnchors = null;
  return {
    onAnchor: (source) => {
      aliasObjects.push(source);
      prevAnchors ?? (prevAnchors = anchorNames(doc));
      const anchor = findNewAnchor(prefix, prevAnchors);
      prevAnchors.add(anchor);
      return anchor;
    },
    /**
     * With circular references, the source node is only resolved after all
     * of its child nodes are. This is why anchors are set only after all of
     * the nodes have been created.
     */
    setAnchors: () => {
      for (const source of aliasObjects) {
        const ref = sourceObjects.get(source);
        if (typeof ref === "object" && ref.anchor && (isScalar(ref.node) || isCollection(ref.node))) {
          ref.node.anchor = ref.anchor;
        } else {
          const error = new Error("Failed to resolve repeated object (this should not happen)");
          error.source = source;
          throw error;
        }
      }
    },
    sourceObjects
  };
}

// ../../../frontend/node_modules/yaml/browser/dist/doc/applyReviver.js
function applyReviver(reviver, obj, key, val) {
  if (val && typeof val === "object") {
    if (Array.isArray(val)) {
      for (let i = 0, len = val.length; i < len; ++i) {
        const v0 = val[i];
        const v1 = applyReviver(reviver, val, String(i), v0);
        if (v1 === void 0)
          delete val[i];
        else if (v1 !== v0)
          val[i] = v1;
      }
    } else if (val instanceof Map) {
      for (const k of Array.from(val.keys())) {
        const v0 = val.get(k);
        const v1 = applyReviver(reviver, val, k, v0);
        if (v1 === void 0)
          val.delete(k);
        else if (v1 !== v0)
          val.set(k, v1);
      }
    } else if (val instanceof Set) {
      for (const v0 of Array.from(val)) {
        const v1 = applyReviver(reviver, val, v0, v0);
        if (v1 === void 0)
          val.delete(v0);
        else if (v1 !== v0) {
          val.delete(v0);
          val.add(v1);
        }
      }
    } else {
      for (const [k, v0] of Object.entries(val)) {
        const v1 = applyReviver(reviver, val, k, v0);
        if (v1 === void 0)
          delete val[k];
        else if (v1 !== v0)
          val[k] = v1;
      }
    }
  }
  return reviver.call(obj, key, val);
}

// ../../../frontend/node_modules/yaml/browser/dist/nodes/toJS.js
function toJS(value, arg, ctx) {
  if (Array.isArray(value))
    return value.map((v, i) => toJS(v, String(i), ctx));
  if (value && typeof value.toJSON === "function") {
    if (!ctx || !hasAnchor(value))
      return value.toJSON(arg, ctx);
    const data = { aliasCount: 0, count: 1, res: void 0 };
    ctx.anchors.set(value, data);
    ctx.onCreate = (res2) => {
      data.res = res2;
      delete ctx.onCreate;
    };
    const res = value.toJSON(arg, ctx);
    if (ctx.onCreate)
      ctx.onCreate(res);
    return res;
  }
  if (typeof value === "bigint" && !ctx?.keep)
    return Number(value);
  return value;
}

// ../../../frontend/node_modules/yaml/browser/dist/nodes/Node.js
var NodeBase = class {
  constructor(type) {
    Object.defineProperty(this, NODE_TYPE, { value: type });
  }
  /** Create a copy of this node.  */
  clone() {
    const copy = Object.create(Object.getPrototypeOf(this), Object.getOwnPropertyDescriptors(this));
    if (this.range)
      copy.range = this.range.slice();
    return copy;
  }
  /** A plain JavaScript representation of this node. */
  toJS(doc, { mapAsMap, maxAliasCount, onAnchor, reviver } = {}) {
    if (!isDocument(doc))
      throw new TypeError("A document argument is required");
    const ctx = {
      anchors: /* @__PURE__ */ new Map(),
      doc,
      keep: true,
      mapAsMap: mapAsMap === true,
      mapKeyWarned: false,
      maxAliasCount: typeof maxAliasCount === "number" ? maxAliasCount : 100
    };
    const res = toJS(this, "", ctx);
    if (typeof onAnchor === "function")
      for (const { count, res: res2 } of ctx.anchors.values())
        onAnchor(res2, count);
    return typeof reviver === "function" ? applyReviver(reviver, { "": res }, "", res) : res;
  }
};

// ../../../frontend/node_modules/yaml/browser/dist/nodes/Alias.js
var Alias = class extends NodeBase {
  constructor(source) {
    super(ALIAS);
    this.source = source;
    Object.defineProperty(this, "tag", {
      set() {
        throw new Error("Alias nodes cannot have tags");
      }
    });
  }
  /**
   * Resolve the value of this alias within `doc`, finding the last
   * instance of the `source` anchor before this node.
   */
  resolve(doc, ctx) {
    if (ctx?.maxAliasCount === 0)
      throw new ReferenceError("Alias resolution is disabled");
    let nodes;
    if (ctx?.aliasResolveCache) {
      nodes = ctx.aliasResolveCache;
    } else {
      nodes = [];
      visit(doc, {
        Node: (_key, node) => {
          if (isAlias(node) || hasAnchor(node))
            nodes.push(node);
        }
      });
      if (ctx)
        ctx.aliasResolveCache = nodes;
    }
    let found = void 0;
    for (const node of nodes) {
      if (node === this)
        break;
      if (node.anchor === this.source)
        found = node;
    }
    if (found && ctx) {
      const { anchors, doc: doc2, maxAliasCount } = ctx;
      let data = anchors.get(found);
      if (!data) {
        toJS(found, null, ctx);
        data = anchors.get(found);
      }
      if (data?.res === void 0) {
        const msg = "This should not happen: Alias anchor was not resolved?";
        throw new ReferenceError(msg);
      }
      if (maxAliasCount >= 0) {
        data.count += 1;
        if (data.aliasCount === 0)
          data.aliasCount = getAliasCount(doc2, found, anchors);
        if (data.count * data.aliasCount > maxAliasCount) {
          const msg = "Excessive alias count indicates a resource exhaustion attack";
          throw new ReferenceError(msg);
        }
      }
    }
    return found;
  }
  toJSON(_arg, ctx) {
    if (!ctx)
      return { source: this.source };
    const source = this.resolve(ctx.doc, ctx);
    if (!source) {
      const msg = `Unresolved alias (the anchor must be set before the alias): ${this.source}`;
      throw new ReferenceError(msg);
    }
    return ctx.anchors.get(source).res;
  }
  toString(ctx, _onComment, _onChompKeep) {
    const src = `*${this.source}`;
    if (ctx) {
      anchorIsValid(this.source);
      if (ctx.options.verifyAliasOrder && !ctx.anchors.has(this.source)) {
        const msg = `Unresolved alias (the anchor must be set before the alias): ${this.source}`;
        throw new Error(msg);
      }
      if (ctx.implicitKey)
        return `${src} `;
    }
    return src;
  }
};
function getAliasCount(doc, node, anchors) {
  if (isAlias(node)) {
    const source = node.resolve(doc);
    const anchor = anchors && source && anchors.get(source);
    return anchor ? anchor.count * anchor.aliasCount : 0;
  } else if (isCollection(node)) {
    let count = 0;
    for (const item of node.items) {
      const c = getAliasCount(doc, item, anchors);
      if (c > count)
        count = c;
    }
    return count;
  } else if (isPair(node)) {
    const kc = getAliasCount(doc, node.key, anchors);
    const vc = getAliasCount(doc, node.value, anchors);
    return Math.max(kc, vc);
  }
  return 1;
}

// ../../../frontend/node_modules/yaml/browser/dist/nodes/Scalar.js
var isScalarValue = (value) => !value || typeof value !== "function" && typeof value !== "object";
var Scalar = class extends NodeBase {
  constructor(value) {
    super(SCALAR);
    this.value = value;
  }
  toJSON(arg, ctx) {
    return ctx?.keep ? this.value : toJS(this.value, arg, ctx);
  }
  toString() {
    return String(this.value);
  }
};
Scalar.BLOCK_FOLDED = "BLOCK_FOLDED";
Scalar.BLOCK_LITERAL = "BLOCK_LITERAL";
Scalar.PLAIN = "PLAIN";
Scalar.QUOTE_DOUBLE = "QUOTE_DOUBLE";
Scalar.QUOTE_SINGLE = "QUOTE_SINGLE";

// ../../../frontend/node_modules/yaml/browser/dist/doc/createNode.js
var defaultTagPrefix = "tag:yaml.org,2002:";
function findTagObject(value, tagName, tags) {
  if (tagName) {
    const match = tags.filter((t) => t.tag === tagName);
    const tagObj = match.find((t) => !t.format) ?? match[0];
    if (!tagObj)
      throw new Error(`Tag ${tagName} not found`);
    return tagObj;
  }
  return tags.find((t) => t.identify?.(value) && !t.format);
}
function createNode(value, tagName, ctx) {
  if (isDocument(value))
    value = value.contents;
  if (isNode(value))
    return value;
  if (isPair(value)) {
    const map2 = ctx.schema[MAP].createNode?.(ctx.schema, null, ctx);
    map2.items.push(value);
    return map2;
  }
  if (value instanceof String || value instanceof Number || value instanceof Boolean || typeof BigInt !== "undefined" && value instanceof BigInt) {
    value = value.valueOf();
  }
  const { aliasDuplicateObjects, onAnchor, onTagObj, schema: schema4, sourceObjects } = ctx;
  let ref = void 0;
  if (aliasDuplicateObjects && value && typeof value === "object") {
    ref = sourceObjects.get(value);
    if (ref) {
      ref.anchor ?? (ref.anchor = onAnchor(value));
      return new Alias(ref.anchor);
    } else {
      ref = { anchor: null, node: null };
      sourceObjects.set(value, ref);
    }
  }
  if (tagName?.startsWith("!!"))
    tagName = defaultTagPrefix + tagName.slice(2);
  let tagObj = findTagObject(value, tagName, schema4.tags);
  if (!tagObj) {
    if (value && typeof value.toJSON === "function") {
      value = value.toJSON();
    }
    if (!value || typeof value !== "object") {
      const node2 = new Scalar(value);
      if (ref)
        ref.node = node2;
      return node2;
    }
    tagObj = value instanceof Map ? schema4[MAP] : Symbol.iterator in Object(value) ? schema4[SEQ] : schema4[MAP];
  }
  if (onTagObj) {
    onTagObj(tagObj);
    delete ctx.onTagObj;
  }
  const node = tagObj?.createNode ? tagObj.createNode(ctx.schema, value, ctx) : typeof tagObj?.nodeClass?.from === "function" ? tagObj.nodeClass.from(ctx.schema, value, ctx) : new Scalar(value);
  if (tagName)
    node.tag = tagName;
  else if (!tagObj.default)
    node.tag = tagObj.tag;
  if (ref)
    ref.node = node;
  return node;
}

// ../../../frontend/node_modules/yaml/browser/dist/nodes/Collection.js
function collectionFromPath(schema4, path, value) {
  let v = value;
  for (let i = path.length - 1; i >= 0; --i) {
    const k = path[i];
    if (typeof k === "number" && Number.isInteger(k) && k >= 0) {
      const a = [];
      a[k] = v;
      v = a;
    } else {
      v = /* @__PURE__ */ new Map([[k, v]]);
    }
  }
  return createNode(v, void 0, {
    aliasDuplicateObjects: false,
    keepUndefined: false,
    onAnchor: () => {
      throw new Error("This should not happen, please report a bug.");
    },
    schema: schema4,
    sourceObjects: /* @__PURE__ */ new Map()
  });
}
var isEmptyPath = (path) => path == null || typeof path === "object" && !!path[Symbol.iterator]().next().done;
var Collection = class extends NodeBase {
  constructor(type, schema4) {
    super(type);
    Object.defineProperty(this, "schema", {
      value: schema4,
      configurable: true,
      enumerable: false,
      writable: true
    });
  }
  /**
   * Create a copy of this collection.
   *
   * @param schema - If defined, overwrites the original's schema
   */
  clone(schema4) {
    const copy = Object.create(Object.getPrototypeOf(this), Object.getOwnPropertyDescriptors(this));
    if (schema4)
      copy.schema = schema4;
    copy.items = copy.items.map((it) => isNode(it) || isPair(it) ? it.clone(schema4) : it);
    if (this.range)
      copy.range = this.range.slice();
    return copy;
  }
  /**
   * Adds a value to the collection. For `!!map` and `!!omap` the value must
   * be a Pair instance or a `{ key, value }` object, which may not have a key
   * that already exists in the map.
   */
  addIn(path, value) {
    if (isEmptyPath(path))
      this.add(value);
    else {
      const [key, ...rest] = path;
      const node = this.get(key, true);
      if (isCollection(node))
        node.addIn(rest, value);
      else if (node === void 0 && this.schema)
        this.set(key, collectionFromPath(this.schema, rest, value));
      else
        throw new Error(`Expected YAML collection at ${key}. Remaining path: ${rest}`);
    }
  }
  /**
   * Removes a value from the collection.
   * @returns `true` if the item was found and removed.
   */
  deleteIn(path) {
    const [key, ...rest] = path;
    if (rest.length === 0)
      return this.delete(key);
    const node = this.get(key, true);
    if (isCollection(node))
      return node.deleteIn(rest);
    else
      throw new Error(`Expected YAML collection at ${key}. Remaining path: ${rest}`);
  }
  /**
   * Returns item at `key`, or `undefined` if not found. By default unwraps
   * scalar values from their surrounding node; to disable set `keepScalar` to
   * `true` (collections are always returned intact).
   */
  getIn(path, keepScalar) {
    const [key, ...rest] = path;
    const node = this.get(key, true);
    if (rest.length === 0)
      return !keepScalar && isScalar(node) ? node.value : node;
    else
      return isCollection(node) ? node.getIn(rest, keepScalar) : void 0;
  }
  hasAllNullValues(allowScalar) {
    return this.items.every((node) => {
      if (!isPair(node))
        return false;
      const n = node.value;
      return n == null || allowScalar && isScalar(n) && n.value == null && !n.commentBefore && !n.comment && !n.tag;
    });
  }
  /**
   * Checks if the collection includes a value with the key `key`.
   */
  hasIn(path) {
    const [key, ...rest] = path;
    if (rest.length === 0)
      return this.has(key);
    const node = this.get(key, true);
    return isCollection(node) ? node.hasIn(rest) : false;
  }
  /**
   * Sets a value in this collection. For `!!set`, `value` needs to be a
   * boolean to add/remove the item from the set.
   */
  setIn(path, value) {
    const [key, ...rest] = path;
    if (rest.length === 0) {
      this.set(key, value);
    } else {
      const node = this.get(key, true);
      if (isCollection(node))
        node.setIn(rest, value);
      else if (node === void 0 && this.schema)
        this.set(key, collectionFromPath(this.schema, rest, value));
      else
        throw new Error(`Expected YAML collection at ${key}. Remaining path: ${rest}`);
    }
  }
};

// ../../../frontend/node_modules/yaml/browser/dist/stringify/stringifyComment.js
var stringifyComment = (str2) => str2.replace(/^(?!$)(?: $)?/gm, "#");
function indentComment(comment, indent) {
  if (/^\n+$/.test(comment))
    return comment.substring(1);
  return indent ? comment.replace(/^(?! *$)/gm, indent) : comment;
}
var lineComment = (str2, indent, comment) => str2.endsWith("\n") ? indentComment(comment, indent) : comment.includes("\n") ? "\n" + indentComment(comment, indent) : (str2.endsWith(" ") ? "" : " ") + comment;

// ../../../frontend/node_modules/yaml/browser/dist/stringify/foldFlowLines.js
var FOLD_FLOW = "flow";
var FOLD_BLOCK = "block";
var FOLD_QUOTED = "quoted";
function foldFlowLines(text, indent, mode = "flow", { indentAtStart, lineWidth = 80, minContentWidth = 20, onFold, onOverflow } = {}) {
  if (!lineWidth || lineWidth < 0)
    return text;
  if (lineWidth < minContentWidth)
    minContentWidth = 0;
  const endStep = Math.max(1 + minContentWidth, 1 + lineWidth - indent.length);
  if (text.length <= endStep)
    return text;
  const folds = [];
  const escapedFolds = {};
  let end = lineWidth - indent.length;
  if (typeof indentAtStart === "number") {
    if (indentAtStart > lineWidth - Math.max(2, minContentWidth))
      folds.push(0);
    else
      end = lineWidth - indentAtStart;
  }
  let split = void 0;
  let prev = void 0;
  let overflow = false;
  let i = -1;
  let escStart = -1;
  let escEnd = -1;
  if (mode === FOLD_BLOCK) {
    i = consumeMoreIndentedLines(text, i, indent.length);
    if (i !== -1)
      end = i + endStep;
  }
  for (let ch; ch = text[i += 1]; ) {
    if (mode === FOLD_QUOTED && ch === "\\") {
      escStart = i;
      switch (text[i + 1]) {
        case "x":
          i += 3;
          break;
        case "u":
          i += 5;
          break;
        case "U":
          i += 9;
          break;
        default:
          i += 1;
      }
      escEnd = i;
    }
    if (ch === "\n") {
      if (mode === FOLD_BLOCK)
        i = consumeMoreIndentedLines(text, i, indent.length);
      end = i + indent.length + endStep;
      split = void 0;
    } else {
      if (ch === " " && prev && prev !== " " && prev !== "\n" && prev !== "	") {
        const next = text[i + 1];
        if (next && next !== " " && next !== "\n" && next !== "	")
          split = i;
      }
      if (i >= end) {
        if (split) {
          folds.push(split);
          end = split + endStep;
          split = void 0;
        } else if (mode === FOLD_QUOTED) {
          while (prev === " " || prev === "	") {
            prev = ch;
            ch = text[i += 1];
            overflow = true;
          }
          const j = i > escEnd + 1 ? i - 2 : escStart - 1;
          if (escapedFolds[j])
            return text;
          folds.push(j);
          escapedFolds[j] = true;
          end = j + endStep;
          split = void 0;
        } else {
          overflow = true;
        }
      }
    }
    prev = ch;
  }
  if (overflow && onOverflow)
    onOverflow();
  if (folds.length === 0)
    return text;
  if (onFold)
    onFold();
  let res = text.slice(0, folds[0]);
  for (let i2 = 0; i2 < folds.length; ++i2) {
    const fold = folds[i2];
    const end2 = folds[i2 + 1] || text.length;
    if (fold === 0)
      res = `
${indent}${text.slice(0, end2)}`;
    else {
      if (mode === FOLD_QUOTED && escapedFolds[fold])
        res += `${text[fold]}\\`;
      res += `
${indent}${text.slice(fold + 1, end2)}`;
    }
  }
  return res;
}
function consumeMoreIndentedLines(text, i, indent) {
  let end = i;
  let start = i + 1;
  let ch = text[start];
  while (ch === " " || ch === "	") {
    if (i < start + indent) {
      ch = text[++i];
    } else {
      do {
        ch = text[++i];
      } while (ch && ch !== "\n");
      end = i;
      start = i + 1;
      ch = text[start];
    }
  }
  return end;
}

// ../../../frontend/node_modules/yaml/browser/dist/stringify/stringifyString.js
var getFoldOptions = (ctx, isBlock2) => ({
  indentAtStart: isBlock2 ? ctx.indent.length : ctx.indentAtStart,
  lineWidth: ctx.options.lineWidth,
  minContentWidth: ctx.options.minContentWidth
});
var containsDocumentMarker = (str2) => /^(%|---|\.\.\.)/m.test(str2);
function lineLengthOverLimit(str2, lineWidth, indentLength) {
  if (!lineWidth || lineWidth < 0)
    return false;
  const limit = lineWidth - indentLength;
  const strLen = str2.length;
  if (strLen <= limit)
    return false;
  for (let i = 0, start = 0; i < strLen; ++i) {
    if (str2[i] === "\n") {
      if (i - start > limit)
        return true;
      start = i + 1;
      if (strLen - start <= limit)
        return false;
    }
  }
  return true;
}
function doubleQuotedString(value, ctx) {
  const json = JSON.stringify(value);
  if (ctx.options.doubleQuotedAsJSON)
    return json;
  const { implicitKey } = ctx;
  const minMultiLineLength = ctx.options.doubleQuotedMinMultiLineLength;
  const indent = ctx.indent || (containsDocumentMarker(value) ? "  " : "");
  let str2 = "";
  let start = 0;
  for (let i = 0, ch = json[i]; ch; ch = json[++i]) {
    if (ch === " " && json[i + 1] === "\\" && json[i + 2] === "n") {
      str2 += json.slice(start, i) + "\\ ";
      i += 1;
      start = i;
      ch = "\\";
    }
    if (ch === "\\")
      switch (json[i + 1]) {
        case "u":
          {
            str2 += json.slice(start, i);
            const code = json.substr(i + 2, 4);
            switch (code) {
              case "0000":
                str2 += "\\0";
                break;
              case "0007":
                str2 += "\\a";
                break;
              case "000b":
                str2 += "\\v";
                break;
              case "001b":
                str2 += "\\e";
                break;
              case "0085":
                str2 += "\\N";
                break;
              case "00a0":
                str2 += "\\_";
                break;
              case "2028":
                str2 += "\\L";
                break;
              case "2029":
                str2 += "\\P";
                break;
              default:
                if (code.substr(0, 2) === "00")
                  str2 += "\\x" + code.substr(2);
                else
                  str2 += json.substr(i, 6);
            }
            i += 5;
            start = i + 1;
          }
          break;
        case "n":
          if (implicitKey || json[i + 2] === '"' || json.length < minMultiLineLength) {
            i += 1;
          } else {
            str2 += json.slice(start, i) + "\n\n";
            while (json[i + 2] === "\\" && json[i + 3] === "n" && json[i + 4] !== '"') {
              str2 += "\n";
              i += 2;
            }
            str2 += indent;
            if (json[i + 2] === " ")
              str2 += "\\";
            i += 1;
            start = i + 1;
          }
          break;
        default:
          i += 1;
      }
  }
  str2 = start ? str2 + json.slice(start) : json;
  return implicitKey ? str2 : foldFlowLines(str2, indent, FOLD_QUOTED, getFoldOptions(ctx, false));
}
function singleQuotedString(value, ctx) {
  if (ctx.options.singleQuote === false || ctx.implicitKey && value.includes("\n") || /[ \t]\n|\n[ \t]/.test(value))
    return doubleQuotedString(value, ctx);
  const indent = ctx.indent || (containsDocumentMarker(value) ? "  " : "");
  const res = "'" + value.replace(/'/g, "''").replace(/\n+/g, `$&
${indent}`) + "'";
  return ctx.implicitKey ? res : foldFlowLines(res, indent, FOLD_FLOW, getFoldOptions(ctx, false));
}
function quotedString(value, ctx) {
  const { singleQuote } = ctx.options;
  let qs;
  if (singleQuote === false)
    qs = doubleQuotedString;
  else {
    const hasDouble = value.includes('"');
    const hasSingle = value.includes("'");
    if (hasDouble && !hasSingle)
      qs = singleQuotedString;
    else if (hasSingle && !hasDouble)
      qs = doubleQuotedString;
    else
      qs = singleQuote ? singleQuotedString : doubleQuotedString;
  }
  return qs(value, ctx);
}
var blockEndNewlines;
try {
  blockEndNewlines = new RegExp("(^|(?<!\n))\n+(?!\n|$)", "g");
} catch {
  blockEndNewlines = /\n+(?!\n|$)/g;
}
function blockString({ comment, type, value }, ctx, onComment, onChompKeep) {
  const { blockQuote, commentString, lineWidth } = ctx.options;
  if (!blockQuote || /\n[\t ]+$/.test(value)) {
    return quotedString(value, ctx);
  }
  const indent = ctx.indent || (ctx.forceBlockIndent || containsDocumentMarker(value) ? "  " : "");
  const literal = blockQuote === "literal" ? true : blockQuote === "folded" || type === Scalar.BLOCK_FOLDED ? false : type === Scalar.BLOCK_LITERAL ? true : !lineLengthOverLimit(value, lineWidth, indent.length);
  if (!value)
    return literal ? "|\n" : ">\n";
  let chomp;
  let endStart;
  for (endStart = value.length; endStart > 0; --endStart) {
    const ch = value[endStart - 1];
    if (ch !== "\n" && ch !== "	" && ch !== " ")
      break;
  }
  let end = value.substring(endStart);
  const endNlPos = end.indexOf("\n");
  if (endNlPos === -1) {
    chomp = "-";
  } else if (value === end || endNlPos !== end.length - 1) {
    chomp = "+";
    if (onChompKeep)
      onChompKeep();
  } else {
    chomp = "";
  }
  if (end) {
    value = value.slice(0, -end.length);
    if (end[end.length - 1] === "\n")
      end = end.slice(0, -1);
    end = end.replace(blockEndNewlines, `$&${indent}`);
  }
  let startWithSpace = false;
  let startEnd;
  let startNlPos = -1;
  for (startEnd = 0; startEnd < value.length; ++startEnd) {
    const ch = value[startEnd];
    if (ch === " ")
      startWithSpace = true;
    else if (ch === "\n")
      startNlPos = startEnd;
    else
      break;
  }
  let start = value.substring(0, startNlPos < startEnd ? startNlPos + 1 : startEnd);
  if (start) {
    value = value.substring(start.length);
    start = start.replace(/\n+/g, `$&${indent}`);
  }
  const indentSize = indent ? "2" : "1";
  let header = (startWithSpace ? indentSize : "") + chomp;
  if (comment) {
    header += " " + commentString(comment.replace(/ ?[\r\n]+/g, " "));
    if (onComment)
      onComment();
  }
  if (!literal) {
    const foldedValue = value.replace(/\n+/g, "\n$&").replace(/(?:^|\n)([\t ].*)(?:([\n\t ]*)\n(?![\n\t ]))?/g, "$1$2").replace(/\n+/g, `$&${indent}`);
    let literalFallback = false;
    const foldOptions = getFoldOptions(ctx, true);
    if (blockQuote !== "folded" && type !== Scalar.BLOCK_FOLDED) {
      foldOptions.onOverflow = () => {
        literalFallback = true;
      };
    }
    const body = foldFlowLines(`${start}${foldedValue}${end}`, indent, FOLD_BLOCK, foldOptions);
    if (!literalFallback)
      return `>${header}
${indent}${body}`;
  }
  value = value.replace(/\n+/g, `$&${indent}`);
  return `|${header}
${indent}${start}${value}${end}`;
}
function plainString(item, ctx, onComment, onChompKeep) {
  const { type, value } = item;
  const { actualString, implicitKey, indent, indentStep, inFlow } = ctx;
  if (implicitKey && value.includes("\n") || inFlow && /[[\]{},]/.test(value)) {
    return quotedString(value, ctx);
  }
  if (/^[\n\t ,[\]{}#&*!|>'"%@`]|^[?-]$|^[?-][ \t]|[\n:][ \t]|[ \t]\n|[\n\t ]#|[\n\t :]$/.test(value)) {
    return implicitKey || inFlow || !value.includes("\n") ? quotedString(value, ctx) : blockString(item, ctx, onComment, onChompKeep);
  }
  if (!implicitKey && !inFlow && type !== Scalar.PLAIN && value.includes("\n")) {
    return blockString(item, ctx, onComment, onChompKeep);
  }
  if (containsDocumentMarker(value)) {
    if (indent === "") {
      ctx.forceBlockIndent = true;
      return blockString(item, ctx, onComment, onChompKeep);
    } else if (implicitKey && indent === indentStep) {
      return quotedString(value, ctx);
    }
  }
  const str2 = value.replace(/\n+/g, `$&
${indent}`);
  if (actualString) {
    const test = (tag) => tag.default && tag.tag !== "tag:yaml.org,2002:str" && tag.test?.test(str2);
    const { compat, tags } = ctx.doc.schema;
    if (tags.some(test) || compat?.some(test))
      return quotedString(value, ctx);
  }
  return implicitKey ? str2 : foldFlowLines(str2, indent, FOLD_FLOW, getFoldOptions(ctx, false));
}
function stringifyString(item, ctx, onComment, onChompKeep) {
  const { implicitKey, inFlow } = ctx;
  const ss = typeof item.value === "string" ? item : Object.assign({}, item, { value: String(item.value) });
  let { type } = item;
  if (type !== Scalar.QUOTE_DOUBLE) {
    if (/[\x00-\x08\x0b-\x1f\x7f-\x9f\u{D800}-\u{DFFF}]/u.test(ss.value))
      type = Scalar.QUOTE_DOUBLE;
  }
  const _stringify = (_type) => {
    switch (_type) {
      case Scalar.BLOCK_FOLDED:
      case Scalar.BLOCK_LITERAL:
        return implicitKey || inFlow ? quotedString(ss.value, ctx) : blockString(ss, ctx, onComment, onChompKeep);
      case Scalar.QUOTE_DOUBLE:
        return doubleQuotedString(ss.value, ctx);
      case Scalar.QUOTE_SINGLE:
        return singleQuotedString(ss.value, ctx);
      case Scalar.PLAIN:
        return plainString(ss, ctx, onComment, onChompKeep);
      default:
        return null;
    }
  };
  let res = _stringify(type);
  if (res === null) {
    const { defaultKeyType, defaultStringType } = ctx.options;
    const t = implicitKey && defaultKeyType || defaultStringType;
    res = _stringify(t);
    if (res === null)
      throw new Error(`Unsupported default string type ${t}`);
  }
  return res;
}

// ../../../frontend/node_modules/yaml/browser/dist/stringify/stringify.js
function createStringifyContext(doc, options) {
  const opt = Object.assign({
    blockQuote: true,
    commentString: stringifyComment,
    defaultKeyType: null,
    defaultStringType: "PLAIN",
    directives: null,
    doubleQuotedAsJSON: false,
    doubleQuotedMinMultiLineLength: 40,
    falseStr: "false",
    flowCollectionPadding: true,
    indentSeq: true,
    lineWidth: 80,
    minContentWidth: 20,
    nullStr: "null",
    simpleKeys: false,
    singleQuote: null,
    trailingComma: false,
    trueStr: "true",
    verifyAliasOrder: true
  }, doc.schema.toStringOptions, options);
  let inFlow;
  switch (opt.collectionStyle) {
    case "block":
      inFlow = false;
      break;
    case "flow":
      inFlow = true;
      break;
    default:
      inFlow = null;
  }
  return {
    anchors: /* @__PURE__ */ new Set(),
    doc,
    flowCollectionPadding: opt.flowCollectionPadding ? " " : "",
    indent: "",
    indentStep: typeof opt.indent === "number" ? " ".repeat(opt.indent) : "  ",
    inFlow,
    options: opt
  };
}
function getTagObject(tags, item) {
  if (item.tag) {
    const match = tags.filter((t) => t.tag === item.tag);
    if (match.length > 0)
      return match.find((t) => t.format === item.format) ?? match[0];
  }
  let tagObj = void 0;
  let obj;
  if (isScalar(item)) {
    obj = item.value;
    let match = tags.filter((t) => t.identify?.(obj));
    if (match.length > 1) {
      const testMatch = match.filter((t) => t.test);
      if (testMatch.length > 0)
        match = testMatch;
    }
    tagObj = match.find((t) => t.format === item.format) ?? match.find((t) => !t.format);
  } else {
    obj = item;
    tagObj = tags.find((t) => t.nodeClass && obj instanceof t.nodeClass);
  }
  if (!tagObj) {
    const name = obj?.constructor?.name ?? (obj === null ? "null" : typeof obj);
    throw new Error(`Tag not resolved for ${name} value`);
  }
  return tagObj;
}
function stringifyProps(node, tagObj, { anchors, doc }) {
  if (!doc.directives)
    return "";
  const props = [];
  const anchor = (isScalar(node) || isCollection(node)) && node.anchor;
  if (anchor && anchorIsValid(anchor)) {
    anchors.add(anchor);
    props.push(`&${anchor}`);
  }
  const tag = node.tag ?? (tagObj.default ? null : tagObj.tag);
  if (tag)
    props.push(doc.directives.tagString(tag));
  return props.join(" ");
}
function stringify(item, ctx, onComment, onChompKeep) {
  if (isPair(item))
    return item.toString(ctx, onComment, onChompKeep);
  if (isAlias(item)) {
    if (ctx.doc.directives)
      return item.toString(ctx);
    if (ctx.resolvedAliases?.has(item)) {
      throw new TypeError(`Cannot stringify circular structure without alias nodes`);
    } else {
      if (ctx.resolvedAliases)
        ctx.resolvedAliases.add(item);
      else
        ctx.resolvedAliases = /* @__PURE__ */ new Set([item]);
      item = item.resolve(ctx.doc);
    }
  }
  let tagObj = void 0;
  const node = isNode(item) ? item : ctx.doc.createNode(item, { onTagObj: (o) => tagObj = o });
  tagObj ?? (tagObj = getTagObject(ctx.doc.schema.tags, node));
  const props = stringifyProps(node, tagObj, ctx);
  if (props.length > 0)
    ctx.indentAtStart = (ctx.indentAtStart ?? 0) + props.length + 1;
  const str2 = typeof tagObj.stringify === "function" ? tagObj.stringify(node, ctx, onComment, onChompKeep) : isScalar(node) ? stringifyString(node, ctx, onComment, onChompKeep) : node.toString(ctx, onComment, onChompKeep);
  if (!props)
    return str2;
  return isScalar(node) || str2[0] === "{" || str2[0] === "[" ? `${props} ${str2}` : `${props}
${ctx.indent}${str2}`;
}

// ../../../frontend/node_modules/yaml/browser/dist/stringify/stringifyPair.js
function stringifyPair({ key, value }, ctx, onComment, onChompKeep) {
  const { allNullValues, doc, indent, indentStep, options: { commentString, indentSeq, simpleKeys } } = ctx;
  let keyComment = isNode(key) && key.comment || null;
  if (simpleKeys) {
    if (keyComment) {
      throw new Error("With simple keys, key nodes cannot have comments");
    }
    if (isCollection(key) || !isNode(key) && typeof key === "object") {
      const msg = "With simple keys, collection cannot be used as a key value";
      throw new Error(msg);
    }
  }
  let explicitKey = !simpleKeys && (!key || keyComment && value == null && !ctx.inFlow || isCollection(key) || (isScalar(key) ? key.type === Scalar.BLOCK_FOLDED || key.type === Scalar.BLOCK_LITERAL : typeof key === "object"));
  ctx = Object.assign({}, ctx, {
    allNullValues: false,
    implicitKey: !explicitKey && (simpleKeys || !allNullValues),
    indent: indent + indentStep
  });
  let keyCommentDone = false;
  let chompKeep = false;
  let str2 = stringify(key, ctx, () => keyCommentDone = true, () => chompKeep = true);
  if (!explicitKey && !ctx.inFlow && str2.length > 1024) {
    if (simpleKeys)
      throw new Error("With simple keys, single line scalar must not span more than 1024 characters");
    explicitKey = true;
  }
  if (ctx.inFlow) {
    if (allNullValues || value == null) {
      if (keyCommentDone && onComment)
        onComment();
      return str2 === "" ? "?" : explicitKey ? `? ${str2}` : str2;
    }
  } else if (allNullValues && !simpleKeys || value == null && explicitKey) {
    str2 = `? ${str2}`;
    if (keyComment && !keyCommentDone) {
      str2 += lineComment(str2, ctx.indent, commentString(keyComment));
    } else if (chompKeep && onChompKeep)
      onChompKeep();
    return str2;
  }
  if (keyCommentDone)
    keyComment = null;
  if (explicitKey) {
    if (keyComment)
      str2 += lineComment(str2, ctx.indent, commentString(keyComment));
    str2 = `? ${str2}
${indent}:`;
  } else {
    str2 = `${str2}:`;
    if (keyComment)
      str2 += lineComment(str2, ctx.indent, commentString(keyComment));
  }
  let vsb, vcb, valueComment;
  if (isNode(value)) {
    vsb = !!value.spaceBefore;
    vcb = value.commentBefore;
    valueComment = value.comment;
  } else {
    vsb = false;
    vcb = null;
    valueComment = null;
    if (value && typeof value === "object")
      value = doc.createNode(value);
  }
  ctx.implicitKey = false;
  if (!explicitKey && !keyComment && isScalar(value))
    ctx.indentAtStart = str2.length + 1;
  chompKeep = false;
  if (!indentSeq && indentStep.length >= 2 && !ctx.inFlow && !explicitKey && isSeq(value) && !value.flow && !value.tag && !value.anchor) {
    ctx.indent = ctx.indent.substring(2);
  }
  let valueCommentDone = false;
  const valueStr = stringify(value, ctx, () => valueCommentDone = true, () => chompKeep = true);
  let ws = " ";
  if (keyComment || vsb || vcb) {
    ws = vsb ? "\n" : "";
    if (vcb) {
      const cs = commentString(vcb);
      ws += `
${indentComment(cs, ctx.indent)}`;
    }
    if (valueStr === "" && !ctx.inFlow) {
      if (ws === "\n" && valueComment)
        ws = "\n\n";
    } else {
      ws += `
${ctx.indent}`;
    }
  } else if (!explicitKey && isCollection(value)) {
    const vs0 = valueStr[0];
    const nl0 = valueStr.indexOf("\n");
    const hasNewline = nl0 !== -1;
    const flow = ctx.inFlow ?? value.flow ?? value.items.length === 0;
    if (hasNewline || !flow) {
      let hasPropsLine = false;
      if (hasNewline && (vs0 === "&" || vs0 === "!")) {
        let sp0 = valueStr.indexOf(" ");
        if (vs0 === "&" && sp0 !== -1 && sp0 < nl0 && valueStr[sp0 + 1] === "!") {
          sp0 = valueStr.indexOf(" ", sp0 + 1);
        }
        if (sp0 === -1 || nl0 < sp0)
          hasPropsLine = true;
      }
      if (!hasPropsLine)
        ws = `
${ctx.indent}`;
    }
  } else if (valueStr === "" || valueStr[0] === "\n") {
    ws = "";
  }
  str2 += ws + valueStr;
  if (ctx.inFlow) {
    if (valueCommentDone && onComment)
      onComment();
  } else if (valueComment && !valueCommentDone) {
    str2 += lineComment(str2, ctx.indent, commentString(valueComment));
  } else if (chompKeep && onChompKeep) {
    onChompKeep();
  }
  return str2;
}

// ../../../frontend/node_modules/yaml/browser/dist/log.js
function warn(logLevel, warning) {
  if (logLevel === "debug" || logLevel === "warn") {
    console.warn(warning);
  }
}

// ../../../frontend/node_modules/yaml/browser/dist/schema/yaml-1.1/merge.js
var MERGE_KEY = "<<";
var merge = {
  identify: (value) => value === MERGE_KEY || typeof value === "symbol" && value.description === MERGE_KEY,
  default: "key",
  tag: "tag:yaml.org,2002:merge",
  test: /^<<$/,
  resolve: () => Object.assign(new Scalar(Symbol(MERGE_KEY)), {
    addToJSMap: addMergeToJSMap
  }),
  stringify: () => MERGE_KEY
};
var isMergeKey = (ctx, key) => (merge.identify(key) || isScalar(key) && (!key.type || key.type === Scalar.PLAIN) && merge.identify(key.value)) && ctx?.doc.schema.tags.some((tag) => tag.tag === merge.tag && tag.default);
function addMergeToJSMap(ctx, map2, value) {
  const source = resolveAliasValue(ctx, value);
  if (isSeq(source))
    for (const it of source.items)
      mergeValue(ctx, map2, it);
  else if (Array.isArray(source))
    for (const it of source)
      mergeValue(ctx, map2, it);
  else
    mergeValue(ctx, map2, source);
}
function mergeValue(ctx, map2, value) {
  const source = resolveAliasValue(ctx, value);
  if (!isMap(source))
    throw new Error("Merge sources must be maps or map aliases");
  const srcMap = source.toJSON(null, ctx, Map);
  for (const [key, value2] of srcMap) {
    if (map2 instanceof Map) {
      if (!map2.has(key))
        map2.set(key, value2);
    } else if (map2 instanceof Set) {
      map2.add(key);
    } else if (!Object.prototype.hasOwnProperty.call(map2, key)) {
      Object.defineProperty(map2, key, {
        value: value2,
        writable: true,
        enumerable: true,
        configurable: true
      });
    }
  }
  return map2;
}
function resolveAliasValue(ctx, value) {
  return ctx && isAlias(value) ? value.resolve(ctx.doc, ctx) : value;
}

// ../../../frontend/node_modules/yaml/browser/dist/nodes/addPairToJSMap.js
function addPairToJSMap(ctx, map2, { key, value }) {
  if (isNode(key) && key.addToJSMap)
    key.addToJSMap(ctx, map2, value);
  else if (isMergeKey(ctx, key))
    addMergeToJSMap(ctx, map2, value);
  else {
    const jsKey = toJS(key, "", ctx);
    if (map2 instanceof Map) {
      map2.set(jsKey, toJS(value, jsKey, ctx));
    } else if (map2 instanceof Set) {
      map2.add(jsKey);
    } else {
      const stringKey = stringifyKey(key, jsKey, ctx);
      const jsValue = toJS(value, stringKey, ctx);
      if (stringKey in map2)
        Object.defineProperty(map2, stringKey, {
          value: jsValue,
          writable: true,
          enumerable: true,
          configurable: true
        });
      else
        map2[stringKey] = jsValue;
    }
  }
  return map2;
}
function stringifyKey(key, jsKey, ctx) {
  if (jsKey === null)
    return "";
  if (typeof jsKey !== "object")
    return String(jsKey);
  if (isNode(key) && ctx?.doc) {
    const strCtx = createStringifyContext(ctx.doc, {});
    strCtx.anchors = /* @__PURE__ */ new Set();
    for (const node of ctx.anchors.keys())
      strCtx.anchors.add(node.anchor);
    strCtx.inFlow = true;
    strCtx.inStringifyKey = true;
    const strKey = key.toString(strCtx);
    if (!ctx.mapKeyWarned) {
      let jsonStr = JSON.stringify(strKey);
      if (jsonStr.length > 40)
        jsonStr = jsonStr.substring(0, 36) + '..."';
      warn(ctx.doc.options.logLevel, `Keys with collection values will be stringified due to JS Object restrictions: ${jsonStr}. Set mapAsMap: true to use object keys.`);
      ctx.mapKeyWarned = true;
    }
    return strKey;
  }
  return JSON.stringify(jsKey);
}

// ../../../frontend/node_modules/yaml/browser/dist/nodes/Pair.js
function createPair(key, value, ctx) {
  const k = createNode(key, void 0, ctx);
  const v = createNode(value, void 0, ctx);
  return new Pair(k, v);
}
var Pair = class _Pair {
  constructor(key, value = null) {
    Object.defineProperty(this, NODE_TYPE, { value: PAIR });
    this.key = key;
    this.value = value;
  }
  clone(schema4) {
    let { key, value } = this;
    if (isNode(key))
      key = key.clone(schema4);
    if (isNode(value))
      value = value.clone(schema4);
    return new _Pair(key, value);
  }
  toJSON(_, ctx) {
    const pair = ctx?.mapAsMap ? /* @__PURE__ */ new Map() : {};
    return addPairToJSMap(ctx, pair, this);
  }
  toString(ctx, onComment, onChompKeep) {
    return ctx?.doc ? stringifyPair(this, ctx, onComment, onChompKeep) : JSON.stringify(this);
  }
};

// ../../../frontend/node_modules/yaml/browser/dist/stringify/stringifyCollection.js
function stringifyCollection(collection, ctx, options) {
  const flow = ctx.inFlow ?? collection.flow;
  const stringify4 = flow ? stringifyFlowCollection : stringifyBlockCollection;
  return stringify4(collection, ctx, options);
}
function stringifyBlockCollection({ comment, items }, ctx, { blockItemPrefix, flowChars, itemIndent, onChompKeep, onComment }) {
  const { indent, options: { commentString } } = ctx;
  const itemCtx = Object.assign({}, ctx, { indent: itemIndent, type: null });
  let chompKeep = false;
  const lines = [];
  for (let i = 0; i < items.length; ++i) {
    const item = items[i];
    let comment2 = null;
    if (isNode(item)) {
      if (!chompKeep && item.spaceBefore)
        lines.push("");
      addCommentBefore(ctx, lines, item.commentBefore, chompKeep);
      if (item.comment)
        comment2 = item.comment;
    } else if (isPair(item)) {
      const ik = isNode(item.key) ? item.key : null;
      if (ik) {
        if (!chompKeep && ik.spaceBefore)
          lines.push("");
        addCommentBefore(ctx, lines, ik.commentBefore, chompKeep);
      }
    }
    chompKeep = false;
    let str3 = stringify(item, itemCtx, () => comment2 = null, () => chompKeep = true);
    if (comment2)
      str3 += lineComment(str3, itemIndent, commentString(comment2));
    if (chompKeep && comment2)
      chompKeep = false;
    lines.push(blockItemPrefix + str3);
  }
  let str2;
  if (lines.length === 0) {
    str2 = flowChars.start + flowChars.end;
  } else {
    str2 = lines[0];
    for (let i = 1; i < lines.length; ++i) {
      const line = lines[i];
      str2 += line ? `
${indent}${line}` : "\n";
    }
  }
  if (comment) {
    str2 += "\n" + indentComment(commentString(comment), indent);
    if (onComment)
      onComment();
  } else if (chompKeep && onChompKeep)
    onChompKeep();
  return str2;
}
function stringifyFlowCollection({ items }, ctx, { flowChars, itemIndent }) {
  const { indent, indentStep, flowCollectionPadding: fcPadding, options: { commentString } } = ctx;
  itemIndent += indentStep;
  const itemCtx = Object.assign({}, ctx, {
    indent: itemIndent,
    inFlow: true,
    type: null
  });
  let reqNewline = false;
  let linesAtValue = 0;
  const lines = [];
  for (let i = 0; i < items.length; ++i) {
    const item = items[i];
    let comment = null;
    if (isNode(item)) {
      if (item.spaceBefore)
        lines.push("");
      addCommentBefore(ctx, lines, item.commentBefore, false);
      if (item.comment)
        comment = item.comment;
    } else if (isPair(item)) {
      const ik = isNode(item.key) ? item.key : null;
      if (ik) {
        if (ik.spaceBefore)
          lines.push("");
        addCommentBefore(ctx, lines, ik.commentBefore, false);
        if (ik.comment)
          reqNewline = true;
      }
      const iv = isNode(item.value) ? item.value : null;
      if (iv) {
        if (iv.comment)
          comment = iv.comment;
        if (iv.commentBefore)
          reqNewline = true;
      } else if (item.value == null && ik?.comment) {
        comment = ik.comment;
      }
    }
    if (comment)
      reqNewline = true;
    let str2 = stringify(item, itemCtx, () => comment = null);
    reqNewline || (reqNewline = lines.length > linesAtValue || str2.includes("\n"));
    if (i < items.length - 1) {
      str2 += ",";
    } else if (ctx.options.trailingComma) {
      if (ctx.options.lineWidth > 0) {
        reqNewline || (reqNewline = lines.reduce((sum, line) => sum + line.length + 2, 2) + (str2.length + 2) > ctx.options.lineWidth);
      }
      if (reqNewline) {
        str2 += ",";
      }
    }
    if (comment)
      str2 += lineComment(str2, itemIndent, commentString(comment));
    lines.push(str2);
    linesAtValue = lines.length;
  }
  const { start, end } = flowChars;
  if (lines.length === 0) {
    return start + end;
  } else {
    if (!reqNewline) {
      const len = lines.reduce((sum, line) => sum + line.length + 2, 2);
      reqNewline = ctx.options.lineWidth > 0 && len > ctx.options.lineWidth;
    }
    if (reqNewline) {
      let str2 = start;
      for (const line of lines)
        str2 += line ? `
${indentStep}${indent}${line}` : "\n";
      return `${str2}
${indent}${end}`;
    } else {
      return `${start}${fcPadding}${lines.join(" ")}${fcPadding}${end}`;
    }
  }
}
function addCommentBefore({ indent, options: { commentString } }, lines, comment, chompKeep) {
  if (comment && chompKeep)
    comment = comment.replace(/^\n+/, "");
  if (comment) {
    const ic = indentComment(commentString(comment), indent);
    lines.push(ic.trimStart());
  }
}

// ../../../frontend/node_modules/yaml/browser/dist/nodes/YAMLMap.js
function findPair(items, key) {
  const k = isScalar(key) ? key.value : key;
  for (const it of items) {
    if (isPair(it)) {
      if (it.key === key || it.key === k)
        return it;
      if (isScalar(it.key) && it.key.value === k)
        return it;
    }
  }
  return void 0;
}
var YAMLMap = class extends Collection {
  static get tagName() {
    return "tag:yaml.org,2002:map";
  }
  constructor(schema4) {
    super(MAP, schema4);
    this.items = [];
  }
  /**
   * A generic collection parsing method that can be extended
   * to other node classes that inherit from YAMLMap
   */
  static from(schema4, obj, ctx) {
    const { keepUndefined, replacer } = ctx;
    const map2 = new this(schema4);
    const add = (key, value) => {
      if (typeof replacer === "function")
        value = replacer.call(obj, key, value);
      else if (Array.isArray(replacer) && !replacer.includes(key))
        return;
      if (value !== void 0 || keepUndefined)
        map2.items.push(createPair(key, value, ctx));
    };
    if (obj instanceof Map) {
      for (const [key, value] of obj)
        add(key, value);
    } else if (obj && typeof obj === "object") {
      for (const key of Object.keys(obj))
        add(key, obj[key]);
    }
    if (typeof schema4.sortMapEntries === "function") {
      map2.items.sort(schema4.sortMapEntries);
    }
    return map2;
  }
  /**
   * Adds a value to the collection.
   *
   * @param overwrite - If not set `true`, using a key that is already in the
   *   collection will throw. Otherwise, overwrites the previous value.
   */
  add(pair, overwrite) {
    let _pair;
    if (isPair(pair))
      _pair = pair;
    else if (!pair || typeof pair !== "object" || !("key" in pair)) {
      _pair = new Pair(pair, pair?.value);
    } else
      _pair = new Pair(pair.key, pair.value);
    const prev = findPair(this.items, _pair.key);
    const sortEntries = this.schema?.sortMapEntries;
    if (prev) {
      if (!overwrite)
        throw new Error(`Key ${_pair.key} already set`);
      if (isScalar(prev.value) && isScalarValue(_pair.value))
        prev.value.value = _pair.value;
      else
        prev.value = _pair.value;
    } else if (sortEntries) {
      const i = this.items.findIndex((item) => sortEntries(_pair, item) < 0);
      if (i === -1)
        this.items.push(_pair);
      else
        this.items.splice(i, 0, _pair);
    } else {
      this.items.push(_pair);
    }
  }
  delete(key) {
    const it = findPair(this.items, key);
    if (!it)
      return false;
    const del = this.items.splice(this.items.indexOf(it), 1);
    return del.length > 0;
  }
  get(key, keepScalar) {
    const it = findPair(this.items, key);
    const node = it?.value;
    return (!keepScalar && isScalar(node) ? node.value : node) ?? void 0;
  }
  has(key) {
    return !!findPair(this.items, key);
  }
  set(key, value) {
    this.add(new Pair(key, value), true);
  }
  /**
   * @param ctx - Conversion context, originally set in Document#toJS()
   * @param {Class} Type - If set, forces the returned collection type
   * @returns Instance of Type, Map, or Object
   */
  toJSON(_, ctx, Type) {
    const map2 = Type ? new Type() : ctx?.mapAsMap ? /* @__PURE__ */ new Map() : {};
    if (ctx?.onCreate)
      ctx.onCreate(map2);
    for (const item of this.items)
      addPairToJSMap(ctx, map2, item);
    return map2;
  }
  toString(ctx, onComment, onChompKeep) {
    if (!ctx)
      return JSON.stringify(this);
    for (const item of this.items) {
      if (!isPair(item))
        throw new Error(`Map items must all be pairs; found ${JSON.stringify(item)} instead`);
    }
    if (!ctx.allNullValues && this.hasAllNullValues(false))
      ctx = Object.assign({}, ctx, { allNullValues: true });
    return stringifyCollection(this, ctx, {
      blockItemPrefix: "",
      flowChars: { start: "{", end: "}" },
      itemIndent: ctx.indent || "",
      onChompKeep,
      onComment
    });
  }
};

// ../../../frontend/node_modules/yaml/browser/dist/schema/common/map.js
var map = {
  collection: "map",
  default: true,
  nodeClass: YAMLMap,
  tag: "tag:yaml.org,2002:map",
  resolve(map2, onError) {
    if (!isMap(map2))
      onError("Expected a mapping for this tag");
    return map2;
  },
  createNode: (schema4, obj, ctx) => YAMLMap.from(schema4, obj, ctx)
};

// ../../../frontend/node_modules/yaml/browser/dist/nodes/YAMLSeq.js
var YAMLSeq = class extends Collection {
  static get tagName() {
    return "tag:yaml.org,2002:seq";
  }
  constructor(schema4) {
    super(SEQ, schema4);
    this.items = [];
  }
  add(value) {
    this.items.push(value);
  }
  /**
   * Removes a value from the collection.
   *
   * `key` must contain a representation of an integer for this to succeed.
   * It may be wrapped in a `Scalar`.
   *
   * @returns `true` if the item was found and removed.
   */
  delete(key) {
    const idx = asItemIndex(key);
    if (typeof idx !== "number")
      return false;
    const del = this.items.splice(idx, 1);
    return del.length > 0;
  }
  get(key, keepScalar) {
    const idx = asItemIndex(key);
    if (typeof idx !== "number")
      return void 0;
    const it = this.items[idx];
    return !keepScalar && isScalar(it) ? it.value : it;
  }
  /**
   * Checks if the collection includes a value with the key `key`.
   *
   * `key` must contain a representation of an integer for this to succeed.
   * It may be wrapped in a `Scalar`.
   */
  has(key) {
    const idx = asItemIndex(key);
    return typeof idx === "number" && idx < this.items.length;
  }
  /**
   * Sets a value in this collection. For `!!set`, `value` needs to be a
   * boolean to add/remove the item from the set.
   *
   * If `key` does not contain a representation of an integer, this will throw.
   * It may be wrapped in a `Scalar`.
   */
  set(key, value) {
    const idx = asItemIndex(key);
    if (typeof idx !== "number")
      throw new Error(`Expected a valid index, not ${key}.`);
    const prev = this.items[idx];
    if (isScalar(prev) && isScalarValue(value))
      prev.value = value;
    else
      this.items[idx] = value;
  }
  toJSON(_, ctx) {
    const seq2 = [];
    if (ctx?.onCreate)
      ctx.onCreate(seq2);
    let i = 0;
    for (const item of this.items)
      seq2.push(toJS(item, String(i++), ctx));
    return seq2;
  }
  toString(ctx, onComment, onChompKeep) {
    if (!ctx)
      return JSON.stringify(this);
    return stringifyCollection(this, ctx, {
      blockItemPrefix: "- ",
      flowChars: { start: "[", end: "]" },
      itemIndent: (ctx.indent || "") + "  ",
      onChompKeep,
      onComment
    });
  }
  static from(schema4, obj, ctx) {
    const { replacer } = ctx;
    const seq2 = new this(schema4);
    if (obj && Symbol.iterator in Object(obj)) {
      let i = 0;
      for (let it of obj) {
        if (typeof replacer === "function") {
          const key = obj instanceof Set ? it : String(i++);
          it = replacer.call(obj, key, it);
        }
        seq2.items.push(createNode(it, void 0, ctx));
      }
    }
    return seq2;
  }
};
function asItemIndex(key) {
  let idx = isScalar(key) ? key.value : key;
  if (idx && typeof idx === "string")
    idx = Number(idx);
  return typeof idx === "number" && Number.isInteger(idx) && idx >= 0 ? idx : null;
}

// ../../../frontend/node_modules/yaml/browser/dist/schema/common/seq.js
var seq = {
  collection: "seq",
  default: true,
  nodeClass: YAMLSeq,
  tag: "tag:yaml.org,2002:seq",
  resolve(seq2, onError) {
    if (!isSeq(seq2))
      onError("Expected a sequence for this tag");
    return seq2;
  },
  createNode: (schema4, obj, ctx) => YAMLSeq.from(schema4, obj, ctx)
};

// ../../../frontend/node_modules/yaml/browser/dist/schema/common/string.js
var string = {
  identify: (value) => typeof value === "string",
  default: true,
  tag: "tag:yaml.org,2002:str",
  resolve: (str2) => str2,
  stringify(item, ctx, onComment, onChompKeep) {
    ctx = Object.assign({ actualString: true }, ctx);
    return stringifyString(item, ctx, onComment, onChompKeep);
  }
};

// ../../../frontend/node_modules/yaml/browser/dist/schema/common/null.js
var nullTag = {
  identify: (value) => value == null,
  createNode: () => new Scalar(null),
  default: true,
  tag: "tag:yaml.org,2002:null",
  test: /^(?:~|[Nn]ull|NULL)?$/,
  resolve: () => new Scalar(null),
  stringify: ({ source }, ctx) => typeof source === "string" && nullTag.test.test(source) ? source : ctx.options.nullStr
};

// ../../../frontend/node_modules/yaml/browser/dist/schema/core/bool.js
var boolTag = {
  identify: (value) => typeof value === "boolean",
  default: true,
  tag: "tag:yaml.org,2002:bool",
  test: /^(?:[Tt]rue|TRUE|[Ff]alse|FALSE)$/,
  resolve: (str2) => new Scalar(str2[0] === "t" || str2[0] === "T"),
  stringify({ source, value }, ctx) {
    if (source && boolTag.test.test(source)) {
      const sv = source[0] === "t" || source[0] === "T";
      if (value === sv)
        return source;
    }
    return value ? ctx.options.trueStr : ctx.options.falseStr;
  }
};

// ../../../frontend/node_modules/yaml/browser/dist/stringify/stringifyNumber.js
function stringifyNumber({ format, minFractionDigits, tag, value }) {
  if (typeof value === "bigint")
    return String(value);
  const num = typeof value === "number" ? value : Number(value);
  if (!isFinite(num))
    return isNaN(num) ? ".nan" : num < 0 ? "-.inf" : ".inf";
  let n = Object.is(value, -0) ? "-0" : JSON.stringify(value);
  if (!format && minFractionDigits && (!tag || tag === "tag:yaml.org,2002:float") && /^-?\d/.test(n) && !n.includes("e")) {
    let i = n.indexOf(".");
    if (i < 0) {
      i = n.length;
      n += ".";
    }
    let d = minFractionDigits - (n.length - i - 1);
    while (d-- > 0)
      n += "0";
  }
  return n;
}

// ../../../frontend/node_modules/yaml/browser/dist/schema/core/float.js
var floatNaN = {
  identify: (value) => typeof value === "number",
  default: true,
  tag: "tag:yaml.org,2002:float",
  test: /^(?:[-+]?\.(?:inf|Inf|INF)|\.nan|\.NaN|\.NAN)$/,
  resolve: (str2) => str2.slice(-3).toLowerCase() === "nan" ? NaN : str2[0] === "-" ? Number.NEGATIVE_INFINITY : Number.POSITIVE_INFINITY,
  stringify: stringifyNumber
};
var floatExp = {
  identify: (value) => typeof value === "number",
  default: true,
  tag: "tag:yaml.org,2002:float",
  format: "EXP",
  test: /^[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)[eE][-+]?[0-9]+$/,
  resolve: (str2) => parseFloat(str2),
  stringify(node) {
    const num = Number(node.value);
    return isFinite(num) ? num.toExponential() : stringifyNumber(node);
  }
};
var float = {
  identify: (value) => typeof value === "number",
  default: true,
  tag: "tag:yaml.org,2002:float",
  test: /^[-+]?(?:\.[0-9]+|[0-9]+\.[0-9]*)$/,
  resolve(str2) {
    const node = new Scalar(parseFloat(str2));
    const dot = str2.indexOf(".");
    if (dot !== -1 && str2[str2.length - 1] === "0")
      node.minFractionDigits = str2.length - dot - 1;
    return node;
  },
  stringify: stringifyNumber
};

// ../../../frontend/node_modules/yaml/browser/dist/schema/core/int.js
var intIdentify = (value) => typeof value === "bigint" || Number.isInteger(value);
var intResolve = (str2, offset, radix, { intAsBigInt }) => intAsBigInt ? BigInt(str2) : parseInt(str2.substring(offset), radix);
function intStringify(node, radix, prefix) {
  const { value } = node;
  if (intIdentify(value) && value >= 0)
    return prefix + value.toString(radix);
  return stringifyNumber(node);
}
var intOct = {
  identify: (value) => intIdentify(value) && value >= 0,
  default: true,
  tag: "tag:yaml.org,2002:int",
  format: "OCT",
  test: /^0o[0-7]+$/,
  resolve: (str2, _onError, opt) => intResolve(str2, 2, 8, opt),
  stringify: (node) => intStringify(node, 8, "0o")
};
var int = {
  identify: intIdentify,
  default: true,
  tag: "tag:yaml.org,2002:int",
  test: /^[-+]?[0-9]+$/,
  resolve: (str2, _onError, opt) => intResolve(str2, 0, 10, opt),
  stringify: stringifyNumber
};
var intHex = {
  identify: (value) => intIdentify(value) && value >= 0,
  default: true,
  tag: "tag:yaml.org,2002:int",
  format: "HEX",
  test: /^0x[0-9a-fA-F]+$/,
  resolve: (str2, _onError, opt) => intResolve(str2, 2, 16, opt),
  stringify: (node) => intStringify(node, 16, "0x")
};

// ../../../frontend/node_modules/yaml/browser/dist/schema/core/schema.js
var schema = [
  map,
  seq,
  string,
  nullTag,
  boolTag,
  intOct,
  int,
  intHex,
  floatNaN,
  floatExp,
  float
];

// ../../../frontend/node_modules/yaml/browser/dist/schema/json/schema.js
function intIdentify2(value) {
  return typeof value === "bigint" || Number.isInteger(value);
}
var stringifyJSON = ({ value }) => JSON.stringify(value);
var jsonScalars = [
  {
    identify: (value) => typeof value === "string",
    default: true,
    tag: "tag:yaml.org,2002:str",
    resolve: (str2) => str2,
    stringify: stringifyJSON
  },
  {
    identify: (value) => value == null,
    createNode: () => new Scalar(null),
    default: true,
    tag: "tag:yaml.org,2002:null",
    test: /^null$/,
    resolve: () => null,
    stringify: stringifyJSON
  },
  {
    identify: (value) => typeof value === "boolean",
    default: true,
    tag: "tag:yaml.org,2002:bool",
    test: /^true$|^false$/,
    resolve: (str2) => str2 === "true",
    stringify: stringifyJSON
  },
  {
    identify: intIdentify2,
    default: true,
    tag: "tag:yaml.org,2002:int",
    test: /^-?(?:0|[1-9][0-9]*)$/,
    resolve: (str2, _onError, { intAsBigInt }) => intAsBigInt ? BigInt(str2) : parseInt(str2, 10),
    stringify: ({ value }) => intIdentify2(value) ? value.toString() : JSON.stringify(value)
  },
  {
    identify: (value) => typeof value === "number",
    default: true,
    tag: "tag:yaml.org,2002:float",
    test: /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]*)?(?:[eE][-+]?[0-9]+)?$/,
    resolve: (str2) => parseFloat(str2),
    stringify: stringifyJSON
  }
];
var jsonError = {
  default: true,
  tag: "",
  test: /^/,
  resolve(str2, onError) {
    onError(`Unresolved plain scalar ${JSON.stringify(str2)}`);
    return str2;
  }
};
var schema2 = [map, seq].concat(jsonScalars, jsonError);

// ../../../frontend/node_modules/yaml/browser/dist/schema/yaml-1.1/binary.js
var binary = {
  identify: (value) => value instanceof Uint8Array,
  // Buffer inherits from Uint8Array
  default: false,
  tag: "tag:yaml.org,2002:binary",
  /**
   * Returns a Buffer in node and an Uint8Array in browsers
   *
   * To use the resulting buffer as an image, you'll want to do something like:
   *
   *   const blob = new Blob([buffer], { type: 'image/jpeg' })
   *   document.querySelector('#photo').src = URL.createObjectURL(blob)
   */
  resolve(src, onError) {
    if (typeof atob === "function") {
      const str2 = atob(src.replace(/[\n\r]/g, ""));
      const buffer = new Uint8Array(str2.length);
      for (let i = 0; i < str2.length; ++i)
        buffer[i] = str2.charCodeAt(i);
      return buffer;
    } else {
      onError("This environment does not support reading binary tags; either Buffer or atob is required");
      return src;
    }
  },
  stringify({ comment, type, value }, ctx, onComment, onChompKeep) {
    if (!value)
      return "";
    const buf = value;
    let str2;
    if (typeof btoa === "function") {
      let s = "";
      for (let i = 0; i < buf.length; ++i)
        s += String.fromCharCode(buf[i]);
      str2 = btoa(s);
    } else {
      throw new Error("This environment does not support writing binary tags; either Buffer or btoa is required");
    }
    type ?? (type = Scalar.BLOCK_LITERAL);
    if (type !== Scalar.QUOTE_DOUBLE) {
      const lineWidth = Math.max(ctx.options.lineWidth - ctx.indent.length, ctx.options.minContentWidth);
      const n = Math.ceil(str2.length / lineWidth);
      const lines = new Array(n);
      for (let i = 0, o = 0; i < n; ++i, o += lineWidth) {
        lines[i] = str2.substr(o, lineWidth);
      }
      str2 = lines.join(type === Scalar.BLOCK_LITERAL ? "\n" : " ");
    }
    return stringifyString({ comment, type, value: str2 }, ctx, onComment, onChompKeep);
  }
};

// ../../../frontend/node_modules/yaml/browser/dist/schema/yaml-1.1/pairs.js
function resolvePairs(seq2, onError) {
  if (isSeq(seq2)) {
    for (let i = 0; i < seq2.items.length; ++i) {
      let item = seq2.items[i];
      if (isPair(item))
        continue;
      else if (isMap(item)) {
        if (item.items.length > 1)
          onError("Each pair must have its own sequence indicator");
        const pair = item.items[0] || new Pair(new Scalar(null));
        if (item.commentBefore)
          pair.key.commentBefore = pair.key.commentBefore ? `${item.commentBefore}
${pair.key.commentBefore}` : item.commentBefore;
        if (item.comment) {
          const cn = pair.value ?? pair.key;
          cn.comment = cn.comment ? `${item.comment}
${cn.comment}` : item.comment;
        }
        item = pair;
      }
      seq2.items[i] = isPair(item) ? item : new Pair(item);
    }
  } else
    onError("Expected a sequence for this tag");
  return seq2;
}
function createPairs(schema4, iterable, ctx) {
  const { replacer } = ctx;
  const pairs2 = new YAMLSeq(schema4);
  pairs2.tag = "tag:yaml.org,2002:pairs";
  let i = 0;
  if (iterable && Symbol.iterator in Object(iterable))
    for (let it of iterable) {
      if (typeof replacer === "function")
        it = replacer.call(iterable, String(i++), it);
      let key, value;
      if (Array.isArray(it)) {
        if (it.length === 2) {
          key = it[0];
          value = it[1];
        } else
          throw new TypeError(`Expected [key, value] tuple: ${it}`);
      } else if (it && it instanceof Object) {
        const keys = Object.keys(it);
        if (keys.length === 1) {
          key = keys[0];
          value = it[key];
        } else {
          throw new TypeError(`Expected tuple with one key, not ${keys.length} keys`);
        }
      } else {
        key = it;
      }
      pairs2.items.push(createPair(key, value, ctx));
    }
  return pairs2;
}
var pairs = {
  collection: "seq",
  default: false,
  tag: "tag:yaml.org,2002:pairs",
  resolve: resolvePairs,
  createNode: createPairs
};

// ../../../frontend/node_modules/yaml/browser/dist/schema/yaml-1.1/omap.js
var YAMLOMap = class _YAMLOMap extends YAMLSeq {
  constructor() {
    super();
    this.add = YAMLMap.prototype.add.bind(this);
    this.delete = YAMLMap.prototype.delete.bind(this);
    this.get = YAMLMap.prototype.get.bind(this);
    this.has = YAMLMap.prototype.has.bind(this);
    this.set = YAMLMap.prototype.set.bind(this);
    this.tag = _YAMLOMap.tag;
  }
  /**
   * If `ctx` is given, the return type is actually `Map<unknown, unknown>`,
   * but TypeScript won't allow widening the signature of a child method.
   */
  toJSON(_, ctx) {
    if (!ctx)
      return super.toJSON(_);
    const map2 = /* @__PURE__ */ new Map();
    if (ctx?.onCreate)
      ctx.onCreate(map2);
    for (const pair of this.items) {
      let key, value;
      if (isPair(pair)) {
        key = toJS(pair.key, "", ctx);
        value = toJS(pair.value, key, ctx);
      } else {
        key = toJS(pair, "", ctx);
      }
      if (map2.has(key))
        throw new Error("Ordered maps must not include duplicate keys");
      map2.set(key, value);
    }
    return map2;
  }
  static from(schema4, iterable, ctx) {
    const pairs2 = createPairs(schema4, iterable, ctx);
    const omap2 = new this();
    omap2.items = pairs2.items;
    return omap2;
  }
};
YAMLOMap.tag = "tag:yaml.org,2002:omap";
var omap = {
  collection: "seq",
  identify: (value) => value instanceof Map,
  nodeClass: YAMLOMap,
  default: false,
  tag: "tag:yaml.org,2002:omap",
  resolve(seq2, onError) {
    const pairs2 = resolvePairs(seq2, onError);
    const seenKeys = [];
    for (const { key } of pairs2.items) {
      if (isScalar(key)) {
        if (seenKeys.includes(key.value)) {
          onError(`Ordered maps must not include duplicate keys: ${key.value}`);
        } else {
          seenKeys.push(key.value);
        }
      }
    }
    return Object.assign(new YAMLOMap(), pairs2);
  },
  createNode: (schema4, iterable, ctx) => YAMLOMap.from(schema4, iterable, ctx)
};

// ../../../frontend/node_modules/yaml/browser/dist/schema/yaml-1.1/bool.js
function boolStringify({ value, source }, ctx) {
  const boolObj = value ? trueTag : falseTag;
  if (source && boolObj.test.test(source))
    return source;
  return value ? ctx.options.trueStr : ctx.options.falseStr;
}
var trueTag = {
  identify: (value) => value === true,
  default: true,
  tag: "tag:yaml.org,2002:bool",
  test: /^(?:Y|y|[Yy]es|YES|[Tt]rue|TRUE|[Oo]n|ON)$/,
  resolve: () => new Scalar(true),
  stringify: boolStringify
};
var falseTag = {
  identify: (value) => value === false,
  default: true,
  tag: "tag:yaml.org,2002:bool",
  test: /^(?:N|n|[Nn]o|NO|[Ff]alse|FALSE|[Oo]ff|OFF)$/,
  resolve: () => new Scalar(false),
  stringify: boolStringify
};

// ../../../frontend/node_modules/yaml/browser/dist/schema/yaml-1.1/float.js
var floatNaN2 = {
  identify: (value) => typeof value === "number",
  default: true,
  tag: "tag:yaml.org,2002:float",
  test: /^(?:[-+]?\.(?:inf|Inf|INF)|\.nan|\.NaN|\.NAN)$/,
  resolve: (str2) => str2.slice(-3).toLowerCase() === "nan" ? NaN : str2[0] === "-" ? Number.NEGATIVE_INFINITY : Number.POSITIVE_INFINITY,
  stringify: stringifyNumber
};
var floatExp2 = {
  identify: (value) => typeof value === "number",
  default: true,
  tag: "tag:yaml.org,2002:float",
  format: "EXP",
  test: /^[-+]?(?:[0-9][0-9_]*)?(?:\.[0-9_]*)?[eE][-+]?[0-9]+$/,
  resolve: (str2) => parseFloat(str2.replace(/_/g, "")),
  stringify(node) {
    const num = Number(node.value);
    return isFinite(num) ? num.toExponential() : stringifyNumber(node);
  }
};
var float2 = {
  identify: (value) => typeof value === "number",
  default: true,
  tag: "tag:yaml.org,2002:float",
  test: /^[-+]?(?:[0-9][0-9_]*)?\.[0-9_]*$/,
  resolve(str2) {
    const node = new Scalar(parseFloat(str2.replace(/_/g, "")));
    const dot = str2.indexOf(".");
    if (dot !== -1) {
      const f = str2.substring(dot + 1).replace(/_/g, "");
      if (f[f.length - 1] === "0")
        node.minFractionDigits = f.length;
    }
    return node;
  },
  stringify: stringifyNumber
};

// ../../../frontend/node_modules/yaml/browser/dist/schema/yaml-1.1/int.js
var intIdentify3 = (value) => typeof value === "bigint" || Number.isInteger(value);
function intResolve2(str2, offset, radix, { intAsBigInt }) {
  const sign = str2[0];
  if (sign === "-" || sign === "+")
    offset += 1;
  str2 = str2.substring(offset).replace(/_/g, "");
  if (intAsBigInt) {
    switch (radix) {
      case 2:
        str2 = `0b${str2}`;
        break;
      case 8:
        str2 = `0o${str2}`;
        break;
      case 16:
        str2 = `0x${str2}`;
        break;
    }
    const n2 = BigInt(str2);
    return sign === "-" ? BigInt(-1) * n2 : n2;
  }
  const n = parseInt(str2, radix);
  return sign === "-" ? -1 * n : n;
}
function intStringify2(node, radix, prefix) {
  const { value } = node;
  if (intIdentify3(value)) {
    const str2 = value.toString(radix);
    return value < 0 ? "-" + prefix + str2.substr(1) : prefix + str2;
  }
  return stringifyNumber(node);
}
var intBin = {
  identify: intIdentify3,
  default: true,
  tag: "tag:yaml.org,2002:int",
  format: "BIN",
  test: /^[-+]?0b[0-1_]+$/,
  resolve: (str2, _onError, opt) => intResolve2(str2, 2, 2, opt),
  stringify: (node) => intStringify2(node, 2, "0b")
};
var intOct2 = {
  identify: intIdentify3,
  default: true,
  tag: "tag:yaml.org,2002:int",
  format: "OCT",
  test: /^[-+]?0[0-7_]+$/,
  resolve: (str2, _onError, opt) => intResolve2(str2, 1, 8, opt),
  stringify: (node) => intStringify2(node, 8, "0")
};
var int2 = {
  identify: intIdentify3,
  default: true,
  tag: "tag:yaml.org,2002:int",
  test: /^[-+]?[0-9][0-9_]*$/,
  resolve: (str2, _onError, opt) => intResolve2(str2, 0, 10, opt),
  stringify: stringifyNumber
};
var intHex2 = {
  identify: intIdentify3,
  default: true,
  tag: "tag:yaml.org,2002:int",
  format: "HEX",
  test: /^[-+]?0x[0-9a-fA-F_]+$/,
  resolve: (str2, _onError, opt) => intResolve2(str2, 2, 16, opt),
  stringify: (node) => intStringify2(node, 16, "0x")
};

// ../../../frontend/node_modules/yaml/browser/dist/schema/yaml-1.1/set.js
var YAMLSet = class _YAMLSet extends YAMLMap {
  constructor(schema4) {
    super(schema4);
    this.tag = _YAMLSet.tag;
  }
  add(key) {
    let pair;
    if (isPair(key))
      pair = key;
    else if (key && typeof key === "object" && "key" in key && "value" in key && key.value === null)
      pair = new Pair(key.key, null);
    else
      pair = new Pair(key, null);
    const prev = findPair(this.items, pair.key);
    if (!prev)
      this.items.push(pair);
  }
  /**
   * If `keepPair` is `true`, returns the Pair matching `key`.
   * Otherwise, returns the value of that Pair's key.
   */
  get(key, keepPair) {
    const pair = findPair(this.items, key);
    return !keepPair && isPair(pair) ? isScalar(pair.key) ? pair.key.value : pair.key : pair;
  }
  set(key, value) {
    if (typeof value !== "boolean")
      throw new Error(`Expected boolean value for set(key, value) in a YAML set, not ${typeof value}`);
    const prev = findPair(this.items, key);
    if (prev && !value) {
      this.items.splice(this.items.indexOf(prev), 1);
    } else if (!prev && value) {
      this.items.push(new Pair(key));
    }
  }
  toJSON(_, ctx) {
    return super.toJSON(_, ctx, Set);
  }
  toString(ctx, onComment, onChompKeep) {
    if (!ctx)
      return JSON.stringify(this);
    if (this.hasAllNullValues(true))
      return super.toString(Object.assign({}, ctx, { allNullValues: true }), onComment, onChompKeep);
    else
      throw new Error("Set items must all have null values");
  }
  static from(schema4, iterable, ctx) {
    const { replacer } = ctx;
    const set2 = new this(schema4);
    if (iterable && Symbol.iterator in Object(iterable))
      for (let value of iterable) {
        if (typeof replacer === "function")
          value = replacer.call(iterable, value, value);
        set2.items.push(createPair(value, null, ctx));
      }
    return set2;
  }
};
YAMLSet.tag = "tag:yaml.org,2002:set";
var set = {
  collection: "map",
  identify: (value) => value instanceof Set,
  nodeClass: YAMLSet,
  default: false,
  tag: "tag:yaml.org,2002:set",
  createNode: (schema4, iterable, ctx) => YAMLSet.from(schema4, iterable, ctx),
  resolve(map2, onError) {
    if (isMap(map2)) {
      if (map2.hasAllNullValues(true))
        return Object.assign(new YAMLSet(), map2);
      else
        onError("Set items must all have null values");
    } else
      onError("Expected a mapping for this tag");
    return map2;
  }
};

// ../../../frontend/node_modules/yaml/browser/dist/schema/yaml-1.1/timestamp.js
function parseSexagesimal(str2, asBigInt) {
  const sign = str2[0];
  const parts = sign === "-" || sign === "+" ? str2.substring(1) : str2;
  const num = (n) => asBigInt ? BigInt(n) : Number(n);
  const res = parts.replace(/_/g, "").split(":").reduce((res2, p) => res2 * num(60) + num(p), num(0));
  return sign === "-" ? num(-1) * res : res;
}
function stringifySexagesimal(node) {
  let { value } = node;
  let num = (n) => n;
  if (typeof value === "bigint")
    num = (n) => BigInt(n);
  else if (isNaN(value) || !isFinite(value))
    return stringifyNumber(node);
  let sign = "";
  if (value < 0) {
    sign = "-";
    value *= num(-1);
  }
  const _60 = num(60);
  const parts = [value % _60];
  if (value < 60) {
    parts.unshift(0);
  } else {
    value = (value - parts[0]) / _60;
    parts.unshift(value % _60);
    if (value >= 60) {
      value = (value - parts[0]) / _60;
      parts.unshift(value);
    }
  }
  return sign + parts.map((n) => String(n).padStart(2, "0")).join(":").replace(/000000\d*$/, "");
}
var intTime = {
  identify: (value) => typeof value === "bigint" || Number.isInteger(value),
  default: true,
  tag: "tag:yaml.org,2002:int",
  format: "TIME",
  test: /^[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+$/,
  resolve: (str2, _onError, { intAsBigInt }) => parseSexagesimal(str2, intAsBigInt),
  stringify: stringifySexagesimal
};
var floatTime = {
  identify: (value) => typeof value === "number",
  default: true,
  tag: "tag:yaml.org,2002:float",
  format: "TIME",
  test: /^[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*$/,
  resolve: (str2) => parseSexagesimal(str2, false),
  stringify: stringifySexagesimal
};
var timestamp = {
  identify: (value) => value instanceof Date,
  default: true,
  tag: "tag:yaml.org,2002:timestamp",
  // If the time zone is omitted, the timestamp is assumed to be specified in UTC. The time part
  // may be omitted altogether, resulting in a date format. In such a case, the time part is
  // assumed to be 00:00:00Z (start of day, UTC).
  test: RegExp("^([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})(?:(?:t|T|[ \\t]+)([0-9]{1,2}):([0-9]{1,2}):([0-9]{1,2}(\\.[0-9]+)?)(?:[ \\t]*(Z|[-+][012]?[0-9](?::[0-9]{2})?))?)?$"),
  resolve(str2) {
    const match = str2.match(timestamp.test);
    if (!match)
      throw new Error("!!timestamp expects a date, starting with yyyy-mm-dd");
    const [, year, month, day2, hour, minute, second] = match.map(Number);
    const millisec = match[7] ? Number((match[7] + "00").substr(1, 3)) : 0;
    let date = Date.UTC(year, month - 1, day2, hour || 0, minute || 0, second || 0, millisec);
    const tz = match[8];
    if (tz && tz !== "Z") {
      let d = parseSexagesimal(tz, false);
      if (Math.abs(d) < 30)
        d *= 60;
      date -= 6e4 * d;
    }
    return new Date(date);
  },
  stringify: ({ value }) => value?.toISOString().replace(/(T00:00:00)?\.000Z$/, "") ?? ""
};

// ../../../frontend/node_modules/yaml/browser/dist/schema/yaml-1.1/schema.js
var schema3 = [
  map,
  seq,
  string,
  nullTag,
  trueTag,
  falseTag,
  intBin,
  intOct2,
  int2,
  intHex2,
  floatNaN2,
  floatExp2,
  float2,
  binary,
  merge,
  omap,
  pairs,
  set,
  intTime,
  floatTime,
  timestamp
];

// ../../../frontend/node_modules/yaml/browser/dist/schema/tags.js
var schemas = /* @__PURE__ */ new Map([
  ["core", schema],
  ["failsafe", [map, seq, string]],
  ["json", schema2],
  ["yaml11", schema3],
  ["yaml-1.1", schema3]
]);
var tagsByName = {
  binary,
  bool: boolTag,
  float,
  floatExp,
  floatNaN,
  floatTime,
  int,
  intHex,
  intOct,
  intTime,
  map,
  merge,
  null: nullTag,
  omap,
  pairs,
  seq,
  set,
  timestamp
};
var coreKnownTags = {
  "tag:yaml.org,2002:binary": binary,
  "tag:yaml.org,2002:merge": merge,
  "tag:yaml.org,2002:omap": omap,
  "tag:yaml.org,2002:pairs": pairs,
  "tag:yaml.org,2002:set": set,
  "tag:yaml.org,2002:timestamp": timestamp
};
function getTags(customTags, schemaName, addMergeTag) {
  const schemaTags = schemas.get(schemaName);
  if (schemaTags && !customTags) {
    return addMergeTag && !schemaTags.includes(merge) ? schemaTags.concat(merge) : schemaTags.slice();
  }
  let tags = schemaTags;
  if (!tags) {
    if (Array.isArray(customTags))
      tags = [];
    else {
      const keys = Array.from(schemas.keys()).filter((key) => key !== "yaml11").map((key) => JSON.stringify(key)).join(", ");
      throw new Error(`Unknown schema "${schemaName}"; use one of ${keys} or define customTags array`);
    }
  }
  if (Array.isArray(customTags)) {
    for (const tag of customTags)
      tags = tags.concat(tag);
  } else if (typeof customTags === "function") {
    tags = customTags(tags.slice());
  }
  if (addMergeTag)
    tags = tags.concat(merge);
  return tags.reduce((tags2, tag) => {
    const tagObj = typeof tag === "string" ? tagsByName[tag] : tag;
    if (!tagObj) {
      const tagName = JSON.stringify(tag);
      const keys = Object.keys(tagsByName).map((key) => JSON.stringify(key)).join(", ");
      throw new Error(`Unknown custom tag ${tagName}; use one of ${keys}`);
    }
    if (!tags2.includes(tagObj))
      tags2.push(tagObj);
    return tags2;
  }, []);
}

// ../../../frontend/node_modules/yaml/browser/dist/schema/Schema.js
var sortMapEntriesByKey = (a, b) => a.key < b.key ? -1 : a.key > b.key ? 1 : 0;
var Schema = class _Schema {
  constructor({ compat, customTags, merge: merge2, resolveKnownTags, schema: schema4, sortMapEntries, toStringDefaults }) {
    this.compat = Array.isArray(compat) ? getTags(compat, "compat") : compat ? getTags(null, compat) : null;
    this.name = typeof schema4 === "string" && schema4 || "core";
    this.knownTags = resolveKnownTags ? coreKnownTags : {};
    this.tags = getTags(customTags, this.name, merge2);
    this.toStringOptions = toStringDefaults ?? null;
    Object.defineProperty(this, MAP, { value: map });
    Object.defineProperty(this, SCALAR, { value: string });
    Object.defineProperty(this, SEQ, { value: seq });
    this.sortMapEntries = typeof sortMapEntries === "function" ? sortMapEntries : sortMapEntries === true ? sortMapEntriesByKey : null;
  }
  clone() {
    const copy = Object.create(_Schema.prototype, Object.getOwnPropertyDescriptors(this));
    copy.tags = this.tags.slice();
    return copy;
  }
};

// ../../../frontend/node_modules/yaml/browser/dist/stringify/stringifyDocument.js
function stringifyDocument(doc, options) {
  const lines = [];
  let hasDirectives = options.directives === true;
  if (options.directives !== false && doc.directives) {
    const dir = doc.directives.toString(doc);
    if (dir) {
      lines.push(dir);
      hasDirectives = true;
    } else if (doc.directives.docStart)
      hasDirectives = true;
  }
  if (hasDirectives)
    lines.push("---");
  const ctx = createStringifyContext(doc, options);
  const { commentString } = ctx.options;
  if (doc.commentBefore) {
    if (lines.length !== 1)
      lines.unshift("");
    const cs = commentString(doc.commentBefore);
    lines.unshift(indentComment(cs, ""));
  }
  let chompKeep = false;
  let contentComment = null;
  if (doc.contents) {
    if (isNode(doc.contents)) {
      if (doc.contents.spaceBefore && hasDirectives)
        lines.push("");
      if (doc.contents.commentBefore) {
        const cs = commentString(doc.contents.commentBefore);
        lines.push(indentComment(cs, ""));
      }
      ctx.forceBlockIndent = !!doc.comment;
      contentComment = doc.contents.comment;
    }
    const onChompKeep = contentComment ? void 0 : () => chompKeep = true;
    let body = stringify(doc.contents, ctx, () => contentComment = null, onChompKeep);
    if (contentComment)
      body += lineComment(body, "", commentString(contentComment));
    if ((body[0] === "|" || body[0] === ">") && lines[lines.length - 1] === "---") {
      lines[lines.length - 1] = `--- ${body}`;
    } else
      lines.push(body);
  } else {
    lines.push(stringify(doc.contents, ctx));
  }
  if (doc.directives?.docEnd) {
    if (doc.comment) {
      const cs = commentString(doc.comment);
      if (cs.includes("\n")) {
        lines.push("...");
        lines.push(indentComment(cs, ""));
      } else {
        lines.push(`... ${cs}`);
      }
    } else {
      lines.push("...");
    }
  } else {
    let dc = doc.comment;
    if (dc && chompKeep)
      dc = dc.replace(/^\n+/, "");
    if (dc) {
      if ((!chompKeep || contentComment) && lines[lines.length - 1] !== "")
        lines.push("");
      lines.push(indentComment(commentString(dc), ""));
    }
  }
  return lines.join("\n") + "\n";
}

// ../../../frontend/node_modules/yaml/browser/dist/doc/Document.js
var Document = class _Document {
  constructor(value, replacer, options) {
    this.commentBefore = null;
    this.comment = null;
    this.errors = [];
    this.warnings = [];
    Object.defineProperty(this, NODE_TYPE, { value: DOC });
    let _replacer = null;
    if (typeof replacer === "function" || Array.isArray(replacer)) {
      _replacer = replacer;
    } else if (options === void 0 && replacer) {
      options = replacer;
      replacer = void 0;
    }
    const opt = Object.assign({
      intAsBigInt: false,
      keepSourceTokens: false,
      logLevel: "warn",
      prettyErrors: true,
      strict: true,
      stringKeys: false,
      uniqueKeys: true,
      version: "1.2"
    }, options);
    this.options = opt;
    let { version } = opt;
    if (options?._directives) {
      this.directives = options._directives.atDocument();
      if (this.directives.yaml.explicit)
        version = this.directives.yaml.version;
    } else
      this.directives = new Directives({ version });
    this.setSchema(version, options);
    this.contents = value === void 0 ? null : this.createNode(value, _replacer, options);
  }
  /**
   * Create a deep copy of this Document and its contents.
   *
   * Custom Node values that inherit from `Object` still refer to their original instances.
   */
  clone() {
    const copy = Object.create(_Document.prototype, {
      [NODE_TYPE]: { value: DOC }
    });
    copy.commentBefore = this.commentBefore;
    copy.comment = this.comment;
    copy.errors = this.errors.slice();
    copy.warnings = this.warnings.slice();
    copy.options = Object.assign({}, this.options);
    if (this.directives)
      copy.directives = this.directives.clone();
    copy.schema = this.schema.clone();
    copy.contents = isNode(this.contents) ? this.contents.clone(copy.schema) : this.contents;
    if (this.range)
      copy.range = this.range.slice();
    return copy;
  }
  /** Adds a value to the document. */
  add(value) {
    if (assertCollection(this.contents))
      this.contents.add(value);
  }
  /** Adds a value to the document. */
  addIn(path, value) {
    if (assertCollection(this.contents))
      this.contents.addIn(path, value);
  }
  /**
   * Create a new `Alias` node, ensuring that the target `node` has the required anchor.
   *
   * If `node` already has an anchor, `name` is ignored.
   * Otherwise, the `node.anchor` value will be set to `name`,
   * or if an anchor with that name is already present in the document,
   * `name` will be used as a prefix for a new unique anchor.
   * If `name` is undefined, the generated anchor will use 'a' as a prefix.
   */
  createAlias(node, name) {
    if (!node.anchor) {
      const prev = anchorNames(this);
      node.anchor = // eslint-disable-next-line @typescript-eslint/prefer-nullish-coalescing
      !name || prev.has(name) ? findNewAnchor(name || "a", prev) : name;
    }
    return new Alias(node.anchor);
  }
  createNode(value, replacer, options) {
    let _replacer = void 0;
    if (typeof replacer === "function") {
      value = replacer.call({ "": value }, "", value);
      _replacer = replacer;
    } else if (Array.isArray(replacer)) {
      const keyToStr = (v) => typeof v === "number" || v instanceof String || v instanceof Number;
      const asStr = replacer.filter(keyToStr).map(String);
      if (asStr.length > 0)
        replacer = replacer.concat(asStr);
      _replacer = replacer;
    } else if (options === void 0 && replacer) {
      options = replacer;
      replacer = void 0;
    }
    const { aliasDuplicateObjects, anchorPrefix, flow, keepUndefined, onTagObj, tag } = options ?? {};
    const { onAnchor, setAnchors, sourceObjects } = createNodeAnchors(
      this,
      // eslint-disable-next-line @typescript-eslint/prefer-nullish-coalescing
      anchorPrefix || "a"
    );
    const ctx = {
      aliasDuplicateObjects: aliasDuplicateObjects ?? true,
      keepUndefined: keepUndefined ?? false,
      onAnchor,
      onTagObj,
      replacer: _replacer,
      schema: this.schema,
      sourceObjects
    };
    const node = createNode(value, tag, ctx);
    if (flow && isCollection(node))
      node.flow = true;
    setAnchors();
    return node;
  }
  /**
   * Convert a key and a value into a `Pair` using the current schema,
   * recursively wrapping all values as `Scalar` or `Collection` nodes.
   */
  createPair(key, value, options = {}) {
    const k = this.createNode(key, null, options);
    const v = this.createNode(value, null, options);
    return new Pair(k, v);
  }
  /**
   * Removes a value from the document.
   * @returns `true` if the item was found and removed.
   */
  delete(key) {
    return assertCollection(this.contents) ? this.contents.delete(key) : false;
  }
  /**
   * Removes a value from the document.
   * @returns `true` if the item was found and removed.
   */
  deleteIn(path) {
    if (isEmptyPath(path)) {
      if (this.contents == null)
        return false;
      this.contents = null;
      return true;
    }
    return assertCollection(this.contents) ? this.contents.deleteIn(path) : false;
  }
  /**
   * Returns item at `key`, or `undefined` if not found. By default unwraps
   * scalar values from their surrounding node; to disable set `keepScalar` to
   * `true` (collections are always returned intact).
   */
  get(key, keepScalar) {
    return isCollection(this.contents) ? this.contents.get(key, keepScalar) : void 0;
  }
  /**
   * Returns item at `path`, or `undefined` if not found. By default unwraps
   * scalar values from their surrounding node; to disable set `keepScalar` to
   * `true` (collections are always returned intact).
   */
  getIn(path, keepScalar) {
    if (isEmptyPath(path))
      return !keepScalar && isScalar(this.contents) ? this.contents.value : this.contents;
    return isCollection(this.contents) ? this.contents.getIn(path, keepScalar) : void 0;
  }
  /**
   * Checks if the document includes a value with the key `key`.
   */
  has(key) {
    return isCollection(this.contents) ? this.contents.has(key) : false;
  }
  /**
   * Checks if the document includes a value at `path`.
   */
  hasIn(path) {
    if (isEmptyPath(path))
      return this.contents !== void 0;
    return isCollection(this.contents) ? this.contents.hasIn(path) : false;
  }
  /**
   * Sets a value in this document. For `!!set`, `value` needs to be a
   * boolean to add/remove the item from the set.
   */
  set(key, value) {
    if (this.contents == null) {
      this.contents = collectionFromPath(this.schema, [key], value);
    } else if (assertCollection(this.contents)) {
      this.contents.set(key, value);
    }
  }
  /**
   * Sets a value in this document. For `!!set`, `value` needs to be a
   * boolean to add/remove the item from the set.
   */
  setIn(path, value) {
    if (isEmptyPath(path)) {
      this.contents = value;
    } else if (this.contents == null) {
      this.contents = collectionFromPath(this.schema, Array.from(path), value);
    } else if (assertCollection(this.contents)) {
      this.contents.setIn(path, value);
    }
  }
  /**
   * Change the YAML version and schema used by the document.
   * A `null` version disables support for directives, explicit tags, anchors, and aliases.
   * It also requires the `schema` option to be given as a `Schema` instance value.
   *
   * Overrides all previously set schema options.
   */
  setSchema(version, options = {}) {
    if (typeof version === "number")
      version = String(version);
    let opt;
    switch (version) {
      case "1.1":
        if (this.directives)
          this.directives.yaml.version = "1.1";
        else
          this.directives = new Directives({ version: "1.1" });
        opt = { resolveKnownTags: false, schema: "yaml-1.1" };
        break;
      case "1.2":
      case "next":
        if (this.directives)
          this.directives.yaml.version = version;
        else
          this.directives = new Directives({ version });
        opt = { resolveKnownTags: true, schema: "core" };
        break;
      case null:
        if (this.directives)
          delete this.directives;
        opt = null;
        break;
      default: {
        const sv = JSON.stringify(version);
        throw new Error(`Expected '1.1', '1.2' or null as first argument, but found: ${sv}`);
      }
    }
    if (options.schema instanceof Object)
      this.schema = options.schema;
    else if (opt)
      this.schema = new Schema(Object.assign(opt, options));
    else
      throw new Error(`With a null YAML version, the { schema: Schema } option is required`);
  }
  // json & jsonArg are only used from toJSON()
  toJS({ json, jsonArg, mapAsMap, maxAliasCount, onAnchor, reviver } = {}) {
    const ctx = {
      anchors: /* @__PURE__ */ new Map(),
      doc: this,
      keep: !json,
      mapAsMap: mapAsMap === true,
      mapKeyWarned: false,
      maxAliasCount: typeof maxAliasCount === "number" ? maxAliasCount : 100
    };
    const res = toJS(this.contents, jsonArg ?? "", ctx);
    if (typeof onAnchor === "function")
      for (const { count, res: res2 } of ctx.anchors.values())
        onAnchor(res2, count);
    return typeof reviver === "function" ? applyReviver(reviver, { "": res }, "", res) : res;
  }
  /**
   * A JSON representation of the document `contents`.
   *
   * @param jsonArg Used by `JSON.stringify` to indicate the array index or
   *   property name.
   */
  toJSON(jsonArg, onAnchor) {
    return this.toJS({ json: true, jsonArg, mapAsMap: false, onAnchor });
  }
  /** A YAML representation of the document. */
  toString(options = {}) {
    if (this.errors.length > 0)
      throw new Error("Document with errors cannot be stringified");
    if ("indent" in options && (!Number.isInteger(options.indent) || Number(options.indent) <= 0)) {
      const s = JSON.stringify(options.indent);
      throw new Error(`"indent" option must be a positive integer, not ${s}`);
    }
    return stringifyDocument(this, options);
  }
};
function assertCollection(contents) {
  if (isCollection(contents))
    return true;
  throw new Error("Expected a YAML collection as document contents");
}

// ../../../frontend/node_modules/yaml/browser/dist/errors.js
var YAMLError = class extends Error {
  constructor(name, pos, code, message) {
    super();
    this.name = name;
    this.code = code;
    this.message = message;
    this.pos = pos;
  }
};
var YAMLParseError = class extends YAMLError {
  constructor(pos, code, message) {
    super("YAMLParseError", pos, code, message);
  }
};
var YAMLWarning = class extends YAMLError {
  constructor(pos, code, message) {
    super("YAMLWarning", pos, code, message);
  }
};
var prettifyError = (src, lc) => (error) => {
  if (error.pos[0] === -1)
    return;
  error.linePos = error.pos.map((pos) => lc.linePos(pos));
  const { line, col } = error.linePos[0];
  error.message += ` at line ${line}, column ${col}`;
  let ci = col - 1;
  let lineStr = src.substring(lc.lineStarts[line - 1], lc.lineStarts[line]).replace(/[\n\r]+$/, "");
  if (ci >= 60 && lineStr.length > 80) {
    const trimStart = Math.min(ci - 39, lineStr.length - 79);
    lineStr = "\u2026" + lineStr.substring(trimStart);
    ci -= trimStart - 1;
  }
  if (lineStr.length > 80)
    lineStr = lineStr.substring(0, 79) + "\u2026";
  if (line > 1 && /^ *$/.test(lineStr.substring(0, ci))) {
    let prev = src.substring(lc.lineStarts[line - 2], lc.lineStarts[line - 1]);
    if (prev.length > 80)
      prev = prev.substring(0, 79) + "\u2026\n";
    lineStr = prev + lineStr;
  }
  if (/[^ ]/.test(lineStr)) {
    let count = 1;
    const end = error.linePos[1];
    if (end?.line === line && end.col > col) {
      count = Math.max(1, Math.min(end.col - col, 80 - ci));
    }
    const pointer = " ".repeat(ci) + "^".repeat(count);
    error.message += `:

${lineStr}
${pointer}
`;
  }
};

// ../../../frontend/node_modules/yaml/browser/dist/compose/resolve-props.js
function resolveProps(tokens, { flow, indicator, next, offset, onError, parentIndent, startOnNewline }) {
  let spaceBefore = false;
  let atNewline = startOnNewline;
  let hasSpace = startOnNewline;
  let comment = "";
  let commentSep = "";
  let hasNewline = false;
  let reqSpace = false;
  let tab = null;
  let anchor = null;
  let tag = null;
  let newlineAfterProp = null;
  let comma = null;
  let found = null;
  let start = null;
  for (const token of tokens) {
    if (reqSpace) {
      if (token.type !== "space" && token.type !== "newline" && token.type !== "comma")
        onError(token.offset, "MISSING_CHAR", "Tags and anchors must be separated from the next token by white space");
      reqSpace = false;
    }
    if (tab) {
      if (atNewline && token.type !== "comment" && token.type !== "newline") {
        onError(tab, "TAB_AS_INDENT", "Tabs are not allowed as indentation");
      }
      tab = null;
    }
    switch (token.type) {
      case "space":
        if (!flow && (indicator !== "doc-start" || next?.type !== "flow-collection") && token.source.includes("	")) {
          tab = token;
        }
        hasSpace = true;
        break;
      case "comment": {
        if (!hasSpace)
          onError(token, "MISSING_CHAR", "Comments must be separated from other tokens by white space characters");
        const cb = token.source.substring(1) || " ";
        if (!comment)
          comment = cb;
        else
          comment += commentSep + cb;
        commentSep = "";
        atNewline = false;
        break;
      }
      case "newline":
        if (atNewline) {
          if (comment)
            comment += token.source;
          else if (!found || indicator !== "seq-item-ind")
            spaceBefore = true;
        } else
          commentSep += token.source;
        atNewline = true;
        hasNewline = true;
        if (anchor || tag)
          newlineAfterProp = token;
        hasSpace = true;
        break;
      case "anchor":
        if (anchor)
          onError(token, "MULTIPLE_ANCHORS", "A node can have at most one anchor");
        if (token.source.endsWith(":"))
          onError(token.offset + token.source.length - 1, "BAD_ALIAS", "Anchor ending in : is ambiguous", true);
        anchor = token;
        start ?? (start = token.offset);
        atNewline = false;
        hasSpace = false;
        reqSpace = true;
        break;
      case "tag": {
        if (tag)
          onError(token, "MULTIPLE_TAGS", "A node can have at most one tag");
        tag = token;
        start ?? (start = token.offset);
        atNewline = false;
        hasSpace = false;
        reqSpace = true;
        break;
      }
      case indicator:
        if (anchor || tag)
          onError(token, "BAD_PROP_ORDER", `Anchors and tags must be after the ${token.source} indicator`);
        if (found)
          onError(token, "UNEXPECTED_TOKEN", `Unexpected ${token.source} in ${flow ?? "collection"}`);
        found = token;
        atNewline = indicator === "seq-item-ind" || indicator === "explicit-key-ind";
        hasSpace = false;
        break;
      case "comma":
        if (flow) {
          if (comma)
            onError(token, "UNEXPECTED_TOKEN", `Unexpected , in ${flow}`);
          comma = token;
          atNewline = false;
          hasSpace = false;
          break;
        }
      // else fallthrough
      default:
        onError(token, "UNEXPECTED_TOKEN", `Unexpected ${token.type} token`);
        atNewline = false;
        hasSpace = false;
    }
  }
  const last = tokens[tokens.length - 1];
  const end = last ? last.offset + last.source.length : offset;
  if (reqSpace && next && next.type !== "space" && next.type !== "newline" && next.type !== "comma" && (next.type !== "scalar" || next.source !== "")) {
    onError(next.offset, "MISSING_CHAR", "Tags and anchors must be separated from the next token by white space");
  }
  if (tab && (atNewline && tab.indent <= parentIndent || next?.type === "block-map" || next?.type === "block-seq"))
    onError(tab, "TAB_AS_INDENT", "Tabs are not allowed as indentation");
  return {
    comma,
    found,
    spaceBefore,
    comment,
    hasNewline,
    anchor,
    tag,
    newlineAfterProp,
    end,
    start: start ?? end
  };
}

// ../../../frontend/node_modules/yaml/browser/dist/compose/util-contains-newline.js
function containsNewline(key) {
  if (!key)
    return null;
  switch (key.type) {
    case "alias":
    case "scalar":
    case "double-quoted-scalar":
    case "single-quoted-scalar":
      if (key.source.includes("\n"))
        return true;
      if (key.end) {
        for (const st of key.end)
          if (st.type === "newline")
            return true;
      }
      return false;
    case "flow-collection":
      for (const it of key.items) {
        for (const st of it.start)
          if (st.type === "newline")
            return true;
        if (it.sep) {
          for (const st of it.sep)
            if (st.type === "newline")
              return true;
        }
        if (containsNewline(it.key) || containsNewline(it.value))
          return true;
      }
      return false;
    default:
      return true;
  }
}

// ../../../frontend/node_modules/yaml/browser/dist/compose/util-flow-indent-check.js
function flowIndentCheck(indent, fc, onError) {
  if (fc?.type === "flow-collection") {
    const end = fc.end[0];
    if (end.indent === indent && (end.source === "]" || end.source === "}") && containsNewline(fc)) {
      const msg = "Flow end indicator should be more indented than parent";
      onError(end, "BAD_INDENT", msg, true);
    }
  }
}

// ../../../frontend/node_modules/yaml/browser/dist/compose/util-map-includes.js
function mapIncludes(ctx, items, search) {
  const { uniqueKeys } = ctx.options;
  if (uniqueKeys === false)
    return false;
  const isEqual = typeof uniqueKeys === "function" ? uniqueKeys : (a, b) => a === b || isScalar(a) && isScalar(b) && a.value === b.value;
  return items.some((pair) => isEqual(pair.key, search));
}

// ../../../frontend/node_modules/yaml/browser/dist/compose/resolve-block-map.js
var startColMsg = "All mapping items must start at the same column";
function resolveBlockMap({ composeNode: composeNode2, composeEmptyNode: composeEmptyNode2 }, ctx, bm, onError, tag) {
  const NodeClass = tag?.nodeClass ?? YAMLMap;
  const map2 = new NodeClass(ctx.schema);
  if (ctx.atRoot)
    ctx.atRoot = false;
  let offset = bm.offset;
  let commentEnd = null;
  for (const collItem of bm.items) {
    const { start, key, sep, value } = collItem;
    const keyProps = resolveProps(start, {
      indicator: "explicit-key-ind",
      next: key ?? sep?.[0],
      offset,
      onError,
      parentIndent: bm.indent,
      startOnNewline: true
    });
    const implicitKey = !keyProps.found;
    if (implicitKey) {
      if (key) {
        if (key.type === "block-seq")
          onError(offset, "BLOCK_AS_IMPLICIT_KEY", "A block sequence may not be used as an implicit map key");
        else if ("indent" in key && key.indent !== bm.indent)
          onError(offset, "BAD_INDENT", startColMsg);
      }
      if (!keyProps.anchor && !keyProps.tag && !sep) {
        commentEnd = keyProps.end;
        if (keyProps.comment) {
          if (map2.comment)
            map2.comment += "\n" + keyProps.comment;
          else
            map2.comment = keyProps.comment;
        }
        continue;
      }
      if (keyProps.newlineAfterProp || containsNewline(key)) {
        onError(key ?? start[start.length - 1], "MULTILINE_IMPLICIT_KEY", "Implicit keys need to be on a single line");
      }
    } else if (keyProps.found?.indent !== bm.indent) {
      onError(offset, "BAD_INDENT", startColMsg);
    }
    ctx.atKey = true;
    const keyStart = keyProps.end;
    const keyNode = key ? composeNode2(ctx, key, keyProps, onError) : composeEmptyNode2(ctx, keyStart, start, null, keyProps, onError);
    if (ctx.schema.compat)
      flowIndentCheck(bm.indent, key, onError);
    ctx.atKey = false;
    if (mapIncludes(ctx, map2.items, keyNode))
      onError(keyStart, "DUPLICATE_KEY", "Map keys must be unique");
    const valueProps = resolveProps(sep ?? [], {
      indicator: "map-value-ind",
      next: value,
      offset: keyNode.range[2],
      onError,
      parentIndent: bm.indent,
      startOnNewline: !key || key.type === "block-scalar"
    });
    offset = valueProps.end;
    if (valueProps.found) {
      if (implicitKey) {
        if (value?.type === "block-map" && !valueProps.hasNewline)
          onError(offset, "BLOCK_AS_IMPLICIT_KEY", "Nested mappings are not allowed in compact mappings");
        if (ctx.options.strict && keyProps.start < valueProps.found.offset - 1024)
          onError(keyNode.range, "KEY_OVER_1024_CHARS", "The : indicator must be at most 1024 chars after the start of an implicit block mapping key");
      }
      const valueNode = value ? composeNode2(ctx, value, valueProps, onError) : composeEmptyNode2(ctx, offset, sep, null, valueProps, onError);
      if (ctx.schema.compat)
        flowIndentCheck(bm.indent, value, onError);
      offset = valueNode.range[2];
      const pair = new Pair(keyNode, valueNode);
      if (ctx.options.keepSourceTokens)
        pair.srcToken = collItem;
      map2.items.push(pair);
    } else {
      if (implicitKey)
        onError(keyNode.range, "MISSING_CHAR", "Implicit map keys need to be followed by map values");
      if (valueProps.comment) {
        if (keyNode.comment)
          keyNode.comment += "\n" + valueProps.comment;
        else
          keyNode.comment = valueProps.comment;
      }
      const pair = new Pair(keyNode);
      if (ctx.options.keepSourceTokens)
        pair.srcToken = collItem;
      map2.items.push(pair);
    }
  }
  if (commentEnd && commentEnd < offset)
    onError(commentEnd, "IMPOSSIBLE", "Map comment with trailing content");
  map2.range = [bm.offset, offset, commentEnd ?? offset];
  return map2;
}

// ../../../frontend/node_modules/yaml/browser/dist/compose/resolve-block-seq.js
function resolveBlockSeq({ composeNode: composeNode2, composeEmptyNode: composeEmptyNode2 }, ctx, bs, onError, tag) {
  const NodeClass = tag?.nodeClass ?? YAMLSeq;
  const seq2 = new NodeClass(ctx.schema);
  if (ctx.atRoot)
    ctx.atRoot = false;
  if (ctx.atKey)
    ctx.atKey = false;
  let offset = bs.offset;
  let commentEnd = null;
  for (const { start, value } of bs.items) {
    const props = resolveProps(start, {
      indicator: "seq-item-ind",
      next: value,
      offset,
      onError,
      parentIndent: bs.indent,
      startOnNewline: true
    });
    if (!props.found) {
      if (props.anchor || props.tag || value) {
        if (value?.type === "block-seq")
          onError(props.end, "BAD_INDENT", "All sequence items must start at the same column");
        else
          onError(offset, "MISSING_CHAR", "Sequence item without - indicator");
      } else {
        commentEnd = props.end;
        if (props.comment)
          seq2.comment = props.comment;
        continue;
      }
    }
    const node = value ? composeNode2(ctx, value, props, onError) : composeEmptyNode2(ctx, props.end, start, null, props, onError);
    if (ctx.schema.compat)
      flowIndentCheck(bs.indent, value, onError);
    offset = node.range[2];
    seq2.items.push(node);
  }
  seq2.range = [bs.offset, offset, commentEnd ?? offset];
  return seq2;
}

// ../../../frontend/node_modules/yaml/browser/dist/compose/resolve-end.js
function resolveEnd(end, offset, reqSpace, onError) {
  let comment = "";
  if (end) {
    let hasSpace = false;
    let sep = "";
    for (const token of end) {
      const { source, type } = token;
      switch (type) {
        case "space":
          hasSpace = true;
          break;
        case "comment": {
          if (reqSpace && !hasSpace)
            onError(token, "MISSING_CHAR", "Comments must be separated from other tokens by white space characters");
          const cb = source.substring(1) || " ";
          if (!comment)
            comment = cb;
          else
            comment += sep + cb;
          sep = "";
          break;
        }
        case "newline":
          if (comment)
            sep += source;
          hasSpace = true;
          break;
        default:
          onError(token, "UNEXPECTED_TOKEN", `Unexpected ${type} at node end`);
      }
      offset += source.length;
    }
  }
  return { comment, offset };
}

// ../../../frontend/node_modules/yaml/browser/dist/compose/resolve-flow-collection.js
var blockMsg = "Block collections are not allowed within flow collections";
var isBlock = (token) => token && (token.type === "block-map" || token.type === "block-seq");
function resolveFlowCollection({ composeNode: composeNode2, composeEmptyNode: composeEmptyNode2 }, ctx, fc, onError, tag) {
  const isMap2 = fc.start.source === "{";
  const fcName = isMap2 ? "flow map" : "flow sequence";
  const NodeClass = tag?.nodeClass ?? (isMap2 ? YAMLMap : YAMLSeq);
  const coll = new NodeClass(ctx.schema);
  coll.flow = true;
  const atRoot = ctx.atRoot;
  if (atRoot)
    ctx.atRoot = false;
  if (ctx.atKey)
    ctx.atKey = false;
  let offset = fc.offset + fc.start.source.length;
  for (let i = 0; i < fc.items.length; ++i) {
    const collItem = fc.items[i];
    const { start, key, sep, value } = collItem;
    const props = resolveProps(start, {
      flow: fcName,
      indicator: "explicit-key-ind",
      next: key ?? sep?.[0],
      offset,
      onError,
      parentIndent: fc.indent,
      startOnNewline: false
    });
    if (!props.found) {
      if (!props.anchor && !props.tag && !sep && !value) {
        if (i === 0 && props.comma)
          onError(props.comma, "UNEXPECTED_TOKEN", `Unexpected , in ${fcName}`);
        else if (i < fc.items.length - 1)
          onError(props.start, "UNEXPECTED_TOKEN", `Unexpected empty item in ${fcName}`);
        if (props.comment) {
          if (coll.comment)
            coll.comment += "\n" + props.comment;
          else
            coll.comment = props.comment;
        }
        offset = props.end;
        continue;
      }
      if (!isMap2 && ctx.options.strict && containsNewline(key))
        onError(
          key,
          // checked by containsNewline()
          "MULTILINE_IMPLICIT_KEY",
          "Implicit keys of flow sequence pairs need to be on a single line"
        );
    }
    if (i === 0) {
      if (props.comma)
        onError(props.comma, "UNEXPECTED_TOKEN", `Unexpected , in ${fcName}`);
    } else {
      if (!props.comma)
        onError(props.start, "MISSING_CHAR", `Missing , between ${fcName} items`);
      if (props.comment) {
        let prevItemComment = "";
        loop: for (const st of start) {
          switch (st.type) {
            case "comma":
            case "space":
              break;
            case "comment":
              prevItemComment = st.source.substring(1);
              break loop;
            default:
              break loop;
          }
        }
        if (prevItemComment) {
          let prev = coll.items[coll.items.length - 1];
          if (isPair(prev))
            prev = prev.value ?? prev.key;
          if (prev.comment)
            prev.comment += "\n" + prevItemComment;
          else
            prev.comment = prevItemComment;
          props.comment = props.comment.substring(prevItemComment.length + 1);
        }
      }
    }
    if (!isMap2 && !sep && !props.found) {
      const valueNode = value ? composeNode2(ctx, value, props, onError) : composeEmptyNode2(ctx, props.end, sep, null, props, onError);
      coll.items.push(valueNode);
      offset = valueNode.range[2];
      if (isBlock(value))
        onError(valueNode.range, "BLOCK_IN_FLOW", blockMsg);
    } else {
      ctx.atKey = true;
      const keyStart = props.end;
      const keyNode = key ? composeNode2(ctx, key, props, onError) : composeEmptyNode2(ctx, keyStart, start, null, props, onError);
      if (isBlock(key))
        onError(keyNode.range, "BLOCK_IN_FLOW", blockMsg);
      ctx.atKey = false;
      const valueProps = resolveProps(sep ?? [], {
        flow: fcName,
        indicator: "map-value-ind",
        next: value,
        offset: keyNode.range[2],
        onError,
        parentIndent: fc.indent,
        startOnNewline: false
      });
      if (valueProps.found) {
        if (!isMap2 && !props.found && ctx.options.strict) {
          if (sep)
            for (const st of sep) {
              if (st === valueProps.found)
                break;
              if (st.type === "newline") {
                onError(st, "MULTILINE_IMPLICIT_KEY", "Implicit keys of flow sequence pairs need to be on a single line");
                break;
              }
            }
          if (props.start < valueProps.found.offset - 1024)
            onError(valueProps.found, "KEY_OVER_1024_CHARS", "The : indicator must be at most 1024 chars after the start of an implicit flow sequence key");
        }
      } else if (value) {
        if ("source" in value && value.source?.[0] === ":")
          onError(value, "MISSING_CHAR", `Missing space after : in ${fcName}`);
        else
          onError(valueProps.start, "MISSING_CHAR", `Missing , or : between ${fcName} items`);
      }
      const valueNode = value ? composeNode2(ctx, value, valueProps, onError) : valueProps.found ? composeEmptyNode2(ctx, valueProps.end, sep, null, valueProps, onError) : null;
      if (valueNode) {
        if (isBlock(value))
          onError(valueNode.range, "BLOCK_IN_FLOW", blockMsg);
      } else if (valueProps.comment) {
        if (keyNode.comment)
          keyNode.comment += "\n" + valueProps.comment;
        else
          keyNode.comment = valueProps.comment;
      }
      const pair = new Pair(keyNode, valueNode);
      if (ctx.options.keepSourceTokens)
        pair.srcToken = collItem;
      if (isMap2) {
        const map2 = coll;
        if (mapIncludes(ctx, map2.items, keyNode))
          onError(keyStart, "DUPLICATE_KEY", "Map keys must be unique");
        map2.items.push(pair);
      } else {
        const map2 = new YAMLMap(ctx.schema);
        map2.flow = true;
        map2.items.push(pair);
        const endRange = (valueNode ?? keyNode).range;
        map2.range = [keyNode.range[0], endRange[1], endRange[2]];
        coll.items.push(map2);
      }
      offset = valueNode ? valueNode.range[2] : valueProps.end;
    }
  }
  const expectedEnd = isMap2 ? "}" : "]";
  const [ce, ...ee] = fc.end;
  let cePos = offset;
  if (ce?.source === expectedEnd)
    cePos = ce.offset + ce.source.length;
  else {
    const name = fcName[0].toUpperCase() + fcName.substring(1);
    const msg = atRoot ? `${name} must end with a ${expectedEnd}` : `${name} in block collection must be sufficiently indented and end with a ${expectedEnd}`;
    onError(offset, atRoot ? "MISSING_CHAR" : "BAD_INDENT", msg);
    if (ce && ce.source.length !== 1)
      ee.unshift(ce);
  }
  if (ee.length > 0) {
    const end = resolveEnd(ee, cePos, ctx.options.strict, onError);
    if (end.comment) {
      if (coll.comment)
        coll.comment += "\n" + end.comment;
      else
        coll.comment = end.comment;
    }
    coll.range = [fc.offset, cePos, end.offset];
  } else {
    coll.range = [fc.offset, cePos, cePos];
  }
  return coll;
}

// ../../../frontend/node_modules/yaml/browser/dist/compose/compose-collection.js
function resolveCollection(CN2, ctx, token, onError, tagName, tag) {
  const coll = token.type === "block-map" ? resolveBlockMap(CN2, ctx, token, onError, tag) : token.type === "block-seq" ? resolveBlockSeq(CN2, ctx, token, onError, tag) : resolveFlowCollection(CN2, ctx, token, onError, tag);
  const Coll = coll.constructor;
  if (tagName === "!" || tagName === Coll.tagName) {
    coll.tag = Coll.tagName;
    return coll;
  }
  if (tagName)
    coll.tag = tagName;
  return coll;
}
function composeCollection(CN2, ctx, token, props, onError) {
  const tagToken = props.tag;
  const tagName = !tagToken ? null : ctx.directives.tagName(tagToken.source, (msg) => onError(tagToken, "TAG_RESOLVE_FAILED", msg));
  if (token.type === "block-seq") {
    const { anchor, newlineAfterProp: nl } = props;
    const lastProp = anchor && tagToken ? anchor.offset > tagToken.offset ? anchor : tagToken : anchor ?? tagToken;
    if (lastProp && (!nl || nl.offset < lastProp.offset)) {
      const message = "Missing newline after block sequence props";
      onError(lastProp, "MISSING_CHAR", message);
    }
  }
  const expType = token.type === "block-map" ? "map" : token.type === "block-seq" ? "seq" : token.start.source === "{" ? "map" : "seq";
  if (!tagToken || !tagName || tagName === "!" || tagName === YAMLMap.tagName && expType === "map" || tagName === YAMLSeq.tagName && expType === "seq") {
    return resolveCollection(CN2, ctx, token, onError, tagName);
  }
  let tag = ctx.schema.tags.find((t) => t.tag === tagName && t.collection === expType);
  if (!tag) {
    const kt = ctx.schema.knownTags[tagName];
    if (kt?.collection === expType) {
      ctx.schema.tags.push(Object.assign({}, kt, { default: false }));
      tag = kt;
    } else {
      if (kt) {
        onError(tagToken, "BAD_COLLECTION_TYPE", `${kt.tag} used for ${expType} collection, but expects ${kt.collection ?? "scalar"}`, true);
      } else {
        onError(tagToken, "TAG_RESOLVE_FAILED", `Unresolved tag: ${tagName}`, true);
      }
      return resolveCollection(CN2, ctx, token, onError, tagName);
    }
  }
  const coll = resolveCollection(CN2, ctx, token, onError, tagName, tag);
  const res = tag.resolve?.(coll, (msg) => onError(tagToken, "TAG_RESOLVE_FAILED", msg), ctx.options) ?? coll;
  const node = isNode(res) ? res : new Scalar(res);
  node.range = coll.range;
  node.tag = tagName;
  if (tag?.format)
    node.format = tag.format;
  return node;
}

// ../../../frontend/node_modules/yaml/browser/dist/compose/resolve-block-scalar.js
function resolveBlockScalar(ctx, scalar, onError) {
  const start = scalar.offset;
  const header = parseBlockScalarHeader(scalar, ctx.options.strict, onError);
  if (!header)
    return { value: "", type: null, comment: "", range: [start, start, start] };
  const type = header.mode === ">" ? Scalar.BLOCK_FOLDED : Scalar.BLOCK_LITERAL;
  const lines = scalar.source ? splitLines(scalar.source) : [];
  let chompStart = lines.length;
  for (let i = lines.length - 1; i >= 0; --i) {
    const content = lines[i][1];
    if (content === "" || content === "\r")
      chompStart = i;
    else
      break;
  }
  if (chompStart === 0) {
    const value2 = header.chomp === "+" && lines.length > 0 ? "\n".repeat(Math.max(1, lines.length - 1)) : "";
    let end2 = start + header.length;
    if (scalar.source)
      end2 += scalar.source.length;
    return { value: value2, type, comment: header.comment, range: [start, end2, end2] };
  }
  let trimIndent = scalar.indent + header.indent;
  let offset = scalar.offset + header.length;
  let contentStart = 0;
  for (let i = 0; i < chompStart; ++i) {
    const [indent, content] = lines[i];
    if (content === "" || content === "\r") {
      if (header.indent === 0 && indent.length > trimIndent)
        trimIndent = indent.length;
    } else {
      if (indent.length < trimIndent) {
        const message = "Block scalars with more-indented leading empty lines must use an explicit indentation indicator";
        onError(offset + indent.length, "MISSING_CHAR", message);
      }
      if (header.indent === 0)
        trimIndent = indent.length;
      contentStart = i;
      if (trimIndent === 0 && !ctx.atRoot) {
        const message = "Block scalar values in collections must be indented";
        onError(offset, "BAD_INDENT", message);
      }
      break;
    }
    offset += indent.length + content.length + 1;
  }
  for (let i = lines.length - 1; i >= chompStart; --i) {
    if (lines[i][0].length > trimIndent)
      chompStart = i + 1;
  }
  let value = "";
  let sep = "";
  let prevMoreIndented = false;
  for (let i = 0; i < contentStart; ++i)
    value += lines[i][0].slice(trimIndent) + "\n";
  for (let i = contentStart; i < chompStart; ++i) {
    let [indent, content] = lines[i];
    offset += indent.length + content.length + 1;
    const crlf = content[content.length - 1] === "\r";
    if (crlf)
      content = content.slice(0, -1);
    if (content && indent.length < trimIndent) {
      const src = header.indent ? "explicit indentation indicator" : "first line";
      const message = `Block scalar lines must not be less indented than their ${src}`;
      onError(offset - content.length - (crlf ? 2 : 1), "BAD_INDENT", message);
      indent = "";
    }
    if (type === Scalar.BLOCK_LITERAL) {
      value += sep + indent.slice(trimIndent) + content;
      sep = "\n";
    } else if (indent.length > trimIndent || content[0] === "	") {
      if (sep === " ")
        sep = "\n";
      else if (!prevMoreIndented && sep === "\n")
        sep = "\n\n";
      value += sep + indent.slice(trimIndent) + content;
      sep = "\n";
      prevMoreIndented = true;
    } else if (content === "") {
      if (sep === "\n")
        value += "\n";
      else
        sep = "\n";
    } else {
      value += sep + content;
      sep = " ";
      prevMoreIndented = false;
    }
  }
  switch (header.chomp) {
    case "-":
      break;
    case "+":
      for (let i = chompStart; i < lines.length; ++i)
        value += "\n" + lines[i][0].slice(trimIndent);
      if (value[value.length - 1] !== "\n")
        value += "\n";
      break;
    default:
      value += "\n";
  }
  const end = start + header.length + scalar.source.length;
  return { value, type, comment: header.comment, range: [start, end, end] };
}
function parseBlockScalarHeader({ offset, props }, strict, onError) {
  if (props[0].type !== "block-scalar-header") {
    onError(props[0], "IMPOSSIBLE", "Block scalar header not found");
    return null;
  }
  const { source } = props[0];
  const mode = source[0];
  let indent = 0;
  let chomp = "";
  let error = -1;
  for (let i = 1; i < source.length; ++i) {
    const ch = source[i];
    if (!chomp && (ch === "-" || ch === "+"))
      chomp = ch;
    else {
      const n = Number(ch);
      if (!indent && n)
        indent = n;
      else if (error === -1)
        error = offset + i;
    }
  }
  if (error !== -1)
    onError(error, "UNEXPECTED_TOKEN", `Block scalar header includes extra characters: ${source}`);
  let hasSpace = false;
  let comment = "";
  let length = source.length;
  for (let i = 1; i < props.length; ++i) {
    const token = props[i];
    switch (token.type) {
      case "space":
        hasSpace = true;
      // fallthrough
      case "newline":
        length += token.source.length;
        break;
      case "comment":
        if (strict && !hasSpace) {
          const message = "Comments must be separated from other tokens by white space characters";
          onError(token, "MISSING_CHAR", message);
        }
        length += token.source.length;
        comment = token.source.substring(1);
        break;
      case "error":
        onError(token, "UNEXPECTED_TOKEN", token.message);
        length += token.source.length;
        break;
      /* istanbul ignore next should not happen */
      default: {
        const message = `Unexpected token in block scalar header: ${token.type}`;
        onError(token, "UNEXPECTED_TOKEN", message);
        const ts = token.source;
        if (ts && typeof ts === "string")
          length += ts.length;
      }
    }
  }
  return { mode, indent, chomp, comment, length };
}
function splitLines(source) {
  const split = source.split(/\n( *)/);
  const first = split[0];
  const m = first.match(/^( *)/);
  const line0 = m?.[1] ? [m[1], first.slice(m[1].length)] : ["", first];
  const lines = [line0];
  for (let i = 1; i < split.length; i += 2)
    lines.push([split[i], split[i + 1]]);
  return lines;
}

// ../../../frontend/node_modules/yaml/browser/dist/compose/resolve-flow-scalar.js
function resolveFlowScalar(scalar, strict, onError) {
  const { offset, type, source, end } = scalar;
  let _type;
  let value;
  const _onError = (rel, code, msg) => onError(offset + rel, code, msg);
  switch (type) {
    case "scalar":
      _type = Scalar.PLAIN;
      value = plainValue(source, _onError);
      break;
    case "single-quoted-scalar":
      _type = Scalar.QUOTE_SINGLE;
      value = singleQuotedValue(source, _onError);
      break;
    case "double-quoted-scalar":
      _type = Scalar.QUOTE_DOUBLE;
      value = doubleQuotedValue(source, _onError);
      break;
    /* istanbul ignore next should not happen */
    default:
      onError(scalar, "UNEXPECTED_TOKEN", `Expected a flow scalar value, but found: ${type}`);
      return {
        value: "",
        type: null,
        comment: "",
        range: [offset, offset + source.length, offset + source.length]
      };
  }
  const valueEnd = offset + source.length;
  const re = resolveEnd(end, valueEnd, strict, onError);
  return {
    value,
    type: _type,
    comment: re.comment,
    range: [offset, valueEnd, re.offset]
  };
}
function plainValue(source, onError) {
  let badChar = "";
  switch (source[0]) {
    /* istanbul ignore next should not happen */
    case "	":
      badChar = "a tab character";
      break;
    case ",":
      badChar = "flow indicator character ,";
      break;
    case "%":
      badChar = "directive indicator character %";
      break;
    case "|":
    case ">": {
      badChar = `block scalar indicator ${source[0]}`;
      break;
    }
    case "@":
    case "`": {
      badChar = `reserved character ${source[0]}`;
      break;
    }
  }
  if (badChar)
    onError(0, "BAD_SCALAR_START", `Plain value cannot start with ${badChar}`);
  return unfoldLines(source);
}
function singleQuotedValue(source, onError) {
  if (source[source.length - 1] !== "'" || source.length === 1)
    onError(source.length, "MISSING_CHAR", "Missing closing 'quote");
  return unfoldLines(source.slice(1, -1)).replace(/''/g, "'");
}
function unfoldLines(source) {
  const line = /(.*?)\r?\n/sy;
  let match = line.exec(source);
  if (!match)
    return source;
  let trimEnd, trimBoth;
  try {
    trimEnd = new RegExp("(?<![ 	])[ 	]+$");
    trimBoth = new RegExp("^[ 	]+|(?<![ 	])[ 	]+$", "g");
  } catch {
    trimEnd = /[ \t]+$/;
    trimBoth = /^[ \t]+|[ \t]+$/g;
  }
  let res = match[1].replace(trimEnd, "");
  let sep = " ";
  let pos = line.lastIndex;
  while (match = line.exec(source)) {
    const lm = match[1].replace(trimBoth, "");
    if (lm === "") {
      if (sep === "\n")
        res += sep;
      else
        sep = "\n";
    } else {
      res += sep + lm;
      sep = " ";
    }
    pos = line.lastIndex;
  }
  const last = /[ \t]*(.*)/sy;
  last.lastIndex = pos;
  match = last.exec(source);
  return res + sep + (match?.[1] ?? "");
}
function doubleQuotedValue(source, onError) {
  let res = "";
  for (let i = 1; i < source.length - 1; ++i) {
    const ch = source[i];
    if (ch === "\r" && source[i + 1] === "\n")
      continue;
    if (ch === "\n") {
      const { fold, offset } = foldNewline(source, i);
      res += fold;
      i = offset;
    } else if (ch === "\\") {
      let next = source[++i];
      const cc = escapeCodes[next];
      if (cc)
        res += cc;
      else if (next === "\n") {
        next = source[i + 1];
        while (next === " " || next === "	")
          next = source[++i + 1];
      } else if (next === "\r" && source[i + 1] === "\n") {
        next = source[++i + 1];
        while (next === " " || next === "	")
          next = source[++i + 1];
      } else if (next === "x" || next === "u" || next === "U") {
        const length = next === "x" ? 2 : next === "u" ? 4 : 8;
        res += parseCharCode(source, i + 1, length, onError);
        i += length;
      } else {
        const raw = source.substr(i - 1, 2);
        onError(i - 1, "BAD_DQ_ESCAPE", `Invalid escape sequence ${raw}`);
        res += raw;
      }
    } else if (ch === " " || ch === "	") {
      const wsStart = i;
      let next = source[i + 1];
      while (next === " " || next === "	")
        next = source[++i + 1];
      if (next !== "\n" && !(next === "\r" && source[i + 2] === "\n"))
        res += i > wsStart ? source.slice(wsStart, i + 1) : ch;
    } else {
      res += ch;
    }
  }
  if (source[source.length - 1] !== '"' || source.length === 1)
    onError(source.length, "MISSING_CHAR", 'Missing closing "quote');
  return res;
}
function foldNewline(source, offset) {
  let fold = "";
  let ch = source[offset + 1];
  while (ch === " " || ch === "	" || ch === "\n" || ch === "\r") {
    if (ch === "\r" && source[offset + 2] !== "\n")
      break;
    if (ch === "\n")
      fold += "\n";
    offset += 1;
    ch = source[offset + 1];
  }
  if (!fold)
    fold = " ";
  return { fold, offset };
}
var escapeCodes = {
  "0": "\0",
  // null character
  a: "\x07",
  // bell character
  b: "\b",
  // backspace
  e: "\x1B",
  // escape character
  f: "\f",
  // form feed
  n: "\n",
  // line feed
  r: "\r",
  // carriage return
  t: "	",
  // horizontal tab
  v: "\v",
  // vertical tab
  N: "\x85",
  // Unicode next line
  _: "\xA0",
  // Unicode non-breaking space
  L: "\u2028",
  // Unicode line separator
  P: "\u2029",
  // Unicode paragraph separator
  " ": " ",
  '"': '"',
  "/": "/",
  "\\": "\\",
  "	": "	"
};
function parseCharCode(source, offset, length, onError) {
  const cc = source.substr(offset, length);
  const ok = cc.length === length && /^[0-9a-fA-F]+$/.test(cc);
  const code = ok ? parseInt(cc, 16) : NaN;
  try {
    return String.fromCodePoint(code);
  } catch {
    const raw = source.substr(offset - 2, length + 2);
    onError(offset - 2, "BAD_DQ_ESCAPE", `Invalid escape sequence ${raw}`);
    return raw;
  }
}

// ../../../frontend/node_modules/yaml/browser/dist/compose/compose-scalar.js
function composeScalar(ctx, token, tagToken, onError) {
  const { value, type, comment, range } = token.type === "block-scalar" ? resolveBlockScalar(ctx, token, onError) : resolveFlowScalar(token, ctx.options.strict, onError);
  const tagName = tagToken ? ctx.directives.tagName(tagToken.source, (msg) => onError(tagToken, "TAG_RESOLVE_FAILED", msg)) : null;
  let tag;
  if (ctx.options.stringKeys && ctx.atKey) {
    tag = ctx.schema[SCALAR];
  } else if (tagName)
    tag = findScalarTagByName(ctx.schema, value, tagName, tagToken, onError);
  else if (token.type === "scalar")
    tag = findScalarTagByTest(ctx, value, token, onError);
  else
    tag = ctx.schema[SCALAR];
  let scalar;
  try {
    const res = tag.resolve(value, (msg) => onError(tagToken ?? token, "TAG_RESOLVE_FAILED", msg), ctx.options);
    scalar = isScalar(res) ? res : new Scalar(res);
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    onError(tagToken ?? token, "TAG_RESOLVE_FAILED", msg);
    scalar = new Scalar(value);
  }
  scalar.range = range;
  scalar.source = value;
  if (type)
    scalar.type = type;
  if (tagName)
    scalar.tag = tagName;
  if (tag.format)
    scalar.format = tag.format;
  if (comment)
    scalar.comment = comment;
  return scalar;
}
function findScalarTagByName(schema4, value, tagName, tagToken, onError) {
  if (tagName === "!")
    return schema4[SCALAR];
  const matchWithTest = [];
  for (const tag of schema4.tags) {
    if (!tag.collection && tag.tag === tagName) {
      if (tag.default && tag.test)
        matchWithTest.push(tag);
      else
        return tag;
    }
  }
  for (const tag of matchWithTest)
    if (tag.test?.test(value))
      return tag;
  const kt = schema4.knownTags[tagName];
  if (kt && !kt.collection) {
    schema4.tags.push(Object.assign({}, kt, { default: false, test: void 0 }));
    return kt;
  }
  onError(tagToken, "TAG_RESOLVE_FAILED", `Unresolved tag: ${tagName}`, tagName !== "tag:yaml.org,2002:str");
  return schema4[SCALAR];
}
function findScalarTagByTest({ atKey, directives, schema: schema4 }, value, token, onError) {
  const tag = schema4.tags.find((tag2) => (tag2.default === true || atKey && tag2.default === "key") && tag2.test?.test(value)) || schema4[SCALAR];
  if (schema4.compat) {
    const compat = schema4.compat.find((tag2) => tag2.default && tag2.test?.test(value)) ?? schema4[SCALAR];
    if (tag.tag !== compat.tag) {
      const ts = directives.tagString(tag.tag);
      const cs = directives.tagString(compat.tag);
      const msg = `Value may be parsed as either ${ts} or ${cs}`;
      onError(token, "TAG_RESOLVE_FAILED", msg, true);
    }
  }
  return tag;
}

// ../../../frontend/node_modules/yaml/browser/dist/compose/util-empty-scalar-position.js
function emptyScalarPosition(offset, before, pos) {
  if (before) {
    pos ?? (pos = before.length);
    for (let i = pos - 1; i >= 0; --i) {
      let st = before[i];
      switch (st.type) {
        case "space":
        case "comment":
        case "newline":
          offset -= st.source.length;
          continue;
      }
      st = before[++i];
      while (st?.type === "space") {
        offset += st.source.length;
        st = before[++i];
      }
      break;
    }
  }
  return offset;
}

// ../../../frontend/node_modules/yaml/browser/dist/compose/compose-node.js
var CN = { composeNode, composeEmptyNode };
function composeNode(ctx, token, props, onError) {
  const atKey = ctx.atKey;
  const { spaceBefore, comment, anchor, tag } = props;
  let node;
  let isSrcToken = true;
  switch (token.type) {
    case "alias":
      node = composeAlias(ctx, token, onError);
      if (anchor || tag)
        onError(token, "ALIAS_PROPS", "An alias node must not specify any properties");
      break;
    case "scalar":
    case "single-quoted-scalar":
    case "double-quoted-scalar":
    case "block-scalar":
      node = composeScalar(ctx, token, tag, onError);
      if (anchor)
        node.anchor = anchor.source.substring(1);
      break;
    case "block-map":
    case "block-seq":
    case "flow-collection":
      try {
        node = composeCollection(CN, ctx, token, props, onError);
        if (anchor)
          node.anchor = anchor.source.substring(1);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        onError(token, "RESOURCE_EXHAUSTION", message);
      }
      break;
    default: {
      const message = token.type === "error" ? token.message : `Unsupported token (type: ${token.type})`;
      onError(token, "UNEXPECTED_TOKEN", message);
      isSrcToken = false;
    }
  }
  node ?? (node = composeEmptyNode(ctx, token.offset, void 0, null, props, onError));
  if (anchor && node.anchor === "")
    onError(anchor, "BAD_ALIAS", "Anchor cannot be an empty string");
  if (atKey && ctx.options.stringKeys && (!isScalar(node) || typeof node.value !== "string" || node.tag && node.tag !== "tag:yaml.org,2002:str")) {
    const msg = "With stringKeys, all keys must be strings";
    onError(tag ?? token, "NON_STRING_KEY", msg);
  }
  if (spaceBefore)
    node.spaceBefore = true;
  if (comment) {
    if (token.type === "scalar" && token.source === "")
      node.comment = comment;
    else
      node.commentBefore = comment;
  }
  if (ctx.options.keepSourceTokens && isSrcToken)
    node.srcToken = token;
  return node;
}
function composeEmptyNode(ctx, offset, before, pos, { spaceBefore, comment, anchor, tag, end }, onError) {
  const token = {
    type: "scalar",
    offset: emptyScalarPosition(offset, before, pos),
    indent: -1,
    source: ""
  };
  const node = composeScalar(ctx, token, tag, onError);
  if (anchor) {
    node.anchor = anchor.source.substring(1);
    if (node.anchor === "")
      onError(anchor, "BAD_ALIAS", "Anchor cannot be an empty string");
  }
  if (spaceBefore)
    node.spaceBefore = true;
  if (comment) {
    node.comment = comment;
    node.range[2] = end;
  }
  return node;
}
function composeAlias({ options }, { offset, source, end }, onError) {
  const alias = new Alias(source.substring(1));
  if (alias.source === "")
    onError(offset, "BAD_ALIAS", "Alias cannot be an empty string");
  if (alias.source.endsWith(":"))
    onError(offset + source.length - 1, "BAD_ALIAS", "Alias ending in : is ambiguous", true);
  const valueEnd = offset + source.length;
  const re = resolveEnd(end, valueEnd, options.strict, onError);
  alias.range = [offset, valueEnd, re.offset];
  if (re.comment)
    alias.comment = re.comment;
  return alias;
}

// ../../../frontend/node_modules/yaml/browser/dist/compose/compose-doc.js
function composeDoc(options, directives, { offset, start, value, end }, onError) {
  const opts = Object.assign({ _directives: directives }, options);
  const doc = new Document(void 0, opts);
  const ctx = {
    atKey: false,
    atRoot: true,
    directives: doc.directives,
    options: doc.options,
    schema: doc.schema
  };
  const props = resolveProps(start, {
    indicator: "doc-start",
    next: value ?? end?.[0],
    offset,
    onError,
    parentIndent: 0,
    startOnNewline: true
  });
  if (props.found) {
    doc.directives.docStart = true;
    if (value && (value.type === "block-map" || value.type === "block-seq") && !props.hasNewline)
      onError(props.end, "MISSING_CHAR", "Block collection cannot start on same line with directives-end marker");
  }
  doc.contents = value ? composeNode(ctx, value, props, onError) : composeEmptyNode(ctx, props.end, start, null, props, onError);
  const contentEnd = doc.contents.range[2];
  const re = resolveEnd(end, contentEnd, false, onError);
  if (re.comment)
    doc.comment = re.comment;
  doc.range = [offset, contentEnd, re.offset];
  return doc;
}

// ../../../frontend/node_modules/yaml/browser/dist/compose/composer.js
function getErrorPos(src) {
  if (typeof src === "number")
    return [src, src + 1];
  if (Array.isArray(src))
    return src.length === 2 ? src : [src[0], src[1]];
  const { offset, source } = src;
  return [offset, offset + (typeof source === "string" ? source.length : 1)];
}
function parsePrelude(prelude) {
  let comment = "";
  let atComment = false;
  let afterEmptyLine = false;
  for (let i = 0; i < prelude.length; ++i) {
    const source = prelude[i];
    switch (source[0]) {
      case "#":
        comment += (comment === "" ? "" : afterEmptyLine ? "\n\n" : "\n") + (source.substring(1) || " ");
        atComment = true;
        afterEmptyLine = false;
        break;
      case "%":
        if (prelude[i + 1]?.[0] !== "#")
          i += 1;
        atComment = false;
        break;
      default:
        if (!atComment)
          afterEmptyLine = true;
        atComment = false;
    }
  }
  return { comment, afterEmptyLine };
}
var Composer = class {
  constructor(options = {}) {
    this.doc = null;
    this.atDirectives = false;
    this.prelude = [];
    this.errors = [];
    this.warnings = [];
    this.onError = (source, code, message, warning) => {
      const pos = getErrorPos(source);
      if (warning)
        this.warnings.push(new YAMLWarning(pos, code, message));
      else
        this.errors.push(new YAMLParseError(pos, code, message));
    };
    this.directives = new Directives({ version: options.version || "1.2" });
    this.options = options;
  }
  decorate(doc, afterDoc) {
    const { comment, afterEmptyLine } = parsePrelude(this.prelude);
    if (comment) {
      const dc = doc.contents;
      if (afterDoc) {
        doc.comment = doc.comment ? `${doc.comment}
${comment}` : comment;
      } else if (afterEmptyLine || doc.directives.docStart || !dc) {
        doc.commentBefore = comment;
      } else if (isCollection(dc) && !dc.flow && dc.items.length > 0) {
        let it = dc.items[0];
        if (isPair(it))
          it = it.key;
        const cb = it.commentBefore;
        it.commentBefore = cb ? `${comment}
${cb}` : comment;
      } else {
        const cb = dc.commentBefore;
        dc.commentBefore = cb ? `${comment}
${cb}` : comment;
      }
    }
    if (afterDoc) {
      for (let i = 0; i < this.errors.length; ++i)
        doc.errors.push(this.errors[i]);
      for (let i = 0; i < this.warnings.length; ++i)
        doc.warnings.push(this.warnings[i]);
    } else {
      doc.errors = this.errors;
      doc.warnings = this.warnings;
    }
    this.prelude = [];
    this.errors = [];
    this.warnings = [];
  }
  /**
   * Current stream status information.
   *
   * Mostly useful at the end of input for an empty stream.
   */
  streamInfo() {
    return {
      comment: parsePrelude(this.prelude).comment,
      directives: this.directives,
      errors: this.errors,
      warnings: this.warnings
    };
  }
  /**
   * Compose tokens into documents.
   *
   * @param forceDoc - If the stream contains no document, still emit a final document including any comments and directives that would be applied to a subsequent document.
   * @param endOffset - Should be set if `forceDoc` is also set, to set the document range end and to indicate errors correctly.
   */
  *compose(tokens, forceDoc = false, endOffset = -1) {
    for (const token of tokens)
      yield* this.next(token);
    yield* this.end(forceDoc, endOffset);
  }
  /** Advance the composer by one CST token. */
  *next(token) {
    switch (token.type) {
      case "directive":
        this.directives.add(token.source, (offset, message, warning) => {
          const pos = getErrorPos(token);
          pos[0] += offset;
          this.onError(pos, "BAD_DIRECTIVE", message, warning);
        });
        this.prelude.push(token.source);
        this.atDirectives = true;
        break;
      case "document": {
        const doc = composeDoc(this.options, this.directives, token, this.onError);
        if (this.atDirectives && !doc.directives.docStart)
          this.onError(token, "MISSING_CHAR", "Missing directives-end/doc-start indicator line");
        this.decorate(doc, false);
        if (this.doc)
          yield this.doc;
        this.doc = doc;
        this.atDirectives = false;
        break;
      }
      case "byte-order-mark":
      case "space":
        break;
      case "comment":
      case "newline":
        this.prelude.push(token.source);
        break;
      case "error": {
        const msg = token.source ? `${token.message}: ${JSON.stringify(token.source)}` : token.message;
        const error = new YAMLParseError(getErrorPos(token), "UNEXPECTED_TOKEN", msg);
        if (this.atDirectives || !this.doc)
          this.errors.push(error);
        else
          this.doc.errors.push(error);
        break;
      }
      case "doc-end": {
        if (!this.doc) {
          const msg = "Unexpected doc-end without preceding document";
          this.errors.push(new YAMLParseError(getErrorPos(token), "UNEXPECTED_TOKEN", msg));
          break;
        }
        this.doc.directives.docEnd = true;
        const end = resolveEnd(token.end, token.offset + token.source.length, this.doc.options.strict, this.onError);
        this.decorate(this.doc, true);
        if (end.comment) {
          const dc = this.doc.comment;
          this.doc.comment = dc ? `${dc}
${end.comment}` : end.comment;
        }
        this.doc.range[2] = end.offset;
        break;
      }
      default:
        this.errors.push(new YAMLParseError(getErrorPos(token), "UNEXPECTED_TOKEN", `Unsupported token ${token.type}`));
    }
  }
  /**
   * Call at end of input to yield any remaining document.
   *
   * @param forceDoc - If the stream contains no document, still emit a final document including any comments and directives that would be applied to a subsequent document.
   * @param endOffset - Should be set if `forceDoc` is also set, to set the document range end and to indicate errors correctly.
   */
  *end(forceDoc = false, endOffset = -1) {
    if (this.doc) {
      this.decorate(this.doc, true);
      yield this.doc;
      this.doc = null;
    } else if (forceDoc) {
      const opts = Object.assign({ _directives: this.directives }, this.options);
      const doc = new Document(void 0, opts);
      if (this.atDirectives)
        this.onError(endOffset, "MISSING_CHAR", "Missing directives-end indicator line");
      doc.range = [0, endOffset, endOffset];
      this.decorate(doc, false);
      yield doc;
    }
  }
};

// ../../../frontend/node_modules/yaml/browser/dist/parse/cst-visit.js
var BREAK2 = /* @__PURE__ */ Symbol("break visit");
var SKIP2 = /* @__PURE__ */ Symbol("skip children");
var REMOVE2 = /* @__PURE__ */ Symbol("remove item");
function visit2(cst, visitor) {
  if ("type" in cst && cst.type === "document")
    cst = { start: cst.start, value: cst.value };
  _visit(Object.freeze([]), cst, visitor);
}
visit2.BREAK = BREAK2;
visit2.SKIP = SKIP2;
visit2.REMOVE = REMOVE2;
visit2.itemAtPath = (cst, path) => {
  let item = cst;
  for (const [field, index] of path) {
    const tok = item?.[field];
    if (tok && "items" in tok) {
      item = tok.items[index];
    } else
      return void 0;
  }
  return item;
};
visit2.parentCollection = (cst, path) => {
  const parent = visit2.itemAtPath(cst, path.slice(0, -1));
  const field = path[path.length - 1][0];
  const coll = parent?.[field];
  if (coll && "items" in coll)
    return coll;
  throw new Error("Parent collection not found");
};
function _visit(path, item, visitor) {
  let ctrl = visitor(item, path);
  if (typeof ctrl === "symbol")
    return ctrl;
  for (const field of ["key", "value"]) {
    const token = item[field];
    if (token && "items" in token) {
      for (let i = 0; i < token.items.length; ++i) {
        const ci = _visit(Object.freeze(path.concat([[field, i]])), token.items[i], visitor);
        if (typeof ci === "number")
          i = ci - 1;
        else if (ci === BREAK2)
          return BREAK2;
        else if (ci === REMOVE2) {
          token.items.splice(i, 1);
          i -= 1;
        }
      }
      if (typeof ctrl === "function" && field === "key")
        ctrl = ctrl(item, path);
    }
  }
  return typeof ctrl === "function" ? ctrl(item, path) : ctrl;
}

// ../../../frontend/node_modules/yaml/browser/dist/parse/cst.js
var BOM = "\uFEFF";
var DOCUMENT = "";
var FLOW_END = "";
var SCALAR2 = "";
function tokenType(source) {
  switch (source) {
    case BOM:
      return "byte-order-mark";
    case DOCUMENT:
      return "doc-mode";
    case FLOW_END:
      return "flow-error-end";
    case SCALAR2:
      return "scalar";
    case "---":
      return "doc-start";
    case "...":
      return "doc-end";
    case "":
    case "\n":
    case "\r\n":
      return "newline";
    case "-":
      return "seq-item-ind";
    case "?":
      return "explicit-key-ind";
    case ":":
      return "map-value-ind";
    case "{":
      return "flow-map-start";
    case "}":
      return "flow-map-end";
    case "[":
      return "flow-seq-start";
    case "]":
      return "flow-seq-end";
    case ",":
      return "comma";
  }
  switch (source[0]) {
    case " ":
    case "	":
      return "space";
    case "#":
      return "comment";
    case "%":
      return "directive-line";
    case "*":
      return "alias";
    case "&":
      return "anchor";
    case "!":
      return "tag";
    case "'":
      return "single-quoted-scalar";
    case '"':
      return "double-quoted-scalar";
    case "|":
    case ">":
      return "block-scalar-header";
  }
  return null;
}

// ../../../frontend/node_modules/yaml/browser/dist/parse/lexer.js
function isEmpty(ch) {
  switch (ch) {
    case void 0:
    case " ":
    case "\n":
    case "\r":
    case "	":
      return true;
    default:
      return false;
  }
}
var hexDigits = new Set("0123456789ABCDEFabcdef");
var tagChars = new Set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-#;/?:@&=+$_.!~*'()");
var flowIndicatorChars = new Set(",[]{}");
var invalidAnchorChars = new Set(" ,[]{}\n\r	");
var isNotAnchorChar = (ch) => !ch || invalidAnchorChars.has(ch);
var Lexer = class {
  constructor() {
    this.atEnd = false;
    this.blockScalarIndent = -1;
    this.blockScalarKeep = false;
    this.buffer = "";
    this.flowKey = false;
    this.flowLevel = 0;
    this.indentNext = 0;
    this.indentValue = 0;
    this.lineEndPos = null;
    this.next = null;
    this.pos = 0;
  }
  /**
   * Generate YAML tokens from the `source` string. If `incomplete`,
   * a part of the last line may be left as a buffer for the next call.
   *
   * @returns A generator of lexical tokens
   */
  *lex(source, incomplete = false) {
    if (source) {
      if (typeof source !== "string")
        throw TypeError("source is not a string");
      this.buffer = this.buffer ? this.buffer + source : source;
      this.lineEndPos = null;
    }
    this.atEnd = !incomplete;
    let next = this.next ?? "stream";
    while (next && (incomplete || this.hasChars(1)))
      next = yield* this.parseNext(next);
  }
  atLineEnd() {
    let i = this.pos;
    let ch = this.buffer[i];
    while (ch === " " || ch === "	")
      ch = this.buffer[++i];
    if (!ch || ch === "#" || ch === "\n")
      return true;
    if (ch === "\r")
      return this.buffer[i + 1] === "\n";
    return false;
  }
  charAt(n) {
    return this.buffer[this.pos + n];
  }
  continueScalar(offset) {
    let ch = this.buffer[offset];
    if (this.indentNext > 0) {
      let indent = 0;
      while (ch === " ")
        ch = this.buffer[++indent + offset];
      if (ch === "\r") {
        const next = this.buffer[indent + offset + 1];
        if (next === "\n" || !next && !this.atEnd)
          return offset + indent + 1;
      }
      return ch === "\n" || indent >= this.indentNext || !ch && !this.atEnd ? offset + indent : -1;
    }
    if (ch === "-" || ch === ".") {
      const dt = this.buffer.substr(offset, 3);
      if ((dt === "---" || dt === "...") && isEmpty(this.buffer[offset + 3]))
        return -1;
    }
    return offset;
  }
  getLine() {
    let end = this.lineEndPos;
    if (typeof end !== "number" || end !== -1 && end < this.pos) {
      end = this.buffer.indexOf("\n", this.pos);
      this.lineEndPos = end;
    }
    if (end === -1)
      return this.atEnd ? this.buffer.substring(this.pos) : null;
    if (this.buffer[end - 1] === "\r")
      end -= 1;
    return this.buffer.substring(this.pos, end);
  }
  hasChars(n) {
    return this.pos + n <= this.buffer.length;
  }
  setNext(state) {
    this.buffer = this.buffer.substring(this.pos);
    this.pos = 0;
    this.lineEndPos = null;
    this.next = state;
    return null;
  }
  peek(n) {
    return this.buffer.substr(this.pos, n);
  }
  *parseNext(next) {
    switch (next) {
      case "stream":
        return yield* this.parseStream();
      case "line-start":
        return yield* this.parseLineStart();
      case "block-start":
        return yield* this.parseBlockStart();
      case "doc":
        return yield* this.parseDocument();
      case "flow":
        return yield* this.parseFlowCollection();
      case "quoted-scalar":
        return yield* this.parseQuotedScalar();
      case "block-scalar":
        return yield* this.parseBlockScalar();
      case "plain-scalar":
        return yield* this.parsePlainScalar();
    }
  }
  *parseStream() {
    let line = this.getLine();
    if (line === null)
      return this.setNext("stream");
    if (line[0] === BOM) {
      yield* this.pushCount(1);
      line = line.substring(1);
    }
    if (line[0] === "%") {
      let dirEnd = line.length;
      let cs = line.indexOf("#");
      while (cs !== -1) {
        const ch = line[cs - 1];
        if (ch === " " || ch === "	") {
          dirEnd = cs - 1;
          break;
        } else {
          cs = line.indexOf("#", cs + 1);
        }
      }
      while (true) {
        const ch = line[dirEnd - 1];
        if (ch === " " || ch === "	")
          dirEnd -= 1;
        else
          break;
      }
      const n = (yield* this.pushCount(dirEnd)) + (yield* this.pushSpaces(true));
      yield* this.pushCount(line.length - n);
      this.pushNewline();
      return "stream";
    }
    if (this.atLineEnd()) {
      const sp = yield* this.pushSpaces(true);
      yield* this.pushCount(line.length - sp);
      yield* this.pushNewline();
      return "stream";
    }
    yield DOCUMENT;
    return yield* this.parseLineStart();
  }
  *parseLineStart() {
    const ch = this.charAt(0);
    if (!ch && !this.atEnd)
      return this.setNext("line-start");
    if (ch === "-" || ch === ".") {
      if (!this.atEnd && !this.hasChars(4))
        return this.setNext("line-start");
      const s = this.peek(3);
      if ((s === "---" || s === "...") && isEmpty(this.charAt(3))) {
        yield* this.pushCount(3);
        this.indentValue = 0;
        this.indentNext = 0;
        return s === "---" ? "doc" : "stream";
      }
    }
    this.indentValue = yield* this.pushSpaces(false);
    if (this.indentNext > this.indentValue && !isEmpty(this.charAt(1)))
      this.indentNext = this.indentValue;
    return yield* this.parseBlockStart();
  }
  *parseBlockStart() {
    const [ch0, ch1] = this.peek(2);
    if (!ch1 && !this.atEnd)
      return this.setNext("block-start");
    if ((ch0 === "-" || ch0 === "?" || ch0 === ":") && isEmpty(ch1)) {
      const n = (yield* this.pushCount(1)) + (yield* this.pushSpaces(true));
      this.indentNext = this.indentValue + 1;
      this.indentValue += n;
      return "block-start";
    }
    return "doc";
  }
  *parseDocument() {
    yield* this.pushSpaces(true);
    const line = this.getLine();
    if (line === null)
      return this.setNext("doc");
    let n = yield* this.pushIndicators();
    switch (line[n]) {
      case "#":
        yield* this.pushCount(line.length - n);
      // fallthrough
      case void 0:
        yield* this.pushNewline();
        return yield* this.parseLineStart();
      case "{":
      case "[":
        yield* this.pushCount(1);
        this.flowKey = false;
        this.flowLevel = 1;
        return "flow";
      case "}":
      case "]":
        yield* this.pushCount(1);
        return "doc";
      case "*":
        yield* this.pushUntil(isNotAnchorChar);
        return "doc";
      case '"':
      case "'":
        return yield* this.parseQuotedScalar();
      case "|":
      case ">":
        n += yield* this.parseBlockScalarHeader();
        n += yield* this.pushSpaces(true);
        yield* this.pushCount(line.length - n);
        yield* this.pushNewline();
        return yield* this.parseBlockScalar();
      default:
        return yield* this.parsePlainScalar();
    }
  }
  *parseFlowCollection() {
    let nl, sp;
    let indent = -1;
    do {
      nl = yield* this.pushNewline();
      if (nl > 0) {
        sp = yield* this.pushSpaces(false);
        this.indentValue = indent = sp;
      } else {
        sp = 0;
      }
      sp += yield* this.pushSpaces(true);
    } while (nl + sp > 0);
    const line = this.getLine();
    if (line === null)
      return this.setNext("flow");
    if (indent !== -1 && indent < this.indentNext && line[0] !== "#" || indent === 0 && (line.startsWith("---") || line.startsWith("...")) && isEmpty(line[3])) {
      const atFlowEndMarker = indent === this.indentNext - 1 && this.flowLevel === 1 && (line[0] === "]" || line[0] === "}");
      if (!atFlowEndMarker) {
        this.flowLevel = 0;
        yield FLOW_END;
        return yield* this.parseLineStart();
      }
    }
    let n = 0;
    while (line[n] === ",") {
      n += yield* this.pushCount(1);
      n += yield* this.pushSpaces(true);
      this.flowKey = false;
    }
    n += yield* this.pushIndicators();
    switch (line[n]) {
      case void 0:
        return "flow";
      case "#":
        yield* this.pushCount(line.length - n);
        return "flow";
      case "{":
      case "[":
        yield* this.pushCount(1);
        this.flowKey = false;
        this.flowLevel += 1;
        return "flow";
      case "}":
      case "]":
        yield* this.pushCount(1);
        this.flowKey = true;
        this.flowLevel -= 1;
        return this.flowLevel ? "flow" : "doc";
      case "*":
        yield* this.pushUntil(isNotAnchorChar);
        return "flow";
      case '"':
      case "'":
        this.flowKey = true;
        return yield* this.parseQuotedScalar();
      case ":": {
        const next = this.charAt(1);
        if (this.flowKey || isEmpty(next) || next === ",") {
          this.flowKey = false;
          yield* this.pushCount(1);
          yield* this.pushSpaces(true);
          return "flow";
        }
      }
      // fallthrough
      default:
        this.flowKey = false;
        return yield* this.parsePlainScalar();
    }
  }
  *parseQuotedScalar() {
    const quote = this.charAt(0);
    let end = this.buffer.indexOf(quote, this.pos + 1);
    if (quote === "'") {
      while (end !== -1 && this.buffer[end + 1] === "'")
        end = this.buffer.indexOf("'", end + 2);
    } else {
      while (end !== -1) {
        let n = 0;
        while (this.buffer[end - 1 - n] === "\\")
          n += 1;
        if (n % 2 === 0)
          break;
        end = this.buffer.indexOf('"', end + 1);
      }
    }
    const qb = this.buffer.substring(0, end);
    let nl = qb.indexOf("\n", this.pos);
    if (nl !== -1) {
      while (nl !== -1) {
        const cs = this.continueScalar(nl + 1);
        if (cs === -1)
          break;
        nl = qb.indexOf("\n", cs);
      }
      if (nl !== -1) {
        end = nl - (qb[nl - 1] === "\r" ? 2 : 1);
      }
    }
    if (end === -1) {
      if (!this.atEnd)
        return this.setNext("quoted-scalar");
      end = this.buffer.length;
    }
    yield* this.pushToIndex(end + 1, false);
    return this.flowLevel ? "flow" : "doc";
  }
  *parseBlockScalarHeader() {
    this.blockScalarIndent = -1;
    this.blockScalarKeep = false;
    let i = this.pos;
    while (true) {
      const ch = this.buffer[++i];
      if (ch === "+")
        this.blockScalarKeep = true;
      else if (ch > "0" && ch <= "9")
        this.blockScalarIndent = Number(ch) - 1;
      else if (ch !== "-")
        break;
    }
    return yield* this.pushUntil((ch) => isEmpty(ch) || ch === "#");
  }
  *parseBlockScalar() {
    let nl = this.pos - 1;
    let indent = 0;
    let ch;
    loop: for (let i2 = this.pos; ch = this.buffer[i2]; ++i2) {
      switch (ch) {
        case " ":
          indent += 1;
          break;
        case "\n":
          nl = i2;
          indent = 0;
          break;
        case "\r": {
          const next = this.buffer[i2 + 1];
          if (!next && !this.atEnd)
            return this.setNext("block-scalar");
          if (next === "\n")
            break;
        }
        // fallthrough
        default:
          break loop;
      }
    }
    if (!ch && !this.atEnd)
      return this.setNext("block-scalar");
    if (indent >= this.indentNext) {
      if (this.blockScalarIndent === -1)
        this.indentNext = indent;
      else {
        this.indentNext = this.blockScalarIndent + (this.indentNext === 0 ? 1 : this.indentNext);
      }
      do {
        const cs = this.continueScalar(nl + 1);
        if (cs === -1)
          break;
        nl = this.buffer.indexOf("\n", cs);
      } while (nl !== -1);
      if (nl === -1) {
        if (!this.atEnd)
          return this.setNext("block-scalar");
        nl = this.buffer.length;
      }
    }
    let i = nl + 1;
    ch = this.buffer[i];
    while (ch === " ")
      ch = this.buffer[++i];
    if (ch === "	") {
      while (ch === "	" || ch === " " || ch === "\r" || ch === "\n")
        ch = this.buffer[++i];
      nl = i - 1;
    } else if (!this.blockScalarKeep) {
      do {
        let i2 = nl - 1;
        let ch2 = this.buffer[i2];
        if (ch2 === "\r")
          ch2 = this.buffer[--i2];
        const lastChar = i2;
        while (ch2 === " ")
          ch2 = this.buffer[--i2];
        if (ch2 === "\n" && i2 >= this.pos && i2 + 1 + indent > lastChar)
          nl = i2;
        else
          break;
      } while (true);
    }
    yield SCALAR2;
    yield* this.pushToIndex(nl + 1, true);
    return yield* this.parseLineStart();
  }
  *parsePlainScalar() {
    const inFlow = this.flowLevel > 0;
    let end = this.pos - 1;
    let i = this.pos - 1;
    let ch;
    while (ch = this.buffer[++i]) {
      if (ch === ":") {
        const next = this.buffer[i + 1];
        if (isEmpty(next) || inFlow && flowIndicatorChars.has(next))
          break;
        end = i;
      } else if (isEmpty(ch)) {
        let next = this.buffer[i + 1];
        if (ch === "\r") {
          if (next === "\n") {
            i += 1;
            ch = "\n";
            next = this.buffer[i + 1];
          } else
            end = i;
        }
        if (next === "#" || inFlow && flowIndicatorChars.has(next))
          break;
        if (ch === "\n") {
          const cs = this.continueScalar(i + 1);
          if (cs === -1)
            break;
          i = Math.max(i, cs - 2);
        }
      } else {
        if (inFlow && flowIndicatorChars.has(ch))
          break;
        end = i;
      }
    }
    if (!ch && !this.atEnd)
      return this.setNext("plain-scalar");
    yield SCALAR2;
    yield* this.pushToIndex(end + 1, true);
    return inFlow ? "flow" : "doc";
  }
  *pushCount(n) {
    if (n > 0) {
      yield this.buffer.substr(this.pos, n);
      this.pos += n;
      return n;
    }
    return 0;
  }
  *pushToIndex(i, allowEmpty) {
    const s = this.buffer.slice(this.pos, i);
    if (s) {
      yield s;
      this.pos += s.length;
      return s.length;
    } else if (allowEmpty)
      yield "";
    return 0;
  }
  *pushIndicators() {
    let n = 0;
    loop: while (true) {
      switch (this.charAt(0)) {
        case "!":
          n += yield* this.pushTag();
          n += yield* this.pushSpaces(true);
          continue loop;
        case "&":
          n += yield* this.pushUntil(isNotAnchorChar);
          n += yield* this.pushSpaces(true);
          continue loop;
        case "-":
        // this is an error
        case "?":
        // this is an error outside flow collections
        case ":": {
          const inFlow = this.flowLevel > 0;
          const ch1 = this.charAt(1);
          if (isEmpty(ch1) || inFlow && flowIndicatorChars.has(ch1)) {
            if (!inFlow)
              this.indentNext = this.indentValue + 1;
            else if (this.flowKey)
              this.flowKey = false;
            n += yield* this.pushCount(1);
            n += yield* this.pushSpaces(true);
            continue loop;
          }
        }
      }
      break loop;
    }
    return n;
  }
  *pushTag() {
    if (this.charAt(1) === "<") {
      let i = this.pos + 2;
      let ch = this.buffer[i];
      while (!isEmpty(ch) && ch !== ">")
        ch = this.buffer[++i];
      return yield* this.pushToIndex(ch === ">" ? i + 1 : i, false);
    } else {
      let i = this.pos + 1;
      let ch = this.buffer[i];
      while (ch) {
        if (tagChars.has(ch))
          ch = this.buffer[++i];
        else if (ch === "%" && hexDigits.has(this.buffer[i + 1]) && hexDigits.has(this.buffer[i + 2])) {
          ch = this.buffer[i += 3];
        } else
          break;
      }
      return yield* this.pushToIndex(i, false);
    }
  }
  *pushNewline() {
    const ch = this.buffer[this.pos];
    if (ch === "\n")
      return yield* this.pushCount(1);
    else if (ch === "\r" && this.charAt(1) === "\n")
      return yield* this.pushCount(2);
    else
      return 0;
  }
  *pushSpaces(allowTabs) {
    let i = this.pos - 1;
    let ch;
    do {
      ch = this.buffer[++i];
    } while (ch === " " || allowTabs && ch === "	");
    const n = i - this.pos;
    if (n > 0) {
      yield this.buffer.substr(this.pos, n);
      this.pos = i;
    }
    return n;
  }
  *pushUntil(test) {
    let i = this.pos;
    let ch = this.buffer[i];
    while (!test(ch))
      ch = this.buffer[++i];
    return yield* this.pushToIndex(i, false);
  }
};

// ../../../frontend/node_modules/yaml/browser/dist/parse/line-counter.js
var LineCounter = class {
  constructor() {
    this.lineStarts = [];
    this.addNewLine = (offset) => this.lineStarts.push(offset);
    this.linePos = (offset) => {
      let low = 0;
      let high = this.lineStarts.length;
      while (low < high) {
        const mid = low + high >> 1;
        if (this.lineStarts[mid] < offset)
          low = mid + 1;
        else
          high = mid;
      }
      if (this.lineStarts[low] === offset)
        return { line: low + 1, col: 1 };
      if (low === 0)
        return { line: 0, col: offset };
      const start = this.lineStarts[low - 1];
      return { line: low, col: offset - start + 1 };
    };
  }
};

// ../../../frontend/node_modules/yaml/browser/dist/parse/parser.js
function includesToken(list, type) {
  for (let i = 0; i < list.length; ++i)
    if (list[i].type === type)
      return true;
  return false;
}
function findNonEmptyIndex(list) {
  for (let i = 0; i < list.length; ++i) {
    switch (list[i].type) {
      case "space":
      case "comment":
      case "newline":
        break;
      default:
        return i;
    }
  }
  return -1;
}
function isFlowToken(token) {
  switch (token?.type) {
    case "alias":
    case "scalar":
    case "single-quoted-scalar":
    case "double-quoted-scalar":
    case "flow-collection":
      return true;
    default:
      return false;
  }
}
function getPrevProps(parent) {
  switch (parent.type) {
    case "document":
      return parent.start;
    case "block-map": {
      const it = parent.items[parent.items.length - 1];
      return it.sep ?? it.start;
    }
    case "block-seq":
      return parent.items[parent.items.length - 1].start;
    /* istanbul ignore next should not happen */
    default:
      return [];
  }
}
function getFirstKeyStartProps(prev) {
  if (prev.length === 0)
    return [];
  let i = prev.length;
  loop: while (--i >= 0) {
    switch (prev[i].type) {
      case "doc-start":
      case "explicit-key-ind":
      case "map-value-ind":
      case "seq-item-ind":
      case "newline":
        break loop;
    }
  }
  while (prev[++i]?.type === "space") {
  }
  return prev.splice(i, prev.length);
}
function arrayPushArray(target, source) {
  if (source.length < 1e5)
    Array.prototype.push.apply(target, source);
  else
    for (let i = 0; i < source.length; ++i)
      target.push(source[i]);
}
function fixFlowSeqItems(fc) {
  if (fc.start.type === "flow-seq-start") {
    for (const it of fc.items) {
      if (it.sep && !it.value && !includesToken(it.start, "explicit-key-ind") && !includesToken(it.sep, "map-value-ind")) {
        if (it.key)
          it.value = it.key;
        delete it.key;
        if (isFlowToken(it.value)) {
          if (it.value.end)
            arrayPushArray(it.value.end, it.sep);
          else
            it.value.end = it.sep;
        } else
          arrayPushArray(it.start, it.sep);
        delete it.sep;
      }
    }
  }
}
var Parser = class {
  /**
   * @param onNewLine - If defined, called separately with the start position of
   *   each new line (in `parse()`, including the start of input).
   */
  constructor(onNewLine) {
    this.atNewLine = true;
    this.atScalar = false;
    this.indent = 0;
    this.offset = 0;
    this.onKeyLine = false;
    this.stack = [];
    this.source = "";
    this.type = "";
    this.lexer = new Lexer();
    this.onNewLine = onNewLine;
  }
  /**
   * Parse `source` as a YAML stream.
   * If `incomplete`, a part of the last line may be left as a buffer for the next call.
   *
   * Errors are not thrown, but yielded as `{ type: 'error', message }` tokens.
   *
   * @returns A generator of tokens representing each directive, document, and other structure.
   */
  *parse(source, incomplete = false) {
    if (this.onNewLine && this.offset === 0)
      this.onNewLine(0);
    for (const lexeme of this.lexer.lex(source, incomplete))
      yield* this.next(lexeme);
    if (!incomplete)
      yield* this.end();
  }
  /**
   * Advance the parser by the `source` of one lexical token.
   */
  *next(source) {
    this.source = source;
    if (this.atScalar) {
      this.atScalar = false;
      yield* this.step();
      this.offset += source.length;
      return;
    }
    const type = tokenType(source);
    if (!type) {
      const message = `Not a YAML token: ${source}`;
      yield* this.pop({ type: "error", offset: this.offset, message, source });
      this.offset += source.length;
    } else if (type === "scalar") {
      this.atNewLine = false;
      this.atScalar = true;
      this.type = "scalar";
    } else {
      this.type = type;
      yield* this.step();
      switch (type) {
        case "newline":
          this.atNewLine = true;
          this.indent = 0;
          if (this.onNewLine)
            this.onNewLine(this.offset + source.length);
          break;
        case "space":
          if (this.atNewLine && source[0] === " ")
            this.indent += source.length;
          break;
        case "explicit-key-ind":
        case "map-value-ind":
        case "seq-item-ind":
          if (this.atNewLine)
            this.indent += source.length;
          break;
        case "doc-mode":
        case "flow-error-end":
          return;
        default:
          this.atNewLine = false;
      }
      this.offset += source.length;
    }
  }
  /** Call at end of input to push out any remaining constructions */
  *end() {
    while (this.stack.length > 0)
      yield* this.pop();
  }
  get sourceToken() {
    const st = {
      type: this.type,
      offset: this.offset,
      indent: this.indent,
      source: this.source
    };
    return st;
  }
  *step() {
    const top = this.peek(1);
    if (this.type === "doc-end" && top?.type !== "doc-end") {
      while (this.stack.length > 0)
        yield* this.pop();
      this.stack.push({
        type: "doc-end",
        offset: this.offset,
        source: this.source
      });
      return;
    }
    if (!top)
      return yield* this.stream();
    switch (top.type) {
      case "document":
        return yield* this.document(top);
      case "alias":
      case "scalar":
      case "single-quoted-scalar":
      case "double-quoted-scalar":
        return yield* this.scalar(top);
      case "block-scalar":
        return yield* this.blockScalar(top);
      case "block-map":
        return yield* this.blockMap(top);
      case "block-seq":
        return yield* this.blockSequence(top);
      case "flow-collection":
        return yield* this.flowCollection(top);
      case "doc-end":
        return yield* this.documentEnd(top);
    }
    yield* this.pop();
  }
  peek(n) {
    return this.stack[this.stack.length - n];
  }
  *pop(error) {
    const token = error ?? this.stack.pop();
    if (!token) {
      const message = "Tried to pop an empty stack";
      yield { type: "error", offset: this.offset, source: "", message };
    } else if (this.stack.length === 0) {
      yield token;
    } else {
      const top = this.peek(1);
      if (token.type === "block-scalar") {
        token.indent = "indent" in top ? top.indent : 0;
      } else if (token.type === "flow-collection" && top.type === "document") {
        token.indent = 0;
      }
      if (token.type === "flow-collection")
        fixFlowSeqItems(token);
      switch (top.type) {
        case "document":
          top.value = token;
          break;
        case "block-scalar":
          top.props.push(token);
          break;
        case "block-map": {
          const it = top.items[top.items.length - 1];
          if (it.value) {
            top.items.push({ start: [], key: token, sep: [] });
            this.onKeyLine = true;
            return;
          } else if (it.sep) {
            it.value = token;
          } else {
            Object.assign(it, { key: token, sep: [] });
            this.onKeyLine = !it.explicitKey;
            return;
          }
          break;
        }
        case "block-seq": {
          const it = top.items[top.items.length - 1];
          if (it.value)
            top.items.push({ start: [], value: token });
          else
            it.value = token;
          break;
        }
        case "flow-collection": {
          const it = top.items[top.items.length - 1];
          if (!it || it.value)
            top.items.push({ start: [], key: token, sep: [] });
          else if (it.sep)
            it.value = token;
          else
            Object.assign(it, { key: token, sep: [] });
          return;
        }
        /* istanbul ignore next should not happen */
        default:
          yield* this.pop();
          yield* this.pop(token);
      }
      if ((top.type === "document" || top.type === "block-map" || top.type === "block-seq") && (token.type === "block-map" || token.type === "block-seq")) {
        const last = token.items[token.items.length - 1];
        if (last && !last.sep && !last.value && last.start.length > 0 && findNonEmptyIndex(last.start) === -1 && (token.indent === 0 || last.start.every((st) => st.type !== "comment" || st.indent < token.indent))) {
          if (top.type === "document")
            top.end = last.start;
          else
            top.items.push({ start: last.start });
          token.items.splice(-1, 1);
        }
      }
    }
  }
  *stream() {
    switch (this.type) {
      case "directive-line":
        yield { type: "directive", offset: this.offset, source: this.source };
        return;
      case "byte-order-mark":
      case "space":
      case "comment":
      case "newline":
        yield this.sourceToken;
        return;
      case "doc-mode":
      case "doc-start": {
        const doc = {
          type: "document",
          offset: this.offset,
          start: []
        };
        if (this.type === "doc-start")
          doc.start.push(this.sourceToken);
        this.stack.push(doc);
        return;
      }
    }
    yield {
      type: "error",
      offset: this.offset,
      message: `Unexpected ${this.type} token in YAML stream`,
      source: this.source
    };
  }
  *document(doc) {
    if (doc.value)
      return yield* this.lineEnd(doc);
    switch (this.type) {
      case "doc-start": {
        if (findNonEmptyIndex(doc.start) !== -1) {
          yield* this.pop();
          yield* this.step();
        } else
          doc.start.push(this.sourceToken);
        return;
      }
      case "anchor":
      case "tag":
      case "space":
      case "comment":
      case "newline":
        doc.start.push(this.sourceToken);
        return;
    }
    const bv = this.startBlockValue(doc);
    if (bv)
      this.stack.push(bv);
    else {
      yield {
        type: "error",
        offset: this.offset,
        message: `Unexpected ${this.type} token in YAML document`,
        source: this.source
      };
    }
  }
  *scalar(scalar) {
    if (this.type === "map-value-ind") {
      const prev = getPrevProps(this.peek(2));
      const start = getFirstKeyStartProps(prev);
      let sep;
      if (scalar.end) {
        sep = scalar.end;
        sep.push(this.sourceToken);
        delete scalar.end;
      } else
        sep = [this.sourceToken];
      const map2 = {
        type: "block-map",
        offset: scalar.offset,
        indent: scalar.indent,
        items: [{ start, key: scalar, sep }]
      };
      this.onKeyLine = true;
      this.stack[this.stack.length - 1] = map2;
    } else
      yield* this.lineEnd(scalar);
  }
  *blockScalar(scalar) {
    switch (this.type) {
      case "space":
      case "comment":
      case "newline":
        scalar.props.push(this.sourceToken);
        return;
      case "scalar":
        scalar.source = this.source;
        this.atNewLine = true;
        this.indent = 0;
        if (this.onNewLine) {
          let nl = this.source.indexOf("\n") + 1;
          while (nl !== 0) {
            this.onNewLine(this.offset + nl);
            nl = this.source.indexOf("\n", nl) + 1;
          }
        }
        yield* this.pop();
        break;
      /* istanbul ignore next should not happen */
      default:
        yield* this.pop();
        yield* this.step();
    }
  }
  *blockMap(map2) {
    const it = map2.items[map2.items.length - 1];
    switch (this.type) {
      case "newline":
        this.onKeyLine = false;
        if (it.value) {
          const end = "end" in it.value ? it.value.end : void 0;
          const last = Array.isArray(end) ? end[end.length - 1] : void 0;
          if (last?.type === "comment")
            end?.push(this.sourceToken);
          else
            map2.items.push({ start: [this.sourceToken] });
        } else if (it.sep) {
          it.sep.push(this.sourceToken);
        } else {
          it.start.push(this.sourceToken);
        }
        return;
      case "space":
      case "comment":
        if (it.value) {
          map2.items.push({ start: [this.sourceToken] });
        } else if (it.sep) {
          it.sep.push(this.sourceToken);
        } else {
          if (this.atIndentedComment(it.start, map2.indent)) {
            const prev = map2.items[map2.items.length - 2];
            const end = prev?.value?.end;
            if (Array.isArray(end)) {
              arrayPushArray(end, it.start);
              end.push(this.sourceToken);
              map2.items.pop();
              return;
            }
          }
          it.start.push(this.sourceToken);
        }
        return;
    }
    if (this.indent >= map2.indent) {
      const atMapIndent = !this.onKeyLine && this.indent === map2.indent;
      const atNextItem = atMapIndent && (it.sep || it.explicitKey) && this.type !== "seq-item-ind";
      let start = [];
      if (atNextItem && it.sep && !it.value) {
        const nl = [];
        for (let i = 0; i < it.sep.length; ++i) {
          const st = it.sep[i];
          switch (st.type) {
            case "newline":
              nl.push(i);
              break;
            case "space":
              break;
            case "comment":
              if (st.indent > map2.indent)
                nl.length = 0;
              break;
            default:
              nl.length = 0;
          }
        }
        if (nl.length >= 2)
          start = it.sep.splice(nl[1]);
      }
      switch (this.type) {
        case "anchor":
        case "tag":
          if (atNextItem || it.value) {
            start.push(this.sourceToken);
            map2.items.push({ start });
            this.onKeyLine = true;
          } else if (it.sep) {
            it.sep.push(this.sourceToken);
          } else {
            it.start.push(this.sourceToken);
          }
          return;
        case "explicit-key-ind":
          if (!it.sep && !it.explicitKey) {
            it.start.push(this.sourceToken);
            it.explicitKey = true;
          } else if (atNextItem || it.value) {
            start.push(this.sourceToken);
            map2.items.push({ start, explicitKey: true });
          } else {
            this.stack.push({
              type: "block-map",
              offset: this.offset,
              indent: this.indent,
              items: [{ start: [this.sourceToken], explicitKey: true }]
            });
          }
          this.onKeyLine = true;
          return;
        case "map-value-ind":
          if (it.explicitKey) {
            if (!it.sep) {
              if (includesToken(it.start, "newline")) {
                Object.assign(it, { key: null, sep: [this.sourceToken] });
              } else {
                const start2 = getFirstKeyStartProps(it.start);
                this.stack.push({
                  type: "block-map",
                  offset: this.offset,
                  indent: this.indent,
                  items: [{ start: start2, key: null, sep: [this.sourceToken] }]
                });
              }
            } else if (it.value) {
              map2.items.push({ start: [], key: null, sep: [this.sourceToken] });
            } else if (includesToken(it.sep, "map-value-ind")) {
              this.stack.push({
                type: "block-map",
                offset: this.offset,
                indent: this.indent,
                items: [{ start, key: null, sep: [this.sourceToken] }]
              });
            } else if (isFlowToken(it.key) && !includesToken(it.sep, "newline")) {
              const start2 = getFirstKeyStartProps(it.start);
              const key = it.key;
              const sep = it.sep;
              sep.push(this.sourceToken);
              delete it.key;
              delete it.sep;
              this.stack.push({
                type: "block-map",
                offset: this.offset,
                indent: this.indent,
                items: [{ start: start2, key, sep }]
              });
            } else if (start.length > 0) {
              it.sep = it.sep.concat(start, this.sourceToken);
            } else {
              it.sep.push(this.sourceToken);
            }
          } else {
            if (!it.sep) {
              Object.assign(it, { key: null, sep: [this.sourceToken] });
            } else if (it.value || atNextItem) {
              map2.items.push({ start, key: null, sep: [this.sourceToken] });
            } else if (includesToken(it.sep, "map-value-ind")) {
              this.stack.push({
                type: "block-map",
                offset: this.offset,
                indent: this.indent,
                items: [{ start: [], key: null, sep: [this.sourceToken] }]
              });
            } else {
              it.sep.push(this.sourceToken);
            }
          }
          this.onKeyLine = true;
          return;
        case "alias":
        case "scalar":
        case "single-quoted-scalar":
        case "double-quoted-scalar": {
          const fs = this.flowScalar(this.type);
          if (atNextItem || it.value) {
            map2.items.push({ start, key: fs, sep: [] });
            this.onKeyLine = true;
          } else if (it.sep) {
            this.stack.push(fs);
          } else {
            Object.assign(it, { key: fs, sep: [] });
            this.onKeyLine = true;
          }
          return;
        }
        default: {
          const bv = this.startBlockValue(map2);
          if (bv) {
            if (bv.type === "block-seq") {
              if (!it.explicitKey && it.sep && !includesToken(it.sep, "newline")) {
                yield* this.pop({
                  type: "error",
                  offset: this.offset,
                  message: "Unexpected block-seq-ind on same line with key",
                  source: this.source
                });
                return;
              }
            } else if (atMapIndent) {
              map2.items.push({ start });
            }
            this.stack.push(bv);
            return;
          }
        }
      }
    }
    yield* this.pop();
    yield* this.step();
  }
  *blockSequence(seq2) {
    const it = seq2.items[seq2.items.length - 1];
    switch (this.type) {
      case "newline":
        if (it.value) {
          const end = "end" in it.value ? it.value.end : void 0;
          const last = Array.isArray(end) ? end[end.length - 1] : void 0;
          if (last?.type === "comment")
            end?.push(this.sourceToken);
          else
            seq2.items.push({ start: [this.sourceToken] });
        } else
          it.start.push(this.sourceToken);
        return;
      case "space":
      case "comment":
        if (it.value)
          seq2.items.push({ start: [this.sourceToken] });
        else {
          if (this.atIndentedComment(it.start, seq2.indent)) {
            const prev = seq2.items[seq2.items.length - 2];
            const end = prev?.value?.end;
            if (Array.isArray(end)) {
              arrayPushArray(end, it.start);
              end.push(this.sourceToken);
              seq2.items.pop();
              return;
            }
          }
          it.start.push(this.sourceToken);
        }
        return;
      case "anchor":
      case "tag":
        if (it.value || this.indent <= seq2.indent)
          break;
        it.start.push(this.sourceToken);
        return;
      case "seq-item-ind":
        if (this.indent !== seq2.indent)
          break;
        if (it.value || includesToken(it.start, "seq-item-ind"))
          seq2.items.push({ start: [this.sourceToken] });
        else
          it.start.push(this.sourceToken);
        return;
    }
    if (this.indent > seq2.indent) {
      const bv = this.startBlockValue(seq2);
      if (bv) {
        this.stack.push(bv);
        return;
      }
    }
    yield* this.pop();
    yield* this.step();
  }
  *flowCollection(fc) {
    const it = fc.items[fc.items.length - 1];
    if (this.type === "flow-error-end") {
      let top;
      do {
        yield* this.pop();
        top = this.peek(1);
      } while (top?.type === "flow-collection");
    } else if (fc.end.length === 0) {
      switch (this.type) {
        case "comma":
        case "explicit-key-ind":
          if (!it || it.sep)
            fc.items.push({ start: [this.sourceToken] });
          else
            it.start.push(this.sourceToken);
          return;
        case "map-value-ind":
          if (!it || it.value)
            fc.items.push({ start: [], key: null, sep: [this.sourceToken] });
          else if (it.sep)
            it.sep.push(this.sourceToken);
          else
            Object.assign(it, { key: null, sep: [this.sourceToken] });
          return;
        case "space":
        case "comment":
        case "newline":
        case "anchor":
        case "tag":
          if (!it || it.value)
            fc.items.push({ start: [this.sourceToken] });
          else if (it.sep)
            it.sep.push(this.sourceToken);
          else
            it.start.push(this.sourceToken);
          return;
        case "alias":
        case "scalar":
        case "single-quoted-scalar":
        case "double-quoted-scalar": {
          const fs = this.flowScalar(this.type);
          if (!it || it.value)
            fc.items.push({ start: [], key: fs, sep: [] });
          else if (it.sep)
            this.stack.push(fs);
          else
            Object.assign(it, { key: fs, sep: [] });
          return;
        }
        case "flow-map-end":
        case "flow-seq-end":
          fc.end.push(this.sourceToken);
          return;
      }
      const bv = this.startBlockValue(fc);
      if (bv)
        this.stack.push(bv);
      else {
        yield* this.pop();
        yield* this.step();
      }
    } else {
      const parent = this.peek(2);
      if (parent.type === "block-map" && (this.type === "map-value-ind" && parent.indent === fc.indent || this.type === "newline" && !parent.items[parent.items.length - 1].sep)) {
        yield* this.pop();
        yield* this.step();
      } else if (this.type === "map-value-ind" && parent.type !== "flow-collection") {
        const prev = getPrevProps(parent);
        const start = getFirstKeyStartProps(prev);
        fixFlowSeqItems(fc);
        const sep = fc.end.splice(1, fc.end.length);
        sep.push(this.sourceToken);
        const map2 = {
          type: "block-map",
          offset: fc.offset,
          indent: fc.indent,
          items: [{ start, key: fc, sep }]
        };
        this.onKeyLine = true;
        this.stack[this.stack.length - 1] = map2;
      } else {
        yield* this.lineEnd(fc);
      }
    }
  }
  flowScalar(type) {
    if (this.onNewLine) {
      let nl = this.source.indexOf("\n") + 1;
      while (nl !== 0) {
        this.onNewLine(this.offset + nl);
        nl = this.source.indexOf("\n", nl) + 1;
      }
    }
    return {
      type,
      offset: this.offset,
      indent: this.indent,
      source: this.source
    };
  }
  startBlockValue(parent) {
    switch (this.type) {
      case "alias":
      case "scalar":
      case "single-quoted-scalar":
      case "double-quoted-scalar":
        return this.flowScalar(this.type);
      case "block-scalar-header":
        return {
          type: "block-scalar",
          offset: this.offset,
          indent: this.indent,
          props: [this.sourceToken],
          source: ""
        };
      case "flow-map-start":
      case "flow-seq-start":
        return {
          type: "flow-collection",
          offset: this.offset,
          indent: this.indent,
          start: this.sourceToken,
          items: [],
          end: []
        };
      case "seq-item-ind":
        return {
          type: "block-seq",
          offset: this.offset,
          indent: this.indent,
          items: [{ start: [this.sourceToken] }]
        };
      case "explicit-key-ind": {
        this.onKeyLine = true;
        const prev = getPrevProps(parent);
        const start = getFirstKeyStartProps(prev);
        start.push(this.sourceToken);
        return {
          type: "block-map",
          offset: this.offset,
          indent: this.indent,
          items: [{ start, explicitKey: true }]
        };
      }
      case "map-value-ind": {
        this.onKeyLine = true;
        const prev = getPrevProps(parent);
        const start = getFirstKeyStartProps(prev);
        return {
          type: "block-map",
          offset: this.offset,
          indent: this.indent,
          items: [{ start, key: null, sep: [this.sourceToken] }]
        };
      }
    }
    return null;
  }
  atIndentedComment(start, indent) {
    if (this.type !== "comment")
      return false;
    if (this.indent <= indent)
      return false;
    return start.every((st) => st.type === "newline" || st.type === "space");
  }
  *documentEnd(docEnd) {
    if (this.type !== "doc-mode") {
      if (docEnd.end)
        docEnd.end.push(this.sourceToken);
      else
        docEnd.end = [this.sourceToken];
      if (this.type === "newline")
        yield* this.pop();
    }
  }
  *lineEnd(token) {
    switch (this.type) {
      case "comma":
      case "doc-start":
      case "doc-end":
      case "flow-seq-end":
      case "flow-map-end":
      case "map-value-ind":
        yield* this.pop();
        yield* this.step();
        break;
      case "newline":
        this.onKeyLine = false;
      // fallthrough
      case "space":
      case "comment":
      default:
        if (token.end)
          token.end.push(this.sourceToken);
        else
          token.end = [this.sourceToken];
        if (this.type === "newline")
          yield* this.pop();
    }
  }
};

// ../../../frontend/node_modules/yaml/browser/dist/public-api.js
function parseOptions(options) {
  const prettyErrors = options.prettyErrors !== false;
  const lineCounter = options.lineCounter || prettyErrors && new LineCounter() || null;
  return { lineCounter, prettyErrors };
}
function parseDocument(source, options = {}) {
  const { lineCounter, prettyErrors } = parseOptions(options);
  const parser = new Parser(lineCounter?.addNewLine);
  const composer = new Composer(options);
  let doc = null;
  for (const _doc of composer.compose(parser.parse(source), true, source.length)) {
    if (!doc)
      doc = _doc;
    else if (doc.options.logLevel !== "silent") {
      doc.errors.push(new YAMLParseError(_doc.range.slice(0, 2), "MULTIPLE_DOCS", "Source contains multiple documents; please use YAML.parseAllDocuments()"));
      break;
    }
  }
  if (prettyErrors && lineCounter) {
    doc.errors.forEach(prettifyError(source, lineCounter));
    doc.warnings.forEach(prettifyError(source, lineCounter));
  }
  return doc;
}

// src/composeForm.ts
var SERVICE_NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9_.-]*$/;
var ENV_KEY_RE = /^[^=\s]+$/;
var ONLY_TEXT = "Bitte im Text \xE4ndern.";
var TAGGED = `Mit einer Markierung wie !reset oder !override geschrieben. ${ONLY_TEXT}`;
var NOT_TEXT = "Diesen Namen liest YAML nicht als Text, sondern etwa als Zahl, Datum oder Wahrheitswert. Nimm einen anderen Namen.";
var NOT_SAFE = `Das l\xE4sst sich im Formular nicht sicher \xE4ndern, weil die Stelle in der Datei besonders geschrieben ist oder an anderer Stelle mitbenutzt wird. ${ONLY_TEXT}`;
var AMBIGUOUS_WORDS = /^(y|yes|n|no|on|off|true|false|null|~)$/i;
var AMBIGUOUS = [
  // Compose liest Zahlen mit `_`, Datumsangaben und `<<` anders als diese Bibliothek; alles, was so beginnt, kommt in Anführungszeichen.
  /^[0-9+\-.]/,
  /^<</,
  /^(y|yes|n|no|on|off|true|false|null|~)$/i,
  /^[-+]?[0-9][0-9_]*(:[0-5]?[0-9])+(\.[0-9_]*)?$/,
  /^[-+]?0[0-7_]+$/,
  /^[-+]?0b[01_]+$/
];
function parse2(text) {
  return parseDocument(text, { prettyErrors: false });
}
function lineStart(text, offset) {
  return text.lastIndexOf("\n", offset - 1) + 1;
}
function lineEnd(text, offset) {
  if (offset > 0 && text[offset - 1] === "\n") return offset;
  const next = text.indexOf("\n", offset);
  return next < 0 ? text.length : next + 1;
}
function blankBefore(text, start) {
  return start > 0 && text.slice(lineStart(text, start - 1), start).trim() === "";
}
function column(text, offset) {
  return offset - lineStart(text, offset);
}
function str(value) {
  return value == null ? "" : String(value);
}
function sameJs(a, b) {
  if (a === b) return true;
  if (typeof a === "number" && typeof b === "number") return Number.isNaN(a) && Number.isNaN(b);
  if (Array.isArray(a)) return Array.isArray(b) && a.length === b.length && a.every((v, i) => sameJs(v, b[i]));
  if (a && b && typeof a === "object" && typeof b === "object" && !Array.isArray(b)) {
    const ka = Object.keys(a);
    return ka.length === Object.keys(b).length && ka.every((k) => k in b && sameJs(a[k], b[k]));
  }
  return false;
}
function readsAsText(name) {
  if (readScalar(name) !== name || AMBIGUOUS_WORDS.test(name)) return false;
  return !/^[-+.]?[0-9]/.test(name) || /[A-Za-z]{2,}/.test(name);
}
function readScalar(source) {
  const doc = parse2(`k: ${source}
`);
  if (doc.errors.length) return void 0;
  try {
    return doc.toJS()?.k;
  } catch {
    return void 0;
  }
}
function scalarText(value, old) {
  const plainOk = !value.includes("\n") && value.trim() === value && value !== "" && !AMBIGUOUS.some((re) => re.test(value));
  if (plainOk && (!old || old.type === "PLAIN")) {
    const parsed = readScalar(value);
    if (parsed === value) return { text: value, parsed };
  }
  if (old?.type === "PLAIN" && typeof old.value === "number" && /^-?(0|[1-9][0-9]{0,15})$/.test(value)) return { text: value, parsed: Number(value) };
  if (old?.type === "QUOTE_SINGLE" && !value.includes("\n")) return { text: `'${value.replace(/'/g, "''")}'`, parsed: value };
  return { text: JSON.stringify(value), parsed: value };
}
function quoted(value, double) {
  return double ? { text: JSON.stringify(value), parsed: value } : scalarText(value);
}
function applySplices(text, splices) {
  let out = text;
  for (const s of [...splices].sort((a, b) => b.from - a.from)) out = out.slice(0, s.from) + s.insert + out.slice(s.to);
  return out;
}
function plainScalar(node) {
  return isScalar(node) && !node.anchor && !node.tag && (node.type === "PLAIN" || node.type === "QUOTE_DOUBLE" || node.type === "QUOTE_SINGLE");
}
function shown(node) {
  return node.type === "PLAIN" && typeof node.source === "string" ? node.source : str(node.value);
}
function blockMap(node) {
  return isMap(node) && !node.flow && !node.anchor && !node.tag;
}
function findPair2(map2, key) {
  return map2.items.find((p) => isScalar(p.key) && String(p.key.value) === key);
}
function scalarLock(node) {
  if (node == null) return null;
  if (isAlias(node)) return `Der Wert kommt von einer anderen Stelle der Datei (*${node.source}). ${ONLY_TEXT}`;
  if (isScalar(node) && (node.type === "BLOCK_LITERAL" || node.type === "BLOCK_FOLDED")) return `Mehrzeilig geschrieben. ${ONLY_TEXT}`;
  if (isScalar(node) && node.tag) return TAGGED;
  if (!plainScalar(node)) return `Besonders geschrieben. ${ONLY_TEXT}`;
  return null;
}
function listLock(node) {
  if (node != null && node.tag) return TAGGED;
  if (node == null || isScalar(node) && node.value == null && !node.anchor) return null;
  if (isAlias(node)) return `Die Liste kommt von einer anderen Stelle der Datei (*${node.source}). ${ONLY_TEXT}`;
  if (!isSeq(node) || node.anchor) return `Besonders geschrieben. ${ONLY_TEXT}`;
  return null;
}
function serviceLock(node) {
  if (isAlias(node)) return `Der Dienst kommt von einer anderen Stelle der Datei. ${ONLY_TEXT}`;
  if (!isMap(node)) return `Der Dienst ist leer oder besonders geschrieben. ${ONLY_TEXT}`;
  if (node.flow) return `Der Dienst ist in einer Zeile geschrieben. ${ONLY_TEXT}`;
  if (node.tag) return TAGGED;
  if (node.anchor) return `Der Dienst wird an anderer Stelle der Datei mitbenutzt (&${node.anchor}). ${ONLY_TEXT}`;
  if (findPair2(node, "<<")) return `Der Dienst \xFCbernimmt Einstellungen von einer anderen Stelle der Datei (<<). ${ONLY_TEXT}`;
  if (findPair2(node, "extends")) return `Der Dienst \xFCbernimmt Einstellungen eines anderen Dienstes (extends). ${ONLY_TEXT}`;
  return null;
}
function itemSummary(text, node) {
  const [from, to] = node.range ?? [0, 0];
  return text.slice(from, to).replace(/\s+/g, " ").trim();
}
function readList(text, node) {
  const locked = listLock(node);
  if (locked || !isSeq(node)) return { items: [], locked };
  const items = node.items.map((item) => plainScalar(item) ? { value: shown(item), editable: true } : { value: itemSummary(text, item), editable: false });
  return { items, locked: null };
}
function envEntry(raw) {
  const at = raw.indexOf("=");
  return at < 0 ? { key: raw, value: "" } : { key: raw.slice(0, at), value: raw.slice(at + 1) };
}
function readEnv(text, node) {
  if (node != null && node.tag) return { list: false, entries: [], locked: TAGGED };
  if (node == null || isScalar(node) && node.value == null && !node.anchor) return { list: false, entries: [], locked: null };
  if (isSeq(node) && !node.anchor) {
    const entries = node.items.map((item) => plainScalar(item) ? { ...envEntry(shown(item)), editable: true } : { key: itemSummary(text, item), value: "", editable: false });
    return { list: true, entries, locked: null };
  }
  if (isMap(node) && !node.anchor) {
    const entries = node.items.map((pair) => {
      const key = isScalar(pair.key) ? str(pair.key.value) : itemSummary(text, pair.key);
      const value = pair.value;
      const editable2 = isScalar(pair.key) && (value == null || plainScalar(value) && (value.value != null || value.source === ""));
      return { key, value: isScalar(value) ? shown(value) : value ? itemSummary(text, value) : "", editable: editable2 };
    });
    return { list: false, entries, locked: null };
  }
  return { list: false, entries: [], locked: `Besonders geschrieben. ${ONLY_TEXT}` };
}
function fileLock(doc, text) {
  if (doc.errors.length) {
    const first = doc.errors[0];
    if (first.code === "MULTIPLE_DOCS") return `Die Datei enth\xE4lt mehrere YAML-Dokumente. ${ONLY_TEXT}`;
    const line = text.slice(0, first.pos[0]).split("\n").length;
    return `Die Datei ist so kein g\xFCltiges YAML (Zeile ${line}). ${ONLY_TEXT}`;
  }
  try {
    doc.toJS();
  } catch {
    return `Die Datei verweist zu oft auf andere Stellen. ${ONLY_TEXT}`;
  }
  if (doc.contents != null && !blockMap(doc.contents)) return `Die Datei ist nicht wie eine Compose-Datei aufgebaut. ${ONLY_TEXT}`;
  const services = doc.contents ? findPair2(doc.contents, "services")?.value : null;
  if (services != null && !(isScalar(services) && services.value == null && services.source === "") && !blockMap(services)) {
    return `Der Abschnitt \u201Eservices\u201C ist besonders geschrieben. ${ONLY_TEXT}`;
  }
  return null;
}
function readForm(text) {
  const doc = parse2(text);
  const locked = fileLock(doc, text);
  if (locked) return { locked, services: [], notes: [] };
  const root = doc.contents;
  const notes = [];
  if (root && findPair2(root, "include")) notes.push("Weitere Dienste kommen aus eingebundenen Dateien (include); die zeigt das Formular nicht.");
  const services = root ? findPair2(root, "services")?.value : null;
  if (!blockMap(services)) return { locked: null, services: [], notes };
  const out = [];
  for (const pair of services.items) {
    const name = isScalar(pair.key) ? str(pair.key.value) : itemSummary(text, pair.key);
    const lock = isScalar(pair.key) ? serviceLock(pair.value) : `Besonders geschrieben. ${ONLY_TEXT}`;
    const map2 = !lock ? pair.value : null;
    const field = (key) => map2 ? findPair2(map2, key)?.value ?? null : null;
    const image = field("image");
    const restart = field("restart");
    out.push({
      name,
      locked: lock,
      image: isScalar(image) && image.value != null ? shown(image) : null,
      imageLocked: scalarLock(image),
      restart: isScalar(restart) && restart.value != null ? shown(restart) : null,
      restartLocked: scalarLock(restart),
      ports: map2 ? readList(text, field("ports")) : { items: [], locked: lock },
      volumes: map2 ? readList(text, field("volumes")) : { items: [], locked: lock },
      environment: map2 ? readEnv(text, field("environment")) : { list: false, entries: [], locked: lock }
    });
  }
  return { locked: null, services: out, notes };
}
function style(text, doc) {
  let unit = 2;
  const root = doc.contents;
  const services = root ? findPair2(root, "services") : void 0;
  if (services && blockMap(services.value) && services.value.items.length) {
    const first = services.value.items[0].key;
    const diff = column(text, first.range[0]) - column(text, services.key.range[0]);
    if (diff > 0) unit = diff;
  }
  let seq2 = null;
  const walk = (node) => {
    if (seq2 != null || !isMap(node) || node.flow) return;
    for (const pair of node.items) {
      if (seq2 != null) return;
      if (isSeq(pair.value) && !pair.value.flow && pair.value.range && pair.key.range) {
        seq2 = Math.max(0, column(text, pair.value.range[0]) - column(text, pair.key.range[0]));
      } else walk(pair.value);
    }
  };
  walk(root);
  return { unit, seq: seq2 ?? unit };
}
function pairEnd(text, pair) {
  const ends = [pair.key.range[2]];
  if (pair.value?.range) ends.push(pair.value.range[2]);
  return lineEnd(text, Math.max(...ends));
}
function removePair(text, pair) {
  return { from: lineStart(text, pair.key.range[0]), to: pairEnd(text, pair), insert: "" };
}
function appendToMap(text, map2, lines) {
  const pairs2 = map2.items;
  const last = pairs2[pairs2.length - 1];
  const at = pairEnd(text, last);
  const indent = " ".repeat(column(text, pairs2[0].key.range[0]));
  const before = at === text.length && !text.endsWith("\n") ? "\n" : "";
  return { from: at, to: at, insert: before + lines(indent) };
}
function setScalarSplice(node, insert) {
  const [from, to] = node.range;
  if (node.value == null && node.source === "") return { from, to, insert: ` ${insert}` };
  return { from, to, insert };
}
function seqLines(indent, seqIndent, items) {
  return items.map((item) => `${indent}${" ".repeat(seqIndent)}- ${item}
`).join("");
}
function verify(before, after, expect) {
  const doc = parse2(after);
  if (doc.errors.length) return { error: NOT_SAFE };
  let wanted;
  let got;
  try {
    wanted = parse2(before).toJS() ?? {};
    expect(wanted);
    got = doc.toJS() ?? {};
  } catch {
    return { error: NOT_SAFE };
  }
  return sameJs(got, wanted) ? { text: after } : { error: NOT_SAFE };
}
function serviceJs(js, name) {
  return js.services[name];
}
function itemsDouble(seq2, field) {
  const scalars = seq2.items.filter(plainScalar);
  if (!scalars.length) return field === "ports";
  return scalars.every((s) => s.type === "QUOTE_DOUBLE");
}
function applyOp(text, op) {
  const doc = parse2(text);
  if (fileLock(doc, text)) return { error: NOT_SAFE };
  const { unit, seq: seq2 } = style(text, doc);
  const root = doc.contents;
  if (op.op === "addService") {
    if (!SERVICE_NAME_RE.test(op.name)) return { error: "Der Name darf nur Buchstaben, Ziffern, \u201E-\u201C, \u201E_\u201C und \u201E.\u201C enthalten und muss mit einem Buchstaben oder einer Ziffer anfangen." };
    if (!readsAsText(op.name)) return { error: NOT_TEXT };
    const image = scalarText(op.image);
    const block = (indent) => `${indent}${op.name}:
${indent}${" ".repeat(unit)}image: ${image.text}
`;
    const servicesPair = root ? findPair2(root, "services") : void 0;
    let splice2;
    if (servicesPair && blockMap(servicesPair.value)) {
      if (findPair2(servicesPair.value, op.name)) return { error: `Einen Dienst \u201E${op.name}\u201C gibt es schon.` };
      const second = servicesPair.value.items[1]?.key.range?.[0];
      const spaced = second != null && blankBefore(text, lineStart(text, second));
      const s = appendToMap(text, servicesPair.value, block);
      splice2 = spaced ? { ...s, insert: s.insert.replace(/^(\n?)/, "$1\n") } : s;
    } else if (servicesPair) {
      const at = lineEnd(text, servicesPair.key.range[2]);
      const indent = " ".repeat(column(text, servicesPair.key.range[0]) + unit);
      splice2 = { from: at, to: at, insert: (at === text.length && !text.endsWith("\n") ? "\n" : "") + block(indent) };
    } else {
      const at = text.length;
      const lead = text === "" || text.endsWith("\n") ? "" : "\n";
      splice2 = { from: at, to: at, insert: `${lead}services:
${block(" ".repeat(unit))}` };
    }
    return verify(text, applySplices(text, [splice2]), (js) => {
      js.services = js.services ?? {};
      js.services[op.name] = { image: image.parsed };
    });
  }
  const services = root ? findPair2(root, "services")?.value : null;
  if (!blockMap(services)) return { error: NOT_SAFE };
  const servicePair = findPair2(services, op.service);
  if (!servicePair || serviceLock(servicePair.value)) return { error: NOT_SAFE };
  const map2 = servicePair.value;
  if (op.op === "set" || op.op === "unset") {
    const pair2 = findPair2(map2, op.field);
    if (op.op === "unset") {
      if (!pair2) return { text };
      if (scalarLock(pair2.value)) return { error: NOT_SAFE };
      return verify(text, applySplices(text, [removePair(text, pair2)]), (js) => {
        delete serviceJs(js, op.service)[op.field];
      });
    }
    if (pair2 && scalarLock(pair2.value)) return { error: NOT_SAFE };
    const old = isScalar(pair2?.value) ? pair2.value : void 0;
    const value = scalarText(op.value, old?.value != null ? old : void 0);
    const splice2 = pair2 && old ? setScalarSplice(old, value.text) : pair2 ? { from: lineStart(text, pair2.key.range[0]), to: pairEnd(text, pair2), insert: `${" ".repeat(column(text, pair2.key.range[0]))}${op.field}: ${value.text}
` } : appendToMap(text, map2, (indent) => `${indent}${op.field}: ${value.text}
`);
    return verify(text, applySplices(text, [splice2]), (js) => {
      serviceJs(js, op.service)[op.field] = value.parsed;
    });
  }
  if (op.op === "addItem" || op.op === "setItem" || op.op === "removeItem") {
    const pair2 = findPair2(map2, op.field);
    const node2 = pair2?.value;
    if (listLock(node2)) return { error: NOT_SAFE };
    const list = isSeq(node2) ? node2 : null;
    if (op.op === "addItem") {
      const value = quoted(op.value, list ? itemsDouble(list, op.field) : op.field === "ports");
      let splice3;
      if (list && list.items.length && list.flow) {
        const last = list.items[list.items.length - 1];
        splice3 = { from: last.range[1], to: last.range[1], insert: `, ${value.text}` };
      } else if (list && list.items.length) {
        const first = list.items[0];
        const last = list.items[list.items.length - 1];
        const at = lineEnd(text, last.range[2]);
        const dash = column(text, list.range[0]);
        if (column(text, first.range[0]) <= dash) return { error: NOT_SAFE };
        splice3 = { from: at, to: at, insert: (at === text.length && !text.endsWith("\n") ? "\n" : "") + `${" ".repeat(dash)}- ${value.text}
` };
      } else if (pair2) {
        const indent = " ".repeat(column(text, pair2.key.range[0]));
        splice3 = { from: lineStart(text, pair2.key.range[0]), to: pairEnd(text, pair2), insert: `${indent}${op.field}:
${seqLines(indent, seq2, [value.text])}` };
      } else {
        splice3 = appendToMap(text, map2, (indent) => `${indent}${op.field}:
${seqLines(indent, seq2, [value.text])}`);
      }
      return verify(text, applySplices(text, [splice3]), (js) => {
        const svc = serviceJs(js, op.service);
        svc[op.field] = [...svc[op.field] ?? [], value.parsed];
      });
    }
    const item = list?.items[op.index];
    if (!list || !item) return { error: NOT_SAFE };
    if (op.op === "setItem") {
      if (!plainScalar(item)) return { error: NOT_SAFE };
      const value = scalarText(op.value, item);
      return verify(text, applySplices(text, [setScalarSplice(item, value.text)]), (js) => {
        serviceJs(js, op.service)[op.field][op.index] = value.parsed;
      });
    }
    let splice2;
    if (list.items.length === 1) splice2 = removePair(text, pair2);
    else if (list.flow) {
      const next = list.items[op.index + 1];
      const prev = list.items[op.index - 1];
      splice2 = next ? { from: item.range[0], to: next.range[0], insert: "" } : { from: prev.range[1], to: item.range[1], insert: "" };
    } else {
      splice2 = { from: lineStart(text, item.range[0]), to: lineEnd(text, item.range[2]), insert: "" };
    }
    return verify(text, applySplices(text, [splice2]), (js) => {
      const svc = serviceJs(js, op.service);
      const rest = svc[op.field].filter((_, i) => i !== op.index);
      if (rest.length) svc[op.field] = rest;
      else delete svc[op.field];
    });
  }
  if (!ENV_KEY_RE.test(op.key)) return { error: "Der Name einer Variablen darf weder \u201E=\u201C noch Leerzeichen enthalten." };
  const pair = findPair2(map2, "environment");
  const node = pair?.value;
  if (isSeq(node) && !node.anchor && !node.tag) {
    const index = node.items.findIndex((item) => plainScalar(item) && envEntry(str(item.value)).key === op.key);
    if (op.op === "removeEnv") return index < 0 ? { text } : applyOp(text, { op: "removeItem", service: op.service, field: "environment", index });
    const raw = `${op.key}=${op.value}`;
    if (index >= 0) return applyOp(text, { op: "setItem", service: op.service, field: "environment", index, value: raw });
    return applyOp(text, { op: "addItem", service: op.service, field: "environment", value: raw });
  }
  if (node != null && !(isScalar(node) && node.value == null && node.source === "") && (!isMap(node) || node.anchor || node.tag)) return { error: NOT_SAFE };
  const env = isMap(node) ? node : null;
  const entry = env ? findPair2(env, op.key) : void 0;
  if (op.op === "removeEnv") {
    if (!entry || !env) return { text };
    if (env.flow || entry.value != null && !plainScalar(entry.value)) return { error: NOT_SAFE };
    const splice2 = env.items.length === 1 ? removePair(text, pair) : removePair(text, entry);
    return verify(text, applySplices(text, [splice2]), (js) => {
      const svc = serviceJs(js, op.service);
      const rest = { ...svc.environment };
      delete rest[op.key];
      if (Object.keys(rest).length) svc.environment = rest;
      else delete svc.environment;
    });
  }
  const key = scalarText(op.key);
  if (key.parsed !== op.key || !readsAsText(op.key)) return { error: NOT_TEXT };
  let splice;
  let parsed;
  if (entry) {
    const old = entry.value;
    if (old != null && !plainScalar(old)) return { error: NOT_SAFE };
    const value = scalarText(op.value, old && old.value != null ? old : void 0);
    parsed = value.parsed;
    splice = old ? setScalarSplice(old, value.text) : { from: entry.key.range[1], to: entry.key.range[1], insert: `: ${value.text}` };
    if (!old && text[entry.key.range[1]] === ":") splice = { from: entry.key.range[1] + 1, to: entry.key.range[1] + 1, insert: ` ${value.text}` };
  } else {
    const value = scalarText(op.value);
    parsed = value.parsed;
    if (env && env.flow) {
      if (!env.items.length) return { error: NOT_SAFE };
      const last = env.items[env.items.length - 1];
      const end = last.value?.range?.[1] ?? last.key.range[1];
      splice = { from: end, to: end, insert: `, ${key.text}: ${value.text}` };
    } else if (env && env.items.length) {
      splice = appendToMap(text, env, (indent) => `${indent}${key.text}: ${value.text}
`);
    } else if (pair) {
      const indent = " ".repeat(column(text, pair.key.range[0]));
      splice = { from: lineStart(text, pair.key.range[0]), to: pairEnd(text, pair), insert: `${indent}environment:
${indent}${" ".repeat(unit)}${key.text}: ${value.text}
` };
    } else {
      splice = appendToMap(text, map2, (indent) => `${indent}environment:
${indent}${" ".repeat(unit)}${key.text}: ${value.text}
`);
    }
  }
  return verify(text, applySplices(text, [splice]), (js) => {
    const svc = serviceJs(js, op.service);
    svc.environment = { ...svc.environment ?? {}, [op.key]: parsed };
  });
}

// src/StackForm.tsx
import { Fragment as Fragment7, jsx as jsx10, jsxs as jsxs9 } from "react/jsx-runtime";
var RESTART = [
  ["", "Kein Eintrag (startet nicht von selbst neu)"],
  ["unless-stopped", "Neu starten, au\xDFer von Hand gestoppt"],
  ["always", "Immer neu starten"],
  ["on-failure", "Nur nach einem Fehler neu starten"],
  ["no", "Nie neu starten"]
];
var LISTS = [
  { field: "ports", label: "Ports", hint: "Links der Port auf dem Server, rechts der im Container.", placeholder: "8080:80" },
  { field: "volumes", label: "Ordner und Volumes", hint: "Links ein Ordner auf dem Server oder ein Volume, rechts der Ordner im Container.", placeholder: "./daten:/data" }
];
var small = `${inputClass} !px-2 !py-1 !text-xs`;
function EditField({ label, value, placeholder, disabled, onEdit, onStart, onEnd }) {
  const [draft, setDraft] = useState7(null);
  return /* @__PURE__ */ jsx10(
    "input",
    {
      "aria-label": label,
      className: small,
      value: draft ?? value,
      placeholder,
      disabled,
      spellCheck: false,
      onFocus: () => {
        onStart();
        setDraft(value);
      },
      onBlur: () => {
        setDraft(null);
        onEnd();
      },
      onChange: (event) => {
        setDraft(event.target.value);
        onEdit(event.target.value);
      }
    }
  );
}
function Locked({ reason }) {
  return /* @__PURE__ */ jsx10("p", { className: "text-amber-300/90", children: reason });
}
function StackForm({ text, onChange, disabled }) {
  const form = useMemo4(() => readForm(text), [text]);
  const [problem, setProblem] = useState7(null);
  const [adding, setAdding] = useState7({});
  const [service, setService] = useState7({ name: "", image: "" });
  const base = useRef3(null);
  function run(op, fromBase = false) {
    const out = applyOp(fromBase && base.current != null ? base.current : text, op);
    if ("error" in out) {
      setProblem(out.error);
      return false;
    }
    setProblem(null);
    if (out.text !== text) onChange(out.text);
    return true;
  }
  const typing = { onStart: () => {
    base.current = text;
  }, onEnd: () => {
    base.current = null;
  }, disabled };
  if (form.locked) return /* @__PURE__ */ jsx10(Locked, { reason: form.locked });
  function addTo(key, op) {
    if (run(op)) setAdding((all) => ({ ...all, [key]: "" }));
  }
  function list(svc, field, label, hint, placeholder) {
    const data = svc[field];
    const key = `${svc.name}/${field}`;
    const draft = adding[key] ?? "";
    return /* @__PURE__ */ jsxs9("div", { className: "flex flex-col gap-1", children: [
      /* @__PURE__ */ jsx10("span", { className: "opacity-70", children: label }),
      data.locked ? /* @__PURE__ */ jsx10(Locked, { reason: data.locked }) : /* @__PURE__ */ jsxs9(Fragment7, { children: [
        data.items.map((item, index) => /* @__PURE__ */ jsxs9("div", { className: "flex items-center gap-1.5", children: [
          item.editable ? /* @__PURE__ */ jsx10(
            EditField,
            {
              label: `${label} ${index + 1} von ${svc.name}`,
              value: item.value,
              ...typing,
              onEdit: (value) => run({ op: "setItem", service: svc.name, field, index, value }, true)
            }
          ) : /* @__PURE__ */ jsx10("span", { className: "flex-1 break-all font-mono opacity-80", title: "Ausf\xFChrlich geschrieben, bitte im Text \xE4ndern.", children: item.value }),
          /* @__PURE__ */ jsx10(
            Button,
            {
              small: true,
              disabled,
              ariaLabel: `${label} ${index + 1} von ${svc.name} entfernen`,
              onClick: () => run({ op: "removeItem", service: svc.name, field, index }),
              children: "Entfernen"
            }
          )
        ] }, index)),
        /* @__PURE__ */ jsxs9("div", { className: "flex items-center gap-1.5", children: [
          /* @__PURE__ */ jsx10(
            "input",
            {
              "aria-label": `${label} f\xFCr ${svc.name} hinzuf\xFCgen`,
              className: small,
              value: draft,
              placeholder,
              disabled,
              spellCheck: false,
              onChange: (event) => setAdding((all) => ({ ...all, [key]: event.target.value }))
            }
          ),
          /* @__PURE__ */ jsx10(Button, { small: true, disabled: disabled || !draft.trim(), onClick: () => addTo(key, { op: "addItem", service: svc.name, field, value: draft.trim() }), children: "Hinzuf\xFCgen" })
        ] }),
        /* @__PURE__ */ jsx10("span", { className: "opacity-60", children: hint })
      ] })
    ] }, field);
  }
  function environment(svc) {
    const env = svc.environment;
    const keyDraft = adding[`${svc.name}/env-key`] ?? "";
    const valueDraft = adding[`${svc.name}/env-value`] ?? "";
    const keyOk = ENV_KEY_RE.test(keyDraft);
    return /* @__PURE__ */ jsxs9("div", { className: "flex flex-col gap-1", children: [
      /* @__PURE__ */ jsx10("span", { className: "opacity-70", children: "Umgebungsvariablen" }),
      env.locked ? /* @__PURE__ */ jsx10(Locked, { reason: env.locked }) : /* @__PURE__ */ jsxs9(Fragment7, { children: [
        env.entries.map((entry, index) => /* @__PURE__ */ jsxs9("div", { className: "flex items-center gap-1.5", children: [
          /* @__PURE__ */ jsx10("span", { className: "w-2/5 shrink-0 break-all font-mono", children: entry.key }),
          entry.editable ? /* @__PURE__ */ jsx10(
            EditField,
            {
              label: `Wert von ${entry.key} in ${svc.name}`,
              value: entry.value,
              ...typing,
              onEdit: (value) => run({ op: "setEnv", service: svc.name, key: entry.key, value }, true)
            }
          ) : /* @__PURE__ */ jsx10("span", { className: "flex-1 break-all font-mono opacity-80", title: "Besonders geschrieben, bitte im Text \xE4ndern.", children: entry.value }),
          /* @__PURE__ */ jsx10(
            Button,
            {
              small: true,
              disabled: disabled || !entry.editable,
              ariaLabel: `${entry.key} aus ${svc.name} entfernen`,
              onClick: () => run({ op: "removeEnv", service: svc.name, key: entry.key }),
              children: "Entfernen"
            }
          )
        ] }, `${entry.key}-${index}`)),
        /* @__PURE__ */ jsxs9("div", { className: "flex items-center gap-1.5", children: [
          /* @__PURE__ */ jsx10(
            "input",
            {
              "aria-label": `Name der neuen Variable f\xFCr ${svc.name}`,
              className: `${small} !w-2/5`,
              value: keyDraft,
              placeholder: "NAME",
              disabled,
              spellCheck: false,
              onChange: (event) => setAdding((all) => ({ ...all, [`${svc.name}/env-key`]: event.target.value }))
            }
          ),
          /* @__PURE__ */ jsx10(
            "input",
            {
              "aria-label": `Wert der neuen Variable f\xFCr ${svc.name}`,
              className: small,
              value: valueDraft,
              placeholder: "Wert",
              disabled,
              spellCheck: false,
              onChange: (event) => setAdding((all) => ({ ...all, [`${svc.name}/env-value`]: event.target.value }))
            }
          ),
          /* @__PURE__ */ jsx10(
            Button,
            {
              small: true,
              disabled: disabled || !keyOk || env.entries.some((e) => e.key === keyDraft),
              onClick: () => {
                if (run({ op: "setEnv", service: svc.name, key: keyDraft, value: valueDraft })) setAdding((all) => ({ ...all, [`${svc.name}/env-key`]: "", [`${svc.name}/env-value`]: "" }));
              },
              children: "Hinzuf\xFCgen"
            }
          )
        ] }),
        env.list && /* @__PURE__ */ jsx10("span", { className: "opacity-60", children: "Steht in der Datei als Liste \u201ENAME=Wert\u201C und bleibt so." })
      ] })
    ] });
  }
  const nameTaken = form.services.some((s) => s.name === service.name);
  return /* @__PURE__ */ jsxs9("div", { className: "flex flex-col gap-3", "data-testid": "stack-form", children: [
    /* @__PURE__ */ jsx10("p", { className: "opacity-70", children: "Das Formular \xE4ndert nur die Stellen, die du hier bearbeitest. Kommentare und der Rest der Datei bleiben, wie sie sind; alles Weitere geht im Text." }),
    form.notes.map((note) => /* @__PURE__ */ jsx10("p", { className: "opacity-70", children: note }, note)),
    problem && /* @__PURE__ */ jsx10("p", { className: "text-red-400", role: "alert", children: problem }),
    form.services.length === 0 && /* @__PURE__ */ jsx10("p", { className: "opacity-70", children: "Noch kein Dienst in der Datei." }),
    form.services.map((svc) => /* @__PURE__ */ jsxs9("section", { className: "flex flex-col gap-2 rounded border border-white/10 p-2", "aria-label": `Dienst ${svc.name}`, children: [
      /* @__PURE__ */ jsxs9("h4", { className: "font-semibold", children: [
        "Dienst \u201E",
        svc.name,
        "\u201C"
      ] }),
      svc.locked ? /* @__PURE__ */ jsx10(Locked, { reason: svc.locked }) : /* @__PURE__ */ jsxs9(Fragment7, { children: [
        /* @__PURE__ */ jsxs9("label", { className: "flex flex-col gap-1", children: [
          /* @__PURE__ */ jsx10("span", { className: "opacity-70", children: "Image" }),
          svc.imageLocked ? /* @__PURE__ */ jsx10(Locked, { reason: svc.imageLocked }) : /* @__PURE__ */ jsx10(
            EditField,
            {
              label: `Image von ${svc.name}`,
              value: svc.image ?? "",
              placeholder: "nginx:1.27",
              ...typing,
              onEdit: (value) => run({ op: "set", service: svc.name, field: "image", value }, true)
            }
          )
        ] }),
        /* @__PURE__ */ jsxs9("label", { className: "flex flex-col gap-1", children: [
          /* @__PURE__ */ jsx10("span", { className: "opacity-70", children: "Neustart" }),
          svc.restartLocked ? /* @__PURE__ */ jsx10(Locked, { reason: svc.restartLocked }) : /* @__PURE__ */ jsxs9(
            "select",
            {
              "aria-label": `Neustart von ${svc.name}`,
              className: small,
              value: svc.restart ?? "",
              disabled,
              onChange: (event) => run(event.target.value ? { op: "set", service: svc.name, field: "restart", value: event.target.value } : { op: "unset", service: svc.name, field: "restart" }),
              children: [
                svc.restart && !RESTART.some(([v]) => v === svc.restart) && /* @__PURE__ */ jsx10("option", { value: svc.restart, children: svc.restart }),
                RESTART.map(([value, label]) => /* @__PURE__ */ jsx10("option", { value, children: label }, value))
              ]
            }
          )
        ] }),
        LISTS.map(({ field, label, hint, placeholder }) => list(svc, field, label, hint, placeholder)),
        environment(svc)
      ] })
    ] }, svc.name)),
    /* @__PURE__ */ jsxs9("div", { className: "flex flex-wrap items-center gap-1.5", children: [
      /* @__PURE__ */ jsx10(
        "input",
        {
          "aria-label": "Name des neuen Dienstes",
          className: `${small} !w-40`,
          value: service.name,
          placeholder: "Name, z. B. app",
          disabled,
          spellCheck: false,
          onChange: (event) => setService((s) => ({ ...s, name: event.target.value }))
        }
      ),
      /* @__PURE__ */ jsx10(
        "input",
        {
          "aria-label": "Image des neuen Dienstes",
          className: `${small} !w-48`,
          value: service.image,
          placeholder: "Image, z. B. nginx:1.27",
          disabled,
          spellCheck: false,
          onChange: (event) => setService((s) => ({ ...s, image: event.target.value }))
        }
      ),
      /* @__PURE__ */ jsx10(
        Button,
        {
          small: true,
          disabled: disabled || !SERVICE_NAME_RE.test(service.name) || nameTaken || !service.image.trim(),
          onClick: () => {
            if (run({ op: "addService", name: service.name, image: service.image.trim() })) setService({ name: "", image: "" });
          },
          children: "Dienst hinzuf\xFCgen"
        }
      ),
      nameTaken && /* @__PURE__ */ jsx10("span", { className: "text-amber-300", children: "Den Namen gibt es schon." })
    ] })
  ] });
}

// src/StackEditor.tsx
import { Fragment as Fragment8, jsx as jsx11, jsxs as jsxs10 } from "react/jsx-runtime";
var JSON_HEADERS = { "Content-Type": "application/json" };
var RISKY_NOTE = "Kommt so eine Einstellung neu dazu, braucht das Speichern eine Freigabe mit hohem Risiko.";
var WAITING = "Zum Speichern vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe \u201EAktionen\u201C.";
var DONE_STATES = /* @__PURE__ */ new Set(["succeeded", "failed", "denied", "expired", "dismissed"]);
function baseName(path) {
  return path.slice(path.lastIndexOf("/") + 1);
}
function editable(file) {
  return !!file && file.content != null && !!file.sha256 && !file.edit_error;
}
function forServer(text, original) {
  return original.includes("\r\n") ? text.replace(/\r?\n/g, "\r\n") : text;
}
function savedAt(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" });
}
async function withResult(action, signal) {
  const id = action.id ?? action.action_id;
  if (action.result || !id || !DONE_STATES.has(action.status ?? "")) return action;
  return { ...action, ...await fetchJson(`/actions/${id}`, { signal }) };
}
function CheckResult({ checked, note }) {
  if (!checked) return null;
  if (checked.error) return /* @__PURE__ */ jsxs10("p", { className: "text-red-400", role: "alert", children: [
    "Compose lehnt die Datei ab: ",
    checked.error
  ] });
  if (checked.unverified) return /* @__PURE__ */ jsxs10("p", { className: "text-amber-300", role: "alert", children: [
    "Nicht gepr\xFCft: ",
    checked.unverified
  ] });
  return /* @__PURE__ */ jsxs10(Fragment8, { children: [
    /* @__PURE__ */ jsxs10("p", { className: "text-emerald-300", role: "status", children: [
      "Compose kann die Datei lesen",
      checked.services.length ? ` (Dienste: ${checked.services.join(", ")})` : "",
      "."
    ] }),
    checked.warnings.length > 0 && /* @__PURE__ */ jsxs10("div", { className: "text-amber-300", "data-testid": "stack-editor-warnings", children: [
      /* @__PURE__ */ jsx11("p", { children: "Riskante Einstellungen:" }),
      /* @__PURE__ */ jsx11("ul", { className: "list-disc pl-5", children: checked.warnings.map((w) => /* @__PURE__ */ jsx11("li", { className: "break-words", children: w }, w)) }),
      /* @__PURE__ */ jsx11("p", { children: note })
    ] })
  ] });
}
function StackEditor({ hostId, project, onClose, onDirty }) {
  const base = `/ext/docker/hosts/${encodeURIComponent(hostId)}/stacks/${encodeURIComponent(project)}`;
  const [files, setFiles] = useState8(null);
  const [error, setError] = useState8(null);
  const [reload, setReload] = useState8(0);
  const [index, setIndex] = useState8(0);
  const [text, setText] = useState8("");
  const [checked, setChecked] = useState8(null);
  const [busy, setBusy] = useState8(false);
  const [problem, setProblem] = useState8(null);
  const [note, setNote] = useState8(null);
  const [history, setHistory] = useState8(null);
  const [historyError, setHistoryError] = useState8(null);
  const [asForm, setAsForm] = useState8(false);
  const keep = useRef4(null);
  const checkRun = useRef4(0);
  const life = useRef4(null);
  useEffect7(() => {
    const controller = new AbortController();
    life.current = controller;
    return () => controller.abort();
  }, []);
  useEffect7(() => {
    const controller = new AbortController();
    setFiles(null);
    setError(null);
    fetchJson(`${base}/file`, { signal: controller.signal }).then((body) => {
      if (controller.signal.aborted) return;
      const kept = keep.current;
      keep.current = null;
      const wanted = kept ? body.files.findIndex((f) => f.path === kept.path && editable(f)) : -1;
      const first = wanted >= 0 ? wanted : Math.max(0, body.files.findIndex(editable));
      setFiles(body.files);
      setIndex(first);
      setText(wanted >= 0 && kept ? kept.text : (body.files[first]?.content ?? "").replace(/\r\n/g, "\n"));
      setChecked(null);
    }).catch((err) => {
      if (!controller.signal.aborted) setError(errorMessage(err));
    });
    return () => controller.abort();
  }, [base, reload]);
  const file = files?.[index];
  const dirty = editable(file) && forServer(text, file.content) !== file.content;
  useEffect7(() => {
    onDirty?.(dirty);
  }, [dirty, onDirty]);
  if (error) return /* @__PURE__ */ jsx11(LoadError, { what: "Die Compose-Datei", error, onRetry: () => setReload((n) => n + 1), compact: true });
  if (!files) return /* @__PURE__ */ jsx11("p", { className: "text-xs opacity-60", children: "Lade die Datei \u2026" });
  if (!editable(file)) {
    return /* @__PURE__ */ jsxs10("div", { className: "flex flex-col gap-2 text-xs", children: [
      /* @__PURE__ */ jsxs10("p", { className: "text-amber-300", children: [
        "Diese Datei l\xE4sst sich hier nicht bearbeiten: ",
        file?.edit_error ?? file?.error ?? "Docker nennt keine lesbare Datei."
      ] }),
      /* @__PURE__ */ jsx11("div", { children: /* @__PURE__ */ jsx11(Button, { small: true, onClick: () => onClose(null), children: "Schlie\xDFen" }) })
    ] });
  }
  const current = file;
  const name = baseName(current.path);
  const content = forServer(text, current.content);
  async function leave(next) {
    if (dirty && !await deck().confirmDialog("Deine \xC4nderung ist noch nicht gespeichert. Verwerfen?", { confirmLabel: "Verwerfen" })) return;
    next();
  }
  function edit(value) {
    checkRun.current += 1;
    setText(value);
    setChecked(null);
    setNote(null);
  }
  async function check() {
    const run = ++checkRun.current;
    setBusy(true);
    setProblem(null);
    setNote(null);
    try {
      const result = await fetchJson(`${base}/check`, {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({ path: current.path, content })
      });
      if (run !== checkRun.current) return null;
      setChecked(result);
      return result;
    } catch (err) {
      if (run === checkRun.current) setProblem(errorMessage(err));
      return null;
    } finally {
      setBusy(false);
    }
  }
  async function save(apply) {
    const result = await check();
    if (!result?.ok) return;
    const risky = result.warnings.length ? ` Riskant: ${result.warnings.join(" ")} ${RISKY_NOTE}` : "";
    const then = apply ? " und den Stack danach anwenden" : "";
    const pause = apply ? " Ge\xE4nderte Dienste werden dabei neu erstellt und sind kurz nicht erreichbar." : "";
    const question = `\u201E${name}\u201C von Stack \u201E${project}\u201C speichern${then}? Die alte Fassung bleibt im Verlauf.${pause}${risky}`;
    if (!await deck().confirmDialog(question, { confirmLabel: apply ? "Speichern und anwenden" : "Speichern" })) return;
    setBusy(true);
    const signal = (life.current ?? new AbortController()).signal;
    let submitted = false;
    try {
      const proposal = await fetchJson(`${base}/save`, {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({ path: current.path, content, base_sha256: current.sha256, apply })
      });
      submitted = true;
      const run = await settleAction(proposal, { signal });
      const action = await withResult(run.action, signal);
      if (signal.aborted) return;
      const outcome = describeActionOutcome(action);
      if (action.status === "proposed") onClose(WAITING);
      else if (action.result?.detail?.saved === true) {
        onClose(outcome.tone === "success" ? action.result.output ?? "Gespeichert." : `${action.result.output ?? ""}
${outcome.text}`.trim());
      } else if (outcome.tone === "pending") onClose(run.text);
      else setProblem(outcome.text);
    } catch (err) {
      if (!signal.aborted) {
        setProblem(submitted ? `Das Ergebnis lie\xDF sich nicht abrufen (${errorMessage(err)}). Ob gespeichert wurde, steht unter \u201EAktionen\u201C.` : errorMessage(err));
      }
    } finally {
      if (!signal.aborted) setBusy(false);
    }
  }
  async function toggleHistory() {
    if (history) {
      setHistory(null);
      return;
    }
    setHistoryError(null);
    try {
      setHistory((await fetchJson(`${base}/history`)).entries);
    } catch (err) {
      setHistoryError(errorMessage(err));
    }
  }
  async function loadOld(entry) {
    const target = files.findIndex((f) => f.path === entry.path && editable(f));
    if (target < 0) {
      setProblem(`Die alte Fassung geh\xF6rt zu ${entry.path}, und diese Datei l\xE4sst sich hier gerade nicht bearbeiten.`);
      return;
    }
    await leave(async () => {
      try {
        const old = await fetchJson(`${base}/history/${encodeURIComponent(entry.id)}`);
        setIndex(target);
        edit(old.content.replace(/\r\n/g, "\n"));
        setProblem(null);
        setNote(`Die Fassung vom ${savedAt(entry.saved_at)} steht jetzt im Editor. Gespeichert ist sie erst nach \u201ESpeichern\u201C.`);
      } catch (err) {
        setProblem(errorMessage(err));
      }
    });
  }
  function reloadKeeping() {
    keep.current = { path: current.path, text };
    setProblem(null);
    setNote("Die Datei auf dem Server hat sich ge\xE4ndert. Dein Speichern ersetzt diese neue Fassung; sie steht danach im Verlauf.");
    setReload((n) => n + 1);
  }
  const switchable = files.map((f, i) => ({ f, i })).filter(({ f }) => editable(f));
  return /* @__PURE__ */ jsxs10("div", { className: "flex flex-col gap-2 text-xs", "data-testid": `stack-editor-${project}`, children: [
    /* @__PURE__ */ jsx11("p", { className: "opacity-70", children: "Auf den Server kommt die \xC4nderung erst mit \u201ESpeichern\u201C und der Freigabe. Vorher pr\xFCft Compose die Datei; die alte Fassung bleibt im Verlauf." }),
    switchable.length > 1 && /* @__PURE__ */ jsx11("div", { className: "flex flex-wrap gap-1.5", children: switchable.map(({ f, i }) => /* @__PURE__ */ jsx11(
      Button,
      {
        small: true,
        pressed: i === index,
        onClick: () => void leave(() => {
          setIndex(i);
          edit((f.content ?? "").replace(/\r\n/g, "\n"));
        }),
        children: baseName(f.path)
      },
      f.path
    )) }),
    /* @__PURE__ */ jsxs10("div", { className: "flex flex-wrap items-center gap-1.5", children: [
      /* @__PURE__ */ jsx11("span", { className: "flex-1 break-all font-mono opacity-80", children: current.path }),
      /* @__PURE__ */ jsx11(Button, { small: true, pressed: !asForm, onClick: () => setAsForm(false), children: "Text" }),
      /* @__PURE__ */ jsx11(Button, { small: true, pressed: asForm, onClick: () => setAsForm(true), children: "Formular" })
    ] }),
    asForm ? /* @__PURE__ */ jsx11(StackForm, { text, onChange: edit, disabled: busy }) : /* @__PURE__ */ jsx11(
      "textarea",
      {
        "aria-label": `Inhalt von ${name}`,
        value: text,
        spellCheck: false,
        wrap: "off",
        rows: 22,
        className: "w-full resize-y rounded bg-black/60 p-2 font-mono text-[11px] leading-snug outline-none focus:ring-1 focus:ring-[var(--color-accent)]",
        onChange: (event) => edit(event.target.value)
      }
    ),
    /* @__PURE__ */ jsx11(DiffView, { before: current.content.replace(/\r\n/g, "\n"), after: text, open: asForm }, `${current.path}-${asForm}`),
    /* @__PURE__ */ jsx11(CheckResult, { checked, note: `${RISKY_NOTE} Was schon in der Datei auf dem Server steht, z\xE4hlt dabei nicht noch einmal.` }),
    note && /* @__PURE__ */ jsx11("p", { className: "opacity-80", role: "status", children: note }),
    problem && /* @__PURE__ */ jsxs10("div", { className: "flex flex-wrap items-center gap-2", children: [
      /* @__PURE__ */ jsx11("p", { className: "break-words text-red-400", role: "alert", children: problem }),
      /* @__PURE__ */ jsx11(Button, { small: true, onClick: reloadKeeping, children: "Stand vom Server holen, meine Fassung behalten" }),
      /* @__PURE__ */ jsx11(Button, { small: true, onClick: () => void leave(() => setReload((n) => n + 1)), children: "Datei neu laden" })
    ] }),
    /* @__PURE__ */ jsxs10("div", { className: "flex flex-wrap gap-1.5", children: [
      /* @__PURE__ */ jsx11(Button, { small: true, disabled: busy, onClick: () => void check(), children: "Pr\xFCfen" }),
      /* @__PURE__ */ jsx11(Button, { small: true, variant: "primary", disabled: busy || !dirty, onClick: () => void save(false), children: "Speichern" }),
      /* @__PURE__ */ jsx11(Button, { small: true, disabled: busy || !dirty, onClick: () => void save(true), children: "Speichern und anwenden" }),
      /* @__PURE__ */ jsx11(Button, { small: true, pressed: history !== null, onClick: () => void toggleHistory(), children: "Verlauf" }),
      /* @__PURE__ */ jsx11(Button, { small: true, onClick: () => void leave(() => onClose(null)), children: "Schlie\xDFen" })
    ] }),
    historyError && /* @__PURE__ */ jsx11(LoadError, { what: "Der Verlauf", error: historyError, onRetry: () => {
      setHistory(null);
      void toggleHistory();
    }, compact: true }),
    history && /* @__PURE__ */ jsx11("div", { "data-testid": "stack-editor-history", children: history.length === 0 ? /* @__PURE__ */ jsx11("p", { className: "opacity-70", children: "Noch keine alte Fassung: Der Verlauf f\xFCllt sich mit jedem Speichern." }) : /* @__PURE__ */ jsx11("ul", { className: "divide-y divide-white/5", children: history.map((entry) => /* @__PURE__ */ jsxs10("li", { className: "flex flex-wrap items-center gap-2 py-1", children: [
      /* @__PURE__ */ jsxs10("span", { className: "flex-1 break-all", children: [
        savedAt(entry.saved_at),
        " \xB7 ",
        baseName(entry.path),
        " \xB7 ",
        entry.size,
        " Bytes"
      ] }),
      /* @__PURE__ */ jsx11(Button, { small: true, onClick: () => void loadOld(entry), children: "In den Editor laden" })
    ] }, entry.id)) }) })
  ] });
}

// src/Registries.tsx
import { jsx as jsx12, jsxs as jsxs11 } from "react/jsx-runtime";
var WAITING2 = {
  login: "Zum Anmelden vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe \u201EAktionen\u201C.",
  logout: "Zum Abmelden vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe \u201EAktionen\u201C."
};
function status(r) {
  if (r.logged_in === null) return /* @__PURE__ */ jsx12(Badge, { children: "unbekannt" });
  return r.logged_in ? /* @__PURE__ */ jsx12(Badge, { tone: "good", children: "angemeldet" }) : /* @__PURE__ */ jsx12(Badge, { children: "nicht angemeldet" });
}
function RegistryList({ hostId }) {
  const [state, setState] = useState9(null);
  const [error, setError] = useState9(null);
  const [message, setMessage] = useState9(null);
  const [busy, setBusy] = useState9(null);
  const [reload, setReload] = useState9(0);
  const base = `/ext/docker/hosts/${encodeURIComponent(hostId)}/registries`;
  useEffect8(() => {
    const controller = new AbortController();
    setError(null);
    fetchJson(base, { signal: controller.signal }).then((body) => {
      if (!controller.signal.aborted) setState(body);
    }).catch((err) => {
      if (!controller.signal.aborted) setError(errorMessage(err));
    });
    return () => controller.abort();
  }, [base, reload]);
  async function run(r, verb) {
    const question = verb === "login" ? `Docker auf diesem Server bei ${r.address} als ${r.username} anmelden? Das Passwort aus dem Tresor geht dabei an Docker auf dem Server, der es wie bei docker login von Hand dort speichert.` : `Docker auf diesem Server bei ${r.address} abmelden? Images aus dieser Registry lassen sich danach nicht mehr holen.`;
    if (!await deck().confirmDialog(question, { confirmLabel: verb === "login" ? "Anmelden" : "Abmelden" })) return;
    setBusy(r.name);
    setMessage(null);
    let submitted = false;
    try {
      const proposal = await fetchJson(`${base}/${encodeURIComponent(r.name)}/${verb}`, { method: "POST", headers: JSON_HEADERS, body: "{}" });
      submitted = true;
      const done = await settleAction(proposal);
      const action = await withResult(done.action);
      const outcome = describeActionOutcome(action);
      if (action.status === "proposed") setMessage({ text: WAITING2[verb], warn: false });
      else if (outcome.tone === "success") setMessage({ text: action.result?.output ?? outcome.text, warn: false });
      else if (outcome.tone === "pending") setMessage({ text: done.text, warn: false });
      else setMessage({ text: action.result?.error ?? outcome.text, warn: true });
      setReload((n) => n + 1);
    } catch (err) {
      const text = submitted ? `Das Ergebnis lie\xDF sich nicht abrufen (${errorMessage(err)}). Wie es ausging, steht unter \u201EAktionen\u201C.` : errorMessage(err);
      setMessage({ text, warn: true });
    } finally {
      setBusy(null);
    }
  }
  if (error) return /* @__PURE__ */ jsx12(LoadError, { what: "Die Registries", error, onRetry: () => setReload((n) => n + 1) });
  if (!state) return /* @__PURE__ */ jsx12("p", { className: "text-sm opacity-60", children: "Lese die Anmeldungen von Docker \u2026" });
  const settings = /* @__PURE__ */ jsx12(SettingsLink, { to: "/settings/extensions/docker", permission: "extensions.manage", variant: "secondary", children: "Moduleinstellungen" });
  const warnings = (state.warnings ?? []).map((w) => /* @__PURE__ */ jsx12("p", { className: "text-sm text-amber-300", role: "alert", children: w }, w));
  if (state.registries.length === 0) {
    return /* @__PURE__ */ jsxs11("div", { className: "flex flex-col items-start gap-2 text-sm", "data-testid": "registries", children: [
      warnings,
      warnings.length === 0 && /* @__PURE__ */ jsx12("p", { className: "opacity-70", children: "Noch keine private Registry eingetragen. Brauchst du Images, die nur mit Anmeldung zu holen sind (etwa private Pakete auf ghcr.io), trag die Registry in den Einstellungen des Moduls unter \u201EErweitert\u201C ein, mit Passwort oder Token f\xFCr den Tresor." }),
      settings
    ] });
  }
  const canChange = deck().hasPermission("hosts.execute");
  return /* @__PURE__ */ jsxs11("div", { className: "flex flex-col gap-2", "data-testid": "registries", children: [
    /* @__PURE__ */ jsxs11("p", { className: "text-xs opacity-60", children: [
      "Angemeldet wird je Server. Docker speichert die Anmeldung dort in ~/.docker/config.json des Zugangs von Nodvard Deck",
      state.creds_store ? ` \xFCber das Hilfsprogramm \u201E${state.creds_store}\u201C` : ", nur umkodiert, nicht verschl\xFCsselt",
      ", wie bei docker login von Hand."
    ] }),
    warnings,
    state.error && /* @__PURE__ */ jsx12(LoadError, { what: "Die Anmeldungen", error: state.error, onRetry: () => setReload((n) => n + 1), compact: true }),
    message && /* @__PURE__ */ jsx12("p", { className: `whitespace-pre-line break-words text-sm ${message.warn ? "text-red-400" : "opacity-90"}`, role: "status", children: message.text }),
    /* @__PURE__ */ jsx12("ul", { className: "divide-y divide-white/5 text-sm", children: state.registries.map((r) => /* @__PURE__ */ jsxs11("li", { className: "flex flex-wrap items-center gap-x-3 gap-y-1 py-2", "data-testid": `registry-${r.name}`, children: [
      /* @__PURE__ */ jsxs11("div", { className: "min-w-0 flex-1", children: [
        /* @__PURE__ */ jsxs11("p", { className: "break-words", children: [
          r.name,
          " ",
          status(r)
        ] }),
        /* @__PURE__ */ jsxs11("p", { className: "break-all text-xs opacity-60", children: [
          r.address,
          " \xB7 als ",
          r.username
        ] }),
        !r.secret_set && /* @__PURE__ */ jsx12("p", { className: "text-xs text-amber-300", children: "Kein Passwort im Tresor. Trag es in den Einstellungen des Moduls ein." })
      ] }),
      canChange && /* @__PURE__ */ jsxs11("div", { className: "flex shrink-0 gap-1.5", children: [
        r.logged_in !== true && /* @__PURE__ */ jsx12(Button, { small: true, variant: "primary", disabled: busy !== null || !r.secret_set, onClick: () => void run(r, "login"), ariaLabel: `Bei ${r.name} anmelden`, children: busy === r.name ? "\u2026" : "Anmelden" }),
        r.logged_in !== false && /* @__PURE__ */ jsx12(Button, { small: true, variant: "secondary", disabled: busy !== null, onClick: () => void run(r, "logout"), ariaLabel: `Bei ${r.name} abmelden`, children: "Abmelden" })
      ] })
    ] }, r.name)) }),
    /* @__PURE__ */ jsx12("div", { children: settings })
  ] });
}
function RegistriesSection({ hostId, onHost, empty }) {
  return /* @__PURE__ */ jsx12(HostSection, { hostId, onHost, empty, children: (id) => /* @__PURE__ */ jsx12(RegistryList, { hostId: id }, id) });
}

// src/Stacks.tsx
import { useCallback as useCallback2, useEffect as useEffect11, useRef as useRef6, useState as useState12 } from "react";

// src/Backups.tsx
import { useEffect as useEffect9, useState as useState10 } from "react";
import { jsx as jsx13, jsxs as jsxs12 } from "react/jsx-runtime";
var WAITING3 = "Vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe \u201EAktionen\u201C.";
function named(names, one, many) {
  return `${names.join(", ")} ${names.length === 1 ? one : many}`;
}
function notContained(backup) {
  return [.../* @__PURE__ */ new Set([...backup.missing ?? [], ...backup.unfinished ?? []])].sort();
}
function when(iso) {
  return new Date(iso).toLocaleString("de-DE", { day: "numeric", month: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" });
}
function BackupsPanel({ hostId, project, onDone }) {
  const [list, setList] = useState10(null);
  const [error, setError] = useState10(null);
  const [failed, setFailed] = useState10(null);
  const [chosen, setChosen] = useState10({});
  const [busy, setBusy] = useState10(false);
  const [reload, setReload] = useState10(0);
  const base = `/ext/docker/hosts/${encodeURIComponent(hostId)}/stacks/${encodeURIComponent(project)}/backups`;
  useEffect9(() => {
    const controller = new AbortController();
    setList(null);
    setError(null);
    fetchJson(base, { signal: controller.signal }).then((body) => {
      if (controller.signal.aborted) return;
      setList(body);
      setChosen(Object.fromEntries(body.backups.map((b) => [b.stamp, b.volumes.filter((v) => v.restorable).map((v) => v.name)])));
    }).catch((err) => {
      if (!controller.signal.aborted) setError(errorMessage(err));
    });
    return () => controller.abort();
  }, [base, reload]);
  async function send(path, body, question, label, danger) {
    if (!await deck().confirmDialog(question, { confirmLabel: label, danger })) return;
    setBusy(true);
    setFailed(null);
    let submitted = false;
    try {
      const proposal = await fetchJson(`${base}/${path}`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body) });
      submitted = true;
      const run = await settleAction(proposal);
      const action = await withResult(run.action);
      const outcome = describeActionOutcome(action);
      if (action.status === "proposed") onDone(WAITING3);
      else if (outcome.tone === "success") onDone(action.result?.output ?? outcome.text);
      else if (outcome.tone === "pending") onDone(run.text);
      else if (action.result) onDone(action.result.error ?? outcome.text, true);
      else setFailed(outcome.text);
    } catch (err) {
      setFailed(submitted ? `Das Ergebnis lie\xDF sich nicht abrufen (${errorMessage(err)}). Wie es ausging, steht unter \u201EAktionen\u201C.` : errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  function restore(backup) {
    const volumes = chosen[backup.stamp] ?? [];
    const picked = backup.volumes.filter((v) => volumes.includes(v.name));
    const idle = [...new Set(picked.flatMap((v) => v.stopped_services ?? []))].sort();
    const services = [...new Set(picked.flatMap((v) => v.services))].filter((s) => !idle.includes(s)).sort();
    let question = `${volumes.join(", ")} auf den Stand vom ${when(backup.at)} zur\xFCckspielen? Was seitdem in ${volumes.length === 1 ? "diesem Volume" : "diesen Volumes"} dazukam oder sich \xE4nderte, ist danach weg.`;
    question += services.length > 0 ? ` ${named(services, "stoppt", "stoppen")} daf\xFCr, bis das Zur\xFCckspielen fertig ist; bei gro\xDFen Volumes kann das dauern.` : " Bei gro\xDFen Volumes kann das dauern.";
    if (idle.length > 0 && services.length === 0) question += ` ${named(idle, "ist gestoppt und bleibt", "sind gestoppt und bleiben")} es.`;
    else if (idle.length > 0) {
      question += idle.length === 1 ? ` ${idle[0]} ist gestoppt. Deck startet ihn nicht; Compose kann ihn aber mitstarten, wenn ein laufender Dienst ihn braucht, etwa als Einmal-Dienst f\xFCr Migrationen.` : ` ${idle.join(", ")} sind gestoppt. Deck startet sie nicht; Compose kann sie aber mitstarten, wenn ein laufender Dienst sie braucht, etwa als Einmal-Dienst f\xFCr Migrationen.`;
    }
    const gaps = notContained(backup);
    if (gaps.length > 0) {
      question += ` ${named(gaps, "ist nicht in dieser Sicherung und bleibt", "sind nicht in dieser Sicherung und bleiben")} auf dem heutigen Stand; die Daten der Dienste passen danach vielleicht nicht zusammen.`;
    }
    void send(`${encodeURIComponent(backup.stamp)}/restore`, { volumes }, question, "Zur\xFCckspielen", true);
  }
  function drop(backup) {
    const question = `Sicherung vom ${when(backup.at)} l\xF6schen? Sie l\xE4sst sich danach nicht mehr zur\xFCckspielen.`;
    void send(`${encodeURIComponent(backup.stamp)}/delete`, {}, question, "L\xF6schen", true);
  }
  if (error) return /* @__PURE__ */ jsx13(LoadError, { what: "Die Sicherungen", error, onRetry: () => setReload((n) => n + 1), compact: true });
  if (!list) return /* @__PURE__ */ jsx13("p", { className: "text-xs opacity-60", children: "Lade die Sicherungen \u2026" });
  if (list.error) return /* @__PURE__ */ jsx13(LoadError, { what: "Die Sicherungen", error: list.error, onRetry: () => setReload((n) => n + 1), compact: true });
  const canChange = deck().hasPermission("hosts.execute");
  return /* @__PURE__ */ jsxs12("div", { className: "flex flex-col gap-2 text-xs", "data-testid": `backups-${project}`, children: [
    /* @__PURE__ */ jsxs12("p", { className: "opacity-70", children: [
      "Sicherungen entstehen beim Update mit dem Haken \u201EVolumes vorher sichern\u201C und liegen in ",
      /* @__PURE__ */ jsx13("span", { className: "break-all font-mono", children: list.folder ?? "\u2026" }),
      ". Zur\xFCckspielen ersetzt die Daten im Volume durch den Stand der Sicherung. Deck pr\xFCft vorher das Archiv und den freien Platz; ist etwas nicht in Ordnung, bleiben die Daten, wie sie sind."
    ] }),
    list.note && /* @__PURE__ */ jsx13("p", { className: "text-amber-300", role: "status", children: list.note }),
    list.busy && /* @__PURE__ */ jsxs12("div", { className: "flex flex-wrap items-center gap-2 text-amber-300", role: "status", children: [
      /* @__PURE__ */ jsxs12("span", { children: [
        "Am Stack l\xE4uft gerade etwas (",
        list.busy,
        "). Zur\xFCckspielen und L\xF6schen gehen erst danach."
      ] }),
      /* @__PURE__ */ jsx13(Button, { small: true, onClick: () => setReload((n) => n + 1), children: "Neu laden" })
    ] }),
    list.backups.length === 0 && /* @__PURE__ */ jsx13("p", { className: "opacity-70", children: "F\xFCr diesen Stack gibt es noch keine Sicherung." }),
    /* @__PURE__ */ jsx13("ul", { className: "flex flex-col gap-2", children: list.backups.map((backup) => {
      const picked = chosen[backup.stamp] ?? [];
      const unfinished = backup.unfinished ?? [];
      const gaps = notContained(backup);
      return /* @__PURE__ */ jsxs12("li", { className: "flex flex-col gap-1 rounded border border-white/10 p-2", "data-testid": `backup-${backup.stamp}`, children: [
        /* @__PURE__ */ jsx13("p", { className: "font-medium", children: when(backup.at) }),
        /* @__PURE__ */ jsx13("ul", { className: "flex flex-col gap-0.5", children: backup.volumes.map((v) => /* @__PURE__ */ jsxs12("li", { className: "flex flex-wrap items-center gap-2", children: [
          v.restorable ? /* @__PURE__ */ jsx13(
            "input",
            {
              type: "checkbox",
              "aria-label": `${v.name} zur\xFCckspielen`,
              checked: picked.includes(v.name),
              disabled: busy,
              onChange: (e) => setChosen((all) => ({
                ...all,
                [backup.stamp]: e.target.checked ? [...picked, v.name] : picked.filter((x) => x !== v.name)
              }))
            }
          ) : /* @__PURE__ */ jsx13("span", { className: "w-[13px]" }),
          /* @__PURE__ */ jsx13("span", { className: "font-mono", children: v.name }),
          /* @__PURE__ */ jsx13("span", { className: "opacity-60", children: v.size_bytes != null ? formatBytes(v.size_bytes) : "Gr\xF6\xDFe unbekannt" }),
          v.restorable ? /* @__PURE__ */ jsx13("span", { className: "opacity-60", children: v.services.map((s) => v.stopped_services?.includes(s) ? `${s} (gestoppt)` : s).join(", ") }) : /* @__PURE__ */ jsx13(Badge, { children: "nutzt der Stack nicht mehr" })
        ] }, v.name)) }),
        backup.volumes.length === 0 && /* @__PURE__ */ jsx13("p", { className: "opacity-70", children: "Diese Sicherung enth\xE4lt keine Archive." }),
        gaps.length > 0 && (list.busy ? /* @__PURE__ */ jsxs12("p", { className: "opacity-70", children: [
          "Noch nicht fertig: ",
          gaps.join(", "),
          ". Vielleicht packt Deck gerade daran."
        ] }) : /* @__PURE__ */ jsxs12("p", { className: "text-amber-300", children: [
          "Nicht enthalten: ",
          gaps.join(", "),
          ". Die Sicherung ist unvollst\xE4ndig, etwa weil das Packen abbrach; spielst du nur einen Teil zur\xFCck, passen die Daten der Dienste vielleicht nicht zusammen.",
          unfinished.length > 0 && ` Von ${unfinished.join(", ")} liegt noch ein halbes Archiv im Ordner; \u201ESicherung l\xF6schen\u201C nimmt die Reste mit.`
        ] })),
        backup.contents_error && /* @__PURE__ */ jsx13("p", { className: "text-amber-300", children: backup.contents_error }),
        canChange && /* @__PURE__ */ jsxs12("div", { className: "flex flex-wrap gap-1.5", children: [
          /* @__PURE__ */ jsx13(Button, { small: true, variant: "danger", disabled: busy || Boolean(list.busy) || picked.length === 0, onClick: () => restore(backup), children: busy ? "\u2026" : "Zur\xFCckspielen" }),
          /* @__PURE__ */ jsx13(Button, { small: true, disabled: busy || Boolean(list.busy), onClick: () => drop(backup), children: "Sicherung l\xF6schen" })
        ] })
      ] }, backup.stamp);
    }) }),
    failed && /* @__PURE__ */ jsx13("p", { className: "break-words text-red-400", role: "alert", children: failed })
  ] });
}

// src/NewStack.tsx
import { useEffect as useEffect10, useRef as useRef5, useState as useState11 } from "react";
import { jsx as jsx14, jsxs as jsxs13 } from "react/jsx-runtime";
var EXAMPLE = `services:
  app:
    image: nginx:1.27
    restart: unless-stopped
    ports:
      - "8080:80"
`;
var NAME_RE = /^[a-z0-9][a-z0-9_-]{0,62}$/;
var MAX_BYTES = 256 * 1024;
var RISKY_NOTE2 = "Das Anlegen braucht daf\xFCr eine Freigabe mit hohem Risiko.";
var WAITING4 = "Zum Anlegen vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe \u201EAktionen\u201C.";
function NewStack({ hostId, onClose }) {
  const base = `/ext/docker/hosts/${encodeURIComponent(hostId)}`;
  const [place, setPlace] = useState11(null);
  const [error, setError] = useState11(null);
  const [reload, setReload] = useState11(0);
  const [name, setName] = useState11("");
  const [text, setText] = useState11(EXAMPLE);
  const [checked, setChecked] = useState11(null);
  const [busy, setBusy] = useState11(false);
  const [problem, setProblem] = useState11(null);
  const [rootDraft, setRootDraft] = useState11(null);
  const [asForm, setAsForm] = useState11(false);
  const [templates, setTemplates] = useState11(null);
  const [templateError, setTemplateError] = useState11(null);
  const [templateReload, setTemplateReload] = useState11(0);
  const [picked, setPicked] = useState11(null);
  const checkRun = useRef5(0);
  const life = useRef5(null);
  useEffect10(() => {
    const controller = new AbortController();
    life.current = controller;
    return () => controller.abort();
  }, []);
  useEffect10(() => {
    const controller = new AbortController();
    setError(null);
    fetchJson(`${base}/new-stack`, { signal: controller.signal }).then((body) => {
      if (!controller.signal.aborted) setPlace(body);
    }).catch((err) => {
      if (!controller.signal.aborted) setError(errorMessage(err));
    });
    return () => controller.abort();
  }, [base, reload]);
  useEffect10(() => {
    const controller = new AbortController();
    setTemplateError(null);
    fetchJson("/ext/docker/stack-templates", { signal: controller.signal }).then((body) => {
      if (!controller.signal.aborted) setTemplates(body.templates);
    }).catch((err) => {
      if (!controller.signal.aborted) setTemplateError(errorMessage(err));
    });
    return () => controller.abort();
  }, [templateReload]);
  if (error) return /* @__PURE__ */ jsx14(LoadError, { what: "Der Ordner f\xFCr neue Stacks", error, onRetry: () => setReload((n) => n + 1), compact: true });
  if (!place) return /* @__PURE__ */ jsx14("p", { className: "text-xs opacity-60", children: "Lade \u2026" });
  const nameOk = NAME_RE.test(name);
  const changed = name !== "" || text !== EXAMPLE;
  function edit(next) {
    checkRun.current += 1;
    if (next.name !== void 0) setName(next.name);
    if (next.text !== void 0) setText(next.text);
    setChecked(null);
    setProblem(null);
  }
  async function pick(id) {
    const template = templates?.find((t) => t.id === id);
    if (!template) return;
    const untouched = text === EXAMPLE || text === picked?.compose || text.trim() === "";
    if (!untouched && !await deck().confirmDialog("Den bisherigen Text durch die Vorlage ersetzen?", { confirmLabel: "Ersetzen" })) return;
    setPicked(template);
    edit({ text: template.compose, ...name === "" || name === picked?.id ? { name: template.id } : {} });
  }
  async function leave() {
    if (changed && !await deck().confirmDialog("Der neue Stack ist noch nicht angelegt. Verwerfen?", { confirmLabel: "Verwerfen" })) return;
    onClose(null);
  }
  async function check() {
    const run = ++checkRun.current;
    setBusy(true);
    setProblem(null);
    try {
      const result = await fetchJson(`${base}/new-stack/check`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ name, content: text }) });
      if (run !== checkRun.current) return null;
      setChecked(result);
      return result;
    } catch (err) {
      if (run === checkRun.current) setProblem(errorMessage(err));
      return null;
    } finally {
      setBusy(false);
    }
  }
  async function create(start) {
    const result = await check();
    if (!result?.ok || result.conflict) return;
    const risky = result.warnings.length ? ` Riskant: ${result.warnings.join(" ")} ${RISKY_NOTE2}` : "";
    const then = start ? " und starten" : "";
    const question = `Stack \u201E${name}\u201C in ${result.folder} anlegen${then}? Nodvard Deck legt den Ordner an, schreibt compose.yaml und \xFCbernimmt den Stack.${risky}`;
    if (!await deck().confirmDialog(question, { confirmLabel: start ? "Anlegen und starten" : "Anlegen" })) return;
    setBusy(true);
    const signal = (life.current ?? new AbortController()).signal;
    let submitted = false;
    try {
      const proposal = await fetchJson(`${base}/new-stack`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ name, content: text, start }) });
      submitted = true;
      const run = await settleAction(proposal, { signal });
      const action = await withResult(run.action, signal);
      if (signal.aborted) return;
      const outcome = describeActionOutcome(action);
      if (action.status === "proposed") onClose(WAITING4);
      else if (action.result?.detail?.created === true) {
        onClose(outcome.tone === "success" ? action.result.output ?? "Angelegt." : `${action.result.output ?? ""}
${outcome.text}`.trim());
      } else if (outcome.tone === "pending") onClose(run.text);
      else setProblem(outcome.text);
    } catch (err) {
      if (!signal.aborted) {
        setProblem(submitted ? `Das Ergebnis lie\xDF sich nicht abrufen (${errorMessage(err)}). Ob angelegt wurde, steht unter \u201EAktionen\u201C.` : errorMessage(err));
      }
    } finally {
      if (!signal.aborted) setBusy(false);
    }
  }
  function upload(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (file.size > MAX_BYTES) {
      setProblem(`Die Datei ist gr\xF6\xDFer als ${MAX_BYTES / 1024} KB.`);
      return;
    }
    void file.text().then((content) => {
      setPicked(null);
      edit({ text: content.replace(/\r\n/g, "\n") });
    });
  }
  async function saveRoot(path) {
    setBusy(true);
    setProblem(null);
    try {
      const body = await fetchJson(`${base}/stack-root`, { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ path }) });
      setRootDraft(null);
      setChecked(null);
      setPlace({ ...place, root: body.root, root_state: "unknown", root_problem: null, setting_problem: null });
      setReload((n) => n + 1);
    } catch (err) {
      setProblem(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  return /* @__PURE__ */ jsxs13("div", { className: "flex flex-col gap-2 text-xs", "data-testid": "new-stack", children: [
    /* @__PURE__ */ jsx14("p", { className: "opacity-70", children: "Ein neuer Stack bekommt einen eigenen Ordner. Nodvard Deck legt ihn erst nach der Freigabe an, schreibt darin compose.yaml und \xFCberschreibt nie einen bestehenden Ordner." }),
    rootDraft === null ? /* @__PURE__ */ jsxs13("p", { className: "flex flex-wrap items-center gap-2", children: [
      /* @__PURE__ */ jsxs13("span", { className: "break-all", children: [
        "Ordner f\xFCr neue Stacks: ",
        /* @__PURE__ */ jsx14("span", { className: "font-mono", children: place.root }),
        place.root_state === "missing" && " (gibt es noch nicht, wird mit angelegt)"
      ] }),
      /* @__PURE__ */ jsx14(Button, { small: true, onClick: () => setRootDraft(place.root), children: "\xC4ndern" })
    ] }) : /* @__PURE__ */ jsxs13("div", { className: "flex flex-wrap items-center gap-2", children: [
      /* @__PURE__ */ jsx14(
        "input",
        {
          "aria-label": "Ordner f\xFCr neue Stacks",
          value: rootDraft,
          onChange: (e) => setRootDraft(e.target.value),
          className: "min-w-0 flex-1 rounded border border-white/10 bg-black/25 px-2 py-1 font-mono outline-none focus:border-[var(--color-accent)]"
        }
      ),
      /* @__PURE__ */ jsx14(Button, { small: true, variant: "primary", disabled: busy, onClick: () => void saveRoot(rootDraft.trim()), children: "\xDCbernehmen" }),
      place.root !== place.default && /* @__PURE__ */ jsxs13(Button, { small: true, disabled: busy, onClick: () => void saveRoot(null), children: [
        "Vorgabe (",
        place.default,
        ")"
      ] }),
      place.home && /* @__PURE__ */ jsx14(Button, { small: true, disabled: busy, onClick: () => setRootDraft(`${place.home}/stacks`), children: "Im Heimatordner" }),
      /* @__PURE__ */ jsx14(Button, { small: true, onClick: () => setRootDraft(null), children: "Abbrechen" })
    ] }),
    place.setting_problem && /* @__PURE__ */ jsx14("p", { className: "text-amber-300", role: "status", children: place.setting_problem }),
    place.root_problem && /* @__PURE__ */ jsx14("p", { className: "text-amber-300", role: "status", children: place.root_problem }),
    templateError ? /* @__PURE__ */ jsx14(LoadError, { what: "Die Vorlagen", error: templateError, onRetry: () => setTemplateReload((n) => n + 1), compact: true }) : templates && /* @__PURE__ */ jsxs13("label", { className: "flex flex-col gap-1", children: [
      /* @__PURE__ */ jsx14("span", { children: "Vorlage" }),
      /* @__PURE__ */ jsxs13(
        "select",
        {
          "aria-label": "Vorlage",
          value: picked?.id ?? "",
          disabled: busy,
          onChange: (e) => void pick(e.target.value),
          className: "rounded border border-white/10 bg-black/25 px-2 py-1 outline-none focus:border-[var(--color-accent)]",
          children: [
            /* @__PURE__ */ jsx14("option", { value: "", disabled: true, children: "Ohne Vorlage" }),
            templates.map((t) => /* @__PURE__ */ jsx14("option", { value: t.id, children: t.name }, t.id))
          ]
        }
      )
    ] }),
    picked && /* @__PURE__ */ jsxs13("div", { className: "flex flex-col gap-1 rounded border border-white/10 bg-white/[0.03] p-2", "data-testid": "template-note", children: [
      /* @__PURE__ */ jsx14("p", { children: picked.summary }),
      /* @__PURE__ */ jsx14("p", { className: "opacity-70", children: picked.note }),
      /* @__PURE__ */ jsxs13("p", { className: "opacity-70", children: [
        "Belegt auf dem Server ",
        picked.ports.length === 1 ? "den Port" : "die Ports",
        " ",
        picked.ports.join(", "),
        "."
      ] }),
      text !== picked.compose && /* @__PURE__ */ jsx14("p", { className: "text-amber-300", children: "Du hast die Datei ge\xE4ndert; die Angaben oben gelten f\xFCr die Vorlage, wie sie war." })
    ] }),
    /* @__PURE__ */ jsxs13("label", { className: "flex flex-col gap-1", children: [
      /* @__PURE__ */ jsx14("span", { children: "Name" }),
      /* @__PURE__ */ jsx14(
        "input",
        {
          "aria-label": "Name des Stacks",
          value: name,
          onChange: (e) => edit({ name: e.target.value.trim() }),
          placeholder: "zum Beispiel vaultwarden",
          className: "rounded border border-white/10 bg-black/25 px-2 py-1 outline-none focus:border-[var(--color-accent)]"
        }
      )
    ] }),
    name && !nameOk && /* @__PURE__ */ jsx14("p", { className: "text-amber-300", children: "Nur Kleinbuchstaben, Ziffern, \u201E-\u201C und \u201E_\u201C, am Anfang ein Buchstabe oder eine Ziffer." }),
    nameOk && /* @__PURE__ */ jsxs13("p", { className: "break-all opacity-70", children: [
      "Ordner: ",
      /* @__PURE__ */ jsxs13("span", { className: "font-mono", children: [
        place.root,
        "/",
        name
      ] })
    ] }),
    /* @__PURE__ */ jsxs13("div", { className: "flex flex-wrap gap-1.5", children: [
      /* @__PURE__ */ jsx14(Button, { small: true, onClick: () => {
        setPicked(null);
        edit({ text: EXAMPLE });
      }, children: "Beispiel einf\xFCgen" }),
      /* @__PURE__ */ jsxs13("label", { className: "cursor-pointer rounded-lg border border-white/10 bg-white/[0.06] px-2 py-1 hover:bg-white/[0.12]", children: [
        "Datei hochladen",
        /* @__PURE__ */ jsx14("input", { type: "file", accept: ".yml,.yaml,text/yaml", className: "hidden", "aria-label": "Compose-Datei hochladen", onChange: upload })
      ] }),
      /* @__PURE__ */ jsx14("span", { className: "flex-1" }),
      /* @__PURE__ */ jsx14(Button, { small: true, pressed: !asForm, onClick: () => setAsForm(false), children: "Text" }),
      /* @__PURE__ */ jsx14(Button, { small: true, pressed: asForm, onClick: () => setAsForm(true), children: "Formular" })
    ] }),
    asForm ? /* @__PURE__ */ jsx14(StackForm, { text, onChange: (next) => edit({ text: next }), disabled: busy }) : /* @__PURE__ */ jsx14(
      "textarea",
      {
        "aria-label": "Compose-Datei des neuen Stacks",
        value: text,
        spellCheck: false,
        wrap: "off",
        rows: 16,
        className: "w-full resize-y rounded bg-black/60 p-2 font-mono text-[11px] leading-snug outline-none focus:ring-1 focus:ring-[var(--color-accent)]",
        onChange: (e) => edit({ text: e.target.value })
      }
    ),
    checked?.conflict && /* @__PURE__ */ jsx14("p", { className: "text-red-400", role: "alert", children: checked.conflict }),
    /* @__PURE__ */ jsx14(CheckResult, { checked, note: RISKY_NOTE2 }),
    problem && /* @__PURE__ */ jsx14("p", { className: "break-words text-red-400", role: "alert", children: problem }),
    /* @__PURE__ */ jsxs13("div", { className: "flex flex-wrap gap-1.5", children: [
      /* @__PURE__ */ jsx14(Button, { small: true, disabled: busy || !nameOk, onClick: () => void check(), children: "Pr\xFCfen" }),
      /* @__PURE__ */ jsx14(Button, { small: true, variant: "primary", disabled: busy || !nameOk, onClick: () => void create(false), children: "Anlegen" }),
      /* @__PURE__ */ jsx14(Button, { small: true, disabled: busy || !nameOk, onClick: () => void create(true), children: "Anlegen und starten" }),
      /* @__PURE__ */ jsx14(Button, { small: true, onClick: () => void leave(), children: "Schlie\xDFen" })
    ] })
  ] });
}

// src/Stacks.tsx
import { Fragment as Fragment9, jsx as jsx15, jsxs as jsxs14 } from "react/jsx-runtime";
var STATE = {
  running: { label: "l\xE4uft", tone: "good" },
  partial: { label: "l\xE4uft teilweise", tone: "warn" },
  stopped: { label: "gestoppt", tone: "neutral" }
};
var SOURCE_NOTE = {
  portainer: "Von Portainer verwaltet: Die Datei liegt in Portainers eigenem Speicher, nicht in einem Ordner auf dem Server.",
  unknown: "Docker nennt keine Compose-Datei, die Nodvard Deck lesen kann: sehr altes Compose oder ungew\xF6hnliche Zeichen im Pfad."
};
var MEMBER_STATE = {
  exited: "gestoppt",
  created: "erstellt",
  restarting: "startet neu",
  paused: "pausiert",
  dead: "tot",
  removing: "wird entfernt"
};
function membersText(containers) {
  return containers.map((c) => {
    const note = c.status.includes("(unhealthy)") ? "ungesund" : c.state === "running" ? "" : MEMBER_STATE[c.state] ?? c.state;
    return note ? `${c.name} (${note})` : c.name;
  }).join(", ");
}
function changesText(changes) {
  return [
    changes.changed.length ? `ge\xE4ndert: ${changes.changed.join(", ")}` : "",
    changes.added.length ? `neu in der Datei: ${changes.added.join(", ")}` : "",
    changes.removed.length ? `nicht mehr in der Datei: ${changes.removed.join(", ")}` : ""
  ].filter(Boolean).join(" \xB7 ");
}
function StackFile({ hostId, project }) {
  const [files, setFiles] = useState12(null);
  const [env, setEnv] = useState12([]);
  const [error, setError] = useState12(null);
  const [reload, setReload] = useState12(0);
  useEffect11(() => {
    const controller = new AbortController();
    setFiles(null);
    setError(null);
    fetchJson(`/ext/docker/hosts/${encodeURIComponent(hostId)}/stacks/${encodeURIComponent(project)}/file`, { signal: controller.signal }).then((body) => {
      if (controller.signal.aborted) return;
      setFiles(body.files);
      setEnv(body.env ?? []);
    }).catch((err) => {
      if (!controller.signal.aborted) setError(errorMessage(err));
    });
    return () => controller.abort();
  }, [hostId, project, reload]);
  if (error) return /* @__PURE__ */ jsx15(LoadError, { what: "Die Compose-Datei", error, onRetry: () => setReload((n) => n + 1) });
  if (!files) return /* @__PURE__ */ jsx15("p", { className: "text-xs opacity-60", children: "Lade die Datei \u2026" });
  return /* @__PURE__ */ jsxs14("div", { className: "flex flex-col gap-3", "data-testid": `stack-file-${project}`, children: [
    /* @__PURE__ */ jsx15("p", { className: "text-xs opacity-60", children: "Nur zum Ansehen. So steht die Datei gerade auf dem Server." }),
    files.map((file) => /* @__PURE__ */ jsxs14("div", { children: [
      /* @__PURE__ */ jsx15("p", { className: "mb-1 break-all font-mono text-xs opacity-80", children: file.path }),
      file.content != null ? /* @__PURE__ */ jsx15("pre", { className: "max-h-96 overflow-auto rounded bg-black/60 p-2 font-mono text-[11px] leading-snug", children: file.content }) : /* @__PURE__ */ jsx15("p", { className: "text-xs text-red-400", children: file.error })
    ] }, file.path)),
    env.map((file) => /* @__PURE__ */ jsxs14("div", { "data-testid": "stack-env", children: [
      /* @__PURE__ */ jsx15("p", { className: "mb-1 break-all font-mono text-xs opacity-80", children: file.path }),
      file.error ? /* @__PURE__ */ jsx15("p", { className: "text-xs text-red-400", children: file.error }) : /* @__PURE__ */ jsx15("p", { className: "break-words text-xs", children: file.names.length ? /* @__PURE__ */ jsxs14(Fragment9, { children: [
        "Variablen: ",
        /* @__PURE__ */ jsx15("span", { className: "font-mono", children: file.names.join(", ") })
      ] }) : "Keine Variablen." }),
      file.note && /* @__PURE__ */ jsx15("p", { className: "text-xs text-amber-300", children: file.note })
    ] }, file.path)),
    env.length > 0 && /* @__PURE__ */ jsx15("p", { className: "text-xs opacity-60", children: "Die Werte aus .env zeigt Nodvard Deck nicht, weil darin oft Passw\xF6rter stehen." })
  ] });
}
var ADOPT_TEXT = "\xDCbernehmen hei\xDFt: Nodvard Deck merkt sich Ordner und Dateien dieses Stacks. Am Server \xE4ndert sich dabei nichts, kein Container wird neu erstellt. Vorerst bleibt ein \xFCbernommener Stack dadurch in der Liste, auch wenn gerade keiner seiner Container existiert, und seine Datei l\xE4sst sich weiter ansehen.";
function AdoptPanel({ hostId, project, onDone }) {
  const [check, setCheck] = useState12(null);
  const [error, setError] = useState12(null);
  const [failed, setFailed] = useState12(null);
  const [busy, setBusy] = useState12(false);
  const [reload, setReload] = useState12(0);
  const url = `/ext/docker/hosts/${encodeURIComponent(hostId)}/stacks/${encodeURIComponent(project)}/adopt`;
  useEffect11(() => {
    const controller = new AbortController();
    setCheck(null);
    setError(null);
    fetchJson(url, { signal: controller.signal }).then((body) => {
      if (!controller.signal.aborted) setCheck(body);
    }).catch((err) => {
      if (!controller.signal.aborted) setError(errorMessage(err));
    });
    return () => controller.abort();
  }, [url, reload]);
  async function adopt(shown2) {
    setBusy(true);
    setFailed(null);
    try {
      const res = await authedFetch(url, {
        method: "POST",
        body: JSON.stringify({ working_dir: shown2.working_dir, config_files: shown2.config_files, env_files: shown2.env_files }),
        headers: { "Content-Type": "application/json" }
      });
      if (!res.ok) throw new Error(await errorText(res));
      onDone(`\u201E${project}\u201C ist \xFCbernommen.`);
    } catch (err) {
      setFailed(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  if (error) return /* @__PURE__ */ jsx15(LoadError, { what: "Die Pr\xFCfung", error, onRetry: () => setReload((n) => n + 1), compact: true });
  if (!check) return /* @__PURE__ */ jsx15("p", { className: "text-xs opacity-60", children: "Pr\xFCfe, ob sich der Stack \xFCbernehmen l\xE4sst \u2026" });
  return /* @__PURE__ */ jsxs14("div", { className: "flex flex-col gap-2 text-xs", "data-testid": `adopt-${project}`, children: [
    /* @__PURE__ */ jsx15("p", { className: "opacity-70", children: ADOPT_TEXT }),
    check.ok ? /* @__PURE__ */ jsxs14(Fragment9, { children: [
      /* @__PURE__ */ jsxs14("p", { className: "break-words", children: [
        "Alles bereit. Gemerkt werden der Ordner ",
        check.working_dir,
        " und ",
        [...check.config_files, ...check.env_files].join(", "),
        "."
      ] }),
      /* @__PURE__ */ jsx15("div", { children: /* @__PURE__ */ jsx15(Button, { small: true, variant: "primary", disabled: busy, onClick: () => void adopt(check), children: busy ? "\u2026" : "Jetzt \xFCbernehmen" }) }),
      failed && /* @__PURE__ */ jsxs14("div", { className: "flex flex-wrap items-center gap-2", children: [
        /* @__PURE__ */ jsxs14("p", { className: "text-red-400", role: "alert", children: [
          "\xDCbernehmen hat nicht geklappt: ",
          failed
        ] }),
        /* @__PURE__ */ jsx15(Button, { small: true, onClick: () => {
          setFailed(null);
          setReload((n) => n + 1);
        }, children: "Pr\xFCfung neu laden" })
      ] })
    ] }) : /* @__PURE__ */ jsxs14(Fragment9, { children: [
      /* @__PURE__ */ jsx15("p", { className: "text-amber-300", children: "So l\xE4sst sich der Stack nicht \xFCbernehmen:" }),
      /* @__PURE__ */ jsx15("ul", { className: "list-disc pl-5", children: check.reasons.map((reason) => /* @__PURE__ */ jsx15("li", { className: "break-words", children: reason }, reason)) })
    ] })
  ] });
}
var DELETE_TEXT = "L\xF6schen f\xE4hrt den Stack herunter: Container und Netze sind danach weg, und mit ihnen alles, was nur im Container lag. Die Volumes bleiben. Nodvard Deck vergisst den Stack; sein Ordner mit der Compose-Datei bleibt, au\xDFer du hakst unten das L\xF6schen an.";
function DeletePanel({ hostId, project, onDone }) {
  const [check, setCheck] = useState12(null);
  const [error, setError] = useState12(null);
  const [failed, setFailed] = useState12(null);
  const [withFolder, setWithFolder] = useState12(false);
  const [busy, setBusy] = useState12(false);
  const [reload, setReload] = useState12(0);
  const url = `/ext/docker/hosts/${encodeURIComponent(hostId)}/stacks/${encodeURIComponent(project)}/delete`;
  useEffect11(() => {
    const controller = new AbortController();
    setCheck(null);
    setError(null);
    fetchJson(url, { signal: controller.signal }).then((body) => {
      if (!controller.signal.aborted) setCheck(body);
    }).catch((err) => {
      if (!controller.signal.aborted) setError(errorMessage(err));
    });
    return () => controller.abort();
  }, [url, reload]);
  async function remove(folder) {
    const what = withFolder ? ` Auch der Ordner ${folder} mit allen Dateien darin wird gel\xF6scht; ein Zur\xFCck gibt es nicht.` : "";
    if (!await deck().confirmDialog(`Stack \u201E${project}\u201C l\xF6schen?${what} Die Volumes bleiben.`, { confirmLabel: "Stack l\xF6schen" })) return;
    setBusy(true);
    setFailed(null);
    let submitted = false;
    try {
      const proposal = await fetchJson(url, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ remove_folder: withFolder }) });
      submitted = true;
      const run = await settleAction(proposal);
      const action = await withResult(run.action);
      const outcome = describeActionOutcome(action);
      if (action.status === "proposed") onDone("Zum L\xF6schen vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe \u201EAktionen\u201C.");
      else if (outcome.tone === "success") onDone(action.result?.output ?? `Stack \u201E${project}\u201C gel\xF6scht.`);
      else if (outcome.tone === "pending") onDone(run.text);
      else if (action.result?.output) onDone(`${action.result.output}
${outcome.text}`, true);
      else setFailed(outcome.text);
    } catch (err) {
      setFailed(submitted ? `Das Ergebnis lie\xDF sich nicht abrufen (${errorMessage(err)}). Ob gel\xF6scht wurde, steht unter \u201EAktionen\u201C.` : errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  if (error) return /* @__PURE__ */ jsx15(LoadError, { what: "Die Pr\xFCfung", error, onRetry: () => setReload((n) => n + 1), compact: true });
  if (!check) return /* @__PURE__ */ jsx15("p", { className: "text-xs opacity-60", children: "Pr\xFCfe, was sich l\xF6schen l\xE4sst \u2026" });
  return /* @__PURE__ */ jsxs14("div", { className: "flex flex-col gap-2 text-xs", "data-testid": `delete-${project}`, children: [
    /* @__PURE__ */ jsx15("p", { className: "opacity-70", children: DELETE_TEXT }),
    /* @__PURE__ */ jsxs14("label", { className: "flex items-start gap-2", children: [
      /* @__PURE__ */ jsx15("input", { type: "checkbox", checked: withFolder, disabled: busy || check.folder_refusal !== null, onChange: (e) => setWithFolder(e.target.checked) }),
      /* @__PURE__ */ jsxs14("span", { className: "break-all", children: [
        "Auch den Ordner ",
        check.folder,
        " mit allen Dateien darin l\xF6schen"
      ] })
    ] }),
    check.folder_refusal && /* @__PURE__ */ jsx15("p", { className: "opacity-70", children: check.folder_refusal }),
    withFolder && /* @__PURE__ */ jsx15("p", { className: "text-red-400", children: "Darin liegen oft die Daten des Stacks, etwa in Unterordnern wie ./daten. Sie sind danach weg, ein Zur\xFCck gibt es nicht." }),
    failed && /* @__PURE__ */ jsx15("p", { className: "break-words text-red-400", role: "alert", children: failed }),
    /* @__PURE__ */ jsx15("div", { children: /* @__PURE__ */ jsx15(Button, { small: true, variant: "danger", disabled: busy, onClick: () => void remove(check.folder), children: busy ? "\u2026" : "Stack l\xF6schen" }) })
  ] });
}
var KEEP_IN_PORTAINER = "L\xF6sche den Stack danach nicht in Portainer und rolle ihn dort nicht neu aus: Er hat denselben Projektnamen, Portainer w\xFCrde die neuen Container entfernen oder mit seiner alten Datei ersetzen.";
function PortainerPanel({ hostId, project, onDone }) {
  const [plan, setPlan] = useState12(null);
  const [error, setError] = useState12(null);
  const [failed, setFailed] = useState12(null);
  const [busy, setBusy] = useState12(false);
  const [reload, setReload] = useState12(0);
  const url = `/ext/docker/hosts/${encodeURIComponent(hostId)}/stacks/${encodeURIComponent(project)}/from-portainer`;
  useEffect11(() => {
    const controller = new AbortController();
    setPlan(null);
    setError(null);
    fetchJson(url, { signal: controller.signal }).then((body) => {
      if (!controller.signal.aborted) setPlan(body);
    }).catch((err) => {
      if (!controller.signal.aborted) setError(errorMessage(err));
    });
    return () => controller.abort();
  }, [url, reload]);
  async function move(folder) {
    const started = plan?.stopped ? " Der Stack ist gerade gestoppt; der Umzug startet ihn." : "";
    const question = `Stack \u201E${project}\u201C von Portainer nach ${folder} holen und dort neu ausrollen? Die Container werden einmal neu erstellt, die Volumes bleiben; was nur in den alten Containern lag und nicht in einem Volume oder Ordner, ist danach weg.${started} ${KEEP_IN_PORTAINER}`;
    if (!await deck().confirmDialog(question, { confirmLabel: "Umziehen" })) return;
    setBusy(true);
    setFailed(null);
    let submitted = false;
    try {
      const proposal = await fetchJson(url, { method: "POST", headers: JSON_HEADERS, body: "{}" });
      submitted = true;
      const run = await settleAction(proposal);
      const action = await withResult(run.action);
      const outcome = describeActionOutcome(action);
      if (action.status === "proposed") onDone("Zum Umzug vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe \u201EAktionen\u201C.");
      else if (outcome.tone === "success") onDone(action.result?.output ?? `Stack \u201E${project}\u201C umgezogen.`);
      else if (outcome.tone === "pending") onDone(run.text);
      else if (action.result?.output) onDone(`${action.result.output}
${outcome.text}`, true);
      else setFailed(outcome.text);
    } catch (err) {
      setFailed(submitted ? `Das Ergebnis lie\xDF sich nicht abrufen (${errorMessage(err)}). Wie es ausging, steht unter \u201EAktionen\u201C.` : errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  if (error) return /* @__PURE__ */ jsx15(LoadError, { what: "Die Pr\xFCfung", error, onRetry: () => setReload((n) => n + 1), compact: true });
  if (!plan) return /* @__PURE__ */ jsx15("p", { className: "text-xs opacity-60", children: "Pr\xFCfe, ob der Umzug geht \u2026" });
  return /* @__PURE__ */ jsxs14("div", { className: "flex flex-col gap-2 text-xs", "data-testid": `portainer-${project}`, children: [
    /* @__PURE__ */ jsxs14("p", { className: "opacity-70", children: [
      "Nodvard Deck holt Datei und Variablen dieses Stacks aus Portainer in den Ordner ",
      /* @__PURE__ */ jsx15("span", { className: "break-all font-mono", children: plan.folder }),
      " und rollt ihn dort unter demselben Namen neu aus. Die Container werden dabei einmal neu erstellt, die Volumes mit den Daten bleiben; was nur in den alten Containern lag und nicht in einem Volume oder Ordner, geht dabei verloren. Danach bearbeitest du ihn hier."
    ] }),
    /* @__PURE__ */ jsxs14("p", { className: "text-amber-300", children: [
      KEEP_IN_PORTAINER,
      " Lass den Eintrag stehen oder schalte Portainer ganz ab, wenn alle Stacks umgezogen sind."
    ] }),
    plan.stopped && /* @__PURE__ */ jsx15("p", { className: "text-amber-300", children: "Der Stack ist gerade gestoppt; der Umzug startet ihn. Dienste f\xFCr einmalige Aufgaben laufen dabei noch einmal." }),
    plan.problem && /* @__PURE__ */ jsx15("p", { className: "break-words text-red-400", role: "alert", children: plan.problem }),
    failed && /* @__PURE__ */ jsx15("p", { className: "break-words text-red-400", role: "alert", children: failed }),
    /* @__PURE__ */ jsx15("div", { children: /* @__PURE__ */ jsx15(Button, { small: true, variant: "primary", disabled: busy || plan.problem !== null, onClick: () => void move(plan.folder), children: busy ? "\u2026" : "Umziehen" }) })
  ] });
}
var IMAGE_STATUS = {
  update: "Update verf\xFCgbar",
  current: "aktuell",
  local: "selbst gebaut, keine Registry",
  unknown: "nicht pr\xFCfbar"
};
function withSharers(plan, services) {
  const images = new Set(plan.services.filter((s) => services.includes(s.service)).map((s) => s.image));
  return [...services, ...plan.services.filter((s) => !services.includes(s.service) && images.has(s.image)).map((s) => s.service)];
}
function backupDate(plan) {
  const dates = plan.services.filter((s) => s.backup && s.backup_at).map((s) => new Date(s.backup_at));
  if (dates.length === 0) return null;
  return new Date(Math.max(...dates.map((d) => d.getTime()))).toLocaleDateString("de-DE");
}
function UpdatePanel2({ hostId, project, onDone }) {
  const [plan, setPlan] = useState12(null);
  const [error, setError] = useState12(null);
  const [failed, setFailed] = useState12(null);
  const [chosen, setChosen] = useState12([]);
  const [saveVolumes, setSaveVolumes] = useState12(false);
  const [busy, setBusy] = useState12(false);
  const [reload, setReload] = useState12(0);
  const base = `/ext/docker/hosts/${encodeURIComponent(hostId)}/stacks/${encodeURIComponent(project)}`;
  useEffect11(() => {
    const controller = new AbortController();
    setPlan(null);
    setError(null);
    fetchJson(`${base}/update`, { signal: controller.signal }).then((body) => {
      if (controller.signal.aborted) return;
      setPlan(body);
      setChosen(body.updates);
    }).catch((err) => {
      if (!controller.signal.aborted) setError(errorMessage(err));
    });
    return () => controller.abort();
  }, [base, reload]);
  async function send(path, services, question, label, backup = false) {
    if (!await deck().confirmDialog(question, { confirmLabel: label })) return;
    setBusy(true);
    setFailed(null);
    let submitted = false;
    try {
      const proposal = await fetchJson(`${base}/${path}`, {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify(path === "update" ? { services, backup } : { services })
      });
      submitted = true;
      const run = await settleAction(proposal);
      const action = await withResult(run.action);
      const outcome = describeActionOutcome(action);
      if (action.status === "proposed") onDone("Vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe \u201EAktionen\u201C.");
      else if (outcome.tone === "success") onDone(action.result?.output ?? outcome.text);
      else if (outcome.tone === "pending") onDone(run.text);
      else if (action.result) onDone(action.result.error ?? outcome.text, true);
      else setFailed(outcome.text);
    } catch (err) {
      setFailed(submitted ? `Das Ergebnis lie\xDF sich nicht abrufen (${errorMessage(err)}). Wie es ausging, steht unter \u201EAktionen\u201C.` : errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  if (error) return /* @__PURE__ */ jsx15(LoadError, { what: "Die \xDCbersicht", error, onRetry: () => setReload((n) => n + 1), compact: true });
  if (!plan) return /* @__PURE__ */ jsx15("p", { className: "text-xs opacity-60", children: "Lade die \xDCbersicht \u2026" });
  const together = withSharers(plan, chosen);
  const sharers = together.filter((s) => !chosen.includes(s));
  const stored = plan.services.filter((s) => together.includes(s.service) && s.data_store).map((s) => s.service);
  const unapplied = plan.unapplied ?? [];
  const blockedUpdate = together.filter((s) => unapplied.includes(s));
  const blockedBack = plan.rollback.filter((s) => unapplied.includes(s));
  const checkError = plan.unapplied_error ?? null;
  const since = backupDate(plan);
  const pause = "Die gew\xE4hlten Dienste werden neu erstellt und sind dabei kurz nicht erreichbar.";
  const volumePlan = plan.volume_backup;
  const volumes = [...new Set(plan.services.filter((s) => together.includes(s.service)).flatMap((s) => s.volumes ?? []))].sort();
  const sizes = volumes.map((v) => volumePlan?.volumes[v]);
  const size2 = sizes.every((v) => v != null) ? formatBytes(sizes.reduce((sum, v) => sum + (v ?? 0), 0)) : "Gr\xF6\xDFe unbekannt";
  const canSave = !!volumePlan && !volumePlan.error && volumes.length > 0;
  const withBackup = saveVolumes && canSave;
  return /* @__PURE__ */ jsxs14("div", { className: "flex flex-col gap-2 text-xs", "data-testid": `update-${project}`, children: [
    /* @__PURE__ */ jsx15("p", { className: "opacity-70", children: "Das Update holt die neuen Images, beh\xE4lt die alten als Sicherung und erstellt nur die gew\xE4hlten Dienste neu. Startet einer danach nicht, geht es von selbst zur alten Version zur\xFCck, au\xDFer bei Datenbanken und anderen Datenspeichern." }),
    /* @__PURE__ */ jsx15("ul", { className: "flex flex-col gap-1", children: plan.services.map((s) => /* @__PURE__ */ jsxs14("li", { className: "flex flex-wrap items-center gap-2", children: [
      s.status === "update" && !s.stopped && !s.mixed ? /* @__PURE__ */ jsx15(
        "input",
        {
          type: "checkbox",
          "aria-label": `${s.service} aktualisieren`,
          checked: chosen.includes(s.service),
          disabled: busy,
          onChange: (e) => setChosen((all) => e.target.checked ? [...all, s.service] : all.filter((x) => x !== s.service))
        }
      ) : /* @__PURE__ */ jsx15("span", { className: "w-[13px]" }),
      /* @__PURE__ */ jsx15("span", { className: "font-mono", children: s.service }),
      /* @__PURE__ */ jsx15("span", { className: "break-all opacity-60", children: s.image }),
      /* @__PURE__ */ jsx15(Badge, { tone: s.status === "update" ? "warn" : "neutral", children: IMAGE_STATUS[s.status ?? ""] ?? "noch nicht gepr\xFCft" }),
      s.data_store && /* @__PURE__ */ jsx15(Badge, { tone: "info", children: "Datenspeicher" }),
      s.stopped && /* @__PURE__ */ jsx15(Badge, { tone: "neutral", children: "gestoppt" }),
      s.mixed && /* @__PURE__ */ jsx15(Badge, { tone: "warn", children: "Container mit verschiedenen Images" }),
      s.reason && /* @__PURE__ */ jsx15("span", { className: "opacity-60", children: s.reason })
    ] }, s.service)) }),
    plan.updates.length === 0 && /* @__PURE__ */ jsx15("p", { className: "opacity-70", children: "F\xFCr keinen Dienst meldet die Image-Pr\xFCfung gerade ein Update." }),
    plan.services.some((s) => s.stopped && s.status === "update") && /* @__PURE__ */ jsx15("p", { className: "opacity-70", children: "Gestoppte Dienste aktualisiert Deck hier nicht, weil sie dabei starten w\xFCrden; sie bekommen das Update beim n\xE4chsten Anwenden." }),
    sharers.length > 0 && /* @__PURE__ */ jsxs14("p", { className: "opacity-70", children: [
      sharers.join(", "),
      " ",
      sharers.length === 1 ? "nutzt" : "nutzen",
      " dasselbe Image und ",
      sharers.length === 1 ? "kommt" : "kommen",
      " mit."
    ] }),
    stored.length > 0 && /* @__PURE__ */ jsxs14("p", { className: "text-amber-300", children: [
      "Dabei ist ein Datenspeicher (",
      stored.join(", "),
      "): Geht etwas schief, springt Deck nicht von selbst zur\xFCck, und die Freigabe braucht die hohe Stufe. Lege vorher eine Sicherung der Daten an."
    ] }),
    (blockedUpdate.length > 0 || blockedBack.length > 0) && /* @__PURE__ */ jsxs14("p", { className: "text-amber-300", role: "alert", children: [
      "In der Datei steht f\xFCr ",
      [.../* @__PURE__ */ new Set([...blockedUpdate, ...blockedBack])].join(", "),
      " etwas anderes, als gerade l\xE4uft. Ein Update oder der R\xFCckweg w\xFCrde das mit anwenden. Wende den Stack zuerst an oder nimm die \xC4nderung zur\xFCck."
    ] }),
    plan.updates.length > 0 && volumes.length > 0 && /* @__PURE__ */ jsxs14("div", { className: "flex flex-col gap-1", children: [
      /* @__PURE__ */ jsxs14("label", { className: "flex items-start gap-2", children: [
        /* @__PURE__ */ jsx15("input", { type: "checkbox", checked: withBackup, disabled: busy || !canSave, onChange: (e) => setSaveVolumes(e.target.checked) }),
        /* @__PURE__ */ jsxs14("span", { className: "break-all", children: [
          "Volumes vorher sichern (",
          volumes.join(", "),
          ": ",
          size2,
          volumePlan?.free_bytes != null ? `, frei ${formatBytes(volumePlan.free_bytes)}` : "",
          ")"
        ] })
      ] }),
      volumePlan?.error ? /* @__PURE__ */ jsx15("p", { className: "opacity-70", children: volumePlan.error }) : /* @__PURE__ */ jsxs14("p", { className: "opacity-70", children: [
        "Die Dienste sind w\xE4hrend der Sicherung gestoppt. Die Archive kommen nach ",
        volumePlan?.folder ?? "\u2026",
        "; nur sie bringen auch Daten zur\xFCck, die die neue Version umbaut."
      ] })
    ] }),
    checkError && /* @__PURE__ */ jsx15("p", { className: "text-amber-300", role: "alert", children: checkError }),
    failed && /* @__PURE__ */ jsx15("p", { className: "break-words text-red-400", role: "alert", children: failed }),
    /* @__PURE__ */ jsxs14("div", { className: "flex flex-wrap gap-1.5", children: [
      plan.updates.length > 0 && /* @__PURE__ */ jsx15(
        Button,
        {
          small: true,
          variant: "primary",
          disabled: busy || chosen.length === 0 || blockedUpdate.length > 0 || checkError !== null,
          onClick: () => void send("update", chosen, `Stack \u201E${project}\u201C aktualisieren (${together.join(", ")})? ${pause}${withBackup ? " Vorher werden die Volumes gesichert; die Dienste sind dabei gestoppt." : ""}`, "Aktualisieren", withBackup),
          children: busy ? "\u2026" : "Jetzt aktualisieren"
        }
      ),
      plan.rollback.length > 0 && /* @__PURE__ */ jsxs14(
        Button,
        {
          small: true,
          disabled: busy || blockedBack.length > 0 || checkError !== null,
          onClick: () => void send("rollback", plan.rollback, `${plan.rollback.join(", ")} von \u201E${project}\u201C auf die Version vor dem letzten Update zur\xFCcksetzen? ${pause} Daten, die die neue Version schon umgebaut hat, bleiben so.`, "Zur\xFCcksetzen"),
          children: [
            "Zur\xFCck zur vorigen Version (",
            plan.rollback.join(", "),
            since ? `, Sicherung vom ${since}` : "",
            ")"
          ]
        }
      )
    ] })
  ] });
}
var VERBS = [
  {
    type: "docker.stack_up",
    label: "Anwenden",
    question: (p) => `Stack \u201E${p}\u201C anwenden?`,
    confirm: "Der Stack startet nach seiner Compose-Datei. Ge\xE4nderte Dienste werden dabei neu erstellt und sind kurz nicht erreichbar.",
    when: () => true
  },
  {
    type: "docker.stack_stop",
    label: "Stoppen",
    question: (p) => `Stack \u201E${p}\u201C stoppen?`,
    confirm: "Alle Container des Stacks werden gestoppt. Seine Dienste sind nicht erreichbar, bis du ihn wieder anwendest.",
    when: (s) => s.running > 0
  },
  {
    type: "docker.stack_down",
    label: "Herunterfahren",
    question: (p) => `Stack \u201E${p}\u201C herunterfahren?`,
    confirm: "Container und Netze des Stacks werden gel\xF6scht. Was nur im Container lag und nicht in einem Volume oder Ordner, ist danach weg. Die Volumes bleiben, und \u201EAnwenden\u201C legt die Container neu an.",
    when: (s) => s.total > 0
  },
  {
    type: "docker.stack_pull",
    label: "Images holen",
    question: (p) => `Images f\xFCr den Stack \u201E${p}\u201C neu holen?`,
    confirm: "Die Images des Stacks werden neu heruntergeladen. Die Container laufen dabei weiter; neu erstellt werden sie erst beim n\xE4chsten \u201EAnwenden\u201C.",
    when: () => true
  }
];
function usageText(usage2, measured, running) {
  if (!running) return "";
  if (!measured) return "CPU/RAM werden gemessen \u2026";
  if (!usage2) return "CPU/RAM unbekannt";
  const cpu = usage2.cpu_percent != null ? `${usage2.cpu_percent.toFixed(1)} %` : "n.\xA0v.";
  return `CPU ${cpu} \xB7 RAM ${usage2.mem_used != null ? formatBytes(usage2.mem_used) : "n.\xA0v."}`;
}
function StackList({ hostId }) {
  const [stacks, setStacks] = useState12(null);
  const [checkError, setCheckError] = useState12(null);
  const [error, setError] = useState12(null);
  const [usage2, setUsage] = useState12({});
  const [measured, setMeasured] = useState12(false);
  const [open, setOpen] = useState12(null);
  const [message, setMessage] = useState12(null);
  const note = (text, warn2 = false) => setMessage(text === null ? null : { text, warn: warn2 });
  const [busy, setBusy] = useState12(null);
  const [reload, setReload] = useState12(0);
  const [creating, setCreating] = useState12(false);
  useEffect11(() => {
    const controller = new AbortController();
    const base = `/ext/docker/hosts/${encodeURIComponent(hostId)}/stacks`;
    setStacks(null);
    setError(null);
    setMeasured(false);
    fetchJson(base, { signal: controller.signal }).then((body) => {
      if (controller.signal.aborted) return;
      setStacks(body.stacks);
      setCheckError(body.check_error);
    }).catch((err) => {
      if (!controller.signal.aborted) setError(errorMessage(err));
    });
    fetchJson(`${base}/usage`, { signal: controller.signal }).then((body) => {
      if (!controller.signal.aborted) setUsage(body.usage);
    }).catch(() => {
      if (!controller.signal.aborted) setUsage({});
    }).finally(() => {
      if (!controller.signal.aborted) setMeasured(true);
    });
    return () => controller.abort();
  }, [hostId, reload]);
  const editorDirty = useRef6(false);
  const markDirty = useCallback2((dirty) => {
    editorDirty.current = dirty;
  }, []);
  async function toggle(project, panel) {
    if (open?.panel === "edit" && editorDirty.current && !await deck().confirmDialog("Deine \xC4nderung im Editor ist noch nicht gespeichert. Verwerfen?", { confirmLabel: "Verwerfen" })) return;
    editorDirty.current = false;
    setOpen(open?.project === project && open.panel === panel ? null : { project, panel });
  }
  async function act(stack, verb) {
    const ok = await deck().confirmDialog(`${verb.question(stack.project)} ${verb.confirm}`, { confirmLabel: verb.label });
    if (!ok) return;
    setBusy(`${stack.project}:${verb.type}`);
    note(null);
    try {
      const result = await hostAction(hostId, verb.type, `Stack \u201E${stack.project}\u201C \xFCber die Docker-Seite: ${verb.label}.`, void 0, { project: stack.project });
      const named2 = result.text.startsWith(`Stack \u201E${stack.project}\u201C`);
      note(named2 ? result.text : `Stack \u201E${stack.project}\u201C, ${verb.label}: ${result.text}`);
      setReload((n) => n + 1);
    } catch (err) {
      note(`Fehler: ${errorMessage(err)}`, true);
    } finally {
      setBusy(null);
    }
  }
  async function release(stack) {
    const project = stack.project;
    const ok = await deck().confirmDialog(
      `\xDCbernahme von \u201E${project}\u201C aufheben? Nodvard Deck vergisst nur Ordner und Dateien; Stack, Datei und Container bleiben, wie sie sind.${stack.total ? "" : " Weil gerade keiner seiner Container existiert, verschwindet er danach aus der Liste."}`,
      { confirmLabel: "\xDCbernahme aufheben" }
    );
    if (!ok) return;
    note(null);
    try {
      const res = await authedFetch(`/ext/docker/hosts/${encodeURIComponent(hostId)}/stacks/${encodeURIComponent(project)}/adopt`, { method: "DELETE" });
      if (!res.ok) throw new Error(await errorText(res));
      note(`\xDCbernahme von \u201E${project}\u201C aufgehoben.`);
      setReload((n) => n + 1);
    } catch (err) {
      note(`Fehler: ${errorMessage(err)}`, true);
    }
  }
  if (error) return /* @__PURE__ */ jsx15(LoadError, { what: "Die Liste der Stacks", error, onRetry: () => setReload((n) => n + 1) });
  if (!stacks) return /* @__PURE__ */ jsx15("p", { className: "text-sm opacity-60", children: "Lade die Stacks \u2026" });
  return /* @__PURE__ */ jsxs14("div", { "data-testid": "stacks", children: [
    /* @__PURE__ */ jsx15("p", { className: "mb-2 text-xs opacity-60", children: "Ein Stack ist ein Compose-Projekt: Container, die zusammen aus einer Compose-Datei gestartet wurden. Hier stehen alle, auch die von Hand oder mit anderen Werkzeugen angelegten. Ma\xDFgeblich ist immer die Datei auf dem Server." }),
    message && /* @__PURE__ */ jsx15("p", { className: `mb-2 whitespace-pre-line text-sm ${message.warn ? "text-red-400" : "opacity-90"}`, role: message.warn ? "alert" : "status", children: message.text }),
    creating ? /* @__PURE__ */ jsx15("div", { className: "mb-3 bg-black/20 p-2 panel", children: /* @__PURE__ */ jsx15(NewStack, { hostId, onClose: (text) => {
      setCreating(false);
      if (text) {
        note(text);
        setReload((n) => n + 1);
      }
    } }) }) : /* @__PURE__ */ jsx15("div", { className: "mb-2", children: /* @__PURE__ */ jsx15(Button, { small: true, onClick: () => setCreating(true), children: "Neuer Stack" }) }),
    checkError && /* @__PURE__ */ jsxs14("p", { className: "mb-2 text-xs text-amber-300", children: [
      "Ob Dateien seit dem letzten Start ge\xE4ndert wurden, lie\xDF sich nicht pr\xFCfen: ",
      checkError
    ] }),
    stacks.length === 0 && /* @__PURE__ */ jsx15("p", { className: "text-sm opacity-70", children: "Auf diesem Server l\xE4uft kein Compose-Projekt." }),
    /* @__PURE__ */ jsx15("ul", { className: "divide-y divide-white/5 text-sm", children: stacks.map((stack) => {
      const state = STATE[stack.state];
      const pending = stack.changes ? changesText(stack.changes) : "";
      const facts = [
        stack.total ? `${stack.running} von ${stack.total} Containern laufen` : "gerade keine Container",
        ...stack.working_dir && stack.source === "compose" ? [stack.working_dir] : [],
        usageText(usage2[stack.project], measured, stack.running)
      ].filter(Boolean);
      return /* @__PURE__ */ jsxs14("li", { className: "py-2", "data-testid": `stack-${stack.project}`, children: [
        /* @__PURE__ */ jsxs14("div", { className: "flex flex-wrap items-center gap-x-3 gap-y-1", children: [
          /* @__PURE__ */ jsxs14("div", { className: "min-w-0 flex-1 max-md:basis-full", children: [
            /* @__PURE__ */ jsxs14("p", { className: "flex flex-wrap items-center gap-1.5 break-words", children: [
              /* @__PURE__ */ jsx15("span", { className: "font-medium", children: stack.project }),
              /* @__PURE__ */ jsx15(Badge, { tone: state.tone, children: state.label }),
              stack.problems > 0 && /* @__PURE__ */ jsx15(Badge, { tone: "bad", children: stack.problems === 1 ? "1 Container mit Problemen" : `${stack.problems} Container mit Problemen` }),
              stack.is_self && /* @__PURE__ */ jsx15(Badge, { children: "Nodvard Deck" }),
              stack.source === "portainer" && /* @__PURE__ */ jsx15(Badge, { tone: "info", children: "Portainer" }),
              stack.adopted && /* @__PURE__ */ jsx15(Badge, { tone: "accent", children: "\xFCbernommen" }),
              stack.changed && /* @__PURE__ */ jsx15(Badge, { tone: "warn", children: "Datei ge\xE4ndert, noch nicht angewendet" })
            ] }),
            /* @__PURE__ */ jsx15("p", { className: "break-words text-xs opacity-60", children: facts.join(" \xB7 ") }),
            stack.containers.length > 0 && /* @__PURE__ */ jsxs14("p", { className: "break-words text-xs opacity-60", children: [
              "Container: ",
              membersText(stack.containers)
            ] }),
            stack.changed && /* @__PURE__ */ jsxs14("p", { className: "text-xs text-amber-300", children: [
              pending,
              ". \xDCbernommen wird das erst, wenn der Stack neu angewendet wird (docker compose up -d). Kommt das unerwartet, wurde der Stack vielleicht mit eigenen Umgebungsvariablen gestartet."
            ] }),
            stack.check_error && /* @__PURE__ */ jsx15("p", { className: "text-xs opacity-70", children: stack.check_error }),
            SOURCE_NOTE[stack.source] && /* @__PURE__ */ jsx15("p", { className: "text-xs opacity-70", children: SOURCE_NOTE[stack.source] }),
            stack.adoption_note && /* @__PURE__ */ jsx15("p", { className: "text-xs text-amber-300", children: stack.adoption_note })
          ] }),
          /* @__PURE__ */ jsxs14("div", { className: "flex shrink-0 flex-wrap gap-1.5", children: [
            stack.source === "compose" && /* @__PURE__ */ jsx15(Button, { small: true, pressed: open?.project === stack.project && open.panel === "file", onClick: () => void toggle(stack.project, "file"), children: "Datei ansehen" }),
            stack.adopted && !stack.is_self && /* @__PURE__ */ jsx15(Button, { small: true, pressed: open?.project === stack.project && open.panel === "edit", onClick: () => void toggle(stack.project, "edit"), children: "Bearbeiten" }),
            stack.source === "compose" && !stack.adopted && stack.total > 0 && /* @__PURE__ */ jsx15(Button, { small: true, pressed: open?.project === stack.project && open.panel === "adopt", onClick: () => void toggle(stack.project, "adopt"), children: stack.adoption_note ? "Neu \xFCbernehmen" : "\xDCbernehmen" }),
            stack.source === "portainer" && stack.total > 0 && !stack.is_self && /* @__PURE__ */ jsx15(Button, { small: true, pressed: open?.project === stack.project && open.panel === "portainer", onClick: () => void toggle(stack.project, "portainer"), children: "Von Portainer \xFCbernehmen" }),
            stack.adopted && VERBS.filter((verb) => verb.when(stack)).map((verb) => /* @__PURE__ */ jsx15(
              Button,
              {
                small: true,
                disabled: busy !== null,
                onClick: () => void act(stack, verb),
                ariaLabel: `${verb.label}: ${stack.project}`,
                children: busy === `${stack.project}:${verb.type}` ? "\u2026" : verb.label
              },
              verb.type
            )),
            (stack.adopted || stack.adoption_note) && /* @__PURE__ */ jsx15(Button, { small: true, onClick: () => void release(stack), children: "\xDCbernahme aufheben" }),
            stack.adopted && !stack.is_self && stack.total > 0 && /* @__PURE__ */ jsx15(Button, { small: true, pressed: open?.project === stack.project && open.panel === "update", onClick: () => void toggle(stack.project, "update"), children: "Update" }),
            stack.adopted && !stack.is_self && /* @__PURE__ */ jsx15(Button, { small: true, pressed: open?.project === stack.project && open.panel === "backups", onClick: () => void toggle(stack.project, "backups"), children: "Sicherungen" }),
            stack.adopted && !stack.is_self && /* @__PURE__ */ jsx15(
              Button,
              {
                small: true,
                variant: "danger",
                pressed: open?.project === stack.project && open.panel === "delete",
                onClick: () => void toggle(stack.project, "delete"),
                children: "L\xF6schen"
              }
            )
          ] })
        ] }),
        open?.project === stack.project && /* @__PURE__ */ jsx15("div", { className: "mt-2 bg-black/20 p-2 panel", children: open.panel === "file" ? /* @__PURE__ */ jsx15(StackFile, { hostId, project: stack.project }) : open.panel === "edit" ? /* @__PURE__ */ jsx15(
          StackEditor,
          {
            hostId,
            project: stack.project,
            onDirty: markDirty,
            onClose: (text) => {
              editorDirty.current = false;
              setOpen(null);
              if (text) {
                note(text);
                setReload((n) => n + 1);
              }
            }
          }
        ) : open.panel === "update" ? /* @__PURE__ */ jsx15(UpdatePanel2, { hostId, project: stack.project, onDone: (text, warn2) => {
          setOpen(null);
          note(text, warn2);
          setReload((n) => n + 1);
        } }) : open.panel === "portainer" ? /* @__PURE__ */ jsx15(PortainerPanel, { hostId, project: stack.project, onDone: (text, warn2) => {
          setOpen(null);
          note(text, warn2);
          setReload((n) => n + 1);
        } }) : open.panel === "backups" ? /* @__PURE__ */ jsx15(BackupsPanel, { hostId, project: stack.project, onDone: (text, warn2) => {
          setOpen(null);
          note(text, warn2);
          setReload((n) => n + 1);
        } }) : open.panel === "delete" ? /* @__PURE__ */ jsx15(DeletePanel, { hostId, project: stack.project, onDone: (text, warn2) => {
          setOpen(null);
          note(text, warn2);
          setReload((n) => n + 1);
        } }) : /* @__PURE__ */ jsx15(AdoptPanel, { hostId, project: stack.project, onDone: (text) => {
          setOpen(null);
          note(text);
          setReload((n) => n + 1);
        } }) })
      ] }, stack.project);
    }) })
  ] });
}
function StacksSection({ hostId, onHost, empty }) {
  return /* @__PURE__ */ jsx15(HostSection, { hostId, onHost, empty, children: (id) => /* @__PURE__ */ jsx15(StackList, { hostId: id }, id) });
}

// src/ToStack.tsx
import { useEffect as useEffect12, useState as useState13 } from "react";
import { Fragment as Fragment10, jsx as jsx16, jsxs as jsxs15 } from "react/jsx-runtime";
function ToStackPanel({ hostId, container, onDone }) {
  const [plan, setPlan] = useState13(null);
  const [error, setError] = useState13(null);
  const [failed, setFailed] = useState13(null);
  const [busy, setBusy] = useState13(false);
  const [reload, setReload] = useState13(0);
  const url = `/ext/docker/hosts/${encodeURIComponent(hostId)}/containers/${encodeURIComponent(container)}/to-stack`;
  useEffect12(() => {
    const controller = new AbortController();
    setPlan(null);
    setError(null);
    fetchJson(url, { signal: controller.signal }).then((body) => {
      if (!controller.signal.aborted) setPlan(body);
    }).catch((err) => {
      if (!controller.signal.aborted) setError(errorMessage(err));
    });
    return () => controller.abort();
  }, [url, reload]);
  async function convert(folder) {
    const risky = plan?.warnings.length ? ` Riskant: ${plan.warnings.join(" ")}` : "";
    const question = `\u201E${container}\u201C in einen Stack in ${folder} umwandeln? Der Container wird gestoppt und aus der neuen Compose-Datei neu erstellt; Volumes und Ordner bleiben. Klappt das, l\xF6scht Deck den alten Container; was nur in ihm lag und nicht in einem Volume oder Ordner, ist dann weg. Klappt es nicht, startet der alte wieder.${risky}`;
    if (!await deck().confirmDialog(question, { confirmLabel: "Umwandeln" })) return;
    setBusy(true);
    setFailed(null);
    let submitted = false;
    try {
      const proposal = await fetchJson(url, { method: "POST", headers: JSON_HEADERS, body: "{}" });
      submitted = true;
      const run = await settleAction(proposal);
      const action = await withResult(run.action);
      const outcome = describeActionOutcome(action);
      if (action.status === "proposed") onDone("Zum Umwandeln vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe \u201EAktionen\u201C.");
      else if (outcome.tone === "success") onDone(action.result?.output ?? `\u201E${container}\u201C ist jetzt ein Stack.`);
      else if (outcome.tone === "pending") onDone(run.text);
      else if (action.result) onDone([action.result.output, action.result.error ?? outcome.text].filter(Boolean).join("\n"), true);
      else setFailed(outcome.text);
    } catch (err) {
      setFailed(submitted ? `Das Ergebnis lie\xDF sich nicht abrufen (${errorMessage(err)}). Wie es ausging, steht unter \u201EAktionen\u201C.` : errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  if (error) return /* @__PURE__ */ jsx16(LoadError, { what: "Die Vorschau", error, onRetry: () => setReload((n) => n + 1), compact: true });
  if (!plan) return /* @__PURE__ */ jsx16("p", { className: "text-xs opacity-60", children: "Lese den Container \u2026" });
  const blocked = plan.refused.length > 0 || !plan.folder;
  return /* @__PURE__ */ jsxs15("div", { className: "mt-2 flex flex-col gap-2 text-xs", "data-testid": `to-stack-${container}`, children: [
    /* @__PURE__ */ jsxs15("p", { className: "opacity-70", children: [
      "Deck schreibt f\xFCr diesen Container eine Compose-Datei",
      plan.folder ? /* @__PURE__ */ jsxs15(Fragment10, { children: [
        " nach ",
        /* @__PURE__ */ jsx16("span", { className: "break-all font-mono", children: plan.folder })
      ] }) : null,
      ", stoppt ihn und startet ihn daraus neu. Volumes und Ordner bleiben dieselben; danach l\xE4sst er sich wie jeder Stack bearbeiten und aktualisieren. Den alten Container l\xF6scht Deck, sobald der neue l\xE4uft; was nur in ihm lag und nicht in einem Volume oder Ordner, geht dabei verloren."
    ] }),
    plan.compose && /* @__PURE__ */ jsx16("pre", { className: "max-h-64 overflow-auto rounded bg-black/60 p-2 font-mono text-[11px] leading-snug", "aria-label": "Compose-Datei des neuen Stacks", children: plan.compose }),
    plan.env_names.length > 0 && /* @__PURE__ */ jsxs15("p", { className: "opacity-70", children: [
      "Die Umgebungsvariablen (",
      plan.env_names.join(", "),
      ") kommen in die Datei container.env daneben, nur f\xFCr den Zugang lesbar. Startbefehl, Labels und Healthcheck stehen dagegen so in der Compose-Datei oben; auch sie ist auf dem Server nur f\xFCr den Zugang lesbar."
    ] }),
    plan.warnings.length > 0 && /* @__PURE__ */ jsxs15("p", { className: "text-amber-300", children: [
      "Riskant: ",
      plan.warnings.join(" "),
      " Die Freigabe braucht die hohe Stufe."
    ] }),
    plan.refused.length > 0 && /* @__PURE__ */ jsx16("ul", { className: "list-disc pl-4 text-red-400", role: "alert", children: plan.refused.map((why) => /* @__PURE__ */ jsx16("li", { className: "break-words", children: why }, why)) }),
    failed && /* @__PURE__ */ jsx16("p", { className: "break-words text-red-400", role: "alert", children: failed }),
    /* @__PURE__ */ jsx16("div", { children: /* @__PURE__ */ jsx16(Button, { small: true, variant: "primary", disabled: busy || blocked, onClick: () => void convert(plan.folder), children: busy ? "\u2026" : "Umwandeln" }) })
  ] });
}

// src/ContainersPage.tsx
import { jsx as jsx17, jsxs as jsxs16 } from "react/jsx-runtime";
var MEASURING_HINT = "CPU/RAM werden gemessen \u2013 das braucht je Server ein paar Sekunden.";
var MEM_UNKNOWN_HINT = "Der Server meldet keinen Speicherverbrauch je Container (auf dem Raspberry Pi ist die cgroup-Speicherabrechnung standardm\xE4\xDFig aus).";
var TONE_CLASS = {
  good: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  warn: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  danger: "bg-red-500/15 text-red-300 border-red-500/40",
  neutral: "bg-white/10 opacity-70"
};
var CARD_ROW = "max-md:flex max-md:flex-wrap max-md:items-center max-md:gap-x-3 max-md:gap-y-2 max-md:rounded-lg max-md:border max-md:border-white/10 max-md:bg-white/[0.03] max-md:p-3";
var CARD_LABEL = "max-md:before:mr-1 max-md:before:opacity-60 max-md:before:content-[attr(data-label)]";
var TOUCH = "max-md:px-3 max-md:py-2";
var STATE_LABEL = {
  running: "l\xE4uft",
  exited: "gestoppt",
  created: "erstellt",
  restarting: "startet neu",
  paused: "pausiert",
  removing: "wird entfernt",
  dead: "tot",
  error: "Fehler"
};
var STATE_FILTERS = [
  { value: "running", label: "L\xE4uft" },
  { value: "stopped", label: "Gestoppt oder pausiert" },
  { value: "problems", label: "Mit Problemen" }
];
var MANY_RESTARTS = 3;
var RECENT_START = /^(Up (Less than a second|\d+ seconds?|About a minute|\d+ minutes?)|Restarting)\b/;
function restartsAreAProblem(restarts, status2) {
  return (restarts ?? 0) >= MANY_RESTARTS && RECENT_START.test(status2);
}
var STOP_CODES = /* @__PURE__ */ new Set([0, 130, 137, 143]);
function crashed(status2) {
  const code = /^Exited \((\d+)\)/.exec(status2)?.[1];
  return code !== void 0 && !STOP_CODES.has(Number(code));
}
function inStateFilter(entry, filter, restarts = 0) {
  if (filter === "running") return entry.state === "running";
  if (filter === "stopped") return ["exited", "created", "paused"].includes(entry.state);
  if (filter === "problems") {
    return ["restarting", "dead", "removing", "error"].includes(entry.state) || /\(unhealthy\)/.test(entry.status) || crashed(entry.status) || restartsAreAProblem(restarts, entry.status);
  }
  return true;
}
var NO_STACK = "-";
function hostIdOf(entry) {
  return entry.host_id ?? (entry.id.endsWith(":__error__") ? entry.id.slice(0, -":__error__".length) : void 0);
}
function environmentText(env) {
  const limits = [`${env.cpus} CPU`, ...env.mem_total ? [`${formatBytes(env.mem_total)} RAM`] : []].join(" \xB7 ");
  if (env.virtualization === "lxc") {
    return {
      text: `im LXC-Container, Grenze ${limits}`,
      title: "Docker l\xE4uft hier in einem LXC-Container und sieht nur dessen Anteil. CPU und RAM der Container z\xE4hlen innerhalb dieser Grenze, nicht f\xFCr den ganzen Rechner darunter."
    };
  }
  if (env.virtualization === "vm") return { text: `VM mit ${limits}`, title: "Docker l\xE4uft hier in einer virtuellen Maschine; das sind ihre Werte." };
  if (env.virtualization === "container") return { text: `im Container, ${limits}`, title: "Docker l\xE4uft hier selbst in einem Container; das sind die Werte, die er sieht." };
  return { text: limits, title: "So viel CPU und RAM sieht Docker auf diesem Server." };
}
function FilterSelect({ label, all, value, options, onChange, testId }) {
  return /* @__PURE__ */ jsxs16(
    "select",
    {
      "aria-label": label,
      value: value ?? "",
      onChange: (e) => onChange(e.target.value || null),
      "data-testid": testId,
      className: "max-w-[12rem] px-2 py-1 text-sm rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]",
      children: [
        /* @__PURE__ */ jsx17("option", { value: "", children: all }),
        options.map(([v, text]) => /* @__PURE__ */ jsx17("option", { value: v, children: text }, v))
      ]
    }
  );
}
var DURATION_UNITS = {
  second: { one: "Sekunde", many: "Sekunden", about: "einer" },
  minute: { one: "Minute", many: "Minuten", about: "einer" },
  hour: { one: "Stunde", many: "Stunden", about: "einer" },
  day: { one: "Tag", many: "Tagen", about: "einem" },
  week: { one: "Woche", many: "Wochen", about: "einer" },
  month: { one: "Monat", many: "Monaten", about: "einem" },
  year: { one: "Jahr", many: "Jahren", about: "einem" }
};
function durationText(raw) {
  const text = raw.trim().toLowerCase();
  if (text === "less than a second") return "weniger als einer Sekunde";
  const about = /^about an? (second|minute|hour|day|week|month|year)$/.exec(text);
  if (about) return `etwa ${DURATION_UNITS[about[1]].about} ${DURATION_UNITS[about[1]].one}`;
  const counted = /^(\d+) (second|minute|hour|day|week|month|year)s?$/.exec(text);
  if (!counted) return null;
  const n = Number(counted[1]);
  const unit = DURATION_UNITS[counted[2]];
  return `${n} ${n === 1 ? unit.one : unit.many}`;
}
function statusText(status2) {
  const raw = (status2 ?? "").trim();
  if (!raw) return "";
  const health = (h) => !h ? "" : h === "healthy" ? " (gesund)" : h === "unhealthy" ? " (nicht gesund)" : h === "health: starting" ? " (wird gepr\xFCft)" : ` (${h})`;
  const up = /^Up (.+?)(?: \(((?:un)?healthy|health: starting)\))?(?: \(Paused\))?$/i.exec(raw);
  if (up) {
    const d = durationText(up[1]);
    if (d) return `L\xE4uft seit ${d}${health(up[2]?.toLowerCase())}${/\(Paused\)$/i.test(raw) ? " (pausiert)" : ""}`;
  }
  const ended = /^Exited \((-?\d+)\) (.+) ago$/i.exec(raw);
  if (ended) {
    const d = durationText(ended[2]);
    if (d) return `Beendet (Code ${ended[1]}) vor ${d}`;
  }
  const restarting = /^Restarting \((-?\d+)\) (.+) ago$/i.exec(raw);
  if (restarting) {
    const d = durationText(restarting[2]);
    if (d) return `Startet neu (Code ${restarting[1]}), zuletzt vor ${d}`;
  }
  const plain = { created: "Angelegt, noch nicht gestartet", paused: "Pausiert", dead: "Defekt", "removal in progress": "Wird entfernt" };
  return plain[raw.toLowerCase()] ?? raw;
}
var IMAGE_POLL_MS = 2e3;
function scopeImages(images, hostId) {
  if (!images || !hostId) return images;
  const prefix = `${hostId}:`;
  const only = (record) => Object.fromEntries(Object.entries(record ?? {}).filter(([key]) => key.startsWith(prefix)));
  return {
    ...images,
    data: only(images.data),
    hosts: Object.fromEntries(Object.entries(images.hosts ?? {}).filter(([id]) => id === hostId)),
    applying: only(images.applying),
    applied: only(images.applied)
  };
}
function summarizeImages(images, checking, operateServers = 0) {
  if (checking) return "Image-Updates: Die Image-Quellen werden gefragt \u2026";
  if (!images) return null;
  const results = Object.values(images.data ?? {});
  const hosts = Object.values(images.hosts ?? {});
  const failedHosts = hosts.filter((h) => h.error).length;
  const lastCheck = hosts.map((h) => h.checked_at).filter((t) => Boolean(t)).sort().pop();
  const applying = Object.keys(images.applying ?? {}).length;
  const applyingText = applying === 1 ? "1 Update wird eingespielt" : `${applying} Updates werden eingespielt`;
  if (!lastCheck && failedHosts === 0) return applying > 0 ? `Image-Updates: ${applyingText}` : null;
  const head = lastCheck ? `Image-Updates gepr\xFCft ${new Date(lastCheck).toLocaleString()}` : "Image-Updates";
  const notChecked = operateServers > 0 ? ` \xB7 ${operateServers} Server im Modus \u201ENur bedienen\u201C nicht gepr\xFCft` : "";
  const count = (status2) => results.filter((r) => r.status === status2).length;
  const update = count("update");
  const current = count("current");
  const local = count("local");
  const unknown = count("unknown");
  const stale = results.filter((r) => r.stale).length;
  if (results.length === 0) {
    return `${head} \xB7 ${failedHosts > 0 ? "Pr\xFCfung nicht m\xF6glich" : "keine laufenden Container"}${applying > 0 ? ` \xB7 ${applyingText}` : ""}${notChecked}`;
  }
  const parts = [];
  const open = update > 0 || unknown > 0 || stale > 0 || failedHosts > 0;
  if (!open && current > 0) {
    parts.push("alles aktuell");
  } else {
    if (update > 0) parts.push(`${update} mit Update`);
    if (current > 0) parts.push(`${current} aktuell`);
  }
  if (local > 0) parts.push(`${local} selbst gebaut`);
  if (unknown > 0) parts.push(`${unknown} nicht pr\xFCfbar`);
  if (stale > 0) parts.push(`${stale} mit \xE4lterer Antwort der Registry`);
  if (failedHosts > 0) parts.push(failedHosts === 1 ? "1 Server nicht erreichbar" : `${failedHosts} Server nicht erreichbar`);
  if (applying > 0) parts.push(applyingText);
  return `${head} \xB7 ${parts.join(" \xB7 ")}${notChecked}`;
}
function StaleNote({ result }) {
  if (!result.stale) return null;
  const at = result.registry_at ? ` Letzte Antwort der Registry: ${new Date(result.registry_at).toLocaleString()}.` : "";
  return /* @__PURE__ */ jsx17("span", { className: "mt-0.5 block text-[11px] opacity-60", title: `${result.reason ?? ""}${at}`, children: "alter Stand" });
}
function NeutralBadge({ label, reason }) {
  return /* @__PURE__ */ jsxs16("span", { title: reason ?? void 0, children: [
    /* @__PURE__ */ jsx17("span", { className: `rounded border px-1.5 py-0.5 text-xs ${TONE_CLASS.neutral}`, children: label }),
    reason && /* @__PURE__ */ jsx17("span", { className: "mt-0.5 block max-w-[15rem] whitespace-normal break-words text-[11px] opacity-60", children: reason })
  ] });
}
function ImageUpdateBadge({ result, checking = false, running = true, operateOnly = false }) {
  if (operateOnly) return /* @__PURE__ */ jsx17("span", { className: "text-xs opacity-40", title: OPERATE_NO_IMAGE_CHECK, children: "\u2013" });
  if (!result) {
    return checking ? /* @__PURE__ */ jsx17("span", { className: "text-xs opacity-60", children: "pr\xFCft \u2026" }) : /* @__PURE__ */ jsx17("span", { className: "text-xs opacity-40", title: running ? "Noch nicht gepr\xFCft." : "Gestoppte Container werden nicht gepr\xFCft.", children: "\u2013" });
  }
  if (result.status === "current") {
    return /* @__PURE__ */ jsxs16("span", { children: [
      /* @__PURE__ */ jsx17("span", { className: `rounded border px-1.5 py-0.5 text-xs ${TONE_CLASS.good}`, title: result.image, children: "aktuell" }),
      /* @__PURE__ */ jsx17(StaleNote, { result })
    ] });
  }
  if (result.status === "update") {
    const short = result.remote_digest?.replace("sha256:", "").slice(0, 12);
    return /* @__PURE__ */ jsxs16("span", { children: [
      /* @__PURE__ */ jsx17(
        "span",
        {
          className: `rounded border px-1.5 py-0.5 text-xs ${TONE_CLASS.warn}`,
          title: `${result.image}: Bei der Registry liegt eine neuere Version${short ? ` (${short})` : ""}.`,
          children: "Update verf\xFCgbar"
        }
      ),
      /* @__PURE__ */ jsx17(StaleNote, { result })
    ] });
  }
  return /* @__PURE__ */ jsx17(NeutralBadge, { label: result.status === "local" ? "selbst gebaut" : "nicht pr\xFCfbar", reason: result.reason });
}
function afterCheckText(action) {
  const check = action.result?.detail?.check;
  return typeof check?.text === "string" && check.text ? check.text : null;
}
var VERB_TEXT = {
  start: { label: "Starten", done: "gestartet" },
  stop: { label: "Stoppen", done: "gestoppt", confirm: "wirklich stoppen? Der Dienst ist danach nicht erreichbar." },
  restart: { label: "Neustart", done: "neu gestartet", confirm: "wirklich neu starten?" },
  remove: {
    label: "L\xF6schen",
    done: "gel\xF6scht",
    confirm: "wirklich l\xF6schen? Volumes und das Image bleiben, aber was nur im Container selbst lag, ist weg."
  }
};
function consoleHref(hostId, container) {
  return `/terminal?${new URLSearchParams({ host: hostId, scope: "container", ref: container }).toString()}`;
}
var REMOVABLE_STATES = /* @__PURE__ */ new Set(["exited", "created", "dead"]);
var PRUNE_ACTIONS = [
  { type: "docker.prune_images", label: "Verwaiste Images entfernen", confirm: "Images ohne Namen entfernen, die kein Container nutzt?" },
  { type: "docker.prune_unused_images", label: "Ungenutzte Images entfernen", confirm: "Alle Images ohne Container entfernen \u2013 auch benannte? Werden sie wieder gebraucht, l\xE4dt Docker sie neu herunter." },
  { type: "docker.prune_build_cache", label: "Build-Cache leeren", confirm: "Build-Cache leeren? Der n\xE4chste docker build auf diesem Server dauert dann deutlich l\xE4nger." }
];
function StoragePanel({ hostId }) {
  const unmountSignal = useUnmountSignal();
  const [data, setData] = useState14(null);
  const [error, setError] = useState14(null);
  const [busy, setBusy] = useState14(null);
  const [message, setMessage] = useState14(null);
  const [reload, setReload] = useState14(0);
  useEffect13(() => {
    let cancelled = false;
    setError(null);
    authedFetch(`/ext/docker/hosts/${hostId}/docker`).then(async (res) => {
      if (!res.ok) throw new Error(await errorText(res));
      return res.json();
    }).then((body) => {
      if (!cancelled) setData(body);
    }).catch((err) => {
      if (!cancelled) setError(err instanceof Error ? err.message : String(err));
    });
    return () => {
      cancelled = true;
    };
  }, [hostId, reload]);
  async function prune(action) {
    const ok = await deck().confirmDialog(action.confirm, { confirmLabel: action.label });
    if (!ok) return;
    setBusy(action.type);
    setMessage(null);
    try {
      setMessage(`${action.label}: ${await runHostAction(hostId, action.type, `${action.label} \xFCber die Docker-Seite.`, unmountSignal())}`);
      setReload((n) => n + 1);
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(null);
    }
  }
  if (error) return /* @__PURE__ */ jsxs16("p", { className: "text-xs text-red-400", children: [
    "Docker-Speicher nicht abrufbar: ",
    error
  ] });
  if (!data) return /* @__PURE__ */ jsx17("p", { className: "text-xs opacity-60", children: "Lade Docker-Speicher \u2026 (docker system df braucht ein paar Sekunden)" });
  const canRun = deck().hasPermission("hosts.execute");
  return /* @__PURE__ */ jsxs16("div", { className: "text-xs", "data-testid": `storage-${hostId}`, children: [
    /* @__PURE__ */ jsx17("div", { className: "mb-2 flex flex-wrap gap-2", children: data.disk.map((d) => /* @__PURE__ */ jsxs16("div", { className: "px-2.5 py-1.5 panel", children: [
      /* @__PURE__ */ jsxs16("span", { className: "block opacity-60", children: [
        d.label,
        d.total != null ? ` (${d.active ?? "?"}/${d.total} aktiv)` : ""
      ] }),
      /* @__PURE__ */ jsx17("span", { className: "text-sm font-medium", children: d.size != null ? formatBytes(d.size) : "?" }),
      d.reclaimable ? /* @__PURE__ */ jsxs16("span", { className: "ml-1.5 text-amber-300", children: [
        formatBytes(d.reclaimable),
        " frei machbar"
      ] }) : null
    ] }, d.type)) }),
    canRun && /* @__PURE__ */ jsx17("div", { className: "mb-2 flex flex-wrap gap-1.5", children: PRUNE_ACTIONS.map((a) => /* @__PURE__ */ jsx17(
      "button",
      {
        type: "button",
        disabled: busy !== null,
        onClick: () => void prune(a),
        className: "px-2 py-1 disabled:opacity-40 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
        children: busy === a.type ? "\u2026" : a.label
      },
      a.type
    )) }),
    message && /* @__PURE__ */ jsx17("p", { className: "mb-2 opacity-90", role: "status", children: message }),
    data.images.length > 0 && /* @__PURE__ */ jsxs16("table", { className: "w-full", children: [
      /* @__PURE__ */ jsx17("thead", { children: /* @__PURE__ */ jsxs16("tr", { className: "border-b border-white/10 text-left opacity-60", children: [
        /* @__PURE__ */ jsx17("th", { className: "py-1", children: "Image" }),
        /* @__PURE__ */ jsx17("th", { className: "py-1", children: "Gr\xF6\xDFe" }),
        /* @__PURE__ */ jsx17("th", { className: "py-1", children: "Erstellt" }),
        /* @__PURE__ */ jsx17("th", { className: "py-1", children: "Genutzt" })
      ] }) }),
      /* @__PURE__ */ jsx17("tbody", { className: "divide-y divide-white/5", children: data.images.map((img) => /* @__PURE__ */ jsxs16("tr", { children: [
        /* @__PURE__ */ jsx17("td", { className: "py-1 break-words", children: img.name ?? /* @__PURE__ */ jsxs16("span", { className: "opacity-60", children: [
          "ohne Namen (",
          img.id,
          ")"
        ] }) }),
        /* @__PURE__ */ jsx17("td", { className: "py-1 whitespace-nowrap", children: img.size != null ? formatBytes(img.size) : "?" }),
        /* @__PURE__ */ jsx17("td", { className: "py-1 opacity-70", children: img.created ?? "" }),
        /* @__PURE__ */ jsx17("td", { className: `py-1 ${img.in_use === false ? "text-amber-300" : "opacity-70"}`, children: img.in_use == null ? "?" : img.in_use ? "ja" : "nein" })
      ] }, img.id)) })
    ] })
  ] });
}
var RESTART_LABEL = {
  no: "nie",
  always: "immer",
  "unless-stopped": "au\xDFer manuell gestoppt",
  "on-failure": "bei Fehler"
};
var HEALTH_LABEL = { healthy: "gesund", unhealthy: "ungesund", starting: "startet" };
function DetailsPanel({ entry, onChanged }) {
  const [data, setData] = useState14(null);
  const [error, setError] = useState14(null);
  const [toStack, setToStack] = useState14(false);
  useEffect13(() => {
    let cancelled = false;
    authedFetch(`/ext/docker/containers/${entry.host_id}/${encodeURIComponent(entry.container)}/inspect`).then(async (res) => {
      if (!res.ok) throw new Error(await errorText(res));
      return res.json();
    }).then((body) => {
      if (!cancelled) setData(body);
    }).catch((err) => {
      if (!cancelled) setError(err instanceof Error ? err.message : String(err));
    });
    return () => {
      cancelled = true;
    };
  }, [entry.host_id, entry.container]);
  if (error) return /* @__PURE__ */ jsxs16("p", { className: "text-xs text-red-400", children: [
    "Details nicht abrufbar: ",
    error
  ] });
  if (!data) return /* @__PURE__ */ jsx17("p", { className: "text-xs opacity-60", children: "Lade Details \u2026" });
  const d = data;
  const since = d.running ? d.started_at : d.finished_at;
  return /* @__PURE__ */ jsxs16("div", { className: "text-xs", "data-testid": `inspect-${entry.id}`, children: [
    /* @__PURE__ */ jsxs16("dl", { className: "grid grid-cols-2 gap-x-6 gap-y-1 opacity-85 sm:grid-cols-4", children: [
      /* @__PURE__ */ jsxs16("div", { children: [
        /* @__PURE__ */ jsx17("dt", { className: "opacity-60", children: "Image" }),
        /* @__PURE__ */ jsxs16("dd", { className: "break-words", children: [
          d.image ?? "?",
          d.image_id ? ` (${d.image_id})` : ""
        ] })
      ] }),
      /* @__PURE__ */ jsxs16("div", { children: [
        /* @__PURE__ */ jsx17("dt", { className: "opacity-60", children: d.running ? "L\xE4uft seit" : "Beendet" }),
        /* @__PURE__ */ jsxs16("dd", { children: [
          since ? new Date(since).toLocaleString() : "\u2013",
          !d.running && d.exit_code != null ? ` \xB7 Exit ${d.exit_code}` : "",
          d.oom_killed ? " \xB7 Speicher voll (OOM)" : ""
        ] })
      ] }),
      /* @__PURE__ */ jsxs16("div", { children: [
        /* @__PURE__ */ jsx17("dt", { className: "opacity-60", children: "Neustart-Regel" }),
        /* @__PURE__ */ jsxs16("dd", { children: [
          RESTART_LABEL[d.restart_policy] ?? d.restart_policy,
          d.restart_count ? ` \xB7 ${d.restart_count}\xD7 neu gestartet` : ""
        ] })
      ] }),
      /* @__PURE__ */ jsxs16("div", { children: [
        /* @__PURE__ */ jsx17("dt", { className: "opacity-60", children: "Zustand" }),
        /* @__PURE__ */ jsxs16("dd", { children: [
          d.health ? HEALTH_LABEL[d.health] ?? d.health : "kein Healthcheck",
          d.privileged ? " \xB7 privilegiert" : ""
        ] })
      ] }),
      d.compose && /* @__PURE__ */ jsxs16("div", { children: [
        /* @__PURE__ */ jsx17("dt", { className: "opacity-60", children: "Compose" }),
        /* @__PURE__ */ jsxs16("dd", { className: "break-words", children: [
          d.compose.project,
          d.compose.service ? ` / ${d.compose.service}` : "",
          d.compose.working_dir ? ` \xB7 ${d.compose.working_dir}` : ""
        ] })
      ] }),
      d.memory_limit ? /* @__PURE__ */ jsxs16("div", { children: [
        /* @__PURE__ */ jsx17("dt", { className: "opacity-60", children: "RAM-Limit" }),
        /* @__PURE__ */ jsx17("dd", { children: formatBytes(d.memory_limit) })
      ] }) : null
    ] }),
    /* @__PURE__ */ jsx17("p", { className: "mt-2 mb-0.5 font-medium opacity-60", children: "Ports" }),
    /* @__PURE__ */ jsx17("p", { className: "opacity-85", children: d.ports.length === 0 ? d.network_mode === "host" ? "Host-Netz (alle Ports direkt)" : "keine" : d.ports.map((p) => `${p.published.length ? p.published.join(", ") : "intern"} \u2192 ${p.container}`).join(" \xB7 ") }),
    /* @__PURE__ */ jsx17("p", { className: "mt-2 mb-0.5 font-medium opacity-60", children: "Speicher" }),
    /* @__PURE__ */ jsxs16("ul", { className: "space-y-0.5 opacity-85", children: [
      d.mounts.length === 0 && /* @__PURE__ */ jsx17("li", { children: "keine Mounts" }),
      d.mounts.map((m) => /* @__PURE__ */ jsxs16("li", { className: "break-words", children: [
        /* @__PURE__ */ jsx17("span", { className: "opacity-60", children: m.type === "volume" ? "Volume" : m.type === "bind" ? "Ordner" : m.type }),
        " ",
        m.source,
        " \u2192 ",
        m.destination,
        m.read_only ? " (nur lesen)" : ""
      ] }, `${m.destination}`))
    ] }),
    /* @__PURE__ */ jsx17("p", { className: "mt-2 mb-0.5 font-medium opacity-60", children: "Netze" }),
    /* @__PURE__ */ jsx17("p", { className: "opacity-85", children: d.networks.map((n) => `${n.name}${n.ip ? ` (${n.ip})` : ""}`).join(" \xB7 ") || "keine" }),
    /* @__PURE__ */ jsxs16("p", { className: "mt-2 mb-0.5 font-medium opacity-60", children: [
      "Umgebungsvariablen (",
      d.env_keys.length,
      ")"
    ] }),
    /* @__PURE__ */ jsx17("p", { className: "break-words opacity-70", title: "Werte werden bewusst nicht angezeigt \u2013 dort stehen oft Passw\xF6rter.", children: d.env_keys.join(", ") || "keine" }),
    !d.compose && !entry.operate_only && entry.host_id && entry.container && onChanged && deck().hasPermission("hosts.execute") && /* @__PURE__ */ jsxs16("div", { className: "mt-2", children: [
      /* @__PURE__ */ jsx17(Button, { small: true, pressed: toStack, onClick: () => setToStack((open) => !open), children: "In Stack umwandeln" }),
      toStack && /* @__PURE__ */ jsx17(ToStackPanel, { hostId: entry.host_id, container: entry.container, onDone: (text, warn2) => {
        setToStack(false);
        onChanged(text, warn2);
      } })
    ] })
  ] });
}
function UrlPanel({ entry, onSaved, onClose }) {
  const current = entry.url_custom ? entry.url ?? "" : "";
  const [value, setValue] = useState14(current);
  const [busy, setBusy] = useState14(false);
  const [error, setError] = useState14(null);
  const save = async (url) => {
    setBusy(true);
    setError(null);
    try {
      const res = await authedFetch(`/ext/docker/containers/${encodeURIComponent(entry.host_id)}/${encodeURIComponent(entry.container)}/url`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url })
      });
      if (!res.ok) throw new Error(await errorText(res));
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  return /* @__PURE__ */ jsxs16("form", { className: "space-y-2 text-xs", onSubmit: (e) => {
    e.preventDefault();
    if (value.trim() !== current) void save(value.trim() || null);
  }, children: [
    /* @__PURE__ */ jsxs16("label", { className: "block", children: [
      /* @__PURE__ */ jsx17("span", { className: "mb-1 block opacity-70", children: "Adresse, die \u201E\xD6ffnen\u201C und die Kachel aufrufen" }),
      /* @__PURE__ */ jsx17(
        "input",
        {
          type: "url",
          value,
          onChange: (e) => setValue(e.target.value),
          placeholder: entry.url_detected ?? "http://192.0.2.10:8080",
          className: "w-full rounded border border-white/10 bg-black/30 px-2 py-1.5 sm:max-w-md"
        }
      )
    ] }),
    /* @__PURE__ */ jsx17("p", { className: "opacity-60", children: entry.url_detected ? `Erkannt: ${entry.url_detected}` : "Nodvard Deck hat f\xFCr diesen Container keine Weboberfl\xE4che erkannt." }),
    error && /* @__PURE__ */ jsx17("p", { role: "alert", className: "text-red-400", children: error }),
    /* @__PURE__ */ jsxs16("div", { className: "flex flex-wrap gap-1.5", children: [
      /* @__PURE__ */ jsx17("button", { type: "submit", disabled: busy || value.trim() === current, className: `rounded bg-emerald-500/20 px-2 py-1 text-emerald-300 hover:bg-emerald-500/30 disabled:opacity-40 ${TOUCH}`, children: busy ? "\u2026" : "Speichern" }),
      entry.url_custom && /* @__PURE__ */ jsx17("button", { type: "button", disabled: busy, onClick: () => void save(null), className: `rounded bg-white/10 px-2 py-1 hover:bg-white/20 disabled:opacity-40 ${TOUCH}`, children: "Erkannte nehmen" }),
      /* @__PURE__ */ jsx17("button", { type: "button", onClick: onClose, className: `rounded px-2 py-1 opacity-70 hover:opacity-100 ${TOUCH}`, children: "Abbrechen" })
    ] })
  ] });
}
function OpenLink({ url }) {
  const href = safeHref(url);
  if (!href) return null;
  return /* @__PURE__ */ jsx17("a", { href, target: "_blank", rel: "noreferrer", className: "px-1 py-1 text-xs opacity-70 hover:opacity-100 hover:underline max-md:px-2 max-md:py-2", children: "\xD6ffnen" });
}
function NoContainers() {
  const tag = useSettingText("docker", "docker_host_tag", "docker");
  return /* @__PURE__ */ jsx17(
    EmptyState,
    {
      icon: "package",
      title: "Noch kein Server mit Docker",
      text: `Lege unter Server & Zug\xE4nge einen Server an und gib ihm die Markierung \u201E${tag}\u201C. L\xE4uft dort Docker, erscheinen seine Container hier von selbst. Die Markierung l\xE4sst sich in den Einstellungen des Moduls \xE4ndern.`,
      action: /* @__PURE__ */ jsxs16("div", { className: "flex flex-wrap justify-center gap-2", children: [
        /* @__PURE__ */ jsx17(SettingsLink, { to: "/settings/hosts", permission: "hosts.write", children: "Server & Zug\xE4nge \xF6ffnen" }),
        /* @__PURE__ */ jsx17(SettingsLink, { to: "/settings/extensions/docker", permission: "extensions.manage", variant: "secondary", children: "Moduleinstellungen" })
      ] })
    }
  );
}
var TABS = [{ kind: "stacks", label: "Stacks" }, ...RESOURCE_TABS, { kind: "registries", label: "Registries" }];
function ContainersPage() {
  const [urlParams, updateUrl] = useUrlParams();
  const resourcesAllowed = deck().hasPermission("hosts.execute");
  const tab = resourcesAllowed && TABS.find((t) => t.kind === urlParams.get("tab"))?.kind || null;
  const tabs = resourcesAllowed && /* @__PURE__ */ jsx17("nav", { className: "mb-4 flex flex-wrap gap-1.5", "aria-label": "Bereiche der Docker-Seite", children: [{ kind: null, label: "Container" }, ...TABS].map((t) => /* @__PURE__ */ jsx17(
    Button,
    {
      small: true,
      variant: tab === t.kind ? "primary" : "secondary",
      pressed: tab === t.kind,
      onClick: () => updateUrl({ tab: t.kind }),
      children: t.label
    },
    t.label
  )) });
  if (!tab) return /* @__PURE__ */ jsx17(ContainerList, { tabs });
  return /* @__PURE__ */ jsxs16("div", { className: "mx-auto w-full max-w-7xl p-4 sm:p-6", children: [
    /* @__PURE__ */ jsx17("h2", { className: "mb-4 font-semibold text-xl tracking-tight", children: "Docker" }),
    tabs,
    tab === "stacks" ? /* @__PURE__ */ jsx17(StacksSection, { hostId: urlParams.get("host") || null, onHost: (id) => updateUrl({ host: id }), empty: /* @__PURE__ */ jsx17(NoContainers, {}) }) : tab === "registries" ? /* @__PURE__ */ jsx17(RegistriesSection, { hostId: urlParams.get("host") || null, onHost: (id) => updateUrl({ host: id }), empty: /* @__PURE__ */ jsx17(NoContainers, {}) }) : /* @__PURE__ */ jsx17(ResourcesSection, { kind: tab, hostId: urlParams.get("host") || null, onHost: (id) => updateUrl({ host: id }), empty: /* @__PURE__ */ jsx17(NoContainers, {}) })
  ] });
}
function ContainerList({ tabs }) {
  const unmountSignal = useUnmountSignal();
  const [services, setServices] = useState14(null);
  const [error, setError] = useState14(null);
  const [message, setMessage] = useState14(null);
  const [pending, setPending] = useState14(null);
  const [urlParams, updateUrl] = useUrlParams();
  const logsFor = urlParams.get("logs") || null;
  const setLogsFor = (id) => updateUrl({ logs: id });
  const hostFilter = urlParams.get("host") || null;
  const stackFilter = urlParams.get("stack") || null;
  const stateFilter = urlParams.get("state") || null;
  const [detailsFor, setDetailsFor] = useState14(null);
  const [urlFor, setUrlFor] = useState14(null);
  const [updateFor, setUpdateFor] = useState14(null);
  const [storageFor, setStorageFor] = useState14(null);
  const [query, setQuery] = useState14("");
  const [stats, setStats] = useState14({});
  const [environments, setEnvironments] = useState14({});
  const [restarts, setRestarts] = useState14({});
  const [statsLoaded, setStatsLoaded] = useState14(false);
  const [images, setImages] = useState14(null);
  const [imageMessage, setImageMessage] = useState14(null);
  const loadImages = useCallback3(async (signal) => {
    try {
      const res = await authedFetch("/ext/docker/image-updates", { signal });
      if (!res.ok) return;
      const body = await res.json();
      if (!signal?.aborted) setImages(body);
    } catch {
    }
  }, []);
  const reloadImages = useCallback3(() => void loadImages(unmountSignal()), [loadImages, unmountSignal]);
  const load = useCallback3(() => {
    void loadImages(unmountSignal());
    fetchJson("/ext/docker/widgets/matrix").then((body) => {
      setServices(body.data);
      setError(null);
    }).catch((err) => setError(errorMessage(err)));
  }, [loadImages, unmountSignal]);
  useEffect13(() => {
    load();
    const interval = setInterval(load, 3e4);
    return () => clearInterval(interval);
  }, [load]);
  const visibleImages = scopeImages(images, hostFilter);
  const imagesChecking = Object.values(visibleImages?.hosts ?? {}).some((h) => h.checking);
  const imagesBusy = imagesChecking || Object.keys(visibleImages?.applying ?? {}).length > 0;
  useEffect13(() => {
    if (!imagesBusy) return;
    const controller = new AbortController();
    const interval = setInterval(() => void loadImages(controller.signal), IMAGE_POLL_MS);
    return () => {
      clearInterval(interval);
      controller.abort();
    };
  }, [imagesBusy, loadImages]);
  useEffect13(() => {
    let cancelled = false;
    const loadStats = () => authedFetch("/ext/docker/stats").then((res) => res.ok ? res.json() : { data: {}, hosts: void 0, restarts: void 0 }).then((body) => {
      if (cancelled) return;
      setStats(body.data);
      if (body.hosts) setEnvironments(body.hosts);
      if (body.restarts) setRestarts(body.restarts);
      setStatsLoaded(true);
    }).catch(() => !cancelled && setStatsLoaded(true));
    void loadStats();
    const interval = setInterval(loadStats, 3e4);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);
  async function trigger(entry, verb) {
    const text = VERB_TEXT[verb];
    if (text.confirm) {
      const extra = entry.is_self && verb === "restart" ? " Das ist Nodvard Deck selbst \u2013 die Oberfl\xE4che ist kurz weg." : verb === "remove" && entry.compose_project ? ` Er geh\xF6rt zum Compose-Projekt \u201E${entry.compose_project}\u201C: Beim n\xE4chsten \u201Edocker compose up\u201C (auch beim Einspielen eines Updates) legt Docker ihn wieder an.` : "";
      const ok = await deck().confirmDialog(`"${entry.name}" ${text.confirm}${extra}`, {
        danger: verb === "stop" || verb === "remove",
        confirmLabel: text.label
      });
      if (!ok) return;
    }
    setPending(`${entry.id}:${verb}`);
    setMessage(null);
    try {
      const { action, approved } = await runAction(`/hosts/${entry.host_id}/actions/container.${verb}`, {
        method: "POST",
        body: JSON.stringify({ payload: { container: entry.container }, reason: `\xDCber die Docker-Seite ausgel\xF6st (${verb}).` })
      }, { signal: unmountSignal() });
      if (!approved && action.status === "proposed") {
        setMessage(`"${entry.name}": ${text.label} vorgeschlagen \u2013 Freigabe durch einen Admin n\xF6tig, siehe "Aktionen".`);
        return;
      }
      const status2 = action.status ?? "?";
      const failure = action.result?.error;
      const check = afterCheckText(action);
      setMessage(
        status2 === "succeeded" ? `"${entry.name}" ${text.done}.${check ? ` Pr\xFCfung danach: ${check}` : ""}` : `"${entry.name}": ${text.label} -> ${ACTION_STATUS_LABEL[status2] ?? status2}${failure ? ` (${failure})` : ""}.`
      );
      load();
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPending(null);
    }
  }
  async function startImageCheck(force) {
    setImageMessage(null);
    const params = new URLSearchParams();
    if (hostFilter) params.set("host_id", hostFilter);
    if (force) params.set("force", "true");
    const query2 = params.toString();
    try {
      const res = await authedFetch(`/ext/docker/image-updates/check${query2 ? `?${query2}` : ""}`, { method: "POST", signal: unmountSignal() });
      if (!res.ok) throw new Error(await errorText(res));
      setImages(await res.json());
    } catch (err) {
      setImageMessage(`Image-Pr\xFCfung: ${err instanceof Error ? err.message : String(err)}`);
    }
  }
  async function restartStack(entry) {
    const project = entry.compose_project;
    const members = (services ?? []).filter((s) => s.host_id === entry.host_id && s.compose_project === project);
    const self = members.some((s) => s.is_self);
    const ok = await deck().confirmDialog(
      `Stack "${project}" neu starten (${members.map((s) => s.name).join(", ")})? Die Dienste sind kurz nicht erreichbar.${self ? " Darin l\xE4uft Nodvard Deck selbst \u2013 die Oberfl\xE4che ist kurz weg." : ""}`,
      { confirmLabel: "Neu starten" }
    );
    if (!ok) return;
    setPending(`stack:${entry.host_id}:${project}`);
    setMessage(null);
    try {
      const { action, approved } = await runAction(`/hosts/${entry.host_id}/actions/docker.stack_restart`, {
        method: "POST",
        body: JSON.stringify({ payload: { project }, reason: `Stack "${project}" \xFCber die Docker-Seite neu gestartet.` })
      }, { signal: unmountSignal() });
      const status2 = action.status ?? "?";
      if (approved) {
        setMessage(
          status2 === "succeeded" ? action.result?.output ?? "Stack neu gestartet." : `Stack "${project}": ${ACTION_STATUS_LABEL[status2] ?? status2}${action.result?.error ? ` (${action.result.error})` : ""}`
        );
      } else if (status2 === "proposed") {
        setMessage(`Stack "${project}": vorgeschlagen \u2013 Freigabe durch einen Admin n\xF6tig, siehe "Aktionen".`);
      } else {
        setMessage(`Stack "${project}": ${ACTION_STATUS_LABEL[status2] ?? status2}`);
      }
      load();
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPending(null);
    }
  }
  if (!services && !error) return /* @__PURE__ */ jsxs16("div", { className: "p-4 sm:p-6", children: [
    tabs,
    /* @__PURE__ */ jsx17("p", { className: "text-sm opacity-60", children: "Lade \u2026" })
  ] });
  if (error && !services) return /* @__PURE__ */ jsxs16("div", { className: "p-4 sm:p-6", children: [
    tabs,
    /* @__PURE__ */ jsx17(LoadError, { what: "Die Liste der Container", error, onRetry: load })
  ] });
  const scoped = (services ?? []).filter((s) => !hostFilter || hostIdOf(s) === hostFilter);
  const operateServers = new Set(scoped.filter((s) => s.operate_only).map((s) => hostIdOf(s))).size;
  const imageSummary = summarizeImages(visibleImages, imagesChecking, operateServers);
  const needle = query.trim().toLowerCase();
  const byHost = /* @__PURE__ */ new Map();
  for (const s of services ?? []) {
    if (needle && !s.name.toLowerCase().includes(needle) && !(s.compose_project ?? "").toLowerCase().includes(needle)) continue;
    if (hostFilter && hostIdOf(s) !== hostFilter) continue;
    if (s.state !== "error") {
      if (stackFilter && (stackFilter === NO_STACK ? Boolean(s.compose_project) : s.compose_project !== stackFilter)) continue;
      if (stateFilter && !inStateFilter(s, stateFilter, restarts[s.id])) continue;
    }
    const list = byHost.get(s.host) ?? [];
    list.push(s);
    byHost.set(s.host, list);
  }
  const hostOptions = /* @__PURE__ */ new Map();
  for (const s of services ?? []) {
    const id = hostIdOf(s);
    if (id && !hostOptions.has(id)) hostOptions.set(id, s.host);
  }
  if (hostFilter && !hostOptions.has(hostFilter)) hostOptions.set(hostFilter, "dieser Server");
  const stacks = new Set((services ?? []).filter((s) => !hostFilter || hostIdOf(s) === hostFilter).flatMap((s) => s.compose_project ? [s.compose_project] : []));
  if (stackFilter && stackFilter !== NO_STACK) stacks.add(stackFilter);
  const stackOptions = [...[...stacks].sort().map((p) => [p, p]), [NO_STACK, "Ohne Stack"]];
  const filtered = Boolean(hostFilter || stackFilter || stateFilter);
  const onlyOperate = scoped.length > 0 && scoped.every((s) => s.operate_only);
  return /* @__PURE__ */ jsxs16("div", { className: "mx-auto w-full max-w-7xl p-4 sm:p-6", children: [
    /* @__PURE__ */ jsxs16("div", { className: "mb-4 flex flex-wrap items-center gap-3", children: [
      /* @__PURE__ */ jsx17("h2", { className: "font-semibold text-xl tracking-tight", children: "Docker" }),
      /* @__PURE__ */ jsx17(
        "input",
        {
          value: query,
          onChange: (e) => setQuery(e.target.value),
          placeholder: "Container suchen \u2026",
          "aria-label": "Container suchen",
          className: "w-56 px-2 py-1 text-sm rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]"
        }
      ),
      /* @__PURE__ */ jsx17("button", { type: "button", onClick: load, className: "px-2 py-1 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition", children: "Aktualisieren" }),
      deck().hasPermission("hosts.execute") && (services ?? []).length > 0 && !onlyOperate && /* @__PURE__ */ jsx17(
        "button",
        {
          type: "button",
          disabled: imagesChecking,
          onClick: () => void startImageCheck(false),
          title: "Fragt bei den Registries nach, ob es neuere Images gibt. Es wird nichts heruntergeladen oder neu gestartet.",
          className: "px-2 py-1 text-xs disabled:opacity-40 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
          children: imagesChecking ? "Pr\xFCfe Images \u2026" : "Image-Updates pr\xFCfen"
        }
      )
    ] }),
    tabs,
    (services ?? []).length > 0 && /* @__PURE__ */ jsxs16("div", { className: "mb-4 flex flex-wrap items-center gap-2", "data-testid": "filters", children: [
      (hostOptions.size > 1 || hostFilter) && /* @__PURE__ */ jsx17(
        FilterSelect,
        {
          label: "Server",
          all: "Alle Server",
          value: hostFilter,
          options: [...hostOptions],
          testId: "host-filter",
          onChange: (v) => updateUrl({ host: v, stack: null })
        }
      ),
      (stacks.size > 0 || stackFilter) && /* @__PURE__ */ jsx17(FilterSelect, { label: "Stack", all: "Alle Stacks", value: stackFilter, options: stackOptions, onChange: (v) => updateUrl({ stack: v }) }),
      /* @__PURE__ */ jsx17(
        FilterSelect,
        {
          label: "Zustand",
          all: "Alle Zust\xE4nde",
          value: stateFilter,
          options: STATE_FILTERS.map((f) => [f.value, f.label]),
          onChange: (v) => updateUrl({ state: v })
        }
      ),
      filtered && /* @__PURE__ */ jsx17(
        "button",
        {
          type: "button",
          onClick: () => updateUrl({ host: null, stack: null, state: null }),
          className: "px-2 py-1 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
          children: "Filter entfernen"
        }
      )
    ] }),
    error && /* @__PURE__ */ jsx17(LoadError, { what: "Der aktuelle Stand", error, onRetry: load, hint: "Die Tabelle zeigt den letzten bekannten Stand." }),
    message && /* @__PURE__ */ jsx17("p", { className: "mb-3 text-sm opacity-80", children: message }),
    (imageSummary || imageMessage) && /* @__PURE__ */ jsxs16("p", { className: "mb-3 flex flex-wrap items-center gap-x-3 text-xs opacity-80", "aria-live": "polite", "data-testid": "image-summary", children: [
      imageSummary && /* @__PURE__ */ jsx17("span", { children: imageSummary }),
      imageMessage && /* @__PURE__ */ jsx17("span", { className: "text-red-400", children: imageMessage }),
      !imagesChecking && imageSummary && deck().hasPermission("hosts.execute") && /* @__PURE__ */ jsx17(
        "button",
        {
          type: "button",
          onClick: () => void startImageCheck(true),
          title: "Fragt ohne Zwischenspeicher noch einmal bei den Image-Quellen (Registries) nach. Sonst werden ihre Antworten 6 Stunden wiederverwendet. Docker Hub begrenzt die Zahl der Abfragen.",
          className: "underline opacity-70 hover:opacity-100",
          children: "Neu abfragen"
        }
      )
    ] }),
    (services ?? []).length === 0 && /* @__PURE__ */ jsx17(NoContainers, {}),
    (services ?? []).length > 0 && byHost.size === 0 && /* @__PURE__ */ jsx17("p", { className: "mb-3 text-sm opacity-70", "data-testid": "no-match", children: needle ? "Kein Container passt zu deiner Suche." : "F\xFCr diesen Filter gibt es keine Container." }),
    [...byHost.entries()].map(([host, entries]) => /* @__PURE__ */ jsxs16("section", { className: "mb-6 overflow-x-auto", children: [
      /* @__PURE__ */ jsxs16("div", { className: "mb-2 flex flex-wrap items-center gap-3", children: [
        /* @__PURE__ */ jsx17("h3", { className: "text-sm font-medium opacity-60", children: host }),
        entries[0]?.host_id && environments[entries[0].host_id] && /* @__PURE__ */ jsx17("span", { className: "text-xs opacity-60", title: environmentText(environments[entries[0].host_id]).title, "data-testid": `environment-${entries[0].host_id}`, children: environmentText(environments[entries[0].host_id]).text }),
        entries[0]?.operate_only && entries[0].host_id && /* @__PURE__ */ jsx17(OperateBadge, { hostId: entries[0].host_id }),
        entries[0]?.host_id && entries[0].state !== "error" && !entries[0].operate_only && deck().hasPermission("hosts.execute") && /* @__PURE__ */ jsx17(
          "button",
          {
            type: "button",
            onClick: () => setStorageFor(storageFor === entries[0].host_id ? null : entries[0].host_id),
            "aria-expanded": storageFor === entries[0].host_id,
            className: "px-2 py-0.5 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
            children: "Docker-Speicher"
          }
        ),
        entries[0]?.host_id && !entries[0].operate_only && deck().hasPermission("hosts.execute") && /* @__PURE__ */ jsx17(
          BulkProposeButton,
          {
            hostId: entries[0].host_id,
            hostName: host,
            candidates: entries.filter((s) => s.host_id && s.container && s.state !== "error" && (s.state === "running" || s.state === "restarting") && !s.is_self && images?.data[s.id]?.status === "update" && images.data[s.id].apply?.mode !== "none").map((s) => ({ hostId: s.host_id, container: s.container, name: s.name })),
            onDone: reloadImages
          }
        )
      ] }),
      entries[0]?.host_id && images?.hosts[entries[0].host_id]?.error && /* @__PURE__ */ jsxs16("p", { className: "mb-2 text-xs text-red-400", "data-testid": `image-error-${entries[0].host_id}`, children: [
        "Image-Pr\xFCfung: ",
        images.hosts[entries[0].host_id].error
      ] }),
      storageFor && storageFor === entries[0]?.host_id && /* @__PURE__ */ jsx17("div", { className: "mb-3 bg-black/20 p-2 panel", children: /* @__PURE__ */ jsx17(StoragePanel, { hostId: storageFor }) }),
      /* @__PURE__ */ jsxs16("table", { role: "table", className: "w-full text-sm max-md:block", children: [
        /* @__PURE__ */ jsx17("thead", { className: "max-md:hidden", children: /* @__PURE__ */ jsxs16("tr", { className: "border-b border-white/10 text-left text-xs opacity-60", children: [
          /* @__PURE__ */ jsx17("th", { className: "py-1 w-1/4", children: "Container" }),
          /* @__PURE__ */ jsx17("th", { className: "py-1 whitespace-nowrap", children: "Zustand" }),
          /* @__PURE__ */ jsx17("th", { className: "py-1", children: "Status" }),
          /* @__PURE__ */ jsx17("th", { className: "py-1 whitespace-nowrap", children: "Image-Update" }),
          /* @__PURE__ */ jsx17("th", { className: "py-1 whitespace-nowrap", children: "CPU" }),
          /* @__PURE__ */ jsx17("th", { className: "py-1 whitespace-nowrap", children: "RAM" }),
          /* @__PURE__ */ jsx17("th", { className: "py-1", children: "Aktionen" })
        ] }) }),
        /* @__PURE__ */ jsx17("tbody", { role: "rowgroup", className: "divide-y divide-white/5 max-md:block max-md:space-y-2 max-md:divide-y-0", children: entries.map((s) => {
          const manageable = Boolean(s.host_id && s.container) && s.state !== "error";
          const isRunning = s.state === "running" || s.state === "restarting";
          const logsOpen = logsFor === s.id;
          const busy = (verb) => pending === `${s.id}:${verb}`;
          const imageShown = Boolean(images?.data[s.id]) || Boolean(s.host_id && images?.hosts[s.host_id]?.checking);
          return /* @__PURE__ */ jsxs16(Fragment11, { children: [
            /* @__PURE__ */ jsxs16("tr", { role: "row", className: CARD_ROW, "data-testid": `row-${s.id}`, children: [
              /* @__PURE__ */ jsxs16("td", { role: "cell", className: "py-1.5 break-words max-md:w-full max-md:py-0 max-md:text-base", children: [
                s.name,
                s.is_self && /* @__PURE__ */ jsx17("span", { className: "ml-1.5 rounded bg-white/10 px-1 text-[10px] opacity-70", children: "Nodvard Deck" }),
                s.compose_project && s.operate_only && /* @__PURE__ */ jsx17("span", { className: "ml-1.5 rounded bg-sky-500/15 px-1 text-[10px] text-sky-300", title: "Docker-Compose-Projekt", children: s.compose_project }),
                s.compose_project && !s.operate_only && /* @__PURE__ */ jsx17(
                  "button",
                  {
                    type: "button",
                    disabled: !deck().hasPermission("hosts.execute") || pending === `stack:${s.host_id}:${s.compose_project}`,
                    onClick: () => void restartStack(s),
                    title: "Docker-Compose-Projekt \u2013 klicken: ganzen Stack neu starten",
                    className: "ml-1.5 rounded bg-sky-500/15 px-1 text-[10px] text-sky-300 hover:bg-sky-500/25 disabled:cursor-default disabled:hover:bg-sky-500/15",
                    children: s.compose_project
                  }
                ),
                s.image && /* @__PURE__ */ jsx17("span", { className: "block text-xs opacity-50", children: s.image })
              ] }),
              /* @__PURE__ */ jsx17("td", { role: "cell", className: "py-1.5 max-md:py-0", children: /* @__PURE__ */ jsx17("span", { className: `rounded border px-1.5 py-0.5 text-xs ${TONE_CLASS[s.tone] ?? TONE_CLASS.neutral}`, children: STATE_LABEL[s.state] ?? s.state }) }),
              /* @__PURE__ */ jsxs16("td", { role: "cell", className: "py-1.5 break-words opacity-70 max-md:min-w-0 max-md:flex-1 max-md:py-0 max-md:text-xs", title: s.status !== statusText(s.status) ? s.status : void 0, children: [
                statusText(s.status),
                restarts[s.id] > 0 && /* @__PURE__ */ jsxs16(
                  "span",
                  {
                    className: `ml-1 whitespace-nowrap ${restartsAreAProblem(restarts[s.id], s.status) ? "text-amber-300" : ""}`,
                    "data-testid": `restarts-${s.id}`,
                    title: "So oft hat Docker den Container nach seinem Ende von selbst wieder gestartet.",
                    children: [
                      "\xB7 ",
                      restarts[s.id],
                      "\xD7 neu gestartet"
                    ]
                  }
                )
              ] }),
              /* @__PURE__ */ jsxs16("td", { role: "cell", className: `py-1.5 text-xs max-md:w-full max-md:py-0 ${imageShown ? "" : "max-md:hidden"}`, children: [
                /* @__PURE__ */ jsx17("div", { "data-testid": `image-${s.id}`, children: manageable && /* @__PURE__ */ jsx17(
                  ImageUpdateBadge,
                  {
                    result: images?.data[s.id],
                    checking: Boolean(s.host_id && images?.hosts[s.host_id]?.checking),
                    running: isRunning,
                    operateOnly: s.operate_only
                  }
                ) }),
                manageable && /* @__PURE__ */ jsx17(
                  ImageUpdateAction,
                  {
                    entryId: s.id,
                    offer: isRunning && !s.is_self && !s.operate_only && images?.data[s.id]?.status === "update" && deck().hasPermission("hosts.execute"),
                    apply: images?.data[s.id]?.apply,
                    applying: images?.applying?.[s.id],
                    applied: images?.applied?.[s.id],
                    open: updateFor === s.id,
                    onToggle: () => setUpdateFor(updateFor === s.id ? null : s.id)
                  }
                )
              ] }),
              /* @__PURE__ */ jsx17("td", { role: "cell", "data-label": "CPU", className: `py-1.5 whitespace-nowrap text-xs opacity-80 max-md:py-0 ${CARD_LABEL}`, "data-testid": `cpu-${s.id}`, children: !statsLoaded ? /* @__PURE__ */ jsx17("span", { title: MEASURING_HINT, children: "\u2026" }) : stats[s.id]?.cpu_percent != null ? `${stats[s.id].cpu_percent.toFixed(1)} %` : "\u2013" }),
              /* @__PURE__ */ jsx17("td", { role: "cell", "data-label": "RAM", className: `py-1.5 whitespace-nowrap text-xs opacity-80 max-md:py-0 ${CARD_LABEL}`, "data-testid": `mem-${s.id}`, children: !statsLoaded ? /* @__PURE__ */ jsx17("span", { title: MEASURING_HINT, children: "\u2026" }) : stats[s.id] ? stats[s.id].mem_used != null ? formatBytes(stats[s.id].mem_used) : /* @__PURE__ */ jsx17("span", { title: MEM_UNKNOWN_HINT, children: "n. v." }) : "\u2013" }),
              /* @__PURE__ */ jsx17("td", { role: "cell", className: "py-1.5 max-md:w-full max-md:py-0", children: /* @__PURE__ */ jsxs16("div", { className: "flex flex-wrap gap-1.5", children: [
                manageable && !isRunning && /* @__PURE__ */ jsx17(
                  "button",
                  {
                    type: "button",
                    disabled: busy("start"),
                    onClick: () => void trigger(s, "start"),
                    className: `rounded bg-emerald-500/20 px-2 py-1 text-xs text-emerald-300 hover:bg-emerald-500/30 disabled:opacity-40 ${TOUCH}`,
                    children: busy("start") ? "\u2026" : "Starten"
                  }
                ),
                manageable && REMOVABLE_STATES.has(s.state) && !s.is_self && !s.operate_only && /* @__PURE__ */ jsx17(
                  "button",
                  {
                    type: "button",
                    disabled: busy("remove"),
                    onClick: () => void trigger(s, "remove"),
                    className: `rounded px-2 py-1 text-xs text-red-300 hover:bg-red-500/20 disabled:opacity-40 ${TOUCH}`,
                    children: busy("remove") ? "\u2026" : "L\xF6schen"
                  }
                ),
                manageable && isRunning && /* @__PURE__ */ jsx17(
                  "button",
                  {
                    type: "button",
                    disabled: busy("restart"),
                    onClick: () => void trigger(s, "restart"),
                    className: `px-2 py-1 text-xs disabled:opacity-40 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition ${TOUCH}`,
                    children: busy("restart") ? "\u2026" : "Neustart"
                  }
                ),
                manageable && isRunning && /* @__PURE__ */ jsx17(
                  "button",
                  {
                    type: "button",
                    disabled: busy("stop") || s.is_self,
                    onClick: () => void trigger(s, "stop"),
                    title: s.is_self ? "Das ist Nodvard Deck selbst \u2013 Stoppen nur direkt auf dem Server." : void 0,
                    className: `rounded bg-red-500/20 px-2 py-1 text-xs text-red-300 hover:bg-red-500/30 disabled:opacity-40 ${TOUCH}`,
                    children: busy("stop") ? "\u2026" : "Stoppen"
                  }
                ),
                manageable && /* @__PURE__ */ jsx17(
                  "button",
                  {
                    type: "button",
                    onClick: () => setDetailsFor(detailsFor === s.id ? null : s.id),
                    "aria-expanded": detailsFor === s.id,
                    className: `rounded px-2 py-1 text-xs hover:bg-white/20 ${TOUCH} ${detailsFor === s.id ? "bg-white/20" : "bg-white/10"}`,
                    children: "Details"
                  }
                ),
                manageable && isRunning && !s.is_self && !s.operate_only && s.host_id && s.container && deck().hasPermission("hosts.execute") && // Shell im Container über das Terminal des Kerns (scope/ref, siehe shell.py).
                /* @__PURE__ */ jsx17(
                  "a",
                  {
                    href: consoleHref(s.host_id, s.container),
                    className: `rounded bg-white/10 px-2 py-1 text-xs hover:bg-white/20 ${TOUCH}`,
                    children: "Konsole"
                  }
                ),
                manageable && /* @__PURE__ */ jsx17(
                  "button",
                  {
                    type: "button",
                    onClick: () => setLogsFor(logsOpen ? null : s.id),
                    "aria-expanded": logsOpen,
                    className: `rounded px-2 py-1 text-xs hover:bg-white/20 ${TOUCH} ${logsOpen ? "bg-white/20" : "bg-white/10"}`,
                    children: "Logs"
                  }
                ),
                /* @__PURE__ */ jsx17(OpenLink, { url: s.url }),
                manageable && s.host_id && s.container && deck().hasPermission("apps.write") && /* @__PURE__ */ jsx17(
                  "button",
                  {
                    type: "button",
                    onClick: () => setUrlFor(urlFor === s.id ? null : s.id),
                    "aria-expanded": urlFor === s.id,
                    title: s.url_custom ? "Eigene Adresse \u2013 hier \xE4ndern oder die erkannte wieder nehmen." : "Adresse f\xFCr \u201E\xD6ffnen\u201C selbst festlegen.",
                    className: `px-1 py-1 text-xs opacity-70 hover:opacity-100 hover:underline max-md:px-2 max-md:py-2`,
                    children: s.url_custom ? "Adresse (eigene)" : "Adresse"
                  }
                )
              ] }) })
            ] }),
            urlFor === s.id && s.host_id && s.container && /* @__PURE__ */ jsx17("tr", { role: "row", className: "max-md:block", children: /* @__PURE__ */ jsx17("td", { role: "cell", colSpan: 7, className: "bg-black/20 p-2 max-md:block", children: /* @__PURE__ */ jsx17(UrlPanel, { entry: s, onSaved: load, onClose: () => setUrlFor(null) }) }) }),
            detailsFor === s.id && /* @__PURE__ */ jsx17("tr", { role: "row", className: "max-md:block", children: /* @__PURE__ */ jsx17("td", { role: "cell", colSpan: 7, className: "bg-black/20 p-2 max-md:block", children: /* @__PURE__ */ jsx17(DetailsPanel, { entry: s, onChanged: (text) => {
              setDetailsFor(null);
              setMessage(text);
              load();
            } }) }) }),
            updateFor === s.id && s.host_id && s.container && /* @__PURE__ */ jsx17("tr", { role: "row", className: "max-md:block", children: /* @__PURE__ */ jsx17("td", { role: "cell", colSpan: 7, className: "bg-black/20 p-2 max-md:block", children: /* @__PURE__ */ jsx17(
              UpdatePanel,
              {
                hostId: s.host_id,
                container: s.container,
                applying: images?.applying?.[s.id],
                onStarted: reloadImages,
                onFinished: load,
                onClose: () => setUpdateFor(null)
              }
            ) }) }),
            logsOpen && /* @__PURE__ */ jsx17("tr", { role: "row", className: "max-md:block", children: /* @__PURE__ */ jsx17("td", { role: "cell", colSpan: 7, className: "bg-black/20 p-2 max-md:block", children: /* @__PURE__ */ jsx17(LogPanel, { entry: s, onClose: () => setLogsFor(null) }) }) })
          ] }, s.id);
        }) })
      ] })
    ] }, host))
  ] });
}
var ServiceMatrixPage = ContainersPage;
export {
  ContainersPage,
  ImageUpdateBadge,
  MANY_RESTARTS,
  STATE_FILTERS,
  STATE_LABEL,
  ServiceMatrixPage,
  afterCheckText,
  consoleHref,
  environmentText,
  inStateFilter,
  restartsAreAProblem,
  scopeImages,
  statusText,
  summarizeImages
};
