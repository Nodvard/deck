/**
 * Nodvard Shield (Erweiterung nexus-soc) -- PageSpec `component="SocPage"` (siehe nodvard_deck_ext_nexus_soc/__init__.py).
 *
 * Virenschutz plus die bestehende KI-Container-Wache:
 * Uebersicht mit Schutzstatus je Server, Scans (ClamAV), Quarantaene, Haertung (Lynis)
 * und Container-Wache. Alles laeuft ueber `/ext/nexus-soc/defender/...`.
 */
import { useCallback, useContext, useEffect, useMemo, useState } from "react";

import { Badge, Button, Card, EmptyState, Icon, Notice, Page, Stat, inputClass, type Tone } from "../../../_shared/frontend/src/ui";
import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { useUrlParams } from "../../../_shared/frontend/src/location";
import { describeSchedule } from "../../../../frontend/src/components/SchedulePicker";
import { API, HostFilter, ago, call, proposeAndApprove, useForHost, when } from "./api";
import { deck } from "../../../_shared/frontend/src/deck";
import { ContainerWatch } from "./ContainerWatch";
import { GuardTab } from "./GuardTab";
import { UpdatesTab } from "./UpdatesTab";

interface ScanRow {
  id: string;
  host_id: string;
  host_name: string;
  kind: string;
  kind_label: string;
  paths: string[];
  trigger: string;
  status: string;
  files_scanned: number | null;
  infected: number;
  error: string | null;
  output_tail: string | null;
  started_at: number;
  finished_at: number | null;
}

interface HostRow {
  host_id: string;
  host_name: string;
  host_status: string;
  reachable?: boolean;
  error?: string;
  clamav_installed: boolean;
  clamav_version?: string | null;
  signature_version?: string | null;
  signature_date?: string | null;
  freshclam_active?: boolean | null;
  lynis_installed: boolean;
  quarantine_files?: number;
  last_scan: ScanRow | null;
  last_audit: { status: string; hardening_index: number | null; warnings: number; created_at: number; error: string | null } | null;
  scanning: boolean;
  auditing: boolean;
}

interface Overview {
  hosts: HostRow[];
  summary: {
    hosts: number; protected: number; open_threats: number; quarantined: number; neutralized_total: number;
    findings_30d: number; avg_hardening: number | null; score: number;
  };
  config: {
    auto_quarantine: boolean; realtime_enabled: boolean; watch_interval_min: number;
    quick_scan_cron: string; deep_scan_cron: string; audit_cron: string;
  };
}

interface Finding {
  id: string;
  host_id: string;
  host_name: string;
  path: string;
  signature: string;
  status: string;
  quarantine_path: string | null;
  note: string | null;
  detected_at: number;
  status_changed_at: number | null;
}

interface AuditRow {
  id: string;
  host_id: string;
  host_name: string;
  status: string;
  hardening_index: number | null;
  warnings: string[];
  suggestions: string[];
  error: string | null;
  created_at: number;
}

type Tab = "overview" | "scans" | "quarantine" | "hardening" | "updates" | "guard" | "containers";

const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Übersicht" },
  { id: "scans", label: "Scans" },
  { id: "quarantine", label: "Quarantäne" },
  { id: "hardening", label: "Härtung" },
  { id: "updates", label: "Updates" },
  { id: "guard", label: "Einbruchschutz" },
  { id: "containers", label: "Container-Wache" },
];

const SCAN_STATUS: Record<string, { label: string; tone: Tone }> = {
  running: { label: "läuft", tone: "info" },
  clean: { label: "sauber", tone: "good" },
  infected: { label: "Bedrohung gefunden", tone: "bad" },
  error: { label: "Fehler", tone: "warn" },
};

const FINDING_STATUS: Record<string, { label: string; tone: Tone }> = {
  detected: { label: "offen", tone: "bad" },
  quarantined: { label: "in Quarantäne", tone: "warn" },
  restored: { label: "wiederhergestellt", tone: "neutral" },
  deleted: { label: "gelöscht", tone: "good" },
  ignored: { label: "ignoriert", tone: "neutral" },
};

function ScoreRing({ score }: { score: number }) {
  const r = 42;
  const c = 2 * Math.PI * r;
  const tone = score >= 80 ? "#34d399" : score >= 50 ? "#fbbf24" : "#f87171";
  const label = score >= 80 ? "Gut geschützt" : score >= 50 ? "Verbesserungsbedarf" : "Handlungsbedarf";
  return (
    <div className="flex items-center gap-5">
      <svg width="104" height="104" viewBox="0 0 104 104" role="img" aria-label={`Schutzwert ${score} von 100`}>
        <circle cx="52" cy="52" r={r} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="9" />
        <circle
          cx="52" cy="52" r={r} fill="none" stroke={tone} strokeWidth="9" strokeLinecap="round"
          strokeDasharray={`${(c * score) / 100} ${c}`} transform="rotate(-90 52 52)"
        />
        <text x="52" y="58" textAnchor="middle" className="fill-white text-[22px] font-semibold">{score}</text>
      </svg>
      <div>
        <p className="text-xs uppercase tracking-wider text-white/45">Schutzwert</p>
        <p className="text-lg font-semibold" style={{ color: tone }}>{label}</p>
        <p className="mt-1 max-w-xs text-xs text-white/45">Aus Abdeckung (ClamAV), aktuellen Scans, offenen Funden und Härtungsindex.</p>
      </div>
    </div>
  );
}

export function SocPage(): JSX.Element {
  const unmountSignal = useUnmountSignal();
  // Reiter und Server-Filter kommen aus der Adresszeile (`?tab=`, `?host=`) und folgen ihr:
  // ein Link auf die schon offene Seite wechselt sie, ein Klick im Reiterstreifen schreibt sie
  // zurueck (replaceState) -- Neuladen und Lesezeichen behalten den Stand.
  const [urlParams, updateUrl] = useUrlParams();
  const tabParam = urlParams.get("tab");
  const tab: Tab = TABS.find((x) => x.id === tabParam)?.id ?? "overview";
  const setTab = useCallback((t: Tab) => updateUrl({ tab: t === "overview" ? null : t }), [updateUrl]);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const canManage = deck().hasPermission("soc.manage");
  const hostFilter = urlParams.get("host") || null;
  const showAllHosts = () => updateUrl({ host: null });

  const loadOverview = useCallback((refresh = false) => {
    call<Overview>(`${API}/overview${refresh ? "?refresh=true" : ""}`)
      .then((o) => { setOverview(o); setError(null); })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(() => { loadOverview(); }, [loadOverview]);

  // Am Handy liegt der aktive Reiter sonst ausserhalb des sichtbaren Streifens.
  useEffect(() => {
    document.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.scrollIntoView?.({ block: "nearest", inline: "center" });
  }, [tab]);

  // Kurzlage aus Update-Zentrale und Einbruchschutz fuer Reiter-Zaehler und Uebersicht.
  const [side, setSide] = useState<SideSummary>({ updates: null, guard: null });
  useEffect(() => {
    call<{ summary: SideSummary["updates"] }>(`${API}/updates`).then((u) => setSide((p) => ({ ...p, updates: u.summary }))).catch(() => undefined);
    call<{ summary: SideSummary["guard"] }>(`${API}/guard`).then((g) => setSide((p) => ({ ...p, guard: g.summary }))).catch(() => undefined);
  }, [tab]);
  useEffect(() => {
    const busy = overview?.hosts.some((h) => h.scanning || h.auditing);
    if (!busy) return;
    const t = setInterval(() => loadOverview(), 5000);
    return () => clearInterval(t);
  }, [overview, loadOverview]);

  async function run(label: string, fn: () => Promise<string>) {
    setNotice(null);
    try {
      const text = await fn();
      setNotice({ kind: text.startsWith("Fehlgeschlagen") ? "error" : "ok", text: `${label}: ${text}` });
      loadOverview();
    } catch (err) {
      setNotice({ kind: "error", text: `${label}: ${err instanceof Error ? err.message : String(err)}` });
    }
  }

  const startScan = (kind: "quick" | "deep", hostIds: string[] | "all" = "all") =>
    run(kind === "quick" ? "Schnellscan" : "Tiefenscan", async () => {
      const r = await call<{ scans: string[] }>(`${API}/scans`, { method: "POST", body: JSON.stringify({ kind, host_ids: hostIds }) });
      return r.scans.length ? `${r.scans.length} Server werden geprüft.` : "Läuft bereits oder kein Server verfügbar.";
    });

  const startAudit = (hostIds: string[] | "all" = "all") =>
    run("Härtungs-Audit", async () => {
      const r = await call<{ hosts: number }>(`${API}/audits`, { method: "POST", body: JSON.stringify({ host_ids: hostIds }) });
      return `${r.hosts} Server werden geprüft (dauert einige Minuten).`;
    });

  const sendBriefing = () =>
    run("Briefing", async () => {
      const r = await call<{ title: string }>(`${API}/briefing`, { method: "POST" });
      return `gesendet – „${r.title}“`;
    });

  const install = (host: HostRow, pkg: "clamav" | "lynis" | "signatures") =>
    run(pkg === "signatures" ? `Signaturen (${host.host_name})` : `${pkg === "clamav" ? "ClamAV" : "Lynis"} installieren (${host.host_name})`, async () => {
      const ok = await deck().confirmDialog(
        pkg === "signatures" ? `Virensignaturen auf „${host.host_name}“ jetzt aktualisieren? Das automatische Signatur-Update wird dabei eingeschaltet.` : `${pkg === "clamav" ? "ClamAV" : "Lynis"} auf „${host.host_name}“ installieren?`,
        { confirmLabel: pkg === "signatures" ? "Aktualisieren" : "Installieren" },
      );
      if (!ok) return "abgebrochen.";
      const text = await proposeAndApprove(`${API}/hosts/${host.host_id}/install`, { package: pkg }, pkg === "signatures" ? "low" : "medium", unmountSignal());
      loadOverview(true);
      return text;
    });

  return (
    <Page
      title="Nodvard Shield"
      description="Virenschutz, Quarantäne und Härtung für alle Server – plus die KI-Container-Wache (Nodvard KI) für Docker-Container."
      actions={canManage && (
        <>
          <Button onClick={() => void sendBriefing()}>Briefing senden</Button>
          <Button onClick={() => void startAudit()}><Icon name="clock" size={14} /> Härtungs-Audit</Button>
          <Button onClick={() => void startScan("deep")}><Icon name="search" size={14} /> Tiefenscan</Button>
          <Button variant="primary" onClick={() => void startScan("quick")}><Icon name="play" size={14} /> Schnellscan starten</Button>
        </>
      )}
    >
      <nav role="tablist" aria-label="Bereiche" className="mb-5 flex gap-1 overflow-x-auto border-b border-white/[0.08] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`-mb-px whitespace-nowrap border-b-2 px-3.5 py-2 text-sm transition ${
              tab === t.id ? "border-[var(--color-accent)] text-white" : "border-transparent text-white/55 hover:text-white"
            }`}
          >
            {t.label}
            {t.id === "quarantine" && overview && overview.summary.open_threats > 0 && (
              <span className="ml-1.5 rounded-full bg-red-500/80 px-1.5 text-[10px] text-white">{overview.summary.open_threats}</span>
            )}
            {t.id === "updates" && !!side.updates?.security && (
              <span className="ml-1.5 rounded-full bg-red-500/80 px-1.5 text-[10px] text-white" title="Sicherheitsupdates">{side.updates.security}</span>
            )}
            {t.id === "guard" && !!side.guard?.open_events && (
              <span className="ml-1.5 rounded-full bg-red-500/80 px-1.5 text-[10px] text-white" title="offene Ereignisse">{side.guard.open_events}</span>
            )}
          </button>
        ))}
      </nav>

      {hostFilter && (
        <div className="mb-4 flex flex-wrap items-center gap-2 rounded-lg border border-[color-mix(in_srgb,var(--color-accent)_35%,transparent)] bg-[color-mix(in_srgb,var(--color-accent)_10%,transparent)] px-3 py-2 text-sm" data-testid="host-filter">
          <span>Ansicht für <strong>{overview?.hosts.find((h) => h.host_id === hostFilter)?.host_name ?? "diesen Server"}</strong></span>
          <span className="flex-1" />
          <button type="button" onClick={showAllHosts} className="text-xs text-white/70 hover:text-white hover:underline">Alle Server anzeigen</button>
        </div>
      )}
      <HostFilter.Provider value={hostFilter}>
      {notice && <Notice text={notice.text} kind={notice.kind} onClose={() => setNotice(null)} />}
      {error && ["overview", "scans", "quarantine", "hardening"].includes(tab) && <Notice text={`Fehler: ${error}`} />}

      {tab === "overview" && overview && (
        <OverviewTab overview={overview} canManage={canManage} onRefresh={() => loadOverview(true)} onScan={(h) => void startScan("quick", [h.host_id])}
          onAudit={(h) => void startAudit([h.host_id])} onInstall={(h, p) => void install(h, p)} onOpenThreats={() => setTab("quarantine")} side={side} onOpen={setTab} />
      )}
      {tab === "overview" && !overview && !error && <p className="text-sm text-white/50">Lade …</p>}
      {tab === "scans" && <ScansTab hosts={overview?.hosts ?? []} canManage={canManage} onStarted={() => loadOverview()} />}
      {tab === "quarantine" && <QuarantineTab canManage={canManage} onChanged={() => loadOverview()} />}
      {tab === "hardening" && <HardeningTab canManage={canManage} onAudit={() => void startAudit()} />}
      {tab === "updates" && <UpdatesTab canManage={canManage} />}
      {tab === "guard" && <GuardTab canManage={canManage} />}
      {tab === "containers" && <ContainerWatch />}
      </HostFilter.Provider>
    </Page>
  );
}

interface SideSummary {
  updates: { hosts: number; checked: number; up_to_date: number; packages: number; security: number; reboot: number } | null;
  guard: { failed_24h: number; banned: number; open_events: number; fail2ban_running: number; hosts: number } | null;
}

function SideLink({ title, text, tone, onClick }: { title: string; text: string; tone: "good" | "warn" | "bad"; onClick: () => void }) {
  const dot = tone === "good" ? "bg-emerald-400" : tone === "warn" ? "bg-amber-400" : "bg-red-400";
  return (
    <button type="button" onClick={onClick} className="panel flex items-center gap-3 px-4 py-3 text-left transition hover:bg-white/[0.04]">
      <span className={`h-2.5 w-2.5 flex-none rounded-full ${dot}`} />
      <span className="min-w-0 flex-1">
        <span className="block text-xs text-white/50">{title}</span>
        <span className="block truncate text-sm">{text}</span>
      </span>
      <span className="text-white/35">→</span>
    </button>
  );
}

function OverviewTab({
  overview, canManage, onRefresh, onScan, onAudit, onInstall, onOpenThreats, side, onOpen,
}: {
  side: SideSummary;
  onOpen: (tab: Tab) => void;
  overview: Overview;
  canManage: boolean;
  onRefresh: () => void;
  onScan: (h: HostRow) => void;
  onAudit: (h: HostRow) => void;
  onInstall: (h: HostRow, pkg: "clamav" | "lynis" | "signatures") => void;
  onOpenThreats: () => void;
}) {
  const s = overview.summary;
  const c = overview.config;
  const hosts = useForHost(overview.hosts);
  return (
    <>
      <div className="mb-5 grid grid-cols-1 gap-4 lg:grid-cols-[auto_1fr]">
        <div className="panel flex items-center px-6 py-4"><ScoreRing score={s.score} /></div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="Geschützte Server" value={`${s.protected} / ${s.hosts}`} hint="ClamAV installiert" tone={s.protected < s.hosts ? "warn" : "good"} />
          <button type="button" onClick={onOpenThreats} className="text-left [&>div]:h-full">
            <Stat label="Offene Bedrohungen" value={s.open_threats} hint={s.open_threats ? "Jetzt prüfen →" : "keine"} tone={s.open_threats ? "bad" : "good"} />
          </button>
          <Stat label="Neutralisiert" value={s.neutralized_total} hint={`${s.quarantined} in Quarantäne · ${s.findings_30d} Funde in 30 Tagen`} />
          <Stat label="Härtung (Ø Lynis)" value={s.avg_hardening ?? "–"} hint="von 100" tone={s.avg_hardening == null ? undefined : s.avg_hardening >= 70 ? "good" : "warn"} />
        </div>
      </div>

      {(side.updates?.checked || side.guard) && (
        <div className="mb-5 grid gap-3 md:grid-cols-2" data-testid="side-summary">
          {!!side.updates?.checked && (
            <SideLink
              title="Updates"
              tone={side.updates.security ? "bad" : side.updates.packages || side.updates.reboot ? "warn" : "good"}
              text={side.updates.security
                ? `${side.updates.security} Sicherheitsupdate(s) offen`
                : side.updates.packages ? `${side.updates.packages} Update(s) offen` : `Alle ${side.updates.hosts} Server aktuell`}
              onClick={() => onOpen("updates")}
            />
          )}
          {side.guard && (
            <SideLink
              title="Einbruchschutz"
              tone={side.guard.open_events ? "bad" : side.guard.fail2ban_running < side.guard.hosts ? "warn" : "good"}
              text={side.guard.open_events
                ? `${side.guard.open_events} offene(s) Ereignis(se)`
                : `ruhig · ${side.guard.failed_24h} SSH-Fehlversuche in 24 Std., ${side.guard.banned} gesperrt`}
              onClick={() => onOpen("guard")}
            />
          )}
        </div>
      )}

      <div className="mb-5 flex flex-wrap gap-2 text-xs">
        <Badge tone={c.realtime_enabled ? "good" : "warn"}>Echtzeit-Wächter {c.realtime_enabled ? `aktiv (alle ${c.watch_interval_min} Min.)` : "aus"}</Badge>
        <Badge tone={c.auto_quarantine ? "good" : "warn"}>Automatische Quarantäne {c.auto_quarantine ? "an" : "aus"}</Badge>
        <Badge>Schnellscan: {describeSchedule(c.quick_scan_cron, "aus")}</Badge>
        <Badge>Tiefenscan: {describeSchedule(c.deep_scan_cron, "aus")}</Badge>
        <Badge>Audit: {describeSchedule(c.audit_cron, "aus")}</Badge>
        <a href="/settings/extensions/nexus-soc" className="text-white/50 underline-offset-2 hover:text-white hover:underline">Einstellungen ändern</a>
      </div>

      <Card title="Server" padded={false} actions={<Button small variant="ghost" onClick={onRefresh}><Icon name="refresh" size={12} /> Status neu prüfen</Button>}>
        {hosts.length === 0 ? (
          <p className="px-5 py-6 text-sm text-white/50">
            Keine Linux-Server mit SSH-Zugang gefunden. Einrichten unter{" "}
            {deck().hasPermission("hosts.write") ? (
              <a href="/settings/hosts" className="underline underline-offset-2 hover:text-white">Einstellungen → Server &amp; Zugänge</a>
            ) : (
              "Einstellungen → Server & Zugänge"
            )}
            .
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wider text-white/40">
                <tr className="border-b border-white/[0.06]">
                  <th className="px-5 py-2.5 font-medium">Server</th>
                  <th className="px-3 py-2.5 font-medium">Virenschutz</th>
                  <th className="px-3 py-2.5 font-medium">Letzter Scan</th>
                  <th className="px-3 py-2.5 font-medium">Härtung</th>
                  <th className="px-3 py-2.5 font-medium" />
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {hosts.map((h) => (
                  <tr key={h.host_id} data-testid={`host-${h.host_id}`}>
                    <td className="px-5 py-3">
                      <p className="font-medium">{h.host_name}</p>
                      {h.reachable === false && <p className="text-xs text-amber-300">nicht erreichbar{h.error ? ` – ${h.error}` : ""}</p>}
                    </td>
                    <td className="px-3 py-3">
                      {h.clamav_installed ? (
                        <>
                          <Badge tone="good">ClamAV {h.clamav_version}</Badge>
                          <p className="mt-0.5 text-xs text-white/45">
                            Signaturen {h.signature_version ?? "?"}{h.signature_date ? ` · ${h.signature_date}` : ""}
                            {h.freshclam_active === false && <span className="text-amber-300"> · Auto-Update aus</span>}
                          </p>
                        </>
                      ) : h.reachable === false ? <Badge>unbekannt</Badge> : <Badge tone="bad">nicht installiert</Badge>}
                    </td>
                    <td className="px-3 py-3">
                      {h.scanning ? <Badge tone="info">Scan läuft …</Badge> : h.last_scan ? (
                        <>
                          <Badge tone={SCAN_STATUS[h.last_scan.status]?.tone ?? "neutral"}>{SCAN_STATUS[h.last_scan.status]?.label ?? h.last_scan.status}</Badge>
                          <p className="mt-0.5 text-xs text-white/45">{h.last_scan.kind_label} · {ago(h.last_scan.started_at)}</p>
                        </>
                      ) : <span className="text-xs text-white/40">noch nie</span>}
                    </td>
                    <td className="px-3 py-3">
                      {h.auditing ? <Badge tone="info">Audit läuft …</Badge> : h.last_audit?.hardening_index != null ? (
                        <div className="w-28">
                          <div className="flex justify-between text-xs"><span>{h.last_audit.hardening_index}</span><span className="text-white/40">{h.last_audit.warnings} Warn.</span></div>
                          <div className="mt-1 h-1.5 rounded-full bg-white/10">
                            <div className="h-1.5 rounded-full" style={{ width: `${h.last_audit.hardening_index}%`, background: h.last_audit.hardening_index >= 70 ? "#34d399" : "#fbbf24" }} />
                          </div>
                        </div>
                      ) : h.last_audit?.error ? <span className="text-xs text-amber-300">{h.last_audit.error}</span> : <span className="text-xs text-white/40">–</span>}
                    </td>
                    <td className="px-3 py-3">
                      {canManage && (
                        <div className="flex flex-wrap justify-end gap-1">
                          {h.clamav_installed && <Button small onClick={() => onScan(h)} disabled={h.scanning}>Scannen</Button>}
                          {h.clamav_installed && <Button small variant="ghost" onClick={() => onInstall(h, "signatures")}>Signaturen</Button>}
                          {!h.clamav_installed && h.reachable !== false && <Button small variant="primary" onClick={() => onInstall(h, "clamav")}>ClamAV installieren</Button>}
                          {!h.lynis_installed && h.reachable !== false && <Button small onClick={() => onInstall(h, "lynis")}>Lynis installieren</Button>}
                          {h.lynis_installed && <Button small variant="ghost" onClick={() => onAudit(h)} disabled={h.auditing}>Audit</Button>}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

/** Blendet die technischen Marker-Zeilen aus der Scan-Ausgabe aus: den Rückgabecode `@@scan-rc-…=0`
 *  und die alten `@@nexus-rc=…`-Reste in bereits gespeicherten Läufen. */
function visibleScanOutput(tail: string): string {
  return tail.split("\n").filter((line) => !line.startsWith("@@")).join("\n").trim();
}

function ScansTab({ hosts, canManage, onStarted }: { hosts: HostRow[]; canManage: boolean; onStarted: () => void }) {
  const [scans, setScans] = useState<ScanRow[] | null>(null);
  const [showWatch, setShowWatch] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [kind, setKind] = useState<"quick" | "deep" | "custom">("quick");
  const [target, setTarget] = useState("all");
  const [paths, setPaths] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const hostFilter = useContext(HostFilter);
  useEffect(() => { setTarget(hostFilter ?? "all"); }, [hostFilter]);

  const load = useCallback(() => {
    call<ScanRow[]>(`${API}/scans?include_watch=${showWatch}${hostFilter ? `&host=${encodeURIComponent(hostFilter)}` : ""}`).then(setScans).catch(() => setScans([]));
  }, [showWatch, hostFilter]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!scans?.some((s) => s.status === "running")) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [scans, load]);

  async function start() {
    setMsg(null);
    try {
      const body = { kind, host_ids: target === "all" ? "all" : [target], paths: kind === "custom" ? paths.split(/[\n,]/).map((p) => p.trim()).filter(Boolean) : null };
      const r = await call<{ scans: string[] }>(`${API}/scans`, { method: "POST", body: JSON.stringify(body) });
      setMsg({ kind: "ok", text: r.scans.length ? `${r.scans.length} Scan(s) gestartet.` : "Läuft bereits oder kein Server verfügbar." });
      load();
      onStarted();
    } catch (err) {
      setMsg({ kind: "error", text: err instanceof Error ? err.message : String(err) });
    }
  }

  return (
    <div className="space-y-5">
      {canManage && (
        <Card title="Scan starten" description="Schnellscan prüft typische Ablageorte (tmp, home, root), der Tiefenscan das ganze System – das kann Stunden dauern.">
          {msg && <Notice text={msg.text} kind={msg.kind} onClose={() => setMsg(null)} />}
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-sm">
              <span className="mb-1.5 block text-white/70">Art</span>
              <select aria-label="Scan-Art" value={kind} onChange={(e) => setKind(e.target.value as typeof kind)} className={`${inputClass} w-44`}>
                <option value="quick">Schnellscan</option>
                <option value="deep">Tiefenscan</option>
                <option value="custom">Bestimmte Ordner</option>
              </select>
            </label>
            <label className="text-sm">
              <span className="mb-1.5 block text-white/70">Server</span>
              <select aria-label="Scan-Ziel" value={target} onChange={(e) => setTarget(e.target.value)} className={`${inputClass} w-52`}>
                <option value="all">Alle Server</option>
                {hosts.map((h) => <option key={h.host_id} value={h.host_id}>{h.host_name}</option>)}
              </select>
            </label>
            {kind === "custom" && (
              <label className="min-w-[16rem] flex-1 text-sm">
                <span className="mb-1.5 block text-white/70">Ordner (Komma-getrennt)</span>
                <input aria-label="Ordner" value={paths} placeholder="/var/www, /srv/uploads" onChange={(e) => setPaths(e.target.value)} className={`${inputClass} font-mono`} />
              </label>
            )}
            <Button variant="primary" onClick={() => void start()}><Icon name="play" size={13} /> Starten</Button>
          </div>
        </Card>
      )}

      <Card
        title="Verlauf"
        padded={false}
        actions={
          <label className="flex items-center gap-2 text-xs text-white/55">
            <input type="checkbox" checked={showWatch} onChange={(e) => setShowWatch(e.target.checked)} /> saubere Wächter-Läufe zeigen
          </label>
        }
      >
        {!scans ? <p className="px-5 py-4 text-sm text-white/50">Lade …</p> : scans.length === 0 ? (
          <p className="px-5 py-6 text-sm text-white/50">Noch keine Scans.</p>
        ) : (
          <ul className="divide-y divide-white/[0.05]" data-testid="scan-list">
            {scans.map((s) => {
              const st = SCAN_STATUS[s.status] ?? { label: s.status, tone: "neutral" as Tone };
              const isOpen = open === s.id;
              const output = s.output_tail ? visibleScanOutput(s.output_tail) : "";
              return (
                <li key={s.id} className="px-5 py-3 text-sm">
                  <button type="button" onClick={() => setOpen(isOpen ? null : s.id)} className="flex w-full flex-wrap items-center gap-2 text-left">
                    <Icon name={isOpen ? "chevron-down" : "chevron-right"} size={14} className="text-white/40" />
                    <Badge tone={st.tone}>{st.label}</Badge>
                    <span className="font-medium">{s.host_name}</span>
                    <span className="text-white/50">{s.kind_label}</span>
                    {s.infected > 0 && <span className="text-red-300">{s.infected} Fund(e)</span>}
                    {s.files_scanned != null && <span className="text-xs text-white/40">{s.files_scanned.toLocaleString("de-DE")} Dateien</span>}
                    <span className="ml-auto text-xs text-white/40">{when(s.started_at)} · {s.trigger === "schedule" ? "Zeitplan" : "manuell"}</span>
                  </button>
                  {s.error && <p className="ml-6 mt-1 text-xs text-amber-300">{s.error}</p>}
                  {isOpen && (
                    <div className="ml-6 mt-2 space-y-2">
                      <p className="text-xs text-white/45">Ordner: <span className="font-mono">{s.paths.join(", ")}</span>{s.finished_at ? ` · Dauer ${Math.max(1, Math.round((s.finished_at - s.started_at) / 60))} Min.` : ""}</p>
                      {output && <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-black/40 p-2.5 font-mono text-[11px] text-white/75">{output}</pre>}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}

function QuarantineTab({ canManage, onChanged }: { canManage: boolean; onChanged: () => void }) {
  const unmountSignal = useUnmountSignal();
  const [items, setItems] = useState<Finding[] | null>(null);
  const [filter, setFilter] = useState<"active" | "all">("active");
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const load = useCallback(() => {
    call<Finding[]>(`${API}/findings`).then(setItems).catch(() => setItems([]));
  }, []);
  useEffect(() => { load(); }, [load]);

  const hostItems = useForHost(items ?? []);
  const visible = useMemo(
    () => hostItems.filter((f) => filter === "all" || f.status === "detected" || f.status === "quarantined"),
    [hostItems, filter],
  );

  async function act(f: Finding, action: "quarantine" | "ignore" | "restore" | "delete") {
    setMsg(null);
    const questions = {
      quarantine: `„${f.path}“ in Quarantäne verschieben?`,
      ignore: `Fund „${f.path}“ ignorieren? Die Datei bleibt, wo sie ist.`,
      restore: `„${f.path}“ wiederherstellen? Nur tun, wenn sicher ist, dass es ein Fehlalarm war.`,
      delete: `„${f.path}“ endgültig löschen?`,
    };
    const ok = await deck().confirmDialog(questions[action], { danger: action !== "quarantine", confirmLabel: { quarantine: "Verschieben", ignore: "Ignorieren", restore: "Wiederherstellen", delete: "Löschen" }[action] });
    if (!ok) return;
    try {
      let text = "Erledigt.";
      if (action === "quarantine" || action === "ignore") {
        await call(`${API}/findings/${f.id}/${action}`, { method: "POST" });
      } else {
        text = await proposeAndApprove(`${API}/findings/${f.id}/${action}`, {}, "medium", unmountSignal());
      }
      setMsg({ kind: text.startsWith("Fehlgeschlagen") ? "error" : "ok", text });
      load();
      onChanged();
    } catch (err) {
      setMsg({ kind: "error", text: err instanceof Error ? err.message : String(err) });
    }
  }

  if (!items) return <p className="text-sm text-white/50">Lade …</p>;
  if (items.length === 0) {
    return <EmptyState icon="package" title="Keine Funde" text="Bisher wurde keine Schadsoftware gefunden. Funde landen hier – bei aktivierter automatischer Quarantäne sind sie sofort unschädlich gemacht." />;
  }
  return (
    <>
      {msg && <Notice text={msg.text} kind={msg.kind} onClose={() => setMsg(null)} />}
      <Card
        title="Funde & Quarantäne"
        description="Dateien in Quarantäne sind unlesbar und nicht ausführbar (Rechte 000) im Tresor auf dem Server, auf dem sie gefunden wurden."
        padded={false}
        actions={
          <select aria-label="Filter" value={filter} onChange={(e) => setFilter(e.target.value as typeof filter)} className={`${inputClass} w-auto py-1 text-xs`}>
            <option value="active">Offen &amp; in Quarantäne</option>
            <option value="all">Alle, inkl. erledigt</option>
          </select>
        }
      >
        <ul className="divide-y divide-white/[0.05]" data-testid="findings">
          {visible.length === 0 && <li className="px-5 py-6 text-sm text-white/50">Nichts offen.</li>}
          {visible.map((f) => {
            const st = FINDING_STATUS[f.status] ?? { label: f.status, tone: "neutral" as Tone };
            return (
              <li key={f.id} className="flex flex-wrap items-center gap-3 px-5 py-3 text-sm">
                <div className="min-w-0 flex-1">
                  <p className="flex flex-wrap items-center gap-2">
                    <Badge tone={st.tone}>{st.label}</Badge>
                    <span className="font-medium text-red-200">{f.signature}</span>
                  </p>
                  <p className="mt-0.5 truncate font-mono text-xs text-white/70">{f.host_name}: {f.path}</p>
                  <p className="text-xs text-white/40">gefunden {when(f.detected_at)}{f.status_changed_at ? ` · geändert ${when(f.status_changed_at)}` : ""}</p>
                  {f.note && <p className="text-xs text-amber-300">{f.note}</p>}
                </div>
                {canManage && (
                  <div className="flex gap-1">
                    {f.status === "detected" && <Button small variant="primary" onClick={() => void act(f, "quarantine")}>In Quarantäne</Button>}
                    {f.status === "detected" && <Button small variant="ghost" onClick={() => void act(f, "ignore")}>Ignorieren</Button>}
                    {f.status === "quarantined" && <Button small onClick={() => void act(f, "restore")}>Wiederherstellen</Button>}
                    {f.status === "quarantined" && <Button small variant="danger" onClick={() => void act(f, "delete")}>Endgültig löschen</Button>}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </Card>
    </>
  );
}

function HardeningTab({ canManage, onAudit }: { canManage: boolean; onAudit: () => void }) {
  const [audits, setAudits] = useState<AuditRow[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => {
    call<AuditRow[]>(`${API}/audits`).then((a) => setAudits(a.sort((x, y) => (x.hardening_index ?? 0) - (y.hardening_index ?? 0)))).catch(() => setAudits([]));
  }, []);

  const shown = useForHost(audits ?? []);
  if (!audits) return <p className="text-sm text-white/50">Lade …</p>;
  if (shown.length === 0) {
    return (
      <EmptyState icon="clock" title="Noch kein Härtungs-Audit" text="Lynis prüft jeden Server auf unsichere Einstellungen (SSH, Passwortregeln, offene Dienste …) und vergibt einen Härtungsindex von 0 bis 100."
        action={canManage && <Button variant="primary" onClick={onAudit}>Audit jetzt starten</Button>} />
    );
  }
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {shown.map((a) => (
        <Card key={a.id} title={a.host_name} description={`Geprüft ${when(a.created_at)}`}
          actions={a.hardening_index != null && <span className={`text-2xl font-semibold ${a.hardening_index >= 70 ? "text-emerald-300" : "text-amber-300"}`}>{a.hardening_index}</span>}>
          {a.status !== "ok" ? <p className="text-sm text-amber-300">{a.error}</p> : (
            <>
              <p className="mb-2 text-xs uppercase tracking-wider text-white/40">Warnungen ({a.warnings.length})</p>
              {a.warnings.length === 0 ? <p className="mb-3 text-sm text-emerald-300">Keine Warnungen.</p> : (
                <ul className="mb-3 space-y-1 text-sm">{a.warnings.map((w) => <li key={w} className="text-red-200">• {w}</li>)}</ul>
              )}
              <button type="button" onClick={() => setOpen(open === a.id ? null : a.id)} className="flex items-center gap-1 text-xs text-white/55 hover:text-white">
                <Icon name={open === a.id ? "chevron-down" : "chevron-right"} size={12} /> {a.suggestions.length} Verbesserungsvorschläge
              </button>
              {open === a.id && <ul className="mt-2 max-h-72 space-y-1 overflow-auto text-xs text-white/70">{a.suggestions.map((s) => <li key={s}>• {s}</li>)}</ul>}
            </>
          )}
        </Card>
      ))}
    </div>
  );
}
