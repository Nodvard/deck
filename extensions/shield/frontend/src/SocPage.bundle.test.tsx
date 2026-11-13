import { cardTable } from "../../../../frontend/src/test/mobileLayout";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

beforeEach(() => {
  window.history.replaceState(null, "", "/ext/shield/soc");
  window.__nodvardDeck = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog: vi.fn(), promptDialog: vi.fn(), hasPermission: () => true,
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = input.toString();
    const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
    if (url.endsWith("/defender/checks")) return json({ hosts: [{
      host_id: "h1", host_name: "Server", external_enabled: true, running: false,
      cve: { status: "ok", checked_at: 1, counts: { high: 1, critical: 0 }, error: null,
        findings: [{ package: "openssl", installed_version: "3.0.1", cve: "CVE-2026-1234", severity: "high", fixed_version: "3.0.2" }] },
      external: null,
    }] });
    if (url.endsWith("/defender/overview")) return json({
      hosts: [{
        host_id: "h1", host_name: "Server", host_status: "up", reachable: true, rpm_ostree: true,
        clamav_installed: false, lynis_installed: false, last_scan: null, last_audit: null,
        scanning: false, auditing: false,
      }],
      summary: { hosts: 1, protected: 0, open_threats: 0, quarantined: 0, neutralized_total: 0, findings_30d: 0, avg_hardening: 0, score: 0 },
      config: {},
    });
    if (url.endsWith("/defender/guard")) return json({
      hosts: [{
        host_id: "h1", host_name: "Server", host_status: "up", checking: false, open_events: 0,
        view: {
          is_root: true, ssh_log_found: true, failed_24h: 0, attacker_count: 0, banned_count: 0,
          fail2ban: "running", jails: { sshd: { banned: [] } }, attackers: [], logins: [], ports: [],
          files_watched: 0, files_pending: [], checked_at: 1, ssh_findings: [],
        },
      }],
      summary: { hosts: 1, failed_24h: 0, attackers: 0, banned: 0, open_events: 0, fail2ban_running: 1, new_ports: 0, files_pending: 0 },
      config: { enabled: true, file_watch: true },
    });
    if (url.endsWith("/defender/updates")) return json({
      hosts: [{
        host_id: "h1", host_name: "Server", host_status: "up", checking: false, busy: null, last_run: null,
        status: {
          manager: "apt", count: 1, security_count: 1, reboot_required: false, reboot_reasons: [],
          kernel: null, latest_kernel: null, uptime_s: 3600, unattended: true,
          refresh_error: null, error: null, checked_at: 1,
          packages: [{ name: "openssl", current_version: "3.0.13", new_version: "3.0.14", security: true }],
        },
      }],
      runs: [], summary: { hosts: 1, checked: 1, up_to_date: 0, packages: 1, security: 1, reboot: 0, errors: 0 },
      config: { check_enabled: false, auto_enabled: false },
    });
    if (url.endsWith("/hosts/h1/credentials")) return json([
      { id: "key-1", kind: "ssh_key", username: "deck", port: 22, is_default: true },
    ]);
    if (url.includes("/hosts/h1/ssh-hardening?")) return json({
      has_key: true, authorized: true, hardened: false, managed: false, root: false, warning: null,
    });
    if (url.endsWith("/hosts/h1/fail2ban")) return json({
      bantime: 600, findtime: 900, maxretry: 5, ignoreip: ["192.0.2.1"], deck_address: "192.0.2.1",
      jails: ["sshd"], available_jails: ["sshd"],
    });
    if (/\/defender\/(findings|scans|events)(\?|$)/.test(url)) return json([]);
    throw new Error(`Unerwarteter Fetch: ${url}`);
  }));
});

async function renderBundle() {
  const bundlePath = "../dist/index.js";
  const { SocPage }: typeof import("./SocPage") = await import(bundlePath);
  render(<SocPage />);
}

it("zeigt im Bundle die manuelle Installation auf rpm-ostree ohne Installationsknöpfe", async () => {
  await renderBundle();
  const host = within(await screen.findByTestId("host-h1"));
  expect(host.getByText("unveränderliches System (rpm-ostree): Installation von Hand, dann Neustart")).toBeInTheDocument();
  expect(host.getByText("rpm-ostree install clamav clamav-update lynis fail2ban")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "ClamAV installieren" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Lynis installieren" })).toBeNull();
});

it("zeigt die CVE-Karte im Bundle und verlinkt die Updates des Servers", async () => {
  await renderBundle();
  const checks = within(await screen.findByTestId("checks-h1"));
  expect(checks.getByText("CVE-2026-1234")).toBeInTheDocument();
  expect(checks.getByText("1 hoch")).toBeInTheDocument();
  expect(checks.getByRole("link", { name: "Updates ansehen" })).toHaveAttribute("href", "/ext/shield/soc?tab=updates&host=h1");
});

it("zeigt nach der CVE-Karte die Paketliste mit beschrifteten Versionen", async () => {
  await renderBundle();
  const checks = within(await screen.findByTestId("checks-h1"));
  expect(checks.getByText("CVE-2026-1234")).toBeInTheDocument();
  expect(checks.getByRole("link", { name: "Updates ansehen" })).toHaveAttribute("href", "/ext/shield/soc?tab=updates&host=h1");
  fireEvent.click(screen.getByRole("tab", { name: /Updates/ }));
  const host = within(await screen.findByTestId("updates-h1"));
  fireEvent.click(host.getByRole("button", { name: "1 Paket anzeigen" }));
  const row = within(host.getByText("openssl").closest("tr")!);
  expect(row.getByText("3.0.13")).toHaveAttribute("data-label", "installiert");
  expect(row.getByText("3.0.14")).toHaveAttribute("data-label", "neu");
  expect(row.getByText("Sicherheit")).toBeInTheDocument();
});

it("zeigt im Bundle SSH-Härtung und Fail2ban-Einstellungen des Servers", async () => {
  await renderBundle();
  const label = await screen.findByText("Handlungsbedarf");
  expect(label).not.toHaveAttribute("style");
  expect(label).toHaveAttribute("data-deck-style");
  fireEvent.click(screen.getByRole("tab", { name: /Einbruchschutz/ }));
  const host = within(await screen.findByTestId("guard-h1"));
  await waitFor(() => expect(host.getByRole("button", { name: "SSH härten" })).toBeEnabled());
  fireEvent.click(host.getByRole("button", { name: "Fail2ban-Einstellungen" }));
  expect(await host.findByLabelText("Sperrdauer (Sekunden)")).toHaveValue(600);
  expect(host.getByRole("checkbox", { name: "sshd (Pflicht)" })).toBeChecked();
  expect(host.getByRole("button", { name: "Einstellungen übernehmen" })).toBeInTheDocument();
  expect(host.getByRole("button", { name: "SSH härten" })).toBeEnabled();
});

it("zeigt im Bundle nach einer fehlgeschlagenen Update-Prüfung einen unbekannten Stand", async () => {
  const base = fetch;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (!input.toString().endsWith("/defender/updates")) return base(input, init);
    return new Response(JSON.stringify({
      hosts: [{
        host_id: "h1", host_name: "Server", host_status: "up", checking: false, busy: null, last_run: null,
        status: {
          manager: "apt", count: 0, security_count: 0, packages: [], reboot_required: false, reboot_reasons: [],
          error: "Die Paketverwaltung ist gesperrt. Später erneut prüfen.", checked_at: null,
        },
      }],
      runs: [], config: {},
      summary: { hosts: 1, checked: 0, up_to_date: 0, packages: 0, security: 0, reboot: 0, errors: 1 },
    }), { status: 200 });
  }));
  await renderBundle();
  const side = within(await screen.findByTestId("side-summary"));
  expect(await side.findByText(/Update-Prüfung fehlgeschlagen/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: /Updates/ }));
  const host = within(await screen.findByTestId("updates-h1"));
  expect(host.getByText("Prüfung fehlgeschlagen")).toBeInTheDocument();
  expect(host.getByText("Die Paketverwaltung ist gesperrt. Später erneut prüfen.")).toBeInTheDocument();
  expect(screen.getAllByText("unbekannt")).toHaveLength(3);
  expect(host.queryByRole("button", { name: "Alle einspielen" })).not.toBeInTheDocument();
});

it("nennt im Bundle normale Updates neben fehlgeschlagenen Prüfungen", async () => {
  const base = fetch;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (!input.toString().endsWith("/defender/updates")) return base(input, init);
    return new Response(JSON.stringify({
      hosts: [], runs: [], summary: { hosts: 2, checked: 1, up_to_date: 0, packages: 3, security: 0, reboot: 0, errors: 1 },
    }), { status: 200 });
  }));
  await renderBundle();
  const side = within(await screen.findByTestId("side-summary"));
  expect(await side.findByText("3 Updates offen. 1 Update-Prüfung fehlgeschlagen. Unter „Updates“ den Grund prüfen und erneut versuchen.")).toBeInTheDocument();
});

it("zeigt im Bundle Paketnamen und Versionen als Karten", async () => {
  await renderBundle();
  fireEvent.click(await screen.findByRole("tab", { name: /Updates/ }));
  const host = within(await screen.findByTestId("updates-h1"));
  fireEvent.click(host.getByRole("button", { name: "1 Paket anzeigen" }));
  const table = host.getByRole("table");
  await cardTable(table, ["installiert", "neu"]);
  const row = within(within(table).getByText("openssl").closest("tr")!);
  expect(row.getByText("3.0.13")).toHaveAttribute("data-label", "installiert");
  expect(row.getByText("3.0.14")).toHaveAttribute("data-label", "neu");
  expect(row.getByText("Sicherheit")).toBeInTheDocument();
}, 30000);

it("öffnet im Bundle den Wochenbericht ohne aktuelle Serverdaten abzurufen", async () => {
  window.history.replaceState(null, "", "/ext/shield/soc?tab=reports");
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    expect(input.toString()).toMatch(/\/reports$/);
    return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 });
  });
  vi.stubGlobal("fetch", fetch);
  await renderBundle();
  expect(screen.getByRole("tab", { name: "Wochenbericht" })).toHaveAttribute("aria-selected", "true");
  expect(await screen.findByText("Noch kein Wochenbericht")).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(1);
});
