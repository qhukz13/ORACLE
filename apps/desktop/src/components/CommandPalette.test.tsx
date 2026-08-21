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
