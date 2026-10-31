// src/ProxmoxNodePage.tsx
import { Fragment as Fragment3, useCallback as useCallback2, useEffect as useEffect7, useRef as useRef3, useState as useState7 } from "react";

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

// ../../_shared/frontend/src/deck.ts
var FOCUS_CLASS = "nodvard-deck-focus";

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
async function authedFetchConfirmed(path, build) {
  const first = await authedFetch(path, build());
  if (first.status !== 403) return first;
  const body = await first.clone().json().catch(() => null);
  const ask = deck().confirmIdentity;
  if (body?.code !== "confirm_required" || !ask) return first;
  const message = typeof body.detail === "string" ? body.detail : "Bitte zur Best\xE4tigung das aktuelle Passwort eingeben.";
  const confirmation = await ask(message, body.two_factor === true);
  if (!confirmation) return null;
  return authedFetch(path, build(confirmation));
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

// ../../_shared/frontend/src/menu.tsx
import { useEffect as useEffect4, useRef as useRef2, useState as useState3 } from "react";
import { jsx as jsx4, jsxs as jsxs3 } from "react/jsx-runtime";
function MoreMenu({
  items,
  label = "Mehr",
  title = "Weitere Aktionen",
  align = "left",
  small = false,
  testId
}) {
  const ref = useRef2(null);
  const [open, setOpen] = useState3(false);
  const close = (refocus) => {
    const details = ref.current;
    if (!details) return;
    if (refocus) details.querySelector("summary")?.focus();
    details.open = false;
  };
  useEffect4(() => {
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
  return /* @__PURE__ */ jsxs3(
    "details",
    {
      ref,
      className: "relative",
      "data-testid": testId,
      onToggle: (e) => setOpen(e.currentTarget.open),
      onBlur: (e) => {
        if (ref.current?.open && e.relatedTarget && !ref.current.contains(e.relatedTarget)) close(false);
      },
      children: [
        /* @__PURE__ */ jsxs3("summary", { title, "aria-label": `${label}: ${title}`, className: `${buttonClass("secondary", small)} cursor-pointer list-none [&::-webkit-details-marker]:hidden`, children: [
          label,
          " ",
          /* @__PURE__ */ jsx4(Icon, { name: "chevron-down", size: small ? 12 : 14 })
        ] }),
        /* @__PURE__ */ jsx4(
          "div",
          {
            className: `absolute ${align === "right" ? "right-0" : "left-0"} z-20 mt-1 flex w-56 max-w-[calc(100vw-2rem)] flex-col rounded-xl border border-white/10 bg-[var(--color-surface)] p-1 shadow-xl`,
            children: items.map((item) => /* @__PURE__ */ jsx4(
              "button",
              {
                type: "button",
                disabled: item.disabled,
                title: item.title,
                onClick: () => {
                  close(true);
                  item.run();
                },
                className: `rounded-md px-3 py-2 text-left text-sm disabled:opacity-50 ${item.danger ? "text-red-300 hover:bg-red-500/15" : "hover:bg-white/[0.06]"}`,
                children: item.label
              },
              item.id
            ))
          }
        )
      ]
    }
  );
}

// ../../_shared/frontend/src/certificate.tsx
import { useEffect as useEffect5, useState as useState4 } from "react";
import { jsx as jsx5, jsxs as jsxs4 } from "react/jsx-runtime";
function CertificateStatus({ connection }) {
  if (connection.tls_fingerprint_invalid) {
    return /* @__PURE__ */ jsx5("span", { className: "text-red-300", children: "Fingerabdruck ung\xFCltig" });
  }
  if (connection.tls_fingerprint_sha256) {
    return /* @__PURE__ */ jsx5("span", { className: "text-emerald-300", title: connection.tls_fingerprint_sha256, children: "Fingerabdruck gemerkt" });
  }
  if (connection.tls_insecure_skip_verify) {
    return /* @__PURE__ */ jsx5("span", { className: "text-amber-300", children: "wird nicht gepr\xFCft" });
  }
  return /* @__PURE__ */ jsx5("span", { className: "opacity-70", children: "normal gepr\xFCft" });
}
function formatDate(iso) {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleDateString("de-DE");
}
var PROXMOX_FINGERPRINT_HINT = "In Proxmox links den Knoten anklicken \u2192 System \u2192 Zertifikate, Spalte \u201EFingerabdruck\u201C \u2013 bei pveproxy-ssl.pem, falls es die Zeile gibt, sonst bei pve-ssl.pem.";
function ConnectionCertificate({
  extId,
  connection,
  onSaved,
  hint = PROXMOX_FINGERPRINT_HINT,
  unchecked = false
}) {
  const [info, setInfo] = useState4(null);
  const [error, setError] = useState4(null);
  const [confirmed, setConfirmed] = useState4(false);
  const [busy, setBusy] = useState4(false);
  const [message, setMessage] = useState4(null);
  const path = `/ext/${extId}/connections/${encodeURIComponent(connection)}/certificate`;
  useEffect5(() => {
    let active = true;
    setInfo(null);
    setError(null);
    setMessage(null);
    setConfirmed(false);
    authedFetch(path).then(async (res) => {
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      if (active) setInfo(body);
    }).catch((err) => {
      if (active) setError(err instanceof Error ? err.message : String(err));
    });
    return () => {
      active = false;
    };
  }, [path]);
  async function save() {
    if (!info) return;
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      const res = await authedFetch(path, { method: "POST", body: JSON.stringify({ sha256: info.sha256 }) });
      const body = await res.json().catch(() => ({}));
      if (res.status === 409 && typeof body.sha256 === "string") {
        setInfo({ ...info, sha256: body.sha256, matches: false, trusted: false, subject: null, not_after: null });
        setConfirmed(false);
        throw new Error(errorFromBody(body, res.status));
      }
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      setInfo(body);
      setConfirmed(false);
      setMessage("Gemerkt. Nodvard Deck spricht jetzt nur mit dem Server, der genau dieses Zertifikat zeigt.");
      onSaved?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  const until = formatDate(info?.not_after);
  const changed = Boolean(info && info.stored && !info.matches);
  return /* @__PURE__ */ jsxs4("section", { className: "mb-3 space-y-2 rounded-lg border border-white/10 p-3 text-xs", "aria-label": `Zertifikat \u2013 ${connection}`, children: [
    /* @__PURE__ */ jsxs4("h4", { className: "font-medium", children: [
      "Zertifikat \u2013 ",
      connection
    ] }),
    !info && !error && /* @__PURE__ */ jsx5("p", { className: "opacity-60", children: "Lese das Zertifikat \u2026" }),
    error && /* @__PURE__ */ jsx5("p", { role: "alert", className: "text-red-300", children: error }),
    message && /* @__PURE__ */ jsx5("p", { role: "status", className: "text-emerald-300", children: message }),
    info && info.matches && /* @__PURE__ */ jsx5("p", { className: "text-emerald-300", "data-testid": "certificate-matches", children: "Der gemerkte Fingerabdruck passt zum Zertifikat des Servers." }),
    info && !info.matches && /* @__PURE__ */ jsxs4("div", { className: "space-y-2", "data-testid": "certificate-to-confirm", children: [
      changed && /* @__PURE__ */ jsx5("p", { className: "text-amber-300", "data-testid": "certificate-changed", children: "Der Server zeigt ein anderes Zertifikat als das gemerkte. Bis du den neuen Fingerabdruck best\xE4tigst, spricht Nodvard Deck nicht mit ihm." }),
      info.stored_invalid && /* @__PURE__ */ jsx5("p", { className: "text-red-300", children: "Der eingetragene Fingerabdruck ist ung\xFCltig \u2013 bis er stimmt, spricht Nodvard Deck nicht mit dem Server." }),
      !info.stored && !info.stored_invalid && /* @__PURE__ */ jsx5("p", { className: info.trusted ? "opacity-80" : "text-amber-300", children: info.trusted ? "Das Zertifikat ist von einer anerkannten Stelle best\xE4tigt, Merken ist nicht n\xF6tig. Erneuert es sich regelm\xE4\xDFig (z. B. Let's Encrypt), merk es dir besser nicht \u2013 sonst musst du nach jeder Erneuerung neu best\xE4tigen." : unchecked ? "Das Zertifikat wird bisher nicht gepr\xFCft. Merk dir den Fingerabdruck, dann spricht Nodvard Deck nur mit genau diesem Server." : "Das Zertifikat ist nicht von einer anerkannten Stelle best\xE4tigt, deshalb klappt die Verbindung noch nicht. Merk dir den Fingerabdruck, dann spricht Nodvard Deck nur mit genau diesem Server." }),
      /* @__PURE__ */ jsx5("p", { className: "opacity-80", children: hint }),
      /* @__PURE__ */ jsxs4("div", { children: [
        /* @__PURE__ */ jsxs4("p", { className: "opacity-60", children: [
          changed ? "Neuer Fingerabdruck" : "Fingerabdruck",
          " (SHA-256)"
        ] }),
        /* @__PURE__ */ jsx5("p", { className: "break-all rounded bg-black/30 px-2 py-1.5 font-mono", "aria-label": "Fingerabdruck des Servers", children: info.sha256 })
      ] }),
      changed && info.stored && /* @__PURE__ */ jsxs4("div", { children: [
        /* @__PURE__ */ jsx5("p", { className: "opacity-60", children: "Bisher gemerkt" }),
        /* @__PURE__ */ jsx5("p", { className: "break-all rounded bg-black/20 px-2 py-1.5 font-mono opacity-60", "aria-label": "Bisher gemerkt", children: info.stored })
      ] }),
      (info.subject || until) && /* @__PURE__ */ jsxs4("p", { className: "text-white/55", children: [
        info.subject ? `Ausgestellt f\xFCr ${info.subject}` : "",
        info.subject && until ? " \xB7 " : "",
        until ? `g\xFCltig bis ${until}` : ""
      ] }),
      /* @__PURE__ */ jsxs4("label", { className: "flex cursor-pointer items-start gap-2", children: [
        /* @__PURE__ */ jsx5(
          "input",
          {
            type: "checkbox",
            className: "mt-0.5 h-4 w-4 flex-none accent-[var(--color-accent)]",
            checked: confirmed,
            onChange: (e) => setConfirmed(e.target.checked)
          }
        ),
        /* @__PURE__ */ jsx5("span", { children: "Der Fingerabdruck stimmt mit dem in Proxmox \xFCberein." })
      ] }),
      /* @__PURE__ */ jsx5("p", { className: "text-white/45", children: "Stimmt er nicht, best\xE4tige nichts: Dann antwortet unter dieser Adresse vielleicht ein anderer Rechner." }),
      /* @__PURE__ */ jsx5(Button, { small: true, variant: "primary", disabled: !confirmed || busy, onClick: () => void save(), children: busy ? "Speichere \u2026" : changed ? "Neuen Fingerabdruck best\xE4tigen" : "Fingerabdruck merken" })
    ] })
  ] });
}

// src/OldTokens.tsx
import { useEffect as useEffect6, useState as useState5 } from "react";
import { jsx as jsx6, jsxs as jsxs5 } from "react/jsx-runtime";
function canCopy() {
  return typeof navigator !== "undefined" && Boolean(navigator.clipboard?.writeText) && Boolean(window.isSecureContext);
}
function OldTokenList({ tokens }) {
  const [copied, setCopied] = useState5(false);
  if (tokens.length === 0) return null;
  const commands = tokens.map((t) => t.command).join("\n");
  async function copy() {
    try {
      await navigator.clipboard.writeText(commands);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }
  return /* @__PURE__ */ jsxs5("div", { className: "space-y-1.5 text-xs", "data-testid": "old-tokens", children: [
    /* @__PURE__ */ jsxs5("p", { className: "text-white/70", children: [
      "Diese Tokens hat Nodvard Deck fr\xFCher angelegt, benutzt werden sie hier nicht mehr:",
      " ",
      tokens.map((t) => t.token_id).join(", "),
      ". Braucht sie nichts anderes mehr (etwa ein zweites Nodvard Deck), l\xF6sch sie in Proxmox unter Rechenzentrum \u2192 Rechte \u2192 API-Token oder auf dem Server mit:"
    ] }),
    /* @__PURE__ */ jsx6("pre", { className: "select-all overflow-x-auto rounded bg-black/30 px-2 py-1.5 font-mono", "aria-label": "Befehle zum L\xF6schen", children: commands }),
    canCopy() && /* @__PURE__ */ jsx6(Button, { small: true, variant: "ghost", onClick: () => void copy(), children: copied ? "Kopiert" : "Befehle kopieren" }),
    /* @__PURE__ */ jsx6("p", { className: "text-white/45", children: "Nodvard Deck l\xF6scht sie nicht selbst: Daf\xFCr br\xE4uchte sein Zugang in Proxmox das Recht, Benutzer zu \xE4ndern \u2013 und damit k\xF6nnte er sich alle Rechte seines Benutzers geben." })
  ] });
}
function OldTokens({ connection }) {
  const [data, setData] = useState5(null);
  const [error, setError] = useState5(null);
  useEffect6(() => {
    let active = true;
    setData(null);
    setError(null);
    authedFetch(`/ext/proxmox/connections/${encodeURIComponent(connection)}/old-tokens`).then(async (res) => {
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      if (active) setData(body);
    }).catch((err) => {
      if (active) setError(err instanceof Error ? err.message : String(err));
    });
    return () => {
      active = false;
    };
  }, [connection]);
  return /* @__PURE__ */ jsxs5("section", { className: "mb-3 space-y-1.5 rounded-lg border border-white/10 p-3", "aria-label": `Alte Tokens \u2013 ${connection}`, children: [
    /* @__PURE__ */ jsxs5("h4", { className: "text-xs font-medium", children: [
      "Alte Tokens \u2013 ",
      connection
    ] }),
    !data && !error && /* @__PURE__ */ jsx6("p", { className: "text-xs opacity-60", children: "Lade \u2026" }),
    error && /* @__PURE__ */ jsxs5("p", { role: "alert", className: "text-xs text-red-300", children: [
      "Nicht lesbar: ",
      error
    ] }),
    data && data.tokens.length === 0 && /* @__PURE__ */ jsx6("p", { className: "text-xs opacity-70", children: "In Proxmox liegen keine alten Tokens von Nodvard Deck." }),
    data && /* @__PURE__ */ jsx6(OldTokenList, { tokens: data.tokens }),
    data && !data.backups_checked && /* @__PURE__ */ jsx6("p", { className: "text-xs text-white/45", children: "Proxmox-Backups ist aus \u2013 dessen Tokens (nodvard-backup@pve) z\xE4hlen deshalb nie als alt." }),
    data && data.privsep_off.length > 0 && /* @__PURE__ */ jsxs5("p", { className: "text-xs text-amber-300", children: [
      "Ohne Privilegientrennung (darf alles, was sein Benutzer darf): ",
      data.privsep_off.join(", "),
      ". Brauchst du ihn nicht, l\xF6sch ihn in Proxmox."
    ] })
  ] });
}

// src/ProxmoxConnect.tsx
import { useId, useState as useState6 } from "react";

// ../../_shared/frontend/src/passwordWarning.tsx
import { Fragment as Fragment2, jsxs as jsxs6 } from "react/jsx-runtime";
function sendsInClear(location) {
  if (location.protocol === "https:") return false;
  const host = location.hostname.replace(/^\[|\]$/g, "").toLowerCase();
  return !(host === "localhost" || host === "::1" || /^127\.\d+\.\d+\.\d+$/.test(host));
}
function PasswordInClearWarning({
  alternative,
  location
}) {
  const page = location ?? (typeof window === "undefined" ? void 0 : window.location);
  if (!page || !sendsInClear(page)) return null;
  return /* @__PURE__ */ jsxs6("p", { role: "note", "data-testid": "password-in-clear", className: "rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200", children: [
    "Achtung: Diese Seite l\xE4uft ohne HTTPS. Das Passwort geht unverschl\xFCsselt bis zu Nodvard Deck \u2013 im eigenen Heimnetz meist in Ordnung, in einem fremden Netz nicht.",
    alternative ? /* @__PURE__ */ jsxs6(Fragment2, { children: [
      " ",
      alternative
    ] }) : null
  ] });
}

// src/ProxmoxConnect.tsx
import { jsx as jsx7, jsxs as jsxs7 } from "react/jsx-runtime";
var TFA_LABELS = {
  totp: "Code aus der App",
  recovery: "Wiederherstellungsschl\xFCssel",
  yubico: "YubiKey (Einmal-Code)"
};
var CERTIFICATE_CODES = /* @__PURE__ */ new Set(["certificate_unconfirmed", "certificate_changed", "certificate_changed_after_login"]);
var LOGIN_AGAIN_CODES = /* @__PURE__ */ new Set(["tfa_wrong", "tfa_expired", "secret_foreign"]);
async function post(path, body) {
  const res = await authedFetch(`/ext/proxmox${path}`, { method: "POST", body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const failure = {
      detail: errorFromBody(data, res.status),
      code: typeof data.code === "string" ? data.code : "unknown",
      certificate: data.certificate ?? void 0
    };
    throw failure;
  }
  return data;
}
function asFailure(err) {
  if (typeof err === "object" && err !== null && "code" in err && "detail" in err) return err;
  return { detail: err instanceof Error ? err.message : String(err), code: "unknown" };
}
function formatDate2(iso) {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleDateString("de-DE");
}
function CertificateBox({
  cert,
  confirmed,
  onConfirm
}) {
  if (cert.trusted) {
    return /* @__PURE__ */ jsx7("p", { className: "rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200", "data-testid": "certificate-trusted", children: "Das Zertifikat ist von einer anerkannten Stelle best\xE4tigt. Dein Passwort geht verschl\xFCsselt und gepr\xFCft an diesen Server." });
  }
  const until = formatDate2(cert.not_after);
  return /* @__PURE__ */ jsxs7("div", { className: "space-y-2 rounded-lg border border-amber-500/30 bg-amber-500/[0.06] p-3 text-sm", "data-testid": "certificate-confirm", children: [
    /* @__PURE__ */ jsxs7("p", { children: [
      "Proxmox nutzt ein eigenes Zertifikat. Vergleiche den Fingerabdruck, bevor du dein Passwort eingibst: In Proxmox links den Knoten anklicken \u2192 ",
      /* @__PURE__ */ jsx7("strong", { children: "System" }),
      " \u2192 ",
      /* @__PURE__ */ jsx7("strong", { children: "Zertifikate" }),
      ", Spalte \u201EFingerabdruck\u201C \u2013 bei ",
      /* @__PURE__ */ jsx7("code", { children: "pveproxy-ssl.pem" }),
      ", falls es die Zeile gibt, sonst bei ",
      /* @__PURE__ */ jsx7("code", { children: "pve-ssl.pem" }),
      "."
    ] }),
    /* @__PURE__ */ jsx7("p", { className: "break-all rounded bg-black/30 px-2 py-1.5 font-mono text-xs", "aria-label": "Fingerabdruck (SHA-256)", children: cert.sha256 }),
    (cert.subject || until) && /* @__PURE__ */ jsxs7("p", { className: "text-xs text-white/55", children: [
      cert.subject ? `Ausgestellt f\xFCr ${cert.subject}` : "",
      cert.subject && until ? " \xB7 " : "",
      until ? `g\xFCltig bis ${until}` : ""
    ] }),
    /* @__PURE__ */ jsxs7("label", { className: "flex cursor-pointer items-start gap-2.5", children: [
      /* @__PURE__ */ jsx7(
        "input",
        {
          type: "checkbox",
          className: "mt-0.5 h-4 w-4 flex-none accent-[var(--color-accent)]",
          checked: confirmed,
          onChange: (e) => onConfirm(e.target.checked)
        }
      ),
      /* @__PURE__ */ jsx7("span", { children: "Der Fingerabdruck stimmt mit dem in Proxmox \xFCberein." })
    ] }),
    /* @__PURE__ */ jsx7("p", { className: "text-xs text-white/55", children: "Stimmt er nicht, gib hier nichts ein: Dann antwortet unter dieser Adresse vielleicht ein anderer Rechner. Stimmt er, merkt sich Nodvard Deck den Fingerabdruck und spricht auch sp\xE4ter nur mit genau diesem Server." })
  ] });
}
function LabeledField({
  label,
  hint,
  children
}) {
  const id = useId();
  const hintId = `${id}-hinweis`;
  return /* @__PURE__ */ jsxs7("div", { className: "block text-sm", children: [
    /* @__PURE__ */ jsx7("label", { htmlFor: id, className: "mb-1.5 block text-white/70", children: label }),
    children({ id, "aria-describedby": hint ? hintId : void 0 }),
    hint && /* @__PURE__ */ jsx7("p", { id: hintId, className: "mt-1 text-xs text-white/45", children: hint })
  ] });
}
function ProxmoxConnect({
  onConnected,
  onClose,
  pageLocation
}) {
  const [step, setStep] = useState6("address");
  const [address, setAddress] = useState6("");
  const [baseUrl, setBaseUrl] = useState6("");
  const [cert, setCert] = useState6(null);
  const [confirmed, setConfirmed] = useState6(false);
  const [username, setUsername] = useState6("root@pam");
  const [password, setPassword] = useState6("");
  const [name, setName] = useState6("");
  const [access, setAccess] = useState6("full");
  const [backupsAvailable, setBackupsAvailable] = useState6(false);
  const [withBackups, setWithBackups] = useState6(true);
  const [tfa, setTfa] = useState6(null);
  const [tfaKind, setTfaKind] = useState6("totp");
  const [code, setCode] = useState6("");
  const [busy, setBusy] = useState6(false);
  const [error, setError] = useState6(null);
  const [result, setResult] = useState6(null);
  function showFailure(failure) {
    setError(failure.detail);
    if (failure.certificate && CERTIFICATE_CODES.has(failure.code)) {
      setCert(failure.certificate);
      setConfirmed(false);
      setStep("login");
    }
  }
  function finish(out) {
    if (out.status === "tfa_required") {
      setTfa(out.tfa);
      setTfaKind(out.tfa.kinds[0] ?? "totp");
      setStep("tfa");
      return;
    }
    setResult(out);
    setStep("done");
    onConnected(out.connection.name);
  }
  async function checkCertificate(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const out = await post(
        "/connect/certificate",
        { base_url: address.trim() }
      );
      setBaseUrl(out.base_url);
      setCert(out.certificate);
      setBackupsAvailable(out.backups_available === true);
      setConfirmed(false);
      setStep("login");
    } catch (err) {
      setError(asFailure(err).detail);
    } finally {
      setBusy(false);
    }
  }
  async function connect(e) {
    e.preventDefault();
    if (!cert || !cert.trusted && !confirmed) return;
    const typed = password;
    setPassword("");
    setBusy(true);
    setError(null);
    try {
      finish(await post("/connect", {
        base_url: baseUrl,
        username: username.trim(),
        password: typed,
        name: name.trim() || null,
        fingerprint: cert.trusted ? null : cert.sha256,
        access,
        backups: backupsAvailable && withBackups
      }));
    } catch (err) {
      showFailure(asFailure(err));
    } finally {
      setBusy(false);
    }
  }
  async function sendCode(e) {
    e.preventDefault();
    if (!tfa) return;
    const typed = code;
    setCode("");
    setBusy(true);
    setError(null);
    try {
      finish(await post("/connect/tfa", { flow: tfa.flow, code: typed, kind: tfaKind }));
    } catch (err) {
      const failure = asFailure(err);
      showFailure(failure);
      if (LOGIN_AGAIN_CODES.has(failure.code)) {
        setTfa(null);
        setStep("login");
      }
    } finally {
      setBusy(false);
    }
  }
  return /* @__PURE__ */ jsxs7("section", { className: "mb-6 space-y-4 p-4 panel", "data-testid": "proxmox-connect", "aria-label": "Mit Passwort verbinden", children: [
    /* @__PURE__ */ jsxs7("div", { className: "flex flex-wrap items-start justify-between gap-2", children: [
      /* @__PURE__ */ jsxs7("div", { children: [
        /* @__PURE__ */ jsx7("h3", { className: "text-sm font-semibold", children: "Proxmox mit Passwort verbinden" }),
        /* @__PURE__ */ jsx7("p", { className: "mt-0.5 max-w-2xl text-xs text-white/55", children: "Du gibst einmal Adresse und Passwort ein, Nodvard Deck legt in Proxmox einen eigenen Zugang an (Benutzer nodvard@pve mit API-Token und passenden Rechten) und vergisst das Passwort sofort. Es wird nirgends gespeichert." })
      ] }),
      /* @__PURE__ */ jsx7(Button, { small: true, variant: "ghost", onClick: onClose, children: step === "done" ? "Schlie\xDFen" : "Abbrechen" })
    ] }),
    step === "address" && /* @__PURE__ */ jsxs7("form", { onSubmit: (e) => void checkCertificate(e), className: "flex flex-wrap items-end gap-2", noValidate: true, children: [
      /* @__PURE__ */ jsx7("div", { className: "min-w-[16rem] flex-1", children: /* @__PURE__ */ jsx7(LabeledField, { label: "Adresse des Proxmox-Servers", children: (p) => /* @__PURE__ */ jsx7(
        "input",
        {
          ...p,
          className: inputClass,
          value: address,
          onChange: (e) => setAddress(e.target.value),
          placeholder: "https://192.168.2.10:8006",
          autoCapitalize: "none",
          autoCorrect: "off",
          spellCheck: false,
          autoComplete: "off"
        }
      ) }) }),
      /* @__PURE__ */ jsx7(Button, { type: "submit", variant: "primary", disabled: busy || !address.trim(), children: busy ? "Pr\xFCfe \u2026" : "Weiter" })
    ] }),
    step === "login" && cert && /* @__PURE__ */ jsxs7("form", { onSubmit: (e) => void connect(e), className: "space-y-4", noValidate: true, children: [
      /* @__PURE__ */ jsxs7("p", { className: "text-xs text-white/55", children: [
        "Server: ",
        /* @__PURE__ */ jsx7("span", { className: "font-mono", children: baseUrl }),
        " ",
        /* @__PURE__ */ jsx7("button", { type: "button", className: "underline hover:text-white", onClick: () => {
          setStep("address");
          setError(null);
        }, children: "\xE4ndern" })
      ] }),
      /* @__PURE__ */ jsx7(CertificateBox, { cert, confirmed, onConfirm: setConfirmed }),
      /* @__PURE__ */ jsxs7("div", { className: "grid gap-4 sm:grid-cols-2", children: [
        /* @__PURE__ */ jsx7(LabeledField, { label: "Benutzer", hint: "Mit Bereich, meist root@pam. Das Konto muss Benutzer und Rechte anlegen d\xFCrfen.", children: (p) => /* @__PURE__ */ jsx7(
          "input",
          {
            ...p,
            className: inputClass,
            value: username,
            onChange: (e) => setUsername(e.target.value),
            autoCapitalize: "none",
            autoCorrect: "off",
            spellCheck: false,
            autoComplete: "off",
            placeholder: "root@pam"
          }
        ) }),
        /* @__PURE__ */ jsx7(LabeledField, { label: "Passwort", hint: "Nur f\xFCr diese eine Anmeldung.", children: (p) => /* @__PURE__ */ jsx7(
          "input",
          {
            ...p,
            type: "password",
            className: inputClass,
            value: password,
            onChange: (e) => setPassword(e.target.value),
            autoComplete: "new-password"
          }
        ) }),
        /* @__PURE__ */ jsx7(LabeledField, { label: "Kurzname (freiwillig)", hint: "Leer lassen: Nodvard Deck nimmt den Namen aus Proxmox.", children: (p) => /* @__PURE__ */ jsx7(
          "input",
          {
            ...p,
            className: inputClass,
            value: name,
            onChange: (e) => setName(e.target.value),
            placeholder: "z. B. pve1",
            autoCapitalize: "none",
            autoCorrect: "off",
            spellCheck: false,
            autoComplete: "off"
          }
        ) }),
        /* @__PURE__ */ jsxs7("fieldset", { className: "text-sm", children: [
          /* @__PURE__ */ jsx7("legend", { className: "mb-1.5 text-white/70", children: "Was darf Nodvard Deck?" }),
          /* @__PURE__ */ jsxs7("label", { className: "flex cursor-pointer items-start gap-2", children: [
            /* @__PURE__ */ jsx7("input", { type: "radio", name: "access", className: "mt-1", checked: access === "full", onChange: () => setAccess("full") }),
            /* @__PURE__ */ jsxs7("span", { children: [
              "Ansehen und steuern ",
              /* @__PURE__ */ jsx7("span", { className: "text-white/45", children: "(Starten, Herunterfahren, Snapshots, Konsole \u2026)" })
            ] })
          ] }),
          /* @__PURE__ */ jsxs7("label", { className: "mt-1 flex cursor-pointer items-start gap-2", children: [
            /* @__PURE__ */ jsx7("input", { type: "radio", name: "access", className: "mt-1", checked: access === "read", onChange: () => setAccess("read") }),
            /* @__PURE__ */ jsx7("span", { children: "Nur ansehen" })
          ] })
        ] })
      ] }),
      backupsAvailable && /* @__PURE__ */ jsxs7("label", { className: "flex cursor-pointer items-start gap-2.5 text-sm", children: [
        /* @__PURE__ */ jsx7(
          "input",
          {
            type: "checkbox",
            className: "mt-0.5 h-4 w-4 flex-none accent-[var(--color-accent)]",
            checked: withBackups,
            onChange: (e) => setWithBackups(e.target.checked)
          }
        ),
        /* @__PURE__ */ jsxs7("span", { children: [
          "Auch Proxmox-Backups verbinden",
          /* @__PURE__ */ jsx7("span", { className: "block text-xs text-white/45", children: access === "full" ? "Mit eigenem Zugang (Benutzer nodvard-backup@pve): Backups ansehen, starten und Jobs \xE4ndern, als Kopie wiederherstellen (nur in den Pool \u201Enodvard-kopien\u201C). Proxmox erlaubt diesem Zugang auch, Sicherungen zu l\xF6schen und einen Gast aus einer Sicherung zu \xFCberschreiben \u2013 Nodvard Deck tut beides nie." : "Mit eigenem Zugang (Benutzer nodvard-backup@pve), der Backups nur ansieht." })
        ] })
      ] }),
      /* @__PURE__ */ jsx7(PasswordInClearWarning, { alternative: "Sicherer ist dann der Weg mit API-Token aus der Anleitung.", location: pageLocation }),
      /* @__PURE__ */ jsx7(Button, { type: "submit", variant: "primary", disabled: busy || !password || !username.trim() || !cert.trusted && !confirmed, children: busy ? "Verbinde \u2026" : "Verbinden" })
    ] }),
    step === "tfa" && tfa && /* @__PURE__ */ jsxs7("form", { onSubmit: (e) => void sendCode(e), className: "space-y-3", noValidate: true, "data-testid": "proxmox-connect-tfa", children: [
      /* @__PURE__ */ jsx7("p", { className: "text-sm", children: "Proxmox verlangt einen zweiten Faktor. Gib den Code ein \u2013 dein Passwort geht dabei nicht noch einmal hinaus." }),
      tfa.kinds.length > 1 && /* @__PURE__ */ jsx7("div", { className: "max-w-xs", children: /* @__PURE__ */ jsx7(LabeledField, { label: "Art", children: (p) => /* @__PURE__ */ jsx7("select", { ...p, className: inputClass, value: tfaKind, onChange: (e) => setTfaKind(e.target.value), children: tfa.kinds.map((kind) => /* @__PURE__ */ jsx7("option", { value: kind, children: TFA_LABELS[kind] ?? kind }, kind)) }) }) }),
      /* @__PURE__ */ jsx7("div", { className: "max-w-xs", children: /* @__PURE__ */ jsx7(LabeledField, { label: TFA_LABELS[tfaKind] ?? "Code", children: (p) => /* @__PURE__ */ jsx7(
        "input",
        {
          ...p,
          className: inputClass,
          value: code,
          onChange: (e) => setCode(e.target.value),
          autoComplete: "one-time-code",
          inputMode: tfaKind === "totp" ? "numeric" : "text",
          autoCapitalize: "none",
          autoCorrect: "off",
          spellCheck: false
        }
      ) }) }),
      /* @__PURE__ */ jsx7(Button, { type: "submit", variant: "primary", disabled: busy || !code.trim(), children: busy ? "Pr\xFCfe \u2026" : "Best\xE4tigen" })
    ] }),
    step === "done" && result && /* @__PURE__ */ jsxs7("div", { className: "space-y-2 text-sm", "data-testid": "proxmox-connect-done", children: [
      /* @__PURE__ */ jsxs7("p", { role: "status", className: "text-emerald-300", children: [
        "Verbunden als \u201E",
        result.connection.name,
        "\u201C (",
        result.connection.token_id,
        ").",
        " ",
        result.discovery === "started" ? "Nodvard Deck liest Knoten, VMs und LXC jetzt ein \u2013 das dauert meist nur einen Moment." : "Nodvard Deck liest Knoten, VMs und LXC in den n\xE4chsten Minuten ein."
      ] }),
      result.backups && /* @__PURE__ */ jsxs7("p", { className: "text-emerald-300", "data-testid": "proxmox-connect-backups", children: [
        "Proxmox-Backups ist als \u201E",
        result.backups.name,
        "\u201C verbunden (",
        result.backups.token_id,
        ")."
      ] }),
      result.created.length > 0 && /* @__PURE__ */ jsxs7("div", { children: [
        /* @__PURE__ */ jsx7("p", { className: "text-xs text-white/55", children: "In Proxmox angelegt:" }),
        /* @__PURE__ */ jsx7("ul", { className: "ml-4 list-disc text-xs text-white/70", children: result.created.map((line) => /* @__PURE__ */ jsx7("li", { children: line }, line)) })
      ] }),
      result.notes.map((note) => /* @__PURE__ */ jsx7("p", { className: "text-xs text-white/55", children: note }, note)),
      /* @__PURE__ */ jsx7(OldTokenList, { tokens: result.old_tokens ?? [] })
    ] }),
    error && /* @__PURE__ */ jsx7("p", { role: "alert", className: "text-sm text-red-300", children: error })
  ] });
}

// src/ProxmoxNodePage.tsx
import { Fragment as Fragment4, jsx as jsx8, jsxs as jsxs8 } from "react/jsx-runtime";
var RELOAD_AFTER_CONNECT_MS = 5e3;
var HOST_STATUS_LABEL = { up: "l\xE4uft", down: "gestoppt", unknown: "unbekannt", maintenance: "Wartung" };
var KIND_LABEL = { vm: "VM", lxc: "LXC", hypervisor: "Knoten" };
var VM_ACTIONS = [
  { type: "vm.start", label: "Starten", showWhen: (s) => s !== "up", main: true },
  {
    type: "vm.shutdown",
    label: "Herunterfahren",
    confirm: "sauber herunterfahren? Das Betriebssystem in der VM f\xE4hrt geordnet herunter.",
    dangerConfirm: false,
    showWhen: (s) => s !== "down",
    main: true
  },
  { type: "vm.reboot", label: "Neustarten", confirm: "wirklich neu starten?", showWhen: (s) => s !== "down" },
  { type: "vm.snapshot", label: "Snapshot anlegen" },
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
  const [snaps, setSnaps] = useState7(null);
  const [error, setError] = useState7(null);
  useEffect7(() => {
    let cancelled = false;
    authedFetch(`/ext/proxmox/guests/${hostId}/snapshots`).then(async (res) => {
      if (!res.ok) throw new Error(await errorText(res));
      return res.json();
    }).then((rows) => !cancelled && setSnaps(rows)).catch((err) => !cancelled && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      cancelled = true;
    };
  }, [hostId, refreshKey]);
  if (error) return /* @__PURE__ */ jsxs8("p", { className: "text-xs text-red-400", children: [
    "Snapshots nicht abrufbar: ",
    error
  ] });
  if (!snaps) return /* @__PURE__ */ jsx8("p", { className: "text-xs opacity-60", children: "Lade Snapshots \u2026" });
  if (snaps.length === 0) return /* @__PURE__ */ jsx8("p", { className: "text-xs opacity-60", children: "Keine Snapshots." });
  return /* @__PURE__ */ jsx8("ul", { className: "flex flex-col gap-1 text-xs", "data-testid": `snapshots-${hostId}`, children: snaps.map((snap) => /* @__PURE__ */ jsxs8("li", { className: "flex flex-wrap items-center gap-2", children: [
    /* @__PURE__ */ jsx8("span", { className: "font-medium", children: snap.name }),
    /* @__PURE__ */ jsx8("span", { className: "opacity-60", children: snap.snaptime ? new Date(snap.snaptime * 1e3).toLocaleString("de-DE") : "" }),
    snap.with_ram && /* @__PURE__ */ jsx8("span", { className: "rounded bg-white/10 px-1 text-[10px]", children: "mit RAM" }),
    snap.description && /* @__PURE__ */ jsxs8("span", { className: "opacity-60", children: [
      "-- ",
      snap.description
    ] }),
    /* @__PURE__ */ jsxs8("span", { className: "ml-auto flex gap-1.5", children: [
      /* @__PURE__ */ jsx8("button", { type: "button", disabled: isPending("vm.snapshot_rollback", snap.name), onClick: () => onAction("vm.snapshot_rollback", snap.name), className: "rounded bg-amber-500/20 px-2 py-0.5 text-amber-300 hover:bg-amber-500/30 disabled:opacity-40", children: "Zur\xFCckrollen" }),
      /* @__PURE__ */ jsx8("button", { type: "button", disabled: isPending("vm.snapshot_delete", snap.name), onClick: () => onAction("vm.snapshot_delete", snap.name), className: "px-2 py-0.5 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition disabled:opacity-40", children: "L\xF6schen" })
    ] })
  ] }, snap.name)) });
}
var BADGE_TONE = {
  good: "bg-emerald-500/20 text-emerald-300",
  warn: "bg-amber-500/20 text-amber-300",
  danger: "bg-red-500/20 text-red-300",
  neutral: "bg-white/10 opacity-70"
};
function shieldUpdatesHref(tools) {
  const tool = tools.find((t) => t.ext_id === "shield" && t.id === "security");
  if (!tool || !tool.href.startsWith("/") || tool.href.startsWith("//")) return null;
  const url = new URL(tool.href, "http://deck.invalid");
  url.searchParams.set("tab", "updates");
  return url.pathname + url.search;
}
function UpdatesSection({ nodes, shieldHref }) {
  return /* @__PURE__ */ jsx8("div", { children: /* @__PURE__ */ jsx8("ul", { className: "space-y-2", children: nodes.map((n) => {
    const href = !n.error && (n.count ?? 0) > 0 ? shieldHref?.(n) : void 0;
    return /* @__PURE__ */ jsxs8("li", { className: "text-sm", "data-testid": `updates-${n.node}`, children: [
      /* @__PURE__ */ jsxs8("div", { className: "flex flex-wrap items-center gap-2", children: [
        /* @__PURE__ */ jsx8("span", { className: "font-medium", children: n.node }),
        /* @__PURE__ */ jsx8("span", { className: `rounded px-1.5 py-0.5 text-xs ${BADGE_TONE[n.tone] ?? BADGE_TONE.neutral}`, children: n.badge }),
        /* @__PURE__ */ jsx8("span", { className: "opacity-80", children: n.summary }),
        href && /* @__PURE__ */ jsx8("a", { href, className: "ml-auto text-xs underline underline-offset-2 hover:text-white", "data-testid": `updates-shield-${n.node}`, children: "In Shield einspielen" })
      ] }),
      !n.error && /* @__PURE__ */ jsxs8("p", { className: "text-xs opacity-60", children: [
        "Proxmox ",
        n.pve_version ?? "?",
        " \xB7 Kernel ",
        n.running_kernel ?? "?",
        n.last_check ? ` \xB7 zuletzt gepr\xFCft ${new Date(n.last_check * 1e3).toLocaleString("de-DE")}` : " \xB7 noch kein Pr\xFCflauf von Proxmox gefunden"
      ] }),
      !n.error && n.last_check_ok === false && /* @__PURE__ */ jsxs8("p", { className: "text-xs text-amber-300", "data-testid": `updates-check-failed-${n.node}`, children: [
        "Die letzte Pr\xFCfung auf neue Pakete ist fehlgeschlagen",
        n.last_check_status ? `: ${n.last_check_status}` : "",
        ". Die Liste kann veraltet sein."
      ] }),
      !n.error && n.last_check_stale && /* @__PURE__ */ jsxs8("p", { className: "text-xs text-amber-300", "data-testid": `updates-check-stale-${n.node}`, children: [
        "Der n\xE4chtliche Pr\xFCflauf von Proxmox ist seit ",
        n.last_check_age_s ? `${Math.floor(n.last_check_age_s / 3600)} Stunden` : "\xFCber 36 Stunden",
        " nicht gelaufen. Die Liste kann veraltet sein; auf dem Knoten \u201Esystemctl status pve-daily-update.timer\u201C ansehen."
      ] }),
      n.packages.length > 0 && /* @__PURE__ */ jsxs8("details", { className: "mt-1", children: [
        /* @__PURE__ */ jsxs8("summary", { className: "cursor-pointer text-xs opacity-70", children: [
          "Pakete anzeigen (",
          n.packages.length,
          ")"
        ] }),
        /* @__PURE__ */ jsx8("table", { className: "mt-1 w-full text-xs", children: /* @__PURE__ */ jsx8("tbody", { className: "divide-y divide-white/5", children: n.packages.map((pkg) => /* @__PURE__ */ jsxs8("tr", { children: [
          /* @__PURE__ */ jsx8("td", { className: "py-0.5 pr-2 font-mono", children: pkg.package }),
          /* @__PURE__ */ jsx8("td", { className: "py-0.5 pr-2 whitespace-nowrap font-mono", children: pkg.new_package ? `neu: ${pkg.version}` : `${pkg.old_version} \u2192 ${pkg.version}` }),
          /* @__PURE__ */ jsx8("td", { className: "py-0.5 opacity-60", children: pkg.title })
        ] }, pkg.package)) }) })
      ] })
    ] }, n.node);
  }) }) });
}
function UnreachableHint({
  connection,
  sources,
  testId = `unreachable-${connection}`
}) {
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
  return /* @__PURE__ */ jsx8("div", { className: "mt-3 space-y-1 text-sm text-amber-300", "data-testid": testId, children: [...lines].map(([rest, whats]) => /* @__PURE__ */ jsx8("p", { children: `${whats.join(" und ")} ${rest}` }, rest)) });
}
function StorageSection({ pools, focusNode = null }) {
  return /* @__PURE__ */ jsx8("div", { children: /* @__PURE__ */ jsxs8("table", { className: "w-full text-sm", children: [
    /* @__PURE__ */ jsx8("thead", { children: /* @__PURE__ */ jsxs8("tr", { className: "border-b border-white/10 text-left text-xs opacity-60", children: [
      /* @__PURE__ */ jsx8("th", { className: "py-1 w-1/5", children: "Pool" }),
      /* @__PURE__ */ jsx8("th", { className: "py-1", children: "Inhalt" }),
      /* @__PURE__ */ jsx8("th", { className: "py-1 w-1/3", children: "Belegung" })
    ] }) }),
    /* @__PURE__ */ jsx8("tbody", { className: "divide-y divide-white/5", children: pools.map((pool) => /* @__PURE__ */ jsxs8("tr", { "data-testid": `pool-${pool.storage}`, className: focusNode && pool.node === focusNode ? FOCUS_CLASS : void 0, children: [
      /* @__PURE__ */ jsxs8("td", { className: "py-1.5 align-top", children: [
        /* @__PURE__ */ jsx8("span", { className: "font-medium", children: pool.storage }),
        /* @__PURE__ */ jsxs8("span", { className: "block text-xs opacity-60", children: [
          pool.type,
          pool.shared ? " \xB7 geteilt" : ""
        ] })
      ] }),
      /* @__PURE__ */ jsxs8("td", { className: "py-1.5 align-top text-xs", children: [
        /* @__PURE__ */ jsx8("span", { className: "opacity-70", children: pool.content_labels.join(", ") }),
        (pool.volumes ?? []).length > 0 && /* @__PURE__ */ jsxs8("span", { className: "block", children: [
          "Gast-Disks:",
          " ",
          (pool.volumes ?? []).map((v) => `${v.name} (${formatBytes2(v.size ?? 0)})`).join(", ")
        ] }),
        Object.values(pool.other ?? {}).length > 0 && /* @__PURE__ */ jsx8("span", { className: "block opacity-60", children: Object.values(pool.other ?? {}).map((o) => `${o.count} ${o.label} (${formatBytes2(o.size)})`).join(", ") })
      ] }),
      /* @__PURE__ */ jsxs8("td", { className: "py-1.5 align-top text-xs", children: [
        /* @__PURE__ */ jsx8("div", { className: "h-1.5 w-full overflow-hidden rounded bg-white/10", role: "progressbar", "aria-valuenow": pool.used_percent ?? 0, "aria-valuemin": 0, "aria-valuemax": 100, children: /* @__PURE__ */ jsx8("div", { className: `h-full ${BAR_TONE[pool.tone] ?? BAR_TONE.neutral}`, style: { width: `${Math.min(100, pool.used_percent ?? 0)}%` } }) }),
        /* @__PURE__ */ jsxs8("span", { className: "opacity-70", children: [
          formatBytes2(pool.used ?? 0),
          " / ",
          formatBytes2(pool.total ?? 0),
          pool.used_percent != null ? ` (${pool.used_percent} %)` : ""
        ] })
      ] })
    ] }, pool.id)) })
  ] }) });
}
function formatDuration(seconds) {
  if (seconds == null) return "";
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes}m ${seconds % 60}s` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}
function TaskRow({ task }) {
  const [lines, setLines] = useState7(null);
  const [open, setOpen] = useState7(false);
  const [error, setError] = useState7(null);
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
  return /* @__PURE__ */ jsxs8(Fragment4, { children: [
    /* @__PURE__ */ jsxs8("tr", { className: "cursor-pointer hover:bg-white/5", onClick: () => void toggle(), "data-testid": `task-${task.upid}`, children: [
      /* @__PURE__ */ jsx8("td", { className: "py-1 whitespace-nowrap opacity-70", children: task.starttime ? new Date(task.starttime * 1e3).toLocaleString("de-DE") : "" }),
      /* @__PURE__ */ jsxs8("td", { className: "py-1", children: [
        task.type_label,
        task.guest_name ? ` \xB7 ${task.guest_name}` : task.guest_id ? ` \xB7 ${task.guest_id}` : ""
      ] }),
      /* @__PURE__ */ jsx8("td", { className: "py-1 opacity-70", children: task.user }),
      /* @__PURE__ */ jsx8("td", { className: `py-1 break-words ${statusClass}`, children: statusText }),
      /* @__PURE__ */ jsx8("td", { className: "py-1 whitespace-nowrap opacity-70", children: formatDuration(task.duration_s) })
    ] }),
    open && /* @__PURE__ */ jsx8("tr", { children: /* @__PURE__ */ jsxs8("td", { colSpan: 5, className: "bg-black/30 p-2", children: [
      error && /* @__PURE__ */ jsx8("p", { className: "text-red-400", children: error }),
      !error && !lines && /* @__PURE__ */ jsx8("p", { className: "opacity-60", children: "Lade Protokoll \u2026" }),
      lines && /* @__PURE__ */ jsx8("pre", { className: "max-h-64 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px]", children: lines.join("\n") || "(leer)" })
    ] }) })
  ] });
}
function TasksTable({ tasks }) {
  if (tasks.length === 0) return /* @__PURE__ */ jsx8("p", { className: "text-xs opacity-60", children: "Keine Aufgaben in letzter Zeit." });
  return /* @__PURE__ */ jsx8("div", { className: "overflow-x-auto", children: /* @__PURE__ */ jsxs8("table", { className: "w-full min-w-[32rem] text-xs", children: [
    /* @__PURE__ */ jsx8("thead", { children: /* @__PURE__ */ jsxs8("tr", { className: "border-b border-white/10 text-left opacity-60", children: [
      /* @__PURE__ */ jsx8("th", { className: "py-1", children: "Zeit" }),
      /* @__PURE__ */ jsx8("th", { className: "py-1", children: "Aufgabe" }),
      /* @__PURE__ */ jsx8("th", { className: "py-1", children: "Benutzer" }),
      /* @__PURE__ */ jsx8("th", { className: "py-1", children: "Status" }),
      /* @__PURE__ */ jsx8("th", { className: "py-1", children: "Dauer" })
    ] }) }),
    /* @__PURE__ */ jsx8("tbody", { className: "divide-y divide-white/5", children: tasks.map((task) => /* @__PURE__ */ jsx8(TaskRow, { task }, task.upid)) })
  ] }) });
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
    // Kein Ballon-Wert heißt in Proxmox: Minimum = RAM.
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
  const [values, setValues] = useState7(initial);
  const [busy, setBusy] = useState7(false);
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
  return /* @__PURE__ */ jsxs8("form", { onSubmit: (e) => void save(e), className: "mt-2 rounded border border-white/10 bg-black/20 p-2", "data-testid": `edit-${hostId}`, children: [
    /* @__PURE__ */ jsxs8("div", { className: "flex flex-wrap items-end gap-3", children: [
      EDIT_KEYS[details.kind].map(
        (key) => key === "onboot" ? /* @__PURE__ */ jsxs8("label", { className: "flex items-center gap-1 pb-1", children: [
          /* @__PURE__ */ jsx8("input", { type: "checkbox", checked: values.onboot, onChange: (e) => setValues({ ...values, onboot: e.target.checked }) }),
          EDIT_LABEL[key]
        ] }, key) : /* @__PURE__ */ jsxs8("label", { className: "flex flex-col gap-0.5", children: [
          /* @__PURE__ */ jsx8("span", { className: "opacity-60", children: EDIT_LABEL[key] }),
          /* @__PURE__ */ jsx8(
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
      /* @__PURE__ */ jsx8("button", { type: "submit", disabled: busy || changed.length === 0, className: "rounded bg-white/15 px-3 py-1 hover:bg-white/25 disabled:opacity-40", children: busy ? "\u2026" : "Speichern" }),
      /* @__PURE__ */ jsx8("button", { type: "button", onClick: () => onDone(null), className: "rounded px-2 py-1 opacity-70 hover:opacity-100", children: "Abbrechen" })
    ] }),
    details.kind === "qemu" && /* @__PURE__ */ jsx8("p", { className: "mt-1 opacity-50", children: "Ballon-Minimum 0 schaltet Ballooning ab. Kerne und RAM greifen bei laufender VM meist erst nach einem Neustart." })
  ] });
}
function GuestDetailsPanel({ hostId }) {
  const [details, setDetails] = useState7(null);
  const [error, setError] = useState7(null);
  const [editing, setEditing] = useState7(false);
  const [editMessage, setEditMessage] = useState7(null);
  const [reloadKey, setReloadKey] = useState7(0);
  useEffect7(() => {
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
  if (error) return /* @__PURE__ */ jsxs8("p", { className: "mt-2 text-xs text-red-400", children: [
    "Details nicht verf\xFCgbar: ",
    error
  ] });
  if (!details) return /* @__PURE__ */ jsx8("p", { className: "mt-2 text-xs opacity-60", children: "Lade Details \u2026" });
  const d = details;
  const agent = d.kind === "qemu" ? agentText(d) : null;
  const cpu = d.kind === "qemu" ? `${d.cores * (d.sockets ?? 1)} vCPU${d.cpu_type ? ` (${d.cpu_type})` : ""}` : `${d.cores} Kerne`;
  const memory = d.kind === "qemu" ? `${formatMb(d.memory_mb)}${d.balloon_mb ? `, Ballooning ab ${formatMb(d.balloon_mb)}` : ""}` : `${formatMb(d.memory_mb)}${d.swap_mb ? ` + ${formatMb(d.swap_mb)} Swap` : ""}`;
  return /* @__PURE__ */ jsxs8("div", { className: "mt-2 text-xs", "data-testid": `details-${hostId}`, children: [
    deck().hasPermission("hosts.execute") && !editing && /* @__PURE__ */ jsx8("button", { type: "button", onClick: () => {
      setEditMessage(null);
      setEditing(true);
    }, className: "mb-2 px-2 py-1 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition", children: "Hardware \xE4ndern" }),
    editing && /* @__PURE__ */ jsx8(
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
    editMessage && /* @__PURE__ */ jsx8("p", { className: "mb-2 opacity-90", role: "status", children: editMessage }),
    /* @__PURE__ */ jsxs8("dl", { className: "grid grid-cols-2 gap-x-6 gap-y-1 opacity-80 sm:grid-cols-4", children: [
      /* @__PURE__ */ jsxs8("div", { children: [
        /* @__PURE__ */ jsx8("dt", { className: "opacity-60", children: "Prozessor" }),
        /* @__PURE__ */ jsx8("dd", { children: cpu })
      ] }),
      /* @__PURE__ */ jsxs8("div", { children: [
        /* @__PURE__ */ jsx8("dt", { className: "opacity-60", children: "Arbeitsspeicher" }),
        /* @__PURE__ */ jsx8("dd", { children: memory })
      ] }),
      /* @__PURE__ */ jsxs8("div", { children: [
        /* @__PURE__ */ jsx8("dt", { className: "opacity-60", children: "System" }),
        /* @__PURE__ */ jsxs8("dd", { children: [
          d.os ?? "?",
          d.kind === "qemu" ? ` \xB7 ${d.bios} \xB7 ${d.machine}` : d.unprivileged ? " \xB7 unprivilegiert" : " \xB7 privilegiert"
        ] })
      ] }),
      /* @__PURE__ */ jsxs8("div", { children: [
        /* @__PURE__ */ jsx8("dt", { className: "opacity-60", children: "Autostart" }),
        /* @__PURE__ */ jsx8("dd", { children: d.onboot ? `ja${d.startup_order != null ? ` (Reihenfolge ${d.startup_order})` : ""}` : "nein" })
      ] }),
      agent && /* @__PURE__ */ jsxs8("div", { children: [
        /* @__PURE__ */ jsx8("dt", { className: "opacity-60", children: "Gast-Agent" }),
        /* @__PURE__ */ jsx8("dd", { className: agent.className, children: agent.text })
      ] }),
      d.features && d.features.length > 0 && /* @__PURE__ */ jsxs8("div", { children: [
        /* @__PURE__ */ jsx8("dt", { className: "opacity-60", children: "Funktionen" }),
        /* @__PURE__ */ jsx8("dd", { children: d.features.join(", ") })
      ] }),
      d.passthrough.length > 0 && /* @__PURE__ */ jsxs8("div", { children: [
        /* @__PURE__ */ jsx8("dt", { className: "opacity-60", children: "Durchgereicht" }),
        /* @__PURE__ */ jsx8("dd", { children: d.passthrough.join(", ") })
      ] }),
      d.tags.length > 0 && /* @__PURE__ */ jsxs8("div", { children: [
        /* @__PURE__ */ jsx8("dt", { className: "opacity-60", children: "Tags" }),
        /* @__PURE__ */ jsx8("dd", { children: d.tags.join(", ") })
      ] })
    ] }),
    /* @__PURE__ */ jsx8("p", { className: "mt-2 mb-1 font-medium opacity-60", children: "Laufwerke" }),
    /* @__PURE__ */ jsx8("ul", { className: "space-y-0.5", children: d.disks.map((disk) => /* @__PURE__ */ jsxs8("li", { className: disk.kind === "unused" ? "text-amber-300" : "opacity-80", children: [
      /* @__PURE__ */ jsx8("span", { className: "font-mono opacity-60", children: disk.slot }),
      " ",
      diskLabel(disk)
    ] }, disk.slot)) }),
    /* @__PURE__ */ jsx8("p", { className: "mt-2 mb-1 font-medium opacity-60", children: "Netzwerk" }),
    /* @__PURE__ */ jsxs8("ul", { className: "space-y-0.5", children: [
      d.networks.map((nic) => /* @__PURE__ */ jsxs8("li", { className: "opacity-80", children: [
        /* @__PURE__ */ jsx8("span", { className: "font-mono opacity-60", children: nic.slot }),
        " ",
        nic.name ? `${nic.name} \xB7 ` : "",
        nic.bridge ?? "?",
        nic.vlan ? ` (VLAN ${nic.vlan})` : "",
        " \xB7 ",
        nic.model ?? "?",
        " \xB7 ",
        /* @__PURE__ */ jsx8("span", { className: "font-mono", children: nic.mac ?? "?" }),
        nic.firewall ? " \xB7 Firewall" : "",
        nic.link_down ? " \xB7 getrennt" : "",
        nic.ips.length > 0 ? ` \xB7 ${nic.ips.join(", ")}` : nic.configured_ip ? ` \xB7 ${nic.configured_ip === "dhcp" ? "DHCP" : nic.configured_ip}` : ""
      ] }, nic.slot)),
      d.other_ips.length > 0 && /* @__PURE__ */ jsxs8("li", { className: "opacity-60", children: [
        "Weitere Adressen im Gast: ",
        d.other_ips.join(", ")
      ] })
    ] })
  ] });
}
function NodeHealthPanel({ hostId }) {
  const [health, setHealth] = useState7(null);
  const [error, setError] = useState7(null);
  useEffect7(() => {
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
  if (error) return /* @__PURE__ */ jsxs8("p", { className: "mt-2 text-xs text-red-400", children: [
    "Knotendaten nicht verf\xFCgbar: ",
    error
  ] });
  if (!health) return /* @__PURE__ */ jsx8("p", { className: "mt-2 text-xs opacity-60", children: "Lade Knotendaten \u2026" });
  const h = health;
  const memUsed = h.mem_total != null && h.mem_available != null ? h.mem_total - h.mem_available : null;
  return /* @__PURE__ */ jsxs8("div", { className: "mt-2 text-xs", "data-testid": `node-health-${hostId}`, children: [
    /* @__PURE__ */ jsxs8("dl", { className: "grid grid-cols-2 gap-x-6 gap-y-1 opacity-80 sm:grid-cols-4", children: [
      /* @__PURE__ */ jsxs8("div", { className: "col-span-2", children: [
        /* @__PURE__ */ jsx8("dt", { className: "opacity-60", children: "Prozessor" }),
        /* @__PURE__ */ jsxs8("dd", { children: [
          h.cpu_model ?? "?",
          " (",
          h.cpu_cores ?? "?",
          " Kerne / ",
          h.cpu_threads ?? "?",
          " Threads)"
        ] })
      ] }),
      /* @__PURE__ */ jsxs8("div", { children: [
        /* @__PURE__ */ jsx8("dt", { className: "opacity-60", children: "Last (1/5/15 min)" }),
        /* @__PURE__ */ jsxs8("dd", { children: [
          h.loadavg.map((l) => l.toFixed(2)).join(" / "),
          " \xB7 IO-Wartezeit ",
          h.io_wait_percent,
          " %"
        ] })
      ] }),
      /* @__PURE__ */ jsxs8("div", { children: [
        /* @__PURE__ */ jsx8("dt", { className: "opacity-60", children: "L\xE4uft seit" }),
        /* @__PURE__ */ jsx8("dd", { children: formatUptime(h.uptime_s ?? 0) })
      ] }),
      /* @__PURE__ */ jsxs8("div", { children: [
        /* @__PURE__ */ jsx8("dt", { className: "opacity-60", children: "RAM belegt" }),
        /* @__PURE__ */ jsxs8("dd", { children: [
          memUsed != null ? formatBytes2(memUsed) : "?",
          " / ",
          formatBytes2(h.mem_total ?? 0),
          h.ksm_shared ? ` \xB7 KSM teilt ${formatBytes2(h.ksm_shared)}` : ""
        ] })
      ] }),
      /* @__PURE__ */ jsxs8("div", { children: [
        /* @__PURE__ */ jsx8("dt", { className: "opacity-60", children: "Swap belegt" }),
        /* @__PURE__ */ jsxs8("dd", { children: [
          formatBytes2(h.swap_used ?? 0),
          " / ",
          formatBytes2(h.swap_total ?? 0)
        ] })
      ] }),
      /* @__PURE__ */ jsxs8("div", { children: [
        /* @__PURE__ */ jsx8("dt", { className: "opacity-60", children: "Systemplatte" }),
        /* @__PURE__ */ jsxs8("dd", { children: [
          formatBytes2(h.rootfs_used ?? 0),
          " / ",
          formatBytes2(h.rootfs_total ?? 0)
        ] })
      ] }),
      /* @__PURE__ */ jsxs8("div", { children: [
        /* @__PURE__ */ jsx8("dt", { className: "opacity-60", children: "System" }),
        /* @__PURE__ */ jsxs8("dd", { children: [
          "Proxmox ",
          h.pve_version ?? "?",
          " \xB7 Kernel ",
          h.kernel ?? "?",
          h.boot_mode ? ` \xB7 ${h.boot_mode}` : ""
        ] })
      ] })
    ] }),
    /* @__PURE__ */ jsx8("p", { className: "mt-2 mb-1 font-medium opacity-60", children: "Datentr\xE4ger" }),
    h.disks_error && /* @__PURE__ */ jsxs8("p", { className: "text-red-400", children: [
      "Nicht abrufbar: ",
      h.disks_error
    ] }),
    /* @__PURE__ */ jsx8("ul", { className: "space-y-0.5", children: h.disks.map((d) => /* @__PURE__ */ jsxs8("li", { className: "flex flex-wrap items-center gap-2 opacity-90", children: [
      /* @__PURE__ */ jsx8("span", { className: `rounded px-1.5 py-0.5 ${BADGE_TONE[d.tone] ?? BADGE_TONE.neutral}`, children: d.badge }),
      /* @__PURE__ */ jsx8("span", { children: d.model }),
      /* @__PURE__ */ jsxs8("span", { className: "opacity-60", children: [
        d.summary,
        d.power_on_hours != null ? ` \xB7 ${d.power_on_hours.toLocaleString("de-DE")} Betriebsstunden` : ""
      ] })
    ] }, d.devpath)) }),
    ((h.zfs?.length ?? 0) > 0 || h.zfs_error) && /* @__PURE__ */ jsxs8(Fragment4, { children: [
      /* @__PURE__ */ jsx8("p", { className: "mt-2 mb-1 font-medium opacity-60", children: "ZFS-Pools" }),
      h.zfs_error && /* @__PURE__ */ jsxs8("p", { className: "text-red-400", children: [
        "Nicht abrufbar: ",
        h.zfs_error
      ] }),
      /* @__PURE__ */ jsx8("ul", { className: "space-y-0.5", "data-testid": "zfs-pools", children: (h.zfs ?? []).map((p) => /* @__PURE__ */ jsxs8("li", { className: "flex flex-wrap items-center gap-2 opacity-90", children: [
        /* @__PURE__ */ jsx8("span", { className: `rounded px-1.5 py-0.5 ${BADGE_TONE[p.tone] ?? BADGE_TONE.neutral}`, children: p.badge }),
        /* @__PURE__ */ jsx8("span", { children: p.name }),
        /* @__PURE__ */ jsx8("span", { className: "opacity-60", children: p.summary })
      ] }, p.name)) })
    ] })
  ] });
}
function NodeCard({
  node,
  focused = false,
  visit = 0,
  updates,
  onOpenUpdates
}) {
  const [open, setOpen] = useState7(focused);
  useEffect7(() => {
    if (focused) setOpen(true);
  }, [focused, visit]);
  return /* @__PURE__ */ jsxs8("div", { id: `host-${node.id}`, className: `mb-2 rounded border border-white/10 p-2 text-sm ${focused ? FOCUS_CLASS : ""}`, children: [
    /* @__PURE__ */ jsx8(
      "button",
      {
        type: "button",
        onClick: () => setOpen((v) => !v),
        "aria-label": open ? "Knotendetails einklappen" : "Knotendetails anzeigen",
        className: "mr-2 opacity-60 hover:opacity-100",
        children: open ? "\u25BE" : "\u25B8"
      }
    ),
    /* @__PURE__ */ jsx8("span", { className: "font-medium", children: node.display_name }),
    " ",
    /* @__PURE__ */ jsxs8("span", { className: "opacity-60", children: [
      "(Knoten) \u2013 ",
      HOST_STATUS_LABEL[node.status] ?? node.status
    ] }),
    updates && !updates.error && updates.tone !== "good" && /* @__PURE__ */ jsx8(
      "button",
      {
        type: "button",
        onClick: onOpenUpdates,
        title: "Zu den Updates",
        className: `ml-2 rounded px-1.5 py-0.5 text-xs hover:brightness-125 ${BADGE_TONE[updates.tone] ?? BADGE_TONE.neutral}`,
        children: updates.badge
      }
    ),
    open && /* @__PURE__ */ jsx8(NodeHealthPanel, { hostId: node.id })
  ] });
}
function MetricsPanel({ hostId }) {
  const [metrics, setMetrics] = useState7(null);
  const [error, setError] = useState7(null);
  useEffect7(() => {
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
  if (error) return /* @__PURE__ */ jsxs8("p", { className: "text-xs text-red-400", children: [
    "Metriken nicht verf\xFCgbar: ",
    error
  ] });
  if (!metrics) return /* @__PURE__ */ jsx8("p", { className: "text-xs opacity-60", children: "Lade Metriken \u2026" });
  const v = metrics.values;
  return /* @__PURE__ */ jsxs8("dl", { className: "grid grid-cols-2 gap-x-6 gap-y-1 text-xs opacity-80 sm:grid-cols-4", children: [
    /* @__PURE__ */ jsxs8("div", { children: [
      /* @__PURE__ */ jsx8("dt", { className: "opacity-60", children: "CPU" }),
      /* @__PURE__ */ jsxs8("dd", { children: [
        v.cpu_percent?.toFixed(1) ?? "?",
        " %"
      ] })
    ] }),
    /* @__PURE__ */ jsxs8("div", { children: [
      /* @__PURE__ */ jsx8("dt", { className: "opacity-60", children: "RAM" }),
      /* @__PURE__ */ jsxs8("dd", { children: [
        formatBytes2(v.mem_used_bytes ?? 0),
        " / ",
        formatBytes2(v.mem_total_bytes ?? 0)
      ] })
    ] }),
    /* @__PURE__ */ jsxs8("div", { children: [
      /* @__PURE__ */ jsx8("dt", { className: "opacity-60", children: "Laufzeit" }),
      /* @__PURE__ */ jsx8("dd", { children: formatUptime(v.uptime_s ?? 0) })
    ] }),
    /* @__PURE__ */ jsxs8("div", { children: [
      /* @__PURE__ */ jsx8("dt", { className: "opacity-60", children: "Stand" }),
      /* @__PURE__ */ jsx8("dd", { children: new Date(metrics.sampled_at).toLocaleTimeString("de-DE") })
    ] })
  ] });
}
function ConnectionsPanel({
  onConnectWithPassword,
  version = 0,
  bare = false
}) {
  const [connections, setConnections] = useState7(null);
  const [error, setError] = useState7(null);
  const [message, setMessage] = useState7(null);
  const [showAddForm, setShowAddForm] = useState7(false);
  const [newName, setNewName] = useState7("");
  const [newBaseUrl, setNewBaseUrl] = useState7("");
  const [newTokenId, setNewTokenId] = useState7("");
  const [newTlsInsecure, setNewTlsInsecure] = useState7(false);
  const [pending, setPending] = useState7(null);
  const [oldTokensFor, setOldTokensFor] = useState7(null);
  const [certificateFor, setCertificateFor] = useState7(null);
  const load = useCallback2(() => {
    setError(null);
    authedFetch("/ext/proxmox/connections").then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    }).then(setConnections).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);
  useEffect7(() => {
    load();
  }, [load, version]);
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
      const res = await authedFetchConfirmed(`/extensions/proxmox/secrets`, (confirmation) => ({
        method: "PUT",
        body: JSON.stringify({ label: `proxmox-token:${name}`, value, ...confirmation })
      }));
      if (res === null) return;
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
  if (error) return /* @__PURE__ */ jsxs8("p", { className: "mb-4 text-xs text-red-400", children: [
    "Verbindungen nicht ladbar: ",
    error
  ] });
  const content = /* @__PURE__ */ jsxs8("div", { className: bare ? "" : "mt-3", children: [
    message && /* @__PURE__ */ jsx8("p", { className: "mb-2 text-xs opacity-80", children: message }),
    connections && connections.length === 0 && /* @__PURE__ */ jsx8("p", { className: "mb-2 text-xs opacity-60", children: "Noch keine Verbindung konfiguriert." }),
    connections && connections.length > 0 && /* @__PURE__ */ jsxs8("table", { className: "mb-3 w-full text-xs", children: [
      /* @__PURE__ */ jsx8("thead", { children: /* @__PURE__ */ jsxs8("tr", { className: "border-b border-white/10 text-left opacity-60", children: [
        /* @__PURE__ */ jsx8("th", { className: "py-1 w-1/6", children: "Name" }),
        /* @__PURE__ */ jsx8("th", { className: "py-1 w-1/3", children: "Adresse" }),
        /* @__PURE__ */ jsx8("th", { className: "py-1 whitespace-nowrap", children: "Zertifikat" }),
        /* @__PURE__ */ jsx8("th", { className: "py-1 whitespace-nowrap", children: "Token" }),
        /* @__PURE__ */ jsx8("th", { className: "py-1 whitespace-nowrap", children: "Aktiv" }),
        /* @__PURE__ */ jsx8("th", { className: "py-1 whitespace-nowrap", children: "Aktionen" })
      ] }) }),
      /* @__PURE__ */ jsx8("tbody", { className: "divide-y divide-white/5", children: connections.map((c) => /* @__PURE__ */ jsxs8("tr", { children: [
        /* @__PURE__ */ jsx8("td", { className: "py-1.5 break-words", children: c.name }),
        /* @__PURE__ */ jsx8("td", { className: "py-1.5 break-all opacity-70", children: c.base_url }),
        /* @__PURE__ */ jsx8("td", { className: "py-1.5 whitespace-nowrap", children: /* @__PURE__ */ jsx8(CertificateStatus, { connection: c }) }),
        /* @__PURE__ */ jsx8("td", { className: "py-1.5 whitespace-nowrap", children: c.has_token ? "gesetzt" : /* @__PURE__ */ jsx8("span", { className: "text-amber-400", children: "fehlt" }) }),
        /* @__PURE__ */ jsx8("td", { className: "py-1.5", children: /* @__PURE__ */ jsx8(
          "button",
          {
            type: "button",
            disabled: pending === `toggle:${c.name}`,
            onClick: () => void toggleEnabled(c),
            className: "px-2 py-0.5 disabled:opacity-40 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
            children: c.enabled ? "aktiv" : "deaktiviert"
          }
        ) }),
        /* @__PURE__ */ jsx8("td", { className: "py-1.5", children: /* @__PURE__ */ jsxs8("div", { className: "flex gap-1.5", children: [
          /* @__PURE__ */ jsx8(
            "button",
            {
              type: "button",
              disabled: pending === `token:${c.name}`,
              onClick: () => void setToken(c.name, c.has_token),
              className: "px-2 py-1 disabled:opacity-40 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
              children: c.has_token ? "Token ersetzen" : "Token setzen"
            }
          ),
          /* @__PURE__ */ jsx8(
            "button",
            {
              type: "button",
              "aria-pressed": certificateFor === c.name,
              onClick: () => setCertificateFor(certificateFor === c.name ? null : c.name),
              className: "px-2 py-1 whitespace-nowrap border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
              children: "Zertifikat"
            }
          ),
          /* @__PURE__ */ jsx8(
            "button",
            {
              type: "button",
              "aria-pressed": oldTokensFor === c.name,
              onClick: () => setOldTokensFor(oldTokensFor === c.name ? null : c.name),
              className: "px-2 py-1 whitespace-nowrap border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
              children: "Alte Tokens"
            }
          ),
          /* @__PURE__ */ jsx8(
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
    certificateFor && connections?.some((c) => c.name === certificateFor) && /* @__PURE__ */ jsx8(
      ConnectionCertificate,
      {
        extId: "proxmox",
        connection: certificateFor,
        onSaved: load,
        unchecked: connections?.find((c) => c.name === certificateFor)?.tls_insecure_skip_verify ?? false
      }
    ),
    oldTokensFor && connections?.some((c) => c.name === oldTokensFor) && /* @__PURE__ */ jsx8(OldTokens, { connection: oldTokensFor }),
    !showAddForm && /* @__PURE__ */ jsxs8("div", { className: "flex flex-wrap gap-2", children: [
      onConnectWithPassword && /* @__PURE__ */ jsx8(
        "button",
        {
          type: "button",
          onClick: onConnectWithPassword,
          className: "px-2 py-1 text-xs accent-gradient text-on-accent rounded-lg hover:bg-accent-strong font-medium",
          children: "Mit Passwort verbinden"
        }
      ),
      /* @__PURE__ */ jsx8(
        "button",
        {
          type: "button",
          onClick: () => setShowAddForm(true),
          className: "px-2 py-1 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
          children: "+ Neue Verbindung"
        }
      )
    ] }),
    showAddForm && /* @__PURE__ */ jsxs8("form", { onSubmit: (e) => void addConnection(e), className: "flex flex-wrap items-end gap-2 text-xs", children: [
      /* @__PURE__ */ jsxs8("label", { className: "flex flex-col gap-1", children: [
        "Name",
        /* @__PURE__ */ jsx8("input", { required: true, value: newName, onChange: (e) => setNewName(e.target.value), className: "px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]" })
      ] }),
      /* @__PURE__ */ jsxs8("label", { className: "flex flex-col gap-1", children: [
        "Adresse (URL)",
        /* @__PURE__ */ jsx8(
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
      /* @__PURE__ */ jsxs8("label", { className: "flex flex-col gap-1", children: [
        "Token-ID",
        /* @__PURE__ */ jsx8(
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
      /* @__PURE__ */ jsxs8("label", { className: "flex items-center gap-1", title: "Veraltet: besser nach dem Anlegen unter \u201EZertifikat\u201C den Fingerabdruck merken.", children: [
        /* @__PURE__ */ jsx8("input", { type: "checkbox", checked: newTlsInsecure, onChange: (e) => setNewTlsInsecure(e.target.checked) }),
        "Zertifikat nicht pr\xFCfen (veraltet)"
      ] }),
      /* @__PURE__ */ jsx8("button", { type: "submit", disabled: pending === "add", className: "px-2 py-1 disabled:opacity-40 accent-gradient text-on-accent rounded-lg hover:bg-accent-strong font-medium", children: "Anlegen" }),
      /* @__PURE__ */ jsx8("button", { type: "button", onClick: () => setShowAddForm(false), className: "px-2 py-1 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition", children: "Abbrechen" })
    ] })
  ] });
  if (bare) {
    return /* @__PURE__ */ jsxs8("div", { className: "mb-6 p-3 panel", children: [
      /* @__PURE__ */ jsx8("h3", { className: "mb-3 text-sm font-medium", children: "Verbindungen verwalten" }),
      content
    ] });
  }
  return /* @__PURE__ */ jsxs8("details", { className: "mb-6 p-3 panel", open: connections?.length === 0, children: [
    /* @__PURE__ */ jsx8("summary", { className: "cursor-pointer text-sm font-medium", children: "Verbindungen verwalten" }),
    content
  ] });
}
function ProxmoxNodePage() {
  const unmountSignal = useUnmountSignal();
  const [urlParams, updateUrl, visits] = useUrlParams();
  const focusHost = urlParams.get("host") || null;
  const focusTasks = urlParams.get("tasks") === "1";
  const tabParam = urlParams.get("tab");
  const tab = isTab(tabParam) ? tabParam : focusTasks ? "tasks" : "guests";
  const setTab = useCallback2((id) => updateUrl({ tab: id, host: null, tasks: null }), [updateUrl]);
  const [hosts, setHosts] = useState7(null);
  const [error, setError] = useState7(null);
  const [pending, setPending] = useState7(() => /* @__PURE__ */ new Set());
  const [actionMessages, setActionMessages] = useState7(() => /* @__PURE__ */ new Map());
  const [expanded, setExpanded] = useState7(focusHost);
  const expandedFor = useRef3(visits);
  useEffect7(() => {
    if (expandedFor.current === visits) return;
    expandedFor.current = visits;
    setExpanded(focusHost);
  }, [visits, focusHost]);
  const taskFocus = focusTasks ? focusHost : null;
  const [snapshotRefresh, setSnapshotRefresh] = useState7(0);
  const [storage, setStorage] = useState7(null);
  const [tasks, setTasks] = useState7(null);
  const [updates, setUpdates] = useState7(null);
  const [updatesErrors, setUpdatesErrors] = useState7([]);
  const [storageErrors, setStorageErrors] = useState7([]);
  const [updatesLoadError, setUpdatesLoadError] = useState7(null);
  const [tasksLoadError, setTasksLoadError] = useState7(null);
  const [storageLoadError, setStorageLoadError] = useState7(null);
  const canConnect = deck().hasPermission("secrets.write");
  const [connectOpen, setConnectOpen] = useState7(false);
  const [connectionsVersion, setConnectionsVersion] = useState7(0);
  const [connectedAt, setConnectedAt] = useState7(0);
  const loadUpdates = useCallback2(() => {
    setUpdatesLoadError(null);
    fetchJson("/ext/proxmox/updates").then((body) => {
      setUpdates(body.nodes ?? []);
      setUpdatesErrors(body.errors ?? []);
    }).catch((err) => setUpdatesLoadError(errorMessage(err)));
  }, []);
  useEffect7(() => {
    loadUpdates();
  }, [loadUpdates]);
  const [showConsoleTasks, setShowConsoleTasks] = useState7(false);
  const loadTasks = useCallback2(() => {
    setTasksLoadError(null);
    fetchJson(`/ext/proxmox/tasks?limit=100${showConsoleTasks ? "&include_console=true" : ""}`).then((body) => setTasks(body.tasks ?? [])).catch((err) => setTasksLoadError(errorMessage(err)));
  }, [showConsoleTasks]);
  useEffect7(() => {
    loadTasks();
  }, [loadTasks, snapshotRefresh]);
  const loadStorage = useCallback2(() => {
    setStorageLoadError(null);
    fetchJson("/ext/proxmox/storage").then((body) => {
      setStorage(body.pools ?? []);
      setStorageErrors(body.errors ?? []);
    }).catch((err) => setStorageLoadError(errorMessage(err)));
  }, []);
  useEffect7(() => {
    loadStorage();
  }, [loadStorage]);
  const load = useCallback2(() => {
    setError(null);
    fetchJson("/hosts?tag=proxmox").then(setHosts).catch((err) => setError(errorMessage(err)));
  }, []);
  useEffect7(() => {
    load();
  }, [load]);
  useEffect7(() => {
    if (!connectedAt) return void 0;
    const timer = window.setTimeout(load, RELOAD_AFTER_CONNECT_MS);
    return () => window.clearTimeout(timer);
  }, [connectedAt, load]);
  const scrolledFor = useRef3(null);
  useEffect7(() => {
    if (!hosts || !focusHost || scrolledFor.current === visits) return;
    const target = hosts.find((h) => h.id === focusHost);
    if (!target) return;
    if (tab === "tasks" && !tasks) return;
    if (tab === "storage" && !storage) return;
    scrolledFor.current = visits;
    const { connection } = parseProviderRef(target.provider_ref);
    scrollToElement(tab === "tasks" ? `tasks-${connection}` : tab === "storage" ? `storage-${connection}` : `host-${target.id}`);
  }, [hosts, tasks, storage, focusHost, tab, visits]);
  const [shieldLinks, setShieldLinks] = useState7({});
  const nodeIds = (hosts ?? []).filter((h) => h.kind === "hypervisor" || h.kind === "node").map((h) => h.id).join(",");
  useEffect7(() => {
    if (tab !== "updates" || !nodeIds) return;
    let cancelled = false;
    void Promise.all(nodeIds.split(",").map(async (id) => {
      try {
        return [id, shieldUpdatesHref(await fetchJson(`/hosts/${encodeURIComponent(id)}/tools`))];
      } catch {
        return [id, null];
      }
    })).then((pairs) => {
      if (cancelled) return;
      setShieldLinks(Object.fromEntries(pairs.filter((p) => p[1] !== null)));
    });
    return () => {
      cancelled = true;
    };
  }, [tab, nodeIds]);
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
  if (!hosts && !error) return /* @__PURE__ */ jsx8("div", { className: "p-6 text-sm opacity-60", children: "Lade \u2026" });
  if (error) {
    return /* @__PURE__ */ jsx8("div", { className: "mx-auto w-full max-w-7xl p-4 sm:p-6", children: /* @__PURE__ */ jsx8(LoadError, { what: "Die Liste der Proxmox-Server", error, onRetry: load }) });
  }
  const nodes = (hosts ?? []).filter((h) => h.kind === "hypervisor" || h.kind === "node");
  const guests = (hosts ?? []).filter((h) => h.kind === "vm" || h.kind === "lxc");
  const connections = [...new Set((hosts ?? []).map((h) => parseProviderRef(h.provider_ref).connection))].sort();
  const empty = !hosts || hosts.length === 0;
  const taskHost = (hosts ?? []).find((h) => h.id === taskFocus) ?? null;
  const taskRef = taskHost ? parseProviderRef(taskHost.provider_ref) : null;
  function tasksFor(connection) {
    const all = (tasks ?? []).filter((t) => t.connection === connection);
    if (!taskRef || taskRef.connection !== connection) return all;
    return all.filter((t) => t.node === taskRef.node && (taskRef.vmid === null || t.guest_id === taskRef.vmid));
  }
  const taskConnections = taskRef ? connections.filter((c) => c === taskRef.connection) : connections;
  const storageFocus = tab === "storage" && focusHost ? (hosts ?? []).find((h) => h.id === focusHost) : void 0;
  const storageRef = storageFocus ? parseProviderRef(storageFocus.provider_ref) : null;
  const shieldHrefFor = (u) => {
    const node = nodes.find((h) => {
      const ref = parseProviderRef(h.provider_ref);
      return ref.connection === u.connection && ref.node === u.node;
    });
    return node ? shieldLinks[node.id] : void 0;
  };
  const nodeUpdates = (node) => {
    const ref = parseProviderRef(node.provider_ref);
    return updates?.find((u) => u.connection === ref.connection && u.node === ref.node);
  };
  const ownUpdates = (updates ?? []).filter((u) => connections.includes(u.connection));
  const ownPools = (storage ?? []).filter((p) => connections.includes(p.connection));
  return /* @__PURE__ */ jsxs8("div", { className: "mx-auto w-full max-w-7xl p-4 sm:p-6", children: [
    /* @__PURE__ */ jsx8("h2", { className: "font-semibold text-xl tracking-tight", children: "Proxmox" }),
    /* @__PURE__ */ jsx8("p", { className: "mb-4 text-sm opacity-60", children: "Deine Proxmox-Server mit allen VMs und LXC: starten, herunterfahren, Snapshots, Speicher und Updates." }),
    empty && /* @__PURE__ */ jsx8(ConnectionsPanel, { onConnectWithPassword: canConnect ? () => setConnectOpen(true) : void 0, version: connectionsVersion }),
    empty && connectOpen && /* @__PURE__ */ jsx8(
      ProxmoxConnect,
      {
        onClose: () => setConnectOpen(false),
        onConnected: () => {
          setConnectionsVersion((n) => n + 1);
          setConnectedAt(Date.now());
          load();
        }
      }
    ),
    empty && !connectOpen && /* @__PURE__ */ jsx8(
      EmptyState,
      {
        icon: "refresh",
        title: "Noch keine Proxmox-Server gefunden",
        text: canConnect ? "Am einfachsten: \u201EMit Passwort verbinden\u201C \u2013 Adresse und Passwort deines Proxmox-Servers eingeben, Nodvard Deck legt dort einen eigenen Zugang an. Oder trag einen API-Token von Hand ein (oben unter \u201EVerbindungen verwalten\u201C oder in den Moduleinstellungen). Danach liest Nodvard Deck alle 5 Minuten alle Knoten, VMs und LXC von selbst ein." : "Trag daf\xFCr die Adresse deines Proxmox-Servers und einen API-Token ein (oben unter \u201EVerbindungen verwalten\u201C oder in den Moduleinstellungen). Danach liest Nodvard Deck alle 5 Minuten alle Knoten, VMs und LXC von selbst ein.",
        action: /* @__PURE__ */ jsxs8("div", { className: "flex flex-wrap items-center justify-center gap-2", children: [
          canConnect && /* @__PURE__ */ jsx8(Button, { variant: "primary", onClick: () => setConnectOpen(true), children: "Mit Passwort verbinden" }),
          /* @__PURE__ */ jsx8(SettingsLink, { to: "/settings/extensions/proxmox", permission: "extensions.manage", children: "Proxmox einrichten" })
        ] })
      }
    ),
    !empty && /* @__PURE__ */ jsxs8(Fragment4, { children: [
      /* @__PURE__ */ jsx8(
        Summary,
        {
          nodes,
          guests,
          connections,
          updates: updatesLoadError ? "error" : updates ? ownUpdates : null,
          pools: storageLoadError ? "error" : storage ? ownPools : null,
          updatesIncomplete: updatesErrors.some((e) => connections.includes(e.connection)),
          storageIncomplete: storageErrors.some((e) => connections.includes(e.connection)),
          onOpen: setTab
        }
      ),
      /* @__PURE__ */ jsx8(
        TabBar,
        {
          active: tab,
          onSelect: setTab,
          badges: {
            updates: updatesCount(ownUpdates) > 0 ? { text: String(updatesCount(ownUpdates)), label: `${updatesCount(ownUpdates)} Pakete warten` } : void 0,
            tasks: tasksLoadError ? { text: "!", label: "nicht abrufbar" } : void 0
          }
        }
      ),
      [...actionMessages].map(([key, text]) => /* @__PURE__ */ jsx8("p", { className: "mb-3 text-sm opacity-80", children: text }, key)),
      /* @__PURE__ */ jsx8(TabPanel, { id: "guests", active: tab, children: connections.map((connection) => /* @__PURE__ */ jsxs8("section", { className: "mb-6", children: [
        /* @__PURE__ */ jsx8("h3", { className: "mb-2 text-sm font-medium opacity-60", children: connection }),
        nodes.filter((n) => parseProviderRef(n.provider_ref).connection === connection).map((node) => /* @__PURE__ */ jsx8(
          NodeCard,
          {
            node,
            focused: node.id === focusHost && tab === "guests",
            visit: visits,
            updates: nodeUpdates(node),
            onOpenUpdates: () => setTab("updates")
          },
          node.id
        )),
        /* @__PURE__ */ jsxs8("table", { className: "w-full text-sm", children: [
          /* @__PURE__ */ jsx8("thead", { children: /* @__PURE__ */ jsxs8("tr", { className: "border-b border-white/10 text-left text-xs opacity-60", children: [
            /* @__PURE__ */ jsx8("th", { className: "py-1 w-6" }),
            /* @__PURE__ */ jsx8("th", { className: "py-1 sm:w-1/2", children: "Name" }),
            /* @__PURE__ */ jsx8("th", { className: "hidden py-1 whitespace-nowrap sm:table-cell sm:w-28", children: "Status" }),
            /* @__PURE__ */ jsx8("th", { className: "py-1 text-right", children: "Aktionen" })
          ] }) }),
          /* @__PURE__ */ jsx8("tbody", { className: "divide-y divide-white/5", children: guests.filter((g) => parseProviderRef(g.provider_ref).connection === connection).map((host) => {
            const { node } = parseProviderRef(host.provider_ref);
            const isExpanded = expanded === host.id;
            const visible = VM_ACTIONS.filter((a) => !a.showWhen || a.showWhen(host.status));
            const runAction2 = (action) => void trigger(host.id, host.display_name, action.type, action.confirm, {}, action.dangerConfirm);
            const isBusy = (type) => pending.has(`${host.id}:${type}`);
            return /* @__PURE__ */ jsxs8(Fragment3, { children: [
              /* @__PURE__ */ jsxs8("tr", { id: `host-${host.id}`, className: host.id === focusHost ? FOCUS_CLASS : void 0, children: [
                /* @__PURE__ */ jsx8("td", { className: "py-1.5 align-top", children: /* @__PURE__ */ jsx8(
                  "button",
                  {
                    type: "button",
                    onClick: () => setExpanded(isExpanded ? null : host.id),
                    "aria-label": isExpanded ? "Details einklappen" : "Details anzeigen",
                    className: "opacity-60 hover:opacity-100",
                    children: isExpanded ? "\u25BE" : "\u25B8"
                  }
                ) }),
                /* @__PURE__ */ jsxs8("td", { className: "py-1.5 break-words", children: [
                  /* @__PURE__ */ jsx8("span", { className: "font-medium", children: host.display_name }),
                  /* @__PURE__ */ jsxs8("span", { className: "block text-xs opacity-60", children: [
                    KIND_LABEL[host.kind ?? ""] ?? host.kind,
                    " \xB7 ",
                    node
                  ] }),
                  /* @__PURE__ */ jsx8("span", { className: `block text-xs sm:hidden ${STATUS_TONE[host.status] ?? ""}`, children: HOST_STATUS_LABEL[host.status] ?? host.status })
                ] }),
                /* @__PURE__ */ jsx8("td", { className: `hidden py-1.5 whitespace-nowrap sm:table-cell ${STATUS_TONE[host.status] ?? ""}`, children: HOST_STATUS_LABEL[host.status] ?? host.status }),
                /* @__PURE__ */ jsx8("td", { className: "py-1.5", children: /* @__PURE__ */ jsxs8("div", { className: "flex flex-wrap justify-end gap-1.5", children: [
                  host.status === "up" ? /* @__PURE__ */ jsx8(
                    "a",
                    {
                      href: `/console/${host.id}`,
                      target: "_blank",
                      rel: "noreferrer",
                      className: "accent-soft rounded-lg px-2.5 py-1 text-xs hover:brightness-125",
                      children: "Konsole"
                    }
                  ) : /* @__PURE__ */ jsx8(
                    "span",
                    {
                      title: "Nur f\xFCr laufende VMs/LXC verf\xFCgbar.",
                      className: "cursor-not-allowed rounded-lg bg-white/5 px-2.5 py-1 text-xs opacity-40",
                      children: "Konsole"
                    }
                  ),
                  visible.filter((a) => a.main).map((action) => /* @__PURE__ */ jsx8(
                    "button",
                    {
                      type: "button",
                      disabled: isBusy(action.type),
                      onClick: () => runAction2(action),
                      className: "px-2.5 py-1 text-xs disabled:opacity-40 border rounded-lg transition border-white/10 bg-white/[0.06] hover:bg-white/[0.12]",
                      children: isBusy(action.type) ? "\u2026" : action.label
                    },
                    action.type
                  )),
                  visible.some((a) => !a.main) && /* @__PURE__ */ jsx8(
                    MoreMenu,
                    {
                      small: true,
                      align: "right",
                      label: visible.some((a) => !a.main && isBusy(a.type)) ? "Mehr \u2026" : "Mehr",
                      title: `Weitere Aktionen f\xFCr ${host.display_name}`,
                      testId: `more-${host.id}`,
                      items: visible.filter((a) => !a.main).map((action) => ({
                        id: action.type,
                        label: isBusy(action.type) ? `${action.label} (l\xE4uft \u2026)` : action.label,
                        disabled: isBusy(action.type),
                        danger: action.variant === "danger",
                        run: () => runAction2(action)
                      }))
                    }
                  )
                ] }) })
              ] }),
              isExpanded && /* @__PURE__ */ jsxs8("tr", { children: [
                /* @__PURE__ */ jsx8("td", {}),
                /* @__PURE__ */ jsxs8("td", { colSpan: 3, className: "bg-black/20 py-2", children: [
                  /* @__PURE__ */ jsx8(MetricsPanel, { hostId: host.id }),
                  /* @__PURE__ */ jsx8(GuestDetailsPanel, { hostId: host.id }),
                  /* @__PURE__ */ jsx8("p", { className: "mt-2 mb-1 text-xs font-medium opacity-60", children: "Snapshots" }),
                  /* @__PURE__ */ jsx8(
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
        /* @__PURE__ */ jsx8(UnreachableHint, { connection, sources: [["Updates", updatesErrors], ["Speicher", storageErrors]] })
      ] }, connection)) }),
      /* @__PURE__ */ jsxs8(TabPanel, { id: "storage", active: tab, children: [
        storageLoadError && /* @__PURE__ */ jsx8(LoadError, { what: "Die Liste der Speicher", error: storageLoadError, onRetry: loadStorage }),
        !storage && !storageLoadError && /* @__PURE__ */ jsx8("p", { className: "text-sm opacity-60", children: "Lade Speicher \u2026" }),
        connections.map((connection) => /* @__PURE__ */ jsxs8("section", { id: `storage-${connection}`, className: "mb-6", children: [
          /* @__PURE__ */ jsx8("h3", { className: "mb-2 text-sm font-medium opacity-60", children: connection }),
          storage && !storage.some((p) => p.connection === connection) && !storageErrors.some((e) => e.connection === connection) && /* @__PURE__ */ jsx8("p", { className: "text-xs opacity-60", children: "Keine Speicher gefunden." }),
          storage && storage.some((p) => p.connection === connection) && /* @__PURE__ */ jsx8(
            StorageSection,
            {
              pools: storage.filter((p) => p.connection === connection),
              focusNode: storageRef?.connection === connection ? storageRef.node : null
            }
          ),
          /* @__PURE__ */ jsx8(UnreachableHint, { connection, sources: [["Speicher", storageErrors]], testId: `unreachable-storage-${connection}` })
        ] }, connection))
      ] }),
      /* @__PURE__ */ jsxs8(TabPanel, { id: "updates", active: tab, children: [
        updatesLoadError && /* @__PURE__ */ jsx8(LoadError, { what: "Die Liste der Updates", error: updatesLoadError, onRetry: loadUpdates }),
        !updates && !updatesLoadError && /* @__PURE__ */ jsx8("p", { className: "text-sm opacity-60", children: "Lade Updates \u2026" }),
        updates && /* @__PURE__ */ jsx8("p", { className: "mb-3 text-xs opacity-60", children: "Hier steht, was auf den Knoten wartet. Einspielen kannst du die Updates in Nodvard Shield unter \u201EUpdates\u201C (daf\xFCr braucht der Knoten einen SSH-Zugang) oder wie gewohnt in der Proxmox-Oberfl\xE4che." }),
        connections.map((connection) => /* @__PURE__ */ jsxs8("section", { className: "mb-6", children: [
          /* @__PURE__ */ jsx8("h3", { className: "mb-2 text-sm font-medium opacity-60", children: connection }),
          updates && updates.some((u) => u.connection === connection) && /* @__PURE__ */ jsx8(UpdatesSection, { nodes: updates.filter((u) => u.connection === connection), shieldHref: shieldHrefFor }),
          updates && !updates.some((u) => u.connection === connection) && !updatesErrors.some((e) => e.connection === connection) && /* @__PURE__ */ jsx8("p", { className: "text-xs opacity-60", children: "Keine Knoten gefunden." }),
          /* @__PURE__ */ jsx8(UnreachableHint, { connection, sources: [["Updates", updatesErrors]], testId: `unreachable-updates-${connection}` })
        ] }, connection))
      ] }),
      /* @__PURE__ */ jsxs8(TabPanel, { id: "tasks", active: tab, children: [
        tasksLoadError && /* @__PURE__ */ jsx8(LoadError, { what: "Der Aufgabenverlauf", error: tasksLoadError, onRetry: loadTasks }),
        !tasks && !tasksLoadError && /* @__PURE__ */ jsx8("p", { className: "text-sm opacity-60", children: "Lade Aufgabenverlauf \u2026" }),
        /* @__PURE__ */ jsxs8("div", { className: "mb-3 flex flex-wrap items-center gap-3 text-xs opacity-80", children: [
          /* @__PURE__ */ jsxs8("label", { className: "flex items-center gap-1", children: [
            /* @__PURE__ */ jsx8("input", { type: "checkbox", checked: showConsoleTasks, onChange: () => setShowConsoleTasks((v) => !v) }),
            "Konsolen-\xD6ffnungen anzeigen"
          ] }),
          taskHost && /* @__PURE__ */ jsxs8("span", { className: "accent-soft flex items-center gap-1 rounded px-2 py-0.5", children: [
            "Nur ",
            taskHost.display_name,
            /* @__PURE__ */ jsx8(
              "button",
              {
                type: "button",
                onClick: () => updateUrl({ tasks: null, tab: "tasks" }),
                "aria-label": "Filter entfernen",
                className: "opacity-70 hover:opacity-100",
                children: "\u2715"
              }
            )
          ] })
        ] }),
        tasks && taskConnections.map((connection) => {
          const list = tasksFor(connection);
          return /* @__PURE__ */ jsxs8("section", { id: `tasks-${connection}`, className: "mb-6", children: [
            /* @__PURE__ */ jsx8("h3", { className: "mb-1 text-sm font-medium opacity-60", children: connection }),
            list.length > 0 && /* @__PURE__ */ jsx8("p", { className: "mb-1 text-xs opacity-60", "data-testid": `tasks-count-${connection}`, children: list.length === 1 ? "1 Aufgabe" : `${list.length} Aufgaben` }),
            /* @__PURE__ */ jsx8(TasksTable, { tasks: list })
          ] }, connection);
        })
      ] }),
      /* @__PURE__ */ jsxs8(TabPanel, { id: "connections", active: tab, children: [
        connectOpen && /* @__PURE__ */ jsx8(
          ProxmoxConnect,
          {
            onClose: () => setConnectOpen(false),
            onConnected: () => {
              setConnectionsVersion((n) => n + 1);
              setConnectedAt(Date.now());
              load();
            }
          }
        ),
        /* @__PURE__ */ jsx8(ConnectionsPanel, { bare: true, onConnectWithPassword: canConnect ? () => setConnectOpen(true) : void 0, version: connectionsVersion })
      ] })
    ] })
  ] });
}
var TABS = [
  { id: "guests", label: "VMs & LXC" },
  { id: "storage", label: "Speicher" },
  { id: "updates", label: "Updates" },
  { id: "tasks", label: "Aufgabenverlauf" },
  { id: "connections", label: "Verbindungen" }
];
function isTab(value) {
  return TABS.some((t) => t.id === value);
}
function TabBar({ active, onSelect, badges }) {
  const refs = useRef3({});
  const bar = useRef3(null);
  useEffect7(() => {
    const tab = refs.current[active];
    if (!tab || !bar.current) return;
    const t = tab.getBoundingClientRect();
    const b = bar.current.getBoundingClientRect();
    if (t.left < b.left || t.right > b.right) bar.current.scrollLeft += t.left - b.left - 16;
  }, [active]);
  function onKeyDown(e) {
    const index = TABS.findIndex((t) => t.id === active);
    const next = e.key === "ArrowRight" ? (index + 1) % TABS.length : e.key === "ArrowLeft" ? (index - 1 + TABS.length) % TABS.length : e.key === "Home" ? 0 : e.key === "End" ? TABS.length - 1 : -1;
    if (next < 0) return;
    e.preventDefault();
    onSelect(TABS[next].id);
    refs.current[TABS[next].id]?.focus();
  }
  return /* @__PURE__ */ jsx8("div", { ref: bar, role: "tablist", "aria-label": "Bereiche", className: "mb-4 flex gap-1 overflow-x-auto border-b border-white/[0.08] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden", children: TABS.map((t) => {
    const badge = badges[t.id];
    return /* @__PURE__ */ jsxs8(
      "button",
      {
        ref: (el) => {
          refs.current[t.id] = el;
        },
        type: "button",
        role: "tab",
        id: `pve-tab-${t.id}`,
        "aria-selected": active === t.id,
        "aria-controls": `pve-panel-${t.id}`,
        tabIndex: active === t.id ? 0 : -1,
        onClick: () => onSelect(t.id),
        onKeyDown,
        className: `-mb-px whitespace-nowrap border-b-2 px-3.5 py-2 text-sm transition ${active === t.id ? "border-[var(--color-accent)] text-white" : "border-transparent text-white/55 hover:text-white"}`,
        children: [
          t.label,
          badge && /* @__PURE__ */ jsxs8("span", { className: "ml-1.5 rounded-full bg-amber-500/80 px-1.5 text-[10px] text-black", title: badge.label, children: [
            /* @__PURE__ */ jsx8("span", { "aria-hidden": "true", children: badge.text }),
            /* @__PURE__ */ jsx8("span", { className: "sr-only", children: `: ${badge.label}` })
          ] })
        ]
      },
      t.id
    );
  }) });
}
function TabPanel({ id, active, children }) {
  return /* @__PURE__ */ jsx8("div", { role: "tabpanel", id: `pve-panel-${id}`, "aria-labelledby": `pve-tab-${id}`, hidden: active !== id, tabIndex: 0, className: "outline-none", children });
}
function updatesCount(nodes) {
  return nodes.reduce((sum, n) => sum + (n.count ?? 0), 0);
}
var STATUS_TONE = { up: "text-emerald-300", down: "opacity-60", maintenance: "text-amber-300" };
var TILE_TONE = { good: "text-emerald-300", warn: "text-amber-300", bad: "text-red-300", neutral: "text-white" };
function Tile({ label, value, hint, tone = "neutral", onClick, testId }) {
  return /* @__PURE__ */ jsxs8("button", { type: "button", onClick, "data-testid": testId, className: "panel px-4 py-3 text-left transition hover:bg-white/[0.04]", children: [
    /* @__PURE__ */ jsx8("span", { className: "block text-xs text-white/50", children: label }),
    /* @__PURE__ */ jsx8("span", { className: `mt-1 block text-xl font-semibold tabular-nums ${TILE_TONE[tone]}`, children: value }),
    /* @__PURE__ */ jsx8("span", { className: "mt-0.5 block text-[11px] text-white/40", children: hint })
  ] });
}
function Summary({ nodes, guests, connections, updates, pools, updatesIncomplete, storageIncomplete, onOpen }) {
  const nodesUp = nodes.filter((n) => n.status === "up").length;
  const guestsUp = guests.filter((g) => g.status === "up").length;
  const guestsDown = guests.filter((g) => g.status === "down").length;
  let updatesTile;
  if (updates === "error") updatesTile = { value: "?", hint: "gerade nicht abrufbar", tone: "warn" };
  else if (updates === null) updatesTile = { value: "\u2026", hint: "wird geladen", tone: "neutral" };
  else {
    const count = updatesCount(updates);
    const reboot = updates.some((u) => u.reboot_pending);
    const incomplete = updatesIncomplete || updates.some((u) => u.error) || connections.some((c) => !updates.some((u) => u.connection === c));
    const outdated = updates.some((u) => !u.error && (u.last_check_ok === false || u.last_check_stale));
    if (count > 0) updatesTile = { value: String(count), hint: count === 1 ? "Paket wartet" : "Pakete warten", tone: "warn" };
    else if (reboot) updatesTile = { value: "Neustart", hint: "Neustart n\xF6tig, damit ein Update gilt", tone: "warn" };
    else if (incomplete) updatesTile = { value: "?", hint: "nicht alle Knoten abrufbar", tone: "warn" };
    else if (outdated) updatesTile = { value: "?", hint: "Pr\xFCfung auf Updates veraltet oder fehlgeschlagen", tone: "warn" };
    else updatesTile = { value: "aktuell", hint: "alles auf dem neuesten Stand", tone: "good" };
    if (count > 0 && reboot) updatesTile.hint += " \xB7 Neustart n\xF6tig";
    if ((count > 0 || reboot) && incomplete) updatesTile.hint += " \xB7 nicht alles abrufbar";
  }
  let storageTile;
  if (pools === "error") storageTile = { value: "?", hint: "gerade nicht abrufbar", tone: "warn" };
  else if (pools === null) storageTile = { value: "\u2026", hint: "wird geladen", tone: "neutral" };
  else {
    const fullest = [...pools].filter((p) => p.used_percent != null).sort((a, b) => (b.used_percent ?? 0) - (a.used_percent ?? 0))[0];
    const unavailable = pools.find((p) => p.used_percent == null && p.tone === "danger");
    storageTile = unavailable ? { value: "!", hint: `${unavailable.storage}${unavailable.shared ? "" : ` (${unavailable.node})`} nicht verf\xFCgbar`, tone: "bad" } : fullest ? {
      value: `${Math.round(fullest.used_percent ?? 0)} %`,
      hint: `am vollsten: ${fullest.storage}${fullest.shared ? "" : ` (${fullest.node})`}`,
      tone: fullest.tone === "danger" ? "bad" : fullest.tone === "warn" ? "warn" : "good"
    } : storageIncomplete ? { value: "?", hint: "gerade nicht abrufbar", tone: "warn" } : { value: "\u2013", hint: "keine Speicher gefunden", tone: "neutral" };
    if (fullest && !unavailable && storageIncomplete) {
      storageTile.hint += " \xB7 nicht alles abrufbar";
      if (storageTile.tone === "good") storageTile.tone = "warn";
    }
  }
  return /* @__PURE__ */ jsxs8("div", { className: "mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4", "data-testid": "pve-summary", children: [
    /* @__PURE__ */ jsx8(
      Tile,
      {
        testId: "tile-nodes",
        label: "Knoten",
        value: `${nodesUp}/${nodes.length}`,
        hint: "online",
        tone: nodes.length === 0 ? "neutral" : nodesUp < nodes.length ? "bad" : "good",
        onClick: () => onOpen("guests")
      }
    ),
    /* @__PURE__ */ jsx8(
      Tile,
      {
        testId: "tile-guests",
        label: "VMs & LXC",
        value: `${guestsUp}/${guests.length}`,
        hint: guestsDown > 0 ? `laufen \xB7 ${guestsDown} gestoppt` : "laufen",
        onClick: () => onOpen("guests")
      }
    ),
    /* @__PURE__ */ jsx8(Tile, { testId: "tile-updates", label: "Updates", ...updatesTile, onClick: () => onOpen("updates") }),
    /* @__PURE__ */ jsx8(Tile, { testId: "tile-storage", label: "Speicher", ...storageTile, onClick: () => onOpen("storage") })
  ] });
}
export {
  ProxmoxNodePage,
  editChanges,
  shieldUpdatesHref
};
