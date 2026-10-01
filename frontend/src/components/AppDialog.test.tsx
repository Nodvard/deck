import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type AppTileOut, type HostOut, useOverview } from "../lib/overview";
import { useAuthStore } from "../state/auth";
import { AppDialog } from "./AppDialog";

interface Call { method: string; url: string; body: Record<string, unknown> | null }
let calls: Call[];
let respondWith: (call: Call) => Response | Promise<Response>;

function installFetch() {
  calls = [];
  respondWith = (call) => new Response(JSON.stringify({ id: "neu", name: String(call.body?.name ?? "") }), { status: call.method === "POST" ? 201 : 200 });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const call: Call = {
      method: init?.method ?? "GET",
      url: String(input).replace("/api/v1", ""),
      body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
    };
    calls.push(call);
    return respondWith(call);
  }));
}

const HOSTS = [
  { id: "h1", name: "nas", display_name: "Mein NAS" },
  { id: "h2", name: "pi", display_name: "" },
] as HostOut[];

const EXISTING: AppTileOut = {
  id: "a1", source: "custom", name: "Router", url: "http://192.168.2.1", host: "Mein NAS", host_id: "h1", state: null, tone: null,
  image: null, icon: "router", color: "#3b82f6", group: "Netzwerk", open_in_new_tab: false, sort_order: 0,
};

/** Wie das Cockpit: jemand beobachtet `["overview"]` und laedt es nach jeder Aenderung neu. */
function OverviewObserver() {
  useOverview();
  return null;
}

function renderDialog(props: Partial<React.ComponentProps<typeof AppDialog>> = {}, options: { observeOverview?: boolean } = {}) {
  const onOpenChange = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      {options.observeOverview && <OverviewObserver />}
      <AppDialog open onOpenChange={onOpenChange} groups={["Netzwerk", "Speicher"]} hosts={HOSTS} {...props} />
    </QueryClientProvider>,
  );
  return { onOpenChange, ...view };
}

const field = (label: string | RegExp) => screen.getByLabelText(label) as HTMLInputElement;
const submit = (name: string) => fireEvent.click(screen.getByRole("button", { name }));

beforeEach(() => {
  useAuthStore.setState({
    accessToken: "tok",
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: true, locale: "de", permissions: ["*"] },
    status: "authenticated",
    mfaToken: null,
  });
  installFetch();
});
afterEach(() => vi.unstubAllGlobals());

describe("AppDialog: neue App", () => {
  it("zeigt das leere Formular mit verständlichem Text", () => {
    renderDialog();
    expect(screen.getByRole("dialog", { name: "App hinzufügen" })).toBeInTheDocument();
    expect(field("Name").value).toBe("");
    expect(field("Adresse").value).toBe("");
    expect(screen.getByRole("dialog").textContent).toContain("ruft die Adresse nicht selbst auf");
    expect(screen.getByRole("button", { name: "Kein Symbol" })).toHaveAttribute("aria-pressed", "true");
    expect((field("Gehört zu Server (optional)")).value).toBe("");
    expect(screen.getByLabelText("In einem neuen Tab öffnen")).toBeChecked();
  });

  it("legt eine App an: ergänzt http://, trimmt, schickt Symbol, Farbe, Gruppe und Server", async () => {
    const { onOpenChange } = renderDialog();
    fireEvent.change(field("Name"), { target: { value: "  Pi-hole  " } });
    fireEvent.change(field("Adresse"), { target: { value: "192.168.2.72:8088/admin" } });
    fireEvent.click(screen.getByRole("button", { name: "Schutzschild" }));
    fireEvent.click(screen.getByRole("button", { name: "Grün" }));
    fireEvent.change(field(/Gruppe/), { target: { value: " Netzwerk " } });
    fireEvent.change(field(/Gehört zu Server/), { target: { value: "h2" } });
    fireEvent.click(screen.getByLabelText("In einem neuen Tab öffnen"));
    submit("Hinzufügen");

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({ method: "POST", url: "/apps" });
    expect(calls[0].body).toEqual({
      name: "Pi-hole", url: "http://192.168.2.72:8088/admin", icon: "shield-check", color: "#10b981", group: "Netzwerk",
      open_in_new_tab: false, host_id: "h2",
    });
  });

  it("nimmt auch ein Emoji als Symbol", async () => {
    const { onOpenChange } = renderDialog();
    fireEvent.change(field("Name"), { target: { value: "Home Assistant" } });
    fireEvent.change(field("Adresse"), { target: { value: "http://192.168.2.60:8123" } });
    fireEvent.change(field(/Emoji/), { target: { value: "🏠" } });
    expect(screen.getByRole("button", { name: "Kein Symbol" })).toHaveAttribute("aria-pressed", "false");
    submit("Hinzufügen");
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(calls[0].body).toMatchObject({ icon: "🏠", color: null, group: null, host_id: null });
  });

  it("prüft vor dem Senden: javascript: und Bild-Adressen werden am Feld abgelehnt, es geht nichts raus", async () => {
    const { onOpenChange } = renderDialog();
    submit("Hinzufügen");
    expect(await screen.findByText("Bitte gib einen Namen an.")).toBeInTheDocument();
    expect(screen.getByText("Bitte gib eine Adresse an.")).toBeInTheDocument();

    fireEvent.change(field("Name"), { target: { value: "Böse" } });
    fireEvent.change(field("Adresse"), { target: { value: "javascript:alert(document.cookie)" } });
    fireEvent.change(field(/Emoji/), { target: { value: "https://tracker.example/pixel.png" } });
    submit("Hinzufügen");
    expect(await screen.findByText("Die Adresse muss mit http:// oder https:// beginnen.")).toBeInTheDocument();
    expect(screen.getByText(/Wähle ein Symbol aus der Liste/)).toBeInTheDocument();
    expect(field("Adresse")).toHaveAttribute("aria-invalid", "true");
    expect(calls).toEqual([]);
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it("beim Verlassen des Adressfelds wird http:// ergänzt; ein Fehler bleibt stehen, wenn sich nichts ändert", async () => {
    renderDialog();
    fireEvent.change(field("Adresse"), { target: { value: " 192.168.2.1 " } });
    fireEvent.blur(field("Adresse"));
    expect(field("Adresse").value).toBe("http://192.168.2.1");

    fireEvent.change(field("Adresse"), { target: { value: "ftp://nas.local" } });
    submit("Hinzufügen");
    expect(await screen.findByText("Die Adresse muss mit http:// oder https:// beginnen.")).toBeInTheDocument();
    fireEvent.blur(field("Adresse"));
    expect(screen.getByText("Die Adresse muss mit http:// oder https:// beginnen.")).toBeInTheDocument();
  });

  it("lehnt einen Namen aus Füllzeichen und einen mit Zeilentrenner ab, ohne etwas zu senden", async () => {
    renderDialog();
    fireEvent.change(field("Adresse"), { target: { value: "http://192.168.2.1" } });
    fireEvent.change(field("Name"), { target: { value: "\u3164\u3164" } });
    submit("Hinzufügen");
    expect(await screen.findByText("Bitte gib einen Namen an.")).toBeInTheDocument();

    fireEvent.change(field("Name"), { target: { value: "Router\u2028Admin" } });
    submit("Hinzufügen");
    expect(await screen.findByText("Der Name darf keine Zeilenumbrüche oder Steuerzeichen enthalten.")).toBeInTheDocument();

    fireEvent.change(field("Name"), { target: { value: "Router" } });
    fireEvent.change(field(/Gruppe/), { target: { value: "\u3164" } });
    submit("Hinzufügen");
    expect(await screen.findByText("Die Gruppe braucht mindestens ein sichtbares Zeichen.")).toBeInTheDocument();
    expect(calls).toEqual([]);
  });

  it("lehnt Zugangsdaten in der Adresse ab", async () => {
    renderDialog();
    fireEvent.change(field("Name"), { target: { value: "NAS" } });
    fireEvent.change(field("Adresse"), { target: { value: "http://admin:geheim@192.168.2.20" } });
    submit("Hinzufügen");
    expect(await screen.findByText("Bitte keinen Benutzernamen und kein Passwort in die Adresse schreiben.")).toBeInTheDocument();
    expect(calls).toEqual([]);
  });

  it("springt zum ersten Feld mit Fehler (auf dem Handy liegt es sonst vielleicht außerhalb des Bildschirms)", async () => {
    renderDialog();
    submit("Hinzufügen");
    await waitFor(() => expect(field("Name")).toHaveFocus());
    fireEvent.change(field("Name"), { target: { value: "Router" } });
    submit("Hinzufügen");
    await waitFor(() => expect(field("Adresse")).toHaveFocus());
  });

  it("ein Fehler verschwindet, sobald man das Feld ändert", async () => {
    renderDialog();
    submit("Hinzufügen");
    expect(await screen.findByText("Bitte gib einen Namen an.")).toBeInTheDocument();
    fireEvent.change(field("Name"), { target: { value: "Router" } });
    expect(screen.queryByText("Bitte gib einen Namen an.")).toBeNull();
  });

  it("zeigt einen 422 des Servers am passenden Feld", async () => {
    respondWith = () => new Response(JSON.stringify({ detail: [{ type: "value_error", loc: ["body", "url"], msg: "Der Port in der Adresse ist ungültig." }] }), { status: 422 });
    const { onOpenChange } = renderDialog();
    fireEvent.change(field("Name"), { target: { value: "Router" } });
    fireEvent.change(field("Adresse"), { target: { value: "http://192.168.2.1" } });
    submit("Hinzufügen");
    expect(await screen.findByText("Der Port in der Adresse ist ungültig.")).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Hinzufügen" })).not.toBeDisabled();
  });

  it("zeigt andere Fehler (z. B. Obergrenze) als Meldung im Dialog und lässt ihn offen", async () => {
    respondWith = () => new Response(JSON.stringify({ detail: "Es gibt schon 200 Apps – das ist die Obergrenze." }), { status: 409 });
    const { onOpenChange } = renderDialog();
    fireEvent.change(field("Name"), { target: { value: "Router" } });
    fireEvent.change(field("Adresse"), { target: { value: "http://192.168.2.1" } });
    submit("Hinzufügen");
    expect(await screen.findByText("Es gibt schon 200 Apps – das ist die Obergrenze.")).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it("zeigt eine Meldung ohne Feldbezug im festen Fußbereich bei den Knöpfen, nicht im scrollenden Teil", async () => {
    respondWith = () => new Response(JSON.stringify({ detail: "Es gibt schon 200 Apps – das ist die Obergrenze." }), { status: 409 });
    renderDialog();
    fireEvent.change(field("Name"), { target: { value: "Router" } });
    fireEvent.change(field("Adresse"), { target: { value: "http://192.168.2.1" } });
    submit("Hinzufügen");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Es gibt schon 200 Apps");
    // Das Formular ist auf dem Handy mehrere Bildschirme lang: die Meldung darf nicht unten im scrollenden Teil stehen.
    expect(screen.getByTestId("app-form-footer")).toContainElement(alert);
    expect(screen.getByTestId("app-form-body")).not.toContainElement(alert);
  });

  it("die Meldung verschwindet, sobald man etwas ändert, und der Knopf ist wieder frei", async () => {
    respondWith = () => new Response(JSON.stringify({ detail: "Diese App gibt es nicht (mehr)." }), { status: 404 });
    renderDialog({ app: EXISTING });
    submit("Speichern");
    expect(await screen.findByRole("alert")).toHaveTextContent("Diese App gibt es nicht (mehr).");
    fireEvent.change(field("Name"), { target: { value: "Router 2" } });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("button", { name: "Speichern" })).not.toBeDisabled();
  });

  it("schlägt vorhandene Gruppen vor und zeigt die Server (Anzeigename, sonst Kurzname)", () => {
    renderDialog();
    const options = Array.from(document.querySelectorAll("#app-groups option")).map((o) => o.getAttribute("value"));
    expect(options).toEqual(["Netzwerk", "Speicher"]);
    const select = field(/Gehört zu Server/) as unknown as HTMLSelectElement;
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["Kein Server", "Mein NAS", "pi"]);
  });

  it("ohne Server in der Liste gibt es das Feld nicht", () => {
    renderDialog({ hosts: [] });
    expect(screen.queryByLabelText(/Gehört zu Server/)).toBeNull();
  });

  it("Abbrechen schließt, ohne etwas zu senden", () => {
    const { onOpenChange } = renderDialog();
    fireEvent.change(field("Name"), { target: { value: "Router" } });
    fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(calls).toEqual([]);
  });

  it("schließt gleich nach dem Speichern, auch wenn das Neuladen der Übersicht hängt", async () => {
    respondWith = (call) =>
      call.method === "GET"
        ? new Promise<Response>(() => {}) // die Übersicht wartet auf einen hängenden Server
        : new Response(JSON.stringify({ id: "neu", name: "Router" }), { status: 201 });
    const { onOpenChange } = renderDialog({}, { observeOverview: true });
    fireEvent.change(field("Name"), { target: { value: "Router" } });
    fireEvent.change(field("Adresse"), { target: { value: "http://192.168.2.1" } });
    submit("Hinzufügen");
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false), { timeout: 1500 });
  });
});

describe("AppDialog: bearbeiten", () => {
  it("zeigt die Werte der App und speichert nur über PATCH", async () => {
    const { onOpenChange } = renderDialog({ app: EXISTING });
    expect(screen.getByRole("dialog", { name: "App bearbeiten" })).toBeInTheDocument();
    expect(field("Name").value).toBe("Router");
    expect(field("Adresse").value).toBe("http://192.168.2.1");
    expect(screen.getByRole("button", { name: "Router" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Blau" })).toHaveAttribute("aria-pressed", "true");
    expect(field(/Gruppe/).value).toBe("Netzwerk");
    expect((field(/Gehört zu Server/) as unknown as HTMLSelectElement).value).toBe("h1");
    expect(screen.getByLabelText("In einem neuen Tab öffnen")).not.toBeChecked();

    fireEvent.change(field("Name"), { target: { value: "FRITZ!Box" } });
    fireEvent.click(screen.getByRole("button", { name: "Automatisch" }));
    submit("Speichern");
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({ method: "PATCH", url: "/apps/a1" });
    expect(calls[0].body).toMatchObject({ name: "FRITZ!Box", url: "http://192.168.2.1", icon: "router", color: null, group: "Netzwerk", open_in_new_tab: false, host_id: "h1" });
  });

  it("zeigt ein Emoji-Symbol im Emoji-Feld", () => {
    renderDialog({ app: { ...EXISTING, icon: "🏠" } });
    expect(field(/Emoji/).value).toBe("🏠");
    expect(screen.getByRole("button", { name: "Kein Symbol" })).toHaveAttribute("aria-pressed", "false");
  });

  it("öffnet bei jeder App frisch (kein Rest der vorigen Eingabe)", () => {
    const { rerender } = renderDialog({ app: EXISTING });
    fireEvent.change(field("Name"), { target: { value: "Geändert" } });
    const client = new QueryClient();
    rerender(
      <QueryClientProvider client={client}>
        <AppDialog open onOpenChange={() => {}} groups={[]} hosts={HOSTS} app={{ ...EXISTING, id: "a2", name: "NAS" }} />
      </QueryClientProvider>,
    );
    expect(field("Name").value).toBe("NAS");
  });
});
