"""The budget service: may this request use a cloud model, and what has been spent?

The router asks before every cloud call (`decide`, or `estimate_cost` + `check`) and stays
local when the answer is no. Rules and why: docs/decisions/0008-budget-service.md.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from alfred_server.budget.models import (
    ALIAS_PROVIDERS,
    PROVIDER_ALIASES,
    BudgetDecision,
    BudgetSettings,
    CostEstimate,
    EscalationReason,
    Provider,
    Tier,
)
from alfred_server.budget.prices import GatewayPrices, Price, PriceUnavailableError
from alfred_server.budget.spend import (
    LiteLLMSpendLogs,
    SpendRow,
    SpendSource,
    SpendUnavailableError,
)
from alfred_server.budget.store import BudgetStore
from alfred_server.config import Settings, get_settings
from alfred_server.models.gateway import LOCAL_ALIASES, ModelAlias

logger = logging.getLogger(__name__)

# Month-to-date spend is re-read at most this often. LiteLLM writes spend logs about a
# minute late anyway.
SPEND_CACHE_TTL_S = 60.0

PROVIDER_NAMES: dict[Provider, str] = {"claude": "Claude", "gpt": "GPT", "gemini": "Gemini"}


class SettingsUnavailableError(RuntimeError):
    """The budget settings couldn't be read from the database."""


class PriceSource(Protocol):
    def price(self, alias: ModelAlias) -> Price: ...


class SpendSummary(BaseModel):
    """`GET /spend/summary`. Amounts in GBP, converted at `usd_to_gbp`."""

    currency: str = "GBP"
    usd_to_gbp: float
    month: str
    today_gbp: float
    month_to_date_gbp: float
    monthly_cap_gbp: float
    remaining_gbp: float
    tier: Tier
    by_provider: dict[str, float]
    by_agent: dict[str, float]
    by_task_id: dict[str, float]
    by_trigger: dict[str, float]


def _gbp(value: float) -> float:
    return round(value, 4)


def _group(rows: Iterable[SpendRow], key: Callable[[SpendRow], str | None], rate: float):
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        totals[key(row) or "untagged"] += row.usd * rate
    return {k: _gbp(v) for k, v in sorted(totals.items(), key=lambda kv: -kv[1])}


def _provider_of(row: SpendRow) -> str:
    if row.alias in ALIAS_PROVIDERS:
        return ALIAS_PROVIDERS[row.alias]
    if row.alias in LOCAL_ALIASES:
        return "local"
    return row.alias or "unknown"


class BudgetService:
    def __init__(
        self,
        store: BudgetStore,
        spend: SpendSource,
        prices: PriceSource,
        *,
        usd_to_gbp: float,
        timezone: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        spend_cache_ttl_s: float = SPEND_CACHE_TTL_S,
    ) -> None:
        self.store = store
        self.spend = spend
        self.prices = prices
        self.usd_to_gbp = usd_to_gbp
        self.tz = ZoneInfo(timezone)
        self.clock = clock
        self.spend_cache_ttl_s = spend_cache_ttl_s
        self._mtd: tuple[str, datetime, float] | None = None  # (month, fetched at, GBP)
        self._lock = threading.Lock()

    # --- Calendar ---------------------------------------------------------------------------

    def _month_bounds(self, now: datetime) -> tuple[str, datetime, datetime]:
        local = now.astimezone(self.tz)
        start = datetime(local.year, local.month, 1, tzinfo=self.tz)
        year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
        end = datetime(year, month, 1, tzinfo=self.tz)
        return f"{local.year:04d}-{local.month:02d}", start, end

    def _day_bounds(self, now: datetime) -> tuple[datetime, datetime]:
        local = now.astimezone(self.tz).date()
        start = datetime(local.year, local.month, local.day, tzinfo=self.tz)
        nxt = local + timedelta(days=1)
        return start, datetime(nxt.year, nxt.month, nxt.day, tzinfo=self.tz)

    # --- Spend and cost -----------------------------------------------------------------------

    def load_settings(self) -> BudgetSettings:
        """The stored settings. Raises `SettingsUnavailableError` if they can't be read."""
        try:
            return self.store.load()
        except Exception as exc:
            raise SettingsUnavailableError(f"can't read budget settings: {exc}") from exc

    def get_settings(self) -> BudgetSettings:
        return self.load_settings()

    def save_settings(self, settings: BudgetSettings) -> BudgetSettings:
        self.store.save(settings)
        return settings

    def get_month_to_date_spend(self) -> float:
        """Cloud spend so far this (local) month in GBP, cached for `spend_cache_ttl_s`.

        Raises `SpendUnavailableError` if the spend logs can't be read.
        """
        now = self.clock()
        month, start, end = self._month_bounds(now)
        with self._lock:
            cached = self._mtd
            if (
                cached is not None
                and cached[0] == month
                and (now - cached[1]).total_seconds() < self.spend_cache_ttl_s
            ):
                return cached[2]
            total = sum(r.usd for r in self.spend.rows(start, end)) * self.usd_to_gbp
            self._mtd = (month, now, total)
            return total

    def estimate_cost(
        self, alias: ModelAlias, prompt_tokens: int, expected_output_tokens: int
    ) -> CostEstimate:
        """Estimated cost of one call. Raises `PriceUnavailableError` if the price is unknown."""
        usd = self.prices.price(alias).cost(prompt_tokens, expected_output_tokens)
        return CostEstimate(
            alias=alias,
            prompt_tokens=prompt_tokens,
            output_tokens=expected_output_tokens,
            usd=usd,
            gbp=usd * self.usd_to_gbp,
        )

    # --- Decisions ----------------------------------------------------------------------------

    def tier_for(self, settings: BudgetSettings, month_to_date_gbp: float) -> Tier:
        cap = settings.monthly_cap_gbp
        if cap <= 0:
            return "exhausted"
        fraction = month_to_date_gbp / cap
        if fraction >= settings.thresholds.exhausted:
            return "exhausted"
        if fraction >= settings.thresholds.restricted:
            return "restricted"
        return "free"

    def check(
        self, alias: ModelAlias, estimate: CostEstimate, reason: EscalationReason = "auto"
    ) -> BudgetDecision:
        """May a call to `alias` costing `estimate` go ahead? Fails closed (deny) on errors."""
        if alias in LOCAL_ALIASES:
            return BudgetDecision(True, "local", None, "Local models are always allowed.")
        try:
            settings = self.store.load()
        except Exception:  # can't read settings: no cloud
            return BudgetDecision(
                False, "settings_unavailable", None, "I can't read the budget settings."
            )
        if not settings.cloud_enabled:
            return BudgetDecision(False, "cloud_disabled", None, "Cloud models are turned off.")
        provider = ALIAS_PROVIDERS[alias]
        if not settings.provider_enabled(provider):
            return BudgetDecision(
                False, "provider_disabled", None, f"{PROVIDER_NAMES[provider]} is turned off."
            )
        try:
            spent = self.get_month_to_date_spend()
        except SpendUnavailableError:
            return BudgetDecision(
                False,
                "spend_unavailable",
                None,
                "I can't check the cloud budget right now, so I'll stay local.",
            )

        cap = settings.monthly_cap_gbp
        remaining = max(cap - spent, 0.0)
        tier = self.tier_for(settings, spent)

        def decision(allowed: bool, code, message: str, announce: bool = False) -> BudgetDecision:
            return BudgetDecision(
                allowed, code, tier, message, announce, _gbp(spent), _gbp(remaining)
            )

        if tier == "exhausted":
            month, _, end = self._month_bounds(self.clock())
            try:
                announce = self.store.mark_over_budget_announced(month)
            except Exception:  # missing an announcement beats failing the request
                logger.exception("couldn't record the over-budget announcement for %s", month)
                announce = False
            until = f"{end.day} {end.strftime('%B')}"
            return decision(
                False,
                "exhausted",
                f"I've used this month's £{cap:.2f} cloud budget, so I'll use local models "
                f"until {until}.",
                announce,
            )
        if tier == "restricted" and reason == "auto":
            used = round(100 * spent / cap)
            return decision(
                False,
                "restricted",
                f"The cloud budget is {used}% used, so I'm saving it for harder requests.",
            )
        if estimate.gbp > remaining:
            return decision(
                False,
                "over_remaining",
                f"That would cost about £{estimate.gbp:.2f}, more than the £{remaining:.2f} "
                "left this month.",
            )
        return decision(True, "ok", f"About £{estimate.gbp:.2f}; £{remaining:.2f} left this month.")

    def decide(
        self,
        alias: ModelAlias,
        prompt_tokens: int,
        expected_output_tokens: int,
        reason: EscalationReason = "auto",
    ) -> BudgetDecision:
        """`estimate_cost` then `check`, denying if the price is unknown."""
        if alias in LOCAL_ALIASES:
            return BudgetDecision(True, "local", None, "Local models are always allowed.")
        try:
            estimate = self.estimate_cost(alias, prompt_tokens, expected_output_tokens)
        except PriceUnavailableError:
            return BudgetDecision(
                False, "price_unavailable", None, "I don't know what that model costs."
            )
        return self.check(alias, estimate, reason)

    def preferred_aliases(self, task_type: str) -> list[ModelAlias]:
        """Enabled cloud aliases for `task_type`, most preferred first. Empty if cloud is off.

        Doesn't check the budget: call `decide` for the one you pick. Empty, too, if the
        settings can't be read (fail closed).
        """
        try:
            settings = self.store.load()
        except Exception:
            logger.exception("can't read budget settings")
            return []
        if not settings.cloud_enabled:
            return []
        prefs = settings.provider_preferences
        order = prefs.get(task_type) or prefs.get("default") or list(PROVIDER_ALIASES)
        if order == "cheapest":
            priced: list[tuple[float, Provider]] = []
            for provider in PROVIDER_ALIASES:
                try:
                    price = self.prices.price(PROVIDER_ALIASES[provider])
                except PriceUnavailableError:
                    continue
                # Typical call shape: more prompt than output.
                priced.append((price.cost(4000, 500), provider))
            order = [p for _, p in sorted(priced, key=lambda cp: cp[0])]
        return [PROVIDER_ALIASES[p] for p in order if settings.provider_enabled(p)]

    # --- Reporting ----------------------------------------------------------------------------

    def summary(self) -> SpendSummary:
        """Spend today and this month, by provider and tag.

        Raises `SpendUnavailableError` (spend logs) or `SettingsUnavailableError` (settings).
        """
        settings = self.load_settings()
        now = self.clock()
        month, start, end = self._month_bounds(now)
        day_start, day_end = self._day_bounds(now)
        rows = self.spend.rows(start, end)
        rate = self.usd_to_gbp
        spent = sum(r.usd for r in rows) * rate
        today = sum(r.usd for r in rows if day_start <= r.start_time < day_end) * rate
        return SpendSummary(
            usd_to_gbp=rate,
            month=month,
            today_gbp=_gbp(today),
            month_to_date_gbp=_gbp(spent),
            monthly_cap_gbp=settings.monthly_cap_gbp,
            remaining_gbp=_gbp(max(settings.monthly_cap_gbp - spent, 0.0)),
            tier=self.tier_for(settings, spent),
            by_provider=_group(rows, _provider_of, rate),
            by_agent=_group(rows, lambda r: r.agent, rate),
            by_task_id=_group(rows, lambda r: r.task_id, rate),
            by_trigger=_group(rows, lambda r: r.trigger, rate),
        )


def build_budget_service(settings: Settings) -> BudgetService:
    database_url = settings.database_url.get_secret_value()
    litellm_url = (
        make_url(settings.litellm_database_url.get_secret_value())
        if settings.litellm_database_url is not None
        else make_url(database_url).set(database="litellm")
    )
    api_key = settings.litellm_api_key.get_secret_value() if settings.litellm_api_key else None
    return BudgetService(
        BudgetStore(create_engine(database_url, pool_pre_ping=True)),
        LiteLLMSpendLogs(create_engine(litellm_url, pool_pre_ping=True)),
        GatewayPrices(settings.litellm_base_url, api_key),
        usd_to_gbp=settings.budget_usd_to_gbp,
        timezone=settings.timezone,
    )


@lru_cache
def get_budget_service() -> BudgetService:
    """The process-wide budget service. Use as a FastAPI dependency (overridden in tests)."""
    return build_budget_service(get_settings())
