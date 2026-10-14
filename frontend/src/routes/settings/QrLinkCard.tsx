/**
 * Karte unter dem Einstellungs-Formular einer Erweiterung, wenn deren Schema `x-qr-url` nennt (Pfad unter /api/v1,
 * Antwort `{url, text, error}`): `url` steckt im QR-Code, `text` sagt, was man damit tut, `error` ersetzt den Code,
 * wenn sich kein Link berechnen lässt. Nach jedem Speichern wird aus den gespeicherten Einstellungen neu geladen.
 */
import { useEffect, useState } from "react";

import { QrCode } from "../../components/QrCode";
import { api } from "../../lib/api";
import { Card } from "./ui";

interface QrLinkAnswer {
  url?: string | null;
  text?: string | null;
  error?: string | null;
}

type Loaded = { state: "loading" } | { state: "failed" } | { state: "ready"; data: QrLinkAnswer };

export function QrLinkCard({
  path, enabled, reloadKey, unsaved,
}: {
  path: string;
  /** Eine ausgeschaltete Erweiterung hat die Route nicht: dann gar nicht erst fragen. */
  enabled: boolean;
  /** Ändert sich nach jedem Speichern, dann wird neu geladen. */
  reloadKey: number;
  /** Das Formular hat Änderungen, die noch nicht gespeichert sind (der Code zeigt den gespeicherten Stand). */
  unsaved: boolean;
}) {
  const [result, setResult] = useState<Loaded>({ state: "loading" });

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    setResult({ state: "loading" });
    api.get<QrLinkAnswer>(path)
      .then((data) => alive && setResult({ state: "ready", data }))
      .catch(() => alive && setResult({ state: "failed" }));
    return () => { alive = false; };
  }, [path, enabled, reloadKey]);

  let body: JSX.Element;
  if (!enabled) {
    body = <p className="text-sm text-white/55">Der QR-Code erscheint, sobald die Erweiterung eingeschaltet ist.</p>;
  } else if (result.state === "loading") {
    body = <p className="text-sm text-white/50">Lade …</p>;
  } else if (result.state === "failed") {
    body = <p role="alert" className="text-sm text-red-300">Der QR-Code konnte nicht geladen werden.</p>;
  } else if (!result.data.url) {
    body = <p role="status" className="text-sm text-amber-200/90">{result.data.error || "Es lässt sich noch kein QR-Code berechnen."}</p>;
  } else {
    body = (
      <div className="flex flex-wrap items-start gap-5">
        <QrCode value={result.data.url} label="QR-Code" size={176} />
        <div className="min-w-[14rem] flex-1 space-y-2">
          {result.data.text && <p className="text-sm text-white/70">{result.data.text}</p>}
          <p data-testid="qr-link" className="break-all rounded-lg border border-white/10 bg-black/25 px-3 py-2 font-mono text-xs text-white/80">{result.data.url}</p>
        </div>
      </div>
    );
  }

  return (
    <Card title="QR-Code" description="Berechnet aus den gespeicherten Einstellungen. Nach dem Speichern aktualisiert.">
      {body}
      {enabled && unsaved && (
        <p role="status" className="mt-3 text-xs text-amber-200/80">Du hast Änderungen, die noch nicht gespeichert sind. Der Code zeigt den gespeicherten Stand.</p>
      )}
    </Card>
  );
}
