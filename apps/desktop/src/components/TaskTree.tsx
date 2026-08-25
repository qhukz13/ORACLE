/**
 * A task graph as a tree — the minimum a person needs to see what a supervisor is doing
 * (docs/ORCHESTRATION.md §6). The execution visualisation with lanes and timings is
 * Phase 11; this is the list that stops a graph from being invisible until then.
 *
 * Two rules carried from the rest of the UI:
 *
 * - **Nothing optimistic.** The cancel button sends a command and changes nothing; the
 *   row keeps its status until the server says otherwise.
 * - **Evidence is not the same as the worker's claim.** They render differently and are
 *   labelled differently, because the whole verification design rests on the difference
 *   and a UI that blurs it undoes the design at the last possible moment.
 */

import type { Graph, GraphTask } from "../protocol";

const STATUS_LABEL: Record<string, string> = {
  pending: "waiting on dependencies",
  ready: "ready",
  waiting: "NEEDS YOU",
  running: "running",
  succeeded: "done",
  failed: "failed",
  timeout: "timed out",
  // Deliberately different words for deliberately different facts: nobody stopped a
  // skipped task, and a cancelled one did not simply become ineligible.
  skipped: "skipped — an earlier task did not succeed",
  cancelled: "cancelled",
};

export interface TaskTreeProps {
  graphs: Graph[];
  onCancelTask(rootId: string, taskId: string): void;
  onCancelGraph(rootId: string): void;
}

function evidenceLine(task: GraphTask): string | null {
  const evidence = task.evidence ?? {};
  const parts: string[] = [];
  if (typeof evidence["diff_lines"] === "number") parts.push(`${evidence["diff_lines"]} diff lines`);
  if (typeof evidence["harvest_commit"] === "string") {
    parts.push(`kept as ${String(evidence["harvest_commit"]).slice(0, 8)}`);
  }
  const observed = evidence["observed"];
  if (observed && typeof observed === "object") {
    const o = observed as Record<string, unknown>;
    parts.push(`tests ${o["passed"] ?? "?"} passed, ${o["failed"] ?? "?"} failed`);
  }
  const newFailures = evidence["new_failures"];
  if (Array.isArray(newFailures) && newFailures.length > 0) {
    parts.push(`${newFailures.length} NEW failure(s)`);
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}

function TaskRow({
  task,
  rootId,
  onCancel,
}: {
  task: GraphTask;
  rootId: string;
  onCancel(rootId: string, taskId: string): void;
}) {
  const evidence = evidenceLine(task);
  const stoppable = ["pending", "ready", "running", "waiting"].includes(task.status);
  return (
    <li className={`tt-task tt-${task.status}`}>
      <div className="tt-head">
        <span className="tt-id">{task.taskId}</span>
        <span className="tt-kind">{task.kind}</span>
        <span className="tt-status">{STATUS_LABEL[task.status] ?? task.status}</span>
        {stoppable && (
          <button type="button" onClick={() => onCancel(rootId, task.taskId)}>
            cancel
          </button>
        )}
      </div>
      {task.dependsOn.length > 0 && (
        <div className="tt-deps">after {task.dependsOn.join(", ")}</div>
      )}
      {task.summary && <div className="tt-summary">{task.summary}</div>}
      {evidence && <div className="tt-evidence">ORACLE measured: {evidence}</div>}
      {task.claim && <div className="tt-claim">the worker said: “{task.claim}”</div>}
    </li>
  );
}

export function TaskTree({ graphs, onCancelTask, onCancelGraph }: TaskTreeProps) {
  if (graphs.length === 0) return null;
  return (
    <section className="task-trees" aria-label="Task graphs">
      {graphs.map((graph) => {
        const live = graph.tasks.some((t) =>
          ["pending", "ready", "running", "waiting"].includes(t.status),
        );
        return (
          <article key={graph.rootId} className="tt">
            <header className="tt-graph-head">
              <span className="tt-root">{graph.rootId}</span>
              <span className="tt-count">{graph.tasks.length} tasks</span>
              {live && (
                <button type="button" onClick={() => onCancelGraph(graph.rootId)}>
                  stop graph
                </button>
              )}
            </header>
            <ol className="tt-tasks">
              {graph.tasks.map((task) => (
                <TaskRow
                  key={task.taskId}
                  task={task}
                  rootId={graph.rootId}
                  onCancel={onCancelTask}
                />
              ))}
            </ol>
          </article>
        );
      })}
    </section>
  );
}
