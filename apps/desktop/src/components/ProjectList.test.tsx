/**
 * The sidebar's project section (docs/UI.md#4, docs/PROJECT_STATE.md).
 *
 * The fixtures below are the **shape the API actually returns** — snake_case keys, every
 * field present — not a convenient hand-written object. `TaskTree.test.tsx` is green on a
 * fixture the running app cannot produce, and that is the bug this file exists not to
 * repeat.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProjectList, toObservation, toProjects } from "./ProjectList";

/** Exactly what `GET /api/v1/projects` serialises. */
const WIRE = {
  projects: [
    {
      id: "pj_1",
      name: "Asterim",
      root: "C:\\Projects\\Asterim",
      status: "active",
      description: "the game",
      description_source: "user",
      first_seen: "2026-08-26T10:00:00.000Z",
      last_touched: "2026-08-26T18:00:00.000Z",
      briefed_through_seq: 12,
      open_tasks: 2,
      failed_tasks: 1,
      tokens_spent: 1400,
      usd_spent: 0.42,
    },
    {
      id: "pj_2",
      name: "GameRecs",
      root: "C:\\Projects\\GameRecs",
      status: "missing",
      description: "",
      description_source: "user",
      first_seen: "2026-08-26T10:00:00.000Z",
      last_touched: null,
      briefed_through_seq: 0,
      open_tasks: 0,
      failed_tasks: 0,
      tokens_spent: 0,
      usd_spent: 0,
    },
  ],
  candidates: ["New folder", "Kaggle"],
  projects_root: "C:\\Projects",
};

describe("toProjects", () => {
  it("reads the wire shape", () => {
    const data = toProjects(WIRE);
    // See Briefing.test.tsx: `noUncheckedIndexedAccess` is on, so the element is bound
    // and asserted before it is read.
    const first = data.projects[0];
    expect(first).toBeDefined();
    expect(data.projects.map((p) => p.name)).toEqual(["Asterim", "GameRecs"]);
    expect(first?.openTasks).toBe(2);
    expect(first?.failedTasks).toBe(1);
    expect(data.candidates).toEqual(["New folder", "Kaggle"]);
    expect(data.projectsRoot).toBe("C:\\Projects");
  });

  it("survives garbage without throwing", () => {
    // The panel must render something for a response it did not expect. A parser that
    // throws takes the whole sidebar down for one bad field.
    expect(toProjects(null).projects).toEqual([]);
    expect(toProjects({ projects: "nope", candidates: 3 }).projects).toEqual([]);
  });

  it("clamps an unknown status to idle", () => {
    // A class name built from server text is how a typo becomes an unstyled row.
    const data = toProjects({ projects: [{ id: "x", name: "X", status: "exploded" }] });
    expect(data.projects[0]?.status).toBe("idle");
  });
});

describe("ProjectList", () => {
  it("renders each project with its status as a word, not only a colour", () => {
    render(
      <ProjectList data={toProjects(WIRE)} onSelect={() => {}} onRegister={() => {}} />,
    );
    expect(screen.getByText("Asterim")).toBeTruthy();
    expect(screen.getByText("active")).toBeTruthy();
    expect(screen.getByText("missing")).toBeTruthy();
  });

  it("shows failed and open counts", () => {
    render(
      <ProjectList data={toProjects(WIRE)} onSelect={() => {}} onRegister={() => {}} />,
    );
    expect(screen.getByTitle("1 failed")).toBeTruthy();
    expect(screen.getByTitle("2 open")).toBeTruthy();
  });

  it("omits counts that are zero", () => {
    // A row of zeroes is noise. GameRecs has none of either.
    render(
      <ProjectList data={toProjects(WIRE)} onSelect={() => {}} onRegister={() => {}} />,
    );
    expect(screen.queryByTitle("0 open")).toBeNull();
    expect(screen.queryByTitle("0 failed")).toBeNull();
  });

  it("selects a project when clicked", () => {
    const onSelect = vi.fn();
    render(<ProjectList data={toProjects(WIRE)} onSelect={onSelect} onRegister={() => {}} />);

    fireEvent.click(screen.getByText("Asterim"));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0]?.[0]?.name).toBe("Asterim");
  });

  it("marks the selected project for assistive tech, not only visually", () => {
    render(
      <ProjectList
        data={toProjects(WIRE)}
        selected="pj_1"
        onSelect={() => {}}
        onRegister={() => {}}
      />,
    );
    const current = screen.getByText("Asterim").closest("button");
    expect(current?.getAttribute("aria-current")).toBe("true");
  });

  it("keeps candidates collapsed and separate from tracked projects", () => {
    render(
      <ProjectList data={toProjects(WIRE)} onSelect={() => {}} onRegister={() => {}} />,
    );
    // `New folder` is a real directory in the real projects root. Auto-registering it
    // would put it in the briefing, and the briefing's value is that it is short.
    expect(screen.getByText("2 not tracked")).toBeTruthy();
  });

  it("registers a candidate on click", () => {
    const onRegister = vi.fn();
    render(<ProjectList data={toProjects(WIRE)} onSelect={() => {}} onRegister={onRegister} />);

    fireEvent.click(screen.getByText("New folder"));
    expect(onRegister).toHaveBeenCalledWith("New folder");
  });

  it("distinguishes 'nothing on disk' from 'nothing registered'", () => {
    const { rerender } = render(
      <ProjectList
        data={{ projects: [], candidates: [], projectsRoot: "" }}
        onSelect={() => {}}
        onRegister={() => {}}
      />,
    );
    expect(screen.getByText("none discovered")).toBeTruthy();

    rerender(
      <ProjectList
        data={{ projects: [], candidates: ["Asterim"], projectsRoot: "" }}
        onSelect={() => {}}
        onRegister={() => {}}
      />,
    );
    // Two different situations that would otherwise render identically, and only one of
    // them is something the user can act on.
    expect(screen.getByText("none tracked yet — register one below")).toBeTruthy();
  });

  it("renders no git state unless an observation is handed to it", () => {
    // OQ-24, measured 2026-08-28: the full fan-out misses the 1 s budget 2–3×, so git
    // state exists only as a per-selected-row observation. With none passed, none may
    // appear — the eager fan-out stays impossible by default, and caching would make
    // the sidebar lie after a branch switch.
    const { container } = render(
      <ProjectList data={toProjects(WIRE)} onSelect={() => {}} onRegister={() => {}} />,
    );
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/main|branch|dirty|↑|↓/);
  });
});

/** Exactly what `GET /api/v1/projects/{id}` serialises for the observed half. */
const DETAIL_WIRE = {
  id: "pj_1",
  name: "Asterim",
  observation: {
    branch: "main",
    upstream: "origin/main",
    ahead: 3,
    behind: 1,
    dirty: 2,
    clean: false,
    last_commit: ["abc1234", "2026-08-28T10:00:00.000Z", "fix: timeout"],
    kinds: ["node"],
    test: ["npm test"],
    agent_docs: [],
    error: null,
  },
};

describe("the observed half (OQ-24: lazy, per selected row)", () => {
  it("parses the detail wire shape", () => {
    const o = toObservation(DETAIL_WIRE);
    expect(o.branch).toBe("main");
    expect(o.ahead).toBe(3);
    expect(o.behind).toBe(1);
    expect(o.dirty).toBe(2);
    expect(o.error).toBe("");
  });

  it("renders the git line on the selected row only", () => {
    render(
      <ProjectList
        data={toProjects(WIRE)}
        selected="pj_1"
        observation={toObservation(DETAIL_WIRE)}
        onSelect={vi.fn()}
        onRegister={vi.fn()}
      />,
    );
    const line = screen.getByText("⎇ main ↑3 ↓1 ~2");
    // On the Asterim row, not floating loose somewhere in the list.
    expect(line.closest("button")?.textContent).toContain("Asterim");
  });

  it("renders no git line while nothing is observed, and none for an errored one", () => {
    const { rerender } = render(
      <ProjectList
        data={toProjects(WIRE)}
        selected="pj_1"
        observation={null}
        onSelect={vi.fn()}
        onRegister={vi.fn()}
      />,
    );
    expect(screen.queryByText(/⎇/)).toBeNull();

    // A project that is not a repository has no branch to show; the status word
    // already carries what is wrong. No line beats a made-up one.
    rerender(
      <ProjectList
        data={toProjects(WIRE)}
        selected="pj_2"
        observation={toObservation({ observation: { error: "root does not exist" } })}
        onSelect={vi.fn()}
        onRegister={vi.fn()}
      />,
    );
    expect(screen.queryByText(/⎇/)).toBeNull();
  });

  it("keeps zeros out of the line rather than rendering them", () => {
    render(
      <ProjectList
        data={toProjects(WIRE)}
        selected="pj_1"
        observation={toObservation({
          observation: { branch: "main", ahead: 0, behind: 0, dirty: 0, clean: true, error: null },
        })}
        onSelect={vi.fn()}
        onRegister={vi.fn()}
      />,
    );
    expect(screen.getByText("⎇ main")).toBeTruthy();
  });
});
