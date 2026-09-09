/**
 * OQ-22 measurement 2 — canvas vs SVG, at the scene measurement 3 chose.
 *
 * The other three OQ-22 measurements ran headless on 2026-08-26 and the dev log is explicit that
 * this one could not: it needs `requestAnimationFrame` deltas from a *compositing* window on this
 * GPU, and "any frame timing from a page that was never drawn" is the kind of number this project
 * keeps throwing away. So this file is deliberately a page, not a script — it must be looked at
 * while it runs.
 *
 * Three things it does that a naive harness would not:
 *
 * 1. **It measures the vsync floor first.** An empty rAF loop on a 60 Hz display returns 16.7 ms
 *    deltas no matter how fast the renderer is. Without that control, "60 fps" is unfalsifiable —
 *    every renderer that keeps up scores identically, and the only real signal is the tail.
 * 2. **It runs the camera off wall-clock, not off frame count.** Both renderers traverse the same
 *    path over the same duration; a slower one covers it in fewer frames instead of taking longer.
 *    Driving the camera per-frame would silently hand the slow renderer an easier scene.
 * 3. **It measures two SVG variants, because the product's requirement decides the comparison.**
 *    `svg-group` moves one `<g>` transform and lets nodes scale with zoom — SVG's best case.
 *    `svg-constant` keeps nodes a constant size on screen, which is what a graph you can click at
 *    any zoom actually needs, and which costs 1,420 attribute writes per frame. Canvas gets
 *    constant-size nodes for free. Comparing canvas only against SVG's best case would answer a
 *    question ADR-0023 did not ask.
 */

export {};

/** The wire shape of `public/bench-scene.json`, written by `scripts/export_graph_scene.py`. */
type SceneJson = {
  fingerprint: string;
  live_fingerprint: string | null;
  edge_model: { k: number; threshold: number };
  counts: { nodes: number; edges: number; explicit: number };
  collections: string[];
  /** Node count. Ids are not exported — see `export_graph_scene.py` for why. */
  n: number;
  collection: number[];
  x: number[];
  y: number[];
  edges: number[];
};

/**
 * A dense numeric array. `Float64Array`, `Int32Array` and `Uint8Array` are all assignable to it.
 *
 * **Reads from these are asserted non-null (`!`) throughout the render loop, and that is the one
 * deliberate type escape in this file.** `noUncheckedIndexedAccess` is on repo-wide and is right
 * to be — it catches the off-by-one and sparse-array bugs this project's TS is meant to be free
 * of — but it applies to every index signature, so no declaration can opt a hot path out of it.
 * The render loop reads these several million times per run, and a `?? 0` on each line would put
 * a fallback where no fallback is meaningful: every index here is in range by construction,
 * emitted by `export_graph_scene.py` as an index into the very arrays it writes alongside it. An
 * out-of-range read is a bug in the exporter, and `?? 0` would draw it at the origin instead of
 * crashing.
 */
type Nums = { readonly [i: number]: number; readonly length: number };

/** The scene in typed arrays: the renderers read it far too often for boxed JS numbers. */
type Scene = {
  json: SceneJson;
  n: number;
  x: Nums;
  y: Nums;
  collection: Nums;
  /** Flat `[a0, b0, a1, b1, …]` node indices. */
  edges: Nums;
  collectionCount: number;
};

type Camera = { cx: number; cy: number; zoom: number };

type Stats = {
  frames: number;
  fps_median: number;
  ms_p50: number;
  ms_p95: number;
  ms_p99: number;
  ms_worst: number;
  over_vsync: number;
  janky: number;
};

type RunResult = {
  kind: string;
  build_ms: number;
  first_paint_ms: number;
  elements: number;
  animate: Stats;
  pick_p50_ms: number;
  pick_p95_ms: number;
};

type Report = {
  measurement: string;
  when: string;
  agent: string;
  dpr: number;
  viewport: { w: number; h: number };
  scene: SceneJson["counts"];
  fingerprint: string;
  vsync_ms: number;
  animate_ms: number;
  runs: RunResult[];
};

/** Node radius in CSS pixels, constant on screen. Small enough that 1,420 of them read as a map. */
const NODE_R = 2.6;
const ANIMATE_MS = 6000;
const VSYNC_SAMPLE_MS = 1200;
const PICKS = 200;

const COLOURS = ["#d2a8ff", "#79c0ff", "#7ee787", "#ffa657", "#ff7b72"] as const;
const EDGE_COLOUR = "rgba(139,148,158,0.22)";
const BACKGROUND = "#0e1116";

const colourOf = (c: number): string => COLOURS[c % COLOURS.length] ?? COLOURS[0];

// ---------------------------------------------------------------------------
// statistics
// ---------------------------------------------------------------------------

function percentile(sorted: readonly number[], p: number): number {
  if (sorted.length === 0) return NaN;
  const i = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[i] ?? NaN;
}

/**
 * `vsyncMs` is the measured floor, not an assumed 16.67. A 60 Hz panel actually running at 59.94,
 * or a 144 Hz one, would otherwise turn every renderer into a failure or every renderer into a
 * pass. `over_vsync` counts frames that missed the display's own budget by more than 25%.
 */
function summarise(deltas: number[], vsyncMs: number): Stats {
  const sorted = [...deltas].sort((a, b) => a - b);
  const p50 = percentile(sorted, 50);
  return {
    frames: deltas.length,
    fps_median: p50 > 0 ? +(1000 / p50).toFixed(1) : 0,
    ms_p50: +p50.toFixed(2),
    ms_p95: +percentile(sorted, 95).toFixed(2),
    ms_p99: +percentile(sorted, 99).toFixed(2),
    ms_worst: +percentile(sorted, 100).toFixed(2),
    over_vsync: deltas.filter((d) => d > vsyncMs * 1.25).length,
    janky: deltas.filter((d) => d > 33.4).length,
  };
}

// ---------------------------------------------------------------------------
// camera
// ---------------------------------------------------------------------------

/**
 * A fixed circuit: fit → zoom into the dense middle → orbit → zoom back out. Chosen so the run
 * spends time in both regimes that matter — fully zoomed out, where every one of the 3,103 edges is
 * on screen and the cost is fill rate, and zoomed in, where the cost is transform and overdraw.
 */
function cameraAt(t: number, bbox: { cx: number; cy: number; w: number; h: number }): Camera {
  const ease = (u: number) => u * u * (3 - 2 * u);
  let zoom: number;
  let ax = 0;
  let ay = 0;
  if (t < 0.25) {
    zoom = 1 + 3 * ease(t / 0.25);
  } else if (t < 0.75) {
    zoom = 4;
    const a = ((t - 0.25) / 0.5) * Math.PI * 2;
    ax = Math.cos(a) * bbox.w * 0.12;
    ay = Math.sin(a) * bbox.h * 0.12;
  } else {
    zoom = 4 - 3 * ease((t - 0.75) / 0.25);
  }
  return { cx: bbox.cx + ax, cy: bbox.cy + ay, zoom };
}

// ---------------------------------------------------------------------------
// renderers
// ---------------------------------------------------------------------------

interface Renderer {
  readonly kind: string;
  readonly elements: number;
  draw(cam: Camera): void;
  pick(px: number, py: number): number;
  destroy(): void;
}

type Ctx = {
  scene: Scene;
  stage: HTMLElement;
  w: number;
  h: number;
  /** CSS pixels per scene unit at zoom 1 — the fit scale, computed once from the bounding box. */
  fit: number;
  bbox: { cx: number; cy: number; w: number; h: number };
};

class CanvasRenderer implements Renderer {
  readonly kind = "canvas";
  readonly elements = 1;
  private canvas: HTMLCanvasElement;
  private g: CanvasRenderingContext2D;
  private dpr: number;
  private lastCam: Camera = { cx: 0, cy: 0, zoom: 1 };

  constructor(private ctx: Ctx) {
    this.dpr = window.devicePixelRatio || 1;
    this.canvas = document.createElement("canvas");
    this.canvas.width = Math.round(ctx.w * this.dpr);
    this.canvas.height = Math.round(ctx.h * this.dpr);
    this.canvas.style.width = `${ctx.w}px`;
    this.canvas.style.height = `${ctx.h}px`;
    ctx.stage.appendChild(this.canvas);
    const g = this.canvas.getContext("2d", { alpha: false });
    if (!g) throw new Error("no 2d context");
    this.g = g;
  }

  draw(cam: Camera): void {
    this.lastCam = cam;
    const { scene, w, h } = this.ctx;
    const g = this.g;
    g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    g.fillStyle = BACKGROUND;
    g.fillRect(0, 0, w, h);

    const s = this.ctx.fit * cam.zoom;
    const ox = w / 2 - cam.cx * s;
    const oy = h / 2 - cam.cy * s;

    // One path for every edge. Batching matters more than culling at this count: 3,103 strokes in
    // a single path is one submission, and testing each against the viewport would cost more
    // arithmetic than the strokes it saves.
    g.beginPath();
    const e = scene.edges;
    for (let k = 0; k < e.length; k += 2) {
      const a = e[k]!;
      const b = e[k + 1]!;
      g.moveTo(scene.x[a]! * s + ox, scene.y[a]! * s + oy);
      g.lineTo(scene.x[b]! * s + ox, scene.y[b]! * s + oy);
    }
    g.strokeStyle = EDGE_COLOUR;
    g.lineWidth = 1;
    g.stroke();

    // Nodes, grouped by collection so the fill colour is set once per group rather than per node.
    for (let c = 0; c < scene.collectionCount; c++) {
      g.beginPath();
      let any = false;
      for (let i = 0; i < scene.n; i++) {
        if (scene.collection[i]! !== c) continue;
        const px = scene.x[i]! * s + ox;
        const py = scene.y[i]! * s + oy;
        if (px < -8 || py < -8 || px > w + 8 || py > h + 8) continue;
        g.moveTo(px + NODE_R, py);
        g.arc(px, py, NODE_R, 0, Math.PI * 2);
        any = true;
      }
      if (!any) continue;
      g.fillStyle = colourOf(c);
      g.fill();
    }
  }

  /**
   * A linear scan over 1,420 nodes. This *is* the canvas complexity ADR-0023 buys: SVG gets hit
   * testing from the browser's own hit-test tree, and canvas has to bring its own. Reporting the
   * naive version keeps the cost visible rather than hiding it behind a quadtree that would then
   * also need invalidating.
   */
  pick(px: number, py: number): number {
    const { scene, w, h } = this.ctx;
    const cam = this.lastCam;
    const s = this.ctx.fit * cam.zoom;
    const ox = w / 2 - cam.cx * s;
    const oy = h / 2 - cam.cy * s;
    const r2 = (NODE_R + 3) ** 2;
    let best = -1;
    let bestD = Infinity;
    for (let i = 0; i < scene.n; i++) {
      const dx = scene.x[i]! * s + ox - px;
      const dy = scene.y[i]! * s + oy - py;
      const d = dx * dx + dy * dy;
      if (d < r2 && d < bestD) {
        bestD = d;
        best = i;
      }
    }
    return best;
  }

  destroy(): void {
    this.canvas.remove();
  }
}

class SvgRenderer implements Renderer {
  readonly kind: string;
  readonly elements: number;
  private svg: SVGSVGElement;
  private root: SVGGElement;
  private circles: SVGCircleElement[] = [];

  constructor(
    private ctx: Ctx,
    /** false = one group transform, nodes scale with zoom (SVG's best case). */
    private constantNodeSize: boolean,
  ) {
    this.kind = constantNodeSize ? "svg-constant" : "svg-group";
    const NS = "http://www.w3.org/2000/svg";
    const { scene, w, h } = ctx;
    this.svg = document.createElementNS(NS, "svg");
    this.svg.setAttribute("width", String(w));
    this.svg.setAttribute("height", String(h));
    this.svg.style.background = BACKGROUND;
    this.root = document.createElementNS(NS, "g");

    const edgeGroup = document.createElementNS(NS, "g");
    edgeGroup.setAttribute("stroke", EDGE_COLOUR);
    edgeGroup.setAttribute("stroke-width", "1");
    // Without this, edges thicken 4x at zoom 4 and the map turns into a smear. It is also why the
    // group-transform variant is viable at all: the browser keeps strokes hairline while
    // compositing instead of forcing a re-layout of 3,103 elements.
    edgeGroup.setAttribute("vector-effect", "non-scaling-stroke");
    const e = scene.edges;
    for (let k = 0; k < e.length; k += 2) {
      const a = e[k]!;
      const b = e[k + 1]!;
      const line = document.createElementNS(NS, "line");
      line.setAttribute("x1", String(scene.x[a]!));
      line.setAttribute("y1", String(scene.y[a]!));
      line.setAttribute("x2", String(scene.x[b]!));
      line.setAttribute("y2", String(scene.y[b]!));
      edgeGroup.appendChild(line);
    }
    this.root.appendChild(edgeGroup);

    const nodeGroup = document.createElementNS(NS, "g");
    for (let i = 0; i < scene.n; i++) {
      const c = document.createElementNS(NS, "circle");
      c.setAttribute("cx", String(scene.x[i]!));
      c.setAttribute("cy", String(scene.y[i]!));
      c.setAttribute("fill", colourOf(scene.collection[i]!));
      nodeGroup.appendChild(c);
      this.circles.push(c);
    }
    this.root.appendChild(nodeGroup);
    this.svg.appendChild(this.root);
    ctx.stage.appendChild(this.svg);
    this.elements = e.length / 2 + scene.n;
  }

  draw(cam: Camera): void {
    const { w, h } = this.ctx;
    const s = this.ctx.fit * cam.zoom;
    this.root.setAttribute(
      "transform",
      `translate(${w / 2} ${h / 2}) scale(${s}) translate(${-cam.cx} ${-cam.cy})`,
    );
    // Constant size on screen means the radius must move opposite the zoom, once per node per
    // frame. The group variant sets `r` once and lets the transform scale it.
    if (this.constantNodeSize) {
      const r = String(NODE_R / s);
      for (const c of this.circles) c.setAttribute("r", r);
    } else if (!this.circles[0]?.hasAttribute("r")) {
      const r = String(NODE_R / this.ctx.fit);
      for (const c of this.circles) c.setAttribute("r", r);
    }
  }

  /** The browser's own hit-test tree, which is the thing canvas has to reimplement. */
  pick(px: number, py: number): number {
    const rect = this.svg.getBoundingClientRect();
    const el = document.elementFromPoint(px + rect.left, py + rect.top);
    if (!el || el.tagName !== "circle") return -1;
    return this.circles.indexOf(el as SVGCircleElement);
  }

  destroy(): void {
    this.svg.remove();
  }
}

// ---------------------------------------------------------------------------
// the run
// ---------------------------------------------------------------------------

const raf = (): Promise<number> => new Promise<number>((r) => requestAnimationFrame(r));

/**
 * Refuse to measure a page nobody is drawing.
 *
 * This is not hypothetical: the first attempt to run this harness sat in a hidden browser pane
 * where `document.visibilityState` reported **"visible"**, `setTimeout` fired normally, and
 * `requestAnimationFrame` delivered **zero** callbacks in 1.5 seconds. Every obvious guard passes
 * there, and the harness would have reported a plausible-looking frame distribution assembled from
 * a compositor that never ran — the precise failure the 2026-08-26 dev log refused to commit when
 * it left measurement 2 unanswered.
 *
 * So the liveness check is the frame loop itself, and nothing else: if frames are not arriving,
 * there is no measurement to be had and saying so is the correct output.
 */
async function requireLiveFrames(): Promise<void> {
  let frames = 0;
  const tick = (): void => {
    frames++;
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  await new Promise((r) => setTimeout(r, 500));
  if (frames < 5) {
    throw new Error(
      `requestAnimationFrame delivered ${frames} frames in 500 ms — this window is not being ` +
        `composited (visibilityState="${document.visibilityState}"). Show the window and re-run; ` +
        `frame timings from a page that was never drawn are not a measurement.`,
    );
  }
}

/** The display's own floor. Everything downstream is compared against this, not against 16.67. */
async function measureVsync(): Promise<number> {
  const deltas: number[] = [];
  let last = await raf();
  const until = performance.now() + VSYNC_SAMPLE_MS;
  while (performance.now() < until) {
    const now = await raf();
    deltas.push(now - last);
    last = now;
  }
  return percentile(
    [...deltas].sort((a, b) => a - b),
    50,
  );
}

async function runOne(
  make: (ctx: Ctx) => Renderer,
  ctx: Ctx,
  vsyncMs: number,
  say: (s: string) => void,
): Promise<RunResult> {
  const t0 = performance.now();
  const r = make(ctx);
  const buildMs = performance.now() - t0;
  say(`${r.kind}: built ${r.elements} element(s) in ${buildMs.toFixed(0)} ms`);

  // First paint: draw once, then wait for the frame *after* the one that committed it. A single
  // rAF returns before the compositor has shown anything, which is how harnesses end up reporting
  // sub-millisecond paints for scenes that took a second to appear.
  const p0 = performance.now();
  r.draw(cameraAt(0, ctx.bbox));
  await raf();
  const firstPaintMs = (await raf()) - p0;

  say(`${r.kind}: animating ${ANIMATE_MS / 1000}s…`);
  const deltas: number[] = [];
  const start = performance.now();
  let last = await raf();
  for (;;) {
    const elapsed = performance.now() - start;
    if (elapsed >= ANIMATE_MS) break;
    r.draw(cameraAt(elapsed / ANIMATE_MS, ctx.bbox));
    const now = await raf();
    deltas.push(now - last);
    last = now;
  }
  const animate = summarise(deltas, vsyncMs);

  // Hit testing, from a deterministic pseudo-random sprinkle over the viewport.
  r.draw(cameraAt(0, ctx.bbox));
  await raf();
  const picks: number[] = [];
  let seed = 12345;
  for (let i = 0; i < PICKS; i++) {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    const px = (seed / 0xffffffff) * ctx.w;
    seed = (seed * 1664525 + 1013904223) >>> 0;
    const py = (seed / 0xffffffff) * ctx.h;
    const t = performance.now();
    r.pick(px, py);
    picks.push(performance.now() - t);
  }
  picks.sort((a, b) => a - b);
  r.destroy();

  return {
    kind: r.kind,
    build_ms: +buildMs.toFixed(1),
    first_paint_ms: +firstPaintMs.toFixed(1),
    elements: r.elements,
    animate,
    pick_p50_ms: +percentile(picks, 50).toFixed(4),
    pick_p95_ms: +percentile(picks, 95).toFixed(4),
  };
}

// ---------------------------------------------------------------------------
// page
// ---------------------------------------------------------------------------

const el = <T extends HTMLElement>(id: string): T => {
  const found = document.getElementById(id);
  if (!found) throw new Error(`missing #${id}`);
  return found as T;
};

const stage = el("stage");
const statusEl = el("status");
const resultsEl = el("results");
const jsonEl = el("json");
const sceneLine = el("scene-line");
const copyButton = el<HTMLButtonElement>("copy");
const say = (s: string): void => {
  statusEl.textContent = s;
};

let ctx: Ctx | null = null;
let report: Report | null = null;

const MAKERS: Record<string, (c: Ctx) => Renderer> = {
  canvas: (c) => new CanvasRenderer(c),
  "svg-group": (c) => new SvgRenderer(c, false),
  "svg-constant": (c) => new SvgRenderer(c, true),
};

async function loadScene(): Promise<Ctx> {
  const t0 = performance.now();
  const response = await fetch("/bench-scene.json");
  if (!response.ok) throw new Error(`bench-scene.json: ${response.status}`);
  const json = (await response.json()) as SceneJson;
  const fetchMs = performance.now() - t0;

  const scene: Scene = {
    json,
    n: json.n,
    x: Float64Array.from(json.x),
    y: Float64Array.from(json.y),
    collection: Uint8Array.from(json.collection),
    edges: Int32Array.from(json.edges),
    collectionCount: json.collections.length,
  };

  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (let i = 0; i < scene.n; i++) {
    if (scene.x[i]! < minX) minX = scene.x[i]!;
    if (scene.x[i]! > maxX) maxX = scene.x[i]!;
    if (scene.y[i]! < minY) minY = scene.y[i]!;
    if (scene.y[i]! > maxY) maxY = scene.y[i]!;
  }
  const w = stage.clientWidth;
  const h = stage.clientHeight;
  const bbox = { cx: (minX + maxX) / 2, cy: (minY + maxY) / 2, w: maxX - minX, h: maxY - minY };
  const fit = 0.9 * Math.min(w / (bbox.w || 1), h / (bbox.h || 1));

  sceneLine.textContent =
    `${json.counts.nodes} nodes · ${json.counts.edges} edges ` +
    `(k=${json.edge_model.k} thr=${json.edge_model.threshold}) · ` +
    `${w}×${h} @${window.devicePixelRatio}x · scene fetched in ${fetchMs.toFixed(0)} ms`;
  return { scene, stage, w, h, fit, bbox };
}

function render(): void {
  if (!report) return;
  const rows = report.runs
    .map(
      (r) => `<tr>
        <td>${r.kind}</td>
        <td>${r.animate.fps_median}</td>
        <td>${r.animate.ms_p95}</td>
        <td>${r.animate.ms_worst}</td>
        <td class="${r.animate.over_vsync === 0 ? "pass" : "fail"}">${r.animate.over_vsync}</td>
        <td>${r.first_paint_ms}</td>
        <td>${r.build_ms}</td>
        <td>${r.pick_p50_ms}</td>
      </tr>`,
    )
    .join("");
  resultsEl.innerHTML = `
    <table>
      <tr><th>renderer</th><th>fps</th><th>p95</th><th>worst</th><th>slow</th>
          <th>paint</th><th>build</th><th>pick</th></tr>
      ${rows}
    </table>
    <div class="legend">vsync floor ${report.vsync_ms.toFixed(2)} ms
      (${(1000 / report.vsync_ms).toFixed(0)} Hz) · <b>slow</b> = frames over 1.25× vsync ·
      all times in ms</div>`;
  jsonEl.textContent = JSON.stringify(report, null, 2);
  copyButton.disabled = false;
}

async function run(kinds: string[]): Promise<void> {
  const buttons = [...document.querySelectorAll("button")];
  buttons.forEach((b) => (b.disabled = true));
  try {
    if (!ctx) ctx = await loadScene();
    stage.replaceChildren();
    say("checking that this window is actually being drawn…");
    await requireLiveFrames();
    say("measuring the display's vsync floor…");
    const vsyncMs = await measureVsync();
    const runs: RunResult[] = [];
    for (const kind of kinds) {
      const make = MAKERS[kind];
      if (!make) throw new Error(`no renderer named ${kind}`);
      runs.push(await runOne(make, ctx, vsyncMs, say));
    }
    report = {
      measurement: "OQ-22 #2 — canvas vs SVG",
      when: new Date().toISOString(),
      agent: navigator.userAgent,
      dpr: window.devicePixelRatio,
      viewport: { w: ctx.w, h: ctx.h },
      scene: ctx.scene.json.counts,
      fingerprint: ctx.scene.json.fingerprint,
      vsync_ms: +vsyncMs.toFixed(3),
      animate_ms: ANIMATE_MS,
      runs,
    };
    render();
    say("done");
  } catch (err) {
    say(`failed: ${err instanceof Error ? err.message : String(err)}`);
    throw err;
  } finally {
    buttons.forEach((b) => (b.disabled = false));
    copyButton.disabled = !report;
  }
}

el("run-all").addEventListener("click", () => {
  void run(["canvas", "svg-group", "svg-constant"]);
});
el("run-canvas").addEventListener("click", () => void run(["canvas"]));
el("run-svg").addEventListener("click", () => void run(["svg-group", "svg-constant"]));
copyButton.addEventListener("click", () => {
  void navigator.clipboard.writeText(JSON.stringify(report, null, 2));
  say("copied");
});

/**
 * The idle-CPU gate cannot be measured from inside the page — a page that polls to prove it is idle
 * is not idle. This mounts a renderer and stops, so an external sampler (`Get-Counter` against the
 * renderer process) has a window with a known start.
 */
async function mountIdle(kind: string): Promise<string> {
  if (!ctx) ctx = await loadScene();
  const make = MAKERS[kind];
  if (!make) throw new Error(`no renderer named ${kind}`);
  stage.replaceChildren();
  const r = make(ctx);
  r.draw(cameraAt(0, ctx.bbox));
  const at = new Date().toISOString();
  say(`${kind} mounted, idle since ${at}`);
  return at;
}

Object.assign(window, {
  __oq22: {
    run,
    mountIdle,
    report: (): Report | null => report,
    scene: (): SceneJson["counts"] | undefined => ctx?.scene.json.counts,
  },
});

void loadScene()
  .then((c) => {
    ctx = c;
    say("ready — run it at a real window, not a headless one");
  })
  .catch((err: unknown) => say(`scene failed to load: ${String(err)}`));
