import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { sendsInClear } from "../../../lib/hosts";
import { useAuthStore } from "../../../state/auth";
import { checkFixture, credentialFixture, FINGERPRINT, hostFixture, mockHostsApi, reply, type Call } from "../../../test/hostsMock";
import { HostDetailSettings } from "../HostDetailSettings";

vi.mock("../../../state/dialogs", () => ({ confirmDialog: vi.fn(async () => true), promptDialog: vi.fn(async () => null) }));

// Ob die Seite „unverschlüsselt“ läuft, hängt an `window.location` (jsdom: http://localhost). `inClear.value` erzwingt
// ein Ergebnis; `null` nimmt die echte Funktion.
const inClear = vi.hoisted(() => ({ value: null as boolean | null }));
vi.mock("../../../lib/hosts", async (importOriginal) => {
  const real = await importOriginal<typeof import("../../../lib/hosts")>();
  return { ...real, sendsInClear: (loc: Pick<Location, "protocol" | "hostname">) => inClear.value ?? real.sendsInClear(loc) };
});

const SECRET = "hunter2-einmalig";
const SETUP_URL = "POST /hosts/h1/credentials/c1/setup-with-password";

const REQUIREMENTS = [
  { ext_id: "shield", id: "root", label: "Root-Rechte (Nodvard Shield)", check_command: null, ok_text: "", fail_hint: "", unix_group: null, needs_root: true, root_reason: "Updates einspielen", order: 50 },
  { ext_id: "docker", id: "docker-group", label: "Docker ohne sudo (Container-Verwaltung)", check_command: "docker ps -q", ok_text: "", fail_hint: "", unix_group: "docker", needs_root: false, root_reason: null, order: 100 },
];

function setupFixture(call: Call) {
  const q = new URLSearchParams(call.query);
  return {
    username: "nodvard", public_key: "ssh-ed25519 AAAA nodvard@bastel-pi", fingerprint: "SHA256:xyz",
    one_liner: "S=; [ \"$(id -u)\" = 0 ] || S=sudo; $S sh -c 'set -e; U=nodvard'", script: "set -e\nU=nodvard",
    notes: ["Der Schlüssel gilt nur zum Anmelden und für Befehle (restrict,pty): kein Weiterleiten von Ports, kein Agent."],
    groups: q.getAll("groups"), sudo: q.get("sudo") === "true",
  };
}

function okResult(over: Record<string, unknown> = {}) {
  return {
    ok: true, check: checkFixture(), messages: ["Benutzer nodvard angelegt."], made_default: false,
    notes: ["Der Schlüssel gilt nur zum Anmelden und für Befehle (restrict,pty): kein Weiterleiten von Ports, kein Agent."],
    ...over,
  };
}

function routes(over: Record<string, unknown> = {}) {
  return {
    "GET /hosts/h1": hostFixture(),
    "GET /hosts/h1/credentials": [credentialFixture({ username: "nodvard" })],
    "GET /hosts/h1/known-hosts": [],
    "GET /hosts/h1/requirements": REQUIREMENTS,
    "GET /host-groups": [],
    "GET /hosts/h1/credentials/c1/setup": setupFixture,
    ...over,
  };
}

function renderDetail() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/settings/hosts/h1"]}>
        <Routes>
          <Route path="/settings/hosts/:hostId" element={<HostDetailSettings />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { client, ...view };
}

/** Einrichtungsbefehl aufklappen und den Weg „Mit Passwort einrichten“ wählen. */
async function openPasswordWay(): Promise<HTMLElement> {
  fireEvent.click(await screen.findByRole("button", { name: "Einrichtungsbefehl zeigen" }));
  fireEvent.click(await screen.findByRole("radio", { name: /Mit Passwort einrichten/ }));
  return screen.findByTestId("password-setup");
}

function typePassword(form: HTMLElement, value = SECRET) {
  fireEvent.change(within(form).getByLabelText("Passwort dieses Kontos"), { target: { value } });
}

function noSecretAnywhere(client: QueryClient, secret = SECRET) {
  const cached = JSON.stringify(client.getQueryCache().getAll().map((q) => ({ key: q.queryKey, data: q.state.data })));
  expect(cached).not.toContain(secret);
  expect(client.getMutationCache().getAll()).toHaveLength(0);
  expect(window.location.href).not.toContain(secret);
  expect(JSON.stringify({ ...window.localStorage })).not.toContain(secret);
  expect(JSON.stringify({ ...window.sessionStorage })).not.toContain(secret);
}

beforeEach(() => {
  inClear.value = null;
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

describe("Mit Passwort einrichten: Formular", () => {
  it("Standard bleibt „Befehl selbst einfügen“; der zweite Weg zeigt Anmelde-Konto (root) und ein verdecktes Passwortfeld", async () => {
    mockHostsApi(routes());
    renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Einrichtungsbefehl zeigen" }));
    expect(await screen.findByRole("radio", { name: "Befehl selbst einfügen" })).toBeChecked();
    expect(await screen.findByLabelText("Einrichtungsbefehl")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: /Mit Passwort einrichten/ }));
    const form = await screen.findByTestId("password-setup");
    expect(screen.queryByLabelText("Einrichtungsbefehl")).not.toBeInTheDocument();
    expect(within(form).getByLabelText("Anmelde-Konto")).toHaveValue("root");
    const field = within(form).getByLabelText("Passwort dieses Kontos");
    expect(field).toHaveAttribute("type", "password");
    // „off“ übergehen viele Browser und bieten an, das Passwort zu speichern; „new-password“ füllt nichts vor.
    expect(field).toHaveAttribute("autocomplete", "new-password");
    expect(within(form).getByText(/vergisst das Passwort sofort/)).toBeInTheDocument();
    // root braucht kein sudo: kein Haken dafür; „Einrichten“ erst mit Passwort.
    expect(within(form).queryByRole("checkbox", { name: /Für sudo dasselbe Passwort/ })).not.toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Einrichten" })).toBeDisabled();
    // „Was macht der Befehl?“ bleibt sichtbar: es läuft genau dieser Befehl.
    expect(screen.getByText("Was macht der Befehl?")).toBeInTheDocument();
  });

  it("Absenden: das Feld ist sofort leer, die Haken gehen mit, nichts liegt im Cache, in der Adresse oder im Browser-Speicher", async () => {
    let answer: (v: unknown) => void = () => undefined;
    const calls = mockHostsApi(routes({ [SETUP_URL]: () => new Promise((r) => { answer = r; }) }));
    const { client } = renderDetail();
    const form = await openPasswordWay();
    typePassword(form);
    fireEvent.click(within(form).getByRole("button", { name: "Einrichten" }));

    // Noch vor der Antwort: leer.
    await waitFor(() => expect(within(form).getByLabelText("Passwort dieses Kontos")).toHaveValue(""));
    expect(within(form).getByRole("button", { name: /Richte ein/ })).toBeDisabled();
    const sent = calls.find((c) => `${c.method} ${c.path}` === SETUP_URL);
    // Vorbelegt: root-Rechte (Nodvard Shield) und die Gruppe docker (Docker), wie beim Befehl.
    expect(sent?.body).toEqual({
      login_user: "root", password: SECRET, sudo: true, full_sudo: false, drop_full_rule: false, groups: ["docker"],
      sudo_password_same: true,
    });
    expect(sent?.query).toBe("");

    answer(okResult());
    expect(await within(form).findByText(/Eingerichtet\. Nodvard Deck meldet sich jetzt mit dem neuen Schlüssel an\./)).toBeInTheDocument();
    expect(within(form).getByText("Benutzer nodvard angelegt.")).toBeInTheDocument();
    expect(within(form).getByRole("list", { name: "Ergebnis der Prüfung" })).toBeInTheDocument();
    // Der Hinweis zu restrict,pty steht schon beim Befehl: im Ergebnis nicht noch einmal.
    expect(within(form).queryByText(/restrict,pty/)).not.toBeInTheDocument();
    noSecretAnywhere(client);
  });

  it("ein abgewählter Haken gilt auch hier", async () => {
    const calls = mockHostsApi(routes({ [SETUP_URL]: okResult() }));
    renderDetail();
    const form = await openPasswordWay();
    fireEvent.click(screen.getByRole("checkbox", { name: /Root-Rechte ohne Passwort/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Zur Gruppe „docker“/ }));
    typePassword(form);
    fireEvent.click(within(form).getByRole("button", { name: "Einrichten" }));
    await within(form).findByTestId("password-setup-result");
    expect(calls.find((c) => `${c.method} ${c.path}` === SETUP_URL)?.body).toMatchObject({ sudo: false, groups: [] });
  });

  it("„Volle Rechte“ unter „Erweitert“ geht auch beim Einrichten mit Passwort mit", async () => {
    const helper = {
      ext_id: "shield", module: "Nodvard Shield", path: "/usr/local/lib/nodvard-deck/shield-root", version: 1,
      sha256: "a".repeat(64), text: "#!/bin/sh\n",
    };
    const withHelpers = (call: Call) => ({
      ...setupFixture(call), helpers: [helper], full_sudo: new URLSearchParams(call.query).get("full_sudo") === "true",
    });
    const calls = mockHostsApi(routes({ "GET /hosts/h1/credentials/c1/setup": withHelpers, [SETUP_URL]: okResult() }));
    renderDetail();
    const form = await openPasswordWay();
    fireEvent.click(within(await screen.findByTestId("setup-advanced")).getByRole("checkbox", { name: /Volle Rechte/ }));
    typePassword(form);
    fireEvent.click(within(form).getByRole("button", { name: "Einrichten" }));
    await waitFor(() => expect(calls.find((c) => `${c.method} ${c.path}` === SETUP_URL)?.body).toMatchObject({ sudo: true, full_sudo: true }));
  });

  it("„Volle sudo-Regel entfernen“ geht auch beim Einrichten mit Passwort mit", async () => {
    const helper = {
      ext_id: "shield", module: "Nodvard Shield", path: "/usr/local/lib/nodvard-deck/shield-root", version: 1,
      sha256: "a".repeat(64), text: "#!/bin/sh\n",
    };
    const withHelpers = (call: Call) => ({ ...setupFixture(call), helpers: [helper], full_sudo: false });
    const calls = mockHostsApi(routes({ "GET /hosts/h1/credentials/c1/setup": withHelpers, [SETUP_URL]: okResult() }));
    renderDetail();
    const form = await openPasswordWay();
    fireEvent.click(await within(await screen.findByTestId("setup-advanced")).findByRole("checkbox", { name: /Volle sudo-Regel entfernen/ }));
    typePassword(form);
    fireEvent.click(within(form).getByRole("button", { name: "Einrichten" }));
    await waitFor(() => expect(calls.find((c) => `${c.method} ${c.path}` === SETUP_URL)?.body).toMatchObject({
      sudo: true, full_sudo: false, drop_full_rule: true,
    }));
  });

  it("braucht kein Modul root, sagt der Haken, dass kein sudo kommt, und bietet „Volle Rechte“ an", async () => {
    const onlyGroup = REQUIREMENTS.filter((r) => !r.needs_root);
    const withoutRoot = (call: Call) => {
      const full = new URLSearchParams(call.query).get("full_sudo") === "true";
      const notes = full ? [] : ["Kein eingeschaltetes Modul braucht auf diesem Server root. Darum richtet der Befehl kein sudo ein."];
      return { ...setupFixture(call), sudo: full, full_sudo: full, helpers: [], notes };
    };
    mockHostsApi(routes({ "GET /hosts/h1/requirements": onlyGroup, "GET /hosts/h1/credentials/c1/setup": withoutRoot }));
    renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Einrichtungsbefehl zeigen" }));
    const root = await screen.findByRole("checkbox", { name: /Root-Rechte ohne Passwort/ });
    expect(root).not.toBeChecked();
    fireEvent.click(root);
    expect(await screen.findByText(/Kein Modul braucht auf diesem Server root/)).toBeInTheDocument();
    expect(screen.queryByText(/darum braucht es die volle sudo-Regel/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Kein eingeschaltetes Modul/)).not.toBeInTheDocument();

    fireEvent.click(within(screen.getByTestId("setup-advanced")).getByRole("checkbox", { name: /Volle Rechte/ }));
    expect(await screen.findByText(/darum braucht es die volle sudo-Regel/)).toBeInTheDocument();
    expect(screen.queryByText(/Kein Modul braucht auf diesem Server root/)).not.toBeInTheDocument();
  });

  it("anderes Konto als root: Haken „Für sudo dasselbe Passwort nehmen“ (Vorgabe an), abgewählt geht false mit", async () => {
    const calls = mockHostsApi(routes({ [SETUP_URL]: okResult() }));
    renderDetail();
    const form = await openPasswordWay();
    fireEvent.change(within(form).getByLabelText("Anmelde-Konto"), { target: { value: "admin" } });
    const same = within(form).getByRole("checkbox", { name: /Für sudo dasselbe Passwort nehmen/ });
    expect(same).toBeChecked();
    fireEvent.click(same);
    typePassword(form);
    fireEvent.click(within(form).getByRole("button", { name: "Einrichten" }));
    await within(form).findByTestId("password-setup-result");
    expect(calls.find((c) => `${c.method} ${c.path}` === SETUP_URL)?.body).toMatchObject({ login_user: "admin", sudo_password_same: false });
  });
});

describe("Mit Passwort einrichten: Hinweis bei unverschlüsselter Verbindung", () => {
  it("über http im Netz: deutlicher Hinweis, Absenden bleibt möglich", async () => {
    inClear.value = true;
    mockHostsApi(routes());
    renderDetail();
    const form = await openPasswordWay();
    expect(within(form).getByTestId("password-in-clear").textContent).toContain("Das Passwort geht unverschlüsselt durchs Netz");
    typePassword(form);
    expect(within(form).getByRole("button", { name: "Einrichten" })).toBeEnabled();
  });

  it("direkt auf dem Rechner (jsdom: http://localhost): kein Hinweis", async () => {
    mockHostsApi(routes());
    renderDetail();
    const form = await openPasswordWay();
    expect(within(form).queryByTestId("password-in-clear")).not.toBeInTheDocument();
  });

  it("sendsInClear: http im Netz ja; https, localhost, 127.x und ::1 nein", () => {
    expect(sendsInClear({ protocol: "http:", hostname: "192.168.2.40" })).toBe(true);
    expect(sendsInClear({ protocol: "http:", hostname: "bastel-pi" })).toBe(true);
    expect(sendsInClear({ protocol: "http:", hostname: "localhost.example" })).toBe(true);
    expect(sendsInClear({ protocol: "https:", hostname: "192.168.2.40" })).toBe(false);
    expect(sendsInClear({ protocol: "http:", hostname: "localhost" })).toBe(false);
    expect(sendsInClear({ protocol: "http:", hostname: "127.0.0.1" })).toBe(false);
    expect(sendsInClear({ protocol: "http:", hostname: "[::1]" })).toBe(false);
  });
});

describe("Mit Passwort einrichten: Fehler", () => {
  it("falsches Passwort: fester Satz des Servers, Feld leer, nichts im Cache", async () => {
    mockHostsApi(routes({
      [SETUP_URL]: reply(400, { detail: "Der Server hat die Anmeldung abgelehnt: Benutzer oder Passwort stimmt nicht – oder der Server erlaubt keine Anmeldung mit Passwort.", code: "wrong_password" }),
    }));
    const { client } = renderDetail();
    const form = await openPasswordWay();
    typePassword(form);
    fireEvent.click(within(form).getByRole("button", { name: "Einrichten" }));
    expect(await within(form).findByText(/Der Server hat die Anmeldung abgelehnt/)).toBeInTheDocument();
    expect(within(form).getByLabelText("Passwort dieses Kontos")).toHaveValue("");
    noSecretAnywhere(client);
  });

  it("Server-Schlüssel unbekannt: Fingerabdruck und „bestätigen“ – danach Passwort neu eingeben, nichts wird von selbst gesendet", async () => {
    const calls = mockHostsApi(routes({
      [SETUP_URL]: reply(409, {
        detail: "Nodvard Deck kennt den Schlüssel dieses Servers noch nicht. Vergleiche den Fingerabdruck und bestätige ihn – dein Passwort wurde nicht gesendet.",
        code: "host_key_unconfirmed",
        host_key: { status: "new", key_type: "ssh-ed25519", fingerprint: FINGERPRINT, expected: null },
      }),
      "POST /hosts/h1/known-hosts": { key_type: "ssh-ed25519", fingerprint: FINGERPRINT, first_seen_at: "2026-09-30T10:00:00Z", accepted_by_user_id: "u1", accepted_by_label: "nico" },
    }));
    renderDetail();
    const form = await openPasswordWay();
    typePassword(form);
    fireEvent.click(within(form).getByRole("button", { name: "Einrichten" }));
    const box = await within(form).findByTestId("password-setup-key");
    expect(within(box).getByText(FINGERPRINT)).toBeInTheDocument();
    expect(within(box).getByText("ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub").tagName).toBe("CODE");
    expect(within(box).getByText(/Dein Passwort wurde nicht gesendet/)).toBeInTheDocument();

    fireEvent.click(within(box).getByRole("button", { name: /Fingerabdruck stimmt – bestätigen/ }));
    expect(await within(form).findByText(/Gib das Passwort noch einmal ein/)).toBeInTheDocument();
    // Mit dem Zugang: sein Port zählt (er kann von dem des Standard-Zugangs abweichen).
    expect(calls.find((c) => c.method === "POST" && c.path === "/hosts/h1/known-hosts")?.body).toEqual({
      key_type: "ssh-ed25519", fingerprint: FINGERPRINT, credential_id: "c1",
    });
    expect(calls.filter((c) => `${c.method} ${c.path}` === SETUP_URL)).toHaveLength(1);
    expect(within(form).queryByTestId("password-setup-key")).not.toBeInTheDocument();
  });

  it("geänderter Server-Schlüssel: Warnung mit beiden Fingerabdrücken, kein Bestätigen", async () => {
    mockHostsApi(routes({
      [SETUP_URL]: reply(409, {
        detail: "Achtung: Der Server meldet einen anderen Schlüssel als bisher. Dein Passwort wurde nicht gesendet.",
        code: "host_key_changed",
        host_key: { status: "changed", key_type: "ssh-ed25519", fingerprint: FINGERPRINT, expected: "SHA256:alt" },
      }),
    }));
    renderDetail();
    const form = await openPasswordWay();
    typePassword(form);
    fireEvent.click(within(form).getByRole("button", { name: "Einrichten" }));
    const warning = await within(form).findByTestId("key-changed");
    expect(within(warning).getByText("SHA256:alt")).toBeInTheDocument();
    expect(within(warning).getByText(FINGERPRINT)).toBeInTheDocument();
    expect(within(form).queryByRole("button", { name: /bestätigen/ })).not.toBeInTheDocument();
  });

  it("Befehl fehlgeschlagen: Satz und die Meldungen des Befehls", async () => {
    mockHostsApi(routes({
      [SETUP_URL]: reply(502, {
        detail: "Der Einrichtungsbefehl ist auf dem Server fehlgeschlagen. Die Meldungen unten sagen, woran es lag.",
        code: "setup_failed", messages: ["FEHLER: Das Home-Verzeichnis /home/nodvard gibt es nicht."],
      }),
    }));
    renderDetail();
    const form = await openPasswordWay();
    typePassword(form);
    fireEvent.click(within(form).getByRole("button", { name: "Einrichten" }));
    expect(await within(form).findByText(/Der Einrichtungsbefehl ist auf dem Server fehlgeschlagen/)).toBeInTheDocument();
    expect(within(within(form).getByRole("list", { name: "Meldungen des Befehls" })).getByText(/Home-Verzeichnis/)).toBeInTheDocument();
  });

  it("Verbindung während des Befehls abgebrochen: ehrlicher Satz, keine Meldungsliste, noch einmal möglich", async () => {
    const lost = "Die Verbindung zum Server ist abgebrochen, während der Einrichtungsbefehl lief. Die Einrichtung kann teilweise gelaufen sein: Sieh mit „Verbindung prüfen“ nach, was schon eingerichtet ist. Du kannst sie danach gefahrlos noch einmal starten.";
    let attempts = 0;
    mockHostsApi(routes({
      [SETUP_URL]: () => {
        attempts += 1;
        return attempts === 1 ? reply(502, { detail: lost, code: "connection_lost" }) : okResult();
      },
    }));
    renderDetail();
    const form = await openPasswordWay();
    typePassword(form);
    fireEvent.click(within(form).getByRole("button", { name: "Einrichten" }));
    const alert = await within(form).findByRole("alert");
    expect(alert).toHaveTextContent("teilweise gelaufen sein");
    expect(alert).toHaveTextContent("„Verbindung prüfen“");
    expect(within(form).queryByRole("list", { name: "Meldungen des Befehls" })).not.toBeInTheDocument();
    expect(within(form).getByLabelText("Passwort dieses Kontos")).toHaveValue("");
    // Nach dem Prüfen darf man es noch einmal versuchen.
    typePassword(form);
    fireEvent.click(within(form).getByRole("button", { name: "Einrichten" }));
    expect(await within(form).findByTestId("password-setup-result")).toBeInTheDocument();
    expect(attempts).toBe(2);
  });

  it("ungültiges Anmelde-Konto (422): Meldung am Feld", async () => {
    mockHostsApi(routes({
      [SETUP_URL]: reply(422, { detail: [{ type: "value_error", loc: ["body", "login_user"], msg: "Anmelde-Konto: nur Kleinbuchstaben, Ziffern, _ und - (höchstens 32 Zeichen, nicht mit einer Ziffer oder - beginnen)." }] }),
    }));
    renderDetail();
    const form = await openPasswordWay();
    fireEvent.change(within(form).getByLabelText("Anmelde-Konto"), { target: { value: "Admin" } });
    typePassword(form);
    fireEvent.click(within(form).getByRole("button", { name: "Einrichten" }));
    expect(await within(form).findByText(/Anmelde-Konto: nur Kleinbuchstaben/)).toBeInTheDocument();
    expect(within(form).getByLabelText("Passwort dieses Kontos")).toHaveValue("");
  });
});

describe("Mit Passwort einrichten: neuer Zugang neben dem bisherigen", () => {
  it("nach gelungener Einrichtung wird „Neuen Zugang verwenden“ frei", async () => {
    mockHostsApi(routes({
      "GET /hosts/h1/credentials": [
        credentialFixture({ id: "c0", kind: "ssh_password", username: "root" }),
        credentialFixture({ username: "nodvard", is_default: false }),
      ],
      [SETUP_URL]: okResult(),
    }));
    renderDetail();
    expect(await screen.findByRole("button", { name: "Neuen Zugang verwenden" })).toBeDisabled();
    const form = await openPasswordWay();
    typePassword(form);
    fireEvent.click(within(form).getByRole("button", { name: "Einrichten" }));
    await within(form).findByTestId("password-setup-result");
    expect(screen.getByRole("button", { name: "Neuen Zugang verwenden" })).toBeEnabled();
  });

  it("„Neuen Zugang prüfen“ und Fingerabdruck bestätigen schickt den Zugang mit (sein Port zählt)", async () => {
    const newKey = checkFixture({
      ok: false, host_key: { status: "new", key_type: "ssh-ed25519", fingerprint: FINGERPRINT, expected: null },
      items: [
        { id: "reachable", label: "Server erreichbar", status: "ok", detail: "SSH-Dienst antwortet.", hint: "" },
        { id: "host_key", label: "Server-Schlüssel", status: "confirm", detail: `Nodvard Deck kennt diesen Server noch nicht. Fingerabdruck: ${FINGERPRINT}`, hint: "" },
      ],
    });
    const calls = mockHostsApi(routes({
      "GET /hosts/h1/credentials": [
        credentialFixture({ id: "c0", kind: "ssh_password", username: "root", port: 22 }),
        credentialFixture({ username: "nodvard", is_default: false, port: 2222 }),
      ],
      "POST /hosts/h1/check": newKey,
      "POST /hosts/h1/known-hosts": { key_type: "ssh-ed25519", fingerprint: FINGERPRINT, first_seen_at: "2026-09-30T10:00:00Z", accepted_by_user_id: "u1", accepted_by_label: "nico" },
    }));
    renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Neuen Zugang prüfen" }));
    fireEvent.click(await screen.findByRole("button", { name: /Fingerabdruck stimmt – bestätigen/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/hosts/h1/known-hosts")).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.path === "/hosts/h1/known-hosts")?.body).toEqual({
      key_type: "ssh-ed25519", fingerprint: FINGERPRINT, credential_id: "c1",
    });
  });
});
