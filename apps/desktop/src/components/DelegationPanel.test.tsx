/**
 * The panel is a pure fold of `task.*` / `delegate.event` — these tests feed it the
 * exact payload shapes the DelegationService emits (asserted server-side in
 * tests/test_delegation_service.py) and check the §7 promise: diff, tests, cost,
 * and an honest "not verified" when no verifier ran.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Delegation } from "../protocol";
import { DelegationPanel } from "./DelegationPanel";

function delegation(over: Partial<Delegation> = {}): Delegation {
  return {
    taskId: "dlg_1",
    task: "Fix authentication token refresh in Asterim.",
    adapter: "claude-code",
    state: "finished",
    outcome: "success",
    feed: [
      { kind: "started", text: "claude-sonnet-5", tool: null, fromSubagent: false },
      { kind: "tool_use", text: "", tool: "Read", fromSubagent: false },
      { kind: "finished", text: "", tool: null, fromSubagent: false },
    ],
    result: {
      outcome: "success",
      diff_lines: 42,
      cost_usd: 0.3214,
      workspace: "C:\\Projects\\Asterim\\.oracle\\wt\\dlg_1",
      tests: { ran: true, passed: 12, failed: 0 },
    },
    ...over,
  };
}

describe("DelegationPanel", () => {
  it("renders nothing at all when there are no delegations", () => {
    const { container } = render(<DelegationPanel delegations={[]} onDiscard={vi.fn()} />);
    expect(container.innerHTML).toBe("");
  });

  it("shows the §7 result line: diff, ORACLE's test verdict, cost", () => {
    render(<DelegationPanel delegations={[delegation()]} onDiscard={vi.fn()} />);
    expect(screen.getByText(/42 diff lines/)).toBeTruthy();
    expect(screen.getByText(/tests: 12 passed, 0 failed/)).toBeTruthy();
    expect(screen.getByText(/\$0\.3214/)).toBeTruthy();
  });

  it("says 'not verified' when no verifier ran, instead of implying success", () => {
    const d = delegation({
      result: {
        diff_lines: 1,
        workspace: "x",
        tests: { ran: false, reason: "no verifier wired" },
      },
    });
    render(<DelegationPanel delegations={[d]} onDiscard={vi.fn()} />);
    expect(screen.getByText(/not verified \(no verifier wired\)/)).toBeTruthy();
  });

  it("discard sends the command for the right task and nothing is optimistic", () => {
    const onDiscard = vi.fn();
    render(<DelegationPanel delegations={[delegation()]} onDiscard={onDiscard} />);
    screen.getByText("Discard worktree").click();
    expect(onDiscard).toHaveBeenCalledWith("dlg_1");
    // Still rendered — the card changes only when the server's events say so.
    expect(screen.getByText(/42 diff lines/)).toBeTruthy();
  });

  it("a fallback shows where the packet landed rather than a result line", () => {
    const d = delegation({
      outcome: "fallback",
      result: {
        outcome: "fallback",
        packet_dir: "C:\\p\\.oracle\\handoff\\dlg_1",
        explanation: "'claude' is not on PATH. Delegation falls back to the on-disk Handoff Packet",
      },
    });
    render(<DelegationPanel delegations={[d]} onDiscard={vi.fn()} />);
    expect(screen.getByText(/No agent available/)).toBeTruthy();
    expect(screen.queryByText(/diff lines/)).toBeNull();
  });

  it("a live run shows its state and the tail of the feed", () => {
    const d = delegation({ state: "running", outcome: undefined, result: undefined });
    render(<DelegationPanel delegations={[d]} onDiscard={vi.fn()} />);
    expect(screen.getByText("RUNNING")).toBeTruthy();
    expect(screen.getByText(/⚒ Read/)).toBeTruthy();
  });
});
