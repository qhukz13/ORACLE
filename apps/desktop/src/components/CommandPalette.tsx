/**
 * Command palette (`Ctrl+K`) — docs/UI.md#10.
 *
 * The UI half of the pre-router (ADR-0011). Every action routed here costs **zero
 * model latency**, which is precisely why it ships in the MVP rather than as polish:
 * it is the fastest path to anything, and on a 4 GB GPU "fastest" is not a nicety.
 *
 * Two rules, both from the spec:
 *
 * - **The palette never dead-ends.** The last entry is always "ask the agent", so an
 *   unmatched query still goes somewhere. A palette that shrugs teaches you not to
 *   open it.
 * - **Ranking is by prefix then substring, deterministic.** No fuzzy scoring. The same
 *   query gives the same order every time, which is what lets muscle memory form —
 *   the same argument as the deterministic orbit layout.
 */

import { useEffect, useMemo, useRef, useState } from "react";

export interface PaletteItem {
  id: string;
  label: string;
  hint: string;
  kind: "command" | "project" | "chat" | "delegate";
  /** What actually gets sent. For commands this is the slash form the pre-router eats. */
  send: string;
}

export interface CommandPaletteProps {
  open: boolean;
  projects: string[];
  onClose(): void;
  onSubmit(text: string): void;
}

/** Mirrors `oracle.router.prerouter.COMMANDS`. Kept short on purpose. */
const COMMANDS: ReadonlyArray<{ name: string; summary: string }> = [
  { name: "help", summary: "List available commands" },
  { name: "status", summary: "Agent state, model, event sequence" },
  { name: "halt", summary: "Emergency stop — cancel everything" },
  { name: "sessions", summary: "List recent sessions" },
  { name: "clear", summary: "Clear the current conversation view" },
  { name: "events", summary: "Show the raw event stream" },
];

export function buildItems(query: string, projects: string[]): PaletteItem[] {
  const q = query.trim();
  const lower = q.toLowerCase();
  const items: PaletteItem[] = [];

  const wantsCommand = lower.startsWith(">") || lower.startsWith("/");
  const wantsProject = lower.startsWith("@");
  const needle = lower.replace(/^[>@/#?]/, "").trim();

  const rank = (name: string): number =>
    !needle ? 1 : name.startsWith(needle) ? 0 : name.includes(needle) ? 1 : -1;

  if (!wantsProject) {
    for (const c of COMMANDS) {
      const r = rank(c.name);
      if (r < 0) continue;
      items.push({
        id: `cmd:${c.name}`,
        label: `/${c.name}`,
        hint: c.summary,
        kind: "command",
        send: `/${c.name}`,
      });
    }
  }

  if (!wantsCommand) {
    for (const p of projects) {
      const r = rank(p.toLowerCase());
      if (r < 0) continue;
      items.push({
        id: `proj:${p}`,
        label: p,
        hint: "ask about this project",
        kind: "project",
        send: `what is the status of ${p}`,
      });
    }
  }

  // Delegation, offered per project once the user has typed something to delegate.
  // The wording says where it goes, because the palette entry is the first half of a
  // decision whose second half is the egress preview (docs/INTEGRATIONS.md#6).
  if (!wantsCommand && !wantsProject && needle.length > 3) {
    for (const p of projects) {
      if (!lower.includes(p.toLowerCase())) continue;
      items.push({
        id: `delegate:${p}`,
        label: `Delegate to a coding agent: “${q}”`,
        hint: `${p} · you approve what is sent`,
        kind: "delegate",
        send: `ask Claude to ${q}`,
      });
    }
  }

  // Always last, always present: the palette must not dead-end.
  if (q) {
    items.push({
      id: "chat",
      label: `Ask ORACLE: “${q}”`,
      hint: "chat",
      kind: "chat",
      send: q,
    });
  }
  return items;
}

export function CommandPalette({ open, projects, onClose, onSubmit }: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const items = useMemo(() => buildItems(query, projects), [query, projects]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setIndex(0);
      inputRef.current?.focus();
    }
  }, [open]);

  useEffect(() => {
    setIndex((i) => Math.min(i, Math.max(0, items.length - 1)));
  }, [items.length]);

  if (!open) return null;

  const choose = (item: PaletteItem | undefined) => {
    if (!item) return;
    onSubmit(item.send);
    onClose();
  };

  return (
    <div className="palette-backdrop" onClick={onClose} role="presentation">
      <div
        className="palette"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Command palette"
      >
        <input
          ref={inputRef}
          value={query}
          placeholder="run a command, name a project, or just ask…"
          aria-label="Command palette query"
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              onClose();
            } else if (e.key === "ArrowDown") {
              e.preventDefault();
              setIndex((i) => (i + 1) % Math.max(1, items.length));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setIndex((i) => (i - 1 + items.length) % Math.max(1, items.length));
            } else if (e.key === "Enter") {
              e.preventDefault();
              choose(items[index]);
            }
          }}
        />
        <ul className="palette-list" role="listbox" aria-label="Results">
          {items.length === 0 && <li className="muted palette-empty">Type to search</li>}
          {items.map((item, i) => (
            <li
              key={item.id}
              role="option"
              aria-selected={i === index}
              className={i === index ? "sel" : ""}
              onMouseEnter={() => setIndex(i)}
              onClick={() => choose(item)}
            >
              <span className="pi-label">{item.label}</span>
              <span className="pi-hint">{item.hint}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
