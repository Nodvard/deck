// src/QbittorrentPage.tsx
import { useCallback, useEffect as useEffect3, useRef as useRef2, useState as useState2 } from "react";

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

// ../../../frontend/src/lib/deckGlobal.ts
function findDeck() {
  if (typeof window === "undefined") return void 0;
  return window.__nodvardDeck;
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

// src/QbittorrentPage.tsx
import { Fragment as Fragment2, jsx as jsx4, jsxs as jsxs3 } from "react/jsx-runtime";
var API = "/ext/qbittorrent";
var SETTINGS_PATH = "/settings/extensions/qbittorrent";
var PROBLEM = {
  auth_failed: { label: "Anmeldung fehlgeschlagen", tone: "bad" },
  unreachable: { label: "nicht erreichbar", tone: "bad" },
  error: { label: "Fehler", tone: "warn" }
};
var STATE_LABEL = {
  downloading: { label: "l\xE4dt", tone: "good" },
  stalled_downloading: { label: "wartet auf Quellen", tone: "neutral" },
  uploading: { label: "verteilt", tone: "good" },
  stalled_uploading: { label: "verteilt", tone: "neutral" },
  paused: { label: "pausiert", tone: "neutral" },
  queued: { label: "wartet", tone: "neutral" },
  checking: { label: "pr\xFCft", tone: "neutral" },
  moving: { label: "verschiebt", tone: "neutral" },
  error: { label: "Fehler", tone: "bad" }
};
var CONNECTION_LABEL = {
  connected: { label: "verbunden", tone: "good" },
  firewalled: { label: "hinter Firewall", tone: "warn" },
  disconnected: { label: "getrennt", tone: "bad" }
};
var UNITS = ["B", "KB", "MB", "GB", "TB"];
function sizeText(count) {
  let value = count;
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit > 0 && value < 10 ? 1 : 0;
  const rounded = Math.floor(value * 10 ** digits + 0.5) / 10 ** digits;
  return `${rounded.toFixed(digits).replace(".", ",")} ${UNITS[unit]}`;
}
function speedText(count) {
  return `${sizeText(count)}/s`;
}
function durationText(seconds) {
  let minutes = Math.floor(seconds / 60);
  if (minutes < 1) return "< 1 Min.";
  if (minutes < 60) return `${minutes} Min.`;
  let hours = Math.floor(minutes / 60);
  minutes %= 60;
  if (hours < 24) return minutes ? `${hours} Std. ${minutes} Min.` : `${hours} Std.`;
  const days = Math.floor(hours / 24);
  hours %= 24;
  const dayText = days === 1 ? "1 Tag" : `${days} Tage`;
  return hours ? `${dayText} ${hours} Std.` : dayText;
}
function torrentDetail(torrent) {
  const parts = [`${torrent.progress_percent} %`];
  if (torrent.download_speed) parts.push(`\u2193 ${speedText(torrent.download_speed)}`);
  if (torrent.upload_speed) parts.push(`\u2191 ${speedText(torrent.upload_speed)}`);
  if (torrent.state === "downloading" && torrent.eta_seconds !== null) parts.push(`noch ${durationText(torrent.eta_seconds)}`);
  if (torrent.size !== null) parts.push(sizeText(torrent.size));
  return parts.join(" \xB7 ");
}
function headline(connection) {
  if (connection.state === "not_configured") return { label: "nicht eingerichtet", tone: "neutral" };
  if (connection.state !== "ok") return PROBLEM[connection.state] ?? PROBLEM.error;
  const transfer = connection.transfer;
  const errors = connection.counts?.error ?? 0;
  if (connection.problems.length > 0) return { label: "unvollst\xE4ndig", tone: "warn" };
  if (transfer?.connection === "disconnected") return { label: "getrennt", tone: "bad" };
  if (errors > 0) return { label: `${errors} Fehler`, tone: "warn" };
  if (transfer && (transfer.download_speed > 0 || transfer.upload_speed > 0)) return { label: "aktiv", tone: "good" };
  return { label: "ruhig", tone: "neutral" };
}
function limitText(transfer) {
  const parts = [];
  if (transfer.download_limit !== null) parts.push(`Download bis ${speedText(transfer.download_limit)}`);
  if (transfer.upload_limit !== null) parts.push(`Upload bis ${speedText(transfer.upload_limit)}`);
  return `${transfer.alt_limits ? "Alternative Tempolimits an" : "Tempolimit"}: ${parts.join(", ") || "an"}`;
}
function TorrentRow({ torrent }) {
  const state = STATE_LABEL[torrent.state];
  return /* @__PURE__ */ jsxs3("li", { "data-testid": "torrent", className: "space-y-2 px-4 py-3 sm:px-5", children: [
    /* @__PURE__ */ jsxs3("div", { className: "flex flex-wrap items-start justify-between gap-x-4 gap-y-1", children: [
      /* @__PURE__ */ jsxs3("div", { className: "min-w-0 flex-1 basis-48", children: [
        /* @__PURE__ */ jsx4("p", { className: "break-words text-sm font-medium", children: torrent.name }),
        /* @__PURE__ */ jsx4("p", { className: "break-words text-xs text-white/60", children: torrentDetail(torrent) })
      ] }),
      state && /* @__PURE__ */ jsx4(Badge, { tone: state.tone, children: state.label })
    ] }),
    /* @__PURE__ */ jsx4(
      "div",
      {
        role: "progressbar",
        "aria-label": `Fortschritt von ${torrent.name}`,
        "aria-valuemin": 0,
        "aria-valuemax": 100,
        "aria-valuenow": torrent.progress_percent,
        className: "h-1 w-full overflow-hidden rounded bg-white/10",
        children: /* @__PURE__ */ jsx4("div", { className: "h-full rounded bg-[var(--color-accent)]", style: { width: `${torrent.progress_percent}%` } })
      }
    )
  ] });
}
function Section({ title, children }) {
  return /* @__PURE__ */ jsxs3("div", { className: "border-t border-white/[0.06] pt-3", children: [
    /* @__PURE__ */ jsx4("h4", { className: "mb-2 text-xs font-semibold uppercase tracking-wide text-white/50", children: title }),
    children
  ] });
}
function ConnectionCard({ connection, onRetry }) {
  const head = headline(connection);
  const problem = connection.state !== "ok" && connection.state !== "not_configured";
  const canManage = deck().hasPermission("extensions.manage");
  const facts = [
    connection.version ? `Version ${connection.version}` : null,
    connection.api_version ? `Web-API ${connection.api_version}` : null
  ].filter(Boolean);
  const ok = connection.state === "ok";
  const transfer = connection.transfer;
  const counts = connection.counts;
  const active = connection.active;
  const link = transfer ? CONNECTION_LABEL[transfer.connection] : void 0;
  const openHref = safeHref(connection.url);
  return /* @__PURE__ */ jsx4(
    Card,
    {
      title: connection.name,
      description: openHref ? /* @__PURE__ */ jsx4("a", { href: openHref, target: "_blank", rel: "noreferrer", className: "break-all hover:text-white hover:underline", children: "qBittorrent \xF6ffnen" }) : void 0,
      actions: /* @__PURE__ */ jsx4(Badge, { tone: head.tone, children: head.label }),
      children: /* @__PURE__ */ jsxs3("div", { "data-testid": `connection-${connection.name}`, className: "space-y-4", children: [
        facts.length > 0 && /* @__PURE__ */ jsx4("p", { "data-testid": "facts", className: "break-words text-sm text-white/70", children: facts.join(" \xB7 ") }),
        connection.state === "not_configured" && /* @__PURE__ */ jsxs3("div", { "data-testid": "not-configured", className: "flex flex-col items-start gap-3", children: [
          /* @__PURE__ */ jsx4("p", { className: "break-words text-sm text-white/60", children: connection.message }),
          canManage ? /* @__PURE__ */ jsx4("a", { href: SETTINGS_PATH, className: buttonClass("primary", true), children: "Jetzt einrichten" }) : /* @__PURE__ */ jsx4("p", { className: "text-xs text-white/45", children: "Ein Administrator kann das unter Einstellungen \u2192 Erweiterungen \u2192 qBittorrent einrichten." })
        ] }),
        problem && /* @__PURE__ */ jsxs3("div", { "data-testid": "problem", role: "alert", className: "flex flex-col items-start gap-3", children: [
          /* @__PURE__ */ jsx4("p", { className: "break-words text-sm text-red-300", children: connection.message }),
          /* @__PURE__ */ jsxs3("div", { className: "flex flex-wrap items-center gap-2", children: [
            connection.state !== "auth_failed" && /* @__PURE__ */ jsx4(Button, { small: true, onClick: onRetry, children: "Erneut versuchen" }),
            canManage && /* @__PURE__ */ jsx4("a", { href: SETTINGS_PATH, className: buttonClass("ghost", true), children: "Einstellungen" })
          ] })
        ] }),
        ok && connection.problems.length > 0 && /* @__PURE__ */ jsx4("ul", { "data-testid": "problems", role: "alert", className: "space-y-1 text-sm text-amber-300", children: connection.problems.map((text) => /* @__PURE__ */ jsx4("li", { className: "break-words", children: text }, text)) }),
        ok && connection.notes.length > 0 && /* @__PURE__ */ jsx4("ul", { "data-testid": "notes", className: "space-y-1 text-sm text-white/60", children: connection.notes.map((text) => /* @__PURE__ */ jsx4("li", { className: "break-words", children: text }, text)) }),
        ok && (transfer || connection.free_space !== null) && /* @__PURE__ */ jsxs3("div", { "data-testid": "transfer", className: "grid grid-cols-2 gap-3 lg:grid-cols-4", children: [
          transfer && /* @__PURE__ */ jsx4(Stat, { label: "Herunterladen", value: speedText(transfer.download_speed) }),
          transfer && /* @__PURE__ */ jsx4(Stat, { label: "Hochladen", value: speedText(transfer.upload_speed) }),
          transfer && /* @__PURE__ */ jsx4(Stat, { label: "Verbindung", value: link?.label ?? "unbekannt", tone: link?.tone }),
          /* @__PURE__ */ jsx4(Stat, { label: "Freier Platz", value: connection.free_space !== null ? sizeText(connection.free_space) : "unbekannt" })
        ] }),
        ok && transfer?.limited && /* @__PURE__ */ jsx4("p", { "data-testid": "limit", className: "text-sm text-amber-300", children: limitText(transfer) }),
        ok && counts && /* @__PURE__ */ jsx4(Section, { title: `Torrents (${counts.total.toLocaleString("de-DE")})`, children: counts.total === 0 ? /* @__PURE__ */ jsx4("p", { "data-testid": "no-torrents", className: "text-sm text-white/50", children: "Noch keine Torrents." }) : /* @__PURE__ */ jsxs3("div", { "data-testid": "counts", className: "grid grid-cols-2 gap-3 lg:grid-cols-5", children: [
          /* @__PURE__ */ jsx4(Stat, { label: "Laden", value: counts.downloading.toLocaleString("de-DE") }),
          /* @__PURE__ */ jsx4(Stat, { label: "Verteilen", value: counts.seeding.toLocaleString("de-DE") }),
          /* @__PURE__ */ jsx4(Stat, { label: "Pausiert", value: counts.paused.toLocaleString("de-DE") }),
          /* @__PURE__ */ jsx4(Stat, { label: "Fehler", value: counts.error.toLocaleString("de-DE"), tone: counts.error > 0 ? "warn" : void 0 }),
          /* @__PURE__ */ jsx4(Stat, { label: "Warten oder pr\xFCfen", value: counts.other.toLocaleString("de-DE") })
        ] }) }),
        ok && active && /* @__PURE__ */ jsx4(Section, { title: `Aktiv (${active.length}${connection.active_more ? "+" : ""})`, children: active.length === 0 ? /* @__PURE__ */ jsx4("p", { "data-testid": "no-active", className: "text-sm text-white/50", children: "Gerade bewegt sich nichts." }) : /* @__PURE__ */ jsxs3(Fragment2, { children: [
          /* @__PURE__ */ jsx4("ul", { "data-testid": "active", className: "-mx-5 divide-y divide-white/[0.06] border-t border-white/[0.06]", children: active.map((t, i) => /* @__PURE__ */ jsx4(TorrentRow, { torrent: t }, `${i}-${t.name}`)) }),
          connection.active_more && /* @__PURE__ */ jsxs3("p", { className: "mt-2 text-xs text-white/45", children: [
            "Es sind noch mehr aktiv (hier werden die ersten ",
            active.length,
            " gezeigt)."
          ] })
        ] }) })
      ] })
    }
  );
}
function QbittorrentPage() {
  const unmountSignal = useUnmountSignal();
  const [data, setData] = useState2(null);
  const [error, setError] = useState2(null);
  const [loading, setLoading] = useState2(true);
  const seq = useRef2(0);
  const load = useCallback((refresh) => {
    const mine = ++seq.current;
    setLoading(true);
    fetchJson(`${API}/overview${refresh ? "?refresh=true" : ""}`, { signal: unmountSignal() }).then((body) => {
      if (mine !== seq.current) return;
      setData(body);
      setError(null);
    }).catch((err) => {
      if (mine !== seq.current || err instanceof DOMException && err.name === "AbortError") return;
      setError(errorMessage(err));
    }).finally(() => {
      if (mine === seq.current) setLoading(false);
    });
  }, [unmountSignal]);
  useEffect3(() => {
    load(false);
  }, [load]);
  const reload = useCallback(() => load(true), [load]);
  const connections = data?.connections ?? [];
  return /* @__PURE__ */ jsxs3(
    Page,
    {
      title: "qBittorrent",
      description: "Zeigt Geschwindigkeit, aktive Torrents, die Zahl je Zustand und den freien Platz. Nur lesend.",
      actions: /* @__PURE__ */ jsxs3(Fragment2, { children: [
        deck().hasPermission("extensions.manage") && /* @__PURE__ */ jsx4("a", { href: SETTINGS_PATH, className: buttonClass("ghost"), children: "Einstellungen" }),
        /* @__PURE__ */ jsx4(Button, { onClick: reload, disabled: loading, children: "Aktualisieren" })
      ] }),
      children: [
        error && /* @__PURE__ */ jsx4(LoadError, { what: "Der Zustand von qBittorrent", error, onRetry: reload }),
        !data && !error && /* @__PURE__ */ jsx4(Card, { children: /* @__PURE__ */ jsx4("p", { className: "text-sm text-white/50", children: "Lade \u2026" }) }),
        data && connections.length === 0 && /* @__PURE__ */ jsx4(
          EmptyState,
          {
            icon: "clock",
            title: "Noch keine Verbindung",
            text: deck().hasPermission("extensions.manage") ? "Trage qBittorrent unter Einstellungen \u2192 Erweiterungen \u2192 qBittorrent ein." : "Ein Administrator kann qBittorrent unter Einstellungen \u2192 Erweiterungen \u2192 qBittorrent eintragen.",
            action: /* @__PURE__ */ jsx4(SettingsLink, { to: SETTINGS_PATH, permission: "extensions.manage", children: "Jetzt einrichten" })
          }
        ),
        connections.length > 0 && /* @__PURE__ */ jsx4("div", { className: "space-y-6", children: connections.map((c, i) => /* @__PURE__ */ jsx4(ConnectionCard, { connection: c, onRetry: reload }, `${c.name}-${i}`)) })
      ]
    }
  );
}
export {
  QbittorrentPage,
  durationText,
  sizeText,
  speedText,
  torrentDetail
};
