/**
 * The terminal dock — docs/UI.md#5.
 *
 * "The terminal is first-class. Not a hidden debug panel. It's where trust is built:
 * I can see the actual commands." That is the whole reason this ships in the MVP.
 *
 * Two things it must get right:
 *
 * - **Who typed it.** The human's keystrokes go out as `term.input`; the agent's go
 *   through `term.write`, which is confirmed every time. They are different tools on
 *   the backend for exactly this reason, and the distinction is a trust feature rather
 *   than decoration.
 * - **Say when output was lost.** The backend reports `dropped` when scrollback was
 *   trimmed. Rendering the remaining text without saying so would repeat the bug the
 *   backend just had — a reader concluding that something near the start never
 *   appeared, when it did.
 *
 * xterm.js owns the buffer, the escape-sequence parsing and the rendering. Writing a
 * VT emulator by hand to save 250 KB would be a poor trade for something that has to
 * render a real `npm install` correctly.
 */

import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { useEffect, useRef, useState } from "react";

export interface TerminalDockProps {
  /** Open PTY id, or null when nothing is attached yet. */
  ptyId: string | null;
  cwd: string;
  /** Appended in order; the parent hands over each chunk exactly once. */
  chunks: ReadonlyArray<{ seq: number; data: string; dropped: number }>;
  onInput(data: string): void;
  onResize(cols: number, rows: number): void;
  onOpen(): void;
  onClose(): void;
}

/** Matches the app's own tokens so the terminal is part of the interface, not a guest. */
const THEME = {
  background: "#0b0e14",
  foreground: "#d8dee9",
  cursor: "#22d3ee",
  selectionBackground: "#1e2432",
  black: "#11151f",
  red: "#ef4444",
  green: "#22c55e",
  yellow: "#f59e0b",
  blue: "#3b82f6",
  magenta: "#a78bfa",
  cyan: "#22d3ee",
  white: "#d8dee9",
};

export function TerminalDock({
  ptyId,
  cwd,
  chunks,
  onInput,
  onResize,
  onOpen,
  onClose,
}: TerminalDockProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  // How far through `chunks` we have written. The parent keeps a bounded list and only
  // ever appends, so a high-water mark is enough and nothing is written twice.
  const writtenRef = useRef(0);
  const [dropped, setDropped] = useState(0);

  useEffect(() => {
    if (!hostRef.current || termRef.current) return;
    const term = new Terminal({
      fontFamily: '"Cascadia Code", "JetBrains Mono", Consolas, monospace',
      fontSize: 12,
      theme: THEME,
      cursorBlink: true,
      convertEol: false,
      scrollback: 5000,
      // The window's own reduced-motion preference, honoured here too.
      cursorStyle: "block",
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(hostRef.current);
    fit.fit();
    termRef.current = term;
    fitRef.current = fit;

    const sub = term.onData((data) => onInput(data));
    return () => {
      sub.dispose();
      term.dispose();
      termRef.current = null;
    };
    // Mount once. `onInput` is read through the closure that the subscription captured,
    // so the parent passes a stable callback.
  }, []);

  // Resize with the pane, and tell the PTY — otherwise the shell wraps at the wrong
  // column and a build log becomes unreadable.
  useEffect(() => {
    const onWindowResize = () => {
      fitRef.current?.fit();
      const term = termRef.current;
      if (term && ptyId) onResize(term.cols, term.rows);
    };
    window.addEventListener("resize", onWindowResize);
    onWindowResize();
    return () => window.removeEventListener("resize", onWindowResize);
  }, [ptyId, onResize]);

  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    for (let i = writtenRef.current; i < chunks.length; i++) {
      const chunk = chunks[i];
      if (!chunk) continue;
      term.write(chunk.data);
      if (chunk.dropped > dropped) setDropped(chunk.dropped);
    }
    writtenRef.current = chunks.length;
  }, [chunks, dropped]);

  return (
    <section className="dock" aria-label="Terminal">
      <div className="dock-tabs">
        <span className="dock-tab sel">TERMINAL</span>
        {ptyId ? (
          <span className="dock-cwd" title={cwd}>
            {cwd}
          </span>
        ) : (
          <span className="muted">no session</span>
        )}
        {dropped > 0 && (
          <span className="dock-dropped" role="status">
            ⚠ {dropped.toLocaleString()} characters of scrollback were trimmed
          </span>
        )}
        <span className="spacer" />
        {ptyId ? (
          <button className="ghost" onClick={onClose}>
            Close
          </button>
        ) : (
          <button className="ghost" onClick={onOpen}>
            Open shell
          </button>
        )}
      </div>
      <div className="dock-term" ref={hostRef} />
    </section>
  );
}
