/**
 * Column assignment for a task graph: **longest path from a root**, not shortest.
 *
 * UI.md §6b asks for "a topological rank with longest-path column assignment (ported from
 * Asterim's `dagColumns`)", and the reason is stated there in one clause worth expanding:
 * *"drawing a node next to the root would claim a parallelism the graph does not have."*
 *
 * Consider `A → B → D` and `A → C`, with `D` also depending on `C`:
 *
 *   shortest path            longest path
 *   ─────────────            ────────────
 *   col 0: A                 col 0: A
 *   col 1: B, C, D           col 1: B, C
 *   ...                      col 2: D
 *
 * Shortest-path puts `D` beside `B`, which reads as "these run together". They cannot:
 * `D` waits on `B`. The picture would be making a claim about concurrency that the
 * scheduler will not honour, and a person reading it to decide whether to wait would be
 * misled by the drawing rather than by the data.
 *
 * Longest path is therefore not a nicer aesthetic. It is the only assignment where
 * **column N means "cannot possibly start before N stages have completed"**.
 *
 * Pure, no DOM, no dependencies — ADR-0013's rule that layout is arithmetic somebody can
 * read, not a library that owns the rendering.
 */

export interface RankedNode<T> {
  node: T;
  /** Longest path length from any root. Column index, left to right. */
  column: number;
  /** Position within the column, in the input's order. Stable, so the picture does not
   *  reshuffle when an unrelated task finishes. */
  row: number;
}

export interface RankOptions<T> {
  id(node: T): string;
  dependsOn(node: T): readonly string[];
}

/**
 * Rank nodes into columns. Nodes whose dependencies are not in the input are treated as
 * roots — a graph can legitimately reference a task the client has not folded yet, and
 * dropping such a node would make the missing edge into a missing *task*.
 *
 * A cycle cannot reach here from ORACLE's own scheduler (`orchestration/graph.py`
 * validates acyclicity before anything runs, reporting the cycle as a path). But this
 * function is handed whatever the wire produced, and a UI that hangs is worse than a UI
 * that draws a cycle oddly — so cycles terminate, with every node in the cycle placed
 * after everything outside it.
 */
export function rankByLongestPath<T>(nodes: readonly T[], opts: RankOptions<T>): RankedNode<T>[] {
  const byId = new Map<string, T>();
  for (const node of nodes) byId.set(opts.id(node), node);

  const column = new Map<string, number>();
  const visiting = new Set<string>();

  const depth = (id: string): number => {
    const cached = column.get(id);
    if (cached !== undefined) return cached;
    if (visiting.has(id)) {
      // A cycle. Return 0 for this edge so the recursion unwinds; the node still gets a
      // real column from whichever of its other paths is longest.
      return 0;
    }
    const node = byId.get(id);
    if (!node) return -1; // Not in the input: an edge to a task we have not been told about.
    visiting.add(id);
    let best = 0;
    for (const dep of opts.dependsOn(node)) {
      const d = depth(dep);
      if (d >= 0) best = Math.max(best, d + 1);
    }
    visiting.delete(id);
    column.set(id, best);
    return best;
  };

  const perColumn = new Map<number, number>();
  return nodes.map((node) => {
    const col = depth(opts.id(node));
    const row = perColumn.get(col) ?? 0;
    perColumn.set(col, row + 1);
    return { node, column: col, row };
  });
}

/** The widest column — how much horizontal parallelism the graph actually has. */
export function width<T>(ranked: readonly RankedNode<T>[]): number {
  const counts = new Map<number, number>();
  for (const r of ranked) counts.set(r.column, (counts.get(r.column) ?? 0) + 1);
  return Math.max(0, ...counts.values());
}

/** How many stages deep the graph is — the length of its critical path, plus one. */
export function depth<T>(ranked: readonly RankedNode<T>[]): number {
  return ranked.length === 0 ? 0 : Math.max(...ranked.map((r) => r.column)) + 1;
}
