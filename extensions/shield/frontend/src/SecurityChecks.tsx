import { useCallback, useEffect, useState } from "react";

import { Badge, Button, Card, Icon, Notice, buttonClass, type Tone } from "../../../_shared/frontend/src/ui";
import { useUnmountSignal } from "../../../_shared/frontend/src/lifecycle";
import { API, call, useBusyKeys, useForHost, when } from "./api";
import { EXT_ID } from "./ids";

interface CveFinding {
  package: string;
  installed_version: string;
  cve: string;
  severity: string;
  fixed_version: string | null;
}

interface CveCheck {
  status: string;
  checked_at: number;
  findings: CveFinding[];
  counts: { high: number; critical: number };
  error: string | null;
}

interface Certificate {
  hostname: string;
  port: number;
  status: string;
  issuer: string | null;
  expires_at: number | null;
  days_remaining: number | null;
  hostname_matches: boolean | null;
  self_signed: boolean | null;
  trusted: boolean | null;
  protocol: string | null;
  weak_protocols: string[];
  protocol_check_available: boolean;
  warnings: string[];
  error: string | null;
}

interface ExternalCheck {
  status: string;
  checked_at: number;
  open_ports: number[];
  new_ports: number[];
  certificates: Certificate[];
  warnings: string[];
  error: string | null;
}

interface HostChecks {
  host_id: string;
  host_name: string;
  external_enabled: boolean;
  running?: boolean;
  cve: CveCheck | null;
  external: ExternalCheck | null;
}

const SEVERITIES: Record<string, { label: string; tone: Tone }> = {
  critical: { label: "Kritisch", tone: "bad" },
  high: { label: "Hoch", tone: "bad" },
  medium: { label: "Mittel", tone: "warn" },
  low: { label: "Niedrig", tone: "neutral" },
};

function CheckedAt({ value, previous = false }: { value: number; previous?: boolean }) {
  return <p className="mt-3 text-xs text-white/45">{previous ? "Letztes Ergebnis" : "Geprüft"}: {when(value)}</p>;
}

function CveResults({ check }: { check: CveCheck | null }) {
  if (!check) return <p className="text-sm text-white/55">Noch keine CVE-Prüfung. Bekannte Sicherheitslücken wurden noch nicht abgeglichen.</p>;
  const complete = check.status === "ok";
  return (
    <>
      {!complete && <p className="text-sm text-amber-200">{check.status === "unsupported" ? "CVE-Prüfung für dieses System nicht verfügbar." : "CVE-Prüfung nicht abgeschlossen."} {check.error}</p>}
      {complete && (
        <>
          <div className="mb-3 flex flex-wrap gap-2">
            <Badge tone={check.counts.critical ? "bad" : "neutral"}>{check.counts.critical} kritisch</Badge>
            <Badge tone={check.counts.high ? "bad" : "neutral"}>{check.counts.high} hoch</Badge>
          </div>
          {check.findings.length === 0 ? <p className="text-sm text-white/55">Keine bekannten Sicherheitslücken im geprüften Paketbestand gefunden.</p> : (
            <ul className="divide-y divide-white/[0.06]">
              {check.findings.map((finding) => {
                const severity = SEVERITIES[finding.severity.toLowerCase()] ?? { label: "Unbekannt", tone: "neutral" as Tone };
                return (
                  <li key={`${finding.package}:${finding.installed_version}:${finding.cve}`} className="py-2 text-sm">
                    <div className="flex flex-wrap items-center gap-2"><strong className="break-all font-medium">{finding.package}</strong><Badge tone={severity.tone}>{severity.label}</Badge></div>
                    <p className="mt-1 break-all">{finding.cve}</p>
                    <p className="mt-1 break-all text-xs text-white/55">Installiert: {finding.installed_version}</p>
                    <p className="break-all text-xs text-white/55">{finding.fixed_version ? `Behoben ab: ${finding.fixed_version}` : "Noch keine behobene Version bekannt."}</p>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
      <CheckedAt value={check.checked_at} />
    </>
  );
}

function CertificateResult({ certificate: cert }: { certificate: Certificate }) {
  return (
    <li className="py-3 text-xs">
      <p className="break-all text-sm font-medium">HTTPS: {cert.hostname}:{cert.port}</p>
      {cert.error && <p className="mt-1 break-words text-amber-200">Zertifikat nicht vollständig geprüft: {cert.error}</p>}
      <dl className="mt-2 space-y-1 text-white/65">
        <div><dt className="inline">Aussteller: </dt><dd className="inline break-all">{cert.issuer ?? "unbekannt"}</dd></div>
        <div><dt className="inline">Ablauf: </dt><dd className="inline">{cert.expires_at == null ? "unbekannt" : new Date(cert.expires_at * 1000).toLocaleDateString("de-DE")}{cert.days_remaining == null ? "" : cert.days_remaining < 0 ? " (abgelaufen)" : ` (in ${cert.days_remaining} Tagen)`}</dd></div>
        <div><dt className="inline">Hostname: </dt><dd className="inline">{cert.hostname_matches == null ? "nicht geprüft" : cert.hostname_matches ? "passt zum Zertifikat" : "passt nicht zum Zertifikat"}</dd></div>
        <div><dt className="inline">Vertrauen: </dt><dd className="inline">{cert.trusted == null ? "nicht geprüft" : cert.trusted ? "Zertifikat ist vertrauenswürdig" : "Zertifikat ist nicht vertrauenswürdig"}</dd></div>
        {cert.protocol && <div><dt className="inline">Verbindung: </dt><dd className="inline">{cert.protocol}</dd></div>}
      </dl>
      {cert.self_signed && <p className="mt-2 text-amber-200">Selbstsigniertes Zertifikat: Der Server bestätigt seine Identität selbst. Browser vertrauen ihm erst, wenn das Zertifikat ausdrücklich als vertrauenswürdig eingerichtet ist.</p>}
      {cert.weak_protocols.length > 0 && <p className="mt-2 text-amber-200">Schwache TLS-Protokolle: {cert.weak_protocols.join(", ")}</p>}
      {!cert.protocol_check_available && <p className="mt-2 text-amber-200">Schwache TLS-Protokolle konnten nicht vollständig geprüft werden.</p>}
      {cert.warnings.map((warning, index) => <p key={index} className="mt-2 break-words text-amber-200">{warning}</p>)}
    </li>
  );
}

function ExternalResults({ check, enabled }: { check: ExternalCheck | null; enabled: boolean }) {
  if (!check) return <p className="text-sm text-white/55">{enabled ? "Noch keine externe Prüfung." : "Externe Prüfung ist für diesen Server ausgeschaltet."}</p>;
  const complete = check.status === "ok" || check.status === "warning";
  return (
    <>
      {!enabled && <p className="mb-3 text-sm text-amber-200">Externe Prüfung ausgeschaltet. Das letzte Ergebnis bleibt zur Orientierung sichtbar.</p>}
      {!complete && <p className="mb-3 text-sm text-amber-200">Externe Prüfung nicht abgeschlossen. {check.error}</p>}
      {(complete || check.open_ports.length > 0) && <p className="text-sm"><span className="text-white/55">Offene TCP-Ports: </span>{check.open_ports.length ? check.open_ports.join(", ") : "Keine der geprüften Verbindungen war erreichbar."}</p>}
      {check.new_ports.length > 0 && <p className="mt-2 text-sm text-amber-200">Neu erreichbar seit der letzten Prüfung: {check.new_ports.join(", ")}</p>}
      {check.warnings.map((warning, index) => <p key={index} className="mt-2 break-words text-sm text-amber-200">{warning}</p>)}
      {check.certificates.length > 0 && <ul className="mt-3 divide-y divide-white/[0.06]">{check.certificates.map((cert) => <CertificateResult key={`${cert.hostname}:${cert.port}`} certificate={cert} />)}</ul>}
      <CheckedAt value={check.checked_at} previous={!enabled} />
    </>
  );
}

export function SecurityChecks({ canManage }: { canManage: boolean }) {
  const [rows, setRows] = useState<HostChecks[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionErrors, setActionErrors] = useState<Record<string, string | undefined>>({});
  const { busy, begin, end } = useBusyKeys();
  const signal = useUnmountSignal();
  const hosts = useForHost(rows ?? []);
  const load = useCallback(async () => {
    try {
      const result = await call<{ hosts: HostChecks[] }>(`${API}/checks`, { signal: signal() });
      setRows(result.hosts);
      setLoadError(null);
    } catch (error) {
      if (!signal().aborted) setLoadError(error instanceof Error ? error.message : String(error));
    }
  }, [signal]);
  useEffect(() => { void load(); }, [load]);
  const running = rows?.some((host) => host.running) ?? false;
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void load(), 5000);
    return () => clearInterval(timer);
  }, [running, load]);

  async function change(host: HostChecks, externalEnabled?: boolean) {
    if (!canManage || host.running || !begin(host.host_id)) return;
    setActionErrors((previous) => ({ ...previous, [host.host_id]: undefined }));
    try {
      const updated = await call<HostChecks>(`${API}/checks/${encodeURIComponent(host.host_id)}${externalEnabled == null ? "/run" : ""}`, {
        method: externalEnabled == null ? "POST" : "PATCH",
        ...(externalEnabled == null ? {} : { body: JSON.stringify({ external_enabled: externalEnabled }) }),
        signal: signal(),
      });
      setRows((previous) => previous?.map((row) => row.host_id === updated.host_id ? updated : row) ?? [updated]);
    } catch (error) {
      if (!signal().aborted) setActionErrors((previous) => ({ ...previous, [host.host_id]: error instanceof Error ? error.message : String(error) }));
    } finally {
      end(host.host_id);
    }
  }

  return (
    <section className="mt-5 space-y-4" aria-label="Sicherheitsprüfungen">
      <div><h3 className="text-sm font-semibold">Sicherheitslücken und externe Erreichbarkeit</h3><p className="mt-1 text-xs text-white/50">CVE-Prüfung und Verbindungen vom Deck zum Server sind rein lesend. Es werden keine Pakete installiert oder Einstellungen auf dem Server geändert.</p></div>
      {loadError && <div role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm"><p>Sicherheitsprüfungen ließen sich nicht laden: {loadError}</p><p className="mt-1 text-xs text-white/55">Der Schutzstatus ist deshalb unbekannt.</p><div className="mt-2"><Button small onClick={() => void load()}>Prüfungen erneut laden</Button></div></div>}
      {!rows && !loadError && <p className="text-sm text-white/50">Lade Sicherheitsprüfungen …</p>}
      {hosts.map((host) => (
        <section key={host.host_id} data-testid={`checks-${host.host_id}`} className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="text-sm font-medium">{host.host_name}</h4>{canManage ? <Button small disabled={busy.has(host.host_id) || host.running} onClick={() => void change(host)}><Icon name="refresh" size={12} />{host.running ? "Prüfung läuft …" : busy.has(host.host_id) ? "Bitte warten …" : "Jetzt prüfen"}</Button> : host.running && <Badge tone="info">Prüfung läuft …</Badge>}</div>
          {actionErrors[host.host_id] && <Notice text={`Sicherheitsprüfung: ${actionErrors[host.host_id]}`} />}
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <Card title="Bekannte Sicherheitslücken (CVE)" description="Abgleich der installierten Pakete mit bekannten Schwachstellen." actions={<a className={buttonClass("secondary", true)} href={`/ext/${EXT_ID}/soc?tab=updates&host=${encodeURIComponent(host.host_id)}`}>Updates ansehen</a>}>
              <CveResults check={host.cve} />
            </Card>
            <Card title="Externe Prüfung" description="Erreichbare TCP-Ports und HTTPS-Zertifikate, geprüft aus dem Heimnetz (von Deck aus), nicht aus dem Internet." actions={canManage ? <label className="flex items-center gap-2 text-xs"><input type="checkbox" role="switch" aria-label={`Externe Prüfung für ${host.host_name}`} checked={host.external_enabled} disabled={busy.has(host.host_id) || host.running} onChange={(event) => void change(host, event.target.checked)} />Aktiv</label> : <Badge>{host.external_enabled ? "Aktiv" : "Ausgeschaltet"}</Badge>}>
              <ExternalResults check={host.external} enabled={host.external_enabled} />
            </Card>
          </div>
        </section>
      ))}
    </section>
  );
}
