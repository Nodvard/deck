import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PasswordInClearWarning, sendsInClear } from "./passwordWarning";

describe("PasswordInClearWarning", () => {
  it("warnt, wenn die Seite ohne HTTPS über das Netz läuft", () => {
    render(<PasswordInClearWarning location={{ protocol: "http:", hostname: "192.168.2.5" }} alternative="Sicherer ist dann der Weg mit API-Token." />);
    const note = screen.getByTestId("password-in-clear");
    expect(note).toHaveTextContent("Das Passwort geht unverschlüsselt bis zu Nodvard Deck");
    expect(note).toHaveTextContent("Sicherer ist dann der Weg mit API-Token.");
  });

  it("schweigt bei HTTPS und auf diesem Rechner selbst", () => {
    for (const location of [
      { protocol: "https:", hostname: "deck.example" },
      { protocol: "http:", hostname: "localhost" },
      { protocol: "http:", hostname: "127.0.0.1" },
      { protocol: "http:", hostname: "[::1]" },
    ]) {
      const { unmount } = render(<PasswordInClearWarning location={location} />);
      expect(screen.queryByTestId("password-in-clear")).toBeNull();
      unmount();
    }
    expect(sendsInClear({ protocol: "http:", hostname: "bastel-pi" })).toBe(true);
  });
});
