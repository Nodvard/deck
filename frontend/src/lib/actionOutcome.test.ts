import { describe, expect, it } from "vitest";

import { actionReason, describeActionOutcome, isActionOutcomeSource } from "./actionOutcome";

/**
 * Aktionen-Seite, Server-Seite und Widget-Knoepfe werteten das
 * Ergebnis einer Gate-Aktion nicht aus ("Bestätigt" bzw. das Rohwort "failed", kein
 * Grund). Die Formen hier sind die echten ActionOut-Felder aus api/v1/actions.py.
 */
describe("describeActionOutcome", () => {
  it("Erfolg", () => {
    expect(describeActionOutcome({ status: "succeeded", result: { success: true } })).toEqual({ tone: "success", text: "Ausgeführt" });
  });

  it("Fehlschlag mit Grund aus result.error", () => {
    expect(describeActionOutcome({ status: "failed", result: { success: false, error: "SSH: Verbindung abgelehnt" } })).toEqual({
      tone: "error", text: "Fehlgeschlagen: SSH: Verbindung abgelehnt",
    });
  });

  it("Fehlschlag ohne Grund", () => {
    expect(describeActionOutcome({ status: "failed", result: {} }).text).toBe("Fehlgeschlagen: unbekannter Fehler");
    expect(describeActionOutcome({ status: "failed" }).text).toBe("Fehlgeschlagen: unbekannter Fehler");
  });

  it("Gate-Sperre mit Begründung aus gate_decision.detail", () => {
    expect(describeActionOutcome({
      status: "denied", gate_decision: { rule: "flap_limit", detail: "Bereits mehrfach in kurzer Zeit versucht." },
    })).toEqual({ tone: "error", text: "Gesperrt: Bereits mehrfach in kurzer Zeit versucht." });
  });

  it("Gate-Sperre ohne Begründung", () => {
    expect(describeActionOutcome({ status: "denied", gate_decision: { rule: "flap_limit" } })).toEqual({
      tone: "error", text: "Von einer Sicherheitsregel gesperrt",
    });
  });

  it("vom Nutzer abgelehnt: Begründung aus user_reason, nicht als Sperre", () => {
    expect(describeActionOutcome({
      status: "denied", gate_decision: { rule: "user:reject", user_reason: "Nicht jetzt" },
    })).toEqual({ tone: "neutral", text: "Abgelehnt: Nicht jetzt" });
  });

  it("Zwischen- und Endzustände ohne Fehler", () => {
    expect(describeActionOutcome({ status: "executing" })).toEqual({ tone: "pending", text: "Läuft noch …" });
    expect(describeActionOutcome({ status: "approved" })).toEqual({ tone: "pending", text: "Läuft noch …" });
    expect(describeActionOutcome({ status: "proposed" })).toEqual({ tone: "pending", text: "Wartet auf Freigabe" });
    expect(describeActionOutcome({ status: "expired" })).toEqual({ tone: "neutral", text: "Abgelaufen" });
    expect(describeActionOutcome({ status: "dismissed" })).toEqual({ tone: "neutral", text: "Verworfen" });
  });

  it("unbekannter Status bleibt erkennbar", () => {
    expect(describeActionOutcome({ status: "weird" }).text).toBe("Unbekannter Zustand (weird)");
  });
});

describe("actionReason", () => {
  it("nur für fehlgeschlagene und gesperrte Aktionen", () => {
    expect(actionReason({ status: "failed", result: { error: "kaputt" } })).toBe("kaputt");
    expect(actionReason({ status: "denied", gate_decision: { detail: "Sperrliste" } })).toBe("Sperrliste");
    expect(actionReason({ status: "denied", gate_decision: { rule: "user:reject", user_reason: "Nein" } })).toBe("Nein");
    expect(actionReason({ status: "succeeded", result: { error: "egal" } })).toBeNull();
    expect(actionReason({ status: "failed", result: { error: "   " } })).toBeNull();
  });
});

describe("isActionOutcomeSource", () => {
  it("erkennt eine ActionOut-Antwort (z. B. im Body einer 403)", () => {
    expect(isActionOutcomeSource({ status: "denied", gate_decision: { rule: "flap_limit" } })).toBe(true);
    expect(isActionOutcomeSource({ status: "failed", result: {} })).toBe(true);
    expect(isActionOutcomeSource({ detail: "Berechtigung fehlt." })).toBe(false);
    expect(isActionOutcomeSource("HTTP 403")).toBe(false);
    expect(isActionOutcomeSource(null)).toBe(false);
  });
});
