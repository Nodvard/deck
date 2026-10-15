/**
 * Update-Zentrale (Reiter "Updates"): anstehende Paket-Updates je Server,
 * Sicherheitsupdates, Neustart-Bedarf, Einspielen per Klick (ueber das Gate) und der
 * Verlauf aller Laeufe. Daten: GET /ext/shield/defender/updates.
 */
import { useCallback, useContext, useEffect, useState } from "react";

import { Badge, Button, Card, EmptyState, Icon, Notice, Stat, type Tone } from "../../../_shared/frontend/src/ui";
import { ServerUnavailableError } from "../../../_shared/frontend/src/api";
import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { describeSchedule } from "../../../_shared/frontend/src/SchedulePicker";
import { API, ago, call, count, HostFilter, proposeAndApprove, useForHost, when } from "./api";
import { deck } from "../../../_shared/frontend/src/deck";
import { ServerSetupLink } from "./ServerSetupLink";

interface Pkg {
  name: string;
  new_version: string;
  current_version: string | null;
  repo: string | null;
  security: boolean;
}

interface UpdateState {
  manager: string | null;
  packages: Pkg[];
  count: number;
  security_count: number;
  reboot_required: boolean;
  reboot_reasons: string[];
  kernel: string | null;
  latest_kernel: string | null;
  uptime_s: number | null;
  unattended: boolean | null;
  refresh_error: string | null;
  error: string | null;
  /** Kein Paketmanager, den die Update-Zentrale kennt (z. B. ein NAS mit eigener Firmware): kein Fehler (fehlt bei älteren Ständen). */
  unsupported?: boolean;
  /** Letzte erfolgreiche Prüfung (leer, wenn noch keine gelang). */
  checked_at: number | null;
  /** Letzter Versuch, auch fehlgeschlagen (fehlt bei älteren Ständen). */
  attempted_at?: number | null;
  /** Neustart ausgeloest -- die naechste erfolgreiche Pruefung ersetzt den Stand und entfernt das Feld. */
  reboot_pending_since?: number;
  /** Offene Updates, die den Docker-Dienst neu starten (fehlt, wenn keine dabei sind). */
  docker_packages?: { all: string[]; security: string[] };
  /** Läuft Nodvard Deck auf diesem Server? null: ließ sich nicht prüfen (nur gesetzt, wenn Docker-Pakete warten). */
  deck_host?: boolean | null;
}

export interface UpdateRun {
  id: string;
  host_id: string;
  host_name: string;
  mode: string;
  mode_label: string;
  trigger: string;
  status: string;
  upgraded: number;
  summary: string | null;
  output_tail: string | null;
  started_at: number;
  finished_at: number | null;
}

interface HostUpdates {
  host_id: string;
  host_name: string;
  host_status: string;
  status: UpdateState | null;
  checking: boolean;
  busy: string | null;
  /** Der letzte Prüfversuch liegt vor dem zuletzt fälligen Zeitpunkt des Zeitplans – die geplante Prüfung ist für diesen Server ausgeblieben. */
  check_overdue?: boolean;
  last_run: UpdateRun | null;
}

export interface UpdatesOverview {
  hosts: HostUpdates[];
  summary: { hosts: number; checked: number; up_to_date: number; packages: number; security: number; reboot: number; errors: number };
  runs: UpdateRun[];
  config: {
    check_enabled: boolean; check_cron: string; auto_enabled: boolean; auto_mode: string; auto_cron: string;
    auto_reboot: boolean; auto_tag: string | null;
    /** Einstellung „Diese Server nie automatisch neu starten“ (Feld fehlt bei aelteren Servern). */
    auto_reboot_skip?: string[];
    /** Einstellung „Proxmox: Alle Updates als dist-upgrade“ (Feld fehlt bei aelteren Servern). */
    proxmox_dist_upgrade?: boolean;
  };
}

type Mode = "all" | "security" | "cleanup" | "reboot";

/** Arten des Sammel-Vorschlags: Neustart und Aufräumen bleiben Einzelentscheidungen je Server. */
type BulkMode = "security" | "all";

/** Antwort von `POST /defender/updates/propose-all`: je Server ein Ergebnis plus Zählung. */
export interface ProposeAllResult {
  mode: BulkMode;
  results: { host_id: string; name: string; result: "proposed" | "skipped" | "failed"; reason?: string; action_id?: string; status?: string; risk?: string }[];
  counts: { proposed: number; skipped: number; failed: number; total: number };
}

/** Wo die Vorschläge freigegeben werden (Aktionen-Seite des Dashboards, dort gibt es die Sammel-Freigabe). */
const ACTIONS_PATH = "/actions";

/** Wie auf der Aktionen-Seite: Vorschläge mit diesem Risiko lassen sich nicht in der Sammel-Freigabe bestätigen, nur einzeln
 *  (so steht der Befehl vor jeder Freigabe vor Augen). Mit „Alle Updates“ und der Proxmox-Option haben sie „hoch“. */
const SINGLE_APPROVAL_RISKS = ["high", "critical"];

const reasonText = (r?: string) => (r ?? "").replace(/\.$/, "");

/** Nach einem Ausfall (Nodvard Deck startet etwa nach einem Docker-Update neu) versucht es der Reiter nach dieser Pause
 *  selbst wieder. */
export const RETRY_MS = 5000;

/** Läufe, deren Ergebnis die Seite noch zeigen will, auch über ein Neuladen hinweg: Startet Nodvard Deck beim Einspielen
 *  auf seinem eigenen Server neu, endet die Aktion als „unterbrochen“, der Lauf auf dem Server geht aber weiter. */
export const FOLLOW_KEY = "nodvard-shield-updates-follow";
/** Erscheint in dieser Zeit kein neuer Lauf (die Aktion wartet etwa auf Freigabe), gibt die Seite das Folgen auf. */
const FOLLOW_START_MS = 10 * 60 * 1000;
/** Länger wartet auch Nodvard Shield nicht auf einen Lauf (Wartezeit und spätes Nachfragen zusammen). */
const FOLLOW_MAX_MS = 3 * 3600 * 1000;

interface Follow {
  host_name: string;
  /** Letzter Lauf vor dem Einspielen: Jeder andere ist der neue. */
  prev_run: string | null;
  since: number;
}

function readFollows(): Record<string, Follow> {
  try {
    const raw = JSON.parse(window.sessionStorage.getItem(FOLLOW_KEY) ?? "{}") as unknown;
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
    const out: Record<string, Follow> = {};
    for (const [id, f] of Object.entries(raw as Record<string, Partial<Follow>>)) {
      if (f && typeof f.host_name === "string" && typeof f.since === "number" && (f.prev_run === null || typeof f.prev_run === "string")) {
        out[id] = { host_name: f.host_name, prev_run: f.prev_run, since: f.since };
      }
    }
    return out;
  } catch {
    return {};
  }
}

function writeFollows(follows: Record<string, Follow>): void {
  try {
    if (Object.keys(follows).length) window.sessionStorage.setItem(FOLLOW_KEY, JSON.stringify(follows));
    else window.sessionStorage.removeItem(FOLLOW_KEY);
  } catch {
    // Ohne Speicher (privates Fenster) folgt die Seite nur, solange sie offen bleibt.
  }
}

function without<T>(record: Record<string, T>, keys: string[]): Record<string, T> {
  const next = { ...record };
  for (const k of keys) delete next[k];
  return next;
}

/** Ergebnis eines Laufs als Hinweis: maßgeblich ist der Lauf auf dem Server, nicht die (vielleicht unterbrochene) Aktion. */
function runNotice(name: string, run: UpdateRun): { kind: "ok" | "error"; text: string } {
  return run.status === "ok"
    ? { kind: "ok", text: `${name}: ${run.summary ?? "Erledigt."}` }
    : { kind: "error", text: `${name}: Fehlgeschlagen: ${run.summary ?? "unbekannter Fehler"}` };
}

const RESUMED_TEXT = "Nodvard Deck wurde zwischendurch neu gestartet. Das Einspielen läuft auf dem Server weiter, das Ergebnis erscheint hier.";

/** Satz für die Rückfrage, wenn das Einspielen Docker neu startet und hier (vielleicht) Nodvard Deck läuft. */
export function deckRestartHint(st: UpdateState | null | undefined, mode: string): string {
  const names = mode === "security" ? st?.docker_packages?.security : mode === "all" ? st?.docker_packages?.all : undefined;
  if (!names?.length || st?.deck_host === false) return "";
  const which = names.length === 1 ? `ist das Docker-Paket ${names[0]}` : `sind die Docker-Pakete ${names.join(", ")}`;
  return st?.deck_host
    ? ` Dabei ${which}, und hier läuft Nodvard Deck: Es ist danach kurz nicht erreichbar und lädt von selbst neu.`
    : ` Dabei ${which}. Läuft hier Nodvard Deck, ist es danach kurz nicht erreichbar und lädt von selbst neu.`;
}

const RUN_STATUS: Record<string, { label: string; tone: Tone }> = {
  running: { label: "läuft", tone: "info" },
  ok: { label: "erfolgreich", tone: "good" },
  error: { label: "Fehler", tone: "bad" },
};

function uptime(s: number | null): string {
  if (s == null) return "–";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  return d > 0 ? `${d} T ${h} Std.` : `${h} Std. ${Math.floor((s % 3600) / 60)} Min.`;
}

function clock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
}

function hostState(h: HostUpdates): { label: string; tone: Tone } {
  if (h.busy) return { label: h.busy === "reboot" ? "startet neu …" : "spielt ein …", tone: "info" };
  if (h.checking) return { label: "prüft …", tone: "info" };
  const st = h.status;
  if (!st) return { label: "noch nicht geprüft", tone: "neutral" };
  if (st.unsupported) return { label: "nicht unterstützt", tone: "neutral" };
  if (st.error && !st.manager) return { label: "Fehler", tone: "warn" };
  if (st.security_count > 0) return { label: `${st.security_count} Sicherheitsupdate${st.security_count === 1 ? "" : "s"}`, tone: "bad" };
  if (st.count > 0) return { label: `${st.count} Update${st.count === 1 ? "" : "s"}`, tone: "warn" };
  return { label: "aktuell", tone: "good" };
}

export function UpdatesTab({ canManage }: { canManage: boolean }) {
  const unmountSignal = useUnmountSignal();
  const [data, setData] = useState<UpdatesOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [openRun, setOpenRun] = useState<string | null>(null);
  const [pending, setPending] = useState<Record<string, Mode>>({});
  const [proposing, setProposing] = useState<BulkMode | null>(null);
  const [proposed, setProposed] = useState<ProposeAllResult | null>(null);
  // Ein einzelner Server (Server-Seite, `?host=`): dort gibt es nur dessen eigene Knöpfe, keine Sammel-Knöpfe.
  const onlyHost = useContext(HostFilter);
  const [follows, setFollows] = useState<Record<string, Follow>>(readFollows);
  // Fehlversuche in Folge, weil Nodvard Deck nicht antwortet (0: erreichbar).
  const [unreachable, setUnreachable] = useState(0);

  const load = useCallback(() => {
    call<UpdatesOverview>(`${API}/updates`)
      .then((d) => { setData(d); setError(null); setUnreachable(0); })
      .catch((err: unknown) => {
        if (err instanceof ServerUnavailableError) setUnreachable((n) => n + 1);
        else setError(err instanceof Error ? err.message : String(err));
      });
  }, []);

  useEffect(() => { load(); }, [load]);
  // Nicht erreichbar: nach einer kurzen Pause selbst noch einmal, bis es wieder klappt.
  useEffect(() => {
    if (!unreachable) return;
    const t = setTimeout(load, RETRY_MS);
    return () => clearTimeout(t);
  }, [unreachable, load]);
  useEffect(() => writeFollows(follows), [follows]);
  const busy = Object.keys(pending).length > 0 || Object.keys(follows).length > 0 || !!data?.hosts.some((h) => h.checking || h.busy);
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [busy, load]);

  // Läufe, denen die Seite folgt (nicht die, die `apply` gerade selbst abwartet): Ist der neue Lauf fertig, steht sein
  // Ergebnis da. Kommt keiner oder dauert es zu lange, hört die Seite auf zu folgen.
  useEffect(() => {
    if (!data) return;
    const now = Date.now();
    const done: string[] = [];
    for (const [hostId, f] of Object.entries(follows)) {
      if (pending[hostId]) continue;
      const run = data.hosts.find((h) => h.host_id === hostId)?.last_run;
      if (run && run.id !== f.prev_run) {
        if (run.status !== "running") {
          setNotice(runNotice(f.host_name, run));
          done.push(hostId);
        } else if (now - f.since > FOLLOW_MAX_MS) done.push(hostId);
      } else if (now - f.since > FOLLOW_START_MS) done.push(hostId);
    }
    if (done.length) setFollows((fs) => without(fs, done));
  }, [data, follows, pending]);

  async function checkAll(hostIds: string[] | "all" = "all") {
    setNotice(null);
    try {
      const r = await call<{ hosts: number }>(`${API}/updates/check`, { method: "POST", body: JSON.stringify({ host_ids: hostIds }) });
      setNotice({ kind: "ok", text: r.hosts ? `${r.hosts} Server werden geprüft …` : "Prüfung läuft bereits." });
      load();
    } catch (err) {
      setNotice({ kind: "error", text: err instanceof Error ? err.message : String(err) });
    }
  }

  /** Server, für die der Sammel-Vorschlag in dieser Art etwas anlegen würde (dieselben Bedingungen wie die Einzel-Knöpfe;
   *  ein Server, auf dem gerade etwas läuft, bekommt keinen Vorschlag). */
  function proposable(mode: BulkMode): HostUpdates[] {
    return (data?.hosts ?? []).filter((h) => {
      const st = h.status;
      return !!st && !st.unsupported && !!st.manager && !h.busy && (mode === "security" ? st.security_count : st.count) > 0;
    });
  }

  async function proposeAll(mode: BulkMode) {
    const targets = proposable(mode);
    if (targets.length === 0) return;
    const n = targets.length;
    const where = n === 1 ? "1 Server" : `${n} Servern`;
    // Wie beim Einzelweg: „Alle Updates“ nimmt auf Proxmox dist-upgrade, wenn die Einstellung an ist (hohes Risiko). Das
    // gilt für jeden apt-Server (ob Proxmox, entscheidet erst der Befehl) – und hohes Risiko heißt: nur einzeln freigeben.
    const distUpgrade = mode === "all" && !!data?.config.proxmox_dist_upgrade && targets.some((h) => h.status?.manager === "apt");
    const single = distUpgrade ? targets.filter((h) => h.status?.manager === "apt").map((h) => h.host_name) : [];
    const approval = single.length === 0
      ? " Dort kannst du sie auch gesammelt freigeben."
      : single.length === n
        ? " Mit „apt-get dist-upgrade“ (kann Pakete entfernen oder ersetzen) haben sie hohes Risiko: Du gibst sie einzeln frei, sie stehen nicht in der Sammel-Freigabe."
        : ` Mit „apt-get dist-upgrade“ (kann Pakete entfernen oder ersetzen) haben die Vorschläge für ${single.join(", ")} hohes Risiko: Die gibst du einzeln frei, die übrigen auch gesammelt.`;
    const text = `Vorschläge für ${mode === "security" ? "die Sicherheitsupdates" : "alle Updates"} auf ${where} anlegen (${targets.map((h) => h.host_name).join(", ")})? `
      + "Eingespielt wird erst nach deiner Freigabe unter „Aktionen“ – außer unter Einstellungen → Automatik steht „Selbstständig handeln“: Dann startet es bei passender Risikostufe sofort."
      + approval;
    const ok = await deck().confirmDialog(text, { confirmLabel: "Vorschläge anlegen", danger: distUpgrade });
    if (!ok) return;
    setNotice(null);
    setProposed(null);
    setProposing(mode);
    try {
      // Genau die Server, die du in der Rückfrage gesehen hast – nicht „alle, die gerade etwas offen haben“.
      const r = await call<ProposeAllResult>(`${API}/updates/propose-all`, {
        method: "POST", body: JSON.stringify({ mode, host_ids: targets.map((h) => h.host_id) }), signal: unmountSignal(),
      });
      setProposed(r);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setNotice({ kind: "error", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setProposing(null);
      load();
    }
  }

  async function apply(h: HostUpdates, mode: Mode) {
    const st = h.status;
    // "Alle Updates" laeuft auf Proxmox als dist-upgrade, wenn die Einstellung an ist -- das kann
    // Pakete entfernen und gilt als "hoch". Ob der Server Proxmox ist, weiss erst der Befehl selbst.
    const distUpgrade = mode === "all" && st?.manager === "apt" && !!data?.config.proxmox_dist_upgrade;
    const questions: Record<Mode, string> = {
      security: `${count(st?.security_count ?? 0, "Sicherheitsupdate", "Sicherheitsupdates")} auf „${h.host_name}“ jetzt einspielen?${deckRestartHint(st, "security")}`,
      all: `${(st?.count ?? 0) === 1 ? "Das Update" : `Alle ${st?.count ?? 0} Updates`} auf „${h.host_name}“ jetzt einspielen? Das kann einige Minuten dauern.${
        distUpgrade ? " Ist es ein Proxmox-Server, läuft dabei „apt-get dist-upgrade“ – das kann Pakete entfernen oder ersetzen." : ""
      }${deckRestartHint(st, "all")}`,
      cleanup: `Auf „${h.host_name}“ nicht mehr benötigte Pakete und alte Kernel entfernen und den Paket-Cache leeren?`,
      reboot: `„${h.host_name}“ jetzt neu starten? Der Server ist dann ein paar Minuten nicht erreichbar – alle Dienste darauf auch.`,
    };
    const labels: Record<Mode, string> = { security: "Einspielen", all: "Alle einspielen", cleanup: "Aufräumen", reboot: "Neu starten" };
    const ok = await deck().confirmDialog(questions[mode], { confirmLabel: labels[mode], danger: mode === "reboot" || distUpgrade });
    if (!ok) return;
    setPending((p) => ({ ...p, [h.host_id]: mode }));
    setNotice({ kind: "ok", text: mode === "reboot" ? `Neustart von ${h.host_name} wird ausgelöst …` : `${h.host_name}: läuft – du kannst die Seite offen lassen oder weiterarbeiten.` });
    const prevRun = h.last_run?.id ?? null;
    if (mode !== "reboot") setFollows((fs) => ({ ...fs, [h.host_id]: { host_name: h.host_name, prev_run: prevRun, since: Date.now() } }));
    try {
      const text = await proposeAndApprove(`${API}/hosts/${h.host_id}/upgrade`, { mode }, mode === "reboot" ? "high" : "medium", unmountSignal());
      let notice: { kind: "ok" | "error"; text: string } = { kind: text.startsWith("Fehlgeschlagen") ? "error" : "ok", text: `${h.host_name}: ${text}` };
      let keepFollowing = false;
      if (mode !== "reboot" && text !== "Erledigt.") {
        // Endet die Aktion anders als „erledigt“ (etwa „durch Neustart unterbrochen“), zählt der Lauf auf dem Server.
        const fresh = await call<UpdatesOverview>(`${API}/updates`).catch(() => null);
        if (fresh) setData(fresh);
        const run = fresh?.hosts.find((x) => x.host_id === h.host_id)?.last_run;
        if (run && run.id !== prevRun) {
          if (run.status === "running") {
            notice = { kind: "ok", text: `${h.host_name}: ${RESUMED_TEXT}` };
            keepFollowing = true;
          } else notice = runNotice(h.host_name, run);
        }
      }
      if (!keepFollowing) setFollows((fs) => without(fs, [h.host_id]));
      setNotice(notice);
    } catch (err) {
      if (!(err instanceof ServerUnavailableError)) setFollows((fs) => without(fs, [h.host_id]));
      setNotice({ kind: "error", text: `${h.host_name}: ${err instanceof Error ? err.message : String(err)}` });
    } finally {
      setPending((p) => {
        const next = { ...p };
        delete next[h.host_id];
        return next;
      });
      load();
    }
  }

  async function installUnattended(h: HostUpdates) {
    const ok = await deck().confirmDialog(
      `Auf „${h.host_name}“ automatische Sicherheitsupdates des Systems (unattended-upgrades) einrichten? Der Server spielt Sicherheitsupdates dann jeden Tag selbst ein.`,
      { confirmLabel: "Einrichten" },
    );
    if (!ok) return;
    try {
      const text = await proposeAndApprove(`${API}/hosts/${h.host_id}/install`, { package: "unattended" }, "medium", unmountSignal());
      setNotice({ kind: text.startsWith("Fehlgeschlagen") ? "error" : "ok", text: `${h.host_name}: ${text}` });
      void checkAll([h.host_id]);
    } catch (err) {
      setNotice({ kind: "error", text: err instanceof Error ? err.message : String(err) });
    }
  }

  const hosts = useForHost(data?.hosts ?? []);
  const runs = useForHost(data?.runs ?? []);
  const offline = unreachable > 0 && (
    <div data-testid="updates-offline">
      <Notice text="Nodvard Deck ist gerade nicht erreichbar, zum Beispiel weil es nach einem Update neu startet. Die Seite versucht es in ein paar Sekunden selbst wieder." />
    </div>
  );
  if (error) return <Notice text={`Fehler: ${error}`} />;
  if (!data) return offline || <p className="text-sm text-white/50">Lade …</p>;
  const { summary: sm, config: c } = data;
  const unsupported = data.hosts.filter((h) => h.status?.unsupported).length;

  if (hosts.length === 0) {
    return <EmptyState icon="package" title="Keine Server" text="Sobald Linux-Server mit SSH-Zugang angelegt sind, zeigt die Update-Zentrale hier ihre anstehenden Updates." action={<ServerSetupLink className="text-sm" />} />;
  }

  return (
    <>
      {offline}
      {notice && <Notice text={notice.text} kind={notice.kind} onClose={() => setNotice(null)} />}

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Server aktuell" value={`${sm.up_to_date}/${sm.hosts}`} tone={sm.up_to_date === sm.hosts ? "good" : undefined}
          hint={[
            sm.checked + unsupported < sm.hosts ? `${sm.hosts - sm.checked - unsupported} noch nicht geprüft` : "",
            unsupported ? `${unsupported} nicht unterstützt` : "",
          ].filter(Boolean).join(" · ") || undefined} />
        <Stat label="Offene Updates" value={sm.packages} tone={sm.packages ? "warn" : "good"} />
        <Stat label="Sicherheitsupdates" value={sm.security} tone={sm.security ? "bad" : "good"} />
        <Stat label="Neustart nötig" value={sm.reboot} tone={sm.reboot ? "warn" : undefined} hint={sm.errors ? `${count(sm.errors, "Prüfung", "Prüfungen")} fehlgeschlagen` : undefined} />
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Badge tone={c.check_enabled ? "info" : "neutral"}><Icon name="clock" size={11} /> Prüfung: {c.check_enabled ? describeSchedule(c.check_cron, "aus") : "aus"}</Badge>
        <Badge tone={c.auto_enabled ? "good" : "neutral"}>
          Automatisch einspielen: {c.auto_enabled ? `${c.auto_mode === "all" ? "alle Updates" : "Sicherheitsupdates"} · ${describeSchedule(c.auto_cron, "aus")}${c.auto_tag ? ` · nur „${c.auto_tag}“` : ""}` : "aus"}
        </Badge>
        {c.auto_enabled && (
          <Badge tone={c.auto_reboot ? "warn" : "neutral"}>
            Neustart danach: {c.auto_reboot ? "automatisch" : "nein"}
            {c.auto_reboot && c.auto_reboot_skip?.length ? `, außer ${c.auto_reboot_skip.join(", ")}` : ""}
          </Badge>
        )}
        <span className="flex-1" />
        {canManage && !onlyHost && (["security", "all"] as const).map((mode) => {
          const n = proposable(mode).length;
          if (n === 0) return null;
          return (
            <Button key={mode} variant={mode === "security" ? "primary" : "secondary"} disabled={proposing !== null} onClick={() => void proposeAll(mode)}
              title="Legt für jeden dieser Server einen Vorschlag an. Freigegeben wird danach unter „Aktionen“.">
              {proposing === mode ? "Lege Vorschläge an …" : `${mode === "security" ? "Alle Sicherheitsupdates vorschlagen" : "Alle Updates vorschlagen"} (${n})`}
            </Button>
          );
        })}
        {canManage && <Button onClick={() => void checkAll()}><Icon name="refresh" size={14} /> Alle jetzt prüfen</Button>}
      </div>
      {/* Zwei Wege, die nebeneinander stehen: das Einspielen durch Nodvard Deck und unattended-upgrades auf dem Server. */}
      <p className="-mt-2 mb-4 text-xs text-white/50" data-testid="auto-explain">
        Updates automatisch einspielen geht auf zwei Wegen: Nodvard Deck spielt sie nach seinem Zeitplan ein („Updates
        automatisch einspielen“ in den Einstellungen von Nodvard Shield), oder ein Debian- oder Ubuntu-Server spielt
        Sicherheitsupdates jeden Tag selbst ein (unattended-upgrades). Einer der beiden Wege reicht.
      </p>

      {proposed && <ProposedSummary result={proposed} onClose={() => setProposed(null)} />}

      <div className="space-y-3" data-testid="update-hosts">
        {hosts.map((h) => {
          const st = h.status;
          const state = hostState(h);
          const running = pending[h.host_id] ?? (h.busy as Mode | null);
          // Nach „Neu starten“ gilt der alte Stand nicht mehr – nicht erneut „Neustart nötig“
          // samt Knopf anzeigen, bis eine Prüfung den neuen Stand geholt hat.
          const rebootSince = st?.reboot_pending_since;
          const needsReboot = !!st?.reboot_required && !rebootSince;
          const isOpen = open === h.host_id;
          return (
            <div key={h.host_id} className="panel px-4 py-3" data-testid={`updates-${h.host_id}`}>
              <div className="flex flex-wrap items-center gap-3">
                <div className="min-w-0 flex-1">
                  <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                    {h.host_name}
                    <Badge tone={state.tone}>{state.label}</Badge>
                    {needsReboot && !h.busy && <Badge tone="warn">Neustart nötig</Badge>}
                    {st?.unattended === true && <Badge tone="good">Auto-Sicherheitsupdates an</Badge>}
                  </p>
                  <p className="mt-0.5 text-xs text-white/45">
                    {st?.manager ? `${st.manager} · Kernel ${st.kernel ?? "?"} · läuft seit ${uptime(st.uptime_s)} · ` : ""}
                    {st?.checked_at ? `geprüft ${ago(st.checked_at)}` : "noch nie erfolgreich geprüft"}
                    {st?.error && !st.unsupported && st.attempted_at ? ` · letzter Versuch ${ago(st.attempted_at)} fehlgeschlagen` : ""}
                  </p>
                  {h.check_overdue && (
                    <p className="mt-1 text-xs text-amber-300" data-testid={`updates-overdue-${h.host_id}`}>
                      Die geplante Prüfung ist hier ausgeblieben (das Dashboard lief zur Prüfzeit nicht). Der Stand kann veraltet sein – „Prüfen“ holt ihn nach.
                    </p>
                  )}
                  {st?.error && <p className={`mt-1 text-xs ${st.unsupported ? "text-white/50" : "text-amber-300"}`}>{st.error}</p>}
                  {st?.refresh_error && <p className="mt-1 text-xs text-amber-300/80">{st.refresh_error}</p>}
                  {needsReboot && st.reboot_reasons.length > 0 && (
                    <p className="mt-1 text-xs text-white/50">Grund: {st.reboot_reasons.slice(0, 3).join(", ")}</p>
                  )}
                  {rebootSince && !h.busy && (
                    <p className="mt-1 text-xs text-sky-300">Neustart ausgelöst ({clock(rebootSince)}) – die nächste Prüfung zeigt den neuen Stand.</p>
                  )}
                  {h.last_run && (
                    <p className="mt-1 text-xs text-white/45">
                      Zuletzt: {h.last_run.mode_label} {when(h.last_run.started_at)} –{" "}
                      <span className={h.last_run.status === "error" ? "text-red-300" : ""}>{h.last_run.summary ?? RUN_STATUS[h.last_run.status]?.label}</span>
                    </p>
                  )}
                </div>
                {canManage && (
                  <div className="flex flex-wrap gap-1">
                    {!!st?.security_count && <Button small variant="primary" disabled={!!running} onClick={() => void apply(h, "security")}>Sicherheitsupdates einspielen</Button>}
                    {!!st?.count && <Button small variant={st.security_count ? "secondary" : "primary"} disabled={!!running} onClick={() => void apply(h, "all")}>Alle einspielen</Button>}
                    {needsReboot && <Button small variant="danger" disabled={!!running} onClick={() => void apply(h, "reboot")}>Neu starten</Button>}
                    <Button small variant="ghost" disabled={h.checking || !!running} onClick={() => void checkAll([h.host_id])} ariaLabel={`${h.host_name} prüfen`}>
                      <Icon name="refresh" size={12} /> Prüfen
                    </Button>
                  </div>
                )}
              </div>

              {st && st.count > 0 && (
                <button type="button" onClick={() => setOpen(isOpen ? null : h.host_id)} className="mt-2 flex items-center gap-1 text-xs text-white/55 hover:text-white">
                  <Icon name={isOpen ? "chevron-down" : "chevron-right"} size={12} /> {st.count} Paket{st.count === 1 ? "" : "e"} anzeigen
                </button>
              )}
              {isOpen && st && (
                <div className="mt-2 max-h-80 overflow-auto rounded-lg border border-white/[0.06]">
                  <table className="w-full text-left text-xs">
                    <thead className="sticky top-0 bg-[var(--color-surface-head)] text-white/45">
                      <tr><th className="px-3 py-1.5 font-medium">Paket</th><th className="px-3 py-1.5 font-medium">installiert</th><th className="px-3 py-1.5 font-medium">neu</th><th className="px-3 py-1.5" /></tr>
                    </thead>
                    <tbody className="divide-y divide-white/[0.04]">
                      {st.packages.map((p) => (
                        <tr key={p.name}>
                          <td className="px-3 py-1.5 font-mono">{p.name}</td>
                          <td className="px-3 py-1.5 font-mono text-white/50">{p.current_version ?? "–"}</td>
                          <td className="px-3 py-1.5 font-mono">{p.new_version}</td>
                          <td className="px-3 py-1.5 text-right">{p.security && <Badge tone="bad">Sicherheit</Badge>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* „Aufräumen“ für jeden apt-Server; der Tipp davor nur, wenn er noch etwas bringt. */}
              {canManage && st?.manager === "apt" && (
                <p className="mt-2 flex flex-wrap items-center gap-2 text-xs text-white/50">
                  {/* Spielt Nodvard Deck selbst ein (für alle Server, ohne Markierung), braucht es den Tipp nicht. Mit
                      Markierung ist hier nicht bekannt, ob dieser Server dazugehört: dann bleibt der Tipp. */}
                  {st.unattended === false && !(c.auto_enabled && !c.auto_tag) && (
                    <>
                      Tipp: Der Server kann Sicherheitsupdates auch jeden Tag selbst einspielen.
                      <button type="button" onClick={() => void installUnattended(h)} className="text-[var(--color-accent)] hover:underline">Einrichten</button>
                      <span className="text-white/25">·</span>
                    </>
                  )}
                  <button type="button" onClick={() => void apply(h, "cleanup")} className="text-white/60 hover:text-white hover:underline">Aufräumen</button>
                </p>
              )}
            </div>
          );
        })}
      </div>

      <Card title="Verlauf" description="Eingespielte Updates, Aufräumen und Neustarts – manuell und automatisch." className="mt-5" padded={false}>
        <ul className="divide-y divide-white/[0.05]" data-testid="update-runs">
          {runs.length === 0 && <li className="px-5 py-6 text-sm text-white/50">Noch nichts eingespielt.</li>}
          {runs.map((r) => {
            const rs = RUN_STATUS[r.status] ?? { label: r.status, tone: "neutral" as Tone };
            return (
              <li key={r.id} className="px-5 py-2.5 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={rs.tone}>{rs.label}</Badge>
                  <span className="font-medium">{r.host_name}</span>
                  <span className="text-white/60">{r.mode_label}</span>
                  {r.trigger === "schedule" && <Badge>automatisch</Badge>}
                  <span className="text-xs text-white/40">{when(r.started_at)}</span>
                  <span className="min-w-0 flex-1 truncate text-xs text-white/55">{r.summary}</span>
                  {r.output_tail && (
                    <button type="button" onClick={() => setOpenRun(openRun === r.id ? null : r.id)} className="text-xs text-white/50 hover:text-white">
                      {openRun === r.id ? "Ausgabe ausblenden" : "Ausgabe"}
                    </button>
                  )}
                </div>
                {openRun === r.id && r.output_tail && (
                  <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-black/40 p-3 font-mono text-[11px] text-white/70">{r.output_tail}</pre>
                )}
              </li>
            );
          })}
        </ul>
      </Card>
    </>
  );
}

/** Ergebnis des Sammel-Vorschlags: Zählung, was übersprungen wurde oder nicht ging, und der Weg zum Freigeben. */
function ProposedSummary({ result, onClose }: { result: ProposeAllResult; onClose: () => void }) {
  const { counts: c, results } = result;
  const list = (kind: "skipped" | "failed") => results.filter((r) => r.result === kind).map((r) => `${r.name} (${reasonText(r.reason)})`).join("; ");
  const open = results.filter((r) => r.result === "proposed" && r.status === "proposed");
  const waiting = open.length;
  // Hohes Risiko (dist-upgrade) gibt es auf der Aktionen-Seite nur einzeln; ohne Angabe gilt der Normalfall.
  const singleOnly = open.filter((r) => SINGLE_APPROVAL_RISKS.includes(r.risk ?? "")).length;
  const collective = waiting - singleOnly;
  const started = c.proposed - waiting;
  const parts = [`${c.proposed} vorgeschlagen`, ...(c.skipped ? [`${c.skipped} übersprungen`] : []), ...(c.failed ? [`${c.failed} fehlgeschlagen`] : [])];
  const tone = c.failed > 0 ? "border-amber-500/30 bg-amber-500/10 text-amber-200" : "border-emerald-500/30 bg-emerald-500/10 text-emerald-200";
  return (
    <div role="status" data-testid="propose-all-result" className={`mb-4 flex items-start justify-between gap-3 rounded-lg border px-3 py-2 text-sm ${tone}`}>
      <div className="min-w-0 space-y-1">
        <p>{c.proposed === 0 ? "Es wurde kein Vorschlag angelegt." : `${parts.join(", ")}.`}</p>
        {c.skipped > 0 && <p className="break-words opacity-90">Übersprungen: {list("skipped")}.</p>}
        {c.failed > 0 && <p className="break-words opacity-90">Fehlgeschlagen: {list("failed")}.</p>}
        {started > 0 && <p className="opacity-90">{started === 1 ? "Bei 1 Server hat" : `Bei ${started} Servern hat`} die Automatik schon freigegeben – das Einspielen läuft bereits.</p>}
        {waiting > 0 && (
          <p>
            <a href={ACTIONS_PATH} className="font-medium underline">Zu „Aktionen“ zum Freigeben</a>
            {singleOnly === 0
              ? (collective > 1 ? " – dort geht es auch gesammelt." : ".")
              : collective === 0
                ? ` – ${singleOnly === 1 ? "dieser Vorschlag hat" : `diese ${singleOnly} Vorschläge haben`} hohes Risiko und ${singleOnly === 1 ? "wird" : "werden"} einzeln freigegeben, nicht gesammelt.`
                : ` – ${collective === 1 ? "1 Vorschlag" : `${collective} Vorschläge`} kannst du gesammelt freigeben, ${singleOnly === 1 ? "1 mit hohem Risiko" : `${singleOnly} mit hohem Risiko`} nur einzeln.`}
          </p>
        )}
      </div>
      <button type="button" aria-label="Hinweis schließen" onClick={onClose} className="opacity-60 hover:opacity-100"><Icon name="x" size={14} /></button>
    </div>
  );
}
