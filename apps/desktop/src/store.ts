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
import type { AgentState, Approval, ConnectionState, OracleEvent, Tier, ToolCall } from "./protocol";
import { asRecord, num, str } from "./protocol";

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
  lastSeq: number;

  apply(ev: OracleEvent): void;
  setConnection(c: ConnectionState, retryInSec: number): void;
  setGap(expected: number, got: number): void;
  reset(): void;
}

const MAX_EVENTS = 500;
const MAX_DECIDED = 10;

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
  lastSeq: 0,

  setConnection: (connection, retryInSec) => set({ connection, retryInSec }),

  setGap: (expected, got) =>
    set({ gapWarning: `missed events ${expected}..${got - 1} — history may be incomplete` }),

  reset: () => set({ turns: [], events: [], approvals: [], decided: [], gapWarning: null, lastSeq: 0 }),

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

        case "agent.state":
          next.agentState = str(ev.payload["state"], "idle") as AgentState;
          break;

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
