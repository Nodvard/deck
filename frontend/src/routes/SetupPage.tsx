import { ChevronDown, ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { apiFetch } from "../lib/api";
import type { Branding } from "../lib/branding";
import { useAuthStore } from "../state/auth";
import { useBrandingStore } from "../state/branding";
import { RestoreFlow } from "./settings/RestoreFlow";
import { Button } from "./settings/ui";
import { FinishStep, codeBlockClass } from "./setup/FinishStep";
import { ModulesStep } from "./setup/ModulesStep";
import { StepNav } from "./setup/StepNav";
import { TimezoneStep } from "./setup/TimezoneStep";
import { TotpStep } from "./setup/TotpStep";

/**
 * Einrichtungs-Assistent einer frischen Installation. Ohne diese Seite waere ohne API-Client
 * nichts bedienbar: `POST /api/v1/auth/bootstrap` legt den ersten Account an, `/login` kann ohne
 * Nutzer nie klappen. Sechs Schritte, nur der erste ist Pflicht:
 *
 *   1. Konto (Einrichtungscode aus dem Container-Protokoll, Benutzername, Passwort) -- oder
 *      „Sicherung einspielen“ (`RestoreFlow`: dieselben Ablauf-Schritte wie in den Einstellungen, nur
 *      mit dem Einrichtungscode statt mit einem Konto; danach startet Nodvard Deck neu und die
 *      Anmeldung mit den Konten aus der Sicherung folgt)
 *   2. Zeitzone (vorbelegt mit der des Geraets; `PUT /settings/system.timezone`)
 *   3. Module ("Was willst du nutzen?", Schalter je Modul)
 *   4. Zwei-Faktor (QR-Code, Bestaetigung, Wiederherstellungs-Codes) -- freiwillig
 *   5. Aussehen (Produktname, Akzentfarbe) -- freiwillig
 *   6. Fertig (Hinweis auf "Erste Schritte" im Cockpit, Notfall-Befehl)
 *
 * Wiederherstellungs-Codes gibt es erst MIT Zwei-Faktor (api/v1/me.py); sie erscheinen deshalb
 * direkt nach der Bestaetigung in Schritt 4 (RecoveryCodesPanel, einmalig).
 *
 * Selbstlimitierend wie der Backend-Endpunkt selbst: `GET /auth/bootstrap` entscheidet beim Mount,
 * ob diese Seite ueberhaupt gezeigt wird -- ein zweiter Aufruf (z. B. per Lesezeichen) landet auf
 * /login statt auf einem 409 vom Absenden.
 *
 * Neuladen mitten drin: Das Konto existiert dann schon (`needed: false`). Der erreichte Schritt
 * steht in `sessionStorage`; ist er da und die Anmeldung per Cookie noch gueltig, geht es dort
 * weiter, sonst auf /login (danach uebernimmt die Karte "Erste Schritte" im Cockpit).
 */
type Step = "account" | "timezone" | "modules" | "totp" | "branding" | "done";

const STEPS: { id: Step; title: string }[] = [
  { id: "account", title: "Administrator-Konto anlegen" },
  { id: "timezone", title: "Zeitzone" },
  { id: "modules", title: "Was willst du nutzen?" },
  { id: "totp", title: "Zwei-Faktor-Anmeldung (optional)" },
  { id: "branding", title: "Aussehen (optional)" },
  { id: "done", title: "Fertig" },
];

/** Merkzettel fuers Neuladen: der zuletzt erreichte Schritt (nur pro Browser-Tab, nie das Konto selbst). */
const RESUME_KEY = "deck.setup.step";

function readResumeStep(): Step | null {
  try {
    const saved = sessionStorage.getItem(RESUME_KEY);
    return STEPS.some((s) => s.id === saved) && saved !== "account" ? (saved as Step) : null;
  } catch {
    return null;
  }
}

function writeResumeStep(step: Step | null): void {
  try {
    if (step && step !== "account") sessionStorage.setItem(RESUME_KEY, step);
    else sessionStorage.removeItem(RESUME_KEY);
  } catch {
    /* Speicher gesperrt (privates Fenster): dann geht Neuladen eben auf /login */
  }
}

export function SetupPage() {
  const navigate = useNavigate();
  /** `null` = wird geprueft, `false` = nichts zu tun (-> /login). */
  const [needed, setNeeded] = useState<boolean | null>(null);
  const bootstrap = useAuthStore((s) => s.bootstrap);
  const brandingLoaded = useBrandingStore((s) => s.loaded);
  const branding = useBrandingStore((s) => s.branding);
  const loadBranding = useBrandingStore((s) => s.load);
  const setAndApplyBranding = useBrandingStore((s) => s.setAndApply);

  const [step, setStep] = useState<Step>("account");
  const [setupCode, setSetupCode] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [productName, setProductName] = useState("");
  const [shortName, setShortName] = useState("");
  const [accent, setAccent] = useState("#0ea5e9");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  /** Erster Schritt: statt eines neuen Kontos eine Sicherung einspielen. */
  const [restoring, setRestoring] = useState(false);

  function goTo(next: Step) {
    setError(null);
    setStep(next);
    writeResumeStep(next);
  }

  useEffect(() => {
    let alive = true;
    async function check() {
      let isNeeded: boolean;
      try {
        const res = await fetch("/api/v1/auth/bootstrap");
        isNeeded = ((await res.json()) as { needed: boolean }).needed;
      } catch {
        isNeeded = false; // API nicht erreichbar -- lieber /login zeigen als eine tote Seite
      }
      if (!alive) return;
      if (isNeeded) return setNeeded(true);

      // Das Konto gibt es schon. Mitten im Assistenten neu geladen? Dann dort weitermachen.
      const saved = readResumeStep();
      let signedIn = false;
      if (saved) {
        try {
          signedIn = useAuthStore.getState().status === "authenticated" || (await useAuthStore.getState().refresh());
        } catch {
          signedIn = false;
        }
      }
      if (!alive) return;
      if (saved && signedIn) {
        setStep(saved);
        setNeeded(true);
      } else {
        writeResumeStep(null);
        setNeeded(false);
      }
    }
    void check();
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!brandingLoaded) void loadBranding();
  }, [brandingLoaded, loadBranding]);

  useEffect(() => {
    if (branding) {
      setProductName(branding.product_name);
      setShortName(branding.short_name);
      setAccent(branding.colors.accent);
    }
  }, [branding]);

  if (needed === null || !brandingLoaded) return <p className="p-6 text-sm opacity-60">Lade …</p>;
  if (!needed) return <Navigate to="/login" replace />;

  async function handleAccountSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!setupCode.trim()) {
      setError("Bitte den Einrichtungscode eingeben.");
      return;
    }
    if (password !== passwordConfirm) {
      setError("Passwörter stimmen nicht überein.");
      return;
    }
    setPending(true);
    let result: Awaited<ReturnType<typeof bootstrap>>;
    try {
      result = await bootstrap(username, password, setupCode.trim());
    } catch {
      // fetch wirft ohne Antwort -- sonst bliebe der Knopf fuer immer auf "…" (wie auf der Anmeldeseite).
      setError("Server nicht erreichbar – bitte gleich noch einmal versuchen.");
      return;
    } finally {
      setPending(false);
    }
    if (!result.ok) {
      setError(result.error);
      return;
    }
    goTo("timezone");
  }

  async function handleBrandingSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!branding) return;
    setPending(true);
    setError(null);
    try {
      const updated = await apiFetch<Branding>("/branding", {
        method: "PUT",
        body: JSON.stringify({
          ...branding,
          product_name: productName,
          short_name: shortName,
          colors: { ...branding.colors, accent },
        }),
      });
      setAndApplyBranding(updated);
    } catch (err) {
      setPending(false);
      setError(err instanceof Error ? err.message : String(err));
      return;
    }
    setPending(false);
    goTo("done");
  }

  function finish() {
    // Client-seitige Navigation, KEIN window.location.assign -- ein Hard-Reload
    // wuerde den In-Memory-Access-Token verwerfen (D-07) und RequireAuth zu einem
    // stillen Refresh zwingen. Unter React 18 StrictMode loest das
    // den bekannten Refresh-Token-Race (siehe `refresh()` in state/auth.ts) aus --
    // hier waere er sonst bei JEDER Erstinstallation deterministisch statt nur
    // gelegentlich. `status` ist nach bootstrap()/login() bereits "authenticated",
    // RequireAuth muss also gar nicht erst refreshen.
    writeResumeStep(null);
    navigate("/", { replace: true });
  }

  const index = STEPS.findIndex((s) => s.id === step);
  const restoreView = step === "account" && restoring;
  const wide = step === "modules" || restoreView;

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-background)] px-4 py-6 sm:px-6">
      <div className={`w-full min-w-0 rounded-lg border border-white/10 bg-[var(--color-surface)] p-4 sm:p-6 ${wide ? "max-w-2xl" : "max-w-md"}`}>
        <h1 className="mb-1 text-lg font-semibold">Willkommen</h1>
        {restoreView ? (
          <p className="mb-4 text-sm opacity-60" data-testid="setup-progress">Sicherung einspielen</p>
        ) : (
          <>
            <p className="text-sm opacity-60" data-testid="setup-progress">
              Schritt {index + 1} von {STEPS.length} — {STEPS[index].title}
            </p>
            <div className="mb-4 mt-2 flex gap-1" aria-hidden="true">
              {STEPS.map((s, i) => (
                <span key={s.id} className={`h-1 flex-1 rounded-full ${i <= index ? "bg-[var(--color-accent)]" : "bg-white/10"}`} />
              ))}
            </div>
          </>
        )}

        {restoreView ? (
          <RestoreFlow
            mode="setup"
            setupCode={setupCode}
            onSetupCodeChange={setSetupCode}
            codeHelp={<CodeHelp />}
            onLeave={() => setRestoring(false)}
            onDone={(outcome) => {
              // Bei Erfolg wechselt der Ablauf selbst zur Anmeldung (harter Seitenwechsel): der Code wird nicht mehr gebraucht.
              if (outcome.ok) setSetupCode("");
            }}
          />
        ) : step === "account" ? (
          <form onSubmit={(e) => void handleAccountSubmit(e)} className="flex flex-col gap-3">
            <label className="text-sm">
              Einrichtungscode
              <input
                autoFocus
                value={setupCode}
                autoCapitalize="characters"
                autoComplete="off"
                spellCheck={false}
                placeholder="XXXX-XXXX-XXXX"
                onChange={(e) => setSetupCode(e.target.value.toUpperCase())}
                className="mt-1 w-full rounded border border-white/20 bg-transparent px-3 py-2 font-mono tracking-wider"
              />
              <span className="mt-1 block text-xs opacity-60">
                Steht im Protokoll des Containers: <code>docker compose logs nodvard-deck</code>. Er schützt die Einrichtung davor, dass jemand anderes im Netz das erste Konto anlegt.
              </span>
            </label>
            <CodeHelp />
            <label className="text-sm">
              Benutzername
              <input
                value={username}
                autoCapitalize="none"
                spellCheck={false}
                onChange={(e) => setUsername(e.target.value.toLowerCase())}
                className="mt-1 w-full rounded border border-white/20 bg-transparent px-3 py-2"
              />
              <span className="mt-1 block text-xs opacity-60">Nur Kleinbuchstaben, z. B. „admin“. Großbuchstaben werden automatisch umgewandelt.</span>
            </label>
            <label className="text-sm">
              Passwort
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="mt-1 w-full rounded border border-white/20 bg-transparent px-3 py-2"
              />
            </label>
            <label className="text-sm">
              Passwort bestätigen
              <input
                type="password"
                value={passwordConfirm}
                onChange={(e) => setPasswordConfirm(e.target.value)}
                className="mt-1 w-full rounded border border-white/20 bg-transparent px-3 py-2"
              />
            </label>
            {error && <p className="text-sm text-red-400">{error}</p>}
            <Button type="submit" variant="primary" busy={pending} className="py-2">
              Konto anlegen
            </Button>
            <div className="mt-2 border-t border-white/10 pt-4">
              <p className="mb-2 text-sm opacity-70">Du hast schon eine Sicherung von Nodvard Deck?</p>
              <Button onClick={() => { setError(null); setRestoring(true); }} className="w-full py-2">
                Oder: Sicherung einspielen
              </Button>
            </div>
          </form>
        ) : step === "timezone" ? (
          <TimezoneStep onNext={() => goTo("modules")} />
        ) : step === "modules" ? (
          <ModulesStep onBack={() => goTo("timezone")} onNext={() => goTo("totp")} />
        ) : step === "totp" ? (
          <TotpStep username={username || useAuthStore.getState().user?.username || ""} onBack={() => goTo("modules")} onNext={() => goTo("branding")} />
        ) : step === "branding" ? (
          <form onSubmit={(e) => void handleBrandingSubmit(e)} className="flex flex-col gap-3">
            <label className="text-sm">
              Produktname
              <input
                autoFocus
                value={productName}
                onChange={(e) => setProductName(e.target.value)}
                className="mt-1 w-full rounded border border-white/20 bg-transparent px-3 py-2"
              />
            </label>
            <label className="text-sm">
              Kurzname
              <input
                value={shortName}
                onChange={(e) => setShortName(e.target.value)}
                className="mt-1 w-full rounded border border-white/20 bg-transparent px-3 py-2"
              />
            </label>
            <label className="text-sm">
              Akzentfarbe
              <input
                type="color"
                value={accent}
                onChange={(e) => setAccent(e.target.value)}
                className="mt-1 h-9 w-full rounded border border-white/20 bg-transparent px-1 py-1"
              />
            </label>
            <p className="text-xs opacity-50">Logo, weitere Farben und Support-Link lassen sich später in den Einstellungen anpassen.</p>
            {error && <p className="text-sm text-red-400">{error}</p>}
            <StepNav onBack={() => goTo("totp")}>
              <Button onClick={() => goTo("done")}>Überspringen</Button>
              <Button type="submit" variant="primary" busy={pending}>Speichern</Button>
            </StepNav>
          </form>
        ) : (
          <FinishStep onBack={() => goTo("branding")} onFinish={finish} />
        )}
      </div>
    </div>
  );
}

/** Aufklappbare Anleitung: wo steht der Einrichtungscode? (Text im Protokoll: core/setup_code.py::banner) */
function CodeHelp() {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded border border-white/10 text-sm">
      <button
        type="button"
        aria-expanded={open}
        aria-controls="setup-code-help"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 px-3 py-2 text-left font-medium"
      >
        {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        Wo finde ich den Code?
      </button>
      {open && (
        <div id="setup-code-help" className="flex flex-col gap-3 border-t border-white/10 px-3 py-3 text-xs">
          <p className="opacity-70">
            Der Code steht im <strong>Protokoll (Log)</strong> des Containers „nodvard-deck“: in einem Rahmen aus <code>=</code>-Zeichen, der Code
            ganz allein in einer Zeile, etwa <code>K7MQ-X2VD-H9PA</code>. Nach einem Neustart steht derselbe Code noch einmal weiter unten.
          </p>
          <div>
            <p className="font-medium">Befehlszeile</p>
            <p className="opacity-70">Im Ordner mit der Compose-Datei:</p>
            <code className={codeBlockClass}>docker compose logs nodvard-deck | grep -A1 Einrichtungscode</code>
            <p className="mt-1 opacity-70">Windows PowerShell hat kein grep, dort:</p>
            <code className={codeBlockClass}>docker compose logs nodvard-deck | Select-String -Context 0,4 Einrichtungscode</code>
          </div>
          <div>
            <p className="font-medium">Docker Desktop</p>
            <p className="opacity-70">Links „Containers“ → „nodvard-deck“ anklicken → Reiter „Logs“.</p>
          </div>
          <div>
            <p className="font-medium">Portainer</p>
            <p className="opacity-70">Containers → „nodvard-deck“ → Symbol „Logs“.</p>
          </div>
          <div>
            <p className="font-medium">Synology Container Manager</p>
            <p className="opacity-70">Container → „nodvard-deck“ auswählen → „Details“ → Reiter „Protokoll“.</p>
          </div>
          <div>
            <p className="font-medium">Unraid</p>
            <p className="opacity-70">Reiter „Docker“ → Symbol von „nodvard-deck“ anklicken → „Logs“.</p>
          </div>
        </div>
      )}
    </div>
  );
}

