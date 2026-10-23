/** Images, Volumes und Netze eines Docker-Servers als Reiter der Docker-Seite; Löschen nur über die Freigabe. */
import { useEffect, useState, type ReactNode } from "react";

import { deck } from "../../../_shared/frontend/src/deck";
import { formatBytes } from "../../../_shared/frontend/src/format";
import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { LoadError, errorMessage, fetchJson } from "../../../_shared/frontend/src/loadError";
import { Badge, Button } from "../../../_shared/frontend/src/ui";

import { hostAction } from "./hostAction";
import { OperateNote } from "./operate";

export type ResourceKind = "images" | "volumes" | "networks";

export const RESOURCE_TABS: { kind: ResourceKind; label: string }[] = [
  { kind: "images", label: "Images" },
  { kind: "volumes", label: "Volumes" },
  { kind: "networks", label: "Netze" },
];

interface Usage { used_by: string[]; in_use: boolean; removable: boolean }
interface ImageOut extends Usage { id: string; short_id: string; names: string[]; size_bytes: number | null; created_at: string | null; dangling: boolean; rollback?: boolean }
interface VolumeOut extends Usage { name: string; driver: string | null; size_bytes: number | null; anonymous: boolean; compose_project: string | null }
interface NetworkOut extends Usage { id: string; short_id: string; name: string; driver: string | null; internal: boolean; builtin: boolean; compose_project: string | null }

interface Row {
  key: string;
  title: string;
  muted: boolean;
  facts: string[];
  builtin: boolean;
  usedBy: string[];
  inUse: boolean;
  removable: boolean;
  /** Warum etwas Unbenutztes trotzdem keinen Löschen-Knopf hat. */
  kept?: string;
  payload: Record<string, string>;
}

interface KindText {
  what: string;
  noun: string;
  action: string;
  label: string;
  empty: string;
  hint: string;
  confirm: (title: string) => string;
}

const TEXT: Record<ResourceKind, KindText> = {
  images: {
    what: "Die Liste der Images", noun: "Images", action: "docker.image_remove", label: "Image löschen",
    empty: "Auf diesem Server liegen keine Images.",
    hint: "Gelöscht werden kann nur ein Image, das kein Container nutzt, auch kein gestoppter.",
    confirm: (title) => `Image „${title}“ löschen? Braucht es später ein Container, lädt Docker es neu herunter; ein selbst gebautes muss neu gebaut werden.`,
  },
  volumes: {
    what: "Die Liste der Volumes", noun: "Volumes", action: "docker.volume_remove", label: "Volume löschen",
    empty: "Auf diesem Server gibt es keine Volumes.",
    hint: "Volumes enthalten die Daten deiner Container. Gelöscht werden kann nur, was kein Container mehr nutzt.",
    confirm: (title) => `Volume „${title}“ löschen? Die Daten darin sind dann weg, das lässt sich nicht rückgängig machen.`,
  },
  networks: {
    what: "Die Liste der Netze", noun: "Netze", action: "docker.network_remove", label: "Netz löschen",
    empty: "Auf diesem Server gibt es keine Netze.",
    hint: "Die eingebauten Netze von Docker bleiben immer. Ein Netz, an dem noch ein Container hängt, auch ein gestoppter, bleibt ebenfalls.",
    confirm: (title) => `Netz „${title}“ löschen? Ein Compose-Projekt legt es beim nächsten Start wieder an.`,
  },
};

const size = (bytes: number | null) => (bytes == null ? "Größe unbekannt" : formatBytes(bytes));

/** `2026-09-01 10:00:00 +0200 CEST` als `1.9.2026`. */
function day(text: string): string {
  const [y, m, d] = text.slice(0, 10).split("-").map(Number);
  return y && m && d ? `${d}.${m}.${y}` : text.slice(0, 10);
}

function imageRow(i: ImageOut): Row {
  return {
    key: i.id, title: i.names.length ? i.names.join(", ") : `ohne Namen (${i.short_id})`, muted: i.dangling,
    facts: [size(i.size_bytes), ...(i.created_at ? [`erstellt am ${day(i.created_at)}`] : [])],
    builtin: false, usedBy: i.used_by, inUse: i.in_use, removable: i.removable, payload: { image: i.id },
    kept: !i.in_use && !i.removable ? keptImage(i) : undefined,
  };
}

function keptImage(i: ImageOut): string {
  if (i.rollback) {
    return "Bleibt als Rückweg für Nodvard Deck: Ohne dieses Image gibt es nach einem misslungenen Update kein Zurück.";
  }
  if (i.names.length > 1) {
    return `Hat mehrere Namen; Docker löscht es erst, wenn nur noch einer übrig ist. Einen Namen entfernst du im Terminal mit: docker rmi ${i.names[0]}`;
  }
  return `Docker nennt eine ungewohnte Kennung, deshalb gibt es hier keinen Löschen-Knopf. Löschen kannst du es im Terminal mit: docker rmi ${i.short_id}`;
}

function volumeRow(v: VolumeOut): Row {
  return {
    key: v.name, title: v.anonymous ? `anonym (${v.name.slice(0, 12)})` : v.name, muted: v.anonymous,
    facts: [size(v.size_bytes), ...(v.compose_project ? [`Stack ${v.compose_project}`] : []), ...(v.driver && v.driver !== "local" ? [v.driver] : [])],
    builtin: false, usedBy: v.used_by, inUse: v.in_use, removable: v.removable, payload: { volume: v.name },
  };
}

function networkRow(n: NetworkOut): Row {
  return {
    key: n.id, title: n.name, muted: n.builtin,
    facts: [n.driver ?? "?", ...(n.compose_project ? [`Stack ${n.compose_project}`] : []), ...(n.internal ? ["nur intern"] : [])],
    builtin: n.builtin, usedBy: n.used_by, inUse: n.in_use || n.builtin, removable: n.removable, payload: { network: n.id },
  };
}

function usage(row: Row): JSX.Element | string {
  if (row.usedBy.length) return `benutzt von ${row.usedBy.join(", ")}`;
  if (row.inUse) return row.builtin ? "" : "von einem Container benutzt";
  return (
    <>
      <span className="text-amber-300">von keinem Container benutzt</span>
      {row.kept && <span className="block opacity-70">{row.kept}</span>}
    </>
  );
}

function toRows(kind: ResourceKind, body: Record<string, unknown>): Row[] {
  if (kind === "images") return ((body.images ?? []) as ImageOut[]).map(imageRow);
  if (kind === "volumes") return ((body.volumes ?? []) as VolumeOut[]).map(volumeRow);
  return ((body.networks ?? []) as NetworkOut[]).map(networkRow);
}

export function ResourceList({ hostId, kind }: { hostId: string; kind: ResourceKind }): JSX.Element {
  const unmountSignal = useUnmountSignal();
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [waiting, setWaiting] = useState<ReadonlySet<string>>(new Set());
  const [reload, setReload] = useState(0);
  const text = TEXT[kind];

  useEffect(() => {
    const controller = new AbortController();
    setRows(null);
    setError(null);
    fetchJson<Record<string, unknown>>(`/ext/docker/hosts/${encodeURIComponent(hostId)}/${kind}`, { signal: controller.signal })
      .then((body) => {
        if (controller.signal.aborted) return;
        if (kind === "networks" && typeof body.error === "string" && body.error) setError(body.error);
        else setRows(toRows(kind, body));
      })
      .catch((err: unknown) => { if (!controller.signal.aborted) setError(errorMessage(err)); });
    return () => controller.abort();
  }, [hostId, kind, reload]);

  async function remove(row: Row) {
    const ok = await deck().confirmDialog(text.confirm(row.title), { title: text.label, danger: true, confirmLabel: "Löschen" });
    if (!ok) return;
    setBusy(row.key);
    setMessage(null);
    try {
      const result = await hostAction(hostId, text.action, `${text.label} über die Docker-Seite.`, unmountSignal(), row.payload);
      setMessage(`${text.label}: ${result.text}`);
      if (result.waiting) setWaiting((keys) => new Set(keys).add(row.key));
      setReload((n) => n + 1);
    } catch (err) {
      setMessage(`Fehler: ${errorMessage(err)}`);
    } finally {
      setBusy(null);
    }
  }

  if (error) return <LoadError what={text.what} error={error} onRetry={() => setReload((n) => n + 1)} />;
  if (!rows) return <p className="text-sm opacity-60">Lade die {text.noun} … Das dauert ein paar Sekunden.</p>;
  const canRemove = deck().hasPermission("hosts.execute");
  return (
    <div data-testid={`resources-${kind}`}>
      <p className="mb-2 text-xs opacity-60">{text.hint}</p>
      {message && <p className="mb-2 text-sm opacity-90" role="status">{message}</p>}
      {rows.length === 0 && <p className="text-sm opacity-70">{text.empty}</p>}
      <ul className="divide-y divide-white/5 text-sm">
        {rows.map((row) => (
          <li key={row.key} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2" data-testid={`resource-${row.key}`}>
            <div className="min-w-0 flex-1">
              <p className={`break-words ${row.muted ? "opacity-70" : ""}`}>
                {row.title} {row.builtin && <Badge>eingebaut</Badge>}
              </p>
              <p className="text-xs opacity-60">{row.facts.join(" · ")}</p>
            </div>
            <p className="text-xs opacity-80 md:w-64">{usage(row)}</p>
            {canRemove && (
              <div className="w-20 shrink-0 text-right">
                {row.removable && waiting.has(row.key) && <span className="text-xs opacity-70">wartet auf Freigabe</span>}
                {row.removable && !waiting.has(row.key) && (
                  <Button small variant="danger" disabled={busy !== null} onClick={() => void remove(row)} ariaLabel={`${text.label}: ${row.title}`}>
                    {busy === row.key ? "…" : "Löschen"}
                  </Button>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

interface DockerHost { id: string; name: string; mode?: "full" | "operate" }

/** Ein Reiter mit Server-Auswahl; der Server folgt `?host=`, ohne Angabe der erste. */
export function ResourcesSection({ kind, hostId, onHost, empty }: {
  kind: ResourceKind;
  hostId: string | null;
  onHost: (id: string) => void;
  empty: ReactNode;
}): JSX.Element {
  return (
    <HostSection hostId={hostId} onHost={onHost} empty={empty}>
      {(id) => <ResourceList key={`${id}:${kind}`} hostId={id} kind={kind} />}
    </HostSection>
  );
}

export function HostSection({ hostId, onHost, empty, children }: {
  hostId: string | null;
  onHost: (id: string) => void;
  empty: ReactNode;
  children: (hostId: string) => ReactNode;
}): JSX.Element {
  const [hosts, setHosts] = useState<DockerHost[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    fetchJson<{ hosts: DockerHost[] }>("/ext/docker/hosts", { signal: controller.signal })
      .then((body) => { if (!controller.signal.aborted) setHosts(body.hosts ?? []); })
      .catch((err: unknown) => { if (!controller.signal.aborted) setError(errorMessage(err)); });
    return () => controller.abort();
  }, [reload]);

  if (error) return <LoadError what="Die Liste der Docker-Server" error={error} onRetry={() => setReload((n) => n + 1)} />;
  if (!hosts) return <p className="text-sm opacity-60">Lade die Docker-Server …</p>;
  const current = hosts.find((h) => h.id === hostId) ?? hosts[0];
  if (!current) return <>{empty}</>;
  return (
    <section>
      {hosts.length > 1 && (
        <label className="mb-3 flex items-center gap-2 text-sm">
          Server
          <select value={current.id} onChange={(e) => onHost(e.target.value)}
            className="rounded-lg border border-white/10 bg-black/25 px-2 py-1 text-sm outline-none focus:border-[var(--color-accent)]">
            {hosts.map((h) => <option key={h.id} value={h.id}>{h.name}{h.mode === "operate" ? " (nur bedienen)" : ""}</option>)}
          </select>
        </label>
      )}
      {current.mode === "operate" ? <OperateNote hostId={current.id} name={current.name} /> : children(current.id)}
    </section>
  );
}
