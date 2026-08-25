/**
 * The graph approval card — the block inside an `ai.graph` approval that shows what is
 * about to be scheduled (docs/ORCHESTRATION.md §5, docs/PLANNER.md §6).
 *
 * Until this existed the payload carried every task, its role, its agent and whether it
 * would egress, and the UI rendered none of it: the card fell through to the generic
 * EFFECT block, which shows a one-line summary. "Approving what you did not read is the
 * attack" is P8-T1's own sentence about this exact card, and a summary is not a plan.
 *
 * Three things it insists on:
 *
 * - **Who wrote this.** A plan a model decomposed and a deterministic template ORACLE
 *   fell back to are different objects, and a person deciding needs to know which one is
 *   in front of them before they read the tasks (PLANNER.md §6's ladder).
 * - **The objectives, verbatim.** Not summarised, not truncated to a clause: an
 *   instruction hidden inside a plan is only defended against if it is visible here.
 * - **What approving means.** The graph exists and runs; nothing egresses. Each
 *   delegation still asks with its own rendered bytes attached.
 */

import { asRecord, num, str } from "../protocol";

export interface GraphCardProps {
  preview: Record<string, unknown>;
}

/** How a rung reads to a person. The server sends `authored_by`; this is the wording,
 *  and an unknown value falls back to the raw string rather than to silence. */
const AUTHOR_LABEL: Record<string, string> = {
  planner: "a planner wrote this plan",
  template: "NO PLANNER — this is a deterministic template",
  single_task: "NO PLANNER, NO TEMPLATE — one task, your objective unchanged",
  human: "you wrote this plan",
};

interface Row {
  taskId: string;
  kind: string;
  role: string;
  agent: string;
  objective: string;
  project: string;
  egresses: boolean;
}

function rows(value: unknown): Row[] {
  if (!Array.isArray(value)) return [];
  return value.map((entry) => {
    const r = asRecord(entry);
    return {
      taskId: str(r["task_id"]),
      kind: str(r["kind"]),
      role: str(r["role"]),
      agent: str(r["agent"], "—"),
      objective: str(r["objective"]),
      project: str(r["project"]),
      egresses: r["egresses"] === true,
    };
  });
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

export function GraphCard({ preview }: GraphCardProps) {
  const p = asRecord(preview);
  const tasks = rows(p["tasks"]);
  const risks = strings(p["risks"]);
  const author = str(p["authored_by"], "planner");
  const addition = p["addition"] === true;
  const descents = Array.isArray(p["descents"]) ? p["descents"].map(asRecord) : [];
  const egressing = tasks.filter((t) => t.egresses).length;

  return (
    <div className="graph-card" data-testid="graph-card">
      <p className={`gc-author gc-author-${author}`}>
        {AUTHOR_LABEL[author] ?? author}
        {num(p["rung"], 0) > 0 && <> · rung {num(p["rung"], 0)}</>}
      </p>

      {addition && (
        <p className="gc-addition" role="note">
          ⚠ These {tasks.length} task{tasks.length === 1 ? "" : "s"} are being <b>ADDED</b> to a
          graph that is already running, replacing <code>{str(p["replaces"])}</code>, which stays
          failed and is not re-run. Replan {str(p["replan"])}.
        </p>
      )}

      {descents.length > 0 && (
        <ul className="gc-descents">
          {descents.map((step, i) => (
            <li key={`${str(step["to"])}-${i}`}>
              fell back from <b>{str(step["from"])}</b> to <b>{str(step["to"])}</b>:{" "}
              {str(step["why"])}
            </li>
          ))}
        </ul>
      )}

      <p className="gc-objective">{str(p["objective"])}</p>
      {str(p["summary"]) && <p className="gc-summary">{str(p["summary"])}</p>}

      <p className="gc-count">
        {tasks.length} task{tasks.length === 1 ? "" : "s"} · {egressing} will send something to a
        cloud agent
      </p>
      <ol className="gc-tasks">
        {tasks.map((task) => (
          <li key={task.taskId} className={`gc-task gc-kind-${task.kind}`}>
            <div className="gc-task-head">
              <span className="gc-id">{task.taskId}</span>
              <span className="gc-kind">{task.kind}</span>
              <span className="gc-role">{task.role}</span>
              <span className="gc-agent">{task.agent}</span>
              {task.project && <span className="gc-project">{task.project}</span>}
              {task.egresses && <span className="gc-egress">EGRESSES</span>}
            </div>
            {/* Verbatim. An objective summarised here is an objective nobody read. */}
            <div className="gc-objective-line">{task.objective}</div>
          </li>
        ))}
      </ol>

      {risks.length > 0 && (
        <div className="gc-risks">
          <span className="gc-risks-label">THE AUTHOR SAID IT WAS UNSURE ABOUT</span>
          <ul>
            {risks.map((risk, i) => (
              <li key={`${risk.slice(0, 24)}-${i}`}>{risk}</li>
            ))}
          </ul>
        </div>
      )}

      <p className="gc-note muted">{str(p["note"])}</p>
    </div>
  );
}
