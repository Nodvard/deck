// src/ProxmoxNodePage.tsx
import { Fragment as Fragment2, useCallback as useCallback2, useEffect as useEffect4, useRef as useRef2, useState as useState3 } from "react";

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

// ../../_shared/frontend/src/deck.ts
var FOCUS_CLASS = "nodvard-deck-focus lattice-focus";

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

// src/ProxmoxNodePage.tsx
import { Fragment as Fragment3, jsx as jsx3, jsxs as jsxs2 } from "react/jsx-runtime";
var HOST_STATUS_LABEL = { up: "l\xE4uft", down: "gestoppt", unknown: "unbekannt", maintenance: "Wartung" };
var KIND_LABEL = { vm: "VM", lxc: "LXC", hypervisor: "Knoten" };
var VM_ACTIONS = [
  { type: "vm.start", label: "Starten", showWhen: (s) => s !== "up" },
  {
    type: "vm.shutdown",
    label: "Herunterfahren",
    confirm: "sauber herunterfahren? Das Betriebssystem in der VM f\xE4hrt geordnet herunter.",
    dangerConfirm: false,
    showWhen: (s) => s !== "down"
  },
  { type: "vm.reboot", label: "Neustarten", confirm: "wirklich neu starten?", showWhen: (s) => s !== "down" },
  { type: "vm.snapshot", label: "Snapshot" },
  {
    type: "vm.stop",
    label: "Hart ausschalten",
    confirm: "wirklich hart ausschalten? Das ist wie Stecker ziehen: nicht gespeicherte Daten gehen verloren.",
    variant: "danger",
    showWhen: (s) => s !== "down"
  }
];
var ACTION_LABEL = {
  ...Object.fromEntries(VM_ACTIONS.map((a) => [a.type, a.label])),
  "vm.snapshot": "Snapshot anlegen",
  "vm.snapshot_rollback": "Snapshot zur\xFCckrollen",
  "vm.snapshot_delete": "Snapshot l\xF6schen"
};
function actionLabel(actionType, snapname) {
  const label = ACTION_LABEL[actionType] ?? actionType;
  return snapname ? `${label} \u201E${snapname}\u201C` : label;
}
function describeOutcome(hostLabel, actionType, body, snapname) {
  const label = actionLabel(actionType, snapname);
  const status = ACTION_STATUS_LABEL[body.status ?? ""] ?? body.status ?? "?";
  if (body.status === "succeeded" && body.result?.detail?.task_status === "running" && body.result.output?.trim()) {
    return `${hostLabel}: ${label} -> ${body.result.output.trim()}`;
  }
  const reason = body.status === "failed" ? body.result?.error?.trim() : void 0;
  return reason ? `${hostLabel}: ${label} -> ${status}: ${reason}` : `${hostLabel}: ${label} -> ${status}.`;
}
function parseProviderRef(ref) {
  if (!ref) return { connection: "?", node: "?", vmid: null };
  const parts = ref.split("/");
  return { connection: parts[0] ?? "?", node: parts[2] ?? "?", vmid: parts[3] ?? null };
}
function pendingKey(hostId, actionType, snapname) {
  return [hostId, actionType, snapname].filter(Boolean).join(":");
}
function scrollToElement(id) {
  document.getElementById(id)?.scrollIntoView?.({ block: "center", behavior: "smooth" });
}
var formatBytes2 = (n) => formatBytes(n, { fixed: true });
function formatUptime(seconds) {
  if (seconds <= 0) return "\u2013";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor(seconds % 86400 / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}
var BAR_TONE = {
  good: "bg-emerald-400",
  warn: "bg-amber-400",
  danger: "bg-red-400",
  neutral: "bg-white/40"
};
function SnapshotsPanel({
  hostId,
  refreshKey,
  onAction,
  isPending
}) {
  const [snaps, setSnaps] = useState3(null);
  const [error, setError] = useState3(null);
  useEffect4(() => {
    let cancelled = false;
    authedFetch(`/ext/proxmox/guests/${hostId}/snapshots`).then(async (res) => {
      if (!res.ok) throw new Error(await errorText(res));
      return res.json();
    }).then((rows) => !cancelled && setSnaps(rows)).catch((err) => !cancelled && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      cancelled = true;
    };
  }, [hostId, refreshKey]);
  if (error) return /* @__PURE__ */ jsxs2("p", { className: "text-xs text-red-400", children: [
    "Snapshots nicht abrufbar: ",
    error
  ] });
  if (!snaps) return /* @__PURE__ */ jsx3("p", { className: "text-xs opacity-60", children: "Lade Snapshots \u2026" });
  if (snaps.length === 0) return /* @__PURE__ */ jsx3("p", { className: "text-xs opacity-60", children: "Keine Snapshots." });
  return /* @__PURE__ */ jsx3("ul", { className: "flex flex-col gap-1 text-xs", "data-testid": `snapshots-${hostId}`, children: snaps.map((snap) => /* @__PURE__ */ jsxs2("li", { className: "flex flex-wrap items-center gap-2", children: [
    /* @__PURE__ */ jsx3("span", { className: "font-medium", children: snap.name }),
    /* @__PURE__ */ jsx3("span", { className: "opacity-60", children: snap.snaptime ? new Date(snap.snaptime * 1e3).toLocaleString() : "" }),
    snap.with_ram && /* @__PURE__ */ jsx3("span", { className: "rounded bg-white/10 px-1 text-[10px]", children: "mit RAM" }),
    snap.description && /* @__PURE__ */ jsxs2("span", { className: "opacity-60", children: [
      "-- ",
      snap.description
    ] }),
    /* @__PURE__ */ jsxs2("span", { className: "ml-auto flex gap-1.5", children: [
      /* @__PURE__ */ jsx3("button", { type: "button", disabled: isPending("vm.snapshot_rollback", snap.name), onClick: () => onAction("vm.snapshot_rollback", snap.name), className: "rounded bg-amber-500/20 px-2 py-0.5 text-amber-300 hover:bg-amber-500/30 disabled:opacity-40", children: "Zur\xFCckrollen" }),
      /* @__PURE__ */ jsx3("button", { type: "button", disabled: isPending("vm.snapshot_delete", snap.name), onClick: () => onAction("vm.snapshot_delete", snap.name), className: "px-2 py-0.5 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition disabled:opacity-40", children: "L\xF6schen" })
    ] })
  ] }, snap.name)) });
}
var BADGE_TONE = {
  good: "bg-emerald-500/20 text-emerald-300",
  warn: "bg-amber-500/20 text-amber-300",
  danger: "bg-red-500/20 text-red-300",
  neutral: "bg-white/10 opacity-70"
};
function UpdatesSection({ nodes }) {
  return /* @__PURE__ */ jsxs2("div", { className: "mt-3", children: [
    /* @__PURE__ */ jsx3("h4", { className: "mb-1 text-xs font-medium uppercase tracking-wide opacity-60", children: "Updates" }),
    /* @__PURE__ */ jsx3("ul", { className: "space-y-2", children: nodes.map((n) => /* @__PURE__ */ jsxs2("li", { className: "text-sm", "data-testid": `updates-${n.node}`, children: [
      /* @__PURE__ */ jsxs2("div", { className: "flex flex-wrap items-center gap-2", children: [
        /* @__PURE__ */ jsx3("span", { className: "font-medium", children: n.node }),
        /* @__PURE__ */ jsx3("span", { className: `rounded px-1.5 py-0.5 text-xs ${BADGE_TONE[n.tone] ?? BADGE_TONE.neutral}`, children: n.badge }),
        /* @__PURE__ */ jsx3("span", { className: "opacity-80", children: n.summary })
      ] }),
      !n.error && /* @__PURE__ */ jsxs2("p", { className: "text-xs opacity-60", children: [
        "Proxmox ",
        n.pve_version ?? "?",
        " \xB7 Kernel ",
        n.running_kernel ?? "?",
        n.last_check ? ` \xB7 zuletzt gepr\xFCft ${new Date(n.last_check * 1e3).toLocaleString()}` : " \xB7 noch kein Pr\xFCflauf von Proxmox gefunden"
      ] }),
      !n.error && n.last_check_ok === false && /* @__PURE__ */ jsxs2("p", { className: "text-xs text-amber-300", "data-testid": `updates-check-failed-${n.node}`, children: [
        "Die letzte Pr\xFCfung auf neue Pakete ist fehlgeschlagen",
        n.last_check_status ? `: ${n.last_check_status}` : "",
        ". Die Liste kann veraltet sein."
      ] }),
      !n.error && n.last_check_stale && /* @__PURE__ */ jsxs2("p", { className: "text-xs text-amber-300", "data-testid": `updates-check-stale-${n.node}`, children: [
        "Der n\xE4chtliche Pr\xFCflauf von Proxmox ist seit ",
        n.last_check_age_s ? `${Math.floor(n.last_check_age_s / 3600)} Stunden` : "\xFCber 36 Stunden",
        " nicht gelaufen. Die Liste kann veraltet sein; auf dem Knoten \u201Esystemctl status pve-daily-update.timer\u201C ansehen."
      ] }),
      n.packages.length > 0 && /* @__PURE__ */ jsxs2("details", { className: "mt-1", children: [
        /* @__PURE__ */ jsxs2("summary", { className: "cursor-pointer text-xs opacity-70", children: [
          "Pakete anzeigen (",
          n.packages.length,
          ")"
        ] }),
        /* @__PURE__ */ jsx3("table", { className: "mt-1 w-full text-xs", children: /* @__PURE__ */ jsx3("tbody", { className: "divide-y divide-white/5", children: n.packages.map((pkg) => /* @__PURE__ */ jsxs2("tr", { children: [
          /* @__PURE__ */ jsx3("td", { className: "py-0.5 pr-2 font-mono", children: pkg.package }),
          /* @__PURE__ */ jsx3("td", { className: "py-0.5 pr-2 whitespace-nowrap font-mono", children: pkg.new_package ? `neu: ${pkg.version}` : `${pkg.old_version} \u2192 ${pkg.version}` }),
          /* @__PURE__ */ jsx3("td", { className: "py-0.5 opacity-60", children: pkg.title })
        ] }, pkg.package)) }) })
      ] })
    ] }, n.node)) })
  ] });
}
function UnreachableHint({ connection, sources }) {
  const lines = /* @__PURE__ */ new Map();
  for (const [what, errors] of sources) {
    for (const e of errors) {
      if (e.connection !== connection) continue;
      const rest = `${e.node ? `von Knoten ${e.node} ` : ""}gerade nicht abrufbar: ${e.error}`;
      const whats = lines.get(rest) ?? [];
      if (!whats.includes(what)) whats.push(what);
      lines.set(rest, whats);
    }
  }
  if (lines.size === 0) return null;
  return /* @__PURE__ */ jsx3("div", { className: "mt-3 space-y-1 text-sm text-amber-300", "data-testid": `unreachable-${connection}`, children: [...lines].map(([rest, whats]) => /* @__PURE__ */ jsx3("p", { children: `${whats.join(" und ")} ${rest}` }, rest)) });
}
function StorageSection({ pools }) {
  return /* @__PURE__ */ jsxs2("div", { className: "mt-3", children: [
    /* @__PURE__ */ jsx3("h4", { className: "mb-1 text-xs font-medium uppercase tracking-wide opacity-60", children: "Speicher" }),
    /* @__PURE__ */ jsxs2("table", { className: "w-full text-sm", children: [
      /* @__PURE__ */ jsx3("thead", { children: /* @__PURE__ */ jsxs2("tr", { className: "border-b border-white/10 text-left text-xs uppercase opacity-60", children: [
        /* @__PURE__ */ jsx3("th", { className: "py-1 w-1/5", children: "Pool" }),
        /* @__PURE__ */ jsx3("th", { className: "py-1", children: "Inhalt" }),
        /* @__PURE__ */ jsx3("th", { className: "py-1 w-1/3", children: "Belegung" })
      ] }) }),
      /* @__PURE__ */ jsx3("tbody", { className: "divide-y divide-white/5", children: pools.map((pool) => /* @__PURE__ */ jsxs2("tr", { "data-testid": `pool-${pool.storage}`, children: [
        /* @__PURE__ */ jsxs2("td", { className: "py-1.5 align-top", children: [
          /* @__PURE__ */ jsx3("span", { className: "font-medium", children: pool.storage }),
          /* @__PURE__ */ jsxs2("span", { className: "block text-xs opacity-60", children: [
            pool.type,
            pool.shared ? " \xB7 geteilt" : ""
          ] })
        ] }),
        /* @__PURE__ */ jsxs2("td", { className: "py-1.5 align-top text-xs", children: [
          /* @__PURE__ */ jsx3("span", { className: "opacity-70", children: pool.content_labels.join(", ") }),
          (pool.volumes ?? []).length > 0 && /* @__PURE__ */ jsxs2("span", { className: "block", children: [
            "Gast-Disks:",
            " ",
            (pool.volumes ?? []).map((v) => `${v.name} (${formatBytes2(v.size ?? 0)})`).join(", ")
          ] }),
          Object.values(pool.other ?? {}).length > 0 && /* @__PURE__ */ jsx3("span", { className: "block opacity-60", children: Object.values(pool.other ?? {}).map((o) => `${o.count} ${o.label} (${formatBytes2(o.size)})`).join(", ") })
        ] }),
        /* @__PURE__ */ jsxs2("td", { className: "py-1.5 align-top text-xs", children: [
          /* @__PURE__ */ jsx3("div", { className: "h-1.5 w-full overflow-hidden rounded bg-white/10", role: "progressbar", "aria-valuenow": pool.used_percent ?? 0, "aria-valuemin": 0, "aria-valuemax": 100, children: /* @__PURE__ */ jsx3("div", { className: `h-full ${BAR_TONE[pool.tone] ?? BAR_TONE.neutral}`, style: { width: `${Math.min(100, pool.used_percent ?? 0)}%` } }) }),
          /* @__PURE__ */ jsxs2("span", { className: "opacity-70", children: [
            formatBytes2(pool.used ?? 0),
            " / ",
            formatBytes2(pool.total ?? 0),
            pool.used_percent != null ? ` (${pool.used_percent} %)` : ""
          ] })
        ] })
      ] }, pool.id)) })
    ] })
  ] });
}
function formatDuration(seconds) {
  if (seconds == null) return "";
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes}m ${seconds % 60}s` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}
function TaskRow({ task }) {
  const [lines, setLines] = useState3(null);
  const [open, setOpen] = useState3(false);
  const [error, setError] = useState3(null);
  async function toggle() {
    const next = !open;
    setOpen(next);
    if (!next || lines) return;
    try {
      const res = await authedFetch(
        `/ext/proxmox/tasks/${encodeURIComponent(task.connection)}/${encodeURIComponent(task.node)}/log?upid=${encodeURIComponent(task.upid)}`
      );
      if (!res.ok) throw new Error(await errorText(res));
      setLines((await res.json()).lines);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }
  const statusClass = task.running ? "text-amber-300" : task.ok ? "text-emerald-300" : "text-red-300";
  const statusText = task.running ? "l\xE4uft" : task.ok ? "OK" : task.status ?? "Fehler";
  return /* @__PURE__ */ jsxs2(Fragment3, { children: [
    /* @__PURE__ */ jsxs2("tr", { className: "cursor-pointer hover:bg-white/5", onClick: () => void toggle(), "data-testid": `task-${task.upid}`, children: [
      /* @__PURE__ */ jsx3("td", { className: "py-1 whitespace-nowrap opacity-70", children: task.starttime ? new Date(task.starttime * 1e3).toLocaleString() : "" }),
      /* @__PURE__ */ jsxs2("td", { className: "py-1", children: [
        task.type_label,
        task.guest_name ? ` \xB7 ${task.guest_name}` : task.guest_id ? ` \xB7 ${task.guest_id}` : ""
      ] }),
      /* @__PURE__ */ jsx3("td", { className: "py-1 opacity-70", children: task.user }),
      /* @__PURE__ */ jsx3("td", { className: `py-1 break-words ${statusClass}`, children: statusText }),
      /* @__PURE__ */ jsx3("td", { className: "py-1 whitespace-nowrap opacity-70", children: formatDuration(task.duration_s) })
    ] }),
    open && /* @__PURE__ */ jsx3("tr", { children: /* @__PURE__ */ jsxs2("td", { colSpan: 5, className: "bg-black/30 p-2", children: [
      error && /* @__PURE__ */ jsx3("p", { className: "text-red-400", children: error }),
      !error && !lines && /* @__PURE__ */ jsx3("p", { className: "opacity-60", children: "Lade Protokoll \u2026" }),
      lines && /* @__PURE__ */ jsx3("pre", { className: "max-h-64 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px]", children: lines.join("\n") || "(leer)" })
    ] }) })
  ] });
}
function TasksSection({
  id,
  tasks,
  showConsole,
  onToggleConsole,
  focused = false,
  visit = 0,
  filterLabel,
  onClearFilter
}) {
  const ref = useRef2(null);
  useEffect4(() => {
    if (focused && ref.current) ref.current.open = true;
  }, [focused, visit]);
  return /* @__PURE__ */ jsxs2("details", { ref, id, className: "mt-3 p-2 panel", children: [
    /* @__PURE__ */ jsxs2("summary", { className: "cursor-pointer text-xs font-medium uppercase tracking-wide opacity-70", children: [
      "Aufgabenverlauf (",
      tasks.length,
      ")"
    ] }),
    /* @__PURE__ */ jsxs2("div", { className: "mt-2 flex flex-wrap items-center gap-3 text-xs opacity-80", children: [
      /* @__PURE__ */ jsxs2("label", { className: "flex items-center gap-1", children: [
        /* @__PURE__ */ jsx3("input", { type: "checkbox", checked: showConsole, onChange: onToggleConsole }),
        "Konsolen-\xD6ffnungen anzeigen"
      ] }),
      filterLabel && /* @__PURE__ */ jsxs2("span", { className: "accent-soft flex items-center gap-1 rounded px-2 py-0.5", children: [
        "Nur ",
        filterLabel,
        /* @__PURE__ */ jsx3("button", { type: "button", onClick: onClearFilter, "aria-label": "Filter entfernen", className: "opacity-70 hover:opacity-100", children: "\u2715" })
      ] })
    ] }),
    /* @__PURE__ */ jsxs2("table", { className: "mt-1 w-full text-xs", children: [
      /* @__PURE__ */ jsx3("thead", { children: /* @__PURE__ */ jsxs2("tr", { className: "border-b border-white/10 text-left uppercase opacity-60", children: [
        /* @__PURE__ */ jsx3("th", { className: "py-1", children: "Zeit" }),
        /* @__PURE__ */ jsx3("th", { className: "py-1", children: "Aufgabe" }),
        /* @__PURE__ */ jsx3("th", { className: "py-1", children: "Benutzer" }),
        /* @__PURE__ */ jsx3("th", { className: "py-1", children: "Status" }),
        /* @__PURE__ */ jsx3("th", { className: "py-1", children: "Dauer" })
      ] }) }),
      /* @__PURE__ */ jsx3("tbody", { className: "divide-y divide-white/5", children: tasks.map((task) => /* @__PURE__ */ jsx3(TaskRow, { task }, task.upid)) })
    ] })
  ] });
}
function formatMb(mb) {
  if (mb == null) return "?";
  return mb >= 1024 ? `${(mb / 1024).toFixed(mb % 1024 === 0 ? 0 : 1)} GB` : `${mb} MB`;
}
function diskLabel(d) {
  switch (d.kind) {
    case "cdrom":
      return d.volume && d.volume !== "none" ? `CD/DVD: ${d.volume}` : "CD/DVD (leer)";
    case "cloudinit":
      return `Cloud-Init-Laufwerk auf ${d.storage}`;
    case "bind":
      return `Host-Ordner ${d.volume} \u2192 ${d.mountpoint}`;
    case "unused":
      return `Nicht zugeordnet: ${d.storage}:${d.volume}`;
    default:
      return `${d.size ?? "?"} auf ${d.storage ?? "?"}${d.mountpoint ? ` \u2192 ${d.mountpoint}` : ""}`;
  }
}
function agentText(d) {
  if (!d.agent_enabled) return { text: "nicht eingerichtet", className: "opacity-60" };
  if (!d.running) return { text: "eingerichtet (Gast aus)", className: "opacity-60" };
  if (d.agent_responding) return { text: "aktiv", className: "text-emerald-300" };
  return { text: "eingerichtet, antwortet nicht", className: "text-amber-300" };
}
var EDIT_KEYS = {
  qemu: ["cores", "sockets", "memory", "balloon", "onboot", "startup_order"],
  lxc: ["cores", "memory", "swap", "onboot", "startup_order"]
};
var EDIT_LABEL = {
  cores: "Kerne",
  sockets: "Sockel",
  memory: "RAM (MB)",
  balloon: "Ballon-Minimum (MB)",
  swap: "Swap (MB)",
  onboot: "Autostart",
  startup_order: "Startreihenfolge"
};
function toEditValues(d) {
  return {
    cores: String(d.cores),
    sockets: String(d.sockets ?? 1),
    memory: String(d.memory_mb ?? ""),
    // Kein Ballon-Wert heisst in Proxmox: Minimum = RAM.
    balloon: String(d.balloon_mb ?? d.memory_mb ?? ""),
    swap: String(d.swap_mb ?? 0),
    onboot: d.onboot,
    startup_order: d.startup_order == null ? "" : String(d.startup_order)
  };
}
function editChanges(kind, before, after) {
  const changes = {};
  for (const key of EDIT_KEYS[kind]) {
    if (before[key] === after[key]) continue;
    if (key === "onboot") changes.onboot = after.onboot;
    else if (key === "startup_order") changes.startup_order = after.startup_order === "" ? null : Number(after.startup_order);
    else changes[key] = Number(after[key]);
  }
  return changes;
}
function GuestEditForm({ hostId, details, onDone }) {
  const unmountSignal = useUnmountSignal();
  const initial = toEditValues(details);
  const [values, setValues] = useState3(initial);
  const [busy, setBusy] = useState3(false);
  const changes = editChanges(details.kind, initial, values);
  const changed = Object.keys(changes);
  async function save(e) {
    e.preventDefault();
    if (changed.length === 0) return;
    const summary = changed.map((k) => `${EDIT_LABEL[k].replace(/ \(MB\)$/, "")}: ${k === "onboot" ? initial.onboot ? "an" : "aus" : initial[k] || "keine"} \u2192 ${k === "onboot" ? values.onboot ? "an" : "aus" : values[k] || "keine"}`).join(", ");
    const ok = await deck().confirmDialog(`Hardware \xE4ndern \u2013 ${summary}? Manches greift erst nach einem Neustart des Gasts.`, { confirmLabel: "\xC4ndern" });
    if (!ok) return;
    setBusy(true);
    try {
      const { action, approved } = await runAction(`/hosts/${hostId}/actions/vm.config_set`, {
        method: "POST",
        body: JSON.stringify({ payload: { changes }, reason: "Hardware \xFCber die Proxmox-Seite ge\xE4ndert." })
      }, { signal: unmountSignal() });
      const status = action.status ?? "?";
      if (approved) {
        onDone(
          status === "succeeded" ? action.result?.output ?? "Ge\xE4ndert." : isActionRunning(status) ? RUNNING_IN_BACKGROUND : `Fehlgeschlagen: ${action.result?.error ?? status}`
        );
      } else if (status === "proposed") {
        onDone(`\xC4nderung vorgeschlagen \u2013 Freigabe durch einen Admin n\xF6tig, siehe "Aktionen".`);
      } else {
        onDone(`${ACTION_STATUS_LABEL[status] ?? status}.`);
      }
    } catch (err) {
      onDone(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  }
  return /* @__PURE__ */ jsxs2("form", { onSubmit: (e) => void save(e), className: "mt-2 rounded border border-white/10 bg-black/20 p-2", "data-testid": `edit-${hostId}`, children: [
    /* @__PURE__ */ jsxs2("div", { className: "flex flex-wrap items-end gap-3", children: [
      EDIT_KEYS[details.kind].map(
        (key) => key === "onboot" ? /* @__PURE__ */ jsxs2("label", { className: "flex items-center gap-1 pb-1", children: [
          /* @__PURE__ */ jsx3("input", { type: "checkbox", checked: values.onboot, onChange: (e) => setValues({ ...values, onboot: e.target.checked }) }),
          EDIT_LABEL[key]
        ] }, key) : /* @__PURE__ */ jsxs2("label", { className: "flex flex-col gap-0.5", children: [
          /* @__PURE__ */ jsx3("span", { className: "opacity-60", children: EDIT_LABEL[key] }),
          /* @__PURE__ */ jsx3(
            "input",
            {
              type: "number",
              min: key === "startup_order" || key === "balloon" || key === "swap" ? 0 : 1,
              value: values[key],
              placeholder: key === "startup_order" ? "keine" : void 0,
              onChange: (e) => setValues({ ...values, [key]: e.target.value }),
              className: "w-28 px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]"
            }
          )
        ] }, key)
      ),
      /* @__PURE__ */ jsx3("button", { type: "submit", disabled: busy || changed.length === 0, className: "rounded bg-white/15 px-3 py-1 hover:bg-white/25 disabled:opacity-40", children: busy ? "\u2026" : "Speichern" }),
      /* @__PURE__ */ jsx3("button", { type: "button", onClick: () => onDone(null), className: "rounded px-2 py-1 opacity-70 hover:opacity-100", children: "Abbrechen" })
    ] }),
    details.kind === "qemu" && /* @__PURE__ */ jsx3("p", { className: "mt-1 opacity-50", children: "Ballon-Minimum 0 schaltet Ballooning ab. Kerne und RAM greifen bei laufender VM meist erst nach einem Neustart." })
  ] });
}
function GuestDetailsPanel({ hostId }) {
  const [details, setDetails] = useState3(null);
  const [error, setError] = useState3(null);
  const [editing, setEditing] = useState3(false);
  const [editMessage, setEditMessage] = useState3(null);
  const [reloadKey, setReloadKey] = useState3(0);
  useEffect4(() => {
    let cancelled = false;
    authedFetch(`/ext/proxmox/guests/${hostId}/details`).then(async (res) => {
      if (!res.ok) throw new Error(await errorText(res));
      return res.json();
    }).then((data) => {
      if (!cancelled) setDetails(data);
    }).catch((err) => {
      if (!cancelled) setError(err instanceof Error ? err.message : String(err));
    });
    return () => {
      cancelled = true;
    };
  }, [hostId, reloadKey]);
  if (error) return /* @__PURE__ */ jsxs2("p", { className: "mt-2 text-xs text-red-400", children: [
    "Details nicht verf\xFCgbar: ",
    error
  ] });
  if (!details) return /* @__PURE__ */ jsx3("p", { className: "mt-2 text-xs opacity-60", children: "Lade Details \u2026" });
  const d = details;
  const agent = d.kind === "qemu" ? agentText(d) : null;
  const cpu = d.kind === "qemu" ? `${d.cores * (d.sockets ?? 1)} vCPU${d.cpu_type ? ` (${d.cpu_type})` : ""}` : `${d.cores} Kerne`;
  const memory = d.kind === "qemu" ? `${formatMb(d.memory_mb)}${d.balloon_mb ? `, Ballooning ab ${formatMb(d.balloon_mb)}` : ""}` : `${formatMb(d.memory_mb)}${d.swap_mb ? ` + ${formatMb(d.swap_mb)} Swap` : ""}`;
  return /* @__PURE__ */ jsxs2("div", { className: "mt-2 text-xs", "data-testid": `details-${hostId}`, children: [
    deck().hasPermission("hosts.execute") && !editing && /* @__PURE__ */ jsx3("button", { type: "button", onClick: () => {
      setEditMessage(null);
      setEditing(true);
    }, className: "mb-2 px-2 py-1 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition", children: "Hardware \xE4ndern" }),
    editing && /* @__PURE__ */ jsx3(
      GuestEditForm,
      {
        hostId,
        details: d,
        onDone: (message) => {
          setEditing(false);
          setEditMessage(message);
          if (message) setReloadKey((n) => n + 1);
        }
      }
    ),
    editMessage && /* @__PURE__ */ jsx3("p", { className: "mb-2 opacity-90", role: "status", children: editMessage }),
    /* @__PURE__ */ jsxs2("dl", { className: "grid grid-cols-2 gap-x-6 gap-y-1 opacity-80 sm:grid-cols-4", children: [
      /* @__PURE__ */ jsxs2("div", { children: [
        /* @__PURE__ */ jsx3("dt", { className: "opacity-60", children: "Prozessor" }),
        /* @__PURE__ */ jsx3("dd", { children: cpu })
      ] }),
      /* @__PURE__ */ jsxs2("div", { children: [
        /* @__PURE__ */ jsx3("dt", { className: "opacity-60", children: "Arbeitsspeicher" }),
        /* @__PURE__ */ jsx3("dd", { children: memory })
      ] }),
      /* @__PURE__ */ jsxs2("div", { children: [
        /* @__PURE__ */ jsx3("dt", { className: "opacity-60", children: "System" }),
        /* @__PURE__ */ jsxs2("dd", { children: [
          d.os ?? "?",
          d.kind === "qemu" ? ` \xB7 ${d.bios} \xB7 ${d.machine}` : d.unprivileged ? " \xB7 unprivilegiert" : " \xB7 privilegiert"
        ] })
      ] }),
      /* @__PURE__ */ jsxs2("div", { children: [
        /* @__PURE__ */ jsx3("dt", { className: "opacity-60", children: "Autostart" }),
        /* @__PURE__ */ jsx3("dd", { children: d.onboot ? `ja${d.startup_order != null ? ` (Reihenfolge ${d.startup_order})` : ""}` : "nein" })
      ] }),
      agent && /* @__PURE__ */ jsxs2("div", { children: [
        /* @__PURE__ */ jsx3("dt", { className: "opacity-60", children: "Gast-Agent" }),
        /* @__PURE__ */ jsx3("dd", { className: agent.className, children: agent.text })
      ] }),
      d.features && d.features.length > 0 && /* @__PURE__ */ jsxs2("div", { children: [
        /* @__PURE__ */ jsx3("dt", { className: "opacity-60", children: "Funktionen" }),
        /* @__PURE__ */ jsx3("dd", { children: d.features.join(", ") })
      ] }),
      d.passthrough.length > 0 && /* @__PURE__ */ jsxs2("div", { children: [
        /* @__PURE__ */ jsx3("dt", { className: "opacity-60", children: "Durchgereicht" }),
        /* @__PURE__ */ jsx3("dd", { children: d.passthrough.join(", ") })
      ] }),
      d.tags.length > 0 && /* @__PURE__ */ jsxs2("div", { children: [
        /* @__PURE__ */ jsx3("dt", { className: "opacity-60", children: "Tags" }),
        /* @__PURE__ */ jsx3("dd", { children: d.tags.join(", ") })
      ] })
    ] }),
    /* @__PURE__ */ jsx3("p", { className: "mt-2 mb-1 font-medium opacity-60", children: "Laufwerke" }),
    /* @__PURE__ */ jsx3("ul", { className: "space-y-0.5", children: d.disks.map((disk) => /* @__PURE__ */ jsxs2("li", { className: disk.kind === "unused" ? "text-amber-300" : "opacity-80", children: [
      /* @__PURE__ */ jsx3("span", { className: "font-mono opacity-60", children: disk.slot }),
      " ",
      diskLabel(disk)
    ] }, disk.slot)) }),
    /* @__PURE__ */ jsx3("p", { className: "mt-2 mb-1 font-medium opacity-60", children: "Netzwerk" }),
    /* @__PURE__ */ jsxs2("ul", { className: "space-y-0.5", children: [
      d.networks.map((nic) => /* @__PURE__ */ jsxs2("li", { className: "opacity-80", children: [
        /* @__PURE__ */ jsx3("span", { className: "font-mono opacity-60", children: nic.slot }),
        " ",
        nic.name ? `${nic.name} \xB7 ` : "",
        nic.bridge ?? "?",
        nic.vlan ? ` (VLAN ${nic.vlan})` : "",
        " \xB7 ",
        nic.model ?? "?",
        " \xB7 ",
        /* @__PURE__ */ jsx3("span", { className: "font-mono", children: nic.mac ?? "?" }),
        nic.firewall ? " \xB7 Firewall" : "",
        nic.link_down ? " \xB7 getrennt" : "",
        nic.ips.length > 0 ? ` \xB7 ${nic.ips.join(", ")}` : nic.configured_ip ? ` \xB7 ${nic.configured_ip === "dhcp" ? "DHCP" : nic.configured_ip}` : ""
      ] }, nic.slot)),
      d.other_ips.length > 0 && /* @__PURE__ */ jsxs2("li", { className: "opacity-60", children: [
        "Weitere Adressen im Gast: ",
        d.other_ips.join(", ")
      ] })
    ] })
  ] });
}
function NodeHealthPanel({ hostId }) {
  const [health, setHealth] = useState3(null);
  const [error, setError] = useState3(null);
  useEffect4(() => {
    let cancelled = false;
    authedFetch(`/ext/proxmox/nodes/${hostId}/health`).then(async (res) => {
      if (!res.ok) throw new Error(await errorText(res));
      return res.json();
    }).then((data) => {
      if (!cancelled) setHealth(data);
    }).catch((err) => {
      if (!cancelled) setError(err instanceof Error ? err.message : String(err));
    });
    return () => {
      cancelled = true;
    };
  }, [hostId]);
  if (error) return /* @__PURE__ */ jsxs2("p", { className: "mt-2 text-xs text-red-400", children: [
    "Knotendaten nicht verf\xFCgbar: ",
    error
  ] });
  if (!health) return /* @__PURE__ */ jsx3("p", { className: "mt-2 text-xs opacity-60", children: "Lade Knotendaten \u2026" });
  const h = health;
  const memUsed = h.mem_total != null && h.mem_available != null ? h.mem_total - h.mem_available : null;
  return /* @__PURE__ */ jsxs2("div", { className: "mt-2 text-xs", "data-testid": `node-health-${hostId}`, children: [
    /* @__PURE__ */ jsxs2("dl", { className: "grid grid-cols-2 gap-x-6 gap-y-1 opacity-80 sm:grid-cols-4", children: [
      /* @__PURE__ */ jsxs2("div", { className: "col-span-2", children: [
        /* @__PURE__ */ jsx3("dt", { className: "opacity-60", children: "Prozessor" }),
        /* @__PURE__ */ jsxs2("dd", { children: [
          h.cpu_model ?? "?",
          " (",
          h.cpu_cores ?? "?",
          " Kerne / ",
          h.cpu_threads ?? "?",
          " Threads)"
        ] })
      ] }),
      /* @__PURE__ */ jsxs2("div", { children: [
        /* @__PURE__ */ jsx3("dt", { className: "opacity-60", children: "Last (1/5/15 min)" }),
        /* @__PURE__ */ jsxs2("dd", { children: [
          h.loadavg.map((l) => l.toFixed(2)).join(" / "),
          " \xB7 IO-Wartezeit ",
          h.io_wait_percent,
          " %"
        ] })
      ] }),
      /* @__PURE__ */ jsxs2("div", { children: [
        /* @__PURE__ */ jsx3("dt", { className: "opacity-60", children: "L\xE4uft seit" }),
        /* @__PURE__ */ jsx3("dd", { children: formatUptime(h.uptime_s ?? 0) })
      ] }),
      /* @__PURE__ */ jsxs2("div", { children: [
        /* @__PURE__ */ jsx3("dt", { className: "opacity-60", children: "RAM belegt" }),
        /* @__PURE__ */ jsxs2("dd", { children: [
          memUsed != null ? formatBytes2(memUsed) : "?",
          " / ",
          formatBytes2(h.mem_total ?? 0),
          h.ksm_shared ? ` \xB7 KSM teilt ${formatBytes2(h.ksm_shared)}` : ""
        ] })
      ] }),
      /* @__PURE__ */ jsxs2("div", { children: [
        /* @__PURE__ */ jsx3("dt", { className: "opacity-60", children: "Swap belegt" }),
        /* @__PURE__ */ jsxs2("dd", { children: [
          formatBytes2(h.swap_used ?? 0),
          " / ",
          formatBytes2(h.swap_total ?? 0)
        ] })
      ] }),
      /* @__PURE__ */ jsxs2("div", { children: [
        /* @__PURE__ */ jsx3("dt", { className: "opacity-60", children: "Systemplatte" }),
        /* @__PURE__ */ jsxs2("dd", { children: [
          formatBytes2(h.rootfs_used ?? 0),
          " / ",
          formatBytes2(h.rootfs_total ?? 0)
        ] })
      ] }),
      /* @__PURE__ */ jsxs2("div", { children: [
        /* @__PURE__ */ jsx3("dt", { className: "opacity-60", children: "System" }),
        /* @__PURE__ */ jsxs2("dd", { children: [
          "Proxmox ",
          h.pve_version ?? "?",
          " \xB7 Kernel ",
          h.kernel ?? "?",
          h.boot_mode ? ` \xB7 ${h.boot_mode}` : ""
        ] })
      ] })
    ] }),
    /* @__PURE__ */ jsx3("p", { className: "mt-2 mb-1 font-medium opacity-60", children: "Datentr\xE4ger" }),
    h.disks_error && /* @__PURE__ */ jsxs2("p", { className: "text-red-400", children: [
      "Nicht abrufbar: ",
      h.disks_error
    ] }),
    /* @__PURE__ */ jsx3("ul", { className: "space-y-0.5", children: h.disks.map((d) => /* @__PURE__ */ jsxs2("li", { className: "flex flex-wrap items-center gap-2 opacity-90", children: [
      /* @__PURE__ */ jsx3("span", { className: `rounded px-1.5 py-0.5 ${BADGE_TONE[d.tone] ?? BADGE_TONE.neutral}`, children: d.badge }),
      /* @__PURE__ */ jsx3("span", { children: d.model }),
      /* @__PURE__ */ jsxs2("span", { className: "opacity-60", children: [
        d.summary,
        d.power_on_hours != null ? ` \xB7 ${d.power_on_hours.toLocaleString()} Betriebsstunden` : ""
      ] })
    ] }, d.devpath)) })
  ] });
}
function NodeCard({ node, focused = false, visit = 0 }) {
  const [open, setOpen] = useState3(focused);
  useEffect4(() => {
    if (focused) setOpen(true);
  }, [focused, visit]);
  return /* @__PURE__ */ jsxs2("div", { id: `host-${node.id}`, className: `mb-2 rounded border border-white/10 p-2 text-sm ${focused ? FOCUS_CLASS : ""}`, children: [
    /* @__PURE__ */ jsx3(
      "button",
      {
        type: "button",
        onClick: () => setOpen((v) => !v),
        "aria-label": open ? "Knotendetails einklappen" : "Knotendetails anzeigen",
        className: "mr-2 opacity-60 hover:opacity-100",
        children: open ? "\u25BE" : "\u25B8"
      }
    ),
    /* @__PURE__ */ jsx3("span", { className: "font-medium", children: node.display_name }),
    " ",
    /* @__PURE__ */ jsxs2("span", { className: "opacity-60", children: [
      "(Knoten) \u2013 ",
      HOST_STATUS_LABEL[node.status] ?? node.status
    ] }),
    open && /* @__PURE__ */ jsx3(NodeHealthPanel, { hostId: node.id })
  ] });
}
function MetricsPanel({ hostId }) {
  const [metrics, setMetrics] = useState3(null);
  const [error, setError] = useState3(null);
  useEffect4(() => {
    let cancelled = false;
    authedFetch(`/hosts/${hostId}/metrics`).then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    }).then((data) => {
      if (!cancelled) setMetrics(data);
    }).catch((err) => {
      if (!cancelled) setError(err instanceof Error ? err.message : String(err));
    });
    return () => {
      cancelled = true;
    };
  }, [hostId]);
  if (error) return /* @__PURE__ */ jsxs2("p", { className: "text-xs text-red-400", children: [
    "Metriken nicht verf\xFCgbar: ",
    error
  ] });
  if (!metrics) return /* @__PURE__ */ jsx3("p", { className: "text-xs opacity-60", children: "Lade Metriken \u2026" });
  const v = metrics.values;
  return /* @__PURE__ */ jsxs2("dl", { className: "grid grid-cols-2 gap-x-6 gap-y-1 text-xs opacity-80 sm:grid-cols-4", children: [
    /* @__PURE__ */ jsxs2("div", { children: [
      /* @__PURE__ */ jsx3("dt", { className: "opacity-60", children: "CPU" }),
      /* @__PURE__ */ jsxs2("dd", { children: [
        v.cpu_percent?.toFixed(1) ?? "?",
        " %"
      ] })
    ] }),
    /* @__PURE__ */ jsxs2("div", { children: [
      /* @__PURE__ */ jsx3("dt", { className: "opacity-60", children: "RAM" }),
      /* @__PURE__ */ jsxs2("dd", { children: [
        formatBytes2(v.mem_used_bytes ?? 0),
        " / ",
        formatBytes2(v.mem_total_bytes ?? 0)
      ] })
    ] }),
    /* @__PURE__ */ jsxs2("div", { children: [
      /* @__PURE__ */ jsx3("dt", { className: "opacity-60", children: "Laufzeit" }),
      /* @__PURE__ */ jsx3("dd", { children: formatUptime(v.uptime_s ?? 0) })
    ] }),
    /* @__PURE__ */ jsxs2("div", { children: [
      /* @__PURE__ */ jsx3("dt", { className: "opacity-60", children: "Stand" }),
      /* @__PURE__ */ jsx3("dd", { children: new Date(metrics.sampled_at).toLocaleTimeString() })
    ] })
  ] });
}
function ConnectionsPanel() {
  const [connections, setConnections] = useState3(null);
  const [error, setError] = useState3(null);
  const [message, setMessage] = useState3(null);
  const [showAddForm, setShowAddForm] = useState3(false);
  const [newName, setNewName] = useState3("");
  const [newBaseUrl, setNewBaseUrl] = useState3("");
  const [newTokenId, setNewTokenId] = useState3("");
  const [newTlsInsecure, setNewTlsInsecure] = useState3(false);
  const [pending, setPending] = useState3(null);
  const load = useCallback2(() => {
    setError(null);
    authedFetch("/ext/proxmox/connections").then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    }).then(setConnections).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);
  useEffect4(() => {
    load();
  }, [load]);
  async function addConnection(e) {
    e.preventDefault();
    setPending("add");
    setMessage(null);
    try {
      const res = await authedFetch("/ext/proxmox/connections", {
        method: "POST",
        body: JSON.stringify({
          name: newName,
          base_url: newBaseUrl,
          token_id: newTokenId,
          tls_insecure_skip_verify: newTlsInsecure
        })
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      setMessage(`Verbindung "${newName}" angelegt \u2013 jetzt noch ein Token setzen.`);
      setNewName("");
      setNewBaseUrl("");
      setNewTokenId("");
      setNewTlsInsecure(false);
      setShowAddForm(false);
      load();
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPending(null);
    }
  }
  async function setToken(name, replacing) {
    const value = await deck().promptDialog(
      replacing ? `Neues API-Token f\xFCr "${name}" (ersetzt das gespeicherte, Klartext, nur hier eingeben):` : `API-Token f\xFCr "${name}" (Klartext, nur hier eingeben):`
    );
    if (!value) return;
    setPending(`token:${name}`);
    setMessage(null);
    try {
      const res = await authedFetch(`/extensions/proxmox/secrets`, {
        method: "PUT",
        body: JSON.stringify({ label: `proxmox-token:${name}`, value })
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(errorFromBody(body, res.status));
      }
      setMessage(`Token f\xFCr "${name}" gesetzt.`);
      load();
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPending(null);
    }
  }
  async function toggleEnabled(conn) {
    setPending(`toggle:${conn.name}`);
    setMessage(null);
    try {
      const res = await authedFetch(`/ext/proxmox/connections/${conn.name}`, {
        method: "PUT",
        body: JSON.stringify({ enabled: !conn.enabled })
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(errorFromBody(body, res.status));
      }
      load();
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPending(null);
    }
  }
  async function removeConnection(name) {
    const ok = await deck().confirmDialog(
      `Verbindung "${name}" wirklich entfernen? Das gesetzte Token wird mit gel\xF6scht.`,
      { danger: true, confirmLabel: "Entfernen" }
    );
    if (!ok) return;
    setPending(`remove:${name}`);
    setMessage(null);
    try {
      const res = await authedFetch(`/ext/proxmox/connections/${name}`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) {
        const body = await res.json().catch(() => ({}));
        throw new Error(errorFromBody(body, res.status));
      }
      load();
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPending(null);
    }
  }
  if (error) return /* @__PURE__ */ jsxs2("p", { className: "mb-4 text-xs text-red-400", children: [
    "Verbindungen nicht ladbar: ",
    error
  ] });
  return /* @__PURE__ */ jsxs2("details", { className: "mb-6 p-3 panel", open: connections?.length === 0, children: [
    /* @__PURE__ */ jsx3("summary", { className: "cursor-pointer text-sm font-medium", children: "Verbindungen verwalten" }),
    /* @__PURE__ */ jsxs2("div", { className: "mt-3", children: [
      message && /* @__PURE__ */ jsx3("p", { className: "mb-2 text-xs opacity-80", children: message }),
      connections && connections.length === 0 && /* @__PURE__ */ jsx3("p", { className: "mb-2 text-xs opacity-60", children: "Noch keine Verbindung konfiguriert." }),
      connections && connections.length > 0 && /* @__PURE__ */ jsxs2("table", { className: "mb-3 w-full text-xs", children: [
        /* @__PURE__ */ jsx3("thead", { children: /* @__PURE__ */ jsxs2("tr", { className: "border-b border-white/10 text-left uppercase opacity-60", children: [
          /* @__PURE__ */ jsx3("th", { className: "py-1 w-1/6", children: "Name" }),
          /* @__PURE__ */ jsx3("th", { className: "py-1 w-1/3", children: "Adresse" }),
          /* @__PURE__ */ jsx3("th", { className: "py-1 whitespace-nowrap", children: "Zertifikat nicht pr\xFCfen" }),
          /* @__PURE__ */ jsx3("th", { className: "py-1 whitespace-nowrap", children: "Token" }),
          /* @__PURE__ */ jsx3("th", { className: "py-1 whitespace-nowrap", children: "Aktiv" }),
          /* @__PURE__ */ jsx3("th", { className: "py-1 whitespace-nowrap", children: "Aktionen" })
        ] }) }),
        /* @__PURE__ */ jsx3("tbody", { className: "divide-y divide-white/5", children: connections.map((c) => /* @__PURE__ */ jsxs2("tr", { children: [
          /* @__PURE__ */ jsx3("td", { className: "py-1.5 break-words", children: c.name }),
          /* @__PURE__ */ jsx3("td", { className: "py-1.5 break-all opacity-70", children: c.base_url }),
          /* @__PURE__ */ jsx3("td", { className: "py-1.5 whitespace-nowrap opacity-70", children: c.tls_insecure_skip_verify ? "ja" : "nein" }),
          /* @__PURE__ */ jsx3("td", { className: "py-1.5 whitespace-nowrap", children: c.has_token ? "gesetzt" : /* @__PURE__ */ jsx3("span", { className: "text-amber-400", children: "fehlt" }) }),
          /* @__PURE__ */ jsx3("td", { className: "py-1.5", children: /* @__PURE__ */ jsx3(
            "button",
            {
              type: "button",
              disabled: pending === `toggle:${c.name}`,
              onClick: () => void toggleEnabled(c),
              className: "px-2 py-0.5 disabled:opacity-40 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
              children: c.enabled ? "aktiv" : "deaktiviert"
            }
          ) }),
          /* @__PURE__ */ jsx3("td", { className: "py-1.5", children: /* @__PURE__ */ jsxs2("div", { className: "flex gap-1.5", children: [
            /* @__PURE__ */ jsx3(
              "button",
              {
                type: "button",
                disabled: pending === `token:${c.name}`,
                onClick: () => void setToken(c.name, c.has_token),
                className: "px-2 py-1 disabled:opacity-40 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
                children: c.has_token ? "Token ersetzen" : "Token setzen"
              }
            ),
            /* @__PURE__ */ jsx3(
              "button",
              {
                type: "button",
                disabled: pending === `remove:${c.name}`,
                onClick: () => void removeConnection(c.name),
                className: "px-2 py-1 disabled:opacity-40 border border-red-500/40 bg-red-500/15 text-red-200 hover:bg-red-500/25 rounded-lg",
                children: "Entfernen"
              }
            )
          ] }) })
        ] }, c.name)) })
      ] }),
      !showAddForm && /* @__PURE__ */ jsx3(
        "button",
        {
          type: "button",
          onClick: () => setShowAddForm(true),
          className: "px-2 py-1 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
          children: "+ Neue Verbindung"
        }
      ),
      showAddForm && /* @__PURE__ */ jsxs2("form", { onSubmit: (e) => void addConnection(e), className: "flex flex-wrap items-end gap-2 text-xs", children: [
        /* @__PURE__ */ jsxs2("label", { className: "flex flex-col gap-1", children: [
          "Name",
          /* @__PURE__ */ jsx3("input", { required: true, value: newName, onChange: (e) => setNewName(e.target.value), className: "px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]" })
        ] }),
        /* @__PURE__ */ jsxs2("label", { className: "flex flex-col gap-1", children: [
          "Adresse (URL)",
          /* @__PURE__ */ jsx3(
            "input",
            {
              required: true,
              value: newBaseUrl,
              onChange: (e) => setNewBaseUrl(e.target.value),
              placeholder: "https://192.168.2.x:8006",
              className: "w-56 px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]"
            }
          )
        ] }),
        /* @__PURE__ */ jsxs2("label", { className: "flex flex-col gap-1", children: [
          "Token-ID",
          /* @__PURE__ */ jsx3(
            "input",
            {
              required: true,
              value: newTokenId,
              onChange: (e) => setNewTokenId(e.target.value),
              placeholder: "root@pam!dashboard",
              className: "w-40 px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]"
            }
          )
        ] }),
        /* @__PURE__ */ jsxs2("label", { className: "flex items-center gap-1", children: [
          /* @__PURE__ */ jsx3("input", { type: "checkbox", checked: newTlsInsecure, onChange: (e) => setNewTlsInsecure(e.target.checked) }),
          "Zertifikat nicht pr\xFCfen"
        ] }),
        /* @__PURE__ */ jsx3("button", { type: "submit", disabled: pending === "add", className: "px-2 py-1 disabled:opacity-40 accent-gradient text-white rounded-lg shadow-md shadow-black/30 hover:brightness-110 font-medium", children: "Anlegen" }),
        /* @__PURE__ */ jsx3("button", { type: "button", onClick: () => setShowAddForm(false), className: "px-2 py-1 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition", children: "Abbrechen" })
      ] })
    ] })
  ] });
}
function ProxmoxNodePage() {
  const unmountSignal = useUnmountSignal();
  const [urlParams, updateUrl, visits] = useUrlParams();
  const focusHost = urlParams.get("host") || null;
  const focusTasks = urlParams.get("tasks") === "1";
  const [hosts, setHosts] = useState3(null);
  const [error, setError] = useState3(null);
  const [pending, setPending] = useState3(() => /* @__PURE__ */ new Set());
  const [actionMessages, setActionMessages] = useState3(() => /* @__PURE__ */ new Map());
  const [expanded, setExpanded] = useState3(focusHost);
  const expandedFor = useRef2(visits);
  useEffect4(() => {
    if (expandedFor.current === visits) return;
    expandedFor.current = visits;
    setExpanded(focusHost);
  }, [visits, focusHost]);
  const taskFocus = focusTasks ? focusHost : null;
  const [snapshotRefresh, setSnapshotRefresh] = useState3(0);
  const [storage, setStorage] = useState3(null);
  const [tasks, setTasks] = useState3(null);
  const [updates, setUpdates] = useState3(null);
  const [updatesErrors, setUpdatesErrors] = useState3([]);
  const [storageErrors, setStorageErrors] = useState3([]);
  useEffect4(() => {
    authedFetch("/ext/proxmox/updates").then((res) => res.ok ? res.json() : { nodes: [] }).then((body) => {
      setUpdates(body.nodes);
      setUpdatesErrors(body.errors ?? []);
    }).catch(() => setUpdates([]));
  }, []);
  const [showConsoleTasks, setShowConsoleTasks] = useState3(false);
  useEffect4(() => {
    authedFetch(`/ext/proxmox/tasks?limit=100${showConsoleTasks ? "&include_console=true" : ""}`).then((res) => res.ok ? res.json() : { tasks: [] }).then((body) => setTasks(body.tasks)).catch(() => setTasks([]));
  }, [showConsoleTasks, snapshotRefresh]);
  useEffect4(() => {
    authedFetch("/ext/proxmox/storage").then((res) => res.ok ? res.json() : { pools: [] }).then((body) => {
      setStorage(body.pools);
      setStorageErrors(body.errors ?? []);
    }).catch(() => setStorage([]));
  }, []);
  const load = useCallback2(() => {
    setError(null);
    authedFetch("/hosts?tag=proxmox").then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    }).then(setHosts).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);
  useEffect4(() => {
    load();
  }, [load]);
  const scrolledFor = useRef2(null);
  useEffect4(() => {
    if (!hosts || !focusHost || scrolledFor.current === visits) return;
    const target = hosts.find((h) => h.id === focusHost);
    if (!target) return;
    if (focusTasks && !tasks) return;
    scrolledFor.current = visits;
    scrollToElement(focusTasks ? `tasks-${parseProviderRef(target.provider_ref).connection}` : `host-${target.id}`);
  }, [hosts, tasks, focusHost, focusTasks, visits]);
  async function trigger(hostId, hostLabel, actionType, confirmText, payload = {}, dangerConfirm = true) {
    if (confirmText) {
      const ok = await deck().confirmDialog(`"${hostLabel}" ${confirmText}`, { danger: dangerConfirm });
      if (!ok) return;
    }
    const snapname = typeof payload.snapname === "string" && payload.snapname ? payload.snapname : void 0;
    const key = pendingKey(hostId, actionType, snapname);
    setPending((prev) => new Set(prev).add(key));
    setActionMessages(/* @__PURE__ */ new Map());
    try {
      const { action, approved } = await runAction(`/hosts/${hostId}/actions/${actionType}`, {
        method: "POST",
        body: JSON.stringify({ payload, reason: `\xDCber die Proxmox-Node-Seite ausgel\xF6st (${actionType}).` })
      }, { signal: unmountSignal() });
      if (!approved && action.status === "proposed") {
        setMessage(key, `${hostLabel}: ${actionLabel(actionType, snapname)} vorgeschlagen \u2013 Freigabe durch einen Admin n\xF6tig, siehe "Aktionen".`);
      } else {
        setMessage(key, describeOutcome(hostLabel, actionType, action, snapname));
      }
      load();
      setSnapshotRefresh((n) => n + 1);
    } catch (err) {
      setMessage(key, `${hostLabel}: Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPending((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    }
  }
  function setMessage(key, text) {
    setActionMessages((prev) => {
      const next = new Map(prev);
      next.delete(key);
      next.set(key, text);
      return next;
    });
  }
  if (!hosts && !error) return /* @__PURE__ */ jsx3("div", { className: "p-6 text-sm opacity-60", children: "Lade \u2026" });
  if (error) return /* @__PURE__ */ jsxs2("div", { className: "p-6 text-sm text-red-400", children: [
    "Fehler: ",
    error
  ] });
  const nodes = (hosts ?? []).filter((h) => h.kind === "hypervisor" || h.kind === "node");
  const guests = (hosts ?? []).filter((h) => h.kind === "vm" || h.kind === "lxc");
  const connections = [...new Set((hosts ?? []).map((h) => parseProviderRef(h.provider_ref).connection))].sort();
  const taskHost = (hosts ?? []).find((h) => h.id === taskFocus) ?? null;
  const taskRef = taskHost ? parseProviderRef(taskHost.provider_ref) : null;
  function tasksFor(connection) {
    const all = (tasks ?? []).filter((t) => t.connection === connection);
    if (!taskRef || taskRef.connection !== connection) return all;
    return all.filter((t) => t.node === taskRef.node && (taskRef.vmid === null || t.guest_id === taskRef.vmid));
  }
  return /* @__PURE__ */ jsxs2("div", { className: "mx-auto w-full max-w-7xl p-4 sm:p-6", children: [
    /* @__PURE__ */ jsx3("h2", { className: "mb-4 font-semibold text-xl tracking-tight", children: "Proxmox-Knoten & VMs" }),
    /* @__PURE__ */ jsx3(ConnectionsPanel, {}),
    [...actionMessages].map(([key, text]) => /* @__PURE__ */ jsx3("p", { className: "mb-3 text-sm opacity-80", children: text }, key)),
    hosts && hosts.length === 0 && /* @__PURE__ */ jsx3(
      EmptyState,
      {
        icon: "refresh",
        title: "Noch keine Proxmox-Server gefunden",
        text: "Trag daf\xFCr die Adresse deines Proxmox-Servers und einen API-Token ein (oben unter \u201EVerbindungen verwalten\u201C oder in den Moduleinstellungen). Danach liest Nodvard Deck alle 5 Minuten alle Knoten, VMs und Container von selbst ein.",
        action: /* @__PURE__ */ jsx3(SettingsLink, { to: "/settings/extensions/proxmox", permission: "extensions.manage", children: "Proxmox einrichten" })
      }
    ),
    connections.map((connection) => /* @__PURE__ */ jsxs2("section", { className: "mb-6", children: [
      /* @__PURE__ */ jsx3("h3", { className: "mb-2 text-sm font-medium uppercase tracking-wide opacity-60", children: connection }),
      nodes.filter((n) => parseProviderRef(n.provider_ref).connection === connection).map((node) => /* @__PURE__ */ jsx3(NodeCard, { node, focused: node.id === focusHost, visit: visits }, node.id)),
      /* @__PURE__ */ jsxs2("table", { className: "w-full text-sm", children: [
        /* @__PURE__ */ jsx3("thead", { children: /* @__PURE__ */ jsxs2("tr", { className: "border-b border-white/10 text-left text-xs uppercase opacity-60", children: [
          /* @__PURE__ */ jsx3("th", { className: "py-1 w-6" }),
          /* @__PURE__ */ jsx3("th", { className: "py-1 w-1/3", children: "Name" }),
          /* @__PURE__ */ jsx3("th", { className: "py-1 whitespace-nowrap", children: "Typ" }),
          /* @__PURE__ */ jsx3("th", { className: "py-1 whitespace-nowrap", children: "Knoten" }),
          /* @__PURE__ */ jsx3("th", { className: "py-1 whitespace-nowrap", children: "Status" }),
          /* @__PURE__ */ jsx3("th", { className: "py-1", children: "Aktionen" })
        ] }) }),
        /* @__PURE__ */ jsx3("tbody", { className: "divide-y divide-white/5", children: guests.filter((g) => parseProviderRef(g.provider_ref).connection === connection).map((host) => {
          const { node } = parseProviderRef(host.provider_ref);
          const isExpanded = expanded === host.id;
          return /* @__PURE__ */ jsxs2(Fragment2, { children: [
            /* @__PURE__ */ jsxs2("tr", { id: `host-${host.id}`, className: host.id === focusHost ? FOCUS_CLASS : void 0, children: [
              /* @__PURE__ */ jsx3("td", { className: "py-1.5", children: /* @__PURE__ */ jsx3(
                "button",
                {
                  type: "button",
                  onClick: () => setExpanded(isExpanded ? null : host.id),
                  "aria-label": isExpanded ? "Details einklappen" : "Details anzeigen",
                  className: "opacity-60 hover:opacity-100",
                  children: isExpanded ? "\u25BE" : "\u25B8"
                }
              ) }),
              /* @__PURE__ */ jsx3("td", { className: "py-1.5 break-words", children: host.display_name }),
              /* @__PURE__ */ jsx3("td", { className: "py-1.5 whitespace-nowrap opacity-70", children: KIND_LABEL[host.kind ?? ""] ?? host.kind }),
              /* @__PURE__ */ jsx3("td", { className: "py-1.5 whitespace-nowrap opacity-70", children: node }),
              /* @__PURE__ */ jsx3("td", { className: "py-1.5 whitespace-nowrap", children: HOST_STATUS_LABEL[host.status] ?? host.status }),
              /* @__PURE__ */ jsx3("td", { className: "py-1.5", children: /* @__PURE__ */ jsxs2("div", { className: "flex flex-wrap gap-1.5", children: [
                host.status === "up" ? /* @__PURE__ */ jsx3(
                  "a",
                  {
                    href: `/console/${host.id}`,
                    target: "_blank",
                    rel: "noreferrer",
                    className: "accent-soft rounded px-2 py-1 text-xs hover:brightness-125",
                    children: "Konsole"
                  }
                ) : /* @__PURE__ */ jsx3(
                  "span",
                  {
                    title: "Nur f\xFCr laufende VMs/Container verf\xFCgbar.",
                    className: "cursor-not-allowed rounded bg-white/5 px-2 py-1 text-xs opacity-40",
                    children: "Konsole"
                  }
                ),
                VM_ACTIONS.filter((a) => !a.showWhen || a.showWhen(host.status)).map((action) => /* @__PURE__ */ jsx3(
                  "button",
                  {
                    type: "button",
                    disabled: pending.has(`${host.id}:${action.type}`),
                    onClick: () => void trigger(host.id, host.display_name, action.type, action.confirm, {}, action.dangerConfirm),
                    className: `px-2 py-1 text-xs disabled:opacity-40 border rounded-lg transition ${action.variant === "danger" ? "border-red-500/40 bg-red-500/15 text-red-200 hover:bg-red-500/25" : "border-white/10 bg-white/[0.06] hover:bg-white/[0.12]"}`,
                    children: pending.has(`${host.id}:${action.type}`) ? "\u2026" : action.label
                  },
                  action.type
                ))
              ] }) })
            ] }),
            isExpanded && /* @__PURE__ */ jsxs2("tr", { children: [
              /* @__PURE__ */ jsx3("td", {}),
              /* @__PURE__ */ jsxs2("td", { colSpan: 5, className: "bg-black/20 py-2", children: [
                /* @__PURE__ */ jsx3(MetricsPanel, { hostId: host.id }),
                /* @__PURE__ */ jsx3(GuestDetailsPanel, { hostId: host.id }),
                /* @__PURE__ */ jsx3("p", { className: "mt-2 mb-1 text-xs font-medium opacity-60", children: "Snapshots" }),
                /* @__PURE__ */ jsx3(
                  SnapshotsPanel,
                  {
                    hostId: host.id,
                    refreshKey: snapshotRefresh,
                    isPending: (actionType, snapname) => pending.has(pendingKey(host.id, actionType, snapname)),
                    onAction: (actionType, snapname) => void trigger(
                      host.id,
                      host.display_name,
                      actionType,
                      actionType === "vm.snapshot_rollback" ? `auf Snapshot "${snapname}" zur\xFCckrollen? Der Gast wird dabei gestoppt, alles danach geht verloren.` : `Snapshot "${snapname}" l\xF6schen?`,
                      { snapname }
                    )
                  }
                )
              ] })
            ] })
          ] }, host.id);
        }) })
      ] }),
      /* @__PURE__ */ jsx3(UnreachableHint, { connection, sources: [["Updates", updatesErrors], ["Speicher", storageErrors]] }),
      updates && updates.some((u) => u.connection === connection) && /* @__PURE__ */ jsx3(UpdatesSection, { nodes: updates.filter((u) => u.connection === connection) }),
      storage && storage.some((p) => p.connection === connection) && /* @__PURE__ */ jsx3(StorageSection, { pools: storage.filter((p) => p.connection === connection) }),
      tasks && /* @__PURE__ */ jsx3(
        TasksSection,
        {
          id: `tasks-${connection}`,
          tasks: tasksFor(connection),
          showConsole: showConsoleTasks,
          onToggleConsole: () => setShowConsoleTasks((v) => !v),
          focused: taskRef?.connection === connection,
          visit: visits,
          filterLabel: taskRef?.connection === connection ? taskHost?.display_name : void 0,
          onClearFilter: () => updateUrl({ tasks: null })
        }
      )
    ] }, connection))
  ] });
}
export {
  ProxmoxNodePage,
  editChanges
};
