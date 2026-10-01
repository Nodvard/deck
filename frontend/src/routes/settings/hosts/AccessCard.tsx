/** Kleine Zugangs-Karte fuer die Server-Seite: wie sich Nodvard Deck anmeldet, „Verbindung pruefen“, Link zur Einrichtung. */
import { Link } from "react-router-dom";

import { accessLabel, type HostOut } from "../../../lib/hosts";
import { Badge } from "../ui";
import { ConnectionCheck } from "./ConnectionCheck";

export function AccessCard({ host }: { host: HostOut }) {
  return (
    <section className="panel mt-4 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-semibold">Zugang</h2>
          {host.credential ? <Badge tone="good">{accessLabel(host.credential)}</Badge> : <Badge tone="warn">Kein Zugang</Badge>}
        </div>
        <Link to={`/settings/hosts/${host.id}`} className="text-sm text-white/70 underline underline-offset-2 hover:text-white">
          Zugang einrichten
        </Link>
      </div>
      <ConnectionCheck hostId={host.id} />
    </section>
  );
}
