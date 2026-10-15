/**
 * Tresor: alle gespeicherten Passwörter, Schlüssel und Tokens mit ihren Verwendern und dem Ablaufdatum. Werte zeigt die
 * Seite nie (der Server gibt sie nicht heraus). Ersetzen und Löschen verlangen das eigene Passwort und, wenn
 * Zwei-Faktor an ist, den Code aus der App; gelöscht wird nur, was nicht mehr fest gebunden ist (`bound`).
 */
import { KeyRound, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { ApiError, api } from "../../lib/api";
import { useAuthStore } from "../../state/auth";
import { SecondFactorField, isTotpMissing, isTotpRejected, useTwoFactorEnabled } from "./SecondFactorField";
import { Badge, Button, Card, Field, NoticeLine, PageHeader, errorText, inputClass, type Notice } from "./ui";

export interface SecretUsage {
  type: string;
  id: string;
  name: string;
  last_used_at: string | null;
}

export interface VaultEntry {
  id: string;
  label: string;
  kind: string;
  description: string;
  created_at: string;
  rotated_at: string | null;
  last_used_at: string | null;
  expires_at: string | null;
  /** Alle Verwender, auch wer den Eintrag nur einmal gelesen hat (ein Hinweis). */
  used_by: SecretUsage[];
  /** Nur die festen Bindungen (Modul, Server, Anmeldung): Nur sie sperren das Löschen. */
  bound: SecretUsage[];
}

const KIND_LABELS: Record<string, string> = {
  password: "Passwort",
  ssh_private_key: "SSH-Schlüssel",
  api_token: "API-Token",
  totp: "Zwei-Faktor",
  generic: "Sonstiges",
};

const USAGE_LABELS: Record<string, string> = { extension: "Modul", host: "Server", login: "Anmeldung" };

const DAY_MS = 24 * 60 * 60 * 1000;
const SOON_DAYS = 14;

function formatDate(iso: string | null): string {
  if (!iso) return "–";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "–" : d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });
}

/** Ablauf als Badge: abgelaufen, bald (14 Tage) oder nur das Datum. */
function ExpiryBadge({ iso, now }: { iso: string | null; now: number }) {
  if (!iso) return null;
  const left = Math.ceil((new Date(iso).getTime() - now) / DAY_MS);
  if (Number.isNaN(left)) return null;
  if (left <= 0) return <Badge tone="bad">abgelaufen am {formatDate(iso)}</Badge>;
  if (left <= SOON_DAYS) return <Badge tone="warn">läuft in {left} {left === 1 ? "Tag" : "Tagen"} ab</Badge>;
  return <Badge>gültig bis {formatDate(iso)}</Badge>;
}

/** Ein Datum aus `<input type="date">` als Mittag in UTC, damit es in keiner Zeitzone auf den Vortag rutscht. */
export function dateToIso(date: string): string | null {
  return date ? `${date}T12:00:00Z` : null;
}

function isoToDate(iso: string | null): string {
  return iso ? iso.slice(0, 10) : "";
}

function isLogin(entry: VaultEntry): boolean {
  return entry.kind === "totp" || entry.used_by.some((u) => u.type === "login");
}

type Pending = { id: string; kind: "replace" | "delete" | "edit" } | null;

function NewEntry({ onDone }: { onDone: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState("");
  const [kind, setKind] = useState("password");
  const [value, setValue] = useState("");
  const [description, setDescription] = useState("");
  const [expires, setExpires] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setNotice(null);
    try {
      await api.post("/secrets", { label: label.trim(), kind, value, description: description.trim(), expires_at: dateToIso(expires) });
      setLabel(""); setValue(""); setDescription(""); setExpires(""); setOpen(false);
      await onDone();
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return <Button variant="primary" onClick={() => setOpen(true)}><Plus size={14} /> Neuer Eintrag</Button>;
  }
  return (
    <Card title="Neuer Eintrag" description="Zum Beispiel ein Token, das ein Skript oder ein Modul braucht. Der Wert lässt sich danach nicht mehr ansehen, nur ersetzen.">
      <form data-testid="vault-new" className="grid gap-3 sm:grid-cols-2" onSubmit={(e) => void submit(e)}>
        <Field label="Name" hint="Eindeutig, z. B. backup-token.">
          <input value={label} onChange={(e) => setLabel(e.target.value)} maxLength={128} className={inputClass} required autoFocus />
        </Field>
        <Field label="Art">
          <select value={kind} onChange={(e) => setKind(e.target.value)} className={inputClass}>
            {Object.entries(KIND_LABELS).filter(([k]) => k !== "totp").map(([k, text]) => <option key={k} value={k}>{text}</option>)}
          </select>
        </Field>
        <Field label="Wert" className="sm:col-span-2">
          {kind === "ssh_private_key" ? (
            <textarea value={value} onChange={(e) => setValue(e.target.value)} rows={4} spellCheck={false} className={`${inputClass} font-mono`} required />
          ) : (
            <input type="password" autoComplete="new-password" value={value} onChange={(e) => setValue(e.target.value)} className={inputClass} required />
          )}
        </Field>
        <Field label="Beschreibung (optional)">
          <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={255} className={inputClass} />
        </Field>
        <Field label="Läuft ab am (optional)" hint="14 und 3 Tage vorher kommt eine Erinnerung.">
          <input type="date" value={expires} onChange={(e) => setExpires(e.target.value)} className={inputClass} />
        </Field>
        <div className="flex flex-wrap items-center justify-end gap-2 sm:col-span-2">
          <NoticeLine notice={notice} />
          <Button variant="ghost" onClick={() => setOpen(false)}>Abbrechen</Button>
          <Button type="submit" variant="primary" busy={busy} disabled={!label.trim() || !value}>Speichern</Button>
        </div>
      </form>
    </Card>
  );
}

function EntryRow({ entry, canWrite, twoFactor, now, onChanged }: {
  entry: VaultEntry; canWrite: boolean; twoFactor: boolean; now: number; onChanged: (notice: Notice) => Promise<void>;
}) {
  const [pending, setPending] = useState<Pending>(null);
  const [value, setValue] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [askedForCode, setAskedForCode] = useState(false);
  const [description, setDescription] = useState(entry.description);
  const [expires, setExpires] = useState(isoToDate(entry.expires_at));
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const login = isLogin(entry);
  const inUse = entry.bound.length > 0;
  const isBound = (u: SecretUsage) => entry.bound.some((b) => b.type === u.type && b.id === u.id);
  const needsCode = twoFactor || askedForCode;

  function open(kind: NonNullable<Pending>["kind"]) {
    setPending({ id: entry.id, kind });
    setValue(""); setPassword(""); setCode(""); setNotice(null);
    setDescription(entry.description); setExpires(isoToDate(entry.expires_at));
  }

  async function confirm(event: FormEvent) {
    event.preventDefault();
    if (!pending) return;
    const current = password;
    const totp = code.trim();
    const confirmation = totp ? { current_password: current, totp_code: totp } : { current_password: current };
    setBusy(true);
    setNotice(null);
    try {
      const url = `/secrets/${encodeURIComponent(entry.id)}`;
      if (pending.kind === "replace") {
        await api.put(`${url}/value`, { value, ...confirmation });
        await onChanged({ kind: "ok", text: `„${entry.label}“ ersetzt.` });
      } else if (pending.kind === "delete") {
        await api.delete(url, confirmation);
        await onChanged({ kind: "ok", text: `„${entry.label}“ gelöscht.` });
      } else {
        await api.patch(url, { description: description.trim(), expires_at: dateToIso(expires) });
        await onChanged({ kind: "ok", text: `„${entry.label}“ gespeichert.` });
      }
      setPending(null);
      setValue(""); setPassword(""); setCode("");
    } catch (err) {
      if (isTotpMissing(err) || isTotpRejected(err)) {
        // Das Passwort stimmte; es fehlt nur ein (gültiger) Code aus der App.
        setAskedForCode(true);
        setCode("");
      } else {
        setPassword("");
      }
      if (err instanceof ApiError && err.status === 409 && pending.kind === "delete") {
        await onChanged(null);
      }
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  const codeMissing = pending?.kind !== "edit" && needsCode && !code.trim();
  const ready = pending?.kind === "edit" || (password && !codeMissing && (pending?.kind !== "replace" || value));

  return (
    <li data-testid={`vault-${entry.label}`} className="px-4 py-3">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-1">
        <div className="min-w-0 basis-full sm:basis-0 sm:flex-1">
          <p className="break-all text-sm font-medium">{entry.label}</p>
          <p className="text-xs text-white/50">
            {KIND_LABELS[entry.kind] ?? entry.kind}
            {entry.description && <> · {entry.description}</>}
            {" · "}zuletzt benutzt {entry.last_used_at ? formatDate(entry.last_used_at) : "nie festgehalten"}
          </p>
          <p className="mt-1 flex flex-wrap gap-1.5 text-xs">
            {entry.used_by.length === 0 ? (
              <span className="text-white/40">Nichts benutzt diesen Eintrag.</span>
            ) : (
              entry.used_by.map((u) => (
                <span key={`${u.type}:${u.id}`} title={isBound(u) ? undefined : "Hat den Eintrag gelesen. Das ist nur ein Hinweis, Löschen geht trotzdem."}>
                  <Badge tone={isBound(u) ? "accent" : "neutral"}>
                    {USAGE_LABELS[u.type] ?? u.type}: {u.name}{isBound(u) ? "" : " (gelesen)"}
                  </Badge>
                </span>
              ))
            )}
          </p>
        </div>
        <ExpiryBadge iso={entry.expires_at} now={now} />
      </div>
      {canWrite && login && (
        <p className="mt-2 text-xs text-white/45">Gehört zur Zwei-Faktor-Anmeldung eines Kontos. Abschalten geht unter „Mein Konto“ oder „Benutzer“.</p>
      )}
      {canWrite && !login && !pending && (
        <div className="mt-2 flex flex-wrap gap-2">
          <Button variant="ghost" onClick={() => open("replace")}><RefreshCw size={14} /> Ersetzen</Button>
          <Button variant="ghost" onClick={() => open("edit")}><Pencil size={14} /> Beschreibung und Ablauf</Button>
          <Button variant="ghost" onClick={() => open("delete")} disabled={inUse}
            title={inUse ? "Gehört fest zu einem Modul oder Server: erst dort austauschen oder entfernen." : undefined}>
            <Trash2 size={14} /> Löschen
          </Button>
        </div>
      )}
      <NoticeLine notice={notice} />
      {pending && (
        <form data-testid="vault-confirm" className="mt-3 flex flex-wrap items-end gap-2 rounded-lg bg-black/20 p-3" onSubmit={(e) => void confirm(e)}>
          {pending.kind === "replace" && (
            <Field label="Neuer Wert" className="min-w-0 basis-full">
              {entry.kind === "ssh_private_key" ? (
                <textarea value={value} onChange={(e) => setValue(e.target.value)} rows={4} spellCheck={false} className={`${inputClass} font-mono`} autoFocus />
              ) : (
                <input type="password" autoComplete="new-password" value={value} onChange={(e) => setValue(e.target.value)} className={inputClass} autoFocus />
              )}
            </Field>
          )}
          {pending.kind === "edit" ? (
            <>
              <Field label="Beschreibung" className="min-w-0 basis-full sm:flex-1 sm:basis-auto">
                <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={255} className={inputClass} autoFocus />
              </Field>
              <Field label="Läuft ab am" className="min-w-0 basis-full sm:basis-auto">
                <input type="date" value={expires} onChange={(e) => setExpires(e.target.value)} className={inputClass} />
              </Field>
            </>
          ) : (
            <>
              <Field label={pending.kind === "delete" ? "Wirklich löschen? Anmeldepasswort zur Bestätigung" : "Anmeldepasswort zur Bestätigung"} className="min-w-0 basis-full sm:flex-1 sm:basis-auto">
                <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)}
                  aria-label="Anmeldepasswort zur Bestätigung" className={inputClass} autoFocus={pending.kind === "delete"} />
              </Field>
              {needsCode && <SecondFactorField value={code} onChange={setCode} allowRecovery={false} className="min-w-0 basis-full sm:flex-1 sm:basis-auto" />}
            </>
          )}
          <Button variant="ghost" onClick={() => setPending(null)}>Abbrechen</Button>
          <Button type="submit" variant={pending.kind === "delete" ? "danger" : "primary"} disabled={!ready} busy={busy}>
            {pending.kind === "delete" ? "Löschen" : pending.kind === "replace" ? "Ersetzen" : "Speichern"}
          </Button>
        </form>
      )}
    </li>
  );
}

export function VaultSettings(): JSX.Element {
  const canWrite = useAuthStore((s) => s.hasPermission("secrets.write"));
  const twoFactor = useTwoFactorEnabled(canWrite) === true;
  const [entries, setEntries] = useState<VaultEntry[] | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [now] = useState(() => Date.now());

  const load = useCallback(async () => {
    try {
      setEntries(await api.get<VaultEntry[]>("/secrets"));
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function changed(next: Notice) {
    if (next) setNotice(next);
    await load();
  }

  return (
    <div>
      <PageHeader
        title="Tresor"
        description="Passwörter, Schlüssel und Tokens, die Nodvard Deck verschlüsselt aufbewahrt. Die Werte zeigt niemand an, auch nicht für dich."
        actions={canWrite ? <NewEntry onDone={() => changed({ kind: "ok", text: "Eintrag gespeichert." })} /> : undefined}
      />
      <NoticeLine notice={notice} />
      <Card title="Einträge" description="Ein Eintrag, der fest zu einem Modul oder Server gehört, lässt sich erst dort austauschen oder entfernen und danach hier löschen. Hat ein Modul ihn nur gelesen, geht Löschen gleich.">
        {entries === null ? (
          <p className="text-sm text-white/50">Lade …</p>
        ) : entries.length === 0 ? (
          <p className="text-sm text-white/50"><KeyRound size={14} className="mr-1 inline" />Noch nichts im Tresor.</p>
        ) : (
          <ul data-testid="vault-list" className="-mx-5 -my-4 divide-y divide-white/[0.06]">
            {entries.map((e) => (
              <EntryRow key={e.id} entry={e} canWrite={canWrite} twoFactor={twoFactor} now={now} onChanged={changed} />
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
