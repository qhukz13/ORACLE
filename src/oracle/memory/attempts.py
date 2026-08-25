"""Prior attempts: signatures, matching, and turning a finished task into a record.

MEMORY.md §4 calls this "the highest-value memory for a delegation-oriented agent, and
the one most systems omit", and it is the piece that pays for the whole subsystem:
"Claude already tried adding a null check here on the 19th and the tests still failed for
reason X" is worth more than another thousand tokens of source.

**Matching is deterministic in v1, and that is a decision rather than a shortcut.**
MEMORY.md §4 describes embedding similarity plus a project match, and §7 permits fuzziness
here precisely because an attempt "surfaces text a human reads rather than a value the
agent acts on". What is built instead is a normalised signature with a token-overlap
fallback, for three reasons worth writing down:

* it puts **no model on the packet-rendering path**. The embedder already costs seconds in
  curation and degrades to a thinner packet when it is missing; a matcher that degraded
  the same way would silently stop surfacing prior attempts — the exact failure that makes
  a memory system feel like it has none.
* it is decidable offline, so every test here runs without the ONNX models on disk.
* the corpus is tiny. Attempts accumulate at a handful per day, and Jaccard over
  normalised goal tokens is measurably adequate at that scale, where it would not be over
  a document corpus.

The upgrade is a drop-in: `match()` takes candidates and scores them, so swapping the
score for cosine similarity changes one function. Do it when a measurement says the recall
is short, not before.

**No worker claim travels.** `approach` is ORACLE's own account — the objective it sent
and what it measured coming back. What the worker said about its own work stays in the
task row, labelled, because an attempt is read back into a planning prompt and a Handoff
Packet, which are the two places prose becomes instructions.
"""

from __future__ import annotations

import re
from typing import Any

from oracle.core.events import new_id
from oracle.logsink import get_logger
from oracle.memory.models import Attempt

log = get_logger(__name__)

#: Words that carry no signal in a task goal. Short and English-only on purpose: a big
#: stop-word list is a place for a wrong exclusion to hide, and every extra entry makes
#: two different goals more likely to collide into one signature.
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "make",
        "of",
        "on",
        "or",
        "that",
        "the",
        "then",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
    }
)

#: How much of two goals' meaningful words must overlap before they count as the same
#: task. 0.6 is strict: a false match puts *someone else's* dead end in front of a worker,
#: which is worse than showing nothing, and the exact-signature path already catches the
#: repeat-of-the-same-request case that matters most.
MATCH_THRESHOLD = 0.6

#: How many prior attempts reach a packet or a prompt. Small, because they compete for the
#: same budget as the source the worker actually needs (MEMORY.md §5: ~250 tokens).
DEFAULT_LIMIT = 3

_WORD = re.compile(r"[a-z0-9_]+")


def normalise(goal: str) -> tuple[str, ...]:
    """The meaningful words of a goal, lowercased and de-duplicated in order.

    Deliberately not stemmed: a stemmer is another dependency and another thing that
    behaves differently in Russian, and the signature only has to be stable, not clever."""
    seen: dict[str, None] = {}
    for word in _WORD.findall(goal.lower()):
        if word not in STOPWORDS and len(word) > 1:
            seen.setdefault(word, None)
    return tuple(seen)


def signature(goal: str, project: str | None = None) -> str:
    """A stable key for "this task, again".

    Sorted, so that "fix the auth tests" and "the auth tests, fix" are one signature —
    word order is not the thing being matched. Scoped by project, because a task about
    Asterim is not a task about GameRecs (MEMORY.md §7)."""
    words = sorted(normalise(goal))
    return f"{(project or '').lower()}:{' '.join(words)}"


def similarity(left: str, right: str) -> float:
    """Jaccard over normalised words. 1.0 for the same goal, 0.0 for disjoint ones."""
    a, b = set(normalise(left)), set(normalise(right))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def match(
    goal: str,
    candidates: list[Attempt],
    *,
    threshold: float = MATCH_THRESHOLD,
    limit: int = DEFAULT_LIMIT,
) -> list[Attempt]:
    """The prior attempts at *this* task, best first.

    Ties break towards the more recent attempt: two equally-similar dead ends are worth
    the same to a worker, and the newer one is likelier to describe code that still
    exists."""
    scored = [(similarity(goal, c.goal), c) for c in candidates]
    keep = [(score, c) for score, c in scored if score >= threshold]
    keep.sort(key=lambda pair: (pair[0], pair[1].at), reverse=True)
    return [c for _score, c in keep[:limit]]


def from_task(task: Any, *, project: str = "", agent: str = "") -> Attempt:
    """A finished task row becomes a durable record.

    `Any` rather than `Task` on purpose: importing the orchestration layer here would make
    memory depend on the supervisor, when the relationship is the other way round —
    the supervisor records, memory stores. The three attributes read are the ones every
    task row has had since P7-T1.

    The outcome vocabulary is MEMORY.md's three, not `TaskStatus`'s nine. `CANCELLED` and
    `SKIPPED` become `abandoned` because from the point of view of "was this tried?" they
    are the same fact: nobody finished it, and nothing was learned about whether it would
    have worked."""
    status = str(getattr(task, "status", "")).lower()
    result = getattr(task, "result", None)
    spec = getattr(task, "spec", None)
    goal = getattr(spec, "objective", "") if spec else ""
    outcome = (
        "success"
        if status == "succeeded"
        else "abandoned"
        if status in ("cancelled", "skipped")
        else "failure"
    )
    evidence: dict[str, Any] = dict(getattr(result, "evidence", {}) or {}) if result else {}
    error = getattr(getattr(result, "error", None), "message", None) if result else None
    return Attempt(
        id=new_id("att"),
        task_signature=signature(goal, project or getattr(spec, "project", None)),
        goal=goal,
        project=project or (getattr(spec, "project", None) or ""),
        # ORACLE's own account: what it asked for and what it measured. `result.claim`
        # is not read here and there is nowhere on `Attempt` to put it.
        approach=_approach(spec, result, evidence),
        agent=agent or (getattr(task, "agent", None) or ""),
        outcome=outcome,  # type: ignore[arg-type]
        what_failed=error if outcome != "success" else None,
        files_touched=_files(evidence),
        task_id=getattr(task, "id", None),
    )


def _approach(spec: Any, result: Any, evidence: dict[str, Any]) -> str:
    """One paragraph: the role that was asked, and the measurements that came back."""
    role = getattr(spec, "role", "") if spec else ""
    parts = [f"asked a {role} to: {getattr(spec, 'objective', '')}" if role else ""]
    summary = getattr(result, "summary", "") if result else ""
    if summary:
        parts.append(summary)
    measured = ", ".join(
        f"{key}={evidence[key]}"
        for key in ("diff_lines", "exit_code", "new_failures", "delta_passed", "branch")
        if key in evidence
    )
    if measured:
        parts.append(f"ORACLE measured {measured}")
    return "; ".join(p for p in parts if p)


def _files(evidence: dict[str, Any]) -> tuple[str, ...]:
    """Files ORACLE saw change. Only from evidence — a worker's list of what it touched is
    a claim, and the diff is the measurement."""
    touched = evidence.get("files_touched") or evidence.get("files")
    if isinstance(touched, list):
        return tuple(str(f) for f in touched)
    branch = evidence.get("branch")
    return (f"branch:{branch}",) if isinstance(branch, str) and branch else ()


def as_packet_attempts(attempts: list[Attempt]) -> list[Any]:
    """The Handoff Packet's own `Attempt` shape (date, agent, summary).

    Two vocabularies for "what was tried" is how the replan prompt and the packet start
    disagreeing, so this is the one conversion and it lives here rather than in the packet
    renderer — memory owns the record, the packet owns the rendering."""
    from oracle.handoff.packet import Attempt as PacketAttempt

    return [
        PacketAttempt(date=a.at[:10], agent=a.agent or "unknown", summary=a.render())
        for a in attempts
    ]


def render_block(attempts: list[Attempt]) -> str:
    """The band-5 form: a compact block, labelled as ORACLE's own record so it is never
    mistaken for retrieved document text (MEMORY.md §5)."""
    if not attempts:
        return ""
    lines = ["ORACLE has tried this before:"]
    lines.extend(f"- {a.render()}" for a in attempts)
    return "\n".join(lines)
