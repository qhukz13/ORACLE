/**
 * The knowledge graph — docs/UI.md §11b, decision ADR-0023, budgets OQ-22.
 *
 * A map of every indexed document: collection-coloured, positioned by a force layout that ran
 * **offline** and froze. Three things this component deliberately does not do, each of them a
 * measured or decided constraint rather than a preference:
 *
 * 1. **It never simulates.** Positions arrive frozen from the API and are rendered as given. The
 *    canvas is drawn on demand — one coalesced `requestAnimationFrame` per change — never in a
 *    standing loop. That is what keeps idle CPU at the measured 1.09% of a core instead of burning
 *    a core to say nothing, which is ADR-0013's original objection and ADR-0023's inherited budget.
 * 2. **It does not lay out on read, and does not hide that the map is stale.** Documents indexed
 *    since the last layout arrive placed at their neighbours' centroid, and that degrades —
 *    Jaccard@10 0.477 after a 5% holdout against a 0.70 gate. So the count is shown and a re-layout
 *    is *offered*. OQ-22 measurement 4's finding was that re-layout must be prompted, not buried.
 * 3. **It answers three questions, not four.** Shape, neglect, reach. *Bridges* was struck by
 *    measurement 3b: across every k and every threshold this corpus holds exactly one edge joining
 *    notes to projects, because the notes are ML prose and the projects are code. A view that
 *    reliably finds one bridge is a sentence, not a feature.
 *
 * **The list beside the canvas is not a fallback, it is the equivalent** — ADR-0023 pays for canvas
 * by owing it, and the orbit's rule is that every graph action exists in the list. It is built from
 * real `<button>`s rather than `role="option"` divs: the palette already carries a defect from
 * claiming that role without `combobox`/`aria-activedescendant` behind it, and the house rule from
 * TaskTree is to claim a role only with the behaviour attached. A native button needs no claim.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export interface GraphNodeData {
  id: string;
  collection: string;
  project: string | null;
  rel_path: string;
  kind: string;
  /** placed | unembeddable | failed — see `rag/graph.py`. */
  state: string;
  degree: number;
  indexed_at: string;
}

export interface KnowledgeGraphData {
  built: boolean;
  nodes: GraphNodeData[];
  x: number[];
  y: number[];
  /** "layout" | "incremental" | "" (unplaced). */
  placed: string[];
  explicit_edges: number[];
  semantic_edges: number[];
  stats: {
    documents: number;
    explicit_edges: number;
    semantic_edges: number;
    orphans: number;
    unplaced: number;
    edge_model: { k: number; threshold: number };
  };
}

export interface KnowledgeGraphProps {
  data: KnowledgeGraphData;
  /** True while a re-layout is running; the button holds its disabled state for the whole pass
   *  (34 s measured, plus a one-time 88 s vector backfill on an index built before that table). */
  relayouting?: boolean;
  onRelayout?(): void;
  onOpen?(node: GraphNodeData): void;
}

/** Constant node radius in CSS pixels. Measured free on canvas (OQ-22 #2) — nodes stay clickable
 *  at every zoom, which is the reason to want it. */
const NODE_R = 3;
const MIN_ZOOM = 0.4;
const MAX_ZOOM = 24;

/** Stable hue per collection: the same vault is the same colour next month (UI.md §11b). */
function hueOf(collection: string): number {
  let h = 0;
  for (let i = 0; i < collection.length; i++) h = (h * 31 + collection.charCodeAt(i)) >>> 0;
  return h % 360;
}

type Filter = "all" | "orphans" | "failed" | "unplaced";

const FILTERS: { id: Filter; label: string; hint: string }[] = [
  { id: "all", label: "Everything", hint: "every indexed document" },
  { id: "orphans", label: "Orphans", hint: "connected to nothing — the neglect question" },
  { id: "failed", label: "Failed", hint: "indexed with a parse error" },
  { id: "unplaced", label: "Moved since layout", hint: "placed incrementally, not settled" },
];

export function KnowledgeGraph({
  data,
  relayouting,
  onRelayout,
  onOpen,
}: KnowledgeGraphProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [camera, setCamera] = useState({ cx: 0, cy: 0, zoom: 1 });
  const [selected, setSelected] = useState<number | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [size, setSize] = useState({ w: 0, h: 0 });

  const n = data.nodes.length;

  // ---- adjacency, for the reach question (1 and 2 hops) --------------------
  const adjacency = useMemo(() => {
    const adj: number[][] = Array.from({ length: n }, () => []);
    const add = (flat: number[]) => {
      for (let i = 0; i < flat.length; i += 2) {
        const a = flat[i];
        const b = flat[i + 1];
        if (a === undefined || b === undefined) continue;
        adj[a]?.push(b);
        adj[b]?.push(a);
      }
    };
    add(data.explicit_edges);
    add(data.semantic_edges);
    return adj;
  }, [data.explicit_edges, data.semantic_edges, n]);

  /** Focus mode: the selected node, its neighbours, and their neighbours. Everything else recedes
   *  rather than disappearing — the shape of what you are *not* looking at is context. */
  const reach = useMemo(() => {
    if (selected === null) return null;
    const one = new Set<number>(adjacency[selected] ?? []);
    const two = new Set<number>();
    for (const m of one) for (const o of adjacency[m] ?? []) if (!one.has(o) && o !== selected) two.add(o);
    return { one, two };
  }, [selected, adjacency]);

  // ---- the fit transform ---------------------------------------------------
  const bbox = useMemo(() => {
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    for (let i = 0; i < n; i++) {
      const x = data.x[i] ?? 0;
      const y = data.y[i] ?? 0;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
    if (!Number.isFinite(minX)) return { cx: 0, cy: 0, w: 1, h: 1 };
    return { cx: (minX + maxX) / 2, cy: (minY + maxY) / 2, w: maxX - minX || 1, h: maxY - minY || 1 };
  }, [data.x, data.y, n]);

  const fit = useMemo(() => {
    if (!size.w || !size.h) return 1;
    return 0.88 * Math.min(size.w / bbox.w, size.h / bbox.h);
  }, [size, bbox]);

  useEffect(() => {
    setCamera((c) => (c.cx === 0 && c.cy === 0 ? { cx: bbox.cx, cy: bbox.cy, zoom: 1 } : c));
  }, [bbox]);

  // ---- resize --------------------------------------------------------------
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  const toScreen = useCallback(
    (i: number): [number, number] => {
      const s = fit * camera.zoom;
      return [
        ((data.x[i] ?? 0) - camera.cx) * s + size.w / 2,
        ((data.y[i] ?? 0) - camera.cy) * s + size.h / 2,
      ];
    },
    [camera, fit, size, data.x, data.y],
  );

  // ---- drawing, on demand only ---------------------------------------------
  const frame = useRef(0);
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || !size.w || !size.h) return;
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(size.w * dpr)) canvas.width = Math.round(size.w * dpr);
    if (canvas.height !== Math.round(size.h * dpr)) canvas.height = Math.round(size.h * dpr);
    const g = canvas.getContext("2d", { alpha: false });
    if (!g) return;

    const css = getComputedStyle(document.documentElement);
    const bg = css.getPropertyValue("--bg-0").trim() || "#0b0e14";
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.fillStyle = bg;
    g.fillRect(0, 0, size.w, size.h);

    const s = fit * camera.zoom;
    const ox = size.w / 2 - camera.cx * s;
    const oy = size.h / 2 - camera.cy * s;
    const px = (i: number) => (data.x[i] ?? 0) * s + ox;
    const py = (i: number) => (data.y[i] ?? 0) * s + oy;

    const dim = reach !== null;
    const inReach = (i: number) =>
      !dim || i === selected || reach.one.has(i) || reach.two.has(i);

    // Edges first, in one batched path per class. Semantic edges are drawn fainter and dashed:
    // an inferred similarity is a suggestion, and drawing it like a wikilink would state a fact
    // the index never established.
    const strokeEdges = (flat: number[], dashed: boolean, alpha: number) => {
      g.save();
      g.beginPath();
      for (let i = 0; i < flat.length; i += 2) {
        const a = flat[i];
        const b = flat[i + 1];
        if (a === undefined || b === undefined) continue;
        if (dim && !(inReach(a) && inReach(b))) continue;
        g.moveTo(px(a), py(a));
        g.lineTo(px(b), py(b));
      }
      if (dashed) g.setLineDash([2, 3]);
      g.strokeStyle = `rgba(154,165,184,${alpha})`;
      g.lineWidth = 1;
      g.stroke();
      g.restore();
    };
    strokeEdges(data.semantic_edges, true, dim ? 0.35 : 0.14);
    strokeEdges(data.explicit_edges, false, dim ? 0.7 : 0.34);

    // Nodes. Size carries degree, colour carries collection, and the two non-normal states are
    // shapes rather than shades — UI.md §1: never colour alone.
    for (let i = 0; i < n; i++) {
      const node = data.nodes[i];
      if (!node) continue;
      const x = px(i);
      const y = py(i);
      if (x < -20 || y < -20 || x > size.w + 20 || y > size.h + 20) continue;
      const focusedOut = dim && !inReach(i);
      const r = NODE_R + Math.min(3, Math.sqrt(node.degree));
      const hue = hueOf(node.collection);
      const light = node.state === "unembeddable" ? 35 : 68;
      const alpha = focusedOut ? 0.12 : node.state === "unembeddable" ? 0.5 : 1;

      g.beginPath();
      g.arc(x, y, r, 0, Math.PI * 2);
      if (node.state === "failed") {
        // Hollow: it is in the index and it is broken. A filled dot would read as healthy.
        g.strokeStyle = `hsla(0,80%,62%,${alpha})`;
        g.lineWidth = 1.5;
        g.stroke();
      } else {
        g.fillStyle = `hsla(${hue},58%,${light}%,${alpha})`;
        g.fill();
      }
      if (i === selected || i === hovered) {
        g.beginPath();
        g.arc(x, y, r + 3, 0, Math.PI * 2);
        g.strokeStyle = i === selected ? "#d8dee9" : "rgba(216,222,233,0.55)";
        g.lineWidth = 1.5;
        g.stroke();
      }
    }

    // Labels only above a zoom threshold, and only for what is focused — a thousand labels is a
    // wall of text, not a map.
    if (camera.zoom > 6 || selected !== null) {
      g.fillStyle = "#d8dee9";
      g.font = "11px ui-monospace, Menlo, Consolas, monospace";
      for (let i = 0; i < n; i++) {
        const node = data.nodes[i];
        if (!node) continue;
        const show = selected !== null ? i === selected || reach?.one.has(i) : camera.zoom > 6;
        if (!show) continue;
        const x = px(i);
        const y = py(i);
        if (x < 0 || y < 0 || x > size.w || y > size.h) continue;
        const name = node.rel_path.split("/").pop() ?? node.rel_path;
        g.fillText(name, x + 7, y + 3);
      }
    }
  }, [camera, fit, size, data, n, selected, hovered, reach]);

  /** One coalesced frame per change. Never a standing loop: the idle budget is the whole reason
   *  positions are frozen in the first place. */
  useEffect(() => {
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame.current);
  }, [draw]);

  // ---- picking: a linear scan, measured faster than the DOM's hit-test tree ----
  const pick = useCallback(
    (mx: number, my: number): number | null => {
      let best: number | null = null;
      let bestD = Infinity;
      for (let i = 0; i < n; i++) {
        const [x, y] = toScreen(i);
        const d = (x - mx) ** 2 + (y - my) ** 2;
        if (d < 100 && d < bestD) {
          bestD = d;
          best = i;
        }
      }
      return best;
    },
    [n, toScreen],
  );

  const drag = useRef<{ x: number; y: number; cx: number; cy: number } | null>(null);

  const onPointerDown = (e: React.PointerEvent) => {
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, cx: camera.cx, cy: camera.cy };
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const rect = e.currentTarget.getBoundingClientRect();
    if (drag.current) {
      const s = fit * camera.zoom;
      setCamera((c) => ({
        ...c,
        cx: drag.current!.cx - (e.clientX - drag.current!.x) / s,
        cy: drag.current!.cy - (e.clientY - drag.current!.y) / s,
      }));
      return;
    }
    setHovered(pick(e.clientX - rect.left, e.clientY - rect.top));
  };
  const onPointerUp = (e: React.PointerEvent) => {
    const moved =
      drag.current &&
      Math.hypot(e.clientX - drag.current.x, e.clientY - drag.current.y) > 3;
    drag.current = null;
    if (moved) return;
    const rect = e.currentTarget.getBoundingClientRect();
    setSelected(pick(e.clientX - rect.left, e.clientY - rect.top));
  };
  const onWheel = (e: React.WheelEvent) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    setCamera((c) => {
      const next = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, c.zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
      // Zoom about the cursor, not the centre: zooming about the centre walks the thing you were
      // looking at off the screen, which is how a map stops being navigable.
      const s0 = fit * c.zoom;
      const s1 = fit * next;
      const wx = (mx - size.w / 2) / s0 + c.cx;
      const wy = (my - size.h / 2) / s0 + c.cy;
      return { zoom: next, cx: wx - (mx - size.w / 2) / s1, cy: wy - (my - size.h / 2) / s1 };
    });
  };

  const centreOn = useCallback(
    (i: number) => {
      setSelected(i);
      setCamera((c) => ({
        cx: data.x[i] ?? 0,
        cy: data.y[i] ?? 0,
        zoom: Math.max(c.zoom, 6),
      }));
    },
    [data.x, data.y],
  );

  // ---- the list equivalent -------------------------------------------------
  const listed = useMemo(() => {
    const q = query.trim().toLowerCase();
    const out: number[] = [];
    for (let i = 0; i < n; i++) {
      const node = data.nodes[i];
      if (!node) continue;
      if (filter === "orphans" && !(node.degree === 0 && node.state === "placed")) continue;
      if (filter === "failed" && node.state !== "failed") continue;
      if (filter === "unplaced" && data.placed[i] !== "incremental") continue;
      if (q && !node.rel_path.toLowerCase().includes(q)) continue;
      out.push(i);
    }
    return out;
  }, [data, n, filter, query]);

  const selectedNode = selected === null ? null : data.nodes[selected];

  if (!data.built) {
    return (
      <div className="empty">
        <p>No knowledge index yet.</p>
        <p className="muted">Build one from the Knowledge stage, then the map appears here.</p>
      </div>
    );
  }

  return (
    <div className="kgraph">
      <header className="kgraph-head">
        <div className="kgraph-stats">
          <strong>{data.stats.documents.toLocaleString()}</strong> documents ·{" "}
          <strong>{data.stats.explicit_edges.toLocaleString()}</strong> links ·{" "}
          <strong>{data.stats.semantic_edges.toLocaleString()}</strong> inferred (k=
          {data.stats.edge_model.k}, ≥{data.stats.edge_model.threshold}) ·{" "}
          <strong>{data.stats.orphans.toLocaleString()}</strong> orphans
        </div>
        <div className="kgraph-controls">
          <input
            type="search"
            value={query}
            placeholder="Filter by path…"
            aria-label="Filter documents by path"
            onChange={(e) => setQuery(e.target.value)}
          />
          {FILTERS.map((f) => (
            <button
              key={f.id}
              className={`ghost${filter === f.id ? " on" : ""}`}
              aria-pressed={filter === f.id}
              title={f.hint}
              onClick={() => setFilter(f.id)}
            >
              {f.label}
            </button>
          ))}
        </div>
      </header>

      {data.stats.unplaced > 0 && (
        /* Prompted, not buried — OQ-22 measurement 4. The map is still usable; it is just less
           faithful than a fresh layout, and the person gets to decide whether 28 seconds and a
           resettled mental map is worth it today. */
        <div className="kgraph-stale" role="status">
          <span>
            {data.stats.unplaced.toLocaleString()} document
            {data.stats.unplaced === 1 ? " has" : "s have"} no settled position yet.
          </span>
          <button className="ghost" disabled={relayouting} onClick={onRelayout}>
            {relayouting ? "Re-laying out…" : "Re-layout"}
          </button>
          {/* 34 s measured on the real corpus; the first run after an upgrade also pools document
              vectors, which was another 88 s. Saying "~30 s" flat would be wrong exactly once, on
              the run most likely to be someone's first impression of the button. */}
          <span className="muted">Takes ~30 s — longer the first time — and moves everything.</span>
        </div>
      )}

      <div className="kgraph-body">
        <div
          className="kgraph-canvas"
          ref={wrapRef}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerLeave={() => setHovered(null)}
          onWheel={onWheel}
        >
          {/* The canvas carries no semantics of its own — the list beside it is the accessible
              equivalent, and this label is what a screen reader should hear instead of "canvas". */}
          <canvas
            ref={canvasRef}
            role="img"
            aria-label={
              `Knowledge map: ${data.stats.documents} documents, ` +
              `${data.stats.explicit_edges + data.stats.semantic_edges} connections. ` +
              `The list beside it carries the same documents and every action.`
            }
            style={{ width: "100%", height: "100%", display: "block" }}
          />
          {hovered !== null && data.nodes[hovered] && (
            <div className="kgraph-tip" aria-hidden="true">
              {data.nodes[hovered]?.rel_path}
            </div>
          )}
        </div>

        <aside className="kgraph-list" aria-label="Documents">
          <p className="muted kgraph-count">
            {listed.length.toLocaleString()} shown
            {filter !== "all" && ` · ${FILTERS.find((f) => f.id === filter)?.label.toLowerCase()}`}
          </p>
          <ul>
            {listed.slice(0, 400).map((i) => {
              const node = data.nodes[i];
              if (!node) return null;
              return (
                <li key={node.id}>
                  <button
                    className={`kgraph-item${i === selected ? " sel" : ""}`}
                    onClick={() => centreOn(i)}
                    onDoubleClick={() => onOpen?.(node)}
                    onFocus={() => setHovered(i)}
                    onBlur={() => setHovered(null)}
                    title={node.rel_path}
                  >
                    <span
                      className="kgraph-swatch"
                      style={{ background: `hsl(${hueOf(node.collection)},58%,68%)` }}
                      aria-hidden="true"
                    />
                    <span className="kgraph-name">{node.rel_path.split("/").pop()}</span>
                    <span className="muted">
                      {node.degree === 0 ? "orphan" : `${node.degree} link${node.degree === 1 ? "" : "s"}`}
                      {node.state === "failed" && " · failed"}
                      {node.state === "unembeddable" && " · not embedded"}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          {listed.length > 400 && (
            <p className="muted">
              {(listed.length - 400).toLocaleString()} more — narrow the filter.
            </p>
          )}
        </aside>
      </div>

      {selectedNode && (
        <footer className="kgraph-sel" role="status">
          <strong>{selectedNode.rel_path}</strong>
          <span className="muted">
            {selectedNode.collection}
            {selectedNode.project && ` · ${selectedNode.project}`} · {selectedNode.kind} ·{" "}
            {reach ? `${reach.one.size} direct, ${reach.two.size} at two hops` : ""}
          </span>
          <button className="ghost" onClick={() => onOpen?.(selectedNode)}>
            Open
          </button>
          <button className="ghost" onClick={() => setSelected(null)}>
            Clear
          </button>
        </footer>
      )}
    </div>
  );
}
