/**
 * Global search (`Ctrl+Shift+F`) — docs/UI.md §11: one query, six groups, counts and
 * previews, backed by `GET /api/v1/search`.
 *
 * The same combobox contract as the palette (focus never leaves the input;
 * `aria-activedescendant` announces the active row), because it is the same shape of
 * surface — and the palette's audit history is why this one was born with the roles
 * on. Groups are `role="group"` inside the listbox, labelled by their headers.
 *
 * What a result DOES is only what the app can honestly do with it today:
 *
 *   - a project selects it in the sidebar (selection, not `continue` — starting work
 *     is the sidebar's affordance, with its approval card; search only points);
 *   - a task selects into the inspector's task branch;
 *   - an event jumps to the Timeline stage;
 *   - files, notes and git commits are previews only — the app has no file viewer, so
 *     Enter does nothing rather than pretending. `Ctrl+Enter` ("send as context") is
 *     deferred with them: it needs a context-package path that has no API yet.
 *
 * The input debounces 300 ms — the retrieval half measured p50 681 / p95 1,270 ms
 * through the gate (§11's as-built note), so firing per keystroke would queue embeds
 * behind each other on the serialised toolhost. `elapsed_ms` is shown because a slow
 * answer with a number is a measurement, and one without is a broken feeling.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { asRecord, num, str } from "../protocol";

export interface SearchData {
  query: string;
  elapsedMs: number;
  projects: { id: string; name: string; status: string; description: string }[];
  tasks: { id: string; rootId: string; kind: string; status: string; objective: string }[];
  events: { seq: number; ts: string; type: string; snippet: string }[];
  files: SearchHit[];
  notes: SearchHit[];
  git: { short: string; subject: string; author: string; date: string }[];
  tainted: boolean;
  degraded: boolean;
  knowledgeError: string;
  gitSearched: boolean;
  gitError: string;
}

export interface SearchHit {
  path: string;
  project: string;
  anchor: string;
  text: string;
  provenance: string;
}

export function toSearch(raw: unknown): SearchData {
  const r = asRecord(raw);
  const hits = (value: unknown): SearchHit[] =>
    (Array.isArray(value) ? value : []).map((h) => {
      const row = asRecord(h);
      return {
        path: str(row.path),
        project: str(row.project),
        anchor: str(row.anchor),
        text: str(row.text),
        provenance: str(row.provenance, "local_owned"),
      };
    });
  return {
    query: str(r.query),
    elapsedMs: num(r.elapsed_ms),
    projects: (Array.isArray(r.projects) ? r.projects : []).map((p) => {
      const row = asRecord(p);
      return {
        id: str(row.id),
        name: str(row.name),
        status: str(row.status),
        description: str(row.description),
      };
    }),
    tasks: (Array.isArray(r.tasks) ? r.tasks : []).map((t) => {
      const row = asRecord(t);
      return {
        id: str(row.id),
        rootId: str(row.root_id),
        kind: str(row.kind),
        status: str(row.status),
        objective: str(row.objective),
      };
    }),
    events: (Array.isArray(r.events) ? r.events : []).map((e) => {
      const row = asRecord(e);
      return { seq: num(row.seq), ts: str(row.ts), type: str(row.type), snippet: str(row.snippet) };
    }),
    files: hits(r.files),
    notes: hits(r.notes),
    git: (Array.isArray(r.git) ? r.git : []).map((c) => {
      const row = asRecord(c);
      return {
        short: str(row.short),
        subject: str(row.subject),
        author: str(row.author),
        date: str(row.date),
      };
    }),
    tainted: r.tainted === true,
    degraded: r.degraded === true,
    knowledgeError: str(r.knowledge_error),
    gitSearched: r.git_searched === true,
    gitError: str(r.git_error),
  };
}

/** One row of whatever kind, flattened so the arrows walk the whole answer. */
export interface SearchRow {
  group: string;
  label: string;
  hint: string;
  action: { kind: "project"; id: string } | { kind: "task"; id: string } | { kind: "event" } | null;
}

export function flatten(data: SearchData): SearchRow[] {
  const rows: SearchRow[] = [];
  for (const p of data.projects) {
    rows.push({
      group: "PROJECTS",
      label: p.name,
      hint: `${p.status}${p.description ? ` — ${p.description}` : ""}`,
      action: { kind: "project", id: p.id },
    });
  }
  for (const t of data.tasks) {
    rows.push({
      group: "TASKS",
      label: t.id,
      hint: `${t.kind} · ${t.status}${t.objective ? ` — ${t.objective}` : ""}`,
      action: { kind: "task", id: t.id },
    });
  }
  for (const f of data.files) {
    rows.push({
      group: "FILES",
      label: f.path,
      hint: `${f.project}${f.anchor && f.anchor !== "(file)" ? ` · ${f.anchor}` : ""} — ${f.text}`,
      action: null,
    });
  }
  for (const n of data.notes) {
    rows.push({
      group: "NOTES",
      label: n.path,
      hint: `${n.anchor && n.anchor !== "(file)" ? `${n.anchor} — ` : ""}${n.text}`,
      action: null,
    });
  }
  for (const c of data.git) {
    rows.push({
      group: "GIT",
      label: c.short,
      hint: `${c.subject} · ${c.author} · ${c.date}`,
      action: null,
    });
  }
  for (const e of data.events) {
    rows.push({
      group: "EVENTS",
      label: `${e.seq} ${e.type}`,
      hint: e.snippet,
      action: { kind: "event" },
    });
  }
  return rows;
}

const GROUPS = ["PROJECTS", "TASKS", "FILES", "NOTES", "GIT", "EVENTS"] as const;

export interface GlobalSearchProps {
  open: boolean;
  /** The selected project's NAME, or null — scopes retrieval and enables the GIT group. */
  project: string | null;
  onClose(): void;
  onOpenProject(id: string): void;
  onInspectTask(id: string): void;
  onOpenTimeline(): void;
}

export function GlobalSearch({
  open,
  project,
  onClose,
  onOpenProject,
  onInspectTask,
  onOpenTimeline,
}: GlobalSearchProps) {
  const [query, setQuery] = useState("");
  const [data, setData] = useState<SearchData | null>(null);
  const [pending, setPending] = useState(false);
  const [index, setIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQuery("");
      setData(null);
      setIndex(0);
      inputRef.current?.focus();
    }
  }, [open]);

  // Debounced fetch: 300 ms after the last keystroke, never per key — the retrieval
  // half costs ~0.7-1.3 s through the serialised toolhost (§11, measured).
  useEffect(() => {
    if (!open) return;
    const q = query.trim();
    if (q.length < 2) {
      setData(null);
      return;
    }
    setPending(true);
    let cancelled = false;
    const timer = setTimeout(() => {
      const params = new URLSearchParams({ q });
      if (project) params.set("project", project);
      fetch(`/api/v1/search?${params.toString()}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          if (cancelled) return;
          setPending(false);
          if (d) {
            setData(toSearch(d));
            setIndex(0);
          }
        })
        .catch(() => {
          if (!cancelled) setPending(false);
        });
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [open, query, project]);

  const rows = useMemo(() => (data ? flatten(data) : []), [data]);

  if (!open) return null;

  const act = (row: SearchRow | undefined) => {
    if (!row?.action) return; // files, notes and git are previews — nothing to pretend
    if (row.action.kind === "project") onOpenProject(row.action.id);
    else if (row.action.kind === "task") onInspectTask(row.action.id);
    else onOpenTimeline();
    onClose();
  };

  /** Tab: jump to the first row of the next non-empty group (§11: Tab cycles groups). */
  const cycleGroup = (backwards: boolean) => {
    if (rows.length === 0) return;
    const present = GROUPS.filter((g) => rows.some((r) => r.group === g));
    const current = rows[index]?.group;
    const at = present.indexOf(current as (typeof GROUPS)[number]);
    const next =
      present[(at + (backwards ? -1 : 1) + present.length) % present.length] ?? present[0];
    setIndex(rows.findIndex((r) => r.group === next));
  };

  return (
    <div className="palette-backdrop" onClick={onClose} role="presentation">
      <div
        className="palette gsearch"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Global search"
      >
        <input
          ref={inputRef}
          value={query}
          placeholder="search projects, files, notes, tasks, git, events…"
          aria-label="Global search query"
          role="combobox"
          aria-expanded={rows.length > 0}
          aria-controls="gsearch-listbox"
          aria-autocomplete="list"
          aria-activedescendant={rows.length > 0 ? `gsearch-row-${index}` : undefined}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              onClose();
            } else if (e.key === "ArrowDown") {
              e.preventDefault();
              setIndex((i) => (i + 1) % Math.max(1, rows.length));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setIndex((i) => (i - 1 + rows.length) % Math.max(1, rows.length));
            } else if (e.key === "Tab") {
              e.preventDefault();
              cycleGroup(e.shiftKey);
            } else if (e.key === "Enter") {
              e.preventDefault();
              act(rows[index]);
            }
          }}
        />
        {data && (
          <div className="gs-meta muted" role="status">
            {rows.length} result{rows.length === 1 ? "" : "s"} · {data.elapsedMs} ms
            {data.tainted && (
              <span className="cite-taint">
                {" "}
                <span aria-hidden="true">⚠</span> includes content ORACLE did not author
              </span>
            )}
            {data.degraded && <> · keyword search only — embedding model unavailable</>}
            {data.knowledgeError && <> · files/notes unavailable: {data.knowledgeError}</>}
          </div>
        )}
        <ul className="palette-list" id="gsearch-listbox" role="listbox" aria-label="Search results">
          {GROUPS.map((group) => {
            const members = rows
              .map((row, i) => ({ row, i }))
              .filter(({ row }) => row.group === group);
            if (members.length === 0) return null;
            return (
              <li key={group} role="presentation">
                <ul role="group" aria-label={`${group} (${members.length})`} className="gs-group">
                  <li role="presentation" className="gs-head muted">
                    {group} ({members.length})
                  </li>
                  {members.map(({ row, i }) => (
                    <li
                      key={`${group}-${i}`}
                      id={`gsearch-row-${i}`}
                      role="option"
                      aria-selected={i === index}
                      className={i === index ? "sel" : ""}
                      onMouseEnter={() => setIndex(i)}
                      onClick={() => act(row)}
                    >
                      <span className="pi-label">{row.label}</span>
                      <span className="pi-hint">{row.hint}</span>
                    </li>
                  ))}
                </ul>
              </li>
            );
          })}
        </ul>
        {query.trim().length >= 2 && !pending && data && rows.length === 0 && (
          <p className="muted palette-empty" role="status">
            Nothing matches “{query.trim()}”.
          </p>
        )}
        {pending && (
          <p className="muted palette-empty" role="status">
            Searching…
          </p>
        )}
        {data && !data.gitSearched && (
          <p className="muted gs-note">
            git: select a project to search its history — searching every repository would queue
            behind real work.
          </p>
        )}
      </div>
    </div>
  );
}
