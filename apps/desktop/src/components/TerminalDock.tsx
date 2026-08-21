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
import { useCallback, useEffect, useRef, useState } from "react";

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

  /**
   * `fit()` throws if the host has no layout yet, or if the renderer has been torn
   * down: it reads `dimensions` off a renderer that is `undefined`, and the error
   * surfaces as an unhandled TypeError from deep inside xterm rather than as anything
   * that names the cause. Seen in practice on HMR and when the dock is toggled.
   *
   * A zero-size host is a normal transient state — the dock mounts before layout, and
   * a hidden pane never lays out at all — so this is a guard, not a swallowed bug.
   */
  const safeFit = useCallback(() => {
    const host = hostRef.current;
    const fit = fitRef.current;
    if (!host || !fit || host.clientWidth === 0 || host.clientHeight === 0) return false;
    try {
      fit.fit();
      return true;
    } catch {
      return false;
    }
  }, []);

  /**
   * Attach only once the host is actually laid out.
   *
   * xterm measures a character by rendering one. Where it cannot — a zero-size host,
   * which happens because the dock mounts before layout — the renderer's `dimensions`
   * stays undefined and xterm throws from inside its own animation frame:
   * `Cannot read properties of undefined (reading 'dimensions')`. That is *uncatchable*
   * from here, being raised asynchronously rather than from the call, so it has to be
   * prevented instead. A `ResizeObserver` waits for a real size and attaches then.
   *
   * **Measured limitation, stated plainly:** this guard does NOT fix the same error in
   * a browser pane that never composites (a headless preview). There the host has a
   * perfectly good 1280×217 layout and the character still cannot be measured, because
   * nothing paints. The backend pipeline and the terminal *buffer* were verified in
   * that environment; the rendering half was not, and could not be.
   *
   * Nothing is lost by waiting — output stays in the store until a terminal exists to
   * receive it.
   */
  useEffect(() => {
    const host = hostRef.current;
    if (!host || termRef.current) return;

    let disposed = false;
    let cleanup: (() => void) | undefined;

    const attach = () => {
      if (disposed || termRef.current) return;
      if (host.clientWidth === 0 || host.clientHeight === 0) return;

      const term = new Terminal({
        fontFamily: '"Cascadia Code", "JetBrains Mono", Consolas, monospace',
        fontSize: 12,
        theme: THEME,
        cursorBlink: true,
        convertEol: false,
        scrollback: 5000,
        cursorStyle: "block",
      });
      const fit = new FitAddon();
      term.loadAddon(fit);
      term.open(host);
      termRef.current = term;
      fitRef.current = fit;
      safeFit();
      // Anything that arrived while we were waiting for layout.
      writtenRef.current = 0;

      const sub = term.onData((data) => onInput(data));
      cleanup = () => {
        sub.dispose();
        term.dispose();
        termRef.current = null;
        fitRef.current = null;
      };
    };

    attach();
    const observer = new ResizeObserver(attach);
    observer.observe(host);

    return () => {
      disposed = true;
      observer.disconnect();
      cleanup?.();
    };
    // Mount once. `onInput` is captured by the subscription, so the parent passes a
    // stable callback.
  }, []);

  // Resize with the pane, and tell the PTY — otherwise the shell wraps at the wrong
  // column and a build log becomes unreadable.
  useEffect(() => {
    const onWindowResize = () => {
      if (!safeFit()) return;
      const term = termRef.current;
      if (term && ptyId) onResize(term.cols, term.rows);
    };
    window.addEventListener("resize", onWindowResize);
    onWindowResize();
    return () => window.removeEventListener("resize", onWindowResize);
  }, [ptyId, onResize, safeFit]);

  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    for (let i = writtenRef.current; i < chunks.length; i++) {
      const chunk = chunks[i];
      if (!chunk) continue;
      try {
        term.write(chunk.data);
      } catch {
        // A disposed terminal mid-flush. Losing a chunk here is a cosmetic gap in a
        // log, not a correctness problem — the event stream still holds it.
        break;
      }
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
