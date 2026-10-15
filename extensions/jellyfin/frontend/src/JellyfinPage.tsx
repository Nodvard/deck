/** Jellyfin-Seite (PageSpec `component="JellyfinPage"`): je Verbindung eine Karte, nur lesend. */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { errorMessage, fetchJson, LoadError } from "../../../_shared/frontend/src/loadError";
import { SettingsLink } from "../../../_shared/frontend/src/links";
import { useDeckTimezone } from "../../../_shared/frontend/src/timezone";
import { Badge, Button, Card, EmptyState, Page, Stat, buttonClass, type Tone } from "../../../_shared/frontend/src/ui";
import { deck } from "../../../_shared/frontend/src/deck";

const API = "/ext/jellyfin";
const SETTINGS_PATH = "/settings/extensions/jellyfin";

export type ConnectionState = "ok" | "not_configured" | "auth_failed" | "unreachable" | "error";

export interface Media {
  title: string;
  kind: string;
  series: string | null;
  season: number | null;
  episode: number | null;
  artist: string | null;
  year: number | null;
}

export interface Playback extends Media {
  user: string | null;
  device: string | null;
  client: string | null;
  play_method: string | null;
  transcoding: boolean;
  progress_percent: number | null;
  paused: boolean;
}

export interface LatestItem extends Media {
  added_at: string | null;
}

export interface LibraryCounts {
  movies: number | null;
  series: number | null;
  episodes: number | null;
  albums: number | null;
  songs: number | null;
  music_videos: number | null;
  box_sets: number | null;
  books: number | null;
}

export interface Connection {
  name: string;
  url: string | null;
  state: ConnectionState | string;
  message: string | null;
  server_name: string | null;
  version: string | null;
  playback_count: number | null;
  playbacks: Playback[] | null;
  counts: LibraryCounts | null;
  latest: LatestItem[] | null;
  problems: string[];
}

export interface Overview {
  connections: Connection[];
  summary: { connections: number; playing: number; transcoding: number };
}

const PROBLEM: Record<Exclude<ConnectionState, "ok" | "not_configured">, { label: string; tone: Tone }> = {
  auth_failed: { label: "Anmeldung fehlgeschlagen", tone: "bad" },
  unreachable: { label: "nicht erreichbar", tone: "bad" },
  error: { label: "Fehler", tone: "warn" },
};

const COUNT_LABELS: [keyof LibraryCounts, string][] = [
  ["movies", "Filme"], ["series", "Serien"], ["episodes", "Folgen"], ["albums", "Alben"],
  ["songs", "Titel"], ["music_videos", "Musikvideos"], ["box_sets", "Sammlungen"], ["books", "Bücher"],
];

const METHOD_LABEL: Record<string, string> = { direct_play: "direkt", direct_stream: "Direktstream", transcode: "transkodiert" };

/** 1296 wird "1.296", wie in der Kachel. */
export function numberText(count: number): string {
  return count.toLocaleString("de-DE");
}

export function episodeCode(season: number | null, episode: number | null): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  if (season !== null && episode !== null) return `S${pad(season)}E${pad(episode)}`;
  if (season !== null) return `S${pad(season)}`;
  return episode !== null ? `E${pad(episode)}` : "";
}

/** Zeile unter dem Titel: "Serie S02E05", Künstler oder Jahr. */
export function mediaDetail(media: Media): string {
  if (media.kind === "episode") return [media.series, episodeCode(media.season, media.episode)].filter(Boolean).join(" ");
  if (media.kind === "audio") return media.artist ?? "";
  return media.year !== null ? String(media.year) : "";
}

function headline(connection: Connection): { label: string; tone: Tone } {
  if (connection.state === "not_configured") return { label: "nicht eingerichtet", tone: "neutral" };
  if (connection.state !== "ok") return PROBLEM[connection.state as keyof typeof PROBLEM] ?? PROBLEM.error;
  if (connection.problems.length > 0) return { label: "unvollständig", tone: "warn" };
  const playing = connection.playback_count ?? 0;
  if (playing > 0) return { label: playing === 1 ? "1 läuft" : `${playing} laufen`, tone: "good" };
  return { label: "nichts läuft", tone: "neutral" };
}

function addedText(iso: string | null, zone: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const options: Intl.DateTimeFormatOptions = { day: "2-digit", month: "2-digit", year: "numeric" };
  try {
    return date.toLocaleDateString("de-DE", zone ? { ...options, timeZone: zone } : options);
  } catch {
    return date.toLocaleDateString("de-DE", options);
  }
}

function PlaybackRow({ playback }: { playback: Playback }) {
  const detail = mediaDetail(playback);
  const where = playback.device ?? playback.client;
  const who = [playback.user ?? "Unbekannt", where].filter(Boolean).join(" · ");
  const method = playback.play_method ? METHOD_LABEL[playback.play_method] : null;
  return (
    <li data-testid="playback" className="space-y-2 px-4 py-3 sm:px-5">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
        <div className="min-w-0 flex-1 basis-48">
          <p className="break-words text-sm font-medium">{playback.title}</p>
          {detail && <p className="break-words text-xs text-white/60">{detail}</p>}
          <p className="break-words text-xs text-white/50">{who}</p>
        </div>
        <div className="flex flex-none flex-wrap items-center justify-end gap-1.5">
          {playback.paused && <Badge tone="neutral">pausiert</Badge>}
          {method && <Badge tone={playback.transcoding ? "warn" : "good"}>{method}</Badge>}
          {playback.progress_percent !== null && <span className="text-xs tabular-nums text-white/70">{playback.progress_percent} %</span>}
        </div>
      </div>
      {playback.progress_percent !== null && (
        <div
          role="progressbar" aria-label={`Fortschritt von ${playback.title}`} aria-valuemin={0} aria-valuemax={100}
          aria-valuenow={playback.progress_percent} className="h-1 w-full overflow-hidden rounded bg-white/10"
        >
          <div className="h-full rounded bg-[var(--color-accent)]" style={{ width: `${playback.progress_percent}%` }} />
        </div>
      )}
    </li>
  );
}

function LatestRow({ item, zone }: { item: LatestItem; zone: string | null }) {
  const detail = mediaDetail(item);
  const added = addedText(item.added_at, zone);
  return (
    <li data-testid="latest-item" className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-3 sm:px-5">
      <div className="min-w-0 flex-1 basis-48">
        <p className="break-words text-sm font-medium">{item.title}</p>
        {detail && <p className="break-words text-xs text-white/50">{detail}</p>}
      </div>
      {added && <span className="flex-none text-xs tabular-nums text-white/60">{added}</span>}
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

function ConnectionCard({ connection, zone, onRetry }: { connection: Connection; zone: string | null; onRetry: () => void }) {
  const head = headline(connection);
  const problem = connection.state !== "ok" && connection.state !== "not_configured";
  const canManage = deck().hasPermission("extensions.manage");
  const facts = [
    connection.server_name ? `Server ${connection.server_name}` : null,
    connection.version ? `Version ${connection.version}` : null,
  ].filter(Boolean);
  const playbacks = connection.playbacks;
  const hiddenPlaybacks = playbacks && connection.playback_count !== null ? connection.playback_count - playbacks.length : 0;
  const counts = connection.counts;
  const shownCounts = counts ? COUNT_LABELS.filter(([key]) => (counts[key] ?? 0) > 0) : [];
  const ok = connection.state === "ok";
  return (
    <Card
      title={connection.name}
      description={connection.url ? <a href={connection.url} target="_blank" rel="noreferrer" className="break-all hover:text-white hover:underline">Jellyfin öffnen</a> : undefined}
      actions={<Badge tone={head.tone}>{head.label}</Badge>}
    >
      <div data-testid={`connection-${connection.name}`} className="space-y-4">
        {facts.length > 0 && <p data-testid="facts" className="break-words text-sm text-white/70">{facts.join(" · ")}</p>}
        {connection.state === "not_configured" && (
          <div data-testid="not-configured" className="flex flex-col items-start gap-3">
            <p className="text-sm text-white/60">{connection.message}</p>
            {canManage
              ? <a href={SETTINGS_PATH} className={buttonClass("primary", true)}>Jetzt einrichten</a>
              : <p className="text-xs text-white/45">Ein Administrator kann das unter Einstellungen → Erweiterungen → Jellyfin einrichten.</p>}
          </div>
        )}
        {problem && (
          <div data-testid="problem" role="alert" className="flex flex-col items-start gap-3">
            <p className="break-words text-sm text-red-300">{connection.message}</p>
            <div className="flex flex-wrap items-center gap-2">
              {/* Nach einem abgelehnten Schlüssel fragt das Dashboard erst nach einer Pause wieder. */}
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
        {ok && playbacks && (
          <Section title={`Läuft gerade (${connection.playback_count ?? playbacks.length})`}>
            {playbacks.length === 0 ? (
              <p data-testid="no-playbacks" className="text-sm text-white/50">Gerade läuft nichts.</p>
            ) : (
              <>
                <ul data-testid="playbacks" className="-mx-5 divide-y divide-white/[0.06] border-t border-white/[0.06]">
                  {playbacks.map((p, i) => <PlaybackRow key={`${i}-${p.user}-${p.title}`} playback={p} />)}
                </ul>
                {hiddenPlaybacks > 0 && <p className="mt-2 text-xs text-white/45">… und {hiddenPlaybacks} weitere (hier werden die ersten {playbacks.length} gezeigt).</p>}
              </>
            )}
          </Section>
        )}
        {ok && counts && (
          <Section title="Bibliotheken">
            {shownCounts.length === 0 ? (
              <p data-testid="no-media" className="text-sm text-white/50">Noch keine Medien in den Bibliotheken.</p>
            ) : (
              <div data-testid="counts" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                {shownCounts.map(([key, label]) => <Stat key={key} label={label} value={numberText(counts[key] ?? 0)} />)}
              </div>
            )}
          </Section>
        )}
        {ok && connection.latest && (
          <Section title="Zuletzt hinzugefügt">
            {connection.latest.length === 0 ? (
              <p data-testid="no-latest" className="text-sm text-white/50">Noch nichts hinzugefügt.</p>
            ) : (
              <ul data-testid="latest" className="-mx-5 divide-y divide-white/[0.06] border-t border-white/[0.06]">
                {connection.latest.map((item, i) => <LatestRow key={`${i}-${item.title}`} item={item} zone={zone} />)}
              </ul>
            )}
          </Section>
        )}
      </div>
    </Card>
  );
}

export function JellyfinPage(): JSX.Element {
  const unmountSignal = useUnmountSignal();
  const zone = useDeckTimezone();
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
      title="Jellyfin"
      description="Zeigt, was gerade läuft, wie groß die Bibliotheken sind und was zuletzt hinzugekommen ist. Nur lesend."
      actions={
        <>
          {deck().hasPermission("extensions.manage") && <a href={SETTINGS_PATH} className={buttonClass("ghost")}>Einstellungen</a>}
          <Button onClick={reload} disabled={loading}>Aktualisieren</Button>
        </>
      }
    >
      {error && <LoadError what="Der Zustand von Jellyfin" error={error} onRetry={reload} />}
      {!data && !error && <Card><p className="text-sm text-white/50">Lade …</p></Card>}
      {data && connections.length === 0 && (
        <EmptyState
          icon="clock"
          title="Noch keine Verbindung"
          text={
            deck().hasPermission("extensions.manage")
              ? "Trage Jellyfin unter Einstellungen → Erweiterungen → Jellyfin ein."
              : "Ein Administrator kann Jellyfin unter Einstellungen → Erweiterungen → Jellyfin eintragen."
          }
          action={<SettingsLink to={SETTINGS_PATH} permission="extensions.manage">Jetzt einrichten</SettingsLink>}
        />
      )}
      {connections.length > 0 && (
        <div className="space-y-6">
          {connections.map((c, i) => <ConnectionCard key={`${c.name}-${i}`} connection={c} zone={zone} onRetry={reload} />)}
        </div>
      )}
    </Page>
  );
}
