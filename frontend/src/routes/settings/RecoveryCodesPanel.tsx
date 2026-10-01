/**
 * Zeigt frisch erzeugte Wiederherstellungs-Codes EINMAL an -- der Server speichert nur
 * Hashes und kann sie nie wieder ausgeben. Zum Kopieren und als Textdatei zum Speichern;
 * erst nach "Ich habe die Codes gesichert" verschwindet die Anzeige.
 */
import { Copy, Download } from "lucide-react";
import { useState } from "react";

import { Button, Notice, NoticeLine } from "./ui";

export function RecoveryCodesPanel({ codes, username, onDone }: { codes: string[]; username: string; onDone: () => void }) {
  const [notice, setNotice] = useState<Notice>(null);
  const text = codes.join("\n");

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setNotice({ kind: "ok", text: "Codes in die Zwischenablage kopiert." });
    } catch {
      setNotice({ kind: "error", text: "Kopieren hat nicht geklappt – bitte die Codes von Hand abschreiben oder als Datei speichern." });
    }
  }

  function download() {
    const header = `Wiederherstellungs-Codes für ${username}\nJeder Code funktioniert genau einmal anstelle des Authenticator-Codes.\n\n`;
    const url = URL.createObjectURL(new Blob([header + text + "\n"], { type: "text/plain;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "wiederherstellungs-codes.txt";
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="mt-5 rounded-lg border border-amber-400/30 bg-amber-400/5 p-4" data-testid="recovery-codes">
      <h4 className="text-sm font-semibold">Wiederherstellungs-Codes</h4>
      <p className="mt-1 text-sm text-white/70">
        Diese Codes sicher aufbewahren, zum Beispiel in einem Passwortmanager oder ausgedruckt. Wenn das Handy verloren geht, kommt man
        damit wieder hinein. Jeder Code gilt nur einmal. <strong>Sie werden nur jetzt angezeigt</strong> – später können sie nicht erneut
        angezeigt werden.
      </p>
      <ul className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-5">
        {codes.map((code) => (
          <li key={code} className="select-all rounded-md bg-black/40 px-3 py-2 text-center font-mono text-sm tracking-wider">{code}</li>
        ))}
      </ul>
      <NoticeLine notice={notice} />
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button onClick={() => void copy()}><Copy size={14} /> Kopieren</Button>
        <Button onClick={download}><Download size={14} /> Als Textdatei speichern</Button>
        <span className="flex-1" />
        <Button variant="primary" onClick={onDone}>Ich habe die Codes gesichert</Button>
      </div>
    </div>
  );
}
