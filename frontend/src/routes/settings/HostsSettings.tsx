/**
 * Einstellungen -> Server & Zugaenge (`/settings/hosts`): alle Server auf einen Blick, neue
 * anlegen, den SSH-Zugang pro Server einrichten und pruefen. Gedacht fuer das Handy
 * zuerst: Karten statt Tabellen, Knoepfe brechen um.
 */
import { useQueryClient } from "@tanstack/react-query";
import { Plus, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { DemoSeedButton } from "../../components/DemoSeedButton";
import { api, ApiError } from "../../lib/api";
import { accessLabel, checkSummary, refreshHosts, type ConnectionCheck, type HostOut } from "../../lib/hosts";
import { hostHealth, useHosts } from "../../lib/overview";
import { GroupsCard } from "./hosts/GroupsCard";
import { HostForm } from "./hosts/HostForm";
import { ReachabilityCard } from "./hosts/ReachabilityCard";
import { Badge, Button, Card, PageHeader, inputClass } from "./ui";

/** Ab so vielen Servern gibt es ein Suchfeld. */
const SEARCH_FROM = 9;

type RowCheck = { state: "busy" } | { state: "done"; result: ConnectionCheck } | { state: "error"; text: string };

function AccessBadge({ host }: { host: HostOut }) {
  if (!host.credential) return <Badge tone="warn">Kein Zugang</Badge>;
  return <Badge tone="good">{accessLabel(host.credential)}</Badge>;
}

function CheckLine({ check, hostId }: { check: RowCheck; hostId: string }) {
  if (check.state === "busy") return <p className="text-xs text-white/50">Prüfe … (bis zu 30 Sekunden)</p>;
  if (check.state === "error") return <p role="alert" className="text-xs text-red-300">{check.text}</p>;
  const tone = check.result.ok ? "text-emerald-300" : check.result.items.some((i) => i.status === "fail") ? "text-red-300" : "text-amber-300";
  return (
    <p role="status" className={`text-xs ${tone}`}>
      {checkSummary(check.result)}
      {!check.result.ok && (
        <>
          {" · "}
          <Link to={`/settings/hosts/${hostId}`} className="underline underline-offset-2">Details ansehen</Link>
        </>
      )}
    </p>
  );
}

function HostRow({ host }: { host: HostOut }) {
  const queryClient = useQueryClient();
  const [check, setCheck] = useState<RowCheck | null>(null);
  const health = hostHealth(host.status);

  async function runCheck() {
    setCheck({ state: "busy" });
    try {
      const result = await api.post<ConnectionCheck>(`/hosts/${host.id}/check`);
      setCheck({ state: "done", result });
      await refreshHosts(queryClient);
    } catch (err) {
      setCheck({ state: "error", text: err instanceof ApiError ? err.message : String(err) });
    }
  }

  return (
    <li className="panel flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="flex items-center gap-2 font-medium">
          <span className={`status-dot ${health}`} aria-hidden />
          <span className="break-words">{host.display_name}</span>
        </p>
        <p className="mt-0.5 break-all text-xs text-white/50">{host.name} · {host.address}</p>
        <div className="mt-2 flex flex-wrap gap-1.5">
          <AccessBadge host={host} />
          {host.provider_ext_id && <Badge>Automatisch eingelesen</Badge>}
        </div>
        {check && <div className="mt-2"><CheckLine check={check} hostId={host.id} /></div>}
      </div>
      <div className="flex flex-none flex-wrap gap-2">
        <Link
          to={`/settings/hosts/${host.id}`}
          className="inline-flex items-center justify-center rounded-lg border border-white/10 bg-white/[0.06] px-3 py-1.5 text-sm font-medium hover:bg-white/[0.12]"
        >
          Einrichten
        </Link>
        <Button busy={check?.state === "busy"} onClick={() => void runCheck()}>Prüfen</Button>
      </div>
    </li>
  );
}

export function HostsSettings() {
  const navigate = useNavigate();
  const hosts = useHosts();
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState("");

  const sorted = useMemo(
    () => [...(hosts.data ?? [])].sort((a, b) => a.display_name.localeCompare(b.display_name, "de")),
    [hosts.data],
  );
  const needle = query.trim().toLowerCase();
  const shown = needle
    ? sorted.filter((h) => [h.display_name, h.name, h.address].some((v) => v.toLowerCase().includes(needle)))
    : sorted;

  const addButton = (
    <Button variant="primary" onClick={() => setAdding(true)}>
      <Plus size={15} /> Server hinzufügen
    </Button>
  );

  return (
    <>
      <PageHeader
        title="Server & Zugänge"
        description="Hier legst du Server an und richtest den SSH-Zugang ein, mit dem sich Nodvard Deck verbindet."
        actions={!adding ? addButton : undefined}
      />

      {adding && (
        <Card title="Neuen Server anlegen" description="Danach richtest du den SSH-Zugang ein.">
          <HostForm
            onSaved={(host) => navigate(`/settings/hosts/${host.id}?neu=1`)}
            onCancel={() => setAdding(false)}
          />
        </Card>
      )}

      {hosts.isLoading && <p className="text-sm text-white/50">Lade Server …</p>}
      {hosts.isError && <p role="alert" className="text-sm text-red-300">Die Server konnten nicht geladen werden.</p>}

      {hosts.data && hosts.data.length === 0 && !adding && (
        <div className="panel mb-5 p-6 text-center">
          <p className="text-base font-medium">Noch keine Server.</p>
          <p className="mx-auto mt-2 max-w-md text-sm text-white/55">
            Leg den ersten an – zum Beispiel den Raspberry Pi, auf dem Nodvard Deck läuft, ein NAS oder eine Debian-VM. Dafür reichen
            Name, Adresse und ein SSH-Zugang.
          </p>
          <div className="mt-4 flex flex-wrap justify-center gap-2">
            {addButton}
            <DemoSeedButton />
          </div>
          <p className="mt-4 text-xs text-white/45">
            Nutzt du Proxmox? Dann trägst du nur dessen Zugang ein, Knoten und VMs erscheinen danach von selbst:{" "}
            <Link to="/settings/extensions/proxmox" className="underline underline-offset-2 hover:text-white">Proxmox einrichten</Link>
          </p>
        </div>
      )}

      {hosts.data && hosts.data.length >= SEARCH_FROM && (
        <div className="relative mb-3">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-white/40" />
          <input
            type="search" aria-label="Server suchen" placeholder="Server suchen …" value={query}
            onChange={(e) => setQuery(e.target.value)} className={`${inputClass} pl-9`}
          />
        </div>
      )}

      {shown.length > 0 && (
        <ul className="mb-5 space-y-3">
          {shown.map((h) => <HostRow key={h.id} host={h} />)}
        </ul>
      )}
      {hosts.data && hosts.data.length > 0 && shown.length === 0 && (
        <p className="mb-5 text-sm text-white/50">Kein Server passt zur Suche.</p>
      )}

      <ReachabilityCard />
      <GroupsCard />
    </>
  );
}
