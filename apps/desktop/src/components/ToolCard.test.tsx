/**
 * The card shows what actually ran (docs/UI.md#5). These tests cover the part added in
 * Phase 5: a `know.*` result carries its sources, and every other tool carries none.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ToolCall } from "../protocol";
import { ToolCard } from "./ToolCard";

function call(over: Partial<ToolCall> = {}): ToolCall {
  return {
    turnId: "t1",
    tool: "git.status",
    tier: "T0",
    args: { path: "C:\\Projects\\Asterim" },
    running: false,
    ok: true,
    durationMs: 84,
    summary: "main is clean.",
    error: null,
    undoId: null,
    ...over,
  };
}

const SOURCES = [
  {
    chunk_id: "c1",
    project: "Asterim",
    path: "apps/server/src/services/TokenService.ts",
    abs_path: "C:/Projects/Asterim/apps/server/src/services/TokenService.ts",
    anchor: "signAccessToken",
    score: 0.83,
    provenance: "local_owned",
    indexed_at: "2026-08-22T00:00:00Z",
  },
];

describe("ToolCard", () => {
  it("shows the arguments verbatim rather than a paraphrase", () => {
    render(<ToolCard call={call()} onUndo={vi.fn()} />);
    expect(screen.getByText(/C:\\Projects\\Asterim/)).toBeTruthy();
  });

  it("renders no sources for a tool that has none", () => {
    render(<ToolCard call={call()} onUndo={vi.fn()} />);
    expect(screen.queryByRole("region", { name: "Sources" })).toBeNull();
  });

  it("renders the sources behind a know.* result", () => {
    render(
      <ToolCard
        call={call({ tool: "know.search", citations: SOURCES })}
        onUndo={vi.fn()}
      />,
    );
    expect(screen.getByRole("region", { name: "Sources" })).toBeTruthy();
    expect(screen.getByText("apps/server/src/services/TokenService.ts")).toBeTruthy();
  });

  it("opens the source it was given, not one it assembled", () => {
    // The absolute path comes from the runtime. A path built in the browser is how a
    // citation ends up pointing somewhere that does not exist.
    const onOpenSource = vi.fn();
    render(
      <ToolCard
        call={call({ tool: "know.search", citations: SOURCES })}
        onUndo={vi.fn()}
        onOpenSource={onOpenSource}
      />,
    );
    screen.getByRole("button", { name: /TokenService\.ts/ }).click();
    expect(onOpenSource).toHaveBeenCalledWith(SOURCES[0]!.abs_path);
  });

  it("surfaces taint on the card that caused it", () => {
    render(
      <ToolCard
        call={call({
          tool: "know.search",
          citations: [{ ...SOURCES[0]!, provenance: "local_foreign" }],
          tainted: true,
        })}
        onUndo={vi.fn()}
      />,
    );
    expect(screen.getByRole("status").textContent).toMatch(/untrusted content/i);
  });

  it("still offers Undo only when the runtime recorded one", () => {
    const onUndo = vi.fn();
    render(<ToolCard call={call({ undoId: "u1" })} onUndo={onUndo} />);
    screen.getByRole("button", { name: "Undo" }).click();
    expect(onUndo).toHaveBeenCalledWith("u1");
  });
});
