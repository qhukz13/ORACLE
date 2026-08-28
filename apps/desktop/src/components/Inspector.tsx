/**
 * The inspector — docs/UI.md#6, adapted to what exists.
 *
 * The spec describes a **Task** inspector. Until 2026-08-28 there was nothing task-shaped
 * to inspect, so this showed a turn — and the briefing's inspect affordance routed a task
 * id into the *turn* selector, where it matched nothing and the fallback showed the most
 * recent turn instead: it looked like it worked and showed the wrong thing (the P12-T4
 * stopgap, recorded in four documents). Now the task branch exists, grown **above** the
 * turn rather than replacing it, exactly as this header always said it would.
 *
 * The rule that survives intact: **every row is a link to evidence.** The trace id is
 * shown because it is the join key into the audit log and the event stream; a tool card
 * shows the arguments it actually ran with; a task's evidence (what ORACLE measured) is
 * rendered apart from its claim (what the worker said), because the verification design
 * rests on the difference. Nothing here is a summary of something the user cannot then
 * go and read.
 */

import type { GraphTask } from "../protocol";
import type { Turn } from "../store";
import { STATUS_LABEL } from "./TaskTree";

export interface InspectorProps {
  turn: Turn | null;
  traceId: string;
  onUndo(undoId: string): void;
  /** The selected task, when the selection is a task (UI.md §21: one selection model). */
  task?: GraphTask | null;
  /** A task id that resolved to nothing — the store keeps only the last 5 graphs, so an
   *  old briefing line can outlive its graph. Saying so beats silently showing a turn. */
  taskMissing?: string | null;
}

function outcomeLabel(turn: Turn): { text: string; cls: string } {
  if (!turn.done) return { text: "running", cls: "run" };
  switch (turn.outcome) {
    case "completed":
      return { text: "completed", cls: "ok" };
    case "halted":
      return { text: "halted", cls: "halt" };
    case "degraded":
      return { text: "degraded", cls: "wait" };
    case "cancelled":
      return { text: "cancelled", cls: "wait" };
    default:
      return { text: turn.outcome ?? "error", cls: "err" };
  }
}

/** Icon + word for a task status, never colour alone (docs/UI.md §1). The words are
 *  ORCHESTRATION.md §2's, imported from the tree so there is exactly one copy. */
function taskIcon(status: string): string {
  if (status === "running") return "◆";
  if (status === "failed" || status === "timeout") return "✗";
  if (status === "succeeded") return "●";
  return "○";
}

/** One evidence entry, printable. Objects render as JSON because the evidence dict is
 *  the runner's measurement verbatim — reshaping it here would be a summary nobody
 *  audited (same rule as the objective). */
function evidenceValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function TaskSection({ task }: { task: GraphTask }) {
  const evidence = Object.entries(task.evidence ?? {});
  const started = task.startedAt ? Date.parse(task.startedAt) : NaN;
  const finished = task.finishedAt ? Date.parse(task.finishedAt) : NaN;
  const seconds =
    Number.isFinite(started) && Number.isFinite(finished)
      ? Math.max(0, Math.round((finished - started) / 1000))
      : null;
  const cost: string[] = [];
  if (task.cost?.usd) cost.push(`$${task.cost.usd.toFixed(2)}`);
  if (task.cost?.tokens) cost.push(`${task.cost.tokens.toLocaleString()} tok`);

  return (
    <>
      <h2>TASK</h2>
      {/* Verbatim, like everywhere else an objective is shown: a summarised objective is
          one nobody read. */}
      <p className="ins-title">{task.objective || task.taskId}</p>

      <dl className="ins-facts">
        <dt>task</dt>
        <dd className="ins-trace">{task.taskId}</dd>

        <dt>status</dt>
        <dd>
          <span aria-hidden="true">{taskIcon(task.status)}</span>{" "}
          {STATUS_LABEL[task.status] ?? task.status}
        </dd>

        <dt>kind</dt>
        <dd>
          {task.kind}
          {task.role ? ` · ${task.role}` : ""}
          {task.agent ? ` · ${task.agent}` : ""}
        </dd>

        {(task.attempt ?? 1) > 1 && (
          <>
            <dt>attempt</dt>
            <dd>
              {task.attempt}
              {task.maxAttempts ? ` of ${task.maxAttempts}` : ""}
            </dd>
          </>
        )}

        {seconds !== null && (
          <>
            <dt>took</dt>
            <dd>{seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`}</dd>
          </>
        )}

        {/* Absent unless something measured it — "$0.00" would be a number pretending
            to be a measurement (same rule as the tree's graph header). */}
        {cost.length > 0 && (
          <>
            <dt>cost</dt>
            <dd>{cost.join(" · ")}</dd>
          </>
        )}

        {task.dependsOn.length > 0 && (
          <>
            <dt>after</dt>
            <dd>{task.dependsOn.join(", ")}</dd>
          </>
        )}

        {task.supersedes && (
          <>
            <dt>replaces</dt>
            <dd>{task.supersedes}</dd>
          </>
        )}
      </dl>

      {evidence.length > 0 && (
        <>
          <h2>ORACLE MEASURED</h2>
          <dl className="ins-facts">
            {/* A div is the one wrapper the dl content model allows for a dt/dd pair. */}
            {evidence.map(([key, value]) => (
              <div className="ins-pair" key={key}>
                <dt>{key}</dt>
                <dd>{evidenceValue(value)}</dd>
              </div>
            ))}
          </dl>
        </>
      )}

      {task.claim && (
        <>
          <h2>THE WORKER SAID</h2>
          {/* A quote, never a verdict — the whole verification design in one styling
              decision (docs/ORCHESTRATION.md §2). */}
          <blockquote className="ins-claim">“{task.claim}”</blockquote>
        </>
      )}
    </>
  );
}

export function Inspector({ turn, traceId, onUndo, task, taskMissing }: InspectorProps) {
  if (!turn && !task && !taskMissing) {
    return (
      <aside className="inspector" aria-label="Inspector">
        <p className="muted">Select a turn or a task to inspect it.</p>
      </aside>
    );
  }

  if (!turn) {
    return (
      <aside className="inspector" aria-label="Inspector">
        {task && <TaskSection task={task} />}
        {taskMissing && (
          <p className="muted" role="status">
            Task {taskMissing} is not in the last 5 graphs this window holds — its record is
            in the event log.
          </p>
        )}
      </aside>
    );
  }

  const outcome = outcomeLabel(turn);
  const total = turn.tools.reduce((sum, t) => sum + (t.durationMs ?? 0), 0);
  const undoable = turn.tools.filter((t) => t.undoId && !t.undone);

  return (
    <aside className="inspector" aria-label="Inspector">
      {task && <TaskSection task={task} />}
      {taskMissing && (
        <p className="muted" role="status">
          Task {taskMissing} is not in the last 5 graphs this window holds — its record is in
          the event log.
        </p>
      )}
      <h2>TURN</h2>
      <p className="ins-title">{turn.userText || "(no text)"}</p>

      <dl className="ins-facts">
        <dt>status</dt>
        <dd className={`ins-${outcome.cls}`}>
          {/* icon + label, never colour alone (docs/UI.md#1) */}
          <span aria-hidden="true">{turn.done ? "●" : "◆"}</span> {outcome.text}
        </dd>

        <dt>tools</dt>
        <dd>{turn.tools.length || "none"}</dd>

        <dt>tool time</dt>
        <dd>{total ? `${total} ms` : "—"}</dd>

        <dt>trace</dt>
        {/* The join key into the audit log and the event stream. */}
        <dd className="ins-trace">{traceId || "—"}</dd>
      </dl>

      {turn.tools.length > 0 && (
        <>
          <h2>WHAT IT RAN</h2>
          <ol className="ins-steps">
            {turn.tools.map((call, i) => (
              <li key={i} className={call.ok === false ? "ins-err" : undefined}>
                <span aria-hidden="true">{call.running ? "◆" : call.ok ? "✓" : "✗"}</span>{" "}
                <code>{call.tool}</code>
                {call.tier && <span className="tier-chip"> {call.tier}</span>}
                {call.durationMs !== undefined && (
                  <span className="muted"> {call.durationMs} ms</span>
                )}
                {call.summary && <div className="muted ins-detail">{call.summary}</div>}
                {call.error && <div className="ins-detail ins-err">{call.error}</div>}
              </li>
            ))}
          </ol>
        </>
      )}

      {undoable.length > 0 && (
        <>
          <h2>UNDO</h2>
          <ul className="ins-undo">
            {undoable.map((call, i) => (
              <li key={i}>
                <button className="ghost" onClick={() => onUndo(call.undoId as string)}>
                  Undo {call.tool}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </aside>
  );
}
