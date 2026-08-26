# ORACLE — Architecture Decision Records

Every significant decision, with the reasoning that produced it. **Do not deviate silently.** To
change a decision, add a new ADR that supersedes the old one and update the `Status` line of both.

Format per record: Decision · Context · Options · Chosen · Why · Trade-offs · Consequences.

| # | Decision | Status |
|---|---|---|
| [0001](#adr-0001--orchestrator-not-a-monolithic-agent) | Orchestrator, not a monolithic agent | accepted, extended by 0019 |
| [0002](#adr-0002--python-312-managed-by-uv) | Python 3.12 managed by `uv` | accepted |
| [0003](#adr-0003--tool-execution-in-a-separate-process) | Tool execution in a separate process | accepted, **confirmed in implementation** |
| [0004](#adr-0004--two-tier-local-model-router--reasoner) | Two-tier local model (router + reasoner) | accepted, benchmarked · **conditional on 4 GB VRAM — see 0026** |
| [0005](#adr-0005--one-policy-gate-risk-tiers-taint-tracking) | One policy gate, risk tiers, taint tracking | accepted |
| [0006](#adr-0006--sqlite-only-storage-two-files-sqlite-vec--fts5) | SQLite-only storage, two files (sqlite-vec + FTS5) | accepted |
| [0007](#adr-0007--clients-are-peers-of-one-local-api) | Clients are peers of one local API | accepted |
| [0008](#adr-0008--tauri-2-for-the-desktop-shell) | Tauri 2 for the desktop shell | accepted |
| [0009](#adr-0009--ollama-as-default-provider-behind-llmprovider) | Ollama as default provider behind `LLMProvider` | accepted |
| [0010](#adr-0010--event-sourced-runtime) | Event-sourced runtime | accepted |
| [0011](#adr-0011--deterministic-pre-router-before-the-model) | Deterministic pre-router before the model | accepted |
| [0012](#adr-0012--git-worktree-delegation-with-a-vendor-neutral-fallback) | Git-worktree delegation with a vendor-neutral fallback | accepted |
| [0013](#adr-0013--deterministic-svg-orbit-no-force-simulation) | Deterministic SVG orbit, no force simulation | accepted |
| [0014](#adr-0014--embeddings-on-cpu-gpu-reserved-for-the-router) | Embeddings on CPU, GPU reserved for the router | accepted |
| [0015](#adr-0015--intent-shaped-tools-no-general-shell) | Intent-shaped tools, no general shell | accepted |
| [0016](#adr-0016--mvp-excludes-the-interesting-parts) | MVP excludes the interesting parts | accepted |
| [0017](#adr-0017--constrain-what-the-decoder-can-enforce) | Constrain what the decoder can enforce | accepted |
| [0018](#adr-0018--a-launched-application-is-not-a-tool-call) | A launched application is not a tool call | accepted |
| [0019](#adr-0019--the-supervisor-completes-the-orchestrator) | The supervisor completes the orchestrator; planning is a delegated role | accepted 2026-08-24 |
| [0020](#adr-0020--the-task-graph-is-a-durable-dag-with-append-only-replanning) | The task graph is a durable DAG with append-only replanning | accepted 2026-08-24 |
| [0021](#adr-0021--planner-output-is-untrusted-input) | Planner output is untrusted input | accepted 2026-08-24 |
| [0022](#adr-0022--external-agent-frameworks-evaluated-not-adopted) | External agent frameworks: evaluated, not adopted | accepted 2026-08-24 |
| [0023](#adr-0023--the-knowledge-graph-is-simulated-then-frozen-canvas-rendered) | The knowledge graph is simulated-then-frozen, canvas-rendered | accepted 2026-08-24 |
| [0024](#adr-0024--a-project-is-a-first-class-persistent-entity) | A project is a first-class persistent entity | accepted 2026-08-26 |
| [0025](#adr-0025--oracle-is-a-resident-service-the-window-is-a-client) | ORACLE is a resident service; the window is a client | accepted 2026-08-26 |
| [0026](#adr-0026--the-local-tier-ladder-is-capability-shaped-and-gpu-conditional) | The local tier ladder is capability-shaped and GPU-conditional | accepted 2026-08-26, **conditions 0004** |

---

## ADR-0001 — Orchestrator, not a monolithic agent

**Context.** The obvious design is "one capable LLM with tools". On this hardware the best locally
runnable model is ~2–4B, which is not capable enough to be the whole system.

**Options.** (a) One local model does everything. (b) One cloud model does everything. (c) A local
orchestrator that routes to the cheapest competent executor.

**Chosen.** (c).

**Why.** (a) fails on capability — a 2B model cannot debug an auth system. (b) fails on the stated
local-first requirement, costs money per turn, and makes the machine useless offline. (c) plays to
each component's strength: small models are genuinely good at *classification* (what kind of request
is this, which tool, which project), which is exactly what routing needs, and that skill degrades far
more gracefully than reasoning does.

**Trade-offs.** More moving parts. Routing errors are a new failure class. Latency of a delegated task
is bounded by the external agent, not by us.

**Consequences.** The router model's quality bar is "reliable classifier", not "good programmer" —
which makes the 4 GB VRAM constraint survivable. Context assembly becomes critical, because the value
we add is *what we send*, not *what we compute*.

---

## ADR-0002 — Python 3.12 managed by `uv`

**Context.** The machine has Python 3.14.6 and 3.10 installed. The stack needs `onnxruntime`,
`ctranslate2`, `pywinpty`, `tree-sitter` grammars — all native-wheel dependencies.

**Options.** (a) System 3.14. (b) System 3.10. (c) uv-managed standalone 3.12. (d) conda.

**Chosen.** (c).

**Why.** 3.14 is recent enough that native wheels for those four packages are a real risk; 3.10 is
ageing out. The deeper reason is isolation: with `uv`, ORACLE's interpreter is a project artifact, so
installing something unrelated next month cannot break it. On a machine whose Python situation is
already two conflicting versions, that matters more than the version number itself.

**Trade-offs.** One more toolchain concept. A duplicate interpreter on disk (~50 MB, irrelevant).

**Consequences.** `uv` is required for development. `.python-version` is committed. Never invoke `py`
or a system `python` in project scripts.

---

## ADR-0003 — Tool execution in a separate process

**Context.** The agent needs to run `git`, `npm`, `pytest`, `docker` and to read and write files,
while also holding API keys, policy and the audit log in memory.

**Options.** (a) Execute in the main process. (b) Execute in a thread pool. (c) A separate,
low-privilege `oracle-toolhost` process.

**Chosen.** (c).

**Why.** Three concrete failures that (a) and (b) cannot address on Windows: a hung or memory-hungry
tool takes down the agent and the UI with it; a tool bug can read `ANTHROPIC_API_KEY` out of the
process environment; and killing a thread does not kill `npm install`'s grandchildren, so cancellation
and HALT would be unreliable — the one control that must never be unreliable. A Job Object around a
separate process makes tree termination a guarantee.

**Trade-offs.** IPC latency (budgeted < 50 ms), serialisation, a supervision/restart path to build.

**Consequences.** The toolhost receives a pre-authorised `ToolInvocation` and nothing else — no
policy, no secrets it wasn't handed, no way back into the runtime. It can later be hardened further
(restricted token, AppContainer, separate user) without touching agent code.

### Confirmed against the implementation  `2026-08-21`

All three justifications were re-checked once tools that actually spawn processes existed, because
the original argument was made before any of them did.

| claim | measured |
|---|---|
| killing a thread does not kill grandchildren | HALT terminates child, its child **and its grandchild** — all three go false together |
| a tool cannot read `ANTHROPIC_API_KEY` | the child gets a constructed env and **refuses to start** if a key is present; enforced from both sides |
| IPC cost < 50 ms | **p50 27.9 ms**, p95 29.0 ms warm; 1.3 s cold, so the host is pre-warmed at boot |
| no orphans | 100-call soak leaves **zero** orphaned processes |

Two rules were added that the ADR implied but did not state, and both are now enforced rather than
documented:

- **The child never resolves anything.** Paths and program locations are canonicalised and pinned on
  the parent side and handed over as absolute paths. A child that could resolve a path or look up a
  program on `PATH` would put the sandbox decision on the wrong side of the pipe.
- **A spawning tool may not take the in-process path.** `ToolExecutor` refuses to run a contract
  declaring `proc.spawn` when no host is configured. Without the Job Object there is no tree
  termination, and HALT would be a lie. The in-process fallback remains, for tools that cannot spawn.

One exception was found to be necessary and is recorded separately as
[ADR-0018](#adr-0018--a-launched-application-is-not-a-tool-call).

---

## ADR-0004 — Two-tier local model (router + reasoner)

**Context.** ~3.5 GB of usable VRAM. `VERIFIED 2026-08-21`: `qwen3.5:2b` is 2.7 GB, `4b` is 3.4 GB,
`9b` is 6.6 GB. KV cache for a 2B model costs ~55 KB/token, so 16k context at fp16 is another ~0.9 GB.

**Options.** (a) One 4B model, partially offloaded. (b) One small model for everything. (c) A resident
small router plus an on-demand larger reasoner. (d) Cloud for everything.

**Chosen.** (c) — router **`qwen3.5:0.8b` at 16k context, 100% GPU-resident, `think: false`**;
reasoner `2b`/`4b`/`9b` loaded on demand.

> **Updated 2026-08-21 with measured data** ([benchmark](../logs/development/2026-08-21-oq01-router-benchmark.md)).
> The original choice here was `2b`, based on arithmetic. **That was wrong.** Measured: `2b` splits
> 36%/64% CPU/GPU at *both* 4k and 8k context — identical, so the weights are what don't fit and
> shrinking context cannot help. `0.8b` runs 100% on GPU even at 16k. The arithmetic under-counted
> Ollama's compute buffers and headroom policy. Latency: `0.8b` 45.4 tok/s vs `2b` 20.4 tok/s.

**Why.** Model *swap* time dominates inference time on this hardware: a multi-GB read from disk makes
a router that reloads per turn feel broken. Keeping a small model permanently resident is worth more
than the quality difference between 2B and 4B for classification work. Partial offload (a) is slow on
every turn rather than only on rare ones.

**Trade-offs.** Two models to manage. The reasoner is slow when used (~5 tok/s for 9B on CPU). Router
quality is a real ceiling on intent accuracy — measured at 93.3%, which is above the 85% gate but
means roughly one turn in fifteen is misrouted and must be recoverable by the user.

**Consequences.** Context length is a hardware decision, not a model decision. The Context Assembler
becomes first-class. Measured prompt-processing rates (~1300–2200 tok/s) forced the context budget to
be **split by call type** rather than set globally
([AGENT_RUNTIME.md §5](AGENT_RUNTIME.md#5-context-budget)), and made intent-based tool pre-filtering
load-bearing rather than merely tidy: ~1200 tokens of tool schemas costs ~730 ms of latency *per turn*.

**Resolved 2026-08-21:** `qwen3.5:0.8b` reaches **93.3% intent accuracy** with 0% structured-output
failures on the 30-case fixture set. The feared fallback to `2b` was not needed. Routed-turn latency
is ~1.5 s, floored by a ~600 ms Ollama per-request overhead ([OQ-15](OPEN_QUESTIONS.md#oq-15)).

---

## ADR-0005 — One policy gate, risk tiers, taint tracking

**Context.** The agent will have access to files, terminal, credentials and the network, driven by a
small model that will sometimes be wrong and by content that may be adversarial.

**Options.** (a) Confirm everything. (b) Allowlist tools and trust them. (c) A single gate computing a
risk tier from `(tool, resolved args, scope, taint)`.

**Chosen.** (c).

**Why.** (a) produces prompt fatigue, which is itself a security failure — an agent that asks forty
times a day trains me to click Approve without reading. (b) ignores that the same tool is safe or
catastrophic depending on its arguments: `write_file` into scratch and `write_file` into
`C:\Windows` are not the same act. Deciding on resolved arguments is the only correct granularity.
Taint tracking exists because prompt injection is the highest realistic risk, and nothing else in the
design addresses it.

**Trade-offs.** Policy evaluation on every call. Tier tuning needs real-world calibration. Taint
escalation may prove annoying.

**Consequences.** Reversibility beats permission: a reversible action runs automatically with a
journalled undo instead of prompting. Approval-prompt rate becomes a tracked metric with an alarm
threshold. Delegation to a cloud agent is a T2 egress event with a mandatory preview.

---

## ADR-0006 — SQLite-only storage, two files (sqlite-vec + FTS5)

**Context.** Need operational state plus a hybrid (dense + lexical) search index. Corpus measured at
**~800 files in the largest project and ~161 Obsidian notes** → roughly 30k–80k chunks.

**Options.** (a) PostgreSQL + pgvector. (b) Qdrant. (c) Chroma. (d) LanceDB. (e) SQLite + sqlite-vec +
FTS5.

**Chosen.** (e), split into `oracle.db` and a disposable `knowledge.db`.

**Why.** At this corpus size a brute-force vector scan takes tens of milliseconds — **an ANN index
solves a problem we do not have.** Every server-based option adds a service to install, run, back up
and upgrade for a single-user desktop app. Keeping vectors, BM25 and metadata in one file gives
atomic consistency, backup-by-file-copy, and reset-by-delete. The two-file split ensures a bad
chunking change can never damage session history.

**Trade-offs.** Linear scan will not scale past ~1M chunks. sqlite-vec is younger than pgvector.
Hand-written SQL and migrations instead of ORM conveniences.

**Consequences.** `VectorStore` stays an interface; **LanceDB is the designated upgrade path**.
Re-verify if the corpus grows 10×. Changing embedding dimensions requires a full reindex — cheap here,
and by design.

---

## ADR-0007 — Clients are peers of one local API

**Context.** Requirements include a desktop UI, phone control, and eventually voice.

**Options.** (a) Desktop app with the agent embedded; phone/voice as add-ons. (b) A headless backend
with all clients as equal API consumers.

**Chosen.** (b).

**Why.** This is what makes the phone and voice requirements nearly free later: adding voice becomes
"write a client", not "modify the agent". It also contains the desktop-framework risk — because the
shell holds no logic, replacing Tauri is a shell swap, not a rewrite. And it forces every capability
to have an API, which makes the system scriptable and testable.

**Trade-offs.** Everything crosses a serialization boundary, even locally. A local HTTP/WS server must
be secured even for loopback use.

**Consequences.** Zero business logic in the Tauri shell — enforced in review. The PTY lives in the
backend so the phone can attach to the same terminal. **Phase 15 (voice) acceptance includes "zero changes to
`packages/core` to add voice"**, which is the test of whether this decision actually held.

---

## ADR-0008 — Tauri 2 for the desktop shell

**Context.** Need a desktop window, tray, global hotkey (HALT), autostart, and a Python sidecar, on a
machine where RAM is contended by a language model. `VERIFIED 2026-08-21`: current stable is Tauri
2.10.1; there is no Tauri 3.

**Options.** (a) Electron. (b) Tauri 2. (c) Native (WinUI/Qt). (d) Browser only.

**Chosen.** (b).

**Why.** Tauri uses the system WebView2 (already present) instead of bundling Chromium: tens of MB of
RAM rather than hundreds. With a 32 GB budget largely spoken for by models and indexing, that is the
decisive argument. Rust is already installed; `externalBin` supervises the Python backend cleanly.
(c) is too slow to iterate on the most-iterated part of the product and would forfeit the free browser
and mobile clients. (d) has no tray, no global hotkey, no autostart.

**Trade-offs.** WebView2 rendering differs subtly from Chrome. Smaller ecosystem than Electron. Rust
in the build chain.

**Consequences.** Test in WebView2, not only in Chrome. The risk is contained by ADR-0007: if Tauri
disappoints, swap the shell. Windows-only for now, which matches the requirement.

---

## ADR-0009 — Ollama as default provider behind `LLMProvider`

**Context.** Need local inference on a Pascal GPU. `VERIFIED 2026-08-21`: Ollama supports compute
5.0–6.2 with driver ≥ 570 (ours: 582.28), while CUDA 13.0 raised the minimum to 7.5 and 13.3 removed
Pascal entirely.

**Options.** (a) Ollama. (b) llama.cpp server directly. (c) vLLM. (d) HF Transformers.

**Chosen.** (a), strictly behind an `LLMProvider` interface, with (b) as the documented escape hatch.

**Why.** Ollama gives model management, an OpenAI-compatible endpoint, JSON-schema structured output,
automatic partial offload, and — decisively — a CUDA runner that still supports this GPU. It is
already installed. (c) needs far more VRAM and its batching advantages are irrelevant at batch size 1.
(d) means a ~2.5 GB torch dependency to perform worse than llama.cpp on CPU.

**Trade-offs.** Less control than raw llama.cpp (no GBNF grammars, coarser offload control). An
external process we do not own.

**Consequences.** **Pascal support is a tracked risk** ([OQ-03](OPEN_QUESTIONS.md)); a CPU fallback
path is tested in Phase 1, not discovered later. Structured output uses JSON Schema rather than any
model's native tool-call template, so the provider stays replaceable. `FakeProvider` is mandatory for
deterministic tests.

---

## ADR-0010 — Event-sourced runtime

**Context.** Need resumable mobile clients, a real audit trail, a debuggable agent, and deterministic
tests for a non-deterministic system.

**Options.** (a) Mutable state + separate logs. (b) Append-only event log as the source of truth.

**Chosen.** (b), with a globally monotonic `seq`.

**Why.** One mechanism satisfies four requirements at once: `since_seq` reconnection makes mobile over
flaky Wi-Fi implementable at all; replaying a recorded log against a mocked tool layer gives an agent a
regression suite; "what did it do at 03:43" becomes a query; and the Activity Timeline is the log,
rendered. Building these separately would cost more and agree less.

**Trade-offs.** Storage growth (mitigated by summarising turns after 90 days). Every state change must
go through the log. Schema evolution of events needs care.

**Consequences.** `events` is the spine of `oracle.db`. Every event is classified `critical` or
coalescable for backpressure. Unknown event types must be ignored by clients, so old clients survive
server updates.

---

## ADR-0011 — Deterministic pre-router before the model

**Context.** The router model costs latency and can be wrong. Many requests are unambiguous.

**Options.** (a) Everything goes to the model. (b) Deterministic matching first, model as fallback.

**Chosen.** (b): slash commands, palette actions, saved pipelines and exact tool syntax bypass the LLM
entirely.

**Why.** A turn handled here has zero model latency, zero hallucination risk and zero token cost. It
is the single highest-leverage performance and reliability decision in the design, and it is what
makes a 2B model viable as the primary interface. It also gives a **usable degraded mode**: with
Ollama down, commands and search still work.

**Trade-offs.** Two dispatch paths to maintain. Users must learn commands to get the benefit — which
is why the command palette ships in the MVP rather than at Phase 11.

**Consequences.** Target: >50% of daily turns resolved without the model, tracked as a metric. If it
is low, the answer is more palette actions and pipelines — not a bigger model.

---

## ADR-0012 — Git-worktree delegation with a vendor-neutral fallback

**Context.** Delegating to Claude Code or Antigravity means an external process editing my real
projects, reporting its own success. `VERIFIED 2026-08-21`: Antigravity's CLI has an open bug where
`-p` drops stdout when not attached to a TTY — exactly how ORACLE would invoke it.

**Options.** (a) Let the agent work in the project directory and trust its report. (b) Worktree
isolation with independent verification. (c) Only ever hand the user a prompt to paste.

**Chosen.** (b), with (c) as a first-class fallback.

**Why.** An agent claiming "tests pass" is a claim; ORACLE running the tests in the worktree is
evidence, and evidence is what gets reported. Isolation also makes a bad run free to discard — remove
the worktree and branch, and the real working tree was never touched. The fallback matters because
vendor CLIs change and break: a packet written to disk works with any agent, including ones that do
not exist yet.

**Trade-offs.** Worktrees need disk and cleanup. Projects without git (GrowAMonster, MonsterGarden)
need a snapshot-copy path. The fallback needs manual steps.

**Consequences.** `ai.build_packet` is split from `ai.delegate` so "show me what you'd send" is free
and safe. Egress preview is mandatory before any bytes leave. The Antigravity adapter is not written
until [OQ-05](OPEN_QUESTIONS.md) is settled — a documented non-integration beats a flaky one.

---

## ADR-0013 — Deterministic SVG orbit, no force simulation

**Context.** The central visualisation must show state across < 40 nodes, and must be functional
rather than decorative.

**Options.** (a) d3-force simulation. (b) React Flow. (c) WebGL/PixiJS. (d) SVG with a deterministic
polar layout.

**Chosen.** (d) — ring = category, angle = stable hash of node id, radius = recency.

**Why.** Force simulations move nodes between renders, so nothing is ever where it was last time; that
destroys recognisability and turns the view into decoration. A stable angle means Asterim is *always*
in the same place, which is what makes glanceability possible. SVG also stays themeable by the same
CSS variables and accessible via the DOM, which WebGL forfeits.

**Trade-offs.** Manual layout maths. SVG would struggle past a few hundred nodes — irrelevant here.

**Consequences.** `d3-scale`/`d3-shape` as pure maths helpers only; no `d3-selection`. The orbit ships
at Phase 11 with an explicit test: **cover every label and you must still be able to say what ORACLE is
doing.** If it fails, it gets cut, and that outcome is recorded as an ADR rather than quietly ignored.

---

## ADR-0014 — Embeddings on CPU, GPU reserved for the router

**Context.** 4 GB VRAM, an always-resident router model, and an indexing job that also wants a model.

**Options.** (a) Embeddings on GPU. (b) Embeddings on CPU. (c) Share the GPU with load/unload.

**Chosen.** (b) — ONNX Runtime on CPU.

**Why.** (c) causes constant multi-GB load/unload thrash that costs far more than the embedding
compute saves, and makes interactive latency unpredictable — the router stalling because an index job
evicted it is the worst possible failure mode. Meanwhile 24 CPU threads sit idle. Indexing is a
background batch job where latency does not matter; keeping the GPU warm is what does.

**Trade-offs.** Slower indexing than a GPU could manage (acceptable: full index budget is 10 minutes).

**Consequences.** ONNX Runtime rather than torch, avoiding a ~2.5 GB dependency. Embedding runs in a
separate low-priority process. Matryoshka truncation to 384d is worth evaluating ([OQ-02](OPEN_QUESTIONS.md)).

---

## ADR-0015 — Intent-shaped tools, no general shell

**Context.** The initial sketch listed `execute_command` alongside `git_status`, `npm`, `terminal`.

**Options.** (a) A general shell tool. (b) Only specific tools. (c) Specific tools plus a narrow,
allowlisted execution escape hatch.

**Chosen.** (c).

**Why.** If the model can reach a general shell, every narrower tool is decorative and the policy
engine is reduced to guessing about shell strings — there is no way to assign a meaningful risk tier
to arbitrary text. Specific tools give a precise schema, a precise tier, a precise undo, and typed
results instead of scraped stdout. But a pure allowlist would be too rigid for real work, hence a
gated `dev.execute` that takes `{program, args[]}` against a program allowlist — never a shell string,
never `shell=True`.

**Trade-offs.** More tools to write. Some tasks need a new tool rather than an ad-hoc command.

**Consequences.** Hard cap of ~40 tools (they consume the router's context every turn). Tool
descriptions are golden-tested, because drift degrades selection accuracy invisibly. `keyboard`/`mouse`
synthesis is excluded from the MVP entirely — it is unscopeable by nature.

---

## ADR-0016 — MVP excludes the interesting parts

**Context.** The vision includes RAG, agent delegation, pipelines, mobile, voice and an orbital UI.
All are more interesting to build than a policy engine.

**Options.** (a) Build the exciting capabilities first. (b) Build the boring foundation first and ship
a narrow but complete product.

**Chosen.** (b). The MVP is Phases 0–4: local chat, guarded tools, desktop UI, terminal, approvals,
logging. Knowledge, delegation, pipelines, mobile, voice and the orbit are all Post-MVP.

**Why.** Two hard constraints. First, nothing may execute a side effect before the policy gate exists —
retrofitting security is how this class of project ends up an unrestricted shell wrapper. Second, a
personal tool that is not used daily never gets finished, so the MVP must be genuinely usable, which
means a real UI at Phase 4 rather than Phase 11. The interesting features are also the easiest to start
and the easiest to leave at 80%; they attach to a working product rather than substituting for one.

**Trade-offs.** The most motivating features are deferred. Phases 0–2 produce little visible progress.

**Consequences.** The MVP definition in [ROADMAP.md](ROADMAP.md#the-mvp-stated-once) is the
scope-creep test — anything not required by that paragraph is not in the MVP. ~~Definition of done for
Phase 4 is behavioural: **a full working day using ORACLE without opening a terminal manually.**~~

> **Amended 2026-08-22.** That behavioural criterion is retired; Phase 4's definition of done is its
> acceptance list. The record is struck rather than deleted because the reasoning that produced it —
> "a personal tool that is not used daily never gets finished" — still holds and still drives this
> ADR. What was wrong was the *measure*, not the motive: the terminal is a first-class surface in
> this design, so using one was never failure, and "a full working day" is not an experiment anyone
> actually runs. See the note under Phase 4 in [ROADMAP.md](ROADMAP.md).


---

## ADR-0017 — Constrain what the decoder can enforce

**Context.** Structured output relies on JSON-Schema-constrained decoding. The first fixture run
produced a 27.9% structured-output failure rate and 23.3% intent accuracy, which looked like the 0.8B
model being hopeless.

**It was a schema bug.** `confidence: float = Field(ge=0.0, le=1.0)` renders as `minimum`/`maximum`,
and **Ollama's constrained decoding ignores numeric ranges**. The model emitted `95` (meaning 95%) and
failed pydantic validation on 12 of 30 cases. Enums *are* enforced, at the token level.

**Options.** (a) Keep the float, coerce out-of-range values on our side. (b) Keep the float and lean on
the repair attempt. (c) Express the field as an enum the decoder can actually enforce.

**Chosen.** (c). `Literal["high","medium","low"]`, mapped to a score for thresholding.

**Why.** Coercion hides a model that does not understand the field. Repair costs a full extra round
trip — ~1.5 s here — to fix something the schema should have prevented. And a three-value enum is a
more honest ask: a 0.8B model has no calibrated notion of 0.73, and we only ever compare to a
threshold. The change alone took structured failures 27.9% → **0%**.

**Trade-offs.** Coarser confidence. Some constraints cannot be expressed as enums and must be
validated after the fact.

**Consequences.** A rule for all schemas in this codebase: **express constraints the decoder can
enforce — enums, required fields, types. Never depend on `minimum`, `maximum`, `pattern` or
`minLength`; validate them, but do not rely on them.** Also a general lesson recorded in
`logs/development/2026-08-21-p1-router-accuracy.md`: when a small model looks catastrophically bad,
suspect the contract before the model.


---

## ADR-0018 — A launched application is not a tool call

**Context.** [ADR-0003](#adr-0003--tool-execution-in-a-separate-process) puts every tool inside a Job
Object with `KILL_ON_JOB_CLOSE`, so HALT can terminate a whole process tree. `app.launch` breaks that
model on its own terms: an editor the user asked ORACLE to open must **outlive** the tool call, the
turn, a toolhost restart, and HALT itself. HALT means "stop what you are doing", not "close my editor
with unsaved work in it" — and the toolhost restarts on every crash and every timeout.

**Options.**
(a) Launch from the toolhost and accept that the app dies with it.
(b) Add `JOB_OBJECT_LIMIT_BREAKAWAY_OK` to the job and use `CREATE_BREAKAWAY_FROM_JOB`.
(c) Shell out to `explorer.exe` so the Windows shell re-parents the process.
(d) Launch from the parent, detached, as a single narrow exception.

**Chosen.** (d).

**Why.** (a) is a data-loss bug wearing a security hat. (b) is the dangerous one: breakaway is a
property of the **job**, not of one call, so anything the child spawns could then escape HALT —
trading the containment guarantee for the ability to open Explorer is not a trade. (c) is
`ShellExecute` by another name: it cannot pass arguments, and it widens what can be started to every
file association on the machine.

**Trade-offs.** One tool runs in the process that holds the API key. That is a real cost and the
reason the exception is drawn as narrowly as it is.

**Consequences.** The exception is one shape, and the registry enforces it rather than trusting
review: a contract with an `app_field` runs in the parent and **may not also name an allowlisted
program**. Within it:

- the executable is pinned from `config/apps.yaml` and the model supplies an **alias**, never a path;
- the environment is the same constructed one the toolhost child gets, so the API key is absent
  rather than merely unused;
- the launch is detached — no pipes, no console, never waited on — and returns a pid and nothing else.

`term.*` is the deliberate mirror image: a shell **does** live in the toolhost, because a runaway
`npm install` is exactly what HALT exists to stop. Nobody has unsaved work in a spinning install;
everybody has unsaved work in an editor.

There is a security test that kills the toolhost's entire process tree and asserts the launched
window is still running.

---

## ADR-0019 — The supervisor completes the orchestrator

*(Full title: the supervisor completes the orchestrator; planning is a delegated role.)*

**Context.** The 2026-08-24 replan (`logs/development/2026-08-24-supervisor-replan.md`) asked for
a multi-agent supervisor architecture: ORACLE as deterministic runtime, Antigravity as high-level
planner, Claude as specialist worker, local models as lightweight workers. The audit found that
ADR-0001's orchestrator already *is* most of this — gate, adapters, delegation, MCP callback,
egress preview all built and verified — and that the genuinely missing pieces are multi-task
orchestration and any planning capability at all: the shipped runtime is one turn, one tool, one
delegation, by design (`router/pipeline.py:15-19`), and the multi-step `Plan` schema in
AGENT_RUNTIME.md §4 was never implemented.

**Options.** (a) Grow the local model into the planner (the never-built §4 plan loop). (b) Make
Antigravity the runtime: it plans *and* supervises execution. (c) Keep ORACLE as the sole
deterministic supervisor and add planning as a **worker role** behind the existing adapter seam,
with a fallback ladder.

**Chosen.** (c).

**Why.** (a) asks a 0.8–4B model for the one thing it measurably cannot do; the project's whole
shape exists because classification and planning are different skills. (b) puts an external vendor
inside the trust boundary: the component that schedules work, holds state and fronts the gate must
be deterministic, testable and local, or every security property becomes a claim about a cloud
model's behaviour. (c) reuses everything: a planner is invoked exactly like any delegate — packet,
egress preview, structured output, adapter — so the planner tier costs one adapter plus one schema,
and losing the vendor degrades to the shipped Phase-6 behaviour rather than to nothing.

**Trade-offs.** Plan quality depends on an external agent and on context assembly. Planning adds a
previewed egress (latency + a prompt) before multi-task work. Two cloud vendors instead of one on
the default path.

**Consequences.** ORACLE stays the only component that: creates tasks, applies policy, schedules,
starts/stops workers, verifies results, and reports. The planner returns data
([PLANNER.md](PLANNER.md)); Antigravity holds the role by default and is replaceable via the
capability registry; the fallback ladder (Claude → deterministic templates → single-task →
human-provided) is part of the design, not an error path. ADR-0001 is extended, not superseded.
AGENT_RUNTIME.md §4's unimplemented in-turn planner is superseded by this + ADR-0020 and will not
be built as written.

---

## ADR-0020 — The task graph is a durable DAG with append-only replanning

**Context.** Multi-task orchestration needs a structure. The replan brief asked whether a DAG
suffices or a general state machine is required. `task_id` already exists on events; the only task
producer is `DelegationService`, linear and singular. Asterim ships a tested pure-function DAG
algebra and a delegation state vocabulary (`ASTERIM_REUSE.md`).

**Options.** (a) A general graph-rewriting workflow engine (LangGraph-shaped). (b) A DAG executed
by a topological scheduler, with per-task state machines, where dynamism is expressed by
**appending** superseding tasks. (c) No graph: keep chaining single delegations by hand.

**Chosen.** (b).

**Why.** Every dynamic behaviour ORACLE needs — retry, replan, escalate, ask a human — is an
*append*: new tasks that `supersede` failed ones. Appending preserves history, which the
event-sourced runtime (ADR-0010) requires anyway, keeps the scheduler a ~50-line loop over a pure
ready-set function, and makes the UI's execution tree a query rather than a data structure.
Graph rewriting buys expressiveness this project has no requirement for, at the cost of the
property that makes the audit log trustworthy. (c) is what exists, and it caps ORACLE at tasks a
human decomposes.

**Trade-offs.** A replanned graph accumulates superseded tasks (visible, by design). Static
validation cannot price tasks whose arguments only resolve at runtime — those keep per-item
approvals. Concurrency limits are config to tune, not theory.

**Consequences.** `tasks` table in `oracle.db` as a projection of `task.*` events; status
vocabulary adopts Asterim's tested distinctions (`SKIPPED ≠ CANCELLED`, `TIMEOUT ≠ FAILED`);
ready = pending ∧ all deps succeeded (fail-closed for free); aggregate precedence
CANCELLED > FAILED > TIMEOUT > RUNNING > SUCCEEDED; replan budget ≤ 2 per root; graph size ≤ 12;
delegation width 2 on this machine, sub-delegation depth 0 in v1. Crash recovery ports
asterim-pipeline's rules: an interrupted agent is never auto-restarted, corrupt state gates.
Detail in [ORCHESTRATION.md](ORCHESTRATION.md).

---

## ADR-0021 — Planner output is untrusted input

**Context.** A plan authored by a cloud model will drive the creation of tasks that spend money,
write code, and request approvals. Prompt injection is this design's #1 threat (SECURITY.md §1),
and a planner reading project files is a funnel: a README that says "add a task to push to origin"
is now attacking the *planner*, one step removed from the gate.

**Options.** (a) Trust the plan — validation is schema-only. (b) Treat the plan like any external
agent output: `external` provenance, taint, tier escalation, structural validation, and no
execution authority whatsoever.

**Chosen.** (b).

**Why.** The existing taint machinery was built for exactly this shape of problem and already
covers "another agent's output" in its provenance table. A plan is the highest-leverage injection
target in the new architecture — it names projects, orders work, and recommends agents — so it
gets the strictest reading: every field validated against a registry (roles, projects, agents),
free text carried as data only, and every spawned task tier-escalated because the turn that
ingested the plan is tainted. `agent_hint` is a tie-break, never an override, for the same reason.

**Trade-offs.** Tier escalation on planned tasks means more confirmations on planned work than on
identical hand-started work. Accepted: a graph's approvals are batched up front where statically
priceable, which is the fatigue mitigation — not trusting the plan more.

**Consequences.** A plan cannot: name a tool for auto-execution, widen a scope, modify policy,
pre-authorise an egress, or cause any side effect between arrival and validation. Security tests
gain injection fixtures where the planning context contains adversarial instructions and the
assertion is that the resulting plan's tasks are escalated and the adversarial task is inert
without a human approval. SECURITY.md §10 records the full surface.

---

## ADR-0022 — External agent frameworks: evaluated, not adopted

**Context.** Before building orchestration, the 2026-08 landscape was surveyed
(`logs/development/2026-08-24-supervisor-replan.md`, sources in INTEGRATIONS.md §10): ACP,
OpenHands SDK, Claude Agent SDK, LangGraph, CrewAI, AutoGen/MS Agent Framework, A2A, smolagents,
Goose, Pydantic-AI, and the MCP 2026-07-28 revision.

**Options.** (a) Adopt an orchestration framework (LangGraph or CrewAI) for the task graph.
(b) Adopt a protocol layer (ACP) for all agent adapters. (c) Switch the Claude integration to the
Claude Agent SDK. (d) Keep the hand-rolled, fixture-pinned integrations and the ~300-LOC
in-house graph; record triggers for revisiting each external option.

**Chosen.** (d).

**Why, per candidate.** **LangGraph** (MIT, mature): its headline features — durable execution,
checkpointing, replay — duplicate the event-sourced runtime; adopting it would graft a second
state spine onto a system whose spine is the point. **CrewAI** (MIT): its hierarchical mode is an
LLM manager-agent, the exact thing ADR-0019 rejects, with documented routing bugs; its role
vocabulary is referenced in PLANNER.md §4. **ACP** (Apache-2.0): the protocol fits the
supervisor/agent shape, but Claude and Antigravity both need Node adapter shims today — a process
hop and a dependency to reach agents ORACLE already reaches natively; right answer for a future
third-party agent, wrong answer for the two that exist. **Claude Agent SDK** (0.x): typed events
and PreToolUse hooks are genuinely better than stream parsing, but they replace a *working,
pinned, tested* contract with a moving API — re-evaluate at the next contract drift (OQ-19).
**OpenHands SDK** (MIT): the closest reference architecture; adopting its agent-server would
duplicate the runtime. **AutoGen**: maintenance mode. **A2A**: peer-agent interop, out of scope.
**MCP 2026-07-28**: the hand-rolled server speaks `2025-06-18`; migrate when a client requires it
(OQ-21), per the standing "take the SDK when a client rejects the surface" rule.

**Trade-offs.** ORACLE keeps maintaining its own adapters and graph code. Vendor CLI drift stays a
quarterly re-verification cost. If ACP adoption becomes universal, ORACLE arrives late with an
adapter it could have had early — accepted, because the adapter seam makes that adapter cheap
whenever it becomes worth having.

**Consequences.** No new framework dependencies in Phases 7–8. The dependency ledger
(TECH_STACK.md) gains nothing from this ADR — which is the decision. Licensing review recorded:
every surveyed candidate is MIT or Apache-2.0; no copyleft exposure exists on any considered path.
Triggers to revisit: OQ-19 (Agent SDK), OQ-21 (MCP migration), a third-party agent worth
integrating (ACP), corpus/scale shifts (never for the graph — it is pure functions).

---

## ADR-0023 — The knowledge graph is simulated-then-frozen, canvas-rendered

**Context.** The owner wants an interactive map of everything ORACLE knows — Obsidian vaults,
project docs, PDFs — as a Phase 11 view ([UI.md §11b](UI.md#11b-the-knowledge-graph--phase-11)),
with cluster-graph and film-HUD design references. The data exists: `knowledge.db` documents,
embeddings, and the `links` table of extracted wikilinks. The friction is
[ADR-0013](#adr-0013--deterministic-svg-orbit-no-force-simulation), which rejected force
simulation and WebGL for the orbit — and this view cannot simply inherit that ruling, because it
is a different problem: ~1,300 nodes today (ceiling 10k) versus the orbit's < 40, and **cluster
adjacency is the information being displayed**, which no hash-angle layout can produce.

**Options.**
(a) Live force simulation in the viewport, Obsidian-style.
(b) Extend ADR-0013's deterministic geometric layout to the corpus.
(c) Force layout computed **offline** (in the indexing worker), positions **persisted** in
`knowledge.db` and rendered frozen; incremental placement for new documents; re-layout as an
explicit action. Canvas rendering with a DOM overlay for focused elements.

**Chosen.** (c).

**Why.** ADR-0013's *argument* was never "force layouts are bad" — it was that positions which
change between renders destroy recognisability, and that idle animation burns CPU to say nothing.
Both concerns are answered by freezing: the simulation runs once, off the interactive path, and
the map becomes as stable as the orbit — the vault sits where it sat last month, which is what
builds spatial memory. (a) fails exactly where the orbit's rejection said it would, at 10× the
CPU. (b) fails the purpose: a layout that ignores the edges cannot show clusters, hubs, bridges
or orphans, and those four are the view's entire justification. On rendering, ADR-0013 itself
noted SVG struggles past a few hundred nodes while waving it off as irrelevant at 40 — at 1,300
it is relevant, so canvas is permitted **for this view only**, with the accessibility cost paid
explicitly: a full list-view equivalent (the orbit's rule) plus DOM overlays for everything
focusable.

**Trade-offs.** Persisted positions are one more thing the index migration must carry — accepted;
they are rebuildable, like everything in `knowledge.db`. An explicit re-layout occasionally asks
the user to resettle their mental map — better chosen than suffered. Canvas forfeits free DOM
semantics — mitigated as above, and gated by the axe audit like every surface.

**Consequences.** ADR-0013 is **scoped, not superseded**: the orbit keeps its deterministic polar
layout and SVG; this ADR governs the knowledge graph alone, and a third visualisation would need
its own argument. Layout runs beside indexing and respects its budgets; the viewport never
simulates; semantic (embedding-kNN) edges are computed offline and default off. The rendering and
layout budgets are measured at Phase 11 under [OQ-22](OPEN_QUESTIONS.md#oq-22) before the view is
built on them. The view inherits the orbit's go/no-go honesty gate: if it does not answer
questions the list cannot, it is cut, and that outcome gets an ADR.

---

## ADR-0024 — A project is a first-class persistent entity

**Context.** [VISION.md §2](VISION.md#2-the-day--the-acceptance-test) makes *"continue Asterim"* the
product's headline utterance. Today a project is a directory name produced by
`core/projects.py:discover_projects()` and validated against the intent classifier so a hallucinated
name cannot become a filesystem path. `memory_facts`, `memory_attempts` and `TaskSpec` are all
*keyed* by a project string, but there is no entity those keys refer to — nothing records what a
project is, what was last done to it, what remains open, or what it cost. The sidebar mock in
[UI.md §4](UI.md#4-sidebar) already draws numbers that no subsystem can produce.

**Options.** (a) Leave projects derived; answer "continue" by handing the planner a directory
listing. (b) Persist a full project model including git state, build commands and file inventory.
(c) Persist only what ORACLE itself knows, and read everything git knows on demand. (d) Adopt a
project-management schema (boards, assignees, estimates).

**Chosen.** (c) — a `projects` table holding **relational state only**, with **observed state read
fresh through the tool layer** on every use. Design in [PROJECT_STATE.md](PROJECT_STATE.md).

**Why.** (a) fails on the measurement that matters: a planner given a project name and no state
produces plausible work, and plausible work is unfalsifiable — it costs a worktree and a delegation
to discover it was invented. (b) creates a cache that lies: a stored branch name is wrong the moment
I switch branches in my editor, silently, with no event to correct it — and `git status` on a warm
repository costs single-digit milliseconds, so the cache buys nothing and forfeits correctness. This
is the same reasoning that put the event log rather than a projection at the centre of the runtime
([ADR-0010](#adr-0010--event-sourced-runtime)). (d) solves a problem nobody has: the unit of work is
already a task in the graph.

The governing rule: **if git knows it, do not store it; if only ORACLE knows it, store it.**

**Trade-offs.** Observing N projects for a briefing is N git calls through the toolhost process
boundary — a fan-out that is measured, not assumed (`EXPERIMENT NEEDED` in
[PROJECT_STATE.md §4](PROJECT_STATE.md#4-observed-state-the-reader)); if it misses the glance
budget the answer is lazy per-row observation, never a cache. Denormalised counters on the row are
a second copy of facts in `tasks` — accepted because the briefing has a 3–5 second budget, and
bounded by the rule that they are a **projection**: recompute is always correct, the counter is
never authoritative.

**Consequences.** Migration `0005`, plus an index on `tasks(project, status)` that does not exist
today. `discover_projects()` becomes a *candidate* source; registration is an explicit human act, so
`New folder` and `docs.zip` do not appear in the briefing. Project identity is the row id, not the
directory name, so renaming a directory does not orphan its facts and attempts. A new `continue`
intent label is required, and adding a label to a **measured** surface (93.3% intent accuracy over a
30-case fixture set) means re-running that eval rather than assuming it holds. **Registering a
project widens no policy scope** — scopes stay in `config/policy.yaml` where a human edits them and
git records it, asserted in `tests/security/`; otherwise "discover projects" would be privilege
escalation with a friendly name. Repository task documents (`TODO.md`, `current_task.md`) are read
as `local_foreign` evidence that taints the turn and never as instructions
([SECURITY.md §6](SECURITY.md#6-prompt-injection-and-taint-tracking)).

---

## ADR-0025 — ORACLE is a resident service; the window is a client

**Context.** [VISION.md §2](VISION.md#2-the-day--the-acceptance-test) opens with *"I turn on the PC.
ORACLE is already running. I did not launch it."* Today `oracled` is started by hand and the UI is
started by hand. Autostart appears in the repository only as a *justification for choosing Tauri*
([TECH_STACK.md §5](TECH_STACK.md#5-desktop-shell),
[ADR-0008](#adr-0008--tauri-2-for-the-desktop-shell)) — a capability cited, never a subsystem
designed. Meanwhile [ADR-0007](#adr-0007--clients-are-peers-of-one-local-api) already establishes
that clients are peers of one local API and the shell holds zero business logic.

**Options.** (a) Autostart the Tauri shell, which supervises the Python backend as a sidecar.
(b) Autostart `oracled` as a Windows service or scheduled task; the window is an ordinary client
that may or may not be open. (c) Keep manual start and treat residency as a later concern.

**Chosen.** (b).

**Why.** (a) makes the window the thing that is resident, which contradicts ADR-0007 and produces a
specific bad behaviour: closing the window would stop the work. If ORACLE is only running while I am
looking at it, then "it keeps working while I do something else" is false and the briefing has
nothing to brief. (b) makes the *state holder* resident and the *view* optional, which is the same
split that already makes mobile and voice cheap. (c) is what is happening now; it is listed to
record that it was rejected rather than deferred, because every other item in the vision's morning
sequence depends on this one being true.

The sidecar mechanism is not lost — it inverts. The shell, when it starts, attaches to a running
`oracled`; if none is running it may start one. That is a strictly larger set of working
configurations than the sidecar arrangement, and it is what the browser client already requires.

**Trade-offs.** A background service on Windows is harder to observe than a window: a crash at 04:00
is invisible until someone looks. Mitigated by the health surface and the briefing — a service that
died is *itself* the first line of the next briefing. Autostart also means ORACLE holds a GPU-
resident model and two SQLite handles from boot; on a 32 GB / 4 GB-VRAM machine that is a real cost
and the service must therefore start **degraded-capable** (Ollama absent is a supported state today,
[ARCHITECTURE.md §8](ARCHITECTURE.md#8-degradation--what-happens-when-a-piece-is-missing)) rather
than eagerly loading everything.

**Consequences.** Boot is a designed sequence with a health phase, not an `uvicorn` invocation.
**No interrupted worker is ever auto-resumed** — the existing recovery rule stands and is
load-bearing here: a prior agent still alive gates, an agent gone mid-run gates
([ASTERIM_REUSE.md](ASTERIM_REUSE.md)); "resume safe background work" means ORACLE resumes, not the
agent. The briefing needs a resume pointer, which is `briefed_through_seq` on the project row
(ADR-0024) rather than a new mechanism, because the event log's `seq` is already global and gap-free.
HALT must work against a service with no window open, which the global hotkey already implies. The
boot animation budget is set at ~400 ms and stated as a rule: it is a tax paid every morning, and
the correct length of an animation seen 3,000 times is one that is not noticed.

---

## ADR-0026 — The local tier ladder is capability-shaped and GPU-conditional

**Context.** The owner has stated an intent to run ~14B and ~27B local models "once the new GPU
arrives". [ADR-0004](#adr-0004--two-tier-local-model-router--reasoner) is a direct consequence of
this machine's **4 GB of VRAM** (GTX 1050 Ti, Pascal): `qwen3.5:0.8b` beat `2b` by measurement
because `2b` splits 36/64 CPU/GPU at every context length, embeddings were pushed to CPU
([ADR-0014](#adr-0014--embeddings-on-cpu-gpu-reserved-for-the-router)), context length became a
hardware decision, and tool pre-filtering became load-bearing because ~1,200 tokens of tool schemas
costs ~730 ms of latency per turn. A three-tier local stack has sat in the roadmap's idea backlog,
unscheduled, since 2026-08-23. **No GPU model, VRAM figure or date has been stated.**

**Options.** (a) Add the tiers to the routing config now, gated by availability. (b) Re-open
ADR-0004 as soon as hardware lands and re-measure the whole local stack. (c) Design the *routing
abstraction* now — capability tiers with a selection rule — and schedule the *model choices* as a
measured spike conditional on hardware. (d) Skip local mid-tiers; escalate to Claude.

**Chosen.** (c).

**Why.** The tiers in the vision are described by **capability**, not by parameter count — "trivial
extraction", "summarisation", "private work". That is the durable part and it can be designed today:
it is the same shape as the existing capability registry, which already selects an agent by role
rather than by vendor ([PLANNER.md §5](PLANNER.md#5-agent-selection)). The parameter counts are the
perishable part. (a) is the trap: writing `qwen3:14b` into a config against unknown hardware is an
assumption wearing a configuration's clothes, and ADR-0004's own history is the argument — the
original choice there was `2b` "based on arithmetic", and **that was wrong**, corrected only by
measurement. (d) forfeits the entire local/private lane, which is a stated product requirement, and
sends summarisation of my own notes to a cloud API.

**Bigger is not better per task.** A 27B model that must be swapped from disk is *worse* than a
resident 0.8B for routing, because on this class of hardware model swap time dominates inference
time — that is ADR-0004's central finding and it survives any GPU upgrade. Tier selection is
therefore a function of (task shape, residency, privacy), never of "which model is smartest".

**Trade-offs.** Designing an abstraction before its implementers exist risks designing the wrong
one. Bounded by keeping it identical in shape to the agent registry that already works, and by
refusing to name models until they can be measured. The local tiers remain unscheduled work, which
means the vision's "local 14B summarising a report" stays aspirational and every document that
mentions it says so.

**Consequences.** ADR-0004 is **not superseded** — it stands, and it stands *conditionally*: its
context section is the 4 GB machine, and it must be re-opened, not amended, when that changes. A
hardware change re-opens at minimum: which model routes, whether embeddings return to the GPU
(ADR-0014), whether the context budget is still split by call type, and whether a local model
becomes a planner-ladder candidate above deterministic templates — **evaluated with the same
`ExecutionPlan` fixtures used for [OQ-20](OPEN_QUESTIONS.md#oq-20), not by impression.** Until a GPU
is stated, the tier phase carries an `ASSUMPTION` marker and is not scheduled. The
`LLMProvider` seam already accommodates a second provider (LM Studio, llama.cpp) without touching
callers, so no refactor is pre-emptively required.
