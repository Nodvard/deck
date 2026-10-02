/**
 * Vergleichsform einer Zieladresse -- dieselbe Rechnung wie `sdk/python/nodvard_sdk/addresses.py`
 * (beide pruefen dieselben Beispiele aus `sdk/python/tests/vectors/same_target.json`).
 *
 * Zugangsdaten gehoeren zu dem Server, bei dem sie eingegeben wurden. Gleich bleiben:
 * Gross-/Kleinschreibung von Schema und Rechnername, der Standardport (`:80` bei http, `:443` bei
 * https), Schraegstriche am Ende des Pfads, ein Fragment (`#...`) und ein fehlendes Schema
 * (`pi.hole` gilt wie `http://pi.hole`). Nicht gleich sind ein anderer Pfad, eine andere Abfrage
 * oder andere Anmeldedaten in der Adresse. Was sich nicht als Adresse lesen laesst, bleibt, wie es
 * ist (ohne Leerraum an den Raendern), und gilt damit im Zweifel als verschieden.
 */

const DEFAULT_PORTS: Record<string, number> = { http: 80, https: 443 };
const PARTS = /^([A-Za-z][A-Za-z0-9+.-]*):\/\/([^/?#]*)([^?#]*)(?:\?([^#]*))?(?:#.*)?$/;
const IPV_FUTURE = /^v[a-fA-F0-9]+\..+$/;

/** IPv4 so streng wie Python `ipaddress` (vier Zahlen bis 255, ohne führende Nullen). */
function isIPv4(text: string): boolean {
  const octets = text.split(".");
  return octets.length === 4 && octets.every((o) => /^[0-9]{1,3}$/.test(o) && (o === "0" || !o.startsWith("0")) && Number(o) <= 255);
}

/** IPv6 (gern mit Zone `%eth0`) nach denselben Regeln wie Python `ipaddress.IPv6Address`. */
function isIPv6(text: string): boolean {
  const percent = text.indexOf("%");
  if (percent >= 0) {
    const zone = text.slice(percent + 1);
    if (zone === "" || zone.includes("%")) return false;
    text = text.slice(0, percent);
  }
  const parts = text.split(":");
  if (text === "" || parts.length < 3) return false;
  if (parts[parts.length - 1].includes(".")) {
    if (!isIPv4(parts.pop() as string)) return false;
    parts.push("0", "0");
  }
  if (parts.length > 9) return false;
  let skip: number | null = null;
  for (let i = 1; i < parts.length - 1; i++) {
    if (parts[i] !== "") continue;
    if (skip !== null) return false;
    skip = i;
  }
  let hi: number;
  let lo: number;
  if (skip !== null) {
    hi = skip;
    lo = parts.length - skip - 1;
    if (parts[0] === "" && --hi !== 0) return false;
    if (parts[parts.length - 1] === "" && --lo !== 0) return false;
    if (8 - (hi + lo) < 1) return false;
  } else {
    if (parts.length !== 8 || parts[0] === "" || parts[parts.length - 1] === "") return false;
    hi = parts.length;
    lo = 0;
  }
  const hextets = [...parts.slice(0, hi), ...parts.slice(parts.length - lo)];
  return hextets.every((h) => /^[0-9A-Fa-f]{1,4}$/.test(h));
}

/** Was Python in eckigen Klammern annimmt: eine IPv6-Adresse oder eine Zukunftsform `v1.x`. */
function bracketHostValid(host: string): boolean {
  return host.startsWith("v") ? IPV_FUTURE.test(host) : isIPv6(host);
}

/** Rechnername klein, eine Zone hinter `%` bleibt, wie sie ist (wie Python `hostname`). */
function lowerHost(host: string): string {
  const percent = host.indexOf("%");
  return percent >= 0 ? host.slice(0, percent).toLowerCase() + host.slice(percent) : host.toLowerCase();
}

/** Wie Python `_checknetloc`: Zeichen, die erst nach der Unicode-Vereinheitlichung (NFKC) zu `/?#@:`
 * werden (z. B. `℀`), machen die Adresse unlesbar. */
function netlocSurvivesNfkc(netloc: string): boolean {
  if (!/[^\x00-\x7f]/.test(netloc)) return true;
  const n = netloc.replace(/[@:#?]/g, "");
  const normalized = n.normalize("NFKC");
  return n === normalized || !/[/?#@:]/.test(normalized);
}

export function targetForm(value: unknown): unknown {
  if (value === null || value === undefined) return "";
  if (typeof value !== "string") return value;
  const text = value.trim();
  if (text === "") return "";
  // Wie Python `urlsplit`: Steuerzeichen am Anfang sowie Tabulatoren und Zeilenumbrüche fallen vor dem Zerlegen weg.
  const candidate = (text.includes("://") ? text : `http://${text}`).replace(/^[\x00-\x20]+/, "").replace(/[\t\r\n]/g, "");
  const match = PARTS.exec(candidate);
  if (!match) return text;
  const scheme = match[1].toLowerCase();
  const netloc = match[2];
  const path = match[3];
  const query = match[4] ?? "";

  if (netloc.includes("[") !== netloc.includes("]")) return text;
  if (!netlocSurvivesNfkc(netloc)) return text;

  const at = netloc.lastIndexOf("@");
  const userinfo = at >= 0 ? `${netloc.slice(0, at)}@` : "";
  const hostinfo = at >= 0 ? netloc.slice(at + 1) : netloc;
  let host: string;
  let portText: string;
  if (hostinfo.includes("[")) {
    // Nichts vor der Klammer, hinter der schließenden Klammer höchstens `:Port`.
    if (!hostinfo.startsWith("[")) return text;
    const close = hostinfo.indexOf("]");
    host = close >= 0 ? hostinfo.slice(1, close) : hostinfo.slice(1);
    const rest = close >= 0 ? hostinfo.slice(close + 1) : "";
    if (rest !== "" && !rest.startsWith(":")) return text;
    if (!bracketHostValid(host)) return text;
    portText = rest.slice(1);
  } else {
    const colon = hostinfo.indexOf(":");
    host = colon >= 0 ? hostinfo.slice(0, colon) : hostinfo;
    portText = colon >= 0 ? hostinfo.slice(colon + 1) : "";
    // Klammern nur in den Anmeldedaten: Python prüft den Rechnernamen dann wie einen in Klammern.
    if (netloc.includes("[") && !bracketHostValid(host)) return text;
  }
  if (host === "") return text;
  let port: number | null = null;
  if (portText !== "") {
    if (!/^[0-9]+$/.test(portText)) return text;
    port = Number.parseInt(portText, 10);
    if (port > 65535) return text;
  }

  const shownHost = host.includes(":") ? `[${lowerHost(host)}]` : lowerHost(host);
  const shownPort = port !== null && port !== DEFAULT_PORTS[scheme] ? `:${port}` : "";
  return `${scheme}://${userinfo}${shownHost}${shownPort}${path.replace(/\/+$/, "")}${query ? `?${query}` : ""}`;
}

export function sameTarget(a: unknown, b: unknown): boolean {
  return JSON.stringify(targetForm(a)) === JSON.stringify(targetForm(b));
}
