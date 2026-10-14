/**
 * Fingerabdruck des Zertifikats einer Verbindung (Proxmox VE, Proxmox-Backups): ansehen, mit dem Server vergleichen und
 * merken, auch nach einem Wechsel (`GET`/`POST /ext/<id>/connections/{name}/certificate`). Gespeichert wird nur, was der
 * Server gerade zeigt; sonst kommt 409 mit dem aktuellen Wert, den die Ansicht zum neuen Vergleich zeigt.
 */
import { useEffect, useState } from "react";

import { authedFetch, errorFromBody } from "./api";
import { Button } from "./ui";

export interface ConnectionCertificateInfo {
  sha256: string;
  trusted: boolean;
  subject?: string | null;
  not_after?: string | null;
  stored: string | null;
  stored_invalid: boolean;
  matches: boolean;
}

/** Was die Liste der Verbindungen zum Zertifikat weiß (ohne den Server zu fragen). */
export interface CertificateFlags {
  tls_insecure_skip_verify: boolean;
  tls_fingerprint_sha256?: string | null;
  tls_fingerprint_invalid?: boolean;
}

/** Kurzer Stand für die Tabelle der Verbindungen. */
export function CertificateStatus({ connection }: { connection: CertificateFlags }) {
  if (connection.tls_fingerprint_invalid) {
    return <span className="text-red-300">Fingerabdruck ungültig</span>;
  }
  if (connection.tls_fingerprint_sha256) {
    return <span className="text-emerald-300" title={connection.tls_fingerprint_sha256}>Fingerabdruck gemerkt</span>;
  }
  if (connection.tls_insecure_skip_verify) {
    return <span className="text-amber-300">wird nicht geprüft</span>;
  }
  return <span className="opacity-70">normal geprüft</span>;
}

function formatDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleDateString("de-DE");
}

/** Wo der Fingerabdruck in Proxmox steht (wie im Dialog „Mit Passwort verbinden“). */
export const PROXMOX_FINGERPRINT_HINT =
  "In Proxmox links den Knoten anklicken → System → Zertifikate, Spalte „Fingerabdruck“ – bei pveproxy-ssl.pem, " +
  "falls es die Zeile gibt, sonst bei pve-ssl.pem.";

export function ConnectionCertificate({
  extId, connection, onSaved, hint = PROXMOX_FINGERPRINT_HINT, unchecked = false,
}: {
  extId: string;
  connection: string;
  /** Die Verbindung hat „Selbstsigniertes Zertifikat erlauben“ an (sonst scheitert sie an einem solchen Zertifikat). */
  unchecked?: boolean;
  /** Nach dem Speichern (die Liste der Verbindungen neu laden). */
  onSaved?: () => void;
  hint?: string;
}) {
  const [info, setInfo] = useState<ConnectionCertificateInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const path = `/ext/${extId}/connections/${encodeURIComponent(connection)}/certificate`;

  useEffect(() => {
    let active = true;
    setInfo(null);
    setError(null);
    setMessage(null);
    setConfirmed(false);
    authedFetch(path)
      .then(async (res) => {
        const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
        if (!res.ok) throw new Error(errorFromBody(body, res.status));
        if (active) setInfo(body as unknown as ConnectionCertificateInfo);
      })
      .catch((err: unknown) => {
        if (active) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      active = false;
    };
  }, [path]);

  async function save() {
    if (!info) return;
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      const res = await authedFetch(path, { method: "POST", body: JSON.stringify({ sha256: info.sha256 }) });
      const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
      if (res.status === 409 && typeof body.sha256 === "string") {
        // Der Server zeigt inzwischen ein anderes Zertifikat: neu vergleichen lassen. Inhaber und Ablauf gehören zum
        // alten Zertifikat und verschwinden, bis die Seite das neue liest.
        setInfo({ ...info, sha256: body.sha256, matches: false, trusted: false, subject: null, not_after: null });
        setConfirmed(false);
        throw new Error(errorFromBody(body, res.status));
      }
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      setInfo(body as unknown as ConnectionCertificateInfo);
      setConfirmed(false);
      setMessage("Gemerkt. Nodvard Deck spricht jetzt nur mit dem Server, der genau dieses Zertifikat zeigt.");
      onSaved?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const until = formatDate(info?.not_after);
  const changed = Boolean(info && info.stored && !info.matches);

  return (
    <section className="mb-3 space-y-2 rounded-lg border border-white/10 p-3 text-xs" aria-label={`Zertifikat – ${connection}`}>
      <h4 className="font-medium">Zertifikat – {connection}</h4>
      {!info && !error && <p className="opacity-60">Lese das Zertifikat …</p>}
      {error && <p role="alert" className="text-red-300">{error}</p>}
      {message && <p role="status" className="text-emerald-300">{message}</p>}
      {info && info.matches && (
        <p className="text-emerald-300" data-testid="certificate-matches">
          Der gemerkte Fingerabdruck passt zum Zertifikat des Servers.
        </p>
      )}
      {info && !info.matches && (
        <div className="space-y-2" data-testid="certificate-to-confirm">
          {changed && (
            <p className="text-amber-300" data-testid="certificate-changed">
              Der Server zeigt ein anderes Zertifikat als das gemerkte. Bis du den neuen Fingerabdruck bestätigst, spricht
              Nodvard Deck nicht mit ihm.
            </p>
          )}
          {info.stored_invalid && (
            <p className="text-red-300">
              Der eingetragene Fingerabdruck ist ungültig – bis er stimmt, spricht Nodvard Deck nicht mit dem Server.
            </p>
          )}
          {!info.stored && !info.stored_invalid && (
            <p className={info.trusted ? "opacity-80" : "text-amber-300"}>
              {info.trusted
                ? "Das Zertifikat ist von einer anerkannten Stelle bestätigt, Merken ist nicht nötig. Erneuert es sich regelmäßig (z. B. Let's Encrypt), merk es dir besser nicht – sonst musst du nach jeder Erneuerung neu bestätigen."
                : unchecked
                  ? "Das Zertifikat wird bisher nicht geprüft. Merk dir den Fingerabdruck, dann spricht Nodvard Deck nur mit genau diesem Server."
                  : "Das Zertifikat ist nicht von einer anerkannten Stelle bestätigt, deshalb klappt die Verbindung noch nicht. Merk dir den Fingerabdruck, dann spricht Nodvard Deck nur mit genau diesem Server."}
            </p>
          )}
          <p className="opacity-80">{hint}</p>
          <div>
            <p className="opacity-60">{changed ? "Neuer Fingerabdruck" : "Fingerabdruck"} (SHA-256)</p>
            <p className="break-all rounded bg-black/30 px-2 py-1.5 font-mono" aria-label="Fingerabdruck des Servers">{info.sha256}</p>
          </div>
          {changed && info.stored && (
            <div>
              <p className="opacity-60">Bisher gemerkt</p>
              <p className="break-all rounded bg-black/20 px-2 py-1.5 font-mono opacity-60" aria-label="Bisher gemerkt">{info.stored}</p>
            </div>
          )}
          {(info.subject || until) && (
            <p className="text-white/55">
              {info.subject ? `Ausgestellt für ${info.subject}` : ""}
              {info.subject && until ? " · " : ""}
              {until ? `gültig bis ${until}` : ""}
            </p>
          )}
          <label className="flex cursor-pointer items-start gap-2">
            <input
              type="checkbox" className="mt-0.5 h-4 w-4 flex-none accent-[var(--color-accent)]"
              checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)}
            />
            <span>Der Fingerabdruck stimmt mit dem in Proxmox überein.</span>
          </label>
          <p className="text-white/45">
            Stimmt er nicht, bestätige nichts: Dann antwortet unter dieser Adresse vielleicht ein anderer Rechner.
          </p>
          <Button small variant="primary" disabled={!confirmed || busy} onClick={() => void save()}>
            {busy ? "Speichere …" : changed ? "Neuen Fingerabdruck bestätigen" : "Fingerabdruck merken"}
          </Button>
        </div>
      )}
    </section>
  );
}
