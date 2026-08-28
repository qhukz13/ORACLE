/**
 * The centre stage's switcher — docs/UI.md §2 ("Center stage is switchable, not fixed")
 * and §20's `ViewTabs`, replacing three bespoke header toggles that each flipped back to
 * chat by its own rule (one of them didn't — the Events button toggled on `stage ===
 * "chat"`, so pressing it from Memory went to Events while the other buttons went home).
 * A registry cannot have per-button rules, which is the point of it.
 *
 * The stage ids are the views that exist, not the four names UI.md §16 was written with
 * (`Orbit / Chat / Timeline / Tasks`): Orbit is P11-T2 and gated on OQ-14, and the
 * timeline slot is currently the flat event table, labelled as what it is. §16 is
 * corrected in place alongside this file — a keybinding doc that names two views the app
 * cannot show is worse than one that names the real ones.
 *
 * This is a real tablist, not buttons wearing the class: `role="tab"`, `aria-selected`,
 * and arrow-key roving with automatic activation (ARIA APG "Tabs"). The house rule from
 * TaskTree applies — claim the role only with the behaviour attached.
 */

import { useRef } from "react";

export type Stage = "chat" | "tasks" | "events" | "memory" | "briefing" | "knowledge";

export interface StageDef {
  id: Stage;
  label: string;
  title: string;
  /** The digit after Ctrl, for the four primary views (docs/UI.md §16). */
  hotkey?: string;
}

/** Order is layout order AND hotkey order: Ctrl+1..4 are the first four. */
export const STAGES: readonly StageDef[] = [
  { id: "chat", label: "Chat", title: "the conversation", hotkey: "1" },
  { id: "tasks", label: "Tasks", title: "task graphs, workers and their evidence", hotkey: "2" },
  { id: "events", label: "Events", title: "the raw event stream", hotkey: "3" },
  { id: "memory", label: "Memory", title: "what ORACLE has recorded, and why", hotkey: "4" },
  { id: "briefing", label: "Briefing", title: "what happened while you were away" },
  { id: "knowledge", label: "Knowledge", title: "index health" },
];

export interface ViewTabsProps {
  stage: Stage;
  onSwitch(stage: Stage): void;
  /** Stages allowed to demand attention (today: the briefing, until acknowledged). */
  attn?: Partial<Record<Stage, boolean>>;
}

export function ViewTabs({ stage, onSwitch, attn }: ViewTabsProps) {
  const refs = useRef<Partial<Record<Stage, HTMLButtonElement | null>>>({});

  // Roving tabindex with automatic activation: arrows both move focus and switch, so a
  // keyboard user is never focused on a tab the panel is not showing.
  const onKeyDown = (e: React.KeyboardEvent) => {
    const at = STAGES.findIndex((d) => d.id === stage);
    let to = -1;
    if (e.key === "ArrowRight") to = (at + 1) % STAGES.length;
    else if (e.key === "ArrowLeft") to = (at - 1 + STAGES.length) % STAGES.length;
    else if (e.key === "Home") to = 0;
    else if (e.key === "End") to = STAGES.length - 1;
    if (to === -1) return;
    e.preventDefault();
    const def = STAGES[to];
    if (!def) return;
    onSwitch(def.id);
    refs.current[def.id]?.focus();
  };

  return (
    <div className="tabs" role="tablist" aria-label="Centre stage" onKeyDown={onKeyDown}>
      {STAGES.map((def) => (
        <button
          key={def.id}
          ref={(el) => {
            refs.current[def.id] = el;
          }}
          role="tab"
          id={`tab-${def.id}`}
          aria-selected={def.id === stage}
          aria-controls="stage-panel"
          tabIndex={def.id === stage ? 0 : -1}
          className={`tab${attn?.[def.id] ? " attn" : ""}`}
          title={def.hotkey ? `${def.title} — Ctrl+${def.hotkey}` : def.title}
          onClick={() => onSwitch(def.id)}
        >
          {def.label}
          {/* The dot is aria-hidden because the information is in the title and the
              briefing's own content; a screen reader hearing "Briefing bullet" learns
              nothing (docs/UI.md §1: icon + label, never colour alone — the label here
              is the tab's own text plus the badge's title). */}
          {attn?.[def.id] && <span className="tab-dot" aria-hidden="true" />}
        </button>
      ))}
    </div>
  );
}
