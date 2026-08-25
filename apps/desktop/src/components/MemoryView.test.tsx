/**
 * MEMORY.md §6's two requirements, as tests: "why does ORACLE think that?" in one click,
 * and "make it stop thinking that" in two.
 *
 * The assertion worth arguing about is the third one. A superseded belief is still
 * rendered, under the fact that replaced it — because a store that keeps the row while
 * the UI hides it is keeping the row for nobody, and "why did it used to think that?" is
 * a question people actually ask.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MemoryView, toFacts } from "./MemoryView";
import type { MemoryFact } from "./MemoryView";

const payload = {
  facts: [
    {
      id: "fct_new",
      kind: "fact",
      scope: "project",
      scope_ref: "Asterim",
      key: "test_command",
      value: "pnpm test",
      source: "user_corrected",
      confidence: 1,
      effective_confidence: 1,
      stale: false,
      evidence: ["ev_41"],
      origin: "turn_9",
      created_at: new Date().toISOString(),
      last_confirmed_at: new Date().toISOString(),
      hit_count: 7,
      superseded_by: null,
    },
    {
      id: "fct_old",
      kind: "fact",
      scope: "project",
      scope_ref: "Asterim",
      key: "test_command",
      value: "npm test",
      source: "observed",
      confidence: 1,
      effective_confidence: 1,
      stale: false,
      evidence: [],
      origin: "",
      created_at: new Date().toISOString(),
      last_confirmed_at: new Date().toISOString(),
      hit_count: 2,
      superseded_by: "fct_new",
    },
  ],
};

function show(facts: MemoryFact[] = toFacts(payload)) {
  const onForget = vi.fn();
  render(<MemoryView facts={facts} onForget={onForget} />);
  return { onForget };
}

describe("MemoryView", () => {
  it("says what it has and what it dropped", () => {
    show();
    expect(screen.getByText("1 remembered")).toBeTruthy();
    expect(screen.getByText("1 replaced, kept")).toBeTruthy();
  });

  it("shows where a belief came from, not just what it is", () => {
    show();
    // The source is next to the value, in words, because `observed` and `user_corrected`
    // are different claims about the world. It appears twice on purpose: once on the row
    // and once inside the "why?" answer, which is a different question.
    expect(screen.getAllByText("you corrected me").length).toBeGreaterThan(0);
    expect(screen.getByText("pnpm test")).toBeTruthy();
    expect(screen.getByText(/used 7×/)).toBeTruthy();
  });

  it("answers why does ORACLE think that in one click", () => {
    show();
    fireEvent.click(screen.getByText("why does ORACLE think that?"));
    expect(screen.getByText("ev_41")).toBeTruthy();
    expect(screen.getByText(/from turn_9/)).toBeTruthy();
  });

  it("keeps a belief it dropped, under the one that replaced it", () => {
    show();
    // Not a top-level row — there is exactly one of those.
    expect(screen.getAllByRole("listitem").some((li) => li.className.includes("mem-fact"))).toBe(
      true,
    );
    expect(screen.getByText(/previously/)).toBeTruthy();
    expect(screen.getByText("npm test")).toBeTruthy();
    expect(screen.getByText(/I watched it work twice/)).toBeTruthy();
  });

  it("makes it stop thinking that in two", () => {
    const { onForget } = show();
    fireEvent.click(screen.getByRole("button", { name: "forget" }));
    expect(onForget).toHaveBeenCalledWith("fct_new");
  });

  it("says a stale fact is stale rather than quietly downgrading it", () => {
    const stale = toFacts({
      facts: [
        {
          ...payload.facts[0],
          stale: true,
          effective_confidence: 0.8,
          last_confirmed_at: new Date(Date.now() - 100 * 86_400_000).toISOString(),
        },
      ],
    });
    show(stale);
    expect(screen.getByText(/UNCONFIRMED FOR 90 DAYS/)).toBeTruthy();
    expect(screen.getByText(/confidence 0.8/)).toBeTruthy();
    // Still shown. It may well be right; it just says how old it is.
    expect(screen.getByText("pnpm test")).toBeTruthy();
  });

  it("explains an empty memory rather than rendering a blank panel", () => {
    show([]);
    expect(screen.getByText(/only remembers what you tell it/)).toBeTruthy();
  });
});
