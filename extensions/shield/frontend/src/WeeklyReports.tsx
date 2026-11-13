import { useEffect, useState } from "react";

import { deck } from "../../../_shared/frontend/src/deck";
import { errorMessage, fetchJson, LoadError } from "../../../_shared/frontend/src/loadError";
import { useUrlParams } from "../../../_shared/frontend/src/location";
import { Card, EmptyState } from "../../../_shared/frontend/src/ui";
import { useForHost, when } from "./api";
import { EXT_API } from "./ids";

interface ReportSummary {
  id: string;
  created_at: number;
  period_start: number;
  period_end: number;
  host_count: number;
  summary: { updates: number; security_updates: number; new_findings: number; resolved_findings: number; unknown_update_hosts: number; unknown_security_update_hosts: number };
}

interface ReportFinding { id: string; path: string; signature: string; status: string }
interface ReportHost {
  host_id: string;
  host_name: string;
  updates: { open: number; security: number | null; checked_at: number; error: string | null } | null;
  scan: { status: string; infected: number; files_scanned: number | null; started_at: number; finished_at: number | null } | null;
  fail2ban: { status: string; banned: number | null; checked_at: number; error: string | null } | null;
  lynis: { hardening_index: number; created_at: number } | null;
  findings: { new: ReportFinding[]; resolved: ReportFinding[] };
  certificates: null;
}
interface Report extends ReportSummary { hosts: ReportHost[]; certificates_note: string }

const REPORTS = `${EXT_API}/reports`;
const date = (ts: number) => new Date(ts * 1000).toLocaleDateString("de-DE");
const SCANS: Record<string, string> = { clean: "Sauber", infected: "Bedrohung gefunden", error: "Fehlgeschlagen", running: "Läuft" };
const FINDINGS: Record<string, string> = { detected: "Offen", quarantined: "In Quarantäne", restored: "Wiederhergestellt", deleted: "Gelöscht", ignored: "Ignoriert" };
const FAIL2BAN: Record<string, string> = { running: "Läuft", stopped: "Gestoppt", none: "Nicht installiert", noaccess: "Nicht lesbar" };

function Findings({ title, rows }: { title: string; rows: ReportFinding[] }) {
  return (
    <div>
      <h4 className="text-sm font-medium">{title} ({rows.length})</h4>
      {rows.length ? (
        <ul className="mt-2 space-y-2 text-xs">
          {rows.map((r) => <li key={r.id}><p className="break-all text-white/80">{r.path}</p><p className="text-white/50">{r.signature} · {FINDINGS[r.status] ?? r.status}</p></li>)}
        </ul>
      ) : <p className="mt-1 text-xs text-white/50">Keine in diesem Zeitraum</p>}
    </div>
  );
}

function HostReport({ host: h }: { host: ReportHost }) {
  return (
    <div data-testid={`report-host-${h.host_id}`}>
      <Card title={h.host_name}>
        <dl className="grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-3 [&_dt]:text-xs [&_dt]:text-white/50 [&_dd]:mt-1">
          <div>
            <dt>Updates</dt>
            <dd>{h.updates ? `${h.updates.open} offen, ${h.updates.security === null ? "Sicherheitsupdates unbekannt" : `davon ${h.updates.security} Sicherheitsupdate${h.updates.security === 1 ? "" : "s"}`}` : "Keine gespeicherten Update-Daten"}</dd>
            {h.updates && <dd className="text-xs text-white/50">Stand: {when(h.updates.checked_at)}</dd>}
            {h.updates?.error && <dd className="text-xs text-amber-300">{h.updates.error}</dd>}
          </div>
          <div>
            <dt>Letzter Virenscan</dt>
            <dd>{h.scan ? `${SCANS[h.scan.status] ?? h.scan.status}: ${h.scan.infected} Fund${h.scan.infected === 1 ? "" : "e"}` : "Noch kein gespeicherter Virenscan"}</dd>
            {h.scan && <dd className="text-xs text-white/50">{h.scan.files_scanned === null ? "Dateizahl unbekannt" : `${h.scan.files_scanned} Dateien geprüft`} · {when(h.scan.finished_at ?? h.scan.started_at)}</dd>}
          </div>
          <div>
            <dt>Fail2ban-Sperren</dt>
            <dd>{h.fail2ban ? h.fail2ban.banned === null ? "Anzahl der Sperren unbekannt" : `${h.fail2ban.banned} gesperrte Adressen` : "Keine gespeicherten Fail2ban-Daten"}</dd>
            {h.fail2ban && <dd className="text-xs text-white/50">{FAIL2BAN[h.fail2ban.status] ?? h.fail2ban.status} · Stand: {when(h.fail2ban.checked_at)}</dd>}
            {h.fail2ban?.error && <dd className="text-xs text-amber-300">{h.fail2ban.error}</dd>}
          </div>
          <div>
            <dt>Härtungswert (Lynis)</dt>
            <dd>{h.lynis ? `${h.lynis.hardening_index} / 100` : "Noch kein erfolgreicher Härtungstest"}</dd>
            {h.lynis && <dd className="text-xs text-white/50">Stand: {when(h.lynis.created_at)}</dd>}
          </div>
          <div><dt>Bald ablaufende Zertifikate</dt><dd>Keine gespeicherten Zertifikatsdaten</dd></div>
        </dl>
        <div className="mt-5 grid gap-4 border-t border-white/[0.06] pt-4 sm:grid-cols-2">
          <Findings title="Neue Funde" rows={h.findings.new} />
          <Findings title="Erledigte Funde" rows={h.findings.resolved} />
        </div>
      </Card>
    </div>
  );
}

export function WeeklyReports() {
  const [params, updateUrl] = useUrlParams();
  const [history, setHistory] = useState<{ items: ReportSummary[]; total: number } | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyRetry, setHistoryRetry] = useState(0);
  const [report, setReport] = useState<Report | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);
  const [reportRetry, setReportRetry] = useState(0);
  const selected = params.get("report") || history?.items[0]?.id || null;
  const hosts = useForHost(report?.id === selected ? report.hosts : []);

  useEffect(() => {
    const controller = new AbortController();
    setHistoryError(null);
    fetchJson<{ items: ReportSummary[]; total: number }>(REPORTS, { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setHistory(data); })
      .catch((err: unknown) => { if (!controller.signal.aborted) setHistoryError(errorMessage(err)); });
    return () => controller.abort();
  }, [historyRetry]);

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    setReport(null);
    setReportError(null);
    fetchJson<Report>(`${REPORTS}/${encodeURIComponent(selected)}`, { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setReport(data); })
      .catch((err: unknown) => { if (!controller.signal.aborted) setReportError(errorMessage(err)); });
    return () => controller.abort();
  }, [selected, reportRetry]);

  return (
    <div className="space-y-4">
      <Card title="Wochenbericht" actions={deck().hasPermission("extensions.manage") && <a href="/settings/extensions/shield" className="text-xs text-white/70 underline">Zeitplan einstellen</a>}>
        <p className="text-sm text-white/70">Verlauf der letzten 12 Wochen. Jeder Bericht hält die bereits gespeicherten Ergebnisse fest und startet keine neuen Scans.</p>
        <p className="mt-1 text-xs text-white/50">Der Zeitplan gilt für die Zeitzone deiner Installation. Standard: Montag um 08:00 Uhr.</p>
      </Card>
      {historyError && <LoadError what="Der Berichtsverlauf" error={historyError} onRetry={() => setHistoryRetry((n) => n + 1)} />}
      {!history && !historyError && <p className="text-sm text-white/50">Lade Wochenberichte …</p>}
      {history?.items.length === 0 && !selected && <EmptyState icon="file" title="Noch kein Wochenbericht" text="Der erste Bericht erscheint zum eingestellten Wochenzeitpunkt. Bereits vorhandene Prüfergebnisse werden dann zusammengefasst." />}
      {!!history?.items.length && (
        <nav aria-label="Wochenberichte" className="flex flex-wrap gap-2">
          {history.items.map((r) => (
            <button key={r.id} type="button" aria-pressed={selected === r.id} onClick={() => updateUrl({ report: r.id })}
              className={`rounded-lg border px-3 py-2 text-left text-sm ${selected === r.id ? "border-[var(--color-accent)] bg-white/10" : "border-white/10 text-white/60 hover:bg-white/[0.06]"}`}>
              Bericht vom {date(r.created_at)}<span className="mt-1 block text-xs text-white/50">{r.host_count} Server</span>
            </button>
          ))}
        </nav>
      )}
      {reportError && <LoadError what="Der Wochenbericht" error={reportError} onRetry={() => setReportRetry((n) => n + 1)} />}
      {selected && !report && !reportError && <p className="text-sm text-white/50">Lade Bericht …</p>}
      {report?.id === selected && (
        <>
          <div className="text-sm text-white/65">
            <p>{date(report.period_start)} bis {date(report.period_end)} · Erstellt: {when(report.created_at)}</p>
            <p className="mt-1">{report.summary.new_findings} neue Funde · {report.summary.resolved_findings} erledigte Funde</p>
            <p className="mt-1 text-xs text-white/50">{report.certificates_note}</p>
          </div>
          {hosts.map((h) => <HostReport key={h.host_id} host={h} />)}
          {hosts.length === 0 && <p className="text-sm text-white/50">Für diese Ansicht enthält der Bericht keine Serverdaten.</p>}
        </>
      )}
    </div>
  );
}
