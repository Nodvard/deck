import { CARD_BODY, CARD_CELL, CARD_CELL_PLAIN, CARD_HEAD, CARD_ROW, CARD_TABLE } from "../../../_shared/frontend/src/cardTable";
/**
 * Update-Zentrale (Reiter „Updates“): anstehende Paket-Updates je Server, Sicherheitsupdates, Neustart-Bedarf,
 * Einspielen per Klick (über das Gate) und der Verlauf aller Läufe. Daten: GET /ext/shield/defender/updates.
 */
import { useCallback, useContext, useEffect, useState } from "react";

import { Badge, Button, Card, EmptyState, Icon, Notice, Stat, type Tone } from "../../../_shared/frontend/src/ui";
import { ServerUnavailableError } from "../../../_shared/frontend/src/api";
import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { describeSchedule } from "../../../_shared/frontend/src/SchedulePicker";
import { API, ago, call, count, HostFilter, proposeAndApprove, track, useForHost, usePageActions, useReloadWhenSettled, when } from "./api";
import { deck } from "../../../_shared/frontend/src/deck";
import { RunNotice, RunNotices, type PageNotice } from "./RunNotice";
import { ServerSetupLink } from "./ServerSetupLink";

interface Pkg {
  name: string;
  new_version: string;
  current_version: string | null;
  repo: string | null;
  security: boolean;
  /** apt hält das Update wegen Phasing noch zurück (fehlt bei älteren Ständen). */
  phased?: boolean;
  /** Kommt nur mit „apt full-upgrade“, weil andere Pakete ersetzt oder entfernt werden müssten (fehlt bei älteren Ständen). */
  kept_back?: boolean;
  /** Auf dem Server mit apt-mark hold gesperrt (fehlt bei älteren Ständen). */
  held?: boolean;
}

interface UpdateState {
  manager: string | null;
  packages: Pkg[];
  count: number;
  security_count: number;
  /** Davon hält apt wegen Phasing noch zurück; `null`, wenn die Prüfung nicht gelang (fehlt bei älteren Ständen). */
  phased_count?: number | null;
  /** `checked`: Phasing geprüft, `unavailable`: Prüfung gescheitert, `none`: kein apt (fehlt bei älteren Ständen). */
  phasing?: "checked" | "unavailable" | "none";
  /** Zurückgehalten, nur mit full-upgrade; `null`, wenn die Probeläufe nicht gelangen (fehlt bei älteren Ständen). */
  kept_back_count?: number | null;
  /** Mit apt-mark hold gesperrt (fehlt bei älteren Ständen). */
  held_count?: number;
  /** Was „Alle einspielen“ einspielen würde, ohne Verzögerte, Zurückgehaltene und Gesperrte (fehlt bei älteren Ständen). */
  installable_count?: number;
  installable_security_count?: number;
  reboot_required: boolean;
  reboot_reasons: string[];
  kernel: string | null;
  latest_kernel: string | null;
  uptime_s: number | null;
  unattended: boolean | null;
  refresh_error: string | null;
  error: string | null;
  error_kind?: "dpkg" | "locked" | "permission" | "timeout" | "detection" | null;
  /** Kein Paketmanager, den die Update-Zentrale kennt (z. B. ein NAS mit eigener Firmware): kein Fehler (fehlt bei älteren Ständen). */
  unsupported?: boolean;
  /** Letzte erfolgreiche Prüfung (leer, wenn noch keine gelang). */
  checked_at: number | null;
  /** Letzter Versuch, auch fehlgeschlagen (fehlt bei älteren Ständen). */
  attempted_at?: number | null;
  /** Neustart ausgelöst; die nächste erfolgreiche Prüfung ersetzt den Stand und entfernt das Feld. */
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
  /** „Alle einspielen“ nimmt hier vermutlich dist-upgrade (Proxmox, Einstellung an): Zurückgehaltene kommen dann mit. */
  full_upgrade?: boolean;
  last_run: UpdateRun | null;
}

export interface UpdatesOverview {
  hosts: HostUpdates[];
  summary: {
    hosts: number; checked: number; up_to_date: number; packages: number; security: number; reboot: number; errors: number;
    /** Davon mit den Knöpfen einspielbar und was nicht, je Grund (fehlt bei älteren Servern). */
    installable?: number; installable_security?: number;
    not_installable?: Reasons; not_installable_security?: Reasons;
  };
  runs: UpdateRun[];
  config: {
    check_enabled: boolean; check_cron: string; auto_enabled: boolean; auto_mode: string; auto_cron: string;
    auto_reboot: boolean; auto_tag: string | null;
    /** Einstellung „Diese Server nie automatisch neu starten“ (Feld fehlt bei älteren Servern). */
    auto_reboot_skip?: string[];
    /** Einstellung „Proxmox: Alle Updates als dist-upgrade“ (Feld fehlt bei älteren Servern). */
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
function runNotice(name: string, run: UpdateRun): PageNotice {
  return run.status === "ok"
    ? { kind: "ok", text: `${name}: ${run.summary ?? "Erledigt."}` }
    : { kind: "error", text: `${name}: Fehlgeschlagen: ${run.summary ?? "unbekannter Fehler"}` };
}

const unattendedKey = (hostId: string) => `${hostId}:unattended`;

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

export const PHASING_HINT =
  "Ubuntu verteilt manche Updates schrittweise: Erst bekommt sie ein Teil der Rechner, nach und nach alle. Bis dieser Server an " +
  "der Reihe ist, hält apt das Update zurück, auch bei „Alle einspielen“. Das ist gewollt und kein Fehler, du musst nichts tun: " +
  "Nodvard Deck prüft weiter, und sobald der Server dran ist, lässt sich das Update wie jedes andere einspielen. " +
  "Sicherheitsupdates verteilt Ubuntu in der Regel sofort an alle.";

export const PHASING_UNCHECKED_HINT =
  "Ob apt hier Updates zurückhält, weil Ubuntu sie schrittweise verteilt (Phasing), zeigt erst die nächste Prüfung. " +
  "„Prüfen“ holt sie gleich nach. Auf Debian und Proxmox gibt es kein Phasing, dort verschwindet der Hinweis danach.";

export const PHASING_UNAVAILABLE_HINT =
  "apt konnte auf diesem Server nicht durchrechnen, welche Updates es einspielen würde, oft wegen fehlender oder kaputter " +
  "Pakete. Deshalb ist unbekannt, ob Ubuntu Updates wegen Phasing zurückhält und ob apt welche zurückhält, die nur mit " +
  "„apt full-upgrade“ kommen; „Alle einspielen“ kann daran scheitern, „Sicherheitsupdates einspielen“ gibt es so lange " +
  "nicht. Den genauen Grund zeigt auf dem Server der Befehl „apt-get -s upgrade“; die nächste Prüfung versucht es erneut.";

/** Ein apt-Server mit Updates, bei dem offen ist, ob Phasing etwas zurückhält: Text und Erklärung, sonst `null`. */
export function phasingUnknown(st: UpdateState | null | undefined): { label: string; hint: string } | null {
  if (!st || st.manager !== "apt" || !st.count || st.phased_count) return null;
  if (st.phasing === "unavailable") return { label: "Phasing-Prüfung nicht verfügbar", hint: PHASING_UNAVAILABLE_HINT };
  return st.phasing ? null : { label: "Phasing noch nicht geprüft", hint: PHASING_UNCHECKED_HINT };
}

export const KEPT_BACK_HINT =
  "apt hält diese Updates zurück, weil dafür andere Pakete ersetzt oder entfernt werden müssten. „Alle einspielen“ entfernt " +
  "nie Pakete und lässt sie deshalb stehen. Einspielen lassen sie sich auf dem Server mit „sudo apt full-upgrade“. Lies " +
  "vorher die Liste, die apt zeigt: Was dort unter „REMOVED“ (deutsch „ENTFERNT“) steht, wird entfernt.";

export const HELD_HINT =
  "Diese Pakete sind auf dem Server gesperrt (apt-mark hold), apt spielt ihre Updates nie von selbst ein. War das Absicht, " +
  "musst du nichts tun. Sonst hebt „sudo apt-mark unhold <Paketname>“ die Sperre auf.";

/** Was „Alle einspielen“ hier einspielen würde; mit dist-upgrade (Proxmox) kommen die zurückgehaltenen mit. Ältere Stände
 *  ohne Einteilung zählen alle außer den verzögerten. */
export function installable(st: UpdateState, fullUpgrade = false): number {
  return (st.installable_count ?? st.count - (st.phased_count ?? 0)) + (fullUpgrade ? st.kept_back_count ?? 0 : 0);
}

export function installableSecurity(st: UpdateState): number {
  return st.installable_security_count ?? Math.max(0, st.security_count -
    st.packages.filter((p) => p.security && (p.phased || p.held || p.kept_back)).length);
}

/** Was „Sicherheitsupdates einspielen“ hier einspielen würde: nichts, solange unbekannt ist, was apt zurückhält. */
export function securityRunnable(st: UpdateState): number {
  return st.phasing === "unavailable" ? 0 : installableSecurity(st);
}

export interface Reasons { phased: number; kept_back: number; held: number; unknown?: number }

/** „2 durch Phasing verzögert, 3 zurückgehalten, 1 gesperrt“; leer, wenn nichts ausgelassen wird. */
export function reasonsText(r: Reasons): string {
  return [
    r.phased ? `${r.phased} durch Phasing verzögert` : "",
    r.kept_back ? `${r.kept_back} zurückgehalten` : "",
    r.held ? `${r.held} gesperrt` : "",
    r.unknown ? `${r.unknown} nicht prüfbar` : "",
  ].filter(Boolean).join(", ");
}

/** Die Kopfzahl „davon …“: je Grund, bei einem älteren Server nur die Summe. */
function leftOutHint(total: number, installable: number | undefined, reasons: Reasons | undefined): string | undefined {
  const left = total - (installable ?? total);
  if (left <= 0) return undefined;
  return `davon ${(reasons && reasonsText(reasons)) || `${left} nicht einspielbar`}`;
}

/** Updates, die „Alle einspielen“ hier nicht einspielt, je Grund. */
function heldBack(st: UpdateState, fullUpgrade = false): { phased: number; kept: number; held: number } {
  return { phased: st.phased_count ?? 0, kept: fullUpgrade ? 0 : st.kept_back_count ?? 0, held: st.held_count ?? 0 };
}

/** Kurze Texte neben dem Abzeichen mit Erklärung auf Klick: was nicht mitkommt und warum, oder was offen ist. */
export function updateNotes(st: UpdateState | null | undefined, fullUpgrade = false): { key: string; label: string; hint: string }[] {
  if (!st) return [];
  const { phased, kept, held } = heldBack(st, fullUpgrade);
  // Ist nichts einspielbar und gibt es nur einen Grund, nennt das Abzeichen schon die Zahl: dann nur noch das Warum.
  const alone = installable(st, fullUpgrade) === 0 && [phased, kept, held].filter(Boolean).length === 1;
  const more = installable(st, fullUpgrade) > 0 ? "dazu " : "";
  const security = (flag: "kept_back" | "held") => st.packages.filter((p) => p[flag] && p.security).length;
  const ofWhich = (n: number) => (n ? `davon ${n} Sicherheit` : "");
  const notes = [];
  if (phased) notes.push({ key: "phased", label: alone ? "durch Phasing verzögert" : `${more}${phased} durch Phasing verzögert`, hint: PHASING_HINT });
  if (kept) {
    const label = alone ? [ofWhich(security("kept_back")), "braucht full-upgrade"] : [`${more}${kept} zurückgehalten`, ofWhich(security("kept_back")), "braucht full-upgrade"];
    notes.push({ key: "kept", label: label.filter(Boolean).join(", "), hint: KEPT_BACK_HINT });
  }
  if (held) {
    const label = alone ? ["mit apt-mark hold gesperrt", ofWhich(security("held"))] : [`${more}${held} gesperrt`, ofWhich(security("held"))];
    notes.push({ key: "held", label: label.filter(Boolean).join(", "), hint: HELD_HINT });
  }
  const unknown = phasingUnknown(st);
  if (unknown) notes.push({ key: "unknown", ...unknown });
  return notes;
}

/** Für die Rückfrage vor „Alle einspielen“: was dabei stehen bleibt. */
export function notIncludedNote(st: UpdateState | null | undefined, fullUpgrade = false): string {
  if (!st) return "";
  const { phased, kept, held } = heldBack(st, fullUpgrade);
  const parts = [
    phased === 1 ? "1 Paket, das Ubuntu wegen Phasing noch zurückhält" : phased ? `${phased} Pakete, die Ubuntu wegen Phasing noch zurückhält` : "",
    kept === 1 ? "1 zurückgehaltenes Paket, das „apt full-upgrade“ auf dem Server braucht"
      : kept ? `${kept} zurückgehaltene Pakete, die „apt full-upgrade“ auf dem Server brauchen` : "",
    held === 1 ? "1 gesperrtes Paket" : held ? `${held} gesperrte Pakete` : "",
  ].filter(Boolean);
  if (!parts.length) return "";
  return ` Nicht dabei: ${parts.join("; ")}.`;
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
  if (st.error) return { label: "Prüfung fehlgeschlagen", tone: "warn" };
  // Mit dist-upgrade kommen zurückgehaltene Sicherheitsupdates über „Alle einspielen“ mit; sie bleiben Sicherheitsupdates.
  const keptSecurity = h.full_upgrade ? st.packages.filter((p) => p.kept_back && p.security).length : 0;
  const sec = installableSecurity(st) + keptSecurity;
  if (sec > 0) return { label: `${sec} Sicherheitsupdate${sec === 1 ? "" : "s"}`, tone: "bad" };
  const n = installable(st, h.full_upgrade);
  if (n > 0) return { label: `${n} Update${n === 1 ? "" : "s"}`, tone: "warn" };
  if (st.count > 0) {
    // Offen, aber „Alle einspielen“ brächte nichts: nicht gelb wie einspielbare Updates.
    const { phased, held } = heldBack(st, h.full_upgrade);
    const only = phased === st.count ? "verzögert" : held === st.count ? "gesperrt" : phased + held ? "nicht einspielbar" : "zurückgehalten";
    // Ein zurückgehaltenes Sicherheitsupdate bleibt ein Risiko.
    return { label: `${st.count} ${only}`, tone: st.security_count > 0 ? "warn" : "neutral" };
  }
  return { label: "aktuell", tone: "good" };
}

export function UpdatesTab({ canManage }: { canManage: boolean }) {
  const unmountSignal = useUnmountSignal();
  const [data, setData] = useState<UpdatesOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<PageNotice | null>(null);
  const page = usePageActions();
  const { keys: { busy: acting }, signal: pageSignal } = page;
  const [open, setOpen] = useState<string | null>(null);
  const [noteOpen, setNoteOpen] = useState<string | null>(null);
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
  useReloadWhenSettled(acting, load);
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

  // Stößt nach einer Aktion eine Prüfung an und lädt neu, ohne den Hinweis anzufassen: Das Ergebnis der Aktion soll stehen bleiben.
  const recheck = (hostId: string) =>
    void call(`${API}/updates/check`, { method: "POST", body: JSON.stringify({ host_ids: [hostId] }) }).catch(() => undefined).finally(() => load());

  /** Server, für die der Sammel-Vorschlag in dieser Art etwas anlegen würde (dieselben Bedingungen wie die Einzel-Knöpfe;
   *  ein Server, auf dem gerade etwas läuft, bekommt keinen Vorschlag). */
  function proposable(mode: BulkMode): HostUpdates[] {
    return (data?.hosts ?? []).filter((h) => {
      const st = h.status;
      return !!st && !st.error && !st.unsupported && !!st.manager && !h.busy && (mode === "security" ? securityRunnable(st) : installable(st, h.full_upgrade)) > 0;
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
    // „Alle Updates“ läuft auf Proxmox als dist-upgrade, wenn die Einstellung an ist; das kann Pakete entfernen und gilt als
    // „hoch“. Ob der Server Proxmox ist, weiß erst der Befehl selbst.
    const distUpgrade = mode === "all" && st?.manager === "apt" && !!data?.config.proxmox_dist_upgrade;
    const sec = st ? installableSecurity(st) : 0;
    const secLeft = (st?.security_count ?? 0) - sec;
    const secPkgs = st?.packages.filter((p) => p.security) ?? [];
    const secReasons = reasonsText({
      phased: secPkgs.filter((p) => p.phased).length, kept_back: secPkgs.filter((p) => p.kept_back).length,
      held: secPkgs.filter((p) => p.held).length,
    });
    const n = st ? installable(st, h.full_upgrade) : 0;
    const questions: Record<Mode, string> = {
      security: `${count(sec, "Sicherheitsupdate", "Sicherheitsupdates")} auf „${h.host_name}“ jetzt einspielen?${
        secLeft > 0 ? ` ${secLeft === 1 ? "Ein weiteres ist" : `${secLeft} weitere sind`} nicht einspielbar${secReasons ? ` (${secReasons})` : ""} und ${secLeft === 1 ? "bleibt" : "bleiben"} stehen.` : ""
      }${deckRestartHint(st, "security")}`,
      all: `${n === 1 ? "Das Update" : `Alle ${n} Updates`} auf „${h.host_name}“ jetzt einspielen? Das kann einige Minuten dauern.${
        distUpgrade ? " Ist es ein Proxmox-Server, läuft dabei „apt-get dist-upgrade“ – das kann Pakete entfernen oder ersetzen." : ""
      }${notIncludedNote(st, h.full_upgrade)}${deckRestartHint(st, "all")}`,
      cleanup: `Auf „${h.host_name}“ nicht mehr benötigte Pakete und alte Kernel entfernen und den Paket-Cache leeren?`,
      reboot: `„${h.host_name}“ jetzt neu starten? Der Server ist dann ein paar Minuten nicht erreichbar – alle Dienste darauf auch.`,
    };
    const labels: Record<Mode, string> = { security: "Einspielen", all: "Alle einspielen", cleanup: "Aufräumen", reboot: "Neu starten" };
    const ok = await deck().confirmDialog(questions[mode], { confirmLabel: labels[mode], danger: mode === "reboot" || distUpgrade });
    if (!ok) return;
    setPending((p) => ({ ...p, [h.host_id]: mode }));
    setNotice({ kind: "info", text: mode === "reboot" ? `Neustart von ${h.host_name} wird ausgelöst …` : `${h.host_name}: läuft – du kannst die Seite offen lassen oder weiterarbeiten.` });
    const prevRun = h.last_run?.id ?? null;
    if (mode !== "reboot") setFollows((fs) => ({ ...fs, [h.host_id]: { host_name: h.host_name, prev_run: prevRun, since: Date.now() } }));
    try {
      const result = await proposeAndApprove(`${API}/hosts/${h.host_id}/upgrade`, { mode }, mode === "reboot" ? "high" : "medium", unmountSignal());
      let notice: PageNotice = { ...result, text: `${h.host_name}: ${result.text}` };
      let keepFollowing = false;
      if (mode !== "reboot" && result.kind !== "ok") {
        // Endet die Aktion anders als „erledigt“ (etwa „durch Neustart unterbrochen“), zählt der Lauf auf dem Server.
        const fresh = await call<UpdatesOverview>(`${API}/updates`).catch(() => null);
        if (fresh) setData(fresh);
        const run = fresh?.hosts.find((x) => x.host_id === h.host_id)?.last_run;
        if (run && run.id !== prevRun) {
          if (run.status === "running") {
            notice = { kind: "info", text: `${h.host_name}: ${RESUMED_TEXT}` };
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
    const progress = `Automatische Sicherheitsupdates werden auf „${h.host_name}“ eingerichtet. Das kann einige Minuten dauern.`;
    await track(page, unattendedKey(h.host_id), h.host_name, progress, async () => {
      const result = await proposeAndApprove(`${API}/hosts/${h.host_id}/install`, { package: "unattended" }, "medium", pageSignal());
      recheck(h.host_id);
      return result;
    });
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
  const errors = data.hosts.filter((h) => h.status?.error && !h.status.unsupported).length;
  const unknownHint = errors ? `Update-Stand für ${count(errors, "Server", "Server")} unbekannt. Erst die Prüfung wiederholen.` : "";

  if (hosts.length === 0) {
    return <EmptyState icon="package" title="Keine Server" text="Sobald Linux-Server mit SSH-Zugang angelegt sind, zeigt die Update-Zentrale hier ihre anstehenden Updates." action={<ServerSetupLink className="text-sm" />} />;
  }

  return (
    <>
      {offline}
      {page.notices && <RunNotices notices={page.notices} onClose={(key) => page.report(key, null)} />}
      {notice && <RunNotice notice={notice} onClose={() => setNotice(null)} />}

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Server aktuell" value={`${sm.up_to_date}/${sm.hosts}`} tone={sm.up_to_date === sm.hosts ? "good" : undefined}
          hint={[
            sm.checked + unsupported < sm.hosts ? `${sm.hosts - sm.checked - unsupported} noch nicht geprüft` : "",
            unsupported ? `${unsupported} nicht unterstützt` : "",
          ].filter(Boolean).join(" · ") || undefined} />
        <Stat label="Offene Updates" value={errors && !sm.packages ? "unbekannt" : sm.packages} tone={(sm.installable ?? sm.packages) ? "warn" : sm.packages || errors ? undefined : "good"}
          hint={[leftOutHint(sm.packages, sm.installable, sm.not_installable), unknownHint].filter(Boolean).join(" ") || undefined} />
        <Stat label="Sicherheitsupdates" value={errors && !sm.security ? "unbekannt" : sm.security} tone={sm.security ? "bad" : errors ? undefined : "good"}
          hint={[leftOutHint(sm.security, sm.installable_security, sm.not_installable_security), unknownHint].filter(Boolean).join(" ") || undefined} />
        <Stat label="Neustart nötig" value={errors && !sm.reboot ? "unbekannt" : sm.reboot} tone={sm.reboot ? "warn" : undefined} hint={errors ? `${count(errors, "Prüfung", "Prüfungen")} fehlgeschlagen` : undefined} />
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
          const readable = !!st && !st.error && !st.unsupported;
          const state = hostState(h);
          const running = pending[h.host_id] ?? (h.busy as Mode | null);
          // Nach „Neu starten“ gilt der alte Stand nicht mehr – nicht erneut „Neustart nötig“
          // samt Knopf anzeigen, bis eine Prüfung den neuen Stand geholt hat.
          const rebootSince = st?.reboot_pending_since;
          const needsReboot = readable && st.reboot_required && !rebootSince;
          const isOpen = open === h.host_id;
          const notes = updateNotes(st, h.full_upgrade);
          const openNote = notes.find((n) => `${h.host_id}:${n.key}` === noteOpen);
          return (
            <div key={h.host_id} className="panel px-4 py-3" data-testid={`updates-${h.host_id}`}>
              <div className="flex flex-wrap items-center gap-3">
                <div className="min-w-0 flex-[1_1_14rem]">
                  <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                    {h.host_name}
                    <Badge tone={state.tone}>{state.label}</Badge>
                    {!h.busy && !h.checking && notes.map((n) => {
                      const id = `${h.host_id}:${n.key}`;
                      return (
                        <button key={n.key} type="button" title={n.hint} aria-expanded={noteOpen === id}
                          onClick={() => setNoteOpen(noteOpen === id ? null : id)}
                          className="text-xs font-normal text-white/55 underline decoration-dotted underline-offset-2 hover:text-white">
                          {n.label}
                        </button>
                      );
                    })}
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
                  {st?.error && !st.unsupported && st.checked_at && (
                    <p className="mt-1 text-xs text-amber-300">Der angezeigte Stand stammt aus der letzten erfolgreichen Prüfung und kann veraltet sein.</p>
                  )}
                  {st?.refresh_error && <p className="mt-1 text-xs text-amber-300/80">{st.refresh_error}</p>}
                  {openNote && <p className="mt-1 text-xs text-white/50">{openNote.hint}</p>}
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
                    {readable && securityRunnable(st) > 0 && <Button small variant="primary" disabled={!!running} onClick={() => void apply(h, "security")}>Sicherheitsupdates einspielen</Button>}
                    {readable && installable(st, h.full_upgrade) > 0 && <Button small variant={installableSecurity(st) ? "secondary" : "primary"} disabled={!!running} onClick={() => void apply(h, "all")}>Alle einspielen</Button>}
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
                <div className="mt-2 max-h-80 overflow-y-auto rounded-lg border border-white/[0.06] p-2 sm:p-0">
                  <table role="table" className={`${CARD_TABLE} text-left text-left text-xs`}>
                    <thead role="rowgroup" className={`${CARD_HEAD} sm:sticky sm:top-0 bg-[var(--color-surface-head)] text-white/45`}>
                      <tr><th className="px-3 py-1.5 font-medium">Paket</th><th className="px-3 py-1.5 font-medium">installiert</th><th className="px-3 py-1.5 font-medium">neu</th><th className="px-3 py-1.5" /></tr>
                    </thead>
                    <tbody role="rowgroup" className={CARD_BODY}>
                      {st.packages.map((p) => (
                        <tr role="row" className={CARD_ROW} key={p.name}>
                          <td role="cell" className={`${CARD_CELL_PLAIN} min-w-0 [overflow-wrap:anywhere] sm:px-3 sm:py-1.5 font-mono`}>{p.name}</td>
                          <td data-label="installiert" role="cell" className={`${CARD_CELL} min-w-0 [overflow-wrap:anywhere] sm:px-3 sm:py-1.5 font-mono text-white/50`}>{p.current_version ?? "–"}</td>
                          <td data-label="neu" role="cell" className={`${CARD_CELL} min-w-0 [overflow-wrap:anywhere] sm:px-3 sm:py-1.5 font-mono`}>{p.new_version}</td>
                          <td role="cell" className={`${CARD_CELL_PLAIN} min-w-0 [overflow-wrap:anywhere] sm:px-3 sm:py-1.5 sm:text-right`}>
                            {p.security && <Badge tone="bad">Sicherheit</Badge>}
                            {p.phased && <span title={PHASING_HINT}><Badge>verzögert</Badge></span>}
                            {p.kept_back && !h.full_upgrade && <span title={KEPT_BACK_HINT}><Badge>zurückgehalten</Badge></span>}
                            {p.held && <span title={HELD_HINT}><Badge>gesperrt</Badge></span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* „Aufräumen“ für jeden apt-Server; der Tipp davor nur, wenn er noch etwas bringt. */}
              {canManage && readable && st.manager === "apt" && (
                <p className="mt-2 flex flex-wrap items-center gap-2 text-xs text-white/50">
                  {/* Spielt Nodvard Deck selbst ein (für alle Server, ohne Markierung), braucht es den Tipp nicht. Mit
                      Markierung ist hier nicht bekannt, ob dieser Server dazugehört: dann bleibt der Tipp. */}
                  {st.unattended === false && !(c.auto_enabled && !c.auto_tag) && (
                    <>
                      Tipp: Der Server kann Sicherheitsupdates auch jeden Tag selbst einspielen.
                      <button type="button" disabled={acting.has(unattendedKey(h.host_id))} onClick={() => void installUnattended(h)} className="text-[var(--color-accent)] hover:underline disabled:opacity-50 disabled:no-underline">
                        {acting.has(unattendedKey(h.host_id)) ? "Richte ein …" : "Einrichten"}
                      </button>
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
                  <span className="min-w-0 flex-1 break-words text-xs text-white/55">{r.summary}</span>
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
