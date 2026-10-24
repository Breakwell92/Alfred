"""Budget settings and the values the budget service hands back."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from alfred_server.models.gateway import ModelAlias

# Cloud providers by short name; each maps to one gateway alias.
Provider = Literal["claude", "gpt", "gemini"]
PROVIDER_ALIASES: dict[Provider, ModelAlias] = {
    "claude": "cloud-claude",
    "gpt": "cloud-gpt",
    "gemini": "cloud-gemini",
}
ALIAS_PROVIDERS: dict[ModelAlias, Provider] = {a: p for p, a in PROVIDER_ALIASES.items()}

# free: escalate freely. restricted: only hard tasks or explicit requests. exhausted: local only.
Tier = Literal["free", "restricted", "exhausted"]
# Why the caller wants a cloud model. `auto` is the router's own judgement call.
EscalationReason = Literal["auto", "hard_task", "user_request"]
DecisionCode = Literal[
    "local",
    "ok",
    "cloud_disabled",
    "provider_disabled",
    "restricted",
    "exhausted",
    "over_remaining",
    "spend_unavailable",
    "settings_unavailable",
    "price_unavailable",
]


class Thresholds(BaseModel):
    """Fractions of the monthly cap at which the restricted and exhausted tiers begin."""

    restricted: float = Field(default=0.7, gt=0)
    exhausted: float = Field(default=1.0, gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.restricted > self.exhausted:
            raise ValueError("restricted threshold must not exceed the exhausted threshold")
        return self


def _all_providers_enabled() -> dict[Provider, bool]:
    return {"claude": True, "gpt": True, "gemini": True}


def _default_preferences() -> dict[str, list[Provider] | Literal["cheapest"]]:
    return {
        "multi_step_tools": ["claude", "gpt", "gemini"],
        "long_document": ["gemini", "claude", "gpt"],
        "summary": "cheapest",
        "default": ["claude", "gpt", "gemini"],
    }


class BudgetSettings(BaseModel):
    """Dan's budget settings, persisted in Postgres and edited via `PUT /settings/budget`.

    The defaults are the seed values used until the first save.
    """

    # Off until Dan turns it on: cloud models are used only when enabled.
    cloud_enabled: bool = False
    providers: dict[Provider, bool] = Field(default_factory=_all_providers_enabled)
    monthly_cap_gbp: float = Field(default=20.0, ge=0)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    # Provider order per task type; "cheapest" orders by current price. `default` is used for
    # task types without an entry.
    provider_preferences: dict[str, list[Provider] | Literal["cheapest"]] = Field(
        default_factory=_default_preferences
    )

    def provider_enabled(self, provider: Provider) -> bool:
        return self.providers.get(provider, True)


@dataclass(frozen=True)
class CostEstimate:
    alias: ModelAlias
    prompt_tokens: int
    output_tokens: int
    usd: float
    gbp: float


@dataclass(frozen=True)
class BudgetDecision:
    """Whether a cloud call may go ahead.

    `message` is a short explanation that can be said to the user. Say it when `announce` is
    true (the first time the budget is exhausted each month); otherwise it's for logs.
    `tier` is None when it couldn't be worked out (local alias, cloud off, spend unreadable).
    """

    allowed: bool
    code: DecisionCode
    tier: Tier | None
    message: str
    announce: bool = False
    month_to_date_gbp: float | None = None
    remaining_gbp: float | None = None
