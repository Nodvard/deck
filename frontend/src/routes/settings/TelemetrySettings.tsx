import { useEffect, useRef, useState } from "react";

import { apiFetch } from "../../lib/api";
import { useAuthStore } from "../../state/auth";
import { Button, Card, NoticeLine, PageHeader, type Notice } from "./ui";

type Choices = { basis: boolean; nutzung: boolean; fehler: boolean };
type Entry = { at: string; operation: string; outcome: string; report: Record<string, unknown> };
type TelemetryState = {
  choices: Choices;
  blocked: string | null;
  preview: Record<string, unknown> | null;
  preview_error?: string | null;
  history: Entry[];
  next_send_at: string | null;
  pending_deletions: number;
};

const OFF: Choices = { basis: false, nutzung: false, fehler: false };
const BLOCKED: Record<string, string> = {
  admin: "Der Administrator hat Nutzungsdaten mit NODVARD_TELEMETRIE=aus gesperrt.",
  privacy: "Das Teilen bleibt gesperrt, bis die Datenschutzerklärung veröffentlicht und die Freigabe konfiguriert ist.",
  endpoint: "Es ist noch keine gültige Empfängeradresse konfiguriert. Es wird nichts gesendet.",
};
const RESULTS: Record<string, string> = {
  sent: "Bericht gesendet.", failed: "Der Versand ist fehlgeschlagen. Ein neuer Versuch ist frühestens nach 24 Stunden möglich.",
  disabled: "Es wird nichts gesendet. Das Teilen ist ausgeschaltet oder gesperrt.",
  rate_limited: "Seit dem letzten Sendeversuch sind noch keine 24 Stunden vergangen.",
  deleted: "Löschwunsch abgeschlossen. Neue Kennung angelegt, alle Stufen ausgeschaltet.",
  delete_pending: "Neue Kennung angelegt, alle Stufen ausgeschaltet. Mindestens ein Löschwunsch ist noch offen. Bitte später erneut auf Löschen klicken.",
  unsupported_arch: "Für diese Architektur gibt es im Datenformat keine zulässige Angabe. Es wurde nichts gesendet.",
};
const OUTCOMES: Record<string, string> = { sent: "Gesendet", failed: "Fehlgeschlagen", sending: "Sendeversuch begonnen", deleted: "Löschwunsch bestätigt" };

export function TelemetrySettings({ onNext, onBack }: { onNext?: () => void; onBack?: () => void }): JSX.Element {
  const owner = useAuthStore((s) => s.user?.is_owner === true);
  const [data, setData] = useState<TelemetryState | null>(null);
  const [draft, setDraft] = useState<Choices>(OFF);
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const request = useRef(0);

  async function load() {
    setLoadError(null);
    try {
      const value = await apiFetch<TelemetryState>("/telemetry");
      setData(value);
      setDraft(value.choices);
      setPreview(value.preview);
      if (value.preview_error) setNotice({ kind: "error", text: RESULTS[value.preview_error] ?? value.preview_error });
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err));
    }
  }

  useEffect(() => { if (owner) void load(); }, [owner]);

  async function change(key: keyof Choices, checked: boolean) {
    const next = { ...draft, [key]: checked };
    const sequence = ++request.current;
    setDraft(next);
    setPreview(null);
    setPreviewing(true);
    setNotice(null);
    try {
      const value = await apiFetch<TelemetryState>("/telemetry/preview", { method: "POST", body: JSON.stringify(next) });
      if (sequence === request.current) {
        setPreview(value.preview);
        if (value.preview_error) setNotice({ kind: "error", text: RESULTS[value.preview_error] ?? value.preview_error });
      }
    } catch (err) {
      if (sequence === request.current) setNotice({ kind: "error", text: err instanceof Error ? err.message : String(err) });
    } finally {
      if (sequence === request.current) setPreviewing(false);
    }
  }

  async function save(choices = draft, advance = false) {
    setBusy(true);
    setNotice(null);
    try {
      const value = await apiFetch<TelemetryState>("/telemetry", { method: "PUT", body: JSON.stringify(choices) });
      setData(value);
      setDraft(value.choices);
      setPreview(value.preview);
      setNotice({ kind: "ok", text: "Auswahl gespeichert." });
      if (advance) onNext?.();
    } catch (err) {
      setNotice({ kind: "error", text: err instanceof Error ? err.message : String(err) });
    } finally { setBusy(false); }
  }

  async function act(method: "POST" | "DELETE") {
    setBusy(true);
    setNotice(null);
    try {
      const value = await apiFetch<{ result: string }>(method === "POST" ? "/telemetry/send" : "/telemetry", { method });
      setNotice({ kind: ["sent", "deleted"].includes(value.result) ? "ok" : "error", text: RESULTS[value.result] ?? value.result });
      await load();
    } catch (err) {
      setNotice({ kind: "error", text: err instanceof Error ? err.message : String(err) });
    } finally { setBusy(false); }
  }

  if (!owner) return <p>Nur der Inhaber kann Nutzungsdaten einsehen und ändern.</p>;
  if (loadError) return <div role="alert"><p>Nutzungsdaten konnten nicht geladen werden: {loadError}</p><Button onClick={() => void load()}>Erneut versuchen</Button>{onNext && <Button onClick={onNext}>Weiter ohne Teilen</Button>}</div>;
  if (!data) return <p>Lade Nutzungsdaten …</p>;
  const dirty = JSON.stringify(draft) !== JSON.stringify(data.choices);
  const selected = Object.values(draft).some(Boolean);
  const limited = !!data.next_send_at && new Date(data.next_send_at).getTime() > Date.now();

  return <div data-testid="telemetry-settings">
    <PageHeader title="Nutzungsdaten" description="Freiwillig, standardmäßig aus. Jede Stufe lässt sich einzeln wählen und jederzeit ausschalten." />
    <NoticeLine notice={notice} />
    {data.blocked && <p role="status" className="mb-4 text-sm">{BLOCKED[data.blocked]}</p>}
    <Card title="Nutzungsdaten teilen">
      <div className="flex flex-col gap-4">
        {([
          ["basis", "Stufe 1: Basis", "Version, Produkt, Installationsart, Architektur und Sprache."],
          ["nutzung", "Stufe 2: Nutzung", "Mitgelieferte aktive Module, Anzahl fremder Module als Klasse, Server- und Benutzerklasse, Zwei-Faktor und automatische Deck-Sicherungen."],
          ["fehler", "Stufe 3: Fehler", "Zähler für feste Fehlerarten, höchstens 99 je Art. Keine Fehlertexte."],
        ] as const).map(([key, label, description]) => <label key={key} className="flex items-start gap-3 text-sm">
          <input type="checkbox" role="switch" checked={draft[key]} disabled={busy || (!!data.blocked && !draft[key])}
            onChange={(event) => void change(key, event.target.checked)} aria-label={label} />
          <span><strong className="block">{label}</strong><span className="opacity-60">{description}</span></span>
        </label>)}
        <p className="text-xs opacity-60">Jeder Bericht enthält eine zufällige lokale Kennung. Gewählte Stufen werden als eigene Blöcke gesendet. Keine Namen, Hostnamen, IP-Adressen, Pfade oder Inhalte im JSON. Beim Verbindungsaufbau sieht der Empfänger technisch deine öffentliche IP-Adresse.</p>
        <p className="text-xs opacity-60">Höchstens ein Sendeversuch je 24 Stunden. Ohne Empfängeradresse oder ohne Zustimmung wird nichts gesendet. Fehler führen zu keiner sofortigen Wiederholung.</p>
        <details open>
          <summary className="cursor-pointer text-sm font-medium">Das wird gesendet</summary>
          {previewing ? <p role="status">Vorschau wird geladen …</p> : preview && <pre aria-label="JSON-Vorschau" className="mt-2 overflow-auto rounded bg-black/25 p-3 text-xs">{JSON.stringify(preview, null, 2)}</pre>}
          {!selected && <p className="mt-2 text-xs">Alle Stufen sind aus. Die angezeigte Kennung bleibt lokal.</p>}
        </details>
        <div className="flex flex-wrap gap-2">
          {onBack && <Button disabled={busy} onClick={onBack}>Zurück</Button>}
          {onNext ? <>
            <Button className="flex-1" busy={busy} onClick={() => void save(OFF, true)}>Weiter ohne Teilen</Button>
            <Button className="flex-1" busy={busy} disabled={!selected || !!data.blocked || previewing || !preview} onClick={() => void save(draft, true)}>Teilen</Button>
          </> : <>
            <Button busy={busy} disabled={!dirty || previewing || !preview} onClick={() => void save()}>Auswahl speichern</Button>
            <Button busy={busy} disabled={dirty || !selected || !!data.blocked || limited || previewing || !preview} onClick={() => void act("POST")}>Jetzt senden</Button>
          </>}
        </div>
        {!onNext && data.next_send_at && <p className="text-xs opacity-60">Nächster Sendeversuch frühestens: {new Date(data.next_send_at).toLocaleString()}</p>}
      </div>
    </Card>
    {!onNext && <>
      <Card title="Meine Daten löschen">
        <p className="mb-3 text-sm">Fordert die Löschung für die bisherige Kennung an, erzeugt eine neue Kennung und schaltet alle Stufen aus. Offene Löschwünsche bleiben für einen erneuten Versuch lokal erhalten.</p>
        {data.pending_deletions > 0 && <p className="mb-3 text-sm">Offene Löschwünsche: {data.pending_deletions}</p>}
        <Button variant="danger" busy={busy} onClick={() => void act("DELETE")}>Meine Daten löschen</Button>
      </Card>
      <Card title="Letzte Sendungen" description="Die letzten 30 Sendeversuche und Löschwünsche, nur lokal gespeichert.">
        {data.history.length === 0 ? <p className="text-sm">Noch keine Sendungen.</p> : <ol className="flex flex-col gap-3">
          {data.history.map((entry, i) => <li key={`${entry.at}-${i}`}><details>
            <summary className="cursor-pointer text-sm">{new Date(entry.at).toLocaleString()}: {OUTCOMES[entry.outcome] ?? entry.outcome}</summary>
            <pre className="mt-2 overflow-auto text-xs">{JSON.stringify(entry.report, null, 2)}</pre>
          </details></li>)}
        </ol>}
      </Card>
    </>}
  </div>;
}
