/**
 * Erweiterungen ein- und ausschalten. Ausgeschaltete Module verschwinden aus Menue,
 * Uebersicht und Suche; ihre Daten bleiben erhalten.
 */
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Settings2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Icon } from "../../components/Icon";
import { api } from "../../lib/api";
import { confirmDialog } from "../../state/dialogs";
import { Badge, ExtensionStateBadge, NoticeLine, PageHeader, Toggle, errorText, type Notice } from "./ui";

interface ExtensionRow {
  id: string;
  version: string;
  state: string;
  source: string;
  name: string | null;
  description: string | null;
  icon: string | null;
  granted_permissions: string[];
  last_error: string | null;
  has_settings?: boolean;
  /** Nur bei eingeschalteten Modulen: Pflichtangaben fehlen oder der letzte Test schlug fehl. */
  needs_setup?: boolean;
  setup_reasons?: string[];
}

const MAX_REASONS = 2;

export function ExtensionsSettings(): JSX.Element {
  const queryClient = useQueryClient();
  const [rows, setRows] = useState<ExtensionRow[] | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);

  function load() {
    api.get<ExtensionRow[]>("/extensions")
      .then((list) => setRows([...list].sort((a, b) => (a.name ?? a.id).localeCompare(b.name ?? b.id))))
      .catch((err: unknown) => setNotice({ kind: "error", text: errorText(err) }));
  }
  useEffect(load, []);

  async function toggle(ext: ExtensionRow, enable: boolean) {
    const label = ext.name ?? ext.id;
    if (!enable) {
      const ok = await confirmDialog(`„${label}“ ausschalten? Das Modul verschwindet aus Menü und Übersicht, seine Daten bleiben erhalten.`, {
        confirmLabel: "Ausschalten",
      });
      if (!ok) return;
    }
    setBusyId(ext.id);
    setNotice(null);
    try {
      const result = await api.post<Partial<ExtensionRow> | undefined>(`/extensions/${ext.id}/${enable ? "enable" : "disable"}`);
      // Das Backend antwortet auch dann mit 200, wenn das Modul beim Laden abstuerzt
      // (state "error"/"incompatible", Grund in last_error) -- dann nicht "eingeschaltet" melden.
      if (enable && result?.state && result.state !== "enabled") {
        setNotice({ kind: "error", text: `„${label}“ ließ sich nicht einschalten${result.last_error ? `: ${result.last_error}` : "."}` });
      } else {
        setNotice({ kind: "ok", text: `„${label}“ ${enable ? "eingeschaltet" : "ausgeschaltet"}.` });
      }
      load();
      for (const key of ["pages", "widgets", "capabilities"]) void queryClient.invalidateQueries({ queryKey: [key] });
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div>
      <PageHeader title="Erweiterungen" description="Module ein- und ausschalten und einrichten, z. B. Server-Überwachung, Docker-Dienste, Push-Nachrichten oder den Proxmox-Zugang. Ausgeschaltete Module verschwinden aus Menü und Übersicht." />
      <NoticeLine notice={notice} />
      {!rows ? (
        <p className="text-sm text-white/50">Lade …</p>
      ) : (
        <ul className="panel divide-y divide-white/[0.06]">
          {rows.map((ext) => {
            const enabled = ext.state === "enabled";
            return (
              <li key={ext.id} data-testid={`ext-${ext.id}`} className="flex items-center gap-4 px-5 py-4">
                <span className="grid h-10 w-10 flex-none place-items-center rounded-lg bg-white/[0.06] text-[var(--color-accent)]">
                  <Icon name={ext.icon} size={18} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-medium">{ext.name ?? ext.id}</p>
                    <span className="text-xs text-white/35">v{ext.version}</span>
                    <ExtensionStateBadge state={ext.state} />
                    {ext.needs_setup && <Badge tone="warn">Einrichtung nötig</Badge>}
                  </div>
                  {ext.description && <p className="mt-0.5 text-sm text-white/55">{ext.description}</p>}
                  {ext.needs_setup && (ext.setup_reasons?.length ?? 0) > 0 && (
                    <ul data-testid={`setup-reasons-${ext.id}`} className="mt-1.5 space-y-0.5 text-xs text-amber-200/90">
                      {ext.setup_reasons!.slice(0, MAX_REASONS).map((r) => (
                        <li key={r} className="flex items-start gap-1.5"><AlertTriangle size={12} className="mt-0.5 flex-none" /> {r}</li>
                      ))}
                      {ext.setup_reasons!.length > MAX_REASONS && <li className="pl-[18px] text-white/45">und {ext.setup_reasons!.length - MAX_REASONS} weitere</li>}
                    </ul>
                  )}
                  {ext.last_error && <p className="mt-1 text-xs text-red-300">{ext.last_error}</p>}
                </div>
                {ext.has_settings && (
                  <Link
                    to={`/settings/extensions/${ext.id}`}
                    className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium ${
                      ext.needs_setup ? "accent-gradient text-white shadow-md shadow-black/30 hover:brightness-110" : "border border-white/10 bg-white/[0.06] hover:bg-white/[0.12]"
                    }`}
                  >
                    <Settings2 size={14} /> {ext.needs_setup ? "Jetzt einrichten" : "Konfigurieren"}
                  </Link>
                )}
                {ext.state === "error" && (
                  // Nach einem Absturz in on_start() bleibt die Erweiterung halb geladen (Seiten,
                  // Routen, Zeitplaene aktiv). Der Schalter steht auf "aus" und startet sie nur
                  // neu -- zum Abschalten braucht es diesen eigenen Knopf.
                  <button
                    type="button"
                    disabled={busyId !== null}
                    onClick={() => void toggle(ext, false)}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.06] px-3 py-1.5 text-sm font-medium hover:bg-white/[0.12] disabled:opacity-50"
                  >
                    Ausschalten
                  </button>
                )}
                <Toggle
                  checked={enabled}
                  disabled={busyId !== null || ext.state === "uninstalling"}
                  label={`${ext.name ?? ext.id} ${enabled ? "ausschalten" : ext.state === "error" ? "neu starten" : "einschalten"}`}
                  onChange={(v) => void toggle(ext, v)}
                />
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
