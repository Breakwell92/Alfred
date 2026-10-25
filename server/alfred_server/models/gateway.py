"""Agno models backed by the LiteLLM proxy (infra/litellm/).

Code never talks to a provider directly and never names a provider model: it asks for an
alias (`local-router`, `local-main`, `cloud-claude`, …) and LiteLLM maps it to Ollama or a
cloud API, logging tokens and cost per call in its own Postgres database. Which aliases point
where: infra/litellm/config.yaml and docs/decisions/0005-model-gateway.md.

Cloud aliases only exist on the proxy when their API key is set; calling a missing one fails
at request time with a 400 from LiteLLM. Deciding *whether* to use a cloud alias (enabled,
under budget, needed) is the router's and budget service's job, not this module's.

The one check made here is a backstop for privacy routing (docs/decisions/0025-privacy-routing.md):
a cloud model refuses to send a request holding the result of a tool in `private_tools`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal, get_args

from agno.models.message import Message
from agno.models.openai.like import OpenAILike
from agno.models.response import ModelResponse

from alfred_server.config import Settings, get_settings

ModelAlias = Literal["local-router", "local-main", "cloud-claude", "cloud-gpt", "cloud-gemini"]
MODEL_ALIASES: tuple[ModelAlias, ...] = get_args(ModelAlias)
LOCAL_ALIASES: frozenset[ModelAlias] = frozenset({"local-router", "local-main"})


class GatewayNotConfiguredError(RuntimeError):
    """`ALFRED_LITELLM_API_KEY` is not set, so no model call can be made."""


class PrivateContentError(RuntimeError):
    """A cloud call was refused: its messages hold the output of a `local_only` tool."""

    def __init__(self, alias: str, tools: Sequence[str]) -> None:
        super().__init__(
            f"refused to send private tool output ({', '.join(tools)}) to cloud model {alias}"
        )
        self.alias = alias
        self.tools = tuple(tools)


def private_tool_results(messages: Sequence[Message], private_tools: frozenset[str]) -> list[str]:
    """Names of the tools in `private_tools` whose results are among `messages`.

    Every tool result Agno produces is a `tool` message tagged with the tool's name, and it
    keeps that tag in stored history, so results from earlier turns are caught too.
    """
    return sorted(
        {
            m.tool_name
            for m in messages
            if m.role == "tool" and m.tool_name is not None and m.tool_name in private_tools
        }
    )


@dataclass
class GatewayModel(OpenAILike):
    """`OpenAILike` with the privacy backstop.

    On a cloud alias, a request whose messages hold the result of a tool in `private_tools`
    is never sent: it raises `PrivateContentError` and records the tools in `refused_tools`
    (Agno turns the exception into a failed run, so the router reads that to retry locally).
    """

    private_tools: frozenset[str] = frozenset()
    refused_tools: tuple[str, ...] = ()

    def _check_private(self, messages: Sequence[Message]) -> None:
        if self.id in LOCAL_ALIASES or not self.private_tools:
            return
        found = private_tool_results(messages, self.private_tools)
        if found:
            self.refused_tools = tuple(found)
            raise PrivateContentError(self.id, found)

    def invoke(self, messages: list[Message], *args: Any, **kwargs: Any) -> ModelResponse:
        self._check_private(messages)
        return super().invoke(messages, *args, **kwargs)

    async def ainvoke(self, messages: list[Message], *args: Any, **kwargs: Any) -> ModelResponse:
        self._check_private(messages)
        return await super().ainvoke(messages, *args, **kwargs)

    def invoke_stream(
        self, messages: list[Message], *args: Any, **kwargs: Any
    ) -> Iterator[ModelResponse]:
        self._check_private(messages)
        yield from super().invoke_stream(messages, *args, **kwargs)

    async def ainvoke_stream(
        self, messages: list[Message], *args: Any, **kwargs: Any
    ) -> AsyncIterator[ModelResponse]:
        self._check_private(messages)
        async for response in super().ainvoke_stream(messages, *args, **kwargs):
            yield response


@dataclass(frozen=True)
class CallMetadata:
    """Who made a call and why, attached to every request so spend can be grouped later.

    Sent as LiteLLM request metadata: `agent` and `trigger` also become request tags
    (`agent:<name>`, `trigger:<kind>`) for LiteLLM's per-tag spend reports. All three land in
    the spend log's `spend_logs_metadata`. `task_id` is not a tag: it is unique per task.
    """

    agent: str
    task_id: str | None = None
    trigger: str | None = None

    def to_litellm(self) -> dict[str, Any]:
        tags = [f"agent:{self.agent}"]
        if self.trigger:
            tags.append(f"trigger:{self.trigger}")
        fields = {"agent": self.agent, "task_id": self.task_id, "trigger": self.trigger}
        return {
            "tags": tags,
            "spend_logs_metadata": {k: v for k, v in fields.items() if v is not None},
        }


def gateway_model(
    alias: ModelAlias,
    *,
    metadata: CallMetadata | None = None,
    settings: Settings | None = None,
    **kwargs: Any,
) -> GatewayModel:
    """Return an Agno model that calls `alias` through the LiteLLM proxy.

    `kwargs` are passed to `GatewayModel` (e.g. `temperature`, `max_tokens`, `http_client`,
    `private_tools`).

    Raises:
        ValueError: `alias` is not one of `MODEL_ALIASES`.
        GatewayNotConfiguredError: `ALFRED_LITELLM_API_KEY` is not set.
    """
    if alias not in MODEL_ALIASES:
        raise ValueError(f"unknown model alias {alias!r}; expected one of {MODEL_ALIASES}")
    settings = settings or get_settings()
    if settings.litellm_api_key is None or not settings.litellm_api_key.get_secret_value():
        raise GatewayNotConfiguredError(
            "ALFRED_LITELLM_API_KEY is not set; see docs/infra.md#litellm-model-gateway"
        )

    extra_body = dict(kwargs.pop("extra_body", None) or {})
    if metadata is not None:
        extra_body["metadata"] = {**extra_body.get("metadata", {}), **metadata.to_litellm()}

    return GatewayModel(
        id=alias,
        name=f"LiteLLM {alias}",
        provider="LiteLLM",
        base_url=settings.litellm_base_url,
        api_key=settings.litellm_api_key.get_secret_value(),
        extra_body=extra_body or None,
        **kwargs,
    )
