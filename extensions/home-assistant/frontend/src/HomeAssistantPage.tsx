/** Home-Assistant-Seite (PageSpec `component="HomeAssistantPage"`): je Verbindung eine Karte, nur lesend. */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { errorMessage, fetchJson, LoadError } from "../../../_shared/frontend/src/loadError";
import { SettingsLink } from "../../../_shared/frontend/src/links";
import { Badge, Button, Card, EmptyState, Page, Stat, buttonClass, type Tone } from "../../../_shared/frontend/src/ui";
import { deck } from "../../../_shared/frontend/src/deck";

const API = "/ext/home-assistant";
const SETTINGS_PATH = "/settings/extensions/home-assistant";
const OPEN_LIST_UP_TO = 10;

export type ConnectionState = "ok" | "not_configured" | "auth_failed" | "unreachable" | "error";
export type EntityStatus = "ok" | "unavailable" | "unknown" | "missing";

export interface Counts {
  total: number;
  updates: number;
  unavailable: number;
  unknown: number;
}

export interface UpdateItem {
  entity_id: string;
  name: string;
  installed_version: string | null;
  latest_version: string | null;
}

export interface UnavailableItem {
  entity_id: string;
  name: string;
}

export interface EntityValue {
  entity_id: string;
  name: string;
  status: EntityStatus | string;
  state: string | null;
  unit: string | null;
}

export interface Connection {
  name: string;
  url: string | null;
  state: ConnectionState | string;
  message: string | null;
  version: string | null;
  location_name: string | null;
  counts: Counts | null;
  updates: UpdateItem[];
  unavailable: UnavailableItem[];
  entities: EntityValue[];
}

export interface Overview {
  connections: Connection[];
  summary: { connections: number; updates: number; unavailable: number; unknown: number };
}

const PROBLEM: Record<Exclude<ConnectionState, "ok" | "not_configured">, { label: string; tone: Tone }> = {
  auth_failed: { label: "Anmeldung fehlgeschlagen", tone: "bad" },
  unreachable: { label: "nicht erreichbar", tone: "bad" },
  error: { label: "Fehler", tone: "warn" },
};

const DECIMAL = /^-?\d+\.\d+$/;

/** 21.5 und °C werden zu "21,5 °C", wie in der Kachel. */
export function valueText(state: string | null, unit: string | null): string {
  if (state === null) return "–";
  const text = DECIMAL.test(state) ? state.replace(".", ",") : state;
  return unit ? `${text} ${unit}` : text;
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

function headline(connection: Connection): { label: string; tone: Tone } {
  if (connection.state === "not_configured") return { label: "nicht eingerichtet", tone: "neutral" };
  if (connection.state !== "ok") return PROBLEM[connection.state as keyof typeof PROBLEM] ?? PROBLEM.error;
  const counts = connection.counts;
  if (counts && counts.updates > 0) return { label: plural(counts.updates, "Update", "Updates"), tone: "warn" };
  if (counts && counts.unavailable > 0) return { label: `${counts.unavailable} nicht verfügbar`, tone: "warn" };
  return { label: "alles in Ordnung", tone: "good" };
}

function EntityRow({ entity }: { entity: EntityValue }) {
  const shownName = entity.status === "missing" ? entity.entity_id : entity.name;
  let value: JSX.Element;
  if (entity.status === "unavailable") value = <Badge tone="warn">nicht verfügbar</Badge>;
  else if (entity.status === "unknown") value = <Badge tone="neutral">unbekannt</Badge>;
  else if (entity.status === "missing") value = <Badge tone="warn">nicht gefunden</Badge>;
  else value = <span className="text-sm font-medium tabular-nums">{valueText(entity.state, entity.unit)}</span>;
  return (
    <li data-testid="entity" className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-3 sm:px-5">
      <div className="min-w-0 flex-1 basis-48">
        <p className="break-words text-sm font-medium">{shownName}</p>
        <p className="break-all text-xs text-white/50">
          {entity.status === "missing" ? "Diese Kennung gibt es in Home Assistant nicht." : entity.entity_id}
        </p>
      </div>
      <div className="flex-none break-words text-right">{value}</div>
    </li>
  );
}

function UpdateRow({ update }: { update: UpdateItem }) {
  const versions = [update.installed_version, update.latest_version];
  return (
    <li data-testid="update" className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 bg-amber-500/[0.06] px-4 py-3 sm:px-5">
      <div className="min-w-0 flex-1 basis-48">
        <p className="break-words text-sm font-medium">{update.name}</p>
        <p className="break-all text-xs text-white/50">{update.entity_id}</p>
      </div>
      {(versions[0] || versions[1]) && (
        <span className="break-all text-xs text-white/70">{versions[0] ?? "?"} → {versions[1] ?? "?"}</span>
      )}
    </li>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="border-t border-white/[0.06] pt-3">
      <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-white/50">{title}</h4>
      {children}
    </div>
  );
}

function ConnectionCard({ connection, onRetry }: { connection: Connection; onRetry: () => void }) {
  const head = headline(connection);
  const counts = connection.counts;
  const problem = connection.state !== "ok" && connection.state !== "not_configured";
  const canManage = deck().hasPermission("extensions.manage");
  const facts = [
    connection.version ? `Version ${connection.version}` : null,
    connection.location_name ? `Standort ${connection.location_name}` : null,
  ].filter(Boolean);
  const hiddenUpdates = counts ? counts.updates - connection.updates.length : 0;
  const hiddenUnavailable = counts ? counts.unavailable - connection.unavailable.length : 0;
  return (
    <Card
      title={connection.name}
      description={connection.url ? <a href={connection.url} target="_blank" rel="noreferrer" className="break-all hover:text-white hover:underline">Home Assistant öffnen</a> : undefined}
      actions={<Badge tone={head.tone}>{head.label}</Badge>}
    >
      <div data-testid={`connection-${connection.name}`} className="space-y-4">
        {facts.length > 0 && <p data-testid="facts" className="break-words text-sm text-white/70">{facts.join(" · ")}</p>}
        {connection.state === "not_configured" && (
          <div data-testid="not-configured" className="flex flex-col items-start gap-3">
            <p className="text-sm text-white/60">{connection.message}</p>
            {canManage
              ? <a href={SETTINGS_PATH} className={buttonClass("primary", true)}>Jetzt einrichten</a>
              : <p className="text-xs text-white/45">Ein Administrator kann das unter Einstellungen → Erweiterungen → Home Assistant einrichten.</p>}
          </div>
        )}
        {problem && (
          <div data-testid="problem" role="alert" className="flex flex-col items-start gap-3">
            <p className="break-words text-sm text-red-300">{connection.message}</p>
            <div className="flex flex-wrap items-center gap-2">
              {/* Nach einem abgelehnten Token fragt das Dashboard nicht erneut, sonst sperrt Home Assistant die Adresse. */}
              {connection.state !== "auth_failed" && <Button small onClick={onRetry}>Erneut versuchen</Button>}
              {canManage && <a href={SETTINGS_PATH} className={buttonClass("ghost", true)}>Einstellungen</a>}
            </div>
          </div>
        )}
        {counts && (
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Entitäten" value={counts.total} />
            <Stat label="Updates" value={counts.updates} tone={counts.updates > 0 ? "warn" : undefined} />
            <Stat label="Nicht verfügbar" value={counts.unavailable} tone={counts.unavailable > 0 ? "warn" : undefined} />
            <Stat label="Unbekannt" value={counts.unknown} />
          </div>
        )}
        {connection.state === "ok" && counts && (
          <Section title="Updates">
            {connection.updates.length === 0 ? (
              <p data-testid="no-updates" className="text-sm text-white/50">Alles aktuell, es steht kein Update bereit.</p>
            ) : (
              <>
                <ul data-testid="updates" className="-mx-5 divide-y divide-white/[0.06] border-t border-white/[0.06]">
                  {connection.updates.map((u) => <UpdateRow key={u.entity_id} update={u} />)}
                </ul>
                {hiddenUpdates > 0 && <p className="mt-2 text-xs text-white/45">… und {plural(hiddenUpdates, "weiteres", "weitere")} (hier werden die ersten {connection.updates.length} gezeigt).</p>}
              </>
            )}
          </Section>
        )}
        {connection.state === "ok" && connection.entities.length > 0 && (
          <Section title="Ausgewählte Werte">
            <ul data-testid="entities" className="-mx-5 divide-y divide-white/[0.06] border-t border-white/[0.06]">
              {connection.entities.map((e) => <EntityRow key={e.entity_id} entity={e} />)}
            </ul>
          </Section>
        )}
        {connection.state === "ok" && connection.entities.length === 0 && (
          <p data-testid="entities-hint" className="text-xs text-white/45">
            Soll hier der Wert einzelner Geräte stehen (z. B. eine Temperatur), trage ihre Kennungen unter Einstellungen → Erweiterungen → Home Assistant ein.
          </p>
        )}
        {connection.state === "ok" && counts && counts.unavailable > 0 && (
          <Section title="Nicht verfügbar">
            <details data-testid="unavailable" open={connection.unavailable.length <= OPEN_LIST_UP_TO}>
              <summary className="cursor-pointer text-sm text-white/70">{plural(counts.unavailable, "Entität ist", "Entitäten sind")} nicht verfügbar</summary>
              <ul className="mt-2 divide-y divide-white/[0.06]">
                {connection.unavailable.map((u) => (
                  <li key={u.entity_id} data-testid="unavailable-item" className="py-2 first:pt-0 last:pb-0">
                    <p className="break-words text-sm font-medium">{u.name}</p>
                    <p className="break-all text-xs text-white/50">{u.entity_id}</p>
                  </li>
                ))}
              </ul>
              {hiddenUnavailable > 0 && <p className="mt-2 text-xs text-white/45">… und {hiddenUnavailable} weitere (hier werden die ersten {connection.unavailable.length} gezeigt).</p>}
            </details>
          </Section>
        )}
      </div>
    </Card>
  );
}

export function HomeAssistantPage(): JSX.Element {
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

  const connections = data?.connections ?? [];
  return (
    <Page
      title="Home Assistant"
      description="Zeigt Version, bereitstehende Updates, nicht verfügbare Geräte und ausgewählte Werte deines Home Assistant. Nur lesend."
      actions={
        <>
          {deck().hasPermission("extensions.manage") && <a href={SETTINGS_PATH} className={buttonClass("ghost")}>Einstellungen</a>}
          <Button onClick={reload} disabled={loading}>Aktualisieren</Button>
        </>
      }
    >
      {error && <LoadError what="Der Zustand von Home Assistant" error={error} onRetry={reload} />}
      {!data && !error && <Card><p className="text-sm text-white/50">Lade …</p></Card>}
      {data && connections.length === 0 && (
        <EmptyState
          icon="clock"
          title="Noch keine Verbindung"
          text={
            deck().hasPermission("extensions.manage")
              ? "Trage Home Assistant unter Einstellungen → Erweiterungen → Home Assistant ein."
              : "Ein Administrator kann Home Assistant unter Einstellungen → Erweiterungen → Home Assistant eintragen."
          }
          action={<SettingsLink to={SETTINGS_PATH} permission="extensions.manage">Jetzt einrichten</SettingsLink>}
        />
      )}
      {connections.length > 0 && (
        <div className="space-y-6">
          {connections.map((c, i) => <ConnectionCard key={`${c.name}-${i}`} connection={c} onRetry={reload} />)}
        </div>
      )}
    </Page>
  );
}
