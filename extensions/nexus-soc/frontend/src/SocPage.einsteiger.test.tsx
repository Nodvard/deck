/**
 * Nodvard Shield für Einsteiger: "Briefing senden" sperrt sich und meldet ehrlich, ob etwas aufs
 * Handy ging; Abzeichen sind nur grün, wenn der Schutz wirkt; ohne Server gibt es keinen Schutzwert 0/100;
 * der nächste Schritt und die Begriffe stehen in einfachem Deutsch da.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { SocPage } from "./SocPage";

type Json = Record<string, unknown>;

const host = (id: string, name: string, extra: Json = {}): Json => ({
  host_id: id, host_name: name, host_status: "up", reachable: true, clamav_installed: true, clamav_version: "1.0.7",
  signature_version: "27410", signature_date: "Wed Sep 24 2026", freshclam_active: true, lynis_installed: true, quarantine_files: 0,
  last_scan: { id: `s-${id}`, host_id: id, host_name: name, kind: "quick", kind_label: "Schnellscan", paths: ["/tmp"], trigger: "schedule",
    status: "clean", files_scanned: 10, infected: 0, error: null, output_tail: null, started_at: Date.now() / 1000 - 600, finished_at: null },
  last_audit: { status: "ok", hardening_index: 70, warnings: 0, created_at: 1, error: null }, scanning: false, auditing: false, ...extra,
});

const bare = (id: string, name: string, extra: Json = {}): Json =>
  host(id, name, { clamav_installed: false, clamav_version: null, lynis_installed: false, last_scan: null, last_audit: null, ...extra });

const CONFIG = { auto_quarantine: true, realtime_enabled: true, watch_interval_min: 10, quick_scan_cron: "0 2 * * *", deep_scan_cron: "30 3 * * 0", audit_cron: "0 1 * * *" };

function overviewOf(hosts: Json[], summary: Json = {}): Json {
  const protectedCount = hosts.filter((h) => h.clamav_installed).length;
  return {
    hosts, config: CONFIG,
    summary: { hosts: hosts.length, protected: protectedCount, open_threats: 0, quarantined: 0, neutralized_total: 0, findings_30d: 0, avg_hardening: null, score: 22, ...summary },
  };
}

type Call = { url: string; method: string; body: unknown };
interface Setup { overview: Json; briefing?: () => Promise<Response> | Response }

function stubFetch({ overview, briefing }: Setup, calls: Call[] = []) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : null });
    const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
    if (url.includes("/defender/overview")) return json(overview);
    if (url.endsWith("/defender/briefing") && method === "POST") return briefing ? briefing() : json({ title: "Lagebericht – alles im grünen Bereich", push: "sent" });
    if (url.endsWith("/defender/updates")) return json({ hosts: [], runs: [], summary: { hosts: 0, checked: 0, up_to_date: 0, packages: 0, security: 0, reboot: 0 } });
    if (url.endsWith("/defender/guard")) return json({ hosts: [], summary: { failed_24h: 0, banned: 0, open_events: 0, fail2ban_running: 0, hosts: 0 } });
    if (url.includes("/defender/findings") || url.includes("/defender/scans") || url.includes("/defender/events")) return json([]);
    if (url.endsWith("/install")) return json({ action_id: "a1", status: "proposed" });
    if (url.includes("/actions/") && url.endsWith("/approve")) return json({ status: "succeeded", result: {} });
    throw new Error(`Unerwarteter Fetch: ${method} ${url}`);
  }));
}

let confirmDialog: Mock<(message: string) => Promise<boolean>>;
let hasPermission: Mock<(p: string) => boolean>;
beforeEach(() => {
  window.history.replaceState(null, "", "/ext/nexus-soc/soc");
  confirmDialog = vi.fn<(message: string) => Promise<boolean>>().mockResolvedValue(true);
  hasPermission = vi.fn<(p: string) => boolean>().mockReturnValue(true);
  window.__lattice = {
    React: undefined as never, ReactDOM: undefined as never, ReactJsxRuntime: undefined as never,
    getAccessToken: () => "tok", confirmDialog, promptDialog: vi.fn(), hasPermission,
  };
});
afterEach(async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  vi.unstubAllGlobals();
});

const briefingButton = () => screen.getByRole("button", { name: /^Briefing (senden|wird erstellt)/ });

describe("Briefing senden", () => {
  it("sperrt den Knopf, solange es läuft: kein zweites Briefing durch Doppelklick", async () => {
    let finish: (r: Response) => void = () => undefined;
    const calls: Call[] = [];
    stubFetch({ overview: overviewOf([host("h1", "pi")]), briefing: () => new Promise<Response>((resolve) => { finish = resolve; }) }, calls);
    render(<SocPage />);
    await screen.findByTestId("host-h1");

    fireEvent.click(briefingButton());
    fireEvent.click(briefingButton());
    const busy = await screen.findByRole("button", { name: "Briefing wird erstellt …" });
    expect(busy).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent(/Briefing wird erstellt/);
    expect(screen.getByRole("status")).toHaveTextContent(/bis zu einer halben Minute/);
    fireEvent.click(busy);
    expect(calls.filter((c) => c.url.endsWith("/defender/briefing"))).toHaveLength(1);

    await act(async () => { finish(new Response(JSON.stringify({ title: "Lagebericht – alles im grünen Bereich", push: "sent" }), { status: 200 })); });
    await waitFor(() => expect(briefingButton()).toBeEnabled());
    expect(briefingButton()).toHaveTextContent("Briefing senden");
    expect(screen.queryByText(/Briefing wird erstellt …/)).toBeNull();
  });

  it("meldet nur dann, dass es zugestellt wurde, wenn es wirklich zugestellt wurde", async () => {
    stubFetch({ overview: overviewOf([host("h1", "pi")]) });
    render(<SocPage />);
    await screen.findByTestId("host-h1");
    fireEvent.click(briefingButton());
    const notice = await screen.findByText(/Briefing erstellt: „Lagebericht – alles im grünen Bereich“/);
    expect(notice).toHaveTextContent("wurde auch als Push-Nachricht zugestellt");
    expect(notice.closest('[role="status"]')?.className).toContain("emerald");
    expect(screen.getByRole("link", { name: "Meldungen öffnen" })).toHaveAttribute("href", "/notifications");
    expect(screen.queryByRole("link", { name: "Push-Nachrichten einrichten" })).toBeNull();
  });

  it("ohne eingerichtete Push-Nachrichten: steht unter Meldungen, aufs Handy erst nach der Einrichtung, mit Link", async () => {
    stubFetch({ overview: overviewOf([host("h1", "pi")]), briefing: () => new Response(JSON.stringify({ title: "Lagebericht – es gibt etwas zu tun", push: "not_delivered" }), { status: 200 }) });
    render(<SocPage />);
    await screen.findByTestId("host-h1");
    fireEvent.click(briefingButton());
    const notice = await screen.findByText(/Briefing erstellt: „Lagebericht – es gibt etwas zu tun“/);
    expect(notice).toHaveTextContent("Es steht unter „Meldungen“");
    expect(notice).toHaveTextContent("Aufs Handy kommt es erst, wenn Push-Nachrichten (ntfy) eingerichtet sind");
    expect(notice.textContent).not.toMatch(/gesendet|zugestellt\./);
    expect(notice.closest('[role="status"]')?.className).toContain("amber");
    expect(screen.getByRole("link", { name: "Push-Nachrichten einrichten" })).toHaveAttribute("href", "/settings/extensions/ntfy");
    expect(screen.getByRole("link", { name: "Meldungen öffnen" })).toHaveAttribute("href", "/notifications");
  });

  it("meint mit dem Hinweis zum Push den ntfy-Dienst, nicht die eigenen Server", async () => {
    stubFetch({ overview: overviewOf([host("h1", "pi")]), briefing: () => new Response(JSON.stringify({ title: "Lagebericht", push: "not_delivered" }), { status: 200 }) });
    render(<SocPage />);
    await screen.findByTestId("host-h1");
    fireEvent.click(briefingButton());
    const notice = await screen.findByText(/Aufs Handy kommt es erst/);
    expect(notice).toHaveTextContent("und der ntfy-Dienst erreichbar ist (nicht einer deiner Server)");
    expect(notice.textContent).not.toMatch(/und der Server antwortet/);
  });

  it("ohne das Recht für die Modul-Einstellungen gibt es keinen Link dorthin", async () => {
    hasPermission.mockImplementation((p) => p !== "extensions.manage");
    stubFetch({ overview: overviewOf([host("h1", "pi")]), briefing: () => new Response(JSON.stringify({ title: "Lagebericht", push: "not_delivered" }), { status: 200 }) });
    render(<SocPage />);
    await screen.findByTestId("host-h1");
    fireEvent.click(briefingButton());
    await screen.findByText(/Aufs Handy kommt es erst/);
    expect(screen.queryByRole("link", { name: "Push-Nachrichten einrichten" })).toBeNull();
    expect(screen.getByRole("link", { name: "Meldungen öffnen" })).toBeInTheDocument();
  });

  it("im Wartungsfenster: erstellt, aber der Push ist unterdrückt", async () => {
    stubFetch({ overview: overviewOf([host("h1", "pi")]), briefing: () => new Response(JSON.stringify({ title: "Lagebericht – es gibt etwas zu tun", push: "suppressed" }), { status: 200 }) });
    render(<SocPage />);
    await screen.findByTestId("host-h1");
    fireEvent.click(briefingButton());
    expect(await screen.findByText(/weil gerade ein Wartungsfenster läuft/)).toBeInTheDocument();
  });

  it("ältere Server wissen nichts über den Push: dann steht da nur, was sicher stimmt", async () => {
    stubFetch({ overview: overviewOf([host("h1", "pi")]), briefing: () => new Response(JSON.stringify({ title: "Lagebericht – alles im grünen Bereich" }), { status: 200 }) });
    render(<SocPage />);
    await screen.findByTestId("host-h1");
    fireEvent.click(briefingButton());
    const notice = await screen.findByText(/Briefing erstellt: „Lagebericht – alles im grünen Bereich“/);
    expect(notice.textContent).not.toMatch(/Push|Handy|gesendet/);
  });

  it("ein Fehler (z. B. „wird gerade schon erstellt“) steht als Fehler da und gibt den Knopf wieder frei", async () => {
    stubFetch({
      overview: overviewOf([host("h1", "pi")]),
      briefing: () => new Response(JSON.stringify({ detail: "Der Lagebericht wird gerade schon erstellt – bitte einen Moment warten." }), { status: 409 }),
    });
    render(<SocPage />);
    await screen.findByTestId("host-h1");
    fireEvent.click(briefingButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("Briefing: Der Lagebericht wird gerade schon erstellt");
    expect(briefingButton()).toBeEnabled();
  });
});

describe("Übersicht für Einsteiger", () => {
  it("ohne Server: keine Note 0/100, sondern der Hinweis, zuerst einen Server hinzuzufügen", async () => {
    stubFetch({ overview: overviewOf([], { score: 0 }) });
    render(<SocPage />);
    const box = within(await screen.findByTestId("no-servers"));
    expect(box.getByText("Noch kein Server – nichts zu bewerten")).toBeInTheDocument();
    expect(box.getByText(/Füge zuerst einen Server hinzu/)).toBeInTheDocument();
    expect(box.getByRole("link", { name: "Server hinzufügen" })).toHaveAttribute("href", "/settings/hosts");
    expect(screen.queryByRole("img", { name: /Schutzwert/ })).toBeNull();
    expect(screen.queryByText("Handlungsbedarf")).toBeNull();
    expect(screen.queryByTestId("next-step")).toBeNull();
    expect(screen.getByText("0 / 0").closest("div")?.className).not.toMatch(/emerald|amber/);
  });

  it("Server angelegt, aber keiner prüfbar (kein SSH-Zugang): kein \"füge zuerst einen Server hinzu\", sondern der Zugang fehlt", async () => {
    stubFetch({ overview: overviewOf([], { score: 0, hosts_known: 2 }) });
    render(<SocPage />);
    const box = within(await screen.findByTestId("no-servers"));
    expect(box.getByText("Noch kein Server prüfbar – nichts zu bewerten")).toBeInTheDocument();
    expect(box.getByText(/Du hast 2 Server angelegt/)).toBeInTheDocument();
    expect(box.getByText(/SSH-Zugang/)).toBeInTheDocument();
    expect(box.queryByText(/Füge zuerst einen Server hinzu/)).toBeNull();
    expect(box.queryByText("Noch kein Server – nichts zu bewerten")).toBeNull();
    expect(box.getByRole("link", { name: "Zugang einrichten" })).toHaveAttribute("href", "/settings/hosts");
    expect(box.queryByRole("link", { name: "Server hinzufügen" })).toBeNull();
    expect(screen.queryByRole("img", { name: /Schutzwert/ })).toBeNull();
  });

  it("Abzeichen sind nur grün, wenn der Schutz wirklich wirkt", async () => {
    stubFetch({ overview: overviewOf([bare("h1", "test-pi")]) });
    const first = render(<SocPage />);
    await screen.findByTestId("host-h1");
    const watch = screen.getByText("Echtzeit-Wächter: an, aber noch auf keinem Server aktiv");
    expect(watch.className).toContain("amber");
    expect(screen.getByText("Automatische Quarantäne: an, aber noch auf keinem Server aktiv").className).toContain("amber");
    expect(screen.queryByText(/Echtzeit-Wächter aktiv/)).toBeNull();
    first.unmount();

    stubFetch({ overview: overviewOf([host("h1", "pi"), bare("h2", "docker")]) });
    const second = render(<SocPage />);
    await screen.findByTestId("host-h2");
    const partly = screen.getByText("Echtzeit-Wächter: aktiv auf 1 von 2 Servern (alle 10 Min.)");
    expect(partly.className).toContain("amber");
    second.unmount();

    stubFetch({ overview: overviewOf([host("h1", "pi")]) });
    render(<SocPage />);
    await screen.findByTestId("host-h1");
    expect(screen.getByText("Echtzeit-Wächter aktiv (alle 10 Min.)").className).toContain("emerald");
    expect(screen.getByText("Automatische Quarantäne an").className).toContain("emerald");
  });

  it("abgeschaltete Funktionen bleiben „aus“ (gelb)", async () => {
    stubFetch({ overview: { ...overviewOf([host("h1", "pi")]), config: { ...CONFIG, realtime_enabled: false, auto_quarantine: false } } });
    render(<SocPage />);
    await screen.findByTestId("host-h1");
    expect(screen.getByText("Echtzeit-Wächter aus").className).toContain("amber");
    expect(screen.getByText("Automatische Quarantäne aus").className).toContain("amber");
  });

  it("nächster Schritt: ClamAV auf dem Server ohne Virenscanner installieren (mit Knopf)", async () => {
    const calls: Call[] = [];
    stubFetch({ overview: overviewOf([bare("h1", "test-pi")]) }, calls);
    render(<SocPage />);
    const step = within(await screen.findByTestId("next-step"));
    expect(step.getByText(/Nächster Schritt:/)).toBeInTheDocument();
    expect(step.getByText(/Installiere ClamAV \(den Virenscanner\) auf „test-pi“/)).toBeInTheDocument();
    fireEvent.click(step.getByRole("button", { name: "ClamAV installieren" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/hosts/h1/install"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/hosts/h1/install"))?.body).toEqual({ package: "clamav" });
    expect(confirmDialog).toHaveBeenCalledWith(expect.stringContaining("ClamAV"), expect.anything());
  });

  it("nächster Schritt bei einem Server, der nicht antwortet: prüfen statt installieren", async () => {
    stubFetch({ overview: overviewOf([bare("h1", "bastel-pi", { reachable: false, host_status: "down", error: "Zeitüberschreitung" })]) });
    render(<SocPage />);
    const step = within(await screen.findByTestId("next-step"));
    expect(step.getByText(/„bastel-pi“ antwortet nicht/)).toBeInTheDocument();
    expect(step.getByRole("link", { name: "Server & Zugänge öffnen" })).toHaveAttribute("href", "/settings/hosts");
    expect(step.queryByRole("button")).toBeNull();
  });

  it("nächster Schritt bei offenen Bedrohungen führt zu den Funden; ohne Dringendes gibt es keinen", async () => {
    stubFetch({ overview: overviewOf([host("h1", "pi")], { open_threats: 2 }) });
    const first = render(<SocPage />);
    const step = within(await screen.findByTestId("next-step"));
    expect(step.getByText(/Es wurden 2 Bedrohungen gefunden/)).toBeInTheDocument();
    fireEvent.click(step.getByRole("button", { name: "Funde ansehen" }));
    expect(screen.getByRole("tab", { name: /Quarantäne/ })).toHaveAttribute("aria-selected", "true");
    first.unmount();
    window.history.replaceState(null, "", "/ext/nexus-soc/soc");

    stubFetch({ overview: overviewOf([host("h1", "pi")]) });
    render(<SocPage />);
    await screen.findByTestId("host-h1");
    expect(screen.queryByTestId("next-step")).toBeNull();
  });

  it("nächster Schritt: erst scannen, dann Lynis; Knöpfe nur mit dem Recht zum Verwalten", async () => {
    stubFetch({ overview: overviewOf([host("h1", "pi", { last_scan: null })]) });
    const first = render(<SocPage />);
    expect(within(await screen.findByTestId("next-step")).getByText(/„pi“ wurde noch nie geprüft/)).toBeInTheDocument();
    first.unmount();

    stubFetch({ overview: overviewOf([host("h1", "pi", { lynis_installed: false, last_audit: null })]) });
    render(<SocPage />);
    const step = within(await screen.findByTestId("next-step"));
    expect(step.getByText(/Installiere Lynis auf „pi“. Es prüft, wie sicher der Server eingestellt ist/)).toBeInTheDocument();
    expect(step.getByRole("button", { name: "Lynis installieren" })).toBeInTheDocument();
  });

  it("erklärt die Fachbegriffe in einfachem Deutsch", async () => {
    stubFetch({ overview: overviewOf([host("h1", "pi")]) });
    render(<SocPage />);
    const glossary = within(await screen.findByTestId("glossary"));
    expect(glossary.getByText("Was bedeuten die Begriffe?")).toBeInTheDocument();
    for (const [term, snippet] of [
      ["ClamAV", "der Virenscanner"], ["Lynis, Härtung, Härtungs-Audit", "Lynis prüft, wie sicher ein Server eingestellt ist"],
      ["Fail2ban", "sperrt Adressen, die zu oft ein falsches Passwort probieren"], ["Quarantäne", "weggesperrt statt gelöscht"],
    ]) {
      expect(glossary.getByText(term)).toBeInTheDocument();
      expect(screen.getByTestId("glossary")).toHaveTextContent(snippet);
    }
    expect(screen.getByRole("button", { name: /Härtungs-Audit/ })).toHaveAttribute("title", expect.stringContaining("wie sicher"));
    expect(screen.getByText("Härtung (Ø)")).toBeInTheDocument();
    expect(screen.getByText(/wie sicher die Server eingestellt sind \(Lynis\)/)).toBeInTheDocument();
  });

  it("Handy: die Kurzlage-Karten dürfen schmaler als ihr Text werden (kein waagerechtes Scrollen)", async () => {
    window.history.replaceState(null, "", "/ext/nexus-soc/soc");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
      if (url.includes("/defender/overview")) return json(overviewOf([host("h1", "pi")]));
      if (url.endsWith("/defender/updates")) return json({ hosts: [], runs: [], summary: { hosts: 1, checked: 1, up_to_date: 0, packages: 3, security: 0, reboot: 0 } });
      if (url.endsWith("/defender/guard")) return json({ hosts: [], summary: { failed_24h: 0, banned: 0, open_events: 0, fail2ban_running: 0, hosts: 1 } });
      return json([]);
    }));
    render(<SocPage />);
    const side = await screen.findByTestId("side-summary");
    await waitFor(() => expect(within(side).getAllByRole("button")).toHaveLength(2));
    // Ein Grid ohne feste Spalte wächst mit dem längsten Text; `grid-cols-1` (minmax(0,1fr)) und `min-w-0` lassen es schrumpfen.
    expect(side.className).toContain("grid-cols-1");
    for (const card of within(side).getAllByRole("button")) expect(card.className).toMatch(/\bmin-w-0\b/);
    expect(within(side).getByText(/ruhig · 0 SSH-Fehlversuche in 24 Std\., 0 gesperrt/)).toHaveClass("truncate");
  });
});
