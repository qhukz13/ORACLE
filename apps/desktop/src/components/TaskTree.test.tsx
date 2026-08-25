/**
 * What a person must be able to read off a running graph: what is happening, what
 * failed, and why something never ran.
 *
 * The assertions worth arguing about are the two vocabulary ones. `SKIPPED` and
 * `CANCELLED` must not render as the same word, and neither must `TIMEOUT` and `FAILED`
 * — the backend keeps those distinctions carefully (docs/ORCHESTRATION.md §2) and a UI
 * that collapses them throws the information away at the last possible moment.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TaskTree } from "./TaskTree";
import type { Graph } from "../protocol";

const graph: Graph = {
  rootId: "tk_root",
  tasks: [
    { taskId: "look", kind: "tool", status: "succeeded", dependsOn: [], summary: "fs.read ok" },
    { taskId: "fix", kind: "delegation", status: "running", dependsOn: ["look"] },
    {
      taskId: "check",
      kind: "verify",
      status: "failed",
      dependsOn: ["fix"],
      summary: "1 test that passed before this work now fails",
      evidence: { observed: { passed: 583, failed: 29 }, new_failures: ["tests.test_x"] },
      claim: "everything passes",
    },
    { taskId: "tell", kind: "report", status: "skipped", dependsOn: ["check"] },
  ],
};

function renderTree(g: Graph = graph) {
  const onCancelTask = vi.fn();
  const onCancelGraph = vi.fn();
  render(<TaskTree graphs={[g]} onCancelTask={onCancelTask} onCancelGraph={onCancelGraph} />);
  return { onCancelTask, onCancelGraph };
}

describe("TaskTree", () => {
  it("renders nothing when there is no graph", () => {
    const { container } = render(
      <TaskTree graphs={[]} onCancelTask={vi.fn()} onCancelGraph={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("shows every task with its dependencies", () => {
    renderTree();
    expect(screen.getByText("look")).toBeTruthy();
    expect(screen.getByText("after look")).toBeTruthy();
    expect(screen.getByText("4 tasks")).toBeTruthy();
  });

  it("explains a skipped task instead of just labelling it", () => {
    renderTree();
    // "skipped" alone reads as a choice somebody made. It was not.
    expect(screen.getByText(/skipped — an earlier task did not succeed/)).toBeTruthy();
  });

  it("does not render cancelled and skipped as the same thing", () => {
    renderTree({
      rootId: "tk_root",
      tasks: [
        { taskId: "a", kind: "tool", status: "cancelled", dependsOn: [] },
        { taskId: "b", kind: "tool", status: "skipped", dependsOn: ["a"] },
      ],
    });
    const cancelled = screen.getByText("cancelled");
    const skipped = screen.getByText(/skipped/);
    expect(cancelled.textContent).not.toEqual(skipped.textContent);
  });

  it("keeps ORACLE's evidence and the worker's claim visibly apart", () => {
    renderTree();
    expect(screen.getByText(/ORACLE measured:.*583 passed, 29 failed/)).toBeTruthy();
    expect(screen.getByText(/1 NEW failure/)).toBeTruthy();
    // The claim is attributed, not presented as a verdict.
    expect(screen.getByText(/the worker said: “everything passes”/)).toBeTruthy();
  });

  it("offers to stop what is still stoppable, and nothing else", () => {
    renderTree();
    const buttons = screen.getAllByRole("button", { name: "cancel" });
    // running + skipped-is-terminal → only `fix` is stoppable here.
    expect(buttons).toHaveLength(1);
  });

  it("sends a cancel without changing the row itself", () => {
    const { onCancelTask } = renderTree();
    fireEvent.click(screen.getByRole("button", { name: "cancel" }));
    expect(onCancelTask).toHaveBeenCalledWith("tk_root", "fix");
    // Nothing optimistic: the status is whatever the server last said.
    expect(screen.getByText("running")).toBeTruthy();
  });

  it("offers to stop the whole graph only while something is live", () => {
    const { onCancelGraph } = renderTree();
    fireEvent.click(screen.getByRole("button", { name: "stop graph" }));
    expect(onCancelGraph).toHaveBeenCalledWith("tk_root");

    render(
      <TaskTree
        graphs={[
          {
            rootId: "tk_done",
            tasks: [{ taskId: "a", kind: "tool", status: "succeeded", dependsOn: [] }],
          },
        ]}
        onCancelTask={vi.fn()}
        onCancelGraph={vi.fn()}
      />,
    );
    expect(screen.queryAllByRole("button", { name: "stop graph" })).toHaveLength(1);
  });
});
