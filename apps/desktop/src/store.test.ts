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
