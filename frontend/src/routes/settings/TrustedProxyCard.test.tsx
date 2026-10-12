import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { mockHostsApi, reply, type Call } from "../../test/hostsMock";
import { useAuthStore } from "../../state/auth";
import { SystemSettings } from "./SystemSettings";
import {
  TrustedProxyCard,
  connectionLine,
  parseEntries,
  wideNetworkWarning,
  wideNetworks,
  type ConnectionInfo,
} from "./TrustedProxyCard";

const FIELD = "Vertrauenswürdiger Proxy";

function setUser(permissions: string[], isOwner = true) {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: isOwner, locale: "de", permissions },
  });
}

function connection(over: Partial<ConnectionInfo> = {}): ConnectionInfo {
  return {
    peer: "172.18.0.5", client: "172.18.0.5", scheme: "http", via_trusted_proxy: false, forwarded_headers: false,
    trusted_proxies: [], trusted_proxies_source: "setting", ...over,
  };
}

const rows = (value: unknown = []) => [{ key: "system.trusted_proxies", value }];

beforeEach(() => setUser(["*"]));
afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("Karte „Vertrauenswürdiger Proxy“", () => {
  it("zeigt die gespeicherte Liste und eine direkte Anfrage", async () => {
    mockHostsApi({ "GET /system/connection": connection({ peer: "192.168.2.40", client: "192.168.2.40" }) });
    render(<TrustedProxyCard settings={rows(["172.18.0.5", "fd00:1::/64"])} />);
    expect(await screen.findByText("Deine Anfrage kommt gerade von 192.168.2.40 (direkt) über http.")).toBeInTheDocument();
    expect(screen.getByLabelText(FIELD)).toHaveValue("172.18.0.5\nfd00:1::/64");
    expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Diese Adresse eintragen" })).not.toBeInTheDocument();
  });

  it("zeigt die echte Adresse und den Proxy, wenn er eingetragen ist", async () => {
    mockHostsApi({
      "GET /system/connection": connection({ client: "192.168.2.40", scheme: "https", via_trusted_proxy: true, forwarded_headers: true, trusted_proxies: ["172.18.0.5"] }),
    });
    render(<TrustedProxyCard settings={rows(["172.18.0.5"])} />);
    expect(await screen.findByText("Deine Anfrage kommt gerade von 192.168.2.40 über https, weitergeleitet von 172.18.0.5.")).toBeInTheDocument();
  });

  it("schlägt den Proxy vor, trägt ihn ein, speichert und prüft die Verbindung neu", async () => {
    let trusted = false;
    const calls = mockHostsApi({
      "GET /system/connection": () =>
        trusted
          ? connection({ client: "192.168.2.40", scheme: "https", via_trusted_proxy: true, forwarded_headers: true, trusted_proxies: ["172.18.0.5"] })
          : connection({ forwarded_headers: true }),
      "PUT /settings/system.trusted_proxies": (call: Call) => {
        trusted = true;
        return { key: "system.trusted_proxies", value: (call.body as { value: string[] }).value };
      },
    });
    render(<TrustedProxyCard settings={rows()} />);
    expect(await screen.findByText("Deine Anfrage kommt gerade von 172.18.0.5 (direkt) über http.")).toBeInTheDocument();
    expect(screen.getByText(/über einen Proxy mit der Adresse 172.18.0.5/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Diese Adresse eintragen" }));
    expect(screen.getByLabelText(FIELD)).toHaveValue("172.18.0.5");
    expect(screen.getByText("Eingetragen, noch nicht gespeichert.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByText(/^Gespeichert\./)).toBeInTheDocument();
    expect(await screen.findByText("Deine Anfrage kommt gerade von 192.168.2.40 über https, weitergeleitet von 172.18.0.5.")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "PUT").map((c) => [c.path, c.body])).toEqual([
      ["/settings/system.trusted_proxies", { value: ["172.18.0.5"] }],
    ]);
    expect(calls.filter((c) => c.path === "/system/connection")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Diese Adresse eintragen" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
  });

  it("übernimmt die Schreibweise des Servers und zeigt seine Fehler", async () => {
    let first = true;
    mockHostsApi({
      "GET /system/connection": connection(),
      "PUT /settings/system.trusted_proxies": () => {
        if (first) {
          first = false;
          return reply(422, { detail: "system.trusted_proxies: „0.0.0.0/0“ ist zu groß." });
        }
        return { key: "system.trusted_proxies", value: ["172.18.0.6"] };
      },
    });
    render(<TrustedProxyCard settings={rows()} />);
    const field = await screen.findByLabelText(FIELD);
    fireEvent.change(field, { target: { value: "0.0.0.0/0" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("ist zu groß");
    expect(screen.getByRole("button", { name: "Speichern" })).toBeEnabled();

    fireEvent.change(field, { target: { value: "::ffff:172.18.0.6" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await screen.findByText(/^Gespeichert\./);
    expect(field).toHaveValue("172.18.0.6");
  });

  it("zeigt eine Liste aus der Umgebungsvariable nur an", async () => {
    mockHostsApi({
      "GET /system/connection": connection({ forwarded_headers: true, trusted_proxies: ["172.18.0.9"], trusted_proxies_source: "env" }),
    });
    render(<TrustedProxyCard settings={rows(["172.18.0.9"])} />);
    expect(await screen.findByText(/Umgebungsvariable NODVARD_DECK_TRUSTED_PROXIES/)).toBeInTheDocument();
    expect(screen.getByLabelText(FIELD)).toBeDisabled();
    expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Diese Adresse eintragen" })).not.toBeInTheDocument();
  });

  it("ein Admin, der nicht Inhaber ist, sieht die Liste nur", async () => {
    setUser(["*"], false);
    mockHostsApi({ "GET /system/connection": connection({ forwarded_headers: true }) });
    render(<TrustedProxyCard settings={rows(["172.18.0.9"])} />);
    expect(await screen.findByText("Ändern kann diese Liste nur der Inhaber (Owner) dieser Installation.")).toBeInTheDocument();
    expect(screen.getByLabelText(FIELD)).toBeDisabled();
    expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Diese Adresse eintragen" })).not.toBeInTheDocument();
  });

  it("bleibt bedienbar, wenn sich die Verbindung nicht prüfen lässt", async () => {
    mockHostsApi({ "GET /system/connection": reply(500, { detail: "kaputt" }) });
    render(<TrustedProxyCard settings={rows()} />);
    expect(await screen.findByText(/ließ sich nicht prüfen/)).toBeInTheDocument();
    expect(screen.getByLabelText(FIELD)).toBeEnabled();
  });

  it("warnt bei einem Netz mit mehr als 16 Adressen, auch in privaten Netzen, und lässt das Speichern zu", async () => {
    const calls = mockHostsApi({
      "GET /system/connection": connection(),
      "PUT /settings/system.trusted_proxies": (call: Call) => ({
        key: "system.trusted_proxies",
        value: (call.body as { value: string[] }).value,
      }),
    });
    render(<TrustedProxyCard settings={rows(["172.18.0.5"])} />);
    const field = await screen.findByLabelText(FIELD);
    expect(screen.queryByTestId("wide-network-warning")).not.toBeInTheDocument();

    fireEvent.change(field, { target: { value: "172.18.0.5\n172.18.0.0/16\nfd00:1:2:3::/64" } });
    const warning = await screen.findByTestId("wide-network-warning");
    expect(warning).toHaveAttribute("role", "note");
    expect(warning).toHaveTextContent(
      "„172.18.0.0/16“ umfasst mehr als 16 Adressen. Jedes Gerät darin könnte Nodvard Deck eine falsche Adresse vorgeben. Trag besser nur die Adresse deines Proxys ein.",
    );
    expect(warning).toHaveTextContent("„fd00:1:2:3::/64“ umfasst mehr als 16 Adressen.");
    expect(warning).not.toHaveTextContent("„172.18.0.5“");
    expect(warning.className).toContain("border-amber-500/30");

    // Speichern bleibt möglich.
    const save = screen.getByRole("button", { name: "Speichern" });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await screen.findByText(/^Gespeichert\./);
    expect(calls.filter((c) => c.method === "PUT").map((c) => c.body)).toEqual([
      { value: ["172.18.0.5", "172.18.0.0/16", "fd00:1:2:3::/64"] },
    ]);
    expect(screen.getByTestId("wide-network-warning")).toBeInTheDocument();

    // Nur noch kleine Netze und Adressen: keine Warnung.
    fireEvent.change(field, { target: { value: "172.18.0.0/28\nfd00:1:2:3::/124\n10.0.0.7/32" } });
    expect(screen.queryByTestId("wide-network-warning")).not.toBeInTheDocument();
  });

  it("warnt auch bei einer Liste aus der Umgebungsvariable", async () => {
    mockHostsApi({
      "GET /system/connection": connection({ trusted_proxies: ["10.0.0.0/8"], trusted_proxies_source: "env" }),
    });
    render(<TrustedProxyCard settings={rows(["10.0.0.0/8"])} />);
    expect(await screen.findByTestId("wide-network-warning")).toHaveTextContent("„10.0.0.0/8“ umfasst mehr als 16 Adressen.");
  });

  it("Hilfsfunktionen: Netze mit mehr als 16 Adressen", () => {
    // IPv4: /28 sind genau 16 Adressen (keine Warnung), /27 sind 32.
    expect(wideNetworks(["10.0.0.0/27", "10.0.0.0/28", "10.0.0.5/32", "10.0.0.5", "10.0.0.0/8", "0.0.0.0/0"])).toEqual([
      "10.0.0.0/27", "10.0.0.0/8", "0.0.0.0/0",
    ]);
    // IPv6: /124 sind genau 16 Adressen, /123 sind 32; IPv4 in IPv6-Schreibweise zählt wie IPv4 (96 + Präfix).
    expect(wideNetworks(["fd00::/123", "fd00::/124", "fd00::5/128", "fd00::5", "fd00:1::/64", "::ffff:172.18.0.0/112", "::ffff:172.18.0.0/124"])).toEqual([
      "fd00::/123", "fd00:1::/64", "::ffff:172.18.0.0/112",
    ]);
    // Unlesbares und Doppeltes.
    expect(wideNetworks(["proxy.local/8", "10.0.0.0/abc", "10.0.0.0/33", "fd00::/129", "/8", "10.0.0.0/", "10.0.0.0/8", "10.0.0.0/8"])).toEqual(["10.0.0.0/8"]);
    expect(wideNetworks([])).toEqual([]);
    expect(wideNetworkWarning("10.0.0.0/8")).toBe(
      "„10.0.0.0/8“ umfasst mehr als 16 Adressen. Jedes Gerät darin könnte Nodvard Deck eine falsche Adresse vorgeben. Trag besser nur die Adresse deines Proxys ein.",
    );
  });

  it("Hilfsfunktionen: Liste aus dem Feld und Text der Zeile", () => {
    expect(parseEntries(" 172.18.0.5\n\n192.168.2.0/24, fd00::1 ")).toEqual(["172.18.0.5", "192.168.2.0/24", "fd00::1"]);
    expect(connectionLine(connection({ via_trusted_proxy: true }))).toBe("Deine Anfrage kommt gerade über deinen Proxy 172.18.0.5 über http.");
    expect(connectionLine(connection({ peer: null, client: null, scheme: "https" }))).toBe(
      "Deine Anfrage kommt gerade über eine Verbindung ohne IP-Adresse über https.",
    );
  });
});

describe("Reiter System", () => {
  it("zeigt die Karte mit settings.write und system.read", async () => {
    setUser(["settings.write", "system.read"], false);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input).replace(/^\/api\/v1/, "").split("?")[0];
      if (path === "/settings") return new Response(JSON.stringify(rows()), { status: 200 });
      if (path === "/system/connection") return new Response(JSON.stringify(connection()), { status: 200 });
      return new Response(JSON.stringify({}), { status: 500 });
    }));
    render(<SystemSettings />);
    expect(await screen.findByText("Vertrauenswürdiger Proxy", { selector: "h3" })).toBeInTheDocument();
    expect(await screen.findByText("Deine Anfrage kommt gerade von 172.18.0.5 (direkt) über http.")).toBeInTheDocument();
  });

  it("ohne system.read gibt es die Karte nicht, und niemand fragt die Verbindung ab", async () => {
    setUser(["settings.write"], false);
    const calls = mockHostsApi({ "GET /settings": rows() });
    render(<SystemSettings />);
    await screen.findByText("Zeit & Protokoll");
    await waitFor(() => expect(calls.some((c) => c.path === "/settings")).toBe(true));
    expect(screen.queryByText("Vertrauenswürdiger Proxy", { selector: "h3" })).not.toBeInTheDocument();
    expect(calls.some((c) => c.path === "/system/connection")).toBe(false);
  });
});
