/**
 * „Mit Passwort einrichten“: Man gibt einmal Benutzer und Passwort eines Kontos ein, das sich per SSH anmelden darf
 * (root oder mit sudo); Nodvard Deck führt damit denselben Befehl aus, vergisst das Passwort und prüft danach die
 * Anmeldung mit dem neuen Schlüssel (`POST /hosts/{id}/credentials/{cid}/setup-with-password`).
 */
import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { api, ApiError } from "../../../lib/api";
import {
  fieldErrors, hostKeyFile, refreshHosts, sendsInClear,
  type CheckItem, type ConnectionCheck as CheckResult, type CredentialOut, type HostKeyInfo, type HostOut,
  type SetupWithPasswordError, type SetupWithPasswordOut,
} from "../../../lib/hosts";
import { Button, NoticeLine, inputClass, type Notice } from "../ui";
import { CheckResultList, KeyChanged, KeyConfirm } from "./ConnectionCheck";
import { FormField } from "./FormField";

const CHECKBOX = "mt-0.5 h-4 w-4 flex-none accent-[var(--color-accent)]";

function failureOf(err: unknown): SetupWithPasswordError {
  if (err instanceof ApiError && typeof err.detail === "object" && err.detail !== null && "code" in err.detail) {
    const body = err.detail as Partial<SetupWithPasswordError>;
    return { detail: err.message, code: String(body.code), host_key: body.host_key, messages: body.messages };
  }
  return { detail: err instanceof Error ? err.message : String(err), code: "unknown" };
}

function Messages({ lines }: { lines: string[] }) {
  if (lines.length === 0) return null;
  return (
    <ul className="space-y-0.5 rounded-lg border border-white/[0.08] bg-black/20 px-3 py-2 font-mono text-[11px] text-white/75" aria-label="Meldungen des Befehls">
      {lines.map((line) => <li key={line} className="break-words">{line}</li>)}
    </ul>
  );
}

function Failure({
  failure, confirming, onConfirmKey,
}: { failure: SetupWithPasswordError; confirming: boolean; onConfirmKey: (key: HostKeyInfo) => void }) {
  const key = failure.host_key;
  if (failure.code === "host_key_unconfirmed" && key) {
    const item: CheckItem = {
      id: "host_key", label: "Server-Schlüssel", status: "confirm",
      detail: "Nodvard Deck kennt diesen Server noch nicht. Dein Passwort wurde nicht gesendet.",
      hint: `Zum Vergleichen auf dem Server: ssh-keygen -lf ${hostKeyFile(key.key_type)}`,
    };
    return (
      <div className="rounded-lg border border-amber-500/30 bg-amber-500/[0.06] p-3 text-sm" data-testid="password-setup-key">
        <KeyConfirm item={item} hostKey={key} busy={confirming} onConfirm={() => onConfirmKey(key)} />
      </div>
    );
  }
  if (failure.code === "host_key_changed" && key) {
    const item: CheckItem = {
      id: "host_key", label: "Server-Schlüssel", status: "fail", detail: failure.detail,
      hint: "Dein Passwort wurde nicht gesendet. Das passiert nach einer Neuinstallation – oder wenn sich jemand dazwischenschaltet.",
    };
    return <KeyChanged item={item} hostKey={key} />;
  }
  return (
    <div className="space-y-2">
      <p role="alert" className="text-sm text-red-300">{failure.detail}</p>
      <Messages lines={failure.messages ?? []} />
    </div>
  );
}

export function PasswordSetup({
  host, credential, sudo, fullSudo = false, groups, ready = true, knownNotes = [], onChecked,
}: {
  host: HostOut;
  credential: CredentialOut;
  /** Wie beim Befehl zum Einfügen: root-Rechte ohne Passwort für den Benutzer des Zugangs. */
  sudo: boolean;
  /** Zu `sudo` die volle Regel statt nur der Regeln der Hilfsskripte. */
  fullSudo?: boolean;
  groups: string[];
  /** Sind die Haken schon vorbelegt (was die Erweiterungen brauchen)? Vorher kein Absenden. */
  ready?: boolean;
  /** Hinweise, die schon beim Befehl stehen: im Ergebnis nicht noch einmal. */
  knownNotes?: string[];
  /** Ergebnis der Prüfung mit dem neuen Schlüssel (z. B. um „Neuen Zugang verwenden“ freizugeben). */
  onChecked?: (result: CheckResult) => void;
}) {
  const queryClient = useQueryClient();
  const [loginUser, setLoginUser] = useState("root");
  const [password, setPassword] = useState("");
  const [samePassword, setSamePassword] = useState(true);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [failure, setFailure] = useState<SetupWithPasswordError | null>(null);
  const [result, setResult] = useState<SetupWithPasswordOut | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  // Ohne HTTPS (und nicht direkt auf diesem Rechner) steht ein deutlicher Hinweis da, aber keine Sperre.
  const inClear = sendsInClear(window.location);
  const asRoot = loginUser.trim() === "root";

  async function submit(e: FormEvent) {
    e.preventDefault();
    // Das Passwort sofort aus dem Zustand nehmen: Es lebt nur noch in dieser einen Anfrage, nie in `useMutation`,
    // einem Query-Key, der Adresse oder dem Browser-Speicher.
    const typed = password;
    setPassword("");
    setErrors({});
    setFailure(null);
    setResult(null);
    setNotice(null);
    setBusy(true);
    try {
      const out = await api.post<SetupWithPasswordOut>(`/hosts/${host.id}/credentials/${credential.id}/setup-with-password`, {
        login_user: loginUser.trim(), password: typed, sudo, full_sudo: sudo && fullSudo, groups, sudo_password_same: samePassword,
      });
      setResult(out);
      if (out.check) onChecked?.(out.check);
      await refreshHosts(queryClient);
    } catch (err) {
      const perField = fieldErrors(err);
      setErrors(perField);
      if (Object.keys(perField).length === 0) setFailure(failureOf(err));
    } finally {
      setBusy(false);
    }
  }

  async function confirmKey(key: HostKeyInfo) {
    setConfirming(true);
    setNotice(null);
    try {
      // Genau der Fingerabdruck, den der Server gerade gezeigt hat (das Backend vergleicht ihn mit seinem Merkzettel),
      // und dieser Zugang: gesehen wurde der Schlüssel auf seinem Port.
      await api.post(`/hosts/${host.id}/known-hosts`, { key_type: key.key_type, fingerprint: key.fingerprint, credential_id: credential.id });
      setFailure(null);
      setNotice({ kind: "ok", text: "Fingerabdruck bestätigt. Gib das Passwort noch einmal ein und drück „Einrichten“." });
      await refreshHosts(queryClient);
    } catch (err) {
      setNotice({ kind: "error", text: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setConfirming(false);
    }
  }

  const extraNotes = result ? result.notes.filter((n) => !knownNotes.includes(n)) : [];

  // Das Passwortfeld nutzt `autocomplete="new-password"`, weil viele Browser „off“ bei Passwortfeldern übergehen: So
  // setzt der Browser kein gespeichertes Passwort ein und bietet nicht an, es zu merken.
  return (
    <form onSubmit={(e) => void submit(e)} noValidate className="space-y-4" data-testid="password-setup">
      <p className="text-sm text-white/70">
        Nodvard Deck meldet sich einmal mit einem Konto an, das sich per SSH anmelden darf (root oder ein Konto mit sudo),
        führt genau den Befehl aus und vergisst das Passwort sofort. Es wird nirgends gespeichert.
      </p>
      {inClear && (
        <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200" data-testid="password-in-clear">
          Achtung: Diese Seite läuft ohne HTTPS. Das Passwort geht unverschlüsselt durchs Netz – im eigenen Heimnetz meist in
          Ordnung, in einem fremden Netz nicht. Sicherer ist dann „Befehl selbst einfügen“.
        </p>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <FormField
          label="Anmelde-Konto"
          hint="Meist root. Lässt der Server root nicht mit Passwort herein (bei Debian und Ubuntu üblich), nimm ein Konto, das sudo darf."
          error={errors.login_user}
        >
          {(p) => (
            <input
              {...p} className={inputClass} value={loginUser} onChange={(e) => setLoginUser(e.target.value)}
              autoCapitalize="none" autoCorrect="off" spellCheck={false} autoComplete="off" placeholder="root"
            />
          )}
        </FormField>
        <FormField label="Passwort dieses Kontos" hint="Nur für diese eine Anmeldung." error={errors.password}>
          {(p) => (
            <input {...p} type="password" className={inputClass} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
          )}
        </FormField>
      </div>
      {!asRoot && (
        <label className="flex cursor-pointer items-start gap-2.5 text-sm">
          <input type="checkbox" className={CHECKBOX} checked={samePassword} onChange={(e) => setSamePassword(e.target.checked)} />
          <span>
            Für sudo dasselbe Passwort nehmen <span className="text-white/50">(sonst muss das Konto sudo ohne Passwort dürfen)</span>
          </span>
        </label>
      )}
      <div>
        <Button type="submit" variant="primary" busy={busy} disabled={!password || !ready}>
          {busy ? "Richte ein … (bis zu zwei Minuten)" : "Einrichten"}
        </Button>
      </div>
      <NoticeLine notice={notice} />
      {failure && <Failure failure={failure} confirming={confirming} onConfirmKey={(key) => void confirmKey(key)} />}
      {result && (
        <div className="space-y-3" data-testid="password-setup-result">
          <p role="status" className={`text-sm ${result.ok ? "text-emerald-300" : "text-amber-200"}`}>
            {result.ok
              ? "Eingerichtet. Nodvard Deck meldet sich jetzt mit dem neuen Schlüssel an."
              : "Der Befehl ist gelaufen, aber die Prüfung zeigt noch etwas – siehe unten."}
            {result.made_default ? " Der Zugang ist jetzt der Standard." : ""}
          </p>
          <Messages lines={result.messages} />
          {result.check && <CheckResultList result={result.check} />}
          {extraNotes.map((n) => <p key={n} className="text-xs text-white/55">{n}</p>)}
        </div>
      )}
    </form>
  );
}
