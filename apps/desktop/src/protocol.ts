/**
 * Wire protocol types.
 *
 * P0 hand-wrote these. From Phase 1 they are meant to be GENERATED from the pydantic
 * models (docs/API.md#1-shape) — a hand-written mirror of a server model is a bug
 * waiting to happen, so this file is a temporary exception, not a pattern.
 *
 * Everything below describes what the SERVER already decided. The UI never computes a
 * tier, a rule or a digest; it renders what the event says. That is what makes the
 * confirmation card trustworthy — see docs/UI.md#9-confirmation-center.
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

/** Risk tiers, as the server labels them (docs/SECURITY.md#risk-tiers). */
export type Tier = "T0" | "T1" | "T2" | "T3" | "T4";

/**
 * The confirmation card, straight off `approval.requested`.
 *
 * Every field the user needs to decide is here, because the card is built from the
 * event and nothing else. If something is missing from the event it could not have
 * informed the decision, and the fix is to add it to the event — never to fetch it
 * from somewhere the audit log cannot see.
 */
export interface Approval {
  approvalId: string;
  tool: string;
  tier: Tier;
  decision: string;
  rule: string;
  tainted: boolean;
  escalated: boolean;
  args: Record<string, unknown>;
  preview: Record<string, unknown>;
  /** Seconds remaining when the event was emitted. */
  expiresInSec: number;
  /**
   * When the SERVER issued it, from `ev.ts` — not when this client received it.
   *
   * The difference matters after a reload. History is replayed from seq 0, so an
   * approval from a backend that has since exited arrives looking brand new. Counting
   * from receipt would show a live countdown for a request nothing can answer; counting
   * from the server's timestamp shows it as expired, which is what it is.
   */
  issuedAt: number;
  resolution?: string;
}

/** One tool call, from `tool.started` and closed by `tool.finished`. */
export interface ToolCall {
  turnId: string | null;
  tool: string;
  tier: Tier | null;
  args: Record<string, unknown>;
  running: boolean;
  ok?: boolean;
  durationMs?: number;
  summary?: string;
  error?: string | null;
  undoId?: string | null;
  undone?: boolean;
}

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

export function str(value: unknown, fallback = ""): string {
  return value === null || value === undefined ? fallback : String(value);
}

export function num(value: unknown, fallback = 0): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}
