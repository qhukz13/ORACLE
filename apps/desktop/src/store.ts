/**
 * Event-sourced client store.
 *
 * Mirrors the backend exactly: events arrive, a reducer folds them into view state.
 * There is no optimistic mutation — nothing appears in the UI until the server has
 * sequenced it (docs/UI.md#21-interaction-rules, rule 4).
 *
 * That rule matters most for approvals. Clicking Approve does not mark the card
 * approved; it sends a command, and the card changes when `approval.resolved` comes
 * back. A UI that showed the optimistic result would be showing a decision the runtime
 * might not have accepted.
 */

import { create } from "zustand";
import type {
  AgentState,
  Approval,
  ConnectionState,
  Delegation,
  Graph,
  GraphTask,
  OracleEvent,
  Tier,
  ToolCall,
} from "./protocol";
import { asRecord, num, str } from "./protocol";

export interface TermChunk {
  seq: number;
  data: string;
  dropped: number;
}

export interface Turn {
  turnId: string;
  sessionId: string | null;
  userText: string;
  reply: string;
  done: boolean;
  outcome?: string;
  /** Tool calls belonging to this turn, in order. */
  tools: ToolCall[];
}

interface State {
  connection: ConnectionState;
  retryInSec: number;
  agentState: AgentState;
  sessionId: string | null;
  turns: Turn[];
  events: OracleEvent[];
  /** Open approvals, oldest first. Each is decided individually — no "approve all". */
  approvals: Approval[];
  /** Resolved approvals, newest first, kept briefly so the card can show the outcome. */
  decided: Approval[];
  gapWarning: string | null;
  /**
   * Set by `system.degraded`. A missing model is a NORMAL state, not an error
   * (ADR-0011): the pre-router, slash commands and every deterministic path keep
   * working, so this is a banner explaining what is unavailable — never a modal, and
   * never something that blocks the composer.
   */
  degraded: { component: string; reason: string; remedy: string } | null;
  /**
   * Set by `knowledge.state`. Indexing is background work the user did not ask for at
   * the moment it happens, and the machine getting busy without explanation is the
   * thing this exists to prevent — it is a status line, never a modal.
   */
  indexing: { state: string; pending: number; indexed: number } | null;
  /**
   * Delegations, oldest first, folded from `task.*` and `delegate.event`. Kept after
   * they finish: the result card (diff, tests, cost, discard) IS the point.
   */
  delegations: Delegation[];
  /**
   * Task graphs by root id, folded from `task.*` events stamped `source: "graph"`.
   * Separate from `delegations` because they are different things seen through the same
   * event types: a delegation is one worker's lifecycle, a graph is the shape of the
   * work. A DELEGATION task appears in both, under the same task id, which is what lets
   * a reader click through from one to the other.
   */
  graphs: Graph[];
  lastSeq: number;
  /** The attached PTY, if any. One at a time in v1 — sub-tabs are later. */
  terminal: { ptyId: string | null; cwd: string };
  /** Output chunks in arrival order. Bounded: xterm.js owns the real scrollback. */
  termChunks: TermChunk[];

  apply(ev: OracleEvent): void;
  setConnection(c: ConnectionState, retryInSec: number): void;
  setGap(expected: number, got: number): void;
  reset(): void;
}

const MAX_EVENTS = 500;
const MAX_DECIDED = 10;
/** Feed lines kept per delegation. `delegate.event` is coalescable by contract, so the
 *  tail is what matters; decisions live on `task.*` and are never truncated. */
const MAX_FEED = 100;
const MAX_DELEGATIONS = 10;
/** Graphs kept in memory. A graph is at most 12 tasks (ORCHESTRATION.md §3), so this is
 *  bounded work, not a scrollback. */
const MAX_GRAPHS = 5;
/** Only what xterm.js has not consumed needs to live here — it keeps 5000 lines of its
 *  own scrollback, and duplicating that in the store would be pure waste. */
const MAX_TERM_CHUNKS = 200;

/** The most recent turn id, so a tool card lands on the turn that caused it. */
function attachTool(turns: Turn[], turnId: string | null, call: ToolCall): Turn[] {
  const target = turnId ?? turns[turns.length - 1]?.turnId;
  return turns.map((t) => (t.turnId === target ? { ...t, tools: [...t.tools, call] } : t));
}

function closeTool(turns: Turn[], turnId: string | null, payload: Record<string, unknown>): Turn[] {
  const target = turnId ?? turns[turns.length - 1]?.turnId;
  return turns.map((t) => {
    if (t.turnId !== target) return t;
    const tools = [...t.tools];
    // Close the most recent RUNNING call for this tool. Matching by tool id alone would
    // close the wrong card when the same tool ran twice in one turn.
    for (let i = tools.length - 1; i >= 0; i--) {
      const call = tools[i];
      if (call && call.running && call.tool === str(payload["tool"])) {
        tools[i] = {
          ...call,
          running: false,
          ok: Boolean(payload["ok"]),
          durationMs: num(payload["duration_ms"]),
          summary: str(payload["summary"]),
          error: payload["error"] === null ? null : str(payload["error"]),
          undoId: payload["undo_id"] === null ? null : str(payload["undo_id"]),
          // Present only for `know.*`. Undefined everywhere else, so ToolCard renders
          // nothing rather than an empty "0 sources" row.
          citations: payload["citations"],
          tainted: payload["tainted"] === true,
          degraded: payload["degraded"] === true,
        };
        break;
      }
    }
    return { ...t, tools };
  });
}

export const useStore = create<State>((set) => ({
  connection: "connecting",
  retryInSec: 0,
  agentState: "idle",
  sessionId: null,
  turns: [],
  events: [],
  approvals: [],
  decided: [],
  gapWarning: null,
  degraded: null,
  indexing: null,
  delegations: [],
  graphs: [],
  lastSeq: 0,
  terminal: { ptyId: null, cwd: "" },
  termChunks: [],

  setConnection: (connection, retryInSec) => set({ connection, retryInSec }),

  setGap: (expected, got) =>
    set({ gapWarning: `missed events ${expected}..${got - 1} — history may be incomplete` }),

  reset: () =>
    set({
      turns: [],
      events: [],
      approvals: [],
      decided: [],
      termChunks: [],
      terminal: { ptyId: null, cwd: "" },
      gapWarning: null,
      degraded: null,
      indexing: null,
      delegations: [],
      graphs: [],
      lastSeq: 0,
    }),

  apply: (ev) =>
    set((s) => {
      const events = [...s.events, ev].slice(-MAX_EVENTS);
      const next: Partial<State> = { events, lastSeq: ev.seq };

      if (ev.session_id) next.sessionId = ev.session_id;

      switch (ev.type) {
        case "session.resync":
          // A resync means our history is not the server's. Approvals go too: an
          // approval we cannot place in a turn is one the user cannot judge.
          return {
            ...next,
            turns: [],
            approvals: [],
            decided: [],
            events: [ev],
            gapWarning: "resynced from server",
          };

        case "system.degraded":
          next.degraded = {
            component: str(ev.payload["component"], "reasoning"),
            reason: str(ev.payload["reason"]),
            remedy: str(ev.payload["remedy"]),
          };
          break;

        case "agent.state":
          next.agentState = str(ev.payload["state"], "idle") as AgentState;
          break;

        case "knowledge.state": {
          const state = str(ev.payload["state"], "watching");
          // "watching" is the resting state and says nothing worth a line of UI. Only a
          // run that is happening, or one that needs the user (a stale index, a failure),
          // is worth showing.
          next.indexing =
            state === "watching" && !ev.payload["indexed"]
              ? null
              : {
                  state,
                  pending: num(ev.payload["pending"], 0),
                  indexed: num(ev.payload["indexed"], 0),
                };
          break;
        }

        case "turn.started": {
          if (!ev.turn_id) break;
          const turn: Turn = {
            turnId: ev.turn_id,
            sessionId: ev.session_id,
            userText: str(ev.payload["text"]),
            reply: "",
            done: false,
            tools: [],
          };
          next.turns = [...s.turns, turn];
          break;
        }

        case "message.delta":
          next.turns = s.turns.map((t) =>
            t.turnId === ev.turn_id ? { ...t, reply: t.reply + str(ev.payload["text"]) } : t,
          );
          break;

        case "message.completed":
          next.turns = s.turns.map((t) =>
            t.turnId === ev.turn_id ? { ...t, reply: str(ev.payload["text"], t.reply) } : t,
          );
          break;

        case "turn.finished":
          next.turns = s.turns.map((t) =>
            t.turnId === ev.turn_id
              ? { ...t, done: true, outcome: str(ev.payload["outcome"], "completed") }
              : t,
          );
          break;

        case "tool.started":
          next.turns = attachTool(s.turns, ev.turn_id, {
            turnId: ev.turn_id,
            tool: str(ev.payload["tool"]),
            tier: (str(ev.payload["tier"]) || null) as Tier | null,
            args: asRecord(ev.payload["args"]),
            running: true,
          });
          break;

        case "tool.finished":
          next.turns = closeTool(s.turns, ev.turn_id, ev.payload);
          break;

        case "approval.requested": {
          const approval: Approval = {
            approvalId: str(ev.payload["approval_id"]),
            tool: str(ev.payload["tool"]),
            tier: str(ev.payload["tier"], "T2") as Tier,
            decision: str(ev.payload["decision"]),
            rule: str(ev.payload["rule"]),
            tainted: Boolean(ev.payload["tainted"]),
            escalated: Boolean(ev.payload["escalated"]),
            args: asRecord(ev.payload["args"]),
            preview: asRecord(ev.payload["preview"]),
            expiresInSec: num(ev.payload["expires_in_s"], 0),
            // The server's clock, not ours: a replayed approval must look as old as it
            // actually is. `Date.parse` of an ISO-8601 Z stamp is exact.
            issuedAt: Date.parse(ev.ts) || Date.now(),
          };
          if (s.approvals.some((a) => a.approvalId === approval.approvalId)) break;
          // An approval that had ALREADY expired when it reached us never joins the
          // queue. History is replayed from seq 0 after a reload, so requests issued by
          // a backend that has since exited arrive looking new — and a dead card at the
          // head of the queue hides the live one behind it. This is the server's own
          // rule, applied on arrival rather than on a timer.
          if (approval.issuedAt + approval.expiresInSec * 1000 <= Date.now()) break;
          next.approvals = [...s.approvals, approval];
          break;
        }

        case "term.opened":
          next.terminal = { ptyId: str(ev.payload["pty_id"]), cwd: str(ev.payload["cwd"]) };
          next.termChunks = [];
          break;

        case "term.closed":
          // Only clear if it is OUR session closing. A stale event replayed from a
          // previous backend must not detach the terminal being looked at right now.
          if (str(ev.payload["pty_id"]) === s.terminal.ptyId) {
            next.terminal = { ptyId: null, cwd: "" };
          }
          break;

        case "term.output": {
          if (str(ev.payload["pty_id"]) !== s.terminal.ptyId) break;
          const chunk: TermChunk = {
            seq: ev.seq,
            data: str(ev.payload["data"]),
            dropped: num(ev.payload["dropped"], 0),
          };
          next.termChunks = [...s.termChunks, chunk].slice(-MAX_TERM_CHUNKS);
          break;
        }

        case "task.created": {
          const taskId = str(ev.task_id);
          if (taskId && ev.payload["source"] === "graph") {
            next.graphs = upsertTask(s.graphs, ev, {
              taskId,
              kind: str(ev.payload["kind"]),
              status: "pending",
              // The scheduler sends this now. It did not until 2026-08-26, which made the
              // dependency line in TaskTree dead in the running app.
              dependsOn: Array.isArray(ev.payload["depends_on"])
                ? (ev.payload["depends_on"] as unknown[]).map(String)
                : [],
              objective: optional(ev.payload["objective"]),
              role: optional(ev.payload["role"]),
              agent: optional(ev.payload["agent"]),
              project: optional(ev.payload["project"]),
              attempt: ev.payload["attempt"] == null ? undefined : num(ev.payload["attempt"], 1),
              maxAttempts:
                ev.payload["max_attempts"] == null
                  ? undefined
                  : num(ev.payload["max_attempts"], 1),
              // Carried on the first event about the row, so a replacement can be placed
              // without re-querying the tree and diffing it.
              supersedes:
                ev.payload["supersedes"] == null ? undefined : str(ev.payload["supersedes"]),
            });
            break;
          }
          // Otherwise: a delegation's own lifecycle. Guarding on the tool keeps a future
          // task type from being folded into a panel that does not understand it.
          if (!taskId || str(ev.payload["tool"]) !== "ai.delegate") break;
          if (s.delegations.some((d) => d.taskId === taskId)) break;
          next.delegations = [
            ...s.delegations,
            {
              taskId,
              task: str(ev.payload["task"]),
              adapter: str(ev.payload["adapter"]),
              state: "created",
              feed: [],
            },
          ].slice(-MAX_DELEGATIONS);
          break;
        }

        case "task.updated":
          if (ev.payload["source"] === "graph") {
            next.graphs = patchTask(s.graphs, ev, (t) => ({
              ...t,
              status: str(ev.payload["status"], t.status),
            }));
            break;
          }
          next.delegations = s.delegations.map((d) =>
            d.taskId === str(ev.task_id) ? { ...d, state: str(ev.payload["state"], d.state) } : d,
          );
          break;

        case "delegate.event":
          next.delegations = s.delegations.map((d) =>
            d.taskId === str(ev.task_id)
              ? {
                  ...d,
                  feed: [
                    ...d.feed,
                    {
                      kind: str(ev.payload["kind"]),
                      text: str(ev.payload["text"]),
                      tool: ev.payload["tool"] == null ? null : str(ev.payload["tool"]),
                      fromSubagent: ev.payload["from_subagent"] === true,
                    },
                  ].slice(-MAX_FEED),
                }
              : d,
          );
          break;

        case "task.finished":
          if (ev.payload["source"] === "graph") {
            next.graphs = patchTask(s.graphs, ev, (t) => ({
              ...t,
              status: str(ev.payload["status"], t.status),
              summary: str(ev.payload["summary"]),
              evidence: asRecord(ev.payload["evidence"]),
              // Kept as its own field, deliberately: rendering a worker's claim beside
              // ORACLE's evidence is fine; rendering it *as* evidence is not.
              claim: ev.payload["claim"] == null ? undefined : str(ev.payload["claim"]),
              cost:
                ev.payload["cost"] == null
                  ? undefined
                  : (asRecord(ev.payload["cost"]) as GraphTask["cost"]),
              attempt: ev.payload["attempt"] == null ? t.attempt : num(ev.payload["attempt"], 1),
              startedAt: optional(ev.payload["started_at"]) ?? t.startedAt,
              finishedAt: optional(ev.payload["finished_at"]) ?? t.finishedAt,
            }));
            break;
          }
          next.delegations = s.delegations.map((d) =>
            d.taskId === str(ev.task_id)
              ? {
                  ...d,
                  state: "finished",
                  outcome: str(ev.payload["outcome"], "finished"),
                  result: ev.payload,
                }
              : d,
          );
          break;

        case "approval.resolved": {
          const id = str(ev.payload["approval_id"]);
          const resolution = str(ev.payload["resolution"]);
          const match = s.approvals.find((a) => a.approvalId === id);
          next.approvals = s.approvals.filter((a) => a.approvalId !== id);
          if (match) {
            next.decided = [{ ...match, resolution }, ...s.decided].slice(0, MAX_DECIDED);
          }
          break;
        }
      }

      return next;
    }),
}));

/** Add a task to its graph, creating the graph on first sight. Graphs are keyed by
 *  `root_id` from the payload — the event carries it precisely so a client never has to
 *  infer which graph a task belongs to. */
/** A payload field that may legitimately be absent, as `string | undefined`.
 *
 *  `str(x)` turns a missing field into `""`, which then renders as an empty label rather than
 *  as nothing at all. The distinction matters most for `agent`: a TOOL task genuinely has no
 *  agent, and a blank column is the truthful rendering of that, not an em-dash pretending. */
function optional(value: unknown): string | undefined {
  return value === null || value === undefined ? undefined : String(value);
}

function upsertTask(graphs: Graph[], ev: OracleEvent, task: GraphTask): Graph[] {
  const rootId = str(ev.payload["root_id"]);
  if (!rootId) return graphs;
  const existing = graphs.find((g) => g.rootId === rootId);
  if (!existing) return [...graphs, { rootId, tasks: [task] }].slice(-MAX_GRAPHS);
  if (existing.tasks.some((t) => t.taskId === task.taskId)) return graphs;
  return graphs.map((g) => (g.rootId === rootId ? { ...g, tasks: [...g.tasks, task] } : g));
}

/** Update one task in place. A task the client never saw created is ignored rather than
 *  invented: a half-known graph rendered as if it were whole is worse than a gap. */
function patchTask(
  graphs: Graph[],
  ev: OracleEvent,
  patch: (t: GraphTask) => GraphTask,
): Graph[] {
  const taskId = str(ev.task_id);
  const rootId = str(ev.payload["root_id"]);
  if (!taskId || !rootId) return graphs;
  return graphs.map((g) =>
    g.rootId === rootId
      ? { ...g, tasks: g.tasks.map((t) => (t.taskId === taskId ? patch(t) : t)) }
      : g,
  );
}
