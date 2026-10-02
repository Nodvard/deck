/**
 * Container-Wache (Reiter auf der Nodvard-Shield-Seite, siehe SocPage.tsx).
 *
 * Bewusst kein Chat-UI -- das ist NICHT der Fokus dieser Seite. Der Chat-Endpunkt
 * (`POST /chat`) bleibt im Backend bestehen (wird z. B. weiterhin fuer jede
 * Vorfalls-Zusammenfassung intern genutzt, siehe `_propose_from_response()`),
 * hat aber absichtlich KEINE UI mehr hier. Diese Seite zeigt stattdessen
 * echtes SOC-Monitoring: Status/Statistiken, der Live-Vorfall-Feed, und was
 * die KI je Vorfall erkannt/vorgeschlagen hat (`ai_summary`).
 *
 * **Historie:** die Liste ist die durchsuchbare, dauerhafte Historie
 * (`GET .../history`, ueberlebt Neustarts): Freitext (trifft auch die
 * KI-Zusammenfassung), Status, Host, Zeitraum, seitenweise. Je Vorfall klappt der
 * Verlauf aus dem Kern-Audit-Log auf (`GET /audit?correlation_id=<id>`: erfasst,
 * KI-Vorschlag, Gate-Entscheidung, jeder Statuswechsel) -- nur mit `audit.read`.
 */
import { useCallback, useEffect, useState } from "react";

import { authedFetch } from "../../../_shared/frontend/src/api";
import { Badge, Button, Card, Icon, Notice, Stat, buttonClass, inputClass, type Tone } from "../../../_shared/frontend/src/ui";
import { deck } from "../../../_shared/frontend/src/deck";

/** Ältere Protokolleinträge tragen noch die eingefrorene Docker-Zeit ("... 4 seconds ago"). */
function withoutRelativeTime(text: string): string {
  return text.replace(/\s*(?:about\s+|less than\s+)?(?:an?|\d+)\s*(?:second|minute|hour|day|week|month|year)s? ago\b/gi, "");
}

interface Incident {
  id: string;
  host_name: string;
  target: string;
  message: string;
  created_at: number;
  status: string;
  status_label?: string;
  status_changed_at?: number | null;
  ai_summary: string | null;
  action_id: string | null;
  is_crash: boolean;
  occurrences?: number;
  last_seen?: number | null;
}

interface HistoryPage {
  items: Incident[];
  total: number;
  hosts: string[];
}

interface Stats {
  by_status: Record<string, number>;
  pending_batch: number;
  watched_hosts: number;
  ai_healthy: boolean;
  ai_message: string | null;
  /** `false`: kein KI-Server eingetragen. Die KI ist optional, das ist kein Fehler (fehlt bei älteren Servern). */
  ai_configured?: boolean;
}

interface AuditEntry {
  id: string;
  ts: string;
  actor_type: string;
  actor_id: string;
  action: string;
  outcome: string;
  reason: string | null;
}

export const STATUS_LABELS: Record<string, string> = {
  open: "Offen",
  proposed: "Aktion vorgeschlagen",
  reviewed: "Geprüft",
  resolved: "Erledigt",
  dismissed: "Verworfen",
};

const STATUS_TONE: Record<string, Tone> = {
  open: "bad",
  proposed: "warn",
  reviewed: "info",
  resolved: "good",
  dismissed: "neutral",
};

const AUDIT_ACTION_LABELS: Record<string, string> = {
  "nexus_soc.incident": "Vorfall erfasst und von Nodvard KI bewertet",
  "nexus_soc.incident_status": "Status geändert",
  "nexus_soc.proposal_rejected": "Vorschlag von Nodvard KI abgelehnt (Sperrliste)",
};

const OUTCOME_LABELS: Record<string, string> = {
  success: "ok",
  failure: "Fehler",
  denied: "abgelehnt",
  proposed: "vorgeschlagen",
};

const PAGE_SIZE = 25;

function formatTimestamp(unixSeconds: number): string {
  return new Date(unixSeconds * 1000).toLocaleString();
}

/** `<input type="date">` liefert "YYYY-MM-DD" (lokaler Tag) -> ISO-Zeitpunkt am
 * Tagesanfang bzw. -ende in LOKALER Zeit, damit "bis 24.09." den ganzen Tag einschliesst. */
function dayBoundary(day: string, end: boolean): string {
  const d = new Date(`${day}T${end ? "23:59:59.999" : "00:00:00"}`);
  return d.toISOString();
}

function AuditTrail({ incidentId }: { incidentId: string }): JSX.Element {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    authedFetch(`/audit?correlation_id=${encodeURIComponent(incidentId)}&limit=100`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<AuditEntry[]>;
      })
      .then((rows) => setEntries([...rows].sort((a, b) => a.ts.localeCompare(b.ts))))
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, [incidentId]);

  if (error) return <p className="text-xs text-red-400">Verlauf nicht abrufbar: {error}</p>;
  if (!entries) return <p className="text-xs opacity-60">Lade Verlauf …</p>;
  if (entries.length === 0) return <p className="text-xs opacity-60">Kein Audit-Eintrag zu diesem Vorfall.</p>;
  return (
    <ol className="space-y-1 border-l border-white/10 pl-3 text-xs" data-testid={`trail-${incidentId}`}>
      {entries.map((e) => (
        <li key={e.id}>
          <span className="opacity-50">{new Date(e.ts).toLocaleString()}</span>{" "}
          <span className="font-medium">{AUDIT_ACTION_LABELS[e.action] ?? e.action}</span>{" "}
          <span className="opacity-60">({OUTCOME_LABELS[e.outcome] ?? e.outcome})</span>
          {e.reason && <span className="opacity-80"> -- {withoutRelativeTime(e.reason)}</span>}
        </li>
      ))}
    </ol>
  );
}

export function ContainerWatch(): JSX.Element {
  const [page, setPage] = useState<HistoryPage | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [hostFilter, setHostFilter] = useState("");
  const [fromDay, setFromDay] = useState("");
  const [toDay, setToDay] = useState("");
  const [offset, setOffset] = useState(0);
  const [openTrail, setOpenTrail] = useState<string | null>(null);
  const canReadAudit = deck().hasPermission("audit.read");

  const load = useCallback(() => {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (appliedQuery) params.set("q", appliedQuery);
    if (statusFilter) params.set("status", statusFilter);
    if (hostFilter) params.set("host", hostFilter);
    if (fromDay) params.set("since", dayBoundary(fromDay, false));
    if (toDay) params.set("until", dayBoundary(toDay, true));
    Promise.all([
      authedFetch(`/ext/nexus-soc/history?${params.toString()}`).then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<HistoryPage>;
      }),
      authedFetch("/ext/nexus-soc/stats").then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<Stats>;
      }),
    ])
      .then(([historyData, statsData]) => {
        setPage(historyData);
        setStats(statsData);
        setError(null);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, [appliedQuery, statusFilter, hostFilter, fromDay, toDay, offset]);

  useEffect(() => {
    load();
    const interval = setInterval(load, 30_000);
    return () => clearInterval(interval);
  }, [load]);

  // Jeder neue Filter beginnt wieder auf der ersten Seite.
  useEffect(() => {
    setOffset(0);
  }, [appliedQuery, statusFilter, hostFilter, fromDay, toDay]);

  async function changeStatus(id: string, verb: "confirm" | "dismiss" | "resolve" | "reopen") {
    await authedFetch(`/ext/nexus-soc/incidents/${id}/${verb}`, { method: "POST" });
    load();
  }

  function resetFilters() {
    setQuery("");
    setAppliedQuery("");
    setStatusFilter("");
    setHostFilter("");
    setFromDay("");
    setToDay("");
  }

  if (!page && !stats && !error) return <div className="text-sm text-white/50">Lade …</div>;

  const total = page?.total ?? 0;
  const incidents = page?.items ?? [];
  const filtersActive = Boolean(appliedQuery || statusFilter || hostFilter || fromDay || toDay);

  return (
    <div className="space-y-5">
      {error && <Notice text={`Fehler: ${error}`} />}

      {stats && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
          {["open", "proposed", "reviewed", "resolved", "dismissed"].map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setStatusFilter(statusFilter === s ? "" : s)}
              aria-pressed={statusFilter === s}
              className={`panel px-4 py-3 text-left transition hover:bg-white/[0.04] ${statusFilter === s ? "ring-2 ring-[var(--color-accent)]" : ""}`}
            >
              <div className={`text-xl font-semibold tabular-nums ${s === "open" && (stats.by_status[s] ?? 0) > 0 ? "text-red-300" : ""}`}>{stats.by_status[s] ?? 0}</div>
              <div className="text-xs text-white/50">{STATUS_LABELS[s]}</div>
            </button>
          ))}
          <Stat label="Wartet auf Bündelung" value={stats.pending_batch} />
          <Stat label="Beobachtete Hosts" value={stats.watched_hosts} />
        </div>
      )}

      {stats && (
        stats.ai_configured === false ? (
          <div className="flex items-center gap-2 text-xs" data-testid="ai-not-configured">
            <span className="h-2 w-2 rounded-full bg-white/30" />
            <span className="text-white/60">
              Nodvard KI ist nicht eingerichtet (optional). Die Wache zeigt abgestürzte Container trotzdem an; Einschätzungen und Vorschläge gibt es mit einem KI-Server (Ollama) unter Einstellungen → Erweiterungen → Nodvard Shield.
            </span>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-xs">
            <span className={`h-2 w-2 rounded-full ${stats.ai_healthy ? "bg-emerald-400" : "bg-red-400"}`} />
            <span className="text-white/60">Nodvard KI: {stats.ai_healthy ? "erreichbar" : "nicht erreichbar"}{stats.ai_message ? ` -- ${stats.ai_message}` : ""}</span>
          </div>
        )
      )}

      <Card padded>
        <form
          className="flex flex-wrap items-end gap-3 text-sm"
          onSubmit={(e) => {
            e.preventDefault();
            setAppliedQuery(query.trim());
          }}
        >
          <label className="min-w-[14rem] flex-1">
            <span className="mb-1.5 block text-xs text-white/55">Suche</span>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Container, Meldung, KI-Text …"
              aria-label="Historie durchsuchen"
              className={inputClass}
            />
          </label>
          <label htmlFor="soc-status-filter">
            <span className="mb-1.5 block text-xs text-white/55">Status</span>
            <select id="soc-status-filter" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className={`${inputClass} w-44`}>
              <option value="">Alle</option>
              {Object.entries(STATUS_LABELS).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </label>
          <label>
            <span className="mb-1.5 block text-xs text-white/55">Host</span>
            <select value={hostFilter} onChange={(e) => setHostFilter(e.target.value)} aria-label="Host-Filter" className={`${inputClass} w-40`}>
              <option value="">Alle</option>
              {(page?.hosts ?? []).map((h) => (
                <option key={h} value={h}>{h}</option>
              ))}
            </select>
          </label>
          <label>
            <span className="mb-1.5 block text-xs text-white/55">Von</span>
            <input type="date" value={fromDay} onChange={(e) => setFromDay(e.target.value)} aria-label="Von" className={`${inputClass} w-40`} />
          </label>
          <label>
            <span className="mb-1.5 block text-xs text-white/55">Bis</span>
            <input type="date" value={toDay} onChange={(e) => setToDay(e.target.value)} aria-label="Bis" className={`${inputClass} w-40`} />
          </label>
          <Button type="submit"><Icon name="search" size={13} /> Suchen</Button>
          {filtersActive && <Button variant="ghost" onClick={resetFilters}>Filter zurücksetzen</Button>}
        </form>
      </Card>

      <p className="text-xs text-white/50">
        {total === 0 ? "Keine Treffer." : `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} von ${total} Vorfällen`}
      </p>

      {incidents.length > 0 && (
        <div className="panel divide-y divide-white/[0.06]">
          {incidents.map((incident) => {
            const active = incident.status === "open" || incident.status === "proposed";
            return (
              <div key={incident.id} data-testid={`incident-${incident.id}`} className="px-5 py-4 text-sm">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1 basis-64">
                    <p className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{incident.host_name}</span>
                      <span className="text-white/50">/ {incident.target}</span>
                      <Badge tone={STATUS_TONE[incident.status] ?? "neutral"}>{STATUS_LABELS[incident.status] ?? incident.status_label ?? incident.status}</Badge>
                      {incident.is_crash && <Badge tone="bad">Absturz</Badge>}
                    </p>
                    <p className="mt-1 break-words text-white/80">{incident.message}</p>
                    {incident.ai_summary && (
                      <p className="mt-2 whitespace-pre-wrap break-words rounded-lg border border-white/[0.06] bg-black/20 p-3 text-xs text-white/75">{incident.ai_summary}</p>
                    )}
                    <p className="mt-1.5 text-xs text-white/40">
                      {formatTimestamp(incident.created_at)}
                      {(incident.occurrences ?? 1) > 1 ? ` · ${incident.occurrences}× aufgetreten, zuletzt ${formatTimestamp(incident.last_seen ?? incident.created_at)}` : ""}
                      {incident.status_changed_at ? ` · Status geändert ${formatTimestamp(incident.status_changed_at)}` : ""}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {active && (
                      <>
                        <Button small onClick={() => void changeStatus(incident.id, "confirm")}>Bestätigen</Button>
                        <Button small onClick={() => void changeStatus(incident.id, "resolve")}>Erledigt</Button>
                        <Button small variant="ghost" onClick={() => void changeStatus(incident.id, "dismiss")}>Verwerfen</Button>
                      </>
                    )}
                    {!active && <Button small onClick={() => void changeStatus(incident.id, "reopen")}>Wieder öffnen</Button>}
                    {canReadAudit && (
                      <button
                        type="button"
                        onClick={() => setOpenTrail(openTrail === incident.id ? null : incident.id)}
                        aria-expanded={openTrail === incident.id}
                        className={buttonClass("ghost", true)}
                      >
                        Verlauf
                      </button>
                    )}
                  </div>
                </div>
                {openTrail === incident.id && (
                  <div className="mt-3">
                    <AuditTrail incidentId={incident.id} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {total > PAGE_SIZE && (
        <div className="flex items-center gap-2">
          <Button small disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>← Neuere</Button>
          <Button small disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>Ältere →</Button>
        </div>
      )}
    </div>
  );
}
