"""FakeProvider — deterministic replay.

Required, not optional (docs/TESTING.md#1-the-three-properties-that-make-this-testable).
It is what turns a non-deterministic agent into something with a regression suite: no
test may require Ollama to be running.

Two modes:
  scripted  — canned responses, matched by a predicate or consumed in order
  replay    — responses recorded from a real run, keyed by a hash of the request
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from oracle.llm.types import (
    Completion,
    CompletionRequest,
    Delta,
    ProviderCaps,
    ProviderUnavailable,
    Usage,
)

Predicate = Callable[[CompletionRequest], bool]


def request_key(req: CompletionRequest) -> str:
    """Stable identity for a request, so a recording can be looked up on replay."""
    blob = json.dumps(
        {
            "messages": [m.model_dump() for m in req.messages],
            "schema": req.schema_,
            "think": req.think,
            "temperature": req.temperature,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class FakeProvider:
    def __init__(
        self,
        responses: list[str] | None = None,
        *,
        model: str = "fake",
        unavailable: bool = False,
        recordings: dict[str, str] | None = None,
    ) -> None:
        self._queue = list(responses or [])
        self._rules: list[tuple[Predicate, str]] = []
        self._recordings = recordings or {}
        self.model = model
        self.unavailable = unavailable
        #: Every request seen, so tests can assert on what was actually sent —
        #: including that `think` was False and the budget was respected.
        self.calls: list[CompletionRequest] = []

    # ------------------------------------------------------------ construction

    def when(self, predicate: Predicate, response: str) -> FakeProvider:
        self._rules.append((predicate, response))
        return self

    @classmethod
    def from_file(cls, path: Path, **kw: object) -> FakeProvider:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(recordings=data, **kw)  # type: ignore[arg-type]

    # ------------------------------------------------------------ provider API

    def capabilities(self) -> ProviderCaps:
        return ProviderCaps(
            name="fake",
            model=self.model,
            context_length=16384,
            supports_schema=True,
            supports_thinking=True,
        )

    async def preflight(self) -> None:
        if self.unavailable:
            raise ProviderUnavailable("fake provider is configured unavailable")

    async def aclose(self) -> None:
        return None

    def _resolve(self, req: CompletionRequest) -> str:
        for predicate, response in self._rules:
            if predicate(req):
                return response
        key = request_key(req)
        if key in self._recordings:
            return self._recordings[key]
        if self._queue:
            return self._queue.pop(0)
        raise AssertionError(
            f"FakeProvider has no response for request {key}. "
            "Add one with .when(...), pass it in `responses`, or record it."
        )

    async def complete(self, req: CompletionRequest) -> Completion:
        await self.preflight()
        self.calls.append(req)
        text = self._resolve(req)
        return Completion(
            text=text,
            model=self.model,
            usage=Usage(prompt_tokens=0, completion_tokens=len(text.split())),
        )

    async def stream(self, req: CompletionRequest) -> AsyncIterator[Delta]:
        await self.preflight()
        self.calls.append(req)
        text = self._resolve(req)
        for word in text.split(" "):
            yield Delta(text=word + " ")
        yield Delta(done=True, usage=Usage(completion_tokens=len(text.split())))

    async def count_tokens(self, text: str) -> int:
        return len(text) // 4
