/**
 * Wire protocol types.
 *
 * P0 hand-writes these. From Phase 1 they are GENERATED from the pydantic models
 * (docs/API.md#1-shape) — a hand-written mirror of a server model is a bug waiting to
 * happen, so this file is a temporary exception, not a pattern.
 */

export const PROTOCOL_VERSION = 1;

export interface OracleEvent {
  v: number;
  seq: number;
  ts: string;
  type: string;
  session_id: string | null;
  turn_id: string | null;
  trace_id: string;
  payload: Record<string, unknown>;
}

export interface ClientCommand {
  v?: number;
  id?: string;
  type: string;
  payload?: Record<string, unknown>;
}

export type ConnectionState = "connecting" | "online" | "offline";

/** Mirrors the runtime state machine (docs/AGENT_RUNTIME.md#3-state-machine). */
export type AgentState =
  | "idle"
  | "understanding"
  | "retrieving"
  | "planning"
  | "awaiting_approval"
  | "executing"
  | "delegating"
  | "summarizing"
  | "error"
  | "halted";
