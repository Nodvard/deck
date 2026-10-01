/**
 * System-Seite (Roadmap Punkt 3, "Linux-Hosts per SSH"): Zustand EINES Linux-Servers --
 * Betriebssystem, Auslastung, Platten, fehlgeschlagene Dienste, Updates. Erreichbar
 * ueber die Kachel "System-Monitor" auf der Server-Seite (`?host=`) oder direkt mit
 * Host-Auswahl.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { isActionRunning, runAction, RUNNING_IN_BACKGROUND } from "../../../_shared/frontend/src/actions";
import { authedFetch, errorFromBody } from "../../../_shared/frontend/src/api";
import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { SettingsLink } from "../../../_shared/frontend/src/links";
import { useUrlParams } from "../../../_shared/frontend/src/location";
import { Button, EmptyState, Notice } from "../../../_shared/frontend/src/ui";
import { deck } from "../../../_shared/frontend/src/deck";
import { formatBytes, formatUptime } from "./format";
import { type LiveData, LiveView } from "./LiveView";

interface HostOut {
  id: string;
  display_name: string;
  address: string;
  os_family: string;
  status: string;
  /** Standard-Zugang, `null` ohne SSH-Zugang (ohne den es nichts zu messen gibt). */
  credential?: unknown;
}

interface Disk { device: string; fstype: string; mount: string; size: number; used: number; available: number; percent: number; tone: string }
interface Finding { tone: string; text: string }

export interface SystemInfo {
  host: { id: string; name: string; address: string };
  os: string | null;
  kernel: string | null;
  arch: string | null;
  hostname: string | null;
  uptime_s: number | null;
  load: number[];
  cpus: number | null;
  cpu_percent: number | null;
  mem_total: number | null;
  mem_available: number | null;
  swap_total: number | null;
  swap_used: number | null;
  disks: Disk[];
  failed_units: string[];
  running_units?: string[];
  /** Dienste, für die "Neu starten" überhaupt in Frage kommt (ohne gesperrte wie ssh, dbus, systemd-…). */
  restartable_units?: string[];
  updates: { package: string; security: boolean }[] | null;
  reboot_required: boolean;
  temperature_c: number | null;
  temperature_tone?: string;
  findings: Finding[];
}

export { formatBytes, formatUptime } from "./format";

const TONE_TEXT: Record<string, string> = { good: "text-emerald-300", warn: "text-amber-300", danger: "text-red-300" };
const TONE_BAR: Record<string, string> = { good: "bg-emerald-400/70", warn: "bg-amber-400/80", danger: "bg-red-400/80" };

function Bar({ percent, tone }: { percent: number; tone: string }): JSX.Element {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded bg-white/10">
      <div className={`h-full ${TONE_BAR[tone] ?? TONE_BAR.good}`} style={{ width: `${Math.min(100, Math.max(0, percent))}%` }} />
    </div>
  );
}

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }): JSX.Element {
  return (
    <div className="panel p-3">
      <p className="text-[11px] uppercase tracking-wider opacity-50">{label}</p>
      <p className={`text-lg font-semibold tabular-nums ${tone && tone !== "good" ? TONE_TEXT[tone] ?? "" : ""}`}>{value}</p>
      {sub && <p className="text-xs opacity-60">{sub}</p>}
    </div>
  );
}

interface Message { kind: "ok" | "error"; text: string }

/** Ein Dienst mit "Neu starten" daneben (nur für Nutzer, die Aktionen vorschlagen dürfen). */
function UnitRow({ unit, tone, canRestart, busy, onRestart }: {
  unit: string;
  tone?: string;
  canRestart: boolean;
  busy: string | null;
  onRestart: (unit: string) => void;
}): JSX.Element {
  return (
    <li className="flex items-center justify-between gap-2 py-0.5">
      <span className={`break-words ${tone ?? ""}`}>{unit}</span>
      {canRestart && (
        <Button small ariaLabel={`${unit} neu starten`} disabled={busy !== null} onClick={() => onRestart(unit)}>
          {busy === unit ? "Startet neu …" : "Neu starten"}
        </Button>
      )}
    </li>
  );
}

function InfoView({ info, canRestart, busyUnit, onRestart }: {
  info: SystemInfo;
  canRestart: boolean;
  busyUnit: string | null;
  onRestart: (unit: string) => void;
}): JSX.Element {
  const memUsed = info.mem_total != null && info.mem_available != null ? info.mem_total - info.mem_available : null;
  const memPct = memUsed != null && info.mem_total ? (100 * memUsed) / info.mem_total : null;
  const swapPct = info.swap_total ? (100 * (info.swap_used ?? 0)) / info.swap_total : null;
  const security = (info.updates ?? []).filter((u) => u.security).length;
  // Knöpfe nur, wo der Server den Neustart auch zulässt -- sonst käme die Rückfrage und danach eine Absage.
  const restartable = new Set(info.restartable_units ?? []);
  return (
    <div data-testid="system-info">
      <p className="mb-4 text-sm opacity-70">
        {info.os ?? "Linux"} · Kernel {info.kernel ?? "?"} · {info.arch ?? "?"} · Hostname {info.hostname ?? "?"}
      </p>

      {info.findings.length > 0 ? (
        <ul className="panel mb-4 divide-y divide-white/5 text-sm" data-testid="findings">
          {info.findings.map((f) => (
            <li key={f.text} className={`px-3 py-2 ${TONE_TEXT[f.tone] ?? ""}`}>{f.text}</li>
          ))}
        </ul>
      ) : (
        <p className="panel mb-4 px-3 py-2 text-sm text-emerald-300" data-testid="findings">Alles in Ordnung -- nichts braucht Aufmerksamkeit.</p>
      )}

      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Läuft seit" value={formatUptime(info.uptime_s)} />
        <Stat label="CPU" value={info.cpu_percent != null ? `${info.cpu_percent.toFixed(0)} %` : "?"} sub={`${info.cpus ?? "?"} Kerne · Last ${info.load.map((l) => l.toFixed(2)).join(" / ")}`} />
        <Stat label="RAM" value={memPct != null ? `${memPct.toFixed(0)} %` : "?"} sub={`${formatBytes(memUsed)} von ${formatBytes(info.mem_total)}`} />
        <Stat label="Swap" value={swapPct != null ? `${swapPct.toFixed(0)} %` : "keiner"} sub={info.swap_total ? `${formatBytes(info.swap_used)} von ${formatBytes(info.swap_total)}` : undefined} />
        <Stat label="Temperatur" value={info.temperature_c != null ? `${info.temperature_c.toFixed(0)} °C` : "–"} tone={info.temperature_tone} />
      </div>

      <h3 className="mb-2 text-sm font-semibold uppercase tracking-wider opacity-70">Dateisysteme</h3>
      <table className="mb-6 w-full text-sm" data-testid="disks">
        <thead>
          <tr className="border-b border-white/10 text-left text-xs uppercase opacity-60">
            <th className="py-1">Einhängepunkt</th><th className="py-1">Gerät</th><th className="py-1 w-1/3">Belegung</th><th className="py-1">Frei</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5">
          {info.disks.map((d) => (
            <tr key={`${d.device}${d.mount}`}>
              <td className="py-1.5 break-words">{d.mount}</td>
              <td className="py-1.5 break-words opacity-70">{d.device} · {d.fstype}</td>
              <td className="py-1.5 pr-4">
                <div className={`mb-0.5 text-xs ${TONE_TEXT[d.tone] ?? ""}`}>{d.percent.toFixed(0)} % von {formatBytes(d.size)}</div>
                <Bar percent={d.percent} tone={d.tone} />
              </td>
              <td className="py-1.5 whitespace-nowrap opacity-80">{formatBytes(d.available)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="grid gap-6 lg:grid-cols-2">
        <div>
          <h3 className="mb-2 text-sm font-semibold uppercase tracking-wider opacity-70">Dienste</h3>
          {info.failed_units.length === 0 ? (
            <p className="text-sm opacity-70">Keine fehlgeschlagenen systemd-Dienste.</p>
          ) : (
            <ul className="text-sm" data-testid="failed-units">
              {info.failed_units.map((u) => <UnitRow key={u} unit={u} tone="text-red-300" canRestart={canRestart && restartable.has(u)} busy={busyUnit} onRestart={onRestart} />)}
            </ul>
          )}
          {(info.running_units?.length ?? 0) > 0 && (
            <details className="mt-3" data-testid="running-units">
              <summary className="cursor-pointer text-sm">{info.running_units?.length} laufende Dienste</summary>
              <ul className="mt-1 max-h-72 overflow-y-auto text-xs opacity-90">
                {info.running_units?.map((u) => <UnitRow key={u} unit={u} canRestart={canRestart && restartable.has(u)} busy={busyUnit} onRestart={onRestart} />)}
              </ul>
            </details>
          )}
        </div>
        <div>
          <h3 className="mb-2 text-sm font-semibold uppercase tracking-wider opacity-70">Updates</h3>
          {info.updates == null ? (
            <p className="text-sm opacity-70">Paketverwaltung nicht unterstützt (nur apt).</p>
          ) : info.updates.length === 0 ? (
            <p className="text-sm opacity-70">Auf dem neuesten Stand (laut letzter Paketlisten-Aktualisierung).</p>
          ) : (
            <details data-testid="updates">
              <summary className="cursor-pointer text-sm">
                {info.updates.length} Update(s) ausstehend{security ? `, davon ${security} Sicherheits-Update(s)` : ""}
              </summary>
              <ul className="mt-1 columns-2 text-xs opacity-80">
                {info.updates.map((u) => (
                  <li key={u.package} className={`break-words ${u.security ? "text-amber-300" : ""}`}>{u.package}</li>
                ))}
              </ul>
            </details>
          )}
          {info.reboot_required && <p className="mt-2 text-sm text-amber-300">Neustart nötig, damit installierte Updates greifen.</p>}
        </div>
      </div>
    </div>
  );
}

export function SystemPage(): JSX.Element {
  // Der gewaehlte Server steht in der Adresszeile (`?host=`) und folgt ihr (location.ts): ein Link
  // von der Server-Seite wirkt auch auf die schon offene Seite, nachdem man hier umgeschaltet hat.
  const [urlParams, updateUrl] = useUrlParams();
  const hostId = urlParams.get("host") || null;
  // `null`: noch nicht geladen -- sonst gaebe es kurz den Leerzustand "kein Server".
  const [hosts, setHosts] = useState<HostOut[] | null>(null);
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<"live" | "info">("live");
  // Der Server gehört zur Aktion: wechselt man währenddessen den Server, gehört sie nicht mehr zur Ansicht.
  const [busy, setBusy] = useState<{ hostId: string; unit: string } | null>(null);
  const [message, setMessage] = useState<Message | null>(null);
  const unmountSignal = useUnmountSignal();
  const canRestart = deck().hasPermission("hosts.execute");
  const hostIdRef = useRef(hostId);
  const tabRef = useRef(tab);
  hostIdRef.current = hostId;
  tabRef.current = tab;
  const busyUnit = busy && busy.hostId === hostId ? busy.unit : null;

  const fetchLive = useCallback(async (id: string): Promise<LiveData> => {
    const res = await authedFetch(`/ext/system/hosts/${id}/live`);
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(errorFromBody(body, res.status));
    return body as LiveData;
  }, []);

  useEffect(() => {
    authedFetch("/hosts")
      .then((res) => (res.ok ? (res.json() as Promise<HostOut[]>) : []))
      .then((list) => setHosts(list.filter((h) => h.os_family === "linux")))
      .catch(() => setHosts([]));
  }, []);

  const load = useCallback(async (id: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await authedFetch(`/ext/system/hosts/${id}/info`);
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorFromBody(body, res.status));
      // Antwort eines Servers, den man inzwischen verlassen hat: verwerfen.
      if (hostIdRef.current === id) setInfo(body as SystemInfo);
    } catch (err) {
      if (hostIdRef.current === id) {
        setInfo(null);
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setInfo(null);
    if (hostId && tab === "info") void load(hostId);
  }, [hostId, tab, load]);

  const hostName = info?.host.name ?? (hosts ?? []).find((h) => h.id === hostId)?.display_name;

  useEffect(() => { setMessage(null); }, [hostId]);

  /** Vorschlagen und -- wer freigeben darf -- gleich bestätigen (runAction fragt bei
   * längerer Dauer alle 3 s nach). */
  async function restartUnit(unit: string) {
    if (!hostId) return;
    const ok = await deck().confirmDialog(
      `Dienst ${unit} auf ${hostName ?? "diesem Server"} neu starten? Er ist dabei kurz nicht erreichbar.`,
      { title: "Dienst neu starten", confirmLabel: "Neu starten" },
    );
    if (!ok) return;
    setBusy({ hostId, unit });
    setMessage(null);
    // Ergebnis nur zeigen, wenn man noch beim selben Server ist.
    const show = (m: Message) => { if (hostIdRef.current === hostId) setMessage(m); };
    try {
      const run = await runAction(
        `/ext/system/hosts/${encodeURIComponent(hostId)}/services/restart`,
        { method: "POST", body: JSON.stringify({ unit }) },
        { signal: unmountSignal() },
      );
      const status = run.action.status ?? "";
      if (status === "succeeded") show({ kind: "ok", text: run.action.result?.output || `${unit} neu gestartet.` });
      else if (status === "proposed") show({ kind: "ok", text: "Vorgeschlagen – wartet auf Freigabe unter „Aktionen“." });
      else if (isActionRunning(status)) show({ kind: "ok", text: RUNNING_IN_BACKGROUND });
      else show({ kind: "error", text: run.text });
    } catch (err) {
      show({ kind: "error", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy((b) => (b && b.hostId === hostId && b.unit === unit ? null : b));
      if (hostIdRef.current === hostId && tabRef.current === "info") void load(hostId);
    }
  }

  return (
    <div className="p-4 sm:p-6">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h2 className="text-2xl font-semibold tracking-tight">System{hostName ? `: ${hostName}` : ""}</h2>
        <select
          aria-label="Server wählen"
          value={hostId ?? ""}
          onChange={(e) => updateUrl({ host: e.target.value || null })}
          className="rounded bg-white/10 px-2 py-1 text-sm"
        >
          <option value="">Server wählen …</option>
          {(hosts ?? []).map((h) => (
            <option key={h.id} value={h.id}>{h.display_name} ({h.address}){h.credential === null ? " – ohne SSH-Zugang" : ""}</option>
          ))}
        </select>
        {hostId && (
          <div className="flex overflow-hidden rounded-lg border border-white/10 text-xs" role="tablist" aria-label="Ansicht">
            {([["live", "Live"], ["info", "Zustand & Updates"]] as const).map(([key, label]) => (
              <button key={key} type="button" role="tab" aria-selected={tab === key} onClick={() => setTab(key)}
                className={`px-3 py-1 ${tab === key ? "bg-white/15" : "opacity-60 hover:bg-white/5"}`}>
                {label}
              </button>
            ))}
          </div>
        )}
        {hostId && tab === "info" && (
          <button type="button" onClick={() => void load(hostId)} disabled={loading} className="rounded bg-white/10 px-2 py-1 text-xs hover:bg-white/20 disabled:opacity-40">
            {loading ? "Frage ab …" : "Aktualisieren"}
          </button>
        )}
      </div>
      {!hostId && hosts !== null && hosts.length === 0 && (
        <EmptyState
          icon="eye"
          title="Noch kein Linux-Server"
          text="Lege unter Server & Zugänge einen Linux-Server mit SSH-Zugang an. Dann siehst du hier seine Auslastung, Dienste und Updates."
          action={<SettingsLink to="/settings/hosts" permission="hosts.write">Server &amp; Zugänge öffnen</SettingsLink>}
        />
      )}
      {!hostId && hosts !== null && hosts.length > 0 && (
        <p className="text-sm opacity-60">Einen Linux-Server wählen -- oder über dessen Server-Seite „System-Monitor“ öffnen.</p>
      )}
      {hostId && tab === "live" && <LiveView hostId={hostId} fetchLive={fetchLive} />}
      {tab === "info" && error && <p className="text-sm text-red-400">Fehler: {error}</p>}
      {hostId && tab === "info" && loading && !info && <p className="text-sm opacity-60">Frage den Server ab … (dauert gut eine Sekunde, die CPU-Last wird über 1 s gemessen)</p>}
      {tab === "info" && message && <Notice kind={message.kind} text={message.text} onClose={() => setMessage(null)} />}
      {tab === "info" && info && <InfoView info={info} canRestart={canRestart} busyUnit={busyUnit} onRestart={(u) => void restartUnit(u)} />}
    </div>
  );
}
