/**
 * Erweiterungen ein- und ausschalten. Ausgeschaltete Module verschwinden aus Menue,
 * Uebersicht und Suche; ihre Daten bleiben erhalten.
 */
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Settings2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { BetaBadge, BetaExplainer, isBeta } from "../../components/BetaBadge";
import { Icon } from "../../components/Icon";
import { api } from "../../lib/api";
import { extensionErrorText } from "../../lib/extensionError";
import { groupModules } from "../../lib/moduleGroups";
import { confirmDialog } from "../../state/dialogs";
import { Badge, ExtensionStateBadge, NoticeLine, PageHeader, Toggle, errorText, type Notice } from "./ui";

/** Eine Erweiterung, die die gespeicherten Daten eines Moduls unter dessen Kennung übernehmen will (`legacy_ids`). */
interface TakeoverRequest {
  id: string;
  old_id: string;
  name: string;
  /** `pip`: nachinstalliertes Paket; `bundled`: Ordner im Erweiterungsverzeichnis. */
  source: string;
  permissions: string[];
  /** Schon erlaubt: gilt ab dem nächsten Neustart, bis dahin läuft keine der beiden. Fehlt bei älteren Servern. */
  allowed?: boolean;
}

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
  /** Gruppe und Reihenfolge aus dem Manifest (siehe `lib/moduleGroups.ts`); fehlen bei älteren Servern. */
  category?: string | null;
  sort_order?: number;
  /** `beta`: funktioniert, ist aber noch nicht so gründlich erprobt (Abzeichen „Beta“); `stable` oder fehlend: fertig. */
  stage?: string;
  /** Frühere Kennungen einer umbenannten Erweiterung (stehen in eingelesenen Servern, die älter sind als die Umbenennung). */
  legacy_ids?: string[];
  /** Mit Nodvard Deck ausgeliefert: die eigene `version` wird bis zur 1.0 nicht gepflegt, es gilt die Programmversion. Fehlt bei aelteren Servern. */
  bundled?: boolean;
  /** Programmversion (mitgeliefert) oder eigene Version (nachinstalliert). Fehlt bei aelteren Servern. */
  display_version?: string | null;
  /** Nur bei eingeschalteten Modulen: Pflichtangaben fehlen oder der letzte Test schlug fehl. */
  needs_setup?: boolean;
  setup_reasons?: string[];
  /** Nur für Verwalter, fehlt bei älteren Servern. Die Erweiterung wird erst geladen, wenn jemand das erlaubt. */
  takeover_requests?: TakeoverRequest[];
}

const MAX_REASONS = 2;

/** Wie viele Server das Modul eingelesen hat (`provider_ext_id`); `null`, wenn sich das nicht feststellen lässt
 * (kein Recht auf die Serverliste, Server nicht erreichbar): dann fragt der Dialog wie sonst. */
async function countProvidedHosts(ext: ExtensionRow): Promise<number | null> {
  try {
    const hosts = await api.get<{ provider_ext_id: string | null }[]>("/hosts");
    const ids = new Set([ext.id, ...(ext.legacy_ids ?? [])]);
    return hosts.filter((h) => h.provider_ext_id !== null && ids.has(h.provider_ext_id)).length;
  } catch {
    return null;
  }
}

/** Was mit den eingelesenen Servern passiert, wenn das Modul ausgeschaltet wird: Sie bleiben stehen. */
export function providedHostsText(count: number | null): string {
  if (!count) return "";
  return count === 1
    ? " Ein Server, den es eingelesen hat, bleibt in der Liste stehen, wird aber nicht mehr aktualisiert. Wenn du ihn nicht mehr brauchst, löschst du ihn unter Einstellungen → Server & Zugänge."
    : ` Die ${count} Server, die es eingelesen hat, bleiben in der Liste stehen, werden aber nicht mehr aktualisiert. Wenn du sie nicht mehr brauchst, löschst du sie unter Einstellungen → Server & Zugänge.`;
}

/** Der Satz nach dem Erlauben; die Übernahme gilt erst nach einem Neustart. */
export function takeoverAllowedText(name: string, label: string, extra: string[]): string {
  const allowed = `Übernahme durch „${name}“ erlaubt. Sie gilt nach dem nächsten Neustart von Nodvard Deck.`;
  if (extra.length === 0) return allowed;
  const more = `„${name}“ will mehr Rechte, als „${label}“ hatte (${extra.join(", ")})`;
  return `${allowed} ${more}, und bleibt deshalb aus, bis du es einschaltest.`;
}

export function ExtensionsSettings(): JSX.Element {
  const queryClient = useQueryClient();
  const [rows, setRows] = useState<ExtensionRow[] | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);

  function load() {
    api.get<ExtensionRow[]>("/extensions")
      .then(setRows)
      .catch((err: unknown) => setNotice({ kind: "error", text: errorText(err) }));
  }
  useEffect(load, []);

  async function toggle(ext: ExtensionRow, enable: boolean) {
    const label = ext.name ?? ext.id;
    if (!enable) {
      const hostsText = providedHostsText(await countProvidedHosts(ext));
      const ok = await confirmDialog(`„${label}“ ausschalten? Das Modul verschwindet aus Menü und Übersicht, seine Daten bleiben erhalten.${hostsText}`, {
        confirmLabel: "Ausschalten",
      });
      if (!ok) return;
    }
    setBusyId(ext.id);
    setNotice(null);
    try {
      const result = await api.post<Partial<ExtensionRow> | undefined>(`/extensions/${ext.id}/${enable ? "enable" : "disable"}`);
      // Das Backend antwortet auch dann mit 200, wenn das Modul beim Laden abstürzt (state "error" oder
      // "incompatible", Grund in last_error): dann nicht „eingeschaltet“ melden.
      if (enable && result?.state && result.state !== "enabled") {
        setNotice({ kind: "error", text: `„${label}“ ließ sich nicht einschalten${result.last_error ? `: ${extensionErrorText(result.last_error)}` : "."}` });
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

  async function allowTakeover(ext: ExtensionRow, request: TakeoverRequest) {
    const label = ext.name ?? ext.id;
    const where = request.source === "pip" ? "ein nachinstalliertes Paket" : "ein Ordner im Erweiterungsverzeichnis";
    const rights = request.permissions.length ? request.permissions.join(", ") : "keine";
    const ok = await confirmDialog(
      `„${request.name}“ (${where}) die gespeicherten Daten von „${label}“ übernehmen lassen? Dazu gehören ` +
        `Einstellungen, Datenordner, Aktionen, Tresor-Einträge und Tabellen. Es bekommt die Rechte: ${rights}. ` +
        `Erlaube das nur, wenn du weißt, dass es die neue Version von „${label}“ ist. Bis zum nächsten Neustart ` +
        `kannst du es noch zurücknehmen, indem du „${request.name}“ wieder entfernst, danach nicht mehr.`,
      { confirmLabel: "Übernahme erlauben" },
    );
    if (!ok) return;
    setBusyId(ext.id);
    setNotice(null);
    try {
      const result = await api.post<{ extra_permissions: string[] }>(`/extensions/${request.id}/takeover`, {
        old_id: request.old_id,
      });
      setNotice({ kind: "ok", text: takeoverAllowedText(request.name, label, result.extra_permissions ?? []) });
      load();
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
        <div className="space-y-6">
          {groupModules(rows).map((group) => (
            <section key={group.id} aria-label={group.title} data-testid={`ext-group-${group.id}`}>
              <h2 className="text-xs font-semibold text-white/45">{group.title}</h2>
              {group.hint && <p className="mt-0.5 text-xs text-white/40">{group.hint}</p>}
              <ul className="panel mt-2 divide-y divide-white/[0.06]">
                {group.rows.map((ext) => {
                  const enabled = ext.state === "enabled";
                  return (
                    <li key={ext.id} data-testid={`ext-${ext.id}`} className="flex items-center gap-4 px-5 py-4">
                      <span className="grid h-10 w-10 flex-none place-items-center rounded-lg bg-white/[0.06] text-[var(--color-accent)]">
                        <Icon name={ext.icon} size={18} />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="text-sm font-medium">{ext.name ?? ext.id}</p>
                          {isBeta(ext.stage) && <BetaBadge />}
                          <span className="text-xs text-white/35" data-testid={`ext-version-${ext.id}`}>
                            {ext.bundled && ext.display_version ? `Teil von Nodvard Deck ${ext.display_version}` : `v${ext.display_version || ext.version}`}
                          </span>
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
                        {ext.last_error && <p className="mt-1 text-xs text-red-300">{extensionErrorText(ext.last_error)}</p>}
                        {ext.takeover_requests?.map((request) => (
                          <div
                            key={`${request.id}:${request.old_id}`}
                            data-testid={`takeover-${request.id}`}
                            className="mt-2 rounded-lg border border-amber-300/25 bg-amber-300/[0.06] px-3 py-2 text-xs text-amber-100/90"
                          >
                            {request.allowed ? (
                              <p>
                                Übernahme durch „{request.name}“ erlaubt. Sie gilt nach dem nächsten Neustart von
                                Nodvard Deck, bis dahin läuft keine der beiden.
                              </p>
                            ) : (
                              <>
                                <p>
                                  „{request.name}“ will die gespeicherten Daten dieses Moduls übernehmen. Es wird erst
                                  geladen, wenn du das erlaubst.
                                </p>
                                <button
                                  type="button"
                                  disabled={busyId !== null}
                                  onClick={() => void allowTakeover(ext, request)}
                                  className="mt-1.5 rounded-lg border border-white/10 bg-white/[0.06] px-3 py-1 text-xs font-medium hover:bg-white/[0.12] disabled:opacity-50"
                                >
                                  Übernahme erlauben
                                </button>
                              </>
                            )}
                          </div>
                        ))}
                      </div>
                      {ext.has_settings && (
                        <Link
                          to={`/settings/extensions/${ext.id}`}
                          className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium ${
                            ext.needs_setup ? "accent-gradient text-on-accent hover:bg-accent-strong" : "border border-white/10 bg-white/[0.06] hover:bg-white/[0.12]"
                          }`}
                        >
                          <Settings2 size={14} /> {ext.needs_setup ? "Jetzt einrichten" : "Konfigurieren"}
                        </Link>
                      )}
                      {ext.state === "error" ? (
                        // Nach einem Absturz beim Start bleibt die Erweiterung halb geladen (Seiten, Routen, Zeitpläne
                        // aktiv): Ein Schalter auf „aus“ wäre falsch, deshalb zwei Knöpfe.
                        <>
                          <button
                            type="button"
                            disabled={busyId !== null}
                            onClick={() => void toggle(ext, true)}
                            aria-label={`${ext.name ?? ext.id} neu starten`}
                            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.06] px-3 py-1.5 text-sm font-medium hover:bg-white/[0.12] disabled:opacity-50"
                          >
                            Neu starten
                          </button>
                          <button
                            type="button"
                            disabled={busyId !== null}
                            onClick={() => void toggle(ext, false)}
                            aria-label={`${ext.name ?? ext.id} ausschalten`}
                            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.06] px-3 py-1.5 text-sm font-medium hover:bg-white/[0.12] disabled:opacity-50"
                          >
                            Ausschalten
                          </button>
                        </>
                      ) : (
                        <Toggle
                          checked={enabled}
                          disabled={busyId !== null || ext.state === "uninstalling"}
                          label={`${ext.name ?? ext.id} ${enabled ? "ausschalten" : "einschalten"}`}
                          onChange={(v) => void toggle(ext, v)}
                        />
                      )}
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
          {rows.some((r) => isBeta(r.stage)) && <BetaExplainer />}
        </div>
      )}
    </div>
  );
}
