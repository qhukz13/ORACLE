# ORACLE — Architecture Decision Records

Every significant decision, with the reasoning that produced it. **Do not deviate silently.** To
change a decision, add a new ADR that supersedes the old one and update the `Status` line of both.

Format per record: Decision · Context · Options · Chosen · Why · Trade-offs · Consequences.

| # | Decision | Status |
|---|---|---|
| [0001](#adr-0001--orchestrator-not-a-monolithic-agent) | Orchestrator, not a monolithic agent | accepted |
| [0002](#adr-0002--python-312-managed-by-uv) | Python 3.12 managed by `uv` | accepted |
| [0003](#adr-0003--tool-execution-in-a-separate-process) | Tool execution in a separate process | accepted, **confirmed in implementation** |
| [0004](#adr-0004--two-tier-local-model-router--reasoner) | Two-tier local model (router + reasoner) | accepted, benchmarked |
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
backend so the phone can attach to the same terminal. **Phase 10 acceptance includes "zero changes to
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
is why the command palette ships in the MVP rather than at Phase 9.

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
at Phase 9 with an explicit test: **cover every label and you must still be able to say what ORACLE is
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
means a real UI at Phase 4 rather than Phase 9. The interesting features are also the easiest to start
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
