/**
 * Die Backups-Seite -- PageSpec `component=
 * "BackupsPage"` (siehe nodvard_deck_ext_backups/__init__.py). Zeigt jeden konfigurierten
 * VZDump-Job PRO VM als eigene Zeile mit strukturiertem Status, nicht als Freitext --
 * ein fehlschlagender Job soll nicht wochenlang unbemerkt bleiben, nur weil der
 * Bericht als Prosa daherkommt.
 *
 * `retry()`s Rueckfrage laeuft nicht ueber `window.confirm()`, sondern ueber
 * `window.__nodvardDeck.confirmDialog()` (main.tsx),
 * derselbe Grund/dieselbe Loesung wie proxmox' Node-Seite (kein direkter Zugriff auf
 * state/dialogs.ts ueber den Import-Map-Shim).
 *
 * `retry()` legt ueber das Gate einen Vorschlag an. Hat der Nutzer
 * `actions.approve:<risk>`, bestaetigt die Seite den eigenen Vorschlag automatisch --
 * derselbe Ansatz wie proxmox' Node-Seite; sonst bleibt er auf der Aktionen-Seite
 * (ActionsPage.tsx im Kern) zur Freigabe stehen. `POST .../retry` liefert dafuer
 * zusaetzlich `risk` mit (siehe __init__.py).
 */
import { Fragment, useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";

import { ACTION_STATUS_LABEL, isActionRunning, RUNNING_IN_BACKGROUND, settleAction } from "../../../_shared/frontend/src/actions";
import { authedFetch, errorFromBody, errorText } from "../../../_shared/frontend/src/api";
import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { useUrlParams } from "../../../_shared/frontend/src/location";
import { deck } from "../../../_shared/frontend/src/deck";

interface ConnectionOut {
  name: string;
  base_url: string;
  token_id: string;
  tls_insecure_skip_verify: boolean;
  enabled: boolean;
  has_token: boolean;
}

interface JobOut {
  job_ref: string;
  connection: string;
  vmid: string;
  name: string;
  host_id: string | null;
  node: string | null;
  storage: string | null;
  schedule: string | null;
  enabled: boolean;
  last_status: string;
  last_run_at: number | null;
  next_run_at?: number | null;
  retention?: string | null;
  /** Nur bei `last_status: "unreachable"` -- eine Verbindung, die nicht antwortet. */
  error?: string;
}

interface InventoryEntry {
  connection: string;
  vmid: string;
  count: number;
  total_size: number;
  newest_at: number | null;
  oldest_at: number | null;
  storages: string[];
}

type Inventory = Map<string, InventoryEntry>;

interface OrphanEntry extends InventoryEntry {
  kind: string | null;
}

function formatSize(bytes: number): string {
  if (bytes >= 1e12) return `${(bytes / 1e12).toFixed(1)} TB`;
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  return `${Math.round(bytes / 1e6)} MB`;
}

/** Was tatsaechlich auf den Backup-Speichern liegt -- einmal laden, je Gast nachschlagen. */
function useInventory(): { inventory: Inventory | null; orphans: OrphanEntry[] } {
  const [inventory, setInventory] = useState<Inventory | null>(null);
  const [orphans, setOrphans] = useState<OrphanEntry[]>([]);
  useEffect(() => {
    authedFetch("/ext/backups/inventory")
      .then((res) => (res.ok ? (res.json() as Promise<{ guests: InventoryEntry[]; orphans?: OrphanEntry[] }>) : { guests: [], orphans: [] }))
      .then((body) => {
        setInventory(new Map(body.guests.map((g) => [`${g.connection}/${g.vmid}`, g])));
        setOrphans(body.orphans ?? []);
      })
      .catch(() => setInventory(new Map()));
  }, []);
  return { inventory, orphans };
}

function inventoryText(entry: InventoryEntry | undefined): string {
  if (!entry || entry.count === 0) return "keine Sicherung vorhanden";
  const newest = formatEpoch(entry.newest_at);
  return `${entry.count}× · neueste ${newest} · ${formatSize(entry.total_size)} (${entry.storages.join(", ")})`;
}

interface UnprotectedGuest {
  connection: string;
  vmid: string;
  name: string;
  host_id: string | null;
  kind: string;
  acknowledged?: boolean;
  note?: string | null;
}

/**
 * Gaeste, die KEIN Backup-Job erfasst (Proxmox' eigene Auswertung). Live beim Bau:
 * 4 von 6 -- die Seite zeigte bis dahin nur, was gesichert wird, nie, was fehlt.
 */
/** Neuer Backup-Job fuer EINEN ungesicherten Gast -- ueber das Gate, niedriges Risiko. */
function NewJobForm({ guest, defaultStorage, onDone }: { guest: UnprotectedGuest; defaultStorage: string; onDone: (message: string | null) => void }): JSX.Element {
  const unmountSignal = useUnmountSignal();
  const [schedule, setSchedule] = useState("sun 02:00");
  const [storage, setStorage] = useState(defaultStorage);
  const [keepLast, setKeepLast] = useState(3);
  const [busy, setBusy] = useState(false);

  async function create() {
    setBusy(true);
    try {
      const res = await postWithSpaceCheck(`/ext/backups/unprotected/${encodeURIComponent(guest.connection)}/${guest.vmid}/job`, {
        values: { schedule, storage, "keep-last": keepLast },
      });
      if (res === null) return;
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      const { action, approved } = await settleAction(body, { signal: unmountSignal() });
      if (approved) {
        onDone(
          action.status === "succeeded" ? action.result?.output ?? "Angelegt."
            : isActionRunning(action.status) ? RUNNING_IN_BACKGROUND
            : `Fehlgeschlagen: ${action.result?.error ?? action.status}`,
        );
      } else {
        onDone(action.status === "proposed" ? `Vorgeschlagen – Freigabe durch einen Admin nötig, siehe "Aktionen".` : ACTION_STATUS_LABEL[action.status ?? ""] ?? action.status ?? "?");
      }
    } catch (err) {
      onDone(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="my-1 flex flex-wrap items-end gap-2 p-2 text-xs panel" data-testid={`new-job-${guest.connection}-${guest.vmid}`}>
      <label className="flex flex-col gap-0.5"><span className="opacity-60">Zeitplan</span>
        <input value={schedule} onChange={(e) => setSchedule(e.target.value)} className="w-32 px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]" /></label>
      <label className="flex flex-col gap-0.5"><span className="opacity-60">Speicher</span>
        <input value={storage} onChange={(e) => setStorage(e.target.value)} className="w-32 px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]" /></label>
      <label className="flex flex-col gap-0.5"><span className="opacity-60">Letzte behalten</span>
        <input type="number" min={1} value={keepLast} onChange={(e) => setKeepLast(Number(e.target.value))} className="w-16 px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]" /></label>
      <button type="button" disabled={busy || !schedule || !storage} onClick={() => void create()} className="rounded bg-emerald-500/20 px-3 py-1 text-emerald-300 hover:bg-emerald-500/30 disabled:opacity-40">
        {busy ? "…" : "Anlegen"}
      </button>
      <button type="button" onClick={() => onDone(null)} className="rounded px-2 py-1 opacity-70 hover:opacity-100">Abbrechen</button>
    </div>
  );
}

function UnprotectedSection({ inventory, defaultStorage = "", onChanged }: { inventory: Inventory | null; defaultStorage?: string; onChanged?: () => void }): JSX.Element | null {
  const [creating, setCreating] = useState<string | null>(null);
  const [data, setData] = useState<{ guests: UnprotectedGuest[]; errors: { connection: string; error: string }[] } | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(() => {
    authedFetch("/ext/backups/unprotected")
      .then((res) => (res.ok ? (res.json() as Promise<{ guests: UnprotectedGuest[]; errors: { connection: string; error: string }[] }>) : null))
      .then(setData)
      .catch(() => setData(null));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // "Bewusst ohne Backup" ist eine Konfigurationsentscheidung -> settings.write.
  const canDecide = deck().hasPermission("settings.write");

  async function acknowledge(g: UnprotectedGuest) {
    const note = await deck().promptDialog(`${g.name} bewusst ohne Backup lassen? Kurze Begründung (optional):`);
    if (note === null) return;
    await setAcknowledged(g, { method: "PUT", body: JSON.stringify({ note }) });
  }

  async function setAcknowledged(g: UnprotectedGuest, init: RequestInit) {
    setMessage(null);
    const res = await authedFetch(`/ext/backups/unprotected/${encodeURIComponent(g.connection)}/${encodeURIComponent(g.vmid)}/acknowledged`, init);
    if (!res.ok) {
      setMessage(`Fehler: ${await errorText(res)}`);
      return;
    }
    load();
  }

  if (!data || (data.guests.length === 0 && data.errors.length === 0)) return null;
  const open = data.guests.filter((g) => !g.acknowledged);
  const accepted = data.guests.filter((g) => g.acknowledged);
  const line = (g: UnprotectedGuest) => (
    <>
      {g.name} <span className="opacity-60">· {g.kind} {g.vmid} · {g.connection}</span>
      {inventory && <span className="opacity-60"> · {inventoryText(inventory.get(`${g.connection}/${g.vmid}`))}</span>}
    </>
  );
  return (
    <section
      className={`mb-4 rounded border p-3 ${open.length > 0 ? "border-amber-500/40 bg-amber-500/5" : "border-white/10"}`}
      data-testid="unprotected"
    >
      <h3 className={`text-sm font-semibold ${open.length > 0 ? "text-amber-300" : "opacity-80"}`}>Ohne Backup-Job ({open.length})</h3>
      <p className="mb-2 text-xs opacity-70">Diese Gäste sichert kein Backup-Job in Proxmox. Geht die Disk verloren, sind sie weg.</p>
      <ul className="space-y-0.5 text-sm">
        {open.map((g) => (
          <li key={`${g.connection}-${g.vmid}`}>
            {line(g)}
            {canDecide && (
              <button type="button" onClick={() => setCreating(creating === `${g.connection}-${g.vmid}` ? null : `${g.connection}-${g.vmid}`)}
                className="ml-2 rounded bg-emerald-500/20 px-1.5 py-0.5 text-xs text-emerald-300 hover:bg-emerald-500/30">
                Job anlegen
              </button>
            )}
            {canDecide && (
              <button type="button" onClick={() => void acknowledge(g)} className="ml-2 px-1.5 py-0.5 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition">
                Bewusst so lassen
              </button>
            )}
            {creating === `${g.connection}-${g.vmid}` && (
              <NewJobForm guest={g} defaultStorage={defaultStorage} onDone={(text) => {
                setCreating(null);
                if (text) {
                  setMessage(text);
                  load();
                  onChanged?.();
                }
              }} />
            )}
          </li>
        ))}
      </ul>
      {accepted.length > 0 && (
        <div className="mt-2" data-testid="unprotected-accepted">
          <p className="text-xs font-medium opacity-60">Bewusst ohne Backup ({accepted.length}) – warnt nicht auf dem Dashboard</p>
          <ul className="space-y-0.5 text-xs opacity-70">
            {accepted.map((g) => (
              <li key={`${g.connection}-${g.vmid}`}>
                {line(g)}
                {g.note ? <span className="italic"> · „{g.note}“</span> : null}
                {canDecide && (
                  <button
                    type="button"
                    onClick={() => void setAcknowledged(g, { method: "DELETE" })}
                    className="ml-2 px-1.5 py-0.5 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition"
                  >
                    Wieder warnen
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {message && <p className="mt-1 text-xs text-red-400">{message}</p>}
      {data.errors.map((e) => (
        <p key={e.connection} className="mt-1 text-xs text-red-400">
          {e.connection}: nicht prüfbar ({e.error})
        </p>
      ))}
    </section>
  );
}

interface HistoryEntry {
  upid: string;
  node: string | null;
  status: string;
  started_at: number | null;
  finished_at: number | null;
}

const STATUS_LABEL: Record<string, string> = {
  ok: "OK", failed: "Fehlgeschlagen", running: "Läuft", unknown: "Unbekannt",
};

const STATUS_CLASS: Record<string, string> = {
  ok: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  failed: "bg-red-500/15 text-red-300 border-red-500/40",
  running: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  unknown: "bg-white/10 opacity-70",
};

function formatEpoch(seconds: number | null): string {
  if (!seconds) return "nie";
  return new Date(seconds * 1000).toLocaleString();
}

/**
 * POST mit Platz-Pruefung (space.py): passt der Gast nicht auf den Backup-Speicher,
 * antwortet der Server mit 409 + `space_warning` -- dann nachfragen und bei "trotzdem"
 * dieselbe Anfrage mit `ignore_space: true` wiederholen. `null` = abgebrochen.
 * Schutz davor, dass ein einzelnes grosses Backup den Zielspeicher vollschreibt.
 */
async function postWithSpaceCheck(path: string, payload: Record<string, unknown>): Promise<Response | null> {
  const send = (extra: Record<string, unknown>) =>
    authedFetch(path, { method: "POST", body: JSON.stringify({ ...payload, ...extra }) });
  const res = await send({});
  if (res.status !== 409) return res;
  const body = await res.clone().json().catch(() => ({}));
  if (!body.space_warning) return res;
  const ok = await deck().confirmDialog(`Speicher reicht vermutlich nicht: ${body.detail} Trotzdem fortfahren?`, {
    danger: true,
    confirmLabel: "Trotzdem",
  });
  return ok ? send({ ignore_space: true }) : null;
}

/**
 * Verbindungsverwaltung: identisches Muster wie proxmox' `ConnectionsPanel` in
 * `ProxmoxNodePage.tsx` -- backups haelt eine EIGENE, unabhaengige
 * Verbindungsliste (siehe connector.py-Docstring), deshalb hier dupliziert statt
 * geteilt (kein Code-Sharing zwischen Extension-Frontends, siehe SDK-Doku).
 */
function ConnectionsPanel(): JSX.Element {
  const [connections, setConnections] = useState<ConnectionOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [newName, setNewName] = useState("");
  const [newBaseUrl, setNewBaseUrl] = useState("");
  const [newTokenId, setNewTokenId] = useState("");
  const [newTlsInsecure, setNewTlsInsecure] = useState(false);
  const [pending, setPending] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    authedFetch("/ext/backups/connections")
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<ConnectionOut[]>;
      })
      .then(setConnections)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function addConnection(e: FormEvent) {
    e.preventDefault();
    setPending("add");
    setMessage(null);
    try {
      const res = await authedFetch("/ext/backups/connections", {
        method: "POST",
        body: JSON.stringify({
          name: newName, base_url: newBaseUrl, token_id: newTokenId, tls_insecure_skip_verify: newTlsInsecure,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      setMessage(`Verbindung "${newName}" angelegt – jetzt noch ein Token setzen.`);
      setNewName("");
      setNewBaseUrl("");
      setNewTokenId("");
      setNewTlsInsecure(false);
      setShowAddForm(false);
      load();
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPending(null);
    }
  }

  async function setToken(name: string, replacing: boolean) {
    const value = await deck().promptDialog(
      replacing ? `Neues API-Token für "${name}" (ersetzt das gespeicherte, Klartext, nur hier eingeben):` : `API-Token für "${name}" (Klartext, nur hier eingeben):`,
    );
    if (!value) return;
    setPending(`token:${name}`);
    setMessage(null);
    try {
      // Legt an ODER ersetzt. Der alte Weg (`POST /ext/backups/connections/{name}/token`) legt nur
      // an und meldete beim zweiten Mal 409.
      const res = await authedFetch(`/extensions/backups/secrets`, {
        method: "PUT", body: JSON.stringify({ label: `backups-token:${name}`, value }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(errorFromBody(body, res.status));
      }
      setMessage(`Token für "${name}" gesetzt.`);
      load();
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPending(null);
    }
  }

  async function toggleEnabled(conn: ConnectionOut) {
    setPending(`toggle:${conn.name}`);
    setMessage(null);
    try {
      const res = await authedFetch(`/ext/backups/connections/${conn.name}`, {
        method: "PUT", body: JSON.stringify({ enabled: !conn.enabled }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(errorFromBody(body, res.status));
      }
      load();
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPending(null);
    }
  }

  async function removeConnection(name: string) {
    const ok = await deck().confirmDialog(
      `Verbindung "${name}" wirklich entfernen? Das gesetzte Token wird mit gelöscht.`,
      { danger: true, confirmLabel: "Entfernen" },
    );
    if (!ok) return;
    setPending(`remove:${name}`);
    setMessage(null);
    try {
      const res = await authedFetch(`/ext/backups/connections/${name}`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) {
        const body = await res.json().catch(() => ({}));
        throw new Error(errorFromBody(body, res.status));
      }
      load();
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPending(null);
    }
  }

  if (error) return <p className="mb-4 text-xs text-red-400">Verbindungen nicht ladbar: {error}</p>;

  return (
    <details className="mb-6 p-3 panel" open={connections?.length === 0}>
      <summary className="cursor-pointer text-sm font-medium">Verbindungen verwalten</summary>
      <div className="mt-3">
        {message && <p className="mb-2 text-xs opacity-80">{message}</p>}
        {connections && connections.length === 0 && (
          <p className="mb-2 text-xs opacity-60">Noch keine Verbindung konfiguriert.</p>
        )}
        {connections && connections.length > 0 && (
          <table className="mb-3 w-full text-xs">
            <thead>
              <tr className="border-b border-white/10 text-left uppercase opacity-60">
                <th className="py-1">Name</th>
                <th className="py-1">Adresse</th>
                <th className="py-1">Zertifikat nicht prüfen</th>
                <th className="py-1">Token</th>
                <th className="py-1">Aktiv</th>
                <th className="py-1">Aktionen</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {connections.map((c) => (
                <tr key={c.name}>
                  <td className="py-1.5">{c.name}</td>
                  <td className="py-1.5 opacity-70">{c.base_url}</td>
                  <td className="py-1.5 opacity-70">{c.tls_insecure_skip_verify ? "ja" : "nein"}</td>
                  <td className="py-1.5">{c.has_token ? "gesetzt" : <span className="text-amber-400">fehlt</span>}</td>
                  <td className="py-1.5">
                    <button
                      type="button"
                      disabled={pending === `toggle:${c.name}`}
                      onClick={() => void toggleEnabled(c)}
                      className="px-2 py-0.5 disabled:opacity-40 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition"
                    >
                      {c.enabled ? "aktiv" : "deaktiviert"}
                    </button>
                  </td>
                  <td className="py-1.5">
                    <div className="flex gap-1.5">
                      <button
                        type="button"
                        disabled={pending === `token:${c.name}`}
                        onClick={() => void setToken(c.name, c.has_token)}
                        className="px-2 py-1 disabled:opacity-40 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition"
                      >
                        {c.has_token ? "Token ersetzen" : "Token setzen"}
                      </button>
                      <button
                        type="button"
                        disabled={pending === `remove:${c.name}`}
                        onClick={() => void removeConnection(c.name)}
                        className="px-2 py-1 disabled:opacity-40 border border-red-500/40 bg-red-500/15 text-red-200 hover:bg-red-500/25 rounded-lg"
                      >
                        Entfernen
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {!showAddForm && (
          <button
            type="button"
            onClick={() => setShowAddForm(true)}
            className="px-2 py-1 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition"
          >
            + Neue Verbindung
          </button>
        )}
        {showAddForm && (
          <form onSubmit={(e) => void addConnection(e)} className="flex flex-wrap items-end gap-2 text-xs">
            <label className="flex flex-col gap-1">
              Name
              <input required value={newName} onChange={(e) => setNewName(e.target.value)} className="px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]" />
            </label>
            <label className="flex flex-col gap-1">
              Adresse (URL)
              <input
                required value={newBaseUrl} onChange={(e) => setNewBaseUrl(e.target.value)}
                placeholder="https://192.168.2.x:8006" className="w-56 px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]"
              />
            </label>
            <label className="flex flex-col gap-1">
              Token-ID
              <input
                required value={newTokenId} onChange={(e) => setNewTokenId(e.target.value)}
                placeholder="root@pam!dashboard" className="w-40 px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]"
              />
            </label>
            <label className="flex items-center gap-1">
              <input type="checkbox" checked={newTlsInsecure} onChange={(e) => setNewTlsInsecure(e.target.checked)} />
              Zertifikat nicht prüfen
            </label>
            <button type="submit" disabled={pending === "add"} className="px-2 py-1 disabled:opacity-40 accent-gradient text-white rounded-lg shadow-md shadow-black/30 hover:brightness-110 font-medium">
              Anlegen
            </button>
            <button type="button" onClick={() => setShowAddForm(false)} className="px-2 py-1 border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition">
              Abbrechen
            </button>
          </form>
        )}
      </div>
    </details>
  );
}

const KEEPS = ["keep-last", "keep-daily", "keep-weekly", "keep-monthly", "keep-yearly"] as const;
const JOB_LABEL: Record<string, string> = {
  schedule: "Zeitplan", enabled: "Aktiv", storage: "Speicher", mode: "Modus", "keep-last": "Letzte",
  "keep-daily": "Tägliche", "keep-weekly": "Wöchentliche", "keep-monthly": "Monatliche", "keep-yearly": "Jährliche",
};
type JobConfig = Record<string, string | number | boolean>;
const NEW_RULE_WARNING = "Bisher galt die Aufbewahrung des Speichers. Mit einer eigenen Regel löscht Proxmox beim nächsten Lauf, was über der neuen Grenze liegt.";

/** Nur was sich geaendert hat -- der Server prueft Whitelist, Grenzen und digest. */
export function jobChanges(before: JobConfig, after: JobConfig): JobConfig {
  const changes: JobConfig = {};
  for (const key of ["schedule", "enabled", "storage", "mode", ...KEEPS]) {
    if (before[key] !== after[key]) changes[key] = after[key];
  }
  return changes;
}

/**
 * Backup-Job bearbeiten (Roadmap Punkt 1) -- ueber das Gate. Gilt fuer ALLE Gaeste
 * des Jobs; weniger Aufbewahrung loescht beim naechsten Lauf (hohes Risiko).
 */
function JobEditForm({ job, guests, onDone }: { job: JobOut; guests: string[]; onDone: (message: string | null) => void }): JSX.Element {
  const unmountSignal = useUnmountSignal();
  const [initial, setInitial] = useState<JobConfig | null>(null);
  const [values, setValues] = useState<JobConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    authedFetch(`/ext/backups/jobs/${encodeURIComponent(job.job_ref)}/config`)
      .then(async (res) => {
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(errorFromBody(body, res.status));
        setInitial(body as JobConfig);
        setValues(body as JobConfig);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, [job.job_ref]);

  if (error) return <p className="text-xs text-red-400">Job nicht lesbar: {error}</p>;
  if (!initial || !values) return <p className="text-xs opacity-60">Lade Job …</p>;
  const changes = jobChanges(initial, values);
  const changed = Object.keys(changes);
  const keepAll = initial["keep-all"] === true;
  const shrinks = KEEPS.some((k) => Number(initial[k]) > 0 && Number(values[k]) < Number(initial[k])) || (initial.enabled === true && values.enabled === false);
  // Ohne eigene Regel galt die des Speichers (oft "alle behalten") -- eine eigene kann weniger behalten.
  const newRule = !keepAll && KEEPS.every((k) => !Number(initial[k])) && KEEPS.some((k) => Number(values[k]) > 0);

  async function save() {
    const summary = changed.map((k) => `${JOB_LABEL[k]}: ${String(initial![k])} → ${String(values![k])}`).join(", ");
    const warning = shrinks
      ? " Weniger Aufbewahrung: Proxmox löscht beim nächsten Lauf, was über der neuen Grenze liegt."
      : newRule ? ` ${NEW_RULE_WARNING}` : "";
    const ok = await deck().confirmDialog(`Backup-Job ändern (gilt für ${guests.join(", ")}) – ${summary}?${warning}`, { danger: shrinks || newRule, confirmLabel: "Ändern" });
    if (!ok) return;
    setBusy(true);
    try {
      const res = await postWithSpaceCheck(`/ext/backups/jobs/${encodeURIComponent(job.job_ref)}/edit`, { changes });
      if (res === null) return;
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      const { action, approved } = await settleAction(body, { signal: unmountSignal() });
      if (approved) {
        onDone(
          action.status === "succeeded" ? action.result?.output ?? "Geändert."
            : isActionRunning(action.status) ? RUNNING_IN_BACKGROUND
            : `Fehlgeschlagen: ${action.result?.error ?? action.status}`,
        );
      } else if (action.status === "proposed") {
        onDone(`Änderung vorgeschlagen – Freigabe durch einen Admin nötig, siehe "Aktionen".`);
      } else {
        onDone(ACTION_STATUS_LABEL[action.status ?? ""] ?? action.status ?? "?");
      }
    } catch (err) {
      onDone(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(false);
    }
  }

  const input = (key: string, props: { type?: string; width?: string; placeholder?: string } = {}) => (
    <label key={key} className="flex flex-col gap-0.5">
      <span className="opacity-60">{JOB_LABEL[key]}</span>
      <input
        type={props.type ?? "text"}
        min={props.type === "number" ? 0 : undefined}
        value={String(values[key] ?? "")}
        placeholder={props.placeholder}
        onChange={(e) => setValues({ ...values, [key]: props.type === "number" ? Number(e.target.value) : e.target.value })}
        className={`${props.width ?? "w-20"} rounded bg-white/10 px-2 py-1`}
      />
    </label>
  );

  return (
    <div className="p-2 text-xs panel" data-testid={`job-edit-${job.job_ref}`}>
      <p className="mb-2 opacity-70">Gilt für alle Gäste dieses Jobs: {guests.join(", ")}</p>
      <div className="flex flex-wrap items-end gap-3">
        {input("schedule", { width: "w-40", placeholder: "z. B. sun 03:00" })}
        {input("storage", { width: "w-32" })}
        <label className="flex flex-col gap-0.5">
          <span className="opacity-60">{JOB_LABEL.mode}</span>
          <select value={String(values.mode)} onChange={(e) => setValues({ ...values, mode: e.target.value })} className="px-2 py-1 rounded-lg border border-white/10 bg-black/25 outline-none focus:border-[var(--color-accent)]">
            <option value="snapshot">Snapshot (läuft weiter)</option>
            <option value="suspend">Anhalten</option>
            <option value="stop">Stoppen</option>
          </select>
        </label>
        <label className="flex items-center gap-1 pb-1">
          <input type="checkbox" checked={values.enabled === true} onChange={(e) => setValues({ ...values, enabled: e.target.checked })} />
          {JOB_LABEL.enabled}
        </label>
      </div>
      {keepAll ? (
        <p className="mt-2 opacity-70">Aufbewahrung: Behält alle Sicherungen. Das lässt sich nur direkt in Proxmox ändern.</p>
      ) : (
        <>
          <p className="mt-2 mb-1 opacity-60">Aufbewahrung (0 = keine eigene Regel, dann gilt die des Speichers)</p>
          <div className="flex flex-wrap items-end gap-3">{KEEPS.map((k) => input(k, { type: "number" }))}</div>
        </>
      )}
      {shrinks && <p className="mt-2 text-amber-300">Weniger Aufbewahrung: beim nächsten Lauf löscht Proxmox, was über der neuen Grenze liegt.</p>}
      {!shrinks && newRule && <p className="mt-2 text-amber-300">{NEW_RULE_WARNING}</p>}
      <div className="mt-2 flex gap-2">
        <button type="button" disabled={busy || changed.length === 0} onClick={() => void save()} className="rounded bg-white/15 px-3 py-1 hover:bg-white/25 disabled:opacity-40">
          {busy ? "…" : "Speichern"}
        </button>
        <button type="button" onClick={() => onDone(null)} className="rounded px-2 py-1 opacity-70 hover:opacity-100">Abbrechen</button>
      </div>
    </div>
  );
}

export function BackupsPage(): JSX.Element {
  const unmountSignal = useUnmountSignal();
  // Sprung von der Server-Seite des Kerns (`?host=`): nur die Jobs dieses Hosts. Der Filter
  // folgt der Adresszeile (location.ts): ein Link auf die schon offene Seite setzt ihn wieder,
  // auch nachdem man ihn hier entfernt hat.
  const [urlParams, updateUrl] = useUrlParams();
  const hostFilter = urlParams.get("host") || null;
  const [allJobs, setJobs] = useState<JobOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    authedFetch("/ext/backups/jobs")
      .then(async (res) => {
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(errorFromBody(body, res.status));
        return body as JobOut[];
      })
      .then(setJobs)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function toggleHistory(job: JobOut) {
    if (expanded === job.job_ref) {
      setExpanded(null);
      return;
    }
    setExpanded(job.job_ref);
    const res = await authedFetch(`/ext/backups/jobs/${encodeURIComponent(job.job_ref)}/history`);
    setHistory(res.ok ? ((await res.json()) as HistoryEntry[]) : []);
  }

  async function retry(job: JobOut) {
    const ok = await deck().confirmDialog(`Startet sofort ein Backup von '${job.name}' mit den Einstellungen des Jobs. Hat der Job eine Aufbewahrung, können danach ältere Sicherungen dieses Gastes auf dem Speicher gelöscht werden, auch manuelle und die anderer Jobs. Ohne eigene Aufbewahrung bleibt alles erhalten. Fortfahren?`);
    if (!ok) return;
    setBusy(job.job_ref);
    setMessage(null);
    try {
      const res = await postWithSpaceCheck(`/ext/backups/jobs/${encodeURIComponent(job.job_ref)}/retry`, {});
      if (res === null) return;
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));

      const { action, approved } = await settleAction(body, { signal: unmountSignal() });
      if (!approved && action.status === "proposed") {
        setMessage(`Backup-Retry vorgeschlagen – Freigabe durch einen Admin nötig, siehe "Aktionen".`);
      } else {
        setMessage(`Backup-Retry -> ${approved && action.status === "succeeded" ? "angenommen" : ACTION_STATUS_LABEL[action.status ?? ""] ?? action.status ?? "?"}.`);
      }
      load();
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(null);
    }
  }

  // Eine nicht erreichbare Verbindung kommt als Platzhalterzeile -- als Hinweis
  // zeigen, nicht als Job (kein Verlauf, kein "Erneut versuchen").
  const unreachable = allJobs?.filter((j) => j.last_status === "unreachable") ?? [];
  const jobs = allJobs ? allJobs.filter((j) => j.last_status !== "unreachable") : null;
  const failing = jobs?.filter((j) => j.last_status === "failed" || j.last_status === "unknown").length ?? 0;
  const { inventory, orphans } = useInventory();
  const visibleJobs = jobs && hostFilter ? jobs.filter((j) => j.host_id === hostFilter) : jobs;

  return (
    <div className="mx-auto w-full max-w-7xl p-4 sm:p-6">
      <h2 className="mb-1 font-semibold text-xl tracking-tight">Backups</h2>
      <p className="mb-3 text-sm opacity-70" data-testid="backups-scope">
        Hier stehen die Backup-Jobs deiner Proxmox-Server. Ohne Proxmox brauchst du dieses Modul nicht und kannst es ausgeschaltet lassen.
        Nodvard Deck selbst sicherst du unter Einstellungen → System.
      </p>
      <ConnectionsPanel />
      {jobs && (jobs.length > 0 || unreachable.length === 0) && (
        <p className="mb-4 text-sm opacity-70">
          {jobs.length} Job(s) – {failing > 0 ? `${failing} ohne bestätigtes erfolgreiches Backup` : "alle zuletzt erfolgreich"}
        </p>
      )}
      <UnprotectedSection
        inventory={inventory}
        // Vorschlag: der Speicher, den die bestehenden Jobs am haeufigsten nutzen.
        defaultStorage={Object.entries((jobs ?? []).reduce<Record<string, number>>((acc, j) => (j.storage ? { ...acc, [j.storage]: (acc[j.storage] ?? 0) + 1 } : acc), {})).sort((a, b) => b[1] - a[1])[0]?.[0] ?? ""}
        onChanged={load}
      />
      {error && <p className="text-sm text-red-400">Fehler: {error}</p>}
      {unreachable.length > 0 && (
        <div className="mb-2 text-sm text-red-400" data-testid="unreachable">
          {unreachable.map((u) => (
            <p key={u.connection}>
              Verbindung „{u.connection}“ nicht erreichbar – ihre Backup-Jobs fehlen hier gerade. <span className="text-xs opacity-80">({u.error ?? u.storage})</span>
            </p>
          ))}
        </div>
      )}
      {message && <p className="mb-2 text-sm opacity-80">{message}</p>}
      {!jobs && !error && <p className="text-sm opacity-60">Lade …</p>}
      {jobs && jobs.length === 0 && unreachable.length === 0 && <p className="text-sm opacity-60">Keine Backup-Jobs konfiguriert.</p>}
      {hostFilter && jobs && (
        <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
          <span className="accent-soft flex items-center gap-1 rounded px-2 py-0.5" data-testid="host-filter">
            Nur {visibleJobs?.[0]?.name ?? "dieser Server"}
            <button type="button" onClick={() => updateUrl({ host: null })} aria-label="Filter entfernen" className="opacity-70 hover:opacity-100">
              ✕
            </button>
          </span>
          {visibleJobs?.length === 0 && (
            <span className="text-amber-300">Kein Backup-Job erfasst diesen Server – siehe „Ohne Backup“ oben.</span>
          )}
        </div>
      )}

      {visibleJobs && visibleJobs.length > 0 && (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-white/10 text-left text-xs uppercase opacity-60">
              <th className="py-1">VM</th>
              <th className="py-1">Storage</th>
              <th className="py-1">Zeitplan</th>
              <th className="py-1">Letzter Status</th>
              <th className="py-1">Letzter Lauf</th>
              <th className="py-1">Vorhanden</th>
              <th className="py-1">Aktionen</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {visibleJobs.map((job) => (
              <Fragment key={job.job_ref}>
                <tr>
                  <td className="py-1.5">{job.name} <span className="opacity-50">({job.vmid})</span></td>
                  <td className="py-1.5 opacity-70">{job.storage ?? "-"}</td>
                  <td className="py-1.5 opacity-70">
                    {job.schedule ?? "-"}
                    {job.next_run_at ? <div className="text-xs">nächster: {formatEpoch(job.next_run_at)}</div> : null}
                    {job.retention ? <div className="text-xs">behält: {job.retention}</div> : null}
                  </td>
                  <td className="py-1.5">
                    <span className={`rounded border px-1.5 py-0.5 text-xs ${STATUS_CLASS[job.last_status] ?? STATUS_CLASS.unknown}`}>
                      {STATUS_LABEL[job.last_status] ?? job.last_status}
                    </span>
                  </td>
                  <td className="py-1.5 opacity-70">{formatEpoch(job.last_run_at)}</td>
                  <td className="py-1.5 text-xs opacity-70" data-testid={`inventory-${job.job_ref}`}>
                    {inventory ? inventoryText(inventory.get(`${job.connection}/${job.vmid}`)) : "…"}
                  </td>
                  <td className="py-1.5">
                    <div className="flex gap-1.5">
                      <button type="button" onClick={() => void toggleHistory(job)} className="px-2 py-1 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition">
                        {expanded === job.job_ref ? "Verlauf ausblenden" : "Verlauf"}
                      </button>
                      {/* Der Knopf startet immer ein volles Backup jetzt; "Erneut versuchen"
                          passt nur nach einem Fehlschlag, waehrend eines Laufs gibt es keinen. */}
                      {job.last_status !== "running" && (
                        <button
                          type="button" disabled={busy === job.job_ref} onClick={() => void retry(job)}
                          className={job.last_status === "failed"
                            ? "rounded bg-red-500/20 px-2 py-1 text-xs hover:bg-red-500/30 disabled:opacity-40"
                            : "px-2 py-1 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition disabled:opacity-40"}
                        >
                          {job.last_status === "failed" ? "Erneut versuchen" : "Jetzt sichern"}
                        </button>
                      )}
                      {deck().hasPermission("settings.write") && (
                        <button type="button" onClick={() => setEditing(editing === job.job_ref ? null : job.job_ref)}
                          className="px-2 py-1 text-xs border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition">
                          Job bearbeiten
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
                {editing === job.job_ref && (
                  <tr>
                    <td colSpan={7} className="bg-black/20 py-2">
                      <JobEditForm
                        job={job}
                        guests={(jobs ?? []).filter((j) => j.connection === job.connection && j.job_ref.split("--")[1] === job.job_ref.split("--")[1]).map((j) => j.name)}
                        onDone={(text) => {
                          setEditing(null);
                          if (text) {
                            setMessage(text);
                            load();
                          }
                        }}
                      />
                    </td>
                  </tr>
                )}
                {expanded === job.job_ref && (
                  <tr>
                    <td colSpan={7} className="bg-black/20 py-2">
                      <ul className="text-xs opacity-80">
                        {history.length === 0 && <li>Keine Läufe bekannt.</li>}
                        {history.map((h) => (
                          <li key={h.upid}>
                            {formatEpoch(h.started_at)} – {STATUS_LABEL[h.status] ?? h.status} ({h.node ?? "?"})
                          </li>
                        ))}
                      </ul>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}

      {orphans.length > 0 && (
        <section className="mt-4 text-sm" data-testid="orphans">
          <h3 className="font-semibold opacity-80">Verwaiste Sicherungen ({orphans.length})</h3>
          <p className="mb-1 text-xs opacity-60">
            Zu diesen Dateien gibt es keinen bekannten Gast mehr (gelöscht oder umgezogen). Sie belegen nur Platz – vor dem Löschen in Proxmox prüfen.
          </p>
          <ul className="text-xs opacity-80">
            {orphans.map((o) => (
              <li key={`${o.connection}-${o.vmid}-${o.kind ?? ""}`}>
                {o.kind === "lxc" ? "Container" : "VM"} {o.vmid} (gesehen über {o.connection}) · {inventoryText(o)}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
