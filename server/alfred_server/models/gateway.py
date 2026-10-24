"""Agno models backed by the LiteLLM proxy (infra/litellm/).

Code never talks to a provider directly and never names a provider model: it asks for an
alias (`local-router`, `local-main`, `cloud-claude`, …) and LiteLLM maps it to Ollama or a
cloud API, logging tokens and cost per call in its own Postgres database. Which aliases point
where: infra/litellm/config.yaml and docs/decisions/0005-model-gateway.md.

Cloud aliases only exist on the proxy when their API key is set; calling a missing one fails
at request time with a 400 from LiteLLM. Deciding *whether* to use a cloud alias (enabled,
under budget, needed) is the router's and budget service's job, not this module's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, get_args

from agno.models.openai.like import OpenAILike

from alfred_server.config import Settings, get_settings

ModelAlias = Literal["local-router", "local-main", "cloud-claude", "cloud-gpt", "cloud-gemini"]
MODEL_ALIASES: tuple[ModelAlias, ...] = get_args(ModelAlias)
LOCAL_ALIASES: frozenset[ModelAlias] = frozenset({"local-router", "local-main"})


class GatewayNotConfiguredError(RuntimeError):
    """`ALFRED_LITELLM_API_KEY` is not set, so no model call can be made."""


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
) -> OpenAILike:
    """Return an Agno model that calls `alias` through the LiteLLM proxy.

    `kwargs` are passed to `OpenAILike` (e.g. `temperature`, `max_tokens`, `http_client`).

    Raises:
        ValueError: `alias` is not one of `MODEL_ALIASES`.
        GatewayNotConfiguredError: `ALFRED_LITELLM_API_KEY` is not set.
    """
    if alias not in MODEL_ALIASES:
        raise ValueError(f"unknown model alias {alias!r}; expected one of {MODEL_ALIASES}")
    settings = settings or get_settings()
    if settings.litellm_api_key is None:
        raise GatewayNotConfiguredError(
            "ALFRED_LITELLM_API_KEY is not set; see docs/infra.md#litellm-model-gateway"
        )

    extra_body = dict(kwargs.pop("extra_body", None) or {})
    if metadata is not None:
        extra_body["metadata"] = {**extra_body.get("metadata", {}), **metadata.to_litellm()}

    return OpenAILike(
        id=alias,
        name=f"LiteLLM {alias}",
        provider="LiteLLM",
        base_url=settings.litellm_base_url,
        api_key=settings.litellm_api_key.get_secret_value(),
        extra_body=extra_body or None,
        **kwargs,
    )
