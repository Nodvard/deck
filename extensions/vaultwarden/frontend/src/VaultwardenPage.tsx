/**
 * Vaultwarden-Seite -- PageSpec `component="VaultwardenPage"` (nodvard_deck_ext_vaultwarden/__init__.py).
 *
 * Läuft Vaultwarden, welche Version, über HTTPS erreichbar, Registrierung offen oder zu, und wie sich die
 * Bitwarden-Apps verbinden. Nur lesen: Deck schickt Vaultwarden kein Passwort.
 */
import { useCallback, useEffect, useState } from "react";

import { authedFetch, errorFromBody } from "../../../_shared/frontend/src/api";
import { Badge, Button, Card, Notice, Page, Stat, buttonClass, type Tone } from "../../../_shared/frontend/src/ui";
import { deck } from "../../../_shared/frontend/src/deck";

const API = "/ext/vaultwarden";
const SETTINGS_PATH = "/settings/extensions/vaultwarden";

export interface VaultwardenStatus {
  state: "ok" | "not_configured" | "unreachable";
  message: string | null;
  url?: string;
  https?: boolean;
  version?: string | null;
  api_version?: string | null;
  server_name?: string | null;
  signups_open?: boolean | null;
  domain?: string | null;
  domain_matches?: boolean | null;
}

async function loadStatus(): Promise<VaultwardenStatus> {
  const res = await authedFetch(`${API}/summary`);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorFromBody(body, res.status));
  return body as VaultwardenStatus;
}

function SettingsHint() {
  if (deck().hasPermission("extensions.manage")) {
    return <a href={SETTINGS_PATH} className={buttonClass("primary", true)}>Jetzt einrichten</a>;
  }
  return <p className="text-xs text-white/45">Ein Administrator kann das unter Einstellungen → Erweiterungen → Vaultwarden einrichten.</p>;
}

function Hints({ data }: { data: VaultwardenStatus }) {
  const hints: { key: string; tone: Tone; label: string; text: string }[] = [];
  if (!data.https) {
    hints.push({
      key: "https", tone: "warn", label: "ohne HTTPS",
      text: "Der Web-Tresor im Browser funktioniert nur über HTTPS, die Bitwarden-Apps warnen ohne. Am einfachsten ein Proxy davor (Nginx Proxy Manager, Traefik) mit Zertifikat, dann hier die https-Adresse eintragen.",
    });
  }
  if (data.signups_open) {
    hints.push({
      key: "signups", tone: "warn", label: "Registrierung offen",
      text: "Jeder, der die Adresse kennt, kann ein Konto anlegen. Wenn dein Konto steht, in Vaultwarden SIGNUPS_ALLOWED=false setzen (oder in der Verwaltung unter /admin).",
    });
  }
  if (data.domain_matches === false && data.domain) {
    hints.push({
      key: "domain", tone: "warn", label: "andere Adresse",
      text: `In Vaultwarden ist ${data.domain} als Adresse eingetragen (DOMAIN). Passt sie nicht zur Adresse, unter der du es öffnest, gehen Anhänge und Links in Mails nicht.`,
    });
  }
  if (hints.length === 0) return null;
  return (
    <Card title="Hinweise">
      <ul data-testid="hints" className="space-y-3">
        {hints.map((h) => (
          <li key={h.key} className="flex flex-col gap-1 sm:flex-row sm:items-start sm:gap-3">
            <Badge tone={h.tone}>{h.label}</Badge>
            <span className="text-sm text-white/70">{h.text}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function AppsCard({ url }: { url: string }) {
  return (
    <Card title="Bitwarden-Apps verbinden" description="Handy, Windows, Browser-Erweiterung: überall dieselben Schritte.">
      <ol data-testid="apps-howto" className="list-decimal space-y-1 pl-5 text-sm text-white/70">
        <li>Bitwarden-App öffnen, vor dem Anmelden bei „Anmelden bei“ <strong>Selbst gehostet</strong> wählen.</li>
        <li>Als Server-Adresse <code className="rounded bg-white/[0.06] px-1">{url}</code> eintragen.</li>
        <li>Mit E-Mail und Master-Passwort anmelden. Das Master-Passwort kennt nur die App, nie Nodvard Deck.</li>
      </ol>
    </Card>
  );
}

export function VaultwardenPage(): JSX.Element {
  const [data, setData] = useState<VaultwardenStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoadError(null);
    loadStatus().then(setData).catch((err: unknown) => setLoadError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(() => { load(); }, [load]);

  let body: JSX.Element | null = null;
  if (data?.state === "not_configured") {
    body = (
      <Card title="Vaultwarden">
        <div data-testid="not-configured" className="flex flex-col items-start gap-3">
          <Badge>nicht eingerichtet</Badge>
          <p className="text-sm text-white/60">{data.message}</p>
          <SettingsHint />
        </div>
      </Card>
    );
  } else if (data && data.state !== "ok") {
    body = (
      <Card title="Vaultwarden" actions={<Badge tone="bad">nicht erreichbar</Badge>}>
        <div data-testid="problem" className="flex flex-col items-start gap-3">
          <p className="text-sm text-white/70">{data.message}</p>
          <Button small onClick={load}>Erneut versuchen</Button>
        </div>
      </Card>
    );
  } else if (data) {
    const signups = data.signups_open === true ? "offen" : data.signups_open === false ? "zu" : "unbekannt";
    body = (
      <div className="space-y-4">
        <div data-testid="stats" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="Zustand" value="läuft" />
          <Stat label="Version" value={data.version ?? "unbekannt"} hint={data.server_name ?? undefined} />
          <Stat label="Verbindung" value={data.https ? "HTTPS" : "ohne HTTPS"} />
          <Stat label="Registrierung" value={signups} />
        </div>
        <Hints data={data} />
        {data.url && <AppsCard url={data.url} />}
      </div>
    );
  } else if (!loadError) {
    body = <Card title="Vaultwarden"><p className="text-sm text-white/50">Lade …</p></Card>;
  }

  return (
    <Page
      title="Vaultwarden"
      description="Dein Passwort-Manager. Die Bitwarden-Apps sprechen direkt mit Vaultwarden."
      actions={
        <>
          {data?.url && <a href={`${data.url}/`} target="_blank" rel="noreferrer" className={buttonClass("ghost")}>Web-Tresor öffnen</a>}
          {deck().hasPermission("extensions.manage") && <a href={SETTINGS_PATH} className={buttonClass("ghost")}>Einstellungen</a>}
          <Button onClick={load}>Aktualisieren</Button>
        </>
      }
    >
      {loadError && <Notice text={loadError} onClose={() => setLoadError(null)} />}
      {body}
    </Page>
  );
}
