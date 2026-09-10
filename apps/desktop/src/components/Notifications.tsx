/**
 * Toasts — docs/UI.md §12.
 *
 * The spec's own sentence is the whole design constraint:
 *
 * > *A notification means something changed that I would want to know without looking. Everything
 * > else is noise, and noise trains me to ignore the channel that matters.*
 *
 * So this deliberately notifies about **three** things and nothing else: a task finished, a task
 * failed, and an approval is waiting. Not tool calls, not indexing, not retrieval, not delegate
 * chatter — those are log territory, and every one of them is more frequent than the three that
 * matter. A toast per `tool.finished` would bury the approval that blocks the machine.
 *
 * **Derived from events, not stored.** Like the knowledge map's retrieval traces: the event log
 * already holds every one of these facts, and a second copy would only get a chance to disagree.
 * Dismissal is the one piece of local state, because "I have seen this" is a fact about the reader
 * rather than about the system.
 *
 * **Degradation is not here.** §12 says it is a sticky *banner, not a toast*, and `App.tsx` already
 * renders one. A model being offline is a standing condition, not an event — it does not want a
 * thing that slides in and leaves.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { Approval, OracleEvent } from "../protocol";
import { str } from "../protocol";

/** §12: max 3 stacked, then a "+N" collapse. */
const MAX_VISIBLE = 3;
/** §12: task completed 4 s. Failures and approvals are sticky until dismissed. */
const COMPLETED_MS = 4000;

export type ToastKind = "completed" | "failed" | "approval";

export interface Toast {
  id: string;
  kind: ToastKind;
  title: string;
  detail: string;
  /** Sticky toasts stay until dismissed — a failure nobody saw is a failure nobody fixed. */
  sticky: boolean;
}

export interface NotificationsProps {
  events: readonly OracleEvent[];
  approvals: readonly Approval[];
  /** Focus the thing the toast is about. */
  onOpen?(toast: Toast): void;
}

/**
 * Read the three notifiable facts out of the log.
 *
 * `task.finished` carries `status`, so completed and failed are one event type split by payload
 * rather than two subscriptions. Pipeline steps emit ordinary `task.*` by design (a pipeline *is* a
 * task graph), which is why there is no separate pipeline case here — and why the graph-source
 * filter matters: without it a delegation's internal tasks would each raise a toast.
 */
export function toToasts(events: readonly OracleEvent[], approvals: readonly Approval[]): Toast[] {
  const out: Toast[] = [];

  for (const e of events) {
    if (e.type !== "task.finished") continue;
    if (e.payload["source"] !== "graph") continue;
    const status = str(e.payload["status"], "done");
    const failed = status === "failed" || status === "error";
    const summary = str(e.payload["summary"]);
    out.push({
      id: `task-${e.seq}`,
      kind: failed ? "failed" : "completed",
      title: failed ? "Task failed" : "Task completed",
      detail: summary || str(e.task_id) || status,
      sticky: failed,
    });
  }

  // Approvals come from live state rather than the log: an approval that has already been
  // answered must not reappear as a toast on reconnect, and `approvals` is exactly the set that
  // is still open. This is the one case where the store is a better source than the events.
  for (const a of approvals) {
    out.push({
      id: `approval-${a.approvalId}`,
      kind: "approval",
      title: "Approval needed",
      detail: `${a.tool} — ${a.rule || a.tier}`,
      sticky: true,
    });
  }

  return out.reverse();
}

export function Notifications({ events, approvals, onOpen }: NotificationsProps) {
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(() => new Set());
  const [expired, setExpired] = useState<ReadonlySet<string>>(() => new Set());

  const toasts = useMemo(() => toToasts(events, approvals), [events, approvals]);
  const live = useMemo(
    () => toasts.filter((t) => !dismissed.has(t.id) && !expired.has(t.id)),
    [toasts, dismissed, expired],
  );

  const dismiss = useCallback((id: string) => {
    setDismissed((prev) => new Set(prev).add(id));
  }, []);

  // Non-sticky toasts time out. Keyed on the id list rather than on `live`, so re-rendering for an
  // unrelated reason cannot restart a timer that was already running.
  const transientIds = live
    .filter((t) => !t.sticky)
    .map((t) => t.id)
    .join(",");
  useEffect(() => {
    if (!transientIds) return;
    const ids = transientIds.split(",");
    const timers = ids.map((id) =>
      window.setTimeout(() => setExpired((prev) => new Set(prev).add(id)), COMPLETED_MS),
    );
    return () => timers.forEach((t) => window.clearTimeout(t));
  }, [transientIds]);

  if (live.length === 0) return null;
  const visible = live.slice(0, MAX_VISIBLE);
  const hidden = live.length - visible.length;

  return (
    // `role="log"` with a polite live region: a toast announces itself once, without stealing
    // focus. An approval is urgent but it is not an interruption — the Confirmation Center is
    // where it gets decided, and yanking focus mid-sentence is how people approve by accident.
    <div className="toasts" role="log" aria-live="polite" aria-label="Notifications">
      {visible.map((t) => (
        <div key={t.id} className={`toast toast-${t.kind}`}>
          {/* Icon plus text, never colour alone (UI.md §1). */}
          <span className="toast-glyph" aria-hidden="true">
            {t.kind === "failed" ? "✗" : t.kind === "approval" ? "⏸" : "✓"}
          </span>
          <span className="toast-body">
            <strong>{t.title}</strong>
            <span className="muted toast-detail">{t.detail}</span>
          </span>
          {onOpen && (
            <button className="ghost" onClick={() => onOpen(t)}>
              {t.kind === "approval" ? "Review" : "Show"}
            </button>
          )}
          <button className="ghost toast-x" aria-label={`Dismiss: ${t.title}`} onClick={() => dismiss(t.id)}>
            ✕
          </button>
        </div>
      ))}
      {hidden > 0 && (
        <div className="toast toast-more">
          <span className="muted">+{hidden} more</span>
          <button className="ghost" onClick={() => live.forEach((t) => dismiss(t.id))}>
            Dismiss all
          </button>
        </div>
      )}
    </div>
  );
}
