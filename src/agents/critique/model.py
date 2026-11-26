"""The single seam between the critique panel and a model provider."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_google_genai import ChatGoogleGenerativeAI

# Mirrors langchain_google_genai.chat_models._REFUSAL_FINISH_REASONS; a test pins the equality.
REFUSAL_FINISH_REASONS = frozenset({"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"})

_TRACING_VARS = (
    "LANGCHAIN_TRACING_V2",
    "LANGCHAIN_TRACING",
    "LANGSMITH_TRACING",
    "LANGSMITH_TRACING_V2",
)


class TracingEnabledRefusal(RuntimeError):  # noqa: N818 - name fixed by the packet contract
    """LangChain tracing would ship the manuscript to a third party."""


class ProviderCallError(RuntimeError):
    """A provider call failed; the message is only the exception type name."""


@dataclass(frozen=True)
class ModelReply:
    text: str
    finish_reason: str
    input_tokens: int | None
    output_tokens: int | None


class ReviewModel(Protocol):
    model_id: str
    temperature: float

    async def complete(self, prompt: str) -> ModelReply: ...


def refuse_if_tracing() -> None:
    for var in _TRACING_VARS:
        if os.environ.get(var, "").strip().lower() in {"true", "1"}:
            raise TracingEnabledRefusal(var)


class GeminiReviewModel:
    """One provider request per ``complete``: the chat model must not retry."""

    def __init__(self, chat: ChatGoogleGenerativeAI, rate_limiter: Any) -> None:
        # max_retries=0 means "SDK default" and 6 means six resends of the whole manuscript.
        if chat.max_retries != 1:
            raise ValueError(
                "chat.max_retries must be 1 so one call is one provider request"
            )
        if chat.temperature is None:
            raise ValueError("chat.temperature must be set")
        self._chat = chat
        self._limiter = rate_limiter
        self.model_id = chat.model
        self.temperature = chat.temperature

    async def complete(self, prompt: str) -> ModelReply:
        refuse_if_tracing()
        try:
            async with self._limiter.acquire():
                message = await self._chat.ainvoke(prompt)
        except Exception as exc:
            raise ProviderCallError(type(exc).__name__) from None
        usage: Mapping[str, Any] = message.usage_metadata or {}
        return ModelReply(
            message.text,
            str(message.response_metadata.get("finish_reason", "")),
            usage.get("input_tokens"),
            usage.get("output_tokens"),
        )
