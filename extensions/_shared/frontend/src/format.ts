/** Gemeinsame Anzeige-Helfer der Erweiterungsseiten. */

/**
 * Größe in 1024er-Einheiten. Standard: unter 10 eine Nachkommastelle, sonst keine
 * ("8.5 GB", "45 MB"); `null`/`undefined` als "?". `fixed`: auch große Werte mit einer
 * Nachkommastelle ("45.5 GB"), ganze Werte ohne ("608 GB"), 0 als "0 B" -- so zeigt Proxmox Speicherplatz.
 */
export function formatBytes(n: number | null | undefined, options: { fixed?: boolean } = {}): string {
  if (n == null) return "?";
  const units = ["B", "KB", "MB", "GB", "TB"];
  if (options.fixed) {
    if (n <= 0) return "0 B";
    const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
    return `${(n / 1024 ** i).toFixed(1).replace(/\.0$/, "")} ${units[i]}`;
  }
  let value = n;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  const text = value.toFixed(value < 10 && i > 0 ? 1 : 0);
  return `${i >= 3 ? text.replace(/\.0$/, "") : text} ${units[i]}`;
}
