/**
 * The agent queue's arithmetic — [UI.md §8](../../../docs/UI.md#8-agent-queue).
 *
 * ```
 * NOW       investigate Asterim auth        4m12s   [cancel]
 * NEXT      run frontend tests                      [skip]
 * WAITING   claude · review changes         3m      [monitor]
 * BLOCKED   git.push → needs approval               [review] ← amber
 * DONE      indexed Obsidian (161 docs)     2m ago
 * ```
 *
 * Kept pure and out of the component for the same reason the graph layout was: the
 * interesting part is the *bucketing*, and it has two traps in it that are worth asserting
 * directly rather than through the DOM.
 *
 * **Trap 1: `WAITING` means the opposite thing on each side of this file.** `TaskStatus.WAITING`
 * is a scheduler word for *waiting on its dependencies* — the task has not started and nobody is
 * doing anything about it. §8's `WAITING` bucket is *someone else is working on it right now*.
 * They are near-opposites, so a task whose status is `waiting` belongs in **NEXT**.
 *
 * **Trap 2: a delegated task is in `graphs` and `delegations` at once, under the same id.**
 * `TaskTree.recorded.test.tsx` records what that costs when it goes unhandled. Here it would show
 * one delegation twice — once as NOW (the graph's `running`) and once as WAITING (the adapter's).
 * The live delegation wins, because it is the row that can name the adapter and offer `[monitor]`.
 */

import type { Approval, Delegation, Graph, GraphTask } from "./protocol";

export type Bucket = "BLOCKED" | "NOW" | "WAITING" | "NEXT" | "DONE";

export interface QueueRow {
  /** Unique within the queue: an approval id or a task id. */
  id: string;
  bucket: Bucket;
  /** What is happening, in the words whoever is doing it used. */
  label: string;
  /** The status word, verbatim from the wire — `failed` and `skipped` stay different words. */
  detail: string;
  /** ISO timestamp the work began, where anything recorded one. */
  startedAt?: string;
  /** ISO timestamp it ended — set on DONE rows, so the age reads "2m ago" not "2m". */
  finishedAt?: string;
  /** Set on rows that belong to a graph, which is what `graph.cancel` needs. */
  rootId?: string;
  /** True for a row that ended badly. DONE is not a synonym for went well. */
  bad?: boolean;
}

/** §8: "`BLOCKED` items always sort to the top." The rest follow the spec's own order. */
const ORDER: Record<Bucket, number> = { BLOCKED: 0, NOW: 1, WAITING: 2, NEXT: 3, DONE: 4 };

const TERMINAL = new Set(["succeeded", "failed", "timeout", "skipped", "cancelled"]);
const BAD = new Set(["failed", "timeout", "cancelled"]);

/** How many finished rows to keep. A queue that never forgets is a log, and §7 is the log. */
const DONE_LIMIT = 6;

/**
 * What a task row says it is doing. The objective is preferred and **never summarised** — the
 * same rule the approval card follows, and the reason `GraphTask.objective` exists at all. It is
 * clipped only for width, with an ellipsis so the clip is visible rather than silent.
 */
function labelOf(task: GraphTask): string {
  const text = task.objective?.trim() || task.kind || task.taskId;
  return text.length > 64 ? `${text.slice(0, 63)}…` : text;
}

export function toQueue(
  graphs: readonly Graph[],
  delegations: readonly Delegation[],
  approvals: readonly Approval[],
): QueueRow[] {
  const rows: QueueRow[] = [];

  // BLOCKED first, and it is not a subset of the graph: an approval can be raised by a plain
  // tool call with no graph behind it. §8 mirrors these into the Confirmation Center, which is
  // where they are actually decided — a one-line row is not enough to approve from (§9).
  for (const a of approvals) {
    rows.push({
      id: a.approvalId,
      bucket: "BLOCKED",
      label: `${a.tool} → needs approval`,
      detail: a.tier,
      bad: false,
    });
  }

  // A delegation is "live" until it carries an outcome. `state` is its own progress word
  // (rendering → awaiting_egress → running → verifying) and belongs in the detail column, not
  // in the bucket decision: all four mean the same thing to a queue, which is "not you".
  const live = new Map<string, Delegation>();
  for (const d of delegations) {
    if (!d.outcome) live.set(d.taskId, d);
  }

  for (const g of graphs) {
    for (const t of g.tasks) {
      const delegated = live.get(t.taskId);
      if (delegated && !TERMINAL.has(t.status)) continue; // trap 2 — the WAITING row below owns it
      const base = {
        id: t.taskId,
        label: labelOf(t),
        detail: t.status,
        startedAt: t.startedAt,
        finishedAt: t.finishedAt,
        rootId: g.rootId,
      };
      if (TERMINAL.has(t.status)) {
        rows.push({ ...base, bucket: "DONE", bad: BAD.has(t.status) });
      } else if (t.status === "running") {
        rows.push({ ...base, bucket: "NOW" });
      } else {
        // pending · ready · waiting — trap 1. All three mean "has not started".
        rows.push({ ...base, bucket: "NEXT" });
      }
    }
  }

  for (const d of live.values()) {
    rows.push({
      id: d.taskId,
      bucket: "WAITING",
      label: `${d.adapter} · ${d.task}`,
      detail: d.state,
    });
  }

  // Within a bucket, order by what that bucket is *for*. DONE answers "what just happened", so
  // it runs newest-first; everything else is work in hand, where a stable id order keeps a row
  // from jumping under the cursor as statuses change.
  rows.sort(
    (a, b) =>
      ORDER[a.bucket] - ORDER[b.bucket] ||
      (a.bucket === "DONE"
        ? (b.finishedAt ?? "").localeCompare(a.finishedAt ?? "")
        : 0) ||
      a.id.localeCompare(b.id),
  );
  const done = rows.filter((r) => r.bucket === "DONE");
  if (done.length <= DONE_LIMIT) return rows;
  const keep = new Set(done.slice(0, DONE_LIMIT).map((r) => r.id));
  return rows.filter((r) => r.bucket !== "DONE" || keep.has(r.id));
}

/** `4m12s` while it runs, `2m ago` once it has stopped. Seconds are dropped past an hour. */
export function elapsed(fromIso: string | undefined, nowMs: number, past = false): string {
  if (!fromIso) return "";
  const started = Date.parse(fromIso);
  if (!Number.isFinite(started)) return "";
  const total = Math.max(0, Math.round((nowMs - started) / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const text = h > 0 ? `${h}h${m}m` : m > 0 ? `${m}m${s.toString().padStart(2, "0")}s` : `${s}s`;
  return past ? `${text} ago` : text;
}
