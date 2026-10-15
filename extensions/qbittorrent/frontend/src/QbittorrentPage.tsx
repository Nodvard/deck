/** qBittorrent-Seite (PageSpec `component="QbittorrentPage"`): je Verbindung eine Karte, nur lesend. */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { errorMessage, fetchJson, LoadError } from "../../../_shared/frontend/src/loadError";
import { SettingsLink } from "../../../_shared/frontend/src/links";
import { Badge, Button, Card, EmptyState, Page, Stat, buttonClass, type Tone } from "../../../_shared/frontend/src/ui";
import { deck } from "../../../_shared/frontend/src/deck";

const API = "/ext/qbittorrent";
const SETTINGS_PATH = "/settings/extensions/qbittorrent";

export type ConnectionState = "ok" | "not_configured" | "auth_failed" | "unreachable" | "error";

export interface Transfer {
  download_speed: number;
  upload_speed: number;
  connection: "connected" | "firewalled" | "disconnected" | "unknown" | string;
  download_limit: number | null;
  upload_limit: number | null;
  limited: boolean;
  alt_limits: boolean | null;
}

export interface Torrent {
  name: string;
  state: string;
  progress_percent: number;
  size: number | null;
  download_speed: number;
  upload_speed: number;
  eta_seconds: number | null;
}

export interface TorrentCounts {
  total: number;
  downloading: number;
  seeding: number;
  paused: number;
  error: number;
  other: number;
}

export interface Connection {
  name: string;
  url: string | null;
  state: ConnectionState | string;
  message: string | null;
  version: string | null;
  api_version: string | null;
  transfer: Transfer | null;
  active: Torrent[] | null;
  active_more: boolean;
  counts: TorrentCounts | null;
  free_space: number | null;
  notes: string[];
  problems: string[];
}

export interface Overview {
  connections: Connection[];
  summary: { connections: number; download_speed: number; upload_speed: number; active: number };
}

const PROBLEM: Record<Exclude<ConnectionState, "ok" | "not_configured">, { label: string; tone: Tone }> = {
  auth_failed: { label: "Anmeldung fehlgeschlagen", tone: "bad" },
  unreachable: { label: "nicht erreichbar", tone: "bad" },
  error: { label: "Fehler", tone: "warn" },
};

const STATE_LABEL: Record<string, { label: string; tone: Tone }> = {
  downloading: { label: "lädt", tone: "good" },
  stalled_downloading: { label: "wartet auf Quellen", tone: "neutral" },
  uploading: { label: "verteilt", tone: "good" },
  stalled_uploading: { label: "verteilt", tone: "neutral" },
  paused: { label: "pausiert", tone: "neutral" },
  queued: { label: "wartet", tone: "neutral" },
  checking: { label: "prüft", tone: "neutral" },
  moving: { label: "verschiebt", tone: "neutral" },
  error: { label: "Fehler", tone: "bad" },
};

const CONNECTION_LABEL: Record<string, { label: string; tone: Tone }> = {
  connected: { label: "verbunden", tone: "good" },
  firewalled: { label: "hinter Firewall", tone: "warn" },
  disconnected: { label: "getrennt", tone: "bad" },
};

const UNITS = ["B", "KB", "MB", "GB", "TB"];

/** "3,2 MB": 1024er-Schritte, unter 10 eine Nachkommastelle (mit Komma), Byte ohne; wie in der Kachel. */
export function sizeText(count: number): string {
  let value = count;
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit > 0 && value < 10 ? 1 : 0;
  const rounded = Math.floor(value * 10 ** digits + 0.5) / 10 ** digits; // kaufmännisch, wie die Kachel
  return `${rounded.toFixed(digits).replace(".", ",")} ${UNITS[unit]}`;
}

export function speedText(count: number): string {
  return `${sizeText(count)}/s`;
}

/** "5 Min.", "1 Std. 12 Min." oder "2 Tage 3 Std."; unter einer Minute "< 1 Min.". */
export function durationText(seconds: number): string {
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

/** Zeile unter dem Namen: "42 % · ↓ 3,0 MB/s · ↑ 256 KB/s · noch 20 Min. · 6,0 GB". */
export function torrentDetail(torrent: Torrent): string {
  const parts = [`${torrent.progress_percent} %`];
  if (torrent.download_speed) parts.push(`↓ ${speedText(torrent.download_speed)}`);
  if (torrent.upload_speed) parts.push(`↑ ${speedText(torrent.upload_speed)}`);
  if (torrent.state === "downloading" && torrent.eta_seconds !== null) parts.push(`noch ${durationText(torrent.eta_seconds)}`);
  if (torrent.size !== null) parts.push(sizeText(torrent.size));
  return parts.join(" · ");
}

function headline(connection: Connection): { label: string; tone: Tone } {
  if (connection.state === "not_configured") return { label: "nicht eingerichtet", tone: "neutral" };
  if (connection.state !== "ok") return PROBLEM[connection.state as keyof typeof PROBLEM] ?? PROBLEM.error;
  const transfer = connection.transfer;
  const errors = connection.counts?.error ?? 0;
  if (connection.problems.length > 0) return { label: "unvollständig", tone: "warn" };
  if (transfer?.connection === "disconnected") return { label: "getrennt", tone: "bad" };
  if (errors > 0) return { label: `${errors} Fehler`, tone: "warn" };
  if (transfer && (transfer.download_speed > 0 || transfer.upload_speed > 0)) return { label: "aktiv", tone: "good" };
  return { label: "ruhig", tone: "neutral" };
}

function limitText(transfer: Transfer): string {
  const parts = [];
  if (transfer.download_limit !== null) parts.push(`Download bis ${speedText(transfer.download_limit)}`);
  if (transfer.upload_limit !== null) parts.push(`Upload bis ${speedText(transfer.upload_limit)}`);
  return `${transfer.alt_limits ? "Alternative Tempolimits an" : "Tempolimit"}: ${parts.join(", ") || "an"}`;
}

function TorrentRow({ torrent }: { torrent: Torrent }) {
  const state = STATE_LABEL[torrent.state];
  return (
    <li data-testid="torrent" className="space-y-2 px-4 py-3 sm:px-5">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
        <div className="min-w-0 flex-1 basis-48">
          <p className="break-words text-sm font-medium">{torrent.name}</p>
          <p className="break-words text-xs text-white/60">{torrentDetail(torrent)}</p>
        </div>
        {state && <Badge tone={state.tone}>{state.label}</Badge>}
      </div>
      <div
        role="progressbar" aria-label={`Fortschritt von ${torrent.name}`} aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={torrent.progress_percent} className="h-1 w-full overflow-hidden rounded bg-white/10"
      >
        <div className="h-full rounded bg-[var(--color-accent)]" style={{ width: `${torrent.progress_percent}%` }} />
      </div>
    </li>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="border-t border-white/[0.06] pt-3">
      <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-white/50">{title}</h4>
      {children}
    </div>
  );
}

function ConnectionCard({ connection, onRetry }: { connection: Connection; onRetry: () => void }) {
  const head = headline(connection);
  const problem = connection.state !== "ok" && connection.state !== "not_configured";
  const canManage = deck().hasPermission("extensions.manage");
  const facts = [
    connection.version ? `Version ${connection.version}` : null,
    connection.api_version ? `Web-API ${connection.api_version}` : null,
  ].filter(Boolean);
  const ok = connection.state === "ok";
  const transfer = connection.transfer;
  const counts = connection.counts;
  const active = connection.active;
  const link = transfer ? CONNECTION_LABEL[transfer.connection] : undefined;
  return (
    <Card
      title={connection.name}
      description={connection.url ? <a href={connection.url} target="_blank" rel="noreferrer" className="break-all hover:text-white hover:underline">qBittorrent öffnen</a> : undefined}
      actions={<Badge tone={head.tone}>{head.label}</Badge>}
    >
      <div data-testid={`connection-${connection.name}`} className="space-y-4">
        {facts.length > 0 && <p data-testid="facts" className="break-words text-sm text-white/70">{facts.join(" · ")}</p>}
        {connection.state === "not_configured" && (
          <div data-testid="not-configured" className="flex flex-col items-start gap-3">
            <p className="break-words text-sm text-white/60">{connection.message}</p>
            {canManage
              ? <a href={SETTINGS_PATH} className={buttonClass("primary", true)}>Jetzt einrichten</a>
              : <p className="text-xs text-white/45">Ein Administrator kann das unter Einstellungen → Erweiterungen → qBittorrent einrichten.</p>}
          </div>
        )}
        {problem && (
          <div data-testid="problem" role="alert" className="flex flex-col items-start gap-3">
            <p className="break-words text-sm text-red-300">{connection.message}</p>
            <div className="flex flex-wrap items-center gap-2">
              {/* Nach einer abgelehnten Anmeldung fragt das Dashboard erst nach einer Pause wieder. */}
              {connection.state !== "auth_failed" && <Button small onClick={onRetry}>Erneut versuchen</Button>}
              {canManage && <a href={SETTINGS_PATH} className={buttonClass("ghost", true)}>Einstellungen</a>}
            </div>
          </div>
        )}
        {ok && connection.problems.length > 0 && (
          <ul data-testid="problems" role="alert" className="space-y-1 text-sm text-amber-300">
            {connection.problems.map((text) => <li key={text} className="break-words">{text}</li>)}
          </ul>
        )}
        {ok && connection.notes.length > 0 && (
          <ul data-testid="notes" className="space-y-1 text-sm text-white/60">
            {connection.notes.map((text) => <li key={text} className="break-words">{text}</li>)}
          </ul>
        )}
        {ok && (transfer || connection.free_space !== null) && (
          <div data-testid="transfer" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {transfer && <Stat label="Herunterladen" value={speedText(transfer.download_speed)} />}
            {transfer && <Stat label="Hochladen" value={speedText(transfer.upload_speed)} />}
            {transfer && <Stat label="Verbindung" value={link?.label ?? "unbekannt"} tone={link?.tone} />}
            <Stat label="Freier Platz" value={connection.free_space !== null ? sizeText(connection.free_space) : "unbekannt"} />
          </div>
        )}
        {ok && transfer?.limited && <p data-testid="limit" className="text-sm text-amber-300">{limitText(transfer)}</p>}
        {ok && counts && (
          <Section title={`Torrents (${counts.total.toLocaleString("de-DE")})`}>
            {counts.total === 0 ? (
              <p data-testid="no-torrents" className="text-sm text-white/50">Noch keine Torrents.</p>
            ) : (
              <div data-testid="counts" className="grid grid-cols-2 gap-3 lg:grid-cols-5">
                <Stat label="Laden" value={counts.downloading.toLocaleString("de-DE")} />
                <Stat label="Verteilen" value={counts.seeding.toLocaleString("de-DE")} />
                <Stat label="Pausiert" value={counts.paused.toLocaleString("de-DE")} />
                <Stat label="Fehler" value={counts.error.toLocaleString("de-DE")} tone={counts.error > 0 ? "warn" : undefined} />
                <Stat label="Warten oder prüfen" value={counts.other.toLocaleString("de-DE")} />
              </div>
            )}
          </Section>
        )}
        {ok && active && (
          <Section title={`Aktiv (${active.length}${connection.active_more ? "+" : ""})`}>
            {active.length === 0 ? (
              <p data-testid="no-active" className="text-sm text-white/50">Gerade bewegt sich nichts.</p>
            ) : (
              <>
                <ul data-testid="active" className="-mx-5 divide-y divide-white/[0.06] border-t border-white/[0.06]">
                  {active.map((t, i) => <TorrentRow key={`${i}-${t.name}`} torrent={t} />)}
                </ul>
                {connection.active_more && <p className="mt-2 text-xs text-white/45">Es sind noch mehr aktiv (hier werden die ersten {active.length} gezeigt).</p>}
              </>
            )}
          </Section>
        )}
      </div>
    </Card>
  );
}

export function QbittorrentPage(): JSX.Element {
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
      title="qBittorrent"
      description="Zeigt Geschwindigkeit, aktive Torrents, die Zahl je Zustand und den freien Platz. Nur lesend."
      actions={
        <>
          {deck().hasPermission("extensions.manage") && <a href={SETTINGS_PATH} className={buttonClass("ghost")}>Einstellungen</a>}
          <Button onClick={reload} disabled={loading}>Aktualisieren</Button>
        </>
      }
    >
      {error && <LoadError what="Der Zustand von qBittorrent" error={error} onRetry={reload} />}
      {!data && !error && <Card><p className="text-sm text-white/50">Lade …</p></Card>}
      {data && connections.length === 0 && (
        <EmptyState
          icon="clock"
          title="Noch keine Verbindung"
          text={
            deck().hasPermission("extensions.manage")
              ? "Trage qBittorrent unter Einstellungen → Erweiterungen → qBittorrent ein."
              : "Ein Administrator kann qBittorrent unter Einstellungen → Erweiterungen → qBittorrent eintragen."
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
