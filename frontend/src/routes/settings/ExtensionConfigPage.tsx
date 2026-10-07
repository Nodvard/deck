/**
 * Einstellungen -> Erweiterungen -> <Erweiterung>: Formular aus dem Schema der
 * Erweiterung, darunter die Verbindungskarte: Zugangsdaten (Tokens, Passwoerter), die nur
 * gesetzt, ersetzt oder entfernt, nie wieder angezeigt werden, und der Verbindungstest.
 */
import { AlertTriangle, ArrowLeft, CheckCircle2, KeyRound, Lock, RotateCcw, Send, XCircle, Zap } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { Icon } from "../../components/Icon";
import { api } from "../../lib/api";
import { sameTarget } from "../../lib/targetAddress";
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
  /** Nur nach dem Speichern: Geheimnisse, die wegen einer geänderten Adresse gelöscht wurden. */
  secrets_cleared?: string[];
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

/** Nur das, was in diesem Formular geändert wurde: Gespeichertes, das hier unverändert blieb
 * (auch in einem anderen Tab oder von jemand anderem inzwischen geändert), wird nicht überschrieben.
 * Ein entferntes Feld geht als `null` (der Server nimmt den Wert dann heraus). */
function changedValues(schema: JsonSchema | null, saved: Record<string, unknown>, current: Record<string, unknown>) {
  const out: Record<string, unknown> = {};
  for (const key of new Set([...Object.keys(saved), ...Object.keys(current)])) {
    if (schema?.properties?.[key]?.["x-hidden"]) continue;
    if (JSON.stringify(saved[key]) === JSON.stringify(current[key])) continue;
    out[key] = current[key] === undefined ? null : current[key];
  }
  return out;
}

interface SecretSpec {
  label: string;
  per_item?: string;
  "x-secret-bound-to"?: string[];
}

function pathValue(values: unknown, path: string): unknown {
  let cur = values;
  for (const part of path.split(".")) {
    if (cur === null || typeof cur !== "object" || Array.isArray(cur)) return undefined;
    cur = (cur as Record<string, unknown>)[part];
  }
  return cur;
}

function pathDefault(node: JsonSchema | undefined, path: string): unknown {
  let cur = node;
  for (const part of path.split(".")) cur = cur?.properties?.[part];
  return cur?.default;
}

function isEmptyTarget(value: unknown): boolean {
  return value === undefined || value === null || value === "" || (typeof value === "object" && Object.keys(value as object).length === 0);
}

/** Das Ziel hinter einem Pfad: der Wert, sonst der Standardwert aus dem Schema; leer ist immer `""`. */
function targetAt(node: JsonSchema | undefined, values: unknown, path: string): unknown {
  const value = pathValue(values, path) ?? pathDefault(node, path);
  return isEmptyTarget(value) ? "" : value;
}

/** Jede Änderung eines gebundenen Felds zählt, auch eine erste Adresse (leer -> Wert): dieselbe Regel wie im Backend. */
function targetMoved(node: JsonSchema | undefined, path: string, before: unknown, after: unknown): boolean {
  return !sameTarget(targetAt(node, before, path), targetAt(node, after, path));
}

/** Welche Zugangsdaten der Server beim Speichern dieses Formulars löschen würde, weil sich die
 * Adresse geändert hat, zu der sie gehören (gleiche Regel wie im Backend). Wer in diesem Zustand
 * erst das neue Token einträgt und dann speichert, verlöre es sofort wieder. */
function pendingSecretClears(schema: (JsonSchema & { "x-secrets"?: unknown }) | null, saved: Record<string, unknown>, current: Record<string, unknown>): Set<string> {
  const out = new Set<string>();
  const specs = Array.isArray(schema?.["x-secrets"]) ? (schema["x-secrets"] as SecretSpec[]) : [];
  for (const spec of specs) {
    const bound = (spec["x-secret-bound-to"] ?? []).filter((p) => typeof p === "string" && p);
    if (bound.length === 0) continue;
    if (spec.per_item) {
      const itemSchema = schema?.properties?.[spec.per_item]?.items;
      const items = (v: unknown) => (Array.isArray(v) ? (v as Record<string, unknown>[]) : []).filter((i) => i && typeof i === "object");
      const nameOf = (i: Record<string, unknown>) => String(i.name ?? "").trim();
      const now = new Map(items(current[spec.per_item]).map((i) => [nameOf(i), i] as const));
      for (const item of items(saved[spec.per_item])) {
        const name = nameOf(item);
        if (!name) continue;
        const after = now.get(name);
        if (after === undefined || bound.some((p) => targetMoved(itemSchema, p, item, after))) out.add(spec.label.replace("{name}", name));
      }
    } else if (bound.some((p) => targetMoved(schema ?? undefined, p, saved, current))) {
      out.add(spec.label);
    }
  }
  return out;
}

/** Zugangsdaten, die an Felder gebunden sind, die gespeichert noch alle leer sind: Sie gehören zu keinem
 * Server, der Server nimmt sie erst an, wenn die Adresse gespeichert ist (409). */
function secretsWithoutTarget(schema: (JsonSchema & { "x-secrets"?: unknown }) | null, saved: Record<string, unknown>): Set<string> {
  const out = new Set<string>();
  const specs = Array.isArray(schema?.["x-secrets"]) ? (schema["x-secrets"] as SecretSpec[]) : [];
  for (const spec of specs) {
    const bound = (spec["x-secret-bound-to"] ?? []).filter((p) => typeof p === "string" && p);
    if (bound.length === 0) continue;
    const missing = (node: JsonSchema | undefined, values: unknown) => bound.every((p) => sameTarget(targetAt(node, values, p), ""));
    if (spec.per_item) {
      const itemSchema = schema?.properties?.[spec.per_item]?.items;
      const list = saved[spec.per_item];
      for (const item of Array.isArray(list) ? (list as Record<string, unknown>[]) : []) {
        const name = item && typeof item === "object" ? String(item.name ?? "").trim() : "";
        if (name && missing(itemSchema, item)) out.add(spec.label.replace("{name}", name));
      }
    } else if (missing(schema ?? undefined, saved)) {
      out.add(spec.label);
    }
  }
  return out;
}

export function ExtensionConfigPage(): JSX.Element {
  const { extId = "" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [info, setInfo] = useState<ExtInfo | null>(null);
  const [data, setData] = useState<ExtSettings | null>(null);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  // Die Erweiterung, deren Seite gerade offen ist (`null`: Seite verlassen). Eine Antwort, die erst nach dem Verlassen
  // oder nach dem Wechsel auf eine andere Erweiterung ankommt, ändert nichts mehr und leitet vor allem nicht mehr um.
  const openExt = useRef<string | null>(null);
  const stillOpen = (id: string) => openExt.current === id;
  // Die Adresse von jetzt, nicht die vom Zeitpunkt der Anfrage (Abfrage und Anker für die Weiterleitung).
  const here = useRef(location);
  here.current = location;

  const refreshInfo = () => {
    api.get<ExtInfo>(`/extensions/${extId}`)
      .then((loaded) => {
        if (!stillOpen(extId)) return;
        // Die Adresse nennt eine frühere Kennung der Erweiterung (Lesezeichen, alter Link): die Seite lebt unter der
        // heutigen. Ersetzend, damit „Zurück“ nicht wieder auf die alte Adresse führt; alle Aufrufe darunter laufen
        // dann mit der heutigen Kennung.
        if (loaded.id && loaded.id !== extId) {
          const { search, hash } = here.current;
          navigate({ pathname: `/settings/extensions/${encodeURIComponent(loaded.id)}`, search, hash }, { replace: true });
        } else {
          setInfo(loaded);
        }
      })
      .catch(() => {});
  };

  useEffect(() => {
    openExt.current = extId;
    refreshInfo();
    api.get<ExtSettings>(`/extensions/${extId}/settings`)
      .then((d) => { if (stillOpen(extId)) { setData(d); setValues(d.values); } })
      .catch((err: unknown) => { if (stillOpen(extId)) setNotice({ kind: "error", text: errorText(err) }); });
    return () => { openExt.current = null; };
  }, [extId]);

  const dirty = data !== null && JSON.stringify(values) !== JSON.stringify(data.values);
  const formErrors = data?.schema?.properties ? validateValues(data.schema, values) : [];
  const pendingClears = data ? pendingSecretClears(data.schema, data.values, values) : new Set<string>();
  const noTarget = data ? secretsWithoutTarget(data.schema, data.values) : new Set<string>();
  const pendingTitles = (data?.secrets ?? [])
    .filter((slot) => pendingClears.has(slot.label) && slot.is_set)
    .map((slot) => slot.title + (slot.item ? ` (${slot.item})` : ""));

  async function save() {
    setBusy(true);
    setNotice(null);
    try {
      const d = await api.put<ExtSettings>(`/extensions/${extId}/settings`, {
        values: changedValues(data?.schema ?? null, data?.values ?? {}, values),
      });
      if (!stillOpen(extId)) return;
      setData(d);
      setValues(d.values);
      const cleared = (d.secrets_cleared ?? []).map((label) => {
        const slot = d.secrets.find((x) => x.label === label);
        return slot ? slot.title + (slot.item ? ` (${slot.item})` : "") : label;
      });
      setNotice({
        kind: "ok",
        text: cleared.length > 0
          ? `Einstellungen gespeichert. Weil sich die Adresse geändert hat, wurden diese Zugangsdaten gelöscht: ${cleared.join(", ")}. Bitte neu eintragen.`
          : "Einstellungen gespeichert.",
      });
      refreshInfo();
    } catch (err) {
      if (stillOpen(extId)) setNotice({ kind: "error", text: errorText(err) });
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
      {pendingTitles.length > 0 && (
        <p role="status" className="mb-4 flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
          <AlertTriangle size={16} className="mt-0.5 flex-none" />
          <span>Du hast eine Adresse geändert. Beim Speichern werden diese Zugangsdaten gelöscht, danach trägst du sie neu ein: {pendingTitles.join(", ")}.</span>
        </p>
      )}
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
            pendingClears={pendingClears}
            noTarget={noTarget}
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
  extId, slots, schema, state, dirty, pendingClears, noTarget, lastTest, onSecretChanged, onTested,
}: {
  extId: string;
  slots: SecretSlot[];
  schema: JsonSchema;
  state: string;
  dirty: boolean;
  pendingClears: Set<string>;
  noTarget: Set<string>;
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
          {slots.map((slot) => (
            <SecretRow
              key={slot.label}
              extId={extId}
              slot={slot}
              lockedHint={pendingClears.has(slot.label) ? LOCKED_HINT : noTarget.has(slot.label) ? NO_TARGET_HINT : null}
              onChanged={onSecretChanged}
            />
          ))}
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

const LOCKED_HINT = "Erst die Einstellungen mit der neuen Adresse speichern, dann hier eintragen. Sonst wird der Wert beim Speichern gleich wieder gelöscht.";
const NO_TARGET_HINT = "Erst die Adresse eintragen und die Einstellungen speichern, dann hier die Zugangsdaten hinterlegen.";

function SecretRow({ extId, slot, lockedHint, onChanged }: { extId: string; slot: SecretSlot; lockedHint: string | null; onChanged: (label: string, isSet: boolean) => void }) {
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
      {editing && lockedHint && <p className="mt-2 pl-6 text-xs text-amber-200/80">{lockedHint}</p>}
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
          <Button
            variant="primary"
            busy={busy === "save"}
            disabled={!value || lockedHint !== null}
            title={lockedHint ?? undefined}
            onClick={() => void save()}
          ><KeyRound size={14} /> {slot.is_set ? "Ersetzen" : "Speichern"}</Button>
          {slot.is_set && <Button variant="ghost" onClick={() => { setEditing(false); setValue(""); }}>Abbrechen</Button>}
        </div>
      )}
    </div>
  );
}
