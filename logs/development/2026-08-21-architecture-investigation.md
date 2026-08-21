# 2026-08-21 — Initial architecture investigation

Design pass for ORACLE. No code written. This log records **what was measured, what was verified
against primary sources, and which of my starting assumptions were wrong** — so nobody repeats the
work or inherits the stale assumptions.

---

## 1. Environment survey (measured, not assumed)

```
GPU     NVIDIA GeForce GTX 1050 Ti · 4096 MiB · compute 6.1 (Pascal) · driver 582.28
CPU     Intel Xeon E5-2670 v3 · 12 cores / 24 threads · Haswell-EP · 2.30 GHz
RAM     31.9 GB
Disk    C: 405.7 used / 39.8 free   D: 278.5 / 187.3   E: 702.8 / 190.9
OS      Windows 10 Pro 19045
```

Installed toolchains: Python **3.14.6** and **3.10** (no 3.11/3.12/3.13) · Node v24.16.0 / npm 11.13.0
· git 2.45.1 · Docker 29.5.3 · **Ollama client 0.32.5, daemon not running** · cargo 1.91.1 ·
**Claude Code 2.1.234**.

### Findings that changed the design

1. **C: has only 39.8 GB free.** Ollama defaults to `%USERPROFILE%\.ollama\models` on C:. A single 9B
   model is 6.6 GB. → **All runtime data goes to `D:\ORACLE\`**; `OLLAMA_MODELS` must be set.
2. **Neither installed Python is suitable.** 3.14 is too new for reliable native wheels
   (`onnxruntime`, `ctranslate2`, `pywinpty`, tree-sitter grammars); 3.10 is ageing out. → `uv`-managed
   standalone 3.12 ([ADR-0002](../../docs/DECISIONS.md#adr-0002--python-312-managed-by-uv)).
3. **Rust and Docker are already present**, so Tauri costs no extra toolchain and Docker can be a tool
   ORACLE drives rather than a thing ORACLE runs in.
4. **Claude Code CLI is installed**, making that integration first-class rather than aspirational.

---

## 2. Corpus survey

```
Asterim              798 git-tracked files: 267 ts · 190 md · 99 png · 91 tsx · 62 js · 30 json
                     also has AGENTS.md, CLAUDE.md, decisions.md, blueprint/, docs/
Source2DemViewer     Rust; target/ contains 3,915 files  ← must never be indexed
GameRecs             449 files; apps/ + infra/ + docker-compose + alembic
GrowAMonster         310 files; Roblox (.rbxlx, Luau), docs/ tasks/ reports/
asterim-pipeline     45 files; Node CLI
MonsterGarden        empty
AsterimDesign        107 files; no git

Obsidian vaults (from %APPDATA%\obsidian\obsidian.json):
  Documents\AI\ML Learning       157 .md   1.5 MB
  Documents\ObsidianNotes          3 .md    25 KB
  Documents\MLAI NOTES\ML\AI       1 .md +  1 .pdf (32 MB)
```

**Total realistic corpus: a few thousand documents → ~30k–80k chunks.**

Two consequences:

- **This decisively kills pgvector/Qdrant/Chroma for v1.** Brute-force KNN over ~80k × 768-dim vectors
  is tens of milliseconds. An ANN index and a database server solve a problem that does not exist here.
  → sqlite-vec ([ADR-0006](../../docs/DECISIONS.md#adr-0006--sqlite-only-storage-two-files-sqlite-vec--fts5)).
- **`Documents\` cannot be indexed wholesale** — it contains Paradox Interactive saves, League of
  Legends configs, Arma 3 data. Confirms the explicit opt-in collection registry in
  [RAG.md](../../docs/RAG.md#2-what-gets-indexed).

Also notable: Asterim already contains `AGENTS.md`, `CLAUDE.md` and `decisions.md` — files written to
orient a coding agent. Forwarding those in a Handoff Packet is far higher value per token than more
source code.

---

## 3. Research — assumptions that were wrong

All checked against primary sources on 2026-08-21.

### 3.1 Qwen3 is not current — Qwen3.5 is

Ollama's library lists **Qwen3.5 (0.8b–122b), Qwen3.6 (27b, 35b), Qwen3.8 (27b)**, all with a `tools`
capability tag. Published Qwen3.5 sizes:

| tag | size | fits ~3.5 GB usable VRAM? |
|---|---|---|
| 0.8b | 1.0 GB | yes, comfortably, with long context |
| 2b | 2.7 GB | yes — tight; needs 8–16k ctx with `q8_0` KV |
| **4b** | **3.4 GB** | **no** — weights alone leave nothing for KV cache |
| 9b | 6.6 GB | no — CPU/hybrid only, ~5 tok/s |

Sizes are larger than a text-only model of that parameter count implies, because the family is
multimodal and the tags include a vision tower. ORACLE needs no vision → [OQ-10](../../docs/OPEN_QUESTIONS.md#oq-10).

KV cache arithmetic for a 2B-class model (~55 KB/token): 16k fp16 ≈ 0.9 GB (does not fit);
16k `q8_0` ≈ 0.46 GB (fits); 8k `q8_0` ≈ 0.23 GB (comfortable). **Context length is a hardware
decision here, not a model decision.**

### 3.2 Pascal is on borrowed time

- CUDA Toolkit **13.0** raised the minimum compute capability to **7.5** (Turing).
- CUDA Toolkit **13.3** removed Maxwell, Pascal and Volta entirely.
- Ollama's GPU docs still list compute **5.0+**, with **5.0–6.2 requiring driver ≥ 570**. Ours is
  582.28, so it works **today** via an older bundled CUDA runner.

→ When Ollama drops that runner, this GPU loses acceleration in an update. CPU fallback is a
requirement, not a nicety. Tracked as [OQ-03](../../docs/OPEN_QUESTIONS.md#oq-03).
**Do not auto-update Ollama without reading release notes.**

### 3.3 Antigravity now has an official CLI — with a blocker

Antigravity 2.0 shipped at I/O 2026: standalone app, **CLI (`agy`, v1.1.x)**, **SDK (v0.1.x)**, and a
Managed Agents API. Headless mode is documented: `agy -p` with `--output-format json|stream-json`,
`--json-schema`, `--print-timeout`, `--continue`/`--conversation`, `--sandbox`,
`--dangerously-skip-permissions`. Events: `init` → `step_update` → `result`.

**But:** open issue [antigravity-cli#76](https://github.com/google-antigravity/antigravity-cli/issues/76)
(21 May 2026, still open) — `agy -p` **silently drops stdout when stdout is not a TTY** (pipes,
redirects, subprocesses). That is precisely how ORACLE would invoke it. The reporter rejected every
workaround (ConPTY wrapper, scheduled task, parsing `cli.log` — which contains only operational
events, not model output).

→ Antigravity is **"Potential", not "Supported"**. [OQ-05](../../docs/OPEN_QUESTIONS.md#oq-05) is a
15-minute experiment that decides whether the adapter gets written at all. The documented
`--output-format json` may postdate the bug report.

### 3.4 Claude Code's contract, pinned

`claude -p` with: `--bare` · `--output-format text|json|stream-json` · `--json-schema` →
`structured_output` · `--include-partial-messages` · `--allowedTools` with prefix-match rule syntax
(`Bash(git diff *)` — **the space before `*` matters**) · `--permission-mode auto|dontAsk|acceptEdits`
· `--resume <id>` (findable from any directory since v2.1.223) · `--add-dir` · `--mcp-config`.
SIGINT ends the turn; SIGTERM exits 143 and kills Bash process trees. Piped stdin capped at 10 MB.
`--output-format json` reports `total_cost_usd`.

**Security-critical:** without `--bare`, `claude -p` runs hooks from a project's `.claude/settings.json`
and connects MCP servers from `.mcp.json` **even in a folder never trusted**, because a `-p` session
shows no trust dialog. ORACLE runs Claude against arbitrary project directories → `--bare` is
mandatory, not an optimisation.

### 3.5 Other corrections

- **Tauri stable is 2.10.1** (March 2026). There is no Tauri 3. Sidecar bundling via
  `tauri.bundle.externalBin` is the supported way to ship the Python backend.
- **Piper TTS was archived in October 2025.** The obvious local-TTS default is gone; Kokoro-82M's
  Russian coverage is unverified. → The voice stack is deliberately left unresolved until Phase 10
  rather than documented with a stale recommendation.

---

## 4. Design conclusions

1. **4 GB VRAM is the binding constraint on the whole project.** Everything else follows: a small
   resident router, a promoted Context Assembler, CPU embeddings, and delegation for real reasoning.
2. **The corpus is small** → SQLite-only storage, and retrieval *quality* is the only thing worth
   optimising.
3. **Three processes, three trust levels** — needed for reliable HALT on Windows, and to keep API keys
   out of the tool executor's address space.
4. **Prompt injection is the top realistic threat**, and nothing in a conventional permission model
   addresses it → taint tracking with tier escalation and provenance shown at the confirmation.
5. **The vendor-neutral Handoff Packet must be a first-class path**, not a fallback bolted on later —
   §3.3 is the evidence that vendor CLIs break in exactly the way that matters.

---

## 5. Dead ends and rejected paths

Recorded so they are not re-explored:

| Explored | Rejected because |
|---|---|
| pgvector / Qdrant / Chroma | A database server for ~80k chunks on a single-user desktop. Measured corpus made this indefensible. LanceDB kept as the documented upgrade path. |
| Celery / RQ for task execution | Requires Redis — a second service and failure mode for one user. In-process asyncio supervisor + SQLite persistence instead. |
| Electron | 150–300 MB baseline RSS on a machine whose RAM is contended by a language model. |
| d3-force for the orbital view | Physics jitter moves nodes between renders, destroying recognisability. Deterministic polar layout instead. |
| Embeddings on GPU | Load/unload thrash against the resident router costs more than it saves; 24 CPU threads sit idle. |
| A general `execute_command` shell tool | Makes every narrower tool decorative and reduces the policy engine to guessing about shell strings. Replaced by intent-shaped tools + a gated argv-only escape hatch. |
| SQLAlchemy ORM + Alembic | Heavy abstraction over a schema we fully control and query with SQLite-specific features (FTS5 `MATCH`, `vec0`) the ORM doesn't model. |
| Native mobile app | Two toolchains and a release process for a single-user tool. PWA over the same API instead. |
| Telegram bot as the mobile UI | No rich approval previews, and it routes task data through a third party — contradicts local-first. |

---

## 6. Next actions

1. **[OQ-05](../../docs/OPEN_QUESTIONS.md#oq-05)** — 15 min, decides a Phase 6 design question:
   `agy -p "say hello" --output-format json > out.json` and check whether `out.json` is empty.
2. **Pull models** with `OLLAMA_MODELS=D:\ORACLE\models` set — `qwen3.5:0.8b` and `qwen3.5:2b`, to
   unblock [OQ-01](../../docs/OPEN_QUESTIONS.md#oq-01) at the start of Phase 1.
3. **Begin [P0-T1](../../docs/current_task.md)** — the walking skeleton.
