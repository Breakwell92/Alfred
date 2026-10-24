from datetime import datetime, timedelta

import pytest

from alfred_server.budget.models import BudgetSettings, CostEstimate, Thresholds
from alfred_server.budget.service import BudgetService
from alfred_server.budget.store import BudgetStore
from tests.budget_helpers import NOW, Clock, FakePrices, FakeSpend, make_service, memory_engine

EARLIER = NOW - timedelta(days=1)


def estimate(gbp: float = 0.01, alias: str = "cloud-claude") -> CostEstimate:
    return CostEstimate(alias=alias, prompt_tokens=1000, output_tokens=100, usd=gbp, gbp=gbp)  # type: ignore[arg-type]


def service_with(
    spent: float, *, settings: BudgetSettings | None = None, clock: Clock | None = None
) -> tuple[BudgetService, FakeSpend]:
    spend = FakeSpend()
    if spent:
        spend.add(spent, EARLIER)
    service = make_service(spend=spend, clock=clock)
    service.save_settings(settings or BudgetSettings(cloud_enabled=True, monthly_cap_gbp=20))
    return service, spend


@pytest.mark.parametrize(
    ("spent", "tier"),
    [
        (0.0, "free"),
        (13.99, "free"),  # 69.95%
        (14.0, "restricted"),  # exactly 70%
        (14.01, "restricted"),
        (19.99, "restricted"),
        (20.0, "exhausted"),  # exactly 100%
        (25.0, "exhausted"),
    ],
)
def test_tier_boundaries(spent: float, tier: str) -> None:
    service, _ = service_with(spent)

    decision = service.check("cloud-claude", estimate(0.0), "user_request")

    assert decision.tier == tier
    assert decision.month_to_date_gbp == pytest.approx(spent)


def test_free_tier_allows_auto_escalation() -> None:
    service, _ = service_with(5.0)

    decision = service.check("cloud-claude", estimate(0.5))

    assert decision.allowed
    assert decision.code == "ok"
    assert decision.remaining_gbp == pytest.approx(15.0)


@pytest.mark.parametrize(
    ("reason", "allowed"), [("auto", False), ("hard_task", True), ("user_request", True)]
)
def test_restricted_tier_only_allows_hard_or_requested(reason: str, allowed: bool) -> None:
    service, _ = service_with(15.0)

    decision = service.check("cloud-claude", estimate(0.5), reason)  # type: ignore[arg-type]

    assert decision.tier == "restricted"
    assert decision.allowed is allowed
    assert decision.code == ("ok" if allowed else "restricted")


def test_exhausted_denies_even_explicit_requests() -> None:
    service, _ = service_with(20.5)

    decision = service.check("cloud-claude", estimate(0.0), "user_request")

    assert not decision.allowed
    assert decision.code == "exhausted"
    assert decision.remaining_gbp == 0
    assert "£20.00" in decision.message
    assert "1 November" in decision.message


def test_exhausted_is_announced_once_per_month() -> None:
    clock = Clock(NOW)
    service, spend = service_with(21.0, clock=clock)

    first = service.check("cloud-claude", estimate(), "user_request")
    second = service.check("cloud-gpt", estimate(alias="cloud-gpt"), "user_request")

    assert first.announce and not second.announce
    assert not first.allowed and not second.allowed

    # Next month the counter resets; going over again is announced again.
    clock.now = datetime(2026, 11, 20, 12, 0, tzinfo=NOW.tzinfo)
    spend.add(21.0, clock.now - timedelta(hours=1))
    third = service.check("cloud-claude", estimate(), "user_request")
    assert third.code == "exhausted" and third.announce


def test_estimate_above_remaining_is_refused() -> None:
    service, _ = service_with(5.0)

    assert service.check("cloud-claude", estimate(14.99)).allowed
    decision = service.check("cloud-claude", estimate(15.01))

    assert not decision.allowed
    assert decision.code == "over_remaining"
    assert decision.tier == "free"


def test_estimate_above_remaining_in_restricted_tier() -> None:
    service, _ = service_with(19.0)

    decision = service.check("cloud-claude", estimate(1.5), "hard_task")

    assert decision.code == "over_remaining"


def test_disabled_cloud_denies_without_reading_spend() -> None:
    service, spend = service_with(0.0, settings=BudgetSettings(cloud_enabled=False))

    decision = service.check("cloud-claude", estimate(), "user_request")

    assert not decision.allowed
    assert decision.code == "cloud_disabled"
    assert spend.reads == 0


def test_disabled_provider_is_denied_others_allowed() -> None:
    settings = BudgetSettings(
        cloud_enabled=True, providers={"claude": True, "gpt": False, "gemini": True}
    )
    service, _ = service_with(0.0, settings=settings)

    gpt = service.check("cloud-gpt", estimate(alias="cloud-gpt"))
    claude = service.check("cloud-claude", estimate())

    assert gpt.code == "provider_disabled" and not gpt.allowed
    assert "GPT" in gpt.message
    assert claude.allowed


def test_seed_defaults_keep_cloud_off() -> None:
    service = make_service()

    assert service.check("cloud-claude", estimate()).code == "cloud_disabled"


def test_local_alias_always_allowed() -> None:
    service, spend = service_with(50.0, settings=BudgetSettings(cloud_enabled=False))

    decision = service.check("local-main", estimate(0.0, alias="local-main"))

    assert decision.allowed and decision.code == "local"
    assert spend.reads == 0


def test_zero_cap_is_exhausted() -> None:
    service, _ = service_with(0.0, settings=BudgetSettings(cloud_enabled=True, monthly_cap_gbp=0))

    decision = service.check("cloud-claude", estimate(0.0), "user_request")

    assert decision.tier == "exhausted" and not decision.allowed


def test_custom_thresholds() -> None:
    settings = BudgetSettings(
        cloud_enabled=True,
        monthly_cap_gbp=10,
        thresholds=Thresholds(restricted=0.5, exhausted=0.9),
    )
    service, spend = service_with(4.9, settings=settings)
    assert service.check("cloud-claude", estimate(0.0)).tier == "free"

    spend.add(0.1, EARLIER)
    service._mtd = None  # bypass the 60 s cache
    assert service.check("cloud-claude", estimate(0.0)).tier == "restricted"

    spend.add(4.0, EARLIER)
    service._mtd = None
    assert service.check("cloud-claude", estimate(0.0)).tier == "exhausted"


def test_thresholds_must_be_ordered() -> None:
    with pytest.raises(ValueError):
        Thresholds(restricted=0.9, exhausted=0.8)


def test_spend_unavailable_fails_closed() -> None:
    service, spend = service_with(0.0)
    spend.fail = True

    decision = service.check("cloud-claude", estimate(), "user_request")

    assert not decision.allowed
    assert decision.code == "spend_unavailable"


def test_settings_unavailable_fails_closed() -> None:
    service, _ = service_with(0.0)
    service.store.engine.dispose()
    service.store = BudgetStore(memory_engine())
    service.store.load = lambda: (_ for _ in ()).throw(RuntimeError("db down"))  # type: ignore[method-assign]

    assert service.check("cloud-claude", estimate()).code == "settings_unavailable"


def test_month_to_date_spend_is_cached_for_60s() -> None:
    clock = Clock(NOW)
    service, spend = service_with(1.0, clock=clock)

    assert service.get_month_to_date_spend() == pytest.approx(1.0)
    spend.add(2.0, EARLIER)
    clock.now = NOW + timedelta(seconds=59)
    assert service.get_month_to_date_spend() == pytest.approx(1.0)
    assert spend.reads == 1

    clock.now = NOW + timedelta(seconds=60)
    assert service.get_month_to_date_spend() == pytest.approx(3.0)
    assert spend.reads == 2


def test_month_to_date_converts_usd_to_gbp() -> None:
    spend = FakeSpend()
    spend.add(10.0, EARLIER)
    service = make_service(spend=spend, usd_to_gbp=0.8)

    assert service.get_month_to_date_spend() == pytest.approx(8.0)


def test_estimate_cost_uses_alias_price_and_rate() -> None:
    service = make_service(usd_to_gbp=0.8)

    est = service.estimate_cost("cloud-claude", prompt_tokens=10_000, expected_output_tokens=1_000)

    # 10k * $2/M + 1k * $10/M = $0.03
    assert est.usd == pytest.approx(0.03)
    assert est.gbp == pytest.approx(0.024)
    assert service.estimate_cost("local-main", 10_000, 1_000).usd == 0


def test_decide_denies_unpriced_alias() -> None:
    service = make_service(prices=FakePrices({}))
    service.save_settings(BudgetSettings(cloud_enabled=True))

    decision = service.decide("cloud-gemini", 1000, 100, "user_request")

    assert not decision.allowed and decision.code == "price_unavailable"


def test_decide_estimates_then_checks() -> None:
    service, _ = service_with(0.0)

    assert service.decide("cloud-claude", 1000, 100).allowed
    # 1M output tokens = $10 → fine; 3M = $30 > £20 remaining.
    assert service.decide("cloud-claude", 0, 3_000_000).code == "over_remaining"
    assert service.decide("local-router", 10**9, 10**9).code == "local"


def test_preferred_aliases_follow_settings() -> None:
    settings = BudgetSettings(
        cloud_enabled=True, providers={"claude": False, "gpt": True, "gemini": True}
    )
    service, _ = service_with(0.0, settings=settings)

    assert service.preferred_aliases("long_document") == ["cloud-gemini", "cloud-gpt"]
    assert service.preferred_aliases("multi_step_tools") == ["cloud-gpt", "cloud-gemini"]
    assert service.preferred_aliases("something_new") == ["cloud-gpt", "cloud-gemini"]


def test_preferred_aliases_cheapest_orders_by_price() -> None:
    from alfred_server.budget.prices import Price

    prices = FakePrices(
        {
            "cloud-claude": Price(3e-6, 15e-6),
            "cloud-gpt": Price(1e-6, 4e-6),
            # cloud-gemini not on the gateway: left out
        }
    )
    service = make_service(prices=prices)
    service.save_settings(BudgetSettings(cloud_enabled=True))

    assert service.preferred_aliases("summary") == ["cloud-gpt", "cloud-claude"]


def test_preferred_aliases_empty_when_cloud_off() -> None:
    assert make_service().preferred_aliases("default") == []
