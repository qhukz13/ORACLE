# ORACLE — Open Questions, Assumptions & Experiments

Everything the design does **not** know. Nothing here is hidden inside another document as a
confident-sounding sentence.

**Markers:** `UNKNOWN` (nobody has established this) · `ASSUMPTION` (proceeding as if true, unverified)
· `TO VERIFY` (cheap to check, just check it) · `EXPERIMENT NEEDED` (requires building a spike).

**Rule:** an item blocking a phase is resolved *inside* that phase, before the code that depends on
it. Resolving an item means: run it, record the result in `logs/development/`, update the affected
doc, delete the marker.

| # | Question | Marker | Blocks | Status |
|---|---|---|---|---|
| [OQ-01](#oq-01) | Which router model actually fits and performs? | ~~`EXPERIMENT NEEDED`~~ | Phase 1 | **RESOLVED 2026-08-21 — 0.8b, 93.3% accuracy** |
| [OQ-02](#oq-02) | Which embedding model for mixed RU/EN? | `EXPERIMENT NEEDED` | Phase 5 | open |
| [OQ-03](#oq-03) | How long will Pascal keep GPU acceleration? | `UNKNOWN` | risk, not a phase | monitoring |
| [OQ-04](#oq-04) | Does `realpath` resolve Windows junctions? | ~~`TO VERIFY`~~ | Phase 2 | **RESOLVED 2026-08-21 — yes; but `is_symlink()` lies** |
| [OQ-05](#oq-05) | Does `agy -p` emit stdout when piped? | ~~`EXPERIMENT NEEDED`~~ | Phase 6 (Antigravity only) | **RESOLVED 2026-08-21 — yes, with `--output-format`** |
| [OQ-06](#oq-06) | Can a PWA install over a self-signed cert? | `TO VERIFY` | Phase 8 (push only) | open |
| [OQ-07](#oq-07) | Is the memory subsystem dual- or quad-channel? | `UNKNOWN` | CPU-fallback planning | open |
| [OQ-08](#oq-08) | Does FTS5 `unicode61` handle Russian acceptably? | `TO VERIFY` | Phase 5 | open |
| [OQ-09](#oq-09) | `pywinpty` on Python 3.12 + ConPTY behaviour | ~~`TO VERIFY`~~ | Phase 3 | **RESOLVED 2026-08-21 — works; readiness must be measured, not slept** |
| [OQ-10](#oq-10) | Is there a text-only Qwen3.5 quant? | `TO VERIFY` | Phase 1 | open |
| [OQ-11](#oq-11) | Does the Tauri sidecar die with the shell? | ~~`TO VERIFY`~~ | Phase 0 | **RESOLVED 2026-08-21 — yes, via Job Object** |
| [OQ-12](#oq-12) | Is taint escalation tolerable in daily use? | `ASSUMPTION` | Phase 5+ tuning | open |
| [OQ-13](#oq-13) | What approval rate causes prompt fatigue? | `ASSUMPTION` | Phase 3+ tuning | open |
| [OQ-14](#oq-14) | Does the orbital view earn its place? | `UNKNOWN` | Phase 9 go/no-go | open |
| [OQ-15](#oq-15) | Can routed-turn latency get under ~1.5 s? | `EXPERIMENT NEEDED` | UX quality, not a phase | open |
| [OQ-16](#oq-16) | Does `connect_read_pipe` work anywhere on Windows? | `UNKNOWN` | none — worked around | monitoring |

---

### OQ-01
**Which router model actually fits in ~3.5 GB and performs well enough?**
**RESOLVED 2026-08-21 — `qwen3.5:0.8b`, 93.3% intent accuracy, 100% GPU-resident at 16k.**

Full write-up: [`logs/development/2026-08-21-oq01-router-benchmark.md`](../logs/development/2026-08-21-oq01-router-benchmark.md).

**Measured on this GPU:**

| model | num_ctx | placement | prompt eval (2371 tok) | generation |
|---|---|---|---|---|
| **qwen3.5:0.8b** | 8192 & **16384** | **100% GPU** | 1566 ms | **45.4 tok/s** |
| qwen3.5:2b | 4096 **and** 8192 | 36%/64% CPU/GPU | 3352 ms | 20.4 tok/s |

**→ Router is `qwen3.5:0.8b` at 16k context, `think: false`.**

Three findings that changed the design:

1. **`2b` cannot be rescued by shrinking context** — 4096 and 8192 give an *identical* 36%/64% split,
   so the weights are what don't fit. The ADR-0004 arithmetic was too optimistic; it ignored Ollama's
   compute buffers and headroom policy.
2. **Prompt processing is the TTFT bottleneck**, not generation: 227 tok → 173 ms · 1227 tok → 726 ms ·
   2427 tok → 1168 ms · 4827 tok → 2216 ms. An 8k prompt would cost ~3.7 s before the first token.
   → **the context budget had to be split by call type** ([AGENT_RUNTIME.md §5](AGENT_RUNTIME.md#5-context-budget)).
3. **Qwen3.5 is a thinking model.** Default settings spent 229 tokens reasoning about saying "hello"
   and returned an *empty* `response` field. `think: false` is mandatory on every router call.

**Accuracy: RESOLVED 2026-08-21 — `qwen3.5:0.8b` is good enough.**
Full write-up: [`logs/development/2026-08-21-p1-router-accuracy.md`](../logs/development/2026-08-21-p1-router-accuracy.md).

```
intent accuracy    93.3%  (28/30)   gate 85%   PASS
clarify behaviour 100.0%  (30/30)
structured output  0.00% failures   gate <2%   PASS
```

The feared fallback to `2b` was never needed. Three changes got there, none of them model tuning:

1. **`confidence` float → enum.** Ollama's constrained decoding enforces enums but *ignores* numeric
   `minimum`/`maximum`; a float confidence produced `95` and failed validation on 12 of 30 cases.
   23.3% → 63.3%, and structured failures 27.9% → 0%.
2. **Few-shot examples** in the system prompt: 63.3% → 83.3%. The single biggest lever.
3. **Moving three decisions out of the model entirely** (ADR-0011): naming an agent, naming a
   registered pipeline, and bare stop words are facts of the sentence, decided in ~5 ms instead of
   ~1500 ms. 83.3% → 93.3%.

**Latency is the part that missed** — see [OQ-15](#oq-15), split out because it is a property of the
runtime rather than of the model.

---

### OQ-02
**Which embedding model for a mixed Russian/English corpus of prose and code?** `EXPERIMENT NEEDED` · blocks **Phase 5**

Candidates: `multilingual-e5-base` (768d, needs `query:`/`passage:` prefixes), `bge-m3` (1024d,
stronger multilingual, heavier), and Matryoshka truncation to 384d.

**Experiment.** Build the 20-question retrieval fixture set from [RAG.md §8](RAG.md#8-quality-measurement)
— crucially including Russian questions against English code, the case most likely to regress
silently. Measure recall@5 and CPU throughput (chunks/sec on 24 threads) for each candidate, and for
768d vs truncated 384d.

**Decides.** Embedding model, vector dimension (fixed at index build), index size.

---

### OQ-03
**How long will this GPU keep hardware acceleration?** `UNKNOWN` · standing risk

`VERIFIED 2026-08-21`: CUDA 13.0 raised the minimum compute capability to 7.5; CUDA 13.3 removed
Maxwell/Pascal/Volta. Ollama currently supports compute 5.0–6.2 with driver ≥ 570 (ours is 582.28)
because it ships an older CUDA runner. **When that runner is dropped, this GTX 1050 Ti loses GPU
acceleration in an Ollama update.**

**Not resolvable** — it is a vendor decision. Mitigations, all already in the design: the CPU fallback
path is tested in Phase 1 rather than discovered in production; `LLMProvider` allows switching to a
`llama.cpp` build compiled for `sm_61`; and the degradation table
([ARCHITECTURE §8](ARCHITECTURE.md#8-degradation--what-happens-when-a-piece-is-missing)) covers it.

**Watch:** Ollama release notes each quarter. **Do not auto-update Ollama** without checking.

---

### OQ-04
**Does Python's `os.path.realpath` fully resolve Windows junctions and mount points?**
**RESOLVED 2026-08-21 — yes, and `GetFinalPathNameByHandleW` was not needed.**

Full write-up: [`logs/development/2026-08-21-oq04-windows-paths.md`](../logs/development/2026-08-21-oq04-windows-paths.md).
Tested against a **real** `mklink /J` fixture tree, not mocks.

`realpath` correctly resolves junctions, symlinks, 8.3 aliases (`PROGRA~1`), trailing dots and `..`.
`os.path.abspath` resolves none of it and must never be substituted.

**Four findings that changed the implementation:**

1. **`Path.is_symlink()` returns `False` for a junction** (`st_reparse_tag = 0xa0000003`). The natural
   optimisation — "only resolve if it's a link" — walks straight past every junction. Detection must
   use `st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT`. This is the bug that would have shipped.
2. **Junctions need no admin; symlinks do.** Developer Mode is off here, so an unprivileged attacker
   can create junctions but not symlinks — the junction is the *realistic* vector and the symlink the
   theoretical one. The suite treats junction tests as required, symlink tests as skippable.
3. **`realpath` does NOT strip an alternate data stream.** `normal.txt:hidden` writes a hidden stream
   to a file whose size never changes. Rejected by inspection, before resolution.
4. **UNC and device paths pass through `realpath` unchanged** (`\?\C:\…`, `\.\C:`,
   `\host\C$\…`), so they are rejected outright — and *before* the wildcard check, since `\?\`
   contains `?` and `C$` contains `$`.

Windows also silently strips trailing dots and spaces, so `.env.` opens `.env`. Deny rules are
therefore matched **after** resolution, never against the raw string.

---

### OQ-05
**Does `agy -p` emit stdout when run from a subprocess?** **RESOLVED 2026-08-21 — yes.**

Full write-up: [`logs/development/2026-08-21-oq05-antigravity-stdout.md`](../logs/development/2026-08-21-oq05-antigravity-stdout.md).

Tested with `agy` v1.1.14, stdout redirected to a file (not a TTY — the exact condition of
[issue #76](https://github.com/google-antigravity/antigravity-cli/issues/76)):

| mode | result |
|---|---|
| `--output-format json` | exit 0, **257 bytes**, complete valid JSON |
| `--output-format stream-json` | exit 0, **2278 bytes**, 5 NDJSON lines, `init` → `step_update`×3 → `result` |

Issue #76 affects **default text mode** only. **→ Always pass `--output-format`; never rely on
default text output from a subprocess.** That one rule is the entire mitigation, and it costs nothing
because ORACLE wants structured output regardless.

**Envelope gotcha the docs don't state:** the discriminator is an `event` field and the payload sits
under a key *named after the event* — `{"event":"init","init":{...}}`. Parse `obj[obj["event"]]`.

**→ `AntigravityAdapter` is unblocked for Phase 6.** One caveat found: `agy` used **14,119 input
tokens** to answer "say hello" (large injected system prompt), so it is a poor choice for small calls
— route those to Claude or the local model.

---

### OQ-06
**Can a PWA install and receive push over a self-signed certificate?** `TO VERIFY` · bounds **Phase 8**

Browsers require a secure context for service workers. A self-signed cert is untrusted by default,
which likely blocks PWA installation and Web Push.

**Check.** Serve the PWA over the self-signed cert; attempt install and service-worker registration on
the actual phone. Then repeat with a locally-installed CA.

**Does not block Phase 8** — v1 ships in-app WS notifications only, and says so plainly
([MOBILE.md §5](MOBILE.md#the-open-problem--oq-06)). It only decides whether background push is
achievable later.

---

### OQ-07
**Is the memory subsystem dual-channel or quad-channel?** `UNKNOWN` · affects CPU-fallback planning

The Xeon E5-2670 v3 supports quad-channel DDR4, but many X99 boards populate only two channels.
CPU inference is memory-bandwidth-bound, so this is roughly the difference between ~13 tok/s and
~6 tok/s for a 3.4 GB model — the difference between a usable and an unusable fallback.

**Check.** Run a memory bandwidth benchmark, or inspect the DIMM population. Record the result; it
sets realistic expectations for [OQ-03](#oq-03)'s fallback and for the 9B reasoner.

---

### OQ-08
**Does SQLite FTS5 `unicode61` tokenize Russian acceptably?** `TO VERIFY` · blocks **Phase 5**

Half the hybrid search depends on BM25. If the tokenizer mangles Cyrillic, lexical retrieval silently
degrades for Russian queries **with no error anywhere** — the worst kind of failure.

**Check.** Index the 157 Obsidian notes; run Russian queries with inflected forms; compare against
expected hits. If inadequate, evaluate a custom tokenizer or a stemming preprocessor.
Covered by the retrieval fixture suite, which is why that suite must include Russian cases.

---

### OQ-09
**`pywinpty` on Python 3.12, and ConPTY resize/encoding behaviour.**
**RESOLVED 2026-08-21 — `pywinpty` 3.0.5 works; the real hazard was somewhere else entirely.**

Full write-up: [`logs/development/2026-08-21-oq09-conpty.md`](../logs/development/2026-08-21-oq09-conpty.md).

Measured on this machine:

| worry | result |
|---|---|
| Python 3.12 wheel | present, installs clean |
| Cyrillic on a Russian-locale install | **works with no `chcp`** — ConPTY normalises to UTF-8 |
| resize mid-stream | safe; the session survives and keeps streaming (300-line burst intact) |
| concurrent sessions | isolated; output does not cross between them |

**The finding that mattered was not on the list.** Input written before the shell is
reading is **swallowed silently** — no error, no exception, nothing in any log. A fixed
sleep is a coin flip: 1.5 s failed and 2.5 s succeeded, run to run. Waiting for first
output followed by a 300 ms quiet gap was 8/8 at ~0.33 s.

**Rule for the codebase:** readiness of an external process is a *measured condition*,
never a sleep. This is the same shape as [OQ-16](#oq-16) — an external process that
looks alive while quietly ignoring you.

---

### OQ-10
**Is there a text-only Qwen3.5 quant?** `TO VERIFY` · affects **Phase 1**

Published Ollama sizes (`2b` = 2.7 GB, `4b` = 3.4 GB) are larger than a text-only model of that
parameter count implies, because the family is multimodal and the tags include a vision tower. ORACLE
needs no vision. A text-only build could free several hundred MB of VRAM — which at 3.5 GB usable is
the difference between 8k and 16k context.

**Check.** Look for text-only tags in the Ollama library; failing that, evaluate a text-only GGUF from
the community or build one. Fold into [OQ-01](#oq-01).

---

### OQ-11
**Does the Python sidecar terminate when the Tauri shell is force-quit?** **RESOLVED 2026-08-21 — yes.**

Implemented in [`apps/desktop/src-tauri/src/backend.rs`](../apps/desktop/src-tauri/src/backend.rs):
the shell creates a Windows **Job Object** with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and assigns
`oracled` to it. When the last handle to the job closes — which the OS does on process death, however
violent — every process in the job is terminated.

Measured:

```
shell pid 7728 started -> backend reachable: True -> oracled pid 26764
Stop-Process -Id 7728 -Force      (simulates Task Manager "End Task")
oracled still alive after shell force-kill: False
port 8787 no longer serving -> clean
```

Neither `Drop` on `std::process::Child` nor a Tauri `on_window_event` handler survives a hard kill;
the job object is the only mechanism that does. If assignment to the job fails, `Backend::spawn`
refuses to continue rather than run a backend it cannot guarantee to clean up.

The reverse case is also verified: killing the backend leaves the UI showing
`Backend offline — reconnecting in Ns`, and it recovers on its own with no gap
([P0-T1 report](current_report.md)).

---

### OQ-12
**Is taint escalation tolerable in daily use?** `ASSUMPTION` · tuning from Phase 5

The design assumes escalating tiers on tainted turns
([SECURITY.md §6](SECURITY.md#6-prompt-injection-and-taint-tracking)) will fire often enough to
matter but rarely enough not to be disabled. **That is an assumption, not a finding**, and reading any
`node_modules` file taints a turn — which may be constant in practice.

**Resolve by measurement.** Track the escalation rate as a metric from Phase 5. If it is high and
mostly false-positive, refine provenance granularity (e.g. treat a dependency's `package.json` as less
dangerous than its README) rather than weakening the control.

---

### OQ-13
**What approval rate actually causes prompt fatigue?** `ASSUMPTION` · tuning from Phase 3

[ADR-0005](DECISIONS.md#adr-0005--one-policy-gate-risk-tiers-taint-tracking) treats prompt fatigue
as a security failure and sets an alarm at ~5–6 prompts per active hour. **That number is a guess.**

**Resolve by measurement.** Track prompts/hour and the approve-without-reading proxy (decision latency
< 2 s). If people approve in under two seconds, the prompt has stopped being a control and the tier
should move to "auto + undo" instead.

---

### OQ-14
**Does the orbital view earn its place?** `UNKNOWN` · Phase 9 go/no-go

The design commits to a test rather than to the feature: cover every label and it must still be
possible to say what ORACLE is doing
([UI.md §3](UI.md#3-the-core-orbital-view--phase-9), [ROADMAP P9](ROADMAP.md#phase-9--advanced-ui--post-mvp)).

**Resolve at Phase 9.** If it fails, delete it and record an ADR saying so. Deleting a centrepiece
that does not work is a success, not a failure — and deciding this *after* months of real event data
is exactly why it is scheduled late.

---

### OQ-15
**Can a routed turn get meaningfully under ~1.5 s?** `EXPERIMENT NEEDED` · quality, not a blocker

Measured decomposition of a routed turn on this stack:

| component | cost | ours to control? |
|---|---|---|
| Ollama fixed per-request overhead | **~600 ms** | no |
| prompt processing (~900 tok, few-shot) | ~570 ms | yes — but it buys +30 accuracy points |
| generation (~19 tokens) | ~330 ms | marginally |

The ~600 ms floor is real: a 2-token prompt generating *zero* tokens still costs 638 ms, while raw
HTTP to the same daemon is 5 ms. It is unaffected by schema/grammar.

**The 900 ms p50 gate in ROADMAP Phase 1 was mis-derived** — it came from OQ-01's prompt-eval
measurements alone and never budgeted for generation or per-request overhead. It is unreachable here
at any prompt size.

Things worth trying: `/api/generate` with a pre-rendered prompt instead of `/api/chat`; a llama.cpp
server directly (ADR-0009's documented escape hatch); trimming the few-shot block once the
modify/delegate boundary moves to a deterministic later step.

**The real mitigation already works:** the pre-router resolves turns in ~5 ms. Every turn it handles
skips all three costs. That is why ADR-0011 targets >50% of turns.

### OQ-16
**Is `loop.connect_read_pipe` usable at all on Windows' Proactor loop?** `UNKNOWN` · worked around

`asyncio`'s `connect_read_pipe(sys.stdin)` fails inside
`_ProactorReadPipeTransport._loop_reading()` on this platform. The failure mode is the dangerous
kind: the process starts, answers its first write, and then silently never reads again. Every call
times out with nothing to point at.

**Worked around** by reading stdin on a worker thread
([write-up](../logs/development/2026-08-21-toolhost-isolation.md)). That is fine here — the toolhost
handles one invocation at a time — but the same trap is waiting for **any** future component that
tries to read a pipe asynchronously on Windows: the voice daemon, a PTY bridge, an external-agent
adapter streaming stdout.

**Rule for this codebase:** on Windows, read pipes on a thread. Do not reach for
`connect_read_pipe`.

## Standing assumptions

Not questions, but things the design takes as true and would need revisiting if they change:

| Assumption | If false |
|---|---|
| Single user, single machine | Most of the security model simplifies incorrectly; multi-user would need a redesign |
| Windows 10 only | Path handling, ConPTY, Job Objects and DPAPI are all Windows-specific |
| The corpus stays in the tens of thousands of chunks | sqlite-vec brute force stops being adequate; switch to LanceDB |
| Claude Code stays available and affordable | Delegation degrades to the Handoff Packet fallback (already built) |
| Projects mostly live under `C:\Projects` | Scope configuration grows, but nothing structural changes |
| D:/E: keep ~190 GB free | Models and index need a new home; C: cannot host them |
