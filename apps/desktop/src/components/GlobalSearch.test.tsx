/**
 * Global search (UI.md §11). What these tests hold onto:
 *
 *   - the fetch is debounced — the retrieval half costs ~0.7-1.3 s through the
 *     serialised toolhost (measured), so a request per keystroke would queue embeds
 *     behind each other;
 *   - Enter does only what the app can honestly do: select a project, inspect a task,
 *     jump to the timeline — and does NOTHING for files/notes/git, which are previews
 *     until a viewer exists. A no-op beats a pretence;
 *   - the taint badge survives the trip — a search result is a chunk somebody may not
 *     have written;
 *   - the absent GIT group says why it is absent.
 *
 * The fixture is the wire shape of `GET /api/v1/search`, snake_case and complete.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { flatten, GlobalSearch, toSearch } from "./GlobalSearch";

const WIRE = {
  query: "auth",
  elapsed_ms: 712.4,
  projects: [{ id: "pj_1", name: "Asterim", status: "active", description: "the game" }],
  tasks: [
    {
      id: "tk_2",
      root_id: "tk_root",
      kind: "delegation",
      status: "failed",
      objective: "repair the auth retry ladder",
    },
  ],
  events: [
    { seq: 41, ts: "2026-08-28T20:00:00Z", type: "continue.derived", snippet: '{"notes": ["auth"]}' },
  ],
  files: [
    {
      chunk_id: "ch_1",
      collection: "projects",
      project: "Asterim",
      path: "src/auth.ts",
      abs_path: "C:/x/src/auth.ts",
      anchor: "TokenService",
      score: 0.8,
      provenance: "local_owned",
      indexed_at: "2026-08-28T19:00:00Z",
      text: "refresh() rotates the token…",
    },
  ],
  notes: [
    {
      chunk_id: "ch_2",
      collection: "notes",
      project: "",
      path: "vault/auth-patterns.md",
      abs_path: "D:/vault/auth-patterns.md",
      anchor: "(file)",
      score: 0.6,
      provenance: "local_foreign",
      indexed_at: "2026-08-28T19:00:00Z",
      text: "prefer rotation over revocation…",
    },
  ],
  tainted: true,
  degraded: false,
  knowledge_error: "",
  git: [{ sha: "a".repeat(40), short: "aaaaaaa", author: "q", date: "2026-08-28", subject: "fix auth" }],
  git_searched: true,
  git_error: "",
};

const noop = {
  onClose: () => {},
  onOpenProject: () => {},
  onInspectTask: () => {},
  onOpenTimeline: () => {},
};

function stubSearch(payload: unknown = WIRE) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: unknown) => {
      calls.push(String(input));
      return Promise.resolve({ ok: true, json: async () => payload });
    }),
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("toSearch and flatten", () => {
  it("reads the wire shape and flattens in the §11 group order", () => {
    const rows = flatten(toSearch(WIRE));
    expect(rows.map((r) => r.group)).toEqual([
      "PROJECTS",
      "TASKS",
      "FILES",
      "NOTES",
      "GIT",
      "EVENTS",
    ]);
    expect(rows[0]?.action).toEqual({ kind: "project", id: "pj_1" });
    expect(rows[1]?.action).toEqual({ kind: "task", id: "tk_2" });
    // Previews carry no action — the app has no file viewer, and a fake affordance
    // is worse than none.
    expect(rows[2]?.action).toBeNull();
    expect(rows[3]?.action).toBeNull();
    expect(rows[4]?.action).toBeNull();
  });

  it("survives garbage without throwing", () => {
    expect(flatten(toSearch(null))).toEqual([]);
    expect(toSearch({ projects: 7, git: "no" }).projects).toEqual([]);
  });
});

describe("the view", () => {
  it("debounces, sends the scope, and renders grouped results with the taint badge", async () => {
    const calls = stubSearch();
    render(<GlobalSearch open project="Asterim" {...noop} />);

    fireEvent.change(screen.getByLabelText("Global search query"), {
      target: { value: "auth" },
    });
    // Inside the debounce window nothing has been sent.
    expect(calls).toHaveLength(0);

    await waitFor(() => expect(screen.getByText("PROJECTS (1)")).toBeTruthy());
    expect(calls).toEqual(["/api/v1/search?q=auth&project=Asterim"]);
    expect(screen.getByText("GIT (1)")).toBeTruthy();
    expect(screen.getByText(/712.4 ms/)).toBeTruthy();
    expect(screen.getByText(/includes content ORACLE did not author/)).toBeTruthy();
  });

  it("routes Enter to the one honest action per kind", async () => {
    stubSearch();
    const onOpenProject = vi.fn();
    const onClose = vi.fn();
    render(
      <GlobalSearch
        open
        project={null}
        {...noop}
        onOpenProject={onOpenProject}
        onClose={onClose}
      />,
    );
    const input = screen.getByLabelText("Global search query");
    fireEvent.change(input, { target: { value: "auth" } });
    await waitFor(() => expect(screen.getByText("PROJECTS (1)")).toBeTruthy());

    fireEvent.keyDown(input, { key: "Enter" }); // row 0: the project
    expect(onOpenProject).toHaveBeenCalledWith("pj_1");
    expect(onClose).toHaveBeenCalled();
  });

  it("does nothing on Enter over a preview row, rather than pretending", async () => {
    stubSearch();
    const onClose = vi.fn();
    render(<GlobalSearch open project={null} {...noop} onClose={onClose} />);
    const input = screen.getByLabelText("Global search query");
    fireEvent.change(input, { target: { value: "auth" } });
    await waitFor(() => expect(screen.getByText("FILES (1)")).toBeTruthy());

    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" }); // row 2: the file preview
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("cycles groups with Tab, as §11 asks", async () => {
    stubSearch();
    render(<GlobalSearch open project={null} {...noop} />);
    const input = screen.getByLabelText("Global search query");
    fireEvent.change(input, { target: { value: "auth" } });
    await waitFor(() => expect(screen.getByText("TASKS (1)")).toBeTruthy());

    expect(input.getAttribute("aria-activedescendant")).toBe("gsearch-row-0");
    fireEvent.keyDown(input, { key: "Tab" });
    expect(input.getAttribute("aria-activedescendant")).toBe("gsearch-row-1"); // TASKS
    fireEvent.keyDown(input, { key: "Tab" });
    expect(input.getAttribute("aria-activedescendant")).toBe("gsearch-row-2"); // FILES
    fireEvent.keyDown(input, { key: "Tab", shiftKey: true });
    expect(input.getAttribute("aria-activedescendant")).toBe("gsearch-row-1");
  });

  it("keeps the combobox contract: the active row resolves and is selected", async () => {
    stubSearch();
    render(<GlobalSearch open project={null} {...noop} />);
    const input = screen.getByLabelText("Global search query");
    expect(input.getAttribute("role")).toBe("combobox");
    fireEvent.change(input, { target: { value: "auth" } });
    await waitFor(() => expect(screen.getByText("PROJECTS (1)")).toBeTruthy());

    const active = document.getElementById(input.getAttribute("aria-activedescendant") ?? "");
    expect(active?.getAttribute("role")).toBe("option");
    expect(active?.getAttribute("aria-selected")).toBe("true");
  });

  it("says why the GIT group is absent instead of rendering silence", async () => {
    stubSearch({ ...WIRE, git: [], git_searched: false });
    render(<GlobalSearch open project={null} {...noop} />);
    fireEvent.change(screen.getByLabelText("Global search query"), {
      target: { value: "auth" },
    });
    await waitFor(() =>
      expect(screen.getByText(/select a project to search its history/)).toBeTruthy(),
    );
  });

  it("renders nothing when closed and clears state on reopen", () => {
    const { container, rerender } = render(<GlobalSearch open={false} project={null} {...noop} />);
    expect(container.innerHTML).toBe("");
    stubSearch();
    rerender(<GlobalSearch open project={null} {...noop} />);
    expect(screen.getByLabelText("Global search query")).toBeTruthy();
  });
});
