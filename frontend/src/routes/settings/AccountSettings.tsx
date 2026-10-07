/**
 * Mein Konto: Profil (Anzeigename, E-Mail), Passwort aendern und
 * Zwei-Faktor-Anmeldung per Authenticator-App einrichten oder abschalten.
 */
import { KeyRound, ShieldCheck, ShieldOff } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "../../lib/api";
import { useAuthStore } from "../../state/auth";
import { RecoveryCodesPanel } from "./RecoveryCodesPanel";
import { SecondFactorField, isTotpRejected } from "./SecondFactorField";
import { TotpEnroll, type TotpSetup } from "./TotpEnroll";
import { TotpPasswordForm } from "./TotpPasswordForm";
import { Badge, Button, Card, Field, NoticeLine, PageHeader, errorText, inputClass, type Notice } from "./ui";

interface Me {
  id: string;
  username: string;
  display_name: string;
  email: string | null;
  is_owner: boolean;
  locale: string;
  permissions: string[];
  totp_enabled: boolean;
  recovery_codes_remaining?: number;
}

export function AccountSettings(): JSX.Element {
  const [me, setMe] = useState<Me | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  function reload() {
    api.get<Me>("/me").then(setMe).catch((err: unknown) => setLoadError(errorText(err)));
  }
  useEffect(reload, []);

  if (loadError) return <NoticeLine notice={{ kind: "error", text: loadError }} />;
  if (!me) return <p className="text-sm text-white/50">Lade …</p>;

  return (
    <div>
      <PageHeader title="Mein Konto" description={`Angemeldet als ${me.username}${me.is_owner ? " (Inhaber)" : ""}.`} />
      <ProfileCard me={me} onSaved={setMe} />
      <PasswordCard />
      <TotpCard enabled={me.totp_enabled} username={me.username} remaining={me.recovery_codes_remaining ?? 0} onChanged={reload} />
      <FirstStepsPreferenceCard />
    </div>
  );
}

function ProfileCard({ me, onSaved }: { me: Me; onSaved: (me: Me) => void }) {
  const [displayName, setDisplayName] = useState(me.display_name);
  const [email, setEmail] = useState(me.email ?? "");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  async function save() {
    setBusy(true);
    setNotice(null);
    try {
      const updated = await api.patch<Me>("/me", { display_name: displayName, email });
      onSaved(updated);
      const current = useAuthStore.getState().user;
      if (current) useAuthStore.setState({ user: { ...current, display_name: updated.display_name, email: updated.email, locale: updated.locale } });
      setNotice({ kind: "ok", text: "Profil gespeichert." });
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Profil"
      description="So erscheinst du in der Oberfläche und im Protokoll."
      footer={<Button variant="primary" busy={busy} onClick={() => void save()}>Profil speichern</Button>}
    >
      <NoticeLine notice={notice} />
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Benutzername" hint="Wird zur Anmeldung verwendet und kann nicht geändert werden.">
          <input value={me.username} disabled className={inputClass} />
        </Field>
        <Field label="Anzeigename">
          <input value={displayName} maxLength={128} onChange={(e) => setDisplayName(e.target.value)} className={inputClass} />
        </Field>
        <Field label="E-Mail" hint="Optional, z. B. für Benachrichtigungen.">
          <input type="email" value={email} maxLength={255} onChange={(e) => setEmail(e.target.value)} className={inputClass} />
        </Field>
      </div>
    </Card>
  );
}

/** Die Karte „Erste Schritte“ im Cockpit nach „Ausblenden“ wieder zeigen. */
function FirstStepsPreferenceCard() {
  const [dismissed, setDismissed] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  useEffect(() => {
    api
      .get<{ first_steps_dismissed: boolean }>("/me/preferences")
      .then((p) => setDismissed(p.first_steps_dismissed))
      .catch(() => setDismissed(null));
  }, []);

  if (!dismissed) return null;

  async function show() {
    setBusy(true);
    setNotice(null);
    try {
      await api.patch("/me/preferences", { first_steps_dismissed: false });
      setDismissed(false);
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Erste Schritte"
      description="Du hast die Karte „Erste Schritte“ im Cockpit ausgeblendet. Sie zeigt, was für die Einrichtung noch fehlt."
      footer={<Button busy={busy} onClick={() => void show()}>Wieder anzeigen</Button>}
    >
      <NoticeLine notice={notice} />
    </Card>
  );
}

function PasswordCard() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  async function save() {
    setNotice(null);
    if (next.length < 8) return setNotice({ kind: "error", text: "Das neue Passwort muss mindestens 8 Zeichen haben." });
    if (next !== repeat) return setNotice({ kind: "error", text: "Die beiden neuen Passwörter stimmen nicht überein." });
    setBusy(true);
    try {
      await api.post("/me/password", { current_password: current, new_password: next });
      setCurrent("");
      setNext("");
      setRepeat("");
      setNotice({ kind: "ok", text: "Passwort geändert." });
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Passwort"
      description="Mindestens 8 Zeichen. Nimm ein Passwort, das du nirgends sonst verwendest."
      footer={<Button variant="primary" busy={busy} disabled={!current || !next} onClick={() => void save()}>Passwort ändern</Button>}
    >
      <NoticeLine notice={notice} />
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Aktuelles Passwort">
          <input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} className={inputClass} />
        </Field>
        <Field label="Neues Passwort">
          <input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} className={inputClass} />
        </Field>
        <Field label="Neues Passwort wiederholen">
          <input type="password" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} className={inputClass} />
        </Field>
      </div>
    </Card>
  );
}

const RECOVERY_TOTAL = 10;

function TotpCard({ enabled, username, remaining, onChanged }: { enabled: boolean; username: string; remaining: number; onChanged: () => void }) {
  const [setup, setSetup] = useState<TotpSetup | null>(null);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [newCodes, setNewCodes] = useState<string[] | null>(null);
  const [renewing, setRenewing] = useState(false);
  const [renewPassword, setRenewPassword] = useState("");
  const [renewCode, setRenewCode] = useState("");
  const [disabling, setDisabling] = useState(false);
  const [disablePassword, setDisablePassword] = useState("");
  const [disableCode, setDisableCode] = useState("");

  async function renew() {
    setBusy(true);
    setNotice(null);
    try {
      const res = await api.post<{ recovery_codes: string[] }>("/me/recovery-codes", { current_password: renewPassword, totp_code: renewCode.trim() });
      setNewCodes(res.recovery_codes);
      setRenewing(false);
      setRenewPassword("");
      setRenewCode("");
      onChanged();
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
      // Nur wenn der Code selbst abgelehnt wurde: Bei falschem Passwort prüft der Server ihn gar nicht erst.
      if (isTotpRejected(err)) setRenewCode("");
    } finally {
      setBusy(false);
    }
  }

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
      const res = await api.post<{ recovery_codes: string[] }>("/me/totp/confirm", { code });
      setSetup(null);
      setNewCodes(res?.recovery_codes?.length ? res.recovery_codes : null);
      setNotice({ kind: "ok", text: "Zwei-Faktor-Anmeldung ist aktiv." });
      onChanged();
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  async function disable() {
    setBusy(true);
    setNotice(null);
    try {
      await api.delete("/me/totp", { current_password: disablePassword, totp_code: disableCode.trim() });
      setNewCodes(null);
      setRenewing(false);
      setDisabling(false);
      setDisablePassword("");
      setDisableCode("");
      setNotice({ kind: "ok", text: "Zwei-Faktor-Anmeldung abgeschaltet." });
      onChanged();
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
      // Nur wenn der Code selbst abgelehnt wurde: Bei falschem Passwort prüft der Server ihn gar nicht erst, und ein
      // abgetippter Wiederherstellungs-Code soll dann stehen bleiben.
      if (isTotpRejected(err)) setDisableCode("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Zwei-Faktor-Anmeldung" description="Zusätzlich zum Passwort wird bei der Anmeldung ein Code aus einer Authenticator-App abgefragt.">
      <NoticeLine notice={notice} />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          {enabled ? <ShieldCheck className="text-emerald-400" size={22} /> : <ShieldOff className="text-white/40" size={22} />}
          <div>
            <p className="text-sm font-medium">Status</p>
            {enabled ? <Badge tone="good">Aktiv</Badge> : <Badge tone="warn">Nicht eingerichtet</Badge>}
          </div>
        </div>
        {enabled ? (
          !disabling && <Button variant="danger" busy={busy} onClick={() => setDisabling(true)}>Abschalten</Button>
        ) : (
          !setup && !asking && <Button variant="primary" busy={busy} onClick={() => setAsking(true)}><KeyRound size={14} /> Einrichten</Button>
        )}
      </div>

      {enabled && disabling && (
        <div className="mt-5 rounded-lg border border-red-500/30 bg-red-500/5 p-4" data-testid="disable-2fa">
          <p className="text-sm text-white/80">
            Zwei-Faktor-Anmeldung wirklich abschalten? Dein Konto ist dann nur noch durch das Passwort geschützt, und deine
            Wiederherstellungs-Codes werden gelöscht. Zur Sicherheit brauchst du dafür dein Passwort und einen Code.
          </p>
          <div className="mt-3 grid max-w-xl gap-3 sm:grid-cols-2">
            <Field label="Passwort zum Abschalten" hint="Dein aktuelles Passwort.">
              <input type="password" autoComplete="current-password" value={disablePassword} onChange={(e) => setDisablePassword(e.target.value)} className={inputClass} />
            </Field>
            <SecondFactorField value={disableCode} onChange={setDisableCode} />
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button variant="danger" busy={busy} disabled={!disablePassword || !disableCode.trim()} onClick={() => void disable()}>Abschalten</Button>
            <Button variant="ghost" onClick={() => { setDisabling(false); setDisablePassword(""); setDisableCode(""); }}>Abbrechen</Button>
          </div>
        </div>
      )}

      {newCodes && <RecoveryCodesPanel codes={newCodes} username={username} onDone={() => setNewCodes(null)} />}

      {enabled && !newCodes && (
        <div className="mt-5 border-t border-white/[0.06] pt-4" data-testid="recovery-status">
          <p className="text-sm font-medium">Wiederherstellungs-Codes</p>
          <p className="mt-1 text-xs text-white/55">
            {remaining > 0
              ? `Noch ${remaining} von ${RECOVERY_TOTAL} Codes übrig. Sie ersetzen bei der Anmeldung den Code aus der App, falls das Handy weg ist.`
              : "Du hast keine Wiederherstellungs-Codes mehr. Ohne Handy kämst du nicht mehr ins Konto – bitte erzeuge neue."}
          </p>
          {remaining > 0 && remaining <= 3 && <p className="mt-1 text-xs text-amber-300">Es sind nur noch wenige Codes übrig.</p>}
          {renewing ? (
            <div className="mt-3" data-testid="renew-codes">
              <p className="text-xs text-white/55">Die alten Codes werden damit sofort ungültig. Zur Sicherheit brauchst du dein Passwort und einen Code.</p>
              <div className="mt-3 grid max-w-xl gap-3 sm:grid-cols-2">
                <Field label="Passwort zur Bestätigung" hint="Dein aktuelles Passwort.">
                  <input type="password" autoComplete="current-password" value={renewPassword} onChange={(e) => setRenewPassword(e.target.value)} className={inputClass} />
                </Field>
                <SecondFactorField value={renewCode} onChange={setRenewCode} />
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button variant="primary" busy={busy} disabled={!renewPassword || !renewCode.trim()} onClick={() => void renew()}>Erzeugen</Button>
                <Button variant="ghost" onClick={() => { setRenewing(false); setRenewPassword(""); setRenewCode(""); }}>Abbrechen</Button>
              </div>
            </div>
          ) : (
            <div className="mt-3">
              <Button onClick={() => setRenewing(true)}>Neue Wiederherstellungs-Codes erzeugen</Button>
            </div>
          )}
        </div>
      )}

      {!enabled && asking && !setup && (
        <TotpPasswordForm busy={busy} onSubmit={(password) => void start(password)} onCancel={() => { setAsking(false); setNotice(null); }} />
      )}

      {setup && (
        <TotpEnroll setup={setup} busy={busy} onConfirm={(digits) => void confirm(digits)} onCancel={() => setSetup(null)} />
      )}
    </Card>
  );
}
