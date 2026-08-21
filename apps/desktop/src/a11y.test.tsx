/**
 * Accessibility, checked rather than asserted.
 *
 * docs/UI.md#1 makes two of these a design rule rather than a nicety:
 *
 * - **Never colour alone.** Every status carries icon + label + colour. That is for
 *   accessibility *and* for glanceability — the whole interface is meant to be readable
 *   from across the room, which is the same requirement by another name.
 * - **Full keyboard navigation with visible focus.** An interface where the safety
 *   surface needs a mouse is one where the safety surface gets clicked without reading.
 *
 * axe runs against the real rendered DOM. It cannot see contrast on elements it cannot
 * lay out (happy-dom has no layout engine), so colour-contrast is disabled here and the
 * palette is fixed in `styles.css` against the tokens in docs/UI.md#14 instead.
 */

import { render } from "@testing-library/react";
import axe from "axe-core";
import { describe, expect, it } from "vitest";
import { CommandPalette } from "./components/CommandPalette";
import { ConfirmationCenter } from "./components/ConfirmationCenter";
import { TerminalDock } from "./components/TerminalDock";
import { ToolCard } from "./components/ToolCard";
import type { Approval, ToolCall } from "./protocol";

/** Rules happy-dom cannot answer honestly, because it does not lay anything out. */
const DISABLED = {
  "color-contrast": { enabled: false },
  "landmark-one-main": { enabled: false },
  "page-has-heading-one": { enabled: false },
  region: { enabled: false },
};

async function violations(node: HTMLElement): Promise<string[]> {
  const results = await axe.run(node, {
    rules: DISABLED,
    resultTypes: ["violations"],
  });
  return results.violations
    .filter((v) => v.impact === "critical" || v.impact === "serious")
    .map((v) => `${v.id} (${v.impact}): ${v.help}`);
}

const approval: Approval = {
  approvalId: "ap_1",
  tool: "git.push",
  tier: "T2",
  decision: "confirm",
  rule: "tools.git.push.tier",
  tainted: true,
  escalated: true,
  args: { path: "C:\\Projects\\Asterim" },
  preview: { summary: "Publishes commits to a remote.", detail: "2 commits" },
  expiresInSec: 180,
  issuedAt: Date.now(),
};

const call: ToolCall = {
  turnId: "t1",
  tool: "dev.run_tests",
  tier: "T1",
  args: { path: "C:\\Projects\\Asterim" },
  running: false,
  ok: false,
  durationMs: 2140,
  summary: "Tests FAILED: 40 passed, 1 failed",
  error: "assertion failed",
  undoId: null,
};

describe("no serious or critical accessibility violations", () => {
  it("confirmation center", async () => {
    const { container } = render(
      <ConfirmationCenter approvals={[approval]} decided={[]} onRespond={() => {}} />,
    );
    expect(await violations(container)).toEqual([]);
  });

  it("tool card", async () => {
    const { container } = render(<ToolCard call={call} onUndo={() => {}} />);
    expect(await violations(container)).toEqual([]);
  });

  it("command palette", async () => {
    const { container } = render(
      <CommandPalette open projects={["Asterim"]} onClose={() => {}} onSubmit={() => {}} />,
    );
    expect(await violations(container)).toEqual([]);
  });

  it("terminal dock", async () => {
    const { container } = render(
      <TerminalDock
        ptyId="term_1"
        cwd="C:\\Projects"
        chunks={[]}
        onInput={() => {}}
        onResize={() => {}}
        onOpen={() => {}}
        onClose={() => {}}
      />,
    );
    expect(await violations(container)).toEqual([]);
  });
});

describe("status is never carried by colour alone", () => {
  it("a failed tool card says FAILED and shows a glyph", () => {
    const { container } = render(<ToolCard call={call} onUndo={() => {}} />);
    expect(container.querySelector(".tc-status")?.textContent).toBe("FAILED");
    expect(container.querySelector(".tc-glyph")?.textContent).toBe("✗");
  });

  it("a running tool card says RUNNING and shows a different glyph", () => {
    const { container } = render(
      <ToolCard call={{ ...call, running: true, ok: undefined }} onUndo={() => {}} />,
    );
    expect(container.querySelector(".tc-status")?.textContent).toBe("RUNNING");
    expect(container.querySelector(".tc-glyph")?.textContent).toBe("◆");
  });

  it("an approval names its tier in text, not just in a colour", () => {
    const { container } = render(
      <ConfirmationCenter approvals={[approval]} decided={[]} onRespond={() => {}} />,
    );
    expect(container.querySelector(".ap-tier")?.textContent).toBe("T2");
    expect(container.textContent).toContain("APPROVAL REQUIRED");
  });
});

describe("the safety surface is reachable from the keyboard", () => {
  it("both approval actions are real buttons, so Tab reaches them", () => {
    const { container } = render(
      <ConfirmationCenter approvals={[approval]} decided={[]} onRespond={() => {}} />,
    );
    const buttons = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(buttons.some((t) => t?.includes("Deny"))).toBe(true);
    expect(buttons.some((t) => t?.includes("Approve"))).toBe(true);
  });

  it("the palette input is labelled and its list is a listbox", () => {
    const { container } = render(
      <CommandPalette open projects={["Asterim"]} onClose={() => {}} onSubmit={() => {}} />,
    );
    expect(container.querySelector("input")?.getAttribute("aria-label")).toBeTruthy();
    expect(container.querySelector('[role="listbox"]')).toBeTruthy();
  });
});
