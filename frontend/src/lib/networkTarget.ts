function localIPv4(host: string): boolean {
  const parts = host.split(".").map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) return false;
  const [a, b] = parts;
  return a === 10 || a === 127 || (a === 172 && b >= 16 && b <= 31)
    || (a === 192 && b === 168) || (a === 169 && b === 254) || (a === 100 && b >= 64 && b <= 127);
}

function localIPv6(host: string): boolean {
  const [left, right] = host.split("::");
  const start = left ? left.split(":") : [];
  const end = right ? right.split(":") : [];
  const groups = right === undefined ? start : [...start, ...Array(8 - start.length - end.length).fill("0"), ...end];
  const parts = groups.map((part) => parseInt(part, 16));
  if (parts.length !== 8 || parts.some((part) => !Number.isInteger(part))) return false;
  if (parts.slice(0, 7).every((part) => part === 0) && parts[7] === 1) return true;
  if ((parts[0] & 0xfe00) === 0xfc00 || (parts[0] & 0xffc0) === 0xfe80) return true;
  if (parts.slice(0, 5).every((part) => part === 0) && parts[5] === 0xffff) {
    return localIPv4(`${parts[6] >> 8}.${parts[6] & 255}.${parts[7] >> 8}.${parts[7] & 255}`);
  }
  return false;
}

/** Der Hinweis bewertet nur die eingetragene Adresse, ohne DNS-Abfrage. */
export function leavesLocalNetwork(value: unknown): boolean {
  if (typeof value !== "string" || !value.trim()) return false;
  try {
    const url = new URL(value);
    if (url.protocol !== "http:" && url.protocol !== "https:") return false;
    const host = url.hostname.toLowerCase().replace(/\.$/, "").replace(/^\[|\]$/g, "");
    if (!host || host === "localhost" || host.endsWith(".localhost") || host.endsWith(".ts.net")) return false;
    return !(host.includes(":") ? localIPv6(host) : localIPv4(host));
  } catch {
    return false;
  }
}
