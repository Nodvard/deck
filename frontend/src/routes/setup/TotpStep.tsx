/**
 * Assistent, Schritt „Zwei-Faktor-Anmeldung“ (freiwillig): QR-Code für die Authenticator-App, Code
 * bestätigen, danach die Wiederherstellungs-Codes (einmalig). Dieselben Endpunkte und Bausteine wie
 * Einstellungen -> Mein Konto (`TotpEnroll`, `RecoveryCodesPanel`).
 *
 * Wer nach einem Neuladen hierher zurückkommt, obwohl Zwei-Faktor schon läuft, sieht das und kann
 * die Wiederherstellungs-Codes später unter „Mein Konto“ neu erzeugen (der Server zeigt sie nur
 * ein einziges Mal).
 */
import { ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "../../lib/api";
import { RecoveryCodesPanel } from "../settings/RecoveryCodesPanel";
import { TotpEnroll, type TotpSetup } from "../settings/TotpEnroll";
import { TotpPasswordForm } from "../settings/TotpPasswordForm";
import { Button, NoticeLine, errorText, type Notice } from "../settings/ui";
import { StepNav } from "./StepNav";

export function TotpStep({ username, onBack, onNext }: { username: string; onBack: () => void; onNext: (enabled: boolean) => void }) {
  /** `null` = noch nicht geladen. */
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [setup, setSetup] = useState<TotpSetup | null>(null);
  const [asking, setAsking] = useState(false);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  useEffect(() => {
    let alive = true;
    api
      .get<{ totp_enabled?: boolean }>("/me")
      .then((me) => alive && setEnabled(Boolean(me.totp_enabled)))
      .catch(() => alive && setEnabled(false));
    return () => { alive = false; };
  }, []);

  // Die Codes sieht man nur jetzt: vor dem Neuladen oder Schließen warnen.
  useEffect(() => {
    if (!codes) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [codes]);

  async function start(password: string) {
    setBusy(true);
    setNotice(null);
    try {
      setSetup(await api.post<TotpSetup>("/me/totp/setup", { current_password: password }));
      setAsking(false);
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  async function confirm(code: string) {
    setBusy(true);
    setNotice(null);
    try {
      const res = await api.post<{ recovery_codes?: string[] } | undefined>("/me/totp/confirm", { code });
      setSetup(null);
      setEnabled(true);
      if (res?.recovery_codes?.length) setCodes(res.recovery_codes);
      else setNotice({ kind: "ok", text: "Zwei-Faktor-Anmeldung ist aktiv." });
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  if (enabled === null) return <p className="text-sm text-white/50">Lade …</p>;

  // Frisch eingerichtet: erst die Codes, der Knopf darunter geht direkt weiter.
  if (codes) {
    return (
      <div data-testid="setup-totp">
        <NoticeLine notice={{ kind: "ok", text: "Zwei-Faktor-Anmeldung ist aktiv." }} />
        <RecoveryCodesPanel codes={codes} username={username} onDone={() => onNext(true)} />
      </div>
    );
  }

  if (enabled) {
    return (
      <div data-testid="setup-totp">
        <NoticeLine notice={notice} />
        <p className="flex items-start gap-2 text-sm text-white/80">
          <ShieldCheck size={18} className="mt-0.5 flex-none text-emerald-400" />
          Die Zwei-Faktor-Anmeldung ist schon eingerichtet. Neue Wiederherstellungs-Codes kannst du jederzeit unter Einstellungen → Mein Konto
          erzeugen.
        </p>
        <StepNav onBack={onBack}>
          <Button variant="primary" onClick={() => onNext(true)}>Weiter</Button>
        </StepNav>
      </div>
    );
  }

  return (
    <div data-testid="setup-totp">
      <p className="text-sm text-white/70">
        Mit der Zwei-Faktor-Anmeldung fragt Nodvard Deck bei jeder Anmeldung zusätzlich nach einem Code aus einer App auf deinem Handy
        (einer „Authenticator-App“). So bleibt dein Konto sicher, auch wenn jemand dein Passwort kennt. Das ist freiwillig und lässt sich
        später unter Einstellungen → Mein Konto nachholen.
      </p>
      <NoticeLine notice={notice} />
      {setup ? (
        <TotpEnroll setup={setup} busy={busy} onConfirm={(digits) => void confirm(digits)} onCancel={() => setSetup(null)} />
      ) : asking ? (
        <TotpPasswordForm busy={busy} onSubmit={(password) => void start(password)} onCancel={() => { setAsking(false); setNotice(null); }} />
      ) : (
        <StepNav onBack={onBack}>
          <Button onClick={() => onNext(false)}>Überspringen</Button>
          <Button variant="primary" onClick={() => setAsking(true)}>Jetzt einrichten</Button>
        </StepNav>
      )}
    </div>
  );
}
