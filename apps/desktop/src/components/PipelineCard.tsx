/**
 * The pipeline approval card — the block inside a `pipe.run` approval (docs/PIPELINES.md §3).
 *
 * A separate component from `GraphCard`, not a variant of it, and the reason is the same
 * reason `GraphCard` names its author: **a person must not think they are approving one
 * kind of thing when they are approving another.** A graph card's rows are
 * `{role, agent, objective, egresses}` — work a model decomposed and a vendor may run. A
 * pipeline's rows are `{step, tool, args, tier, rule}` — commands a human wrote, run
 * locally, with nothing leaving the machine. Sharing one component would mean one of the
 * two sets of columns is always a lie.
 *
 * This card carries more weight than any other in ORACLE, because it is the only one that
 * authorises **several actions at once**. PIPELINES.md §3 asks for that deliberately —
 * being stopped at step 3 of 6 is the prompt fatigue the security model exists to avoid —
 * and the cost is that "approve six things" and "rubber-stamp six things" are the same
 * gesture. Four things here are what keep them apart:
 *
 * - **Every elevated step shows its RESOLVED arguments**, not a count and not a summary.
 *   SECURITY.md §2 rule 5: confirm actions, not intentions.
 * - **Where the file came from is on the card.** A pipeline that arrived with a
 *   `git clone` is repository content, and approving one is a different decision from
 *   approving a file in your own `config/`. The tier alone does not say which.
 * - **What the parameters removed is shown too.** A run is defined as much by the steps
 *   `when:` took out as by the ones left in.
 * - **What approving means, in a sentence**: these commands, these arguments, and nothing
 *   asks again once it starts.
 */

import { asRecord, str } from "../protocol";

export interface PipelineCardProps {
  preview: Record<string, unknown>;
}

interface StepRow {
  step: string;
  tool: string;
  args: Record<string, unknown>;
  tier: string;
  rule: string;
  asks: boolean;
}

function steps(value: unknown): StepRow[] {
  if (!Array.isArray(value)) return [];
  return value.map((entry) => {
    const r = asRecord(entry);
    return {
      step: str(r["step"]),
      tool: str(r["tool"]),
      args: asRecord(r["args"]),
      tier: str(r["tier"]),
      rule: str(r["rule"]),
      asks: r["asks"] === true,
    };
  });
}

/** Arguments as one readable line. Rendered verbatim: an argument abbreviated here is an
 *  argument nobody checked, and the digest the grant binds to is computed from these. */
function argLine(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([key, value]) => `${key}=${Array.isArray(value) ? value.join(" ") : String(value)}`)
    .join("  ");
}

export function PipelineCard({ preview }: PipelineCardProps) {
  const p = asRecord(preview);
  const rows = steps(p["steps"]);
  const omitted = Array.isArray(p["omitted"]) ? p["omitted"].map(asRecord) : [];
  const params = asRecord(p["params"]);
  const fromRepo = str(p["source"]) === "project";
  const asking = rows.filter((r) => r.asks);

  return (
    <div className="pipeline-card" data-testid="pipeline-card">
      <p className="pc-name">
        <b>{str(p["pipeline"])}</b>
        {str(p["project"]) && <span className="pc-project"> · {str(p["project"])}</span>}
      </p>

      {fromRepo ? (
        <p className="pc-source pc-source-project" role="note">
          ⚠ This pipeline came from the <b>repository</b> (<code>{str(p["path"])}</code>), not
          from your own config. Whoever wrote that repo wrote these steps.
        </p>
      ) : (
        <p className="pc-source pc-source-global muted">
          from your config · <code>{str(p["path"])}</code>
        </p>
      )}

      {Object.keys(params).length > 0 && (
        <p className="pc-params">
          {Object.entries(params).map(([key, value]) => (
            <span key={key} className="pc-param">
              {key}={String(value)}
            </span>
          ))}
        </p>
      )}

      <p className="pc-count">
        {rows.length} step{rows.length === 1 ? "" : "s"} · {asking.length} need
        {asking.length === 1 ? "s" : ""} this approval
      </p>

      <ol className="pc-steps">
        {rows.map((row) => (
          <li key={row.step} className={`pc-step ${row.asks ? "pc-asks" : "pc-auto"}`}>
            <div className="pc-step-head">
              <span className="pc-id">{row.step}</span>
              <span className="pc-tool">{row.tool}</span>
              <span className={`pc-tier pc-tier-${row.tier}`}>{row.tier}</span>
              {row.asks && <span className="pc-badge">NEEDS APPROVAL</span>}
            </div>
            {/* Verbatim, and this is the whole card: the grant that gets minted is bound
                to a digest of exactly these arguments. */}
            <div className="pc-args">{argLine(row.args)}</div>
            <div className="pc-rule muted">{row.rule}</div>
          </li>
        ))}
      </ol>

      {omitted.length > 0 && (
        <div className="pc-omitted">
          <span className="pc-omitted-label">NOT RUNNING — REMOVED BY A CONDITION</span>
          <ul>
            {omitted.map((entry, i) => (
              <li key={`${str(entry["step"])}-${i}`}>
                <code>{str(entry["step"])}</code> · {str(entry["reason"])}
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="pc-note muted">{str(p["note"])}</p>
    </div>
  );
}
