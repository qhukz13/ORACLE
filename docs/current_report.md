# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** P0-T1 — Foundation & walking skeleton
**Status:** `DONE` — all acceptance criteria verified · **Date:** 2026-08-21

---

## What was done

The walking skeleton is built, running, and verified end to end: a keystroke in the desktop UI reaches
the backend, is persisted as a sequenced event, fans out to every connected client, and survives a
backend restart with an exact catch-up. It carries **no intelligence at all** — that is Phase 1.

## What changed

```
pyproject.toml · .python-version · Makefile · scripts/check.py · .claude/launch.json
src/oracle/
  config.py                    settings; data on D: (C: has <40 GB free)
  logsink/__init__.py          structlog JSONL, rotation, trace_id via contextvars
  logsink/redact.py            the one redaction sink — no bypass
  storage/db.py                WAL, pragmas, numbered .sql migration runner
  storage/migrations/0001_init.sql
  core/events.py               envelope, event vocabulary, critical classification
  core/eventlog.py             append · fan-out · resume  <- the spine
  core/sessions.py             session lifecycle
  core/echo.py                 synthetic agent (the seam Phase 1 replaces)
  api/app.py                   FastAPI: REST + WS, backpressure, HALT stub
  api/__main__.py              `oracled`
tests/  test_eventlog.py · test_api.py · test_redaction.py · conftest.py   (31 tests)
apps/desktop/
  src/protocol.ts client.ts store.ts App.tsx main.tsx styles.css
  src/client.test.ts store.test.ts                                          (14 tests)
  src-tauri/src/main.rs backend.rs · Cargo.toml · tauri.conf.json · icons/
logs/development/  2026-08-21-oq05-antigravity-stdout.md
```

## Verification — every acceptance criterion, measured

| Criterion | Result |
|---|---|
| Message → persisted → rendered | ✅ verified in a real browser against the real backend |
| Reload → history intact | ✅ reload replays from the event log |
| Kill backend → offline → reconnect → catch up, **no gaps or duplicates** | ✅ see below |
| Two clients see identical streams | ✅ test + `test_two_clients_see_identical_streams` |
| `trace_id` end to end; planted secret redacted | ✅ `test_secret_in_a_message_never_reaches_the_wire` |
| Gate green | ✅ 45 tests, ruff, mypy --strict, tsc |
| **[OQ-11](OPEN_QUESTIONS.md#oq-11)** no orphaned `oracled` | ✅ resolved — Job Object |

**The restart test, in full.** Sent a message (`seq 13`), force-killed the backend process. UI showed
`Backend offline — reconnecting in 4s`, input disabled, `seq 13` retained. Restarted `oracled`, which
loaded `last_seq=13` from disk. The client reconnected on its own with `since_seq=13`, no gap warning.
A further message continued at **seq 14→22** with both turns intact.

**OQ-11, in full.** Shell pid 7728 spawned `oracled` pid 26764; `Stop-Process -Force` on the shell
(simulating Task Manager "End Task") left `oracled still alive: False` and port 8787 clean. The
Windows Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` is what makes this work — neither `Drop`
on `Child` nor a Tauri window-event handler survives a hard kill.

## Decisions made while building

1. **The backlog→live handover is the subtle part of resume.** `EventLog.stream()` subscribes to the
   fan-out *before* snapshotting the head, then dedupes by `seq`. Subscribing after the read would
   silently drop anything appended in the window. `test_no_gap_when_appends_race_the_backlog_read`
   exists specifically to keep that ordering.
2. **Backpressure drops the subscriber, not the event.** A slow client is disconnected and resumes via
   `since_seq`; silently shedding a critical event would be the worse failure.
3. **`scripts/check.py` is the canonical gate, not the Makefile.** GNU make is not installed (Git for
   Windows doesn't ship it) — a gate that cannot be run is not a gate. The Makefile delegates to it.
4. **Redaction runs before persistence**, so a secret never reaches the database, not just the logs.
5. **HALT exists as a stub already.** It has nothing to stop yet, but the path from API → runtime is
   wired so it is never bolted on later.

## Problems and unresolved issues

- **Starlette now requires `httpx2`**, not `httpx`, for its test client; the old import raises a
  deprecation error at collection time. Dev dependency swapped.
- **`windows` crate needed `Win32_Security`** for `CreateJobObjectW`, and `HANDLE` is not `Send`, so
  `Backend` carries a narrowly-justified `unsafe impl Send/Sync` — documented at the site.
- **Deferred, deliberately:** production packaging. The shell spawns `uv run oracled` for development;
  shipping needs a frozen sidecar binary (PyInstaller or similar). Not needed to satisfy P0 and not
  worth doing before the backend stops changing shape.
- **Not done:** generated TypeScript types. `apps/desktop/src/protocol.ts` is hand-written and marked
  as a temporary exception — Phase 1 should generate it from the pydantic models, since a hand-mirrored
  server model is exactly the drift [API.md](API.md#1-shape) warns about.
- The repo is initialised but **nothing is committed yet** — no commit was requested.
- A stray empty `New folder/` sits in the repo root (created outside this session); left alone.

## Also resolved this session

**[OQ-05](OPEN_QUESTIONS.md#oq-05) — Antigravity stdout.** `agy` v1.1.14 is installed. With
`--output-format json` it returns a complete 257-byte JSON envelope when redirected to a file, and
`stream-json` returns 5 well-formed NDJSON lines. **Issue #76 affects default text mode only.**
Antigravity is promoted from *Potential (blocked)* to **Supported**. One undocumented detail found:
the stream envelope keys the payload by event name (`{"event":"init","init":{…}}`), so an adapter
parses `obj[obj["event"]]`. Full write-up in
[`logs/development/2026-08-21-oq05-antigravity-stdout.md`](../logs/development/2026-08-21-oq05-antigravity-stdout.md).

## Recommended next action

**Start [P1-T1](current_task.md).** Build the **30-case intent fixture set before the router logic** —
it is the gating risk of Phase 1. `qwen3.5:0.8b` is proven to *fit and be fast*; whether it is
*accurate enough* is unknown, and if it isn't, nothing else fits this 4 GB card.

Run everything with:

```
uv run python scripts/check.py
uv run oracled                     # backend
npm --prefix apps/desktop run dev  # UI at http://localhost:5273
```
