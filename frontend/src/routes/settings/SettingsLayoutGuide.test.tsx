import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { useAuthStore } from "../../state/auth";
import { SettingsLayout } from "./SettingsLayout";

/** Unten in der Einstellungs-Navigation steht die Anleitung, als Link nach außen erkennbar. */
beforeEach(() => {
  useAuthStore.setState({
    accessToken: "tok", status: "authenticated", mfaToken: null,
    user: { id: "u1", username: "nico", display_name: "Nico", email: null, is_owner: false, locale: "de", permissions: [] },
  });
});

function renderNav() {
  render(
    <MemoryRouter initialEntries={["/settings/account"]}>
      <Routes><Route path="/settings/*" element={<SettingsLayout />} /></Routes>
    </MemoryRouter>,
  );
  return within(screen.getByRole("navigation", { name: "Einstellungen" }));
}

describe("Einstellungen: Eintrag „Anleitung“", () => {
  it("steht für jede angemeldete Person ganz unten und öffnet die Anleitung in einem neuen Tab", () => {
    const nav = renderNav();
    const link = nav.getByRole("link", { name: /Anleitung/ });
    expect(link).toHaveAttribute("href", "https://github.com/nodvard/deck/blob/main/docs/11-ERST-EINRICHTUNG.md");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    // Letzter Eintrag der Liste, hinter „Über Nodvard Deck“
    const items = nav.getAllByRole("listitem");
    expect(items[items.length - 1]).toContainElement(link);
    expect(items[items.length - 2].textContent).toContain("Über Nodvard Deck");
  });

  it("ist als externer Link erkennbar (Symbol und Hinweis für Screenreader)", () => {
    const link = renderNav().getByRole("link", { name: /Anleitung/ });
    expect(link.querySelector("svg.lucide-external-link")).not.toBeNull();
    expect(link.textContent).toContain("öffnet in neuem Tab");
  });

  it("ist kein Bereich der Einstellungen: die Seite bleibt dieselbe", () => {
    const nav = renderNav();
    expect(nav.getByRole("link", { name: /Mein Konto/ })).toHaveAttribute("href", "/settings/account");
    expect(nav.getByRole("link", { name: /Anleitung/ }).getAttribute("href")).not.toContain("/settings/");
  });
});
