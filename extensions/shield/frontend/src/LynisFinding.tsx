/**
 * Ein Fund des Härtungs-Audits (Lynis). Kennt das Backend die Kennung (lynis_text.py), steht oben die deutsche
 * Überschrift und auf Klick „Was heißt das?“ und „Was kannst du tun?“; der englische Text von Lynis bleibt klein
 * darunter. Unbekannte Kennungen bleiben englisch.
 */
import { useState } from "react";

export interface LynisHelp {
  title: string;
  what: string;
  todo: string;
  /** 3 = wichtig, 2 = sinnvoll, 1 = für ein Heimnetz meist nicht nötig (ältere Antworten ohne Wert: 1). */
  weight?: number;
}

const ID_RE = /^\s*([A-Z]{2,5}-\d{4}|LYNIS)\b/;

/** Die Kennung eines Fundes („AUTH-9286: Configure …“) oder null. */
export function lynisId(entry: string): string | null {
  return ID_RE.exec(entry)?.[1] ?? null;
}

export function LynisFinding({
  entry, help, tone,
}: {
  entry: string;
  help?: Record<string, LynisHelp>;
  tone: "warning" | "suggestion";
}) {
  const [open, setOpen] = useState(false);
  const text = help?.[lynisId(entry) ?? ""];
  const color = tone === "warning" ? "text-red-200" : "text-white/70";
  if (!text) return <li className={color}>• {entry}</li>;
  return (
    <li className={color}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="text-left hover:text-white"
      >
        • {text.title}
      </button>
      <span className="ml-3 block text-[11px] text-white/40">{entry}</span>
      {open && (
        <div className="ml-3 mt-1 space-y-1 text-xs text-white/70">
          <p><span className="font-medium text-white/85">Was heißt das?</span> {text.what}</p>
          <p><span className="font-medium text-white/85">Was kannst du tun?</span> {text.todo}</p>
        </div>
      )}
    </li>
  );
}

export interface TopIssue {
  id: string;
  help: LynisHelp;
  warning: boolean;
  /** Anzeigenamen der Server (gezählt wird über die Kennung, zwei Server mit gleichem Namen zählen doppelt). */
  hosts: string[];
  hostIds: string[];
}

interface AuditLike {
  host_id?: string;
  host_name: string;
  status: string;
  warnings: string[];
  suggestions: string[];
  help?: Record<string, LynisHelp>;
}

/**
 * Die wichtigsten Baustellen über alle Server: nur bekannte Hinweise mit Gewicht 2 oder 3, je Kennung einmal mit den
 * Servern, auf denen er vorkommt. Reihenfolge: Gewicht, dann Warnung vor Vorschlag, dann Anzahl der Server.
 */
export function topIssues(audits: AuditLike[], limit = 5): TopIssue[] {
  const byId = new Map<string, TopIssue>();
  for (const audit of audits) {
    if (audit.status !== "ok") continue;
    const entries: [string, boolean][] = [
      ...audit.warnings.map((w): [string, boolean] => [w, true]),
      ...audit.suggestions.map((s): [string, boolean] => [s, false]),
    ];
    for (const [entry, warning] of entries) {
      const id = lynisId(entry);
      const help = id ? audit.help?.[id] : undefined;
      if (!id || !help || (help.weight ?? 1) < 2) continue;
      const issue = byId.get(id) ?? { id, help, warning: false, hosts: [], hostIds: [] };
      issue.warning = issue.warning || warning;
      const hostKey = audit.host_id ?? audit.host_name;
      if (!issue.hostIds.includes(hostKey)) {
        issue.hostIds.push(hostKey);
        issue.hosts.push(audit.host_name);
      }
      byId.set(id, issue);
    }
  }
  return [...byId.values()]
    .sort((a, b) =>
      (b.help.weight ?? 1) - (a.help.weight ?? 1)
      || Number(b.warning) - Number(a.warning)
      || b.hostIds.length - a.hostIds.length
      || a.id.localeCompare(b.id))
    .slice(0, limit);
}

export function TopIssues({ issues }: { issues: TopIssue[] }) {
  const [open, setOpen] = useState<string | null>(null);
  if (issues.length === 0) return null;
  return (
    <ol className="space-y-2 text-sm" data-testid="top-issues">
      {issues.map((issue, i) => (
        <li key={issue.id}>
          <button
            type="button"
            onClick={() => setOpen(open === issue.id ? null : issue.id)}
            aria-expanded={open === issue.id}
            className="text-left hover:text-white"
          >
            <span className="mr-1.5 text-white/40">{i + 1}.</span>
            <span className={issue.warning ? "text-red-200" : ""}>{issue.help.title}</span>
          </button>
          <span className="ml-5 block text-xs text-white/45">
            {issue.help.weight === 3 ? "wichtig" : "sinnvoll"} · auf {issue.hosts.join(", ")}
          </span>
          {open === issue.id && (
            <div className="ml-5 mt-1 space-y-1 text-xs text-white/70">
              <p><span className="font-medium text-white/85">Was heißt das?</span> {issue.help.what}</p>
              <p><span className="font-medium text-white/85">Was kannst du tun?</span> {issue.help.todo}</p>
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}
