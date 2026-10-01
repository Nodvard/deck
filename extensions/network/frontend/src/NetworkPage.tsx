/**
 * Netzwerk-Seite -- PageSpec `component="NetworkPage"` (nodvard_deck_ext_network/__init__.py).
 *
 * Pi-hole (Anfragen, Blockierung pausieren/fortsetzen) und Nginx Proxy Manager
 * (Proxy-Hosts mit Ziel und Zertifikat, Hosts ein-/ausschalten). Beide sind optional;
 * "nicht eingerichtet" zeigt einen Hinweis mit Link zu den Einstellungen.
 *
 * Aktionen gehen als Vorschlag ans Aktions-Gate; wer `actions.approve:<risk>` hat, gibt
 * den eigenen Vorschlag gleich frei (wie Backups/Nodvard Shield), alle anderen sehen
 * "wartet auf Freigabe".
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { isActionRunning, runAction, RUNNING_IN_BACKGROUND } from "../../../_shared/frontend/src/actions";
import { authedFetch, errorFromBody } from "../../../_shared/frontend/src/api";
import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { Badge, Button, Card, Notice, Page, SearchInput, Stat, buttonClass, type Tone } from "../../../_shared/frontend/src/ui";
import { deck } from "../../../_shared/frontend/src/deck";

const API = "/ext/network";
const SETTINGS_PATH = "/settings/extensions/network";
const PAUSE_MINUTES = [5, 15, 60] as const;

type ServiceState = "ok" | "not_configured" | "unreachable" | "auth_failed" | "unsupported";

export interface PiholeStatus {
  state: ServiceState;
  message: string | null;
  url: string | null;
  summary: {
    queries_total: number | null;
    queries_blocked: number | null;
    percent_blocked: number | null;
    domains_blocked: number | null;
    gravity_updated_at: number | null;
    clients_active: number | null;
  } | null;
  blocking: { status: string; enabled: boolean | null; timer_s: number | null } | null;
}

export interface Certificate {
  id: number;
  name: string;
  domains: string[];
  provider: string;
  provider_label: string;
  expires_at: string | null;
  days_left: number | null;
  status: "ok" | "warn" | "expired" | "unknown";
  status_label: string;
  days_text: string;
}

export interface ProxyHost {
  id: number;
  domains: string[];
  target: string | null;
  enabled: boolean;
  ssl_forced: boolean;
  certificate_id: number | null;
  certificate: Certificate | null;
  nginx_online: boolean | null;
  nginx_error: string | null;
}

export interface NpmStatus {
  state: ServiceState;
  message: string | null;
  url: string | null;
  hosts: ProxyHost[];
  certificates: Certificate[];
  summary: { hosts: number; hosts_enabled: number; certificates: number; certificates_warn: number; certificates_expired: number } | null;
}

type Message = { kind: "ok" | "error"; text: string } | null;

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await authedFetch(path, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorFromBody(body, res.status));
  return body as T;
}

/** Vorschlagen und -- wer freigeben darf -- gleich bestaetigen; laeuft die Aktion laenger,
 * fragt runAction alle 3 s nach. */
async function proposeAndApprove(path: string, body?: unknown, signal?: AbortSignal): Promise<Message> {
  const { action, approved } = await runAction(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }, { signal });
  const status = action.status ?? "";
  if (approved) {
    if (status === "succeeded") return { kind: "ok", text: action.result?.output || "Erledigt." };
    if (isActionRunning(status)) return { kind: "ok", text: RUNNING_IN_BACKGROUND };
    return { kind: "error", text: action.result?.error || `Fehlgeschlagen (${status}).` };
  }
  if (status === "proposed") return { kind: "ok", text: "Vorgeschlagen – wartet auf Freigabe unter „Aktionen“." };
  if (status === "succeeded") return { kind: "ok", text: action.result?.output || "Erledigt." };
  if (status === "failed") return { kind: "error", text: action.result?.error || "Fehlgeschlagen." };
  if (isActionRunning(status)) return { kind: "ok", text: RUNNING_IN_BACKGROUND };
  if (status === "denied") return { kind: "error", text: `Abgelehnt${action.detail ? `: ${action.detail}` : "."}` };
  return { kind: "ok", text: `Status: ${status}` };
}

function num(value: number | null | undefined): string {
  return value == null ? "–" : value.toLocaleString("de-DE");
}

function pct(value: number | null | undefined): string {
  return value == null ? "–" : `${value.toLocaleString("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} %`;
}

function date(iso: string | null): string {
  if (!iso) return "unbekannt";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "unbekannt" : d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });
}

function ago(epoch: number | null | undefined): string {
  if (!epoch) return "unbekannt";
  const s = Date.now() / 1000 - epoch;
  if (s < 3600) return "vor weniger als einer Stunde";
  if (s < 86400) return `vor ${Math.round(s / 3600)} Std.`;
  const days = Math.round(s / 86400);
  return `vor ${days} ${days === 1 ? "Tag" : "Tagen"}`;
}

const CERT_TONE: Record<Certificate["status"], Tone> = { ok: "good", warn: "warn", expired: "bad", unknown: "neutral" };

const PROBLEM: Record<Exclude<ServiceState, "ok" | "not_configured">, { label: string; tone: Tone }> = {
  unreachable: { label: "nicht erreichbar", tone: "bad" },
  auth_failed: { label: "Anmeldung fehlgeschlagen", tone: "bad" },
  unsupported: { label: "nicht unterstützt", tone: "warn" },
};

function SettingsHint() {
  if (deck().hasPermission("extensions.manage")) {
    return (
      <a href={SETTINGS_PATH} className={buttonClass("primary", true)}>
        Jetzt einrichten
      </a>
    );
  }
  return <p className="text-xs text-white/45">Ein Administrator kann das unter Einstellungen → Erweiterungen → Netzwerk einrichten.</p>;
}

function NotSetUp({ title, message, testId }: { title: string; message: string | null; testId: string }) {
  return (
    <Card title={title}>
      <div data-testid={testId} className="flex flex-col items-start gap-3">
        <Badge>nicht eingerichtet</Badge>
        <p className="text-sm text-white/60">{message}</p>
        <SettingsHint />
      </div>
    </Card>
  );
}

function Problem({ title, state, message, onRetry, testId }: { title: string; state: ServiceState; message: string | null; onRetry: () => void; testId: string }) {
  const problem = PROBLEM[state as keyof typeof PROBLEM] ?? PROBLEM.unreachable;
  return (
    <Card title={title} actions={<Badge tone={problem.tone}>{problem.label}</Badge>}>
      <div data-testid={testId} className="flex flex-col items-start gap-3">
        <p className="text-sm text-white/70">{message}</p>
        <div className="flex flex-wrap items-center gap-2">
          <Button small onClick={onRetry}>Erneut versuchen</Button>
          {(state === "auth_failed" || state === "unsupported") && deck().hasPermission("extensions.manage") && (
            <a href={SETTINGS_PATH} className={buttonClass("ghost", true)}>Einstellungen</a>
          )}
        </div>
      </div>
    </Card>
  );
}

function PiholeSection({ data, canAct, busy, onPause, onResume, onRetry }: {
  data: PiholeStatus;
  canAct: boolean;
  busy: string | null;
  onPause: (minutes: number) => void;
  onResume: () => void;
  onRetry: () => void;
}) {
  if (data.state === "not_configured") return <NotSetUp title="Pi-hole" message={data.message} testId="pihole-not-configured" />;
  if (data.state !== "ok" || !data.summary) return <Problem title="Pi-hole" state={data.state} message={data.message} onRetry={onRetry} testId="pihole-problem" />;
  const s = data.summary;
  const blocking = data.blocking;
  const enabled = blocking?.enabled;
  const minutesLeft = blocking?.timer_s ? Math.max(1, Math.round(blocking.timer_s / 60)) : null;
  const badge: { label: string; tone: Tone } =
    enabled === true ? { label: "Blockierung aktiv", tone: "good" }
      : enabled === false ? (minutesLeft ? { label: `pausiert – noch ${minutesLeft} Min.`, tone: "warn" } : { label: "Blockierung aus", tone: "bad" })
        : { label: "Zustand unklar", tone: "warn" };

  return (
    <Card
      title="Pi-hole"
      description={data.url ? <a href={`${data.url}/admin/`} target="_blank" rel="noreferrer" className="hover:text-white hover:underline">Pi-hole öffnen</a> : undefined}
      actions={<Badge tone={badge.tone}>{badge.label}</Badge>}
    >
      <div data-testid="pihole" className="space-y-4">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="Anfragen (24 Std.)" value={num(s.queries_total)} hint={s.clients_active != null ? `${num(s.clients_active)} aktive Geräte` : undefined} />
          <Stat label="Blockiert" value={num(s.queries_blocked)} />
          <Stat label="Anteil blockiert" value={pct(s.percent_blocked)} />
          <Stat label="Domains auf der Sperrliste" value={num(s.domains_blocked)} hint={`aktualisiert ${ago(s.gravity_updated_at)}`} />
        </div>
        {canAct && (
          <div className="flex flex-wrap items-center gap-2 border-t border-white/[0.06] pt-3">
            {enabled === false ? (
              <>
                <span className="text-sm text-white/70">
                  {minutesLeft ? `Blockierung pausiert, noch ${minutesLeft} Min.` : "Blockierung ist ausgeschaltet."}
                </span>
                <Button variant="primary" small disabled={busy !== null} onClick={onResume}>Blockierung fortsetzen</Button>
              </>
            ) : (
              <>
                <span className="text-sm text-white/70">Blockierung pausieren:</span>
                {PAUSE_MINUTES.map((m) => (
                  <Button key={m} small disabled={busy !== null} onClick={() => onPause(m)} ariaLabel={`${m} Minuten pausieren`}>
                    {m} Min.
                  </Button>
                ))}
              </>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}

function CertBadge({ cert }: { cert: Certificate | null }) {
  if (!cert) return <Badge>ohne Zertifikat</Badge>;
  return <Badge tone={CERT_TONE[cert.status]}>{cert.status === "ok" ? `gültig bis ${date(cert.expires_at)}` : `${cert.status_label} · ${cert.days_text}`}</Badge>;
}

function hostUrl(host: ProxyHost): string | null {
  const domain = host.domains[0];
  if (!domain || domain.includes("*")) return null;
  return `${host.certificate_id ? "https" : "http"}://${domain}`;
}

function NpmSection({ data, canAct, busy, onToggle, onRetry }: {
  data: NpmStatus;
  canAct: boolean;
  busy: string | null;
  onToggle: (host: ProxyHost) => void;
  onRetry: () => void;
}) {
  const [search, setSearch] = useState("");
  const hosts = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return data.hosts;
    return data.hosts.filter((h) => h.domains.some((d) => d.toLowerCase().includes(q)) || (h.target ?? "").toLowerCase().includes(q));
  }, [data.hosts, search]);

  if (data.state === "not_configured") return <NotSetUp title="Nginx Proxy Manager" message={data.message} testId="npm-not-configured" />;
  if (data.state !== "ok" || !data.summary) {
    return <Problem title="Nginx Proxy Manager" state={data.state} message={data.message} onRetry={onRetry} testId="npm-problem" />;
  }
  const s = data.summary;
  const urgent = data.certificates.filter((c) => c.status === "warn" || c.status === "expired");

  return (
    <div className="space-y-4">
      <Card
        title="Nginx Proxy Manager"
        description={data.url ? <a href={data.url} target="_blank" rel="noreferrer" className="hover:text-white hover:underline">Verwaltung öffnen</a> : undefined}
      >
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="Proxy-Hosts" value={`${s.hosts_enabled} / ${s.hosts}`} hint="eingeschaltet / gesamt" />
          <Stat label="Zertifikate" value={s.certificates} />
          <Stat label="Laufen bald ab" value={s.certificates_warn} hint="in den nächsten 14 Tagen" tone={s.certificates_warn > 0 ? "warn" : undefined} />
          <Stat label="Abgelaufen" value={s.certificates_expired} tone={s.certificates_expired > 0 ? "bad" : undefined} />
        </div>
      </Card>

      {urgent.length > 0 ? (
        <Card title="Zertifikate, die Aufmerksamkeit brauchen" description="Nginx Proxy Manager erneuert Let's-Encrypt-Zertifikate normalerweise selbst. Steht eins hier, klemmt meist die Erneuerung.">
          <ul data-testid="urgent-certificates" className="divide-y divide-white/[0.06]">
            {urgent.map((c) => (
              <li key={c.id} data-testid={`cert-${c.id}`} className="flex flex-wrap items-center justify-between gap-2 py-2 first:pt-0 last:pb-0">
                <div className="min-w-0">
                  <p className="break-words text-sm font-medium">{c.name}</p>
                  <p className="break-words text-xs text-white/50">{c.domains.join(", ")} · {c.provider_label} · {c.status === "expired" ? "abgelaufen am" : "läuft ab am"} {date(c.expires_at)}</p>
                </div>
                <Badge tone={CERT_TONE[c.status]}>{c.status_label} · {c.days_text}</Badge>
              </li>
            ))}
          </ul>
        </Card>
      ) : (
        data.certificates.length > 0 && <p className="text-sm text-emerald-300">Alle Zertifikate sind noch mindestens 14 Tage gültig.</p>
      )}

      <Card title="Proxy-Hosts" padded={false} actions={data.hosts.length > 5 ? <SearchInput value={search} onChange={setSearch} placeholder="Domain oder Ziel suchen" label="Proxy-Hosts durchsuchen" /> : undefined}>
        {hosts.length === 0 ? (
          <p className="px-5 py-6 text-center text-sm text-white/45">{data.hosts.length === 0 ? "Noch keine Proxy-Hosts angelegt." : "Kein Proxy-Host passt zur Suche."}</p>
        ) : (
          <ul className="divide-y divide-white/[0.06]">
            {hosts.map((h) => {
              const warn = h.certificate && (h.certificate.status === "warn" || h.certificate.status === "expired");
              const url = hostUrl(h);
              return (
                <li
                  key={h.id}
                  data-testid={`host-${h.id}`}
                  className={`flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3 ${warn ? (h.certificate?.status === "expired" ? "bg-red-500/[0.06]" : "bg-amber-500/[0.06]") : ""}`}
                >
                  <div className="min-w-0 flex-1 basis-60">
                    <p className="break-words text-sm font-medium">
                      {url ? <a href={url} target="_blank" rel="noreferrer" className="hover:underline">{h.domains.join(", ")}</a> : h.domains.join(", ")}
                    </p>
                    <p className="break-words text-xs text-white/50">→ {h.target ?? "kein Ziel"}{h.nginx_online === false ? " · Nginx meldet einen Fehler" : ""}</p>
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <CertBadge cert={h.certificate} />
                    {h.enabled ? <Badge tone="good">an</Badge> : <Badge tone="neutral">aus</Badge>}
                    {canAct && (
                      <Button small variant={h.enabled ? "danger" : "secondary"} disabled={busy !== null} onClick={() => onToggle(h)}>
                        {h.enabled ? "Ausschalten" : "Einschalten"}
                      </Button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}

export function NetworkPage(): JSX.Element {
  const unmountSignal = useUnmountSignal();
  const [pihole, setPihole] = useState<PiholeStatus | null>(null);
  const [npm, setNpm] = useState<NpmStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [message, setMessage] = useState<Message>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const canAct = deck().hasPermission("hosts.execute");

  const loadPihole = useCallback(() => {
    call<PiholeStatus>(`${API}/pihole`).then(setPihole).catch((err: unknown) => setLoadError(err instanceof Error ? err.message : String(err)));
  }, []);
  const loadNpm = useCallback(() => {
    call<NpmStatus>(`${API}/npm`).then(setNpm).catch((err: unknown) => setLoadError(err instanceof Error ? err.message : String(err)));
  }, []);
  const loadAll = useCallback(() => {
    setLoadError(null);
    loadPihole();
    loadNpm();
  }, [loadPihole, loadNpm]);

  useEffect(() => { loadAll(); }, [loadAll]);

  async function run(key: string, path: string, body: unknown, after: () => void) {
    setBusy(key);
    setMessage(null);
    try {
      setMessage(await proposeAndApprove(path, body, unmountSignal()));
    } catch (err) {
      setMessage({ kind: "error", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(null);
      after();
    }
  }

  async function pause(minutes: number) {
    const ok = await deck().confirmDialog(
      `Pi-hole für ${minutes} Minuten pausieren? In dieser Zeit werden Werbung und Tracker im ganzen Netz nicht blockiert. Danach blockiert Pi-hole von selbst wieder.`,
      { confirmLabel: "Pausieren" },
    );
    if (!ok) return;
    await run("pihole", `${API}/pihole/pause`, { minutes }, loadPihole);
  }

  async function resume() {
    await run("pihole", `${API}/pihole/resume`, undefined, loadPihole);
  }

  async function toggle(host: ProxyHost) {
    const name = host.domains.join(", ") || `Proxy-Host ${host.id}`;
    const ok = host.enabled
      ? await deck().confirmDialog(
        `„${name}“ ausschalten? Die Seite ist danach nicht mehr erreichbar, bis du den Proxy-Host wieder einschaltest.`,
        { title: "Proxy-Host ausschalten", danger: true, confirmLabel: "Ausschalten" },
      )
      : await deck().confirmDialog(`„${name}“ wieder einschalten?`, { title: "Proxy-Host einschalten", confirmLabel: "Einschalten" });
    if (!ok) return;
    await run(`host-${host.id}`, `${API}/npm/hosts/${host.id}/${host.enabled ? "disable" : "enable"}`, undefined, loadNpm);
  }

  return (
    <Page
      title="Netzwerk"
      description="Pi-hole und Nginx Proxy Manager auf einen Blick."
      actions={
        <>
          {deck().hasPermission("extensions.manage") && <a href={SETTINGS_PATH} className={buttonClass("ghost")}>Einstellungen</a>}
          <Button onClick={loadAll}>Aktualisieren</Button>
        </>
      }
    >
      {loadError && <Notice text={loadError} onClose={() => setLoadError(null)} />}
      {message && <Notice text={message.text} kind={message.kind} onClose={() => setMessage(null)} />}
      <div className="space-y-6">
        {pihole ? (
          <PiholeSection data={pihole} canAct={canAct} busy={busy} onPause={(m) => void pause(m)} onResume={() => void resume()} onRetry={loadPihole} />
        ) : (
          !loadError && <Card title="Pi-hole"><p className="text-sm text-white/50">Lade …</p></Card>
        )}
        {npm ? (
          <NpmSection data={npm} canAct={canAct} busy={busy} onToggle={(h) => void toggle(h)} onRetry={loadNpm} />
        ) : (
          !loadError && <Card title="Nginx Proxy Manager"><p className="text-sm text-white/50">Lade …</p></Card>
        )}
      </div>
    </Page>
  );
}
