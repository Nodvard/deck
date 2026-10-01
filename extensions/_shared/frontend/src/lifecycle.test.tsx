import { cleanup, render } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { useUnmountSignal } from "./lifecycle";

let unmountSignal: (() => AbortSignal) | null = null;

function Probe() {
  unmountSignal = useUnmountSignal();
  return null;
}

afterEach(() => {
  cleanup();
  unmountSignal = null;
});

describe("useUnmountSignal", () => {
  it("bricht erst beim Verlassen der Seite ab", () => {
    const view = render(<Probe />);
    const signal = unmountSignal!();
    expect(signal.aborted).toBe(false);
    view.unmount();
    expect(signal.aborted).toBe(true);
  });

  it("ist im StrictMode nach dem Einhaengen nicht schon abgebrochen", () => {
    const view = render(<StrictMode><Probe /></StrictMode>);
    const signal = unmountSignal!();
    expect(signal.aborted).toBe(false);
    view.unmount();
    expect(signal.aborted).toBe(true);
  });
});
