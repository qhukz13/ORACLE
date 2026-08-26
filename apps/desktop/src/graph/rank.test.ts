/**
 * Column assignment, including the property that makes it worth writing at all.
 *
 * The central claim is not "it produces columns" — a shortest-path walk does that in three
 * lines. It is that **column N means "cannot start before N stages have completed"**, and
 * that is a property over every edge rather than an assertion about one example graph. So
 * it is checked as one: for any graph, every node sits strictly right of everything it
 * depends on.
 */

import { describe, expect, it } from "vitest";

import { depth, rankByLongestPath, width, type RankedNode } from "./rank";

interface Node {
  id: string;
  deps: string[];
}

const opts = { id: (n: Node) => n.id, dependsOn: (n: Node) => n.deps };
const rank = (nodes: Node[]) => rankByLongestPath(nodes, opts);
const columns = (r: RankedNode<Node>[]) => Object.fromEntries(r.map((x) => [x.node.id, x.column]));

describe("longest path, not shortest", () => {
  it("puts a node after the longest chain that reaches it", () => {
    // A → B → D, A → C → D. `D` is two stages deep even though one route reaches it in one.
    const r = rank([
      { id: "A", deps: [] },
      { id: "B", deps: ["A"] },
      { id: "C", deps: ["A"] },
      { id: "D", deps: ["B", "C"] },
    ]);
    expect(columns(r)).toEqual({ A: 0, B: 1, C: 1, D: 2 });
  });

  it("does not claim a parallelism the graph does not have", () => {
    // The case UI.md §6b names. Shortest-path would put D in column 1 beside B, which
    // reads as "B and D run together". D waits on B; they never do.
    const r = rank([
      { id: "A", deps: [] },
      { id: "B", deps: ["A"] },
      { id: "C", deps: ["A"] },
      { id: "D", deps: ["C", "B"] },
    ]);
    const c = columns(r);
    expect(c["D"]).toBeGreaterThan(c["B"]!);
    expect(c["D"]).toBeGreaterThan(c["C"]!);
  });

  it("a graph with no edges is one column, which is the common case", () => {
    // Five of twelve valid plans in the P6-T5 spike declared no dependencies at all.
    const r = rank([
      { id: "A", deps: [] },
      { id: "B", deps: [] },
      { id: "C", deps: [] },
    ]);
    expect(depth(r)).toBe(1);
    expect(width(r)).toBe(3);
  });

  it("a chain is one node per column", () => {
    const r = rank([
      { id: "A", deps: [] },
      { id: "B", deps: ["A"] },
      { id: "C", deps: ["B"] },
    ]);
    expect(depth(r)).toBe(3);
    expect(width(r)).toBe(1);
  });
});

describe("the property that makes it correct", () => {
  /** A deterministic pseudo-random DAG: node i may only depend on nodes before it, which
   *  makes acyclicity structural rather than something the generator has to check. */
  function randomDag(n: number, seed: number): Node[] {
    let x = seed;
    const next = () => (x = (x * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
    return Array.from({ length: n }, (_, i) => ({
      id: `n${i}`,
      deps: Array.from({ length: i }, (_, j) => `n${j}`).filter(() => next() < 0.25),
    }));
  }

  it("every node sits strictly right of everything it depends on, over 200 graphs", () => {
    for (let seed = 1; seed <= 200; seed++) {
      const nodes = randomDag(12, seed);
      const c = columns(rank(nodes));
      for (const node of nodes) {
        for (const dep of node.deps) {
          expect(
            c[node.id],
            `${node.id} (col ${c[node.id]}) must be right of ${dep} (col ${c[dep]}), seed ${seed}`,
          ).toBeGreaterThan(c[dep]!);
        }
      }
    }
  });

  it("column order does not depend on the order rows arrived in", () => {
    // A graph is folded from an event stream, so rows arrive in whatever order the
    // scheduler dispatched them. The picture must not depend on that.
    const nodes = randomDag(10, 42);
    const forward = columns(rank(nodes));
    const backward = columns(rank([...nodes].reverse()));
    expect(backward).toEqual(forward);
  });
});

describe("what it does with graphs it should never see", () => {
  it("treats a dependency on an unknown task as a root, rather than dropping the node", () => {
    // The client folds an event stream; a row can reference a task not yet folded. Dropping
    // it would turn a missing *edge* into a missing *task*, which is a worse lie.
    const r = rank([{ id: "B", deps: ["A-not-here"] }]);
    expect(columns(r)).toEqual({ B: 0 });
  });

  it("terminates on a cycle instead of hanging", () => {
    // `orchestration/graph.py` refuses a cycle before anything runs, so this cannot arrive
    // from ORACLE's own scheduler. But this function is handed whatever the wire produced,
    // and a UI that hangs is worse than one that draws a cycle oddly.
    const r = rank([
      { id: "A", deps: ["B"] },
      { id: "B", deps: ["A"] },
    ]);
    expect(r).toHaveLength(2);
    expect(r.every((x) => Number.isFinite(x.column))).toBe(true);
  });

  it("is empty for an empty graph", () => {
    expect(rank([])).toEqual([]);
    expect(depth([])).toBe(0);
    expect(width([])).toBe(0);
  });
});
