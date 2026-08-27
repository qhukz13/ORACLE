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
| [OQ-02](#oq-02) | Which embedding model for mixed RU/EN? | ~~`EXPERIMENT NEEDED`~~ | Phase 5 | **RESOLVED 2026-08-24 — `bge-m3`, shipped; it only wins with the fusion gate fixed** |
| [OQ-03](#oq-03) | How long will Pascal keep GPU acceleration? | `UNKNOWN` | risk, not a phase | monitoring |
| [OQ-04](#oq-04) | Does `realpath` resolve Windows junctions? | ~~`TO VERIFY`~~ | Phase 2 | **RESOLVED 2026-08-21 — yes; but `is_symlink()` lies** |
| [OQ-05](#oq-05) | Does `agy -p` emit stdout when piped? | ~~`EXPERIMENT NEEDED`~~ | Phase 6 (Antigravity only) | **RESOLVED 2026-08-21 — yes, with `--output-format`** |
| [OQ-06](#oq-06) | Can a PWA install over a self-signed cert? | `TO VERIFY` | Phase 14 (push only) | open |
| [OQ-07](#oq-07) | Is the memory subsystem dual- or quad-channel? | `UNKNOWN` | CPU-fallback planning | open |
| [OQ-08](#oq-08) | Does FTS5 `unicode61` handle Russian acceptably? | ~~`TO VERIFY`~~ | Phase 5 | **RESOLVED 2026-08-22 — yes; no stemmer, no camelCase split** |
| [OQ-09](#oq-09) | `pywinpty` on Python 3.12 + ConPTY behaviour | ~~`TO VERIFY`~~ | Phase 3 | **RESOLVED 2026-08-21 — works; readiness must be measured, not slept** |
| [OQ-10](#oq-10) | Is there a text-only Qwen3.5 quant? | `TO VERIFY` | Phase 1 | open |
| [OQ-11](#oq-11) | Does the Tauri sidecar die with the shell? | ~~`TO VERIFY`~~ | Phase 0 | **RESOLVED 2026-08-21 — yes, via Job Object** |
| [OQ-12](#oq-12) | Is taint escalation tolerable in daily use? | `ASSUMPTION` | Phase 5+ tuning | open |
| [OQ-13](#oq-13) | What approval rate causes prompt fatigue? | `ASSUMPTION` | Phase 3+ tuning | open |
| [OQ-14](#oq-14) | Does the orbital view earn its place? | `UNKNOWN` | Phase 11 go/no-go | open |
| [OQ-15](#oq-15) | Can routed-turn latency get under ~1.5 s? | `EXPERIMENT NEEDED` | UX quality, not a phase | open |
| [OQ-16](#oq-16) | Does `connect_read_pipe` work anywhere on Windows? | `UNKNOWN` | none — worked around | monitoring |
| [OQ-17](#oq-17) | Is a ~43 min **cold** reindex acceptable? | `ASSUMPTION` | Phase 5 tuning | narrowed 2026-08-22 — warm rebuilds are 37 s |
| [OQ-18](#oq-18) | Can Russian questions reach an English corpus at all? | measured 2026-08-26 | Phase 5 gate | **both levers measured — 78.9% against an 80% gate, one fixture short; gate NOT moved** |
| [OQ-19](#oq-19) | Should the Claude integration move to the Claude Agent SDK? | `TO VERIFY` (on trigger) | none — trigger-based | open |
| [OQ-20](#oq-20) | Can `agy --json-schema` reliably return a valid ExecutionPlan? | measured 2026-08-24 | P6-T5 / Phase 8 | **answered NO — 75% vs a 90% gate; the ladder promoted Claude** |
| [OQ-21](#oq-21) | When does ORACLE's MCP server need the 2026-07-28 spec? | `UNKNOWN` | none — watch item | monitoring |
| [OQ-22](#oq-22) | Does the knowledge graph hold its budgets at corpus scale? | measured 2026-08-26 | Phase 11 (graph view only) | **3 of 4 answered — build it, narrower; canvas-vs-SVG still needs a real window** |
| [OQ-23](#oq-23) | Does a failure-carrying prompt produce a *different* plan? | `EXPERIMENT NEEDED` | nothing — replanning ships bounded | opened 2026-08-25 |
| [OQ-24](#oq-24) | Does observing every project fit the glance budget? | `EXPERIMENT NEEDED` | Phase 12 (the sidebar and the briefing) | opened 2026-08-26 |
| [OQ-25](#oq-25) | Did adding the `continue` label move intent accuracy? | `TO VERIFY` | nothing — but it is a regression risk carried knowingly | **first real evidence 2026-08-28: the label routes; the project slot does not** |

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
**Which embedding model for a mixed Russian/English corpus of prose and code?**
**RESOLVED 2026-08-24 — `bge-m3` at 1024d, conditional on the fusion-gate fix that landed
with it. Not switched by default; see below.**

Full write-up: [`logs/development/2026-08-24-oq02-bge-m3.md`](../logs/development/2026-08-24-oq02-bge-m3.md).

The decisive run finally happened: both candidates built over the same full corpus on the
same day and measured by the same code, 38 fixtures of which 25 are Russian.

| embedding | fusion gate | recall@5 | crosslang | p95 |
|---|---|---:|---:|---:|
| `e5-base` | as shipped | 55% | 36% | 271 ms |
| `e5-base` | fixed | 55% | 36% | 260 ms |
| `bge-m3` | as shipped | 53% | 32% | 401 ms (fails) |
| **`bge-m3`** | **fixed** | **61%** | **44%** | 332 ms |

**Measured against the retrieval code as it shipped, `bge-m3` loses.** The fusion gate was
admitting BM25's thirty results on 38 questions out of 38 — including all 25 Russian ones,
for which BM25 returned the corpus's one Russian-documented project whatever the question
was about. Fusion can only displace a correct dense hit that exists, so the damage scaled
with how good the dense half was: it cost `bge-m3` twelve points of cross-language recall
and `e5-base` nothing. **The comparison was measuring the gate, not the model** — and the
2026-08-22 conclusion that "the Russian failures are the embedding" was drawn through the
same instrument.

The gate now drops terms in a script the corpus is not written in, and requires the
survivors to cover 40% of the question. No configuration regresses; `bge-m3` gains eight
points on the column this question exists to decide.

**`DEFAULT` is `bge-m3` as of 2026-08-24.** The owner took the switch: one line
(`DEFAULT = BGE_M3`), one full rebuild, and resident memory goes from ~1.5 GB to ~3 GB.
`e5-base` keeps its `ModelSpec` and is one line back — `KnowledgeStore.bind` refuses an
index built by the other model, so a switch either way is a rebuild, never silent
nonsense.

**What this question no longer answers.** At 61% the system is nineteen points under its
own 80% recall gate, and 7 of 25 Russian cases never enter the candidate set at all. That
is not an embedding choice; the untried levers are query translation and the ~20% of
chunks silently truncated at 512 tokens (`TO VERIFY` in `rag/chunking.py`). See
[OQ-18](#oq-18).

---

**REOPENED 2026-08-22, the same day it was resolved** — retained below, because the
reasoning it records is what the 2026-08-24 run corrected.

The answer below — `multilingual-e5-base` at 768d — was chosen on a Russian sample of **eight**
questions. Expanding that set to 25 (P5-T2 requirement 6, ground truth read from the files rather
than retrieved) puts `e5-base` at **36% on Russian, not 62%**: the small set had overstated it by 26
points. The English and lexical halves are unaffected and still measure as recorded here.

Nothing below is retracted — it was all measured — but the *decision* it supports rested on the one
number that has since moved most. `bge-m3` scored 100% on the same eight where `e5-base` scored 75%;
both are inflated, and the run that would settle it is ~2.5 h over the full corpus.

Two alternative explanations were eliminated first, so this is not a retrieval bug wearing a model's
clothes. The fusion gate did have a real denominator bug — fixed, and recall did not move. And the
failures are not near-misses: of 25 Russian cases, 9 land in the top 5, **zero** in ranks 6-10, and
**12 never enter the candidate set at all**.
[Log](../logs/development/2026-08-22-fusion-denominator.md).

---

**Resolved 2026-08-22 (superseded by the above) — `multilingual-e5-base` at 768d. Not truncated, not quantised.**

Full write-up: [`logs/development/2026-08-22-oq02-embeddings.md`](../logs/development/2026-08-22-oq02-embeddings.md).
Harness: `scripts/eval_embeddings.py`. Fixtures: `tests/fixtures/retrieval/cases.yaml`.

21 fixtures, identical chunks for every candidate. Recall from a 3,000-chunk sample for
the comparison, plus the winner measured over the **whole corpus**; throughput from a
dedicated idle-machine run:

| candidate | dim | dense r@5 | hybrid r@5 | RU→EN r@5 | chunks/s |
|---|---:|---:|---:|---:|---:|
| BM25 only | — | — | 62% | **0%** | — |
| e5-small | 384 | 76% | 71% | 38% | 7.95 |
| **e5-base** | **768** | 81% | 90% | 75% | **4.71** |
| e5-base → 384 | 384 | 71% | 81% | 62% | 4.71 |
| e5-base int8 | 768 | 52% | 76% | 62% | 4.59 |
| bge-m3 | 1024 | 90% | 95% | 100% | 1.37 |
| **e5-base, FULL corpus** | 768 | — | **81%** | **62%** | — |

**What it decided.** Embedding model `multilingual-e5-base`, vector dimension **768**
(fixed at index build; the store records it and refuses a mismatch), index ~8 MB.

**Three findings worth carrying:**

1. **Matryoshka truncation costs 9 points, not "minimal".** E5 is not Matryoshka-trained,
   so its first 384 dimensions are half an embedding rather than a small one. Saving: 4 MB.
2. **The published int8 export loses 29 points of dense recall** and gains only 13%
   throughput — this CPU is Haswell, with no AVX-512 and no VNNI, so the export's kernels
   do not apply. It fell *below* BM25 alone.
3. **BM25 scores 0% on cross-language retrieval.** Eight Russian questions, eight misses.
   The dense half of the index is not an enhancement; it is the only thing that answers
   the query class this project cares most about.

**Still open, and less comfortably than the sample suggested.** On the full corpus
`e5-base` scores **81%** — one point over the gate — and **62% on the Russian questions**,
missing three of eight. The 3,000-chunk sample overstated it by 9 points overall and 13 on
the cross-language column.

`bge-m3` beat it on that sample by 5 points overall and **25 on the Russian subset**, at
**3.4x** the indexing time (1.37 vs 4.71 chunks/s; ~2.5 h against 43 min for a full build)
and 2x resident memory. e5-base ships as the default; the switch is one `ModelSpec` and a
rebuild, and `KnowledgeStore.bind` refuses an index built by the other model rather than
returning nonsense.

**The indexing cost is no longer the argument against it.** Since the embedding cache
([OQ-17](#oq-17)) a model's ~2.5 h is paid *once*; every rebuild after that is seconds. What
is left is the recall question, which is the one that actually needs answering.

**Two measurements would settle it**, neither yet run:
1. `bge-m3` over the **full** corpus — ~2.5 h of CPU, and the direct comparison.
2. Expanding the Russian fixtures from 8 to ~25, which is cheap and makes both numbers
   mean something. n=8 cannot support a decision this expensive.

**And one criterion it invalidated:** the Phase 5 "full index in < 10 min" target is not
achievable for a *cold* build on this CPU — measured at 42.8 min. With the embedding cache
added afterwards, a warm rebuild is 37 s. See [OQ-17](#oq-17).

---

### OQ-17
**Is a ~43-minute cold reindex acceptable?**
`ASSUMPTION` · Phase 5 tuning · **narrowed 2026-08-22 by the embedding cache**

Opened by [OQ-02](#oq-02), which replaced a guessed budget with a measured one. Then
mostly answered by building the thing that made the question smaller
([log](../logs/development/2026-08-22-embedding-cache.md)).

The original worry was that *any* rebuild costs an hour, which would make the index's
disposability — a load-bearing property of ADR-0006 — expensive enough that nobody uses it.
That is no longer the case. Embeddings are cached by `sha256(text)` in a file separate from
`knowledge.db`, so what a rebuild costs depends on whether the cache is warm:

| | cost | how often |
|---|---|---|
| Cold — first build, or a change of embedding model | **~43 min** | once per model |
| Warm — the index was deleted, chunking unchanged | **~37 s** | every other time |
| Warm — chunking changed | **2.5–20 min** | when this repo edits `chunking.py` |
| Incremental, nothing changed | 1.4–4.4 s | dozens of times a day |

The third row was measured after this entry was first written, and it corrects it: a chunking change
moves chunk *text*, which is the cache key, so the first tree-sitter build hit only 45% of the cache
and cost 19.9 minutes. Later builds hit 71%, 95% and 97% as successive changes moved less text. It is
a developer cost, not a user one — but "warm rebuild = 37 s" was the best case quoted as the rule.

**What remains an assumption** is only the first row: that a one-off 43 minutes, on a
model change, is tolerable. Everything else is now fast enough not to be a design concern.

It also removes indexing cost as an argument against `bge-m3` — its ~2.5 h is paid once,
after which its rebuilds are as cheap as e5-base's. That argument has since been settled:
[OQ-02](#oq-02) resolved on 2026-08-24 in `bge-m3`'s favour, and the cost this entry
measures is the price of the switch.

**Resolve by using it.** If a cold rebuild ever becomes frequent, the remaining levers are
to embed only changed collections, or to accept `e5-small` for a first pass and upgrade in
the background.

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
**Can a PWA install and receive push over a self-signed certificate?** `TO VERIFY` · bounds **Phase 14**

Browsers require a secure context for service workers. A self-signed cert is untrusted by default,
which likely blocks PWA installation and Web Push.

**Check.** Serve the PWA over the self-signed cert; attempt install and service-worker registration on
the actual phone. Then repeat with a locally-installed CA.

**Does not block Phase 14** — v1 ships in-app WS notifications only, and says so plainly
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
**Does SQLite FTS5 `unicode61` tokenize Russian acceptably?**
**RESOLVED 2026-08-22 — yes, with two index-time mitigations. No custom tokenizer.**

Full write-up: [`logs/development/2026-08-22-oq08-fts5-russian.md`](../logs/development/2026-08-22-oq08-fts5-russian.md).

Measured on SQLite 3.50.4:

| | |
|---|---|
| Cyrillic case folding | **works** — `токен` matches `ТОКЕН`, both directions |
| Underscore splitting | **works** — `MAX_YAML_DEPTH` is findable as `yaml`, `depth` |
| Stemming | **absent** — `токен` does not match `токена`, and Russian is inflected |
| camelCase splitting | **absent**, and unconfigurable — `separators` adds separator *characters*, and a case transition is not one |

Two obligations follow into the Phase 5 schema, and each needs a test because each degrades silently:

1. **An `ident` FTS5 column** holding identifiers exploded into parts, written at index time.
   An unqualified `MATCH` searches every column, so `entitlement` then finds `entitlementGuard`.
2. **Cyrillic query terms are prefix-expanded** (`токен` → `токен*`); Latin terms are not.
   It recovers the inflections and over-matches in a bounded way — `токен*` also hits `токенизация` —
   which BM25's IDF and RRF fusion absorb.

A custom tokenizer is a C extension and a build step; it is not worth it for a gap the fusion layer
largely covers. Revisit only if the fixture set shows Russian lexical misses that RRF does not rescue.

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
**Does the orbital view earn its place?** `UNKNOWN` · Phase 11 go/no-go

The design commits to a test rather than to the feature: cover every label and it must still be
possible to say what ORACLE is doing
([UI.md §3](UI.md#3-the-core-orbital-view--phase-11), [ROADMAP P11](ROADMAP.md#phase-11--execution-visualisation--advanced-ui--capability-arc)).

**Resolve at Phase 11.** If it fails, delete it and record an ADR saying so. Deleting a centrepiece
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

### OQ-18
**Can a Russian question reach an English codebase well enough to meet the 80% gate?**
`EXPERIMENT NEEDED` · Phase 5 gate · **opened 2026-08-24 by [OQ-02](#oq-02)'s resolution**

OQ-02 asked which embedding model, got a decisive answer, and the answer is not enough.
The best configuration this system has produced — `bge-m3` with the fixed fusion gate —
scores **61% recall@5 against a gate of 80%**, and **44% on the 25 Russian fixtures**
([log](../logs/development/2026-08-24-oq02-bge-m3.md)).

The shape of the remaining failure says it is not a ranking problem. Seven of the 25
Russian cases never enter the thirty candidates at all, so no reranker, no wider top-k and
no further fusion work can reach them. Their English neighbours in the same collections do
land, so nothing is unindexed.

Two levers, neither measured:

1. **Query translation.** Embed an English translation of the Russian question as a second
   dense probe and fuse the two candidate lists. The router model is already resident and
   already sees every query, so the marginal cost is one short generation — but it puts a
   model call on the retrieval path, which [RAG.md §5](RAG.md#5-hybrid-retrieval) has so
   far avoided, and the latency budget has ~70 ms of headroom at `bge-m3`'s p95.
2. **The 512-token truncation.** ~20% of chunks exceed the model limit and are silently
   truncated (`TO VERIFY` in `rag/chunking.py`, open since 2026-08-22). If the answer
   sentence is routinely past the cut, this is a chunking bug being read as a retrieval
   one, and it is much cheaper to fix.

**Measure the second first** — it is a property of the corpus that can be counted without
building anything, and it would change what the first experiment means.

**Until this resolves, the Phase 5 recall criterion is not met**, and saying so is more
useful than moving the gate to where the numbers already are.

#### Lever 2, measured  `2026-08-25, P9-T1`

`scripts/measure_truncation.py`, [dev log](../logs/development/2026-08-25-oq18-truncation.md).
Tokenized the declared corpus with `bge-m3`'s own tokenizer — no inference, so the whole
measurement costs about a minute.

**Truncation is real and much worse than the estimate:** 2,545 of 12,648 chunks (**20.1%**) exceed
the 512-token window, taking **10.1% of all corpus tokens** with them. It is not uniform —
**88% of `config` chunks** overflow, against 13% of code. And the character cap that was supposed
to prevent this is **not enforced**: 17% of chunks are longer than `MAX_CHARS`, the longest by more
than double.

**And it is not the cause of this question's gap.** The seven Russian cases that never enter the
candidate list all point at notes-collection markdown whose chunks fit the window with room to
spare — **0% of their tokens are lost**. Across all 25 Russian fixtures only five expected files
contain any truncated chunk at all, and the worst loses 11% of one two-chunk file.

**So lever 2 is ruled out and lever 1 is what remains.** Query translation now has to be run on its
own merits, and — this is what the ordering bought — its result will be interpretable, because the
index it is measured against does not have a hole where the answers are. The chunking defects are
worth fixing on their own terms; they belong to a task that touches retrieval, because they change
chunk boundaries and therefore invalidate every recall number measured before them.

#### The baseline was measured with the wrong chunker  `2026-08-26, P9-T2`

[dev log](../logs/development/2026-08-26-oq18-chunking.md). `scripts/eval_embeddings.py` carried its
own copy of the chunker — correct for OQ-02, where five candidates had to see byte-identical chunks,
and wrong from the moment the model was fixed. On the same corpus the copy produced **12,770**
chunks and the shipped chunker **11,727**. **Every recall number this question records was computed
over chunks ORACLE does not produce.**

The harness calls the shipped chunker now. Re-measured against it, before any repair:

| | recorded here | measured 2026-08-26 |
|---|---|---|
| best overall recall@5 | 61% (gated) | **68%** (`rrf_w2`); gated is 58% |
| RU (crosslang) recall@5 | 44% | **40%** |

Neither is a regression — they are the first numbers that describe the shipped code. The gate is
unchanged at 80% and unmet.

The truncation figures in the section above were measured the same way and are corrected in the same
log: **27.1%** of *embedded* chunks over the window (not 20.1%), and **6.42%** of embedded tokens
(not 10.1%, which counted config chunks that are never embedded at all). The conclusion is
unaffected: the seven Russian cases that never reach the candidate list lose **0%** of their tokens.

`MAX_CHARS` is recalibrated (1800 → 1200) and now enforced against the rendered chunk, taking
truncation to 0.7% of embedded chunks.

#### Both levers measured  `2026-08-26, P9-T2`

Two full runs over the real corpus, ~4.3 hours of CPU, same fixtures, same model, the only
difference between them the chunker: `logs/measurements/oq18-{before,after}.json`,
[dev log](../logs/development/2026-08-26-oq18-chunking.md).

**First, a correction that changes what this question has been recording.** `retrieve()`'s
`discriminating_terms` drops minority-script terms at any frequency, so a Russian query returns no
lexical terms and ORACLE takes the **dense-only** path — it never fuses BM25. The eval harness's
`gated` strategy uses a different rule with no script test, so **the 44% recorded above was
measuring a code path ORACLE does not run.**

Composed per-case from the miss lists, for the path that actually ships:

| | overall recall@5 | RU recall@5 |
|---|---|---|
| before this task | 68.4% | 56.0% |
| **after the chunker repair** | **71.1%** | **60.0%** |
| **+ an English probe (human translation)** | **78.9%** | **72.0%** |
| the gate | 80% — 31 of 38 fixtures | |

**78.9% is 30 of 38. The gate needs 31.** One fixture.

| lever | effect on the shipped path |
|---|---|
| 2 · truncation, fixed | +2.7 overall, +4.0 RU |
| 1 · query translation, at its ceiling | +7.8 overall, +12.0 RU |
| 3 · not fusing BM25 on a crosslingual query | already correct in `retrieval.py`; worth **12–20 RU points**, and the harness had it wrong |

**The gate stays at 80% and is unmet.** 6.3 points on 38 fixtures is 2.4 cases; a gate re-argued to
sit just below where the numbers landed would measure nothing. What changed is that "unmet" now has
a number, a decomposition, and a named next step.

**Two things block closing it, and neither is a matter of typing:**

1. **The translator is unmeasured.** +12.0 RU is what a *human* translation buys — the ceiling of
   the idea, deliberately, so that a negative result would have killed it outright. Whether the
   resident 0.8B model's Russian reaches that ceiling is the next measurement, and it is cheap:
   translate 25 fixture questions, re-score the query half. Ollama was not running on this machine,
   so it could not be done here.
2. **The latency does not fit the interactive path.** A second dense probe costs one more query
   embedding — 63 ms p50 / 97 ms p95, measured — against the ~70 ms of headroom recorded above, and
   that is *before* the generation call. Where it fits is the Handoff Packet, where a delegation
   takes minutes and retrieval already runs.

**One of the eight remaining misses is structural rather than a ranking problem.**
`en-relay-dockerfile` expects `Asterim/Dockerfile.relay`, a **config** file — indexed lexically and
never embedded. No dense probe of any quality can retrieve it; the lexical half is what should, and
the script rule turns that half off for the queries around it. A fixture set that includes it is
measuring fusion and dense retrieval with one number.

---

### OQ-19
**Should the Claude integration move from the pinned CLI contract to the Claude Agent SDK?**
`TO VERIFY` · trigger-based, blocks nothing

The Python Claude Agent SDK (0.x as of 2026-08) wraps the same `claude` subprocess with typed
streaming events, lifecycle hooks (PreToolUse can *block* a tool — which could enforce the policy
gate in-process rather than via `--allowedTools` + MCP), in-process MCP servers, and session
management. That is genuinely better than parsing stream-json by hand. It is also a moving 0.x
API replacing a contract that is **working, recorded into fixtures, and tested** — and it would
shift the pinned surface in INTEGRATIONS.md §3 from CLI flags to an SDK version.

**Decision recorded in [ADR-0022](DECISIONS.md#adr-0022--external-agent-frameworks-evaluated-not-adopted):**
keep the hand-rolled contract. **Trigger to re-open:** the next breaking drift of the CLI stream
contract (quarterly re-verification will catch it) — at that point the migration cost is paid
either way, and the SDK should win. Check then: SDK maturity (out of 0.x?), whether hooks can
express the gate's decisions, dependency weight, and whether `--setting-sources`/scrub isolation
survives the SDK path.

---

### OQ-20
**Can `agy --json-schema` reliably return a valid `ExecutionPlan`, at what cost and latency?**
**Answered `NO` at the stated gate — 2026-08-24, P6-T5.** The ladder has promoted; Phase 8's
default planner is **Claude**, not Antigravity.

Measured over 16 supervised calls (4 real objectives × `--effort low|high` × 2 repeats), driving
the real adapter against a schema generated from PLANNER.md §2. Full analysis and every call:
[`logs/development/2026-08-24-p6t5-antigravity-planning.md`](../logs/development/2026-08-24-p6t5-antigravity-planning.md).

| | result | gate |
|---|---|---|
| valid on first attempt | **12/16 = 75%** | ≥ 90% — **missed** |
| …at `--effort low` alone | 7/8 = 87.5% | still short, on 8 samples |
| median latency | 27.1 s (low) · 42.9 s (high) | — |
| median cost | ~55k tokens per plan (955k across the run) | — |

**The failure shapes matter more than the rate.** None of them was the truncation or
prose-wrapping the question anticipated:

* **3 × the planner went browsing.** All at `--effort high`. Given an empty temp workspace, it
  tried to `read_file("C:\Users\qhukz")` — the owner's home directory — and once a named personal
  file. The vendor's permission gate denied it, which ended the run. That gate exists here only
  because ORACLE refuses `--dangerously-skip-permissions`; under that flag those calls would have
  read the owner's home directory and sent what they found to the vendor. **The strongest result
  of the spike is a security one, and it is not about planning.**
* **1 × `structured_output` returned `tasks: []`** while the raw `response` beside it held a
  complete six-task plan. The vendor's schema filter drops non-conforming items **silently**.
  A schema-shaped answer is not a validated answer.
* **Valid ≠ schedulable.** Only 7 of 12 valid plans declared *any* dependency; five were DAGs with
  no edges — tasks a scheduler would fire simultaneously that must plainly be sequential.
  `project`, `context_hints` and `agent_hint` were filled on 45 of 72 tasks.

**Sample size, stated plainly:** 16 calls, not the ≥ 20 the task specified. The pilot measured
55.6k tokens per plan — 4× the pre-run estimate — and the owner trimmed the grid. OQ-20 is
therefore **narrowed with numbers rather than closed at the stated power**, and re-opening it
costs another ~1M tokens.

**What follows** (PLANNER.md §5–§6): Claude authors plans against the same schema and the same
validation; Antigravity keeps `reviewer` and `researcher`. If it is ever reconsidered for
`planner`: pin `--effort low`, add the repair round trip, add a tolerant parse of the raw
`response` as a second source, and demand dependencies explicitly in the prompt.

---

### OQ-21
**When does ORACLE's hand-rolled MCP server need the 2026-07-28 spec revision?**
`UNKNOWN` · watch item, blocks nothing

ORACLE speaks protocol `2025-06-18` — four JSON-RPC methods, pinned by tests, zero dependencies
(INTEGRATIONS.md §4). The 2026-07-28 revision makes the core stateless, replaces server-initiated
requests with MRTR, and adds a Tasks extension for long-running operations — the last being
genuinely relevant to exposing delegations over MCP someday.

**The standing rule already covers this:** take the SDK (now v2) the day a client rejects the
hand-rolled surface. **Watch:** Claude CLI release notes for a minimum-protocol-version bump;
re-check quarterly with the vendor-contract re-verification. Do not migrate pre-emptively — the
current surface works and the SDK costs 24 packages in the trusted base (measured, P6-T3).

---

### OQ-22
**Does the knowledge-graph view hold its layout, rendering and quality budgets at corpus scale?**
`EXPERIMENT NEEDED` · blocks the Phase 11 graph view (nothing else); design in
[UI.md §11b](UI.md#11b-the-knowledge-graph--phase-11), decision in
[ADR-0023](DECISIONS.md#adr-0023--the-knowledge-graph-is-simulated-then-frozen-canvas-rendered)

The design commits to numbers nobody has measured on this corpus (~1,330 documents, ceiling 10k)
and this machine. Four measurements, run at the start of Phase 11 before the view is built:

1. **Offline layout cost.** A force layout of the full document graph in the indexing worker —
   wall-clock and peak memory, cold and incremental. Gate: fits inside the incremental-index
   budget for the add-one-document case; a full re-layout may cost minutes because it is explicit.
2. **Rendering.** Canvas pan/zoom over the full corpus at 60 fps on this GPU/WebView2, idle
   < 5% CPU, first paint < 1 s from cached positions. Compare an SVG control run to keep ADR-0023
   honest — if SVG survives at this node count, the canvas complexity is unjustified.
3. **Semantic-edge quality.** Embedding-kNN thresholds/caps that produce readable clusters rather
   than a hairball, judged against the four questions the view exists to answer (shape, neglect,
   reach, use) on the real corpus. If no threshold reads well, semantic edges ship off and stay a
   toggle-nothing — the explicit link graph alone may be the honest product.
4. **Incremental placement.** New documents placed at neighbour centroids: does the map stay
   recognisable after a week of real edits, without a re-layout?

Failure of 1 or 2 changes the mechanism (coarser graph: one node per note/section, or
level-of-detail culling), not the goal. Record results in `logs/development/` and fold the
numbers into TESTING.md's performance table when the view lands.

#### Measured  `2026-08-26, P11-T1`

`scripts/measure_graph.py`, [dev log](../logs/development/2026-08-26-oq22-knowledge-graph.md),
data in `logs/measurements/oq22-graph.{json,txt}`. Corpus fingerprint `e342f8a55a6ce17d`.

**Measurement 3 ran first**, against the ordering above, because the edge model decides the node
count and the node count is what the rendering question is asked at.

| | result |
|---|---|
| **3 · semantic edges** | **Required, not optional.** Explicit wikilinks touch **11% of the corpus** (157 of 1,420 documents; 156 of them `notes`, 1 `projects`). Explicit-only leaves **1,168 of 1,325 embeddable documents orphaned**. Semantic edges take that to 44. Recommended default **k=4, thr=0.85**: 3,103 edges, 189 orphans, 35% giant component, 2-hop median 0.9%. The useful band is **0.80–0.90**; 0.95 is indistinguishable from no semantic edges. |
| **3b · bridges** | **The one question the view cannot answer.** Across every k and every threshold the graph holds **one** edge joining `notes` to `projects`. Not a tuning failure — the notes are ML prose and the projects are code. UI.md §11b's four questions become three. |
| **1 · layout** | Cold 1,420 nodes / 3,103 edges: **27.8 s**, peak RSS **121 MB**. Incremental placement p95 **0.032 ms**. All three gates pass by wide margins. |
| **1b · the real cost** | Reading 13,771 vectors out of `vec0` is **51.8 s**; pooling and the full kNN together are **0.2 s**. The arithmetic is 0.2% of the work. **`document_vectors` is a required table**, written by `store.put()` — otherwise incremental indexing spends 52 s against a `< 5 s` budget and it gets misdiagnosed as slow layout. |
| **1c · the ceiling** | Clean O(N²): 500/1k/2k/4k → 3.4/13.8/55.4/200.7 s projected. Extrapolated to ADR-0023's 10k ceiling: ~21 min, ~800 MB — inside the time gate, **outside the 500 MB one**. The current corpus does not need Barnes-Hut; a 7x larger one would. |
| **4 · stability** | **Reframed as a holdout**, because "after a week of real edits" is unanswerable inside a phase. Jaccard@10 against a full re-layout: **0.477 / 0.410 / 0.336** at 5 / 10 / 20% holdout, against a self-imposed 0.70 gate — **missed**. Positions remain *stable* (nothing moves on its own, per ADR-0023); what degrades is *fidelity*. So re-layout must be prompted after a few percent growth, not buried — and at 28 s it is cheap. |
| **2 · canvas vs SVG** | **Not answered.** It needs rAF deltas from a compositing window on this GPU inside WebView2, and the spike ran without one. Frozen positions are in `oq22-graph.positions.npz` so the harness has its input. At 1,420 nodes / 3,103 edges the scene is unremarkable for SVG, and OQ-22 asks for that control precisely to keep ADR-0023 honest — so **[ADR-0023](DECISIONS.md#adr-0023--the-knowledge-graph-is-simulated-then-frozen-canvas-rendered) is UNCONFIRMED** until somebody runs it. |

**And one finding that was not one of the four.** The first stability run returned 0.249 at *every*
holdout fraction — a metric not responding to its own variable. The cause was in the layout:
initial positions were seeded by **array index**, so the same document started somewhere different
depending on how many documents existed and in what order. Seeding from a hash of the node's own id
fixed it, and the numbers immediately became monotone. This matters beyond the measurement:
**ADR-0013's argument is that a person learns where things are**, and array-order seeding breaks
that at the source — reindex after adding one file and every position shifts. It would have shipped
as "the layout is unstable, add more iterations".

---

### OQ-23
**Given a failure, does a real planner produce a materially different plan — or a rephrased one?**
`EXPERIMENT NEEDED` · blocks nothing; replanning shipped in P8-T2 with the budget that makes a bad
answer cheap. Design in
[ORCHESTRATION.md §4](ORCHESTRATION.md#as-built--replanning--p8-t2-2026-08-25).

The replan prompt states what failed, ORACLE's measurements of it, what never ran, and that the
failed approach must not be repeated. Every one of those sentences is a *design decision*: nobody
has checked that a vendor given them changes its mind rather than restating its first plan with
new wording. The P6-T5 spike measured plan validity, not plan *difference*, and the two are not
the same property.

**The measurement**, when a real objective has failed at least twice in normal use (a synthetic
failure would answer a synthetic question):

1. Capture the first plan, the failure, and the replan for ≥ 10 real replans.
2. Score each pair: same tasks reworded · same decomposition with different targets · genuinely
   different approach. A useful replanner should mostly land in the third bucket; mostly landing
   in the first means the prompt is decoration and the budget is spending money for nothing.
3. Separately: does naming the skipped work cause the replan to re-author it, or to forget it?
   That sentence exists to prevent silent loss of scope and has never been checked.

Failure of (2) changes the prompt, not the mechanism — the append-only lineage, the budget and the
approvals are correct regardless of whether the planner has a second idea worth having. Failure of
(3) is a scope bug and is worth fixing immediately.

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

---

### OQ-24
**Does observing every project fit the 3–5 second glance budget?** `EXPERIMENT NEEDED` ·
opened 2026-08-26 · bounds **Phase 12**'s sidebar and briefing.

[VISION.md §2](VISION.md#2-the-day--the-acceptance-test) allocates 3–5 seconds to understanding
the screen. Observed state is deliberately never cached
([PROJECT_STATE.md §2](PROJECT_STATE.md#2-the-distinction-that-makes-this-design-work)), so
showing branch and dirty count for N projects costs **N × (`git.status` + `git.log`)**, each a
toolhost round trip plus a `git` process. Warm IPC is p50 **27.9 ms** and a `git status` on a warm
repo is single-digit milliseconds, so the arithmetic suggests ~13 projects ≈ 1 s — but the
arithmetic is exactly what ADR-0004 got wrong about `qwen3.5:2b`, so it is not an answer.

**What P12-T1 shipped instead of guessing:** `GET /api/v1/projects` runs **no git at all** and
omits branch and dirty count; only `GET /api/v1/projects/{id}` observes, one project at a time.
A test asserts the list endpoint exposes no observed state, so the cheap path cannot quietly
acquire a fan-out later.

**The experiment**, when the sidebar wants those columns: time the fan-out at the real project
count against a cold and a warm toolhost, on this machine, with a repository the size of
`Source2DemViewer` (3,915 files in `target/`) in the set.

**The answer is not a cache.** If the fan-out misses, observe **lazily per row** — the row that
is on screen, when it is on screen. Caching would make the sidebar wrong the moment someone
switches branches in their editor, which is the failure this whole design is shaped to avoid.

---

### OQ-25
**Did adding the `continue` label move intent accuracy?** `TO VERIFY` · opened 2026-08-26 ·
**deliberately deferred by the owner**, recorded rather than skipped.

P12-T2 added an eleventh `IntentLabel`. Accuracy was measured at **93.3%** and single-tool
selection at **100%** on a 30-case fixture set with ten labels
([OQ-01](#oq-01)); neither number has been re-measured since.

**The named risk is `continue` vs `run` and `modify`.** To a 0.8B classifier *"run the Asterim
tests"* and *"continue Asterim"* differ by one word, and both name a project. `run` is the
expensive confusion: it would send a `continue` to tool selection, which picks one existing
command instead of reading the project's state.

**What was done instead of measuring**, so the deferral is not a blank cheque:

- the system prompt states the boundary explicitly rather than leaving it inferable —
  *"continue: resume unfinished work on a project. No specific task is named"*, plus a paired
  example contrasting it with `run`;
- four few-shots, one of them Russian, matching what every other label carries;
- a test asserts the prompt teaches the boundary and that the Russian example exists, so a
  future edit cannot quietly delete the mitigation.

None of that is a number. **A wrong route here is recoverable** — the user sees the intent on
the turn and can rephrase — which is why this blocks nothing.

**To resolve:** run `scripts/eval_intent.py` against the fixture set, add `continue` cases to it
first, and record the result the way OQ-01 was recorded. Note that `make eval` is documented in
[TESTING.md §8](TESTING.md) and **defined nowhere** — that has to be fixed or the doc corrected
before this can be run the documented way.

### First real evidence  `2026-08-28, P12-T5`

Two live `continue ORACLE` runs against the real router
([dev log](../logs/development/2026-08-28-p12t5-first-run.md)):

| | |
|---|---|
| `intent` | **`continue`** both times, confidence `medium` — the label routes |
| `project` | **`null` both times** — on an input whose second word is a registered project |

So the feared failure did not happen: `continue` was not confused with `run` or `modify`. A
different one did. **The project slot is unreliable**, and the turn only worked because
`_named_project` scans the raw text against the registry — a fallback written for `delegate`.

That fallback cannot cover the cases that matter later: a project named in a *previous* turn, or
referred to obliquely. Fixtures should pin the slot, not just the label, before anything trusts
it. This does not change the marker — the eval is still un-rerun — but it narrows what to look
for when it is.
