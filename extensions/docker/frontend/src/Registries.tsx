/** Private Registries je Server: wer angemeldet ist, Anmelden und Abmelden nur über die Freigabe. */
import { useEffect, useState, type ReactNode } from "react";

import { describeActionOutcome, settleAction, type ActionState } from "../../../_shared/frontend/src/actions";
import { deck } from "../../../_shared/frontend/src/deck";
import { SettingsLink } from "../../../_shared/frontend/src/links";
import { LoadError, errorMessage, fetchJson } from "../../../_shared/frontend/src/loadError";
import { Badge, Button } from "../../../_shared/frontend/src/ui";

import { HostSection } from "./Resources";
import { JSON_HEADERS, withResult } from "./StackEditor";

interface RegistryOut {
  name: string;
  address: string;
  username: string;
  logged_in: boolean | null;
  secret_set: boolean;
}

interface RegistryState {
  registries: RegistryOut[];
  creds_store: string | null;
  error: string | null;
  /** Einträge, die Deck nicht nutzt (etwa ein doppelter Name), als Sätze. */
  warnings?: string[];
}

type Verb = "login" | "logout";

const WAITING: Record<Verb, string> = {
  login: "Zum Anmelden vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe „Aktionen“.",
  logout: "Zum Abmelden vorgeschlagen. Es fehlt noch die Freigabe durch einen Admin, siehe „Aktionen“.",
};

function status(r: RegistryOut): ReactNode {
  if (r.logged_in === null) return <Badge>unbekannt</Badge>;
  return r.logged_in ? <Badge tone="good">angemeldet</Badge> : <Badge>nicht angemeldet</Badge>;
}

export function RegistryList({ hostId }: { hostId: string }): JSX.Element {
  const [state, setState] = useState<RegistryState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<{ text: string; warn: boolean } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const base = `/ext/docker/hosts/${encodeURIComponent(hostId)}/registries`;

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    fetchJson<RegistryState>(base, { signal: controller.signal })
      .then((body) => { if (!controller.signal.aborted) setState(body); })
      .catch((err: unknown) => { if (!controller.signal.aborted) setError(errorMessage(err)); });
    return () => controller.abort();
  }, [base, reload]);

  async function run(r: RegistryOut, verb: Verb) {
    const question = verb === "login"
      ? `Docker auf diesem Server bei ${r.address} als ${r.username} anmelden? Das Passwort aus dem Tresor geht dabei an Docker auf dem Server, `
        + "der es wie bei docker login von Hand dort speichert."
      : `Docker auf diesem Server bei ${r.address} abmelden? Images aus dieser Registry lassen sich danach nicht mehr holen.`;
    if (!(await deck().confirmDialog(question, { confirmLabel: verb === "login" ? "Anmelden" : "Abmelden" }))) return;
    setBusy(r.name);
    setMessage(null);
    let submitted = false;
    try {
      const proposal = await fetchJson<ActionState>(`${base}/${encodeURIComponent(r.name)}/${verb}`, { method: "POST", headers: JSON_HEADERS, body: "{}" });
      submitted = true;
      const done = await settleAction(proposal);
      const action = await withResult(done.action);
      const outcome = describeActionOutcome(action);
      if (action.status === "proposed") setMessage({ text: WAITING[verb], warn: false });
      else if (outcome.tone === "success") setMessage({ text: action.result?.output ?? outcome.text, warn: false });
      else if (outcome.tone === "pending") setMessage({ text: done.text, warn: false });
      else setMessage({ text: action.result?.error ?? outcome.text, warn: true });
      setReload((n) => n + 1);
    } catch (err) {
      const text = submitted ? `Das Ergebnis ließ sich nicht abrufen (${errorMessage(err)}). Wie es ausging, steht unter „Aktionen“.` : errorMessage(err);
      setMessage({ text, warn: true });
    } finally {
      setBusy(null);
    }
  }

  if (error) return <LoadError what="Die Registries" error={error} onRetry={() => setReload((n) => n + 1)} />;
  if (!state) return <p className="text-sm opacity-60">Lese die Anmeldungen von Docker …</p>;
  const settings = <SettingsLink to="/settings/extensions/docker" permission="extensions.manage" variant="secondary">Moduleinstellungen</SettingsLink>;
  const warnings = (state.warnings ?? []).map((w) => <p key={w} className="text-sm text-amber-300" role="alert">{w}</p>);
  if (state.registries.length === 0) {
    return (
      <div className="flex flex-col items-start gap-2 text-sm" data-testid="registries">
        {warnings}
        {warnings.length === 0 && <p className="opacity-70">
          Noch keine private Registry eingetragen. Brauchst du Images, die nur mit Anmeldung zu holen sind (etwa private Pakete auf ghcr.io),
          trag die Registry in den Einstellungen des Moduls unter „Erweitert“ ein, mit Passwort oder Token für den Tresor.
        </p>}
        {settings}
      </div>
    );
  }
  const canChange = deck().hasPermission("hosts.execute");
  return (
    <div className="flex flex-col gap-2" data-testid="registries">
      <p className="text-xs opacity-60">
        Angemeldet wird je Server. Docker speichert die Anmeldung dort in ~/.docker/config.json des Zugangs von Nodvard Deck
        {state.creds_store ? ` über das Hilfsprogramm „${state.creds_store}“` : ", nur umkodiert, nicht verschlüsselt"}, wie bei docker login von Hand.
      </p>
      {warnings}
      {state.error && <LoadError what="Die Anmeldungen" error={state.error} onRetry={() => setReload((n) => n + 1)} compact />}
      {message && <p className={`whitespace-pre-line break-words text-sm ${message.warn ? "text-red-400" : "opacity-90"}`} role="status">{message.text}</p>}
      <ul className="divide-y divide-white/5 text-sm">
        {state.registries.map((r) => (
          <li key={r.name} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2" data-testid={`registry-${r.name}`}>
            <div className="min-w-0 flex-1">
              <p className="break-words">{r.name} {status(r)}</p>
              <p className="break-all text-xs opacity-60">{r.address} · als {r.username}</p>
              {!r.secret_set && <p className="text-xs text-amber-300">Kein Passwort im Tresor. Trag es in den Einstellungen des Moduls ein.</p>}
            </div>
            {canChange && (
              <div className="flex shrink-0 gap-1.5">
                {r.logged_in !== true && (
                  <Button small variant="primary" disabled={busy !== null || !r.secret_set} onClick={() => void run(r, "login")} ariaLabel={`Bei ${r.name} anmelden`}>
                    {busy === r.name ? "…" : "Anmelden"}
                  </Button>
                )}
                {r.logged_in !== false && (
                  <Button small variant="secondary" disabled={busy !== null} onClick={() => void run(r, "logout")} ariaLabel={`Bei ${r.name} abmelden`}>
                    Abmelden
                  </Button>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
      <div>{settings}</div>
    </div>
  );
}

export function RegistriesSection({ hostId, onHost, empty }: { hostId: string | null; onHost: (id: string) => void; empty: ReactNode }): JSX.Element {
  return (
    <HostSection hostId={hostId} onHost={onHost} empty={empty}>
      {(id) => <RegistryList key={id} hostId={id} />}
    </HostSection>
  );
}
