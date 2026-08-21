/**
 * One card per tool call — docs/UI.md#5, and the reason the terminal is first-class:
 * **trust is built by seeing the actual command.**
 *
 * So the card shows the arguments verbatim rather than a sentence about them, and it
 * shows the tier, because "this ran without asking" is only reassuring if you can see
 * *why* it was allowed to.
 *
 * The Undo control appears only when the runtime recorded an undo for this call. It is
 * not an offer the UI makes on its own: `undo_id` comes from the journal, and a button
 * that appeared without one would promise something nothing can deliver.
 */

import type { ToolCall } from "../protocol";

export interface ToolCardProps {
  call: ToolCall;
  onUndo(undoId: string): void;
}

function argLine(args: Record<string, unknown>): string {
  const parts = Object.entries(args).map(
    ([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`,
  );
  return parts.join("  ");
}

export function ToolCard({ call, onUndo }: ToolCardProps) {
  const status = call.running ? "running" : call.ok ? "ok" : "failed";
  const label = call.running ? "RUNNING" : call.ok ? "DONE" : "FAILED";

  return (
    <div className={`tool-card tc-${status}`} data-tool={call.tool}>
      <div className="tc-head">
        {/* Icon + label + colour. Never colour alone (docs/UI.md#1). */}
        <span className="tc-glyph" aria-hidden="true">
          {call.running ? "◆" : call.ok ? "✓" : "✗"}
        </span>
        <code className="tc-name">{call.tool}</code>
        {call.tier && <span className={`tc-tier tier-${call.tier.toLowerCase()}`}>{call.tier}</span>}
        <span className="spacer" />
        <span className="tc-status">{label}</span>
        {call.durationMs !== undefined && <span className="tc-ms">{call.durationMs} ms</span>}
      </div>

      <pre className="tc-args">{argLine(call.args)}</pre>

      {call.summary && <p className="tc-summary">{call.summary}</p>}
      {call.error && (
        <p className="tc-error" role="alert">
          {call.error}
        </p>
      )}

      {call.undoId && !call.undone && (
        <div className="tc-actions">
          <button className="ghost" onClick={() => onUndo(call.undoId as string)}>
            Undo
          </button>
        </div>
      )}
      {call.undone && <p className="tc-undone">undone</p>}
    </div>
  );
}
