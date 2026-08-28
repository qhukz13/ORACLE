/**
 * The centre-stage switcher is a real tablist, not buttons wearing the class: the role
 * claims arrow-key navigation, so these tests hold it to that (the TaskTree rule —
 * claim an ARIA pattern only with the behaviour attached).
 *
 * The three bespoke toggles this replaced had three different flip-back rules, one of
 * them inconsistent by accident (Events toggled on `stage === "chat"`, so pressing it
 * from Memory went to Events while Memory's own button went home). A registry cannot
 * have per-button rules — that is the regression these tests pin against.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { STAGES, ViewTabs } from "./ViewTabs";

describe("ViewTabs", () => {
  it("renders one tab per stage, with the selected one marked and focusable", () => {
    render(<ViewTabs stage="tasks" onSwitch={vi.fn()} />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(STAGES.length);
    const selected = screen.getByRole("tab", { selected: true });
    expect(selected.textContent).toContain("Tasks");
    // Roving tabindex: exactly the selected tab is in the tab order.
    expect(selected.tabIndex).toBe(0);
    for (const tab of tabs) {
      if (tab !== selected) expect(tab.tabIndex).toBe(-1);
    }
  });

  it("switches on click", () => {
    const onSwitch = vi.fn();
    render(<ViewTabs stage="chat" onSwitch={onSwitch} />);
    fireEvent.click(screen.getByRole("tab", { name: /Memory/ }));
    expect(onSwitch).toHaveBeenCalledWith("memory");
  });

  it("moves with the arrow keys, wrapping at both ends", () => {
    const onSwitch = vi.fn();
    const { rerender } = render(<ViewTabs stage="chat" onSwitch={onSwitch} />);
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(onSwitch).toHaveBeenLastCalledWith("tasks");
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowLeft" });
    expect(onSwitch).toHaveBeenLastCalledWith("knowledge"); // wraps backwards off chat

    rerender(<ViewTabs stage="knowledge" onSwitch={onSwitch} />);
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(onSwitch).toHaveBeenLastCalledWith("chat"); // wraps forwards off the end
  });

  it("jumps home and end", () => {
    const onSwitch = vi.fn();
    render(<ViewTabs stage="events" onSwitch={onSwitch} />);
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "Home" });
    expect(onSwitch).toHaveBeenLastCalledWith("chat");
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "End" });
    expect(onSwitch).toHaveBeenLastCalledWith("knowledge");
  });

  it("advertises the Ctrl+digit keys on the four primary tabs", () => {
    render(<ViewTabs stage="chat" onSwitch={vi.fn()} />);
    expect(screen.getByRole("tab", { name: "Chat" }).title).toContain("Ctrl+1");
    expect(screen.getByRole("tab", { name: "Tasks" }).title).toContain("Ctrl+2");
    expect(screen.getByRole("tab", { name: "Briefing" }).title).not.toContain("Ctrl+");
  });

  it("lets a stage demand attention without saying it twice to a screen reader", () => {
    const { container, rerender } = render(
      <ViewTabs stage="chat" onSwitch={vi.fn()} attn={{ briefing: true }} />,
    );
    const briefing = screen.getByRole("tab", { name: "Briefing" });
    expect(briefing.className).toContain("attn");
    expect(container.querySelector(".tab-dot")?.getAttribute("aria-hidden")).toBe("true");

    rerender(<ViewTabs stage="chat" onSwitch={vi.fn()} />);
    expect(container.querySelector(".tab-dot")).toBeNull();
  });
});
