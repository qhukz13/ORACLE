/**
 * The pipeline card is part of the security model, not a rendering of it.
 *
 * It is the only card in ORACLE that authorises several actions at once, so what it
 * fails to show is what somebody will not have read. These tests assert the three things
 * a person cannot make a good decision without: the concrete arguments, where the file
 * came from, and what a condition removed.
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PipelineCard } from "./PipelineCard";

const PREVIEW = {
  pipeline: "asterim-check",
  source: "global",
  path: "config/pipelines/asterim-check.yaml",
  project: "Asterim",
  params: { skip_frontend: false },
  steps: [
    {
      step: "status",
      tool: "git.status",
      args: { path: "C:/Projects/Asterim" },
      tier: "T0",
      rule: "tools.git.status.tier",
      asks: false,
    },
    {
      step: "build",
      tool: "dev.execute",
      args: { path: "C:/Projects/Asterim", program: "npm", args: ["run", "build"] },
      tier: "T2",
      rule: "tools.dev.execute.tier",
      asks: true,
    },
  ],
  omitted: [{ step: "frontend_tests", reason: "when: not params.skip_frontend" }],
  note: "approving runs every step listed above with exactly these arguments",
};

describe("PipelineCard", () => {
  it("shows each step's tool and its resolved arguments, not a summary", () => {
    // SECURITY.md §2 rule 5: confirm actions, not intentions. The grant that gets minted
    // is bound to a digest of exactly these arguments, so an abbreviation here would be
    // an argument nobody checked.
    render(<PipelineCard preview={PREVIEW} />);
    const card = screen.getByTestId("pipeline-card");

    expect(within(card).getByText("git.status")).toBeTruthy();
    expect(within(card).getByText("dev.execute")).toBeTruthy();
    expect(card.textContent).toContain("path=C:/Projects/Asterim");
    expect(card.textContent).toContain("program=npm");
    // A list argument is joined, not printed as "[object Object]" or elided.
    expect(card.textContent).toContain("args=run build");
  });

  it("marks the steps this decision is actually about", () => {
    render(<PipelineCard preview={PREVIEW} />);
    const card = screen.getByTestId("pipeline-card");
    expect(card.textContent).toContain("1 needs this approval");
    expect(within(card).getAllByText("NEEDS APPROVAL")).toHaveLength(1);
  });

  it("says when a pipeline came from a repository rather than from your config", () => {
    // A pipeline under `<project>/.oracle/pipelines/` is repository content — the same
    // trust class as a checked-in AGENTS.md. Approving one is a different decision, and
    // the tier alone does not say which kind it is.
    render(<PipelineCard preview={{ ...PREVIEW, source: "project" }} />);
    const card = screen.getByTestId("pipeline-card");
    expect(card.textContent).toContain("repository");
    expect(card.querySelector(".pc-source-project")).toBeTruthy();
  });

  it("does not cry wolf about a pipeline from your own config", () => {
    render(<PipelineCard preview={PREVIEW} />);
    const card = screen.getByTestId("pipeline-card");
    expect(card.querySelector(".pc-source-project")).toBeNull();
    expect(card.textContent).toContain("from your config");
  });

  it("shows what a condition removed, because a run is defined by that too", () => {
    render(<PipelineCard preview={PREVIEW} />);
    const card = screen.getByTestId("pipeline-card");
    expect(card.textContent).toContain("NOT RUNNING");
    expect(card.textContent).toContain("frontend_tests");
    expect(card.textContent).toContain("when: not params.skip_frontend");
  });

  it("counts only the steps that will run", () => {
    render(<PipelineCard preview={PREVIEW} />);
    expect(screen.getByTestId("pipeline-card").textContent).toContain("2 steps");
  });

  it("renders a payload with nothing in it rather than throwing", () => {
    // Every card in this app has to survive a payload from a newer or older daemon.
    render(<PipelineCard preview={{}} />);
    expect(screen.getByTestId("pipeline-card")).toBeTruthy();
  });
});
