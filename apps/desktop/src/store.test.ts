import { beforeEach, describe, expect, it } from "vitest";
import type { OracleEvent } from "./protocol";
import { useStore } from "./store";

function ev(seq: number, type: string, payload: Record<string, unknown> = {}, turn = "t_1"): OracleEvent {
  return {
    v: 1,
    seq,
    ts: "2026-08-21T00:00:00.000Z",
    type,
    session_id: "s_1",
    turn_id: turn,
    trace_id: "tr_1",
    payload,
  };
}

describe("store.apply", () => {
  beforeEach(() => {
    useStore.getState().reset();
    useStore.setState({ agentState: "idle", sessionId: null });
  });

  it("folds a full turn into one rendered exchange", () => {
    const { apply } = useStore.getState();
    apply(ev(1, "turn.started", { text: "hi" }));
    apply(ev(2, "message.delta", { text: "echo: " }));
    apply(ev(3, "message.delta", { text: "hi" }));
    apply(ev(4, "turn.finished", { outcome: "completed" }));

    const turns = useStore.getState().turns;
    expect(turns).toHaveLength(1);
    expect(turns[0]?.userText).toBe("hi");
    expect(turns[0]?.reply).toBe("echo: hi");
    expect(turns[0]?.done).toBe(true);
  });

  it("tracks agent state from the runtime vocabulary", () => {
    useStore.getState().apply(ev(1, "agent.state", { state: "executing" }));
    expect(useStore.getState().agentState).toBe("executing");
  });

  it("message.completed replaces accumulated deltas rather than appending", () => {
    const { apply } = useStore.getState();
    apply(ev(1, "turn.started", { text: "x" }));
    apply(ev(2, "message.delta", { text: "par" }));
    apply(ev(3, "message.completed", { text: "partial then final" }));
    expect(useStore.getState().turns[0]?.reply).toBe("partial then final");
  });

  it("keeps a non-completed outcome visible", () => {
    const { apply } = useStore.getState();
    apply(ev(1, "turn.started", { text: "x" }));
    apply(ev(2, "turn.finished", { outcome: "cancelled" }));
    expect(useStore.getState().turns[0]?.outcome).toBe("cancelled");
  });

  it("resync clears local history instead of merging into it", () => {
    const { apply } = useStore.getState();
    apply(ev(1, "turn.started", { text: "old" }));
    apply(ev(50, "session.resync", { baseline: 40 }));
    expect(useStore.getState().turns).toHaveLength(0);
    expect(useStore.getState().gapWarning).toContain("resynced");
  });

  it("ignores unknown event types without throwing", () => {
    expect(() => useStore.getState().apply(ev(1, "some.future.type", { a: 1 }))).not.toThrow();
    expect(useStore.getState().lastSeq).toBe(1);
  });
});

/* ─────────────────────────── Phase 4: tool cards and approvals in the reducer ── */

function evt(
  type: string,
  payload: Record<string, unknown>,
  seq: number,
  turnId: string | null = "t1",
): OracleEvent {
  return {
    v: 1,
    seq,
    ts: "2026-08-21T10:00:00.000Z",
    type,
    session_id: "s1",
    turn_id: turnId,
    trace_id: "tr",
    payload,
  };
}

describe("tool cards", () => {
  beforeEach(() => useStore.getState().reset());

  it("attaches a card to its turn and closes it on finish", () => {
    const s = useStore.getState();
    s.apply(evt("turn.started", { text: "run the tests" }, 1));
    s.apply(evt("tool.started", { tool: "dev.run_tests", args: { path: "C:/x" }, tier: "T1" }, 2));

    let turn = useStore.getState().turns[0]!;
    expect(turn.tools).toHaveLength(1);
    expect(turn.tools[0]!.running).toBe(true);
    expect(turn.tools[0]!.tier).toBe("T1");

    s.apply(
      evt(
        "tool.finished",
        { tool: "dev.run_tests", ok: true, duration_ms: 42, summary: "3 passed", undo_id: null },
        3,
      ),
    );
    turn = useStore.getState().turns[0]!;
    expect(turn.tools[0]!.running).toBe(false);
    expect(turn.tools[0]!.ok).toBe(true);
    expect(turn.tools[0]!.summary).toBe("3 passed");
  });

  it("closes the right card when one tool ran twice in a turn", () => {
    const s = useStore.getState();
    s.apply(evt("turn.started", { text: "twice" }, 1));
    s.apply(evt("tool.started", { tool: "git.status", args: {}, tier: "T0" }, 2));
    s.apply(evt("tool.finished", { tool: "git.status", ok: true, duration_ms: 10, summary: "first" }, 3));
    s.apply(evt("tool.started", { tool: "git.status", args: {}, tier: "T0" }, 4));
    s.apply(evt("tool.finished", { tool: "git.status", ok: false, duration_ms: 20, summary: "second" }, 5));

    const tools = useStore.getState().turns[0]!.tools;
    expect(tools.map((t) => t.summary)).toEqual(["first", "second"]);
    expect(tools.map((t) => t.ok)).toEqual([true, false]);
  });

  it("carries the undo id through, so the card can offer Undo", () => {
    const s = useStore.getState();
    s.apply(evt("turn.started", { text: "commit" }, 1));
    s.apply(evt("tool.started", { tool: "git.commit", args: {}, tier: "T1" }, 2));
    s.apply(evt("tool.finished", { tool: "git.commit", ok: true, duration_ms: 9, undo_id: "u_abc" }, 3));
    expect(useStore.getState().turns[0]!.tools[0]!.undoId).toBe("u_abc");
  });
});

describe("approvals", () => {
  beforeEach(() => useStore.getState().reset());

  /** A live request: the reducer now cares when it was issued, so the stamp is real. */
  const requested = (id = "ap_1", tier = "T2") => ({
    ...evt(
      "approval.requested",
      {
        approval_id: id,
        tool: "git.push",
        tier,
        decision: "confirm",
        rule: "tools.git.push.tier",
        tainted: false,
        escalated: false,
        args: { path: "C:/x" },
        preview: { summary: "publishes 3 commits" },
        expires_in_s: 180,
      },
      10,
    ),
    ts: new Date().toISOString(),
  });

  it("queues a request and removes it when resolved", () => {
    const s = useStore.getState();
    s.apply(requested());
    expect(useStore.getState().approvals).toHaveLength(1);

    s.apply(evt("approval.resolved", { approval_id: "ap_1", resolution: "approved" }, 11));
    expect(useStore.getState().approvals).toHaveLength(0);
    expect(useStore.getState().decided[0]!.resolution).toBe("approved");
  });

  it("ignores a duplicate request for the same id", () => {
    const s = useStore.getState();
    s.apply(requested());
    s.apply(requested());
    expect(useStore.getState().approvals).toHaveLength(1);
  });

  it("drops pending approvals on a resync", () => {
    // Our history is not the server's. An approval we cannot place in a turn is one
    // the user cannot judge, so it goes rather than floating free.
    const s = useStore.getState();
    s.apply(requested());
    s.apply(evt("session.resync", { baseline: 0 }, 12, null));
    expect(useStore.getState().approvals).toHaveLength(0);
    expect(useStore.getState().turns).toHaveLength(0);
  });

  it("ignores an approval that had already expired when it arrived", () => {
    // The reload case: history replays from seq 0, so a request from a backend that has
    // since exited arrives looking new. A dead card at the head of the queue would hide
    // the live one behind it.
    const stale = { ...requested(), ts: new Date(Date.now() - 10 * 60 * 1000).toISOString() };
    useStore.getState().apply(stale);
    expect(useStore.getState().approvals).toHaveLength(0);
  });

  it("keeps an approval that is still live", () => {
    useStore.getState().apply(requested());
    expect(useStore.getState().approvals).toHaveLength(1);
  });

  it("does not resolve optimistically — only the server's event clears a card", () => {
    const s = useStore.getState();
    s.apply(requested());
    // Nothing the UI does locally may empty this list.
    expect(useStore.getState().approvals).toHaveLength(1);
  });
});

describe("terminal", () => {
  beforeEach(() => useStore.getState().reset());

  const opened = (id = "term_1") =>
    evt("term.opened", { pty_id: id, cwd: "C:/Projects", shell: "cmd.exe", banner: "" }, 20, null);

  const output = (id: string, data: string, seq: number, dropped = 0) =>
    evt("term.output", { pty_id: id, stream: "stdout", data, dropped }, seq, null);

  it("attaches on open and collects output in order", () => {
    const s = useStore.getState();
    s.apply(opened());
    expect(useStore.getState().terminal.ptyId).toBe("term_1");

    s.apply(output("term_1", "first", 21));
    s.apply(output("term_1", "second", 22));
    expect(useStore.getState().termChunks.map((c) => c.data)).toEqual(["first", "second"]);
  });

  it("ignores output from a terminal that is not the attached one", () => {
    // Two clients can have different terminals open against one backend. Writing
    // somebody else's output into this xterm would be a genuine confusion of sessions.
    const s = useStore.getState();
    s.apply(opened("term_mine"));
    s.apply(output("term_theirs", "not mine", 21));
    expect(useStore.getState().termChunks).toHaveLength(0);
  });

  it("carries the dropped count so the UI can say scrollback was trimmed", () => {
    const s = useStore.getState();
    s.apply(opened());
    s.apply(output("term_1", "tail of a long build", 21, 4096));
    expect(useStore.getState().termChunks[0]!.dropped).toBe(4096);
  });

  it("clears the buffer when a new terminal opens", () => {
    const s = useStore.getState();
    s.apply(opened("term_1"));
    s.apply(output("term_1", "old", 21));
    s.apply(opened("term_2"));
    expect(useStore.getState().termChunks).toHaveLength(0);
    expect(useStore.getState().terminal.ptyId).toBe("term_2");
  });

  it("detaches only when OUR terminal closes", () => {
    const s = useStore.getState();
    s.apply(opened("term_mine"));
    s.apply(evt("term.closed", { pty_id: "term_theirs" }, 22, null));
    expect(useStore.getState().terminal.ptyId).toBe("term_mine");

    s.apply(evt("term.closed", { pty_id: "term_mine" }, 23, null));
    expect(useStore.getState().terminal.ptyId).toBeNull();
  });

  it("bounds the chunk list — xterm owns the real scrollback", () => {
    const s = useStore.getState();
    s.apply(opened());
    for (let i = 0; i < 260; i++) s.apply(output("term_1", `line ${i}`, 100 + i));
    const chunks = useStore.getState().termChunks;
    expect(chunks.length).toBeLessThanOrEqual(200);
    // The most recent survive: the tail is what has not been rendered yet.
    expect(chunks.at(-1)!.data).toBe("line 259");
  });
});

describe("degradation", () => {
  beforeEach(() => useStore.getState().reset());

  it("records what is unavailable without blocking anything", () => {
    // ADR-0011: a missing model is a normal state. The banner explains; the composer
    // and every deterministic path keep working.
    useStore
      .getState()
      .apply(
        evt(
          "system.degraded",
          { component: "llm", reason: "ollama is not running", remedy: "start ollama" },
          5,
          null,
        ),
      );
    const d = useStore.getState().degraded;
    expect(d?.component).toBe("llm");
    expect(d?.remedy).toBe("start ollama");
  });
});

describe("knowledge.state", () => {
  beforeEach(() => {
    useStore.getState().reset();
  });

  it("shows a run that is happening", () => {
    useStore.getState().apply(ev(1, "knowledge.state", { state: "indexing", pending: 4 }));
    expect(useStore.getState().indexing).toEqual({ state: "indexing", pending: 4, indexed: 0 });
  });

  it("keeps the result of a run that changed something", () => {
    const { apply } = useStore.getState();
    apply(ev(1, "knowledge.state", { state: "indexing", pending: 2 }));
    apply(ev(2, "knowledge.state", { state: "watching", indexed: 2, seen: 2, ms: 900 }));
    expect(useStore.getState().indexing?.indexed).toBe(2);
  });

  it("says nothing while it is merely watching", () => {
    // The resting state is most of the daemon's life. Rendering "watching" forever is
    // a permanent line of UI that carries no information.
    useStore.getState().apply(ev(1, "knowledge.state", { state: "watching", roots: 3 }));
    expect(useStore.getState().indexing).toBeNull();
  });

  it("surfaces an index built by a different model", () => {
    useStore.getState().apply(ev(1, "knowledge.state", { state: "stale", error: "model mismatch" }));
    expect(useStore.getState().indexing?.state).toBe("stale");
  });
});

describe("store.apply — delegations", () => {
  beforeEach(() => {
    useStore.getState().reset();
  });

  function devent(
    seq: number,
    type: string,
    payload: Record<string, unknown> = {},
    taskId = "dlg_1",
  ): OracleEvent {
    return { ...ev(seq, type, payload, "t_1"), task_id: taskId };
  }

  it("folds a full delegation from created through finished", () => {
    const { apply } = useStore.getState();
    apply(devent(1, "task.created", { tool: "ai.delegate", task: "fix auth", adapter: "claude-code" }));
    apply(devent(2, "task.updated", { state: "awaiting_egress" }));
    apply(devent(3, "task.updated", { state: "running" }));
    apply(devent(4, "delegate.event", { kind: "tool_use", tool: "Read", text: "" }));
    apply(devent(5, "task.finished", { outcome: "success", diff_lines: 3 }));

    const d = useStore.getState().delegations[0];
    expect(d?.task).toBe("fix auth");
    expect(d?.state).toBe("finished");
    expect(d?.outcome).toBe("success");
    expect(d?.feed).toHaveLength(1);
    expect(d?.result?.["diff_lines"]).toBe(3);
  });

  it("ignores task events that are not delegations", () => {
    useStore.getState().apply(devent(1, "task.created", { tool: "pipe.run", task: "x" }));
    expect(useStore.getState().delegations).toHaveLength(0);
  });

  it("bounds the feed to the tail — decisions live on task.*, not here", () => {
    const { apply } = useStore.getState();
    apply(devent(1, "task.created", { tool: "ai.delegate", task: "t", adapter: "a" }));
    for (let i = 0; i < 150; i++) {
      apply(devent(2 + i, "delegate.event", { kind: "thinking", text: `line ${i}` }));
    }
    const feed = useStore.getState().delegations[0]?.feed ?? [];
    expect(feed).toHaveLength(100);
    expect(feed.at(-1)?.text).toBe("line 149");
  });

  it("replaying the same task.created twice does not duplicate the card", () => {
    const { apply } = useStore.getState();
    const created = devent(1, "task.created", { tool: "ai.delegate", task: "t", adapter: "a" });
    apply(created);
    apply({ ...created, seq: 2 });
    expect(useStore.getState().delegations).toHaveLength(1);
  });
});

describe("store.apply — task graphs", () => {
  beforeEach(() => {
    useStore.getState().reset();
  });

  function gevent(
    seq: number,
    type: string,
    payload: Record<string, unknown> = {},
    taskId = "look",
  ): OracleEvent {
    return {
      ...ev(seq, type, { source: "graph", root_id: "tk_root", ...payload }, "t_1"),
      task_id: taskId,
    };
  }

  it("folds a graph from created through finished", () => {
    const { apply } = useStore.getState();
    apply(gevent(1, "task.created", { kind: "tool" }));
    apply(gevent(2, "task.created", { kind: "delegation" }, "fix"));
    apply(gevent(3, "task.updated", { status: "running" }));
    apply(
      gevent(4, "task.finished", {
        status: "succeeded",
        summary: "fs.read ok",
        evidence: { rule: "fs.read" },
        claim: "I read it",
      }),
    );

    const graph = useStore.getState().graphs[0];
    expect(graph?.rootId).toBe("tk_root");
    expect(graph?.tasks).toHaveLength(2);
    const look = graph?.tasks.find((t) => t.taskId === "look");
    expect(look?.status).toBe("succeeded");
    expect(look?.evidence).toEqual({ rule: "fs.read" });
    // Kept apart at the last possible moment, as everywhere else.
    expect(look?.claim).toBe("I read it");
  });

  it("keeps a replacement's lineage from the event that created it", () => {
    // The scheduler stamps `supersedes` on the first event about a replanned row, so a
    // client never has to re-query the tree and diff it to find out what replaced what.
    const { apply } = useStore.getState();
    apply(gevent(1, "task.created", { kind: "delegation" }, "fix"));
    apply(gevent(2, "task.finished", { status: "failed", summary: "wrong file" }, "fix"));
    apply(
      gevent(3, "task.created", { kind: "delegation", supersedes: "fix" }, "fix-r1"),
    );

    const graph = useStore.getState().graphs[0];
    expect(graph?.tasks.map((t) => t.taskId)).toEqual(["fix", "fix-r1"]);
    // The failed row is still there, still failed. Nothing is rewritten.
    expect(graph?.tasks.find((t) => t.taskId === "fix")?.status).toBe("failed");
    expect(graph?.tasks.find((t) => t.taskId === "fix-r1")?.supersedes).toBe("fix");
    expect(graph?.tasks.find((t) => t.taskId === "fix")?.supersedes).toBeUndefined();
  });

  it("does not fold a delegation's own lifecycle into a graph", () => {
    // The same event types, the same task id, a different meaning: `source` is the only
    // honest discriminator, and guessing from payload keys is what it exists to prevent.
    const { apply } = useStore.getState();
    apply({
      ...ev(1, "task.created", { tool: "ai.delegate", task: "t", adapter: "a" }, "t_1"),
      task_id: "dlg_1",
    });
    expect(useStore.getState().graphs).toHaveLength(0);
    expect(useStore.getState().delegations).toHaveLength(1);
  });

  it("ignores an update for a task it never saw created", () => {
    // A half-known graph rendered as if it were whole is worse than a visible gap.
    useStore.getState().apply(gevent(1, "task.updated", { status: "running" }, "ghost"));
    expect(useStore.getState().graphs).toHaveLength(0);
  });

  it("replaying task.created does not duplicate a task", () => {
    const { apply } = useStore.getState();
    const created = gevent(1, "task.created", { kind: "tool" });
    apply(created);
    apply({ ...created, seq: 2 });
    expect(useStore.getState().graphs[0]?.tasks).toHaveLength(1);
  });
});

/** Apply a list of raw wire events and hand back the resulting state. */
function play(events: Array<Record<string, unknown>>) {
  useStore.getState().reset();
  const { apply } = useStore.getState();
  for (const e of events) {
    apply({ v: 1, session_id: "s_1", turn_id: null, trace_id: "tr_1", ...e } as OracleEvent);
  }
  return useStore.getState();
}

describe("the graph slice folds what the scheduler actually sends", () => {
  /**
   * These payloads are the ones `orchestration/scheduler.py::_emit` builds, field for field.
   *
   * The reason this suite exists is a specific failure: `TaskTree.test.tsx` asserted that a task's
   * dependencies render, using a fixture that hand-populated `dependsOn` — while the scheduler
   * never sent `depends_on` and `store.ts` set it to `[]` unconditionally. A green test over a
   * shape the app could not produce. Fixtures for wire-folding belong close to the wire.
   */
  const created = (over: Record<string, unknown> = {}) => ({
    seq: 1,
    type: "task.created",
    ts: "2026-08-26T12:00:00Z",
    task_id: "tk_root-b",
    payload: {
      root_id: "tk_root",
      source: "graph",
      kind: "tool",
      root: "tk_root",
      depends_on: ["tk_root-a"],
      objective: "dev.run_tests (oracle-selfcheck/tests)",
      role: "operator",
      agent: null,
      project: "ORACLE",
      attempt: 1,
      max_attempts: 2,
      supersedes: null,
      ...over,
    },
  });

  it("keeps the dependencies, so the client has a graph and not a list", () => {
    const s = play([created()]);
    expect(s.graphs[0]?.tasks[0]?.dependsOn).toEqual(["tk_root-a"]);
  });

  it("carries the objective verbatim rather than a summary of it", () => {
    const s = play([created()]);
    expect(s.graphs[0]?.tasks[0]?.objective).toBe("dev.run_tests (oracle-selfcheck/tests)");
  });

  it("leaves agent undefined when there is none, rather than inventing a dash", () => {
    // A TOOL task genuinely has no agent. `""` would render as an empty label; undefined
    // lets the view decide, and the view's decision is to draw nothing.
    const s = play([created()]);
    expect(s.graphs[0]?.tasks[0]?.agent).toBeUndefined();
    expect(s.graphs[0]?.tasks[0]?.role).toBe("operator");
  });

  it("records cost from task.finished, and undefined is not zero", () => {
    const withCost = {
      seq: 2,
      type: "task.finished",
      ts: "2026-08-26T12:01:00Z",
      task_id: "tk_root-b",
      payload: {
        root_id: "tk_root",
        source: "graph",
        status: "succeeded",
        ok: true,
        summary: "ok",
        evidence: { tool: "dev.run_tests" },
        claim: null,
        cost: { tokens: 1200, usd: 0.04 },
        attempt: 1,
        started_at: "2026-08-26T12:00:01Z",
        finished_at: "2026-08-26T12:00:59Z",
      },
    };
    const s = play([created(), withCost]);
    const task = s.graphs[0]?.tasks[0];
    expect(task?.cost?.tokens).toBe(1200);
    expect(task?.startedAt).toBe("2026-08-26T12:00:01Z");

    const free = play([created(), { ...withCost, payload: { ...withCost.payload, cost: null } }]);
    expect(free.graphs[0]?.tasks[0]?.cost).toBeUndefined();
  });

  it("still keeps evidence and the worker's claim apart", () => {
    const s = play([
      created(),
      {
        seq: 2,
        type: "task.finished",
        ts: "2026-08-26T12:01:00Z",
        task_id: "tk_root-b",
        payload: {
          root_id: "tk_root",
          source: "graph",
          status: "failed",
          ok: false,
          summary: "tests 40/41",
          evidence: { passed: 40, failed: 1 },
          claim: "everything passes",
        },
      },
    ]);
    const task = s.graphs[0]?.tasks[0];
    expect(task?.evidence).toEqual({ passed: 40, failed: 1 });
    expect(task?.claim).toBe("everything passes");
  });
});
