/**
 * Karte „Vertrauenswürdiger Proxy“ (Einstellungen, System): Reverse-Proxys (Nginx Proxy Manager, Traefik, Caddy),
 * denen Nodvard Deck Absenderadresse und HTTPS glaubt (`system.trusted_proxies`, ändern nur der Owner). Eine Zeile
 * zeigt, wie die eigene Anfrage ankommt (`GET /system/connection`).
 */
import { AlertTriangle } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../../lib/api";
import { useAuthStore } from "../../state/auth";
import { Button, Card, Field, NoticeLine, errorText, inputClass, type Notice } from "./ui";

export const KEY_TRUSTED_PROXIES = "system.trusted_proxies";
export const ENV_TRUSTED_PROXIES = "NODVARD_DECK_TRUSTED_PROXIES";

interface SettingRow {
  key: string;
  value: unknown;
}

/** Antwort von GET /system/connection. */
export interface ConnectionInfo {
  peer: string | null;
  client: string | null;
  scheme: string;
  via_trusted_proxy: boolean;
  forwarded_headers: boolean;
  trusted_proxies: string[];
  trusted_proxies_source: "setting" | "env";
}

/** Text des Feldes -> Liste (eine Adresse pro Zeile; Kommas und Leerzeichen trennen ebenso). */
export function parseEntries(text: string): string[] {
  return text.split(/[\s,]+/).map((part) => part.trim()).filter(Boolean);
}

/**
 * Ab so vielen Adressen in einem Netz warnt die Karte schon beim Tippen (IPv4 Präfix unter 28, IPv6 unter 124), auch
 * wenn der Server es annimmt: Jedes Gerät darin dürfte Nodvard Deck eine falsche Absenderadresse vorgeben.
 */
export const WIDE_NETWORK_ADDRESSES = 16;

/** Präfixlänge eines Eintrags wie `172.18.0.0/16`; `null` bei einer einzelnen Adresse oder einem unlesbaren Eintrag. */
function networkPrefix(entry: string): { prefix: number; max: number } | null {
  const slash = entry.indexOf("/");
  if (slash === -1) return null;
  const host = entry.slice(0, slash);
  const prefixText = entry.slice(slash + 1);
  if (!/^[0-9a-fA-F:.]+$/.test(host) || !/^\d{1,3}$/.test(prefixText)) return null;
  const max = host.includes(":") ? 128 : 32;
  const prefix = Number(prefixText);
  return prefix <= max ? { prefix, max } : null;
}

/** Die Einträge, die ein Netz mit mehr als 16 Adressen meinen (auch in privaten Netzen). */
export function wideNetworks(entries: string[]): string[] {
  const wide = entries.filter((entry) => {
    const parsed = networkPrefix(entry);
    return parsed !== null && parsed.max - parsed.prefix > Math.log2(WIDE_NETWORK_ADDRESSES);
  });
  return Array.from(new Set(wide));
}

export function wideNetworkWarning(entry: string): string {
  return (
    `„${entry}“ umfasst mehr als ${WIDE_NETWORK_ADDRESSES} Adressen. Jedes Gerät darin könnte Nodvard Deck eine falsche ` +
    "Adresse vorgeben. Trag besser nur die Adresse deines Proxys ein."
  );
}

function sameList(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((value, i) => value === b[i]);
}

/** Die Zeile „Deine Anfrage kommt gerade …“. */
export function connectionLine(info: ConnectionInfo): string {
  const scheme = info.scheme === "https" ? "https" : "http";
  if (info.via_trusted_proxy && info.peer && info.client && info.client !== info.peer) {
    return `Deine Anfrage kommt gerade von ${info.client} über ${scheme}, weitergeleitet von ${info.peer}.`;
  }
  if (info.via_trusted_proxy && info.peer) {
    return `Deine Anfrage kommt gerade über deinen Proxy ${info.peer} über ${scheme}.`;
  }
  if (info.peer) return `Deine Anfrage kommt gerade von ${info.peer} (direkt) über ${scheme}.`;
  return `Deine Anfrage kommt gerade über eine Verbindung ohne IP-Adresse über ${scheme}.`;
}

export function TrustedProxyCard({ settings }: { settings: SettingRow[] }): JSX.Element {
  const stored = settings.find((r) => r.key === KEY_TRUSTED_PROXIES)?.value;
  const initial = useMemo(() => (Array.isArray(stored) ? stored.filter((v): v is string => typeof v === "string") : []), [stored]);
  const [saved, setSaved] = useState<string[]>(initial);
  const [text, setText] = useState(initial.join("\n"));
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [connection, setConnection] = useState<ConnectionInfo | null>(null);
  const [connectionFailed, setConnectionFailed] = useState(false);

  const loadConnection = useCallback(() => {
    api.get<ConnectionInfo>("/system/connection")
      .then((info) => {
        setConnection(info);
        setConnectionFailed(false);
      })
      .catch(() => setConnectionFailed(true));
  }, []);

  useEffect(() => {
    loadConnection();
  }, [loadConnection]);

  const isOwner = useAuthStore((s) => Boolean(s.user?.is_owner));
  // Ist die Umgebungsvariable NODVARD_DECK_TRUSTED_PROXIES gesetzt, zeigt die Karte deren Liste nur an.
  const fromEnv = connection?.trusted_proxies_source === "env";
  const locked = fromEnv || !isOwner;
  const entries = parseEntries(text);
  const dirty = !sameList(entries, saved);
  const peer = connection?.peer ?? null;
  // Trägt die Anfrage Angaben eines Proxys (`forwarded_headers`), dessen Adresse noch nicht eingetragen ist, schlägt
  // die Karte genau diese Adresse vor (ungespeichert).
  const suggest = Boolean(connection && peer && connection.forwarded_headers && !connection.via_trusted_proxy && !locked);
  const suggestedAlready = peer !== null && entries.includes(peer);
  const wide = wideNetworks(entries);

  async function save() {
    setBusy(true);
    setNotice(null);
    try {
      const result = await api.put<SettingRow>(`/settings/${KEY_TRUSTED_PROXIES}`, { value: entries });
      const next = Array.isArray(result?.value) ? (result.value as string[]) : entries;
      setSaved(next);
      setText(next.join("\n"));
      setNotice({ kind: "ok", text: "Gespeichert. Ab der nächsten Anfrage gilt die neue Liste." });
      loadConnection();
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  function addPeer() {
    if (!peer || entries.includes(peer)) return;
    setText([...entries, peer].join("\n"));
  }

  return (
    <Card
      title="Vertrauenswürdiger Proxy"
      description="Nur nötig, wenn du Nodvard Deck über einen Reverse-Proxy wie Nginx Proxy Manager, Traefik oder Caddy aufrufst. Dann zählen die echten Adressen deiner Geräte und HTTPS."
      footer={<Button variant="primary" busy={busy} disabled={!dirty || locked} onClick={() => void save()}>Speichern</Button>}
    >
      <NoticeLine notice={notice} />

      <div className="space-y-4">
        {connection && <p className="text-sm text-white/80">{connectionLine(connection)}</p>}
        {!connection && !connectionFailed && <p className="text-sm text-white/50">Prüfe die Verbindung …</p>}
        {connectionFailed && <p className="text-sm text-white/50">Wie deine Anfrage gerade ankommt, ließ sich nicht prüfen.</p>}

        {suggest && (
          <div className="flex flex-wrap items-start gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
            <AlertTriangle size={16} className="mt-0.5 flex-none" />
            <p className="min-w-0 flex-1">
              Deine Anfrage kommt offenbar über einen Proxy mit der Adresse {peer}, der hier noch nicht eingetragen ist.
              Ist das dein Proxy, trag diese Adresse ein. Sonst lass es: Nur dein eigener Proxy gehört in die Liste.
              Ist es das Gateway eines Docker-Netzes (oft eine Adresse mit .1 am Ende), nur dann, wenn Port 8080 nicht
              nach außen freigegeben ist, sonst könnten andere Geräte ihre Adresse fälschen.
            </p>
            {suggestedAlready ? (
              <span className="text-xs text-amber-200/80">Eingetragen, noch nicht gespeichert.</span>
            ) : (
              <Button onClick={addPeer}>Diese Adresse eintragen</Button>
            )}
          </div>
        )}

        <Field
          label="Vertrauenswürdiger Proxy"
          hint="Am besten nur die Adresse deines Proxys, eine pro Zeile, zum Beispiel 172.18.0.5. Ein kleines Netz geht auch, zum Beispiel 172.18.0.0/28. Leer lassen, wenn kein Proxy davor sitzt."
        >
          <textarea
            rows={3}
            value={text}
            onChange={(e) => setText(e.target.value)}
            disabled={locked || busy}
            spellCheck={false}
            aria-label="Vertrauenswürdiger Proxy"
            className={`${inputClass} font-mono`}
          />
        </Field>
        {wide.length > 0 && (
          <div
            role="note"
            data-testid="wide-network-warning"
            className="flex items-start gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-100"
          >
            <AlertTriangle size={16} className="mt-0.5 flex-none" />
            <div className="min-w-0 flex-1 space-y-1">
              {wide.map((entry) => (
                <p key={entry}>{wideNetworkWarning(entry)}</p>
              ))}
            </div>
          </div>
        )}
        {fromEnv && (
          <p className="text-xs text-white/50">
            Festgelegt über die Umgebungsvariable {ENV_TRUSTED_PROXIES}. Ändern geht nur dort.
          </p>
        )}
        {!fromEnv && !isOwner && (
          <p className="text-xs text-white/50">Ändern kann diese Liste nur der Inhaber (Owner) dieser Installation.</p>
        )}
        <p className="text-xs text-white/40">
          Nur von diesen Adressen glaubt Nodvard Deck die Angaben, wer wirklich anfragt und ob die Verbindung verschlüsselt
          war. Das braucht die Sperre nach zu vielen falschen Anmeldungen, das Protokoll und die Anmeldung über HTTPS. Trag
          nie ein ganzes Netz ein, aus dem auch normale Geräte kommen: Jedes eingetragene Gerät könnte sich sonst als ein
          anderes ausgeben.
        </p>
      </div>
    </Card>
  );
}
