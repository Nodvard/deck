/**
 * Einbruchschutz & Datei-Waechter (Reiter "Einbruchschutz"): Sicherheitsereignisse
 * (Brute-Force, Anmeldung von neuer Adresse, neuer Port, geaenderte Systemdatei),
 * je Server SSH-Angriffe, Fail2ban, letzte Anmeldungen, offene Ports und der Stand
 * des Datei-Waechters. Daten: GET /ext/shield/defender/guard und /events.
 */
import { useCallback, useEffect, useState } from "react";

import { Badge, Button, Card, EmptyState, Icon, Notice, Stat, type Tone } from "../../../_shared/frontend/src/ui";
import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { API, ago, call, proposeAndApprove, useForHost, when } from "./api";
import { deck } from "../../../_shared/frontend/src/deck";
import { ServerSetupLink } from "./ServerSetupLink";

interface Attacker { ip: string; count: number; users: string[]; last_ts: number | null; banned: boolean }
interface LoginRow { user: string; ip: string; method: string; ts: number | null }
interface PortRow {
  key: string; proto: string; address: string; port: number; process: string | null; public: boolean; new: boolean;
  dynamic?: boolean; count?: number;
  /** Ohne root zeigt der Server keinen Programmnamen (fehlt bei älteren Ständen). */
  unreadable?: boolean;
}

/** Was bei einem Port ohne Programmnamen dahintersteht: ohne root nicht lesbar, sonst ein Kernel-Dienst. */
function noProcessText(p: PortRow): string {
  if (p.unreadable) return "Programm nicht lesbar (kein root)";
  return p.dynamic ? "ohne Programm (Kernel)" : "unbekanntes Programm";
}

/** Programme mit wechselnden Ports (NFS-Server) erscheinen als ein Eintrag statt mit jedem Zufallsport. */
function portName(p: PortRow): string {
  if (p.dynamic) return `wechselnde Ports/${p.proto}${p.count && p.count > 1 ? ` (${p.count})` : ""}`;
  return `${p.port}/${p.proto}`;
}

interface GuardView {
  error?: string;
  is_root: boolean;
  ssh_log_found: boolean;
  failed_24h: number;
  attackers: Attacker[];
  attacker_count: number;
  logins: LoginRow[];
  /** noaccess = ohne root nicht lesbar – weder „läuft“ noch „gestoppt“. */
  fail2ban: "none" | "stopped" | "running" | "noaccess";
  jails: Record<string, { banned: string[]; total_failed?: number; total_banned?: number }>;
  banned_count: number;
  ports: PortRow[];
  files_watched: number;
  files_pending: string[];
  checked_at: number;
  ssh_port?: string | null;
  /** null = nicht lesbar (ohne root liefert `sshd -T` nichts) – nicht dasselbe wie „keine Befunde“. */
  ssh_findings?: { key: string; value: string; severity: "info" | "warning" | "critical"; text: string; advice: string }[] | null;
}

interface GuardHost { host_id: string; host_name: string; host_status: string; view: GuardView | null; checking: boolean; open_events: number }

export interface GuardOverview {
  hosts: GuardHost[];
  summary: { failed_24h: number; attackers: number; banned: number; open_events: number; fail2ban_running: number; fail2ban_unreadable?: number; hosts: number; new_ports: number; files_pending: number };
  config: { enabled: boolean; interval_min: number; threshold: number; file_watch: boolean };
}

export interface SecurityEvent {
  id: string;
  host_id: string;
  host_name: string;
  kind: string;
  kind_label: string;
  severity: "info" | "warning" | "critical";
  title: string;
  detail: Record<string, unknown>;
  acknowledged: boolean;
  created_at: number;
}

const SEVERITY: Record<string, { label: string; tone: Tone }> = {
  critical: { label: "kritisch", tone: "bad" },
  warning: { label: "Warnung", tone: "warn" },
  info: { label: "Hinweis", tone: "info" },
};

const ACK_LABEL: Record<string, string> = { new_port: "Als bekannt übernehmen", file_changed: "Änderung übernehmen" };

function EventDetail({ ev, onBan }: { ev: SecurityEvent; onBan: (ip: string) => void }) {
  const d = ev.detail as {
    changes?: { path: string; change: string; severity: string; note: string }[];
    attackers?: { ip: string; count: number; users: string[] }[];
    logins?: LoginRow[];
    ports?: PortRow[];
  };
  return (
    <div className="mt-2 rounded-lg bg-black/25 p-3 text-xs">
      {d.changes && (
        <ul className="space-y-1">
          {d.changes.map((c) => (
            <li key={c.path} className="flex flex-wrap items-center gap-2">
              <Badge tone={SEVERITY[c.severity]?.tone ?? "neutral"}>{c.change === "added" ? "neu" : c.change === "removed" ? "gelöscht" : "geändert"}</Badge>
              <span className="font-mono">{c.path}</span>
              <span className="text-white/50">{c.note}</span>
            </li>
          ))}
        </ul>
      )}
      {d.attackers && (
        <ul className="space-y-1">
          {d.attackers.map((a) => (
            <li key={a.ip} className="flex flex-wrap items-center gap-2">
              <span className="font-mono">{a.ip}</span>
              <span className="text-white/50">{a.count}× · Benutzer: {a.users.join(", ") || "–"}</span>
              <button type="button" onClick={() => onBan(a.ip)} className="text-[var(--color-accent)] hover:underline">Sperren</button>
            </li>
          ))}
        </ul>
      )}
      {d.logins && (
        <ul className="space-y-1">
          {d.logins.map((l) => <li key={l.ip}><span className="font-mono">{l.user}@{l.ip}</span> <span className="text-white/50">· {l.method} · {when(l.ts)}</span></li>)}
        </ul>
      )}
      {d.ports && (
        <ul className="space-y-1">
          {d.ports.map((p) => (
            <li key={p.key}><span className="font-mono">{portName(p)}</span> <span className="text-white/50">· {p.process ?? noProcessText(p)} · {p.public ? `offen auf ${p.address}` : "nur lokal"}</span></li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function GuardTab({ canManage }: { canManage: boolean }) {
  const unmountSignal = useUnmountSignal();
  const [data, setData] = useState<GuardOverview | null>(null);
  const [events, setEvents] = useState<SecurityEvent[] | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [checking, setChecking] = useState(false);

  const load = useCallback(() => {
    Promise.all([call<GuardOverview>(`${API}/guard`), call<SecurityEvent[]>(`${API}/events${showAll ? "?all=true" : ""}`)])
      .then(([g, e]) => { setData(g); setEvents(e); setError(null); })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, [showAll]);

  useEffect(() => { load(); }, [load]);
  const busy = checking || !!data?.hosts.some((h) => h.checking);
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(() => {
      load();
      setChecking(false);
    }, 4000);
    return () => clearInterval(t);
  }, [busy, load]);

  async function run(label: string, fn: () => Promise<string>) {
    setNotice(null);
    try {
      const text = await fn();
      setNotice({ kind: text.startsWith("Fehlgeschlagen") ? "error" : "ok", text: `${label}: ${text}` });
    } catch (err) {
      setNotice({ kind: "error", text: `${label}: ${err instanceof Error ? err.message : String(err)}` });
    }
    load();
  }

  const checkNow = (hostIds: string[] | "all" = "all") =>
    run("Prüfung", async () => {
      const r = await call<{ hosts: number }>(`${API}/guard/check`, { method: "POST", body: JSON.stringify({ host_ids: hostIds }) });
      setChecking(true);
      return r.hosts ? `${r.hosts} Server werden geprüft …` : "läuft bereits.";
    });

  const acknowledge = (ev: SecurityEvent) =>
    run(ev.kind_label, async () => {
      await call(`${API}/events/${ev.id}/acknowledge`, { method: "POST" });
      return ev.kind in ACK_LABEL ? "als bekannt übernommen." : "bestätigt.";
    });

  const acknowledgeAll = () =>
    run("Ereignisse", async () => {
      const ok = await deck().confirmDialog("Alle offenen Ereignisse bestätigen? Neue Ports und geänderte Dateien gelten danach als bekannt.", { confirmLabel: "Alle bestätigen" });
      if (!ok) return "abgebrochen.";
      const r = await call<{ acknowledged: number }>(`${API}/events/acknowledge-all`, { method: "POST" });
      return `${r.acknowledged} bestätigt.`;
    });

  const ban = (hostId: string, hostName: string, ip: string, unban = false) =>
    run(`${ip} ${unban ? "entsperren" : "sperren"}`, async () => {
      const ok = await deck().confirmDialog(
        unban ? `${ip} auf „${hostName}“ wieder freigeben?` : `${ip} auf „${hostName}“ per Fail2ban sperren? Von dieser Adresse ist dann keine SSH-Verbindung mehr möglich.`,
        { confirmLabel: unban ? "Entsperren" : "Sperren", danger: !unban },
      );
      if (!ok) return "abgebrochen.";
      const text = await proposeAndApprove(`${API}/hosts/${hostId}/ban`, { ip, unban }, "low", unmountSignal());
      void call(`${API}/guard/check`, { method: "POST", body: JSON.stringify({ host_ids: [hostId] }) }).then(() => setChecking(true)).catch(() => undefined);
      return text;
    });

  const installFail2ban = (h: GuardHost) =>
    run(`Fail2ban (${h.host_name})`, async () => {
      const ok = await deck().confirmDialog(
        `Fail2ban auf „${h.host_name}“ installieren? Es sperrt Adressen automatisch, die zu oft ein falsches SSH-Passwort versuchen.`,
        { confirmLabel: "Installieren" },
      );
      if (!ok) return "abgebrochen.";
      const text = await proposeAndApprove(`${API}/hosts/${h.host_id}/install`, { package: "fail2ban" }, "medium", unmountSignal());
      void checkNow([h.host_id]);
      return text;
    });

  const hosts = useForHost(data?.hosts ?? []);
  const shownEvents = useForHost(events ?? []);
  if (error) return <Notice text={`Fehler: ${error}`} />;
  if (!data || !events) return <p className="text-sm text-white/50">Lade …</p>;
  const sm = data.summary;

  if (hosts.length === 0) {
    return <EmptyState icon="eye" title="Keine Server" text="Sobald Linux-Server mit SSH-Zugang angelegt sind, überwacht der Einbruchschutz ihre SSH-Anmeldungen, Ports und wichtigen Dateien." action={<ServerSetupLink className="text-sm" />} />;
  }

  return (
    <>
      {notice && <Notice text={notice.text} kind={notice.kind} onClose={() => setNotice(null)} />}

      <p className="mb-4 text-sm text-white/55" data-testid="guard-intro">
        Der Einbruchschutz zeigt, wer sich von außen auf deinen Servern anzumelden versucht (per SSH, dem Fernzugang) und was sich auf ihnen verändert.
        Fail2ban ist ein kleines Programm, das Adressen sperrt, die zu oft ein falsches Passwort probieren.
      </p>

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Fehlgeschlagene SSH-Anmeldungen" value={sm.failed_24h} hint="letzte 24 Stunden" tone={sm.failed_24h > 100 ? "warn" : undefined} />
        <Stat label="Angreifende Adressen" value={sm.attackers} />
        <Stat label="Von Fail2ban gesperrt" value={sm.banned} hint={`Fail2ban läuft auf ${sm.fail2ban_running}/${sm.hosts} Servern${sm.fail2ban_unreadable ? ` · bei ${sm.fail2ban_unreadable} nicht lesbar` : ""}`} tone={sm.fail2ban_running < sm.hosts ? "warn" : "good"} />
        <Stat label="Offene Ereignisse" value={sm.open_events} tone={sm.open_events ? "bad" : "good"} />
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Badge tone={data.config.enabled ? "info" : "neutral"}><Icon name="clock" size={11} /> {data.config.enabled ? `Prüfung alle ${data.config.interval_min} Min.` : "Automatische Prüfung aus"}</Badge>
        <Badge>Warnung ab {data.config.threshold} Fehlversuchen</Badge>
        <Badge tone={data.config.file_watch ? "good" : "neutral"}>Datei-Wächter {data.config.file_watch ? "an" : "aus"}</Badge>
        <span className="flex-1" />
        {canManage && <Button onClick={() => void checkNow()}><Icon name="refresh" size={14} /> Alle jetzt prüfen</Button>}
      </div>

      <Card
        title="Sicherheitsereignisse"
        description="Was seit der letzten Bestätigung aufgefallen ist. Jede Auffälligkeit wird genau einmal gemeldet."
        padded={false}
        className="mb-5"
        actions={
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1.5 text-xs text-white/55">
              <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> auch erledigte
            </label>
            {canManage && shownEvents.some((e) => !e.acknowledged) && <Button small onClick={() => void acknowledgeAll()}>Alle bestätigen</Button>}
          </div>
        }
      >
        <ul className="divide-y divide-white/[0.05]" data-testid="events">
          {shownEvents.length === 0 && <li className="px-5 py-6 text-sm text-emerald-300/80">Keine offenen Ereignisse – alles ruhig.</li>}
          {shownEvents.map((ev) => {
            const sev = SEVERITY[ev.severity] ?? SEVERITY.info;
            return (
              <li key={ev.id} className={`px-5 py-3 text-sm ${ev.acknowledged ? "opacity-55" : ""}`} data-testid={`event-${ev.id}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={sev.tone}>{sev.label}</Badge>
                  <span className="text-white/60">{ev.host_name}</span>
                  <span className="font-medium">{ev.title}</span>
                  <span className="text-xs text-white/40">{when(ev.created_at)}</span>
                  <span className="flex-1" />
                  <button type="button" onClick={() => setOpen(open === ev.id ? null : ev.id)} className="text-xs text-white/50 hover:text-white">
                    {open === ev.id ? "weniger" : "Details"}
                  </button>
                  {canManage && !ev.acknowledged && <Button small onClick={() => void acknowledge(ev)}>{ACK_LABEL[ev.kind] ?? "Zur Kenntnis genommen"}</Button>}
                </div>
                {open === ev.id && <EventDetail ev={ev} onBan={(ip) => void ban(ev.host_id, ev.host_name, ip)} />}
              </li>
            );
          })}
        </ul>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        {hosts.map((h) => {
          const v = h.view;
          const f2b = v?.fail2ban;
          return (
            <Card
              key={h.host_id}
              title={h.host_name}
              description={v?.checked_at ? `geprüft ${ago(v.checked_at)}` : "noch nicht geprüft"}
              actions={
                <div className="flex flex-wrap items-center gap-1.5">
                  {h.checking && <Badge tone="info">prüft …</Badge>}
                  {f2b === "running" && <Badge tone="good">Fail2ban aktiv</Badge>}
                  {f2b === "stopped" && <Badge tone="warn">Fail2ban gestoppt</Badge>}
                  {f2b === "none" && <Badge tone="warn">ohne Fail2ban</Badge>}
                  {f2b === "noaccess" && <Badge tone="neutral">Fail2ban: Zustand nicht lesbar (root nötig)</Badge>}
                  {h.open_events > 0 && <Badge tone="bad">{h.open_events} offen</Badge>}
                  {canManage && <Button small variant="ghost" ariaLabel={`${h.host_name} prüfen`} disabled={h.checking} onClick={() => void checkNow([h.host_id])}><Icon name="refresh" size={12} /></Button>}
                </div>
              }
            >
              <div data-testid={`guard-${h.host_id}`}>
                {!v && <p className="text-sm text-white/50">Noch keine Daten – „Alle jetzt prüfen“ startet den ersten Blick.</p>}
                {v?.error && <p className="text-sm text-amber-300">{v.error}</p>}
                {v && !v.error && (
                  <div className="space-y-4 text-sm">
                    {(!v.is_root || !v.ssh_log_found) && (
                      <p className="rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                        {!v.is_root ? "Ohne root-Rechte sind SSH-Protokoll, Fail2ban und Programmnamen der Ports nur eingeschränkt lesbar. Zufällige UDP-Ports ohne Programmnamen zählen dann zusammen als ein Eintrag – ein neues Programm darunter fällt nicht auf." : "Kein SSH-Protokoll gefunden (journald/auth.log)."}
                      </p>
                    )}

                    <section>
                      <p className="mb-1.5 text-xs uppercase tracking-wider text-white/40">SSH – letzte 24 Stunden</p>
                      <p className="mb-2">
                        <span className={v.failed_24h ? "text-amber-300" : "text-emerald-300"}>{v.failed_24h} Fehlversuch{v.failed_24h === 1 ? "" : "e"}</span>
                        <span className="text-white/50"> von {v.attacker_count} Adresse{v.attacker_count === 1 ? "" : "n"}</span>
                        {v.banned_count > 0 && <span className="text-white/50"> · {v.banned_count} gesperrt</span>}
                      </p>
                      {v.attackers.length > 0 && (
                        <ul className="space-y-1 text-xs">
                          {v.attackers.slice(0, 6).map((a) => (
                            <li key={a.ip} className="flex flex-wrap items-center gap-2">
                              <span className="w-36 font-mono">{a.ip}</span>
                              <span className="tabular-nums text-white/70">{a.count}×</span>
                              <span className="min-w-0 flex-1 truncate text-white/45">{a.users.join(", ")}</span>
                              {a.banned ? (
                                <>
                                  <Badge tone="good">gesperrt</Badge>
                                  {canManage && <button type="button" onClick={() => void ban(h.host_id, h.host_name, a.ip, true)} className="text-white/45 hover:text-white">entsperren</button>}
                                </>
                              ) : canManage && f2b === "running" ? (
                                <button type="button" onClick={() => void ban(h.host_id, h.host_name, a.ip)} className="text-[var(--color-accent)] hover:underline">Sperren</button>
                              ) : null}
                            </li>
                          ))}
                        </ul>
                      )}
                      {canManage && f2b === "none" && (
                        <div className="mt-2"><Button small onClick={() => void installFail2ban(h)}>Fail2ban installieren</Button></div>
                      )}
                    </section>

                    {v.ssh_findings === null && (
                      <section data-testid={`sshd-${h.host_id}`}>
                        <p className="mb-1.5 text-xs uppercase tracking-wider text-white/40">SSH-Einstellungen</p>
                        <p className="text-xs text-white/50">
                          {v.is_root
                            ? "SSH-Einstellungen konnten nicht gelesen werden."
                            : "SSH-Einstellungen nicht lesbar – dafür braucht Nodvard Deck root oder sudo ohne Passwort."}
                        </p>
                      </section>
                    )}
                    {v.ssh_findings && (
                      <section data-testid={`sshd-${h.host_id}`}>
                        <p className="mb-1.5 text-xs uppercase tracking-wider text-white/40">SSH-Einstellungen{v.ssh_port ? ` · Port ${v.ssh_port}` : ""}</p>
                        {v.ssh_findings.length === 0 ? (
                          <p className="text-xs text-emerald-300/80">Sicher eingestellt – nur Schlüssel, kein root mit Passwort.</p>
                        ) : (
                          <ul className="space-y-1 text-xs">
                            {v.ssh_findings.map((f) => (
                              <li key={f.key} className="flex flex-wrap items-center gap-2">
                                <Badge tone={SEVERITY[f.severity]?.tone ?? "neutral"}>{SEVERITY[f.severity]?.label ?? f.severity}</Badge>
                                <span>{f.text}</span>
                                <span className="text-white/45">→ besser: <code className="font-mono text-white/70">{f.advice}</code></span>
                              </li>
                            ))}
                          </ul>
                        )}
                      </section>
                    )}

                    {v.logins.length > 0 && (
                      <section>
                        <p className="mb-1.5 text-xs uppercase tracking-wider text-white/40">Letzte Anmeldungen</p>
                        <ul className="space-y-0.5 text-xs">
                          {v.logins.slice(0, 5).map((l, i) => (
                            <li key={`${l.ip}-${i}`}><span className="font-mono">{l.user}@{l.ip}</span> <span className="text-white/45">· {l.method === "publickey" ? "Schlüssel" : l.method === "password" ? "Passwort" : l.method} · {l.ts ? when(l.ts) : "–"}</span></li>
                          ))}
                        </ul>
                      </section>
                    )}

                    <section>
                      <p className="mb-1.5 text-xs uppercase tracking-wider text-white/40">Offene Ports ({v.ports.length})</p>
                      <div className="flex flex-wrap gap-1.5">
                        {v.ports.map((p) => (
                          <span
                            key={p.key}
                            title={p.dynamic ? `Zufällige Ports, ändern sich nach jedem Neustart · ${p.process ?? (p.unreadable ? "Programm nicht lesbar (kein root)" : "ohne Programm")}` : `${p.address}:${p.port} · ${p.process ?? "?"}${p.public ? "" : " · nur lokal"}`}
                            className={`rounded-md px-2 py-0.5 font-mono text-[11px] ${p.new ? "bg-red-500/20 text-red-200" : p.public ? "bg-white/[0.08] text-white/80" : "bg-white/[0.04] text-white/40"}`}
                          >
                            {portName(p)}{p.process ? ` ${p.process}` : ""}{p.new ? " · neu" : ""}
                          </span>
                        ))}
                        {v.ports.length === 0 && <span className="text-xs text-white/45">keine gefunden</span>}
                      </div>
                    </section>

                    <section>
                      <p className="mb-1.5 text-xs uppercase tracking-wider text-white/40">Datei-Wächter</p>
                      {v.files_pending.length === 0 ? (
                        <p className="text-xs text-emerald-300/80">{v.files_watched} Dateien überwacht – keine unbestätigten Änderungen.</p>
                      ) : (
                        <ul className="space-y-0.5 text-xs">
                          {v.files_pending.map((p) => <li key={p} className="font-mono text-amber-200">{p}</li>)}
                        </ul>
                      )}
                    </section>
                  </div>
                )}
              </div>
            </Card>
          );
        })}
      </div>
    </>
  );
}
