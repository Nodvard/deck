/** Gemeinsame Anzeige-Helfer der system-Seite (Zustand + Live-Ansicht). */

export { formatBytes } from "../../../_shared/frontend/src/format";

export function formatUptime(seconds: number | null): string {
  if (seconds == null) return "?";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return days > 0 ? `${days} d ${hours} h` : hours > 0 ? `${hours} h ${minutes} min` : `${minutes} min`;
}
