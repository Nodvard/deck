/**
 * Jeder Code, jeder Schritt und jedes Ergebnis des Update-Helfers hat einen Text. Die Codes kommen zur Testzeit aus den
 * gemeinsamen Vektoren des Helfers (protocol.json ist dort gegen policy.py geprüft, status.json sind echte Status), nicht
 * aus einer Liste, die hier von Hand gepflegt würde. Gegenstück im Backend: backend/tests/test_updater_texts.py.
 */
import { describe, expect, it } from "vitest";

import protocol from "../../../../deploy/updater/tests/vectors/protocol.json";
import status from "../../../../deploy/updater/tests/vectors/status.json";
import {
  FAILED_MANUAL_STEPS,
  HELPER_CODE_TEXTS,
  OUTCOME_TITLES,
  OUTCOME_TONES,
  PRESENCE_TEXTS,
  ROLLBACK_DATA_HINT,
  STAGES,
  STEP_TEXTS,
  codeText,
  outcomeText,
  stageOf,
  stepText,
  type HelperOutcome,
} from "./updaterTexts";

const ALL_CODES = Object.values(protocol.codes).flat();

type StatusDoc = {
  reason: string | null;
  busy: { step: string } | null;
  results: { action: string; from: string | null; to: string | null; outcome: string; code: string | null }[];
};

describe("Texte zum Update-Helfer", () => {
  it("jeder Code aus dem Protokoll des Helfers hat einen eigenen Text, keiner ist übrig", () => {
    expect(ALL_CODES.length).toBeGreaterThan(40);
    for (const code of ALL_CODES) {
      expect(HELPER_CODE_TEXTS[code], code).toBeTruthy();
      expect(codeText(code)).toBe(HELPER_CODE_TEXTS[code]);
    }
    expect(Object.keys(HELPER_CODE_TEXTS).sort()).toEqual([...ALL_CODES].sort());
  });

  it("jeder Schritt hat einen Satz und gehört zu genau einem Abschnitt der Anzeige", () => {
    for (const step of protocol.steps) {
      expect(STEP_TEXTS[step], step).toBeTruthy();
      expect(stepText(step, "update")).toBe(STEP_TEXTS[step]);
      expect(stepText(step, "rollback")).toBeTruthy();
      expect(STAGES.filter((s) => s.steps.includes(step)), step).toHaveLength(1);
      expect(stageOf(step)).toBeGreaterThanOrEqual(0);
    }
    expect(STAGES.flatMap((s) => s.steps).sort()).toEqual([...protocol.steps].sort());
  });

  it("jedes Ergebnis hat Titel, Ton und einen ganzen Satz", () => {
    for (const outcome of protocol.outcomes as HelperOutcome[]) {
      expect(OUTCOME_TITLES[outcome], outcome).toBeTruthy();
      expect(OUTCOME_TONES[outcome], outcome).toBeTruthy();
      for (const action of protocol.actions as ("update" | "rollback")[]) {
        const text = outcomeText({ action, from: "0.7.0", to: "0.7.1", outcome, code: null });
        expect(text, `${action} ${outcome}`).toMatch(/[.“]$/);
        expect(text).not.toContain("undefined");
        expect(text).not.toContain("null");
      }
    }
  });

  it("alle Gründe, Schritte und Codes aus den Status-Vektoren haben einen Text", () => {
    for (const { name, doc } of status.valid as { name: string; doc: StatusDoc }[]) {
      if (doc.reason) expect(HELPER_CODE_TEXTS[doc.reason], `${name}: ${doc.reason}`).toBeTruthy();
      if (doc.busy) expect(STEP_TEXTS[doc.busy.step], `${name}: ${doc.busy.step}`).toBeTruthy();
      for (const result of doc.results) {
        if (result.code) expect(HELPER_CODE_TEXTS[result.code], `${name}: ${result.code}`).toBeTruthy();
        expect(OUTCOME_TITLES[result.outcome as HelperOutcome], `${name}: ${result.outcome}`).toBeTruthy();
        const text = outcomeText({ ...result, action: result.action as "update" | "rollback" });
        // Bei failed_manual und external_change sagt der Satz zum Ergebnis schon alles (der Code wiederholte ihn nur).
        if (result.code && !["failed_manual", "external_change"].includes(result.outcome)) expect(text).toContain(HELPER_CODE_TEXTS[result.code]);
      }
    }
  });

  it("die Gründe der Ansicht (Helfer fehlt, antwortet nicht …) haben einen Text", () => {
    expect(Object.keys(PRESENCE_TEXTS).sort()).toEqual(["invalid", "missing", "proto", "stale", "unsafe"]);
    expect(codeText("finishing")).toMatch(/räumt/);
  });

  it("not_from_registry erklärt den häufigsten Grund (pull ohne up -d) und was hilft", () => {
    const text = HELPER_CODE_TEXTS.not_from_registry;
    expect(text).toContain("ghcr.io/nodvard/deck");
    expect(text).toContain("docker compose pull");
    expect(text).toContain("docker compose up -d");
  });

  it("ein unbekannter Code eines neueren Helfers bekommt einen allgemeinen Text", () => {
    expect(codeText("ganz_neu")).toContain("„ganz_neu“");
    expect(codeText(null)).toBeNull();
    expect(outcomeText({ action: "update", from: "0.7.0", to: "0.7.1", outcome: "rolled_back", code: "ganz_neu" })).toContain("ganz_neu");
  });

  it("gescheiterter Rückweg: der Hinweis auf die Meldung zu den Daten nur, wenn die Daten mitgehen sollten", () => {
    const failed = { action: "rollback" as const, from: "0.7.1", to: "0.7.0", outcome: "rolled_back", code: "exited" };
    // Ohne Daten sagt die Meldung des Dashboards nichts über Daten: kein Verweis darauf.
    expect(outcomeText(failed)).not.toContain("Meldung");
    expect(outcomeText(failed)).not.toContain("Daten");
    expect(outcomeText(failed, { dataRevert: true })).toContain(ROLLBACK_DATA_HINT);
    expect(ROLLBACK_DATA_HINT).toContain("Glocke");
    // Beim Update gibt es keinen solchen Hinweis.
    expect(outcomeText({ ...failed, action: "update" }, { dataRevert: true })).not.toContain(ROLLBACK_DATA_HINT);
  });

  it("nach failed_manual steht da, was du auf dem Server tun kannst", () => {
    expect(FAILED_MANUAL_STEPS.join(" ")).toContain("docker compose ps");
    expect(FAILED_MANUAL_STEPS.join(" ")).toContain("docker compose logs updater");
    expect(outcomeText({ action: "update", from: "0.7.0", to: "0.7.1", outcome: "failed_manual", code: "rollback_failed" })).toContain("0.7.0");
  });
});
