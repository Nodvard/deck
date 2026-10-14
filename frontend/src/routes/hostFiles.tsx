/**
 * „Dateien“ auf der Server-Seite: Knopf zum Dateimanager dieses Servers, wichtige Orte als Links und, wenn es keine
 * Dateiquelle gibt, ein Satz dazu. Ob es eine Quelle gibt, sagt `GET /files/sources` (zwischengespeichert); die
 * SFTP-Quelle liefert das Modul „Terminal“, der Kern zeigt sie nur Nutzern mit dem Recht, Befehle auszuführen.
 */
import { useQuery } from "@tanstack/react-query";
import { FolderOpen } from "lucide-react";
import { Link } from "react-router-dom";

import { type ExtensionInfo } from "../lib/firstSteps";
import { isSshCredential } from "../lib/hosts";
import { api } from "../lib/api";
import { type HostOut } from "../lib/overview";
import { useAuthStore } from "../state/auth";

/**
 * Das Modul, das die Dateien eines Servers per SSH liefert. Eine allgemeine Kennzeichnung
 * über die API gibt es (noch) nicht.
 */
const SFTP_EXTENSION_ID = "terminal";
/** So viele Orte stehen höchstens als Links auf der Server-Seite; alle zeigt der Dateimanager. */
const MAX_PLACES = 4;

interface FileSourceInfo {
  source_id: string;
  /** Server hinter der Quelle; fehlt bei Quellen ohne Server und bei älteren Versionen. */
  host_id?: string | null;
}

interface FilePlace {
  label: string;
  path: string;
  icon: string;
  hint: string | null;
}

export function useHostFiles(hostId: string) {
  const canRead = useAuthStore((s) => s.hasPermission("files.read"));
  const sources = useQuery({
    queryKey: ["files", "sources"],
    queryFn: () => api.get<FileSourceInfo[]>("/files/sources"),
    enabled: canRead,
    staleTime: 10_000,
    retry: false,
  });
  const hasSource = (sources.data ?? []).some((s) => s.host_id === hostId);
  const places = useQuery({
    queryKey: ["files", "places", hostId],
    queryFn: () => api.get<FilePlace[]>(`/files/places?host_id=${encodeURIComponent(hostId)}`),
    enabled: canRead && hasSource,
    staleTime: 60_000,
    retry: false,
  });
  return { canRead, loaded: sources.isSuccess, hasSource, places: places.data ?? [] };
}

export type HostFiles = ReturnType<typeof useHostFiles>;

/** Knopf „Dateien“ neben „Terminal“; ohne Quelle gibt es ihn nicht (der Grund steht in `HostFilesNote`). */
export function HostFilesButton({ files, hostId }: { files: HostFiles; hostId: string }) {
  if (!files.hasSource) return null;
  return (
    <Link
      to={`/files?host=${encodeURIComponent(hostId)}`}
      className="flex items-center gap-1.5 rounded-lg bg-white/10 px-3 py-1.5 text-xs font-medium hover:bg-white/15"
    >
      <FolderOpen size={14} /> Dateien
    </Link>
  );
}

/**
 * Warum es für diesen Server keine Dateiquelle gibt, in einem Satz, mit dem Weg, es zu
 * beheben (falls man ihn gehen darf).
 */
function missingSourceReason(
  host: HostOut,
  can: { execute: boolean; writeHosts: boolean; manageExtensions: boolean },
  extensions: ExtensionInfo[] | undefined,
): { text: string; to?: string; action?: string } {
  if (!can.execute) {
    return { text: "Dateien auf Servern siehst du nur, wenn du auch Befehle auf Servern ausführen darfst." };
  }
  if (!host.credential || !isSshCredential(host.credential)) {
    return {
      text: "Dateien zeigt Nodvard Deck per SSH. Dafür braucht dieser Server einen SSH-Zugang.",
      ...(can.writeHosts ? { to: `/settings/hosts/${host.id}`, action: "SSH-Zugang einrichten" } : {}),
    };
  }
  const ext = extensions?.find((e) => e.id === SFTP_EXTENSION_ID);
  if (ext && ext.state !== "enabled") {
    return {
      text: `Dateien auf Servern kommen vom Modul „${ext.name ?? SFTP_EXTENSION_ID}“. Es ist ausgeschaltet oder gestört.`,
      ...(can.manageExtensions ? { to: "/settings/extensions", action: "Module ansehen" } : {}),
    };
  }
  return { text: "Für diesen Server gibt es gerade keine Dateiquelle." };
}

/** Unter dem Kopf der Server-Seite: die ersten wichtigen Orte als Links, oder der Satz, warum es keine Dateien gibt. */
export function HostFilesNote({
  files, host, canExecute, canWriteHosts, canManageExtensions, extensions,
}: {
  files: HostFiles;
  host: HostOut;
  canExecute: boolean;
  canWriteHosts: boolean;
  canManageExtensions: boolean;
  extensions: ExtensionInfo[] | undefined;
}) {
  if (!files.canRead || !files.loaded) return null;
  if (files.hasSource) {
    const shown = files.places.slice(0, MAX_PLACES);
    if (shown.length === 0) return null;
    return (
      <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-xs text-white/55" data-testid="host-files-places">
        <span>Wichtige Orte:</span>
        {shown.map((place) => (
          <Link
            key={place.path}
            to={`/files?host=${encodeURIComponent(host.id)}&path=${encodeURIComponent(place.path)}`}
            title={place.hint ? `${place.hint} (${place.path})` : place.path}
            className="rounded-full bg-white/10 px-2.5 py-1 text-white/80 hover:bg-white/15"
          >
            {place.label}
          </Link>
        ))}
      </div>
    );
  }
  const why = missingSourceReason(host, { execute: canExecute, writeHosts: canWriteHosts, manageExtensions: canManageExtensions }, extensions);
  return (
    <p data-testid="host-files-hint" className="mt-3 text-xs text-white/55">
      {why.text}
      {why.to && (
        <>
          {" "}
          <Link to={why.to} className="text-white/75 underline underline-offset-2 hover:text-white">{why.action}</Link>
        </>
      )}
    </p>
  );
}
