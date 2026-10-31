// src/VaultwardenPage.tsx
import { useCallback, useEffect as useEffect2, useState as useState2 } from "react";

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
function Notice({ text, kind = "error", onClose }) {
  const cls = kind === "ok" ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200" : "border-red-500/30 bg-red-500/10 text-red-200";
  return /* @__PURE__ */ jsxs("div", { role: kind === "ok" ? "status" : "alert", className: `mb-4 flex items-start justify-between gap-3 rounded-lg border px-3 py-2 text-sm ${cls}`, children: [
    /* @__PURE__ */ jsx("span", { children: text }),
    onClose && /* @__PURE__ */ jsx("button", { type: "button", "aria-label": "Hinweis schlie\xDFen", onClick: onClose, className: "opacity-60 hover:opacity-100", children: /* @__PURE__ */ jsx(Icon, { name: "x", size: 14 }) })
  ] });
}

// ../../_shared/frontend/src/links.tsx
import { useEffect, useState } from "react";
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

// src/VaultwardenPage.tsx
import { Fragment as Fragment2, jsx as jsx3, jsxs as jsxs2 } from "react/jsx-runtime";
var API = "/ext/vaultwarden";
var SETTINGS_PATH = "/settings/extensions/vaultwarden";
async function loadStatus() {
  const res = await authedFetch(`${API}/summary`);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorFromBody(body, res.status));
  return body;
}
function SettingsHint() {
  if (deck().hasPermission("extensions.manage")) {
    return /* @__PURE__ */ jsx3("a", { href: SETTINGS_PATH, className: buttonClass("primary", true), children: "Jetzt einrichten" });
  }
  return /* @__PURE__ */ jsx3("p", { className: "text-xs text-white/45", children: "Ein Administrator kann das unter Einstellungen \u2192 Erweiterungen \u2192 Vaultwarden einrichten." });
}
function Hints({ data }) {
  const hints = [];
  if (!data.https) {
    hints.push({
      key: "https",
      tone: "warn",
      label: "ohne HTTPS",
      text: "Der Web-Tresor im Browser funktioniert nur \xFCber HTTPS, die Bitwarden-Apps warnen ohne. Am einfachsten ein Proxy davor (Nginx Proxy Manager, Traefik) mit Zertifikat, dann hier die https-Adresse eintragen."
    });
  }
  if (data.signups_open) {
    hints.push({
      key: "signups",
      tone: "warn",
      label: "Registrierung offen",
      text: "Jeder, der die Adresse kennt, kann ein Konto anlegen. Wenn dein Konto steht, in Vaultwarden SIGNUPS_ALLOWED=false setzen (oder in der Verwaltung unter /admin)."
    });
  }
  if (data.domain_matches === false && data.domain) {
    hints.push({
      key: "domain",
      tone: "warn",
      label: "andere Adresse",
      text: `In Vaultwarden ist ${data.domain} als Adresse eingetragen (DOMAIN). Passt sie nicht zur Adresse, unter der du es \xF6ffnest, gehen Anh\xE4nge und Links in Mails nicht.`
    });
  }
  if (hints.length === 0) return null;
  return /* @__PURE__ */ jsx3(Card, { title: "Hinweise", children: /* @__PURE__ */ jsx3("ul", { "data-testid": "hints", className: "space-y-3", children: hints.map((h) => /* @__PURE__ */ jsxs2("li", { className: "flex flex-col gap-1 sm:flex-row sm:items-start sm:gap-3", children: [
    /* @__PURE__ */ jsx3(Badge, { tone: h.tone, children: h.label }),
    /* @__PURE__ */ jsx3("span", { className: "text-sm text-white/70", children: h.text })
  ] }, h.key)) }) });
}
function AppsCard({ url }) {
  return /* @__PURE__ */ jsx3(Card, { title: "Bitwarden-Apps verbinden", description: "Handy, Windows, Browser-Erweiterung: \xFCberall dieselben Schritte.", children: /* @__PURE__ */ jsxs2("ol", { "data-testid": "apps-howto", className: "list-decimal space-y-1 pl-5 text-sm text-white/70", children: [
    /* @__PURE__ */ jsxs2("li", { children: [
      "Bitwarden-App \xF6ffnen, vor dem Anmelden bei \u201EAnmelden bei\u201C ",
      /* @__PURE__ */ jsx3("strong", { children: "Selbst gehostet" }),
      " w\xE4hlen."
    ] }),
    /* @__PURE__ */ jsxs2("li", { children: [
      "Als Server-Adresse ",
      /* @__PURE__ */ jsx3("code", { className: "rounded bg-white/[0.06] px-1", children: url }),
      " eintragen."
    ] }),
    /* @__PURE__ */ jsx3("li", { children: "Mit E-Mail und Master-Passwort anmelden. Das Master-Passwort kennt nur die App, nie Nodvard Deck." })
  ] }) });
}
function VaultwardenPage() {
  const [data, setData] = useState2(null);
  const [loadError, setLoadError] = useState2(null);
  const load = useCallback(() => {
    setLoadError(null);
    loadStatus().then(setData).catch((err) => setLoadError(err instanceof Error ? err.message : String(err)));
  }, []);
  useEffect2(() => {
    load();
  }, [load]);
  let body = null;
  if (data?.state === "not_configured") {
    body = /* @__PURE__ */ jsx3(Card, { title: "Vaultwarden", children: /* @__PURE__ */ jsxs2("div", { "data-testid": "not-configured", className: "flex flex-col items-start gap-3", children: [
      /* @__PURE__ */ jsx3(Badge, { children: "nicht eingerichtet" }),
      /* @__PURE__ */ jsx3("p", { className: "text-sm text-white/60", children: data.message }),
      /* @__PURE__ */ jsx3(SettingsHint, {})
    ] }) });
  } else if (data && data.state !== "ok") {
    body = /* @__PURE__ */ jsx3(Card, { title: "Vaultwarden", actions: /* @__PURE__ */ jsx3(Badge, { tone: "bad", children: "nicht erreichbar" }), children: /* @__PURE__ */ jsxs2("div", { "data-testid": "problem", className: "flex flex-col items-start gap-3", children: [
      /* @__PURE__ */ jsx3("p", { className: "text-sm text-white/70", children: data.message }),
      /* @__PURE__ */ jsx3(Button, { small: true, onClick: load, children: "Erneut versuchen" })
    ] }) });
  } else if (data) {
    const signups = data.signups_open === true ? "offen" : data.signups_open === false ? "zu" : "unbekannt";
    body = /* @__PURE__ */ jsxs2("div", { className: "space-y-4", children: [
      /* @__PURE__ */ jsxs2("div", { "data-testid": "stats", className: "grid grid-cols-2 gap-3 lg:grid-cols-4", children: [
        /* @__PURE__ */ jsx3(Stat, { label: "Zustand", value: "l\xE4uft" }),
        /* @__PURE__ */ jsx3(Stat, { label: "Version", value: data.version ?? "unbekannt", hint: data.server_name ?? void 0 }),
        /* @__PURE__ */ jsx3(Stat, { label: "Verbindung", value: data.https ? "HTTPS" : "ohne HTTPS" }),
        /* @__PURE__ */ jsx3(Stat, { label: "Registrierung", value: signups })
      ] }),
      /* @__PURE__ */ jsx3(Hints, { data }),
      data.url && /* @__PURE__ */ jsx3(AppsCard, { url: data.url })
    ] });
  } else if (!loadError) {
    body = /* @__PURE__ */ jsx3(Card, { title: "Vaultwarden", children: /* @__PURE__ */ jsx3("p", { className: "text-sm text-white/50", children: "Lade \u2026" }) });
  }
  const webVault = safeHref(data?.url);
  return /* @__PURE__ */ jsxs2(
    Page,
    {
      title: "Vaultwarden",
      description: "Dein Passwort-Manager. Die Bitwarden-Apps sprechen direkt mit Vaultwarden.",
      actions: /* @__PURE__ */ jsxs2(Fragment2, { children: [
        webVault && /* @__PURE__ */ jsx3("a", { href: `${webVault}/`, target: "_blank", rel: "noreferrer", className: buttonClass("ghost"), children: "Web-Tresor \xF6ffnen" }),
        deck().hasPermission("extensions.manage") && /* @__PURE__ */ jsx3("a", { href: SETTINGS_PATH, className: buttonClass("ghost"), children: "Einstellungen" }),
        /* @__PURE__ */ jsx3(Button, { onClick: load, children: "Aktualisieren" })
      ] }),
      children: [
        loadError && /* @__PURE__ */ jsx3(Notice, { text: loadError, onClose: () => setLoadError(null) }),
        body
      ]
    }
  );
}
export {
  VaultwardenPage
};
