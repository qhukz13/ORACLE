/**
 * The knowledge graph view.
 *
 * happy-dom has no canvas and no layout engine, so what is testable here is deliberately not
 * "does it draw" — it is everything the drawing depends on and everything a person can reach
 * without a mouse. Those are also the parts that can be wrong while the picture still looks fine:
 * a list that silently omits documents, a stale map that never says it is stale, a "reach" answer
 * computed off the wrong edge set.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { KnowledgeGraph, toTraces } from "./KnowledgeGraph";
import type { KnowledgeGraphData, RetrievalTrace } from "./KnowledgeGraph";

function node(
  rel: string,
  over: Partial<KnowledgeGraphData["nodes"][number]> = {},
): KnowledgeGraphData["nodes"][number] {
  // A real document id is `collection/rel_path` — the same string a citation reconstructs to.
  // A fixture that used the bare name would let a trace-matching bug pass here and fail live.
  const collection = over.collection ?? "notes";
  return {
    id: `${collection}/${rel}`,
    collection,
    project: null,
    rel_path: rel,
    kind: "markdown",
    state: "placed",
    degree: 1,
    indexed_at: "2026-09-09T00:00:00Z",
    ...over,
  };
}

function data(over: Partial<KnowledgeGraphData> = {}): KnowledgeGraphData {
  return {
    built: true,
    nodes: [node("a.md"), node("b.md"), node("c.md", { degree: 0 })],
    x: [0, 1, 2],
    y: [0, 1, 2],
    placed: ["layout", "layout", "layout"],
    explicit_edges: [0, 1],
    semantic_edges: [],
    stats: {
      documents: 3,
      explicit_edges: 1,
      semantic_edges: 0,
      orphans: 1,
      unplaced: 0,
      edge_model: { k: 4, threshold: 0.85 },
    },
    ...over,
  };
}

describe("KnowledgeGraph", () => {
  it("says so rather than drawing an empty map when there is no index", () => {
    render(<KnowledgeGraph data={{ ...data(), built: false }} />);
    expect(screen.getByText(/No knowledge index yet/)).toBeTruthy();
  });

  it("lists every document beside the canvas, because the list is the equivalent", () => {
    render(<KnowledgeGraph data={data()} />);
    for (const name of ["a.md", "b.md", "c.md"]) {
      expect(screen.getByTitle(name)).toBeTruthy();
    }
  });

  it("gives the canvas a label that points at the list rather than announcing 'canvas'", () => {
    render(<KnowledgeGraph data={data()} />);
    const img = screen.getByRole("img");
    expect(img.getAttribute("aria-label")).toContain("3 documents");
    expect(img.getAttribute("aria-label")).toContain("list beside it");
  });

  it("the orphan filter finds the document connected to nothing", () => {
    render(<KnowledgeGraph data={data()} />);
    fireEvent.click(screen.getByRole("button", { name: "Orphans" }));
    expect(screen.queryByTitle("a.md")).toBeNull();
    expect(screen.getByTitle("c.md")).toBeTruthy();
  });

  it("counts an unembeddable document as neither orphan nor failure in the list", () => {
    /* Config has no vector by policy. Listing it under "orphans" would give the neglect
       question a permanent floor of things that are not actually neglected. */
    render(
      <KnowledgeGraph
        data={data({
          nodes: [node("a.md"), node("tsconfig.json", { degree: 0, state: "unembeddable" })],
          x: [0, 1],
          y: [0, 1],
          placed: ["layout", "layout"],
          explicit_edges: [],
          semantic_edges: [],
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Orphans" }));
    expect(screen.queryByTitle("tsconfig.json")).toBeNull();
  });

  it("offers a re-layout only when the map has actually drifted, and says what it costs", () => {
    /* OQ-22 measurement 4: incremental placement is stable but loses fidelity, so the fix is
       prompted rather than buried — and never nagged when there is nothing to fix. */
    const { rerender } = render(<KnowledgeGraph data={data()} />);
    expect(screen.queryByRole("button", { name: /Re-layout/ })).toBeNull();

    rerender(
      <KnowledgeGraph data={data({ stats: { ...data().stats, unplaced: 4 } })} />,
    );
    expect(screen.getByRole("button", { name: "Re-layout" })).toBeTruthy();
    expect(screen.getByText(/moves everything/)).toBeTruthy();
  });

  it("holds the re-layout button disabled while the 28-second pass runs", () => {
    const onRelayout = vi.fn();
    render(
      <KnowledgeGraph
        data={data({ stats: { ...data().stats, unplaced: 4 } })}
        relayouting
        onRelayout={onRelayout}
      />,
    );
    const button = screen.getByRole("button", { name: "Re-laying out…" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
    expect(onRelayout).not.toHaveBeenCalled();
  });

  it("selecting from the list reports the two-hop neighbourhood — the reach question", () => {
    render(
      <KnowledgeGraph
        data={data({
          nodes: [node("a.md"), node("b.md"), node("c.md")],
          explicit_edges: [0, 1, 1, 2],
          stats: { ...data().stats, explicit_edges: 2 },
        })}
      />,
    );
    fireEvent.click(screen.getByTitle("a.md"));
    // a—b—c: one direct neighbour, one at two hops. Getting this off the wrong edge set is
    // invisible in the picture and wrong in the answer.
    expect(screen.getByText(/1 direct, 1 at two hops/)).toBeTruthy();
  });

  it("the path filter narrows the list", () => {
    render(<KnowledgeGraph data={data()} />);
    fireEvent.change(screen.getByLabelText("Filter documents by path"), {
      target: { value: "b.md" },
    });
    expect(screen.getByTitle("b.md")).toBeTruthy();
    expect(screen.queryByTitle("a.md")).toBeNull();
  });

  it("every action in the footer is reachable as a real button", () => {
    const onOpen = vi.fn();
    render(<KnowledgeGraph data={data()} onOpen={onOpen} />);
    fireEvent.click(screen.getByTitle("a.md"));
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ rel_path: "a.md" }));
  });

  describe("the use question — what was retrieved, and from where", () => {
    const trace = (over: Partial<RetrievalTrace> = {}): RetrievalTrace => ({
      id: "42",
      at: "2026-09-09T12:00:00Z",
      tool: "know.search",
      query: "how does taint work",
      documents: ["notes/a.md", "notes/b.md"],
      ...over,
    });

    it("shows nothing at all when nothing has been retrieved", () => {
      /* An empty trace bar is a permanent advertisement for a feature rather than an answer. */
      render(<KnowledgeGraph data={data()} traces={[]} />);
      expect(screen.queryByText("Retrievals:")).toBeNull();
    });

    it("names the retrieval and reports how many sources it lit up", () => {
      render(<KnowledgeGraph data={data()} traces={[trace()]} />);
      fireEvent.click(screen.getByRole("button", { name: /how does taint work/ }));
      expect(screen.getByText(/Showing the 2 sources behind/)).toBeTruthy();
    });

    it("says when a cited document has left the index instead of quietly dropping it", () => {
      /* A source that is gone is a real thing to know while you are checking the answer it
         supported — silently drawing one fewer node would make the answer look better sourced
         than it now is. */
      render(
        <KnowledgeGraph
          data={data()}
          traces={[trace({ documents: ["notes/a.md", "notes/deleted.md"] })]}
        />,
      );
      fireEvent.click(screen.getByRole("button", { name: /how does taint work/ }));
      expect(screen.getByText(/1 cited document is no longer in the index/)).toBeTruthy();
    });

    it("toggles off when the same retrieval is clicked again", () => {
      render(<KnowledgeGraph data={data()} traces={[trace()]} />);
      const button = screen.getByRole("button", { name: /how does taint work/ });
      fireEvent.click(button);
      expect(button.getAttribute("aria-pressed")).toBe("true");
      fireEvent.click(button);
      expect(button.getAttribute("aria-pressed")).toBe("false");
      expect(screen.queryByText(/Showing the/)).toBeNull();
    });
  });

  describe("toTraces — reading retrievals out of the event log", () => {
    /* The payload here is the exact shape `rag/retrieval.py:to_citation` emits, field names and
       all. A fixture that invented its own key names would let a rename pass the suite and break
       the feature live, which is the only way this derivation can fail. */
    const finished = (seq: number, rows: Record<string, unknown>[]) => ({
      v: 1,
      seq,
      ts: "2026-09-09T12:00:00Z",
      type: "tool.finished",
      trace_id: "tr_1",
      payload: { tool: "know.search", summary: "taint tracking", citations: rows },
    });
    const row = (collection: string, path: string) => ({
      chunk_id: `ch_${path}`,
      collection,
      project: "ORACLE",
      path,
      abs_path: `C:/Projects/${path}`,
      anchor: "§6",
      score: 0.8,
      provenance: "local_owned",
      indexed_at: "2026-09-09T00:00:00Z",
    });

    it("reconstructs the graph's document id from collection and path", () => {
      const [trace] = toTraces([
        finished(7, [row("projects", "ORACLE/docs/SECURITY.md")]),
      ] as never);
      expect(trace?.documents).toEqual(["projects/ORACLE/docs/SECURITY.md"]);
    });

    it("ignores tool calls that cited nothing", () => {
      expect(toTraces([finished(7, [])] as never)).toEqual([]);
    });

    it("counts a document cited twice in one answer as one source", () => {
      const [trace] = toTraces([
        finished(7, [row("notes", "a.md"), row("notes", "a.md")]),
      ] as never);
      expect(trace?.documents).toEqual(["notes/a.md"]);
    });

    it("returns the most recent retrieval first", () => {
      const traces = toTraces([
        finished(1, [row("notes", "old.md")]),
        finished(2, [row("notes", "new.md")]),
      ] as never);
      expect(traces.map((t) => t.documents[0])).toEqual(["notes/new.md", "notes/old.md"]);
    });
  });

  describe("collection is never carried by colour alone", () => {
    it("writes each collection's name and count in a legend", () => {
      /* UI.md §1. The swatch is decoration; the name is the information. */
      render(
        <KnowledgeGraph
          data={data({
            nodes: [node("a.md"), node("b.md", { collection: "projects" })],
            x: [0, 1],
            y: [0, 1],
            placed: ["layout", "layout"],
            explicit_edges: [],
            semantic_edges: [],
          })}
        />,
      );
      const legend = screen.getByRole("list", { name: "Collections" });
      expect(legend.textContent).toContain("notes");
      expect(legend.textContent).toContain("projects");
    });

    it("names the collection in each list row, not just its swatch", () => {
      render(<KnowledgeGraph data={data()} />);
      expect(screen.getByTitle("a.md").textContent).toContain("notes");
    });
  });

  describe("select-as-context", () => {
    it("offers nothing until documents are ticked", () => {
      render(<KnowledgeGraph data={data()} onPin={vi.fn()} />);
      const button = screen.getByRole("button", { name: "Use as context" });
      expect((button as HTMLButtonElement).disabled).toBe(true);
    });

    it("sends exactly the ticked documents, by id", () => {
      const onPin = vi.fn();
      render(<KnowledgeGraph data={data()} onPin={onPin} />);
      fireEvent.click(screen.getByLabelText("Use a.md as context"));
      fireEvent.click(screen.getByRole("button", { name: "Use as context" }));
      // Ids, not paths: the backend resolves these against the index, and a path would be a
      // file read of something that may never have been indexed or scoped.
      expect(onPin).toHaveBeenCalledWith(["notes/a.md"]);
    });

    it("says what is pinned rather than only highlighting it", () => {
      /* Context that steers answers invisibly is the thing this surface exists to expose. */
      render(<KnowledgeGraph data={data()} pinned={["notes/a.md"]} onPin={vi.fn()} />);
      expect(screen.getByText(/1 pinned for this conversation/)).toBeTruthy();
    });

    it("clearing sends an empty list, so the pin is replaced and not merely hidden", () => {
      const onPin = vi.fn();
      render(<KnowledgeGraph data={data()} pinned={["notes/a.md"]} onPin={onPin} />);
      fireEvent.click(screen.getByRole("button", { name: "Clear context" }));
      expect(onPin).toHaveBeenCalledWith([]);
    });

    it("shows no pinning affordance at all when the host does not support it", () => {
      render(<KnowledgeGraph data={data()} />);
      expect(screen.queryByRole("button", { name: "Use as context" })).toBeNull();
      expect(screen.queryByLabelText("Use a.md as context")).toBeNull();
    });
  });

  it("caps the rendered list and says how much it is hiding rather than truncating silently", () => {
    const many = Array.from({ length: 450 }, (_, i) => node(`doc${i}.md`));
    render(
      <KnowledgeGraph
        data={data({
          nodes: many,
          x: many.map(() => 0),
          y: many.map(() => 0),
          placed: many.map(() => "layout"),
          explicit_edges: [],
          semantic_edges: [],
          stats: { ...data().stats, documents: 450 },
        })}
      />,
    );
    expect(screen.getByText(/50 more — narrow the filter/)).toBeTruthy();
  });
});
