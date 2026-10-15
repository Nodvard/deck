// src/BackupsPage.tsx
import { Fragment as Fragment4, useCallback as useCallback2, useEffect as useEffect6, useRef as useRef2, useState as useState5 } from "react";

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

// ../../_shared/frontend/src/certificate.tsx
import { useEffect, useState } from "react";

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

// ../../_shared/frontend/src/certificate.tsx
import { jsx as jsx2, jsxs as jsxs2 } from "react/jsx-runtime";
function CertificateStatus({ connection }) {
  if (connection.tls_fingerprint_invalid) {
    return /* @__PURE__ */ jsx2("span", { className: "text-red-300", children: "Fingerabdruck ung\xFCltig" });
  }
  if (connection.tls_fingerprint_sha256) {
    return /* @__PURE__ */ jsx2("span", { className: "text-emerald-300", title: connection.tls_fingerprint_sha256, children: "Fingerabdruck gemerkt" });
  }
  if (connection.tls_insecure_skip_verify) {
    return /* @__PURE__ */ jsx2("span", { className: "text-amber-300", children: "wird nicht gepr\xFCft" });
  }
  return /* @__PURE__ */ jsx2("span", { className: "opacity-70", children: "normal gepr\xFCft" });
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
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  const path = `/ext/${extId}/connections/${encodeURIComponent(connection)}/certificate`;
  useEffect(() => {
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
  return /* @__PURE__ */ jsxs2("section", { className: "mb-3 space-y-2 rounded-lg border border-white/10 p-3 text-xs", "aria-label": `Zertifikat \u2013 ${connection}`, children: [
    /* @__PURE__ */ jsxs2("h4", { className: "font-medium", children: [
      "Zertifikat \u2013 ",
      connection
    ] }),
    !info && !error && /* @__PURE__ */ jsx2("p", { className: "opacity-60", children: "Lese das Zertifikat \u2026" }),
    error && /* @__PURE__ */ jsx2("p", { role: "alert", className: "text-red-300", children: error }),
    message && /* @__PURE__ */ jsx2("p", { role: "status", className: "text-emerald-300", children: message }),
    info && info.matches && /* @__PURE__ */ jsx2("p", { className: "text-emerald-300", "data-testid": "certificate-matches", children: "Der gemerkte Fingerabdruck passt zum Zertifikat des Servers." }),
    info && !info.matches && /* @__PURE__ */ jsxs2("div", { className: "space-y-2", "data-testid": "certificate-to-confirm", children: [
      changed && /* @__PURE__ */ jsx2("p", { className: "text-amber-300", "data-testid": "certificate-changed", children: "Der Server zeigt ein anderes Zertifikat als das gemerkte. Bis du den neuen Fingerabdruck best\xE4tigst, spricht Nodvard Deck nicht mit ihm." }),
      info.stored_invalid && /* @__PURE__ */ jsx2("p", { className: "text-red-300", children: "Der eingetragene Fingerabdruck ist ung\xFCltig \u2013 bis er stimmt, spricht Nodvard Deck nicht mit dem Server." }),
      !info.stored && !info.stored_invalid && /* @__PURE__ */ jsx2("p", { className: info.trusted ? "opacity-80" : "text-amber-300", children: info.trusted ? "Das Zertifikat ist von einer anerkannten Stelle best\xE4tigt, Merken ist nicht n\xF6tig. Erneuert es sich regelm\xE4\xDFig (z. B. Let's Encrypt), merk es dir besser nicht \u2013 sonst musst du nach jeder Erneuerung neu best\xE4tigen." : unchecked ? "Das Zertifikat wird bisher nicht gepr\xFCft. Merk dir den Fingerabdruck, dann spricht Nodvard Deck nur mit genau diesem Server." : "Das Zertifikat ist nicht von einer anerkannten Stelle best\xE4tigt, deshalb klappt die Verbindung noch nicht. Merk dir den Fingerabdruck, dann spricht Nodvard Deck nur mit genau diesem Server." }),
      /* @__PURE__ */ jsx2("p", { className: "opacity-80", children: hint }),
      /* @__PURE__ */ jsxs2("div", { children: [
        /* @__PURE__ */ jsxs2("p", { className: "opacity-60", children: [
          changed ? "Neuer Fingerabdruck" : "Fingerabdruck",
          " (SHA-256)"
        ] }),
        /* @__PURE__ */ jsx2("p", { className: "break-all rounded bg-black/30 px-2 py-1.5 font-mono", "aria-label": "Fingerabdruck des Servers", children: info.sha256 })
      ] }),
      changed && info.stored && /* @__PURE__ */ jsxs2("div", { children: [
        /* @__PURE__ */ jsx2("p", { className: "opacity-60", children: "Bisher gemerkt" }),
        /* @__PURE__ */ jsx2("p", { className: "break-all rounded bg-black/20 px-2 py-1.5 font-mono opacity-60", "aria-label": "Bisher gemerkt", children: info.stored })
      ] }),
      (info.subject || until) && /* @__PURE__ */ jsxs2("p", { className: "text-white/55", children: [
        info.subject ? `Ausgestellt f\xFCr ${info.subject}` : "",
        info.subject && until ? " \xB7 " : "",
        until ? `g\xFCltig bis ${until}` : ""
      ] }),
      /* @__PURE__ */ jsxs2("label", { className: "flex cursor-pointer items-start gap-2", children: [
        /* @__PURE__ */ jsx2(
          "input",
          {
            type: "checkbox",
            className: "mt-0.5 h-4 w-4 flex-none accent-[var(--color-accent)]",
            checked: confirmed,
            onChange: (e) => setConfirmed(e.target.checked)
          }
        ),
        /* @__PURE__ */ jsx2("span", { children: "Der Fingerabdruck stimmt mit dem in Proxmox \xFCberein." })
      ] }),
      /* @__PURE__ */ jsx2("p", { className: "text-white/45", children: "Stimmt er nicht, best\xE4tige nichts: Dann antwortet unter dieser Adresse vielleicht ein anderer Rechner." }),
      /* @__PURE__ */ jsx2(Button, { small: true, variant: "primary", disabled: !confirmed || busy, onClick: () => void save(), children: busy ? "Speichere \u2026" : changed ? "Neuen Fingerabdruck best\xE4tigen" : "Fingerabdruck merken" })
    ] })
  ] });
}

// ../../_shared/frontend/src/lifecycle.ts
import { useEffect as useEffect2, useRef } from "react";
function useUnmountSignal() {
  const ref = useRef(null);
  if (!ref.current) ref.current = new AbortController();
  useEffect2(() => {
    if (ref.current?.signal.aborted) ref.current = new AbortController();
    return () => ref.current?.abort();
  }, []);
  return useRef(() => ref.current.signal).current;
}

// ../../_shared/frontend/src/loadError.tsx
import { jsx as jsx3, jsxs as jsxs3 } from "react/jsx-runtime";
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
    return /* @__PURE__ */ jsxs3("div", { role: "alert", "data-testid": "load-error", className: "text-sm", children: [
      /* @__PURE__ */ jsxs3("p", { className: "text-red-300", children: [
        what,
        " lie\xDF sich gerade nicht laden: ",
        error
      ] }),
      hint && /* @__PURE__ */ jsx3("p", { className: "text-xs text-white/50", children: hint }),
      onRetry && /* @__PURE__ */ jsx3("button", { type: "button", onClick: onRetry, className: "mt-1 text-xs text-white/70 underline underline-offset-2 hover:text-white", children: "Erneut versuchen" })
    ] });
  }
  return /* @__PURE__ */ jsxs3("div", { role: "alert", "data-testid": "load-error", className: "mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm", children: [
    /* @__PURE__ */ jsxs3("div", { className: "min-w-0 flex-1", children: [
      /* @__PURE__ */ jsxs3("p", { className: "text-red-200", children: [
        what,
        " lie\xDF sich gerade nicht laden: ",
        error
      ] }),
      hint && /* @__PURE__ */ jsx3("p", { className: "text-xs text-white/55", children: hint })
    ] }),
    onRetry && /* @__PURE__ */ jsx3(Button, { small: true, onClick: onRetry, children: "Erneut versuchen" })
  ] });
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

// src/FailureCause.tsx
import { useEffect as useEffect4, useState as useState3 } from "react";
import { Fragment as Fragment2, jsx as jsx4, jsxs as jsxs4 } from "react/jsx-runtime";
function FailureCause({ jobRef, upid = null }) {
  const [cause, setCause] = useState3(null);
  const [error, setError] = useState3(null);
  useEffect4(() => {
    let active = true;
    setCause(null);
    setError(null);
    const query = upid ? `?upid=${encodeURIComponent(upid)}` : "";
    authedFetch(`/ext/backups/jobs/${encodeURIComponent(jobRef)}/failure${query}`).then(async (res) => {
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      if (active) setCause(body);
    }).catch((err) => {
      if (active) setError(err instanceof Error ? err.message : String(err));
    });
    return () => {
      active = false;
    };
  }, [jobRef, upid]);
  return /* @__PURE__ */ jsxs4("div", { className: "mb-2 space-y-1 rounded border border-red-500/30 bg-red-500/5 p-2 text-xs", "data-testid": "failure-cause", children: [
    !cause && !error && /* @__PURE__ */ jsx4("p", { className: "opacity-60", children: "Lese, warum der Lauf fehlgeschlagen ist \u2026" }),
    error && /* @__PURE__ */ jsxs4("p", { role: "alert", className: "text-red-300", children: [
      "Grund nicht lesbar: ",
      error
    ] }),
    cause && /* @__PURE__ */ jsxs4(Fragment2, { children: [
      /* @__PURE__ */ jsxs4("p", { children: [
        /* @__PURE__ */ jsx4("span", { className: "font-medium", children: "Was ist passiert?" }),
        " ",
        cause.what
      ] }),
      /* @__PURE__ */ jsxs4("p", { children: [
        /* @__PURE__ */ jsx4("span", { className: "font-medium", children: "Was kannst du tun?" }),
        " ",
        cause.todo
      ] }),
      cause.detail && /* @__PURE__ */ jsxs4("p", { className: "break-all font-mono opacity-70", "data-testid": "failure-detail", children: [
        "Proxmox meldet: ",
        cause.detail
      ] }),
      !cause.log_readable && /* @__PURE__ */ jsx4("p", { className: "opacity-60", children: "Das Protokoll des Laufs lie\xDF sich nicht lesen (meist fehlt dem Zugang das Recht \u201ESys.Audit\u201C, oder Proxmox hat das alte Protokoll schon gel\xF6scht). Die Erkl\xE4rung st\xFCtzt sich nur auf das Ergebnis des Laufs." })
    ] })
  ] });
}

// src/RestoreCopy.tsx
import { useEffect as useEffect5, useState as useState4 } from "react";

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

// src/RestoreCopy.tsx
import { Fragment as Fragment3, jsx as jsx5, jsxs as jsxs5 } from "react/jsx-runtime";
function when(seconds) {
  return seconds ? new Date(seconds * 1e3).toLocaleString() : "unbekannt";
}
async function getJson(path) {
  const res = await authedFetch(path);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorFromBody(body, res.status));
  return body;
}
function RestoreRules({ kind, linkDown, pool, keepHint }) {
  const container = kind === "lxc";
  return /* @__PURE__ */ jsxs5("ul", { className: "list-disc space-y-1 pl-4", "data-testid": "restore-rules", children: [
    /* @__PURE__ */ jsx5("li", { children: "Das Original bleibt unber\xFChrt. Die Kopie bekommt eine neue Nummer und neue MAC-Adressen." }),
    /* @__PURE__ */ jsxs5("li", { children: [
      "Die Kopie entsteht im Pool ",
      pool,
      ": nur dort darf Nodvard Deck G\xE4ste anlegen. ",
      keepHint
    ] }),
    /* @__PURE__ */ jsx5("li", { children: "Sie startet nicht von selbst \u2013 auch nicht, wenn der Server neu startet: Nodvard Deck schaltet den Autostart schon beim Anlegen ab und pr\xFCft ihn, sobald Proxmox fertig ist." }),
    /* @__PURE__ */ jsxs5("li", { children: [
      "Im Gast ist alles wie beim Original, auch eine feste IP-Adresse. Laufen beide gleichzeitig im selben Netz, st\xF6ren sie sich.",
      container ? " Den neuen Namen setzt Proxmox beim Container auch als Rechnernamen." : " Bei einer VM \xE4ndert sich nur der Name in Proxmox, nicht der Rechnername im Gast."
    ] }),
    linkDown ? /* @__PURE__ */ jsxs5("li", { children: [
      "Deshalb trennt Nodvard Deck die Netzwerkkarten der Kopie, sobald Proxmox mit der Wiederherstellung fertig ist. Endet die Aktion vorher (Neustart des Dashboards, mehr als 45 Minuten), holt der Backup-W\xE4chter das nach \u2013 er sieht alle 15 Minuten nach und meldet sich. Bis dahin die Kopie nicht starten. Verbinden: in Proxmox bei der Kopie unter \u201E",
      container ? "Netzwerk" : "Hardware",
      "\u201C die Netzwerkkarte bearbeiten und \u201EGetrennt\u201C abhaken \u2013 erst wenn die IP-Adresse ge\xE4ndert ist oder das Original aus ist."
    ] }) : /* @__PURE__ */ jsx5("li", { className: "text-amber-300", children: "Die Netzwerkkarten bleiben verbunden: \xC4ndere vor dem ersten Start die IP-Adresse in der Kopie oder schalte das Original aus." })
  ] });
}
function RestoreCopyPanel({ target, onDone }) {
  const unmountSignal = useUnmountSignal();
  const [list, setList] = useState4(null);
  const [volid, setVolid] = useState4("");
  const [options, setOptions] = useState4(null);
  const [node, setNode] = useState4("");
  const [storage, setStorage] = useState4("");
  const [vmid, setVmid] = useState4("");
  const [name, setName] = useState4("");
  const [linkDown, setLinkDown] = useState4(true);
  const [confirming, setConfirming] = useState4(false);
  const [error, setError] = useState4(null);
  const [busy, setBusy] = useState4(false);
  useEffect5(() => {
    const query = new URLSearchParams({ connection: target.connection, vmid: target.vmid });
    if (target.kind) query.set("kind", target.kind);
    getJson(`/ext/backups/restore/backups?${query.toString()}`).then((body) => {
      setList(body);
      setVolid(body.backups[0]?.volid ?? "");
    }).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [target.connection, target.vmid, target.kind]);
  useEffect5(() => {
    if (!volid) return;
    setOptions(null);
    setConfirming(false);
    setError(null);
    const query = new URLSearchParams({ connection: target.connection, volid });
    getJson(`/ext/backups/restore/options?${query.toString()}`).then((body) => {
      setOptions(body);
      setNode(body.default_node);
      setStorage(body.default_storage ?? "");
      setVmid(body.suggested_vmid ? String(body.suggested_vmid) : "");
      setName(body.suggested_name);
    }).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [target.connection, volid]);
  const storages = options?.nodes.find((n) => n.node === node)?.storages ?? [];
  const chosen = storages.find((s) => s.storage === storage);
  const canApprove = deck().hasPermission("actions.approve:high");
  const ready = !!options && !options.refuse && !options.blocked && !!chosen?.fits && /^\d+$/.test(vmid) && name.trim() !== "" && !busy;
  function chooseNode(next) {
    setNode(next);
    setStorage(options?.nodes.find((n) => n.node === next)?.storages.find((s) => s.fits)?.storage ?? "");
  }
  async function submit() {
    if (!options || options.blocked) return;
    setBusy(true);
    setError(null);
    try {
      const res = await authedFetch("/ext/backups/restore", {
        method: "POST",
        body: JSON.stringify({ connection: target.connection, volid, node, storage, vmid: Number(vmid), name: name.trim(), link_down: linkDown })
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(errorFromBody(body, res.status));
        setConfirming(false);
        return;
      }
      const { action, approved } = await settleAction(body, { signal: unmountSignal() });
      if (approved) {
        onDone(
          action.status === "succeeded" ? action.result?.output ?? "Kopie angelegt." : isActionRunning(action.status) ? RUNNING_IN_BACKGROUND : `Fehlgeschlagen: ${action.result?.error ?? action.status}`
        );
      } else if (action.status === "proposed") {
        onDone("Wiederherstellung vorgeschlagen \u2013 Freigabe durch einen Admin n\xF6tig, siehe \u201EAktionen\u201C.");
      } else {
        onDone(ACTION_STATUS_LABEL[action.status ?? ""] ?? action.status ?? "?");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  const title = `\u201E${target.name}\u201C als Kopie wiederherstellen`;
  const showForm = !!options && !options.refuse && !confirming;
  return /* @__PURE__ */ jsxs5("div", { className: "space-y-3 p-3 text-sm panel", role: "group", "aria-label": title, "data-testid": `restore-${target.connection}-${target.vmid}`, children: [
    /* @__PURE__ */ jsx5("p", { className: "font-medium", children: title }),
    error && /* @__PURE__ */ jsx5(Notice, { text: error }),
    !list && !error && /* @__PURE__ */ jsx5("p", { className: "opacity-60", children: "Lade Sicherungen \u2026" }),
    list && list.backups.length === 0 && /* @__PURE__ */ jsxs5("p", { className: "opacity-70", children: [
      "Keine Sicherung gefunden.",
      list.errors.length > 0 ? ` (${list.errors.join("; ")})` : ""
    ] }),
    list && list.backups.length > 0 && !confirming && /* @__PURE__ */ jsx5(Field, { label: "Sicherung", children: /* @__PURE__ */ jsx5("select", { value: volid, onChange: (e) => setVolid(e.target.value), className: inputClass, children: list.backups.map((b) => /* @__PURE__ */ jsxs5("option", { value: b.volid, children: [
      when(b.ctime),
      " \xB7 ",
      b.kind_label,
      " \xB7 ",
      formatBytes(b.size),
      " \xB7 ",
      b.storage,
      b.notes ? ` \xB7 ${b.notes}` : ""
    ] }, b.volid)) }) }),
    volid && !options && !error && /* @__PURE__ */ jsx5("p", { className: "opacity-60", children: "Pr\xFCfe Speicher und Nummer \u2026" }),
    options?.refuse && /* @__PURE__ */ jsx5(Notice, { text: options.refuse }),
    options?.blocked && !options.refuse && /* @__PURE__ */ jsx5("div", { "data-testid": "restore-blocked", children: /* @__PURE__ */ jsx5(Notice, { text: options.blocked }) }),
    showForm && /* @__PURE__ */ jsxs5(Fragment3, { children: [
      /* @__PURE__ */ jsxs5("p", { className: "opacity-70", children: [
        "Sicherung von ",
        options.kind_label,
        " ",
        options.source_vmid,
        options.source_name ? ` (${options.source_name})` : "",
        " vom ",
        when(options.backup_at),
        ", ",
        formatBytes(options.backup_size),
        options.source_exists ? "" : " \u2013 das Original gibt es in Proxmox nicht mehr",
        "."
      ] }),
      /* @__PURE__ */ jsxs5("div", { className: "grid gap-3 sm:grid-cols-2", children: [
        /* @__PURE__ */ jsx5(Field, { label: "Zielknoten", children: /* @__PURE__ */ jsx5("select", { value: node, onChange: (e) => chooseNode(e.target.value), className: inputClass, children: options.nodes.map((n) => /* @__PURE__ */ jsx5("option", { value: n.node, children: n.node }, n.node)) }) }),
        /* @__PURE__ */ jsx5(Field, { label: "Zielspeicher", children: /* @__PURE__ */ jsxs5("select", { value: storage, onChange: (e) => setStorage(e.target.value), className: inputClass, children: [
          !chosen && /* @__PURE__ */ jsx5("option", { value: "", children: "\u2013 bitte w\xE4hlen \u2013" }),
          storages.map((s) => /* @__PURE__ */ jsxs5("option", { value: s.storage, disabled: !s.fits, children: [
            s.storage,
            " \u2013 ",
            s.avail === null ? "freier Platz unbekannt" : `${formatBytes(s.avail)} frei`,
            s.fits ? "" : " (zu wenig Platz)"
          ] }, s.storage))
        ] }) }),
        /* @__PURE__ */ jsx5(Field, { label: "Neue Nummer (VMID)", children: /* @__PURE__ */ jsx5("input", { value: vmid, inputMode: "numeric", onChange: (e) => setVmid(e.target.value.trim()), className: inputClass }) }),
        /* @__PURE__ */ jsx5(Field, { label: "Name der Kopie", children: /* @__PURE__ */ jsx5("input", { value: name, onChange: (e) => setName(e.target.value), className: inputClass }) })
      ] }),
      storages.length > 0 && !storages.some((s) => s.fits) && /* @__PURE__ */ jsx5("p", { className: "text-amber-300", children: "Auf diesem Knoten passt die Kopie auf keinen Speicher." }),
      chosen && /* @__PURE__ */ jsxs5("p", { "data-testid": "restore-space", className: "flex flex-wrap items-center gap-2", children: [
        /* @__PURE__ */ jsx5(Badge, { tone: chosen.fits ? chosen.warning ? "warn" : "good" : "bad", children: chosen.fits ? "Platz reicht" : "zu wenig Platz" }),
        /* @__PURE__ */ jsxs5("span", { className: "opacity-80", children: [
          "Braucht mindestens ",
          formatBytes(chosen.need_bytes),
          " (Sicherung plus Reserve)",
          options.disk_bytes ? `, die Platten sind zusammen bis zu ${formatBytes(options.disk_bytes)} gro\xDF` : "",
          "; frei auf ",
          chosen.storage,
          ":",
          " ",
          formatBytes(chosen.avail),
          "."
        ] })
      ] }),
      chosen?.warning && /* @__PURE__ */ jsx5("p", { className: "text-amber-300", children: chosen.warning }),
      /* @__PURE__ */ jsxs5("label", { className: "flex items-center gap-2", children: [
        /* @__PURE__ */ jsx5("input", { type: "checkbox", checked: linkDown, onChange: (e) => setLinkDown(e.target.checked) }),
        "Netzwerkkarten der Kopie getrennt anlegen (empfohlen)"
      ] }),
      /* @__PURE__ */ jsxs5("div", { className: "rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs", "data-testid": "restore-notes", children: [
        /* @__PURE__ */ jsx5(RestoreRules, { kind: options.kind, linkDown, pool: options.pool, keepHint: options.keep_hint }),
        options.warnings.length > 0 && /* @__PURE__ */ jsx5("ul", { className: "mt-2 list-disc space-y-1 pl-4 text-amber-300", children: options.warnings.map((w) => /* @__PURE__ */ jsx5("li", { children: w }, w)) })
      ] })
    ] }),
    confirming && options && /* @__PURE__ */ jsxs5("div", { className: "space-y-2 rounded-lg border border-white/10 bg-white/[0.04] p-3", "data-testid": "restore-summary", children: [
      /* @__PURE__ */ jsx5("p", { className: "font-medium", children: "Das legt Nodvard Deck an:" }),
      /* @__PURE__ */ jsxs5("ul", { className: "list-disc space-y-0.5 pl-4", children: [
        /* @__PURE__ */ jsxs5("li", { children: [
          "Neue Nummer: ",
          vmid
        ] }),
        /* @__PURE__ */ jsxs5("li", { children: [
          "Name: ",
          name.trim()
        ] }),
        /* @__PURE__ */ jsxs5("li", { children: [
          "Aus der Sicherung von ",
          options.kind_label,
          " ",
          options.source_vmid,
          options.source_name ? ` (${options.source_name})` : "",
          " vom ",
          when(options.backup_at)
        ] }),
        /* @__PURE__ */ jsxs5("li", { children: [
          "Knoten: ",
          node
        ] }),
        /* @__PURE__ */ jsxs5("li", { children: [
          "Zielspeicher: ",
          storage
        ] }),
        /* @__PURE__ */ jsxs5("li", { children: [
          "Pool ",
          options.pool
        ] }),
        /* @__PURE__ */ jsx5("li", { className: linkDown ? void 0 : "text-amber-300", children: linkDown ? "Netzwerk getrennt" : "Netzwerk verbunden" }),
        /* @__PURE__ */ jsx5("li", { children: "Die Kopie startet nicht von selbst." })
      ] }),
      /* @__PURE__ */ jsxs5("p", { className: "text-xs opacity-80", children: [
        "Das Original bleibt unber\xFChrt. ",
        options.keep_hint
      ] }),
      /* @__PURE__ */ jsx5("p", { className: "text-xs opacity-80", children: canApprove ? "Mit \u201EKopie anlegen\u201C gibst du die Wiederherstellung gleich selbst frei." : "Mit \u201EKopie anlegen\u201C schl\xE4gst du die Wiederherstellung nur vor: Ein Admin muss sie unter \u201EAktionen\u201C freigeben, erst dann legt Nodvard Deck die Kopie an." })
    ] }),
    /* @__PURE__ */ jsxs5("div", { className: "flex flex-wrap gap-2", children: [
      options && !options.refuse && !confirming && /* @__PURE__ */ jsx5(Button, { variant: "secondary", disabled: !ready, onClick: () => setConfirming(true), children: "Weiter" }),
      options && !options.refuse && confirming && /* @__PURE__ */ jsxs5(Fragment3, { children: [
        /* @__PURE__ */ jsx5(Button, { variant: canApprove ? "danger" : "secondary", disabled: !ready, onClick: () => void submit(), children: busy ? "L\xE4uft \u2026" : "Kopie anlegen" }),
        /* @__PURE__ */ jsx5(Button, { variant: "ghost", disabled: busy, onClick: () => setConfirming(false), children: "Zur\xFCck" })
      ] }),
      /* @__PURE__ */ jsx5(Button, { variant: "ghost", onClick: () => onDone(null), children: "Abbrechen" })
    ] }),
    busy && canApprove && /* @__PURE__ */ jsx5("p", { className: "text-xs opacity-60", children: "Das kann bei gro\xDFen G\xE4sten lange dauern. Du kannst die Seite verlassen \u2013 das Ergebnis steht dann unter \u201EAktionen\u201C." })
  ] });
}

// src/BackupsPage.tsx
import { Fragment as Fragment5, jsx as jsx6, jsxs as jsxs6 } from "react/jsx-runtime";
function formatSize(bytes) {
  if (bytes >= 1e12) return `${(bytes / 1e12).toFixed(1)} TB`;
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  return `${Math.round(bytes / 1e6)} MB`;
}
function useInventory() {
  const [inventory, setInventory] = useState5(null);
  const [orphans, setOrphans] = useState5([]);
  const [error, setError] = useState5(null);
  const reload = useCallback2(() => {
    setError(null);
    fetchJson("/ext/backups/inventory").then((body) => {
      setInventory(new Map((body.guests ?? []).map((g) => [`${g.connection}/${g.vmid}`, g])));
      setOrphans(body.orphans ?? []);
    }).catch((err) => setError(errorMessage(err)));
  }, []);
  useEffect6(() => {
    reload();
  }, [reload]);
  return { inventory, orphans, error, reload };
}
function inventoryText(entry) {
  if (!entry || entry.count === 0) return "keine Sicherung vorhanden";
  const newest = formatEpoch(entry.newest_at);
  return `${entry.count}\xD7 \xB7 neueste ${newest} \xB7 ${formatSize(entry.total_size)} (${entry.storages.join(", ")})`;
}
function NewJobForm({ guest, defaultStorage, onDone }) {
  const unmountSignal = useUnmountSignal();
  const [schedule, setSchedule] = useState5("sun 02:00");
  const [storage, setStorage] = useState5(defaultStorage);
  const [keepLast, setKeepLast] = useState5(3);
  const [busy, setBusy] = useState5(false);
  async function create() {
    setBusy(true);
    try {
      const res = await postWithSpaceCheck(`/ext/backups/unprotected/${encodeURIComponent(guest.connection)}/${guest.vmid}/job`, {
        values: { schedule, storage, "keep-last": keepLast }
      });
      if (res === null) return;
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      const { action, approved } = await settleAction(body, { signal: unmountSignal() });
      if (approved) {
        onDone(
          action.status === "succeeded" ? action.result?.output ?? "Angelegt." : isActionRunning(action.status) ? RUNNING_IN_BACKGROUND : `Fehlgeschlagen: ${action.result?.error ?? action.status}`
        );
      } else {
        onDone(action.status === "proposed" ? `Vorgeschlagen \u2013 Freigabe durch einen Admin n\xF6tig, siehe "Aktionen".` : ACTION_STATUS_LABEL[action.status ?? ""] ?? action.status ?? "?");
      }
    } catch (err) {
      onDone(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  }
  return /* @__PURE__ */ jsxs6("div", { className: "my-1 flex flex-wrap items-end gap-2 p-2 text-xs panel", "data-testid": `new-job-${guest.connection}-${guest.vmid}`, children: [
    /* @__PURE__ */ jsxs6("label", { className: "flex flex-col gap-0.5", children: [
      /* @__PURE__ */ jsx6("span", { className: "opacity-60", children: "Zeitplan" }),
      /* @__PURE__ */ jsx6("input", { value: schedule, onChange: (e) => setSchedule(e.target.value), className: "w-32 px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]" })
    ] }),
    /* @__PURE__ */ jsxs6("label", { className: "flex flex-col gap-0.5", children: [
      /* @__PURE__ */ jsx6("span", { className: "opacity-60", children: "Speicher" }),
      /* @__PURE__ */ jsx6("input", { value: storage, onChange: (e) => setStorage(e.target.value), className: "w-32 px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]" })
    ] }),
    /* @__PURE__ */ jsxs6("label", { className: "flex flex-col gap-0.5", children: [
      /* @__PURE__ */ jsx6("span", { className: "opacity-60", children: "Letzte behalten" }),
      /* @__PURE__ */ jsx6("input", { type: "number", min: 1, value: keepLast, onChange: (e) => setKeepLast(Number(e.target.value)), className: "w-16 px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]" })
    ] }),
    /* @__PURE__ */ jsx6("button", { type: "button", disabled: busy || !schedule || !storage, onClick: () => void create(), className: "rounded bg-emerald-500/20 px-3 py-1 text-emerald-300 hover:bg-emerald-500/30 disabled:opacity-40", children: busy ? "\u2026" : "Anlegen" }),
    /* @__PURE__ */ jsx6("button", { type: "button", onClick: () => onDone(null), className: "rounded px-2 py-1 opacity-70 hover:opacity-100", children: "Abbrechen" })
  ] });
}
function canRestore(entry) {
  return !!entry && entry.count > 0 && deck().hasPermission("hosts.execute");
}
function RestoreButton({ open, onClick }) {
  return /* @__PURE__ */ jsx6("button", { type: "button", onClick, className: "ml-2 px-1.5 py-0.5 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition", children: open ? "Wiederherstellen schlie\xDFen" : "Als Kopie wiederherstellen" });
}
function UnprotectedSection({ inventory, defaultStorage = "", onChanged }) {
  const [creating, setCreating] = useState5(null);
  const [restoring, setRestoring] = useState5(null);
  const [data, setData] = useState5(null);
  const [message, setMessage] = useState5(null);
  const [loadError, setLoadError] = useState5(null);
  const load = useCallback2(() => {
    setLoadError(null);
    fetchJson("/ext/backups/unprotected").then((body) => setData({ guests: body.guests ?? [], errors: body.errors ?? [] })).catch((err) => setLoadError(errorMessage(err)));
  }, []);
  useEffect6(() => {
    load();
  }, [load]);
  const canDecide = deck().hasPermission("settings.write");
  async function acknowledge(g) {
    const note = await deck().promptDialog(`${g.name} bewusst ohne Backup lassen? Kurze Begr\xFCndung (optional):`);
    if (note === null) return;
    await setAcknowledged(g, { method: "PUT", body: JSON.stringify({ note }) });
  }
  async function setAcknowledged(g, init) {
    setMessage(null);
    const res = await authedFetch(`/ext/backups/unprotected/${encodeURIComponent(g.connection)}/${encodeURIComponent(g.vmid)}/acknowledged`, init);
    if (!res.ok) {
      setMessage(`Fehler: ${await errorText(res)}`);
      return;
    }
    load();
  }
  if (loadError && !data) {
    return /* @__PURE__ */ jsx6("section", { className: "mb-4 rounded border border-white/10 p-3", "data-testid": "unprotected", children: /* @__PURE__ */ jsx6(LoadError, { compact: true, what: "Die Liste der G\xE4ste ohne Backup-Job", error: loadError, onRetry: load }) });
  }
  if (!data || data.guests.length === 0 && data.errors.length === 0) return null;
  const open = data.guests.filter((g) => !g.acknowledged);
  const accepted = data.guests.filter((g) => g.acknowledged);
  const line = (g) => /* @__PURE__ */ jsxs6(Fragment5, { children: [
    g.name,
    " ",
    /* @__PURE__ */ jsxs6("span", { className: "opacity-60", children: [
      "\xB7 ",
      g.kind,
      " ",
      g.vmid,
      " \xB7 ",
      g.connection
    ] }),
    inventory && /* @__PURE__ */ jsxs6("span", { className: "opacity-60", children: [
      " \xB7 ",
      inventoryText(inventory.get(`${g.connection}/${g.vmid}`))
    ] })
  ] });
  return /* @__PURE__ */ jsxs6(
    "section",
    {
      className: `mb-4 rounded border p-3 ${open.length > 0 ? "border-amber-500/40 bg-amber-500/5" : "border-white/10"}`,
      "data-testid": "unprotected",
      children: [
        /* @__PURE__ */ jsxs6("h3", { className: `text-sm font-semibold ${open.length > 0 ? "text-amber-300" : "opacity-80"}`, children: [
          "Ohne Backup-Job (",
          open.length,
          ")"
        ] }),
        /* @__PURE__ */ jsx6("p", { className: "mb-2 text-xs opacity-70", children: "Diese G\xE4ste sichert kein Backup-Job in Proxmox. Geht die Disk verloren, sind sie weg." }),
        /* @__PURE__ */ jsx6("ul", { className: "space-y-0.5 text-sm", children: open.map((g) => /* @__PURE__ */ jsxs6("li", { children: [
          line(g),
          canDecide && /* @__PURE__ */ jsx6(
            "button",
            {
              type: "button",
              onClick: () => setCreating(creating === `${g.connection}-${g.vmid}` ? null : `${g.connection}-${g.vmid}`),
              className: "ml-2 rounded bg-emerald-500/20 px-1.5 py-0.5 text-xs text-emerald-300 hover:bg-emerald-500/30",
              children: "Job anlegen"
            }
          ),
          canDecide && /* @__PURE__ */ jsx6("button", { type: "button", onClick: () => void acknowledge(g), className: "ml-2 px-1.5 py-0.5 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition", children: "Bewusst so lassen" }),
          inventory && canRestore(inventory.get(`${g.connection}/${g.vmid}`)) && /* @__PURE__ */ jsx6(
            RestoreButton,
            {
              open: restoring === `${g.connection}-${g.vmid}`,
              onClick: () => setRestoring(restoring === `${g.connection}-${g.vmid}` ? null : `${g.connection}-${g.vmid}`)
            }
          ),
          restoring === `${g.connection}-${g.vmid}` && /* @__PURE__ */ jsx6("div", { className: "my-1", children: /* @__PURE__ */ jsx6(RestoreCopyPanel, { target: { connection: g.connection, vmid: g.vmid, name: g.name }, onDone: (text) => {
            setRestoring(null);
            if (text) setMessage(text);
          } }) }),
          creating === `${g.connection}-${g.vmid}` && /* @__PURE__ */ jsx6(NewJobForm, { guest: g, defaultStorage, onDone: (text) => {
            setCreating(null);
            if (text) {
              setMessage(text);
              load();
              onChanged?.();
            }
          } })
        ] }, `${g.connection}-${g.vmid}`)) }),
        accepted.length > 0 && /* @__PURE__ */ jsxs6("div", { className: "mt-2", "data-testid": "unprotected-accepted", children: [
          /* @__PURE__ */ jsxs6("p", { className: "text-xs font-medium opacity-60", children: [
            "Bewusst ohne Backup (",
            accepted.length,
            ") \u2013 warnt nicht auf dem Dashboard"
          ] }),
          /* @__PURE__ */ jsx6("ul", { className: "space-y-0.5 text-xs opacity-70", children: accepted.map((g) => /* @__PURE__ */ jsxs6("li", { children: [
            line(g),
            g.note ? /* @__PURE__ */ jsxs6("span", { className: "italic", children: [
              " \xB7 \u201E",
              g.note,
              "\u201C"
            ] }) : null,
            canDecide && /* @__PURE__ */ jsx6(
              "button",
              {
                type: "button",
                onClick: () => void setAcknowledged(g, { method: "DELETE" }),
                className: "ml-2 px-1.5 py-0.5 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
                children: "Wieder warnen"
              }
            )
          ] }, `${g.connection}-${g.vmid}`)) })
        ] }),
        message && /* @__PURE__ */ jsx6("p", { className: "mt-1 text-xs text-red-400", children: message }),
        data.errors.map((e) => /* @__PURE__ */ jsxs6("p", { className: "mt-1 text-xs text-red-400", children: [
          e.connection,
          ": nicht pr\xFCfbar (",
          e.error,
          ")"
        ] }, e.connection))
      ]
    }
  );
}
function historyLabel(h) {
  if (h.status === "ok" && h.warnings) return "OK, mit Warnungen";
  return STATUS_LABEL[h.status] ?? h.status;
}
var STATUS_LABEL = {
  ok: "OK",
  failed: "Fehlgeschlagen",
  running: "L\xE4uft",
  unknown: "Unbekannt"
};
var STATUS_CLASS = {
  ok: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  failed: "bg-red-500/15 text-red-300 border-red-500/40",
  running: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  unknown: "bg-white/10 opacity-70"
};
function formatEpoch(seconds) {
  if (!seconds) return "nie";
  return new Date(seconds * 1e3).toLocaleString();
}
async function postWithSpaceCheck(path, payload) {
  const send = (extra) => authedFetch(path, { method: "POST", body: JSON.stringify({ ...payload, ...extra }) });
  const res = await send({});
  if (res.status !== 409) return res;
  const body = await res.clone().json().catch(() => ({}));
  if (!body.space_warning) return res;
  const ok = await deck().confirmDialog(`Speicher reicht vermutlich nicht: ${body.detail} Trotzdem fortfahren?`, {
    danger: true,
    confirmLabel: "Trotzdem"
  });
  return ok ? send({ ignore_space: true }) : null;
}
function ConnectionsPanel() {
  const [connections, setConnections] = useState5(null);
  const [error, setError] = useState5(null);
  const [message, setMessage] = useState5(null);
  const [showAddForm, setShowAddForm] = useState5(false);
  const [newName, setNewName] = useState5("");
  const [newBaseUrl, setNewBaseUrl] = useState5("");
  const [newTokenId, setNewTokenId] = useState5("");
  const [newTlsInsecure, setNewTlsInsecure] = useState5(false);
  const [pending, setPending] = useState5(null);
  const [certificateFor, setCertificateFor] = useState5(null);
  const load = useCallback2(() => {
    setError(null);
    authedFetch("/ext/backups/connections").then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    }).then(setConnections).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);
  useEffect6(() => {
    load();
  }, [load]);
  async function addConnection(e) {
    e.preventDefault();
    setPending("add");
    setMessage(null);
    try {
      const res = await authedFetch("/ext/backups/connections", {
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
      const res = await authedFetchConfirmed(`/extensions/backups/secrets`, (confirmation) => ({
        method: "PUT",
        body: JSON.stringify({ label: `backups-token:${name}`, value, ...confirmation })
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
      const res = await authedFetch(`/ext/backups/connections/${conn.name}`, {
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
      const res = await authedFetch(`/ext/backups/connections/${name}`, { method: "DELETE" });
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
  if (error) return /* @__PURE__ */ jsxs6("p", { className: "mb-4 text-xs text-red-400", children: [
    "Verbindungen nicht ladbar: ",
    error
  ] });
  return /* @__PURE__ */ jsxs6("details", { className: "mb-6 p-3 panel", open: connections?.length === 0, children: [
    /* @__PURE__ */ jsx6("summary", { className: "cursor-pointer text-sm font-medium", children: "Verbindungen verwalten" }),
    /* @__PURE__ */ jsxs6("div", { className: "mt-3", children: [
      message && /* @__PURE__ */ jsx6("p", { className: "mb-2 text-xs opacity-80", children: message }),
      connections && connections.length === 0 && /* @__PURE__ */ jsx6("p", { className: "mb-2 text-xs opacity-60", children: "Noch keine Verbindung konfiguriert." }),
      connections && connections.length > 0 && /* @__PURE__ */ jsxs6("table", { className: "mb-3 w-full text-xs", children: [
        /* @__PURE__ */ jsx6("thead", { children: /* @__PURE__ */ jsxs6("tr", { className: "border-b border-white/10 text-left opacity-60", children: [
          /* @__PURE__ */ jsx6("th", { className: "py-1", children: "Name" }),
          /* @__PURE__ */ jsx6("th", { className: "py-1", children: "Adresse" }),
          /* @__PURE__ */ jsx6("th", { className: "py-1", children: "Zertifikat" }),
          /* @__PURE__ */ jsx6("th", { className: "py-1", children: "Token" }),
          /* @__PURE__ */ jsx6("th", { className: "py-1", children: "Aktiv" }),
          /* @__PURE__ */ jsx6("th", { className: "py-1", children: "Aktionen" })
        ] }) }),
        /* @__PURE__ */ jsx6("tbody", { className: "divide-y divide-white/5", children: connections.map((c) => /* @__PURE__ */ jsxs6("tr", { children: [
          /* @__PURE__ */ jsx6("td", { className: "py-1.5", children: c.name }),
          /* @__PURE__ */ jsx6("td", { className: "py-1.5 opacity-70", children: c.base_url }),
          /* @__PURE__ */ jsx6("td", { className: "py-1.5 whitespace-nowrap", children: /* @__PURE__ */ jsx6(CertificateStatus, { connection: c }) }),
          /* @__PURE__ */ jsx6("td", { className: "py-1.5", children: c.has_token ? "gesetzt" : /* @__PURE__ */ jsx6("span", { className: "text-amber-400", children: "fehlt" }) }),
          /* @__PURE__ */ jsx6("td", { className: "py-1.5", children: /* @__PURE__ */ jsx6(
            "button",
            {
              type: "button",
              disabled: pending === `toggle:${c.name}`,
              onClick: () => void toggleEnabled(c),
              className: "px-2 py-0.5 disabled:opacity-40 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
              children: c.enabled ? "aktiv" : "deaktiviert"
            }
          ) }),
          /* @__PURE__ */ jsx6("td", { className: "py-1.5", children: /* @__PURE__ */ jsxs6("div", { className: "flex gap-1.5", children: [
            /* @__PURE__ */ jsx6(
              "button",
              {
                type: "button",
                disabled: pending === `token:${c.name}`,
                onClick: () => void setToken(c.name, c.has_token),
                className: "px-2 py-1 disabled:opacity-40 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
                children: c.has_token ? "Token ersetzen" : "Token setzen"
              }
            ),
            /* @__PURE__ */ jsx6(
              "button",
              {
                type: "button",
                "aria-pressed": certificateFor === c.name,
                onClick: () => setCertificateFor(certificateFor === c.name ? null : c.name),
                className: "px-2 py-1 whitespace-nowrap border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
                children: "Zertifikat"
              }
            ),
            /* @__PURE__ */ jsx6(
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
      certificateFor && connections?.some((c) => c.name === certificateFor) && /* @__PURE__ */ jsx6(
        ConnectionCertificate,
        {
          extId: "backups",
          connection: certificateFor,
          onSaved: load,
          unchecked: connections?.find((c) => c.name === certificateFor)?.tls_insecure_skip_verify ?? false
        }
      ),
      !showAddForm && /* @__PURE__ */ jsx6(
        "button",
        {
          type: "button",
          onClick: () => setShowAddForm(true),
          className: "px-2 py-1 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
          children: "+ Neue Verbindung"
        }
      ),
      showAddForm && /* @__PURE__ */ jsxs6("form", { onSubmit: (e) => void addConnection(e), className: "flex flex-wrap items-end gap-2 text-xs", children: [
        /* @__PURE__ */ jsxs6("label", { className: "flex flex-col gap-1", children: [
          "Name",
          /* @__PURE__ */ jsx6("input", { required: true, value: newName, onChange: (e) => setNewName(e.target.value), className: "px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]" })
        ] }),
        /* @__PURE__ */ jsxs6("label", { className: "flex flex-col gap-1", children: [
          "Adresse (URL)",
          /* @__PURE__ */ jsx6(
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
        /* @__PURE__ */ jsxs6("label", { className: "flex flex-col gap-1", children: [
          "Token-ID",
          /* @__PURE__ */ jsx6(
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
        /* @__PURE__ */ jsxs6("label", { className: "flex items-center gap-1", title: "Veraltet: besser nach dem Anlegen unter \u201EZertifikat\u201C den Fingerabdruck merken.", children: [
          /* @__PURE__ */ jsx6("input", { type: "checkbox", checked: newTlsInsecure, onChange: (e) => setNewTlsInsecure(e.target.checked) }),
          "Zertifikat nicht pr\xFCfen (veraltet)"
        ] }),
        /* @__PURE__ */ jsx6("button", { type: "submit", disabled: pending === "add", className: "px-2 py-1 disabled:opacity-40 accent-gradient text-on-accent rounded-lg hover:bg-accent-strong font-medium", children: "Anlegen" }),
        /* @__PURE__ */ jsx6("button", { type: "button", onClick: () => setShowAddForm(false), className: "px-2 py-1 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition", children: "Abbrechen" })
      ] })
    ] })
  ] });
}
var KEEPS = ["keep-last", "keep-daily", "keep-weekly", "keep-monthly", "keep-yearly"];
var JOB_LABEL = {
  schedule: "Zeitplan",
  enabled: "Aktiv",
  storage: "Speicher",
  mode: "Modus",
  "keep-last": "Letzte",
  "keep-daily": "T\xE4gliche",
  "keep-weekly": "W\xF6chentliche",
  "keep-monthly": "Monatliche",
  "keep-yearly": "J\xE4hrliche"
};
var NEW_RULE_WARNING = "Bisher galt die Aufbewahrung des Speichers. Mit einer eigenen Regel l\xF6scht Proxmox beim n\xE4chsten Lauf, was \xFCber der neuen Grenze liegt.";
function jobChanges(before, after) {
  const changes = {};
  for (const key of ["schedule", "enabled", "storage", "mode", ...KEEPS]) {
    if (before[key] !== after[key]) changes[key] = after[key];
  }
  return changes;
}
function JobEditForm({ job, guests, onDone }) {
  const unmountSignal = useUnmountSignal();
  const [initial, setInitial] = useState5(null);
  const [values, setValues] = useState5(null);
  const [error, setError] = useState5(null);
  const [busy, setBusy] = useState5(false);
  useEffect6(() => {
    authedFetch(`/ext/backups/jobs/${encodeURIComponent(job.job_ref)}/config`).then(async (res) => {
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      setInitial(body);
      setValues(body);
    }).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [job.job_ref]);
  if (error) return /* @__PURE__ */ jsxs6("p", { className: "text-xs text-red-400", children: [
    "Job nicht lesbar: ",
    error
  ] });
  if (!initial || !values) return /* @__PURE__ */ jsx6("p", { className: "text-xs opacity-60", children: "Lade Job \u2026" });
  const changes = jobChanges(initial, values);
  const changed = Object.keys(changes);
  const keepAll = initial["keep-all"] === true;
  const shrinks = KEEPS.some((k) => Number(initial[k]) > 0 && Number(values[k]) < Number(initial[k])) || initial.enabled === true && values.enabled === false;
  const newRule = !keepAll && KEEPS.every((k) => !Number(initial[k])) && KEEPS.some((k) => Number(values[k]) > 0);
  async function save() {
    const summary = changed.map((k) => `${JOB_LABEL[k]}: ${String(initial[k])} \u2192 ${String(values[k])}`).join(", ");
    const warning = shrinks ? " Weniger Aufbewahrung: Proxmox l\xF6scht beim n\xE4chsten Lauf, was \xFCber der neuen Grenze liegt." : newRule ? ` ${NEW_RULE_WARNING}` : "";
    const ok = await deck().confirmDialog(`Backup-Job \xE4ndern (gilt f\xFCr ${guests.join(", ")}) \u2013 ${summary}?${warning}`, { danger: shrinks || newRule, confirmLabel: "\xC4ndern" });
    if (!ok) return;
    setBusy(true);
    try {
      const res = await postWithSpaceCheck(`/ext/backups/jobs/${encodeURIComponent(job.job_ref)}/edit`, { changes });
      if (res === null) return;
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      const { action, approved } = await settleAction(body, { signal: unmountSignal() });
      if (approved) {
        onDone(
          action.status === "succeeded" ? action.result?.output ?? "Ge\xE4ndert." : isActionRunning(action.status) ? RUNNING_IN_BACKGROUND : `Fehlgeschlagen: ${action.result?.error ?? action.status}`
        );
      } else if (action.status === "proposed") {
        onDone(`\xC4nderung vorgeschlagen \u2013 Freigabe durch einen Admin n\xF6tig, siehe "Aktionen".`);
      } else {
        onDone(ACTION_STATUS_LABEL[action.status ?? ""] ?? action.status ?? "?");
      }
    } catch (err) {
      onDone(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  }
  const input = (key, props = {}) => /* @__PURE__ */ jsxs6("label", { className: "flex flex-col gap-0.5", children: [
    /* @__PURE__ */ jsx6("span", { className: "opacity-60", children: JOB_LABEL[key] }),
    /* @__PURE__ */ jsx6(
      "input",
      {
        type: props.type ?? "text",
        min: props.type === "number" ? 0 : void 0,
        value: String(values[key] ?? ""),
        placeholder: props.placeholder,
        onChange: (e) => setValues({ ...values, [key]: props.type === "number" ? Number(e.target.value) : e.target.value }),
        className: `${props.width ?? "w-20"} rounded bg-white/10 px-2 py-1`
      }
    )
  ] }, key);
  return /* @__PURE__ */ jsxs6("div", { className: "p-2 text-xs panel", "data-testid": `job-edit-${job.job_ref}`, children: [
    /* @__PURE__ */ jsxs6("p", { className: "mb-2 opacity-70", children: [
      "Gilt f\xFCr alle G\xE4ste dieses Jobs: ",
      guests.join(", ")
    ] }),
    /* @__PURE__ */ jsxs6("div", { className: "flex flex-wrap items-end gap-3", children: [
      input("schedule", { width: "w-40", placeholder: "z. B. sun 03:00" }),
      input("storage", { width: "w-32" }),
      /* @__PURE__ */ jsxs6("label", { className: "flex flex-col gap-0.5", children: [
        /* @__PURE__ */ jsx6("span", { className: "opacity-60", children: JOB_LABEL.mode }),
        /* @__PURE__ */ jsxs6("select", { value: String(values.mode), onChange: (e) => setValues({ ...values, mode: e.target.value }), className: "px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]", children: [
          /* @__PURE__ */ jsx6("option", { value: "snapshot", children: "Snapshot (l\xE4uft weiter)" }),
          /* @__PURE__ */ jsx6("option", { value: "suspend", children: "Anhalten" }),
          /* @__PURE__ */ jsx6("option", { value: "stop", children: "Stoppen" })
        ] })
      ] }),
      /* @__PURE__ */ jsxs6("label", { className: "flex items-center gap-1 pb-1", children: [
        /* @__PURE__ */ jsx6("input", { type: "checkbox", checked: values.enabled === true, onChange: (e) => setValues({ ...values, enabled: e.target.checked }) }),
        JOB_LABEL.enabled
      ] })
    ] }),
    keepAll ? /* @__PURE__ */ jsx6("p", { className: "mt-2 opacity-70", children: "Aufbewahrung: Beh\xE4lt alle Sicherungen. Das l\xE4sst sich nur direkt in Proxmox \xE4ndern." }) : /* @__PURE__ */ jsxs6(Fragment5, { children: [
      /* @__PURE__ */ jsx6("p", { className: "mt-2 mb-1 opacity-60", children: "Aufbewahrung (0 = keine eigene Regel, dann gilt die des Speichers)" }),
      /* @__PURE__ */ jsx6("div", { className: "flex flex-wrap items-end gap-3", children: KEEPS.map((k) => input(k, { type: "number" })) })
    ] }),
    shrinks && /* @__PURE__ */ jsx6("p", { className: "mt-2 text-amber-300", children: "Weniger Aufbewahrung: beim n\xE4chsten Lauf l\xF6scht Proxmox, was \xFCber der neuen Grenze liegt." }),
    !shrinks && newRule && /* @__PURE__ */ jsx6("p", { className: "mt-2 text-amber-300", children: NEW_RULE_WARNING }),
    /* @__PURE__ */ jsxs6("div", { className: "mt-2 flex gap-2", children: [
      /* @__PURE__ */ jsx6("button", { type: "button", disabled: busy || changed.length === 0, onClick: () => void save(), className: "rounded bg-white/15 px-3 py-1 hover:bg-white/25 disabled:opacity-40", children: busy ? "\u2026" : "Speichern" }),
      /* @__PURE__ */ jsx6("button", { type: "button", onClick: () => onDone(null), className: "rounded px-2 py-1 opacity-70 hover:opacity-100", children: "Abbrechen" })
    ] })
  ] });
}
function BackupsPage() {
  const unmountSignal = useUnmountSignal();
  const [urlParams, updateUrl] = useUrlParams();
  const hostFilter = urlParams.get("host") || null;
  const [allJobs, setJobs] = useState5(null);
  const [error, setError] = useState5(null);
  const [expanded, setExpanded] = useState5(null);
  const [history, setHistory] = useState5([]);
  const [historyError, setHistoryError] = useState5(null);
  const expandedRef = useRef2(null);
  const [causeFor, setCauseFor] = useState5(null);
  const [busy, setBusy] = useState5(null);
  const [message, setMessage] = useState5(null);
  const [editing, setEditing] = useState5(null);
  const [restoring, setRestoring] = useState5(null);
  const load = useCallback2(() => {
    setError(null);
    authedFetch("/ext/backups/jobs").then(async (res) => {
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      return body;
    }).then(setJobs).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);
  useEffect6(() => {
    load();
  }, [load]);
  async function toggleHistory(job) {
    if (expanded === job.job_ref) {
      setExpanded(null);
      return;
    }
    setExpanded(job.job_ref);
    setCauseFor(null);
    await loadHistory(job.job_ref);
  }
  async function loadHistory(jobRef) {
    expandedRef.current = jobRef;
    setHistory(null);
    setHistoryError(null);
    try {
      const entries = await fetchJson(`/ext/backups/jobs/${encodeURIComponent(jobRef)}/history`);
      if (expandedRef.current === jobRef) setHistory(entries);
    } catch (err) {
      if (expandedRef.current === jobRef) {
        setHistory([]);
        setHistoryError(errorMessage(err));
      }
    }
  }
  async function retry(job) {
    const ok = await deck().confirmDialog(`Startet sofort ein Backup von '${job.name}' mit den Einstellungen des Jobs. Hat der Job eine Aufbewahrung, k\xF6nnen danach \xE4ltere Sicherungen dieses Gastes auf dem Speicher gel\xF6scht werden, auch manuelle und die anderer Jobs. Ohne eigene Aufbewahrung bleibt alles erhalten. Fortfahren?`);
    if (!ok) return;
    setBusy(job.job_ref);
    setMessage(null);
    try {
      const res = await postWithSpaceCheck(`/ext/backups/jobs/${encodeURIComponent(job.job_ref)}/retry`, {});
      if (res === null) return;
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      const { action, approved } = await settleAction(body, { signal: unmountSignal() });
      if (!approved && action.status === "proposed") {
        setMessage(`Backup-Retry vorgeschlagen \u2013 Freigabe durch einen Admin n\xF6tig, siehe "Aktionen".`);
      } else {
        setMessage(`Backup-Retry -> ${approved && action.status === "succeeded" ? "angenommen" : ACTION_STATUS_LABEL[action.status ?? ""] ?? action.status ?? "?"}.`);
      }
      load();
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(null);
    }
  }
  const unreachable = allJobs?.filter((j) => j.last_status === "unreachable") ?? [];
  const jobs = allJobs ? allJobs.filter((j) => j.last_status !== "unreachable") : null;
  const failing = jobs?.filter((j) => j.last_status === "failed" || j.last_status === "unknown").length ?? 0;
  const { inventory, orphans, error: inventoryError, reload: reloadInventory } = useInventory();
  const visibleJobs = jobs && hostFilter ? jobs.filter((j) => j.host_id === hostFilter) : jobs;
  return /* @__PURE__ */ jsxs6("div", { className: "mx-auto w-full max-w-7xl p-4 sm:p-6", children: [
    /* @__PURE__ */ jsx6("h2", { className: "mb-1 font-semibold text-xl tracking-tight", children: "Proxmox-Backups" }),
    /* @__PURE__ */ jsx6("p", { className: "mb-3 text-sm opacity-70", "data-testid": "backups-scope", children: "Hier stehen die Backup-Jobs deiner Proxmox-Server. Ohne Proxmox brauchst du dieses Modul nicht und kannst es ausgeschaltet lassen. Nodvard Deck selbst sicherst du unter Einstellungen \u2192 System." }),
    /* @__PURE__ */ jsx6(ConnectionsPanel, {}),
    jobs && (jobs.length > 0 || unreachable.length === 0) && /* @__PURE__ */ jsxs6("p", { className: "mb-4 text-sm opacity-70", children: [
      jobs.length,
      " Job(s) \u2013 ",
      failing > 0 ? `${failing} ohne best\xE4tigtes erfolgreiches Backup` : "alle zuletzt erfolgreich"
    ] }),
    inventoryError && /* @__PURE__ */ jsx6(LoadError, { what: "Die \xDCbersicht der vorhandenen Sicherungen", error: inventoryError, onRetry: reloadInventory }),
    /* @__PURE__ */ jsx6(
      UnprotectedSection,
      {
        inventory,
        defaultStorage: Object.entries((jobs ?? []).reduce((acc, j) => j.storage ? { ...acc, [j.storage]: (acc[j.storage] ?? 0) + 1 } : acc, {})).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "",
        onChanged: load
      }
    ),
    error && /* @__PURE__ */ jsxs6("p", { className: "text-sm text-red-400", children: [
      "Fehler: ",
      error
    ] }),
    unreachable.length > 0 && /* @__PURE__ */ jsx6("div", { className: "mb-2 text-sm text-red-400", "data-testid": "unreachable", children: unreachable.map((u) => /* @__PURE__ */ jsxs6("p", { children: [
      "Verbindung \u201E",
      u.connection,
      "\u201C nicht erreichbar \u2013 ihre Backup-Jobs fehlen hier gerade. ",
      /* @__PURE__ */ jsxs6("span", { className: "text-xs opacity-80", children: [
        "(",
        u.error ?? u.storage,
        ")"
      ] })
    ] }, u.connection)) }),
    message && /* @__PURE__ */ jsx6("p", { className: "mb-2 text-sm opacity-80", children: message }),
    !jobs && !error && /* @__PURE__ */ jsx6("p", { className: "text-sm opacity-60", children: "Lade \u2026" }),
    jobs && jobs.length === 0 && unreachable.length === 0 && /* @__PURE__ */ jsx6("p", { className: "text-sm opacity-60", children: "Keine Backup-Jobs konfiguriert." }),
    hostFilter && jobs && /* @__PURE__ */ jsxs6("div", { className: "mb-2 flex flex-wrap items-center gap-2 text-xs", children: [
      /* @__PURE__ */ jsxs6("span", { className: "accent-soft flex items-center gap-1 rounded px-2 py-0.5", "data-testid": "host-filter", children: [
        "Nur ",
        visibleJobs?.[0]?.name ?? "dieser Server",
        /* @__PURE__ */ jsx6("button", { type: "button", onClick: () => updateUrl({ host: null }), "aria-label": "Filter entfernen", className: "opacity-70 hover:opacity-100", children: "\u2715" })
      ] }),
      visibleJobs?.length === 0 && /* @__PURE__ */ jsx6("span", { className: "text-amber-300", children: "Kein Backup-Job erfasst diesen Server \u2013 siehe \u201EOhne Backup\u201C oben." })
    ] }),
    visibleJobs && visibleJobs.length > 0 && /* @__PURE__ */ jsxs6("table", { className: "w-full text-sm", children: [
      /* @__PURE__ */ jsx6("thead", { children: /* @__PURE__ */ jsxs6("tr", { className: "border-b border-white/10 text-left text-xs opacity-60", children: [
        /* @__PURE__ */ jsx6("th", { className: "py-1", children: "VM" }),
        /* @__PURE__ */ jsx6("th", { className: "py-1", children: "Storage" }),
        /* @__PURE__ */ jsx6("th", { className: "py-1", children: "Zeitplan" }),
        /* @__PURE__ */ jsx6("th", { className: "py-1", children: "Letzter Status" }),
        /* @__PURE__ */ jsx6("th", { className: "py-1", children: "Letzter Lauf" }),
        /* @__PURE__ */ jsx6("th", { className: "py-1", children: "Vorhanden" }),
        /* @__PURE__ */ jsx6("th", { className: "py-1", children: "Aktionen" })
      ] }) }),
      /* @__PURE__ */ jsx6("tbody", { className: "divide-y divide-white/5", children: visibleJobs.map((job) => /* @__PURE__ */ jsxs6(Fragment4, { children: [
        /* @__PURE__ */ jsxs6("tr", { children: [
          /* @__PURE__ */ jsxs6("td", { className: "py-1.5", children: [
            job.name,
            " ",
            /* @__PURE__ */ jsxs6("span", { className: "opacity-50", children: [
              "(",
              job.vmid,
              ")"
            ] })
          ] }),
          /* @__PURE__ */ jsx6("td", { className: "py-1.5 opacity-70", children: job.storage ?? "-" }),
          /* @__PURE__ */ jsxs6("td", { className: "py-1.5 opacity-70", children: [
            job.schedule ?? "-",
            job.next_run_at ? /* @__PURE__ */ jsxs6("div", { className: "text-xs", children: [
              "n\xE4chster: ",
              formatEpoch(job.next_run_at)
            ] }) : null,
            job.retention ? /* @__PURE__ */ jsxs6("div", { className: "text-xs", children: [
              "beh\xE4lt: ",
              job.retention
            ] }) : null
          ] }),
          /* @__PURE__ */ jsx6("td", { className: "py-1.5", children: /* @__PURE__ */ jsx6("span", { className: `rounded border px-1.5 py-0.5 text-xs ${STATUS_CLASS[job.last_status] ?? STATUS_CLASS.unknown}`, children: STATUS_LABEL[job.last_status] ?? job.last_status }) }),
          /* @__PURE__ */ jsx6("td", { className: "py-1.5 opacity-70", children: formatEpoch(job.last_run_at) }),
          /* @__PURE__ */ jsx6("td", { className: "py-1.5 text-xs opacity-70", "data-testid": `inventory-${job.job_ref}`, children: inventory ? inventoryText(inventory.get(`${job.connection}/${job.vmid}`)) : inventoryError ? "gerade nicht lesbar" : "\u2026" }),
          /* @__PURE__ */ jsx6("td", { className: "py-1.5", children: /* @__PURE__ */ jsxs6("div", { className: "flex gap-1.5", children: [
            /* @__PURE__ */ jsx6("button", { type: "button", onClick: () => void toggleHistory(job), className: "px-2 py-1 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition", children: expanded === job.job_ref ? "Verlauf ausblenden" : "Verlauf" }),
            job.last_status !== "running" && /* @__PURE__ */ jsx6(
              "button",
              {
                type: "button",
                disabled: busy === job.job_ref,
                onClick: () => void retry(job),
                className: job.last_status === "failed" ? "rounded bg-red-500/20 px-2 py-1 text-xs hover:bg-red-500/30 disabled:opacity-40" : "px-2 py-1 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition disabled:opacity-40",
                children: job.last_status === "failed" ? "Erneut versuchen" : "Jetzt sichern"
              }
            ),
            deck().hasPermission("settings.write") && /* @__PURE__ */ jsx6(
              "button",
              {
                type: "button",
                onClick: () => setEditing(editing === job.job_ref ? null : job.job_ref),
                className: "px-2 py-1 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
                children: "Job bearbeiten"
              }
            ),
            inventory && canRestore(inventory.get(`${job.connection}/${job.vmid}`)) && /* @__PURE__ */ jsx6(
              "button",
              {
                type: "button",
                onClick: () => setRestoring(restoring === job.job_ref ? null : job.job_ref),
                className: "px-2 py-1 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
                children: restoring === job.job_ref ? "Wiederherstellen schlie\xDFen" : "Als Kopie wiederherstellen"
              }
            )
          ] }) })
        ] }),
        restoring === job.job_ref && /* @__PURE__ */ jsx6("tr", { children: /* @__PURE__ */ jsx6("td", { colSpan: 7, className: "bg-black/20 py-2", children: /* @__PURE__ */ jsx6(RestoreCopyPanel, { target: { connection: job.connection, vmid: job.vmid, name: job.name }, onDone: (text) => {
          setRestoring(null);
          if (text) setMessage(text);
        } }) }) }),
        editing === job.job_ref && /* @__PURE__ */ jsx6("tr", { children: /* @__PURE__ */ jsx6("td", { colSpan: 7, className: "bg-black/20 py-2", children: /* @__PURE__ */ jsx6(
          JobEditForm,
          {
            job,
            guests: (jobs ?? []).filter((j) => j.connection === job.connection && j.job_ref.split("--")[1] === job.job_ref.split("--")[1]).map((j) => j.name),
            onDone: (text) => {
              setEditing(null);
              if (text) {
                setMessage(text);
                load();
              }
            }
          }
        ) }) }),
        expanded === job.job_ref && /* @__PURE__ */ jsx6("tr", { children: /* @__PURE__ */ jsxs6("td", { colSpan: 7, className: "bg-black/20 py-2", children: [
          (job.last_status === "failed" || causeFor) && /* @__PURE__ */ jsx6(FailureCause, { jobRef: job.job_ref, upid: causeFor }, causeFor ?? "neuester"),
          historyError && /* @__PURE__ */ jsx6(LoadError, { compact: true, what: "Die Liste der L\xE4ufe", error: historyError, onRetry: () => void loadHistory(job.job_ref) }),
          /* @__PURE__ */ jsxs6("ul", { className: "text-xs opacity-80", children: [
            history === null && /* @__PURE__ */ jsx6("li", { children: "Lade \u2026" }),
            !historyError && history?.length === 0 && /* @__PURE__ */ jsx6("li", { children: "Keine L\xE4ufe bekannt." }),
            (history ?? []).map((h) => /* @__PURE__ */ jsxs6("li", { children: [
              formatEpoch(h.started_at),
              " \u2013 ",
              historyLabel(h),
              " (",
              h.node ?? "?",
              ")",
              h.status === "failed" && /* @__PURE__ */ jsx6(
                "button",
                {
                  type: "button",
                  onClick: () => setCauseFor(h.upid),
                  "aria-label": `Warum ist der Lauf vom ${formatEpoch(h.started_at)} fehlgeschlagen?`,
                  "aria-pressed": causeFor === h.upid,
                  className: "ml-2 rounded bg-white/10 px-1.5 py-0.5 hover:bg-white/20",
                  children: "Warum?"
                }
              )
            ] }, h.upid))
          ] })
        ] }) })
      ] }, job.job_ref)) })
    ] }),
    orphans.length > 0 && /* @__PURE__ */ jsxs6("section", { className: "mt-4 text-sm", "data-testid": "orphans", children: [
      /* @__PURE__ */ jsxs6("h3", { className: "font-semibold opacity-80", children: [
        "Verwaiste Sicherungen (",
        orphans.length,
        ")"
      ] }),
      /* @__PURE__ */ jsx6("p", { className: "mb-1 text-xs opacity-60", children: "Zu diesen Dateien gibt es keinen bekannten Gast mehr (gel\xF6scht oder umgezogen). Sie belegen nur Platz \u2013 vor dem L\xF6schen in Proxmox pr\xFCfen." }),
      /* @__PURE__ */ jsx6("ul", { className: "text-xs opacity-80", children: orphans.map((o) => /* @__PURE__ */ jsxs6("li", { children: [
        o.kind === "lxc" ? "Container" : "VM",
        " ",
        o.vmid,
        " (gesehen \xFCber ",
        o.connection,
        ") \xB7 ",
        inventoryText(o),
        canRestore(o) && /* @__PURE__ */ jsx6(
          RestoreButton,
          {
            open: restoring === `orphan-${o.connection}-${o.vmid}-${o.kind ?? ""}`,
            onClick: () => setRestoring(restoring === `orphan-${o.connection}-${o.vmid}-${o.kind ?? ""}` ? null : `orphan-${o.connection}-${o.vmid}-${o.kind ?? ""}`)
          }
        ),
        restoring === `orphan-${o.connection}-${o.vmid}-${o.kind ?? ""}` && /* @__PURE__ */ jsx6("div", { className: "my-1", children: /* @__PURE__ */ jsx6(
          RestoreCopyPanel,
          {
            target: { connection: o.connection, vmid: o.vmid, name: `${o.kind === "lxc" ? "Container" : "VM"} ${o.vmid}`, kind: o.kind },
            onDone: (text) => {
              setRestoring(null);
              if (text) setMessage(text);
            }
          }
        ) })
      ] }, `${o.connection}-${o.vmid}-${o.kind ?? ""}`)) })
    ] })
  ] });
}
export {
  BackupsPage,
  jobChanges
};
