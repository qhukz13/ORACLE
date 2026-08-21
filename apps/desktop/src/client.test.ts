/**
 * The client half of the resume contract. The server tests prove it replays correctly;
 * these prove the client tracks `since_seq` honestly and never renders a silent hole.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OracleClient } from "./client";
import type { OracleEvent } from "./protocol";

class FakeSocket {
  static last: FakeSocket | null = null;
  static readonly OPEN = 1;
  readyState = 1;
  url: string;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeSocket.last = this;
  }
  send(d: string) {
    this.sent.push(d);
  }
  close() {
    this.readyState = 3;
    this.onclose?.();
  }
  deliver(ev: Partial<OracleEvent> & { seq: number; type: string }) {
    this.onmessage?.({
      data: JSON.stringify({
        v: 1,
        ts: "",
        session_id: "s_1",
        turn_id: null,
        trace_id: "tr_1",
        payload: {},
        ...ev,
      }),
    });
  }
}

let events: OracleEvent[];
let gaps: Array<[number, number]>;
let states: string[];
let client: OracleClient;

beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal("location", { protocol: "http:", host: "localhost:5273" });
  events = [];
  gaps = [];
  states = [];
  client = new OracleClient({
    onEvent: (e) => events.push(e),
    onStateChange: (s) => states.push(s),
    onGap: (a, b) => gaps.push([a, b]),
  });
  client.connect();
  FakeSocket.last!.onopen?.();
});

afterEach(() => {
  client.close();
  vi.unstubAllGlobals();
});

describe("OracleClient", () => {
  it("requests since_seq=0 on a cold start", () => {
    expect(FakeSocket.last!.url).toContain("since_seq=0");
  });

  it("advances seq as events arrive", () => {
    FakeSocket.last!.deliver({ seq: 1, type: "turn.started" });
    FakeSocket.last!.deliver({ seq: 2, type: "turn.finished" });
    expect(client.seq).toBe(2);
    expect(events).toHaveLength(2);
  });

  it("drops duplicates from resume overlap", () => {
    FakeSocket.last!.deliver({ seq: 1, type: "a" });
    FakeSocket.last!.deliver({ seq: 2, type: "b" });
    FakeSocket.last!.deliver({ seq: 2, type: "b" }); // replayed again
    FakeSocket.last!.deliver({ seq: 1, type: "a" });
    expect(events.map((e) => e.seq)).toEqual([1, 2]);
  });

  it("reports a gap instead of rendering an incomplete history", () => {
    FakeSocket.last!.deliver({ seq: 1, type: "a" });
    FakeSocket.last!.deliver({ seq: 5, type: "b" });
    expect(gaps).toEqual([[2, 5]]);
  });

  it("resync rebases seq to the server baseline", () => {
    FakeSocket.last!.deliver({ seq: 1, type: "a" });
    FakeSocket.last!.deliver({ seq: 900, type: "session.resync", payload: { baseline: 800 } });
    expect(client.seq).toBe(800);
    expect(gaps).toHaveLength(0); // a resync is not a gap
  });

  it("survives an unparseable frame", () => {
    expect(() => FakeSocket.last!.onmessage?.({ data: "{{{not json" })).not.toThrow();
    FakeSocket.last!.deliver({ seq: 1, type: "a" });
    expect(events).toHaveLength(1);
  });

  it("goes offline on close and reports the retry countdown", () => {
    FakeSocket.last!.close();
    expect(states).toContain("offline");
  });

  it("refuses to send while disconnected", () => {
    FakeSocket.last!.readyState = 3;
    expect(client.send({ type: "session.message" })).toBe(false);
  });
});
