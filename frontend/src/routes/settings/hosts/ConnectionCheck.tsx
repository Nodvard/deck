/**
 * „Verbindung pruefen“: ruft `POST /hosts/{id}/check` und zeigt das Ergebnis als Liste in
 * einfachem Deutsch -- erreichbar, Server-Schluessel, Anmeldung, Root-Rechte, was die Erweiterungen
 * brauchen, Betriebssystem.
 *
 * Zwei Sonderfaelle beim Server-Schluessel:
 *  - neu: Fingerabdruck und „Bestaetigen“. Bis dahin ging KEIN Passwort und kein Schluessel an den Server.
 *  - geaendert: deutliche Warnung, bewusst OHNE Bestaetigen-Knopf (Neuinstallation oder jemand
 *    schaltet sich dazwischen). Erst den alten Schluessel vergessen (Karte „Server-Schluessel“).
 */
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, HelpCircle, Minus, ShieldAlert, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";

import { api, ApiError } from "../../../lib/api";
import {
  checkSummary, refreshHosts, splitCommands,
  type CheckItem, type CheckStatus, type ConnectionCheck as CheckResult, type HostKeyInfo,
} from "../../../lib/hosts";
import { Button, NoticeLine, type Notice } from "../ui";
import { CopyButton } from "./CopyButton";

const ICON: Record<CheckStatus, ReactNode> = {
  ok: <CheckCircle2 size={18} className="text-emerald-300" aria-label="in Ordnung" />,
  warn: <AlertTriangle size={18} className="text-amber-300" aria-label="Hinweis" />,
  fail: <XCircle size={18} className="text-red-300" aria-label="Fehler" />,
  skipped: <Minus size={18} className="text-white/35" aria-label="übersprungen" />,
  confirm: <HelpCircle size={18} className="text-amber-300" aria-label="wartet auf Bestätigung" />,
};

/** Text mit Befehlen: Befehle als `<code>` mit Kopierknopf. */
function HintText({ text }: { text: string }) {
  return (
    <>
      {splitCommands(text).map((part, i) =>
        part.code ? (
          <span key={i} className="my-1 flex flex-wrap items-center gap-2">
            <code className="break-all rounded bg-black/30 px-1.5 py-0.5 font-mono text-[11px] text-white/85">{part.text}</code>
            <CopyButton text={part.text} className="!px-2 !py-0.5 !text-xs" />
          </span>
        ) : (
          <span key={i}>{part.text}</span>
        ),
      )}
    </>
  );
}

function Fingerprint({ label, value }: { label: string; value: string }) {
  return (
    <p className="mt-1 text-xs">
      <span className="text-white/50">{label}: </span>
      <span className="break-all font-mono text-white/90">{value}</span>
    </p>
  );
}

function withoutFingerprint(detail: string, fingerprint: string): string {
  return detail.replace(fingerprint, "").replace(/\s*Fingerabdruck:\s*$/, "").trim();
}

function KeyConfirm({
  item, hostKey, busy, onConfirm,
}: { item: CheckItem; hostKey: HostKeyInfo | null; busy: boolean; onConfirm: () => void }) {
  return (
    <>
      <p className="text-white/80">{hostKey ? withoutFingerprint(item.detail, hostKey.fingerprint) : item.detail}</p>
      {hostKey && <Fingerprint label={`Fingerabdruck (${hostKey.key_type})`} value={hostKey.fingerprint} />}
      {item.hint && <p className="mt-1.5 text-xs text-white/55"><HintText text={item.hint} /></p>}
      {hostKey && (
        <div className="mt-2.5">
          <Button variant="primary" busy={busy} onClick={onConfirm}>Fingerabdruck stimmt – bestätigen</Button>
          <p className="mt-1 text-xs text-white/45">Erst wenn der Fingerabdruck mit dem auf dem Server übereinstimmt. Vorher wurde nichts an den Server geschickt.</p>
        </div>
      )}
    </>
  );
}

function KeyChanged({ item, hostKey }: { item: CheckItem; hostKey: HostKeyInfo | null }) {
  return (
    <div className="mt-1 rounded-lg border border-red-500/40 bg-red-500/10 p-3" data-testid="key-changed">
      <p className="flex items-center gap-2 font-medium text-red-200">
        <ShieldAlert size={16} className="flex-none" /> Achtung: Der Server meldet einen anderen Schlüssel als bisher.
      </p>
      {hostKey ? (
        <>
          {hostKey.expected && <Fingerprint label="Bisher gemerkt" value={hostKey.expected} />}
          <Fingerprint label="Jetzt vom Server" value={hostKey.fingerprint} />
        </>
      ) : (
        <p className="mt-1 break-all text-xs text-red-100/80">{item.detail}</p>
      )}
      <p className="mt-2 text-xs text-red-100/80">{item.hint}</p>
      <p className="mt-2 text-xs text-red-100/80">
        Bestätigen geht hier mit Absicht nicht. Den alten Schlüssel kannst du unter{" "}
        <a href="#server-schluessel" className="underline underline-offset-2">Server-Schlüssel</a> vergessen.
      </p>
    </div>
  );
}

export function ConnectionCheck({
  hostId, credentialId, buttonLabel = "Verbindung prüfen", onResult,
}: {
  hostId: string;
  /** Mit welchem Zugang geprueft wird; ohne Angabe der Standard-Zugang. */
  credentialId?: string;
  buttonLabel?: string;
  /** Fuer den Aufrufer: das letzte Ergebnis (z. B. um „Neuen Zugang verwenden“ freizugeben). */
  onResult?: (result: CheckResult) => void;
}) {
  const queryClient = useQueryClient();
  const [result, setResult] = useState<CheckResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  async function run(): Promise<void> {
    setBusy(true);
    setNotice(null);
    try {
      const out = await api.post<CheckResult>(`/hosts/${hostId}/check`, credentialId ? { credential_id: credentialId } : undefined);
      setResult(out);
      onResult?.(out);
      await refreshHosts(queryClient);
    } catch (err) {
      // 429: der Text kommt vom Server („Zu viele Pruefungen. Bitte in N Minuten ...“).
      setNotice({ kind: "error", text: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  }

  async function confirmKey(key: HostKeyInfo) {
    setConfirming(true);
    setNotice(null);
    try {
      // Genau der Fingerabdruck, den die Pruefung gerade gezeigt hat (das Backend vergleicht ihn mit seinem Merkzettel).
      await api.post(`/hosts/${hostId}/known-hosts`, { key_type: key.key_type, fingerprint: key.fingerprint });
    } catch (err) {
      setNotice({ kind: "error", text: err instanceof ApiError ? err.message : String(err) });
      setConfirming(false);
      return;
    }
    setConfirming(false);
    await run();
  }

  const time = result ? new Date(result.checked_at).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }) : "";

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3">
        <Button variant={result ? "secondary" : "primary"} busy={busy} onClick={() => void run()}>
          {busy ? "Prüfe … (bis zu 30 Sekunden)" : buttonLabel}
        </Button>
        {result && !busy && (
          <p role="status" className={`text-sm ${result.ok ? "text-emerald-300" : "text-white/70"}`}>
            {checkSummary(result)}{time ? ` · geprüft um ${time} Uhr` : ""}
          </p>
        )}
      </div>
      <div className="mt-3"><NoticeLine notice={notice} /></div>
      {result && (
        <ul className="divide-y divide-white/[0.06] rounded-lg border border-white/[0.08]" aria-label="Ergebnis der Prüfung">
          {result.items.map((item) => {
            const changed = item.id === "host_key" && (item.status === "fail") && result.host_key?.status !== "known";
            return (
              <li key={item.id} className="flex gap-3 px-3 py-2.5 text-sm">
                <span className="mt-0.5 flex-none">{ICON[item.status]}</span>
                <div className="min-w-0 flex-1">
                  <p className="font-medium">{item.label}</p>
                  {item.status === "confirm" && item.id === "host_key" ? (
                    <KeyConfirm item={item} hostKey={result.host_key} busy={confirming || busy} onConfirm={() => result.host_key && void confirmKey(result.host_key)} />
                  ) : changed ? (
                    <KeyChanged item={item} hostKey={result.host_key} />
                  ) : (
                    <>
                      {item.detail && <p className="break-words text-white/75">{item.detail}</p>}
                      {item.hint && <p className="mt-1 text-xs text-white/50"><HintText text={item.hint} /></p>}
                    </>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
