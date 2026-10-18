/**
 * Seite „Nginx Proxy Manager“ (PageSpec `component="NpmPage"`): Proxy-Hosts mit Ziel und Zertifikat, ein- und ausschalten.
 * Der Adress-Check zeigt je Host den Docker-Container hinter dem Ziel; ein gestoppter startet über `container.start` (Gate).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { isActionRunning, runAction, RUNNING_IN_BACKGROUND } from "../../../_shared/frontend/src/actions";
import { authedFetch, errorFromBody } from "../../../_shared/frontend/src/api";
import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { Badge, Button, Card, Notice, Page, SearchInput, Stat, buttonClass, type Tone } from "../../../_shared/frontend/src/ui";
import { deck } from "../../../_shared/frontend/src/deck";
import { safeHref } from "../../../_shared/frontend/src/links";

const API = "/ext/npm";
const SETTINGS_PATH = "/settings/extensions/npm";

type ServiceState = "ok" | "not_configured" | "unreachable" | "auth_failed" | "unsupported";

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

export interface AddressCheck {
  proxy_id: number;
  status: string;
  tone: "good" | "bad" | "warn" | "neutral";
  text: string;
  container: string | null;
  docker_host: string | null;
  docker_host_id: string | null;
  can_start: boolean;
}

export interface AddressCheckResult {
  state: ServiceState | "no_catalog" | "docker_unreadable";
  message: string | null;
  checks: AddressCheck[];
  problems: number;
}

type Message = { kind: "ok" | "error"; text: string } | null;

const CHECK_TEXT_CLASS: Record<AddressCheck["tone"], string> = {
  good: "text-emerald-300",
  bad: "text-red-300",
  warn: "text-amber-300",
  neutral: "text-white/50",
};

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await authedFetch(path, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorFromBody(body, res.status));
  return body as T;
}

/** Vorschlagen und, wer freigeben darf, gleich bestätigen; läuft die Aktion länger, fragt runAction alle 3 s nach. */
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

function date(iso: string | null): string {
  if (!iso) return "unbekannt";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "unbekannt" : d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });
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
  return <p className="text-xs text-white/45">Ein Administrator kann das unter Einstellungen → Erweiterungen → Nginx Proxy Manager einrichten.</p>;
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

function CertBadge({ cert }: { cert: Certificate | null }) {
  if (!cert) return <Badge>ohne Zertifikat</Badge>;
  return <Badge tone={CERT_TONE[cert.status]}>{cert.status === "ok" ? `gültig bis ${date(cert.expires_at)}` : `${cert.status_label} · ${cert.days_text}`}</Badge>;
}

function hostUrl(host: ProxyHost): string | null {
  const domain = host.domains[0];
  if (!domain || domain.includes("*")) return null;
  return safeHref(`${host.certificate_id ? "https" : "http"}://${domain}`) ?? null;
}

function addressSummary(result: AddressCheckResult | null, loading: boolean): { text: string; className: string } | null {
  if (loading && !result) return { text: "Adress-Check läuft …", className: "text-white/45" };
  if (!result) return null;
  if (result.state !== "ok") return result.message ? { text: `Adress-Check: ${result.message}`, className: "text-white/45" } : null;
  if (result.message) return { text: `Adress-Check: ${result.message}`, className: "text-white/45" };
  if (result.problems === 1) return { text: "Adress-Check: Eine Adresse führt zu einem gestoppten Container.", className: "text-red-300" };
  if (result.problems > 1) return { text: `Adress-Check: ${result.problems} Adressen führen zu gestoppten Containern.`, className: "text-red-300" };
  // Entwarnung nur für das, was wirklich geprüft wurde: ohne zugeordnetes Ziel kein grünes Häkchen.
  const assigned = result.checks.filter((c) => c.container).length;
  const unchecked = result.checks.filter((c) => c.status === "unreachable").length;
  if (assigned === 0) {
    return {
      text: unchecked
        ? "Adress-Check: Die Docker-Server sind gerade nicht erreichbar, es ließ sich nichts prüfen."
        : "Adress-Check: Kein Ziel ließ sich einem Container zuordnen.",
      className: "text-white/45",
    };
  }
  // Gestoppt, aber nicht rot: der Proxy-Host ist aus, oder die Zuordnung ist nur vermutet.
  const unsure = result.checks.filter((c) => c.status === "stopped" && c.tone !== "bad").length;
  const counts = [
    `${assigned} von ${result.checks.length} Zielen zugeordnet`,
    ...(unchecked ? [`${unchecked} nicht prüfbar`] : []),
    ...(unsure ? [`${unsure} gestoppt, aber Proxy-Host aus oder Zuordnung unsicher`] : []),
  ];
  return {
    text: `Adress-Check: Kein aktiver Proxy-Host führt sicher zu einem gestoppten Container (${counts.join(", ")}).`,
    className: unchecked || unsure ? "text-white/45" : "text-emerald-300",
  };
}

function NpmSection({ data, canAct, busy, onToggle, onRetry, checks, checking, onStart }: {
  data: NpmStatus;
  canAct: boolean;
  busy: string | null;
  onToggle: (host: ProxyHost) => void;
  onRetry: () => void;
  checks: AddressCheckResult | null;
  checking: boolean;
  onStart: (host: ProxyHost, check: AddressCheck) => void;
}) {
  const [search, setSearch] = useState("");
  const byProxy = useMemo(() => new Map((checks?.checks ?? []).map((c) => [c.proxy_id, c])), [checks]);
  const hosts = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return data.hosts;
    return data.hosts.filter((h) => h.domains.some((d) => d.toLowerCase().includes(q))
      || (h.target ?? "").toLowerCase().includes(q)
      || (byProxy.get(h.id)?.container ?? "").toLowerCase().includes(q));
  }, [data.hosts, search, byProxy]);

  if (data.state === "not_configured") return <NotSetUp title="Nginx Proxy Manager" message={data.message} testId="npm-not-configured" />;
  if (data.state !== "ok" || !data.summary) {
    return <Problem title="Nginx Proxy Manager" state={data.state} message={data.message} onRetry={onRetry} testId="npm-problem" />;
  }
  const s = data.summary;
  const urgent = data.certificates.filter((c) => c.status === "warn" || c.status === "expired");
  const summary = addressSummary(checks, checking);
  const adminHref = safeHref(data.url);

  return (
    <div className="space-y-4">
      <Card
        title="Nginx Proxy Manager"
        description={adminHref ? <a href={adminHref} target="_blank" rel="noreferrer" className="hover:text-white hover:underline">Verwaltung öffnen</a> : undefined}
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

      <Card
        title="Proxy-Hosts"
        padded={false}
        description={summary ? <span data-testid="address-summary" className={summary.className}>{summary.text}</span> : undefined}
        actions={data.hosts.length > 5 ? <SearchInput value={search} onChange={setSearch} placeholder="Domain, Ziel oder Container suchen" label="Proxy-Hosts durchsuchen" /> : undefined}
      >
        {hosts.length === 0 ? (
          <p className="px-5 py-6 text-center text-sm text-white/45">{data.hosts.length === 0 ? "Noch keine Proxy-Hosts angelegt." : "Kein Proxy-Host passt zur Suche."}</p>
        ) : (
          <ul className="divide-y divide-white/[0.06]">
            {hosts.map((h) => {
              const warn = h.certificate && (h.certificate.status === "warn" || h.certificate.status === "expired");
              const url = hostUrl(h);
              const check = byProxy.get(h.id);
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
                    {check && <p data-testid={`address-${h.id}`} className={`break-words text-xs ${CHECK_TEXT_CLASS[check.tone]}`}>{check.text}</p>}
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5">
                    {canAct && check?.can_start && check.docker_host_id && (
                      <Button small variant="primary" disabled={busy !== null} onClick={() => onStart(h, check)}>Container starten</Button>
                    )}
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

export function NpmPage(): JSX.Element {
  const unmountSignal = useUnmountSignal();
  const [npm, setNpm] = useState<NpmStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [message, setMessage] = useState<Message>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const canAct = deck().hasPermission("hosts.execute");

  const [checks, setChecks] = useState<AddressCheckResult | null>(null);
  const [checking, setChecking] = useState(false);
  // Der Adress-Check fragt jeden Docker-Server per SSH und dauert länger: getrennt laden, die Liste wartet nicht.
  // Bei mehreren Läufen (Aktualisieren, Umschalten, Starten) gilt nur die letzte Anfrage.
  const checkSeq = useRef(0);
  const loadChecks = useCallback(() => {
    const seq = ++checkSeq.current;
    setChecking(true);
    call<AddressCheckResult>(`${API}/address-check`)
      .then((result) => { if (seq === checkSeq.current) setChecks(result); })
      .catch((err: unknown) => {
        if (seq === checkSeq.current) setChecks({ state: "unreachable", message: err instanceof Error ? err.message : String(err), checks: [], problems: 0 });
      })
      .finally(() => { if (seq === checkSeq.current) setChecking(false); });
  }, []);
  const loadNpm = useCallback(() => {
    call<NpmStatus>(`${API}/summary`)
      .then((status) => {
        setNpm(status);
        if (status.state === "ok") loadChecks();
      })
      .catch((err: unknown) => setLoadError(err instanceof Error ? err.message : String(err)));
  }, [loadChecks]);
  const loadAll = useCallback(() => {
    setLoadError(null);
    loadNpm();
  }, [loadNpm]);

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

  async function startContainer(host: ProxyHost, check: AddressCheck) {
    const name = host.domains[0] ?? `Proxy-Host ${host.id}`;
    const ok = await deck().confirmDialog(
      `Container „${check.container}“ auf ${check.docker_host} starten? Dahinter liegt ${name}.`,
      { title: "Container starten", confirmLabel: "Starten" },
    );
    if (!ok) return;
    await run(
      `start-${host.id}`,
      `/hosts/${encodeURIComponent(check.docker_host_id ?? "")}/actions/container.start`,
      { payload: { container: check.container }, reason: `Über den Adress-Check des Nginx Proxy Managers gestartet (${name}).` },
      loadChecks,
    );
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
    await run(`host-${host.id}`, `${API}/hosts/${host.id}/${host.enabled ? "disable" : "enable"}`, undefined, loadNpm);
  }

  return (
    <Page
      title="Nginx Proxy Manager"
      description="Proxy-Hosts, Zertifikate und wohin die Adressen führen."
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
        {npm ? (
          <NpmSection
            data={npm} canAct={canAct} busy={busy} onToggle={(h) => void toggle(h)} onRetry={loadNpm}
            checks={checks} checking={checking} onStart={(h, c) => void startContainer(h, c)}
          />
        ) : (
          !loadError && <Card title="Nginx Proxy Manager"><p className="text-sm text-white/50">Lade …</p></Card>
        )}
      </div>
    </Page>
  );
}
