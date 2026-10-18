// src/JellyfinPage.tsx
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

// ../../../frontend/src/lib/deckTimezone.ts
import { useSyncExternalStore } from "react";
function getDeckTimezone() {
  const zone = findDeck()?.timezone;
  return typeof zone === "string" && zone ? zone : null;
}
function subscribe(onChange) {
  const name = deckEventName("timezone");
  window.addEventListener(name, onChange);
  return () => window.removeEventListener(name, onChange);
}
function useDeckTimezone() {
  return useSyncExternalStore(subscribe, getDeckTimezone, () => null);
}

// src/JellyfinPage.tsx
import { Fragment as Fragment2, jsx as jsx4, jsxs as jsxs3 } from "react/jsx-runtime";
var API = "/ext/jellyfin";
var SETTINGS_PATH = "/settings/extensions/jellyfin";
var PROBLEM = {
  auth_failed: { label: "Anmeldung fehlgeschlagen", tone: "bad" },
  unreachable: { label: "nicht erreichbar", tone: "bad" },
  error: { label: "Fehler", tone: "warn" }
};
var COUNT_LABELS = [
  ["movies", "Filme"],
  ["series", "Serien"],
  ["episodes", "Folgen"],
  ["albums", "Alben"],
  ["songs", "Titel"],
  ["music_videos", "Musikvideos"],
  ["box_sets", "Sammlungen"],
  ["books", "B\xFCcher"]
];
var METHOD_LABEL = { direct_play: "direkt", direct_stream: "Direktstream", transcode: "transkodiert" };
function numberText(count) {
  return count.toLocaleString("de-DE");
}
function episodeCode(season, episode) {
  const pad = (n) => String(n).padStart(2, "0");
  if (season !== null && episode !== null) return `S${pad(season)}E${pad(episode)}`;
  if (season !== null) return `S${pad(season)}`;
  return episode !== null ? `E${pad(episode)}` : "";
}
function mediaDetail(media) {
  if (media.kind === "episode") return [media.series, episodeCode(media.season, media.episode)].filter(Boolean).join(" ");
  if (media.kind === "audio") return media.artist ?? "";
  return media.year !== null ? String(media.year) : "";
}
function headline(connection) {
  if (connection.state === "not_configured") return { label: "nicht eingerichtet", tone: "neutral" };
  if (connection.state !== "ok") return PROBLEM[connection.state] ?? PROBLEM.error;
  if (connection.problems.length > 0) return { label: "unvollst\xE4ndig", tone: "warn" };
  const playing = connection.playback_count ?? 0;
  if (playing > 0) return { label: playing === 1 ? "1 l\xE4uft" : `${playing} laufen`, tone: "good" };
  return { label: "nichts l\xE4uft", tone: "neutral" };
}
function addedText(iso, zone) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const options = { day: "2-digit", month: "2-digit", year: "numeric" };
  try {
    return date.toLocaleDateString("de-DE", zone ? { ...options, timeZone: zone } : options);
  } catch {
    return date.toLocaleDateString("de-DE", options);
  }
}
function PlaybackRow({ playback }) {
  const detail = mediaDetail(playback);
  const where = playback.device ?? playback.client;
  const who = [playback.user ?? "Unbekannt", where].filter(Boolean).join(" \xB7 ");
  const method = playback.play_method ? METHOD_LABEL[playback.play_method] : null;
  return /* @__PURE__ */ jsxs3("li", { "data-testid": "playback", className: "space-y-2 px-4 py-3 sm:px-5", children: [
    /* @__PURE__ */ jsxs3("div", { className: "flex flex-wrap items-start justify-between gap-x-4 gap-y-1", children: [
      /* @__PURE__ */ jsxs3("div", { className: "min-w-0 flex-1 basis-48", children: [
        /* @__PURE__ */ jsx4("p", { className: "break-words text-sm font-medium", children: playback.title }),
        detail && /* @__PURE__ */ jsx4("p", { className: "break-words text-xs text-white/60", children: detail }),
        /* @__PURE__ */ jsx4("p", { className: "break-words text-xs text-white/50", children: who })
      ] }),
      /* @__PURE__ */ jsxs3("div", { className: "flex flex-none flex-wrap items-center justify-end gap-1.5", children: [
        playback.paused && /* @__PURE__ */ jsx4(Badge, { tone: "neutral", children: "pausiert" }),
        method && /* @__PURE__ */ jsx4(Badge, { tone: playback.transcoding ? "warn" : "good", children: method }),
        playback.progress_percent !== null && /* @__PURE__ */ jsxs3("span", { className: "text-xs tabular-nums text-white/70", children: [
          playback.progress_percent,
          " %"
        ] })
      ] })
    ] }),
    playback.progress_percent !== null && /* @__PURE__ */ jsx4(
      "div",
      {
        role: "progressbar",
        "aria-label": `Fortschritt von ${playback.title}`,
        "aria-valuemin": 0,
        "aria-valuemax": 100,
        "aria-valuenow": playback.progress_percent,
        className: "h-1 w-full overflow-hidden rounded bg-white/10",
        children: /* @__PURE__ */ jsx4("div", { className: "h-full rounded bg-[var(--color-accent)]", style: { width: `${playback.progress_percent}%` } })
      }
    )
  ] });
}
function LatestRow({ item, zone }) {
  const detail = mediaDetail(item);
  const added = addedText(item.added_at, zone);
  return /* @__PURE__ */ jsxs3("li", { "data-testid": "latest-item", className: "flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-3 sm:px-5", children: [
    /* @__PURE__ */ jsxs3("div", { className: "min-w-0 flex-1 basis-48", children: [
      /* @__PURE__ */ jsx4("p", { className: "break-words text-sm font-medium", children: item.title }),
      detail && /* @__PURE__ */ jsx4("p", { className: "break-words text-xs text-white/50", children: detail })
    ] }),
    added && /* @__PURE__ */ jsx4("span", { className: "flex-none text-xs tabular-nums text-white/60", children: added })
  ] });
}
function Section({ title, children }) {
  return /* @__PURE__ */ jsxs3("div", { className: "border-t border-white/[0.06] pt-3", children: [
    /* @__PURE__ */ jsx4("h4", { className: "mb-2 text-xs font-semibold uppercase tracking-wide text-white/50", children: title }),
    children
  ] });
}
function ConnectionCard({ connection, zone, onRetry }) {
  const head = headline(connection);
  const problem = connection.state !== "ok" && connection.state !== "not_configured";
  const canManage = deck().hasPermission("extensions.manage");
  const facts = [
    connection.server_name ? `Server ${connection.server_name}` : null,
    connection.version ? `Version ${connection.version}` : null
  ].filter(Boolean);
  const playbacks = connection.playbacks;
  const hiddenPlaybacks = playbacks && connection.playback_count !== null ? connection.playback_count - playbacks.length : 0;
  const counts = connection.counts;
  const shownCounts = counts ? COUNT_LABELS.filter(([key]) => (counts[key] ?? 0) > 0) : [];
  const ok = connection.state === "ok";
  const openHref = safeHref(connection.url);
  return /* @__PURE__ */ jsx4(
    Card,
    {
      title: connection.name,
      description: openHref ? /* @__PURE__ */ jsx4("a", { href: openHref, target: "_blank", rel: "noreferrer", className: "break-all hover:text-white hover:underline", children: "Jellyfin \xF6ffnen" }) : void 0,
      actions: /* @__PURE__ */ jsx4(Badge, { tone: head.tone, children: head.label }),
      children: /* @__PURE__ */ jsxs3("div", { "data-testid": `connection-${connection.name}`, className: "space-y-4", children: [
        facts.length > 0 && /* @__PURE__ */ jsx4("p", { "data-testid": "facts", className: "break-words text-sm text-white/70", children: facts.join(" \xB7 ") }),
        connection.state === "not_configured" && /* @__PURE__ */ jsxs3("div", { "data-testid": "not-configured", className: "flex flex-col items-start gap-3", children: [
          /* @__PURE__ */ jsx4("p", { className: "text-sm text-white/60", children: connection.message }),
          canManage ? /* @__PURE__ */ jsx4("a", { href: SETTINGS_PATH, className: buttonClass("primary", true), children: "Jetzt einrichten" }) : /* @__PURE__ */ jsx4("p", { className: "text-xs text-white/45", children: "Ein Administrator kann das unter Einstellungen \u2192 Erweiterungen \u2192 Jellyfin einrichten." })
        ] }),
        problem && /* @__PURE__ */ jsxs3("div", { "data-testid": "problem", role: "alert", className: "flex flex-col items-start gap-3", children: [
          /* @__PURE__ */ jsx4("p", { className: "break-words text-sm text-red-300", children: connection.message }),
          /* @__PURE__ */ jsxs3("div", { className: "flex flex-wrap items-center gap-2", children: [
            connection.state !== "auth_failed" && /* @__PURE__ */ jsx4(Button, { small: true, onClick: onRetry, children: "Erneut versuchen" }),
            canManage && /* @__PURE__ */ jsx4("a", { href: SETTINGS_PATH, className: buttonClass("ghost", true), children: "Einstellungen" })
          ] })
        ] }),
        ok && connection.problems.length > 0 && /* @__PURE__ */ jsx4("ul", { "data-testid": "problems", role: "alert", className: "space-y-1 text-sm text-amber-300", children: connection.problems.map((text) => /* @__PURE__ */ jsx4("li", { className: "break-words", children: text }, text)) }),
        ok && playbacks && /* @__PURE__ */ jsx4(Section, { title: `L\xE4uft gerade (${connection.playback_count ?? playbacks.length})`, children: playbacks.length === 0 ? /* @__PURE__ */ jsx4("p", { "data-testid": "no-playbacks", className: "text-sm text-white/50", children: "Gerade l\xE4uft nichts." }) : /* @__PURE__ */ jsxs3(Fragment2, { children: [
          /* @__PURE__ */ jsx4("ul", { "data-testid": "playbacks", className: "-mx-5 divide-y divide-white/[0.06] border-t border-white/[0.06]", children: playbacks.map((p, i) => /* @__PURE__ */ jsx4(PlaybackRow, { playback: p }, `${i}-${p.user}-${p.title}`)) }),
          hiddenPlaybacks > 0 && /* @__PURE__ */ jsxs3("p", { className: "mt-2 text-xs text-white/45", children: [
            "\u2026 und ",
            hiddenPlaybacks,
            " weitere (hier werden die ersten ",
            playbacks.length,
            " gezeigt)."
          ] })
        ] }) }),
        ok && counts && /* @__PURE__ */ jsx4(Section, { title: "Bibliotheken", children: shownCounts.length === 0 ? /* @__PURE__ */ jsx4("p", { "data-testid": "no-media", className: "text-sm text-white/50", children: "Noch keine Medien in den Bibliotheken." }) : /* @__PURE__ */ jsx4("div", { "data-testid": "counts", className: "grid grid-cols-2 gap-3 lg:grid-cols-4", children: shownCounts.map(([key, label]) => /* @__PURE__ */ jsx4(Stat, { label, value: numberText(counts[key] ?? 0) }, key)) }) }),
        ok && connection.latest && /* @__PURE__ */ jsx4(Section, { title: "Zuletzt hinzugef\xFCgt", children: connection.latest.length === 0 ? /* @__PURE__ */ jsx4("p", { "data-testid": "no-latest", className: "text-sm text-white/50", children: "Noch nichts hinzugef\xFCgt." }) : /* @__PURE__ */ jsx4("ul", { "data-testid": "latest", className: "-mx-5 divide-y divide-white/[0.06] border-t border-white/[0.06]", children: connection.latest.map((item, i) => /* @__PURE__ */ jsx4(LatestRow, { item, zone }, `${i}-${item.title}`)) }) })
      ] })
    }
  );
}
function JellyfinPage() {
  const unmountSignal = useUnmountSignal();
  const zone = useDeckTimezone();
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
      title: "Jellyfin",
      description: "Zeigt, was gerade l\xE4uft, wie gro\xDF die Bibliotheken sind und was zuletzt hinzugekommen ist. Nur lesend.",
      actions: /* @__PURE__ */ jsxs3(Fragment2, { children: [
        deck().hasPermission("extensions.manage") && /* @__PURE__ */ jsx4("a", { href: SETTINGS_PATH, className: buttonClass("ghost"), children: "Einstellungen" }),
        /* @__PURE__ */ jsx4(Button, { onClick: reload, disabled: loading, children: "Aktualisieren" })
      ] }),
      children: [
        error && /* @__PURE__ */ jsx4(LoadError, { what: "Der Zustand von Jellyfin", error, onRetry: reload }),
        !data && !error && /* @__PURE__ */ jsx4(Card, { children: /* @__PURE__ */ jsx4("p", { className: "text-sm text-white/50", children: "Lade \u2026" }) }),
        data && connections.length === 0 && /* @__PURE__ */ jsx4(
          EmptyState,
          {
            icon: "clock",
            title: "Noch keine Verbindung",
            text: deck().hasPermission("extensions.manage") ? "Trage Jellyfin unter Einstellungen \u2192 Erweiterungen \u2192 Jellyfin ein." : "Ein Administrator kann Jellyfin unter Einstellungen \u2192 Erweiterungen \u2192 Jellyfin eintragen.",
            action: /* @__PURE__ */ jsx4(SettingsLink, { to: SETTINGS_PATH, permission: "extensions.manage", children: "Jetzt einrichten" })
          }
        ),
        connections.length > 0 && /* @__PURE__ */ jsx4("div", { className: "space-y-6", children: connections.map((c, i) => /* @__PURE__ */ jsx4(ConnectionCard, { connection: c, zone, onRetry: reload }, `${c.name}-${i}`)) })
      ]
    }
  );
}
export {
  JellyfinPage,
  episodeCode,
  mediaDetail,
  numberText
};
