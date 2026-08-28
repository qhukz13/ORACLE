"""Intent classification.

The model fills in a bounded schema; it does not author control flow
(docs/AGENT_RUNTIME.md#1-the-core-insight). Two rules do most of the safety work:

  * `project` is validated against the registry and never trusted as free text. A
    hallucinated name resolves to nothing and triggers a clarification, rather than
    becoming a filesystem path.
  * below `CONFIDENCE_THRESHOLD` we ask instead of guessing. A wrong confident action
    costs far more than one question.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from oracle.context.budget import Band, ContextAssembler, Item
from oracle.llm.provider import LLMProvider
from oracle.llm.structured import StructuredStats, generate_structured
from oracle.llm.types import CallType, Usage
from oracle.logsink import get_logger

log = get_logger(__name__)

IntentLabel = Literal[
    "chat",
    "question",
    "investigate",
    "modify",
    "run",
    "search",
    "status",
    "delegate",
    "pipeline",
    "control",
    #: Added 2026-08-26 (P12-T2). The first label whose object is a **project rather
    #: than a request**: it means "read this project's state and decide", which is a
    #: planning call, not a tool call. The router stays a router — it does not decide
    #: the work, it decides that the work is unknown and must be planned.
    #:
    #: `MEASURED 2026-08-28` (OQ-25): 97.1% intent accuracy on 34 cases with this label
    #: in — up from 93.3% with ten labels — and all four `continue` cases route AND
    #: resolve their project slot. The feared run/continue confusion appeared once, in
    #: reverse ("собери GameRecs" -> continue). One deterministic exception: the model
    #: never emits the project name ORACLE (9/9 null; a prompt instruction did not move
    #: it) — `_named_project` in pipeline.py carries that case by design.
    "continue",
]

#: MEASURED FINDING (2026-08-21): Ollama's structured output enforces enums and
#: required fields, but NOT numeric `minimum`/`maximum` from the JSON Schema. A float
#: `confidence: ge=0, le=1` produced `95` on 12 of 30 fixture cases and failed
#: validation every time. A three-value enum is enforced at the token level and is a
#: far more realistic ask of a 0.8B model — we only ever threshold it anyway.
Confidence = Literal["high", "medium", "low"]

CONFIDENCE_SCORE: dict[str, float] = {"high": 0.9, "medium": 0.65, "low": 0.3}
CONFIDENCE_THRESHOLD = 0.55  # -> "low" asks, "medium"/"high" proceed


class Intent(BaseModel):
    """Deliberately three fields.

    Every generated token costs latency on this GPU, and the router runs on every turn.
    `targets` and `needs_plan` were dropped from the routing call once measurement
    showed generation — not prompt processing — dominates route latency at this prompt
    size. They belong to a later, richer call if a planner ever needs them."""

    model_config = ConfigDict(extra="ignore")

    intent: IntentLabel
    project: str | None = None
    confidence: Confidence = "medium"

    @property
    def score(self) -> float:
        return CONFIDENCE_SCORE[self.confidence]


class Classification(BaseModel):
    """What the router hands to the runtime: the raw intent plus the decisions made
    about it (project resolution, threshold), so the trace explains itself."""

    intent: Intent
    resolved_project: str | None
    needs_clarification: bool
    clarification: str | None = None
    tokens_used: int = 0
    usage: Usage | None = None
    repairs: int = 0


_SYSTEM = """Classify the user's request. Reply with ONLY the JSON object.

Decide by what the user WANTS TO HAPPEN, not by the grammar of the sentence.

control      stop / cancel / halt the assistant. Highest priority.
status       "what is happening right now" — agent state, git state, is it clean
continue     resume unfinished work on a project. No specific task is named.
run          execute something that already exists: tests, build, lint, a script
pipeline     run a NAMED predefined workflow (the word pipeline, or a workflow name)
investigate  something is BROKEN, failing, slow or unexpected -> find out why
question     asking to be TOLD something. Nothing is broken. No action needed.
search       locate a file, note, symbol or text. The user wants a location.
modify       one small specific edit the assistant can just do
delegate     large / multi-file / "entire" / "whole" work, or names another AI agent
chat         greeting or small talk

Boundaries that matter:
- broken or failing -> investigate, even when phrased as a question ("why is X broken")
- "what does X do" -> question. Nothing is broken.
- "fix the typo in README" -> modify. "rewrite the whole auth module" -> delegate.
- "ask Claude to ..." -> delegate, always.
- "is my repo clean" -> status, not investigate.
- "continue Asterim" -> continue. NO specific work is named; the assistant must look it up.
- "run the Asterim tests" -> run. A specific, already-existing thing is named.

project: copy EXACTLY one name from the known list, or null. NEVER invent one.
         null if the request does not clearly name a project.

confidence: high   the intent and project are both obvious
            medium one of them required a small inference
            low    the request is vague, or you guessed which project was meant"""

#: Few-shot examples. The single highest-leverage accuracy lever for a 0.8B classifier,
#: and cheap: prompt processing runs ~1700 tok/s here while generation runs at 45 tok/s.
#: Chosen to cover the boundaries that actually confused the model on the fixture set,
#: with Russian on the pairs where it degraded most (status, modify, chat, delegate).
_EXAMPLES = """Examples:

"run the tests for Asterim" -> {"intent":"run","project":"Asterim","confidence":"high"}
"запусти тесты для Asterim" -> {"intent":"run","project":"Asterim","confidence":"high"}
"why is Asterim authentication broken" -> {"intent":"investigate","project":"Asterim","confidence":"high"}
"почему сломалась авторизация в Asterim" -> {"intent":"investigate","project":"Asterim","confidence":"high"}
"what does the relay Dockerfile do" -> {"intent":"question","project":null,"confidence":"high"}
"чем отличается pgvector от sqlite-vec" -> {"intent":"question","project":null,"confidence":"high"}
"что ты сейчас делаешь" -> {"intent":"status","project":null,"confidence":"high"}
"is Asterim clean" -> {"intent":"status","project":"Asterim","confidence":"high"}
"find where the token refresh lives" -> {"intent":"search","project":null,"confidence":"high"}
"поправь опечатку в README у GameRecs" -> {"intent":"modify","project":"GameRecs","confidence":"high"}
"refactor the entire auth module" -> {"intent":"delegate","project":null,"confidence":"high"}
"реализуй полностью новую систему инвентаря" -> {"intent":"delegate","project":null,"confidence":"high"}
"ask Claude to fix the migration" -> {"intent":"delegate","project":null,"confidence":"high"}
"run the asterim-check pipeline" -> {"intent":"pipeline","project":"Asterim","confidence":"high"}
"запусти пайплайн проверки" -> {"intent":"pipeline","project":null,"confidence":"medium"}
"hey" -> {"intent":"chat","project":null,"confidence":"high"}
"привет" -> {"intent":"chat","project":null,"confidence":"high"}
"stop what you're doing" -> {"intent":"control","project":null,"confidence":"high"}
"continue Asterim" -> {"intent":"continue","project":"Asterim","confidence":"high"}
"продолжай работу над Asterim" -> {"intent":"continue","project":"Asterim","confidence":"high"}
"look at our unfinished tasks and keep working" -> {"intent":"continue","project":null,"confidence":"medium"}
"pick up where we left off in GameRecs" -> {"intent":"continue","project":"GameRecs","confidence":"high"}
"run the tests" -> {"intent":"run","project":null,"confidence":"low"}
"почини это" -> {"intent":"modify","project":null,"confidence":"low"}"""


def _project_block(projects: list[str]) -> str:
    return "Known projects: " + (", ".join(projects) if projects else "(none)")


class IntentClassifier:
    def __init__(
        self,
        provider: LLMProvider,
        projects: list[str] | None = None,
        assembler: ContextAssembler | None = None,
        stats: StructuredStats | None = None,
    ) -> None:
        self._provider = provider
        self._projects = projects or []
        self._assembler = assembler or ContextAssembler()
        self.stats = stats or StructuredStats()

    def set_projects(self, projects: list[str]) -> None:
        self._projects = projects

    async def classify(self, text: str) -> Classification:
        assembled = self._assembler.assemble(
            CallType.ROUTE,
            [
                Item(Band.SYSTEM, _SYSTEM, role="system", provenance="system"),
                Item(Band.SYSTEM, _EXAMPLES, role="system", provenance="system"),
                Item(
                    Band.TOOLS,
                    _project_block(self._projects),
                    role="system",
                    provenance="system",
                ),
                Item(Band.TASK, f"Request: {text}", role="user", provenance="user"),
            ],
        )

        result = await generate_structured(
            self._provider,
            assembled.messages,
            Intent,
            stats=self.stats,
            max_tokens=80,
            call_type=CallType.ROUTE,
        )
        intent = result.value

        # The model may echo a project that does not exist. Case-insensitive match
        # against the registry; anything else becomes None, not a path.
        resolved = self._resolve_project(intent.project)
        hallucinated = intent.project is not None and resolved is None

        needs_clarification = intent.score < CONFIDENCE_THRESHOLD or hallucinated
        clarification = None
        if needs_clarification:
            clarification = self._clarify(intent, hallucinated)

        if hallucinated:
            log.warning("intent.project_hallucinated", proposed=intent.project)

        return Classification(
            intent=intent,
            resolved_project=resolved,
            needs_clarification=needs_clarification,
            clarification=clarification,
            tokens_used=assembled.tokens,
            usage=result.usage,
            repairs=result.repairs,
        )

    def _resolve_project(self, name: str | None) -> str | None:
        if not name:
            return None
        lowered = name.strip().lower()
        for p in self._projects:
            if p.lower() == lowered:
                return p
        return None

    def _clarify(self, intent: Intent, hallucinated: bool) -> str:
        if hallucinated:
            known = ", ".join(self._projects) or "none configured"
            return f"I don't know a project called {intent.project!r}. Known projects: {known}."
        if intent.project is None and intent.intent in {"run", "modify", "investigate", "pipeline"}:
            known = ", ".join(self._projects) or "none configured"
            return f"Which project did you mean? {known}"
        return "I'm not sure what you're asking for — can you rephrase?"
