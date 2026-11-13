import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ExtensionConfigPage } from "../../../../frontend/src/routes/settings/ExtensionConfigPage";
import { useAuthStore } from "../../../../frontend/src/state/auth";
import schema from "../../settings.schema.json";

interface ApiCall { url: string; method: string; body: unknown }

function mockApi(initial: Record<string, unknown>, result: object = {}) {
  let values = { ...initial };
  const calls: ApiCall[] = [];
  const secrets = schema["x-secrets"].map((entry) => ({ ...entry, is_set: false, item: null }));
  const settings = () => ({ schema, values, secrets });
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input).replace(/^\/api\/v1/, "");
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ url, method, body });
    let data: unknown;
    if (url === "/extensions/shield") data = { id: "shield", name: "Nodvard Shield", state: "enabled", version: "1" };
    else if (url === "/extensions/shield/settings") {
      if (method === "PUT") values = { ...values, ...body.values };
      data = settings();
    } else if (url.startsWith("/ext/shield/ai/models?")) data = { options: [], error: "Keine Modellliste verfügbar. Modellname von Hand eintragen." };
    else if (url.startsWith("/ext/shield/ai/test?") && method === "POST") data = {
      ok: true, latency_ms: 17, model: "demo-model", message: "Kurze Anfrage beantwortet.",
      models: [{ value: "demo-model", label: "Demo-Modell" }], models_error: null, ...result,
    };
    else if (url === "/extensions/shield/secrets" && method === "PUT") {
      const slot = secrets.find((entry) => entry.label === body.label);
      if (slot) slot.is_set = true;
      data = null;
    } else if (url === "/hosts") data = [];
    else throw new Error("Unerwarteter Aufruf: " + method + " " + url);
    return new Response(JSON.stringify(data), { status: 200 });
  }));
  return calls;
}

function renderSettings() {
  return render(<MemoryRouter initialEntries={["/settings/extensions/shield"]}><Routes><Route path="/settings/extensions/:extId" element={<ExtensionConfigPage />} /></Routes></MemoryRouter>);
}

function field(name: string): HTMLElement {
  const element = document.getElementById("f-" + name);
  if (!element) throw new Error("Feld fehlt: " + name);
  return element;
}

beforeEach(() => {
  useAuthStore.setState({ accessToken: "test", status: "authenticated", mfaToken: null, user: { id: "demo", username: "demo", display_name: "Demo", email: null, is_owner: true, locale: "de", permissions: ["*"] } });
});

describe("Nodvard KI einrichten", () => {
  it("startet mit Ollama und speichert beim Anbieterwechsel nur die sichtbaren Entwürfe", async () => {
    const calls = mockApi({ ollama_url: "http://127.0.0.1:11434", openai_compat_url: "http://127.0.0.1:1234/v1" });
    renderSettings();
    await screen.findByRole("button", { name: "Verbindung testen" });
    expect(field("ai_provider")).toHaveValue("ollama");
    expect(document.getElementById("f-openai_compat_url")).toBeNull();
    fireEvent.change(field("ollama_url"), { target: { value: "ungültig" } });
    fireEvent.change(field("ai_provider"), { target: { value: "openai_compat" } });
    expect(field("openai_compat_url")).toHaveValue("http://127.0.0.1:1234/v1");
    expect(document.getElementById("f-ollama_url")).toBeNull();
    const oldSecret = schema["x-secrets"].find((entry) => entry["x-secret-bound-to"].includes("ollama_url"))!.label;
    expect(screen.queryByTestId("secret-" + oldSecret)).toBeNull();
    expect(screen.getByTestId("secret-shield-openai-compat-key")).toBeInTheDocument();
    const save = screen.getAllByRole("button", { name: "Speichern" })[0];
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await screen.findByText("Einstellungen gespeichert.");
    expect(calls.find((call) => call.method === "PUT")?.body).toEqual({ values: { ai_provider: "openai_compat" } });
    fireEvent.change(field("ai_provider"), { target: { value: "ollama" } });
    expect(field("ollama_url")).toHaveValue("http://127.0.0.1:11434");
  });

  it("testet den gespeicherten Anbieter, zeigt Antwortzeit und Modelle und sperrt bei Änderungen", async () => {
    const calls = mockApi({ ai_provider: "openai_compat", openai_compat_url: "http://127.0.0.1:1234/v1", openai_compat_model: "demo-model" });
    renderSettings();
    const test = await screen.findByRole("button", { name: "Verbindung testen" });
    fireEvent.click(test);
    expect(await screen.findByTestId("test-result")).toHaveTextContent("Antwortzeit: 17 ms · Modell: demo-model");
    expect(screen.getByTestId("test-result")).toHaveTextContent("Verfügbare Modelle: Demo-Modell");
    expect(calls.find((call) => call.method === "POST")).toEqual({ url: "/ext/shield/ai/test?which=primary", method: "POST", body: undefined });
    fireEvent.change(field("openai_compat_url"), { target: { value: "http://127.0.0.1:5678/v1" } });
    expect(test).toBeDisabled();
    expect(screen.getByText(/Erst speichern, dann testen/)).toBeInTheDocument();
    expect(screen.queryByTestId("test-result")).toBeNull();
  });

  it("kann den Ersatzserver prüfen und zeigt einen Fehler der Modellliste getrennt", async () => {
    const calls = mockApi({ ai_provider: "openai_compat", openai_compat_url: "http://127.0.0.1/v1", openai_compat_failover_url: "http://127.0.0.2/v1" }, { models: [], models_error: "Die Modellliste wird nicht unterstützt." });
    renderSettings();
    const test = await screen.findByRole("button", { name: "Verbindung testen" });
    fireEvent.change(screen.getByLabelText("Server für den Test"), { target: { value: "failover" } });
    fireEvent.click(test);
    const result = await screen.findByTestId("test-result");
    expect(result).toHaveAttribute("role", "status");
    expect(result).toHaveTextContent("Modellliste nicht verfügbar: Die Modellliste wird nicht unterstützt.");
    expect(calls.find((call) => call.method === "POST")?.url).toBe("/ext/shield/ai/test?which=failover");
  });

  it.each(["Zugangsdaten abgelehnt. API-Schlüssel prüfen.", "Keine Antwort. Adresse und Erreichbarkeit prüfen."])("zeigt Testfehler verständlich an", async (message) => {
    mockApi({ ai_provider: "openai_compat", openai_compat_url: "http://127.0.0.1/v1" }, { ok: false, message, models: [] });
    renderSettings();
    fireEvent.click(await screen.findByRole("button", { name: "Verbindung testen" }));
    expect(await screen.findByTestId("test-result")).toHaveAttribute("role", "alert");
    expect(screen.getByTestId("test-result")).toHaveTextContent(message);
  });

  it("warnt für öffentliche Haupt- und Ersatzadressen, aber nicht für ausgeblendete Anbieter", async () => {
    mockApi({ ai_provider: "openai_compat", ollama_url: "https://hidden.invalid", openai_compat_url: "https://203.0.113.10/v1", openai_compat_failover_url: "https://api.invalid/v1" });
    renderSettings();
    await screen.findByRole("button", { name: "Verbindung testen" });
    expect(screen.getAllByText("Daten verlassen dein Netz")).toHaveLength(2);
    fireEvent.change(field("openai_compat_url"), { target: { value: "http://[::1]/v1" } });
    fireEvent.change(field("openai_compat_failover_url"), { target: { value: "http://192.168.0.1/v1" } });
    expect(screen.queryByText("Daten verlassen dein Netz")).toBeNull();
  });

  it("hinterlegt das Token ausschließlich über den Tresor", async () => {
    const calls = mockApi({ ai_provider: "openai_compat", openai_compat_url: "http://127.0.0.1/v1" });
    renderSettings();
    const row = await screen.findByTestId("secret-shield-openai-compat-key");
    const token = ["demo", "value", String(42)].join("-");
    fireEvent.change(row.querySelector("input")!, { target: { value: token } });
    fireEvent.click(row.querySelector("button")!);
    await waitFor(() => expect(row).toHaveTextContent("Hinterlegt"));
    const saved = calls.find((call) => call.url === "/extensions/shield/secrets");
    expect(saved?.body).toEqual({ label: "shield-openai-compat-key", value: token });
    expect(calls.some((call) => call.url.includes(token))).toBe(false);
    expect(row.querySelector("input")).toBeNull();
  });
});
