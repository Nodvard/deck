import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../../state/auth";
import { SystemSettings } from "./SystemSettings";
import { UpdatesCard, type UpdateStatus } from "./UpdatesCard";

const BASE: UpdateStatus = {
  current: "0.5.0", latest: "0.6.0", latest_digest: null, available: true, channel: "stable", enabled: true,
  checked_at: "2026-10-01T04:41:00Z", attempted_at: "2026-10-01T04:41:00Z", source: "cache", error: null,
  official_image: true, image: "ghcr.io/nodvard/deck", official_image_name: "ghcr.io/nodvard/deck", helper: false,
  release_notes_url: "https://github.com/nodvard/deck/blob/v0.6.0/CHANGELOG.md",
};

type Route = (body: unknown) => Response;

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });

/** `GET /system/info` liefert die Karte nebenbei (Datenbank, Kopien vor Updates); ohne Angabe: SQLite, keine Kopie. */
const INFO_DEFAULT: Route = () => json({ database: "sqlite", pre_update_copies: [] });

function mockApi(routes: Record<string, Route>) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const all: Record<string, Route> = { "GET /system/info": INFO_DEFAULT, ...routes };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^\/api\/v1/, "").split("?")[0];
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ method, path, body });
    const handler = all[`${method} ${path}`];
    if (!handler) throw new Error(`Unerwarteter Fetch: ${method} ${path}`);
    return handler(body);
  }));
  return calls;
}

/** Wartet, bis die Karte steht, und liefert eine Funktion, die einen Reiter waehlt und sein Panel zurueckgibt. */
async function openTabs(ready: string | RegExp = /Version/) {
  await screen.findAllByText(ready);
  const tabs = within(screen.getByRole("tablist"));
  return (name: string) => {
    fireEvent.click(tabs.getByRole("tab", { name }));
    return screen.getByRole("tabpanel");
  };
}

const ALL_TABS = ["Docker Compose", "Docker Desktop", "Portainer", "Synology", "Unraid", "deploy_pi.sh"];

function setUser(permissions: string[]) {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: false, locale: "de", permissions },
  });
}

beforeEach(() => setUser(["system.read"]));
afterEach(() => vi.unstubAllGlobals());

describe("Karte Updates", () => {
  it("zeigt „Update verfügbar“ mit Version und Link zu den Neuerungen", async () => {
    mockApi({ "GET /system/updates": () => json(BASE) });
    render(<UpdatesCard />);
    const hint = await screen.findByText("Update verfügbar: Version 0.6.0");
    expect(hint.closest("[role=status]")).not.toBeNull();
    expect(screen.getByText("Version 0.5.0")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Was ist neu/ })).toHaveAttribute("href", "https://github.com/nodvard/deck/blob/v0.6.0/CHANGELOG.md");
  });

  it("sagt, wenn alles aktuell ist", async () => {
    mockApi({ "GET /system/updates": () => json({ ...BASE, latest: "0.5.0", available: false }) });
    render(<UpdatesCard />);
    expect(await screen.findByText(/Du bist auf dem neuesten Stand/)).toBeInTheDocument();
    expect(screen.queryByText(/Update verfügbar/)).not.toBeInTheDocument();
  });

  it("ohne Prüfung bisher: noch nicht geprüft, kein Hinweis", async () => {
    mockApi({ "GET /system/updates": () => json({ ...BASE, latest: null, available: false, checked_at: null, release_notes_url: null }) });
    render(<UpdatesCard />);
    expect(await screen.findByText("noch nicht geprüft")).toBeInTheDocument();
    expect(screen.queryByText(/Update verfügbar/)).not.toBeInTheDocument();
    expect(screen.queryByText(/neuesten Stand/)).not.toBeInTheDocument();
  });

  it("geprüft, aber keine passende Version: „keine gefunden“ statt „noch nicht geprüft“", async () => {
    const none = { ...BASE, latest: null, available: false, release_notes_url: null, source: "live" as const };
    mockApi({ "GET /system/updates": () => json(none) });
    const first = render(<UpdatesCard />);
    expect(await screen.findByText("keine gefunden")).toBeInTheDocument();
    expect(screen.queryByText("noch nicht geprüft")).not.toBeInTheDocument();
    expect(screen.getByText("Es gibt noch keine fertige Version zum Herunterladen.")).toBeInTheDocument();
    expect(screen.queryByText(/neuesten Stand/)).not.toBeInTheDocument();
    first.unmount();

    mockApi({ "GET /system/updates": () => json({ ...none, channel: "beta" }) });
    render(<UpdatesCard />);
    expect(await screen.findByText("Es gibt noch keine Version zum Herunterladen.")).toBeInTheDocument();
  });

  it("offline nach „keine gefunden“: letzter bekannter Stand", async () => {
    mockApi({
      "GET /system/updates": () => json({ ...BASE, latest: null, available: false, source: "offline", error: "Konnte nicht prüfen (offline?)." }),
    });
    render(<UpdatesCard />);
    expect(await screen.findByText(/Konnte nicht prüfen \(offline\?\)\. Angezeigt ist der letzte bekannte Stand/)).toBeInTheDocument();
    expect(screen.getByText("keine gefunden")).toBeInTheDocument();
    expect(screen.queryByText(/Es gibt noch keine/)).not.toBeInTheDocument();
  });

  it("offline: letzter Stand mit Hinweis", async () => {
    mockApi({ "GET /system/updates": () => json({ ...BASE, source: "offline", error: "Konnte nicht prüfen (offline?)." }) });
    render(<UpdatesCard />);
    expect(await screen.findByText(/Konnte nicht prüfen \(offline\?\)\. Angezeigt ist der letzte bekannte Stand/)).toBeInTheDocument();
    expect(screen.getByText("Update verfügbar: Version 0.6.0")).toBeInTheDocument();
  });

  it("„Jetzt suchen“ fragt nach und zeigt das Ergebnis; zu oft gibt eine verständliche Meldung", async () => {
    let first = true;
    const calls = mockApi({
      "GET /system/updates": () => json({ ...BASE, latest: null, available: false, checked_at: null }),
      "POST /system/updates/check": () => {
        if (first) {
          first = false;
          return json({ ...BASE, latest: "0.7.0", source: "live", release_notes_url: null });
        }
        return json({ detail: "Gerade erst nachgesehen. Bitte in 1 Minute erneut versuchen." }, 429);
      },
    });
    render(<UpdatesCard />);
    const button = await screen.findByRole("button", { name: /Jetzt suchen/ });
    fireEvent.click(button);
    expect(await screen.findByText("Update verfügbar: Version 0.7.0")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Jetzt suchen/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Gerade erst nachgesehen");
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(2);
    expect(screen.getByText("Update verfügbar: Version 0.7.0")).toBeInTheDocument();
  });

  it("Reiter zeigen je Umgebung die Schritte, die Kopie und den Rückweg", async () => {
    mockApi({ "GET /system/updates": () => json(BASE) });
    render(<UpdatesCard />);
    await screen.findByText("Update verfügbar: Version 0.6.0");
    const tabs = within(screen.getByRole("tablist"));
    expect(tabs.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Docker Compose", "Docker Desktop", "Portainer", "Synology", "Unraid", "deploy_pi.sh",
    ]);
    const panel = () => screen.getByRole("tabpanel");
    expect(tabs.getByRole("tab", { name: "Docker Compose" })).toHaveAttribute("aria-selected", "true");
    expect(panel()).toHaveTextContent("docker compose pull");
    expect(panel()).toHaveTextContent("ghcr.io/nodvard/deck:0.5.0");

    fireEvent.click(tabs.getByRole("tab", { name: "Portainer" }));
    expect(tabs.getByRole("tab", { name: "Portainer" })).toHaveAttribute("aria-selected", "true");
    expect(panel()).toHaveTextContent("Update the stack");
    expect(panel()).toHaveTextContent("Re-pull image");

    fireEvent.click(tabs.getByRole("tab", { name: "Unraid" }));
    expect(panel()).toHaveTextContent("Check for Updates");
    expect(panel()).toHaveTextContent("Apply update");

    fireEvent.click(tabs.getByRole("tab", { name: "Synology" }));
    expect(panel()).toHaveTextContent("Container Manager");

    fireEvent.click(tabs.getByRole("tab", { name: "Docker Desktop" }));
    expect(panel()).toHaveTextContent("docker compose up -d");

    fireEvent.click(tabs.getByRole("tab", { name: "deploy_pi.sh" }));
    expect(panel()).toHaveTextContent("scripts/deploy_pi.sh");
    expect(panel()).toHaveTextContent("pi_switch.sh rollback");
    expect(panel()).toHaveTextContent("git checkout v0.6.0");

    for (const name of ALL_TABS) {
      fireEvent.click(tabs.getByRole("tab", { name }));
      expect(panel()).toHaveTextContent("Vorher wird automatisch eine Kopie der Datenbank angelegt");
      expect(panel()).toHaveTextContent("Zurück zur Vorversion");
    }
  });

  it("Compose und Docker Desktop: Befehle für compose.yml und Hinweis auf andere Dateinamen", async () => {
    mockApi({ "GET /system/updates": () => json(BASE) });
    render(<UpdatesCard />);
    const tab = await openTabs("Update verfügbar: Version 0.6.0");
    for (const name of ["Docker Compose", "Docker Desktop"]) {
      const panel = tab(name);
      expect(panel).toHaveTextContent("docker compose pull");
      expect(panel).toHaveTextContent("docker compose up -d");
      expect(panel).toHaveTextContent(
        "Im Ordner mit der Datei compose.yml ausführen (unter Linux ggf. sudo davor). Heißt deine Datei anders (z. B. compose.standalone.yml aus einer älteren Anleitung), häng -f und den Dateinamen an: docker compose -f compose.standalone.yml ….",
      );
      expect(panel).not.toHaveTextContent(/docker compose -f \S+ (pull|up)/);
    }
  });

  it("Rückweg vor dem Update: die jetzige Version notieren, nicht als „bisherige“ hinstellen", async () => {
    mockApi({ "GET /system/updates": () => json(BASE) });
    render(<UpdatesCard />);
    const tab = await openTabs("Update verfügbar: Version 0.6.0");
    for (const name of ["Docker Compose", "Docker Desktop", "Portainer", "Synology", "Unraid"]) {
      const panel = tab(name);
      expect(panel).toHaveTextContent("Notier dir vor dem Update die jetzige Version: ghcr.io/nodvard/deck:0.5.0.");
      expect(panel).toHaveTextContent("diese Version wieder eintragen");
      expect(panel).not.toHaveTextContent("bisherige");
    }
    expect(tab("Docker Compose")).toHaveTextContent("diese Version wieder eintragen und docker compose up -d ausführen");
  });

  it("Rückweg nach dem Update: nie die laufende Version als Ziel, die Vorversion nur, wenn sie sicher bekannt ist", async () => {
    const after = { ...BASE, current: "0.6.0", latest: "0.6.0", available: false };
    mockApi({ "GET /system/updates": () => json(after) });
    const first = render(<UpdatesCard />);
    let tab = await openTabs(/neuesten Stand/);
    for (const name of ["Docker Compose", "Portainer", "Synology", "Unraid"]) {
      const panel = tab(name);
      expect(panel).toHaveTextContent("die Version, die vor dem Update lief (jetzt läuft 0.6.0), eintragen");
      expect(panel).not.toHaveTextContent("ghcr.io/nodvard/deck:0.6.0");
      expect(panel).not.toHaveTextContent("Notier dir");
    }
    first.unmount();

    // Eine Kopie vor dem Update von 0.5.0 auf genau die laufende Version: dann ist die Vorversion bekannt.
    const copy = (from: string, to: string) => ({ name: `x_${from}_${to}.db`, created_at: null, from_version: from, to_version: to, size: 1 });
    mockApi({
      "GET /system/updates": () => json(after),
      "GET /system/info": () => json({ database: "sqlite", pre_update_copies: [copy("0.4.0", "0.5.0"), copy("0.5.0", "0.6.0")] }),
    });
    render(<UpdatesCard />);
    tab = await openTabs(/neuesten Stand/);
    await vi.waitFor(() => expect(tab("Docker Compose")).toHaveTextContent("die Version von vor dem Update (ghcr.io/nodvard/deck:0.5.0) eintragen"));
    expect(tab("Unraid")).toHaveTextContent("bei „Repository“ die Version von vor dem Update (ghcr.io/nodvard/deck:0.5.0) eintragen");
    expect(tab("Unraid")).not.toHaveTextContent("0.4.0");
  });

  it("Rückweg: eine Kopie mit gleicher Von- und Nach-Version (Vorabversion, selbst gebaut) nennt keine Vorversion", async () => {
    const after = { ...BASE, current: "0.6.0", latest: "0.6.0", available: false };
    const copy = (from: string, to: string) => ({ name: `x_${from}_${to}.db`, created_at: null, from_version: from, to_version: to, size: 1 });
    mockApi({
      "GET /system/updates": () => json(after),
      "GET /system/info": () => json({ database: "sqlite", pre_update_copies: [copy("0.6.0", "0.6.0"), copy("0.5.0", "0.6.0")] }),
    });
    render(<UpdatesCard />);
    const tab = await openTabs(/neuesten Stand/);
    await vi.waitFor(() => expect(tab("Docker Compose")).toHaveTextContent("Hat die neue Version noch nie"));
    for (const name of ["Docker Compose", "Portainer", "Synology", "Unraid"]) {
      const panel = tab(name);
      expect(panel).toHaveTextContent("die Version, die vor dem Update lief (jetzt läuft 0.6.0)");
      expect(panel).not.toHaveTextContent("ghcr.io/nodvard/deck:0.6.0");
      expect(panel).not.toHaveTextContent("ghcr.io/nodvard/deck:0.5.0");
    }
  });

  it("Rückweg: „Das klappt erst ab“ steht hinter den zwei Wegen der Notseite, nicht hinter „neue Version eintragen“", async () => {
    mockApi({ "GET /system/updates": () => json(BASE) });
    render(<UpdatesCard />);
    const tab = await openTabs("Update verfügbar: Version 0.6.0");
    for (const name of ALL_TABS) {
      const text = tab(name).textContent ?? "";
      expect(text).toContain("Beides klappt erst ab einer Version, die diese Kopien schon anlegt. Oder wieder auf die neue Version wechseln.");
      expect(text).not.toContain("Oder wieder die neue Version eintragen");
    }
  });

  it("Rückweg: erklärt die Notseite, wenn die neue Version die Datenbank schon umgebaut hat", async () => {
    mockApi({ "GET /system/updates": () => json(BASE) });
    render(<UpdatesCard />);
    const tab = await openTabs("Update verfügbar: Version 0.6.0");
    for (const name of ALL_TABS) {
      const panel = tab(name);
      expect(panel).toHaveTextContent("Hat die neue Version noch nie richtig gestartet, spielt die alte die Kopie von selbst wieder ein.");
      expect(panel).toHaveTextContent("Lief die neue Version schon und hat sie dabei die Datenbank umgebaut, zeigt die alte eine Notseite");
      expect(panel).toHaveTextContent("Notfallcode (steht im Protokoll des Containers)");
      expect(panel).toHaveTextContent("„Stand vor dem Update wiederherstellen“");
      expect(panel).toHaveTextContent("was seit dem Update geändert wurde, geht dabei verloren");
    }
  });

  it("andere Datenbank als SQLite: keine Kopie versprechen", async () => {
    mockApi({ "GET /system/updates": () => json(BASE), "GET /system/info": () => json({ database: "postgresql", pre_update_copies: [] }) });
    render(<UpdatesCard />);
    const tab = await openTabs("Update verfügbar: Version 0.6.0");
    await vi.waitFor(() => expect(tab("Docker Compose")).toHaveTextContent("Bei dieser Datenbank (postgresql) legt Nodvard Deck vor einem Update keine Kopie an"));
    expect(tab("Docker Compose")).not.toHaveTextContent("Vorher wird automatisch eine Kopie");
    expect(tab("Docker Compose")).not.toHaveTextContent("spielt die alte die Kopie von selbst wieder ein");
    expect(tab("Docker Compose")).toHaveTextContent("zurück geht es dann nur mit deiner eigenen Sicherung");
  });

  it("ohne /system/info (z. B. Fehler) bleibt es beim Standard SQLite", async () => {
    mockApi({ "GET /system/updates": () => json(BASE), "GET /system/info": () => json({ detail: "kaputt" }, 500) });
    render(<UpdatesCard />);
    const tab = await openTabs("Update verfügbar: Version 0.6.0");
    expect(tab("Docker Compose")).toHaveTextContent("Vorher wird automatisch eine Kopie der Datenbank angelegt");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("laufende Version neuer als die neueste: keine Anleitung zurück auf die ältere", async () => {
    mockApi({ "GET /system/updates": () => json({ ...BASE, current: "0.6.0", latest: "0.5.0", available: false }) });
    render(<UpdatesCard />);
    const tab = await openTabs(/neuesten Stand/);
    for (const name of ALL_TABS) {
      expect(tab(name)).not.toHaveTextContent("0.5.0");
    }
    expect(tab("Docker Compose")).toHaveTextContent("ghcr.io/nodvard/deck:<neue Version>");
    expect(tab("deploy_pi.sh")).toHaveTextContent("git checkout v<neue Version>");
    expect(screen.queryByText(/Update verfügbar/)).not.toBeInTheDocument();
  });

  it("feste Version oder Reihe statt :latest: Hinweis auch bei Unraid (Repository)", async () => {
    mockApi({ "GET /system/updates": () => json(BASE) });
    render(<UpdatesCard />);
    const tab = await openTabs("Update verfügbar: Version 0.6.0");
    for (const name of ["Docker Compose", "Docker Desktop", "Portainer", "Synology"]) {
      expect(tab(name)).toHaveTextContent(
        "Steht bei image: eine feste Version (z. B. :0.5.0) oder eine Reihe wie :0.5 statt :latest, trag dort zuerst die neue ein: ghcr.io/nodvard/deck:0.6.0.",
      );
    }
    expect(tab("Unraid")).toHaveTextContent("Steht bei „Repository“ (Container bearbeiten: „Edit“, danach „Apply“) eine feste Version");
    expect(tab("Unraid")).toHaveTextContent("ghcr.io/nodvard/deck:0.6.0");
  });

  it("Beta mit Vorabversion: gibt es nicht unter :latest, genaues Tag eintragen", async () => {
    mockApi({
      "GET /system/updates": () => json({ ...BASE, channel: "beta", latest: "0.7.0-rc1", release_notes_url: null }),
    });
    render(<UpdatesCard />);
    expect(await screen.findByText("Update verfügbar: Version 0.7.0-rc1 (Vorabversion)")).toBeInTheDocument();
    const tab = await openTabs(/Vorabversion/);
    for (const name of ["Docker Compose", "Docker Desktop", "Portainer", "Synology"]) {
      const panel = tab(name);
      expect(panel).toHaveTextContent("Vorabversionen gibt es nicht unter :latest: Trag bei image: genau ghcr.io/nodvard/deck:0.7.0-rc1 ein.");
      expect(panel).not.toHaveTextContent("eine Reihe wie");
    }
    expect(tab("Unraid")).toHaveTextContent("Trag bei „Repository“ (Container bearbeiten: „Edit“, danach „Apply“) genau ghcr.io/nodvard/deck:0.7.0-rc1 ein.");
    expect(tab("deploy_pi.sh")).toHaveTextContent("git checkout v0.7.0-rc1");
  });

  it("selbst gebautes Image: Hinweis und Anleitung deploy_pi.sh vorne", async () => {
    mockApi({ "GET /system/updates": () => json({ ...BASE, official_image: false, image: null }) });
    render(<UpdatesCard />);
    expect(await screen.findByText(/läuft mit einem selbst gebauten Image/)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "deploy_pi.sh" })).toHaveAttribute("aria-selected", "true");
  });

  it("offizielles Image: kein Hinweis auf selbst gebaut", async () => {
    mockApi({ "GET /system/updates": () => json(BASE) });
    render(<UpdatesCard />);
    await screen.findByText("Update verfügbar: Version 0.6.0");
    expect(screen.queryByText(/selbst gebauten Image/)).not.toBeInTheDocument();
  });

  it("Schalter und Kanal nur mit settings.write, mit Datenschutz-Hinweis", async () => {
    mockApi({ "GET /system/updates": () => json(BASE) });
    const first = render(<UpdatesCard />);
    await screen.findByText("Update verfügbar: Version 0.6.0");
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    expect(screen.getByText(/Tägliche Suche: an/)).toHaveTextContent(
      "Bei jeder Suche sieht GitHub (ghcr.io) die IP-Adresse und den Zeitpunkt, sonst nichts. Abschalten kann sie, wer Einstellungen ändern darf.",
    );
    first.unmount();

    setUser(["system.read", "settings.write"]);
    const calls = mockApi({
      "GET /system/updates": () => json(BASE),
      "PUT /settings/system.update_check.enabled": (body) => json({ key: "system.update_check.enabled", value: (body as { value: boolean }).value }),
      "PUT /settings/system.update_check.channel": (body) => json({ key: "system.update_check.channel", value: (body as { value: string }).value }),
    });
    render(<UpdatesCard />);
    const toggle = await screen.findByRole("switch", { name: "Täglich automatisch nach Updates suchen" });
    expect(screen.getByText(/GitHub sieht dabei die IP-Adresse/)).toHaveTextContent(
      "Dazu fragt Nodvard Deck einmal am Tag bei GitHub (ghcr.io, dort liegen die Images) nach, welche Versionen es gibt. GitHub sieht dabei die IP-Adresse deines Anschlusses und den Zeitpunkt; sonst wird nichts übertragen, auch nicht die installierte Version. Wenn du das nicht möchtest, schalte die Suche hier ab.",
    );
    expect(toggle).toHaveAttribute("aria-checked", "true");
    fireEvent.click(toggle);
    await vi.waitFor(() => expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false"));
    expect(calls.find((c) => c.method === "PUT")).toEqual({ method: "PUT", path: "/settings/system.update_check.enabled", body: { value: false } });

    fireEvent.change(screen.getByRole("combobox", { name: "Welche Versionen" }), { target: { value: "beta" } });
    await vi.waitFor(() => expect(calls.some((c) => c.path === "/settings/system.update_check.channel")).toBe(true));
    expect(calls.find((c) => c.path === "/settings/system.update_check.channel")?.body).toEqual({ value: "beta" });
  });

  it("während „Jetzt suchen“ sind Schalter und Kanal gesperrt (keine Antwort überschreibt eine neuere)", async () => {
    setUser(["system.read", "settings.write"]);
    let finish: (r: Response) => void = () => undefined;
    const calls = mockApi({
      "GET /system/updates": () => json({ ...BASE, latest: null, available: false, checked_at: null }),
      "POST /system/updates/check": () => new Promise<Response>((resolve) => (finish = resolve)) as unknown as Response,
    });
    render(<UpdatesCard />);
    const toggle = await screen.findByRole("switch", { name: "Täglich automatisch nach Updates suchen" });
    const select = screen.getByRole("combobox", { name: "Welche Versionen" });
    fireEvent.click(screen.getByRole("button", { name: /Jetzt suchen/ }));
    await vi.waitFor(() => expect(toggle).toBeDisabled());
    expect(select).toBeDisabled();
    fireEvent.click(toggle);
    expect(calls.some((c) => c.method === "PUT")).toBe(false);

    finish(json({ ...BASE, latest: "0.7.0", source: "live" }));
    expect(await screen.findByText("Update verfügbar: Version 0.7.0")).toBeInTheDocument();
    expect(toggle).not.toBeDisabled();
    expect(select).not.toBeDisabled();
  });

  it("während gespeichert wird, ist „Jetzt suchen“ gesperrt; der Schalter überschreibt kein neueres Ergebnis", async () => {
    setUser(["system.read", "settings.write"]);
    let finish: (r: Response) => void = () => undefined;
    const calls = mockApi({
      "GET /system/updates": () => json(BASE),
      "PUT /settings/system.update_check.enabled": () => new Promise<Response>((resolve) => (finish = resolve)) as unknown as Response,
    });
    render(<UpdatesCard />);
    const toggle = await screen.findByRole("switch", { name: "Täglich automatisch nach Updates suchen" });
    fireEvent.click(toggle);
    const button = screen.getByRole("button", { name: /Jetzt suchen/ });
    await vi.waitFor(() => expect(button).toBeDisabled());
    fireEvent.click(button);
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    finish(json({ key: "system.update_check.enabled", value: false }));
    await vi.waitFor(() => expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false"));
    expect(button).not.toBeDisabled();
    expect(screen.getByText("Update verfügbar: Version 0.6.0")).toBeInTheDocument();
  });

  it("gehört mit system.read zum Reiter System, ohne das Recht nicht", async () => {
    const calls = mockApi({
      "GET /system/updates": () => json(BASE),
      "GET /system/info": () => json({ pre_update_copies: [] }),
      "GET /system/backups": () => json({}, 500),
      "GET /system/restore/status": () => json({}, 500),
    });
    const first = render(<SystemSettings />);
    expect(await screen.findByText("Updates", { selector: "h3" })).toBeInTheDocument();
    first.unmount();

    setUser(["settings.write"]);
    calls.length = 0;
    mockApi({ "GET /settings": () => json([{ key: "system.timezone", value: "Europe/Berlin" }]) });
    render(<SystemSettings />);
    await screen.findByText("Zeit & Protokoll");
    expect(screen.queryByText("Updates", { selector: "h3" })).not.toBeInTheDocument();
  });
});
