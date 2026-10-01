import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { COPY_FAILED_TEXT } from "../../lib/clipboard";
import {
  checkFixture, credentialFixture, FINGERPRINT, hostFixture, mockHostsApi, OLD_FINGERPRINT, reply, type Call,
} from "../../test/hostsMock";
import { useAuthStore } from "../../state/auth";
import { confirmDialog, promptDialog } from "../../state/dialogs";
import { HostDetailSettings } from "./HostDetailSettings";

vi.mock("../../state/dialogs", () => ({ confirmDialog: vi.fn(async () => true), promptDialog: vi.fn(async () => null) }));

const ONE_LINER = "S=; [ \"$(id -u)\" = 0 ] || S=sudo; $S sh -c 'set -e; U=lattice'";

const REQUIREMENTS = [
  { ext_id: "nexus-soc", id: "root", label: "Root-Rechte (Nodvard Shield)", check_command: null, ok_text: "", fail_hint: "", unix_group: null, needs_root: true, root_reason: "Updates einspielen, Quarantäne", order: 50 },
  { ext_id: "service-matrix", id: "docker-group", label: "Docker ohne sudo (Service-Matrix)", check_command: "docker ps -q", ok_text: "", fail_hint: "", unix_group: "docker", needs_root: false, root_reason: null, order: 100 },
];

function setupFixture(call: Call) {
  const q = new URLSearchParams(call.query);
  return {
    username: "lattice", public_key: "ssh-ed25519 AAAA lattice@bastel-pi", fingerprint: "SHA256:xyz",
    one_liner: ONE_LINER + (q.get("sudo") === "true" ? " #sudo" : "") + (q.getAll("groups").length ? ` #${q.getAll("groups").join(",")}` : ""),
    script: "set -e\nU=lattice", notes: ["Wer in der Gruppe docker ist oder sudo ohne Passwort darf, kann auf dem Server praktisch alles."],
    groups: q.getAll("groups"), sudo: q.get("sudo") === "true",
  };
}

function Marker() {
  const location = useLocation();
  return <p data-testid="where">{location.pathname + location.search}</p>;
}

function renderDetail(initial = "/settings/hosts/h1") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route path="/settings/hosts/:hostId" element={<HostDetailSettings />} />
          <Route path="*" element={<Marker />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { client, ...view };
}

/** Grundausstattung der Abfragen; `over` gewinnt. */
function routes(over: Record<string, unknown> = {}) {
  return {
    "GET /hosts/h1": hostFixture(),
    "GET /hosts/h1/credentials": [],
    "GET /hosts/h1/known-hosts": [],
    "GET /hosts/h1/requirements": REQUIREMENTS,
    "GET /host-groups": [],
    "GET /hosts/h1/credentials/c1/setup": setupFixture,
    ...over,
  };
}

async function findCard(title: string): Promise<HTMLElement> {
  return (await screen.findByRole("heading", { name: title, level: 3 })).closest("section") as HTMLElement;
}

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
  });
});
afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("Server-Detail: SSH-Schlüssel erzeugen und Einzeiler kopieren", () => {
  it("nach „Server anlegen“ (?neu=1) ist das Formular zum Erzeugen schon offen, Standard lattice / 22", async () => {
    mockHostsApi(routes());
    renderDetail("/settings/hosts/h1?neu=1");
    expect(await screen.findByLabelText("Benutzer auf dem Server")).toHaveValue("lattice");
    expect(screen.getByLabelText("SSH-Port")).toHaveValue("22");
    expect(screen.getByText(/Bei Proxmox-Knoten „root“ nehmen/)).toBeInTheDocument();
  });

  it("erzeugt den Schlüssel, zeigt den Einzeiler und kopiert ihn", async () => {
    let creds: unknown[] = [];
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    const calls = mockHostsApi(routes({
      "GET /hosts/h1/credentials": () => creds,
      "POST /hosts/h1/credentials/generate-key": () => {
        creds = [credentialFixture()];
        return { credential: credentialFixture(), public_key: "ssh-ed25519 AAAA lattice@bastel-pi", fingerprint: "SHA256:xyz" };
      },
    }));
    renderDetail();
    const access = await screen.findByRole("heading", { name: "SSH-Zugang", level: 3 });
    const panel = access.closest("section") as HTMLElement;
    expect(await within(panel).findByText(/Noch kein Zugang hinterlegt/)).toBeInTheDocument();
    fireEvent.click(within(panel).getByRole("button", { name: "SSH-Schlüssel erzeugen" }));
    fireEvent.click(within(panel).getByRole("button", { name: "Schlüssel erzeugen" }));

    const area = await screen.findByLabelText("Einrichtungsbefehl");
    // Vorbelegt: root-Rechte (Nodvard Shield verlangt sie) und die Gruppe docker (Service-Matrix).
    await waitFor(() => expect(area).toHaveValue(`${ONE_LINER} #sudo #docker`));
    expect(calls.find((c) => c.path === "/hosts/h1/credentials/generate-key")?.body).toEqual({ username: "lattice", port: 22 });
    const setupCall = calls.filter((c) => c.path === "/hosts/h1/credentials/c1/setup").at(-1);
    expect(setupCall?.query).toBe("sudo=true&groups=docker");
    expect(screen.getByText(/Das ist praktisch root/)).toBeInTheDocument();
    expect(screen.getByText(/nötig für: Updates einspielen, Quarantäne/)).toBeInTheDocument();
    expect(screen.getByText(/nötig für: Docker ohne sudo \(Service-Matrix\)/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Kopieren" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(`${ONE_LINER} #sudo #docker`));
    expect(await screen.findByRole("button", { name: "Kopiert" })).toBeInTheDocument();
  });

  it("die Haken laden den Befehl neu: ohne root-Rechte sudo=false, ohne Gruppe keine groups", async () => {
    const calls = mockHostsApi(routes({ "GET /hosts/h1/credentials": [credentialFixture()] }));
    renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Einrichtungsbefehl zeigen" }));
    const area = await screen.findByLabelText("Einrichtungsbefehl");
    await waitFor(() => expect(area).toHaveValue(`${ONE_LINER} #sudo #docker`));

    fireEvent.click(screen.getByRole("checkbox", { name: /Root-Rechte ohne Passwort/ }));
    await waitFor(() => expect(screen.getByLabelText("Einrichtungsbefehl")).toHaveValue(`${ONE_LINER} #docker`));
    fireEvent.click(screen.getByRole("checkbox", { name: /Zur Gruppe „docker“ hinzufügen/ }));
    await waitFor(() => expect(screen.getByLabelText("Einrichtungsbefehl")).toHaveValue(ONE_LINER));
    const queries = calls.filter((c) => c.path === "/hosts/h1/credentials/c1/setup").map((c) => c.query);
    expect(queries).toContain("sudo=false&groups=docker");
    expect(queries).toContain("sudo=false");
    // Ohne sudo und ohne Gruppe entfällt der „praktisch root“-Hinweis.
    expect(screen.queryByText(/Das ist praktisch root/)).not.toBeInTheDocument();
  });

  it("ohne Zwischenablage (http) und wenn auch das Notkopieren scheitert, steht der Hinweis da", async () => {
    vi.stubGlobal("navigator", {});
    (document as unknown as { execCommand: unknown }).execCommand = vi.fn(() => false);
    mockHostsApi(routes({ "GET /hosts/h1/credentials": [credentialFixture()] }));
    renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Einrichtungsbefehl zeigen" }));
    await screen.findByLabelText("Einrichtungsbefehl");
    fireEvent.click(screen.getByRole("button", { name: "Kopieren" }));
    expect(await screen.findByText(COPY_FAILED_TEXT)).toBeInTheDocument();
  });

  it("für root gibt es keine Haken (er braucht kein sudo und keine Gruppe)", async () => {
    mockHostsApi(routes({
      "GET /hosts/h1/credentials": [credentialFixture({ username: "root" })],
    }));
    renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Einrichtungsbefehl zeigen" }));
    await screen.findByLabelText("Einrichtungsbefehl");
    expect(screen.queryByRole("checkbox", { name: /Root-Rechte/ })).not.toBeInTheDocument();
  });
});

describe("Server-Detail: Verbindung prüfen", () => {
  const withCred = () => routes({ "GET /hosts/h1": hostFixture({ credential: { id: "c1", kind: "ssh_key", username: "lattice", port: 22 } }), "GET /hosts/h1/credentials": [credentialFixture()] });

  it("ok: zeigt jeden Punkt mit Symbol, Text und Hinweis, Befehle zum Kopieren", async () => {
    const calls = mockHostsApi({ ...withCred(), "POST /hosts/h1/check": checkFixture() });
    renderDetail();
    const check = await findCard("Verbindung prüfen");
    fireEvent.click(within(check).getByRole("button", { name: "Verbindung prüfen" }));
    const list = await within(check).findByRole("list", { name: "Ergebnis der Prüfung" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(5);
    expect(within(list).getByText("Anmeldung als lattice")).toBeInTheDocument();
    expect(within(list).getByText("sudo verlangt ein Passwort.")).toBeInTheDocument();
    expect(within(list).getAllByLabelText("in Ordnung")).toHaveLength(4);
    expect(within(list).getByLabelText("Hinweis")).toBeInTheDocument();
    expect(within(check).getByText(/4 von 5 in Ordnung/)).toBeInTheDocument();
    // Der Standard-Zugang wird geprüft: kein credential_id im Body.
    expect(calls.find((c) => c.path === "/hosts/h1/check")?.body).toBeUndefined();
  });

  it("Fehler: zeigt den Grund und den Befehl aus dem Hinweis mit Kopierknopf", async () => {
    mockHostsApi({
      ...withCred(),
      "POST /hosts/h1/check": checkFixture({
        ok: false, host_key: null,
        items: [{
          id: "reachable", label: "Server erreichbar", status: "fail", detail: "Der Server lehnt Verbindungen auf Port 22 ab.",
          hint: "Läuft dort SSH? Debian/Raspberry Pi OS: „sudo apt install openssh-server“ und „sudo systemctl enable --now ssh“.",
        }],
      }),
    });
    renderDetail();
    const check = await findCard("Verbindung prüfen");
    fireEvent.click(within(check).getByRole("button", { name: "Verbindung prüfen" }));
    expect(await within(check).findByText("Der Server lehnt Verbindungen auf Port 22 ab.")).toBeInTheDocument();
    expect(within(check).getByLabelText("Fehler")).toBeInTheDocument();
    expect(within(check).getByText("sudo apt install openssh-server").tagName).toBe("CODE");
    expect(within(check).getByText("sudo systemctl enable --now ssh").tagName).toBe("CODE");
    expect(within(check).getAllByRole("button", { name: "Kopieren" })).toHaveLength(2);
  });

  it("zu viele Prüfungen (429): der Text des Servers erscheint", async () => {
    mockHostsApi({
      ...withCred(),
      "POST /hosts/h1/check": reply(429, { detail: "Zu viele Prüfungen. Bitte in 4 Minuten erneut versuchen." }),
    });
    renderDetail();
    const check = await findCard("Verbindung prüfen");
    fireEvent.click(within(check).getByRole("button", { name: "Verbindung prüfen" }));
    expect(await within(check).findByText("Zu viele Prüfungen. Bitte in 4 Minuten erneut versuchen.")).toBeInTheDocument();
  });

  const newKey = checkFixture({
    ok: false, host_key: { status: "new", key_type: "ssh-ed25519", fingerprint: FINGERPRINT, expected: null },
    items: [
      { id: "reachable", label: "Server erreichbar", status: "ok", detail: "SSH-Dienst antwortet.", hint: "" },
      {
        id: "host_key", label: "Server-Schlüssel", status: "confirm",
        detail: `Nodvard Deck kennt diesen Server noch nicht. Fingerabdruck: ${FINGERPRINT}`,
        hint: "Zum Vergleichen auf dem Server: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub",
      },
    ],
  });

  it("neuer Schlüssel: Fingerabdruck, Vergleichsbefehl und „bestätigen“ – danach wird erneut geprüft", async () => {
    let round = 0;
    const calls = mockHostsApi({
      ...withCred(),
      "POST /hosts/h1/check": () => (round++ === 0 ? newKey : checkFixture()),
      "POST /hosts/h1/known-hosts": { key_type: "ssh-ed25519", fingerprint: FINGERPRINT, first_seen_at: "2026-09-30T10:00:00Z", accepted_by_user_id: "u1", accepted_by_label: "nico" },
    });
    renderDetail();
    const check = await findCard("Verbindung prüfen");
    fireEvent.click(within(check).getByRole("button", { name: "Verbindung prüfen" }));
    expect(await within(check).findByText(FINGERPRINT)).toBeInTheDocument();
    expect(within(check).getByText("ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub").tagName).toBe("CODE");
    // Die Zeile darüber nennt den Fingerabdruck nicht noch einmal.
    expect(within(check).getByText("Nodvard Deck kennt diesen Server noch nicht.")).toBeInTheDocument();
    expect(within(check).getByLabelText("wartet auf Bestätigung")).toBeInTheDocument();

    fireEvent.click(within(check).getByRole("button", { name: /Fingerabdruck stimmt – bestätigen/ }));
    await waitFor(() => expect(calls.filter((c) => c.path === "/hosts/h1/check")).toHaveLength(2));
    // Genau der Fingerabdruck aus der Prüfung geht zurück.
    expect(calls.find((c) => c.path === "/hosts/h1/known-hosts" && c.method === "POST")?.body).toEqual({ key_type: "ssh-ed25519", fingerprint: FINGERPRINT });
    const order = calls.map((c) => `${c.method} ${c.path}`).filter((c) => c.includes("check") || c.includes("known-hosts"));
    expect(order.indexOf("POST /hosts/h1/known-hosts")).toBeLessThan(order.lastIndexOf("POST /hosts/h1/check"));
    expect(await within(check).findByText(/4 von 5 in Ordnung/)).toBeInTheDocument();
    expect(within(check).queryByRole("button", { name: /bestätigen/ })).not.toBeInTheDocument();
  });

  it("neuer Schlüssel, aber das Bestätigen wird abgelehnt (409): der Text des Servers steht da, es wird nicht neu geprüft", async () => {
    const calls = mockHostsApi({
      ...withCred(),
      "POST /hosts/h1/check": newKey,
      "POST /hosts/h1/known-hosts": reply(409, { detail: "Bitte zuerst „Verbindung prüfen“ – der Fingerabdruck muss frisch vom Server kommen." }),
    });
    renderDetail();
    const check = await findCard("Verbindung prüfen");
    fireEvent.click(within(check).getByRole("button", { name: "Verbindung prüfen" }));
    fireEvent.click(await within(check).findByRole("button", { name: /Fingerabdruck stimmt – bestätigen/ }));
    expect(await within(check).findByText(/der Fingerabdruck muss frisch vom Server kommen/)).toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/hosts/h1/check")).toHaveLength(1);
  });

  it("geänderter Schlüssel: deutliche Warnung mit beiden Fingerabdrücken und KEIN Bestätigen-Knopf", async () => {
    mockHostsApi({
      ...withCred(),
      "POST /hosts/h1/check": checkFixture({
        ok: false, host_key: { status: "changed", key_type: "ssh-ed25519", fingerprint: FINGERPRINT, expected: OLD_FINGERPRINT },
        items: [
          { id: "reachable", label: "Server erreichbar", status: "ok", detail: "SSH-Dienst antwortet.", hint: "" },
          {
            id: "host_key", label: "Server-Schlüssel", status: "fail",
            detail: `Achtung: Der Server meldet einen anderen Schlüssel als bisher. Bisher ${OLD_FINGERPRINT}, jetzt ${FINGERPRINT} (ssh-ed25519).`,
            hint: "Das passiert nach einer Neuinstallation – oder wenn sich jemand dazwischenschaltet.",
          },
        ],
      }),
    });
    renderDetail();
    const check = await findCard("Verbindung prüfen");
    fireEvent.click(within(check).getByRole("button", { name: "Verbindung prüfen" }));
    const warning = await within(check).findByTestId("key-changed");
    expect(within(warning).getByText(/Achtung: Der Server meldet einen anderen Schlüssel als bisher/)).toBeInTheDocument();
    expect(within(warning).getByText(OLD_FINGERPRINT)).toBeInTheDocument();
    expect(within(warning).getByText(FINGERPRINT)).toBeInTheDocument();
    expect(within(warning).getByRole("link", { name: "Server-Schlüssel" })).toHaveAttribute("href", "#server-schluessel");
    expect(within(check).queryByRole("button", { name: /bestätigen|merken/i })).not.toBeInTheDocument();
  });
});

describe("Server-Detail: gemerkte Schlüssel", () => {
  const KEY = { key_type: "sk-ssh-ed25519@openssh.com", fingerprint: FINGERPRINT, first_seen_at: "2026-09-12T09:30:00Z", accepted_by_user_id: "u1", accepted_by_label: "nico" };

  it("zeigt Typ, Fingerabdruck, Datum und wer ihn bestätigt hat; leer: Hinweis", async () => {
    mockHostsApi(routes({ "GET /hosts/h1/known-hosts": [KEY, { ...KEY, key_type: "ssh-rsa", accepted_by_label: null }] }));
    renderDetail();
    const keys = await findCard("Server-Schlüssel");
    expect(await within(keys).findByText("sk-ssh-ed25519@openssh.com")).toBeInTheDocument();
    expect(within(keys).getAllByText(FINGERPRINT)).toHaveLength(2);
    expect(within(keys).getByText("gemerkt am 12.09.2026 von nico")).toBeInTheDocument();
    expect(within(keys).getByText("gemerkt am 12.09.2026 von automatisch")).toBeInTheDocument();
  });

  it("leer: Noch kein Schlüssel gemerkt", async () => {
    mockHostsApi(routes());
    renderDetail();
    expect(await within(await findCard("Server-Schlüssel")).findByText(/Noch kein Schlüssel gemerkt/)).toBeInTheDocument();
  });

  it("Vergessen fragt nach und löscht mit dem kodierten Schlüsseltyp", async () => {
    let keys = [KEY];
    const calls = mockHostsApi(routes({
      "GET /hosts/h1/known-hosts": () => keys,
      "DELETE /hosts/h1/known-hosts/sk-ssh-ed25519%40openssh.com": () => { keys = []; return undefined; },
    }));
    renderDetail();
    const panel = await findCard("Server-Schlüssel");
    fireEvent.click(await within(panel).findByRole("button", { name: "Vergessen" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    const [message, options] = vi.mocked(confirmDialog).mock.calls[0];
    expect(message).toBe(
      "Gemerkten Schlüssel (sk-ssh-ed25519@openssh.com) von „Bastel-Pi“ vergessen? Nur machen, wenn der Server neu installiert wurde. Beim nächsten Prüfen musst du den neuen Fingerabdruck bestätigen.",
    );
    expect(options).toMatchObject({ danger: true, confirmLabel: "Vergessen" });
    expect(await within(panel).findByText(/Noch kein Schlüssel gemerkt/)).toBeInTheDocument();
  });

  it("Abbrechen bei der Rückfrage löscht nichts", async () => {
    vi.mocked(confirmDialog).mockResolvedValueOnce(false);
    const calls = mockHostsApi(routes({ "GET /hosts/h1/known-hosts": [KEY] }));
    renderDetail();
    fireEvent.click(await within(await findCard("Server-Schlüssel")).findByRole("button", { name: "Vergessen" }));
    await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });
});

describe("Server-Detail: Zugang ersetzen (make-default)", () => {
  const OLD = credentialFixture({ id: "c1" });
  const NEW = credentialFixture({ id: "c2", is_default: false, created_at: "2026-10-01T10:00:00Z" });
  const both = () => routes({
    "GET /hosts/h1": hostFixture({ credential: { id: "c1", kind: "ssh_key", username: "lattice", port: 22 } }),
    "GET /hosts/h1/credentials": [OLD, NEW],
    "GET /hosts/h1/credentials/c2/setup": setupFixture,
  });
  const loginOk = checkFixture({ credential_id: "c2" });

  it("„Neuen Zugang verwenden“ wird erst nach einer erfolgreichen Prüfung frei und löscht den alten mit", async () => {
    const calls = mockHostsApi({
      ...both(),
      "POST /hosts/h1/check": loginOk,
      "POST /hosts/h1/credentials/c2/make-default": { ...NEW, is_default: true, notice: "Der alte Zugang ist in Nodvard Deck gelöscht. Sein öffentlicher Schlüssel bleibt in ~/.ssh/authorized_keys auf dem Server stehen – dort bei Bedarf selbst entfernen." },
    });
    renderDetail();
    const use = await screen.findByRole("button", { name: "Neuen Zugang verwenden" });
    expect(use).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Neuen Zugang prüfen" }));
    await waitFor(() => expect(use).toBeEnabled());
    expect(calls.find((c) => c.path === "/hosts/h1/check")?.body).toEqual({ credential_id: "c2" });
    fireEvent.click(use);
    await waitFor(() => expect(calls.some((c) => c.path === "/hosts/h1/credentials/c2/make-default")).toBe(true));
    expect(calls.find((c) => c.path === "/hosts/h1/credentials/c2/make-default")?.body).toEqual({ delete_previous: true });
    expect(await screen.findByText(/bleibt in ~\/\.ssh\/authorized_keys auf dem Server stehen/)).toBeInTheDocument();
  });

  it("zu alte Prüfung (409): der Hinweis des Servers erscheint, nichts wurde gelöscht", async () => {
    mockHostsApi({
      ...both(),
      "POST /hosts/h1/check": loginOk,
      "POST /hosts/h1/credentials/c2/make-default": reply(409, {
        detail: "Bitte zuerst „Verbindung prüfen“ für den neuen Zugang ausführen: Die Anmeldung muss geklappt haben, und die Prüfung darf höchstens 10 Minuten her sein. Sonst wäre der alte Zugang weg, ohne dass der neue sicher funktioniert.",
      }),
    });
    renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Neuen Zugang prüfen" }));
    const use = screen.getByRole("button", { name: "Neuen Zugang verwenden" });
    await waitFor(() => expect(use).toBeEnabled());
    fireEvent.click(use);
    expect(await screen.findByText(/die Prüfung darf höchstens 10 Minuten her sein/)).toBeInTheDocument();
    // Beide Zugänge stehen weiter da.
    expect(screen.getAllByText(/Anmeldung mit Schlüssel als lattice/)).toHaveLength(2);
  });

  it("schlägt die Anmeldung mit dem neuen Zugang fehl, bleibt der Knopf gesperrt", async () => {
    mockHostsApi({
      ...both(),
      "POST /hosts/h1/check": checkFixture({
        ok: false, credential_id: "c2",
        items: [{ id: "login", label: "Anmeldung als lattice", status: "fail", detail: "Der Server hat den Schlüssel nicht akzeptiert.", hint: "Ist der Einrichtungsbefehl auf dem Server gelaufen? Stimmt der Benutzer?" }],
      }),
    });
    renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Neuen Zugang prüfen" }));
    expect(await screen.findByText("Der Server hat den Schlüssel nicht akzeptiert.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Neuen Zugang verwenden" })).toBeDisabled();
  });

  it("„Ersetzen“ legt einen NEUEN Zugang an (erzeugt einen Schlüssel), der alte bleibt", async () => {
    let creds = [OLD];
    const calls = mockHostsApi({
      ...both(),
      "GET /hosts/h1/credentials": () => creds,
      "POST /hosts/h1/credentials/generate-key": () => {
        creds = [OLD, NEW];
        return { credential: NEW, public_key: "ssh-ed25519 AAAA lattice@bastel-pi", fingerprint: "SHA256:xyz" };
      },
    });
    renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Ersetzen" }));
    fireEvent.click(screen.getByRole("button", { name: "SSH-Schlüssel erzeugen" }));
    expect(await screen.findByText(/Der bisherige Zugang bleibt Standard/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Schlüssel erzeugen" }));
    expect(await screen.findByRole("button", { name: "Neuen Zugang prüfen" })).toBeInTheDocument();
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    // Der Einrichtungsbefehl des neuen Zugangs ist gleich zu sehen.
    expect(await screen.findByLabelText("Einrichtungsbefehl")).toBeInTheDocument();
  });

  it("Zugang löschen: Rückfrage mit dem Text aus dem Design, dann DELETE", async () => {
    const calls = mockHostsApi(routes({
      "GET /hosts/h1": hostFixture({ credential: { id: "c1", kind: "ssh_key", username: "lattice", port: 22 } }),
      "GET /hosts/h1/credentials": [OLD],
      "DELETE /hosts/h1/credentials/c1": undefined,
    }));
    renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Zugang löschen" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/hosts/h1/credentials/c1")).toBe(true));
    expect(vi.mocked(confirmDialog).mock.calls[0][0]).toBe(
      "SSH-Zugang für „Bastel-Pi“ löschen? Terminal, Updates und Überwachung erreichen den Server dann nicht mehr. Auf dem Server bleibt der Schlüssel eingetragen – bei Bedarf dort aus ~/.ssh/authorized_keys entfernen.",
    );
    expect(vi.mocked(confirmDialog).mock.calls[0][1]).toMatchObject({ danger: true });
  });

  it("zeigt „Anmeldung mit Schlüssel als lattice, Port 22 – seit 30.09.2026“", async () => {
    mockHostsApi(routes({ "GET /hosts/h1/credentials": [OLD] }));
    renderDetail();
    expect(await screen.findByText("Anmeldung mit Schlüssel als lattice, Port 22 – seit 30.09.2026")).toBeInTheDocument();
  });
});

describe("Server-Detail: Eingaben werden nach dem Absenden gelöscht", () => {
  function noSecretAnywhere(client: QueryClient, secret: string) {
    const cached = JSON.stringify(client.getQueryCache().getAll().map((q) => ({ key: q.queryKey, data: q.state.data })));
    expect(cached).not.toContain(secret);
    expect(client.getMutationCache().getAll()).toHaveLength(0);
  }

  it("Passwort: das Feld ist danach leer (auch wenn es schiefging) und nichts liegt im Cache", async () => {
    const calls = mockHostsApi(routes({
      "POST /hosts/h1/credentials": reply(422, { detail: [{ type: "value_error", loc: ["body", "username"], msg: "Benutzername: nur Buchstaben, Ziffern, _, -, ., @, \\ und Leerzeichen (höchstens 64 Zeichen)." }] }),
    }));
    const { client } = renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Passwort eingeben" }));
    fireEvent.change(screen.getByLabelText("Benutzer"), { target: { value: "bad user!" } });
    fireEvent.change(screen.getByLabelText("Passwort"), { target: { value: "hunter2-geheim" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByText(/Benutzername: nur Buchstaben/)).toBeInTheDocument();
    expect(screen.getByLabelText("Passwort")).toHaveValue("");
    expect(calls.find((c) => c.method === "POST" && c.path === "/hosts/h1/credentials")?.body).toEqual({
      kind: "ssh_password", username: "bad user!", port: 22, secret_value: "hunter2-geheim", is_default: true,
    });
    noSecretAnywhere(client, "hunter2-geheim");
  });

  it("eigener Schlüssel: Feld leer, Meldung des Servers am Feld, nichts im Cache", async () => {
    const pasted = "-----BEGIN OPENSSH PRIVATE KEY-----\nGEHEIMERINHALT\n-----END OPENSSH PRIVATE KEY-----";
    mockHostsApi(routes({
      "POST /hosts/h1/credentials": reply(422, { detail: [{ type: "value_error", loc: ["body", "secret_value"], msg: "Das ist kein gültiger privater SSH-Schlüssel." }] }),
    }));
    const { client } = renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Eigenen Schlüssel einfügen" }));
    fireEvent.change(screen.getByLabelText("Benutzer"), { target: { value: "lattice" } });
    fireEvent.change(screen.getByLabelText("Privater Schlüssel"), { target: { value: pasted } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByText("Das ist kein gültiger privater SSH-Schlüssel.")).toBeInTheDocument();
    expect(screen.getByLabelText("Privater Schlüssel")).toHaveValue("");
    noSecretAnywhere(client, "GEHEIMERINHALT");
  });

  it("Erfolg: Passwort gespeichert, Formular zu, nichts im Cache, neuer Zugang ist der Standard", async () => {
    let creds: unknown[] = [];
    const calls = mockHostsApi(routes({
      "GET /hosts/h1/credentials": () => creds,
      "POST /hosts/h1/credentials": (c: Call) => { creds = [credentialFixture({ kind: "ssh_password", username: "admin" })]; return { ...(c.body as object), id: "c1" }; },
    }));
    const { client } = renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Passwort eingeben" }));
    fireEvent.change(screen.getByLabelText("Benutzer"), { target: { value: "admin" } });
    fireEvent.change(screen.getByLabelText("Passwort"), { target: { value: "hunter2-geheim" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByText("Anmeldung mit Passwort als admin, Port 22 – seit 30.09.2026")).toBeInTheDocument();
    expect(screen.queryByLabelText("Passwort")).not.toBeInTheDocument();
    // Für Passwort-Zugänge gibt es keinen Einrichtungsbefehl.
    expect(screen.queryByRole("button", { name: "Einrichtungsbefehl zeigen" })).not.toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST" && c.path === "/hosts/h1/credentials")).toHaveLength(1);
    noSecretAnywhere(client, "hunter2-geheim");
  });
});

describe("Server-Detail: bearbeiten und löschen", () => {
  it("Bearbeiten schickt nur Geändertes – die unveränderte Adresse bleibt weg", async () => {
    const calls = mockHostsApi(routes({ "PATCH /hosts/h1": hostFixture({ display_name: "Pi Wohnzimmer" }) }));
    renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Bearbeiten" }));
    expect(screen.getByText("Der Kurzname lässt sich nicht ändern.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Anzeigename"), { target: { value: "Pi Wohnzimmer" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ display_name: "Pi Wohnzimmer" });
  });

  it("eine geänderte Adresse geht mit; Markierungen nur die manuellen", async () => {
    const calls = mockHostsApi(routes({
      "GET /hosts/h1": hostFixture({ tags: ["proxmox", "docker"], managed_tags: ["proxmox"], provider_ext_id: "proxmox", kind: "vm" }),
      "PATCH /hosts/h1": hostFixture(),
    }));
    renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Bearbeiten" }));
    expect(screen.getByLabelText("Markierungen")).toHaveValue("docker");
    expect(screen.getByText(/Eine von Hand eingetragene Adresse bleibt stehen/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Adresse (IP oder Name)"), { target: { value: "192.168.2.99" } });
    fireEvent.change(screen.getByLabelText("Markierungen"), { target: { value: "docker web" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ address: "192.168.2.99", tags: ["docker", "web"] });
  });

  it("ohne Änderung wird nichts gesendet", async () => {
    const calls = mockHostsApi(routes());
    renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Bearbeiten" }));
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByText("Nichts geändert.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
  });

  it("Löschen: ein falsch eingetippter Name löscht nichts, der richtige schon", async () => {
    const calls = mockHostsApi(routes({ "DELETE /hosts/h1": undefined }));
    renderDetail();
    const danger = await screen.findByRole("heading", { name: "Gefahrenbereich", level: 3 });
    const button = within(danger.closest("section") as HTMLElement).getByRole("button", { name: "Server löschen" });

    vi.mocked(promptDialog).mockResolvedValueOnce("falsch");
    fireEvent.click(button);
    expect(await screen.findByText(/Der Kurzname stimmte nicht mit „bastel-pi“ überein – nichts gelöscht/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);

    vi.mocked(promptDialog).mockResolvedValueOnce(null);
    fireEvent.click(button);
    await waitFor(() => expect(promptDialog).toHaveBeenCalledTimes(2));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);

    vi.mocked(promptDialog).mockResolvedValueOnce("bastel-pi");
    fireEvent.click(button);
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/settings/hosts"));
    expect(calls.filter((c) => c.method === "DELETE").map((c) => c.path)).toEqual(["/hosts/h1"]);
    const text = vi.mocked(promptDialog).mock.calls[0][0];
    expect(text).toContain("Zum Löschen den Kurznamen „bastel-pi“ eintippen.");
    expect(text).toContain("Gelöscht werden auch SSH-Zugang und gemerkter Server-Schlüssel.");
    expect(text).not.toContain("automatisch eingelesen");
  });

  it("ein von Proxmox eingelesener Server bekommt den Zusatz, dass er wiederkommt", async () => {
    mockHostsApi(routes({ "GET /hosts/h1": hostFixture({ provider_ext_id: "proxmox", kind: "vm" }) }));
    renderDetail();
    const danger = await screen.findByRole("heading", { name: "Gefahrenbereich", level: 3 });
    vi.mocked(promptDialog).mockResolvedValueOnce(null);
    fireEvent.click(within(danger.closest("section") as HTMLElement).getByRole("button", { name: "Server löschen" }));
    await waitFor(() => expect(promptDialog).toHaveBeenCalled());
    expect(vi.mocked(promptDialog).mock.calls[0][0]).toContain("taucht beim nächsten Abgleich wieder auf – dann ohne SSH-Zugang.");
  });

  it("läuft gerade eine Aktion, zeigt die Seite die Antwort des Servers (409)", async () => {
    mockHostsApi(routes({
      "DELETE /hosts/h1": reply(409, { detail: "Auf diesem Server läuft gerade eine Aktion. Bitte warten, bis sie fertig ist." }),
    }));
    renderDetail();
    const danger = await screen.findByRole("heading", { name: "Gefahrenbereich", level: 3 });
    vi.mocked(promptDialog).mockResolvedValueOnce("bastel-pi");
    fireEvent.click(within(danger.closest("section") as HTMLElement).getByRole("button", { name: "Server löschen" }));
    expect(await screen.findByText(/Auf diesem Server läuft gerade eine Aktion/)).toBeInTheDocument();
    expect(screen.queryByTestId("where")).not.toBeInTheDocument();
  });
});

describe("Server-Detail: Gruppen", () => {
  it("Haken setzen nimmt den Server auf, Haken weg nimmt ihn heraus", async () => {
    let inDocker = true;
    const calls = mockHostsApi(routes({
      "GET /host-groups": [{ id: "g1", name: "docker-server", description: "" }, { id: "g2", name: "proxmox", description: "" }],
      "GET /hosts?group=g1": () => (inDocker ? [hostFixture()] : []),
      "GET /hosts?group=g2": [],
      "DELETE /host-groups/g1/members/h1": () => { inDocker = false; return undefined; },
      "POST /host-groups/g2/members/h1": undefined,
    }));
    renderDetail();
    const groups = await findCard("Gruppen");
    const docker = await within(groups).findByRole("checkbox", { name: "docker-server" });
    await waitFor(() => expect(docker).toBeChecked());
    expect(within(groups).getByRole("checkbox", { name: "proxmox" })).not.toBeChecked();
    fireEvent.click(docker);
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/host-groups/g1/members/h1")).toBe(true));
    await waitFor(() => expect(docker).not.toBeChecked());
    fireEvent.click(within(groups).getByRole("checkbox", { name: "proxmox" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/host-groups/g2/members/h1")).toBe(true));
  });
});
