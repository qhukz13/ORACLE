# ORACLE — Technology Stack

> Every entry answers **why this** and **what was rejected**. Anything not justified here is not
> approved. Adding a dependency is a maintenance commitment, not a convenience.
>
> Facts marked `VERIFIED 2026-08-21` were checked against primary sources during design.
> Everything else is an `ASSUMPTION` until measured.

## 0. The constraint that drives everything

```
GPU    GTX 1050 Ti · 4096 MiB VRAM · compute 6.1 (Pascal) · driver 582.28
CPU    Xeon E5-2670 v3 · 12c/24t · Haswell · AVX2, no AVX-512 · 2.3 GHz
RAM    32 GB
Disk   C: 39.8 GB free  ·  D: 187 GB free  ·  E: 190 GB free
OS     Windows 10 Pro 19045
```

Two consequences that shape the whole design:

1. **~3.5 GB of usable VRAM.** After the Windows desktop takes its share, that is what remains. It is
   not enough for a 7B model, and barely enough for a 4B one *with any context at all*. The model is
   therefore small, and the architecture compensates by not relying on the model for much.
2. **Pascal is on borrowed time.** `VERIFIED 2026-08-21`: CUDA Toolkit 13.0 raised the minimum
   compute capability to 7.5 and 13.3 removed Maxwell/Pascal/Volta entirely. Ollama still supports
   compute 5.0–6.2 provided the driver is ≥ 570 (ours is 582.28), because it ships an older CUDA
   runner. **When that runner is dropped, this GPU loses acceleration overnight.** CPU fallback is a
   requirement, not a nicety. Tracked as [OQ-03](OPEN_QUESTIONS.md).

### Data locations

C: has under 40 GB free, so nothing large goes there.

| What | Path | Why |
|---|---|---|
| Source code | `C:\Projects\ORACLE` | with the other projects |
| Models | `D:\ORACLE\models` | set `OLLAMA_MODELS`; a 9B model is 6.6 GB |
| Databases | `D:\ORACLE\data` | `oracle.db`, `knowledge.db` |
| Logs, blobs, scratch | `D:\ORACLE\logs`, `…\blobs`, `…\scratch` | growth is unbounded |

`logs/` in the repo is for *development* notes and a symlink target during dev; production log volume
lives on D:. See [LOGGING.md](LOGGING.md).

---

## 1. Runtime

### Python 3.12, installed and pinned by `uv`

The machine has Python **3.14.6** and **3.10** installed. Both are wrong:

- 3.14 is too new for parts of this stack — native wheels for `onnxruntime`, `ctranslate2`,
  `pywinpty` and `tree-sitter` grammar packs historically lag a major release by months, and this
  project depends on all four. `TO VERIFY` before any change.
- 3.10 lacks the typing ergonomics used throughout (PEP 695 aside, `Self`, better `TypedDict`) and is
  approaching end of support.

**3.12** is the widest-wheel-coverage version that is still current. Critically, `uv` downloads and
manages a standalone CPython, so ORACLE does not depend on whatever the system Python becomes when I
install something unrelated next month. That isolation is the actual reason, more than the version.

*Rejected:* system Python (fragile, shared, already two conflicting versions); conda (heavy, another
package universe); 3.13 (fine, but buys nothing here and narrows wheels slightly).

### Node 24 + TypeScript for the frontend only

Already installed (v24.16.0, npm 11.13). Used strictly for the UI build. No Node in the backend path.

### Rust — only as Tauri's build toolchain

`cargo 1.91.1` is present. We write no application Rust in v1; if a hot path ever needs it (the
canonicaliser, the file walker), it becomes a Tauri command or a PyO3 extension. Not now.

---

## 2. Backend

| Choice | Why | Rejected |
|---|---|---|
| **FastAPI** | Pydantic-native, generates OpenAPI *and* JSON Schema from the same models we already need for tool contracts — one source of truth for HTTP, WS payloads, tool args, and TS types. First-class WebSockets. | Flask/Django (sync-first, no schema story), Litestar (good, smaller ecosystem, no compelling edge), raw Starlette (we'd rebuild FastAPI's validation) |
| **uvicorn** | The standard ASGI server; single-process is correct here | hypercorn (no benefit), gunicorn (POSIX) |
| **pydantic v2** | The schema backbone of the entire system (§ "one source of truth" below) | dataclasses+jsonschema (hand-maintained duplication), attrs (no schema gen) |
| **asyncio, single event loop** | The workload is I/O-bound: subprocesses, HTTP, sockets, SQLite | threads (worse cancellation), trio (smaller ecosystem) |
| **In-process task supervisor**, tasks persisted to SQLite | Long-running work must survive a UI reload and be cancellable, but a single user does not need a broker | **Celery/RQ rejected**: both require Redis — a second service, a second failure mode, a second thing to install, for a single-user desktop app |
| **CPU-heavy work in a subprocess** (embedding, indexing) | Keeps the event loop responsive; the GIL would otherwise stall event fan-out during a reindex | threads (GIL), in-loop (blocks streaming) |

### One source of truth

Pydantic models define tool arguments, events, and API payloads. From them we generate:
JSON Schema → constrained decoding for the LLM · OpenAPI → HTTP docs · TypeScript types → frontend.
Nothing in this list is hand-written twice. A hand-written TS interface mirroring a Python model is a
bug waiting to happen and is rejected in review.

---

## 3. Local LLM

### Candidate comparison

Measured against: tool calling, structured output, latency, **fits in ~3.5 GB VRAM**, Russian +
English, agentic behaviour. Sizes are Ollama's published defaults, `VERIFIED 2026-08-21`.

| Model | Size on disk | Fits VRAM? | Tool calling | RU | Verdict |
|---|---|---|---|---|---|
| **qwen3.5:0.8b** | 1.0 GB | **yes — measured 100% GPU at 16k ctx** | adequate | good | ✅ **CHOSEN ROUTER** — 45.4 tok/s |
| qwen3.5:2b | 2.7 GB | **no — measured 36%/64% CPU/GPU** | good | good | Reasoner only; 20.4 tok/s hybrid |
| qwen3.5:4b | 3.4 GB | **no** — weights alone leave nothing for KV | very good | very good | Reasoner via partial offload only |
| qwen3.5:9b | 6.6 GB | no | excellent | excellent | CPU/hybrid, ~5 tok/s — occasional deep use |
| Gemma 4 E4B | ~3–4 GB | no | very good (native FC tokens) | good | Alternative reasoner; Apache-2.0 |
| Phi-4-mini 3.8B | ~2.5 GB | marginal | good | **weak** | Rejected — Russian is a hard requirement |
| Llama 3.2 3B | ~2.0 GB | yes | adequate | mediocre | Fallback only |
| Qwen2.5-Coder 3B | ~2.0 GB | yes | adequate | n/a | Not needed — we delegate coding |

Qwen is chosen for the family, not a specific tag: it is the strongest small-model line for Russian,
has the most mature tool-calling ecosystem, and offers a size ladder (0.8/2/4/9) we can slide along
after benchmarking on *this* GPU. `VERIFIED 2026-08-21` that Qwen3.5 ships 0.8b–122b with a `tools`
capability tag.

> Note: published Qwen3.5 sizes are larger than a text-only model of that parameter count would
> suggest, because the family is multimodal and the tags include a vision tower. We do not need
> vision; a text-only quant may be materially smaller. `TO VERIFY` — check for text-only tags or
> build a text-only GGUF.

### VRAM: measured, not calculated  `VERIFIED 2026-08-21`

The original arithmetic here predicted `2b` would fit at 8–16k with a `q8_0` KV cache
(2.7 GB weights + 0.46 GB KV ≈ 3.2 GB inside ~3.5 GB usable). **Measurement disagreed:**

| model | num_ctx | `ollama ps` placement |
|---|---|---|
| qwen3.5:0.8b | 8192 **and** 16384 | **100% GPU** |
| qwen3.5:2b | 4096 **and** 8192 | 36%/64% CPU/GPU |

`2b` gives the **same split at 4k and 8k** — so it is the *weights* that don't fit, not the KV cache,
and reducing context cannot rescue it. The arithmetic under-counted Ollama's compute buffers and its
conservative headroom policy. Keep this as a standing lesson: **on a 4 GB card, measure placement
before designing around a model.**

### TTFT is dominated by prompt processing, not generation

Measured on `0.8b` at 100% GPU:

| prompt tokens | prompt eval | effective rate |
|---|---|---|
| 227 | 173 ms | 1313 tok/s |
| 1227 | **726 ms** | 1690 tok/s |
| 2427 | **1168 ms** | 2078 tok/s |
| 4827 | 2216 ms | 2179 tok/s |
| 8192 (extrapolated) | **~3.7 s** | — |

Generation is comfortable (45.4 tok/s); *feeding* the model is what costs. This is why the context
budget is **split by call type** rather than set globally
([AGENT_RUNTIME.md §5](AGENT_RUNTIME.md#5-context-budget)), and why intent-based tool pre-filtering is
load-bearing: ~1200 tokens of tool schemas costs ~730 ms of latency on *every* turn.

### `think: false` is mandatory

Qwen3.5 is a thinking model. Left at defaults it spent **229 tokens reasoning about how to say
"hello"** and returned an *empty* `response` field. Every router call sets `think: false`.

**Still open** — the accuracy half of [OQ-01](OPEN_QUESTIONS.md#oq-01): intent and tool-selection
rates for `0.8b` need the Phase 1 fixture set. If it is too weak, nothing else fits this GPU.

### Two-tier model strategy

| Tier | Model | Residency | Used for |
|---|---|---|---|
| **Router** | **qwen3.5:0.8b @ 16k, `think:false`** | GPU-resident (100% GPU), always loaded | intent, tool selection, short answers, summarisation — every turn |
| **Reasoner** | qwen3.5:2b / 4b / 9b | loaded on demand, hybrid CPU+GPU | plan construction, ambiguous cases, Handoff Packet drafting |
| **Delegate** | Claude / Antigravity | remote / separate process | real code work |

Keeping the router permanently resident matters more than its raw quality — and the measurement backs
it: **cold load took 51 s, warm load still 7–14 s** from D:. A router that reloads per turn would make
the system feel broken. See [ADR-0004](DECISIONS.md#adr-0004--two-tier-local-model-router--reasoner).

Note the reasoner tier now starts at `2b` (20.4 tok/s hybrid) rather than `4b`, since `2b` was
displaced from the router role. Also already on disk and worth benchmarking as a reasoner:
**`qwen3.6:35b-a3b`** — an MoE with ~3B active parameters, so CPU inference runs near 3B-dense speed
while needing ~22 GB RAM for weights, which fits in 32 GB. Costs nothing to test.

### Inference runtime — Ollama, behind an interface

**Chosen: Ollama** (client 0.32.5 already installed here). Model management, an OpenAI-compatible
endpoint, JSON-schema structured output, automatic partial GPU offload, and — decisively — it still
ships a CUDA runner supporting compute 6.1.

*Rejected:* **vLLM** (needs far more VRAM, paged-attention gains are irrelevant at batch size 1,
Linux-first); **LM Studio** (GUI-centric, not a service); **HF Transformers** (torch is a ~2.5 GB
dependency to do worse than llama.cpp on CPU); **direct llama.cpp** — *not rejected, deferred*: it is
the documented escape hatch when we need GBNF grammars, precise `n_gpu_layers` control, or when
Ollama drops Pascal. That is exactly why `LLMProvider` exists:

```python
class LLMProvider(Protocol):
    async def complete(self, req: CompletionRequest) -> Completion: ...
    async def stream(self, req: CompletionRequest) -> AsyncIterator[Delta]: ...
    async def structured(self, req: CompletionRequest, schema: type[BaseModel]) -> BaseModel: ...
    def capabilities(self) -> ProviderCaps   # tools, json_schema, ctx_len, vision
```

Implementations: `OllamaProvider` (v1), `LlamaCppProvider` (escape hatch), `AnthropicProvider`
(delegation), `FakeProvider` (**required** — deterministic replay for tests, see [TESTING.md](TESTING.md)).

### Structured output — schema-constrained, never parsed from prose

Small models produce malformed JSON often enough that "parse and hope" is not a strategy.

```
1  request with a JSON Schema (Ollama `format`)      → constrained decoding
2  validate with pydantic                            → typed object or error
3  on failure: ONE repair attempt, feeding the validation error back
4  still failing: deterministic fallback (ask the user / degrade to chat)
                  and log a `structured_output_failure` metric
```

Never a third retry — that is where latency and cost go to die. The failure rate is a tracked metric;
if it exceeds ~2%, the model tier or the schema is wrong.

We rely on **JSON Schema**, not on any model's native tool-call template, because the templates vary
per model and break the "provider is replaceable" property.

---

## 4. Knowledge

| Concern | Choice | Why |
|---|---|---|
| **Vector store** | **sqlite-vec** in `knowledge.db` | see below |
| **Lexical** | SQLite **FTS5** (BM25) | same file, same transaction, no second engine |
| **Fusion** | Reciprocal Rank Fusion | no tuning knobs, robust; beats dense-only on code and on exact identifiers |
| **Embeddings** | multilingual-e5-base (768d) via **ONNX Runtime on CPU** | Russian + English; CPU keeps VRAM free |
| **Code structure** | tree-sitter | function/class-level chunks with real symbol names |
| **Watching** | `watchfiles` (Rust notify) | far better Windows behaviour than `watchdog` |
| **PDF** | `pypdfium2` | Apache/BSD licensing; `PyMuPDF` is AGPL and we avoid that entanglement |

### Why sqlite-vec, decisively

The corpus was measured, not guessed. Asterim: **798 git-tracked files** (267 `.ts`, 190 `.md`,
91 `.tsx`). All three Obsidian vaults together: **~161 Markdown notes**. Total realistic corpus across
every project and note collection: a few thousand documents → roughly **30k–80k chunks**.

At that size a brute-force scan over 768-dim vectors is a few tens of milliseconds. **An ANN index is
not needed, and neither is a database server.**

*Rejected:*
- **PostgreSQL + pgvector** — excellent technology, wrong scale. It means installing and running a
  database server, managing users, backups and version upgrades, for a single-user desktop app with
  80k rows. Revisit only above ~2M chunks or if concurrent writers appear.
- **Qdrant** — either a Docker service (a second runtime to babysit) or its embedded local mode,
  which still adds a separate store to keep consistent with the metadata in SQLite.
- **Chroma** — heavier dependency tree, and its production story is thinner than the alternatives.
- **LanceDB** — genuinely good, and the designated upgrade path if sqlite-vec disappoints. Rejected
  for v1 only because it adds a second storage format for zero benefit at this corpus size.

The win is **one file, one transaction**: vectors, BM25 index, chunks and metadata are atomically
consistent, backed up by copying a file, and reset by deleting it. `VectorStore` is still an
interface so LanceDB can be swapped in without touching the retrieval logic.

### Why embeddings run on the CPU

Non-obvious and deliberate: the GPU holds the router model, permanently. Sharing 4 GB between an LLM
and an embedding model causes constant load/unload thrash that costs far more than the embedding
compute saves. Meanwhile 24 idle threads sit next to it. Indexing is a background batch job; latency
does not matter, and the GPU staying warm does.

`EXPERIMENT NEEDED` — [OQ-02](OPEN_QUESTIONS.md): compare `multilingual-e5-base` against `bge-m3` on
mixed Russian/English notes and code identifiers; measure CPU throughput and quality. Also evaluate
Matryoshka truncation to 384d — halves storage and scan time, usually at little cost.

No reranker in v1. Post-MVP: ONNX `bge-reranker-base` on CPU, top-30 → top-8, only if measured
retrieval quality demands it.

---

## 5. Desktop shell

**Tauri 2** (`VERIFIED 2026-08-21`: current stable is 2.10.1, March 2026; there is no Tauri 3).

Why: uses the system WebView2 (already on this machine) instead of bundling Chromium, so the shell
costs tens of MB of RAM rather than hundreds — and on a machine where RAM is contended by a language
model, that is the entire argument. Rust toolchain is already installed. Tauri's `externalBin`
sidecar mechanism cleanly supervises the Python backend, and its plugins cover tray, global hotkey
(needed for HALT) and autostart.

*Rejected:* **Electron** — 150–300 MB baseline RSS and a ~150 MB bundle to gain a Node runtime we do
not want in the trusted path. **Native (WinUI/Qt)** — the UI is the most iterated part of this project
and web tech iterates fastest; also throws away the browser and mobile clients for free.
**Browser-only** — no tray, no global hotkey, no autostart.

**The Tauri risk is deliberately contained.** Because the shell holds *zero business logic* — it is an
HTTP/WS client of `oracled` like any other — swapping it for Electron, or dropping to a plain browser
tab, is a shell replacement, not a rewrite. This is the mitigation that makes the choice safe.
See [ADR-0007](DECISIONS.md#adr-0007--clients-are-peers-of-one-local-api).

---

## 6. Frontend

| Choice | Why | Rejected |
|---|---|---|
| **React 19 + TypeScript + Vite** | Largest ecosystem for the widgets we need (terminal, virtualised lists, panes); Vite for fast HMR | Svelte/Solid (smaller ecosystem for xterm/virtualisation), Vue (no advantage here) |
| **Tailwind CSS v4 + CSS variables** | The UI is token-driven (status colours carry meaning — see [UI.md](UI.md#14-colour-and-status-semantics)); CSS vars make theming and `prefers-reduced-motion` trivial | CSS-in-JS (runtime cost), plain CSS (no constraint system) |
| **Zustand + a WS event reducer** | Nearly all state is server-pushed events; a single event-sourced store mirrors the backend exactly | Redux Toolkit (ceremony), TanStack Query (built for request/response, not push) |
| **Radix primitives** | Accessible dialog/menu/tooltip behaviour we would otherwise get wrong | MUI/AntD (opinionated visuals fight the design), hand-rolled (a11y bugs) |
| **xterm.js** | The only serious web terminal | — |
| **Hand-written SVG for the orbital view** | see below | — |

### Visualisation: SVG + deterministic layout, not a graph library

The central view has fewer than ~40 nodes. It must be **stable** — a node must sit in the same place
every time so it becomes muscle memory — crisp at any DPI, themeable via the same CSS variables as
everything else, and accessible.

*Rejected:* **force simulation (d3-force)** — physics jitter means nodes move between renders, which
destroys recognisability and makes the view decorative rather than readable. **React Flow** — built
for editable node-edge graphs; wrong interaction model. **PixiJS/WebGL** — overkill at this node
count and forfeits DOM accessibility.

Chosen: deterministic polar layout — ring = category, angle = stable hash of node id, radius =
recency/attention. `d3-scale` and `d3-shape` as pure math helpers; no `d3-selection` (it fights React).

### Terminal: xterm.js + **pywinpty** (ConPTY)  `VERIFIED 2026-08-21`

The PTY lives in the **backend**, not the shell — that is what lets the phone attach to the same
terminal session and what keeps the shell logic-free. `pywinpty` wraps Windows ConPTY.

**`pywinpty>=3.0.5`, verified before it was added** ([OQ-09](OPEN_QUESTIONS.md#oq-09)): the 3.12
wheel installs clean, Cyrillic needs no `chcp` because ConPTY normalises to UTF-8, resize mid-stream
is safe, and concurrent sessions do not leak into each other. It earns its place because the
alternative is reimplementing ConPTY's pseudoconsole handshake in `ctypes`, which is a worse
maintenance commitment than one wheel.

More precisely, the PTY lives in the **toolhost child**, not the API process — a shell must be inside
the Job Object so HALT can actually stop it.

---

## 7. Storage

**SQLite (WAL) only**, two files — `oracle.db` (operational) and `knowledge.db` (rebuildable index).
Access via `aiosqlite`; schema managed by **numbered `.sql` migration files** run by a small
in-house migrator.

*Rejected:* **SQLAlchemy ORM** — a heavy abstraction over a schema we control completely and query in
SQLite-specific ways (FTS5 `MATCH`, `vec0` virtual tables) that the ORM does not model anyway.
**Alembic** — autogeneration is valuable against an ORM; without one it is machinery for nothing.
**PostgreSQL** — see §4.

Explicit trade-off accepted: hand-written SQL and migrations mean more discipline and no free
multi-database portability. We never need portability, and explicit SQL is easier for a coding agent
to reason about than ORM session semantics. Schema in [DATABASE.md](DATABASE.md).

---

## 8. Communication

- **REST** for state you can name: projects, tasks, documents, settings, devices.
- **WebSocket** for everything that streams: tokens, tool output, terminal bytes, events, approvals.
- One versioned envelope for both, generated from pydantic. Protocol in [API.md](API.md).
- **TLS + device tokens** for anything off-loopback; **mDNS** for discovery. See [SECURITY.md](SECURITY.md#8-network-and-device-authentication).

*Rejected:* gRPC (browser support needs a proxy; the schema story is worse than "pydantic
everywhere"), Server-Sent Events (unidirectional — we need client→server commands on the same
channel), raw TCP (rebuilding HTTP badly).

---

## 9. External agents

`VERIFIED 2026-08-21` against primary docs. Full detail in [INTEGRATIONS.md](INTEGRATIONS.md).

| Agent | Interface | Tier |
|---|---|---|
| **Claude Code** | `claude -p --bare --output-format stream-json` + `--json-schema`, `--resume`, `--allowedTools`; installed here (v2.1.234) | **Supported** |
| **Anthropic API** | Messages API via `anthropic` SDK, for cheap non-agentic calls | **Supported** |
| **MCP (inbound)** | ORACLE exposes its guarded tools as an MCP server so delegated agents call back in instead of running raw shell | **Supported** |
| **Antigravity** | `agy -p --output-format stream-json` (CLI v1.1.x, SDK v0.1.x) — but see the open non-TTY stdout bug | **Potential** — blocked on [OQ-05](OPEN_QUESTIONS.md) |
| **Handoff Packet** | Write a self-contained task to disk; collect results via git diff | **Fallback** — works with any agent, including ones that don't exist yet |

---

## 10. Voice (Phase 10 — deliberately unresolved)

The landscape moved: `VERIFIED 2026-08-21` **Piper TTS was archived in October 2025**, so the obvious
default is gone. Recommending it now would be exactly the stale assumption this document exists to
prevent.

Current shape of the decision, to be made at Phase 10, not before:

- **STT**: `faster-whisper` (CTranslate2, int8, CPU) or `whisper.cpp`. Russian needs `small` or better;
  `tiny/base` are inadequate for it. 24 threads should manage near real-time at `small`.
- **VAD**: Silero VAD (tiny, CPU, reliable).
- **Wake word**: openWakeWord (open, trainable, cheap) over Porcupine (proprietary).
- **TTS**: Windows SAPI/WinRT voices as the zero-dependency baseline (a Russian voice ships with the
  OS); Silero TTS for better Russian quality. Kokoro-82M is excellent but its language coverage for
  Russian is `TO VERIFY`.

Architecturally none of this matters yet, and that is the point: **voice is just another client of the
same WS API**. It can be built, replaced, or abandoned without the agent core noticing.

---

## 11. Development tooling

| | |
|---|---|
| Package/venv | `uv` (fast, lockfile, manages the Python toolchain itself) |
| Lint + format | `ruff` (replaces black + isort + flake8 + several plugins) |
| Types | `mypy --strict` on `packages/core`, `packages/policy`, `packages/tools`; looser elsewhere |
| Tests | `pytest`, `pytest-asyncio`, `hypothesis` (property tests for the path canonicaliser) |
| Frontend | `vitest`, `@testing-library/react`, `playwright` for E2E |
| Git | already in use; ORACLE itself is not yet a repo — `git init` is task 1 of Phase 0 |
| Docker | installed (29.5.3); used *as a tool ORACLE drives*, not to run ORACLE |
| CI | GitHub Actions only if this repo ever gets a remote; local `make check` is the real gate |

**ORACLE does not run in Docker.** It controls the host — the whole point — so containerising it
would defeat its purpose while adding a filesystem/GPU passthrough problem.
