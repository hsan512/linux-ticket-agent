"""LLM calls via LangChain's ChatOpenAI, with token, cost, and latency accounting.

- invoke_structured: output constrained to a Pydantic schema (OpenAI Structured Outputs)
- invoke_tools:      model may emit tool calls (OpenAI function calling); we execute them ourselves
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Generic, TypeVar

from langchain_core.messages import AIMessage, BaseMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from agent.config import cost_usd

T = TypeVar("T", bound=BaseModel)


@dataclass
class Usage:
    """Accounting for one API call; copied into the node's trace entry by llm_trace."""

    model: str
    input_tokens: int
    output_tokens: int  # includes reasoning tokens
    reasoning_tokens: int
    cost_usd: float
    latency_s: float


@dataclass
class LLMResult(Usage, Generic[T]):
    """Result of invoke_structured: usage plus the parsed schema object."""

    parsed: T | None = None


@dataclass
class ToolCallResult(Usage):
    """Result of invoke_tools: usage plus the model's message (its tool_calls may be empty)."""

    message: AIMessage | None = None


@lru_cache(maxsize=None)
def chat_model(model: str, reasoning_effort: str | None = None) -> ChatOpenAI:
    """One shared client per (model, reasoning_effort)."""
    # max_retries: the OpenAI client retries 429/5xx/timeouts with exponential backoff.
    # timeout 60s: normal calls take 1-10s; agent-v1-test had one call hang until a 180s timeout.
    return ChatOpenAI(model=model, reasoning_effort=reasoning_effort, max_retries=4, timeout=60)


def _usage(model: str, message: AIMessage, latency_s: float) -> Usage:
    """Token counts from the response metadata (0 when missing) and their cost."""
    usage = message.usage_metadata or {}
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    return Usage(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=(usage.get("output_token_details") or {}).get("reasoning", 0),
        cost_usd=cost_usd(model, input_tokens, output_tokens),
        latency_s=latency_s,
    )


def invoke_structured(
    model: str, schema: type[T], messages: list[BaseMessage], reasoning_effort: str | None = None
) -> LLMResult[T]:
    """One API call whose output is constrained to `schema` (OpenAI Structured Outputs)."""
    runnable = chat_model(model, reasoning_effort).with_structured_output(
        schema, method="json_schema", strict=True, include_raw=True
    )
    start = time.perf_counter()
    out = runnable.invoke(messages)
    latency = time.perf_counter() - start
    if out["parsing_error"] is not None or out["parsed"] is None:
        raise ValueError(f"structured output failed: {out['parsing_error']!r}")
    return LLMResult(**vars(_usage(model, out["raw"], latency)), parsed=out["parsed"])


def invoke_tools(
    model: str, tools: list[type[BaseModel]], messages: list[BaseMessage], reasoning_effort: str | None = None
) -> ToolCallResult:
    """One API call where the model may request tool calls (returned in `message.tool_calls`).
    Tools are Pydantic classes: the class docstring is the tool description, fields are the args."""
    runnable = chat_model(model, reasoning_effort).bind_tools(tools, tool_choice="auto", strict=True)
    start = time.perf_counter()
    message = runnable.invoke(messages)
    latency = time.perf_counter() - start
    return ToolCallResult(**vars(_usage(model, message, latency)), message=message)
