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
