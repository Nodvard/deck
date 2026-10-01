/**
 * Der Ablauf „Sicherung einspielen“ -- derselbe in Einstellungen -> System (Owner, mit Passwort) und im
 * Einrichtungs-Assistenten (ohne Konto, mit Einrichtungscode):
 *
 *   Datei wählen -> hochladen (mit Fortschritt) -> Passwort oder Wiederherstellungsschlüssel -> Zusammenfassung
 *   -> „Einspielen und neu starten“ -> Wartebildschirm (fragt die Gesundheit ab) -> Anmeldung.
 *
 * Passwörter, Schlüssel und der Code stehen nur in lokalem State und werden direkt an `lib/restore.ts`
 * gereicht (nie in einen Query-Cache); nach dem Absenden sind die Felder leer. Nichts davon wird gespeichert.
 *
 * Ansprache: du, wie überall in Nodvard Deck.
 */
import { AlertTriangle, CheckCircle2, FileUp, Loader2, ShieldAlert } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { formatSize, formatWhen } from "../../lib/backups";
import {
  bootstrapAfterRestart,
  browserNavigation,
  cancelRestore,
  formatDuration,
  inspectBackup,
  readUptime,
  restartServer,
  scheduleRestore,
  uploadBackup,
  waitForRestart,
  type PendingRestore,
  type RestoreAuth,
  type RestoreMode,
  type RestoreSecret,
  type StagedRestore,
} from "../../lib/restore";
import { Badge, Button, Field, NoticeLine, errorText, inputClass, type Notice } from "./ui";

type Phase =
  | { kind: "idle" }
  | { kind: "uploading"; loaded: number; total: number }
  | { kind: "secret"; staged: StagedRestore }
  | { kind: "checking"; staged: StagedRestore }
  | { kind: "summary"; staged: StagedRestore }
  | { kind: "applying"; staged: StagedRestore }
  | { kind: "scheduled"; pending: PendingRestore | null; error: string | null }
  | { kind: "waiting"; seconds: number; slow: boolean };

export interface RestoreFlowProps {
  mode: RestoreMode;
  /** Einrichtung: der Einrichtungscode, gehalten vom Aufrufer (wie beim Konto anlegen). */
  setupCode?: string;
  onSetupCodeChange?: (code: string) => void;
  /** Einrichtung: aufklappbare Anleitung „Wo finde ich den Code?“ unter dem Codefeld. */
  codeHelp?: ReactNode;
  /** Zwischenstand bzw. Vormerkung vom Server (Seite wurde neu geladen). */
  resume?: { staged: StagedRestore | null; pending: PendingRestore | null };
  maxUploadBytes?: number;
  /** Nach dem Neustart: in den Einstellungen zur Anmeldung, im Assistenten je nach Ergebnis. */
  onDone: (outcome: { ok: true } | { ok: false; message: string }) => void;
  /** Zurueck, ohne etwas zu tun (Assistent: zurueck zum Konto anlegen). */
  onLeave?: () => void;
}

function secretKindLabel(mode: "passwort" | "schluessel" | undefined): string {
  return mode === "passwort"
    ? "Das Einmal-Passwort, das du beim Herunterladen dieser Sicherung vergeben hast."
    : "Das Sicherungspasswort deiner Installation – oder der Wiederherstellungsschlüssel.";
}

export function RestoreFlow(props: RestoreFlowProps): JSX.Element {
  const { mode, setupCode = "", onSetupCodeChange, codeHelp, resume, maxUploadBytes, onDone, onLeave } = props;
  const initial: Phase = resume?.staged
    ? resume.staged.state === "ready" && resume.staged.summary
      ? { kind: "summary", staged: resume.staged }
      : resume.staged.state === "uploaded"
        ? { kind: "secret", staged: resume.staged }
        : { kind: "idle" }
    : resume?.pending
      ? { kind: "scheduled", pending: resume.pending, error: null }
      : { kind: "idle" };
  const [phase, setPhase] = useState<Phase>(initial);
  const [notice, setNotice] = useState<Notice>(null);
  const [file, setFile] = useState<File | null>(null);
  const [accountPassword, setAccountPassword] = useState("");
  const [secretKind, setSecretKind] = useState<"password" | "recovery_key">("password");
  const [secret, setSecret] = useState("");
  const [understood, setUnderstood] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      abort.current?.abort();
    };
  }, []);

  const auth = useCallback(
    (password: string): RestoreAuth => (mode === "owner" ? { mode, accountPassword: password } : { mode, setupCode }),
    [mode, setupCode],
  );
  const needsAccountPassword = mode === "owner";
  const credentialsReady = mode === "owner" ? Boolean(accountPassword) : Boolean(setupCode.trim());

  const tooBig = file !== null && maxUploadBytes !== undefined && file.size > maxUploadBytes;

  // ---------------------------------------------------------------------------------------- Hochladen
  async function upload() {
    if (!file) return;
    const password = accountPassword;
    setAccountPassword("");
    setNotice(null);
    abort.current = new AbortController();
    setPhase({ kind: "uploading", loaded: 0, total: file.size });
    try {
      const staged = await uploadBackup(file, auth(password), {
        signal: abort.current.signal,
        onProgress: (loaded, total) => alive.current && setPhase({ kind: "uploading", loaded, total }),
      });
      if (!alive.current) return;
      setFile(null);
      setSecretKind("password");
      setPhase({ kind: "secret", staged });
    } catch (err) {
      if (!alive.current) return;
      setPhase({ kind: "idle" });
      if (err instanceof DOMException && err.name === "AbortError") {
        setNotice({ kind: "error", text: "Das Hochladen wurde abgebrochen." });
      } else {
        setNotice({ kind: "error", text: errorText(err) });
      }
    }
  }

  // ------------------------------------------------------------------------------------------ Prüfen
  async function inspect(staged: StagedRestore) {
    const value = secret;
    const body: RestoreSecret = secretKind === "password" ? { password: value } : { recovery_key: value };
    setSecret("");
    setNotice(null);
    setPhase({ kind: "checking", staged });
    try {
      const checked = await inspectBackup(staged.id, body, auth(""));
      if (!alive.current) return;
      setUnderstood(false);
      setPhase({ kind: "summary", staged: checked });
    } catch (err) {
      if (!alive.current) return;
      const status = (err as { status?: number }).status;
      setNotice({ kind: "error", text: errorText(err) });
      // 400: falsches Passwort, die Datei bleibt auf dem Server -- noch einmal. Alles andere: neu hochladen.
      setPhase(status === 400 || status === 507 || status === 409 ? { kind: "secret", staged } : { kind: "idle" });
    }
  }

  async function discard() {
    setNotice(null);
    setSecret("");
    try {
      await cancelRestore(auth(""));
    } catch (err) {
      if (alive.current) setNotice({ kind: "error", text: errorText(err) });
      return;
    }
    if (alive.current) setPhase({ kind: "idle" });
  }

  // ------------------------------------------------------------------- Einspielen und neu starten
  async function restartAndWait(password: string) {
    const baseline = await readUptime();
    await restartServer(auth(password));
    if (!alive.current) return;
    abort.current = new AbortController();
    setPhase({ kind: "waiting", seconds: 0, slow: false });
    const back = await waitForRestart(baseline, {
      signal: abort.current.signal,
      onTick: (seconds) => alive.current && setPhase({ kind: "waiting", seconds, slow: false }),
    });
    if (!alive.current) return;
    if (!back) {
      setPhase({ kind: "waiting", seconds: 0, slow: true });
      return;
    }
    await finishAfterRestart();
  }

  async function finishAfterRestart() {
    if (mode === "owner") {
      // Harter Seitenwechsel: alte Anmeldung und altes Aussehen sind weg, die Anmeldung zeigt den neuen Stand.
      browserNavigation.assign("/login");
      onDone({ ok: true });
      return;
    }
    const state = await bootstrapAfterRestart();
    if (!alive.current) return;
    if (state && state.needed === false) {
      onDone({ ok: true });
      browserNavigation.assign("/login");
      return;
    }
    const why = state?.restore?.message ?? "Beim Neustart wurde nichts eingespielt (die Vormerkung war abgelaufen oder ist verloren gegangen).";
    setPhase({ kind: "idle" });
    onDone({ ok: false, message: why });
    setNotice({ kind: "error", text: `Das Einspielen hat nicht geklappt: ${why} Es wurde nichts verändert.` });
  }

  async function apply(staged: StagedRestore) {
    const password = accountPassword;
    setAccountPassword("");
    setNotice(null);
    setPhase({ kind: "applying", staged });
    let pending: PendingRestore | null = null;
    try {
      pending = await scheduleRestore(staged.id, auth(password));
    } catch (err) {
      if (!alive.current) return;
      setNotice({ kind: "error", text: errorText(err) });
      setPhase({ kind: "summary", staged });
      return;
    }
    try {
      await restartAndWait(password);
    } catch (err) {
      if (!alive.current) return;
      // Vorgemerkt, aber der Neustart ging nicht (z. B. falsches Passwort-Limit): nicht verlieren, anbieten.
      setPhase({ kind: "scheduled", pending, error: errorText(err) });
    }
  }

  async function retryRestart() {
    const password = accountPassword;
    setAccountPassword("");
    setNotice(null);
    const was = phase;
    try {
      await restartAndWait(password);
    } catch (err) {
      if (alive.current) setPhase(was.kind === "scheduled" ? { ...was, error: errorText(err) } : was);
    }
  }

  async function dropPending() {
    setNotice(null);
    try {
      await cancelRestore(auth(""));
      if (alive.current) setPhase({ kind: "idle" });
    } catch (err) {
      if (alive.current) setNotice({ kind: "error", text: errorText(err) });
    }
  }

  // --------------------------------------------------------------------------------------------- Ansicht
  const codeField =
    mode === "setup" ? (
      <div className="space-y-2">
        <Field
          label="Einrichtungscode"
          hint="Steht im Protokoll des Containers. Er schützt davor, dass jemand anderes im Netz eine Sicherung einspielt."
        >
          <input
            value={setupCode}
            autoCapitalize="characters"
            autoComplete="off"
            spellCheck={false}
            placeholder="XXXX-XXXX-XXXX"
            aria-label="Einrichtungscode"
            onChange={(e) => onSetupCodeChange?.(e.target.value.toUpperCase())}
            className={`${inputClass} font-mono tracking-wider`}
          />
        </Field>
        {codeHelp}
      </div>
    ) : null;

  if (phase.kind === "waiting") {
    return (
      <div role="status" aria-live="polite" className="space-y-3 text-sm" data-testid="restore-waiting">
        <p className="flex items-center gap-2 font-medium">
          <Loader2 size={16} className="animate-spin" />
          {phase.slow ? "Das dauert länger als erwartet." : "Nodvard Deck startet neu und spielt die Sicherung ein …"}
        </p>
        {phase.slow ? (
          <>
            <p className="text-white/70">
              Sieh im Protokoll des Containers nach, was passiert.{" "}
              Läuft er gar nicht mehr, fehlt vermutlich die Neustart-Regel (<code>restart: unless-stopped</code>): dann den Container von Hand starten,
              z. B. mit <code>docker compose up -d</code>. Die Sicherung wird beim Start eingespielt.
            </p>
            <Button
              onClick={() => {
                setPhase({ kind: "waiting", seconds: 0, slow: false });
                void (async () => {
                  abort.current = new AbortController();
                  const back = await waitForRestart(null, { signal: abort.current.signal });
                  if (alive.current) {
                    if (back) await finishAfterRestart();
                    else setPhase({ kind: "waiting", seconds: 0, slow: true });
                  }
                })();
              }}
            >
              Weiter warten
            </Button>
          </>
        ) : (
          <p className="text-white/60">
            Bitte lass diese Seite offen. Das dauert meist eine halbe Minute, bei großen Datenmengen länger. Danach geht es automatisch zur Anmeldung weiter.
            {phase.seconds > 0 && <span className="text-white/40"> ({formatDuration(phase.seconds)})</span>}
          </p>
        )}
      </div>
    );
  }

  if (phase.kind === "uploading") {
    const percent = phase.total > 0 ? Math.min(100, Math.round((phase.loaded / phase.total) * 100)) : 0;
    return (
      <div className="space-y-3 text-sm" data-testid="restore-uploading">
        <p className="flex items-center gap-2 font-medium">
          <Loader2 size={16} className="animate-spin" /> Wird hochgeladen …
        </p>
        <div
          role="progressbar"
          aria-label="Fortschritt des Hochladens"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
          className="h-2 overflow-hidden rounded-full bg-white/10"
        >
          <div className="h-full rounded-full bg-[var(--color-accent)] transition-[width]" style={{ width: `${percent}%` }} />
        </div>
        <p className="text-xs text-white/55">
          {percent} % · {formatSize(phase.loaded)} von {formatSize(phase.total)}
        </p>
        <Button variant="ghost" onClick={() => abort.current?.abort()}>
          Abbrechen
        </Button>
      </div>
    );
  }

  if (phase.kind === "secret" || phase.kind === "checking") {
    const staged = phase.staged;
    const checking = phase.kind === "checking";
    const header = staged.header;
    const keyAllowed = header?.mode !== "passwort";
    return (
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (secret && !checking) void inspect(staged);
        }}
      >
        <NoticeLine notice={notice} />
        <p className="text-sm text-white/70">
          Die Datei ist angekommen
          {staged.size ? ` (${formatSize(staged.size)}` : ""}
          {header ? `${staged.size ? ", " : " ("}erstellt ${formatWhen(header.created_at)}, Version ${header.app_version}` : ""}
          {staged.size || header ? ")" : ""}. Zum Prüfen wird sie jetzt entschlüsselt.
        </p>
        {keyAllowed && (
          <fieldset className="flex flex-wrap gap-x-5 gap-y-2 text-sm">
            <legend className="sr-only">Womit öffnen?</legend>
            <label className="flex items-center gap-2">
              <input type="radio" name="restore-secret-kind" checked={secretKind === "password"} onChange={() => { setSecretKind("password"); setSecret(""); }} />
              Passwort
            </label>
            <label className="flex items-center gap-2">
              <input type="radio" name="restore-secret-kind" checked={secretKind === "recovery_key"} onChange={() => { setSecretKind("recovery_key"); setSecret(""); }} />
              Wiederherstellungsschlüssel
            </label>
          </fieldset>
        )}
        <Field
          label={secretKind === "password" ? "Passwort der Sicherung" : "Wiederherstellungsschlüssel der Sicherung"}
          hint={secretKind === "password" ? secretKindLabel(header?.mode) : "Beginnt mit AGE-SECRET-KEY-1 …"}
        >
          <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            autoFocus
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            aria-label={secretKind === "password" ? "Passwort der Sicherung" : "Wiederherstellungsschlüssel der Sicherung"}
            className={`${inputClass} ${secretKind === "recovery_key" ? "font-mono text-xs" : ""}`}
          />
        </Field>
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={() => void discard()} disabled={checking}>
            Verwerfen
          </Button>
          <Button type="submit" variant="primary" busy={checking} disabled={!secret}>
            {checking ? "Wird geprüft …" : "Prüfen"}
          </Button>
        </div>
        {checking && <p className="text-xs text-white/50">Das Entschlüsseln und Prüfen kann bei großen Sicherungen einige Minuten dauern.</p>}
      </form>
    );
  }

  if (phase.kind === "summary" || phase.kind === "applying") {
    const staged = phase.staged;
    const applying = phase.kind === "applying";
    const s = staged.summary;
    return (
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (understood && credentialsReady && !applying) void apply(staged);
        }}
      >
        <NoticeLine notice={notice} />
        {s && <SummaryTable summary={s} />}
        {s?.warnings.map((text) => (
          <p key={text} role="note" className="flex gap-2 rounded-lg border border-amber-400/30 bg-amber-400/5 px-3 py-2 text-sm text-amber-100">
            <AlertTriangle size={16} className="mt-0.5 flex-none" /> {text}
          </p>
        ))}
        <p className="flex gap-2 rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-xs text-white/65">
          <ShieldAlert size={15} className="mt-0.5 flex-none" />
          <span>
            age, das Verfahren hinter der Verschlüsselung, prüft nicht, <strong>wer</strong> eine Sicherung erstellt hat. Wer das Passwort kennt, kann eine
            Sicherung bauen. Spiele nur Sicherungen ein, die du selbst erstellt hast –
            die Angaben oben helfen beim Prüfen.
          </span>
        </p>
        <div role="note" aria-label="Wichtig" className="space-y-2 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-3 text-sm text-red-100">
          <p className="font-semibold">Das Einspielen ersetzt alles – auch die Konten.</p>
          <ul className="list-disc space-y-1 pl-5 text-red-100/90">
            <li>
              Konten, Server, Zugangsdaten, Einstellungen und die Daten der Erweiterungen werden durch den Stand der Sicherung ersetzt. Danach gelten die Konten
              und Passwörter aus der Sicherung, und alle müssen sich neu anmelden.
            </li>
            <li>
              Der bisherige Stand bleibt auf dem Server liegen (Ordner <code>restore</code> im Datenordner), bis du ihn löschst.
            </li>
            <li>
              Läuft die alte Installation noch (z. B. auf einem anderen Gerät), benutzen beide dieselben Schlüssel und Zugangsdaten.{" "}
              Betreibe danach nur eine von beiden.
            </li>
            <li>
              Nodvard Deck startet dafür neu. Das klappt nur, wenn der Container eine Neustart-Regel hat (in den mitgelieferten Compose-Dateien steht{" "}
              <code>restart: unless-stopped</code>). Ohne sie bleibt er danach aus und muss von Hand gestartet werden, z. B. mit <code>docker compose up -d</code>.
            </li>
          </ul>
        </div>
        <label className="flex items-start gap-2 text-sm text-white/80">
          <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} className="mt-1" />
          Ich habe verstanden: Alles in dieser Installation wird durch die Sicherung ersetzt, auch die Konten.
        </label>
        {needsAccountPassword && (
          <Field label="Dein Anmeldepasswort zur Bestätigung" className="sm:max-w-sm">
            <input
              type="password"
              autoComplete="current-password"
              value={accountPassword}
              onChange={(e) => setAccountPassword(e.target.value)}
              aria-label="Anmeldepasswort zur Bestätigung"
              className={inputClass}
            />
          </Field>
        )}
        {mode === "setup" && !setupCode.trim() && codeField}
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={() => void discard()} disabled={applying}>
            Abbrechen
          </Button>
          <Button type="submit" variant="danger" busy={applying} disabled={!understood || !credentialsReady}>
            {applying ? "Wird vorbereitet …" : "Einspielen und neu starten"}
          </Button>
        </div>
      </form>
    );
  }

  if (phase.kind === "scheduled") {
    return (
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (credentialsReady) void retryRestart();
        }}
      >
        <NoticeLine notice={notice} />
        <p role="note" className="flex gap-2 rounded-lg border border-amber-400/30 bg-amber-400/5 px-3 py-2 text-sm text-amber-100">
          <AlertTriangle size={16} className="mt-0.5 flex-none" />
          <span>
            Eine Sicherung ist zum Einspielen <strong>vorgemerkt</strong>
            {phase.pending?.backup.owner_name ? ` (Owner „${phase.pending.backup.owner_name}“)` : ""}. Sie wird beim nächsten Start eingespielt und gilt noch
            {phase.pending ? ` etwa ${formatDuration(phase.pending.expires_in)}` : " kurze Zeit"}.
            {phase.error ? ` Der Neustart hat nicht geklappt: ${phase.error}` : ""}
          </span>
        </p>
        {needsAccountPassword && (
          <Field label="Dein Anmeldepasswort zur Bestätigung" className="sm:max-w-sm">
            <input
              type="password"
              autoComplete="current-password"
              value={accountPassword}
              onChange={(e) => setAccountPassword(e.target.value)}
              aria-label="Anmeldepasswort zur Bestätigung"
              className={inputClass}
            />
          </Field>
        )}
        {mode === "setup" && !setupCode.trim() && codeField}
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={() => void dropPending()}>
            Abbrechen
          </Button>
          <Button type="submit" variant="danger" disabled={!credentialsReady}>
            Jetzt neu starten
          </Button>
        </div>
      </form>
    );
  }

  // idle: Datei wählen
  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (file && credentialsReady && !tooBig) void upload();
      }}
    >
      <NoticeLine notice={notice} />
      <p className="text-sm text-white/70">
        {mode === "owner"
          ? "Wähle eine Sicherungsdatei (.ndbak). Sie wird hochgeladen und geprüft; eingespielt wird erst, wenn du es bestätigst."
          : "Du hast schon eine Sicherung von Nodvard Deck? Dann spiel sie hier ein: Konten, Server, Zugangsdaten und Einstellungen kommen zurück, du musst nichts neu einrichten."}
      </p>
      {codeField}
      <Field
        label="Sicherungsdatei"
        hint={maxUploadBytes ? `Dateiendung .ndbak, höchstens ${formatSize(maxUploadBytes)}.` : "Dateiendung .ndbak."}
      >
        <input
          type="file"
          accept=".ndbak,application/octet-stream"
          aria-label="Sicherungsdatei"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className={`${inputClass} file:mr-3 file:rounded-md file:border-0 file:bg-white/10 file:px-3 file:py-1 file:text-sm file:text-white`}
        />
      </Field>
      {file && (
        <p className="flex flex-wrap items-center gap-2 text-xs text-white/55">
          <FileUp size={14} />
          <span className="break-all">{file.name}</span> · {formatSize(file.size)}
          {tooBig && <Badge tone="bad">zu groß</Badge>}
        </p>
      )}
      {tooBig && maxUploadBytes !== undefined && (
        <p role="alert" className="text-xs text-red-300">
          Die Datei ist größer als erlaubt ({formatSize(maxUploadBytes)}). Die Obergrenze lässt sich mit <code>NODVARD_DECK_RESTORE_MAX_UPLOAD_BYTES</code> anheben.
        </p>
      )}
      {needsAccountPassword && (
        <Field label="Dein Anmeldepasswort zur Bestätigung" hint="Wird zum Hochladen gebraucht und nicht gespeichert." className="sm:max-w-sm">
          <input
            type="password"
            autoComplete="current-password"
            value={accountPassword}
            onChange={(e) => setAccountPassword(e.target.value)}
            aria-label="Anmeldepasswort zum Hochladen"
            className={inputClass}
          />
        </Field>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        {onLeave && (
          <Button variant="ghost" onClick={onLeave}>
            Zurück
          </Button>
        )}
        <Button type="submit" variant="primary" disabled={!file || !credentialsReady || tooBig}>
          Hochladen
        </Button>
      </div>
    </form>
  );
}

function SummaryTable({ summary }: { summary: NonNullable<StagedRestore["summary"]> }) {
  const rows: [string, ReactNode][] = [
    ["Erstellt am", formatWhen(summary.created_at)],
    ["Version", summary.app_version ?? "–"],
    ["Installation", <span key="i" className="break-all font-mono text-xs">{summary.instance_id ?? "–"}</span>],
    ["Inhaber (Owner)", summary.owner_name ?? "–"],
    ["Nutzer", summary.users ?? "–"],
    ["Server", summary.hosts ?? "–"],
    [
      "Erweiterungen",
      summary.extensions.length ? (
        <span key="e" className="flex flex-wrap gap-1">
          {summary.extensions.map((e) => (
            <Badge key={e.id}>{e.id}</Badge>
          ))}
        </span>
      ) : (
        "–"
      ),
    ],
  ];
  return (
    <div>
      <h4 className="mb-2 flex items-center gap-2 text-sm font-semibold">
        <CheckCircle2 size={15} className="text-emerald-300" /> Das steckt in der Sicherung
      </h4>
      <dl className="grid grid-cols-1 gap-x-4 gap-y-1.5 rounded-lg border border-white/10 bg-black/20 px-3 py-3 text-sm sm:grid-cols-[10rem_minmax(0,1fr)]">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-white/50">{label}</dt>
            <dd className="mb-1.5 min-w-0 sm:mb-0">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
