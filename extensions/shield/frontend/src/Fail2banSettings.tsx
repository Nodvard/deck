import { useState } from "react";

import { Button, inputClass } from "../../../_shared/frontend/src/ui";
import { API, call, proposeAndApprove, track, usePageActions } from "./api";
import { RunNotices } from "./RunNotice";

interface Settings {
  bantime: number;
  findtime: number;
  maxretry: number;
  ignoreip: string[];
  jails: string[];
  available_jails: string[];
  deck_address: string;
  runtime?: boolean;
}

export function Fail2banSettings({ hostId, canManage, onSaved }: {
  hostId: string; canManage: boolean; onSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [exceptions, setExceptions] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const page = usePageActions();
  const key = `${hostId}:fail2ban-config`;
  const busy = page.keys.busy.has(key);
  const path = `${API}/hosts/${encodeURIComponent(hostId)}/fail2ban`;

  async function load() {
    setLoading(true);
    setError("");
    try {
      const s = await call<Settings>(path);
      setSettings(s);
      setExceptions(s.ignoreip.filter((ip) => ip !== s.deck_address).join("\n"));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function save() {
    if (!settings) return;
    const body = {
      bantime: settings.bantime, findtime: settings.findtime, maxretry: settings.maxretry,
      ignoreip: [...new Set(exceptions.split(/[\s,]+/).filter(Boolean))],
      jails: [...new Set(["sshd", ...settings.jails])],
    };
    const kind = await track(page, key, "Fail2ban-Einstellungen", "Fail2ban-Einstellungen werden geprüft und übernommen …",
      () => proposeAndApprove(path, body, "medium", page.signal()));
    if (kind === "ok") {
      await load();
      onSaved();
    }
  }

  return (
    <section className="mt-3 rounded-lg border border-white/10 p-3" aria-label="Fail2ban-Einstellungen">
      <button type="button" className="text-sm hover:underline" aria-expanded={open} onClick={() => {
        setOpen(!open);
        if (!open && !settings) void load();
      }}>Fail2ban-Einstellungen</button>
      {open && (
        <div className="mt-3 space-y-3 text-xs">
          {loading && <p>Lade Einstellungen …</p>}
          {error && <p role="alert">{error} <button type="button" onClick={() => void load()}>Erneut laden</button></p>}
          {settings && (
            <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); void save(); }}>
              <p>Änderungen werden nach Freigabe geprüft und nur bei gültiger Konfiguration aktiviert.</p>
              {settings.runtime === false && <p role="status">Fail2ban oder der SSH-Jail ist gerade nicht erreichbar. Angezeigt werden gespeicherte Einstellungen, sonst Standardwerte. Zum Aktivieren muss Fail2ban laufen.</p>}
              <div className="grid gap-3 sm:grid-cols-3">
                {([
                  ["bantime", "Sperrdauer (Sekunden)", 31536000],
                  ["findtime", "Fundzeit (Sekunden)", 31536000],
                  ["maxretry", "Versuche", 1000],
                ] as const).map(([field, label, max]) => (
                  <label key={field} className="block">{label}
                    <input type="number" required min={1} max={max} step={1} value={settings[field]}
                      disabled={!canManage || busy || loading} className={`${inputClass} mt-1 w-full`}
                      onChange={(e) => setSettings({ ...settings, [field]: Number(e.target.value) })} />
                  </label>
                ))}
              </div>
              <p>Fundzeit: In diesem Zeitraum führen die eingestellten Fehlversuche zur Sperre.</p>
              <label className="block">Deck-Verbindung (automatische Ausnahme, nicht entfernbar)
                <input value={settings.deck_address} readOnly className={`${inputClass} mt-1 w-full font-mono`} />
              </label>
              <label className="block">Weitere Ausnahmen (IPs oder Netze, eine je Zeile)
                <textarea value={exceptions} disabled={!canManage || busy || loading} rows={3}
                  className={`${inputClass} mt-1 w-full font-mono`} onChange={(e) => setExceptions(e.target.value)} />
              </label>
              <fieldset disabled={!canManage || busy || loading}>
                <legend className="mb-1">Jails (Schutzregeln)</legend>
                <div className="flex flex-wrap gap-3">
                  {settings.available_jails.map((jail) => (
                    <label key={jail} className="flex items-center gap-1">
                      <input type="checkbox" checked={jail === "sshd" || settings.jails.includes(jail)} disabled={jail === "sshd"}
                        onChange={(e) => setSettings({ ...settings, jails: e.target.checked
                          ? [...settings.jails, jail] : settings.jails.filter((j) => j !== jail) })} />
                      {jail}{jail === "sshd" ? " (Pflicht)" : ""}
                    </label>
                  ))}
                </div>
              </fieldset>
              {canManage && <Button disabled={busy || loading} type="submit">{busy ? "Wird übernommen …" : "Einstellungen übernehmen"}</Button>}
            </form>
          )}
        </div>
      )}
      {page.notices && <RunNotices notices={page.notices} onClose={(k) => page.report(k, null)} />}
    </section>
  );
}
