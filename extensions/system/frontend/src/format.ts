/** Gemeinsame Anzeige-Helfer der system-Seite (Zustand + Live-Ansicht). */

export { formatBytes } from "../../../_shared/frontend/src/format";

export function formatUptime(seconds: number | null): string {
  if (seconds == null) return "?";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return days > 0 ? `${days} d ${hours} h` : hours > 0 ? `${hours} h ${minutes} min` : `${minutes} min`;
}

/** Hinweis unter den Dateisystemen, passend dazu, wie das Lesen der Platten endete (`df_status`). */
export function dfFailedText(status?: string | null): string {
  if (status === "timeout") return "Die Plattenbelegung konnte nicht gelesen werden: Der Server hat nicht rechtzeitig geantwortet, wahrscheinlich hängt ein Laufwerk.";
  if (status === "unavailable") return "Die Plattenbelegung konnte auf dem Server nicht gemessen werden.";
  return "Die Plattenbelegung konnte nicht gelesen werden.";
}
