import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import { useConnectionWarningStore } from "../state/connectionWarning";
import { InsecureConnectionWarning } from "./InsecureConnectionWarning";

const SUCCESS = {
  access_token: "token",
  user: { id: "u1", username: "nico", display_name: null, email: null, is_owner: true, locale: "de", permissions: ["*"] },
};

beforeEach(() => {
  useConnectionWarningStore.getState().reset();
  useAuthStore.setState({ accessToken: null, user: null, status: "anonymous", mfaToken: "mfa-token" });
});
afterEach(() => vi.unstubAllGlobals());

function dismiss() {
  fireEvent.click(screen.getByRole("button", { name: "Verbindungshinweis für diese Sitzung ausblenden" }));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
}

describe("Verbindungswarnung", () => {
  it("blendet den Hinweis auch nach einem Seitenwechsel für die Sitzung aus", () => {
    const view = render(<InsecureConnectionWarning insecure />);
    dismiss();
    view.unmount();
    render(<InsecureConnectionWarning insecure />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(sessionStorage.getItem("deck.insecureConnection.dismissed")).toBe("true");
  });

  it.each(["login", "mfa"])("zeigt die Warnung nach erfolgreichem %s wieder", async (method) => {
    const view = render(<InsecureConnectionWarning insecure />);
    dismiss();
    view.unmount();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(SUCCESS))));
    const auth = useAuthStore.getState();
    if (method === "login") await auth.login("nico", "password");
    else await auth.submitMfa("123456");
    render(<InsecureConnectionWarning insecure />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(sessionStorage.getItem("deck.insecureConnection.dismissed")).toBeNull();
  });

  it.each(["refresh", "failed-login", "mfa-challenge"])("behält das Ausblenden bei %s bei", async (method) => {
    const view = render(<InsecureConnectionWarning insecure />);
    dismiss();
    view.unmount();
    vi.stubGlobal("fetch", vi.fn(async () => method === "refresh"
      ? new Response(JSON.stringify(SUCCESS))
      : method === "mfa-challenge"
        ? new Response(JSON.stringify({ mfa_token: "challenge" }), { status: 202 })
        : new Response(JSON.stringify({ detail: "Falsches Passwort." }), { status: 401 })));
    const auth = useAuthStore.getState();
    if (method === "refresh") await auth.refresh();
    else await auth.login("nico", "password");
    render(<InsecureConnectionWarning insecure />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
