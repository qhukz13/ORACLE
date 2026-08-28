/**
 * The §6 box, rendered from a payload shaped exactly like the server's
 * `approval.requested` preview for `ai.delegate` — the fields the security suite
 * asserts the service emits. If a number matters to the decision, it must be visible.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EgressPreview } from "./EgressPreview";

const PREVIEW = {
  destination: "api.anthropic.com",
  adapter: "claude-code",
  files: ["TASK.md", "CONTEXT.md", "packet.json"],
  tokens: 28400,
  redactions: ["src/auth/token.ts: anthropic_key", "STATE.md: assigned_secret"],
  dropped_excerpts: 2,
  allowed_tools: ["Read", "Edit"],
  tainted_sources: [],
  packet_dir: "C:\\Projects\\Asterim\\.oracle\\handoff\\dlg_1",
};

describe("EgressPreview", () => {
  it("names the destination, the size, and every file that would leave", () => {
    render(<EgressPreview preview={PREVIEW} />);
    expect(screen.getByText(/api\.anthropic\.com/)).toBeTruthy();
    expect(screen.getByText(/3 files · 28400 tokens/)).toBeTruthy();
    expect(screen.getByText("CONTEXT.md")).toBeTruthy();
  });

  it("shows each redaction as an occurrence without exposing the value", () => {
    render(<EgressPreview preview={PREVIEW} />);
    expect(screen.getByText(/2 redactions applied/)).toBeTruthy();
    expect(screen.getByText(/src\/auth\/token\.ts: anthropic_key/)).toBeTruthy();
  });

  it("says when the budget cut context, so approval is of the truth", () => {
    render(<EgressPreview preview={PREVIEW} />);
    expect(screen.getByText(/2 excerpts dropped to fit the token budget/)).toBeTruthy();
  });

  it("flags tainted sources by name, and stays quiet when there are none", () => {
    const { rerender } = render(<EgressPreview preview={PREVIEW} />);
    expect(screen.queryByText(/did not author/)).toBeNull();
    rerender(
      <EgressPreview preview={{ ...PREVIEW, tainted_sources: ["vendor/readme.ru.md"] }} />,
    );
    expect(screen.getByText(/vendor\/readme\.ru\.md/)).toBeTruthy();
  });

  it("says no redactions were needed rather than showing an empty list", () => {
    render(<EgressPreview preview={{ ...PREVIEW, redactions: [] }} />);
    expect(screen.getByText(/No redactions were needed/)).toBeTruthy();
  });
});
