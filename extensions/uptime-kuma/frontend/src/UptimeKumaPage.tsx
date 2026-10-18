/** Uptime-Kuma-Seite (PageSpec `component="UptimeKumaPage"`): je Verbindung eine Karte, nur lesend. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { errorMessage, fetchJson, LoadError } from "../../../_shared/frontend/src/loadError";
import { SettingsLink, safeHref } from "../../../_shared/frontend/src/links";
import { useDeckTimezone } from "../../../_shared/frontend/src/timezone";
import { Badge, Button, Card, EmptyState, Page, SearchInput, Stat, buttonClass, type Tone } from "../../../_shared/frontend/src/ui";
import { deck } from "../../../_shared/frontend/src/deck";

const API = "/ext/uptime-kuma";
const SETTINGS_PATH = "/settings/extensions/uptime-kuma";
const CERT_WARN_DAYS = 14;
const SEARCH_FROM = 8;

export type MonitorStatus = "up" | "down" | "pending" | "maintenance" | "unknown";
export type ConnectionState = "ok" | "not_configured" | "auth_failed" | "unreachable" | "error";

export interface Summary {
  total: number;
  up: number;
  down: number;
  pending: number;
  maintenance: number;
}

export interface Monitor {
  name: string;
  type: string | null;
  url: string | null;
  status: MonitorStatus | string;
  response_ms: number | null;
  uptime_24h: number | null;
  cert_days_left: number | null;
  cert_valid: boolean | null;
}

export interface Outage {
  monitor: string;
  time: string | null;
  message: string;
}

export interface Connection {
  name: string;
  url: string | null;
  state: ConnectionState | string;
  message: string | null;
  monitors: Monitor[];
  summary: Summary | null;
  outages: Outage[];
  status_page: boolean;
}

export interface Overview {
  connections: Connection[];
  summary: Summary;
}

const STATUS: Record<MonitorStatus, { label: string; tone: Tone }> = {
  up: { label: "läuft", tone: "good" },
  down: { label: "ausgefallen", tone: "bad" },
  pending: { label: "ausstehend", tone: "warn" },
  maintenance: { label: "Wartung", tone: "info" },
  unknown: { label: "unbekannt", tone: "neutral" },
};

const PROBLEM: Record<Exclude<ConnectionState, "ok" | "not_configured">, { label: string; tone: Tone }> = {
  auth_failed: { label: "Anmeldung fehlgeschlagen", tone: "bad" },
  unreachable: { label: "nicht erreichbar", tone: "bad" },
  error: { label: "Fehler", tone: "warn" },
};

function statusOf(status: string): { label: string; tone: Tone } {
  return STATUS[status as MonitorStatus] ?? STATUS.unknown;
}

function percent(value: number): string {
  return `${value.toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} %`;
}

function dayText(days: number): string {
  return `${days} ${days === 1 ? "Tag" : "Tagen"}`;
}

function formatTime(iso: string | null, timeZone: string | null): string {
  if (!iso) return "Zeit unbekannt";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "Zeit unbekannt";
  const options: Intl.DateTimeFormatOptions = { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" };
  try {
    return date.toLocaleString("de-DE", timeZone ? { ...options, timeZone } : options);
  } catch {
    // Unbekannte Zone: lieber die Zeit des Geräts als gar keine.
    return date.toLocaleString("de-DE", options);
  }
}

/** Zertifikat eines Monitors: Warnung ab 14 Tagen, sonst nur ein leiser Hinweis. */
export function CertInfo({ days, valid }: { days: number | null; valid: boolean | null }): JSX.Element | null {
  if (days != null && days < 0) return <Badge tone="bad">Zertifikat abgelaufen</Badge>;
  if (valid === false) return <Badge tone="bad">Zertifikat ungültig</Badge>;
  if (days == null) return null;
  if (days === 0) return <Badge tone="warn">Zertifikat läuft heute ab</Badge>;
  if (days <= CERT_WARN_DAYS) return <Badge tone="warn">Zertifikat läuft in {dayText(days)} ab</Badge>;
  return <span className="text-xs text-white/45">Zertifikat noch {days} Tage</span>;
}

function MonitorRow({ monitor }: { monitor: Monitor }) {
  const status = statusOf(monitor.status);
  const target = [monitor.type, monitor.url].filter(Boolean).join(" · ");
  const tint = monitor.status === "down" ? "bg-red-500/[0.06]" : monitor.status === "pending" ? "bg-amber-500/[0.06]" : "";
  return (
    <li data-testid="monitor" className={`flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 sm:px-5 ${tint}`}>
      <div className="min-w-0 flex-1 basis-48">
        <p className="break-words text-sm font-medium">{monitor.name}</p>
        {target && <p className="break-all text-xs text-white/50">{target}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <CertInfo days={monitor.cert_days_left} valid={monitor.cert_valid} />
        {monitor.uptime_24h != null && (
          <span className="text-xs text-white/60" title="Verfügbarkeit der letzten 24 Stunden">{percent(monitor.uptime_24h)} (24 Std.)</span>
        )}
        {monitor.response_ms != null && <span className="text-xs text-white/60">{monitor.response_ms.toLocaleString("de-DE")} ms</span>}
        <Badge tone={status.tone}>{status.label}</Badge>
      </div>
    </li>
  );
}

function MonitorList({ monitors }: { monitors: Monitor[] }) {
  const [search, setSearch] = useState("");
  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return monitors;
    return monitors.filter((m) => m.name.toLowerCase().includes(q) || (m.url ?? "").toLowerCase().includes(q));
  }, [monitors, search]);
  return (
    <div>
      {monitors.length >= SEARCH_FROM && (
        <div className="mb-3 flex">
          <SearchInput value={search} onChange={setSearch} placeholder="Monitor suchen" label="Monitore durchsuchen" />
        </div>
      )}
      {shown.length === 0 ? (
        <p className="py-3 text-center text-sm text-white/45">Kein Monitor passt zur Suche.</p>
      ) : (
        <ul className="-mx-5 divide-y divide-white/[0.06] border-t border-white/[0.06]">
          {shown.map((m, i) => <MonitorRow key={`${m.name}-${i}`} monitor={m} />)}
        </ul>
      )}
    </div>
  );
}

function Outages({ outages, state }: { outages: Outage[]; state: string }) {
  const zone = useDeckTimezone();
  if (outages.length === 0) {
    // Entwarnung nur, wenn die Status-Seite wirklich gelesen wurde.
    return state === "ok" ? <p className="text-sm text-white/50">In den letzten Messungen gab es keinen Ausfall.</p> : null;
  }
  return (
    <ul data-testid="outages" className="divide-y divide-white/[0.06]">
      {outages.map((o, i) => (
        <li key={`${o.monitor}-${o.time}-${i}`} className="py-2 first:pt-0 last:pb-0">
          <p className="break-words text-sm font-medium">{o.monitor}</p>
          <p className="break-words text-xs text-white/50">{formatTime(o.time, zone)}{o.message ? ` · ${o.message}` : ""}</p>
        </li>
      ))}
    </ul>
  );
}

function headline(connection: Connection): { label: string; tone: Tone } {
  if (connection.state === "not_configured") return { label: "nicht eingerichtet", tone: "neutral" };
  if (connection.state !== "ok" && !connection.summary) return PROBLEM[connection.state as keyof typeof PROBLEM] ?? PROBLEM.error;
  const s = connection.summary;
  if (s && s.down > 0) return { label: `${s.down} ausgefallen`, tone: "bad" };
  if (connection.state !== "ok") return PROBLEM[connection.state as keyof typeof PROBLEM] ?? PROBLEM.error;
  if (s && s.total === 0) return { label: "keine Monitore", tone: "neutral" };
  if (s && s.up < s.total) return { label: `${s.up} von ${s.total} laufen`, tone: s.pending > 0 ? "warn" : "neutral" };
  return { label: "alles in Ordnung", tone: "good" };
}

function ConnectionCard({ connection, onRetry }: { connection: Connection; onRetry: () => void }) {
  const head = headline(connection);
  const s = connection.summary;
  const problem = connection.state !== "ok" && connection.state !== "not_configured";
  const canManage = deck().hasPermission("extensions.manage");
  const openHref = safeHref(connection.url);
  return (
    <Card
      title={connection.name}
      description={openHref ? <a href={openHref} target="_blank" rel="noreferrer" className="break-all hover:text-white hover:underline">Uptime Kuma öffnen</a> : undefined}
      actions={<Badge tone={head.tone}>{head.label}</Badge>}
    >
      <div data-testid={`connection-${connection.name}`} className="space-y-4">
        {connection.state === "not_configured" && (
          <div data-testid="not-configured" className="flex flex-col items-start gap-3">
            <p className="text-sm text-white/60">{connection.message}</p>
            {canManage
              ? <a href={SETTINGS_PATH} className={buttonClass("primary", true)}>Jetzt einrichten</a>
              : <p className="text-xs text-white/45">Ein Administrator kann das unter Einstellungen → Erweiterungen → Uptime Kuma einrichten.</p>}
          </div>
        )}
        {problem && (
          <div data-testid="problem" role="alert" className="flex flex-col items-start gap-3">
            <p className="break-words text-sm text-red-300">{connection.message}</p>
            <div className="flex flex-wrap items-center gap-2">
              <Button small onClick={onRetry}>Erneut versuchen</Button>
              {canManage && <a href={SETTINGS_PATH} className={buttonClass("ghost", true)}>Einstellungen</a>}
            </div>
            {s && connection.monitors.length > 0 && <p className="text-xs text-white/45">Angezeigt wird, was trotzdem zu lesen war.</p>}
          </div>
        )}
        {s && (
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Laufen" value={s.up} tone={s.total > 0 && s.up === s.total ? "good" : undefined} />
            <Stat label="Ausgefallen" value={s.down} tone={s.down > 0 ? "bad" : undefined} />
            <Stat label="Ausstehend" value={s.pending} tone={s.pending > 0 ? "warn" : undefined} />
            <Stat label="In Wartung" value={s.maintenance} />
          </div>
        )}
        {connection.monitors.length > 0 && <MonitorList monitors={connection.monitors} />}
        {connection.state === "ok" && s && s.total === 0 && (
          <p className="text-sm text-white/50">In Uptime Kuma ist noch kein Monitor angelegt.</p>
        )}
        {connection.state === "ok" && !connection.status_page && connection.monitors.length > 0 && (
          <p data-testid="status-page-hint" className="text-xs text-white/45">
            Für die Verfügbarkeit der letzten 24 Stunden und die letzten Ausfälle trage unter Einstellungen → Erweiterungen → Uptime Kuma den Kurznamen einer Status-Seite ein.
          </p>
        )}
        {connection.status_page && (connection.outages.length > 0 || connection.state === "ok") && (
          <div className="border-t border-white/[0.06] pt-3">
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-white/50">Letzte Ausfälle</h4>
            <Outages outages={connection.outages} state={connection.state} />
          </div>
        )}
      </div>
    </Card>
  );
}

export function UptimeKumaPage(): JSX.Element {
  const unmountSignal = useUnmountSignal();
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);

  const load = useCallback((refresh: boolean) => {
    const mine = ++seq.current;
    setLoading(true);
    fetchJson<Overview>(`${API}/overview${refresh ? "?refresh=true" : ""}`, { signal: unmountSignal() })
      .then((body) => {
        if (mine !== seq.current) return;
        setData(body);
        setError(null);
      })
      .catch((err: unknown) => {
        if (mine !== seq.current || (err instanceof DOMException && err.name === "AbortError")) return;
        setError(errorMessage(err));
      })
      .finally(() => { if (mine === seq.current) setLoading(false); });
  }, [unmountSignal]);

  useEffect(() => { load(false); }, [load]);
  const reload = useCallback(() => load(true), [load]);

  const connections = data?.connections ?? [];
  return (
    <Page
      title="Uptime Kuma"
      description="Zeigt, welche Dienste laut Uptime Kuma laufen und welche ausgefallen sind."
      actions={
        <>
          {deck().hasPermission("extensions.manage") && <a href={SETTINGS_PATH} className={buttonClass("ghost")}>Einstellungen</a>}
          <Button onClick={reload} disabled={loading}>Aktualisieren</Button>
        </>
      }
    >
      {error && <LoadError what="Der Zustand von Uptime Kuma" error={error} onRetry={reload} />}
      {!data && !error && <Card><p className="text-sm text-white/50">Lade …</p></Card>}
      {data && connections.length === 0 && (
        <EmptyState
          icon="clock"
          title="Noch keine Verbindung"
          text={
            deck().hasPermission("extensions.manage")
              ? "Trage Uptime Kuma unter Einstellungen → Erweiterungen → Uptime Kuma ein."
              : "Ein Administrator kann Uptime Kuma unter Einstellungen → Erweiterungen → Uptime Kuma eintragen."
          }
          action={<SettingsLink to={SETTINGS_PATH} permission="extensions.manage">Jetzt einrichten</SettingsLink>}
        />
      )}
      {connections.length > 0 && (
        <div className="space-y-6">
          {connections.map((c, i) => <ConnectionCard key={`${c.name}-${i}`} connection={c} onRetry={reload} />)}
        </div>
      )}
    </Page>
  );
}
