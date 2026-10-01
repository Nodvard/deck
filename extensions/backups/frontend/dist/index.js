// src/BackupsPage.tsx
import { Fragment, useCallback as useCallback2, useEffect as useEffect3, useState as useState2 } from "react";

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

// ../../_shared/frontend/src/location.ts
import { useCallback, useEffect as useEffect2, useMemo, useState } from "react";
function useUrlParams() {
  const [search, setSearch] = useState(() => window.location.search);
  const [visits, setVisits] = useState(0);
  useEffect2(() => {
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

// src/BackupsPage.tsx
import { Fragment as Fragment2, jsx, jsxs } from "react/jsx-runtime";
function formatSize(bytes) {
  if (bytes >= 1e12) return `${(bytes / 1e12).toFixed(1)} TB`;
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  return `${Math.round(bytes / 1e6)} MB`;
}
function useInventory() {
  const [inventory, setInventory] = useState2(null);
  const [orphans, setOrphans] = useState2([]);
  useEffect3(() => {
    authedFetch("/ext/backups/inventory").then((res) => res.ok ? res.json() : { guests: [], orphans: [] }).then((body) => {
      setInventory(new Map(body.guests.map((g) => [`${g.connection}/${g.vmid}`, g])));
      setOrphans(body.orphans ?? []);
    }).catch(() => setInventory(/* @__PURE__ */ new Map()));
  }, []);
  return { inventory, orphans };
}
function inventoryText(entry) {
  if (!entry || entry.count === 0) return "keine Sicherung vorhanden";
  const newest = formatEpoch(entry.newest_at);
  return `${entry.count}\xD7 \xB7 neueste ${newest} \xB7 ${formatSize(entry.total_size)} (${entry.storages.join(", ")})`;
}
function NewJobForm({ guest, defaultStorage, onDone }) {
  const unmountSignal = useUnmountSignal();
  const [schedule, setSchedule] = useState2("sun 02:00");
  const [storage, setStorage] = useState2(defaultStorage);
  const [keepLast, setKeepLast] = useState2(3);
  const [busy, setBusy] = useState2(false);
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
        onDone(action.status === "proposed" ? `Vorgeschlagen -- Freigabe durch einen Admin n\xF6tig, siehe "Aktionen".` : ACTION_STATUS_LABEL[action.status ?? ""] ?? action.status ?? "?");
      }
    } catch (err) {
      onDone(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  }
  return /* @__PURE__ */ jsxs("div", { className: "my-1 flex flex-wrap items-end gap-2 p-2 text-xs panel", "data-testid": `new-job-${guest.connection}-${guest.vmid}`, children: [
    /* @__PURE__ */ jsxs("label", { className: "flex flex-col gap-0.5", children: [
      /* @__PURE__ */ jsx("span", { className: "opacity-60", children: "Zeitplan" }),
      /* @__PURE__ */ jsx("input", { value: schedule, onChange: (e) => setSchedule(e.target.value), className: "w-32 px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]" })
    ] }),
    /* @__PURE__ */ jsxs("label", { className: "flex flex-col gap-0.5", children: [
      /* @__PURE__ */ jsx("span", { className: "opacity-60", children: "Speicher" }),
      /* @__PURE__ */ jsx("input", { value: storage, onChange: (e) => setStorage(e.target.value), className: "w-32 px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]" })
    ] }),
    /* @__PURE__ */ jsxs("label", { className: "flex flex-col gap-0.5", children: [
      /* @__PURE__ */ jsx("span", { className: "opacity-60", children: "Letzte behalten" }),
      /* @__PURE__ */ jsx("input", { type: "number", min: 1, value: keepLast, onChange: (e) => setKeepLast(Number(e.target.value)), className: "w-16 px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]" })
    ] }),
    /* @__PURE__ */ jsx("button", { type: "button", disabled: busy || !schedule || !storage, onClick: () => void create(), className: "rounded bg-emerald-500/20 px-3 py-1 text-emerald-300 hover:bg-emerald-500/30 disabled:opacity-40", children: busy ? "\u2026" : "Anlegen" }),
    /* @__PURE__ */ jsx("button", { type: "button", onClick: () => onDone(null), className: "rounded px-2 py-1 opacity-70 hover:opacity-100", children: "Abbrechen" })
  ] });
}
function UnprotectedSection({ inventory, defaultStorage = "", onChanged }) {
  const [creating, setCreating] = useState2(null);
  const [data, setData] = useState2(null);
  const [message, setMessage] = useState2(null);
  const load = useCallback2(() => {
    authedFetch("/ext/backups/unprotected").then((res) => res.ok ? res.json() : null).then(setData).catch(() => setData(null));
  }, []);
  useEffect3(() => {
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
  if (!data || data.guests.length === 0 && data.errors.length === 0) return null;
  const open = data.guests.filter((g) => !g.acknowledged);
  const accepted = data.guests.filter((g) => g.acknowledged);
  const line = (g) => /* @__PURE__ */ jsxs(Fragment2, { children: [
    g.name,
    " ",
    /* @__PURE__ */ jsxs("span", { className: "opacity-60", children: [
      "\xB7 ",
      g.kind,
      " ",
      g.vmid,
      " \xB7 ",
      g.connection
    ] }),
    inventory && /* @__PURE__ */ jsxs("span", { className: "opacity-60", children: [
      " \xB7 ",
      inventoryText(inventory.get(`${g.connection}/${g.vmid}`))
    ] })
  ] });
  return /* @__PURE__ */ jsxs(
    "section",
    {
      className: `mb-4 rounded border p-3 ${open.length > 0 ? "border-amber-500/40 bg-amber-500/5" : "border-white/10"}`,
      "data-testid": "unprotected",
      children: [
        /* @__PURE__ */ jsxs("h3", { className: `text-sm font-semibold ${open.length > 0 ? "text-amber-300" : "opacity-80"}`, children: [
          "Ohne Backup-Job (",
          open.length,
          ")"
        ] }),
        /* @__PURE__ */ jsx("p", { className: "mb-2 text-xs opacity-70", children: "Diese G\xE4ste sichert kein Backup-Job in Proxmox. Geht die Disk verloren, sind sie weg." }),
        /* @__PURE__ */ jsx("ul", { className: "space-y-0.5 text-sm", children: open.map((g) => /* @__PURE__ */ jsxs("li", { children: [
          line(g),
          canDecide && /* @__PURE__ */ jsx(
            "button",
            {
              type: "button",
              onClick: () => setCreating(creating === `${g.connection}-${g.vmid}` ? null : `${g.connection}-${g.vmid}`),
              className: "ml-2 rounded bg-emerald-500/20 px-1.5 py-0.5 text-xs text-emerald-300 hover:bg-emerald-500/30",
              children: "Job anlegen"
            }
          ),
          canDecide && /* @__PURE__ */ jsx("button", { type: "button", onClick: () => void acknowledge(g), className: "ml-2 px-1.5 py-0.5 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition", children: "Bewusst so lassen" }),
          creating === `${g.connection}-${g.vmid}` && /* @__PURE__ */ jsx(NewJobForm, { guest: g, defaultStorage, onDone: (text) => {
            setCreating(null);
            if (text) {
              setMessage(text);
              load();
              onChanged?.();
            }
          } })
        ] }, `${g.connection}-${g.vmid}`)) }),
        accepted.length > 0 && /* @__PURE__ */ jsxs("div", { className: "mt-2", "data-testid": "unprotected-accepted", children: [
          /* @__PURE__ */ jsxs("p", { className: "text-xs font-medium opacity-60", children: [
            "Bewusst ohne Backup (",
            accepted.length,
            ") -- warnt nicht auf dem Dashboard"
          ] }),
          /* @__PURE__ */ jsx("ul", { className: "space-y-0.5 text-xs opacity-70", children: accepted.map((g) => /* @__PURE__ */ jsxs("li", { children: [
            line(g),
            g.note ? /* @__PURE__ */ jsxs("span", { className: "italic", children: [
              " \xB7 \u201E",
              g.note,
              "\u201C"
            ] }) : null,
            canDecide && /* @__PURE__ */ jsx(
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
        message && /* @__PURE__ */ jsx("p", { className: "mt-1 text-xs text-red-400", children: message }),
        data.errors.map((e) => /* @__PURE__ */ jsxs("p", { className: "mt-1 text-xs text-red-400", children: [
          e.connection,
          ": nicht pr\xFCfbar (",
          e.error,
          ")"
        ] }, e.connection))
      ]
    }
  );
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
  const [connections, setConnections] = useState2(null);
  const [error, setError] = useState2(null);
  const [message, setMessage] = useState2(null);
  const [showAddForm, setShowAddForm] = useState2(false);
  const [newName, setNewName] = useState2("");
  const [newBaseUrl, setNewBaseUrl] = useState2("");
  const [newTokenId, setNewTokenId] = useState2("");
  const [newTlsInsecure, setNewTlsInsecure] = useState2(false);
  const [pending, setPending] = useState2(null);
  const load = useCallback2(() => {
    setError(null);
    authedFetch("/ext/backups/connections").then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    }).then(setConnections).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);
  useEffect3(() => {
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
      setMessage(`Verbindung "${newName}" angelegt -- jetzt noch ein Token setzen.`);
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
      const res = await authedFetch(`/extensions/backups/secrets`, {
        method: "PUT",
        body: JSON.stringify({ label: `backups-token:${name}`, value })
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
      `Verbindung "${name}" wirklich entfernen? Ein bereits gesetztes Token bleibt im Tresor stehen.`,
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
  if (error) return /* @__PURE__ */ jsxs("p", { className: "mb-4 text-xs text-red-400", children: [
    "Verbindungen nicht ladbar: ",
    error
  ] });
  return /* @__PURE__ */ jsxs("details", { className: "mb-6 p-3 panel", open: connections?.length === 0, children: [
    /* @__PURE__ */ jsx("summary", { className: "cursor-pointer text-sm font-medium", children: "Verbindungen verwalten" }),
    /* @__PURE__ */ jsxs("div", { className: "mt-3", children: [
      message && /* @__PURE__ */ jsx("p", { className: "mb-2 text-xs opacity-80", children: message }),
      connections && connections.length === 0 && /* @__PURE__ */ jsx("p", { className: "mb-2 text-xs opacity-60", children: "Noch keine Verbindung konfiguriert." }),
      connections && connections.length > 0 && /* @__PURE__ */ jsxs("table", { className: "mb-3 w-full text-xs", children: [
        /* @__PURE__ */ jsx("thead", { children: /* @__PURE__ */ jsxs("tr", { className: "border-b border-white/10 text-left uppercase opacity-60", children: [
          /* @__PURE__ */ jsx("th", { className: "py-1", children: "Name" }),
          /* @__PURE__ */ jsx("th", { className: "py-1", children: "Adresse" }),
          /* @__PURE__ */ jsx("th", { className: "py-1", children: "Zertifikat nicht pr\xFCfen" }),
          /* @__PURE__ */ jsx("th", { className: "py-1", children: "Token" }),
          /* @__PURE__ */ jsx("th", { className: "py-1", children: "Aktiv" }),
          /* @__PURE__ */ jsx("th", { className: "py-1", children: "Aktionen" })
        ] }) }),
        /* @__PURE__ */ jsx("tbody", { className: "divide-y divide-white/5", children: connections.map((c) => /* @__PURE__ */ jsxs("tr", { children: [
          /* @__PURE__ */ jsx("td", { className: "py-1.5", children: c.name }),
          /* @__PURE__ */ jsx("td", { className: "py-1.5 opacity-70", children: c.base_url }),
          /* @__PURE__ */ jsx("td", { className: "py-1.5 opacity-70", children: c.tls_insecure_skip_verify ? "ja" : "nein" }),
          /* @__PURE__ */ jsx("td", { className: "py-1.5", children: c.has_token ? "gesetzt" : /* @__PURE__ */ jsx("span", { className: "text-amber-400", children: "fehlt" }) }),
          /* @__PURE__ */ jsx("td", { className: "py-1.5", children: /* @__PURE__ */ jsx(
            "button",
            {
              type: "button",
              disabled: pending === `toggle:${c.name}`,
              onClick: () => void toggleEnabled(c),
              className: "px-2 py-0.5 disabled:opacity-40 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
              children: c.enabled ? "aktiv" : "deaktiviert"
            }
          ) }),
          /* @__PURE__ */ jsx("td", { className: "py-1.5", children: /* @__PURE__ */ jsxs("div", { className: "flex gap-1.5", children: [
            /* @__PURE__ */ jsx(
              "button",
              {
                type: "button",
                disabled: pending === `token:${c.name}`,
                onClick: () => void setToken(c.name, c.has_token),
                className: "px-2 py-1 disabled:opacity-40 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
                children: c.has_token ? "Token ersetzen" : "Token setzen"
              }
            ),
            /* @__PURE__ */ jsx(
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
      !showAddForm && /* @__PURE__ */ jsx(
        "button",
        {
          type: "button",
          onClick: () => setShowAddForm(true),
          className: "px-2 py-1 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
          children: "+ Neue Verbindung"
        }
      ),
      showAddForm && /* @__PURE__ */ jsxs("form", { onSubmit: (e) => void addConnection(e), className: "flex flex-wrap items-end gap-2 text-xs", children: [
        /* @__PURE__ */ jsxs("label", { className: "flex flex-col gap-1", children: [
          "Name",
          /* @__PURE__ */ jsx("input", { required: true, value: newName, onChange: (e) => setNewName(e.target.value), className: "px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]" })
        ] }),
        /* @__PURE__ */ jsxs("label", { className: "flex flex-col gap-1", children: [
          "Adresse (URL)",
          /* @__PURE__ */ jsx(
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
        /* @__PURE__ */ jsxs("label", { className: "flex flex-col gap-1", children: [
          "Token-ID",
          /* @__PURE__ */ jsx(
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
        /* @__PURE__ */ jsxs("label", { className: "flex items-center gap-1", children: [
          /* @__PURE__ */ jsx("input", { type: "checkbox", checked: newTlsInsecure, onChange: (e) => setNewTlsInsecure(e.target.checked) }),
          "Zertifikat nicht pr\xFCfen"
        ] }),
        /* @__PURE__ */ jsx("button", { type: "submit", disabled: pending === "add", className: "px-2 py-1 disabled:opacity-40 accent-gradient text-white rounded-lg shadow-md shadow-black/30 hover:brightness-110 font-medium", children: "Anlegen" }),
        /* @__PURE__ */ jsx("button", { type: "button", onClick: () => setShowAddForm(false), className: "px-2 py-1 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition", children: "Abbrechen" })
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
  const [initial, setInitial] = useState2(null);
  const [values, setValues] = useState2(null);
  const [error, setError] = useState2(null);
  const [busy, setBusy] = useState2(false);
  useEffect3(() => {
    authedFetch(`/ext/backups/jobs/${encodeURIComponent(job.job_ref)}/config`).then(async (res) => {
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      setInitial(body);
      setValues(body);
    }).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [job.job_ref]);
  if (error) return /* @__PURE__ */ jsxs("p", { className: "text-xs text-red-400", children: [
    "Job nicht lesbar: ",
    error
  ] });
  if (!initial || !values) return /* @__PURE__ */ jsx("p", { className: "text-xs opacity-60", children: "Lade Job \u2026" });
  const changes = jobChanges(initial, values);
  const changed = Object.keys(changes);
  const keepAll = initial["keep-all"] === true;
  const shrinks = KEEPS.some((k) => Number(initial[k]) > 0 && Number(values[k]) < Number(initial[k])) || initial.enabled === true && values.enabled === false;
  const newRule = !keepAll && KEEPS.every((k) => !Number(initial[k])) && KEEPS.some((k) => Number(values[k]) > 0);
  async function save() {
    const summary = changed.map((k) => `${JOB_LABEL[k]}: ${String(initial[k])} \u2192 ${String(values[k])}`).join(", ");
    const warning = shrinks ? " Weniger Aufbewahrung: Proxmox l\xF6scht beim n\xE4chsten Lauf, was \xFCber der neuen Grenze liegt." : newRule ? ` ${NEW_RULE_WARNING}` : "";
    const ok = await deck().confirmDialog(`Backup-Job \xE4ndern (gilt f\xFCr ${guests.join(", ")}) -- ${summary}?${warning}`, { danger: shrinks || newRule, confirmLabel: "\xC4ndern" });
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
        onDone(`\xC4nderung vorgeschlagen -- Freigabe durch einen Admin n\xF6tig, siehe "Aktionen".`);
      } else {
        onDone(ACTION_STATUS_LABEL[action.status ?? ""] ?? action.status ?? "?");
      }
    } catch (err) {
      onDone(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  }
  const input = (key, props = {}) => /* @__PURE__ */ jsxs("label", { className: "flex flex-col gap-0.5", children: [
    /* @__PURE__ */ jsx("span", { className: "opacity-60", children: JOB_LABEL[key] }),
    /* @__PURE__ */ jsx(
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
  return /* @__PURE__ */ jsxs("div", { className: "p-2 text-xs panel", "data-testid": `job-edit-${job.job_ref}`, children: [
    /* @__PURE__ */ jsxs("p", { className: "mb-2 opacity-70", children: [
      "Gilt f\xFCr alle G\xE4ste dieses Jobs: ",
      guests.join(", ")
    ] }),
    /* @__PURE__ */ jsxs("div", { className: "flex flex-wrap items-end gap-3", children: [
      input("schedule", { width: "w-40", placeholder: "z. B. sun 03:00" }),
      input("storage", { width: "w-32" }),
      /* @__PURE__ */ jsxs("label", { className: "flex flex-col gap-0.5", children: [
        /* @__PURE__ */ jsx("span", { className: "opacity-60", children: JOB_LABEL.mode }),
        /* @__PURE__ */ jsxs("select", { value: String(values.mode), onChange: (e) => setValues({ ...values, mode: e.target.value }), className: "px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]", children: [
          /* @__PURE__ */ jsx("option", { value: "snapshot", children: "Snapshot (l\xE4uft weiter)" }),
          /* @__PURE__ */ jsx("option", { value: "suspend", children: "Anhalten" }),
          /* @__PURE__ */ jsx("option", { value: "stop", children: "Stoppen" })
        ] })
      ] }),
      /* @__PURE__ */ jsxs("label", { className: "flex items-center gap-1 pb-1", children: [
        /* @__PURE__ */ jsx("input", { type: "checkbox", checked: values.enabled === true, onChange: (e) => setValues({ ...values, enabled: e.target.checked }) }),
        JOB_LABEL.enabled
      ] })
    ] }),
    keepAll ? /* @__PURE__ */ jsx("p", { className: "mt-2 opacity-70", children: "Aufbewahrung: Beh\xE4lt alle Sicherungen. Das l\xE4sst sich nur direkt in Proxmox \xE4ndern." }) : /* @__PURE__ */ jsxs(Fragment2, { children: [
      /* @__PURE__ */ jsx("p", { className: "mt-2 mb-1 opacity-60", children: "Aufbewahrung (0 = keine eigene Regel, dann gilt die des Speichers)" }),
      /* @__PURE__ */ jsx("div", { className: "flex flex-wrap items-end gap-3", children: KEEPS.map((k) => input(k, { type: "number" })) })
    ] }),
    shrinks && /* @__PURE__ */ jsx("p", { className: "mt-2 text-amber-300", children: "Weniger Aufbewahrung: beim n\xE4chsten Lauf l\xF6scht Proxmox, was \xFCber der neuen Grenze liegt." }),
    !shrinks && newRule && /* @__PURE__ */ jsx("p", { className: "mt-2 text-amber-300", children: NEW_RULE_WARNING }),
    /* @__PURE__ */ jsxs("div", { className: "mt-2 flex gap-2", children: [
      /* @__PURE__ */ jsx("button", { type: "button", disabled: busy || changed.length === 0, onClick: () => void save(), className: "rounded bg-white/15 px-3 py-1 hover:bg-white/25 disabled:opacity-40", children: busy ? "\u2026" : "Speichern" }),
      /* @__PURE__ */ jsx("button", { type: "button", onClick: () => onDone(null), className: "rounded px-2 py-1 opacity-70 hover:opacity-100", children: "Abbrechen" })
    ] })
  ] });
}
function BackupsPage() {
  const unmountSignal = useUnmountSignal();
  const [urlParams, updateUrl] = useUrlParams();
  const hostFilter = urlParams.get("host") || null;
  const [allJobs, setJobs] = useState2(null);
  const [error, setError] = useState2(null);
  const [expanded, setExpanded] = useState2(null);
  const [history, setHistory] = useState2([]);
  const [busy, setBusy] = useState2(null);
  const [message, setMessage] = useState2(null);
  const [editing, setEditing] = useState2(null);
  const load = useCallback2(() => {
    setError(null);
    authedFetch("/ext/backups/jobs").then(async (res) => {
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      return body;
    }).then(setJobs).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);
  useEffect3(() => {
    load();
  }, [load]);
  async function toggleHistory(job) {
    if (expanded === job.job_ref) {
      setExpanded(null);
      return;
    }
    setExpanded(job.job_ref);
    const res = await authedFetch(`/ext/backups/jobs/${encodeURIComponent(job.job_ref)}/history`);
    setHistory(res.ok ? await res.json() : []);
  }
  async function retry(job) {
    const ok = await deck().confirmDialog(`Startet sofort ein volles Backup von '${job.name}'. Fortfahren?`);
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
        setMessage(`Backup-Retry vorgeschlagen -- Freigabe durch einen Admin n\xF6tig, siehe "Aktionen".`);
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
  const { inventory, orphans } = useInventory();
  const visibleJobs = jobs && hostFilter ? jobs.filter((j) => j.host_id === hostFilter) : jobs;
  return /* @__PURE__ */ jsxs("div", { className: "mx-auto w-full max-w-7xl p-4 sm:p-6", children: [
    /* @__PURE__ */ jsx("h2", { className: "mb-1 font-semibold text-xl tracking-tight", children: "Backups" }),
    /* @__PURE__ */ jsx("p", { className: "mb-3 text-sm opacity-70", "data-testid": "backups-scope", children: "Hier stehen die Backup-Jobs deiner Proxmox-Server. Ohne Proxmox brauchst du dieses Modul nicht und kannst es ausgeschaltet lassen. Nodvard Deck selbst sicherst du unter Einstellungen \u2192 System." }),
    /* @__PURE__ */ jsx(ConnectionsPanel, {}),
    jobs && (jobs.length > 0 || unreachable.length === 0) && /* @__PURE__ */ jsxs("p", { className: "mb-4 text-sm opacity-70", children: [
      jobs.length,
      " Job(s) -- ",
      failing > 0 ? `${failing} ohne best\xE4tigtes erfolgreiches Backup` : "alle zuletzt erfolgreich"
    ] }),
    /* @__PURE__ */ jsx(
      UnprotectedSection,
      {
        inventory,
        defaultStorage: Object.entries((jobs ?? []).reduce((acc, j) => j.storage ? { ...acc, [j.storage]: (acc[j.storage] ?? 0) + 1 } : acc, {})).sort((a, b) => b[1] - a[1])[0]?.[0] ?? "",
        onChanged: load
      }
    ),
    error && /* @__PURE__ */ jsxs("p", { className: "text-sm text-red-400", children: [
      "Fehler: ",
      error
    ] }),
    unreachable.length > 0 && /* @__PURE__ */ jsx("div", { className: "mb-2 text-sm text-red-400", "data-testid": "unreachable", children: unreachable.map((u) => /* @__PURE__ */ jsxs("p", { children: [
      "Verbindung \u201E",
      u.connection,
      "\u201C nicht erreichbar -- ihre Backup-Jobs fehlen hier gerade. ",
      /* @__PURE__ */ jsxs("span", { className: "text-xs opacity-80", children: [
        "(",
        u.error ?? u.storage,
        ")"
      ] })
    ] }, u.connection)) }),
    message && /* @__PURE__ */ jsx("p", { className: "mb-2 text-sm opacity-80", children: message }),
    !jobs && !error && /* @__PURE__ */ jsx("p", { className: "text-sm opacity-60", children: "Lade \u2026" }),
    jobs && jobs.length === 0 && unreachable.length === 0 && /* @__PURE__ */ jsx("p", { className: "text-sm opacity-60", children: "Keine Backup-Jobs konfiguriert." }),
    hostFilter && jobs && /* @__PURE__ */ jsxs("div", { className: "mb-2 flex flex-wrap items-center gap-2 text-xs", children: [
      /* @__PURE__ */ jsxs("span", { className: "accent-soft flex items-center gap-1 rounded px-2 py-0.5", "data-testid": "host-filter", children: [
        "Nur ",
        visibleJobs?.[0]?.name ?? "dieser Host",
        /* @__PURE__ */ jsx("button", { type: "button", onClick: () => updateUrl({ host: null }), "aria-label": "Filter entfernen", className: "opacity-70 hover:opacity-100", children: "\u2715" })
      ] }),
      visibleJobs?.length === 0 && /* @__PURE__ */ jsx("span", { className: "text-amber-300", children: "Kein Backup-Job erfasst diesen Host -- siehe \u201EOhne Backup\u201C oben." })
    ] }),
    visibleJobs && visibleJobs.length > 0 && /* @__PURE__ */ jsxs("table", { className: "w-full text-sm", children: [
      /* @__PURE__ */ jsx("thead", { children: /* @__PURE__ */ jsxs("tr", { className: "border-b border-white/10 text-left text-xs uppercase opacity-60", children: [
        /* @__PURE__ */ jsx("th", { className: "py-1", children: "VM" }),
        /* @__PURE__ */ jsx("th", { className: "py-1", children: "Storage" }),
        /* @__PURE__ */ jsx("th", { className: "py-1", children: "Zeitplan" }),
        /* @__PURE__ */ jsx("th", { className: "py-1", children: "Letzter Status" }),
        /* @__PURE__ */ jsx("th", { className: "py-1", children: "Letzter Lauf" }),
        /* @__PURE__ */ jsx("th", { className: "py-1", children: "Vorhanden" }),
        /* @__PURE__ */ jsx("th", { className: "py-1", children: "Aktionen" })
      ] }) }),
      /* @__PURE__ */ jsx("tbody", { className: "divide-y divide-white/5", children: visibleJobs.map((job) => /* @__PURE__ */ jsxs(Fragment, { children: [
        /* @__PURE__ */ jsxs("tr", { children: [
          /* @__PURE__ */ jsxs("td", { className: "py-1.5", children: [
            job.name,
            " ",
            /* @__PURE__ */ jsxs("span", { className: "opacity-50", children: [
              "(",
              job.vmid,
              ")"
            ] })
          ] }),
          /* @__PURE__ */ jsx("td", { className: "py-1.5 opacity-70", children: job.storage ?? "-" }),
          /* @__PURE__ */ jsxs("td", { className: "py-1.5 opacity-70", children: [
            job.schedule ?? "-",
            job.next_run_at ? /* @__PURE__ */ jsxs("div", { className: "text-xs", children: [
              "n\xE4chster: ",
              formatEpoch(job.next_run_at)
            ] }) : null,
            job.retention ? /* @__PURE__ */ jsxs("div", { className: "text-xs", children: [
              "beh\xE4lt: ",
              job.retention
            ] }) : null
          ] }),
          /* @__PURE__ */ jsx("td", { className: "py-1.5", children: /* @__PURE__ */ jsx("span", { className: `rounded border px-1.5 py-0.5 text-xs ${STATUS_CLASS[job.last_status] ?? STATUS_CLASS.unknown}`, children: STATUS_LABEL[job.last_status] ?? job.last_status }) }),
          /* @__PURE__ */ jsx("td", { className: "py-1.5 opacity-70", children: formatEpoch(job.last_run_at) }),
          /* @__PURE__ */ jsx("td", { className: "py-1.5 text-xs opacity-70", "data-testid": `inventory-${job.job_ref}`, children: inventory ? inventoryText(inventory.get(`${job.connection}/${job.vmid}`)) : "\u2026" }),
          /* @__PURE__ */ jsx("td", { className: "py-1.5", children: /* @__PURE__ */ jsxs("div", { className: "flex gap-1.5", children: [
            /* @__PURE__ */ jsx("button", { type: "button", onClick: () => void toggleHistory(job), className: "px-2 py-1 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition", children: expanded === job.job_ref ? "Verlauf ausblenden" : "Verlauf" }),
            /* @__PURE__ */ jsx(
              "button",
              {
                type: "button",
                disabled: busy === job.job_ref,
                onClick: () => void retry(job),
                className: "rounded bg-red-500/20 px-2 py-1 text-xs hover:bg-red-500/30 disabled:opacity-40",
                children: "Erneut versuchen"
              }
            ),
            deck().hasPermission("settings.write") && /* @__PURE__ */ jsx(
              "button",
              {
                type: "button",
                onClick: () => setEditing(editing === job.job_ref ? null : job.job_ref),
                className: "px-2 py-1 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition",
                children: "Job bearbeiten"
              }
            )
          ] }) })
        ] }),
        editing === job.job_ref && /* @__PURE__ */ jsx("tr", { children: /* @__PURE__ */ jsx("td", { colSpan: 7, className: "bg-black/20 py-2", children: /* @__PURE__ */ jsx(
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
        expanded === job.job_ref && /* @__PURE__ */ jsx("tr", { children: /* @__PURE__ */ jsx("td", { colSpan: 7, className: "bg-black/20 py-2", children: /* @__PURE__ */ jsxs("ul", { className: "text-xs opacity-80", children: [
          history.length === 0 && /* @__PURE__ */ jsx("li", { children: "Keine L\xE4ufe bekannt." }),
          history.map((h) => /* @__PURE__ */ jsxs("li", { children: [
            formatEpoch(h.started_at),
            " -- ",
            STATUS_LABEL[h.status] ?? h.status,
            " (",
            h.node ?? "?",
            ")"
          ] }, h.upid))
        ] }) }) })
      ] }, job.job_ref)) })
    ] }),
    orphans.length > 0 && /* @__PURE__ */ jsxs("section", { className: "mt-4 text-sm", "data-testid": "orphans", children: [
      /* @__PURE__ */ jsxs("h3", { className: "font-semibold opacity-80", children: [
        "Verwaiste Sicherungen (",
        orphans.length,
        ")"
      ] }),
      /* @__PURE__ */ jsx("p", { className: "mb-1 text-xs opacity-60", children: "Zu diesen Dateien gibt es keinen bekannten Gast mehr (gel\xF6scht oder umgezogen). Sie belegen nur Platz -- vor dem L\xF6schen in Proxmox pr\xFCfen." }),
      /* @__PURE__ */ jsx("ul", { className: "text-xs opacity-80", children: orphans.map((o) => /* @__PURE__ */ jsxs("li", { children: [
        o.kind === "lxc" ? "Container" : "VM",
        " ",
        o.vmid,
        " (gesehen \xFCber ",
        o.connection,
        ") \xB7 ",
        inventoryText(o)
      ] }, `${o.connection}-${o.vmid}-${o.kind ?? ""}`)) })
    ] })
  ] });
}
export {
  BackupsPage,
  jobChanges
};
