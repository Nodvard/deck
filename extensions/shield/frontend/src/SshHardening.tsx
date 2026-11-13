import { useCallback, useEffect, useRef, useState } from "react";
import { Badge, Button, inputClass } from "../../../_shared/frontend/src/ui";
import { deck } from "../../../_shared/frontend/src/deck";
import { API, call, proposeAndApprove, track, usePageActions, useReloadWhenSettled } from "./api";
import { RunNotices } from "./RunNotice";

interface State {
  has_key: boolean;
  authorized: boolean;
  hardened: boolean | null;
  managed: boolean;
  root: boolean;
  warning: string | null;
  root_keys?: boolean;
  two_factor?: boolean;
  unconfirmed?: boolean;
  error?: string;
}

const rootKeysNote = "Für root sind Schlüssel eingetragen. Damit niemand ausgesperrt wird, bleibt root mit Schlüssel erlaubt; gesperrt wird nur die Anmeldung mit Passwort.";

interface Credential {
  id: string;
  kind: string;
  username: string;
  port: number;
  is_default: boolean;
}

const labels = { on: "SSH härten", off: "Härtung zurücknehmen", "add-key": "Deck-Schlüssel eintragen" };

export function SshHardening({ hostId, canManage }: { hostId: string; canManage: boolean }) {
  const [state, setState] = useState<State | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [credentials, setCredentials] = useState<Credential[] | null>(null);
  const [credentialReload, setCredentialReload] = useState(0);
  const [credentialId, setCredentialId] = useState("");
  const [keyId, setKeyId] = useState("");
  const requestVersion = useRef(0);
  const page = usePageActions();
  const key = `${hostId}:ssh`;
  const path = `${API}/hosts/${hostId}/ssh-hardening`;
  const login = credentials?.find((item) => item.id === credentialId);
  const keys = credentials?.filter((item) => item.kind === "ssh_key" && item.username === login?.username && item.port === login?.port) ?? [];
  useEffect(() => {
    let cancelled = false;
    setCredentials(null);
    setState(null);
    call<Credential[]>(`/hosts/${encodeURIComponent(hostId)}/credentials`).then((data) => {
      if (cancelled) return;
      const accesses = data.filter((item) => item.kind === "ssh_key" || item.kind === "ssh_password");
      const initial = accesses.find((item) => item.is_default) ?? accesses[0];
      setCredentials(accesses);
      setCredentialId(initial?.id ?? "");
      setKeyId(initial?.kind === "ssh_key" ? initial.id : accesses.find((item) => item.kind === "ssh_key" && item.username === initial?.username && item.port === initial?.port)?.id ?? "");
    }).catch((err: Error) => { if (!cancelled) setError(err.message); });
    return () => { cancelled = true; requestVersion.current += 1; };
  }, [hostId, credentialReload]);
  const load = useCallback(() => {
    if (!credentials) return;
    const version = ++requestVersion.current;
    setState(null);
    const query = new URLSearchParams();
    if (credentialId) query.set("credential_id", credentialId);
    if (keyId) query.set("key_credential_id", keyId);
    call<State>(`${path}?${query}`).then((data) => {
      if (version === requestVersion.current) { setState(data); setError(null); }
    }).catch((err: Error) => {
      if (version === requestVersion.current) { setState(null); setError(err.message); }
    });
  }, [path, credentials, credentialId, keyId]);
  useEffect(load, [load]);
  useReloadWhenSettled(page.keys.busy, load);

  async function change(mode: keyof typeof labels) {
    const description = mode === "on"
      ? `Passwortanmeldungen ausschalten und höchstens drei Anmeldeversuche erlauben? Wer sich bisher mit Passwort anmeldet, braucht danach einen Schlüssel. Du kommst danach nur noch mit einem Schlüssel auf den Server. Hast du einen eigenen Schlüssel oder einen Konsolenzugang (zum Beispiel über Proxmox)? ${state?.root ? state.warning : state?.root_keys ? rootKeysNote : "Root-Anmeldungen werden gesperrt."} Deck prüft danach eine neue Schlüsselverbindung und nimmt die Änderung bei einem Fehler zurück.`
      : mode === "off"
        ? "Die Härtungsdatei entfernen und SSH neu laden? Danach gelten wieder die übrigen SSH-Einstellungen."
        : "Den öffentlichen Deck-Schlüssel in authorized_keys des Zugangsnutzers eintragen?";
    if (!await deck().confirmDialog(description, { confirmLabel: labels[mode], danger: true })) return;
    const selection = mode === "on" ? {} : { credential_id: credentialId, ...(keyId ? { key_credential_id: keyId } : {}) };
    await track(page, key, labels[mode], `${labels[mode]} läuft …`, () =>
      proposeAndApprove(path, { mode, ...selection }, "high", page.signal()));
    load();
  }

  return (
    <section className="mb-4 space-y-2 text-sm" aria-label="SSH-Härtung">
      {page.notices && <RunNotices notices={page.notices} onClose={(id) => page.report(id, null)} />}
      <Badge tone={state?.hardened ? "good" : "neutral"}>
        SSH: {state && state.hardened !== null ? state.hardened ? "gehärtet" : "nicht gehärtet" : "Zustand unbekannt"}
      </Badge>
      {error && <p className="text-xs text-amber-200">{error}</p>}
      {state?.error && <p className="text-xs text-amber-200">{state.error}</p>}
      {canManage && credentials && credentials.length > 0 && <div className="flex flex-wrap gap-2">
        <label className="space-y-1">Anmelden mit
          <select aria-label="Anmelden mit" className={inputClass} value={credentialId} disabled={page.keys.busy.has(key)} onChange={(event) => {
            const selected = credentials.find((item) => item.id === event.target.value);
            setState(null);
            setCredentialId(event.target.value);
            setKeyId(selected?.kind === "ssh_key" ? selected.id : credentials.find((item) => item.kind === "ssh_key" && item.username === selected?.username && item.port === selected?.port)?.id ?? "");
          }}>
            {credentials.map((item) => <option key={item.id} value={item.id}>{item.username}:{item.port} ({item.kind === "ssh_key" ? "Schlüssel" : "Passwort"}{item.is_default ? ", Standard" : ""})</option>)}
          </select>
        </label>
        <label className="space-y-1">Einzutragender Schlüssel
          <select aria-label="Einzutragender Schlüssel" className={inputClass} value={keyId} disabled={!keys.length || page.keys.busy.has(key)} onChange={(event) => { setState(null); setKeyId(event.target.value); }}>
            {!keys.length && <option value="">Kein passender Schlüssel hinterlegt</option>}
            {keys.map((item) => <option key={item.id} value={item.id}>{item.username}:{item.port} ({item.is_default ? "Standard" : item.id.slice(0, 8)})</option>)}
          </select>
        </label>
      </div>}
      {state && !state.has_key && <p className="text-xs text-white/60">Der Deck-Zugang nutzt nur ein Passwort. Bitte zuerst einen SSH-Schlüssel hinterlegen.</p>}
      {state?.two_factor && <p className="text-xs text-amber-200">Dieser Server verlangt beim SSH-Login einen zweiten Faktor (PAM oder AuthenticationMethods). Die Härtung würde Schlüssel plus Code brechen und wird deshalb nicht angeboten.</p>}
      {state?.unconfirmed && <p className="text-xs text-amber-200">Eine Härtung wurde nach einem Neustart oder Abbruch nicht bestätigt. Bitte „Härtung zurücknehmen“ wählen und danach erneut härten.</p>}
      {state?.root && <p className="text-xs text-amber-200">Warnung: {state.warning}</p>}
      {!state?.root && state?.root_keys && <p className="text-xs text-white/60">{rootKeysNote}</p>}
      {state?.has_key && !state.error && !state.authorized && <p className="text-xs text-white/60">Der Deck-Schlüssel fehlt in authorized_keys. Bitte zuerst eintragen.</p>}
      {canManage && login?.kind === "ssh_key" && credentialId === keyId && (error || (state?.has_key && !state.authorized)) && <p className="text-xs text-amber-200">Zum Eintragen zuerst unter „Anmelden mit“ einen bereits funktionierenden Zugang wählen, zum Beispiel einen Passwortzugang.</p>}
      {state?.has_key && (login?.kind !== "ssh_key" || !login.is_default || keyId !== credentialId) && <p className="text-xs text-white/60">Zum Härten den eingetragenen Schlüssel unter Einstellungen → Server & Zugänge als Standard setzen und hier als Anmeldezugang wählen.</p>}
      {canManage && <div className="flex flex-wrap gap-2">
        <Button small disabled={!state || !state.has_key || !state.authorized || state.managed || !!state.two_factor || login?.kind !== "ssh_key" || !login.is_default || keyId !== credentialId || page.keys.busy.has(key)} onClick={() => void change("on")}>SSH härten</Button>
        {state && !state.has_key && deck().hasPermission("hosts.write") && <a className="underline" href="/settings/hosts">Schlüssel hinterlegen</a>}
        {state?.has_key && !state.error && !state.authorized && <Button small disabled={credentialId === keyId || page.keys.busy.has(key)} onClick={() => void change("add-key")}>Deck-Schlüssel eintragen</Button>}
        {state?.managed && <Button small disabled={page.keys.busy.has(key)} onClick={() => void change("off")}>Härtung zurücknehmen</Button>}
        <Button small variant="ghost" disabled={page.keys.busy.has(key)} onClick={() => credentials ? load() : setCredentialReload((value) => value + 1)}>SSH-Zustand prüfen</Button>
      </div>}
    </section>
  );
}
