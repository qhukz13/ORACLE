/**
 * HALT is a four-key chord, and the awkwardness is the point.
 *
 * UI.md §16: *"HALT is deliberately awkward: four keys, so it cannot be hit by accident."*
 * Until 2026-08-26 it was bound to `F1` — one key, the universal help key, immediately
 * beside Esc. HALT cancels every running task, terminates every job object and drops
 * policy to deny-all until a human resumes; a single-key path to that is a trap rather
 * than a shortcut.
 *
 * This is a keybinding test rather than a UI test because the failure mode is silent: the
 * app looks identical either way, and the only symptom is somebody's work stopping when
 * they reached for help.
 */

import { fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const sent: Array<Record<string, unknown>> = [];

vi.mock("./client", () => ({
  OracleClient: class {
    send(msg: Record<string, unknown>) {
      sent.push(msg);
    }
    connect() {}
    close() {}
    dispose() {}
  },
}));

let App: React.ComponentType;

beforeEach(async () => {
  sent.length = 0;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) }));
  ({ default: App } = await import("./App"));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const halts = () => sent.filter((m) => m.type === "halt");

describe("the HALT keybinding", () => {
  it("does NOT fire on F1", () => {
    render(<App />);
    fireEvent.keyDown(window, { key: "F1" });
    expect(halts()).toHaveLength(0);
  });

  it("fires on Ctrl+Alt+Shift+H", () => {
    render(<App />);
    fireEvent.keyDown(window, { key: "H", ctrlKey: true, altKey: true, shiftKey: true });
    expect(halts()).toHaveLength(1);
  });

  it("needs all four keys, not three", () => {
    // Each of these is one modifier short. None may stop the machine.
    render(<App />);
    fireEvent.keyDown(window, { key: "h", ctrlKey: true, altKey: true });
    fireEvent.keyDown(window, { key: "h", ctrlKey: true, shiftKey: true });
    fireEvent.keyDown(window, { key: "h", altKey: true, shiftKey: true });
    fireEvent.keyDown(window, { key: "h" });
    expect(halts()).toHaveLength(0);
  });
});
