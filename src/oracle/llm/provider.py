"""The provider seam (ADR-0009).

Everything above L8 talks to this protocol, never to Ollama. That is what keeps the
model replaceable when Ollama drops Pascal support (OQ-03) and what lets every test run
against FakeProvider without a running runtime.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from oracle.llm.types import Completion, CompletionRequest, Delta, ProviderCaps


@runtime_checkable
class LLMProvider(Protocol):
    model: str

    def capabilities(self) -> ProviderCaps: ...

    async def preflight(self) -> None:
        """Raise ProviderUnavailable if the runtime or model is missing."""
        ...

    async def complete(self, req: CompletionRequest) -> Completion: ...

    def stream(self, req: CompletionRequest) -> AsyncIterator[Delta]: ...

    async def count_tokens(self, text: str) -> int: ...

    async def aclose(self) -> None: ...
