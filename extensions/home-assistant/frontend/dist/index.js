// src/HomeAssistantPage.tsx
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

// src/HomeAssistantPage.tsx
import { Fragment as Fragment2, jsx as jsx4, jsxs as jsxs3 } from "react/jsx-runtime";
var API = "/ext/home-assistant";
var SETTINGS_PATH = "/settings/extensions/home-assistant";
var OPEN_LIST_UP_TO = 10;
var PROBLEM = {
  auth_failed: { label: "Anmeldung fehlgeschlagen", tone: "bad" },
  unreachable: { label: "nicht erreichbar", tone: "bad" },
  error: { label: "Fehler", tone: "warn" }
};
var DECIMAL = /^-?\d+\.\d+$/;
function valueText(state, unit) {
  if (state === null) return "\u2013";
  const text = DECIMAL.test(state) ? state.replace(".", ",") : state;
  return unit ? `${text} ${unit}` : text;
}
function plural(count, one, many) {
  return `${count} ${count === 1 ? one : many}`;
}
function headline(connection) {
  if (connection.state === "not_configured") return { label: "nicht eingerichtet", tone: "neutral" };
  if (connection.state !== "ok") return PROBLEM[connection.state] ?? PROBLEM.error;
  const counts = connection.counts;
  if (counts && counts.updates > 0) return { label: plural(counts.updates, "Update", "Updates"), tone: "warn" };
  if (counts && counts.unavailable > 0) return { label: `${counts.unavailable} nicht verf\xFCgbar`, tone: "warn" };
  return { label: "alles in Ordnung", tone: "good" };
}
function EntityRow({ entity }) {
  const shownName = entity.status === "missing" ? entity.entity_id : entity.name;
  let value;
  if (entity.status === "unavailable") value = /* @__PURE__ */ jsx4(Badge, { tone: "warn", children: "nicht verf\xFCgbar" });
  else if (entity.status === "unknown") value = /* @__PURE__ */ jsx4(Badge, { tone: "neutral", children: "unbekannt" });
  else if (entity.status === "missing") value = /* @__PURE__ */ jsx4(Badge, { tone: "warn", children: "nicht gefunden" });
  else value = /* @__PURE__ */ jsx4("span", { className: "text-sm font-medium tabular-nums", children: valueText(entity.state, entity.unit) });
  return /* @__PURE__ */ jsxs3("li", { "data-testid": "entity", className: "flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-3 sm:px-5", children: [
    /* @__PURE__ */ jsxs3("div", { className: "min-w-0 flex-1 basis-48", children: [
      /* @__PURE__ */ jsx4("p", { className: "break-words text-sm font-medium", children: shownName }),
      /* @__PURE__ */ jsx4("p", { className: "break-all text-xs text-white/50", children: entity.status === "missing" ? "Diese Kennung gibt es in Home Assistant nicht." : entity.entity_id })
    ] }),
    /* @__PURE__ */ jsx4("div", { className: "flex-none break-words text-right", children: value })
  ] });
}
function UpdateRow({ update }) {
  const versions = [update.installed_version, update.latest_version];
  return /* @__PURE__ */ jsxs3("li", { "data-testid": "update", className: "flex flex-wrap items-center justify-between gap-x-4 gap-y-1 bg-amber-500/[0.06] px-4 py-3 sm:px-5", children: [
    /* @__PURE__ */ jsxs3("div", { className: "min-w-0 flex-1 basis-48", children: [
      /* @__PURE__ */ jsx4("p", { className: "break-words text-sm font-medium", children: update.name }),
      /* @__PURE__ */ jsx4("p", { className: "break-all text-xs text-white/50", children: update.entity_id })
    ] }),
    (versions[0] || versions[1]) && /* @__PURE__ */ jsxs3("span", { className: "break-all text-xs text-white/70", children: [
      versions[0] ?? "?",
      " \u2192 ",
      versions[1] ?? "?"
    ] })
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
  const counts = connection.counts;
  const problem = connection.state !== "ok" && connection.state !== "not_configured";
  const canManage = deck().hasPermission("extensions.manage");
  const facts = [
    connection.version ? `Version ${connection.version}` : null,
    connection.location_name ? `Standort ${connection.location_name}` : null
  ].filter(Boolean);
  const hiddenUpdates = counts ? counts.updates - connection.updates.length : 0;
  const hiddenUnavailable = counts ? counts.unavailable - connection.unavailable.length : 0;
  const openHref = safeHref(connection.url);
  return /* @__PURE__ */ jsx4(
    Card,
    {
      title: connection.name,
      description: openHref ? /* @__PURE__ */ jsx4("a", { href: openHref, target: "_blank", rel: "noreferrer", className: "break-all hover:text-white hover:underline", children: "Home Assistant \xF6ffnen" }) : void 0,
      actions: /* @__PURE__ */ jsx4(Badge, { tone: head.tone, children: head.label }),
      children: /* @__PURE__ */ jsxs3("div", { "data-testid": `connection-${connection.name}`, className: "space-y-4", children: [
        facts.length > 0 && /* @__PURE__ */ jsx4("p", { "data-testid": "facts", className: "break-words text-sm text-white/70", children: facts.join(" \xB7 ") }),
        connection.state === "not_configured" && /* @__PURE__ */ jsxs3("div", { "data-testid": "not-configured", className: "flex flex-col items-start gap-3", children: [
          /* @__PURE__ */ jsx4("p", { className: "text-sm text-white/60", children: connection.message }),
          canManage ? /* @__PURE__ */ jsx4("a", { href: SETTINGS_PATH, className: buttonClass("primary", true), children: "Jetzt einrichten" }) : /* @__PURE__ */ jsx4("p", { className: "text-xs text-white/45", children: "Ein Administrator kann das unter Einstellungen \u2192 Erweiterungen \u2192 Home Assistant einrichten." })
        ] }),
        problem && /* @__PURE__ */ jsxs3("div", { "data-testid": "problem", role: "alert", className: "flex flex-col items-start gap-3", children: [
          /* @__PURE__ */ jsx4("p", { className: "break-words text-sm text-red-300", children: connection.message }),
          /* @__PURE__ */ jsxs3("div", { className: "flex flex-wrap items-center gap-2", children: [
            connection.state !== "auth_failed" && /* @__PURE__ */ jsx4(Button, { small: true, onClick: onRetry, children: "Erneut versuchen" }),
            canManage && /* @__PURE__ */ jsx4("a", { href: SETTINGS_PATH, className: buttonClass("ghost", true), children: "Einstellungen" })
          ] })
        ] }),
        counts && /* @__PURE__ */ jsxs3("div", { className: "grid grid-cols-2 gap-3 lg:grid-cols-4", children: [
          /* @__PURE__ */ jsx4(Stat, { label: "Entit\xE4ten", value: counts.total }),
          /* @__PURE__ */ jsx4(Stat, { label: "Updates", value: counts.updates, tone: counts.updates > 0 ? "warn" : void 0 }),
          /* @__PURE__ */ jsx4(Stat, { label: "Nicht verf\xFCgbar", value: counts.unavailable, tone: counts.unavailable > 0 ? "warn" : void 0 }),
          /* @__PURE__ */ jsx4(Stat, { label: "Unbekannt", value: counts.unknown })
        ] }),
        connection.state === "ok" && counts && /* @__PURE__ */ jsx4(Section, { title: "Updates", children: connection.updates.length === 0 ? /* @__PURE__ */ jsx4("p", { "data-testid": "no-updates", className: "text-sm text-white/50", children: "Alles aktuell, es steht kein Update bereit." }) : /* @__PURE__ */ jsxs3(Fragment2, { children: [
          /* @__PURE__ */ jsx4("ul", { "data-testid": "updates", className: "-mx-5 divide-y divide-white/[0.06] border-t border-white/[0.06]", children: connection.updates.map((u) => /* @__PURE__ */ jsx4(UpdateRow, { update: u }, u.entity_id)) }),
          hiddenUpdates > 0 && /* @__PURE__ */ jsxs3("p", { className: "mt-2 text-xs text-white/45", children: [
            "\u2026 und ",
            plural(hiddenUpdates, "weiteres", "weitere"),
            " (hier werden die ersten ",
            connection.updates.length,
            " gezeigt)."
          ] })
        ] }) }),
        connection.state === "ok" && connection.entities.length > 0 && /* @__PURE__ */ jsx4(Section, { title: "Ausgew\xE4hlte Werte", children: /* @__PURE__ */ jsx4("ul", { "data-testid": "entities", className: "-mx-5 divide-y divide-white/[0.06] border-t border-white/[0.06]", children: connection.entities.map((e) => /* @__PURE__ */ jsx4(EntityRow, { entity: e }, e.entity_id)) }) }),
        connection.state === "ok" && connection.entities.length === 0 && /* @__PURE__ */ jsx4("p", { "data-testid": "entities-hint", className: "text-xs text-white/45", children: "Soll hier der Wert einzelner Ger\xE4te stehen (z. B. eine Temperatur), trage ihre Kennungen unter Einstellungen \u2192 Erweiterungen \u2192 Home Assistant ein." }),
        connection.state === "ok" && counts && counts.unavailable > 0 && /* @__PURE__ */ jsx4(Section, { title: "Nicht verf\xFCgbar", children: /* @__PURE__ */ jsxs3("details", { "data-testid": "unavailable", open: connection.unavailable.length <= OPEN_LIST_UP_TO, children: [
          /* @__PURE__ */ jsxs3("summary", { className: "cursor-pointer text-sm text-white/70", children: [
            plural(counts.unavailable, "Entit\xE4t ist", "Entit\xE4ten sind"),
            " nicht verf\xFCgbar"
          ] }),
          /* @__PURE__ */ jsx4("ul", { className: "mt-2 divide-y divide-white/[0.06]", children: connection.unavailable.map((u) => /* @__PURE__ */ jsxs3("li", { "data-testid": "unavailable-item", className: "py-2 first:pt-0 last:pb-0", children: [
            /* @__PURE__ */ jsx4("p", { className: "break-words text-sm font-medium", children: u.name }),
            /* @__PURE__ */ jsx4("p", { className: "break-all text-xs text-white/50", children: u.entity_id })
          ] }, u.entity_id)) }),
          hiddenUnavailable > 0 && /* @__PURE__ */ jsxs3("p", { className: "mt-2 text-xs text-white/45", children: [
            "\u2026 und ",
            hiddenUnavailable,
            " weitere (hier werden die ersten ",
            connection.unavailable.length,
            " gezeigt)."
          ] })
        ] }) })
      ] })
    }
  );
}
function HomeAssistantPage() {
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
      title: "Home Assistant",
      description: "Zeigt Version, bereitstehende Updates, nicht verf\xFCgbare Ger\xE4te und ausgew\xE4hlte Werte deines Home Assistant. Nur lesend.",
      actions: /* @__PURE__ */ jsxs3(Fragment2, { children: [
        deck().hasPermission("extensions.manage") && /* @__PURE__ */ jsx4("a", { href: SETTINGS_PATH, className: buttonClass("ghost"), children: "Einstellungen" }),
        /* @__PURE__ */ jsx4(Button, { onClick: reload, disabled: loading, children: "Aktualisieren" })
      ] }),
      children: [
        error && /* @__PURE__ */ jsx4(LoadError, { what: "Der Zustand von Home Assistant", error, onRetry: reload }),
        !data && !error && /* @__PURE__ */ jsx4(Card, { children: /* @__PURE__ */ jsx4("p", { className: "text-sm text-white/50", children: "Lade \u2026" }) }),
        data && connections.length === 0 && /* @__PURE__ */ jsx4(
          EmptyState,
          {
            icon: "clock",
            title: "Noch keine Verbindung",
            text: deck().hasPermission("extensions.manage") ? "Trage Home Assistant unter Einstellungen \u2192 Erweiterungen \u2192 Home Assistant ein." : "Ein Administrator kann Home Assistant unter Einstellungen \u2192 Erweiterungen \u2192 Home Assistant eintragen.",
            action: /* @__PURE__ */ jsx4(SettingsLink, { to: SETTINGS_PATH, permission: "extensions.manage", children: "Jetzt einrichten" })
          }
        ),
        connections.length > 0 && /* @__PURE__ */ jsx4("div", { className: "space-y-6", children: connections.map((c, i) => /* @__PURE__ */ jsx4(ConnectionCard, { connection: c, onRetry: reload }, `${c.name}-${i}`)) })
      ]
    }
  );
}
export {
  HomeAssistantPage,
  valueText
};
