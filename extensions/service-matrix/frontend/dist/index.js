// src/ServiceMatrixPage.tsx
import { Fragment as Fragment4, useCallback as useCallback2, useEffect as useEffect5, useMemo as useMemo2, useRef as useRef2, useState as useState5 } from "react";

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
async function errorText(res) {
  const body = await res.json().catch(() => ({}));
  return errorFromBody(body, res.status);
}

// ../../_shared/frontend/src/actions.ts
var RUNNING_IN_BACKGROUND = "L\xE4uft im Hintergrund \u2013 das Ergebnis steht unter \u201EAktionen\u201C.";
function isActionRunning(status) {
  return status === "approved" || status === "executing";
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
function EmptyState({ icon, title, text, action }) {
  return /* @__PURE__ */ jsxs("div", { className: "panel flex flex-col items-center px-6 py-14 text-center", children: [
    /* @__PURE__ */ jsx("span", { className: "mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-white/[0.06] text-[var(--color-accent)]", children: /* @__PURE__ */ jsx(Icon, { name: icon, size: 22 }) }),
    /* @__PURE__ */ jsx("p", { className: "text-sm font-medium", children: title }),
    text && /* @__PURE__ */ jsx("p", { className: "mt-1 max-w-md text-sm text-white/50", children: text }),
    action && /* @__PURE__ */ jsx("div", { className: "mt-5", children: action })
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

// src/BulkPropose.tsx
import { useState as useState3 } from "react";
import { Fragment as Fragment2, jsx as jsx3, jsxs as jsxs2 } from "react/jsx-runtime";
var ACTIONS_PATH = "/actions";
var BUTTON = "px-2 py-0.5 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition disabled:opacity-40";
var isDatabaseWarning = (w) => w.startsWith("Datenbank");
function endpoint(c) {
  return `/ext/service-matrix/containers/${c.hostId}/${encodeURIComponent(c.container)}/image-update`;
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
  return /* @__PURE__ */ jsxs2(Fragment2, { children: [
    (n >= 2 || progress) && /* @__PURE__ */ jsx3(
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
    (result || error) && /* @__PURE__ */ jsxs2("p", { className: "basis-full text-xs", role: "status", "data-testid": `bulk-result-${hostId}`, children: [
      error && /* @__PURE__ */ jsxs2("span", { className: "text-red-400", children: [
        "Fehler: ",
        error
      ] }),
      result && (result.created > 0 ? /* @__PURE__ */ jsxs2("span", { className: "text-emerald-300", children: [
        result.created === 1 ? "1 Vorschlag" : `${result.created} Vorschl\xE4ge`,
        " angelegt \u2013 freigeben unter \u201E",
        /* @__PURE__ */ jsx3("a", { href: ACTIONS_PATH, className: "underline", children: "Aktionen" }),
        "\u201C"
      ] }) : /* @__PURE__ */ jsx3("span", { className: "text-amber-300", children: "Keine Vorschl\xE4ge angelegt." })),
      result && result.skipped.length > 0 && /* @__PURE__ */ jsxs2("span", { className: "block break-words opacity-80", children: [
        "\xDCbersprungen: ",
        result.skipped.map((s) => `${s.name} (${s.reason})`).join("; ")
      ] })
    ] })
  ] });
}

// src/ImageUpdateApply.tsx
import { useEffect as useEffect4, useState as useState4 } from "react";
import { Fragment as Fragment3, jsx as jsx4, jsxs as jsxs3 } from "react/jsx-runtime";
var PHASE_TEXT = {
  start: "startet \u2026",
  pull: "l\xE4dt Image \u2026",
  up: "erstellt Container neu \u2026",
  verify: "pr\xFCft Start \u2026"
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
    parts.push(/* @__PURE__ */ jsxs3("span", { className: "block text-amber-300", role: "status", children: [
      "Update l\xE4uft \u2013 ",
      PHASE_TEXT[applying.phase] ?? PHASE_TEXT.start
    ] }, "run"));
  } else {
    if (applied) {
      const at = timeOf(applied.finished_at);
      parts.push(
        applied.ok ? /* @__PURE__ */ jsxs3("span", { className: "block text-emerald-300", title: applied.summary, children: [
          "\u2713 eingespielt ",
          at
        ] }, "done") : /* @__PURE__ */ jsxs3("span", { className: "block text-red-300", title: applied.summary, children: [
          "Update fehlgeschlagen ",
          at
        ] }, "failed")
      );
    }
    if (offer && apply?.mode === "none") {
      parts.push(/* @__PURE__ */ jsx4("span", { className: "mt-0.5 block max-w-[15rem] whitespace-normal break-words text-[11px] opacity-60", title: apply.why ?? void 0, children: apply.why }, "why"));
    } else if (offer) {
      parts.push(
        /* @__PURE__ */ jsx4(
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
  return /* @__PURE__ */ jsx4("div", { className: "mt-1", "data-testid": `image-action-${entryId}`, children: parts });
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
  const base = `/ext/service-matrix/containers/${hostId}/${encodeURIComponent(container)}/image-update`;
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
      const status = outcome.action.status;
      if (!approvedHere && status === "proposed") {
        setRun({ state: "proposed" });
        return;
      }
      if (status === "succeeded") {
        const output = outcome.action.result?.output ?? null;
        setRun({ state: "done", ok: true, text: `Fertig: ${(output ?? "").split("\n")[0] || "eingespielt"}`, output });
      } else if (status === "failed") {
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
  return /* @__PURE__ */ jsxs3("div", { className: "sticky left-0 max-w-[calc(100vw-3rem)] text-xs sm:max-w-none", "data-testid": `update-panel-${hostId}:${container}`, children: [
    error && /* @__PURE__ */ jsxs3("p", { className: "text-red-400", children: [
      "\xDCbersicht nicht abrufbar: ",
      error
    ] }),
    !plan && !error && /* @__PURE__ */ jsx4("p", { className: "opacity-60", children: "Lade \xDCbersicht \u2026" }),
    plan && !plan.ok && /* @__PURE__ */ jsxs3("div", { children: [
      /* @__PURE__ */ jsx4("p", { className: "opacity-90", children: plan.reason }),
      /* @__PURE__ */ jsx4("button", { type: "button", onClick: onClose, className: `mt-2 ${BUTTON2}`, children: "Schlie\xDFen" })
    ] }),
    plan?.ok && /* @__PURE__ */ jsxs3("div", { className: "flex flex-col gap-2", children: [
      /* @__PURE__ */ jsxs3("p", { className: "text-sm font-medium", children: [
        "Update f\xFCr \u201E",
        plan.container,
        "\u201C einspielen"
      ] }),
      /* @__PURE__ */ jsxs3("dl", { className: "grid gap-x-6 gap-y-1 opacity-85 sm:grid-cols-2", children: [
        /* @__PURE__ */ jsxs3("div", { children: [
          /* @__PURE__ */ jsx4("dt", { className: "opacity-60", children: "Image" }),
          /* @__PURE__ */ jsx4("dd", { className: "break-words", children: plan.image })
        ] }),
        /* @__PURE__ */ jsxs3("div", { children: [
          /* @__PURE__ */ jsx4("dt", { className: "opacity-60", children: "Version" }),
          /* @__PURE__ */ jsxs3("dd", { children: [
            "Image-ID ",
            plan.current_short,
            " \xB7 neue Version bei der Registry (Pr\xFCfsumme ",
            plan.remote_short || "?",
            ")",
            plan.registry_at ? `, Stand ${new Date(plan.registry_at).toLocaleString()}` : ""
          ] })
        ] }),
        /* @__PURE__ */ jsxs3("div", { children: [
          /* @__PURE__ */ jsx4("dt", { className: "opacity-60", children: "Compose" }),
          /* @__PURE__ */ jsxs3("dd", { className: "break-words", children: [
            plan.project,
            "/",
            plan.service
          ] })
        ] }),
        plan.affected.length > 1 && /* @__PURE__ */ jsxs3("div", { children: [
          /* @__PURE__ */ jsx4("dt", { className: "opacity-60", children: "Betrifft" }),
          /* @__PURE__ */ jsx4("dd", { className: "break-words", children: plan.affected.join(", ") })
        ] })
      ] }),
      /* @__PURE__ */ jsxs3("div", { className: "rounded border border-amber-500/40 bg-amber-500/10 p-2 text-amber-200", children: [
        /* @__PURE__ */ jsx4("p", { children: DOWNTIME_TEXT }),
        plan.warnings.length > 0 && /* @__PURE__ */ jsx4("ul", { className: "mt-1 list-disc space-y-0.5 pl-4", children: plan.warnings.map((w) => /* @__PURE__ */ jsx4("li", { className: w.startsWith("Datenbank") ? "font-medium text-red-300" : void 0, children: w }, w)) })
      ] }),
      /* @__PURE__ */ jsxs3("details", { open: true, children: [
        /* @__PURE__ */ jsx4("summary", { className: "cursor-pointer opacity-70", children: "Befehl auf dem Server" }),
        /* @__PURE__ */ jsx4("pre", { className: "mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-black/60 p-2 font-mono text-[11px]", children: plan.command })
      ] }),
      /* @__PURE__ */ jsxs3("details", { children: [
        /* @__PURE__ */ jsx4("summary", { className: "cursor-pointer opacity-70", children: "Zur\xFCck zur alten Version" }),
        /* @__PURE__ */ jsx4("pre", { className: "mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-black/60 p-2 font-mono text-[11px]", children: plan.rollback })
      ] }),
      run.state === "idle" && /* @__PURE__ */ jsxs3("div", { className: "flex flex-col gap-2 sm:flex-row sm:items-center", children: [
        deck().hasPermission(`actions.approve:${plan.risk}`) ? /* @__PURE__ */ jsx4(
          "button",
          {
            type: "button",
            onClick: () => void start(plan),
            className: `rounded px-3 py-1.5 text-xs font-medium ${plan.risk === "high" ? "bg-red-500/80 hover:bg-red-500" : "bg-[var(--color-accent)] hover:opacity-90"}`,
            children: "Jetzt einspielen"
          }
        ) : /* @__PURE__ */ jsxs3(Fragment3, { children: [
          /* @__PURE__ */ jsx4("button", { type: "button", onClick: () => void start(plan), className: BUTTON2, children: "Vorschlagen" }),
          /* @__PURE__ */ jsx4("span", { className: "opacity-70", children: "Ein Admin muss unter \u201EAktionen\u201C freigeben." })
        ] }),
        /* @__PURE__ */ jsx4("button", { type: "button", onClick: onClose, className: BUTTON2, children: "Abbrechen" })
      ] }),
      run.state === "posting" && /* @__PURE__ */ jsxs3("p", { className: "text-amber-300", role: "status", children: [
        "L\xE4uft \u2026 ",
        applying ? PHASE_TEXT[applying.phase] : "wird gestartet \u2026"
      ] }),
      run.state === "proposed" && /* @__PURE__ */ jsx4("p", { role: "status", children: "Vorgeschlagen \u2013 Freigabe durch einen Admin n\xF6tig, siehe \u201EAktionen\u201C." }),
      run.state === "done" && /* @__PURE__ */ jsxs3("div", { role: "status", children: [
        /* @__PURE__ */ jsx4("p", { className: run.ok ? "text-emerald-300" : "text-red-300", children: run.text }),
        run.output && (run.ok ? /* @__PURE__ */ jsxs3("details", { className: "mt-1", children: [
          /* @__PURE__ */ jsx4("summary", { className: "cursor-pointer opacity-70", children: "Protokoll und R\xFCckweg" }),
          /* @__PURE__ */ jsx4("pre", { className: "mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-black/60 p-2 font-mono text-[11px]", children: run.output })
        ] }) : /* @__PURE__ */ jsx4("pre", { className: "mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-black/60 p-2 font-mono text-[11px]", children: run.output }))
      ] }),
      run.state === "error" && /* @__PURE__ */ jsxs3("div", { role: "alert", children: [
        /* @__PURE__ */ jsx4("p", { className: "text-red-300", children: run.message }),
        run.conflict && /* @__PURE__ */ jsx4("button", { type: "button", onClick: () => setReload((n) => n + 1), className: `mt-2 ${BUTTON2}`, children: "Neu laden" })
      ] }),
      (run.state === "proposed" || run.state === "done" || run.state === "error") && /* @__PURE__ */ jsx4("div", { children: /* @__PURE__ */ jsx4("button", { type: "button", onClick: onClose, className: BUTTON2, children: "Schlie\xDFen" }) })
    ] })
  ] });
}

// src/ServiceMatrixPage.tsx
import { jsx as jsx5, jsxs as jsxs4 } from "react/jsx-runtime";
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
function statusText(status) {
  const raw = (status ?? "").trim();
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
function summarizeImages(images, checking) {
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
  const count = (status) => results.filter((r) => r.status === status).length;
  const update = count("update");
  const current = count("current");
  const local = count("local");
  const unknown = count("unknown");
  const stale = results.filter((r) => r.stale).length;
  if (results.length === 0) return `${head} \xB7 ${failedHosts > 0 ? "Pr\xFCfung nicht m\xF6glich" : "keine laufenden Container"}${applying > 0 ? ` \xB7 ${applyingText}` : ""}`;
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
  return `${head} \xB7 ${parts.join(" \xB7 ")}`;
}
function StaleNote({ result }) {
  if (!result.stale) return null;
  const at = result.registry_at ? ` Letzte Antwort der Registry: ${new Date(result.registry_at).toLocaleString()}.` : "";
  return /* @__PURE__ */ jsx5("span", { className: "mt-0.5 block text-[11px] opacity-60", title: `${result.reason ?? ""}${at}`, children: "alter Stand" });
}
function NeutralBadge({ label, reason }) {
  return /* @__PURE__ */ jsxs4("span", { title: reason ?? void 0, children: [
    /* @__PURE__ */ jsx5("span", { className: `rounded border px-1.5 py-0.5 text-xs ${TONE_CLASS.neutral}`, children: label }),
    reason && /* @__PURE__ */ jsx5("span", { className: "mt-0.5 block max-w-[15rem] whitespace-normal break-words text-[11px] opacity-60", children: reason })
  ] });
}
function ImageUpdateBadge({ result, checking = false, running = true }) {
  if (!result) {
    return checking ? /* @__PURE__ */ jsx5("span", { className: "text-xs opacity-60", children: "pr\xFCft \u2026" }) : /* @__PURE__ */ jsx5("span", { className: "text-xs opacity-40", title: running ? "Noch nicht gepr\xFCft." : "Gestoppte Container werden nicht gepr\xFCft.", children: "\u2013" });
  }
  if (result.status === "current") {
    return /* @__PURE__ */ jsxs4("span", { children: [
      /* @__PURE__ */ jsx5("span", { className: `rounded border px-1.5 py-0.5 text-xs ${TONE_CLASS.good}`, title: result.image, children: "aktuell" }),
      /* @__PURE__ */ jsx5(StaleNote, { result })
    ] });
  }
  if (result.status === "update") {
    const short = result.remote_digest?.replace("sha256:", "").slice(0, 12);
    return /* @__PURE__ */ jsxs4("span", { children: [
      /* @__PURE__ */ jsx5(
        "span",
        {
          className: `rounded border px-1.5 py-0.5 text-xs ${TONE_CLASS.warn}`,
          title: `${result.image}: Bei der Registry liegt eine neuere Version${short ? ` (${short})` : ""}.`,
          children: "Update verf\xFCgbar"
        }
      ),
      /* @__PURE__ */ jsx5(StaleNote, { result })
    ] });
  }
  return /* @__PURE__ */ jsx5(NeutralBadge, { label: result.status === "local" ? "selbst gebaut" : "nicht pr\xFCfbar", reason: result.reason });
}
var VERB_TEXT = {
  start: { label: "Starten", done: "gestartet" },
  stop: { label: "Stoppen", done: "gestoppt", confirm: "wirklich stoppen? Der Dienst ist danach nicht erreichbar." },
  restart: { label: "Neustart", done: "neu gestartet", confirm: "wirklich neu starten?" }
};
var TAIL_OPTIONS = [100, 200, 500, 2e3];
var MAX_LINES = 5e3;
var ANSI_RE = /\x1b\[[0-9;?]*[ -/]*[@-~]/g;
async function runHostAction(hostId, actionType, reason, signal) {
  const { action, approved } = await runAction(`/hosts/${hostId}/actions/${actionType}`, { method: "POST", body: JSON.stringify({ payload: {}, reason }) }, { signal });
  const status = action.status ?? "?";
  if (!approved) {
    if (status === "proposed") return `vorgeschlagen \u2013 Freigabe durch einen Admin n\xF6tig, siehe "Aktionen".`;
    return action.result?.output ?? ACTION_STATUS_LABEL[status] ?? status;
  }
  if (status === "succeeded") return action.result?.output ?? "erledigt";
  return `${ACTION_STATUS_LABEL[status] ?? status}${action.result?.error ? ` (${action.result.error})` : ""}`;
}
var PRUNE_ACTIONS = [
  { type: "docker.prune_images", label: "Verwaiste Images entfernen", confirm: "Images ohne Namen entfernen, die kein Container nutzt?" },
  { type: "docker.prune_unused_images", label: "Ungenutzte Images entfernen", confirm: "Alle Images ohne Container entfernen \u2013 auch benannte? Werden sie wieder gebraucht, l\xE4dt Docker sie neu herunter." },
  { type: "docker.prune_build_cache", label: "Build-Cache leeren", confirm: "Build-Cache leeren? Der n\xE4chste docker build auf diesem Server dauert dann deutlich l\xE4nger." }
];
function StoragePanel({ hostId }) {
  const unmountSignal = useUnmountSignal();
  const [data, setData] = useState5(null);
  const [error, setError] = useState5(null);
  const [busy, setBusy] = useState5(null);
  const [message, setMessage] = useState5(null);
  const [reload, setReload] = useState5(0);
  useEffect5(() => {
    let cancelled = false;
    setError(null);
    authedFetch(`/ext/service-matrix/hosts/${hostId}/docker`).then(async (res) => {
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
      setMessage(`${action.label}: ${await runHostAction(hostId, action.type, `${action.label} \xFCber die Service-Matrix.`, unmountSignal())}`);
      setReload((n) => n + 1);
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(null);
    }
  }
  if (error) return /* @__PURE__ */ jsxs4("p", { className: "text-xs text-red-400", children: [
    "Docker-Speicher nicht abrufbar: ",
    error
  ] });
  if (!data) return /* @__PURE__ */ jsx5("p", { className: "text-xs opacity-60", children: "Lade Docker-Speicher \u2026 (docker system df braucht ein paar Sekunden)" });
  const canRun = deck().hasPermission("hosts.execute");
  return /* @__PURE__ */ jsxs4("div", { className: "text-xs", "data-testid": `storage-${hostId}`, children: [
    /* @__PURE__ */ jsx5("div", { className: "mb-2 flex flex-wrap gap-2", children: data.disk.map((d) => /* @__PURE__ */ jsxs4("div", { className: "px-2.5 py-1.5 panel", children: [
      /* @__PURE__ */ jsxs4("span", { className: "block opacity-60", children: [
        d.label,
        d.total != null ? ` (${d.active ?? "?"}/${d.total} aktiv)` : ""
      ] }),
      /* @__PURE__ */ jsx5("span", { className: "text-sm font-medium", children: d.size != null ? formatBytes(d.size) : "?" }),
      d.reclaimable ? /* @__PURE__ */ jsxs4("span", { className: "ml-1.5 text-amber-300", children: [
        formatBytes(d.reclaimable),
        " frei machbar"
      ] }) : null
    ] }, d.type)) }),
    canRun && /* @__PURE__ */ jsx5("div", { className: "mb-2 flex flex-wrap gap-1.5", children: PRUNE_ACTIONS.map((a) => /* @__PURE__ */ jsx5(
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
    message && /* @__PURE__ */ jsx5("p", { className: "mb-2 opacity-90", role: "status", children: message }),
    data.images.length > 0 && /* @__PURE__ */ jsxs4("table", { className: "w-full", children: [
      /* @__PURE__ */ jsx5("thead", { children: /* @__PURE__ */ jsxs4("tr", { className: "border-b border-white/10 text-left uppercase opacity-60", children: [
        /* @__PURE__ */ jsx5("th", { className: "py-1", children: "Image" }),
        /* @__PURE__ */ jsx5("th", { className: "py-1", children: "Gr\xF6\xDFe" }),
        /* @__PURE__ */ jsx5("th", { className: "py-1", children: "Erstellt" }),
        /* @__PURE__ */ jsx5("th", { className: "py-1", children: "Genutzt" })
      ] }) }),
      /* @__PURE__ */ jsx5("tbody", { className: "divide-y divide-white/5", children: data.images.map((img) => /* @__PURE__ */ jsxs4("tr", { children: [
        /* @__PURE__ */ jsx5("td", { className: "py-1 break-words", children: img.name ?? /* @__PURE__ */ jsxs4("span", { className: "opacity-60", children: [
          "ohne Namen (",
          img.id,
          ")"
        ] }) }),
        /* @__PURE__ */ jsx5("td", { className: "py-1 whitespace-nowrap", children: img.size != null ? formatBytes(img.size) : "?" }),
        /* @__PURE__ */ jsx5("td", { className: "py-1 opacity-70", children: img.created ?? "" }),
        /* @__PURE__ */ jsx5("td", { className: `py-1 ${img.in_use === false ? "text-amber-300" : "opacity-70"}`, children: img.in_use == null ? "?" : img.in_use ? "ja" : "nein" })
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
function DetailsPanel({ entry }) {
  const [data, setData] = useState5(null);
  const [error, setError] = useState5(null);
  useEffect5(() => {
    let cancelled = false;
    authedFetch(`/ext/service-matrix/containers/${entry.host_id}/${encodeURIComponent(entry.container)}/inspect`).then(async (res) => {
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
  if (error) return /* @__PURE__ */ jsxs4("p", { className: "text-xs text-red-400", children: [
    "Details nicht abrufbar: ",
    error
  ] });
  if (!data) return /* @__PURE__ */ jsx5("p", { className: "text-xs opacity-60", children: "Lade Details \u2026" });
  const d = data;
  const since = d.running ? d.started_at : d.finished_at;
  return /* @__PURE__ */ jsxs4("div", { className: "text-xs", "data-testid": `inspect-${entry.id}`, children: [
    /* @__PURE__ */ jsxs4("dl", { className: "grid grid-cols-2 gap-x-6 gap-y-1 opacity-85 sm:grid-cols-4", children: [
      /* @__PURE__ */ jsxs4("div", { children: [
        /* @__PURE__ */ jsx5("dt", { className: "opacity-60", children: "Image" }),
        /* @__PURE__ */ jsxs4("dd", { className: "break-words", children: [
          d.image ?? "?",
          d.image_id ? ` (${d.image_id})` : ""
        ] })
      ] }),
      /* @__PURE__ */ jsxs4("div", { children: [
        /* @__PURE__ */ jsx5("dt", { className: "opacity-60", children: d.running ? "L\xE4uft seit" : "Beendet" }),
        /* @__PURE__ */ jsxs4("dd", { children: [
          since ? new Date(since).toLocaleString() : "\u2013",
          !d.running && d.exit_code != null ? ` \xB7 Exit ${d.exit_code}` : "",
          d.oom_killed ? " \xB7 Speicher voll (OOM)" : ""
        ] })
      ] }),
      /* @__PURE__ */ jsxs4("div", { children: [
        /* @__PURE__ */ jsx5("dt", { className: "opacity-60", children: "Neustart-Regel" }),
        /* @__PURE__ */ jsxs4("dd", { children: [
          RESTART_LABEL[d.restart_policy] ?? d.restart_policy,
          d.restart_count ? ` \xB7 ${d.restart_count}\xD7 neu gestartet` : ""
        ] })
      ] }),
      /* @__PURE__ */ jsxs4("div", { children: [
        /* @__PURE__ */ jsx5("dt", { className: "opacity-60", children: "Zustand" }),
        /* @__PURE__ */ jsxs4("dd", { children: [
          d.health ? HEALTH_LABEL[d.health] ?? d.health : "kein Healthcheck",
          d.privileged ? " \xB7 privilegiert" : ""
        ] })
      ] }),
      d.compose && /* @__PURE__ */ jsxs4("div", { children: [
        /* @__PURE__ */ jsx5("dt", { className: "opacity-60", children: "Compose" }),
        /* @__PURE__ */ jsxs4("dd", { className: "break-words", children: [
          d.compose.project,
          d.compose.service ? ` / ${d.compose.service}` : "",
          d.compose.working_dir ? ` \xB7 ${d.compose.working_dir}` : ""
        ] })
      ] }),
      d.memory_limit ? /* @__PURE__ */ jsxs4("div", { children: [
        /* @__PURE__ */ jsx5("dt", { className: "opacity-60", children: "RAM-Limit" }),
        /* @__PURE__ */ jsx5("dd", { children: formatBytes(d.memory_limit) })
      ] }) : null
    ] }),
    /* @__PURE__ */ jsx5("p", { className: "mt-2 mb-0.5 font-medium opacity-60", children: "Ports" }),
    /* @__PURE__ */ jsx5("p", { className: "opacity-85", children: d.ports.length === 0 ? d.network_mode === "host" ? "Host-Netz (alle Ports direkt)" : "keine" : d.ports.map((p) => `${p.published.length ? p.published.join(", ") : "intern"} \u2192 ${p.container}`).join(" \xB7 ") }),
    /* @__PURE__ */ jsx5("p", { className: "mt-2 mb-0.5 font-medium opacity-60", children: "Speicher" }),
    /* @__PURE__ */ jsxs4("ul", { className: "space-y-0.5 opacity-85", children: [
      d.mounts.length === 0 && /* @__PURE__ */ jsx5("li", { children: "keine Mounts" }),
      d.mounts.map((m) => /* @__PURE__ */ jsxs4("li", { className: "break-words", children: [
        /* @__PURE__ */ jsx5("span", { className: "opacity-60", children: m.type === "volume" ? "Volume" : m.type === "bind" ? "Ordner" : m.type }),
        " ",
        m.source,
        " \u2192 ",
        m.destination,
        m.read_only ? " (nur lesen)" : ""
      ] }, `${m.destination}`))
    ] }),
    /* @__PURE__ */ jsx5("p", { className: "mt-2 mb-0.5 font-medium opacity-60", children: "Netze" }),
    /* @__PURE__ */ jsx5("p", { className: "opacity-85", children: d.networks.map((n) => `${n.name}${n.ip ? ` (${n.ip})` : ""}`).join(" \xB7 ") || "keine" }),
    /* @__PURE__ */ jsxs4("p", { className: "mt-2 mb-0.5 font-medium opacity-60", children: [
      "Umgebungsvariablen (",
      d.env_keys.length,
      ")"
    ] }),
    /* @__PURE__ */ jsx5("p", { className: "break-words opacity-70", title: "Werte werden bewusst nicht angezeigt \u2013 dort stehen oft Passw\xF6rter.", children: d.env_keys.join(", ") || "keine" })
  ] });
}
function LogPanel({ entry, onClose }) {
  const [lines, setLines] = useState5([]);
  const [tail, setTail] = useState5(200);
  const [running, setRunning] = useState5(true);
  const [follow, setFollow] = useState5(true);
  const [filter, setFilter] = useState5("");
  const [error, setError] = useState5(null);
  const [ended, setEnded] = useState5(false);
  const boxRef = useRef2(null);
  useEffect5(() => {
    if (!running || !entry.host_id || !entry.container) return;
    const controller = new AbortController();
    setLines([]);
    setError(null);
    setEnded(false);
    (async () => {
      try {
        const res = await authedFetch(
          `/ext/service-matrix/containers/${entry.host_id}/${encodeURIComponent(entry.container)}/logs?tail=${tail}`,
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
  }, [entry.host_id, entry.container, tail, running]);
  useEffect5(() => {
    if (follow && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight;
  }, [lines, follow]);
  const shown = useMemo2(() => {
    if (!filter) return lines;
    const needle = filter.toLowerCase();
    return lines.filter((l) => l.toLowerCase().includes(needle));
  }, [lines, filter]);
  return /* @__PURE__ */ jsxs4("div", { className: "flex flex-col gap-2", "data-testid": "log-panel", children: [
    /* @__PURE__ */ jsxs4("div", { className: "flex flex-wrap items-center gap-2 text-xs", children: [
      /* @__PURE__ */ jsxs4("span", { className: "font-medium", children: [
        "Logs: ",
        entry.name
      ] }),
      /* @__PURE__ */ jsx5("span", { className: `rounded-full px-2 py-0.5 ${running && !ended && !error ? "bg-emerald-500/20 text-emerald-300" : "bg-white/10 opacity-70"}`, children: error ? "Fehler" : !running ? "angehalten" : ended ? "beendet" : "live" }),
      /* @__PURE__ */ jsxs4("label", { className: "flex items-center gap-1", children: [
        "Letzte",
        /* @__PURE__ */ jsx5("select", { value: tail, onChange: (e) => setTail(Number(e.target.value)), className: "px-1 py-0.5 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]", children: TAIL_OPTIONS.map((n) => /* @__PURE__ */ jsx5("option", { value: n, children: n }, n)) }),
        "Zeilen"
      ] }),
      /* @__PURE__ */ jsx5(
        "input",
        {
          value: filter,
          onChange: (e) => setFilter(e.target.value),
          placeholder: "Filtern \u2026",
          "aria-label": "Logs filtern",
          className: "w-40 px-2 py-0.5 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]"
        }
      ),
      /* @__PURE__ */ jsxs4("label", { className: "flex items-center gap-1", children: [
        /* @__PURE__ */ jsx5("input", { type: "checkbox", checked: follow, onChange: (e) => setFollow(e.target.checked) }),
        "Mitlaufen"
      ] }),
      /* @__PURE__ */ jsxs4("div", { className: "ml-auto flex gap-1.5", children: [
        /* @__PURE__ */ jsx5("button", { type: "button", onClick: () => setRunning((r) => !r), className: "px-2 py-0.5 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition", children: running ? "Anhalten" : "Fortsetzen" }),
        /* @__PURE__ */ jsx5("button", { type: "button", onClick: () => setLines([]), className: "px-2 py-0.5 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition", children: "Leeren" }),
        /* @__PURE__ */ jsx5("button", { type: "button", onClick: onClose, className: "px-2 py-0.5 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition", children: "Schlie\xDFen" })
      ] })
    ] }),
    error && /* @__PURE__ */ jsx5("p", { className: "text-xs text-red-400", children: error }),
    /* @__PURE__ */ jsx5(
      "pre",
      {
        ref: boxRef,
        className: "h-72 overflow-auto whitespace-pre-wrap break-all rounded bg-black/60 p-2 font-mono text-[11px] leading-snug",
        children: shown.length === 0 ? filter ? "Keine passenden Zeilen." : "Noch keine Ausgabe \u2026" : shown.join("\n")
      }
    )
  ] });
}
function NoContainers() {
  const tag = useSettingText("service-matrix", "docker_host_tag", "docker");
  return /* @__PURE__ */ jsx5(
    EmptyState,
    {
      icon: "package",
      title: "Noch kein Server mit Docker",
      text: `Lege unter Server & Zug\xE4nge einen Server an und gib ihm die Markierung \u201E${tag}\u201C. L\xE4uft dort Docker, erscheinen seine Container hier von selbst. Die Markierung l\xE4sst sich in den Einstellungen des Moduls \xE4ndern.`,
      action: /* @__PURE__ */ jsxs4("div", { className: "flex flex-wrap justify-center gap-2", children: [
        /* @__PURE__ */ jsx5(SettingsLink, { to: "/settings/hosts", permission: "hosts.write", children: "Server & Zug\xE4nge \xF6ffnen" }),
        /* @__PURE__ */ jsx5(SettingsLink, { to: "/settings/extensions/service-matrix", permission: "extensions.manage", variant: "secondary", children: "Moduleinstellungen" })
      ] })
    }
  );
}
function ServiceMatrixPage() {
  const unmountSignal = useUnmountSignal();
  const [services, setServices] = useState5(null);
  const [error, setError] = useState5(null);
  const [message, setMessage] = useState5(null);
  const [pending, setPending] = useState5(null);
  const [urlParams, updateUrl] = useUrlParams();
  const logsFor = urlParams.get("logs") || null;
  const setLogsFor = (id) => updateUrl({ logs: id });
  const hostFilter = urlParams.get("host") || null;
  const [detailsFor, setDetailsFor] = useState5(null);
  const [updateFor, setUpdateFor] = useState5(null);
  const [storageFor, setStorageFor] = useState5(null);
  const [query, setQuery] = useState5("");
  const [stats, setStats] = useState5({});
  const [statsLoaded, setStatsLoaded] = useState5(false);
  const [images, setImages] = useState5(null);
  const [imageMessage, setImageMessage] = useState5(null);
  const loadImages = useCallback2(async (signal) => {
    try {
      const res = await authedFetch("/ext/service-matrix/image-updates", { signal });
      if (!res.ok) return;
      const body = await res.json();
      if (!signal?.aborted) setImages(body);
    } catch {
    }
  }, []);
  const reloadImages = useCallback2(() => void loadImages(unmountSignal()), [loadImages, unmountSignal]);
  const load = useCallback2(() => {
    setError(null);
    void loadImages(unmountSignal());
    authedFetch("/ext/service-matrix/widgets/matrix").then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    }).then((body) => setServices(body.data)).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [loadImages, unmountSignal]);
  useEffect5(() => {
    load();
    const interval = setInterval(load, 3e4);
    return () => clearInterval(interval);
  }, [load]);
  const visibleImages = scopeImages(images, hostFilter);
  const imagesChecking = Object.values(visibleImages?.hosts ?? {}).some((h) => h.checking);
  const imagesBusy = imagesChecking || Object.keys(visibleImages?.applying ?? {}).length > 0;
  useEffect5(() => {
    if (!imagesBusy) return;
    const controller = new AbortController();
    const interval = setInterval(() => void loadImages(controller.signal), IMAGE_POLL_MS);
    return () => {
      clearInterval(interval);
      controller.abort();
    };
  }, [imagesBusy, loadImages]);
  useEffect5(() => {
    let cancelled = false;
    const loadStats = () => authedFetch("/ext/service-matrix/stats").then((res) => res.ok ? res.json() : { data: {} }).then((body) => {
      if (cancelled) return;
      setStats(body.data);
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
      const extra = entry.is_self && verb === "restart" ? " Das ist Nodvard Deck selbst \u2013 die Oberfl\xE4che ist kurz weg." : "";
      const ok = await deck().confirmDialog(`"${entry.name}" ${text.confirm}${extra}`, {
        danger: verb === "stop",
        confirmLabel: text.label
      });
      if (!ok) return;
    }
    setPending(`${entry.id}:${verb}`);
    setMessage(null);
    try {
      const { action, approved } = await runAction(`/hosts/${entry.host_id}/actions/container.${verb}`, {
        method: "POST",
        body: JSON.stringify({ payload: { container: entry.container }, reason: `\xDCber die Service-Matrix ausgel\xF6st (${verb}).` })
      }, { signal: unmountSignal() });
      if (!approved && action.status === "proposed") {
        setMessage(`"${entry.name}": ${text.label} vorgeschlagen \u2013 Freigabe durch einen Admin n\xF6tig, siehe "Aktionen".`);
        return;
      }
      const status = action.status ?? "?";
      const failure = action.result?.error;
      setMessage(
        status === "succeeded" ? `"${entry.name}" ${text.done}.` : `"${entry.name}": ${text.label} -> ${ACTION_STATUS_LABEL[status] ?? status}${failure ? ` (${failure})` : ""}.`
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
      const res = await authedFetch(`/ext/service-matrix/image-updates/check${query2 ? `?${query2}` : ""}`, { method: "POST", signal: unmountSignal() });
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
        body: JSON.stringify({ payload: { project }, reason: `Stack "${project}" \xFCber die Service-Matrix neu gestartet.` })
      }, { signal: unmountSignal() });
      const status = action.status ?? "?";
      if (approved) {
        setMessage(
          status === "succeeded" ? action.result?.output ?? "Stack neu gestartet." : `Stack "${project}": ${ACTION_STATUS_LABEL[status] ?? status}${action.result?.error ? ` (${action.result.error})` : ""}`
        );
      } else if (status === "proposed") {
        setMessage(`Stack "${project}": vorgeschlagen \u2013 Freigabe durch einen Admin n\xF6tig, siehe "Aktionen".`);
      } else {
        setMessage(`Stack "${project}": ${ACTION_STATUS_LABEL[status] ?? status}`);
      }
      load();
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPending(null);
    }
  }
  if (!services && !error) return /* @__PURE__ */ jsx5("div", { className: "p-6 text-sm opacity-60", children: "Lade \u2026" });
  if (error) return /* @__PURE__ */ jsxs4("div", { className: "p-6 text-sm text-red-400", children: [
    "Fehler: ",
    error
  ] });
  const imageSummary = summarizeImages(visibleImages, imagesChecking);
  const needle = query.trim().toLowerCase();
  const byHost = /* @__PURE__ */ new Map();
  for (const s of services ?? []) {
    if (needle && !s.name.toLowerCase().includes(needle) && !(s.compose_project ?? "").toLowerCase().includes(needle)) continue;
    if (hostFilter && s.host_id !== hostFilter) continue;
    const list = byHost.get(s.host) ?? [];
    list.push(s);
    byHost.set(s.host, list);
  }
  return /* @__PURE__ */ jsxs4("div", { className: "mx-auto w-full max-w-7xl p-4 sm:p-6", children: [
    /* @__PURE__ */ jsxs4("div", { className: "mb-4 flex flex-wrap items-center gap-3", children: [
      /* @__PURE__ */ jsx5("h2", { className: "font-semibold text-xl tracking-tight", children: "Service-Matrix" }),
      /* @__PURE__ */ jsx5(
        "input",
        {
          value: query,
          onChange: (e) => setQuery(e.target.value),
          placeholder: "Container suchen \u2026",
          "aria-label": "Container suchen",
          className: "w-56 px-2 py-1 text-sm rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]"
        }
      ),
      /* @__PURE__ */ jsx5("button", { type: "button", onClick: load, className: "px-2 py-1 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition", children: "Aktualisieren" }),
      deck().hasPermission("hosts.execute") && (services ?? []).length > 0 && /* @__PURE__ */ jsx5(
        "button",
        {
          type: "button",
          disabled: imagesChecking,
          onClick: () => void startImageCheck(false),
          title: "Fragt bei den Registries nach, ob es neuere Images gibt. Es wird nichts heruntergeladen oder neu gestartet.",
          className: "px-2 py-1 text-xs disabled:opacity-40 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
          children: imagesChecking ? "Pr\xFCfe Images \u2026" : "Image-Updates pr\xFCfen"
        }
      ),
      hostFilter && /* @__PURE__ */ jsxs4("span", { className: "accent-soft flex items-center gap-1 rounded px-2 py-0.5 text-xs", "data-testid": "host-filter", children: [
        "Nur ",
        (services ?? []).find((s) => s.host_id === hostFilter)?.host ?? "dieser Server",
        /* @__PURE__ */ jsx5("button", { type: "button", onClick: () => updateUrl({ host: null }), "aria-label": "Filter entfernen", className: "opacity-70 hover:opacity-100", children: "\u2715" })
      ] })
    ] }),
    message && /* @__PURE__ */ jsx5("p", { className: "mb-3 text-sm opacity-80", children: message }),
    (imageSummary || imageMessage) && /* @__PURE__ */ jsxs4("p", { className: "mb-3 flex flex-wrap items-center gap-x-3 text-xs opacity-80", "aria-live": "polite", "data-testid": "image-summary", children: [
      imageSummary && /* @__PURE__ */ jsx5("span", { children: imageSummary }),
      imageMessage && /* @__PURE__ */ jsx5("span", { className: "text-red-400", children: imageMessage }),
      !imagesChecking && imageSummary && deck().hasPermission("hosts.execute") && /* @__PURE__ */ jsx5(
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
    (services ?? []).length === 0 && /* @__PURE__ */ jsx5(NoContainers, {}),
    (services ?? []).length > 0 && byHost.size === 0 && /* @__PURE__ */ jsx5("p", { className: "mb-3 text-sm opacity-70", "data-testid": "no-match", children: needle ? "Kein Container passt zu deiner Suche." : "F\xFCr diesen Filter gibt es keine Container." }),
    [...byHost.entries()].map(([host, entries]) => /* @__PURE__ */ jsxs4("section", { className: "mb-6 overflow-x-auto", children: [
      /* @__PURE__ */ jsxs4("div", { className: "mb-2 flex flex-wrap items-center gap-3", children: [
        /* @__PURE__ */ jsx5("h3", { className: "text-sm font-medium uppercase tracking-wide opacity-60", children: host }),
        entries[0]?.host_id && entries[0].state !== "error" && deck().hasPermission("hosts.execute") && /* @__PURE__ */ jsx5(
          "button",
          {
            type: "button",
            onClick: () => setStorageFor(storageFor === entries[0].host_id ? null : entries[0].host_id),
            "aria-expanded": storageFor === entries[0].host_id,
            className: "px-2 py-0.5 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
            children: "Docker-Speicher"
          }
        ),
        entries[0]?.host_id && deck().hasPermission("hosts.execute") && /* @__PURE__ */ jsx5(
          BulkProposeButton,
          {
            hostId: entries[0].host_id,
            hostName: host,
            candidates: entries.filter((s) => s.host_id && s.container && s.state !== "error" && (s.state === "running" || s.state === "restarting") && !s.is_self && images?.data[s.id]?.status === "update" && images.data[s.id].apply?.mode !== "none").map((s) => ({ hostId: s.host_id, container: s.container, name: s.name })),
            onDone: reloadImages
          }
        )
      ] }),
      entries[0]?.host_id && images?.hosts[entries[0].host_id]?.error && /* @__PURE__ */ jsxs4("p", { className: "mb-2 text-xs text-red-400", "data-testid": `image-error-${entries[0].host_id}`, children: [
        "Image-Pr\xFCfung: ",
        images.hosts[entries[0].host_id].error
      ] }),
      storageFor && storageFor === entries[0]?.host_id && /* @__PURE__ */ jsx5("div", { className: "mb-3 bg-black/20 p-2 panel", children: /* @__PURE__ */ jsx5(StoragePanel, { hostId: storageFor }) }),
      /* @__PURE__ */ jsxs4("table", { role: "table", className: "w-full text-sm max-md:block", children: [
        /* @__PURE__ */ jsx5("thead", { className: "max-md:hidden", children: /* @__PURE__ */ jsxs4("tr", { className: "border-b border-white/10 text-left text-xs uppercase opacity-60", children: [
          /* @__PURE__ */ jsx5("th", { className: "py-1 w-1/4", children: "Container" }),
          /* @__PURE__ */ jsx5("th", { className: "py-1 whitespace-nowrap", children: "Zustand" }),
          /* @__PURE__ */ jsx5("th", { className: "py-1", children: "Status" }),
          /* @__PURE__ */ jsx5("th", { className: "py-1 whitespace-nowrap", children: "Image-Update" }),
          /* @__PURE__ */ jsx5("th", { className: "py-1 whitespace-nowrap", children: "CPU" }),
          /* @__PURE__ */ jsx5("th", { className: "py-1 whitespace-nowrap", children: "RAM" }),
          /* @__PURE__ */ jsx5("th", { className: "py-1", children: "Aktionen" })
        ] }) }),
        /* @__PURE__ */ jsx5("tbody", { role: "rowgroup", className: "divide-y divide-white/5 max-md:block max-md:space-y-2 max-md:divide-y-0", children: entries.map((s) => {
          const manageable = Boolean(s.host_id && s.container) && s.state !== "error";
          const isRunning = s.state === "running" || s.state === "restarting";
          const logsOpen = logsFor === s.id;
          const busy = (verb) => pending === `${s.id}:${verb}`;
          const imageShown = Boolean(images?.data[s.id]) || Boolean(s.host_id && images?.hosts[s.host_id]?.checking);
          return /* @__PURE__ */ jsxs4(Fragment4, { children: [
            /* @__PURE__ */ jsxs4("tr", { role: "row", className: CARD_ROW, "data-testid": `row-${s.id}`, children: [
              /* @__PURE__ */ jsxs4("td", { role: "cell", className: "py-1.5 break-words max-md:w-full max-md:py-0 max-md:text-base", children: [
                s.name,
                s.is_self && /* @__PURE__ */ jsx5("span", { className: "ml-1.5 rounded bg-white/10 px-1 text-[10px] opacity-70", children: "Nodvard Deck" }),
                s.compose_project && /* @__PURE__ */ jsx5(
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
                s.image && /* @__PURE__ */ jsx5("span", { className: "block text-xs opacity-50", children: s.image })
              ] }),
              /* @__PURE__ */ jsx5("td", { role: "cell", className: "py-1.5 max-md:py-0", children: /* @__PURE__ */ jsx5("span", { className: `rounded border px-1.5 py-0.5 text-xs ${TONE_CLASS[s.tone] ?? TONE_CLASS.neutral}`, children: STATE_LABEL[s.state] ?? s.state }) }),
              /* @__PURE__ */ jsx5("td", { role: "cell", className: "py-1.5 break-words opacity-70 max-md:min-w-0 max-md:flex-1 max-md:py-0 max-md:text-xs", title: s.status !== statusText(s.status) ? s.status : void 0, children: statusText(s.status) }),
              /* @__PURE__ */ jsxs4("td", { role: "cell", className: `py-1.5 text-xs max-md:w-full max-md:py-0 ${imageShown ? "" : "max-md:hidden"}`, children: [
                /* @__PURE__ */ jsx5("div", { "data-testid": `image-${s.id}`, children: manageable && /* @__PURE__ */ jsx5(ImageUpdateBadge, { result: images?.data[s.id], checking: Boolean(s.host_id && images?.hosts[s.host_id]?.checking), running: isRunning }) }),
                manageable && /* @__PURE__ */ jsx5(
                  ImageUpdateAction,
                  {
                    entryId: s.id,
                    offer: isRunning && !s.is_self && images?.data[s.id]?.status === "update" && deck().hasPermission("hosts.execute"),
                    apply: images?.data[s.id]?.apply,
                    applying: images?.applying?.[s.id],
                    applied: images?.applied?.[s.id],
                    open: updateFor === s.id,
                    onToggle: () => setUpdateFor(updateFor === s.id ? null : s.id)
                  }
                )
              ] }),
              /* @__PURE__ */ jsx5("td", { role: "cell", "data-label": "CPU", className: `py-1.5 whitespace-nowrap text-xs opacity-80 max-md:py-0 ${CARD_LABEL}`, "data-testid": `cpu-${s.id}`, children: !statsLoaded ? /* @__PURE__ */ jsx5("span", { title: MEASURING_HINT, children: "\u2026" }) : stats[s.id]?.cpu_percent != null ? `${stats[s.id].cpu_percent.toFixed(1)} %` : "\u2013" }),
              /* @__PURE__ */ jsx5("td", { role: "cell", "data-label": "RAM", className: `py-1.5 whitespace-nowrap text-xs opacity-80 max-md:py-0 ${CARD_LABEL}`, "data-testid": `mem-${s.id}`, children: !statsLoaded ? /* @__PURE__ */ jsx5("span", { title: MEASURING_HINT, children: "\u2026" }) : stats[s.id] ? stats[s.id].mem_used != null ? formatBytes(stats[s.id].mem_used) : /* @__PURE__ */ jsx5("span", { title: MEM_UNKNOWN_HINT, children: "n. v." }) : "\u2013" }),
              /* @__PURE__ */ jsx5("td", { role: "cell", className: "py-1.5 max-md:w-full max-md:py-0", children: /* @__PURE__ */ jsxs4("div", { className: "flex flex-wrap gap-1.5", children: [
                manageable && !isRunning && /* @__PURE__ */ jsx5(
                  "button",
                  {
                    type: "button",
                    disabled: busy("start"),
                    onClick: () => void trigger(s, "start"),
                    className: `rounded bg-emerald-500/20 px-2 py-1 text-xs text-emerald-300 hover:bg-emerald-500/30 disabled:opacity-40 ${TOUCH}`,
                    children: busy("start") ? "\u2026" : "Starten"
                  }
                ),
                manageable && isRunning && /* @__PURE__ */ jsx5(
                  "button",
                  {
                    type: "button",
                    disabled: busy("restart"),
                    onClick: () => void trigger(s, "restart"),
                    className: `px-2 py-1 text-xs disabled:opacity-40 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition ${TOUCH}`,
                    children: busy("restart") ? "\u2026" : "Neustart"
                  }
                ),
                manageable && isRunning && /* @__PURE__ */ jsx5(
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
                manageable && /* @__PURE__ */ jsx5(
                  "button",
                  {
                    type: "button",
                    onClick: () => setDetailsFor(detailsFor === s.id ? null : s.id),
                    "aria-expanded": detailsFor === s.id,
                    className: `rounded px-2 py-1 text-xs hover:bg-white/20 ${TOUCH} ${detailsFor === s.id ? "bg-white/20" : "bg-white/10"}`,
                    children: "Details"
                  }
                ),
                manageable && /* @__PURE__ */ jsx5(
                  "button",
                  {
                    type: "button",
                    onClick: () => setLogsFor(logsOpen ? null : s.id),
                    "aria-expanded": logsOpen,
                    className: `rounded px-2 py-1 text-xs hover:bg-white/20 ${TOUCH} ${logsOpen ? "bg-white/20" : "bg-white/10"}`,
                    children: "Logs"
                  }
                ),
                s.url && /* @__PURE__ */ jsx5("a", { href: s.url, target: "_blank", rel: "noreferrer", className: "px-1 py-1 text-xs opacity-70 hover:opacity-100 hover:underline max-md:px-2 max-md:py-2", children: "\xD6ffnen" })
              ] }) })
            ] }),
            detailsFor === s.id && /* @__PURE__ */ jsx5("tr", { role: "row", className: "max-md:block", children: /* @__PURE__ */ jsx5("td", { role: "cell", colSpan: 7, className: "bg-black/20 p-2 max-md:block", children: /* @__PURE__ */ jsx5(DetailsPanel, { entry: s }) }) }),
            updateFor === s.id && s.host_id && s.container && /* @__PURE__ */ jsx5("tr", { role: "row", className: "max-md:block", children: /* @__PURE__ */ jsx5("td", { role: "cell", colSpan: 7, className: "bg-black/20 p-2 max-md:block", children: /* @__PURE__ */ jsx5(
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
            logsOpen && /* @__PURE__ */ jsx5("tr", { role: "row", className: "max-md:block", children: /* @__PURE__ */ jsx5("td", { role: "cell", colSpan: 7, className: "bg-black/20 p-2 max-md:block", children: /* @__PURE__ */ jsx5(LogPanel, { entry: s, onClose: () => setLogsFor(null) }) }) })
          ] }, s.id);
        }) })
      ] })
    ] }, host))
  ] });
}
export {
  ImageUpdateBadge,
  STATE_LABEL,
  ServiceMatrixPage,
  scopeImages,
  statusText,
  summarizeImages
};
