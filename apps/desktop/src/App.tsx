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
import { MemoryView, toFacts } from "./components/MemoryView";
import type { MemoryFact } from "./components/MemoryView";
import { TaskTree } from "./components/TaskTree";
import { Inspector } from "./components/Inspector";
import { TerminalDock } from "./components/TerminalDock";
import { ToolCard } from "./components/ToolCard";
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

type Stage = "chat" | "events" | "memory";

export default function App() {
  const s = useStore();
  const [draft, setDraft] = useState("");
  const [stage, setStage] = useState<Stage>("chat");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [sidebar, setSidebar] = useState(true);
  const [dock, setDock] = useState(false);
  const [inspector, setInspector] = useState(true);
  const [selectedTurn, setSelectedTurn] = useState<string | null>(null);
  const [projects, setProjects] = useState<string[]>([]);
  const [pipelines, setPipelines] = useState<PipelineEntry[]>([]);
  const [projectsRoot, setProjectsRoot] = useState("");
  const [facts, setFacts] = useState<MemoryFact[]>([]);
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

  useEffect(() => {
    logEnd.current?.scrollIntoView({ behavior: "smooth" });
  }, [s.turns, s.events, stage]);

  const submit = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return false;
      return Boolean(
        clientRef.current?.send({
          type: "session.message",
          payload: { text: trimmed, session_id: s.sessionId },
        }),
      );
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
      } else if (e.key === "F1") {
        e.preventDefault();
        halt();
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
        <button className="ghost" onClick={() => setStage(stage === "chat" ? "events" : "chat")}>
          {stage === "chat" ? "Events" : "Chat"}
        </button>
        <button
          className="ghost"
          onClick={() => setStage(stage === "memory" ? "chat" : "memory")}
          title="what ORACLE has recorded, and why"
        >
          {stage === "memory" ? "Chat" : "Memory"}
        </button>
        <button className="halt" onClick={halt} title="F1">
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
            <h2>PROJECTS</h2>
            <ul className="tree">
              {projects.length === 0 && <li className="muted">none discovered</li>}
              {projects.map((p) => (
                <li key={p}>
                  <button className="tree-item" onClick={() => submit(`what is the status of ${p}`)}>
                    <i className="dot" aria-hidden="true" /> {p}
                  </button>
                </li>
              ))}
            </ul>

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
          <ConfirmationCenter approvals={s.approvals} decided={s.decided} onRespond={respond} />
          <DelegationPanel delegations={s.delegations} onDiscard={discard} />
          <TaskTree
            graphs={s.graphs}
            onCancelTask={cancelTask}
            onCancelGraph={cancelGraph}
          />

          {stage === "memory" ? (
            <MemoryView facts={facts} onForget={forget} />
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
                  className={t.turnId === selectedTurn ? "sel" : undefined}
                  onClick={() => setSelectedTurn(t.turnId)}
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
          <div ref={logEnd} />
        </main>

        {inspector && (
          <Inspector
            // Defaults to the most recent turn: that is the one being watched.
            turn={s.turns.find((t) => t.turnId === selectedTurn) ?? s.turns.at(-1) ?? null}
            traceId={s.events.at(-1)?.trace_id ?? ""}
            onUndo={undo}
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
