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
  /** Present on `task.*` and `delegate.event` — groups a delegation's stream. */
  task_id?: string | null;
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
  /** Sources behind a `know.*` result. Metadata only — the chunk text stays in the index. */
  citations?: unknown;
  /** True when any source is `local_foreign`; the turn is tainted (docs/SECURITY.md#6). */
  tainted?: boolean;
  /** True when the embedding model was unavailable and only BM25 ran. */
  degraded?: boolean;
}

/** One normalised event from a delegated agent's stream (`delegate.event`). */
export interface DelegateEvent {
  kind: string;
  text: string;
  tool: string | null;
  fromSubagent: boolean;
}

/**
 * One delegation, folded from `task.created` / `task.updated` / `delegate.event` /
 * `task.finished`. Like everything else in the store, it is a projection of events —
 * the UI never knows more about a delegation than the server has said.
 */
export interface Delegation {
  taskId: string;
  task: string;
  adapter: string;
  /** rendering | awaiting_egress | running | verifying — then "finished". */
  state: string;
  /** success | failed | fallback | refused | expired | halted. Set at the end. */
  outcome?: string;
  feed: DelegateEvent[];
  /** The `task.finished` payload: cost, diff stat, test verdict, workspace path. */
  result?: Record<string, unknown>;
}

/**
 * One task of a graph, folded from the `task.*` events that carry `source: "graph"`.
 *
 * The delegation lifecycle emits `task.*` for the same task id with its own payload
 * shape, which is why the discriminator exists: both streams are wanted, and a client
 * that guessed from payload keys would fold a delegation's "rendering" into a graph's
 * status column.
 */
export interface GraphTask {
  taskId: string;
  kind: string;
  status: string;
  /** The tasks this one waits on. Populated from `task.created` since 2026-08-26 — before
   *  that the scheduler never sent it, so this was always `[]` in the running app while a
   *  test that hand-wrote the field asserted it rendered. A list is not a graph without it. */
  dependsOn: string[];
  /** What the task is for, verbatim. An objective summarised on the way to the screen is an
   *  objective nobody read — the same rule the graph approval card follows. */
  objective?: string;
  role?: string;
  agent?: string;
  project?: string;
  /** Which try this is. A *retry* (`attempt 2` of the same row) is a different thing from a
   *  *replan* (`supersedes` a failed row), and §6b draws them differently. */
  attempt?: number;
  maxAttempts?: number;
  startedAt?: string;
  finishedAt?: string;
  /** What the row cost, where the runner knew. `undefined` means nobody measured — which is
   *  the honest answer for a local tool call, and is not the same as zero. */
  cost?: { tokens?: number; usd?: number };
  summary?: string;
  /** What ORACLE measured. Never merged with `claim` — that distinction is the whole
   *  verification design (docs/ORCHESTRATION.md §2). */
  evidence?: Record<string, unknown>;
  /** What the worker said about its own work. Shown as a quote, never as a verdict. */
  claim?: string;
  /** The failed attempt this task replaces (docs/ORCHESTRATION.md §4). Replanning is
   *  append-only: the superseded row is still here, still failed, and the tree shows it
   *  under its replacement rather than instead of it. */
  supersedes?: string;
}

export interface Graph {
  rootId: string;
  tasks: GraphTask[];
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
