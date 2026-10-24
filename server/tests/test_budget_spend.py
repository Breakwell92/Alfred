from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, insert

from alfred_server.budget.models import BudgetSettings
from alfred_server.budget.service import BudgetService
from alfred_server.budget.spend import (
    LiteLLMSpendLogs,
    SpendUnavailableError,
    _litellm_metadata,
    spend_logs,
)
from alfred_server.budget.store import BudgetStore
from tests.budget_helpers import (  # noqa: F401
    NOW,
    Clock,
    FakePrices,
    memory_engine,
    postgres_engines,
)


def utc(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    """Naive UTC, as LiteLLM stores `startTime`."""
    return datetime(year, month, day, hour, minute)


def meta(**fields: str) -> dict[str, Any]:
    return {"spend_logs_metadata": fields, "user_api_key_alias": None}


# NOW is 2026-10-24 12:00 UTC (13:00 BST). October in Europe/London started at
# 2026-09-30 23:00 UTC; "today" started at 2026-10-23 23:00 UTC.
ROWS: list[dict[str, Any]] = [
    # September (local), excluded.
    {"spend": 5.0, "startTime": utc(2026, 9, 30, 22, 30), "model_group": "cloud-claude"},
    {
        "spend": 1.0,
        "startTime": utc(2026, 9, 30, 23, 30),  # 00:30 BST on 1 October
        "model_group": "cloud-claude",
        "request_tags": ["agent:router", "trigger:voice"],
        "metadata": meta(agent="router", task_id="t1", trigger="voice"),
    },
    {
        "spend": 2.0,
        "startTime": utc(2026, 10, 10, 10, 0),
        "model_group": "cloud-gemini",
        "request_tags": ["agent:research"],
        "metadata": meta(agent="research", task_id="t2"),
    },
    {
        "spend": 0.5,
        "startTime": utc(2026, 10, 23, 23, 30),  # 00:30 BST today
        "model_group": "cloud-gpt",
        # Only tags, no spend_logs_metadata.
        "request_tags": ["agent:comms", "trigger:schedule"],
        "metadata": {},
    },
    {
        "spend": 0.25,
        "startTime": utc(2026, 10, 24, 11, 0),
        "model_group": "cloud-claude",
        "request_tags": [],
        "metadata": None,
    },
    # Local call: logged at zero cost, excluded.
    {"spend": 0.0, "startTime": utc(2026, 10, 24, 11, 30), "model_group": "local-main"},
    # November, excluded.
    {"spend": 9.0, "startTime": utc(2026, 11, 1, 0, 30), "model_group": "cloud-claude"},
]


@pytest.fixture(params=["sqlite", "postgres"])
def litellm_engine(request: pytest.FixtureRequest) -> Engine:
    if request.param == "sqlite":
        engine = memory_engine()
    else:
        engine = request.getfixturevalue("postgres_engines")()
    _litellm_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            insert(spend_logs),
            # Every row needs every key: executemany takes its columns from the first.
            [
                {"request_id": f"req-{i}", "request_tags": None, "metadata": None, **row}
                for i, row in enumerate(ROWS)
            ],
        )
    return engine


def service_for(engine: Engine) -> BudgetService:
    return BudgetService(
        BudgetStore(memory_engine()),
        LiteLLMSpendLogs(engine),
        FakePrices(),
        usd_to_gbp=0.8,
        timezone="Europe/London",
        clock=Clock(NOW),
    )


def test_summary_totals_against_seeded_spend_logs(litellm_engine: Engine) -> None:
    summary = service_for(litellm_engine).summary()

    assert summary.currency == "GBP"
    assert summary.month == "2026-10"
    assert summary.usd_to_gbp == 0.8
    # (1 + 2 + 0.5 + 0.25) USD * 0.8
    assert summary.month_to_date_gbp == pytest.approx(3.0)
    # (0.5 + 0.25) USD * 0.8
    assert summary.today_gbp == pytest.approx(0.6)
    assert summary.monthly_cap_gbp == 20
    assert summary.remaining_gbp == pytest.approx(17.0)
    assert summary.tier == "free"
    assert summary.by_provider == pytest.approx({"gemini": 1.6, "claude": 1.0, "gpt": 0.4})
    assert summary.by_agent == pytest.approx(
        {"research": 1.6, "router": 0.8, "comms": 0.4, "untagged": 0.2}
    )
    assert summary.by_task_id == pytest.approx({"t2": 1.6, "t1": 0.8, "untagged": 0.6})
    assert summary.by_trigger == pytest.approx({"untagged": 1.8, "voice": 0.8, "schedule": 0.4})


def test_month_to_date_from_spend_logs(litellm_engine: Engine) -> None:
    assert service_for(litellm_engine).get_month_to_date_spend() == pytest.approx(3.0)


def test_summary_tier_follows_cap(litellm_engine: Engine) -> None:
    service = service_for(litellm_engine)
    service.save_settings(BudgetSettings(monthly_cap_gbp=4.0))

    summary = service.summary()

    assert summary.tier == "restricted"  # 3.0 / 4.0 = 75%
    assert summary.remaining_gbp == pytest.approx(1.0)


def test_month_boundary_in_winter_time(litellm_engine: Engine) -> None:
    # 1 November is GMT, so the month starts at midnight UTC.
    service = service_for(litellm_engine)
    service.clock = Clock(datetime(2026, 11, 2, 12, 0, tzinfo=UTC))

    summary = service.summary()

    assert summary.month == "2026-11"
    assert summary.month_to_date_gbp == pytest.approx(9.0 * 0.8)
    assert summary.today_gbp == 0


def test_rows_parse_metadata_then_tags(litellm_engine: Engine) -> None:
    rows = LiteLLMSpendLogs(litellm_engine).rows(
        datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC)
    )

    by_alias = {(r.alias, r.usd): r for r in rows}
    comms = by_alias[("cloud-gpt", 0.5)]
    assert (comms.agent, comms.task_id, comms.trigger) == ("comms", None, "schedule")
    untagged = by_alias[("cloud-claude", 0.25)]
    assert (untagged.agent, untagged.task_id, untagged.trigger) == (None, None, None)
    assert all(r.start_time.tzinfo is UTC for r in rows)


def test_unreachable_spend_logs_raise() -> None:
    engine = create_engine(
        "postgresql+psycopg://alfred@127.0.0.1:1/litellm", connect_args={"connect_timeout": 2}
    )

    with pytest.raises(SpendUnavailableError):
        LiteLLMSpendLogs(engine).rows(NOW, NOW)
