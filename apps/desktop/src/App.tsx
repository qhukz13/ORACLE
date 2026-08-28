/**
 * The app shell — docs/UI.md#2-layout.
 *
 * Command bar, workspace sidebar, centre stage, dock. Deliberately NOT the orbital
 * view: that is Phase 9, and building the decorative centrepiece before the functional
 * shell is the classic way this kind of project dies at 80%.
 *
 * Everything rendered here comes from the event stream. There is no optimistic
 * mutation and no local truth — most importantly for approvals, where clicking
 * Approve sends a command and the card changes only when the server says it did.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { OracleClient } from "./client";
import { CommandPalette, type PipelineEntry } from "./components/CommandPalette";
import { ConfirmationCenter } from "./components/ConfirmationCenter";
import { DelegationPanel } from "./components/DelegationPanel";
import { Briefing, toBriefing } from "./components/Briefing";
import type { BriefingData } from "./components/Briefing";
import { ProjectList, toObservation, toProjects } from "./components/ProjectList";
import type { Observation, ProjectRow, ProjectsData } from "./components/ProjectList";
import { MemoryView, toFacts } from "./components/MemoryView";
import type { MemoryFact } from "./components/MemoryView";
import { KnowledgeHealth, toHealth } from "./components/KnowledgeHealth";
import type { KnowledgeHealthData } from "./components/KnowledgeHealth";
import { TaskTree } from "./components/TaskTree";
import { Inspector } from "./components/Inspector";
import { TerminalDock } from "./components/TerminalDock";
import { ToolCard } from "./components/ToolCard";
import { ViewTabs } from "./components/ViewTabs";
import type { Stage } from "./components/ViewTabs";
import { useStore } from "./store";

const STATE_LABEL: Record<string, string> = {
  idle: "IDLE",
  understanding: "THINKING",
  retrieving: "SEARCHING",
  planning: "PLANNING",
  awaiting_approval: "NEEDS YOU",
  executing: "RUNNING",
  delegating: "DELEGATED",
  summarizing: "WRAPPING UP",
  error: "ERROR",
  halted: "HALTED",
};

/** What is selected, app-wide: a turn or a task. One selection model drives the
 *  inspector (docs/UI.md §21 rule 1) — the P12-T4 stopgap that pushed a task id into a
 *  turn-only selector is exactly the bug a second selection model produces. */
type Selection = { kind: "turn" | "task"; id: string } | null;

/** Ctrl+digit → stage, for the four primary views (docs/UI.md §16, corrected in place:
 *  Orbit takes a slot when it exists — it is P11-T2, gated on OQ-14). */
const STAGE_KEYS: Record<string, Stage> = { "1": "chat", "2": "tasks", "3": "events", "4": "memory" };

const NO_PROJECTS: ProjectsData = { projects: [], candidates: [], projectsRoot: "" };

export default function App() {
  const s = useStore();
  const [draft, setDraft] = useState("");
  const [stage, setStage] = useState<Stage>("chat");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [sidebar, setSidebar] = useState(true);
  const [dock, setDock] = useState(false);
  const [inspector, setInspector] = useState(true);
  const [selection, setSelection] = useState<Selection>(null);
  const [projects, setProjects] = useState<string[]>([]);
  const [tracked, setTracked] = useState<ProjectsData>(NO_PROJECTS);
  const [selectedProject, setSelectedProject] = useState<string | null>(null);
  const [observation, setObservation] = useState<Observation | null>(null);
  const [briefing, setBriefing] = useState<BriefingData | null>(null);
  //: Set once, after the first briefing arrives. Without it, dismissing the briefing
  //: would be undone by the next fetch deciding to show it again.
  const briefingShown = useRef(false);
  const [pipelines, setPipelines] = useState<PipelineEntry[]>([]);
  const [projectsRoot, setProjectsRoot] = useState("");
  const [facts, setFacts] = useState<MemoryFact[]>([]);
  const [health, setHealth] = useState<KnowledgeHealthData | null>(null);
  const clientRef = useRef<OracleClient | null>(null);
  const logEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const client = new OracleClient({
      onEvent: (ev) => useStore.getState().apply(ev),
      onStateChange: (state, retry) => useStore.getState().setConnection(state, retry),
      onGap: (expected, got) => useStore.getState().setGap(expected, got),
    });
    clientRef.current = client;
    client.connect();
    return () => client.close();
  }, []);

  // The project list is named state, so it comes from REST rather than the stream
  // (docs/API.md#1-shape: REST for named state, WS for streams).
  useEffect(() => {
    let cancelled = false;
    fetch("/api/v1/status")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled || !d) return;
        if (Array.isArray(d.projects)) setProjects(d.projects as string[]);
        if (Array.isArray(d.pipelines)) setPipelines(d.pipelines as PipelineEntry[]);
        if (typeof d.projects_root === "string") setProjectsRoot(d.projects_root);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [s.connection]);

  // Projects and the briefing are named state too. Re-read when a task or a project event
  // lands rather than patching a local copy: a second projection of project state is
  // exactly the thing that gets to disagree with the first one.
  const projectSeq = useMemo(
    () =>
      s.events.filter(
        (e) => e.type.startsWith("task.") || e.type === "continue.derived",
      ).length,
    [s.events],
  );
  useEffect(() => {
    let cancelled = false;
    fetch("/api/v1/projects")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!cancelled && d) setTracked(toProjects(d));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [s.connection, projectSeq]);

  // The SELECTED project's observed half — branch, ahead/behind, dirty — read fresh on
  // every selection and every task event, held nowhere else. One row by design: the
  // full fan-out misses the 1 s budget 2–3× and the toolhost serialises invocations, so
  // observing rows nobody is looking at would queue behind real work (OQ-24, measured
  // 2026-08-28 by scripts/measure_observation.py).
  useEffect(() => {
    if (!selectedProject) {
      setObservation(null);
      return;
    }
    let cancelled = false;
    setObservation(null); // never show one project's branch on another project's row
    fetch(`/api/v1/projects/${encodeURIComponent(selectedProject)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!cancelled && d) setObservation(toObservation(d));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [selectedProject, projectSeq]);

  // The briefing is fetched, never streamed, and **reading it does not consume it**
  // (docs/PROJECT_STATE.md#6). Only the dismiss button acknowledges.
  useEffect(() => {
    let cancelled = false;
    fetch("/api/v1/briefing")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled || !d) return;
        const next = toBriefing(d);
        setBriefing(next);
        // Auto-switch to it exactly once, on the first paint after connecting, and only
        // if there is something to say. Doing it on every refresh would yank the stage
        // out from under someone mid-sentence.
        if (!briefingShown.current) {
          briefingShown.current = true;
          if (!next.empty) setStage("briefing");
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [s.connection, projectSeq]);

  const acknowledge = useCallback(
    (throughSeq: number, projectId?: string) => {
      // The sequence comes from the payload that was DISPLAYED, never from a fresh read:
      // work that arrived while the reader was looking must not be marked as seen.
      const q = new URLSearchParams({ through_seq: String(throughSeq) });
      if (projectId) q.set("project_id", projectId);
      fetch(`/api/v1/briefing/ack?${q.toString()}`, { method: "POST" })
        .then(() => fetch("/api/v1/briefing"))
        .then((r) => (r && r.ok ? r.json() : null))
        .then((d) => {
          if (d) setBriefing(toBriefing(d));
          setStage("chat");
        })
        .catch(() => undefined);
    },
    [],
  );

  const registerProject = useCallback((name: string) => {
    fetch(`/api/v1/projects?name=${encodeURIComponent(name)}`, { method: "POST" })
      .then(() => fetch("/api/v1/projects"))
      .then((r) => (r && r.ok ? r.json() : null))
      .then((d) => {
        if (d) setTracked(toProjects(d));
      })
      .catch(() => undefined);
  }, []);

  const openProject = useCallback((name: string) => {
    const row = tracked.projects.find((p) => p.name === name);
    setSelectedProject(row ? row.id : null);
  }, [tracked.projects]);

  // What ORACLE remembers is named state, so it comes from REST like the project list.
  // Re-read on every memory event rather than patching a local copy: the store is small,
  // the query is a SELECT, and a second projection of a belief system is exactly the
  // thing that gets to disagree with the first one.
  const memorySeq = useMemo(
    () => s.events.filter((e) => e.type.startsWith("memory.")).length,
    [s.events],
  );
  useEffect(() => {
    let cancelled = false;
    fetch("/api/v1/memory")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!cancelled && d) setFacts(toFacts(d));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [s.connection, memorySeq]);

  const forget = useCallback((factId: string) => {
    clientRef.current?.send({ type: "memory.forget", payload: { fact_id: factId } });
  }, []);

  // Index health is named state like the rest: REST, re-read when a knowledge event
  // lands. The watcher indexes in the background, so the numbers move without a turn.
  const knowledgeSeq = useMemo(
    () => s.events.filter((e) => e.type.startsWith("knowledge.")).length,
    [s.events],
  );
  useEffect(() => {
    let cancelled = false;
    fetch("/api/v1/knowledge")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!cancelled && d) setHealth(toHealth(d));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [s.connection, knowledgeSeq]);

  const reindex = useCallback((full: boolean) => {
    // Through the API and therefore through the tool layer and the policy gate — the
    // UI computes nothing and executes nothing (docs/API.md, `POST /knowledge/reindex`).
    fetch(`/api/v1/knowledge/reindex?full=${full ? "true" : "false"}`, { method: "POST" })
      .then(() => fetch("/api/v1/knowledge"))
      .then((r) => (r && r.ok ? r.json() : null))
      .then((d) => {
        if (d) setHealth(toHealth(d));
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    logEnd.current?.scrollIntoView({ behavior: "smooth" });
  }, [s.turns, s.events, stage]);

  const submit = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return false;
      const sent = Boolean(
        clientRef.current?.send({
          type: "session.message",
          payload: { text: trimmed, session_id: s.sessionId },
        }),
      );
      // §2: a new conversation auto-switches to Chat. This is the one stage change the
      // agent may make (§21 rule 6) — and it is really the user's: every caller of
      // submit is the composer, the palette, or a sidebar click.
      if (sent) setStage("chat");
      return sent;
    },
    [s.sessionId],
  );

  const send = () => {
    if (submit(draft)) setDraft("");
  };

  const respond = useCallback((approvalId: string, approve: boolean) => {
    clientRef.current?.send({
      type: "approval.respond",
      payload: { approval_id: approvalId, decision: approve ? "approve" : "reject" },
    });
  }, []);

  const undo = useCallback((undoId: string) => {
    clientRef.current?.send({ type: "undo", payload: { undo_id: undoId } });
  }, []);

  const discard = useCallback((taskId: string) => {
    // Not optimistic: the card keeps its workspace line until the server's events say
    // the worktree is gone. Discarding is safe by construction — the real tree was
    // never touched (docs/INTEGRATIONS.md §7).
    clientRef.current?.send({ type: "delegate.discard", payload: { task_id: taskId } });
  }, []);

  const cancelTask = useCallback((rootId: string, taskId: string) => {
    // Not optimistic, like every other command here: the row keeps its status until a
    // `task.*` event says otherwise. The scheduler is the one that decides a task is
    // cancelled, and it says so through the same stream as everything else.
    clientRef.current?.send({
      type: "graph.cancel",
      payload: { root_id: rootId, task_id: taskId },
    });
  }, []);

  const cancelGraph = useCallback((rootId: string) => {
    clientRef.current?.send({ type: "graph.cancel", payload: { root_id: rootId } });
  }, []);

  // Stable callbacks: xterm.js subscribes once on mount and holds these in a closure.
  const termInput = useCallback((data: string) => {
    clientRef.current?.send({
      type: "term.input",
      payload: { pty_id: useStore.getState().terminal.ptyId, data },
    });
  }, []);

  const termResize = useCallback((cols: number, rows: number) => {
    clientRef.current?.send({
      type: "term.resize",
      payload: { pty_id: useStore.getState().terminal.ptyId, cols, rows },
    });
  }, []);

  const termOpen = useCallback(() => {
    // Opens in the project root, which is inside a scope. A terminal ORACLE cannot
    // reach the working directory of would be a terminal in name only.
    clientRef.current?.send({
      type: "term.open",
      payload: { path: projectsRoot, session_id: useStore.getState().sessionId },
    });
  }, [projectsRoot]);

  const termClose = useCallback(() => {
    clientRef.current?.send({
      type: "term.close",
      payload: { pty_id: useStore.getState().terminal.ptyId },
    });
  }, []);

  const halt = useCallback(() => {
    clientRef.current?.send({ type: "halt", payload: { reason: "user pressed HALT" } });
  }, []);

  // Global keys. HALT is deliberately reachable from every state and never touches the
  // model (docs/API.md, `halt`).
  //
  // It is `Ctrl+Alt+Shift+H`, and the awkwardness is the feature: UI.md §16 asks for four
  // keys "so it cannot be hit by accident". This was bound to **`F1`** until 2026-08-26,
  // which is the opposite of that — one key, the universal help key, sitting next to Esc.
  // HALT cancels every running task, terminates every job object and puts policy into
  // deny-all until a human resumes it; reaching for help and stopping the machine instead
  // is not a keybinding, it is a trap. `F1` is now free for the cheat sheet §16 says it is.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "b") {
        e.preventDefault();
        setSidebar((v) => !v);
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "i") {
        e.preventDefault();
        setInspector((v) => !v);
      } else if ((e.ctrlKey || e.metaKey) && e.key === "`") {
        e.preventDefault();
        setDock((v) => !v);
      } else if (e.ctrlKey && e.altKey && e.shiftKey && e.key.toLowerCase() === "h") {
        e.preventDefault();
        halt();
      } else {
        // Ctrl+1..4 → the four primary stages (UI.md §16). `!altKey` matters: AltGr
        // arrives as Ctrl+Alt, and a layout where AltGr+digit types a character must
        // not lose the character to a stage switch.
        const to =
          (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey ? STAGE_KEYS[e.key] : undefined;
        if (to) {
          e.preventDefault();
          setStage(to);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [halt]);

  const banner = useMemo(() => {
    if (s.connection === "offline")
      return { cls: "err", text: `Backend offline — reconnecting in ${s.retryInSec}s` };
    if (s.connection === "connecting") return { cls: "warn", text: "Connecting…" };
    return null;
  }, [s.connection, s.retryInSec]);

  const waiting = s.approvals.length;

  // Resolved from the store on every render, never held: MAX_GRAPHS bounds the search
  // to at most 5 × 12 tasks, and a held copy is a projection that gets to disagree.
  const selectedTask =
    selection?.kind === "task"
      ? (s.graphs.flatMap((g) => g.tasks).find((t) => t.taskId === selection.id) ?? null)
      : null;

  return (
    <div className="app">
      <header className="bar">
        <span className="brand">ORACLE</span>
        <button className="ghost" onClick={() => setPaletteOpen(true)} title="Ctrl+K">
          ⌘ search / ask…
        </button>
        <span className={`state st-${s.agentState}`}>
          <i className="dot" aria-hidden="true" />
          {STATE_LABEL[s.agentState] ?? s.agentState.toUpperCase()}
        </span>
        <span className="spacer" />
        {waiting > 0 && (
          <span className="needs-you" role="status">
            ⚠ {waiting} waiting on you
          </span>
        )}
        <span className="meta">seq {s.lastSeq}</span>
        {/* The briefing tab keeps its badge until acknowledged, never removed: "what
            happened while I was away" must stay reachable (docs/UI.md §7b). */}
        <ViewTabs
          stage={stage}
          onSwitch={setStage}
          attn={{ briefing: Boolean(briefing && !briefing.empty) }}
        />
        <button className="halt" onClick={halt} title="HALT — Ctrl+Alt+Shift+H">
          HALT
        </button>
      </header>

      {banner && (
        <div className={`banner ${banner.cls}`} role="status">
          {banner.text}
        </div>
      )}
      {s.gapWarning && (
        <div className="banner warn" role="alert">
          ⚠ {s.gapWarning}
        </div>
      )}
      {s.degraded && (
        // A banner, never a modal: everything deterministic still works without a
        // model, and blocking the UI would make a degraded ORACLE useless rather than
        // reduced (ADR-0011).
        <div className="banner warn" role="status">
          ⚠ {s.degraded.component} is offline — {s.degraded.reason}
          {s.degraded.remedy ? `. Try: ${s.degraded.remedy}` : ""}. Slash commands and the
          command palette still work.
        </div>
      )}
      {s.indexing && (
        // Quieter than the banners above it, and deliberately: nothing is wrong. This is
        // the machine doing background work the user did not ask for at this moment, and
        // an unexplained busy CPU is what it exists to prevent. `stale` is the exception —
        // an index built by a different model answers every question badly until rebuilt.
        <div className={s.indexing.state === "stale" ? "banner warn" : "banner info"} role="status">
          {s.indexing.state === "stale"
            ? "⚠ The knowledge index was built by a different embedding model. Rebuild it to make search work again."
            : s.indexing.state === "indexing"
              ? `Indexing ${s.indexing.pending} changed file${s.indexing.pending === 1 ? "" : "s"}…`
              : `Indexed ${s.indexing.indexed} changed file${s.indexing.indexed === 1 ? "" : "s"}.`}
        </div>
      )}

      <div className="body">
        {sidebar && (
          <nav className="sidebar" aria-label="Workspace">
            <ProjectList
              data={tracked}
              selected={selectedProject}
              observation={observation}
              onSelect={(p: ProjectRow) => {
                setSelectedProject(p.id);
                submit(`continue ${p.name}`);
              }}
              onRegister={registerProject}
            />

            {/* The only sidebar item allowed to demand attention (docs/UI.md#4). */}
            <h2 className={waiting > 0 ? "attn" : ""}>
              WAITING ON ME {waiting > 0 && <span className="count">{waiting}</span>}
            </h2>
            <ul className="tree">
              {waiting === 0 && <li className="muted">nothing</li>}
              {s.approvals.map((a) => (
                <li key={a.approvalId} className="attn">
                  {a.tool} <span className="tier-chip">{a.tier}</span>
                </li>
              ))}
            </ul>
          </nav>
        )}

        <main className="stage">
          {/* The safety surface is not a tab. Approvals and running delegations stay
              visible on every stage — a card that can be hidden behind a view switch is
              a card that expires unseen (approvals expire in 180 s). */}
          <ConfirmationCenter approvals={s.approvals} decided={s.decided} onRespond={respond} />
          <DelegationPanel delegations={s.delegations} onDiscard={discard} />

          <div role="tabpanel" id="stage-panel" aria-labelledby={`tab-${stage}`}>
          {stage === "briefing" ? (
            <Briefing
              data={briefing ?? { throughSeq: 0, sinceTs: null, empty: true, text: "", projects: [], system: { restartedAt: null, unclean: false, degraded: [], errors: 0 } }}
              onAcknowledge={acknowledge}
              // A task id selects a TASK. Until 2026-08-28 this pushed the id into the
              // turn selector, where it matched nothing and the inspector silently showed
              // the latest turn instead — it looked right and was wrong (the P12-T4
              // stopgap). Opening the rail is part of the affordance: an inspect button
              // that selects into a closed inspector did nothing visible.
              onInspect={(taskId) => {
                setSelection({ kind: "task", id: taskId });
                setInspector(true);
              }}
              onOpenProject={openProject}
            />
          ) : stage === "memory" ? (
            <MemoryView facts={facts} onForget={forget} />
          ) : stage === "tasks" ? (
            s.graphs.length > 0 ? (
              <TaskTree
                graphs={s.graphs}
                onCancelTask={cancelTask}
                onCancelGraph={cancelGraph}
                onSelect={(taskId) => {
                  setSelection({ kind: "task", id: taskId });
                  setInspector(true);
                }}
              />
            ) : (
              // §17: empty is a stated absence, never a blank page.
              <div className="empty">
                <p>No task graphs yet.</p>
                <p className="muted">
                  A graph appears when a plan is approved. The <code>oracle-selfcheck</code>{" "}
                  pipeline is the local, no-egress way to run a first one.
                </p>
              </div>
            )
          ) : stage === "knowledge" ? (
            health ? (
              <KnowledgeHealth
                data={health}
                reindexing={s.indexing?.state === "indexing"}
                onReindex={reindex}
              />
            ) : (
              <p className="muted">Reading the index…</p>
            )
          ) : stage === "events" ? (
            <table className="events">
              <thead>
                <tr>
                  <th>seq</th>
                  <th>type</th>
                  <th>trace</th>
                  <th>payload</th>
                </tr>
              </thead>
              <tbody>
                {s.events.map((e) => (
                  <tr key={e.seq}>
                    <td className="num">{e.seq}</td>
                    <td className="type">{e.type}</td>
                    <td className="muted">{e.trace_id}</td>
                    <td className="muted">{JSON.stringify(e.payload).slice(0, 90)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : s.turns.length === 0 ? (
            <div className="empty">
              <p>Nothing yet.</p>
              <p className="muted">
                Try <kbd>Ctrl</kbd>+<kbd>K</kbd>, or ask for something — "run the tests for
                Asterim".
              </p>
            </div>
          ) : (
            <ul className="turns">
              {s.turns.map((t) => (
                <li
                  key={t.turnId}
                  className={
                    selection?.kind === "turn" && selection.id === t.turnId ? "sel" : undefined
                  }
                  onClick={() => setSelection({ kind: "turn", id: t.turnId })}
                >
                  <div className="msg user">{t.userText}</div>
                  {t.tools.map((call, i) => (
                    <ToolCard key={`${t.turnId}-${i}`} call={call} onUndo={undo} />
                  ))}
                  <div className="msg agent">
                    {t.reply || <span className="muted">…</span>}
                    {!t.done && <span className="caret" aria-hidden="true" />}
                    {t.outcome && t.outcome !== "completed" && (
                      <span className="outcome"> [{t.outcome}]</span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
          </div>
          <div ref={logEnd} />
        </main>

        {inspector && (
          <Inspector
            // Defaults to the most recent turn: that is the one being watched. A task
            // selection renders above it rather than instead of it (the inspector's own
            // header explains why).
            turn={
              selection?.kind === "turn"
                ? (s.turns.find((t) => t.turnId === selection.id) ?? s.turns.at(-1) ?? null)
                : (s.turns.at(-1) ?? null)
            }
            traceId={s.events.at(-1)?.trace_id ?? ""}
            onUndo={undo}
            task={selectedTask}
            taskMissing={selection?.kind === "task" && !selectedTask ? selection.id : null}
          />
        )}
      </div>

      {dock && (
        <TerminalDock
          ptyId={s.terminal.ptyId}
          cwd={s.terminal.cwd}
          chunks={s.termChunks}
          onInput={termInput}
          onResize={termResize}
          onOpen={termOpen}
          onClose={termClose}
        />
      )}

      <footer className="composer">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder={s.connection === "online" ? "Message ORACLE…" : "Waiting for backend…"}
          disabled={s.connection !== "online"}
          aria-label="Message"
        />
        <button onClick={send} disabled={s.connection !== "online" || !draft.trim()}>
          Send
        </button>
      </footer>

      <CommandPalette
        open={paletteOpen}
        projects={projects}
        pipelines={pipelines}
        onClose={() => setPaletteOpen(false)}
        onSubmit={submit}
      />
    </div>
  );
}
