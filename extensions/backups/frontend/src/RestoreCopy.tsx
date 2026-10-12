/**
 * "Als Kopie wiederherstellen": aus einer Proxmox-Sicherung einen NEUEN Gast anlegen, das Original bleibt
 * unberuehrt (nodvard_deck_ext_backups/restore.py).
 *
 * Ablauf: Sicherungen des Gastes laden (`GET .../restore/backups`), fuer die gewaehlte Sicherung die Vorschlaege
 * (`GET .../restore/options`: Knoten, passende Speicher mit Platz, freie Nummer, Name, Hinweise, und ob etwas die
 * Kopie von vornherein verhindert: zu alter Proxmox, Pool oder Rechte fehlen). "Weiter" zeigt eine Zusammenfassung
 * (Nummer, Name, Knoten, Speicher, Pool, Netzwerk); erst "Kopie anlegen" legt die Gate-Aktion `backup.restore_copy`
 * an (`POST .../restore`, hohes Risiko). Wer `actions.approve:high` hat, gibt selbst frei, alle anderen schlagen nur
 * vor. Der Server prueft beim Vorschlagen und beim Ausfuehren alles noch einmal selbst (Nummer, Platz, Speicher,
 * Rechte, Version).
 */
import { useEffect, useState } from "react";

import { ACTION_STATUS_LABEL, isActionRunning, RUNNING_IN_BACKGROUND, settleAction } from "../../../_shared/frontend/src/actions";
import { authedFetch, errorFromBody } from "../../../_shared/frontend/src/api";
import { deck } from "../../../_shared/frontend/src/deck";
import { formatBytes } from "../../../_shared/frontend/src/format";
import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { Badge, Button, Field, inputClass, Notice } from "../../../_shared/frontend/src/ui";

export interface RestoreTarget {
  connection: string;
  vmid: string;
  name: string;
  /** "qemu"/"lxc", wenn bekannt (verwaiste Sicherungen); sonst entscheidet der Server. */
  kind?: string | null;
}

interface BackupEntry {
  volid: string;
  storage: string;
  kind: string;
  kind_label: string;
  ctime: number | null;
  size: number | null;
  notes: string | null;
  protected: boolean;
}

interface BackupList {
  backups: BackupEntry[];
  errors: string[];
}

interface StorageChoice {
  storage: string;
  type: string | null;
  avail: number | null;
  need_bytes: number | null;
  fits: boolean;
  warning: string | null;
}

export interface RestoreOptions {
  kind: string;
  kind_label: string;
  source_vmid: string;
  source_name: string | null;
  source_exists: boolean;
  backup_at: number | null;
  backup_size: number;
  disk_bytes: number | null;
  nets: { key: string; bridge: string | null; tag?: string | null; addresses: string[] }[];
  warnings: string[];
  refuse: string | null;
  /** Ein Satz, wenn die Kopie schon vor jeder Eingabe nicht geht (zu alter Proxmox, Pool oder Rechte fehlen). */
  blocked: string | null;
  /** Der feste Pool, in dem jede Kopie entsteht. */
  pool: string;
  /** Der Satz, wie man die Kopie aus dem Pool nimmt, wenn man sie behalten will. */
  keep_hint: string;
  suggested_vmid: number | null;
  suggested_name: string;
  nodes: { node: string; storages: StorageChoice[] }[];
  default_node: string;
  default_storage: string | null;
}

function when(seconds: number | null): string {
  return seconds ? new Date(seconds * 1000).toLocaleString() : "unbekannt";
}

async function getJson<T>(path: string): Promise<T> {
  const res = await authedFetch(path);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorFromBody(body, res.status));
  return body as T;
}

/** Die festen Regeln, so wie Nodvard Deck sie umsetzt -- ehrlich, auch was es NICHT tut. */
export function RestoreRules({ kind, linkDown, pool, keepHint }: { kind: string; linkDown: boolean; pool: string; keepHint: string }): JSX.Element {
  const container = kind === "lxc";
  return (
    <ul className="list-disc space-y-1 pl-4" data-testid="restore-rules">
      <li>Das Original bleibt unberührt. Die Kopie bekommt eine neue Nummer und neue MAC-Adressen.</li>
      <li>
        Die Kopie entsteht im Pool {pool}: nur dort darf Nodvard Deck Gäste anlegen. {keepHint}
      </li>
      <li>
        Sie startet nicht von selbst – auch nicht, wenn der Server neu startet: Nodvard Deck schaltet den Autostart schon
        beim Anlegen ab und prüft ihn, sobald Proxmox fertig ist.
      </li>
      <li>
        Im Gast ist alles wie beim Original, auch eine feste IP-Adresse. Laufen beide gleichzeitig im selben Netz,
        stören sie sich.
        {container
          ? " Den neuen Namen setzt Proxmox beim Container auch als Rechnernamen."
          : " Bei einer VM ändert sich nur der Name in Proxmox, nicht der Rechnername im Gast."}
      </li>
      {linkDown ? (
        <li>
          Deshalb trennt Nodvard Deck die Netzwerkkarten der Kopie, sobald Proxmox mit der Wiederherstellung fertig ist.
          Endet die Aktion vorher (Neustart des Dashboards, mehr als 45 Minuten), holt der Backup-Wächter das nach – er
          sieht alle 15 Minuten nach und meldet sich. Bis dahin die Kopie nicht starten. Verbinden: in Proxmox bei der
          Kopie unter „{container ? "Netzwerk" : "Hardware"}“ die Netzwerkkarte bearbeiten und „Getrennt“ abhaken – erst
          wenn die IP-Adresse geändert ist oder das Original aus ist.
        </li>
      ) : (
        <li className="text-amber-300">
          Die Netzwerkkarten bleiben verbunden: Ändere vor dem ersten Start die IP-Adresse in der Kopie oder schalte das
          Original aus.
        </li>
      )}
    </ul>
  );
}

export function RestoreCopyPanel({ target, onDone }: { target: RestoreTarget; onDone: (message: string | null) => void }): JSX.Element {
  const unmountSignal = useUnmountSignal();
  const [list, setList] = useState<BackupList | null>(null);
  const [volid, setVolid] = useState("");
  const [options, setOptions] = useState<RestoreOptions | null>(null);
  const [node, setNode] = useState("");
  const [storage, setStorage] = useState("");
  const [vmid, setVmid] = useState("");
  const [name, setName] = useState("");
  const [linkDown, setLinkDown] = useState(true);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const query = new URLSearchParams({ connection: target.connection, vmid: target.vmid });
    if (target.kind) query.set("kind", target.kind);
    getJson<BackupList>(`/ext/backups/restore/backups?${query.toString()}`)
      .then((body) => {
        setList(body);
        setVolid(body.backups[0]?.volid ?? "");
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, [target.connection, target.vmid, target.kind]);

  useEffect(() => {
    if (!volid) return;
    setOptions(null);
    setConfirming(false);
    setError(null);
    const query = new URLSearchParams({ connection: target.connection, volid });
    getJson<RestoreOptions>(`/ext/backups/restore/options?${query.toString()}`)
      .then((body) => {
        setOptions(body);
        setNode(body.default_node);
        setStorage(body.default_storage ?? "");
        setVmid(body.suggested_vmid ? String(body.suggested_vmid) : "");
        setName(body.suggested_name);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, [target.connection, volid]);

  const storages = options?.nodes.find((n) => n.node === node)?.storages ?? [];
  const chosen = storages.find((s) => s.storage === storage);
  const canApprove = deck().hasPermission("actions.approve:high");
  const ready = !!options && !options.refuse && !options.blocked && !!chosen?.fits && /^\d+$/.test(vmid) && name.trim() !== "" && !busy;

  function chooseNode(next: string) {
    setNode(next);
    setStorage(options?.nodes.find((n) => n.node === next)?.storages.find((s) => s.fits)?.storage ?? "");
  }

  async function submit() {
    if (!options || options.blocked) return;
    setBusy(true);
    setError(null);
    try {
      const res = await authedFetch("/ext/backups/restore", {
        method: "POST",
        body: JSON.stringify({ connection: target.connection, volid, node, storage, vmid: Number(vmid), name: name.trim(), link_down: linkDown }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        // Nummer inzwischen vergeben, zu wenig Platz ...: der Dialog bleibt offen, damit man es aendern kann.
        setError(errorFromBody(body, res.status));
        setConfirming(false);
        return;
      }
      const { action, approved } = await settleAction(body, { signal: unmountSignal() });
      if (approved) {
        onDone(
          action.status === "succeeded" ? action.result?.output ?? "Kopie angelegt."
            : isActionRunning(action.status) ? RUNNING_IN_BACKGROUND
            : `Fehlgeschlagen: ${action.result?.error ?? action.status}`,
        );
      } else if (action.status === "proposed") {
        onDone("Wiederherstellung vorgeschlagen – Freigabe durch einen Admin nötig, siehe „Aktionen“.");
      } else {
        onDone(ACTION_STATUS_LABEL[action.status ?? ""] ?? action.status ?? "?");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const title = `„${target.name}“ als Kopie wiederherstellen`;
  const showForm = !!options && !options.refuse && !confirming;
  return (
    <div className="space-y-3 p-3 text-sm panel" role="group" aria-label={title} data-testid={`restore-${target.connection}-${target.vmid}`}>
      <p className="font-medium">{title}</p>
      {error && <Notice text={error} />}
      {!list && !error && <p className="opacity-60">Lade Sicherungen …</p>}
      {list && list.backups.length === 0 && (
        <p className="opacity-70">Keine Sicherung gefunden.{list.errors.length > 0 ? ` (${list.errors.join("; ")})` : ""}</p>
      )}
      {list && list.backups.length > 0 && !confirming && (
        <Field label="Sicherung">
          <select value={volid} onChange={(e) => setVolid(e.target.value)} className={inputClass}>
            {list.backups.map((b) => (
              <option key={b.volid} value={b.volid}>
                {when(b.ctime)} · {b.kind_label} · {formatBytes(b.size)} · {b.storage}{b.notes ? ` · ${b.notes}` : ""}
              </option>
            ))}
          </select>
        </Field>
      )}
      {volid && !options && !error && <p className="opacity-60">Prüfe Speicher und Nummer …</p>}
      {options?.refuse && <Notice text={options.refuse} />}
      {options?.blocked && !options.refuse && <div data-testid="restore-blocked"><Notice text={options.blocked} /></div>}
      {showForm && (
        <>
          <p className="opacity-70">
            Sicherung von {options.kind_label} {options.source_vmid}
            {options.source_name ? ` (${options.source_name})` : ""} vom {when(options.backup_at)}, {formatBytes(options.backup_size)}
            {options.source_exists ? "" : " – das Original gibt es in Proxmox nicht mehr"}.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Zielknoten">
              <select value={node} onChange={(e) => chooseNode(e.target.value)} className={inputClass}>
                {options.nodes.map((n) => <option key={n.node} value={n.node}>{n.node}</option>)}
              </select>
            </Field>
            <Field label="Zielspeicher">
              <select value={storage} onChange={(e) => setStorage(e.target.value)} className={inputClass}>
                {!chosen && <option value="">– bitte wählen –</option>}
                {storages.map((s) => (
                  <option key={s.storage} value={s.storage} disabled={!s.fits}>
                    {s.storage} – {s.avail === null ? "freier Platz unbekannt" : `${formatBytes(s.avail)} frei`}
                    {s.fits ? "" : " (zu wenig Platz)"}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Neue Nummer (VMID)">
              <input value={vmid} inputMode="numeric" onChange={(e) => setVmid(e.target.value.trim())} className={inputClass} />
            </Field>
            <Field label="Name der Kopie">
              <input value={name} onChange={(e) => setName(e.target.value)} className={inputClass} />
            </Field>
          </div>
          {storages.length > 0 && !storages.some((s) => s.fits) && (
            <p className="text-amber-300">Auf diesem Knoten passt die Kopie auf keinen Speicher.</p>
          )}
          {chosen && (
            <p data-testid="restore-space" className="flex flex-wrap items-center gap-2">
              <Badge tone={chosen.fits ? (chosen.warning ? "warn" : "good") : "bad"}>{chosen.fits ? "Platz reicht" : "zu wenig Platz"}</Badge>
              <span className="opacity-80">
                Braucht mindestens {formatBytes(chosen.need_bytes)} (Sicherung plus Reserve)
                {options.disk_bytes ? `, die Platten sind zusammen bis zu ${formatBytes(options.disk_bytes)} groß` : ""}; frei auf {chosen.storage}:{" "}
                {formatBytes(chosen.avail)}.
              </span>
            </p>
          )}
          {chosen?.warning && <p className="text-amber-300">{chosen.warning}</p>}
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={linkDown} onChange={(e) => setLinkDown(e.target.checked)} />
            Netzwerkkarten der Kopie getrennt anlegen (empfohlen)
          </label>
          <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs" data-testid="restore-notes">
            <RestoreRules kind={options.kind} linkDown={linkDown} pool={options.pool} keepHint={options.keep_hint} />
            {options.warnings.length > 0 && (
              <ul className="mt-2 list-disc space-y-1 pl-4 text-amber-300">
                {options.warnings.map((w) => <li key={w}>{w}</li>)}
              </ul>
            )}
          </div>
        </>
      )}
      {confirming && options && (
        <div className="space-y-2 rounded-lg border border-white/10 bg-white/[0.04] p-3" data-testid="restore-summary">
          <p className="font-medium">Das legt Nodvard Deck an:</p>
          <ul className="list-disc space-y-0.5 pl-4">
            <li>Neue Nummer: {vmid}</li>
            <li>Name: {name.trim()}</li>
            <li>
              Aus der Sicherung von {options.kind_label} {options.source_vmid}
              {options.source_name ? ` (${options.source_name})` : ""} vom {when(options.backup_at)}
            </li>
            <li>Knoten: {node}</li>
            <li>Zielspeicher: {storage}</li>
            <li>Pool {options.pool}</li>
            <li className={linkDown ? undefined : "text-amber-300"}>{linkDown ? "Netzwerk getrennt" : "Netzwerk verbunden"}</li>
            <li>Die Kopie startet nicht von selbst.</li>
          </ul>
          <p className="text-xs opacity-80">
            Das Original bleibt unberührt. {options.keep_hint}
          </p>
          <p className="text-xs opacity-80">
            {canApprove
              ? "Mit „Kopie anlegen“ gibst du die Wiederherstellung gleich selbst frei."
              : "Mit „Kopie anlegen“ schlägst du die Wiederherstellung nur vor: Ein Admin muss sie unter „Aktionen“ freigeben, erst dann legt Nodvard Deck die Kopie an."}
          </p>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {options && !options.refuse && !confirming && (
          <Button variant="secondary" disabled={!ready} onClick={() => setConfirming(true)}>
            Weiter
          </Button>
        )}
        {options && !options.refuse && confirming && (
          <>
            <Button variant={canApprove ? "danger" : "secondary"} disabled={!ready} onClick={() => void submit()}>
              {busy ? "Läuft …" : "Kopie anlegen"}
            </Button>
            <Button variant="ghost" disabled={busy} onClick={() => setConfirming(false)}>Zurück</Button>
          </>
        )}
        <Button variant="ghost" onClick={() => onDone(null)}>Abbrechen</Button>
      </div>
      {busy && canApprove && (
        <p className="text-xs opacity-60">
          Das kann bei großen Gästen lange dauern. Du kannst die Seite verlassen – das Ergebnis steht dann unter „Aktionen“.
        </p>
      )}
    </div>
  );
}
