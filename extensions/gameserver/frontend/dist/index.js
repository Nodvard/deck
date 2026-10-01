// src/GameServerPage.tsx
import { useCallback as useCallback2, useEffect as useEffect4, useRef as useRef2, useState as useState3 } from "react";

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

// src/GameServerPage.tsx
import { jsx as jsx3, jsxs as jsxs2 } from "react/jsx-runtime";
var ACTION_STATUS_LABEL2 = { ...ACTION_STATUS_LABEL, succeeded: "erledigt" };
var ACTION_LABEL = { start: "Starten", stop: "Stoppen", restart: "Neustarten", backup: "Welt sichern" };
var TONE = {
  good: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  danger: "bg-red-500/15 text-red-300 border-red-500/30",
  warn: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  neutral: "bg-white/10 text-white/70 border-white/10"
};
function ago(seconds) {
  if (seconds == null) return "unbekannt";
  if (seconds < 60) return "gerade eben";
  if (seconds < 3600) return `vor ${Math.floor(seconds / 60)} min`;
  if (seconds < 86400) return `vor ${Math.floor(seconds / 3600)} h`;
  return `vor ${Math.floor(seconds / 86400)} T`;
}
function size(bytes) {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
  return `${Math.round(bytes / 1e3)} KB`;
}
function Stat({ label, value, sub }) {
  return /* @__PURE__ */ jsxs2("div", { className: "panel p-4", children: [
    /* @__PURE__ */ jsx3("p", { className: "text-[11px] font-medium uppercase tracking-wider text-white/50", children: label }),
    /* @__PURE__ */ jsx3("div", { className: "mt-1.5 text-2xl font-semibold tabular-nums", children: value }),
    sub && /* @__PURE__ */ jsx3("p", { className: "mt-0.5 text-xs text-white/50", children: sub })
  ] });
}
function Row({ label, value }) {
  return /* @__PURE__ */ jsxs2("div", { className: "flex justify-between gap-4 py-1.5 text-sm", children: [
    /* @__PURE__ */ jsx3("span", { className: "text-white/55", children: label }),
    /* @__PURE__ */ jsx3("span", { className: "text-right", children: value })
  ] });
}
function SettingsPanel({ server, profiles, onSaved }) {
  const [profileId, setProfileId] = useState3(server.config.profile);
  const [values, setValues] = useState3(server.config.values);
  const [error, setError] = useState3(null);
  const [saving, setSaving] = useState3(false);
  const profile = profiles.find((p) => p.id === profileId);
  const detected = {
    service_name: server.details.service_name,
    log_path: server.details.paths?.log,
    world_dir: server.details.paths?.world,
    backup_dir: server.details.paths?.backup
  };
  async function save() {
    setSaving(true);
    setError(null);
    const res = await authedFetch(`/ext/gameserver/servers/${server.host_id}/config`, {
      method: "PUT",
      body: JSON.stringify({ profile: profileId, values })
    });
    setSaving(false);
    if (!res.ok) {
      setError(await errorText(res));
      return;
    }
    onSaved();
  }
  return /* @__PURE__ */ jsxs2("div", { className: "panel mt-4 p-4", "data-testid": `settings-${server.host_id}`, children: [
    /* @__PURE__ */ jsx3("p", { className: "mb-3 text-sm font-semibold", children: "Einstellungen" }),
    /* @__PURE__ */ jsxs2("label", { className: "mb-3 block text-sm", children: [
      /* @__PURE__ */ jsx3("span", { className: "mb-1 block text-xs text-white/55", children: "Spiel-Profil" }),
      /* @__PURE__ */ jsx3("select", { value: profileId, onChange: (e) => setProfileId(e.target.value), className: "w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2", children: profiles.map((p) => /* @__PURE__ */ jsx3("option", { value: p.id, children: p.label }, p.id)) })
    ] }),
    /* @__PURE__ */ jsx3("div", { className: "grid gap-3 md:grid-cols-2", children: profile?.fields.map((field) => /* @__PURE__ */ jsxs2("label", { className: "block text-sm", children: [
      /* @__PURE__ */ jsx3("span", { className: "mb-1 block text-xs text-white/55", children: field.label }),
      /* @__PURE__ */ jsx3(
        "input",
        {
          value: values[field.key] ?? "",
          onChange: (e) => setValues({ ...values, [field.key]: e.target.value }),
          placeholder: detected[field.key] ? `automatisch: ${detected[field.key]}` : field.default || "automatisch",
          "aria-label": field.label,
          className: "w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 font-mono text-xs placeholder:text-white/30"
        }
      ),
      /* @__PURE__ */ jsx3("span", { className: "mt-1 block text-[11px] text-white/40", children: field.help })
    ] }, field.key)) }),
    error && /* @__PURE__ */ jsx3("p", { className: "mt-2 text-sm text-red-400", children: error }),
    /* @__PURE__ */ jsx3("div", { className: "mt-3 flex justify-end", children: /* @__PURE__ */ jsx3("button", { type: "button", disabled: saving, onClick: () => void save(), className: "accent-gradient rounded-lg px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50", children: saving ? "Speichere \u2026" : "Speichern" }) })
  ] });
}
function ServerCard({ server, profiles, onAction, pending, reload }) {
  const [copied, setCopied] = useState3(false);
  const [showSettings, setShowSettings] = useState3(false);
  const d = server.details;
  const canConfigure = deck().hasPermission("settings.write");
  const canExecute = deck().hasPermission("hosts.execute");
  const profileLabel = profiles.find((p) => p.id === server.config.profile)?.label ?? server.config.profile;
  async function copy() {
    if (!server.join_code) return;
    try {
      await navigator.clipboard.writeText(server.join_code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2e3);
    } catch {
    }
  }
  const button = (action, style) => /* @__PURE__ */ jsx3(
    "button",
    {
      type: "button",
      disabled: pending === `${server.host_id}:${action}`,
      onClick: () => onAction(server, action),
      className: `rounded-lg px-3 py-1.5 text-xs font-medium disabled:opacity-40 ${style}`,
      children: pending === `${server.host_id}:${action}` ? "\u2026" : ACTION_LABEL[action]
    },
    action
  );
  return /* @__PURE__ */ jsxs2("section", { id: `host-${server.host_id}`, className: "mb-8 scroll-mt-4", "data-testid": `server-${server.host_id}`, children: [
    /* @__PURE__ */ jsxs2("div", { className: "flex flex-wrap items-center gap-3", children: [
      /* @__PURE__ */ jsx3("span", { className: "accent-gradient grid h-11 w-11 place-items-center rounded-xl text-lg font-bold text-white shadow-lg shadow-black/40", children: server.name.slice(0, 1).toUpperCase() }),
      /* @__PURE__ */ jsxs2("div", { className: "min-w-0", children: [
        /* @__PURE__ */ jsx3("h3", { className: "text-lg font-semibold leading-tight", children: server.name }),
        /* @__PURE__ */ jsxs2("p", { className: "text-xs text-white/50", children: [
          profileLabel,
          server.version ? ` \xB7 Version ${server.version}` : ""
        ] })
      ] }),
      /* @__PURE__ */ jsx3("span", { className: `rounded-full border px-2.5 py-0.5 text-xs font-medium ${TONE[server.tone] ?? TONE.neutral}`, children: server.service_label }),
      /* @__PURE__ */ jsxs2("div", { className: "ml-auto flex flex-wrap gap-2", children: [
        canExecute && server.can_start && button("start", "accent-gradient text-white"),
        canExecute && server.running && button("restart", "bg-white/10 hover:bg-white/20"),
        canExecute && server.running && button("backup", "bg-white/10 hover:bg-white/20"),
        canExecute && server.can_stop && button("stop", "bg-red-500/20 text-red-200 hover:bg-red-500/30"),
        canConfigure && /* @__PURE__ */ jsx3("button", { type: "button", onClick: () => setShowSettings((v) => !v), className: "rounded-lg bg-white/5 px-3 py-1.5 text-xs hover:bg-white/10", children: showSettings ? "Einstellungen schlie\xDFen" : "Einstellungen" })
      ] })
    ] }),
    (server.error || d.error) && /* @__PURE__ */ jsxs2("p", { className: "mt-3 text-sm text-red-400", children: [
      "Nicht abrufbar: ",
      server.error ?? d.error
    ] }),
    showSettings && /* @__PURE__ */ jsx3(SettingsPanel, { server, profiles, onSaved: () => {
      setShowSettings(false);
      reload();
    } }),
    /* @__PURE__ */ jsxs2("div", { className: "mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4", children: [
      /* @__PURE__ */ jsx3(
        Stat,
        {
          label: "Join-Code",
          value: server.join_code ? /* @__PURE__ */ jsxs2("span", { className: "flex items-center gap-2", children: [
            /* @__PURE__ */ jsx3("span", { className: "font-mono tracking-[0.2em]", children: server.join_code }),
            /* @__PURE__ */ jsx3("button", { type: "button", onClick: () => void copy(), className: "rounded-md px-2 py-0.5 text-xs font-normal tracking-normal border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition", children: copied ? "Kopiert!" : "Kopieren" })
          ] }) : /* @__PURE__ */ jsx3("span", { className: "text-base text-white/50", children: "\u2013" }),
          sub: server.join_code ? `vergeben ${ago(d.join_code_age_s)}` : server.join_code_display
        }
      ),
      /* @__PURE__ */ jsx3(Stat, { label: "Spieler online", value: server.players_online ?? "\u2013", sub: d.recent_players?.length ? `${d.recent_players.length} bekannte Spieler` : void 0 }),
      /* @__PURE__ */ jsx3(Stat, { label: "Welt gespeichert", value: /* @__PURE__ */ jsx3("span", { className: "text-xl", children: ago(d.last_save_age_s) }), sub: d.world?.name ? `${d.world.name} \xB7 ${size(d.world.size)}` : void 0 }),
      /* @__PURE__ */ jsx3(
        Stat,
        {
          label: "Server-Prozess",
          value: /* @__PURE__ */ jsx3("span", { className: "text-xl", children: d.process ? `${(d.process.ram_mb / 1024).toFixed(1)} GB` : "\u2013" }),
          sub: d.process ? `RAM \xB7 ${d.process.responding ? "reagiert" : "reagiert nicht"}` : "l\xE4uft nicht"
        }
      )
    ] }),
    /* @__PURE__ */ jsxs2("div", { className: "mt-3 grid gap-3 lg:grid-cols-3", children: [
      /* @__PURE__ */ jsxs2("div", { className: "panel p-4", children: [
        /* @__PURE__ */ jsx3("p", { className: "mb-2 text-sm font-semibold", children: "Server" }),
        /* @__PURE__ */ jsxs2("div", { className: "divide-y divide-white/5", children: [
          /* @__PURE__ */ jsx3(Row, { label: "Name", value: d.server?.name ?? "\u2013" }),
          /* @__PURE__ */ jsx3(Row, { label: "Welt", value: d.server?.world ?? "\u2013" }),
          /* @__PURE__ */ jsx3(Row, { label: "Port", value: d.server?.port ?? "\u2013" }),
          /* @__PURE__ */ jsx3(Row, { label: "Crossplay", value: d.server ? d.server.crossplay ? "an" : "aus" : "\u2013" }),
          /* @__PURE__ */ jsx3(Row, { label: "\xD6ffentlich", value: d.server?.public == null ? "\u2013" : d.server.public ? "ja" : "nein" }),
          /* @__PURE__ */ jsx3(Row, { label: "Passwort", value: d.server ? d.server.has_password ? "gesetzt" : "keins" : "\u2013" }),
          d.server?.preset && /* @__PURE__ */ jsx3(Row, { label: "Voreinstellung", value: d.server.preset }),
          (d.server?.modifiers ?? []).map((m) => /* @__PURE__ */ jsx3(Row, { label: "Modifikator", value: m }, m))
        ] })
      ] }),
      /* @__PURE__ */ jsxs2("div", { className: "panel p-4", children: [
        /* @__PURE__ */ jsx3("p", { className: "mb-2 text-sm font-semibold", children: "Zuletzt gesehen" }),
        (d.recent_players ?? []).length === 0 && /* @__PURE__ */ jsx3("p", { className: "text-sm text-white/50", children: "Noch niemand." }),
        /* @__PURE__ */ jsx3("ul", { className: "divide-y divide-white/5", children: (d.recent_players ?? []).map((p) => /* @__PURE__ */ jsxs2("li", { className: "flex justify-between py-1.5 text-sm", children: [
          /* @__PURE__ */ jsx3("span", { children: p.name }),
          /* @__PURE__ */ jsx3("span", { className: "text-white/50", children: ago(p.last_seen_age_s) })
        ] }, p.name)) })
      ] }),
      /* @__PURE__ */ jsxs2("div", { className: "panel p-4", children: [
        /* @__PURE__ */ jsx3("p", { className: "mb-2 text-sm font-semibold", children: "Sicherungen" }),
        /* @__PURE__ */ jsx3("p", { className: "mb-1 text-[11px] uppercase tracking-wider text-white/45", children: "Eigene" }),
        (d.backups ?? []).length === 0 && /* @__PURE__ */ jsx3("p", { className: "mb-2 text-sm text-white/50", children: "Noch keine -- \u201EWelt sichern\u201C legt eine an." }),
        /* @__PURE__ */ jsx3("ul", { className: "mb-3", children: (d.backups ?? []).map((b) => /* @__PURE__ */ jsxs2("li", { className: "flex justify-between py-1 text-sm", children: [
          /* @__PURE__ */ jsx3("span", { className: "font-mono text-xs", children: b.name }),
          /* @__PURE__ */ jsx3("span", { className: "text-white/50", children: size(b.size) })
        ] }, b.name)) }),
        /* @__PURE__ */ jsx3("p", { className: "mb-1 text-[11px] uppercase tracking-wider text-white/45", children: "Automatisch (vom Spiel)" }),
        /* @__PURE__ */ jsx3("ul", { children: (d.auto_backups ?? []).map((b) => /* @__PURE__ */ jsxs2("li", { className: "flex justify-between py-1 text-sm", children: [
          /* @__PURE__ */ jsx3("span", { children: ago(b.age_s) }),
          /* @__PURE__ */ jsx3("span", { className: "text-white/50", children: size(b.size) })
        ] }, b.name)) })
      ] })
    ] }),
    (d.log_tail ?? []).length > 0 && /* @__PURE__ */ jsxs2("details", { className: "panel mt-3 p-4", children: [
      /* @__PURE__ */ jsxs2("summary", { className: "cursor-pointer text-sm font-semibold", children: [
        "Server-Log (letzte ",
        d.log_tail.length,
        " Zeilen)"
      ] }),
      /* @__PURE__ */ jsx3("pre", { className: "mt-3 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-black/40 p-3 font-mono text-[11px] leading-relaxed text-white/75", children: d.log_tail.join("\n") })
    ] })
  ] });
}
function NoGameServers() {
  const tag = useSettingText("gameserver", "host_tag", "gameserver");
  return /* @__PURE__ */ jsx3(
    EmptyState,
    {
      icon: "tag",
      title: "Noch kein Gameserver eingerichtet",
      text: `Gib einem Server unter Server & Zug\xE4nge die Markierung \u201E${tag}\u201C, dann erscheint er hier mit Status, Spielern und Welt-Sicherung. Die Markierung l\xE4sst sich in den Einstellungen des Moduls \xE4ndern.`,
      action: /* @__PURE__ */ jsxs2("div", { className: "flex flex-wrap justify-center gap-2", children: [
        /* @__PURE__ */ jsx3(SettingsLink, { to: "/settings/hosts", permission: "hosts.write", children: "Server & Zug\xE4nge \xF6ffnen" }),
        /* @__PURE__ */ jsx3(SettingsLink, { to: "/settings/extensions/gameserver", permission: "extensions.manage", variant: "secondary", children: "Moduleinstellungen" })
      ] })
    }
  );
}
function GameServerPage() {
  const unmountSignal = useUnmountSignal();
  const [urlParams, , visits] = useUrlParams();
  const focusHost = urlParams.get("host") || null;
  const [servers, setServers] = useState3(null);
  const [profiles, setProfiles] = useState3([]);
  const [error, setError] = useState3(null);
  const [pending, setPending] = useState3(null);
  const [message, setMessage] = useState3(null);
  const load = useCallback2(async (fresh = false) => {
    setError(null);
    try {
      const res = await authedFetch("/ext/gameserver/servers");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const list = await res.json();
      const full = deck().hasPermission("hosts.execute");
      const details = await Promise.all(
        list.map(async (s) => {
          const path = full ? `/ext/gameserver/servers/${s.host_id}/details${fresh ? "?fresh=true" : ""}` : `/ext/gameserver/servers/${s.host_id}`;
          const r = await authedFetch(path);
          return r.ok ? await r.json() : { ...s, details: {}, config: { profile: "", values: {} } };
        })
      );
      setServers(details);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);
  useEffect4(() => {
    void load();
    authedFetch("/ext/gameserver/profiles").then((r) => r.ok ? r.json() : []).then(setProfiles).catch(() => setProfiles([]));
    const interval = setInterval(() => void load(), 3e4);
    return () => clearInterval(interval);
  }, [load]);
  async function trigger(server, action) {
    if (action === "stop" || action === "restart") {
      const text = action === "stop" ? `"${server.name}" wirklich stoppen? Verbundene Spieler fliegen raus.` : `"${server.name}" neu starten? Verbundene Spieler fliegen kurz raus, der Join-Code \xE4ndert sich.`;
      const ok = await deck().confirmDialog(text, { danger: true, confirmLabel: ACTION_LABEL[action] });
      if (!ok) return;
    }
    setPending(`${server.host_id}:${action}`);
    setMessage(null);
    try {
      const { action: result, approved } = await runAction(`/ext/gameserver/servers/${server.host_id}/${action}`, { method: "POST" }, { signal: unmountSignal() });
      if (!approved && result.status === "proposed") {
        setMessage(`${server.name}: ${ACTION_LABEL[action]} vorgeschlagen -- Freigabe durch einen Admin n\xF6tig, siehe "Aktionen".`);
      } else {
        setMessage(`${server.name}: ${ACTION_LABEL[action]} -> ${ACTION_STATUS_LABEL2[result.status ?? ""] ?? result.status ?? "?"}.`);
      }
      await load(true);
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPending(null);
    }
  }
  const scrolledFor = useRef2(null);
  useEffect4(() => {
    if (!servers || !focusHost || scrolledFor.current === visits) return;
    scrolledFor.current = visits;
    document.getElementById(`host-${focusHost}`)?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }, [servers, focusHost, visits]);
  if (!servers && !error) return /* @__PURE__ */ jsx3("div", { className: "p-6 text-sm opacity-60", children: "Lade \u2026" });
  if (error) return /* @__PURE__ */ jsxs2("div", { className: "p-6 text-sm text-red-400", children: [
    "Fehler: ",
    error
  ] });
  const ordered = [...servers ?? []].sort((a, b) => Number(b.host_id === focusHost) - Number(a.host_id === focusHost));
  return /* @__PURE__ */ jsxs2("div", { className: "p-4 sm:p-6", children: [
    /* @__PURE__ */ jsxs2("div", { className: "mb-6", children: [
      /* @__PURE__ */ jsx3("h2", { className: "text-2xl font-semibold tracking-tight", children: "Gameserver" }),
      /* @__PURE__ */ jsx3("p", { className: "text-sm text-white/55", children: "Status, Spieler, Join-Code und Welt-Sicherungen deiner Spielserver." })
    ] }),
    message && /* @__PURE__ */ jsx3("p", { className: "panel mb-4 px-4 py-2 text-sm", children: message }),
    servers && servers.length === 0 && /* @__PURE__ */ jsx3(NoGameServers, {}),
    ordered.map((server) => /* @__PURE__ */ jsx3(ServerCard, { server, profiles, onAction: (s, a) => void trigger(s, a), pending, reload: () => void load(true) }, server.host_id))
  ] });
}
export {
  GameServerPage,
  ago
};
