/**
 * The centre stage is switchable (docs/UI.md §2), and the switches these tests pin are
 * the ones with rules attached:
 *
 *   - `Ctrl+1..4` reach the four primary views (§16) — with the AltGr guard, because
 *     Ctrl+Alt+digit is how some layouts type characters, and a stage switch that eats
 *     a character is a keybinding bug nobody reports coherently;
 *   - sending a message auto-switches to Chat — the ONE stage change the app may make
 *     (§21 rule 6), permitted because every caller of submit is the user;
 *   - the briefing's inspect affordance selects a TASK in the inspector. Until
 *     2026-08-28 it pushed the task id into the turn selector, matched nothing, and the
 *     inspector silently showed the latest turn instead — it looked right and was wrong
 *     (the P12-T4 stopgap). These tests are the pin against that regression.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "./store";

vi.mock("./client", () => ({
  OracleClient: class {
    send() {
      return true; // the submit path switches stage only when the socket accepted it
    }
    connect() {}
    close() {}
    dispose() {}
  },
}));

let App: React.ComponentType;

const BRIEFING_WIRE = {
  through_seq: 40,
  since_ts: "2026-08-28T04:00:00.000Z",
  empty: false,
  text: "Since 04:00 …",
  projects: [
    {
      project: "ORACLE",
      status: "active",
      completed: 1,
      failed: 0,
      waiting: 1,
      in_flight: 0,
      cancelled: 0,
      elapsed_s: 60,
      tokens: 0,
      usd: 0,
      needs_you: true,
      more: 0,
      highlights: [
        { id: "tk_1", objective: "fix pipeline timeout", status: "waiting", agent: null, error: null },
      ],
    },
  ],
  system: { restarted_at: null, unclean: false, degraded: [], errors: 0 },
};

/** ok:false for everything — the shape the halt test uses; briefing tests override. */
function stubQuietFetch() {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) }));
}

function stubBriefingFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: unknown) => {
      const url = String(input);
      if (url.startsWith("/api/v1/briefing") && !url.includes("/ack")) {
        return Promise.resolve({ ok: true, json: async () => BRIEFING_WIRE });
      }
      return Promise.resolve({ ok: false, json: async () => ({}) });
    }),
  );
}

/** One graph task in the store, the way the scheduler really announces one. */
function seedTask(taskId: string) {
  useStore.getState().apply({
    v: 1,
    seq: 1,
    ts: "2026-08-28T10:00:00.000Z",
    type: "task.created",
    session_id: null,
    turn_id: null,
    task_id: taskId,
    trace_id: "tr_1",
    payload: {
      source: "graph",
      root_id: "tk_root",
      kind: "delegation",
      depends_on: [],
      objective: "fix pipeline timeout",
    },
  });
}

beforeEach(async () => {
  useStore.getState().reset();
  stubQuietFetch();
  ({ default: App } = await import("./App"));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const selectedTab = () => screen.getByRole("tab", { selected: true }).textContent ?? "";

describe("Ctrl+1..4", () => {
  it("reach tasks, events, memory, and back to chat", () => {
    render(<App />);
    fireEvent.keyDown(window, { key: "2", ctrlKey: true });
    expect(selectedTab()).toContain("Tasks");
    // §17: the empty stage states its absence, never a blank page.
    expect(screen.getByText("No task graphs yet.")).toBeTruthy();

    fireEvent.keyDown(window, { key: "3", ctrlKey: true });
    expect(selectedTab()).toContain("Events");

    fireEvent.keyDown(window, { key: "4", ctrlKey: true });
    expect(selectedTab()).toContain("Memory");

    fireEvent.keyDown(window, { key: "1", ctrlKey: true });
    expect(selectedTab()).toContain("Chat");
  });

  it("does not fire with Alt held — AltGr types characters on some layouts", () => {
    render(<App />);
    fireEvent.keyDown(window, { key: "2", ctrlKey: true, altKey: true });
    expect(selectedTab()).toContain("Chat");
  });

  it("does not fire on a bare digit", () => {
    render(<App />);
    fireEvent.keyDown(window, { key: "2" });
    expect(selectedTab()).toContain("Chat");
  });
});

describe("the one automatic switch", () => {
  it("returns to Chat when a message is sent from another stage", () => {
    useStore.getState().setConnection("online", 0);
    render(<App />);
    fireEvent.keyDown(window, { key: "3", ctrlKey: true });
    expect(selectedTab()).toContain("Events");

    const input = screen.getByLabelText("Message");
    fireEvent.change(input, { target: { value: "run the tests" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(selectedTab()).toContain("Chat");
  });
});

describe("the briefing's inspect affordance", () => {
  it("selects the TASK in the inspector — not a turn, and not silently the wrong thing", async () => {
    stubBriefingFetch();
    seedTask("tk_1");
    render(<App />);

    // A non-empty briefing takes the stage exactly once on first paint (UI.md §7b).
    const inspect = await screen.findByRole("button", {
      name: "Inspect fix pipeline timeout in ORACLE",
    });
    fireEvent.click(inspect);

    await waitFor(() => expect(screen.getByText("TASK")).toBeTruthy());
    expect(screen.getByText("tk_1")).toBeTruthy();
    // The stage did NOT change: selection opened the inspector rail, and only the
    // user's own navigation moves the centre stage (§21 rule 6).
    expect(selectedTab()).toContain("Briefing");
  });

  it("says so when the task is no longer held, instead of showing a turn", async () => {
    stubBriefingFetch();
    // Nothing seeded: tk_1 resolves to no graph task.
    render(<App />);
    const inspect = await screen.findByRole("button", {
      name: "Inspect fix pipeline timeout in ORACLE",
    });
    fireEvent.click(inspect);
    await waitFor(() =>
      expect(screen.getByText(/tk_1 is not in the last 5 graphs/)).toBeTruthy(),
    );
  });
});
