/**
 * Karte „Sicherung“ (Einstellungen, System): Sicherungspasswort, automatische Sicherungen,
 * Liste mit Prüfen/Herunterladen/Löschen, „Jetzt sichern“ und „Sicherung herunterladen“.
 * GET /system/backups (`system.read`); alles Ändernde nur für den Owner, Kritisches zusätzlich
 * mit dem Anmeldepasswort.
 *
 * Passwörter: nur in lokalem State, direkt per `api.*` verschickt und danach geleert --
 * nie in einem Query-Key oder Mutations-Cache (wie HostDetailSettings).
 */
import { AlertTriangle, CheckCircle2, Copy, Download, HardDrive, KeyRound, Loader2, ShieldCheck, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { SchedulePicker, describeSchedule } from "../../components/SchedulePicker";
import { api } from "../../lib/api";
import {
  browserDownload,
  formatSize,
  formatWhen,
  newPasswordProblem,
  protectedConfigChanges,
  recoveryKeyFile,
  waitForTicket,
  type BackupItem,
  type BackupOverview,
  type DownloadTicket,
  type KeyResult,
} from "../../lib/backups";
import { copyText } from "../../lib/clipboard";
import { useAuthStore } from "../../state/auth";
import { Badge, Button, Card, Field, NoticeLine, Toggle, errorText, inputClass, type Notice } from "./ui";

const RUNNING_POLL_MS = 2000;

export function BackupCard(): JSX.Element {
  const isOwner = useAuthStore((s) => Boolean(s.user?.is_owner));
  const [overview, setOverview] = useState<BackupOverview | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [busyRun, setBusyRun] = useState(false);

  const reload = useCallback(async () => {
    try {
      setOverview(await api.get<BackupOverview>("/system/backups"));
      setLoadError(null);
    } catch (err) {
      setLoadError(errorText(err));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Läuft gerade eine Sicherung, regelmäßig nachsehen, bis sie fertig ist.
  useEffect(() => {
    if (!overview?.running) return;
    const timer = setTimeout(() => void reload(), RUNNING_POLL_MS);
    return () => clearTimeout(timer);
  }, [overview, reload]);

  async function runNow() {
    setBusyRun(true);
    setNotice(null);
    try {
      await api.post("/system/backups/run");
      setNotice({ kind: "ok", text: "Sicherung gestartet. Das kann je nach Datenmenge etwas dauern." });
      await reload();
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusyRun(false);
    }
  }

  if (loadError && !overview) {
    return (
      <Card title="Sicherung">
        <NoticeLine notice={{ kind: "error", text: loadError }} />
      </Card>
    );
  }
  if (!overview) {
    return (
      <Card title="Sicherung">
        <p className="text-sm text-white/50">Lade …</p>
      </Card>
    );
  }

  const { last_run: lastRun, running } = overview;

  return (
    <Card
      title="Sicherung"
      description="Alle Daten von Nodvard Deck verschlüsselt sichern: Einstellungen, Server, Zugangsdaten und die Daten der Erweiterungen."
    >
      <NoticeLine notice={notice} />
      {loadError && <NoticeLine notice={{ kind: "error", text: loadError }} />}
      {!overview.sqlite && (
        <NoticeLine notice={{ kind: "error", text: "Sicherungen gibt es nur, wenn die Datenbank eine SQLite-Datei ist." }} />
      )}

      <div className="mb-5 flex flex-wrap items-center gap-3 rounded-lg border border-white/10 bg-black/20 px-4 py-3">
        <HardDrive size={18} className="flex-none text-white/50" />
        <div className="min-w-0 flex-1 text-sm">
          {running ? (
            <p className="flex items-center gap-2"><Loader2 size={14} className="animate-spin" /> Eine Sicherung läuft gerade …</p>
          ) : lastRun ? (
            <p>
              Letzte Sicherung: {formatWhen(lastRun.at)}{" "}
              {lastRun.ok ? <Badge tone="good">erfolgreich</Badge> : <Badge tone="bad">fehlgeschlagen</Badge>}
            </p>
          ) : (
            <p className="text-white/60">Noch keine automatische Sicherung.</p>
          )}
          {lastRun && !lastRun.ok && lastRun.error && <p className="mt-1 text-xs text-red-300">{lastRun.error}</p>}
          {lastRun?.warnings?.map((text) => (
            <p key={text} role="note" className="mt-1 flex gap-1.5 text-xs text-amber-200">
              <AlertTriangle size={13} className="mt-0.5 flex-none" /> {text}
            </p>
          ))}
          <p className="mt-1 text-xs text-white/45">
            {overview.config.enabled ? `Automatisch: ${describeSchedule(overview.config.schedule)}` : "Automatische Sicherung ist aus."}
          </p>
        </div>
        {isOwner && (
          <Button onClick={() => void runNow()} busy={busyRun} disabled={!overview.key || Boolean(running) || !overview.sqlite}
            title={overview.key ? undefined : "Zuerst ein Sicherungspasswort festlegen"}>
            Jetzt sichern
          </Button>
        )}
      </div>

      {!isOwner && (
        <p className="mb-4 text-sm text-white/55">Sicherungen einrichten, herunterladen oder löschen kann nur der Inhaber (Owner) dieser Installation.</p>
      )}

      <KeySection overview={overview} isOwner={isOwner} onChanged={reload} />
      {isOwner && <ScheduleSection overview={overview} onSaved={setOverview} />}
      <StorageHint overview={overview} />
      <BackupList overview={overview} isOwner={isOwner} onChanged={reload} />
      {isOwner && <DownloadSection overview={overview} />}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Sicherungspasswort
// ---------------------------------------------------------------------------

function KeySection({ overview, isOwner, onChanged }: { overview: BackupOverview; isOwner: boolean; onChanged: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [accountPassword, setAccountPassword] = useState("");
  const [understood, setUnderstood] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [result, setResult] = useState<KeyResult | null>(null);
  const min = overview.limits.password_min_length;
  const problem = newPasswordProblem(password, repeat, min);

  function clear() {
    setPassword("");
    setRepeat("");
    setAccountPassword("");
    setUnderstood(false);
  }

  async function submit() {
    setBusy(true);
    setNotice(null);
    try {
      const out = await api.put<KeyResult>("/system/backups/key", { current_password: accountPassword, password });
      clear();
      setOpen(false);
      setResult(out);
      await onChanged();
    } catch (err) {
      setAccountPassword("");
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mb-6" aria-labelledby="backup-key-title">
      <h4 id="backup-key-title" className="mb-2 flex items-center gap-2 text-sm font-semibold"><KeyRound size={15} /> Sicherungspasswort</h4>
      {overview.key ? (
        <p className="text-sm text-white/70">
          Eingerichtet am {formatWhen(overview.key.created_at)} <span className="text-white/40">(Kennung {overview.key.key_id.slice(0, 8)})</span>.
          Automatische Sicherungen werden damit verschlüsselt; Nodvard Deck selbst kennt das Passwort nicht.
        </p>
      ) : (
        <p className="text-sm text-white/70">
          Noch nicht festgelegt. Die Sicherungen werden mit diesem Passwort verschlüsselt. Nodvard Deck speichert es nicht – darum
          bleiben die Sicherungen auch dann sicher, wenn jemand sie in die Hände bekommt.
        </p>
      )}

      {result && <RecoveryKeyPanel result={result} onDone={() => setResult(null)} />}

      {isOwner && !open && !result && (
        <div className="mt-3">
          <Button onClick={() => { setOpen(true); setNotice(null); }}>{overview.key ? "Passwort ändern" : "Sicherungspasswort festlegen"}</Button>
        </div>
      )}

      {isOwner && open && (
        <form
          className="mt-3 space-y-3 rounded-lg border border-white/10 bg-black/20 p-4"
          onSubmit={(e) => { e.preventDefault(); if (!problem && understood && accountPassword) void submit(); }}
        >
          <div role="note" className="flex gap-2 rounded-lg border border-amber-400/40 bg-amber-400/10 px-3 py-2 text-sm text-amber-100">
            <AlertTriangle size={16} className="mt-0.5 flex-none" />
            <span>
              <strong>Passwort weg = Sicherungen wertlos.</strong> Ohne dieses Passwort oder den Wiederherstellungsschlüssel lässt sich
              keine Sicherung mehr öffnen – von niemandem. Bitte gut aufbewahren, z. B. im Passwortmanager.
            </span>
          </div>
          {overview.key && (
            <p className="text-xs text-white/55">
              Ältere Sicherungen bleiben mit dem bisherigen Passwort verschlüsselt. Bewahre es auf, solange du diese Sicherungen brauchst.
            </p>
          )}
          <NoticeLine notice={notice} />
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Neues Sicherungspasswort" hint={`Mindestens ${min} Zeichen.`}>
              <input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)}
                aria-label="Neues Sicherungspasswort" className={inputClass} />
            </Field>
            <Field label="Sicherungspasswort wiederholen">
              <input type="password" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)}
                aria-label="Sicherungspasswort wiederholen" className={inputClass} />
            </Field>
          </div>
          {password && problem && <p role="alert" className="text-xs text-red-300">{problem}</p>}
          <label className="flex items-start gap-2 text-sm text-white/75">
            <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} className="mt-1" />
            Ich habe verstanden: Ohne dieses Passwort sind meine Sicherungen nicht mehr zu öffnen.
          </label>
          <Field label="Dein Anmeldepasswort zur Bestätigung" className="sm:max-w-sm">
            <input type="password" autoComplete="current-password" value={accountPassword} onChange={(e) => setAccountPassword(e.target.value)}
              aria-label="Anmeldepasswort zur Bestätigung" className={inputClass} />
          </Field>
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" onClick={() => { clear(); setOpen(false); setNotice(null); }}>Abbrechen</Button>
            <Button type="submit" variant="primary" busy={busy} disabled={Boolean(problem) || !understood || !accountPassword}>
              Passwort festlegen
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}

function RecoveryKeyPanel({ result, onDone }: { result: KeyResult; onDone: () => void }) {
  const [notice, setNotice] = useState<Notice>(null);

  async function copy() {
    const ok = await copyText(result.recovery_key);
    setNotice(ok ? { kind: "ok", text: "Schlüssel in die Zwischenablage kopiert." } : { kind: "error", text: "Kopieren hat nicht geklappt – bitte als Datei speichern." });
  }

  function save() {
    const url = URL.createObjectURL(new Blob([recoveryKeyFile(result)], { type: "text/plain;charset=utf-8" }));
    browserDownload.start(url, `nodvard-deck-wiederherstellungsschluessel-${result.key_id.slice(0, 8)}.txt`);
    URL.revokeObjectURL(url);
  }

  return (
    <div className="mt-3 rounded-lg border border-amber-400/30 bg-amber-400/5 p-4" data-testid="recovery-key">
      <h5 className="text-sm font-semibold">Dein Wiederherstellungsschlüssel</h5>
      <p className="mt-1 text-sm text-white/70">
        Damit öffnest du deine Sicherungen auch ohne das Sicherungspasswort. <strong>Du siehst ihn nur jetzt</strong> – er wird nirgends
        gespeichert. Ausdrucken oder im Passwortmanager ablegen und niemandem geben.
      </p>
      <p className="mt-3 select-all break-all rounded-md bg-black/40 px-3 py-2 font-mono text-xs tracking-wide">{result.recovery_key}</p>
      <NoticeLine notice={notice} />
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button onClick={() => void copy()}><Copy size={14} /> Kopieren</Button>
        <Button onClick={save}><Download size={14} /> Als Datei speichern</Button>
        <span className="flex-1" />
        <Button variant="primary" onClick={onDone}>Ich habe ihn sicher aufbewahrt</Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Automatisch sichern
// ---------------------------------------------------------------------------

function ScheduleSection({ overview, onSaved }: { overview: BackupOverview; onSaved: (o: BackupOverview) => void }) {
  const cfg = overview.config;
  const [enabled, setEnabled] = useState(cfg.enabled);
  const [schedule, setSchedule] = useState(cfg.schedule);
  const [keep, setKeep] = useState(String(cfg.keep));
  const [dir, setDir] = useState(cfg.dir);
  const [includeRuns, setIncludeRuns] = useState(cfg.include_runs);
  const [accountPassword, setAccountPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const { keep_min: keepMin, keep_max: keepMax } = overview.limits;

  const keepNumber = /^\d+$/.test(keep.trim()) ? Number(keep.trim()) : NaN;
  const keepValid = keepNumber >= keepMin && keepNumber <= keepMax;
  const dirty = enabled !== cfg.enabled || schedule !== cfg.schedule || keepNumber !== cfg.keep || dir !== cfg.dir || includeRuns !== cfg.include_runs;
  // Weniger behalten, ausschalten oder anderer Ordner: nur mit Anmeldepasswort.
  const reasons = protectedConfigChanges(cfg, { enabled, keep: keepNumber, dir }, overview.target.default_dir);
  const needsPassword = reasons.length > 0;

  // Wird die Änderung zurückgenommen, soll kein eingetipptes Passwort im State liegen bleiben.
  useEffect(() => {
    if (!needsPassword) setAccountPassword("");
  }, [needsPassword]);

  async function save() {
    // Passwort nur kurz im lokalen State: direkt senden, danach leeren (nie in einem Query-Cache).
    const confirmation = needsPassword ? { current_password: accountPassword } : {};
    setAccountPassword("");
    setBusy(true);
    setNotice(null);
    try {
      const out = await api.put<BackupOverview>("/system/backups/config", {
        enabled, schedule, keep: keepNumber, dir: dir.trim() || null, include_runs: includeRuns, ...confirmation,
      });
      onSaved(out);
      setDir(out.config.dir);
      setNotice({ kind: "ok", text: "Gespeichert." });
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mb-6" aria-labelledby="backup-auto-title">
      <h4 id="backup-auto-title" className="mb-2 text-sm font-semibold">Automatisch sichern</h4>
      <NoticeLine notice={notice} />
      <div className="mb-3 flex items-center gap-3 text-sm">
        <Toggle checked={enabled} onChange={setEnabled} label="Automatisch sichern" disabled={!overview.key} />
        <span className={overview.key ? "" : "text-white/45"}>
          {overview.key ? "Regelmäßig eine Sicherung anlegen" : "Erst möglich, wenn ein Sicherungspasswort festgelegt ist"}
        </span>
      </div>
      {enabled && <SchedulePicker value={schedule} onChange={setSchedule} label="Zeitplan der Sicherung" />}
      <div className="mt-4 grid gap-4 sm:grid-cols-[10rem_minmax(0,1fr)]">
        <Field label="Anzahl behalten" hint={`${keepMin} bis ${keepMax}. Ältere werden gelöscht.`}>
          <input type="number" inputMode="numeric" min={keepMin} max={keepMax} value={keep} onChange={(e) => setKeep(e.target.value)}
            aria-label="Anzahl behalten" aria-invalid={!keepValid} className={inputClass} />
        </Field>
        <Field label="Ordner" hint={`Erlaubt: ${overview.target.default_dir} oder ein eingebundener Ordner unter ${overview.target.external_root}.`}>
          <input type="text" value={dir} onChange={(e) => setDir(e.target.value)} aria-label="Ordner für Sicherungen"
            spellCheck={false} className={`${inputClass} font-mono`} />
        </Field>
      </div>
      <div className="mt-2 flex flex-wrap gap-2 text-xs">
        <button type="button" className="rounded-md bg-white/[0.06] px-2 py-1 text-white/70 hover:bg-white/[0.12]" onClick={() => setDir(overview.target.default_dir)}>
          Im Datenordner
        </button>
        <button type="button" className="rounded-md bg-white/[0.06] px-2 py-1 text-white/70 hover:bg-white/[0.12] disabled:opacity-40"
          disabled={!overview.target.external_available} onClick={() => setDir(overview.target.external_root)}
          title={overview.target.external_available ? undefined : "Dieser Ordner ist nicht eingebunden."}>
          Eingebundener Ordner {overview.target.external_root}
        </button>
      </div>
      {!overview.target.external_available && (
        <p className="mt-2 text-xs text-white/50" data-testid="external-not-mounted">
          Der Ordner {overview.target.external_root} ist hier nicht eingebunden, darum ist der zweite Knopf gesperrt. Um Sicherungen auf einem anderen Laufwerk oder einem NAS abzulegen, muss dieser Ordner in
          der Compose-Datei von Nodvard Deck unter „volumes“ eingebunden werden. Bis dahin bleibt „Im Datenordner“.
        </p>
      )}
      {!keepValid && <p role="alert" className="mt-2 text-xs text-red-300">Bitte eine ganze Zahl zwischen {keepMin} und {keepMax} eingeben.</p>}
      <label className="mt-3 flex items-start gap-2 text-sm text-white/75">
        <input type="checkbox" checked={includeRuns} onChange={(e) => setIncludeRuns(e.target.checked)} className="mt-1" />
        Auch die Laufprotokolle der Zeitpläne mitsichern (größer, meist nicht nötig)
      </label>
      <form
        className="mt-3 space-y-3"
        onSubmit={(e) => { e.preventDefault(); if (dirty && keepValid && (!needsPassword || accountPassword)) void save(); }}
      >
        {needsPassword && (
          <div role="note" className="space-y-2 rounded-lg border border-amber-400/30 bg-amber-400/5 p-3 text-sm text-amber-100">
            <p className="flex gap-2">
              <AlertTriangle size={16} className="mt-0.5 flex-none" />
              <span>Gib zum Speichern bitte dein Anmeldepasswort ein, denn du willst {reasons.join(", ")}.</span>
            </p>
            <Field label="Dein Anmeldepasswort zur Bestätigung" className="sm:max-w-sm">
              <input type="password" autoComplete="current-password" value={accountPassword} onChange={(e) => setAccountPassword(e.target.value)}
                aria-label="Anmeldepasswort für die Einstellungen" className={inputClass} />
            </Field>
          </div>
        )}
        <div className="flex justify-end">
          <Button type="submit" variant="primary" busy={busy} disabled={!dirty || !keepValid || (needsPassword && !accountPassword)}>Speichern</Button>
        </div>
      </form>
    </section>
  );
}

function StorageHint({ overview }: { overview: BackupOverview }) {
  const { target } = overview;
  if (target.error) return <NoticeLine notice={{ kind: "error", text: target.error }} />;
  return (
    <div className="mb-5 space-y-2 text-sm">
      <p className="break-all text-white/60">
        Ziel: <span className="font-mono text-white/80">{target.dir}</span>
        {target.free_bytes != null && <span className="text-white/45"> · {formatSize(target.free_bytes)} frei</span>}
      </p>
      {target.same_storage_as_data && (
        <div role="note" className="flex gap-2 rounded-lg border border-amber-400/30 bg-amber-400/5 px-3 py-2 text-amber-100">
          <AlertTriangle size={16} className="mt-0.5 flex-none" />
          <span>
            Die Sicherungen liegen auf demselben Speicher wie die Daten. Das schützt vor Fehlbedienung, aber nicht vor einem Defekt der
            Speicherkarte oder Festplatte. Besser: einen Ordner auf einem anderen Laufwerk oder NAS als {target.external_root} einbinden
            oder die Sicherung regelmäßig herunterladen.
          </span>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Liste
// ---------------------------------------------------------------------------

function statusBadge(item: BackupItem) {
  if (item.status === "beschaedigt") return <Badge tone="bad">beschädigt</Badge>;
  if (item.status === "ungeprueft") return <Badge tone="warn">ohne Prüfsumme</Badge>;
  return <Badge tone="good">in Ordnung</Badge>;
}

type Pending = { name: string; kind: "download" | "delete" } | null;

function BackupList({ overview, isOwner, onChanged }: { overview: BackupOverview; isOwner: boolean; onChanged: () => Promise<void> }) {
  const [pending, setPending] = useState<Pending>(null);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);

  async function verify(name: string) {
    setBusy(`verify:${name}`);
    setNotice(null);
    try {
      const out = await api.post<{ ok: boolean; detail: string }>(`/system/backups/${encodeURIComponent(name)}/verify`);
      setNotice({ kind: out.ok ? "ok" : "error", text: out.detail });
      await onChanged();
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(null);
    }
  }

  async function confirm() {
    if (!pending) return;
    const { name, kind } = pending;
    const current = password;
    setPassword("");
    setBusy(`${kind}:${name}`);
    setNotice(null);
    try {
      if (kind === "download") {
        const ticket = await api.post<DownloadTicket>(`/system/backups/${encodeURIComponent(name)}/ticket`, { current_password: current });
        browserDownload.start(ticket.url, ticket.filename);
        setNotice({ kind: "ok", text: "Download gestartet." });
      } else {
        await api.delete(`/system/backups/${encodeURIComponent(name)}`, { current_password: current });
        setNotice({ kind: "ok", text: "Sicherung gelöscht." });
        await onChanged();
      }
      setPending(null);
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="mb-6" aria-labelledby="backup-list-title">
      <h4 id="backup-list-title" className="mb-2 text-sm font-semibold">Vorhandene Sicherungen</h4>
      <NoticeLine notice={notice} />
      {overview.backups.length === 0 ? (
        <p className="text-sm text-white/50">Noch keine Sicherung im Zielordner.</p>
      ) : (
        <ul className="divide-y divide-white/[0.06] rounded-lg border border-white/10" aria-label="Sicherungen">
          {overview.backups.map((item) => (
            <li key={item.name} className="px-3 py-3">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="min-w-0 basis-full text-sm sm:basis-0 sm:flex-1">
                  <span className="block font-medium">{formatWhen(item.created_at)}</span>
                  <span className="block break-all text-xs text-white/45">{item.name}</span>
                </span>
                <span className="flex flex-wrap items-center gap-1.5 text-xs text-white/55">
                  {formatSize(item.size)}
                  {item.app_version && <span>· Version {item.app_version}</span>}
                  {statusBadge(item)}
                  {item.mode === "schluessel" && !item.key_current && <Badge tone="warn">älteres Passwort</Badge>}
                </span>
              </div>
              {isOwner && (
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button variant="ghost" busy={busy === `verify:${item.name}`} onClick={() => void verify(item.name)}>
                    <ShieldCheck size={14} /> Prüfen
                  </Button>
                  <Button variant="ghost" onClick={() => { setPending({ name: item.name, kind: "download" }); setPassword(""); }}>
                    <Download size={14} /> Herunterladen
                  </Button>
                  <Button variant="ghost" onClick={() => { setPending({ name: item.name, kind: "delete" }); setPassword(""); }}>
                    <Trash2 size={14} /> Löschen
                  </Button>
                </div>
              )}
              {pending?.name === item.name && (
                <form className="mt-3 flex flex-wrap items-end gap-2 rounded-lg bg-black/20 p-3"
                  onSubmit={(e) => { e.preventDefault(); if (password) void confirm(); }}>
                  <Field label={pending.kind === "delete" ? "Wirklich löschen? Anmeldepasswort zur Bestätigung" : "Anmeldepasswort zur Bestätigung"} className="min-w-0 basis-full sm:flex-1 sm:basis-auto">
                    <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)}
                      aria-label="Anmeldepasswort zur Bestätigung" className={inputClass} autoFocus />
                  </Field>
                  <Button variant="ghost" onClick={() => { setPending(null); setPassword(""); }}>Abbrechen</Button>
                  <Button type="submit" variant={pending.kind === "delete" ? "danger" : "primary"} disabled={!password}
                    busy={busy === `${pending.kind}:${item.name}`}>
                    {pending.kind === "delete" ? "Löschen" : "Herunterladen"}
                  </Button>
                </form>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Sicherung herunterladen (jetzt erstellt)
// ---------------------------------------------------------------------------

function DownloadSection({ overview }: { overview: BackupOverview }) {
  const [mode, setMode] = useState<"schluessel" | "passwort">(overview.key ? "schluessel" : "passwort");
  const [oneTime, setOneTime] = useState("");
  const [repeat, setRepeat] = useState("");
  const [accountPassword, setAccountPassword] = useState("");
  const [phase, setPhase] = useState<"idle" | "building" | "done">("idle");
  const [notice, setNotice] = useState<Notice>(null);
  const abort = useRef<AbortController | null>(null);
  const min = overview.limits.password_min_length;
  const problem = mode === "passwort" ? newPasswordProblem(oneTime, repeat, min) : null;

  useEffect(() => () => abort.current?.abort(), []);

  async function start() {
    const body = { current_password: accountPassword, mode, password: mode === "passwort" ? oneTime : null };
    setOneTime("");
    setRepeat("");
    setAccountPassword("");
    setNotice(null);
    setPhase("building");
    abort.current = new AbortController();
    try {
      const ticket = await api.post<DownloadTicket>("/system/backups/download", body);
      const ready = await waitForTicket(ticket, { signal: abort.current.signal });
      browserDownload.start(ready.url, ready.filename);
      setPhase("done");
      setNotice({ kind: "ok", text: `Download gestartet (${formatSize(ready.size)}). Der Link gilt nur einmal.` });
    } catch (err) {
      setPhase("idle");
      setNotice({ kind: "error", text: errorText(err) });
    }
  }

  return (
    <section aria-labelledby="backup-download-title">
      <h4 id="backup-download-title" className="mb-2 text-sm font-semibold">Sicherung herunterladen</h4>
      <p className="mb-3 text-sm text-white/60">Erstellt jetzt eine frische, verschlüsselte Sicherung und lädt sie auf dieses Gerät.</p>
      <NoticeLine notice={notice} />
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); if (!problem && accountPassword && phase !== "building") void start(); }}>
        <fieldset className="space-y-2">
          <legend className="sr-only">Verschlüsselung</legend>
          <label className={`flex items-start gap-2 text-sm ${overview.key ? "text-white/80" : "text-white/40"}`}>
            <input type="radio" name="backup-mode" checked={mode === "schluessel"} disabled={!overview.key} onChange={() => setMode("schluessel")} className="mt-1" />
            <span>Mit meinem Sicherungspasswort <span className="block text-xs text-white/45">Wie die automatischen Sicherungen.{overview.key ? "" : " Erst nach dem Festlegen möglich."}</span></span>
          </label>
          <label className="flex items-start gap-2 text-sm text-white/80">
            <input type="radio" name="backup-mode" checked={mode === "passwort"} onChange={() => setMode("passwort")} className="mt-1" />
            <span>Mit einem eigenen Einmal-Passwort <span className="block text-xs text-white/45">Gilt nur für diese eine Datei.</span></span>
          </label>
        </fieldset>
        {mode === "passwort" && (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Einmal-Passwort" hint={`Mindestens ${min} Zeichen. Ohne dieses Passwort ist die Datei wertlos.`}>
              <input type="password" autoComplete="new-password" value={oneTime} onChange={(e) => setOneTime(e.target.value)}
                aria-label="Einmal-Passwort" className={inputClass} />
            </Field>
            <Field label="Einmal-Passwort wiederholen">
              <input type="password" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)}
                aria-label="Einmal-Passwort wiederholen" className={inputClass} />
            </Field>
            {oneTime && problem && <p role="alert" className="text-xs text-red-300 sm:col-span-2">{problem}</p>}
          </div>
        )}
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Dein Anmeldepasswort zur Bestätigung" className="min-w-0 basis-full sm:max-w-sm sm:flex-1 sm:basis-auto">
            <input type="password" autoComplete="current-password" value={accountPassword} onChange={(e) => setAccountPassword(e.target.value)}
              aria-label="Anmeldepasswort für den Download" className={inputClass} />
          </Field>
          <Button type="submit" variant="primary" busy={phase === "building"} disabled={Boolean(problem) || !accountPassword || !overview.sqlite}>
            {phase === "building" ? "Wird erstellt …" : <><Download size={14} /> Sicherung herunterladen</>}
          </Button>
        </div>
        {phase === "done" && <p className="flex items-center gap-1.5 text-xs text-emerald-300"><CheckCircle2 size={13} /> Fertig.</p>}
      </form>
    </section>
  );
}
