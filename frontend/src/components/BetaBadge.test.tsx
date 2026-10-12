import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BETA_HINT, BetaBadge, BetaExplainer, BetaPageNotice, isBeta } from "./BetaBadge";

describe("Kennzeichen „Beta“", () => {
  it("isBeta: nur der Wert `beta` kennzeichnet, alles andere gilt als fertig", () => {
    expect(isBeta("beta")).toBe(true);
    expect(isBeta("stable")).toBe(false);
    expect(isBeta("Beta")).toBe(false);
    expect(isBeta("")).toBe(false);
    expect(isBeta(null)).toBe(false);
    expect(isBeta(undefined)).toBe(false);
  });

  it("der Satz zur Erklärung ist ehrlich, kurz und duzt nicht von oben herab", () => {
    expect(BETA_HINT).toBe("Funktioniert, ist aber noch nicht so gründlich erprobt wie der Rest. Rückmeldungen helfen.");
  });

  it("das Abzeichen sagt „Beta“, erklärt sich im Tooltip und für Vorlese-Programme", () => {
    render(<BetaBadge />);
    const badge = screen.getByTestId("beta-badge");
    expect(badge).toHaveTextContent(/^Beta/);
    expect(badge).toHaveAttribute("title", BETA_HINT);
    expect(badge).toHaveTextContent(BETA_HINT);
  });

  it("steht der Satz ohnehin daneben, wiederholt das Abzeichen ihn nicht", () => {
    render(<BetaBadge tooltip={false} spoken={false} />);
    const badge = screen.getByTestId("beta-badge");
    expect(badge).toHaveTextContent("Beta");
    expect(badge).not.toHaveAttribute("title");
    expect(badge).not.toHaveTextContent(BETA_HINT);
  });

  it("im Menü bleibt nur der Tooltip, damit der Name des Links kurz bleibt", () => {
    render(<BetaBadge tooltip spoken={false} />);
    const badge = screen.getByTestId("beta-badge");
    expect(badge).toHaveAttribute("title", BETA_HINT);
    expect(badge).toHaveTextContent(/^Beta$/);
  });

  it("die Zeile über der Modulseite und der Satz unter den Listen tragen den Hinweis", () => {
    render(<><BetaPageNotice /><BetaExplainer /></>);
    expect(screen.getByTestId("beta-page-notice")).toHaveTextContent(BETA_HINT);
    expect(screen.getByTestId("beta-explainer")).toHaveTextContent(BETA_HINT);
  });
});
