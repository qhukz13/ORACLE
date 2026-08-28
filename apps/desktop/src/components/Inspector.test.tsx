/**
 * The inspector answers "what did this turn actually do", so these tests are about it
 * showing evidence rather than a summary — and about not offering an Undo the runtime
 * never recorded.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { GraphTask, ToolCall } from "../protocol";
import type { Turn } from "../store";
import { Inspector } from "./Inspector";

function call(over: Partial<ToolCall> = {}): ToolCall {
  return {
    turnId: "t1",
    tool: "git.status",
    tier: "T0",
    args: { path: "C:\\Projects\\Asterim" },
    running: false,
    ok: true,
    durationMs: 84,
    summary: "main is clean.",
    error: null,
    undoId: null,
    ...over,
  };
}

function turn(over: Partial<Turn> = {}): Turn {
  return {
    turnId: "t1",
    sessionId: "s1",
    userText: "is Asterim clean",
    reply: "main is clean.",
    done: true,
    outcome: "completed",
    tools: [call()],
    ...over,
  };
}

describe("what the turn did", () => {
  it("lists every tool with its tier and duration", () => {
    const { container } = render(<Inspector turn={turn()} traceId="tr_abc" onUndo={vi.fn()} />);
    const step = container.querySelector(".ins-steps li") as HTMLElement;
    expect(step.textContent).toContain("git.status");
    expect(step.textContent).toContain("T0");
    expect(step.textContent).toContain("84 ms");
  });

  it("shows the trace id, because it is the join key into the audit log", () => {
    render(<Inspector turn={turn()} traceId="tr_abc" onUndo={vi.fn()} />);
    expect(screen.getByText("tr_abc")).toBeTruthy();
  });

  it("sums the time actually spent in tools", () => {
    const t = turn({ tools: [call({ durationMs: 100 }), call({ durationMs: 250 })] });
    render(<Inspector turn={t} traceId="tr" onUndo={vi.fn()} />);
    expect(screen.getByText("350 ms")).toBeTruthy();
  });

  it("surfaces a failure rather than burying it", () => {
    const t = turn({
      tools: [call({ ok: false, summary: undefined, error: "nothing is staged" })],
    });
    render(<Inspector turn={t} traceId="tr" onUndo={vi.fn()} />);
    expect(screen.getByText("nothing is staged")).toBeTruthy();
  });

  it("says status in words as well as colour", () => {
    render(<Inspector turn={turn({ done: false })} traceId="tr" onUndo={vi.fn()} />);
    expect(screen.getByText(/running/)).toBeTruthy();
  });
});

describe("undo is offered only where the runtime recorded one", () => {
  it("offers nothing when no tool is undoable", () => {
    render(<Inspector turn={turn()} traceId="tr" onUndo={vi.fn()} />);
    expect(screen.queryByText(/^Undo /)).toBeNull();
  });

  it("offers exactly the calls that carry an undo id", () => {
    const t = turn({
      tools: [call(), call({ tool: "git.commit", undoId: "u_1" })],
    });
    render(<Inspector turn={t} traceId="tr" onUndo={vi.fn()} />);
    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(1);
    expect(buttons[0]!.textContent).toContain("git.commit");
  });

  it("does not re-offer an undo that was already used", () => {
    const t = turn({ tools: [call({ tool: "git.commit", undoId: "u_1", undone: true })] });
    render(<Inspector turn={t} traceId="tr" onUndo={vi.fn()} />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("with nothing selected", () => {
  it("says so rather than rendering an empty frame", () => {
    render(<Inspector turn={null} traceId="" onUndo={vi.fn()} />);
    expect(screen.getByText(/Select a turn/)).toBeTruthy();
  });
});

function graphTask(over: Partial<GraphTask> = {}): GraphTask {
  return {
    taskId: "tk_2",
    kind: "delegation",
    status: "failed",
    dependsOn: ["tk_1"],
    objective: "make the regression tests pass",
    role: "implementer",
    agent: "claude",
    attempt: 2,
    maxAttempts: 3,
    startedAt: "2026-08-28T10:00:00.000Z",
    finishedAt: "2026-08-28T10:02:05.000Z",
    cost: { tokens: 14000, usd: 0.42 },
    evidence: { diff_lines: 120, observed: { passed: 40, failed: 3 } },
    claim: "everything passes now",
    supersedes: "tk_0",
    ...over,
  };
}

describe("the task branch (P11-T5)", () => {
  it("grows the task ABOVE the turn rather than replacing it", () => {
    const { container } = render(
      <Inspector turn={turn()} traceId="tr" onUndo={vi.fn()} task={graphTask()} />,
    );
    const text = container.textContent ?? "";
    expect(text.indexOf("TASK")).toBeGreaterThanOrEqual(0);
    expect(text.indexOf("TASK")).toBeLessThan(text.indexOf("TURN"));
    // The objective is verbatim — a summarised objective is one nobody read.
    expect(screen.getByText("make the regression tests pass")).toBeTruthy();
  });

  it("renders a task alone when no turn exists yet", () => {
    render(<Inspector turn={null} traceId="" onUndo={vi.fn()} task={graphTask()} />);
    expect(screen.getByText("TASK")).toBeTruthy();
    expect(screen.queryByText("TURN")).toBeNull();
  });

  it("keeps what ORACLE measured apart from what the worker said", () => {
    const { container } = render(
      <Inspector turn={null} traceId="" onUndo={vi.fn()} task={graphTask()} />,
    );
    expect(screen.getByText("ORACLE MEASURED")).toBeTruthy();
    expect(screen.getByText("diff_lines")).toBeTruthy();
    expect(screen.getByText("120")).toBeTruthy();
    // The claim is a quote in its own section — never mixed into the measurements.
    expect(screen.getByText("THE WORKER SAID")).toBeTruthy();
    const claim = container.querySelector(".ins-claim");
    expect(claim?.textContent).toContain("everything passes now");
  });

  it("says status in ORCHESTRATION.md's words, with attempt and lineage", () => {
    // Evidence without the word "failed" in it, so the status assertion below can only
    // be satisfied by the status row itself.
    render(
      <Inspector
        turn={null}
        traceId=""
        onUndo={vi.fn()}
        task={graphTask({ evidence: { diff_lines: 120 } })}
      />,
    );
    expect(screen.getByText(/failed/)).toBeTruthy();
    expect(screen.getByText("2 of 3")).toBeTruthy();
    expect(screen.getByText("tk_0")).toBeTruthy(); // replaces
    expect(screen.getByText("tk_1")).toBeTruthy(); // after
  });

  it("shows cost only where something measured it", () => {
    render(
      <Inspector turn={null} traceId="" onUndo={vi.fn()} task={graphTask({ cost: undefined })} />,
    );
    // "$0.00" would be a number pretending to be a measurement.
    expect(screen.queryByText("cost")).toBeNull();
  });

  it("says when a task id resolved to nothing, instead of silently showing a turn", () => {
    // The P12-T4 stopgap failed exactly here: a task id fell through to the latest
    // turn and the inspector looked right while showing the wrong thing.
    render(<Inspector turn={turn()} traceId="tr" onUndo={vi.fn()} taskMissing="tk_9" />);
    expect(screen.getByText(/tk_9 is not in the last 5 graphs/)).toBeTruthy();
  });
});
