/**
 * Pi-hole-Seite (PageSpec `component="PiholePage"`): Anfragen, Blockierung, aktivste Geräte, Sperrlisten, Domains
 * erlauben/sperren. Aktionen gehen als Vorschlag ans Aktions-Gate; wer `actions.approve:<risk>` hat, gibt den eigenen
 * Vorschlag gleich frei.
 */
import { useCallback, useEffect, useState } from "react";

import { isActionRunning, runAction, RUNNING_IN_BACKGROUND } from "../../../_shared/frontend/src/actions";
import { authedFetch, errorFromBody } from "../../../_shared/frontend/src/api";
import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { Badge, Button, Card, Field, Notice, Page, Stat, buttonClass, inputClass, type Tone } from "../../../_shared/frontend/src/ui";
import { deck } from "../../../_shared/frontend/src/deck";
import { safeHref } from "../../../_shared/frontend/src/links";

const API = "/ext/pihole";
const SETTINGS_PATH = "/settings/extensions/pihole";
const PAUSE_MINUTES = [5, 15, 60] as const;

type ServiceState = "ok" | "not_configured" | "unreachable" | "auth_failed" | "unsupported";

export interface TopClient {
  name: string;
  ip: string | null;
  count: number;
}

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
  top_clients: TopClient[] | null;
}

type Message = { kind: "ok" | "error"; text: string } | null;

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

function num(value: number | null | undefined): string {
  return value == null ? "–" : value.toLocaleString("de-DE");
}

function pct(value: number | null | undefined): string {
  return value == null ? "–" : `${value.toLocaleString("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} %`;
}

function ago(epoch: number | null | undefined): string {
  if (!epoch) return "unbekannt";
  const s = Date.now() / 1000 - epoch;
  if (s < 3600) return "vor weniger als einer Stunde";
  if (s < 86400) return `vor ${Math.round(s / 3600)} Std.`;
  const days = Math.round(s / 86400);
  return `vor ${days} ${days === 1 ? "Tag" : "Tagen"}`;
}

/** Wie `normalize_domain` im Modul: klein, ohne Punkt am Ende, mindestens zwei Teile aus a-z, 0-9 und `-`. */
export function domainProblem(raw: string): string | null {
  const domain = raw.trim().toLowerCase().replace(/\.$/, "");
  if (!domain) return null;
  const labels = domain.split(".");
  const ok = domain.length >= 3 && domain.length <= 253 && labels.length >= 2
    && labels.every((l) => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(l));
  return ok ? null : "Bitte eine Domain wie werbung.example.com eintragen (ohne http:// und ohne Pfad).";
}

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
  return <p className="text-xs text-white/45">Ein Administrator kann das unter Einstellungen → Erweiterungen → Pi-hole einrichten.</p>;
}

function BlockingCard({ data, canAct, busy, onPause, onResume }: {
  data: PiholeStatus;
  canAct: boolean;
  busy: string | null;
  onPause: (minutes: number) => void;
  onResume: () => void;
}) {
  const enabled = data.blocking?.enabled;
  const minutesLeft = data.blocking?.timer_s ? Math.max(1, Math.round(data.blocking.timer_s / 60)) : null;
  const badge: { label: string; tone: Tone } =
    enabled === true ? { label: "Blockierung aktiv", tone: "good" }
      : enabled === false ? (minutesLeft ? { label: `pausiert – noch ${minutesLeft} Min.`, tone: "warn" } : { label: "Blockierung aus", tone: "bad" })
        : { label: "Zustand unklar", tone: "warn" };
  return (
    <Card title="Blockierung" actions={<Badge tone={badge.tone}>{badge.label}</Badge>}>
      <div data-testid="blocking" className="flex flex-wrap items-center gap-2">
        {enabled === false ? (
          <>
            <span className="text-sm text-white/70">
              {minutesLeft ? `Blockierung pausiert, noch ${minutesLeft} Min.` : "Blockierung ist ausgeschaltet – Werbung und Tracker kommen durch."}
            </span>
            {canAct && <Button variant="primary" small disabled={busy !== null} onClick={onResume}>Blockierung fortsetzen</Button>}
          </>
        ) : (
          <>
            <span className="text-sm text-white/70">{canAct ? "Blockierung pausieren:" : "Werbung und Tracker werden im ganzen Netz blockiert."}</span>
            {canAct && PAUSE_MINUTES.map((m) => (
              <Button key={m} small disabled={busy !== null} onClick={() => onPause(m)} ariaLabel={`${m} Minuten pausieren`}>
                {m} Min.
              </Button>
            ))}
          </>
        )}
      </div>
    </Card>
  );
}

function ClientsCard({ clients }: { clients: TopClient[] }) {
  return (
    <Card title="Aktivste Geräte" description="Die meisten Anfragen in den letzten 24 Stunden.">
      {clients.length === 0 ? (
        <p className="text-sm text-white/45">Pi-hole meldet noch keine Geräte.</p>
      ) : (
        <ul data-testid="top-clients" className="divide-y divide-white/[0.06]">
          {clients.map((c) => (
            <li key={`${c.ip ?? ""}-${c.name}`} className="flex flex-wrap items-center justify-between gap-2 py-2 first:pt-0 last:pb-0">
              <div className="min-w-0">
                <p className="break-words text-sm font-medium">{c.name}</p>
                {c.ip && c.ip !== c.name && <p className="break-words text-xs text-white/50">{c.ip}</p>}
              </div>
              <span className="text-sm tabular-nums text-white/70">{num(c.count)} Anfragen</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function ListsCard({ data, canAct, busy, onGravity, onDomain }: {
  data: PiholeStatus;
  canAct: boolean;
  busy: string | null;
  onGravity: () => void;
  onDomain: (list: "allow" | "deny", domain: string) => Promise<boolean>;
}) {
  const [domain, setDomain] = useState("");
  const problem = domainProblem(domain);
  const s = data.summary;

  async function submit(list: "allow" | "deny") {
    if (!domain.trim() || problem) return;
    if (await onDomain(list, domain.trim().toLowerCase().replace(/\.$/, ""))) setDomain("");
  }

  return (
    <Card
      title="Sperrlisten"
      description={`${num(s?.domains_blocked)} Domains · aktualisiert ${ago(s?.gravity_updated_at)}`}
      actions={canAct ? <Button small disabled={busy !== null} onClick={onGravity}>Listen aktualisieren</Button> : undefined}
    >
      {canAct ? (
        // Enter löst nichts aus: ob erlauben oder sperren, entscheidet der Knopf.
        <form data-testid="domain-form" className="space-y-2" onSubmit={(e) => e.preventDefault()}>
          <Field label="Eine Domain erlauben oder sperren">
            <input
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              placeholder="werbung.example.com"
              className={inputClass}
              aria-invalid={problem ? true : undefined}
            />
          </Field>
          {problem && <p className="text-xs text-red-300">{problem}</p>}
          <div className="flex flex-wrap items-center gap-2">
            <Button small disabled={busy !== null || !domain.trim() || problem !== null} onClick={() => void submit("allow")}>Erlauben</Button>
            <Button small variant="danger" disabled={busy !== null || !domain.trim() || problem !== null} onClick={() => void submit("deny")}>Sperren</Button>
            <span className="text-xs text-white/45">Gilt genau für diese Domain, nicht für Unterdomains.</span>
          </div>
        </form>
      ) : (
        <p className="text-sm text-white/45">Listen ändern dürfen nur Konten, die auch Server steuern dürfen.</p>
      )}
    </Card>
  );
}

export function PiholePage(): JSX.Element {
  const unmountSignal = useUnmountSignal();
  const [data, setData] = useState<PiholeStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [message, setMessage] = useState<Message>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const canAct = deck().hasPermission("hosts.execute");

  const load = useCallback(() => {
    setLoadError(null);
    call<PiholeStatus>(`${API}/summary`).then(setData).catch((err: unknown) => setLoadError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(() => { load(); }, [load]);

  async function run(key: string, path: string, body: unknown): Promise<boolean> {
    setBusy(key);
    setMessage(null);
    let ok = false;
    try {
      const result = await proposeAndApprove(path, body, unmountSignal());
      setMessage(result);
      ok = result?.kind === "ok";
    } catch (err) {
      setMessage({ kind: "error", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(null);
      load();
    }
    return ok;
  }

  async function pause(minutes: number) {
    const ok = await deck().confirmDialog(
      `Pi-hole für ${minutes} Minuten pausieren? In dieser Zeit werden Werbung und Tracker im ganzen Netz nicht blockiert. Danach blockiert Pi-hole von selbst wieder.`,
      { confirmLabel: "Pausieren" },
    );
    if (!ok) return;
    await run("blocking", `${API}/pause`, { minutes });
  }

  async function gravity() {
    const ok = await deck().confirmDialog(
      "Alle Sperrlisten neu herunterladen? Das dauert ein paar Minuten; Pi-hole blockiert dabei weiter.",
      { title: "Sperrlisten aktualisieren", confirmLabel: "Aktualisieren" },
    );
    if (!ok) return;
    await run("gravity", `${API}/gravity`, undefined);
  }

  async function domain(list: "allow" | "deny", value: string): Promise<boolean> {
    const ok = list === "allow"
      ? await deck().confirmDialog(`„${value}“ erlauben? Pi-hole blockiert die Domain dann nicht mehr, auch wenn eine Sperrliste sie nennt.`, { title: "Domain erlauben", confirmLabel: "Erlauben" })
      : await deck().confirmDialog(`„${value}“ sperren? Geräte im Netz erreichen die Domain danach nicht mehr.`, { title: "Domain sperren", danger: true, confirmLabel: "Sperren" });
    if (!ok) return false;
    return run("domain", `${API}/domains`, { list, domain: value });
  }

  let body: JSX.Element | null = null;
  if (data?.state === "not_configured") {
    body = (
      <Card title="Pi-hole">
        <div data-testid="not-configured" className="flex flex-col items-start gap-3">
          <Badge>nicht eingerichtet</Badge>
          <p className="text-sm text-white/60">{data.message}</p>
          <SettingsHint />
        </div>
      </Card>
    );
  } else if (data && (data.state !== "ok" || !data.summary)) {
    const problem = PROBLEM[data.state as keyof typeof PROBLEM] ?? PROBLEM.unreachable;
    body = (
      <Card title="Pi-hole" actions={<Badge tone={problem.tone}>{problem.label}</Badge>}>
        <div data-testid="problem" className="flex flex-col items-start gap-3">
          <p className="text-sm text-white/70">{data.message}</p>
          <div className="flex flex-wrap items-center gap-2">
            <Button small onClick={load}>Erneut versuchen</Button>
            {(data.state === "auth_failed" || data.state === "unsupported") && deck().hasPermission("extensions.manage") && (
              <a href={SETTINGS_PATH} className={buttonClass("ghost", true)}>Einstellungen</a>
            )}
          </div>
        </div>
      </Card>
    );
  } else if (data?.summary) {
    const s = data.summary;
    body = (
      <div className="space-y-4">
        <div data-testid="stats" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="Anfragen (24 Std.)" value={num(s.queries_total)} hint={s.clients_active != null ? `${num(s.clients_active)} aktive Geräte` : undefined} />
          <Stat label="Blockiert" value={num(s.queries_blocked)} />
          <Stat label="Anteil blockiert" value={pct(s.percent_blocked)} />
          <Stat label="Domains auf der Sperrliste" value={num(s.domains_blocked)} hint={`aktualisiert ${ago(s.gravity_updated_at)}`} />
        </div>
        <BlockingCard data={data} canAct={canAct} busy={busy} onPause={(m) => void pause(m)} onResume={() => void run("blocking", `${API}/resume`, undefined)} />
        <div className="grid gap-4 lg:grid-cols-2">
          <ClientsCard clients={data.top_clients ?? []} />
          <ListsCard data={data} canAct={canAct} busy={busy} onGravity={() => void gravity()} onDomain={domain} />
        </div>
      </div>
    );
  } else if (!loadError) {
    body = <Card title="Pi-hole"><p className="text-sm text-white/50">Lade …</p></Card>;
  }
  const adminHref = safeHref(data?.url);

  return (
    <Page
      title="Pi-hole"
      description="Werbe- und Tracker-Blocker für das ganze Netz."
      actions={
        <>
          {adminHref && <a href={`${adminHref}/admin/`} target="_blank" rel="noreferrer" className={buttonClass("ghost")}>Pi-hole öffnen</a>}
          {deck().hasPermission("extensions.manage") && <a href={SETTINGS_PATH} className={buttonClass("ghost")}>Einstellungen</a>}
          <Button onClick={load}>Aktualisieren</Button>
        </>
      }
    >
      {loadError && <Notice text={loadError} onClose={() => setLoadError(null)} />}
      {message && <Notice text={message.text} kind={message.kind} onClose={() => setMessage(null)} />}
      {body}
    </Page>
  );
}
