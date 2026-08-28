/**
 * "What happened while I wasn't looking" — docs/UI.md#7b, docs/PROJECT_STATE.md#6.
 *
 * The timeline answers this exhaustively, which is the wrong shape for the question. This
 * is the **glance** surface: the delta since the reader last acknowledged, grouped by
 * project, bounded, readable in the three to five seconds VISION.md#2 allocates to it.
 *
 * Two things about it are load-bearing and easy to lose in a refactor:
 *
 * 1. **Rendering does not consume it.** The component never acknowledges on mount, on
 *    focus, or on scroll. A briefing that clears itself on sight is a notification, and
 *    notifications are how people miss things.
 * 2. **`onAcknowledge` sends the `throughSeq` that was DISPLAYED**, not a freshly-read
 *    one. Work that arrived while the reader was looking must not be marked seen by an
 *    acknowledgement of what they actually saw — which is why the number is carried in
 *    the payload rather than fetched again at dismissal time.
 *
 * The text the server already rendered is authoritative for the plain-text case; this
 * renders the structured fields so each line can carry an affordance. Every line here
 * opens something real, because a line with no affordance is a log entry in a costume.
 */

import { asRecord, num, str } from "../protocol";

export interface BriefingLine {
  id: string;
  objective: string;
  status: string;
  agent: string | null;
  error: string | null;
}

export interface BriefingProject {
  project: string;
  status: string;
  completed: number;
  failed: number;
  waiting: number;
  inFlight: number;
  cancelled: number;
  elapsedS: number;
  usd: number;
  needsYou: boolean;
  more: number;
  highlights: BriefingLine[];
}

export interface BriefingSystem {
  restartedAt: string | null;
  unclean: boolean;
  degraded: string[];
  errors: number;
}

export interface BriefingData {
  throughSeq: number;
  sinceTs: string | null;
  empty: boolean;
  text: string;
  projects: BriefingProject[];
  system: BriefingSystem;
}

function orNull(value: unknown): string | null {
  return value === null || value === undefined || value === "" ? null : String(value);
}

export function toBriefing(raw: unknown): BriefingData {
  const r = asRecord(raw);
  const projects = Array.isArray(r.projects) ? r.projects : [];
  const system = asRecord(r.system);
  const degraded = Array.isArray(system.degraded) ? system.degraded : [];
  return {
    throughSeq: num(r.through_seq),
    sinceTs: orNull(r.since_ts),
    empty: r.empty === true,
    text: str(r.text),
    projects: projects.map((p) => {
      const row = asRecord(p);
      const highlights = Array.isArray(row.highlights) ? row.highlights : [];
      return {
        project: str(row.project),
        status: str(row.status),
        completed: num(row.completed),
        failed: num(row.failed),
        waiting: num(row.waiting),
        inFlight: num(row.in_flight),
        cancelled: num(row.cancelled),
        elapsedS: num(row.elapsed_s),
        usd: num(row.usd),
        needsYou: row.needs_you === true,
        more: num(row.more),
        highlights: highlights.map((h) => {
          const line = asRecord(h);
          return {
            id: str(line.id),
            objective: str(line.objective),
            status: str(line.status),
            agent: orNull(line.agent),
            error: orNull(line.error),
          };
        }),
      };
    }),
    system: {
      restartedAt: orNull(system.restarted_at),
      unclean: system.unclean === true,
      degraded: degraded.map((d) => str(d)),
      errors: num(system.errors),
    },
  };
}

/** Absolute, not "3 hours ago". What happened overnight is a fact about a clock. */
function when(iso: string | null): string {
  if (!iso) return "the beginning";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function duration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

/** Icon + label + colour, never colour alone (docs/UI.md#14). */
const MARK: Record<string, string> = {
  waiting: "⏸",
  failed: "✗",
  timeout: "✗",
  succeeded: "✓",
  running: "●",
  ready: "●",
  pending: "●",
  cancelled: "▫",
  skipped: "▫",
};

function counts(p: BriefingProject): string[] {
  const out: string[] = [];
  if (p.waiting) out.push(`${p.waiting} waiting on you`);
  if (p.failed) out.push(`${p.failed} failed`);
  if (p.inFlight) out.push(`${p.inFlight} running`);
  if (p.completed) out.push(`${p.completed} completed`);
  if (p.cancelled) out.push(`${p.cancelled} cancelled`);
  if (p.elapsedS) out.push(duration(p.elapsedS));
  if (p.usd) out.push(`$${p.usd.toFixed(2)}`);
  return out;
}

export interface BriefingProps {
  data: BriefingData;
  /** Called only from an explicit dismissal. Never on mount, focus or scroll. */
  onAcknowledge(throughSeq: number, projectId?: string): void;
  /** Opens a task in the inspector. A line with no affordance is a log entry. */
  onInspect(taskId: string): void;
  /** Opens the project — the same action as clicking it in the sidebar. */
  onOpenProject(project: string): void;
}

export function Briefing({ data, onAcknowledge, onInspect, onOpenProject }: BriefingProps) {
  if (data.empty) {
    // A real state, not a placeholder and not a fabricated summary of nothing.
    return (
      <section className="briefing" aria-label="Briefing">
        <p className="bf-empty" role="status">
          <span aria-hidden="true">○</span> Nothing ran since {when(data.sinceTs)}.
        </p>
      </section>
    );
  }

  const { system } = data;

  return (
    <section className="briefing" aria-label="Briefing">
      <header className="bf-head">
        <h2 className="bf-title">Since {when(data.sinceTs)}</h2>
        <span className="spacer" />
        <button className="ghost" onClick={() => onAcknowledge(data.throughSeq)}>
          Dismiss all
        </button>
      </header>

      {data.projects.map((p) => (
        <article key={p.project} className={`bf-project${p.needsYou ? " attn" : ""}`}>
          <header className="bf-project-head">
            <button className="link" onClick={() => onOpenProject(p.project)}>
              {p.project}
            </button>
            <span className="bf-counts">{counts(p).join(" · ")}</span>
          </header>

          <ul className="bf-lines">
            {p.highlights.map((line) => (
              <li key={line.id} className={`bf-line st-${line.status}`}>
                <i className="bf-mark" aria-hidden="true">
                  {MARK[line.status] ?? "·"}
                </i>
                <span className="bf-status">{line.status}</span>
                <span className="bf-objective">
                  {line.objective || "(no objective recorded)"}
                </span>
                {line.error && <span className="bf-error">{line.error}</span>}
                <button
                  className="ghost small"
                  onClick={() => onInspect(line.id)}
                  aria-label={`Inspect ${line.objective || line.id} in ${p.project}`}
                >
                  inspect
                </button>
              </li>
            ))}
            {p.more > 0 && (
              <li className="bf-more muted">
                …and {p.more} more — open the timeline for the rest
              </li>
            )}
          </ul>
        </article>
      ))}

      {(system.restartedAt || system.degraded.length > 0 || system.errors > 0) && (
        <article className={`bf-project bf-system${system.unclean ? " attn" : ""}`}>
          <header className="bf-project-head">
            <span className="bf-system-title">System</span>
          </header>
          <ul className="bf-lines">
            {system.restartedAt && (
              <li className={`bf-line ${system.unclean ? "st-failed" : "st-succeeded"}`}>
                <i className="bf-mark" aria-hidden="true">
                  {system.unclean ? "✗" : "✓"}
                </i>
                <span className="bf-status">{system.unclean ? "crashed" : "restarted"}</span>
                <span className="bf-objective">
                  ORACLE {system.unclean ? "stopped unexpectedly and restarted" : "restarted"} at{" "}
                  {when(system.restartedAt)}
                </span>
              </li>
            )}
            {system.degraded.map((reason) => (
              <li key={reason} className="bf-line st-waiting">
                <i className="bf-mark" aria-hidden="true">
                  ⚠
                </i>
                <span className="bf-status">degraded</span>
                <span className="bf-objective">{reason}</span>
              </li>
            ))}
            {system.errors > 0 && (
              <li className="bf-line st-failed">
                <i className="bf-mark" aria-hidden="true">
                  ✗
                </i>
                <span className="bf-status">errors</span>
                <span className="bf-objective">{system.errors} recorded</span>
              </li>
            )}
          </ul>
        </article>
      )}
    </section>
  );
}
