/**
 * Einstellungen -> Erweiterungen -> <Erweiterung>: Formular aus dem Schema der
 * Erweiterung, darunter die Verbindungskarte: Zugangsdaten (Tokens, Passwoerter), die nur
 * gesetzt, ersetzt oder entfernt, nie wieder angezeigt werden, und der Verbindungstest.
 */
import { AlertTriangle, ArrowLeft, CheckCircle2, KeyRound, Lock, RotateCcw, Send, XCircle, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Icon } from "../../components/Icon";
import { api } from "../../lib/api";
import { confirmDialog } from "../../state/dialogs";
import { SchemaFields, validateValues, type JsonSchema } from "./SchemaForm";
import { Badge, Button, Card, ExtensionStateBadge, NoticeLine, errorText, inputClass, type Notice } from "./ui";

interface SecretSlot {
  label: string;
  title: string;
  description: string | null;
  item: string | null;
  is_set: boolean;
  optional?: boolean;
}

interface TestResultData {
  ok: boolean;
  message: string;
  details?: { name: string; ok: boolean; message: string }[] | null;
}

interface ExtSettings {
  schema: (JsonSchema & { "x-secrets"?: unknown }) | null;
  values: Record<string, unknown>;
  secrets: SecretSlot[];
}

interface ExtInfo {
  id: string;
  name: string | null;
  description: string | null;
  icon: string | null;
  state: string;
  version: string;
  needs_setup?: boolean;
  setup_reasons?: string[];
  last_test?: { ok: boolean; message: string; at?: string } | null;
}

export function ExtensionConfigPage(): JSX.Element {
  const { extId = "" } = useParams();
  const [info, setInfo] = useState<ExtInfo | null>(null);
  const [data, setData] = useState<ExtSettings | null>(null);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  const refreshInfo = () => { api.get<ExtInfo>(`/extensions/${extId}`).then(setInfo).catch(() => {}); };

  useEffect(() => {
    refreshInfo();
    api.get<ExtSettings>(`/extensions/${extId}/settings`)
      .then((d) => { setData(d); setValues(d.values); })
      .catch((err: unknown) => setNotice({ kind: "error", text: errorText(err) }));
  }, [extId]);

  const dirty = data !== null && JSON.stringify(values) !== JSON.stringify(data.values);
  const formErrors = data?.schema?.properties ? validateValues(data.schema, values) : [];

  async function save() {
    setBusy(true);
    setNotice(null);
    try {
      const d = await api.put<ExtSettings>(`/extensions/${extId}/settings`, { values });
      setData(d);
      setValues(d.values);
      setNotice({ kind: "ok", text: "Einstellungen gespeichert." });
      refreshInfo();
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  const name = info?.name ?? extId;

  return (
    <div>
      <Link to="/settings/extensions" className="mb-4 inline-flex items-center gap-1.5 text-xs text-white/50 hover:text-white">
        <ArrowLeft size={13} /> Alle Erweiterungen
      </Link>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <span className="grid h-11 w-11 flex-none place-items-center rounded-xl bg-white/[0.06] text-[var(--color-accent)]">
            <Icon name={info?.icon} size={20} />
          </span>
          <div>
            <h2 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
              {name}
              {info && <ExtensionStateBadge state={info.state} />}
            </h2>
            {info?.description && <p className="mt-1 max-w-2xl text-sm text-white/55">{info.description}</p>}
          </div>
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" disabled={!dirty || busy} onClick={() => data && setValues(data.values)}><RotateCcw size={14} /> Verwerfen</Button>
          <Button variant="primary" busy={busy} disabled={!dirty || formErrors.length > 0} title={formErrors.length > 0 ? "Erst die markierten Felder korrigieren." : undefined} onClick={() => void save()}>Speichern</Button>
        </div>
      </div>
      <NoticeLine notice={notice} />
      {info?.needs_setup && info.state === "enabled" && <SetupBanner reasons={info.setup_reasons ?? []} />}
      {formErrors.length > 0 && (
        <p role="alert" className="mb-4 flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200">
          <AlertTriangle size={16} className="mt-0.5 flex-none" />
          <span>Zum Speichern bitte korrigieren: {formErrors.join(" · ")}</span>
        </p>
      )}

      {!data ? (
        !notice && <p className="text-sm text-white/50">Lade …</p>
      ) : !data.schema?.properties ? (
        <Card><p className="text-sm text-white/55">Diese Erweiterung hat keine Einstellungen.</p></Card>
      ) : (
        <>
          <Card title="Einstellungen">
            <SchemaFields schema={data.schema} values={values} onChange={setValues} />
          </Card>
          <ConnectionCard
            extId={extId}
            slots={data.secrets}
            schema={data.schema}
            state={info?.state ?? "enabled"}
            dirty={dirty}
            lastTest={info?.last_test ?? null}
            onSecretChanged={(label, isSet) => {
              setData({ ...data, secrets: data.secrets.map((s) => (s.label === label ? { ...s, is_set: isSet } : s)) });
              refreshInfo();
            }}
            onTested={refreshInfo}
          />
          {dirty && (data.schema as { "x-secrets"?: { per_item?: string }[] })["x-secrets"]?.some((s) => s.per_item) && (
            <p className="text-xs text-white/45">Neue Einträge zuerst speichern, danach kann das zugehörige Geheimnis hinterlegt werden.</p>
          )}
        </>
      )}
    </div>
  );
}

function SetupBanner({ reasons }: { reasons: string[] }) {
  return (
    <div role="status" className="mb-5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
      <p className="flex items-center gap-2 font-medium"><AlertTriangle size={16} /> Einrichtung nötig</p>
      {reasons.length > 0 && (
        <ul className="mt-1.5 list-disc space-y-0.5 pl-6 text-amber-100/85">
          {reasons.map((r) => <li key={r}>{r}</li>)}
        </ul>
      )}
    </div>
  );
}

function ConnectionCard({
  extId, slots, schema, state, dirty, lastTest, onSecretChanged, onTested,
}: {
  extId: string;
  slots: SecretSlot[];
  schema: JsonSchema;
  state: string;
  dirty: boolean;
  lastTest: { ok: boolean; message: string } | null;
  onSecretChanged: (label: string, isSet: boolean) => void;
  onTested: () => void;
}) {
  const canSendMessage = schema["x-test-message"] === true;
  const [running, setRunning] = useState<"connection" | "message" | null>(null);
  const [result, setResult] = useState<TestResultData | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const shown = result ?? (lastTest && !failure ? { ok: lastTest.ok, message: lastTest.message, details: null, earlier: true } : null);
  const blocked = state !== "enabled" ? "Erst die Erweiterung einschalten, dann lässt sich testen." : dirty ? "Erst speichern, dann testen – getestet wird der gespeicherte Stand." : null;

  async function run(mode: "connection" | "message") {
    setRunning(mode);
    setResult(null);
    setFailure(null);
    try {
      setResult(await api.post<TestResultData>(`/extensions/${extId}/test`, { mode }));
      onTested();
    } catch (err) {
      setFailure(errorText(err));
    } finally {
      setRunning(null);
    }
  }

  return (
    <Card
      title="Verbindung"
      description={slots.length > 0 ? "Zugangsdaten werden verschlüsselt gespeichert und nie wieder angezeigt. Zum Ändern einfach ersetzen." : "Prüft, ob die gespeicherten Einstellungen funktionieren."}
    >
      {slots.length > 0 && (
        <div className="divide-y divide-white/[0.06]">
          {slots.map((slot) => <SecretRow key={slot.label} extId={extId} slot={slot} onChanged={onSecretChanged} />)}
        </div>
      )}
      <div className={slots.length > 0 ? "mt-4 border-t border-white/[0.06] pt-4" : ""}>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" busy={running === "connection"} disabled={blocked !== null || running !== null} onClick={() => void run("connection")}>
            <Zap size={14} /> Verbindung testen
          </Button>
          {canSendMessage && (
            <Button busy={running === "message"} disabled={blocked !== null || running !== null} onClick={() => void run("message")}>
              <Send size={14} /> Testnachricht senden
            </Button>
          )}
          {blocked && <span className="text-xs text-white/50">{blocked}</span>}
        </div>
        {failure && <TestResultBox ok={false} message={failure} />}
        {shown && !failure && (
          <TestResultBox ok={shown.ok} message={shown.message} details={shown.details ?? undefined} earlier={"earlier" in shown} />
        )}
      </div>
    </Card>
  );
}

function TestResultBox({ ok, message, details, earlier }: { ok: boolean; message: string; details?: { name: string; ok: boolean; message: string }[]; earlier?: boolean }) {
  return (
    <div
      role={ok ? "status" : "alert"}
      data-testid="test-result"
      className={`mt-3 rounded-lg border px-3 py-2.5 text-sm ${ok ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-100" : "border-red-500/30 bg-red-500/10 text-red-100"}`}
    >
      <p className="flex items-start gap-2">
        {ok ? <CheckCircle2 size={16} className="mt-0.5 flex-none" /> : <XCircle size={16} className="mt-0.5 flex-none" />}
        <span>{earlier && <span className="mr-1 text-xs opacity-70">Letzter Test:</span>}{message}</span>
      </p>
      {details && details.length > 0 && (
        <ul className="mt-2 space-y-1 border-t border-white/10 pt-2 text-[13px]">
          {details.map((d) => (
            <li key={d.name} className="flex items-start gap-2">
              {d.ok ? <CheckCircle2 size={13} className="mt-0.5 flex-none text-emerald-300" /> : <XCircle size={13} className="mt-0.5 flex-none text-red-300" />}
              <span><span className="font-medium">{d.name}:</span> {d.message}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SecretRow({ extId, slot, onChanged }: { extId: string; slot: SecretSlot; onChanged: (label: string, isSet: boolean) => void }) {
  const [value, setValue] = useState("");
  const [editing, setEditing] = useState(!slot.is_set);
  const [busy, setBusy] = useState<"save" | "remove" | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const title = slot.item ? `${slot.title} – ${slot.item}` : slot.title;

  async function save() {
    const replacing = slot.is_set;
    setBusy("save");
    setNotice(null);
    try {
      await api.put(`/extensions/${extId}/secrets`, { label: slot.label, value });
      setValue("");
      setEditing(false);
      onChanged(slot.label, true);
      setNotice({ kind: "ok", text: replacing ? "Ersetzt." : "Gespeichert." });
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    const ok = await confirmDialog(
      `„${title}“ wirklich entfernen? Die Erweiterung kann sich danach nicht mehr damit anmelden, bis ein neuer Wert eingetragen ist.`,
      { danger: true, confirmLabel: "Entfernen" },
    );
    if (!ok) return;
    setBusy("remove");
    setNotice(null);
    try {
      await api.delete(`/extensions/${extId}/secrets?label=${encodeURIComponent(slot.label)}`);
      setValue("");
      setEditing(true);
      onChanged(slot.label, false);
      setNotice({ kind: "ok", text: "Entfernt." });
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="py-3 first:pt-0 last:pb-0" data-testid={`secret-${slot.label}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-[14rem] flex-1 items-start gap-2.5">
          <Lock size={15} className="mt-0.5 text-white/40" />
          <div>
            <p className="text-sm">{title}</p>
            {slot.description && <p className="text-xs text-white/45">{slot.description}</p>}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {slot.is_set ? <Badge tone="good">Hinterlegt</Badge> : slot.optional ? <Badge>Nicht gesetzt (optional)</Badge> : <Badge tone="warn">Fehlt</Badge>}
          {slot.is_set && !editing && (
            <>
              <Button onClick={() => setEditing(true)}>Ersetzen</Button>
              <Button variant="danger" busy={busy === "remove"} onClick={() => void remove()}>Entfernen</Button>
            </>
          )}
        </div>
      </div>
      <NoticeLine notice={notice} />
      {editing && (
        <div className="mt-2 flex gap-2 pl-6">
          <input
            type="password"
            autoComplete="new-password"
            aria-label={title}
            value={value}
            placeholder={slot.is_set ? "Neuer Wert" : "Wert eingeben"}
            onChange={(e) => setValue(e.target.value)}
            className={inputClass}
          />
          <Button variant="primary" busy={busy === "save"} disabled={!value} onClick={() => void save()}><KeyRound size={14} /> {slot.is_set ? "Ersetzen" : "Speichern"}</Button>
          {slot.is_set && <Button variant="ghost" onClick={() => { setEditing(false); setValue(""); }}>Abbrechen</Button>}
        </div>
      )}
    </div>
  );
}
