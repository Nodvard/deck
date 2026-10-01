/**
 * Protokoll: alle sicherheitsrelevanten Vorgaenge (Anmeldungen, Freigaben,
 * ausgefuehrte Befehle, Einstellungs-Aenderungen) mit Filter und Export.
 */
import { ChevronDown, ChevronRight, Download, Search } from "lucide-react";
import { Fragment, useEffect, useMemo, useState } from "react";

import { api } from "../../lib/api";
import { useAuthStore } from "../../state/auth";
import { Badge, Button, NoticeLine, PageHeader, Toggle, errorText, inputClass, type Notice } from "./ui";

interface AuditEntry {
  id: string;
  ts: string;
  actor_type: string;
  actor_id: string;
  action: string;
  target_type: string | null;
  target_id: string | null;
  outcome: string;
  reason: string | null;
  detail: Record<string, unknown>;
  ip: string | null;
  user_agent: string | null;
}

const PAGE = 100;

/** Laufen im Hintergrund dauernd mit (z. B. jeder Proxmox-Abruf) und verdecken sonst alles andere. */
const AUTOMATIC_ACTIONS = ["secret.used"];

const ACTION_LABELS: Record<string, string> = {
  "login.succeeded": "Anmeldung",
  "login.failed": "Anmeldung fehlgeschlagen",
  "logout.succeeded": "Abmeldung",
  "login.locked": "Anmeldung vorübergehend gesperrt",
  "mfa.failed": "Zwei-Faktor-Code falsch",
  "mfa.recovery_used": "Wiederherstellungs-Code benutzt",
  "auth.password_change": "Passwort geändert",
  "auth.recovery_codes_generated": "Wiederherstellungs-Codes erzeugt",
  "auth.2fa_disabled": "Zwei-Faktor abgeschaltet",
  "auth.password_check_failed": "Passwortabfrage fehlgeschlagen",
  "auth.password_check_locked": "Sicherheitsabfragen vorübergehend gesperrt",
  "auth.totp_confirm_failed": "Zwei-Faktor-Einrichtung: falscher Code",
  "auth.password_reset_cli": "Passwort per Notfall-Befehl zurückgesetzt",
  "auth.2fa_disabled_cli": "Zwei-Faktor per Notfall-Befehl abgeschaltet",
  "user.2fa_reset": "Zwei-Faktor zurückgesetzt",
  "setup.completed": "Einrichtung abgeschlossen",
  "setup.failed": "Einrichtungscode falsch",
  "action.approved": "Aktion freigegeben",
  "action.denied": "Aktion abgelehnt",
  "action.dismissed": "Aktion verworfen",
  "action.executed": "Aktion ausgeführt",
  "action.expired": "Aktion abgelaufen",
  "exec.denied": "Befehl gesperrt",
  "console.open": "Konsole geöffnet",
  "secret.used": "Zugangsdaten verwendet",
  "system.settings.changed": "Systemeinstellung geändert",
  "system.backup.created": "Sicherung erstellt",
  "system.backup.failed": "Sicherung fehlgeschlagen",
  "system.backup.config_changed": "Sicherungs-Einstellungen geändert",
  "system.backup.key_set": "Sicherungspasswort festgelegt",
  "system.backup.download_prepared": "Sicherung zum Herunterladen erstellt",
  "system.backup.downloaded": "Sicherung heruntergeladen",
  "system.backup.verified": "Sicherung geprüft",
  "system.backup.deleted": "Sicherung gelöscht",
  "extension.settings": "Erweiterung eingerichtet",
  "extension.secret_set": "Zugangsdaten hinterlegt",
  "extension.enabled": "Erweiterung eingeschaltet",
  "extension.disabled": "Erweiterung ausgeschaltet",
  "extension.auto_enabled": "Erweiterung automatisch eingeschaltet",
  "host.created": "Server angelegt",
  "host.updated": "Server geändert",
  "host.deleted": "Server gelöscht",
  "host.credential_added": "SSH-Zugang hinterlegt",
  "host.credential_deleted": "SSH-Zugang gelöscht",
  "host.credential_made_default": "Standard-Zugang umgestellt",
  "host.key_generated": "SSH-Schlüssel erzeugt",
  "host.known_key_pinned": "Server-Schlüssel bestätigt",
  "host.known_key_forgotten": "Server-Schlüssel vergessen",
  "host.connection_checked": "Verbindung geprüft",
  "host.group_changed": "Gruppe geändert",
  "app.created": "App angelegt",
  "app.updated": "App geändert",
  "app.deleted": "App gelöscht",
  "app.reordered": "Apps umsortiert",
};

const OUTCOME_TONE: Record<string, "good" | "bad" | "warn" | "neutral"> = {
  success: "good", succeeded: "good", failure: "bad", failed: "bad", denied: "bad", error: "bad",
};

const OUTCOME_LABELS: Record<string, string> = { success: "Erfolgreich", failure: "Fehlgeschlagen", denied: "Abgelehnt", proposed: "Vorgeschlagen" };

const ACTOR_LABELS: Record<string, string> = { user: "Benutzer", system: "System", extension: "Erweiterung", ai: "KI", token: "API-Token", anonymous: "Nicht angemeldet" };

export function AuditSettings(): JSX.Element {
  const hasPermission = useAuthStore((s) => s.hasPermission);
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [done, setDone] = useState(false);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<Notice>(null);
  const [outcome, setOutcome] = useState("");
  const [actorType, setActorType] = useState("");
  const [since, setSince] = useState("");
  const [search, setSearch] = useState("");
  const [hideAutomatic, setHideAutomatic] = useState(true);
  const [open, setOpen] = useState<string | null>(null);
  const [userNames, setUserNames] = useState<Record<string, string>>({});

  function query(offset: number): string {
    const params = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
    if (outcome) params.set("outcome", outcome);
    if (actorType) params.set("actor_type", actorType);
    if (since) params.set("since", new Date(since).toISOString());
    if (hideAutomatic) for (const a of AUTOMATIC_ACTIONS) params.append("exclude_action", a);
    return params.toString();
  }

  async function load(offset: number) {
    setLoading(true);
    setNotice(null);
    try {
      const page = await api.get<AuditEntry[]>(`/audit?${query(offset)}`);
      setEntries((prev) => (offset === 0 ? page : [...prev, ...page]));
      setDone(page.length < PAGE);
    } catch (err) {
      setNotice({ kind: "error", text: errorText(err) });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(0); }, [outcome, actorType, since, hideAutomatic]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!hasPermission("users.read")) return;
    api.get<{ id: string; username: string }[]>("/users")
      .then((users) => setUserNames(Object.fromEntries(users.map((u) => [u.id, u.username]))))
      .catch(() => {});
  }, [hasPermission]);

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return entries;
    return entries.filter((e) =>
      [e.action, ACTION_LABELS[e.action], e.actor_id, userNames[e.actor_id], e.target_type, e.target_id, e.reason, e.ip, JSON.stringify(e.detail)]
        .some((v) => v?.toLowerCase().includes(needle)),
    );
  }, [entries, search, userNames]);

  async function exportFile() {
    const params = new URLSearchParams(query(0));
    params.delete("limit");
    params.delete("offset");
    try {
      const token = useAuthStore.getState().accessToken;
      const res = await fetch(`/api/v1/audit/export?${params}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `protokoll-${new Date().toISOString().slice(0, 10)}.ndjson`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setNotice({ kind: "error", text: `Export fehlgeschlagen: ${errorText(err)}` });
    }
  }

  function actorName(e: AuditEntry): string {
    if (e.actor_type === "user") return userNames[e.actor_id] ?? e.actor_id;
    return `${ACTOR_LABELS[e.actor_type] ?? e.actor_type}${e.actor_id && e.actor_id !== e.actor_type ? ` · ${e.actor_id}` : ""}`;
  }

  return (
    <div>
      <PageHeader
        title="Protokoll"
        description="Jede Anmeldung, Freigabe und Änderung wird hier festgehalten und kann nicht bearbeitet werden."
        actions={<Button onClick={() => void exportFile()}><Download size={14} /> Exportieren</Button>}
      />
      <NoticeLine notice={notice} />

      <div className="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <span className="relative block">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-white/35" />
          <input aria-label="Suchen" placeholder="Suchen …" value={search} onChange={(e) => setSearch(e.target.value)} className={`${inputClass} pl-9`} />
        </span>
        <select aria-label="Ergebnis" value={outcome} onChange={(e) => setOutcome(e.target.value)} className={inputClass}>
          <option value="">Alle Ergebnisse</option>
          <option value="success">Erfolgreich</option>
          <option value="failure">Fehlgeschlagen</option>
          <option value="denied">Abgelehnt</option>
          <option value="proposed">Vorgeschlagen</option>
        </select>
        <select aria-label="Auslöser" value={actorType} onChange={(e) => setActorType(e.target.value)} className={inputClass}>
          <option value="">Alle Auslöser</option>
          {Object.entries(ACTOR_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <input aria-label="Seit" type="date" value={since} onChange={(e) => setSince(e.target.value)} className={inputClass} />
      </div>
      <div className="mb-4 flex items-center gap-2.5 text-sm text-white/65">
        <Toggle label="Automatische Zugriffe ausblenden" checked={hideAutomatic} onChange={setHideAutomatic} />
        Automatische Zugriffe von Erweiterungen ausblenden
      </div>

      <div className="panel overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-white/[0.06] text-xs uppercase tracking-wider text-white/40">
            <tr>
              <th className="w-6 px-3 py-2.5" />
              <th className="px-3 py-2.5 font-medium">Zeitpunkt</th>
              <th className="px-3 py-2.5 font-medium">Vorgang</th>
              <th className="px-3 py-2.5 font-medium">Wer</th>
              <th className="px-3 py-2.5 font-medium">Ziel</th>
              <th className="px-3 py-2.5 font-medium">Ergebnis</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/[0.04]">
            {visible.map((e) => (
              <Fragment key={e.id}>
                <tr className="cursor-pointer hover:bg-white/[0.03]" onClick={() => setOpen(open === e.id ? null : e.id)}>
                  <td className="px-3 py-2 text-white/40">{open === e.id ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</td>
                  <td className="whitespace-nowrap px-3 py-2 tabular-nums text-white/60">{new Date(e.ts).toLocaleString("de-DE")}</td>
                  <td className="px-3 py-2">
                    <span className="block">{ACTION_LABELS[e.action] ?? e.action}</span>
                    {ACTION_LABELS[e.action] && <span className="block font-mono text-[11px] text-white/35">{e.action}</span>}
                  </td>
                  <td className="px-3 py-2 text-white/70">{actorName(e)}</td>
                  <td className="max-w-[16rem] truncate px-3 py-2 text-white/60">{e.target_type ? `${e.target_type}${e.target_id ? ` · ${e.target_id}` : ""}` : "–"}</td>
                  <td className="px-3 py-2"><Badge tone={OUTCOME_TONE[e.outcome] ?? "neutral"}>{OUTCOME_LABELS[e.outcome] ?? e.outcome}</Badge></td>
                </tr>
                {open === e.id && (
                  <tr className="bg-black/20">
                    <td />
                    <td colSpan={5} className="px-3 py-3 text-xs">
                      <dl className="grid gap-x-6 gap-y-1 sm:grid-cols-[auto_1fr]">
                        {e.reason && (<><dt className="text-white/40">Grund</dt><dd>{e.reason}</dd></>)}
                        {e.ip && (<><dt className="text-white/40">IP-Adresse</dt><dd className="font-mono">{e.ip}</dd></>)}
                        {e.user_agent && (<><dt className="text-white/40">Browser</dt><dd className="truncate">{e.user_agent}</dd></>)}
                      </dl>
                      {Object.keys(e.detail ?? {}).length > 0 && (
                        <pre className="mt-2 max-h-64 overflow-auto rounded-md bg-black/40 p-2 font-mono text-[11px] text-white/70">{JSON.stringify(e.detail, null, 2)}</pre>
                      )}
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
        {!loading && visible.length === 0 && <p className="px-5 py-8 text-center text-sm text-white/45">Keine Einträge gefunden.</p>}
      </div>
      <div className="mt-3 flex items-center justify-between text-xs text-white/45">
        <span>{visible.length} Einträge angezeigt</span>
        {!done && <Button busy={loading} onClick={() => void load(entries.length)}>Ältere laden</Button>}
      </div>
    </div>
  );
}
