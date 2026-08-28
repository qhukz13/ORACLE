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
 * lay out (happy-dom has no layout engine), so colour-contrast is disabled here — and
 * because that left §14's one explicitly "risky" rule unchecked by anything, it is now
 * checked as arithmetic instead: `contrast.test.ts` parses the tokens out of `styles.css`
 * and does the WCAG sums, no DOM required. It found `--st-halt` under 3:1 on every surface.
 *
 * The list below covered 4 of 12 components until 2026-08-26. An audit that skips most of
 * the interface is a statement about the auditor, so the rest are here now — added *before*
 * Phase 11's new surfaces land, so the audit is a standing gate rather than a phase artifact.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import axe from "axe-core";
import { describe, expect, it, vi } from "vitest";
import { Briefing, toBriefing } from "./components/Briefing";
import { Citations } from "./components/Citations";
import { CommandPalette } from "./components/CommandPalette";
import { DelegationPanel } from "./components/DelegationPanel";
import { EgressPreview } from "./components/EgressPreview";
import { GraphCard } from "./components/GraphCard";
import { Inspector } from "./components/Inspector";
import { KnowledgeHealth } from "./components/KnowledgeHealth";
import { MemoryView } from "./components/MemoryView";
import { PipelineCard } from "./components/PipelineCard";
import { ProjectList, toProjects } from "./components/ProjectList";
import { TaskTree } from "./components/TaskTree";
import { Timeline } from "./components/Timeline";
import { ConfirmationCenter } from "./components/ConfirmationCenter";
import { TerminalDock } from "./components/TerminalDock";
import { ToolCard } from "./components/ToolCard";
import { ViewTabs } from "./components/ViewTabs";
import type { Approval, GraphTask, ToolCall } from "./protocol";
import type { Turn } from "./store";

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
  // The seven that had no case until 2026-08-26. Each renders the shape the app actually
  // produces, not an empty one — an axe pass over a component with nothing in it is a pass
  // over an empty div.

  it("task tree", async () => {
    const { container } = render(
      <TaskTree
        graphs={[
          {
            rootId: "tk_root",
            tasks: [
              {
                taskId: "tk_root-a",
                kind: "tool",
                status: "succeeded",
                dependsOn: [],
                objective: "dev.lint (oracle-selfcheck/lint)",
                role: "operator",
                summary: "dev.lint ok",
                evidence: { observed: { passed: 41, failed: 0 } },
              },
              {
                taskId: "tk_root-b",
                kind: "delegation",
                status: "failed",
                dependsOn: ["tk_root-a"],
                objective: "cover the 401 case",
                role: "tester",
                agent: "claude",
                attempt: 2,
                maxAttempts: 2,
                summary: "tests 40/41",
                claim: "everything passes",
              },
            ],
          },
        ]}
        onCancelTask={() => {}}
        onCancelGraph={() => {}}
      />,
    );
    expect(await violations(container)).toEqual([]);
  });

  it("graph approval card", async () => {
    const { container } = render(
      <GraphCard
        preview={{
          objective: "continue development on Asterim",
          summary: "four tasks",
          authored_by: "planner",
          rung: 1,
          risks: ["the retry policy is guessed"],
          tasks: [
            {
              task_id: "A",
              kind: "delegation",
              role: "coder",
              agent: "claude",
              objective: "implement retry logic",
              egresses: true,
            },
          ],
          note: "approving runs the graph",
        }}
      />,
    );
    expect(await violations(container)).toEqual([]);
  });

  it("pipeline approval card", async () => {
    const { container } = render(
      <PipelineCard
        preview={{
          pipeline: "oracle-selfcheck",
          source: "project",
          path: "config/pipelines/oracle-selfcheck.yaml",
          project: "ORACLE",
          params: { security_only: false },
          steps: [
            {
              step: "types",
              tool: "dev.execute",
              args: { path: "C:/Projects/ORACLE", program: "uv", args: ["run", "mypy"] },
              tier: "T2",
              rule: "tools.dev.execute.tier",
              asks: true,
            },
          ],
          omitted: [{ step: "tests", reason: "when: not params.security_only" }],
          note: "approving runs every step listed above",
        }}
      />,
    );
    expect(await violations(container)).toEqual([]);
  });

  it("delegation panel", async () => {
    const { container } = render(
      <DelegationPanel
        delegations={[
          {
            taskId: "dlg_1",
            task: "add a retry to the token refresh",
            adapter: "claude_cli",
            state: "awaiting_egress",
            feed: [{ kind: "thinking", text: "reading TokenService.ts", tool: null, fromSubagent: false }],
          },
        ]}
        onDiscard={() => {}}
      />,
    );
    expect(await violations(container)).toEqual([]);
  });

  it("memory view", async () => {
    const { container } = render(
      <MemoryView
        facts={[
          {
            id: "f1",
            kind: "preference",
            scope: "project",
            scopeRef: "Asterim",
            key: "test-runner",
            value: "prefers vitest over jest",
            source: "user_stated",
            confidence: 0.9,
            effectiveConfidence: 0.85,
            stale: false,
            evidence: ["said so on 2026-08-25"],
            origin: "chat",
            createdAt: "2026-08-25T00:00:00Z",
            lastConfirmedAt: "2026-08-25T00:00:00Z",
            hitCount: 3,
            supersededBy: "",
          },
        ]}
        onForget={() => {}}
      />,
    );
    expect(await violations(container)).toEqual([]);
  });

  it("knowledge health", async () => {
    // Built, tested, and imported by nothing until Phase 11 mounts it — ADR-0023 puts the
    // re-layout action here, so it needs to be audited before it becomes reachable.
    const { container } = render(
      <KnowledgeHealth
        data={{
          built: true,
          model: "bge-m3",
          path: "D:/ORACLE/data/knowledge.db",
          fileBytes: 141180928,
          chunks: 14586,
          vectors: 13674,
          collections: [
            {
              id: "notes",
              documents: 166,
              lastIndexed: "2026-08-24T15:32:03Z",
              bytes: 105847097,
            },
          ],
          failures: [],
        }}
        onReindex={() => {}}
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

const projectsData = toProjects({
  projects: [
    {
      id: "pj_1",
      name: "Asterim",
      root: "C:\\Projects\\Asterim",
      status: "active",
      description: "",
      open_tasks: 2,
      failed_tasks: 1,
      usd_spent: 0.42,
    },
    {
      id: "pj_2",
      name: "GameRecs",
      root: "C:\\Projects\\GameRecs",
      status: "missing",
      open_tasks: 0,
      failed_tasks: 0,
      usd_spent: 0,
    },
  ],
  candidates: ["New folder"],
  projects_root: "C:\\Projects",
});

const briefingData = toBriefing({
  through_seq: 128,
  since_ts: "2026-08-26T18:04:00.000Z",
  empty: false,
  text: "",
  projects: [
    {
      project: "Asterim",
      status: "active",
      completed: 3,
      failed: 1,
      waiting: 1,
      in_flight: 2,
      cancelled: 0,
      elapsed_s: 2280,
      usd: 0.42,
      needs_you: true,
      more: 4,
      highlights: [
        { id: "tk_1", objective: "fix pipeline timeout", status: "waiting" },
        {
          id: "tk_2",
          objective: "regression tests",
          status: "failed",
          error: "3 tests still failing",
        },
      ],
    },
  ],
  system: {
    restarted_at: "2026-08-26T04:12:00.000Z",
    unclean: true,
    degraded: ["Ollama is not reachable"],
    errors: 2,
  },
});

describe("the P12 surfaces pass the standing audit", () => {
  it("project list", async () => {
    const { container } = render(
      <ProjectList data={projectsData} onSelect={() => {}} onRegister={() => {}} />,
    );
    expect(await violations(container)).toEqual([]);
  });

  it("briefing", async () => {
    const { container } = render(
      <Briefing
        data={briefingData}
        onAcknowledge={() => {}}
        onInspect={() => {}}
        onOpenProject={() => {}}
      />,
    );
    expect(await violations(container)).toEqual([]);
  });

  it("briefing, empty", async () => {
    const { container } = render(
      <Briefing
        data={toBriefing({ empty: true })}
        onAcknowledge={() => {}}
        onInspect={() => {}}
        onOpenProject={() => {}}
      />,
    );
    expect(await violations(container)).toEqual([]);
  });

  it("a project's status is a word, so it is not colour alone", () => {
    const { container } = render(
      <ProjectList data={projectsData} onSelect={() => {}} onRegister={() => {}} />,
    );
    const words = [...container.querySelectorAll(".pj-status")].map((n) => n.textContent);
    expect(words).toContain("active");
    expect(words).toContain("missing");
  });

  it("every briefing line carries its status as a word", () => {
    const { container } = render(
      <Briefing
        data={briefingData}
        onAcknowledge={() => {}}
        onInspect={() => {}}
        onOpenProject={() => {}}
      />,
    );
    const lines = [...container.querySelectorAll(".bf-line")];
    expect(lines.length).toBeGreaterThan(0);
    for (const line of lines) {
      expect(line.querySelector(".bf-status")?.textContent?.trim()).toBeTruthy();
    }
  });

  it("each inspect button names what it inspects", () => {
    // "inspect" repeated eight times is unusable in a screen reader's element list.
    const { container } = render(
      <Briefing
        data={briefingData}
        onAcknowledge={() => {}}
        onInspect={() => {}}
        onOpenProject={() => {}}
      />,
    );
    const labels = [...container.querySelectorAll("button[aria-label]")].map((b) =>
      b.getAttribute("aria-label"),
    );
    expect(new Set(labels).size).toBe(labels.length);
    expect(labels.some((l) => l?.includes("fix pipeline timeout"))).toBe(true);
  });
});

// The three components the audit did not cover until 2026-08-28 — recorded as a gap in
// current_state.md item 4 — plus P11-T5's new tab strip. Same rule as the 2026-08-26
// additions: each renders the shape the app actually produces, not an empty one.

const inspectedTask: GraphTask = {
  taskId: "tk_2",
  kind: "delegation",
  status: "failed",
  dependsOn: ["tk_1"],
  objective: "make the regression tests pass",
  role: "implementer",
  agent: "claude",
  attempt: 2,
  maxAttempts: 3,
  startedAt: "2026-08-28T10:00:00.000Z",
  finishedAt: "2026-08-28T10:02:05.000Z",
  cost: { tokens: 14000, usd: 0.42 },
  evidence: { diff_lines: 120, observed: { passed: 40, failed: 3 } },
  claim: "everything passes now",
  supersedes: "tk_0",
};

describe("the components the audit missed, and the T5 surfaces", () => {
  it("view tabs, with the panel they control", async () => {
    // The tabs never render without their tabpanel in the app, and axe rightly flags a
    // dangling `aria-controls` — so the audit renders the real pair.
    const { container } = render(
      <>
        <ViewTabs stage="chat" onSwitch={() => {}} attn={{ briefing: true }} />
        <div role="tabpanel" id="stage-panel" aria-labelledby="tab-chat" />
      </>,
    );
    expect(await violations(container)).toEqual([]);
  });

  it("inspector, with both a task and a turn", async () => {
    const turn: Turn = {
      turnId: "t1",
      sessionId: "s1",
      userText: "continue ORACLE",
      reply: "dispatched",
      done: true,
      outcome: "completed",
      tools: [call],
    };
    const { container } = render(
      <Inspector turn={turn} traceId="tr_abc" onUndo={() => {}} task={inspectedTask} />,
    );
    expect(await violations(container)).toEqual([]);
  });

  it("citations, including the tainted and degraded states", async () => {
    const { container } = render(
      <Citations
        citations={[
          {
            chunkId: "ch_1",
            project: "ORACLE",
            path: "docs/SECURITY.md",
            absPath: "C:\\Projects\\ORACLE\\docs\\SECURITY.md",
            anchor: "6. Taint",
            score: 0.81,
            provenance: "local_owned",
            indexedAt: "2026-08-27T10:00:00.000Z",
          },
          {
            chunkId: "ch_2",
            project: "notes",
            path: "vault/agents.md",
            absPath: "D:\\Vault\\agents.md",
            anchor: "(file)",
            score: 0.66,
            provenance: "local_foreign",
            indexedAt: "2026-08-27T10:00:00.000Z",
          },
        ]}
        tainted
        degraded
        onOpen={() => {}}
      />,
    );
    expect(await violations(container)).toEqual([]);
  });

  it("timeline", async () => {
    const { container } = render(
      <Timeline
        events={[
          {
            v: 1,
            seq: 1,
            ts: "2026-08-28T20:00:00.000Z",
            type: "turn.started",
            session_id: "s1",
            turn_id: "t1",
            task_id: null,
            trace_id: "tr_1",
            payload: { text: "continue ORACLE" },
          },
          {
            v: 1,
            seq: 2,
            ts: "2026-08-28T20:00:01.000Z",
            type: "tool.finished",
            session_id: "s1",
            turn_id: "t1",
            task_id: null,
            trace_id: "tr_1",
            payload: { tool: "git.status", ok: false, error: "not a repository" },
          },
        ]}
        onInspect={() => {}}
      />,
    );
    expect(await violations(container)).toEqual([]);
  });

  it("global search, with results and the taint badge showing", async () => {
    const { GlobalSearch } = await import("./components/GlobalSearch");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          query: "auth",
          elapsed_ms: 700,
          projects: [{ id: "pj_1", name: "Asterim", status: "active", description: "" }],
          tasks: [],
          events: [{ seq: 1, ts: "2026-08-28T20:00:00Z", type: "turn.started", snippet: "{}" }],
          files: [
            {
              collection: "projects",
              project: "Asterim",
              path: "src/auth.ts",
              anchor: "(file)",
              provenance: "local_foreign",
              text: "…",
            },
          ],
          notes: [],
          tainted: true,
          degraded: false,
          knowledge_error: "",
          git: [],
          git_searched: false,
          git_error: "",
        }),
      }),
    );
    try {
      const { container } = render(
        <GlobalSearch
          open
          project={null}
          onClose={() => {}}
          onOpenProject={() => {}}
          onInspectTask={() => {}}
          onOpenTimeline={() => {}}
        />,
      );
      fireEvent.change(screen.getByLabelText("Global search query"), {
        target: { value: "auth" },
      });
      await waitFor(() => expect(screen.getByText("PROJECTS (1)")).toBeTruthy());
      expect(await violations(container)).toEqual([]);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("egress preview", async () => {
    const { container } = render(
      <EgressPreview
        preview={{
          adapter: "claude",
          destination: "api.anthropic.com",
          tokens: 2820,
          files: ["docs/current_task.md", "docs/ROADMAP.md"],
          redactions: ["OPENAI_API_KEY"],
          tainted_sources: ["docs/current_task.md"],
          allowed_tools: ["fs.read", "dev.run_tests"],
          dropped_excerpts: 1,
          packet_dir: "D:\\ORACLE\\packets\\tk_9",
        }}
      />,
    );
    expect(await violations(container)).toEqual([]);
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

  it("the palette input is a combobox whose active option is announced", () => {
    // Closed 2026-08-28, the audit's last debt: role="option" rows existed only
    // visually. Focus stays in the input, so the input must carry the combobox role
    // and point aria-activedescendant at a row that really exists.
    const { container } = render(
      <CommandPalette open projects={["Asterim"]} onClose={() => {}} onSubmit={() => {}} />,
    );
    const input = container.querySelector("input");
    expect(input?.getAttribute("aria-label")).toBeTruthy();
    expect(input?.getAttribute("role")).toBe("combobox");
    expect(container.querySelector('[role="listbox"]')?.id).toBe(
      input?.getAttribute("aria-controls"),
    );
    const active = input?.getAttribute("aria-activedescendant");
    expect(active).toBeTruthy();
    expect(container.querySelector(`#${active}`)?.getAttribute("role")).toBe("option");
  });
});
