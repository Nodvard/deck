/**
 * Cockpit: der obere Teil der Startseite, gedacht als zentrale Uebersicht, die andere
 * Werkzeuge im Alltag ueberfluessig macht. Lagebild in einem
 * Satz, Kennzahlen, Infrastruktur mit Live-Auslastung, was Aufmerksamkeit braucht,
 * alle Apps, alle Module. Das frei anordenbare Widget-Raster folgt darunter
 * (DashboardPage.tsx).
 *
 * Herstellerneutral wie der Kern: Hosts/Metriken/Konsole/Terminal kommen aus Kern-
 * Endpunkten, Dienste/Backups aus GET /overview (SDK-Capabilities) -- kein "Proxmox"
 * im Code, nur `kind` (hypervisor/vm/lxc), das der Kern ohnehin kennt.
 */
import {
  AlertTriangle,
  Bell,
  Boxes,
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
import { usePages } from "../lib/catalog";
import { useExtensionLabel } from "../lib/extensionNames";
import { NEW_HOST_TARGET } from "../lib/firstSteps";
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

const KIND_LABEL: Record<string, string> = { vm: "VM", lxc: "Container", hypervisor: "Knoten", node: "Knoten" };

function greeting(date = new Date()): string {
  const h = date.getHours();
  if (h < 5) return "Gute Nacht";
  if (h < 11) return "Guten Morgen";
  if (h < 18) return "Guten Tag";
  return "Guten Abend";
}

function SectionTitle({ icon, children, right }: { icon: JSX.Element; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="mb-3 flex items-center gap-2">
      <span className="text-white/60">{icon}</span>
      <h2 className="text-sm font-semibold uppercase tracking-[0.12em] text-white/70">{children}</h2>
      <div className="ml-auto">{right}</div>
    </div>
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
  to, icon, label, value, sub, tone = "neutral",
}: { to?: string; icon: JSX.Element; label: string; value: string; sub: string; tone?: "good" | "warn" | "danger" | "neutral" }) {
  const toneClass = { good: "text-emerald-300", warn: "text-amber-300", danger: "text-red-300", neutral: "text-white/55" }[tone];
  const body = (
    <div className="panel panel-hover h-full p-4" data-testid={`stat-${label}`}>
      <div className="flex items-center gap-2.5">
        <span className="accent-soft grid h-8 w-8 place-items-center rounded-lg">{icon}</span>
        <span className="text-xs font-medium uppercase tracking-wider text-white/60">{label}</span>
      </div>
      <p className="mt-3 text-3xl font-semibold tabular-nums tracking-tight">{value}</p>
      <p className={`mt-0.5 text-xs ${toneClass}`}>{sub}</p>
    </div>
  );
  return to ? <Link to={to} className="block">{body}</Link> : body;
}

export function Ring({ value, label, caption, dim = false }: { value: number | null; label: string; caption?: string; dim?: boolean }) {
  const r = 22;
  const c = 2 * Math.PI * r;
  const pct = value == null ? 0 : Math.max(0, Math.min(100, value));
  const color = pct >= 90 ? "#f87171" : pct >= 75 ? "#fbbf24" : "var(--color-accent)";
  return (
    <div className={`flex min-w-0 flex-col items-center gap-1 ${dim ? "opacity-45" : ""}`}>
      <svg width="58" height="58" viewBox="0 0 58 58" role="img" aria-label={`${label} ${value == null ? "unbekannt" : `${Math.round(pct)} %`}`}>
        <circle cx="29" cy="29" r={r} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="6" />
        <circle
          cx="29" cy="29" r={r} fill="none" stroke={color} strokeWidth="6" strokeLinecap="round"
          strokeDasharray={`${(pct / 100) * c} ${c}`} transform="rotate(-90 29 29)"
          style={{ transition: "stroke-dasharray 0.6s ease" }}
        />
        <text x="29" y="33" textAnchor="middle" fontSize="12" fontWeight="600" fill="currentColor">
          {value == null ? "–" : `${Math.round(pct)}%`}
        </text>
      </svg>
      <span className="text-[10px] uppercase tracking-wider text-white/50">{label}</span>
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

/** Eine Karte fuer Hypervisor-Knoten UND Linux-Server mit Messwerten -- gleiches Aussehen, nur die Quelle
 * der Zahlen unterscheidet sich (Knoten: live vom Anbieter, Server: letzter Wert aus dem Verlauf). */
function HostCard({ host, data, testId, canTerminal }: { host: HostOut; data: CardData; testId: string; canTerminal: boolean }) {
  const health = hostHealth(host.status);
  const offline = data.state === "offline";
  const dim = data.state !== "live";
  const uptime = offline ? null : data.uptime_s;
  return (
    <div className="panel min-w-0 p-4" data-testid={testId} data-state={data.state}>
      <div className="flex items-start gap-3">
        <span className="accent-soft grid h-9 w-9 flex-none place-items-center rounded-lg"><Server size={18} /></span>
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-2 font-medium">
            <span className={`status-dot ${health}`} title={hostStatusText(host)} />
            <Link to={`/hosts/${host.id}`} className="min-w-0 break-words hover:underline">{host.display_name}</Link>
          </p>
          <p className="break-words text-xs text-white/50">
            {host.address}{uptime ? ` · läuft seit ${formatUptime(uptime)}` : ""}
          </p>
        </div>
        {canTerminal && (
          <Link
            to={`/terminal?host=${encodeURIComponent(host.id)}`}
            className="flex-none rounded-md p-1.5 text-white/55 hover:bg-white/10 hover:text-white"
            title="Terminal"
            aria-label={`Terminal ${host.display_name}`}
          >
            <SquareTerminal size={15} />
          </Link>
        )}
      </div>
      <div className="mt-4 flex items-start justify-around gap-2">
        <Ring value={offline ? null : data.cpu} label="CPU" dim={dim} />
        <Ring value={offline ? null : data.mem} label="RAM" caption={offline ? undefined : data.memText} dim={dim} />
        {data.disk !== undefined && <Ring value={offline ? null : data.disk} label="Platte" caption={offline ? undefined : data.diskText} dim={dim} />}
      </div>
      {data.note && (
        <p
          className={`mt-3 text-[11px] ${offline ? "text-red-300" : "text-amber-300"}`}
          data-testid={`${testId}-note`}
        >
          {data.note}
        </p>
      )}
    </div>
  );
}

/** Hypervisor-Knoten: Werte live vom Anbieter (eine Abfrage je Knoten, kein SSH). */
function NodeCard({ host, canTerminal }: { host: HostOut; canTerminal: boolean }) {
  const { data, isError } = useHostMetrics(host.id);
  const v = data?.values ?? {};
  const memPct = v.mem_total_bytes ? (100 * (v.mem_used_bytes ?? 0)) / v.mem_total_bytes : null;
  const offline = hostHealth(host.status) === "problem";
  return (
    <HostCard
      host={host}
      testId={`node-${host.id}`}
      canTerminal={canTerminal}
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
function ServerCard({ host, latest, unconfirmed, canTerminal }: { host: HostOut; latest: HostLatestMetrics; unconfirmed: boolean; canTerminal: boolean }) {
  const offline = hostHealth(host.status) === "problem";
  const stale = latest.stale || unconfirmed;
  return (
    <HostCard
      host={host}
      testId={`server-${host.id}`}
      canTerminal={canTerminal}
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

function MachineRow({ host, canTerminal, hint }: { host: HostOut; canTerminal: boolean; hint?: string | null }) {
  const health = hostHealth(host.status);
  return (
    <div className="panel flex items-center gap-3 px-3 py-2.5" data-testid={`machine-${host.id}`}>
      <span className={`status-dot ${health}`} title={hostStatusText(host)} />
      <div className="min-w-0 flex-1">
        <Link to={`/hosts/${host.id}`} className="block break-words text-sm font-medium hover:underline">{host.display_name}</Link>
        <p className="break-words text-[11px] text-white/45">
          {KIND_LABEL[host.kind ?? ""] ?? "Server"} · {host.address}
        </p>
        {hint && <p className="break-words text-[11px] text-white/40" data-testid={`machine-${host.id}-hint`}>{hint}</p>}
      </div>
      {hasConsole(host) && health === "online" && (
        <Link to={`/console/${host.id}`} className="rounded-md p-1.5 text-white/55 hover:bg-white/10 hover:text-white" title="Konsole" aria-label={`Konsole ${host.display_name}`}>
          <Monitor size={15} />
        </Link>
      )}
      {canTerminal && (
        <Link
          to={`/terminal?host=${encodeURIComponent(host.id)}`}
          className="rounded-md p-1.5 text-white/55 hover:bg-white/10 hover:text-white"
          title="Terminal"
          aria-label={`Terminal ${host.display_name}`}
        >
          <SquareTerminal size={15} />
        </Link>
      )}
    </div>
  );
}

// Die Kacheln des Bereichs „Apps“ liegen in components/AppTile.tsx; die Server-Seite importiert sie weiter von hier.
export { AppTile } from "../components/AppTile";

/** Warum ein Linux-Server keine Auslastung zeigt (nur für von Hand angelegte Server, die per SSH gemessen würden). */
function metricsHint(host: HostOut, systemModuleOn: boolean | null): string | null {
  if (host.os_family !== "linux" || host.provider_ext_id || isNode(host) || hasConsole(host)) return null;
  if (!host.credential) return "Keine Auslastung: noch kein SSH-Zugang";
  if (!host.enabled) return "Keine Auslastung: Messwerte für diesen Server sind aus";
  if (systemModuleOn === false) return "Keine Auslastung: Modul „System“ ist aus";
  // Hat der Server nie geantwortet, kommt auch nichts mehr nach: erst die Verbindung klaeren, nicht auf Kurven warten.
  if (hostHealth(host.status) === "problem" && neverAnswered(host)) return "Noch keine Verbindung: prüfe zuerst den Zugang";
  if (systemModuleOn && hostHealth(host.status) !== "problem") return "Noch keine Messwerte";
  return null;
}

export function Cockpit() {
  const user = useAuthStore((s) => s.user);
  const canReadHosts = useAuthStore((s) => s.hasPermission("hosts.read"));
  const canExecute = useAuthStore((s) => s.hasPermission("hosts.execute"));
  const canWriteHosts = useAuthStore((s) => s.hasPermission("hosts.write"));
  const canWriteApps = useAuthStore((s) => s.hasPermission("apps.write"));
  const { data: hosts, isLoading: hostsLoading } = useHosts(canReadHosts);
  const { data: overview, isLoading: overviewLoading } = useOverview(canReadHosts);
  const { data: terminalHosts } = useTerminalHosts(canExecute);
  const { data: latest, isLoading: latestLoading, isError: latestError } = useLatestMetrics(canReadHosts);
  const { data: pages } = usePages();
  const extensionLabel = useExtensionLabel((overview?.attention ?? []).some((a) => a.source_ext_id));

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
  const firstName = (user?.display_name ?? user?.username ?? "").split(" ")[0];
  const loading = canReadHosts && (hostsLoading || overviewLoading);
  // Ohne einen einzigen Server gibt es nichts, was "rund laufen" koennte -- stattdessen zeigt das
  // Cockpit, was als Naechstes zu tun ist.
  const noHosts = canReadHosts && !loading && hosts !== undefined && allHosts.length === 0;

  return (
    <section className="relative isolate" aria-label="Cockpit">
      <div className="nodvard-deck-glow pointer-events-none absolute inset-x-0 top-0 -z-10 h-96" />
      <div className="nodvard-deck-grid pointer-events-none absolute inset-x-0 top-0 -z-10 h-96" />

      <div className="px-4 pb-2 pt-6 sm:px-6 sm:pt-8">
        <p className="text-xs font-medium uppercase tracking-[0.16em] text-white/45">
          {new Date().toLocaleDateString("de-DE", { weekday: "long", day: "numeric", month: "long" })}
        </p>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight">
          {greeting()}{firstName ? `, ${firstName}` : ""}
        </h1>
        {canReadHosts && (
          <p className="mt-2 flex items-center gap-2 text-sm text-white/70" data-testid="cockpit-status">
            {loading ? (
              <span className="opacity-60">Lagebild wird geladen …</span>
            ) : noHosts ? (
              <>
                <Server size={16} className="text-white/55" />
                Noch kein Server eingerichtet.
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
      </div>

      <FirstStepsCard />

      {canReadHosts && (
        <div className="grid grid-cols-2 gap-3 px-4 pt-5 sm:px-6 md:grid-cols-3 xl:grid-cols-5">
          <StatCard
            icon={<Server size={16} />} label="Server" value={loading ? "…" : noHosts ? "0" : `${online}/${allHosts.length}`}
            sub={
              problemHosts.length ? `${problemHosts.length} nicht erreichbar`
                : noHosts ? "noch keiner angelegt"
                : unknownHosts.length ? `online · ${unknownHosts.length} noch nicht geprüft`
                : "online"
            }
            tone={problemHosts.length ? "danger" : noHosts || unknownHosts.length ? "neutral" : "good"}
          />
          <StatCard
            icon={<Boxes size={16} />} label="Dienste"
            value={overview ? (services.length === 0 ? "–" : `${overview.services_running}/${services.length}`) : "…"}
            sub={
              servicesDown.length && services.length === 0 ? `unbekannt · ${servicesDownText}`
                : servicesDown.length ? `Container laufen · ${servicesDownText}`
                : overview && services.length === 0 ? "noch keine erfasst"
                : "Container laufen"
            }
            tone={
              servicesDown.length ? "warn"
                : noHosts || services.length === 0 ? "neutral"
                : overview && overview.services_running < services.length ? "warn" : "good"
            }
          />
          {(backups || loading) && (
            <StatCard
              icon={<DatabaseBackup size={16} />} label="Backups"
              value={backups ? (backups.total === 0 && backupsUnreachable.length > 0 ? "–" : `${backups.ok}/${backups.total}`) : "…"}
              sub={backups ? backupSummary(backups.failed, backupsUnreachable.length, backups.unknown) : ""}
              tone={backups?.failed ? "danger" : backupsUnreachable.length || backups?.unknown ? "warn" : "good"}
            />
          )}
          <StatCard
            to="/notifications" icon={<Bell size={16} />} label="Meldungen" value={overview ? String(overview.unread_notifications) : "…"}
            sub="ungelesen" tone={attention.length ? "warn" : "neutral"}
          />
          <StatCard
            to="/actions" icon={<ShieldCheck size={16} />} label="Freigaben" value={overview ? String(overview.pending_actions) : "…"}
            sub={overview?.pending_actions ? "warten auf dich" : "nichts offen"} tone={overview?.pending_actions ? "warn" : "neutral"}
          />
        </div>
      )}

      <div className="grid gap-6 px-4 pt-8 sm:px-6 xl:grid-cols-3">
        {canReadHosts && (
          <div className="xl:col-span-2">
            <SectionTitle icon={<Server size={15} />}>Infrastruktur</SectionTitle>
            {nodes.length + servers.length > 0 && (
              <div className="mb-3 grid gap-3 sm:grid-cols-2 2xl:grid-cols-3" data-testid="host-cards">
                {nodes.map((node) => <NodeCard key={node.id} host={node} canTerminal={terminalIds.has(node.id)} />)}
                {servers.map((host) => (
                  <ServerCard
                    key={host.id}
                    host={host}
                    latest={latest!.hosts[host.id]}
                    unconfirmed={latestError}
                    canTerminal={terminalIds.has(host.id)}
                  />
                ))}
              </div>
            )}
            {!latestLoading && (
              <div className="grid gap-2 sm:grid-cols-2 2xl:grid-cols-3">
                {machines.map((host) => (
                  <MachineRow key={host.id} host={host} canTerminal={terminalIds.has(host.id)} hint={metricsHint(host, systemModuleOn)} />
                ))}
              </div>
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

        <div className="flex flex-col gap-6">
          {canReadHosts && (
            <div>
              <SectionTitle icon={<AlertTriangle size={15} />}>Braucht Aufmerksamkeit</SectionTitle>
              <div className="panel divide-y divide-white/[0.06]" data-testid="attention">
                {!loading && issues === 0 && (
                  <p className="flex items-center gap-2 p-4 text-sm text-white/60">
                    <CheckCircle2 size={16} className="text-emerald-400" /> Nichts offen – genieß die Ruhe.
                  </p>
                )}
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
            </div>
          )}

          <div>
            <SectionTitle icon={<Icon name="layout-grid" size={15} />}>Module</SectionTitle>
            <div className="grid grid-cols-3 gap-2" data-testid="modules">
              {[
                { to: "/files", icon: "folder-open", title: "Dateien" },
                ...(canExecute ? [{ to: "/terminal", icon: "terminal", title: "Terminal" }] : []),
                ...quickPages.map((p) => ({ to: `/ext/${p.ext_id}${p.path}`, icon: p.icon ?? "box", title: p.title })),
              ].map((m) => (
                <Link key={m.to} to={m.to} className="panel panel-hover flex flex-col items-center gap-1.5 px-2 py-3 text-center">
                  <span className="accent-soft grid h-9 w-9 place-items-center rounded-lg"><Icon name={m.icon} size={17} /></span>
                  <span className="w-full break-words text-xs">{m.title}</span>
                </Link>
              ))}
            </div>
          </div>
        </div>
      </div>

      {canReadHosts && overview && (apps.length > 0 || canWriteApps) && (
        <div className="px-4 pt-8 sm:px-6">
          <AppsSection apps={apps} hosts={allHosts} canWrite={canWriteApps} />
        </div>
      )}
    </section>
  );
}
