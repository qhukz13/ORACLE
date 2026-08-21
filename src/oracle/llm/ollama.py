"""Ollama provider.

ADR-0009: Ollama is the default because it still ships a CUDA runner supporting
compute 6.1 (this GPU is Pascal, and CUDA 13.3 dropped Pascal entirely). It sits behind
`LLMProvider` so llama.cpp can replace it when that runner goes away — see OQ-03.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx2

from oracle.llm.types import (
    Completion,
    CompletionRequest,
    Delta,
    ProviderCaps,
    ProviderUnavailable,
    Usage,
)
from oracle.logsink import get_logger

log = get_logger(__name__)

_CONNECT_TIMEOUT = 3.0
_READ_TIMEOUT = 300.0


class OllamaProvider:
    def __init__(
        self,
        model: str = "qwen3.5:0.8b",
        base_url: str = "http://127.0.0.1:11434",
        num_ctx: int = 16384,
        keep_alive: str = "30m",
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.num_ctx = num_ctx
        # Keep the router resident: measured load cost is 7-14 s warm, 51 s cold.
        # A reload between turns is user-visible and unacceptable (ADR-0004).
        self.keep_alive = keep_alive
        self._client = httpx2.AsyncClient(
            timeout=httpx2.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT)
        )

    def capabilities(self) -> ProviderCaps:
        return ProviderCaps(
            name="ollama",
            model=self.model,
            context_length=self.num_ctx,
            supports_schema=True,
            supports_thinking=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------------------------------------------------------------- preflight

    async def preflight(self) -> None:
        """Raise ProviderUnavailable with an actionable remedy, or return quietly.

        Called before the runtime commits to an LLM path so degradation is a decision,
        not an exception surfacing mid-turn."""
        try:
            r = await self._client.get(f"{self.base_url}/api/tags", timeout=_CONNECT_TIMEOUT)
            r.raise_for_status()
        except Exception as exc:
            raise ProviderUnavailable(
                "Ollama is not reachable", remedy="start Ollama, then retry"
            ) from exc

        names = {m.get("name", "") for m in r.json().get("models", [])}
        if self.model not in names:
            raise ProviderUnavailable(
                f"model {self.model!r} is not pulled",
                remedy=f"ollama pull {self.model}",
            )

    # ---------------------------------------------------------------- generate

    def _body(self, req: CompletionRequest, *, stream: bool) -> dict[str, Any]:
        options: dict[str, Any] = {"num_ctx": self.num_ctx, "temperature": req.temperature}
        if req.max_tokens is not None:
            options["num_predict"] = req.max_tokens
        if req.stop:
            options["stop"] = req.stop

        body: dict[str, Any] = {
            "model": self.model,
            "messages": [m.model_dump() for m in req.messages],
            "stream": stream,
            "think": req.think,
            "keep_alive": self.keep_alive,
            "options": options,
        }
        if req.schema_ is not None:
            body["format"] = req.schema_
        return body

    async def complete(self, req: CompletionRequest) -> Completion:
        try:
            r = await self._client.post(
                f"{self.base_url}/api/chat", json=self._body(req, stream=False)
            )
            r.raise_for_status()
        except httpx2.HTTPError as exc:
            raise ProviderUnavailable("Ollama request failed", remedy="check Ollama") from exc

        data = r.json()
        return Completion(
            text=(data.get("message") or {}).get("content", ""),
            model=self.model,
            usage=_usage(data),
            truncated=data.get("done_reason") == "length",
        )

    async def stream(self, req: CompletionRequest) -> AsyncIterator[Delta]:
        body = self._body(req, stream=True)
        try:
            async with self._client.stream("POST", f"{self.base_url}/api/chat", json=body) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    text = (chunk.get("message") or {}).get("content", "")
                    if chunk.get("done"):
                        yield Delta(text=text, done=True, usage=_usage(chunk))
                        return
                    if text:
                        yield Delta(text=text)
        except httpx2.HTTPError as exc:
            raise ProviderUnavailable("Ollama stream failed", remedy="check Ollama") from exc

    # ---------------------------------------------------------------- tokens

    async def count_tokens(self, text: str) -> int:
        """Exact count from the model itself.

        Ollama exposes no tokenizer endpoint, so we ask for zero tokens of output and
        read `prompt_eval_count`. Costs a round trip, which is why the assembler uses
        an approximation in the hot path and this only calibrates it
        (see oracle.context.tokens)."""
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": text}],
            "stream": False,
            "think": False,
            "keep_alive": self.keep_alive,
            "options": {"num_ctx": self.num_ctx, "num_predict": 0},
        }
        r = await self._client.post(f"{self.base_url}/api/chat", json=body)
        r.raise_for_status()
        return int(r.json().get("prompt_eval_count", 0))


def _usage(data: dict[str, Any]) -> Usage:
    return Usage(
        prompt_tokens=int(data.get("prompt_eval_count", 0)),
        completion_tokens=int(data.get("eval_count", 0)),
        load_ms=int(data.get("load_duration", 0) // 1_000_000),
        prompt_eval_ms=int(data.get("prompt_eval_duration", 0) // 1_000_000),
        eval_ms=int(data.get("eval_duration", 0) // 1_000_000),
    )
