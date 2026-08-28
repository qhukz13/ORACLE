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
 * **Branch and dirty count arrive lazily, for the selected row only.** OQ-24 has its
 * number (2026-08-28, `scripts/measure_observation.py`): the full 8-row fan-out costs
 * 1.7–2.7 s warm under load — 2–3× over the 1 s budget — and the toolhost serialises
 * invocations, so an eager fan-out would also queue behind real work. So the row that is
 * being looked at is the row that gets observed, read fresh on every selection and every
 * task event, held nowhere. Caching instead would make the sidebar lie the moment
 * somebody switches branches in their editor — the failure the whole subsystem is shaped
 * to avoid.
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

/** The observed half of one project — `GET /api/v1/projects/{id}`'s `observation`,
 *  which the server reads fresh through the tool layer on every call. */
export interface Observation {
  branch: string;
  ahead: number;
  behind: number;
  dirty: number;
  clean: boolean;
  error: string;
}

export function toObservation(raw: unknown): Observation {
  const o = asRecord(asRecord(raw).observation);
  return {
    branch: str(o.branch),
    ahead: num(o.ahead),
    behind: num(o.behind),
    dirty: num(o.dirty),
    clean: o.clean === true,
    error: str(o.error),
  };
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
  /** Fresh observation for the SELECTED row, or null while it loads / when nothing is
   *  selected. One row's worth by design — the fan-out misses the budget (OQ-24). */
  observation?: Observation | null;
  onSelect(project: ProjectRow): void;
  onRegister(name: string): void;
}

/** `⎇ main ↑3 ↓1 ~2` — the UI.md §4 line, with zeros absent rather than rendered. */
function gitLine(o: Observation): string {
  const parts = [`⎇ ${o.branch}`];
  if (o.ahead > 0) parts.push(`↑${o.ahead}`);
  if (o.behind > 0) parts.push(`↓${o.behind}`);
  if (o.dirty > 0) parts.push(`~${o.dirty}`);
  return parts.join(" ");
}

export function ProjectList({ data, selected, observation, onSelect, onRegister }: ProjectListProps) {
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
              {/* Only on the selected row, read fresh each time (OQ-24). An observation
                  that errored renders nothing extra: the status word already carries
                  `missing`, and a not-a-repo project has no branch to show. */}
              {selected === p.id && observation && !observation.error && observation.branch && (
                <span
                  className="pj-git"
                  title={`branch ${observation.branch} · ${observation.ahead} ahead · ${observation.behind} behind · ${observation.dirty} dirty`}
                >
                  {gitLine(observation)}
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
