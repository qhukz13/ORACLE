# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** P9-T1 — memory: what ORACLE is allowed to remember, and what it must refuse to forget.
**Done: five of six acceptance criteria.** The sixth — the retrieval recall gate — is **narrowed,
not met**, and the narrowing is the more useful result. Details below.
**Status:** `src/oracle/memory/` exists. Facts, preferences and prior attempts are durable; the
write policy refuses four ways; band 5 is filled; a repeated task's packet carries what was already
tried; the Memory view answers "why does ORACLE think that?" in one click. `make check` green.
**Date:** 2026-08-25

---

## The bands stop being empty

`ContextAssembler` has had bands 5–7 declared and unfed since Phase 1. Band 5 is filled now, in
MEMORY.md §5's priority order, and the ANSWER call goes through the assembler instead of two
hand-built messages — so memory and recent history are budgeted, truncated and provenance-labelled
like everything else.

**Band 6 (retrieval) is deliberately still empty on the interactive answer path**, and that is a
decision rather than an omission: filling it puts the embedder on a path with a measured latency
budget and ~70 ms of headroom. Retrieval already runs where those seconds are free — the Handoff
Packet, where a delegation takes minutes.

## The policy is the subsystem

**Memory is the only place an injection survives the turn it arrived in.** Everything else is
turn-scoped: a document taints one turn, a plan authors one graph, a worker's claim gates nothing
ever. A written memory is read back into future prompts, for months, labelled as ORACLE's own
belief. So `policy.py` is a pure function, says no by default, and is not called from inside the
store — a store that also decided what was permissible would be a place where "just this once"
could be added without anybody noticing.

Four blocks, each with a test: **tainted** (checked first, before the source is even read, so a
document cannot borrow the owner's authority by claiming it) · **mid-plan** · **a single
observation** · **an unapproved inference**. `plan_active` comes from the daemon, not from the
client payload, and a security test checks that against the source.

Two consequences worth stating plainly:

- **Nothing is deleted except by a person.** A fact that loses a conflict is marked `superseded_by`
  and stays readable; `forget()` is the only deletion and nothing inside ORACLE calls it. The
  Memory view renders dropped beliefs under the ones that replaced them.
- **A lower-authority contradiction changes nothing at all.** It returns a question. Auto-deletion
  on contradiction is tempting and wrong — a transient failure would erase a correct fact.

## The criterion that pays for the phase

A delegation fails. The failure becomes a record. The *same objective*, asked again, produces a
packet whose ATTEMPTS.md names it — with nothing wiring the two together, because the signature
matched. There is a test that does exactly that against a real packet on disk.

**No worker claim travels.** `Attempt` has no field for one — a missing field rather than a filter
somebody has to remember — because an attempt is read back into a planning prompt and a Handoff
Packet, which are the two places prose becomes instructions.

Matching is a normalised signature plus token overlap, with **no embedder**, and that is a decision
MEMORY.md §4 did not make: an embedder on the packet-rendering path degrades to "no prior attempts"
exactly when it is unavailable, which is the failure that makes a memory system feel like it has
none. `match()` takes candidates and scores them, so the upgrade is one function when a measurement
asks for it.

## OQ-18: the cheap lever, measured and ruled out

[dev log](../logs/development/2026-08-25-oq18-truncation.md) ·
`scripts/measure_truncation.py` · [OQ-18](OPEN_QUESTIONS.md#oq-18)

OQ-18 said to measure the 512-token truncation first because it is countable without building
anything and would change what the other experiment means. Counted, with `bge-m3`'s own tokenizer
over the real corpus:

- **20.1%** of 12,648 chunks exceed the window; **10.1% of all corpus tokens are never embedded**.
- It is not uniform — **88% of `config` chunks** overflow, against 13% of code.
- The character cap meant to prevent this **is not enforced**: 17% of chunks exceed `MAX_CHARS`,
  the longest by more than double.

**And none of it explains the recall gap.** The seven Russian cases that never enter the candidate
list all point at notes markdown whose chunks fit with room to spare — **0% of their tokens are
lost**. So lever 2 is dead, query translation is what remains, and — this is what the ordering
bought — its result will now be interpretable, because the index it is measured against does not
have a hole where the answers are.

The recall gate is therefore **still not met and not re-argued**: I have removed a hypothesis, not
raised the number. Saying so is more useful than moving the gate to where the numbers already are.

**A methodology mistake worth reusing:** the first run of that script reported those same seven
fixtures as "not in the corpus at all" — a spectacular-looking finding, and wrong. It resolved
fixture paths with an exact dict lookup while `eval_embeddings.hit()` uses a suffix match. A
measurement that resolves identity differently from the system it measures is measuring itself.

## Tests

44 new Python (26 in the security suite) and 7 vitests. Notable:

- a tainted turn refused under **every** source, including `user_stated` with `user_approved`;
- an observation seen five times still losing to what the owner stated, and becoming a question;
- a remembered value that reads like an order arriving in band 5 as labelled text, verbatim;
- memory switched off producing exactly the pre-Phase-9 turn, and a store that raises costing
  band 5 and nothing else;
- the migration adding no new write surface.

## What is deliberately still missing

**Confidence that means anything** (every write lands at 1.0; decay is the only thing that moves
it) · **proposed facts** (rule 4 is enforceable, but nothing observes twice yet) · **§5 item 4**,
"facts referenced in the last 3 turns", which needs a per-turn read timeline that `hit_count` is
not — `bands.recent_facts` exists and says so.

And one friction recorded rather than excepted: "never written mid-plan" is implemented literally,
so **a correction typed while a graph runs is refused**. That is the safe reading, and the fix when
somebody hits it is a queue that applies the write when the graph ends — not an exception in the
policy.

## Next

**P9-T2** ([current_task.md](current_task.md)): the retrieval repair — query translation, which is
now the only hypothesis left for OQ-18, together with the two chunking defects this task measured.
They belong in one task because both change chunk boundaries, and a recall number measured across a
boundary change cannot be compared with the one before it.
