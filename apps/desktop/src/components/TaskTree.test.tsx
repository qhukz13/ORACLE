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

  // -- replanning ------------------------------------------------------------

  const replanned: Graph = {
    rootId: "tk_root",
    tasks: [
      {
        taskId: "fix",
        kind: "delegation",
        status: "failed",
        dependsOn: [],
        summary: "the worker edited the wrong file",
        evidence: { diff_lines: 4 },
      },
      { taskId: "check", kind: "verify", status: "skipped", dependsOn: ["fix"] },
      {
        taskId: "fix-r1",
        kind: "delegation",
        status: "running",
        dependsOn: [],
        supersedes: "fix",
      },
    ],
  };

  it("shows a superseded attempt beside its replacement rather than hiding it", () => {
    renderTree(replanned);
    // Both rows are on the page: replanning is append-only, and the UI is the last place
    // that could quietly rewrite history.
    expect(screen.getByText("fix")).toBeTruthy();
    expect(screen.getByText("fix-r1")).toBeTruthy();
    expect(screen.getByText("the worker edited the wrong file")).toBeTruthy();
    expect(screen.getByText(/replanned after 1 earlier attempt$/)).toBeTruthy();
  });

  it("nests the attempt under the replacement, not beside it", () => {
    const { container } = render(
      <TaskTree graphs={[replanned]} onCancelTask={vi.fn()} onCancelGraph={vi.fn()} />,
    );
    const top = container.querySelectorAll(".tt > .tt-tasks > .tt-task");
    // Two top-level rows - the replacement and the skipped verify - not three.
    expect(top).toHaveLength(2);
    expect([...top].map((li) => li.querySelector(".tt-id")?.textContent)).toEqual([
      "check",
      "fix-r1",
    ]);
    const nested = container.querySelector(".tt-superseded .tt-task .tt-id");
    expect(nested?.textContent).toBe("fix");
  });

  it("does not resurrect the skipped dependent along with the replacement", () => {
    renderTree(replanned);
    // A SKIPPED row stays skipped and stays visible. If the work is still wanted, the
    // replacement plan asked for it as a new row.
    expect(screen.getByText(/skipped — an earlier task did not succeed/)).toBeTruthy();
  });

  it("nests a chain of attempts once, not once per link", () => {
    const { container } = render(
      <TaskTree
        graphs={[
          {
            rootId: "tk_root",
            tasks: [
              { taskId: "a", kind: "delegation", status: "failed", dependsOn: [] },
              {
                taskId: "a-r1",
                kind: "delegation",
                status: "failed",
                dependsOn: [],
                supersedes: "a",
              },
              {
                taskId: "a-r2",
                kind: "delegation",
                status: "failed",
                dependsOn: [],
                supersedes: "a-r1",
              },
            ],
          },
        ]}
        onCancelTask={vi.fn()}
        onCancelGraph={vi.fn()}
      />,
    );
    const top = container.querySelectorAll(".tt > .tt-tasks > .tt-task");
    expect(top).toHaveLength(1);
    expect(top[0]?.querySelector(".tt-id")?.textContent).toBe("a-r2");
    expect(screen.getByText(/replanned after 2 earlier attempts/)).toBeTruthy();
    // Every attempt is still readable - that is what the budget report points at.
    expect(screen.getByText("a")).toBeTruthy();
    expect(screen.getByText("a-r1")).toBeTruthy();
  });

  it("renders a replacement whose attempt it never saw rather than dropping it", () => {
    const { container } = render(
      <TaskTree
        graphs={[
          {
            rootId: "tk_root",
            tasks: [
              {
                taskId: "orphan",
                kind: "delegation",
                status: "running",
                dependsOn: [],
                supersedes: "a-task-this-client-never-saw",
              },
            ],
          },
        ]}
        onCancelTask={vi.fn()}
        onCancelGraph={vi.fn()}
      />,
    );
    expect(container.querySelectorAll(".tt > .tt-tasks > .tt-task")).toHaveLength(1);
    expect(screen.getByText("orphan")).toBeTruthy();
    expect(screen.queryByText(/replanned after/)).toBeNull();
  });
});
