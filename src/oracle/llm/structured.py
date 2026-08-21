"""Schema-constrained structured output.

Small models produce malformed JSON often enough that "parse and hope" is not a
strategy (docs/TECH_STACK.md). The contract is exactly:

    1. request with a JSON Schema      -> constrained decoding
    2. validate with pydantic          -> typed object, or error
    3. ONE repair attempt, fed the validation error
    4. still failing -> deterministic fallback, and count it

Never a third retry. That is where latency goes to die, and a model that failed twice
under constrained decoding is not going to succeed on the third pass.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from oracle.llm.provider import LLMProvider
from oracle.llm.types import CompletionRequest, Message, Usage
from oracle.logsink import get_logger

log = get_logger(__name__)


@dataclass
class StructuredResult[T: BaseModel]:
    """The parsed value plus what it cost. Latency is a first-class result here: the
    router runs on every turn and its cost is the system's felt speed."""

    value: T
    usage: Usage
    repairs: int = 0


MAX_REPAIRS = 1


@dataclass
class StructuredStats:
    """Exposed on /api/v1/status. If the failure rate exceeds ~2%, the model tier or
    the schema is wrong — it is a design signal, not a metric to admire."""

    attempts: int = 0
    repairs: int = 0
    failures: int = 0
    by_schema: dict[str, int] = field(default_factory=dict)

    @property
    def failure_rate(self) -> float:
        return self.failures / self.attempts if self.attempts else 0.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "repairs": self.repairs,
            "failures": self.failures,
            "failure_rate": round(self.failure_rate, 4),
        }


class StructuredOutputError(Exception):
    """Both attempts failed. The caller degrades deterministically — it does not retry."""

    def __init__(self, model_name: str, raw: str, error: str) -> None:
        super().__init__(f"{model_name}: {error}")
        self.raw = raw
        self.error = error


def _strip_fence(text: str) -> str:
    """Constrained decoding usually prevents fences, but not always."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1] if "\n" in t else t
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _extract_object(text: str) -> str:
    """Take the outermost {...}. Cheap and deterministic; no model call."""
    t = _strip_fence(text)
    start, end = t.find("{"), t.rfind("}")
    return t[start : end + 1] if 0 <= start < end else t


async def generate_structured[T: BaseModel](
    provider: LLMProvider,
    messages: list[Message],
    schema: type[T],
    *,
    stats: StructuredStats | None = None,
    temperature: float = 0.0,
    max_tokens: int | None = 512,
    call_type: Any = None,
) -> StructuredResult[T]:
    """Return a validated `schema` instance with its usage, or raise."""
    stats = stats or StructuredStats()
    json_schema = schema.model_json_schema()
    convo = list(messages)
    last_raw = ""
    last_err = ""

    for attempt in range(MAX_REPAIRS + 1):
        stats.attempts += 1
        req = CompletionRequest(
            messages=convo,
            schema=json_schema,
            think=False,  # mandatory: OQ-01
            temperature=temperature,
            max_tokens=max_tokens,
            **({"call_type": call_type} if call_type is not None else {}),
        )
        completion = await provider.complete(req)
        last_raw = completion.text

        try:
            value = schema.model_validate_json(_extract_object(last_raw))
            return StructuredResult(value=value, usage=completion.usage, repairs=attempt)
        except (ValidationError, json.JSONDecodeError) as exc:
            last_err = str(exc)[:600]
            if attempt >= MAX_REPAIRS:
                break
            stats.repairs += 1
            log.warning(
                "structured.repair",
                schema=schema.__name__,
                error=last_err[:200],
                model=provider.model,
            )
            # Feed the model its own output and the specific validation error. A bare
            # "try again" wastes the attempt.
            convo = [
                *messages,
                Message(role="assistant", content=last_raw[:1000]),
                Message(
                    role="user",
                    content=(
                        "That did not validate against the required schema.\n"
                        f"Error: {last_err}\n"
                        "Reply with ONLY the corrected JSON object."
                    ),
                ),
            ]

    stats.failures += 1
    stats.by_schema[schema.__name__] = stats.by_schema.get(schema.__name__, 0) + 1
    log.error("structured.failed", schema=schema.__name__, error=last_err[:200])
    raise StructuredOutputError(schema.__name__, last_raw, last_err)
