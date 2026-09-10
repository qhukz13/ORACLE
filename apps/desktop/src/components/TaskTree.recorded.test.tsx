/**
 * `TaskTree` against a graph nobody drew.
 *
 * `TaskTree.test.tsx` hand-writes its `Graph`, and `protocol.ts` records what that costs:
 *
 * > `dependsOn` — Populated from `task.created` since 2026-08-26 — before that the scheduler never
 * > sent it, so this was always `[]` in the running app **while a test that hand-wrote the field
 * > asserted it rendered**. A list is not a graph without it.
 *
 * A drawn fixture can only assert what somebody believed the wire carries. This one is the wire:
 * 20 events recorded off the event log from the 2026-09-10 `continue ORACLE` run by
 * `scripts/record_graph_fixture.py`, replayed through the **real store reducer** and rendered.
 *
 * The recording earns its place on one property in particular. The delegation service emits its
 * own `task.*` events for the *same task ids* as the graph, without `source: "graph"` — and the
 * real run interleaved them:
 *
 *     1171  task.updated   …-b   {source: graph, status: running}
 *     1174  task.created   …-b   {}                 ← the delegation's, not the graph's
 *     1185  task.finished  …-b   {outcome: expired} ← the delegation's
 *     1186  task.finished  …-b   {source: graph, status: failed}
 *
 * **What the filter actually protects was checked rather than assumed**, by folding this recording
 * both ways. Task counts, statuses and `dependsOn` all survive without it — the delegation reuses
 * the same ids and its `task.finished` carries no `status` to overwrite with. What breaks is
 * `kind`: the delegation's `task.created` has an empty payload, so `a` and `b` go from
 * `delegation` to blank. That is the one assertion below with teeth against this regression, and
 * the first draft of this file did not have it — it claimed the fold "becomes a mess of ten with
 * the wrong statuses", which is not what happens.
 *
 * No hand-written fixture would have contained these events at all, because nobody writes down the
 * events they did not know were there.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TaskTree } from "./TaskTree";
import { useStore } from "../store";
import type { OracleEvent } from "../protocol";

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURE = join(HERE, "../../../../tests/fixtures/graphs/continue-run.json");

const recorded = JSON.parse(readFileSync(FIXTURE, "utf8")) as {
  root_id: string;
  statuses: string[];
  events: OracleEvent[];
};

function replay() {
  useStore.getState().reset();
  for (const ev of recorded.events) useStore.getState().apply(ev);
  return useStore.getState().graphs;
}

beforeEach(() => {
  useStore.getState().reset();
});

describe("a graph folded from real recorded events", () => {
  it("folds to exactly the tasks the graph created, not the delegation's copies", () => {
    /* The recording contains `task.created` twice per delegated task — once from the scheduler
       with `source: "graph"`, once from the delegation service without it. Five tasks in, five
       tasks out. (This one holds either way, since the delegation reuses the ids; the assertion
       with teeth against the filter is `kind`, below.) */
    const graphs = replay();
    expect(graphs).toHaveLength(1);
    expect(graphs[0]!.rootId).toBe(recorded.root_id);
    expect(graphs[0]!.tasks).toHaveLength(5);
  });

  it("keeps each task's kind, which is what the delegation's events actually clobber", () => {
    /* Folding this recording with the `source: "graph"` filter removed was measured: counts,
       statuses and dependsOn are unchanged, and `a` and `b` lose their kind to the delegation's
       empty-payload `task.created`. A graph whose delegations render as blank is a graph that
       cannot say who is doing the work. */
    const byId = Object.fromEntries(replay()[0]!.tasks.map((t) => [t.taskId, t]));
    expect(byId[`${recorded.root_id}-a`]!.kind).toBe("delegation");
    expect(byId[`${recorded.root_id}-b`]!.kind).toBe("delegation");
    expect(byId[`${recorded.root_id}-c`]!.kind).toBe("verify");
    expect(byId[`${recorded.root_id}-e`]!.kind).toBe("report");
  });

  it("carries dependsOn off the wire — the field a hand-written fixture asserted for two weeks", () => {
    const tasks = replay()[0]!.tasks;
    const byId = Object.fromEntries(tasks.map((t) => [t.taskId, t]));
    // c and d both wait on b; e waits on a. a and b start free.
    expect(byId[`${recorded.root_id}-c`]!.dependsOn).toEqual([`${recorded.root_id}-b`]);
    expect(byId[`${recorded.root_id}-d`]!.dependsOn).toEqual([`${recorded.root_id}-b`]);
    expect(byId[`${recorded.root_id}-e`]!.dependsOn).toEqual([`${recorded.root_id}-a`]);
    expect(byId[`${recorded.root_id}-a`]!.dependsOn).toEqual([]);
  });

  it("does not let the delegation's own finish overwrite the graph's status", () => {
    /* Task b finished twice on the wire: `{outcome: "expired"}` from the delegation at seq 1185,
       then `{source: "graph", status: "failed"}` at 1186. The graph's word is the one that
       counts — "expired" is how the delegation ended, "failed" is what the task did. */
    const byId = Object.fromEntries(replay()[0]!.tasks.map((t) => [t.taskId, t]));
    expect(byId[`${recorded.root_id}-a`]!.status).toBe("failed");
    expect(byId[`${recorded.root_id}-b`]!.status).toBe("failed");
    expect(byId[`${recorded.root_id}-c`]!.status).toBe("skipped");
    expect(byId[`${recorded.root_id}-d`]!.status).toBe("skipped");
    expect(byId[`${recorded.root_id}-e`]!.status).toBe("skipped");
  });

  it("renders the real graph, keeping failed and skipped as different words", () => {
    /* The vocabulary assertion from the hand-written suite, now against real data: a run where
       two tasks failed and three never ran must not describe all five the same way. */
    const graphs = replay();
    render(<TaskTree graphs={graphs} onCancelTask={vi.fn()} onCancelGraph={vi.fn()} />);

    expect(screen.getAllByText(/failed/i).length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText(/skipped/i).length).toBeGreaterThanOrEqual(3);
  });

  it("shows the objective the planner actually wrote, not a summary of it", () => {
    /* UI.md's rule for the approval card applies here too: an objective summarised on the way to
       the screen is an objective nobody read. This is Claude's own wording from the live run. */
    const graphs = replay();
    render(<TaskTree graphs={graphs} onCancelTask={vi.fn()} onCancelGraph={vi.fn()} />);
    expect(screen.getByText(/OPEN_QUESTIONS\.md#oq-14/i)).toBeTruthy();
  });
});
