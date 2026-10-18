/** Synology-Seite (PageSpec `component="SynologyPage"`): Zustand eines DSM mit Modell, Auslastung, Speicherpools, Volumes, Festplatten und Hyper Backup, nur lesend. */
import { useCallback, useEffect, useRef, useState } from "react";

import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { errorMessage, fetchJson, LoadError } from "../../../_shared/frontend/src/loadError";
import { SettingsLink, safeHref } from "../../../_shared/frontend/src/links";
import { Badge, Button, Card, EmptyState, Page, Stat, buttonClass, type Tone } from "../../../_shared/frontend/src/ui";
import { deck } from "../../../_shared/frontend/src/deck";

const API = "/ext/synology";
const SETTINGS_PATH = "/settings/extensions/synology";
const FULL_PERCENT = 90; // über dieser Belegung (bzw. Auslastung) wird es gelb

export type OverviewState = "ok" | "not_configured" | "auth_failed" | "unreachable" | "error";

export interface Note {
  kind: "error" | "no_rights" | "unavailable" | "partial" | string;
  message: string;
}

export interface SystemInfo {
  model: string | null;
  dsm_version: string | null;
  uptime_seconds: number | null;
  temperature: number | null;
  temperature_warn: boolean | null;
  ram_mb: number | null;
  clock: string | null;
}

export interface Load {
  cpu_percent: number | null;
  ram_percent: number | null;
}

export interface Volume {
  id: string;
  name: string;
  status: string;
  status_label: string;
  tone: "good" | "warn" | "danger" | "neutral" | string;
  fs_type: string | null;
  used: number | null;
  total: number | null;
  percent: number | null;
  pool_id: string | null;
}

export interface Pool {
  id: string;
  name: string;
  description: string | null;
  raid: string | null;
  status: string;
  status_label: string;
  tone: "good" | "warn" | "danger" | "neutral" | string;
  disks: number | null;
  volumes: string[];
  total: number | null;
}

export interface Disk {
  id: string;
  name: string;
  model: string | null;
  kind: "hdd" | "ssd" | "nvme" | string;
  status: string;
  status_label: string;
  tone: "good" | "warn" | "danger" | "neutral" | string;
  temp: number | null;
  temp_warn: boolean;
  smart_status: string | null;
  smart_label: string | null;
  smart_tone: "good" | "warn" | "danger" | "neutral" | string;
  warning: string | null;
}

export interface Update {
  available: boolean;
  version: string | null;
}

/** Zeiten sind ISO ohne Zone in Ortszeit der Synology, z. B. "2026-10-17T03:00". */
export interface BackupTask {
  id: string;
  name: string;
  state: string | null;
  running: boolean;
  status_read?: boolean;
  last_result: string | null;
  result_label: string;
  tone: "good" | "warn" | "danger" | "neutral" | string;
  last_run: string | null;
  last_success: string | null;
  next_run: string | null;
  overdue: boolean;
  problem: string | null;
}

export interface Problem {
  key: string;
  subject: string;
  severity: "info" | "warning" | "critical" | string;
  title: string;
  text: string;
}

export interface Overview {
  state: OverviewState | string;
  message: string | null;
  url: string | null;
  system: SystemInfo | null;
  load: Load | null;
  volumes: Volume[] | null;
  disks: Disk[] | null;
  pools: Pool[] | null;
  update: Update | null;
  backups: BackupTask[] | null;
  system_note: Note | null;
  load_note: Note | null;
  storage_note: Note | null;
  update_note: Note | null;
  backup_note: Note | null;
  problems: Problem[];
}

const PROBLEM: Record<string, { label: string; tone: Tone }> = {
  auth_failed: { label: "Anmeldung fehlgeschlagen", tone: "bad" },
  unreachable: { label: "nicht erreichbar", tone: "bad" },
  error: { label: "Fehler", tone: "warn" },
};

const UNITS = ["B", "KB", "MB", "GB", "TB", "PB"];

/** Die Töne des Servers (good/warn/danger/neutral) als Ton des UI-Kits. */
export function toneOf(tone: string | null | undefined): Tone {
  if (tone === "good") return "good";
  if (tone === "warn") return "warn";
  if (tone === "danger") return "bad";
  return "neutral";
}

/** "3,6 TB": 1024er-Schritte, unter 10 eine Nachkommastelle (mit Komma), Byte ohne; wie in der Kachel. */
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

export function percentText(value: number): string {
  return `${Math.floor(value + 0.5)} %`;
}

/** "412 GB von 3,6 TB (11 %)"; ohne Größen "unbekannt". */
export function usageText(volume: Volume): string {
  if (volume.used === null || !volume.total) return "unbekannt";
  const percent = volume.percent !== null ? ` (${volume.percent} %)` : "";
  return `${sizeText(volume.used)} von ${sizeText(volume.total)}${percent}`;
}

const DSM_TIME = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/;

/** "2026-10-17T03:00" als "17.10.2026, 03:00"; die Zeit ist Ortszeit der Synology und wird nicht in die des Browsers umgerechnet. */
export function dsmTime(value: string | null): string | null {
  if (!value) return null;
  const m = DSM_TIME.exec(value);
  return m ? `${m[3]}.${m[2]}.${m[1]}, ${m[4]}:${m[5]}` : value;
}

/** Nur kritische Meldungen und Warnungen kommen auf die Karte „Braucht Aufmerksamkeit“. */
function urgent(data: Overview): Problem[] {
  return (data.problems ?? []).filter((p) => p.severity === "critical" || p.severity === "warning");
}

function headline(data: Overview): { label: string; tone: Tone } {
  const tones = [...(data.volumes ?? []).map((v) => v.tone), ...(data.disks ?? []).map((d) => d.tone)];
  const severities = urgent(data).map((p) => p.severity);
  if (tones.includes("danger") || severities.includes("critical") || data.system?.temperature_warn === true) return { label: "Problem", tone: "bad" };
  if (tones.includes("warn") || severities.includes("warning")) return { label: "Hinweis", tone: "warn" };
  const unknown = [data.system_note, data.load_note, data.storage_note, data.update_note, data.backup_note].some((n) => n && n.kind !== "unavailable");
  if (unknown) return { label: "unvollständig", tone: "warn" };
  return { label: "in Ordnung", tone: "good" };
}

const TONE_TEXT: Record<string, string> = { warn: "text-amber-300", danger: "text-red-300" };

function PartNote({ note, testId }: { note: Note | null; testId: string }) {
  if (!note) return null;
  const color = note.kind === "unavailable" ? "text-white/50" : note.kind === "no_rights" || note.kind === "partial" ? "text-amber-300" : "text-red-300";
  return <p data-testid={testId} role={note.kind === "unavailable" ? undefined : "alert"} className={`break-words text-sm ${color}`}>{note.message}</p>;
}

function UsageBar({ volume }: { volume: Volume }) {
  if (volume.percent === null) return <span className="text-white/50">unbekannt</span>;
  const full = volume.percent > FULL_PERCENT;
  return (
    <div className="min-w-[10rem] space-y-1">
      <div
        role="progressbar" aria-label={`Belegung von ${volume.name}`} aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={volume.percent} className="h-1.5 w-full overflow-hidden rounded bg-white/10"
      >
        <div className={`h-full rounded ${full ? "bg-amber-400" : "bg-[var(--color-accent)]"}`} style={{ width: `${volume.percent}%` }} />
      </div>
      <p className={`text-xs ${full ? "text-amber-300" : "text-white/60"}`}>{usageText(volume)}</p>
    </div>
  );
}

function AttentionCard({ data }: { data: Overview }) {
  const items = urgent(data);
  if (items.length === 0) return null;
  return (
    <Card title="Braucht Aufmerksamkeit" padded={false}>
      <ul data-testid="attention" className="divide-y divide-white/[0.06]">
        {items.map((problem) => (
          <li key={problem.key} data-testid="attention-item" className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1 px-5 py-3">
            <div className="min-w-0 flex-1 basis-56">
              <p className="break-words text-sm font-semibold">{problem.subject}</p>
              <p className="break-words text-sm text-white/60">{problem.text}</p>
            </div>
            <Badge tone={problem.severity === "critical" ? "bad" : "warn"}>{problem.severity === "critical" ? "kritisch" : "Warnung"}</Badge>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function PoolsCard({ data }: { data: Overview }) {
  const pools = data.pools;
  if (!pools || pools.length === 0) return null;
  return (
    <Card title="Speicherpools" padded={false}>
      <div className="overflow-x-auto">
        <table data-testid="pools" className="w-full text-sm">
          <thead>
            <tr className="border-b border-white/10 text-left text-xs text-white/50">
              <th className="px-5 py-2 font-medium">Name</th>
              <th className="px-3 py-2 font-medium">RAID</th>
              <th className="px-3 py-2 font-medium">Zustand</th>
              <th className="px-3 py-2 font-medium">Festplatten</th>
              <th className="px-3 py-2 font-medium">Volumes</th>
              <th className="px-5 py-2 font-medium">Größe</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/[0.06]">
            {pools.map((pool) => (
              <tr key={pool.id} data-testid="pool">
                <td className="break-words px-5 py-2.5 align-top">
                  <span className="font-medium">{pool.name}</span>
                  {pool.description && <p className="text-xs text-white/45">{pool.description}</p>}
                </td>
                <td className="whitespace-nowrap px-3 py-2.5 align-top">{pool.raid ?? "–"}</td>
                <td className="px-3 py-2.5 align-top"><Badge tone={toneOf(pool.tone)}>{pool.status_label}</Badge></td>
                <td className="px-3 py-2.5 align-top tabular-nums">{pool.disks ?? "–"}</td>
                <td className="break-words px-3 py-2.5 align-top text-white/70">{pool.volumes.length > 0 ? pool.volumes.join(", ") : "–"}</td>
                <td className="whitespace-nowrap px-5 py-2.5 align-top tabular-nums">{pool.total !== null ? sizeText(pool.total) : "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function VolumesCard({ data }: { data: Overview }) {
  const volumes = data.volumes;
  return (
    <Card title="Volumes" padded={false}>
      {data.storage_note && <div className="px-5 py-4"><PartNote note={data.storage_note} testId="note-storage" /></div>}
      {volumes && volumes.length === 0 && <p data-testid="no-volumes" className="px-5 py-4 text-sm text-white/50">DSM meldet keine Volumes.</p>}
      {volumes && volumes.length > 0 && (
        <div className="overflow-x-auto">
          <table data-testid="volumes" className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/10 text-left text-xs text-white/50">
                <th className="px-5 py-2 font-medium">Name</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-5 py-2 font-medium">Belegung</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.06]">
              {volumes.map((volume) => (
                <tr key={volume.id} data-testid="volume">
                  <td className="break-words px-5 py-2.5 align-top">
                    <span className="font-medium">{volume.name}</span>
                    {volume.fs_type && <span className="ml-2 text-xs text-white/45">{volume.fs_type}</span>}
                  </td>
                  <td className="px-3 py-2.5 align-top"><Badge tone={toneOf(volume.tone)}>{volume.status_label}</Badge></td>
                  <td className="px-5 py-2.5 align-top"><UsageBar volume={volume} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function DisksCard({ data }: { data: Overview }) {
  const disks = data.disks;
  // Ließ sich der Speicher nicht lesen, steht der Grund schon bei den Volumes.
  if (!disks) return null;
  return (
    <Card title="Festplatten" padded={false}>
      {disks.length === 0 && <p data-testid="no-disks" className="px-5 py-4 text-sm text-white/50">DSM meldet keine Festplatten.</p>}
      {disks.length > 0 && (
        <div className="overflow-x-auto">
          <table data-testid="disks" className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/10 text-left text-xs text-white/50">
                <th className="px-5 py-2 font-medium">Name</th>
                <th className="px-3 py-2 font-medium">Modell</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="whitespace-nowrap px-3 py-2 font-medium">Temperatur</th>
                <th className="px-5 py-2 font-medium">SMART</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.06]">
              {disks.map((disk) => (
                <tr key={disk.id} data-testid="disk">
                  <td className="break-words px-5 py-2.5 align-top">
                    <span className="font-medium">{disk.name}</span>
                    {disk.warning && <p data-testid="disk-warning" className="text-xs text-amber-300">{disk.warning}</p>}
                  </td>
                  <td className="break-words px-3 py-2.5 align-top text-white/70">{disk.model ?? "–"}</td>
                  <td className="px-3 py-2.5 align-top"><Badge tone={toneOf(disk.tone)}>{disk.status_label}</Badge></td>
                  <td className="whitespace-nowrap px-3 py-2.5 align-top tabular-nums">
                    {disk.temp === null ? "–" : disk.temp_warn === true ? (
                      <span className="text-amber-300" title="über der Grenze">{`${disk.temp} °C`}<span className="sr-only"> (über der Grenze)</span></span>
                    ) : `${disk.temp} °C`}
                  </td>
                  <td className="px-5 py-2.5 align-top">
                    {disk.smart_label ? <Badge tone={toneOf(disk.smart_tone)}>{disk.smart_label}</Badge> : <span className="text-white/50">–</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function BackupsCard({ data }: { data: Overview }) {
  const backups = data.backups ?? null;
  const note = data.backup_note ?? null;
  // Ein älteres Backend kennt Hyper Backup nicht und sendet weder Aufgaben noch Hinweis.
  if (!backups && !note) return null;
  return (
    <Card title="Hyper Backup" padded={false}>
      {note && <div className="px-5 py-4"><PartNote note={note} testId="backup-note" /></div>}
      {backups && backups.length === 0 && (
        <p data-testid="no-backups" className="px-5 py-4 text-sm text-white/50">In Hyper Backup ist noch keine Sicherung eingerichtet.</p>
      )}
      {backups && backups.length > 0 && (
        <>
          <div className="overflow-x-auto">
            <table data-testid="backups" className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/10 text-left text-xs text-white/50">
                  <th className="px-5 py-2 font-medium">Aufgabe</th>
                  <th className="px-3 py-2 font-medium">Ergebnis</th>
                  <th className="whitespace-nowrap px-3 py-2 font-medium">Letzter Lauf</th>
                  <th className="whitespace-nowrap px-5 py-2 font-medium">Nächster Lauf</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.06]">
                {backups.map((task) => (
                  <tr key={task.id} data-testid="backup">
                    <td className="break-words px-5 py-2.5 align-top">
                      <span className="font-medium">{task.name}</span>
                      {task.problem && <p data-testid="backup-problem" className={`text-xs ${TONE_TEXT[task.tone] ?? "text-white/60"}`}>{task.problem}</p>}
                    </td>
                    <td className="px-3 py-2.5 align-top"><Badge tone={toneOf(task.tone)}>{task.result_label}</Badge></td>
                    <td className="whitespace-nowrap px-3 py-2.5 align-top tabular-nums">{dsmTime(task.last_run) ?? <span className="text-white/50">–</span>}</td>
                    <td className="whitespace-nowrap px-5 py-2.5 align-top tabular-nums">{dsmTime(task.next_run) ?? <span className="text-white/50">kein Zeitplan</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="border-t border-white/[0.06] px-5 py-2.5 text-xs text-white/45">Zeiten in Ortszeit der Synology.</p>
        </>
      )}
    </Card>
  );
}

function Tiles({ data }: { data: Overview }) {
  const { system, load } = data;
  const busy = (value: number | null | undefined): Tone | undefined => (value !== null && value !== undefined && value > FULL_PERCENT ? "warn" : undefined);
  return (
    <div className="space-y-2">
      <div data-testid="tiles" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="CPU" value={load?.cpu_percent != null ? percentText(load.cpu_percent) : "–"} tone={busy(load?.cpu_percent)} />
        <Stat
          label="Arbeitsspeicher"
          value={load?.ram_percent != null ? percentText(load.ram_percent) : "–"}
          hint={system?.ram_mb != null ? `von ${sizeText(system.ram_mb * 1024 * 1024)}` : undefined}
          tone={busy(load?.ram_percent)}
        />
        <Stat
          label="Temperatur"
          value={system?.temperature != null ? `${system.temperature} °C` : "–"}
          hint={system?.temperature_warn === true ? "DSM meldet: zu warm" : undefined}
          tone={system?.temperature_warn === true ? "bad" : undefined}
        />
        <Stat label="Laufzeit" value={system?.uptime_seconds != null ? durationText(system.uptime_seconds) : "–"} />
      </div>
      <PartNote note={data.system_note} testId="note-system" />
      <PartNote note={data.load_note} testId="note-load" />
    </div>
  );
}

function Device({ data }: { data: Overview }) {
  const system = data.system;
  const head = headline(data);
  const openHref = safeHref(data.url);
  const facts = [system?.dsm_version, system?.ram_mb != null ? `${sizeText(system.ram_mb * 1024 * 1024)} Arbeitsspeicher` : null].filter(Boolean);
  return (
    <div className="space-y-6">
      <AttentionCard data={data} />
      <Card
        title={system?.model ?? "Synology"}
        description={openHref ? <a href={openHref} target="_blank" rel="noreferrer" className="break-all hover:text-white hover:underline">DSM öffnen</a> : undefined}
        actions={<Badge tone={head.tone}>{head.label}</Badge>}
      >
        <div data-testid="device" className="space-y-3">
          {facts.length > 0 && <p data-testid="facts" className="break-words text-sm text-white/70">{facts.join(" · ")}</p>}
          {data.update?.available && (
            <p data-testid="update" role="status" className="break-words text-sm text-amber-300">
              Update verfügbar{data.update.version ? `: ${data.update.version}` : ""}
            </p>
          )}
          {data.update && !data.update.available && <p data-testid="up-to-date" className="text-sm text-white/50">DSM ist aktuell.</p>}
          <PartNote note={data.update_note} testId="note-update" />
        </div>
      </Card>
      <Tiles data={data} />
      <PoolsCard data={data} />
      <VolumesCard data={data} />
      <DisksCard data={data} />
      <BackupsCard data={data} />
    </div>
  );
}

function StateProblem({ data, onRetry }: { data: Overview; onRetry: () => void }) {
  const canManage = deck().hasPermission("extensions.manage");
  const head = PROBLEM[data.state] ?? PROBLEM.error;
  return (
    <Card title="Synology DSM" actions={<Badge tone={head.tone}>{head.label}</Badge>}>
      <div data-testid="problem" role="alert" className="flex flex-col items-start gap-3">
        <p className="break-words text-sm text-red-300">{data.message}</p>
        <div className="flex flex-wrap items-center gap-2">
          {/* Nach einer abgelehnten Anmeldung fragt das Dashboard erst nach einer Pause wieder. */}
          {data.state !== "auth_failed" && <Button small onClick={onRetry}>Erneut versuchen</Button>}
          {canManage && <a href={SETTINGS_PATH} className={buttonClass("ghost", true)}>Einstellungen</a>}
        </div>
      </div>
    </Card>
  );
}

function NotConfigured({ data }: { data: Overview }) {
  const canManage = deck().hasPermission("extensions.manage");
  return (
    <EmptyState
      icon="clock"
      title="Noch nicht eingerichtet"
      text={canManage ? data.message ?? undefined : "Ein Administrator kann Synology DSM unter Einstellungen → Erweiterungen → Synology DSM einrichten."}
      action={<SettingsLink to={SETTINGS_PATH} permission="extensions.manage">Jetzt einrichten</SettingsLink>}
    />
  );
}

export function SynologyPage(): JSX.Element {
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

  return (
    <Page
      title="Synology DSM"
      description="Zeigt den Zustand deiner Synology: Auslastung, Speicherpools, Volumes, Festplatten, Hyper Backup und ob ein Update bereitsteht. Nur lesend."
      actions={
        <>
          {deck().hasPermission("extensions.manage") && <a href={SETTINGS_PATH} className={buttonClass("ghost")}>Einstellungen</a>}
          <Button onClick={reload} disabled={loading}>Aktualisieren</Button>
        </>
      }
    >
      {error && <LoadError what="Der Zustand der Synology" error={error} onRetry={reload} />}
      {!data && !error && <Card><p className="text-sm text-white/50">Lade …</p></Card>}
      {data?.state === "not_configured" && <NotConfigured data={data} />}
      {data && data.state !== "ok" && data.state !== "not_configured" && <StateProblem data={data} onRetry={reload} />}
      {data?.state === "ok" && <Device data={data} />}
    </Page>
  );
}
