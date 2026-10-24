import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, insert

from alfred_server.budget.models import BudgetSettings, Thresholds
from alfred_server.budget.store import BudgetStore, budget_months
from tests.budget_helpers import file_engine, memory_engine, postgres_engines  # noqa: F401

CUSTOM = BudgetSettings(
    cloud_enabled=True,
    providers={"claude": True, "gpt": False, "gemini": True},
    monthly_cap_gbp=35.5,
    thresholds=Thresholds(restricted=0.6, exhausted=0.95),
    provider_preferences={"summary": ["gemini"], "default": ["gpt", "claude"]},
)


def test_seed_defaults_before_first_save() -> None:
    settings = BudgetStore(memory_engine()).load()

    assert settings == BudgetSettings()
    assert settings.monthly_cap_gbp == 20
    assert settings.thresholds.restricted == 0.7
    assert settings.thresholds.exhausted == 1.0
    assert settings.provider_preferences["multi_step_tools"][0] == "claude"
    assert settings.provider_preferences["long_document"][0] == "gemini"
    assert settings.provider_preferences["summary"] == "cheapest"


def roundtrip_survives_restart(make_engine: Callable[[], Engine]) -> None:
    first = make_engine()
    BudgetStore(first).save(CUSTOM)
    BudgetStore(first).save(CUSTOM.model_copy(update={"monthly_cap_gbp": 40.0}))  # update path
    first.dispose()

    # A new engine and store, as after a server restart.
    second = make_engine()
    try:
        assert BudgetStore(second).load() == CUSTOM.model_copy(update={"monthly_cap_gbp": 40.0})
    finally:
        second.dispose()


def test_settings_survive_restart_sqlite(tmp_path: Path) -> None:
    roundtrip_survives_restart(lambda: file_engine(tmp_path / "alfred.db"))


def test_settings_survive_restart_postgres(
    postgres_engines: Callable[[], Engine],  # noqa: F811
) -> None:
    roundtrip_survives_restart(postgres_engines)


def announced_once(engine: Engine) -> None:
    store = BudgetStore(engine)
    at = datetime(2026, 10, 24, tzinfo=UTC)

    assert store.mark_over_budget_announced("2026-10", at) is True
    assert BudgetStore(engine).mark_over_budget_announced("2026-10", at) is False
    assert store.mark_over_budget_announced("2026-11", at) is True


def test_over_budget_announced_once_sqlite(tmp_path: Path) -> None:
    announced_once(file_engine(tmp_path / "alfred.db"))


def test_over_budget_announced_once_postgres(
    postgres_engines: Callable[[], Engine],  # noqa: F811
) -> None:
    announced_once(postgres_engines())


def announced_when_row_exists_unflagged(engine: Engine) -> None:
    store = BudgetStore(engine)
    store._ensure_tables()
    with engine.begin() as conn:
        conn.execute(insert(budget_months).values(month="2026-10", over_budget_announced_at=None))

    assert store.mark_over_budget_announced("2026-10") is True
    assert store.mark_over_budget_announced("2026-10") is False


def test_announced_when_row_exists_unflagged_sqlite(tmp_path: Path) -> None:
    announced_when_row_exists_unflagged(file_engine(tmp_path / "alfred.db"))


def test_announced_when_row_exists_unflagged_postgres(
    postgres_engines: Callable[[], Engine],  # noqa: F811
) -> None:
    announced_when_row_exists_unflagged(postgres_engines())


def test_concurrent_first_checks_announce_once_postgres(
    postgres_engines: Callable[[], Engine],  # noqa: F811
) -> None:
    engine = postgres_engines()
    BudgetStore(engine)._ensure_tables()
    barrier = threading.Barrier(8)

    def mark() -> bool:
        store = BudgetStore(engine)
        store._created = True
        barrier.wait()
        return store.mark_over_budget_announced("2026-10")

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: mark(), range(8)))

    assert results.count(True) == 1
