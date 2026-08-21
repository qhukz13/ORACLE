/**
 * The inspector — docs/UI.md#6, adapted to what exists.
 *
 * The spec describes a **Task** inspector, and tasks are a Phase 6/7 concept: there is
 * no delegation, no worktree and no multi-step plan to inspect yet. What there *is* is
 * a turn, and the questions are the same ones: what did it decide, what did it run, how
 * long did it take, and can I get to the evidence.
 *
 * So this shows a turn. When tasks arrive it grows a task above the turn rather than
 * being replaced — the sections here are the ones the spec asks for.
 *
 * The rule that survives intact: **every row is a link to evidence.** The trace id is
 * shown because it is the join key into the audit log and the event stream; a tool card
 * shows the arguments it actually ran with. Nothing here is a summary of something the
 * user cannot then go and read.
 */

import type { Turn } from "../store";

export interface InspectorProps {
  turn: Turn | null;
  traceId: string;
  onUndo(undoId: string): void;
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

export function Inspector({ turn, traceId, onUndo }: InspectorProps) {
  if (!turn) {
    return (
      <aside className="inspector" aria-label="Inspector">
        <p className="muted">Select a turn to inspect it.</p>
      </aside>
    );
  }

  const outcome = outcomeLabel(turn);
  const total = turn.tools.reduce((sum, t) => sum + (t.durationMs ?? 0), 0);
  const undoable = turn.tools.filter((t) => t.undoId && !t.undone);

  return (
    <aside className="inspector" aria-label="Inspector">
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
