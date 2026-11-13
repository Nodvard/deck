/** Netz anlegen und Container verbinden oder trennen; alles geht über die Freigabe. */
import { useState } from "react";

import { deck } from "../../../_shared/frontend/src/deck";
import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { errorMessage } from "../../../_shared/frontend/src/loadError";
import { Button } from "../../../_shared/frontend/src/ui";

import { hostAction } from "./hostAction";
import { productText } from "./product";

export interface HostContainer { name: string; compose_project: string | null; self?: boolean }

const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$/;
const RESERVED = new Set(["bridge", "host", "none", "default"]);
/** Netze, an die Docker keinen Container von Hand hängt: host und none legt man beim Anlegen fest, ingress und
 * docker_gwbridge gehören dem Swarm. */
const FIXED_NETWORKS = new Set(["host", "none", "ingress", "docker_gwbridge"]);
const LINKABLE_DRIVERS = new Set(["bridge", "macvlan", "ipvlan"]);

export function linkable(name: string, driver: string | null): boolean {
  return !FIXED_NETWORKS.has(name) && LINKABLE_DRIVERS.has(driver ?? "");
}

/** `onDone` bekommt bei einem Vorschlag die Kennung der Aktion, damit die Liste nach der Freigabe neu lädt. */
export function NetworkCreate({ hostId, onDone }: { hostId: string; onDone: (message: string, waitingFor?: string) => void }): JSX.Element {
  const unmountSignal = useUnmountSignal();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [internal, setInternal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const valid = NAME_RE.test(name) && !RESERVED.has(name);

  // Bei einem Fehler bleibt das Formular mit dem Namen offen; `onDone` lädt die Liste neu und baut es dabei ab.
  async function create() {
    setBusy(true);
    setError(null);
    try {
      const result = await hostAction(hostId, "docker.network_create", "Netz über die Docker-Seite anlegen.", unmountSignal(), { name, internal });
      if (result.failed) {
        setError(result.text);
        return;
      }
      setOpen(false);
      setName("");
      setInternal(false);
      onDone(`Netz anlegen: ${result.text}`, result.waiting ? result.actionId : undefined);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (!open) return <div className="mb-2"><Button small onClick={() => setOpen(true)}>Netz anlegen</Button></div>;
  return (
    <div className="mb-3 flex max-w-xl flex-col gap-2 bg-black/20 p-2 text-xs panel" data-testid="network-create">
      <label className="flex flex-col gap-1">
        <span>Name des Netzes</span>
        <input aria-label="Name des Netzes" value={name} maxLength={63} onChange={(e) => setName(e.target.value.trim())} placeholder="zum Beispiel intern"
          className="rounded border border-white/10 bg-black/25 px-2 py-1 outline-none focus:border-[var(--color-accent)]" />
      </label>
      {name && !valid && (
        <p className="text-amber-300">Nur Buchstaben, Ziffern, „_“, „.“ und „-“, am Anfang ein Buchstabe oder eine Ziffer, nicht bridge, host, none oder default.</p>
      )}
      <label className="flex items-start gap-2">
        <input type="checkbox" checked={internal} onChange={(e) => setInternal(e.target.checked)} className="mt-0.5" />
        <span>Nur intern: Container erreichen sich in diesem Netz gegenseitig, über dieses Netz aber weder das Internet noch das Heimnetz.</span>
      </label>
      {error && <p role="alert" className="text-red-400">{error}</p>}
      <div className="flex flex-wrap gap-1.5">
        <Button small variant="primary" disabled={busy || !valid} onClick={() => void create()}>{busy ? "…" : "Anlegen"}</Button>
        <Button small disabled={busy} onClick={() => setOpen(false)}>Abbrechen</Button>
      </div>
    </div>
  );
}

function stackNote(container: HostContainer | undefined, connect: boolean): string {
  if (!container?.compose_project) return "";
  return connect
    ? ` Er gehört zum Stack „${container.compose_project}“: Erstellt Compose ihn neu, etwa nach einer Änderung oder einem Update, ist die Verbindung wieder weg. Dauerhaft gehört das Netz in die Compose-Datei.`
    : ` Er gehört zum Stack „${container.compose_project}“: Erstellt Compose ihn neu, hängt er wieder an den Netzen aus der Compose-Datei.`;
}

const CONTAINER_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$/;

export function NetworkLinks({ hostId, network, title, usedBy, containers, networksOf, pending, actionSignal, onDone }: {
  hostId: string;
  network: string;
  title: string;
  usedBy: string[];
  containers: HostContainer[];
  /** An wie vielen Netzen ein Container hängt, damit das Trennen vom letzten eigens gewarnt wird. */
  networksOf: (container: string) => number;
  /** Vorschläge, die noch auf die Freigabe warten (`container:connect` oder `container:disconnect`). */
  pending: ReadonlySet<string>;
  actionSignal: () => AbortSignal;
  onDone: (message: string, outcome: { waiting: boolean; failed: boolean; key: string; actionId?: string }) => void;
}): JSX.Element {
  const [pick, setPick] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const known = new Map(containers.map((c) => [c.name, c]));
  const free = containers.filter((c) => !usedBy.includes(c.name) && !c.self);

  async function link(container: string, connect: boolean) {
    const question = connect
      ? `„${container}“ mit dem Netz „${title}“ verbinden? Der Container kann danach die anderen Container in diesem Netz erreichen und sie ihn.`
      : `„${container}“ vom Netz „${title}“ trennen? Dienste, die ihn über dieses Netz erreichen, finden ihn danach nicht mehr.`;
    const last = !connect && networksOf(container) === 1
      ? " Es ist sein einziges Netz: Danach hat er kein Netz mehr, keine veröffentlichten Ports und keinen Zugang ins Internet."
      : "";
    const ok = await deck().confirmDialog(question + last + stackNote(known.get(container), connect), {
      title: connect ? "Mit Netz verbinden" : "Vom Netz trennen", danger: !connect, confirmLabel: connect ? "Verbinden" : "Trennen",
    });
    if (!ok) return;
    setBusy(container);
    const key = `${container}:${connect ? "connect" : "disconnect"}`;
    try {
      const action = connect ? "docker.network_connect" : "docker.network_disconnect";
      const reason = connect ? "Container über die Docker-Seite mit einem Netz verbinden." : "Container über die Docker-Seite von einem Netz trennen.";
      const result = await hostAction(hostId, action, reason, actionSignal(), { network, container });
      onDone(`${connect ? "Verbinden" : "Trennen"}: ${result.text}`, { waiting: result.waiting, failed: result.failed, key, actionId: result.actionId });
      if (!result.failed) setPick("");
    } catch (err) {
      onDone(`Fehler: ${errorMessage(err)}`, { waiting: false, failed: true, key });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mt-1 flex w-full flex-col gap-1.5 bg-black/20 p-2 text-xs panel" data-testid={`network-links-${title}`}>
      {usedBy.length === 0 && <p className="opacity-70">An diesem Netz hängt noch kein Container.</p>}
      {usedBy.map((name) => {
        const entry = known.get(name);
        return (
          <p key={name} className="flex flex-wrap items-center gap-2">
            <span className="min-w-0 flex-1 break-words">
              {name}{entry?.compose_project ? ` (Stack ${entry.compose_project})` : ""}{entry?.self ? productText(" – Nodvard Deck selbst, bleibt verbunden") : ""}
            </span>
            {pending.has(`${name}:disconnect`) ? <span className="opacity-70">wartet auf Freigabe</span>
              : !entry?.self && CONTAINER_RE.test(name) && (
                <Button small disabled={busy !== null} onClick={() => void link(name, false)} ariaLabel={`Trennen: ${name}`}>
                  {busy === name ? "…" : "Trennen"}
                </Button>
              )}
          </p>
        );
      })}
      {containers.length === 0 ? (
        <p className="opacity-70">Auf diesem Server gibt es keine Container.</p>
      ) : free.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label={`Container für ${title}`} value={pick} onChange={(e) => setPick(e.target.value)} disabled={busy !== null}
            className="min-w-0 flex-1 rounded border border-white/10 bg-black/25 px-2 py-1 outline-none focus:border-[var(--color-accent)]">
            <option value="" disabled>Container wählen …</option>
            {free.map((c) => <option key={c.name} value={c.name}>{c.name}{c.compose_project ? ` (Stack ${c.compose_project})` : ""}</option>)}
          </select>
          {pick && pending.has(`${pick}:connect`) ? <span className="opacity-70">wartet auf Freigabe</span> : (
            <Button small disabled={busy !== null || !pick} onClick={() => void link(pick, true)}>{busy === pick && pick ? "…" : "Verbinden"}</Button>
          )}
        </div>
      ) : (
        <p className="opacity-70">Alle übrigen Container dieses Servers hängen schon an diesem Netz.</p>
      )}
    </div>
  );
}
