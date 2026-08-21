/**
 * WebSocket client with resume.
 *
 * The contract that matters: on reconnect we pass `since_seq` = the highest seq we
 * have actually applied, so the server replays exactly the gap
 * (docs/API.md#connect-and-resume). Losing track of that number is how a client ends
 * up with a hole in its history and never notices.
 */

import type { ClientCommand, OracleEvent } from "./protocol";

export interface ClientHandlers {
  onEvent(ev: OracleEvent): void;
  onStateChange(state: "connecting" | "online" | "offline", retryInSec: number): void;
  onGap(expected: number, got: number): void;
}

const BASE_DELAY_MS = 500;
const MAX_DELAY_MS = 15_000;

export class OracleClient {
  private ws: WebSocket | null = null;
  private lastSeq = 0;
  private attempt = 0;
  private timer: number | null = null;
  private countdown: number | null = null;
  private closed = false;

  constructor(private readonly handlers: ClientHandlers) {}

  /** Highest applied seq. Survives reconnects; resets only on an explicit resync. */
  get seq(): number {
    return this.lastSeq;
  }

  connect(): void {
    this.closed = false;
    this.clearTimers();
    this.handlers.onStateChange("connecting", 0);

    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/api/v1/stream?since_seq=${this.lastSeq}`;
    const ws = new WebSocket(url);
    this.ws = ws;

    ws.onopen = () => {
      this.attempt = 0;
      this.handlers.onStateChange("online", 0);
    };

    ws.onmessage = (msg) => {
      let ev: OracleEvent;
      try {
        ev = JSON.parse(msg.data as string) as OracleEvent;
      } catch {
        return; // unparseable frame: ignore, never crash the stream
      }

      if (ev.type === "session.resync") {
        const baseline = Number(ev.payload?.["baseline"] ?? 0);
        this.lastSeq = baseline;
        this.handlers.onEvent(ev);
        return;
      }

      // A gap means we missed something. Surface it rather than rendering a
      // silently incomplete history.
      if (this.lastSeq > 0 && ev.seq > this.lastSeq + 1) {
        this.handlers.onGap(this.lastSeq + 1, ev.seq);
      }
      if (ev.seq <= this.lastSeq) return; // duplicate; resume overlap

      this.lastSeq = ev.seq;
      this.handlers.onEvent(ev);
    };

    ws.onclose = () => {
      this.ws = null;
      if (!this.closed) this.scheduleReconnect();
    };
    ws.onerror = () => ws.close();
  }

  send(cmd: ClientCommand): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify({ v: 1, ...cmd }));
    return true;
  }

  close(): void {
    this.closed = true;
    this.clearTimers();
    this.ws?.close();
    this.ws = null;
  }

  private scheduleReconnect(): void {
    const delay = Math.min(BASE_DELAY_MS * 2 ** this.attempt++, MAX_DELAY_MS);
    let remaining = Math.ceil(delay / 1000);
    this.handlers.onStateChange("offline", remaining);

    // Visible countdown: "reconnecting in 4s" beats an opaque spinner.
    this.countdown = window.setInterval(() => {
      remaining -= 1;
      if (remaining >= 0) this.handlers.onStateChange("offline", remaining);
    }, 1000);

    this.timer = window.setTimeout(() => this.connect(), delay);
  }

  private clearTimers(): void {
    if (this.timer !== null) window.clearTimeout(this.timer);
    if (this.countdown !== null) window.clearInterval(this.countdown);
    this.timer = null;
    this.countdown = null;
  }
}
