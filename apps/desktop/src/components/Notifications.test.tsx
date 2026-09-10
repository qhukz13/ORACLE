/**
 * Toasts — docs/UI.md §12.
 *
 * Most of these test what the component **does not** do, because that is what the spec is actually
 * about: *"noise trains me to ignore the channel that matters"*. A toast surface is easy to write
 * and easy to ruin, and it is ruined by being helpful — one per tool call, one per indexed file —
 * until the approval that blocks the machine scrolls past unread.
 */

import { fireEvent, render, screen, act } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Notifications, toToasts } from "./Notifications";
import type { Approval, OracleEvent } from "../protocol";

function ev(seq: number, type: string, payload: Record<string, unknown> = {}): OracleEvent {
  return {
    v: 1,
    seq,
    ts: "2026-09-10T10:00:00Z",
    type,
    trace_id: "tr_1",
    payload,
  } as OracleEvent;
}

function approval(id: string, over: Partial<Approval> = {}): Approval {
  return {
    approvalId: id,
    tool: "fs.write",
    tier: "T2",
    decision: "confirm",
    rule: "fs.write outside the workspace",
    ...over,
  } as Approval;
}

const task = (seq: number, status: string, summary = "") =>
  ev(seq, "task.finished", { source: "graph", status, summary });

describe("what it refuses to notify about", () => {
  it("says nothing about tool calls, indexing, retrieval or delegate chatter", () => {
    /* Every one of these is more frequent than the three that matter. A toast per
       `tool.finished` buries the approval that blocks the machine. */
    const noise = [
      ev(1, "tool.started", { tool: "know.search" }),
      ev(2, "tool.finished", { tool: "know.search", ok: true }),
      ev(3, "knowledge.indexed", { files: 12 }),
      ev(4, "delegate.event", { text: "thinking" }),
      ev(5, "message.completed", { text: "done" }),
      ev(6, "term.output", { data: "..." }),
      ev(7, "system.metrics", { cpu: 12 }),
    ];
    expect(toToasts(noise, [])).toEqual([]);
  });

  it("ignores a task that is not part of a graph", () => {
    /* A delegation's internal tasks emit `task.*` too. Without the source filter every
       delegation would raise a fistful of toasts nobody asked for. */
    expect(toToasts([ev(1, "task.finished", { status: "done" })], [])).toEqual([]);
  });

  it("renders nothing at all when there is nothing to say", () => {
    const { container } = render(<Notifications events={[]} approvals={[]} />);
    expect(container.firstChild).toBeNull();
  });
});

describe("what it does notify about", () => {
  it("announces a completed task, and lets it expire", () => {
    vi.useFakeTimers();
    try {
      render(<Notifications events={[task(1, "done", "tests green")]} approvals={[]} />);
      expect(screen.getByText("Task completed")).toBeTruthy();
      act(() => vi.advanceTimersByTime(4100));
      expect(screen.queryByText("Task completed")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps a failure until it is dismissed — a failure nobody saw is a failure nobody fixed", () => {
    vi.useFakeTimers();
    try {
      render(<Notifications events={[task(1, "failed", "3 tests red")]} approvals={[]} />);
      act(() => vi.advanceTimersByTime(60_000));
      expect(screen.getByText("Task failed")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps an approval until it is answered, not until it times out", () => {
    const { rerender } = render(<Notifications events={[]} approvals={[approval("ap_1")]} />);
    expect(screen.getByText("Approval needed")).toBeTruthy();
    // Answered elsewhere: it leaves `approvals`, so the toast goes with it rather than
    // lingering as a prompt for a decision already made.
    rerender(<Notifications events={[]} approvals={[]} />);
    expect(screen.queryByText("Approval needed")).toBeNull();
  });

  it("never carries status by colour alone", () => {
    render(<Notifications events={[task(1, "failed", "boom")]} approvals={[]} />);
    // UI.md §1: the word is the information, the glyph and hue are reinforcement.
    expect(screen.getByText("Task failed")).toBeTruthy();
  });
});

describe("stacking", () => {
  const many = [1, 2, 3, 4, 5].map((i) => task(i, "failed", `failure ${i}`));

  it("shows at most three and collapses the rest", () => {
    render(<Notifications events={many} approvals={[]} />);
    expect(screen.getAllByText("Task failed")).toHaveLength(3);
    expect(screen.getByText("+2 more")).toBeTruthy();
  });

  it("dismisses one without touching the others", () => {
    render(<Notifications events={many} approvals={[]} />);
    fireEvent.click(screen.getAllByLabelText("Dismiss: Task failed")[0]!);
    // One left the stack, so one of the collapsed ones takes its place.
    expect(screen.getAllByText("Task failed")).toHaveLength(3);
    expect(screen.getByText("+1 more")).toBeTruthy();
  });

  it("dismisses all of them, including the collapsed ones", () => {
    const { container } = render(<Notifications events={many} approvals={[]} />);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss all" }));
    expect(container.firstChild).toBeNull();
  });
});

describe("acting on one", () => {
  it("hands the toast back so the host can focus what it is about", () => {
    const onOpen = vi.fn();
    render(<Notifications events={[]} approvals={[approval("ap_1")]} onOpen={onOpen} />);
    fireEvent.click(screen.getByRole("button", { name: "Review" }));
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ kind: "approval" }));
  });

  it("offers no action at all when the host cannot take one", () => {
    render(<Notifications events={[]} approvals={[approval("ap_1")]} />);
    expect(screen.queryByRole("button", { name: "Review" })).toBeNull();
  });
});
