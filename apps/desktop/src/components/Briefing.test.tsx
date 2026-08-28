/**
 * The briefing view (docs/UI.md#7b, docs/PROJECT_STATE.md#6).
 *
 * Two of these are the reason the file exists:
 *
 *   - **rendering does not consume it** — asserted from the component's side, not only
 *     the API's, because the way this rule actually dies is a `useEffect` that
 *     acknowledges on mount;
 *   - **dismissal sends the DISPLAYED `through_seq`** — so work that arrived while the
 *     reader was looking is not marked as seen.
 *
 * The fixture is the shape `GET /api/v1/briefing` really serialises.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Briefing, toBriefing } from "./Briefing";

const WIRE = {
  through_seq: 128,
  since_ts: "2026-08-26T18:04:00.000Z",
  empty: false,
  text: "Since …",
  projects: [
    {
      project: "Asterim",
      status: "active",
      completed: 3,
      failed: 1,
      waiting: 1,
      in_flight: 2,
      cancelled: 0,
      elapsed_s: 2280,
      tokens: 14000,
      usd: 0.42,
      needs_you: true,
      more: 4,
      highlights: [
        {
          id: "tk_1",
          objective: "fix pipeline timeout",
          status: "waiting",
          agent: null,
          error: null,
        },
        {
          id: "tk_2",
          objective: "regression tests",
          status: "failed",
          agent: "claude",
          error: "3 tests still failing",
        },
      ],
    },
  ],
  system: {
    restarted_at: "2026-08-26T04:12:00.000Z",
    unclean: true,
    degraded: ["Ollama is not reachable"],
    errors: 2,
  },
};

const EMPTY = {
  through_seq: 9,
  since_ts: "2026-08-26T18:04:00.000Z",
  empty: true,
  text: "Nothing ran since …",
  projects: [],
  system: { restarted_at: null, unclean: false, degraded: [], errors: 0 },
};

const noop = { onAcknowledge: () => {}, onInspect: () => {}, onOpenProject: () => {} };

describe("toBriefing", () => {
  it("reads the wire shape", () => {
    const data = toBriefing(WIRE);
    // Bound rather than indexed inline: `noUncheckedIndexedAccess` is on, and asserting
    // the element exists first turns "empty array" into a clear failure instead of a
    // confusing undefined-property one.
    const project = data.projects[0];
    expect(project).toBeDefined();
    expect(data.throughSeq).toBe(128);
    expect(project?.inFlight).toBe(2);
    expect(project?.needsYou).toBe(true);
    expect(project?.highlights[1]?.error).toBe("3 tests still failing");
    expect(data.system.unclean).toBe(true);
  });

  it("survives garbage without throwing", () => {
    expect(toBriefing(null).projects).toEqual([]);
    expect(toBriefing({ projects: 7, system: "no" }).system.degraded).toEqual([]);
  });

  it("treats an empty string timestamp as absent", () => {
    // `""` and `null` mean the same thing here and must not render as two states.
    expect(toBriefing({ since_ts: "" }).sinceTs).toBeNull();
  });
});

describe("Briefing", () => {
  it("never acknowledges on render", () => {
    // **The load-bearing rule.** The way it dies is a useEffect that acknowledges on
    // mount, so this asserts from the component's side rather than the API's.
    const onAcknowledge = vi.fn();
    const { rerender } = render(
      <Briefing data={toBriefing(WIRE)} {...noop} onAcknowledge={onAcknowledge} />,
    );
    rerender(<Briefing data={toBriefing(WIRE)} {...noop} onAcknowledge={onAcknowledge} />);
    expect(onAcknowledge).not.toHaveBeenCalled();
  });

  it("dismisses with the sequence it displayed", () => {
    // Not a freshly-read one: work that arrived while the reader was looking must not be
    // marked seen by an acknowledgement of what they actually saw.
    const onAcknowledge = vi.fn();
    render(<Briefing data={toBriefing(WIRE)} {...noop} onAcknowledge={onAcknowledge} />);

    fireEvent.click(screen.getByRole("button", { name: "Dismiss all" }));
    expect(onAcknowledge).toHaveBeenCalledWith(128);
  });

  it("renders counts a person can act on, in order", () => {
    render(<Briefing data={toBriefing(WIRE)} {...noop} />);
    const counts = screen.getByText(/waiting on you/);
    expect(counts.textContent).toContain("1 waiting on you");
    expect(counts.textContent).toContain("1 failed");
    expect(counts.textContent).toContain("2 running");
    expect(counts.textContent).toContain("3 completed");
    // Waiting comes first: one attention channel, one meaning.
    expect(counts.textContent!.indexOf("waiting on you")).toBeLessThan(
      counts.textContent!.indexOf("failed"),
    );
  });

  it("shows elapsed and cost", () => {
    render(<Briefing data={toBriefing(WIRE)} {...noop} />);
    const counts = screen.getByText(/waiting on you/);
    expect(counts.textContent).toContain("38m");
    expect(counts.textContent).toContain("$0.42");
  });

  it("carries the status as a word on every line, not only a colour", () => {
    render(<Briefing data={toBriefing(WIRE)} {...noop} />);
    expect(screen.getByText("waiting")).toBeTruthy();
    expect(screen.getByText("failed")).toBeTruthy();
  });

  it("gives every line an affordance that opens something real", () => {
    // A line with no affordance is a log entry in a costume (UI.md#7b).
    const onInspect = vi.fn();
    render(<Briefing data={toBriefing(WIRE)} {...noop} onInspect={onInspect} />);

    fireEvent.click(
      screen.getByRole("button", { name: "Inspect fix pipeline timeout in Asterim" }),
    );
    expect(onInspect).toHaveBeenCalledWith("tk_1");
  });

  it("opens the project from its heading", () => {
    const onOpenProject = vi.fn();
    render(<Briefing data={toBriefing(WIRE)} {...noop} onOpenProject={onOpenProject} />);

    fireEvent.click(screen.getByRole("button", { name: "Asterim" }));
    expect(onOpenProject).toHaveBeenCalledWith("Asterim");
  });

  it("states what it is not showing", () => {
    // Silent truncation reads as "this is everything".
    render(<Briefing data={toBriefing(WIRE)} {...noop} />);
    expect(screen.getByText(/and 4 more/)).toBeTruthy();
  });

  it("says a crash was a crash", () => {
    // ADR-0025's named risk: a background service failing invisibly. A silent gap in the
    // log looks exactly like an idle night, so the words have to be explicit.
    render(<Briefing data={toBriefing(WIRE)} {...noop} />);
    expect(screen.getByText(/stopped unexpectedly and restarted/)).toBeTruthy();
    expect(screen.getByText("crashed")).toBeTruthy();
  });

  it("names a degradation rather than counting it", () => {
    render(<Briefing data={toBriefing(WIRE)} {...noop} />);
    expect(screen.getByText("Ollama is not reachable")).toBeTruthy();
  });

  it("renders empty as a real state", () => {
    // Not a placeholder, not a skeleton, and never a fabricated summary of nothing.
    render(<Briefing data={toBriefing(EMPTY)} {...noop} />);
    expect(screen.getByRole("status").textContent).toContain("Nothing ran since");
    expect(screen.queryByRole("button", { name: "Dismiss all" })).toBeNull();
  });

  it("omits the system block entirely when there is no system news", () => {
    const quiet = toBriefing({
      ...WIRE,
      system: { restarted_at: null, unclean: false, degraded: [], errors: 0 },
    });
    render(<Briefing data={quiet} {...noop} />);
    expect(screen.queryByText("System")).toBeNull();
  });

  it("renders a project that needs nothing without shouting", () => {
    const calm = toBriefing({
      ...WIRE,
      projects: [{ ...WIRE.projects[0], waiting: 0, needs_you: false, highlights: [] }],
      system: { restarted_at: null, unclean: false, degraded: [], errors: 0 },
    });
    const { container } = render(<Briefing data={calm} {...noop} />);
    expect(container.querySelector(".bf-project.attn")).toBeNull();
  });
});
