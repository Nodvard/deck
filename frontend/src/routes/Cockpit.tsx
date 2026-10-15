/**
 * Cockpit: Übersicht oben auf der Startseite mit Lage, Kennzahlen, Servertabelle, offenen Punkten, Modulen und Apps.
 * Herstellerneutral: Server und Messwerte kommen aus dem Kern, Dienste und Backups aus `GET /overview`.
 */
import {
  AlertTriangle,
  CheckCircle2,
  DatabaseBackup,
  Monitor,
  Server,
  ShieldCheck,
  SquareTerminal,
} from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router-dom";

import { AppsSection } from "../components/AppsSection";
import { DemoSeedButton } from "../components/DemoSeedButton";
import { ButtonLink, EmptyState } from "../components/EmptyState";
import { FirstStepsCard } from "../components/FirstStepsCard";
import { Icon } from "../components/Icon";
import { LoadError } from "../components/LoadError";
import { ProviderBadge } from "../components/ProviderBadge";
import { usePages } from "../lib/catalog";
import { useExtensionLabel } from "../lib/extensionNames";
import { NEW_HOST_TARGET, useExtensions } from "../lib/firstSteps";
import { hostProvider, type HostProvider } from "../lib/hostProvider";
import { neverAnswered } from "../lib/hosts";
import {
  formatAge,
  formatBytes,
  formatUptime,
  hasConsole,
  hostHealth,
  hostStatusText,
  type HostLatestMetrics,
  type HostOut,
  isNode,
  relativeTime,
  useHostMetrics,
  useHosts,
  useLatestMetrics,
  useOverview,
  useTerminalHosts,
} from "../lib/overview";
import { useAuthStore } from "../state/auth";

/** Grund, wenn eine Abfrage ohne Fehler wartet: das Gerät hat gerade kein Netz. */
const OFFLINE_TEXT = "Dieses Gerät ist gerade offline. Sobald es wieder Netz hat, lädt die Seite von selbst.";

const KIND_LABEL: Record<string, string> = { vm: "VM", lxc: "Container", hypervisor: "Knoten", node: "Knoten" };

function Panel({ title, right, children }: { title: string; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="panel overflow-hidden" aria-label={title}>
      <header className="flex items-center gap-2 border-b border-[var(--color-line)] bg-[var(--color-surface-head)] px-4 py-2.5">
        <h2 className="text-sm font-semibold">{title}</h2>
        {right && <div className="ml-auto text-sm">{right}</div>}
      </header>
      {children}
    </section>
  );
}

/** Unterzeile der Backup-Kachel. Eine Verbindung, die nicht antwortet, ist kein Job und
 * kein "noch ohne Lauf" -- sie steht als eigene Warnung da. */
function backupSummary(failed: number, unreachable: number, unknown: number): string {
  const parts: string[] = [];
  if (failed) parts.push(`${failed} fehlgeschlagen`);
  if (unreachable) parts.push(unreachable === 1 ? "Verbindung nicht erreichbar" : `${unreachable} Verbindungen nicht erreichbar`);
  if (!failed && unknown) parts.push(`${unknown} noch ohne Lauf`);
  return parts.join(" · ") || "alle erfolgreich";
}

function StatCard({
  to, label, value, sub, tone = "neutral",
}: { to?: string; label: string; value: string; sub: string; tone?: "good" | "warn" | "danger" | "neutral" }) {
  const toneClass = { good: "text-emerald-300", warn: "text-amber-300", danger: "text-red-300", neutral: "text-white/55" }[tone];
  const body = (
    <div className="panel panel-hover h-full px-4 py-3" data-testid={`stat-${label}`}>
      <p className="text-sm text-white/55">{label}</p>
      <p className={`mt-1 text-2xl font-semibold tabular-nums ${tone === "neutral" ? "" : toneClass}`}>{value}</p>
      <p className={`mt-0.5 text-xs ${tone === "neutral" ? "text-white/55" : toneClass}`}>{sub}</p>
    </div>
  );
  return to ? <Link to={to} className="block">{body}</Link> : body;
}

function loadColor(pct: number): string {
  return pct >= 90 ? "var(--color-bad)" : pct >= 75 ? "var(--color-warn)" : "var(--color-accent)";
}

/** Auslastung als schmaler Balken für die Servertabelle; die Zahlen daneben, auf breiten Schirmen auch absolut. */
function Meter({ value, label, caption, dim = false }: { value: number | null; label: string; caption?: string; dim?: boolean }) {
  const pct = value == null ? 0 : Math.max(0, Math.min(100, value));
  return (
    <span className={`inline-flex items-center gap-2 whitespace-nowrap ${dim ? "opacity-50" : ""}`}>
      <span
        role="img"
        aria-label={`${label} ${value == null ? "unbekannt" : `${Math.round(pct)} %`}`}
        className="inline-block h-1.5 w-12 flex-none overflow-hidden rounded-full bg-[var(--color-track)]"
      >
        <span className="block h-full" style={{ width: `${pct}%`, background: loadColor(pct) }} />
      </span>
      <span className="tabular-nums text-white/70">{value == null ? "–" : `${Math.round(pct)} %`}</span>
      {value != null && caption && <span className="hidden tabular-nums text-white/45 2xl:inline">{caption}</span>}
    </span>
  );
}

export function Ring({ value, label, caption, dim = false }: { value: number | null; label: string; caption?: string; dim?: boolean }) {
  const r = 22;
  const c = 2 * Math.PI * r;
  const pct = value == null ? 0 : Math.max(0, Math.min(100, value));
  const color = loadColor(pct);
  return (
    <div className={`flex min-w-0 flex-col items-center gap-1 ${dim ? "opacity-45" : ""}`}>
      <svg width="58" height="58" viewBox="0 0 58 58" role="img" aria-label={`${label} ${value == null ? "unbekannt" : `${Math.round(pct)} %`}`}>
        <circle cx="29" cy="29" r={r} fill="none" stroke="var(--color-track)" strokeWidth="6" />
        <circle
          cx="29" cy="29" r={r} fill="none" stroke={color} strokeWidth="6" strokeLinecap="round"
          strokeDasharray={`${(pct / 100) * c} ${c}`} transform="rotate(-90 29 29)"
          style={{ transition: "stroke-dasharray 0.6s ease" }}
        />
        <text x="29" y="33" textAnchor="middle" fontSize="12" fontWeight="600" fill="currentColor">
          {value == null ? "–" : `${Math.round(pct)}%`}
        </text>
      </svg>
      <span className="text-[11px] text-white/50">{label}</span>
      {caption && <span className="break-words text-center text-[10px] tabular-nums leading-tight text-white/45">{caption}</span>}
    </div>
  );
}

/** „1.7 GB / 3.7 GB“ -- leer, wenn eine der beiden Zahlen fehlt. */
function usedOfTotal(used: number | null | undefined, total: number | null | undefined): string | undefined {
  return used != null && total ? `${formatBytes(used)} / ${formatBytes(total)}` : undefined;
}

/** Was eine Karte zeigt. `state`: aktuell, veraltet (Wert zu alt) oder nicht erreichbar (keine Werte als aktuell zeigen). */
interface CardData {
  state: "live" | "stale" | "offline";
  cpu: number | null;
  mem: number | null;
  memText?: string;
  /** Platte: nur wenn gemessen (Hypervisor-Knoten liefern sie nicht). */
  disk?: number | null;
  diskText?: string;
  uptime_s?: number | null;
  /** Hinweis unter den Ringen, z. B. „veraltet · vor 4 min“. */
  note?: string;
}

function HostActions({ host, canTerminal, console }: { host: HostOut; canTerminal: boolean; console: boolean }) {
  const linkClass = "rounded-md p-1.5 text-white/55 hover:bg-white/10 hover:text-white";
  return (
    <td className="w-px whitespace-nowrap px-2 text-right">
      {console && (
        <Link to={`/console/${host.id}`} className={linkClass} title="Konsole" aria-label={`Konsole ${host.display_name}`}>
          <Monitor size={15} className="inline" />
        </Link>
      )}
      {canTerminal && (
        <Link to={`/terminal?host=${encodeURIComponent(host.id)}`} className={linkClass} title="Terminal" aria-label={`Terminal ${host.display_name}`}>
          <SquareTerminal size={15} className="inline" />
        </Link>
      )}
    </td>
  );
}

const ROW = "border-t border-[var(--color-line)] align-middle";
const CELL = "px-3 py-2.5";

/** Eine Tabellenzeile für Hypervisor-Knoten und Linux-Server mit Messwerten; nur die Quelle der Zahlen unterscheidet sich. */
function HostRow({ host, data, testId, canTerminal, provider }: { host: HostOut; data: CardData; testId: string; canTerminal: boolean; provider?: HostProvider }) {
  const health = hostHealth(host.status);
  const offline = data.state === "offline";
  const dim = data.state !== "live";
  const uptime = offline ? null : data.uptime_s;
  return (
    <tr className={ROW} data-testid={testId} data-state={data.state}>
      <td className={`${CELL} pl-4`}>
        <span className="flex items-center gap-2">
          <span className={`status-dot ${health}`} title={hostStatusText(host)} />
          <Link to={`/hosts/${host.id}`} className="min-w-0 break-words font-medium hover:underline">{host.display_name}</Link>
          <ProviderBadge provider={provider} testId={`${testId}-provider-off`} />
        </span>
        {data.note && (
          <span className={`mt-0.5 block text-[11px] ${offline ? "text-red-300" : "text-amber-300"}`} data-testid={`${testId}-note`}>
            {data.note}
          </span>
        )}
      </td>
      <td className={`${CELL} hidden tabular-nums text-white/55 md:table-cell`}>{host.address}</td>
      <td className={CELL}><Meter value={offline ? null : data.cpu} label="CPU" dim={dim} /></td>
      <td className={CELL}><Meter value={offline ? null : data.mem} label="RAM" caption={data.memText} dim={dim} /></td>
      <td className={`${CELL} hidden sm:table-cell`}>
        {data.disk !== undefined ? <Meter value={offline ? null : data.disk} label="Platte" caption={data.diskText} dim={dim} /> : <span className="text-white/35">–</span>}
      </td>
      <td className={`${CELL} hidden whitespace-nowrap tabular-nums text-white/55 lg:table-cell`} data-testid={`${testId}-uptime`}>
        {uptime ? formatUptime(uptime) : "–"}
      </td>
      <HostActions host={host} canTerminal={canTerminal} console={false} />
    </tr>
  );
}

/** Hypervisor-Knoten: Werte live vom Anbieter (eine Abfrage je Knoten, kein SSH). */
function NodeCard({ host, canTerminal, provider }: { host: HostOut; canTerminal: boolean; provider?: HostProvider }) {
  const { data, isError } = useHostMetrics(host.id);
  const v = data?.values ?? {};
  const memPct = v.mem_total_bytes ? (100 * (v.mem_used_bytes ?? 0)) / v.mem_total_bytes : null;
  const offline = hostHealth(host.status) === "problem";
  return (
    <HostRow
      host={host}
      testId={`node-${host.id}`}
      canTerminal={canTerminal}
      provider={provider}
      data={{
        state: offline ? "offline" : "live",
        cpu: isError ? null : (v.cpu_percent ?? null),
        mem: isError ? null : memPct,
        memText: usedOfTotal(v.mem_used_bytes, v.mem_total_bytes),
        uptime_s: v.uptime_s,
        note: offline ? "nicht erreichbar · keine aktuellen Werte" : undefined,
      }}
    />
  );
}

/** Linux-Server mit Messwerten aus dem Verlauf (`GET /hosts/metrics/latest`, ohne SSH-Aufruf). Ein
 * nicht erreichbarer Server zeigt keine alten Werte als aktuell; ein zu alter Wert heisst „veraltet“. */
function ServerCard({ host, latest, unconfirmed, canTerminal, provider }: { host: HostOut; latest: HostLatestMetrics; unconfirmed: boolean; canTerminal: boolean; provider?: HostProvider }) {
  const offline = hostHealth(host.status) === "problem";
  const stale = latest.stale || unconfirmed;
  return (
    <HostRow
      host={host}
      testId={`server-${host.id}`}
      canTerminal={canTerminal}
      provider={provider}
      data={{
        state: offline ? "offline" : stale ? "stale" : "live",
        cpu: latest.cpu,
        mem: latest.mem,
        memText: usedOfTotal(latest.mem_used_bytes, latest.mem_total_bytes),
        disk: latest.disk,
        diskText: usedOfTotal(latest.disk_used_bytes, latest.disk_total_bytes),
        uptime_s: latest.uptime_s,
        note: offline
          ? "nicht erreichbar · keine aktuellen Werte"
          : stale ? `veraltet · letzte Messung ${formatAge(latest.age_s)}` : undefined,
      }}
    />
  );
}

/** Server ohne Messwerte: dieselbe Zeile, die Auslastung bleibt leer, dafür steht der Grund unter dem Namen. */
function MachineRow({ host, canTerminal, hint, provider }: { host: HostOut; canTerminal: boolean; hint?: string | null; provider?: HostProvider }) {
  const health = hostHealth(host.status);
  const empty = <span className="text-white/35">–</span>;
  return (
    <tr className={ROW} data-testid={`machine-${host.id}`}>
      <td className={`${CELL} pl-4`}>
        <span className="flex items-center gap-2">
          <span className={`status-dot ${health}`} title={hostStatusText(host)} />
          <Link to={`/hosts/${host.id}`} className="min-w-0 break-words font-medium hover:underline">{host.display_name}</Link>
          {provider?.problem && <ProviderBadge provider={provider} testId={`machine-${host.id}-provider-off`} />}
        </span>
        {hint && <span className="mt-0.5 block text-[11px] text-white/45" data-testid={`machine-${host.id}-hint`}>{hint}</span>}
      </td>
      <td className={`${CELL} hidden whitespace-nowrap text-white/55 md:table-cell`}>
        <span className="tabular-nums">{host.address}</span>
        <span className="text-white/40"> · {KIND_LABEL[host.kind ?? ""] ?? "Server"}</span>
      </td>
      <td className={CELL}>{empty}</td>
      <td className={CELL}>{empty}</td>
      <td className={`${CELL} hidden sm:table-cell`}>{empty}</td>
      <td className={`${CELL} hidden lg:table-cell`}>{empty}</td>
      <HostActions host={host} canTerminal={canTerminal} console={hasConsole(host) && health === "online"} />
    </tr>
  );
}

// Die Kacheln des Bereichs „Apps“ liegen in components/AppTile.tsx; die Server-Seite importiert sie weiter von hier.
export { AppTile } from "../components/AppTile";

/** Warum ein Linux-Server keine Auslastung zeigt (nur für von Hand angelegte Server, die per SSH gemessen würden). */
function metricsHint(host: HostOut, systemModuleOn: boolean | null): string | null {
  if (host.os_family !== "linux" || host.provider_ext_id || isNode(host) || hasConsole(host)) return null;
  if (!host.credential) return "Keine Auslastung: noch kein SSH-Zugang";
  if (!host.enabled) return "Keine Auslastung: Messwerte für diesen Server sind aus";
  if (systemModuleOn === false) return "Keine Auslastung: Modul „Server-Zustand“ ist aus";
  // Hat der Server nie geantwortet, kommt auch nichts mehr nach: erst die Verbindung klaeren, nicht auf Kurven warten.
  if (hostHealth(host.status) === "problem" && neverAnswered(host)) return "Noch keine Verbindung: prüfe zuerst den Zugang";
  if (systemModuleOn && hostHealth(host.status) !== "problem") return "Noch keine Messwerte";
  return null;
}

export function Cockpit() {
  const canReadHosts = useAuthStore((s) => s.hasPermission("hosts.read"));
  const canExecute = useAuthStore((s) => s.hasPermission("hosts.execute"));
  const canWriteHosts = useAuthStore((s) => s.hasPermission("hosts.write"));
  const canWriteApps = useAuthStore((s) => s.hasPermission("apps.write"));
  const hostsQuery = useHosts(canReadHosts);
  const overviewQuery = useOverview(canReadHosts);
  const { data: hosts, isLoading: hostsLoading } = hostsQuery;
  const { data: overview, isLoading: overviewLoading } = overviewQuery;
  const { data: terminalHosts } = useTerminalHosts(canExecute);
  const { data: latest, isLoading: latestLoading, isError: latestError } = useLatestMetrics(canReadHosts);
  const { data: pages } = usePages();
  const extensionLabel = useExtensionLabel((overview?.attention ?? []).some((a) => a.source_ext_id));
  // Welches Modul einen Server eingelesen hat und ob es noch läuft (sonst kennzeichnet ihn das Cockpit).
  const { data: extensions } = useExtensions(canReadHosts);

  const nodes = useMemo(() => (hosts ?? []).filter(isNode), [hosts]);
  // Server mit einem letzten Messwert (aus dem Verlauf) bekommen eine Karte wie die Knoten, alle anderen
  // bleiben eine schlichte Zeile. Solange die Werte laden, gibt es noch keine Zeilen (kein Springen).
  const [servers, machines] = useMemo(() => {
    const order = { problem: 0, unknown: 1, online: 2, stopped: 3 } as const;
    const rest = (hosts ?? [])
      .filter((h) => !isNode(h))
      .sort((a, b) => order[hostHealth(a.status)] - order[hostHealth(b.status)] || a.display_name.localeCompare(b.display_name));
    const measured = latest?.hosts ?? {};
    return [rest.filter((h) => measured[h.id]), rest.filter((h) => !measured[h.id])] as const;
  }, [hosts, latest]);
  const systemModuleOn = pages ? pages.some((p) => p.ext_id === "system") : null;
  const terminalIds = useMemo(() => new Set(terminalHosts ?? []), [terminalHosts]);

  const allHosts = hosts ?? [];
  const online = allHosts.filter((h) => hostHealth(h.status) === "online").length;
  const problemHosts = allHosts.filter((h) => hostHealth(h.status) === "problem");
  // Von Hand angelegte Server haben ohne „Verbindung prüfen“ keinen Zustand (Proxmox liefert ihn
  // für seine Gäste mit): „unbekannt“ ist weder online noch ein Problem und wird so auch gezählt.
  const unknownHosts = allHosts.filter((h) => hostHealth(h.status) === "unknown");
  const backups = overview?.backups ?? null;
  const backupsUnreachable = backups?.unreachable_names ?? [];
  // Platzhalter nicht erreichbarer Server (`unreachable`) sind keine Dienste: sie zaehlen weder als „laeuft“ noch als „laeuft nicht“.
  const services = (overview?.services ?? []).filter((s) => !s.unreachable);
  const servicesDown = overview?.services_unreachable_hosts ?? [];
  // Nicht nur „Server aus“: der Platzhalter kommt auch, wenn der Server antwortet, Docker aber nicht (Dienst aus, keine Rechte).
  const servicesDownText = `Container von ${servicesDown.length === 1 ? "1 Server" : `${servicesDown.length} Servern`} nicht abrufbar`;
  // Ein Server, der ohnehin als „nicht erreichbar“ in der Liste steht, zaehlt nicht doppelt.
  const problemNames = new Set(problemHosts.map((h) => h.display_name));
  const servicesDownExtra = servicesDown.filter((name) => !problemNames.has(name));
  const attention = overview?.attention ?? [];
  const issues = problemHosts.length + servicesDownExtra.length + (backups?.failed ?? 0) + backupsUnreachable.length + attention.length + (overview?.pending_actions ?? 0);

  // Eigene Apps und erkannte Dienste in einer Liste (`services` bleibt fuer die Kennzahl „Dienste“).
  const apps = overview?.apps ?? [];

  const quickPages = (pages ?? []).filter((p) => p.show_in_nav !== false).sort((a, b) => a.nav_order - b.nav_order);
  const loading = canReadHosts && (hostsLoading || overviewLoading);
  // Ohne einen einzigen Server gibt es nichts, was "rund laufen" koennte -- stattdessen zeigt das
  // Cockpit, was als Naechstes zu tun ist.
  const noHosts = canReadHosts && !loading && hosts !== undefined && allHosts.length === 0;
  // Eine gescheiterte Abfrage ist keine Auskunft: ohne Serverliste kein „Alles läuft rund“, ohne Übersicht
  // (Dienste, Backups, Meldungen, Freigaben) kein „Nichts offen“. Scheitert nur das Nachladen, bleiben die
  // letzten Werte stehen, aber als „nicht aktuell“ gekennzeichnet. „Ohne Daten und lädt nicht“ ist auch
  // eine wartende Abfrage (Gerät offline, etwa die installierte App ohne Netz): dort gibt es keinen Fehler.
  const hostsFailed = canReadHosts && !hostsLoading && hosts === undefined;
  const overviewFailed = canReadHosts && !overviewLoading && overview === undefined;
  const stale = canReadHosts && !hostsFailed && !overviewFailed && (hostsQuery.isError || overviewQuery.isError);
  // Der Grund passt zur Überschrift im Kasten (gleiche Reihenfolge wie dort).
  const failedQuery = hostsFailed ? hostsQuery : overviewFailed ? overviewQuery
    : hostsQuery.isError ? hostsQuery : overviewQuery.isError ? overviewQuery : null;
  const loadFailure = failedQuery ? (failedQuery.error ?? OFFLINE_TEXT) : null;
  const retryLoad = () => {
    if (hostsFailed || hostsQuery.isError) void hostsQuery.refetch();
    if (overviewFailed || overviewQuery.isError) void overviewQuery.refetch();
  };
  const unavailable = "gerade nicht abrufbar";

  return (
    <section className="relative isolate" aria-label="Cockpit">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 pt-5 sm:px-6 sm:pt-6">
        <h1 className="text-xl font-semibold">Übersicht</h1>
        {canReadHosts && (
          <p className="flex min-w-0 items-center gap-2 text-sm text-white/60" data-testid="cockpit-status">
            {loading ? (
              <span className="opacity-60">Lagebild wird geladen …</span>
            ) : hostsFailed ? (
              <>
                <AlertTriangle size={16} className="text-amber-400" />
                Lagebild gerade nicht abrufbar. Der Zustand der Server ist unbekannt.
              </>
            ) : noHosts ? (
              <>
                <Server size={16} className="text-white/55" />
                Noch kein Server eingerichtet.
              </>
            ) : issues === 0 && overviewFailed ? (
              <>
                <Server size={16} className="text-white/55" />
                Keine Störung bei den Servern bekannt, {online} von {allHosts.length} online. Dienste, Backups und Meldungen sind {unavailable}.
              </>
            ) : issues === 0 && stale ? (
              <>
                <Server size={16} className="text-white/55" />
                Zuletzt keine Störung bekannt, {online} von {allHosts.length} Servern online. Der Stand ist nicht aktuell.
              </>
            ) : issues === 0 && unknownHosts.length > 0 ? (
              <>
                <Server size={16} className="text-white/55" />
                Keine Störung bekannt. {online} von {allHosts.length} Servern online, {unknownHosts.length} noch nicht geprüft
                {services.length > 0 && overview ? `, ${overview.services_running} Dienste aktiv` : ""}.
              </>
            ) : issues === 0 ? (
              <>
                <CheckCircle2 size={16} className="text-emerald-400" />
                Alles läuft rund. {online} von {allHosts.length} Servern online{overview && services.length > 0 ? `, ${overview.services_running} Dienste aktiv` : ""}.
              </>
            ) : (
              <>
                <AlertTriangle size={16} className="text-amber-400" />
                {issues === 1 ? "Eine Sache braucht" : `${issues} Dinge brauchen`} deine Aufmerksamkeit.
              </>
            )}
          </p>
        )}
        {canWriteHosts && !noHosts && (
          <div className="ml-auto">
            <ButtonLink to={NEW_HOST_TARGET} small>Server hinzufügen</ButtonLink>
          </div>
        )}
      </div>

      {canReadHosts && !loading && loadFailure != null && (
        <div className="px-4 pt-3 sm:px-6" data-testid="cockpit-load-error">
          <LoadError
            what={hostsFailed ? "Die Server-Liste" : overviewFailed ? "Die Übersicht (Dienste, Backups, Meldungen)" : "Der aktuelle Stand"}
            error={loadFailure}
            onRetry={retryLoad}
          />
        </div>
      )}

      <FirstStepsCard />

      {canReadHosts && (
        <div className="grid grid-cols-2 gap-3 px-4 pt-4 sm:px-6 md:grid-cols-3 xl:grid-cols-5">
          <StatCard
            label="Server" value={loading ? "…" : hostsFailed ? "–" : noHosts ? "0" : `${online}/${allHosts.length}`}
            sub={
              hostsFailed ? unavailable
                : problemHosts.length ? `${problemHosts.length} nicht erreichbar`
                : noHosts ? "noch keiner angelegt"
                : unknownHosts.length ? `online · ${unknownHosts.length} noch nicht geprüft`
                : "online"
            }
            tone={hostsFailed ? "warn" : problemHosts.length ? "danger" : noHosts || unknownHosts.length ? "neutral" : "good"}
          />
          <StatCard
            label="Dienste"
            value={overview ? (services.length === 0 ? "–" : `${overview.services_running}/${services.length}`) : overviewFailed ? "–" : "…"}
            sub={
              overviewFailed ? unavailable
                : servicesDown.length && services.length === 0 ? `unbekannt · ${servicesDownText}`
                : servicesDown.length ? `Container laufen · ${servicesDownText}`
                : overview && services.length === 0 ? "noch keine erfasst"
                : "Container laufen"
            }
            tone={
              overviewFailed || servicesDown.length ? "warn"
                : noHosts || services.length === 0 ? "neutral"
                : overview && overview.services_running < services.length ? "warn" : "good"
            }
          />
          {(backups || loading) && (
            <StatCard
              label="Backups"
              value={backups ? (backups.total === 0 && backupsUnreachable.length > 0 ? "–" : `${backups.ok}/${backups.total}`) : "…"}
              sub={backups ? backupSummary(backups.failed, backupsUnreachable.length, backups.unknown) : ""}
              tone={backups?.failed ? "danger" : backupsUnreachable.length || backups?.unknown ? "warn" : "good"}
            />
          )}
          <StatCard
            to="/notifications" label="Meldungen" value={overview ? String(overview.unread_notifications) : overviewFailed ? "–" : "…"}
            sub={overviewFailed ? unavailable : "ungelesen"} tone={overviewFailed || attention.length ? "warn" : "neutral"}
          />
          <StatCard
            to="/actions" label="Freigaben" value={overview ? String(overview.pending_actions) : overviewFailed ? "–" : "…"}
            sub={overviewFailed ? unavailable : overview?.pending_actions ? "warten auf dich" : "nichts offen"}
            tone={overviewFailed || overview?.pending_actions ? "warn" : "neutral"}
          />
        </div>
      )}

      <div className="grid gap-4 px-4 pt-4 sm:px-6 xl:grid-cols-3">
        {canReadHosts && (
          <div className="min-w-0 xl:col-span-2">
            {!noHosts && (
            <Panel title="Server" right={allHosts.length > 0 ? <span className="text-white/50">{allHosts.length}</span> : undefined}>
            {(nodes.length + servers.length > 0 || (!latestLoading && machines.length > 0)) && (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-white/50">
                      <th className="px-3 py-2 pl-4 font-medium">Name</th>
                      <th className="hidden px-3 py-2 font-medium md:table-cell">Adresse</th>
                      <th className="px-3 py-2 font-medium">CPU</th>
                      <th className="px-3 py-2 font-medium">RAM</th>
                      <th className="hidden px-3 py-2 font-medium sm:table-cell">Platte</th>
                      <th className="hidden px-3 py-2 font-medium lg:table-cell">Läuft seit</th>
                      <th className="px-2 py-2"><span className="sr-only">Aktionen</span></th>
                    </tr>
                  </thead>
                  {nodes.length + servers.length > 0 && (
                    <tbody data-testid="host-cards">
                      {nodes.map((node) => <NodeCard key={node.id} host={node} canTerminal={terminalIds.has(node.id)} provider={hostProvider(node, extensions)} />)}
                      {servers.map((host) => (
                        <ServerCard
                          key={host.id}
                          host={host}
                          latest={latest!.hosts[host.id]}
                          unconfirmed={latestError}
                          canTerminal={terminalIds.has(host.id)}
                          provider={hostProvider(host, extensions)}
                        />
                      ))}
                    </tbody>
                  )}
                  {!latestLoading && machines.length > 0 && (
                    <tbody>
                      {machines.map((host) => (
                        <MachineRow key={host.id} host={host} canTerminal={terminalIds.has(host.id)} hint={metricsHint(host, systemModuleOn)} provider={hostProvider(host, extensions)} />
                      ))}
                    </tbody>
                  )}
                </table>
              </div>
            )}
            {loading && <p className="px-4 py-3 text-sm text-white/50">Lade Server …</p>}
            </Panel>
            )}
            {noHosts && (
              <EmptyState
                icon="server"
                testId="cockpit-no-hosts"
                title="Noch kein Server eingerichtet"
                text={
                  canWriteHosts
                    ? "Leg den ersten Server an, zum Beispiel den Raspberry Pi, auf dem Nodvard Deck läuft. Danach siehst du hier seinen Zustand und seine Auslastung."
                    : "Sobald ein Administrator den ersten Server angelegt hat, siehst du hier seinen Zustand und seine Auslastung."
                }
                action={
                  canWriteHosts ? (
                    <>
                      <ButtonLink to={NEW_HOST_TARGET}>Server hinzufügen</ButtonLink>
                      <DemoSeedButton />
                    </>
                  ) : undefined
                }
              />
            )}
          </div>
        )}

        <div className="flex min-w-0 flex-col gap-4">
          {canReadHosts && (
            <Panel title="Braucht Aufmerksamkeit">
              <div className="divide-y divide-[var(--color-line)]" data-testid="attention">
                {!loading && (hostsFailed || overviewFailed) && (
                  <p className="p-4 text-sm text-white/60" data-testid="attention-unknown">
                    {issues === 0
                      ? "Unbekannt: Die Daten dafür ließen sich gerade nicht laden."
                      : "Vielleicht nicht vollständig: Ein Teil der Daten ließ sich gerade nicht laden."}
                  </p>
                )}
                {!loading && issues === 0 && !hostsFailed && !overviewFailed && (stale ? (
                  <p className="p-4 text-sm text-white/60" data-testid="attention-stale">
                    Zuletzt war nichts offen. Der Stand ist nicht aktuell.
                  </p>
                ) : (
                  <p className="flex items-center gap-2 p-4 text-sm text-white/60">
                    <CheckCircle2 size={16} className="text-emerald-400" /> Nichts offen.
                  </p>
                ))}
                {problemHosts.map((h) => (
                  <p key={h.id} className="flex items-center gap-2 px-4 py-3 text-sm">
                    <span className="status-dot problem" /> {h.display_name} ist nicht erreichbar
                  </p>
                ))}
                {(backups?.failed_names ?? []).map((name) => (
                  <p key={name} className="flex items-center gap-2 px-4 py-3 text-sm">
                    <DatabaseBackup size={15} className="text-red-300" /> Backup fehlgeschlagen: {name}
                  </p>
                ))}
                {servicesDownExtra.map((name) => (
                  <p key={`services-unreachable-${name}`} className="flex items-center gap-2 px-4 py-3 text-sm">
                    <span className="status-dot problem" /> Container nicht abrufbar: {name}
                  </p>
                ))}
                {backupsUnreachable.map((name) => (
                  <p key={`backup-unreachable-${name}`} className="flex items-center gap-2 px-4 py-3 text-sm">
                    <DatabaseBackup size={15} className="text-amber-300" /> Backup-Verbindung nicht erreichbar: {name}
                  </p>
                ))}
                {overview && overview.pending_actions > 0 && (
                  <Link to="/actions" className="flex items-center gap-2 px-4 py-3 text-sm hover:bg-white/[0.03]">
                    <ShieldCheck size={15} className="text-amber-300" />
                    {overview.pending_actions === 1 ? "Eine Aktion wartet" : `${overview.pending_actions} Aktionen warten`} auf Freigabe
                  </Link>
                )}
                {attention.map((a) => (
                  <Link key={a.id} to="/notifications" className="flex items-start gap-2 px-4 py-3 text-sm hover:bg-white/[0.03]">
                    <AlertTriangle size={15} className={`mt-0.5 flex-none ${a.severity === "critical" ? "text-red-300" : "text-amber-300"}`} />
                    <span className="min-w-0 flex-1">
                      <span className="block break-words">{a.title}</span>
                      <span className="text-[11px] text-white/45">{relativeTime(a.ts)}{a.source_ext_id ? ` · ${extensionLabel(a.source_ext_id)}` : ""}</span>
                    </span>
                  </Link>
                ))}
                {(overview?.errors ?? []).map((e) => (
                  <p key={e} className="px-4 py-2 text-[11px] text-white/45">Teilweise nicht abrufbar: {e}</p>
                ))}
              </div>
            </Panel>
          )}

          <Panel title="Module">
            <div className="grid grid-cols-2 gap-0.5 p-2" data-testid="modules">
              {[
                { to: "/files", icon: "folder-open", title: "Dateien" },
                ...(canExecute ? [{ to: "/terminal", icon: "terminal", title: "Terminal" }] : []),
                ...quickPages.map((p) => ({ to: `/ext/${p.ext_id}${p.path}`, icon: p.icon ?? "box", title: p.title })),
              ].map((m) => (
                <Link key={m.to} to={m.to} className="flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-white/[0.06]">
                  <Icon name={m.icon} size={15} className="flex-none text-[var(--color-accent-text)]" />
                  <span className="min-w-0 break-words">{m.title}</span>
                </Link>
              ))}
            </div>
          </Panel>
        </div>
      </div>

      {canReadHosts && overview && (apps.length > 0 || canWriteApps) && (
        <div className="px-4 pb-6 pt-6 sm:px-6">
          <AppsSection apps={apps} hosts={allHosts} canWrite={canWriteApps} />
        </div>
      )}
    </section>
  );
}
