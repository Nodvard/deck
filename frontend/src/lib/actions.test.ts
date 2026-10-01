import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../state/auth";
import { ActionWaitTimeout, isAbortError, isActionRunning, throwIfAborted, waitForAction } from "./actions";
import { ApiError } from "./api";

type Step = { status: number; body?: unknown } | Error;

/** Antwortet der Reihe nach mit `steps` (die letzte bleibt stehen) und merkt sich die URLs. */
function stubSteps(steps: Step[]) {
  const urls: string[] = [];
  let i = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    urls.push(typeof input === "string" ? input : input.toString());
    const step = steps[Math.min(i, steps.length - 1)];
    i += 1;
    if (step instanceof Error) throw step;
    return new Response(step.body === undefined ? null : JSON.stringify(step.body), { status: step.status });
  }));
  return urls;
}

beforeEach(() => {
  vi.useFakeTimers();
  useAuthStore.setState({ accessToken: "tok", user: null, status: "authenticated", mfaToken: null });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("throwIfAborted", () => {
  it("wirft nur bei einem schon abgebrochenen Signal einen AbortError", () => {
    const controller = new AbortController();
    expect(() => throwIfAborted(controller.signal)).not.toThrow();
    expect(() => throwIfAborted(undefined)).not.toThrow();
    controller.abort();
    let caught: unknown;
    try {
      throwIfAborted(controller.signal);
    } catch (err) {
      caught = err;
    }
    expect(isAbortError(caught)).toBe(true);
  });
});

describe("isActionRunning", () => {
  it("approved und executing laufen noch, alles andere ist fertig", () => {
    expect(isActionRunning("executing")).toBe(true);
    expect(isActionRunning("approved")).toBe(true);
    expect(isActionRunning("succeeded")).toBe(false);
    expect(isActionRunning("failed")).toBe(false);
    expect(isActionRunning(undefined)).toBe(false);
  });
});

describe("waitForAction", () => {
  it("fragt alle 3 s nach, bis die Aktion fertig ist", async () => {
    const urls = stubSteps([
      { status: 200, body: { id: "a1", status: "executing" } },
      { status: 200, body: { id: "a1", status: "succeeded", result: { success: true } } },
    ]);
    const done = waitForAction<{ status: string }>("a1");

    await vi.advanceTimersByTimeAsync(2999);
    expect(urls).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(urls).toEqual(["/api/v1/actions/a1"]);
    await vi.advanceTimersByTimeAsync(3000);

    await expect(done).resolves.toMatchObject({ status: "succeeded" });
    expect(urls).toHaveLength(2);
  });

  it("wertet kurze Aussetzer (Netzwerkfehler, 502) nicht als Ergebnis", async () => {
    stubSteps([
      new TypeError("Failed to fetch"),
      { status: 502, body: { detail: "Bad Gateway" } },
      { status: 200, body: { id: "a1", status: "failed", result: { error: "kaputt" } } },
    ]);
    const done = waitForAction<{ status: string }>("a1");
    await vi.advanceTimersByTimeAsync(9000);
    await expect(done).resolves.toMatchObject({ status: "failed" });
  });

  it("gibt bei 4xx sofort auf", async () => {
    stubSteps([{ status: 404, body: { detail: "Unbekannte Aktion." } }]);
    const done = waitForAction("a1");
    const caught = done.catch((err: unknown) => err);
    await vi.advanceTimersByTimeAsync(3000);
    const err = await caught;
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(404);
  });

  it("gibt nach der Höchstdauer mit einem Hinweis auf", async () => {
    stubSteps([{ status: 200, body: { id: "a1", status: "executing" } }]);
    const caught = waitForAction("a1", { maxMs: 10_000 }).catch((err: unknown) => err);
    await vi.advanceTimersByTimeAsync(12_000);
    expect(await caught).toBeInstanceOf(ActionWaitTimeout);
  });

  it("hört beim Abbrechen (Seite verlassen) auf zu fragen", async () => {
    const urls = stubSteps([{ status: 200, body: { id: "a1", status: "executing" } }]);
    const controller = new AbortController();
    const caught = waitForAction("a1", { signal: controller.signal }).catch((err: unknown) => err);
    await vi.advanceTimersByTimeAsync(3000);
    expect(urls).toHaveLength(1);
    controller.abort();
    expect(isAbortError(await caught)).toBe(true);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(urls).toHaveLength(1);
  });
});
