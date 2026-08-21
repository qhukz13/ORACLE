/**
 * P0 shell. Deliberately plain: this is the walking skeleton, not the UI.
 * The real interface (sidebar, dock, inspector, palette) is Phase 4 — docs/UI.md.
 *
 * Status tokens follow docs/UI.md#14-colour-and-status-semantics so the vocabulary is
 * established from the start, and status is never carried by colour alone.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { OracleClient } from "./client";
import { useStore } from "./store";

const STATE_LABEL: Record<string, string> = {
  idle: "IDLE",
  understanding: "THINKING",
  retrieving: "SEARCHING",
  planning: "PLANNING",
  awaiting_approval: "NEEDS YOU",
  executing: "RUNNING",
  delegating: "DELEGATED",
  summarizing: "WRAPPING UP",
  error: "ERROR",
  halted: "HALTED",
};

export default function App() {
  const s = useStore();
  const [draft, setDraft] = useState("");
  const [showEvents, setShowEvents] = useState(false);
  const clientRef = useRef<OracleClient | null>(null);
  const logEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const client = new OracleClient({
      onEvent: (ev) => useStore.getState().apply(ev),
      onStateChange: (state, retry) => useStore.getState().setConnection(state, retry),
      onGap: (expected, got) => useStore.getState().setGap(expected, got),
    });
    clientRef.current = client;
    client.connect();
    return () => client.close();
  }, []);

  useEffect(() => {
    logEnd.current?.scrollIntoView({ behavior: "smooth" });
  }, [s.turns, s.events, showEvents]);

  const send = () => {
    const text = draft.trim();
    if (!text) return;
    const ok = clientRef.current?.send({
      type: "session.message",
      payload: { text, session_id: s.sessionId },
    });
    if (ok) setDraft("");
  };

  const banner = useMemo(() => {
    if (s.connection === "offline")
      return { cls: "err", text: `Backend offline — reconnecting in ${s.retryInSec}s` };
    if (s.connection === "connecting") return { cls: "warn", text: "Connecting…" };
    return null;
  }, [s.connection, s.retryInSec]);

  return (
    <div className="app">
      <header className="bar">
        <span className="brand">ORACLE</span>
        <span className={`state st-${s.agentState}`}>
          <i className="dot" aria-hidden="true" />
          {STATE_LABEL[s.agentState] ?? s.agentState.toUpperCase()}
        </span>
        <span className="spacer" />
        <span className="meta">seq {s.lastSeq}</span>
        <button className="ghost" onClick={() => setShowEvents((v) => !v)}>
          {showEvents ? "Chat" : "Events"}
        </button>
      </header>

      {banner && (
        <div className={`banner ${banner.cls}`} role="status">
          {banner.text}
        </div>
      )}
      {s.gapWarning && (
        <div className="banner warn" role="alert">
          ⚠ {s.gapWarning}
        </div>
      )}

      <main className="stage">
        {showEvents ? (
          <table className="events">
            <thead>
              <tr>
                <th>seq</th>
                <th>type</th>
                <th>trace</th>
                <th>payload</th>
              </tr>
            </thead>
            <tbody>
              {s.events.map((e) => (
                <tr key={e.seq}>
                  <td className="num">{e.seq}</td>
                  <td className="type">{e.type}</td>
                  <td className="muted">{e.trace_id}</td>
                  <td className="muted">{JSON.stringify(e.payload).slice(0, 90)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : s.turns.length === 0 ? (
          <div className="empty">
            <p>No conversation yet.</p>
            <p className="muted">
              P0 runs an <b>echo</b> agent — no model is loaded. Phase 1 replaces it.
            </p>
          </div>
        ) : (
          <ul className="turns">
            {s.turns.map((t) => (
              <li key={t.turnId}>
                <div className="msg user">{t.userText}</div>
                <div className="msg agent">
                  {t.reply || <span className="muted">…</span>}
                  {!t.done && <span className="caret" aria-hidden="true" />}
                  {t.outcome && t.outcome !== "completed" && (
                    <span className="outcome"> [{t.outcome}]</span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
        <div ref={logEnd} />
      </main>

      <footer className="composer">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder={s.connection === "online" ? "Message ORACLE…" : "Waiting for backend…"}
          disabled={s.connection !== "online"}
          aria-label="Message"
        />
        <button onClick={send} disabled={s.connection !== "online" || !draft.trim()}>
          Send
        </button>
      </footer>
    </div>
  );
}
