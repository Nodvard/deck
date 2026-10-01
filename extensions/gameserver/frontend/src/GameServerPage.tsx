/**
 * Gameserver-Seite: je Server
 * Join-Code gross mit Kopieren, Spieler online und zuletzt gesehen, Welt und Sicherungen,
 * Server-Einstellungen, Log -- und Start/Stop/Neustart/Welt sichern ueber das Gate.
 * Alles Spielspezifische kommt aus dem Profil des Servers (Backend `profiles/`); die
 * Seite zeigt nur, was das Profil liefert.
 *
 * Authentifizierung wie die uebrigen Extension-Seiten: `window.__nodvardDeck.getAccessToken()`.
 * Auto-Freigabe: ein Vorschlag (`status=proposed`) wird automatisch freigegeben, wenn
 * der Nutzer `actions.approve:<risk>` hat -- die Rueckfrage vor Stopp/Neustart ist dann
 * die menschliche Bestaetigung; sonst verweist die Seite auf "Aktionen".
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { ACTION_STATUS_LABEL as SHARED_STATUS_LABEL, runAction } from "../../../_shared/frontend/src/actions";
import { authedFetch, errorText } from "../../../_shared/frontend/src/api";
import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { SettingsLink, useSettingText } from "../../../_shared/frontend/src/links";
import { useUrlParams } from "../../../_shared/frontend/src/location";
import { EmptyState } from "../../../_shared/frontend/src/ui";
import { deck } from "../../../_shared/frontend/src/deck";

interface Summary {
  host_id: string;
  name: string;
  status: string;
  service_label: string;
  tone: string;
  running: boolean;
  players_online: number | null;
  version: string | null;
  error: string | null;
  can_start: boolean;
  can_stop: boolean;
  join_code: string | null;
  join_code_display: string;
}

interface Aged {
  name: string;
  size: number;
  age_s: number | null;
}

interface Details extends Summary {
  details: {
    service_name?: string | null;
    process?: { ram_mb: number; cpu_s: number; responding: boolean } | null;
    server?: { name: string | null; world: string | null; port: number | null; crossplay: boolean; public: boolean | null; preset: string | null; modifiers: string[]; has_password: boolean };
    join_code_age_s?: number | null;
    recent_players?: { name: string; last_seen_age_s: number | null }[];
    last_save_age_s?: number | null;
    world?: { name: string | null; size: number; age_s: number | null };
    auto_backups?: Aged[];
    backups?: Aged[];
    log_tail?: string[];
    paths?: { log: string | null; world: string | null; backup: string | null };
    error?: string | null;
  };
  config: { profile: string; values: Record<string, string> };
}

interface Profile {
  id: string;
  label: string;
  fields: { key: string; label: string; help: string; default: string }[];
}

type Action = "start" | "stop" | "restart" | "backup";

/** Wie die uebrigen Seiten, nur "erledigt" statt "abgeschlossen". */
const ACTION_STATUS_LABEL: Record<string, string> = { ...SHARED_STATUS_LABEL, succeeded: "erledigt" };
const ACTION_LABEL: Record<Action, string> = { start: "Starten", stop: "Stoppen", restart: "Neustarten", backup: "Welt sichern" };
const TONE: Record<string, string> = {
  good: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  danger: "bg-red-500/15 text-red-300 border-red-500/30",
  warn: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  neutral: "bg-white/10 text-white/70 border-white/10",
};

export function ago(seconds: number | null | undefined): string {
  if (seconds == null) return "unbekannt";
  if (seconds < 60) return "gerade eben";
  if (seconds < 3600) return `vor ${Math.floor(seconds / 60)} min`;
  if (seconds < 86400) return `vor ${Math.floor(seconds / 3600)} h`;
  return `vor ${Math.floor(seconds / 86400)} T`;
}

function size(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
  return `${Math.round(bytes / 1e3)} KB`;
}

function Stat({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
  return (
    <div className="panel p-4">
      <p className="text-[11px] font-medium uppercase tracking-wider text-white/50">{label}</p>
      <div className="mt-1.5 text-2xl font-semibold tabular-nums">{value}</div>
      {sub && <p className="mt-0.5 text-xs text-white/50">{sub}</p>}
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1.5 text-sm">
      <span className="text-white/55">{label}</span>
      <span className="text-right">{value}</span>
    </div>
  );
}

function SettingsPanel({ server, profiles, onSaved }: { server: Details; profiles: Profile[]; onSaved: () => void }) {
  const [profileId, setProfileId] = useState(server.config.profile);
  const [values, setValues] = useState<Record<string, string>>(server.config.values);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const profile = profiles.find((p) => p.id === profileId);
  const detected: Record<string, string | null | undefined> = {
    service_name: server.details.service_name, log_path: server.details.paths?.log,
    world_dir: server.details.paths?.world, backup_dir: server.details.paths?.backup,
  };

  async function save() {
    setSaving(true);
    setError(null);
    const res = await authedFetch(`/ext/gameserver/servers/${server.host_id}/config`, {
      method: "PUT", body: JSON.stringify({ profile: profileId, values }),
    });
    setSaving(false);
    if (!res.ok) {
      setError(await errorText(res));
      return;
    }
    onSaved();
  }

  return (
    <div className="panel mt-4 p-4" data-testid={`settings-${server.host_id}`}>
      <p className="mb-3 text-sm font-semibold">Einstellungen</p>
      <label className="mb-3 block text-sm">
        <span className="mb-1 block text-xs text-white/55">Spiel-Profil</span>
        <select value={profileId} onChange={(e) => setProfileId(e.target.value)} className="w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2">
          {profiles.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
      </label>
      <div className="grid gap-3 md:grid-cols-2">
        {profile?.fields.map((field) => (
          <label key={field.key} className="block text-sm">
            <span className="mb-1 block text-xs text-white/55">{field.label}</span>
            <input
              value={values[field.key] ?? ""}
              onChange={(e) => setValues({ ...values, [field.key]: e.target.value })}
              placeholder={detected[field.key] ? `automatisch: ${detected[field.key]}` : field.default || "automatisch"}
              aria-label={field.label}
              className="w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 font-mono text-xs placeholder:text-white/30"
            />
            <span className="mt-1 block text-[11px] text-white/40">{field.help}</span>
          </label>
        ))}
      </div>
      {error && <p className="mt-2 text-sm text-red-400">{error}</p>}
      <div className="mt-3 flex justify-end">
        <button type="button" disabled={saving} onClick={() => void save()} className="accent-gradient rounded-lg px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50">
          {saving ? "Speichere …" : "Speichern"}
        </button>
      </div>
    </div>
  );
}

function ServerCard({ server, profiles, onAction, pending, reload }: {
  server: Details; profiles: Profile[]; onAction: (s: Details, a: Action) => void; pending: string | null; reload: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const d = server.details;
  const canConfigure = deck().hasPermission("settings.write");
  const canExecute = deck().hasPermission("hosts.execute");
  const profileLabel = profiles.find((p) => p.id === server.config.profile)?.label ?? server.config.profile;

  async function copy() {
    if (!server.join_code) return;
    try {
      await navigator.clipboard.writeText(server.join_code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Zwischenablage evtl. blockiert -- der Code steht ohnehin sichtbar da.
    }
  }

  const button = (action: Action, style: string) => (
    <button
      key={action}
      type="button"
      disabled={pending === `${server.host_id}:${action}`}
      onClick={() => onAction(server, action)}
      className={`rounded-lg px-3 py-1.5 text-xs font-medium disabled:opacity-40 ${style}`}
    >
      {pending === `${server.host_id}:${action}` ? "…" : ACTION_LABEL[action]}
    </button>
  );

  return (
    <section id={`host-${server.host_id}`} className="mb-8 scroll-mt-4" data-testid={`server-${server.host_id}`}>
      <div className="flex flex-wrap items-center gap-3">
        <span className="accent-gradient grid h-11 w-11 place-items-center rounded-xl text-lg font-bold text-white shadow-lg shadow-black/40">
          {server.name.slice(0, 1).toUpperCase()}
        </span>
        <div className="min-w-0">
          <h3 className="text-lg font-semibold leading-tight">{server.name}</h3>
          <p className="text-xs text-white/50">{profileLabel}{server.version ? ` · Version ${server.version}` : ""}</p>
        </div>
        <span className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${TONE[server.tone] ?? TONE.neutral}`}>{server.service_label}</span>
        <div className="ml-auto flex flex-wrap gap-2">
          {canExecute && server.can_start && button("start", "accent-gradient text-white")}
          {canExecute && server.running && button("restart", "bg-white/10 hover:bg-white/20")}
          {canExecute && server.running && button("backup", "bg-white/10 hover:bg-white/20")}
          {canExecute && server.can_stop && button("stop", "bg-red-500/20 text-red-200 hover:bg-red-500/30")}
          {canConfigure && (
            <button type="button" onClick={() => setShowSettings((v) => !v)} className="rounded-lg bg-white/5 px-3 py-1.5 text-xs hover:bg-white/10">
              {showSettings ? "Einstellungen schließen" : "Einstellungen"}
            </button>
          )}
        </div>
      </div>

      {(server.error || d.error) && <p className="mt-3 text-sm text-red-400">Nicht abrufbar: {server.error ?? d.error}</p>}
      {showSettings && <SettingsPanel server={server} profiles={profiles} onSaved={() => { setShowSettings(false); reload(); }} />}

      <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat
          label="Join-Code"
          value={
            server.join_code ? (
              <span className="flex items-center gap-2">
                <span className="font-mono tracking-[0.2em]">{server.join_code}</span>
                <button type="button" onClick={() => void copy()} className="rounded-md px-2 py-0.5 text-xs font-normal tracking-normal border border-white/10 bg-white/[0.06] hover:bg-white/[0.12] rounded-lg transition">
                  {copied ? "Kopiert!" : "Kopieren"}
                </button>
              </span>
            ) : (
              <span className="text-base text-white/50">–</span>
            )
          }
          sub={server.join_code ? `vergeben ${ago(d.join_code_age_s)}` : server.join_code_display}
        />
        <Stat label="Spieler online" value={server.players_online ?? "–"} sub={d.recent_players?.length ? `${d.recent_players.length} bekannte Spieler` : undefined} />
        <Stat label="Welt gespeichert" value={<span className="text-xl">{ago(d.last_save_age_s)}</span>} sub={d.world?.name ? `${d.world.name} · ${size(d.world.size)}` : undefined} />
        <Stat
          label="Server-Prozess"
          value={<span className="text-xl">{d.process ? `${(d.process.ram_mb / 1024).toFixed(1)} GB` : "–"}</span>}
          sub={d.process ? `RAM · ${d.process.responding ? "reagiert" : "reagiert nicht"}` : "läuft nicht"}
        />
      </div>

      <div className="mt-3 grid gap-3 lg:grid-cols-3">
        <div className="panel p-4">
          <p className="mb-2 text-sm font-semibold">Server</p>
          <div className="divide-y divide-white/5">
            <Row label="Name" value={d.server?.name ?? "–"} />
            <Row label="Welt" value={d.server?.world ?? "–"} />
            <Row label="Port" value={d.server?.port ?? "–"} />
            <Row label="Crossplay" value={d.server ? (d.server.crossplay ? "an" : "aus") : "–"} />
            <Row label="Öffentlich" value={d.server?.public == null ? "–" : d.server.public ? "ja" : "nein"} />
            <Row label="Passwort" value={d.server ? (d.server.has_password ? "gesetzt" : "keins") : "–"} />
            {d.server?.preset && <Row label="Voreinstellung" value={d.server.preset} />}
            {(d.server?.modifiers ?? []).map((m) => <Row key={m} label="Modifikator" value={m} />)}
          </div>
        </div>
        <div className="panel p-4">
          <p className="mb-2 text-sm font-semibold">Zuletzt gesehen</p>
          {(d.recent_players ?? []).length === 0 && <p className="text-sm text-white/50">Noch niemand.</p>}
          <ul className="divide-y divide-white/5">
            {(d.recent_players ?? []).map((p) => (
              <li key={p.name} className="flex justify-between py-1.5 text-sm">
                <span>{p.name}</span>
                <span className="text-white/50">{ago(p.last_seen_age_s)}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="panel p-4">
          <p className="mb-2 text-sm font-semibold">Sicherungen</p>
          <p className="mb-1 text-[11px] uppercase tracking-wider text-white/45">Eigene</p>
          {(d.backups ?? []).length === 0 && <p className="mb-2 text-sm text-white/50">Noch keine -- „Welt sichern“ legt eine an.</p>}
          <ul className="mb-3">
            {(d.backups ?? []).map((b) => (
              <li key={b.name} className="flex justify-between py-1 text-sm"><span className="font-mono text-xs">{b.name}</span><span className="text-white/50">{size(b.size)}</span></li>
            ))}
          </ul>
          <p className="mb-1 text-[11px] uppercase tracking-wider text-white/45">Automatisch (vom Spiel)</p>
          <ul>
            {(d.auto_backups ?? []).map((b) => (
              <li key={b.name} className="flex justify-between py-1 text-sm"><span>{ago(b.age_s)}</span><span className="text-white/50">{size(b.size)}</span></li>
            ))}
          </ul>
        </div>
      </div>

      {(d.log_tail ?? []).length > 0 && (
        <details className="panel mt-3 p-4">
          <summary className="cursor-pointer text-sm font-semibold">Server-Log (letzte {d.log_tail!.length} Zeilen)</summary>
          <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-black/40 p-3 font-mono text-[11px] leading-relaxed text-white/75">
            {d.log_tail!.join("\n")}
          </pre>
        </details>
      )}
    </section>
  );
}

/** Leerzustand: sagt, welche Markierung ein Server braucht (die eingestellte, sonst „gameserver“). */
function NoGameServers() {
  const tag = useSettingText("gameserver", "host_tag", "gameserver");
  return (
    <EmptyState
      icon="tag"
      title="Noch kein Gameserver eingerichtet"
      text={`Gib einem Server unter Server & Zugänge die Markierung „${tag}“, dann erscheint er hier mit Status, Spielern und Welt-Sicherung. Die Markierung lässt sich in den Einstellungen des Moduls ändern.`}
      action={
        <div className="flex flex-wrap justify-center gap-2">
          <SettingsLink to="/settings/hosts" permission="hosts.write">Server &amp; Zugänge öffnen</SettingsLink>
          <SettingsLink to="/settings/extensions/gameserver" permission="extensions.manage" variant="secondary">Moduleinstellungen</SettingsLink>
        </div>
      }
    />
  );
}

export function GameServerPage(): JSX.Element {
  const unmountSignal = useUnmountSignal();
  // Sprung von der Server-Seite des Kerns (`?host=`): dieser Server zuerst, hingescrollt. Folgt
  // der Adresszeile (location.ts) -- jeder Link, auch derselbe noch einmal, scrollt wieder hin.
  const [urlParams, , visits] = useUrlParams();
  const focusHost = urlParams.get("host") || null;
  const [servers, setServers] = useState<Details[] | null>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async (fresh = false) => {
    setError(null);
    try {
      const res = await authedFetch("/ext/gameserver/servers");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const list = (await res.json()) as Summary[];
      // Server-Log und "frisch abfragen" nur mit Ausführen-Recht; Betrachter bekommen
      // die zwischengespeicherten Details ohne Log.
      const full = deck().hasPermission("hosts.execute");
      const details = await Promise.all(
        list.map(async (s) => {
          const path = full
            ? `/ext/gameserver/servers/${s.host_id}/details${fresh ? "?fresh=true" : ""}`
            : `/ext/gameserver/servers/${s.host_id}`;
          const r = await authedFetch(path);
          return r.ok ? ((await r.json()) as Details) : ({ ...s, details: {}, config: { profile: "", values: {} } } as Details);
        }),
      );
      setServers(details);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
    authedFetch("/ext/gameserver/profiles")
      .then((r) => (r.ok ? (r.json() as Promise<Profile[]>) : []))
      .then(setProfiles)
      .catch(() => setProfiles([]));
    const interval = setInterval(() => void load(), 30_000);
    return () => clearInterval(interval);
  }, [load]);

  async function trigger(server: Details, action: Action) {
    if (action === "stop" || action === "restart") {
      const text = action === "stop"
        ? `"${server.name}" wirklich stoppen? Verbundene Spieler fliegen raus.`
        : `"${server.name}" neu starten? Verbundene Spieler fliegen kurz raus, der Join-Code ändert sich.`;
      const ok = await deck().confirmDialog(text, { danger: true, confirmLabel: ACTION_LABEL[action] });
      if (!ok) return;
    }
    setPending(`${server.host_id}:${action}`);
    setMessage(null);
    try {
      const { action: result, approved } = await runAction(`/ext/gameserver/servers/${server.host_id}/${action}`, { method: "POST" }, { signal: unmountSignal() });
      if (!approved && result.status === "proposed") {
        setMessage(`${server.name}: ${ACTION_LABEL[action]} vorgeschlagen -- Freigabe durch einen Admin nötig, siehe "Aktionen".`);
      } else {
        setMessage(`${server.name}: ${ACTION_LABEL[action]} -> ${ACTION_STATUS_LABEL[result.status ?? ""] ?? result.status ?? "?"}.`);
      }
      await load(true);
    } catch (err) {
      setMessage(`Fehler: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPending(null);
    }
  }

  // Einmal je Link hinscrollen, sobald der Server da ist -- nicht bei jedem Neuladen (alle 30 s).
  const scrolledFor = useRef<number | null>(null);
  useEffect(() => {
    if (!servers || !focusHost || scrolledFor.current === visits) return;
    scrolledFor.current = visits;
    document.getElementById(`host-${focusHost}`)?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }, [servers, focusHost, visits]);

  if (!servers && !error) return <div className="p-6 text-sm opacity-60">Lade …</div>;
  if (error) return <div className="p-6 text-sm text-red-400">Fehler: {error}</div>;

  const ordered = [...(servers ?? [])].sort((a, b) => Number(b.host_id === focusHost) - Number(a.host_id === focusHost));

  return (
    <div className="p-4 sm:p-6">
      <div className="mb-6">
        <h2 className="text-2xl font-semibold tracking-tight">Gameserver</h2>
        <p className="text-sm text-white/55">Status, Spieler, Join-Code und Welt-Sicherungen deiner Spielserver.</p>
      </div>
      {message && <p className="panel mb-4 px-4 py-2 text-sm">{message}</p>}
      {servers && servers.length === 0 && (
        <NoGameServers />
      )}
      {ordered.map((server) => (
        <ServerCard key={server.host_id} server={server} profiles={profiles} onAction={(s, a) => void trigger(s, a)} pending={pending} reload={() => void load(true)} />
      ))}
    </div>
  );
}
