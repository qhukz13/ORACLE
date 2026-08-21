/**
 * Event-sourced client store.
 *
 * Mirrors the backend exactly: events arrive, a reducer folds them into view state.
 * There is no optimistic mutation — nothing appears in the UI until the server has
 * sequenced it (docs/UI.md#21-interaction-rules, rule 4).
 */

import { create } from "zustand";
import type { AgentState, ConnectionState, OracleEvent } from "./protocol";

export interface Turn {
  turnId: string;
  sessionId: string | null;
  userText: string;
  reply: string;
  done: boolean;
  outcome?: string;
}

interface State {
  connection: ConnectionState;
  retryInSec: number;
  agentState: AgentState;
  sessionId: string | null;
  turns: Turn[];
  events: OracleEvent[];
  gapWarning: string | null;
  lastSeq: number;

  apply(ev: OracleEvent): void;
  setConnection(c: ConnectionState, retryInSec: number): void;
  setGap(expected: number, got: number): void;
  reset(): void;
}

const MAX_EVENTS = 500;

export const useStore = create<State>((set) => ({
  connection: "connecting",
  retryInSec: 0,
  agentState: "idle",
  sessionId: null,
  turns: [],
  events: [],
  gapWarning: null,
  lastSeq: 0,

  setConnection: (connection, retryInSec) => set({ connection, retryInSec }),

  setGap: (expected, got) =>
    set({ gapWarning: `missed events ${expected}..${got - 1} — history may be incomplete` }),

  reset: () => set({ turns: [], events: [], gapWarning: null, lastSeq: 0 }),

  apply: (ev) =>
    set((s) => {
      const events = [...s.events, ev].slice(-MAX_EVENTS);
      const next: Partial<State> = { events, lastSeq: ev.seq };

      if (ev.session_id) next.sessionId = ev.session_id;

      switch (ev.type) {
        case "session.resync":
          return { ...next, turns: [], events: [ev], gapWarning: "resynced from server" };

        case "agent.state":
          next.agentState = String(ev.payload["state"] ?? "idle") as AgentState;
          break;

        case "turn.started": {
          if (!ev.turn_id) break;
          const turn: Turn = {
            turnId: ev.turn_id,
            sessionId: ev.session_id,
            userText: String(ev.payload["text"] ?? ""),
            reply: "",
            done: false,
          };
          next.turns = [...s.turns, turn];
          break;
        }

        case "message.delta":
          next.turns = s.turns.map((t) =>
            t.turnId === ev.turn_id ? { ...t, reply: t.reply + String(ev.payload["text"] ?? "") } : t,
          );
          break;

        case "message.completed":
          next.turns = s.turns.map((t) =>
            t.turnId === ev.turn_id ? { ...t, reply: String(ev.payload["text"] ?? t.reply) } : t,
          );
          break;

        case "turn.finished":
          next.turns = s.turns.map((t) =>
            t.turnId === ev.turn_id
              ? { ...t, done: true, outcome: String(ev.payload["outcome"] ?? "completed") }
              : t,
          );
          break;
      }

      return next;
    }),
}));
