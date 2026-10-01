// src/ScriptsPage.tsx
import { useCallback as useCallback2, useEffect as useEffect4, useRef as useRef2, useState as useState3 } from "react";

// ../../../frontend/src/components/SchedulePicker.tsx
import { useEffect, useState } from "react";

// ../../../frontend/src/lib/deckTimezone.ts
import { useSyncExternalStore } from "react";

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

// ../../../frontend/src/lib/deckTimezone.ts
function browserTimeZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}
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
function foreignDeckTimezone(deck2) {
  return deck2 && deck2 !== browserTimeZone() ? deck2 : null;
}

// ../../../frontend/src/components/SchedulePicker.tsx
import { jsx, jsxs } from "react/jsx-runtime";
var DAY_SHORT = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
var DAY_LONG = ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"];
var WEEK_ORDER = [1, 2, 3, 4, 5, 6, 0];
var pad = (n) => String(n).padStart(2, "0");
var isInt = (s) => /^\d+$/.test(s);
function parseCron(cron) {
  const base = { mode: "daily", hour: 3, minute: 0, days: [0], dayOfMonth: 1, cron: cron ?? "" };
  if (!cron || !cron.trim()) return { ...base, mode: "off" };
  const p = cron.trim().split(/\s+/);
  if (p.length !== 5) return { ...base, mode: "custom" };
  const [mi, h, dom, mon, dow] = p;
  if (!isInt(mi) || mon !== "*") return { ...base, mode: "custom" };
  const minute = Number(mi);
  if (h === "*" && dom === "*" && dow === "*") return { ...base, mode: "hourly", minute };
  if (!isInt(h)) return { ...base, mode: "custom" };
  const hour = Number(h);
  if (dom === "*" && dow === "*") return { ...base, mode: "daily", hour, minute };
  if (dom === "*" && /^[0-7](,[0-7])*$/.test(dow)) {
    const days = [...new Set(dow.split(",").map((d) => Number(d) % 7))].sort();
    return { ...base, mode: days.length === 7 ? "daily" : "weekly", hour, minute, days };
  }
  if (isInt(dom) && dow === "*") return { ...base, mode: "monthly", hour, minute, dayOfMonth: Number(dom) };
  return { ...base, mode: "custom" };
}
function toCron(s) {
  switch (s.mode) {
    case "off":
      return "";
    case "hourly":
      return `${s.minute} * * * *`;
    case "daily":
      return `${s.minute} ${s.hour} * * *`;
    case "weekly":
      return `${s.minute} ${s.hour} * * ${(s.days.length ? s.days : [0]).slice().sort().join(",")}`;
    case "monthly":
      return `${s.minute} ${s.hour} ${s.dayOfMonth} * *`;
    default:
      return s.cron;
  }
}
function describeSchedule(cron, offLabel = "Manuell") {
  const s = parseCron(cron);
  const time = `${pad(s.hour)}:${pad(s.minute)}`;
  switch (s.mode) {
    case "off":
      return offLabel;
    case "hourly":
      return s.minute === 0 ? "St\xFCndlich zur vollen Stunde" : `St\xFCndlich um :${pad(s.minute)}`;
    case "daily":
      return `T\xE4glich um ${time}`;
    case "weekly": {
      const ordered = WEEK_ORDER.filter((d) => s.days.includes(d));
      if (ordered.join() === "1,2,3,4,5") return `Werktags um ${time}`;
      if (ordered.join() === "6,0") return `Am Wochenende um ${time}`;
      if (ordered.length === 1) return `Jeden ${DAY_LONG[ordered[0]]} um ${time}`;
      return `${ordered.map((d) => DAY_SHORT[d]).join(", ")} um ${time}`;
    }
    case "monthly":
      return `Monatlich am ${s.dayOfMonth}. um ${time}`;
    default:
      return `Eigener Zeitplan (${cron})`;
  }
}
var formatters = /* @__PURE__ */ new Map();
function wallClock(date, timeZone) {
  let f = formatters.get(timeZone);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", {
      timeZone,
      hourCycle: "h23",
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "numeric"
    });
    formatters.set(timeZone, f);
  }
  const v = {};
  for (const p of f.formatToParts(date)) if (p.type !== "literal") v[p.type] = Number(p.value);
  return Date.UTC(v.year, v.month - 1, v.day, v.hour % 24, v.minute);
}
function instantAt(wall, timeZone) {
  const DAY = 864e5;
  const offset = (t) => wallClock(new Date(t), timeZone) - Math.floor(t / 6e4) * 6e4;
  const candidates = [.../* @__PURE__ */ new Set([offset(wall - DAY), offset(wall + DAY)])].map((o) => wall - o).filter((t) => wallClock(new Date(t), timeZone) === wall).sort((a, b) => a - b);
  return candidates.length ? new Date(candidates[0]) : null;
}
function nextRun(cron, from = /* @__PURE__ */ new Date(), timeZone) {
  const s = parseCron(cron);
  if (s.mode === "off" || s.mode === "custom") return null;
  const matches = (minute, hour, weekday, dayOfMonth) => minute === s.minute && (s.mode === "hourly" || hour === s.hour) && (s.mode !== "weekly" || s.days.includes(weekday)) && (s.mode !== "monthly" || dayOfMonth === s.dayOfMonth);
  const limit = 60 * 24 * 62;
  if (timeZone && timeZone !== browserTimeZone()) {
    const t2 = new Date(wallClock(from, timeZone) + 6e4);
    for (let i = 0; i < limit; i++) {
      if (matches(t2.getUTCMinutes(), t2.getUTCHours(), t2.getUTCDay(), t2.getUTCDate())) {
        const at = instantAt(t2.getTime(), timeZone);
        if (at && at.getTime() > from.getTime()) return at;
      }
      t2.setUTCMinutes(t2.getUTCMinutes() + 1);
    }
    return null;
  }
  const t = new Date(from.getTime());
  t.setSeconds(0, 0);
  t.setMinutes(t.getMinutes() + 1);
  for (let i = 0; i < limit; i++) {
    if (matches(t.getMinutes(), t.getHours(), t.getDay(), t.getDate())) return t;
    t.setMinutes(t.getMinutes() + 1);
  }
  return null;
}
var MODES = [
  { id: "hourly", label: "St\xFCndlich" },
  { id: "daily", label: "T\xE4glich" },
  { id: "weekly", label: "W\xF6chentlich" },
  { id: "monthly", label: "Monatlich" },
  { id: "custom", label: "Eigener" }
];
function SchedulePicker({
  value,
  onChange,
  allowOff = false,
  offLabel = "Manuell",
  label = "Zeitplan"
}) {
  const [s, setS] = useState(() => parseCron(value));
  useEffect(() => {
    if ((value ?? "") !== toCron(s)) setS(parseCron(value));
  }, [value]);
  function update(patch) {
    const next2 = { ...s, ...patch };
    if (patch.mode === "custom" && !next2.cron) next2.cron = toCron({ ...s, mode: s.mode === "off" ? "daily" : s.mode });
    setS(next2);
    onChange(toCron(next2));
  }
  const cron = toCron(s);
  const deckZone = useDeckTimezone();
  const foreignZone = foreignDeckTimezone(deckZone);
  const next = nextRun(cron, /* @__PURE__ */ new Date(), deckZone);
  const modes = allowOff ? [{ id: "off", label: offLabel }, ...MODES] : MODES;
  return /* @__PURE__ */ jsxs("div", { className: "rounded-xl border border-white/10 bg-black/20 p-4", role: "group", "aria-label": label, children: [
    /* @__PURE__ */ jsx("div", { className: "mb-4 flex flex-wrap gap-1 rounded-lg bg-white/[0.04] p-1", children: modes.map((m) => /* @__PURE__ */ jsx(
      "button",
      {
        type: "button",
        "aria-pressed": s.mode === m.id,
        onClick: () => update({ mode: m.id }),
        className: `flex-1 whitespace-nowrap rounded-md px-3 py-1.5 text-sm transition ${s.mode === m.id ? "bg-[var(--color-accent)] text-white shadow" : "text-white/60 hover:bg-white/[0.06] hover:text-white"}`,
        children: m.label
      },
      m.id
    )) }),
    s.mode !== "off" && s.mode !== "custom" && /* @__PURE__ */ jsxs("div", { className: "flex flex-wrap items-center gap-5", children: [
      /* @__PURE__ */ jsxs("div", { className: "flex items-center gap-1 font-mono text-4xl font-semibold tabular-nums tracking-tight", children: [
        s.mode === "hourly" ? /* @__PURE__ */ jsx("span", { className: "text-white/35", children: "--" }) : /* @__PURE__ */ jsx(
          "select",
          {
            "aria-label": "Stunde",
            value: s.hour,
            onChange: (e) => update({ hour: Number(e.target.value) }),
            className: "cursor-pointer appearance-none rounded-lg bg-white/[0.06] px-2 py-1 text-center outline-none hover:bg-white/[0.1] focus:ring-2 focus:ring-[var(--color-accent)]",
            children: Array.from({ length: 24 }, (_, h) => /* @__PURE__ */ jsx("option", { value: h, children: pad(h) }, h))
          }
        ),
        /* @__PURE__ */ jsx("span", { className: "text-white/40", children: ":" }),
        /* @__PURE__ */ jsx(
          "select",
          {
            "aria-label": "Minute",
            value: s.minute,
            onChange: (e) => update({ minute: Number(e.target.value) }),
            className: "cursor-pointer appearance-none rounded-lg bg-white/[0.06] px-2 py-1 text-center outline-none hover:bg-white/[0.1] focus:ring-2 focus:ring-[var(--color-accent)]",
            children: Array.from({ length: 12 }, (_, i) => i * 5).concat(s.minute % 5 ? [s.minute] : []).sort((a, b) => a - b).map((m) => /* @__PURE__ */ jsx("option", { value: m, children: pad(m) }, m))
          }
        )
      ] }),
      s.mode === "weekly" && /* @__PURE__ */ jsx("div", { className: "flex gap-1.5", children: WEEK_ORDER.map((d) => {
        const on = s.days.includes(d);
        return /* @__PURE__ */ jsx(
          "button",
          {
            type: "button",
            "aria-pressed": on,
            "aria-label": DAY_LONG[d],
            onClick: () => {
              const days = on ? s.days.filter((x) => x !== d) : [...s.days, d];
              update({ days: days.length ? days : [d] });
            },
            className: `h-9 w-9 rounded-full text-xs font-semibold transition ${on ? "accent-gradient text-white shadow" : "bg-white/[0.06] text-white/55 hover:bg-white/[0.12] hover:text-white"}`,
            children: DAY_SHORT[d]
          },
          d
        );
      }) }),
      s.mode === "monthly" && /* @__PURE__ */ jsxs("label", { className: "flex items-center gap-2 text-sm text-white/70", children: [
        "am",
        /* @__PURE__ */ jsx(
          "select",
          {
            "aria-label": "Tag im Monat",
            value: s.dayOfMonth,
            onChange: (e) => update({ dayOfMonth: Number(e.target.value) }),
            className: "rounded-lg border border-white/10 bg-black/25 px-2 py-1.5 text-sm outline-none",
            children: Array.from({ length: 28 }, (_, i) => i + 1).map((d) => /* @__PURE__ */ jsxs("option", { value: d, children: [
              d,
              "."
            ] }, d))
          }
        ),
        "Tag"
      ] })
    ] }),
    s.mode === "custom" && /* @__PURE__ */ jsxs("label", { className: "block text-sm", children: [
      /* @__PURE__ */ jsx("span", { className: "mb-1.5 block text-white/60", children: "Cron-Ausdruck (Minute Stunde Tag Monat Wochentag)" }),
      /* @__PURE__ */ jsx(
        "input",
        {
          "aria-label": "Cron-Ausdruck",
          value: s.cron,
          placeholder: "*/15 * * * *",
          onChange: (e) => update({ cron: e.target.value }),
          className: "w-full rounded-lg border border-white/10 bg-black/25 px-3 py-2 font-mono text-sm outline-none focus:border-[var(--color-accent)]"
        }
      )
    ] }),
    /* @__PURE__ */ jsxs("p", { className: "mt-3 flex flex-wrap items-center gap-x-2 text-sm", children: [
      /* @__PURE__ */ jsx("span", { className: "font-medium text-white", children: describeSchedule(cron, offLabel) }),
      next && /* @__PURE__ */ jsxs("span", { className: "text-white/45", children: [
        "\xB7 n\xE4chster Lauf",
        " ",
        next.toLocaleString("de-DE", {
          weekday: "short",
          day: "2-digit",
          month: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          timeZone: deckZone ?? void 0
        })
      ] })
    ] }),
    foreignZone && /* @__PURE__ */ jsxs("p", { className: "mt-1 text-xs text-white/45", children: [
      "Uhrzeiten in ",
      foreignZone
    ] })
  ] });
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

// ../../_shared/frontend/src/actions.ts
function isActionRunning(status) {
  return status === "approved" || status === "executing";
}
var ACTION_POLL_INTERVAL_MS = 3e3;
var ACTION_POLL_MAX_MS = 60 * 60 * 1e3;
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

// ../../_shared/frontend/src/ui.tsx
import { Fragment, jsx as jsx2, jsxs as jsxs2 } from "react/jsx-runtime";
var inputClass = "w-full rounded-lg border border-white/10 bg-black/25 px-3 py-2 text-sm text-white placeholder:text-white/30 outline-none transition focus:border-[var(--color-accent)] focus:ring-2 focus:ring-[color-mix(in_srgb,var(--color-accent)_30%,transparent)] disabled:opacity-50";
var PATHS = {
  plus: /* @__PURE__ */ jsx2("path", { d: "M12 5v14M5 12h14" }),
  search: /* @__PURE__ */ jsxs2(Fragment, { children: [
    /* @__PURE__ */ jsx2("circle", { cx: "11", cy: "11", r: "7" }),
    /* @__PURE__ */ jsx2("path", { d: "m20 20-3.5-3.5" })
  ] }),
  trash: /* @__PURE__ */ jsxs2(Fragment, { children: [
    /* @__PURE__ */ jsx2("path", { d: "M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" }),
    /* @__PURE__ */ jsx2("path", { d: "M10 11v6M14 11v6" })
  ] }),
  edit: /* @__PURE__ */ jsx2("path", { d: "M4 20h4L19 9l-4-4L4 16v4zM14 6l4 4" }),
  download: /* @__PURE__ */ jsx2("path", { d: "M12 4v11m0 0-4-4m4 4 4-4M5 20h14" }),
  upload: /* @__PURE__ */ jsx2("path", { d: "M12 20V9m0 0-4 4m4-4 4 4M5 4h14" }),
  "chevron-right": /* @__PURE__ */ jsx2("path", { d: "m9 6 6 6-6 6" }),
  "chevron-down": /* @__PURE__ */ jsx2("path", { d: "m6 9 6 6 6-6" }),
  play: /* @__PURE__ */ jsx2("path", { d: "M7 4v16l13-8z" }),
  x: /* @__PURE__ */ jsx2("path", { d: "M6 6l12 12M18 6 6 18" }),
  tag: /* @__PURE__ */ jsxs2(Fragment, { children: [
    /* @__PURE__ */ jsx2("path", { d: "M3 12V3h9l9 9-9 9z" }),
    /* @__PURE__ */ jsx2("circle", { cx: "7.5", cy: "7.5", r: "1.5" })
  ] }),
  folder: /* @__PURE__ */ jsx2("path", { d: "M3 6h6l2 2h10v11H3z" }),
  package: /* @__PURE__ */ jsxs2(Fragment, { children: [
    /* @__PURE__ */ jsx2("path", { d: "m12 3 9 5v8l-9 5-9-5V8z" }),
    /* @__PURE__ */ jsx2("path", { d: "m3 8 9 5 9-5M12 13v8" })
  ] }),
  file: /* @__PURE__ */ jsxs2(Fragment, { children: [
    /* @__PURE__ */ jsx2("path", { d: "M6 3h8l4 4v14H6z" }),
    /* @__PURE__ */ jsx2("path", { d: "M14 3v4h4M9 13h6M9 17h6" })
  ] }),
  copy: /* @__PURE__ */ jsxs2(Fragment, { children: [
    /* @__PURE__ */ jsx2("rect", { x: "8", y: "8", width: "12", height: "12", rx: "2" }),
    /* @__PURE__ */ jsx2("path", { d: "M16 8V4H4v12h4" })
  ] }),
  eye: /* @__PURE__ */ jsxs2(Fragment, { children: [
    /* @__PURE__ */ jsx2("path", { d: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z" }),
    /* @__PURE__ */ jsx2("circle", { cx: "12", cy: "12", r: "3" })
  ] }),
  refresh: /* @__PURE__ */ jsx2("path", { d: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7" }),
  clock: /* @__PURE__ */ jsxs2(Fragment, { children: [
    /* @__PURE__ */ jsx2("circle", { cx: "12", cy: "12", r: "9" }),
    /* @__PURE__ */ jsx2("path", { d: "M12 7v5l3 2" })
  ] }),
  code: /* @__PURE__ */ jsx2("path", { d: "m8 7-5 5 5 5M16 7l5 5-5 5" }),
  "map-pin": /* @__PURE__ */ jsxs2(Fragment, { children: [
    /* @__PURE__ */ jsx2("path", { d: "M12 21s-7-6.5-7-12a7 7 0 0 1 14 0c0 5.5-7 12-7 12z" }),
    /* @__PURE__ */ jsx2("circle", { cx: "12", cy: "9", r: "2.5" })
  ] })
};
function Icon({ name, size = 16, className = "" }) {
  return /* @__PURE__ */ jsx2(
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
  return /* @__PURE__ */ jsxs2("div", { className: "mx-auto w-full max-w-7xl p-4 sm:p-6", children: [
    /* @__PURE__ */ jsxs2("div", { className: "mb-6 flex flex-wrap items-end justify-between gap-3", children: [
      /* @__PURE__ */ jsxs2("div", { children: [
        /* @__PURE__ */ jsx2("h2", { className: "text-xl font-semibold tracking-tight", children: title }),
        description && /* @__PURE__ */ jsx2("p", { className: "mt-1 text-sm text-white/55", children: description })
      ] }),
      actions && /* @__PURE__ */ jsx2("div", { className: "flex flex-wrap items-center gap-2", children: actions })
    ] }),
    children
  ] });
}
function Card({ title, description, actions, children, className = "", padded = true }) {
  return /* @__PURE__ */ jsxs2("section", { className: `panel overflow-hidden ${className}`, children: [
    (title || actions) && /* @__PURE__ */ jsxs2("header", { className: "flex flex-wrap items-center justify-between gap-2 border-b border-white/[0.06] px-5 py-3.5", children: [
      /* @__PURE__ */ jsxs2("div", { children: [
        title && /* @__PURE__ */ jsx2("h3", { className: "text-sm font-semibold", children: title }),
        description && /* @__PURE__ */ jsx2("p", { className: "mt-0.5 text-xs text-white/50", children: description })
      ] }),
      actions && /* @__PURE__ */ jsx2("div", { className: "flex items-center gap-2", children: actions })
    ] }),
    /* @__PURE__ */ jsx2("div", { className: padded ? "px-5 py-4" : "", children })
  ] });
}
var BUTTON_STYLES = {
  primary: "accent-gradient text-white shadow-md shadow-black/30 hover:brightness-110",
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
  return /* @__PURE__ */ jsx2("button", { type, onClick, disabled, title, "aria-label": ariaLabel, "aria-pressed": pressed, className: buttonClass(variant, small), children });
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
  return /* @__PURE__ */ jsx2("span", { className: `inline-flex items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-medium ${TONES[tone]}`, children });
}
function EmptyState({ icon, title, text, action }) {
  return /* @__PURE__ */ jsxs2("div", { className: "panel flex flex-col items-center px-6 py-14 text-center", children: [
    /* @__PURE__ */ jsx2("span", { className: "mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-white/[0.06] text-[var(--color-accent)]", children: /* @__PURE__ */ jsx2(Icon, { name: icon, size: 22 }) }),
    /* @__PURE__ */ jsx2("p", { className: "text-sm font-medium", children: title }),
    text && /* @__PURE__ */ jsx2("p", { className: "mt-1 max-w-md text-sm text-white/50", children: text }),
    action && /* @__PURE__ */ jsx2("div", { className: "mt-5", children: action })
  ] });
}
function Notice({ text, kind = "error", onClose }) {
  const cls = kind === "ok" ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200" : "border-red-500/30 bg-red-500/10 text-red-200";
  return /* @__PURE__ */ jsxs2("div", { role: kind === "ok" ? "status" : "alert", className: `mb-4 flex items-start justify-between gap-3 rounded-lg border px-3 py-2 text-sm ${cls}`, children: [
    /* @__PURE__ */ jsx2("span", { children: text }),
    onClose && /* @__PURE__ */ jsx2("button", { type: "button", "aria-label": "Hinweis schlie\xDFen", onClick: onClose, className: "opacity-60 hover:opacity-100", children: /* @__PURE__ */ jsx2(Icon, { name: "x", size: 14 }) })
  ] });
}
function Field({ label, children, className = "" }) {
  return /* @__PURE__ */ jsxs2("label", { className: `block text-sm ${className}`, children: [
    /* @__PURE__ */ jsx2("span", { className: "mb-1.5 block text-white/70", children: label }),
    children
  ] });
}

// src/ScriptsPage.tsx
import { Fragment as Fragment2, jsx as jsx3, jsxs as jsxs3 } from "react/jsx-runtime";
var EXEC_STATUS = {
  proposed: { label: "wartet auf Freigabe", tone: "warn" },
  executing: { label: "l\xE4uft", tone: "info" },
  succeeded: { label: "erfolgreich", tone: "good" },
  failed: { label: "fehlgeschlagen", tone: "bad" },
  denied: { label: "abgelehnt", tone: "bad" },
  dismissed: { label: "verworfen", tone: "neutral" },
  expired: { label: "abgelaufen", tone: "neutral" }
};
function slugify(name) {
  return name.toLowerCase().replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
}
function triggeredBy(e) {
  const name = e.proposed_by_label || e.proposed_by;
  return e.proposed_by.startsWith("extension/") ? `Zeitplan \xB7 ${name}` : name;
}
function Executions({ items }) {
  if (items.length === 0) return /* @__PURE__ */ jsx3("p", { className: "text-sm text-white/45", children: "Noch nicht ausgef\xFChrt." });
  return /* @__PURE__ */ jsx3("ul", { className: "space-y-2", "data-testid": "executions", children: items.map((e) => {
    const st = EXEC_STATUS[e.status] ?? { label: e.status, tone: "neutral" };
    const hasOutput = Boolean(e.output || e.error);
    return /* @__PURE__ */ jsx3("li", { className: "rounded-lg border border-white/[0.08] bg-black/15 text-sm", children: /* @__PURE__ */ jsxs3("details", { open: e === items[0] && hasOutput, children: [
      /* @__PURE__ */ jsxs3("summary", { className: "flex cursor-pointer flex-wrap items-center gap-2 px-3 py-2", children: [
        /* @__PURE__ */ jsx3(Badge, { tone: st.tone, children: st.label }),
        /* @__PURE__ */ jsx3("span", { className: "font-medium", children: e.host_name ?? "\u2013" }),
        e.exit_code != null && /* @__PURE__ */ jsxs3("span", { className: "text-xs text-white/45", children: [
          "Exit ",
          e.exit_code
        ] }),
        e.duration_ms != null && /* @__PURE__ */ jsxs3("span", { className: "text-xs text-white/45", children: [
          (e.duration_ms / 1e3).toFixed(1),
          " s"
        ] }),
        !hasOutput && e.status === "failed" && /* @__PURE__ */ jsx3("span", { className: "text-xs text-red-300", children: "ohne Fehlermeldung" }),
        /* @__PURE__ */ jsxs3("span", { className: "ml-auto text-xs text-white/40", title: e.proposed_by, children: [
          e.created_at ? new Date(e.created_at).toLocaleString("de-DE") : "",
          " \xB7 ",
          triggeredBy(e)
        ] })
      ] }),
      /* @__PURE__ */ jsx3("div", { className: "border-t border-white/[0.06] px-3 py-2", children: hasOutput ? /* @__PURE__ */ jsxs3(Fragment2, { children: [
        e.output && /* @__PURE__ */ jsx3("pre", { className: "max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-black/50 p-2.5 font-mono text-[11px] text-white/80", children: e.output }),
        e.error && /* @__PURE__ */ jsx3("pre", { className: "mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-red-950/40 p-2.5 font-mono text-[11px] text-red-200", children: e.error })
      ] }) : /* @__PURE__ */ jsx3("p", { className: "text-xs text-white/45", children: e.status === "proposed" ? "Wird erst nach der Freigabe ausgef\xFChrt." : "Keine Ausgabe." }) })
    ] }) }, e.action_id);
  }) });
}
function failureReason(result) {
  const line = (result?.error ?? "").split("\n").map((l) => l.trim()).find(Boolean);
  const short = line && line.length > 80 ? `${line.slice(0, 79)}\u2026` : line;
  return [result?.exit_code != null ? `Exit ${result.exit_code}` : "", short ?? ""].filter(Boolean).join(": ") || void 0;
}
async function approveRun(actionId, immediate) {
  try {
    const res = await authedFetch(`/actions/${actionId}/approve${immediate ? "?wait=0" : ""}`, { method: "POST" });
    const body = await res.json().catch(() => ({}));
    if (res.status === 409) return { status: "failed", reason: /'expired'/.test(body.detail ?? "") ? "Vorschlag abgelaufen" : "schon anderweitig bearbeitet" };
    if (res.status === 403) return { status: "failed", reason: "keine Berechtigung zum Freigeben" };
    if (!res.ok) return { status: "failed", reason: errorFromBody(body, res.status) };
    return { status: body.status ?? "", reason: failureReason(body.result) };
  } catch (err) {
    return { status: "failed", reason: err instanceof Error ? err.message : String(err) };
  }
}
async function finishRun(actionId, signal) {
  try {
    const state = await waitForAction(actionId, {}, { signal });
    return { status: state.status ?? "executing", reason: failureReason(state.result ?? void 0) };
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") return { status: "executing" };
    return { status: "failed", reason: `Stand nicht abrufbar: ${err instanceof Error ? err.message : String(err)}` };
  }
}
function countOutcome(outcome, host, verdict) {
  if (verdict.status === "succeeded") outcome.succeeded += 1;
  else if (isActionRunning(verdict.status)) outcome.running += 1;
  else if (verdict.status === "proposed") outcome.waiting += 1;
  else {
    let reason = verdict.reason;
    if (!reason && verdict.status === "denied") reason = "von einer Schutzregel blockiert";
    else if (!reason && verdict.status !== "failed") reason = EXEC_STATUS[verdict.status]?.label ?? verdict.status;
    outcome.failed.push(reason ? `${host} (${reason})` : host);
  }
}
function describeRun(outcome, skipped) {
  return [
    outcome.succeeded ? `${outcome.succeeded} Ziel(e) ausgef\xFChrt` : "",
    outcome.running ? `${outcome.running} laufen noch` : "",
    outcome.waiting ? `${outcome.waiting} warten auf Freigabe unter \u201EAktionen\u201C` : "",
    outcome.failed.length ? `${outcome.failed.length} fehlgeschlagen: ${outcome.failed.join(", ")}` : "",
    skipped.length ? `${skipped.length} \xFCbersprungen: ${skipped.join(", ")}` : ""
  ].filter(Boolean).join(" \xB7 ") || "Keine Ziele.";
}
function emptyDraft(hostId = null) {
  return {
    id: "",
    name: "",
    description: "",
    content: "#!/bin/sh\n",
    params_schema: {},
    target: { kind: "host", host_id: hostId },
    schedule: null,
    enabled: true,
    job_id: ""
  };
}
function targetHint(target) {
  if (target.kind === "group" && !target.group_id) return "Bitte zuerst eine Gruppe w\xE4hlen.";
  if ((target.kind ?? "host") === "host" && !target.host_id) return "Bitte zuerst einen Server w\xE4hlen.";
  return null;
}
function targetLabel(target, hosts, groups) {
  if (target.kind === "all") return "Alle Server";
  if (target.kind === "group") return groups.find((g) => g.id === target.group_id)?.name ?? "Gruppe";
  const host = hosts.find((h) => h.id === target.host_id);
  return host ? host.display_name || host.name : "Ein Server";
}
function ScriptsPage() {
  const [urlParams, updateUrl] = useUrlParams();
  const hostFilter = urlParams.get("host") || null;
  const [scripts, setScripts] = useState3(null);
  const [jobs, setJobs] = useState3([]);
  const [hosts, setHosts] = useState3([]);
  const [groups, setGroups] = useState3([]);
  const [error, setError] = useState3(null);
  const [selectedId, setSelectedId] = useState3(null);
  const [draft, setDraft] = useState3(() => emptyDraft(hostFilter));
  const [isNew, setIsNew] = useState3(true);
  const [idTouched, setIdTouched] = useState3(false);
  const [busy, setBusy] = useState3(false);
  const runSeq = useRef2(0);
  useEffect4(() => () => {
    runSeq.current += 1;
  }, []);
  const unmountSignal = useUnmountSignal();
  const [message, setMessage] = useState3(null);
  const [runs, setRuns] = useState3([]);
  const [executions, setExecutions] = useState3([]);
  const [history, setHistory] = useState3([]);
  const loadExecutions = useCallback2((scriptId) => {
    authedFetch(`/ext/scripts/scripts/${scriptId}/runs`).then((res) => res.ok ? res.json() : []).then(setExecutions).catch(() => setExecutions([]));
  }, []);
  const load = useCallback2(() => {
    setError(null);
    Promise.all([
      authedFetch("/ext/scripts/scripts").then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      }),
      authedFetch("/jobs?ext_id=scripts").then((res) => res.ok ? res.json() : [])
    ]).then(([s, j]) => {
      setScripts(s);
      setJobs(j);
    }).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);
  useEffect4(() => {
    load();
    authedFetch("/hosts").then((r) => r.ok ? r.json() : []).then(setHosts).catch(() => setHosts([]));
    authedFetch("/host-groups").then((r) => r.ok ? r.json() : []).then(setGroups).catch(() => setGroups([]));
  }, [load]);
  function selectScript(script) {
    setSelectedId(script.id);
    setDraft(script);
    setIsNew(false);
    setMessage(null);
    runSeq.current += 1;
    const job = jobs.find((j) => j.ext_job_key === script.job_id);
    if (job) {
      authedFetch(`/jobs/${job.id}/runs?limit=10`).then((res) => res.ok ? res.json() : []).then(setRuns).catch(() => setRuns([]));
    } else {
      setRuns([]);
    }
    loadExecutions(script.id);
    authedFetch(`/ext/scripts/scripts/${script.id}/history`).then((res) => res.ok ? res.json() : []).then(setHistory).catch(() => setHistory([]));
  }
  function selectNew() {
    setSelectedId(null);
    setDraft(emptyDraft(hostFilter));
    setIsNew(true);
    setIdTouched(false);
    setMessage(null);
    runSeq.current += 1;
    setRuns([]);
    setExecutions([]);
    setHistory([]);
  }
  async function save() {
    if (!draft.id.trim()) {
      setMessage({ kind: "error", text: "Bitte einen Namen bzw. eine Kennung vergeben." });
      return;
    }
    if (isNew && scripts?.some((s) => s.id === draft.id)) {
      setMessage({ kind: "error", text: `Ein Skript mit der Kennung \u201E${draft.id}\u201C gibt es schon \u2013 bitte eine andere Kennung w\xE4hlen.` });
      return;
    }
    setBusy(true);
    setMessage(null);
    runSeq.current += 1;
    try {
      const res = await authedFetch(`/ext/scripts/scripts/${draft.id}${isNew ? "?create=true" : ""}`, {
        method: "PUT",
        body: JSON.stringify({
          name: draft.name || draft.id,
          description: draft.description,
          content: draft.content,
          params_schema: draft.params_schema,
          target: draft.target,
          schedule: draft.schedule || null,
          enabled: draft.enabled
        })
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      setMessage({ kind: "ok", text: "Gespeichert." });
      setIsNew(false);
      setSelectedId(draft.id);
      load();
    } catch (err) {
      setMessage({ kind: "error", text: `Fehler: ${err instanceof Error ? err.message : String(err)}` });
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    if (!selectedId) return;
    const ok = await deck().confirmDialog(`Skript \u201E${draft.name || selectedId}\u201C l\xF6schen?`, { danger: true, confirmLabel: "L\xF6schen" });
    if (!ok) return;
    setBusy(true);
    try {
      const res = await authedFetch(`/ext/scripts/scripts/${selectedId}`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`);
      selectNew();
      load();
    } catch (err) {
      setMessage({ kind: "error", text: `Fehler: ${err instanceof Error ? err.message : String(err)}` });
    } finally {
      setBusy(false);
    }
  }
  async function runNow() {
    if (!selectedId) return;
    const ok = await deck().confirmDialog(`Skript \u201E${draft.name || selectedId}\u201C jetzt ausf\xFChren?`, { danger: true, confirmLabel: "Ausf\xFChren" });
    if (!ok) return;
    setBusy(true);
    setMessage(null);
    const seq = ++runSeq.current;
    try {
      const res = await authedFetch(`/ext/scripts/scripts/${selectedId}/run`, {
        method: "POST",
        body: JSON.stringify({ param_overrides: {} })
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      const results = body.results ?? [];
      const outcome = { succeeded: 0, running: 0, waiting: 0, failed: [] };
      const canApprove = deck().hasPermission("actions.approve:high");
      const immediate = results.filter((r) => !r.skipped && !r.error && r.action_id && r.status === "proposed").length > 1;
      const running = [];
      const record = (host, actionId, verdict) => {
        countOutcome(outcome, host, verdict);
        if (isActionRunning(verdict.status)) running.push({ host, actionId });
      };
      for (const r of results) {
        if (r.skipped) continue;
        const host = r.host_name ?? r.host_id ?? "?";
        if (r.error) {
          outcome.failed.push(`${host} (${r.error})`);
        } else if (r.action_id && r.status === "proposed" && canApprove) {
          record(host, r.action_id, await approveRun(r.action_id, immediate));
        } else if (r.status === "proposed") {
          outcome.waiting += 1;
        } else if (r.action_id) {
          record(host, r.action_id, { status: r.status ?? "" });
        }
      }
      const skipped = results.filter((r) => r.skipped).map((r) => `${r.host_name ?? "?"} (${r.skipped})`);
      setMessage({ kind: outcome.failed.length ? "error" : "ok", text: describeRun(outcome, skipped) });
      loadExecutions(selectedId);
      if (running.length > 0) {
        const scriptId = selectedId;
        const signal = unmountSignal();
        for (const r of running) {
          void finishRun(r.actionId, signal).then((verdict) => {
            if (runSeq.current !== seq || isActionRunning(verdict.status)) return;
            outcome.running -= 1;
            countOutcome(outcome, r.host, verdict);
            setMessage({ kind: outcome.failed.length ? "error" : "ok", text: describeRun(outcome, skipped) });
            loadExecutions(scriptId);
          });
        }
      }
    } catch (err) {
      setMessage({ kind: "error", text: `Fehler: ${err instanceof Error ? err.message : String(err)}` });
    } finally {
      setBusy(false);
    }
  }
  const visibleScripts = hostFilter ? scripts?.filter((s) => s.target.kind === "host" && s.target.host_id === hostFilter) : scripts;
  const lines = draft.content.split("\n").length;
  const missingTarget = targetHint(draft.target);
  const showEditor = !isNew || selectedId !== null || (scripts?.length ?? 0) > 0 || draft.id !== "" || draft.name !== "";
  return /* @__PURE__ */ jsxs3(
    Page,
    {
      title: "Skripte",
      description: "Eigene Befehle zentral pflegen und auf einem, mehreren oder allen Servern ausf\xFChren \u2013 sofort oder nach Zeitplan.",
      actions: /* @__PURE__ */ jsxs3(Button, { variant: "primary", onClick: selectNew, children: [
        /* @__PURE__ */ jsx3(Icon, { name: "plus", size: 14 }),
        " Neues Skript"
      ] }),
      children: [
        error && /* @__PURE__ */ jsx3(Notice, { text: `Fehler: ${error}` }),
        /* @__PURE__ */ jsxs3("div", { className: "flex flex-col gap-5 lg:flex-row lg:items-start", children: [
          /* @__PURE__ */ jsx3("aside", { className: "lg:w-72 lg:flex-none", children: /* @__PURE__ */ jsxs3(Card, { title: `Skripte${scripts ? ` (${visibleScripts?.length ?? 0})` : ""}`, padded: false, children: [
            hostFilter && /* @__PURE__ */ jsxs3("div", { className: "flex items-center justify-between gap-2 border-b border-white/[0.06] px-4 py-2 text-xs", "data-testid": "host-filter", children: [
              /* @__PURE__ */ jsx3("span", { className: "text-white/60", children: "Nur Skripte f\xFCr diesen Server" }),
              /* @__PURE__ */ jsx3("button", { type: "button", onClick: () => updateUrl({ host: null }), "aria-label": "Filter entfernen", className: "text-white/40 hover:text-white", children: /* @__PURE__ */ jsx3(Icon, { name: "x", size: 13 }) })
            ] }),
            !scripts && !error && /* @__PURE__ */ jsx3("p", { className: "px-4 py-3 text-sm text-white/45", children: "Lade \u2026" }),
            scripts && visibleScripts?.length === 0 && /* @__PURE__ */ jsx3("p", { className: "px-4 py-4 text-sm text-white/45", children: hostFilter ? "Noch kein Skript f\xFCr diesen Server \u2013 \u201ENeues Skript\u201C legt eins an." : "Noch keine Skripte." }),
            /* @__PURE__ */ jsx3("ul", { className: "divide-y divide-white/[0.05]", children: visibleScripts?.map((s) => /* @__PURE__ */ jsx3("li", { children: /* @__PURE__ */ jsxs3(
              "button",
              {
                type: "button",
                onClick: () => selectScript(s),
                className: `flex w-full items-start gap-2.5 px-4 py-3 text-left transition ${selectedId === s.id ? "bg-white/[0.07]" : "hover:bg-white/[0.03]"}`,
                children: [
                  /* @__PURE__ */ jsx3(Icon, { name: "code", size: 15, className: `mt-0.5 ${selectedId === s.id ? "text-[var(--color-accent)]" : "text-white/40"}` }),
                  /* @__PURE__ */ jsxs3("span", { className: "min-w-0 flex-1", children: [
                    /* @__PURE__ */ jsx3("span", { className: "block truncate text-sm font-medium", children: s.name }),
                    /* @__PURE__ */ jsxs3("span", { className: "mt-0.5 block text-xs text-white/45", children: [
                      /* @__PURE__ */ jsx3(Icon, { name: "clock", size: 11, className: "mr-1 inline -mt-px align-middle" }),
                      describeSchedule(s.schedule),
                      " \xB7 ",
                      targetLabel(s.target, hosts, groups)
                    ] })
                  ] }),
                  !s.enabled && /* @__PURE__ */ jsx3(Badge, { children: "aus" })
                ]
              }
            ) }, s.id)) })
          ] }) }),
          /* @__PURE__ */ jsx3("div", { className: "min-w-0 flex-1 space-y-5", children: !showEditor ? /* @__PURE__ */ jsx3(
            EmptyState,
            {
              icon: "code",
              title: "Noch keine Skripte",
              text: "Lege z. B. ein Update-, Aufr\xE4um- oder Pr\xFCfskript an und starte es per Klick auf einem oder allen Servern \u2013 oder plane es: Dann erscheint jeder Lauf zur Freigabe unter \u201EAktionen\u201C.",
              action: /* @__PURE__ */ jsxs3(Button, { variant: "primary", onClick: () => setDraft({ ...emptyDraft(hostFilter), name: "Neues Skript", id: "neues-skript" }), children: [
                /* @__PURE__ */ jsx3(Icon, { name: "plus", size: 14 }),
                " Erstes Skript anlegen"
              ] })
            }
          ) : /* @__PURE__ */ jsxs3(Fragment2, { children: [
            message && /* @__PURE__ */ jsx3(Notice, { text: message.text, kind: message.kind, onClose: () => setMessage(null) }),
            /* @__PURE__ */ jsxs3(
              Card,
              {
                title: isNew ? "Neues Skript" : draft.name || draft.id,
                actions: /* @__PURE__ */ jsxs3("label", { className: "flex items-center gap-2 text-xs text-white/60", children: [
                  /* @__PURE__ */ jsx3("input", { type: "checkbox", checked: draft.enabled, onChange: (e) => setDraft({ ...draft, enabled: e.target.checked }) }),
                  "aktiv"
                ] }),
                children: [
                  /* @__PURE__ */ jsxs3("div", { className: "grid gap-4 md:grid-cols-2", children: [
                    /* @__PURE__ */ jsx3(Field, { label: "Name", children: /* @__PURE__ */ jsx3(
                      "input",
                      {
                        className: inputClass,
                        placeholder: "z. B. Sicherheitsupdates",
                        value: draft.name,
                        onChange: (e) => setDraft({ ...draft, name: e.target.value, id: isNew && !idTouched ? slugify(e.target.value) : draft.id })
                      }
                    ) }),
                    /* @__PURE__ */ jsx3(Field, { label: "Kennung", children: /* @__PURE__ */ jsx3(
                      "input",
                      {
                        className: `${inputClass} font-mono`,
                        placeholder: "skript-id",
                        value: draft.id,
                        disabled: !isNew,
                        onChange: (e) => {
                          setIdTouched(true);
                          setDraft({ ...draft, id: e.target.value });
                        }
                      }
                    ) }),
                    /* @__PURE__ */ jsx3(Field, { label: "Ausf\xFChren auf", children: /* @__PURE__ */ jsxs3("div", { className: "flex gap-2", children: [
                      /* @__PURE__ */ jsxs3(
                        "select",
                        {
                          className: `${inputClass} w-auto`,
                          "aria-label": "Ziel-Art",
                          value: draft.target.kind ?? "host",
                          onChange: (e) => setDraft({ ...draft, target: { kind: e.target.value } }),
                          children: [
                            /* @__PURE__ */ jsx3("option", { value: "host", children: "Einem Server" }),
                            /* @__PURE__ */ jsx3("option", { value: "group", children: "Einer Gruppe" }),
                            /* @__PURE__ */ jsx3("option", { value: "all", children: "Allen Servern" })
                          ]
                        }
                      ),
                      draft.target.kind === "host" && /* @__PURE__ */ jsxs3(
                        "select",
                        {
                          className: inputClass,
                          "aria-label": "Server",
                          value: draft.target.host_id ?? "",
                          onChange: (e) => setDraft({ ...draft, target: { kind: "host", host_id: e.target.value || null } }),
                          children: [
                            /* @__PURE__ */ jsx3("option", { value: "", children: "Server w\xE4hlen \u2026" }),
                            hosts.map((h) => /* @__PURE__ */ jsx3("option", { value: h.id, children: h.display_name || h.name }, h.id)),
                            draft.target.host_id && !hosts.some((h) => h.id === draft.target.host_id) && /* @__PURE__ */ jsx3("option", { value: draft.target.host_id, children: draft.target.host_id })
                          ]
                        }
                      ),
                      draft.target.kind === "group" && /* @__PURE__ */ jsxs3(
                        "select",
                        {
                          className: inputClass,
                          "aria-label": "Gruppe",
                          value: draft.target.group_id ?? "",
                          onChange: (e) => setDraft({ ...draft, target: { kind: "group", group_id: e.target.value || null } }),
                          children: [
                            /* @__PURE__ */ jsx3("option", { value: "", children: "Gruppe w\xE4hlen \u2026" }),
                            groups.map((g) => /* @__PURE__ */ jsx3("option", { value: g.id, children: g.name }, g.id))
                          ]
                        }
                      )
                    ] }) }),
                    /* @__PURE__ */ jsxs3("div", { className: "md:col-span-2", children: [
                      /* @__PURE__ */ jsx3("p", { className: "mb-1.5 text-sm text-white/70", children: "Zeitplan" }),
                      /* @__PURE__ */ jsx3(
                        SchedulePicker,
                        {
                          label: "Zeitplan",
                          allowOff: true,
                          offLabel: "Manuell",
                          value: draft.schedule,
                          onChange: (cron) => setDraft({ ...draft, schedule: cron || null })
                        }
                      ),
                      draft.schedule && /* @__PURE__ */ jsxs3("p", { className: "mt-2 flex items-start gap-1.5 text-xs text-white/55", "data-testid": "schedule-hint", children: [
                        /* @__PURE__ */ jsx3(Icon, { name: "clock", size: 12, className: "mt-0.5 flex-none" }),
                        /* @__PURE__ */ jsx3("span", { children: "Geplante L\xE4ufe erscheinen als Vorschlag unter \u201EAktionen\u201C und m\xFCssen dort freigegeben werden, sonst verfallen sie nach 24 Stunden. Ohne R\xFCckfrage laufen sie nur, wenn unter Einstellungen \u2192 Automatik \u201ESelbstst\xE4ndig handeln\u201C bis Risikostufe \u201EHoch\u201C oder \u201EKritisch\u201C erlaubt ist." })
                      ] })
                    ] })
                  ] }),
                  /* @__PURE__ */ jsxs3("div", { className: "mt-4 overflow-hidden rounded-lg border border-white/10 bg-black/40", children: [
                    /* @__PURE__ */ jsxs3("div", { className: "flex items-center justify-between border-b border-white/[0.06] px-3 py-1.5 text-[11px] text-white/40", children: [
                      /* @__PURE__ */ jsx3("span", { children: "Shell-Skript" }),
                      /* @__PURE__ */ jsxs3("span", { children: [
                        lines,
                        " Zeilen"
                      ] })
                    ] }),
                    /* @__PURE__ */ jsx3(
                      "textarea",
                      {
                        "aria-label": "Skript-Inhalt",
                        className: "block h-72 w-full resize-y bg-transparent p-3 font-mono text-xs leading-relaxed text-white/90 outline-none",
                        spellCheck: false,
                        value: draft.content,
                        onChange: (e) => setDraft({ ...draft, content: e.target.value })
                      }
                    )
                  ] }),
                  /* @__PURE__ */ jsxs3("div", { className: "mt-4 flex flex-wrap items-center gap-2", children: [
                    /* @__PURE__ */ jsx3(Button, { variant: "primary", disabled: busy || missingTarget !== null, onClick: () => void save(), children: "Speichern" }),
                    missingTarget && /* @__PURE__ */ jsx3("span", { className: "text-xs text-amber-300", children: missingTarget }),
                    !isNew && /* @__PURE__ */ jsxs3(Fragment2, { children: [
                      /* @__PURE__ */ jsxs3(Button, { disabled: busy, onClick: () => void runNow(), children: [
                        /* @__PURE__ */ jsx3(Icon, { name: "play", size: 13 }),
                        " Jetzt ausf\xFChren"
                      ] }),
                      /* @__PURE__ */ jsx3("span", { className: "flex-1" }),
                      /* @__PURE__ */ jsxs3(Button, { variant: "danger", disabled: busy, onClick: () => void remove(), children: [
                        /* @__PURE__ */ jsx3(Icon, { name: "trash", size: 13 }),
                        " L\xF6schen"
                      ] })
                    ] })
                  ] })
                ]
              }
            ),
            !isNew && /* @__PURE__ */ jsx3(
              Card,
              {
                title: "Ausf\xFChrungen",
                description: "Die letzten L\xE4ufe mit Ausgabe. Aufklappen zeigt, was das Skript ausgegeben hat.",
                actions: /* @__PURE__ */ jsxs3(Button, { small: true, variant: "ghost", onClick: () => selectedId && loadExecutions(selectedId), children: [
                  /* @__PURE__ */ jsx3(Icon, { name: "refresh", size: 12 }),
                  " Aktualisieren"
                ] }),
                children: /* @__PURE__ */ jsx3(Executions, { items: executions })
              }
            ),
            !isNew && /* @__PURE__ */ jsxs3("div", { className: "grid gap-5 md:grid-cols-2", children: [
              /* @__PURE__ */ jsx3(Card, { title: "Zeitplan-L\xE4ufe", children: /* @__PURE__ */ jsxs3("ul", { className: "space-y-1.5 text-sm", children: [
                runs.length === 0 && /* @__PURE__ */ jsx3("li", { className: "text-white/45", children: "Noch keine L\xE4ufe." }),
                runs.map((r) => /* @__PURE__ */ jsxs3("li", { className: "flex flex-wrap items-center gap-2", children: [
                  /* @__PURE__ */ jsx3(Badge, { tone: r.status === "succeeded" ? "good" : r.status === "failed" ? "bad" : "neutral", children: r.status === "succeeded" ? "erfolgreich" : r.status === "failed" ? "fehlgeschlagen" : r.status }),
                  /* @__PURE__ */ jsx3("span", { className: "text-white/70", children: new Date(r.started_at).toLocaleString("de-DE") }),
                  /* @__PURE__ */ jsx3("span", { className: "text-xs text-white/40", children: r.trigger === "schedule" ? "Zeitplan" : r.trigger }),
                  r.error && /* @__PURE__ */ jsx3("span", { className: "w-full text-xs text-red-300", children: r.error })
                ] }, r.id))
              ] }) }),
              /* @__PURE__ */ jsx3(Card, { title: "Versionen", description: "Jede \xC4nderung wird gespeichert.", children: /* @__PURE__ */ jsxs3("ul", { className: "space-y-1.5 text-sm", children: [
                history.length === 0 && /* @__PURE__ */ jsx3("li", { className: "text-white/45", children: "Keine Versionen." }),
                history.map((h) => /* @__PURE__ */ jsxs3("li", { className: "flex items-baseline gap-2", children: [
                  /* @__PURE__ */ jsx3("code", { className: "text-[11px] text-white/35", children: h.sha.slice(0, 7) }),
                  /* @__PURE__ */ jsx3("span", { className: "min-w-0 flex-1 truncate text-white/75", children: h.message }),
                  /* @__PURE__ */ jsx3("span", { className: "text-xs text-white/35", children: new Date(h.commit_time * 1e3).toLocaleDateString("de-DE") })
                ] }, h.sha))
              ] }) })
            ] })
          ] }) })
        ] })
      ]
    }
  );
}
export {
  Executions,
  ScriptsPage,
  describeSchedule
};
