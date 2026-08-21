"""LLM provider vocabulary.

Vendor shapes stop at the adapter boundary (docs/ARCHITECTURE.md#4-layers, L8): nothing
above this module knows what Ollama's JSON looks like.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CallType(StrEnum):
    """Call types carry different token budgets because TTFT is dominated by prompt
    processing on this GPU (docs/AGENT_RUNTIME.md#5-context-budget). One global budget
    would make the router take ~4 s to answer 'run the tests'."""

    ROUTE = "route"
    #: Choosing a tool. A separate type from ROUTE because the shape is inverted: a
    #: small system prompt and a comparatively large TOOLS band, where routing is the
    #: other way round. Sharing ROUTE's allowances silently TRUNCATED the tool
    #: descriptions — which are the entire basis for the model's choice.
    SELECT = "select"
    ANSWER = "answer"
    REASON = "reason"
    SUMMARIZE = "summarize"


#: Hard ceilings, measured not guessed. See logs/development/2026-08-21-oq01-*.
BUDGETS: dict[CallType, int] = {
    # ROUTE is 2000, not the 1200 originally designed, to fit few-shot examples that
    # lift intent accuracy from 63% to 93%. Cost is real (~570 ms prompt-eval/turn):
    # Ollama does not reuse a shared prefix across differing user messages.
    CallType.ROUTE: 2000,
    # Selection: measured at 180 (system) + 229 (examples) + up to 211 (descriptions)
    # + the request. 1200 leaves headroom for the catalogue to grow to the 40-tool cap
    # without silently cutting a summary off mid-sentence.
    CallType.SELECT: 1200,
    CallType.ANSWER: 2400,
    CallType.REASON: 8000,
    CallType.SUMMARIZE: 16000,
}


class Message(BaseModel):
    model_config = ConfigDict(frozen=True)
    role: Literal["system", "user", "assistant"]
    content: str


class CompletionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    messages: list[Message]
    call_type: CallType = CallType.ANSWER
    #: Qwen3.5 is a thinking model. Left on, it spends hundreds of tokens reasoning
    #: about trivial replies and returns an EMPTY response field. Off by default.
    think: bool = False
    temperature: float = 0.0
    max_tokens: int | None = None
    #: JSON Schema for constrained decoding. Never parse structure out of prose.
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    stop: list[str] = Field(default_factory=list)


class Usage(BaseModel):
    model_config = ConfigDict(frozen=True)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    #: Ollama reports these separately; they are billed to us in latency either way.
    load_ms: int = 0
    prompt_eval_ms: int = 0
    eval_ms: int = 0

    @property
    def ttft_ms(self) -> int:
        """Time to first token, as the user experiences it on a resident model."""
        return self.load_ms + self.prompt_eval_ms


class Completion(BaseModel):
    model_config = ConfigDict(frozen=True)
    text: str
    model: str
    usage: Usage = Usage()
    truncated: bool = False


class Delta(BaseModel):
    model_config = ConfigDict(frozen=True)
    text: str = ""
    done: bool = False
    usage: Usage | None = None


class ProviderCaps(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    model: str
    context_length: int
    supports_schema: bool
    supports_thinking: bool
    streaming: bool = True


class ProviderUnavailable(Exception):
    """The backing runtime is not reachable or the model is not present.

    Distinct from a generation failure: this degrades the whole system to
    deterministic-only mode rather than failing one turn
    (docs/ARCHITECTURE.md#8-degradation--what-happens-when-a-piece-is-missing)."""

    def __init__(self, reason: str, *, remedy: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.remedy = remedy
