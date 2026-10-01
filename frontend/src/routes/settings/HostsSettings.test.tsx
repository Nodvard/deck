import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { checkFixture, hostFixture, mockHostsApi, reply } from "../../test/hostsMock";
import { useAuthStore } from "../../state/auth";
import { confirmDialog, promptDialog } from "../../state/dialogs";
import { HostsSettings } from "./HostsSettings";

vi.mock("../../state/dialogs", () => ({ confirmDialog: vi.fn(async () => true), promptDialog: vi.fn(async () => null) }));

function Where() {
  const location = useLocation();
  return <p data-testid="where">{location.pathname + location.search}</p>;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/settings/hosts"]}>
        <Routes>
          <Route path="/settings/hosts" element={<HostsSettings />} />
          <Route path="*" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const PI = hostFixture({ id: "h1", credential: { id: "c1", kind: "ssh_key", username: "lattice", port: 22 } });
const ZABBIX = hostFixture({
  id: "h2", name: "zabbix", display_name: "Zabbix", address: "192.168.2.83", kind: "vm", provider_ext_id: "proxmox",
  credential: { id: "c2", kind: "ssh_password", username: "admin", port: 22 },
});
const KI = hostFixture({ id: "h3", name: "ki-server", display_name: "KI-Server", address: "192.168.2.87", credential: null });

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
  });
});
afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("Server & Zugänge: Liste", () => {
  it("zeigt jeden Server mit Namen, Adresse und der Art seines Zugangs", async () => {
    mockHostsApi({ "GET /hosts": [PI, ZABBIX, KI], "GET /host-groups": [] });
    renderPage();
    expect(await screen.findByText("Bastel-Pi")).toBeInTheDocument();
    expect(screen.getByText("bastel-pi · 192.168.2.72")).toBeInTheDocument();
    expect(screen.getByText("Schlüssel · lattice")).toBeInTheDocument();
    expect(screen.getByText("Passwort · admin")).toBeInTheDocument();
    expect(screen.getByText("Kein Zugang")).toBeInTheDocument();
    // Von Proxmox eingelesen, nicht von Hand angelegt.
    expect(screen.getAllByText("Automatisch eingelesen")).toHaveLength(1);
    expect(screen.getAllByRole("link", { name: "Einrichten" })[0]).toHaveAttribute("href", "/settings/hosts/h1");
    // Bei wenigen Servern braucht es keine Suche.
    expect(screen.queryByPlaceholderText("Server suchen …")).not.toBeInTheDocument();
  });

  it("Suchfeld erst bei mehr als acht Servern, und es filtert", async () => {
    const many = Array.from({ length: 9 }, (_, i) => hostFixture({ id: `x${i}`, name: `srv-${i}`, display_name: `Server ${i}`, address: `10.0.0.${i}` }));
    mockHostsApi({ "GET /hosts": many, "GET /host-groups": [] });
    renderPage();
    const search = await screen.findByPlaceholderText("Server suchen …");
    fireEvent.change(search, { target: { value: "server 3" } });
    expect(screen.getByText("Server 3")).toBeInTheDocument();
    expect(screen.queryByText("Server 4")).not.toBeInTheDocument();
  });

  it("Prüfen zeigt das Ergebnis kurz in der Zeile", async () => {
    const calls = mockHostsApi({
      "GET /hosts": [PI], "GET /host-groups": [],
      "POST /hosts/h1/check": checkFixture(),
    });
    renderPage();
    const row = (await screen.findByText("Bastel-Pi")).closest("li") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Prüfen" }));
    expect(await within(row).findByText("4 von 5 in Ordnung")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/hosts/h1/check")).toBe(true);
  });

  it("Prüfen: bei einem Fehler steht der Text des Servers da (z. B. zu viele Prüfungen)", async () => {
    mockHostsApi({
      "GET /hosts": [PI], "GET /host-groups": [],
      "POST /hosts/h1/check": reply(429, { detail: "Zu viele Prüfungen. Bitte in 3 Minuten erneut versuchen." }),
    });
    renderPage();
    const row = (await screen.findByText("Bastel-Pi")).closest("li") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Prüfen" }));
    expect(await within(row).findByText("Zu viele Prüfungen. Bitte in 3 Minuten erneut versuchen.")).toBeInTheDocument();
  });
});

describe("Server & Zugänge: leerer Zustand", () => {
  it("erklärt freundlich, wie es losgeht, mit Knopf und Link zu Proxmox", async () => {
    mockHostsApi({ "GET /hosts": [], "GET /host-groups": [] });
    renderPage();
    expect(await screen.findByText(/Noch keine Server\./)).toBeInTheDocument();
    expect(screen.getByText(/zum Beispiel den Raspberry Pi/)).toBeInTheDocument();
    // Der Knopf oben und der im leeren Zustand öffnen dasselbe Formular.
    expect(screen.getAllByRole("button", { name: "Server hinzufügen" }).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByRole("link", { name: "Proxmox einrichten" })).toHaveAttribute("href", "/settings/extensions/proxmox");
  });

  it("bietet „Mit Beispieldaten ansehen“ an, aber nur mit hosts.write und settings.write", async () => {
    mockHostsApi({ "GET /hosts": [], "GET /host-groups": [] });
    const first = renderPage();
    expect(await screen.findByRole("button", { name: "Mit Beispieldaten ansehen" })).toBeInTheDocument();
    first.unmount();

    useAuthStore.setState({
      user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: false, locale: "de", permissions: ["hosts.read", "hosts.write"] },
    });
    renderPage();
    await screen.findByText(/Noch keine Server\./);
    expect(screen.queryByRole("button", { name: "Mit Beispieldaten ansehen" })).toBeNull();
  });
});

describe("Server & Zugänge: Erreichbarkeit", () => {
  it("zeigt die Karte „Erreichbarkeit prüfen“ mit dem Recht settings.write", async () => {
    mockHostsApi({ "GET /hosts": [PI], "GET /host-groups": [], "GET /settings": [] });
    renderPage();
    expect(await screen.findByRole("switch", { name: "Erreichbarkeit regelmäßig prüfen" })).toBeInTheDocument();
  });

  it("lässt die Karte ohne settings.write weg", async () => {
    useAuthStore.setState({
      user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: false, locale: "de", permissions: ["hosts.read", "hosts.write"] },
    });
    const calls = mockHostsApi({ "GET /hosts": [PI], "GET /host-groups": [] });
    renderPage();
    await screen.findByText("Bastel-Pi");
    expect(screen.queryByText("Erreichbarkeit prüfen")).toBeNull();
    expect(calls.some((c) => c.path === "/settings")).toBe(false);
  });
});

describe("Server & Zugänge: Server anlegen", () => {
  it("schickt Kleinbuchstaben und Markierungen als Liste und öffnet danach den Zugang des neuen Servers", async () => {
    const calls = mockHostsApi({
      "GET /hosts": [], "GET /host-groups": [],
      "POST /hosts": (c: { body: unknown }) => hostFixture({ id: "new1", ...(c.body as object) }),
    });
    renderPage();
    fireEvent.click((await screen.findAllByRole("button", { name: "Server hinzufügen" }))[0]);
    fireEvent.change(screen.getByLabelText("Kurzname"), { target: { value: " Bastel-Pi " } });
    fireEvent.change(screen.getByLabelText("Adresse (IP oder Name)"), { target: { value: "192.168.2.72" } });
    fireEvent.change(screen.getByLabelText("Markierungen"), { target: { value: "docker, Web  nas" } });
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/settings/hosts/new1?neu=1"));
    const post = calls.find((c) => c.method === "POST" && c.path === "/hosts");
    expect(post?.body).toEqual({
      name: "bastel-pi", display_name: "bastel-pi", address: "192.168.2.72", os_family: "linux", tags: ["docker", "web", "nas"],
    });
  });

  it("zeigt die deutschen Meldungen des Backends direkt am Feld", async () => {
    mockHostsApi({
      "GET /hosts": [], "GET /host-groups": [],
      "POST /hosts": reply(422, {
        detail: [
          { type: "value_error", loc: ["body", "name"], msg: "Kurzname: nur Kleinbuchstaben, Ziffern, - und _ (höchstens 64 Zeichen)." },
          { type: "value_error", loc: ["body", "address"], msg: "Adresse: nur IP-Adresse oder Rechnername, ohne http:// und ohne Port." },
        ],
      }),
    });
    renderPage();
    fireEvent.click((await screen.findAllByRole("button", { name: "Server hinzufügen" }))[0]);
    fireEvent.change(screen.getByLabelText("Kurzname"), { target: { value: "Mein Server!" } });
    fireEvent.change(screen.getByLabelText("Adresse (IP oder Name)"), { target: { value: "http://x:22" } });
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));
    expect(await screen.findByText("Kurzname: nur Kleinbuchstaben, Ziffern, - und _ (höchstens 64 Zeichen).")).toBeInTheDocument();
    expect(screen.getByText("Adresse: nur IP-Adresse oder Rechnername, ohne http:// und ohne Port.")).toBeInTheDocument();
    // Wir bleiben auf der Seite, die Eingaben bleiben stehen.
    expect(screen.queryByTestId("where")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Kurzname")).toHaveValue("Mein Server!");
  });

  it("ein schon vergebener Kurzname (409) steht als Meldung da", async () => {
    mockHostsApi({
      "GET /hosts": [], "GET /host-groups": [],
      "POST /hosts": reply(409, { detail: "Ein Host mit diesem Namen existiert bereits." }),
    });
    renderPage();
    fireEvent.click((await screen.findAllByRole("button", { name: "Server hinzufügen" }))[0]);
    fireEvent.change(screen.getByLabelText("Kurzname"), { target: { value: "pi" } });
    fireEvent.change(screen.getByLabelText("Adresse (IP oder Name)"), { target: { value: "10.0.0.1" } });
    fireEvent.click(screen.getByRole("button", { name: "Anlegen" }));
    expect(await screen.findByText("Ein Host mit diesem Namen existiert bereits.")).toBeInTheDocument();
  });
});

describe("Server & Zugänge: Gruppen", () => {
  it("zeigt Gruppen, legt neue an, benennt um und löscht mit Rückfrage", async () => {
    let groups = [{ id: "g1", name: "docker-server", description: "" }];
    const calls = mockHostsApi({
      "GET /hosts": [PI], "GET /host-groups": () => groups,
      "POST /host-groups": (c: { body: unknown }) => { groups = [...groups, { id: "g2", description: "", ...(c.body as { name: string }) }]; return groups[1]; },
      "PATCH /host-groups/g1": (c: { body: unknown }) => { groups = [{ ...groups[0], ...(c.body as object) }]; return groups[0]; },
      "DELETE /host-groups/g1": () => undefined,
    });
    vi.mocked(promptDialog).mockResolvedValueOnce("alle-docker");
    renderPage();
    const card = (await screen.findByText("docker-server")).closest("section") as HTMLElement;

    fireEvent.change(within(card).getByLabelText("Neue Gruppe"), { target: { value: "proxmox" } });
    fireEvent.click(within(card).getByRole("button", { name: "Gruppe anlegen" }));
    expect(await within(card).findByText("proxmox")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.path === "/host-groups")?.body).toEqual({ name: "proxmox" });

    fireEvent.click(within(card).getAllByRole("button", { name: "Umbenennen" })[0]);
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ name: "alle-docker" }));
    expect(await within(card).findByText("alle-docker")).toBeInTheDocument();

    fireEvent.click(within(card).getAllByRole("button", { name: "Löschen" })[0]);
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/host-groups/g1")).toBe(true));
    expect(vi.mocked(confirmDialog).mock.calls[0][0]).toMatch(/Gruppe „alle-docker“ löschen\?.*Server darin bleiben/s);
  });

  it("ohne Gruppen steht „Noch keine Gruppen.“ da", async () => {
    mockHostsApi({ "GET /hosts": [PI], "GET /host-groups": [] });
    renderPage();
    expect(await screen.findByText("Noch keine Gruppen.")).toBeInTheDocument();
  });
});
