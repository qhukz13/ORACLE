/**
 * The agent queue, against hand-written state for the edge cases and against the **recorded
 * wire** for the shape a real run actually has.
 *
 * The two traps in `queue.ts` are the reason most of these exist: `WAITING` means opposite things
 * on the two sides of the mapping, and a delegated task arrives twice under one id. Both are
 * asserted on the arithmetic rather than through the DOM, because that is where they live.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AgentQueue } from "./AgentQueue";
import { elapsed, toQueue } from "../queue";
import { useStore } from "../store";
import type { Approval, Delegation, Graph, GraphTask, OracleEvent } from "../protocol";

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURE = join(HERE, "../../../../tests/fixtures/graphs/continue-run.json");
const recorded = JSON.parse(readFileSync(FIXTURE, "utf8")) as {
  root_id: string;
  events: OracleEvent[];
};

function graph(...tasks: Partial<GraphTask>[]): Graph[] {
  return [
    {
      rootId: "tk_root",
      tasks: tasks.map((t, i) => ({
        taskId: `tk_root-${i}`,
        kind: "local",
        status: "pending",
        dependsOn: [],
        ...t,
      })),
    },
  ];
}

const approval: Approval = {
  approvalId: "ap_1",
  tool: "git.push",
  tier: "T3",
  decision: "confirm_strong",
  rule: "egress",
  tainted: false,
  escalated: false,
  args: {},
  preview: {},
  expiresInSec: null,
  waits: true,
  issuedAt: Date.parse("2026-09-10T12:00:00Z"),
};

const delegation: Delegation = {
  taskId: "tk_root-0",
  task: "review changes",
  adapter: "claude",
  state: "running",
  feed: [],
};

describe("bucketing (UI.md §8)", () => {
  it("sorts BLOCKED to the top whatever else is happening", () => {
    /* §8: "BLOCKED items always sort to the top." The approval is passed last and its id sorts
       after the task ids, so nothing but the bucket order can put it first. */
    const rows = toQueue(graph({ status: "running" }, { status: "succeeded" }), [], [approval]);
    expect(rows[0]!.bucket).toBe("BLOCKED");
    expect(rows.map((r) => r.bucket)).toEqual(["BLOCKED", "NOW", "DONE"]);
  });

  it("puts a task whose status is `waiting` in NEXT, not WAITING", () => {
    /* Trap 1. `TaskStatus.WAITING` is the scheduler saying "blocked on dependencies" — nothing is
       happening to it. §8's WAITING bucket is "an external agent has it right now". Reading the
       status word as the bucket name would file an idle task under active work. */
    const rows = toQueue(
      graph({ status: "waiting" }, { status: "ready" }, { status: "pending" }),
      [],
      [],
    );
    expect(rows.map((r) => r.bucket)).toEqual(["NEXT", "NEXT", "NEXT"]);
  });

  it("shows a delegated task once, as the row that can name the adapter", () => {
    /* Trap 2: the same task id is a `running` graph task AND a live delegation. Two rows would
       claim ORACLE is doing the work locally *and* that Claude has it. */
    const rows = toQueue(graph({ status: "running" }), [delegation], []);
    expect(rows).toHaveLength(1);
    expect(rows[0]!.bucket).toBe("WAITING");
    expect(rows[0]!.label).toBe("claude · review changes");
  });

  it("hands the row back to the graph once the delegation ends", () => {
    /* A finished delegation stops owning the id — otherwise a failed delegation would sit in
       WAITING forever while the graph had already moved the task to DONE. */
    const ended: Delegation = { ...delegation, outcome: "failed" };
    const rows = toQueue(
      graph({ status: "failed", finishedAt: "2026-09-10T12:00:00Z" }),
      [ended],
      [],
    );
    expect(rows).toHaveLength(1);
    expect(rows[0]!.bucket).toBe("DONE");
    expect(rows[0]!.bad).toBe(true);
  });

  it("keeps failed and skipped as different words inside DONE", () => {
    const rows = toQueue(graph({ status: "failed" }, { status: "skipped" }), [], []);
    expect(rows.map((r) => r.detail)).toEqual(["failed", "skipped"]);
    expect(rows.map((r) => r.bad)).toEqual([true, false]);
  });

  it("does not summarise the objective, only clips it visibly", () => {
    /* The approval card's rule, applied here: a summarised objective is an objective nobody read.
       Clipping is allowed because the ellipsis says it happened; paraphrasing is not. */
    const long = "x".repeat(200);
    const rows = toQueue(graph({ status: "running", objective: long }), [], []);
    expect(rows[0]!.label.startsWith("x".repeat(63))).toBe(true);
    expect(rows[0]!.label.endsWith("…")).toBe(true);
  });

  it("caps DONE without capping anything that still needs doing", () => {
    const finished = Array.from({ length: 12 }, (_, i) => ({
      status: "succeeded",
      finishedAt: `2026-09-10T12:00:${String(i).padStart(2, "0")}Z`,
    }));
    const rows = toQueue(graph(...finished, { status: "running" }), [], []);
    expect(rows.filter((r) => r.bucket === "DONE")).toHaveLength(6);
    expect(rows.filter((r) => r.bucket === "NOW")).toHaveLength(1);
    // Newest kept, not the first six by id — and shown newest-first, because "what just
    // happened" is the only question DONE answers. The first live run got the *selection* right
    // and the *order* wrong: it kept the six most recent and then listed them by task id, so the
    // oldest of the six sat at the top.
    const kept = rows.filter((r) => r.bucket === "DONE").map((r) => r.finishedAt);
    expect(kept).toContain("2026-09-10T12:00:11Z");
    expect(kept).not.toContain("2026-09-10T12:00:00Z");
    expect(kept).toEqual([...kept].sort().reverse());
    expect(kept[0]).toBe("2026-09-10T12:00:11Z");
  });
});

describe("against the recorded wire", () => {
  it("buckets a real finished graph as five DONE rows, two of them bad", () => {
    /* The same recording `TaskTree.recorded.test.tsx` folds: a `continue ORACLE` run where two
       delegations failed and three tasks were skipped. Nothing is running, so the queue must be
       entirely history — a queue showing NOW rows for a finished graph would be inventing work.
       The delegation's own `task.*` events are in this recording, so trap 2 is exercised against
       real data rather than a fixture somebody drew to have the property. */
    useStore.getState().reset();
    for (const ev of recorded.events) useStore.getState().apply(ev);
    const s = useStore.getState();

    const rows = toQueue(s.graphs, s.delegations, s.approvals);
    expect(rows.every((r) => r.bucket === "DONE")).toBe(true);
    expect(rows).toHaveLength(5);
    expect(rows.filter((r) => r.bad)).toHaveLength(2);
    // One row per task id: the delegation's copies did not become rows of their own.
    expect(new Set(rows.map((r) => r.id)).size).toBe(5);
  });
});

describe("age", () => {
  const t0 = Date.parse("2026-09-10T12:00:00Z");

  it("reads as running time on a live row and as an age on a finished one", () => {
    expect(elapsed("2026-09-10T12:00:00Z", t0 + 252_000)).toBe("4m12s");
    expect(elapsed("2026-09-10T12:00:00Z", t0 + 120_000, true)).toBe("2m00s ago");
  });

  it("drops seconds past an hour and says nothing when nobody recorded a start", () => {
    expect(elapsed("2026-09-10T12:00:00Z", t0 + 3_780_000)).toBe("1h3m");
    expect(elapsed(undefined, t0)).toBe("");
    expect(elapsed("not a date", t0)).toBe("");
  });
});

describe("the view", () => {
  it("offers review on a blocked row and cancel on a running one — and never skip", () => {
    /* §8's sketch says `[skip]`. There is no skip verb: `graph.cancel` is the only per-task
       command, so the button says what the event will say. */
    render(
      <AgentQueue
        graphs={graph({ status: "running", objective: "run the tests" })}
        delegations={[]}
        approvals={[approval]}
        onCancelTask={vi.fn()}
        onReview={vi.fn()}
        onMonitor={vi.fn()}
      />,
    );
    expect(screen.getByText("review")).toBeTruthy();
    expect(screen.getByText("cancel")).toBeTruthy();
    expect(screen.queryByText("skip")).toBeNull();
  });

  it("says nothing is queued rather than drawing an empty frame", () => {
    const { container } = render(
      <AgentQueue
        graphs={[]}
        delegations={[]}
        approvals={[]}
        onCancelTask={vi.fn()}
        onReview={vi.fn()}
        onMonitor={vi.fn()}
      />,
    );
    expect(screen.getByText("nothing queued")).toBeTruthy();
    expect(container.querySelectorAll(".q-row")).toHaveLength(0);
  });

  it("marks the heading as demanding attention only when something is blocked", () => {
    const { container, rerender } = render(
      <AgentQueue
        graphs={graph({ status: "running" })}
        delegations={[]}
        approvals={[]}
        onCancelTask={vi.fn()}
        onReview={vi.fn()}
        onMonitor={vi.fn()}
      />,
    );
    expect(container.querySelector("h2")!.className).toBe("");
    rerender(
      <AgentQueue
        graphs={graph({ status: "running" })}
        delegations={[]}
        approvals={[approval]}
        onCancelTask={vi.fn()}
        onReview={vi.fn()}
        onMonitor={vi.fn()}
      />,
    );
    expect(container.querySelector("h2")!.className).toBe("attn");
  });
});
