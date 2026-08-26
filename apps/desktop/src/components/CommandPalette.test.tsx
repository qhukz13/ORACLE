/**
 * The palette's job is to be the fastest path to anything, and to never dead-end.
 *
 * Ranking is deliberately deterministic — prefix, then substring, no fuzzy scoring —
 * for the same reason the orbit layout is deterministic: the same query giving the same
 * order is what lets muscle memory form.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { buildItems, CommandPalette } from "./CommandPalette";

const PROJECTS = ["Asterim", "GameRecs", "Source2DemViewer"];

describe("building the result list", () => {
  it("always ends with 'ask the agent' so the palette cannot dead-end", () => {
    const items = buildItems("something nothing matches", PROJECTS);
    expect(items.at(-1)?.kind).toBe("chat");
    expect(items.at(-1)?.send).toBe("something nothing matches");
  });

  it("offers no chat fallback for an empty query, because there is nothing to ask", () => {
    expect(buildItems("", PROJECTS).some((i) => i.kind === "chat")).toBe(false);
  });

  it("matches commands by prefix", () => {
    const items = buildItems("stat", PROJECTS);
    expect(items[0]?.label).toBe("/status");
  });

  it("scopes to commands with > or /", () => {
    const items = buildItems(">", PROJECTS);
    expect(items.every((i) => i.kind !== "project")).toBe(true);
  });

  it("scopes to projects with @", () => {
    const items = buildItems("@aster", PROJECTS);
    expect(items.filter((i) => i.kind === "project").map((i) => i.label)).toEqual(["Asterim"]);
    expect(items.some((i) => i.kind === "command")).toBe(false);
  });

  it("is deterministic — the same query gives the same order", () => {
    const a = buildItems("s", PROJECTS).map((i) => i.id);
    const b = buildItems("s", PROJECTS).map((i) => i.id);
    expect(a).toEqual(b);
  });

  it("sends a command in the slash form the pre-router understands", () => {
    const halt = buildItems("halt", PROJECTS).find((i) => i.id === "cmd:halt");
    expect(halt?.send).toBe("/halt");
  });
});

describe("keyboard navigation", () => {
  it("moves the selection and submits with Enter", () => {
    const onSubmit = vi.fn();
    const onClose = vi.fn();
    render(
      <CommandPalette open projects={PROJECTS} onClose={onClose} onSubmit={onSubmit} />,
    );

    const input = screen.getByLabelText("Command palette query");
    fireEvent.change(input, { target: { value: "status" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(onSubmit).toHaveBeenCalledWith("/status");
    expect(onClose).toHaveBeenCalled();
  });

  it("wraps around with ArrowDown", () => {
    render(<CommandPalette open projects={[]} onClose={vi.fn()} onSubmit={vi.fn()} />);
    const input = screen.getByLabelText("Command palette query");
    fireEvent.change(input, { target: { value: "e" } });

    const options = screen.getAllByRole("option");
    expect(options[0]?.getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(screen.getAllByRole("option")[1]?.getAttribute("aria-selected")).toBe("true");
  });

  it("closes on Escape without submitting", () => {
    const onSubmit = vi.fn();
    const onClose = vi.fn();
    render(<CommandPalette open projects={[]} onClose={onClose} onSubmit={onSubmit} />);
    fireEvent.keyDown(screen.getByLabelText("Command palette query"), { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("renders nothing when closed", () => {
    const { container } = render(
      <CommandPalette open={false} projects={PROJECTS} onClose={vi.fn()} onSubmit={vi.fn()} />,
    );
    expect(container.innerHTML).toBe("");
  });
});

describe("buildItems — delegation", () => {
  it("offers a delegation once the query names a known project", () => {
    const items = buildItems("fix the auth tests in Asterim", ["Asterim", "GrowAMonster"]);
    const delegate = items.find((i) => i.kind === "delegate");
    expect(delegate).toBeTruthy();
    // The phrasing the pre-router recognises deterministically, so the entry does not
    // depend on the model classifying it.
    expect(delegate?.send).toBe("ask Claude to fix the auth tests in Asterim");
    expect(delegate?.hint).toContain("Asterim");
    // The entry says a human still approves the egress; it is half a decision.
    expect(delegate?.hint).toContain("approve");
  });

  it("does not offer one until a project is named", () => {
    const items = buildItems("fix the auth tests", ["Asterim"]);
    expect(items.some((i) => i.kind === "delegate")).toBe(false);
  });

  it("does not offer one for a slash command or a bare project browse", () => {
    expect(buildItems("/halt", ["Asterim"]).some((i) => i.kind === "delegate")).toBe(false);
    expect(buildItems("@Asterim", ["Asterim"]).some((i) => i.kind === "delegate")).toBe(false);
  });
});

describe("pipelines in the palette", () => {
  const PIPELINES = [
    {
      name: "oracle-selfcheck",
      description: "ORACLE's own quality gate.",
      project: "ORACLE",
      source: "global",
      steps: 5,
    },
    {
      name: "asterim-check",
      description: "Health check before pushing.",
      project: "Asterim",
      source: "project",
      steps: 4,
    },
  ];

  it("offers a discovered workflow by name", () => {
    const items = buildItems("selfcheck", [], PIPELINES);
    const pipe = items.find((i) => i.kind === "pipeline");
    expect(pipe?.label).toBe("oracle-selfcheck");
    // Sent as the bare name: the pre-router matches it deterministically, with no model
    // in the loop (PIPELINES.md §5).
    expect(pipe?.send).toBe("oracle-selfcheck");
  });

  it("says how many steps and which project, before anything is run", () => {
    const items = buildItems("selfcheck", [], PIPELINES);
    expect(items.find((i) => i.kind === "pipeline")?.hint).toContain("5 steps");
    expect(items.find((i) => i.kind === "pipeline")?.hint).toContain("ORACLE");
  });

  it("marks a workflow that came from a repository", () => {
    // Running a pipeline someone else's repo shipped is a different decision from running
    // your own, and the palette is where that decision starts.
    const items = buildItems("asterim", [], PIPELINES);
    expect(items.find((i) => i.kind === "pipeline")?.hint).toContain("from the repository");
  });

  it("does not offer one when a slash query is being typed", () => {
    expect(buildItems("/hal", [], PIPELINES).some((i) => i.kind === "pipeline")).toBe(false);
  });

  it("is absent, not broken, when nothing was discovered", () => {
    expect(buildItems("check", []).some((i) => i.kind === "pipeline")).toBe(false);
  });
});
