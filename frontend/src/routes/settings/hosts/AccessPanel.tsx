/**
 * SSH-Zugang eines Servers: anlegen (Schluessel erzeugen, Passwort, eigenen Schluessel
 * einfuegen), den Einrichtungsbefehl zeigen, einen Zugang ersetzen und loeschen.
 *
 * Geheimnisse: Was jemand hier eintippt oder einfuegt (Passwort, privater Schluessel), wird
 * im selben Augenblick aus dem Zustand der Seite geloescht, in dem es abgeschickt wird -- also noch
 * vor der Antwort -- und laeuft nie durch `useMutation` oder einen Query-Key. Der erzeugte
 * Schluessel verlaesst den Tresor nie; die Oberflaeche sieht nur seinen oeffentlichen Teil.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";

import { api, ApiError } from "../../../lib/api";
import {
  CREDENTIAL_KIND_LABEL, fieldErrors, formatDate, isSshCredential, refreshHosts,
  type ConnectionCheck as CheckResult, type CredentialOut, type GeneratedKeyOut, type HostOut, type MakeDefaultOut,
} from "../../../lib/hosts";
import { confirmDialog } from "../../../state/dialogs";
import { Badge, Button, NoticeLine, inputClass, type Notice } from "../ui";
import { ConnectionCheck } from "./ConnectionCheck";
import { FormField } from "./FormField";
import { SetupCommand } from "./SetupCommand";

type Mode = "generate" | "password" | "paste";

const MODE_LABEL: Record<Mode, string> = {
  generate: "SSH-Schlüssel erzeugen",
  password: "Passwort eingeben",
  paste: "Eigenen Schlüssel einfügen",
};

function describeCredential(c: CredentialOut): string {
  const how = c.kind === "ssh_key" ? "Anmeldung mit Schlüssel" : "Anmeldung mit Passwort";
  const since = formatDate(c.created_at);
  return `${how} als ${c.username}, Port ${c.port}${since ? ` – seit ${since}` : ""}`;
}

/** Formular zum Anlegen eines Zugangs in einer der drei Arten. */
function NewAccessForm({
  host, mode, hasDefault, onCreated, onCancel,
}: {
  host: HostOut;
  mode: Mode;
  /** Gibt es schon einen Standard-Zugang? Dann wird der neue NICHT zum Standard. */
  hasDefault: boolean;
  onCreated: (credential: CredentialOut, notice: string) => void;
  onCancel: () => void;
}) {
  // "lattice" bleibt der vorgeschlagene Benutzername: so heisst er auf den Servern bestehender Installationen.
  const [username, setUsername] = useState(mode === "generate" ? "lattice" : "");
  const [port, setPort] = useState("22");
  // Die Eingabe des Geheimnisses: Passwort oder privater Schluessel.
  const [secret, setSecret] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<Notice>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErrors({});
    setNotice(null);
    const portNumber = Number(port);
    // Das Geheimnis sofort aus dem Zustand nehmen -- es lebt nur noch in dieser einen Anfrage.
    const typed = secret;
    setSecret("");
    setBusy(true);
    try {
      if (mode === "generate") {
        const out = await api.post<GeneratedKeyOut>(`/hosts/${host.id}/credentials/generate-key`, { username: username.trim(), port: portNumber });
        onCreated(out.credential, "SSH-Schlüssel erzeugt. Führe jetzt den Befehl unten auf dem Server aus.");
      } else {
        const out = await api.post<CredentialOut>(`/hosts/${host.id}/credentials`, {
          kind: mode === "password" ? "ssh_password" : "ssh_key",
          username: username.trim(),
          port: portNumber,
          secret_value: typed,
          is_default: !hasDefault,
        });
        onCreated(
          out,
          mode === "password"
            ? "Zugang gespeichert. Prüfe jetzt die Verbindung."
            : "Schlüssel gespeichert. Damit sich Nodvard Deck anmelden kann, muss der zugehörige öffentliche Schlüssel auf dem Server eingetragen sein – der Befehl unten macht das.",
        );
      }
    } catch (err) {
      const perField = fieldErrors(err);
      setErrors(perField);
      if (Object.keys(perField).length === 0) setNotice({ kind: "error", text: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} noValidate className="mt-4 space-y-4 rounded-lg border border-white/[0.08] bg-black/10 p-4">
      <h4 className="text-sm font-semibold">{MODE_LABEL[mode]}</h4>
      <NoticeLine notice={notice} />
      <div className="grid gap-4 sm:grid-cols-[1fr_8rem]">
        <FormField
          label={mode === "generate" ? "Benutzer auf dem Server" : "Benutzer"}
          hint={
            mode === "generate"
              ? "Wird angelegt, falls es ihn noch nicht gibt. Bei Proxmox-Knoten „root“ nehmen."
              : "Ein Benutzer, der auf dem Server schon existiert."
          }
          error={errors.username}
        >
          {(p) => <input {...p} className={inputClass} value={username} onChange={(e) => setUsername(e.target.value)} autoCapitalize="none" autoCorrect="off" spellCheck={false} placeholder={mode === "generate" ? "lattice" : "root"} />}
        </FormField>
        <FormField label="SSH-Port" error={errors.port}>
          {(p) => <input {...p} className={inputClass} value={port} onChange={(e) => setPort(e.target.value)} inputMode="numeric" />}
        </FormField>
      </div>
      {mode === "password" && (
        <FormField label="Passwort" hint="Wird verschlüsselt gespeichert und nie wieder angezeigt." error={errors.secret_value}>
          {(p) => <input {...p} type="password" className={inputClass} value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="new-password" />}
        </FormField>
      )}
      {mode === "paste" && (
        <FormField
          label="Privater Schlüssel"
          hint="Der ganze Text, der mit -----BEGIN … beginnt. Ohne Passphrase. Wird verschlüsselt gespeichert und nie wieder angezeigt."
          error={errors.secret_value}
        >
          {(p) => (
            <textarea
              {...p} rows={6} value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="off" autoCapitalize="none"
              autoCorrect="off" spellCheck={false} placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
              className={`${inputClass} font-mono text-xs`}
            />
          )}
        </FormField>
      )}
      {hasDefault && (
        <p className="text-xs text-white/50">Der bisherige Zugang bleibt Standard, bis du den neuen geprüft und bewusst umgestellt hast.</p>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="ghost" onClick={onCancel}>Abbrechen</Button>
        <Button type="submit" variant="primary" busy={busy}>
          {mode === "generate" ? "Schlüssel erzeugen" : "Speichern"}
        </Button>
      </div>
    </form>
  );
}

/** Die Wahl der drei Wege, einen Zugang anzulegen. */
function ModeButtons({ onPick }: { onPick: (mode: Mode) => void }) {
  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
      {(["generate", "password", "paste"] as Mode[]).map((m) => (
        <Button key={m} variant={m === "generate" ? "primary" : "secondary"} onClick={() => onPick(m)}>
          {MODE_LABEL[m]}
        </Button>
      ))}
    </div>
  );
}

function CredentialRow({
  host, credential, expanded, verified, onToggleSetup, onChecked, onChanged, onNotice,
}: {
  host: HostOut;
  credential: CredentialOut;
  expanded: boolean;
  verified: boolean;
  onToggleSetup: () => void;
  onChecked: (result: CheckResult) => void;
  onChanged: () => Promise<void>;
  onNotice: (n: Notice) => void;
}) {
  const [busy, setBusy] = useState<"delete" | "default" | null>(null);

  async function remove() {
    const ok = await confirmDialog(
      `SSH-Zugang für „${host.display_name}“ löschen? Terminal, Updates und Überwachung erreichen den Server dann nicht mehr. Auf dem Server bleibt der Schlüssel eingetragen – bei Bedarf dort aus ~/.ssh/authorized_keys entfernen.`,
      { danger: true, confirmLabel: "Löschen" },
    );
    if (!ok) return;
    setBusy("delete");
    onNotice(null);
    try {
      await api.delete(`/hosts/${host.id}/credentials/${credential.id}`);
      await onChanged();
      onNotice({ kind: "ok", text: "SSH-Zugang gelöscht." });
    } catch (err) {
      onNotice({ kind: "error", text: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setBusy(null);
    }
  }

  async function makeDefault() {
    const ok = await confirmDialog(
      "Den neuen Zugang verwenden? Der bisherige Zugang wird in Nodvard Deck gelöscht. Sein Schlüssel bleibt auf dem Server eingetragen – dort bei Bedarf aus ~/.ssh/authorized_keys entfernen.",
      { confirmLabel: "Neuen Zugang verwenden" },
    );
    if (!ok) return;
    setBusy("default");
    onNotice(null);
    try {
      const out = await api.post<MakeDefaultOut>(`/hosts/${host.id}/credentials/${credential.id}/make-default`, { delete_previous: true });
      await onChanged();
      onNotice({ kind: "ok", text: out.notice ?? "Der neue Zugang ist jetzt der Standard." });
    } catch (err) {
      // 409: die Pruefung ist zu alt oder fehlt -- der Text des Servers sagt, was zu tun ist.
      onNotice({ kind: "error", text: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <li className="px-3 py-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="break-words text-sm font-medium">{describeCredential(credential)}</p>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {credential.is_default ? <Badge tone="good">Standard</Badge> : <Badge tone="accent">Neuer Zugang – noch nicht in Benutzung</Badge>}
            <Badge>{CREDENTIAL_KIND_LABEL[credential.kind] ?? credential.kind}</Badge>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {credential.kind === "ssh_key" && host.os_family === "linux" && (
            <Button onClick={onToggleSetup}>{expanded ? "Einrichtungsbefehl verbergen" : "Einrichtungsbefehl zeigen"}</Button>
          )}
          <Button variant="danger" busy={busy === "delete"} onClick={() => void remove()}>Zugang löschen</Button>
        </div>
      </div>

      {expanded && credential.kind === "ssh_key" && (
        <div className="mt-4 rounded-lg border border-white/[0.08] bg-black/10 p-4"><SetupCommand host={host} credential={credential} /></div>
      )}

      {!credential.is_default && (
        <div className="mt-4 rounded-lg border border-white/[0.08] bg-black/10 p-4">
          <p className="mb-3 text-sm text-white/70">Erst prüfen, ob die Anmeldung mit dem neuen Zugang klappt. Danach kannst du ihn verwenden.</p>
          <ConnectionCheck hostId={host.id} credentialId={credential.id} buttonLabel="Neuen Zugang prüfen" onResult={onChecked} />
          <div className="mt-4 border-t border-white/[0.06] pt-4">
            <Button variant="primary" disabled={!verified} busy={busy === "default"} onClick={() => void makeDefault()}>
              Neuen Zugang verwenden
            </Button>
            {!verified && <p className="mt-1.5 text-xs text-white/45">Wird frei, sobald die Prüfung mit diesem Zugang geklappt hat.</p>}
          </div>
        </div>
      )}
    </li>
  );
}

export function AccessPanel({
  host, credentials, autoOpen = false,
}: {
  host: HostOut;
  credentials: CredentialOut[];
  /** Nach „Server anlegen“: das Formular zum Erzeugen gleich aufklappen. */
  autoOpen?: boolean;
}) {
  const queryClient = useQueryClient();
  const ssh = credentials.filter(isSshCredential);
  const hasDefault = ssh.some((c) => c.is_default);
  const [mode, setMode] = useState<Mode | null>(null);
  const [adding, setAdding] = useState(false);
  const [setupFor, setSetupFor] = useState<string | null>(null);
  const [verified, setVerified] = useState<string[]>([]);
  const [notice, setNotice] = useState<Notice>(null);

  // Ohne jeden Zugang und frisch angelegt: gleich das Formular zum Erzeugen zeigen.
  useEffect(() => {
    if (autoOpen && ssh.length === 0) setMode((m) => m ?? "generate");
    // nur beim ersten Mal -- danach entscheidet die Person
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoOpen]);

  async function changed() {
    await refreshHosts(queryClient);
  }

  async function created(credential: CredentialOut, text: string) {
    setMode(null);
    setAdding(false);
    if (credential.kind === "ssh_key") setSetupFor(credential.id);
    setNotice({ kind: "ok", text });
    await changed();
  }

  const noAccess = ssh.length === 0;

  return (
    <div>
      <NoticeLine notice={notice} />

      {noAccess && (
        <>
          <p className="mb-4 text-sm text-white/65">
            Noch kein Zugang hinterlegt. Am einfachsten: Nodvard Deck erzeugt einen eigenen Schlüssel nur für diesen Server.
          </p>
          <ModeButtons onPick={setMode} />
        </>
      )}

      {!noAccess && (
        <ul className="divide-y divide-white/[0.06] rounded-lg border border-white/[0.08]">
          {ssh.map((c) => (
            <CredentialRow
              key={c.id} host={host} credential={c} expanded={setupFor === c.id} verified={verified.includes(c.id)}
              onToggleSetup={() => setSetupFor(setupFor === c.id ? null : c.id)}
              onChecked={(r) => setVerified((v) => {
                const ok = r.items.some((i) => i.id === "login" && i.status === "ok");
                return ok ? [...new Set([...v, c.id])] : v.filter((x) => x !== c.id);
              })}
              onChanged={changed}
              onNotice={setNotice}
            />
          ))}
        </ul>
      )}

      {!noAccess && !adding && (
        <div className="mt-4">
          <Button onClick={() => setAdding(true)}>Ersetzen</Button>
          <p className="mt-1.5 text-xs text-white/45">Legt einen neuen Zugang an. Der bisherige bleibt, bis der neue geprüft ist.</p>
        </div>
      )}
      {!noAccess && adding && mode === null && (
        <div className="mt-4">
          <p className="mb-2 text-sm text-white/65">Wie soll der neue Zugang aussehen?</p>
          <ModeButtons onPick={setMode} />
          <div className="mt-2"><Button variant="ghost" onClick={() => setAdding(false)}>Abbrechen</Button></div>
        </div>
      )}

      {mode !== null && (
        <NewAccessForm
          key={mode} host={host} mode={mode} hasDefault={hasDefault}
          onCreated={(c, text) => void created(c, text)}
          onCancel={() => { setMode(null); setAdding(false); }}
        />
      )}
    </div>
  );
}
