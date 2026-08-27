/**
 * The sidebar's project section — docs/UI.md#4, reading docs/PROJECT_STATE.md.
 *
 * Until now the sidebar rendered a bare list of directory names off `/api/v1/status`,
 * because that was all there was. `GET /api/v1/projects` returns two lists and the split
 * is the point:
 *
 *   - **projects** — rows ORACLE tracks, with counters it can actually produce;
 *   - **candidates** — directories on disk that nobody has registered. The real projects
 *     root on this machine holds `New folder` and `docs.zip` next to the real ones, which
 *     is exactly why registration is an explicit act and why these are collapsed.
 *
 * **Branch and dirty count are deliberately absent.** Producing them for every row means a
 * `git` subprocess per project on every render, and that fan-out is unmeasured
 * (docs/OPEN_QUESTIONS.md#oq-24). Caching them instead would make the sidebar lie the
 * moment somebody switches branches in their editor — the failure the whole subsystem is
 * shaped to avoid. They arrive per-row, lazily, once OQ-24 has a number.
 */

import { asRecord, num, str } from "../protocol";

export type ProjectStatus = "active" | "idle" | "archived" | "missing";

export interface ProjectRow {
  id: string;
  name: string;
  root: string;
  status: ProjectStatus;
  description: string;
  openTasks: number;
  failedTasks: number;
  usdSpent: number;
}

export interface ProjectsData {
  projects: ProjectRow[];
  candidates: string[];
  projectsRoot: string;
}

const STATUSES: ReadonlySet<string> = new Set(["active", "idle", "archived", "missing"]);

export function toProjects(raw: unknown): ProjectsData {
  const r = asRecord(raw);
  const rows = Array.isArray(r.projects) ? r.projects : [];
  const candidates = Array.isArray(r.candidates) ? r.candidates : [];
  return {
    projects: rows.map((row) => {
      const p = asRecord(row);
      const status = str(p.status);
      return {
        id: str(p.id),
        name: str(p.name),
        root: str(p.root),
        // An unknown status renders as `idle` rather than as itself: a class name built
        // from server text is how a typo becomes an unstyled row nobody notices.
        status: (STATUSES.has(status) ? status : "idle") as ProjectStatus,
        description: str(p.description),
        openTasks: num(p.open_tasks),
        failedTasks: num(p.failed_tasks),
        usdSpent: num(p.usd_spent),
      };
    }),
    candidates: candidates.map((c) => str(c)),
    projectsRoot: str(r.projects_root),
  };
}

/** Icon + label + colour, never colour alone (docs/UI.md#14). */
const MARK: Record<ProjectStatus, string> = {
  active: "●",
  idle: "○",
  archived: "▫",
  missing: "⚠",
};

export interface ProjectListProps {
  data: ProjectsData;
  selected?: string | null;
  onSelect(project: ProjectRow): void;
  onRegister(name: string): void;
}

export function ProjectList({ data, selected, onSelect, onRegister }: ProjectListProps) {
  const { projects, candidates } = data;

  return (
    <>
      <h2>PROJECTS</h2>
      <ul className="tree">
        {projects.length === 0 && (
          <li className="muted">
            {candidates.length === 0
              ? "none discovered"
              : "none tracked yet — register one below"}
          </li>
        )}
        {projects.map((p) => (
          <li key={p.id}>
            <button
              className={`tree-item pj pj-${p.status}${selected === p.id ? " selected" : ""}`}
              aria-current={selected === p.id ? "true" : undefined}
              onClick={() => onSelect(p)}
              title={p.description || p.root}
            >
              <i className="dot" aria-hidden="true">
                {MARK[p.status]}
              </i>
              <span className="pj-name">{p.name}</span>
              {/* The status word is present for every row, not only the alarming ones:
                  a label that appears only on failure is one nobody learns to read. */}
              <span className="pj-status">{p.status}</span>
              {p.failedTasks > 0 && (
                <span className="pj-count attn" title={`${p.failedTasks} failed`}>
                  ✗{p.failedTasks}
                </span>
              )}
              {p.openTasks > 0 && (
                <span className="pj-count" title={`${p.openTasks} open`}>
                  {p.openTasks}
                </span>
              )}
            </button>
          </li>
        ))}
      </ul>

      {candidates.length > 0 && (
        <details className="pj-candidates">
          <summary>
            {candidates.length} not tracked
          </summary>
          {/* Registering is a human act (ADR-0024) and grants nothing: scopes live in
              config/policy.yaml where a person edits them and git records the edit. */}
          <ul className="tree">
            {candidates.map((name) => (
              <li key={name}>
                <button className="tree-item ghost-item" onClick={() => onRegister(name)}>
                  <i className="dot" aria-hidden="true">
                    +
                  </i>
                  <span className="pj-name">{name}</span>
                  <span className="pj-status">track</span>
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}
