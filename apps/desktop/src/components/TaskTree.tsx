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
 * - **Nothing is erased, because the event log does not erase.** A replanned attempt is
 *   shown collapsed *under* its replacement (docs/ORCHESTRATION.md §4), never hidden. A
 *   tree that dropped the failed attempt would be the one place in the whole design where
 *   history gets rewritten, and it would be the place a person actually looks.
 */

import { depth, rankByLongestPath } from "../graph/rank";
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

/** How many stages deep the graph is, by longest path (docs/UI.md §6b). */
function stages(tasks: GraphTask[]): number {
  return depth(rankByLongestPath(tasks, { id: (t) => t.taskId, dependsOn: (t) => t.dependsOn }));
}

/** Which stage a task sits in, or undefined if it is not in this graph. */
function stageOf(tasks: GraphTask[], taskId: string): number | undefined {
  const ranked = rankByLongestPath(tasks, {
    id: (t) => t.taskId,
    dependsOn: (t) => t.dependsOn,
  });
  return ranked.find((r) => r.node.taskId === taskId)?.column;
}

/** Wall-clock across the graph: earliest start to latest finish, or to now if it is live.
 *
 *  Summing the tasks would be wrong and would flatter the machine — a graph that ran four
 *  things in parallel for one minute took one minute, not four. */
function elapsed(tasks: GraphTask[]): string | null {
  const starts = tasks.map((t) => t.startedAt).filter(Boolean) as string[];
  if (starts.length === 0) return null;
  const first = Math.min(...starts.map((s) => Date.parse(s)));
  const ends = tasks.map((t) => t.finishedAt).filter(Boolean) as string[];
  const running = tasks.some((t) => ["running", "ready", "pending", "waiting"].includes(t.status));
  const last = running || ends.length === 0 ? Date.now() : Math.max(...ends.map((s) => Date.parse(s)));
  const seconds = Math.max(0, Math.round((last - first) / 1000));
  if (!Number.isFinite(seconds)) return null;
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

/** What the graph cost, or null if nothing measured.
 *
 *  Null rather than zero, deliberately. A graph of local tool calls has no measured cost,
 *  and rendering "$0.00" would put a number in front of somebody that looks like a
 *  measurement and is an absence. */
function totalCost(tasks: GraphTask[]): string | null {
  const priced = tasks.filter((t) => t.cost);
  if (priced.length === 0) return null;
  const usd = priced.reduce((sum, t) => sum + (t.cost?.usd ?? 0), 0);
  const tokens = priced.reduce((sum, t) => sum + (t.cost?.tokens ?? 0), 0);
  const parts: string[] = [];
  if (usd > 0) parts.push(`$${usd.toFixed(2)}`);
  if (tokens > 0) parts.push(`${tokens.toLocaleString()} tok`);
  return parts.length > 0 ? parts.join(" · ") : null;
}

function TaskRow({
  task,
  rootId,
  stage,
  onCancel,
  superseded = [],
}: {
  task: GraphTask;
  rootId: string;
  /** Longest-path column: how many stages must complete before this one can start. */
  stage?: number;
  onCancel(rootId: string, taskId: string): void;
  /** The attempts this row replaced, newest first. Rendered inside this row so a replan
   *  reads as one story rather than as several unrelated failures. */
  superseded?: GraphTask[];
}) {
  const evidence = evidenceLine(task);
  const stoppable = ["pending", "ready", "running", "waiting"].includes(task.status);
  return (
    <li className={`tt-task tt-${task.status}`}>
      <div className="tt-head">
        {stage !== undefined && (
          <span className="tt-stage" title="cannot start before this many stages complete">
            {stage + 1}
          </span>
        )}
        <span className="tt-id">{task.taskId}</span>
        <span className="tt-kind">{task.kind}</span>
        {task.role && <span className="tt-role">{task.role}</span>}
        {/* Absent, not em-dashed: a TOOL task genuinely has no agent, and a placeholder
            would read as one whose name we failed to fetch. */}
        {task.agent && <span className="tt-agent">{task.agent}</span>}
        <span className="tt-status">{STATUS_LABEL[task.status] ?? task.status}</span>
        {/* A RETRY of the same row, which §6b draws differently from a REPLAN: a retry is
            the same task trying again, a replan is a different task replacing it. */}
        {(task.attempt ?? 1) > 1 && (
          <span className="tt-attempt">
            attempt {task.attempt}
            {task.maxAttempts ? ` of ${task.maxAttempts}` : ""}
          </span>
        )}
        {stoppable && (
          <button type="button" onClick={() => onCancel(rootId, task.taskId)}>
            cancel
          </button>
        )}
      </div>
      {/* Verbatim. An objective summarised on the way to the screen is an objective
          nobody read — the same rule the graph approval card follows. */}
      {task.objective && <div className="tt-objective">{task.objective}</div>}
      {task.dependsOn.length > 0 && (
        <div className="tt-deps">after {task.dependsOn.join(", ")}</div>
      )}
      {task.summary && <div className="tt-summary">{task.summary}</div>}
      {evidence && <div className="tt-evidence">ORACLE measured: {evidence}</div>}
      {task.claim && <div className="tt-claim">the worker said: “{task.claim}”</div>}
      {superseded.length > 0 && (
        <details className="tt-superseded">
          <summary>
            replanned after {superseded.length} earlier attempt
            {superseded.length > 1 ? "s" : ""}
          </summary>
          <ol className="tt-tasks">
            {superseded.map((attempt) => (
              <TaskRow
                key={attempt.taskId}
                task={attempt}
                rootId={rootId}
                onCancel={onCancel}
              />
            ))}
          </ol>
        </details>
      )}
    </li>
  );
}

/** Fold a replanned graph into what a person reads top to bottom: the replacement is the
 *  row, and the attempts it replaced hang off it, newest first.
 *
 *  Two rules, both of which exist because breaking either loses a row:
 *
 *  - **First replacement wins the attempt.** One replan may author several tasks; showing
 *    the failed row again under each of them would read as several failures.
 *  - **A chain nests once.** `a → a' → a''` renders as one row with two collapsed
 *    attempts, not as `a''` at the top and `a'` *also* at the top carrying `a`.
 *
 *  A task that supersedes something this client has never seen still renders on its own:
 *  a half-known graph shown as if it were whole is worse than a gap. */
function arrange(tasks: GraphTask[]): { task: GraphTask; superseded: GraphTask[] }[] {
  const byId = new Map(tasks.map((t) => [t.taskId, t]));
  const replacedBy = new Map<string, string>();
  for (const task of tasks) {
    if (task.supersedes && byId.has(task.supersedes) && !replacedBy.has(task.supersedes)) {
      replacedBy.set(task.supersedes, task.taskId);
    }
  }
  const rows: { task: GraphTask; superseded: GraphTask[] }[] = [];
  for (const task of tasks) {
    if (replacedBy.has(task.taskId)) continue; // it hangs under its replacement instead
    const superseded: GraphTask[] = [];
    let current = task;
    for (;;) {
      const previous = current.supersedes ? byId.get(current.supersedes) : undefined;
      // Only follow the link this row actually owns, and never twice: the ids come from
      // the server but the walk is ours, and a cycle here would hang the render.
      if (!previous || replacedBy.get(previous.taskId) !== current.taskId) break;
      superseded.push(previous);
      current = previous;
    }
    rows.push({ task, superseded });
  }
  return rows;
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
              <span className="tt-count">
                {graph.tasks.length} task{graph.tasks.length === 1 ? "" : "s"} ·{" "}
                {stages(graph.tasks)} stage{stages(graph.tasks) === 1 ? "" : "s"}
              </span>
              {elapsed(graph.tasks) && <span className="tt-elapsed">{elapsed(graph.tasks)}</span>}
              {/* Cost is absent unless something measured it. A graph of local tool calls
                  costs nothing anybody counted, and "$0.00" would be a number pretending
                  to be a measurement. */}
              {totalCost(graph.tasks) && <span className="tt-cost">{totalCost(graph.tasks)}</span>}
              {live && (
                <button type="button" onClick={() => onCancelGraph(graph.rootId)}>
                  stop graph
                </button>
              )}
            </header>
            <ol className="tt-tasks">
              {arrange(graph.tasks).map(({ task, superseded }) => (
                <TaskRow
                  key={task.taskId}
                  task={task}
                  stage={stageOf(graph.tasks, task.taskId)}
                  superseded={superseded}
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
