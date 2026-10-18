/**
 * „Mit Passwort verbinden“: Adresse und Passwort eines Proxmox-Administrators einmal eingeben, Nodvard Deck legt Rolle,
 * Benutzer, Berechtigung und API-Token selbst an und trägt die Verbindung ein (password_connect.py). Das Passwort geht
 * nur an ein anerkanntes oder per Fingerabdruck bestätigtes Zertifikat und wird beim Absenden aus dem Feld geleert.
 */
import { useId, useState } from "react";
import type { FormEvent, ReactNode } from "react";

import { authedFetch, errorFromBody } from "../../../_shared/frontend/src/api";
import { PasswordInClearWarning } from "../../../_shared/frontend/src/passwordWarning";
import { Button, inputClass } from "../../../_shared/frontend/src/ui";

import { OldTokenList, type OldToken } from "./OldTokens";

export interface PeerCertificate {
  sha256: string;
  trusted: boolean;
  subject: string | null;
  issuer: string | null;
  not_after: string | null;
  alt_names: string[];
}

interface BackupsOut {
  name: string;
  token_id: string;
  replaced_token_id: string | null;
  storages: string[];
  pool: string | null;
}

interface ConnectedOut {
  status: "connected";
  connection: {
    name: string; base_url: string; token_id: string; tls_insecure_skip_verify: boolean; tls_fingerprint_sha256?: string | null;
  };
  created: string[];
  nodes: string[];
  notes: string[];
  backups?: BackupsOut | null;
  old_tokens?: OldToken[];
  discovery?: "started" | "not_started";
}

interface TfaOut {
  status: "tfa_required";
  tfa: { flow: string; kinds: string[]; expires_in_s: number };
}

interface Failure {
  detail: string;
  code: string;
  certificate?: PeerCertificate;
}

const TFA_LABELS: Record<string, string> = {
  totp: "Code aus der App",
  recovery: "Wiederherstellungsschlüssel",
  yubico: "YubiKey (Einmal-Code)",
};

const CERTIFICATE_CODES = new Set(["certificate_unconfirmed", "certificate_changed", "certificate_changed_after_login"]);
// Nach dem Code zurück zur Anmeldung: Vorgang verbraucht – bei „secret_foreign“ mit anderem Kurznamen neu versuchen.
const LOGIN_AGAIN_CODES = new Set(["tfa_wrong", "tfa_expired", "secret_foreign"]);

async function post<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const res = await authedFetch(`/ext/proxmox${path}`, { method: "POST", body: JSON.stringify(body) });
  const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (!res.ok) {
    const failure: Failure = {
      detail: errorFromBody(data, res.status),
      code: typeof data.code === "string" ? data.code : "unknown",
      certificate: (data.certificate as PeerCertificate | undefined) ?? undefined,
    };
    throw failure;
  }
  return data as T;
}

function asFailure(err: unknown): Failure {
  if (typeof err === "object" && err !== null && "code" in err && "detail" in err) return err as Failure;
  return { detail: err instanceof Error ? err.message : String(err), code: "unknown" };
}

function formatDate(iso: string | null): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleDateString("de-DE");
}

function CertificateBox({
  cert, confirmed, onConfirm,
}: { cert: PeerCertificate; confirmed: boolean; onConfirm: (value: boolean) => void }) {
  if (cert.trusted) {
    return (
      <p className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200" data-testid="certificate-trusted">
        Das Zertifikat ist von einer anerkannten Stelle bestätigt. Dein Passwort geht verschlüsselt und geprüft an diesen Server.
      </p>
    );
  }
  const until = formatDate(cert.not_after);
  return (
    <div className="space-y-2 rounded-lg border border-amber-500/30 bg-amber-500/[0.06] p-3 text-sm" data-testid="certificate-confirm">
      <p>
        Proxmox nutzt ein eigenes Zertifikat. Vergleiche den Fingerabdruck, bevor du dein Passwort eingibst: In Proxmox
        links den Knoten anklicken → <strong>System</strong> → <strong>Zertifikate</strong>, Spalte
        „Fingerabdruck“ – bei <code>pveproxy-ssl.pem</code>, falls es die Zeile gibt, sonst bei <code>pve-ssl.pem</code>.
      </p>
      <p className="break-all rounded bg-black/30 px-2 py-1.5 font-mono text-xs" aria-label="Fingerabdruck (SHA-256)">{cert.sha256}</p>
      {(cert.subject || until) && (
        <p className="text-xs text-white/55">
          {cert.subject ? `Ausgestellt für ${cert.subject}` : ""}
          {cert.subject && until ? " · " : ""}
          {until ? `gültig bis ${until}` : ""}
        </p>
      )}
      <label className="flex cursor-pointer items-start gap-2.5">
        <input
          type="checkbox" className="mt-0.5 h-4 w-4 flex-none accent-[var(--color-accent)]"
          checked={confirmed} onChange={(e) => onConfirm(e.target.checked)}
        />
        <span>Der Fingerabdruck stimmt mit dem in Proxmox überein.</span>
      </label>
      <p className="text-xs text-white/55">
        Stimmt er nicht, gib hier nichts ein: Dann antwortet unter dieser Adresse vielleicht ein anderer Rechner.
        Stimmt er, merkt sich Nodvard Deck den Fingerabdruck und spricht auch später nur mit genau diesem Server.
      </p>
    </div>
  );
}

/** Eingabefeld mit Beschriftung und Hinweis darunter (der Hinweis gehört nicht zum Namen des Felds). */
function LabeledField({
  label, hint, children,
}: { label: string; hint?: string; children: (props: { id: string; "aria-describedby"?: string }) => ReactNode }) {
  const id = useId();
  const hintId = `${id}-hinweis`;
  return (
    <div className="block text-sm">
      <label htmlFor={id} className="mb-1.5 block text-white/70">{label}</label>
      {children({ id, "aria-describedby": hint ? hintId : undefined })}
      {hint && <p id={hintId} className="mt-1 text-xs text-white/45">{hint}</p>}
    </div>
  );
}

export function ProxmoxConnect({
  onConnected, onClose, pageLocation,
}: {
  onConnected: (name: string) => void;
  onClose: () => void;
  /** Adresse dieser Seite (Vorgabe: `window.location`) -- entscheidet über den Hinweis „ohne HTTPS“. */
  pageLocation?: Pick<Location, "protocol" | "hostname">;
}) {
  const [step, setStep] = useState<"address" | "login" | "tfa" | "done">("address");
  const [address, setAddress] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [cert, setCert] = useState<PeerCertificate | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [username, setUsername] = useState("root@pam");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [access, setAccess] = useState<"full" | "read">("full");
  const [backupsAvailable, setBackupsAvailable] = useState(false);
  const [withBackups, setWithBackups] = useState(true);
  const [tfa, setTfa] = useState<TfaOut["tfa"] | null>(null);
  const [tfaKind, setTfaKind] = useState("totp");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ConnectedOut | null>(null);

  function showFailure(failure: Failure) {
    setError(failure.detail);
    if (failure.certificate && CERTIFICATE_CODES.has(failure.code)) {
      setCert(failure.certificate);
      setConfirmed(false);
      setStep("login");
    }
  }

  function finish(out: ConnectedOut | TfaOut) {
    if (out.status === "tfa_required") {
      setTfa(out.tfa);
      setTfaKind(out.tfa.kinds[0] ?? "totp");
      setStep("tfa");
      return;
    }
    setResult(out);
    setStep("done");
    onConnected(out.connection.name);
  }

  async function checkCertificate(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const out = await post<{ base_url: string; certificate: PeerCertificate; backups_available?: boolean }>(
        "/connect/certificate", { base_url: address.trim() },
      );
      setBaseUrl(out.base_url);
      setCert(out.certificate);
      setBackupsAvailable(out.backups_available === true);
      setConfirmed(false);
      setStep("login");
    } catch (err) {
      setError(asFailure(err).detail);
    } finally {
      setBusy(false);
    }
  }

  async function connect(e: FormEvent) {
    e.preventDefault();
    // Derselbe Riegel wie am Knopf: ohne bestätigten Fingerabdruck (oder anerkanntes Zertifikat) geht nichts hinaus.
    if (!cert || (!cert.trusted && !confirmed)) return;
    // Das Passwort sofort aus dem Zustand nehmen -- es lebt nur noch in dieser einen Anfrage.
    const typed = password;
    setPassword("");
    setBusy(true);
    setError(null);
    try {
      finish(await post<ConnectedOut | TfaOut>("/connect", {
        base_url: baseUrl, username: username.trim(), password: typed, name: name.trim() || null,
        fingerprint: cert.trusted ? null : cert.sha256, access, backups: backupsAvailable && withBackups,
      }));
    } catch (err) {
      showFailure(asFailure(err));
    } finally {
      setBusy(false);
    }
  }

  async function sendCode(e: FormEvent) {
    e.preventDefault();
    if (!tfa) return;
    const typed = code;
    setCode("");
    setBusy(true);
    setError(null);
    try {
      finish(await post<ConnectedOut | TfaOut>("/connect/tfa", { flow: tfa.flow, code: typed, kind: tfaKind }));
    } catch (err) {
      const failure = asFailure(err);
      showFailure(failure);
      if (LOGIN_AGAIN_CODES.has(failure.code)) {
        setTfa(null);
        setStep("login");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mb-6 space-y-4 p-4 panel" data-testid="proxmox-connect" aria-label="Mit Passwort verbinden">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">Proxmox mit Passwort verbinden</h3>
          <p className="mt-0.5 max-w-2xl text-xs text-white/55">
            Du gibst einmal Adresse und Passwort ein, Nodvard Deck legt in Proxmox einen eigenen Zugang an (Benutzer
            nodvard@pve mit API-Token und passenden Rechten) und vergisst das Passwort sofort. Es wird nirgends gespeichert.
          </p>
        </div>
        <Button small variant="ghost" onClick={onClose}>{step === "done" ? "Schließen" : "Abbrechen"}</Button>
      </div>

      {step === "address" && (
        <form onSubmit={(e) => void checkCertificate(e)} className="flex flex-wrap items-end gap-2" noValidate>
          <div className="min-w-[16rem] flex-1">
            <LabeledField label="Adresse des Proxmox-Servers">
              {(p) => (
                <input
                  {...p} className={inputClass} value={address} onChange={(e) => setAddress(e.target.value)}
                  placeholder="https://192.168.2.10:8006" autoCapitalize="none" autoCorrect="off" spellCheck={false} autoComplete="off"
                />
              )}
            </LabeledField>
          </div>
          <Button type="submit" variant="primary" disabled={busy || !address.trim()}>{busy ? "Prüfe …" : "Weiter"}</Button>
        </form>
      )}

      {step === "login" && cert && (
        <form onSubmit={(e) => void connect(e)} className="space-y-4" noValidate>
          <p className="text-xs text-white/55">
            Server: <span className="font-mono">{baseUrl}</span>{" "}
            <button type="button" className="underline hover:text-white" onClick={() => { setStep("address"); setError(null); }}>ändern</button>
          </p>
          <CertificateBox cert={cert} confirmed={confirmed} onConfirm={setConfirmed} />
          <div className="grid gap-4 sm:grid-cols-2">
            <LabeledField label="Benutzer" hint="Mit Bereich, meist root@pam. Das Konto muss Benutzer und Rechte anlegen dürfen.">
              {(p) => (
                <input
                  {...p} className={inputClass} value={username} onChange={(e) => setUsername(e.target.value)}
                  autoCapitalize="none" autoCorrect="off" spellCheck={false} autoComplete="off" placeholder="root@pam"
                />
              )}
            </LabeledField>
            <LabeledField label="Passwort" hint="Nur für diese eine Anmeldung.">
              {(p) => (
                <input
                  {...p} type="password" className={inputClass} value={password} onChange={(e) => setPassword(e.target.value)}
                  autoComplete="new-password"
                />
              )}
            </LabeledField>
            <LabeledField label="Kurzname (freiwillig)" hint="Leer lassen: Nodvard Deck nimmt den Namen aus Proxmox.">
              {(p) => (
                <input
                  {...p} className={inputClass} value={name} onChange={(e) => setName(e.target.value)} placeholder="z. B. pve1"
                  autoCapitalize="none" autoCorrect="off" spellCheck={false} autoComplete="off"
                />
              )}
            </LabeledField>
            <fieldset className="text-sm">
              <legend className="mb-1.5 text-white/70">Was darf Nodvard Deck?</legend>
              <label className="flex cursor-pointer items-start gap-2">
                <input type="radio" name="access" className="mt-1" checked={access === "full"} onChange={() => setAccess("full")} />
                <span>Ansehen und steuern <span className="text-white/45">(Starten, Herunterfahren, Snapshots, Konsole …)</span></span>
              </label>
              <label className="mt-1 flex cursor-pointer items-start gap-2">
                <input type="radio" name="access" className="mt-1" checked={access === "read"} onChange={() => setAccess("read")} />
                <span>Nur ansehen</span>
              </label>
            </fieldset>
          </div>
          {backupsAvailable && (
            <label className="flex cursor-pointer items-start gap-2.5 text-sm">
              <input
                type="checkbox" className="mt-0.5 h-4 w-4 flex-none accent-[var(--color-accent)]"
                checked={withBackups} onChange={(e) => setWithBackups(e.target.checked)}
              />
              <span>
                Auch Proxmox-Backups verbinden
                <span className="block text-xs text-white/45">
                  {access === "full"
                    ? "Mit eigenem Zugang (Benutzer nodvard-backup@pve): Backups ansehen, starten und Jobs ändern, als Kopie wiederherstellen (nur in den Pool „nodvard-kopien“). Proxmox erlaubt diesem Zugang auch, Sicherungen zu löschen und einen Gast aus einer Sicherung zu überschreiben – Nodvard Deck tut beides nie."
                    : "Mit eigenem Zugang (Benutzer nodvard-backup@pve), der Backups nur ansieht."}
                </span>
              </span>
            </label>
          )}
          <PasswordInClearWarning alternative="Sicherer ist dann der Weg mit API-Token aus der Anleitung." location={pageLocation} />
          <Button type="submit" variant="primary" disabled={busy || !password || !username.trim() || (!cert.trusted && !confirmed)}>
            {busy ? "Verbinde …" : "Verbinden"}
          </Button>
        </form>
      )}

      {step === "tfa" && tfa && (
        <form onSubmit={(e) => void sendCode(e)} className="space-y-3" noValidate data-testid="proxmox-connect-tfa">
          <p className="text-sm">Proxmox verlangt einen zweiten Faktor. Gib den Code ein – dein Passwort geht dabei nicht noch einmal hinaus.</p>
          {tfa.kinds.length > 1 && (
            <div className="max-w-xs">
              <LabeledField label="Art">
                {(p) => (
                  <select {...p} className={inputClass} value={tfaKind} onChange={(e) => setTfaKind(e.target.value)}>
                    {tfa.kinds.map((kind) => <option key={kind} value={kind}>{TFA_LABELS[kind] ?? kind}</option>)}
                  </select>
                )}
              </LabeledField>
            </div>
          )}
          <div className="max-w-xs">
            <LabeledField label={TFA_LABELS[tfaKind] ?? "Code"}>
              {(p) => (
                <input
                  {...p} className={inputClass} value={code} onChange={(e) => setCode(e.target.value)} autoComplete="one-time-code"
                  inputMode={tfaKind === "totp" ? "numeric" : "text"} autoCapitalize="none" autoCorrect="off" spellCheck={false}
                />
              )}
            </LabeledField>
          </div>
          <Button type="submit" variant="primary" disabled={busy || !code.trim()}>{busy ? "Prüfe …" : "Bestätigen"}</Button>
        </form>
      )}

      {step === "done" && result && (
        <div className="space-y-2 text-sm" data-testid="proxmox-connect-done">
          <p role="status" className="text-emerald-300">
            Verbunden als „{result.connection.name}“ ({result.connection.token_id}).{" "}
            {result.discovery === "started"
              ? "Nodvard Deck liest Knoten, VMs und LXC jetzt ein – das dauert meist nur einen Moment."
              : "Nodvard Deck liest Knoten, VMs und LXC in den nächsten Minuten ein."}
          </p>
          {result.backups && (
            <p className="text-emerald-300" data-testid="proxmox-connect-backups">
              Proxmox-Backups ist als „{result.backups.name}“ verbunden ({result.backups.token_id}).
            </p>
          )}
          {result.created.length > 0 && (
            <div>
              <p className="text-xs text-white/55">In Proxmox angelegt:</p>
              <ul className="ml-4 list-disc text-xs text-white/70">
                {result.created.map((line) => <li key={line}>{line}</li>)}
              </ul>
            </div>
          )}
          {result.notes.map((note) => <p key={note} className="text-xs text-white/55">{note}</p>)}
          <OldTokenList tokens={result.old_tokens ?? []} />
        </div>
      )}

      {error && <p role="alert" className="text-sm text-red-300">{error}</p>}
    </section>
  );
}
