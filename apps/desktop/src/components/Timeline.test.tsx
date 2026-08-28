/**
 * The timeline is the debugging surface, so what these tests protect is its honesty:
 * grouping never reorders the log, the filter never hides a match inside a closed
 * group, and every row that belongs to something is a way into the inspector.
 *
 * Fixtures are the wire shape — full OracleEvent objects, snake_case payloads — the
 * standing rule since TaskTree was caught green on a shape the app cannot produce.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { OracleEvent } from "../protocol";
import { groupEvents, mark, summarise, Timeline } from "./Timeline";

let seq = 0;
function ev(over: Partial<OracleEvent>): OracleEvent {
  seq += 1;
  return {
    v: 1,
    seq,
    ts: "2026-08-28T20:00:00.000Z",
    type: "tool.finished",
    session_id: "s1",
    turn_id: null,
    task_id: null,
    trace_id: `tr_${seq}`,
    payload: {},
    ...over,
  };
}

const STREAM: OracleEvent[] = [
  ev({ type: "turn.started", turn_id: "t1", payload: { text: "why is Asterim auth broken?" } }),
  ev({ type: "tool.finished", turn_id: "t1", payload: { tool: "git.status", ok: true, duration_ms: 84, summary: "3 modified" } }),
  ev({ type: "task.created", task_id: "tk_1", payload: { source: "graph", kind: "delegation", root_id: "tk_root" } }),
  ev({ type: "turn.finished", turn_id: "t1", payload: { outcome: "completed" } }),
  ev({ type: "system.degraded", payload: { component: "ollama" } }),
];

describe("grouping", () => {
  it("folds consecutive events of one turn, and an interleaving starts a new group", () => {
    const groups = groupEvents(STREAM);
    // t1, tk_1, t1 again, system — contiguity, never a re-sort: the log's order is
    // the truth being displayed.
    expect(groups.map((g) => g.kind)).toEqual(["turn", "task", "turn", "system"]);
    expect(groups[0]?.events).toHaveLength(2);
    expect(groups[2]?.events).toHaveLength(1);
  });

  it("names a turn group by what the user said", () => {
    const groups = groupEvents(STREAM);
    expect(groups[0]?.label).toBe("why is Asterim auth broken?");
    expect(groups[1]?.label).toBe("task tk_1");
    expect(groups[3]?.label).toBe("system");
  });
});

describe("marks and summaries", () => {
  it("distinguishes a failed tool from a successful one, in glyph and text", () => {
    expect(mark(ev({ type: "tool.finished", payload: { ok: true } }))).toBe("✓");
    expect(mark(ev({ type: "tool.finished", payload: { ok: false } }))).toBe("✗");
    expect(mark(ev({ type: "task.finished", payload: { status: "timeout" } }))).toBe("✗");
    expect(mark(ev({ type: "task.finished", payload: { status: "succeeded" } }))).toBe("✓");
  });

  it("falls back to sliced payload JSON rather than hiding unknown events", () => {
    const strange = ev({ type: "something.new", payload: { detail: "x".repeat(200) } });
    expect(summarise(strange)).toContain('{"detail"');
    expect(summarise(strange).length).toBeLessThanOrEqual(90);
  });
});

describe("the view", () => {
  it("renders every group and states emptiness rather than showing a blank page", () => {
    const { rerender } = render(<Timeline events={STREAM} onInspect={vi.fn()} />);
    // Twice on purpose: once as the group's label, once as the turn.started row.
    expect(screen.getAllByText("why is Asterim auth broken?").length).toBeGreaterThan(0);
    expect(screen.getByText("system")).toBeTruthy();

    rerender(<Timeline events={[]} onInspect={vi.fn()} />);
    expect(screen.getByText(/Nothing has happened yet/)).toBeTruthy();
  });

  it("filters by substring over type, ids and payload — and says when nothing matches", () => {
    render(<Timeline events={STREAM} onInspect={vi.fn()} />);
    const filter = screen.getByLabelText("Filter the timeline");

    fireEvent.change(filter, { target: { value: "git.status" } });
    expect(screen.getByText("tool.finished")).toBeTruthy();
    expect(screen.queryByText("system.degraded")).toBeNull();

    fireEvent.change(filter, { target: { value: "zzz-no-match" } });
    expect(screen.getByText(/Nothing matches/)).toBeTruthy();
  });

  it("routes a group into the app-wide selection: tasks as tasks, turns as turns", () => {
    const onInspect = vi.fn();
    render(<Timeline events={STREAM} onInspect={onInspect} />);

    fireEvent.click(screen.getByRole("button", { name: "inspect task tk_1" }));
    expect(onInspect).toHaveBeenLastCalledWith({ kind: "task", id: "tk_1" });

    // t1 was split into two groups by the interleaving task event; both point at the
    // same turn, which is why an identical label on both is honest rather than a bug.
    const turnButtons = screen.getAllByRole("button", { name: "inspect turn t1" });
    expect(turnButtons).toHaveLength(2);
    fireEvent.click(turnButtons[0] as HTMLElement);
    expect(onInspect).toHaveBeenLastCalledWith({ kind: "turn", id: "t1" });
  });

  it("gives the system group no inspect affordance, because nothing can be selected", () => {
    render(<Timeline events={[ev({ type: "system.degraded", payload: {} })]} onInspect={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /inspect/ })).toBeNull();
  });
});
