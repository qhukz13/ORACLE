/**
 * The inspector answers "what did this turn actually do", so these tests are about it
 * showing evidence rather than a summary — and about not offering an Undo the runtime
 * never recorded.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ToolCall } from "../protocol";
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
