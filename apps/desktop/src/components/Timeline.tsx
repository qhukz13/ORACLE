/**
 * The activity timeline — docs/UI.md §7: the event log, rendered. A chronological
 * stream, grouped by turn, filterable, with every row an entry point into the
 * inspector. **This is the debugging surface for the agent itself** — when ORACLE does
 * something strange, this is where to find out why.
 *
 * Three shapes it deliberately keeps:
 *
 * - **Grouping is by contiguity, never by sort.** Consecutive events sharing a turn (or
 *   a task, for turn-less graph events) fold into one group; an interleaving starts a
 *   new group rather than being reordered into an old one. The log's order is the
 *   truth being displayed — a timeline that re-sorts it is editing the evidence.
 * - **The filter is one substring over everything** — type, ids, payload. §7 says
 *   "filterable by project/task/tool/level", and all four are substrings of the row;
 *   a faceted filter can earn its place when somebody outgrows this one.
 * - **Selection drives the inspector** (§21 rule 1). A row belonging to a task selects
 *   the task; a row belonging to a turn selects the turn. Same selection model as the
 *   chat list, the task tree and the briefing — this view adds no second one.
 *
 * Groups are an APG disclosure — a `button[aria-expanded]` beside the inspect button —
 * rather than TaskTree's native `<details>`, and the audit chose that, not taste: the
 * group header needs a second control, and a button nested inside `<summary>` is a
 * `nested-interactive` violation (axe, serious). Two sibling buttons are the honest
 * shape. The newest group opens by itself; a group the user toggles keeps the user's
 * choice even as new events arrive (a live surface that snaps shut under the reader is
 * unusable).
 */

import { useMemo, useState } from "react";
import type { OracleEvent } from "../protocol";

export interface TimelineSelection {
  kind: "turn" | "task";
  id: string;
}

export interface TimelineProps {
  events: OracleEvent[];
  onInspect(selection: TimelineSelection): void;
}

interface Group {
  /** Stable across re-renders: the first event's seq. */
  key: string;
  label: string;
  kind: "turn" | "task" | "system";
  /** The id selection would use, when the group belongs to a turn or task. */
  ref: string | null;
  events: OracleEvent[];
}

/** Icon per event, beside the type word — never instead of it (docs/UI.md §1). */
export function mark(ev: OracleEvent): string {
  const p = ev.payload;
  switch (ev.type) {
    case "turn.started":
      return "▸";
    case "turn.finished":
      return "●";
    case "tool.started":
      return "◆";
    case "tool.finished":
      return p["ok"] === false ? "✗" : "✓";
    case "approval.requested":
      return "⚠";
    case "approval.resolved":
      return p["decision"] === "approve" ? "✓" : "✗";
    case "task.finished": {
      const status = String(p["status"] ?? "");
      return status === "succeeded" ? "✓" : ["failed", "timeout"].includes(status) ? "✗" : "●";
    }
    case "system.degraded":
      return "⚠";
    default:
      return "·";
  }
}

/** One human-readable line per event, falling back to sliced JSON — the same honest
 *  fallback the raw table used: a summary must never hide that there is more. */
export function summarise(ev: OracleEvent): string {
  const p = ev.payload;
  switch (ev.type) {
    case "turn.started":
      return String(p["text"] ?? "");
    case "tool.started":
      return String(p["tool"] ?? "");
    case "tool.finished": {
      const ms = p["duration_ms"] != null ? ` ${String(p["duration_ms"])}ms` : "";
      return `${String(p["tool"] ?? "")}${ms}  ${String(p["summary"] ?? p["error"] ?? "")}`.trim();
    }
    case "approval.requested":
      return `${String(p["tool"] ?? "")} ${String(p["tier"] ?? "")}`.trim();
    case "approval.resolved":
      return String(p["decision"] ?? "");
    case "agent.state":
      return String(p["state"] ?? "");
    case "task.created":
    case "task.updated":
    case "task.finished":
      return `${String(p["kind"] ?? "")} ${String(p["status"] ?? "")}`.trim();
    default:
      return JSON.stringify(p).slice(0, 90);
  }
}

function groupKeyOf(ev: OracleEvent): { id: string; kind: Group["kind"]; ref: string | null } {
  if (ev.turn_id) return { id: `turn:${ev.turn_id}`, kind: "turn", ref: ev.turn_id };
  if (ev.task_id) return { id: `task:${ev.task_id}`, kind: "task", ref: ev.task_id };
  return { id: "system", kind: "system", ref: null };
}

export function groupEvents(events: OracleEvent[]): Group[] {
  const groups: Group[] = [];
  let currentId = "";
  for (const ev of events) {
    const { id, kind, ref } = groupKeyOf(ev);
    const last = groups[groups.length - 1];
    if (!last || id !== currentId) {
      groups.push({ key: `g${ev.seq}`, label: "", kind, ref, events: [ev] });
      currentId = id;
    } else {
      last.events.push(ev);
    }
  }
  for (const g of groups) {
    const started = g.events.find((e) => e.type === "turn.started");
    g.label =
      g.kind === "turn"
        ? String(started?.payload["text"] ?? "") || `turn ${g.ref}`
        : g.kind === "task"
          ? `task ${g.ref}`
          : "system";
  }
  return groups;
}

function clock(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleTimeString();
}

export function Timeline({ events, onInspect }: TimelineProps) {
  const [filter, setFilter] = useState("");
  // A group the user toggled keeps the user's choice; only untouched groups follow the
  // default (newest open, and everything open while a filter narrows the rows).
  const [toggled, setToggled] = useState<ReadonlyMap<string, boolean>>(new Map());

  const groups = useMemo(() => groupEvents(events), [events]);
  const needle = filter.trim().toLowerCase();
  const shown = useMemo(() => {
    if (!needle) return groups.map((g) => ({ group: g, rows: g.events }));
    return groups
      .map((g) => ({
        group: g,
        rows: g.events.filter((e) =>
          `${e.type} ${e.trace_id} ${e.turn_id ?? ""} ${e.task_id ?? ""} ${JSON.stringify(e.payload)}`
            .toLowerCase()
            .includes(needle),
        ),
      }))
      .filter((entry) => entry.rows.length > 0);
  }, [groups, needle]);

  return (
    <section className="timeline" aria-label="Activity timeline">
      <input
        className="tl-filter"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="filter — type, tool, project, task, anything in a payload…"
        aria-label="Filter the timeline"
      />
      {events.length === 0 && (
        <p className="muted" role="status">
          Nothing has happened yet this session. Events appear here as they occur.
        </p>
      )}
      {events.length > 0 && shown.length === 0 && (
        <p className="muted" role="status">
          Nothing matches “{filter.trim()}”.
        </p>
      )}
      {shown.map(({ group, rows }, i) => {
        // Newest open, filter matches open, the rest closed — unless the user chose.
        const open = toggled.get(group.key) ?? (i === shown.length - 1 || Boolean(needle));
        const inspectable: TimelineSelection | null =
          group.kind !== "system" && group.ref ? { kind: group.kind, id: group.ref } : null;
        return (
          <section key={group.key} className="tl-group">
            <div className="tl-head">
              <button
                type="button"
                className="tl-disclose"
                aria-expanded={open}
                aria-controls={`tl-rows-${group.key}`}
                onClick={() =>
                  setToggled((prev) => new Map(prev).set(group.key, !open))
                }
              >
                <span className="tl-caret" aria-hidden="true">
                  {open ? "▾" : "▸"}
                </span>
                <span className="tl-time">{clock(rows[0]?.ts ?? "")}</span>
                <span className="tl-label">{group.label}</span>
                <span className="tl-count muted">
                  {rows.length} event{rows.length === 1 ? "" : "s"}
                </span>
              </button>
              {inspectable && (
                // The §7 [inspect] affordance, once per group rather than once per row:
                // every row of a turn targets the same turn, and five hundred
                // identically-labelled buttons is noise, not access. Same selection model
                // as everywhere (§21).
                <button
                  type="button"
                  className="ghost small"
                  aria-label={`inspect ${inspectable.kind} ${inspectable.id}`}
                  onClick={() => onInspect(inspectable)}
                >
                  inspect
                </button>
              )}
            </div>
            {/* `hidden` rather than unmounted, so aria-controls always points at a real
                element — a dangling reference is its own axe violation. */}
            <ol id={`tl-rows-${group.key}`} className="tl-rows" hidden={!open}>
              {rows.map((ev) => (
                <li key={ev.seq} className="tl-row">
                  <span className="tl-time muted">{clock(ev.ts)}</span>
                  <span className="tl-mark" aria-hidden="true">
                    {mark(ev)}
                  </span>
                  <span className="tl-type">{ev.type}</span>
                  <span className="tl-summary muted">{summarise(ev)}</span>
                  <span className="tl-seq muted">{ev.seq}</span>
                </li>
              ))}
            </ol>
          </section>
        );
      })}
    </section>
  );
}
