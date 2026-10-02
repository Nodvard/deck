import { AlertCircle, ArrowLeft, Eye, EyeOff, HardDrive, KeyRound, Loader2, Lock, Server, ShieldCheck, User } from "lucide-react";
import { useEffect, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { copyrightLine } from "../lib/branding";
import { useAuthStore } from "../state/auth";
import { useBrandingStore } from "../state/branding";

/**
 * Anmeldung und zweiter Faktor. Name, Logo, Untertitel und Farben kommen aus dem
 * Branding (Einstellungen), damit die Seite fuer jeden Betreiber passt.
 *
 * Eine frische Installation (noch kein Konto) leitet auf /setup um.
 */

const FEATURES = [
  { icon: Server, title: "Server und virtuelle Maschinen", text: "Zustand, Auslastung und Verlauf aller Systeme an einer Stelle." },
  { icon: HardDrive, title: "Backups im Blick", text: "Sieht, was gesichert ist, und warnt, bevor der Speicher voll läuft." },
  { icon: ShieldCheck, title: "Nachvollziehbare Änderungen", text: "Jede Aktion mit Freigabe und Protokoll: wer, wann, was." },
];

const UNREACHABLE = "Server nicht erreichbar – bitte gleich noch einmal versuchen.";

const inputClass =
  "w-full rounded-lg border border-white/10 bg-black/30 py-2.5 pl-10 pr-3 text-sm text-white placeholder:text-white/30 outline-none transition focus:border-[var(--color-accent)] focus:ring-2 focus:ring-[color-mix(in_srgb,var(--color-accent)_35%,transparent)]";

export function LoginPage() {
  const status = useAuthStore((s) => s.status);
  const login = useAuthStore((s) => s.login);
  const submitMfa = useAuthStore((s) => s.submitMfa);
  const mfaToken = useAuthStore((s) => s.mfaToken);
  const branding = useBrandingStore((s) => s.branding);
  const location = useLocation();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [capsLock, setCapsLock] = useState(false);
  const [code, setCode] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [needsBootstrap, setNeedsBootstrap] = useState(false);
  const [showForgot, setShowForgot] = useState(false);

  useEffect(() => {
    fetch("/api/v1/auth/bootstrap")
      .then((res) => res.json() as Promise<{ needed: boolean }>)
      .then((body) => setNeedsBootstrap(body.needed))
      .catch(() => {});
  }, []);

  if (needsBootstrap) return <Navigate to="/setup" replace />;

  if (status === "authenticated") {
    const from = (location.state as { from?: string } | null)?.from ?? "/";
    return <Navigate to={from} replace />;
  }

  const productName = branding?.product_name ?? "Nodvard Deck";
  const subtitle = branding?.login_subtitle || "Die Verwaltungsoberfläche für deine Server, Dienste und Backups.";

  async function handleLoginSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!username.trim() || !password) {
      setError("Bitte Benutzername und Passwort eingeben.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      const result = await login(username.trim().toLowerCase(), password);
      if (!result.ok && "error" in result) setError(result.error);
    } catch {
      // fetch wirft ohne Antwort (z. B. Container startet nach einem Deploy neu) --
      // sonst bliebe der Knopf fuer immer auf "Bitte warten …".
      setError(UNREACHABLE);
    } finally {
      setPending(false);
    }
  }

  async function handleMfaSubmit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      const result = await submitMfa(code.replace(/\s/g, ""));
      if (!result.ok) setError(result.error);
      // Zu viele falsche Codes: der Store ist schon zurueck beim Passwort-Schritt,
      // der Hinweis bleibt dort stehen, der alte Code soll nicht wieder auftauchen.
      if (!useAuthStore.getState().mfaToken) setCode("");
    } catch {
      setError(UNREACHABLE);
    } finally {
      setPending(false);
    }
  }

  function backToLogin() {
    useAuthStore.setState({ mfaToken: null });
    setCode("");
    setUseRecovery(false);
    setError(null);
  }

  const logo = branding?.logo_url ? (
    <img src={branding.logo_url} alt="" className="h-11 w-11 rounded-xl object-contain" />
  ) : (
    <span className="accent-gradient grid h-11 w-11 place-items-center rounded-xl text-lg font-bold text-white shadow-lg shadow-black/40">
      {(branding?.short_name || productName).slice(0, 1).toUpperCase()}
    </span>
  );

  return (
    <div className="relative isolate flex min-h-screen bg-[var(--color-background)] text-white">
      <div className="nodvard-deck-glow pointer-events-none absolute inset-0 -z-10" />
      <div className="nodvard-deck-grid pointer-events-none absolute inset-0 -z-10" />

      {/* Linke Haelfte: Produkt (ab Desktop-Breite) */}
      <aside className="hidden flex-1 flex-col justify-between border-r border-white/[0.06] p-12 lg:flex">
        <div className="flex items-center gap-3">
          {logo}
          <div>
            <p className="text-lg font-semibold tracking-tight">{productName}</p>
            {branding?.short_name && branding.short_name !== productName && <p className="text-xs text-white/50">{branding.short_name}</p>}
          </div>
        </div>
        <div className="max-w-md">
          <h2 className="text-3xl font-semibold leading-tight tracking-tight">{subtitle}</h2>
          <ul className="mt-8 space-y-5">
            {FEATURES.map(({ icon: Icon, title, text }) => (
              <li key={title} className="flex gap-3">
                <span className="grid h-9 w-9 flex-none place-items-center rounded-lg bg-white/[0.06] text-[var(--color-accent)]">
                  <Icon size={18} />
                </span>
                <span>
                  <span className="block text-sm font-medium">{title}</span>
                  <span className="block text-sm text-white/55">{text}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
        <p className="text-xs text-white/35" data-testid="copyright">{copyrightLine()}</p>
      </aside>

      {/* Rechte Haelfte: Formular */}
      <main className="flex flex-1 items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            {logo}
            <p className="text-lg font-semibold tracking-tight">{productName}</p>
          </div>

          <div className="panel p-7">
            {mfaToken ? (
              <form onSubmit={(e) => void handleMfaSubmit(e)} className="flex flex-col gap-4" noValidate>
                <div>
                  <h1 className="text-xl font-semibold tracking-tight">Bestätigung</h1>
                  <p className="mt-1 text-sm text-white/55">
                    {useRecovery ? "Gib einen deiner Wiederherstellungs-Codes ein. Jeder Code gilt nur einmal." : "Gib den Code aus deiner Authenticator-App ein."}
                  </p>
                </div>
                <label className="block text-sm">
                  <span className="mb-1.5 block text-white/70">{useRecovery ? "Wiederherstellungs-Code" : "Sechsstelliger Code"}</span>
                  <span className="relative block">
                    <KeyRound size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-white/40" />
                    {useRecovery ? (
                      <input
                        key="recovery"
                        autoFocus
                        autoComplete="off"
                        autoCapitalize="characters"
                        spellCheck={false}
                        placeholder="XXXXX-XXXXX"
                        maxLength={16}
                        value={code}
                        onChange={(e) => setCode(e.target.value.toUpperCase())}
                        className={`${inputClass} font-mono tracking-widest`}
                      />
                    ) : (
                      <input
                        key="totp"
                        autoFocus
                        inputMode="numeric"
                        autoComplete="one-time-code"
                        placeholder="123 456"
                        maxLength={7}
                        value={code}
                        onChange={(e) => setCode(e.target.value)}
                        className={`${inputClass} font-mono tracking-[0.3em]`}
                      />
                    )}
                  </span>
                </label>
                {error && <ErrorBox text={error} />}
                <SubmitButton pending={pending} label="Bestätigen" />
                <button
                  type="button"
                  onClick={() => { setUseRecovery((v) => !v); setCode(""); setError(null); }}
                  className="text-center text-xs text-white/60 underline-offset-2 hover:text-white hover:underline"
                >
                  {useRecovery ? "Stattdessen Code aus der App eingeben" : "Handy nicht zur Hand? Wiederherstellungs-Code verwenden"}
                </button>
                <button type="button" onClick={backToLogin} className="flex items-center justify-center gap-1.5 text-xs text-white/50 hover:text-white">
                  <ArrowLeft size={13} /> Zurück zur Anmeldung
                </button>
              </form>
            ) : (
              <form onSubmit={(e) => void handleLoginSubmit(e)} className="flex flex-col gap-4" noValidate>
                <div>
                  <h1 className="text-xl font-semibold tracking-tight">Anmelden</h1>
                  <p className="mt-1 text-sm text-white/55">Willkommen zurück bei {productName}.</p>
                </div>
                <label className="block text-sm">
                  <span className="mb-1.5 block text-white/70">Benutzername</span>
                  <span className="relative block">
                    <User size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-white/40" />
                    <input
                      autoFocus
                      autoComplete="username"
                      autoCapitalize="none"
                      spellCheck={false}
                      value={username}
                      onChange={(e) => setUsername(e.target.value.toLowerCase())}
                      className={inputClass}
                    />
                  </span>
                </label>
                <label className="block text-sm">
                  <span className="mb-1.5 block text-white/70">Passwort</span>
                  <span className="relative block">
                    <Lock size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-white/40" />
                    <input
                      type={showPassword ? "text" : "password"}
                      autoComplete="current-password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      onKeyUp={(e) => setCapsLock(e.getModifierState?.("CapsLock") ?? false)}
                      className={`${inputClass} pr-10`}
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword((v) => !v)}
                      aria-label={showPassword ? "Passwort verbergen" : "Passwort anzeigen"}
                      className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-white/40 hover:text-white"
                    >
                      {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                    </button>
                  </span>
                  {capsLock && <span className="mt-1.5 block text-xs text-amber-300">Feststelltaste ist aktiv.</span>}
                </label>
                <button
                  type="button"
                  aria-expanded={showForgot}
                  aria-controls="forgot-password-help"
                  onClick={() => setShowForgot((v) => !v)}
                  className="-mt-1 self-end text-xs text-white/60 underline-offset-2 hover:text-white hover:underline"
                >
                  Passwort vergessen?
                </button>
                {showForgot && <ForgotPasswordHelp />}
                {error && <ErrorBox text={error} />}
                <SubmitButton pending={pending} label="Anmelden" />
              </form>
            )}
          </div>

          {branding?.support_url && (
            <p className="mt-6 text-center text-xs text-white/45">
              Probleme bei der Anmeldung?{" "}
              <a href={branding.support_url} target="_blank" rel="noreferrer" className="text-white/70 underline-offset-2 hover:text-white hover:underline">
                Support kontaktieren
              </a>
            </p>
          )}

          {/* Am Handy und Tablet ist die linke Spalte versteckt: die Zeile steht dann hier. */}
          <p className="mt-8 text-center text-xs text-white/35 lg:hidden" data-testid="copyright-compact">{copyrightLine()}</p>
        </div>
      </main>
    </div>
  );
}

const codeBlockClass = "mt-1 block select-all overflow-x-auto rounded bg-black/40 px-2 py-1.5 font-mono text-[11px] text-white/80";

/**
 * Erklaerung zu "Passwort vergessen?". Wiederherstellungs-Codes ersetzen nur den Code aus der
 * Authenticator-App (Schritt "Bestaetigung", s. o.), nicht das Passwort -- das steht hier ehrlich dabei.
 * Die Befehle sind die von `python -m nodvard_deck.admin` (backend/src/nodvard_deck/admin.py, docs/11 Abschnitt 12).
 */
function ForgotPasswordHelp() {
  return (
    <div id="forgot-password-help" className="flex flex-col gap-3 rounded-lg border border-white/10 bg-white/[0.03] p-3 text-xs text-white/65">
      <div>
        <p className="font-medium text-white/85">Handy verloren? Mit Wiederherstellungs-Code anmelden</p>
        <p className="mt-0.5">
          Gib zuerst Benutzername und Passwort ein. Im nächsten Schritt („Bestätigung“) klickst du auf „Handy nicht zur Hand?
          Wiederherstellungs-Code verwenden“ und tippst einen deiner Codes ein. Jeder Code gilt nur einmal. Er ersetzt den Code aus der
          App, nicht das Passwort.
        </p>
      </div>
      <div>
        <p className="font-medium text-white/85">Passwort vergessen? Ein Administrator kann es zurücksetzen</p>
        <p className="mt-0.5">
          Wer Administrator ist, setzt es unter Einstellungen → Benutzer → Bearbeiten neu. Beim ersten Konto (dem Inhaber) geht das nur
          mit dem Notfall-Befehl – unten, auch ohne Befehlszeile.
        </p>
      </div>
      <div>
        <p className="font-medium text-white/85">Notfall-Befehl auf dem Server</p>
        <p className="mt-0.5">Im Ordner mit der Compose-Datei (compose.yml). Der Befehl gibt ein neues Zufalls-Passwort aus:</p>
        <code className={codeBlockClass}>docker compose exec nodvard-deck python -m nodvard_deck.admin reset-password &lt;benutzername&gt;</code>
        <p className="mt-1">Alle Benutzernamen zeigt:</p>
        <code className={codeBlockClass}>docker compose exec nodvard-deck python -m nodvard_deck.admin list-users</code>
        <p className="mt-1">Handy und Codes verloren? Dann schaltet dieser Befehl die Zwei-Faktor-Anmeldung ab:</p>
        <code className={codeBlockClass}>docker compose exec nodvard-deck python -m nodvard_deck.admin disable-2fa &lt;benutzername&gt;</code>
      </div>
      <div data-testid="forgot-no-command-line">
        <p className="font-medium text-white/85">Ohne Befehlszeile: die Konsole des Containers</p>
        <p className="mt-0.5">
          Du verwaltest Nodvard Deck mit Portainer, Docker Desktop oder auf einem NAS? Dann brauchst du keine Befehlszeile: Öffne die
          Konsole des Containers von Nodvard Deck und gib den Befehl dort ein. Den Container erkennst du an seinem Namen: Er enthält
          „nodvard-deck“, meist heißt er „nodvard-deck-nodvard-deck-1“. Ohne das „docker compose exec nodvard-deck“ davor, das steckt
          in der Konsole schon drin:
        </p>
        <ul className="mt-1 list-disc space-y-0.5 pl-4">
          <li><strong className="font-medium text-white/85">Portainer:</strong> Containers → Container von Nodvard Deck → Symbol „Exec Console“ → „Connect“.</li>
          <li><strong className="font-medium text-white/85">Docker Desktop:</strong> „Containers“ → die Gruppe „nodvard-deck“ aufklappen → den Container darin anklicken → Reiter „Exec“.</li>
          <li><strong className="font-medium text-white/85">Synology Container Manager:</strong> Container → den von Nodvard Deck auswählen → „Details“ → Reiter „Terminal“ → „Erstellen“.</li>
          <li><strong className="font-medium text-white/85">Unraid:</strong> Reiter „Docker“ → Symbol des Containers von Nodvard Deck → „Console“.</li>
        </ul>
        <p className="mt-1">Dort gibst du diesen Befehl ein. Er gibt ein neues Zufalls-Passwort aus. Statt <code>&lt;benutzername&gt;</code> schreibst du deinen eigenen Benutzernamen, zum Beispiel <code>admin</code>:</p>
        <code className={codeBlockClass}>python -m nodvard_deck.admin reset-password &lt;benutzername&gt;</code>
        <p className="mt-1">Alle Benutzernamen zeigt <code>python -m nodvard_deck.admin list-users</code>, die Zwei-Faktor-Anmeldung schaltet <code>python -m nodvard_deck.admin disable-2fa &lt;benutzername&gt;</code> ab.</p>
      </div>
    </div>
  );
}

function ErrorBox({ text }: { text: string }) {
  return (
    <p role="alert" className="flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200">
      <AlertCircle size={16} className="mt-0.5 flex-none" />
      {text}
    </p>
  );
}

function SubmitButton({ pending, label }: { pending: boolean; label: string }) {
  return (
    <button
      type="submit"
      disabled={pending}
      className="accent-gradient flex items-center justify-center gap-2 rounded-lg px-3 py-2.5 text-sm font-semibold text-white shadow-lg shadow-black/30 transition hover:brightness-110 disabled:opacity-60"
    >
      {pending && <Loader2 size={16} className="animate-spin" />}
      {pending ? "Bitte warten …" : label}
    </button>
  );
}
