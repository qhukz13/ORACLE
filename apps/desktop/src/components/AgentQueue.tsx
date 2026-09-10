/**
 * The agent queue — [UI.md §8](../../../../docs/UI.md#8-agent-queue).
 *
 * One compact list answering "what is ORACLE doing, what is next, and what is stuck", in the
 * sidebar where it is visible on every stage. It **replaces** the ad-hoc `WAITING ON ME` block
 * that used to sit here: that block was the BLOCKED bucket under another name, and two renderings
 * of one fact in one panel is exactly what OQ-14 just cost the orbital view
 * ([ADR-0029](../../../../docs/DECISIONS.md#adr-0029--the-orbital-view-is-cut)).
 *
 * **Two deviations from §8's sketch, both because the verb does not exist:**
 *
 * - `[skip]` on a NEXT row is written `[cancel]`. The scheduler's only per-task verb is
 *   `graph.cancel`, which on a task that has not started finishes it as `cancelled` and skips its
 *   dependents. Labelling that "skip" would make this row and the task tree call the same event
 *   two different things.
 * - `[review]` navigates to the Confirmation Center; it does not decide. §9's rule is that the
 *   safety surface shows the real action and never a paraphrase, and a one-line queue row is a
 *   paraphrase by construction.
 *
 * Nothing here is optimistic. Every action sends a command and the row changes when the server's
 * events say it did — the house rule from `TaskTree`.
 */

import { useEffect, useState } from "react";

import { elapsed, toQueue } from "../queue";
import type { Bucket, QueueRow } from "../queue";
import type { Approval, Delegation, Graph } from "../protocol";

export interface AgentQueueProps {
  graphs: readonly Graph[];
  delegations: readonly Delegation[];
  approvals: readonly Approval[];
  onCancelTask(rootId: string, taskId: string): void;
  onReview(approvalId: string): void;
  onMonitor(taskId: string): void;
}

/** Buckets whose age is still running. Anything else is either finished or never started. */
const TICKING: ReadonlySet<Bucket> = new Set<Bucket>(["NOW", "WAITING"]);

export function AgentQueue({
  graphs,
  delegations,
  approvals,
  onCancelTask,
  onReview,
  onMonitor,
}: AgentQueueProps) {
  const rows = toQueue(graphs, delegations, approvals);
  const live = rows.some((r) => TICKING.has(r.bucket));

  // §3's cost rule outlived the view it was written for: idle must cost nothing. The interval
  // exists only while something is actually running, so an idle ORACLE re-renders never.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [live]);

  const blocked = rows.filter((r) => r.bucket === "BLOCKED").length;

  return (
    <>
      <h2 className={blocked > 0 ? "attn" : ""}>
        QUEUE {blocked > 0 && <span className="count">{blocked}</span>}
      </h2>
      <ul className="queue">
        {rows.length === 0 && <li className="muted">nothing queued</li>}
        {rows.map((row) => (
          <Row
            key={`${row.bucket}:${row.id}`}
            row={row}
            now={now}
            onCancelTask={onCancelTask}
            onReview={onReview}
            onMonitor={onMonitor}
          />
        ))}
      </ul>
    </>
  );
}

function Row({
  row,
  now,
  onCancelTask,
  onReview,
  onMonitor,
}: {
  row: QueueRow;
  now: number;
  onCancelTask: AgentQueueProps["onCancelTask"];
  onReview: AgentQueueProps["onReview"];
  onMonitor: AgentQueueProps["onMonitor"];
}) {
  const done = row.bucket === "DONE";
  const age = elapsed(done ? row.finishedAt : row.startedAt, now, done);

  return (
    <li className={`q-row q-${row.bucket.toLowerCase()}${row.bad ? " bad" : ""}`}>
      <span className="q-bucket">{row.bucket}</span>
      <span className="q-label" title={row.label}>
        {row.label}
      </span>
      {/* The status word is not decorative: `failed` and `skipped` are different outcomes and
          §11b's vocabulary rule says they must stay different words. */}
      <span className="q-detail">{row.detail}</span>
      <span className="q-age">{age}</span>
      {row.bucket === "BLOCKED" && (
        <button className="ghost q-act" onClick={() => onReview(row.id)}>
          review
        </button>
      )}
      {row.bucket === "WAITING" && (
        <button className="ghost q-act" onClick={() => onMonitor(row.id)}>
          monitor
        </button>
      )}
      {(row.bucket === "NOW" || row.bucket === "NEXT") && row.rootId && (
        <button
          className="ghost q-act"
          onClick={() => onCancelTask(row.rootId!, row.id)}
          aria-label={`Cancel ${row.label}`}
        >
          cancel
        </button>
      )}
    </li>
  );
}
