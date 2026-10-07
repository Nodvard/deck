import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { browserNavigation, restartTiming } from "../../lib/restore";
import { resetUpdateHelper, updaterTiming, type HelperResult, type HelperView } from "../../lib/updater";
import { useAuthStore } from "../../state/auth";
import { SystemSettings } from "./SystemSettings";
import { UpdatesCard, type UpdateStatus } from "./UpdatesCard";
import { FAILED_MANUAL_STEPS, HELPER_CODE_TEXTS, HELPER_GUIDE_URL, PRESENCE_TEXTS, STEP_TEXTS, outcomeTitle } from "./updaterTexts";

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

beforeEach(() => {
  setUser(["system.read"]);
  resetUpdateHelper();
});
afterEach(() => {
  resetUpdateHelper();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

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

// ---------------------------------------------------------------------------
// Update-Helfer: Zustand, Knopf, Bestätigung, Fortschritt, Ergebnis
// ---------------------------------------------------------------------------

const REQUEST_ID = "6f1c2b0e-9a4d-4c1e-8f00-1b2c3d4e5f60";

function helperView(patch: Partial<HelperView> = {}): HelperView {
  return {
    present: true, reason: null, ready: true, ready_reason: null, state: "idle", helper_version: "0.7.0",
    heartbeat_at: Math.floor(Date.now() / 1000) - 20, target: { current_version: "0.5.0", floating_tag: "latest", pinned: false },
    busy: null, previous: null, last_result: null, pending: null, ...patch,
  };
}

const ABSENT = (reason: HelperView["reason"]) =>
  helperView({ present: false, reason, ready: false, state: null, helper_version: null, heartbeat_at: null, target: null });

const busyView = (step: string) =>
  helperView({ ready: false, ready_reason: "busy", state: "busy", target: null, busy: { id: REQUEST_ID, action: "update", step, since: Math.floor(Date.now() / 1000) } });

const helperResult = (outcome: HelperResult["outcome"], code: string | null = null, action: HelperResult["action"] = "update"): HelperResult => ({
  id: REQUEST_ID, action, from: action === "update" ? "0.5.0" : "0.6.0", to: action === "update" ? "0.6.0" : "0.5.0", outcome, code,
  finished_at: Math.floor(Date.now() / 1000) - 30,
});

function setOwner() {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
  });
}

const offline = () => {
  throw new TypeError("offline");
};

async function flush(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe("Karte Updates: Update-Helfer", () => {
  it("nicht eingerichtet: kurzer Hinweis mit Link zur Anleitung, kein Knopf", async () => {
    setOwner();
    mockApi({ "GET /system/updates": () => json(BASE), "GET /system/updates/helper": () => json(ABSENT("missing")) });
    render(<UpdatesCard />);
    const state = await screen.findByTestId("helper-state");
    expect(state).toHaveAttribute("data-state", "missing");
    expect(state).toHaveTextContent("Update-Helfer: nicht eingerichtet.");
    expect(within(state).getByRole("link", { name: /Anleitung: deploy\/README\.md, Abschnitt „Update-Helfer“/ })).toHaveAttribute("href", HELPER_GUIDE_URL);
    expect(screen.queryByRole("button", { name: /Jetzt aktualisieren/ })).not.toBeInTheDocument();
  });

  it("bereit: mit Version und letztem Lebenszeichen", async () => {
    mockApi({ "GET /system/updates": () => json(BASE), "GET /system/updates/helper": () => json(helperView()) });
    render(<UpdatesCard />);
    const state = await screen.findByTestId("helper-state");
    expect(state).toHaveAttribute("data-state", "ready");
    // Vor 20 Sekunden (ganze Sekunden vom Server, je nach Bruchteil gerundet 20 oder 21).
    expect(state).toHaveTextContent(/Update-Helfer: bereit · Version 0\.7\.0 · letztes Lebenszeichen vor 2[01] Sek\./);
  });

  it("nicht bereit: der Grund aus dem Code, verständlich", async () => {
    setOwner();
    mockApi({
      "GET /system/updates": () => json(BASE),
      "GET /system/updates/helper": () => json(helperView({ ready: false, ready_reason: "not_from_registry" })),
    });
    render(<UpdatesCard />);
    const state = await screen.findByTestId("helper-state");
    expect(state).toHaveAttribute("data-state", "not-ready");
    expect(state).toHaveTextContent(HELPER_CODE_TEXTS.not_from_registry);
    expect(screen.queryByRole("button", { name: /Jetzt aktualisieren/ })).not.toBeInTheDocument();
  });

  it("antwortet nicht: sagt es und was zu tun ist", async () => {
    setOwner();
    mockApi({ "GET /system/updates": () => json(BASE), "GET /system/updates/helper": () => json(ABSENT("stale")) });
    render(<UpdatesCard />);
    const state = await screen.findByTestId("helper-state");
    expect(state).toHaveAttribute("data-state", "stale");
    expect(state).toHaveTextContent("Update-Helfer: antwortet nicht.");
    expect(state).toHaveTextContent(PRESENCE_TEXTS.stale);
    expect(screen.queryByRole("button", { name: /Jetzt aktualisieren/ })).not.toBeInTheDocument();
  });

  it("Knopf, wenn alles passt: Helfer bereit, Update, offizielles Image, fertige Version, nicht fest, Owner", async () => {
    setOwner();
    mockApi({ "GET /system/updates": () => json(BASE), "GET /system/updates/helper": () => json(helperView()) });
    render(<UpdatesCard />);
    expect(await screen.findByRole("button", { name: "Jetzt aktualisieren auf 0.6.0" })).toBeInTheDocument();
    expect(screen.queryByTestId("helper-why-not")).not.toBeInTheDocument();
  });

  it.each([
    ["nicht der Owner", {}, {}, false, /nur der Inhaber/],
    ["Helfer nicht bereit", {}, { ready: false, ready_reason: "target_unhealthy" }, true, null],
    ["Helfer antwortet nicht", {}, ABSENT("stale"), true, null],
    ["kein Update verfügbar", { latest: "0.5.0", available: false }, {}, true, null],
    ["kein offizielles Image", { official_image: false, image: null }, {}, true, null],
    ["Vorabversion", { latest: "0.6.0-rc1" }, {}, true, /Vorabversionen spielt der Update-Helfer nicht ein/],
    ["Version fest eingetragen", {}, { target: { current_version: "0.5.0", floating_tag: null, pinned: true } }, true, /feste Version/],
    ["Reihe passt nicht", { latest: "0.6.0" }, { target: { current_version: "0.5.0", floating_tag: "0.5", pinned: false } }, true, /Reihe „:0\.5“, Version 0\.6\.0 kommt darüber nicht/],
  ] as [string, Partial<UpdateStatus>, Partial<HelperView>, boolean, RegExp | null][])(
    "kein Knopf: %s", async (_name, statusPatch, viewPatch, owner, why) => {
      if (owner) setOwner();
      mockApi({ "GET /system/updates": () => json({ ...BASE, ...statusPatch }), "GET /system/updates/helper": () => json(helperView(viewPatch)) });
      render(<UpdatesCard />);
      await screen.findByTestId("helper-state");
      expect(screen.queryByRole("button", { name: /Jetzt aktualisieren/ })).not.toBeInTheDocument();
      if (why) expect(screen.getByTestId("helper-why-not")).toHaveTextContent(why);
      else expect(screen.queryByTestId("helper-why-not")).not.toBeInTheDocument();
    },
  );

  it("Bestätigung: Hinweis auf die Kopie, Code erst nach totp_missing, falscher Code leert nur das Codefeld", async () => {
    setOwner();
    const answers = [
      () => json({ detail: "Bitte gib zusätzlich den Code aus deiner Authenticator-App ein.", code: "totp_missing" }, 403),
      () => json({ detail: "Der Code stimmt nicht.", code: "totp_wrong" }, 400),
      () => json({ request_id: REQUEST_ID, action: "update", from: "0.5.0", to: "0.6.0", data_revert: false }, 202),
    ];
    const calls = mockApi({
      "GET /system/updates": () => json(BASE),
      "GET /system/updates/helper": () => json(helperView({ pending: null })),
      "POST /system/updates/apply": () => answers.shift()!(),
    });
    render(<UpdatesCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Jetzt aktualisieren auf 0.6.0" }));
    const form = screen.getByRole("form", { name: "Auf Version 0.6.0 aktualisieren" });
    expect(form).toHaveTextContent("Vorher wird automatisch eine Kopie der Datenbank angelegt");
    expect(within(form).queryByLabelText(/^Zwei-Faktor-Code/)).not.toBeInTheDocument();
    const submit = within(form).getByRole("button", { name: "Jetzt aktualisieren" });
    expect(submit).toBeDisabled();

    fireEvent.change(within(form).getByLabelText("Anmeldepasswort zur Bestätigung"), { target: { value: "geheim" } });
    fireEvent.click(submit);
    expect(await within(form).findByText(/Code aus deiner Authenticator-App/)).toBeInTheDocument();
    const code = within(form).getByLabelText(/^Zwei-Faktor-Code/);
    expect(within(form).getByLabelText("Anmeldepasswort zur Bestätigung")).toHaveValue("geheim");
    expect(submit).toBeDisabled();

    fireEvent.change(code, { target: { value: "111111" } });
    fireEvent.click(submit);
    expect(await within(form).findByText("Der Code stimmt nicht.")).toBeInTheDocument();
    expect(within(form).getByLabelText(/^Zwei-Faktor-Code/)).toHaveValue("");
    expect(within(form).getByLabelText("Anmeldepasswort zur Bestätigung")).toHaveValue("geheim");

    fireEvent.change(within(form).getByLabelText(/^Zwei-Faktor-Code/), { target: { value: "222222" } });
    fireEvent.click(submit);
    expect(await screen.findByTestId("helper-progress")).toHaveTextContent("Update auf Version 0.6.0");
    expect(calls.filter((c) => c.method === "POST").map((c) => c.body)).toEqual([
      { version: "0.6.0", current_password: "geheim" },
      { version: "0.6.0", current_password: "geheim", totp_code: "111111" },
      { version: "0.6.0", current_password: "geheim", totp_code: "222222" },
    ]);
  });

  it("andere Datenbank: kein Versprechen einer Kopie", async () => {
    setOwner();
    mockApi({
      "GET /system/updates": () => json(BASE),
      "GET /system/info": () => json({ database: "postgresql", pre_update_copies: [] }),
      "GET /system/updates/helper": () => json(helperView()),
    });
    render(<UpdatesCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Jetzt aktualisieren auf 0.6.0" }));
    const form = screen.getByRole("form", { name: "Auf Version 0.6.0 aktualisieren" });
    await vi.waitFor(() => expect(form).toHaveTextContent("Bei deiner Datenbank (postgresql) legt Nodvard Deck vorher keine Kopie an"));
    expect(form).not.toHaveTextContent("Vorher wird automatisch eine Kopie");
  });

  it("Ablehnung durch das Dashboard: Satz vom Server und Grund des Helfers, das Passwort bleibt stehen", async () => {
    setOwner();
    mockApi({
      "GET /system/updates": () => json(BASE),
      "GET /system/updates/helper": () => json(helperView()),
      "POST /system/updates/apply": () => json({ detail: "Der Update-Helfer ist nicht bereit.", code: "helper_not_ready", helper_reason: "target_unhealthy" }, 409),
    });
    render(<UpdatesCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Jetzt aktualisieren auf 0.6.0" }));
    const form = screen.getByRole("form", { name: "Auf Version 0.6.0 aktualisieren" });
    fireEvent.change(within(form).getByLabelText("Anmeldepasswort zur Bestätigung"), { target: { value: "geheim" } });
    fireEvent.click(within(form).getByRole("button", { name: "Jetzt aktualisieren" }));
    const alert = await within(form).findByRole("alert");
    expect(alert).toHaveTextContent("Der Update-Helfer ist nicht bereit.");
    expect(alert).toHaveTextContent(HELPER_CODE_TEXTS.target_unhealthy);
    expect(within(form).getByLabelText("Anmeldepasswort zur Bestätigung")).toHaveValue("geheim");
  });

  it("falsches Passwort: das Feld wird geleert, kein Codefeld", async () => {
    setOwner();
    mockApi({
      "GET /system/updates": () => json(BASE),
      "GET /system/updates/helper": () => json(helperView()),
      "POST /system/updates/apply": () => json({ detail: "Das Passwort stimmt nicht." }, 400),
    });
    render(<UpdatesCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Jetzt aktualisieren auf 0.6.0" }));
    const form = screen.getByRole("form", { name: "Auf Version 0.6.0 aktualisieren" });
    fireEvent.change(within(form).getByLabelText("Anmeldepasswort zur Bestätigung"), { target: { value: "falsch" } });
    fireEvent.click(within(form).getByRole("button", { name: "Jetzt aktualisieren" }));
    expect(await within(form).findByText("Das Passwort stimmt nicht.")).toBeInTheDocument();
    expect(within(form).getByLabelText("Anmeldepasswort zur Bestätigung")).toHaveValue("");
    expect(within(form).queryByLabelText(/^Zwei-Faktor-Code/)).not.toBeInTheDocument();
  });

  it("Fortschritt: erst die Schritte des Helfers, dann /health, dann das Ergebnis und Neuladen", async () => {
    vi.useFakeTimers();
    restartTiming.pollMs = 2000;
    updaterTiming.pollMs = 3000;
    updaterTiming.timeoutMs = 30 * 60 * 1000;
    updaterTiming.reloadDelayMs = 3000;
    const assign = vi.spyOn(browserNavigation, "assign").mockImplementation(() => undefined);
    setOwner();
    let helper: () => Response = () => json(helperView());
    let health: () => Response = offline;
    mockApi({
      "GET /system/updates": () => json(BASE),
      "GET /system/updates/helper": () => helper(),
      "GET /health": () => health(),
      "POST /system/updates/apply": () => json({ request_id: REQUEST_ID, action: "update", from: "0.5.0", to: "0.6.0", data_revert: false }, 202),
    });
    render(<UpdatesCard />);
    await flush(10);
    fireEvent.click(screen.getByRole("button", { name: "Jetzt aktualisieren auf 0.6.0" }));
    fireEvent.change(screen.getByLabelText("Anmeldepasswort zur Bestätigung"), { target: { value: "geheim" } });
    helper = () => json(helperView({ pending: { id: REQUEST_ID, action: "update", to: "0.6.0", at: Math.floor(Date.now() / 1000) } }));
    fireEvent.click(screen.getByRole("button", { name: "Jetzt aktualisieren" }));
    await flush(10);
    const progress = () => screen.getByTestId("helper-progress");
    expect(progress()).toHaveAttribute("data-phase", "waiting");
    expect(progress()).toHaveTextContent("Der Auftrag ist gestellt");

    helper = () => json(busyView("begin"));
    await flush(3000);
    expect(progress()).toHaveAttribute("data-phase", "working");
    expect(progress()).toHaveTextContent(STEP_TEXTS.begin);
    expect(within(progress()).getByText("Herunterladen")).toHaveAttribute("data-state", "current");

    helper = () => json(busyView("old_stopped"));
    await flush(3000);
    expect(within(progress()).getByText("Umschalten")).toHaveAttribute("data-state", "current");
    expect(within(progress()).getByText(/Vorbereiten/)).toHaveAttribute("data-state", "done");

    // Phase 2: das Dashboard antwortet nicht mehr, gefragt wird /health.
    helper = offline;
    await flush(3000);
    expect(progress()).toHaveAttribute("data-phase", "restarting");
    expect(progress()).toHaveTextContent("wird gerade umgeschaltet");
    await flush(4000);
    expect(progress()).toHaveAttribute("data-phase", "restarting");

    health = () => json({ status: "ok", version: "0.6.0", uptime_s: 4 });
    helper = () => json(busyView("started"));
    await flush(2000);
    // Der Helfer prüft noch (Schritt bleibt „started“): Die Karte sagt, dass die neue Version schon antwortet.
    expect(progress()).toHaveAttribute("data-phase", "checking");
    expect(progress()).toHaveTextContent("Version 0.6.0 antwortet. Der Update-Helfer prüft noch");
    expect(progress()).not.toHaveTextContent(STEP_TEXTS.started);
    expect(within(progress()).getByText("Starten und prüfen")).toHaveAttribute("aria-current", "step");

    helper = () => json(helperView({ last_result: helperResult("applied") }));
    await flush(3000);
    const box = screen.getByTestId("helper-result");
    expect(box).toHaveTextContent("Update eingespielt");
    expect(box).toHaveTextContent("Version 0.6.0 läuft jetzt (vorher 0.5.0).");
    expect(box).toHaveTextContent("Die Seite lädt gleich neu");
    expect(assign).not.toHaveBeenCalled();
    await flush(3000);
    expect(assign).toHaveBeenCalledWith("/settings/system");
  });

  it("Rückbau nach dem Neustart: Ergebnis mit Grund, kein Neuladen, „Schließen“ zeigt wieder den Zustand", async () => {
    vi.useFakeTimers();
    restartTiming.pollMs = 2000;
    updaterTiming.pollMs = 3000;
    const assign = vi.spyOn(browserNavigation, "assign").mockImplementation(() => undefined);
    let helper: () => Response = () => json(busyView("started"));
    let health: () => Response = offline;
    mockApi({
      "GET /system/updates": () => json(BASE),
      "GET /system/updates/helper": () => helper(),
      "GET /health": () => health(),
    });
    render(<UpdatesCard />);
    await flush(10);
    expect(screen.getByTestId("helper-progress")).toHaveAttribute("data-phase", "working");
    helper = offline;
    await flush(3000);
    health = () => json({ status: "ok", version: "0.5.0", uptime_s: 2 });
    helper = () => json(busyView("started"));
    await flush(2000);
    // Der Helfer baut noch zurück (der Schritt bleibt „started“): nicht wieder „Die neue Version startet …“.
    expect(screen.getByTestId("helper-progress")).toHaveAttribute("data-phase", "checking");
    expect(screen.getByTestId("helper-progress")).toHaveTextContent("Nodvard Deck antwortet wieder");
    await flush(3000 * 5);
    expect(screen.getByTestId("helper-progress")).not.toHaveTextContent(STEP_TEXTS.started);
    helper = () => json(helperView({ last_result: helperResult("rolled_back", "rescue_page") }));
    await flush(3000);
    const box = screen.getByTestId("helper-result");
    expect(box).toHaveTextContent("Hat nicht geklappt, zurückgeschaltet");
    expect(box).toHaveTextContent(HELPER_CODE_TEXTS.rescue_page);
    expect(box).toHaveTextContent("0.5.0 läuft wieder");
    await flush(60_000);
    expect(assign).not.toHaveBeenCalled();
    fireEvent.click(within(box).getByRole("button", { name: "Schließen" }));
    await flush(10);
    expect(screen.queryByTestId("helper-result")).not.toBeInTheDocument();
    expect(screen.getByTestId("helper-last-result")).toHaveTextContent(outcomeTitle("rolled_back"));
  });

  it("Frist: nach 30 Minuten ohne Ergebnis sagt die Karte, wo du nachsehen kannst, und zeigt ein spätes Ergebnis", async () => {
    vi.useFakeTimers();
    updaterTiming.pollMs = 3000;
    updaterTiming.timeoutMs = 30 * 60 * 1000;
    updaterTiming.latePollMs = 30_000;
    let helper = () => json(busyView("begin"));
    mockApi({
      "GET /system/updates": () => json(BASE),
      "GET /system/updates/helper": () => helper(),
    });
    render(<UpdatesCard />);
    await flush(10);
    await flush(29 * 60 * 1000);
    expect(screen.getByTestId("helper-progress")).toBeInTheDocument();
    await flush(2 * 60 * 1000);
    const box = screen.getByTestId("helper-timeout");
    expect(box).toHaveTextContent(/Nach 3[01] Min\. noch kein Ergebnis/);
    expect(box).toHaveTextContent("fragt alle 30 Sek. weiter nach");
    expect(box).toHaveTextContent("docker compose logs updater");

    // Der Helfer brauchte länger (langsamer Download): Das Ergebnis erscheint trotzdem von selbst.
    helper = () => json(helperView({ last_result: helperResult("rolled_back", "timeout") }));
    await flush(30_000);
    expect(screen.queryByTestId("helper-timeout")).not.toBeInTheDocument();
    expect(screen.getByTestId("helper-result")).toHaveTextContent(HELPER_CODE_TEXTS.timeout);
  });

  it("Frist: „Schließen“, während der Helfer noch arbeitet, zeigt wieder den Fortschritt statt gleich wieder die Frist", async () => {
    vi.useFakeTimers();
    updaterTiming.pollMs = 3000;
    updaterTiming.timeoutMs = 30 * 60 * 1000;
    // Der Vorgang begann beim ersten Laden; `since` bleibt dabei (wie beim Helfer).
    const since = Math.floor(Date.now() / 1000);
    const view = busyView("begin");
    mockApi({
      "GET /system/updates": () => json(BASE),
      "GET /system/updates/helper": () => json({ ...view, busy: { ...view.busy!, since } }),
    });
    render(<UpdatesCard />);
    await flush(10);
    await flush(31 * 60 * 1000);
    fireEvent.click(within(screen.getByTestId("helper-timeout")).getByRole("button", { name: "Schließen" }));
    await flush(100);
    expect(screen.queryByTestId("helper-timeout")).not.toBeInTheDocument();
    expect(screen.getByTestId("helper-progress")).toHaveTextContent(STEP_TEXTS.begin);
  });

  it.each([
    ["applied", null, "Update eingespielt", "Version 0.6.0 läuft jetzt (vorher 0.5.0)."],
    ["reverted", null, "Zurück auf die vorige Version", "Version 0.5.0 läuft wieder (vorher 0.6.0)."],
    ["rolled_back", "timeout", "Hat nicht geklappt, zurückgeschaltet", HELPER_CODE_TEXTS.timeout],
    ["refused", "pull_failed", "Vom Update-Helfer abgelehnt", HELPER_CODE_TEXTS.pull_failed],
    ["aborted", null, "Abgebrochen, nichts verändert", "Es ist alles wie vorher."],
  ] as [HelperResult["outcome"], string | null, string, string][])("letzter Vorgang %s: verständlich in einer Zeile", async (outcome, code, title, text) => {
    mockApi({
      "GET /system/updates": () => json(BASE),
      "GET /system/updates/helper": () => json(helperView({ last_result: helperResult(outcome, code, outcome === "reverted" ? "rollback" : "update") })),
    });
    render(<UpdatesCard />);
    const line = await screen.findByTestId("helper-last-result");
    expect(line).toHaveTextContent(title);
    expect(line).toHaveTextContent(text);
  });

  it("Fortschritt: der aktuelle Abschnitt ist für Screenreader ausgezeichnet, das Häkchen wird nicht vorgelesen", async () => {
    vi.useFakeTimers();
    mockApi({ "GET /system/updates": () => json(BASE), "GET /system/updates/helper": () => json(busyView("created")) });
    render(<UpdatesCard />);
    await flush(10);
    const progress = screen.getByTestId("helper-progress");
    const done = within(progress).getByText("Herunterladen");
    expect(done).toHaveAttribute("data-state", "done");
    expect(done).not.toHaveAttribute("aria-current");
    expect(done.querySelector("[aria-hidden=true]")).toHaveTextContent("✓");
    expect(done.querySelector(".sr-only")).toHaveTextContent("(erledigt)");
    expect(within(progress).getByText("Vorbereiten")).toHaveAttribute("aria-current", "step");
    expect(within(progress).getByText("Umschalten")).not.toHaveAttribute("aria-current");
    // Die Seite darf zu: Der Helfer arbeitet ohne sie weiter.
    expect(progress).toHaveTextContent("Du kannst die Seite auch schließen");
    expect(progress).not.toHaveTextContent("Lass diese Seite offen");
  });

  it("anderer Tab hat gerade ein Update gestartet: nach request_pending folgt die Karte dem laufenden Vorgang", async () => {
    setOwner();
    let helper = () => json(helperView());
    mockApi({
      "GET /system/updates": () => json(BASE),
      "GET /system/updates/helper": () => helper(),
      "POST /system/updates/apply": () => json({ detail: "Es wartet schon eine Anforderung auf den Update-Helfer.", code: "request_pending" }, 409),
    });
    render(<UpdatesCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Jetzt aktualisieren auf 0.6.0" }));
    const form = screen.getByRole("form", { name: "Auf Version 0.6.0 aktualisieren" });
    fireEvent.change(within(form).getByLabelText("Anmeldepasswort zur Bestätigung"), { target: { value: "geheim" } });
    helper = () => json(helperView({ pending: { id: REQUEST_ID, action: "update", to: "0.6.0", at: Math.floor(Date.now() / 1000) } }));
    fireEvent.click(within(form).getByRole("button", { name: "Jetzt aktualisieren" }));
    expect(await screen.findByTestId("helper-progress")).toHaveTextContent("Update auf Version 0.6.0");
    expect(screen.queryByRole("form", { name: "Auf Version 0.6.0 aktualisieren" })).not.toBeInTheDocument();
  });

  it("anderer Tab: nach helper_busy liest die Karte den Zustand neu", async () => {
    setOwner();
    let helper = () => json(helperView());
    const calls = mockApi({
      "GET /system/updates": () => json(BASE),
      "GET /system/updates/helper": () => helper(),
      "POST /system/updates/apply": () => json({ detail: "Der Update-Helfer ist gerade beschäftigt.", code: "helper_busy" }, 409),
    });
    render(<UpdatesCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Jetzt aktualisieren auf 0.6.0" }));
    fireEvent.change(screen.getByLabelText("Anmeldepasswort zur Bestätigung"), { target: { value: "geheim" } });
    helper = () => json(busyView("pulled"));
    fireEvent.click(screen.getByRole("button", { name: "Jetzt aktualisieren" }));
    expect(await screen.findByTestId("helper-progress")).toHaveTextContent(STEP_TEXTS.pulled);
    expect(calls.filter((c) => c.path === "/system/updates/helper").length).toBeGreaterThanOrEqual(2);
  });

  it("selbst gebautes Image ohne Helfer: keine Einladung, den Helfer einzurichten", async () => {
    setOwner();
    mockApi({
      "GET /system/updates": () => json({ ...BASE, official_image: false, image: "nodvard-deck:pi-abc" }),
      "GET /system/updates/helper": () => json(ABSENT("missing")),
    });
    render(<UpdatesCard />);
    await screen.findByText(/selbst gebauten Image/);
    await vi.waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("/system/updates/helper"))).toBe(true));
    await act(async () => undefined);
    expect(screen.queryByTestId("helper-state")).not.toBeInTheDocument();
    expect(screen.queryByText(/Update-Helfer: nicht eingerichtet/)).not.toBeInTheDocument();
  });

  it("selbst gebautes Image mit Helfer: sein Zustand bleibt sichtbar (er sagt, warum er nicht kann)", async () => {
    setOwner();
    mockApi({
      "GET /system/updates": () => json({ ...BASE, official_image: false, image: "nodvard-deck:pi-abc" }),
      "GET /system/updates/helper": () => json(helperView({ ready: false, ready_reason: "foreign_image" })),
    });
    render(<UpdatesCard />);
    expect(await screen.findByTestId("helper-state")).toHaveTextContent(HELPER_CODE_TEXTS.foreign_image);
  });

  it("nicht eingerichtet: das Wort „Anleitung“ steht nur einmal da", async () => {
    mockApi({ "GET /system/updates": () => json(BASE), "GET /system/updates/helper": () => json(ABSENT("missing")) });
    render(<UpdatesCard />);
    const state = await screen.findByTestId("helper-state");
    expect(state.textContent?.match(/Anleitung/g)).toHaveLength(1);
  });

  it("Bestätigung: nach einem gelungenen Update geht es 7 Tage per Knopf zurück (nicht „danach“ nach dem Zurückschalten)", async () => {
    setOwner();
    mockApi({ "GET /system/updates": () => json(BASE), "GET /system/updates/helper": () => json(helperView()) });
    render(<UpdatesCard />);
    fireEvent.click(await screen.findByRole("button", { name: "Jetzt aktualisieren auf 0.6.0" }));
    const form = screen.getByRole("form", { name: "Auf Version 0.6.0 aktualisieren" });
    expect(form).toHaveTextContent("Nach einem gelungenen Update kannst du 7 Tage lang per Knopf zurück");
    expect(form).not.toHaveTextContent("Danach kannst du 7 Tage lang");
  });

  it("gerade von dieser Version zurückgegangen: kein Knopf, sondern ab wann es wieder geht", async () => {
    setOwner();
    const back = { ...helperResult("reverted", null, "rollback"), finished_at: Math.floor(Date.now() / 1000) - 3600 };
    mockApi({ "GET /system/updates": () => json(BASE), "GET /system/updates/helper": () => json(helperView({ last_result: back })) });
    render(<UpdatesCard />);
    const why = await screen.findByTestId("helper-why-not");
    expect(why).toHaveTextContent("Von Version 0.6.0 bist du vor Kurzem zurückgegangen");
    expect(why).toHaveTextContent("erst ab");
    expect(screen.queryByRole("button", { name: /Jetzt aktualisieren/ })).not.toBeInTheDocument();
  });

  it("der Rückweg ist länger als 24 Stunden her oder kam von einer anderen Version: der Knopf ist da", async () => {
    setOwner();
    const old = { ...helperResult("reverted", null, "rollback"), finished_at: Math.floor(Date.now() / 1000) - 25 * 3600 };
    mockApi({ "GET /system/updates": () => json(BASE), "GET /system/updates/helper": () => json(helperView({ last_result: old })) });
    const first = render(<UpdatesCard />);
    expect(await screen.findByRole("button", { name: "Jetzt aktualisieren auf 0.6.0" })).toBeInTheDocument();
    first.unmount();
    resetUpdateHelper();
    const other = { ...helperResult("reverted", null, "rollback"), from: "0.5.1" };
    mockApi({ "GET /system/updates": () => json(BASE), "GET /system/updates/helper": () => json(helperView({ last_result: other })) });
    render(<UpdatesCard />);
    expect(await screen.findByRole("button", { name: "Jetzt aktualisieren auf 0.6.0" })).toBeInTheDocument();
  });

  it("failed_manual von vor Tagen und der Helfer ist wieder bereit: nur noch die kurze Zeile", async () => {
    const old = { ...helperResult("failed_manual", "rollback_failed"), finished_at: Math.floor(Date.now() / 1000) - 3 * 24 * 3600 };
    mockApi({ "GET /system/updates": () => json(BASE), "GET /system/updates/helper": () => json(helperView({ last_result: old })) });
    render(<UpdatesCard />);
    const line = await screen.findByTestId("helper-last-result");
    expect(line).toHaveTextContent("Bitte von Hand nachsehen");
    expect(screen.queryByTestId("helper-result")).not.toBeInTheDocument();
  });

  it("failed_manual von vor Tagen, der Helfer ist aber nicht bereit: weiter der Kasten mit den Schritten", async () => {
    const old = { ...helperResult("failed_manual", "rollback_failed"), finished_at: Math.floor(Date.now() / 1000) - 3 * 24 * 3600 };
    mockApi({
      "GET /system/updates": () => json(BASE),
      "GET /system/updates/helper": () => json(helperView({ ready: false, ready_reason: "target_not_running", last_result: old })),
    });
    render(<UpdatesCard />);
    expect(await screen.findByTestId("helper-result")).toHaveTextContent(FAILED_MANUAL_STEPS[1]);
  });

  it("nach failed_manual steht in der Karte, was auf dem Server zu tun ist", async () => {
    mockApi({
      "GET /system/updates": () => json(BASE),
      "GET /system/updates/helper": () => json(helperView({ ready: false, ready_reason: "target_unhealthy", last_result: helperResult("failed_manual", "rollback_failed") })),
    });
    render(<UpdatesCard />);
    const box = await screen.findByTestId("helper-result");
    expect(box).toHaveAttribute("data-outcome", "failed_manual");
    expect(box).toHaveTextContent("Bitte von Hand nachsehen");
    expect(box).toHaveTextContent("Version 0.5.0, die vorher lief, nicht wieder starten");
    for (const step of FAILED_MANUAL_STEPS) expect(box).toHaveTextContent(step);
  });

  it("von außen geändert: deutlich, mit dem Hinweis, was jetzt läuft", async () => {
    mockApi({
      "GET /system/updates": () => json(BASE),
      "GET /system/updates/helper": () => json(helperView({ last_result: helperResult("external_change", "external_change") })),
    });
    render(<UpdatesCard />);
    const box = await screen.findByTestId("helper-result");
    expect(box).toHaveTextContent("Abgebrochen: von außen geändert");
    expect(box).toHaveTextContent("docker compose ps");
  });
});
