/**
 * Einstellungen -> Server & Zugaenge -> ein Server (`/settings/hosts/:hostId`): Allgemeines,
 * SSH-Zugang samt Einrichtungsbefehl, „Verbindung pruefen“, gemerkte Server-Schluessel, Gruppen
 * und Loeschen. Nach „Server anlegen“ (`?neu=1`) klappt das Erzeugen des Schluessels gleich auf.
 */
import { ArrowLeft } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { useCredentials, useHost, type HostOut } from "../../lib/hosts";
import { hostHealth } from "../../lib/overview";
import { AccessPanel } from "./hosts/AccessPanel";
import { ConnectionCheck } from "./hosts/ConnectionCheck";
import { DeleteHostButton } from "./hosts/deleteHost";
import { GroupMembership } from "./hosts/GroupMembership";
import { HostForm } from "./hosts/HostForm";
import { KnownKeys } from "./hosts/KnownKeys";
import { Badge, Button, Card, NoticeLine, PageHeader, type Notice } from "./ui";

const KIND_LABEL: Record<string, string> = { vm: "Virtuelle Maschine", lxc: "Container", hypervisor: "Proxmox-Knoten", node: "Knoten" };

function Summary({ host }: { host: HostOut }) {
  const rows: [string, string][] = [
    ["Kurzname", host.name],
    ["Anzeigename", host.display_name],
    ["Adresse", host.address],
    ["Betriebssystem", host.os_family === "windows" ? "Windows" : "Linux"],
  ];
  return (
    <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
      {rows.map(([k, v]) => (
        <div key={k} className="min-w-0">
          <dt className="text-xs text-white/45">{k}</dt>
          <dd className="break-all">{v}</dd>
        </div>
      ))}
      <div className="sm:col-span-2">
        <dt className="text-xs text-white/45">Markierungen</dt>
        <dd className="mt-0.5 flex flex-wrap gap-1.5">
          {host.tags.length === 0 && <span className="text-white/45">keine</span>}
          {host.tags.map((t) => <Badge key={t} tone={host.managed_tags.includes(t) ? "neutral" : "accent"}>{t}</Badge>)}
        </dd>
      </div>
      {host.provider_ext_id && (
        <p className="text-xs text-white/45 sm:col-span-2">
          Dieser Server wird automatisch von der Erweiterung „{host.provider_ext_id}“ eingelesen{host.kind ? ` (${KIND_LABEL[host.kind] ?? host.kind})` : ""}.
        </p>
      )}
    </dl>
  );
}

function HostDetail({ hostId }: { hostId: string }) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const host = useHost(hostId);
  const credentials = useCredentials(hostId);
  const [editing, setEditing] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  if (host.isLoading) return <p className="text-sm text-white/50">Lade Server …</p>;
  if (host.isError || !host.data) {
    return (
      <>
        <p role="alert" className="mb-3 text-sm text-red-300">Server nicht gefunden.</p>
        <Link to="/settings/hosts" className="text-sm underline underline-offset-2">Zurück zu „Server &amp; Zugänge“</Link>
      </>
    );
  }
  const h = host.data;
  const health = hostHealth(h.status);

  return (
    <>
      <Link to="/settings/hosts" className="mb-4 inline-flex items-center gap-1 text-xs text-white/50 hover:text-white">
        <ArrowLeft size={13} /> Alle Server
      </Link>
      <PageHeader
        title={h.display_name}
        description={`${h.name} · ${h.address}`}
        actions={
          <Link to={`/hosts/${h.id}`} className="inline-flex items-center gap-2 text-sm text-white/70 hover:text-white">
            <span className={`status-dot ${health}`} aria-hidden /> Server-Seite öffnen
          </Link>
        }
      />
      <NoticeLine notice={notice} />

      <Card
        title="Allgemein"
        footer={!editing ? <Button onClick={() => setEditing(true)}>Bearbeiten</Button> : undefined}
      >
        {editing ? <HostForm host={h} onSaved={() => setEditing(false)} onCancel={() => setEditing(false)} /> : <Summary host={h} />}
      </Card>

      <Card title="SSH-Zugang" description="Damit sich Nodvard Deck auf dem Server anmelden kann (Terminal, Updates, Überwachung).">
        {credentials.isLoading && <p className="text-sm text-white/50">Lade Zugänge …</p>}
        {credentials.isError && <p role="alert" className="text-sm text-red-300">Die Zugänge konnten nicht geladen werden.</p>}
        {credentials.data && <AccessPanel host={h} credentials={credentials.data} autoOpen={params.get("neu") === "1"} />}
      </Card>

      <Card title="Verbindung prüfen" description="Testet Schritt für Schritt, ob sich Nodvard Deck mit dem Server verbinden kann, und sagt, was noch fehlt.">
        <ConnectionCheck hostId={h.id} />
      </Card>

      <Card title="Server-Schlüssel" description="Den Fingerabdruck des Servers merkt sich Nodvard Deck bei der ersten Prüfung und lehnt danach jeden anderen ab.">
        <KnownKeys host={h} />
      </Card>

      <Card title="Gruppen" description="Mit Gruppen lässt „Skripte“ ein Skript auf mehreren Servern laufen.">
        <GroupMembership hostId={h.id} />
      </Card>

      <Card title="Gefahrenbereich">
        <p className="mb-3 text-sm text-white/60">
          Löscht den Server aus Nodvard Deck – samt SSH-Zugang und gemerktem Server-Schlüssel. Der Server selbst bleibt unberührt.
        </p>
        <DeleteHostButton host={h} onDeleted={() => navigate("/settings/hosts")} onMessage={setNotice} />
      </Card>
    </>
  );
}

export function HostDetailSettings() {
  const { hostId = "" } = useParams();
  // Wie bei der Server-Seite: ein anderer Server bekommt einen frischen Zustand.
  return <HostDetail key={hostId} hostId={hostId} />;
}
