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
import { toCitations } from "./Citations";
import { str } from "../protocol";
import type { OracleEvent } from "../protocol";

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

/** One retrieval, as the event log recorded it: the documents ORACLE cited together in one answer.
 *  Episodic and derived, never stored — UI.md §11b's "use" question is a reading of the event log,
 *  not another table. */
export interface RetrievalTrace {
  id: string;
  at: string;
  tool: string;
  query: string;
  /** Document ids (`collection/rel_path`), which is exactly how citations address a node. */
  documents: string[];
}

/**
 * Read retrievals out of the event log. **Derived, never stored.**
 *
 * Every `tool.finished` for a `know.*` call already carries its citations, and a citation's
 * `collection` + `path` reconstruct exactly the `collection/rel_path` id the graph addresses nodes
 * by. So the *use* question is answered by reading what the client already has; persisting a
 * second copy of it would be a table whose only job is to disagree with the log eventually.
 *
 * Newest first, because "what did ORACLE just retrieve" is the question, and de-duplicated per
 * retrieval because one answer citing the same document twice is one source, not two.
 */
export function toTraces(events: readonly OracleEvent[]): RetrievalTrace[] {
  const out: RetrievalTrace[] = [];
  for (const e of events) {
    if (e.type !== "tool.finished") continue;
    const citations = toCitations(e.payload.citations);
    if (citations.length === 0) continue;
    out.push({
      id: String(e.seq),
      at: e.ts,
      tool: str(e.payload.tool, "know.search"),
      query: str(e.payload.summary),
      documents: [...new Set(citations.map((c) => `${c.collection}/${c.path}`))],
    });
  }
  return out.reverse();
}

export interface KnowledgeGraphProps {
  data: KnowledgeGraphData;
  /** Most recent first. */
  traces?: RetrievalTrace[];
  /** Documents already pinned as this session's context, so the view opens showing the truth. */
  pinned?: string[];
  /** Replaces the pin. An empty list clears it. */
  onPin?(documents: string[]): void;
  /** True while a re-layout is running; the button holds its disabled state for the whole pass
   *  (34 s measured, plus a one-time 88 s vector backfill on an index built before that table). */
  relayouting?: boolean;
  onRelayout?(): void;
  onOpen?(node: GraphNodeData): void;
}

/** Constant node radius in CSS pixels. Measured free on canvas (OQ-22 #2) — nodes stay clickable
 *  at every zoom, which is the reason to want it. */
const NODE_R = 3;
/** How much of a collection counts as its core, by distance from the centroid. 0.75 keeps the
 *  cluster and drops the orphan ring; at 1.0 the hull is the whole map and means nothing. */
const CORE_QUANTILE = 0.75;
const MIN_ZOOM = 0.4;
const MAX_ZOOM = 24;

/** Stable hue per collection: the same vault is the same colour next month (UI.md §11b). */
function hueOf(collection: string): number {
  let h = 0;
  for (let i = 0; i < collection.length; i++) h = (h * 31 + collection.charCodeAt(i)) >>> 0;
  return h % 360;
}

/** Monotone chain. Small and exact, rather than a dependency: the input is at most a few thousand
 *  points and this runs once per data change, not per frame. */
function convexHull(points: [number, number][]): [number, number][] {
  const pts = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (pts.length < 3) return pts;
  const cross = (o: [number, number], a: [number, number], b: [number, number]) =>
    (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const build = (source: [number, number][]) => {
    const out: [number, number][] = [];
    for (const p of source) {
      while (out.length >= 2 && cross(out[out.length - 2]!, out[out.length - 1]!, p) <= 0) out.pop();
      out.push(p);
    }
    out.pop();
    return out;
  };
  return [...build(pts), ...build([...pts].reverse())];
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
  traces = [],
  pinned,
  relayouting,
  onRelayout,
  onOpen,
  onPin,
}: KnowledgeGraphProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [camera, setCamera] = useState({ cx: 0, cy: 0, zoom: 1 });
  const [selected, setSelected] = useState<number | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [traceId, setTraceId] = useState<string | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(() => new Set(pinned ?? []));

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

  const indexById = useMemo(() => {
    const m = new Map<string, number>();
    for (let i = 0; i < n; i++) {
      const node = data.nodes[i];
      if (node) m.set(node.id, i);
    }
    return m;
  }, [data.nodes, n]);

  const trace = useMemo(() => traces.find((t) => t.id === traceId) ?? null, [traces, traceId]);

  /** The retrieval's documents, as node indices. A cited document that is not in the graph is
   *  dropped rather than counted: it means the index moved on since the answer, and inventing a
   *  node for it would draw a source that is no longer there. */
  const traced = useMemo(() => {
    if (!trace) return null;
    const hit: number[] = [];
    for (const id of trace.documents) {
      const i = indexById.get(id);
      if (i !== undefined) hit.push(i);
    }
    return { set: new Set(hit), order: hit, missing: trace.documents.length - hit.length };
  }, [trace, indexById]);

  /**
   * A hull per collection, traced around its **dense core** rather than around every member.
   *
   * The first version hulled the whole collection and was useless on the real corpus: orphans sit
   * on an outer ring by design (that is the neglect answer), so the convex hull of a collection is
   * just the convex hull of the ring — a polygon covering the entire map, saying only "these
   * documents exist". §11b asks for a region behind each *cluster*, and with orphans on the rim a
   * collection is not a cluster.
   *
   * So outliers are dropped before hulling: everything past `CORE_QUANTILE` of the distance from
   * the collection's centroid. The hull then traces the cluster, and the orphans fall visibly
   * *outside* it — which is the same picture answering two of the three questions at once.
   */
  const hulls = useMemo(() => {
    const byCollection = new Map<string, [number, number][]>();
    for (let i = 0; i < n; i++) {
      const node = data.nodes[i];
      if (!node || data.placed[i] === "") continue;
      const pts = byCollection.get(node.collection) ?? [];
      pts.push([data.x[i] ?? 0, data.y[i] ?? 0]);
      byCollection.set(node.collection, pts);
    }
    const out: { collection: string; points: [number, number][] }[] = [];
    for (const [collection, pts] of byCollection) {
      if (pts.length < 8) continue;
      const cx = pts.reduce((a, q) => a + q[0], 0) / pts.length;
      const cy = pts.reduce((a, q) => a + q[1], 0) / pts.length;
      const withDistance = pts
        .map((q) => ({ q, d: Math.hypot(q[0] - cx, q[1] - cy) }))
        .sort((a, b) => a.d - b.d);
      const keep = withDistance
        .slice(0, Math.max(3, Math.floor(withDistance.length * CORE_QUANTILE)))
        .map((e) => e.q);
      if (keep.length >= 3) out.push({ collection, points: convexHull(keep) });
    }
    return out;
  }, [data.nodes, data.x, data.y, data.placed, n]);

  const collections = useMemo(() => {
    const counts = new Map<string, number>();
    for (const node of data.nodes) counts.set(node.collection, (counts.get(node.collection) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [data.nodes]);

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

    const dim = reach !== null || traced !== null;
    const inReach = (i: number) =>
      (reach === null || i === selected || reach.one.has(i) || reach.two.has(i)) &&
      (traced === null || traced.set.has(i));

    // Collection hulls, behind everything. A tinted region is a second, non-colour carrier for
    // "these belong together" — and at a glance it is the answer to the shape question, because
    // two hulls that barely overlap is what "the vault and the projects do not touch" looks like.
    for (const hull of hulls) {
      if (hull.points.length < 3) continue;
      g.beginPath();
      hull.points.forEach(([hx, hy], idx) => {
        const sx = hx * s + ox;
        const sy = hy * s + oy;
        if (idx === 0) g.moveTo(sx, sy);
        else g.lineTo(sx, sy);
      });
      g.closePath();
      const hue = hueOf(hull.collection);
      g.fillStyle = `hsla(${hue},55%,50%,0.05)`;
      g.fill();
      g.setLineDash([3, 4]);
      g.strokeStyle = `hsla(${hue},55%,62%,0.30)`;
      g.lineWidth = 1;
      g.stroke();
      g.setLineDash([]);
    }

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

    // The retrieval overlay: what ORACLE cited together in one answer. Drawn as a ring through the
    // cited documents rather than as more graph edges, because co-citation is not a property of
    // the corpus — it is a fact about one turn, and drawing it like a wikilink would state a
    // relationship the index never found.
    if (traced && traced.order.length > 1) {
      g.save();
      g.beginPath();
      traced.order.forEach((i, idx) => {
        const x = px(i);
        const y = py(i);
        if (idx === 0) g.moveTo(x, y);
        else g.lineTo(x, y);
      });
      g.closePath();
      g.setLineDash([4, 3]);
      g.strokeStyle = "rgba(34,211,238,0.75)";
      g.lineWidth = 1.5;
      g.stroke();
      g.restore();
      for (const i of traced.order) {
        g.beginPath();
        g.arc(px(i), py(i), NODE_R + 5, 0, Math.PI * 2);
        g.strokeStyle = "rgba(34,211,238,0.9)";
        g.lineWidth = 1.5;
        g.stroke();
      }
    }

    // Labels only above a zoom threshold, and only for what is focused — a thousand labels is a
    // wall of text, not a map.
    if (camera.zoom > 6 || selected !== null || traced !== null) {
      g.fillStyle = "#d8dee9";
      g.font = "11px ui-monospace, Menlo, Consolas, monospace";
      for (let i = 0; i < n; i++) {
        const node = data.nodes[i];
        if (!node) continue;
        const show =
          traced !== null
            ? traced.set.has(i)
            : selected !== null
              ? i === selected || reach?.one.has(i)
              : camera.zoom > 6;
        if (!show) continue;
        const x = px(i);
        const y = py(i);
        if (x < 0 || y < 0 || x > size.w || y > size.h) continue;
        const name = node.rel_path.split("/").pop() ?? node.rel_path;
        g.fillText(name, x + 7, y + 3);
      }
    }
  }, [camera, fit, size, data, n, selected, hovered, reach, traced, hulls]);

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

      {onPin && (
        <div className="kgraph-pin">
          <span className="muted">
            {chosen.size === 0
              ? "Tick documents below to use them as context."
              : `${chosen.size} document${chosen.size === 1 ? "" : "s"} selected.`}
          </span>
          <button
            className="ghost"
            disabled={chosen.size === 0}
            onClick={() => onPin([...chosen])}
          >
            Use as context
          </button>
          {(pinned?.length ?? 0) > 0 && (
            <>
              <span className="kgraph-pinned">
                {/* Stated, not implied by a highlight: context that silently steers answers is the
                    thing this whole surface exists to make visible. */}
                {pinned?.length} pinned for this conversation
              </span>
              <button
                className="ghost"
                onClick={() => {
                  setChosen(new Set());
                  onPin([]);
                }}
              >
                Clear context
              </button>
            </>
          )}
        </div>
      )}

      {traces.length > 0 && (
        /* UI.md §11b's *use* question: "what did ORACLE just retrieve, and from where". It is a
           reading of the event log, so it appears only when there is something to read — an empty
           trace bar would be a permanent reminder of a feature rather than an answer. */
        <div className="kgraph-traces">
          <span className="muted">Retrievals:</span>
          {traces.slice(0, 6).map((t) => (
            <button
              key={t.id}
              className={`ghost${t.id === traceId ? " on" : ""}`}
              aria-pressed={t.id === traceId}
              title={`${t.tool} · ${t.documents.length} sources · ${t.at}`}
              onClick={() => {
                setTraceId(t.id === traceId ? null : t.id);
                setSelected(null);
              }}
            >
              {t.query || t.tool} <span className="muted">{t.documents.length}</span>
            </button>
          ))}
          {trace && (
            <button className="ghost" onClick={() => setTraceId(null)}>
              Clear trace
            </button>
          )}
        </div>
      )}

      {trace && traced && (
        <div className="kgraph-stale" role="status">
          <span>
            Showing the {traced.order.length} source
            {traced.order.length === 1 ? "" : "s"} behind “{trace.query || trace.tool}”.
          </span>
          {traced.missing > 0 && (
            /* Not hidden and not rounded away: a source that has left the index since the answer
               was given is a real thing to know when you are checking that answer. */
            <span className="muted">
              {traced.missing} cited document{traced.missing === 1 ? " is" : "s are"} no longer in
              the index.
            </span>
          )}
        </div>
      )}

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

      {/* The legend is the accessibility payment for colouring by collection: the name is written
          out beside its swatch, so the grouping survives for anyone who cannot separate the hues,
          and the hull behind each cluster carries it a third way. */}
      <ul className="kgraph-legend" aria-label="Collections">
        {collections.map(([name, count]) => (
          <li key={name}>
            <span
              className="kgraph-swatch"
              style={{ background: `hsl(${hueOf(name)},58%,68%)` }}
              aria-hidden="true"
            />
            {name} <span className="muted">{count.toLocaleString()}</span>
          </li>
        ))}
      </ul>

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
                  {onPin && (
                    <input
                      type="checkbox"
                      className="kgraph-check"
                      checked={chosen.has(node.id)}
                      aria-label={`Use ${node.rel_path} as context`}
                      onChange={(e) =>
                        setChosen((prev) => {
                          const next = new Set(prev);
                          if (e.target.checked) next.add(node.id);
                          else next.delete(node.id);
                          return next;
                        })
                      }
                    />
                  )}
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
                      {node.collection} ·{" "}
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
